"""Icons: file-type icons come from the desktop's freedesktop icon theme; the few
UI glyphs Explorer needs (view menu, panes, help, back/forward orbs) are drawn here,
so no Microsoft artwork is used."""
from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap, QRadialGradient, QPolygonF)
from PySide6.QtWidgets import QApplication, QFileIconProvider, QStyle

from . import util

_cache: dict[str, QIcon] = {}
_provider: QFileIconProvider | None = None


def setup_theme():
    """Make sure an icon theme is active even without a GTK platform theme."""
    name = QIcon.themeName()
    if not name or name == "hicolor":
        rc, out, _ = util.run(["gsettings", "get", "org.gnome.desktop.interface", "icon-theme"], timeout=3)
        cand = out.strip().strip("'") if rc == 0 else ""
        for t in [cand, "Yaru", "Adwaita", "breeze", "Papirus", "elementary", "Humanity", "gnome"]:
            if t and any(os.path.isdir(os.path.join(p, t)) for p in QIcon.themeSearchPaths() + ["/usr/share/icons"]):
                QIcon.setThemeName(t)
                break
    paths = QIcon.themeSearchPaths()
    for p in ["/usr/share/icons", os.path.expanduser("~/.local/share/icons"), os.path.expanduser("~/.icons")]:
        if p not in paths and os.path.isdir(p):
            paths.append(p)
    QIcon.setThemeSearchPaths(paths)
    QIcon.setFallbackThemeName("Adwaita" if QIcon.themeName() != "Adwaita" else "hicolor")
    fb = QIcon.fallbackSearchPaths()
    for p in ["/usr/share/pixmaps"]:
        if p not in fb:
            fb.append(p)
    QIcon.setFallbackSearchPaths(fb)


def theme(*names: str, fallback: str = "file") -> QIcon:
    key = "|".join(names) + "#" + fallback
    ic = _cache.get(key)
    if ic is not None:
        return ic
    ic = QIcon()
    for n in names:
        if not n:
            continue
        if os.path.isabs(n) and os.path.exists(n):
            ic = QIcon(n)
            break
        if QIcon.hasThemeIcon(n):
            cand = QIcon.fromTheme(n)
            if _renders_badly(cand):
                continue
            ic = cand
            break
    if ic.isNull() and any(x and any(k in x for k in _ARCHIVE_HINTS) for x in names):
        ic = archive_icon()
    if ic.isNull():
        ic = _std(fallback)
    _cache[key] = ic
    return ic


_ARCHIVE_HINTS = ("package", "zip", "tar", "compress", "archive", "bzip", "gzip", "xz", "rar", "7z")


def _renders_badly(ic: QIcon) -> bool:
    """Newer Adwaita SVGs use masks/filters that QtSvg cannot render: they come out with an opaque
    black square around the artwork. Detect that by looking at a corner pixel of a large rendering."""
    try:
        im = ic.pixmap(64).toImage()
    except Exception:
        return False
    if im.isNull() or im.width() < 8:
        return False
    for x, y in ((1, 1), (im.width() - 2, 1), (1, im.height() - 2)):
        c = im.pixelColor(x, y)
        if not (c.alpha() > 250 and c.red() < 8 and c.green() < 8 and c.blue() < 8):
            return False
    return True


_archive_icon = None


