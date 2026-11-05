"""Smaller dialogs: Browse For Folder, Choose Details, Recycle Bin properties, Extract wizard,
Create Shortcut, Help and About."""
from __future__ import annotations

import os

from PySide6.QtCore import QDir, QModelIndex, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFileSystemModel, QGroupBox,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QRadioButton, QSpinBox, QStackedWidget, QTextBrowser,
                               QTreeView, QVBoxLayout, QWidget)

from .. import APP_NAME, VERSION, icons, places, trash, util
from ..i18n import T, strip_amp
from ..model import COLUMNS
from ..settings import settings


# --------------------------------------------------------------------------- Browse For Folder
class _FolderBrowser(QDialog):
    def __init__(self, parent, title, prompt, start, ok_text):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 480)
        v = QVBoxLayout(self)
        l = QLabel(prompt)
        l.setWordWrap(True)
        v.addWidget(l)
        self.fsm = QFileSystemModel(self)
        self.fsm.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot |
                           (QDir.Filter.Hidden if settings().get("show_hidden") else QDir.Filter(0)))
        self.fsm.setRootPath("/")
        self.tree = QTreeView()
        self.tree.setModel(self.fsm)
        for c in range(1, 4):
            self.tree.hideColumn(c)
        self.tree.setHeaderHidden(True)
        idx = self.fsm.index(start)
        self.tree.setCurrentIndex(idx)
        self.tree.scrollTo(idx)
        self.tree.expand(idx)
        v.addWidget(self.tree, 1)
        h = QHBoxLayout()
        h.addWidget(QLabel(T("folder_label")))
        self.edit = QLineEdit(start)
        h.addWidget(self.edit, 1)
        v.addLayout(h)
        self.tree.selectionModel().currentChanged.connect(lambda c, p: self.edit.setText(self.fsm.filePath(c)))
        bh = QHBoxLayout()
        mk = QPushButton(T("make_new_folder"))
        mk.clicked.connect(self._mkdir)
        bh.addWidget(mk)
        bh.addStretch(1)
        ok = QPushButton(ok_text)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton(T("cancel"))
        cancel.clicked.connect(self.reject)
        bh.addWidget(ok)
        bh.addWidget(cancel)
        v.addLayout(bh)

    def _mkdir(self):
        base = self.edit.text()
        if not os.path.isdir(base):
            return
        name = util.unique_name(base, T("new_folder_name"), True)
        name, ok = QInputDialog.getText(self, T("make_new_folder"), T("new_name"), text=name)
        if ok and name.strip():
            p = os.path.join(base, name.strip())
            try:
                os.mkdir(p)
                self.tree.setCurrentIndex(self.fsm.index(p))
            except OSError as e:
                QMessageBox.warning(self, T("make_new_folder"), str(e))

    def value(self) -> str:
        return self.edit.text()


def browse_for_folder(parent, title, prompt, start, ok_text) -> str | None:
    d = _FolderBrowser(parent, title, prompt, start, ok_text)
    if d.exec() and os.path.isdir(d.value()):
        return d.value()
    return None


