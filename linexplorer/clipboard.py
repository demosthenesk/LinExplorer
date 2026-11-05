"""Cut / Copy / Paste through the desktop clipboard (Nautilus/Dolphin compatible)."""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QObject, QUrl, Signal
from PySide6.QtWidgets import QApplication

GNOME = "x-special/gnome-copied-files"
KDE_CUT = "application/x-kde-cutselection"
OURS = "application/x-linexplorer-zip-members"


class ClipWatch(QObject):
    changed = Signal()


watch = ClipWatch()


def make_mime(paths: list[str], cut: bool = False, zip_members: list[tuple[str, str, bool]] | None = None) -> QMimeData:
    md = QMimeData()
    urls = [QUrl.fromLocalFile(p) for p in paths]
    md.setUrls(urls)
    md.setText("\n".join(paths))
    payload = ("cut" if cut else "copy") + "".join("\n" + u.toString(QUrl.ComponentFormattingOption.FullyEncoded) for u in urls)
    md.setData(GNOME, payload.encode())
    md.setData(KDE_CUT, b"1" if cut else b"0")
    if zip_members:
        import json
        md.setData(OURS, json.dumps(zip_members).encode())
    return md


def set_files(paths: list[str], cut: bool = False, zip_members=None):
    QApplication.clipboard().setMimeData(make_mime(paths, cut, zip_members))
    watch.changed.emit()


def parse(md: QMimeData) -> tuple[list[str], bool, list]:
    """(paths, cut, zip_members)."""
    import json
    paths, cut = [], False
    zipm = []
    if md is None:
        return paths, cut, zipm
    if md.hasFormat(OURS):
        try:
            zipm = json.loads(bytes(md.data(OURS)).decode())
        except ValueError:
            zipm = []
    if md.hasFormat(GNOME):
        lines = bytes(md.data(GNOME)).decode("utf-8", "replace").splitlines()
        if lines:
            cut = lines[0].strip() == "cut"
            for ln in lines[1:]:
                u = QUrl(ln.strip())
                if u.isLocalFile():
                    paths.append(u.toLocalFile())
    if not paths and md.hasUrls():
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        if md.hasFormat(KDE_CUT):
            cut = bytes(md.data(KDE_CUT)).strip() == b"1"
    if not paths and not zipm and md.hasText():
        import os
        for ln in md.text().splitlines():
            ln = ln.strip()
            if ln.startswith("file://"):
                ln = QUrl(ln).toLocalFile()
            if ln.startswith("/") and os.path.exists(ln):
                paths.append(ln)
    return paths, cut, zipm


def get_files() -> tuple[list[str], bool, list]:
    return parse(QApplication.clipboard().mimeData())


def has_files() -> bool:
    p, _, z = get_files()
    return bool(p or z)


def clear_if_cut(paths: list[str]):
    cur, cut, _ = get_files()
    if cut and set(cur) & set(paths):
        QApplication.clipboard().clear()
        watch.changed.emit()
