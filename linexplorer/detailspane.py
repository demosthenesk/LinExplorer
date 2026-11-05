"""Details pane (bottom): thumbnail, name, type and properties, with tag editing."""
from __future__ import annotations

import os

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import icons, metadata, places, thumbs, util
from .i18n import T


class Stars(QWidget):
    changed = Signal(int)

    def __init__(self, value: int = 0, editable: bool = False, parent=None):
        super().__init__(parent)
        self.value = int(value or 0)
        self.editable = editable
        self._hover = -1
        self.setFixedSize(5 * 15 + 2, 16)
        self.setMouseTracking(editable)
        if editable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        v = self._hover if self._hover >= 0 else self.value
        on, off = icons.glyph_star(True, 13), icons.glyph_star(False, 13)
        for i in range(5):
            p.drawPixmap(i * 15, 1, on if i < v else off)

    def mouseMoveEvent(self, e):
        if self.editable:
            self._hover = min(5, int(e.position().x() // 15) + 1)
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        if self.editable:
            n = min(5, int(e.position().x() // 15) + 1)
            self.value = 0 if n == self.value else n
            self.changed.emit(self.value)
            self.update()


class _Value(QLineEdit):
    """A value that looks like a label until hovered/clicked (Windows 7 details pane)."""

    def __init__(self, text: str, editable: bool):
        super().__init__(text)
        self.setReadOnly(not editable)
        self.editable = editable
        self.setCursorPosition(0)
        self.setFrame(False)
        self.setMinimumWidth(60)
        self.setStyleSheet("QLineEdit { background: transparent; border: 1px solid transparent; color: #1e1e1e;"
                           " padding: 0 2px; }"
                           + ("QLineEdit:hover { border: 1px solid #b6c9e0; background: white; }"
                              "QLineEdit:focus { border: 1px solid #5d7fa8; background: white; }" if editable else ""))


class DetailsPane(QFrame):
    save_requested = Signal(object, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(14)
        self.icon = QLabel()
        self.icon.setFixedSize(64, 64)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        nv = QVBoxLayout()
        nv.setSpacing(0)
        self.name = QLabel()
        f = self.name.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        self.name.setFont(f)
        self.name.setStyleSheet("color: #1e1e1e;")
        self.type = QLabel()
        self.type.setStyleSheet("color: #6d6d6d;")
        nv.addStretch(1)
        nv.addWidget(self.name)
        nv.addWidget(self.type)
        nv.addStretch(1)
        self.namebox = QWidget()
        self.namebox.setLayout(nv)
        self.namebox.setMinimumWidth(160)
        self.namebox.setMaximumWidth(320)
        lay.addWidget(self.namebox, 0)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(1)
        lay.addWidget(self.grid_host, 1)
        bv = QVBoxLayout()
        bv.addStretch(1)
        self.save_btn = QPushButton(T("save"))
        self.cancel_btn = QPushButton(T("cancel"))
        self.save_btn.clicked.connect(self._save)
        self.cancel_btn.clicked.connect(self._cancel)
        bv.addWidget(self.save_btn)
        bv.addWidget(self.cancel_btn)
        self.save_btn.hide()
        self.cancel_btn.hide()
        lay.addLayout(bv)
        self._rows: list[tuple[str, QWidget]] = []
        self._item = None
        self._edits: dict = {}
        self._orig: dict = {}

    def paintEvent(self, e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(245, 249, 253))
        g.setColorAt(1, QColor(226, 235, 247))
        p.fillRect(self.rect(), g)
        p.setPen(QColor(205, 218, 234))
        p.drawLine(0, 0, self.width(), 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()
        big = max(32, min(128, self.height() - 14))
        self.icon.setFixedSize(big, big)
        if hasattr(self, "_pm_src") and self._pm_src is not None:
            self._set_pm(self._pm_src)

    def _set_pm(self, pm: QPixmap):
        self._pm_src = pm
        s = self.icon.size()
        if pm.width() > s.width() or pm.height() > s.height():
            pm = pm.scaled(s, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.icon.setPixmap(pm)

    def _clear_grid(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self._rows = []
        self._edits = {}
        self._orig = {}
        self.save_btn.hide()
        self.cancel_btn.hide()

    def _add(self, label: str, value, key: str | None = None, editable: bool = False):
        if value in (None, "") and not editable:
            return
        lab = QLabel(label + ":")
        lab.setStyleSheet("color: #6d6d6d;")
        lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if isinstance(value, QWidget):
            w = value
        else:
            w = _Value(str(value) if value is not None else "", editable)
            if editable:
                w.setPlaceholderText(T("add_a_tag") if key == "tags" else T("specify_" + (key or "x")))
                self._orig[key] = str(value or "")
                w.textEdited.connect(lambda t, k=key: self._edited(k, t))
        self._rows.append((lab, w))

    def _edited(self, key, value):
        self._edits[key] = value
        self.save_btn.show()
        self.cancel_btn.show()

    def _relayout(self):
        for lab, w in self._rows:
            self.grid.removeWidget(lab)
            self.grid.removeWidget(w)
        if not self._rows:
            return
        lh = max(18, self.fontMetrics().height() + 4)
        per_col = max(1, (self.height() - 10) // lh)
        for i, (lab, w) in enumerate(self._rows):
            c, r = divmod(i, per_col)
            if c > 3:
                lab.hide()
                w.hide()
                continue
            lab.show()
            w.show()
            self.grid.addWidget(lab, r, c * 2)
            self.grid.addWidget(w, r, c * 2 + 1)
        for c in range(8):
            self.grid.setColumnStretch(c, 1 if c % 2 else 0)
        self.grid.setRowStretch(per_col, 1)

    # ------------------------------------------------------------------ content
    def show_folder(self, title: str, icon, count: int, extra: list[tuple[str, str]] | None = None):
        self._item = None
        self._clear_grid()
        self._set_pm(icon.pixmap(64, 64))
        self.name.setText(T("one_item") if count == 1 else T("n_items", util.fmt_int(count)))
        self.type.setText("")
        for k, v in (extra or []):
            self._add(k, v)
        self._relayout()

    def show_items(self, model, items: list):
        self._clear_grid()
        self._item = items[0] if len(items) == 1 else None
        if len(items) > 1:
            self._set_pm(icons.theme("edit-copy", "folder", fallback="file").pixmap(64, 64)
                         if any(not i.is_dir for i in items) else icons.folder().pixmap(64, 64))
            self.name.setText(T("n_items_selected", util.fmt_int(len(items))))
            self.type.setText("")
            ms = [i.mtime for i in items if i.mtime]
            if ms:
                a, b = min(ms), max(ms)
                self._add(T("col_date_modified"), util.fmt_datetime(a) if a == b else
                          f"{util.fmt_datetime(a)} - {util.fmt_datetime(b)}")
            sizes = [i.size for i in items if not i.is_dir and i.size is not None]
            if sizes:
                self._add(T("col_size"), util.fmt_size_short(sum(sizes)))
            self._relayout()
            return
        it = items[0]
        self.name.setText(model.display_name(it))
        self.type.setText(it.type_text())
        pm = None
        if it.kind in ("file", "trash") and not it.is_dir:
            pm = thumbs.service().get(it.path, it.mtime, 128)
        if pm is None:
            ic = model.icon(it)
            pm = ic.pixmap(QSize(64, 64))
            if it.is_link:
                pm = icons.with_link_overlay(pm)
        self._set_pm(pm)
        if it.kind == "drive":
            d = it.extra.get("drive") or {}
            if d.get("size") and d.get("mount"):
                bar = _CapBar(d)
                self._add(T("space_used"), bar)
                self._add(T("col_free_space"), util.fmt_size_short(d.get("free") or 0))
                self._add(T("col_total_size"), util.fmt_size_short(d["size"]))
            elif d.get("size"):
                self._add(T("col_total_size"), util.fmt_size_short(d["size"]))
                self._add(T("status"), T("not_mounted"))
            self._add(T("col_filesystem"), (d.get("fstype") or "").upper())
            if d.get("model"):
                self._add(T("device"), d["model"])
            self._relayout()
            return
        if it.kind == "library":
            lib = it.extra.get("library") or {}
            self._add(T("includes"), T("n_locations", len(lib.get("locations", []))))
            self._relayout()
            return
        p = it.props() if it.kind in ("file", "trash", "zipfile") else {}
        edit = metadata.can_edit_tags(it)
        if it.kind == "trash":
            self._add(T("col_orig_location"), it.extra.get("orig_dir"))
            self._add(T("col_date_deleted"), util.fmt_datetime(it.extra.get("deleted")))
        mt = it.mime.name() if not it.is_dir else ""
        if edit:
            for key in ("title", "artists", "album", "year", "genre", "track"):
                self._add(T("col_" + key), p.get(key, ""), key, True)
            stars = Stars(p.get("rating") or 0, True)
            stars.changed.connect(lambda v: self._edited("rating", v))
            self._orig["rating"] = p.get("rating") or 0
            self._add(T("col_rating"), stars)
            self._add(T("col_length"), p.get("length"))
            self._add(T("col_bitrate"), p.get("bitrate"))
        else:
            if mt.startswith("image/"):
                self._add(T("col_date_taken"), p.get("date_taken"))
                self._add(T("col_dimensions"), p.get("dimensions"))
                self._add(T("col_camera_maker"), p.get("camera_maker"))
                self._add(T("col_camera_model"), p.get("camera_model"))
            for key in ("title", "authors", "subject", "pages", "artists", "album", "length", "bitrate",
                        "frame_width", "frame_height", "program"):
                self._add(T("col_" + key), p.get(key))
        if it.mtime:
            self._add(T("col_date_modified"), util.fmt_datetime(it.mtime))
        if not it.is_dir and it.size is not None:
            self._add(T("col_size"), util.fmt_size_short(it.size))
        if it.kind in ("file", "dir"):
            self._add(T("col_date_created"), util.fmt_datetime(it.btime))
        if it.kind == "zipfile":
            self._add(T("col_compressed_size"), util.fmt_size_short(it.extra.get("csize") or 0))
        if it.is_link:
            self._add(T("col_link_target"), it.link)
        self._relayout()

    def _save(self):
        if self._item is not None and self._edits:
            self.save_requested.emit(self._item, dict(self._edits))
        self._edits = {}
        self.save_btn.hide()
        self.cancel_btn.hide()

    def _cancel(self):
        self._edits = {}
        self.save_btn.hide()
        self.cancel_btn.hide()
        for lab, w in self._rows:
            if isinstance(w, _Value) and w.editable:
                pass
        if self._item is not None:
            self._item.invalidate()


class _CapBar(QWidget):
    def __init__(self, d):
        super().__init__()
        self.d = d
        self.setFixedSize(150, 14)

    def paintEvent(self, e):
        d = self.d
        size = d.get("size") or 1
        used = d.get("used") if d.get("used") is not None else size - (d.get("free") or 0)
        frac = max(0.0, min(1.0, used / size))
        p = QPainter(self)
        r = self.rect().adjusted(0, 1, -1, -2)
        p.setPen(QPen(QColor(150, 160, 170)))
        p.setBrush(QColor(230, 230, 230))
        p.drawRect(r)
        fr = QRect(r.left() + 1, r.top() + 1, int((r.width() - 1) * frac), r.height() - 1)
        g = QLinearGradient(0, fr.top(), 0, fr.bottom())
        if frac > 0.9:
            g.setColorAt(0, QColor(240, 120, 120))
            g.setColorAt(1, QColor(200, 30, 30))
        else:
            g.setColorAt(0, QColor(110, 190, 245))
            g.setColorAt(1, QColor(38, 160, 218))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRect(fr)
