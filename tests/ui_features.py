"""1.1 features: tabs, archives (tar/gz/bz2/xz) and network (SAMBA/FTP/SFTP).

    HOME=/tmp/lxhome DISPLAY=:99 python tests/ui_features.py [en|el]
Network checks run when LX_NET_TEST=1 (local sshd/vsftpd/smbd with user tester/secret123, gvfsd running)."""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox

errors = []


def hook(t, v, tb):
    errors.append("".join(traceback.format_exception(t, v, tb)))
    sys.__excepthook__(t, v, tb)


sys.excepthook = hook
lang = sys.argv[1] if len(sys.argv) > 1 else "en"
app = QApplication(sys.argv[:1])
from linexplorer import i18n, icons, settings as S  # noqa: E402

S.settings().set("language", lang)
i18n.init(lang)
icons.setup_theme()
from linexplorer.app import apply_theme  # noqa: E402

apply_theme(app)
from linexplorer import fileops, locations, places, trash, undo, ziputil  # noqa: E402
from linexplorer.window import MainWindow  # noqa: E402

HOME = os.path.expanduser("~")
DOCS = os.path.join(HOME, "Documents")
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name, flush=True)


def wait(ms=300, until=None, timeout=15):
    t0 = time.time()
    while True:
        loop = QEventLoop()
        QTimer.singleShot(ms if until is None else 50, loop.quit)
        loop.exec()
        if until is None:
            return
        if until() or time.time() - t0 > timeout:
            return


def wait_jobs(timeout=30):
    wait(until=lambda: not fileops.ops().busy(), timeout=timeout)
    wait(400)


# auto-answer modal dialogs
answer = {"mode": "accept"}


def closer():
    w = app.activeModalWidget()
    if w is not None:
        if isinstance(w, QMessageBox):
            btn = w.button(QMessageBox.StandardButton.Yes) or w.defaultButton()
            if answer["mode"] == "reject":
                btn = w.button(QMessageBox.StandardButton.No) or w.button(QMessageBox.StandardButton.Cancel) or btn
            if btn:
                btn.click()
            else:
                w.accept()
        elif isinstance(w, fileops.ConflictDialog):
            w._done(answer.get("conflict", "keep"))
        elif isinstance(w, fileops.MergeDialog):
            w._done("yes")
        elif isinstance(w, QDialog):
            if answer["mode"] == "reject":
                w.reject()
            else:
                w.accept()
    for m in app.topLevelWidgets():
        if isinstance(m, QMenu) and m.isVisible():
            m.close()


t = QTimer()
t.timeout.connect(closer)
t.start(250)


