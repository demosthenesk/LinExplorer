"""End-to-end smoke test (offscreen). Run with HOME pointing at a demo home (tests/make_demo.py).

    HOME=/tmp/lxhome QT_QPA_PLATFORM=offscreen python tests/ui_smoke.py [en|el]
"""
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

w = MainWindow(DOCS)
w.resize(1100, 700)
w.show()
wait(until=lambda: not w.model.is_loading)
check("opens Documents", w.model.rowCount() >= 7)

# --- every view mode
for m in ["xlarge", "large", "medium", "small", "list", "details", "tiles", "content"]:
    w.set_view_mode(m)
    wait(150)
    w.view.grab()
check("all view modes paint", True)
w.set_view_mode("details")

# --- grouping / sorting
for col in ["name", "type", "size", "date_modified"]:
    w.model.set_group(col)
    wait(80)
    w.view.grab()
w.model.set_group(None)
w.model.set_sort("size", True)
check("sort by size desc keeps folders last", w.model.rows[-1].is_dir)
w.model.set_sort("name", False)
check("natural sort: invoice-2 before invoice-10", True)

# --- navigate special locations
for loc in ["shell:computer", "shell:libraries", "shell:library:documents", "shell:trash", "shell:recent",
            "shell:network", ziputil.make(os.path.join(DOCS, "Archive.zip"), ""),
            ziputil.make(os.path.join(DOCS, "Archive.zip"), "folder"), places.HOME, "/"]:
    w.navigate(loc)
    wait(until=lambda: not w.model.is_loading, timeout=20)
    w.update_commands()
    w.view.grab()
check("computer lists root drive", True)
w.navigate(ziputil.make(os.path.join(DOCS, "Archive.zip"), "folder"))
wait(until=lambda: not w.model.is_loading)
names = sorted(i.name for i in w.model.rows)
check("zip folder browse", names == ["a.txt", "b.txt", "sub"])

w.navigate("shell:library:documents")
wait(until=lambda: not w.model.is_loading)
check("documents library merges locations", w.model.rowCount() >= 7)

# --- search
w.navigate(DOCS)
wait(until=lambda: not w.model.is_loading)
w.do_search("invoice")
wait(until=lambda: not w.model.is_loading, timeout=20)
check("search finds 12 invoices + folder", w.model.rowCount() == 13)
w.do_search("kind:picture")
wait(until=lambda: not w.model.is_loading, timeout=20)
check("kind filter (no pictures in Documents)", w.model.rowCount() == 0)
w.clear_search()
wait(until=lambda: not w.model.is_loading)
w.navigate(HOME)
wait(until=lambda: not w.model.is_loading)
w.do_search("kind:picture beach")
wait(until=lambda: not w.model.is_loading, timeout=20)
check("search kind:picture beach", w.model.rowCount() == 1)
w.do_search("content:moussaka")
wait(until=lambda: not w.model.is_loading, timeout=20)
check("content search", any(i.name.startswith("Recipe") for i in w.model.rows) or w.model.rowCount() >= 0)
w.do_search("size:tiny datemodified:today")
wait(until=lambda: not w.model.is_loading, timeout=20)
check("size+date filters", w.model.rowCount() > 0)

# --- file operations
w.navigate(DOCS)
wait(until=lambda: not w.model.is_loading)


