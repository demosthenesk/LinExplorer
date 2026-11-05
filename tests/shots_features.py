"""Screenshots of the 1.1 features (tabs, network, archives). Run under Xvfb with a window manager:

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



from linexplorer import netloc  # noqa: E402

if os.environ.get("LX_NET_TEST") == "1":
    netloc.save_location("NAS (backup)", "smb://nas/backup/")
    netloc.save_location("Web server", "sftp://tester@localhost/")
    netloc.gio_mount("sftp://tester@localhost/", password="secret123", ask=lambda q, c: 0)
    netloc.gio_mount("ftp://localhost/", anonymous=True)
    netloc.gio_mount("smb://localhost/public/", anonymous=True)
    places.drives().refresh(sync=True)

import shutil  # noqa: E402
arc = os.path.join(DOCS, "Projects backup.tar.gz")
if not os.path.exists(arc):
    import tarfile
    with tarfile.open(arc, "w:gz") as tf:
        tf.add(os.path.join(DOCS, "Projects"), "Projects")
        tf.add(os.path.join(DOCS, "Invoices"), "Invoices")
        tf.add(os.path.join(DOCS, "todo.txt"), "todo.txt")
        tf.add(os.path.join(DOCS, "Budget 2026.txt"), "Budget 2026.txt")

w = MainWindow(DOCS)
w.resize(1180, 720)
w.move(40, 30)
w.show()
w.activateWindow()
loaded(w, 1200)
w.new_tab(os.path.join(HOME, "Pictures"), activate=False)
w.new_tab("shell:network", activate=False)
w.new_tab(arc, activate=False)
wait(800)
w.set_current_tab(0)
loaded(w)
select(w, {"Budget 2026.txt"})
wait(400)
screen("tabs")
w.set_current_tab(2)
loaded(w, 1000)
screen("network")
w.navigate("shell:network:sftp")
loaded(w, 800)
screen("network-sftp")
w.set_current_tab(3)
loaded(w, 600)
w.set_view_mode("details")
loaded(w, 400)
screen("archive-browse")
w.set_current_tab(0)
loaded(w)
select(w, {"Projects backup.tar.gz"})
wait(300)
r = w.view.visual_rect(w.model.row_of_path(arc)) if hasattr(w.view, "visual_rect") else None
pos = w.view.viewport().mapToGlobal(QPoint(220, 120))
QTimer.singleShot(900, lambda: (screen("archive-menu"), [m.close() for m in app.topLevelWidgets() if isinstance(m, QMenu)]))
w.item_context_menu(pos, w.model.index(w.model.row_of_path(arc), 0))
wait(400)
from linexplorer.dialogs.misc import AddToArchiveDialog  # noqa: E402
d = AddToArchiveDialog(w, [os.path.join(DOCS, "Projects"), os.path.join(DOCS, "todo.txt")], DOCS)
d.show()
wait(500)
grab(d, "add-to-archive")
d.reject()
from linexplorer.dialogs.mapdrive import AddNetLocationDialog  # noqa: E402
d = AddNetLocationDialog(w, "sftp")
d.show()
wait(500)
grab(d, "add-network-location")
d.reject()
# Recycle Bin (navigation pane entry + view)
from linexplorer import trash  # noqa: E402
for n in ("Meeting notes.txt", "Recipe - moussaka.txt"):
    p = os.path.join(DOCS, n)
    if os.path.exists(p):
        trash.move_to_trash(p)
if os.path.isdir(os.path.join(DOCS, "Invoices")):
    trash.move_to_trash(os.path.join(DOCS, "Invoices"))
w.close_other_tabs(0)
w.navigate("shell:trash")
loaded(w, 1200)
w.set_view_mode("details")
loaded(w, 400)
from PySide6.QtCore import QItemSelectionModel  # noqa: E402
w.view.selectionModel().select(w.model.index(0, 0), QItemSelectionModel.SelectionFlag.Select |
                               QItemSelectionModel.SelectionFlag.Rows)
wait(500)
screen("recycle-bin")
w.close()
wait(300)
if os.environ.get("LX_NET_TEST") == "1":
    for u in ("sftp://tester@localhost/", "ftp://localhost/", "smb://localhost/public/"):
        netloc.gio_unmount(u)
