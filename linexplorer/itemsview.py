"""One item view for all eight Windows 7 view modes, with groups.

Extra large / Large / Medium / Small icons, List, Details, Tiles and Content are laid
out and painted here; Details adds a QHeaderView on top.  Grouping ('Group by'),
collapsible group headers, rubber-band selection, check boxes, type-to-select,
in-place rename and drag & drop all work in every mode.
"""
from __future__ import annotations

import math

from PySide6.QtCore import (QItemSelection, QItemSelectionModel, QMimeData, QModelIndex, QPoint, QPointF, QRect,
                            QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QBrush, QColor, QDrag, QFont, QFontMetrics, QIcon, QKeySequence, QLinearGradient,
                           QPainter, QPainterPath, QPalette, QPen, QPixmap, QRegion, QTextLayout, QTextOption)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHeaderView, QLineEdit, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem)

from . import icons, thumbs, util
from .i18n import T
from .model import COLUMNS, GhostRole, ItemRole
from .settings import settings

MODES = ["xlarge", "large", "medium", "small", "list", "details", "tiles", "content"]
WHEEL_ORDER = ["content", "tiles", "details", "list", "small", "medium", "large", "xlarge"]

ICON_PX = {"xlarge": 256, "large": 96, "medium": 48, "small": 16, "list": 16, "details": 16, "tiles": 48,
           "content": 48}

SEL_FILL = QColor(204, 232, 255)
SEL_BORDER = QColor(153, 209, 255)
SEL_FILL_NOFOCUS = QColor(217, 217, 217)
SEL_BORDER_NOFOCUS = QColor(217, 217, 217)
HOT_FILL = QColor(229, 243, 251)
HOT_BORDER = QColor(112, 192, 231)
GROUP_TEXT = QColor(30, 57, 145)
GROUP_LINE = QColor(226, 226, 226)
GRAY_TEXT = QColor(109, 109, 109)
HILITE = QColor(255, 236, 140)

GROUP_H = 28
MARGIN = 6


class _Cell:
    __slots__ = ("rect", "row")

    def __init__(self, rect, row):
        self.rect = rect
        self.row = row


