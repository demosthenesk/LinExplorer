"""Application start-up, single instance, theme and desktop integration."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QStyleFactory

from . import APP_ID, APP_NAME, VERSION, i18n, icons, util
from .settings import settings

SOCKET = f"linexplorer-{os.getuid()}"


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="linexplorer", description=f"{APP_NAME} {VERSION}")
    p.add_argument("location", nargs="*", help="folder, file (selected), URI or shell: location")
    p.add_argument("--select", "-s", help="open the containing folder and select this item")
    p.add_argument("--new-window", "-n", action="store_true", help="do not reuse a running instance")
    p.add_argument("--shot", help=argparse.SUPPRESS)
    p.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    a, unknown = p.parse_known_args(argv)
    # Windows-style '/select,path' and '/e,path'
    rest = []
    for x in a.location + unknown:
        low = x.lower()
        if low.startswith("/select,"):
            a.select = x.split(",", 1)[1]
        elif low.startswith("/e,") or low.startswith("/n,") or low.startswith("/root,"):
            rest.append(x.split(",", 1)[1])
        elif x in ("/e", "/n"):
            continue
        else:
            rest.append(x)
    a.location = rest
    return a


def resolve(arg: str) -> tuple[str, str | None]:
    """(location, select)."""
    from . import locations
    if arg.startswith("file://"):
        arg = QUrl(arg).toLocalFile()
    loc = locations.normalize(arg, os.getcwd()) or arg
    if loc.startswith("/") and os.path.isfile(loc):
        return os.path.dirname(loc), loc
    return loc, None


def apply_theme(app: QApplication):
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    win = QColor(240, 240, 240)
    pal.setColor(QPalette.ColorRole.Window, win)
    pal.setColor(QPalette.ColorRole.WindowText, QColor(0, 0, 0))
    pal.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(245, 247, 250))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(87, 87, 87))
    pal.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
    pal.setColor(QPalette.ColorRole.Button, QColor(236, 236, 236))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(0, 0, 0))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(51, 153, 255))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ColorRole.Link, QColor(0, 102, 204))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(120, 120, 120))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(150, 150, 150))
    app.setPalette(pal)
    f = app.font()
    if f.pointSizeF() > 10:
        f.setPointSizeF(9.5)
        app.setFont(f)
    app.setStyleSheet("""
        QToolTip { border: 1px solid #767676; padding: 2px; background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                   stop:0 #ffffff, stop:1 #e4e5f0); color: #575757; }
        QMenu { background: #f5f5f5; border: 1px solid #979797; padding: 2px; }
        QMenu::item { padding: 4px 26px 4px 30px; border: 1px solid transparent; }
        QMenu::item:selected { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f2f8fd, stop:1 #dcecfb);
                   border: 1px solid #aecff7; border-radius: 3px; color: black; }
        QMenu::item:disabled { color: #8d8d8d; }
        QMenu::separator { height: 1px; background: #e0e0e0; margin: 3px 2px 3px 28px; }
        QMenu::icon { padding-left: 6px; }
        QMenuBar::item { padding: 3px 8px; background: transparent; }
        QMenuBar::item:selected { background: #d5e7f8; border: 1px solid #aecff7; }
        QSplitter::handle:vertical { background: #d6e5f5; }
    """)


def install_desktop_entry() -> str:
    """Write ~/.local/share/applications/linexplorer.desktop (+ icon); returns the desktop id."""
    apps = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "applications")
    os.makedirs(apps, exist_ok=True)
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        exe = f'"{appimage}"'
    else:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        exe = f'env PYTHONPATH="{here}" {sys.executable} -m linexplorer'
    src_icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "linexplorer.svg")
    icon_dir = os.path.join(os.path.dirname(apps), "icons", "hicolor", "scalable", "apps")
    os.makedirs(icon_dir, exist_ok=True)
    try:
        shutil.copyfile(src_icon, os.path.join(icon_dir, "linexplorer.svg"))
    except OSError:
        pass
    content = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
GenericName=File Manager
GenericName[el]=Διαχείριση αρχείων
Comment=Browse files and folders the Windows 7 way
Comment[el]=Περιήγηση σε αρχεία και φακέλους όπως στα Windows 7
Exec={exe} %U
Icon=linexplorer
Terminal=false
Categories=System;FileTools;FileManager;Utility;Core;
MimeType=inode/directory;application/x-gnome-saved-search;
StartupNotify=true
StartupWMClass=linexplorer
Keywords=folder;manager;explore;disk;filesystem;explorer;windows;
Actions=new-window;

[Desktop Action new-window]
Name=New Window
Name[el]=Νέο παράθυρο
Exec={exe} --new-window
"""
    path = os.path.join(apps, f"{APP_ID}.desktop")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    util.run(["update-desktop-database", apps], timeout=10)
    from . import mime
    mime.all_apps(reload=True)
    return f"{APP_ID}.desktop"


class Instance(QObject):
    def __init__(self, open_cb):
        super().__init__()
        self.server = QLocalServer(self)
        self.open_cb = open_cb
        QLocalServer.removeServer(SOCKET)
        self.server.listen(SOCKET)
        self.server.newConnection.connect(self._conn)

    def _conn(self):
        s = self.server.nextPendingConnection()
        if s is None:
            return
        s.waitForReadyRead(1500)
        data = bytes(s.readAll()).decode("utf-8", "replace")
        s.disconnectFromServer()
        try:
            msg = json.loads(data)
        except ValueError:
            return
        self.open_cb(msg.get("args", []), msg.get("cwd", os.getcwd()))


def send_to_running(args: list[str]) -> bool:
    s = QLocalSocket()
    s.connectToServer(SOCKET)
    if not s.waitForConnected(400):
        return False
    s.write(json.dumps({"args": args, "cwd": os.getcwd()}).encode())
    s.flush()
    s.waitForBytesWritten(1000)
    s.disconnectFromServer()
    return True


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    a = parse_args(argv)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([sys.argv[0]] + [])
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setDesktopFileName(APP_ID)
    app.setQuitOnLastWindowClosed(True)
    if not a.new_window and not a.shot and send_to_running(argv):
        return 0
    i18n.init(settings().get("language"))
    if i18n.lang_code() == "el":
        from .qt_el import GreekQt
        app._qt_el = GreekQt(app)
        app.installTranslator(app._qt_el)
    icons.setup_theme()
    apply_theme(app)
    app.setWindowIcon(icons.app_icon())
    from . import ziputil
    ziputil.cleanup_tmp()
    from .window import MainWindow

    def open_from(args_list, cwd):
        try:
            os.chdir(cwd)
        except OSError:
            pass
        aa = parse_args(args_list)
        opened = []
        targets = aa.location or ([] if aa.select else [None])
        if aa.select:
            sel = os.path.abspath(aa.select)
            w = MainWindow(os.path.dirname(sel), sel)
            w.show()
            opened.append(w)
        for t in targets:
            if t is None:
                loc, sel = None, None
            else:
                loc, sel = resolve(t)
            w = MainWindow(loc, sel)
            w.show()
            opened.append(w)
        for w in opened:
            w.raise_()
            w.activateWindow()
        return opened

    Instance(open_from) if not a.shot else None
    from .dialogs.mapdrive import reconnect_mapped
    reconnect_mapped()
    restore = settings().get("restore_windows") and not a.location and not a.select
    wins = []
    if restore:
        from . import locations
        for entry in settings().get_json("open_windows", [])[:8]:
            tabs = entry.get("tabs", []) if isinstance(entry, dict) else [entry]
            tabs = [l for l in tabs if isinstance(l, str) and locations.exists(l)][:20]
            if tabs:
                w = MainWindow(tabs)
                if isinstance(entry, dict):
                    w.set_current_tab(int(entry.get("current", 0) or 0))
                w.show()
                wins.append(w)
    if not wins:
        wins = open_from(argv, os.getcwd())
    if a.shot:
        w = wins[0]
        QTimer.singleShot(2500, lambda: (w.grab().save(a.shot), app.quit()))
    return app.exec()
