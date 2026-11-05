"""Properties dialogs (Alt+Enter) for files, folders, multiple items, drives and Recycle Bin items."""
from __future__ import annotations

import grp
import os
import pwd
import socket
import stat
import threading

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QRadioButton, QSizePolicy, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .. import icons, metadata, mime, places, sharing, thumbs, trash, undo, util, ziputil
from ..i18n import T
from ..settings import settings

_open: list = []


def show_properties(win, items: list, model=None, tab: str | None = None):
    dlg = PropertiesDialog(win, items, model, tab)
    _open.append(dlg)
    dlg.finished.connect(lambda _r: _open.remove(dlg) if dlg in _open else None)
    dlg.show()
    return dlg


def _sep():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color: #dfdfdf;")
    return f


def _val(text: str = "") -> QLabel:
    l = QLabel(text)
    l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    l.setWordWrap(True)
    l.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return l


def _hidden_in(folder: str) -> set[str]:
    return util.read_dot_hidden(folder)


def _set_hidden(path: str, hidden: bool):
    """Windows' Hidden attribute -> the parent folder's .hidden file (GNOME/KDE convention)."""
    folder, name = os.path.split(path.rstrip("/"))
    if name.startswith("."):
        return
    hfile = os.path.join(folder, ".hidden")
    names = _hidden_in(folder)
    if hidden:
        names.add(name)
    else:
        names.discard(name)
    try:
        if names:
            with open(hfile, "w", encoding="utf-8") as f:
                f.write("\n".join(sorted(names)) + "\n")
        elif os.path.exists(hfile):
            os.remove(hfile)
    except OSError:
        pass


class _Counter(QObject):
    update = Signal(int, int, int, int, bool)     # bytes, ondisk, files, folders, done


def count_async(paths: list[str], counter: _Counter, cancel: threading.Event):
    def work():
        total = disk = files = folders = 0
        last = 0
        import time
        for p in paths:
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                for root, dirs, fs in os.walk(p):
                    if cancel.is_set():
                        return
                    folders += len(dirs)
                    for f in fs:
                        try:
                            s = os.lstat(os.path.join(root, f))
                        except OSError:
                            continue
                        files += 1
                        total += s.st_size
                        disk += s.st_blocks * 512
                    if time.time() - last > 0.25:
                        last = time.time()
                        counter.update.emit(total, disk, files, folders, False)
            else:
                files += 1
                total += st.st_size
                disk += st.st_blocks * 512
        counter.update.emit(total, disk, files, folders, True)
    util.in_thread(work)


