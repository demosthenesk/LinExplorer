"""File properties for the Details pane, Details tab and extra view columns."""
from __future__ import annotations

import os
import re
import struct
import zipfile
from xml.etree import ElementTree as ET

from PySide6.QtCore import QSize
from PySide6.QtGui import QImageReader

try:
    import mutagen
    from mutagen.easyid3 import EasyID3  # noqa: F401
except Exception:  # pragma: no cover
    mutagen = None


# --------------------------------------------------------------------------- EXIF
_EXIF_TAGS = {0x010F: "camera_maker", 0x0110: "camera_model", 0x0112: "orientation", 0x9003: "date_taken",
              0x829A: "exposure", 0x829D: "fstop", 0x8827: "iso", 0x920A: "focal", 0x9209: "flash",
              0x8769: "_exif", 0xA002: "px_w", 0xA003: "px_h", 0x0131: "software", 0x013B: "artist",
              0x8298: "copyright", 0x010E: "description"}


def read_exif(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"\xff\xd8":
                return {}
            while True:
                b = f.read(2)
                if len(b) < 2 or b[0] != 0xFF:
                    return {}
                marker = b[1]
                if marker in (0xD9, 0xDA):
                    return {}
                ln = struct.unpack(">H", f.read(2))[0]
                data = f.read(ln - 2)
                if marker == 0xE1 and data[:6] == b"Exif\0\0":
                    return _parse_tiff(data[6:])
    except (OSError, struct.error):
        return {}


def _parse_tiff(t: bytes) -> dict:
    out = {}
    if len(t) < 8:
        return out
    end = "<" if t[:2] == b"II" else ">"

    def u16(o):
        return struct.unpack_from(end + "H", t, o)[0]

    def u32(o):
        return struct.unpack_from(end + "I", t, o)[0]

    def ifd(off, depth=0):
        if off <= 0 or off + 2 > len(t) or depth > 2:
            return
        n = u16(off)
        for i in range(n):
            e = off + 2 + i * 12
            if e + 12 > len(t):
                return
            tag, typ, cnt = u16(e), u16(e + 2), u32(e + 4)
            name = _EXIF_TAGS.get(tag)
            if not name:
                continue
            size = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}.get(typ, 1) * cnt
            voff = e + 8 if size <= 4 else u32(e + 8)
            if voff + size > len(t):
                continue
            if name == "_exif":
                ifd(u32(e + 8), depth + 1)
                continue
            if typ == 2:
                val = t[voff:voff + cnt].split(b"\0")[0].decode("utf-8", "replace").strip()
            elif typ == 3:
                val = u16(voff)
            elif typ == 4:
                val = u32(voff)
            elif typ in (5, 10):
                a, b = struct.unpack_from(end + ("II" if typ == 5 else "ii"), t, voff)
                val = (a, b)
            else:
                continue
            out[name] = val
    try:
        ifd(u32(4))
    except struct.error:
        pass
    return out


def _ratio(v):
    if isinstance(v, tuple) and v[1]:
        return v[0] / v[1]
    return None


# --------------------------------------------------------------------------- readers
def read(item) -> dict:
    """Return a dict of property-key -> display value (strings/ints)."""
    from . import util
    from .i18n import T
    path = item.path
    props: dict = {}
    if item.kind == "zipfile":
        props["compressed_size"] = util.fmt_size_short(item.extra.get("csize", 0))
        return props
    if item.is_dir:
        return props
    mt = item.mime.name()
    try:
        if mt.startswith("image/"):
            r = QImageReader(path)
            sz: QSize = r.size()
            if sz.isValid():
                props["dimensions"] = f"{sz.width()} x {sz.height()}"
                props["width"] = T("n_pixels", sz.width())
                props["height"] = T("n_pixels", sz.height())
            if mt in ("image/jpeg", "image/jpg"):
                ex = read_exif(path)
                if ex.get("date_taken"):
                    m = re.match(r"(\d{4}):(\d\d):(\d\d) (\d\d):(\d\d):(\d\d)", str(ex["date_taken"]))
                    if m:
                        import datetime as _d
                        try:
                            ts = _d.datetime(*map(int, m.groups())).timestamp()
                            props["date_taken"] = util.fmt_datetime(ts)
                            props["_date_taken_ts"] = ts
                        except ValueError:
                            pass
                for k in ("camera_maker", "camera_model", "software", "artist", "copyright", "description"):
                    if ex.get(k):
                        props[k] = str(ex[k])
                if ex.get("exposure"):
                    r_ = _ratio(ex["exposure"])
                    if r_:
                        props["exposure"] = f"1/{round(1 / r_)} sec." if r_ < 1 else f"{r_:g} sec."
                if ex.get("fstop"):
                    r_ = _ratio(ex["fstop"])
                    if r_:
                        props["fstop"] = f"f/{r_:.1f}"
                if ex.get("iso"):
                    props["iso"] = f"ISO-{ex['iso']}"
                if ex.get("focal"):
                    r_ = _ratio(ex["focal"])
                    if r_:
                        props["focal"] = f"{round(r_)} mm"
            if mt == "image/png":
                props["bit_depth"] = _png_depth(path)
        elif (mt.startswith("audio/") or mt.startswith("video/") or mt in ("application/ogg",)) and mutagen:
            props.update(read_media(path))
        elif mt == "application/pdf":
            n = _pdf_pages(path)
            if n:
                props["pages"] = n
            props.update(_pdf_info(path))
        elif mt.startswith("application/vnd.openxmlformats-officedocument"):
            props.update(_ooxml_props(path))
        elif mt.startswith("application/vnd.oasis.opendocument"):
            props.update(_odf_props(path))
        elif mt == "application/x-desktop":
            from .mime import DesktopEntry
            e = DesktopEntry(path, os.path.basename(path))
            if e.comment:
                props["description"] = e.comment
            if e.exec:
                props["target"] = e.exec
    except Exception:
        pass
    return props


