"""Network: SAMBA, FTP and SFTP locations - saved locations, gvfs connections, discovery and
an interactive 'gio mount' driver (user name, domain, password, host-key questions)."""
from __future__ import annotations

import os
import re
import select
import subprocess
import time
import urllib.parse

from . import places, util
from .settings import settings

PROTOS = ["smb", "ftp", "sftp"]
DEFAULT_PORT = {"smb": 445, "ftp": 21, "ftps": 21, "ftpis": 990, "sftp": 22, "dav": 80, "davs": 443}
# gvfs schemes: ftp = plain, ftps = explicit FTP over TLS (AUTH TLS on port 21), ftpis = implicit TLS (port 990)
FTP_SCHEMES = ("ftp", "ftps", "ftpis")
AVAHI_TYPES = {"_smb._tcp": "smb", "_sftp-ssh._tcp": "sftp", "_ssh._tcp": "sftp", "_ftp._tcp": "ftp",
               "_webdav._tcp": "dav", "_webdavs._tcp": "davs"}


def proto_of_uri(uri: str) -> str:
    s = uri.split(":", 1)[0].lower()
    return {"smb": "smb", "cifs": "smb", "ftp": "ftp", "ftps": "ftp", "ftpis": "ftp", "sftp": "sftp", "ssh": "sftp",
            "dav": "dav", "davs": "dav", "webdav": "dav", "webdavs": "dav"}.get(s, "other")


def proto_title(p: str) -> str:
    from .i18n import T
    return T("net_proto_" + p) if p in ("smb", "ftp", "sftp", "dav", "other") else p.upper()


def build_uri(proto: str, host: str, port: int | None = None, path: str = "", user: str = "") -> str:
    scheme = {"smb": "smb", "ftp": "ftp", "ftps": "ftps", "ftpis": "ftpis", "sftp": "sftp", "dav": "dav",
              "davs": "davs"}.get(proto, proto)
    host = host.strip().strip("/")
    if host.startswith("\\\\"):
        host = host[2:]
    netloc = (urllib.parse.quote(user, safe="") + "@" if user else "") + host
    if port and port != DEFAULT_PORT.get(scheme):
        netloc += f":{port}"
    path = path.strip().replace("\\", "/").strip("/")
    p = "/" + "/".join(urllib.parse.quote(x) for x in path.split("/") if x)
    return f"{scheme}://{netloc}{p.rstrip('/')}/"


def normalize_uri(uri: str) -> str:
    pr = urllib.parse.urlparse(uri)
    host = (pr.hostname or "").lower()
    if host.endswith(".local"):
        host = host[:-6]
    port = pr.port
    scheme = pr.scheme.lower()
    if port == DEFAULT_PORT.get(scheme):
        port = None
    path = urllib.parse.unquote(pr.path).strip("/")
    if scheme in ("smb", "cifs"):
        path = path.lower()          # SMB share and path names are case-insensitive
    return f"{scheme}://{host}{':' + str(port) if port else ''}/{path}".rstrip("/")


# --------------------------------------------------------------------------- saved locations
def saved() -> list[dict]:
    return settings().get_json("net_locations", [])


def save_location(name: str, uri: str):
    lst = [l for l in saved() if normalize_uri(l["uri"]) != normalize_uri(uri)]
    lst.append({"name": name, "uri": uri})
    settings().set_json("net_locations", lst)


def remove_location(uri: str):
    settings().set_json("net_locations", [l for l in saved() if normalize_uri(l["uri"]) != normalize_uri(uri)])


def rename_location(uri: str, name: str):
    lst = saved()
    for l in lst:
        if normalize_uri(l["uri"]) == normalize_uri(uri):
            l["name"] = name
    settings().set_json("net_locations", lst)


