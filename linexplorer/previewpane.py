"""Preview pane (Alt+P): images, text, PDF, audio/video, fonts, HTML/Markdown, archives."""
from __future__ import annotations

import os
import zipfile

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QFont, QFontDatabase, QImageReader, QMovie, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QPlainTextEdit, QSizePolicy, QSlider,
                               QStackedWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from . import icons, mime, thumbs, util
from .i18n import T
from .settings import settings

_TEXT_EXT = {".txt", ".log", ".md", ".py", ".sh", ".c", ".h", ".cpp", ".hpp", ".js", ".ts", ".json", ".xml", ".ini",
             ".conf", ".cfg", ".yaml", ".yml", ".toml", ".csv", ".bas", ".java", ".rs", ".go", ".css", ".sql", ".bat",
             ".desktop", ".srt", ".tex", ".rb", ".pl", ".php", ".lua", ".nfo", ".diz", ".rst"}


class _ImageLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._pm = None

    def set_image(self, pm: QPixmap | None):
        self._pm = pm
        self._fit()

    def resizeEvent(self, e):
        self._fit()

    def _fit(self):
        if self._pm is None or self._pm.isNull():
            self.clear()
            return
        sz = self.size() - QSize(16, 16)
        if self._pm.width() > sz.width() or self._pm.height() > sz.height():
            self.setPixmap(self._pm.scaled(sz, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
        else:
            self.setPixmap(self._pm)


class _Media(QWidget):
    def __init__(self):
        super().__init__()
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.video = QVideoWidget()
        self.video.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self.video, 1)
        self.cover = QLabel()
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.cover, 1)
        row = QHBoxLayout()
        self.play = QToolButton()
        self.play.setIcon(icons.theme("media-playback-start", fallback="file"))
        self.play.clicked.connect(self.toggle)
        row.addWidget(self.play)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderMoved.connect(lambda v: self.player.setPosition(v))
        row.addWidget(self.slider, 1)
        self.time = QLabel("00:00")
        row.addWidget(self.time)
        lay.addLayout(row)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, d))
        self.player.positionChanged.connect(self._pos)
        self.player.playbackStateChanged.connect(self._state)

    def load(self, path: str, is_video: bool):
        self.player.stop()
        self.video.setVisible(is_video)
        self.cover.setVisible(not is_video)
        if not is_video:
            self.cover.setPixmap(icons.for_mime(mime.mime_for(path)).pixmap(96, 96))
        self.player.setSource(QUrl.fromLocalFile(path))

    def toggle(self):
        from PySide6.QtMultimedia import QMediaPlayer
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self):
        self.player.stop()
        self.player.setSource(QUrl())

    def _pos(self, p):
        if not self.slider.isSliderDown():
            self.slider.setValue(p)
        self.time.setText(util.fmt_duration(p / 1000)[3:] if p < 3600000 else util.fmt_duration(p / 1000))

    def _state(self, s):
        from PySide6.QtMultimedia import QMediaPlayer
        self.play.setIcon(icons.theme("media-playback-pause" if s == QMediaPlayer.PlaybackState.PlayingState
                                      else "media-playback-start", fallback="file"))


