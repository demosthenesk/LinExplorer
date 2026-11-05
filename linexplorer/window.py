"""The Explorer window."""
from __future__ import annotations

import os

from PySide6.QtCore import QByteArray, QEvent, QItemSelectionModel, QModelIndex, QPoint, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPalette, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QMessageBox, QSizePolicy, QSplitter, QStackedWidget, QStatusBar, QToolButton,
                               QVBoxLayout, QWidget)

from . import (addressbar, clipboard, commandbar, icons, locations, places, undo, util, ziputil)
from .commands import CommandsMixin
from .detailspane import DetailsPane
from .i18n import T
from .itemsview import MODES, ItemsView
from .model import COLUMNS, DEFAULT_VIEWS, FolderModel, ItemRole
from .navpane import NavPane
from .previewpane import PreviewPane
from .settings import settings
from .tabs import Tab, TabStrip


def drop_action_for_target(mime, target_loc: str | None, mods) -> Qt.DropAction:
    """Windows rules: same drive = move, other drive = copy; Ctrl copy, Shift move, Alt/Ctrl+Shift link."""
    paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()] if mime.hasUrls() else []
    zipm = mime.hasFormat(clipboard.OURS)
    if not target_loc:
        return Qt.DropAction.IgnoreAction
    k = locations.kind(target_loc)
    if k == "trash":
        return Qt.DropAction.MoveAction if paths else Qt.DropAction.IgnoreAction
    if k == "net":            # upload into a remote folder (always a copy)
        return Qt.DropAction.CopyAction if paths and target_loc.split("://", 1)[0][4:] not in ("network",) \
            else Qt.DropAction.IgnoreAction
    if k not in ("fs", "library", "zip") and not target_loc.startswith("/"):
        return Qt.DropAction.IgnoreAction
    tdir = target_loc
    if k == "library":
        lib = places.libraries().get(locations.library_name(target_loc))
        tdir = (lib or {}).get("save") or ""
        if not tdir:
            return Qt.DropAction.IgnoreAction
    if k == "zip":
        return Qt.DropAction.CopyAction if ziputil.is_writable(ziputil.split(target_loc)[0]) else Qt.DropAction.IgnoreAction
    if paths and all(os.path.abspath(p) == os.path.abspath(tdir) for p in paths):
        return Qt.DropAction.IgnoreAction
    alt = bool(mods & Qt.KeyboardModifier.AltModifier)
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
    shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
    if alt or (ctrl and shift):
        return Qt.DropAction.LinkAction
    if ctrl:
        return Qt.DropAction.CopyAction
    if shift:
        return Qt.DropAction.MoveAction
    if zipm or not paths:
        return Qt.DropAction.CopyAction
    if tdir.lower().endswith(".zip"):
        return Qt.DropAction.CopyAction
    if all(util.same_device(p, tdir) for p in paths):
        return Qt.DropAction.MoveAction
    return Qt.DropAction.CopyAction