# --------------------------------------------------------------------------- connections (gvfs)
def gvfs_uri(name: str) -> str | None:
    """'smb-share:server=nas,share=media,user=bob' -> 'smb://bob@nas/media/'."""
    kind, _, rest = name.partition(":")
    kv = {}
    for part in rest.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            kv[k] = urllib.parse.unquote(v)
    user = kv.get("user", "")
    if kind == "smb-share":
        return build_uri("smb", kv.get("server", ""), int(kv["port"]) if kv.get("port", "").isdigit() else None,
                         kv.get("share", ""), user)
    if kind == "smb-server":
        return build_uri("smb", kv.get("server", ""))
    if kind in ("sftp", "ftp", "ftps", "ftpis", "dav", "davs"):
        return build_uri(kind, kv.get("host", ""), int(kv["port"]) if kv.get("port", "").isdigit() else None,
                         kv.get("prefix", ""), user)
    return None


def scheme_of(uri: str) -> str:
    return uri.split(":", 1)[0].lower()


def with_scheme(uri: str, scheme: str) -> str:
    """ftp://u@h/x -> ftps://u@h/x (a port equal to the old default is kept only if it differs from the new one)."""
    pr = urllib.parse.urlparse(uri)
    netloc = pr.netloc
    if pr.port and pr.port == DEFAULT_PORT.get(pr.scheme) and pr.port != DEFAULT_PORT.get(scheme):
        netloc = netloc.rsplit(":", 1)[0]
    return urllib.parse.urlunparse((scheme, netloc, pr.path or "/", pr.params, pr.query, pr.fragment))


def replace_saved_uri(old: str, new: str):
    lst = saved()
    changed = False
    for l in lst:
        if normalize_uri(l["uri"]) == normalize_uri(old):
            l["uri"] = new
            changed = True
    if changed:
        settings().set_json("net_locations", lst)


def gvfs_root() -> str:
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "gvfs")


def gvfs_path_to_uri(path: str) -> str | None:
    """'/run/user/1000/gvfs/ftp:host=nas,user=bob/docs' -> 'ftp://bob@nas/docs'."""
    path = os.path.abspath(path)
    for root in {gvfs_root(), f"/run/user/{os.getuid()}/gvfs"}:
        if path == root or not path.startswith(root + "/"):
            continue
        rest = path[len(root) + 1:]
        name, _, sub = rest.partition("/")
        base = gvfs_uri(name)
        if not base:
            return None
        base = base.rstrip("/")
        return base + ("/" + "/".join(urllib.parse.quote(x) for x in sub.split("/") if x) if sub else "/")
    return None


_ATTR_SPLIT = re.compile(r" (?=[\w-]+::[\w:-]+=)")


def gio_list(uri: str, timeout: float = 30) -> tuple[int, list[dict], str]:
    """List a GIO location without FUSE: (rc, [{uri, name, size, type, mtime, mime, target, hidden}], error)."""
    rc, out, err = util.run(["gio", "list", "-l", "-u", "-h", "-a",
                             "standard::display-name,standard::target-uri,standard::size,time::modified,"
                             "standard::content-type,standard::is-hidden", uri], timeout=timeout)
    res = []
    if rc == 0:
        for ln in out.splitlines():
            parts = ln.split("\t", 3)
            if len(parts) < 3 or not parts[0]:
                continue
            attrs = {}
            if len(parts) > 3:
                for a in _ATTR_SPLIT.split(parts[3]):
                    if "=" in a:
                        k, v = a.split("=", 1)
                        attrs[k] = v
            typ = parts[2].strip("()")
            res.append({"uri": parts[0],
                        "name": attrs.get("standard::display-name") or urllib.parse.unquote(parts[0].rstrip("/").rsplit("/", 1)[-1]),
                        "size": int(parts[1]) if parts[1].isdigit() else None, "type": typ,
                        "mtime": float(attrs["time::modified"]) if attrs.get("time::modified", "").isdigit() else 0.0,
                        "mime": attrs.get("standard::content-type", ""),
                        "target": attrs.get("standard::target-uri", ""),
                        "hidden": attrs.get("standard::is-hidden") == "TRUE"})
    msg = (err or "").strip()
    msg = "\n".join(l[5:].strip() if l.startswith("gio: ") else l for l in msg.splitlines()
                    if l.strip() and "CRITICAL" not in l and "WARNING" not in l)
    return rc, res, msg


