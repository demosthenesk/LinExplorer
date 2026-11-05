"""Compressed folders: ZIP, TAR, TAR.GZ/TGZ, TAR.BZ2/TBZ2, TAR.XZ and single .gz/.bz2/.xz files.

Browse them like folders, extract, add, delete and create. (The module keeps its historical
name; it handles every archive format.)
"""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import shutil
import stat
import tarfile
import tempfile
import time
import zipfile

ZIP_EXTS = (".zip", ".jar", ".cbz", ".xpi", ".apk", ".epub")
BROWSE_ZIP = (".zip", ".jar", ".cbz", ".xpi")
TAR_EXTS = {".tar": "tar", ".tar.gz": "tar.gz", ".tgz": "tar.gz", ".taz": "tar.gz",
            ".tar.bz2": "tar.bz2", ".tbz2": "tar.bz2", ".tbz": "tar.bz2", ".tb2": "tar.bz2",
            ".tar.xz": "tar.xz", ".txz": "tar.xz"}
SINGLE_EXTS = {".gz": "gz", ".bz2": "bz2", ".bz": "bz2", ".xz": "xz"}
CREATE_FORMATS = ["zip", "tar", "tar.gz", "tar.bz2", "tar.xz"]
SINGLE_FORMATS = ["gz", "bz2", "xz"]
FORMAT_EXT = {"zip": ".zip", "tar": ".tar", "tar.gz": ".tar.gz", "tar.bz2": ".tar.bz2", "tar.xz": ".tar.xz",
              "gz": ".gz", "bz2": ".bz2", "xz": ".xz"}
_TAR_MODE = {"tar": "", "tar.gz": "gz", "tar.bz2": "bz2", "tar.xz": "xz"}
_OPENERS = {"gz": gzip.open, "bz2": bz2.open, "xz": lzma.open}
_MAGIC = {"gz": b"\x1f\x8b", "bz2": b"BZh", "xz": b"\xfd7zXZ\x00"}


# --------------------------------------------------------------------------- formats
def fmt_by_name(name: str) -> str | None:
    low = name.lower()
    for ext in sorted(TAR_EXTS, key=len, reverse=True):
        if low.endswith(ext):
            return TAR_EXTS[ext]
    if low.endswith(BROWSE_ZIP):
        return "zip"
    for ext, f in SINGLE_EXTS.items():
        if low.endswith(ext):
            return f
    return None


_fmt_cache: dict = {}


