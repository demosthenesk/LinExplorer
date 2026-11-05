"""Library properties / 'Documents Library Locations' dialog."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from .. import icons, locations, places, util
from ..i18n import T


class LibraryDialog(QDialog):
    def __init__(self, parent, lib: dict):
        super().__init__(parent)
        self.lib = dict(lib)
        self.lib["locations"] = list(lib.get("locations", []))
        L = places.libraries()
        self.setWindowTitle(T("library_props_title", L.title(lib)))
        self.resize(440, 500)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(T("library_locations")))
        self.list = QListWidget()
        v.addWidget(self.list, 1)
        h = QHBoxLayout()
        self.b_save = QPushButton(T("set_save_location"))
        self.b_add = QPushButton(T("include_folder"))
        self.b_rem = QPushButton(T("remove_btn"))
        for b in (self.b_save, self.b_add, self.b_rem):
            h.addWidget(b)
        v.addLayout(h)
        self.b_save.clicked.connect(self._set_save)
        self.b_add.clicked.connect(self._add)
        self.b_rem.clicked.connect(self._remove)
        v.addWidget(QLabel(T("optimize_library_for")))
        self.combo = QComboBox()
        kinds = ["general", "documents", "pictures", "music", "videos"]
        for k in kinds:
            self.combo.addItem(T("ft_" + k), k)
        self.combo.setCurrentIndex(kinds.index(self.lib.get("type", "general")) if self.lib.get("type") in kinds else 0)
        v.addWidget(self.combo)
        self.size_lbl = QLabel()
        v.addWidget(self.size_lbl)
        self.nav = QCheckBox(T("shown_in_nav"))
        self.nav.setChecked(self.lib.get("nav", True))
        v.addWidget(self.nav)
        h2 = QHBoxLayout()
        h2.addStretch(1)
        rd = QPushButton(T("restore_defaults"))
        rd.clicked.connect(self._restore)
        h2.addWidget(rd)
        v.addLayout(h2)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._fill()

    def _fill(self):
        self.list.clear()
        save = self.lib.get("save")
        total = 0
        for p in self.lib["locations"]:
            it = QListWidgetItem(locations.icon(p), (p + "   " + T("save_location_tag")) if p == save else p)
            if p == save:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            if not os.path.isdir(p):
                it.setForeground(Qt.GlobalColor.gray)
            it.setData(Qt.ItemDataRole.UserRole, p)
            self.list.addItem(it)
        self.size_lbl.setText("")

    def _cur(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _set_save(self):
        p = self._cur()
        if p:
            self.lib["save"] = p
            self._fill()

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, T("include_folder"), places.HOME)
        if d and d not in self.lib["locations"]:
            self.lib["locations"].append(d)
            if not self.lib.get("save"):
                self.lib["save"] = d
            self._fill()

    def _remove(self):
        p = self._cur()
        if p:
            self.lib["locations"].remove(p)
            if self.lib.get("save") == p:
                self.lib["save"] = self.lib["locations"][0] if self.lib["locations"] else ""
            self._fill()

    def _restore(self):
        places.libraries().restore_defaults()
        cur = places.libraries().get(self.lib["name"])
        if cur:
            self.lib = dict(cur)
            self.lib["locations"] = list(cur["locations"])
        self._fill()

    def _ok(self):
        self.lib["type"] = self.combo.currentData()
        self.lib["nav"] = self.nav.isChecked()
        places.libraries().save(self.lib)
        self.accept()
