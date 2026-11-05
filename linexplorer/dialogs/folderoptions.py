"""Folder Options (General / View / Search), as in Windows 7."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGroupBox,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton, QRadioButton, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import icons
from ..i18n import T
from ..settings import DEFAULTS, settings

GENERAL_KEYS = ["browse_same_window", "single_click", "underline_always", "nav_show_all_folders", "nav_auto_expand",
                "nav_show_recycle_bin"]
VIEW_KEYS = ["always_icons", "always_menus", "file_icon_on_thumbs", "size_in_tips", "show_hidden",
             "hide_empty_drives", "hide_extensions", "restore_windows", "tab_bar_always", "new_tab_same_folder", "show_drive_letters", "popup_descriptions",
             "preview_handlers", "checkboxes", "typing_mode", "status_bar"]
SEARCH_KEYS = ["search_contents", "search_subfolders", "search_partial", "search_system_dirs", "search_compressed"]


class FolderOptionsDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(T("folder_options"))
        self.vals = {k: settings().get(k) for k in GENERAL_KEYS + VIEW_KEYS + SEARCH_KEYS + ["language"]}
        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs)
        self.tabs.addTab(self._general(), T("tab_general"))
        self.tabs.addTab(self._view(), T("tab_view"))
        self.tabs.addTab(self._search(), T("tab_search"))
        bb = QDialogButtonBox()
        ok = bb.addButton(T("ok"), QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(T("cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.apply_btn = bb.addButton(T("apply"), QDialogButtonBox.ButtonRole.ApplyRole)
        self.apply_btn.setEnabled(False)
        ok.clicked.connect(lambda: (self._apply(), self.accept()))
        bb.rejected.connect(self.reject)
        self.apply_btn.clicked.connect(self._apply)
        v.addWidget(bb)
        self.resize(430, 520)

    def _set(self, k, v):
        self.vals[k] = v
        self.apply_btn.setEnabled(True)

    def _radio_pair(self, title, key, a_text, b_text, a_val=True, b_val=False):
        gb = QGroupBox(title)
        lay = QVBoxLayout(gb)
        ra, rb = QRadioButton(a_text), QRadioButton(b_text)
        (ra if self.vals[key] == a_val else rb).setChecked(True)
        ra.toggled.connect(lambda c: c and self._set(key, a_val))
        rb.toggled.connect(lambda c: c and self._set(key, b_val))
        lay.addWidget(ra)
        lay.addWidget(rb)
        return gb, lay

    def _general(self):
        w = QWidget()
        v = QVBoxLayout(w)
        gb, _ = self._radio_pair(T("fo_browse_folders"), "browse_same_window", T("fo_same_window"), T("fo_own_window"))
        v.addWidget(gb)
        gb, lay = self._radio_pair(T("fo_click_items"), "single_click", T("fo_single_click"), T("fo_double_click"))
        v.addWidget(gb)
        gb = QGroupBox(T("fo_nav_pane"))
        lay = QVBoxLayout(gb)
        for k in ("nav_show_all_folders", "nav_auto_expand", "nav_show_recycle_bin"):
            cb = QCheckBox(T("fo_" + k))
            cb.setChecked(self.vals[k])
            cb.toggled.connect(lambda c, k=k: self._set(k, c))
            lay.addWidget(cb)
        v.addWidget(gb)
        gb = QGroupBox(T("fo_language"))
        lay = QHBoxLayout(gb)
        combo = QComboBox()
        for code, label in (("auto", T("lang_auto")), ("en", "English"), ("el", "Ελληνικά")):
            combo.addItem(label, code)
        combo.setCurrentIndex(max(0, combo.findData(self.vals["language"])))
        combo.currentIndexChanged.connect(lambda i: self._set("language", combo.itemData(i)))
        lay.addWidget(combo)
        v.addWidget(gb)
        v.addStretch(1)
        h = QHBoxLayout()
        h.addStretch(1)
        b = QPushButton(T("restore_defaults"))
        b.clicked.connect(lambda: self._defaults(GENERAL_KEYS))
        h.addWidget(b)
        v.addLayout(h)
        return w

    def _view(self):
        w = QWidget()
        v = QVBoxLayout(w)
        gb = QGroupBox(T("fo_folder_views"))
        h = QHBoxLayout(gb)
        ic = QLabel()
        ic.setPixmap(icons.folder().pixmap(32, 32))
        h.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        vv = QVBoxLayout()
        t = QLabel(T("fo_folder_views_text"))
        t.setWordWrap(True)
        vv.addWidget(t)
        hb = QHBoxLayout()
        a = QPushButton(T("fo_apply_to_folders"))
        a.clicked.connect(self._apply_to_folders)
        r = QPushButton(T("fo_reset_folders"))
        r.clicked.connect(self._reset_folders)
        hb.addWidget(a)
        hb.addWidget(r)
        vv.addLayout(hb)
        h.addLayout(vv, 1)
        v.addWidget(gb)
        v.addWidget(QLabel(T("fo_advanced_settings")))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self._items = {}
        root = QTreeWidgetItem([T("fo_files_and_folders")])
        root.setIcon(0, icons.folder())
        self.tree.addTopLevelItem(root)

        def cb(key):
            it = QTreeWidgetItem([T("fo_" + key)])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Checked if self.vals[key] else Qt.CheckState.Unchecked)
            it.setData(0, Qt.ItemDataRole.UserRole, key)
            root.addChild(it)
            self._items[key] = it

        def radio_group(title, key, options):
            g = QTreeWidgetItem([title])
            g.setIcon(0, icons.folder())
            root.addChild(g)
            for val, label in options:
                it = QTreeWidgetItem([("● " if self.vals[key] == val else "○ ") + label])
                it.setData(0, Qt.ItemDataRole.UserRole, (key, val))
                g.addChild(it)
            g.setExpanded(True)

        cb("always_icons")
        cb("always_menus")
        cb("file_icon_on_thumbs")
        cb("size_in_tips")
        radio_group(T("fo_hidden_files"), "show_hidden", [(False, T("fo_dont_show_hidden")), (True, T("fo_show_hidden"))])
        cb("hide_empty_drives")
        cb("hide_extensions")
        cb("restore_windows")
        cb("tab_bar_always")
        cb("new_tab_same_folder")
        cb("show_drive_letters")
        cb("popup_descriptions")
        cb("preview_handlers")
        cb("status_bar")
        cb("checkboxes")
        radio_group(T("fo_typing"), "typing_mode", [("search", T("fo_typing_search")), ("select", T("fo_typing_select"))])
        root.setExpanded(True)
        self.tree.itemChanged.connect(self._tree_changed)
        self.tree.itemClicked.connect(self._tree_clicked)
        v.addWidget(self.tree, 1)
        h = QHBoxLayout()
        h.addStretch(1)
        b = QPushButton(T("restore_defaults"))
        b.clicked.connect(lambda: self._defaults(VIEW_KEYS))
        h.addWidget(b)
        v.addLayout(h)
        return w

    def _tree_changed(self, it, col):
        key = it.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(key, str):
            self._set(key, it.checkState(0) == Qt.CheckState.Checked)

    def _tree_clicked(self, it, col):
        data = it.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, tuple):
            key, val = data
            self._set(key, val)
            parent = it.parent()
            for i in range(parent.childCount()):
                c = parent.child(i)
                k2, v2 = c.data(0, Qt.ItemDataRole.UserRole)
                label = c.text(0)[2:]
                c.setText(0, ("● " if v2 == val else "○ ") + label)

    def _search(self):
        w = QWidget()
        v = QVBoxLayout(w)
        gb, _ = self._radio_pair(T("fo_what_to_search"), "search_contents", T("fo_names_contents_indexed"),
                                 T("fo_always_contents"), False, True)
        v.addWidget(gb)
        gb = QGroupBox(T("fo_how_to_search"))
        lay = QVBoxLayout(gb)
        for k in ("search_subfolders", "search_partial"):
            c = QCheckBox(T("fo_" + k))
            c.setChecked(self.vals[k])
            c.toggled.connect(lambda x, k=k: self._set(k, x))
            lay.addWidget(c)
        v.addWidget(gb)
        gb = QGroupBox(T("fo_non_indexed"))
        lay = QVBoxLayout(gb)
        for k in ("search_system_dirs", "search_compressed"):
            c = QCheckBox(T("fo_" + k))
            c.setChecked(self.vals[k])
            c.toggled.connect(lambda x, k=k: self._set(k, x))
            lay.addWidget(c)
        v.addWidget(gb)
        v.addStretch(1)
        h = QHBoxLayout()
        h.addStretch(1)
        b = QPushButton(T("restore_defaults"))
        b.clicked.connect(lambda: self._defaults(SEARCH_KEYS))
        h.addWidget(b)
        v.addLayout(h)
        return w

    def _defaults(self, keys):
        for k in keys:
            self.vals[k] = DEFAULTS[k]
        self._apply()
        idx = self.tabs.currentIndex()
        self.tabs.clear()
        self.tabs.addTab(self._general(), T("tab_general"))
        self.tabs.addTab(self._view(), T("tab_view"))
        self.tabs.addTab(self._search(), T("tab_search"))
        self.tabs.setCurrentIndex(idx)

    def _apply(self):
        lang_changed = self.vals["language"] != settings().get("language")
        for k, v in self.vals.items():
            settings().set(k, v)
        self.apply_btn.setEnabled(False)
        if lang_changed:
            QMessageBox.information(self, T("folder_options"), T("restart_for_language"))

    def _apply_to_folders(self):
        if QMessageBox.question(self, T("folder_views"), T("apply_to_folders_q")) != QMessageBox.StandardButton.Yes:
            return
        ftype = self.win._folder_type(self.win.loc)
        settings().set_type_default(ftype, {k: v for k, v in self.win.current_bag().items() if k != "ftype"})

    def _reset_folders(self):
        if QMessageBox.question(self, T("folder_views"), T("reset_folders_q")) != QMessageBox.StandardButton.Yes:
            return
        settings().reset_all_bags()
        self.win.navigate(self.win.loc, add_history=False)