# --------------------------------------------------------------------------- Choose Details
class ChooseDetailsDialog(QDialog):
    def __init__(self, parent, columns: list[str], widths: dict, ftype: str):
        super().__init__(parent)
        self.setWindowTitle(T("choose_details"))
        self.resize(380, 470)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(T("choose_details_text")))
        v.addWidget(QLabel(T("details_label")))
        h = QHBoxLayout()
        self.list = QListWidget()
        allc = list(columns) + [c for c in COLUMNS if c not in columns]
        for c in allc:
            it = QListWidgetItem(T(COLUMNS[c][0]))
            it.setData(Qt.ItemDataRole.UserRole, c)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if c in columns else Qt.CheckState.Unchecked)
            if c == "name":
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.list.addItem(it)
        h.addWidget(self.list, 1)
        bv = QVBoxLayout()
        for text, fn in ((T("move_up"), lambda: self._move(-1)), (T("move_down"), lambda: self._move(1)),
                         (T("show"), lambda: self._check(True)), (T("hide"), lambda: self._check(False))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bv.addWidget(b)
        bv.addStretch(1)
        h.addLayout(bv)
        v.addLayout(h, 1)
        wh = QHBoxLayout()
        wh.addWidget(QLabel(T("width_of_column")))
        self.width = QSpinBox()
        self.width.setRange(20, 2000)
        self.width.setSuffix(" px")
        wh.addWidget(self.width)
        wh.addStretch(1)
        v.addLayout(wh)
        self.widths = dict(widths)
        self.list.currentItemChanged.connect(self._cur)
        self.width.valueChanged.connect(self._wchg)
        self.list.setCurrentRow(0)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _cur(self, it, prev):
        if it:
            c = it.data(Qt.ItemDataRole.UserRole)
            self.width.blockSignals(True)
            self.width.setValue(int(self.widths.get(c, COLUMNS[c][2])))
            self.width.blockSignals(False)

    def _wchg(self, v):
        it = self.list.currentItem()
        if it:
            self.widths[it.data(Qt.ItemDataRole.UserRole)] = v

    def _move(self, d):
        r = self.list.currentRow()
        n = r + d
        if r < 0 or n < 0 or n >= self.list.count():
            return
        it = self.list.takeItem(r)
        self.list.insertItem(n, it)
        self.list.setCurrentRow(n)

    def _check(self, on):
        it = self.list.currentItem()
        if it and it.data(Qt.ItemDataRole.UserRole) != "name":
            it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def result_columns(self):
        cols = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.CheckState.Checked or it.data(Qt.ItemDataRole.UserRole) == "name":
                cols.append(it.data(Qt.ItemDataRole.UserRole))
        return cols, {c: self.widths.get(c, COLUMNS[c][2]) for c in cols}


# --------------------------------------------------------------------------- Recycle Bin properties
class TrashPropertiesDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(T("trash_props_title"))
        v = QVBoxLayout(self)
        v.addWidget(QLabel(T("trash_location")))
        self.list = QListWidget()
        for td, top in trash.trash_dirs():
            d = places.drives().for_path(td)
            label = places.drive_title(d) if d else td
            n = 0
            try:
                n = len(os.listdir(os.path.join(td, "files")))
            except OSError:
                pass
            free = util.fmt_size_short(d["free"]) if d and d.get("free") is not None else ""
            it = QListWidgetItem(icons.trash(n > 0), f"{label}    {T('n_items', n)}    {free}")
            self.list.addItem(it)
        v.addWidget(self.list)
        gb = QGroupBox(T("settings_selected_location"))
        g = QVBoxLayout(gb)
        self.r_trash = QRadioButton(T("use_recycle_bin"))
        self.r_del = QRadioButton(T("dont_move_to_bin"))
        (self.r_trash if settings().get("use_trash") else self.r_del).setChecked(True)
        g.addWidget(self.r_trash)
        g.addWidget(self.r_del)
        v.addWidget(gb)
        self.confirm = QCheckBox(T("display_delete_confirm"))
        self.confirm.setChecked(settings().get("confirm_delete"))
        v.addWidget(self.confirm)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.resize(420, 380)

    def _ok(self):
        settings().set("use_trash", self.r_trash.isChecked())
        settings().set("confirm_delete", self.confirm.isChecked())
        self.accept()


# --------------------------------------------------------------------------- Extract wizard
class ExtractDialog(QDialog):
    def __init__(self, parent, zp: str):
        super().__init__(parent)
        self.setWindowTitle(T("extract_title"))
        self.resize(520, 260)
        v = QVBoxLayout(self)
        h = QLabel(T("extract_heading"))
        f = h.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)
        h.setFont(f)
        h.setStyleSheet("color: #1e3287;")
        v.addWidget(h)
        v.addSpacing(10)
        v.addWidget(QLabel(T("extract_to")))
        row = QHBoxLayout()
        from .. import ziputil
        base = os.path.join(os.path.dirname(zp), ziputil.base_name(zp))
        self.dest = QLineEdit(base)
        row.addWidget(self.dest, 1)
        b = QPushButton(T("browse_btn"))
        b.clicked.connect(self._browse)
        row.addWidget(b)
        v.addLayout(row)
        self.show_cb = QCheckBox(T("show_extracted"))
        self.show_cb.setChecked(True)
        v.addWidget(self.show_cb)
        v.addStretch(1)
        bh = QHBoxLayout()
        bh.addStretch(1)
        ok = QPushButton(T("extract_btn"))
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        c = QPushButton(T("cancel"))
        c.clicked.connect(self.reject)
        bh.addWidget(ok)
        bh.addWidget(c)
        v.addLayout(bh)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, T("extract_title"), os.path.dirname(self.dest.text()))
        if d:
            self.dest.setText(d)

    def result_values(self):
        return self.dest.text().strip(), self.show_cb.isChecked()


