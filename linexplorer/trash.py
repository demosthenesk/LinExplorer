"""freedesktop.org Trash (the Recycle Bin): listing, restoring, emptying."""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import urllib.parse

from . import places, util

UID = os.getuid()


def home_trash() -> str:
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "Trash")


def trash_dirs() -> list[tuple[str, str]]:
    """[(trash_dir, topdir)] - topdir is '' for the home trash (absolute paths in .trashinfo)."""
    out = [(home_trash(), "")]
    seen = {os.path.realpath(home_trash())}
    for d in places.drives().list():
        m = d.get("mount")
        if not m or d.get("kind") == "network":
            continue
        for cand in (os.path.join(m, ".Trash", str(UID)), os.path.join(m, f".Trash-{UID}")):
            if os.path.isdir(os.path.join(cand, "files")):
                rp = os.path.realpath(cand)
                if rp not in seen:
                    seen.add(rp)
                    out.append((cand, m))
    return out


class TrashEntry:
    __slots__ = ("trash_dir", "topdir", "name", "files_path", "info_path", "orig_path", "deleted", "size",
                 "is_dir", "mtime")

    def __init__(self, trash_dir, topdir, name):
        self.trash_dir = trash_dir
        self.topdir = topdir
        self.name = name
        self.files_path = os.path.join(trash_dir, "files", name)
        self.info_path = os.path.join(trash_dir, "info", name + ".trashinfo")
        self.orig_path = ""
        self.deleted = 0.0
        self.size = None
        self.is_dir = os.path.isdir(self.files_path) and not os.path.islink(self.files_path)
        try:
            self.mtime = os.lstat(self.files_path).st_mtime
        except OSError:
            self.mtime = 0
        self._read_info()

    def _read_info(self):
        try:
            with open(self.info_path, encoding="utf-8", errors="replace") as f:
                for ln in f:
                    if ln.startswith("Path="):
                        p = urllib.parse.unquote(ln[5:].strip())
                        self.orig_path = p if os.path.isabs(p) else os.path.join(self.topdir or "/", p)
                    elif ln.startswith("DeletionDate="):
                        try:
                            self.deleted = _dt.datetime.fromisoformat(ln[13:].strip()).timestamp()
                        except ValueError:
                            pass
        except OSError:
            pass
        if not self.orig_path:
            self.orig_path = os.path.join(self.topdir or os.path.expanduser("~"), self.name)

    @property
    def display_name(self) -> str:
        return os.path.basename(self.orig_path) or self.name


def list_entries() -> list[TrashEntry]:
    out = []
    for td, top in trash_dirs():
        fdir = os.path.join(td, "files")
        try:
            names = os.listdir(fdir)
        except OSError:
            continue
        for n in names:
            out.append(TrashEntry(td, top, n))
    return out


def is_empty() -> bool:
    for td, _ in trash_dirs():
        try:
            if os.listdir(os.path.join(td, "files")):
                return False
        except OSError:
            pass
    return True


def entry_from_files_path(path: str) -> TrashEntry | None:
    fdir = os.path.dirname(path)
    td = os.path.dirname(fdir)
    if os.path.basename(fdir) != "files":
        return None
    top = ""
    for t, tp in trash_dirs():
        if os.path.realpath(t) == os.path.realpath(td):
            top = tp
    return TrashEntry(td, top, os.path.basename(path))


def _topdir_of(path: str) -> str:
    """Mount point containing path."""
    p = os.path.abspath(path)
    dev = os.lstat(p).st_dev
    while True:
        parent = os.path.dirname(p)
        if parent == p:
            return p
        try:
            if os.lstat(parent).st_dev != dev:
                return p
        except OSError:
            return p
        p = parent


