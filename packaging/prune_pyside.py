"""Keep only the parts of PySide6 LinExplorer needs (bindings, Qt libraries, plugins)."""
import os
import re
import shutil
import subprocess
import sys

root = sys.argv[1]
KEEP_MODULES = ["QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtSvg", "QtPdf", "QtPdfWidgets", "QtMultimedia",
                "QtMultimediaWidgets", "QtDBus"]
KEEP_PLUGINS = {
    "platforms": ["libqxcb.so", "libqwayland-egl.so", "libqwayland-generic.so", "libqoffscreen.so", "libqminimal.so"],
    "platformthemes": None, "imageformats": None, "iconengines": None, "multimedia": None,
    "xcbglintegrations": None, "platforminputcontexts": None, "wayland-decoration-client": None,
    "wayland-graphics-integration-client": None, "wayland-shell-integration": None, "tls": None,
    "networkinformation": None, "generic": None,
}

# bindings
for f in os.listdir(root):
    p = os.path.join(root, f)
    m = re.match(r"^(Qt\w+)\.(abi3\.so|pyi)$", f)
    if m and m.group(1) not in KEEP_MODULES:
        os.remove(p)
for d in ("examples", "include", "typesystems", "glue", "scripts", "doc", "qml", "lupdate", "Qt/qml",
          "Qt/translations", "Qt/libexec", "Qt/metatypes", "Qt/resources"):
    shutil.rmtree(os.path.join(root, d), ignore_errors=True)
for f in os.listdir(root):
    if f.endswith((".exe",)) or f in ("assistant", "designer", "linguist", "lrelease", "lupdate", "qmlformat",
                                     "qmllint", "qmlls", "balsam", "balsamui", "svgtoqml", "qsb", "uic", "rcc",
                                     "androiddeploy", "deploy_lib"):
        p = os.path.join(root, f)
        shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
# plugins
pdir = os.path.join(root, "Qt", "plugins")
for d in os.listdir(pdir):
    full = os.path.join(pdir, d)
    if d not in KEEP_PLUGINS:
        shutil.rmtree(full)
        continue
    keep = KEEP_PLUGINS[d]
    if keep is not None:
        for f in os.listdir(full):
            if f not in keep:
                os.remove(os.path.join(full, f))


# Qt libraries: transitive closure of what the kept binaries need
libdir = os.path.join(root, "Qt", "lib")


def needed(path):
    try:
        out = subprocess.run(["objdump", "-p", path], capture_output=True, text=True).stdout
    except OSError:
        return []
    return re.findall(r"NEEDED\s+(\S+)", out)


starts = [os.path.join(root, f) for f in os.listdir(root) if f.endswith(".so") or ".so." in f]
for dp, dn, fn in os.walk(pdir):
    starts += [os.path.join(dp, f) for f in fn if f.endswith(".so")]
want = set()
todo = list(starts)
while todo:
    p = todo.pop()
    for n in needed(p):
        if n in want:
            continue
        cand = os.path.join(libdir, n)
        if os.path.exists(cand):
            want.add(n)
            todo.append(cand)
# the FFmpeg stubs are dlopen'ed (VA-API, OpenSSL) - keep them
for f in os.listdir(libdir):
    if f.startswith("libQt6FFmpegStub"):
        want.add(f)
removed = 0
for f in os.listdir(libdir):
    p = os.path.join(libdir, f)
    if os.path.isdir(p):
        shutil.rmtree(p)
        continue
    if f not in want:          # wheels hold no symlinks: unversioned copies are dead weight
        os.remove(p)
        removed += 1
size = 0
for dp, dn, fn in os.walk(root):
    for f in fn:
        fp = os.path.join(dp, f)
        if not os.path.islink(fp):
            size += os.path.getsize(fp)
print(f"   kept {len(want)} Qt libraries, removed {removed}; PySide6 now {size / 1e6:.0f} MB")
