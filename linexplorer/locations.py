"""Locations (what the address bar shows) and the providers that list them.

Location strings:
    /abs/path                    a folder on disk
    shell:computer               Computer
    shell:network                Network
    shell:trash                  Recycle Bin
    shell:libraries              Libraries
    shell:library:<name>         one library
    shell:recent                 Recent Places
    net:<uri>                    a GIO network location (smb://, sftp://, ...)
    zip:<file.zip>!<inner/path>  inside a compressed (zipped) folder
    search:?scope=<loc>&q=<text> search results
"""
from __future__ import annotations

import os
import urllib.parse

from PySide6.QtCore import QObject, Signal

from . import icons, places, trash, util, ziputil
from .i18n import T
from .items import FileItem
from .settings import settings

HOME = places.HOME


class UseGio(Exception):
    """The gvfs FUSE path failed but GIO itself can list the location: browse it as net:URI instead."""

    def __init__(self, uri: str, msg: str = ""):
        super().__init__(msg or uri)
        self.uri = uri


class NetError(OSError):
    """A network location failed; the message is GIO's own explanation."""


class NeedsMount(Exception):
    def __init__(self, uri: str, msg: str = ""):
        super().__init__(msg or uri)
        self.uri = uri


# =========================================================================== naming helpers
def kind(loc: str) -> str:
    if loc.startswith("/"):
        return "fs"
    if loc.startswith("shell:library:"):
        return "library"
    if loc.startswith("shell:network:"):
        return "netproto"
    if loc.startswith("shell:"):
        return loc[6:]
    if loc.startswith("net:"):
        return "net"
    if loc.startswith("zip:"):
        return "zip"
    if loc.startswith("search:"):
        return "search"
    return "fs"


def search_location(scope: str, query: str, content: bool = False) -> str:
    q = {"scope": scope, "q": query}
    if content:
        q["content"] = "1"
    return "search:?" + urllib.parse.urlencode(q)


def parse_search(loc: str) -> dict:
    q = urllib.parse.parse_qs(loc.split("?", 1)[1] if "?" in loc else "", keep_blank_values=True)
    return {"scope": q.get("scope", [HOME])[0], "q": q.get("q", [""])[0], "content": q.get("content", ["0"])[0] == "1"}


def library_name(loc: str) -> str:
    return loc[len("shell:library:"):]


def fs_display_name(path: str) -> str:
    path = os.path.abspath(path)
    if path == HOME:
        return places.login_name()
    d = places.drives().by_mount(path)
    if d is not None:
        return places.drive_title(d)
    if path == "/":
        return T("local_disk") + " (/)"
    return os.path.basename(path) or path


def display_name(loc: str) -> str:
    k = kind(loc)
    if k == "fs":
        return fs_display_name(loc)
    if k == "computer":
        return T("computer")
    if k == "network":
        return T("network")
    if k == "trash":
        return T("recycle_bin")
    if k == "libraries":
        return T("libraries")
    if k == "library":
        lib = places.libraries().get(library_name(loc))
        return places.libraries().title(lib) if lib else library_name(loc)
    if k == "recent":
        return T("recent_places")
    if k == "netproto":
        from . import netloc
        return netloc.proto_title(loc.rsplit(":", 1)[1])
    if k == "net":
        uri = loc[4:]
        p = urllib.parse.urlparse(uri)
        tail = p.path.strip("/").split("/")[-1] if p.path.strip("/") else ""
        return urllib.parse.unquote(tail) or p.hostname or uri
    if k == "zip":
        zp, inner = ziputil.split(loc)
        return inner.split("/")[-1] if inner else os.path.basename(zp)
    if k == "search":
        s = parse_search(loc)
        return T("search_results_in", display_name(s["scope"]))
    return loc


