"""Import, paste and drop pictures."""

from __future__ import annotations

from ..dialog_buttons import dialog_buttons

import os
from typing import List, NamedTuple, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..prefs.settings import app_settings

SETTING = "opt/transparent_paste"
METAFILE_SUFFIXES = ("wmf", "emf")
#: drawn by QtSvg at the size they are placed at (never stretched afterwards)
SVG_SUFFIXES = ("svg", "svgz")
#: File > Import Recent: the pictures imported last, and the ones kept as favourites
RECENT_KEY = "import/recent"
FAVOURITES_KEY = "import/favourites"
RECENT_MAX = 10
#: file suffixes CtDraw's Import lists (help 1172155) that Qt reads under
#: another format name: Windows bitmaps *.DIB / *.RLE, JPEG *.JIF / *.JFF /
#: *.JGE (a QImage of 'x.dib' is null -- Qt goes by the suffix)
SUFFIX_FORMATS = {"dib": "bmp", "rle": "bmp", "jif": "jpeg", "jff": "jpeg", "jge": "jpeg"}


def _qt_formats() -> set:
    return {bytes(f).decode("ascii", "replace").lower() for f in QtGui.QImageReader.supportedImageFormats()}


def raster_suffixes() -> List[str]:
    """Every picture format this Qt can read (QImageReader), plus CtDraw's
    other names for bitmap / JPEG files."""
    fmts = _qt_formats()
    return sorted(fmts | {s for s, f in SUFFIX_FORMATS.items() if f in fmts})


def importable_suffixes() -> List[str]:
    return sorted(set(raster_suffixes()) | set(METAFILE_SUFFIXES) | set(SVG_SUFFIXES))


def is_importable(path: str) -> bool:
    return os.path.splitext(path)[1].lstrip(".").lower() in importable_suffixes()


def import_filter(metafiles: bool = True) -> str:
    """The file dialog filter: all graphics first, then per kind."""
    rs = sorted(set(raster_suffixes()) | set(SVG_SUFFIXES))
    every = rs + (list(METAFILE_SUFFIXES) if metafiles else [])
    parts = ["Graphics files (%s)" % " ".join("*." + s for s in every)]
    parts.append("Bitmaps (%s)" % " ".join("*." + s for s in rs))
    if metafiles:
        parts.append("Windows Metafiles (*.wmf *.emf)")
    parts.append("All files (*.*)")
    return ";;".join(parts)


def transparent_colour() -> Optional[int]:
    """The Transparent Paste colour (0x00RRGGBB), or None when none is set."""
    v = str(app_settings().value(SETTING, "", type=str) or "").strip()
    try:
        return int(v, 16) & 0xFFFFFF if v else None
    except ValueError:
        return None


def set_transparent_colour(colour: Optional[int]) -> None:
    app_settings().setValue(SETTING, "" if colour is None else "%06x" % (colour & 0xFFFFFF))


def apply_key(rgba: bytes, key: Optional[int]) -> bytes:
    """``rgba`` with every pixel of colour ``key`` made transparent."""
    if key is None:
        return rgba
    import numpy as np

    a = np.frombuffer(bytes(rgba), dtype=np.uint8).reshape(-1, 4).copy()
    hit = (a[:, 0] == (key >> 16) & 0xFF) & (a[:, 1] == (key >> 8) & 0xFF) & (a[:, 2] == key & 0xFF)
    a[hit] = 0
    return a.tobytes()


