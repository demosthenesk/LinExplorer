"""The folder contents model behind every view mode."""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import (QAbstractTableModel, QFileSystemWatcher, QModelIndex, QObject, Qt, QTimer,
                            Signal)
from PySide6.QtGui import QColor, QIcon

from . import icons, locations, mime, places, util
from .i18n import T
from .items import FileItem
from .settings import settings

ItemRole = Qt.ItemDataRole.UserRole + 1
SortRole = Qt.ItemDataRole.UserRole + 2
GroupRole = Qt.ItemDataRole.UserRole + 3
GhostRole = Qt.ItemDataRole.UserRole + 4

# id: (label key, alignment, width, numeric)
COLUMNS = {
    "name": ("col_name", Qt.AlignmentFlag.AlignLeft, 220, False),
    "date_modified": ("col_date_modified", Qt.AlignmentFlag.AlignLeft, 130, True),
    "type": ("col_type", Qt.AlignmentFlag.AlignLeft, 130, False),
    "size": ("col_size", Qt.AlignmentFlag.AlignRight, 80, True),
    "date_created": ("col_date_created", Qt.AlignmentFlag.AlignLeft, 130, True),
    "date_accessed": ("col_date_accessed", Qt.AlignmentFlag.AlignLeft, 130, True),
    "attributes": ("col_attributes", Qt.AlignmentFlag.AlignLeft, 70, False),
    "owner": ("col_owner", Qt.AlignmentFlag.AlignLeft, 100, False),
    "in_folder": ("col_folder", Qt.AlignmentFlag.AlignLeft, 240, False),
    "orig_location": ("col_orig_location", Qt.AlignmentFlag.AlignLeft, 220, False),
    "date_deleted": ("col_date_deleted", Qt.AlignmentFlag.AlignLeft, 130, True),
    "total_size": ("col_total_size", Qt.AlignmentFlag.AlignRight, 90, True),
    "free_space": ("col_free_space", Qt.AlignmentFlag.AlignRight, 90, True),
    "filesystem": ("col_filesystem", Qt.AlignmentFlag.AlignLeft, 80, False),
    "percent_full": ("col_percent_full", Qt.AlignmentFlag.AlignLeft, 110, True),
    "dimensions": ("col_dimensions", Qt.AlignmentFlag.AlignLeft, 90, False),
    "date_taken": ("col_date_taken", Qt.AlignmentFlag.AlignLeft, 130, True),
    "camera_model": ("col_camera_model", Qt.AlignmentFlag.AlignLeft, 120, False),
    "rating": ("col_rating", Qt.AlignmentFlag.AlignLeft, 90, True),
    "track": ("col_track", Qt.AlignmentFlag.AlignRight, 40, True),
    "title": ("col_title", Qt.AlignmentFlag.AlignLeft, 160, False),
    "artists": ("col_artists", Qt.AlignmentFlag.AlignLeft, 150, False),
    "album": ("col_album", Qt.AlignmentFlag.AlignLeft, 150, False),
    "year": ("col_year", Qt.AlignmentFlag.AlignLeft, 50, True),
    "genre": ("col_genre", Qt.AlignmentFlag.AlignLeft, 90, False),
    "length": ("col_length", Qt.AlignmentFlag.AlignLeft, 70, True),
    "bitrate": ("col_bitrate", Qt.AlignmentFlag.AlignLeft, 70, True),
    "authors": ("col_authors", Qt.AlignmentFlag.AlignLeft, 120, False),
    "pages": ("col_pages", Qt.AlignmentFlag.AlignRight, 50, True),
    "compressed_size": ("col_compressed_size", Qt.AlignmentFlag.AlignRight, 90, True),
    "ratio": ("col_ratio", Qt.AlignmentFlag.AlignRight, 50, True),
    "category": ("col_category", Qt.AlignmentFlag.AlignLeft, 120, False),
}
PROP_COLUMNS = {"dimensions", "date_taken", "camera_model", "rating", "track", "title", "artists", "album", "year",
                "genre", "length", "bitrate", "authors", "pages"}

