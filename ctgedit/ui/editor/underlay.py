"""The underlay: another page or picture drawn faded behind the page."""

from __future__ import annotations

import collections
import contextlib
import hashlib
import json
import math
import os
import time
import weakref
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ...infra import caches as _caches
from . import canvas_hooks
from ..prefs.settings import app_settings, option

HELP_TOPIC = "underlay"

# ------------------------------------------------------------------------------- settings
OPACITY_KEY = "opt/underlay_opacity"
TINT_KEY = "opt/underlay_tint"
RESOLUTION_KEY = "opt/underlay_resolution"
DIR_KEY = "dir/underlay"
ABOVE_OPTION = "underlay_above"
REMEMBER_OPTION = "underlay_remember"
SNAP_OPTION = "underlay_snap"
#: the tints: (value, label)
TINTS = (("none", "None"), ("grey", "Grey"), ("blue", "Blue"))
#: Settings > Canvas > Underlay > 'Draw page underlays at': picture pixels per page pixel
RESOLUTIONS = ((1, "1x the page size"), (2, "2x the page size"), (4, "4x the page size"))
OPACITY_RANGE = (5, 100)
#: a page underlay's picture (and a picture underlaid): at most this many pixels a side and
#: in all (one copy of 32 M pixels is 128 MB)
MAX_SIDE = 8192
MAX_PIXELS = 32_000_000
#: a picture file larger than Qt's allocation limit (256 MB) is decoded whole before it is
#: scaled down (PNG, BMP, TIFF): the limit is raised for that one read, up to this (MB)
READ_LIMIT_MB = 1024
#: the scale range of the window's Scale field (1.0 = 100 %)
SCALE_RANGE = (0.01, 20.0)
#: the coordinate range of X and Y (the record range)
COORD_RANGE = 32000
#: the pictures QFileDialog offers
IMAGE_FILTER = "Pictures (*.png *.jpg *.jpeg *.bmp *.gif *.svg *.tif *.tiff *.webp);;All files (*.*)"
#: the settings group of the remembered underlays and how many pages keep one (the default of
#: Settings > Canvas > Underlay > Remember the underlay of at most:
#: ui/prefs/timing_options.UNDERLAYS_KEPT)
MEMORY_PREFIX = "underlay/"
MEMORY_MAX = 200


def default_opacity() -> int:
    try:
        v = int(str(app_settings().value(OPACITY_KEY, 40)).strip())
    except TypeError, ValueError:  # swallow-ok: a garbled setting reads as its default
        v = 40
    return max(OPACITY_RANGE[0], min(OPACITY_RANGE[1], v))


def default_tint() -> str:
    v = str(app_settings().value(TINT_KEY, "grey") or "grey")
    return v if v in dict(TINTS) else "grey"


def resolution() -> int:
    try:
        v = int(str(app_settings().value(RESOLUTION_KEY, 2)).strip())
    except TypeError, ValueError:  # swallow-ok: a garbled setting reads as its default
        v = 2
    return v if v in dict(RESOLUTIONS) else 2


def _note_exc(where: str, **kw) -> None:
    from ...infra.logsetup import note_exception

    note_exception("underlay." + where, **kw)


# ------------------------------------------------------------------------------ the cache
#: the pictures read or drawn for underlays: key -> (QImage, info dict); a few small ones
#: (a page underlay draws library elements: a library saved empties it)
_IMAGE_CACHE: dict = {}
_caches.register("ui.underlay.images", _IMAGE_CACHE, on=("libraries", "refresh", "tests"))
#: the pictures kept (the oldest goes first) and their pixels together at most (64 MB); a
#: larger picture is not kept
IMAGE_CACHE_MAX = 6
IMAGE_CACHE_PIXELS = 16_000_000


def file_stamp(path: str) -> Optional[tuple]:
    """(modified time, size) of ``path``; None when it is not there."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _cache_key(path: str, stamp, *extra) -> tuple:
    return (os.path.normcase(os.path.abspath(path)), stamp) + tuple(extra)


def _cache_get(key):
    hit = _IMAGE_CACHE.pop(key, None)
    if hit is not None:
        _IMAGE_CACHE[key] = hit  # (the newest last)
    return hit


def _cache_put(key, img: QtGui.QImage, info: dict) -> None:
    _IMAGE_CACHE.pop(key, None)
    if img.width() * img.height() > IMAGE_CACHE_PIXELS:
        return  # (too large to keep beside the one drawn)
    _IMAGE_CACHE[key] = (img, dict(info))
    total = sum(v[0].width() * v[0].height() for v in _IMAGE_CACHE.values())
    while _IMAGE_CACHE and (len(_IMAGE_CACHE) > IMAGE_CACHE_MAX or total > IMAGE_CACHE_PIXELS):
        old = next(iter(_IMAGE_CACHE))
        im = _IMAGE_CACHE.pop(old)[0]
        total -= im.width() * im.height()


def _label_mode() -> str:
    """The reader's label mode (a page underlay's texts follow it: part of its cache key)."""
    try:
        from ...io import reader

        return str(reader.get_label_mode())
    except Exception:  # noqa: BLE001  # swallow-ok: a tool without the reader set up
        return ""


def page_cache_key(ul, stamp, factor: Optional[int] = None) -> tuple:
    """The cache key of a page underlay's picture (its file, the resolution, the template
    choice, the version's page and the label mode)."""
    return _cache_key(
        ul.path, stamp, int(factor or resolution()), bool(ul.template), ul.page_path, _label_mode()
    )


def drawable(img: QtGui.QImage) -> QtGui.QImage:
    """``img`` in a format the canvas draws at once (premultiplied with alpha, else RGB32):
    the picture is converted once here, never at paint time."""
    if img is None or img.isNull():
        return QtGui.QImage()
    want = QtGui.QImage.Format_ARGB32_Premultiplied if img.hasAlphaChannel() else QtGui.QImage.Format_RGB32
    return img if img.format() == want else img.convertToFormat(want)


# --------------------------------------------------------------------------------- tints
def tint_table(tint: str) -> List[int]:
    """The 256 colours (QRgb) the grey values 0 to 255 take in ``tint``: 'grey' as they are,
    'blue' from a dark to a light blue (theme.FIXED underlay_blue_dark / _light)."""
    ramp = range(256)
    if tint != "blue":
        return [0xFF000000 | (v << 16) | (v << 8) | v for v in ramp]
    from ..style.theme import fixed

    lo, hi = fixed("underlay_blue_dark"), fixed("underlay_blue_light")
    out = []
    for v in ramp:
        c = [
            max(0, min(255, int(a + (b - a) * v / 255.0 + 0.5)))
            for a, b in ((lo.red(), hi.red()), (lo.green(), hi.green()), (lo.blue(), hi.blue()))
        ]
        out.append(0xFF000000 | (c[0] << 16) | (c[1] << 8) | c[2])
    return out


def tint_base(img: QtGui.QImage):
    """(grey values as an 8-bit indexed picture, alpha as an 8-bit picture or None for an
    opaque one) of ``img``: what Grey and Blue are made from, a quarter of the picture each.
    Qt's own conversions (its luminance through the colour space): no full-size Python
    temporaries. None when the memory for it is not there."""
    if img is None or img.isNull():
        return None
    grey = img.convertToFormat(QtGui.QImage.Format_Grayscale8)
    if grey.isNull():
        return None
    alpha = None
    if img.hasAlphaChannel():
        alpha = img.convertToFormat(QtGui.QImage.Format_Alpha8)
        if alpha.isNull():
            return None
    # (the same bytes read as indexes into a colour table: the tint is that table)
    grey.reinterpretAsFormat(QtGui.QImage.Format_Indexed8)
    return grey, alpha


def tint_from_base(base, tint: str) -> QtGui.QImage:
    """The picture in ``tint`` from its :func:`tint_base`: its colour table applied, its alpha
    put back (premultiplied, or RGB32 without alpha: both drawn at once). A null picture when
    the memory for it is not there."""
    if base is None:
        return QtGui.QImage()
    grey, alpha = base
    ix = QtGui.QImage(grey)
    ix.setColorTable(tint_table(tint))
    out = ix.convertToFormat(
        QtGui.QImage.Format_ARGB32_Premultiplied if alpha is not None else QtGui.QImage.Format_RGB32
    )
    del ix
    if out.isNull():
        return out
    if alpha is not None:
        out.setAlphaChannel(alpha)
    return out


def tint_image(img: QtGui.QImage, tint: str) -> QtGui.QImage:
    """``img`` in ``tint`` ('none': as it is; 'grey': its grey values; 'blue': its grey
    values from a dark to a light blue), alpha kept, premultiplied (the fastest to draw)."""
    if img is None or img.isNull():
        return QtGui.QImage()
    if tint not in ("grey", "blue"):
        return drawable(img)
    return tint_from_base(tint_base(img), tint)


def memory_note(size) -> str:
    """The error of a picture there is no memory for."""
    return (
        "there is not enough memory for its picture (%d × %d px). Settings > Canvas > "
        "Underlay > Draw page underlays at 1x takes less" % (size[0], size[1])
    )


