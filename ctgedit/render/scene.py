"""Draw the document model onto a QGraphicsScene."""

from __future__ import annotations

import bisect
import contextlib
import math

import os
import re

from PySide6 import QtCore, QtGui, QtWidgets

from ..model.document import GraphicObject, ObjectKind, Page, TextJustify

# at module level: an import inside _text_dpi / _gdi_ascent runs PySide's import hook per text
from ..format import textmetrics as _textmetrics
from . import buttons as _btn
from ..infra.logsetup import note_exception as _note_exc

#: a genie parameter token in a caption ('%Line3%', '%Title=default%')
_GENIE_TOKEN = re.compile(r"%[^%\s=]+(?:=[^%]*)?%")

# Qt's enum values the item builders read for every item, looked up once (a QtCore.Qt
# attribute lookup is slow)
_QT_NO_PEN = QtCore.Qt.NoPen
_QT_NO_BRUSH = QtCore.Qt.NoBrush
_QT_SMOOTH = QtCore.Qt.SmoothTransformation
_QT_RGBA8888 = QtGui.QImage.Format_RGBA8888


# Citect on-disk colours are packed 0x00RRGGBB, not Win32 COLORREF 0x00BBGGRR (the catalog
# still documents BBGGRR). High byte 0xFF = no-colour/transparent sentinel -> default.
def colorref_to_qcolor(c: int | None, default: QtGui.QColor | None = None) -> QtGui.QColor | None:
    if c is None:
        return default
    if (c >> 24) & 0xFF == 0xFF:
        return default
    r = (c >> 16) & 0xFF
    g = (c >> 8) & 0xFF
    b = c & 0xFF
    return QtGui.QColor(r, g, b)


def _gdi_ascent(obj, lines: int, box_h: float) -> "int | None":
    """GDI's ascent for a text's font, when its box is the one CtDraw measured."""
    st = obj.style
    if obj.raw.get("_font_px") or not st.font_size:
        return None
    attribs = (
        (0x10 if st.bold else 0)
        | (0x20 if st.italic else 0)
        | (0x40 if st.underline else 0)
        | (0x80 if st.strikeout else 0)
    )
    try:
        m = _textmetrics.font_metrics(st.font or "Arial", int(st.font_size), attribs)
    except Exception:  # noqa: BLE001
        _note_exc("scene._gdi_ascent")
        return None
    if m is None or abs(box_h - m[1] * max(1, lines)) > 1:
        return None
    return m[0]