def _png_depth(path):
    try:
        with open(path, "rb") as f:
            h = f.read(29)
        if h[:8] == b"\x89PNG\r\n\x1a\n":
            depth, ctype = h[24], h[25]
            ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype, 1)
            return depth * ch
    except OSError:
        pass
    return None


def read_media(path: str) -> dict:
    from . import util
    props = {}
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        f = None
    if f is None:
        return props
    info = getattr(f, "info", None)
    if info is not None:
        if getattr(info, "length", 0):
            props["length"] = util.fmt_duration(info.length)
            props["_length_s"] = info.length
        br = getattr(info, "bitrate", 0)
        if br:
            props["bitrate"] = f"{round(br / 1000)}kbps"
        sr = getattr(info, "sample_rate", 0)
        if sr:
            props["sample_rate"] = f"{sr / 1000:g} kHz"
        ch = getattr(info, "channels", 0)
        if ch:
            props["channels"] = "2 (stereo)" if ch == 2 else "1 (mono)" if ch == 1 else str(ch)
        w = getattr(info, "width", 0)
        h = getattr(info, "height", 0)
        if w and h:
            props["frame_width"] = w
            props["frame_height"] = h
    tags = f.tags or {}

    def first(*keys):
        for k in keys:
            v = tags.get(k) if hasattr(tags, "get") else None
            if v:
                return str(v[0]) if isinstance(v, list) else str(v)
        return ""

    for k, keys in (("title", ("title",)), ("artists", ("artist",)), ("album", ("album",)),
                    ("album_artist", ("albumartist", "album artist")), ("genre", ("genre",)),
                    ("year", ("date", "year")), ("track", ("tracknumber",)), ("composers", ("composer",)),
                    ("comments", ("comment", "description"))):
        v = first(*keys)
        if v:
            if k == "year":
                v = v[:4]
            if k == "track":
                v = v.split("/")[0]
            props[k] = v
    # rating (POPM for ID3, RATING for vorbis)
    try:
        raw = mutagen.File(path)
        if raw is not None and raw.tags is not None:
            for key in list(raw.tags.keys()):
                if str(key).startswith("POPM"):
                    r = raw.tags[key].rating
                    props["rating"] = 0 if r == 0 else 1 if r < 64 else 2 if r < 128 else 3 if r < 196 else 4 if r < 255 else 5
                    break
            else:
                for key in ("RATING", "rating"):
                    if key in raw.tags:
                        v = raw.tags[key]
                        v = int(str(v[0] if isinstance(v, list) else v) or 0)
                        props["rating"] = v // 20 if v > 5 else v
                        break
    except Exception:
        pass
    return props


EDITABLE_AUDIO = ("title", "artists", "album", "album_artist", "genre", "year", "track", "composers", "comments")
_AUDIO_KEYMAP = {"title": "title", "artists": "artist", "album": "album", "album_artist": "albumartist",
                 "genre": "genre", "year": "date", "track": "tracknumber", "composers": "composer",
                 "comments": "comment"}


def can_edit_tags(item) -> bool:
    if mutagen is None or item.kind != "file":
        return False
    mt = item.mime.name()
    return (mt.startswith("audio/") or mt in ("application/ogg", "video/mp4")) and os.access(item.path, os.W_OK)


def write_tags(path: str, values: dict) -> str | None:
    """Write edited tags; returns an error message or None."""
    try:
        f = mutagen.File(path, easy=True)
        if f is None:
            return "unsupported"
        if f.tags is None:
            try:
                f.add_tags()
            except Exception:
                pass
        for k, v in values.items():
            if k == "rating":
                continue
            key = _AUDIO_KEYMAP.get(k)
            if not key:
                continue
            try:
                if v:
                    f[key] = [v]
                elif key in f:
                    del f[key]
            except Exception:
                pass
        f.save()
        if "rating" in values:
            _write_rating(path, int(values["rating"] or 0))
    except Exception as e:
        return str(e)
    return None


