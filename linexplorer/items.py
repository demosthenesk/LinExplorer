"""The item shown in a folder view."""
from __future__ import annotations

import os
import stat as _stat

from . import mime as _mime
from . import util


class FileItem:
    __slots__ = ("name", "path", "kind", "is_dir", "size", "mtime", "atime", "ctime", "mode", "uid", "gid",
                 "hidden", "link", "extra", "_mime", "_btime", "_props", "dev", "ino", "sk")

    def __init__(self, name: str, path: str, kind: str = "file", is_dir: bool = False):
        self.name = name
        self.path = path
        self.kind = kind
        self.is_dir = is_dir
        self.size = None
        self.mtime = 0.0
        self.atime = 0.0
        self.ctime = 0.0
        self.mode = 0
        self.uid = -1
        self.gid = -1
        self.hidden = False
        self.link = None
        self.extra = {}
        self._mime = None
        self._btime = None
        self._props = None
        self.dev = 0
        self.ino = 0
        self.sk = {}

    # ---------------------------------------------------------------- construction
    @classmethod
    def from_path(cls, path: str, name: str | None = None, entry: os.DirEntry | None = None) -> "FileItem":
        name = name or os.path.basename(path.rstrip("/")) or path
        it = cls(name, path)
        try:
            lst = entry.stat(follow_symlinks=False) if entry is not None else os.lstat(path)
        except OSError:
            it.kind = "file"
            return it
        if _stat.S_ISLNK(lst.st_mode):
            try:
                it.link = os.readlink(path)
            except OSError:
                it.link = ""
            try:
                st = os.stat(path)
            except OSError:
                st = lst          # dangling link
        else:
            st = lst
        it.is_dir = _stat.S_ISDIR(st.st_mode)
        it.kind = "dir" if it.is_dir else "file"
        it.size = None if it.is_dir else st.st_size
        it.mtime = st.st_mtime
        it.atime = st.st_atime
        it.ctime = st.st_ctime
        it.mode = st.st_mode
        it.uid = st.st_uid
        it.gid = st.st_gid
        it.dev = st.st_dev
        it.ino = st.st_ino
        it.hidden = util.is_hidden_name(name)
        return it

    # ---------------------------------------------------------------- properties
    @property
    def fs_path(self) -> str | None:
        if self.kind in ("file", "dir", "trash"):
            return self.path
        if self.kind == "drive":
            return self.extra.get("drive", {}).get("mount") or None
        return None

    @property
    def is_fs(self) -> bool:
        return self.kind in ("file", "dir")

    @property
    def mime(self):
        if self._mime is None:
            if self.kind in ("dir", "zipdir"):
                self._mime = _mime.db().mimeTypeForName("inode/directory")
            elif self.kind in ("file", "trash"):
                if self.is_dir:
                    self._mime = _mime.db().mimeTypeForName("inode/directory")
                else:
                    self._mime = _mime.mime_for(self.path)
            elif self.kind == "zipfile":
                self._mime = _mime.mime_for_name(self.name)
            elif self.kind == "netfile":
                mt = _mime.db().mimeTypeForName(self.extra.get("mime") or "")
                self._mime = mt if mt.isValid() and not mt.isDefault() else _mime.mime_for_name(self.name)
            else:
                self._mime = _mime.db().mimeTypeForName("inode/directory")
        return self._mime

    @property
    def btime(self) -> float | None:
        if self._btime is None:
            if self.kind in ("file", "dir"):
                self._btime = util.birth_time(self.path, self.ctime) or 0.0
            else:
                self._btime = self.mtime or 0.0
        return self._btime or None

    @property
    def is_link(self) -> bool:
        return self.link is not None

    @property
    def is_executable(self) -> bool:
        return self.kind == "file" and bool(self.mode & 0o111) and not self.is_dir

    def type_text(self) -> str:
        from .i18n import T
        if self.kind == "drive":
            d = self.extra.get("drive", {})
            if d.get("kind") == "network":
                return T("network_location")
            if d.get("kind") == "optical":
                return T("cd_drive")
            if d.get("removable"):
                return T("removable_disk")
            return T("local_disk")
        if self.kind == "library":
            return T("library")
        if self.kind == "net":
            return self.extra.get("type", T("network_location"))
        if self.kind == "special":
            return self.extra.get("type", T("system_folder"))
        if self.is_link and self.is_dir:
            return T("folder_shortcut")
        return _mime.type_description(self.mime, self.name)

    def props(self) -> dict:
        """Extended properties (lazy, may hit the disk)."""
        if self._props is None:
            from . import metadata
            self._props = metadata.read(self) if self.kind in ("file", "trash", "zipfile") else {}
        return self._props

    def invalidate(self):
        self.sk = {}
        self._props = None
        self._mime = None
        self._btime = None

    def __repr__(self):
        return f"<FileItem {self.kind} {self.path!r}>"