class PropertiesDialog(QDialog):
    def __init__(self, win, items: list, model, tab: str | None):
        super().__init__(win)
        self.win = win
        self.items = items
        self.model = model
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._cancel = threading.Event()
        self._apply_fns = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        bb = QDialogButtonBox()
        self.ok = bb.addButton(T("ok"), QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(T("cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.apply_btn = bb.addButton(T("apply"), QDialogButtonBox.ButtonRole.ApplyRole)
        self.apply_btn.setEnabled(False)
        self.ok.clicked.connect(self._ok)
        bb.rejected.connect(self.reject)
        self.apply_btn.clicked.connect(self._apply)
        lay.addWidget(bb)
        self.setMinimumSize(400, 500)
        it = items[0]
        if len(items) > 1:
            self.setWindowTitle(T("props_multi_title", util.fmt_int(len(items))))
            self.tabs.addTab(self._general_multi(items), T("tab_general"))
        elif it.kind == "drive":
            self.setWindowTitle(T("props_title", it.name))
            self._drive_tabs(it)
        elif it.kind == "trash":
            self.setWindowTitle(T("props_title", it.name))
            self.tabs.addTab(self._general_trash(it), T("tab_general"))
            if not it.is_dir:
                self.tabs.addTab(self._details_tab(it), T("tab_details"))
        elif it.kind in ("zipfile", "zipdir"):
            self.setWindowTitle(T("props_title", it.name))
            self.tabs.addTab(self._general_zip(it), T("tab_general"))
        else:
            name = model.display_name(it) if model else it.name
            self.setWindowTitle(T("props_title", name))
            if it.is_dir:
                self.tabs.addTab(self._general_folder(it), T("tab_general"))
                if sharing.available():
                    self.tabs.addTab(self._sharing_tab(it), T("tab_sharing"))
                self.tabs.addTab(self._security_tab(it), T("tab_security"))
                self.tabs.addTab(self._customize_tab(it), T("tab_customize"))
            else:
                self.tabs.addTab(self._general_file(it), T("tab_general"))
                self.tabs.addTab(self._security_tab(it), T("tab_security"))
                self.tabs.addTab(self._details_tab(it), T("tab_details"))
            if it.is_link:
                self.tabs.insertTab(0, self._shortcut_tab(it), T("tab_shortcut"))
                self.tabs.setCurrentIndex(0)
        if tab:
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i).lower() == T("tab_" + tab).lower():
                    self.tabs.setCurrentIndex(i)

    def changed(self, *a):
        self.apply_btn.setEnabled(True)

    def _ok(self):
        if self.apply_btn.isEnabled():
            if not self._apply():
                return
        self.accept()

    def _apply(self) -> bool:
        for fn in self._apply_fns:
            try:
                if fn() is False:
                    return False
            except OSError as e:
                QMessageBox.warning(self, T("apply_props"), str(e))
                return False
        self.apply_btn.setEnabled(False)
        if self.win is not None:
            self.win.model.reload()
        return True

    def done(self, r):
        self._cancel.set()
        super().done(r)

    # ------------------------------------------------------------------ helpers
    def _grid(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(10, 12, 10, 10)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(7)
        g.setColumnMinimumWidth(0, 90)
        g.setColumnStretch(1, 1)
        return w, g

    def _row(self, g, r, label, widget):
        l = QLabel(label + ":")
        g.addWidget(l, r, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        g.addWidget(widget if isinstance(widget, QWidget) else _val(widget), r, 1)

    def _header(self, g, pm: QPixmap, name: str, editable: bool, path: str | None):
        ic = QLabel()
        ic.setPixmap(pm)
        ic.setFixedSize(40, 40)
        g.addWidget(ic, 0, 0)
        e = QLineEdit(name)
        e.setReadOnly(not editable)
        g.addWidget(e, 0, 1)
        if editable and path:
            e.textEdited.connect(self.changed)

            def apply_name(e=e, path=path):
                new = e.text().strip()
                cur = os.path.basename(path)
                if not new or new == cur and self.model is None:
                    return
                shown = self.model.display_name(self.items[0]) if self.model else cur
                if new == shown:
                    return
                if "/" in new:
                    QMessageBox.warning(self, T("rename_title"), T("bad_chars"))
                    return False
                if shown != cur and not self.items[0].is_dir:
                    new = new + util.split_ext(cur)[1]
                dst = os.path.join(os.path.dirname(path), new)
                if os.path.lexists(dst):
                    QMessageBox.warning(self, T("rename_title"), T("name_exists"))
                    return False
                os.rename(path, dst)
                undo.manager().push("rename", {"old": path, "new": dst})
                self.items[0].path = dst
                self.items[0].name = new
            self._apply_fns.append(apply_name)
        return e

    def _icon_for(self, it, size=32) -> QPixmap:
        if it.kind in ("file", "trash") and not it.is_dir:
            pm = thumbs.service().get(it.path, it.mtime, 128)
            if pm is not None:
                return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if self.model is not None:
            return self.model.icon(it).pixmap(size, size)
        return icons.for_mime(it.mime).pixmap(size, size)

    def _attrs(self, g, r, paths: list[str], is_folder=False):
        box = QWidget()
        h = QVBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        ro = QCheckBox(T("attr_readonly") + (T("attr_readonly_folder") if is_folder else ""))
        hid = QCheckBox(T("attr_hidden"))
        ro_states = [not os.access(p, os.W_OK) for p in paths]
        hid_states = [os.path.basename(p).startswith(".") or os.path.basename(p) in _hidden_in(os.path.dirname(p))
                      for p in paths]
        for cb, states in ((ro, ro_states), (hid, hid_states)):
            if all(states):
                cb.setCheckState(Qt.CheckState.Checked)
            elif any(states):
                cb.setTristate(True)
                cb.setCheckState(Qt.CheckState.PartiallyChecked)
            cb.stateChanged.connect(self.changed)
            h.addWidget(cb)
        dot = all(os.path.basename(p).startswith(".") for p in paths)
        if dot:
            hid.setEnabled(False)
            hid.setToolTip(T("hidden_dot_tip"))
        self._row(g, r, T("attributes"), box)

        def apply_attrs():
            for p in paths:
                if ro.checkState() != Qt.CheckState.PartiallyChecked:
                    want_ro = ro.checkState() == Qt.CheckState.Checked
                    targets = [p]
                    if is_folder:
                        targets = [os.path.join(rt, f) for rt, _d, fs in os.walk(p) for f in fs]
                    for t in targets:
                        try:
                            m = os.stat(t).st_mode
                            os.chmod(t, (m & ~0o222) if want_ro else (m | stat.S_IWUSR))
                        except OSError:
                            pass
                if hid.isEnabled() and hid.checkState() != Qt.CheckState.PartiallyChecked:
                    _set_hidden(p, hid.checkState() == Qt.CheckState.Checked)
        self._apply_fns.append(apply_attrs)

    # ------------------------------------------------------------------ general: file
    def _general_file(self, it):
        w, g = self._grid()
        r = 0
        self._header(g, self._icon_for(it), self.model.display_name(it) if self.model else it.name,
                     os.access(os.path.dirname(it.path), os.W_OK), it.path)
        r += 1
        g.addWidget(_sep(), r, 0, 1, 2)
        r += 1
        _, ext = util.split_ext(it.name)
        self._row(g, r, T("type_of_file"), f"{it.type_text()} ({ext})" if ext else it.type_text())
        r += 1
        opens = QWidget()
        oh = QHBoxLayout(opens)
        oh.setContentsMargins(0, 0, 0, 0)
        app = mime.default_app(it.mime)
        ai = QLabel()
        if app:
            ai.setPixmap(app.qicon().pixmap(16, 16))
        oh.addWidget(ai)
        an = QLabel(app.name if app else T("unknown_application"))
        oh.addWidget(an, 1)
        ch = QPushButton(T("change_btn"))
        oh.addWidget(ch)

        def change():
            from .openwith import OpenWithDialog
            d = OpenWithDialog(self, it.path, set_default_only=True)
            if d.exec():
                a2 = mime.default_app(it.mime)
                if a2:
                    ai.setPixmap(a2.qicon().pixmap(16, 16))
                    an.setText(a2.name)
        ch.clicked.connect(change)
        self._row(g, r, T("opens_with"), opens)
        r += 1
        g.addWidget(_sep(), r, 0, 1, 2)
        r += 1
        st = os.lstat(it.path)
        self._row(g, r, T("location"), os.path.dirname(it.path))
        r += 1
        self._row(g, r, T("size"), util.fmt_size_long(it.size or 0))
        r += 1
        try:
            disk = os.stat(it.path).st_blocks * 512
        except OSError:
            disk = st.st_blocks * 512
        self._row(g, r, T("size_on_disk"), util.fmt_size_long(disk))
        r += 1
        g.addWidget(_sep(), r, 0, 1, 2)
        r += 1
        self._row(g, r, T("created"), util.fmt_datetime_long(it.btime))
        r += 1
        self._row(g, r, T("modified"), util.fmt_datetime_long(it.mtime))
        r += 1
        self._row(g, r, T("accessed"), util.fmt_datetime_long(it.atime))
        r += 1
        g.addWidget(_sep(), r, 0, 1, 2)
        r += 1
        self._attrs(g, r, [it.path])
        g.setRowStretch(r + 1, 1)
        return w

    # ------------------------------------------------------------------ general: folder
    def _general_folder(self, it):
        w, g = self._grid()
        r = 0
        kind = places.special_kind(it.path)
        self._header(g, self._icon_for(it), it.name if it.path != "/" else "/",
                     os.access(os.path.dirname(it.path) or "/", os.W_OK) and not kind, it.path)
        r += 1
        g.addWidget(_sep(), r, 0, 1, 2)
        r += 1
        self._row(g, r, T("type"), it.type_text())
        r += 1
        self._row(g, r, T("location"), os.path.dirname(it.path) or "/")
        r += 1
        size = _val(T("calculating"))
        disk = _val(T("calculating"))
        contains = _val(T("calculating"))
        self._row(g, r, T("size"), size)
        r += 1
        self._row(g, r, T("size_on_disk"), disk)
        r += 1
        self._row(g, r, T("contains"), contains)
        r += 1
        g.addWidget(_sep(), r, 0, 1, 2)
        r += 1
        self._row(g, r, T("created"), util.fmt_datetime_long(it.btime))
        r += 1
        g.addWidget(_sep(), r, 0, 1, 2)
        r += 1
        self._attrs(g, r, [it.path], True)
        g.setRowStretch(r + 1, 1)
        self._counter = _Counter()
        self._counter.update.connect(lambda b, d, f, fo, done: (
            size.setText(util.fmt_size_long(b)), disk.setText(util.fmt_size_long(d)),
            contains.setText(T("n_files_folders", util.fmt_int(f), util.fmt_int(fo)))))
        count_async([it.path], self._counter, self._cancel)
        return w

    # ------------------------------------------------------------------ general: many
    def _general_multi(self, items):
        w, g = self._grid()
        r = 0
        ic = QLabel()
        ic.setPixmap(icons.theme("edit-copy", "folder", fallback="file").pixmap(32, 32))
        g.addWidget(ic, 0, 0)
        files = sum(1 for i in items if not i.is_dir)
        folders = len(items) - files
        g.addWidget(_val(T("n_files_folders", util.fmt_int(files), util.fmt_int(folders))), 0, 1)
        r += 1
        g.addWidget(_sep(), r, 0, 1, 2)
        r += 1
        types = {i.type_text() for i in items}
        self._row(g, r, T("type"), T("all_of_type", types.pop()) if len(types) == 1 else T("multiple_types"))
        r += 1
        dirs = {i.extra.get("in_folder") or os.path.dirname(i.path) for i in items}
        self._row(g, r, T("location"), T("all_in", dirs.pop()) if len(dirs) == 1 else T("various_folders"))
        r += 1
        size = _val(T("calculating"))
        disk = _val(T("calculating"))
        self._row(g, r, T("size"), size)
        r += 1
        self._row(g, r, T("size_on_disk"), disk)
        r += 1
        g.addWidget(_sep(), r, 0, 1, 2)
        r += 1
        paths = [i.path for i in items if i.kind in ("file", "dir")]
        self._attrs(g, r, paths, folders > 0)
        g.setRowStretch(r + 1, 1)
        self._counter = _Counter()
        self._counter.update.connect(lambda b, d, f, fo, done: (size.setText(util.fmt_size_long(b)),
                                                                 disk.setText(util.fmt_size_long(d))))
        count_async(paths, self._counter, self._cancel)
        return w

    # ------------------------------------------------------------------ general: trash / zip
    def _general_trash(self, it):
        w, g = self._grid()
        e = it.extra.get("trash")
        self._header(g, self._icon_for(it), it.name, False, None)
        g.addWidget(_sep(), 1, 0, 1, 2)
        self._row(g, 2, T("type"), it.type_text())
        self._row(g, 3, T("origin"), it.extra.get("orig_dir", ""))
        self._row(g, 4, T("size"), util.fmt_size_long(trash.tree_size(it.path)))
        g.addWidget(_sep(), 5, 0, 1, 2)
        self._row(g, 6, T("deleted"), util.fmt_datetime_long(it.extra.get("deleted")))
        self._row(g, 7, T("modified"), util.fmt_datetime_long(it.mtime))
        g.setRowStretch(8, 1)
        return w

    def _general_zip(self, it):
        w, g = self._grid()
        self._header(g, self._icon_for(it), it.name, False, None)
        g.addWidget(_sep(), 1, 0, 1, 2)
        self._row(g, 2, T("type"), it.type_text())
        self._row(g, 3, T("location"), f"{it.extra.get('zip')}/{os.path.dirname(it.extra.get('member', ''))}")
        if not it.is_dir:
            self._row(g, 4, T("size"), util.fmt_size_long(it.size or 0))
            self._row(g, 5, T("col_compressed_size"), util.fmt_size_long(it.extra.get("csize") or 0))
            self._row(g, 6, T("col_ratio"), f"{it.extra.get('ratio', 0)}%")
        self._row(g, 7, T("modified"), util.fmt_datetime_long(it.mtime))
        g.setRowStretch(8, 1)
        return w

    # ------------------------------------------------------------------ shortcut (symlink)
    def _shortcut_tab(self, it):
        w, g = self._grid()
        self._header(g, self._icon_for(it), it.name, False, None)
        g.addWidget(_sep(), 1, 0, 1, 2)
        tgt = os.path.realpath(it.path)
        self._row(g, 2, T("target_type"), T("file_folder") if os.path.isdir(tgt) else
                  mime.type_description(mime.mime_for(tgt), tgt) if os.path.exists(tgt) else T("missing_target"))
        self._row(g, 3, T("target_location"), os.path.basename(os.path.dirname(tgt)))
        te = QLineEdit(it.link or "")
        te.textEdited.connect(self.changed)
        self._row(g, 4, T("target"), te)
        btn = QPushButton(T("open_file_location"))
        btn.clicked.connect(lambda: (self.win.navigate(os.path.dirname(tgt), select=[tgt]), self.accept()))
        h = QHBoxLayout()
        h.addWidget(btn)
        h.addStretch(1)
        g.addLayout(h, 5, 0, 1, 2)
        g.setRowStretch(6, 1)

        def apply_target():
            new = te.text().strip()
            if new and new != it.link:
                os.remove(it.path)
                os.symlink(new, it.path)
        self._apply_fns.append(apply_target)
        return w

    # ------------------------------------------------------------------ security
    def _security_tab(self, it):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 12, 10, 10)
        try:
            st = os.lstat(it.path) if not it.is_link else os.stat(it.path)
        except OSError:
            st = os.lstat(it.path)
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = str(st.st_uid)
        try:
            group = grp.getgrgid(st.st_gid).gr_name
        except KeyError:
            group = str(st.st_gid)
        v.addWidget(QLabel(T("object_name") + "  " + it.path))
        g = QGridLayout()
        g.addWidget(QLabel(T("owner") + ":"), 0, 0)
        g.addWidget(_val(owner), 0, 1)
        g.addWidget(QLabel(T("group") + ":"), 1, 0)
        g.addWidget(_val(group), 1, 1)
        v.addLayout(g)
        box = QGroupBox(T("permissions_for"))
        bg = QGridLayout(box)
        heads = [T("perm_read"), T("perm_write"), T("perm_execute") if not it.is_dir else T("perm_list")]
        for c, h in enumerate(heads):
            l = QLabel(h)
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bg.addWidget(l, 0, c + 1)
        mine = st.st_uid == os.getuid()
        checks = {}
        bits = {("u", 0): stat.S_IRUSR, ("u", 1): stat.S_IWUSR, ("u", 2): stat.S_IXUSR,
                ("g", 0): stat.S_IRGRP, ("g", 1): stat.S_IWGRP, ("g", 2): stat.S_IXGRP,
                ("o", 0): stat.S_IROTH, ("o", 1): stat.S_IWOTH, ("o", 2): stat.S_IXOTH}
        for r, (who, label) in enumerate((("u", T("owner") + f" ({owner})"), ("g", T("group") + f" ({group})"),
                                          ("o", T("others"))), start=1):
            bg.addWidget(QLabel(label), r, 0)
            for c in range(3):
                cb = QCheckBox()
                cb.setChecked(bool(st.st_mode & bits[(who, c)]))
                cb.setEnabled(mine)
                cb.stateChanged.connect(self.changed)
                bg.addWidget(cb, r, c + 1, Qt.AlignmentFlag.AlignCenter)
                checks[(who, c)] = cb
        v.addWidget(box)
        if not mine:
            n = QLabel(T("owner_only_note"))
            n.setWordWrap(True)
            n.setStyleSheet("color: #6d6d6d;")
            v.addWidget(n)
        recurse = None
        if it.is_dir and mine:
            recurse = QCheckBox(T("apply_to_enclosed"))
            recurse.stateChanged.connect(self.changed)
            v.addWidget(recurse)
        v.addStretch(1)

        def apply_perm():
            if not mine:
                return
            mode = st.st_mode & ~0o777
            for k, cb in checks.items():
                if cb.isChecked():
                    mode |= bits[k]
            os.chmod(it.path, mode & 0o7777)
            if recurse is not None and recurse.isChecked():
                fmode = mode & 0o666 & 0o7777
                for root, dirs, files in os.walk(it.path):
                    for d in dirs:
                        try:
                            os.chmod(os.path.join(root, d), mode & 0o7777)
                        except OSError:
                            pass
                    for f in files:
                        p = os.path.join(root, f)
                        try:
                            keepx = os.stat(p).st_mode & 0o111
                            os.chmod(p, fmode | (keepx & (mode & 0o111)))
                        except OSError:
                            pass
        self._apply_fns.append(apply_perm)
        return w

    # ------------------------------------------------------------------ details
    def _details_tab(self, it):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        tree = QTreeWidget()
        tree.setHeaderLabels([T("property"), T("value")])
        tree.setRootIsDecorated(False)
        tree.setColumnWidth(0, 150)
        p = it.props()
        editable = metadata.can_edit_tags(it)
        self._tag_edits = {}

        pending = {"sec": None}

        def section(title):
            s = QTreeWidgetItem([title])
            f = s.font(0)
            f.setBold(True)
            s.setFont(0, f)
            s.setForeground(0, QColor(30, 57, 145))
            pending["sec"] = s

        def row(label, value, key=None):
            if value in (None, "") and not (editable and key):
                return
            if pending["sec"] is not None:
                tree.addTopLevelItem(pending["sec"])
                pending["sec"].setFirstColumnSpanned(True)
                pending["sec"] = None
            i = QTreeWidgetItem([label, str(value if value is not None else "")])
            if editable and key:
                i.setFlags(i.flags() | Qt.ItemFlag.ItemIsEditable)
                i.setData(0, Qt.ItemDataRole.UserRole, key)
            tree.addTopLevelItem(i)

        section(T("sec_description"))
        row(T("col_title"), p.get("title"), "title")
        row(T("col_subject"), p.get("subject"))
        if editable or p.get("rating"):
            row(T("col_rating"), "★" * int(p.get("rating") or 0) or T("unrated"))
        row(T("col_tags"), p.get("tags"))
        row(T("col_comments"), p.get("comments"), "comments")
        section(T("sec_origin"))
        row(T("col_authors"), p.get("authors"))
        row(T("col_date_taken"), p.get("date_taken"))
        row(T("col_program"), p.get("program") or p.get("software"))
        row(T("col_copyright"), p.get("copyright"))
        if any(k in p for k in ("dimensions", "camera_model")):
            section(T("sec_image"))
            row(T("col_dimensions"), p.get("dimensions"))
            row(T("col_width"), p.get("width"))
            row(T("col_height"), p.get("height"))
            row(T("col_bit_depth"), p.get("bit_depth"))
            row(T("col_camera_maker"), p.get("camera_maker"))
            row(T("col_camera_model"), p.get("camera_model"))
            row(T("col_exposure"), p.get("exposure"))
            row(T("col_fstop"), p.get("fstop"))
            row(T("col_iso"), p.get("iso"))
            row(T("col_focal"), p.get("focal"))
        if any(k in p for k in ("length", "bitrate")) or editable:
            section(T("sec_media"))
            row(T("col_artists"), p.get("artists"), "artists")
            row(T("col_album_artist"), p.get("album_artist"), "album_artist")
            row(T("col_album"), p.get("album"), "album")
            row(T("col_year"), p.get("year"), "year")
            row(T("col_track"), p.get("track"), "track")
            row(T("col_genre"), p.get("genre"), "genre")
            row(T("col_composers"), p.get("composers"), "composers")
            row(T("col_length"), p.get("length"))
            row(T("col_bitrate"), p.get("bitrate"))
            row(T("col_sample_rate"), p.get("sample_rate"))
            row(T("col_channels"), p.get("channels"))
            row(T("col_frame_width"), p.get("frame_width"))
            row(T("col_frame_height"), p.get("frame_height"))
        if p.get("pages") or p.get("words"):
            section(T("sec_content"))
            row(T("col_pages"), p.get("pages"))
            row(T("col_words"), p.get("words"))
            row(T("col_last_saved_by"), p.get("last_saved_by"))
            row(T("col_revision"), p.get("revision"))
            row(T("col_template"), p.get("template"))
        section(T("sec_file"))
        row(T("col_name"), it.name)
        row(T("col_item_type"), it.type_text())
        row(T("col_folder_path"), it.extra.get("orig_dir") or os.path.dirname(it.path))
        row(T("col_date_created"), util.fmt_datetime(it.btime))
        row(T("col_date_modified"), util.fmt_datetime(it.mtime))
        row(T("col_size"), util.fmt_size_short(it.size or 0))
        try:
            row(T("col_owner"), pwd.getpwuid(it.uid).pw_name if it.uid >= 0 else "")
        except KeyError:
            pass
        row(T("col_computer"), f"{socket.gethostname()} ({T('this_computer')})")
        if editable:
            tree.itemChanged.connect(lambda item, col: (self._tag_edits.__setitem__(item.data(0, Qt.ItemDataRole.UserRole),
                                                                                     item.text(1)), self.changed())
                                     if item.data(0, Qt.ItemDataRole.UserRole) else None)
            tree.setEditTriggers(QTreeWidget.EditTrigger.DoubleClicked | QTreeWidget.EditTrigger.SelectedClicked)

            def apply_tags():
                if self._tag_edits:
                    err = metadata.write_tags(it.path, dict(self._tag_edits))
                    if err:
                        QMessageBox.warning(self, T("apply_props"), err)
                        return False
                    self._tag_edits = {}
                    it.invalidate()
            self._apply_fns.append(apply_tags)
        v.addWidget(tree)
        if it.kind == "file" and (it.mime.name() == "image/jpeg" or metadata.can_edit_tags(it)):
            link = QLabel(f"<a href='#' style='color:#0066cc'>{T('remove_props_link')}</a>")
            link.linkActivated.connect(lambda _l: RemovePropertiesDialog(self, it.path).exec())
            v.addWidget(link)
        return w

    # ------------------------------------------------------------------ sharing
    def _sharing_tab(self, it):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 12, 10, 10)
        box = QGroupBox(T("net_file_sharing"))
        bv = QVBoxLayout(box)
        info = sharing.info(it.path)
        h = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.folder("public" if info else "").pixmap(32, 32))
        h.addWidget(ic)
        nv = QVBoxLayout()
        nv.addWidget(QLabel(it.name))
        state = QLabel(T("shared") if info else T("not_shared"))
        state.setStyleSheet("color: #6d6d6d;")
        nv.addWidget(state)
        h.addLayout(nv, 1)
        bv.addLayout(h)
        path_l = QLabel()
        bv.addWidget(path_l)
        v.addWidget(box)
        adv = QGroupBox(T("advanced_sharing"))
        ag = QGridLayout(adv)
        share_cb = QCheckBox(T("share_this_folder"))
        share_cb.setChecked(info is not None)
        ag.addWidget(share_cb, 0, 0, 1, 2)
        ag.addWidget(QLabel(T("share_name") + ":"), 1, 0)
        name_e = QLineEdit(info["name"] if info else os.path.basename(it.path.rstrip("/")))
        ag.addWidget(name_e, 1, 1)
        ag.addWidget(QLabel(T("comments") + ":"), 2, 0)
        cmt = QLineEdit(info.get("comment", "") if info else "")
        ag.addWidget(cmt, 2, 1)
        ro = QRadioButton(T("share_perm_read"))
        rw = QRadioButton(T("share_perm_readwrite"))
        (rw if info and info.get("writable") else ro).setChecked(True)
        ag.addWidget(ro, 3, 0, 1, 2)
        ag.addWidget(rw, 4, 0, 1, 2)
        guest = QCheckBox(T("share_guests"))
        guest.setChecked(info.get("guest", True) if info else True)
        ag.addWidget(guest, 5, 0, 1, 2)
        v.addWidget(adv)
        v.addStretch(1)

        def upd():
            on = share_cb.isChecked()
            for x in (name_e, cmt, ro, rw, guest):
                x.setEnabled(on)
            path_l.setText(T("network_path") + f"  \\\\{socket.gethostname()}\\{name_e.text()}" if on else T("network_path") + "  " + T("not_shared"))
        upd()
        for x in (share_cb,):
            x.stateChanged.connect(lambda *_: (upd(), self.changed()))
        for x in (name_e, cmt):
            x.textEdited.connect(lambda *_: (upd(), self.changed()))
        for x in (ro, rw, guest):
            x.toggled.connect(self.changed)

        def apply_share():
            if share_cb.isChecked():
                err = sharing.set_share(it.path, rw.isChecked(), name_e.text().strip() or None, cmt.text(), guest.isChecked())
            else:
                err = sharing.set_share(it.path, None)
            if err:
                QMessageBox.warning(self, T("file_sharing"), err)
                return False
            state.setText(T("shared") if share_cb.isChecked() else T("not_shared"))
        self._apply_fns.append(apply_share)
        return w

    # ------------------------------------------------------------------ customize
    def _customize_tab(self, it):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 12, 10, 10)
        box = QGroupBox(T("what_kind_folder"))
        bv = QVBoxLayout(box)
        bv.addWidget(QLabel(T("optimize_for") + ":"))
        combo = QComboBox()
        kinds = ["general", "documents", "pictures", "music", "videos"]
        for k in kinds:
            combo.addItem(T("ft_" + k), k)
        bag = settings().bag(it.path) or {}
        cur = bag.get("ftype") or places.folder_type_for(it.path)
        combo.setCurrentIndex(kinds.index(cur) if cur in kinds else 0)
        combo.currentIndexChanged.connect(self.changed)
        bv.addWidget(combo)
        sub = QCheckBox(T("apply_to_subfolders"))
        sub.stateChanged.connect(self.changed)
        bv.addWidget(sub)
        v.addWidget(box)
        box2 = QGroupBox(T("folder_pictures"))
        b2 = QHBoxLayout(box2)
        lv = QVBoxLayout()
        lv.addWidget(QLabel(T("choose_picture_text")))
        choose = QPushButton(T("choose_file"))
        restore = QPushButton(T("restore_default"))
        lv.addWidget(choose)
        lv.addWidget(restore)
        lv.addStretch(1)
        b2.addLayout(lv, 1)
        prev = QLabel()
        prev.setFixedSize(72, 72)
        prev.setAlignment(Qt.AlignmentFlag.AlignCenter)
        b2.addWidget(prev)
        v.addWidget(box2)
        box3 = QGroupBox(T("folder_icons"))
        b3 = QHBoxLayout(box3)
        b3.addWidget(QLabel(T("change_icon_text")), 1)
        chi = QPushButton(T("change_icon"))
        b3.addWidget(chi)
        v.addWidget(box3)
        v.addStretch(1)
        state = {"picture": bag.get("picture"), "icon": bag.get("icon")}

        def show_prev():
            p = state.get("picture") or state.get("icon")
            if p and os.path.exists(p):
                prev.setPixmap(QPixmap(p).scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation))
            else:
                prev.setPixmap(icons.folder().pixmap(64, 64))
        show_prev()

        def pick(key):
            f, _ = QFileDialog.getOpenFileName(self, T("choose_file"), it.path,
                                               "Images (*.png *.jpg *.jpeg *.svg *.ico *.gif *.bmp *.webp)")
            if f:
                state[key] = f
                show_prev()
                self.changed()
        choose.clicked.connect(lambda: pick("picture"))
        chi.clicked.connect(lambda: pick("icon"))
        restore.clicked.connect(lambda: (state.update(picture=None, icon=None), show_prev(), self.changed()))

        def apply_custom():
            b = dict(settings().bag(it.path) or {})
            b["ftype"] = combo.currentData()
            for k in ("picture", "icon"):
                if state.get(k):
                    b[k] = state[k]
                else:
                    b.pop(k, None)
            # a new optimisation resets the stored view to the template's defaults
            if (settings().bag(it.path) or {}).get("ftype") != combo.currentData():
                for k in ("mode", "columns", "widths", "sort", "desc", "group"):
                    b.pop(k, None)
            settings().set_bag(it.path, b)
            if sub.isChecked():
                for root, dirs, _f in os.walk(it.path):
                    for d in dirs:
                        p = os.path.join(root, d)
                        bb = dict(settings().bag(p) or {})
                        bb["ftype"] = combo.currentData()
                        settings().set_bag(p, bb)
            icon_file = state.get("icon") or state.get("picture")
            if util.which("gio"):
                if icon_file:
                    util.run(["gio", "set", it.path, "metadata::custom-icon", "file://" + icon_file], timeout=5)
                else:
                    util.run(["gio", "set", "-t", "unset", it.path, "metadata::custom-icon"], timeout=5)
            if self.win is not None and self.win.loc == it.path:
                self.win.navigate(it.path, add_history=False)
        self._apply_fns.append(apply_custom)
        return w

    # ------------------------------------------------------------------ drives
    def _drive_tabs(self, it):
        d = it.extra.get("drive") or {}
        w, g = self._grid()
        self._header(g, icons.drive(d.get("kind")).pixmap(32, 32), d.get("label") or it.name, False, None)
        g.addWidget(_sep(), 1, 0, 1, 2)
        self._row(g, 2, T("type"), it.type_text())
        self._row(g, 3, T("col_filesystem"), (d.get("fstype") or "").upper() or "-")
        g.addWidget(_sep(), 4, 0, 1, 2)
        size = d.get("size") or 0
        free = d.get("free") or 0
        used = d.get("used") if d.get("used") is not None else max(0, size - free)
        if d.get("mount"):
            for r, (label, val, color) in enumerate(((T("used_space"), used, QColor(38, 160, 218)),
                                                     (T("free_space"), free, QColor(200, 60, 200))), start=5):
                sq = QLabel()
                pm = QPixmap(14, 14)
                pm.fill(color)
                sq.setPixmap(pm)
                h = QHBoxLayout()
                h.addWidget(sq)
                h.addWidget(QLabel(label + ":"))
                h.addStretch(1)
                g.addLayout(h, r, 0)
                vv = QHBoxLayout()
                vv.addWidget(_val(T("n_bytes", util.fmt_int(val))))
                vv.addWidget(_val(util.fmt_size_short(val)))
                g.addLayout(vv, r, 1)
            g.addWidget(_sep(), 7, 0, 1, 2)
            self._row(g, 8, T("capacity"), f"{T('n_bytes', util.fmt_int(size))}    {util.fmt_size_short(size)}")
            pie = _Pie(used, size, T("drive_x", d.get("mount") or d.get("dev", "")))
            g.addWidget(pie, 9, 0, 1, 2)
            if util.which("baobab"):
                b = QPushButton(T("disk_cleanup"))
                b.clicked.connect(lambda: util.spawn(["baobab", d["mount"]]))
                h = QHBoxLayout()
                h.addStretch(1)
                h.addWidget(b)
                g.addLayout(h, 10, 0, 1, 2)
        else:
            self._row(g, 5, T("capacity"), util.fmt_size_short(size))
            self._row(g, 6, T("status"), T("not_mounted"))
        g.setRowStretch(11, 1)
        self.tabs.addTab(w, T("tab_general"))
        # tools
        tw = QWidget()
        tv = QVBoxLayout(tw)
        tv.setContentsMargins(10, 12, 10, 10)
        for title, text, btn, cmd in ((T("error_checking"), T("error_checking_text"), T("check_now"),
                                       ["gnome-disks", "--block-device", d.get("dev", "")]),
                                      (T("backup"), T("backup_text"), T("backup_now"), ["deja-dup"])):
            gb = QGroupBox(title)
            h = QHBoxLayout(gb)
            l = QLabel(text)
            l.setWordWrap(True)
            h.addWidget(l, 1)
            b = QPushButton(btn)
            b.setEnabled(bool(util.which(cmd[0])) and (cmd[0] != "gnome-disks" or bool(d.get("dev", "").startswith("/dev/"))))
            b.clicked.connect(lambda _=False, c=cmd: util.spawn(c))
            h.addWidget(b, 0, Qt.AlignmentFlag.AlignBottom)
            tv.addWidget(gb)
        tv.addStretch(1)
        self.tabs.addTab(tw, T("tab_tools"))
        # hardware
        hw = QWidget()
        hv = QVBoxLayout(hw)
        hv.setContentsMargins(10, 12, 10, 10)
        tree = QTreeWidget()
        tree.setHeaderLabels([T("property"), T("value")])
        tree.setRootIsDecorated(False)
        tree.setColumnWidth(0, 140)
        for k, val in ((T("device"), d.get("model") or "-"), (T("device_node"), d.get("dev") or "-"),
                       (T("parent_device"), d.get("parent") or "-"), ("UUID", d.get("uuid") or "-"),
                       (T("mount_point"), d.get("mount") or "-"), (T("removable"), T("yes") if d.get("removable") else T("no")),
                       (T("read_only"), T("yes") if d.get("ro") else T("no"))):
            tree.addTopLevelItem(QTreeWidgetItem([k, str(val)]))
        hv.addWidget(tree)
        self.tabs.addTab(hw, T("tab_hardware"))


