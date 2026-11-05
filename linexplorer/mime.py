"""File types, .desktop applications, default programs and launching."""
from __future__ import annotations

import configparser
import glob
import os
import shlex
import stat

from PySide6.QtCore import QMimeDatabase, QUrl
from PySide6.QtGui import QDesktopServices

from . import util

_db = QMimeDatabase()


def db() -> QMimeDatabase:
    return _db


def mime_for(path: str, is_dir: bool = False, content: bool = False):
    if is_dir:
        return _db.mimeTypeForName("inode/directory")
    mt = _db.mimeTypeForFile(path, QMimeDatabase.MatchMode.MatchExtension)
    if mt.isDefault() or content:
        try:
            mt2 = _db.mimeTypeForFile(path, QMimeDatabase.MatchMode.MatchContent)
            if not mt2.isDefault() or content:
                mt = mt2
        except Exception:
            pass
    return mt


def mime_for_name(name: str):
    return _db.mimeTypeForFile(name, QMimeDatabase.MatchMode.MatchExtension)


def type_description(mt, name: str = "") -> str:
    from .i18n import T
    n = mt.name()
    if n == "inode/directory":
        return T("file_folder")
    if mt.isDefault():
        _, ext = util.split_ext(name)
        if ext:
            return T("ext_file", ext[1:].upper())
        return T("file")
    c = mt.comment()
    return c[:1].upper() + c[1:] if c else n


def perceived(mt) -> str:
    """Windows 'kind' for a MIME type."""
    n = mt.name()
    if n == "inode/directory":
        return "folder"
    if any(mt.inherits(x) for x in ("image/*",)) or n.startswith("image/"):
        return "picture"
    if n.startswith("audio/") or n in ("application/ogg", "application/x-ogg"):
        return "music"
    if n.startswith("video/"):
        return "video"
    if n.startswith("text/") or mt.inherits("text/plain"):
        return "document"
    if n in ("application/pdf", "application/rtf") or "officedocument" in n or "opendocument" in n \
            or "msword" in n or "ms-excel" in n or "ms-powerpoint" in n or n == "application/epub+zip":
        return "document"
    if n in ("application/zip", "application/x-7z-compressed", "application/x-tar", "application/gzip",
             "application/x-compressed-tar", "application/x-bzip2", "application/x-xz", "application/x-rar",
             "application/vnd.rar", "application/x-xz-compressed-tar", "application/zstd"):
        return "compressed"
    if n in ("application/x-executable", "application/x-sharedlib", "application/x-pie-executable",
             "application/vnd.appimage", "application/x-shellscript", "application/x-desktop",
             "application/x-ms-dos-executable"):
        return "program"
    if n.startswith("font/") or "font" in n:
        return "font"
    return "other"


# --------------------------------------------------------------------------- desktop entries
def xdg_data_dirs() -> list[str]:
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    out = [home] + [d for d in dirs.split(":") if d]
    for extra in ("/var/lib/flatpak/exports/share", os.path.expanduser("~/.local/share/flatpak/exports/share"),
                  "/var/lib/snapd/desktop", "/usr/share"):
        if extra not in out and os.path.isdir(extra):
            out.append(extra)
    return out