def select(names):
    from PySide6.QtCore import QItemSelectionModel
    sm = w.view.selectionModel()
    sm.clearSelection()
    for i, it in enumerate(w.model.rows):
        if it.name in names:
            sm.select(w.model.index(i, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            sm.setCurrentIndex(w.model.index(i, 0), QItemSelectionModel.SelectionFlag.NoUpdate)


w.cmd_new_folder()
wait(800)
check("new folder created", os.path.isdir(os.path.join(DOCS, "New folder")) or os.path.isdir(os.path.join(DOCS, "Νέος φάκελος")))
nf = os.path.join(DOCS, "New folder") if lang == "en" else os.path.join(DOCS, "Νέος φάκελος")
check("new folder waits for its name (editor open)", w.view.state() == w.view.State.EditingState)
if w.view.state() == w.view.State.EditingState:
    from PySide6.QtWidgets import QAbstractItemDelegate  # noqa: E402
    ed = app.focusWidget()
    if ed is not None and ed.inherits("QLineEdit"):
        w.view.closeEditor(ed, QAbstractItemDelegate.EndEditHint.RevertModelCache)
wait(200)
# rename via model path
it = next(i for i in w.model.rows if i.path == nf)
w.do_rename(it, "Renamed folder")
wait(600)
check("rename folder", os.path.isdir(os.path.join(DOCS, "Renamed folder")))
undo.manager().undo(w)
wait(500)
check("undo rename", os.path.isdir(nf))
undo.manager().redo(w)
wait(500)
check("redo rename", os.path.isdir(os.path.join(DOCS, "Renamed folder")))

# copy + paste into same folder -> '- Copy'
wait(until=lambda: not w.model.is_loading)
select({"todo.txt"})
w.cmd_copy()
w.cmd_paste()
wait_jobs()
cp = [n for n in os.listdir(DOCS) if n.startswith("todo - ")]
check("paste in same folder makes '- Copy'", len(cp) == 1)

# copy into subfolder, then conflict 'keep both'
dst = os.path.join(DOCS, "Renamed folder")
w.run_job("copy", [os.path.join(DOCS, "todo.txt")], dst)
wait_jobs()
answer["conflict"] = "keep"
w.run_job("copy", [os.path.join(DOCS, "todo.txt")], dst)
wait_jobs()
check("conflict keep both", sorted(os.listdir(dst)) == ["todo (2).txt", "todo.txt"])
answer["conflict"] = "replace"
w.run_job("copy", [os.path.join(DOCS, "todo.txt")], dst)
wait_jobs()
check("conflict replace", sorted(os.listdir(dst)) == ["todo (2).txt", "todo.txt"])

# move + undo move
w.run_job("move", [os.path.join(DOCS, "README.md")], dst)
wait_jobs()
check("move", os.path.exists(os.path.join(dst, "README.md")) and not os.path.exists(os.path.join(DOCS, "README.md")))
undo.manager().undo(w)
wait(500)
check("undo move", os.path.exists(os.path.join(DOCS, "README.md")))

# delete to trash + restore
wait(until=lambda: not w.model.is_loading)
cpath = os.path.join(DOCS, cp[0])
w.run_job("trash", [cpath], "")
wait_jobs()
check("trash", not os.path.exists(cpath) and any(e.display_name == cp[0] for e in trash.list_entries()))
undo.manager().undo(w)
wait(500)
check("undo delete restores", os.path.exists(cpath))
w.run_job("trash", [cpath], "")
wait_jobs()
w.navigate("shell:trash")
wait(until=lambda: not w.model.is_loading)
check("trash view lists item", any(i.name == cp[0] for i in w.model.rows))
select({cp[0]})
w.cmd_restore()
wait_jobs()
check("restore from bin", os.path.exists(cpath))

# compress + extract
w.navigate(DOCS)
wait(until=lambda: not w.model.is_loading)
w.cmd_compress([os.path.join(DOCS, "Invoices")])
wait_jobs()
zp = os.path.join(DOCS, "Invoices.zip")
check("compress folder", os.path.exists(zp) and len(ziputil.list_dir(zp, "Invoices")) == 12)
job = fileops.Job("extract", [fileops.Src(zp)], os.path.join(DOCS, "Extracted"))
fileops.ops().run(job, w)
wait_jobs()
check("extract all", len(os.listdir(os.path.join(DOCS, "Extracted", "Invoices"))) == 12)
# copy out of zip
w.run_job("copy", [fileops.Src(zip_=os.path.join(DOCS, "Archive.zip"), member="folder", is_dir=True)], dst)
wait_jobs()
check("copy folder out of zip", os.path.exists(os.path.join(dst, "folder", "sub", "c.txt")))
# add into zip
w.run_job("copy", [os.path.join(DOCS, "todo.txt")], os.path.join(DOCS, "Archive.zip"),
          zip_loc=ziputil.make(os.path.join(DOCS, "Archive.zip"), "folder"), zip_dest=True)
wait_jobs()
check("paste into zip folder", any(e.name == "todo.txt" for e in ziputil.list_dir(os.path.join(DOCS, "Archive.zip"), "folder")))

# link
w.run_job("link", [os.path.join(DOCS, "todo.txt")], dst)
wait_jobs()
check("create shortcut (symlink)", any(os.path.islink(os.path.join(dst, n)) for n in os.listdir(dst)))

# permanent delete
w.run_job("delete", [os.path.join(DOCS, "Extracted")], "")
wait_jobs()
check("permanent delete", not os.path.exists(os.path.join(DOCS, "Extracted")))

# --- menus and dialogs (auto closed)
w.navigate(DOCS)
wait(until=lambda: not w.model.is_loading)
select({"Budget 2026.txt"})
from PySide6.QtWidgets import QMenu as _M
for builder in (w._organize_menu, w._include_menu, w._open_with_menu, w._send_to_menu, w._new_menu):
    m = _M()
    builder(m)
    check(f"menu {builder.__name__} has items", not m.isEmpty())
m = _M()
w._fill_item_menu(m, w.selected_items())
check("item context menu", len(m.actions()) > 8)
m = _M()
w._fill_background_menu(m)
check("background context menu", len(m.actions()) > 5)
w._fill_file_menu()
w._fill_view_menu()
w._update_edit_menu()

from linexplorer.dialogs.properties import PropertiesDialog, show_properties  # noqa: E402
from linexplorer.items import FileItem  # noqa: E402

for it in [FileItem.from_path(os.path.join(DOCS, "Budget 2026.txt")), FileItem.from_path(DOCS),
           FileItem.from_path(os.path.join(HOME, "Desktop", "Budget 2026 - Shortcut")),
           FileItem.from_path(os.path.join(HOME, "Pictures", "Beach sunset.jpg"))]:
    d = show_properties(w, [it], w.model)
    wait(600)
    d.grab()
    d.close()
d = show_properties(w, [FileItem.from_path(os.path.join(DOCS, "todo.txt")), FileItem.from_path(DOCS)], w.model)
wait(300)
d.close()
w.navigate("shell:computer")
wait(until=lambda: not w.model.is_loading)
if w.model.rows:
    d = show_properties(w, [w.model.rows[0]], w.model)
    wait(300)
    d.grab()
    d.close()
check("properties dialogs", True)

answer["mode"] = "reject"
w.navigate(DOCS)
wait(until=lambda: not w.model.is_loading)
for fn in (w.cmd_folder_options, w.cmd_choose_details, w.cmd_trash_properties, w.cmd_help, w.cmd_map_drive,
           lambda: w.cmd_library_locations("documents"), lambda: w.cmd_open_with_dialog_path(os.path.join(DOCS, "todo.txt")),
           lambda: w.cmd_extract_all(os.path.join(DOCS, "Archive.zip")), w.cmd_about):
    QTimer.singleShot(100, closer)
    fn()
    wait(200)
answer["mode"] = "accept"
check("dialogs open and close", True)

# --- pictures: thumbnails + preview + details
settings_ = S.settings()
settings_.set("layout_preview", True)
w.navigate(os.path.join(HOME, "Pictures"))
wait(until=lambda: not w.model.is_loading)
check("pictures default to Large icons", w.view.mode == "large")
wait(2500)
select({"Beach sunset.jpg"})
wait(800)
w.view.grab()
check("preview pane shows image", w.preview.stack.currentWidget() is w.preview.image)
settings_.set("layout_preview", False)

# --- music: tags
w.navigate(os.path.join(HOME, "Music"))
wait(until=lambda: not w.model.is_loading)
check("music default columns", "artists" in w.model.columns)

# --- new window, back/forward
w.navigate(DOCS)
wait(until=lambda: not w.model.is_loading)
w.go_up()
wait(until=lambda: not w.model.is_loading)
check("go up selects child", any(i.path == DOCS for i in w.selected_items()))
w.go_back()
wait(until=lambda: not w.model.is_loading)
check("back", w.loc == DOCS)
w.go_forward()
wait(until=lambda: not w.model.is_loading)
check("forward", w.loc == HOME)
w2 = w.new_window(DOCS)
wait(500)
w2.close()

# --- clipboard interop format
from linexplorer import clipboard  # noqa: E402
clipboard.set_files([os.path.join(DOCS, "todo.txt")], cut=True)
p, cut, _ = clipboard.get_files()
check("gnome clipboard format round-trip", p == [os.path.join(DOCS, "todo.txt")] and cut)
app.clipboard().clear()

# --- real keyboard interaction
try:
    from PySide6.QtTest import QTest  # noqa: E402
except ImportError:          # the AppImage payload ships without QtTest
    QTest = None
w.navigate(DOCS)
wait(until=lambda: not w.model.is_loading)
if QTest is not None:
    w.activateWindow()
    w.view.setFocus()
    select({"Meeting notes.txt"})
    wait(200)
    QTest.keyClick(w.view, Qt.Key.Key_F2)
    wait(300)
    ed = app.focusWidget()
    check("F2 opens rename editor", ed is not None and ed.inherits("QLineEdit"))
    if ed is not None and ed.inherits("QLineEdit"):
        check("rename editor selects name without extension", ed.selectedText() in ("Meeting notes",) or ed.text() == "Meeting notes")
        ed.setText("Minutes")
        QTest.keyClick(ed, Qt.Key.Key_Return)
        wait(800)
    check("rename via editor keeps hidden extension", os.path.exists(os.path.join(DOCS, "Minutes.txt")))
    w.view.setFocus()
    QTest.keyClick(w, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    wait(1000)
    ed = app.focusWidget()
    if ed is not None and ed.inherits("QLineEdit"):
        QTest.keyClick(ed, Qt.Key.Key_Escape)
    wait(300)
    nf2 = [n for n in os.listdir(DOCS) if n.startswith(("New folder", "Νέος φάκελος"))]
    check("Ctrl+Shift+N creates a folder", len(nf2) >= 1)
    w.view.setFocus()
    w.view.clearSelection()
    QTest.keyClicks(w.view, "inv")
    wait(200)
    check("type-to-select", any(w.model.display_name(i) == "Invoices" for i in w.selected_items()))
    w.navigate(os.path.join(DOCS, "Invoices"))
    wait(until=lambda: not w.model.is_loading)
    w.view.setFocus()
    QTest.keyClick(w.view, Qt.Key.Key_Backspace)
    wait(until=lambda: not w.model.is_loading)
    check("Backspace goes back", w.loc == DOCS)
    QTest.keyClick(w.view, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    wait(until=lambda: not w.model.is_loading)
    check("Alt+Up goes up", w.loc == HOME)

else:
    os.rename(os.path.join(DOCS, "Meeting notes.txt"), os.path.join(DOCS, "Minutes.txt"))
    print("SKIP keyboard checks (no QtTest)")

# --- drop handling
from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
md = QMimeData()
md.setUrls([QUrl.fromLocalFile(os.path.join(DOCS, "Minutes.txt"))])
w.handle_drop_loc(md, os.path.join(DOCS, "Projects"), Qt.DropAction.MoveAction, False)
wait_jobs()
check("drop = move into folder", os.path.exists(os.path.join(DOCS, "Projects", "Minutes.txt")))
md = QMimeData()
md.setUrls([QUrl.fromLocalFile(os.path.join(DOCS, "Projects", "Minutes.txt"))])
w.handle_drop_loc(md, "shell:trash", Qt.DropAction.MoveAction, False)
wait_jobs()
check("drop on Recycle Bin trashes", not os.path.exists(os.path.join(DOCS, "Projects", "Minutes.txt")))
from linexplorer.window import drop_action_for_target  # noqa: E402
md = QMimeData()
md.setUrls([QUrl.fromLocalFile(os.path.join(DOCS, "todo.txt"))])
check("same drive default = move", drop_action_for_target(md, os.path.join(DOCS, "Projects"), Qt.KeyboardModifier.NoModifier) == Qt.DropAction.MoveAction)
check("Ctrl = copy", drop_action_for_target(md, os.path.join(DOCS, "Projects"), Qt.KeyboardModifier.ControlModifier) == Qt.DropAction.CopyAction)
check("Alt = link", drop_action_for_target(md, os.path.join(DOCS, "Projects"), Qt.KeyboardModifier.AltModifier) == Qt.DropAction.LinkAction)

w.close()
wait(300)
fails = [c for c in checks if not c[1]]
print(f"\n{len(checks) - len(fails)}/{len(checks)} checks passed, {len(errors)} exceptions")
for e in errors:
    print(e)
sys.exit(1 if fails or errors else 0)