def qimage_rgba(img: QtGui.QImage):
    """``(rgba, width, height)`` of a QImage (straight alpha, no row padding)."""
    img = img.convertToFormat(QtGui.QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    stride = img.bytesPerLine()
    data = bytes(memoryview(img.constBits()))
    if stride != w * 4:
        data = b"".join(data[y * stride : y * stride + w * 4] for y in range(h))
    return data[: w * h * 4], w, h


class Picture(NamedTuple):
    """A picture ready to place: ``kind`` 'bitmap' (``data`` = RGBA,
    ``width`` x ``height`` pixels) or 'metafile' (``data`` = the memory
    metafile). ``place_w`` / ``place_h`` = its size on the page when it
    differs from the pixels (0 = the pixel size)."""

    kind: str
    data: bytes
    width: int
    height: int
    path: str = ""
    place_w: int = 0
    place_h: int = 0

    @property
    def size(self):
        """Its size on the page."""
        return (self.place_w or self.width, self.place_h or self.height)


class PictureError(Exception):
    """A file that cannot be placed; the message says why, in plain words."""


def _raster(img: QtGui.QImage, path: str, key: Optional[int]) -> Picture:
    from ...format.bitmaps import MAX_SIDE

    if img.isNull():
        raise PictureError("%s could not be read as a picture." % os.path.basename(path))
    if img.width() > MAX_SIDE or img.height() > MAX_SIDE:
        raise PictureError(
            "%s is %d x %d pixels; a bitmap can be at most %d pixels wide "
            "and high. Make it smaller first." % (os.path.basename(path), img.width(), img.height(), MAX_SIDE)
        )
    rgba, w, h = qimage_rgba(img)
    return Picture("bitmap", apply_key(rgba, key), w, h, path)


def read_picture(path: str, key: Optional[int] = None, raster_only: bool = False) -> Picture:
    """Read an importable file. A .wmf is a 'metafile' picture, or with
    ``raster_only`` (Import As Flashing: two bitmaps) drawn to a bitmap.
    ``key`` = the colour made transparent in a bitmap. Raises PictureError."""
    src = open_source(path)
    if src.kind == "raster":
        return _raster(src.image(), path, key)
    return build(src, plain_options(src, key)._replace(as_drawing=not raster_only))


# -- sources: a picture before its size and transparency are chosen ----------------------
class Source:
    """A picture read from a file or the clipboard."""

    def __init__(
        self,
        kind: str,
        name: str,
        width: int,
        height: int,
        path: str = "",
        image: Optional[QtGui.QImage] = None,
        wmf: bytes = b"",
        svg: Optional[bytes] = None,
        emf: bool = False,
    ):
        self.kind, self.name, self.path = kind, name, path
        self.width, self.height = max(1, int(width)), max(1, int(height))
        self._image, self.wmf, self._svg, self.emf = image, wmf, svg, emf
        self.has_alpha = kind == "svg" or bool(
            image is not None and image.hasAlphaChannel() and _any_clear(image)
        )

    @property
    def vector(self) -> bool:
        """It stays sharp at any size (an SVG, a metafile)."""
        return self.kind in ("svg", "metafile")

    def image(self, w: int = 0, h: int = 0) -> QtGui.QImage:
        """The picture drawn at ``w`` x ``h`` (default its own size): a raster picture
        scaled smoothly, an SVG or a metafile drawn at that size."""
        w, h = int(w or self.width), int(h or self.height)
        if self.kind == "raster":
            img = self._image
            if (img.width(), img.height()) != (w, h):
                img = img.convertToFormat(QtGui.QImage.Format_ARGB32_Premultiplied).scaled(
                    w, h, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation
                )
            return img.convertToFormat(QtGui.QImage.Format_ARGB32)
        if self.kind == "svg":
            from PySide6 import QtSvg

            r = QtSvg.QSvgRenderer(QtCore.QByteArray(self._svg))
            img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32_Premultiplied)
            img.fill(0)
            p = QtGui.QPainter(img)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
            r.render(p, QtCore.QRectF(0, 0, w, h))
            p.end()
            return img.convertToFormat(QtGui.QImage.Format_ARGB32)
        from ...format.symbols import render_metafile

        eb = render_metafile(self.wmf, 0, w, h, max_dim=max(w, h))
        if eb is None:
            raise PictureError(
                "%s could not be drawn (it draws nothing, or this computer "
                "cannot play metafiles)." % self.name
            )
        return _image_of(eb.rgba, eb.width, eb.height)


