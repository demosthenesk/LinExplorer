"""Search (the search box): Windows 7 style query syntax over a recursive walk."""
from __future__ import annotations

import datetime as _dt
import os
import re
import shlex
import time

from . import locations, mime, places, util, ziputil
from .i18n import T
from .items import FileItem
from .settings import settings

SKIP_DIRS = {"/proc", "/sys", "/dev", "/run", "/snap", "/tmp/.X11-unix", "/var/lib/docker", "/lost+found"}

FILTER_ALIASES = {
    "kind": "kind", "είδος": "kind",
    "type": "type", "τύπος": "type", "ext": "ext", "επέκταση": "ext",
    "datemodified": "datemodified", "modified": "datemodified", "date": "datemodified",
    "ημερομηνίατροποποίησης": "datemodified", "τροποποίηση": "datemodified",
    "datecreated": "datecreated", "ημερομηνίαδημιουργίας": "datecreated",
    "size": "size", "μέγεθος": "size",
    "name": "name", "όνομα": "name",
    "content": "content", "περιεχόμενο": "content", "contents": "content",
}

KIND_ALIASES = {
    "document": "document", "documents": "document", "έγγραφο": "document", "έγγραφα": "document",
    "picture": "picture", "pictures": "picture", "image": "picture", "photo": "picture", "εικόνα": "picture",
    "εικόνες": "picture", "music": "music", "audio": "music", "song": "music", "μουσική": "music",
    "video": "video", "videos": "video", "movie": "video", "βίντεο": "video",
    "folder": "folder", "folders": "folder", "φάκελος": "folder", "φάκελοι": "folder",
    "program": "program", "programs": "program", "πρόγραμμα": "program",
    "compressed": "compressed", "archive": "compressed", "zip": "compressed", "συμπιεσμένο": "compressed",
    "font": "font", "γραμματοσειρά": "font",
}

SIZE_BUCKETS = {
    "empty": (0, 0), "κενό": (0, 0),
    "tiny": (0, 10 * 1024), "πολύμικρό": (0, 10 * 1024),
    "small": (10 * 1024, 100 * 1024), "μικρό": (10 * 1024, 100 * 1024),
    "medium": (100 * 1024, 1024 ** 2), "μεσαίο": (100 * 1024, 1024 ** 2),
    "large": (1024 ** 2, 16 * 1024 ** 2), "μεγάλο": (1024 ** 2, 16 * 1024 ** 2),
    "huge": (16 * 1024 ** 2, 128 * 1024 ** 2), "τεράστιο": (16 * 1024 ** 2, 128 * 1024 ** 2),
    "gigantic": (128 * 1024 ** 2, 1 << 62), "γιγαντιαίο": (128 * 1024 ** 2, 1 << 62),
}
_UNIT = {"b": 1, "kb": 1024, "k": 1024, "mb": 1024 ** 2, "m": 1024 ** 2, "gb": 1024 ** 3, "g": 1024 ** 3,
         "tb": 1024 ** 4}