class LibraryPane(QFrame):
    """'Documents library / Includes: 2 locations / Arrange by: Folder'."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setFixedHeight(54)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 6, 16, 6)
        v = QVBoxLayout()
        v.setSpacing(0)
        self.title = QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.45)
        self.title.setFont(f)
        self.title.setStyleSheet("color: #1e3287;")
        self.includes = QLabel()
        self.includes.setTextFormat(Qt.TextFormat.RichText)
        self.includes.linkActivated.connect(lambda _l: self.win.cmd_library_locations())
        v.addWidget(self.title)
        v.addWidget(self.includes)
        lay.addLayout(v)
        lay.addStretch(1)
        self.arrange = QToolButton()
        self.arrange.setAutoRaise(True)
        self.arrange.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.arrange.setStyleSheet("QToolButton { color: #0066cc; border: none; } QToolButton::menu-indicator { image: none; }")
        lab = QLabel(T("arrange_by") + ":")
        lab.setStyleSheet("color: #444;")
        lay.addWidget(lab)
        lay.addWidget(self.arrange)
        self.setStyleSheet("LibraryPane { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff,"
                           " stop:1 #f4f7fb); border-bottom: 1px solid #e3e8ef; }")

    def set_library(self, lib: dict | None, arrange: str):
        if lib is None:
            return
        L = places.libraries()
        self.title.setText(T("x_library", L.title(lib)))
        n = len(lib.get("locations", []))
        self.includes.setText(f"{T('includes')}: <a href='#' style='color:#0066cc;text-decoration:none'>"
                              f"{T('n_locations', n)}</a>")
        names = {"folder": T("arr_folder"), "date_modified": T("col_date_modified"), "name": T("col_name"),
                 "type": T("col_type"), "size": T("col_size")}
        self.arrange.setText(names.get(arrange, T("arr_folder")) + " ▾")
        m = QMenu(self)
        for key, label in names.items():
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(key == arrange)
            a.triggered.connect(lambda _=False, k=key: self.win.set_arrange(k))
        self.arrange.setMenu(m)


class MainWindow(CommandsMixin, QMainWindow):
    instances: list["MainWindow"] = []

    def __init__(self, location: str | None = None, select: str | None = None):
        super().__init__()
        MainWindow.instances.append(self)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowIcon(icons.app_icon())
        self.tabs: list[Tab] = []
        self.tab: Tab | None = None
        self._closed_tabs: list[dict] = []
        self._bg = False
        self._menubar_temp = False
        self._build()
        self._shortcuts()
        self._restore_geometry()
        settings().changed.connect(self._on_setting)
        undo.manager().changed.connect(self.update_commands)
        clipboard.watch.changed.connect(self._update_ghost)
        QApplication.clipboard().dataChanged.connect(self._update_ghost)
        if isinstance(location, (list, tuple)):
            self.restore_tabs(location)
        else:
            self.navigate(location or places.HOME, select=[select] if select else None)

    # ------------------------------------------------------------------ per-tab state (delegated)
    def _tabprop(name):
        return property(lambda s: getattr(s.tab, name), lambda s, v: setattr(s.tab, name, v))

    model = _tabprop("model")
    view = _tabprop("view")
    loc = _tabprop("loc")
    history = _tabprop("history")
    hist_pos = _tabprop("hist_pos")
    _select_after = _tabprop("select_after")
    _edit_after = _tabprop("edit_after")
    _saved_sel = _tabprop("saved_sel")
    _saved_cur = _tabprop("saved_cur")
    _saved_scroll = _tabprop("saved_scroll")
    _restoring = _tabprop("restoring")
    _arrange = _tabprop("arrange")
    del _tabprop

    # ------------------------------------------------------------------ construction
    def _build(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        vl = QVBoxLayout(central)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        # address row (glass area)
        self.top = _GlassRow()
        tv = QVBoxLayout(self.top)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)
        self.tabstrip = TabStrip()
        tb = self.tabbar = self.tabstrip.bar
        tb.new_tab_requested.connect(lambda: self.new_tab(self._new_tab_location()))
        tb.close_requested.connect(self.close_tab)
        tb.currentChanged.connect(self._tab_changed)
        tb.tabMoved.connect(self._tab_moved)
        tb.context_requested.connect(self._tab_menu)
        tb.drop_requested.connect(self._tab_drop)
        tb.drop_action_for = lambda i, md, mods: drop_action_for_target(md, self.tabs[i].loc, mods) \
            if 0 <= i < len(self.tabs) else Qt.DropAction.IgnoreAction
        tv.addWidget(self.tabstrip)
        arow = QWidget()
        tv.addWidget(arow)
        hl = QHBoxLayout(arow)
        hl.setContentsMargins(4, 4, 6, 4)
        hl.setSpacing(6)
        self.orbs = addressbar.NavOrbs()
        self.orbs.back.connect(self.go_back)
        self.orbs.forward.connect(self.go_forward)
        self.orbs.history.connect(self._history_menu)
        hl.addWidget(self.orbs)
        self.addr = addressbar.AddressBar()
        self.addr.navigate.connect(self.navigate)
        self.addr.refresh.connect(self.refresh)
        self.search_box = addressbar.SearchBox()
        self.search_box.search.connect(self.do_search)
        self.search_box.cleared.connect(self.clear_search)
        self.addr_split = QSplitter(Qt.Orientation.Horizontal)
        self.addr_split.setChildrenCollapsible(False)
        self.addr_split.setHandleWidth(6)
        self.addr_split.addWidget(self.addr)
        self.addr_split.addWidget(self.search_box)
        self.addr_split.setStretchFactor(0, 1)
        self.addr_split.setStretchFactor(1, 0)
        self.addr_split.setSizes([600, 220])
        self.addr_split.setStyleSheet("QSplitter::handle { background: transparent; }")
        hl.addWidget(self.addr_split, 1)
        vl.addWidget(self.top)
        # menu bar (hidden like Windows 7 until Alt)
        from .menurow import MenuRow
        self.mbar = MenuRow()
        self.build_menubar(self.mbar)
        self.mbar.closed.connect(self._hide_menubar_temp)
        vl.addWidget(self.mbar)
        self.cmdbar = commandbar.CommandBar()
        self.cmdbar.view_chosen.connect(self.set_view_mode)
        self.cmdbar.view_cycle.connect(self.cycle_view)
        self.cmdbar.preview_toggled.connect(lambda: settings().set("layout_preview", not settings().get("layout_preview")))
        self.cmdbar.help_clicked.connect(self.cmd_help)
        vl.addWidget(self.cmdbar)
        # body
        self.vsplit = QSplitter(Qt.Orientation.Vertical)
        self.vsplit.setChildrenCollapsible(False)
        self.vsplit.setHandleWidth(3)
        self.hsplit = QSplitter(Qt.Orientation.Horizontal)
        self.hsplit.setChildrenCollapsible(False)
        self.hsplit.setHandleWidth(4)
        self.hsplit.setStyleSheet("QSplitter::handle { background: #ffffff; }")
        self.nav = NavPane()
        self.nav.navigate.connect(self.navigate)
        self.nav.open_new_window.connect(lambda l: self.new_window(l))
        self.nav.open_new_tab.connect(lambda l: self.new_tab(l))
        self.nav.context_requested.connect(self.nav_context_menu)
        self.nav.drop_requested.connect(self.handle_drop_loc)
        self.nav.setMinimumWidth(120)
        self.hsplit.addWidget(self.nav)
        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        self.libpane = LibraryPane(self)
        ml.addWidget(self.libpane)
        self.stack = QStackedWidget()
        self._sel_timer = QTimer(self)
        self._sel_timer.setSingleShot(True)
        self._sel_timer.setInterval(25)
        self._sel_timer.timeout.connect(self._on_selection)
        self.vsplit_inner = QSplitter(Qt.Orientation.Horizontal)
        self.vsplit_inner.setChildrenCollapsible(False)
        self.vsplit_inner.setHandleWidth(4)
        self.vsplit_inner.addWidget(self.stack)
        self.preview = PreviewPane()
        self.vsplit_inner.addWidget(self.preview)
        self.vsplit_inner.setStretchFactor(0, 1)
        self.vsplit_inner.setSizes([700, 300])
        ml.addWidget(self.vsplit_inner, 1)
        self.hsplit.addWidget(mid)
        self.hsplit.setStretchFactor(1, 1)
        self.hsplit.setSizes([200, 800])
        self.vsplit.addWidget(self.hsplit)
        self.details = DetailsPane()
        self.details.save_requested.connect(self.save_tags)
        self.vsplit.addWidget(self.details)
        self.vsplit.setStretchFactor(0, 1)
        self.vsplit.setSizes([600, 70])
        vl.addWidget(self.vsplit, 1)
        self.status = QStatusBar()
        self.status.setSizeGripEnabled(True)
        self.status_lbl = QLabel()
        self.status.addWidget(self.status_lbl, 1)
        self.setStatusBar(self.status)
        self._add_tab()
        self.apply_layout()
        self.nav.installEventFilter(self)

    # ------------------------------------------------------------------ tabs
    def _add_tab(self, index: int | None = None) -> Tab:
        """Create a tab's model+view (no location yet) and insert it into the tab bar."""
        model = FolderModel(self)
        view = ItemsView()
        view.setModel(model)
        model.busy_check = lambda v=view: v.state() == v.State.EditingState
        t = Tab(model, view)
        view.open_requested.connect(self.open_index)
        view.open_new_tab.connect(self._open_index_new_tab)
        view.context_requested.connect(self.item_context_menu)
        view.drop_requested.connect(self.handle_drop_item)
        view.mime_provider = self.mime_for_rows
        view.drop_action_for = self._drop_action_item
        view.mode_changed.connect(lambda m, t=t: self._on_mode_changed(m) if t is self.tab else None)
        view.header_context.connect(self.header_menu)
        view.sort_clicked.connect(self._sort_clicked)
        view.columns_resized.connect(lambda t=t: self._save_bag_soon() if t is self.tab else None)
        view.back_forward.connect(lambda d: self.go_back() if d < 0 else self.go_forward())
        view.selectionModel().selectionChanged.connect(lambda *a, t=t: self._sel_timer.start() if t is self.tab else None)
        model.rename_requested.connect(self.do_rename)
        model.about_to_reset.connect(lambda t=t: self._before_reset(t))
        model.reset_done.connect(lambda t=t: self._after_reset(t))
        model.contents_changed.connect(lambda t=t: self._on_contents(t))
        model.loading.connect(lambda busy, t=t: self._on_loading(t, busy))
        model.load_failed.connect(lambda exc, t=t: self._on_load_failed(t, exc))
        view.installEventFilter(self)
        self.stack.addWidget(view)
        if index is None or index > len(self.tabs):
            index = len(self.tabs)
        self.tabs.insert(index, t)
        self.tabbar.blockSignals(True)
        self.tabbar.insertTab(index, "")
        self.tabbar.blockSignals(False)
        if self.tab is None:
            self.tab = t
            self.stack.setCurrentWidget(view)
            self.tabbar.setCurrentIndex(index)
        self._update_tabstrip()
        return t

    def _new_tab_location(self) -> str:
        return self.loc if settings().get("new_tab_same_folder") and self.loc else places.HOME

    def new_tab(self, loc: str | None = None, select: list[str] | None = None, activate: bool = True,
                history: list[str] | None = None, hist_pos: int | None = None) -> Tab:
        """Open loc in a new tab next to the current one."""
        loc = loc or self._new_tab_location()
        cur = self.tabs.index(self.tab) if self.tab in self.tabs else len(self.tabs) - 1
        idx = cur + 1
        if not activate:         # background tabs line up after the ones already opened from this tab
            while idx < len(self.tabs) and self.tabs[idx].opener is self.tab:
                idx += 1
        t = self._add_tab(idx)
        t.opener = self.tab if not activate else None
        if activate:
            self.set_current_tab(self.tabs.index(t))
            self._open_in_tab(loc, select, history, hist_pos)
        else:
            self._in_background(t, lambda: self._open_in_tab(loc, select, history, hist_pos))
        return t

    def _open_in_tab(self, loc, select, history, hist_pos):
        if history:
            self.history = list(history)
            self.hist_pos = hist_pos if hist_pos is not None else len(history) - 1
            self.navigate(self.history[self.hist_pos], add_history=False, select=select)
        else:
            self.navigate(loc, select=select)
        if not self.loc:                 # navigation refused (missing folder...) -> home
            self.navigate(places.HOME)

    def _in_background(self, t: Tab, fn):
        """Run fn with t as the current tab without touching the shared UI."""
        prev, bg = self.tab, self._bg
        self.tab, self._bg = t, True
        try:
            fn()
        finally:
            self.tab, self._bg = prev, bg
            self.stack.setCurrentWidget(prev.view)

    def set_current_tab(self, i: int):
        if 0 <= i < len(self.tabs):
            if self.tabbar.currentIndex() != i:
                self.tabbar.setCurrentIndex(i)          # -> _tab_changed
            elif self.tabs[i] is not self.tab:
                self._tab_changed(i)

    def _tab_changed(self, i: int):
        if not (0 <= i < len(self.tabs)) or self.tabs[i] is self.tab:
            return
        if self.tab is not None and self.tab.loc:
            self.save_bag()
        had_focus = self.tab is not None and self.tab.view.hasFocus()
        self.tab = self.tabs[i]
        self.stack.setCurrentWidget(self.tab.view)
        if self.loc:
            self._sync_ui()
            self.cmdbar.current_view = self.view.mode
        if had_focus:
            self.view.setFocus()

    def _tab_moved(self, frm: int, to: int):
        t = self.tabs.pop(frm)
        self.tabs.insert(to, t)

    def close_tab(self, i: int | None = None):
        if i is None:
            i = self.tabs.index(self.tab)
        if not (0 <= i < len(self.tabs)):
            return
        if len(self.tabs) == 1:
            self.close()
            return
        t = self.tabs[i]
        if t.loc:
            if t is self.tab:
                self.save_bag()
            self._closed_tabs.append({"loc": t.loc, "history": list(t.history), "hist_pos": t.hist_pos, "index": i})
            del self._closed_tabs[:-20]
        if t is self.tab:
            j = i - 1 if i > 0 else 1
            self.set_current_tab(j)
        self.tabs.pop(i)
        self.tabbar.blockSignals(True)
        self.tabbar.removeTab(i)
        self.tabbar.setCurrentIndex(self.tabs.index(self.tab))
        self.tabbar.blockSignals(False)
        t.model._cancel.set()
        self.stack.removeWidget(t.view)
        t.view.deleteLater()
        t.model.deleteLater()
        self._update_tabstrip()

    def close_other_tabs(self, keep: int):
        k = self.tabs[keep]
        self.set_current_tab(keep)
        for t in [t for t in self.tabs if t is not k]:
            self.close_tab(self.tabs.index(t))

    def close_tabs_right(self, i: int):
        while len(self.tabs) > i + 1:
            self.close_tab(len(self.tabs) - 1)

    def duplicate_tab(self, i: int | None = None):
        t = self.tabs[i] if i is not None else self.tab
        self.set_current_tab(self.tabs.index(t))
        self.new_tab(t.loc, history=t.history, hist_pos=t.hist_pos)

    def reopen_closed_tab(self):
        while self._closed_tabs:
            d = self._closed_tabs.pop()
            if locations.exists(d["loc"]):
                nt = self.new_tab(d["loc"], history=d["history"], hist_pos=d["hist_pos"])
                j = self.tabs.index(nt)
                to = min(d["index"], len(self.tabs) - 1)
                if to != j:
                    self.tabbar.moveTab(j, to)
                return

    def next_tab(self, step: int = 1):
        if len(self.tabs) > 1:
            self.set_current_tab((self.tabs.index(self.tab) + step) % len(self.tabs))

    def _update_tab_label(self, t: Tab):
        if t not in self.tabs:
            return
        i = self.tabs.index(t)
        name = locations.display_name(t.loc) if t.loc else ""
        self.tabbar.setTabText(i, name.replace("&", "&&"))
        self.tabbar.setTabIcon(i, locations.icon(t.loc) if t.loc else QIcon())
        self.tabbar.setTabToolTip(i, t.loc if t.loc.startswith("/") else name)

    def _update_tabstrip(self):
        self.tabstrip.setVisible(len(self.tabs) > 1 or bool(settings().get("tab_bar_always")))

    def _tab_menu(self, i: int, pos):
        m = QMenu(self)
        m.addAction(T("tab_new"), lambda: self.new_tab(self._new_tab_location())).setShortcut(QKeySequence("Ctrl+T"))
        if i >= 0:
            m.addAction(T("tab_duplicate"), lambda: self.duplicate_tab(i))
            m.addAction(T("tab_open_new_window"), lambda: self._tab_to_window(i))
            m.addSeparator()
            m.addAction(T("tab_close"), lambda: self.close_tab(i)).setShortcut(QKeySequence("Ctrl+W"))
            a = m.addAction(T("tab_close_others"), lambda: self.close_other_tabs(i))
            a.setEnabled(len(self.tabs) > 1)
            a = m.addAction(T("tab_close_right"), lambda: self.close_tabs_right(i))
            a.setEnabled(i < len(self.tabs) - 1)
        m.addSeparator()
        a = m.addAction(T("tab_reopen"), self.reopen_closed_tab)
        a.setShortcut(QKeySequence("Ctrl+Shift+T"))
        a.setEnabled(bool(self._closed_tabs))
        m.exec(pos)

    def _tab_to_window(self, i: int):
        t = self.tabs[i]
        self.new_window(t.loc)
        if len(self.tabs) > 1:
            self.close_tab(i)

    def _tab_drop(self, i: int, md, action):
        if 0 <= i < len(self.tabs):
            self.handle_drop_loc(md, self.tabs[i].loc, action, False)

    def _open_index_new_tab(self, idx):
        it = self.model.item_at(idx)
        if it is not None:
            self.open_item(it, new_tab=True)

    def tab_session(self) -> dict:
        return {"tabs": [t.loc for t in self.tabs if t.loc and locations.kind(t.loc) != "search"],
                "current": max(0, [t for t in self.tabs if t.loc and locations.kind(t.loc) != "search"].index(self.tab))
                if self.tab in self.tabs and self.loc and locations.kind(self.loc) != "search" else 0}

    def restore_tabs(self, locs: list, current: int = 0):
        locs = [l for l in locs if l and locations.exists(l)] or [places.HOME]
        self.navigate(locs[0])
        for l in locs[1:]:
            self.new_tab(l, activate=False)
        if 0 < current < len(self.tabs):
            self.set_current_tab(current)

    def _shortcuts(self):
        def sc(keys, fn, ctx=Qt.ShortcutContext.WindowShortcut):
            for k in ([keys] if isinstance(keys, (str, QKeySequence.StandardKey)) else keys):
                s = QShortcut(QKeySequence(k), self)
                s.setContext(ctx)
                s.activated.connect(fn)
        sc(["Alt+Left", "Back", "Backspace"], self._back_key)
        sc("Alt+Right", self.go_forward)
        sc("Alt+Up", self.go_up)
        sc(["F5", "Ctrl+R", "Ctrl+F5"], self.refresh)
        sc(["Alt+D", "Ctrl+L"], lambda: self.addr.start_edit())
        sc("F4", lambda: self.addr.start_edit(False))
        sc(["F3", "Ctrl+E", "Ctrl+F"], lambda: (self.search_box.edit.setFocus(Qt.FocusReason.ShortcutFocusReason),
                                               self.search_box.edit.selectAll()))
        sc("Ctrl+N", lambda: self.new_window(self.loc))
        sc("Ctrl+Shift+N", self.cmd_new_folder)
        sc(["Ctrl+W", "Ctrl+F4"], lambda: self.close_tab())
        sc("Ctrl+T", lambda: self.new_tab(self._new_tab_location()))
        sc("Ctrl+Shift+T", self.reopen_closed_tab)
        sc(["Ctrl+Tab", "Ctrl+PgDown"], lambda: self.next_tab(1))
        sc(["Ctrl+Shift+Tab", "Ctrl+Backtab", "Ctrl+PgUp"], lambda: self.next_tab(-1))
        for n in range(1, 10):
            sc(f"Alt+{n}", lambda n=n: self.set_current_tab(len(self.tabs) - 1 if n == 9 else n - 1))
        sc("Alt+P", lambda: settings().set("layout_preview", not settings().get("layout_preview")))
        sc("Ctrl+Shift+E", lambda: self.nav.select_location(self.loc, expand_to=True))
        sc("F11", self._toggle_fullscreen)
        sc("F6", self._cycle_focus)
        sc("F10", self._show_menubar_temp)
        sc("F1", self.cmd_help)
        for ch, idx in self.mbar.mnemonics():
            sc(f"Alt+{ch.upper()}", lambda idx=idx: self._open_menu_mnemonic(idx))
        for i, m in enumerate(["xlarge", "large", "medium", "small", "details", "list", "tiles", "content"]):
            sc(f"Ctrl+Shift+{i + 1}", lambda m=m: self.set_view_mode(m))
        # file commands bound to the view
        vctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for keys, fn in ((["Ctrl+C", "Ctrl+Insert"], self.cmd_copy), ("Ctrl+X", self.cmd_cut),
                         (["Ctrl+V", "Shift+Insert"], self.cmd_paste), ("Ctrl+A", self.cmd_select_all),
                         (["Delete", "Ctrl+D"], lambda: self.cmd_delete(False)), ("Shift+Delete", lambda: self.cmd_delete(True)),
                         ("Alt+Return", self.cmd_properties), ("Ctrl+Z", self.cmd_undo), ("Ctrl+Y", self.cmd_redo),
                         ("Ctrl+Shift+C", self.cmd_copy_path)):
            for k in ([keys] if isinstance(keys, str) else keys):
                s = QShortcut(QKeySequence(k), self.stack)
                s.setContext(vctx)
                s.activated.connect(fn)
            if fn in (self.cmd_undo, self.cmd_redo, self.cmd_properties):
                for k in ([keys] if isinstance(keys, str) else keys):
                    s2 = QShortcut(QKeySequence(k), self.nav)
                    s2.setContext(vctx)
                    s2.activated.connect(fn)

    def _back_key(self):
        fw = QApplication.focusWidget()
        if fw is not None and fw.inherits("QLineEdit") and fw.isVisible():
            return
        self.go_back()

    def _toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _cycle_focus(self):
        order = [self.addr, self.nav, self.view, self.search_box.edit]
        fw = QApplication.focusWidget()
        i = 0
        for k, w in enumerate(order):
            if fw is w or (fw is not None and w.isAncestorOf(fw)):
                i = k + 1
        w = order[i % len(order)]
        if w is self.addr:
            self.addr.start_edit()
        else:
            w.setFocus()

    # ------------------------------------------------------------------ layout
    def apply_layout(self):
        s = settings()
        self.mbar.setVisible(s.get("layout_menubar") or s.get("always_menus") or self._menubar_temp)
        self.details.setVisible(s.get("layout_details"))
        self.preview.setVisible(s.get("layout_preview"))
        self.nav.setVisible(s.get("layout_nav"))
        self.status.setVisible(s.get("status_bar"))
        self.cmdbar.set_preview_on(s.get("layout_preview"))
        self.libpane.setVisible(locations.kind(self.loc) == "library" and s.get("layout_library"))
        if s.get("layout_preview"):
            self._on_selection()

    def _on_setting(self, key):
        if key.startswith("layout_") or key in ("status_bar", "always_menus"):
            self.apply_layout()
        if key in ("checkboxes", "always_icons", "file_icon_on_thumbs", "single_click", "hide_extensions"):
            for t in self.tabs:
                t.view.viewport().update()
        if key == "tab_bar_always":
            self._update_tabstrip()
        if key == "language":
            pass

    def _show_menubar_temp(self):
        if self.mbar.isVisible() and not self._menubar_temp:
            self.mbar.focus_first()
            return
        self._menubar_temp = True
        self.mbar.show()
        self.mbar.focus_first()

    def _open_menu_mnemonic(self, idx: int):
        if not self.mbar.isVisible():
            self._menubar_temp = True
            self.mbar.show()
        self.mbar.open_menu(idx)

    def _hide_menubar_temp(self):
        if self._menubar_temp and not self.mbar.any_menu_open():
            self._menubar_temp = False
            self.apply_layout()
            self.view.setFocus()

    def keyPressEvent(self, ev):
        self._alt_down = ev.key() == Qt.Key.Key_Alt and not ev.isAutoRepeat()
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if ev.key() == Qt.Key.Key_Alt and getattr(self, "_alt_down", False):
            self._alt_down = False
            if QApplication.activePopupWidget() is None:
                if self._menubar_temp:
                    self._hide_menubar_temp()
                elif not self.mbar.isVisible():
                    QTimer.singleShot(0, self._show_menubar_temp)
                else:
                    self.mbar.focus_first()
            return
        self._alt_down = False
        super().keyReleaseEvent(ev)

    def eventFilter(self, obj, ev):
        """On the item view: 'When typing into list view: Automatically type into the Search Box'."""
        if ev.type() == QEvent.Type.KeyPress:
            if ev.key() == Qt.Key.Key_Alt:
                self._alt_down = not ev.isAutoRepeat()
                return False
            self._alt_down = False
            if settings().get("typing_mode") == "search" and ev.text() and ev.text().isprintable() \
                    and not ev.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier) \
                    and self.view.state() != self.view.State.EditingState and ev.text() != " ":
                self.search_box.edit.setFocus()
                self.search_box.edit.insert(ev.text())
                self.search_box._edited(self.search_box.edit.text())
                return True
        elif ev.type() == QEvent.Type.KeyRelease and ev.key() == Qt.Key.Key_Alt and getattr(self, "_alt_down", False):
            self.keyReleaseEvent(ev)
            return True
        return False

    # ------------------------------------------------------------------ navigation
    def navigate(self, loc: str, add_history: bool = True, select: list[str] | None = None):
        if not loc:
            return
        if loc.startswith("dev:"):
            self.mount_and_open(loc[4:])
            return
        if loc == "shell:favorites":
            return
        if locations.kind(loc) == "fs":
            loc = os.path.abspath(loc)
            if os.path.isfile(loc):
                if ziputil.is_zip_path(loc):
                    loc = ziputil.make(loc, "")
                else:
                    self.navigate(os.path.dirname(loc), add_history, [loc])
                    return
            elif not os.path.isdir(loc) or not os.access(loc, os.R_OK | os.X_OK):
                from . import netloc
                uri = netloc.gvfs_path_to_uri(loc) if "/gvfs/" in loc else None
                if uri:                  # gvfs FUSE path that cannot be read: go through GIO instead
                    self.navigate("net:" + uri, add_history, select)
                    return
                if not os.path.isdir(loc):
                    QMessageBox.warning(self, T("location_unavailable"), T("location_unavailable_text", loc))
                    return
            if locations.kind(loc) == "fs" and not os.access(loc, os.R_OK | os.X_OK):
                QMessageBox.warning(self, T("location_unavailable"), T("access_denied_text", loc))
                return
        elif not locations.exists(loc):
            QMessageBox.warning(self, T("location_unavailable"), T("location_unavailable_text", loc))
            return
        if self.loc:
            self.save_bag()
        prev = self.loc
        self.loc = loc
        if add_history:
            self.history = self.history[:self.hist_pos + 1]
            if not self.history or self.history[-1] != loc:
                self.history.append(loc)
            self.hist_pos = len(self.history) - 1
        self._select_after = select
        if select is None and prev and locations.parent(prev) == loc:
            self._select_after = [prev if prev.startswith("/") or prev.startswith("zip:") else ""]
        self._load_bag(loc)
        self.model.set_location(loc)
        self.view.empty_text = self.model.provider.empty_text()
        self.view.verticalScrollBar().setValue(0)
        self.view.horizontalScrollBar().setValue(0)
        self._update_tab_label(self.tab)
        if locations.kind(loc) == "fs":
            places.add_recent_place(loc)
        if not self._bg:
            self._sync_ui()

    def _sync_ui(self):
        """Bring the window chrome (address bar, search box, title, nav pane, panes) in line with the
        current tab."""
        loc = self.loc
        self.addr.set_location(loc)
        k = locations.kind(loc)
        if k == "search":
            s = locations.parse_search(loc)
            self.search_box.set_scope(locations.display_name(s["scope"]))
            self.search_box.set_text(s["q"])
        else:
            self.search_box.set_scope(locations.display_name(loc))
            self.search_box._timer.stop()
            self.search_box.set_text("")
        self.setWindowTitle(locations.display_name(loc))
        self.nav.select_location(loc)
        self.orbs.set_state(self.hist_pos > 0, self.hist_pos < len(self.history) - 1,
                            T("back_to", locations.display_name(self.history[self.hist_pos - 1])) if self.hist_pos > 0 else "",
                            T("forward_to", locations.display_name(self.history[self.hist_pos + 1]))
                            if self.hist_pos < len(self.history) - 1 else "")
        lib = places.libraries().get(locations.library_name(loc)) if k == "library" else None
        self.libpane.set_library(lib, self._arrange.get(loc, "folder"))
        self.apply_layout()
        self.update_commands()
        self._update_status()
        self._on_selection()
        self._update_ghost()

    def go_back(self):
        if self.hist_pos > 0:
            self.hist_pos -= 1
            self.navigate(self.history[self.hist_pos], add_history=False)

    def go_forward(self):
        if self.hist_pos < len(self.history) - 1:
            self.hist_pos += 1
            self.navigate(self.history[self.hist_pos], add_history=False)

    def go_up(self):
        p = locations.parent(self.loc)
        if p:
            self.navigate(p, select=[self.loc])

    def refresh(self):
        places.drives().refresh()
        self.model.reload()
        self.nav.viewport().update()

    def _history_menu(self, pos: QPoint):
        m = QMenu(self)
        lo = max(0, self.hist_pos - 8)
        hi = min(len(self.history), self.hist_pos + 9)
        for i in range(hi - 1, lo - 1, -1):
            loc = self.history[i]
            a = m.addAction(locations.icon(loc), locations.display_name(loc))
            if i == self.hist_pos:
                a.setCheckable(True)
                a.setChecked(True)
            a.triggered.connect(lambda _=False, i=i: self._jump(i))
        m.addSeparator()
        a = m.addAction(T("recent_pages_clear"))
        a.triggered.connect(self._clear_history)
        m.exec(pos)

    def _jump(self, i):
        self.hist_pos = i
        self.navigate(self.history[i], add_history=False)

    def _clear_history(self):
        self.history = [self.loc]
        self.hist_pos = 0
        self.orbs.set_state(False, False)

    def do_search(self, text: str):
        k = locations.kind(self.loc)
        scope = locations.parse_search(self.loc)["scope"] if k == "search" else self.loc
        if k in ("trash",):
            scope = places.HOME
        loc = locations.search_location(scope, text, settings().get("search_contents"))
        replace = k == "search"
        if replace and self.history:
            self.history[self.hist_pos] = loc
            self.navigate(loc, add_history=False)
        else:
            self.navigate(loc)
        self.search_box.edit.setFocus()

    def clear_search(self):
        if locations.kind(self.loc) == "search":
            scope = locations.parse_search(self.loc)["scope"]
            if self.hist_pos > 0 and self.history[self.hist_pos - 1] == scope:
                self.go_back()
            else:
                self.navigate(scope)

    def new_window(self, loc: str | None = None, select: str | None = None):
        w = MainWindow(loc or self.loc, select)
        g = self.geometry()
        w.resize(self.size())
        w.move(g.x() + 26, g.y() + 26)
        w.show()
        return w

    # ------------------------------------------------------------------ per-folder views
    def _bag_key(self, loc: str) -> str:
        k = locations.kind(loc)
        if k == "search":
            return "__search__"
        if k == "zip":
            return "__zip__"
        if k == "net":
            return "__net__"
        return loc

    def _load_bag(self, loc: str):
        bag = settings().bag(self._bag_key(loc))
        prov_type = self._folder_type(loc)
        if bag is None:
            bag = settings().type_default(prov_type)
        mode, cols = DEFAULT_VIEWS.get(prov_type, DEFAULT_VIEWS["general"])
        if bag:
            mode = bag.get("mode", mode)
            cols = [c for c in bag.get("columns", cols) if c in COLUMNS] or cols
        widths = (bag or {}).get("widths", {})
        sort = (bag or {}).get("sort", "name")
        desc = (bag or {}).get("desc", False)
        group = (bag or {}).get("group")
        if bag is None and prov_type in ("computer", "netproto"):
            group = "category"
        if bag is None and prov_type == "trash":
            sort = "name"
        self.model.set_columns(cols, widths)
        self.model.sort_col = sort if sort in COLUMNS else "name"
        self.model.sort_desc = bool(desc)
        self.model.group_col = group if group in COLUMNS else None
        self.model.group_desc = bool((bag or {}).get("gdesc", False))
        self.view._collapsed = set()
        self.view.set_mode(mode)
        if not self._bg:
            self.cmdbar.current_view = mode

    def _folder_type(self, loc: str) -> str:
        k = locations.kind(loc)
        custom = settings().bag(loc)
        if custom and custom.get("ftype"):
            return custom["ftype"]
        if k == "fs":
            return places.folder_type_for(loc)
        if k == "library":
            lib = places.libraries().get(locations.library_name(loc))
            return (lib or {}).get("type", "general")
        return {"computer": "computer", "trash": "trash", "libraries": "libraries", "search": "search",
                "zip": "zip", "network": "network", "net": "network", "netproto": "netproto"}.get(k, "general")

    def current_bag(self) -> dict:
        order = self.view.column_order()
        cols = [self.model.columns[i] for i in order if i < len(self.model.columns)]
        bag = {"mode": self.view.mode, "columns": cols,
               "widths": {c: self.model.width_of(c) for c in cols},
               "sort": self.model.sort_col, "desc": self.model.sort_desc, "group": self.model.group_col,
               "gdesc": self.model.group_desc}
        old = settings().bag(self._bag_key(self.loc)) or {}
        if old.get("ftype"):
            bag["ftype"] = old["ftype"]
        return bag

    def save_bag(self):
        if self.loc:
            settings().set_bag(self._bag_key(self.loc), self.current_bag())

    def _save_bag_soon(self):
        if not hasattr(self, "_bag_timer"):
            self._bag_timer = QTimer(self)
            self._bag_timer.setSingleShot(True)
            self._bag_timer.setInterval(500)
            self._bag_timer.timeout.connect(self.save_bag)
        self._bag_timer.start()

    def set_view_mode(self, mode: str):
        self.view.set_mode(mode)

    def cycle_view(self):
        order = ["list", "details", "tiles", "content", "large"]
        cur = self.view.mode
        i = order.index(cur) if cur in order else -1
        self.view.set_mode(order[(i + 1) % len(order)])

    def _on_mode_changed(self, mode):
        self.cmdbar.current_view = mode
        self._save_bag_soon()
        self.update_view_actions()

    def _sort_clicked(self, col):
        self.model.set_sort(col)
        self.view._sync_header()
        self._save_bag_soon()

    def set_arrange(self, key: str):
        self._arrange[self.loc] = key
        if key == "folder":
            self.model.set_group(None)
        else:
            self.model.set_group(key)
        lib = places.libraries().get(locations.library_name(self.loc))
        self.libpane.set_library(lib, key)

    # ------------------------------------------------------------------ model events
    def _before_reset(self, t: Tab):
        if t.restoring:
            return
        v, m = t.view, t.model
        t.saved_sel = [m.rows[r].path for r in v.selected_rows() if r < len(m.rows)]
        cur = v.currentIndex()
        t.saved_cur = m.rows[cur.row()].path if cur.isValid() and cur.row() < len(m.rows) else ""
        t.saved_scroll = (v.horizontalScrollBar().value(), v.verticalScrollBar().value())

    def _after_reset(self, t: Tab):
        t.restoring = True
        v, m = t.view, t.model
        try:
            want = t.select_after if t.select_after else t.saved_sel
            sm = v.selectionModel()
            sm.clearSelection()
            first = None
            if want:
                wanted = set(want)
                for i, it in enumerate(m.rows):
                    if it.path in wanted:
                        idx = m.index(i, 0)
                        sm.select(idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
                        if first is None:
                            first = idx
            curp = t.saved_cur if not t.select_after else (want[0] if want else "")
            r = m.row_of_path(curp) if curp else -1
            if r >= 0:
                sm.setCurrentIndex(m.index(r, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
            elif first is not None:
                sm.setCurrentIndex(first, QItemSelectionModel.SelectionFlag.NoUpdate)
            if not t.select_after:
                h, vv = t.saved_scroll
                QTimer.singleShot(0, v, lambda: (v.horizontalScrollBar().setValue(h), v.verticalScrollBar().setValue(vv)))
            elif first is not None:
                QTimer.singleShot(0, v, lambda f=first: v.scrollTo(f))
        finally:
            t.restoring = False

    def _on_contents(self, t: Tab):
        v, m = t.view, t.model
        if t.select_after and not m.is_loading and m._deferred is None:
            t.select_after = None
        if t.edit_after:
            r = m.row_of_path(t.edit_after)
            if r >= 0:
                t.edit_after = None
                idx = m.index(r, 0)
                v.selectionModel().setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.ClearAndSelect |
                                                   QItemSelectionModel.SelectionFlag.Rows)
                QTimer.singleShot(30, v, lambda: (v.scrollTo(idx), v.edit(idx)))
        if t is not self.tab:
            return
        self._update_status()
        if not v.selectionModel().hasSelection():
            self._on_selection()

    def _on_loading(self, t: Tab, busy: bool):
        t.view.viewport().update()
        if t is not self.tab:
            return
        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor) if not QApplication.overrideCursor() else None
        else:
            while QApplication.overrideCursor():
                QApplication.restoreOverrideCursor()
        if locations.kind(self.loc) == "search":
            self._update_status()
            self.addr.go_btn.setToolTip(T("searching") if busy else T("refresh_tip"))

    def _on_load_failed(self, t: Tab, exc):
        if t is not self.tab and t in self.tabs:
            self.set_current_tab(self.tabs.index(t))
        from . import ftpclient
        if isinstance(exc, (ftpclient.NeedsLogin, ftpclient.CertQuestion)):
            if self.ftp_login(exc):
                self.model.reload()
            elif self.hist_pos > 0:
                self.go_back()
            return
        if isinstance(exc, locations.UseGio):
            loc = "net:" + exc.uri
            if self.history and 0 <= self.hist_pos < len(self.history) and self.history[self.hist_pos] == self.loc:
                self.history[self.hist_pos] = loc
            self.navigate(loc, add_history=False)
            return
        if isinstance(exc, locations.NetError):
            QMessageBox.warning(self, T("location_unavailable"), T("net_error_text", self.loc, exc.strerror or str(exc)))
            return
        if isinstance(exc, locations.NeedsMount):
            self.mount_network(exc.uri)
            return
        if isinstance(exc, PermissionError):
            QMessageBox.warning(self, T("location_unavailable"), T("access_denied_text", self.loc))
        elif isinstance(exc, FileNotFoundError):
            QMessageBox.warning(self, T("location_unavailable"), T("location_unavailable_text", self.loc))
        else:
            QMessageBox.warning(self, T("location_unavailable"), f"{self.loc}\n\n{exc}")

    # ------------------------------------------------------------------ selection
    def selected_items(self) -> list:
        return [self.model.rows[r] for r in self.view.selected_rows() if r < len(self.model.rows)]

    def _on_selection(self, *a):
        items = self.selected_items()
        if self.details.isVisible():
            if items:
                self.details.show_items(self.model, items)
            else:
                extra = []
                k = locations.kind(self.loc)
                if k == "library":
                    lib = places.libraries().get(locations.library_name(self.loc))
                    extra.append((T("includes"), T("n_locations", len((lib or {}).get("locations", [])))))
                elif k == "fs":
                    d = places.drives().for_path(self.loc)
                    if d and d.get("size") and self.loc == d.get("mount"):
                        extra.append((T("col_free_space"), util.fmt_size_short(d.get("free") or 0)))
                        extra.append((T("col_total_size"), util.fmt_size_short(d["size"])))
                elif k == "search":
                    extra.append((T("search_label"), locations.parse_search(self.loc)["q"]))
                self.details.show_folder(self.model.provider.title() if self.model.provider else "",
                                         locations.icon(self.loc), self.model.rowCount(), extra)
        if self.preview.isVisible():
            self.preview.show_items(items)
        self._update_status()
        self.update_commands()

    def _update_status(self):
        n = self.model.rowCount()
        items = self.selected_items()
        txt = (T("one_item") if n == 1 else T("n_items", util.fmt_int(n)))
        if locations.kind(self.loc) == "search" and self.model.is_loading:
            txt = T("searching_found", util.fmt_int(n))
        if items:
            txt = T("n_items_selected", util.fmt_int(len(items))) if len(items) != 1 else T("one_item_selected")
            sizes = [i.size for i in items if not i.is_dir and i.size is not None]
            if sizes:
                txt += "    " + util.fmt_size_short(sum(sizes))
        self.status_lbl.setText(txt)

    def _update_ghost(self):
        paths, cut, _ = clipboard.get_files()
        self.model.set_ghost(set(paths) if cut else set())

    # ------------------------------------------------------------------ geometry
    def _restore_geometry(self):
        g = settings().q.value("window/geometry")
        if isinstance(g, QByteArray) and not g.isEmpty() and len(MainWindow.instances) == 1:
            self.restoreGeometry(g)
        else:
            self.resize(980, 640)
        s = settings().q.value("window/hsplit")
        if isinstance(s, QByteArray):
            self.hsplit.restoreState(s)
        s = settings().q.value("window/vsplit")
        if isinstance(s, QByteArray):
            self.vsplit.restoreState(s)
        s = settings().q.value("window/inner")
        if isinstance(s, QByteArray):
            self.vsplit_inner.restoreState(s)
        self.apply_layout()

    def closeEvent(self, e):
        self.save_bag()
        settings().q.setValue("window/geometry", self.saveGeometry())
        settings().q.setValue("window/hsplit", self.hsplit.saveState())
        settings().q.setValue("window/vsplit", self.vsplit.saveState())
        settings().q.setValue("window/inner", self.vsplit_inner.saveState())
        if settings().get("restore_windows"):
            sess = [w.tab_session() for w in MainWindow.instances]
            settings().set_json("open_windows", [x for x in sess if x["tabs"]])
        for sig, slot in ((settings().changed, self._on_setting), (undo.manager().changed, self.update_commands),
                          (clipboard.watch.changed, self._update_ghost),
                          (QApplication.clipboard().dataChanged, self._update_ghost)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        for t in self.tabs:
            t.model._cancel.set()
        self.preview._stop_media()
        if self in MainWindow.instances:
            MainWindow.instances.remove(self)
        super().closeEvent(e)


class _GlassRow(QWidget):
    """The light-blue 'glass' strip that holds the address row."""

    def paintEvent(self, e):
        from PySide6.QtGui import QLinearGradient
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(219, 233, 248))
        g.setColorAt(1, QColor(198, 218, 241))
        p.fillRect(self.rect(), g)
        p.setPen(QColor(170, 190, 215))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
