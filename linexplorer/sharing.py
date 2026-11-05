"""'Share with' through Samba user shares (net usershare), the Linux equivalent of
Windows 7 network file sharing."""
from __future__ import annotations

import re
import time

from . import util
from .i18n import T

_avail = None
_cache = (0.0, {})


def available() -> bool:
    global _avail
    if _avail is None:
        if not util.which("net"):
            _avail = False
        else:
            rc, out, err = util.run(["net", "usershare", "info"], timeout=5)
            _avail = rc == 0
    return _avail


def shares() -> dict:
    """{name: {path, comment, acl, guest, writable}}."""
    global _cache
    if time.time() - _cache[0] < 2:
        return _cache[1]
    out_d = {}
    rc, out, err = util.run(["net", "usershare", "info"], timeout=5)
    if rc == 0:
        cur = None
        for ln in out.splitlines():
            m = re.match(r"^\[(.+)\]$", ln.strip())
            if m:
                cur = {"name": m.group(1)}
                out_d[cur["name"]] = cur
            elif cur is not None and "=" in ln:
                k, v = ln.split("=", 1)
                cur[k.strip()] = v.strip()
        for s in out_d.values():
            acl = s.get("usershare_acl", "")
            s["writable"] = ":F" in acl or ":f" in acl
            s["guest"] = s.get("guest_ok", "n").lower() == "y"
    _cache = (time.time(), out_d)
    return out_d


def info(path: str) -> dict | None:
    if not available():
        return None
    for s in shares().values():
        if s.get("path", "").rstrip("/") == path.rstrip("/"):
            return s
    return None


def _name_for(path: str) -> str:
    import os
    base = os.path.basename(path.rstrip("/")) or "share"
    base = re.sub(r"[^\w\- ]", "_", base)[:60]
    names = shares()
    name = base
    n = 2
    while name in names and names[name].get("path", "").rstrip("/") != path.rstrip("/"):
        name = f"{base}{n}"
        n += 1
    return name


def set_share(path: str, writable: bool | None, name: str | None = None, comment: str = "",
              guest: bool = True) -> str | None:
    """writable None = stop sharing. Returns an error message or None."""
    global _cache
    if not available():
        return T("sharing_unavailable")
    cur = info(path)
    if writable is None:
        if cur:
            rc, out, err = util.run(["net", "usershare", "delete", cur["name"]], timeout=10)
            _cache = (0.0, {})
            return None if rc == 0 else (err or out).strip()
        return None
    if cur and name and name != cur["name"]:
        util.run(["net", "usershare", "delete", cur["name"]], timeout=10)
        cur = None
    name = name or (cur["name"] if cur else _name_for(path))
    acl = "Everyone:F" if writable else "Everyone:R"
    rc, out, err = util.run(["net", "usershare", "add", name, path, comment, acl, f"guest_ok={'y' if guest else 'n'}"],
                            timeout=10)
    _cache = (0.0, {})
    if rc != 0:
        return (err or out).strip()
    return None
