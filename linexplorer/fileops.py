"""File operations with Windows 7 style progress, conflict and confirmation dialogs."""
from __future__ import annotations

import errno
import os
import shutil
import stat
import threading
import time

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QCommandLinkButton, QDialog, QDialogButtonBox, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from . import icons, mime, trash, util, ziputil
from .i18n import T
from .settings import settings


# =========================================================================== sources
class Src:
    """A thing to copy: a file/folder on disk or a member of a zip archive."""
    __slots__ = ("path", "zip", "member", "is_dir", "name")

    def __init__(self, path: str = "", zip_: str = "", member: str = "", is_dir: bool | None = None):
        self.path, self.zip, self.member = path, zip_, member
        if zip_:
            self.name = member.rstrip("/").split("/")[-1]
            self.is_dir = bool(is_dir)
        else:
            self.name = os.path.basename(path.rstrip("/"))
            self.is_dir = os.path.isdir(path) and not os.path.islink(path) if is_dir is None else is_dir

    @property
    def label(self):
        return self.path or f"{self.zip}/{self.member}"


def tree_stats(path: str) -> tuple[int, int]:
    """(bytes, items) for a path, without following symlinks."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0, 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size, 1
    total, n = 0, 1
    for root, dirs, files in os.walk(path):
        n += len(dirs) + len(files)
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total, n


# =========================================================================== job
class Cancelled(Exception):
    pass


class Job(QObject):
    progress = Signal(dict)
    ask = Signal(str, dict)
    finished = Signal(dict)

    def __init__(self, op: str, sources: list[Src], dest: str = "", **opts):
        super().__init__()
        self.op = op                      # copy move link trash delete restore extract compress zipadd zipdelete empty
        self.sources = sources
        self.dest = dest
        self.opts = opts
        self.cancel_flag = threading.Event()
        self._answer = None
        self._answer_ev = threading.Event()
        self.total_bytes = 0
        self.total_items = 0
        self.done_bytes = 0
        self.done_items = 0
        self.started = 0.0
        self.current = ""
        self.cur_from = ""
        self.cur_to = ""
        self.result = {"created": [], "moved": [], "trashed": [], "deleted": [], "restored": [], "errors": [],
                       "cancelled": False, "op": op, "dest": dest}
        self._last_emit = 0.0
        self._apply_all: dict[str, tuple] = {}
        self._conflicts_left = 0
        self._strategies: dict = {}
        self.strategy = None
        self.last_method = ""

    # ---- thread side helpers
    def _check(self):
        if self.cancel_flag.is_set():
            raise Cancelled()

    def _emit(self, force=False):
        now = time.time()
        if not force and now - self._last_emit < 0.1:
            return
        self._last_emit = now
        self.progress.emit({"bytes": self.done_bytes, "total": self.total_bytes, "items": self.done_items,
                            "total_items": self.total_items, "name": self.current, "from": self.cur_from,
                            "to": self.cur_to, "elapsed": now - self.started})

    def request(self, kind: str, data: dict):
        """Ask the UI thread and wait for the answer."""
        self._answer_ev.clear()
        self.ask.emit(kind, data)
        while not self._answer_ev.wait(0.1):
            if self.cancel_flag.is_set():
                raise Cancelled()
        return self._answer

    def answer(self, value):
        self._answer = value
        self._answer_ev.set()

    def _bytes(self, n: int):
        self.done_bytes += n
        self._emit()

    # ---- run
    def run(self):
        self.started = time.time()
        try:
            self._prepare()
            getattr(self, "_op_" + self.op)()
        except Cancelled:
            self.result["cancelled"] = True
        except Exception as e:  # noqa: BLE001
            self.result["errors"].append(str(e))
        self._emit(force=True)
        self.finished.emit(self.result)

    def _prepare(self):
        if self.op in ("copy", "move", "zipadd", "compress", "extract"):
            for s in self.sources:
                self._check()
                if s.zip:
                    b = ziputil.member_size(s.zip, s.member, s.is_dir)
                    self.total_bytes += b
                    self.total_items += 1
                elif self.op == "extract":
                    self.total_bytes += ziputil.uncompressed_total(s.path)
                    self.total_items += 1
                else:
                    if self.op == "move" and util.same_device(s.path, self.dest):
                        self.total_items += 1
                        continue
                    b, n = tree_stats(s.path)
                    self.total_bytes += b
                    self.total_items += n
                self.current = s.name
                self._emit()
        else:
            self.total_items = len(self.sources)

    # ---------------------------------------------------------------- conflicts
    def _resolve(self, src: Src, dst: str, moving: bool) -> tuple[str, str]:
        """Returns (action, target) with action in replace/skip/keep."""
        if not os.path.lexists(dst):
            return "go", dst
        if "file" in self._apply_all:
            act = self._apply_all["file"]
        else:
            ans = self.request("conflict", {"src": src.label, "src_zip": bool(src.zip), "dst": dst, "moving": moving,
                                            "remaining": self._conflicts_left})
            if ans is None or ans[0] == "cancel":
                raise Cancelled()
            act, all_ = ans
            if all_:
                self._apply_all["file"] = act
        if act == "skip":
            return "skip", dst
        if act == "keep":
            folder = os.path.dirname(dst)
            return "go", os.path.join(folder, util.unique_name(folder, os.path.basename(dst), src.is_dir))
        return "replace", dst

    def _merge_ok(self, src: Src, dst: str) -> bool:
        if "folder" in self._apply_all:
            return self._apply_all["folder"] == "yes"
        ans = self.request("merge", {"src": src.label, "dst": dst})
        if ans is None or ans[0] == "cancel":
            raise Cancelled()
        if ans[1]:
            self._apply_all["folder"] = ans[0]
        return ans[0] == "yes"

    def _error(self, msg: str, path: str) -> str:
        if "error" in self._apply_all:
            return self._apply_all["error"]
        ans = self.request("error", {"msg": msg, "path": path, "op": self.op})
        if ans is None or ans[0] == "cancel":
            raise Cancelled()
        if ans[1]:
            self._apply_all["error"] = ans[0]
        return ans[0]

    # ---------------------------------------------------------------- copy / move
    def _copy_file(self, src: str, dst: str):
        self._check()
        if os.path.islink(src):
            if os.path.lexists(dst):
                os.remove(dst)
            os.symlink(os.readlink(src), dst)
            self.done_items += 1
            return
        from . import copyengine
        try:
            key = (os.stat(src).st_dev, os.stat(os.path.dirname(dst) or ".").st_dev)
        except OSError:
            key = None
        strat = self._strategies.get(key) if key else None
        if strat is None:
            strat = copyengine.classify(src, os.path.dirname(dst) or ".")
            if key:
                self._strategies[key] = strat
        self.strategy = strat
        try:
            self.last_method = copyengine.copy_file(src, dst, strat, self._bytes, self.cancel_flag.is_set)
        except copyengine.Cancelled:
            try:
                os.remove(dst)
            except OSError:
                pass
            raise Cancelled()
        except BaseException:
            try:
                os.remove(dst)
            except OSError:
                pass
            raise
        try:
            shutil.copystat(src, dst)
        except OSError:
            pass
        self.done_items += 1

    def _copy_tree(self, src: str, dst: str):
        os.makedirs(dst, exist_ok=True)
        try:
            names = os.listdir(src)
        except OSError as e:
            if self._error(str(e), src) == "skip":
                return
            raise
        for n in names:
            self._check()
            s, d = os.path.join(src, n), os.path.join(dst, n)
            self.current, self.cur_from, self.cur_to = n, os.path.dirname(s), os.path.dirname(d)
            try:
                if os.path.isdir(s) and not os.path.islink(s):
                    if os.path.lexists(d) and not os.path.isdir(d):
                        act, d = self._resolve(Src(s), d, False)
                        if act == "skip":
                            continue
                        if act == "replace":
                            os.remove(d)
                    self._copy_tree(s, d)
                    self.done_items += 1
                else:
                    act, d2 = self._resolve(Src(s), d, False) if os.path.lexists(d) else ("go", d)
                    if act == "skip":
                        self.done_items += 1
                        self._bytes(os.lstat(s).st_size)
                        continue
                    if act == "replace" and os.path.isdir(d2) and not os.path.islink(d2):
                        shutil.rmtree(d2)
                    self._copy_file(s, d2)
            except Cancelled:
                raise
            except OSError as e:
                r = self._error(f"{e.strerror or e}", s)
                if r == "retry":
                    return self._copy_tree(src, dst)
                self.result["errors"].append(f"{s}: {e}")
        try:
            shutil.copystat(src, dst, follow_symlinks=False)
        except OSError:
            pass

    def _op_copy(self):
        self._conflicts_left = len(self.sources)
        dest = self.dest
        if ziputil.is_zip_path(dest) or self.opts.get("zip_dest"):
            return self._op_zipadd()
        for s in self.sources:
            self._check()
            self._conflicts_left -= 1
            self.current, self.cur_from, self.cur_to = s.name, os.path.dirname(s.path) if s.path else s.zip, dest
            if s.zip:
                target = os.path.join(dest, s.name)
                act, target = self._resolve(s, target, False)
                if act == "skip":
                    continue
                if act == "replace":
                    self._remove(target)
                try:
                    ziputil.copy_out(s.zip, s.member, s.is_dir, target, self._bytes, self.cancel_flag.is_set)
                except InterruptedError:
                    raise Cancelled()
                self.result["created"].append(target)
                continue
            if not os.path.lexists(s.path):
                continue
            if s.is_dir and util.is_subpath(dest, s.path):
                self.request("info", {"msg": T("dest_is_subfolder"), "path": s.path})
                continue
            if os.path.dirname(os.path.abspath(s.path)) == os.path.abspath(dest):
                target = os.path.join(dest, util.copy_name(dest, s.name, s.is_dir))
            else:
                target = os.path.join(dest, s.name)
            try:
                if s.is_dir:
                    if os.path.isdir(target):
                        if not self._merge_ok(s, target):
                            continue
                    elif os.path.lexists(target):
                        act, target = self._resolve(s, target, False)
                        if act == "skip":
                            continue
                        if act == "replace":
                            self._remove(target)
                    existed = os.path.isdir(target)
                    self._copy_tree(s.path, target)
                    if not existed:
                        self.result["created"].append(target)
                else:
                    act, target = self._resolve(s, target, False)
                    if act == "skip":
                        continue
                    if act == "replace":
                        self._remove(target)
                    self._copy_file(s.path, target)
                    self.result["created"].append(target)
            except Cancelled:
                raise
            except OSError as e:
                if self._error(str(e.strerror or e), s.path) == "retry":
                    self.sources.append(s)
                else:
                    self.result["errors"].append(f"{s.path}: {e}")

    def _remove(self, p: str):
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        elif os.path.lexists(p):
            os.remove(p)

    def _move_tree_merge(self, src: str, dst: str):
        """Move folder contents into an existing folder (merge)."""
        for n in os.listdir(src):
            self._check()
            s, d = os.path.join(src, n), os.path.join(dst, n)
            if os.path.isdir(s) and not os.path.islink(s) and os.path.isdir(d):
                self._move_tree_merge(s, d)
                continue
            if os.path.lexists(d):
                act, d = self._resolve(Src(s), d, True)
                if act == "skip":
                    continue
                if act == "replace":
                    self._remove(d)
            self._move_one(s, d)
        try:
            os.rmdir(src)
        except OSError:
            pass

    def _move_one(self, s: str, d: str):
        try:
            os.rename(s, d)
            self.done_items += 1
            self._emit()
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
            if os.path.isdir(s) and not os.path.islink(s):
                self._copy_tree(s, d)
                shutil.rmtree(s)
            else:
                self._copy_file(s, d)
                os.remove(s)

    def _op_move(self):
        dest = self.dest
        self._conflicts_left = len(self.sources)
        if ziputil.is_zip_path(dest) or self.opts.get("zip_dest"):
            self._op_zipadd()
            for s in self.sources:
                if s.path and not s.zip:
                    self._remove(s.path)
            return
        for s in self.sources:
            self._check()
            self._conflicts_left -= 1
            if s.zip:
                target = os.path.join(dest, s.name)
                act, target = self._resolve(s, target, True)
                if act == "skip":
                    continue
                if act == "replace":
                    self._remove(target)
                ziputil.copy_out(s.zip, s.member, s.is_dir, target, self._bytes, self.cancel_flag.is_set)
                ziputil.delete_members(s.zip, [(s.member, s.is_dir)])
                self.result["created"].append(target)
                continue
            if not os.path.lexists(s.path):
                continue
            src_parent = os.path.dirname(os.path.abspath(s.path))
            if src_parent == os.path.abspath(dest):
                continue          # moving onto itself does nothing
            if s.is_dir and util.is_subpath(dest, s.path):
                self.request("info", {"msg": T("dest_is_subfolder"), "path": s.path})
                continue
            target = os.path.join(dest, s.name)
            self.current, self.cur_from, self.cur_to = s.name, src_parent, dest
            try:
                if s.is_dir and os.path.isdir(target):
                    if not self._merge_ok(s, target):
                        continue
                    self._move_tree_merge(s.path, target)
                    self.result["moved"].append((s.path, target, True))
                    continue
                act, target = self._resolve(s, target, True)
                if act == "skip":
                    continue
                if act == "replace":
                    self._remove(target)
                self._move_one(s.path, target)
                self.result["moved"].append((s.path, target, False))
                self.result["created"].append(target)
            except Cancelled:
                raise
            except OSError as e:
                if self._error(str(e.strerror or e), s.path) == "retry":
                    self.sources.append(s)
                else:
                    self.result["errors"].append(f"{s.path}: {e}")

    def _op_link(self):
        dest = self.dest
        for s in self.sources:
            self._check()
            base = T("shortcut_name", s.name)
            name = util.unique_name(dest, base, s.is_dir)
            target = os.path.join(dest, name)
            try:
                os.symlink(s.path, target)
                self.result["created"].append(target)
            except OSError as e:
                self.result["errors"].append(f"{s.path}: {e}")
            self.done_items += 1
            self._emit()

    # ---------------------------------------------------------------- delete
    def _op_trash(self):
        for s in self.sources:
            self._check()
            self.current = s.name
            self.cur_from = os.path.dirname(s.path)
            where = trash.move_to_trash(s.path)
            if where is None:
                ans = self.request("no_trash", {"path": s.path})
                if ans and ans[0] == "yes":
                    try:
                        self._remove(s.path)
                        self.result["deleted"].append(s.path)
                    except OSError as e:
                        self.result["errors"].append(f"{s.path}: {e}")
                elif ans and ans[0] == "cancel":
                    raise Cancelled()
            else:
                self.result["trashed"].append((s.path, where))
            self.done_items += 1
            self._emit()

    def _op_delete(self):
        for s in self.sources:
            self._check()
            self.current = s.name
            self.cur_from = os.path.dirname(s.path)
            try:
                if s.path.startswith(trash.home_trash()) or "/.Trash" in s.path:
                    e = trash.entry_from_files_path(s.path)
                    if e is not None:
                        trash.delete_entry(e)
                        self.result["deleted"].append(s.path)
                        self.done_items += 1
                        continue
                self._remove(s.path)
                self.result["deleted"].append(s.path)
            except OSError as e:
                if self._error(str(e.strerror or e), s.path) == "retry":
                    self.sources.append(s)
                else:
                    self.result["errors"].append(f"{s.path}: {e}")
            self.done_items += 1
            self._emit()

    def _op_empty(self):
        entries = trash.list_entries()
        self.total_items = len(entries)
        for e in entries:
            self._check()
            self.current = e.display_name
            trash.delete_entry(e)
            self.done_items += 1
            self._emit()

    def _op_restore(self):
        for s in self.sources:
            self._check()
            e = trash.entry_from_files_path(s.path)
            if e is None:
                continue
            dest = self.opts.get("targets", {}).get(s.path) or e.orig_path
            self.current = e.display_name
            if os.path.lexists(dest):
                act, dest = self._resolve(Src(s.path), dest, True)
                if act == "skip":
                    continue
                if act == "replace":
                    self._remove(dest)
            try:
                trash.restore(e, dest)
                self.result["restored"].append((s.path, dest))
            except OSError as ex:
                self.result["errors"].append(f"{e.display_name}: {ex}")
            self.done_items += 1
            self._emit()

    # ---------------------------------------------------------------- zip
    def _op_extract(self):
        for s in self.sources:
            self._check()
            dest = self.dest
            os.makedirs(dest, exist_ok=True)
            self.current = s.name
            self.cur_from = s.path
            self.cur_to = dest

            def conflict(target, name, is_dir, zp=s.path):
                act, t = self._resolve(Src(zip_=zp, member=name, is_dir=False), target, False)
                if act == "skip":
                    return None
                if act == "replace":
                    self._remove(t)
                return t

            def progress(n):
                self._bytes(n)

            try:
                written = ziputil.extract_all(s.path, dest, progress, self.cancel_flag.is_set, conflict)
            except InterruptedError:
                raise Cancelled()
            self.done_items += len(written)
            self.result["created"].append(dest)

    def _op_compress(self):
        paths = [s.path for s in self.sources]
        self.current = os.path.basename(self.dest)
        try:
            ziputil.create(self.dest, paths, self._bytes, self.cancel_flag.is_set, self.opts.get("fmt"))
        except InterruptedError:
            raise Cancelled()
        self.result["created"].append(self.dest)

    def _op_zipadd(self):
        zp, inner = (ziputil.split(self.opts["zip_loc"]) if self.opts.get("zip_loc") else (self.dest, ""))
        paths = [s.path for s in self.sources if s.path and not s.zip]
        existing = {e.name for e in ziputil.list_dir(zp, inner)} if os.path.exists(zp) else set()
        keep = []
        for p in paths:
            n = os.path.basename(p.rstrip("/"))
            if n in existing:
                ans = self.request("conflict", {"src": p, "src_zip": False, "dst": f"{zp}/{inner}/{n}".replace("//", "/"),
                                                "moving": False, "remaining": 0, "dst_zip": True})
                if ans is None or ans[0] == "cancel":
                    raise Cancelled()
                if ans[0] == "skip":
                    continue
            keep.append(p)
        self.current = os.path.basename(zp)
        if keep:
            if not ziputil.is_writable(zp):
                self.result["errors"].append(f"{os.path.basename(zp)}: {T('archive_readonly')}")
                return
            try:
                ziputil.add_paths(zp, inner, keep, self._bytes, self.cancel_flag.is_set)
            except InterruptedError:
                raise Cancelled()
        self.result["created"].append(zp)

    def _op_zipdelete(self):
        zp = self.dest
        ziputil.delete_members(zp, [(s.member, s.is_dir) for s in self.sources])
        self.result["deleted"] += [s.member for s in self.sources]


# =========================================================================== dialogs
def _file_info_widget(path: str, is_zip: bool = False) -> QWidget:
    """Icon + name + location + size + date, as in the Windows 7 conflict dialog."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(24, 0, 0, 0)
    lay.setSpacing(10)
    ic = QLabel()
    name = os.path.basename(path.rstrip("/"))
    if is_zip or not os.path.lexists(path):
        mt = mime.mime_for_name(name)
        size_txt, date_txt = "", ""
    else:
        isdir = os.path.isdir(path)
        mt = mime.mime_for(path, isdir)
        st = os.lstat(path)
        size_txt = "" if isdir else T("size_label", util.fmt_size_short(st.st_size))
        date_txt = T("date_modified_label", util.fmt_datetime(st.st_mtime))
    pm = None
    from . import thumbs
    if not is_zip and os.path.isfile(path) and thumbs.kind_of(path) == "image":
        from PySide6.QtGui import QImageReader, QPixmap
        r = QImageReader(path)
        r.setAutoTransform(True)
        sz = r.size()
        if sz.isValid():
            r.setScaledSize(sz.scaled(QSize(48, 48), Qt.AspectRatioMode.KeepAspectRatio))
            img = r.read()
            if not img.isNull():
                pm = QPixmap.fromImage(img)
    ic.setPixmap(pm or icons.for_mime(mt).pixmap(48, 48))
    ic.setFixedSize(52, 52)
    lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
    v = QVBoxLayout()
    v.setSpacing(1)
    for txt, bold in ((name, False), (os.path.dirname(path) if not is_zip else path, False), (size_txt, False),
                      (date_txt, False)):
        if not txt:
            continue
        l = QLabel(txt)
        l.setStyleSheet("color: #333;")
        l.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        v.addWidget(l)
    lay.addLayout(v, 1)
    return w