class RenameDelegate(QStyledItemDelegate):
    """Line edit placed over the item label; selects the name without its extension."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def createEditor(self, parent, option, index):
        e = QLineEdit(parent)
        e.setFrame(True)
        e.setStyleSheet("QLineEdit { border: 1px solid #5d6e83; padding: 0 1px; background: white; }")
        if self.view.mode in ("xlarge", "large", "medium"):
            e.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        return e

    def setEditorData(self, editor, index):
        name = index.data(Qt.ItemDataRole.EditRole) or ""
        editor.setText(name)
        it = index.data(ItemRole)
        if it is not None and not it.is_dir:
            base, ext = util.split_ext(name)
            if ext and base:
                QTimer.singleShot(0, lambda: editor.setSelection(0, len(base)))
                return
        QTimer.singleShot(0, editor.selectAll)

    def updateEditorGeometry(self, editor, option, index):
        r = self.view.label_rect(index)
        fm = editor.fontMetrics()
        h = fm.height() + 6
        if self.view.mode in ("xlarge", "large", "medium"):
            w = max(r.width() + 16, min(260, fm.horizontalAdvance(editor.text()) + 20))
            editor.setGeometry(QRect(r.center().x() - w // 2, r.top() - 1, w, h))
        else:
            w = max(r.width(), min(500, fm.horizontalAdvance(editor.text()) + 24))
            editor.setGeometry(QRect(r.left() - 3, r.center().y() - h // 2, w, h))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text(), Qt.ItemDataRole.EditRole)


class ItemsView(QAbstractItemView):
    open_requested = Signal(QModelIndex)
    context_requested = Signal(QPoint, QModelIndex)
    drop_requested = Signal(object, object, object, bool)     # QMimeData, target item (or None), action, right_drag
    mode_changed = Signal(str)
    header_context = Signal(QPoint)
    header_filter = Signal(str, QPoint)
    columns_resized = Signal()
    sort_clicked = Signal(str)
    back_forward = Signal(int)
    open_new_tab = Signal(QModelIndex)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "details"
        self._cells: list[_Cell] = []
        self._headers: list[tuple] = []
        self._content = QSize(0, 0)
        self._collapsed: set[str] = set()
        self._hover = -1
        self._hover_header = -1
        self._band: QRect | None = None
        self._press_pos: QPoint | None = None
        self._right_press: QPoint | None = None
        self._drop_row = -1
        self._list_rows = 1
        self._list_colw = 200
        self._dirty = True
        self.empty_text = ""
        self.mime_provider = None          # callable(rows) -> QMimeData
        self.drop_action_for = None        # callable(mime, target_item, modifiers) -> Qt.DropAction
        self.setItemDelegate(RenameDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed |
                             QAbstractItemView.EditTrigger.SelectedClicked)
        self.setMouseTracking(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setFrameShape(QAbstractItemView.Shape.NoFrame)
        self.setAutoScroll(True)
        self.viewport().setBackgroundRole(QPalette.ColorRole.Base)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.header = QHeaderView(Qt.Orientation.Horizontal, self)
        self.header.setSectionsClickable(True)
        self.header.setSectionsMovable(True)
        self.header.setHighlightSections(False)
        self.header.setStretchLastSection(False)
        self.header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.header.setSortIndicatorShown(True)
        self.header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.header.customContextMenuRequested.connect(lambda p: self.header_context.emit(self.header.mapToGlobal(p)))
        self.header.sectionResized.connect(self._on_section_resized)
        self.header.sectionMoved.connect(self._on_section_moved)
        self.header.sectionClicked.connect(self._on_section_clicked)
        self.header.setFixedHeight(max(24, self.fontMetrics().height() + 10))
        self.header.setStyleSheet(
            "QHeaderView::section { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:1 #f3f5f8);"
            " border: none; border-right: 1px solid #e3e8ee; border-bottom: 1px solid #d5dfe5; padding-left: 6px;"
            " color: #4c607a; }"
            "QHeaderView::section:hover { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f3f9fd, stop:1 #dbeffa);"
            " border-right: 1px solid #c1e3f6; }")
        self.header.hide()
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(0)
        self._relayout_timer.timeout.connect(self._do_layout)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(60)
        self._thumb_timer.timeout.connect(self.viewport().update)
        thumbs.service().ready.connect(lambda _p: self._thumb_timer.start())
        self._hover_timer = QTimer(self)        # spring-loaded folders while dragging
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(1100)
        self.setFont(QApplication.font())
        self._bold = QFont(self.font())
        self._big = QFont(self.font())
        self._big.setPointSizeF(self.font().pointSizeF() * 1.0)

    # ------------------------------------------------------------------ model plumbing
    def setModel(self, model):
        super().setModel(model)
        self.header.setModel(model)
        self._anchor_path = None
        self._extending = False
        self.selectionModel().currentChanged.connect(self._current_changed)
        model.modelReset.connect(self._after_reset)
        model.layoutChanged.connect(self.schedule_layout)
        model.dataChanged.connect(lambda *a: self.viewport().update())
        self._sync_header()
        self.schedule_layout()

    def m(self):
        return self.model()

    def _after_reset(self):
        self._hover = -1
        self._sync_header()
        self.schedule_layout()

    def _sync_header(self):
        model = self.model()
        if model is None:
            return
        self.header.blockSignals(True)
        for i, col in enumerate(model.columns):
            self.header.resizeSection(i, model.width_of(col))
        if model.sort_col in model.columns:
            self.header.setSortIndicator(model.columns.index(model.sort_col),
                                         Qt.SortOrder.DescendingOrder if model.sort_desc else Qt.SortOrder.AscendingOrder)
        else:
            self.header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.header.blockSignals(False)

    def _on_section_resized(self, idx, old, new):
        model = self.model()
        if model and idx < len(model.columns):
            model.widths[model.columns[idx]] = new
        self.schedule_layout()
        self.columns_resized.emit()

    def _on_section_moved(self, logical, old_vis, new_vis):
        self.schedule_layout()
        self.columns_resized.emit()

    def _on_section_clicked(self, idx):
        model = self.model()
        if model and idx < len(model.columns):
            self.sort_clicked.emit(model.columns[idx])

    def column_order(self) -> list[int]:
        model = self.model()
        n = len(model.columns) if model else 0
        return [self.header.logicalIndex(v) for v in range(n)]

    def set_mode(self, mode: str):
        if mode not in MODES:
            mode = "details"
        if mode == self.mode and not self._dirty:
            return
        self.mode = mode
        self.header.setVisible(mode == "details")
        self._update_margins()
        self.schedule_layout()
        self.mode_changed.emit(mode)

    def _update_margins(self):
        if self.mode == "details":
            self.setViewportMargins(0, self.header.height(), 0, 0)
        else:
            self.setViewportMargins(0, 0, 0, 0)
        self._place_header()

    def _place_header(self):
        if getattr(self, "header", None) is None:
            return
        if self.mode == "details":
            vg = self.viewport().geometry()
            self.header.setGeometry(vg.left(), vg.top() - self.header.height(), vg.width(), self.header.height())
            self.header.setOffset(self.horizontalScrollBar().value())

    def schedule_layout(self):
        self._dirty = True
        self._relayout_timer.start()

    def is_grouped(self) -> bool:
        m = self.model()
        return bool(m and m.group_col)

    # ------------------------------------------------------------------ metrics
    def icon_px(self) -> int:
        return ICON_PX[self.mode]

    def cell_size(self) -> QSize:
        fm = self.fontMetrics()
        lh = fm.height()
        if self.mode == "xlarge":
            return QSize(276, 256 + 2 * lh + 18)
        if self.mode == "large":
            return QSize(112, 96 + 2 * lh + 14)
        if self.mode == "medium":
            return QSize(84, 48 + 2 * lh + 12)
        if self.mode == "small":
            return QSize(230, max(20, lh + 6))
        if self.mode == "tiles":
            return QSize(256, max(58, 3 * lh + 10))
        if self.mode in ("list", "details"):
            return QSize(200, max(20, lh + 6))
        if self.mode == "content":
            return QSize(0, max(54, 2 * lh + 22))
        return QSize(100, 100)

    # ------------------------------------------------------------------ layout
    def _do_layout(self):
        model = self.model()
        self._cells = []
        self._headers = []
        if model is None:
            return
        vw = max(50, self.viewport().width())
        vh = max(50, self.viewport().height())
        n = model.rowCount()
        groups = model.groups() if model.group_col else [(None, "", 0, n)]
        cs = self.cell_size()
        y = MARGIN if self.mode not in ("details", "content") else 0
        maxw = vw
        self._cells = [None] * n
        if self.mode == "list" and not model.group_col:
            fm = self.fontMetrics()
            sample = range(n) if n < 3000 else range(0, n, max(1, n // 3000))
            w = 0
            for r in sample:
                w = max(w, fm.horizontalAdvance(model.text(model.rows[r], "name")))
            self._list_colw = cw = min(420, max(120, w + 32))
            rows = max(1, (vh - 2 * MARGIN) // cs.height())
            self._list_rows = rows
            for r in range(n):
                c, rr = divmod(r, rows)
                self._cells[r] = _Cell(QRect(MARGIN + c * cw, MARGIN + rr * cs.height(), cw - 4, cs.height()), r)
            cols = math.ceil(n / rows) if n else 0
            self._content = QSize(MARGIN * 2 + cols * cw, vh)
            self._finish_layout()
            return
        if self.mode == "details":
            total = sum(self.header.sectionSize(i) for i in range(len(model.columns)))
            maxw = max(vw, total)
        for key, label, first, count in groups:
            if key is not None:
                hr = QRect(0, y, maxw if self.mode in ("details", "content") else vw, GROUP_H)
                self._headers.append((hr, key, label, count, first))
                y += GROUP_H + (2 if self.mode in ("details", "content") else 4)
                if key in self._collapsed:
                    continue
            rng = range(first, first + count)
            if self.mode in ("details", "content"):
                h = cs.height()
                w = maxw
                for r in rng:
                    self._cells[r] = _Cell(QRect(0, y, w, h), r)
                    y += h
                if key is not None:
                    y += 6
            elif self.mode == "list":
                cw = 220
                cols = max(1, (vw - 2 * MARGIN) // cw)
                rows = max(1, math.ceil(count / cols))
                for i, r in enumerate(rng):
                    c, rr = divmod(i, rows)
                    self._cells[r] = _Cell(QRect(MARGIN + c * cw, y + rr * cs.height(), cw - 4, cs.height()), r)
                y += rows * cs.height() + 8
            else:
                cw, ch = cs.width(), cs.height()
                avail = vw - 2 * MARGIN
                cols = max(1, avail // cw)
                if self.mode in ("xlarge", "large", "medium") and cols > 1:
                    # distribute spare space like Explorer's auto-arrange
                    spare = avail - cols * cw
                    cw = cw + spare // cols if spare < cw else cw
                for i, r in enumerate(rng):
                    rr, c = divmod(i, cols)
                    self._cells[r] = _Cell(QRect(MARGIN + c * cw, y + rr * ch, cw - 2, ch - 2), r)
                rows = math.ceil(count / cols) if count else 0
                y += rows * ch + (10 if key is not None else 0)
        self._content = QSize(maxw, y + MARGIN)
        self._finish_layout()

    def _finish_layout(self):
        self._dirty = False
        self.updateGeometries()
        self.viewport().update()

    def updateGeometries(self):
        if getattr(self, "header", None) is None:
            return super().updateGeometries()
        vs = self.viewport().size()
        hs = self.horizontalScrollBar()
        vsb = self.verticalScrollBar()
        cs = self.cell_size()
        hs.setRange(0, max(0, self._content.width() - vs.width()))
        hs.setPageStep(vs.width())
        hs.setSingleStep(max(20, cs.width() // 2 if self.mode == "list" else 20))
        vsb.setRange(0, max(0, self._content.height() - vs.height()))
        vsb.setPageStep(vs.height())
        vsb.setSingleStep(max(20, cs.height() // 2 if self.mode not in ("details", "list", "small") else cs.height()))
        self._place_header()
        super().updateGeometries()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "header", None) is None:
            return
        self._place_header()
        self.schedule_layout()

    def scrollContentsBy(self, dx, dy):
        self.viewport().update()
        if self.mode == "details":
            self.header.setOffset(self.horizontalScrollBar().value())

    def horizontalOffset(self):
        return self.horizontalScrollBar().value()

    def verticalOffset(self):
        return self.verticalScrollBar().value()

    def _off(self) -> QPoint:
        return QPoint(self.horizontalOffset(), self.verticalOffset())

    def cell_rect(self, row: int) -> QRect:
        if self._dirty:
            self._do_layout()
        if 0 <= row < len(self._cells) and self._cells[row] is not None:
            return QRect(self._cells[row].rect)
        return QRect()

    def visualRect(self, index: QModelIndex) -> QRect:
        if not index.isValid():
            return QRect()
        r = self.cell_rect(index.row())
        if r.isNull():
            return QRect()
        return r.translated(-self._off())

    def isIndexHidden(self, index):
        return self.cell_rect(index.row()).isNull()

    def indexAt(self, point: QPoint) -> QModelIndex:
        row = self.row_at(point)
        return self.model().index(row, 0) if row >= 0 else QModelIndex()

    def row_at(self, point: QPoint, strict: bool = True) -> int:
        if self._dirty:
            self._do_layout()
        p = point + self._off()
        for c in self._cells:
            if c is not None and c.rect.contains(p):
                if strict and self.mode == "details":
                    # Explorer only hits the name column (plus a little) in Details
                    nr = self._name_hit_rect(c)
                    if not nr.contains(p):
                        return -1
                return c.row
        return -1

    def _name_hit_rect(self, c: _Cell) -> QRect:
        model = self.model()
        if "name" not in model.columns:
            return c.rect
        li = model.columns.index("name")
        x = self.header.sectionViewportPosition(li) + self.horizontalOffset()
        w = self.header.sectionSize(li)
        it = model.rows[c.row]
        fm = self.fontMetrics()
        tw = min(w - 26, fm.horizontalAdvance(model.text(it, "name")) + 8)
        return QRect(x, c.rect.top(), 24 + tw, c.rect.height())

    def header_at(self, point: QPoint) -> int:
        p = point + self._off()
        for i, h in enumerate(self._headers):
            if h[0].contains(p):
                return i
        return -1

    def scrollTo(self, index: QModelIndex, hint=QAbstractItemView.ScrollHint.EnsureVisible):
        r = self.cell_rect(index.row())
        if r.isNull():
            return
        vs = self.viewport().size()
        hs, vsb = self.horizontalScrollBar(), self.verticalScrollBar()
        if hint == QAbstractItemView.ScrollHint.PositionAtCenter:
            vsb.setValue(r.center().y() - vs.height() // 2)
            return
        if r.top() < vsb.value():
            vsb.setValue(r.top() - (GROUP_H if self.is_grouped() else 0))
        elif r.bottom() > vsb.value() + vs.height():
            vsb.setValue(r.bottom() - vs.height() + 1)
        if r.left() < hs.value():
            hs.setValue(r.left())
        elif r.right() > hs.value() + vs.width() and self.mode == "list":
            hs.setValue(r.right() - vs.width() + 1)

    def setSelection(self, rect: QRect, command):
        model = self.model()
        if model is None:
            return
        r = rect.normalized().translated(self._off())
        sel = QItemSelection()
        last = model.columnCount() - 1
        for c in self._cells:
            if c is None:
                continue
            hit = c.rect
            if self.mode == "details":
                hit = self._name_hit_rect(c)
            elif self.mode in ("xlarge", "large", "medium"):
                hit = self._icon_area(c.rect).united(self._text_area(c.rect, c.row))
            if hit.intersects(r):
                sel.select(model.index(c.row, 0), model.index(c.row, last))
        self.selectionModel().select(sel, command)

    def visualRegionForSelection(self, selection) -> QRegion:
        reg = QRegion()
        for rng in selection:
            for row in range(rng.top(), rng.bottom() + 1):
                r = self.cell_rect(row)
                if not r.isNull():
                    reg += r.translated(-self._off())
        return reg

    def moveCursor(self, action, modifiers):
        model = self.model()
        n = model.rowCount() if model else 0
        if n == 0:
            return QModelIndex()
        cur = self.currentIndex()
        row = cur.row() if cur.isValid() else -1
        A = QAbstractItemView.CursorAction
        visible = [c.row for c in self._cells if c is not None]
        if not visible:
            return QModelIndex()
        if row < 0 or self.cell_rect(row).isNull():
            return model.index(visible[0], 0)
        if action in (A.MoveHome,):
            return model.index(visible[0], 0)
        if action in (A.MoveEnd,):
            return model.index(visible[-1], 0)
        if action in (A.MovePageDown, A.MovePageUp):
            r = self.cell_rect(row)
            dy = self.viewport().height() * (1 if action == A.MovePageDown else -1)
            target = QPoint(r.center().x(), r.center().y() + dy)
            best = min(visible, key=lambda x: abs(self.cell_rect(x).center().y() - target.y()) * 4
                       + abs(self.cell_rect(x).center().x() - target.x()))
            return model.index(best, 0)
        if self.mode in ("details", "content"):
            idx = visible.index(row) if row in visible else 0
            if action in (A.MoveDown, A.MoveNext):
                idx = min(len(visible) - 1, idx + 1)
            elif action in (A.MoveUp, A.MovePrevious):
                idx = max(0, idx - 1)
            return model.index(visible[idx], 0)
        if self.mode == "list" and not self.is_grouped():
            idx = row
            if action == A.MoveDown:
                idx = min(n - 1, row + 1)
            elif action == A.MoveUp:
                idx = max(0, row - 1)
            elif action in (A.MoveRight, A.MoveNext):
                idx = min(n - 1, row + self._list_rows)
            elif action in (A.MoveLeft, A.MovePrevious):
                idx = max(0, row - self._list_rows)
            return model.index(idx, 0)
        # geometric search for icon modes
        r = self.cell_rect(row)
        c0 = r.center()
        dirs = {A.MoveRight: (1, 0), A.MoveNext: (1, 0), A.MoveLeft: (-1, 0), A.MovePrevious: (-1, 0),
                A.MoveDown: (0, 1), A.MoveUp: (0, -1)}
        d = dirs.get(action)
        if d is None:
            return cur
        if d[1] == 0:          # left/right follows reading order
            idx = visible.index(row)
            idx = max(0, min(len(visible) - 1, idx + d[0]))
            return model.index(visible[idx], 0)
        best, bestd = row, 1e18
        for x in visible:
            if x == row:
                continue
            cc = self.cell_rect(x).center()
            dx, dy = cc.x() - c0.x(), cc.y() - c0.y()
            if dy * d[1] <= 0:
                continue
            dist = abs(dy) * 1 + abs(dx) * 3
            if dist < bestd:
                best, bestd = x, dist
        return model.index(best, 0)

    # ------------------------------------------------------------------ geometry of parts
    def _icon_area(self, r: QRect) -> QRect:
        px = self.icon_px()
        if self.mode in ("xlarge", "large", "medium"):
            return QRect(r.center().x() - px // 2, r.top() + 4, px, px)
        if self.mode in ("small", "list", "details"):
            return QRect(r.left() + 4, r.center().y() - 8, 16, 16)
        if self.mode == "tiles":
            return QRect(r.left() + 6, r.center().y() - 24, 48, 48)
        if self.mode == "content":
            return QRect(r.left() + 10, r.center().y() - 20, 40, 40)
        return r

    def _text_area(self, r: QRect, row: int) -> QRect:
        fm = self.fontMetrics()
        if self.mode in ("xlarge", "large", "medium"):
            ia = self._icon_area(r)
            top = ia.bottom() + 4
            model = self.model()
            name = model.text(model.rows[row], "name")
            lines = self._wrap(name, r.width() - 6, 2)
            w = max(fm.horizontalAdvance(ln) for ln in lines) + 6 if lines else 10
            return QRect(r.center().x() - w // 2, top, w, len(lines) * fm.height() + 2)
        if self.mode in ("small", "list"):
            model = self.model()
            name = model.text(model.rows[row], "name")
            w = min(r.width() - 26, fm.horizontalAdvance(name) + 6)
            return QRect(r.left() + 24, r.top(), w, r.height())
        return r

    def label_rect(self, index: QModelIndex) -> QRect:
        """Viewport rect of the item's name (used for the rename editor)."""
        row = index.row()
        r = self.cell_rect(row)
        if r.isNull():
            return QRect()
        fm = self.fontMetrics()
        if self.mode in ("xlarge", "large", "medium"):
            t = self._text_area(r, row)
            out = QRect(r.left() + 2, t.top(), r.width() - 4, fm.height())
        elif self.mode in ("small", "list"):
            out = QRect(r.left() + 24, r.top(), r.width() - 26, r.height())
        elif self.mode == "details":
            model = self.model()
            li = model.columns.index("name") if "name" in model.columns else 0
            x = self.header.sectionViewportPosition(li) + self.horizontalOffset()
            out = QRect(x + 24, r.top(), self.header.sectionSize(li) - 26, r.height())
        elif self.mode == "tiles":
            out = QRect(r.left() + 60, r.top() + 6, r.width() - 66, fm.height() + 2)
        else:
            out = QRect(r.left() + 60, r.top() + 6, max(200, r.width() // 2 - 60), fm.height() + 2)
        return out.translated(-self._off())

    def _wrap(self, text: str, width: int, max_lines: int) -> list[str]:
        fm = self.fontMetrics()
        if fm.horizontalAdvance(text) <= width:
            return [text]
        layout = QTextLayout(text, self.font())
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(opt)
        layout.beginLayout()
        lines = []
        while True:
            ln = layout.createLine()
            if not ln.isValid():
                break
            ln.setLineWidth(width)
            lines.append((ln.textStart(), ln.textLength()))
        layout.endLayout()
        out = []
        for i, (s, l) in enumerate(lines[:max_lines]):
            if i == max_lines - 1 and len(lines) > max_lines:
                out.append(fm.elidedText(text[s:], Qt.TextElideMode.ElideRight, width))
            else:
                out.append(text[s:s + l].rstrip())
        return out

    # ------------------------------------------------------------------ painting
    def paintEvent(self, e):
        if self._dirty:
            self._do_layout()
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        model = self.model()
        off = self._off()
        clip = e.rect().translated(off)
        p.translate(-off)
        if model is None:
            return
        sel = self.selectionModel()
        cur = self.currentIndex()
        focus = self.hasFocus() or (self.state() == QAbstractItemView.State.EditingState)
        if not model.rowCount():
            if self.empty_text and not model.is_loading:
                p.setPen(GRAY_TEXT)
                r = QRect(0, 12, self.viewport().width(), 30).translated(off)
                p.drawText(r, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.empty_text)
            elif model.is_loading:
                p.setPen(GRAY_TEXT)
                r = QRect(0, 12, self.viewport().width(), 30).translated(off)
                p.drawText(r, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, T("working_on_it"))
        for i, h in enumerate(self._headers):
            if h[0].intersects(clip):
                self._paint_group_header(p, h, i == self._hover_header)
        if self.mode == "details":
            self._paint_sorted_column(p, clip)
        show_checks = settings().get("checkboxes")
        for c in self._cells:
            if c is None or not c.rect.intersects(clip):
                continue
            idx = model.index(c.row, 0)
            selected = sel.isRowSelected(c.row, QModelIndex())
            state = 0
            if selected:
                state = 2 if focus else 3
            elif c.row == self._hover or c.row == self._drop_row:
                state = 1
            if c.row == self._drop_row:
                state = 2
            self._paint_item(p, c, idx, state, cur.isValid() and cur.row() == c.row and focus, show_checks)
        if self._band is not None:
            br = self._band.normalized()
            p.setPen(QPen(QColor(51, 153, 255), 1))
            p.setBrush(QColor(51, 153, 255, 60))
            p.drawRect(br.adjusted(0, 0, -1, -1))
        p.end()

    def _paint_sorted_column(self, p: QPainter, clip: QRect):
        pass

    def _paint_group_header(self, p: QPainter, h, hot: bool):
        r, key, label, count, first = h
        fm = self.fontMetrics()
        x = r.left() + (14 if self.mode in ("details", "content") else MARGIN + 8)
        collapsed = key in self._collapsed
        if hot:
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            g.setColorAt(0, QColor(247, 251, 254))
            g.setColorAt(1, QColor(230, 242, 251))
            p.setPen(QPen(QColor(200, 225, 245)))
            p.setBrush(g)
            p.drawRoundedRect(QRectF(r).adjusted(MARGIN, 1.5, -MARGIN - 0.5, -1.5), 2, 2)
        # chevron
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(90, 120, 170) if hot else QColor(150, 160, 175), 1.4))
        cy = r.center().y()
        cx = x - 2
        if collapsed:
            p.drawPolyline([QPointF(cx - 2, cy - 4), QPointF(cx + 2, cy), QPointF(cx - 2, cy + 4)])
        else:
            p.drawPolyline([QPointF(cx - 4, cy - 2), QPointF(cx, cy + 2), QPointF(cx + 4, cy - 2)])
        p.restore()
        text = f"{label} ({count})"
        f = QFont(self.font())
        f.setPointSizeF(self.font().pointSizeF() * 1.12)
        p.setFont(f)
        p.setPen(GROUP_TEXT)
        tw = QFontMetrics(f).horizontalAdvance(text)
        p.drawText(QRect(x + 10, r.top(), tw + 4, r.height()), Qt.AlignmentFlag.AlignVCenter, text)
        p.setFont(self.font())
        lx = x + 10 + tw + 10
        right = min(r.right() - MARGIN, self.viewport().width() + self.horizontalOffset() - MARGIN)
        if lx < right:
            g = QLinearGradient(QPointF(lx, 0), QPointF(right, 0))
            g.setColorAt(0, QColor(200, 210, 225))
            g.setColorAt(1, QColor(255, 255, 255, 0))
            p.setPen(QPen(QBrush(g), 1))
            p.drawLine(lx, r.center().y() + 1, right, r.center().y() + 1)

    def _paint_bg(self, p: QPainter, r: QRect, state: int, focus_rect: bool):
        if state == 0:
            if focus_rect:
                p.setPen(QPen(QColor(125, 162, 206), 1, Qt.PenStyle.DotLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r.adjusted(0, 0, -1, -1))
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if state == 1:
            top, bot, border = QColor(250, 253, 255), HOT_FILL, QColor(184, 214, 251)
        elif state == 2:
            top, bot, border = QColor(220, 235, 252), QColor(193, 219, 252), QColor(125, 162, 206)
        else:
            top, bot, border = QColor(248, 248, 248), QColor(229, 229, 229), QColor(217, 217, 217)
        g = QLinearGradient(QPointF(r.topLeft()), QPointF(r.bottomLeft()))
        g.setColorAt(0, top)
        g.setColorAt(1, bot)
        p.setBrush(g)
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        p.setPen(QPen(QColor(255, 255, 255, 150), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(r).adjusted(1.5, 1.5, -1.5, -1.5), 2, 2)
        p.restore()

    def _pixmap_for(self, it, idx, px: int) -> tuple[QPixmap, bool]:
        """(pixmap, is_thumbnail)."""
        model = self.model()
        dpr = self.devicePixelRatioF()
        if px >= 32 and not settings().get("always_icons") and it.kind in ("file", "trash") and not it.is_dir:
            pm = thumbs.service().get(it.path, it.mtime, int(px * dpr))
            if pm is not None:
                return pm, True
        if px >= 32 and it.kind == "dir" and not settings().get("always_icons"):
            pass
        ic: QIcon = model.icon(it)
        pm = ic.pixmap(QSize(px, px), dpr)
        if it.is_link and it.kind == "file" or (it.is_link and it.kind == "dir"):
            pm = icons.with_link_overlay(pm)
        return pm, False

    def _draw_pixmap(self, p: QPainter, area: QRect, pm: QPixmap, thumb: bool, ghost: bool, it=None):
        dpr = pm.devicePixelRatio() or 1
        sz = QSize(int(pm.width() / dpr), int(pm.height() / dpr))
        if thumb:
            sz = sz.scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
        elif sz.width() > area.width() or sz.height() > area.height():
            sz = sz.scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = area.left() + (area.width() - sz.width()) // 2
        y = area.top() + (area.height() - sz.height()) // 2 if not thumb or self.mode not in ("xlarge", "large", "medium") \
            else area.bottom() - sz.height() + 1
        target = QRect(x, y, sz.width(), sz.height())
        if ghost:
            p.setOpacity(0.45)
        if thumb and area.width() >= 40:
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 28))
            p.drawRect(target.adjusted(1, 1, 3, 3))
            p.restore()
        p.drawPixmap(target, pm)
        if thumb and area.width() >= 40:
            p.setPen(QPen(QColor(200, 200, 200), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(target.adjusted(0, 0, -1, -1))
            if it is not None and settings().get("file_icon_on_thumbs") and area.width() >= 64 \
                    and it.mime.name().startswith("video/"):
                ic = self.model().icon(it).pixmap(QSize(24, 24), self.devicePixelRatioF())
                p.drawPixmap(QRect(target.right() - 26, target.bottom() - 26, 24, 24), ic)
        if ghost:
            p.setOpacity(1.0)

    def _paint_check(self, p: QPainter, r: QRect, checked: bool):
        b = QRect(r.left() + 3, r.top() + 3, 13, 13)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(110, 130, 160), 1))
        p.setBrush(QColor(255, 255, 255))
        p.drawRect(b)
        if checked:
            p.setPen(QPen(QColor(40, 70, 130), 1.8))
            p.drawPolyline([QPointF(b.left() + 3, b.center().y()), QPointF(b.left() + 5.5, b.bottom() - 3),
                            QPointF(b.right() - 2.5, b.top() + 3)])
        p.restore()

    def check_rect(self, row: int) -> QRect:
        r = self.cell_rect(row)
        if self.mode in ("xlarge", "large", "medium"):
            ia = self._icon_area(r)
            return QRect(ia.left() - 4, ia.top() - 2, 19, 19)
        if self.mode == "details":
            return QRect(r.left(), r.top(), 19, r.height())
        return QRect(r.left(), r.top(), 19, 19)

    def _draw_text_hl(self, p: QPainter, rect: QRect, text: str, flags, color: QColor):
        """Text with search-term highlighting (yellow, like Windows 7 search results)."""
        model = self.model()
        fm = self.fontMetrics()
        el = fm.elidedText(text, Qt.TextElideMode.ElideRight, max(10, rect.width()))
        terms = model.search_terms if model else []
        if terms and el == text:
            low = text.casefold()
            x0 = rect.left()
            if flags & Qt.AlignmentFlag.AlignHCenter:
                x0 = rect.left() + (rect.width() - fm.horizontalAdvance(text)) // 2
            for t in terms:
                if not t:
                    continue
                start = 0
                while True:
                    i = low.find(t, start)
                    if i < 0:
                        break
                    a = fm.horizontalAdvance(text[:i])
                    w = fm.horizontalAdvance(text[i:i + len(t)])
                    y = rect.top() + (rect.height() - fm.height()) // 2 if flags & Qt.AlignmentFlag.AlignVCenter else rect.top()
                    p.fillRect(QRect(x0 + a, y, w, fm.height()), HILITE)
                    start = i + len(t)
        p.setPen(color)
        p.drawText(rect, flags, el)

    def _paint_item(self, p: QPainter, c: _Cell, idx: QModelIndex, state: int, focus_rect: bool, checks: bool):
        model = self.model()
        it = model.rows[c.row]
        r = c.rect
        ghost = bool(idx.data(GhostRole))
        fm = self.fontMetrics()
        mode = self.mode
        txt = QColor(0, 0, 0)
        name = model.text(it, "name")
        if mode in ("xlarge", "large", "medium"):
            ia = self._icon_area(r)
            ta = self._text_area(r, c.row)
            selected = state in (2, 3)
            lines = self._wrap(name, r.width() - 6, 5 if (selected and focus_rect) else 2)
            ta.setHeight(len(lines) * fm.height() + 2)
            if state:
                bg = QRect(r.left() + 1, r.top() + 1, r.width() - 2, ta.bottom() - r.top() + 3)
                self._paint_bg(p, bg, state, False)
            elif focus_rect:
                self._paint_bg(p, QRect(r.left() + 1, r.top() + 1, r.width() - 2, ta.bottom() - r.top() + 3), 0, True)
            pm, thumb = self._pixmap_for(it, idx, ia.width())
            self._draw_pixmap(p, ia, pm, thumb, ghost, it)
            p.setPen(txt)
            y = ta.top()
            for ln in lines:
                self._draw_text_hl(p, QRect(r.left() + 2, y, r.width() - 4, fm.height()), ln,
                                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, txt)
                y += fm.height()
            if checks and (state or c.row == self._hover):
                self._paint_check(p, self.check_rect(c.row), state in (2, 3))
            return
        if mode in ("small", "list"):
            ta = self._text_area(r, c.row)
            bg = QRect(r.left(), r.top() + 1, ta.right() - r.left() + 2, r.height() - 2)
            self._paint_bg(p, bg, state, focus_rect)
            ia = self._icon_area(r)
            if checks:
                ia.translate(16, 0)
                ta.translate(16, 0)
                if state or c.row == self._hover:
                    self._paint_check(p, QRect(r.left(), r.top() + (r.height() - 19) // 2, 19, 19), state in (2, 3))
            pm, thumb = self._pixmap_for(it, idx, 16)
            self._draw_pixmap(p, ia, pm, False, ghost)
            self._draw_text_hl(p, ta.adjusted(2, 0, 0, 0), name, Qt.AlignmentFlag.AlignVCenter, txt)
            return
        if mode == "details":
            self._paint_details_row(p, c, idx, it, state, focus_rect, ghost, checks)
            return
        if mode == "tiles":
            self._paint_bg(p, r.adjusted(2, 1, -2, -1), state, focus_rect)
            ia = self._icon_area(r)
            pm, thumb = self._pixmap_for(it, idx, 48)
            self._draw_pixmap(p, ia, pm, thumb, ghost, it)
            x = ia.right() + 8
            w = r.right() - x - 6
            lines = self._tile_lines(it)
            lh = fm.height()
            total = lh * (1 + len(lines)) + (6 if it.kind == "drive" and it.size else 0)
            y = r.top() + (r.height() - total) // 2
            self._draw_text_hl(p, QRect(x, y, w, lh), name, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, txt)
            y += lh
            if it.kind == "drive" and it.size:
                self._paint_capacity(p, QRect(x, y + 2, min(w, 180), 12), it)
                y += 16
                lines = lines[1:] if lines else []
            for ln in lines:
                p.setPen(GRAY_TEXT)
                p.drawText(QRect(x, y, w, lh), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                           fm.elidedText(ln, Qt.TextElideMode.ElideRight, w))
                y += lh
            if checks and (state or c.row == self._hover):
                self._paint_check(p, QRect(r.left() + 2, r.top() + 2, 19, 19), state in (2, 3))
            return
        if mode == "content":
            self._paint_content_row(p, c, idx, it, state, focus_rect, ghost, checks)

    def _tile_lines(self, it) -> list[str]:
        model = self.model()
        if it.kind == "drive":
            d = it.extra.get("drive") or {}
            if d.get("size") and d.get("mount"):
                return ["", T("free_of", util.fmt_size_short(d.get("free") or 0), util.fmt_size_short(d["size"]))]
            if d.get("size"):
                return [T("not_mounted") + " · " + util.fmt_size_short(d["size"])]
            return [it.type_text()]
        if it.kind == "library":
            return [T("library")]
        if it.kind == "special" and "counts" in it.extra:
            c, sv = it.extra["counts"]
            out = [it.type_text()]
            if c or sv:
                out.append(T("net_counts", c, sv))
            return out
        out = [it.type_text()]
        if not it.is_dir and it.size is not None:
            out.append(util.fmt_size_short(it.size))
        cols = [c for c in model.columns if c not in ("name", "type", "size")]
        if it.is_dir and cols:
            v = model.text(it, cols[0])
            if v and cols[0] == "date_modified":
                out.append(v)
        return out[:2]

    def _paint_capacity(self, p: QPainter, r: QRect, it):
        d = it.extra.get("drive") or {}
        size = d.get("size") or 0
        used = d.get("used") if d.get("used") is not None else size - (d.get("free") or 0)
        frac = max(0.0, min(1.0, used / size)) if size else 0
        p.save()
        p.setPen(QPen(QColor(150, 160, 170), 1))
        g = QLinearGradient(QPointF(r.topLeft()), QPointF(r.bottomLeft()))
        g.setColorAt(0, QColor(236, 236, 236))
        g.setColorAt(1, QColor(220, 220, 220))
        p.setBrush(g)
        p.drawRect(r.adjusted(0, 0, -1, -1))
        fr = QRect(r.left() + 1, r.top() + 1, int((r.width() - 2) * frac), r.height() - 2)
        g2 = QLinearGradient(QPointF(fr.topLeft()), QPointF(fr.bottomLeft()))
        if frac > 0.9:
            g2.setColorAt(0, QColor(240, 120, 120))
            g2.setColorAt(1, QColor(200, 30, 30))
        else:
            g2.setColorAt(0, QColor(110, 190, 245))
            g2.setColorAt(1, QColor(38, 160, 218))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g2)
        p.drawRect(fr)
        p.restore()

    def _paint_details_row(self, p, c, idx, it, state, focus_rect, ghost, checks):
        model = self.model()
        r = c.rect
        fm = self.fontMetrics()
        total = sum(self.header.sectionSize(i) for i in range(len(model.columns)))
        row_rect = QRect(r.left(), r.top(), max(total, 10), r.height())
        if state or focus_rect:
            self._paint_bg(p, row_rect.adjusted(1, 0, -1, 0), state, focus_rect)
        hx = self.horizontalOffset()
        for vis in range(len(model.columns)):
            li = self.header.logicalIndex(vis)
            if li < 0 or li >= len(model.columns) or self.header.isSectionHidden(li):
                continue
            col = model.columns[li]
            x = self.header.sectionViewportPosition(li) + hx
            w = self.header.sectionSize(li)
            cr = QRect(x + 6, r.top(), w - 12, r.height())
            if col == "name":
                ir = QRect(x + 4, r.center().y() - 8, 16, 16)
                if checks:
                    if state or c.row == self._hover:
                        self._paint_check(p, QRect(x, r.top() + (r.height() - 19) // 2, 19, 19), state in (2, 3))
                    ir.translate(16, 0)
                pm, _ = self._pixmap_for(it, idx, 16)
                self._draw_pixmap(p, ir, pm, False, ghost)
                tr = QRect(ir.right() + 5, r.top(), x + w - ir.right() - 10, r.height())
                self._draw_text_hl(p, tr, model.text(it, "name"), Qt.AlignmentFlag.AlignVCenter, QColor(0, 0, 0))
            elif col == "rating":
                self._paint_stars(p, cr, it.props().get("rating") or 0)
            elif col == "percent_full" and it.kind == "drive":
                self._paint_capacity(p, QRect(cr.left(), r.center().y() - 6, min(cr.width(), 100), 12), it)
            else:
                al = COLUMNS[col][1]
                p.setPen(GRAY_TEXT)
                p.drawText(cr, al | Qt.AlignmentFlag.AlignVCenter,
                           fm.elidedText(model.text(it, col), Qt.TextElideMode.ElideRight, cr.width()))

    def _paint_stars(self, p: QPainter, r: QRect, n: int):
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 0
        on = icons.glyph_star(True, 13)
        off = icons.glyph_star(False, 13)
        x = r.left()
        y = r.center().y() - 6
        for i in range(5):
            p.drawPixmap(x + i * 14, y, on if i < n else off)

    def _paint_content_row(self, p, c, idx, it, state, focus_rect, ghost, checks):
        model = self.model()
        r = c.rect
        fm = self.fontMetrics()
        vw = self.viewport().width()
        rr = QRect(r.left(), r.top(), max(vw, 100), r.height())
        if state or focus_rect:
            self._paint_bg(p, rr.adjusted(2, 1, -2, -1), state, focus_rect)
        else:
            p.setPen(QPen(QColor(234, 234, 234), 1))
            p.drawLine(rr.left() + 8, rr.bottom(), rr.right() - 8, rr.bottom())
        ia = self._icon_area(rr)
        if checks:
            if state or c.row == self._hover:
                self._paint_check(p, QRect(r.left() + 2, r.top() + 2, 19, 19), state in (2, 3))
        pm, thumb = self._pixmap_for(it, idx, 40)
        self._draw_pixmap(p, ia, pm, thumb, ghost, it)
        lh = fm.height()
        x = ia.right() + 12
        right_w = min(260, rr.width() // 3)
        mid_w = rr.right() - right_w - x - 10
        top = rr.top() + (rr.height() - 2 * lh - 4) // 2
        name = model.text(it, "name")
        self._draw_text_hl(p, QRect(x, top, mid_w, lh), name, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                           QColor(0, 0, 0))
        second = ""
        if it.kind == "drive":
            d = it.extra.get("drive") or {}
            second = it.type_text()
            if d.get("size"):
                self._paint_capacity(p, QRect(x, top + lh + 4, min(mid_w, 200), 11), it)
                second = ""
        elif model.search_terms or it.extra.get("in_folder"):
            second = it.extra.get("in_folder", "")
        else:
            p_ = it.props() if it.kind == "file" else {}
            second = p_.get("artists") or p_.get("authors") or p_.get("dimensions") or it.type_text()
        if second:
            self._draw_text_hl(p, QRect(x, top + lh + 4, mid_w, lh), second,
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, GRAY_TEXT)
        rx = rr.right() - right_w
        pairs = []
        if it.kind == "drive":
            d = it.extra.get("drive") or {}
            if d.get("size"):
                pairs = [(T("col_free_space"), util.fmt_size_short(d.get("free") or 0)),
                         (T("col_total_size"), util.fmt_size_short(d["size"]))]
        else:
            if it.mtime:
                pairs.append((T("col_date_modified"), util.fmt_datetime(it.mtime)))
            if not it.is_dir and it.size is not None:
                pairs.append((T("col_size"), util.fmt_size_short(it.size)))
            elif it.kind == "library":
                pairs.append((T("col_type"), T("library")))
        y = top
        for k, v in pairs[:2]:
            lab = k + ": "
            p.setPen(GRAY_TEXT)
            lw = fm.horizontalAdvance(lab)
            p.drawText(QRect(rx, y, lw, lh), Qt.AlignmentFlag.AlignLeft, lab)
            p.setPen(QColor(30, 30, 30))
            p.drawText(QRect(rx + lw, y, right_w - lw, lh), Qt.AlignmentFlag.AlignLeft,
                       fm.elidedText(v, Qt.TextElideMode.ElideRight, right_w - lw))
            y += lh + 4

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        hi = self.header_at(pos)
        if hi >= 0 and e.button() == Qt.MouseButton.LeftButton:
            h = self._headers[hi]
            r = h[0].translated(-self._off())
            chevron = QRect(r.left(), r.top(), (14 if self.mode in ("details", "content") else MARGIN + 8) + 10, r.height())
            if chevron.contains(pos) or e.modifiers() & Qt.KeyboardModifier.AltModifier:
                self.toggle_group(h[1])
            else:
                self.select_group(h[1], e.modifiers())
            return
        if e.button() == Qt.MouseButton.MiddleButton:
            row = self.row_at(pos)
            if row >= 0:
                self.open_new_tab.emit(self.model().index(row, 0))
            e.accept()
            return
        if e.button() in (Qt.MouseButton.BackButton, Qt.MouseButton.XButton1):
            self.back_forward.emit(-1)
            return
        if e.button() in (Qt.MouseButton.ForwardButton, Qt.MouseButton.XButton2):
            self.back_forward.emit(1)
            return
        row = self.row_at(pos)
        if settings().get("checkboxes") and row >= 0 and e.button() == Qt.MouseButton.LeftButton:
            if self.check_rect(row).translated(-self._off()).contains(pos):
                idx = self.model().index(row, 0)
                self.selectionModel().select(idx, QItemSelectionModel.SelectionFlag.Toggle |
                                             QItemSelectionModel.SelectionFlag.Rows)
                self.selectionModel().setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
                return
        if e.button() == Qt.MouseButton.RightButton:
            self._right_press = pos
            if row >= 0:
                idx = self.model().index(row, 0)
                if not self.selectionModel().isRowSelected(row, QModelIndex()):
                    self.selectionModel().select(idx, QItemSelectionModel.SelectionFlag.ClearAndSelect |
                                                 QItemSelectionModel.SelectionFlag.Rows)
                self.selectionModel().setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
            elif not (e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
                self.clearSelection()
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton and e.modifiers() & Qt.KeyboardModifier.ShiftModifier and row >= 0:
            self.select_range_to(row, add=bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier))
            self._press_pos = None
            e.accept()
            return
        self._press_pos = pos + self._off()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._right_press is not None and e.buttons() & Qt.MouseButton.RightButton:
            if (pos - self._right_press).manhattanLength() >= QApplication.startDragDistance():
                row = self.row_at(self._right_press)
                self._right_press = None
                if row >= 0:
                    self.startDrag(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.LinkAction,
                                   right=True)
            return
        row = self.row_at(pos) if not (e.buttons() & Qt.MouseButton.LeftButton) else self._hover
        hh = self.header_at(pos) if not e.buttons() else -1
        if row != self._hover or hh != self._hover_header:
            self._hover = row
            self._hover_header = hh
            self.viewport().update()
        cursor = Qt.CursorShape.ArrowCursor
        if settings().get("single_click") and row >= 0:
            cursor = Qt.CursorShape.PointingHandCursor
        self.viewport().setCursor(cursor)
        super().mouseMoveEvent(e)
        if self.state() == QAbstractItemView.State.DragSelectingState and self._press_pos is not None:
            self._band = QRect(self._press_pos, pos + self._off()).normalized()
            self.viewport().update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton and self._right_press is not None:
            self._right_press = None
            row = self.row_at(e.position().toPoint())
            self.context_requested.emit(e.globalPosition().toPoint(),
                                        self.model().index(row, 0) if row >= 0 else QModelIndex())
            return
        self._right_press = None
        had_band = self._band is not None
        self._band = None
        self._press_pos = None
        super().mouseReleaseEvent(e)
        if had_band:
            self.viewport().update()
        if settings().get("single_click") and e.button() == Qt.MouseButton.LeftButton and not had_band \
                and not (e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
            row = self.row_at(e.position().toPoint())
            if row >= 0 and self.state() != QAbstractItemView.State.EditingState:
                self.open_requested.emit(self.model().index(row, 0))

    def mouseDoubleClickEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()
        if self.header_at(pos) >= 0:
            self.toggle_group(self._headers[self.header_at(pos)][1])
            return
        row = self.row_at(pos)
        if row >= 0 and not settings().get("single_click"):
            self.open_requested.emit(self.model().index(row, 0))

    def leaveEvent(self, e):
        if self._hover != -1 or self._hover_header != -1:
            self._hover = -1
            self._hover_header = -1
            self.viewport().update()
        super().leaveEvent(e)

    def contextMenuEvent(self, e):
        if e.reason() == e.Reason.Mouse:
            return            # handled on right-button release (Explorer behaviour)
        idx = self.currentIndex()
        pos = self.viewport().mapToGlobal(self.visualRect(idx).center()) if idx.isValid() and \
            self.selectionModel().isRowSelected(idx.row(), QModelIndex()) else self.mapToGlobal(QPoint(30, 30))
        self.context_requested.emit(pos, idx if idx.isValid() and self.selectionModel().isRowSelected(idx.row(), QModelIndex()) else QModelIndex())

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            d = e.angleDelta().y()
            if d:
                i = WHEEL_ORDER.index(self.mode) if self.mode in WHEEL_ORDER else 2
                i = max(0, min(len(WHEEL_ORDER) - 1, i + (1 if d > 0 else -1)))
                self.set_mode(WHEEL_ORDER[i])
            e.accept()
            return
        if self.mode == "list" and not self.is_grouped() and not e.angleDelta().x():
            hs = self.horizontalScrollBar()
            hs.setValue(hs.value() - e.angleDelta().y())
            e.accept()
            return
        super().wheelEvent(e)

    # ------------------------------------------------------------------ range selection (Shift)
    def _current_changed(self, cur, _prev):
        """The anchor of Shift selections follows the current item unless a range is being extended."""
        if self._extending or not cur.isValid():
            return
        rows = self.model().rows if hasattr(self.model(), "rows") else None
        self._anchor_path = rows[cur.row()].path if rows is not None and cur.row() < len(rows) else None

    def _anchor_row(self) -> int:
        m = self.model()
        if self._anchor_path and hasattr(m, "row_of_path"):
            r = m.row_of_path(self._anchor_path)
            if r >= 0:
                return r
        cur = self.currentIndex()
        return cur.row() if cur.isValid() else -1

    def select_range_to(self, row: int, add: bool = False):
        """Select from the anchor to row in the order items are shown (Windows Shift+arrow/click)."""
        m = self.model()
        if row < 0 or m is None:
            return
        anchor = self._anchor_row()
        if anchor < 0:
            anchor = row
        order = [c.row for c in self._cells if c is not None]
        pos = {r: i for i, r in enumerate(order)}
        if anchor not in pos or row not in pos:
            rng = range(min(anchor, row), max(anchor, row) + 1)
        else:
            a, b = sorted((pos[anchor], pos[row]))
            rng = order[a:b + 1]
        sel = QItemSelection()
        last = m.columnCount() - 1
        for r in rng:
            sel.select(m.index(r, 0), m.index(r, last))
        flag = QItemSelectionModel.SelectionFlag.Select if add else QItemSelectionModel.SelectionFlag.ClearAndSelect
        self._extending = True
        try:
            self.selectionModel().select(sel, flag)
            self.selectionModel().setCurrentIndex(m.index(row, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            self._extending = False
        self.scrollTo(m.index(row, 0))
        self.viewport().update()

    _NAV_KEYS = {Qt.Key.Key_Up: "MoveUp", Qt.Key.Key_Down: "MoveDown", Qt.Key.Key_Left: "MoveLeft",
                 Qt.Key.Key_Right: "MoveRight", Qt.Key.Key_Home: "MoveHome", Qt.Key.Key_End: "MoveEnd",
                 Qt.Key.Key_PageUp: "MovePageUp", Qt.Key.Key_PageDown: "MovePageDown"}

    def keyPressEvent(self, e):
        mods = e.modifiers()
        if e.key() in self._NAV_KEYS and mods & Qt.KeyboardModifier.ShiftModifier \
                and self.state() != QAbstractItemView.State.EditingState and self.model() is not None \
                and self.model().rowCount():
            if not self.currentIndex().isValid():
                first = self.moveCursor(QAbstractItemView.CursorAction.MoveHome, mods)
                self.selectionModel().setCurrentIndex(first, QItemSelectionModel.SelectionFlag.ClearAndSelect |
                                                      QItemSelectionModel.SelectionFlag.Rows)
                e.accept()
                return
            if e.key() == Qt.Key.Key_Left and self.is_grouped() and self.mode in ("details", "content"):
                e.accept()
                return
            action = getattr(QAbstractItemView.CursorAction, self._NAV_KEYS[e.key()])
            idx = self.moveCursor(action, mods)
            if idx.isValid():
                self.select_range_to(idx.row(), add=bool(mods & Qt.KeyboardModifier.ControlModifier))
            e.accept()
            return
        if e.key() == Qt.Key.Key_Return or e.key() == Qt.Key.Key_Enter:
            if self.state() != QAbstractItemView.State.EditingState:
                idx = self.currentIndex()
                if idx.isValid() and e.modifiers() == Qt.KeyboardModifier.NoModifier:
                    self.open_requested.emit(idx)
                    return
        if e.key() == Qt.Key.Key_Left and self.is_grouped() and self.mode in ("details", "content"):
            idx = self.currentIndex()
            if idx.isValid():
                g = self._group_of_row(idx.row())
                if g is not None:
                    self.toggle_group(g, collapse=True)
                    return
        super().keyPressEvent(e)

    def _group_of_row(self, row: int):
        for h in self._headers:
            r, key, label, count, first = h
            if first <= row < first + count:
                return key
        return None

    def toggle_group(self, key, collapse: bool | None = None):
        if collapse is None:
            collapse = key not in self._collapsed
        if collapse:
            self._collapsed.add(key)
        else:
            self._collapsed.discard(key)
        self.schedule_layout()

    def expand_all(self):
        self._collapsed.clear()
        self.schedule_layout()

    def collapse_all(self):
        for h in self._headers:
            self._collapsed.add(h[1])
        self.schedule_layout()

    def select_group(self, key, mods=Qt.KeyboardModifier.NoModifier):
        model = self.model()
        sel = QItemSelection()
        for h in self._headers:
            if h[1] == key:
                first, count = h[4], h[3]
                sel.select(model.index(first, 0), model.index(first + count - 1, model.columnCount() - 1))
                flag = QItemSelectionModel.SelectionFlag.Select if mods & Qt.KeyboardModifier.ControlModifier \
                    else QItemSelectionModel.SelectionFlag.ClearAndSelect
                self.selectionModel().select(sel, flag)
                self.selectionModel().setCurrentIndex(model.index(first, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
                break

    def selected_rows(self) -> list[int]:
        sm = self.selectionModel()
        if sm is None:
            return []
        rows = set()
        for rng in sm.selection():
            rows.update(range(rng.top(), rng.bottom() + 1))
        return sorted(rows)

    # ------------------------------------------------------------------ drag & drop
    def startDrag(self, actions, right: bool = False):
        rows = self.selected_rows()
        if not rows or self.mime_provider is None:
            return
        mime: QMimeData = self.mime_provider(rows)
        if mime is None:
            return
        if right:
            mime.setData("application/x-linexplorer-rightdrag", b"1")
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self._drag_pixmap(rows))
        drag.setHotSpot(QPoint(40, 40))
        default = Qt.DropAction.MoveAction
        drag.exec(actions | Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.LinkAction, default)

    def _drag_pixmap(self, rows: list[int]) -> QPixmap:
        model = self.model()
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(110 * dpr), int(110 * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(0.85)
        for i, r in enumerate(rows[:3][::-1]):
            it = model.rows[r]
            ipm, thumb = self._pixmap_for(it, model.index(r, 0), 72)
            off = (len(rows[:3]) - 1 - i) * 6
            self._draw_pixmap(p, QRect(10 + off, 10 + off, 72, 72), ipm, thumb, False)
        p.setOpacity(1)
        if len(rows) > 1:
            txt = str(len(rows))
            f = QFont(self.font())
            f.setBold(True)
            p.setFont(f)
            w = max(20, QFontMetrics(f).horizontalAdvance(txt) + 12)
            br = QRect(60, 70, w, 20)
            p.setBrush(QColor(30, 110, 220))
            p.setPen(QPen(QColor(255, 255, 255), 1.5))
            p.drawRoundedRect(br, 9, 9)
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, txt)
        p.end()
        return pm

    def _drop_target(self, pos: QPoint):
        row = self.row_at(pos, strict=False)
        if row >= 0:
            it = self.model().rows[row]
            if it.is_dir or it.kind in ("drive", "library") or (it.kind == "file" and it.name.lower().endswith(".zip")):
                if not self.selectionModel().isRowSelected(row, QModelIndex()) or self.state() != QAbstractItemView.State.DraggingState:
                    return row, it
        return -1, None

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasFormat("x-special/gnome-copied-files"):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        pos = e.position().toPoint()
        row, it = self._drop_target(pos)
        if row != self._drop_row:
            self._drop_row = row
            self.viewport().update()
        if self.drop_action_for is not None:
            act = self.drop_action_for(e.mimeData(), it, e.modifiers())
            if act == Qt.DropAction.IgnoreAction:
                e.ignore()
                return
            e.setDropAction(act)
            e.accept()
        else:
            e.acceptProposedAction()
        # autoscroll near edges
        m = 20
        vs = self.viewport().rect()
        if pos.y() < m:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - 12)
        elif pos.y() > vs.height() - m:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + 12)

    def dragLeaveEvent(self, e):
        self._drop_row = -1
        self.viewport().update()

    def dropEvent(self, e):
        pos = e.position().toPoint()
        row, it = self._drop_target(pos)
        self._drop_row = -1
        self.viewport().update()
        right = e.mimeData().hasFormat("application/x-linexplorer-rightdrag")
        act = e.dropAction()
        if self.drop_action_for is not None:
            act = self.drop_action_for(e.mimeData(), it, e.modifiers())
        if act == Qt.DropAction.IgnoreAction:
            e.ignore()
            return
        # copy the mime data: the source's object dies with the drag
        md = QMimeData()
        for f in e.mimeData().formats():
            md.setData(f, e.mimeData().data(f))
        if e.mimeData().hasUrls():
            md.setUrls(e.mimeData().urls())
        e.setDropAction(act if not right else Qt.DropAction.CopyAction)
        e.accept()
        QTimer.singleShot(0, lambda: self.drop_requested.emit(md, it, act, right))

    # ------------------------------------------------------------------ misc
    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.viewport().update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.viewport().update()

    def viewportEvent(self, e):
        if e.type() == e.Type.ToolTip:
            row = self.row_at(e.pos())
            if row >= 0:
                tip = self.model().tooltip(self.model().rows[row])
                if tip:
                    from PySide6.QtWidgets import QToolTip
                    QToolTip.showText(e.globalPos(), tip, self.viewport())
                    return True
            from PySide6.QtWidgets import QToolTip
            QToolTip.hideText()
            e.ignore()
            return True
        return super().viewportEvent(e)

    def keyboardSearch(self, text: str):
        model = self.model()
        if model is None or not text:
            return
        if not hasattr(self, "_ks_timer"):
            self._ks_buf = ""
            self._ks_timer = QTimer(self)
            self._ks_timer.setSingleShot(True)
            self._ks_timer.timeout.connect(lambda: setattr(self, "_ks_buf", ""))
        self._ks_buf += text
        self._ks_timer.start(QApplication.keyboardInputInterval())
        buf = self._ks_buf.casefold()
        cur = self.currentIndex().row()
        n = model.rowCount()
        start = cur if len(self._ks_buf) > 1 else cur + 1
        for k in range(n):
            r = (start + k) % n if n else 0
            if self.cell_rect(r).isNull():
                continue
            if model.text(model.rows[r], "name").casefold().startswith(buf):
                idx = model.index(r, 0)
                self.selectionModel().setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.ClearAndSelect |
                                                      QItemSelectionModel.SelectionFlag.Rows)
                self.scrollTo(idx)
                return
