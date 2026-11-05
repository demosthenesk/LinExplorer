"""Menus, command bar contents and every command of the Explorer window."""
from __future__ import annotations

import os
import shutil

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QCursor, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMenu, QMessageBox

from . import (clipboard, fileops, icons, locations, metadata, mime, netloc, places, sharing, trash, undo, util,
               ziputil)
from .fileops import Job, Src
from .i18n import T, strip_amp
from .model import COLUMNS
from .settings import DATA_DIR, settings

VIEW_KEYS = ["xlarge", "large", "medium", "small", "list", "details", "tiles", "content"]
PRINTABLE = ("application/pdf", "text/plain", "image/jpeg", "image/png", "application/postscript", "image/gif",
             "image/tiff", "image/bmp")


class CommandsMixin:
    # ================================================================== menu bar
    def build_menubar(self, mb):
        self.m_file = mb.addMenu(T("m_file"))
        self.m_file.aboutToShow.connect(self._fill_file_menu)
        m = mb.addMenu(T("m_edit"))
        self.a_undo = m.addAction(T("m_undo"), self.cmd_undo)
        self.a_redo = m.addAction(T("m_redo"), self.cmd_redo)
        m.addSeparator()
        self.a_cut = m.addAction(T("m_cut"), self.cmd_cut)
        self.a_copy = m.addAction(T("m_copy"), self.cmd_copy)
        self.a_paste = m.addAction(T("m_paste"), self.cmd_paste)
        self.a_paste_sc = m.addAction(T("m_paste_shortcut"), self.cmd_paste_shortcut)
        m.addSeparator()
        m.addAction(T("m_copy_to_folder"), lambda: self.cmd_copy_move_to(False))
        m.addAction(T("m_move_to_folder"), lambda: self.cmd_copy_move_to(True))
        m.addSeparator()
        m.addAction(T("m_select_all"), self.cmd_select_all)
        m.addAction(T("m_invert_selection"), self.cmd_invert_selection)
        m.aboutToShow.connect(self._update_edit_menu)
        self.m_view = mb.addMenu(T("m_view"))
        self.m_view.aboutToShow.connect(self._fill_view_menu)
        m = mb.addMenu(T("m_tools"))
        m.addAction(T("m_map_drive"), self.cmd_map_drive)
        m.addAction(T("m_disconnect_drive"), self.cmd_disconnect_drive)
        m.addSeparator()
        m.addAction(T("m_open_terminal"), lambda: mime.open_terminal(self._fs_dir() or places.HOME))
        m.addAction(T("m_set_default_fm"), self.cmd_set_default_fm)
        m.addSeparator()
        m.addAction(T("m_folder_options"), self.cmd_folder_options)
        m = mb.addMenu(T("m_help"))
        m.addAction(T("m_view_help"), self.cmd_help)
        m.addSeparator()
        m.addAction(T("m_about"), self.cmd_about)

    def _update_edit_menu(self):
        um = undo.manager()
        self.a_undo.setText(T("m_undo_x", um.undo_text()) if um.can_undo() else T("m_undo"))
        self.a_undo.setEnabled(um.can_undo())
        self.a_redo.setText(T("m_redo_x", um.redo_text()) if um.can_redo() else T("m_redo"))
        self.a_redo.setEnabled(um.can_redo())
        has = bool(self.selected_items())
        self.a_cut.setEnabled(has and self._can_delete())
        self.a_copy.setEnabled(has)
        self.a_paste.setEnabled(clipboard.has_files() and self._can_paste_here())
        self.a_paste_sc.setEnabled(clipboard.has_files() and bool(self._fs_dir()))

    def _fill_file_menu(self):
        m = self.m_file
        m.clear()
        items = self.selected_items()
        if items:
            self._fill_item_menu(m, items, from_menubar=True)
        else:
            self._fill_background_menu(m, from_menubar=True)
        m.addSeparator()
        m.addAction(T("tab_new"), lambda: self.new_tab(self._new_tab_location())).setShortcut(QKeySequence("Ctrl+T"))
        m.addAction(T("m_new_window"), lambda: self.new_window(self.loc)).setShortcut(QKeySequence("Ctrl+N"))
        a = m.addAction(T("tab_reopen"), self.reopen_closed_tab)
        a.setEnabled(bool(self._closed_tabs))
        m.addSeparator()
        if len(self.tabs) > 1:
            m.addAction(T("tab_close"), lambda: self.close_tab()).setShortcut(QKeySequence("Ctrl+W"))
        m.addAction(T("m_close"), self.close)

    def _fill_view_menu(self):
        m = self.m_view
        m.clear()
        a = m.addAction(T("m_status_bar"))
        a.setCheckable(True)
        a.setChecked(settings().get("status_bar"))
        a.triggered.connect(lambda c: settings().set("status_bar", c))
        m.addSeparator()
        self._add_view_modes(m)
        m.addSeparator()
        m.addMenu(self._sort_menu(m))
        m.addMenu(self._group_menu(m))
        m.addAction(T("m_choose_details"), self.cmd_choose_details)
        m.addAction(T("m_customize_folder"), self.cmd_customize).setEnabled(locations.kind(self.loc) == "fs")
        m.addSeparator()
        go = m.addMenu(T("m_go_to"))
        go.addAction(T("m_back"), self.go_back).setEnabled(self.hist_pos > 0)
        go.addAction(T("m_forward"), self.go_forward).setEnabled(self.hist_pos < len(self.history) - 1)
        go.addAction(T("m_up_level"), self.go_up).setEnabled(bool(locations.parent(self.loc)))
        if len(self.tabs) > 1:
            tm = m.addMenu(T("m_tabs"))
            for i, t in enumerate(self.tabs):
                a = tm.addAction(locations.icon(t.loc), locations.display_name(t.loc),
                                 lambda i=i: self.set_current_tab(i))
                a.setCheckable(True)
                a.setChecked(t is self.tab)
            tm.addSeparator()
            tm.addAction(T("tab_next"), lambda: self.next_tab(1)).setShortcut(QKeySequence("Ctrl+Tab"))
            tm.addAction(T("tab_prev"), lambda: self.next_tab(-1)).setShortcut(QKeySequence("Ctrl+Shift+Tab"))
        m.addAction(T("m_refresh"), self.refresh)

    def update_view_actions(self):
        pass

    def _add_view_modes(self, m: QMenu):
        g = QActionGroup(m)
        for k in VIEW_KEYS:
            a = m.addAction(T("mv_" + k))
            a.setCheckable(True)
            a.setChecked(self.view.mode == k)
            a.triggered.connect(lambda _=False, k=k: self.set_view_mode(k))
            g.addAction(a)

    def _sort_menu(self, parent) -> QMenu:
        sm = QMenu(T("m_sort_by"), parent)
        g = QActionGroup(sm)
        for col in self.model.columns:
            a = sm.addAction(T(COLUMNS[col][0]))
            a.setCheckable(True)
            a.setChecked(self.model.sort_col == col)
            a.triggered.connect(lambda _=False, c=col: (self.model.set_sort(c, self.model.sort_desc),
                                                        self.view._sync_header(), self._save_bag_soon()))
            g.addAction(a)
        sm.addSeparator()
        g2 = QActionGroup(sm)
        for desc, key in ((False, "m_ascending"), (True, "m_descending")):
            a = sm.addAction(T(key))
            a.setCheckable(True)
            a.setChecked(self.model.sort_desc == desc)
            a.triggered.connect(lambda _=False, d=desc: (self.model.set_sort(self.model.sort_col, d),
                                                         self.view._sync_header(), self._save_bag_soon()))
            g2.addAction(a)
        sm.addSeparator()
        sm.addAction(T("m_more"), self.cmd_choose_details)
        return sm

    def _group_menu(self, parent) -> QMenu:
        gm = QMenu(T("m_group_by"), parent)
        g = QActionGroup(gm)
        for col in self.model.columns + (["category"] if self.model.provider and
                                         self.model.provider.folder_type == "computer" else []):
            if col in ("in_folder",) and False:
                continue
            a = gm.addAction(T(COLUMNS[col][0]))
            a.setCheckable(True)
            a.setChecked(self.model.group_col == col)
            a.triggered.connect(lambda _=False, c=col: (self.model.set_group(c), self._save_bag_soon()))
            g.addAction(a)
        a = gm.addAction(T("m_none"))
        a.setCheckable(True)
        a.setChecked(not self.model.group_col)
        a.triggered.connect(lambda: (self.model.set_group(None), self._save_bag_soon()))
        g.addAction(a)
        gm.addSeparator()
        g2 = QActionGroup(gm)
        for desc, key in ((False, "m_ascending"), (True, "m_descending")):
            a = gm.addAction(T(key))
            a.setCheckable(True)
            a.setChecked(self.model.group_desc == desc)
            a.setEnabled(bool(self.model.group_col))
            a.triggered.connect(lambda _=False, d=desc: (self.model.set_group(self.model.group_col, d), self._save_bag_soon()))
            g2.addAction(a)
        gm.addSeparator()
        gm.addAction(T("m_more"), self.cmd_choose_details)
        if self.model.group_col:
            gm.addSeparator()
            gm.addAction(T("m_expand_groups"), self.view.expand_all)
            gm.addAction(T("m_collapse_groups"), self.view.collapse_all)
        return gm

    # ================================================================== command bar
    def update_commands(self):
        k = locations.kind(self.loc)
        items = self.selected_items()
        ft = self.model.provider.folder_type if self.model.provider else "general"
        specs = [{"text": T("cb_organize"), "menu": self._organize_menu, "tip": T("tip_organize")}]
        one = items[0] if len(items) == 1 else None
        if k == "trash":
            if not items:
                specs.append({"text": T("cb_empty_bin"), "click": self.cmd_empty_trash,
                              "enabled": self.model.rowCount() > 0, "tip": T("tip_empty_bin")})
                specs.append({"text": T("cb_restore_all"), "click": lambda: self.cmd_restore(all_=True),
                              "enabled": self.model.rowCount() > 0, "tip": T("tip_restore")})
            else:
                specs.append({"text": T("cb_restore_this") if one else T("cb_restore_selected"),
                              "click": self.cmd_restore, "tip": T("tip_restore")})
                specs.append({"text": T("cb_restore_to"), "click": self.cmd_restore_to})
                specs.append({"text": T("cb_delete_perm"), "click": lambda: self.cmd_delete(True)})
        elif k == "computer":
            if one and one.kind == "drive":
                specs.append({"text": T("cb_properties"), "click": self.cmd_properties})
                d = one.extra.get("drive") or {}
                if d.get("removable") or d.get("kind") == "optical":
                    specs.append({"text": T("cb_eject"), "click": lambda: self.cmd_eject(one), "tip": T("tip_eject")})
                if not d.get("mount") and d.get("dev"):
                    specs.append({"text": T("cb_mount"), "click": lambda: self.mount_and_open(d["dev"], open_=False)})
                if d.get("mount") and d.get("kind") == "network":
                    specs.append({"text": T("cb_disconnect"), "click": lambda: self.cmd_net_disconnect(one)})
            else:
                specs.append({"text": T("cb_system_properties"), "click": self.cmd_system_properties,
                              "tip": T("tip_system_properties")})
                specs.append({"text": T("cb_uninstall"), "click": self.cmd_uninstall, "tip": T("tip_uninstall")})
            specs.append({"text": T("cb_map_drive"), "click": self.cmd_map_drive, "tip": T("tip_map_drive")})
            specs.append({"text": T("cb_control_panel"), "click": self.cmd_control_panel, "tip": T("tip_control_panel")})
        elif k == "libraries":
            specs.append({"text": T("cb_new_library"), "click": self.cmd_new_library, "tip": T("tip_new_library")})
        elif k in ("network", "netproto", "net") or (one is not None and one.extra.get("netcat")):
            proto = self.loc.rsplit(":", 1)[1] if k == "netproto" else None
            if one is not None and one.kind == "net":
                specs.append({"text": T("cb_connect"), "click": lambda: self.cmd_net_connect(one)})
            if one is not None and one.extra.get("netcat") == "connected":
                specs.append({"text": T("cb_disconnect"), "click": lambda: self.cmd_net_disconnect(one)})
            elif one is None and k == "net" and self._network_connection_here():
                specs.append({"text": T("cb_disconnect"), "click": lambda: self.disconnect_location(self.loc)})
            specs.append({"text": T("cb_add_net_location"), "click": lambda: self.cmd_add_net_location(proto),
                          "tip": T("tip_add_net_location")})
            specs.append({"text": T("cb_map_drive"), "click": self.cmd_map_drive, "tip": T("tip_map_drive")})
            specs.append({"text": T("cb_net_center"), "click": self.cmd_net_center, "tip": T("tip_net_center")})
        elif k == "zip":
            specs.append({"text": T("cb_extract_all"), "click": self.cmd_extract_all, "tip": T("tip_extract_all")})
        else:
            if k == "search":
                specs.append({"text": T("cb_save_search"), "click": self.cmd_save_search, "tip": T("tip_save_search")})
            if one is not None and one.kind in ("file",) and not one.is_dir:
                n = one.mime.name()
                if ziputil.is_zip_path(one.path):
                    specs.append({"text": T("cb_extract_all"), "click": lambda: self.cmd_extract_all(one.path),
                                  "tip": T("tip_extract_all")})
                if n.startswith("image/"):
                    specs.append({"text": T("cb_preview"), "icon": self._default_icon(one), "menu": self._open_with_menu,
                                  "split": True, "click": lambda: self.open_item(one)})
                elif n.startswith("audio/"):
                    specs.append({"text": T("cb_play"), "icon": self._default_icon(one), "menu": self._open_with_menu,
                                  "split": True, "click": lambda: self.open_item(one)})
                else:
                    specs.append({"text": T("cb_open"), "icon": self._default_icon(one), "menu": self._open_with_menu,
                                  "split": True, "click": lambda: self.open_item(one)})
            elif one is not None and (one.is_dir or one.kind in ("library", "drive")):
                specs.append({"text": T("cb_open"), "click": lambda: self.open_item(one)})
            elif len(items) > 1:
                specs.append({"text": T("cb_open"), "click": lambda: self.open_items(self.selected_items())})
            fsdir = self._fs_dir()
            if (not items or (one is not None and one.kind == "dir")) and k == "fs":
                specs.append({"text": T("cb_include_library"), "menu": self._include_menu, "tip": T("tip_include_library")})
            if (k in ("fs", "library") or items) and sharing.available():
                specs.append({"text": T("cb_share_with"), "menu": self._share_menu, "tip": T("tip_share_with")})
            few = items[:200]
            if items and any(i.kind == "file" and i.mime.name() in PRINTABLE for i in few):
                specs.append({"text": T("cb_print"), "click": self.cmd_print, "tip": T("tip_print")})
            if items and all(i.kind == "file" and not i.is_dir for i in few):
                specs.append({"text": T("cb_email"), "click": self.cmd_email, "tip": T("tip_email")})
            if ft == "music" and not items:
                specs.append({"text": T("cb_play_all"), "click": self.cmd_play_all, "tip": T("tip_play_all")})
            if (ft == "pictures" or any(i.mime.name().startswith("image/") for i in few if i.kind == "file")) and \
                    k in ("fs", "library", "search"):
                specs.append({"text": T("cb_slide_show"), "click": self.cmd_slideshow, "tip": T("tip_slide_show")})
            if self._burn_app():
                specs.append({"text": T("cb_burn"), "click": self.cmd_burn, "tip": T("tip_burn")})
            if fsdir and os.access(fsdir, os.W_OK):
                specs.append({"text": T("cb_new_folder"), "click": self.cmd_new_folder, "tip": T("tip_new_folder")})
        self.cmdbar.set_buttons(specs)

    def _default_icon(self, it):
        app = mime.default_app(it.mime)
        return app.qicon() if app else None

    def _organize_menu(self, m: QMenu):
        has = bool(self.selected_items())
        um = undo.manager()
        m.addAction(icons.action("edit-cut"), T("o_cut"), self.cmd_cut).setEnabled(has and self._can_delete())
        m.addAction(icons.action("edit-copy"), T("o_copy"), self.cmd_copy).setEnabled(has)
        m.addAction(icons.action("edit-paste"), T("o_paste"), self.cmd_paste).setEnabled(
            clipboard.has_files() and self._can_paste_here())
        m.addAction(icons.action("edit-undo"), T("o_undo"), self.cmd_undo).setEnabled(um.can_undo())
        m.addAction(icons.action("edit-redo"), T("o_redo"), self.cmd_redo).setEnabled(um.can_redo())
        m.addAction(icons.action("edit-select-all"), T("o_select_all"), self.cmd_select_all)
        m.addSeparator()
        lay = m.addMenu(T("o_layout"))
        for key, label in (("layout_menubar", "o_menu_bar"), ("layout_details", "o_details_pane"),
                           ("layout_preview", "o_preview_pane"), ("layout_nav", "o_nav_pane")):
            a = lay.addAction(T(label))
            a.setCheckable(True)
            a.setChecked(settings().get(key))
            a.triggered.connect(lambda c, k=key: settings().set(k, c))
        if locations.kind(self.loc) == "library":
            a = lay.addAction(T("o_library_pane"))
            a.setCheckable(True)
            a.setChecked(settings().get("layout_library"))
            a.triggered.connect(lambda c: settings().set("layout_library", c))
        m.addAction(T("o_folder_options"), self.cmd_folder_options)
        m.addSeparator()
        m.addAction(icons.action("edit-delete"), T("o_delete"), lambda: self.cmd_delete(False)).setEnabled(
            has and self._can_delete())
        m.addAction(T("o_rename"), self.cmd_rename).setEnabled(self._can_rename())
        one = self.selected_items()
        m.addAction(T("o_remove_properties"), self.cmd_remove_properties).setEnabled(
            len(one) == 1 and one[0].kind == "file" and (one[0].mime.name() in ("image/jpeg",) or metadata.can_edit_tags(one[0])))
        m.addAction(icons.action("document-properties"), T("o_properties"), self.cmd_properties)
        m.addSeparator()
        m.addAction(T("o_close"), self.close)

    def _include_menu(self, m: QMenu):
        L = places.libraries()
        target = self._fs_dir()
        items = self.selected_items()
        if len(items) == 1 and items[0].kind == "dir":
            target = items[0].path
        for lib in L.all():
            a = m.addAction(icons.library(lib.get("type", "")), L.title(lib))
            a.triggered.connect(lambda _=False, n=lib["name"], t=target: self.cmd_include(n, t))
        m.addSeparator()
        m.addAction(T("create_new_library"), lambda: self.cmd_include(None, target))

    def _share_menu(self, m: QMenu):
        items = self.selected_items()
        target = items[0].path if len(items) == 1 and items[0].kind == "dir" else (self._fs_dir() if not items else None)
        if not target:
            a = m.addAction(T("share_folders_only"))
            a.setEnabled(False)
            return
        info = sharing.info(target)
        a = m.addAction(T("share_nobody"), lambda: self.cmd_share(target, None))
        a.setCheckable(True)
        a.setChecked(info is None)
        a = m.addAction(T("share_read"), lambda: self.cmd_share(target, False))
        a.setCheckable(True)
        a.setChecked(bool(info and not info.get("writable")))
        a = m.addAction(T("share_readwrite"), lambda: self.cmd_share(target, True))
        a.setCheckable(True)
        a.setChecked(bool(info and info.get("writable")))
        m.addSeparator()
        m.addAction(T("share_advanced"), lambda: self.cmd_properties_of_path(target, "sharing"))

    def _open_with_menu(self, m: QMenu):
        items = [i for i in self.selected_items() if i.kind in ("file", "zipfile")]
        if not items:
            return
        it = items[0]
        apps = mime.apps_for(it.mime)
        default = mime.default_app(it.mime)
        seen = set()
        for app in ([default] if default else []) + apps:
            if app is None or app.id in seen:
                continue
            seen.add(app.id)
            a = m.addAction(app.qicon(), app.name)
            a.triggered.connect(lambda _=False, app=app: self.open_with(app, self.selected_items()))
            if len(seen) >= 12:
                break
        m.addSeparator()
        m.addAction(T("choose_default_program"), lambda: self.cmd_open_with_dialog(it))

    # ================================================================== context menus
    def item_context_menu(self, pos: QPoint, index: QModelIndex):
        m = QMenu(self)
        items = self.selected_items() if index.isValid() else []
        if items:
            self._fill_item_menu(m, items)
        else:
            self._fill_background_menu(m)
        if not m.isEmpty():
            m.exec(pos)

    def _shift(self) -> bool:
        return bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)

    def _fill_item_menu(self, m: QMenu, items: list, from_menubar: bool = False):
        k = locations.kind(self.loc)
        one = items[0] if len(items) == 1 else None
        kinds = {i.kind for i in items}
        if kinds == {"trash"}:
            a = m.addAction(T("c_restore"), self.cmd_restore)
            f = a.font()
            f.setBold(True)
            a.setFont(f)
            m.addAction(T("c_restore_to"), self.cmd_restore_to)
            if one:
                m.addAction(T("c_open_orig_location"), lambda: self.cmd_open_original_location(one))
            m.addSeparator()
            m.addAction(T("c_cut"), self.cmd_cut)
            m.addSeparator()
            m.addAction(T("c_delete"), lambda: self.cmd_delete(True))
            m.addSeparator()
            m.addAction(T("c_properties"), self.cmd_properties)
            return
        if kinds == {"drive"} and one:
            d = one.extra.get("drive") or {}
            a = m.addAction(T("c_open"), lambda: self.open_item(one))
            f = a.font()
            f.setBold(True)
            a.setFont(f)
            m.addAction(T("c_open_new_tab"), lambda: self.open_item(one, new_tab=True))
            m.addAction(T("c_open_new_window"), lambda: self.open_item(one, new_window=True))
            m.addSeparator()
            if d.get("mount") and d.get("kind") != "network":
                if d.get("removable") or d.get("kind") == "optical":
                    m.addAction(T("c_eject"), lambda: self.cmd_eject(one))
                elif d.get("mount") != "/":
                    m.addAction(T("c_unmount"), lambda: self.cmd_unmount(one))
            elif d.get("dev") and not d.get("mount"):
                m.addAction(T("c_mount"), lambda: self.mount_and_open(d["dev"], open_=False))
            if d.get("mount") and d.get("kind") == "network":
                m.addAction(T("cb_disconnect"), lambda: self.cmd_net_disconnect(one))
            if d.get("dev", "").startswith("/dev/") and util.which("gnome-disks"):
                m.addAction(T("c_format"), lambda: util.spawn(["gnome-disks", "--block-device", d["dev"],
                                                                "--format-device"]))
            m.addSeparator()
            if d.get("mount"):
                m.addAction(T("c_copy"), self.cmd_copy)
                m.addSeparator()
            m.addAction(T("c_properties"), self.cmd_properties)
            return
        if one and one.kind == "special":
            a = m.addAction(T("c_open"), lambda: self.open_item(one))
            f = a.font()
            f.setBold(True)
            a.setFont(f)
            m.addAction(T("c_open_new_tab"), lambda: self.open_item(one, new_tab=True))
            m.addAction(T("c_open_new_window"), lambda: self.open_item(one, new_window=True))
            if one.path.startswith("shell:network:"):
                m.addSeparator()
                m.addAction(T("cb_add_net_location"), lambda: self.cmd_add_net_location(one.path.rsplit(":", 1)[1]))
            return
        if "netfile" in kinds and kinds <= {"net", "netfile"}:
            if one:
                a = m.addAction(T("c_open"), lambda: self.open_item(one))
                f = a.font()
                f.setBold(True)
                a.setFont(f)
                m.addSeparator()
            m.addAction(T("c_download_to"), lambda: self.cmd_net_download(self.selected_items()))
            m.addSeparator()
            m.addAction(T("c_delete"), self.cmd_net_delete)
            if one:
                m.addAction(T("c_rename"), self.cmd_remote_rename)
                m.addSeparator()
                m.addAction(T("c_copy_address"), lambda: QApplication.clipboard().setText(one.extra.get("uri", "")))
            return
        if kinds == {"net"} and one and locations.kind(self.loc) == "net" and one.extra.get("type") == T("file_folder"):
            a = m.addAction(T("c_open"), lambda: self.open_item(one))
            f = a.font()
            f.setBold(True)
            a.setFont(f)
            m.addAction(T("c_open_new_tab"), lambda: self.open_item(one, new_tab=True))
            m.addAction(T("c_open_new_window"), lambda: self.open_item(one, new_window=True))
            m.addSeparator()
            m.addAction(T("c_download_to"), lambda: self.cmd_net_download(self.selected_items()))
            m.addSeparator()
            m.addAction(T("c_delete"), self.cmd_net_delete)
            m.addAction(T("c_rename"), self.cmd_remote_rename)
            m.addSeparator()
            m.addAction(T("c_copy_address"), lambda: QApplication.clipboard().setText(one.extra.get("uri", "")))
            return
        if kinds == {"net"} and one:
            self._net_location_menu(m, one.extra.get("uri") or one.path[4:], one.name, bool(one.extra.get("saved")),
                                    open_fn=lambda: self.open_item(one))
            return
        if kinds == {"net"} and len(items) > 1 and any(i.extra.get("saved") for i in items):
            m.addAction(T("c_remove_locations"), lambda: self.cmd_net_remove_many(self.selected_items()))
            m.addAction(T("c_force_disconnect"), lambda: [self.disconnect_location(i.extra.get("uri") or i.path[4:], force=True)
                                                           for i in self.selected_items()])
            return
        if kinds == {"library"} and one:
            a = m.addAction(T("c_open"), lambda: self.open_item(one))
            f = a.font()
            f.setBold(True)
            a.setFont(f)
            m.addAction(T("c_open_new_tab"), lambda: self.open_item(one, new_tab=True))
            m.addAction(T("c_open_new_window"), lambda: self.open_item(one, new_window=True))
            m.addSeparator()
            lib = one.extra.get("library") or {}
            a = m.addAction(T("c_dont_show_nav") if lib.get("nav", True) else T("c_show_nav"))
            a.triggered.connect(lambda: self._toggle_lib_nav(lib))
            m.addSeparator()
            m.addAction(T("c_delete"), lambda: self.cmd_delete_library(lib))
            m.addAction(T("c_rename"), self.cmd_rename)
            m.addSeparator()
            m.addAction(T("c_properties"), lambda: self.cmd_library_locations(lib["name"]))
            return
        # files / folders / zip members / network items
        a = m.addAction(T("c_open"), lambda: self.open_items(self.selected_items()))
        f = a.font()
        f.setBold(True)
        a.setFont(f)
        if one and (one.is_dir or one.kind in ("net",) or (one.kind == "file" and ziputil.is_archive_path(one.path))):
            if len(items) > 1 and all(i.is_dir for i in items):
                m.addAction(T("c_open_new_tabs"), lambda: [self.open_item(i, new_tab=True, activate=False)
                                                           for i in self.selected_items()])
            else:
                m.addAction(T("c_open_new_tab"), lambda: self.open_item(one, new_tab=True))
            m.addAction(T("c_open_new_window"), lambda: self.open_item(one, new_window=True))
        file_items = [i for i in items if i.kind in ("file", "zipfile") and not i.is_dir]
        if one and one.kind == "file" and not one.is_dir:
            n = one.mime.name()
            if n.startswith("text/") or one.mime.inherits("text/plain"):
                ed = self._text_editor()
                if ed:
                    m.addAction(T("c_edit"), lambda: ed.launch([one.path]))
            if n in PRINTABLE and util.which("lp"):
                m.addAction(T("c_print"), self.cmd_print)
            if ziputil.is_zip_path(one.path):
                m.addAction(T("c_extract_all"), lambda: self.cmd_extract_all(one.path))
                if os.access(os.path.dirname(one.path), os.W_OK):
                    m.addAction(T("c_extract_here"), lambda: self.cmd_extract_quick(one.path, False))
                    m.addAction(T("c_extract_to", ziputil.base_name(one.path)),
                                lambda: self.cmd_extract_quick(one.path, True))
            if n.startswith("image/"):
                m.addAction(T("c_preview"), lambda: self.open_item(one))
        if file_items:
            ow = m.addMenu(T("c_open_with"))
            ow.aboutToShow.connect(lambda ow=ow: (ow.clear(), self._open_with_menu(ow)))
        if self._shift() and one and one.kind in ("dir",):
            m.addAction(T("c_open_terminal_here"), lambda: mime.open_terminal(one.path))
        if one and one.extra.get("netcat") == "connected":
            m.addAction(T("cb_disconnect"), lambda: self.cmd_net_disconnect(one))
            m.addAction(T("c_force_disconnect"), lambda: self.cmd_net_disconnect(one, force=True))
        m.addSeparator()
        fs_items = [i for i in items if i.kind in ("file", "dir")]
        if fs_items:
            if one and one.kind == "dir" and sharing.available():
                sw = m.addMenu(T("c_share_with"))
                sw.aboutToShow.connect(lambda sw=sw: (sw.clear(), self._share_menu(sw)))
            if one and one.kind == "dir":
                il = m.addMenu(T("c_include_library"))
                il.aboutToShow.connect(lambda il=il: (il.clear(), self._include_menu(il)))
            if self._fs_dir() and os.access(self._fs_dir(), os.W_OK):
                m.addAction(T("c_add_to_archive"), lambda: self.cmd_add_to_archive([i.path for i in fs_items]))
            st = m.addMenu(T("c_send_to"))
            st.aboutToShow.connect(lambda st=st: (st.clear(), self._send_to_menu(st)))
            m.addSeparator()
        m.addAction(T("c_cut"), self.cmd_cut).setEnabled(self._can_delete())
        m.addAction(T("c_copy"), self.cmd_copy)
        if self._shift() and fs_items:
            m.addAction(T("c_copy_as_path"), self.cmd_copy_path)
        if one and one.kind == "dir" and clipboard.has_files():
            m.addAction(T("c_paste"), lambda: self.cmd_paste(target=one.path))
        m.addSeparator()
        if fs_items and self._fs_dir() and os.access(self._fs_dir(), os.W_OK):
            m.addAction(T("c_create_shortcut"), self.cmd_create_shortcut)
        m.addAction(T("c_delete"), lambda: self.cmd_delete(False)).setEnabled(self._can_delete())
        m.addAction(T("c_rename"), self.cmd_rename).setEnabled(self._can_rename())
        m.addSeparator()
        if k in ("search", "library", "recent") or (one and one.is_link) or k == "fs" and one and one.extra.get("in_folder"):
            if one and one.kind in ("file", "dir"):
                m.addAction(T("c_open_file_location"), lambda: self.cmd_open_location(one))
                m.addSeparator()
        m.addAction(T("c_properties"), self.cmd_properties)

    def _text_editor(self):
        mt = mime.db().mimeTypeForName("text/plain")
        return mime.default_app(mt)

    def _send_to_menu(self, m: QMenu):
        items = [i for i in self.selected_items() if i.kind in ("file", "dir")]
        paths = [i.path for i in items]
        m.addAction(icons.theme("package-x-generic", "application-zip", fallback="file"), T("st_zip"),
                    lambda: self.cmd_compress(paths))
        m.addAction(icons.folder("desktop"), T("st_desktop"), lambda: self.cmd_link_to(paths, places.xdg_dirs()["desktop"]))
        docs = places.xdg_dirs().get("documents")
        if docs and os.path.isdir(docs):
            m.addAction(icons.folder("documents"), T("st_documents"), lambda: self.run_job("copy", paths, docs))
        if util.which("xdg-email") and all(os.path.isfile(p) for p in paths):
            m.addAction(icons.theme("mail-send", "internet-mail", "mail-message-new", fallback="file"), T("st_mail"),
                        self.cmd_email)
        # SendTo folder (like Windows' shell:sendto)
        sd = os.path.join(DATA_DIR, "SendTo")
        if os.path.isdir(sd):
            for n in sorted(os.listdir(sd), key=str.casefold):
                p = os.path.join(sd, n)
                tgt = os.path.realpath(p)
                if n.endswith(".desktop"):
                    e = mime.DesktopEntry(p, n)
                    m.addAction(e.qicon(), e.name, lambda e=e: e.launch(paths))
                elif os.path.isdir(tgt):
                    m.addAction(icons.folder(), n, lambda t=tgt: self.run_job("copy", paths, t))
        rem = [d for d in places.drives().list() if d.get("removable") and d.get("mount")]
        if rem:
            m.addSeparator()
            for d in rem:
                m.addAction(icons.drive(d.get("kind")), places.drive_title(d),
                            lambda t=d["mount"]: self.run_job("copy", paths, t))

    def _fill_background_menu(self, m: QMenu, from_menubar: bool = False):
        k = locations.kind(self.loc)
        if not from_menubar:
            vm = m.addMenu(T("c_view"))
            self._add_view_modes(vm)
            if self.model.group_col:
                vm.addSeparator()
                vm.addAction(T("m_expand_groups"), self.view.expand_all)
                vm.addAction(T("m_collapse_groups"), self.view.collapse_all)
            m.addMenu(self._sort_menu(m))
            m.addMenu(self._group_menu(m))
            m.addAction(T("c_refresh"), self.refresh)
            if self._network_connection_here():
                m.addAction(T("cb_disconnect"), lambda: self.disconnect_location(self.loc))
                m.addAction(T("c_force_disconnect"), lambda: self.disconnect_location(self.loc, force=True))
            if k in ("network", "netproto"):
                proto = self.loc.rsplit(":", 1)[1] if k == "netproto" else None
                m.addSeparator()
                m.addAction(T("c_force_disconnect_all"), lambda: self.cmd_force_disconnect_all(proto))
                m.addAction(T("c_remove_all_locations"), lambda: self.cmd_remove_all_saved(proto)) \
                    .setEnabled(any(proto is None or locations._proto_bucket(netloc.proto_of_uri(l["uri"])) == proto
                                    for l in netloc.saved()))
            m.addSeparator()
            if k == "fs":
                m.addAction(T("c_customize"), self.cmd_customize)
                m.addSeparator()
        if k == "trash":
            m.addAction(T("cb_empty_bin"), self.cmd_empty_trash).setEnabled(self.model.rowCount() > 0)
            if not from_menubar:
                m.addSeparator()
                um = undo.manager()
                m.addAction(T("c_undo_x", um.undo_text()) if um.can_undo() else T("c_undo"), self.cmd_undo).setEnabled(um.can_undo())
                m.addSeparator()
            m.addAction(T("c_properties"), self.cmd_trash_properties)
            return
        if self._remote_folder_here():
            m.addAction(T("c_paste"), self.cmd_paste).setEnabled(clipboard.has_files())
            m.addSeparator()
            nm = m.addMenu(T("c_new"))
            nm.addAction(icons.folder(), strip_amp(T("new_folder_item")), self.cmd_net_new_folder)
            m.addSeparator()
        fsdir = self._fs_dir()
        if fsdir or k == "zip":
            m.addAction(T("c_paste"), self.cmd_paste).setEnabled(clipboard.has_files() and self._can_paste_here())
            if fsdir:
                m.addAction(T("c_paste_shortcut"), self.cmd_paste_shortcut).setEnabled(clipboard.has_files())
        um = undo.manager()
        m.addAction(T("c_undo_x", um.undo_text()) if um.can_undo() else T("c_undo"), self.cmd_undo).setEnabled(um.can_undo())
        if um.can_redo():
            m.addAction(T("c_redo_x", um.redo_text()), self.cmd_redo)
        if self._shift() and fsdir:
            m.addAction(T("c_open_terminal_here"), lambda: mime.open_terminal(fsdir))
        m.addSeparator()
        if k == "fs" and sharing.available():
            sw = m.addMenu(T("c_share_with"))
            sw.aboutToShow.connect(lambda sw=sw: (sw.clear(), self._share_menu(sw)))
        if fsdir and os.access(fsdir, os.W_OK):
            nm = m.addMenu(T("c_new"))
            self._new_menu(nm)
            m.addSeparator()
        if k == "libraries":
            m.addAction(T("cb_new_library"), self.cmd_new_library)
            m.addSeparator()
        if k in ("network", "netproto"):
            m.addAction(T("cb_add_net_location"),
                        lambda: self.cmd_add_net_location(self.loc.rsplit(":", 1)[1] if k == "netproto" else None))
            m.addAction(T("m_map_drive"), self.cmd_map_drive)
        if k in ("fs", "library", "computer", "zip"):
            m.addAction(T("c_properties"), self.cmd_folder_properties)

    def _new_menu(self, nm: QMenu):
        nm.addAction(icons.folder(), T("new_folder_item"), self.cmd_new_folder)
        nm.addAction(icons.action("emblem-symbolic-link"), T("new_shortcut_item"), self.cmd_new_shortcut)
        nm.addSeparator()
        tmpl = places.xdg_dirs().get("templates")
        entries = []
        if tmpl and os.path.isdir(tmpl) and os.path.abspath(tmpl) != places.HOME:
            for n in sorted(os.listdir(tmpl), key=str.casefold):
                p = os.path.join(tmpl, n)
                if os.path.isfile(p) and not n.startswith("."):
                    entries.append((n, p))
        nm.addAction(icons.for_mime(mime.db().mimeTypeForName("text/plain")), T("new_text_document"),
                     lambda: self.cmd_new_file(T("new_text_document_name") + ".txt", None))
        nm.addAction(icons.theme("package-x-generic", "application-zip", fallback="file"), T("new_zip"),
                     lambda: self.cmd_new_file(T("new_zip_name") + ".zip", "zip"))
        for n, p in entries:
            base, ext = util.split_ext(n)
            nm.addAction(icons.for_mime(mime.mime_for(p)), base, lambda p=p, n=n: self.cmd_new_file(n, p))

    def _network_connection_here(self, loc: str | None = None) -> bool:
        loc = self.loc if loc is None else loc
        k = locations.kind(loc)
        if k == "fs":
            d = places.drives().for_path(loc)
            return bool(d and d.get("kind") == "network")
        if k == "net":
            return bool(netloc.mount_root_uri(loc)) or netloc.builtin(loc[4:])
        return False

    def nav_context_menu(self, loc: str, kind: str, pos: QPoint):
        m = QMenu(self)
        if loc and self._network_connection_here(loc):
            m.addAction(T("c_open"), lambda: self.navigate(loc))
            m.addAction(T("c_open_new_tab"), lambda: self.new_tab(loc))
            m.addAction(T("c_open_new_window"), lambda: self.new_window(loc))
            m.addSeparator()
            m.addAction(T("cb_disconnect"), lambda: self.disconnect_location(loc))
            m.addAction(T("c_force_disconnect"), lambda: self.disconnect_location(loc, force=True))
            m.exec(pos)
            return
        if loc and loc.startswith("net:"):
            uri = loc[4:]
            sv = [l for l in netloc.saved() if netloc.normalize_uri(l["uri"]) == netloc.normalize_uri(uri)]
            self._net_location_menu(m, uri, sv[0]["name"] if sv else uri, bool(sv), open_fn=lambda: self.navigate(loc))
            m.exec(pos)
            return
        if loc and loc.startswith("shell:network"):
            proto = loc.rsplit(":", 1)[1] if loc.startswith("shell:network:") else None
            m.addAction(T("c_open"), lambda: self.navigate(loc))
            m.addAction(T("c_open_new_tab"), lambda: self.new_tab(loc))
            m.addSeparator()
            m.addAction(T("cb_add_net_location"), lambda: self.cmd_add_net_location(proto))
            m.addSeparator()
            m.addAction(T("c_force_disconnect_all"), lambda: self.cmd_force_disconnect_all(proto))
            m.addAction(T("c_remove_all_locations"), lambda: self.cmd_remove_all_saved(proto))
            m.exec(pos)
            return
        if loc == "shell:favorites":
            m.addAction(T("fav_restore"), places.favorites().restore)
            m.addAction(T("fav_add_current"), lambda: places.favorites().add(self.loc))
            m.exec(pos)
            return
        idx = self.nav.currentIndex()
        if loc.startswith("dev:"):
            m.addAction(T("c_mount"), lambda: self.mount_and_open(loc[4:], open_=False))
            m.addAction(T("c_open"), lambda: self.navigate(loc))
            m.exec(pos)
            return
        a = m.addAction(T("c_expand") if not self.nav.isExpanded(self.nav.indexAt(self.nav.viewport().mapFromGlobal(pos)))
                        else T("c_collapse"))
        nidx = self.nav.indexAt(self.nav.viewport().mapFromGlobal(pos))
        a.triggered.connect(lambda: self.nav.setExpanded(nidx, not self.nav.isExpanded(nidx)))
        m.addSeparator()
        m.addAction(T("c_open"), lambda: self.navigate(loc))
        m.addAction(T("c_open_new_tab"), lambda: self.new_tab(loc))
        m.addAction(T("c_open_new_window"), lambda: self.new_window(loc))
        if loc.startswith("/"):
            if sharing.available():
                sw = m.addMenu(T("c_share_with"))
                sw.aboutToShow.connect(lambda sw=sw: (sw.clear(), self._share_menu_for(sw, loc)))
            il = m.addMenu(T("c_include_library"))
            L = places.libraries()
            for lib in L.all():
                il.addAction(icons.library(lib.get("type", "")), L.title(lib),
                             lambda n=lib["name"]: self.cmd_include(n, loc))
            il.addSeparator()
            il.addAction(T("create_new_library"), lambda: self.cmd_include(None, loc))
            m.addSeparator()
            m.addAction(T("c_copy"), lambda: clipboard.set_files([loc]))
            if clipboard.has_files() and os.access(loc, os.W_OK):
                m.addAction(T("c_paste"), lambda: self.cmd_paste(target=loc))
            m.addSeparator()
        if kind == "favorite":
            m.addAction(T("fav_remove"), lambda: places.favorites().remove(loc))
            m.addAction(T("c_rename"), lambda: self._rename_favorite(loc))
            m.addSeparator()
        if kind == "library":
            lib = places.libraries().get(locations.library_name(loc))
            if lib:
                m.addAction(T("c_dont_show_nav"), lambda: self._toggle_lib_nav(lib))
                m.addSeparator()
                m.addAction(T("c_properties"), lambda: self.cmd_library_locations(lib["name"]))
                m.exec(pos)
                return
        if loc == "shell:libraries":
            m.addAction(T("cb_new_library"), self.cmd_new_library)
            m.addAction(T("lib_restore_defaults"), places.libraries().restore_defaults)
            m.exec(pos)
            return
        if loc == "shell:trash":
            n = len(trash.list_entries())
            a = m.addAction(T("c_open"), lambda: self.navigate(loc))
            f = a.font()
            f.setBold(True)
            a.setFont(f)
            m.addAction(T("c_open_new_tab"), lambda: self.new_tab(loc))
            m.addAction(T("c_open_new_window"), lambda: self.new_window(loc))
            m.addSeparator()
            m.addAction(T("cb_restore_all"), self._restore_all_from_anywhere).setEnabled(n > 0)
            m.addAction(T("cb_empty_bin"), self.cmd_empty_trash).setEnabled(n > 0)
            m.addSeparator()
            m.addAction(T("c_properties"), self.cmd_trash_properties)
            m.exec(pos)
            return
        if loc in ("shell:computer", "shell:network"):
            m.addAction(T("m_map_drive"), self.cmd_map_drive)
            m.addAction(T("m_disconnect_drive"), self.cmd_disconnect_drive)
            m.exec(pos)
            return
        d = places.drives().by_mount(loc)
        if d and (d.get("removable") or d.get("kind") == "optical"):
            from .items import FileItem
            fi = FileItem(places.drive_title(d), loc, "drive", True)
            fi.extra["drive"] = d
            m.addAction(T("c_eject"), lambda: self.cmd_eject(fi))
            m.addSeparator()
        if loc.startswith("/"):
            m.addAction(T("c_properties"), lambda: self.cmd_properties_of_path(loc))
        m.exec(pos)

    def _share_menu_for(self, m, path):
        info = sharing.info(path)
        for label, val, checked in ((T("share_nobody"), None, info is None),
                                    (T("share_read"), False, bool(info and not info.get("writable"))),
                                    (T("share_readwrite"), True, bool(info and info.get("writable")))):
            a = m.addAction(label, lambda v=val: self.cmd_share(path, v))
            a.setCheckable(True)
            a.setChecked(checked)
        m.addSeparator()
        m.addAction(T("share_advanced"), lambda: self.cmd_properties_of_path(path, "sharing"))

    def _rename_favorite(self, loc):
        cur = next((f.get("name") for f in places.favorites().items() if f["path"] == loc), "") or locations.display_name(loc)
        name, ok = QInputDialog.getText(self, T("c_rename").replace("&", ""), T("new_name"), text=cur)
        if ok:
            places.favorites().rename(loc, name.strip())

    def _toggle_lib_nav(self, lib):
        lib["nav"] = not lib.get("nav", True)
        places.libraries().save(lib)

    def header_menu(self, pos: QPoint):
        m = QMenu(self)
        m.addAction(T("size_column_to_fit"), self._fit_column)
        m.addAction(T("size_all_columns_to_fit"), self._fit_all_columns)
        m.addSeparator()
        common = ["name", "date_modified", "type", "size", "date_created", "date_accessed", "attributes",
                  "owner", "dimensions", "date_taken", "rating", "length", "artists", "album", "title"]
        ft = self.model.provider.folder_type if self.model.provider else "general"
        if ft == "trash":
            common = ["name", "orig_location", "date_deleted", "size", "type", "date_modified"] + common[4:]
        elif ft == "computer":
            common = ["name", "type", "total_size", "free_space", "filesystem", "percent_full"]
        elif ft == "search":
            common = ["name", "in_folder"] + common[1:]
        elif ft == "zip":
            common = ["name", "type", "compressed_size", "size", "ratio", "date_modified"]
        for col in common:
            a = m.addAction(T(COLUMNS[col][0]))
            a.setCheckable(True)
            a.setChecked(col in self.model.columns)
            a.setEnabled(col != "name")
            a.triggered.connect(lambda c, col=col: self._toggle_column(col, c))
        m.addSeparator()
        m.addAction(T("m_more"), self.cmd_choose_details)
        m.exec(pos)

    def _toggle_column(self, col: str, on: bool):
        cols = [self.model.columns[i] for i in self.view.column_order()]
        if on and col not in cols:
            cols.append(col)
        elif not on and col in cols:
            cols.remove(col)
        self.model.set_columns(cols)
        self.view._sync_header()
        self.view.schedule_layout()
        self._save_bag_soon()

    def _fit_column(self):
        pos = self.view.header.mapFromGlobal(QCursor.pos())
        li = self.view.header.logicalIndexAt(pos)
        if li >= 0:
            self._fit(li)

    def _fit_all_columns(self):
        for i in range(len(self.model.columns)):
            self._fit(i)

    def _fit(self, li: int):
        col = self.model.columns[li]
        fm = self.view.fontMetrics()
        w = fm.horizontalAdvance(T(COLUMNS[col][0])) + 30
        for it in self.model.rows[:3000]:
            w = max(w, fm.horizontalAdvance(self.model.text(it, col)) + (40 if col == "name" else 18))
        self.view.header.resizeSection(li, min(w, 600))

    # ================================================================== helpers
    def _fs_dir(self) -> str | None:
        """Real directory where new things go (current folder or library save location)."""
        t = self.model.target_dir()
        return t if t and os.path.isdir(t) else None

    def _can_delete(self) -> bool:
        items = self.selected_items()
        if not items:
            return False
        for i in items:
            if i.kind in ("file", "dir", "trash"):
                if not os.access(os.path.dirname(i.path) or "/", os.W_OK):
                    return False
            elif i.kind in ("zipfile", "zipdir"):
                if not ziputil.is_writable(i.extra.get("zip", "")):
                    return False
            elif i.kind == "library":
                continue
            else:
                return False
        return True

    def _can_rename(self) -> bool:
        items = self.selected_items()
        return len(items) == 1 and self.model.can_rename(items[0])

    def _can_paste_here(self) -> bool:
        k = locations.kind(self.loc)
        if k == "zip":
            zp, _ = ziputil.split(self.loc)
            return ziputil.is_writable(zp)
        d = self._fs_dir()
        return bool(d and os.access(d, os.W_OK))

    def _burn_app(self) -> list[str] | None:
        for cmd in (["brasero"], ["xfburn"], ["k3b"]):
            if util.which(cmd[0]):
                return cmd
        return None

    def mime_for_rows(self, rows: list[int]):
        items = [self.model.rows[r] for r in rows if r < len(self.model.rows)]
        paths = [i.path for i in items if i.kind in ("file", "dir", "trash")]
        paths += [i.extra["drive"]["mount"] for i in items if i.kind == "drive" and (i.extra.get("drive") or {}).get("mount")]
        zipm = [(i.extra["zip"], i.extra["member"], i.is_dir) for i in items if i.kind in ("zipfile", "zipdir")]
        if not paths and not zipm:
            return None
        md = clipboard.make_mime(paths, False, zipm or None)
        if zipm and not paths:
            # let other applications receive extracted copies
            import tempfile
            tmp = tempfile.mkdtemp(prefix="linexplorer-zip-")
            urls = []
            for zp, member, is_dir in zipm[:200]:
                out = os.path.join(tmp, member.rstrip("/").split("/")[-1])
                try:
                    ziputil.copy_out(zp, member, is_dir, out)
                    urls.append(QUrl.fromLocalFile(out))
                except Exception:
                    pass
            md.setUrls(urls)
        return md

    def _drop_target_loc(self, item) -> str | None:
        if item is None:
            return self.loc if locations.kind(self.loc) in ("fs", "library", "zip", "trash", "net") else None
        if item.kind == "dir":
            return item.path
        if item.kind == "drive":
            return (item.extra.get("drive") or {}).get("mount")
        if item.kind == "library":
            return item.path
        if item.kind == "net" and self._remote_folder_here():
            return item.path
        if item.kind == "zipdir":
            return item.path
        if item.kind == "file" and ziputil.is_zip_path(item.path):
            return ziputil.make(item.path, "")
        return None

    def _drop_action_item(self, mime_, item, mods):
        from .window import drop_action_for_target
        return drop_action_for_target(mime_, self._drop_target_loc(item), mods)

    def handle_drop_item(self, md, item, action, right):
        self.handle_drop_loc(md, self._drop_target_loc(item), action, right)

    def handle_drop_loc(self, md, target: str | None, action, right: bool):
        if not target:
            return
        paths, _cut, zipm = clipboard.parse(md)
        if md.hasFormat(clipboard.OURS):
            paths = [p for p in paths if "linexplorer-zip-" not in p]
        if not paths and not zipm:
            return
        k = locations.kind(target)
        if k == "net":
            if paths:
                self.cmd_net_upload(paths, target[4:])
            return
        if right:
            m = QMenu(self)
            acts = {}
            acts[m.addAction(T("dd_copy_here"))] = Qt.DropAction.CopyAction
            if paths:
                acts[m.addAction(T("dd_move_here"))] = Qt.DropAction.MoveAction
                if k == "fs":
                    acts[m.addAction(T("dd_link_here"))] = Qt.DropAction.LinkAction
            m.addSeparator()
            m.addAction(T("dd_cancel"))
            chosen = m.exec(QCursor.pos())
            if chosen not in acts:
                return
            action = acts[chosen]
        if k == "trash":
            self.run_job("trash", paths, "")
            return
        dest = target
        zip_loc = None
        if k == "library":
            lib = places.libraries().get(locations.library_name(target))
            dest = (lib or {}).get("save") or ""
            if not dest:
                return
        elif k == "zip":
            zip_loc = target
            dest = ziputil.split(target)[0]
        srcs = [Src(p) for p in paths] + [Src(zip_=z, member=mb, is_dir=d) for z, mb, d in zipm]
        if action == Qt.DropAction.LinkAction:
            self.run_job("link", srcs, dest)
        elif action == Qt.DropAction.MoveAction:
            self.run_job("move", srcs, dest, zip_loc=zip_loc, zip_dest=bool(zip_loc))
        else:
            self.run_job("copy", srcs, dest, zip_loc=zip_loc, zip_dest=bool(zip_loc))

    def run_job(self, op: str, sources, dest: str, select_result: bool = True, **opts):
        srcs = [s if isinstance(s, Src) else Src(s) for s in sources]
        job = Job(op, srcs, dest, **opts)
        tab = self.tab

        def done(res):
            undo.manager().record_result(res)
            if op == "move":
                clipboard.clear_if_cut([s.path for s in srcs])
            if tab not in self.tabs:
                return
            fs_dir = tab.loc if locations.kind(tab.loc) == "fs" else None
            if select_result and res.get("created") and dest and fs_dir and \
                    os.path.abspath(dest) == os.path.abspath(fs_dir):
                tab.select_after = list(res["created"])
            places.drives().refresh()
            tab.model.reload()
            for w in type(self).instances:
                for t in w.tabs:
                    if t is not tab and t.loc and locations.kind(t.loc) != "search":
                        t.model.reload()
            if op in ("trash", "empty", "restore", "delete"):
                self.nav.rebuild() if op in ("empty",) else None
        fileops.ops().run(job, self, on_done=done)
        return job

    # ================================================================== opening
    def open_index(self, idx: QModelIndex):
        it = self.model.item_at(idx)
        if it is None:
            return
        items = self.selected_items()
        if it in items and len(items) > 1:
            self.open_items(items)
        else:
            self.open_item(it)

    def open_items(self, items: list):
        dirs = [i for i in items if i.is_dir or i.kind in ("drive", "library", "net")]
        files = [i for i in items if i not in dirs]
        if len(dirs) == 1 and not files:
            self.open_item(dirs[0])
            return
        for d in dirs:
            self.open_item(d, new_tab=True, activate=False)
        # group files by default application, like Explorer's multi-open
        by_app: dict = {}
        for f in files:
            if f.kind != "file":
                self.open_item(f)
                continue
            app = mime.default_app(f.mime)
            by_app.setdefault(app.id if app else None, (app, []))[1].append(f.path)
        for key, (app, paths) in by_app.items():
            if app is not None:
                app.launch(paths)
            else:
                for p in paths:
                    mime.open_with_default(p)

    def open_item(self, it, new_window: bool = False, new_tab: bool = False, activate: bool = True):
        k = it.kind
        target = None
        if k in ("dir", "zipdir", "library", "net", "special"):
            target = it.path
        elif k == "drive":
            d = it.extra.get("drive") or {}
            if not d.get("mount"):
                self.mount_and_open(d.get("dev", ""), new_window=new_window, new_tab=new_tab)
                return
            target = d["mount"]
        elif k == "trash":
            self.cmd_properties()
            return
        elif k == "netfile":
            self._open_netfile(it)
            return
        elif k == "zipfile":
            try:
                p = ziputil.extract_to_temp(it.extra["zip"], it.extra["member"])
                mime.open_with_default(p)
            except Exception as e:
                QMessageBox.warning(self, T("app_name"), str(e))
            return
        elif k == "file":
            if it.is_dir:
                target = it.path
            elif ziputil.is_zip_path(it.path):
                target = ziputil.make(it.path, "")
            elif it.path.endswith(".lxsearch"):
                self._open_saved_search(it.path)
                return
            else:
                self.launch_file(it.path)
                return
        if target:
            if new_tab:
                self.new_tab(target, activate=activate)
            elif new_window or not settings().get("browse_same_window"):
                self.new_window(target)
            else:
                self.navigate(target)

    def launch_file(self, path: str):
        mt = mime.mime_for(path)
        n = mt.name()
        if mime.is_executable_file(path) and n not in ("application/x-desktop",):
            if mime.is_elf_or_appimage(path):
                util.spawn([path], cwd=os.path.dirname(path))
                return
            if n.startswith("text/") or mt.inherits("text/plain") or n == "application/x-shellscript":
                box = QMessageBox(self)
                box.setWindowTitle(T("app_name"))
                box.setText(T("run_or_display", os.path.basename(path)))
                run = box.addButton(T("run"), QMessageBox.ButtonRole.AcceptRole)
                term = box.addButton(T("run_in_terminal"), QMessageBox.ButtonRole.ActionRole)
                disp = box.addButton(T("display"), QMessageBox.ButtonRole.ActionRole)
                box.addButton(QMessageBox.StandardButton.Cancel)
                box.exec()
                c = box.clickedButton()
                if c is run:
                    util.spawn([path], cwd=os.path.dirname(path))
                elif c is term:
                    t = mime.terminal_command()
                    if t:
                        util.spawn(t + [path], cwd=os.path.dirname(path))
                elif c is disp:
                    mime.open_with_default(path)
                return
        if not mime.open_with_default(path):
            self.cmd_open_with_dialog_path(path)

    def open_with(self, app, items):
        paths = [i.path if i.kind == "file" else ziputil.extract_to_temp(i.extra["zip"], i.extra["member"])
                 for i in items if i.kind in ("file", "zipfile")]
        if paths:
            app.launch(paths)

    def _open_saved_search(self, path: str):
        import json
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            self.navigate(locations.search_location(d.get("scope", places.HOME), d.get("query", ""),
                                                    d.get("content", False)))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, T("app_name"), str(e))

    def mount_and_open(self, dev: str, new_window: bool = False, open_: bool = True, new_tab: bool = False):
        if not dev:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        rc, out, err = util.run(["udisksctl", "mount", "-b", dev], timeout=60)
        QApplication.restoreOverrideCursor()
        if rc != 0:
            rc2, out2, err2 = util.run(["gio", "mount", "-d", dev], timeout=60)
            if rc2 != 0:
                QMessageBox.warning(self, T("app_name"), T("mount_failed", dev) + "\n\n" + (err or err2).strip())
                return
        places.drives().refresh(sync=True)
        mount = None
        for d in places.drives().list():
            if d.get("dev") == dev and d.get("mount"):
                mount = d["mount"]
        if mount is None:
            import re
            m = re.search(r" at (/\S+)", out)
            mount = m.group(1).rstrip(".") if m else None
        self.model.reload() if locations.kind(self.loc) == "computer" else None
        if mount and open_:
            if new_tab:
                self.new_tab(mount)
            elif new_window:
                self.new_window(mount)
            else:
                self.navigate(mount)

    def mount_network(self, uri: str, anonymous: bool = False):
        from .dialogs.mapdrive import gio_mount
        eff = gio_mount(self, uri, anonymous=anonymous)
        if not eff:
            if self.hist_pos > 0 and self.history[self.hist_pos] == "net:" + uri:
                self.go_back()
            return
        if eff != uri:             # e.g. ftp:// that the server only accepts over TLS -> ftps://
            netloc.replace_saved_uri(uri, eff)
            if self.history and self.history[self.hist_pos] == "net:" + uri:
                self.history[self.hist_pos] = "net:" + eff
            uri = eff
        places.drives().refresh(sync=True)
        local = netloc.mount_for(uri) or locations.gio_local_path(uri)
        if local and os.path.isdir(local):
            if self.history and self.history[self.hist_pos] == "net:" + uri:
                self.history[self.hist_pos] = local
                self.navigate(local, add_history=False)
            else:
                self.navigate(local)
        else:
            self.model.reload()

    # ================================================================== network
    def _busy(self, title: str, fn):
        """Run fn() in a worker thread behind an indeterminate progress dialog; returns its result."""
        from PySide6.QtWidgets import QProgressDialog
        import threading
        box = QProgressDialog(title, T("cancel"), 0, 0, self)
        box.setWindowTitle(T("app_name"))
        box.setMinimumDuration(400)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        res = {}
        cancel = threading.Event()

        def work():
            try:
                res["v"] = fn(cancel.is_set)
            except Exception as e:  # noqa: BLE001
                res["e"] = e
        t = util.in_thread(work)
        while t.is_alive():
            QApplication.processEvents()
            if box.wasCanceled():
                cancel.set()
            t.join(0.03)
        box.close()
        if "e" in res:
            raise res["e"]
        return res.get("v")

    def ftp_login(self, exc) -> bool:
        """Log in with the built-in FTP client: ask for the password and confirm unknown certificates."""
        from . import ftpclient
        from .dialogs.mapdrive import CredentialsDialog, _ask_question
        uri = exc.uri
        for _round in range(6):
            if isinstance(exc, ftpclient.NeedsLogin):
                msg = T("ftp_builtin_text", str(exc)) if str(exc) and str(exc) != uri else ""
                d = CredentialsDialog(self, uri, msg)
                if not d.exec():
                    return False
                ftpclient.set_credentials(uri, d.user.text().strip(), d.pw.text(), d.anon.isChecked())
            elif isinstance(exc, ftpclient.CertQuestion):
                q = T("ftp_cert_question", f"{exc.host}:{exc.port}", exc.reason, exc.subject or "-", exc.issuer or "-",
                      exc.fingerprint or "-")
                if _ask_question(self, q, [T("ftp_cert_trust"), T("cancel")]) != 0:
                    return False
                ftpclient.trust_cert(exc.host, exc.port, exc.fingerprint)
            try:
                self._busy(T("ftp_connecting", uri), lambda c: ftpclient.check(uri))
                return True
            except (ftpclient.NeedsLogin, ftpclient.CertQuestion) as e:
                exc = e
                if isinstance(e, ftpclient.NeedsLogin):
                    exc = ftpclient.NeedsLogin(uri, T("wrong_password"))
            except OSError as e:
                ftpclient.forget(uri)
                QMessageBox.warning(self, T("location_unavailable"), T("net_error_text", uri, str(e)))
                return False
        return False

    def _open_netfile(self, it):
        import tempfile
        d = tempfile.mkdtemp(prefix="linexplorer-net-")
        dest = os.path.join(d, it.name.replace("/", "_"))
        errs = self._busy(T("net_downloading", it.name),
                          lambda c: netloc.remote_download(it.extra.get("uri", ""), dest, False, c))
        if errs:
            QMessageBox.warning(self, T("app_name"), "\n".join(errs[:10]))
            return
        if not mime.open_with_default(dest):
            self.cmd_open_with_dialog_path(dest)

    def cmd_net_download(self, items, dest: str | None = None):
        items = [i for i in items if i.kind in ("net", "netfile") and i.extra.get("uri")]
        if not items:
            return
        if dest is None:
            dest = QFileDialog.getExistingDirectory(self, strip_amp(T("c_download_to")).rstrip("…. "),
                                                    places.xdg_dirs().get("download") or places.HOME)
        if not dest:
            return

        def work(cancelled):
            errs = []
            for i in items:
                if cancelled():
                    break
                errs += netloc.remote_download(i.extra["uri"], os.path.join(dest, util.unique_name(dest, i.name, i.is_dir)),
                                            i.is_dir, cancelled)
            return errs
        errs = self._busy(T("net_downloading", items[0].name if len(items) == 1 else T("n_items", len(items))), work)
        if errs:
            QMessageBox.warning(self, T("app_name"), "\n".join(errs[:10]))
        return dest

    def cmd_net_upload(self, paths: list[str], dest_uri: str):
        paths = [p for p in paths if p and os.path.lexists(p)]
        if not paths:
            return

        def work(cancelled):
            errs = []
            for p in paths:
                if cancelled():
                    break
                errs += netloc.remote_upload(p, dest_uri, cancelled)
            return errs
        errs = self._busy(T("net_uploading", os.path.basename(paths[0]) if len(paths) == 1 else
                            T("n_items", len(paths))), work)
        if errs:
            QMessageBox.warning(self, T("app_name"), "\n".join(errs[:10]))
        self.model.reload()

    def cmd_add_net_location(self, proto: str | None = None):
        from .dialogs.mapdrive import AddNetLocationDialog
        dlg = AddNetLocationDialog(self, proto or "smb")
        if dlg.exec():
            if dlg.connect_now:
                # saved only when the connection works, so failed attempts do not pile up in the list
                from .dialogs.mapdrive import gio_mount
                eff = gio_mount(self, dlg.uri_value, anonymous=dlg.anonymous)
                if eff:
                    netloc.save_location(dlg.name_value, eff)
                    self.mount_network(eff)
            else:
                netloc.save_location(dlg.name_value, dlg.uri_value)
            self._net_changed()

    def cmd_net_connect(self, it):
        uri = it.extra.get("uri") or it.path[4:]
        self.mount_network(uri)

    def _net_location_menu(self, m, uri: str, name: str, is_saved: bool, open_fn):
        """Menu for a saved or discovered network location (view and navigation pane)."""
        a = m.addAction(T("c_open"), open_fn)
        f = a.font()
        f.setBold(True)
        a.setFont(f)
        m.addAction(T("c_open_new_tab"), lambda: self.new_tab("net:" + uri))
        m.addAction(T("c_open_new_window"), lambda: self.new_window("net:" + uri))
        m.addSeparator()
        mounted = netloc.is_mounted(uri)
        if mounted:
            m.addAction(T("cb_disconnect"), lambda: self.disconnect_location(uri))
            m.addAction(T("c_force_disconnect"), lambda: self.disconnect_location(uri, force=True))
        else:
            m.addAction(T("cb_connect"), lambda: self.mount_network(uri))
        m.addSeparator()
        if is_saved:
            m.addAction(T("c_rename"), lambda: self._net_rename_uri(uri, name))
            m.addAction(T("c_remove_location"), lambda: self._net_remove_uri(uri))
        else:
            m.addAction(T("c_save_location"), lambda: (netloc.save_location(name, uri), self.model.reload(),
                                                       self.nav.rebuild()))
        m.addSeparator()
        m.addAction(T("c_copy_address"), lambda: QApplication.clipboard().setText(uri))

    def _net_remove_uri(self, uri: str):
        if netloc.is_mounted(uri):
            self.disconnect_location(uri, force=True)
        netloc.remove_location(uri)
        self._net_changed()

    def _net_rename_uri(self, uri: str, name: str):
        new, ok = QInputDialog.getText(self, T("rename_title"), T("new_name"), text=name)
        if ok and new.strip():
            netloc.rename_location(uri, new.strip())
            self._net_changed()

    def _net_changed(self):
        for w in type(self).instances:
            if locations.kind(w.loc) in ("network", "netproto"):
                w.model.reload()
            w.nav.rebuild()

    def cmd_net_remove_many(self, items):
        for i in items:
            if i.extra.get("saved"):
                netloc.remove_location(i.extra.get("uri") or i.path[4:])
        self._net_changed()

    def cmd_force_disconnect_all(self, proto: str | None = None):
        mounts = [x for x in netloc.gio_mount_list()
                  if proto is None or locations._proto_bucket(x["proto"]) == proto]
        if not mounts:
            QMessageBox.information(self, T("c_force_disconnect_all").replace("&", ""), T("net_nothing_connected"))
            return
        if QMessageBox.question(self, T("c_force_disconnect_all").replace("&", ""),
                                T("net_force_all_confirm", "\n".join(x["uri"] for x in mounts[:20]))) \
                != QMessageBox.StandardButton.Yes:
            return
        for x in mounts:
            self.disconnect_location(x["uri"], force=True, quiet=True)
        left = [x["uri"] for x in netloc.gio_mount_list() if any(netloc.normalize_uri(x["uri"]) ==
                                                                 netloc.normalize_uri(y["uri"]) for y in mounts)]
        if left:
            QMessageBox.warning(self, T("cb_disconnect"), T("net_disconnect_failed", "\n".join(left)))
        self._net_changed()

    def cmd_remove_all_saved(self, proto: str | None = None):
        lst = [l for l in netloc.saved() if proto is None or locations._proto_bucket(netloc.proto_of_uri(l["uri"])) == proto]
        if not lst:
            return
        if QMessageBox.question(self, T("c_remove_all_locations").replace("&", ""),
                                T("net_remove_all_confirm", "\n".join(f"{l['name']}  ({l['uri']})" for l in lst[:20]))) \
                != QMessageBox.StandardButton.Yes:
            return
        netloc.remove_all_saved(proto)
        self._net_changed()

    def cmd_net_disconnect(self, it, force: bool = False):
        if it.kind == "drive":
            target = (it.extra.get("drive") or {}).get("mount") or ""
        elif it.kind == "dir":
            target = it.path
        else:
            target = it.extra.get("uri") or it.path
        self.disconnect_location(target, force=force)

    def disconnect_location(self, target: str, force: bool = False, quiet: bool = False) -> bool:
        from . import ftpclient
        if ftpclient.is_ftp_uri(target) and ftpclient.has_credentials(target):
            server = ftpclient.parse(target)[0][:3]
            ftpclient.forget(target[4:] if target.startswith("net:") else target)
            for w in type(self).instances:
                for t in list(w.tabs):
                    if t.loc.startswith("net:") and ftpclient.is_ftp_uri(t.loc) and ftpclient.parse(t.loc)[0][:3] == server:
                        if t is w.tab:
                            w.navigate("shell:network")
                        else:
                            w._in_background(t, lambda w=w: w.navigate("shell:network"))
            if not netloc.is_mounted(target[4:] if target.startswith("net:") else target):
                return True
        """Disconnect the network connection that contains target (URI, net:, gvfs path or mount folder).
        Tabs and windows showing a folder of it are moved to Network first, so nothing keeps it busy."""
        root = netloc.mount_root_uri(target)
        mount = netloc.mount_path_of(root) if root else (target if target.startswith("/") else None)

        def inside(loc: str) -> bool:
            if not loc:
                return False
            if mount and loc.startswith("/") and util.is_subpath(loc, mount):
                return True
            if root and loc.startswith("net:"):
                n, r = netloc.normalize_uri(loc[4:]), netloc.normalize_uri(root)
                return n == r or n.startswith(r + "/")
            return False
        for w in type(self).instances:
            for t in list(w.tabs):
                if inside(t.loc):
                    t.model._cancel.set()
                    if t is w.tab:
                        w.navigate("shell:network")
                    else:
                        w._in_background(t, lambda w=w: w.navigate("shell:network"))
        QApplication.processEvents()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, msg = netloc.gio_unmount(target, force=force)
        finally:
            QApplication.restoreOverrideCursor()
        if not ok and not force and netloc.is_busy_message(msg):
            r = QMessageBox.question(self, T("cb_disconnect"), T("net_disconnect_busy", msg),
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r == QMessageBox.StandardButton.Yes:
                ok, msg = netloc.gio_unmount(target, force=True)
            else:
                msg = ""
        if not ok and msg and not quiet:
            QMessageBox.warning(self, T("cb_disconnect"), T("net_disconnect_failed", msg))
        places.drives().refresh(sync=True)
        for w in type(self).instances:
            w.model.reload()
            w.nav.rebuild()
        return ok

    def cmd_net_remove(self, it):
        self._net_remove_uri(it.extra.get("uri") or it.path[4:])

    def cmd_net_rename(self, it):
        name, ok = QInputDialog.getText(self, T("rename_title"), T("new_name"), text=it.name)
        if ok and name.strip():
            netloc.rename_location(it.extra.get("uri") or it.path[4:], name.strip())
            self.model.reload()
            self.nav.rebuild()

    # ================================================================== edit commands
    def cmd_copy(self):
        items = self.selected_items()
        paths = [i.path for i in items if i.kind in ("file", "dir")]
        paths += [i.extra["drive"]["mount"] for i in items if i.kind == "drive" and (i.extra.get("drive") or {}).get("mount")]
        zipm = [(i.extra["zip"], i.extra["member"], i.is_dir) for i in items if i.kind in ("zipfile", "zipdir")]
        if paths or zipm:
            clipboard.set_files(paths, False, zipm or None)

    def cmd_cut(self):
        items = [i for i in self.selected_items() if i.kind in ("file", "dir", "trash")]
        if items and self._can_delete():
            clipboard.set_files([i.path for i in items], True)

    def cmd_copy_path(self):
        items = self.selected_items()
        if items:
            QApplication.clipboard().setText("\n".join(f'"{i.path}"' for i in items))

    def cmd_paste(self, target: str | None = None):
        paths, cut, zipm = clipboard.get_files()
        if not paths and not zipm:
            return
        k = locations.kind(self.loc)
        zip_loc = None
        if target is None and k == "net" and paths:
            self.cmd_net_upload([p for p in paths if os.path.lexists(p)], self.loc[4:])
            return
        if target is None:
            if k == "zip":
                zip_loc = self.loc
                target = ziputil.split(self.loc)[0]
            else:
                target = self._fs_dir()
        if not target:
            return
        srcs = [Src(p) for p in paths if os.path.lexists(p)] + [Src(zip_=z, member=m, is_dir=d) for z, m, d in zipm]
        # pasting cut items from the Recycle Bin restores them there
        if cut and all(trash.entry_from_files_path(p) is not None and ("/Trash/files/" in p or "/.Trash" in p) for p in paths):
            targets = {p: os.path.join(target, trash.entry_from_files_path(p).display_name) for p in paths}
            self.run_job("restore", srcs, target, targets=targets)
            QApplication.clipboard().clear()
            return
        self.run_job("move" if cut else "copy", srcs, target, zip_loc=zip_loc, zip_dest=bool(zip_loc))
        if cut:
            QApplication.clipboard().clear()

    def cmd_paste_shortcut(self):
        paths, _, _ = clipboard.get_files()
        d = self._fs_dir()
        if paths and d:
            self.run_job("link", paths, d)

    def cmd_select_all(self):
        fw = QApplication.focusWidget()
        if fw is not None and fw.inherits("QLineEdit"):
            fw.selectAll()
            return
        self.view.selectAll()

    def cmd_invert_selection(self):
        sm = self.view.selectionModel()
        from PySide6.QtCore import QItemSelection
        sel = QItemSelection()
        n = self.model.rowCount()
        if n:
            sel.select(self.model.index(0, 0), self.model.index(n - 1, self.model.columnCount() - 1))
            sm.select(sel, QItemSelectionModel.SelectionFlag.Toggle | QItemSelectionModel.SelectionFlag.Rows)

    def cmd_undo(self):
        fw = QApplication.focusWidget()
        if fw is not None and fw.inherits("QLineEdit") and fw.isVisible():
            fw.undo()
            return
        undo.manager().undo(self)
        self.refresh()

    def cmd_redo(self):
        fw = QApplication.focusWidget()
        if fw is not None and fw.inherits("QLineEdit") and fw.isVisible():
            fw.redo()
            return
        undo.manager().redo(self)
        self.refresh()

    def _remote_folder_here(self) -> bool:
        """A net: location that is a real folder (not a list of servers or SMB shares)."""
        if locations.kind(self.loc) != "net":
            return False
        uri = self.loc[4:]
        scheme = uri.split(":", 1)[0].lower()
        if scheme in ("network", "dns-sd", "wsdd"):
            return False
        path = uri.split("://", 1)[-1].split("/", 1)[1] if "/" in uri.split("://", 1)[-1] else ""
        if scheme in ("smb", "cifs"):
            return bool(path.strip("/"))
        return True

    def _remote_items(self, items=None) -> list:
        """Selected files/folders of a remote (net:) folder listing."""
        if locations.kind(self.loc) != "net":
            return []
        items = self.selected_items() if items is None else items
        return [i for i in items if i.kind == "netfile" or (i.kind == "net" and i.extra.get("type") == T("file_folder"))]

    def cmd_net_delete(self, items=None):
        items = self._remote_items(items)
        if not items:
            return
        if len(items) == 1:
            txt = T("perm_delete_folder") if items[0].is_dir else T("perm_delete_file")
            txt += "\n\n" + items[0].name
        else:
            txt = T("perm_delete_n", len(items))
        if QMessageBox.question(self, T("app_name"), txt) != QMessageBox.StandardButton.Yes:
            return
        errs = self._busy(T("net_deleting"), lambda c: [m for m in (netloc.remote_delete(i.extra["uri"], i.is_dir)
                                                                       for i in items) if m])
        if errs:
            QMessageBox.warning(self, T("app_name"), "\n".join(errs[:10]))
        self.model.reload()

    def cmd_remote_rename(self, it=None):
        items = self._remote_items([it] if it else None)
        if len(items) != 1:
            return
        it = items[0]
        name, ok = QInputDialog.getText(self, T("rename_title"), T("new_name"), text=it.name)
        if not ok or not name.strip() or name.strip() == it.name or "/" in name:
            return
        err = self._busy(T("net_renaming"), lambda c: netloc.remote_rename(it.extra["uri"], name.strip()))
        if err:
            QMessageBox.warning(self, T("rename_title"), err)
        self._select_after = None
        self.model.reload()

    def cmd_net_new_folder(self):
        if locations.kind(self.loc) != "net":
            return
        name, ok = QInputDialog.getText(self, T("new_folder_name"), T("new_name"), text=T("new_folder_name"))
        if not ok or not name.strip() or "/" in name:
            return
        import urllib.parse
        uri = self.loc[4:].rstrip("/") + "/" + urllib.parse.quote(name.strip())
        err = self._busy(T("net_creating_folder"), lambda c: netloc.remote_mkdir(uri))
        if err:
            QMessageBox.warning(self, T("app_name"), err)
        self.model.reload()

    def cmd_delete(self, permanent: bool = False):
        fw = QApplication.focusWidget()
        if fw is not None and fw.inherits("QLineEdit") and fw.isVisible() and fw is not self.view:
            return
        if self._remote_items():
            self.cmd_net_delete()
            return
        items = self.selected_items()
        if not items or not self._can_delete():
            return
        k = locations.kind(self.loc)
        libs = [i for i in items if i.kind == "library"]
        if libs:
            for l in libs:
                self.cmd_delete_library(l.extra.get("library"))
            return
        zipitems = [i for i in items if i.kind in ("zipfile", "zipdir")]
        if zipitems:
            if QMessageBox.question(self, T("delete_file"), T("perm_delete_n", len(zipitems)) if len(zipitems) > 1
                                    else T("perm_delete_file")) != QMessageBox.StandardButton.Yes:
                return
            zp = zipitems[0].extra["zip"]
            self.run_job("zipdelete", [Src(zip_=zp, member=i.extra["member"], is_dir=i.is_dir) for i in zipitems], zp)
            return
        if k == "trash" or any(i.kind == "trash" for i in items):
            permanent = True
        use_trash = settings().get("use_trash") and not permanent
        if not fileops.confirm_delete(self, [i.path for i in items], not use_trash):
            return
        paths = [i.path for i in items if i.kind in ("file", "dir", "trash")]
        self.run_job("trash" if use_trash else "delete", paths, "")

    def cmd_rename(self):
        items = self.selected_items()
        if len(items) != 1:
            return
        if self._remote_items(items):
            self.cmd_remote_rename()
            return
        it = items[0]
        if it.kind == "library":
            lib = it.extra.get("library") or {}
            name, ok = QInputDialog.getText(self, T("rename_title"), T("new_name"), text=places.libraries().title(lib))
            if ok and name.strip() and name.strip() != places.libraries().title(lib):
                if not places.libraries().rename(lib["name"], name.strip()):
                    QMessageBox.warning(self, T("rename_title"), T("name_exists"))
            return
        idx = self.view.currentIndex()
        if not idx.isValid() or self.model.item_at(idx) is not it:
            r = self.model.row_of_path(it.path)
            idx = self.model.index(r, 0)
        self.view.scrollTo(idx)
        self.view.edit(idx)

    def do_rename(self, it, new: str):
        new = new.strip().rstrip(".") if new.strip() not in (".", "..") else ""
        old_display = self.model.display_name(it)
        if not new or new == old_display:
            return
        if it.kind == "library":
            places.libraries().rename(it.extra["library"]["name"], new)
            return
        if "/" in new or "\0" in new:
            QMessageBox.warning(self, T("rename_title"), T("bad_chars"))
            QTimer.singleShot(0, lambda: self.cmd_rename())
            return
        folder = os.path.dirname(it.path)
        name = new
        if old_display != it.name and not it.is_dir:
            # extension hidden: keep it
            _, ext = util.split_ext(it.name)
            name = new + ext
        else:
            _, oe = util.split_ext(it.name)
            _, ne = util.split_ext(new)
            if not it.is_dir and oe.lower() != ne.lower() and oe:
                r = QMessageBox.question(self, T("rename_title"), T("change_ext_warning"))
                if r != QMessageBox.StandardButton.Yes:
                    return
        dst = os.path.join(folder, name)
        if os.path.lexists(dst) and os.path.realpath(dst) != os.path.realpath(it.path):
            alt = util.unique_name(folder, name, it.is_dir)
            r = QMessageBox.question(self, T("rename_file"), T("rename_exists", name, alt))
            if r != QMessageBox.StandardButton.Yes:
                return
            dst = os.path.join(folder, alt)
        try:
            if os.path.realpath(dst) == os.path.realpath(it.path) and it.name.lower() == name.lower():
                tmp = it.path + ".~lxren"
                os.rename(it.path, tmp)
                os.rename(tmp, dst)
            else:
                os.rename(it.path, dst)
        except OSError as e:
            QMessageBox.warning(self, T("error_renaming"), f"{it.name}\n\n{e.strerror or e}")
            return
        undo.manager().push("rename", {"old": it.path, "new": dst})
        self._select_after = [dst]
        self.model.reload()

    def cmd_new_folder(self):
        if locations.kind(self.loc) == "net":
            self.cmd_net_new_folder()
            return
        d = self._fs_dir()
        if not d or not os.access(d, os.W_OK):
            return
        name = util.unique_name(d, T("new_folder_name"), True)
        p = os.path.join(d, name)
        try:
            os.mkdir(p)
        except OSError as e:
            QMessageBox.warning(self, T("app_name"), str(e))
            return
        undo.manager().push("newfolder", {"path": p})
        self._edit_after = p
        self._select_after = [p]
        self.model.reload()

    def cmd_new_file(self, name: str, template: str | None):
        d = self._fs_dir()
        if not d:
            return
        name = util.unique_name(d, name)
        p = os.path.join(d, name)
        try:
            if template == "zip":
                import zipfile
                with zipfile.ZipFile(p, "w"):
                    pass
            elif template:
                shutil.copyfile(template, p)
            else:
                open(p, "x").close()
        except OSError as e:
            QMessageBox.warning(self, T("app_name"), str(e))
            return
        undo.manager().push("copy", {"created": [p]})
        self._edit_after = p
        self._select_after = [p]
        self.model.reload()

    def cmd_new_shortcut(self):
        from .dialogs.misc import new_shortcut_wizard
        d = self._fs_dir()
        if d:
            p = new_shortcut_wizard(self, d)
            if p:
                undo.manager().push("link", {"created": [p]})
                self._select_after = [p]
                self.model.reload()

    def cmd_create_shortcut(self):
        items = [i for i in self.selected_items() if i.kind in ("file", "dir")]
        d = self._fs_dir()
        if not items or not d:
            return
        self.run_job("link", [i.path for i in items], d)

    def cmd_link_to(self, paths, dest):
        if paths and os.path.isdir(dest):
            self.run_job("link", paths, dest, select_result=False)

    def cmd_copy_move_to(self, move: bool):
        items = [i for i in self.selected_items() if i.kind in ("file", "dir", "zipfile", "zipdir")]
        if not items:
            return
        from .dialogs.misc import browse_for_folder
        title = T("move_items") if move else T("copy_items")
        prompt = (T("move_prompt_n", len(items)) if move else T("copy_prompt_n", len(items))) if len(items) > 1 else \
            (T("move_prompt", items[0].name) if move else T("copy_prompt", items[0].name))
        dest = browse_for_folder(self, title, prompt, self._fs_dir() or places.HOME,
                                 T("move_btn") if move else T("copy_btn"))
        if not dest:
            return
        srcs = [Src(i.path) if i.kind in ("file", "dir") else Src(zip_=i.extra["zip"], member=i.extra["member"],
                                                                     is_dir=i.is_dir) for i in items]
        self.run_job("move" if move else "copy", srcs, dest)

    def cmd_open_location(self, it):
        folder = it.extra.get("in_folder") or os.path.dirname(it.path)
        if it.is_link and locations.kind(self.loc) != "search" and not it.extra.get("in_folder"):
            tgt = os.path.realpath(it.path)
            self.navigate(os.path.dirname(tgt), select=[tgt])
            return
        self.navigate(folder, select=[it.path])

    # ================================================================== Recycle Bin
    def cmd_empty_trash(self):
        n = len(trash.list_entries())
        if not n:
            return
        msg = T("perm_delete_n", n) if n > 1 else T("perm_delete_file")
        if QMessageBox.question(self, T("delete_multiple") if n > 1 else T("delete_file"), msg) != QMessageBox.StandardButton.Yes:
            return
        self.run_job("empty", [], "")

    def cmd_restore(self, all_: bool = False):
        items = self.model.rows if all_ else self.selected_items()
        items = [i for i in items if i.kind == "trash"]
        if items:
            self.run_job("restore", [Src(i.path) for i in items], "")

    def cmd_restore_to(self):
        items = [i for i in self.selected_items() if i.kind == "trash"]
        if not items:
            return
        start = items[0].extra.get("orig_dir") or places.HOME
        if not os.path.isdir(start):
            start = places.HOME
        dest = QFileDialog.getExistingDirectory(self, strip_amp(T("c_restore_to")).rstrip("…. "), start)
        if not dest:
            return
        targets = {i.path: os.path.join(dest, i.extra["trash"].display_name if i.extra.get("trash") else i.name)
                   for i in items}
        self.run_job("restore", [Src(i.path) for i in items], dest, targets=targets)

    def cmd_open_original_location(self, it):
        e = it.extra.get("trash")
        d = it.extra.get("orig_dir") or (os.path.dirname(e.orig_path) if e else "")
        if d and os.path.isdir(d):
            self.new_tab(d)
        else:
            QMessageBox.information(self, T("recycle_bin"), T("location_unavailable_text", d or "?"))

    def _restore_all_from_anywhere(self):
        entries = trash.list_entries()
        if entries and QMessageBox.question(self, T("recycle_bin"), T("restore_all_confirm", len(entries))) \
                == QMessageBox.StandardButton.Yes:
            self.run_job("restore", [Src(e.files_path) for e in entries], "")

    def cmd_trash_properties(self):
        from .dialogs.misc import TrashPropertiesDialog
        TrashPropertiesDialog(self).exec()

    # ================================================================== archives
    def cmd_extract_all(self, zp: str | None = None):
        if zp is None:
            if locations.kind(self.loc) == "zip":
                zp = ziputil.split(self.loc)[0]
            else:
                items = self.selected_items()
                if len(items) == 1 and items[0].kind == "file":
                    zp = items[0].path
        if not zp:
            return
        from .dialogs.misc import ExtractDialog
        dlg = ExtractDialog(self, zp)
        if dlg.exec():
            dest, show = dlg.result_values()

            def after(res, dest=dest, show=show):
                if show and not res.get("cancelled"):
                    self.new_window(dest)
            job = Job("extract", [Src(zp)], dest)
            fileops.ops().run(job, self, on_done=lambda r: (undo.manager().push("copy", {"created": [dest]})
                                                             if r.get("created") else None, after(r), self.model.reload()))

    def cmd_extract_quick(self, ap: str, into_folder: bool):
        """'Extract Here' / 'Extract to "name"'."""
        folder = os.path.dirname(ap)
        if into_folder:
            dest = os.path.join(folder, util.unique_name(folder, ziputil.base_name(ap), True))
        else:
            dest = folder
        job = Job("extract", [Src(ap)], dest)

        def done(res):
            if into_folder and res.get("created"):
                undo.manager().push("copy", {"created": [dest]})
                self._select_after = [dest]
            self.model.reload()
        fileops.ops().run(job, self, on_done=done)

    def cmd_add_to_archive(self, paths: list[str]):
        if not paths:
            return
        from .dialogs.misc import AddToArchiveDialog
        d = os.path.dirname(paths[0])
        if not os.access(d, os.W_OK):
            d = places.xdg_dirs().get("desktop") or places.HOME
        dlg = AddToArchiveDialog(self, paths, d)
        if dlg.exec():
            dest, fmt = dlg.result_values()
            self.cmd_compress(paths, dest, fmt)

    def cmd_compress(self, paths: list[str], dest: str | None = None, fmt: str = "zip"):
        if not paths:
            return
        if dest is None:
            d = os.path.dirname(paths[0])
            if not os.access(d, os.W_OK):
                d = places.xdg_dirs().get("desktop") or places.HOME
            base = os.path.basename(paths[0].rstrip("/"))
            if len(paths) == 1 and not os.path.isdir(paths[0]):
                base = util.split_ext(base)[0]
            dest = os.path.join(d, util.unique_name(d, base + ziputil.FORMAT_EXT[fmt]))
        zp = dest
        job = Job("compress", [Src(p) for p in paths], zp, fmt=fmt)

        def done(res):
            undo.manager().record_result({"op": "copy", "created": res.get("created", [])})
            if res.get("created"):
                self._edit_after = zp
                self._select_after = [zp]
            self.model.reload()
        fileops.ops().run(job, self, on_done=done)

    # ================================================================== properties & dialogs
    def cmd_properties(self):
        items = self.selected_items()
        if not items:
            self.cmd_folder_properties()
            return
        from .dialogs.properties import show_properties
        show_properties(self, items, self.model)

    def cmd_properties_of_path(self, path: str, tab: str | None = None):
        from .dialogs.properties import show_properties
        from .items import FileItem
        show_properties(self, [FileItem.from_path(path)], self.model, tab)

    def cmd_folder_properties(self):
        k = locations.kind(self.loc)
        if k == "fs":
            self.cmd_properties_of_path(self.loc)
        elif k == "library":
            self.cmd_library_locations(locations.library_name(self.loc))
        elif k == "computer":
            self.cmd_system_properties()
        elif k == "zip":
            self.cmd_properties_of_path(ziputil.split(self.loc)[0])
        elif k == "trash":
            self.cmd_trash_properties()

    def cmd_open_with_dialog(self, it):
        self.cmd_open_with_dialog_path(it.path if it.kind == "file" else
                                       ziputil.extract_to_temp(it.extra["zip"], it.extra["member"]))

    def cmd_open_with_dialog_path(self, path: str):
        from .dialogs.openwith import OpenWithDialog
        OpenWithDialog(self, path).exec()

    def cmd_folder_options(self):
        from .dialogs.folderoptions import FolderOptionsDialog
        FolderOptionsDialog(self).exec()

    def cmd_choose_details(self):
        from .dialogs.misc import ChooseDetailsDialog
        dlg = ChooseDetailsDialog(self, [self.model.columns[i] for i in self.view.column_order()],
                                  {c: self.model.width_of(c) for c in self.model.columns},
                                  self.model.provider.folder_type if self.model.provider else "general")
        if dlg.exec():
            cols, widths = dlg.result_columns()
            self.model.set_columns(cols, widths)
            self.view._sync_header()
            self.view.schedule_layout()
            self._save_bag_soon()

    def cmd_customize(self):
        if locations.kind(self.loc) == "fs":
            self.cmd_properties_of_path(self.loc, "customize")

    def cmd_remove_properties(self):
        items = self.selected_items()
        if len(items) != 1:
            return
        from .dialogs.properties import RemovePropertiesDialog
        RemovePropertiesDialog(self, items[0].path).exec()
        self.model.reload()

    def save_tags(self, it, edits: dict):
        err = metadata.write_tags(it.path, edits)
        if err:
            QMessageBox.warning(self, T("app_name"), T("save_props_failed") + "\n\n" + err)
        it.invalidate()
        self.model.reload()

    def cmd_library_locations(self, name: str | None = None):
        from .dialogs.library import LibraryDialog
        name = name or locations.library_name(self.loc)
        lib = places.libraries().get(name)
        if lib:
            LibraryDialog(self, lib).exec()

    def cmd_new_library(self):
        lib = places.libraries().create(T("new_library_name"))
        self._edit_after = "shell:library:" + lib["name"]
        self.model.reload()
        QTimer.singleShot(300, lambda: self._rename_new_library(lib))

    def _rename_new_library(self, lib):
        name, ok = QInputDialog.getText(self, T("cb_new_library"), T("new_name"), text=lib["name"])
        if ok and name.strip() and name.strip() != lib["name"]:
            places.libraries().rename(lib["name"], name.strip())

    def cmd_delete_library(self, lib):
        if not lib:
            return
        if QMessageBox.question(self, T("delete_library"), T("delete_library_q", places.libraries().title(lib))) \
                == QMessageBox.StandardButton.Yes:
            places.libraries().delete(lib["name"])

    def cmd_include(self, name: str | None, folder: str | None):
        if not folder:
            return
        L = places.libraries()
        if name is None:
            lib = L.create(os.path.basename(folder) or folder)
            name = lib["name"]
        L.include(name, folder)
        self.navigate("shell:library:" + name)

    def cmd_save_search(self):
        import json
        s = locations.parse_search(self.loc)
        d = os.path.join(places.HOME, "Searches")
        os.makedirs(d, exist_ok=True)
        default = os.path.join(d, (s["q"] or "search").replace("/", "_") + ".lxsearch")
        path, _ = QFileDialog.getSaveFileName(self, T("cb_save_search"), default, "LinExplorer search (*.lxsearch)")
        if path:
            if not path.endswith(".lxsearch"):
                path += ".lxsearch"
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"scope": s["scope"], "query": s["q"], "content": s["content"]}, f, ensure_ascii=False)
            if os.path.dirname(path) == d:
                places.favorites().add(d) if all(f["path"] != d for f in places.favorites().items()) else None

    # ================================================================== sharing / devices / misc
    def cmd_share(self, path: str, writable: bool | None):
        err = sharing.set_share(path, writable)
        if err:
            QMessageBox.warning(self, T("file_sharing"), err)
        self.update_commands()

    def cmd_eject(self, it):
        d = it.extra.get("drive") or {}
        m = d.get("mount")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        rc, out, err = util.run(["gio", "mount", "-e", m], timeout=60) if m else (1, "", "")
        if rc != 0 and d.get("dev"):
            rc, out, err = util.run(["udisksctl", "unmount", "-b", d["dev"]], timeout=60)
            if rc == 0 and d.get("parent"):
                util.run(["udisksctl", "power-off", "-b", d["parent"]], timeout=30)
        QApplication.restoreOverrideCursor()
        if rc != 0:
            QMessageBox.warning(self, T("problem_ejecting"), (err or out).strip() or T("device_in_use"))
        else:
            if m and util.is_subpath(self.loc, m):
                self.navigate("shell:computer")
        places.drives().refresh()
        self.model.reload()

    def cmd_unmount(self, it):
        d = it.extra.get("drive") or {}
        rc, out, err = util.run(["udisksctl", "unmount", "-b", d.get("dev", "")], timeout=60)
        if rc != 0:
            rc, out, err = util.run(["gio", "mount", "-u", d.get("mount", "")], timeout=60)
        if rc != 0:
            QMessageBox.warning(self, T("app_name"), (err or out).strip())
        places.drives().refresh()
        self.model.reload()

    def cmd_map_drive(self):
        from .dialogs.mapdrive import MapDriveDialog
        dlg = MapDriveDialog(self)
        if dlg.exec() and dlg.local_path:
            places.drives().refresh(sync=True)
            self.navigate(dlg.local_path)

    def cmd_disconnect_drive(self):
        from .dialogs.mapdrive import DisconnectDialog
        DisconnectDialog(self).exec()
        places.drives().refresh()
        self.model.reload()

    def _spawn_first(self, cands: list[list[str]], what: str):
        for c in cands:
            if util.which(c[0]):
                util.spawn(c)
                return
        QMessageBox.information(self, T("app_name"), T("not_installed", what))

    def cmd_system_properties(self):
        self._spawn_first([["gnome-control-center", "system"], ["gnome-control-center", "info-overview"],
                           ["linux-hw-z"], ["systemsettings", "kcm_about-distro"], ["xfce4-about"], ["hardinfo2"],
                           ["hardinfo"]], T("cb_system_properties"))

    def cmd_uninstall(self):
        self._spawn_first([["gnome-software", "--mode=installed"], ["snap-store", "--mode=installed"],
                           ["plasma-discover", "--mode", "Installed"], ["synaptic-pkexec"], ["mintinstall"]],
                          T("cb_uninstall"))

    def cmd_control_panel(self):
        self._spawn_first([["gnome-control-center"], ["systemsettings"], ["xfce4-settings-manager"],
                           ["mate-control-center"], ["cinnamon-settings"], ["lxqt-config"]], T("cb_control_panel"))

    def cmd_net_center(self):
        self._spawn_first([["gnome-control-center", "network"], ["nm-connection-editor"],
                           ["systemsettings", "kcm_networkmanagement"]], T("cb_net_center"))

    def cmd_print(self):
        files = [i.path for i in self.selected_items() if i.kind == "file" and i.mime.name() in PRINTABLE]
        if not files:
            return
        if util.which("lp"):
            for f in files:
                rc, out, err = util.run(["lp", f], timeout=30)
                if rc != 0:
                    QMessageBox.warning(self, T("cb_print"), err.strip() or out.strip())
                    return
        else:
            QMessageBox.information(self, T("cb_print"), T("not_installed", "CUPS (lp)"))

    def cmd_email(self):
        files = [i.path for i in self.selected_items() if i.kind == "file"]
        if not files:
            return
        if util.which("xdg-email"):
            argv = ["xdg-email"]
            for f in files:
                argv += ["--attach", f]
            util.spawn(argv)
        else:
            QDesktopServices.openUrl(QUrl("mailto:"))

    def cmd_burn(self):
        app = self._burn_app()
        items = self.selected_items()
        files = [i.path for i in items if i.kind in ("file", "dir")] or ([self._fs_dir()] if self._fs_dir() else [])
        if app:
            argv = list(app)
            if app[0] == "brasero":
                argv += ["--data"] if False else []
            util.spawn(argv + files)

    def cmd_play_all(self):
        files = [i.path for i in self.model.rows if i.kind == "file" and i.mime.name().startswith("audio/")]
        if not files:
            return
        app = mime.default_app(mime.mime_for(files[0]))
        if app:
            app.launch(files)
        else:
            mime.open_with_default(files[0])

    def cmd_slideshow(self):
        imgs = [i for i in self.selected_items() if i.kind == "file" and i.mime.name().startswith("image/")]
        if len(imgs) <= 1:
            imgs = [i for i in self.model.rows if i.kind == "file" and i.mime.name().startswith("image/")]
        if not imgs:
            return
        from .dialogs.slideshow import SlideShow
        start = 0
        sel = self.selected_items()
        if sel and sel[0] in imgs:
            start = imgs.index(sel[0])
        self._slideshow = SlideShow([i.path for i in imgs], start)
        self._slideshow.showFullScreen()

    def cmd_set_default_fm(self):
        from .app import install_desktop_entry
        did = install_desktop_entry()
        rc, out, err = util.run(["xdg-mime", "default", did, "inode/directory"], timeout=10)
        mt = mime.db().mimeTypeForName("inode/directory")
        e = mime.app_by_id(did) or mime.DesktopEntry(os.path.join(os.path.expanduser("~/.local/share/applications"), did), did)
        try:
            mime.set_default_app(mt, e)
        except OSError:
            pass
        QMessageBox.information(self, T("app_name"), T("default_fm_done"))

    def cmd_help(self):
        from .dialogs.misc import HelpDialog
        HelpDialog(self).exec()

    def cmd_about(self):
        from .dialogs.misc import about
        about(self)
