"""FTP over TLS + GIO fallback checks (needs vsftpd on :2121 with TLS allowed and :2122 with TLS required,
user tester/secret123, gvfsd + gvfsd-fuse running).

    HOME=/tmp/lxhome DISPLAY=:99 python tests/net_ftp_tls.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication(sys.argv[:1])
from linexplorer import i18n, icons, locations, netloc, places  # noqa: E402

i18n.init("en")
icons.setup_theme()
from linexplorer.dialogs import mapdrive  # noqa: E402
from linexplorer.window import MainWindow  # noqa: E402

checks = []
warnings = []


def check(name, cond):
    checks.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name, flush=True)


def wait(ms=300, until=None, timeout=20):
    t0 = time.time()
    while True:
        loop = QEventLoop()
        QTimer.singleShot(ms if until is None else 50, loop.quit)
        loop.exec()
        if until is None or until() or time.time() - t0 > timeout:
            return


mapdrive._ask_question = lambda parent, q, c: 0          # accept the self-signed certificate
QMessageBox.warning = staticmethod(lambda *a, **k: warnings.append(a[2] if len(a) > 2 else "") or QMessageBox.StandardButton.Ok)

w = MainWindow(os.path.expanduser("~"))
w.show()
wait(500)

# 1. server that requires TLS: plain ftp:// login is retried as ftps://
eff = mapdrive.gio_mount(w, "ftp://tester@localhost:2122/", user="tester", pw="secret123")
check("ftp:// on a TLS-only server falls back to ftps://", eff.startswith("ftps://tester@localhost:2122"))
places.drives().refresh(sync=True)
check("ftps connection listed under FTP", any(c["proto"] == "ftp" and c["uri"].startswith("ftps://") for c in netloc.connections()))
m = netloc.mount_for(eff)
check("ftps mount path resolved", m and os.path.isdir(m))

# 2. explicit FTPS chosen directly
eff2 = mapdrive.gio_mount(w, "ftps://tester@localhost:2121/", user="tester", pw="secret123")
check("explicit FTPS on a TLS-optional server", eff2.startswith("ftps://"))

# 3. plain ftp on the TLS-optional server, then a FUSE failure -> GIO fallback
eff3 = mapdrive.gio_mount(w, "ftp://tester@localhost:2121/", user="tester", pw="secret123")
check("plain ftp still works on a TLS-optional server", eff3.startswith("ftp://"))
places.drives().refresh(sync=True)
HOMEURI = eff3.rstrip("/") + "/home/tester"
fuse = netloc.mount_for(HOMEURI)
check("gvfs path maps back to the URI", netloc.gvfs_path_to_uri(fuse + "/ftpdocs") == HOMEURI + "/ftpdocs")
real_scan = locations._scan


def broken(path, emit, cancelled):
    if "/gvfs/" in path:
        raise OSError(5, "Input/output error", path)
    return real_scan(path, emit, cancelled)


locations._scan = broken
w.navigate(fuse)
wait(until=lambda: w.loc.startswith("net:") and not w.model.is_loading)
wait(400)
check("EIO on the FUSE path switches to GIO browsing", w.loc.startswith("net:ftp://"))
names = {i.name: i for i in w.model.rows}
check("GIO listing shows files and folders", "notes.txt" in names and "ftpdocs" in names)
check("file sizes and dates from GIO", names.get("notes.txt") and names["notes.txt"].size == 11 and names["notes.txt"].mtime > 0)
check("file type from GIO", names.get("notes.txt") and "text" in names["notes.txt"].type_text().lower())
dl = "/tmp/lxhome/dl"
os.makedirs(dl, exist_ok=True)
w.cmd_net_download([names["notes.txt"], names["ftpdocs"]], dl)
check("download a file through GIO", open(os.path.join(dl, "notes.txt")).read().strip() == "hello sftp")
check("download a folder through GIO", os.path.isdir(os.path.join(dl, "ftpdocs")))
up = os.path.join(dl, "upload me.txt")
open(up, "w").write("uploaded\n")
w.cmd_net_upload([up], w.loc[4:])
wait(until=lambda: not w.model.is_loading)
wait(300)
check("upload through GIO", any(i.name == "upload me.txt" for i in w.model.rows))
os.makedirs(os.path.join(dl, "upfolder", "inner"), exist_ok=True)
open(os.path.join(dl, "upfolder", "inner", "x.txt"), "w").write("x")
w.cmd_net_upload([os.path.join(dl, "upfolder")], w.loc[4:])
check("upload a folder through GIO", os.path.isfile("/home/tester/upfolder/inner/x.txt"))
w.navigate("net:" + HOMEURI + "/ftpdocs/")
wait(until=lambda: not w.model.is_loading)
check("open a sub-folder through GIO", w.model.rowCount() >= 0 and w.loc.endswith("/ftpdocs/"))
# 4. an error GIO cannot fix is reported with GIO's text, not just 'Input/output error'
warnings.clear()
w.navigate(fuse + "/does-not-exist")
wait(800)
w.navigate("net:" + HOMEURI + "/does-not-exist/")
wait(until=lambda: not w.model.is_loading)
wait(500)
check("GIO error text shown", any("does-not-exist" in str(x) or "directory" in str(x).lower() for x in warnings))

# 5. disconnecting
places.drives().refresh(sync=True)
w.navigate(fuse)                                    # FUSE broken -> GIO view of the connection
wait(until=lambda: w.loc.startswith("net:") and not w.model.is_loading)
w.new_tab(netloc.mount_for(eff) or "/")             # a second tab inside the ftps connection
wait(until=lambda: not w.model.is_loading)
locations._scan = real_scan
check("background menu offers Disconnect inside a connection", w._network_connection_here())
w.set_current_tab(0)
wait(300)
check("Disconnect offered in the GIO view", w._network_connection_here())
w.disconnect_location(w.loc)
wait(500)
places.drives().refresh(sync=True)
check("disconnect from the GIO view unmounts", not any(c["uri"].startswith("ftp://tester@localhost:2121") for c in netloc.connections()))
check("tab inside the connection moved to Network", w.tabs[0].loc == "shell:network")
w.navigate("shell:computer")
wait(until=lambda: not w.model.is_loading)
wait(300)
drv = [i for i in w.model.rows if i.kind == "drive" and (i.extra.get("drive") or {}).get("kind") == "network"]
check("network connections shown in Computer", len(drv) >= 1)
from PySide6.QtWidgets import QMenu  # noqa: E402
w.view.selectionModel().clearSelection()
from PySide6.QtCore import QItemSelectionModel  # noqa: E402
r = w.model.rows.index(drv[0])
w.view.selectionModel().select(w.model.index(r, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
mm = QMenu()
w._fill_item_menu(mm, w.selected_items())
check("Computer: network drive menu has Disconnect", any(a.text() == i18n.T("cb_disconnect") for a in mm.actions()))
for d in drv:
    w.cmd_net_disconnect(d)
wait(500)
places.drives().refresh(sync=True)
check("disconnect from Computer unmounts (ftps too)", not netloc.connections())
check("other tab inside the ftps connection moved away", all(not t.loc.startswith("/run/user") for t in w.tabs))
# 6. leftover saved locations and stale connections
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
netloc.save_location("FTP: user1@localhost", "ftp://user1@localhost/")
netloc.save_location("FTP: user1@localhost (tls)", "ftps://user1@localhost/")
netloc.save_location("SFTP (SSH): user1@localhost", "sftp://user1@localhost/")
w.navigate("shell:network:ftp")
wait(until=lambda: not w.model.is_loading)
wait(300)
saved_items = [i for i in w.model.rows if i.extra.get("saved")]
check("saved FTP locations listed", len(saved_items) >= 2)
mm = QMenu()
w.view.selectionModel().clearSelection()
r = w.model.rows.index(saved_items[0])
w.view.selectionModel().select(w.model.index(r, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
w._fill_item_menu(mm, w.selected_items())
texts = [a.text() for a in mm.actions()]
check("saved location menu: Remove + Connect", i18n.T("c_remove_location") in texts and i18n.T("cb_connect") in texts)
w._net_remove_uri("ftp://user1@localhost/")
check("remove one saved location", not any(l["uri"] == "ftp://user1@localhost/" for l in netloc.saved()))
w.cmd_remove_all_saved("ftp")
check("remove all saved FTP locations (SFTP kept)",
      not any(netloc.proto_of_uri(l["uri"]) == "ftp" for l in netloc.saved()) and
      any(l["uri"].startswith("sftp://") for l in netloc.saved()))
# a connection whose folder is gone: gvfs still holds it, the FUSE folder does not exist
st, _ = netloc.gio_mount("ftp://tester@localhost:2121/", password="secret123")
st2, _ = netloc.gio_mount("ftps://tester@localhost:2122/", password="secret123", ask=lambda q, c: 0)
import subprocess  # noqa: E402
subprocess.run(["pkill", "-9", "-f", "gvfsd-fuse"])
wait(500)
places.drives().refresh(sync=True)
check("stale mounts are not visible as folders", not any(c["uri"].startswith(("ftp://tester@localhost:2121", "ftps://"))
                                                          for c in netloc.connections()))
check("gio still holds them", len([m for m in netloc.gio_mount_list() if m["proto"] == "ftp"]) >= 2)
w.navigate("shell:network:ftp")
wait(until=lambda: not w.model.is_loading)
wait(300)
stale = [i for i in w.model.rows if i.extra.get("stale")]
check("stale connections listed under FTP", len(stale) >= 2)
w.view.selectionModel().clearSelection()
r = w.model.rows.index(stale[0])
w.view.selectionModel().select(w.model.index(r, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
mm = QMenu()
w._fill_item_menu(mm, w.selected_items())
check("stale connection menu has Force disconnect", i18n.T("c_force_disconnect") in [a.text() for a in mm.actions()])
w.disconnect_location(stale[0].extra["uri"], force=True)
check("force disconnect of a stale connection", not netloc.is_mounted(stale[0].extra["uri"]))
w.navigate("shell:network")
wait(until=lambda: not w.model.is_loading)
w.cmd_force_disconnect_all()
check("force disconnect all", not netloc.gio_mount_list())
mm = QMenu()
w.nav_context_menu.__func__  # exists
# 7. built-in FTP client when gvfs cannot list the server (FileZilla Server: 'Operation failed')
from linexplorer import ftpclient  # noqa: E402
from PySide6.QtWidgets import QInputDialog  # noqa: E402
real_gio_list = netloc.gio_list
netloc.gio_list = lambda uri, timeout=30: (2, [], f"gio: {uri}: Operation failed") \
    if (":2121" in uri or ":2123" in uri) else real_gio_list(uri, timeout)
dialogs = []


class FakeCreds:
    def __init__(self, parent, uri, msg=""):
        dialogs.append((uri, msg))
        from PySide6.QtWidgets import QLineEdit, QCheckBox
        self.user, self.pw, self.domain, self.anon = QLineEdit("tester"), QLineEdit("secret123"), QLineEdit(), QCheckBox()

    def exec(self):
        return 1


mapdrive.CredentialsDialog = FakeCreds
HOME1 = "ftp://tester@localhost:2121/home/tester/"
w.navigate("net:" + HOME1)
wait(until=lambda: not w.model.is_loading and w.model.rowCount() > 0, timeout=20)
wait(300)
names = {i.name: i for i in w.model.rows}
check("gvfs failure -> asks for the password once", len(dialogs) == 1 and "Operation failed" in dialogs[0][1])
check("built-in client lists the FTP folder", "notes.txt" in names and "ftpdocs" in names and netloc.builtin(HOME1))
check("built-in listing has sizes and dates", names["notes.txt"].size == 11 and names["notes.txt"].mtime > 0)
dl = "/tmp/lxhome/dl2"
os.makedirs(dl, exist_ok=True)
w.cmd_net_download([names["notes.txt"], names["ftpdocs"]], dl)
check("built-in download (file + folder)", open(os.path.join(dl, "notes.txt")).read().strip() == "hello sftp"
      and os.path.isfile(os.path.join(dl, "ftpdocs", "f.txt")))
src = os.path.join(dl, "big upload.bin")
open(src, "wb").write(os.urandom(300000))
from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
md = QMimeData()
md.setUrls([QUrl.fromLocalFile(src)])
check("drop on a remote folder = copy", __import__("linexplorer.window", fromlist=["x"]).drop_action_for_target(
    md, w.loc, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.KeyboardModifier.NoModifier).name == "CopyAction")
w.handle_drop_loc(md, w.loc, None, False)
wait(until=lambda: not w.model.is_loading)
check("built-in upload by drop", open("/home/tester/big upload.bin", "rb").read() == open(src, "rb").read())
QInputDialog.getText = staticmethod(lambda *a, **k: ("Νέος φάκελος", True))
w.cmd_new_folder()
wait(until=lambda: not w.model.is_loading)
check("built-in new folder (Greek name)", os.path.isdir("/home/tester/Νέος φάκελος"))
wait(300)
it = [i for i in w.model.rows if i.name == "big upload.bin"][0]
QInputDialog.getText = staticmethod(lambda *a, **k: ("renamed.bin", True))
w.cmd_remote_rename(it)
check("built-in rename", os.path.isfile("/home/tester/renamed.bin"))
wait(until=lambda: not w.model.is_loading)
wait(300)
sel = [i for i in w.model.rows if i.name in ("renamed.bin", "Νέος φάκελος")]
w.cmd_net_delete(sel)
check("built-in delete (file + folder)", not os.path.exists("/home/tester/renamed.bin")
      and not os.path.exists("/home/tester/Νέος φάκελος"))
wait(until=lambda: not w.model.is_loading)
check("Disconnect offered for the built-in connection", w._network_connection_here())
w.disconnect_location(w.loc)
wait(300)
check("built-in disconnect forgets the login and leaves the folder", not netloc.builtin(HOME1) and w.loc == "shell:network")
# explicit FTPS with TLS session resumption required (vsftpd require_ssl_reuse=YES, like FileZilla Server)
S = S2 = None
dialogs.clear()
S = S2 = "ftps://tester@localhost:2123/home/tester/"
d = json_path = None
from linexplorer.settings import settings as _settings  # noqa: E402
_settings().set_json("ftp_trusted_certs", {})
w.navigate("net:" + S)
wait(until=lambda: not w.model.is_loading and w.model.rowCount() > 0, timeout=20)
wait(300)
check("FTPS with session resumption via built-in client", any(i.name == "notes.txt" for i in w.model.rows))
check("self-signed certificate trusted after the question", "localhost:2123" in _settings().get_json("ftp_trusted_certs", {}))
w.disconnect_location(w.loc)
# credentials typed for gvfs are reused when gvfs cannot list
dialogs.clear()
effr = mapdrive.gio_mount(w, "ftp://tester@localhost:2121/", user="tester", pw="secret123")
w.navigate("net:" + HOME1)
wait(until=lambda: not w.model.is_loading and w.model.rowCount() > 0, timeout=20)
check("password typed for the connection is reused (no second prompt)", not dialogs and any(i.name == "notes.txt" for i in w.model.rows))
w.disconnect_location(w.loc)
netloc.gio_list = real_gio_list
for u in (eff, eff2, eff3):
    netloc.gio_unmount(u)
w.close()
wait(200)
fails = [c for c in checks if not c[1]]
print(f"\n{len(checks) - len(fails)}/{len(checks)} checks passed")
sys.exit(1 if fails else 0)
