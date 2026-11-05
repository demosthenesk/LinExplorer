"""Slide show (command bar 'Slide show'): full screen, 4 s per picture."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QWidget

from ..i18n import T


class SlideShow(QWidget):
    def __init__(self, paths: list[str], start: int = 0):
        super().__init__()
        self.paths = paths
        self.i = start
        self.pm = None
        self.paused = False
        self.setWindowTitle(T("cb_slide_show"))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.next)
        self.timer.start()
        self._load()

    def _load(self):
        r = QImageReader(self.paths[self.i])
        r.setAutoTransform(True)
        sz = r.size()
        scr = self.screen().size() if self.screen() else QSize(1920, 1080)
        if sz.isValid() and (sz.width() > scr.width() * 2 or sz.height() > scr.height() * 2):
            r.setScaledSize(sz.scaled(scr * 2, Qt.AspectRatioMode.KeepAspectRatio))
        img = r.read()
        self.pm = QPixmap.fromImage(img) if not img.isNull() else None
        self.update()

    def next(self):
        self.i = (self.i + 1) % len(self.paths)
        self._load()

    def prev(self):
        self.i = (self.i - 1) % len(self.paths)
        self._load()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        if self.pm:
            s = self.pm.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            if self.pm.width() < self.width() and self.pm.height() < self.height():
                s = self.pm.size()
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap((self.width() - s.width()) // 2, (self.height() - s.height()) // 2, s.width(), s.height(), self.pm)

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.close()
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self.next()
            self.timer.start()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_Backspace):
            self.prev()
            self.timer.start()
        elif k == Qt.Key.Key_P:
            self._toggle_pause()

    def _toggle_pause(self):
        self.paused = not self.paused
        self.timer.stop() if self.paused else self.timer.start()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.next()
            self.timer.start()
        elif e.button() == Qt.MouseButton.RightButton:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            m = QMenu(self)
            m.addAction(T("ss_play") if self.paused else T("ss_pause"), self._toggle_pause)
            m.addAction(T("ss_next"), self.next)
            m.addAction(T("ss_back"), self.prev)
            m.addSeparator()
            m.addAction(T("ss_exit"), self.close)
            m.exec(e.globalPosition().toPoint())
            self.setCursor(Qt.CursorShape.BlankCursor)
