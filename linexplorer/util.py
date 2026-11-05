"""Formatting, sorting and small OS helpers."""
from __future__ import annotations

import ctypes
import ctypes.util
import datetime as _dt
import math
import os
import re
import shutil
import subprocess
import threading

from PySide6.QtCore import QDateTime, QLocale

# --------------------------------------------------------------------------- locale
_locale = QLocale()


def set_locale(loc: QLocale):
    global _locale
    _locale = loc


def locale() -> QLocale:
    return _locale


def fmt_int(n: int) -> str:
    return _locale.toString(int(n))


def fmt_float(x: float, decimals: int) -> str:
    return _locale.toString(float(x), "f", decimals)


# --------------------------------------------------------------------------- sizes
_UNITS = ["bytes", "KB", "MB", "GB", "TB", "PB"]


def fmt_size_short(n: int | None) -> str:
    """Windows style: three significant digits, '1.23 MB', '12.3 KB', '345 bytes'."""
    if n is None:
        return ""
    if n < 1024:
        from .i18n import T
        return T("n_bytes", fmt_int(n))
    x = float(n)
    i = 0
    while x >= 1024 and i < len(_UNITS) - 1:
        x /= 1024.0
        i += 1
    if x >= 100:
        s = fmt_float(math.floor(x), 0)
    elif x >= 10:
        s = fmt_float(math.floor(x * 10) / 10, 1)
    else:
        s = fmt_float(math.floor(x * 100) / 100, 2)
    return f"{s} {_UNITS[i]}"


def fmt_size_kb(n: int | None) -> str:
    """Details-view column: always KB, rounded up (0 bytes -> '0 KB')."""
    if n is None:
        return ""
    kb = (n + 1023) // 1024
    return f"{fmt_int(kb)} KB"


def fmt_size_long(n: int) -> str:
    """Properties dialog: '1.23 MB (1,290,240 bytes)'."""
    from .i18n import T
    if n < 1024:
        return T("n_bytes", fmt_int(n))
    return f"{fmt_size_short(n)} ({T('n_bytes', fmt_int(n))})"


def fmt_size_gb(n: int) -> str:
    """Drive tiles: '12.3 GB'."""
    return fmt_size_short(n)


# --------------------------------------------------------------------------- dates
def fmt_datetime(ts: float | None) -> str:
    if not ts:
        return ""
    dt = QDateTime.fromSecsSinceEpoch(int(ts))
    return _locale.toString(dt, QLocale.FormatType.ShortFormat)


def fmt_datetime_long(ts: float | None) -> str:
    if not ts:
        return ""
    dt = QDateTime.fromSecsSinceEpoch(int(ts))
    d = _locale.toString(dt.date(), QLocale.FormatType.LongFormat)
    t = _locale.toString(dt.time(), QLocale.FormatType.ShortFormat)
    return f"{d}, {t}"


def date_group(ts: float | None) -> tuple[int, str]:
    """Windows 7 'Group by Date modified' buckets: (order, key)."""
    if not ts:
        return 99, "unspecified"
    now = _dt.datetime.now()
    d = _dt.datetime.fromtimestamp(ts)
    today = now.date()
    dd = d.date()
    if dd > today:
        return 0, "future"
    if dd == today:
        return 1, "today"
    if dd == today - _dt.timedelta(days=1):
        return 2, "yesterday"
    start_week = today - _dt.timedelta(days=today.weekday())
    if dd >= start_week:
        return 3, "earlier_this_week"
    if dd >= start_week - _dt.timedelta(days=7):
        return 4, "last_week"
    if dd.year == today.year and dd.month == today.month:
        return 5, "earlier_this_month"
    lm = (today.replace(day=1) - _dt.timedelta(days=1))
    if dd.year == lm.year and dd.month == lm.month:
        return 6, "last_month"
    if dd.year == today.year:
        return 7, "earlier_this_year"
    return 8, "long_ago"


def size_group(n: int | None, is_dir: bool) -> tuple[int, str]:
    if is_dir or n is None:
        return 0, "unspecified"
    K = 1024
    if n == 0:
        return 1, "size_empty"
    if n < 10 * K:
        return 2, "size_tiny"
    if n < 100 * K:
        return 3, "size_small"
    if n < K * K:
        return 4, "size_medium"
    if n < 16 * K * K:
        return 5, "size_large"
    if n < 128 * K * K:
        return 6, "size_huge"
    return 7, "size_gigantic"


def name_group(name: str) -> tuple[int, str]:
    c = name[:1].upper()
    if not c:
        return 9, "other"
    if c.isdigit():
        return 0, "0 - 9"
    if "A" <= c <= "H":
        return 1, "A - H"
    if "I" <= c <= "P":
        return 2, "I - P"
    if "Q" <= c <= "Z":
        return 3, "Q - Z"
    if "Α" <= c <= "Θ":
        return 4, "Α - Θ"
    if "Ι" <= c <= "Π":
        return 5, "Ι - Π"
    if "Ρ" <= c <= "Ω":
        return 6, "Ρ - Ω"
    return 8, "other"


