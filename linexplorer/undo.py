"""Multi-level Undo / Redo for file operations (Ctrl+Z / Ctrl+Y), shared by all windows."""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Signal

from . import fileops, trash
from .i18n import T


class Action:
    def __init__(self, kind: str, data: dict):
        self.kind = kind
        self.data = data

    def label(self) -> str:
        return T("undo_" + self.kind)


class UndoManager(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.undo_stack: list[Action] = []
        self.redo_stack: list[Action] = []
        self._busy = False

    def push(self, kind: str, data: dict):
        if self._busy:
            return
        self.undo_stack.append(Action(kind, data))
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.changed.emit()

    def record_result(self, res: dict):
        op = res.get("op")
        if res.get("cancelled") and not (res.get("created") or res.get("moved") or res.get("trashed")):
            return
        if op == "copy" and res.get("created"):
            self.push("copy", {"created": list(res["created"])})
        elif op == "move" and res.get("moved"):
            self.push("move", {"moved": list(res["moved"])})
        elif op == "trash" and res.get("trashed"):
            self.push("delete", {"trashed": list(res["trashed"])})
        elif op == "link" and res.get("created"):
            self.push("link", {"created": list(res["created"])})
        elif op == "restore" and res.get("restored"):
            self.push("restore", {"restored": list(res["restored"])})

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def undo_text(self) -> str:
        return self.undo_stack[-1].label() if self.undo_stack else T("undo")

    def redo_text(self) -> str:
        return T("redo_" + self.redo_stack[-1].kind) if self.redo_stack else T("redo")

    def undo(self, parent=None):
        if not self.undo_stack:
            return
        a = self.undo_stack.pop()
        if not self._apply(a, reverse=True, parent=parent):
            self.undo_stack.append(a)
            return
        self.redo_stack.append(a)
        self.changed.emit()

    def redo(self, parent=None):
        if not self.redo_stack:
            return
        a = self.redo_stack.pop()
        if not self._apply(a, reverse=False, parent=parent):
            self.redo_stack.append(a)
            return
        self.undo_stack.append(a)
        self.changed.emit()

    def _apply(self, a: Action, reverse: bool, parent):
        k, d = a.kind, a.data
        self._busy = True
        try:
            if k == "rename":
                src, dst = (d["new"], d["old"]) if reverse else (d["old"], d["new"])
                if os.path.lexists(src) and not os.path.lexists(dst):
                    os.rename(src, dst)
            elif k == "newfolder":
                p = d["path"]
                if reverse:
                    if os.path.isdir(p):
                        try:
                            os.rmdir(p)
                        except OSError:
                            d["trashed"] = trash.move_to_trash(p)
                else:
                    os.makedirs(p, exist_ok=True)
            elif k in ("copy", "link"):
                paths = d["created"]
                if reverse:
                    if not fileops.confirm_delete(parent, [p for p in paths if os.path.lexists(p)], False):
                        raise _Abort()
                    tr = []
                    for p in paths:
                        if os.path.lexists(p):
                            w = trash.move_to_trash(p)
                            if w:
                                tr.append((p, w))
                    d["trashed"] = tr
                else:
                    for orig, where in d.get("trashed", []):
                        e = trash.entry_from_files_path(where)
                        if e and not os.path.lexists(orig):
                            trash.restore(e, orig)
            elif k == "move":
                pairs = d["moved"]
                for src, dst, merged in (pairs if not reverse else reversed(pairs)):
                    a_, b_ = (dst, src) if reverse else (src, dst)
                    if os.path.lexists(a_) and not os.path.lexists(b_):
                        os.makedirs(os.path.dirname(b_), exist_ok=True)
                        try:
                            os.rename(a_, b_)
                        except OSError:
                            import shutil
                            shutil.move(a_, b_)
            elif k == "delete":
                if reverse:
                    for orig, where in d["trashed"]:
                        e = trash.entry_from_files_path(where)
                        if e and not os.path.lexists(orig):
                            trash.restore(e, orig)
                else:
                    new = []
                    for orig, _ in d["trashed"]:
                        if os.path.lexists(orig):
                            w = trash.move_to_trash(orig)
                            if w:
                                new.append((orig, w))
                    d["trashed"] = new
            elif k == "restore":
                if reverse:
                    new = []
                    for where, dest in d["restored"]:
                        if os.path.lexists(dest):
                            w = trash.move_to_trash(dest)
                            if w:
                                new.append((w, dest))
                    d["restored"] = new
                else:
                    new = []
                    for where, dest in d["restored"]:
                        e = trash.entry_from_files_path(where)
                        if e and not os.path.lexists(dest):
                            trash.restore(e, dest)
                            new.append((where, dest))
            elif k == "properties":
                pass
        except _Abort:
            return False
        except OSError:
            pass
        finally:
            self._busy = False
        return True


class _Abort(Exception):
    pass


_undo: UndoManager | None = None


def manager() -> UndoManager:
    global _undo
    if _undo is None:
        _undo = UndoManager()
    return _undo
