"""Map Network Drive / Disconnect Network Drive (GIO/gvfs mounts)."""
from __future__ import annotations

import os
import subprocess
import urllib.parse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton,
                               QVBoxLayout)

from .. import icons, locations, places, util
from ..i18n import T
from ..settings import settings


def to_uri(text: str) -> str:
    t = text.strip()
    if t.startswith("\\\\"):
        parts = [p for p in t[2:].split("\\") if p]
        return "smb://" + "/".join(urllib.parse.quote(p) for p in parts) + "/"
    if "://" not in t and t:
        return "smb://" + t.strip("/") + "/"
    return t


class CredentialsDialog(QDialog):
    def __init__(self, parent, uri: str, msg: str = ""):
        super().__init__(parent)
        from .. import netloc
        proto = netloc.proto_of_uri(uri)
        self.setWindowTitle(T("enter_credentials"))
        v = QVBoxLayout(self)
        lab = QLabel(T("credentials_text", uri) + (("\n\n" + msg) if msg else ""))
        lab.setWordWrap(True)
        v.addWidget(lab)
        self.anon = QRadioButton(T("connect_anonymously"))
        self.reg = QRadioButton(T("registered_user"))
        self.reg.setChecked(True)
        if proto in ("smb", "ftp"):
            v.addWidget(self.anon)
            v.addWidget(self.reg)
        f = QFormLayout()
        pr = urllib.parse.urlparse(uri)
        self.user = QLineEdit(urllib.parse.unquote(pr.username) if pr.username else os.environ.get("USER", ""))
        self.domain = QLineEdit("WORKGROUP")
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        f.addRow(T("user_name"), self.user)
        if proto == "smb":
            f.addRow(T("domain"), self.domain)
        f.addRow(T("password"), self.pw)
        v.addLayout(f)
        self.anon.toggled.connect(lambda a: [w.setEnabled(not a) for w in (self.user, self.domain, self.pw)])
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.pw.setFocus()


def _ask_question(parent, text: str, choices: list[str]):
    box = QMessageBox(parent)
    box.setWindowTitle(T("map_drive_title"))
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(text or T("question"))
    buttons = [box.addButton(c, QMessageBox.ButtonRole.AcceptRole if i == 0 else QMessageBox.ButtonRole.RejectRole)
               for i, c in enumerate(choices)]
    if not buttons:
        return 0
    box.exec()
    clicked = box.clickedButton()
    return buttons.index(clicked) if clicked in buttons else None


def gio_mount(parent, uri: str, user: str | None = None, domain: str | None = None, pw: str | None = None,
              anonymous: bool = False, ask: bool = True) -> str:
    """Mount a network location, asking for credentials or certificate/host-key confirmation when needed.

    Returns the address that was actually mounted ('' on failure). A plain ftp:// login that the server
    rejects is retried once as ftps:// (explicit FTP over TLS), because servers that require TLS answer
    a plain login with '530' which gvfs reports exactly like a wrong password."""
    from .. import netloc
    from PySide6.QtWidgets import QApplication
    if not util.which("gio"):
        QMessageBox.warning(parent, T("map_drive_title"), T("not_installed", "gio (gvfs)"))
        return ""

    def attempt(u, user, domain, pw, anonymous):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return netloc.gio_mount(u, user, domain, pw, anonymous,
                                    ask=lambda q, c: (QApplication.restoreOverrideCursor(),
                                                      _ask_question(parent, q, c),
                                                      QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor))[1],
                                    pump=QApplication.processEvents)
        finally:
            QApplication.restoreOverrideCursor()

    tried_tls = False
    while True:
        status, text = attempt(uri, user, domain, pw, anonymous)
        if status == "need_creds" and netloc.scheme_of(uri) == "ftp" and not tried_tls and (pw is not None or anonymous):
            tried_tls = True
            tls = netloc.with_scheme(uri, "ftps")
            st2, text2 = attempt(tls, user, domain, pw, anonymous)
            if st2 == "ok":
                uri, status, text = tls, st2, text2
            elif st2 == "cancelled":
                return ""
        if status == "ok":
            places.drives().refresh(sync=True)
            from .. import ftpclient
            if ftpclient.is_ftp_uri(uri):
                ftpclient.remember(uri, user, pw, anonymous)
            return uri
        if status == "cancelled":
            return ""
        if status == "need_creds" and ask:
            d = CredentialsDialog(parent, uri, T("wrong_password") if (pw is not None or anonymous) else "")
            if not d.exec():
                return ""
            user, domain, pw, anonymous = d.user.text(), d.domain.text(), d.pw.text(), d.anon.isChecked()
            continue
        QMessageBox.warning(parent, T("map_drive_title"), T("mount_failed", uri) + "\n\n" + _clean_gio(text)[-600:])
        return ""