# ------------------------------------------------------------------------------ the model
class Underlay:
    """One page tab's underlay."""

    def __init__(self, kind: str, path: str, page_path: str = "", when: str = ""):
        self.kind = kind if kind in ("page", "image", "version") else "page"
        self.path = os.path.abspath(path)
        #: an earlier version: the page it is a version of (its project draws it)
        self.page_path = os.path.abspath(page_path) if page_path else ""
        self.when = when
        self.x = 0.0
        self.y = 0.0
        self.scale = 1.0
        self.opacity = default_opacity() / 100.0
        self.above = option(ABOVE_OPTION)
        self.shown = True
        self.snap = option(SNAP_OPTION)
        self.smooth = True
        #: a page source: its template's objects are drawn too
        self.template = False
        # -- what it has drawn
        #: the picture as it came (None once the tinted copy is made from it)
        self.image: Optional[QtGui.QImage] = None
        #: its picture's size in picture px (whatever is kept of it)
        self.pic_size: Tuple[int, int] = (0, 0)
        #: grows with each picture (what the tinted copy is made from)
        self.pic_serial = 0
        #: its size in page px at 100 % (a page: the source page's size; a picture: its own)
        self.base: Tuple[float, float] = (0.0, 0.0)
        #: a page source's top-level object boxes, in the source page's coordinates
        self.boxes: List[tuple] = []
        self.status = "loading"  # 'loading' | 'ready' | 'error'
        self.error = ""
        #: the file's stamp the picture was made from (a newer file is drawn again) and the
        #: one a failure saw (a newer file is tried again)
        self.stamp = None
        self.fail_stamp = None
        self.serial = 0
        #: Tint None was picked after the picture as it came went: draw it again
        self.need_raw = False
        #: a new underlay: say so once it is drawn (with a way to the window)
        self.announce = False
        # -- 'Move with the Mouse': on, and where it was when it was turned on
        self.moving = False
        self.move_origin: Optional[Tuple[float, float]] = None
        self._tint = default_tint()
        self._grey = None  # (grey values as Indexed8, alpha8 | None)
        self._drawn = None  # ((tint, pic_serial), QImage): what is drawn
        self._levels: dict = {}
        self._levels_src = None  # the drawn picture the halvings come from
        #: the drawn tiles ((i, j) -> QImage | None, the least used first), the picture and
        #: the mapping they were made for (:meth:`_draw_tiles`)
        self._tiles: "collections.OrderedDict" = collections.OrderedDict()
        self._tiles_img = None
        self._tiles_key = None
        self._snap_cache = None

    # -- what it is -----------------------------------------------------------------------
    def is_page(self) -> bool:
        return self.kind in ("page", "version")

    def ready(self) -> bool:
        """It has a picture to draw."""
        return self.pic_size[0] > 0 and (
            self.image is not None or self._grey is not None or self._drawn is not None
        )

    @property
    def tint(self) -> str:
        return self._tint

    @tint.setter
    def tint(self, value: str) -> None:
        value = value if value in dict(TINTS) else "grey"
        if value == self._tint:
            return
        self._tint = value
        # (the copy drawn now stays until the new one is made: keyed by the tint)
        if self.pic_size[0] <= 0:
            return
        if value == "none":
            self.need_raw = self.image is None
            if self.image is not None:
                self.image = drawable(self.image)
        else:
            self.need_raw = False
            self._make_grey()

    def name(self) -> str:
        """'Mixer (Example)', 'Mixer, saved 2026-10-11 14:02:31', 'plant.png'."""
        if self.kind == "image":
            return os.path.basename(self.path)
        if self.kind == "version":
            page = os.path.splitext(os.path.basename(self.page_path or self.path))[0]
            return "%s, saved %s" % (page, self.when) if self.when else page
        page = os.path.splitext(os.path.basename(self.path))[0]
        return "%s (%s)" % (page, os.path.basename(os.path.dirname(self.path)))

    def kind_text(self) -> str:
        return {"page": "Page", "image": "Picture", "version": "Earlier version"}[self.kind]

    def describe(self) -> str:
        """The source in a sentence (tool tips)."""
        if self.kind == "image":
            return "The picture %s." % self.path
        if self.kind == "version":
            return "The version of %s saved %s." % (
                os.path.splitext(os.path.basename(self.page_path))[0],
                self.when,
            )
        return "The page %s." % self.path

    # -- where it lies ----------------------------------------------------------------------
    def rect(self) -> QtCore.QRectF:
        """Where it lies on the page (page px)."""
        bw, bh = self.base
        return QtCore.QRectF(self.x, self.y, bw * self.scale, bh * self.scale)

    def snap_rects(self) -> List[QtCore.QRectF]:
        """The source page's object boxes where they lie on this page, on whole page pixels
        (a fractional X or scale gives fractional edges, and an object snapped to one would be
        stored a pixel short of it)."""
        return self.snap_set().rects

    def snap_set(self) -> canvas_hooks.SnapTargets:
        """:meth:`snap_rects` with their edges and centres sorted (canvas_hooks.SnapTargets):
        made once per place and size."""
        key = (self.serial, self.x, self.y, self.scale)
        c = self._snap_cache
        if c is not None and c[0] == key:
            return c[1]
        s = self.scale
        out = []
        for bx, by, bw, bh in self.boxes:
            left = math.floor(self.x + bx * s + 0.5)
            top = math.floor(self.y + by * s + 0.5)
            right = math.floor(self.x + (bx + bw) * s + 0.5)
            bottom = math.floor(self.y + (by + bh) * s + 0.5)
            out.append(QtCore.QRectF(left, top, right - left, bottom - top))
        targets = canvas_hooks.SnapTargets(out)
        self._snap_cache = (key, targets)
        return targets

    def touch(self) -> None:
        """Something changed (the window, the caches and the canvas follow ``serial``)."""
        self.serial += 1
        self._snap_cache = None

    def set_picture(self, img: QtGui.QImage, base, boxes=()) -> None:
        """The picture drawn or read. With Grey or Blue its grey values are made now and
        the picture as it came goes (one full copy kept: no premultiplied copy is made for
        it); with None it is put in a :func:`drawable` format now, never at paint time."""
        if self._tint == "none":
            size = (img.width(), img.height())
            img = drawable(img)
            if img.isNull():
                self.pic_size = size
                self._out_of_memory()
                return
        self.image = img
        self.pic_size = (img.width(), img.height())
        self.pic_serial += 1
        self._grey = None
        self._drawn = None
        self._drop_levels()
        self.need_raw = False
        self.base = (float(base[0]), float(base[1]))
        self.boxes = [tuple(float(v) for v in b[:4]) for b in boxes if len(b) >= 4]
        self.status = "ready"
        self.error = ""
        self.fail_stamp = None
        self.touch()
        if self._tint != "none":
            self._make_grey()

    def set_failed(self, error: str) -> None:
        """It could not be drawn (the last picture, if any, still shows)."""
        self.status = "error"
        self.error = error
        self.fail_stamp = file_stamp(self.path)
        self.touch()

    def _out_of_memory(self) -> None:
        """The memory for its picture was not there: everything of it goes (no paint tries
        again), it says why."""
        size = self.pic_size
        self.release()
        self.set_failed(memory_note(size))
        # (found in a paint, maybe: the window, its field and its notice follow from the
        # event loop)
        ref = weakref.ref(self)

        def tell() -> None:
            ul = ref()
            if ul is None or ul.status != "error":
                return
            _changed_everywhere(ul)
            win = _window_of(ul)
            if win is not None:
                win.underlay_failed(ul)

        QtCore.QTimer.singleShot(0, tell)

    def release(self) -> None:
        """Every picture goes (the page closed, or there was no memory for it)."""
        self.image = None
        self._grey = None
        self.release_drawn()
        self.pic_size = (0, 0)

    def release_drawn(self) -> None:
        """The tinted copy and its halvings go (the tab is not shown: made again from the
        grey values when it shows)."""
        self._drawn = None
        self._drop_levels()

    def _drop_levels(self) -> None:
        """The halvings and the drawn tiles go."""
        self._levels = {}
        self._levels_src = None
        self._tiles = collections.OrderedDict()
        self._tiles_img = None
        self._tiles_key = None

    def _make_grey(self) -> bool:
        """The grey values (and alpha) Grey and Blue are made from; then the picture as it
        came goes when its file is still there (Tint None draws or reads it again)."""
        if self._grey is not None:
            return True
        if self.image is None:
            return False
        grey = tint_base(self.image)
        if grey is None:
            self._out_of_memory()
            return False
        self._grey = grey
        if os.path.isfile(self.path):
            self.image = None
        return True

    # -- drawing ---------------------------------------------------------------------------
    def drawn(self) -> QtGui.QImage:
        """The picture as it is drawn: in its tint (made once per tint and picture, from the
        grey values: a colour table and the alpha, no conversion of the whole picture's
        colours). While Tint None waits for the picture as it came, the last copy."""
        key = (self._tint, self.pic_serial)
        d = self._drawn
        if d is not None and d[0] == key:
            return d[1]
        if self._tint == "none":
            if self.image is None:
                return d[1] if d is not None else QtGui.QImage()
            img = self.image = drawable(self.image)
            if img.isNull():
                self._out_of_memory()
                return img
        else:
            if self._grey is None and not self._make_grey():
                return d[1] if d is not None and self.ready() else QtGui.QImage()
            # (the old copy and its halvings go first: never two tinted copies at once)
            d = self._drawn = None
            self._drop_levels()
            img = tint_from_base(self._grey, self._tint)
            if img.isNull():
                self._out_of_memory()
                return QtGui.QImage()
        self._drawn = (key, img)
        return img

    def tinted(self) -> QtGui.QImage:
        """The picture in its tint (:meth:`drawn`)."""
        return self.drawn()

    def level(self, n: int) -> QtGui.QImage:
        """The drawn picture halved ``n`` times (made once, from the level before)."""
        top = self.drawn()
        if self._levels_src is not top:
            self._drop_levels()
            self._levels_src = top
        if n <= 0 or top.isNull():
            return top
        img = self._levels.get(n)
        if img is None:
            prev = self.level(n - 1)
            img = prev.scaled(
                max(1, prev.width() // 2),
                max(1, prev.height() // 2),
                QtCore.Qt.IgnoreAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            self._levels[n] = img
        return img

    #: a drawn tile's side, in device pixels
    TILE = 256
    #: the drawn tiles kept besides those the canvas shows (scrolled back: not made again)
    TILE_KEEP = 32

    def draw(self, painter: QtGui.QPainter, exposed: QtCore.QRectF) -> None:
        """Paint the part in ``exposed`` (scene coordinates). The picture is taken at the
        halving whose pixels are at least half a device pixel (a big picture zoomed out is
        never scaled down whole at paint time); it is scaled into tiles of the device's
        pixels (:meth:`_draw_tiles`), which are then copied unscaled."""
        if not self.ready():
            return
        r = self.rect()
        if r.width() <= 0 or r.height() <= 0:
            return
        vis = r.intersected(exposed)
        if vis.isEmpty():
            return
        top = self.drawn()
        if top.isNull():
            return
        dt = painter.deviceTransform()
        dev = abs(dt.m11()) or 1.0  # device px per page px
        s = dev * r.width() / max(1, top.width())  # device px per picture px
        n = 0
        while (
            s * (1 << n) < 0.5
            and n < 8
            and (top.width() >> (n + 1)) >= 16
            and (top.height() >> (n + 1)) >= 16
        ):
            n += 1
        img = self.level(n)
        painter.save()
        try:
            painter.setOpacity(max(0.0, min(1.0, self.opacity)))
            if not self._draw_tiles(painter, dt, r, vis, img):
                # (a turned or mirrored painter, never the canvas's: scaled here, clipped)
                pad = 1.0 / dev
                painter.setClipRect(vis.adjusted(-pad, -pad, pad, pad), QtCore.Qt.IntersectClip)
                painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, bool(self.smooth))
                painter.drawImage(r, img, QtCore.QRectF(img.rect()))
        finally:
            painter.restore()

    def _draw_tiles(
        self,
        painter: QtGui.QPainter,
        dt: QtGui.QTransform,
        r: QtCore.QRectF,
        vis: QtCore.QRectF,
        img: QtGui.QImage,
    ) -> bool:
        """Draw ``img`` on the underlay from device-pixel tiles."""
        if dt.type().value > QtGui.QTransform.TxScale.value or dt.m11() <= 0 or dt.m22() <= 0:
            return False
        inv_world, ok = painter.worldTransform().inverted()
        if not ok:
            return False
        # (the painter's own px -> device px: the screen's pixel ratio, a redirection; its
        # inverse as the world transform draws on device px)
        to_device, ok = (inv_world * dt).inverted()
        if not ok:
            return False
        sx, sy = dt.m11(), dt.m22()
        # the underlay's corner in device px, the whole-pixel part of the painter's offset
        # (the scroll) taken out: the same for every strip and the full repaint
        ox, oy = math.floor(dt.dx()), math.floor(dt.dy())
        cl = r.left() * sx + (dt.dx() - ox)
        ct = r.top() * sy + (dt.dy() - oy)
        x0, y0 = math.floor(cl), math.floor(ct)
        rw, rh = r.width() * sx, r.height() * sy
        fx, fy = cl - x0, ct - y0
        key = (sx, sy, rw, rh, fx, fy, bool(self.smooth))
        if self._tiles_img is not img or self._tiles_key != key:
            self._tiles = collections.OrderedDict()
            self._tiles_img = img
            self._tiles_key = key
        t = self.TILE
        e = dt.mapRect(vis)
        cols = max(1, math.ceil(fx + rw))
        rows = max(1, math.ceil(fy + rh))
        i0 = max(0, (math.floor(e.left()) - ox - x0) // t)
        i1 = min((cols - 1) // t, (math.ceil(e.right()) - 1 - ox - x0) // t)
        j0 = max(0, (math.floor(e.top()) - oy - y0) // t)
        j1 = min((rows - 1) // t, (math.ceil(e.bottom()) - 1 - oy - y0) // t)
        if i1 < i0 or j1 < j0:
            return True
        # (a device pixel more on each side: the painter's own clip, the exposed pixels,
        # bounds the tiles; this one keeps them to the strip should it have none)
        painter.setClipRect(vis.adjusted(-1.0 / sx, -1.0 / sy, 1.0 / sx, 1.0 / sy), QtCore.Qt.IntersectClip)
        painter.setWorldTransform(to_device)
        tiles = self._tiles
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                if (i, j) in tiles:
                    tile = tiles[(i, j)]
                    tiles.move_to_end((i, j))
                else:
                    tile = tiles[(i, j)] = self._tile(img, i, j, fx, fy, rw, rh, cols, rows)
                if tile is not None:
                    painter.drawImage(QtCore.QPointF(ox + x0 + i * t, oy + y0 + j * t), tile)
        keep = self.TILE_KEEP + (i1 - i0 + 1) * (j1 - j0 + 1)
        while len(tiles) > keep:
            tiles.popitem(last=False)
        return True

    def _tile(
        self,
        img: QtGui.QImage,
        i: int,
        j: int,
        fx: float,
        fy: float,
        rw: float,
        rh: float,
        cols: int,
        rows: int,
    ) -> Optional[QtGui.QImage]:
        """Tile (i, j): the picture scaled onto ``rw`` x ``rh`` device px whose corner lies
        at (``fx``, ``fy``) of tile (0, 0); the edge tiles only as large as the underlay.
        Drawn from the source pixels it covers and 2 more around them (smooth scaling
        reads its neighbours). None: nothing of the picture there, or no memory."""
        t = self.TILE
        tw, th = min(t, cols - i * t), min(t, rows - j * t)
        if tw <= 0 or th <= 0:
            return None
        left, top = fx - i * t, fy - j * t  # the underlay's corner in the tile
        kx, ky = img.width() / rw, img.height() / rh  # picture px per device px
        sx0 = max(0, math.floor(-left * kx) - 2)
        sy0 = max(0, math.floor(-top * ky) - 2)
        sx1 = min(img.width(), math.ceil((tw - left) * kx) + 2)
        sy1 = min(img.height(), math.ceil((th - top) * ky) + 2)
        if sx1 <= sx0 or sy1 <= sy0:
            return None
        tile = QtGui.QImage(tw, th, QtGui.QImage.Format_ARGB32_Premultiplied)
        if tile.isNull():
            return None
        tile.fill(0)
        p = QtGui.QPainter(tile)
        try:
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, bool(self.smooth))
            p.drawImage(
                QtCore.QRectF(left + sx0 / kx, top + sy0 / ky, (sx1 - sx0) / kx, (sy1 - sy0) / ky),
                img,
                QtCore.QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0),
            )
        finally:
            p.end()
        return tile

    # -- memory ----------------------------------------------------------------------------
    FIELDS = ("x", "y", "scale", "opacity", "tint", "above", "shown", "snap", "smooth", "template")

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "path": self.path, "page_path": self.page_path, "when": self.when}
        for f in self.FIELDS:
            d[f] = getattr(self, f)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Underlay":
        ul = cls(
            str(d.get("kind") or "page"),
            str(d["path"]),
            str(d.get("page_path") or ""),
            str(d.get("when") or ""),
        )
        for f in ("x", "y", "scale", "opacity"):
            if f in d:
                setattr(ul, f, float(d[f]))
        ul.scale = max(SCALE_RANGE[0], min(SCALE_RANGE[1], ul.scale))
        ul.opacity = max(OPACITY_RANGE[0] / 100.0, min(1.0, ul.opacity))
        if d.get("tint") in dict(TINTS):
            ul.tint = d["tint"]
        for f in ("above", "shown", "snap", "smooth", "template"):
            if f in d:
                setattr(ul, f, bool(d[f]))
        return ul


# ------------------------------------------------------------------------- per document
def peek(doc) -> Optional[Underlay]:
    """``doc``'s underlay (nothing is restored here: the paint path asks)."""
    st = getattr(doc, "ui", None) if doc is not None else None
    ul = getattr(st, "underlay", None) if st is not None else None
    return ul if isinstance(ul, Underlay) else None


def _set(doc, ul: Optional[Underlay]) -> None:
    st = getattr(doc, "ui", None)
    if st is not None:
        st.underlay = ul


def _is_version_tab(doc) -> bool:
    return getattr(getattr(doc, "ui", None), "version_view", None) is not None


def page_name(doc) -> Optional[str]:
    """What an underlay is remembered for: the page file (a library element: its library
    file and the element), normalised; None for a version shown read-only."""
    path = getattr(doc, "path", None)
    if not path or _is_version_tab(doc):
        return None
    name = os.path.normcase(os.path.abspath(path))
    element = getattr(doc, "element_name", None)
    if element:
        name += "|" + str(element).lower()
    return name


def memory_key(doc) -> Optional[str]:
    name = page_name(doc)
    if name is None:
        return None
    return MEMORY_PREFIX + hashlib.sha1(name.encode("utf-8")).hexdigest()[:20]


def remember(doc, ul: Optional[Underlay]) -> None:
    """Keep ``doc``'s underlay for the next time the page opens (Settings > Canvas >
    'Remember each page's underlay'); None forgets it."""
    key = memory_key(doc)
    if key is None:
        return
    st = app_settings()
    if ul is None:
        st.remove(key)
        return
    if not option(REMEMBER_OPTION):
        return
    d = ul.to_dict()
    d["page"] = page_name(doc)
    d["t"] = round(time.time(), 3)
    st.setValue(key, json.dumps(d))
    prune_remembered(st)


def prune_remembered(st) -> None:
    """The remembered underlays beyond Settings > Canvas > Underlay > "Remember the underlay of
    at most" go, oldest first (after a store, and from the setting's hook)."""
    from ..prefs import timing_options

    keep = timing_options.UNDERLAYS_KEPT
    st.beginGroup(MEMORY_PREFIX.rstrip("/"))
    try:
        keys = st.allKeys()
        if len(keys) <= keep:
            return
        stamped = []
        for k in keys:
            try:
                stamped.append((float(json.loads(str(st.value(k))).get("t", 0)), k))
            except ValueError, TypeError, AttributeError:  # swallow-ok: garbled = oldest
                stamped.append((0.0, k))
        stamped.sort()
        for _t, k in stamped[: len(keys) - keep]:
            st.remove(k)
    finally:
        st.endGroup()


def remembered(doc) -> Optional[Underlay]:
    """The underlay kept for ``doc``'s page (None: none, or a garbled entry)."""
    key = memory_key(doc)
    if key is None:
        return None
    raw = app_settings().value(key, "")
    if not raw:
        return None
    try:
        d = json.loads(str(raw))
        if d.get("page") != page_name(doc):
            return None  # (another page with the same hash)
        return Underlay.from_dict(d)
    except ValueError, TypeError, KeyError, AttributeError:  # swallow-ok: garbled = none
        return None


# ------------------------------------------------------------------------- the sources
def read_picture(path: str) -> Tuple[Optional[QtGui.QImage], float, str]:
    """(picture, picture px per original px, error) of the picture file ``path``: a picture
    larger than MAX_SIDE / MAX_PIXELS is read scaled down (its unit tells by how much). PNG,
    BMP and TIFF decode the whole picture before they scale it, so Qt's allocation limit is
    raised for this read up to READ_LIMIT_MB (a larger one is refused with its size)."""
    reader = QtGui.QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    unit = 1.0
    need_mb = 0
    if size.isValid() and size.width() > 0 and size.height() > 0:
        w, h = size.width(), size.height()
        k = min(1.0, MAX_SIDE / float(w), MAX_SIDE / float(h), math.sqrt(MAX_PIXELS / float(w * h)))
        if k < 1.0:
            reader.setScaledSize(QtCore.QSize(max(1, int(w * k)), max(1, int(h * k))))
            unit = k
        # (the whole picture decoded, 4 bytes a pixel, in MB)
        need_mb = int(math.ceil(w * h * 4 / 1048576.0)) + 1
        if need_mb > READ_LIMIT_MB:
            return (
                None,
                1.0,
                (
                    "the picture is too large to read (%d × %d px). ctgedit reads pictures of "
                    "up to %d million pixels" % (w, h, READ_LIMIT_MB * 1048576 // 4 // 1000000)
                ),
            )
    limit = QtGui.QImageReader.allocationLimit()
    raise_it = bool(limit) and need_mb > limit
    if raise_it:
        QtGui.QImageReader.setAllocationLimit(need_mb)
    try:
        img = reader.read()
    finally:
        if raise_it:
            QtGui.QImageReader.setAllocationLimit(limit)
    if img.isNull():
        return None, 1.0, reader.errorString() or "the picture could not be read"
    if unit < 1.0 and size.isValid():
        unit = img.width() / float(size.width())
    return img, unit, ""


def render_options(ul: Underlay, factor: Optional[int] = None) -> dict:
    """What the render process is asked for (preview.render_to_files' options): the
    resolution and the size caps are kept there too (the page's size may be unknown here)."""
    opts = {
        "transparent": True,
        "boxes": True,
        "fast": True,
        "template": bool(ul.template),
        "factor": int(factor or resolution()),
        "max_pixels": MAX_PIXELS,
    }
    if ul.kind == "version" and ul.page_path:
        opts["as_path"] = ul.page_path
    return opts


def render_size(factor: int, page_size=None) -> Tuple[int, int]:
    """The largest picture the render may make: ``factor`` times the source page's size
    (when known), within MAX_SIDE and MAX_PIXELS."""
    if not page_size or not page_size[0] or not page_size[1]:
        return MAX_SIDE, MAX_SIDE
    w, h = float(page_size[0]), float(page_size[1])
    k = min(float(factor), MAX_SIDE / w, MAX_SIDE / h, math.sqrt(MAX_PIXELS / (w * h)))
    return max(1, int(w * k)), max(1, int(h * k))


def source_page_size(path: str) -> Optional[Tuple[int, int]]:
    """The page's size from its head (no record parse: format/pagesummary); None."""
    try:
        from ...format import pagesummary as PS

        s = PS.read_page_summary(path, template_scan=0)
        w, h = int(getattr(s, "width", 0) or 0), int(getattr(s, "height", 0) or 0)
        return (w, h) if w and h else None
    except Exception:  # noqa: BLE001  # swallow-ok: the render then finds the size itself
        return None


def request_render(queue, token: int, path: str, w: int, h: int, options: dict) -> None:
    """Ask the render process for ``path`` (the result comes to ``queue._on_rendered``;
    a seam: the tests stand in for the process)."""
    from ..browser.preview import renderer

    renderer("underlay").request(token, path, w, h, queue, options=options)


class _RenderQueue(QtCore.QObject):
    """The page underlays waiting to be drawn, one render process at a time (the preview's
    runner 'underlay': the newest request there stops the running one, so they queue
    here). Each render is a task in the status bar while it runs; its Cancel stops that
    render (by its token) even when its underlay is gone (its tab closed)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending: list = []  # [weakref to Underlay]
        self._current = None  # {"ul", "token", "task", "key", "stamp"}
        self._tokens = 0

    def add(self, ul: Underlay) -> None:
        self.drop(ul, quiet=True)
        self._pending.append(weakref.ref(ul))
        self._next()

    def busy_with(self, ul: Underlay) -> bool:
        cur = self._current
        return (cur is not None and cur["ul"]() is ul) or any(r() is ul for r in self._pending)

    def drop(self, ul: Underlay, quiet: bool = False) -> None:
        """``ul`` is not wanted any more (removed, replaced, its tab closed): its render
        stops."""
        self._pending = [r for r in self._pending if r() is not None and r() is not ul]
        cur = self._current
        if cur is not None and cur["ul"]() is ul:
            self._stop_current(later=quiet)

    def _stop_current(self, later: bool = False) -> None:
        """The running render stops, its task goes, the next one starts (``later``: from the
        event loop: the caller queues it again, or draws it from the cache first)."""
        cur, self._current = self._current, None
        if cur is None:
            return
        from ..jobs import tasks
        from ..browser.preview import renderer

        renderer("underlay").cancel()
        tasks.drop(cur["task"])
        if later:
            QtCore.QTimer.singleShot(0, self, self._next)
        else:
            self._next()

    def cancel_token(self, token: int) -> None:
        """The task's Cancel of render ``token``: its underlay goes (it has nothing to show);
        when that underlay is gone already, the render stops all the same."""
        cur = self._current
        if cur is None or cur["token"] != token:
            return
        ul = cur["ul"]()
        if ul is not None:
            _cancel_render(ul)
        if self._current is cur:
            self._stop_current()

    def _next(self) -> None:
        if self._current is not None:
            return
        while self._pending:
            ul = self._pending.pop(0)()
            if ul is None or ul.status != "loading":
                continue
            self._start(ul)
            return

    def _start(self, ul: Underlay) -> None:
        from ..jobs import tasks

        self._tokens += 1
        token = self._tokens
        factor = resolution()
        stamp = file_stamp(ul.path)
        key = page_cache_key(ul, stamp, factor)
        win = _window_of(ul)
        task = tasks.begin(
            "underlay",
            DRAWING_NOTE % ul.name(),
            cancel=lambda t=token: render_queue().cancel_token(t),
            window=win,
        )
        self._current = {"ul": weakref.ref(ul), "token": token, "task": task, "key": key, "stamp": stamp}
        w, h = render_size(factor, source_page_size(ul.path))
        request_render(self, token, ul.path, w, h, render_options(ul, factor))

    def _on_rendered(self, token: int, img, info: dict, err: str) -> None:
        cur = self._current
        if cur is None or cur["token"] != token:
            return
        self._current = None
        from ..jobs import tasks

        ul = cur["ul"]()
        if ul is not None and ul.status == "loading":
            if img is not None and img.isNull():
                img = None
            if img is None:
                ul.set_failed(err or "the page could not be drawn")
                tasks.end(cur["task"], ok=False, tell=False)
                win = _window_of(ul)
                if win is not None:
                    win.underlay_failed(ul)
            else:
                info = dict(info or {})
                _cache_put(cur["key"], img, info)
                _apply_render(ul, img, info, cur["stamp"])
                del img
                win = _window_of(ul)
                _clear_drawing_note(win)
                if ul.status != "ready":
                    # (no memory for its grey values: said by the underlay itself)
                    tasks.end(cur["task"], ok=False, tell=False)
                else:
                    # (a new underlay says so itself, with a way to its window)
                    tasks.end(
                        cur["task"],
                        ok=True,
                        summary="Drew the underlay %s." % ul.name(),
                        tell=not ul.announce,
                    )
                    if win is not None:
                        win.underlay_ready(ul)
            _changed_everywhere(ul)
        else:
            tasks.drop(cur["task"])
        self._next()

    def clear(self) -> None:
        """Stop the running render and forget the waiting ones (tests)."""
        self._pending = []
        cur, self._current = self._current, None
        if cur is not None:
            from ..jobs import tasks
            from ..browser.preview import renderer

            renderer("underlay").cancel()
            tasks.drop(cur["task"])

    def wait(self, timeout: float = 60.0) -> bool:
        """Block until every queued render is delivered (tests / scripts)."""
        from ..browser.preview import renderer

        t0 = time.monotonic()
        while (self._current is not None or self._pending) and time.monotonic() - t0 < timeout:
            renderer("underlay").wait(max(0.1, timeout - (time.monotonic() - t0)))
            QtWidgets.QApplication.processEvents()
            if self._current is None:
                self._next()
        return self._current is None and not self._pending


_QUEUE: list = []


def render_queue() -> _RenderQueue:
    q = _QUEUE[0] if _QUEUE else None
    if q is not None:
        try:
            q.objectName()
            return q
        except RuntimeError:
            _QUEUE.clear()
    q = _RenderQueue(QtWidgets.QApplication.instance())
    _QUEUE[:] = [q]
    return q


def _apply_render(ul: Underlay, img: QtGui.QImage, info: dict, stamp) -> None:
    page = info.get("page") or [img.width(), img.height()]
    ul.stamp = stamp
    ul.set_picture(img, (page[0], page[1]), info.get("boxes") or ())


#: the status message a render shows while it runs (cleared when it is done)
DRAWING_NOTE = "Drawing the underlay %s…"


def _clear_drawing_note(win) -> None:
    if win is None:
        return
    with contextlib.suppress(RuntimeError, AttributeError):
        sb = win.statusBar()
        if sb.currentMessage().startswith(DRAWING_NOTE.split("%")[0]):
            sb.clearMessage()


def _cancel_render(ul: Optional[Underlay]) -> None:
    """The task's Cancel: the underlay goes (it has nothing to show)."""
    if ul is None:
        return
    for win in _windows():
        for doc in _docs_of(win):
            if peek(doc) is ul:
                win.remove_underlay(doc=doc)
                return
    render_queue().drop(ul)


def load(ul: Underlay) -> None:
    """Draw ``ul``'s source: a picture at once, a page in the render process (or from the
    cache: the same file, resolution, template choice and label mode). What it shows now
    stays until the new picture comes (no copy made for it in between)."""
    ul.status = "loading"
    ul.error = ""
    ul.touch()
    stamp = file_stamp(ul.path)
    if stamp is None:
        ul.set_failed("its file is not there")  # (the window and the tips name it)
        return
    if ul.kind == "image":
        key = _cache_key(ul.path, stamp, "picture")
        hit = _cache_get(key)
        if hit is None:
            img, unit, err = read_picture(ul.path)
            if img is None:
                ul.set_failed(err)
                return
            hit = (img, {"unit": unit})
            _cache_put(key, img, hit[1])
        img, info = hit
        unit = float(info.get("unit") or 1.0)
        ul.stamp = stamp
        ul.set_picture(img, (img.width() / unit, img.height() / unit))
        return
    hit = _cache_get(page_cache_key(ul, stamp))
    if hit is not None:
        _apply_render(ul, hit[0], hit[1], stamp)
        return
    render_queue().add(ul)


# ------------------------------------------------------------------------ windows / docs
def _windows() -> list:
    try:
        from ..main_window import MainWindow

        return list(MainWindow.editor_windows())
    except ImportError:  # swallow-ok: no editor windows (a tool)
        return []


def _docs_of(win) -> list:
    return [e["doc"] for e in getattr(win, "_docs", []) or []]


def _window_of(ul: Underlay):
    for win in _windows():
        if any(peek(d) is ul for d in _docs_of(win)):
            return win
    return None


def _changed_everywhere(ul: Underlay) -> None:
    """``ul`` changed: the window whose page shows it draws it again."""
    for win in _windows():
        try:
            if peek(getattr(win, "doc", None)) is ul:
                win.underlay_changed(remember_it=False)
        except RuntimeError:  # swallow-ok: a window being closed
            continue


def _editor_of(view):
    """The editor window whose page canvas ``view`` is (None: another canvas)."""
    try:
        win = view.window()
    except RuntimeError:  # swallow-ok: the canvas is being destroyed
        return None
    return win if getattr(win, "view", None) is view else None


def shown_underlay(view) -> Optional[Underlay]:
    """The underlay drawn on ``view`` now (shown and drawn), or None."""
    win = _editor_of(view)
    if win is None:
        return None
    ul = peek(getattr(win, "doc", None))
    return ul if ul is not None and ul.shown and ul.ready() else None


# --------------------------------------------------------------------- canvas overlays
def _paint_under(view, painter, exposed) -> None:
    ul = shown_underlay(view)
    if ul is not None and not ul.above:
        ul.draw(painter, exposed)


def _paint_over(view, painter, exposed) -> None:
    ul = shown_underlay(view)
    if ul is not None and ul.above:
        ul.draw(painter, exposed)


def _snap_targets(view):
    ul = shown_underlay(view)
    if ul is None or not ul.snap or not ul.is_page():
        return None
    return ul.snap_set()


def _pointer_mode(view):
    """Move with the Mouse on: the canvas shows the open hand and no hover outlines (not
    while a tool is held for a moment: Z shows the zoom box's pointer)."""
    ul = shown_underlay(view)
    if ul is None or not ul.moving or _Mover._held_tool(view):
        return None
    return QtCore.Qt.OpenHandCursor


canvas_hooks.register_overlay("underlay", _paint_under, layer="background", order=-100)
canvas_hooks.register_overlay("underlay_above", _paint_over, layer="over_items", order=-100)
canvas_hooks.register_snap_targets("underlay", _snap_targets)
canvas_hooks.register_pointer_mode("underlay", _pointer_mode)


# ------------------------------------------------------------------ moving with the mouse
def _arrow_step(win, view, mods, dx: int, dy: int) -> tuple:
    """How far an arrow key moves the underlay."""
    try:
        base = int(win._key_step())
    except AttributeError, TypeError, ValueError:  # swallow-ok: a window without the step
        base = 1
    big = max(1, int(getattr(win, "_big_nudge", 10) or 10))
    if mods & QtCore.Qt.ControlModifier:
        mode = str(app_settings().value("opt/ctrl_arrow", "move"))
        if mode == "grid":
            try:
                gx, gy, _ox, _oy = view.grid()
                return dx * float(gx), dy * float(gy)
            except AttributeError, TypeError, ValueError:  # swallow-ok: no grid: the step
                pass
        step = base * big if mode == "x10" else base
    elif mods & QtCore.Qt.ShiftModifier:
        step = base * big
    else:
        step = base
    return dx * step, dy * step


class _Mover(QtCore.QObject):
    """'Move with the Mouse': drag and arrow keys move the underlay."""

    arrow_step = staticmethod(_arrow_step)
    ARROWS = {
        QtCore.Qt.Key_Left: (-1, 0),
        QtCore.Qt.Key_Right: (1, 0),
        QtCore.Qt.Key_Up: (0, -1),
        QtCore.Qt.Key_Down: (0, 1),
    }
    FINISH = (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)

    def __init__(self, win):
        super().__init__(win)
        self._win = weakref.ref(win)
        #: (the press point in SCENE coordinates, x0, y0): a scroll or a zoom while the
        #: button is held keeps the grabbed point under the pointer
        self.drag = None

    def _underlay(self):
        win = self._win()
        ul = peek(getattr(win, "doc", None)) if win is not None else None
        return win, (ul if ul is not None and ul.moving and ul.shown else None)

    @staticmethod
    def _passes(view, pos) -> bool:
        """A left press at ``pos`` that is not the underlay's: a pan, a scroll bar, or a tool
        held for a moment (Z: the zoom box). Turning the move on made Select the tool and
        another tool ends it, so any other tool now is one of those."""
        try:
            return bool(_Mover._held_tool(view) or view.pans_on_left_press() or view.on_scroll_bar(pos))
        except AttributeError, RuntimeError:
            return False

    @staticmethod
    def _held_tool(view) -> bool:
        """A tool other than Select: one held for a moment (see :meth:`_passes`)."""
        try:
            return view.tool() != "select"
        except AttributeError, RuntimeError:
            return False

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        t = ev.type()
        if t not in (
            QtCore.QEvent.MouseButtonPress,
            QtCore.QEvent.MouseMove,
            QtCore.QEvent.MouseButtonRelease,
            QtCore.QEvent.MouseButtonDblClick,
            QtCore.QEvent.KeyPress,
            QtCore.QEvent.ShortcutOverride,
        ):
            return False
        win, ul = self._underlay()
        if ul is None:
            self.drag = None
            return False
        try:
            view = win.view
            vp = view.viewport()
        except AttributeError, RuntimeError:
            return False
        if t in (QtCore.QEvent.KeyPress, QtCore.QEvent.ShortcutOverride):
            if (obj is not view and obj is not vp) or self._held_tool(view):
                return False
            key = ev.key()
            if key not in self.ARROWS and key not in self.FINISH and key != QtCore.Qt.Key_Escape:
                return False
            if ev.modifiers() & ~(
                QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier | QtCore.Qt.KeypadModifier
            ):
                return False
            if t == QtCore.QEvent.ShortcutOverride:
                ev.accept()  # (the key comes as a key press, not a shortcut)
                return True
            if key == QtCore.Qt.Key_Escape:
                if self.drag is not None:
                    _p, x0, y0 = self.drag
                    self.drag = None
                    win.move_underlay_to(x0, y0)
                else:
                    win.end_underlay_move(put_back=True)
                return True
            if key in self.FINISH:
                self.drag = None
                win.finish_underlay_move()
                return True
            dx, dy = self.ARROWS[key]
            mx, my = self.arrow_step(win, view, ev.modifiers(), dx, dy)
            win.move_underlay_to(ul.x + mx, ul.y + my)
            return True
        if obj is not vp:
            return False
        left = getattr(ev, "button", lambda: None)() == QtCore.Qt.LeftButton
        if t == QtCore.QEvent.MouseButtonPress and left:
            if self._passes(view, ev.position()):
                return False
            self.drag = (view.mapToScene(ev.position().toPoint()), ul.x, ul.y)
            vp.setCursor(QtCore.Qt.ClosedHandCursor)
            view.setFocus(QtCore.Qt.MouseFocusReason)
            return True
        if t == QtCore.QEvent.MouseMove:
            if self.drag is not None and ev.buttons() & QtCore.Qt.LeftButton:
                p0, x0, y0 = self.drag
                d = view.mapToScene(ev.position().toPoint()) - p0
                win.move_underlay_to(round(x0 + d.x()), round(y0 + d.y()), remember_it=False)
                return True
            return False
        if t == QtCore.QEvent.MouseButtonRelease and left:
            if self.drag is None:
                return False  # (the end of a pan or a scroll bar drag)
            self.drag = None
            vp.setCursor(QtCore.Qt.OpenHandCursor)
            win.underlay_changed()  # (remembered now)
            return True
        if t == QtCore.QEvent.MouseButtonDblClick and left:
            return not self._passes(view, ev.position())
        return False


# ---------------------------------------------------------------------- the status field
class UnderlayField(QtWidgets.QToolButton):
    """The underlay's status bar field."""

    def __init__(self, window):
        super().__init__(window)
        self._win = window
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.setAccessibleName("Underlay")
        self.setVisible(False)
        self._click = QtCore.QTimer(self)
        self._click.setSingleShot(True)
        self._click.timeout.connect(self._toggle)
        self.clicked.connect(self._clicked)
        self._double_at = 0.0

    def _state(self) -> str:
        ul = peek(getattr(self._win, "doc", None))
        if ul is None:
            return ""
        if ul.moving:
            return "moving"
        return "error" if ul.status == "error" else "shown"

    def _clicked(self) -> None:
        # (a double-click's first click must not hide it: wait for the second; the release
        # that ends a double-click is no click of its own)
        interval = QtWidgets.QApplication.doubleClickInterval()
        if time.monotonic() - self._double_at < interval / 1000.0:
            return
        if self._state() in ("moving", "error"):
            self._click.stop()
            self._toggle()  # (nothing to wait for: at once)
            return
        self._click.start(interval)

    def _toggle(self) -> None:
        state = self._state()
        name = {"moving": "act_underlay_move", "error": "act_underlay_settings"}.get(
            state, "act_underlay_show"
        )
        a = getattr(self._win, name, None)
        if a is not None and a.isEnabled():
            a.trigger()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if e.button() == QtCore.Qt.LeftButton:
            self._click.stop()
            self._double_at = time.monotonic()
            a = getattr(self._win, "act_underlay_settings", None)
            if a is not None and a.isEnabled():
                a.trigger()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def refresh(self, window) -> None:
        from ..style.theme import set_note_role

        ul = peek(getattr(window, "doc", None))
        if ul is None:
            self.setVisible(False)
            return
        if ul.moving:
            text, role = "Moving the underlay", "accent"
            tip = (
                "Drag on the page to move the underlay. Click here or press Enter when it "
                "is in place. Esc puts it back."
            )
        elif ul.status == "error":
            text, role = "Underlay failed", "danger"
            tip = "%s It could not be drawn: %s. Click for the Underlay window." % (
                ul.describe(),
                ul.error.rstrip("."),
            )
        elif not ul.shown:
            text, role = "Underlay hidden", "text_dim"
            tip = "%s Click to show it, double-click for its settings." % ul.describe()
        else:
            text = "Underlay %d %%" % round(ul.opacity * 100)
            role = "text_dim" if ul.status == "loading" else ""
            more = " It is being drawn." if ul.status == "loading" else ""
            tip = "%s%s Click to hide it, double-click for its settings." % (ul.describe(), more)
        if self.text() != text:
            self.setText(text)
        if self.property("_ctgRole") != role:
            self.setProperty("_ctgRole", role)
            set_note_role(self, role, "padding:0 6px;") if role else set_note_role(self, "", "padding:0 6px;")
        if self.toolTip() != tip:
            self.setToolTip(tip)
        self.setVisible(True)


def _field_factory(window):
    f = UnderlayField(window)
    window.status_underlay = f
    return f


def _register_field() -> None:
    from ..panels import status_items

    # (it says the page shows more than its own objects: it stays in a narrow window)
    status_items.register("underlay", _field_factory, 57, label="Underlay", needs_doc=True, priority=72)


# ------------------------------------------------------------------- the canvas's menu
def _context_section(win, menu, ctx) -> None:
    if peek(getattr(win, "doc", None)) is None:
        return
    sub = menu.addMenu("Underlay")
    for a in (
        win.act_underlay_show,
        win.act_underlay_above,
        win.act_underlay_move,
        win.act_underlay_settings,
        win.act_underlay_remove,
    ):
        sub.addAction(a)


def _register_menu() -> None:
    from ..window import context_menu

    context_menu.register_section("underlay", 85, _context_section)


# --------------------------------------------------------------------------- the pickers
def ask_page(win, doc) -> Optional[str]:
    """Underlay a Page…: the page browser, Pages only, in the page's project, the page itself
    not selected first (a seam: tests replace it)."""
    from ..browser import PageBrowserDialog

    path = getattr(doc, "path", None)
    return PageBrowserDialog.get_open_path(
        win, start_path=path, title="Underlay a Page", ok_text="Underlay", avoid_path=path
    )


def ask_image(win) -> Optional[str]:
    """Underlay an Image…: a picture file, from the folder of the last one (a seam)."""
    st = app_settings()
    start = str(st.value(DIR_KEY, "") or "")
    path, _f = QtWidgets.QFileDialog.getOpenFileName(win, "Underlay an Image", start, IMAGE_FILTER)
    if path:
        st.setValue(DIR_KEY, os.path.dirname(path))
    return path or None


def ask_version(win, doc, versions):
    """Underlay an Earlier Version…: one of ``versions`` (infra.history.Version), or None
    (a seam)."""
    from .underlay_panel import VersionPickerDialog

    dlg = VersionPickerDialog(versions, os.path.splitext(os.path.basename(doc.path))[0], win)
    try:
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        return dlg.current()
    finally:
        dlg.deleteLater()


def page_versions(doc) -> list:
    """The page's earlier versions (File > Page History), newest first; read again only
    when its versions' list changed on disk."""
    from ...infra import history as H

    if doc is None or not getattr(doc, "path", "") or getattr(doc, "element_name", None):
        return []  # (an element's versions are libraries, no pages)
    element = getattr(doc, "element_name", "") or ""
    folder = H.folder_for(doc.path, element)
    stamp = file_stamp(os.path.join(folder, H.MANIFEST))
    st = getattr(doc, "ui", None)
    memo = getattr(st, "underlay_versions", None) if st is not None else None
    if memo is not None and memo[0] == (folder, stamp):
        return memo[1]
    out = H.list_versions(doc.path, element) if stamp is not None else []
    if st is not None:
        st.underlay_versions = ((folder, stamp), out)
    return out


# ------------------------------------------------------------------- the window's side
class UnderlayCommands:
    """The editor window's side (a MainWindow mixin): View > Underlay."""

    def _build_underlay_actions(self) -> None:
        from .. import help as _help

        self.act_underlay_page = self._act("Underlay a Page…", self.underlay_page, None, "underlay_page")
        self.act_underlay_image = self._act("Underlay an Image…", self.underlay_image, None, "underlay_image")
        self.act_underlay_version = self._act(
            "Underlay an Earlier Version…", self.underlay_version, None, "underlay_version"
        )
        self.act_underlay_show = self._act(
            "Show Underlay", self.show_underlay, "Alt+Shift+U", "underlay", checkable=True
        )
        self.act_underlay_above = self._act(
            "Underlay Above Objects", self.underlay_above, None, "underlay_above", checkable=True
        )
        self.act_underlay_move = self._act(
            "Move Underlay with the Mouse", self.underlay_move, None, "underlay_move", checkable=True
        )
        self.act_underlay_settings = self._act(
            "Underlay Settings…", self.underlay_settings, None, "underlay_settings"
        )
        self.act_underlay_remove = self._act("Remove Underlay", self.remove_underlay, None, "underlay_remove")
        for a, anchor in (
            (self.act_underlay_page, "underlay-a-page"),
            (self.act_underlay_image, "underlay-a-picture"),
            (self.act_underlay_version, "underlay-an-earlier-version"),
            (self.act_underlay_show, "show-hide-and-remove"),
            (self.act_underlay_above, "above-or-under-the-objects"),
            (self.act_underlay_move, "move-it-with-the-mouse"),
            (self.act_underlay_settings, "the-underlay-window"),
            (self.act_underlay_remove, "show-hide-and-remove"),
        ):
            _help.set_help(a, "%s#%s" % (HELP_TOPIC, anchor))

    def _build_underlay_menu(self, menu: QtWidgets.QMenu) -> QtWidgets.QMenu:
        sub = menu.addMenu("Underlay")
        for a in (self.act_underlay_page, self.act_underlay_image, self.act_underlay_version):
            sub.addAction(a)
        sub.addSeparator()
        for a in (
            self.act_underlay_show,
            self.act_underlay_above,
            self.act_underlay_move,
            self.act_underlay_settings,
            self.act_underlay_remove,
        ):
            sub.addAction(a)
        self.underlay_menu = sub
        return sub

    def _install_underlay_ui(self) -> None:
        mover = self._underlay_mover = _Mover(self)
        self.view.installEventFilter(mover)
        self.view.viewport().installEventFilter(mover)
        _register_settings_hooks()

    # -- the state ---------------------------------------------------------------------
    def underlay(self, doc=None) -> Optional[Underlay]:
        """The underlay of ``doc`` (default: the page shown)."""
        return peek(self.doc if doc is None else doc)

    def _sync_underlay_actions(self, ul: Optional[Underlay]) -> None:
        """The checkable commands show the state of the page shown's underlay."""
        self.act_underlay_show.setChecked(bool(ul is not None and ul.shown))
        self.act_underlay_above.setChecked(bool(ul is not None and ul.above))
        self.act_underlay_move.setChecked(bool(ul is not None and ul.moving))
        for name in ("act_underlay_show", "act_underlay_above", "act_underlay_remove"):
            getattr(self, name).setEnabled(ul is not None)
        self.act_underlay_move.setEnabled(bool(ul is not None and ul.shown and ul.ready()))

    def _underlay_update_actions(self) -> None:
        """_update_actions: the commands follow the page shown (and its underlay comes back
        the first time the page shows, Settings > Canvas)."""
        doc = self.doc
        if doc is not None:
            self._restore_underlay(doc)
        ul = peek(doc)
        last = self.__dict__.get("_underlay_shown_for")
        if last is None or last() is not doc:
            # (another page shows: the page left stops moving its underlay where it is and
            # lets go of its tinted copy; the Underlay window shows this one's at once)
            left = peek(last()) if last is not None else None
            if left is not None and left is not ul:
                if left.moving:
                    self.stop_underlay_moving(left)
                left.release_drawn()
            self.__dict__["_underlay_shown_for"] = weakref.ref(doc) if doc is not None else (lambda: None)
            dlg = self._dialogs.get("underlay") if hasattr(self, "_dialogs") else None
            if dlg is not None and doc is not None:
                with contextlib.suppress(RuntimeError):
                    dlg.set_document(doc)
        self._sync_underlay_actions(ul)
        if ul is not None and ul.status in ("ready", "error") and not render_queue().busy_with(ul):
            stamp = file_stamp(ul.path)
            again = (
                (stamp is not None and ul.stamp is not None and stamp != ul.stamp)
                if ul.status == "ready"
                else (stamp is not None and stamp != ul.fail_stamp)
            )
            if again:
                # (its file was saved since, or a file that failed is there again or newer:
                # drawn again)
                load(ul)
                if ul.status != "loading":
                    self.underlay_changed(remember_it=False)

    def _underlay_versions(self) -> list:
        doc = self.doc
        if doc is None:
            return []
        try:
            return page_versions(doc)
        except Exception:  # noqa: BLE001 -- a garbled history folder lists nothing
            _note_exc("versions", page=getattr(doc, "path", ""))
            return []

    def _restore_underlay(self, doc) -> None:
        st = getattr(doc, "ui", None)
        if st is None or getattr(st, "underlay_restored", False):
            return
        st.underlay_restored = True
        if peek(doc) is not None or not option(REMEMBER_OPTION):
            return
        ul = remembered(doc)
        if ul is None:
            return
        if not os.path.isfile(ul.path):
            remember(doc, None)
            from ..notify import notify

            notify(
                self,
                "The underlay %s of %s is not there any more, so it was removed."
                % (ul.name(), os.path.splitext(os.path.basename(doc.path))[0]),
                "warning",
                key="underlay:%s" % page_name(doc),
                detail=ul.path,
            )
            return
        _set(doc, ul)
        load(ul)
        QtCore.QTimer.singleShot(0, self, lambda d=doc: self._after_restore(d))

    def _after_restore(self, doc) -> None:
        if doc is self.doc:
            self.underlay_changed(remember_it=False)

    def _underlay_doc_closed(self, doc) -> None:
        """``doc``'s tab closed: its render stops (the next tab's waits no more), its
        pictures go."""
        ul = peek(doc)
        if ul is None:
            return
        render_queue().drop(ul)
        self.stop_underlay_moving(ul)
        ul.release()
        _set(doc, None)

    def _underlay_doc_replaced(self, old, new) -> None:
        """``old`` was read again from its file as ``new`` (Revert to Saved): the underlay
        stays on the page."""
        ul = peek(old)
        st = getattr(new, "ui", None)
        if ul is None or st is None:
            return
        _set(old, None)
        st.underlay = ul
        st.underlay_restored = True

    def _underlay_tool_changed(self, tool: str = "") -> None:
        """Another tool was picked: Move with the Mouse ends, the underlay stays where it
        is. Not for Select: that is the tool coming back after Z held for a moment (the
        move made Select the tool, so it is the one it runs with)."""
        if tool == "select":
            return
        ul = peek(self.doc)
        if ul is not None and ul.moving:
            self.finish_underlay_move()

    def set_underlay(self, ul: Optional[Underlay], doc=None) -> None:
        """Give ``doc`` (the page shown) the underlay ``ul`` (None: none); its picture is
        drawn (a page in the render process)."""
        doc = self.doc if doc is None else doc
        if doc is None:
            return
        old = peek(doc)
        if old is not None and old is not ul:
            render_queue().drop(old)
            self.stop_underlay_moving(old)
        st = getattr(doc, "ui", None)
        if st is not None:
            st.underlay_restored = True
        _set(doc, ul)
        if ul is not None:
            load(ul)
        remember(doc, ul)
        if doc is self.doc:
            self.underlay_changed(remember_it=False)
            if ul is not None and ul.status == "error":
                self.underlay_failed(ul)
            elif ul is not None and ul.status == "ready":
                self.underlay_ready(ul)

    def underlay_failed(self, ul: Underlay) -> None:
        """Say that ``ul`` could not be drawn (once per failure)."""
        from ..notify import notify

        ul.announce = False
        notify(
            self,
            "The underlay %s could not be drawn: %s." % (ul.name(), ul.error.rstrip(".")),
            "error",
            key="underlay:%s" % ul.path,
            detail=ul.path,
            actions=[("Try Again", self.reload_underlay, lambda u=ul: peek(self.doc) is u)],
        )

    def underlay_ready(self, ul: Underlay) -> None:
        """``ul`` is drawn: a new one says where it lies and how to change that (once)."""
        if not ul.announce or ul.status != "ready":
            return
        ul.announce = False
        from ..notify import notify

        page = getattr(self.doc, "path", "") or ""
        if ul.kind == "image":
            text = "Underlaid %s at 100 %% at the page's top left corner." % ul.name()
        elif (
            ul.kind == "page"
            and page
            and os.path.normcase(os.path.abspath(page)) == os.path.normcase(ul.path)
        ):
            # (picked on purpose: the page under its own unsaved changes)
            text = "Underlaid %s as it was last saved, under your changes." % ul.name()
        else:
            text = "Underlaid %s, lined up with this page." % ul.name()
        notify(
            self,
            text,
            "success",
            key="underlay-new:%s" % ul.path,
            detail=ul.path,
            actions=[("Adjust…", self.underlay_settings, lambda u=ul: peek(self.doc) is u)],
        )

    def underlay_changed(self, remember_it: bool = True) -> None:
        """The underlay of the page shown changed (or the page shown did): the canvas, the
        commands, the status field and the Underlay window follow; kept in the settings.
        Tint None picked after the picture as it came went: drawn again from its file."""
        doc = self.doc
        ul = peek(doc)
        if ul is not None and ul.need_raw and not render_queue().busy_with(ul):
            ul.need_raw = False
            self.reload_underlay()
            if remember_it and doc is not None:
                remember(doc, ul)
            return
        with contextlib.suppress(RuntimeError):
            self.view.request_overlay_update(background=True)
        self._sync_underlay_actions(ul)
        f = getattr(self, "status_underlay", None)
        if f is not None:
            with contextlib.suppress(RuntimeError):
                f.refresh(self)
        dlg = self._dialogs.get("underlay") if hasattr(self, "_dialogs") else None
        if dlg is not None:
            with contextlib.suppress(RuntimeError):
                dlg.set_document(doc)
        if remember_it and doc is not None and ul is not None:
            remember(doc, ul)

    # -- the commands --------------------------------------------------------------------
    def _underlay_new(self, ul: Underlay) -> None:
        old = peek(self.doc)
        if old is not None:
            # (a new source keeps how the underlay is drawn; a picture starts at 100 %)
            for f in ("opacity", "tint", "above", "snap", "smooth", "template"):
                setattr(ul, f, getattr(old, f))
        ul.announce = True
        self.set_underlay(ul)
        if ul.status == "loading":
            self.statusBar().showMessage(DRAWING_NOTE % ul.name(), 8000)

    def underlay_page(self) -> None:
        """View > Underlay > Underlay a Page…"""
        doc = self.doc
        if doc is None:
            return
        path = ask_page(self, doc)
        if path:
            self._underlay_new(Underlay("page", path))

    def underlay_image(self) -> None:
        """View > Underlay > Underlay an Image…"""
        if self.doc is None:
            return
        path = ask_image(self)
        if path:
            self._underlay_new(Underlay("image", path))

    def underlay_version(self) -> None:
        """View > Underlay > Underlay an Earlier Version…"""
        doc = self.doc
        versions = self._underlay_versions()
        if doc is None:
            return
        if not versions:
            self.statusBar().showMessage(
                "%s has no earlier versions yet. Each save keeps one (Settings > Editing > "
                "Safety)." % os.path.splitext(os.path.basename(doc.path))[0],
                6000,
            )
            return
        v = ask_version(self, doc, versions)
        if v is not None:
            self.underlay_version_file(v)

    def underlay_version_file(self, v) -> None:
        """Underlay the earlier version ``v`` (an infra.history.Version) of the page shown."""
        doc = self.doc
        if doc is None:
            return
        self._underlay_new(Underlay("version", v.file, page_path=doc.path, when=v.when))

    def show_underlay(self, on=None) -> None:
        """View > Underlay > Show Underlay (checkable)."""
        ul = peek(self.doc)
        if ul is None:
            self.act_underlay_show.setChecked(False)
            return
        ul.shown = (not ul.shown) if on is None else bool(on)
        if not ul.shown and ul.moving:
            self.stop_underlay_moving(ul)
        ul.touch()
        self.underlay_changed()
        self.statusBar().showMessage("Underlay shown." if ul.shown else "Underlay hidden.", 2500)

    def underlay_above(self, on=None) -> None:
        """View > Underlay > Underlay Above Objects (checkable)."""
        ul = peek(self.doc)
        if ul is None:
            self.act_underlay_above.setChecked(False)
            return
        ul.above = (not ul.above) if on is None else bool(on)
        ul.touch()
        self.underlay_changed()

    def underlay_move(self, on=None) -> None:
        """View > Underlay > Move Underlay with the Mouse (checkable; the Underlay window's
        button and the canvas menu too): on, a drag on the page moves the underlay; off, it
        stays where it is."""
        ul = peek(self.doc)
        if ul is None:
            self.act_underlay_move.setChecked(False)
            return
        on = (not ul.moving) if on is None else bool(on)
        if on:
            self.set_underlay_moving(True)
        else:
            self.finish_underlay_move()

    def underlay_settings(self) -> None:
        """View > Underlay > Underlay Settings…: the Underlay window (modeless; it shows the
        underlay of the page tab shown)."""
        if self.doc is None:
            return
        dlg = self._dialogs.get("underlay")
        if self._raise_modeless(dlg):
            dlg.set_document(self.doc)
            return
        from .underlay_panel import UnderlayDialog

        dlg = UnderlayDialog(self)
        dlg._ctg_reads_page = True
        dlg._ctg_follow = lambda doc, d=dlg: d.set_document(doc)
        self._show_modeless(dlg, "underlay", scope="window", title="Underlay")
        dlg.set_document(self.doc)

    def remove_underlay(self, *_args, doc=None) -> None:
        """View > Underlay > Remove Underlay: the page shown (``doc``) has none any more
        (nothing is drawn, nothing is kept for it). (The menu's checked flag goes to
        ``_args``.)"""
        doc = self.doc if doc is None else doc
        ul = peek(doc)
        if ul is None:
            return
        render_queue().drop(ul)
        self.stop_underlay_moving(ul)
        _set(doc, None)
        ul.release()
        remember(doc, None)
        if doc is self.doc:
            self.underlay_changed(remember_it=False)
            self.statusBar().showMessage("Removed the underlay %s." % ul.name(), 4000)

    # -- placing it -------------------------------------------------------------------------
    def move_underlay_to(self, x: float, y: float, remember_it: bool = True) -> None:
        ul = peek(self.doc)
        if ul is None:
            return
        lim = COORD_RANGE
        ul.x = float(max(-lim, min(lim, x)))
        ul.y = float(max(-lim, min(lim, y)))
        ul.touch()
        self.underlay_changed(remember_it=remember_it)

    def set_underlay_moving(self, on: bool) -> None:
        """Move with the Mouse on or off (off: where it is now; :meth:`finish_underlay_move`
        also keeps it in the settings). On: Select is the tool (a drawing tool or the Hand
        would take the drag), the canvas takes the keyboard (the arrow keys nudge it, Enter
        ends, Esc puts it back)."""
        ul = peek(self.doc)
        if ul is None:
            self._sync_underlay_actions(None)
            return
        on = bool(on) and ul.shown and ul.ready()
        if on == ul.moving:
            self.underlay_changed(remember_it=False)
            return
        if on and self.view.tool() != "select":
            self._set_tool("select")
        ul.moving = on
        ul.move_origin = (ul.x, ul.y) if on else None
        vp = self.view.viewport()
        if on:
            vp.setCursor(QtCore.Qt.OpenHandCursor)
            self.activateWindow()
            self.view.setFocus(QtCore.Qt.OtherFocusReason)
            self.statusBar().showMessage(
                "Drag on the page to move the underlay. The arrow keys move it 1 px (Shift: "
                "10 px). Enter ends, Esc puts it back.",
                8000,
            )
        else:
            vp.unsetCursor()
            mover = getattr(self, "_underlay_mover", None)
            if mover is not None:
                mover.drag = None
        self.underlay_changed(remember_it=False)

    def finish_underlay_move(self) -> None:
        """Move with the Mouse ends with the underlay where it is now (Enter, a click on the
        status field, the command again, another tool, the Underlay window closed)."""
        ul = peek(self.doc)
        if ul is None or not ul.moving:
            return
        self.set_underlay_moving(False)
        self.underlay_changed()
        self.statusBar().showMessage("The underlay stays where it is.", 3000)

    def stop_underlay_moving(self, ul: Underlay) -> None:
        """``ul`` stops moving with the mouse (the canvas's own pointer and drags again)."""
        if not ul.moving:
            return
        ul.moving = False
        ul.move_origin = None
        mover = getattr(self, "_underlay_mover", None)
        if mover is not None:
            mover.drag = None
        with contextlib.suppress(RuntimeError):
            self.view.viewport().unsetCursor()

    def end_underlay_move(self, put_back: bool = False) -> None:
        """Esc while it moves: back where it was when moving began (``put_back``), and the
        mouse moves objects again."""
        ul = peek(self.doc)
        if ul is None:
            return
        if put_back and ul.move_origin is not None:
            ul.x, ul.y = ul.move_origin
            ul.touch()
        self.set_underlay_moving(False)
        self.underlay_changed()
        if put_back:
            self.statusBar().showMessage("The underlay is back where it was.", 3000)

    def fit_underlay(self, fit: bool = True) -> None:
        """Fit Page (the underlay fits the page, centred) or Actual Size (100 % at 0, 0)."""
        doc = self.doc
        ul = peek(doc)
        if ul is None:
            return
        if not fit:
            ul.x, ul.y, ul.scale = 0.0, 0.0, 1.0
        else:
            try:
                pw = float(doc.page.appearance.width or 0)
                ph = float(doc.page.appearance.height or 0)
            except AttributeError:
                pw = ph = 0.0
            bw, bh = ul.base
            if pw <= 0 or ph <= 0 or bw <= 0 or bh <= 0:
                return
            s = max(SCALE_RANGE[0], min(SCALE_RANGE[1], min(pw / bw, ph / bh)))
            ul.scale = s
            ul.x = round((pw - bw * s) / 2.0, 2)
            ul.y = round((ph - bh * s) / 2.0, 2)
        ul.touch()
        self.underlay_changed()

    def reload_underlay(self) -> None:
        """Draw the page shown's underlay again from its file (the resolution or the template
        choice changed, Tint None needs the picture as it came, Try Again)."""
        ul = peek(self.doc)
        if ul is None:
            return
        ul.need_raw = False
        render_queue().drop(ul, quiet=True)
        load(ul)
        self.underlay_changed()
        if ul.status == "error":
            self.underlay_failed(ul)


# ----------------------------------------------------------------------------- settings
def _resolution_changed(window) -> None:
    """Settings > Canvas > Underlay > the resolution: every page underlay is drawn again."""
    for doc in _docs_of(window):
        ul = peek(doc)
        if ul is not None and ul.is_page():
            render_queue().drop(ul, quiet=True)
            load(ul)
    with contextlib.suppress(RuntimeError, AttributeError):
        window.underlay_changed(remember_it=False)


def _register_settings_hooks() -> None:
    from ..prefs import settings_pages as SP

    if _resolution_changed not in SP.APPLY_HOOKS.get(RESOLUTION_KEY, []):
        SP.register_apply(RESOLUTION_KEY, _resolution_changed)


_register_field()
_register_menu()
