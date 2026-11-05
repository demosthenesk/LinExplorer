"""Built-in FTP / FTPS client (Python ftplib).

Used for ftp://, ftps:// (explicit TLS) and ftpis:// (implicit TLS) locations when gvfs cannot work with a
server (e.g. FileZilla Server answers gvfs with 'Operation failed'). Supports passive mode (EPSV/PASV), MLSD
with a LIST fallback (Unix and DOS formats), TLS session resumption on data connections (required by
FileZilla Server by default) and per-host trust of self-signed certificates (SHA-256 fingerprint)."""
from __future__ import annotations

import ftplib
import hashlib
import os
import re
import socket
import ssl
import threading
import time
import urllib.parse
from datetime import datetime, timezone

from .settings import settings

TIMEOUT = 30
_creds: dict[tuple, tuple[str, str]] = {}          # (scheme, host, port, user) -> (user, password)
_sessions: dict[tuple, "Session"] = {}
_lock = threading.Lock()


class FtpError(OSError):
    pass


class NeedsLogin(Exception):
    def __init__(self, uri: str, msg: str = ""):
        super().__init__(msg or uri)
        self.uri = uri


class CertQuestion(Exception):
    """The server's certificate is not trusted: ask the user, then call trust_cert()."""

    def __init__(self, host: str, port: int, fingerprint: str, subject: str, issuer: str, reason: str):
        super().__init__(reason)
        self.host, self.port, self.fingerprint = host, port, fingerprint
        self.uri = ""
        self.subject, self.issuer, self.reason = subject, issuer, reason


# --------------------------------------------------------------------------- addresses
def parse(uri: str) -> tuple[tuple, str]:
    """'ftps://bob@nas:2121/a b/' -> ((scheme, host, port, user), '/a b')."""
    if uri.startswith("net:"):
        uri = uri[4:]
    pr = urllib.parse.urlparse(uri)
    scheme = pr.scheme.lower()
    port = pr.port or (990 if scheme == "ftpis" else 21)
    user = urllib.parse.unquote(pr.username or "")
    path = urllib.parse.unquote(pr.path or "/") or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return (scheme, (pr.hostname or "").lower(), port, user), path


def make_uri(key: tuple, path: str) -> str:
    scheme, host, port, user = key
    default = 990 if scheme == "ftpis" else 21
    netloc = (urllib.parse.quote(user, safe="") + "@" if user else "") + host + (f":{port}" if port != default else "")
    p = "/".join(urllib.parse.quote(x) for x in path.split("/"))
    return f"{scheme}://{netloc}{p if p.startswith('/') else '/' + p}"


def is_ftp_uri(uri: str) -> bool:
    if uri.startswith("net:"):
        uri = uri[4:]
    return uri.split(":", 1)[0].lower() in ("ftp", "ftps", "ftpis")


# --------------------------------------------------------------------------- credentials / trust
_known: dict[tuple, tuple[str, str]] = {}


def remember(uri: str, user: str | None, password: str | None, anonymous: bool = False):
    """Credentials typed for a gvfs connection, kept (in memory only) in case gvfs cannot list the server."""
    key, _ = parse(uri)
    if anonymous:
        _known[key[:3]] = ("anonymous", "anonymous@")
    elif password is not None:
        _known[key[:3]] = (user or key[3] or os.environ.get("USER", ""), password)


def activate_known(uri: str) -> bool:
    key, _ = parse(uri)
    k = _known.get(key[:3])
    if not k:
        return False
    set_credentials(uri, k[0], k[1], anonymous=k[0] == "anonymous")
    return True


def set_credentials(uri: str, user: str, password: str, anonymous: bool = False):
    key, _ = parse(uri)
    if anonymous:
        user, password = "anonymous", "anonymous@"
    _creds[key] = (user or key[3] or "anonymous", password)
    if user and not key[3]:
        _creds[(key[0], key[1], key[2], user)] = _creds[key]
    drop(uri)


def has_credentials(uri: str) -> bool:
    key, _ = parse(uri)
    return key in _creds or (key[3] == "" and any(k[:3] == key[:3] for k in _creds))


def forget(uri: str):
    key, _ = parse(uri)
    for k in [k for k in _creds if k[:3] == key[:3] and (not key[3] or k[3] == key[3])]:
        _creds.pop(k, None)
        with _lock:
            s = _sessions.pop(k, None)
        if s:
            s.close()
    _known.pop(key[:3], None)


def active() -> list[str]:
    """Addresses that are browsed with the built-in client in this session."""
    return [make_uri(k, "/") for k in _creds]


def _trusted() -> dict:
    return settings().get_json("ftp_trusted_certs", {})