def icon(loc: str):
    k = kind(loc)
    if k == "fs":
        sk = places.special_kind(loc)
        if sk:
            return icons.folder({"download": "downloads", "publicshare": "public"}.get(sk, sk))
        d = places.drives().by_mount(loc)
        if d is not None:
            return icons.drive(d.get("kind"))
        return icons.folder()
    if k == "computer":
        return icons.computer()
    if k == "network":
        return icons.network()
    if k == "trash":
        return icons.trash(not trash.is_empty())
    if k == "libraries":
        return icons.libraries()
    if k == "library":
        lib = places.libraries().get(library_name(loc))
        return icons.library(lib.get("type", "") if lib else "")
    if k == "recent":
        return icons.folder("recent")
    if k == "netproto":
        return icons.net_proto(loc.rsplit(":", 1)[1])
    if k == "net":
        return icons.network_server()
    if k == "zip":
        return icons.theme("package-x-generic", "application-zip", "folder", fallback="folder")
    if k == "search":
        return icons.folder("search")
    return icons.folder()


def parent(loc: str) -> str | None:
    k = kind(loc)
    if k == "fs":
        p = os.path.abspath(loc)
        if p == "/":
            return "shell:computer"
        d = places.drives().by_mount(p)
        if d is not None and p != HOME:
            return "shell:computer"
        return os.path.dirname(p)
    if k == "library":
        return "shell:libraries"
    if k == "netproto":
        return "shell:network"
    if k == "net":
        uri = loc[4:]
        pr = urllib.parse.urlparse(uri)
        path = pr.path.rstrip("/")
        if not path:
            from . import netloc
            return "shell:network:" + _proto_bucket(netloc.proto_of_uri(uri))
        return "net:" + urllib.parse.urlunparse(pr._replace(path=os.path.dirname(path) + "/"))
    if k == "zip":
        zp, inner = ziputil.split(loc)
        if not inner:
            return os.path.dirname(zp)
        return ziputil.make(zp, os.path.dirname(inner))
    if k == "search":
        return parse_search(loc)["scope"]
    return None


def crumbs(loc: str) -> list[tuple[str, str]]:
    k = kind(loc)
    if k == "fs":
        p = os.path.abspath(loc)
        if util.is_subpath(p, HOME):
            out = [(HOME, places.login_name())]
            rel = os.path.relpath(p, HOME)
            cur = HOME
            if rel != ".":
                for part in rel.split("/"):
                    cur = os.path.join(cur, part)
                    out.append((cur, part))
            return out
        out = [("shell:computer", T("computer"))]
        d = places.drives().for_path(p) or places.drives().by_mount("/")
        mount = d.get("mount") if d else "/"
        out.append((mount, places.drive_title(d) if d else T("local_disk")))
        rel = os.path.relpath(p, mount)
        cur = mount
        if rel != ".":
            for part in rel.split("/"):
                cur = os.path.join(cur, part)
                out.append((cur, part))
        return out
    if k == "library":
        return [("shell:libraries", T("libraries")), (loc, display_name(loc))]
    if k == "netproto":
        return [("shell:network", T("network")), (loc, display_name(loc))]
    if k == "net":
        uri = loc[4:]
        pr = urllib.parse.urlparse(uri)
        from . import netloc
        pb = "shell:network:" + _proto_bucket(netloc.proto_of_uri(uri))
        out = [("shell:network", T("network")), (pb, display_name(pb))]
        root = urllib.parse.urlunparse(pr._replace(path="/"))
        out.append(("net:" + root, pr.hostname or uri))
        cur = "/"
        for part in [x for x in pr.path.split("/") if x]:
            cur += part + "/"
            out.append(("net:" + urllib.parse.urlunparse(pr._replace(path=cur)), urllib.parse.unquote(part)))
        return out
    if k == "zip":
        zp, inner = ziputil.split(loc)
        out = crumbs(os.path.dirname(zp))
        out.append((ziputil.make(zp, ""), os.path.basename(zp)))
        cur = ""
        for part in [x for x in inner.split("/") if x]:
            cur = f"{cur}/{part}" if cur else part
            out.append((ziputil.make(zp, cur), part))
        return out
    if k == "search":
        s = parse_search(loc)
        return crumbs(s["scope"]) + [(loc, T("search_results_in", display_name(s["scope"])))]
    return [(loc, display_name(loc))]


