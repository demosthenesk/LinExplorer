"""Address row: Back/Forward orbs, breadcrumb address bar, search box."""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal, QStringListModel
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QKeySequence, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import (QAbstractButton, QCompleter, QFrame, QHBoxLayout, QLineEdit, QMenu, QSizePolicy,
                               QStyle, QStyleOptionFrame, QToolButton, QWidget)

from . import icons, locations, places, util
from .i18n import T
from .settings import settings


# =========================================================================== back / forward orbs
class NavOrbs(QWidget):
    back = Signal()
    forward = Signal()
    history = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(QSize(76, 30))
        self._can_back = False
        self._can_fwd = False
        self._hot = None
        self._down = None
        self.setMouseTracking(True)
        self.back_tip = T("back")
        self.fwd_tip = T("forward")

    def set_state(self, can_back: bool, can_fwd: bool, back_tip: str = "", fwd_tip: str = ""):
        self._can_back, self._can_fwd = can_back, can_fwd
        self.back_tip = back_tip or T("back")
        self.fwd_tip = fwd_tip or T("forward")
        self.update()

    def _rects(self):
        return QRectF(2, 3, 25, 25), QRectF(29, 3, 25, 25), QRectF(56, 9, 14, 14)

    def _hit(self, pos):
        b, f, h = self._rects()
        if b.contains(pos):
            return "b"
        if f.contains(pos):
            return "f"
        if h.adjusted(-2, -6, 4, 6).contains(pos):
            return "h"
        return None

    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h != self._hot:
            self._hot = h
            self.setToolTip(self.back_tip if h == "b" else self.fwd_tip if h == "f" else T("recent_pages") if h == "h" else "")
            self.update()

    def leaveEvent(self, e):
        self._hot = None
        self.update()

    def mousePressEvent(self, e):
        self._down = self._hit(e.position())
        self.update()
        if self._down == "h":
            self.history.emit(self.mapToGlobal(QPoint(0, self.height())))
            self._down = None

    def mouseReleaseEvent(self, e):
        h = self._hit(e.position())
        if h == self._down == "b" and self._can_back:
            self.back.emit()
        elif h == self._down == "f" and self._can_fwd:
            self.forward.emit()
        self._down = None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        b, f, h = self._rects()
        # the glass 'pill' behind both orbs
        pill = QPainterPath()
        pill.addRoundedRect(QRectF(0.5, 1.5, 72, 28), 14, 14)
        g = QLinearGradient(0, 0, 0, 30)
        g.setColorAt(0, QColor(255, 255, 255, 120))
        g.setColorAt(1, QColor(200, 215, 235, 90))
        p.setPen(QPen(QColor(150, 170, 200, 120), 1))
        p.setBrush(g)
        p.drawPath(pill)
        for r, enabled, key, left in ((b, self._can_back, "b", True), (f, self._can_fwd, "f", False)):
            self._orb(p, r, enabled, self._hot == key, self._down == key, left)
        # history arrow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(60, 70, 90) if self._hot == "h" else QColor(90, 100, 115))
        cx, cy = h.center().x(), h.center().y()
        p.drawPolygon(QPolygonF([QPointF(cx - 3.5, cy - 1.5), QPointF(cx + 3.5, cy - 1.5), QPointF(cx, cy + 2.5)]))

    def _orb(self, p: QPainter, r: QRectF, enabled: bool, hot: bool, down: bool, left: bool):
        if enabled:
            c1, c2, c3 = QColor(120, 180, 240), QColor(30, 100, 190), QColor(10, 60, 140)
            if hot:
                c1, c2, c3 = QColor(150, 205, 255), QColor(50, 130, 220), QColor(20, 80, 170)
            if down:
                c1, c2, c3 = QColor(90, 150, 220), QColor(20, 80, 160), QColor(10, 50, 120)
        else:
            c1, c2, c3 = QColor(235, 238, 242), QColor(200, 206, 214), QColor(170, 176, 186)
        g = QRadialGradient(r.center() + QPointF(0, r.height() * 0.35), r.width() * 0.75)
        g.setColorAt(0, c1)
        g.setColorAt(0.6, c2)
        g.setColorAt(1, c3)
        p.setPen(QPen(QColor(40, 70, 120) if enabled else QColor(160, 168, 178), 1))
        p.setBrush(g)
        p.drawEllipse(r)
        # highlight
        hl = QLinearGradient(r.topLeft(), QPointF(r.left(), r.center().y()))
        hl.setColorAt(0, QColor(255, 255, 255, 190 if enabled else 220))
        hl.setColorAt(1, QColor(255, 255, 255, 20))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(hl)
        p.drawEllipse(QRectF(r.left() + 3, r.top() + 1.5, r.width() - 6, r.height() * 0.5))
        # arrow
        p.setBrush(QColor(255, 255, 255) if enabled else QColor(150, 156, 165))
        cx, cy = r.center().x(), r.center().y()
        s = 6.5
        if left:
            pts = [QPointF(cx - s, cy), QPointF(cx - 0.5, cy - s + 0.5), QPointF(cx - 0.5, cy - 2.4),
                   QPointF(cx + s, cy - 2.4), QPointF(cx + s, cy + 2.4), QPointF(cx - 0.5, cy + 2.4),
                   QPointF(cx - 0.5, cy + s - 0.5)]
        else:
            pts = [QPointF(cx + s, cy), QPointF(cx + 0.5, cy - s + 0.5), QPointF(cx + 0.5, cy - 2.4),
                   QPointF(cx - s, cy - 2.4), QPointF(cx - s, cy + 2.4), QPointF(cx + 0.5, cy + 2.4),
                   QPointF(cx + 0.5, cy + s - 0.5)]
        p.drawPolygon(QPolygonF(pts))