class DesktopEntry:
    def __init__(self, path: str, did: str):
        self.path = path
        self.id = did
        cp = configparser.RawConfigParser(strict=False, interpolation=None)
        cp.optionxform = str
        try:
            cp.read(path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError, OSError):
            pass
        sec = cp["Desktop Entry"] if cp.has_section("Desktop Entry") else {}
        from .i18n import lang_code
        lc = lang_code()
        self.name = sec.get(f"Name[{lc}]") or sec.get("Name", did)
        self.generic = sec.get(f"GenericName[{lc}]") or sec.get("GenericName", "")
        self.comment = sec.get(f"Comment[{lc}]") or sec.get("Comment", "")
        self.icon = sec.get("Icon", "")
        self.exec = sec.get("Exec", "")
        self.try_exec = sec.get("TryExec", "")
        self.type = sec.get("Type", "Application")
        self.url = sec.get("URL", "")
        self.terminal = sec.get("Terminal", "false").lower() == "true"
        self.nodisplay = sec.get("NoDisplay", "false").lower() == "true"
        self.hidden = sec.get("Hidden", "false").lower() == "true"
        self.mimetypes = [m for m in sec.get("MimeType", "").split(";") if m]
        self.path_dir = sec.get("Path", "")
        self.only_show_in = [x for x in sec.get("OnlyShowIn", "").split(";") if x]

    def valid(self) -> bool:
        if self.hidden or self.type != "Application" or not self.exec:
            return False
        if self.try_exec and not (os.path.isabs(self.try_exec) and os.path.exists(self.try_exec)) \
                and not util.which(self.try_exec):
            return False
        return True

    def qicon(self):
        from . import icons
        return icons.theme(self.icon, "application-x-executable", fallback="file")

    def command(self, files: list[str]) -> list[list[str]]:
        """Expand field codes; returns one or more argv lists."""
        try:
            parts = shlex.split(self.exec)
        except ValueError:
            parts = self.exec.split()
        multi = any(p in ("%F", "%U") for p in parts)
        single = any(p in ("%f", "%u") for p in parts)

        def expand(fs):
            out = []
            for p in parts:
                if p in ("%F", "%U"):
                    out.extend(QUrl.fromLocalFile(f).toString() if p == "%U" else f for f in fs)
                elif p in ("%f", "%u"):
                    if fs:
                        out.append(QUrl.fromLocalFile(fs[0]).toString() if p == "%u" else fs[0])
                elif p == "%i":
                    if self.icon:
                        out += ["--icon", self.icon]
                elif p == "%c":
                    out.append(self.name)
                elif p == "%k":
                    out.append(self.path)
                elif p in ("%d", "%D", "%n", "%N", "%v", "%m"):
                    continue
                else:
                    out.append(p.replace("%%", "%"))
            return out

        if not files or multi:
            cmds = [expand(files)]
        elif single:
            cmds = [expand([f]) for f in files]
        else:
            cmds = [expand([]) + list(files)]
        if self.terminal:
            term = terminal_command()
            cmds = [term + c for c in cmds] if term else cmds
        return cmds

    def launch(self, files: list[str] | None = None) -> bool:
        ok = True
        cwd = self.path_dir or (os.path.dirname(files[0]) if files else None)
        for argv in self.command(files or []):
            ok = util.spawn(argv, cwd=cwd) and ok
        return ok


_apps: dict[str, DesktopEntry] | None = None


def all_apps(reload: bool = False) -> dict[str, DesktopEntry]:
    global _apps
    if _apps is not None and not reload:
        return _apps
    apps: dict[str, DesktopEntry] = {}
    for d in xdg_data_dirs():
        base = os.path.join(d, "applications")
        if not os.path.isdir(base):
            continue
        for path in glob.glob(os.path.join(base, "**", "*.desktop"), recursive=True):
            did = os.path.relpath(path, base).replace("/", "-")
            if did in apps:
                continue
            e = DesktopEntry(path, did)
            apps[did] = e
    _apps = {k: v for k, v in apps.items() if v.valid()}
    return _apps


def app_by_id(did: str) -> DesktopEntry | None:
    return all_apps().get(did)


def _mimeapps_files() -> list[str]:
    cfg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    files = [os.path.join(cfg, "mimeapps.list")]
    for d in (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":"):
        files.append(os.path.join(d, "mimeapps.list"))
    for d in xdg_data_dirs():
        files.append(os.path.join(d, "applications", "mimeapps.list"))
        files.append(os.path.join(d, "applications", "defaults.list"))
    return files


def _read_list(path: str) -> configparser.RawConfigParser:
    cp = configparser.RawConfigParser(strict=False, interpolation=None, delimiters=("=",))
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        pass
    return cp


def _mime_chain(mt) -> list[str]:
    chain = [mt.name()] + [a for a in mt.aliases()]
    for p in mt.allAncestors():
        if p not in chain:
            chain.append(p)
    return chain


