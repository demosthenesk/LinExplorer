"""The command bar (Organize, context buttons, Views, Preview pane, Help)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

from . import icons
from .i18n import T
from .itemsview import MODES

VIEW_ORDER = ["xlarge", "large", "medium", "small", "list", "details", "tiles", "content"]


class CmdButton(QToolButton):
    """Flat Windows 7 command bar button; optional drop-down arrow, optional split."""

    def __init__(self, text: str, icon: QIcon | None = None, dropdown: bool = False, split: bool = False, parent=None):
        super().__init__(parent)
        self.setText(text)
        if icon is not None:
            self.setIcon(icon)
        self.dropdown = dropdown
        self.split = split
        self.checked_look = False
        self._hot = False
        self._hot_arrow = False
        self._down = False
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.menu_builder = None
        self.on_click = None

    def sizeHint(self):
        fm = self.fontMetrics()
        w = 12 + (fm.horizontalAdvance(self.text()) if self.text() else 0)
        if not self.icon().isNull():
            w += 18 if self.text() else 16
        if self.dropdown:
            w += 14
        return QSize(max(w, 26), 28)

    def arrow_x(self) -> int:
        return self.width() - 14

    def enterEvent(self, e):
        self._hot = True
        self.update()

    def leaveEvent(self, e):
        self._hot = False
        self._hot_arrow = False
        self.update()

    def mouseMoveEvent(self, e):
        ha = self.split and e.position().x() >= self.arrow_x() - 2
        if ha != self._hot_arrow:
            self._hot_arrow = ha
            self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._down = True
        self.update()
        on_arrow = self.split and e.position().x() >= self.arrow_x() - 2
        if self.menu_builder is not None and (not self.split or on_arrow):
            m = QMenu(self)
            r = self.menu_builder(m)
            if isinstance(r, QWidget) and not isinstance(r, QMenu):
                self._down = False
                self.update()
                return
            if not m.isEmpty():
                m.exec(self.mapToGlobal(QPoint(0, self.height())))
            self._down = False
            self._hot = False
            self.update()
            return

    def mouseReleaseEvent(self, e):
        was = self._down
        self._down = False
        self.update()
        if was and self.rect().contains(e.position().toPoint()) and self.on_click is not None:
            on_arrow = self.split and e.position().x() >= self.arrow_x() - 2
            if not on_arrow and (self.split or self.menu_builder is None):
                self.on_click()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 2.5, -0.5, -2.5)
        if self._hot or self._down or self.checked_look:
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            if self._down or self.checked_look:
                g.setColorAt(0, QColor(194, 218, 240, 230))
                g.setColorAt(1, QColor(170, 200, 232, 230))
            else:
                g.setColorAt(0, QColor(255, 255, 255, 200))
                g.setColorAt(0.5, QColor(240, 246, 252, 170))
                g.setColorAt(1, QColor(214, 230, 246, 170))
            p.setBrush(g)
            p.setPen(QPen(QColor(130, 160, 200, 200), 1))
            p.drawRoundedRect(r, 3, 3)
            if self.split:
                x = self.arrow_x() - 2.5
                p.setPen(QPen(QColor(130, 160, 200, 160), 1))
                p.drawLine(QPointF(x, r.top() + 2), QPointF(x, r.bottom() - 2))
        x = 6
        if not self.icon().isNull():
            self.icon().paint(p, QRect(x, (self.height() - 16) // 2, 16, 16),
                              mode=QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled)
            x += 20 if self.text() else 16
        if self.text():
            p.setPen(QColor(30, 57, 91) if self.isEnabled() else QColor(150, 160, 175))
            tw = self.fontMetrics().horizontalAdvance(self.text())
            p.drawText(QRect(x, 0, tw + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        if self.dropdown:
            cx = self.arrow_x() + 6
            cy = self.height() / 2 + 1
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(50, 70, 100))
            p.drawPolygon(QPolygonF([QPointF(cx - 3, cy - 1.5), QPointF(cx + 3, cy - 1.5), QPointF(cx, cy + 1.5)]))


class ViewsPopup(QFrame):
    """The slider popup of the Views button."""
    chosen = Signal(str)

    def __init__(self, current: str, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.current = current
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("ViewsPopup { background: #f0f0f0; border: 1px solid #979797; }")
        self._labels = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 6, 10, 6)
        lay.setSpacing(0)
        self._icons = {
            "xlarge": icons.theme("view-grid-symbolic", "view-grid", "folder"),
            "large": icons.theme("view-grid-symbolic", "view-grid", "folder"),
            "medium": icons.theme("view-grid-symbolic", "view-grid", "folder"),
            "small": icons.theme("view-list-symbolic", "view-list", "folder"),
            "list": icons.theme("view-list-symbolic", "view-list", "folder"),
            "details": icons.theme("view-list-details-symbolic", "view-list-details", "view-list", "folder"),
            "tiles": icons.theme("view-list-icons-symbolic", "view-grid", "folder"),
            "content": icons.theme("view-paged-symbolic", "view-list", "folder"),
        }
        for m in VIEW_ORDER:
            b = QToolButton(self)
            b.setText(T("view_" + m))
            b.setIcon(self._icons[m])
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setAutoRaise(True)
            b.setMinimumWidth(170)
            b.setFixedHeight(26 if m in ("xlarge", "large", "medium", "small") else 24)
            b.setStyleSheet("QToolButton { text-align: left; border: 1px solid transparent; padding-left: 4px; }"
                            "QToolButton:hover { background: #dbeaf9; border: 1px solid #a8c8ea; border-radius: 2px; }"
                            + ("QToolButton { font-weight: bold; }" if m == current else ""))
            b.clicked.connect(lambda _=False, m=m: self._pick(m))
            lay.addWidget(b)
            self._labels.append(b)
        self._drag = False

    def _pick(self, m):
        self.chosen.emit(m)
        self.close()

    def _track(self) -> tuple[int, int]:
        a = self._labels[0].geometry().center().y()
        b = self._labels[-1].geometry().center().y()
        return a, b

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a, b = self._track()
        x = 18
        p.setPen(QPen(QColor(150, 150, 150), 1))
        p.setBrush(QColor(230, 230, 230))
        p.drawRoundedRect(QRectF(x - 2, a - 4, 4, b - a + 8), 2, 2)
        for i, lab in enumerate(self._labels):
            y = lab.geometry().center().y()
            p.setPen(QPen(QColor(120, 120, 120), 1))
            p.drawLine(QPointF(x + 5, y), QPointF(x + 9, y))
        cur = VIEW_ORDER.index(self.current) if self.current in VIEW_ORDER else 5
        y = self._labels[cur].geometry().center().y()
        g = QLinearGradient(0, y - 6, 0, y + 6)
        g.setColorAt(0, QColor(250, 250, 250))
        g.setColorAt(1, QColor(200, 210, 225))
        p.setBrush(g)
        p.setPen(QPen(QColor(90, 110, 140), 1))
        p.drawPolygon(QPolygonF([QPointF(x - 8, y - 5), QPointF(x + 3, y - 5), QPointF(x + 8, y),
                                 QPointF(x + 3, y + 5), QPointF(x - 8, y + 5)]))

    def mousePressEvent(self, e):
        if e.position().x() < 34:
            self._drag = True
            self._move_to(e.position().y())
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag:
            self._move_to(e.position().y())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.chosen.emit(self.current)
            self.close()

    def _move_to(self, y):
        best = min(range(len(self._labels)), key=lambda i: abs(self._labels[i].geometry().center().y() - y))
        if VIEW_ORDER[best] != self.current:
            self.current = VIEW_ORDER[best]
            self.chosen.emit(self.current)
            self.update()


class CommandBar(QFrame):
    view_chosen = Signal(str)
    view_cycle = Signal()
    preview_toggled = Signal()
    help_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.setObjectName("CommandBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(1)
        self.left = QHBoxLayout()
        self.left.setSpacing(1)
        lay.addLayout(self.left)
        lay.addStretch(1)
        self.views = CmdButton("", icons.glyph_views(), dropdown=True, split=True)
        self.views.setToolTip(T("change_view_tip"))
        self.views.on_click = self.view_cycle.emit
        self.views.menu_builder = self._views_menu
        self.preview = CmdButton("", icons.glyph_preview())
        self.preview.setToolTip(T("show_preview_tip"))
        self.preview.on_click = self.preview_toggled.emit
        self.help = CmdButton("", icons.glyph_help())
        self.help.setToolTip(T("get_help"))
        self.help.on_click = self.help_clicked.emit
        for b in (self.views, self.preview, self.help):
            lay.addWidget(b)
        self.current_view = "details"
        self._buttons: list[CmdButton] = []

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect()
        g = QLinearGradient(0, 0, 0, r.height())
        g.setColorAt(0, QColor(245, 249, 253))
        g.setColorAt(0.45, QColor(228, 237, 248))
        g.setColorAt(0.5, QColor(219, 231, 245))
        g.setColorAt(1, QColor(205, 220, 239))
        p.fillRect(r, g)
        p.setPen(QColor(255, 255, 255, 180))
        p.drawLine(0, 0, r.width(), 0)
        p.setPen(QColor(160, 180, 205))
        p.drawLine(0, r.height() - 1, r.width(), r.height() - 1)

    def set_buttons(self, specs: list[dict]):
        for b in self._buttons:
            self.left.removeWidget(b)
            b.deleteLater()
        self._buttons = []
        for s in specs:
            b = CmdButton(s["text"], s.get("icon"), dropdown=bool(s.get("menu")), split=bool(s.get("split")))
            if s.get("tip"):
                b.setToolTip(s["tip"])
            if s.get("menu"):
                b.menu_builder = s["menu"]
            if s.get("click"):
                b.on_click = s["click"]
            b.setEnabled(s.get("enabled", True))
            self.left.addWidget(b)
            self._buttons.append(b)

    def set_preview_on(self, on: bool):
        self.preview.checked_look = on
        self.preview.setToolTip(T("hide_preview_tip") if on else T("show_preview_tip"))
        self.preview.update()

    def _views_menu(self, menu: QMenu):
        pop = ViewsPopup(self.current_view, self)
        pop.chosen.connect(self.view_chosen)
        pop.adjustSize()
        pos = self.views.mapToGlobal(QPoint(self.views.width() - pop.width(), self.views.height()))
        pop.move(pos)
        pop.show()
        return pop