# =========================================================================== breadcrumb
class _Crumb(QAbstractButton):
    """A crumb: text part and arrow part, painted Windows 7 style."""
    text_clicked = Signal()
    arrow_clicked = Signal()

    def __init__(self, text: str, has_arrow: bool, icon: QIcon | None = None, parent=None):
        super().__init__(parent)
        self.label = text
        self.has_arrow = has_arrow
        self.icon_ = icon
        self.only_arrow = False
        self.menu_open = False
        self._hot = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def text_w(self) -> int:
        if self.only_arrow:
            return 0
        w = self.fontMetrics().horizontalAdvance(self.label) + 12 if self.label else 0
        if self.icon_ is not None:
            w += 20
        return w

    def arrow_w(self) -> int:
        return 16 if self.has_arrow else 0

    def sizeHint(self):
        return QSize(self.text_w() + self.arrow_w(), 22)

    def _part(self, x) -> str:
        return "text" if x < self.text_w() else "arrow"

    def mouseMoveEvent(self, e):
        h = self._part(e.position().x())
        if h != self._hot:
            self._hot = h
            self.update()

    def enterEvent(self, e):
        self.parent().hovering = True
        self.parent().update()

    def leaveEvent(self, e):
        self._hot = None
        self.parent().hovering = False
        self.parent().update()
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._part(e.position().x()) == "arrow" and self.has_arrow:
            self.arrow_clicked.emit()
        else:
            self._pressed_text = True

    def mouseReleaseEvent(self, e):
        if getattr(self, "_pressed_text", False) and self._part(e.position().x()) == "text":
            self.text_clicked.emit()
        self._pressed_text = False

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(0, 1, -1, -2)
        tw = self.text_w()
        bar_hover = getattr(self.parent(), "hovering", False)
        if self._hot or self.menu_open:
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            g.setColorAt(0, QColor(234, 246, 253))
            g.setColorAt(1, QColor(197, 228, 247))
            p.setBrush(g)
            p.setPen(QPen(QColor(110, 180, 230), 1))
            p.drawRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5))
            if self.has_arrow and tw:
                p.drawLine(QPointF(tw + 0.5, r.top() + 1), QPointF(tw + 0.5, r.bottom()))
        elif bar_hover and self.has_arrow and tw:
            p.setPen(QPen(QColor(210, 222, 236), 1))
            p.drawLine(QPointF(tw + 0.5, r.top() + 3), QPointF(tw + 0.5, r.bottom() - 2))
        x = 6
        if self.icon_ is not None and not self.only_arrow:
            self.icon_.paint(p, QRect(4, (self.height() - 16) // 2, 16, 16))
            x = 24
        if tw and self.label:
            p.setPen(QColor(0, 0, 0))
            p.drawText(QRect(x, 0, tw - x, self.height()), Qt.AlignmentFlag.AlignVCenter, self.label)
        if self.has_arrow:
            cx = tw + 8
            cy = self.height() / 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(40, 40, 40))
            if self.menu_open:
                p.drawPolygon(QPolygonF([QPointF(cx - 3.5, cy - 1.5), QPointF(cx + 3.5, cy - 1.5), QPointF(cx, cy + 2.5)]))
            else:
                p.drawPolygon(QPolygonF([QPointF(cx - 1.5, cy - 3.5), QPointF(cx + 2.5, cy), QPointF(cx - 1.5, cy + 3.5)]))


class AddressBar(QFrame):
    navigate = Signal(str)
    refresh = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.location = ""
        self.hovering = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedHeight(26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self._crumbs: list[_Crumb] = []
        self._root = None
        self.edit = QLineEdit(self)
        self.edit.setFrame(False)
        self.edit.hide()
        self.edit.installEventFilter(self)
        self.edit.returnPressed.connect(self._commit)
        self._completer_model = QStringListModel(self)
        comp = QCompleter(self._completer_model, self)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.edit.setCompleter(comp)
        self.edit.textEdited.connect(self._update_completions)
        self.hist_btn = QToolButton(self)
        self.hist_btn.setAutoRaise(True)
        self.hist_btn.setArrowType(Qt.ArrowType.DownArrow)
        self.hist_btn.setFixedSize(16, 22)
        self.hist_btn.setToolTip(T("previous_locations"))
        self.hist_btn.setStyleSheet("QToolButton { border: none; } QToolButton::menu-indicator { image: none; }")
        self.hist_btn.clicked.connect(self._show_history)
        self.go_btn = QToolButton(self)
        self.go_btn.setAutoRaise(True)
        self.go_btn.setFixedSize(24, 24)
        self.go_btn.setIcon(icons.glyph_refresh())
        self.go_btn.setToolTip(T("refresh_tip"))
        self.go_btn.clicked.connect(self._go_clicked)
        self.go_btn.setStyleSheet("QToolButton { border: none; border-left: 1px solid #d9dfe7; }"
                                  "QToolButton:hover { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                                  " stop:0 #eaf6fd, stop:1 #c5e4f7); }")
        self.setStyleSheet("AddressBar { background: white; border: 1px solid #9aa7b8; border-radius: 1px; }")

    # ------------------------------------------------------------------ api
    def set_location(self, loc: str):
        self.location = loc
        if self.edit.isVisible():
            self._end_edit()
        self._rebuild()

    def start_edit(self, select_all: bool = True):
        self.edit.setText(self._edit_text())
        self.edit.show()
        self.edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        if select_all:
            self.edit.selectAll()
        for c in self._crumbs:
            c.hide()
        if self._root:
            self._root.hide()
        self.go_btn.setIcon(icons.glyph_go())
        self.go_btn.setToolTip(T("go_to", self.edit.text()))
        self._layout()

    def _edit_text(self) -> str:
        k = locations.kind(self.location)
        if k == "fs":
            return self.location
        if k == "net":
            return self.location[4:]
        if k == "zip":
            zp, inner = locations.ziputil.split(self.location)
            return os.path.join(zp, inner) if inner else zp
        if k == "search":
            return locations.display_name(self.location)
        return locations.display_name(self.location)

    def _end_edit(self):
        self.edit.hide()
        for c in self._crumbs:
            c.show()
        if self._root:
            self._root.show()
        self.go_btn.setIcon(icons.glyph_refresh())
        self.go_btn.setToolTip(T("refresh_tip"))
        self._layout()

    def _commit(self):
        text = self.edit.text()
        loc = locations.normalize(text, self.location if self.location.startswith("/") else None)
        self._end_edit()
        if loc is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, T("app_name"), T("cant_find", text))
            return
        hist = settings().get_json("address_history", [])
        hist = [h for h in hist if h != text]
        hist.insert(0, text)
        settings().set_json("address_history", hist[:25])
        self.navigate.emit(loc)

    def _go_clicked(self):
        if self.edit.isVisible():
            self._commit()
        else:
            self.refresh.emit()

    def _update_completions(self, text: str):
        t = os.path.expanduser(text)
        if not t.startswith("/"):
            self._completer_model.setStringList(settings().get_json("address_history", []))
            return
        d = t if t.endswith("/") else os.path.dirname(t)
        out = []
        try:
            for n in sorted(os.listdir(d), key=str.casefold):
                p = os.path.join(d, n)
                if os.path.isdir(p) and (settings().get("show_hidden") or not n.startswith(".")):
                    out.append(p)
        except OSError:
            pass
        self._completer_model.setStringList(out[:300])

    def _show_history(self):
        m = QMenu(self)
        for h in settings().get_json("address_history", [])[:20]:
            a = m.addAction(h)
            a.triggered.connect(lambda _=False, h=h: self._go_text(h))
        if m.isEmpty():
            a = m.addAction(T("no_history"))
            a.setEnabled(False)
        m.exec(self.mapToGlobal(QPoint(0, self.height())))

    def _go_text(self, text):
        loc = locations.normalize(text)
        if loc:
            self.navigate.emit(loc)

    # ------------------------------------------------------------------ crumbs
    def _rebuild(self):
        for c in self._crumbs:
            c.deleteLater()
        self._crumbs = []
        if self._root:
            self._root.deleteLater()
        self._root = _Crumb("", True, locations.icon(self.location), self)
        self._root.arrow_clicked.connect(lambda: self._root_menu(self._root))
        self._root.text_clicked.connect(lambda: self._root_menu(self._root))
        self._root.show()
        for loc, label in locations.crumbs(self.location):
            c = _Crumb(label, self._has_children(loc), None, self)
            c.text_clicked.connect(lambda loc=loc: self.navigate.emit(loc))
            c.arrow_clicked.connect(lambda c=c, loc=loc: self._crumb_menu(c, loc))
            c.show()
            self._crumbs.append(c)
        self._layout()

    def _has_children(self, loc: str) -> bool:
        k = locations.kind(loc)
        if k in ("trash", "search"):
            return False
        return True

    def resizeEvent(self, e):
        self._layout()

    def _layout(self):
        h = self.height()
        right = self.width() - self.go_btn.width() - 1
        self.go_btn.setGeometry(right, 1, self.go_btn.width(), h - 2)
        right -= self.hist_btn.width()
        self.hist_btn.setGeometry(right, 2, self.hist_btn.width(), h - 4)
        if self.edit.isVisible():
            self.edit.setGeometry(24, 2, right - 26, h - 4)
            return
        x = 1
        if self._root:
            self._root.only_arrow = False
            rw = self._root.sizeHint().width()
            self._root.setGeometry(x, 1, rw, h - 2)
            x += rw
        avail = right - x - 30
        widths = [c.sizeHint().width() for c in self._crumbs]
        # hide leading crumbs that don't fit (they go into the root menu like Explorer's «)
        start = 0
        while start < len(widths) - 1 and sum(widths[start:]) > avail:
            start += 1
        self._hidden = self._crumbs[:start]
        for c in self._hidden:
            c.setGeometry(0, 0, 0, 0)
        for c, w in zip(self._crumbs[start:], widths[start:]):
            c.setGeometry(x, 1, w, h - 2)
            x += w
        self._crumb_end = x

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.start_edit()

    def eventFilter(self, obj, ev):
        if obj is self.edit:
            if ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
                self._end_edit()
                return True
            if ev.type() == QEvent.Type.FocusOut:
                popup = self.edit.completer().popup()
                if not (popup and popup.isVisible()):
                    QTimer.singleShot(0, self._end_edit)
        return super().eventFilter(obj, ev)

    def _crumb_menu(self, crumb: _Crumb, loc: str):
        m = QMenu(self)
        crumb.menu_open = True
        crumb.update()
        children = self._children(loc)
        cur_next = None
        cl = locations.crumbs(self.location)
        for i, (l, _) in enumerate(cl):
            if l == loc and i + 1 < len(cl):
                cur_next = cl[i + 1][0]
        for child_loc, label, ic in children[:400]:
            a = m.addAction(ic, label)
            if child_loc == cur_next:
                f = a.font()
                f.setBold(True)
                a.setFont(f)
            a.triggered.connect(lambda _=False, l=child_loc: self.navigate.emit(l))
        if not children:
            a = m.addAction(T("empty_menu"))
            a.setEnabled(False)
        m.exec(crumb.mapToGlobal(QPoint(crumb.text_w(), crumb.height())))
        crumb.menu_open = False
        crumb.update()

    def _children(self, loc: str) -> list[tuple[str, str, QIcon]]:
        k = locations.kind(loc)
        out = []
        if k == "fs":
            try:
                names = sorted(os.listdir(loc), key=util.natural_key)
            except OSError:
                names = []
            sh = settings().get("show_hidden")
            for n in names:
                p = os.path.join(loc, n)
                if (sh or not util.is_hidden_name(n)) and os.path.isdir(p):
                    out.append((p, n, locations.icon(p)))
        elif k == "computer":
            for d in places.drives().list():
                if d.get("mount"):
                    out.append((d["mount"], places.drive_title(d), icons.drive(d.get("kind"))))
        elif k == "libraries":
            L = places.libraries()
            for lib in L.all():
                out.append(("shell:library:" + lib["name"], L.title(lib), icons.library(lib.get("type", ""))))
        elif k == "library":
            lib = places.libraries().get(locations.library_name(loc))
            for p in (lib or {}).get("locations", []):
                out.append((p, os.path.basename(p), locations.icon(p)))
        elif k == "zip":
            zp, inner = locations.ziputil.split(loc)
            for e in sorted(locations.ziputil.list_dir(zp, inner), key=lambda e: util.natural_key(e.name)):
                if e.is_dir:
                    out.append((locations.ziputil.make(zp, e.path), e.name, icons.folder()))
        elif k in ("network", "net"):
            pass
        return out

    def _root_menu(self, crumb: _Crumb):
        m = QMenu(self)
        crumb.menu_open = True
        crumb.update()
        hidden = getattr(self, "_hidden", [])
        if hidden:
            for c in hidden:
                idx = self._crumbs.index(c)
                loc = locations.crumbs(self.location)[idx][0]
                a = m.addAction(locations.icon(loc), c.label)
                a.triggered.connect(lambda _=False, l=loc: self.navigate.emit(l))
            m.addSeparator()
        x = places.xdg_dirs()
        entries = [(x["desktop"], T("desktop")), ("shell:libraries", T("libraries")),
                   (places.HOME, places.login_name()), ("shell:computer", T("computer")),
                   ("shell:network", T("network")), ("shell:trash", T("recycle_bin"))]
        for loc, label in entries:
            a = m.addAction(locations.icon(loc), label)
            a.triggered.connect(lambda _=False, l=loc: self.navigate.emit(l))
        m.exec(crumb.mapToGlobal(QPoint(0, crumb.height())))
        crumb.menu_open = False
        crumb.update()


# =========================================================================== search box
class SearchBox(QFrame):
    search = Signal(str)
    cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self.setStyleSheet("SearchBox { background: white; border: 1px solid #9aa7b8; border-radius: 1px; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 2, 0)
        lay.setSpacing(0)
        self.edit = QLineEdit(self)
        self.edit.setFrame(False)
        self.edit.setStyleSheet("QLineEdit { background: transparent; font-style: normal; }")
        lay.addWidget(self.edit, 1)
        self.btn = QToolButton(self)
        self.btn.setAutoRaise(True)
        self.btn.setIcon(icons.glyph_search(14))
        self.btn.setFixedSize(22, 22)
        self.btn.clicked.connect(self._btn)
        lay.addWidget(self.btn)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(450)
        self._timer.timeout.connect(self._fire)
        self.edit.textEdited.connect(self._edited)
        self.edit.returnPressed.connect(self._fire)
        self.edit.installEventFilter(self)
        self._suppress = False
        self.scope_name = ""

    def set_scope(self, name: str):
        self.scope_name = name
        self.edit.setPlaceholderText(T("search_x", name))

    def set_text(self, text: str):
        self._suppress = True
        self.edit.setText(text)
        self._suppress = False
        self._update_btn()

    def text(self) -> str:
        return self.edit.text()

    def _edited(self, _t):
        self._update_btn()
        self._timer.start()

    def _update_btn(self):
        self.btn.setIcon(icons.glyph_close(12) if self.edit.text() else icons.glyph_search(14))
        self.btn.setToolTip(T("clear_search") if self.edit.text() else T("search"))

    def _fire(self):
        self._timer.stop()
        t = self.edit.text().strip()
        if t:
            hist = settings().get_json("search_history", [])
            hist = [h for h in hist if h != t]
            hist.insert(0, t)
            settings().q.setValue("search_history", __import__("json").dumps(hist[:20]))
            self.search.emit(t)
        else:
            self.cleared.emit()

    def _btn(self):
        if self.edit.text():
            self.edit.clear()
            self._update_btn()
            self.cleared.emit()
        else:
            self.edit.setFocus()

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.Type.MouseButtonRelease and not self.edit.text():
            QTimer.singleShot(50, self._suggest)
        if obj is self.edit and ev.type() == QEvent.Type.FocusIn and \
                ev.reason() == Qt.FocusReason.ShortcutFocusReason and not self.edit.text():
            QTimer.singleShot(100, self._suggest)
        if obj is self.edit and ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
            if self.edit.text():
                self.edit.clear()
                self._update_btn()
                self.cleared.emit()
            return False
        return super().eventFilter(obj, ev)

    def _suggest(self):
        """Explorer's drop-down: recent searches and 'Add a search filter'."""
        if not self.edit.hasFocus() or self.edit.text():
            return
        m = QMenu(self)
        hist = settings().get_json("search_history", [])[:6]
        for h in hist:
            a = m.addAction(icons.glyph_search(12), h)
            a.triggered.connect(lambda _=False, h=h: (self.edit.setText(h), self._update_btn(), self._fire()))
        if hist:
            m.addSeparator()
        t = m.addAction(T("add_search_filter"))
        t.setEnabled(False)
        for key, label in (("kind", T("flt_kind")), ("datemodified", T("flt_datemodified")),
                           ("type", T("flt_type")), ("size", T("flt_size"))):
            sub = m.addMenu(label + ":")
            for v, vl in FILTER_VALUES[key]():
                a = sub.addAction(vl)
                a.triggered.connect(lambda _=False, k=key, v=v: self._add_filter(k, v))
        m.setMinimumWidth(self.width())
        m.popup(self.mapToGlobal(QPoint(0, self.height())))

    def _add_filter(self, key, value):
        t = self.edit.text().strip()
        if " " in value:
            value = f'"{value}"'
        self.edit.setText((t + " " if t else "") + f"{key}:{value}")
        self._update_btn()
        self.edit.setFocus()
        self._fire()


FILTER_VALUES = {
    "kind": lambda: [(k, T("kind_" + k)) for k in ("document", "folder", "picture", "music", "video", "program",
                                                   "compressed")],
    "datemodified": lambda: [(k, T("dm_" + k)) for k in ("today", "yesterday", "thisweek", "lastweek", "thismonth",
                                                        "lastmonth", "thisyear", "lastyear")],
    "type": lambda: [(".txt", ".txt"), (".pdf", ".pdf"), (".jpg", ".jpg"), (".png", ".png"), (".mp3", ".mp3"),
                     (".mp4", ".mp4"), (".odt", ".odt"), (".docx", ".docx"), (".zip", ".zip")],
    "size": lambda: [(k, T("sz_" + k)) for k in ("empty", "tiny", "small", "medium", "large", "huge", "gigantic")],
}