# --------------------------------------------------------------------------- Add to archive
class AddToArchiveDialog(QDialog):
    def __init__(self, parent, paths: list[str], folder: str):
        super().__init__(parent)
        from PySide6.QtWidgets import QComboBox, QFormLayout
        from .. import ziputil
        self.paths = paths
        self.folder = folder
        self.setWindowTitle(T("add_to_archive_title"))
        self.resize(500, 200)
        v = QVBoxLayout(self)
        h = QLabel(T("add_to_archive_heading"))
        f = h.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        h.setFont(f)
        h.setStyleSheet("color: #1e3287;")
        v.addWidget(h)
        form = QFormLayout()
        base = os.path.basename(paths[0].rstrip("/"))
        if len(paths) == 1 and not os.path.isdir(paths[0]):
            base = util.split_ext(base)[0]
        self.base = base
        self.name = QLineEdit(base)
        form.addRow(T("archive_name"), self.name)
        self.fmt = QComboBox()
        fmts = list(ziputil.CREATE_FORMATS)
        if len(paths) == 1 and os.path.isfile(paths[0]):
            fmts += ziputil.SINGLE_FORMATS
        for k in fmts:
            self.fmt.addItem(T("fmt_" + k.replace(".", "_")), k)
        form.addRow(T("archive_format"), self.fmt)
        row = QHBoxLayout()
        self.where = QLineEdit(folder)
        row.addWidget(self.where, 1)
        b = QPushButton(T("browse_btn"))
        b.clicked.connect(self._browse)
        row.addWidget(b)
        lbl = QLabel(T("archive_location"))
        lbl.setBuddy(self.where)
        form.addRow(lbl, row)
        v.addLayout(form)
        self.preview = QLabel()
        self.preview.setStyleSheet("color: #6d6d6d;")
        v.addWidget(self.preview)
        self.name.textChanged.connect(self._upd)
        self.fmt.currentIndexChanged.connect(self._upd)
        self.where.textChanged.connect(self._upd)
        self._upd()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, strip_amp(T("archive_location")).rstrip(":"), self.where.text())
        if d:
            self.where.setText(d)

    def _target(self) -> str:
        from .. import ziputil
        return os.path.join(self.where.text().strip(), self.name.text().strip() + ziputil.FORMAT_EXT[self.fmt.currentData()])

    def _upd(self):
        self.preview.setText(self._target())

    def _ok(self):
        t = self._target()
        if not self.name.text().strip() or "/" in self.name.text() or not os.path.isdir(self.where.text().strip()):
            return
        if os.path.exists(t):
            if QMessageBox.question(self, T("add_to_archive_title"), T("archive_exists", os.path.basename(t))) \
                    != QMessageBox.StandardButton.Yes:
                return
        self.accept()

    def result_values(self):
        return self._target(), self.fmt.currentData()


# --------------------------------------------------------------------------- Create Shortcut
def new_shortcut_wizard(parent, folder: str) -> str | None:
    dlg = QDialog(parent)
    dlg.setWindowTitle(T("create_shortcut_title"))
    dlg.resize(520, 240)
    v = QVBoxLayout(dlg)
    stack = QStackedWidget()
    v.addWidget(stack)
    p1 = QWidget()
    l1 = QVBoxLayout(p1)
    h = QLabel(T("shortcut_what"))
    f = h.font()
    f.setPointSizeF(f.pointSizeF() * 1.25)
    h.setFont(f)
    h.setStyleSheet("color: #1e3287;")
    l1.addWidget(h)
    l1.addWidget(QLabel(T("shortcut_type_location")))
    row = QHBoxLayout()
    target = QLineEdit()
    row.addWidget(target, 1)
    b = QPushButton(T("browse_btn"))

    def browse():
        f_, _ = QFileDialog.getOpenFileName(dlg, T("browse_btn"), folder)
        if f_:
            target.setText(f_)
    b.clicked.connect(browse)
    row.addWidget(b)
    l1.addLayout(row)
    l1.addStretch(1)
    p2 = QWidget()
    l2 = QVBoxLayout(p2)
    h2 = QLabel(T("shortcut_name_q"))
    h2.setFont(f)
    h2.setStyleSheet("color: #1e3287;")
    l2.addWidget(h2)
    l2.addWidget(QLabel(T("shortcut_type_name")))
    name = QLineEdit()
    l2.addWidget(name)
    l2.addStretch(1)
    stack.addWidget(p1)
    stack.addWidget(p2)
    bh = QHBoxLayout()
    bh.addStretch(1)
    nxt = QPushButton(T("next_btn"))
    cancel = QPushButton(T("cancel"))
    bh.addWidget(nxt)
    bh.addWidget(cancel)
    v.addLayout(bh)
    result = {}

    def on_next():
        if stack.currentIndex() == 0:
            t = os.path.expanduser(target.text().strip())
            if not t or not os.path.exists(t):
                QMessageBox.warning(dlg, T("create_shortcut_title"), T("cant_find", t))
                return
            name.setText(os.path.basename(t.rstrip("/")) or t)
            stack.setCurrentIndex(1)
            nxt.setText(T("finish_btn"))
        else:
            n = name.text().strip()
            if not n or "/" in n:
                return
            out = os.path.join(folder, util.unique_name(folder, n))
            try:
                os.symlink(os.path.expanduser(target.text().strip()), out)
                result["p"] = out
                dlg.accept()
            except OSError as e:
                QMessageBox.warning(dlg, T("create_shortcut_title"), str(e))
    nxt.clicked.connect(on_next)
    cancel.clicked.connect(dlg.reject)
    dlg.exec()
    return result.get("p")


# --------------------------------------------------------------------------- Help / About
class HelpDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(T("help_title"))
        self.resize(620, 560)
        v = QVBoxLayout(self)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setHtml(T("help_html"))
        v.addWidget(tb)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)


def about(parent):
    box = QMessageBox(parent)
    box.setWindowTitle(T("m_about").replace("&", ""))
    box.setIconPixmap(icons.app_icon().pixmap(64, 64))
    box.setText(f"<b style='font-size:14pt'>{APP_NAME}</b><br>{T('version')} {VERSION}")
    box.setInformativeText(T("about_text"))
    box.exec()
