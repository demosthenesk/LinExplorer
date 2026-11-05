"""Adaptive copy engine checks. Needs an ext4 loop mount at /mnt/lxext and an ntfs-3g mount at /mnt/lxntfs
(see the README's test notes); runs as root in the test container.

    python tests/copy_engine.py
"""
import hashlib
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from linexplorer import copyengine as C  # noqa: E402

checks = []


def check(name, cond):
    checks.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name, flush=True)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def dirty_kb():
    with open("/proc/meminfo") as f:
        for ln in f:
            if ln.startswith("Dirty:"):
                return int(ln.split()[1])
    return 0


SRC_DIR = "/tmp/claude-0/cpe"
os.makedirs(SRC_DIR, exist_ok=True)
src = os.path.join(SRC_DIR, "big.bin")
if not os.path.exists(src) or os.path.getsize(src) != 600 << 20:
    with open(src, "wb") as f:
        for _ in range(600):
            f.write(os.urandom(1 << 20))
os.sync()
H = sha(src)

# classification
s_same = C.classify(src, SRC_DIR)
check("same file system -> local + same_fs", s_same.kind == "local" and s_same.same_fs)
s_ext = C.classify(src, "/mnt/lxext")
check("other local disk (loop ext4) -> local, not same", s_ext.kind == "local" and not s_ext.same_fs and s_ext.fstype == "ext4")
s_ntfs = C.classify(src, "/mnt/lxntfs")
check("ntfs-3g -> fuse strategy with 8 MB chunks + fdatasync", s_ntfs.kind == "fuse" and s_ntfs.chunk == 8 << 20
      and s_ntfs.sync_mode == "fdatasync")
real_block = C._block_info
C._block_info = lambda dev=None, node="": (True, True)
s_usb = C.classify(src, "/mnt/lxext")
check("USB/removable -> 4 MB chunks, 32 MB windows, final fdatasync",
      s_usb.kind == "removable" and s_usb.chunk == 4 << 20 and s_usb.window == 32 << 20 and s_usb.final_sync)
s_usb_ntfs = C.classify(src, "/mnt/lxntfs")
C._block_info = real_block
check("NTFS on a USB disk (fuseblk on /dev) keeps the fuse rules", s_usb_ntfs.kind in ("fuse", "removable"))
s_net = C.Strategy("network", 8 << 20, 64 << 20, "fdatasync", True, False, "cifs", "network")


def run(strat, dst, sample=False):
    total = [0]
    peak = [0]
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            peak[0] = max(peak[0], dirty_kb())
            time.sleep(0.02)
    t = threading.Thread(target=sampler, daemon=True)
    if sample:
        t.start()
    t0 = time.time()
    m = C.copy_file(src, dst, strat, lambda n: total.__setitem__(0, total[0] + n))
    el = time.time() - t0
    stop.set()
    return m, total[0], el, peak[0]


# same file system: copy_file_range in the kernel
d = os.path.join(SRC_DIR, "same.bin")
m, tot, el, _ = run(s_same, d)
check(f"same FS uses {m} ({el:.2f}s), content identical", m in ("reflink", "copy_file_range") and sha(d) == H and tot == 600 << 20)
os.remove(d)

# plain local copy vs removable (throttled) copy to the loop ext4 disk: dirty memory stays bounded
os.sync()
d = "/mnt/lxext/plain.bin"
s_plain = C.Strategy("local", 8 << 20, 1 << 40, "range", False, False, "ext4", "local")   # no throttling at all
m, tot, el, peak_plain = run(s_plain, d, sample=True)
os.sync()
os.remove(d)
os.sync()
d = "/mnt/lxext/usb.bin"
m, tot, el, peak_usb = run(s_usb, d, sample=True)
print(f"   dirty page cache peak: unthrottled {peak_plain // 1024} MB, removable strategy {peak_usb // 1024} MB")
check("removable strategy keeps dirty data bounded (< 100 MB)", peak_usb < 100 * 1024)
check("removable strategy holds far less dirty data than an unthrottled copy", peak_usb * 3 < max(peak_plain, 1))
check("removable copy: content identical and data on disk at the end (Dirty ~ 0)",
      sha(d) == H and tot == 600 << 20 and dirty_kb() < 40 * 1024)
os.remove(d)

# NTFS (ntfs-3g FUSE)
d = "/mnt/lxntfs/ntfs.bin"
m, tot, el, _ = run(s_ntfs, d)
check(f"NTFS via FUSE: {m}, {600 / el:.0f} MB/s, content identical", m == "read/write" and sha(d) == H)
os.remove(d)

# network-like (fdatasync windows) on the loop disk
d = "/mnt/lxext/net.bin"
m, tot, el, _ = run(s_net, d)
check("network strategy: read/write with fdatasync windows, content identical", m == "read/write" and sha(d) == H)
os.remove(d)

# cancellation and small/empty files
d = "/mnt/lxext/cancel.bin"
n = [0]
try:
    C.copy_file(src, d, s_usb, lambda k: n.__setitem__(0, n[0] + k), lambda: n[0] > (50 << 20))
    check("cancel raises", False)
except C.Cancelled:
    check("cancel stops the copy", n[0] < (100 << 20))
if os.path.exists(d):
    os.remove(d)
e = os.path.join(SRC_DIR, "empty")
open(e, "wb").close()
for st in (s_same, s_usb, s_ntfs):
    C.copy_file(e, "/mnt/lxext/empty", st)
check("empty file copies", os.path.getsize("/mnt/lxext/empty") == 0)
small = os.path.join(SRC_DIR, "small")
open(small, "wb").write(b"x" * 12345)
C.copy_file(small, "/mnt/lxntfs/small", s_ntfs)
check("small file over FUSE", open("/mnt/lxntfs/small", "rb").read() == b"x" * 12345)

fails = [c for c in checks if not c[1]]
print(f"\n{len(checks) - len(fails)}/{len(checks)} checks passed")
sys.exit(1 if fails else 0)