DEFAULT_VIEWS = {
    "general": ("details", ["name", "date_modified", "type", "size"]),
    "documents": ("details", ["name", "date_modified", "type", "size"]),
    "pictures": ("large", ["name", "date_taken", "dimensions", "size", "rating"]),
    "music": ("details", ["name", "track", "title", "artists", "album"]),
    "videos": ("large", ["name", "date_modified", "type", "size", "length"]),
    "computer": ("tiles", ["name", "type", "total_size", "free_space"]),
    "trash": ("details", ["name", "orig_location", "date_deleted", "size", "type", "date_modified"]),
    "libraries": ("tiles", ["name", "type"]),
    "search": ("content", ["name", "date_modified", "type", "size", "in_folder"]),
    "zip": ("details", ["name", "type", "compressed_size", "size", "ratio", "date_modified"]),
    "network": ("tiles", ["name", "type"]),
    "netproto": ("tiles", ["name", "type"]),
}


_custom_icons: dict = {}
_F_BASE = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
_F = {(d, e): _F_BASE | (Qt.ItemFlag.ItemIsDropEnabled if d else Qt.ItemFlag.NoItemFlags)
      | (Qt.ItemFlag.ItemIsEditable if e else Qt.ItemFlag.NoItemFlags) for d in (0, 1) for e in (0, 1)}


class _Loader(QObject):
    batch = Signal(int, list)
    done = Signal(int)
    failed = Signal(int, object)


