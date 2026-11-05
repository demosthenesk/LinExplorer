"""Screenshots for review / README. Run under Xvfb with a window manager:

    HOME=/tmp/lxhome xvfb-run -s "-screen 0 1280x800x24" sh -c "openbox & python tests/shots.py OUTDIR [en|el]"
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from PySide6.QtCore import QEventLoop, QPoint, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

out = sys.argv[1]
lang = sys.argv[2] if len(sys.argv) > 2 else "en"
only = sys.argv[3].split(",") if len(sys.argv) > 3 else None
os.makedirs(out, exist_ok=True)
app = QApplication(sys.argv[:1])
from linexplorer import i18n, icons, settings as S  # noqa: E402

S.settings().set("language", lang)
i18n.init(lang)
icons.setup_theme()
from linexplorer.app import apply_theme  # noqa: E402

apply_theme(app)
app.setWindowIcon(icons.app_icon())
from linexplorer import fileops, places, ziputil  # noqa: E402
from linexplorer.window import MainWindow  # noqa: E402

HOME = os.path.expanduser("~")
DOCS = os.path.join(HOME, "Documents")


def wait(ms=300):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def loaded(w, extra=600):
    t0 = time.time()
    while w.model.is_loading and time.time() - t0 < 15:
        wait(50)
    wait(extra)


def screen(name):
    if only and name not in only:
        return
    app.primaryScreen().grabWindow(0).save(os.path.join(out, name + ".png"))
    print("saved", name)


def grab(widget, name):
    if only and name not in only:
        return
    widget.grab().save(os.path.join(out, name + ".png"))
    print("saved", name)


def select(w, names):
    from PySide6.QtCore import QItemSelectionModel
    sm = w.view.selectionModel()
    sm.clearSelection()
    for i, it in enumerate(w.model.rows):
        if it.name in names:
            sm.select(w.model.index(i, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            sm.setCurrentIndex(w.model.index(i, 0), QItemSelectionModel.SelectionFlag.NoUpdate)


w = MainWindow(DOCS)
w.resize(1180, 720)
w.move(40, 30)
w.show()
w.activateWindow()
loaded(w, 1200)
w.view.setFocus()
select(w, {"Budget 2026.txt"})
wait(500)
grab(w, "01-documents-details")

# pictures, large icons with thumbnails and preview pane
S.settings().set("layout_preview", True)
w.navigate(os.path.join(HOME, "Pictures"))
loaded(w, 2500)
select(w, {"Mountain lake.jpg"})
wait(1200)
grab(w, "02-pictures-large-preview")
S.settings().set("layout_preview", False)

# computer
w.navigate("shell:computer")
loaded(w, 800)
grab(w, "03-computer")

# search
w.navigate(HOME)
loaded(w)
w.do_search("invoice")
loaded(w, 1200)
grab(w, "04-search-content")

# libraries + library pane
w.navigate("shell:library:documents")
loaded(w, 800)
w.set_view_mode("tiles")
wait(400)
grab(w, "05-library-tiles")
w.set_view_mode("details")

# grouped details
w.navigate(DOCS)
loaded(w)
w.model.set_group("type")
wait(600)
grab(w, "06-grouped-by-type")
w.model.set_group(None)

# organize menu (popup) and context menu
w.raise_()
w.activateWindow()
wait(300)
org = w.cmdbar._buttons[0]


def shoot_menu(name, open_fn):
    if only and name not in only:
        return
    def later():
        wait(500)
        screen(name)
        for m in app.topLevelWidgets():
            if isinstance(m, QMenu) and m.isVisible():
                m.close()
    QTimer.singleShot(50, later)
    open_fn()


def open_org():
    m = QMenu(org)
    w._organize_menu(m)
    sub = [a for a in m.actions() if a.menu()]
    m.popup(org.mapToGlobal(QPoint(0, org.height())))
    wait(300)
    if sub:
        m.setActiveAction(sub[0])
        sub[0].menu().popup(m.mapToGlobal(m.actionGeometry(sub[0]).topRight()))
    wait(800)


shoot_menu("07-organize-menu", open_org)
wait(400)
select(w, {"Meeting notes.txt"})


def open_ctx():
    m = QMenu(w)
    w._fill_item_menu(m, w.selected_items())
    r = w.view.visualRect(w.view.currentIndex())
    m.popup(w.view.viewport().mapToGlobal(r.center()))
    wait(800)


shoot_menu("08-context-menu", open_ctx)
wait(400)


def open_bg():
    w.view.clearSelection()
    m = QMenu(w)
    w._fill_background_menu(m)
    sub = [a for a in m.actions() if a.menu()]
    m.popup(w.view.viewport().mapToGlobal(QPoint(500, 300)))
    wait(300)
    new = [a for a in sub if a.menu() and a.text().replace("&", "") in ("New", "Δημιουργία")]
    if new:
        m.setActiveAction(new[0])
        new[0].menu().popup(m.mapToGlobal(m.actionGeometry(new[0]).topRight()))
    wait(800)


shoot_menu("09-background-menu", open_bg)
wait(400)

# views popup
def open_views():
    from linexplorer.commandbar import ViewsPopup
    pop = ViewsPopup(w.view.mode, w.cmdbar)
    pop.adjustSize()
    v = w.cmdbar.views
    pop.move(v.mapToGlobal(QPoint(v.width() - pop.width(), v.height())))
    pop.show()
    wait(600)
    screen("10-views-popup")
    pop.close()


if not only or "10-views-popup" in only:
    open_views()
wait(300)

# menu bar shown
S.settings().set("layout_menubar", True)
wait(300)


def open_file_menu():
    w.mbar.open_menu(2)
    wait(800)


shoot_menu("11-menubar-view-menu", open_file_menu)
S.settings().set("layout_menubar", False)
wait(400)

# properties dialog
from linexplorer.dialogs.properties import show_properties  # noqa: E402
from linexplorer.items import FileItem  # noqa: E402

d = show_properties(w, [FileItem.from_path(os.path.join(DOCS, "Budget 2026.txt"))], w.model)
d.move(300, 120)
wait(800)
grab(d, "12-properties-file")
d.tabs.setCurrentIndex(2)
wait(300)
grab(d, "13-properties-details")
d.close()
d = show_properties(w, [FileItem.from_path(DOCS)], w.model)
d.move(300, 120)
wait(1000)
grab(d, "14-properties-folder")
d.close()
w.navigate("shell:computer")
loaded(w)
if w.model.rows:
    d = show_properties(w, [w.model.rows[0]], w.model)
    d.move(300, 120)
    wait(800)
    grab(d, "15-properties-drive")
    d.close()

# conflict dialog
data = {"src": os.path.join(DOCS, "todo.txt"), "dst": os.path.join(DOCS, "Invoices", "invoice-1.txt"),
        "moving": False, "remaining": 3}
cd = fileops.ConflictDialog(data, w)
cd.move(320, 100)
cd.show()
wait(600)
grab(cd, "16-conflict-dialog")
cd.close()

# progress dialog
job = fileops.Job("copy", [fileops.Src(os.path.join(DOCS, "Invoices"))], os.path.join(HOME, "Downloads"))
pd = fileops.ProgressDialog(job, w)
job.total_bytes = 1_500_000_000
job.sources = [fileops.Src(os.path.join(DOCS, "Invoices"))] * 37
pd._toggle()
pd.update_progress({"bytes": 600_000_000, "total": 1_500_000_000, "items": 120, "total_items": 340,
                    "name": "invoice-7.txt", "from": os.path.join(DOCS, "Invoices"),
                    "to": os.path.join(HOME, "Downloads"), "elapsed": 22})
pd.move(320, 160)
pd.show()
wait(600)
grab(pd, "17-progress-dialog")
pd.hide()

# folder options
from linexplorer.dialogs.folderoptions import FolderOptionsDialog  # noqa: E402
fo = FolderOptionsDialog(w)
fo.tabs.setCurrentIndex(1)
fo.move(320, 80)
fo.show()
wait(600)
grab(fo, "18-folder-options")
fo.close()

# zip browsing
w.navigate(ziputil.make(os.path.join(DOCS, "Archive.zip"), ""))
loaded(w)
grab(w, "19-zip-folder")

# recycle bin
w.navigate("shell:trash")
loaded(w)
grab(w, "20-recycle-bin")
w.close()
wait(300)
print("done")