def fmt_duration(sec: float) -> str:
    sec = int(round(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# --------------------------------------------------------------------------- natural sort
_num_re = re.compile(r"(\d+)")


def natural_key(s: str):
    """Explorer 'logical' order: file2 < file10, case-insensitive."""
    parts = _num_re.split(s.casefold())
    out = []
    for p in parts:
        if p.isdigit():
            out.append((0, int(p), len(p)))
        else:
            out.append((1, p, 0))
    return out


# --------------------------------------------------------------------------- names
def split_ext(name: str) -> tuple[str, str]:
    """('archive.tar', '.gz') style split, hidden files have no extension."""
    if name.startswith(".") and name.count(".") == 1:
        return name, ""
    low = name.lower()
    for dbl in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz", ".tar.z"):
        if low.endswith(dbl) and len(name) > len(dbl):
            return name[:-len(dbl)], name[-len(dbl):]
    base, ext = os.path.splitext(name)
    if not base:
        return name, ""
    return base, ext


def unique_name(folder: str, name: str, is_dir: bool = False, style: str = "paren") -> str:
    """'name (2).ext' like Windows 7 'keep both files' / new folder numbering."""
    if not os.path.lexists(os.path.join(folder, name)):
        return name
    base, ext = (name, "") if is_dir else split_ext(name)
    m = re.match(r"^(.*) \((\d+)\)$", base)
    if m:
        base, n = m.group(1), int(m.group(2)) + 1
    else:
        n = 2
    while True:
        cand = f"{base} ({n}){ext}"
        if not os.path.lexists(os.path.join(folder, cand)):
            return cand
        n += 1


def copy_name(folder: str, name: str, is_dir: bool) -> str:
    """Pasting into the same folder: 'name - Copy.ext', 'name - Copy (2).ext'."""
    from .i18n import T
    base, ext = (name, "") if is_dir else split_ext(name)
    suffix = T("copy_suffix")
    cand = f"{base} - {suffix}{ext}"
    if not os.path.lexists(os.path.join(folder, cand)):
        return cand
    n = 2
    while True:
        cand = f"{base} - {suffix} ({n}){ext}"
        if not os.path.lexists(os.path.join(folder, cand)):
            return cand
        n += 1


def is_hidden_name(name: str) -> bool:
    return name.startswith(".") or name.endswith("~")


def read_dot_hidden(folder: str) -> set[str]:
    try:
        with open(os.path.join(folder, ".hidden"), encoding="utf-8", errors="replace") as f:
            return {ln.strip() for ln in f if ln.strip()}
    except OSError:
        return set()


# --------------------------------------------------------------------------- statx (birth time)
class _StatxTs(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_int64), ("tv_nsec", ctypes.c_uint32), ("_r", ctypes.c_int32)]


class _Statx(ctypes.Structure):
    _fields_ = [
        ("stx_mask", ctypes.c_uint32), ("stx_blksize", ctypes.c_uint32),
        ("stx_attributes", ctypes.c_uint64), ("stx_nlink", ctypes.c_uint32),
        ("stx_uid", ctypes.c_uint32), ("stx_gid", ctypes.c_uint32),
        ("stx_mode", ctypes.c_uint16), ("_s1", ctypes.c_uint16),
        ("stx_ino", ctypes.c_uint64), ("stx_size", ctypes.c_uint64),
        ("stx_blocks", ctypes.c_uint64), ("stx_attributes_mask", ctypes.c_uint64),
        ("stx_atime", _StatxTs), ("stx_btime", _StatxTs), ("stx_ctime", _StatxTs),
        ("stx_mtime", _StatxTs), ("_rest", ctypes.c_uint64 * 48),  # kernel struct is 256 bytes; keep slack
    ]


_libc = None
_statx_fn = None
try:
    _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    _statx_fn = _libc.statx
    _statx_fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_Statx)]
    _statx_fn.restype = ctypes.c_int
except Exception:  # pragma: no cover
    _statx_fn = None

_AT_FDCWD = -100
_STATX_BTIME = 0x800


def birth_time(path: str, fallback: float | None = None) -> float | None:
    """File creation time (statx btime), falling back to ctime when unsupported."""
    if _statx_fn is not None:
        st = _Statx()
        try:
            if _statx_fn(_AT_FDCWD, os.fsencode(path), 0, _STATX_BTIME, ctypes.byref(st)) == 0:
                if st.stx_mask & _STATX_BTIME and st.stx_btime.tv_sec:
                    return st.stx_btime.tv_sec + st.stx_btime.tv_nsec / 1e9
        except Exception:
            pass
    return fallback


# --------------------------------------------------------------------------- processes
def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def clean_env() -> dict:
    """Environment for launching host programs from inside the AppImage."""
    env = dict(os.environ)
    for k in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
              "QML2_IMPORT_PATH", "LD_LIBRARY_PATH", "LINEXPLORER_APPDIR"):
        if k in env and k != "LD_LIBRARY_PATH":
            env.pop(k, None)
    orig = os.environ.get("LINEXPLORER_ORIG_LD_LIBRARY_PATH")
    if orig is not None:
        if orig:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    if "APPDIR" in env:
        for k in ("APPDIR", "APPIMAGE", "ARGV0", "OWD"):
            env.pop(k, None)
    return env


def spawn(argv: list[str], cwd: str | None = None) -> bool:
    """Start a detached host program."""
    try:
        subprocess.Popen(argv, cwd=cwd or os.path.expanduser("~"), env=clean_env(),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def run(argv: list[str], timeout: float = 10, input: str | None = None) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           env=clean_env(), input=input)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


def in_thread(fn, *args, **kw):
    t = threading.Thread(target=fn, args=args, kwargs=kw, daemon=True)
    t.start()
    return t


def same_device(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def is_subpath(child: str, parent: str) -> bool:
    child = os.path.abspath(child)
    parent = os.path.abspath(parent)
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def home() -> str:
    return os.path.expanduser("~")