class Query:
    def __init__(self, text: str, content: bool = False):
        self.text = text
        self.words: list[str] = []
        self.kinds: set[str] = set()
        self.exts: set[str] = set()
        self.types: list[str] = []
        self.size_ranges: list[tuple[int, int]] = []
        self.size_cmp: list[tuple[str, int]] = []
        self.date_ranges: list[tuple[float, float]] = []
        self.created_ranges: list[tuple[float, float]] = []
        self.content_words: list[str] = []
        self.search_content = content
        self._parse(text)

    def _parse(self, text: str):
        try:
            toks = shlex.split(text)
        except ValueError:
            toks = text.split()
        for tok in toks:
            if ":" in tok and not tok.startswith(":"):
                key, _, val = tok.partition(":")
                f = FILTER_ALIASES.get(key.lower().replace(" ", ""))
                if f:
                    self._filter(f, val.strip())
                    continue
            if tok.upper() in ("AND", "ΚΑΙ"):
                continue
            self.words.append(tok.casefold())

    def _filter(self, f: str, val: str):
        v = val.casefold().strip("\"'")
        if not v:
            return
        if f == "kind":
            for x in v.split("|"):
                k = KIND_ALIASES.get(x.strip("="))
                if k:
                    self.kinds.add(k)
        elif f == "ext":
            for x in v.split("|"):
                self.exts.add("." + x.lstrip("*.").lower())
        elif f == "type":
            for x in v.split("|"):
                if x.startswith(".") or x.startswith("*."):
                    self.exts.add("." + x.lstrip("*.").lower())
                else:
                    self.types.append(x)
        elif f == "size":
            for x in v.split("|"):
                if x in SIZE_BUCKETS:
                    self.size_ranges.append(SIZE_BUCKETS[x])
                    continue
                m = re.match(r"^(>=|<=|>|<|=)?\s*([\d.,]+)\s*([kmgt]?b?)$", x)
                if m:
                    num = float(m.group(2).replace(",", "."))
                    mult = _UNIT.get(m.group(3) or "b", 1)
                    self.size_cmp.append((m.group(1) or "=", int(num * mult)))
        elif f in ("datemodified", "datecreated"):
            rng = parse_date_filter(v)
            if rng:
                (self.date_ranges if f == "datemodified" else self.created_ranges).append(rng)
        elif f == "name":
            self.words.append(v)
        elif f == "content":
            self.content_words.append(v)

    @property
    def empty(self) -> bool:
        return not (self.words or self.kinds or self.exts or self.types or self.size_ranges or self.size_cmp
                    or self.date_ranges or self.created_ranges or self.content_words)

    def match_name(self, name: str) -> bool:
        n = name.casefold()
        partial = settings().get("search_partial")
        for w in self.words:
            if partial:
                if w not in n:
                    return False
            else:
                if not re.search(r"(^|[\W_])" + re.escape(w), n):
                    return False
        return True

    def match_meta(self, fi: FileItem) -> bool:
        if self.kinds:
            k = mime.perceived(fi.mime) if not fi.is_dir else "folder"
            if k not in self.kinds:
                return False
        if self.exts:
            low = fi.name.lower()
            if not any(low.endswith(e) for e in self.exts):
                return False
        if self.types:
            t = fi.type_text().casefold()
            if not any(x in t for x in self.types):
                return False
        if self.size_ranges or self.size_cmp:
            if fi.is_dir or fi.size is None:
                return False
            s = fi.size
            ok = any(lo <= s <= hi if lo == hi else lo <= s < hi for lo, hi in self.size_ranges) if self.size_ranges else True
            for op, n in self.size_cmp:
                ok = ok and {"=": s == n, ">": s > n, "<": s < n, ">=": s >= n, "<=": s <= n}[op]
            if not ok:
                return False
        if self.date_ranges:
            if not any(lo <= fi.mtime < hi for lo, hi in self.date_ranges):
                return False
        if self.created_ranges:
            b = fi.btime or 0
            if not any(lo <= b < hi for lo, hi in self.created_ranges):
                return False
        return True

    def needs_content(self) -> bool:
        return bool(self.content_words) or (self.search_content and bool(self.words))


def parse_date_filter(v: str) -> tuple[float, float] | None:
    today = _dt.date.today()

    def ts(d: _dt.date) -> float:
        return time.mktime(d.timetuple())

    words = {
        "today": (today, today + _dt.timedelta(days=1)), "σήμερα": (today, today + _dt.timedelta(days=1)),
        "yesterday": (today - _dt.timedelta(days=1), today), "χθες": (today - _dt.timedelta(days=1), today),
    }
    sw = today - _dt.timedelta(days=today.weekday())
    words.update({
        "thisweek": (sw, today + _dt.timedelta(days=1)), "αυτήτηνεβδομάδα": (sw, today + _dt.timedelta(days=1)),
        "lastweek": (sw - _dt.timedelta(days=7), sw), "τηνπροηγούμενηεβδομάδα": (sw - _dt.timedelta(days=7), sw),
    })
    sm = today.replace(day=1)
    lm = (sm - _dt.timedelta(days=1)).replace(day=1)
    sy = today.replace(month=1, day=1)
    words.update({
        "thismonth": (sm, today + _dt.timedelta(days=1)), "αυτόντονμήνα": (sm, today + _dt.timedelta(days=1)),
        "lastmonth": (lm, sm), "τονπροηγούμενομήνα": (lm, sm),
        "thisyear": (sy, today + _dt.timedelta(days=1)), "φέτος": (sy, today + _dt.timedelta(days=1)),
        "lastyear": (sy.replace(year=sy.year - 1), sy), "πέρυσι": (sy.replace(year=sy.year - 1), sy),
        "earlierthisyear": (sy, lm),
        "alongtimeago": (_dt.date(1970, 1, 2), sy.replace(year=sy.year - 1)),
    })
    key = v.replace(" ", "")
    if key in words:
        a, b = words[key]
        return ts(a), ts(b)

    def pdate(s: str) -> _dt.date | None:
        s = s.strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y"):
            try:
                d = _dt.datetime.strptime(s, fmt).date()
                return d
            except ValueError:
                pass
        return None

    if ".." in v:
        a, b = v.split("..", 1)
        da, db = pdate(a), pdate(b)
        if da and db:
            return ts(da), ts(db + _dt.timedelta(days=1))
    for op in (">=", "<=", ">", "<"):
        if v.startswith(op):
            d = pdate(v[len(op):])
            if d:
                if op in (">", ">="):
                    return ts(d if op == ">=" else d + _dt.timedelta(days=1)), 4e12
                return 0.0, ts(d + _dt.timedelta(days=1) if op == "<=" else d)
    d = pdate(v)
    if d:
        if re.fullmatch(r"\d{4}", v.strip()):
            return ts(d), ts(d.replace(year=d.year + 1))
        return ts(d), ts(d + _dt.timedelta(days=1))
    return None


