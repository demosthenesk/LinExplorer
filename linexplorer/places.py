"""Well-known places: XDG user folders, drives, libraries, favorites, recent places."""
from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import threading
import time

from PySide6.QtCore import QObject, Signal

from . import util
from .settings import CONFIG_DIR, settings

HOME = os.path.expanduser("~")
UID = os.getuid()


def user_name() -> str:
    try:
        pw = pwd.getpwuid(UID)
        full = pw.pw_gecos.split(",")[0].strip()
        return full or pw.pw_name
    except KeyError:
        return os.environ.get("USER", "user")


def login_name() -> str:
    try:
        return pwd.getpwuid(UID).pw_name
    except KeyError:
        return os.environ.get("USER", "user")


# --------------------------------------------------------------------------- XDG user dirs
_XDG_KEYS = {
    "DESKTOP": ("Desktop", "desktop"), "DOWNLOAD": ("Downloads", "downloads"),
    "TEMPLATES": ("Templates", "templates"), "PUBLICSHARE": ("Public", "public"),
    "DOCUMENTS": ("Documents", "documents"), "MUSIC": ("Music", "music"),
    "PICTURES": ("Pictures", "pictures"), "VIDEOS": ("Videos", "videos"),
}
_xdg: dict[str, str] | None = None


def xdg_dirs() -> dict[str, str]:
    global _xdg
    if _xdg is not None:
        return _xdg
    d = {k.lower(): os.path.join(HOME, v[0]) for k, v in _XDG_KEYS.items()}
    cfg = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "user-dirs.dirs")
    try:
        with open(cfg, encoding="utf-8") as f:
            for ln in f:
                m = re.match(r'\s*XDG_(\w+)_DIR\s*=\s*"(.*)"', ln)
                if m and m.group(1) in _XDG_KEYS:
                    p = m.group(2).replace("$HOME", HOME)
                    d[m.group(1).lower()] = p
    except OSError:
        pass
    _xdg = d
    return d


def special_kind(path: str) -> str:
    """'documents', 'pictures', ... for XDG folders and home, else ''."""
    p = os.path.abspath(path)
    if p == HOME:
        return "home"
    for k, v in xdg_dirs().items():
        if v and os.path.abspath(v) == p and v != HOME:
            return k
    return ""


def folder_type_for(path: str) -> str:
    k = special_kind(path)
    if k in ("documents", "pictures", "music", "videos"):
        return k
    for lib in libraries().all():
        for loc in lib["locations"]:
            if util.is_subpath(path, loc) and lib.get("type") in ("documents", "pictures", "music", "videos"):
                return lib["type"]
    return "general"


# --------------------------------------------------------------------------- libraries
LIB_DIR = os.path.join(CONFIG_DIR, "libraries")