def _proto_bucket(p: str) -> str:
    return p if p in ("smb", "ftp", "sftp") else "other"


def normalize(text: str, base: str | None = None) -> str | None:
    """Turn what the user typed in the address bar into a location."""
    t = text.strip()
    if not t:
        return None
    low = t.lower()
    names = {
        T("computer").lower(): "shell:computer", "computer": "shell:computer",
        T("network").lower(): "shell:network", "network": "shell:network",
        T("recycle_bin").lower(): "shell:trash", "recycle bin": "shell:trash", "trash": "shell:trash",
        "trash:///": "shell:trash", T("libraries").lower(): "shell:libraries", "libraries": "shell:libraries",
        "network:///": "shell:network", "computer:///": "shell:computer",
        T("recent_places").lower(): "shell:recent",
    }
    if low in names:
        return names[low]
    if low.startswith("shell:"):
        return t
    if t.startswith("\\\\"):
        parts = [p for p in t[2:].split("\\") if p]
        return "net:smb://" + "/".join(urllib.parse.quote(p) for p in parts) + "/"
    if low.startswith("file://"):
        return urllib.parse.unquote(urllib.parse.urlparse(t).path) or "/"
    for scheme in ("smb://", "sftp://", "ftp://", "ftps://", "dav://", "davs://", "afp://", "nfs://", "ssh://",
                   "mtp://", "gphoto2://", "network://"):
        if low.startswith(scheme):
            if scheme == "ssh://":
                t = "sftp://" + t[6:]
            return "net:" + t
    for lib in places.libraries().all():
        if low == places.libraries().title(lib).lower():
            return "shell:library:" + lib["name"]
    t = os.path.expanduser(os.path.expandvars(t))
    if not t.startswith("/"):
        if base and base.startswith("/"):
            t = os.path.join(base, t)
        else:
            t = os.path.join(HOME, t)
    t = os.path.normpath(t)
    # path inside a zip file?
    cur = t
    while cur and cur != "/":
        if os.path.isfile(cur):
            if ziputil.is_zip_path(cur):
                return ziputil.make(cur, os.path.relpath(t, cur) if t != cur else "")
            return None
        cur = os.path.dirname(cur)
    return t


def is_container_file(path: str) -> bool:
    return ziputil.is_zip_path(path)


# =========================================================================== providers
class Provider(QObject):
    """Lists the items of one location. load() runs in a worker thread."""
    refresh_needed = Signal()
    folder_type = "general"
    writable = False
    virtual = True

    def __init__(self, location: str):
        super().__init__()
        self.location = location

    def title(self) -> str:
        return display_name(self.location)

    def target_dir(self) -> str | None:
        return None

    def watch_paths(self) -> list[str]:
        return []

    def load(self, emit, cancelled):
        raise NotImplementedError

    def empty_text(self) -> str:
        return T("folder_empty")


def _scan(folder: str, emit, cancelled, extra: dict | None = None):
    show_hidden = settings().get("show_hidden")
    dot_hidden = util.read_dot_hidden(folder)
    batch = []
    try:
        it = os.scandir(folder)
    except OSError as e:
        raise e
    with it:
        for entry in it:
            if cancelled():
                return
            fi = FileItem.from_path(entry.path, entry.name, entry)
            if entry.name in dot_hidden:
                fi.hidden = True
            if fi.hidden and not show_hidden:
                continue
            if extra:
                fi.extra.update(extra)
            batch.append(fi)
            if len(batch) >= 400:
                emit(batch)
                batch = []
    if batch:
        emit(batch)


class FsProvider(Provider):
    virtual = False

    def __init__(self, location: str):
        super().__init__(os.path.abspath(location))
        self.folder_type = places.folder_type_for(self.location)
        self.writable = os.access(self.location, os.W_OK)

    def target_dir(self):
        return self.location

    def watch_paths(self):
        return [self.location]

    def load(self, emit, cancelled):
        try:
            _scan(self.location, emit, cancelled)
        except OSError as e:
            if not isinstance(e, (PermissionError, FileNotFoundError)) or "/gvfs/" in self.location:
                _diagnose_gvfs(self.location, e)
            raise


