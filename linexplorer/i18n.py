"""User-interface language (English / Greek)."""
from __future__ import annotations

import os

from PySide6.QtCore import QLocale

_lang = "en"


def detect() -> str:
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var, "")
        if v:
            return "el" if v.lower().startswith("el") else "en"
    return "el" if QLocale.system().language() == QLocale.Language.Greek else "en"


def init(choice: str = "auto"):
    global _lang
    _lang = detect() if choice in ("", "auto", None) else ("el" if choice == "el" else "en")
    from . import util
    if _lang == "el":
        loc = QLocale(QLocale.Language.Greek, QLocale.Country.Greece)
    else:
        sysloc = QLocale.system()
        loc = sysloc if sysloc.language() == QLocale.Language.English else QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)
    util.set_locale(loc)
    QLocale.setDefault(loc)          # MIME type descriptions follow the UI language


def lang_code() -> str:
    return _lang


def T(key: str, *args) -> str:
    from .strings import STRINGS
    entry = STRINGS.get(key)
    if entry is None:
        s = key
    else:
        s = entry[1] if _lang == "el" and len(entry) > 1 and entry[1] else entry[0]
    if args:
        try:
            s = s.format(*args)
        except (IndexError, KeyError, ValueError):
            pass
    return s


def strip_amp(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        if s[i] == "&":
            if i + 1 < len(s) and s[i + 1] == "&":
                out.append("&")
                i += 2
                continue
            i += 1
            continue
        out.append(s[i])
        i += 1
    return "".join(out).split("\t")[0]
