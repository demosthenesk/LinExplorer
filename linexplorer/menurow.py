"""Classic menu bar (File Edit View Tools Help) that sits under the address row like in
Windows 7. A light QToolButton row instead of QMenuBar, which misbehaves when placed in the
middle of a layout."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QToolButton


class MenuRow(QFrame):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MenuRow")
        self.setStyleSheet(
            "#MenuRow { background: #f0f0f0; border-bottom: 1px solid #d8d8d8; }"
            "#MenuRow QToolButton { border: 1px solid transparent; padding: 2px 7px; background: transparent; }"
            "#MenuRow QToolButton:hover, #MenuRow QToolButton:focus, #MenuRow QToolButton:pressed"
            " { background: #d5e7f8; border: 1px solid #aecff7; }"
            "#MenuRow QToolButton::menu-indicator { image: none; width: 0; }")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(2, 1, 2, 1)
        self.lay.setSpacing(0)
        self.lay.addStretch(1)
        self.buttons: list[QToolButton] = []
        self.menus: list[QMenu] = []
        self._open_idx = -1

    def addMenu(self, title: str) -> QMenu:
        m = QMenu(title, self)
        b = QToolButton(self)
        b.setText(title)
        b.setMenu(m)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        b.installEventFilter(self)
        m.installEventFilter(self)
        idx = len(self.buttons)
        m.aboutToShow.connect(lambda i=idx: setattr(self, "_open_idx", i))
        m.aboutToHide.connect(lambda: QTimer.singleShot(0, self._menu_hidden))
        self.lay.insertWidget(idx, b)
        self.buttons.append(b)
        self.menus.append(m)
        return m

    def actions(self):
        return self.buttons

    def mnemonics(self) -> list[tuple[str, int]]:
        out = []
        for i, b in enumerate(self.buttons):
            t = b.text()
            k = t.find("&")
            if 0 <= k < len(t) - 1:
                out.append((t[k + 1], i))
        return out

    def open_menu(self, i: int):
        if 0 <= i < len(self.buttons):
            b = self.buttons[i]
            b.setFocus()
            QTimer.singleShot(0, b.showMenu)

    def focus_first(self):
        if self.buttons:
            self.buttons[0].setFocus(Qt.FocusReason.ShortcutFocusReason)

    def any_menu_open(self) -> bool:
        return any(m.isVisible() for m in self.menus)

    def _menu_hidden(self):
        if not self.any_menu_open() and not any(b.hasFocus() for b in self.buttons):
            self.closed.emit()

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.KeyPress:
            k = ev.key()
            if obj in self.menus:
                i = self.menus.index(obj)
                active = obj.activeAction()
                if k == Qt.Key.Key_Right and not (active and active.menu()):
                    obj.hide()
                    self.open_menu((i + 1) % len(self.menus))
                    return True
                if k == Qt.Key.Key_Left:
                    obj.hide()
                    self.open_menu((i - 1) % len(self.menus))
                    return True
            elif obj in self.buttons:
                i = self.buttons.index(obj)
                if k == Qt.Key.Key_Right:
                    self.buttons[(i + 1) % len(self.buttons)].setFocus()
                    return True
                if k == Qt.Key.Key_Left:
                    self.buttons[(i - 1) % len(self.buttons)].setFocus()
                    return True
                if k in (Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                    obj.showMenu()
                    return True
                if k == Qt.Key.Key_Escape:
                    self.closed.emit()
                    return True
                txt = ev.text().lower()
                for ch, j in self.mnemonics():
                    if txt and ch.lower() == txt:
                        self.open_menu(j)
                        return True
        elif ev.type() == QEvent.Type.FocusOut and obj in self.buttons:
            QTimer.singleShot(120, self._menu_hidden)
        return False