class Libraries(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        os.makedirs(LIB_DIR, exist_ok=True)
        self._libs: dict[str, dict] = {}
        self.load()

    def _defaults(self) -> list[dict]:
        x = xdg_dirs()
        pub = x.get("publicshare", "")
        out = []
        for key in ("documents", "music", "pictures", "videos"):
            locs = [x.get(key) or os.path.join(HOME, key.capitalize())]
            out.append({"name": key, "builtin": key, "type": key, "locations": locs, "save": locs[0],
                        "nav": True})
        if pub and os.path.isdir(pub):
            out[0]["locations"].append(pub)
        return out

    def load(self):
        self._libs.clear()
        files = sorted(f for f in os.listdir(LIB_DIR) if f.endswith(".json"))
        if not files:
            for lib in self._defaults():
                self._write(lib)
            files = sorted(f for f in os.listdir(LIB_DIR) if f.endswith(".json"))
        for f in files:
            try:
                with open(os.path.join(LIB_DIR, f), encoding="utf-8") as fh:
                    lib = json.load(fh)
                lib.setdefault("locations", [])
                lib.setdefault("type", "general")
                lib.setdefault("nav", True)
                lib.setdefault("save", lib["locations"][0] if lib["locations"] else "")
                self._libs[lib["name"]] = lib
            except (OSError, ValueError, KeyError):
                pass

    def _write(self, lib: dict):
        fn = re.sub(r"[^\w\-. ]", "_", lib["name"]) + ".json"
        with open(os.path.join(LIB_DIR, fn), "w", encoding="utf-8") as f:
            json.dump(lib, f, ensure_ascii=False, indent=1)

    def all(self) -> list[dict]:
        order = {"documents": 0, "music": 1, "pictures": 2, "videos": 3}
        return sorted(self._libs.values(), key=lambda l: (order.get(l.get("builtin", ""), 9), self.title(l).casefold()))

    def get(self, name: str) -> dict | None:
        return self._libs.get(name)

    def title(self, lib: dict) -> str:
        from .i18n import T
        b = lib.get("builtin")
        return T("lib_" + b) if b else lib["name"]

    def save(self, lib: dict):
        self._libs[lib["name"]] = lib
        self._write(lib)
        self.changed.emit()

    def create(self, name: str) -> dict:
        base = name
        n = 2
        while name in self._libs:
            name = f"{base} ({n})"
            n += 1
        lib = {"name": name, "type": "general", "locations": [], "save": "", "nav": True}
        self.save(lib)
        return lib

    def delete(self, name: str):
        lib = self._libs.pop(name, None)
        if lib:
            fn = re.sub(r"[^\w\-. ]", "_", name) + ".json"
            try:
                os.remove(os.path.join(LIB_DIR, fn))
            except OSError:
                pass
            self.changed.emit()

    def rename(self, old: str, new: str) -> bool:
        if new in self._libs or old not in self._libs:
            return False
        lib = self._libs[old]
        self.delete(old)
        lib["name"] = new
        lib.pop("builtin", None)
        self.save(lib)
        return True

    def include(self, name: str, folder: str):
        lib = self._libs.get(name)
        if lib is None:
            return
        if folder not in lib["locations"]:
            lib["locations"].append(folder)
            if not lib.get("save"):
                lib["save"] = folder
            self.save(lib)

    def restore_defaults(self):
        for lib in self._defaults():
            cur = self._libs.get(lib["name"])
            if cur:
                for loc in lib["locations"]:
                    if loc not in cur["locations"]:
                        cur["locations"].append(loc)
                cur["nav"] = True
                self._write(cur)
            else:
                self._libs[lib["name"]] = lib
                self._write(lib)
        self.changed.emit()


_libs: Libraries | None = None


def libraries() -> Libraries:
    global _libs
    if _libs is None:
        _libs = Libraries()
    return _libs


# --------------------------------------------------------------------------- favorites
class Favorites(QObject):
    changed = Signal()

    def default(self) -> list[dict]:
        x = xdg_dirs()
        return [{"path": x["desktop"], "name": ""}, {"path": x["download"], "name": ""},
                {"path": "shell:recent", "name": ""}]

    def items(self) -> list[dict]:
        v = settings().get_json("favorites", None)
        if v is None:
            v = self.default()
        return v

    def set_items(self, items):
        settings().set_json("favorites", items)
        self.changed.emit()

    def add(self, path: str, index: int = -1):
        items = [i for i in self.items() if i["path"] != path]
        if index < 0 or index > len(items):
            items.append({"path": path, "name": ""})
        else:
            items.insert(index, {"path": path, "name": ""})
        self.set_items(items)

    def remove(self, path: str):
        self.set_items([i for i in self.items() if i["path"] != path])

    def rename(self, path: str, name: str):
        items = self.items()
        for i in items:
            if i["path"] == path:
                i["name"] = name
        self.set_items(items)

    def restore(self):
        items = self.items()
        for d in self.default():
            if all(i["path"] != d["path"] for i in items):
                items.append(d)
        self.set_items(items)


_fav: Favorites | None = None


def favorites() -> Favorites:
    global _fav
    if _fav is None:
        _fav = Favorites()
    return _fav


# --------------------------------------------------------------------------- recent places
def add_recent_place(path: str):
    if not path.startswith("/"):
        return
    lst = settings().get_json("recent_places", [])
    lst = [p for p in lst if p != path]
    lst.insert(0, path)
    settings().q.setValue("recent_places", json.dumps(lst[:40]))


def recent_places() -> list[str]:
    return [p for p in settings().get_json("recent_places", []) if os.path.isdir(p)]


# --------------------------------------------------------------------------- drives
HIDE_MOUNT_PREFIXES = ("/snap/", "/boot", "/var/snap", "/var/lib/docker", "/run/", "/sys", "/proc", "/dev")


class Drive(dict):
    """Keys: dev, label, fstype, size, free, used, mount, removable, kind, category, model, uuid, parent."""


class Drives(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._drives: list[Drive] = []
        self._lock = threading.Lock()
        self._last = 0.0
        self.refresh(sync=True)

    def list(self) -> list[Drive]:
        with self._lock:
            return list(self._drives)

    def refresh(self, sync: bool = False):
        if sync:
            self._do_refresh()
        else:
            util.in_thread(self._do_refresh)

    def _do_refresh(self):
        drives = scan_drives()
        with self._lock:
            old = [(d.get("dev"), d.get("mount"), d.get("free")) for d in self._drives]
            self._drives = drives
            self._last = time.time()
        if old != [(d.get("dev"), d.get("mount"), d.get("free")) for d in drives]:
            self.changed.emit()

    def for_path(self, path: str) -> Drive | None:
        best = None
        for d in self.list():
            m = d.get("mount")
            if m and util.is_subpath(path, m):
                if best is None or len(m) > len(best["mount"]):
                    best = d
        return best

    def by_mount(self, mount: str) -> Drive | None:
        for d in self.list():
            if d.get("mount") == mount:
                return d
        return None


def _mountinfo() -> list[tuple[str, str, str]]:
    out = []
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
            for ln in f:
                a, _, b = ln.partition(" - ")
                fa = a.split()
                fb = b.split()
                if len(fa) >= 5 and len(fb) >= 2:
                    mp = fa[4].encode().decode("unicode_escape") if "\\" in fa[4] else fa[4]
                    out.append((fb[1], mp, fb[0]))
    except OSError:
        pass
    return out


def scan_drives() -> list[Drive]:
    drives: list[Drive] = []
    seen_mounts = set()
    rc, out, _ = util.run(["lsblk", "-J", "-b", "-o",
                           "NAME,PATH,LABEL,FSTYPE,SIZE,MOUNTPOINTS,RM,HOTPLUG,TYPE,MODEL,TRAN,UUID,RO,PARTLABEL,PKNAME,ROTA"],
                          timeout=5)
    devs = []
    if rc == 0:
        try:
            data = json.loads(out)

            def walk(nodes, parent=None):
                for n in nodes:
                    n["_parent"] = parent
                    devs.append(n)
                    walk(n.get("children") or [], n)
            walk(data.get("blockdevices", []))
        except ValueError:
            pass
    for n in devs:
        typ = n.get("type")
        if typ in ("loop", "zram", "ram"):
            continue
        fst = n.get("fstype") or ""
        if typ != "rom" and fst in ("swap", "crypto_LUKS", "LVM2_member", "linux_raid_member", ""):
            continue
        mps = [m for m in (n.get("mountpoints") or []) if m and m != "[SWAP]"]
        mps.sort(key=len)
        mount = mps[0] if mps else ""
        if mount and mount != "/" and mount.startswith(HIDE_MOUNT_PREFIXES) \
                and not mount.startswith(("/media/", "/run/media/")):
            continue
        root = n
        while root.get("_parent") is not None:
            root = root["_parent"]
        removable = bool(n.get("rm") or n.get("hotplug") or root.get("rm") or root.get("hotplug")) \
            or (root.get("tran") in ("usb", "mmc"))
        if typ == "rom":
            kind = "optical"
        elif removable:
            kind = "usb" if root.get("tran") == "usb" else "flash" if root.get("tran") == "mmc" else "removable"
        elif mount == "/":
            kind = "root"
        else:
            kind = "hdd" if root.get("rota") else "ssd"
        if fst == "vfat" and not removable and n.get("partlabel", "") in ("EFI System Partition", "EFI system partition"):
            continue
        if fst == "vfat" and not removable and (n.get("size") or 0) < 1100 * 1024 * 1024 and not mount:
            continue          # EFI/system reserved style partitions
        d = Drive(dev=n.get("path"), label=n.get("label") or n.get("partlabel") or "", fstype=fst,
                  size=int(n.get("size") or 0), mount=mount, removable=removable, kind=kind,
                  category="removable" if removable or typ == "rom" else "hdd",
                  model=(root.get("model") or "").strip(), uuid=n.get("uuid") or "",
                  parent=root.get("path"), ro=bool(n.get("ro")))
        if mount:
            try:
                du = shutil.disk_usage(mount)
                d["size"], d["free"], d["used"] = du.total, du.free, du.used
            except OSError:
                pass
            seen_mounts.add(mount)
        drives.append(d)
    # network / gvfs mounts and other fuse filesystems the user mounted
    gv = f"/run/user/{UID}/gvfs"
    if os.path.isdir(gv):
        try:
            for name in sorted(os.listdir(gv)):
                p = os.path.join(gv, name)
                drives.append(Drive(dev=name, label=gvfs_label(name), fstype="gvfs", size=0, mount=p,
                                    removable=False, kind="network", category="network", model="", uuid="",
                                    parent="", ro=False))
        except OSError:
            pass
    for fstype, mp, src in _mountinfo():
        if mp in seen_mounts:
            continue
        if fstype in ("nfs", "nfs4", "cifs", "smb3", "fuse.sshfs", "fuse.rclone", "davfs", "fuse.davfs2"):
            d = Drive(dev=src, label=os.path.basename(mp) or src, fstype=fstype, size=0, mount=mp, removable=False,
                      kind="network", category="network", model="", uuid="", parent="", ro=False)
            try:
                du = shutil.disk_usage(mp)
                d["size"], d["free"], d["used"] = du.total, du.free, du.used
            except OSError:
                pass
            drives.append(d)
            seen_mounts.add(mp)
    if not any(d.get("mount") == "/" for d in drives):
        try:
            du = shutil.disk_usage("/")
            drives.insert(0, Drive(dev="", label="", fstype="", size=du.total, free=du.free, used=du.used, mount="/",
                                   removable=False, kind="root", category="hdd", model="", uuid="", parent="", ro=False))
        except OSError:
            pass
    return drives


def gvfs_label(name: str) -> str:
    """'smb-share:server=nas,share=media' -> 'media (\\\\nas)'."""
    kind, _, rest = name.partition(":")
    kv = dict(p.split("=", 1) for p in rest.split(",") if "=" in p)
    if kind == "smb-share":
        return f"{kv.get('share', '')} (\\\\{kv.get('server', '')})"
    if kind in ("sftp", "ftp", "dav", "davs", "afp-volume", "nfs"):
        host = kv.get("host", kv.get("server", ""))
        user = kv.get("user")
        return f"{kind}://{user + '@' if user else ''}{host}"
    if kind == "mtp":
        return kv.get("host", "MTP").replace("_", " ")
    if kind == "gphoto2":
        return kv.get("host", "Camera").replace("_", " ")
    return name


def drive_title(d: Drive) -> str:
    from .i18n import T
    m = d.get("mount") or ""
    label = d.get("label") or ""
    kind = d.get("kind")
    if kind == "network":
        return label or m
    if kind == "optical":
        base = label or T("dvd_drive")
    elif m == "/":
        base = label or T("local_disk")
    elif d.get("removable"):
        base = label or (T("removable_disk") if not d.get("size") else f"{util.fmt_size_short(d['size'])} {T('volume')}")
    else:
        base = label or (f"{util.fmt_size_short(d['size'])} {T('volume')}" if d.get("size") else T("local_disk"))
    if settings().get("show_drive_letters"):
        tag = m or (d.get("dev") or "")
        return f"{base} ({tag})" if tag else base
    return base


_drives: Drives | None = None


def drives() -> Drives:
    global _drives
    if _drives is None:
        _drives = Drives()
    return _drives