def _diagnose_gvfs(path: str, exc: OSError):
    """A folder under gvfs's FUSE mount could not be read (often just 'Input/output error'): ask GIO
    directly. If GIO can list it, browse through GIO; otherwise report GIO's real error message."""
    from . import netloc
    uri = netloc.gvfs_path_to_uri(path)
    if not uri:
        return
    rc, _ents, err = netloc.gio_list(uri)
    from . import ftpclient
    if rc == 0 or ftpclient.is_ftp_uri(uri):
        raise UseGio(uri, str(exc))           # FTP: the built-in client may still work
    raise NetError(exc.errno or 5, (err or exc.strerror or str(exc)) + f"\n\n({uri})")


class ComputerProvider(Provider):
    folder_type = "computer"

    def __init__(self, location="shell:computer"):
        super().__init__(location)
        places.drives().changed.connect(self.refresh_needed)

    def load(self, emit, cancelled):
        places.drives().refresh(sync=True)
        out = []
        hide_empty = settings().get("hide_empty_drives")
        for d in places.drives().list():
            if d.get("kind") == "network" and d.get("fstype") == "gvfs":
                pass
            if hide_empty and d.get("removable") and not d.get("size"):
                continue
            path = d.get("mount") or ("dev:" + (d.get("dev") or ""))
            fi = FileItem(places.drive_title(d), path, "drive", True)
            fi.extra["drive"] = d
            fi.size = d.get("size") or None
            fi.extra["free"] = d.get("free")
            out.append(fi)
        emit(out)

    def empty_text(self):
        return ""


class TrashProvider(Provider):
    folder_type = "trash"

    def watch_paths(self):
        return [os.path.join(td, "files") for td, _ in trash.trash_dirs()]

    def load(self, emit, cancelled):
        out = []
        for e in trash.list_entries():
            fi = FileItem.from_path(e.files_path, e.display_name)
            fi.kind = "trash"
            fi.hidden = False
            fi.extra["trash"] = e
            fi.extra["orig_dir"] = os.path.dirname(e.orig_path)
            fi.extra["deleted"] = e.deleted
            if fi.is_dir:
                fi.size = None
            out.append(fi)
            if len(out) >= 300:
                emit(out)
                out = []
        emit(out)

    def empty_text(self):
        return T("folder_empty")


class LibrariesProvider(Provider):
    folder_type = "libraries"

    def __init__(self, location="shell:libraries"):
        super().__init__(location)
        places.libraries().changed.connect(self.refresh_needed)

    def load(self, emit, cancelled):
        out = []
        L = places.libraries()
        for lib in L.all():
            fi = FileItem(L.title(lib), "shell:library:" + lib["name"], "library", True)
            fi.extra["library"] = lib
            out.append(fi)
        emit(out)


class LibraryProvider(Provider):
    def __init__(self, location: str):
        super().__init__(location)
        self.lib = places.libraries().get(library_name(location)) or {"locations": [], "type": "general",
                                                                         "name": library_name(location)}
        self.folder_type = self.lib.get("type", "general")
        tgt = self.target_dir()
        self.writable = bool(tgt) and os.access(tgt, os.W_OK)
        places.libraries().changed.connect(self.refresh_needed)

    def target_dir(self):
        s = self.lib.get("save") or (self.lib["locations"][0] if self.lib["locations"] else None)
        return s if s and os.path.isdir(s) else None

    def watch_paths(self):
        return [p for p in self.lib["locations"] if os.path.isdir(p)]

    def load(self, emit, cancelled):
        for loc in self.lib["locations"]:
            if os.path.isdir(loc):
                try:
                    _scan(loc, emit, cancelled, {"in_folder": loc})
                except OSError:
                    pass

    def empty_text(self):
        if not self.lib["locations"]:
            return T("library_empty")
        return T("folder_empty")