def fmt_of(path: str) -> str | None:
    """Archive format of a file on disk (name + content check), or None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    key = (path, st.st_mtime, st.st_size)
    if key in _fmt_cache:
        return _fmt_cache[key]
    f = fmt_by_name(os.path.basename(path))
    ok = None
    try:
        if f == "zip":
            ok = "zip" if zipfile.is_zipfile(path) else None
        elif f in ("tar", "tar.gz", "tar.bz2", "tar.xz"):
            with open(path, "rb") as fh:
                head = fh.read(6)
            comp = _TAR_MODE[f]
            if comp and not head.startswith(_MAGIC[comp]):
                ok = None
            else:
                ok = f if (comp or tarfile.is_tarfile(path)) else None
        elif f in ("gz", "bz2", "xz"):
            with open(path, "rb") as fh:
                head = fh.read(6)
            ok = f if head.startswith(_MAGIC[f]) else None
    except OSError:
        ok = None
    if len(_fmt_cache) > 2000:
        _fmt_cache.clear()
    _fmt_cache[key] = ok
    return ok


def is_archive_path(path: str) -> bool:
    return fmt_of(path) is not None


is_zip_path = is_archive_path         # historical name used across the code


def is_writable(ap: str) -> bool:
    return fmt_of(ap) in ("zip", "tar", "tar.gz", "tar.bz2", "tar.xz") and os.access(ap, os.W_OK)


def split(loc: str) -> tuple[str, str]:
    """'zip:/a/b.tar.gz!dir/sub' -> ('/a/b.tar.gz', 'dir/sub')."""
    body = loc[4:]
    zp, _, inner = body.partition("!")
    return zp, inner.strip("/")


def make(zp: str, inner: str = "") -> str:
    return f"zip:{zp}!{inner.strip('/')}"


def single_name(ap: str) -> str:
    base = os.path.basename(ap)
    low = base.lower()
    for ext in sorted(SINGLE_EXTS, key=len, reverse=True):
        if low.endswith(ext):
            return base[:-len(ext)] or "data"
    return base + ".out"


# --------------------------------------------------------------------------- index
class Member:
    __slots__ = ("name", "is_dir", "size", "csize", "mtime", "mode", "kind", "linkname")

    def __init__(self, name, is_dir, size=0, csize=0, mtime=0.0, mode=0, kind="file", linkname=""):
        self.name, self.is_dir, self.size, self.csize = name, is_dir, size, csize
        self.mtime, self.mode, self.kind, self.linkname = mtime, mode, kind, linkname


def _dt(info: zipfile.ZipInfo) -> float:
    try:
        return time.mktime(info.date_time + (0, 0, -1))
    except (OverflowError, ValueError):
        return 0.0


_index_cache: dict = {}


def index(ap: str) -> list[Member]:
    st = os.stat(ap)
    key = (ap, st.st_mtime, st.st_size)
    hit = _index_cache.get(key)
    if hit is not None:
        return hit
    f = fmt_of(ap)
    out: list[Member] = []
    if f == "zip":
        with zipfile.ZipFile(ap) as zf:
            for i in zf.infolist():
                n = i.filename.replace("\\", "/")
                out.append(Member(n.rstrip("/"), i.is_dir(), i.file_size, i.compress_size, _dt(i),
                                  (i.external_attr >> 16) & 0o7777))
    elif f and f.startswith("tar"):
        with tarfile.open(ap, "r:*") as tf:
            for m in tf.getmembers():
                n = m.name.lstrip("./") if m.name not in (".", "./") else ""
                if not n:
                    continue
                kind = "dir" if m.isdir() else "symlink" if m.issym() else "hardlink" if m.islnk() else \
                    "file" if m.isfile() else "special"
                out.append(Member(n.rstrip("/"), m.isdir(), m.size, m.size, float(m.mtime), m.mode, kind, m.linkname))
    elif f in ("gz", "bz2", "xz"):
        size = st.st_size
        if f == "gz":
            try:
                with open(ap, "rb") as fh:          # ISIZE trailer (mod 2^32)
                    fh.seek(-4, 2)
                    size = int.from_bytes(fh.read(4), "little")
            except OSError:
                pass
        out.append(Member(single_name(ap), False, size, st.st_size, st.st_mtime, 0o644))
    if len(_index_cache) > 64:
        _index_cache.clear()
    _index_cache[key] = out
    return out


class Entry:
    __slots__ = ("name", "path", "is_dir", "size", "csize", "mtime", "info")

    def __init__(self, name, path, is_dir, size=0, csize=0, mtime=0.0, info=None):
        self.name, self.path, self.is_dir = name, path, is_dir
        self.size, self.csize, self.mtime, self.info = size, csize, mtime, info


def list_dir(zp: str, inner: str) -> list[Entry]:
    inner = inner.strip("/")
    prefix = inner + "/" if inner else ""
    out: dict[str, Entry] = {}
    for m in index(zp):
        n = m.name
        if not n.startswith(prefix) or n == inner:
            continue
        rest = n[len(prefix):]
        first, sep, more = rest.partition("/")
        if not first:
            continue
        if sep:
            e = out.get(first)
            if e is None or not e.is_dir:
                out[first] = Entry(first, prefix + first, True)
        elif m.is_dir:
            e = out.get(first)
            if e is None:
                out[first] = Entry(first, prefix + first, True, mtime=m.mtime)
            else:
                e.mtime = m.mtime
        else:
            out[first] = Entry(first, n, False, m.size, m.csize, m.mtime, m)
    return list(out.values())


def member_size(zp: str, inner_path: str, is_dir: bool) -> int:
    p = inner_path.strip("/")
    if not is_dir:
        return sum(m.size for m in index(zp) if m.name == p)
    pre = p + "/"
    return sum(m.size for m in index(zp) if m.name.startswith(pre) and not m.is_dir)


def uncompressed_total(zp: str) -> int:
    return sum(m.size for m in index(zp) if not m.is_dir)


# --------------------------------------------------------------------------- reading
def safe_join(base: str, rel: str) -> str:
    p = os.path.normpath(os.path.join(base, rel))
    if not (p == base or p.startswith(base.rstrip("/") + "/")):
        raise ValueError("unsafe path in archive: " + rel)
    return p


def _copy_stream(src, dst_path: str, progress, cancel, mtime: float = 0, mode: int = 0):
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    with open(dst_path, "wb") as dst:
        while True:
            if cancel and cancel():
                dst.close()
                try:
                    os.remove(dst_path)
                except OSError:
                    pass
                raise InterruptedError
            buf = src.read(1 << 20)
            if not buf:
                break
            dst.write(buf)
            if progress:
                progress(len(buf))
    if mtime:
        try:
            os.utime(dst_path, (mtime, mtime))
        except OSError:
            pass
    if mode & 0o777:
        try:
            os.chmod(dst_path, mode & 0o777 | stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def iter_extract(zp: str, select, target_for, progress=None, cancel=None):
    """One pass over the archive. select(name, is_dir) -> bool; target_for(name, is_dir) -> path or None.
    Returns the list of written paths."""
    f = fmt_of(zp)
    written = []
    if f == "zip":
        with zipfile.ZipFile(zp) as zf:
            for info in zf.infolist():
                n = info.filename.replace("\\", "/").rstrip("/")
                if not n or not select(n, info.is_dir()):
                    continue
                t = target_for(n, info.is_dir())
                if not t:
                    continue
                if info.is_dir():
                    os.makedirs(t, exist_ok=True)
                    continue
                with zf.open(info) as src:
                    _copy_stream(src, t, progress, cancel, _dt(info), (info.external_attr >> 16) & 0o777)
                written.append(t)
    elif f and f.startswith("tar"):
        links = []
        with tarfile.open(zp, "r|*") as tf:          # stream mode: a single sequential pass
            for m in tf:
                if cancel and cancel():
                    raise InterruptedError
                n = m.name.lstrip("./").rstrip("/")
                if not n or not select(n, m.isdir()):
                    continue
                t = target_for(n, m.isdir())
                if not t:
                    continue
                if m.isdir():
                    os.makedirs(t, exist_ok=True)
                elif m.isfile():
                    src = tf.extractfile(m)
                    if src is not None:
                        _copy_stream(src, t, progress, cancel, float(m.mtime), m.mode)
                        written.append(t)
                elif m.issym() or m.islnk():
                    links.append((m, t))
        for m, t in links:                            # links last, only inside the destination
            try:
                base = os.path.dirname(t)
                if m.issym():
                    real = os.path.normpath(os.path.join(base, m.linkname))
                    if os.path.isabs(m.linkname):
                        continue
                    if os.path.lexists(t):
                        os.remove(t)
                    os.symlink(m.linkname, t)
                else:
                    src_name = m.linkname.lstrip("./")
                    src_t = target_for(src_name, False)
                    if src_t and os.path.isfile(src_t):
                        shutil.copy2(src_t, t)
                written.append(t)
            except (OSError, ValueError):
                pass
    elif f in ("gz", "bz2", "xz"):
        name = single_name(zp)
        if select(name, False):
            t = target_for(name, False)
            if t:
                with _OPENERS[f](zp, "rb") as src:
                    _copy_stream(src, t, progress, cancel, os.stat(zp).st_mtime, 0o644)
                written.append(t)
    return written


def copy_out(zp: str, inner_path: str, is_dir: bool, dest: str, progress=None, cancel=None):
    """Copy a member (file or folder) of the archive to dest (the full target path)."""
    p = inner_path.strip("/")
    if not is_dir:
        iter_extract(zp, lambda n, d: n == p and not d, lambda n, d: dest, progress, cancel)
        return
    pre = p + "/"
    os.makedirs(dest, exist_ok=True)
    iter_extract(zp, lambda n, d: n.startswith(pre),
                 lambda n, d: safe_join(dest, n[len(pre):]), progress, cancel)


def extract_all(zp: str, dest: str, progress=None, cancel=None, conflict=None):
    """conflict(target, name, is_dir) -> new target or None (skip)."""
    def target(n, d):
        try:
            t = safe_join(dest, n)
        except ValueError:
            return None
        if conflict and not d and os.path.lexists(t):
            return conflict(t, n, d)
        return t
    os.makedirs(dest, exist_ok=True)
    return iter_extract(zp, lambda n, d: True, target, progress, cancel)


def extract_to_temp(zp: str, member: str) -> str:
    """Extract one file for opening it (Explorer opens files inside archives this way)."""
    tmp = tempfile.mkdtemp(prefix="linexplorer-zip-")
    out = os.path.join(tmp, os.path.basename(member.rstrip("/")))
    copy_out(zp, member, False, out)
    return out


def read_names(zp: str, limit: int = 2000) -> list[str]:
    return [m.name + ("/" if m.is_dir else "") for m in index(zp)[:limit]]


# --------------------------------------------------------------------------- writing
def _walk(paths: list[str], inner: str = ""):
    """Yield (arcname, source path, is_dir) for files/folders to add."""
    inner = inner.strip("/")
    for p in paths:
        base = os.path.basename(p.rstrip("/"))
        arc_base = f"{inner}/{base}" if inner else base
        if os.path.isdir(p) and not os.path.islink(p):
            yield arc_base, p, True
            for root, dirs, files in os.walk(p):
                rel = os.path.relpath(root, p)
                arc_root = arc_base if rel == "." else f"{arc_base}/{rel}"
                for d in sorted(dirs):
                    yield f"{arc_root}/{d}", os.path.join(root, d), True
                for f in sorted(files):
                    yield f"{arc_root}/{f}", os.path.join(root, f), False
        else:
            yield arc_base, p, False


def _zip_write(out: zipfile.ZipFile, items, progress, cancel):
    for arc, src, is_dir in items:
        if cancel and cancel():
            raise InterruptedError
        if is_dir:
            out.writestr(zipfile.ZipInfo.from_file(src, arc + "/"), b"")
        else:
            out.write(src, arc)
            if progress:
                try:
                    progress(os.path.getsize(src))
                except OSError:
                    pass


def _tar_write(out: tarfile.TarFile, items, progress, cancel):
    for arc, src, is_dir in items:
        if cancel and cancel():
            raise InterruptedError
        out.add(src, arcname=arc, recursive=False)
        if progress and not is_dir:
            try:
                progress(os.lstat(src).st_size)
            except OSError:
                pass


def _rewrite(zp: str, drop, new_items, progress=None, cancel=None):
    """Copy the archive to a temp file without members for which drop(name) is true, then add new_items."""
    f = fmt_of(zp) if os.path.exists(zp) else fmt_by_name(zp)
    tmp = zp + ".tmp-linexplorer"
    try:
        if f == "zip":
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
                if os.path.exists(zp):
                    with zipfile.ZipFile(zp) as zf:
                        for info in zf.infolist():
                            if drop(info.filename.replace("\\", "/").rstrip("/")):
                                continue
                            with zf.open(info) as src, out.open(info, "w") as dst:
                                shutil.copyfileobj(src, dst, 1 << 20)
                _zip_write(out, new_items, progress, cancel)
        elif f and f.startswith("tar"):
            mode = "w:" + _TAR_MODE[f] if _TAR_MODE[f] else "w"
            with tarfile.open(tmp, mode) as out:
                if os.path.exists(zp):
                    with tarfile.open(zp, "r|*") as tf:
                        for m in tf:
                            if drop(m.name.lstrip("./").rstrip("/")):
                                continue
                            out.addfile(m, tf.extractfile(m) if m.isfile() else None)
                _tar_write(out, new_items, progress, cancel)
        else:
            raise ValueError("this archive format can't be changed")
        os.replace(tmp, zp)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def add_paths(zp: str, inner: str, paths: list[str], progress=None, cancel=None, replace: set | None = None):
    """Add files/folders under 'inner' (replacing members with the same names)."""
    items = list(_walk(paths, inner))
    names = {a for a, _, _ in items}
    existing = {m.name for m in index(zp)} if os.path.exists(zp) else set()
    f = fmt_of(zp) if os.path.exists(zp) else fmt_by_name(zp)
    if not (names & existing) and os.path.exists(zp) and f in ("zip", "tar"):
        if f == "zip":
            with zipfile.ZipFile(zp, "a", zipfile.ZIP_DEFLATED) as out:
                _zip_write(out, items, progress, cancel)
        else:
            with tarfile.open(zp, "a") as out:
                _tar_write(out, items, progress, cancel)
        return

    def drop(n):
        return n in names or any(n.startswith(x + "/") for x in names if x in existing)
    _rewrite(zp, drop, items, progress, cancel)


def delete_members(zp: str, paths: list[tuple[str, bool]]):
    targets = [(p.strip("/"), d) for p, d in paths]

    def drop(n):
        for p, d in targets:
            if n == p or (d and n.startswith(p + "/")):
                return True
        return False
    _rewrite(zp, drop, [])


def create(zp: str, paths: list[str], progress=None, cancel=None, fmt: str | None = None):
    fmt = fmt or fmt_by_name(zp) or "zip"
    tmp = zp + ".part"
    try:
        if fmt == "zip":
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
                _zip_write(out, _walk(paths), progress, cancel)
        elif fmt in _TAR_MODE:
            mode = "w:" + _TAR_MODE[fmt] if _TAR_MODE[fmt] else "w"
            with tarfile.open(tmp, mode) as out:
                _tar_write(out, _walk(paths), progress, cancel)
        elif fmt in _OPENERS:
            if len(paths) != 1 or os.path.isdir(paths[0]):
                raise ValueError("gz/bz2/xz compress a single file")
            with open(paths[0], "rb") as src, _OPENERS[fmt](tmp, "wb") as dst:
                while True:
                    if cancel and cancel():
                        raise InterruptedError
                    buf = src.read(1 << 20)
                    if not buf:
                        break
                    dst.write(buf)
                    if progress:
                        progress(len(buf))
        else:
            raise ValueError("unknown format " + fmt)
        os.replace(tmp, zp)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def cleanup_tmp():
    tmp = tempfile.gettempdir()
    try:
        for n in os.listdir(tmp):
            if n.startswith("linexplorer-zip-"):
                p = os.path.join(tmp, n)
                if time.time() - os.path.getmtime(p) > 86400:
                    shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def base_name(ap: str) -> str:
    """'photos.tar.gz' -> 'photos' (name for the 'Extract to' folder)."""
    name = os.path.basename(ap)
    low = name.lower()
    for ext in sorted(list(TAR_EXTS) + list(BROWSE_ZIP) + list(SINGLE_EXTS) + list(ZIP_EXTS), key=len, reverse=True):
        if low.endswith(ext) and len(name) > len(ext):
            return name[:-len(ext)]
    return os.path.splitext(name)[0] or name