def scope_roots(scope: str) -> list[str]:
    k = locations.kind(scope)
    if k == "fs":
        return [scope]
    if k == "library":
        lib = places.libraries().get(locations.library_name(scope))
        return [p for p in (lib or {}).get("locations", []) if os.path.isdir(p)]
    if k == "libraries":
        out = []
        for lib in places.libraries().all():
            out += [p for p in lib["locations"] if os.path.isdir(p)]
        return out
    if k == "computer":
        out = []
        for d in places.drives().list():
            m = d.get("mount")
            if m and d.get("kind") != "network":
                out.append(m)
        return out or ["/"]
    if k == "zip":
        zp, _ = locations.ziputil.split(scope)
        return [os.path.dirname(zp)]
    return [places.HOME]


_TEXTLIKE = ("text/", "application/json", "application/xml", "application/javascript", "application/x-shellscript",
             "application/x-desktop", "application/sql", "application/x-yaml", "application/toml")


def content_match(path: str, words: list[str], mt) -> bool:
    n = mt.name()
    if not (n.startswith(_TEXTLIKE) or mt.inherits("text/plain")):
        return False
    try:
        if os.path.getsize(path) > 20 * 1024 * 1024:
            return False
        with open(path, "rb") as f:
            data = f.read().decode("utf-8", "ignore").casefold()
    except OSError:
        return False
    return all(w in data for w in words)


class SearchProvider(locations.Provider):
    folder_type = "search"

    def __init__(self, location: str):
        super().__init__(location)
        s = locations.parse_search(location)
        self.scope = s["scope"]
        self.query = Query(s["q"], s["content"] or settings().get("search_contents"))
        self.count = 0

    def title(self):
        return T("search_results_in", locations.display_name(self.scope))

    def empty_text(self):
        return T("no_items_match")

    def load(self, emit, cancelled):
        q = self.query
        if q.empty:
            return
        roots = scope_roots(self.scope)
        show_hidden = settings().get("show_hidden")
        subfolders = settings().get("search_subfolders")
        zips = settings().get("search_compressed")
        seen_real = set()
        batch = []
        last = time.time()
        cwords = q.content_words + (q.words if q.search_content else [])
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                if cancelled():
                    return
                if dirpath in SKIP_DIRS:
                    dirnames[:] = []
                    continue
                if not show_hidden:
                    dirnames[:] = [d for d in dirnames if not util.is_hidden_name(d)]
                    filenames = [f for f in filenames if not util.is_hidden_name(f)]
                dirnames[:] = [d for d in dirnames if os.path.join(dirpath, d) not in SKIP_DIRS]
                try:
                    rp = os.path.realpath(dirpath)
                except OSError:
                    rp = dirpath
                if rp in seen_real:
                    dirnames[:] = []
                    continue
                seen_real.add(rp)
                for name in dirnames + filenames:
                    name_ok = q.match_name(name)
                    full = os.path.join(dirpath, name)
                    if not name_ok and not (cwords and name in filenames):
                        continue
                    fi = FileItem.from_path(full, name)
                    if not q.match_meta(fi):
                        continue
                    if q.content_words and not fi.is_dir:
                        if not content_match(full, q.content_words, fi.mime):
                            continue
                    elif not name_ok:
                        if fi.is_dir or not content_match(full, cwords, fi.mime):
                            continue
                    fi.extra["in_folder"] = dirpath
                    batch.append(fi)
                    if zips and not fi.is_dir and ziputil.fmt_by_name(name) and ziputil.is_archive_path(full):
                        batch += self._zip_hits(full, q)
                if zips and q.words:
                    for name in filenames:
                        if ziputil.fmt_by_name(name) and not q.match_name(name):
                            full2 = os.path.join(dirpath, name)
                            if ziputil.is_archive_path(full2):
                                batch += self._zip_hits(full2, q)
                if batch and (len(batch) > 200 or time.time() - last > 0.4):
                    self.count += len(batch)
                    emit(batch)
                    batch = []
                    last = time.time()
                if not subfolders and dirpath == root:
                    dirnames[:] = []
        if batch:
            self.count += len(batch)
            emit(batch)

    def _zip_hits(self, zp: str, q: Query) -> list[FileItem]:
        out = []
        try:
            members = ziputil.index(zp)
        except (OSError, EOFError, ValueError, Exception):
            return out
        for m in members:
            base = m.name.split("/")[-1]
            if base and q.words and q.match_name(base):
                fi = FileItem(base, ziputil.make(zp, m.name), "zipdir" if m.is_dir else "zipfile", m.is_dir)
                fi.size = None if m.is_dir else m.size
                fi.mtime = m.mtime
                fi.extra.update(zip=zp, member=m.name, csize=m.csize,
                                in_folder=zp + ("/" + os.path.dirname(m.name) if "/" in m.name else ""))
                out.append(fi)
        return out