class _Pie(QWidget):
    """The 3D pie of the drive Properties dialog (own drawing)."""

    def __init__(self, used, total, label):
        super().__init__()
        self.used, self.total, self.label = used, total, label
        self.setMinimumHeight(120)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = min(150, self.width() - 20)
        h = w * 0.45
        x = (self.width() - w) / 2
        y = 10
        depth = 14
        frac = self.used / self.total if self.total else 0
        span = int(360 * 16 * frac)
        blue, mag = QColor(38, 160, 218), QColor(200, 60, 200)
        for i in range(depth, 0, -1):
            r = QRectF(x, y + i, w, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(blue.darker(140))
            p.drawPie(r, 0, -span)
            p.setBrush(mag.darker(140))
            p.drawPie(r, -span, -(360 * 16 - span))
        r = QRectF(x, y, w, h)
        p.setPen(QPen(QColor(80, 80, 80), 0.6))
        p.setBrush(blue)
        p.drawPie(r, 0, -span)
        p.setBrush(mag)
        p.drawPie(r, -span, -(360 * 16 - span))
        p.setPen(QColor(30, 30, 30))
        p.drawText(QRectF(0, y + h + depth + 4, self.width(), 20), Qt.AlignmentFlag.AlignHCenter, self.label)


class RemovePropertiesDialog(QDialog):
    def __init__(self, parent, path: str):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(T("remove_props_title"))
        v = QVBoxLayout(self)
        v.addWidget(QLabel(T("remove_props_text")))
        self.copy = QRadioButton(T("remove_props_copy"))
        self.copy.setChecked(True)
        v.addWidget(self.copy)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _ok(self):
        folder = os.path.dirname(self.path)
        base, ext = util.split_ext(os.path.basename(self.path))
        out = os.path.join(folder, util.unique_name(folder, f"{base} - {T('copy_suffix')}{ext}"))
        if metadata.strip_properties(self.path, out):
            undo.manager().push("copy", {"created": [out]})
            self.accept()
        else:
            QMessageBox.warning(self, T("remove_props_title"), T("remove_props_failed"))