def gio_copy(src_uri: str, dest: str, timeout: float = 3600) -> tuple[bool, str]:
    rc, out, err = util.run(["gio", "copy", "-T", src_uri, dest], timeout=timeout)
    return rc == 0, (err or out).strip()


# --------------------------------------------------------------------------- remote operations (GIO or built-in FTP)
def builtin(uri: str) -> bool:
    """True when this location is handled by LinExplorer's own FTP client instead of gvfs."""
    from . import ftpclient
    return ftpclient.is_ftp_uri(uri) and ftpclient.has_credentials(uri)


def remote_list(uri: str) -> list[dict]:
    """List a remote folder; entries like gio_list(). Raises NetError / ftpclient.NeedsLogin / CertQuestion."""
    from . import ftpclient
    from .locations import NetError
    if builtin(uri):
        try:
            ents = ftpclient.listdir(uri)
        except ftpclient.FtpError as e:
            raise NetError(e.errno or 5, e.strerror or str(e))
        return [{"uri": e["uri"], "name": e["name"], "size": e["size"],
                 "type": "directory" if e["type"] == "dir" else "regular", "mtime": e["mtime"], "mime": "",
                 "target": "", "hidden": e["name"].startswith(".")} for e in ents]
    rc, ents, err = gio_list(uri)
    if rc != 0:
        if ftpclient.is_ftp_uri(uri) and "not mounted" not in err.lower():
            # gvfs cannot talk to this FTP server (e.g. FileZilla Server: 'Operation failed'): use our client
            if ftpclient.activate_known(uri):
                return remote_list(uri)
            raise ftpclient.NeedsLogin(uri, err)
        if "not mounted" in err.lower() or "mount" in err.lower():
            from .locations import NeedsMount
            raise NeedsMount(uri, err.strip())
        raise NetError(5, err.strip() or "gio list failed")
    return ents


def remote_download(uri: str, dest: str, is_dir: bool, cancelled=lambda: False) -> list[str]:
    from . import ftpclient
    if builtin(uri):
        return ftpclient.download(uri, dest, is_dir, cancelled)
    return gio_download(uri, dest, is_dir, cancelled)


def remote_upload(path: str, dest_uri: str, cancelled=lambda: False) -> list[str]:
    from . import ftpclient
    if builtin(dest_uri):
        return ftpclient.upload(path, dest_uri, cancelled)
    return gio_upload(path, dest_uri, cancelled)


def remote_mkdir(uri: str) -> str:
    from . import ftpclient
    if builtin(uri):
        try:
            ftpclient.mkdir(uri)
            return ""
        except OSError as e:
            return str(e)
    rc, out, err = util.run(["gio", "mkdir", uri], timeout=60)
    return "" if rc == 0 else _clean(err or out)


def remote_delete(uri: str, is_dir: bool) -> str:
    from . import ftpclient
    if builtin(uri):
        try:
            ftpclient.delete(uri, is_dir)
            return ""
        except OSError as e:
            return str(e)
    if is_dir:
        rc, ents, err = gio_list(uri)
        if rc != 0:
            return err
        for e in ents:
            m = remote_delete(e["uri"], e["type"] == "directory")
            if m:
                return m
    rc, out, err = util.run(["gio", "remove", uri.rstrip("/") if is_dir else uri], timeout=60)
    return "" if rc == 0 else _clean(err or out)


def remote_rename(uri: str, new_name: str) -> str:
    from . import ftpclient
    if builtin(uri):
        try:
            ftpclient.rename(uri, new_name)
            return ""
        except OSError as e:
            return str(e)
    rc, out, err = util.run(["gio", "rename", uri.rstrip("/"), new_name], timeout=60)
    return "" if rc == 0 else _clean(err or out)


def gio_download(src_uri: str, dest: str, is_dir: bool, cancelled=lambda: False) -> list[str]:
    """Copy a remote file or folder (recursively) to the local path dest. Returns error messages."""
    errors = []
    if not is_dir:
        ok, msg = gio_copy(src_uri, dest)
        if not ok:
            errors.append(f"{src_uri}: {msg}")
        return errors
    os.makedirs(dest, exist_ok=True)
    rc, ents, err = gio_list(src_uri)
    if rc != 0:
        return [f"{src_uri}: {err}"]
    for e in ents:
        if cancelled():
            break
        name = e["name"].replace("/", "_")
        errors += gio_download(e["uri"], os.path.join(dest, name), e["type"] == "directory", cancelled)
    return errors