class RecentProvider(Provider):
    def load(self, emit, cancelled):
        out = []
        for p in places.recent_places():
            fi = FileItem.from_path(p)
            fi.name = fs_display_name(p)
            out.append(fi)
        emit(out)


class ZipProvider(Provider):
    folder_type = "zip"

    def __init__(self, location: str):
        super().__init__(location)
        self.zp, self.inner = ziputil.split(location)
        self.writable = ziputil.is_writable(self.zp)

    def watch_paths(self):
        return [os.path.dirname(self.zp)]

    def load(self, emit, cancelled):
        out = []
        for e in ziputil.list_dir(self.zp, self.inner):
            loc = ziputil.make(self.zp, e.path)
            fi = FileItem(e.name, loc, "zipdir" if e.is_dir else "zipfile", e.is_dir)
            fi.size = None if e.is_dir else e.size
            fi.mtime = e.mtime
            fi.extra["zip"] = self.zp
            fi.extra["member"] = e.path
            fi.extra["csize"] = e.csize
            if e.size and not e.is_dir:
                fi.extra["ratio"] = max(0, round(100 - 100 * e.csize / e.size))
            out.append(fi)
        emit(out)


class NetworkProvider(Provider):
    """Network: one folder per kind of server (SAMBA, FTP, SFTP, and Other when used)."""
    folder_type = "network"

    def __init__(self, location="shell:network"):
        super().__init__(location)
        places.drives().changed.connect(self.refresh_needed)

    def load(self, emit, cancelled):
        from . import netloc
        conns = netloc.connections()
        gmounts = netloc.gio_mount_list()
        sv = netloc.saved()
        out = []
        protos = list(netloc.PROTOS)
        if any(_proto_bucket(c["proto"]) == "other" for c in conns) or \
                any(_proto_bucket(netloc.proto_of_uri(l["uri"])) == "other" for l in sv):
            protos.append("other")
        for p in protos:
            fi = FileItem(netloc.proto_title(p), "shell:network:" + p, "special", True)
            keys = {netloc.normalize_uri(c["uri"]) or c["mount"] for c in conns if _proto_bucket(c["proto"]) == p}
            keys |= {netloc.normalize_uri(m["uri"]) for m in gmounts if _proto_bucket(m["proto"]) == p}
            n_conn = len(keys)
            n_saved = sum(1 for l in sv if _proto_bucket(netloc.proto_of_uri(l["uri"])) == p)
            fi.extra["type"] = T("net_proto_desc_" + p)
            fi.extra["proto"] = p
            fi.extra["counts"] = (n_conn, n_saved)
            out.append(fi)
        emit(out)

    def empty_text(self):
        return T("network_empty")


