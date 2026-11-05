"""'Open with' dialog: Recommended Programs / Other Programs / Always use the selected program."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from .. import icons, mime, util
from ..i18n import T


class OpenWithDialog(QDialog):
    def __init__(self, parent, path: str, set_default_only: bool = False):
        super().__init__(parent)
        self.path = path
        self.set_default_only = set_default_only
        self.mt = mime.mime_for(path)
        self.setWindowTitle(T("open_with_title"))
        self.resize(520, 520)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(T("choose_program")))
        h = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.for_mime(self.mt).pixmap(32, 32))
        h.addWidget(ic)
        h.addWidget(QLabel(T("file_label") + " " + os.path.basename(path)), 1)
        v.addLayout(h)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(T("search_programs"))
        self.filter.textChanged.connect(self._filter)
        v.addWidget(self.filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(icons.QSize(24, 24) if hasattr(icons, "QSize") else self.tree.iconSize())
        self.tree.itemDoubleClicked.connect(lambda i, c: self._ok() if i.data(0, Qt.ItemDataRole.UserRole) else None)
        v.addWidget(self.tree, 1)
        rec = QTreeWidgetItem([T("recommended_programs")])
        oth = QTreeWidgetItem([T("other_programs")])
        for top in (rec, oth):
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(top)
        recommended = mime.apps_for(self.mt)
        default = mime.default_app(self.mt)
        ids = set()
        first = None
        for app in ([default] if default else []) + recommended:
            if app is None or app.id in ids:
                continue
            ids.add(app.id)
            it = QTreeWidgetItem([app.name])
            it.setIcon(0, app.qicon())
            it.setData(0, Qt.ItemDataRole.UserRole, app.id)
            it.setToolTip(0, app.comment or app.exec)
            rec.addChild(it)
            if first is None:
                first = it
        for did, app in sorted(mime.all_apps().items(), key=lambda kv: kv[1].name.casefold()):
            if did in ids or app.nodisplay:
                continue
            it = QTreeWidgetItem([app.name])
            it.setIcon(0, app.qicon())
            it.setData(0, Qt.ItemDataRole.UserRole, did)
            it.setToolTip(0, app.comment or app.exec)
            oth.addChild(it)
        rec.setExpanded(True)
        oth.setExpanded(not recommended)
        if first is not None:
            self.tree.setCurrentItem(first)
        self.always = QCheckBox(T("always_use"))
        self.always.setChecked(set_default_only)
        v.addWidget(self.always)
        h2 = QHBoxLayout()
        b = QPushButton(T("browse_btn"))
        b.clicked.connect(self._browse)
        h2.addStretch(1)
        h2.addWidget(b)
        v.addLayout(h2)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _filter(self, text):
        t = text.casefold()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for k in range(top.childCount()):
                c = top.child(k)
                c.setHidden(bool(t) and t not in c.text(0).casefold())
            if t:
                top.setExpanded(True)

    def _browse(self):
        f, _ = QFileDialog.getOpenFileName(self, T("open_with_title"), "/usr/bin")
        if f and os.access(f, os.X_OK):
            util.spawn([f, self.path])
            self.accept()

    def _ok(self):
        it = self.tree.currentItem()
        did = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        if not did:
            return
        app = mime.app_by_id(did)
        if app is None:
            return
        if self.always.isChecked():
            try:
                mime.set_default_app(self.mt, app)
            except OSError:
                pass
        if not self.set_default_only:
            app.launch([self.path])
        self.accept()