class ConflictDialog(QDialog):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        moving = data.get("moving")
        self.setWindowTitle(T("move_file") if moving else T("copy_file"))
        self.choice = ("cancel", False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 12)
        lay.setSpacing(8)
        head = QLabel(T("conflict_heading"))
        f = head.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        head.setFont(f)
        head.setStyleSheet("color: #1e3287;")
        head.setWordWrap(True)
        lay.addWidget(head)
        sub = QLabel(T("click_file_keep"))
        lay.addWidget(sub)
        src, dst = data["src"], data["dst"]
        dst_zip = data.get("dst_zip", False)

        def cmd(title, desc, info_path, info_zip, value):
            b = QCommandLinkButton(title, desc)
            b.clicked.connect(lambda: self._done(value))
            lay.addWidget(b)
            if info_path:
                lay.addWidget(_file_info_widget(info_path, info_zip))

        cmd(T("move_and_replace") if moving else T("copy_and_replace"),
            T("replace_desc_move") if moving else T("replace_desc_copy"), src, data.get("src_zip", False), "replace")
        cmd(T("dont_move") if moving else T("dont_copy"), T("dont_copy_desc"), dst, dst_zip, "skip")
        folder = os.path.dirname(dst)
        newname = util.unique_name(folder, os.path.basename(dst)) if not dst_zip else os.path.basename(dst)
        cmd(T("move_keep_both") if moving else T("copy_keep_both"), T("keep_both_desc", newname), None, False, "keep")
        bottom = QHBoxLayout()
        self.all = QCheckBox(T("do_this_next_n", data.get("remaining", 0)) if data.get("remaining", 0) > 0
                             else T("do_this_all_conflicts"))
        bottom.addWidget(self.all)
        bottom.addStretch(1)
        cancel = QPushButton(T("cancel"))
        cancel.clicked.connect(lambda: self._done("cancel"))
        bottom.addWidget(cancel)
        lay.addLayout(bottom)
        self.setMinimumWidth(520)

    def _done(self, v):
        self.choice = (v, self.all.isChecked())
        self.accept()