def gio_upload(path: str, dest_uri: str, cancelled=lambda: False) -> list[str]:
    """Copy a local file or folder (recursively) into the remote folder dest_uri."""
    errors = []
    target = dest_uri.rstrip("/") + "/" + urllib.parse.quote(os.path.basename(path.rstrip("/")))
    if os.path.isdir(path) and not os.path.islink(path):
        rc, out, err = util.run(["gio", "mkdir", target], timeout=60)
        if rc != 0 and "exist" not in (err or "").lower():
            return [f"{target}: {(err or out).strip()}"]
        try:
            names = sorted(os.listdir(path))
        except OSError as e:
            return [f"{path}: {e}"]
        for n in names:
            if cancelled():
                break
            errors += gio_upload(os.path.join(path, n), target + "/", cancelled)
        return errors
    ok, msg = gio_copy(path, target)
    if not ok:
        errors.append(f"{path}: {msg}")
    return errors


def connections() -> list[dict]:
    """Mounted network locations: [{uri, proto, mount, label}]."""
    out = []
    for d in places.drives().list():
        if d.get("kind") != "network" or not d.get("mount"):
            continue
        uri = gvfs_uri(d.get("dev") or "") if d.get("fstype") == "gvfs" else None
        if uri is None:
            fst = d.get("fstype", "")
            proto = "smb" if fst in ("cifs", "smb3") else "sftp" if "sshfs" in fst else "other"
            uri = ""
        else:
            proto = proto_of_uri(uri)
        out.append({"uri": uri, "proto": proto, "mount": d["mount"], "label": d.get("label") or d["mount"]})
    return out


def mount_for(uri: str) -> str | None:
    n = normalize_uri(uri)
    for c in connections():
        if c["uri"] and (normalize_uri(c["uri"]) == n or n.startswith(normalize_uri(c["uri"]) + "/")):
            rest = n[len(normalize_uri(c["uri"])):].strip("/")
            return os.path.join(c["mount"], rest) if rest else c["mount"]
    return None


# --------------------------------------------------------------------------- discovery
_disc_cache = (0.0, [])


def discover(timeout: float = 4.0) -> list[dict]:
    """Servers announced on the local network: [{name, uri, proto}] (avahi + gio network:///)."""
    global _disc_cache
    if time.time() - _disc_cache[0] < 30:
        return _disc_cache[1]
    found: dict[str, dict] = {}
    if util.which("avahi-browse"):
        rc, out, _ = util.run(["avahi-browse", "-a", "-t", "-r", "-p", "-k"], timeout=timeout + 4)
        for ln in out.splitlines():
            f = ln.split(";")
            if len(f) < 10 or f[0] != "=":
                continue
            stype = f[4]
            proto = AVAHI_TYPES.get(stype)
            if not proto:
                continue
            name = f[3].replace("\\032", " ")
            host = f[6] or f[7]
            port = int(f[8]) if f[8].isdigit() else None
            uri = build_uri(proto, host, port)
            key = normalize_uri(uri)
            if key not in found:
                found[key] = {"name": name, "uri": uri, "proto": "dav" if proto in ("dav", "davs") else proto}
    if util.which("gio"):
        rc, ents, _ = gio_list("network:///", timeout=timeout + 2)
        for e in ents if rc == 0 else []:
            target = e["target"]
            if not target or "://" not in target or target.startswith("network:"):
                continue
            proto = proto_of_uri(target)
            key = normalize_uri(target)
            if proto in ("smb", "ftp", "sftp", "dav") and key not in found:
                found[key] = {"name": e["name"] or target, "uri": target, "proto": proto}
        rc, ents, _ = gio_list("smb://", timeout=timeout + 2)
        for e in ents if rc == 0 else []:
            target = e["target"] or e["uri"]
            if target.startswith("smb://"):
                key = normalize_uri(target)
                if key not in found:
                    found[key] = {"name": e["name"] or target, "uri": target, "proto": "smb", "workgroup": True}
    res = sorted(found.values(), key=lambda d: d["name"].casefold())
    _disc_cache = (time.time(), res)
    return res


