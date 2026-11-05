#!/bin/bash
# Build the LinExplorer AppImage on Ubuntu 24.04 (x86_64).
# Needs: python3, python3-pip, curl, unzip, file, binutils; the xcb helper libraries
# (libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-render-util0 libxkbcommon-x11-0).
#
# The AppImage bundles a pruned PySide6 (Qt 6.8) + mutagen and runs on the host's /usr/bin/python3 (3.9+).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$HERE/linexplorer/__init__.py")"
BUILD="${BUILD:-/tmp/linexplorer-build}"
OUT="${OUT:-$HERE/dist}"
PYSIDE="${PYSIDE:-6.8.1}"
APPDIR="$BUILD/LinExplorer.AppDir"
PYLIB="$APPDIR/usr/lib/python"

echo ">> LinExplorer $VERSION"
rm -rf "$BUILD"
mkdir -p "$BUILD/wheels" "$PYLIB" "$APPDIR/usr/lib" "$OUT"

echo ">> downloading wheels"
python3 -m pip download -q --only-binary=:all: --no-deps --python-version 3.12 --platform manylinux_2_28_x86_64 \
    -d "$BUILD/wheels" "PySide6-Essentials==$PYSIDE" "PySide6-Addons==$PYSIDE" "shiboken6==$PYSIDE"
python3 -m pip download -q --only-binary=:all: --no-deps -d "$BUILD/wheels" "mutagen==1.47.0"
for w in "$BUILD"/wheels/*.whl; do unzip -q -o "$w" -d "$PYLIB"; done
rm -rf "$PYLIB"/*.dist-info

echo ">> pruning PySide6"
python3 "$HERE/packaging/prune_pyside.py" "$PYLIB/PySide6"

echo ">> application"
cp -r "$HERE/linexplorer" "$PYLIB/"
find "$PYLIB" -name "__pycache__" -type d -prune -exec rm -rf {} +
cat > "$PYLIB/linexplorer_run.py" <<'EOF'
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from linexplorer.app import main
sys.exit(main())
EOF
python3 -m compileall -q "$PYLIB/linexplorer" "$PYLIB/mutagen" || true

echo ">> xcb helper libraries"
for lib in libxcb-cursor.so.0 libxcb-icccm.so.4 libxcb-keysyms.so.1 libxcb-image.so.0 libxcb-render-util.so.0 \
           libxkbcommon-x11.so.0 libxcb-util.so.1; do
    src="$(ldconfig -p | awk -v l="$lib" '$1 == l && /x86-64/ {print $NF; exit}')"
    if [ -n "$src" ]; then cp -L "$src" "$APPDIR/usr/lib/"; else echo "   (missing $lib on build host)"; fi
done

echo ">> desktop integration"
cp "$HERE/linexplorer/resources/linexplorer.svg" "$APPDIR/linexplorer.svg"
QT_QPA_PLATFORM=offscreen PYTHONPATH="$PYLIB" python3 "$HERE/packaging/svg2png.py" "$APPDIR/linexplorer.svg" "$APPDIR/linexplorer.png" 256
cp "$APPDIR/linexplorer.png" "$APPDIR/.DirIcon"
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/metainfo"
cp "$APPDIR/linexplorer.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
cat > "$APPDIR/linexplorer.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=LinExplorer
GenericName=File Manager
GenericName[el]=Διαχείριση αρχείων
Comment=Browse files and folders the Windows 7 way
Comment[el]=Περιήγηση σε αρχεία και φακέλους όπως στα Windows 7
Exec=linexplorer %U
Icon=linexplorer
Terminal=false
Categories=System;FileTools;FileManager;Utility;
MimeType=inode/directory;
StartupWMClass=linexplorer
X-AppImage-Version=$VERSION
EOF
cp "$APPDIR/linexplorer.desktop" "$APPDIR/usr/share/applications/"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export LINEXPLORER_ORIG_LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
export LD_LIBRARY_PATH="$HERE/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LINEXPLORER_APPDIR="$HERE"
PY=""
for cand in /usr/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)"; do
    [ -n "$cand" ] && [ -x "$cand" ] || continue
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
    msg="LinExplorer needs Python 3.9 or newer (python3)."
    command -v zenity >/dev/null && zenity --error --text="$msg" || echo "$msg" >&2
    exit 1
fi
exec "$PY" -s -E "$HERE/usr/lib/python/linexplorer_run.py" "$@"
EOF
chmod +x "$APPDIR/AppRun"

echo ">> appimagetool"
TOOL="$BUILD/appimagetool"
if [ -n "${APPIMAGETOOL:-}" ] && [ -x "$APPIMAGETOOL" ]; then TOOL="$APPIMAGETOOL"; else
    curl -fsSL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi
TARGET="$OUT/LinExplorer-$VERSION-x86_64.AppImage"
rm -f "$TARGET"
ARCH=x86_64 "$TOOL" --appimage-extract-and-run --no-appstream "$APPDIR" "$TARGET" >/dev/null
( cd "$OUT" && sha256sum "$(basename "$TARGET")" > "$(basename "$TARGET").sha256" )
du -h "$TARGET"
cat "$TARGET.sha256"