def default_app(mt) -> DesktopEntry | None:
    apps = all_apps()
    for mname in _mime_chain(mt):
        for f in _mimeapps_files():
            cp = _read_list(f)
            for sec in ("Default Applications",):
                if cp.has_section(sec) and cp.has_option(sec, mname):
                    for did in cp.get(sec, mname).split(";"):
                        did = did.strip()
                        if did in apps:
                            return apps[did]
        cands = apps_for(mt, chain_only=[mname])
        if cands:
            return cands[0]
    return None


def apps_for(mt, chain_only: list[str] | None = None) -> list[DesktopEntry]:
    chain = chain_only or _mime_chain(mt)
    apps = all_apps()
    removed = set()
    added: list[str] = []
    for f in _mimeapps_files():
        cp = _read_list(f)
        for m in chain:
            if cp.has_section("Removed Associations") and cp.has_option("Removed Associations", m):
                removed.update(x for x in cp.get("Removed Associations", m).split(";") if x)
            if cp.has_section("Added Associations") and cp.has_option("Added Associations", m):
                added += [x for x in cp.get("Added Associations", m).split(";") if x]
    out: list[DesktopEntry] = []
    seen = set()
    for did in added:
        if did in apps and did not in seen and did not in removed:
            out.append(apps[did])
            seen.add(did)
    for m in chain:
        for did, e in sorted(apps.items(), key=lambda kv: kv[1].name.casefold()):
            if did in seen or did in removed:
                continue
            if m in e.mimetypes or any(x.endswith("/*") and m.startswith(x[:-1]) for x in e.mimetypes):
                out.append(e)
                seen.add(did)
    return out


def set_default_app(mt, entry: DesktopEntry):
    cfg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(cfg, "mimeapps.list")
    cp = _read_list(path)
    if not cp.has_section("Default Applications"):
        cp.add_section("Default Applications")
    if not cp.has_section("Added Associations"):
        cp.add_section("Added Associations")
    cp.set("Default Applications", mt.name(), entry.id)
    cur = [x for x in (cp.get("Added Associations", mt.name()) if cp.has_option("Added Associations", mt.name()) else "").split(";") if x]
    if entry.id in cur:
        cur.remove(entry.id)
    cp.set("Added Associations", mt.name(), ";".join([entry.id] + cur) + ";")
    os.makedirs(cfg, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        cp.write(f, space_around_delimiters=False)


def terminal_command() -> list[str] | None:
    for t, args in (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("ptyxis", ["--"]),
                    ("kgx", ["--"]), ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]), ("mate-terminal", ["-x"]),
                    ("tilix", ["-e"]), ("xterm", ["-e"])):
        if util.which(t):
            return [t] + args
    return None


def open_terminal(folder: str) -> bool:
    for t, args in (("x-terminal-emulator", []), ("gnome-terminal", ["--working-directory=" + folder]),
                    ("ptyxis", ["--working-directory=" + folder]), ("kgx", ["--working-directory=" + folder]),
                    ("konsole", ["--workdir", folder]), ("xfce4-terminal", ["--working-directory=" + folder]),
                    ("mate-terminal", ["--working-directory=" + folder]), ("tilix", ["-w", folder]), ("xterm", [])):
        if util.which(t):
            return util.spawn([t] + args, cwd=folder)
    return False


# --------------------------------------------------------------------------- opening files
def is_executable_file(path: str) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and os.access(path, os.X_OK)


def is_elf_or_appimage(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def open_with_default(path: str) -> bool:
    mt = mime_for(path)
    if mt.name() == "application/x-desktop" or path.endswith(".desktop"):
        e = DesktopEntry(path, os.path.basename(path))
        if e.type == "Link" and e.url:
            return QDesktopServices.openUrl(QUrl(e.url))
        if e.exec:
            return e.launch([])
    app = default_app(mt)
    if app is not None:
        return app.launch([path])
    if util.which("xdg-open"):
        return util.spawn(["xdg-open", path])
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def open_files_with(entry: DesktopEntry, paths: list[str]) -> bool:
    return entry.launch(paths)