def _clean_gio(text: str) -> str:
    return "\n".join(l for l in text.strip().splitlines()
                     if l.strip() and "CRITICAL" not in l and "WARNING" not in l and not l.startswith("(process:"))


class AddNetLocationDialog(QDialog):
    """'Add a network location': SAMBA / FTP / SFTP / WebDAV."""

    def __init__(self, parent, proto: str = "smb"):
        super().__init__(parent)
        from PySide6.QtWidgets import QSpinBox
        self.setWindowTitle(T("add_net_location_title"))
        self.resize(520, 380)
        v = QVBoxLayout(self)
        h = QLabel(T("add_net_location_heading"))
        f = h.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        h.setFont(f)
        h.setStyleSheet("color: #1e3287;")
        v.addWidget(h)
        form = QFormLayout()
        self.proto = QComboBox()
        for key in ("smb", "ftp", "ftps", "ftpis", "sftp", "dav"):
            self.proto.addItem(T("net_proto_" + key), key)
        self.proto.setCurrentIndex(max(0, self.proto.findData(proto if proto in ("smb", "ftp", "ftps", "ftpis", "sftp", "dav")
                                                                  else "smb")))
        form.addRow(T("net_type"), self.proto)
        self.host = QLineEdit()
        self.host.setPlaceholderText("192.168.1.10, nas.local, ftp.example.com")
        form.addRow(T("net_server"), self.host)
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        form.addRow(T("net_port"), self.port)
        self.path = QLineEdit()
        form.addRow(T("net_folder"), self.path)
        self.user = QLineEdit()
        form.addRow(T("user_name"), self.user)
        self.anon = QCheckBox(T("connect_anonymously"))
        form.addRow("", self.anon)
        self.name = QLineEdit()
        form.addRow(T("net_location_name"), self.name)
        v.addLayout(form)
        self.preview = QLabel()
        self.preview.setStyleSheet("color: #6d6d6d;")
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.preview)
        self.connect_cb = QCheckBox(T("net_connect_now"))
        self.connect_cb.setChecked(True)
        v.addWidget(self.connect_cb)
        v.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._name_edited = False
        self.name.textEdited.connect(lambda _t: setattr(self, "_name_edited", True))
        self.proto.currentIndexChanged.connect(self._proto_changed)
        for w in (self.host, self.path, self.user):
            w.textChanged.connect(self._upd)
        self.port.valueChanged.connect(self._upd)
        self.anon.toggled.connect(self._upd)
        self._proto_changed()

    def _proto_changed(self):
        from .. import netloc
        p = self.proto.currentData()
        self.port.setValue(netloc.DEFAULT_PORT.get(p, 0))
        self.path.setPlaceholderText(T("net_share_hint") if p == "smb" else T("net_path_hint"))
        self.anon.setVisible(p in ("smb", "ftp", "ftps", "ftpis"))
        self._upd()

    def _uri(self) -> str:
        from .. import netloc
        p = self.proto.currentData()
        user = "" if (self.anon.isChecked() and p in ("smb", "ftp", "ftps", "ftpis")) else self.user.text().strip()
        return netloc.build_uri(p, self.host.text(), self.port.value() or None, self.path.text(), user)

    def _upd(self):
        uri = self._uri() if self.host.text().strip() else ""
        self.preview.setText(uri)
        if not self._name_edited:
            share = self.path.text().strip().strip("/").split("/")[-1] if self.path.text().strip() else ""
            host = self.host.text().strip()
            self.name.setText(T("net_name_on", share, host) if share and host else host)

    def _ok(self):
        if not self.host.text().strip():
            self.host.setFocus()
            return
        self.uri_value = self._uri()
        self.name_value = self.name.text().strip() or self.host.text().strip()
        self.connect_now = self.connect_cb.isChecked()
        self.anonymous = self.anon.isChecked() and self.proto.currentData() in ("smb", "ftp", "ftps", "ftpis")
        self.accept()


class MapDriveDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.local_path = None
        self.setWindowTitle(T("map_drive_title"))
        self.resize(560, 300)
        v = QVBoxLayout(self)
        h = QLabel(T("map_drive_heading"))
        f = h.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        h.setFont(f)
        h.setStyleSheet("color: #1e3287;")
        v.addWidget(h)
        v.addWidget(QLabel(T("map_drive_text")))
        form = QFormLayout()
        self.folder = QComboBox()
        self.folder.setEditable(True)
        for m in settings().get_json("mapped_drives", []):
            self.folder.addItem(m.get("uri", ""))
        self.folder.setCurrentText("")
        form.addRow(T("folder_label"), self.folder)
        v.addLayout(form)
        ex = QLabel(T("map_drive_example"))
        ex.setStyleSheet("color: #6d6d6d;")
        v.addWidget(ex)
        self.reconnect = QCheckBox(T("reconnect_at_signin"))
        self.reconnect.setChecked(True)
        v.addWidget(self.reconnect)
        self.creds = QCheckBox(T("different_credentials"))
        v.addWidget(self.creds)
        v.addStretch(1)
        bh = QHBoxLayout()
        bh.addStretch(1)
        ok = QPushButton(T("finish_btn"))
        ok.setDefault(True)
        ok.clicked.connect(self._ok)
        c = QPushButton(T("cancel"))
        c.clicked.connect(self.reject)
        bh.addWidget(ok)
        bh.addWidget(c)
        v.addLayout(bh)

    def _ok(self):
        uri = to_uri(self.folder.currentText())
        if not uri:
            return
        ok = False
        if self.creds.isChecked():
            d = CredentialsDialog(self, uri)
            if not d.exec():
                return
            eff = gio_mount(self, uri, d.user.text(), d.domain.text(), d.pw.text(), d.anon.isChecked())
        else:
            eff = gio_mount(self, uri)
        if not eff:
            return
        uri = eff
        if self.reconnect.isChecked():
            lst = [m for m in settings().get_json("mapped_drives", []) if m.get("uri") != uri]
            lst.append({"uri": uri, "reconnect": True})
            settings().set_json("mapped_drives", lst)
        from .. import netloc
        if not any(netloc.normalize_uri(l["uri"]) == netloc.normalize_uri(uri) for l in netloc.saved()):
            netloc.save_location(netloc.proto_title(netloc.proto_of_uri(uri)) + ": " + uri.split("://", 1)[-1].strip("/"), uri)
        self.local_path = netloc.mount_for(uri) or locations.gio_local_path(uri)
        self.accept()


class DisconnectDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(T("disconnect_title"))
        self.resize(460, 300)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(T("disconnect_text")))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        for d in places.drives().list():
            if d.get("kind") == "network" and d.get("mount"):
                it = QListWidgetItem(icons.drive("network"), d.get("label") or d["mount"])
                it.setData(Qt.ItemDataRole.UserRole, d["mount"])
                self.list.addItem(it)
        v.addWidget(self.list, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _ok(self):
        targets = [it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems()]
        self.accept()
        p = self.parent()
        for m in targets:
            if p is not None and hasattr(p, "disconnect_location"):
                p.disconnect_location(m)
            else:
                from .. import netloc
                ok, msg = netloc.gio_unmount(m)
                if not ok and msg:
                    QMessageBox.warning(self, T("disconnect_title"), msg)


def reconnect_mapped():
    """At start-up: remount drives marked 'Reconnect at sign-in' (non-interactive)."""
    for m in settings().get_json("mapped_drives", []):
        if m.get("reconnect"):
            util.in_thread(util.run, ["gio", "mount", m["uri"]], 30, "")