# --------------------------------------------------------------------------- gio mount driver
_PROMPTS = [
    (re.compile(r"User(?: \[[^\]]*\])?:\s*$"), "user"),
    (re.compile(r"Domain(?: \[[^\]]*\])?:\s*$"), "domain"),
    (re.compile(r"Password:\s*$"), "password"),
    (re.compile(r"Choice:\s*$"), "choice"),
]


def gio_mount(uri: str, user: str | None = None, domain: str | None = None, password: str | None = None,
              anonymous: bool = False, ask=None, timeout: float = 90, pump=None) -> tuple[str, str]:
    """Mount with 'gio mount', answering its prompts.

    Returns (status, text): status is 'ok', 'need_creds', 'cancelled' or 'error'.
    ask(question_text, choices) -> chosen index (0-based) or None for host-key / other questions.
    pump() is called while waiting (to keep a UI responsive)."""
    if not util.which("gio"):
        return "error", "gio (gvfs) is not installed"
    argv = ["gio", "mount"] + (["--anonymous"] if anonymous else []) + [uri]
    try:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=util.clean_env(), bufsize=0)
    except OSError as e:
        return "error", str(e)
    buf = ""
    scan_from = 0
    answered_pw = False
    t0 = time.time()
    status = None
    fd = p.stdout.fileno()
    os.set_blocking(fd, False)
    try:
        while True:
            if pump:
                pump()
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    chunk = b""
                if not chunk:
                    if p.poll() is not None:
                        break
                buf += chunk.decode("utf-8", "replace")
                tail = buf[max(scan_from, len(buf) - 300):]
                for rx, what in _PROMPTS:
                    if rx.search(tail):
                        if what == "user":
                            ans = user if user is not None else (urllib.parse.urlparse(uri).username or
                                                                  os.environ.get("USER", ""))
                        elif what == "domain":
                            ans = domain or "WORKGROUP"
                        elif what == "password":
                            if password is None or answered_pw:
                                status = "need_creds"
                                p.kill()
                                break
                            ans = password
                            answered_pw = True
                        else:
                            lines = buf.splitlines()
                            choices = [re.sub(r"^\s*\[\d+\]\s*", "", ln) for ln in lines if re.match(r"^\s*\[\d+\]", ln)]
                            question = "\n".join(ln for ln in lines if not re.match(r"^\s*\[\d+\]", ln)
                                                 and not ln.strip().startswith("Choice"))[-800:]
                            idx = ask(question.strip(), choices) if ask else 0
                            if idx is None:
                                status = "cancelled"
                                p.kill()
                                break
                            ans = str(idx + 1)
                        try:
                            p.stdin.write((ans + "\n").encode())
                            p.stdin.flush()
                        except (BrokenPipeError, OSError):
                            pass
                        buf += "\n"
                        scan_from = len(buf)
                        break
                if status:
                    break
            elif p.poll() is not None:
                break
            if time.time() - t0 > timeout:
                p.kill()
                return "error", "timeout"
    finally:
        try:
            rest = p.stdout.read() or b""
            buf += rest.decode("utf-8", "replace")
        except (OSError, ValueError):
            pass
        p.wait(timeout=5)
    if status:
        return status, buf
    text = buf.strip()
    low = text.lower()
    if p.returncode == 0 or "already mounted" in low:
        return "ok", text
    if any(x in low for x in ("password", "permission denied", "login", "authentication", "access denied")):
        return "need_creds", text
    msg = [ln for ln in text.splitlines() if ln.lower().startswith(("error", "gio:"))]
    return "error", (msg[-1] if msg else text)


def mount_root_uri(target: str) -> str | None:
    """The gvfs mount (as a URI) that contains target - a URI, a gvfs FUSE path or a net: location."""
    if target.startswith("net:"):
        target = target[4:]
    if target.startswith("/"):
        uri = gvfs_path_to_uri(target)
        if not uri:
            return None
        target = uri
    n = normalize_uri(target)
    best = None
    for c in connections():
        cu = c.get("uri")
        if cu and (n == normalize_uri(cu) or n.startswith(normalize_uri(cu) + "/")):
            if best is None or len(cu) > len(best):
                best = cu
    return best


