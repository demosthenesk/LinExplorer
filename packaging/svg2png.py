"""svg2png.py IN.svg OUT.png SIZE - render with Qt (no rsvg needed)."""
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

app = QGuiApplication(sys.argv[:1])
size = int(sys.argv[3]) if len(sys.argv) > 3 else 256
img = QImage(size, size, QImage.Format.Format_ARGB32)
img.fill(Qt.GlobalColor.transparent)
p = QPainter(img)
QSvgRenderer(sys.argv[1]).render(p)
p.end()
img.save(sys.argv[2])
