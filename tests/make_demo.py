"""Build a demo home folder for tests and screenshots."""
import os, sys, zipfile, shutil, struct, wave, math

def make(home):
    if os.path.exists(home):
        shutil.rmtree(home)
    os.makedirs(home)
    dirs = {"DESKTOP": "Desktop", "DOWNLOAD": "Downloads", "DOCUMENTS": "Documents", "MUSIC": "Music",
            "PICTURES": "Pictures", "VIDEOS": "Videos", "TEMPLATES": "Templates", "PUBLICSHARE": "Public"}
    os.makedirs(os.path.join(home, ".config"))
    with open(os.path.join(home, ".config", "user-dirs.dirs"), "w") as f:
        for k, v in dirs.items():
            os.makedirs(os.path.join(home, v))
            f.write(f'XDG_{k}_DIR="$HOME/{v}"\n')
    from PySide6.QtGui import QImage, QColor, QPainter, QLinearGradient, QFont
    from PySide6.QtCore import Qt
    pics = os.path.join(home, "Pictures")
    names = ["Beach sunset", "Mountain lake", "City at night", "Autumn forest", "Desert dunes", "Snowy cabin"]
    cols = [("#ff9a3c", "#5b2a86"), ("#5fc3e4", "#1d6b3a"), ("#0b1d51", "#e94560"), ("#e67e22", "#7b3f00"),
            ("#f6d365", "#c0392b"), ("#dfe9f3", "#5d8aa8")]
    for n, (a, b) in zip(names, cols):
        img = QImage(1200, 800, QImage.Format.Format_RGB32)
        p = QPainter(img)
        g = QLinearGradient(0, 0, 0, 800)
        g.setColorAt(0, QColor(a)); g.setColorAt(1, QColor(b))
        p.fillRect(img.rect(), g)
        p.setPen(QColor(255, 255, 255, 180)); f = QFont(); f.setPointSize(60); p.setFont(f)
        p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, n)
        p.end()
        img.save(os.path.join(pics, n + ".jpg"), quality=85)
    docs = os.path.join(home, "Documents")
    for i, n in enumerate(["Budget 2026.txt", "Meeting notes.txt", "Recipe - moussaka.txt", "README.md", "todo.txt"]):
        with open(os.path.join(docs, n), "w") as f:
            f.write(f"# {n}\n\n" + ("Lorem ipsum dolor sit amet. " * (20 * (i + 1))) + "\nΚαλημέρα κόσμε!\n")
    os.makedirs(os.path.join(docs, "Projects", "LinExplorer"))
    os.makedirs(os.path.join(docs, "Invoices"))
    for i in range(1, 13):
        with open(os.path.join(docs, "Invoices", f"invoice-{i}.txt"), "w") as f:
            f.write(f"Invoice {i}\nTotal: {i * 37} EUR\n")
    with zipfile.ZipFile(os.path.join(docs, "Archive.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("readme.txt", "inside the zip\n" * 50)
        z.writestr("folder/a.txt", "A" * 1000)
        z.writestr("folder/b.txt", "B" * 2000)
        z.writestr("folder/sub/c.txt", "C" * 3000)
    music = os.path.join(home, "Music")
    for i, n in enumerate(["01 - Morning.wav", "02 - Noon.wav", "03 - Night.wav"]):
        w = wave.open(os.path.join(music, n), "w")
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(t * (i + 1) * 0.05))) for t in range(8000)))
        w.close()
    os.symlink(os.path.join(docs, "Budget 2026.txt"), os.path.join(home, "Desktop", "Budget 2026 - Shortcut"))
    with open(os.path.join(home, "Templates", "Empty document.odt"), "wb") as f:
        f.write(b"")
    with open(os.path.join(home, ".hidden_file"), "w") as f:
        f.write("x")

if __name__ == "__main__":
    from PySide6.QtGui import QGuiApplication
    _app = QGuiApplication(sys.argv[:1])
    make(sys.argv[1])