class PreviewPane(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(160)
        self.setStyleSheet("PreviewPane { background: white; border-left: 1px solid #d6e5f5; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)
        self.msg = QLabel(T("select_file_preview"))
        self.msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.msg.setStyleSheet("color: #6d6d6d;")
        self.msg.setWordWrap(True)
        self.image = _ImageLabel()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFrameShape(QFrame.Shape.NoFrame)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.rich = QTextBrowser()
        self.rich.setFrameShape(QFrame.Shape.NoFrame)
        self.rich.setOpenLinks(False)
        self.listw = QListWidget()
        self.listw.setFrameShape(QFrame.Shape.NoFrame)
        self.font_lbl = QLabel()
        self.font_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.font_lbl.setWordWrap(True)
        self.font_lbl.setContentsMargins(12, 12, 12, 12)
        for w in (self.msg, self.image, self.text, self.rich, self.listw, self.font_lbl):
            self.stack.addWidget(w)
        self.pdf = None
        self.media = None
        self._movie = None
        self._path = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._load)
        self._pending = None

    def _show_msg(self, text):
        self.msg.setText(text)
        self.stack.setCurrentWidget(self.msg)

    def clear(self):
        self._stop_media()
        self._path = None
        self._show_msg(T("select_file_preview"))

    def show_items(self, items: list):
        if len(items) != 1:
            self._pending = None
            self._timer.stop()
            self._stop_media()
            self._path = None
            self._show_msg(T("select_file_preview") if not items else T("n_items_selected", len(items)))
            return
        self._pending = items[0]
        self._timer.start()

    def _stop_media(self):
        if self.media is not None:
            self.media.stop()
        if self._movie is not None:
            self._movie.stop()
            self._movie = None

    def _load(self):
        it = self._pending
        if it is None:
            return
        path = it.path if it.kind in ("file", "trash") else None
        if it.kind == "zipfile":
            self._show_msg(T("no_preview"))
            return
        if path == self._path and path is not None:
            return
        self._stop_media()
        self._path = path
        if it.is_dir or not path:
            self._show_msg(T("no_preview"))
            return
        if not settings().get("preview_handlers"):
            self._show_msg(T("no_preview"))
            return
        mt = it.mime
        n = mt.name()
        ext = os.path.splitext(path)[1].lower()
        try:
            if n.startswith("image/"):
                if n == "image/gif":
                    self._movie = QMovie(path)
                    self.image.setMovie(self._movie)
                    self._movie.start()
                    self.stack.setCurrentWidget(self.image)
                    return
                r = QImageReader(path)
                r.setAutoTransform(True)
                sz = r.size()
                if sz.isValid() and (sz.width() > 2400 or sz.height() > 2400):
                    r.setScaledSize(sz.scaled(QSize(2400, 2400), Qt.AspectRatioMode.KeepAspectRatio))
                img = r.read()
                if img.isNull():
                    self._show_msg(T("no_preview"))
                    return
                self.image.set_image(QPixmap.fromImage(img))
                self.stack.setCurrentWidget(self.image)
                return
            if n == "application/pdf":
                from PySide6.QtPdf import QPdfDocument
                from PySide6.QtPdfWidgets import QPdfView
                if self.pdf is None:
                    self.pdf_doc = QPdfDocument(self)
                    self.pdf = QPdfView(self)
                    self.pdf.setDocument(self.pdf_doc)
                    self.pdf.setPageMode(QPdfView.PageMode.MultiPage)
                    self.pdf.setZoomMode(QPdfView.ZoomMode.FitToWidth)
                    self.stack.addWidget(self.pdf)
                self.pdf_doc.load(path)
                self.stack.setCurrentWidget(self.pdf)
                return
            if n.startswith("audio/") or n.startswith("video/") or n == "application/ogg":
                try:
                    if self.media is None:
                        self.media = _Media()
                        self.stack.addWidget(self.media)
                    self.media.load(path, n.startswith("video/"))
                    self.stack.setCurrentWidget(self.media)
                except Exception:
                    self._show_msg(T("no_preview"))
                return
            if n.startswith("font/") or ext in (".ttf", ".otf", ".woff", ".woff2"):
                fid = QFontDatabase.addApplicationFont(path)
                fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
                if not fams:
                    self._show_msg(T("no_preview"))
                    return
                fam = fams[0]
                html = f"<div style='font-family:\"{fam}\"'>"
                html += f"<p style='font-size:20pt'>{fam}</p>"
                for s in (12, 18, 24, 36, 48):
                    html += f"<p style='font-size:{s}pt;margin:2px'>The quick brown fox · Ξεσκεπάζω την ψυχοφθόρα</p>"
                html += "</div>"
                self.font_lbl.setText(html)
                self.stack.setCurrentWidget(self.font_lbl)
                return
            if n in ("text/markdown", "text/x-markdown") or ext == ".md":
                self.rich.setMarkdown(self._read(path))
                self.stack.setCurrentWidget(self.rich)
                return
            if n == "text/html" or ext in (".htm", ".html"):
                self.rich.setHtml(self._read(path))
                self.stack.setCurrentWidget(self.rich)
                return
            from . import ziputil
            if ziputil.fmt_by_name(path) and ziputil.is_archive_path(path):
                self.listw.clear()
                for name in ziputil.read_names(path):
                    self.listw.addItem(name)
                self.stack.setCurrentWidget(self.listw)
                return
            if n.startswith("text/") or mt.inherits("text/plain") or ext in _TEXT_EXT or n in (
                    "application/json", "application/xml", "application/x-shellscript", "application/x-desktop",
                    "application/javascript", "application/x-yaml", "application/toml", "application/sql"):
                f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
                self.text.setFont(f)
                self.text.setPlainText(self._read(path))
                self.stack.setCurrentWidget(self.text)
                return
        except Exception:
            pass
        # last resort: a big thumbnail if one exists
        self._show_msg(T("no_preview"))

    @staticmethod
    def _read(path: str, limit: int = 512 * 1024) -> str:
        try:
            with open(path, "rb") as f:
                data = f.read(limit)
        except OSError:
            return ""
        for enc in ("utf-8", "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "", "cp1253", "latin-1"):
            if not enc:
                continue
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("latin-1", "replace")

    def hideEvent(self, e):
        self._stop_media()
        super().hideEvent(e)