class NetProtoProvider(Provider):
    """One protocol: connected locations, saved locations and servers found on the network."""
    folder_type = "netproto"

    def __init__(self, location: str):
        super().__init__(location)
        self.proto = location.rsplit(":", 1)[1]
        places.drives().changed.connect(self.refresh_needed)

    def load(self, emit, cancelled):
        from . import netloc
        seen = set()
        out = []
        for c in netloc.connections():
            if _proto_bucket(c["proto"]) != self.proto:
                continue
            fi = FileItem(c["label"], c["mount"], "dir", True)
            fi.extra.update(netcat="connected", uri=c["uri"], netdrive=True,
                            type=T("net_connected_type", netloc.proto_title(c["proto"])))
            out.append(fi)
            if c["uri"]:
                seen.add(netloc.normalize_uri(c["uri"]))
        saved_keys = {netloc.normalize_uri(l["uri"]): l for l in netloc.saved()}
        for m in netloc.gio_mount_list():
            # mounts gvfs still holds but without a usable folder (e.g. a failed or dropped connection)
            key = netloc.normalize_uri(m["uri"])
            if _proto_bucket(m["proto"]) != self.proto or key in seen:
                continue
            sv = saved_keys.get(key)
            fi = FileItem((sv or {}).get("name") or m["name"] or m["uri"], "net:" + m["uri"], "net", True)
            fi.extra.update(netcat="connected", uri=m["uri"], saved=bool(sv), stale=True,
                            type=T("net_connected_type", netloc.proto_title(m["proto"])))
            out.append(fi)
            seen.add(key)
        for l in netloc.saved():
            if _proto_bucket(netloc.proto_of_uri(l["uri"])) != self.proto:
                continue
            if netloc.normalize_uri(l["uri"]) in seen:
                continue
            fi = FileItem(l["name"] or l["uri"], "net:" + l["uri"], "net", True)
            fi.extra.update(netcat="saved", uri=l["uri"], saved=True, type=l["uri"])
            out.append(fi)
            seen.add(netloc.normalize_uri(l["uri"]))
        emit(out)
        if self.proto == "other" or cancelled():
            return
        found = []
        for d in netloc.discover():
            if d["proto"] != self.proto or netloc.normalize_uri(d["uri"]) in seen:
                continue
            fi = FileItem(d["name"], "net:" + d["uri"], "net", True)
            fi.extra.update(netcat="discovered", uri=d["uri"],
                            type=T("workgroup") if d.get("workgroup") else d["uri"])
            found.append(fi)
        emit(found)

    def empty_text(self):
        return T("net_proto_empty")


class NetBrowseProvider(Provider):
    """net:URI - servers, shares and (when the FUSE path does not work) folders listed through GIO."""
    folder_type = "network"

    def __init__(self, location: str):
        super().__init__(location)
        self.uri = location[4:]

    def load(self, emit, cancelled):
        from . import netloc
        ents = netloc.remote_list(self.uri)
        out = []
        top = self.uri.split("://", 1)[-1].strip("/").count("/") == 0 and self.uri.startswith(("smb:", "network:"))
        show_hidden = settings().get("show_hidden")
        for e in ents:
            if e["hidden"] and not show_hidden:
                continue
            target = e["target"] or e["uri"]
            is_dir = e["type"] in ("directory", "mountable", "shortcut")
            if is_dir:
                fi = FileItem(e["name"], "net:" + (target if target.endswith("/") else target + "/"), "net", True)
                fi.extra["type"] = T("network_share") if top else T("file_folder")
                fi.mtime = e["mtime"]
            else:
                fi = FileItem(e["name"], "net:" + target, "netfile", False)
                fi.size = e["size"]
                fi.mtime = e["mtime"]
                fi.extra["mime"] = e["mime"]
            fi.extra["uri"] = target
            fi.hidden = e["hidden"]
            out.append(fi)
        emit(out)


def make_provider(loc: str) -> Provider:
    k = kind(loc)
    if k == "fs":
        return FsProvider(loc)
    if k == "computer":
        return ComputerProvider(loc)
    if k == "trash":
        return TrashProvider(loc)
    if k == "libraries":
        return LibrariesProvider(loc)
    if k == "library":
        return LibraryProvider(loc)
    if k == "recent":
        return RecentProvider(loc)
    if k == "zip":
        return ZipProvider(loc)
    if k == "network":
        return NetworkProvider(loc)
    if k == "netproto":
        return NetProtoProvider(loc)
    if k == "net":
        return NetBrowseProvider(loc)
    if k == "search":
        from .search import SearchProvider
        return SearchProvider(loc)
    return FsProvider(HOME)


def exists(loc: str) -> bool:
    k = kind(loc)
    if k == "fs":
        return os.path.isdir(loc)
    if k == "zip":
        zp, inner = ziputil.split(loc)
        return os.path.isfile(zp)
    if k == "library":
        return places.libraries().get(library_name(loc)) is not None
    return True


def gio_local_path(uri: str) -> str | None:
    rc, out, _ = util.run(["gio", "info", uri], timeout=10)
    if rc == 0:
        for ln in out.splitlines():
            if ln.startswith("local path:"):
                return ln.split(":", 1)[1].strip()
    return None