def _any_clear(img: QtGui.QImage) -> bool:
    import numpy as np

    rgba, _w, _h = qimage_rgba(img)
    return bool((np.frombuffer(rgba, dtype=np.uint8)[3::4] < 255).any())


def _image_of(rgba: bytes, w: int, h: int) -> QtGui.QImage:
    return QtGui.QImage(bytes(rgba), w, h, w * 4, QtGui.QImage.Format_RGBA8888).convertToFormat(
        QtGui.QImage.Format_ARGB32
    )


def open_source(path: str) -> Source:
    """Read an importable file as a :class:`Source`. Raises PictureError."""
    name = os.path.basename(path)
    suffix = os.path.splitext(path)[1].lstrip(".").lower()
    if suffix in METAFILE_SUFFIXES or suffix in SVG_SUFFIXES:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            raise PictureError("%s could not be read: %s" % (name, e.strerror or e)) from e
        if suffix in METAFILE_SUFFIXES:
            return metafile_source(data, name, path)
        return svg_source(data, name, path)
    if suffix not in raster_suffixes():
        raise PictureError(
            "%s is not a picture format that can be imported (%s)." % (name, ", ".join(importable_suffixes()))
        )
    if suffix in SUFFIX_FORMATS:
        img = QtGui.QImageReader(path, SUFFIX_FORMATS[suffix].encode("ascii")).read()
    else:
        img = QtGui.QImage(path)
    if img.isNull():
        raise PictureError("%s could not be read as a picture." % name)
    return Source("raster", name, img.width(), img.height(), path, image=img)


def svg_source(data: bytes, name: str, path: str = "") -> Source:
    """An SVG (or gzipped .svgz) drawing as a Source. Raises PictureError."""
    from PySide6 import QtSvg

    if data[:2] == b"\x1f\x8b":
        import gzip

        try:
            data = gzip.decompress(data)
        except (OSError, EOFError) as e:
            raise PictureError("%s could not be read: %s" % (name, e)) from e
    r = QtSvg.QSvgRenderer(QtCore.QByteArray(data))
    if not r.isValid():
        raise PictureError("%s could not be read as an SVG drawing." % name)
    size = r.defaultSize()
    w, h = size.width(), size.height()
    if w < 1 or h < 1:
        vb = r.viewBoxF()
        w, h = round(vb.width()) or 100, round(vb.height()) or 100
    return Source("svg", name, w, h, path, svg=data)


def metafile_source(data: bytes, name: str, path: str = "") -> Source:
    """A .wmf or .emf file's bytes as a 'metafile' Source. Raises PictureError."""
    from ...format.bitmaps import MetafileError, wmf_from_file
    from ...infra import emf

    is_emf = emf.is_emf(data)
    try:
        if is_emf:
            data = emf.to_wmf(data)
        wmf, w, h = wmf_from_file(data)
    except (emf.EmfError, MetafileError) as e:
        raise PictureError("%s cannot be placed: %s." % (name, e)) from e
    return Source("metafile", name, w, h, path, wmf=wmf, emf=is_emf)


def image_source(img: QtGui.QImage, name: str = "the clipboard picture") -> Source:
    if img.isNull():
        raise PictureError("%s could not be read." % name)
    return Source("raster", name, img.width(), img.height(), image=img)


def clipboard_source(mime: Optional[QtCore.QMimeData], drawing: bool = False) -> Optional[Source]:
    """What another program copied, as a Source: with ``drawing`` its enhanced metafile
    when it copied one (a drawing that stays sharp), else its picture. None when neither
    is there."""
    if drawing:
        from ...infra import emf

        data = emf.clipboard_emf()
        if data:
            return metafile_source(data, "the clipboard drawing")
    if mime is None or not mime.hasImage():
        return None
    data = mime.imageData()
    if isinstance(data, QtGui.QPixmap):
        data = data.toImage()
    img = data if isinstance(data, QtGui.QImage) else QtGui.QImage()
    return None if img.isNull() else image_source(img)