def mount_path_of(uri: str) -> str | None:
    for c in connections():
        if c.get("uri") and normalize_uri(c["uri"]) == normalize_uri(uri):
            return c["mount"]
    return None


def _clean(msg: str) -> str:
    return "\n".join(l[5:].strip() if l.startswith("gio: ") else l for l in (msg or "").strip().splitlines()
                     if l.strip() and "CRITICAL" not in l and "WARNING" not in l and not l.startswith("(process:"))


def gio_unmount(target: str, force: bool = False) -> tuple[bool, str]:
    """Disconnect a network location given as URI, gvfs FUSE path, net: location or mount folder.

    Unmounting by URI is tried first: it does not touch the FUSE file system, so it also works when the
    FUSE path itself is broken ('Input/output error'). Returns (ok, message); the message is empty when the
    location was not mounted at all."""
    cands = []
    root = mount_root_uri(target)
    if root:
        cands.append(root)
    elif "://" in target:
        n = normalize_uri(target[4:] if target.startswith("net:") else target)
        for m in gio_mount_list():          # stale mounts without a usable FUSE folder
            mu = normalize_uri(m["uri"])
            if n == mu or n.startswith(mu + "/"):
                cands.append(m["uri"])
    if target.startswith("net:"):
        target = target[4:]
    if target not in cands:
        cands.append(target)
    path = mount_path_of(root) if root else None
    if path and path not in cands:
        cands.append(path)
    last = ""
    for c in cands:
        rc, out, err = util.run(["gio", "mount", "-u"] + (["-f"] if force else []) + [c], timeout=60)
        if rc == 0:
            return True, ""
        last = _clean(err or out)
    return False, last


NET_SCHEMES = ("smb", "ftp", "ftps", "ftpis", "sftp", "ssh", "dav", "davs", "afp", "nfs")


def gio_mount_list() -> list[dict]:
    """Every network mount gvfs knows about, straight from 'gio mount -l' (also the ones whose FUSE
    folder is missing or broken): [{uri, name, proto}]."""
    rc, out, _ = util.run(["gio", "mount", "-l"], timeout=15)
    res, seen = [], set()
    for ln in out.splitlines() if rc == 0 else []:
        m = re.match(r"\s*Mount\(\d+\):\s*(.*?)\s*->\s*(\S+)", ln)
        if not m:
            continue
        uri = m.group(2)
        if scheme_of(uri) not in NET_SCHEMES:
            continue
        key = normalize_uri(uri)
        if key in seen:
            continue
        seen.add(key)
        res.append({"uri": uri, "name": m.group(1), "proto": proto_of_uri(uri)})
    return res


def is_mounted(uri: str) -> bool:
    n = normalize_uri(uri)
    for m in gio_mount_list():
        mu = normalize_uri(m["uri"])
        if n == mu or n.startswith(mu + "/"):
            return True
    return False


def force_disconnect_all(proto: str | None = None) -> tuple[int, list[str]]:
    """Force-unmount every network mount (of one protocol bucket, when given). Returns (count, errors)."""
    n, errs = 0, []
    for m in gio_mount_list():
        if proto and (m["proto"] if m["proto"] in PROTOS else "other") != proto:
            continue
        ok, msg = gio_unmount(m["uri"], force=True)
        if ok:
            n += 1
        else:
            errs.append(f"{m['uri']}: {msg}")
    return n, errs


def remove_all_saved(proto: str | None = None) -> int:
    keep, n = [], 0
    for l in saved():
        p = proto_of_uri(l["uri"])
        if proto is None or (p if p in PROTOS else "other") == proto:
            n += 1
        else:
            keep.append(l)
    settings().set_json("net_locations", keep)
    return n


def is_busy_message(msg: str) -> bool:
    low = msg.lower()
    return any(x in low for x in ("busy", "in use", "outstanding", "open files"))
