"""Persistent settings (Folder Options, layout, per-folder view 'bags')."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QObject, QSettings, Signal

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "LinExplorer")
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "LinExplorer")

DEFAULTS = {
    # Folder Options > General
    "browse_same_window": True,
    "single_click": False,
    "underline_always": False,
    "nav_show_all_folders": False,
    "nav_auto_expand": False,
    "nav_show_recycle_bin": True,
    # Folder Options > View (advanced settings)
    "always_icons": False,
    "always_menus": False,
    "file_icon_on_thumbs": True,
    "size_in_tips": True,
    "show_hidden": False,
    "hide_empty_drives": True,
    "hide_extensions": True,
    "restore_windows": False,
    "tab_bar_always": True,
    "new_tab_same_folder": False,
    "show_drive_letters": True,
    "popup_descriptions": True,
    "preview_handlers": True,
    "checkboxes": False,
    "typing_mode": "select",            # 'select' | 'search'
    "separate_process": False,
    # Folder Options > Search
    "search_contents": False,
    "search_subfolders": True,
    "search_partial": True,
    "search_system_dirs": True,
    "search_compressed": False,
    # Recycle Bin properties
    "confirm_delete": True,
    "use_trash": True,
    # Layout (Organize > Layout)
    "layout_menubar": False,
    "layout_details": True,
    "layout_preview": False,
    "layout_nav": True,
    "layout_library": True,
    "status_bar": False,
    # misc
    "language": "auto",
}


class Settings(QObject):
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(DATA_DIR, exist_ok=True)
        self.q = QSettings(os.path.join(CONFIG_DIR, "linexplorer.conf"), QSettings.Format.IniFormat)
        self._bags_path = os.path.join(CONFIG_DIR, "folderviews.json")
        try:
            with open(self._bags_path, encoding="utf-8") as f:
                self._bags = json.load(f)
        except (OSError, ValueError):
            self._bags = {}

    # ---- simple values
    def get(self, key: str):
        d = DEFAULTS.get(key)
        v = self.q.value(key, d)
        if isinstance(d, bool):
            if isinstance(v, str):
                return v.lower() in ("1", "true", "yes")
            return bool(v)
        if isinstance(d, int) and not isinstance(d, bool):
            try:
                return int(v)
            except (TypeError, ValueError):
                return d
        return v

    def set(self, key: str, value):
        if self.get(key) == value and self.q.contains(key):
            return
        self.q.setValue(key, value)
        self.q.sync()
        self.changed.emit(key)

    def reset_defaults(self, keys):
        for k in keys:
            self.q.remove(k)
        self.q.sync()
        for k in keys:
            self.changed.emit(k)

    # ---- lists / json values
    def get_json(self, key: str, default):
        v = self.q.value(key)
        if v is None:
            return default
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return default

    def set_json(self, key: str, value):
        self.q.setValue(key, json.dumps(value, ensure_ascii=False))
        self.q.sync()
        self.changed.emit(key)

    # ---- per-folder views
    def bag(self, location: str) -> dict | None:
        return self._bags.get(location)

    def set_bag(self, location: str, bag: dict):
        self._bags[location] = bag
        if len(self._bags) > 5000:          # keep it bounded like Windows' BagMRU
            for k in list(self._bags)[:1000]:
                self._bags.pop(k, None)
        self._save_bags()

    def type_default(self, ftype: str) -> dict | None:
        return self._bags.get("__type__:" + ftype)

    def set_type_default(self, ftype: str, bag: dict | None):
        k = "__type__:" + ftype
        if bag is None:
            self._bags.pop(k, None)
        else:
            self._bags[k] = bag
        self._save_bags()

    def reset_all_bags(self):
        self._bags = {}
        self._save_bags()

    def _save_bags(self):
        tmp = self._bags_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._bags, f, ensure_ascii=False)
            os.replace(tmp, self._bags_path)
        except OSError:
            pass


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