# -- what the import makes of a source -------------------------------------------------
#: transparency: none (every pixel shows; transparent ones over white), the picture's own
#: (pixels less opaque than the threshold become transparent) or a colour (within the
#: tolerance)
TRANSPARENCY = (("none", "None"), ("alpha", "The picture's own"), ("colour", "A colour"))


class ImportOptions(NamedTuple):
    """How a Source becomes a Picture."""

    width: int
    height: int
    store_at_size: bool = True
    as_drawing: bool = True
    transparency: str = "alpha"
    threshold: int = 128
    key: int = 0xFFFFFF
    tolerance: int = 0


def plain_options(src: Source, key: Optional[int] = None) -> ImportOptions:
    """CtDraw's import (no dialog): the picture's own size and transparency, and the
    Transparent Paste colour ``key`` made transparent."""
    return ImportOptions(
        src.width,
        src.height,
        transparency="colour" if key is not None else "alpha",
        key=0xFFFFFF if key is None else key,
    )


def make_transparent(rgba: bytes, opts: ImportOptions) -> bytes:
    """``rgba`` with ``opts``'s transparency, each pixel opaque or clear (0, 0, 0, 0) as a
    bitmap record stores it."""
    import numpy as np

    a = np.frombuffer(bytes(rgba), dtype=np.uint8).reshape(-1, 4).copy()
    if opts.transparency == "none":
        # over white: the picture as it shows on a page with nothing behind it
        al = a[:, 3:4].astype(np.uint16)
        a[:, :3] = ((a[:, :3].astype(np.uint16) * al + 255 * (255 - al)) // 255).astype(np.uint8)
        a[:, 3] = 255
        return a.tobytes()
    clear = a[:, 3] < max(1, min(255, int(opts.threshold)))
    if opts.transparency == "colour":
        tol = round(max(0, min(100, int(opts.tolerance))) * 255 / 100)
        k = int(opts.key)
        d = np.maximum(
            np.maximum(
                np.abs(a[:, 0].astype(np.int16) - ((k >> 16) & 0xFF)),
                np.abs(a[:, 1].astype(np.int16) - ((k >> 8) & 0xFF)),
            ),
            np.abs(a[:, 2].astype(np.int16) - (k & 0xFF)),
        )
        clear |= d <= tol
    a[clear] = 0
    a[~clear, 3] = 255
    return a.tobytes()


def stored_pixels(src: Source, opts: ImportOptions):
    """The pixel size the bitmap is stored at (an SVG is always drawn at its size)."""
    if opts.store_at_size or src.kind == "svg":
        return max(1, int(opts.width)), max(1, int(opts.height))
    return src.width, src.height


def build(src: Source, opts: ImportOptions) -> Picture:
    """The Picture to place. Raises PictureError (a bitmap too large to store)."""
    from ...format.bitmaps import MAX_SIDE

    w, h = max(1, int(opts.width)), max(1, int(opts.height))
    if src.kind == "metafile" and opts.as_drawing:
        return Picture("metafile", src.wmf, w, h, src.path)
    sw, sh = stored_pixels(src, opts)
    if sw > MAX_SIDE or sh > MAX_SIDE:
        raise PictureError(
            "%s would be %d x %d pixels; a bitmap can be at most %d pixels "
            "wide and high. Import it smaller (File > Import lets you choose "
            "its size)." % (src.name, sw, sh, MAX_SIDE)
        )
    rgba, sw, sh = qimage_rgba(src.image(sw, sh))
    rgba = make_transparent(rgba, opts)
    return Picture("bitmap", rgba, sw, sh, src.path, place_w=w if w != sw else 0, place_h=h if h != sh else 0)


def stored_bytes(pic: Picture) -> int:
    """About how many bytes the picture takes in the page (its compressed DIB)."""
    if pic.kind == "metafile":
        return len(pic.data)
    import zlib
    from ...format.bitmaps import encode_dib

    return len(zlib.compress(encode_dib(pic.data, pic.width, pic.height), 6)) + 40


def smooth_resample(rgba: bytes, width: int, height: int, new_w: int, new_h: int) -> bytes:
    """A picture scaled smoothly to ``new_w`` x ``new_h`` (a stretched bitmap is drawn with
    the runtime's coarse stretching), each pixel opaque or clear again."""
    if (width, height) == (new_w, new_h):
        return bytes(rgba[: width * height * 4])
    src = Source("raster", "", width, height, image=_image_of(rgba, width, height))
    out, _w, _h = qimage_rgba(src.image(new_w, new_h))
    return make_transparent(out, ImportOptions(new_w, new_h, transparency="alpha"))


# -- File > Import Recent ----------------------------------------------------------------
def _path_list(key: str) -> List[str]:
    v = app_settings().value(key, [])
    if isinstance(v, str):
        v = [v] if v else []
    return [str(x) for x in (v or []) if str(x).strip()]


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def recent_pictures() -> List[str]:
    return _path_list(RECENT_KEY)


def favourite_pictures() -> List[str]:
    return _path_list(FAVOURITES_KEY)


def add_recent(paths) -> None:
    """The files just imported go to the top of File > Import Recent."""
    keep: List[str] = []
    for p in list(paths) + recent_pictures():
        if p and not any(_same_path(p, q) for q in keep):
            keep.append(os.path.normpath(p))
    app_settings().setValue(RECENT_KEY, keep[:RECENT_MAX])


def clear_recent() -> None:
    app_settings().setValue(RECENT_KEY, [])


def is_favourite(path: str) -> bool:
    return any(_same_path(path, p) for p in favourite_pictures())


def set_favourite(path: str, on: bool) -> None:
    favs = [p for p in favourite_pictures() if not _same_path(p, path)]
    if on:
        favs.append(os.path.normpath(path))
    app_settings().setValue(FAVOURITES_KEY, favs)


def dropped_files(mime: Optional[QtCore.QMimeData]) -> List[str]:
    """The importable local files of a drag (Explorer) or a copied file list."""
    if mime is None or not mime.hasUrls():
        return []
    out = []
    for url in mime.urls():
        if url.isLocalFile():
            p = os.path.normpath(url.toLocalFile())
            if os.path.isfile(p) and is_importable(p):
                out.append(p)
    return out


class TransparentPasteDialog(QtWidgets.QDialog):
    """Tools > Colors > Transparent Paste Colour: the colour that becomes
    transparent when a bitmap is pasted or imported (CtDraw: Options >
    Transparent Paste)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transparent Paste Colour")
        from ..objprops.colour_field import ColourPairField

        lay = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel(
            "The colour to make transparent in pasted and imported pictures, for example "
            "the white background of an icon."
        )
        info.setWordWrap(True)
        lay.addWidget(info)
        cur = transparent_colour()
        self.enabled = QtWidgets.QCheckBox("Make this colour transparent")
        self.enabled.setChecked(cur is not None)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.enabled)
        self.colour = ColourPairField("Transparent paste colour", flash=False)
        self.colour.set_pair(0xFFFFFF if cur is None else cur)
        self.colour.setEnabled(cur is not None)
        self.enabled.toggled.connect(self.colour.setEnabled)
        row.addWidget(self.colour)
        lay.addLayout(row)
        bb = dialog_buttons(self)
        lay.addWidget(bb)

    def value(self) -> Optional[int]:
        if not self.enabled.isChecked():
            return None
        return self.colour.pair()[0] & 0xFFFFFF

    def accept(self) -> None:
        set_transparent_colour(self.value())
        super().accept()