def move_to_trash(path: str) -> str | None:
    """freedesktop.org trash spec. Returns the location inside the trash, or None."""
    path = os.path.abspath(path)
    try:
        st = os.lstat(path)
    except OSError:
        return None
    ht = home_trash()
    os.makedirs(os.path.join(ht, "files"), exist_ok=True)
    os.makedirs(os.path.join(ht, "info"), exist_ok=True)
    if os.stat(ht).st_dev == st.st_dev:
        tdir, top = ht, ""
    else:
        top = _topdir_of(path)
        tdir = ""
        shared = os.path.join(top, ".Trash")
        try:
            sst = os.lstat(shared)
            import stat as _st
            if _st.S_ISDIR(sst.st_mode) and not _st.S_ISLNK(sst.st_mode) and sst.st_mode & _st.S_ISVTX:
                cand = os.path.join(shared, str(UID))
                os.makedirs(os.path.join(cand, "files"), mode=0o700, exist_ok=True)
                os.makedirs(os.path.join(cand, "info"), mode=0o700, exist_ok=True)
                tdir = cand
        except OSError:
            pass
        if not tdir:
            cand = os.path.join(top, f".Trash-{UID}")
            try:
                os.makedirs(os.path.join(cand, "files"), mode=0o700, exist_ok=True)
                os.makedirs(os.path.join(cand, "info"), mode=0o700, exist_ok=True)
                tdir = cand
            except OSError:
                return None
    base = os.path.basename(path.rstrip("/")) or "file"
    name = base
    n = 1
    while True:
        info = os.path.join(tdir, "info", name + ".trashinfo")
        try:
            fd = os.open(info, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            n += 1
            b, e = util.split_ext(base)
            name = f"{b}.{n}{e}"
        except OSError:
            return None
    rel = path if not top else os.path.relpath(path, top)
    content = "[Trash Info]\nPath={}\nDeletionDate={}\n".format(
        urllib.parse.quote(rel), _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    dest = os.path.join(tdir, "files", name)
    try:
        os.rename(path, dest)
    except OSError:
        try:
            os.remove(info)
        except OSError:
            pass
        return None
    return dest


def restore(entry: TrashEntry, dest: str | None = None) -> str:
    dest = dest or entry.orig_path
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.lexists(dest):
        raise FileExistsError(dest)
    try:
        os.rename(entry.files_path, dest)
    except OSError:
        shutil.move(entry.files_path, dest)
    try:
        os.remove(entry.info_path)
    except OSError:
        pass
    _drop_dirsize(entry)
    return dest


def delete_entry(entry: TrashEntry):
    p = entry.files_path
    if os.path.isdir(p) and not os.path.islink(p):
        shutil.rmtree(p, ignore_errors=True)
    else:
        try:
            os.remove(p)
        except OSError:
            pass
    try:
        os.remove(entry.info_path)
    except OSError:
        pass
    _drop_dirsize(entry)


def _drop_dirsize(entry: TrashEntry):
    ds = os.path.join(entry.trash_dir, "directorysizes")
    if not os.path.exists(ds):
        return
    try:
        with open(ds, encoding="utf-8") as f:
            lines = f.readlines()
        key = urllib.parse.quote(entry.name)
        keep = [ln for ln in lines if ln.rstrip("\n").split(" ")[-1] != key]
        if len(keep) != len(lines):
            with open(ds, "w", encoding="utf-8") as f:
                f.writelines(keep)
    except OSError:
        pass


def empty(progress=None):
    entries = list_entries()
    for i, e in enumerate(entries):
        delete_entry(e)
        if progress:
            progress(i + 1, len(entries))


def total_size() -> tuple[int, int]:
    n = 0
    size = 0
    for e in list_entries():
        n += 1
        size += tree_size(e.files_path)
    return n, size


def tree_size(path: str) -> int:
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not os.path.isdir(path) or os.path.islink(path):
        return st.st_size
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def trash_dir_for(path: str) -> str:
    d = places.drives().for_path(path)
    if d and d.get("mount") and d["mount"] != "/" and not util.same_device(path, home_trash()):
        return os.path.join(d["mount"], f".Trash-{UID}")
    return home_trash()


# --------------------------------------------------------------------------- change notification
_watcher = None


def watcher():
    """A QObject with a 'changed' signal emitted (debounced) when anything enters or leaves any trash."""
    global _watcher
    if _watcher is None:
        from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

        class _W(QObject):
            changed = Signal()

            def __init__(self):
                super().__init__()
                self.fsw = QFileSystemWatcher(self)
                self.timer = QTimer(self)
                self.timer.setSingleShot(True)
                self.timer.setInterval(300)
                self.timer.timeout.connect(self.changed.emit)
                self.fsw.directoryChanged.connect(lambda _p: self.timer.start())
                places.drives().changed.connect(self.rewatch)
                self.rewatch()

            def rewatch(self):
                want = []
                os.makedirs(os.path.join(home_trash(), "files"), exist_ok=True)
                os.makedirs(os.path.join(home_trash(), "info"), exist_ok=True)
                for td, _top in trash_dirs():
                    want.append(os.path.join(td, "files"))
                have = set(self.fsw.directories())
                add = [d for d in want if d not in have and os.path.isdir(d)]
                if add:
                    self.fsw.addPaths(add)
                gone = [d for d in have if d not in want]
                if gone:
                    self.fsw.removePaths(gone)
                self.timer.start()

        _watcher = _W()
    return _watcher