class MergeDialog(QDialog):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(T("confirm_folder_replace"))
        self.choice = ("cancel", False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 12)
        name = os.path.basename(data["dst"].rstrip("/"))
        t = QLabel(T("merge_text", name))
        t.setWordWrap(True)
        lay.addWidget(t)
        q = QLabel(T("merge_question"))
        q.setStyleSheet("color: #1e3287;")
        lay.addWidget(q)
        lay.addWidget(_file_info_widget(data["src"]))
        lay.addWidget(QLabel(T("with_this_one")))
        lay.addWidget(_file_info_widget(data["dst"]))
        bottom = QHBoxLayout()
        self.all = QCheckBox(T("do_this_all_current"))
        bottom.addWidget(self.all)
        bottom.addStretch(1)
        for key, v in (("yes", "yes"), ("no", "no"), ("cancel", "cancel")):
            b = QPushButton(T(key))
            b.clicked.connect(lambda _=False, v=v: self._done(v))
            bottom.addWidget(b)
        lay.addLayout(bottom)
        self.setMinimumWidth(480)

    def _done(self, v):
        self.choice = (v, self.all.isChecked())
        self.accept()


class ErrorDialog(QDialog):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        op = data.get("op", "copy")
        self.setWindowTitle({"move": T("error_moving"), "delete": T("error_deleting")}.get(op, T("error_copying")))
        self.choice = ("cancel", False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 12)
        h = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(QApplication.style().standardIcon(QApplication.style().StandardPixmap.SP_MessageBoxWarning).pixmap(32, 32))
        h.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        t = QLabel(T("cannot_access", os.path.basename(data["path"])) + "\n\n" + data["msg"])
        t.setWordWrap(True)
        h.addWidget(t, 1)
        lay.addLayout(h)
        lay.addWidget(_file_info_widget(data["path"]))
        bottom = QHBoxLayout()
        self.all = QCheckBox(T("do_this_all_current"))
        bottom.addWidget(self.all)
        bottom.addStretch(1)
        for key, v in (("try_again", "retry"), ("skip", "skip"), ("cancel", "cancel")):
            b = QPushButton(T(key))
            b.clicked.connect(lambda _=False, v=v: self._done(v))
            bottom.addWidget(b)
        lay.addLayout(bottom)
        self.setMinimumWidth(460)

    def _done(self, v):
        self.choice = (v, self.all.isChecked())
        self.accept()