def trust_cert(host: str, port: int, fingerprint: str):
    d = _trusted()
    d[f"{host}:{port}"] = fingerprint
    settings().set_json("ftp_trusted_certs", d)


def _fp(der: bytes) -> str:
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def _contexts(host: str, port: int):
    """(context, expected_fingerprint or None)."""
    fp = _trusted().get(f"{host}:{port}")
    if fp:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx, fp
    return ssl.create_default_context(), None


def _cert_question(host, port, sock_or_der, reason) -> CertQuestion:
    der = sock_or_der if isinstance(sock_or_der, bytes) else b""
    subject = issuer = ""
    try:
        if not der:
            raise ValueError
        import tempfile
        pem = ssl.DER_cert_to_PEM_cert(der)
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
            f.write(pem)
        try:
            info = ssl._ssl._test_decode_cert(f.name)       # noqa: SLF001 (no public API for this)
            subject = ", ".join("=".join(x) for r in info.get("subject", ()) for x in r)
            issuer = ", ".join("=".join(x) for r in info.get("issuer", ()) for x in r)
        finally:
            os.unlink(f.name)
    except Exception:  # noqa: BLE001
        pass
    return CertQuestion(host, port, _fp(der) if der else "", subject, issuer, reason)


def _fetch_cert(host: str, port: int, implicit: bool) -> bytes:
    """The server certificate without verification (for the trust question)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    s = socket.create_connection((host, port), TIMEOUT)
    try:
        if not implicit:
            f = s.makefile("rb")
            _read_reply(f)
            s.sendall(b"AUTH TLS\r\n")
            _read_reply(f)
        t = ctx.wrap_socket(s, server_hostname=host)
        der = t.getpeercert(binary_form=True) or b""
        t.close()
        return der
    finally:
        s.close()


def _read_reply(f):
    line = f.readline().decode("utf-8", "replace")
    if line[3:4] == "-":
        code = line[:3]
        while True:
            ln = f.readline().decode("utf-8", "replace")
            if not ln or (ln[:3] == code and ln[3:4] == " "):
                break
    return line


# --------------------------------------------------------------------------- ftplib classes
class _FTP(ftplib.FTP):
    def makepasv(self):
        # some servers advertise a wrong (public / internal) address in PASV: always use the control host
        if self.af == socket.AF_INET:
            try:
                untrusted, port = ftplib.parse227(self.sendcmd("PASV"))
                return self.host, port
            except ftplib.error_perm:
                pass
        return ftplib.parse229(self.sendcmd("EPSV"), self.sock.getpeername())


class _FTPS(ftplib.FTP_TLS):
    """Explicit FTPS with TLS session resumption on the data connection."""

    def makepasv(self):
        return _FTP.makepasv(self)

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size

    def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):
        self.voidcmd("TYPE I")
        with self.transfercmd(cmd, rest) as conn:
            while True:
                buf = fp.read(blocksize)
                if not buf:
                    break
                conn.sendall(buf)
                if callback:
                    callback(buf)
            if isinstance(conn, ssl.SSLSocket):
                try:
                    conn.unwrap()
                except (OSError, ValueError):
                    pass
        return self.voidresp()


class _FTPIS(_FTPS):
    """Implicit FTPS (TLS from the first byte, usually port 990)."""

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.af = self.sock.family
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


# --------------------------------------------------------------------------- sessions
class Session:
    def __init__(self, key: tuple):
        self.key = key
        self.ftp = None
        self.lock = threading.RLock()
        self.mlsd = True
        self.last = 0.0

    def _connect(self):
        scheme, host, port, user = self.key
        cred = _creds.get(self.key)
        if cred is None:
            raise NeedsLogin(make_uri(self.key, "/"))
        luser, pw = cred
        if scheme in ("ftps", "ftpis"):
            ctx, fp = _contexts(host, port)
            cls = _FTPIS if scheme == "ftpis" else _FTPS
            ftp = cls(context=ctx, timeout=TIMEOUT, encoding="utf-8")
            try:
                ftp.connect(host, port, TIMEOUT)
                if scheme == "ftps":
                    ftp.auth()
            except ssl.SSLCertVerificationError as e:
                try:
                    der = _fetch_cert(host, port, scheme == "ftpis")
                except OSError:
                    der = b""
                q = _cert_question(host, port, der, e.verify_message or str(e))
                q.uri = make_uri(self.key, "/")
                raise q
            if fp:
                der = ftp.sock.getpeercert(binary_form=True) or b""
                if _fp(der) != fp:
                    ftp.close()
                    q = _cert_question(host, port, der, "certificate changed")
                    q.uri = make_uri(self.key, "/")
                    raise q
        else:
            ftp = _FTP(timeout=TIMEOUT, encoding="utf-8")
            ftp.connect(host, port, TIMEOUT)
        try:
            ftp.login(luser, pw)
        except ftplib.error_perm as e:
            ftp.close()
            if str(e)[:3] in ("530", "331", "332"):
                _creds.pop(self.key, None)
                raise NeedsLogin(make_uri(self.key, "/"), str(e))
            raise FtpError(5, str(e))
        if scheme in ("ftps", "ftpis"):
            ftp.prot_p()
        ftp.set_pasv(True)
        try:
            ftp.sendcmd("OPTS UTF8 ON")
        except ftplib.all_errors:
            pass
        self.ftp = ftp

    def run(self, fn):
        """fn(ftp) with one reconnect when the connection dropped."""
        with self.lock:
            for attempt in (0, 1):
                if self.ftp is None:
                    self._connect()
                try:
                    r = fn(self.ftp)
                    self.last = time.time()
                    return r
                except (EOFError, ConnectionError, socket.timeout, ssl.SSLError, ftplib.error_temp) as e:
                    if isinstance(e, ftplib.error_temp) and not str(e).startswith(("421", "425", "426")):
                        raise FtpError(5, str(e))
                    self.close()
                    if attempt:
                        raise FtpError(5, str(e) or type(e).__name__)
                except ftplib.error_perm as e:
                    raise FtpError(2 if str(e).startswith("550") else 5, str(e))
                except ftplib.error_reply as e:
                    raise FtpError(5, str(e))

    def close(self):
        if self.ftp is not None:
            try:
                self.ftp.quit()
            except Exception:  # noqa: BLE001
                try:
                    self.ftp.close()
                except Exception:  # noqa: BLE001
                    pass
        self.ftp = None


def session(uri: str) -> tuple[Session, str]:
    key, path = parse(uri)
    if key not in _creds and key[3] == "":
        # anonymous address but credentials saved for a user on the same server
        for k in _creds:
            if k[:3] == key[:3]:
                key = k
                break
    with _lock:
        s = _sessions.get(key)
        if s is None:
            s = _sessions[key] = Session(key)
    return s, path


def drop(uri: str):
    key, _ = parse(uri)
    with _lock:
        s = _sessions.pop(key, None)
    if s:
        s.close()


def check(uri: str):
    """Connect and log in (raises NeedsLogin / CertQuestion / FtpError)."""
    s, path = session(uri)
    s.run(lambda f: f.pwd())


# --------------------------------------------------------------------------- listing
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"], 1)}
_UNIX = re.compile(r"^([\-dlbcps])[rwxsStT\-]{9}\S*\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(\w{3})\s+(\d{1,2})\s+"
                   r"(\d{1,2}:\d{2}|\d{4})\s+(.+)$")
_UNIX_NOGROUP = re.compile(r"^([\-dlbcps])[rwxsStT\-]{9}\S*\s+\d+\s+\S+\s+(\d+)\s+(\w{3})\s+(\d{1,2})\s+"
                           r"(\d{1,2}:\d{2}|\d{4})\s+(.+)$")
_DOS = re.compile(r"^(\d{2})-(\d{2})-(\d{2,4})\s+(\d{1,2}):(\d{2})(AM|PM)?\s+(<DIR>|\d+)\s+(.+)$", re.I)


def _parse_list_line(ln: str):
    m = _UNIX.match(ln) or _UNIX_NOGROUP.match(ln)
    if m:
        t, size, mon, day, ty, name = m.groups()
        now = datetime.now()
        try:
            if ":" in ty:
                hh, mm = ty.split(":")
                dt = datetime(now.year, _MONTHS.get(mon.lower(), 1), int(day), int(hh), int(mm))
                if dt > now.replace(microsecond=0) and (dt - now).days > 1:
                    dt = dt.replace(year=now.year - 1)
            else:
                dt = datetime(int(ty), _MONTHS.get(mon.lower(), 1), int(day))
            mtime = dt.timestamp()
        except ValueError:
            mtime = 0.0
        if t == "l" and " -> " in name:
            name = name.split(" -> ", 1)[0]
        typ = {"d": "dir", "l": "link"}.get(t, "file")
        return {"name": name, "type": typ, "size": int(size) if typ == "file" else None, "mtime": mtime}
    m = _DOS.match(ln)
    if m:
        mo, d, y, hh, mi, ap, sz, name = m.groups()
        y = int(y)
        y += 2000 if y < 70 else (1900 if y < 100 else 0)
        h = int(hh) % 12 + (12 if (ap or "").upper() == "PM" else 0) if ap else int(hh)
        try:
            mtime = datetime(y, int(mo), int(d), h, int(mi)).timestamp()
        except ValueError:
            mtime = 0.0
        is_dir = sz.upper() == "<DIR>"
        return {"name": name, "type": "dir" if is_dir else "file", "size": None if is_dir else int(sz), "mtime": mtime}
    return None


def _mlsd_time(v: str) -> float:
    try:
        return datetime.strptime(v[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def listdir(uri: str) -> list[dict]:
    """[{name, type ('dir'|'file'|'link'), size, mtime, uri}]"""
    s, path = session(uri)

    def do(ftp):
        out = []
        if s.mlsd:
            try:
                for name, facts in ftp.mlsd(path, facts=["type", "size", "modify"]):
                    t = facts.get("type", "").lower()
                    if t in ("cdir", "pdir") or name in (".", ".."):
                        continue
                    out.append({"name": name.rsplit("/", 1)[-1],
                                 "type": "dir" if t == "dir" else "file",
                                 "size": int(facts["size"]) if facts.get("size", "").isdigit() and t != "dir" else None,
                                 "mtime": _mlsd_time(facts.get("modify", ""))})
                return out
            except ftplib.error_perm as e:
                if not str(e).startswith(("500", "501", "502", "504")):
                    raise
                s.mlsd = False
        lines = []
        ftp.cwd(path)
        ftp.retrlines("LIST", lines.append)
        for ln in lines:
            e = _parse_list_line(ln.rstrip("\r"))
            if e and e["name"] not in (".", ".."):
                out.append(e)
        # links: find out whether they point to folders
        for e in out:
            if e["type"] == "link":
                try:
                    ftp.cwd((path.rstrip("/") + "/" + e["name"]))
                    e["type"] = "dir"
                except ftplib.error_perm:
                    e["type"] = "file"
        return out
    res = s.run(do)
    for e in res:
        e["uri"] = make_uri(s.key, path.rstrip("/") + "/" + e["name"]) + ("/" if e["type"] == "dir" else "")
    return res


# --------------------------------------------------------------------------- transfers / changes
def download(uri: str, local: str, is_dir: bool, cancelled=lambda: False) -> list[str]:
    if is_dir:
        os.makedirs(local, exist_ok=True)
        errs = []
        try:
            entries = listdir(uri)
        except OSError as e:
            return [f"{uri}: {e}"]
        for e in entries:
            if cancelled():
                break
            errs += download(e["uri"], os.path.join(local, e["name"].replace("/", "_")), e["type"] == "dir", cancelled)
        return errs
    s, path = session(uri)
    tmp = local + ".part"
    try:
        with open(tmp, "wb") as f:
            def cb(b):
                if cancelled():
                    raise InterruptedError
                f.write(b)
            s.run(lambda ftp: ftp.retrbinary("RETR " + path, cb))
        os.replace(tmp, local)
        return []
    except InterruptedError:
        s.close()
        return []
    except OSError as e:
        return [f"{uri}: {e}"]
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def upload(local: str, dest_uri: str, cancelled=lambda: False) -> list[str]:
    s, dpath = session(dest_uri)
    name = os.path.basename(local.rstrip("/"))
    rpath = dpath.rstrip("/") + "/" + name
    if os.path.isdir(local) and not os.path.islink(local):
        try:
            s.run(lambda ftp: ftp.mkd(rpath))
        except OSError as e:
            if "550" not in str(e) and "exist" not in str(e).lower():
                return [f"{rpath}: {e}"]
        errs = []
        sub = make_uri(s.key, rpath) + "/"
        for n in sorted(os.listdir(local)):
            if cancelled():
                break
            errs += upload(os.path.join(local, n), sub, cancelled)
        return errs
    try:
        with open(local, "rb") as f:
            def cb(_b):
                if cancelled():
                    raise InterruptedError
            s.run(lambda ftp: (f.seek(0), ftp.storbinary("STOR " + rpath, f, callback=cb))[1])
        return []
    except InterruptedError:
        s.close()
        return []
    except OSError as e:
        return [f"{local}: {e}"]


def mkdir(uri: str):
    s, path = session(uri)
    s.run(lambda ftp: ftp.mkd(path))


def delete(uri: str, is_dir: bool):
    if is_dir:
        for e in listdir(uri):
            delete(e["uri"], e["type"] == "dir")
        s, path = session(uri)
        s.run(lambda ftp: ftp.rmd(path))
    else:
        s, path = session(uri)
        s.run(lambda ftp: ftp.delete(path))


def rename(uri: str, new_name: str) -> str:
    s, path = session(uri)
    parent = path.rstrip("/").rsplit("/", 1)[0] or "/"
    dst = parent.rstrip("/") + "/" + new_name
    s.run(lambda ftp: ftp.rename(path.rstrip("/"), dst))
    return make_uri(s.key, dst)


def disconnect(uri: str):
    forget(uri)
