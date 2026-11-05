"""Tabs: several folders in one Explorer window.

Each Tab owns its own FolderModel + ItemsView + history; MainWindow delegates its 'current folder'
attributes (model, view, loc, history...) to the current tab."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QTabBar, QToolButton, QWidget

from .i18n import T


class Tab:
    """Per-tab state."""
    __slots__ = ("model", "view", "loc", "history", "hist_pos", "select_after", "edit_after", "saved_sel",
                 "saved_cur", "saved_scroll", "restoring", "arrange", "uid", "opener")
    _next = 1

    def __init__(self, model, view):
        self.model = model
        self.view = view
        self.loc = ""
        self.history: list[str] = []
        self.hist_pos = -1
        self.select_after: list[str] | None = None
        self.edit_after: str | None = None
        self.saved_sel: list[str] = []
        self.saved_cur = ""
        self.saved_scroll = (0, 0)
        self.restoring = False
        self.arrange: dict = {}
        self.opener = None
        self.uid = Tab._next
        Tab._next += 1


class TabBar(QTabBar):
    """Closable, movable tab bar; middle-click closes, double-click on empty space opens a tab,
    and dragging files over a tab switches to it (and dropping drops into that folder)."""
    new_tab_requested = Signal()
    close_requested = Signal(int)
    context_requested = Signal(int, QPoint)
    drop_requested = Signal(int, object, object)          # index, QMimeData, Qt.DropAction

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setSelectionBehaviorOnRemove(QTabBar.SelectionBehavior.SelectPreviousTab)
        self.setIconSize(QSize(16, 16))
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setDrawBase(False)
        self.drop_action_for = None                         # callable(index, mime, mods) -> Qt.DropAction
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(450)
        self._hover_timer.timeout.connect(self._hover_switch)
        self._hover_idx = -1
        self.tabCloseRequested.connect(self.close_requested.emit)
        self.setStyleSheet("""
            QTabBar { background: transparent; }
            QTabBar::tab {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #e9f1fb, stop:1 #d3e2f4);
                border: 1px solid #9fb6d3; border-bottom: none;
                border-top-left-radius: 4px; border-top-right-radius: 4px;
                padding: 3px 6px 3px 8px; margin-right: 1px; margin-top: 2px;
                min-width: 60px; max-width: 220px; color: #1e395b;
            }
            QTabBar::tab:selected {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #f2f7fd);
                border-color: #7f9db9; margin-top: 0px;
            }
            QTabBar::tab:hover:!selected {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f4f9ff, stop:1 #dcebfc);
            }
        """)

    def tabSizeHint(self, i):
        s = super().tabSizeHint(i)
        return QSize(min(max(s.width(), 110), 230), s.height())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton:
            i = self.tabAt(e.position().toPoint())
            if i >= 0:
                self.close_requested.emit(i)
                return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.tabAt(e.position().toPoint()) < 0 and e.button() == Qt.MouseButton.LeftButton:
            self.new_tab_requested.emit()
            return
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        self.context_requested.emit(self.tabAt(e.pos()), e.globalPos())

    # -- drag & drop onto tabs
    def _action(self, i, md, mods):
        if i < 0 or self.drop_action_for is None:
            return Qt.DropAction.IgnoreAction
        return self.drop_action_for(i, md, mods)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        i = self.tabAt(e.position().toPoint())
        if i != self._hover_idx:
            self._hover_idx = i
            if i >= 0 and i != self.currentIndex():
                self._hover_timer.start()
            else:
                self._hover_timer.stop()
        act = self._action(i, e.mimeData(), e.modifiers())
        if act == Qt.DropAction.IgnoreAction:
            e.ignore(self.tabRect(i)) if i >= 0 else e.ignore()
            return
        e.setDropAction(act)
        e.accept(self.tabRect(i))

    def dragLeaveEvent(self, e):
        self._hover_timer.stop()
        self._hover_idx = -1

    def _hover_switch(self):
        if 0 <= self._hover_idx < self.count():
            self.setCurrentIndex(self._hover_idx)

    def dropEvent(self, e):
        self._hover_timer.stop()
        i = self.tabAt(e.position().toPoint())
        self._hover_idx = -1
        act = self._action(i, e.mimeData(), e.modifiers())
        if i < 0 or act == Qt.DropAction.IgnoreAction:
            e.ignore()
            return
        e.setDropAction(act)
        e.accept()
        self.drop_requested.emit(i, e.mimeData(), act)


class _PlusButton(QToolButton):
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(2, 4, -2, -1)
        if self.underMouse():
            p.setPen(QColor(127, 157, 185))
            p.setBrush(QColor(244, 249, 255))
            p.drawRoundedRect(r, 3, 3)
        p.setPen(QPen(QColor(30, 57, 91), 1.6))
        c = r.center()
        p.drawLine(c.x() - 4, c.y(), c.x() + 4, c.y())
        p.drawLine(c.x(), c.y() - 4, c.x(), c.y() + 4)


class TabStrip(QWidget):
    """The tab bar plus a 'New tab' button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 2, 6, 0)
        h.setSpacing(2)
        self.bar = TabBar()
        h.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignBottom)
        self.plus = _PlusButton()
        self.plus.setFixedSize(26, 24)
        self.plus.setToolTip(T("tab_new_tip"))
        self.plus.setAutoRaise(True)
        self.plus.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.plus.clicked.connect(self.bar.new_tab_requested.emit)
        h.addWidget(self.plus, 0, Qt.AlignmentFlag.AlignBottom)
        h.addStretch(1)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.bar.new_tab_requested.emit()
