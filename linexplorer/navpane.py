"""Navigation pane: Favorites, Libraries, Computer, Network (and 'Show all folders')."""
from __future__ import annotations

import os

from PySide6.QtCore import QModelIndex, QMimeData, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QDrag, QFont, QIcon, QLinearGradient, QPainter, QPen, QPolygonF,
                           QStandardItem, QStandardItemModel)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QTreeView)

from . import icons, locations, places, trash, util
from .i18n import T
from .settings import settings

LocRole = Qt.ItemDataRole.UserRole + 10
KindRole = Qt.ItemDataRole.UserRole + 11       # header, favorite, library, drive, folder, spacer, net
DoneRole = Qt.ItemDataRole.UserRole + 12


class _Delegate(QStyledItemDelegate):
    def __init__(self, tree):
        super().__init__(tree)
        self.tree = tree

    def sizeHint(self, opt, idx):
        if idx.data(KindRole) == "spacer":
            return QSize(10, 12)
        return QSize(10, max(22, opt.fontMetrics.height() + 6))

    def paint(self, p: QPainter, opt: QStyleOptionViewItem, idx):
        kind = idx.data(KindRole)
        if kind == "spacer":
            return
        r = QRect(opt.rect)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        hot = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        drop = self.tree._drop_index.isValid() and self.tree._drop_index == idx
        icon: QIcon = idx.data(Qt.ItemDataRole.DecorationRole)
        text = idx.data(Qt.ItemDataRole.DisplayRole) or ""
        fm = opt.fontMetrics
        tw = fm.horizontalAdvance(text)
        bg = QRect(r.left(), r.top() + 1, min(r.width() - 2, 24 + tw + 10), r.height() - 2)
        if sel or hot or drop:
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            g = QLinearGradient(QPointF(bg.topLeft()), QPointF(bg.bottomLeft()))
            if sel and self.tree.hasFocus() or drop:
                g.setColorAt(0, QColor(220, 235, 252))
                g.setColorAt(1, QColor(193, 219, 252))
                pen = QColor(125, 162, 206)
            elif sel:
                g.setColorAt(0, QColor(248, 248, 248))
                g.setColorAt(1, QColor(229, 229, 229))
                pen = QColor(217, 217, 217)
            else:
                g.setColorAt(0, QColor(250, 253, 255))
                g.setColorAt(1, QColor(229, 243, 251))
                pen = QColor(184, 214, 251)
            p.setBrush(g)
            p.setPen(QPen(pen, 1))
            p.drawRoundedRect(QRectF(bg).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
            p.restore()
        if icon is not None:
            icon.paint(p, QRect(r.left() + 3, r.center().y() - 8, 16, 16))
        p.setPen(QColor(0, 0, 0) if kind != "header" else QColor(30, 57, 91))
        f = QFont(opt.font)
        p.setFont(f)
        p.drawText(QRect(r.left() + 24, r.top(), r.width() - 26, r.height()), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(text, Qt.TextElideMode.ElideRight, r.width() - 28))


class NavPane(QTreeView):
    navigate = Signal(str)
    open_new_window = Signal(str)
    open_new_tab = Signal(str)
    context_requested = Signal(str, str, QPoint)       # location, kind, global pos
    drop_requested = Signal(object, str, object, bool)  # mime, target location, action, right

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = QStandardItemModel(self)
        self.setModel(self.model_)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setIndentation(14)
        self.setUniformRowHeights(False)
        self.setAnimated(False)
        self.setMouseTracking(True)
        self.setExpandsOnDoubleClick(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setFrameShape(QTreeView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setItemDelegate(_Delegate(self))
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx)
        self.expanded.connect(self._on_expand)
        self.clicked.connect(self._on_click)
        self.setStyleSheet("NavPane { background: white; border: none; border-right: 1px solid #d6e5f5; }"
                           "QTreeView::item { border: none; }")
        pal = self.palette()
        from PySide6.QtGui import QPalette
        for grp in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            pal.setColor(grp, QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
            pal.setColor(grp, QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
        self.setPalette(pal)
        self._hovering = False
        self._always_arrows = False
        self._drop_index = QModelIndex()
        self._spring = QTimer(self)
        self._spring.setSingleShot(True)
        self._spring.setInterval(800)
        self._spring.timeout.connect(self._spring_open)
        self._current_loc = ""
        self._suppress_click = False
        places.favorites().changed.connect(self.rebuild)
        places.libraries().changed.connect(self.rebuild)
        places.drives().changed.connect(self._refresh_computer)
        places.drives().changed.connect(self._refresh_network)
        settings().changed.connect(self._on_setting)
        trash.watcher().changed.connect(self._update_trash)
        self.rebuild()

    # ------------------------------------------------------------------ building
    def _item(self, text, icon, loc, kind, lazy=False) -> QStandardItem:
        it = QStandardItem(icon, text)
        it.setData(loc, LocRole)
        it.setData(kind, KindRole)
        it.setEditable(False)
        it.setToolTip(loc if loc.startswith("/") else "")
        if lazy:
            dummy = QStandardItem("")
            dummy.setData("dummy", KindRole)
            it.appendRow(dummy)
        return it

    def _spacer(self) -> QStandardItem:
        it = QStandardItem("")
        it.setData("spacer", KindRole)
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        return it

    def rebuild(self):
        expanded = self._expanded_locs()
        self.model_.clear()
        root = self.model_.invisibleRootItem()
        fav = self._item(T("favorites"), icons.favorites(), "shell:favorites", "header")
        for f in places.favorites().items():
            loc = f["path"]
            name = f.get("name") or locations.display_name(loc)
            fav.appendRow(self._item(name, locations.icon(loc), loc, "favorite"))
        root.appendRow(fav)
        root.appendRow(self._spacer())
        if settings().get("nav_show_all_folders"):
            desk = places.xdg_dirs()["desktop"]
            d = self._item(T("desktop"), icons.folder("desktop"), desk, "header")
            d.appendRow(self._libraries_node())
            d.appendRow(self._item(places.login_name(), icons.folder("home"), places.HOME, "folder", True))
            d.appendRow(self._computer_node())
            d.appendRow(self._item(T("network"), icons.network(), "shell:network", "header", True))
            d.appendRow(self._item(T("recycle_bin"), icons.trash(not trash.is_empty()), "shell:trash", "header"))
            root.appendRow(d)
        else:
            root.appendRow(self._libraries_node())
            root.appendRow(self._spacer())
            root.appendRow(self._computer_node())
            root.appendRow(self._spacer())
            root.appendRow(self._item(T("network"), icons.network(), "shell:network", "header", True))
            if settings().get("nav_show_recycle_bin"):
                root.appendRow(self._spacer())
                root.appendRow(self._item(T("recycle_bin"), icons.trash(not trash.is_empty()), "shell:trash", "header"))
        self._update_trash()
        self.expand(self.model_.indexFromItem(fav))
        for i in range(root.rowCount()):
            ch = root.child(i)
            if ch.data(KindRole) == "header" and ch.data(LocRole) in ("shell:libraries", "shell:computer"):
                if not expanded or ch.data(LocRole) in expanded:
                    self.expand(self.model_.indexFromItem(ch))
            if settings().get("nav_show_all_folders") and ch.data(LocRole) == places.xdg_dirs()["desktop"]:
                self.expand(self.model_.indexFromItem(ch))
        for loc in expanded:
            idx = self._find(loc)
            if idx.isValid():
                self.expand(idx)
        self.select_location(self._current_loc)

    def _libraries_node(self) -> QStandardItem:
        L = places.libraries()
        n = self._item(T("libraries"), icons.libraries(), "shell:libraries", "header")
        for lib in L.all():
            if not lib.get("nav", True):
                continue
            li = self._item(L.title(lib), icons.library(lib.get("type", "")), "shell:library:" + lib["name"],
                            "library", bool(lib["locations"]))
            n.appendRow(li)
        return n

    def _computer_node(self) -> QStandardItem:
        n = self._item(T("computer"), icons.computer(), "shell:computer", "header")
        self._fill_drives(n)
        return n

    def _fill_drives(self, n: QStandardItem):
        n.removeRows(0, n.rowCount())
        hide_empty = settings().get("hide_empty_drives")
        for d in places.drives().list():
            if d.get("kind") == "network":
                continue
            if hide_empty and d.get("removable") and not d.get("size"):
                continue
            loc = d.get("mount") or ("dev:" + (d.get("dev") or ""))
            n.appendRow(self._item(places.drive_title(d), icons.drive(d.get("kind")), loc, "drive", bool(d.get("mount"))))

    def _refresh_network(self):
        idx = self._find("shell:network")
        if not idx.isValid() or not self.isExpanded(idx):
            return
        expanded = self._expanded_locs()
        item = self.model_.itemFromIndex(idx)
        item.removeRows(0, item.rowCount())
        self._populate(item)
        for loc in expanded:
            if loc and loc.startswith("shell:network:"):
                i2 = self._find(loc)
                if i2.isValid():
                    self.expand(i2)

    def _refresh_computer(self):
        idx = self._find("shell:computer")
        if idx.isValid():
            item = self.model_.itemFromIndex(idx)
            was = self.isExpanded(idx)
            self._fill_drives(item)
            if was:
                self.expand(idx)

    def _expanded_locs(self) -> set:
        out = set()

        def walk(parent: QModelIndex):
            for r in range(self.model_.rowCount(parent)):
                i = self.model_.index(r, 0, parent)
                if self.isExpanded(i):
                    out.add(i.data(LocRole))
                    walk(i)
        if self.model_.rowCount():
            walk(QModelIndex())
        return out

    def _update_trash(self):
        """Full/empty Recycle Bin icon and an 'N items, size' tooltip."""
        idx = self._find("shell:trash")
        if not idx.isValid():
            return
        it = self.model_.itemFromIndex(idx)
        entries = trash.list_entries()
        it.setIcon(icons.trash(bool(entries)))
        if entries:
            it.setToolTip(T("trash_tip", len(entries)))
        else:
            it.setToolTip(T("trash_tip_empty"))

    def _on_setting(self, key):
        if key == "nav_show_recycle_bin":
            self.rebuild()
            return
        if key in ("nav_show_all_folders", "show_hidden", "hide_empty_drives", "show_drive_letters"):
            self.rebuild()

    def _on_expand(self, idx: QModelIndex):
        item = self.model_.itemFromIndex(idx)
        if item is None or item.data(DoneRole):
            return
        if item.rowCount() == 1 and item.child(0).data(KindRole) == "dummy":
            item.removeRow(0)
            item.setData(True, DoneRole)
            self._populate(item)

    def _populate(self, item: QStandardItem):
        loc = item.data(LocRole)
        kind = item.data(KindRole)
        sh = settings().get("show_hidden")
        if kind == "library":
            lib = places.libraries().get(locations.library_name(loc))
            for p in (lib or {}).get("locations", []):
                if os.path.isdir(p):
                    item.appendRow(self._item(os.path.basename(p) or p, locations.icon(p), p, "folder",
                                              self._has_subdirs(p)))
            return
        if loc == "shell:network":
            from . import netloc
            for p in netloc.PROTOS:
                item.appendRow(self._item(netloc.proto_title(p), icons.net_proto(p), "shell:network:" + p,
                                          "netproto", True))
            if any(locations._proto_bucket(c["proto"]) == "other" for c in netloc.connections()):
                item.appendRow(self._item(netloc.proto_title("other"), icons.net_proto("other"),
                                          "shell:network:other", "netproto", True))
            return
        if loc and loc.startswith("shell:network:"):
            from . import netloc
            proto = loc.rsplit(":", 1)[1]
            seen = set()
            for c in netloc.connections():
                if locations._proto_bucket(c["proto"]) == proto:
                    item.appendRow(self._item(c["label"], icons.drive("network"), c["mount"], "folder",
                                              self._has_subdirs(c["mount"])))
                    if c["uri"]:
                        seen.add(netloc.normalize_uri(c["uri"]))
            for l in netloc.saved():
                if locations._proto_bucket(netloc.proto_of_uri(l["uri"])) == proto and \
                        netloc.normalize_uri(l["uri"]) not in seen:
                    item.appendRow(self._item(l["name"] or l["uri"], icons.folder("remote"), "net:" + l["uri"], "net"))
            return
        if loc and loc.startswith("/"):
            try:
                names = sorted(os.listdir(loc), key=util.natural_key)
            except OSError:
                return
            hidden = util.read_dot_hidden(loc)
            for n in names:
                if not sh and (util.is_hidden_name(n) or n in hidden):
                    continue
                p = os.path.join(loc, n)
                if os.path.isdir(p):
                    item.appendRow(self._item(n, locations.icon(p), p, "folder", self._has_subdirs(p)))

    @staticmethod
    def _has_subdirs(p: str) -> bool:
        try:
            with os.scandir(p) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=True):
                        return True
        except OSError:
            return False
        return False

    # ------------------------------------------------------------------ selection sync
    def _find(self, loc: str, parent: QModelIndex = QModelIndex(), depth: int = 0) -> QModelIndex:
        for r in range(self.model_.rowCount(parent)):
            i = self.model_.index(r, 0, parent)
            if i.data(LocRole) == loc and i.data(KindRole) != "favorite":
                return i
            if self.isExpanded(i) and depth < 30:
                f = self._find(loc, i, depth + 1)
                if f.isValid():
                    return f
        return QModelIndex()

    def select_location(self, loc: str, expand_to: bool | None = None):
        self._current_loc = loc
        if expand_to is None:
            expand_to = settings().get("nav_auto_expand")
        idx = self._find(loc)
        if not idx.isValid() and expand_to and loc.startswith("/"):
            idx = self._expand_to(loc)
        if not idx.isValid():
            # a favorite pointing here?
            for r in range(self.model_.rowCount()):
                top = self.model_.index(r, 0)
                for c in range(self.model_.rowCount(top)):
                    ch = self.model_.index(c, 0, top)
                    if ch.data(LocRole) == loc:
                        idx = ch
                        break
        self.blockSignals(True)
        if idx.isValid():
            self.setCurrentIndex(idx)
            self.scrollTo(idx)
        else:
            self.clearSelection()
            self.setCurrentIndex(QModelIndex())
        self.blockSignals(False)

    def _expand_to(self, path: str) -> QModelIndex:
        # find the deepest visible ancestor (drive or library location or home)
        best = QModelIndex()
        best_len = -1

        def walk(parent: QModelIndex, depth=0):
            nonlocal best, best_len
            for r in range(self.model_.rowCount(parent)):
                i = self.model_.index(r, 0, parent)
                l = i.data(LocRole) or ""
                if l.startswith("/") and util.is_subpath(path, l) and len(l) > best_len and i.data(KindRole) != "favorite":
                    best, best_len = i, len(l)
                if self.isExpanded(i) and depth < 3:
                    walk(i, depth + 1)
        walk(QModelIndex())
        if not best.isValid():
            return QModelIndex()
        cur = best
        while True:
            if cur.data(LocRole) == path:
                return cur
            self.expand(cur)
            found = QModelIndex()
            for r in range(self.model_.rowCount(cur)):
                ch = self.model_.index(r, 0, cur)
                l = ch.data(LocRole) or ""
                if l and util.is_subpath(path, l):
                    found = ch
                    break
            if not found.isValid():
                return cur
            cur = found

    def _on_click(self, idx: QModelIndex):
        kind = idx.data(KindRole)
        loc = idx.data(LocRole)
        if kind in ("spacer", "dummy") or not loc:
            return
        if loc == "shell:favorites":
            return
        self.navigate.emit(loc)

    def drawBranches(self, p: QPainter, rect, idx: QModelIndex):
        """Windows 7 hollow/filled triangles, only while the mouse is over the pane."""
        if not (self._hovering or self._always_arrows):
            return
        if not self.model_.hasChildren(idx):
            return
        open_ = self.isExpanded(idx)
        cx = rect.right() - self.indentation() / 2 + 1
        cy = rect.center().y() + 0.5
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if open_:
            pts = [QPointF(cx + 3, cy - 3), QPointF(cx + 3, cy + 3), QPointF(cx - 3, cy + 3)]
            p.setPen(QPen(QColor(38, 38, 38), 1))
            p.setBrush(QColor(89, 89, 89))
        else:
            pts = [QPointF(cx - 2, cy - 4.5), QPointF(cx + 2.5, cy), QPointF(cx - 2, cy + 4.5)]
            p.setPen(QPen(QColor(150, 150, 150), 1))
            p.setBrush(QColor(255, 255, 255))
        p.drawPolygon(QPolygonF(pts))
        p.restore()

    # ------------------------------------------------------------------ events
    def enterEvent(self, e):
        self._hovering = True
        self.viewport().update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovering = False
        self.viewport().update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton:
            idx = self.indexAt(e.position().toPoint())
            if idx.isValid() and idx.data(LocRole):
                self.open_new_tab.emit(idx.data(LocRole))
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            idx = self.currentIndex()
            if idx.isValid():
                self._on_click(idx)
            return
        super().keyPressEvent(e)
        if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            idx = self.currentIndex()
            if idx.isValid() and idx.data(KindRole) not in ("spacer",):
                QTimer.singleShot(0, lambda: self._on_click(idx))

    def _ctx(self, pos):
        idx = self.indexAt(pos)
        if not idx.isValid() or idx.data(KindRole) in ("spacer", "dummy"):
            return
        self.context_requested.emit(idx.data(LocRole), idx.data(KindRole), self.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ drag & drop
    def startDrag(self, actions):
        idx = self.currentIndex()
        loc = idx.data(LocRole) if idx.isValid() else None
        if not loc or not loc.startswith("/") or idx.data(KindRole) in ("header",):
            return
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(loc)])
        md.setData("application/x-linexplorer-nav", (idx.data(KindRole) or "").encode())
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(idx.data(Qt.ItemDataRole.DecorationRole).pixmap(32, 32))
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.LinkAction,
                  Qt.DropAction.MoveAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def _favorite_drop(self, idx: QModelIndex) -> bool:
        return idx.isValid() and (idx.data(KindRole) == "favorite" or idx.data(LocRole) == "shell:favorites")

    def dragMoveEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if idx != self._drop_index:
            self._drop_index = idx
            self.viewport().update()
            self._spring.start()
        if not idx.isValid():
            e.ignore()
            return
        loc = idx.data(LocRole) or ""
        if self._favorite_drop(idx):
            urls = [u.toLocalFile() for u in e.mimeData().urls()]
            if urls and all(os.path.isdir(u) for u in urls):
                e.setDropAction(Qt.DropAction.LinkAction)
                e.accept()
                return
        if loc.startswith("/") or loc.startswith("shell:library:") or loc == "shell:trash":
            from .window import drop_action_for_target
            act = drop_action_for_target(e.mimeData(), loc, e.modifiers())
            e.setDropAction(act)
            e.accept()
            return
        e.ignore()

    def dragLeaveEvent(self, e):
        self._drop_index = QModelIndex()
        self._spring.stop()
        self.viewport().update()

    def _spring_open(self):
        if self._drop_index.isValid():
            self.expand(self._drop_index)

    def dropEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        self._drop_index = QModelIndex()
        self._spring.stop()
        self.viewport().update()
        if not idx.isValid():
            return
        md = QMimeData()
        for f in e.mimeData().formats():
            md.setData(f, e.mimeData().data(f))
        md.setUrls(e.mimeData().urls())
        if self._favorite_drop(idx):
            urls = [u.toLocalFile() for u in e.mimeData().urls()]
            if urls and all(os.path.isdir(u) for u in urls):
                pos = idx.row() + 1 if idx.data(KindRole) == "favorite" else -1
                for u in urls:
                    places.favorites().add(u, pos)
                e.accept()
                return
        loc = idx.data(LocRole)
        right = e.mimeData().hasFormat("application/x-linexplorer-rightdrag")
        from .window import drop_action_for_target
        act = drop_action_for_target(e.mimeData(), loc, e.modifiers())
        e.setDropAction(act)
        e.accept()
        QTimer.singleShot(0, lambda: self.drop_requested.emit(md, loc, act, right))
