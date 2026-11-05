"""File copy engine that adapts to the destination.

Linux keeps written data in RAM ('dirty' page cache, up to vm.dirty_ratio = 20 % of memory by default) and
writes it out later. Copying many GB to a slow device therefore starts fast, then stalls, the progress bar
reaches 100 % long before the data is on the device, and the whole system becomes sluggish while the cache
drains - the same thing cp and Caja show. This engine picks a strategy per destination:

* same file system  -> reflink (FICLONE: instant on btrfs/XFS) or copy_file_range in the kernel
* USB / removable   -> 4 MB chunks, write-back started every 32 MB and waited for one window later
                       (sync_file_range), cache dropped behind, fdatasync at the end -> steady, honest speed
* NTFS/exFAT (fuse), network (CIFS, NFS, gvfs, sshfs) -> 8 MB chunks, fdatasync every 64 MB
* other local disks -> 8 MB chunks (or copy_file_range), write-back bounded to 256 MB windows

The source file's pages are dropped from the cache after reading (posix_fadvise DONTNEED) so a large copy
does not push everything else out of memory."""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import fcntl
import os
import stat as _stat

FICLONE = 0x40049409
SYNC_FILE_RANGE_WAIT_BEFORE = 1
SYNC_FILE_RANGE_WRITE = 2
SYNC_FILE_RANGE_WAIT_AFTER = 4

FUSE_LIKE = ("fuseblk", "fuse", "ntfs", "ntfs3", "exfat", "vfat", "msdos")
NETWORK = ("cifs", "smb3", "nfs", "nfs4", "9p", "fuse.sshfs", "fuse.gvfsd-fuse", "fuse.rclone", "davfs",
           "fuse.davfs2", "afs", "ceph", "glusterfs")

_libc = None


def _sync_file_range(fd: int, off: int, n: int, flags: int) -> bool:
    global _libc
    if _libc is None:
        try:
            _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
            _libc.sync_file_range.argtypes = [ctypes.c_int, ctypes.c_longlong, ctypes.c_longlong, ctypes.c_uint]
            _libc.sync_file_range.restype = ctypes.c_int
        except (OSError, AttributeError):
            _libc = False
    if not _libc:
        return False
    return _libc.sync_file_range(fd, off, n, flags) == 0


# --------------------------------------------------------------------------- destination classification
class Strategy:
    __slots__ = ("kind", "chunk", "window", "sync_mode", "final_sync", "same_fs", "fstype", "label")

    def __init__(self, kind, chunk, window, sync_mode, final_sync, same_fs, fstype, label):
        self.kind, self.chunk, self.window, self.sync_mode = kind, chunk, window, sync_mode
        self.final_sync, self.same_fs, self.fstype, self.label = final_sync, same_fs, fstype, label

    def __repr__(self):
        return (f"<Strategy {self.kind} fs={self.fstype} chunk={self.chunk >> 20}M window={self.window >> 20}M "
                f"sync={self.sync_mode} final={self.final_sync} same={self.same_fs}>")