def archive_icon() -> QIcon:
    """Drawn compressed-file icon (page with a zipper) used when the theme's one is unusable."""
    global _archive_icon
    if _archive_icon is not None:
        return _archive_icon
    ic = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm, p = _pm(size)
        s = size / 32.0
        page = QPainterPath()
        page.moveTo(6 * s, 2 * s)
        page.lineTo(20 * s, 2 * s)
        page.lineTo(27 * s, 9 * s)
        page.lineTo(27 * s, 30 * s)
        page.lineTo(6 * s, 30 * s)
        page.closeSubpath()
        g = QLinearGradient(0, 0, 0, size)
        g.setColorAt(0, QColor(244, 236, 220))
        g.setColorAt(1, QColor(222, 206, 178))
        p.setPen(QPen(QColor(150, 125, 90), max(1.0, s)))
        p.setBrush(QBrush(g))
        p.drawPath(page)
        p.setBrush(QColor(205, 186, 152))
        p.drawPolygon(QPolygonF([QPointF(20 * s, 2 * s), QPointF(20 * s, 9 * s), QPointF(27 * s, 9 * s)]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(90, 90, 96))
        y = 3 * s
        k = 0
        while y < 19 * s:
            x = 14 * s if k % 2 == 0 else 16 * s
            p.drawRect(QRectF(x, y, 2 * s, 1.6 * s))
            y += 2 * s
            k += 1
        p.setBrush(QColor(70, 70, 76))
        p.drawRoundedRect(QRectF(13 * s, 19 * s, 6 * s, 7 * s), 1.2 * s, 1.2 * s)
        p.setBrush(QColor(230, 230, 232))
        p.drawRect(QRectF(14.5 * s, 22 * s, 3 * s, 1.5 * s))
        p.end()
        ic.addPixmap(pm)
    _archive_icon = ic
    return ic


def _std(kind: str) -> QIcon:
    global _provider
    if _provider is None:
        _provider = QFileIconProvider()
    st = QApplication.style()
    m = {
        "folder": lambda: _provider.icon(QFileIconProvider.IconType.Folder),
        "file": lambda: _provider.icon(QFileIconProvider.IconType.File),
        "drive": lambda: _provider.icon(QFileIconProvider.IconType.Drive),
        "computer": lambda: _provider.icon(QFileIconProvider.IconType.Computer),
        "network": lambda: _provider.icon(QFileIconProvider.IconType.Network),
        "trash": lambda: _provider.icon(QFileIconProvider.IconType.Trashcan),
        "desktop": lambda: _provider.icon(QFileIconProvider.IconType.Desktop),
    }
    f = m.get(kind)
    ic = f() if f else st.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
    return ic


# --------------------------------------------------------------------------- semantic icons
def folder(kind: str = "") -> QIcon:
    names = {
        "documents": ("folder-documents",),
        "music": ("folder-music",),
        "pictures": ("folder-pictures", "folder-images"),
        "videos": ("folder-videos", "folder-video"),
        "downloads": ("folder-download", "folder-downloads"),
        "desktop": ("user-desktop", "folder-desktop"),
        "home": ("user-home", "folder-home"),
        "templates": ("folder-templates",),
        "public": ("folder-publicshare", "folder-public"),
        "remote": ("folder-remote", "folder-network"),
        "recent": ("document-open-recent", "folder-recent"),
        "search": ("folder-saved-search", "system-search", "edit-find"),
        "open": ("folder-open",),
    }.get(kind, ())
    return theme(*names, "folder", fallback="folder")


def computer() -> QIcon:
    return theme("computer", "computer-laptop", "user-desktop", fallback="computer")


def network() -> QIcon:
    return theme("network-workgroup", "network-wired", "network-server", fallback="network")


def net_proto(p: str) -> QIcon:
    if p == "smb":
        return theme("network-workgroup", "folder-remote", "network-server", fallback="network")
    if p == "ftp":
        return theme("folder-remote", "network-server", fallback="network")
    if p == "sftp":
        return theme("network-server", "utilities-terminal", "folder-remote", fallback="network")
    return theme("folder-remote", "network-server", fallback="network")


def network_server() -> QIcon:
    return theme("network-server", "computer", fallback="network")


def trash(full: bool) -> QIcon:
    return theme("user-trash-full" if full else "user-trash", "user-trash", fallback="trash")


def library(kind: str = "") -> QIcon:
    if kind in ("documents", "music", "pictures", "videos"):
        return folder(kind)
    return theme("folder-saved-search", "folder-documents", "folder", fallback="folder")


def libraries() -> QIcon:
    return theme("folder-saved-search", "folder-documents", "folder", fallback="folder")


def favorites() -> QIcon:
    ic = _cache.get("__favorites__")
    if ic is None:
        ic = QIcon()
        for s in (16, 24, 32, 48):
            ic.addPixmap(glyph_star(True, s))
        _cache["__favorites__"] = ic
    return ic


def drive(kind: str) -> QIcon:
    names = {
        "root": ("drive-harddisk-system", "drive-harddisk"),
        "hdd": ("drive-harddisk",),
        "ssd": ("drive-harddisk-solidstate", "drive-harddisk"),
        "usb": ("drive-removable-media-usb", "drive-removable-media", "media-removable"),
        "removable": ("drive-removable-media", "media-removable"),
        "flash": ("media-flash", "drive-removable-media"),
        "optical": ("drive-optical", "media-optical"),
        "network": ("folder-remote", "network-server", "drive-harddisk"),
    }.get(kind, ("drive-harddisk",))
    return theme(*names, fallback="drive")


def action(name: str) -> QIcon:
    key = "action:" + name
    ic = _cache.get(key)
    if ic is None:
        ic = QIcon()
        for n in (name, name + "-symbolic"):
            if QIcon.hasThemeIcon(n):
                ic = QIcon.fromTheme(n)
                break
        _cache[key] = ic
    return ic


_mime_cache: dict[str, QIcon] = {}


def for_mime(mt) -> QIcon:
    name = mt.name()
    ic = _mime_cache.get(name)
    if ic is not None:
        return ic
    cands = [mt.iconName(), mt.genericIconName()]
    if name == "inode/directory":
        cands = ["folder"]
    elif name.startswith("text/"):
        cands.append("text-x-generic")
    elif name.startswith("image/"):
        cands.append("image-x-generic")
    elif name.startswith("audio/"):
        cands.append("audio-x-generic")
    elif name.startswith("video/"):
        cands.append("video-x-generic")
    if any(k in name for k in _ARCHIVE_HINTS[1:]) or mt.genericIconName() == "package-x-generic":
        cands = [c for c in cands if c] + ["package-x-generic"]
        ic = theme(*cands, fallback="file")
        _mime_cache[name] = ic
        return ic
    cands += ["unknown", "text-x-generic", "application-x-generic"]
    ic = theme(*cands, fallback="file")
    _mime_cache[name] = ic
    return ic


# --------------------------------------------------------------------------- overlays
def with_link_overlay(pm: QPixmap) -> QPixmap:
    """Shortcut arrow overlay drawn in the lower-left corner."""
    out = QPixmap(pm)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = max(9, int(out.width() / out.devicePixelRatio() * 0.38))
    r = QRectF(0, out.height() / out.devicePixelRatio() - s, s, s)
    p.setPen(QPen(QColor(90, 90, 90), 1))
    p.setBrush(QColor(255, 255, 255))
    p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
    p.setPen(QPen(QColor(30, 80, 170), max(1.2, s / 8), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    path = QPainterPath()
    path.moveTo(r.left() + s * 0.25, r.bottom() - s * 0.2)
    path.cubicTo(r.left() + s * 0.25, r.top() + s * 0.45, r.left() + s * 0.45, r.top() + s * 0.32,
                 r.right() - s * 0.25, r.top() + s * 0.3)
    p.drawPath(path)
    tip = QPolygonF([QPointF(r.right() - s * 0.18, r.top() + s * 0.3),
                     QPointF(r.right() - s * 0.42, r.top() + s * 0.12),
                     QPointF(r.right() - s * 0.42, r.top() + s * 0.5)])
    p.setBrush(QColor(30, 80, 170))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon(tip)
    p.end()
    return out


# --------------------------------------------------------------------------- drawn UI glyphs
def _pm(size: int) -> tuple[QPixmap, QPainter]:
    dpr = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pm, p


def glyph_views(size: int = 16) -> QIcon:
    pm, p = _pm(size)
    c1, c2 = QColor(60, 110, 180), QColor(140, 180, 230)
    for i, (x, y) in enumerate([(1, 1), (9, 1), (1, 9), (9, 9)]):
        g = QLinearGradient(x, y, x + 6, y + 6)
        g.setColorAt(0, c2)
        g.setColorAt(1, c1)
        p.setBrush(g)
        p.setPen(QPen(QColor(50, 80, 130), 0.8))
        p.drawRoundedRect(QRectF(x, y, 6, 6), 1, 1)
    p.end()
    return QIcon(pm)


def glyph_preview(size: int = 16) -> QIcon:
    pm, p = _pm(size)
    p.setPen(QPen(QColor(70, 90, 120), 1))
    p.setBrush(QColor(255, 255, 255))
    p.drawRect(QRectF(1.5, 2.5, 13, 11))
    p.setBrush(QColor(120, 170, 230))
    p.drawRect(QRectF(8.5, 2.5, 6, 11))
    p.setPen(QPen(QColor(255, 255, 255), 1))
    for y in (5.5, 8, 10.5):
        p.drawLine(QPointF(9.8, y), QPointF(13.2, y))
    p.end()
    return QIcon(pm)


def glyph_help(size: int = 16) -> QIcon:
    pm, p = _pm(size)
    g = QRadialGradient(QPointF(6, 5), 9)
    g.setColorAt(0, QColor(130, 185, 245))
    g.setColorAt(1, QColor(20, 85, 175))
    p.setBrush(g)
    p.setPen(QPen(QColor(15, 60, 130), 0.8))
    p.drawEllipse(QRectF(1, 1, 14, 14))
    f = p.font()
    f.setBold(True)
    f.setPixelSize(11)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255))
    p.drawText(QRectF(1, 1, 14, 14), Qt.AlignmentFlag.AlignCenter, "?")
    p.end()
    return QIcon(pm)


def glyph_search(size: int = 14, color: QColor | None = None) -> QIcon:
    pm, p = _pm(size)
    c = color or QColor(110, 120, 135)
    p.setPen(QPen(c, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(1.5, 1.5, size * 0.55, size * 0.55))
    p.setPen(QPen(c, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(size * 0.62, size * 0.62), QPointF(size - 1.5, size - 1.5))
    p.end()
    return QIcon(pm)


def glyph_refresh(size: int = 16) -> QIcon:
    pm, p = _pm(size)
    c = QColor(60, 120, 60)
    p.setPen(QPen(c, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(QRectF(3, 3, 10, 10), 30 * 16, 280 * 16)
    tip = QPolygonF([QPointF(13.5, 2.5), QPointF(13.5, 7.5), QPointF(9, 5.5)])
    p.setBrush(c)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon(tip)
    p.end()
    return QIcon(pm)


def glyph_go(size: int = 16) -> QIcon:
    pm, p = _pm(size)
    c = QColor(40, 110, 40)
    p.setPen(QPen(c, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(3, 8), QPointF(12, 8))
    p.drawLine(QPointF(8.5, 4.5), QPointF(12, 8))
    p.drawLine(QPointF(8.5, 11.5), QPointF(12, 8))
    p.end()
    return QIcon(pm)


def glyph_close(size: int = 12) -> QIcon:
    pm, p = _pm(size)
    p.setPen(QPen(QColor(90, 90, 90), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(3, 3), QPointF(size - 3, size - 3))
    p.drawLine(QPointF(size - 3, 3), QPointF(3, size - 3))
    p.end()
    return QIcon(pm)


def glyph_star(filled: bool, size: int = 14) -> QPixmap:
    import math
    pm, p = _pm(size)
    pts = []
    cx = cy = size / 2
    for i in range(10):
        r = size * 0.46 if i % 2 == 0 else size * 0.2
        a = -math.pi / 2 + i * math.pi / 5
        pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a) + 0.5))
    if filled:
        g = QLinearGradient(0, 0, 0, size)
        g.setColorAt(0, QColor(255, 220, 90))
        g.setColorAt(1, QColor(240, 160, 20))
        p.setBrush(g)
        p.setPen(QPen(QColor(190, 120, 10), 0.8))
    else:
        p.setBrush(QColor(235, 238, 242))
        p.setPen(QPen(QColor(170, 175, 185), 0.8))
    p.drawPolygon(QPolygonF(pts))
    p.end()
    return pm


def app_icon() -> QIcon:
    here = os.path.dirname(os.path.abspath(__file__))
    svg = os.path.join(here, "resources", "linexplorer.svg")
    if os.path.exists(svg):
        return QIcon(svg)
    return folder()