def _write_rating(path: str, stars: int):
    raw = mutagen.File(path)
    if raw is None:
        return
    from mutagen.id3 import ID3, POPM
    if isinstance(raw.tags, ID3):
        val = [0, 1, 64, 128, 196, 255][max(0, min(5, stars))]
        raw.tags.delall("POPM")
        if stars:
            raw.tags.add(POPM(email="Windows Media Player 9 Series", rating=val, count=0))
        raw.save()
    elif raw.tags is not None and hasattr(raw.tags, "__setitem__"):
        try:
            if stars:
                raw.tags["RATING"] = str(stars * 20)
            elif "RATING" in raw.tags:
                del raw.tags["RATING"]
            raw.save()
        except Exception:
            pass


def strip_properties(path: str, out_path: str) -> bool:
    """'Create a copy with all possible properties removed'."""
    import shutil
    mt_l = path.lower()
    try:
        if mt_l.endswith((".jpg", ".jpeg")):
            with open(path, "rb") as f:
                data = f.read()
            out = bytearray(data[:2])
            i = 2
            while i + 4 <= len(data) and data[i] == 0xFF:
                m = data[i + 1]
                if m in (0xDA, 0xD9):
                    out += data[i:]
                    break
                ln = struct.unpack(">H", data[i + 2:i + 4])[0]
                seg = data[i:i + 2 + ln]
                if m not in (0xE1, 0xED, 0xFE):      # EXIF/XMP, IPTC, comments
                    out += seg
                i += 2 + ln
            with open(out_path, "wb") as f:
                f.write(out)
            return True
        if mutagen:
            shutil.copy2(path, out_path)
            f = mutagen.File(out_path)
            if f is not None:
                f.delete()
                return True
            os.remove(out_path)
    except Exception:
        pass
    return False


def _pdf_pages(path: str):
    try:
        from PySide6.QtPdf import QPdfDocument
        doc = QPdfDocument()
        doc.load(path)
        n = doc.pageCount()
        doc.close()
        return n or None
    except Exception:
        return None


def _pdf_info(path: str) -> dict:
    out = {}
    try:
        from PySide6.QtPdf import QPdfDocument
        doc = QPdfDocument()
        doc.load(path)
        for key, f in (("title", QPdfDocument.MetaDataField.Title), ("authors", QPdfDocument.MetaDataField.Author),
                       ("subject", QPdfDocument.MetaDataField.Subject),
                       ("program", QPdfDocument.MetaDataField.Producer)):
            v = doc.metaData(f)
            if v:
                out[key] = str(v)
        doc.close()
    except Exception:
        pass
    return out


def _ooxml_props(path: str) -> dict:
    out = {}
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "docProps/core.xml" in names:
                root = ET.fromstring(z.read("docProps/core.xml"))
                for el in root:
                    tag = el.tag.split("}")[-1]
                    if el.text:
                        if tag == "title":
                            out["title"] = el.text
                        elif tag == "creator":
                            out["authors"] = el.text
                        elif tag == "subject":
                            out["subject"] = el.text
                        elif tag == "keywords":
                            out["tags"] = el.text
                        elif tag == "lastModifiedBy":
                            out["last_saved_by"] = el.text
                        elif tag == "revision":
                            out["revision"] = el.text
            if "docProps/app.xml" in names:
                root = ET.fromstring(z.read("docProps/app.xml"))
                for el in root:
                    tag = el.tag.split("}")[-1]
                    if el.text:
                        if tag == "Pages":
                            out["pages"] = el.text
                        elif tag == "Words":
                            out["words"] = el.text
                        elif tag == "Application":
                            out["program"] = el.text
                        elif tag == "Template":
                            out["template"] = el.text
    except Exception:
        pass
    return out


def _odf_props(path: str) -> dict:
    out = {}
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("meta.xml"))
        for el in root.iter():
            tag = el.tag.split("}")[-1]
            if tag == "title" and el.text:
                out["title"] = el.text
            elif tag in ("initial-creator", "creator") and el.text and "authors" not in out:
                out["authors"] = el.text
            elif tag == "subject" and el.text:
                out["subject"] = el.text
            elif tag == "keyword" and el.text:
                out["tags"] = el.text
            elif tag == "generator" and el.text:
                out["program"] = el.text.split("/")[0]
            elif tag == "document-statistic":
                for k, v in el.attrib.items():
                    kk = k.split("}")[-1]
                    if kk == "page-count":
                        out["pages"] = v
                    elif kk == "word-count":
                        out["words"] = v
    except Exception:
        pass
    return out