def _mount_of(path: str) -> tuple[str, str, str]:
    """(fstype, mount point, source) of the file system holding path."""
    best = ("", "/", "")
    path = os.path.realpath(path)
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
            for ln in f:
                parts = ln.split()
                if " - " not in ln or len(parts) < 5:
                    continue
                mp = parts[4].replace("\\040", " ")
                pre, post = ln.split(" - ", 1)
                fst, src = (post.split() + ["", ""])[:2]
                if (path == mp or path.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(best[1]):
                    best = (fst, mp, src)
    except OSError:
        pass
    return best


def _block_info(dev: int | None = None, node: str = "") -> tuple[bool, bool]:
    """(removable_or_usb, rotational) for a block device given by st_dev or /dev/... node."""
    try:
        if node:
            st = os.stat(node)
            if not _stat.S_ISBLK(st.st_mode):
                return False, False
            dev = st.st_rdev
        if dev is None:
            return False, False
        sys = os.path.realpath(f"/sys/dev/block/{os.major(dev)}:{os.minor(dev)}")
    except OSError:
        return False, False
    if not os.path.exists(sys):
        return False, False
    disk = sys if os.path.exists(os.path.join(sys, "removable")) else os.path.dirname(sys)

    def rd(name):
        try:
            with open(os.path.join(disk, name)) as f:
                return f.read().strip()
        except OSError:
            return ""
    usb = "/usb" in sys or "/mmc" in sys or os.path.basename(disk).startswith("mmcblk")
    removable = rd("removable") == "1"
    rotational = rd("queue/rotational") == "1"
    return usb or removable, rotational


def classify(src: str, dst_dir: str) -> Strategy:
    try:
        same = os.stat(src).st_dev == os.stat(dst_dir).st_dev
    except OSError:
        same = False
    fst, mp, source = _mount_of(dst_dir)
    try:
        ddev = os.stat(dst_dir).st_dev
    except OSError:
        ddev = None
    if fst in NETWORK or fst.startswith("fuse.") and fst not in ("fuse.ntfs-3g",) and "gvfs" in mp:
        return Strategy("network", 8 << 20, 64 << 20, "fdatasync", True, False, fst, "network")
    if fst.startswith("fuse.") and fst not in ("fuseblk",) and source and not source.startswith("/dev/"):
        return Strategy("network", 8 << 20, 64 << 20, "fdatasync", True, False, fst, "fuse")
    if fst in FUSE_LIKE or fst.startswith("fuse"):
        removable, _rot = _block_info(node=source) if source.startswith("/dev/") else (False, False)
        if removable:
            return Strategy("removable", 4 << 20, 32 << 20, "fdatasync" if fst.startswith("fuse") else "range",
                            True, same and not fst.startswith("fuse"), fst, "removable")
        return Strategy("fuse", 8 << 20, 64 << 20, "fdatasync" if fst.startswith("fuse") else "range", False,
                        False, fst, "fuse")
    removable, rotational = _block_info(dev=ddev)
    if not removable and source.startswith("/dev/"):
        removable, rotational = _block_info(node=source)
    if removable:
        return Strategy("removable", 4 << 20, 32 << 20, "range", True, same, fst, "removable")
    return Strategy("local", 8 << 20, 256 << 20, "range", False, same, fst, "hdd" if rotational else "local")


# --------------------------------------------------------------------------- copying
class Cancelled(Exception):
    pass


def copy_file(src: str, dst: str, strat: Strategy, progress=lambda n: None, cancelled=lambda: False):
    """Copy the contents of src to dst (created/truncated). progress(n) gets byte counts as they are
    written (for throttled destinations: once the device has accepted them)."""
    fin = os.open(src, os.O_RDONLY)
    try:
        st = os.fstat(fin)
        size = st.st_size
        fout = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            try:
                os.posix_fadvise(fin, 0, 0, os.POSIX_FADV_SEQUENTIAL)
            except (OSError, AttributeError):
                pass
            if strat.same_fs and size:
                try:
                    fcntl.ioctl(fout, FICLONE, fin)          # reflink: instant, shares blocks (btrfs, XFS)
                    progress(size)
                    return "reflink"
                except OSError:
                    pass
            method = _copy_loop(fin, fout, size, strat, progress, cancelled)
            if strat.final_sync:
                os.fdatasync(fout)
            return method
        finally:
            os.close(fout)
    finally:
        try:
            os.posix_fadvise(fin, 0, 0, os.POSIX_FADV_DONTNEED)
        except (OSError, AttributeError):
            pass
        os.close(fin)


def _copy_loop(fin, fout, size, strat: Strategy, progress, cancelled) -> str:
    use_cfr = hasattr(os, "copy_file_range") and strat.kind != "network" and not strat.fstype.startswith("fuse")
    method = "copy_file_range" if use_cfr else "read/write"
    pos = 0
    mark = 0          # start of the window being written back
    prev = 0          # start of the window we wait for
    chunk = strat.chunk
    buf = bytearray(chunk) if not use_cfr else None
    view = memoryview(buf) if buf is not None else None
    while True:
        if cancelled():
            raise Cancelled()
        n = 0
        if use_cfr:
            try:
                n = os.copy_file_range(fin, fout, chunk)
            except OSError as e:
                if e.errno in (errno.EXDEV, errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.EBADF) and pos == 0:
                    use_cfr = False
                    method = "read/write"
                    buf = bytearray(chunk)
                    view = memoryview(buf)
                    continue
                raise
        else:
            got = os.readv(fin, [buf])
            if got:
                off = 0
                while off < got:
                    off += os.write(fout, view[off:got])
            n = got
        if not n:
            break
        pos += n
        if pos - mark >= strat.window:
            _writeback(fin, fout, strat, prev, mark, pos)
            prev, mark = mark, pos
        progress(n)
    if pos > mark and strat.kind in ("removable", "network", "fuse"):
        _writeback(fin, fout, strat, prev, mark, pos)
    return method


def _writeback(fin, fout, strat: Strategy, prev: int, mark: int, pos: int):
    """Start write-back of [mark, pos) and wait until [prev, mark) is on the device, so at most two windows
    of dirty data exist for this file."""
    if strat.sync_mode == "range" and _sync_file_range(fout, mark, pos - mark, SYNC_FILE_RANGE_WRITE):
        if mark > prev:
            _sync_file_range(fout, prev, mark - prev,
                             SYNC_FILE_RANGE_WAIT_BEFORE | SYNC_FILE_RANGE_WRITE | SYNC_FILE_RANGE_WAIT_AFTER)
            try:
                os.posix_fadvise(fout, prev, mark - prev, os.POSIX_FADV_DONTNEED)
            except OSError:
                pass
    else:
        try:
            os.fdatasync(fout)
        except OSError:
            pass
        try:
            os.posix_fadvise(fout, 0, pos, os.POSIX_FADV_DONTNEED)
        except OSError:
            pass
    try:
        os.posix_fadvise(fin, prev, pos - prev, os.POSIX_FADV_DONTNEED)
    except OSError:
        pass