def _em(points: int) -> int:
    """A font's em in page pixels, the way CtDraw sizes it."""
    return max(1, _text_dpi() * int(points) // 72)


def _text_dpi() -> int:
    """The LOGPIXELSY text is sized at (96 without GDI)."""
    return _textmetrics.system_dpi() or 96


def font_px(points: int) -> int:
    """A font of ``points`` in page pixels as the page draws its text (:func:`_em`): what the
    editor's typing preview of a new text takes (ui/editor/canvas)."""
    return _em(points)


def _contrast_text_colour(bg: QtGui.QColor | None) -> QtGui.QColor:
    """Black/white text for legibility over an optional backdrop."""
    if bg is None:
        return QtGui.QColor("#1a1a1a")
    lum = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    return QtGui.QColor("#000000") if lum > 140 else QtGui.QColor("#ffffff")


# kinds rendered as a labelled placeholder (no native primitive yet).
# GROUP excluded: its members are separate records that draw themselves, so a
# box for the group would just clutter over its own children.
_PLACEHOLDER_KINDS = frozenset(
    {
        ObjectKind.SYMBOL,
        ObjectKind.SYMBOL_SET,
        ObjectKind.GENIE,
        ObjectKind.COMPOSITE_GENIE,
        ObjectKind.TREND,
        ObjectKind.UNKNOWN,
    }
)

# Drawable LEAF kinds that PAINT THEIR OWN ART even when they own children: a record of one of
# these whose children are behaviour records (Touch / Keyboard) draws its shape, unlike a real
# container (group/genie/symbol/symbol_set) whose member leaves draw themselves. Mirrors
# reader._LEAF_KINDS.
_PAINTS_WITH_MEMBERS = frozenset(
    {
        ObjectKind.LINE,
        ObjectKind.RECTANGLE,
        ObjectKind.ELLIPSE,
        ObjectKind.POLYGON,
        ObjectKind.PIPE,
        ObjectKind.TEXT,
        ObjectKind.NUMBER,
        ObjectKind.BUTTON,
        ObjectKind.TREND,
    }
)

# the container classes: a group / genie / template (0x08) and a symbol set (0x0C)
_CONTAINER_TCS = frozenset({0x08, 0x0C})


def _gdi_line(x0: int, y0: int, x1: int, y1: int) -> list:
    """The pixels GDI's LineTo sets from (x0, y0) to (x1, y1), the end pixel left out (along
    the major axis; a half rounds down)."""
    dx, dy = x1 - x0, y1 - y0
    n = max(abs(dx), abs(dy))
    pts = []
    for k in range(n):
        if abs(dx) >= abs(dy):
            x = x0 + (k if dx > 0 else -k)
            pts.append((x, math.ceil(y0 + dy * (x - x0) / dx - 0.5)))
        else:
            y = y0 + (k if dy > 0 else -k)
            pts.append((math.ceil(x0 + dx * (y - y0) / dy - 0.5), y))
    return pts


def _paints_with_members(obj: GraphicObject) -> bool:
    """Does a record that owns children still paint its own art?"""
    return obj.kind in _PAINTS_WITH_MEMBERS or (
        obj.raw.get("_embedded_bmp") is not None and obj.raw.get("_type_code") not in _CONTAINER_TCS
    )


def _in_placed_element(page: Page, i: int) -> bool:
    """Whether record ``i`` lies inside a placed genie / linked symbol / template
    (a tc 0x08 ancestor that is a genie instance, dwFlags 0x200, or links a
    library element, dwFlags 0x1): its members are art-stripped copies whose
    look CtDraw takes from the element."""
    objs = page.objects
    p = objs[i].parent
    while p is not None and 0 <= p < len(objs):
        a = objs[p]
        if a.raw.get("_type_code") == 0x08 and (a.raw.get("_dwflags", 0) or 0) & 0x201:
            return True
        p = a.parent
    return False


# the classes whose 3D effects / rectangle border the scene draws (CtDraw's base 3D effects for
# Line / PolyLine / Circle, the Square's own bevel); text and button captions draw their own
# (_text_item / the button draw)
_EFFECT_KINDS = frozenset({ObjectKind.LINE, ObjectKind.RECTANGLE, ObjectKind.ELLIPSE, ObjectKind.POLYGON})
_RECT_TCS = frozenset({0x0F, 0x0E, 0x03})

# Item data role carrying the PAGE INDEX of the root a scene item belongs to,
# stamped on every item of every root's subtree by set_page(). Hit-testing reads
# it directly and NEVER walks with parentItem()/topLevelItem(): in PySide6
# QGraphicsItem.parentItem() is typesystem-ruled <parent index="return"
# action="add"/> on `this`, so on a TOP-LEVEL item its None return detaches the
# item from the scene's ownership and hands it to Python: a scene item no
# Python name references is then DELETED as soon as the wrapper is dropped.
# Keep a Python reference to every loose top-level item (self._items,
# self._ai_items, self._backdrop).
PAGE_INDEX_ROLE = 1
# Item data role naming the element record (``id()`` of its GraphicObject in the owner's
# ``children``) an item inside a resolved symbol / genie / composite-genie picture draws: the
# simulator finds the item of a composite genie's member through it (sim_states).
SIM_CHILD_ROLE = 2
# Item data role on a trend item's parts (_trend_item): 'box' = the pen lines' clip box, 'design' = what
# only the editor draws (the design-time sine traces, the dotted focus rectangle), 'pen' = one
# of the 8 hidden pen lines the simulator draws the engine's samples into (sim_states).
TREND_PART_ROLE = 4
TREND_PENS = 8
# Item data role on an alarm indicator's parts (_alarm_indicator_item): 'strip' = the four
# priority-coloured strips, 'flag' = the flag genie (or its 20 x 20 placeholder): the simulator
# recolours / swaps them for the priority of the active alarm (sim_states).
AI_PART_ROLE = 5


def _pixel_align(item) -> None:
    """Put odd-width strokes on pixel centres, as GDI does."""
    try:
        pen = item.pen()
    except AttributeError:
        return
    if pen.style() == _QT_NO_PEN:
        return
    w = pen.widthF() or 1.0
    if int(round(w)) % 2 == 0:
        return
    if isinstance(item, (QtWidgets.QGraphicsRectItem, QtWidgets.QGraphicsEllipseItem)):
        item.setRect(item.rect().translated(0.5, 0.5))
    elif isinstance(item, QtWidgets.QGraphicsPathItem):
        item.setPath(item.path().translated(0.5, 0.5))
    elif isinstance(item, QtWidgets.QGraphicsPolygonItem):
        item.setPolygon(item.polygon().translated(0.5, 0.5))
    elif isinstance(item, QtWidgets.QGraphicsLineItem):
        item.setLine(item.line().translated(0.5, 0.5))


class JustifiedTextItem(QtWidgets.QGraphicsSimpleTextItem):
    """A text whose lines each sit by the text's justify inside the block, as CtDraw lays
    them out (every line from the anchor by its justify, the baseline
    stepping by tmHeight). QGraphicsSimpleTextItem left-aligns every line of a block; a
    one-line or left-justified text paints exactly as it does."""

    def __init__(self, text: str = "", parent=None, align: int = 0):
        super().__init__(text, parent)
        #: 0 left, 1 centre, 2 right
        self.align = align

    def paint(self, painter, option, widget=None):
        text = self.text()
        if not self.align or "\n" not in text:
            return super().paint(painter, option, widget)
        fm = QtGui.QFontMetricsF(self.font())
        w = self.boundingRect().width()
        painter.save()
        painter.setFont(self.font())
        painter.setPen(QtGui.QPen(self.brush(), 0))
        y = fm.ascent()
        for line in text.split("\n"):
            lw = fm.horizontalAdvance(line)
            x = (w - lw) / 2.0 if self.align == 1 else (w - lw)
            painter.drawText(QtCore.QPointF(x, y), line)
            y += fm.height()
        painter.restore()


class _TextFrame(QtWidgets.QGraphicsItem):
    """The dotted frame CtDraw's editor draws round a text it cannot draw as placed (a
    stretched or mirrored text, reader._text_needs_frame): a 1 px PS_DOT pen in R2_NOTXORPEN
    (the pixels under the dots inverted), on screen only -- never in a print, a bitmap or a
    thumbnail."""

    def __init__(self, rect: QtCore.QRectF, parent=None):
        super().__init__(parent)
        self._rect = QtCore.QRectF(rect)

    def boundingRect(self) -> QtCore.QRectF:
        return self._rect.adjusted(-1, -1, 1, 1)

    def paint(self, painter, option, widget=None):
        sc = self.scene()
        # editor chrome: never in an export, nor in the Simulate window's runtime look (the
        # runtime draws no frame)
        if sc is not None and (getattr(sc, "_export", None) or getattr(sc, "_runtime_look", False)):
            return
        r = self._rect
        if r.width() < 2 or r.height() < 2:
            return
        painter.save()
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
            if (
                painter.device() is not None
                and painter.paintEngine() is not None
                and painter.paintEngine().type() == QtGui.QPaintEngine.Raster
            ):
                painter.setCompositionMode(QtGui.QPainter.RasterOp_NotDestination)
            pen = QtGui.QPen(QtGui.QColor("black"), 0)
            pen.setDashPattern([3, 3])
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            # GDI's Rectangle(l, t, r, b) outlines l .. r-1, t .. b-1
            painter.drawRect(QtCore.QRectF(r.left() + 0.5, r.top() + 0.5, r.width() - 1, r.height() - 1))
        finally:
            painter.restore()


def _justify_code(obj) -> int:
    j = obj.style.justify
    return 1 if j == TextJustify.CENTRE else 2 if j == TextJustify.RIGHT else 0


class StrokePathItem(QtWidgets.QGraphicsPathItem):
    """An open polyline whose hit shape is the stroke only."""

    PICK_SLOP = 4.0

    def shape(self) -> QtGui.QPainterPath:
        if self.brush().style() != QtCore.Qt.NoBrush:
            return super().shape()  # a FILLED open polygon hit-tests by its area
        stroker = QtGui.QPainterPathStroker()
        stroker.setWidth(max(self.pen().widthF(), 1.0) + self.PICK_SLOP)
        stroker.setCapStyle(self.pen().capStyle())
        stroker.setJoinStyle(self.pen().joinStyle())
        return stroker.createStroke(self.path())


class PaintOnlyPathItem(QtWidgets.QGraphicsPathItem):
    """A drawn part that picks nothing itself; its parent is the pick target."""

    _EMPTY = QtGui.QPainterPath()

    def shape(self) -> QtGui.QPainterPath:
        return self._EMPTY

    def contains(self, point) -> bool:
        return False

    def boundingRect(self) -> QtCore.QRectF:
        # (Qt's own is the bounds of shape() when the pen has a width: the empty shape would
        # cull the paint. These are the bounds Qt's own shape gives, made once per path /
        # pen: setPath and setPen drop them)
        r = self.__dict__.get("_bounds")
        if r is None:
            if self.pen().widthF() == 0:
                r = self.path().controlPointRect()
            else:
                r = QtWidgets.QGraphicsPathItem.shape(self).controlPointRect()
            self._bounds = r
        return QtCore.QRectF(r)

    # (dropped after Qt's own call: its prepareGeometryChange asks for the old bounds)
    def setPath(self, path) -> None:  # noqa: N802 -- Qt naming
        QtWidgets.QGraphicsPathItem.setPath(self, path)
        self._bounds = None

    def setPen(self, pen) -> None:  # noqa: N802 -- Qt naming
        QtWidgets.QGraphicsPathItem.setPen(self, pen)
        self._bounds = None


class HollowPolygonItem(QtWidgets.QGraphicsPolygonItem):
    """Closed polygon that hit-tests by its EDGE when unfilled (CtDraw selects
    a hollow shape by its outline, not its interior); filled polygons keep the
    area hit."""

    PICK_SLOP = 4.0

    def shape(self) -> QtGui.QPainterPath:
        if self.brush().style() != QtCore.Qt.NoBrush:
            return super().shape()
        p = QtGui.QPainterPath()
        p.addPolygon(self.polygon())
        p.closeSubpath()
        stroker = QtGui.QPainterPathStroker()
        stroker.setWidth(max(self.pen().widthF(), 1.0) + self.PICK_SLOP)
        stroker.setJoinStyle(self.pen().joinStyle())
        return stroker.createStroke(p)


#: GDI's dash patterns in pixels for a cosmetic pen (CreatePen with width 1):
#: PS_DASH 18-6, PS_DOT 3-3, PS_DASHDOT 9-6-3-6, PS_DASHDOTDOT 9-3-3-3-3-3;
#: and in pen widths for a geometric pen (ExtCreatePen)
_GDI_DASHES = {1: [18, 6], 2: [3, 3], 3: [9, 6, 3, 6], 4: [9, 3, 3, 3, 3, 3]}
_GDI_GEOMETRIC_DASHES = {1: [3, 1], 2: [1, 1], 3: [3, 1, 1, 1], 4: [3, 1, 1, 1, 1, 1]}


def _gdi_pen_style(pen: QtGui.QPen, style) -> None:
    """The pen style as CtDraw creates it."""
    ls = (style.line_style or 0) & 0x0F
    if ls == 5:  # PS_NULL: no line at all
        pen.setStyle(QtCore.Qt.NoPen)
        return
    raw = style.style_flags[1] if style.style_flags else ls
    geometric = bool(raw & 0xF0)
    if geometric:
        pen.setCapStyle(
            {0x10: QtCore.Qt.SquareCap, 0x20: QtCore.Qt.FlatCap}.get(raw & 0x30, QtCore.Qt.RoundCap)
        )
        pen.setJoinStyle(
            {0x40: QtCore.Qt.BevelJoin, 0x80: QtCore.Qt.MiterJoin}.get(raw & 0xC0, QtCore.Qt.RoundJoin)
        )
        pattern = _GDI_GEOMETRIC_DASHES.get(ls)
    else:
        pattern = _GDI_DASHES.get(ls) if pen.width() <= 1 else None
        if pattern is not None:
            pen.setCapStyle(QtCore.Qt.FlatCap)  # a GDI dash ends where it ends
    if pattern is not None:
        pen.setDashPattern(pattern)


_DYN_BADGE = {
    "visibility": ("V", "#3b82f6"),
    "movement": ("M", "#a855f7"),
    "fill_colour": ("C", "#ef4444"),
    "fill_level": ("L", "#06b6d4"),
    "symbol_set": ("S", "#f59e0b"),
    "display_value": ("#", "#10b981"),
    "touch": ("T", "#ec4899"),
    "keyboard": ("K", "#8b5cf6"),
}


_HATCH = None


def _element_hatch() -> QtGui.QImage:
    """CtDraw's element-window background tile (8x8, #C0C0C0 on white)."""
    global _HATCH
    if _HATCH is None:
        rows = (
            "..#..#..",
            ".#....#.",
            "#......#",
            "#......#",
            ".#....#.",
            "..#..#..",
            "...##...",
            "...##...",
        )
        img = QtGui.QImage(8, 8, QtGui.QImage.Format_RGB32)
        for y, row in enumerate(rows):
            for x, c in enumerate(row):
                img.setPixel(x, y, 0xFFC0C0C0 if c == "#" else 0xFFFFFFFF)
        _HATCH = img
    return _HATCH


def _render_env() -> tuple:
    """The environment switches the item builders read: a change means every
    item must be built again (PageScene.update_units refuses)."""
    return (os.environ.get("CTG_DYN_BADGES"),)


#: the page texts' fonts by (face, pixel size, bold, italic, underline, strikeout, aliased):
#: a text takes a copy (PageScene._object_font)
_FONT_CACHE: dict = {}
#: the empty pen and brush of the invisible anchors (setPen / setBrush copy them)
_ANCHOR_PEN = QtGui.QPen(_QT_NO_PEN)
_ANCHOR_BRUSH = QtGui.QBrush(_QT_NO_BRUSH)


#: the page coordinate range a record can store: CtDraw keeps every object
#: within +/-32000 (Adjust Position and Size's own limit; the record fields are
#: i16). The work area, the canvas's scroll surface and every point the canvas
#: draws, drops or moves to stay inside it.
COORD_LIMIT = 32000

#: View > Outside the Page (setting 'view/surround'): 'desk' = the window theme's desk
#: colour with the page on it as a card (a soft shadow, the grid on the page only: the
#: default), 'shaded' = the page colour mixed 15 % toward the window's workspace colour
#: (off-page objects stay legible, the page still reads as the page), 'page' = the page
#: colour everywhere (the Graphics Builder: its canvas brush is the page brush),
#: 'workspace' = the flat workspace colour of the window theme
SURROUND_MODES = ("desk", "shaded", "page", "workspace")
_SHADE = 0.15

#: the editor look (Settings > Canvas, View > Grid; PageScene.set_look): the grid's major
#: line every N grid steps (0 = none, 1 = every line), the grid style ('lines' / 'dots'),
#: the grid outside the page too (the Graphics Builder's look), the pixel grid at 800 % and
#: more, the page edge ('dotted' = CtDraw's, 'line', 'none'), the page's shadow. Editor
#: chrome only: a render, a print, a thumbnail or Convert to Bitmap never draws any of it.
EDITOR_LOOK = {
    "grid_major": 8,
    "grid_style": "lines",
    "grid_outside": False,
    "pixel_grid": True,
    "page_edge": "dotted",
    "page_shadow": True,
}
GRID_STYLES = ("lines", "dots")
PAGE_EDGES = ("dotted", "line", "none")
GRID_MAJOR_MAX = 100
#: the pixel grid shows from this zoom (800 %)
PIXEL_GRID_ZOOM = 8.0
#: grid lines (and dots) closer than this on screen are not drawn; minor lines fade in
#: over the next GRID_FADE_PX
GRID_MIN_PX = 4.0
GRID_FADE_PX = 8.0
#: the page's shadow: (alpha, offset down and right, spread) per layer, in device pixels
_SHADOW_LAYERS = ((6, 4, 3), (12, 3, 2), (25, 3, 1), (40, 2, 0))


def clamp_rect(r: QtCore.QRectF, limit: float = COORD_LIMIT) -> QtCore.QRectF:
    """``r`` cut to +/- ``limit`` on both axes."""
    left, top = max(r.left(), -limit), max(r.top(), -limit)
    right, bottom = min(r.right(), limit), min(r.bottom(), limit)
    if right < left or bottom < top:
        return QtCore.QRectF()
    return QtCore.QRectF(QtCore.QPointF(left, top), QtCore.QPointF(right, bottom))


def _luminance(c: QtGui.QColor) -> float:
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


def _contrast_ratio(a: QtGui.QColor, b: QtGui.QColor) -> float:
    """The WCAG contrast ratio of two colours (1 = the same, 21 = black / white)."""

    def rel(c):
        out = []
        for v in (c.redF(), c.greenF(), c.blueF()):
            out.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]

    la, lb = rel(a), rel(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _high_contrast() -> bool:
    """Whether the window runs Windows' high-contrast colours (ui/style/theme's
    'contrast' scheme). Read from the theme module only when the editor has
    loaded it -- the scene itself never imports the UI."""
    import sys

    theme = sys.modules.get(__name__.rsplit(".", 2)[0] + ".ui.style.theme")
    try:
        return theme is not None and theme.current_scheme() == "contrast"
    except Exception:  # noqa: BLE001 -- a theme problem must not break painting
        _note_exc("scene._high_contrast")
        return False


def _round(v: float) -> int:
    return int(math.floor(v + 0.5))


class _AnPointMarker(QtWidgets.QGraphicsItem):
    """CtDraw's editor marker for a V3/V4 animation point."""

    _FONT = None

    def __init__(self, scene, x: int, y: int, an: int):
        super().__init__()
        self._scene_ref = scene
        self._x, self._y, self._an = int(x), int(y), int(an)
        # the pixel under the point / the number's origin when it lies ON the page (set by
        # PageScene._resolve_marker_colours from the drawn content); None = decided at
        # paint time (off the page: the surround)
        self.under = None
        self.under_text = None

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(self._x - 4, self._y - 9, 25, 15)

    @staticmethod
    def _ink(rgb) -> QtGui.QColor:
        r, g, b = (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
        return QtGui.QColor(0, 0, 0) if min(r, g, b) >= 0xEC else QtGui.QColor(255, 255, 255)

    def _under(self, known, x, y) -> int:
        if known is not None:
            return known
        sc = self._scene_ref
        if 0 <= x < sc._page_w and 0 <= y < sc._page_h:
            c = sc._page_bg
        else:
            c = sc.surround_colour() if sc._chrome else sc._page_bg
        return c.rgb() & 0xFFFFFF

    def paint(self, painter, option, widget=None):
        x, y = self._x, self._y
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, False)
        ink = self._ink(self._under(self.under, x, y))
        painter.fillRect(QtCore.QRectF(x - 4, y, 9, 1), ink)  # MoveTo x-4 .. LineTo x+5
        painter.fillRect(QtCore.QRectF(x, y - 4, 1, 9), ink)
        if _AnPointMarker._FONT is None:
            f = QtGui.QFont("Arial")
            f.setPixelSize(10)
            f.setStyleStrategy(QtGui.QFont.NoAntialias)
            _AnPointMarker._FONT = f
        painter.setFont(_AnPointMarker._FONT)
        painter.setPen(self._ink(self._under(self.under_text, x + 3, y - 9)))
        painter.drawText(QtCore.QPointF(x + 3, y), str(self._an))  # baseline: ascent 9
        painter.restore()


class PageScene(QtWidgets.QGraphicsScene):
    """Scene mirroring a Page. set_page() rebuilds it; update_units() rebuilds what changed."""

    # the WORKSPACE colour around the page (ui/style/theme sets the theme's 'desk');
    # near-black in the dark theme
    _MARGIN_COLOUR = QtGui.QColor("#2a2d31")
    # update_units rebuilds at most this share of the page's objects (else set_page)
    patch_limit = 0.5
    #: the work area changed (set_page, grown by update_units, element mode)
    workAreaChanged = QtCore.Signal(QtCore.QRectF)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page: Page | None = None
        self._items: dict[int, QtWidgets.QGraphicsItem] = {}
        self._ai_items: dict[int, QtWidgets.QGraphicsItem] = {}  # alarm indicators
        self._ai_static: dict = {}  # alarm indicator -> its members' built bounds
        self._runtime_look = False  # the Simulate tab's look (set_runtime_look)
        self._aliased_text = False  # text without smoothing (set_aliased_text)
        self._backdrop = None  # the thumbnail underlay (a loose item)
        self._export = None  # set by export_only()
        self._page_w = 0
        self._page_h = 0
        self._page_bg = QtGui.QColor("#d4d0c8")
        self._grid_size = 0  # horizontal pitch (0 = no grid)
        self._grid_y = 0  # vertical pitch
        self._grid_origin = (0, 0)
        self._element_hotspot = None  # set_element_mode(): a library element window
        self._element_mode = False
        # Situational Awareness panes: editor-only labels (drawForeground);
        # [(pane record, root, (l, t, r, b), title, detail, problem)]
        self._pane_labels: list = []
        self._pane_labels_on = True
        self._work = QtCore.QRectF()  # the work area (the scene rect)
        self._surround = "desk"  # View > Outside the Page
        self._look = dict(EDITOR_LOOK)  # the editor look (set_look)
        self._chrome = 0  # > 0 while a canvas paints (editor_chrome)
        self._hidden_applied = False  # some item may be editor-hidden
        self._covered: set = set()  # the records an editor-hidden root covers
        self._sim = None  # the simulator's live states (sim_states.SimLayer)
        self._build_pixmaps = None  # while a build runs: its pictures' pixmaps
        #: bumped whenever items are built or dropped (a canvas caches the
        #: selected unit's bounds per build)
        self.build_serial = 0

    # -- the work area ------------------------------------------------------
    def page_rect(self) -> QtCore.QRectF:
        """The page, (0, 0, W, H); null before a page is set."""
        if self._page_w <= 0 or self._page_h <= 0:
            return QtCore.QRectF()
        return QtCore.QRectF(0, 0, self._page_w, self._page_h)

    def work_rect(self) -> QtCore.QRectF:
        """The work area (see the class notes); null before a page is set."""
        return QtCore.QRectF(self._work)

    def _work_for(self, content: QtCore.QRectF) -> QtCore.QRectF:
        """The work area of the page and ``content`` (the drawn items)."""
        w, h = self._page_w, self._page_h
        if w <= 0 or h <= 0:
            return QtCore.QRectF()
        r = QtCore.QRectF(0, 0, w, h)
        if self._element_mode:
            margin = 40  # an element window: its canvas + content
        else:
            # CtDraw's scroll surface: the page x 1.1 from (0, 0), integer maths
            r = r.united(QtCore.QRectF(0, 0, (w * 11) // 10, (h * 11) // 10))
            margin = max(40, _round(0.05 * max(w, h)))
        if content is not None and not content.isNull():
            r = r.united(content)
        return clamp_rect(r.adjusted(-margin, -margin, margin, margin))

    def _set_work(self, work: QtCore.QRectF) -> None:
        if work.isNull() or work == self._work:
            return
        self._work = QtCore.QRectF(work)
        self.setSceneRect(work)
        self.workAreaChanged.emit(QtCore.QRectF(work))

    def _update_work_area(self, grow_only: bool = False, items=()) -> None:
        """Set the work area from the drawn items (``grow_only``: only grow
        it to cover ``items``, the items just built -- a shrink waits for the
        next set_page, since every scene-rect change re-indexes the scene)."""
        if not grow_only or self._work.isNull():
            self._set_work(self._work_for(self.itemsBoundingRect()))
            return
        content = QtCore.QRectF()
        for it in items:
            r = it.sceneBoundingRect()
            kids = it.childrenBoundingRect()
            if not kids.isNull():
                r = r.united(it.mapRectToScene(kids))
            content = r if content.isNull() else content.united(r)
        if content.isNull():
            return
        need = self._work_for(content)
        if not self._work.contains(need):
            self._set_work(self._work.united(need))

    # -- the editor look ------------------------------------------------------
    def set_surround(self, mode: str) -> None:
        """View > Outside the Page: 'desk' / 'shaded' / 'page' / 'workspace'
        (SURROUND_MODES)."""
        mode = mode if mode in SURROUND_MODES else "desk"
        if mode != self._surround:
            self._surround = mode
            self.update()

    def surround(self) -> str:
        return self._surround

    def set_look(self, **look) -> None:
        """The editor look (:data:`EDITOR_LOOK`): grid_major, grid_style, grid_outside,
        pixel_grid, page_edge, page_shadow. An unknown key or value is ignored."""
        changed = False
        for key, value in look.items():
            if key not in EDITOR_LOOK or value is None:
                continue
            if key == "grid_major":
                try:
                    value = max(0, min(GRID_MAJOR_MAX, int(value)))
                except TypeError, ValueError:
                    continue
            elif key == "grid_style":
                if value not in GRID_STYLES:
                    continue
            elif key == "page_edge":
                if value not in PAGE_EDGES:
                    continue
            else:
                value = bool(value)
            if self._look.get(key) != value:
                self._look[key] = value
                changed = True
        if changed:
            self.update()

    def look(self, key: str | None = None):
        """The editor look (a copy), or one value of it."""
        return dict(self._look) if key is None else self._look.get(key, EDITOR_LOOK.get(key))

    def grid_visible_at(self, zoom: float) -> bool:
        """Whether the grid (when it is on) draws anything at ``zoom``: minor lines at least
        GRID_MIN_PX apart on screen, or major lines (the NavBar's grid button says 'too fine
        to show' when not)."""
        gx, gy = self._grid_size, self._grid_y or self._grid_size
        if not gx or not gy:
            return False
        z = abs(float(zoom or 0.0))
        if min(gx, gy) * z > GRID_MIN_PX:
            return True
        major = int(self._look.get("grid_major") or 0)
        return bool(major) and min(gx, gy) * major * z >= GRID_MIN_PX

    def _shadow_on(self) -> bool:
        return bool(self._look.get("page_shadow")) and not self._element_mode

    def _grid_on_page_only(self) -> bool:
        return self._surround == "desk" or not self._look.get("grid_outside")

    def surround_colour(self) -> QtGui.QColor:
        """The colour the canvas paints outside the page. In Windows high
        contrast a 'shaded' surround that hardly differs from the page (ratio
        < 1.2) falls back to the workspace colour, so the page edge stays
        clear."""
        desk = QtGui.QColor(self._MARGIN_COLOUR)
        page = QtGui.QColor(self._page_bg)
        if self._surround in ("workspace", "desk"):
            return desk
        if self._surround == "page":
            return page
        shaded = QtGui.QColor(
            _round(page.red() + (desk.red() - page.red()) * _SHADE),
            _round(page.green() + (desk.green() - page.green()) * _SHADE),
            _round(page.blue() + (desk.blue() - page.blue()) * _SHADE),
        )
        if _high_contrast() and _contrast_ratio(page, shaded) < 1.2:
            return desk
        return shaded

    @contextlib.contextmanager
    def editor_chrome(self):
        """Paint the canvas's EDITOR background while inside (the canvas's
        drawBackground and the magnifier use it): surround, page, page edge
        and the grid over the whole view. Outside it, a render paints the
        classic look (and export_only its export look)."""
        self._chrome += 1
        try:
            yield
        finally:
            self._chrome -= 1

    def background_signature(self) -> tuple:
        """Everything the editor background depends on: a canvas that caches
        its background (QGraphicsView.CacheBackground) repaints it when this
        changes."""
        return (
            self._page_w,
            self._page_h,
            self._page_bg.rgba(),
            self._grid_size,
            self._grid_y,
            self._grid_origin,
            self._element_mode,
            self._surround,
            self.surround_colour().rgba(),
            QtGui.QColor(self._MARGIN_COLOUR).rgba(),
            self._export,
            self._runtime_look,
            tuple(sorted(self._look.items())),
        )

    def _page_device_rect(self, tr: QtGui.QTransform):
        """The page in DEVICE pixels under ``tr`` (whole pixels: the canvas
        fills it with the page colour exactly, cached or not), or None."""
        if self._page_w <= 0 or self._page_h <= 0:
            return None
        a = tr.map(QtCore.QPointF(0, 0))
        b = tr.map(QtCore.QPointF(self._page_w, self._page_h))
        left, top = _round(min(a.x(), b.x())), _round(min(a.y(), b.y()))
        right, bottom = _round(max(a.x(), b.x())), _round(max(a.y(), b.y()))
        return QtCore.QRect(left, top, max(1, right - left), max(1, bottom - top))

    @staticmethod
    def _grid_colours(bg: QtGui.QColor) -> tuple:
        """(major, minor) grid colours on ``bg``: black at alpha 46 / 20 on a light
        ground, white at 46 / 20 on a dark one."""
        if _luminance(bg) > 128:
            return QtGui.QColor(0, 0, 0, 46), QtGui.QColor(0, 0, 0, 20)
        return QtGui.QColor(255, 255, 255, 46), QtGui.QColor(255, 255, 255, 20)

    def _draw_editor_background(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        """Draw the canvas background in whole device pixels."""
        tr = painter.transform()
        dev = tr.mapRect(rect).toAlignedRect().adjusted(-1, -1, 1, 1)
        page = self._page_device_rect(tr)
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        if self._element_mode:
            o = tr.map(QtCore.QPointF(0, 0))
            painter.setBrushOrigin(QtCore.QPoint(_round(o.x()), _round(o.y())))
            painter.fillRect(dev, QtGui.QBrush(_element_hatch()))
        else:
            surround = self.surround_colour()
            painter.fillRect(dev, surround)
            if page is not None:
                if self._shadow_on():
                    self._draw_page_shadow(painter, page)
                painter.fillRect(page, self._page_bg)
        painter.restore()
        if self._page_w <= 0 or self._page_h <= 0:
            return
        if (
            page is not None
            and not self._element_mode
            and not self._runtime_look
            and self._look.get("pixel_grid")
            and min(abs(tr.m11()), abs(tr.m22())) >= PIXEL_GRID_ZOOM
        ):
            self._draw_pixel_grid(painter, rect, page)
        self._draw_grid_everywhere(painter, rect, dev, page)
        if not self._element_mode and page is not None:
            self._draw_page_edge(painter, page)

    @staticmethod
    def _draw_page_shadow(painter: QtGui.QPainter, page: QtCore.QRect) -> None:
        """A soft shadow down and to the right of the page (device pixels, the painter
        reset): stacked translucent black rects, the page fills over them."""
        for alpha, off, spread in _SHADOW_LAYERS:
            r = page.translated(off, off).adjusted(-spread, -spread, spread, spread)
            painter.fillRect(r, QtGui.QColor(0, 0, 0, alpha))

    def _draw_pixel_grid(self, painter: QtGui.QPainter, rect: QtCore.QRectF, page: QtCore.QRect) -> None:
        """A faint line at every page pixel boundary, on the page only (alpha 22 on a light
        page, 18 on a dark one), under the grid."""
        tr = painter.transform()
        colour = (
            QtGui.QColor(0, 0, 0, 22) if _luminance(self._page_bg) > 128 else QtGui.QColor(255, 255, 255, 18)
        )
        x0 = max(1, int(math.floor(rect.left())))
        x1 = min(self._page_w - 1, int(math.ceil(rect.right())))
        y0 = max(1, int(math.floor(rect.top())))
        y1 = min(self._page_h - 1, int(math.ceil(rect.bottom())))
        if x1 < x0 and y1 < y0:
            return
        dpr = self._device_ratio(painter)
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        painter.setClipRect(page, QtCore.Qt.IntersectClip)
        top, h = page.top(), page.height()
        lft, w = page.left(), page.width()
        for x in range(x0, x1 + 1):
            d = _round(tr.map(QtCore.QPointF(x, 0)).x())
            self._hairline(painter, dpr, d, top, h, colour, vertical=True)
        for y in range(y0, y1 + 1):
            d = _round(tr.map(QtCore.QPointF(0, y)).y())
            self._hairline(painter, dpr, d, lft, w, colour, vertical=False)
        painter.restore()

    @staticmethod
    def _device_ratio(painter: QtGui.QPainter) -> float:
        """The device pixels per logical pixel of what ``painter`` paints on (1.5 at
        150 % Windows scaling)."""
        try:
            return float(painter.device().devicePixelRatioF()) or 1.0
        except AttributeError, RuntimeError:  # swallow-ok: a device without a ratio
            return 1.0

    @staticmethod
    def _device_px(v: int, dpr: float) -> int:
        """The device pixel a cosmetic point at logical ``v`` lands on (a half rounds
        down)."""
        return int(math.ceil(v * dpr - 0.5))

    @classmethod
    def _hairline(
        cls,
        painter: QtGui.QPainter,
        dpr: float,
        at: int,
        start: int,
        span: int,
        colour: QtGui.QColor,
        vertical: bool,
    ) -> None:
        """A line ONE device pixel wide (as a cosmetic pen draws it, at any device pixel
        ratio) at logical ``at``, over ``span`` logical pixels from ``start`` (the
        painter reset, antialiasing off)."""
        if dpr == 1.0:
            r = QtCore.QRect(at, start, 1, span) if vertical else QtCore.QRect(start, at, span, 1)
            painter.fillRect(r, colour)
            return
        d = cls._device_px(at, dpr) / dpr
        rf = (
            QtCore.QRectF(d, start, 1.0 / dpr, span) if vertical else QtCore.QRectF(start, d, span, 1.0 / dpr)
        )
        painter.fillRect(rf, colour)

    def _draw_page_edge(self, painter: QtGui.QPainter, page: QtCore.QRect) -> None:
        """Draw the page edge."""
        mode = self._look.get("page_edge", "dotted")
        if mode == "none":
            return
        light = _luminance(self.surround_colour()) > 128
        left, top = page.left() - 1, page.top() - 1
        right, bottom = page.right() + 1, page.bottom() + 1
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        if mode == "line":
            brush = QtGui.QBrush(QtGui.QColor(0, 0, 0, 90) if light else QtGui.QColor(255, 255, 255, 70))
        else:
            dots = QtGui.QColor(0, 0, 0) if light else QtGui.QColor(255, 255, 255)
            painter.setBrushOrigin(QtCore.QPoint(left, top))
            brush = QtGui.QBrush(dots, QtCore.Qt.Dense4Pattern)
        span = right - left + 1
        for strip in (
            QtCore.QRect(left, top, span, 1),
            QtCore.QRect(left, bottom, span, 1),
            QtCore.QRect(left, top + 1, 1, bottom - top - 1),
            QtCore.QRect(right, top + 1, 1, bottom - top - 1),
        ):
            if strip.width() > 0 and strip.height() > 0:
                painter.fillRect(strip, brush)
        painter.restore()

    @staticmethod
    def _grid_fade(spacing_px: float) -> float:
        """The share of the minor lines' alpha at ``spacing_px`` screen pixels apart: 0 up to
        GRID_MIN_PX, rising to 1 over GRID_FADE_PX."""
        return max(0.0, min(1.0, (spacing_px - GRID_MIN_PX) / GRID_FADE_PX))

    def _draw_grid_everywhere(
        self, painter: QtGui.QPainter, rect: QtCore.QRectF, dev: QtCore.QRect, page
    ) -> None:
        """Draw the grid over the exposed rect."""
        tr = painter.transform()
        sx = abs(tr.m11()) or 1.0
        sy = abs(tr.m22()) or 1.0
        gx, gy = self._grid_size, self._grid_y or self._grid_size
        if not gx or not gy:
            return
        major = int(self._look.get("grid_major") or 0)
        fade_x, fade_y = self._grid_fade(gx * sx), self._grid_fade(gy * sy)
        # (1 = every line is a major one)
        major_x = major >= 1 and gx * major * sx >= GRID_MIN_PX
        major_y = major >= 1 and gy * major * sy >= GRID_MIN_PX
        if not (fade_x or fade_y or major_x or major_y):
            return
        inner = self._grid_colours(self._page_bg)
        if self._element_mode or page is None:
            passes = [(None, inner)]
        elif self._grid_on_page_only():
            passes = [(QtGui.QRegion(page), inner)]
        else:
            outer = self._grid_colours(self.surround_colour())
            if outer == inner:
                passes = [(None, inner)]
            else:
                on_page = QtGui.QRegion(page)
                passes = [(on_page, inner), (QtGui.QRegion(dev).subtracted(on_page), outer)]
        dots = self._look.get("grid_style") == "dots"
        ox, oy = self._grid_origin
        # the grid positions in the exposed rect (and a little past it: its edge pixels are
        # drawn whatever strip of the view is repainted), as (scene coordinate, is major)
        xs = self._grid_positions(rect.left() - 2.0 / sx, rect.right() + 2.0 / sx, ox, gx, max(0, major))
        ys = self._grid_positions(rect.top() - 2.0 / sy, rect.bottom() + 2.0 / sy, oy, gy, max(0, major))
        for clip, (c_major, c_minor) in passes:
            painter.save()
            painter.resetTransform()
            painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
            if clip is not None:
                painter.setClipRegion(clip, QtCore.Qt.IntersectClip)
            if dots:
                self._grid_dots(painter, tr, xs, ys, c_major, c_minor, fade_x, fade_y, major_x and major_y)
            else:
                self._grid_lines(painter, tr, dev, xs, ys, c_major, c_minor, fade_x, fade_y, major_x, major_y)
            painter.restore()

    @staticmethod
    def _grid_positions(lo: float, hi: float, origin: int, pitch: int, major: int) -> list:
        k = int(math.ceil((lo - origin) / pitch))
        out = []
        v = origin + k * pitch
        while v <= hi:
            out.append((v, bool(major) and k % major == 0))
            k += 1
            v += pitch
        return out

    @staticmethod
    def _faded(colour: QtGui.QColor, f: float) -> QtGui.QColor:
        c = QtGui.QColor(colour)
        c.setAlpha(int(round(colour.alpha() * f)))
        return c

    def _grid_lines(
        self, painter, tr, dev, xs, ys, c_major, c_minor, fade_x, fade_y, major_x, major_y
    ) -> None:
        """Grid lines one DEVICE pixel wide at any device pixel ratio (crisp at any zoom;
        the painter reset)."""
        minor_x = self._faded(c_minor, fade_x) if fade_x else None
        minor_y = self._faded(c_minor, fade_y) if fade_y else None
        dpr = self._device_ratio(painter)
        top, h = dev.top(), dev.height()
        left, w = dev.left(), dev.width()
        for v, is_major in xs:
            col = c_major if (is_major and major_x) else minor_x
            if col is None or col.alpha() <= 0:
                continue
            d = _round(tr.map(QtCore.QPointF(v, 0)).x())
            self._hairline(painter, dpr, d, top, h, col, vertical=True)
        for v, is_major in ys:
            col = c_major if (is_major and major_y) else minor_y
            if col is None or col.alpha() <= 0:
                continue
            d = _round(tr.map(QtCore.QPointF(0, v)).y())
            self._hairline(painter, dpr, d, left, w, col, vertical=False)

    def _grid_dots(self, painter, tr, xs, ys, c_major, c_minor, fade_x, fade_y, majors) -> None:
        """One device pixel at each crossing: the minor ones while they are far enough
        apart (both directions), the major ones in the major colour."""
        minor = self._faded(c_minor, min(fade_x, fade_y)) if (fade_x and fade_y) else None
        if minor is not None and minor.alpha() <= 0:
            minor = None
        if minor is None and not majors:
            return
        dx = [(_round(tr.map(QtCore.QPointF(v, 0)).x()), m) for v, m in xs]
        dy = [(_round(tr.map(QtCore.QPointF(0, v)).y()), m) for v, m in ys]
        if not dx or not dy:
            return
        # one row of dots made once as a 1-pixel high picture, drawn at every grid row
        # (cheaper than a point list per row); minor rows first, the major crossings over them
        dpr = self._device_ratio(painter)

        def dev(v: int) -> int:
            return self._device_px(v, dpr)

        for colour, cols, rows in (
            (minor, [x for x, _m in dx], [y for y, _m in dy]),
            (c_major if majors else None, [x for x, m in dx if m], [y for y, m in dy if m]),
        ):
            if colour is None or not cols or not rows:
                continue
            img, left = self._dot_row([dev(x) for x in cols], colour)
            if dpr != 1.0:
                img.setDevicePixelRatio(dpr)
            for y in rows:
                painter.drawImage(QtCore.QPointF(left / dpr, dev(y) / dpr), img)

    @staticmethod
    def _dot_row(xs: list, colour: QtGui.QColor):
        """(a picture one pixel high with a ``colour`` dot at each pixel x of ``xs``, its
        left x)."""
        left = min(xs)
        img = QtGui.QImage(max(xs) - left + 1, 1, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(0)
        for x in xs:
            img.setPixelColor(x - left, 0, colour)
        return img, left

    def drawBackground(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        """Paint the margin, page background and grid here rather than as scene items."""
        export = getattr(self, "_export", None)
        if export == "selection":
            return  # transparent: only the objects
        if export is None and self._chrome:
            self._draw_editor_background(painter, rect)
            return
        if self._element_mode and export is None:
            # a library element has no page: CtDraw's element window is its
            # 8x8 #C0C0C0 diamond lattice on white everywhere
            painter.save()
            dev = painter.transform().mapRect(rect)
            painter.resetTransform()
            painter.fillRect(dev, QtGui.QBrush(_element_hatch()))
            painter.restore()
            if self._page_w <= 0 or self._page_h <= 0:
                return
        else:
            if export != "page":
                painter.fillRect(rect, self._MARGIN_COLOUR)
            if self._page_w <= 0 or self._page_h <= 0:
                return
            page_rect = QtCore.QRectF(0, 0, self._page_w, self._page_h)
            painter.fillRect(page_rect, self._page_bg)
            if export == "page":
                return  # no editor border / grid
            if not self._runtime_look:
                # (the runtime's window has no page border: the Simulate tab draws none)
                painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 120)))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawRect(page_rect)
        # Draw the grid only when its lines are at least ~4px apart on screen (pitch x zoom), not
        # by raw pitch. The painter's horizontal scale is the effective zoom (the view, the
        # magnifier render and print).
        _scale = abs(painter.transform().m11()) or 1.0
        _yscale = abs(painter.transform().m22()) or 1.0
        gx, gy = self._grid_size, self._grid_y or self._grid_size
        ox, oy = self._grid_origin
        # each direction is drawn only when its lines are >= 4 px apart on screen
        draw_v = bool(gx) and gx * _scale >= 4.0
        draw_h = bool(gy) and gy * _yscale >= 4.0
        if draw_v or draw_h:
            lum = 0.299 * self._page_bg.red() + 0.587 * self._page_bg.green() + 0.114 * self._page_bg.blue()
            gcol = QtGui.QColor(0, 0, 0, 26) if lum > 128 else QtGui.QColor(255, 255, 255, 18)
            # antialiasing off: a 1px line at a fractional device position would smear across
            # two pixels. A cosmetic (width-0) pen stays 1px at any zoom.
            had_aa = painter.testRenderHint(QtGui.QPainter.Antialiasing)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
            pen = QtGui.QPen(gcol)
            pen.setCosmetic(True)
            painter.setPen(pen)
            # only the grid lines within the exposed rect (cheap on big pages),
            # counted from the grid origin; an element window has no page edge
            # -- its grid covers the whole view -- a page's grid skips the edges
            if self._element_mode:
                x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
                top, bot, lft, rgt = rect.top(), rect.bottom(), rect.left(), rect.right()
            else:
                x0, y0 = max(1.0, rect.left()), max(1.0, rect.top())
                x1, y1 = min(self._page_w - 1, rect.right()), min(self._page_h - 1, rect.bottom())
                top, bot, lft, rgt = 0, self._page_h, 0, self._page_w
            if draw_v:
                x = ox + math.ceil((x0 - ox) / gx) * gx
                while x <= x1:
                    painter.drawLine(QtCore.QLineF(x, top, x, bot))
                    x += gx
            if draw_h:
                y = oy + math.ceil((y0 - oy) / gy) * gy
                while y <= y1:
                    painter.drawLine(QtCore.QLineF(lft, y, rgt, y))
                    y += gy
            painter.setRenderHint(QtGui.QPainter.Antialiasing, had_aa)

    def set_element_mode(self, on: bool, hotspot=None) -> None:
        """Show a library element the way CtDraw's element window does."""
        on = bool(on)
        hotspot = tuple(hotspot) if (on and hotspot) else None
        if on == self._element_mode and hotspot == self._element_hotspot:
            return
        changed = on != self._element_mode
        self._element_mode = on
        self._element_hotspot = hotspot
        self._collect_pane_labels(self._page)
        if changed:
            # an element window's work area is its canvas + content
            self._update_work_area()
        self.update()

    # -- Situational Awareness pane labels (editor chrome) ---------------------
    def set_pane_labels_visible(self, on: bool) -> None:
        """View > Pane Labels: name the SA workspace panes on the canvas."""
        self._pane_labels_on = bool(on)
        self.update()

    def pane_labels(self) -> list:
        """The labels the canvas draws: [(pane record, root, (l, t, r, b),
        title, detail, problem)] (tests / the magnifier)."""
        return list(self._pane_labels) if self._pane_labels_on else []

    def _collect_pane_labels(self, page: Page) -> None:
        """Every SA pane of ``page`` (sa_workspace.model_panes: the objects the
        runtime reads as panes) with its Name, fill mode, default page and a
        problem the page alone shows. Nothing on a library element window."""
        before, self._pane_labels = self._pane_labels, []
        try:
            self._pane_labels_from(page)
        finally:
            # the panes' records and the units that hold them: a move of anything else
            # leaves the labels as they are (shift_records)
            recs = set()
            for lab in self._pane_labels:
                recs.add(lab[0])
                recs.add(lab[1])
                with contextlib.suppress(Exception):
                    recs.update(page.ancestors(lab[0]))
            self.__dict__["_pane_records"] = frozenset(recs)
            # a label can change on a pane whose items were not rebuilt (the
            # 'name used twice' mark moves when ANOTHER pane is renamed)
            if self._pane_labels != before:
                self.update()

    def _pane_labels_from(self, page: Page) -> None:
        if page is None or self._element_mode:
            return
        try:
            from ..features import sa_workspace as SA

            panes = SA.model_panes(page)
            if not panes:
                return
            problems = SA.local_problems(panes)
        except Exception:  # noqa: BLE001 -- an aid only
            _note_exc("scene.PageScene._pane_labels_from")
            return
        for p in panes:
            bits = [p.fill_mode_text()]
            if p.is_default:
                bits.append("default pane")
            if p.tab_pane:
                bits.append("tabs in %s" % p.tab_pane)
            detail = " · ".join(bits)
            page_line = p.default_page if p.default_page.lower() not in ("", "none") else ""
            self._pane_labels.append(
                (
                    p.index,
                    p.root,
                    p.rect,
                    p.name or "(no name)",
                    detail + ("\n→ " + page_line if page_line else ""),
                    problems.get(p.index, ""),
                )
            )

    def _draw_pane_labels(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        """Draw a label badge and dashed outline on each SA pane."""
        tr = painter.transform()
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        f = QtGui.QFont(painter.font())
        f.setPixelSize(11)
        fb = QtGui.QFont(f)
        fb.setBold(True)
        fm, fmb = QtGui.QFontMetrics(f), QtGui.QFontMetrics(fb)
        for index, root, (l, t, r, b), title, detail, problem in self._pane_labels:
            item = self._items.get(index) or self._items.get(root)
            if item is not None and not item.isVisible():
                continue
            off = item.pos() if item is not None else QtCore.QPointF()
            sr = QtCore.QRectF(l, t, max(r - l, 1), max(b - t, 1)).translated(off)
            if not sr.intersects(rect):
                continue
            dev = tr.mapRect(sr).toAlignedRect()
            if dev.width() < 24 or dev.height() < 12:
                continue
            colour = QtGui.QColor(214, 128, 0) if problem else QtGui.QColor(37, 99, 235)
            pen = QtGui.QPen(colour)
            pen.setStyle(QtCore.Qt.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRect(dev.adjusted(1, 1, -2, -2))
            lines = [(fb, fmb, title + ("  ⚠ " + problem if problem else ""))]
            for ln in detail.split("\n"):
                if ln:
                    lines.append((f, fm, ln))
            pad = 3
            height = pad
            shown = []
            for font, metrics, text in lines:
                if height + metrics.height() + pad > dev.height() - 4 and shown:
                    break
                shown.append((font, metrics, text))
                height += metrics.height()
            height += pad
            width = max(m.horizontalAdvance(txt) for _f, m, txt in shown) + 2 * pad
            box = QtCore.QRect(
                dev.left() + 2, dev.top() + 2, min(width, dev.width() - 4), min(height, dev.height() - 4)
            )
            painter.save()
            # (within the painted region: a partial repaint never paints more)
            painter.setClipRect(dev.adjusted(1, 1, -1, -1), QtCore.Qt.IntersectClip)
            fill = QtGui.QColor(colour)
            fill.setAlpha(215)
            painter.fillRect(box, fill)
            painter.setPen(QtGui.QColor(255, 255, 255))
            y = box.top() + pad
            for font, metrics, text in shown:
                painter.setFont(font)
                painter.drawText(
                    QtCore.QRect(box.left() + pad, y, box.width() - 2 * pad, metrics.height()),
                    int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
                    metrics.elidedText(text, QtCore.Qt.ElideRight, max(box.width() - 2 * pad, 8)),
                )
                y += metrics.height()
            painter.restore()
        painter.restore()

    def drawForeground(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        super().drawForeground(painter, rect)
        # editor chrome only: never in a print, thumbnail, bitmap or sample
        if self._pane_labels and self._pane_labels_on and not getattr(self, "_export", None):
            self._draw_pane_labels(painter, rect)
        if self._element_hotspot is None or getattr(self, "_export", None):
            return
        # CtDraw's hotspot marker: a 7px circle with four 4px arms, black on a
        # 1px white halo, a constant size on screen
        pt = painter.transform().map(QtCore.QPointF(*self._element_hotspot))
        cx, cy = round(pt.x()), round(pt.y())
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        painter.setBrush(QtCore.Qt.NoBrush)
        for colour, width in ((QtGui.QColor(255, 255, 255), 3), (QtGui.QColor(0, 0, 0), 1)):
            pen = QtGui.QPen(colour)
            pen.setWidth(width)
            painter.setPen(pen)
            painter.drawEllipse(QtCore.QRect(cx - 3, cy - 3, 6, 6))
            painter.drawLine(cx - 7, cy, cx - 4, cy)
            painter.drawLine(cx + 4, cy, cx + 7, cy)
            painter.drawLine(cx, cy - 7, cx, cy - 4)
            painter.drawLine(cx, cy + 4, cx, cy + 7)
        painter.restore()

    def clear_page(self) -> None:
        """Drop the page and every item: a scene parked or about to be deleted keeps nothing
        of its page (it would hold the page until its deleteLater runs)."""
        if self._sim is not None:
            with contextlib.suppress(Exception):
                self._sim.forget_items()
        self._page = None
        self.shown = None
        self._covered = set()
        with contextlib.suppress(RuntimeError):
            self.clear()
        self._items.clear()
        self._ai_items = {}
        self._ai_static = {}
        self._backdrop = None

    def set_page(self, page: Page, thumbnail_opacity: float = 0.0, grid_size: int = 0) -> None:
        self._page = page
        # the (document, derive) a caller tracks what this scene shows by
        # (MainWindow._refresh); any other page shown here invalidates it
        self.shown = None
        if self._sim is not None:
            self._sim.forget_items()  # clear() deletes every item it drew on
        self._covered = set()
        self.clear()
        self._items.clear()
        self._ai_items = {}
        self._ai_static = {}
        self._backdrop = None
        self._hidden_applied = False  # every new item is visible
        self.build_serial += 1
        self._apply_page_state(page, grid_size)
        w, h = self._page_w, self._page_h
        self._thumb_opacity = thumbnail_opacity
        self._render_env = _render_env()
        # optional reference backdrop: Citect's own rendered thumbnail
        if thumbnail_opacity > 0 and page.thumbnail_bmp:
            img = QtGui.QImage.fromData(page.thumbnail_bmp, "BMP")
            if not img.isNull():
                pm = self.addPixmap(
                    QtGui.QPixmap.fromImage(
                        img.scaled(
                            int(w), int(h), QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation
                        )
                    )
                )
                pm.setOpacity(thumbnail_opacity)
                pm.setZValue(-1000)
                self._backdrop = pm  # a loose item: keep a Python reference
        # Every item stacks in RECORD order, the page template included: CtDraw
        # paints the object list first to last (a group paints its
        # children the same way), so a template stored AFTER
        # page objects covers them. Clicks do not depend on the stacking: a
        # locked template's records resolve to no unit, and the canvas skips
        # the template root's own page-size anchor (CanvasView._hits_under).
        self._build_pixmaps = {}
        try:
            for i, obj in enumerate(page.objects):
                self._build_object(page, i, obj)
        finally:
            self._build_pixmaps = None

        # CtDraw's ALARM INDICATOR (editor chrome): only GROUP records whose own
        # AI_ON atoi is non-zero (reader._alarm_indicators). It is
        # painted at the END of the group's paint, above its members and below
        # every later record, so stack it just before the first item drawn
        # after the group's subtree.
        self._ai_items = {}
        self._ai_static = {}
        drawn = sorted(self._items)
        for i, obj in enumerate(page.objects):
            self._add_alarm_indicator(page, i, obj, drawn)
        self._stamp_page_indices()
        self._collect_pane_labels(page)
        self._resolve_marker_colours()
        # the work area = the page, CtDraw's scroll surface and every drawn item
        self._update_work_area()
        if self._sim is not None:
            self._sim.rebind()  # the live states over the new items

    def _apply_page_state(self, page: Page, grid_size: int) -> None:
        """The page's size, background colour and the grid pitch (all painted by
        drawBackground; the size and colour are read by _brush too, so
        update_units declines when they change). The scene rect is the work
        area, set once the items are built (_update_work_area)."""
        w = page.appearance.width or 1024
        h = page.appearance.height or 768
        self._page_w, self._page_h = w, h
        self._page_bg = colorref_to_qcolor(page.appearance.background_colour, QtGui.QColor("#d4d0c8"))
        # keep the requested pitch down to 1 (pixel grid); drawBackground decides whether it's
        # dense enough on screen to actually draw. ``grid_size`` = one pitch, or
        # (pitch x, pitch y, origin x, origin y) from Grid Setup.
        if isinstance(grid_size, (tuple, list)):
            gx, gy, ox, oy = (list(grid_size) + [0, 0, 0, 0])[:4]
        else:
            gx = gy = grid_size
            ox = oy = 0
        gx = int(gx) if (gx and gx >= 1) else 0
        gy = int(gy) if (gy and gy >= 1) else 0
        self._grid_size, self._grid_y = (gx, gy) if gx and gy else (0, 0)
        self._grid_origin = (int(ox or 0), int(oy or 0))
        self.update()

    def _build_object(self, page: Page, i: int, obj: GraphicObject) -> None:
        """The item of page object ``i`` (added to the scene and ``_items``),
        or nothing when it draws nothing on its own."""
        # a library element that is not found: CtDraw's X box hides the record's content
        # (reader._mark_missing_elements)
        if obj.raw.get("_under_missing"):
            return
        if obj.raw.get("_missing_element"):
            item = self._missing_element_item(obj)
            item.setData(0, i)
            self.addItem(item)
            self._items[i] = item
            return
        # non-drawable behaviour/anchor objects (AnPoint, Keyboard, Touch,
        # editor-tool sentinels): no visual representation.
        if obj.raw.get("_non_drawable"):
            icon = self._class_icon_item(page, obj)
            if icon is None and obj.raw.get("_type_code") == 0x1000 and not obj.raw.get("_in_composite"):
                icon = self._anpoint_marker(obj)
            if icon is not None:
                icon.setData(0, i)
                self.addItem(icon)
                self._items[i] = icon
            return
        # composite-genie members draw via the genie's resolved cache art
        # (below), not individually.
        if obj.raw.get("_in_composite"):
            return
        # tc=0x0C multi-state container: only the active state draws (the
        # slots share one rect).
        if obj.raw.get("_state_hidden"):
            return
        # NO hidden-when gate on the page stream: CtDraw's editor has no
        # Cicode interpreter (VISIBLE is only classified as a dynamic key,
        # never evaluated at draw) and draws every page object, inline
        # genie members included, whatever its hidden-when says. Only a
        # COMPOSITION CACHE folds constant gates (see _add_symbol_child).
        # runtime-exclusive state-stack duplicate: identical art to a
        # sibling drawn on top of it. Not in the Simulate tab's look: there
        # the simulator decides which copy shows, and the copy dropped here
        # could be the one it shows.
        if obj.raw.get("_dup_suppressed") and not self._runtime_look:
            return
        # Composite genie with resolved cache art draws it scaled into the
        # placement box (inline members suppressed above).
        if obj.members and obj.children:
            item = self._make_symbol_item(obj)
            if item is not None:
                item.setData(0, i)
                self.addItem(item)
                self._items[i] = item
                self._add_badges(obj, item)
            return
        # Container record (group/genie with INLINED members) draws nothing
        # itself; its member leaves draw their own art. Emit an invisible
        # anchor so selection/badges have a host keyed to the container
        # index. EXCEPTION: a drawable LEAF that owns behaviour children (a
        # button-face rect with an attached Touch, a text whose child is its
        # input record, a bitmap with its picture and a Touch) paints its
        # own art too.
        if obj.members and not _paints_with_members(obj):
            item = QtWidgets.QGraphicsRectItem(
                obj.geometry.x, obj.geometry.y, max(obj.geometry.extent_x, 1), max(obj.geometry.extent_y, 1)
            )
            item.setPen(_ANCHOR_PEN)
            item.setBrush(_ANCHOR_BRUSH)
            item.setData(0, i)
            self.addItem(item)
            self._items[i] = item
            return
        # Placed symbol/genie resolved to real library content draws its
        # child element objects instead of a placeholder box.
        if obj.children:
            item = self._make_symbol_item(obj)
            if item is not None:
                item.setData(0, i)
                self.addItem(item)
                self._items[i] = item
                self._add_badges(obj, item)
            return
        # an ART-LESS symbol slot of a placed genie / linked symbol / template
        # (a tc 0x07 member whose art neither its own record nor its element
        # twin gave us, e.g. instance-only symbol-set states a placed genie
        # gained after its element was stored) draws nothing: CtDraw shows no
        # placeholder there. A TOP-LEVEL symbol whose library is missing keeps
        # its placeholder.
        if (
            obj.raw.get("_type_code") == 0x07
            and not obj.children
            and obj.raw.get("_embedded_bmp") is None
            and _in_placed_element(page, i)
        ):
            return
        item = self._make_item(obj)
        if item is not None:
            # key selection by object INDEX (z-order), the stable id shared
            # with the editable document; AN is often -1/non-unique.
            item.setData(0, i)
            self.addItem(item)
            self._items[i] = item
            self._add_badges(obj, item)

    def _add_alarm_indicator(self, page: Page, i: int, obj: GraphicObject, drawn) -> None:
        """The alarm-indicator item of group ``i`` (``drawn`` = the sorted
        indices that have an item), stacked just before the first item drawn
        after the group's subtree."""
        ai = obj.raw.get("_alarm_indicator")
        if not ai:
            return
        item = self._alarm_indicator_item(ai)
        if item is None:
            return
        # the reader computed the frame for the group's parse-time rect1; a move
        # (EditableDocument.move_objects) shifts the model in place without a
        # re-parse, so place the indicator by the group's CURRENT origin
        ox, oy = ai.get("origin", (obj.geometry.x, obj.geometry.y))
        item.setPos(obj.geometry.x - ox, obj.geometry.y - oy)
        # a click on the frame picks the group (CtDraw's bounds include
        # it); composite members have no item of their own -> nearest ancestor
        host = i
        while host is not None and host not in self._items:
            host = page.objects[host].parent
        item.setData(0, host)
        self.addItem(item)
        # (each member's drawn bounds as built: the simulator sizes an active indicator to
        # the members' current ones)
        self._ai_static[i] = {
            j: self._items[j].sceneBoundingRect() for j, _r in ai.get("members") or () if j in self._items
        }
        k = bisect.bisect_right(drawn, ai["last"])
        if k < len(drawn):
            nxt = self._items[drawn[k]]
            if nxt.zValue() == item.zValue():
                item.stackBefore(nxt)
        self._ai_items[i] = item

    def update_units(
        self, page: Page, roots, thumbnail_opacity: float = 0.0, grid_size: int = 0, kept=None
    ) -> bool:
        """Rebuild only the items of the root units ``roots``."""
        old = self._page
        n = len(page.objects)
        if (
            old is None
            or thumbnail_opacity != getattr(self, "_thumb_opacity", 0.0)
            or (thumbnail_opacity > 0 and old.thumbnail_bmp != page.thumbnail_bmp)
            or getattr(self, "_render_env", None) != _render_env()
        ):
            return False
        # the item builders read the page size and background (an embossed text's
        # body is the page colour): a page-level change (Page Properties,
        # set_page_background) builds every item again
        if (page.appearance.width or 1024, page.appearance.height or 768) != (
            self._page_w,
            self._page_h,
        ) or colorref_to_qcolor(page.appearance.background_colour, QtGui.QColor("#d4d0c8")) != self._page_bg:
            return False
        spans = []
        for r in sorted({page.root_of(r) for r in roots if 0 <= r < n}):
            spans.append((r, max(page.subtree(r)) + 1))
        moves = self._kept_units(page, spans, kept)
        # restacking costs a pass over the scene per item: past half the page a
        # full rebuild is cheaper
        limit = max(200, n * self.patch_limit)
        if sum(e - s for s, e in spans if s not in moves) > limit:
            return False
        # (a change of most of the page whose units mostly stay: the scene ends as set_page
        # leaves it, below)
        whole = sum(e - s for s, e in spans) > limit
        self._page = page
        self.build_serial += 1
        self._apply_page_state(page, grid_size)
        if self._sim is not None:
            # the live states' own items of the rebuilt / dropped records go with them
            gone = {i for s, e in spans for i in range(s, e)}
            gone.update(i for store in (self._items, self._ai_items) for i in store if i >= n)
            self._sim.forget_items(gone)
        # (the items of the kept units, by their old index, until they take the new one)
        released: dict = {}
        stale = [i for store in (self._items, self._ai_items) for i in store if i >= n]
        for i in stale:
            for store in (self._items, self._ai_items):
                it = store.pop(i, None)
                if it is not None:
                    if moves and store is self._items:
                        released[i] = it
                    else:
                        self.removeItem(it)
        # the pane labels read the whole page (a renamed / moved / new pane):
        # one pass over the objects, cheaper than tracking the units
        self._collect_pane_labels(page)
        if not spans:
            if self._sim is not None:
                self._sim.rebind()
            return True
        for s, e in spans:
            for i in range(s, e):
                for store in (self._items, self._ai_items):
                    it = store.pop(i, None)
                    if it is not None:
                        if moves and store is self._items:
                            released[i] = it
                        else:
                            self.removeItem(it)
        rekeyed = []
        # (in record order, kept and built alike: the items keep the order a rebuild gives
        # them, which a marquee walks)
        self._build_pixmaps = {}
        try:
            for s, e in spans:
                o = moves.get(s)
                if o is None:
                    for i in range(s, e):
                        self._build_object(page, i, page.objects[i])
                    continue
                for k in range(e - s):
                    it = released.pop(o + k, None)
                    if it is not None:
                        it.setData(0, s + k)
                        self._items[s + k] = it
                        rekeyed.append((s + k, it))
        finally:
            self._build_pixmaps = None
        for it in released.values():
            self.removeItem(it)
        drawn = sorted(self._items)
        new_items = []
        for s, e in reversed(spans):
            if s in moves:
                continue  # (kept in its place)
            # the stacking order = record order (every item z 0, the page
            # template included -- see set_page): each new item goes just before
            # the first item drawn after its unit with the same z (in record
            # order, so the unit keeps its own order; the LAST unit first, so a
            # later rebuilt unit is already in place)
            k = bisect.bisect_left(drawn, e)
            nxt_by_z = {}
            for i in range(s, e):
                it = self._items.get(i)
                if it is None:
                    continue
                z = it.zValue()
                if z not in nxt_by_z:
                    nxt_by_z[z] = next(
                        (self._items[j] for j in drawn[k:] if self._items[j].zValue() == z), None
                    )
                if nxt_by_z[z] is not None:
                    it.stackBefore(nxt_by_z[z])
                new_items.append((i, it))
        for s, e in spans:
            if s in moves:
                continue
            for i in range(s, e):
                self._add_alarm_indicator(page, i, page.objects[i], drawn)
                ai = self._ai_items.get(i)
                if ai is not None:
                    new_items.append((ai.data(0), ai))
        self._stamp_page_indices(new_items + rekeyed)
        if whole:
            # what set_page gives: the items by record order, every marker's ink sampled, the
            # work area measured again
            for store in (self._items, self._ai_items):
                ordered = sorted(store.items(), key=lambda kv: kv[0])
                store.clear()
                store.update(ordered)
            self._ai_static = {i: self._ai_static[i] for i in self._ai_items if i in self._ai_static}
            self._resolve_marker_colours()
        else:
            # (a kept marker's ink is sampled again too: what lies under it may have changed)
            self._resolve_marker_colours([it for _i, it in new_items + rekeyed])
        self._apply_hidden()
        if whole:
            self._update_work_area()
        else:
            # an edit that draws beyond the work area grows it (never shrinks it:
            # every scene-rect change re-indexes the scene; a kept item is inside it)
            self._update_work_area(grow_only=True, items=[it for _i, it in new_items])
        if self._sim is not None:
            self._sim.rebind()
        return True

    def _kept_units(self, page: Page, spans, kept) -> dict:
        """The units of ``spans`` whose items stay, under their new index: {root now: root
        then} of ``kept`` (update_units), without a unit an alarm indicator frames (its
        frame names its members' indices) and while the simulator's live states hold no
        item snapshots."""
        if not kept or self._sim is not None:
            return {}
        out = {}
        objs = page.objects
        for s, e in spans:
            o = kept.get(s)
            if o is None:
                continue
            if any(objs[i].raw.get("_alarm_indicator") or (o + i - s) in self._ai_items for i in range(s, e)):
                continue
            out[s] = o
        return out

    def shift_records(self, moves) -> bool:
        """Move the items of moved records by whole pixels, without a rebuild."""
        if self._sim is not None or self._page is None:
            return False
        moved = []
        for idx, dx, dy in moves:
            if not (dx or dy):
                continue
            for i in idx:
                for store in (self._items, self._ai_items):
                    it = store.get(i)
                    if it is not None:
                        it.moveBy(dx, dy)
                        moved.append(it)
        if not moved:
            return True
        self.build_serial += 1
        # (the labels change only when a pane, or what holds one, moved)
        panes = self.__dict__.get("_pane_records")
        if panes is None or any(i in panes for idx, dx, dy in moves if dx or dy for i in idx):
            self._collect_pane_labels(self._page)
        self._resolve_marker_colours([it for it in moved if isinstance(it, _AnPointMarker)])
        self._update_work_area(grow_only=True, items=moved)
        return True

    def _stamp_page_indices(self, roots=None) -> None:
        """Tag every item with its page index for hit-testing."""
        if roots is None:
            roots = list(self._items.items())
            roots += [(it.data(0), it) for it in self._ai_items.values()]
        for i, top in roots:
            if not isinstance(i, int):
                continue
            stack = [top]
            while stack:
                it = stack.pop()
                it.setData(PAGE_INDEX_ROLE, i)
                stack.extend(it.childItems())

    @contextlib.contextmanager
    def export_only(self, indices):
        """Render the page for output: no margin, border or grid."""
        sim = self._sim if (self._sim is not None and self._sim.active()) else None
        if sim is not None:
            sim.suspend()
        try:
            with self._export_static(indices):
                yield
        finally:
            if sim is not None:
                sim.resume()

    @contextlib.contextmanager
    def _export_static(self, indices):
        prev = (self._export, [(i, it.isVisible()) for i, it in self._items.items()])
        ai_prev = [(it, it.isVisible()) for it in self._ai_items.values()]
        for it in self._ai_items.values():  # editor chrome: CtDraw's prints / bitmaps
            it.setVisible(False)  # leave it out
        self._export = "selection" if indices is not None else "page"
        if indices is not None:
            keep = set(indices)
            for i, item in self._items.items():
                if i not in keep:
                    item.setVisible(False)
        else:
            for item in self._items.values():  # editor-hidden roots too
                item.setVisible(True)
        for item in self._markers():  # animation points: editor chrome (an
            item.setVisible(False)  # export draws nothing)
        try:
            yield
        finally:
            self._export = prev[0]
            for i, vis in prev[1]:
                it = self._items.get(i)
                if it is not None:
                    it.setVisible(vis)
            for it, vis in ai_prev:
                it.setVisible(vis)

    def sample_colour(self, x: float, y: float):
        """The colour drawn at page point (x, y), for the eye dropper."""
        if self._page_w <= 0 or self._page_h <= 0:
            return None
        if not (0 <= x < self._page_w and 0 <= y < self._page_h):
            return None
        px, py = math.floor(x), math.floor(y)
        img = QtGui.QImage(1, 1, QtGui.QImage.Format_ARGB32)
        img.fill(self._page_bg)
        prev = self._export
        ai_prev = [(it, it.isVisible()) for it in list(self._ai_items.values()) + self._markers()]
        for it, _vis in ai_prev:
            it.setVisible(False)
        self._export = "page"
        painter = QtGui.QPainter(img)
        try:
            self.render(
                painter, QtCore.QRectF(0, 0, 1, 1), QtCore.QRectF(px, py, 1, 1), QtCore.Qt.IgnoreAspectRatio
            )
        finally:
            painter.end()
            self._export = prev
            for it, vis in ai_prev:
                it.setVisible(vis)
        c = QtGui.QColor(img.pixel(0, 0))
        return (c.red() << 16) | (c.green() << 8) | c.blue()

    def set_hidden(self, indices) -> None:
        """Editor-only visibility for ``indices`` and everything under them."""
        self._hidden_roots = set(indices)
        self._apply_hidden(changed_only=True)

    def _apply_hidden(self, changed_only: bool = False) -> None:
        """Show / hide the items by the hidden set. ``changed_only``: the items are as the
        last pass left them (no build since), so only those whose state changes are set."""
        page = self._page
        hidden = getattr(self, "_hidden_roots", set())
        if page is None:
            return
        if not hidden and not self._hidden_applied:
            return  # nothing hidden before or now: every item is shown
        self._hidden_applied = bool(hidden)
        covered = set()
        n = len(page.objects)
        for r in hidden:
            if not 0 <= r < n:
                continue
            try:
                covered |= set(page.subtree(r))
            except Exception:  # noqa: BLE001
                _note_exc("scene.PageScene._apply_hidden")
                covered.add(r)
        before = self._covered
        self._covered = covered
        sim = self._sim
        if changed_only:
            items, ai_items = self._items, self._ai_items
            for i in covered ^ before:
                item = items.get(i)
                if item is not None:
                    item.setVisible(i not in covered and (sim is None or sim.shows(i)))
                item = ai_items.get(i)
                if item is not None:
                    item.setVisible(i not in covered and (sim is None or sim.shows(i, "ai")))
        else:
            for i, item in self._items.items():
                item.setVisible(i not in covered and (sim is None or sim.shows(i)))
            for i, item in self._ai_items.items():
                item.setVisible(i not in covered and (sim is None or sim.shows(i, "ai")))
        if sim is not None:
            sim.editor_hidden_changed()

    # -- the simulator's live object states (the Simulate tab) -------------------
    def sim_layer(self):
        """The layer that draws the simulator's object states over the static
        items (render/sim_states.SimLayer), made on first use."""
        if self._sim is None:
            from .sim_states import SimLayer

            self._sim = SimLayer(self)
        return self._sim

    def apply_states(self, states, replace: bool = False) -> dict:
        """Draw the simulator's object states over the static page."""
        return self.sim_layer().apply(states, replace=replace)

    def clear_states(self) -> None:
        """Every record back to its static look (and the flash phase back to on)."""
        if self._sim is not None:
            self._sim.clear()
            self._sim.set_flash(True)

    def set_flash_phase(self, on: bool) -> int:
        """The simulator's global flash phase (PageState.flash_on): flashing bitmaps show
        their second picture while it is off. Returns the flashing pictures on the page."""
        return self.sim_layer().set_flash(on)

    def sim_states(self) -> dict:
        """The object states drawn now, by AN."""
        return dict(self._sim.states) if self._sim is not None else {}

    def set_runtime_look(self, on: bool) -> None:
        """Hide the editor-only marks the runtime never draws (animation point
        crosses, Cicode object and keyboard class icons, pane labels): the
        Simulate tab's look."""
        self.set_pane_labels_visible(not on)
        self.sim_layer().set_runtime_look(on)
        if self._runtime_look != bool(on):
            self._runtime_look = bool(on)
            self.invalidate(self.sceneRect(), QtWidgets.QGraphicsScene.BackgroundLayer)

    def _alarm_indicator_item(self, ai):
        """CtDraw as one item: 4 FillRect strips (W+1 wide) along
        ``outer`` in the priority colour, then a 1px AI_COLOUR Rectangle on
        ``inner`` and on ``outer`` (NULL brush; GDI's l..r-1 outline), then the
        flag genie (or the 20x20 white X box when it does not resolve)."""
        l, t, r, b = ai["outer"]
        s = ai["strip"]
        if r <= l or b <= t:
            return None
        root = QtWidgets.QGraphicsRectItem(l, t, r - l, b - t)
        root.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        root.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        fill = QtGui.QColor(*ai["fill"])
        for x, y, w, h in ((l, t, s, b - t), (l, t, r - l, s), (r - s, t, s, b - t), (l, b - s, r - l, s)):
            if w <= 0 or h <= 0:
                continue
            strip = QtWidgets.QGraphicsRectItem(x, y, w, h, root)
            strip.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            strip.setBrush(QtGui.QBrush(fill))
            strip.setData(AI_PART_ROLE, "strip")
        pen = QtGui.QPen(QtGui.QColor(*ai["pen"]))
        pen.setWidth(1)
        pen.setCosmetic(False)
        pen.setJoinStyle(QtCore.Qt.MiterJoin)
        for rl, rt, rr, rb in (ai["inner"], ai["outer"]):
            x0, x1 = min(rl, rr), max(rl, rr)  # GDI normalises a flipped rect
            y0, y1 = min(rt, rb), max(rt, rb)
            line = QtWidgets.QGraphicsRectItem(x0, y0, max(x1 - x0 - 1, 0), max(y1 - y0 - 1, 0), root)
            line.setPen(pen)
            line.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
            _pixel_align(line)
        flag = ai.get("flag")
        if isinstance(flag, tuple) and flag and flag[0] == "default":
            _k, fx, fy = flag
            self.default_flag_item(fx, fy, root)
        elif flag is not None and getattr(flag, "children", None):
            f_item = self._make_symbol_item(flag)
            if f_item is not None:
                f_item.setParentItem(root)
                f_item.setData(AI_PART_ROLE, "flag")
        return root

    @staticmethod
    def default_flag_item(fx, fy, parent):
        """CtDraw's 20 x 20 placeholder flag (a white box with a black cross) at (fx, fy)."""
        box = QtWidgets.QGraphicsRectItem(fx, fy, 19, 19, parent)
        bp = QtGui.QPen(QtGui.QColor(0, 0, 0))
        bp.setWidth(1)
        box.setPen(bp)
        box.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255)))
        _pixel_align(box)
        box.setData(AI_PART_ROLE, "flag")
        for p0, p1 in (((fx, fy), (fx + 20, fy + 20)), ((fx, fy + 19), (fx + 19, fy))):
            ln = QtWidgets.QGraphicsLineItem(p0[0] + 0.5, p0[1] + 0.5, p1[0] + 0.5, p1[1] + 0.5, box)
            ln.setPen(bp)
        return box

    # -- per-object rendering ---------------------------------------------
    def _pen(self, obj: GraphicObject, default=None) -> QtGui.QPen:
        # CtDraw's default outline for a shape with no colour block is the
        # WINDOW-TEXT colour (black): a colourless frame rectangle draws a black
        # border. Dense thin TABLE RULES (SOE/alarm row grids) keep a faint grey,
        # so black is gated to genuine FRAME shapes (a closed shape whose smaller
        # side is a real box, not a 1-row rule).
        if default is None:
            g = obj.geometry
            ex, ey = g.extent_x or 0, g.extent_y or 0
            small, big = min(ex, ey), max(ex, ey)
            aspect = (big / small) if small else 999.0
            # a FRAME is a box (both sides >= 8) that is not a long thin RULE
            # (aspect < 6) -- the latter are SOE/alarm/Hardware table-row rules
            # CtDraw paints faintly; large boxes (>=120) stay frames regardless.
            is_frame = (
                (obj.kind in (ObjectKind.RECTANGLE, ObjectKind.ELLIPSE, ObjectKind.POLYGON))
                and small >= 8
                and (aspect < 6 or small >= 120)
            )
            default = "#000000" if is_frame else "#d4d4d4"
        col = colorref_to_qcolor(obj.style.line_colour, QtGui.QColor(default))
        pen = QtGui.QPen(col)
        pen.setWidth(max(1, obj.style.line_width or 1))
        pen.setCosmetic(False)
        _gdi_pen_style(pen, obj.style)
        return pen

    def _brush(self, obj: GraphicObject) -> QtGui.QBrush:
        fill = colorref_to_qcolor(obj.style.fill_colour, None)
        if not obj.style.filled or fill is None:
            return QtGui.QBrush(_QT_NO_BRUSH)
        # gradient fill (AttributeGradientMode: 1 L>R, 2 R>L, 3 T>B, 4 B>T,
        # 5/6 horizontal edge<->middle, 7/8 vertical edge<->middle)
        mode = obj.style.gradient_mode
        c2 = colorref_to_qcolor(obj.style.gradient_colour, None)
        if mode and c2 is not None:
            # (every gradient as CtDraw draws it)
            g = obj.geometry
            x, y, w, h = g.x, g.y, max(g.extent_x, 1), max(g.extent_y, 1)
            if mode in (1, 2):
                lg = QtGui.QLinearGradient(x, y, x + w, y)
                a, b = (fill, c2) if mode == 1 else (c2, fill)
                lg.setColorAt(0.0, a)
                lg.setColorAt(1.0, b)
            elif mode in (3, 4):
                lg = QtGui.QLinearGradient(x, y, x, y + h)
                a, b = (fill, c2) if mode == 3 else (c2, fill)
                lg.setColorAt(0.0, a)
                lg.setColorAt(1.0, b)
            elif mode in (5, 6):
                lg = QtGui.QLinearGradient(x, y, x, y + h)
                a, b = (fill, c2) if mode == 5 else (c2, fill)
                lg.setColorAt(0.0, a)
                lg.setColorAt(0.5, b)
                lg.setColorAt(1.0, a)
            else:  # 7, 8
                lg = QtGui.QLinearGradient(x, y, x + w, y)
                a, b = (fill, c2) if mode == 7 else (c2, fill)
                lg.setColorAt(0.0, a)
                lg.setColorAt(0.5, b)
                lg.setColorAt(1.0, a)
            return QtGui.QBrush(lg)
        return QtGui.QBrush(fill)

    # -- 3D effects + the rectangle's border (CtDraw's draw code, as format/ctf.py bakes it into
    #    the .ctF) ----------------------------------------------------------------------------
    def _sel_colour(self, obj, sel: int) -> QtGui.QColor | None:
        """The colour of a draw 'selection' (ctf._Painter): 0 the own line, 1 hilight,
        2 lolight, 3 shadow, 4 the page background; None = the no-colour sentinel (a NULL
        pen / brush). The editor paints each pair's FIRST half (reader._design_colour)."""
        st = obj.style
        if sel == 4:
            return QtGui.QColor(self._page_bg)
        c = {0: st.line_colour, 1: st.hilight_colour, 2: st.lolight_colour, 3: st.shadow_colour}[sel]
        return colorref_to_qcolor(c, None)

    @staticmethod
    def _clone_shape(item, parent):
        """A copy of a shape item's geometry as a child of ``parent``."""
        if isinstance(item, QtWidgets.QGraphicsRectItem):
            return QtWidgets.QGraphicsRectItem(item.rect(), parent)
        if isinstance(item, QtWidgets.QGraphicsEllipseItem):
            return QtWidgets.QGraphicsEllipseItem(item.rect(), parent)
        # (a copy hit-tests as its original does: an open line by its stroke, a hollow
        # polygon by its edge; a plain item would pick its whole closed area)
        if isinstance(item, HollowPolygonItem):
            return HollowPolygonItem(item.polygon(), parent)
        if isinstance(item, QtWidgets.QGraphicsPolygonItem):
            return QtWidgets.QGraphicsPolygonItem(item.polygon(), parent)
        if isinstance(item, StrokePathItem):
            return StrokePathItem(item.path(), parent)
        if isinstance(item, QtWidgets.QGraphicsPathItem):
            return QtWidgets.QGraphicsPathItem(item.path(), parent)
        return None

    def _effect_copy(self, item, obj, sel: int, dx: float, dy: float, behind: bool) -> None:
        """One offset copy of the shape drawn with selection ``sel``'s pen (and
        brush, when the shape is filled): CtDraw's body(sel) under
        SetWindowOrgEx(-dx, -dy)."""
        c = self._clone_shape(item, item)
        if c is None:
            return
        col = self._sel_colour(obj, sel)
        pen = QtGui.QPen(item.pen())
        if col is None:
            pen = QtGui.QPen(QtCore.Qt.NoPen)
        else:
            pen.setColor(col)
        c.setPen(pen)
        if item.brush().style() != QtCore.Qt.NoBrush and col is not None:
            c.setBrush(QtGui.QBrush(col))
        else:
            c.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        c.setPos(dx, dy)
        if behind:
            c.setFlag(QtWidgets.QGraphicsItem.ItemStacksBehindParent, True)
        c.setData(0, None)

    @staticmethod
    def _strip(parent, x: int, y: int, w: int, h: int, colour: QtGui.QColor) -> None:
        """A run of GDI pixels (MoveTo / LineTo excludes the end point) as a
        filled 1px strip -- pixel-exact at any zoom."""
        if w <= 0 or h <= 0:
            return
        s = QtWidgets.QGraphicsRectItem(x, y, w, h, parent)
        s.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        s.setBrush(QtGui.QBrush(colour))
        s.setData(0, None)

    def _effects(self, item, obj) -> None:
        """CtDraw's 3D effect on a styled shape."""
        from ..format import geometry as G

        st = obj.style
        fl = st.style_flags
        tc = obj.raw.get("_type_code", 0)
        eff = fl[0] & 0x0F
        fill_style = fl[2] & 0x0F
        width = fl[4] or 1
        attribs = fl[3]
        g = obj.geometry
        l, t = int(round(g.x)), int(round(g.y))
        r, b = l + int(round(g.extent_x)), t + int(round(g.extent_y))
        d = G.effect_depth(tc, (l, t, r, b), fl) if eff in (1, 2, 3, 4) else 0
        is_rect = tc in _RECT_TCS
        square_filled = is_rect and fill_style != 0 and not st.corner_radius
        if eff in (1, 2) and square_filled:
            self._bevels(item, obj, (l, t, r, b), eff, d, width, attribs)
        elif eff == 1:
            self._effect_copy(item, obj, 2, d, d, True)
            self._effect_copy(item, obj, 1, -d, -d, True)
        elif eff == 2:
            self._effect_copy(item, obj, 1, d, d, True)
            self._effect_copy(item, obj, 2, -d, -d, True)
        elif eff == 3:
            self._effect_copy(item, obj, 3, d, d, True)
        elif eff == 4:
            if fill_style == 0 and width == 1:
                # body(hilight) at 0, then lolight at (-d, -d) over it; no own colour
                col = self._sel_colour(obj, 1)
                pen = QtGui.QPen(item.pen())
                if col is None:
                    pen = QtGui.QPen(QtCore.Qt.NoPen)
                else:
                    pen.setColor(col)
                item.setPen(pen)
                self._effect_copy(item, obj, 2, -d, -d, False)
            else:
                self._effect_copy(item, obj, 1, d, d, True)
                self._effect_copy(item, obj, 2, -d, -d, True)
                bg = self._sel_colour(obj, 4)
                pen = QtGui.QPen(item.pen())
                pen.setColor(bg)
                item.setPen(pen)
                if item.brush().style() != QtCore.Qt.NoBrush:
                    item.setBrush(QtGui.QBrush(bg))
        if is_rect and attribs & 0x10 and not (eff == 4 or (eff in (1, 2) and not square_filled)):
            self._border(item, (l, t, r, b), eff, d, width)

    def _bevels(self, item, obj, rect, eff: int, d: int, width: int, attribs: int) -> None:
        """(bottom / right) + (top / left): d GDI lines per
        side outside the pen, lolight / hilight (Raised) or hilight / lolight
        (Lowered, drawn top / left first)."""
        l, t, r, b = rect
        h = width // 2
        L, T, R, B = l - h, t - h, r + 1 + h, b + 1 + h
        odd = (width + 1) & 1
        R, B = R - odd, B - odd
        L, T, R, B = L - d, T - d, R + d, B + d
        order = ((2, "br"), (1, "tl")) if eff == 1 else ((2, "tl"), (1, "br"))
        for sel, side in order:
            col = self._sel_colour(obj, sel)
            if col is None:
                continue
            n = d + (1 if (sel == 2 and attribs & 0x20) else 0)
            for i in range(n):
                if side == "br":
                    # MoveTo(R-i-1, B-1) LineTo(R-i-1, T+i); MoveTo(R-1, B-i-1) LineTo(L-1+i, B-i-1)
                    self._strip(item, R - i - 1, T + i + 1, 1, (B - 1) - (T + i), col)
                    self._strip(item, L + i, B - i - 1, (R - 1) - (L + i) + 1, 1, col)
                else:
                    # MoveTo(L+i, T) LineTo(L+i, B-i); MoveTo(L, T+i) LineTo(R-i, T+i)
                    self._strip(item, L + i, T, 1, (B - i) - T, col)
                    self._strip(item, L, T + i, (R - i) - L, 1, col)

    def _border(self, item, rect, eff: int, d: int, width: int) -> None:
        """the 'Border' option = a 1px black frame (GDI Rectangle
        with BLACK_PEN) half the pen plus one pixel outside the rectangle, and
        outside the 3D edge of a Raised / Lowered / Embossed one."""
        l, t, r, b = rect
        k = width // 2 + 1
        L, T, R, B = l - k, t - k, r + 1 + k, b + 1 + k
        odd = (width + 1) & 1
        R, B = R - odd, B - odd
        if eff in (1, 2, 4):
            L, T, R, B = L - d, T - d, R + d, B + d
        black = QtGui.QColor(0, 0, 0)
        self._strip(item, L, T, R - L, 1, black)
        self._strip(item, L, B - 1, R - L, 1, black)
        self._strip(item, L, T, 1, B - T, black)
        self._strip(item, R - 1, T, 1, B - T, black)

    def _make_item(self, obj: GraphicObject) -> QtWidgets.QGraphicsItem | None:
        g = obj.geometry
        k = obj.kind
        x, y, ex, ey = g.x, g.y, g.extent_x, g.extent_y
        item: QtWidgets.QGraphicsItem | None = None

        # Bitmap with a decoded inline picture draws the real pixels scaled
        # into its box (the actual symbol/genie art).
        if obj.raw.get("_embedded_bmp") is not None:
            pm_item = self._embedded_bitmap_item(obj, x, y, ex, ey)
            if pm_item is not None:
                pm_item.setData(0, None)  # page items are re-stamped with their index
                pm_item.setToolTip(self._tooltip(obj))
                # an unlinked bitmap's extent is its class-native rect (= rect1); the slot-1
                # H,K is move history, never an offset. Only a LINEAR part turns the picture,
                # about the box centre. A METAFILE never turns: CtDraw plays it axis-aligned
                # into its extent (CtDraw sets no world transform).
                t = obj.geometry.transform
                if (
                    (t.a, t.b, t.c, t.d) != (1.0, 0.0, 0.0, 1.0)
                    and not obj.raw.get("_bmp_grafted")
                    and not obj.raw.get("_metafile")
                ):
                    cx, cy = ex / 2.0, ey / 2.0  # item space (pos = box top-left)
                    pm_item.setTransform(
                        pm_item.transform()
                        * QtGui.QTransform(1, 0, 0, 1, -cx, -cy)
                        * QtGui.QTransform(t.a, t.b, t.c, t.d, 0, 0)
                        * QtGui.QTransform(1, 0, 0, 1, cx, cy)
                    )
                return pm_item

        if k in (ObjectKind.RECTANGLE, ObjectKind.NUMBER):
            # an INVISIBLE setpoint/slider HOTSPOT (runtime-animated rect sitting over a filled value
            # field; reader-tagged ``_no_outline``) draws nothing in CtDraw's editor
            if obj.raw.get("_no_outline") and not obj.style.filled:
                return None
            # (drawn from its data: a stored radius only; an art-stripped face takes its
            # owner's style)
            r = obj.style.corner_radius
            if r:
                path = QtGui.QPainterPath()
                path.addRoundedRect(QtCore.QRectF(x, y, max(ex, 1), max(ey, 1)), r, r)
                item = QtWidgets.QGraphicsPathItem(path)
            else:
                item = QtWidgets.QGraphicsRectItem(x, y, max(ex, 1), max(ey, 1))
            item.setPen(self._pen(obj))
            item.setBrush(self._brush(obj))
            if not r and item.pen().style() == _QT_NO_PEN and item.brush().gradient() is not None:
                # CtDraw fills a GRADIENT over (l, t, r + 1, b + 1) (ctf._Painter.square): a
                # null-pen gradient face covers its inclusive extent (a solid null-pen fill
                # covers l..r-1, as Qt's rect fill already does)
                item.setRect(QtCore.QRectF(x, y, max(ex, 1) + 1, max(ey, 1) + 1))
            if k == ObjectKind.NUMBER:
                self._text_label(obj, x, y, ex, ey, parent=item)
        elif k == ObjectKind.BUTTON:
            item = self._button_item(obj, x, y, ex, ey)
            mode = obj.raw.get("_button_mode", 0)
            face = colorref_to_qcolor(obj.raw.get("_button_face"), _btn.sys_colour(_btn.COLOR_BTNFACE))
            if mode in (0, 4):
                icon_face = obj.raw.get("_button_icon")
                if icon_face is not None:
                    # '#lib.element' caption = element bitmap as the face
                    iw, ih, rgba, _a = icon_face
                    img = QtGui.QImage(rgba, iw, ih, iw * 4, QtGui.QImage.Format_RGBA8888).copy()
                    pm = QtGui.QPixmap.fromImage(img)
                    if iw > ex - 2 or ih > ey - 2:
                        pm = pm.scaled(
                            int(ex - 2),
                            int(ey - 2),
                            QtCore.Qt.KeepAspectRatio,
                            QtCore.Qt.SmoothTransformation,
                        )
                    pix = QtWidgets.QGraphicsPixmapItem(pm, item)
                    pix.setPos(x + (ex - pm.width()) / 2.0, y + (ey - pm.height()) / 2.0)
                else:
                    # caption: style text, else the bag TEXT/Button_Text (a '#'
                    # value is an icon-face ref, handled above -- skip it)
                    cap = obj.style.text
                    if not cap:
                        props = obj.raw.get("_props") or {}
                        cap = str(props.get("TEXT") or props.get("Button_Text") or "")
                        if cap.startswith("#"):
                            cap = ""
                    if cap:
                        self._button_caption(obj, cap, x, y, ex, ey, item, face)
        elif k == ObjectKind.ELLIPSE:
            # (a pie / arc with start == end -- also (0, 0) -- is CtDraw's FULL pie: the
            # ellipse plus a radius line at the start angle; a full arc = the ellipse)
            if obj.style.ellipse_style in (1, 2):
                rect = QtCore.QRectF(x, y, max(ex, 1), max(ey, 1))
                # CtDraw's angles (as format/ctf.py draws them):
                # 0 = 12 o'clock, increasing CLOCKWISE (radial point
                # (cx + sin a rx, cy - cos a ry)); the GDI Pie runs from the
                # end radial counter-clockwise to the start one, i.e. clockwise
                # from start to end. Qt: 0 = 3 o'clock, counter-clockwise.
                ct_start = float(obj.style.start_angle)
                start = 90.0 - ct_start
                sweep = ct_start - float(obj.style.end_angle)
                if sweep >= 0:
                    sweep -= 360.0
                path = QtGui.QPainterPath()
                if obj.style.ellipse_style == 1:  # pie
                    path.moveTo(rect.center())
                    path.arcTo(rect, start, sweep)
                    path.closeSubpath()
                else:  # arc (open)
                    path.arcMoveTo(rect, start)
                    path.arcTo(rect, start, sweep)
                # a FILLED arc paints its chord region: BeginPath / Arc /
                # EndPath / FillPath, then strokes the Arc alone; a path item fills
                # the open subpath implicitly closed and strokes only the arc. An
                # UNFILLED arc picks by its stroke only (StrokePathItem), not by the chord
                item = (
                    QtWidgets.QGraphicsPathItem(path)
                    if obj.style.ellipse_style == 1
                    else StrokePathItem(path)
                )
                item.setPen(self._pen(obj))
                item.setBrush(self._brush(obj))
            else:
                item = QtWidgets.QGraphicsEllipseItem(x, y, max(ex, 1), max(ey, 1))
                item.setPen(self._pen(obj))
                item.setBrush(self._brush(obj))
        elif k == ObjectKind.LINE:
            pts = g.nodes or [(x, y), (x + ex, y + ey)]
            path = QtGui.QPainterPath(QtCore.QPointF(*pts[0]))
            for p in pts[1:]:
                path.lineTo(*p)
            item = StrokePathItem(path)
            item.setPen(self._pen(obj))
        elif k in (ObjectKind.POLYGON, ObjectKind.PIPE):
            if k == ObjectKind.PIPE and not g.nodes and not (obj.raw.get("_dwflags", 0) & 0x2):
                # an ART-STRIPPED pipe (no style block, no vertex array: a genie member whose
                # shape lives in its element twin) has no shape of its own: nothing, like the
                # vertex-less polygon rule below (the reader grafts the twin's vertices where
                # it finds them).
                return None
            pts = [QtCore.QPointF(px, py) for px, py in g.nodes] or [
                QtCore.QPointF(x, y),
                QtCore.QPointF(x + ex, y + ey),
            ]
            if k == ObjectKind.POLYGON and not obj.style.polygon_open and len(pts) >= 3:
                item = HollowPolygonItem(QtGui.QPolygonF(pts))
                item.setBrush(self._brush(obj))
                item.setPen(self._pen(obj))
            elif k == ObjectKind.POLYGON and not g.nodes:
                # vertex-less polygon WITHOUT the style block (dwFlags bit 2
                # clear) is an animation-only record, never a shape
                if not (obj.raw.get("_dwflags", 0) & 0x2):
                    return None
                # no decoded vertices -> draw the bounding box (filled if known)
                item = QtWidgets.QGraphicsRectItem(x, y, max(ex, 1), max(ey, 1))
                item.setBrush(self._brush(obj))
                item.setPen(self._pen(obj))
            else:
                path = QtGui.QPainterPath(pts[0])
                for p in pts[1:]:
                    path.lineTo(p)
                item = StrokePathItem(path)
                pen = self._pen(obj)
                if k == ObjectKind.PIPE:
                    item = self._pipe_item(obj, g.nodes or [(x, y), (x + ex, y + ey)], path)
                else:
                    item.setPen(pen)
                    # an OPEN polygon with a fill style is still FILLED: CtDraw's polygon draw
                    # paints Polygon with the NULL pen when fill_style != 0,
                    # then strokes Polyline() -- Qt fills the open subpath implicitly closed
                    if k == ObjectKind.POLYGON and obj.style.filled and len(pts) >= 3:
                        item.setBrush(self._brush(obj))
        elif k == ObjectKind.TEXT:
            item = self._text_item(obj, x, y, ex, ey)
            if obj.raw.get("_text_frame") and item is not None:
                # (in the text item's own coordinates: the frame is its child)
                _TextFrame(item.mapRectFromScene(QtCore.QRectF(x, y, ex, ey)), item)
        elif k == ObjectKind.GROUP:
            # container only -- children draw themselves; emit an invisible
            # anchor so dynamics badges have a host.
            item = QtWidgets.QGraphicsRectItem(x, y, max(ex, 1), max(ey, 1))
            item.setPen(_ANCHOR_PEN)
            item.setBrush(_ANCHOR_BRUSH)
        elif k in _PLACEHOLDER_KINDS:
            item = self._placeholder(obj, x, y, ex, ey)
        else:
            item = self._placeholder(obj, x, y, ex, ey)

        if item is not None:
            _pixel_align(item)
            if k in _EFFECT_KINDS and obj.style.style_flags:
                self._effects(item, obj)
            item.setData(0, None)  # page items are re-stamped with their index
            item.setToolTip(self._tooltip(obj))
            if not obj.geometry.transform.is_identity():
                t = obj.geometry.transform
                item.setTransform(QtGui.QTransform(t.a, t.b, t.c, t.d, t.h, t.k), True)
        return item

    # -- resolved symbol / genie ------------------------------------------
    def _make_symbol_item(self, obj: GraphicObject) -> QtWidgets.QGraphicsItem | None:
        """Render a placed symbol or genie whose element resolved."""
        group = QtWidgets.QGraphicsItemGroup()
        xform = None
        # inside a composite genie's CACHE element (and the elements it places) composition has
        # folded a constant hidden-when away; an ordinary genie's members all draw
        cache = "_CompositionCaches" in str(obj.raw.get("_resolved_ref") or "")
        outer = getattr(self, "_in_cache", 0)
        self._in_cache = outer + (1 if cache else 0)
        try:
            for child in obj.children:
                xform = child.raw.get("_symbol_xform") or xform
                self._add_symbol_child(child, group)
        finally:
            self._in_cache = outer
        # position + scale the whole group from the placement transform
        if xform is not None:
            ox, oy, sx, sy = xform
            t = QtGui.QTransform()
            t.translate(ox, oy)
            t.scale(sx if sx else 1.0, sy if sy else 1.0)
            group.setTransform(t)
        # slot-1 header matrix: apply its ORTHOGONAL part (rotation/mirror)
        # about the placement-rect centre; H,K are bookkeeping already in rect1.
        lx = obj.raw.get("_local_xform")
        if lx is not None and group.childItems():
            a, b, c, d = lx
            g = obj.geometry
            cx = g.x + g.extent_x / 2.0
            cy = g.y + g.extent_y / 2.0
            rot = QtGui.QTransform(1, 0, 0, 1, cx, cy)
            rot = QtGui.QTransform(a, b, c, d, 0, 0) * rot
            rot.translate(-cx, -cy)
            group.setTransform(group.transform() * rot)
        if not group.childItems():
            return self._placeholder(
                obj, obj.geometry.x, obj.geometry.y, obj.geometry.extent_x, obj.geometry.extent_y
            )
        group.setToolTip(self._symbol_tooltip(obj))
        return group

    def _add_symbol_child(self, child: GraphicObject, group) -> None:
        """Add one element child (or its own resolved sub-symbol) to ``group``."""
        if child.raw.get("_non_drawable") or child.raw.get("_state_hidden"):
            return
        # a composite-genie CACHE: composition folds a constant hidden-when TRUE away. An
        # ordinary genie's member with a hidden-when that is always true still draws in
        # CtDraw's editor.
        if child.raw.get("_rest_visible") is False and getattr(self, "_in_cache", 0):
            return
        if child.children:
            sub = self._make_symbol_item(child)
            if sub is not None:
                sub.setData(SIM_CHILD_ROLE, id(child))
                group.addToGroup(sub)
            return
        # container record inside the element stream draws nothing itself; its
        # members appear as separate flat children (an unresolved genie/symbol-set
        # container would paint a placeholder over its members' art). EXCEPTION: a
        # drawable LEAF that owns behaviour members paints its own art (mirrors the
        # page-level rule).
        if child.members and not _paints_with_members(child):
            return
        # artless symbol/state slot inside a resolved element is a RUNTIME slot
        # (populated by Cicode/animation), blank in CtDraw's static render -- a
        # placeholder here is noise.
        if (
            child.kind
            in (
                ObjectKind.SYMBOL,
                ObjectKind.SYMBOL_SET,
                ObjectKind.GENIE,
                ObjectKind.COMPOSITE_GENIE,
                ObjectKind.UNKNOWN,
            )
            and not child.children
            and child.raw.get("_embedded_bmp") is None
        ):
            return
        item = self._make_item(child)
        if item is not None:
            # _make_item stamps data(0) with the child's AN; inside a symbol
            # group that's NOT a page index, and small ANs collide with real
            # indices. Clear it so hit-testing walks up to the
            # group root, which carries the true page index.
            item.setData(0, None)
            item.setData(SIM_CHILD_ROLE, id(child))
            group.addToGroup(item)

    def _embedded_bitmap_item(self, obj: GraphicObject, x, y, ex, ey):
        """Render a bitmap's inline picture as a pixmap."""
        data = obj.raw.get("_embedded_bmp")
        if not data:
            return None
        w, h, rgba, _has_alpha = data
        if w <= 0 or h <= 0 or not rgba:
            return None
        pm = self._picture_pixmap(rgba, w, h)
        if pm is None:
            return None
        item = QtWidgets.QGraphicsPixmapItem(pm)
        item.setTransformationMode(_QT_SMOOTH)
        bw = ex if ex > 0 else w
        bh = ey if ey > 0 else h
        lx = obj.raw.get("_local_xform") if obj.raw.get("_bmp_grafted") else None
        if lx is not None:
            # a DIB grafted from the library twin is in the ELEMENT frame: CtDraw stretches it
            # into the twin's native rect and maps that through the member's Total, whose
            # quarter-turn / mirror our rect1 already shows as the post-rotation footprint. Fit
            # into the PRE-rotation box and turn about the centre.
            a, b, c, d = lx
            pw, ph = (bh, bw) if abs(b) > 0.5 or abs(c) > 0.5 else (bw, bh)
            item.setPos(0, 0)
            m = (
                QtGui.QTransform.fromScale(pw / w, ph / h)
                * QtGui.QTransform(1, 0, 0, 1, -pw / 2.0, -ph / 2.0)
                * QtGui.QTransform(a, b, c, d, 0, 0)
                * QtGui.QTransform(1, 0, 0, 1, x + bw / 2.0, y + bh / 2.0)
            )
            item.setTransform(m)
            return item
        item.setPos(x, y)
        if (bw, bh) != (w, h):
            # scale the native pixmap to fill the box, about the item origin
            # (top-left, placed at (x, y) by setPos).
            t = QtGui.QTransform()
            t.scale(bw / w, bh / h)
            item.setTransform(t)
        return item

    def _picture_pixmap(self, rgba, w: int, h: int):
        """The pixmap of a picture's pixels (None: an empty image). While a build runs, one
        per pixel buffer: the records of one picture share it."""
        memo = getattr(self, "_build_pixmaps", None)
        key = (id(rgba), w, h)
        if memo is not None:
            hit = memo.get(key)
            if hit is not None and hit[0] is rgba:
                return hit[1]
        pm = None
        img = QtGui.QImage(bytes(rgba), w, h, w * 4, _QT_RGBA8888)
        if not img.isNull():
            # QImage doesn't own the buffer; copy so it survives the local `rgba`.
            pm = QtGui.QPixmap.fromImage(img.copy())
            if pm.isNull():
                pm = None
        if memo is not None:
            memo[key] = (rgba, pm)  # (the buffer held: its id stays its own)
        return pm

    def _symbol_tooltip(self, obj: GraphicObject) -> str:
        ref = obj.raw.get("_resolved_ref") or obj.genie_template or ""
        lib = obj.raw.get("_resolved_lib") or ""
        lines = ["#%d  %s" % (obj.an, obj.kind.value)]
        if ref:
            lines.append("symbol: %s" % ref)
        if lib:
            lines.append("library: %s" % lib)
        lines.append("children: %d" % len(obj.children))
        return "\n".join(lines)

    # -- text / number ----------------------------------------------------
    def _text_string(self, obj: GraphicObject) -> str:
        text = obj.style.text
        if not text and obj.animations.display_value:
            text = str((obj.raw.get("_props") or {}).get("TXT_OFF") or "")
            # a genie member whose caption is a parameter ('%Line3%') that the instance set to a
            # blank value: the reader left the caption empty, and CtDraw draws nothing
            if text and _GENIE_TOKEN.search(text):
                text = ""
        if text:
            return text
        # empty value display: CtDraw shows a FORMAT mask only for NUMBER displays; a bare TEXT
        # value with no reader-assigned mask/caption renders BLANK. The reader already sets the
        # mask ('####.#'/explicit) where CtDraw shows one.
        return "####.#" if obj.kind is ObjectKind.NUMBER else ""

    def text_font(self, f: QtGui.QFont) -> QtGui.QFont:
        """``f`` as this scene draws page text: unsmoothed while set_aliased_text is on. (On
        Windows the painter's TextAntialiasing hint does not stop text smoothing; only the
        font's NoAntialias strategy does.)"""
        f.setStyleStrategy(QtGui.QFont.NoAntialias if self._aliased_text else QtGui.QFont.PreferDefault)
        return f

    def set_aliased_text(self, on: bool) -> None:
        """Draw the page's text unsmoothed (GDI's aliased text, as the Graphics Builder and the
        runtime draw it) or smoothed; the text items built so far follow at once."""
        on = bool(on)
        if on == self._aliased_text:
            return
        self._aliased_text = on
        for it in self.items():
            if isinstance(it, (QtWidgets.QGraphicsSimpleTextItem, QtWidgets.QGraphicsTextItem)):
                it.setFont(self.text_font(QtGui.QFont(it.font())))

    def _object_font(self, obj) -> QtGui.QFont:
        """Byte-pinned font (face, POINT size, B/I/U/S flags); the point size
        becomes CtDraw's em in page pixels (``_em``)."""
        st = obj.style
        if obj.raw.get("_font_px"):
            # a V3/V4 named font (fonts.dbf): its size is in pixels
            px = int(obj.raw["_font_px"])
        elif st.font_size:
            px = _em(st.font_size)
        else:
            # legacy text with no font block (most records carry no size byte):
            # Citect fits the run to the box, so approximate from the rect height
            # (clamped). The box is in page pixels measured at the system DPI, so the
            # point size it suggests is taken at that DPI (at 150 % the box is
            # 1.5x taller: the same point size).
            ey = getattr(obj.geometry, "extent_y", 0) or 0
            dpi = _text_dpi()
            pt = ey * 0.55 if dpi == 96 else ey * 0.55 * 96 / dpi
            px = _em(max(8, min(14, round(pt)))) if ey else _em(8)
        # one font per face, size and style: a copy of it
        key = (
            st.font or "Arial",
            px,
            bool(st.bold),
            bool(st.italic),
            bool(st.underline),
            bool(st.strikeout),
            self._aliased_text,
        )
        f = _FONT_CACHE.get(key)
        if f is None:
            f = QtGui.QFont(key[0])
            f.setPixelSize(px)
            f.setBold(st.bold)
            f.setItalic(st.italic)
            f.setUnderline(st.underline)
            f.setStrikeOut(st.strikeout)
            f = self.text_font(f)
            if len(_FONT_CACHE) > 512:
                _FONT_CACHE.clear()
            _FONT_CACHE[key] = f
        return QtGui.QFont(f)

    @staticmethod
    def _layout_text(t, obj, x, y, ex, ey) -> bool:
        """Place text item ``t`` (its text set) in the object box: True when
        it is turned or mirrored (laid about the box centre). The simulator
        lays a changed display value out again with it (sim_states)."""
        br = t.boundingRect()
        # text-tail 'orient' = CW quarter-turn count (1=0deg, 3=90CW, 2=180,
        # 4=270); a det<0 slot-1 matrix is a mirror. Both lay the run about the
        # box centre.
        turns = obj.raw.get("_text_turns", 0)
        lx = obj.raw.get("_local_xform")
        # a text that keeps a matrix draws its font scaled by it (a mirrored text resized:
        # CtDraw keeps the scale in the matrix, the point size as it was)
        ts = obj.raw.get("_text_scale")
        if turns or lx is not None or ts is not None:
            t.setPos(x + ex / 2.0 - br.width() / 2.0, y + ey / 2.0 - br.height() / 2.0)
            t.setTransformOriginPoint(br.width() / 2.0, br.height() / 2.0)
            if turns:
                t.setRotation(90.0 * turns)
            if lx is not None or ts is not None:
                # the text's matrix in page axes (its orthogonal part times the scale of its
                # columns); Qt applies it after the item's rotation, so a text whose page
                # stores its turns as the orient is turned first and then mirrored or
                # scaled through its matrix (a vertical text resized unevenly keeps the
                # scale across and down the page)
                a, b, c, d = lx if lx is not None else (1.0, 0.0, 0.0, 1.0)
                if ts is not None:
                    a, b, c, d = a * ts[0], b * ts[0], c * ts[1], d * ts[1]
                tr = QtGui.QTransform(1, 0, 0, 1, br.width() / 2.0, br.height() / 2.0)
                tr = QtGui.QTransform(a, b, c, d, 0, 0) * tr
                tr.translate(-br.width() / 2.0, -br.height() / 2.0)
                t.setTransform(tr)
            return True
        # justify within the object box (the box is the text bounds in Citect)
        tx, ty = x, y
        if ex > br.width():
            if obj.style.justify == TextJustify.CENTRE:
                tx = x + (ex - br.width()) / 2
            elif obj.style.justify == TextJustify.RIGHT:
                tx = x + (ex - br.width())
        asc = _gdi_ascent(obj, t.text().count("\n") + 1, ey)
        if asc is not None:
            # the baseline GDI's ascent below the box top, as CtDraw draws it from its anchor
            # (Qt's ascent differs by 1 to 3 px for some faces and sizes)
            ty = y + asc - QtGui.QFontMetricsF(t.font()).ascent()
        elif ey > br.height():
            ty = y + (ey - br.height()) / 2
        t.setPos(tx, ty)
        return False

    def _text_item(self, obj, x, y, ex, ey) -> QtWidgets.QGraphicsSimpleTextItem:
        t = JustifiedTextItem(self._text_string(obj), align=_justify_code(obj))
        t.setFont(self._object_font(obj))
        if obj.style.text_colour is None and obj.raw.get("_text_transparent"):
            t.setBrush(QtGui.QBrush(_QT_NO_BRUSH))  # a Transparent text colour
        else:
            t.setBrush(colorref_to_qcolor(obj.style.text_colour, QtGui.QColor("#111111")))
        if self._layout_text(t, obj, x, y, ex, ey):
            return t
        tx, ty = t.pos().x(), t.pos().y()
        # 3D text effect: hilight/lolight/shadow ghost behind the main draw
        # (effects byte 1 Raised / 2 Lowered / 3 Shadowed / 4 Embossed).
        if obj.style.three_d in (1, 2, 3, 4):
            # CtDraw's text draw (the effects switch after a helper(sel, dx, dy)):
            # Raised = lolight at +d, hilight at -d, then the text; Lowered = hilight +d,
            # lolight -d, text; Shadowed = shadow +d, text; Embossed = hilight +d,
            # lolight -d, then the text in the PAGE BACKGROUND colour. d = the effect
            # depth ('automatic' = 1 for a text); a no-colour slot draws
            # nothing.
            from ..format import geometry as G

            fl = obj.style.style_flags
            try:
                d = G.effect_depth(0x05, (0, 0, 0, 0), fl, obj.style.font_size or None) if fl else 1
            except Exception:  # noqa: BLE001
                _note_exc("scene.PageScene._text_item")
                d = 1
            copies = {1: ((2, d), (1, -d)), 2: ((1, d), (2, -d)), 3: ((3, d),), 4: ((1, d), (2, -d))}[
                obj.style.three_d
            ]
            group = QtWidgets.QGraphicsItemGroup()
            for sel, off in copies:
                col = self._sel_colour(obj, sel)
                if col is None or not off:
                    continue
                ghost = JustifiedTextItem(t.text(), align=t.align)
                ghost.setFont(t.font())
                ghost.setBrush(col)
                ghost.setPos(tx + off, ty + off)
                group.addToGroup(ghost)
            if obj.style.three_d == 4:
                body = self._sel_colour(obj, 4)
                if body is not None:
                    t.setBrush(body)
            group.addToGroup(t)
            return group
        return t

    def _text_label(self, obj, x, y, ex, ey, parent) -> None:
        """Value/number label inside a box (right-justified like Citect)."""
        t = QtWidgets.QGraphicsSimpleTextItem(self._text_string(obj), parent)
        t.setFont(self._object_font(obj))
        face = colorref_to_qcolor(obj.style.fill_colour, None)
        t.setBrush(colorref_to_qcolor(obj.style.text_colour, _contrast_text_colour(face)))
        br = t.boundingRect()
        if obj.raw.get("_label_left"):
            tx = x + 2  # (a V3/V4 Number / String: from the left)
        else:
            tx = x + max(0, ex - br.width() - 2)
        ty = y + max(0, (ey - br.height()) / 2)
        t.setPos(tx, ty)

    def _trend_item(self, obj, x, y, w, h) -> QtWidgets.QGraphicsItem:
        """A trend as CtDraw's editor draws it: sine traces for its pens."""
        import math
        from ..format.legacy import DEFAULT_PEN_COLOURS

        group = QtWidgets.QGraphicsItemGroup()
        l, t = int(round(x)), int(round(y))
        r, b = l + int(round(w)), t + int(round(h))
        info = obj.raw.get("_trend") or {}
        sp = max(1, int(info.get("spacing") or 1))
        pens = info.get("pens") or [(c, c) for c in DEFAULT_PEN_COLOURS]
        # the clip box is the extent one pixel wider / taller (rc.right / bottom + 1)
        height = (b + 1) - t
        count = (r + 1 - l) // sp + 1
        box = QtWidgets.QGraphicsRectItem(l, t, r + 1 - l, b + 1 - t)
        box.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        box.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        box.setFlag(QtWidgets.QGraphicsItem.ItemClipsChildrenToShape, True)
        group.addToGroup(box)
        if count >= 1 and height >= 1:
            half = height // 2
            # the angle is accumulated as CtDraw does (+= 2 pi / count)
            table, angle, step = [], 0.0, (3.14159265359 * 2.0) / count
            for _j in range(count):
                table.append(int(math.sin(angle) * half) + half)
                angle += step
            drawn = 0
            for _on, off in pens[:8]:
                if (off >> 24) & 0xFF:
                    continue  # no colour: the pen is not drawn
                shift = (count * drawn) // 16
                drawn += 1
                path = QtGui.QPainterPath(QtCore.QPointF(r + 0.5, t + table[shift % count] + 0.5))
                px = r
                for s in range(1, count):
                    px -= sp
                    path.lineTo(px + 0.5, t + table[(shift + s) % count] + 0.5)
                line = QtWidgets.QGraphicsPathItem(path, box)
                pen = QtGui.QPen(QtGui.QColor((off >> 16) & 0xFF, (off >> 8) & 0xFF, off & 0xFF))
                pen.setWidth(0)
                line.setPen(pen)
                line.setData(TREND_PART_ROLE, "design")
        # the runtime's pen lines in a clip box of their own (empty and hidden: the simulator
        # draws the engine's samples into them; made here so it never adds child items per scan)
        pens_box = QtWidgets.QGraphicsRectItem(l, t, r + 1 - l, b + 1 - t)
        pens_box.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        pens_box.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        pens_box.setFlag(QtWidgets.QGraphicsItem.ItemClipsChildrenToShape, True)
        pens_box.setData(TREND_PART_ROLE, "box")
        for _k in range(TREND_PENS):
            pl = QtWidgets.QGraphicsPathItem(QtGui.QPainterPath(), pens_box)
            pl.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            pl.setVisible(False)
            pl.setData(TREND_PART_ROLE, "pen")
        group.addToGroup(pens_box)
        focus = QtWidgets.QGraphicsRectItem(l + 0.5, t + 0.5, r - l, b - t)
        pen = QtGui.QPen(QtGui.QColor(0, 0, 0))
        pen.setWidth(0)
        pen.setStyle(QtCore.Qt.DotLine)
        focus.setPen(pen)
        focus.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        focus.setData(TREND_PART_ROLE, "design")
        group.addToGroup(focus)
        return group

    def _web_content_item(self, obj, x, y, w, h) -> QtWidgets.QGraphicsItem:
        """A Web Content object as CtDraw's editor draws it: a 1 px #6B6F73 frame filled #F3F4F5, the WEBCONTENTLOGO
        globe in the middle, 'Web Content' in bold 11 pt Segoe UI above it
        and the URL below it (#6B6F73, centred)."""
        from ..format import webcontent as W

        l, t = int(round(x)), int(round(y))
        r, b = l + int(round(w)), t + int(round(h))
        frame = QtGui.QColor((W.FRAME >> 16) & 0xFF, (W.FRAME >> 8) & 0xFF, W.FRAME & 0xFF)
        # GDI Rectangle(l, t, r, b): the frame on pixels l .. r-1 (the item is
        # put on pixel centres by _pixel_align)
        item = QtWidgets.QGraphicsRectItem(l, t, max(r - l - 1, 0), max(b - t - 1, 0))
        pen = QtGui.QPen(frame)
        pen.setWidth(0)
        item.setPen(pen)
        item.setBrush(QtGui.QBrush(QtGui.QColor((W.FACE >> 16) & 0xFF, (W.FACE >> 8) & 0xFF, W.FACE & 0xFF)))
        clip = QtWidgets.QGraphicsRectItem(l + 2, t + 2, max(r - l - 4, 0), max(b - t - 4, 0), item)
        clip.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        clip.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        clip.setFlag(QtWidgets.QGraphicsItem.ItemClipsChildrenToShape, True)
        from .ctres import resource_image

        logo = resource_image("WEBCONTENTLOGO")
        bw, bh = (logo.width(), logo.height()) if logo is not None else (0, 0)
        if logo is not None:
            pm = QtWidgets.QGraphicsPixmapItem(QtGui.QPixmap.fromImage(logo), clip)
            pm.setPos((r - l) // 2 - bw // 2 + l, (b - t) // 2 - bh // 2 + t)
        cy = (b - t) // 2 + t
        font = QtGui.QFont("Segoe UI")
        # 11 pt: lfHeight = -(LOGPIXELSY x 11) / 72 (C division) = a 14 px em at 96 dpi
        font.setPixelSize(_em(11))
        font.setBold(True)
        for text, bold in (("Web Content", True), (obj.raw.get("_web_url") or "", False)):
            if not text:
                continue
            f = QtGui.QFont(font)
            f.setBold(bold)
            ti = QtWidgets.QGraphicsSimpleTextItem(text, clip)
            ti.setFont(f)
            ti.setBrush(QtGui.QBrush(frame))
            br = ti.boundingRect()
            top = (cy - bh // 2 - 16 - br.height()) if bold else (cy + bh // 2 + 16)
            ti.setPos(l + ((r - l) - br.width()) / 2.0, top)
        return item

    # -- placeholder (symbol / set / genie / trend / unknown) -------------
    @staticmethod
    def _missing_element_item(obj) -> QtWidgets.QGraphicsItem:
        """CtDraw's X box: a white GDI Rectangle(l, t, r, b) in a 1 px black
        pen and its diagonals, LineTo (l, t) to (r, b) and (l, b - 1) to (r - 1, t), the last
        pixel left out. The diagonals are plotted as GDI does (a half rounds down); Qt's line
        rasteriser steps 1 px late."""
        l, t, r, b = obj.raw["_rect1"]
        l, r = min(l, r), max(l, r)
        t, b = min(t, b), max(t, b)
        pen = QtGui.QPen(QtGui.QColor(0, 0, 0))
        pen.setWidthF(1.0)
        item = QtWidgets.QGraphicsRectItem(l + 0.5, t + 0.5, r - l - 1, b - t - 1)
        item.setPen(pen)
        item.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255)))
        path = QtGui.QPainterPath()
        path.setFillRule(QtCore.Qt.WindingFill)  # (the crossing pixels overlap)
        for x, y in _gdi_line(l, t, r, b) + _gdi_line(l, b - 1, r - 1, t):
            path.addRect(x, y, 1, 1)
        diag = QtWidgets.QGraphicsPathItem(path, item)
        diag.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        diag.setBrush(QtGui.QBrush(QtGui.QColor(0, 0, 0)))
        item.setToolTip("%s was not found." % obj.raw.get("_missing_element"))
        return item

    def _placeholder(self, obj, x, y, ex, ey) -> QtWidgets.QGraphicsItem | None:
        """A stand-in for content that didn't resolve."""
        w = ex if ex > 1 else 0
        h = ey if ey > 1 else 0
        if w < 3 or h < 3:
            return None  # degenerate anchor
        if obj.kind == ObjectKind.TREND:
            return self._trend_item(obj, x, y, w, h)
        if obj.raw.get("_type_code") == 0x13:
            return self._web_content_item(obj, x, y, w, h)
        # A ProcessAnalyst trend control (tc=0x0D ActiveX, ACTIVEXEXT name *PA*)
        # draws a near-white chart SURFACE in CtDraw's design-time render: a flat
        # light fill. Gated to PA: other OLE controls have a dark surface. The
        # control is known by its OLE CLSID too (a new one has no name).
        from ..io.activex import PROCESS_ANALYST_CLSID

        if (
            obj.raw.get("_type_code") in (0x0D, 0x13)
            and w >= 16
            and h >= 16
            and (
                "PA" in str((obj.raw.get("_props") or {}).get("ACTIVEXEXT_*NAME", "")).upper()
                or obj.raw.get("_ole_clsid") == PROCESS_ANALYST_CLSID
            )
        ):
            item = self._control_item(obj, x, y, w, h)
            item.setBrush(QtGui.QBrush(QtGui.QColor(240, 240, 240)))
            pen = QtGui.QPen(QtGui.QColor(190, 190, 190))
            pen.setWidthF(1.0)
            item.setPen(pen)
            return item
        if obj.raw.get("_type_code") == 0x0D and w >= 8 and h >= 8:
            # another ActiveX control: the Graphics Builder runs the control, which paints its
            # whole box opaque and hides what lies under it. The stored state has no picture
            # of it (only the control's CONTENTS stream), and a 32-bit control cannot run in
            # this process: its box in the window colour, framed (the tool tip names the
            # control), until the control host's picture of it comes
            item = self._control_item(obj, x, y, w, h)
            item.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255)))
            pen = QtGui.QPen(QtGui.QColor(160, 160, 160))
            pen.setWidthF(1.0)
            item.setPen(pen)
            return item
        item = QtWidgets.QGraphicsRectItem(x, y, w, h)
        pen = QtGui.QPen(QtGui.QColor(140, 140, 140))
        pen.setWidthF(0.6)
        item.setPen(pen)
        item.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        return item

    @staticmethod
    def _control_item(obj, x, y, w, h) -> QtWidgets.QGraphicsRectItem:
        """An embedded control's box: its own picture (render/control_pictures) once the
        control host has taken it."""
        from .control_pictures import ControlItem

        return ControlItem(x, y, w, h, obj.raw.get("_ole_clsid") or "", obj.raw.get("_ole_state") or b"")

    def _pipe_item(self, obj, nodes, path):
        """A pipe as CtDraw shades it (render/pipes.py): hilight / lolight from
        the style block (a pipe without them: its line / fill colour, the
        lolight CtDraw's set-colour rule derives -- per byte c >> 1)."""
        from .pipes import pipe_strokes

        st = obj.style
        base = st.line_colour if st.line_colour is not None else st.fill_colour
        if base is None:
            base = 0x9F9F9F
        hi = st.hilight_colour if st.hilight_colour is not None else base
        lo = (
            st.lolight_colour
            if st.lolight_colour is not None
            else (((hi >> 16 & 0xFF) >> 1) << 16) | (((hi >> 8 & 0xFF) >> 1) << 8) | ((hi & 0xFF) >> 1)
        )
        width = st.line_width or 1
        # the pick target: the whole tube (invisible); the shaded strokes are children
        item = StrokePathItem(path)
        pick = QtGui.QPen(QtCore.Qt.transparent)
        pick.setWidth(max(1, width))
        item.setPen(pick)
        for spath, spen in pipe_strokes(
            [(int(round(px)), int(round(py))) for px, py in nodes], width, hi & 0xFFFFFF, lo & 0xFFFFFF
        ):
            # (paint only: the invisible tube above is the pick target, stroke-only)
            s = PaintOnlyPathItem(spath, item)
            s.setPen(spen)
            s.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        return item

    _ICON_PARENTS = frozenset(
        {ObjectKind.GROUP, ObjectKind.GENIE, ObjectKind.SYMBOL_SET, ObjectKind.COMPOSITE_GENIE}
    )

    def _anpoint_marker(self, obj):
        """An animation point's editor marker (_AnPointMarker) at its point, or None."""
        pt = obj.raw.get("_anchor")
        an = getattr(obj, "an", None)
        if not pt or an is None:
            return None
        item = _AnPointMarker(self, pt[0], pt[1], an)
        item.setToolTip("Animation point AN %d" % an)
        return item

    def _markers(self):
        return [it for it in self._items.values() if isinstance(it, _AnPointMarker)]

    def _resolve_marker_colours(self, items=None) -> None:
        """The drawn pixel under each ON-PAGE marker's point and number origin (CtDraw's
        GetPixel test; the markers themselves hidden). Off-page markers read the surround
        at paint time."""
        todo = [
            it for it in (items if items is not None else self._markers()) if isinstance(it, _AnPointMarker)
        ]
        marks = None
        for it in todo:
            it.under = it.under_text = None
            for attr, (px, py) in (("under", (it._x, it._y)), ("under_text", (it._x + 3, it._y - 9))):
                if not (0 <= px < self._page_w and 0 <= py < self._page_h):
                    continue
                if marks is None:
                    marks = [(m, m.isVisible()) for m in self._markers()]
                    for m, _v in marks:
                        m.setVisible(False)
                setattr(it, attr, self.sample_colour(px, py))
        for m, vis in marks or ():
            m.setVisible(vis)

    def _class_icon_item(self, page, obj):
        """CtDraw's design-time class icon ('f(x)' Cicode object, keyboard /
        colour-animation badge) at the record's top-left, for a record on
        the page or in a group / genie; a behaviour child of a drawn shape
        (a button's keyboard command) shows none."""
        name = obj.raw.get("_class_icon")
        if not name or obj.raw.get("_in_composite"):
            return None
        if obj.parent is not None:
            par = page.objects[obj.parent] if 0 <= obj.parent < len(page.objects) else None
            if par is None or par.kind not in self._ICON_PARENTS:
                return None
        from .ctres import class_icon

        img = class_icon(name)
        if img is None:
            return None
        item = QtWidgets.QGraphicsPixmapItem(QtGui.QPixmap.fromImage(img))
        item.setPos(int(round(obj.geometry.x)), int(round(obj.geometry.y)))
        item.setToolTip(self._tooltip(obj))
        return item

    def _button_item(self, obj, x, y, ex, ey):
        """A button's face per its CtDraw draw style (render/buttons.py): the
        area is rect1 INCLUSIVE of its right / bottom edge, pixel-aligned like
        GDI. The returned rect item is the (invisible) pick target."""
        l, t = int(round(x)), int(round(y))
        r, b = l + max(int(round(ex)), 1), t + max(int(round(ey)), 1)
        item = QtWidgets.QGraphicsRectItem(l, t, r - l + 1, b - t + 1)
        item.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        item.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        mode = obj.raw.get("_button_mode", 0)
        own = obj.raw.get("_button_face")

        def stroke(path, colour, dash=False):
            pen = QtGui.QPen(colour)
            pen.setWidth(0)  # cosmetic 1px
            if dash:
                pen.setDashPattern([18, 6])  # GDI PS_DASH
            # (paint only: the face's rect item picks; an open edge path would pick its
            # closed area)
            p = PaintOnlyPathItem(path, item)
            p.setPen(pen)
            p.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
            return p

        def fill(rx, ry, rw, rh, colour):
            if rw <= 0 or rh <= 0:
                return
            f = QtWidgets.QGraphicsRectItem(rx, ry, rw, rh, item)
            f.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            f.setBrush(QtGui.QBrush(colour))

        if mode == 4:  # XP_STYLE
            img = _btn.xp_button_image(r - l + 1, b - t + 1)
            pm = QtWidgets.QGraphicsPixmapItem(QtGui.QPixmap.fromImage(img), item)
            pm.setPos(l, t)
            if own is not None:
                fill(l + 4, t + 4, (r + 1 - 4) - (l + 4), (b + 1 - 4) - (t + 4), colorref_to_qcolor(own))
        elif mode == 0:  # NORMAL: classic push
            fill(l, t, r - l + 1, b - t + 1, _btn.sys_colour(_btn.COLOR_BTNFACE))
            for path, colour in _btn.raised_edges(l, t, r, b, soft=True):
                stroke(path, colour)
            if own is not None:
                fill(l + 2, t + 2, (r + 1 - 2) - (l + 2), (b + 1 - 2) - (t + 2), colorref_to_qcolor(own))
        elif mode == 1:  # BORDER_3D
            for path, colour in _btn.raised_edges(l, t, r, b, soft=False):
                stroke(path, colour)
        else:  # BORDER / TARGET
            stroke(_btn.frame_lines(l, t, r, b), _btn.sys_colour(_btn.COLOR_WINDOWFRAME), dash=(mode == 3))
        return item

    def _button_caption(self, obj, text, x, y, ex, ey, parent, face) -> None:
        """A button caption, drawn as CtDraw draws it."""
        from ..format.geometry import button_caption_lines

        st = obj.style
        f = QtGui.QFont(st.font or "Arial")
        f.setPixelSize(_em(st.font_size or 11))
        f.setBold(bool(st.bold))
        f.setItalic(bool(st.italic))
        f.setUnderline(bool(st.underline))
        f.setStrikeOut(bool(st.strikeout))
        self.text_font(f)
        col = colorref_to_qcolor(st.text_colour, None) if st.text_colour is not None else None
        brush = QtGui.QBrush(col or _btn.sys_colour(_btn.COLOR_BTNTEXT))
        lines = button_caption_lines(text)
        fm = QtGui.QFontMetricsF(f)
        from ..format import textmetrics

        attribs = (
            (0x10 if st.bold else 0)
            | (0x20 if st.italic else 0)
            | (0x40 if st.underline else 0)
            | (0x80 if st.strikeout else 0)
        )
        gm = textmetrics.caption_metrics(lines, st.font or "Arial", int(st.font_size or 11), attribs)
        if gm is not None:
            # GDI's own measures: integer widths, the line advance = the extent cy
            # (tmHeight, which can differ from Qt's height()), each baseline tmAscent
            # below its line top
            widths = [float(v) for v in gm[0]]
            lh, asc = float(gm[1]), float(gm[2])
        else:
            widths = [fm.horizontalAdvance(s) for s in lines]
            lh, asc = fm.height(), fm.ascent()
        bw = max([10.0] + widths)
        bh = max(10.0, lh * len(lines))
        w, h = int(round(ex)) + 1, int(round(ey)) + 1
        # C integer division (toward zero), as the draw centres the block
        bx = round(x) + int((w - bw) / 2.0)
        by = round(y) + int((h - bh) / 2.0)
        # the caption's 3D effect (by the effects nibble):
        # copies of the whole caption at (+d, +d) / (-d, -d) in the style block's
        # hilight / lolight / shadow, then the caption itself: Raised = lolight
        # +d, hilight -d; Lowered = hilight +d, lolight -d; Shadowed = shadow +d;
        # Embossed = hilight +d, lolight -d, the caption in the PAGE colour.
        # d = the effect depth.
        copies = ()
        if st.three_d in (1, 2, 3, 4):
            from ..format import geometry as G

            fl = st.style_flags
            try:
                d = G.effect_depth(0x1001, (0, 0, 0, 0), fl) if fl else 1
            except Exception:  # noqa: BLE001
                _note_exc("scene.PageScene._button_caption")
                d = 1
            copies = {1: ((2, d), (1, -d)), 2: ((1, d), (2, -d)), 3: ((3, d),), 4: ((1, d), (2, -d))}[
                st.three_d
            ]
            if st.three_d == 4:
                body = self._sel_colour(obj, 4)
                if body is not None:
                    brush = QtGui.QBrush(body)
        for n, (s, lw) in enumerate(zip(lines, widths)):
            if not s:
                continue
            if st.justify == TextJustify.RIGHT:
                lx = bx + bw - lw
            elif st.justify == TextJustify.CENTRE:
                lx = bx + int((bw - lw) / 2.0)
            else:
                lx = bx
            ly = by + n * lh + asc - fm.ascent()
            for sel, off in copies:
                ccol = self._sel_colour(obj, sel)
                if ccol is None or not off:
                    continue
                ghost = QtWidgets.QGraphicsSimpleTextItem(s, parent)
                ghost.setFont(f)
                ghost.setBrush(ccol)
                ghost.setPos(lx + off, ly + off)
            lbl = QtWidgets.QGraphicsSimpleTextItem(s, parent)
            lbl.setFont(f)
            lbl.setBrush(brush)
            lbl.setPos(lx, ly)

    def _label(self, text, x, y, w, h, parent=None, small=False, colour=None):
        # QGraphicsSimpleTextItem (not QGraphicsTextItem): the QObject-derived
        # text item is GC'd once the caller discards the wrapper even with a
        # parent, so the caption never paints. The simple item survives.
        lbl = QtWidgets.QGraphicsSimpleTextItem(text, parent)
        col = QtGui.QColor(colour) if colour else QtGui.QColor("#444" if small else "#000")
        lbl.setBrush(QtGui.QBrush(col))
        lbl.setFont(QtGui.QFont("Arial", 6 if small else 8))
        br = lbl.boundingRect()
        lbl.setPos(x + max(0, (w - br.width()) / 2.0), y + max(0, (h - br.height()) / 2.0))
        return lbl

    def _add_badges(self, obj: GraphicObject, item) -> None:
        # dynamics badges are an EDITOR aid, not CtDraw parity; they read as
        # page content (green '#', red 'C' glyphs), so opt-in only (the switch as the
        # build read it: set_page takes _render_env, update_units builds only under the same)
        env = getattr(self, "_render_env", None)
        if (env[0] if env is not None else os.environ.get("CTG_DYN_BADGES")) != "1":
            return
        a = obj.animations
        active = []
        for name, (glyph, color) in _DYN_BADGE.items():
            val = getattr(a, name, None)
            if val:
                active.append((glyph, color))
        if not active:
            return
        bx = item.boundingRect().right() + 2
        by = item.boundingRect().top()
        for i, (glyph, color) in enumerate(active):
            dot = QtWidgets.QGraphicsSimpleTextItem(glyph, item)
            dot.setBrush(QtGui.QColor(color))
            f = QtGui.QFont("Consolas", 6)
            f.setBold(True)
            dot.setFont(f)
            dot.setPos(bx, by + i * 9)

    def _tooltip(self, obj: GraphicObject) -> str:
        a = obj.animations
        dyn = [n for n in _DYN_BADGE if getattr(a, n, None)]
        lines = ["#%d  %s" % (obj.an, obj.kind.value)]
        if obj.cls:
            lines.append("class: %s" % obj.cls)
        if dyn:
            lines.append("dynamics: " + ", ".join(dyn))
        return "\n".join(lines)


from ..infra import caches as _caches  # noqa: E402

_caches.register("scene.fonts", _FONT_CACHE, on=("tests",))