class FolderModel(QAbstractTableModel):
    loading = Signal(bool)
    load_failed = Signal(object)
    contents_changed = Signal()
    about_to_reset = Signal()
    reset_done = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.provider: locations.Provider | None = None
        self.location = ""
        self.items: list[FileItem] = []
        self.rows: list[FileItem] = []
        self.columns: list[str] = ["name", "date_modified", "type", "size"]
        self.widths: dict[str, int] = {}
        self.sort_col = "name"
        self.sort_desc = False
        self.group_col: str | None = None
        self.group_desc = False
        self.filters: dict[str, set] = {}
        self.ghost: set[str] = set()
        self.is_loading = False
        self.search_terms: list[str] = []
        self._gen = 0
        self._pending: list[FileItem] = []
        self._loader = _Loader()
        self._loader.batch.connect(self._on_batch)
        self._loader.done.connect(self._on_done)
        self._loader.failed.connect(self._on_failed)
        self._flush = QTimer(self)
        self._flush.setInterval(120)
        self._flush.timeout.connect(self._flush_pending)
        self._cancel = threading.Event()
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self._on_dir_changed)
        self._watch_timer = QTimer(self)
        self._watch_timer.setSingleShot(True)
        self._watch_timer.setInterval(350)
        self._watch_timer.timeout.connect(self.reload)
        # while the user is typing a name in the view (rename / new folder), a model reset would destroy the
        # editor: refreshes are held back until editing ends
        self.busy_check = None
        self._deferred = None
        self._defer_timer = QTimer(self)
        self._defer_timer.setSingleShot(True)
        self._defer_timer.setInterval(250)
        self._defer_timer.timeout.connect(self._retry_deferred)
        settings().changed.connect(self._on_setting)
        self._hide_ext = settings().get("hide_extensions")
        self._writable_dirs: dict[str, bool] = {}
        self._last_flush = 0.0

    # ------------------------------------------------------------------ loading
    def _hold(self, fn) -> bool:
        """True (and fn kept for later) when the view is busy editing."""
        if self.busy_check is not None:
            try:
                busy = self.busy_check()
            except RuntimeError:
                busy = False
            if busy:
                self._deferred = fn
                self._defer_timer.start()
                return True
        return False

    def _retry_deferred(self):
        fn, self._deferred = self._deferred, None
        if fn is not None and not self._hold(fn):
            fn()
            if self._deferred is None:
                self.contents_changed.emit()

    def set_location(self, loc: str, provider: locations.Provider | None = None):
        self._deferred = None
        self._defer_timer.stop()
        self._cancel.set()
        self._cancel = threading.Event()
        self._gen += 1
        if self.provider is not None:
            try:
                self.provider.refresh_needed.disconnect(self.reload)
            except (RuntimeError, TypeError):
                pass
        self.location = loc
        self.provider = provider or locations.make_provider(loc)
        self.provider.refresh_needed.connect(self.reload)
        self.search_terms = list(getattr(getattr(self.provider, "query", None), "words", []) or [])
        self.filters = {}
        self.about_to_reset.emit()
        self.beginResetModel()
        self.items = []
        self.rows = []
        self.endResetModel()
        self.reset_done.emit()
        self._set_watch()
        self._start()

    def reload(self):
        self._writable_dirs = {}
        if self.provider is None:
            return
        self._deferred = None            # a fresher listing is on its way
        self._defer_timer.stop()
        self._cancel.set()
        self._cancel = threading.Event()
        self._gen += 1
        self._start(replace=True)

    def _set_watch(self):
        paths = self.watcher.directories() + self.watcher.files()
        if paths:
            self.watcher.removePaths(paths)
        wp = [p for p in self.provider.watch_paths() if os.path.isdir(p)]
        if wp:
            self.watcher.addPaths(wp)

    def _on_dir_changed(self, _p):
        if self.provider is not None and self.provider.folder_type != "search":
            self._watch_timer.start()

    def _start(self, replace: bool = False):
        gen = self._gen
        cancel = self._cancel
        prov = self.provider
        self._replace_pending = replace
        self._pending = []
        self._collected: list[FileItem] = []
        self.is_loading = True
        self.loading.emit(True)
        loader = self._loader

        def work():
            try:
                prov.load(lambda b: loader.batch.emit(gen, b), cancel.is_set)
                loader.done.emit(gen)
            except Exception as e:  # noqa: BLE001
                loader.failed.emit(gen, e)

        util.in_thread(work)
        if not replace:
            self._flush.start()

    def _on_batch(self, gen, batch):
        if gen != self._gen:
            return
        if self._replace_pending:
            self._collected.extend(batch)
        else:
            self._pending.extend(batch)

    def _flush_pending(self, force: bool = False):
        if not self._pending:
            return
        import time as _t
        now = _t.time()
        if not force and self.items and now - self._last_flush < 0.8:
            return
        if self._hold(lambda: self._flush_pending(force=True)):
            return
        self._last_flush = now
        new = self._pending
        self._pending = []
        self.about_to_reset.emit()
        self.beginResetModel()
        self.items.extend(new)
        self._resort()
        self.endResetModel()
        self.reset_done.emit()
        self.contents_changed.emit()

    def _on_done(self, gen):
        if gen != self._gen:
            return
        self._flush.stop()
        if self._replace_pending:
            self._replace_with(self._collected)
        else:
            self._flush_pending(force=True)
        self.is_loading = False
        self.loading.emit(False)
        self.contents_changed.emit()

    def _on_failed(self, gen, exc):
        if gen != self._gen:
            return
        self._flush.stop()
        self._flush_pending(force=True)
        self.is_loading = False
        self.loading.emit(False)
        self.load_failed.emit(exc)

    def _replace_with(self, new_items: list[FileItem]):
        if self._hold(lambda: self._replace_with(new_items)):
            return
        old = {it.path: it for it in self.items}
        merged = []
        for it in new_items:
            o = old.get(it.path)
            if o is not None and o.mtime == it.mtime and o.size == it.size and o.kind == it.kind:
                o.extra.update(it.extra)
                merged.append(o)
            else:
                merged.append(it)
        self.about_to_reset.emit()
        self.beginResetModel()
        self.items = merged
        self._resort()
        self.endResetModel()
        self.reset_done.emit()

    def _on_setting(self, key):
        if key in ("show_hidden", "hide_empty_drives", "show_drive_letters"):
            self.reload()
        elif key in ("hide_extensions",):
            self._hide_ext = settings().get("hide_extensions")
            for it in self.items:
                it.sk = {}
            self.relayout()

    # ------------------------------------------------------------------ sorting / grouping
    def sort_value(self, it: FileItem, col: str):
        v = it.sk.get(col)
        if v is None:
            v = it.sk[col] = self._sort_value(it, col)
        return v

    def _sort_value(self, it: FileItem, col: str):
        if col == "name":
            return util.natural_key(self.display_name(it))
        if col == "date_modified":
            return it.mtime or 0
        if col == "date_created":
            return it.btime or 0
        if col == "date_accessed":
            return it.atime or 0
        if col == "date_deleted":
            return it.extra.get("deleted", 0)
        if col in ("size", "total_size"):
            return it.size if it.size is not None else -1
        if col == "free_space":
            return it.extra.get("free") or -1
        if col == "percent_full":
            d = it.extra.get("drive") or {}
            return (d.get("used", 0) / d["size"]) if d.get("size") else -1
        if col in ("rating", "track", "year", "pages"):
            v = it.props().get(col)
            try:
                return int(str(v).split("/")[0]) if v not in (None, "") else -1
            except ValueError:
                return -1
        if col == "length":
            return it.props().get("_length_s", -1)
        if col == "date_taken":
            return it.props().get("_date_taken_ts", 0)
        if col == "compressed_size":
            return it.extra.get("csize", -1)
        if col == "ratio":
            return it.extra.get("ratio", -1)
        if col == "category":
            return self.group_info(it, "category")[0]
        return util.natural_key(str(self.text(it, col)))

    def _sort_key(self, col):
        def key(it: FileItem):
            v = self.sort_value(it, col)
            tie = it.sk.get("__tie")
            if tie is None:
                tie = it.sk["__tie"] = util.natural_key(it.name)
            return (0 if it.is_dir and it.kind not in ("drive", "library") else 1, v, tie)
        return key

    def _resort(self):
        vis = [it for it in self.items if self._passes(it)]
        try:
            vis.sort(key=self._sort_key(self.sort_col), reverse=self.sort_desc)
        except TypeError:
            vis.sort(key=lambda it: util.natural_key(it.name), reverse=self.sort_desc)
        if self.group_col:
            gk = {id(it): self.group_info(it, self.group_col)[0] for it in vis}
            vis.sort(key=lambda it: gk[id(it)], reverse=self.group_desc)
        self.rows = vis

    def _passes(self, it: FileItem) -> bool:
        for col, allowed in self.filters.items():
            if allowed and self.group_info(it, col)[1] not in allowed:
                return False
        return True

    def relayout(self):
        self.about_to_reset.emit()
        self.beginResetModel()
        self._resort()
        self.endResetModel()
        self.reset_done.emit()
        self.contents_changed.emit()

    def set_sort(self, col: str, desc: bool | None = None):
        if desc is None:
            desc = (not self.sort_desc) if col == self.sort_col else False
        self.sort_col, self.sort_desc = col, desc
        self.relayout()

    def set_group(self, col: str | None, desc: bool | None = None):
        self.group_col = col
        if desc is not None:
            self.group_desc = desc
        self.relayout()

    def set_filter(self, col: str, values: set | None):
        if values:
            self.filters[col] = set(values)
        else:
            self.filters.pop(col, None)
        self.relayout()

    def group_info(self, it: FileItem, col: str) -> tuple:
        """(order, key, label)."""
        if col == "name":
            o, k = util.name_group(self.display_name(it))
            return o, k, T("other") if k == "other" else k
        if col in ("date_modified", "date_created", "date_accessed", "date_deleted", "date_taken"):
            ts = {"date_modified": it.mtime, "date_created": it.btime, "date_accessed": it.atime,
                  "date_deleted": it.extra.get("deleted"), "date_taken": it.props().get("_date_taken_ts")}[col]
            o, k = util.date_group(ts)
            return o, k, T("grp_" + k)
        if col == "size":
            o, k = util.size_group(it.size, it.is_dir)
            return o, k, T("grp_" + k)
        if col == "category":
            nc = it.extra.get("netcat")
            if nc:
                o = {"connected": 0, "saved": 1, "discovered": 2}.get(nc, 3)
                return o, nc, T("netcat_" + nc)
            if it.kind == "drive":
                c = (it.extra.get("drive") or {}).get("category", "hdd")
                o = {"hdd": 0, "removable": 1, "network": 2}.get(c, 3)
                return o, c, T({"hdd": "hard_disk_drives", "removable": "removable_devices",
                                "network": "network_location"}.get(c, "other"))
            return 3, "other", T("other")
        if col == "rating":
            r = it.props().get("rating")
            if not r:
                return 9, "unrated", T("unrated")
            return 5 - int(r), str(r), T("n_stars", r)
        if col in ("type",):
            t = self.text(it, col)
            return (util.natural_key(t), t, t)
        v = self.text(it, col)
        if not v:
            return ([(2, "", 0)], "", T("unspecified"))
        return (util.natural_key(v), v, v)

    def groups(self) -> list[tuple[str, str, int, int]]:
        """[(key, label, first_row, count)] in display order (contiguous)."""
        if not self.group_col:
            return []
        out = []
        cur = None
        for i, it in enumerate(self.rows):
            o, k, label = self.group_info(it, self.group_col)
            if cur is None or cur[0] != k:
                cur = [k, label, i, 0]
                out.append(cur)
            cur[3] += 1
        return [tuple(g) for g in out]

    def filter_choices(self, col: str) -> list[tuple[str, str, int]]:
        seen: dict[str, list] = {}
        for it in self.items:
            o, k, label = self.group_info(it, col)
            if k not in seen:
                seen[k] = [o, label, 0]
            seen[k][2] += 1
        return [(k, v[1], v[2]) for k, v in sorted(seen.items(), key=lambda kv: kv[1][0])]

    # ------------------------------------------------------------------ text
    def display_name(self, it: FileItem) -> str:
        dn = it.sk.get("__dn")
        if dn is None:
            dn = it.sk["__dn"] = self._display_name(it)
        return dn

    def _display_name(self, it: FileItem) -> str:
        if it.kind in ("file", "zipfile", "trash") and not it.is_dir and self._hide_ext:
            base, ext = util.split_ext(it.name)
            if ext and base:
                mt = it.mime
                if not mt.isDefault() and mt.name() != "application/x-desktop":
                    return base
                if mt.name() == "application/x-desktop":
                    return base
        return it.name

    def text(self, it: FileItem, col: str) -> str:
        if col == "name":
            return self.display_name(it)
        if col == "date_modified":
            return util.fmt_datetime(it.mtime) if it.kind not in ("drive", "library") and it.mtime else ""
        if col == "type":
            return it.type_text()
        if col == "size":
            if it.is_dir or it.kind in ("drive", "library", "net"):
                return ""
            return util.fmt_size_kb(it.size)
        if col == "date_created":
            return util.fmt_datetime(it.btime) if it.kind in ("file", "dir", "trash") else ""
        if col == "date_accessed":
            return util.fmt_datetime(it.atime) if it.kind in ("file", "dir") else ""
        if col == "attributes":
            if it.kind not in ("file", "dir", "trash"):
                return ""
            a = ""
            if not os.access(it.path, os.W_OK):
                a += "R"
            if it.hidden:
                a += "H"
            if it.is_link:
                a += "L"
            if it.is_executable:
                a += "X"
            return a
        if col == "owner":
            if it.uid < 0:
                return ""
            try:
                import pwd
                return pwd.getpwuid(it.uid).pw_name
            except KeyError:
                return str(it.uid)
        if col == "in_folder":
            return it.extra.get("in_folder", "")
        if col == "orig_location":
            return it.extra.get("orig_dir", "")
        if col == "date_deleted":
            return util.fmt_datetime(it.extra.get("deleted"))
        if col == "total_size":
            return util.fmt_size_short(it.size) if it.kind == "drive" and it.size else ""
        if col == "free_space":
            f = it.extra.get("free")
            return util.fmt_size_short(f) if f is not None and it.kind == "drive" else ""
        if col == "filesystem":
            return (it.extra.get("drive") or {}).get("fstype", "").upper()
        if col == "percent_full":
            d = it.extra.get("drive") or {}
            return f"{round(100 * d.get('used', 0) / d['size'])}%" if d.get("size") and d.get("used") is not None else ""
        if col == "compressed_size":
            return util.fmt_size_kb(it.extra.get("csize")) if it.kind == "zipfile" else ""
        if col == "ratio":
            r = it.extra.get("ratio")
            return f"{r}%" if r is not None else ""
        if col == "category":
            return self.group_info(it, "category")[2]
        if col in PROP_COLUMNS:
            v = it.props().get(col)
            if col == "rating":
                return ""
            return "" if v in (None, "") else str(v)
        return ""

    def tooltip(self, it: FileItem) -> str:
        if not settings().get("popup_descriptions"):
            return ""
        lines = []
        if it.kind == "drive":
            d = it.extra.get("drive") or {}
            if d.get("size"):
                lines.append(f"{T('col_free_space')}: {util.fmt_size_short(d.get('free') or 0)}")
                lines.append(f"{T('col_total_size')}: {util.fmt_size_short(d['size'])}")
            return "\n".join(lines)
        lines.append(f"{T('col_type')}: {it.type_text()}")
        p = it.props() if it.kind == "file" else {}
        for k, key in (("artists", "col_artists"), ("album", "col_album"), ("dimensions", "col_dimensions"),
                       ("date_taken", "col_date_taken"), ("length", "col_length"), ("authors", "col_authors")):
            if p.get(k):
                lines.append(f"{T(key)}: {p[k]}")
        if not it.is_dir and it.size is not None and settings().get("size_in_tips"):
            lines.append(f"{T('col_size')}: {util.fmt_size_short(it.size)}")
        if it.mtime:
            lines.append(f"{T('col_date_modified')}: {util.fmt_datetime(it.mtime)}")
        if it.is_link:
            lines.append(f"{T('col_link_target')}: {it.link}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ icons
    def icon(self, it: FileItem) -> QIcon:
        if it.kind == "drive":
            return icons.drive((it.extra.get("drive") or {}).get("kind", "hdd"))
        if it.kind == "library":
            return icons.library((it.extra.get("library") or {}).get("type", ""))
        if it.kind == "special" and it.path.startswith("shell:network:"):
            return icons.net_proto(it.path.rsplit(":", 1)[1])
        if it.kind == "net":
            if it.extra.get("netcat") == "saved":
                return icons.folder("remote")
            return icons.network_server() if it.path.count("/") <= 4 else icons.folder("remote")
        if it.is_dir:
            if it.kind == "dir":
                b = settings().bag(it.path)
                if b and (b.get("icon") or b.get("picture")):
                    f = b.get("icon") or b.get("picture")
                    if os.path.exists(f):
                        ic = _custom_icons.get(f)
                        if ic is None:
                            ic = _custom_icons[f] = QIcon(f)
                        return ic
            if it.kind in ("dir", "trash") and it.path:
                sk = places.special_kind(it.path)
                if sk:
                    return icons.folder({"download": "downloads", "publicshare": "public"}.get(sk, sk))
                if it.extra.get("netdrive"):
                    return icons.drive("network")
            return icons.folder()
        mt = it.mime
        if mt.name() == "application/x-desktop" and it.kind == "file":
            from .mime import DesktopEntry
            e = DesktopEntry(it.path, os.path.basename(it.path))
            if e.icon:
                return e.qicon()
        return icons.for_mime(mt)

    # ------------------------------------------------------------------ Qt model API
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def item(self, row: int) -> FileItem | None:
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def item_at(self, index: QModelIndex) -> FileItem | None:
        return self.item(index.row()) if index.isValid() else None

    def row_of_path(self, path: str) -> int:
        for i, it in enumerate(self.rows):
            if it.path == path:
                return i
        return -1

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        it = self.rows[index.row()]
        col = self.columns[index.column()] if index.column() < len(self.columns) else "name"
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if role == Qt.ItemDataRole.EditRole:
                return self.display_name(it)
            return self.text(it, col)
        if role == Qt.ItemDataRole.DecorationRole and col == "name":
            return self.icon(it)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.tooltip(it)
        if role == ItemRole:
            return it
        if role == GhostRole:
            return it.path in self.ghost or it.hidden
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return COLUMNS.get(col, COLUMNS["name"])[1] | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.ForegroundRole and col != "name":
            return QColor(109, 109, 109)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal or section >= len(self.columns):
            return None
        col = self.columns[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return T(COLUMNS[col][0])
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        return None

    def flags(self, index):
        if not index.isValid():
            return _F_BASE
        it = self.rows[index.row()]
        return _F[(1 if (it.is_dir or it.kind in ("drive", "library")) else 0, 1 if self.can_rename(it) else 0)]

    def can_rename(self, it: FileItem) -> bool:
        if it.kind in ("file", "dir"):
            d = it.path.rpartition("/")[0] or "/"
            w = self._writable_dirs.get(d)
            if w is None:
                w = self._writable_dirs[d] = os.access(d, os.W_OK)
            return w
        return it.kind == "library"

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        it = self.rows[index.row()]
        self.rename_requested.emit(it, str(value))
        return False

    rename_requested = Signal(object, str)

    def set_columns(self, cols: list[str], widths: dict | None = None):
        self.beginResetModel()
        self.columns = [c for c in cols if c in COLUMNS] or ["name"]
        if "name" not in self.columns:
            self.columns.insert(0, "name")
        if widths:
            self.widths.update(widths)
        self.endResetModel()

    def width_of(self, col: str) -> int:
        return int(self.widths.get(col) or COLUMNS[col][2])

    def set_ghost(self, paths: set[str]):
        self.ghost = set(paths)
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, max(0, len(self.columns) - 1)))

    def summary_counts(self) -> int:
        return len(self.rows)

    def target_dir(self) -> str | None:
        return self.provider.target_dir() if self.provider else None