class ProgressDialog(QDialog):
    """'Copying 3 items (12.4 MB) from X to Y' with More details."""

    def __init__(self, job: Job, parent=None):
        super().__init__(parent)
        self.job = job
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)
        self.head = QLabel()
        self.head.setTextFormat(Qt.TextFormat.RichText)
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.big = QLabel(T("calculating"))
        f = self.big.font()
        f.setPointSizeF(f.pointSizeF() * 1.35)
        self.big.setFont(f)
        self.big.setStyleSheet("color: #1e3287;")
        lay.addWidget(self.big)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(16)
        self.bar.setStyleSheet(
            "QProgressBar { border: 1px solid #b2b2b2; border-radius: 2px; background: #e6e6e6; }"
            "QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #a6e88a, stop:0.5 #37c21b,"
            " stop:1 #1fa50a); }")
        lay.addWidget(self.bar)
        self.details = QWidget()
        g = QGridLayout(self.details)
        g.setContentsMargins(0, 4, 0, 4)
        self.lab = {}
        for i, k in enumerate(("name", "from", "to", "time", "items", "speed")):
            a = QLabel(T("pd_" + k))
            a.setStyleSheet("color: #555;")
            b = QLabel("")
            b.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            g.addWidget(a, i, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            g.addWidget(b, i, 1)
            self.lab[k] = b
        g.setColumnStretch(1, 1)
        self.details.hide()
        lay.addWidget(self.details)
        bottom = QHBoxLayout()
        self.more = QToolButton()
        self.more.setText(T("more_details"))
        self.more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.more.setArrowType(Qt.ArrowType.DownArrow)
        self.more.setAutoRaise(True)
        self.more.clicked.connect(self._toggle)
        bottom.addWidget(self.more)
        bottom.addStretch(1)
        self.cancel_btn = QPushButton(T("cancel"))
        self.cancel_btn.clicked.connect(self._cancel)
        bottom.addWidget(self.cancel_btn)
        lay.addLayout(bottom)
        self._speed_hist = []
        job.progress.connect(self.update_progress)

    def _toggle(self):
        vis = not self.details.isVisible()
        self.details.setVisible(vis)
        self.more.setText(T("fewer_details") if vis else T("more_details"))
        self.more.setArrowType(Qt.ArrowType.UpArrow if vis else Qt.ArrowType.DownArrow)
        self.adjustSize()

    def _cancel(self):
        self.cancel_btn.setEnabled(False)
        self.big.setText(T("cancelling"))
        self.job.cancel_flag.set()

    def closeEvent(self, e):
        self._cancel()
        e.ignore()

    def update_progress(self, d: dict):
        job = self.job
        verb = {"copy": "copying", "move": "moving", "trash": "deleting", "delete": "deleting", "empty": "deleting",
                "extract": "extracting", "compress": "compressing", "zipadd": "copying", "restore": "restoring",
                "link": "copying"}.get(job.op, "copying")
        total, done = d["total"], d["bytes"]
        n = len(job.sources) if job.op not in ("empty",) else d["total_items"]
        if total:
            pct = int(100 * done / total)
        elif d["total_items"]:
            pct = int(100 * d["items"] / max(1, d["total_items"]))
        else:
            pct = 0
        pct = max(0, min(100, pct))
        self.setWindowTitle(T("pct_complete", pct))
        src_dir = d["from"] or (os.path.dirname(job.sources[0].path) if job.sources and job.sources[0].path else "")
        dst_dir = d["to"] or job.dest
        size = f" ({util.fmt_size_short(total)})" if total else ""
        head = T("hd_" + verb, n, size)
        if src_dir and job.op in ("copy", "move", "zipadd", "extract", "compress"):
            head += "<br>" + T("from_to", f"<a style='color:#0066cc;text-decoration:none' href='#'>{os.path.basename(src_dir.rstrip('/')) or src_dir}</a>",
                               f"<a style='color:#0066cc;text-decoration:none' href='#'>{os.path.basename(dst_dir.rstrip('/')) or dst_dir}</a>")
        elif src_dir:
            head += "<br>" + T("from_only", f"<a style='color:#0066cc;text-decoration:none' href='#'>{os.path.basename(src_dir.rstrip('/')) or src_dir}</a>")
        self.head.setText(head)
        self.bar.setRange(0, 100)
        self.bar.setValue(pct)
        el = d["elapsed"]
        speed = done / el if el > 0.3 else 0
        if total and speed > 0 and done < total:
            rem = (total - done) / speed
            self.big.setText(T("about_remaining", self._fmt_secs(rem)))
            self.lab["time"].setText(T("about_x", self._fmt_secs(rem)))
        elif self.cancel_btn.isEnabled():
            self.big.setText(T("calculating") if not done and total else T("pct_complete", pct))
            self.lab["time"].setText(T("calculating"))
        self.lab["name"].setText(d["name"])
        self.lab["from"].setText(src_dir)
        self.lab["to"].setText(dst_dir if job.op in ("copy", "move", "zipadd", "extract", "compress") else "")
        left_items = max(0, d["total_items"] - d["items"])
        self.lab["items"].setText(f"{util.fmt_int(left_items)} ({util.fmt_size_short(max(0, total - done))})")
        self.lab["speed"].setText(f"{util.fmt_size_short(int(speed))}/s" if speed else T("calculating"))

    @staticmethod
    def _fmt_secs(s: float) -> str:
        s = int(s)
        if s < 60:
            return T("n_seconds", max(1, s))
        if s < 3600:
            return T("n_minutes", round(s / 60))
        return T("n_hours_minutes", s // 3600, (s % 3600) // 60)


# =========================================================================== runner
class OpsManager(QObject):
    """Runs jobs in threads and shows the dialogs on the UI thread."""
    job_finished = Signal(dict)

    def __init__(self):
        super().__init__()
        self._jobs: list[tuple[Job, ProgressDialog | None, threading.Thread]] = []

    def run(self, job: Job, parent: QWidget | None = None, show_after: float = 0.6, on_done=None):
        dlg = ProgressDialog(job, parent)
        job.ask.connect(lambda kind, data: self._ask(job, kind, data, dlg, parent))
        holder = {}

        def finished(res):
            timer.stop()
            dlg.hide()
            dlg.deleteLater()
            self._jobs = [j for j in self._jobs if j[0] is not job]
            if res.get("errors") and not res.get("cancelled"):
                msg = "\n".join(res["errors"][:12])
                if len(res["errors"]) > 12:
                    msg += f"\n… (+{len(res['errors']) - 12})"
                QMessageBox.warning(parent, T("problem_" + ("moving" if job.op == "move" else "copying")), msg)
            if on_done:
                on_done(res)
            self.job_finished.emit(res)

        job.finished.connect(finished)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: (dlg.show(), dlg.raise_()))
        timer.start(int(show_after * 1000))
        t = threading.Thread(target=job.run, daemon=True)
        self._jobs.append((job, dlg, t))
        t.start()
        holder["t"] = t
        return job

    def _ask(self, job: Job, kind: str, data: dict, dlg: ProgressDialog, parent):
        owner = dlg if dlg.isVisible() else parent
        if kind == "conflict":
            d = ConflictDialog(data, owner)
            d.exec()
            job.answer(d.choice)
        elif kind == "merge":
            d = MergeDialog(data, owner)
            d.exec()
            job.answer(d.choice)
        elif kind == "error":
            d = ErrorDialog(data, owner)
            d.exec()
            job.answer(d.choice)
        elif kind == "no_trash":
            name = os.path.basename(data["path"])
            r = QMessageBox.question(owner, T("delete_file"), T("cant_recycle", name),
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                                     QMessageBox.StandardButton.Cancel)
            job.answer(({QMessageBox.StandardButton.Yes: "yes", QMessageBox.StandardButton.No: "no"}.get(r, "cancel"), False))
        elif kind == "info":
            QMessageBox.information(owner, T("interrupted_action"), data["msg"] + "\n\n" + data.get("path", ""))
            job.answer(("ok", False))
        else:
            job.answer(None)

    def busy(self) -> bool:
        return bool(self._jobs)


_ops: OpsManager | None = None


def ops() -> OpsManager:
    global _ops
    if _ops is None:
        _ops = OpsManager()
    return _ops


# =========================================================================== confirmation
def confirm_delete(parent, items: list, permanent: bool) -> bool:
    """Windows 7 'Delete File' / 'Delete Multiple Items' confirmation."""
    if not items:
        return False
    if not settings().get("confirm_delete") and not permanent:
        return True
    dlg = QDialog(parent)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(18, 14, 18, 12)
    lay.setSpacing(10)
    h = QHBoxLayout()
    icl = QLabel()
    icl.setPixmap((icons.trash(True) if not permanent else QApplication.style().standardIcon(
        QApplication.style().StandardPixmap.SP_MessageBoxWarning)).pixmap(32, 32))
    h.addWidget(icl, 0, Qt.AlignmentFlag.AlignTop)
    if len(items) == 1:
        it = items[0]
        path = it if isinstance(it, str) else it.path
        isdir = os.path.isdir(path) and not os.path.islink(path)
        islink = os.path.islink(path)
        dlg.setWindowTitle(T("delete_shortcut") if islink else T("delete_folder") if isdir else T("delete_file"))
        if permanent:
            msg = T("perm_delete_folder") if isdir else T("perm_delete_file")
        else:
            msg = T("recycle_shortcut") if islink else T("recycle_folder") if isdir else T("recycle_file")
        t = QLabel(msg)
        t.setWordWrap(True)
        h.addWidget(t, 1)
        lay.addLayout(h)
        lay.addWidget(_file_info_widget(path))
    else:
        dlg.setWindowTitle(T("delete_multiple"))
        msg = T("perm_delete_n", len(items)) if permanent else T("recycle_n", len(items))
        t = QLabel(msg)
        t.setWordWrap(True)
        h.addWidget(t, 1)
        lay.addLayout(h)
    bb = QDialogButtonBox()
    yes = bb.addButton(T("yes_amp"), QDialogButtonBox.ButtonRole.YesRole)
    no = bb.addButton(T("no_amp"), QDialogButtonBox.ButtonRole.NoRole)
    yes.clicked.connect(dlg.accept)
    no.clicked.connect(dlg.reject)
    yes.setDefault(True)
    lay.addWidget(bb)
    dlg.setMinimumWidth(420)
    return dlg.exec() == QDialog.DialogCode.Accepted