def select(names):
    from PySide6.QtCore import QItemSelectionModel
    sm = w.view.selectionModel()
    sm.clearSelection()
    for i, it in enumerate(w.model.rows):
        if it.name in names:
            sm.select(w.model.index(i, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            sm.setCurrentIndex(w.model.index(i, 0), QItemSelectionModel.SelectionFlag.NoUpdate)


def row(name):
    for it in w.model.rows:
        if it.name == name:
            return it
    return None


def loaded():
    wait(until=lambda: not w.model.is_loading)
    wait(150)


w = MainWindow(DOCS)
w.resize(1100, 700)
w.show()
loaded()

# ============================================================== tabs
check("starts with one tab", len(w.tabs) == 1)
check("tab bar shown by default", w.tabstrip.isVisible())
check("tab label is folder name", w.tabbar.tabText(0) == locations.display_name(DOCS))
w.new_tab(HOME)
loaded()
check("new tab opens and becomes current", len(w.tabs) == 2 and w.loc == HOME and w.tabbar.currentIndex() == 1)
check("address bar follows tab", w.addr.location == HOME if hasattr(w.addr, "location") else True)
check("window title follows tab", w.windowTitle() == locations.display_name(HOME))
w.navigate(os.path.join(HOME, "Music"))
loaded()
w.set_current_tab(0)
loaded()
check("switching back restores the folder", w.loc == DOCS and w.windowTitle() == locations.display_name(DOCS))
check("each tab keeps its own model", w.tabs[0].model is not w.tabs[1].model and w.model is w.tabs[0].model)
check("stack shows the tab's view", w.stack.currentWidget() is w.tabs[0].view)
w.set_current_tab(1)
check("per-tab history", w.hist_pos == 1 and w.history[-2:] == [HOME, os.path.join(HOME, "Music")])
w.go_back()
loaded()
check("back within the tab", w.loc == HOME and w.tabs[0].loc == DOCS)
# open a folder in a background tab
w.set_current_tab(0)
loaded()
w.open_item(row("Projects"), new_tab=True, activate=False)
loaded()
check("open in new tab (background)", len(w.tabs) == 3 and w.loc == DOCS and w.tabs[1].loc == os.path.join(DOCS, "Projects"))
wait(until=lambda: not w.tabs[1].model.is_loading)
check("background tab loaded its folder", w.tabs[1].model.rowCount() > 0)
# middle click in the view = open in new tab
inv = w.model.row_of_path(os.path.join(DOCS, "Invoices"))
w.view.open_new_tab.emit(w.model.index(inv, 0))
loaded()
check("middle-click opens folder in new tab", w.loc == os.path.join(DOCS, "Invoices") and len(w.tabs) == 4)
# close + reopen
n = len(w.tabs)
w.close_tab()
loaded()
check("close tab", len(w.tabs) == n - 1 and w.loc != os.path.join(DOCS, "Invoices"))
w.reopen_closed_tab()
loaded()
check("reopen closed tab", len(w.tabs) == n and w.loc == os.path.join(DOCS, "Invoices"))
# duplicate
w.duplicate_tab()
loaded()
check("duplicate tab keeps history", len(w.tabs) == n + 1 and w.loc == os.path.join(DOCS, "Invoices")
      and w.history == w.tabs[w.tabs.index(w.tab) - 1].history)
# move tabs
first = w.tabs[0]
w.tabbar.moveTab(0, 2)
check("dragging a tab reorders", w.tabs[2] is first and w.tabbar.tabText(2) == locations.display_name(first.loc))
# drop onto a tab copies into that tab's folder
from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
md = QMimeData()
md.setUrls([QUrl.fromLocalFile(os.path.join(DOCS, "todo.txt"))])
proj = [i for i, t in enumerate(w.tabs) if t.loc == os.path.join(DOCS, "Projects")][0]
w._tab_drop(proj, md, Qt.DropAction.CopyAction)
wait_jobs()
check("drop on a tab copies into its folder", os.path.exists(os.path.join(DOCS, "Projects", "todo.txt")))
wait(until=lambda: not w.tabs[proj].model.is_loading)
check("background tab refreshed after the copy",
      w.tabs[proj].model.row_of_path(os.path.join(DOCS, "Projects", "todo.txt")) >= 0)
check("drop action on tab", w.tabbar.drop_action_for(proj, md, Qt.KeyboardModifier.ControlModifier) == Qt.DropAction.CopyAction)
# menus
m = QMenu()
w._fill_file_menu()
w._fill_view_menu()
check("View menu lists tabs", any(a.menu() is not None and a.text() == i18n.T("m_tabs") for a in w.m_view.actions()))
w.navigate(DOCS)
loaded()
select({"Projects"})
m = QMenu()
w._fill_item_menu(m, w.selected_items())
check("context menu has Open in new tab", any(a.text() == i18n.T("c_open_new_tab") for a in m.actions()))
# keyboard
try:
    from PySide6.QtTest import QTest  # noqa: E402
except ImportError:
    QTest = None
if QTest is not None:
    w.activateWindow()
    w.view.setFocus()
    n = len(w.tabs)
    QTest.keyClick(w, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier)
    loaded()
    check("Ctrl+T new tab", len(w.tabs) == n + 1)
    i = w.tabs.index(w.tab)
    QTest.keyClick(w, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
    loaded()
    check("Ctrl+Tab next tab", w.tabs.index(w.tab) == (i + 1) % len(w.tabs))
    QTest.keyClick(w, Qt.Key.Key_1, Qt.KeyboardModifier.AltModifier)
    loaded()
    check("Alt+1 first tab", w.tabs.index(w.tab) == 0)
    QTest.keyClick(w, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier)
    loaded()
    check("Ctrl+W closes the tab, not the window", len(w.tabs) == n and w.isVisible())
    w.view.setFocus()
    select({"todo.txt"}) if w.loc == DOCS else None
    QTest.keyClick(w, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    loaded()
    check("Ctrl+Shift+T reopens", len(w.tabs) == n + 1)
sess = w.tab_session()
check("session lists tabs", len(sess["tabs"]) == len(w.tabs))
w.close_other_tabs(0)
loaded()
check("close other tabs", len(w.tabs) == 1)
S.settings().set("tab_bar_always", False)
check("tab bar hides with one tab when not always", not w.tabstrip.isVisible())
w.new_tab(DOCS)
loaded()
check("tab bar appears with two tabs", w.tabstrip.isVisible())
S.settings().set("tab_bar_always", True)
w.close_other_tabs(w.tabs.index(w.tab))
loaded()
w2 = MainWindow([DOCS, HOME, os.path.join(HOME, "Music")])
wait(until=lambda: not w2.model.is_loading)
check("window restored with tabs", len(w2.tabs) == 3 and w2.loc == DOCS)
w2.close()
wait(200)

# ============================================================== archives
w.navigate(DOCS)
loaded()
src = [os.path.join(DOCS, "Projects"), os.path.join(DOCS, "todo.txt")]
for fmt, ext in (("tar.gz", ".tar.gz"), ("tar.bz2", ".tar.bz2"), ("tar.xz", ".tar.xz"), ("tar", ".tar")):
    dest = os.path.join(DOCS, "Pack" + ext)
    w.cmd_compress(src, dest, fmt=fmt)
    wait_jobs()
    check(f"create {fmt}", os.path.exists(dest) and ziputil.fmt_of(dest) == fmt)
w.cmd_compress([os.path.join(DOCS, "README.md")], os.path.join(DOCS, "README.md.gz"), fmt="gz")
wait_jobs()
check("create .gz (single file)", ziputil.fmt_of(os.path.join(DOCS, "README.md.gz")) == "gz")
w.cmd_compress([os.path.join(DOCS, "README.md")], os.path.join(DOCS, "README.md.bz2"), fmt="bz2")
wait_jobs()
check("create .bz2 (single file)", ziputil.fmt_of(os.path.join(DOCS, "README.md.bz2")) == "bz2")
w.navigate(os.path.join(DOCS, "Pack.tar.gz"))
loaded()
check("browse into .tar.gz", locations.kind(w.loc) == "zip" and {"Projects", "todo.txt"} <= {i.name for i in w.model.rows})
w.navigate(ziputil.make(os.path.join(DOCS, "Pack.tar.gz"), "Projects"))
loaded()
check("browse subfolder inside .tar.gz", w.model.rowCount() > 0)
check("paste allowed into tar.gz", w._can_paste_here())
w.navigate(os.path.join(DOCS, "Pack.tar.gz"))
loaded()
md = QMimeData()
md.setUrls([QUrl.fromLocalFile(os.path.join(DOCS, "Budget 2026.txt"))])
w.handle_drop_loc(md, w.loc, Qt.DropAction.CopyAction, False)
wait_jobs()
names = [m.name for m in ziputil.index(os.path.join(DOCS, "Pack.tar.gz"))]
check("add file into .tar.gz", any(n.endswith("Budget 2026.txt") for n in names))
loaded()
select({"todo.txt"})
answer["mode"] = "accept"
w.cmd_delete(True)
wait_jobs()
names = [m.name for m in ziputil.index(os.path.join(DOCS, "Pack.tar.gz"))]
check("delete member from .tar.gz", not any(n.rstrip("/") == "todo.txt" for n in names))
w.navigate(os.path.join(DOCS, "README.md.bz2"))
loaded()
check("browse single .bz2 shows the file", [i.name for i in w.model.rows] == ["README.md"])
check("single .bz2 is read-only", not w._can_paste_here())
w.navigate(DOCS)
loaded()
w.cmd_extract_quick(os.path.join(DOCS, "Pack.tar.bz2"), True)
wait_jobs()
check("Extract to \"Pack\" (tar.bz2)", os.path.isfile(os.path.join(DOCS, "Pack", "todo.txt")))
os.makedirs(os.path.join(DOCS, "x"), exist_ok=True)
import shutil  # noqa: E402
shutil.copy(os.path.join(DOCS, "README.md.gz"), os.path.join(DOCS, "x"))
w.cmd_extract_quick(os.path.join(DOCS, "x", "README.md.gz"), False)
wait_jobs()
check("Extract Here (.gz)", open(os.path.join(DOCS, "x", "README.md"), "rb").read() ==
      open(os.path.join(DOCS, "README.md"), "rb").read())
loaded()
select({"Pack.tar.xz"})
m = QMenu()
w._fill_item_menu(m, w.selected_items())
texts = [a.text() for a in m.actions()]
check("archive context menu has Extract Here", i18n.T("c_extract_here") in texts)
from linexplorer.dialogs.misc import AddToArchiveDialog  # noqa: E402
d = AddToArchiveDialog(w, [os.path.join(DOCS, "todo.txt")], DOCS)
check("Add to archive dialog offers formats", d.findChild(type(d.findChildren(__import__("PySide6.QtWidgets", fromlist=["QComboBox"]).QComboBox)[0])).count() >= 5)
d.deleteLater()

# ============================================================== Shift + arrows / Shift + click
if QTest is not None:
    from PySide6.QtCore import QItemSelectionModel as _ISM  # noqa: E402
    w.navigate(DOCS)
    loaded()
    w.activateWindow()
    for mode in ("details", "list", "tiles", "content", "small", "medium", "large"):
        w.set_view_mode(mode)
        wait(300)
        w.view.setFocus()
        order = [c.row for c in w.view._cells if c is not None]
        n = len(order)
        sm = w.view.selectionModel()
        sm.setCurrentIndex(w.model.index(order[0], 0), _ISM.SelectionFlag.ClearAndSelect | _ISM.SelectionFlag.Rows)
        wait(50)
        down = Qt.Key.Key_Right if mode in ("tiles", "small", "medium", "large") else Qt.Key.Key_Down
        up = Qt.Key.Key_Left if mode in ("tiles", "small", "medium", "large") else Qt.Key.Key_Up
        if mode == "list":
            down, up = Qt.Key.Key_Down, Qt.Key.Key_Up
        for _ in range(3):
            QTest.keyClick(w.view, down, Qt.KeyboardModifier.ShiftModifier)
        wait(100)
        sel = sorted(order.index(r) for r in w.view.selected_rows())
        ok1 = sel == [0, 1, 2, 3]
        QTest.keyClick(w.view, up, Qt.KeyboardModifier.ShiftModifier)
        wait(50)
        ok2 = sorted(order.index(r) for r in w.view.selected_rows()) == [0, 1, 2]
        QTest.keyClick(w.view, Qt.Key.Key_End, Qt.KeyboardModifier.ShiftModifier)
        wait(50)
        ok3 = len(w.view.selected_rows()) == n
        QTest.keyClick(w.view, Qt.Key.Key_Home, Qt.KeyboardModifier.ShiftModifier)
        wait(50)
        ok4 = len(w.view.selected_rows()) == 1
        # Shift+click from the anchor to the 5th item
        r5 = order[min(4, n - 1)]
        rect = w.view.cell_rect(r5).translated(-w.view._off())
        QTest.mouseClick(w.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                         w.view._icon_area(w.view.cell_rect(r5)).translated(-w.view._off()).center())
        wait(100)
        ok5 = sorted(order.index(r) for r in w.view.selected_rows()) == list(range(min(5, n)))
        check(f"Shift+arrows / Shift+End / Shift+Home / Shift+click select ranges ({mode})",
              ok1 and ok2 and ok3 and ok4 and ok5)
        if not (ok1 and ok2 and ok3 and ok4 and ok5):
            print("   ", mode, ok1, ok2, ok3, ok4, ok5, n)
    w.set_view_mode("details")
    w.set_arrange("type") if hasattr(w, "set_arrange") else None
    w.model.set_group("type")
    wait(400)
    order = [c.row for c in w.view._cells if c is not None]
    sm = w.view.selectionModel()
    sm.setCurrentIndex(w.model.index(order[0], 0), _ISM.SelectionFlag.ClearAndSelect | _ISM.SelectionFlag.Rows)
    for _ in range(4):
        QTest.keyClick(w.view, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    wait(100)
    check("Shift+Down across groups follows the shown order",
          sorted(order.index(r) for r in w.view.selected_rows()) == [0, 1, 2, 3, 4])
    w.model.set_group(None)
    wait(200)

# ============================================================== New folder: name it after creation
if QTest is not None:
    w.navigate(DOCS)
    loaded()
    w.activateWindow()
    w.view.setFocus()
    w.cmd_new_folder()
    wait(2500)                      # folder watcher fires and the folder is re-read meanwhile
    ed = app.focusWidget()
    nf = i18n.T("new_folder_name")
    check("New folder: the name editor stays open after the folder refresh",
          w.view.state() == w.view.State.EditingState and ed is not None and ed.inherits("QLineEdit"))
    check("New folder: still selected while naming", any(i.name.startswith(nf) for i in w.selected_items()))
    if ed is not None and ed.inherits("QLineEdit"):
        ed.selectAll()
        ed.insert("Φάκελος 2026")
        QTest.keyClick(ed, Qt.Key.Key_Return)
    wait(1500)
    check("New folder: renamed to the typed name", os.path.isdir(os.path.join(DOCS, "Φάκελος 2026")))
    check("New folder: renamed folder stays selected", [i.name for i in w.selected_items()] == ["Φάκελος 2026"])
    w.view.setFocus()
    w.cmd_new_folder()
    wait(2500)
    ed = app.focusWidget()
    if ed is not None and ed.inherits("QLineEdit"):
        QTest.keyClick(ed, Qt.Key.Key_Escape)
    wait(800)
    sel = [i.name for i in w.selected_items()]
    check("New folder: Esc keeps the default name and the selection", len(sel) == 1 and sel[0].startswith(nf))

# ============================================================== Recycle Bin
from linexplorer import trash as TR  # noqa: E402
from PySide6.QtWidgets import QFileDialog  # noqa: E402
w.navigate(DOCS)
loaded()
check("Recycle Bin in the navigation pane", w.nav._find("shell:trash").isValid())
for n in ("bin1.txt", "bin2.txt", "bin3.txt"):
    open(os.path.join(DOCS, n), "w").write(n)
md = QMimeData()
md.setUrls([QUrl.fromLocalFile(os.path.join(DOCS, n)) for n in ("bin1.txt", "bin2.txt", "bin3.txt")])
w.handle_drop_loc(md, "shell:trash", Qt.DropAction.MoveAction, False)
wait_jobs()
wait(800)
check("drop on Recycle Bin deletes", not os.path.exists(os.path.join(DOCS, "bin1.txt")) and len(TR.list_entries()) >= 3)
tip = w.nav.model_.itemFromIndex(w.nav._find("shell:trash")).toolTip()
check("Recycle Bin icon/tooltip follow the contents", str(len(TR.list_entries())) in tip)
w.navigate("shell:trash")
loaded()
select({"bin1.txt"})
m = QMenu()
w._fill_item_menu(m, w.selected_items())
texts = [a.text() for a in m.actions()]
check("trash item menu: Restore / Restore to / Open original location",
      all(i18n.T(k) in texts for k in ("c_restore", "c_restore_to", "c_open_orig_location")))
os.makedirs(os.path.join(HOME, "Restored"), exist_ok=True)
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: os.path.join(HOME, "Restored"))
w.cmd_restore_to()
wait_jobs()
check("Restore to… another folder", os.path.isfile(os.path.join(HOME, "Restored", "bin1.txt")))
loaded()
select({"bin2.txt"})
w.cmd_restore()
wait_jobs()
check("Restore to the original location", os.path.isfile(os.path.join(DOCS, "bin2.txt")))
loaded()
select({"bin3.txt"})
w.cmd_open_original_location(w.selected_items()[0])
loaded()
check("Open original location opens it in a new tab", w.loc == DOCS)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
w._restore_all_from_anywhere()
wait_jobs()
check("Restore all from the navigation pane", os.path.isfile(os.path.join(DOCS, "bin3.txt")) and TR.is_empty())
S.settings().set("nav_show_recycle_bin", False)
check("Recycle Bin can be hidden from the navigation pane", not w.nav._find("shell:trash").isValid())
S.settings().set("nav_show_recycle_bin", True)
check("…and shown again", w.nav._find("shell:trash").isValid())

# ============================================================== network
from linexplorer import netloc  # noqa: E402
w.navigate("shell:network")
loaded()
cats = {i.path for i in w.model.rows if i.kind == "special"}
check("Network shows SAMBA/FTP/SFTP", {"shell:network:smb", "shell:network:ftp", "shell:network:sftp"} <= cats)
if os.environ.get("LX_NET_TEST") == "1":
    netloc.save_location("Test SFTP", "sftp://tester@localhost/")
    w.navigate("shell:network:sftp")
    loaded()
    check("saved SFTP location listed", any(i.kind == "net" and i.name == "Test SFTP" for i in w.model.rows))
    st, _ = netloc.gio_mount("sftp://tester@localhost/", password="secret123",
                             ask=lambda q, c: 0)
    check("SFTP mount with password", st == "ok")
    places.drives().refresh(sync=True)
    w.model.reload()
    loaded()
    conn = [i for i in w.model.rows if i.kind == "dir"]
    check("SFTP connection listed", bool(conn))
    if conn:
        w.open_item(conn[0])
        loaded()
        check("browse SFTP folder", w.loc.startswith("/run/user/") and w.model.rowCount() >= 0)
    w.navigate("shell:network:ftp")
    loaded()
    w.mount_network("ftp://localhost/", anonymous=True)
    loaded()
    check("FTP anonymous connect opens folder", w.loc.startswith("/run/user/"))
    w.navigate("shell:network:smb")
    loaded()
    w.mount_network("smb://localhost/public/", anonymous=True)
    loaded()
    check("SAMBA guest connect opens share", w.loc.startswith("/run/user/"))
    w.navigate("shell:network")
    loaded()
    counts = {i.path: i.extra.get("counts") for i in w.model.rows if i.kind == "special"}
    check("protocol tiles count connections", all((counts.get(f"shell:network:{p}") or (0, 0))[0] >= 1
                                                   for p in ("smb", "ftp", "sftp")))
    w.navigate("shell:network:ftp")
    loaded()
    c = [i for i in w.model.rows if i.kind == "dir"]
    if c:
        w.cmd_net_disconnect(c[0])
        places.drives().refresh(sync=True)
        w.model.reload()
        loaded()
    check("FTP disconnect", not any(i.kind == "dir" for i in w.model.rows))
else:
    print("SKIP network server checks (set LX_NET_TEST=1)")

w.close()
wait(300)
fails = [c for c in checks if not c[1]]
print(f"\n{len(checks) - len(fails)}/{len(checks)} checks passed, {len(errors)} exceptions")
for e in errors:
    print(e)
sys.exit(1 if fails or errors else 0)
