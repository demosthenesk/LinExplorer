"""Thumbnails (images, videos, PDFs) with the freedesktop.org thumbnail cache."""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

from . import util

CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "thumbnails")
BUCKETS = ((128, "normal"), (256, "large"), (512, "x-large"))

_IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico", ".svg", ".svgz", ".xpm",
            ".pbm", ".pgm", ".ppm", ".tga", ".avif", ".heic", ".jxl", ".icns"}
_VID_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg", ".3gp", ".ogv", ".ts"}


def bucket_for(size: int) -> tuple[int, str]:
    for px, name in BUCKETS:
        if size <= px:
            return px, name
    return BUCKETS[-1]


def kind_of(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in _IMG_EXT:
        return "image"
    if ext in _VID_EXT:
        return "video"
    if ext == ".pdf":
        return "pdf"
    return None


class _Signals(QObject):
    done = Signal(str, int, QImage)


class _Job(QRunnable):
    def __init__(self, svc, path, mtime, px, bname, kind):
        super().__init__()
        self.svc, self.path, self.mtime, self.px, self.bname, self.kind = svc, path, mtime, px, bname, kind

    def run(self):
        img = QImage()
        try:
            img = self._cached()
            if img.isNull():
                img = self._make()
                if not img.isNull():
                    self._store(img)
        except Exception:
            img = QImage()
        self.svc._sig.done.emit(self.path, self.px, img)

    def _uri(self):
        return QUrl.fromLocalFile(self.path).toString(QUrl.ComponentFormattingOption.FullyEncoded)

    def _cache_file(self):
        md5 = hashlib.md5(self._uri().encode()).hexdigest()
        return os.path.join(CACHE, self.bname, md5 + ".png")

    def _cached(self) -> QImage:
        f = self._cache_file()
        if not os.path.exists(f):
            return QImage()
        img = QImage(f)
        if img.isNull():
            return img
        mt = img.text("Thumb::MTime")
        if mt and int(float(mt)) != int(self.mtime):
            return QImage()
        return img

    def _store(self, img: QImage):
        if util.is_subpath(self.path, CACHE) or util.is_subpath(self.path, tempfile.gettempdir()):
            return
        try:
            os.makedirs(os.path.join(CACHE, self.bname), mode=0o700, exist_ok=True)
            img = img.copy()
            img.setText("Thumb::URI", self._uri())
            img.setText("Thumb::MTime", str(int(self.mtime)))
            img.setText("Software", "LinExplorer")
            try:
                img.setText("Thumb::Size", str(os.path.getsize(self.path)))
            except OSError:
                pass
            f = self._cache_file()
            tmp = f + f".{os.getpid()}.tmp"
            if img.save(tmp, "PNG"):
                os.chmod(tmp, 0o600)
                os.replace(tmp, f)
        except OSError:
            pass

    def _make(self) -> QImage:
        px = self.px
        if self.kind == "image":
            r = QImageReader(self.path)
            r.setAutoTransform(True)
            sz = r.size()
            if sz.isValid() and (sz.width() > px or sz.height() > px):
                r.setScaledSize(sz.scaled(QSize(px, px), Qt.AspectRatioMode.KeepAspectRatio))
            img = r.read()
            return img
        if self.kind == "pdf":
            from PySide6.QtPdf import QPdfDocument
            doc = QPdfDocument()
            if doc.load(self.path) != QPdfDocument.Error.None_ or doc.pageCount() < 1:
                return QImage()
            ps = doc.pagePointSize(0)
            target = ps.toSize().scaled(QSize(px, px), Qt.AspectRatioMode.KeepAspectRatio)
            img = doc.render(0, target)
            doc.close()
            if not img.isNull():
                out = QImage(img.size(), QImage.Format.Format_ARGB32)
                out.fill(Qt.GlobalColor.white)
                from PySide6.QtGui import QPainter
                p = QPainter(out)
                p.drawImage(0, 0, img)
                p.end()
                return out
            return img
        if self.kind == "video":
            tmp = tempfile.mktemp(suffix=".png", prefix="lxthumb-")
            try:
                if util.which("ffmpegthumbnailer"):
                    subprocess.run(["ffmpegthumbnailer", "-i", self.path, "-o", tmp, "-s", str(px), "-f"],
                                   timeout=20, capture_output=True, env=util.clean_env())
                elif util.which("ffmpeg"):
                    subprocess.run(["ffmpeg", "-v", "quiet", "-ss", "5", "-i", self.path, "-frames:v", "1",
                                    "-vf", f"scale={px}:{px}:force_original_aspect_ratio=decrease", "-y", tmp],
                                   timeout=20, capture_output=True, env=util.clean_env())
                    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
                        subprocess.run(["ffmpeg", "-v", "quiet", "-i", self.path, "-frames:v", "1",
                                        "-vf", f"scale={px}:{px}:force_original_aspect_ratio=decrease", "-y", tmp],
                                       timeout=20, capture_output=True, env=util.clean_env())
                img = QImage(tmp)
                return img
            except (OSError, subprocess.TimeoutExpired):
                return QImage()
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return QImage()


class ThumbService(QObject):
    ready = Signal(str)

    def __init__(self):
        super().__init__()
        self._sig = _Signals()
        self._sig.done.connect(self._on_done)
        self._mem: OrderedDict = OrderedDict()
        self._pending: set = set()
        self._failed: set = set()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max(2, min(4, (os.cpu_count() or 2) - 1)))

    def get(self, path: str, mtime: float, size: int) -> QPixmap | None:
        """Cached pixmap or None (and schedules generation)."""
        k = kind_of(path)
        if k is None:
            return None
        px, bname = bucket_for(size)
        key = (path, px, int(mtime))
        pm = self._mem.get(key)
        if pm is not None:
            self._mem.move_to_end(key)
            return pm
        # a bigger cached one works too
        for bp, _ in BUCKETS:
            if bp > px:
                big = self._mem.get((path, bp, int(mtime)))
                if big is not None:
                    return big
        if key in self._failed:
            return None
        if key not in self._pending:
            if k == "video" and not (util.which("ffmpegthumbnailer") or util.which("ffmpeg")):
                self._failed.add(key)
                return None
            self._pending.add(key)
            self._mtimes[(path, px)] = int(mtime)
            self.pool.start(_Job(self, path, mtime, px, bname, k))
        return None

    _mtimes: dict = {}

    def _on_done(self, path, px, img: QImage):
        mt = self._mtimes.pop((path, px), 0)
        key = (path, px, mt)
        self._pending.discard(key)
        if img.isNull():
            self._failed.add(key)
            return
        self._mem[key] = QPixmap.fromImage(img)
        while len(self._mem) > 600:
            self._mem.popitem(last=False)
        self.ready.emit(path)

    def cancel_pending(self):
        self.pool.clear()
        self._pending.clear()
        self._mtimes.clear()


_svc: ThumbService | None = None


def service() -> ThumbService:
    global _svc
    if _svc is None:
        _svc = ThumbService()
    return _svc
