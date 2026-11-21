"""Draw the simulator's live object states (the Simulate tab)."""

from __future__ import annotations

import bisect
import math
from typing import Dict, List, NamedTuple, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..model.document import GraphicObject, Geometry, ObjectKind, TextJustify
from ..sim.state import (
    DISABLE_GRAY_ALL,
    DISABLE_GRAY_PART,
    DISABLE_GRAY_SUNK,
    DISABLE_HIDE,
    FILL_DOWN,
    FILL_LEFT,
    FILL_RIGHT,
    ObjectState,
    PageState,
)
from .scene import AI_PART_ROLE, PAGE_INDEX_ROLE, SIM_CHILD_ROLE, TREND_PART_ROLE
from . import sim_alarmlists as _alm
from ..infra.logsetup import note_exception as _note_exc
from ..format import codes as _codes


def _value_box(obj, item):
    """The Bounds of the string a text / number item shows now, measured the way the engine
    lays text out (format.textmetrics.extent from the record's baseline anchor); None when it
    cannot be measured (no font size, no anchor, no GDI, not a text)."""
    if obj.kind not in (ObjectKind.TEXT, ObjectKind.NUMBER):
        return None
    text = item.text() if hasattr(item, "text") else None
    if text is None:
        texts = [c.text() for c in item.childItems() if hasattr(c, "text")]
        text = texts[0] if len(texts) == 1 else None
    st = obj.style
    anchor = obj.raw.get("_anchor")
    if not text or not st.font_size or not anchor:
        return None
    from ..format import textmetrics as TM

    attribs = st.style_flags[3] if st.style_flags and len(st.style_flags) > 3 else 0
    orient = {0: 1, 1: 3, 2: 2, 3: 4}.get(int(obj.raw.get("_text_turns") or 0) % 4, 1)
    try:
        return TM.extent(
            text,
            st.font or "Arial",
            int(st.font_size),
            int(attribs),
            orient,
            0,
            (int(anchor[0]), int(anchor[1])),
        )
    except Exception:  # noqa: BLE001
        _note_exc("sim_states._value_box")
        return None


#: item data role naming the page record a swapped picture inside a composite genie draws for
#: (its items carry the genie's PAGE_INDEX_ROLE, like the rest of the genie's picture)
SIM_RECORD_ROLE = 3

#: the closed shapes a fill level fills (else a level reveals)
_CLOSED_KINDS = frozenset({ObjectKind.RECTANGLE, ObjectKind.ELLIPSE, ObjectKind.POLYGON, ObjectKind.NUMBER})
#: the quality flags the runtime dithers an object with ([Page]IgnoreValueQuality 0)
_DITHERED = frozenset({"bad", "uncertain", "error", "override", "inhibit"})
#: [Page]<Kind>DitheringColor / -Density defaults (help 1118013 ...: black, 2) per flag
DITHER_DEFAULTS = {
    "bad": (0x000000, 2),
    "uncertain": (0x000000, 2),
    "error": (0x000000, 2),
    "override": (0x000000, 2),
    "inhibit": (0x000000, 2),
}
#: item data role on a quality dither overlay (tests)
DITHER_ROLE = 11


def dither_brush(colour: int, density: int) -> QtGui.QBrush:
    """The dot pattern for a dithering density (1 highest to 4 lowest)."""
    d = max(1, min(4, int(density)))
    img = QtGui.QImage(2 * d, 4 * d, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    c = QtGui.QColor((colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF)
    img.setPixelColor(d, 0, c)
    img.setPixelColor(0, 2 * d, c)
    return QtGui.QBrush(img)


#: disable style -> the greyed look (None = unchanged)
_GRAY = {DISABLE_GRAY_SUNK: "sunk", DISABLE_GRAY_ALL: "sunk", DISABLE_GRAY_PART: "part"}
#: a state matrix whose determinant is smaller than this collapses the record (drawn hidden)
_MIN_DET = 1e-6


class _Look(NamedTuple):
    """What one record's items show, from its own state and its ancestors' (compared between
    scans: an unchanged look touches nothing)."""

    hidden: bool
    gray: Optional[str]
    matrix: Optional[tuple]  # (m11, m12, m21, m22, dx, dy) in page coordinates
    colour: Optional[int]
    level: Optional[tuple]  # own level: (fraction, direction, background colour)
    reveal: tuple  # ((l, t, r, b, fraction, direction), ...) of the chain
    text: Optional[str]
    tooltip: Optional[str]
    vertex: Optional[tuple]
    symbol: Optional[str]
    trend: Optional[tuple] = None  # ((pen, colour, lo, hi, (value | None, ...)), ...)
    alarm: Optional[tuple] = None  # (priority, state, colour, flash) of an active alarm
    indication: Optional[str] = None  # the quality flag dithered over the record
    flash: bool = False  # the off flash phase over the record's own flashing colours


_DEFAULT = _Look(False, None, None, None, None, (), None, None, None, None)


class _Chain(NamedTuple):
    """What a record's ancestors give it (SimLayer._chain_look)."""

    hidden: bool
    gray: object
    disabled: bool
    matrix: object
    colour: object
    tooltip: object
    reveal: tuple
    any: bool


def _number(v) -> Optional[float]:
    try:
        f = float(v)
    except TypeError, ValueError:
        return None
    return f if math.isfinite(f) else None


def _trend_look(trend) -> Optional[tuple]:
    """A trend state's pens as the runtime look draws them."""
    if not isinstance(trend, dict):
        return None
    out = []
    for p in trend.get("pens") or ():
        if not isinstance(p, dict):
            continue
        sc = p.get("scale")
        if not isinstance(sc, (list, tuple)) or len(sc) < 2:
            continue
        lo, hi = _number(sc[0]), _number(sc[1])
        vals = p.get("values")
        if lo is None or hi is None or hi == lo or not isinstance(vals, (list, tuple)):
            continue
        c = p.get("colour")
        colour = int(c) if isinstance(c, (int, float)) and not isinstance(c, bool) else None
        num = p.get("pen")
        try:
            num = int(num)
        except TypeError, ValueError:
            num = len(out) + 1
        out.append((num, colour, lo, hi, tuple(_number(v) for v in vals)))
    return tuple(out) if out else None


def _alarm_look(alarm) -> Optional[tuple]:
    """An alarm indicator state that shows: (priority, state, colour, flash), None when the
    equipment has no active alarm (priority 0 or state 'none': the runtime draws no frame)."""
    if not isinstance(alarm, dict):
        return None
    try:
        pr = int(alarm.get("priority") or 0)
    except TypeError, ValueError:
        pr = 0
    state = str(alarm.get("state") or "none")
    if pr <= 0 or state == "none":
        return None
    c = alarm.get("colour")
    colour = int(c) if isinstance(c, (int, float)) and not isinstance(c, bool) else None
    return (pr, state, colour, bool(alarm.get("flash")))


def _is_transparent(c) -> bool:
    return c is not None and ((int(c) >> 24) & 0xFF) == 0xFF


def _qcolour(c) -> Optional[QtGui.QColor]:
    """A packed 0x00RRGGBB colour; None for transparent (high byte 0xFF)."""
    if c is None or _is_transparent(c):
        return None
    c = int(c)
    return QtGui.QColor((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)


def _mtuple(m: Optional[QtGui.QTransform]) -> Optional[tuple]:
    if m is None or m.isIdentity():
        return None
    return (m.m11(), m.m12(), m.m21(), m.m22(), m.dx(), m.dy())


def _matrix(t: tuple) -> QtGui.QTransform:
    return QtGui.QTransform(t[0], t[1], t[2], t[3], t[4], t[5])


def _clamp01(v) -> float:
    try:
        v = float(v)
    except TypeError, ValueError:
        return 0.0
    return 0.0 if v != v else max(0.0, min(1.0, v))  # NaN -> 0


def _level_rect(r: QtCore.QRectF, fraction: float, direction: int) -> QtCore.QRectF:
    """The filled part of ``r``: Up fills from the bottom, Down from the top, Left from the
    right, Right from the left (doc: 1172169 'If you choose Left, the object/group will be
    filled from right to left')."""
    w, h = r.width(), r.height()
    if direction == FILL_DOWN:
        return QtCore.QRectF(r.left(), r.top(), w, h * fraction)
    if direction == FILL_LEFT:
        return QtCore.QRectF(r.right() - w * fraction, r.top(), w * fraction, h)
    if direction == FILL_RIGHT:
        return QtCore.QRectF(r.left(), r.top(), w * fraction, h)
    return QtCore.QRectF(r.left(), r.bottom() - h * fraction, w, h * fraction)  # FILL_UP


def _child_flashes(o) -> bool:
    """A resolved child of ``o`` (an expanded symbol / genie element) has flashing colours."""
    stack = list(o.children)
    while stack:
        c = stack.pop()
        if c.raw.get("_flash_slots") is not None:
            return True
        stack.extend(c.children)
    return False


def _other_half(c: QtGui.QColor, slots) -> Optional[QtGui.QColor]:
    """The other half of the flashing (on, off) pair of ``slots`` one of whose halves is the
    drawn colour ``c`` (a transparent half = a colour with alpha 0); None: no such pair."""
    if c is None or c.alpha() == 0:
        return None
    rgb = c.rgb() & 0xFFFFFF
    for a, b in slots:
        if a == b:
            continue
        for mine, other in ((a, b), (b, a)):
            if not _is_transparent(mine) and (int(mine) & 0xFFFFFF) == rgb:
                q = _qcolour(other)
                return q if q is not None else QtGui.QColor(0, 0, 0, 0)
    return None


def _walk(item) -> list:
    """``item`` and every descendant item (childItems() returns a list: no ownership side
    effect, unlike parentItem())."""
    out, stack = [], [item]
    while stack:
        it = stack.pop()
        out.append(it)
        stack.extend(it.childItems())
    return out


def _stamp(item, index: int) -> None:
    item.setData(0, index)
    for it in _walk(item):
        it.setData(PAGE_INDEX_ROLE, index)


def _ref_core(text: str) -> str:
    """'lib.element' (lower case) of a stored reference ('lib.element (ctl)' /
    'lib.element.<ctl')."""
    s = (text or "").split(" (")[0]
    k = s.find(".<ct")
    if k >= 0:
        s = s[:k]
    return s.strip().lower()


def _is_anchor(item) -> bool:
    """The invisible anchor rect a container record gets (it draws nothing itself)."""
    return (
        isinstance(item, QtWidgets.QGraphicsRectItem)
        and item.pen().style() == QtCore.Qt.NoPen
        and item.brush().style() == QtCore.Qt.NoBrush
        and not item.childItems()
    )


# -- the greyed / embossed look and the reveal clip ---------------------------------------
def _greyscale(src: QtGui.QImage) -> QtGui.QImage:
    """``src`` in grey levels, alpha kept (all in Qt: no per-pixel Python)."""
    g = (
        src.convertToFormat(QtGui.QImage.Format_ARGB32)
        .convertToFormat(QtGui.QImage.Format_Grayscale8)
        .convertToFormat(QtGui.QImage.Format_ARGB32_Premultiplied)
    )
    p = QtGui.QPainter(g)
    p.setCompositionMode(QtGui.QPainter.CompositionMode_DestinationIn)
    p.drawImage(0, 0, src)
    p.end()
    return g


def _silhouette(src: QtGui.QImage, colour: QtGui.QColor) -> QtGui.QImage:
    out = QtGui.QImage(src.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
    out.setDevicePixelRatio(src.devicePixelRatio())
    out.fill(colour)
    p = QtGui.QPainter(out)
    p.setCompositionMode(QtGui.QPainter.CompositionMode_DestinationIn)
    p.drawImage(0, 0, src)
    p.end()
    return out


#: the most pixels one greyed / embossed draw processes (the visible part of its item)
_EFFECT_MAX_PIXELS = 4096 * 4096


class _StateEffect(QtWidgets.QGraphicsEffect):
    """A disabled look and / or clip path on one item."""

    def __init__(self, gray: Optional[str], clip: Optional[QtGui.QPainterPath]):
        super().__init__()
        self._gray = gray
        self._clip = clip
        self._cache = None  # (key, device region, processed image)

    def sourceChanged(self, flags):  # noqa: N802 -- Qt naming
        self._cache = None  # the item (or a child) changed: grey it again

    def boundingRectFor(self, rect):  # noqa: N802 -- Qt naming
        r = QtCore.QRectF(rect)
        return r.adjusted(0, 0, 2, 2) if self._gray == "sunk" else r

    def _processed(self, src: QtGui.QImage) -> QtGui.QImage:
        """``src`` (premultiplied ARGB) greyed, or embossed: a lighter grey over a white copy
        one pixel down-right (cut to the same size: the region was padded for it)."""
        out = _greyscale(src)
        if self._gray == "sunk":
            p = QtGui.QPainter(out)
            p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceAtop)
            p.fillRect(out.rect(), QtGui.QColor(255, 255, 255, 110))
            p.end()
            sunk = QtGui.QImage(src.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
            sunk.setDevicePixelRatio(src.devicePixelRatio())
            sunk.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(sunk)
            p.drawImage(QtCore.QPointF(1, 1), _silhouette(src, QtGui.QColor(255, 255, 255)))
            p.drawImage(QtCore.QPointF(0, 0), out)
            p.end()
            out = sunk
        return out

    def _region(self, painter: QtGui.QPainter, clip: bool) -> QtCore.QRect:
        """The device rect to process: the source's device bounds (+1 px for the embossed
        copy) cut to the paint device, and with ``clip`` to the painter's clip."""
        r = self.sourceBoundingRect(QtCore.Qt.DeviceCoordinates)
        if not all(math.isfinite(v) and abs(v) < 1e9 for v in r.getCoords()):
            return QtCore.QRect()
        out = r.toAlignedRect().adjusted(-1, -1, 2, 2)
        dev = painter.device()
        if dev is not None:
            out &= QtCore.QRect(0, 0, dev.width(), dev.height())
        if clip and painter.hasClipping():
            c = painter.worldTransform().mapRect(painter.clipBoundingRect())
            out &= c.toAlignedRect().adjusted(-1, -1, 1, 1)
        return out

    def draw(self, painter: QtGui.QPainter) -> None:
        painter.save()
        try:
            if self._clip is not None:
                painter.setClipPath(self._clip, QtCore.Qt.IntersectClip)
            if self._gray is None:
                self.drawSource(painter)
                return
            need = self._region(painter, clip=True)
            if need.isEmpty():
                return
            dev = painter.device()
            dpr = dev.devicePixelRatioF() if dev is not None else 1.0
            w = painter.worldTransform()
            key = (w.m11(), w.m12(), w.m21(), w.m22(), w.dx(), w.dy(), dpr, id(dev))
            c = self._cache
            if c is None or c[0] != key or not c[1].contains(need):
                # the whole visible part (later partial repaints reuse it), unless too big
                region = self._region(painter, clip=False)
                if region.width() * region.height() > _EFFECT_MAX_PIXELS:
                    region = need
                if region.width() * region.height() > _EFFECT_MAX_PIXELS:
                    self.drawSource(painter)  # too big to grey: the plain look
                    return
                img = QtGui.QImage(
                    max(1, round(region.width() * dpr)),
                    max(1, round(region.height() * dpr)),
                    QtGui.QImage.Format_ARGB32_Premultiplied,
                )
                if img.isNull():  # out of memory: the plain look
                    self.drawSource(painter)
                    return
                img.setDevicePixelRatio(dpr)
                img.fill(QtCore.Qt.transparent)
                p = QtGui.QPainter(img)
                p.setRenderHints(painter.renderHints())
                # the source as it would be drawn on the device, moved so the region is at 0, 0
                p.setWorldTransform(w * QtGui.QTransform.fromTranslate(-region.x(), -region.y()))
                self.drawSource(p)
                p.end()
                c = self._cache = (key, region, self._processed(img))
            painter.setWorldTransform(QtGui.QTransform())  # (the clip stays in device space)
            painter.drawImage(QtCore.QPointF(c[1].topLeft()), c[2])
        finally:
            painter.restore()


# -- per item: its static look, captured on first touch -------------------------------------
class _Snap:
    __slots__ = (
        "item",
        "t_static",
        "t_base",
        "s_base",
        "texts",
        "tips",
        "fill",
        "text_brush",
        "pipe_pens",
        "extras",
        "shape",
        "effect",
        "outer",
        "geom",
        "flashed",
    )

    def __init__(self, item):
        self.item = item
        self.outer = None  # the composite genie whose picture holds the item
        self.t_static = QtGui.QTransform(item.transform())
        self.t_base = QtGui.QTransform(self.t_static)  # without the animation (a relaid text)
        self.s_base = None  # the scene transform with t_base (lazily)
        self.texts = None  # [(text item, text, pos, rotation, origin, transform)]
        self.tips = None  # [(item, tool tip)]
        self.fill = None  # (pen, brush) before a colour / level
        self.text_brush = None  # (text item, brush)
        self.pipe_pens = None  # [(stroke item, pen)]
        self.extras: list = []  # child items this layer added (level, face)
        self.shape = None  # polygon / path before vertex offsets
        self.effect = False
        self.geom = None  # [(shape item, geometry)] before a scaled rebuild
        self.flashed = None  # [(item, pen, brush, text colour)] before the off phase


class SimLayer:
    """The live states of one PageScene (``PageScene.sim_layer``)."""

    def __init__(self, scene):
        self._scene = scene
        self.states: Dict[int, ObjectState] = {}  # by AN, copies
        self._index_of: Dict[int, int] = {}  # AN -> record index
        self._map_serial = None
        self._keys_serial = None
        self._keys: List[int] = []
        self._applied: Dict[int, _Look] = {}  # record index -> look drawn now
        self._snaps: Dict[tuple, _Snap] = {}  # (index, role) -> snapshot
        self._chain_memo: Optional[dict] = None  # (apply: the ancestors' looks)
        self._swap_cache: Dict[tuple, object] = {}  # (index, symbol) -> item / None
        self._swap_active: Dict[int, object] = {}  # index -> the swap item shown
        self._bg_items: Dict[int, object] = {}  # index -> group level background
        self._dither_items: Dict[int, object] = {}  # index -> quality dither overlay
        self._dither_keys: Dict[int, tuple] = {}  # index -> its brush (colour, grid)
        self._faces: Dict[int, tuple] = {}  # index -> (button item, its face)
        self._edge_fills: Dict[int, tuple] = {}  # index -> (rect item, its own rect)
        self._nudged: Dict[tuple, tuple] = {}  # (index, id) -> (stroke item, geometry)
        self._ai_flags: Dict[tuple, object] = {}  # (index, priority) -> flag item / None
        self._parts: Dict[int, dict] = {}  # composite root -> its members' items
        self._parts_serial = None
        self._swap_inside: Dict[int, int] = {}  # id(swap item) -> composite root
        self._resolver = None
        self._resolver_path = None
        self._runtime = False
        self._suspended = False
        self._flash_on = True
        self._flash_serial = None
        self._flash_items: list = []  # [(pixmap item, on, off picture)]
        self._fc_serial = None
        self._fc: set = set()  # records with flashing colours
        self._alm = None  # alarm lists (sim_alarmlists)
        self._ai_paint: Dict[int, tuple] = {}  # index -> (SymbolPaint, state, label)
        self._ai_geom: Dict[int, list] = {}  # index -> the frame's built geometry
        self._ai_flag_objs: Dict[tuple, GraphicObject] = {}  # (index, priority) -> flag genie
        #: symbols that did not resolve ('lib.element')
        self.missing_symbols: set = set()
        #: ANs given a state that no record of the page has
        self.unmapped: set = set()
        self._drawn = None  # the page's drawn output (sim_drawn)

    # -- public ------------------------------------------------------------
    def active(self) -> bool:
        return (
            bool(self.states or self._applied)
            or not self._flash_on
            or (self._drawn is not None and self._drawn.active())
        )

    def drawn_layer(self):
        """What the page's Cicode drew (render/sim_drawn.DrawnLayer), made on first use."""
        if self._drawn is None:
            from .sim_drawn import DrawnLayer

            self._drawn = DrawnLayer(self)
        return self._drawn

    def apply_drawn(self, drawn) -> dict:
        """Draw the page's drawn list (PageState.drawn: the whole list, {AN: entry})."""
        return self.drawn_layer().apply(drawn)

    def apply(self, states, replace: bool = False) -> dict:
        """Merge ``states`` into the drawn ones and redraw what changed."""
        if isinstance(states, PageState):
            states = states.objects
        page = self._page()
        if page is None:
            return {"changed": 0, "refreshed": 0, "touched": 0, "unmapped": 0, "undrawn": 0}
        self._ensure_map()
        incoming = {}
        for an, st in (states or {}).items():
            try:
                incoming[int(an)] = st
            except TypeError, ValueError:
                continue
        if replace:
            for an in list(self.states):
                if an not in incoming:
                    incoming[an] = None
        changed: List[int] = []
        unmapped = 0
        loose: set = set()  # changed ANs without a record (DspSym members ...)
        for an, st in incoming.items():
            old = self.states.get(an)
            # compared before it is copied: an unchanged state (most of a scan) costs one
            # comparison
            if st is None:
                if old is None:
                    continue
                new = None
            elif old is not None and st == old:
                continue
            elif st.is_default():
                if old is None:
                    continue
                new = None
            else:
                new = _copy_state(st)
                if new == old:
                    continue
            if new is None:
                self.states.pop(an, None)
            else:
                self.states[an] = new
            idx = self._index_of.get(an)
            if idx is None:
                self.unmapped.add(an)
                unmapped += 1
                loose.add(an)
                continue
            self.unmapped.discard(an)
            changed.append(idx)
        affected = set()
        undrawn = 0
        sc = self._scene
        for idx in changed:
            sub = self._subtree(idx)
            affected.update(sub)
            now = self.states.get(page.objects[idx].an)
            swaps = now is not None and (bool(now.symbol) or now.symbol_state is not None)
            if not swaps and not any(self._has_item(j) for j in sub):
                undrawn += 1  # a symbol set state not drawn, a record without art ...
        touched = 0
        self._chain_memo = {}  # (the states are set: the ancestor looks hold)
        try:
            for i in sorted(affected):
                touched += self._refresh(i)
        finally:
            self._chain_memo = None
        # an active alarm indicator follows its group's content (a member grew or moved)
        for i, ai in list(sc._ai_items.items()):
            look = self._applied.get(i)
            if (
                look is not None
                and look.alarm is not None
                and not look.hidden
                and any(
                    j in affected
                    for j, _r in (page.objects[i].raw.get("_alarm_indicator") or {}).get("members") or ()
                )
            ):
                self._size_alarm_frame(i, ai, True)
        if self._alm is not None or any(
            st is not None and (st.alarm_list is not None or st.alarm_row is not None)
            for st in incoming.values()
        ):
            self.alarm_lists().sync()
        if loose and self._drawn is not None:
            # the members of a genie DspSym placed are page objects of the engine's only:
            # the drawn genie draws their states (render/sim_drawn.py _Members)
            self._drawn.member_states(self.states, loose)
        return {
            "changed": len(changed),
            "refreshed": len(affected),
            "touched": touched,
            "unmapped": unmapped,
            "undrawn": undrawn,
        }

    def clear(self) -> None:
        """Every record back to its static look."""
        self.states = {}
        for i in sorted(self._applied):
            self._refresh(i)
        if self._drawn is not None:
            self._drawn.clear()
        if self._alm is not None:
            self._alm.sync()

    def suspend(self) -> None:
        """The static look for an export (states kept; :meth:`resume` draws them again)."""
        if self._suspended:
            return
        self._suspended = True
        if self._drawn is not None:
            self._drawn.suspend()
        for i in sorted(self._applied):
            self._refresh(i)
        if self._alm is not None:
            self._alm.set_hidden(True)
        if not self._flash_on:
            for item, first, _second in self._flash_items:
                item.setPixmap(first)

    def resume(self) -> None:
        if not self._suspended:
            return
        self._suspended = False
        if self._drawn is not None:
            self._drawn.resume()
        self.rebind()
        if self._alm is not None:
            self._alm.set_hidden(False)
        if not self._flash_on:
            for item, _first, second in self._flashing_items():
                item.setPixmap(second)

    def set_runtime_look(self, on: bool) -> None:
        """Hide what only the editor draws."""
        on = bool(on)
        if on == self._runtime:
            return
        self._runtime = on
        page = self._page()
        if page is None:
            return
        objs = page.objects
        for i, item in self._scene._items.items():
            if 0 <= i < len(objs) and self._non_drawable(i):
                item.setVisible(self._visible(i, "main"))
        for i, item in self._scene._ai_items.items():
            item.setVisible(self._visible(i, "ai"))
        for it in self._target_frames():
            it.setVisible(not on)
        for it in self._trend_design_parts():
            it.setVisible(not on)
        self._runtime_fill_edges(on)
        self._runtime_strokes(on)

    def _runtime_drawn(self, i: int) -> bool:
        """Whether the runtime draws record ``i`` itself (its .ctF keeps the record) rather than
        showing it in the page's baked background: the saver's dynamic flag (dwFlags 0x10) on
        the record or a group / genie holding it (ctf.bake_plan's dynamic test)."""
        page = self._page()
        objs = page.objects
        j, guard = i, 0
        while j is not None and 0 <= j < len(objs) and guard < 32:
            if int(objs[j].raw.get("_dwflags") or 0) & 0x10:
                return True
            j = objs[j].parent
            guard += 1
        return False

    def _runtime_fill_edges(self, on: bool) -> None:
        """Fill pen-less rects inclusive of their right and bottom edge, as the runtime does."""
        page = self._page()
        if page is None:
            return
        if not on:
            for item, rect in self._edge_fills.values():
                item.setRect(rect)
            self._edge_fills = {}
            return
        objs = page.objects
        for i, item in self._scene._items.items():
            if not 0 <= i < len(objs) or not isinstance(item, QtWidgets.QGraphicsRectItem):
                continue
            o = objs[i]
            if o.kind not in (ObjectKind.RECTANGLE, ObjectKind.NUMBER) or o.style is None:
                continue
            st = o.style
            if not st.filled or st.corner_radius or st.three_d:
                continue
            if item.pen().style() != QtCore.Qt.NoPen or item.brush().style() != QtCore.Qt.SolidPattern:
                continue  # (a gradient face is inclusive already)
            old = self._edge_fills.get(i)
            if old is not None and old[0] is item:
                continue
            if not self._runtime_drawn(i):
                continue
            rect = QtCore.QRectF(item.rect())
            self._edge_fills[i] = (item, rect)
            item.setRect(rect.adjusted(0, 0, 1, 1))

    def _runtime_strokes(self, on: bool) -> None:
        """Draw slanted 1 px strokes as GDI does."""
        if not on:
            for item, geom in self._nudged.values():
                _set_geometry(item, geom)
            self._nudged = {}
            return
        for i, top in self._scene._items.items():
            for it in _walk(top):
                key = (i, id(it))
                if key in self._nudged:
                    continue
                if not isinstance(it, _NUDGED_KINDS):
                    continue
                pen = it.pen()
                if pen.style() == QtCore.Qt.NoPen or int(round(pen.widthF() or 1.0)) != 1:
                    continue
                geom = _geometry(it)
                if isinstance(geom, QtGui.QPainterPath) and not _straight(geom):
                    continue
                self._nudged[key] = (it, geom)
                _set_geometry(it, geom.translated(-STROKE_NUDGE, -STROKE_NUDGE))

    def runtime_fill_edges(self) -> Dict[int, object]:
        """The records whose fill the runtime look grew to rect1's edge (tests)."""
        return {i: v[0] for i, v in self._edge_fills.items()}

    def _target_frames(self) -> list:
        """The items drawing TARGET buttons' dashed frames (the children of each such button's
        pick item), on the page and inside resolved pictures (by scene.SIM_CHILD_ROLE)."""
        sc = self._scene
        page = self._page()
        out, ids = [], set()
        if page is None:
            return out
        objs = page.objects

        def target(o) -> bool:
            return o.kind is ObjectKind.BUTTON and o.raw.get("_button_mode") == 3

        for i, item in sc._items.items():
            if not 0 <= i < len(objs):
                continue
            o = objs[i]
            if target(o):
                out.extend(item.childItems())
            stack = list(o.children)
            while stack:
                c = stack.pop()
                if target(c):
                    ids.add(id(c))
                stack.extend(c.children)
        if ids:
            for i, top in sc._items.items():
                if 0 <= i < len(objs) and objs[i].children:
                    for it in _walk(top):
                        if it.data(SIM_CHILD_ROLE) in ids:
                            out.extend(it.childItems())
        return out

    def _trend_design_parts(self) -> list:
        """What only the editor draws of a trend (scene._trend_item: the design-time sine
        traces, the dotted focus rectangle): the runtime draws the pens' samples instead."""
        out = []
        for top in self._scene._items.values():
            if not isinstance(top, QtWidgets.QGraphicsItemGroup):
                continue
            for it in _walk(top):
                if it.data(TREND_PART_ROLE) == "design":
                    out.append(it)
        return out

    def runtime_look(self) -> bool:
        return self._runtime

    def set_flash(self, on: bool) -> int:
        """The global flash phase (PageState.flash_on): a FLASHING bitmap shows its first
        picture in the on phase and its second in the off phase (the bitmap's DIB flag 0x100
        adds the flash DIB). Colours arrive resolved for the phase in the states. Returns the
        flashing pictures on the page."""
        on = bool(on)
        if self._drawn is not None:
            self._drawn.set_flash(on)
        items = self._flashing_items()
        if on == self._flash_on:
            return len(items)
        self._flash_on = on
        show_on = on or self._suspended
        for item, first, second in items:
            item.setPixmap(first if show_on else second)
        for i in sorted(self._flash_colour_records()):
            self._refresh(i)
        for paint, state, label in list(self._ai_paint.values()):
            paint.paint(state, on, label)
        if self._alm is not None:
            self._alm.flash()
        return len(items)

    def _flash_colour_records(self) -> set:
        """The records drawn with a colour that FLASHES of its own (an (on, off) pair of the
        style block that gives the record or one of its resolved children its look, reader
        raw['_flash_slots']; the saver marks them dynamic, dwFlags 0x10): the runtime
        alternates the halves on the flash phase, like a flashing bitmap's two pictures."""
        sc = self._scene
        if self._fc_serial == sc.build_serial:
            return self._fc
        self._fc_serial = sc.build_serial
        self._fc = set()
        page = self._page()
        if page is None:
            return self._fc
        objs = page.objects
        for i in sc._items:
            if not 0 <= i < len(objs):
                continue
            o = objs[i]
            if o.raw.get("_flash_slots") is not None or _child_flashes(o):
                self._fc.add(i)
        return self._fc

    def _flashing_items(self) -> list:
        sc = self._scene
        if self._flash_serial == sc.build_serial:
            return self._flash_items
        self._flash_serial = sc.build_serial
        self._flash_items = []
        page = self._page()
        if page is None:
            return self._flash_items
        objs = page.objects
        kids = {}
        for i, top in sc._items.items():
            if not 0 <= i < len(objs):
                continue
            o = objs[i]
            if (
                isinstance(top, QtWidgets.QGraphicsPixmapItem)
                and o.raw.get("_embedded_bmp_flash") is not None
            ):
                self._add_flashing(top, o)
            if o.children:
                stack = list(o.children)
                while stack:
                    c = stack.pop()
                    if c.raw.get("_embedded_bmp_flash") is not None:
                        kids[id(c)] = c
                    stack.extend(c.children)
        if kids:
            for top in sc._items.values():
                for it in _walk(top):
                    c = kids.get(it.data(SIM_CHILD_ROLE))
                    if c is not None and isinstance(it, QtWidgets.QGraphicsPixmapItem):
                        self._add_flashing(it, c)
        if not self._flash_on:
            for item, _first, second in self._flash_items:
                item.setPixmap(second)
        return self._flash_items

    def _add_flashing(self, item, o: GraphicObject) -> None:
        def pixmap(data):
            w, h, rgba, _alpha = data
            img = QtGui.QImage(bytes(rgba), w, h, w * 4, QtGui.QImage.Format_RGBA8888)
            return QtGui.QPixmap.fromImage(img.copy())

        try:
            first = QtGui.QPixmap(item.pixmap())
            second = pixmap(o.raw["_embedded_bmp_flash"])
        except Exception:  # noqa: BLE001 -- a picture that cannot be read does not flash
            _note_exc("sim_states.SimLayer._add_flashing")
            return
        if second.size() == first.size():
            self._flash_items.append((item, first, second))

    def shows(self, i: int, role: str = "main") -> bool:
        """Whether the layer lets record ``i``'s item show (the scene adds the editor's own
        hidden roots)."""
        look = self._applied.get(i, _DEFAULT)
        if role == "main":
            if self._runtime and self._non_drawable(i):
                return False
            if self._drawn is not None and self._drawn.hides(i):
                return False
            return not look.hidden and look.symbol is None
        if role == "ai" and self._runtime:
            return look.alarm is not None and not look.hidden
        return not look.hidden

    def look_of(self, i: int) -> _Look:
        """The look drawn now for record ``i`` (tests, the Simulate view's tool tips)."""
        return self._applied.get(i, _DEFAULT)

    def pick_area(self, i: int) -> Optional[QtCore.QRectF]:
        """Where record ``i`` takes the pointer now: its extent (l, t, r + 1, b + 1, the rect
        DspGetMouseOver and the runtime's hit test use) moved, turned and
        scaled by its look (the bounding box of the mapped rect); None while it or a group
        above it is hidden. Also for a record that draws nothing (a transparent target)."""
        page = self._page()
        if page is None or not 0 <= i < len(page.objects) or i in self._scene._covered:
            return None
        g = page.objects[i].geometry
        self._ensure_map()
        look = self._look(i)
        if look.hidden:
            return None
        r = QtCore.QRectF(g.x, g.y, g.extent_x + 1, g.extent_y + 1)
        if look.matrix is not None:
            r = _matrix(look.matrix).mapRect(r)
        return r

    def index_of(self, an: int) -> Optional[int]:
        self._ensure_map()
        return self._index_of.get(int(an))

    def alarm_lists(self) -> "_alm.AlarmListPainter":
        """The painter of the page's alarm lists, alarm rows and array outputs."""
        if self._alm is None:
            self._alm = _alm.AlarmListPainter(self)
        return self._alm

    def _symbol_resolver(self):
        """The page's symbol resolver (its include chain), None without a page path."""
        from ..io import reader

        path = getattr(self._page(), "source_path", "") or ""
        if self._resolver is None or self._resolver_path != path:
            self._resolver = reader.build_resolver(path) if path else None
            self._resolver_path = path
        return self._resolver

    def tooltip_at(self, i: int) -> Optional[str]:
        """The animated tool tip of record ``i`` (the innermost in its chain), or None."""
        return self._applied.get(i, _DEFAULT).tooltip

    def editor_hidden_changed(self) -> None:
        for i, it in list(self._swap_active.items()):
            if it is not None:
                it.setVisible(self._visible(i, "swap"))
        for i, it in self._bg_items.items():
            it.setVisible(self._visible(i, "bg") and it.data(QtCore.Qt.UserRole + 7) == 1)

    def forget_items(self, indices=None) -> None:
        """The scene is about to drop the items of ``indices`` (None = all: set_page clears
        the scene): forget their snapshots and remove the items this layer added for them."""
        if indices is None:
            # scene.clear() deletes every item, ours too: only drop the references
            self._snaps.clear()
            self._applied.clear()
            self._swap_cache.clear()
            self._swap_active.clear()
            self._swap_inside.clear()
            self._bg_items.clear()
            self._dither_items.clear()
            self._dither_keys.clear()
            self._faces.clear()
            self._edge_fills.clear()
            self._nudged.clear()
            self._ai_flags.clear()
            self._parts = {}
            self._parts_serial = None
            self._flash_items = []
            self._flash_serial = None
            self._fc_serial = None
            self._map_serial = None
            self._keys_serial = None
            if self._drawn is not None:
                self._drawn.forget()
            self._ai_paint = {}
            self._ai_geom = {}
            self._ai_flag_objs = {}
            if self._alm is not None:
                self._alm.forget(None)
            return
        sc = self._scene
        gone = set(indices)
        for key in [k for k in self._snaps if k[0] in gone]:
            del self._snaps[key]
        for key in [k for k in self._ai_flags if k[0] in gone]:
            del self._ai_flags[key]  # (children of the alarm item the scene drops)
        for i in gone:
            self._ai_paint.pop(i, None)
        for key in [k for k in self._ai_flag_objs if k[0] in gone]:
            del self._ai_flag_objs[key]
        if self._alm is not None:
            self._alm.forget(gone)
        for i in gone:
            self._applied.pop(i, None)
            self._swap_active.pop(i, None)
            self._faces.pop(i, None)  # (a child of the item the scene drops)
            self._edge_fills.pop(i, None)
        for key in [k for k in self._nudged if k[0] in gone]:
            del self._nudged[key]
        for i in gone:
            bg = self._bg_items.pop(i, None)
            if bg is not None:
                sc.removeItem(bg)
            self._dither_keys.pop(i, None)
            dt = self._dither_items.pop(i, None)
            if dt is not None:
                sc.removeItem(dt)
        for key in [k for k in self._swap_cache if k[0] in gone]:
            it = self._swap_cache.pop(key)
            if it is not None:
                self._swap_inside.pop(id(it), None)
                sc.removeItem(it)
        self._parts = {}
        self._flash_items = []
        self._flash_serial = None
        self._fc_serial = None
        self._map_serial = None
        self._keys_serial = None

    def rebind(self) -> None:
        """Draw the states again over the scene's (new) items."""
        if self._page() is None:
            return
        self._map_serial = None
        self._ensure_map()
        todo = set(self._applied)
        if not self._flash_on and not self._suspended:
            todo.update(self._flash_colour_records())  # the new items in the off phase
        for an in self.states:
            idx = self._index_of.get(an)
            if idx is not None:
                todo.update(self._subtree(idx))
        for i in sorted(todo):
            self._refresh(i, force=i not in self._applied)
        if self._runtime:
            self._runtime = False
            self.set_runtime_look(True)
        if not self._flash_on and not self._suspended:
            self._flashing_items()  # the new pictures in the off phase
        if self._drawn is not None:
            self._drawn.rebind()
        if self._alm is not None:
            self._alm.redraw()

    # -- the page ------------------------------------------------------------
    def _page(self):
        return getattr(self._scene, "_page", None)

    def _ensure_map(self) -> None:
        serial = self._scene.build_serial
        if self._map_serial == serial:
            return
        self._map_serial = serial
        self._index_of = {}
        page = self._page()
        if page is None:
            return
        for j, o in enumerate(page.objects):
            an = getattr(o, "an", -1)
            if isinstance(an, int) and an > 0 and an not in self._index_of:
                self._index_of[an] = j

    def _subtree(self, idx: int) -> list:
        page = self._page()
        if page is None or not 0 <= idx < len(page.objects):
            return []
        if not page.objects[idx].members:
            return [idx]
        return page.subtree(idx)

    def _non_drawable(self, i: int) -> bool:
        """What only the editor draws of record ``i``."""
        page = self._page()
        if page is None or not 0 <= i < len(page.objects):
            return False
        raw = page.objects[i].raw
        return bool(raw.get("_non_drawable") or raw.get("_type_code") == 0x1009)

    def _state_at(self, j: int) -> Optional[ObjectState]:
        o = self._page().objects[j]
        an = o.an
        if not isinstance(an, int) or an <= 0 or self._index_of.get(an) != j:
            return None
        return self.states.get(an)

    def _centre(self, j: int) -> Tuple[int, int]:
        """The default pivot / fixed point: the record's BOUNDS centre in C integer division
        (CtDraw ctf.rotation_origin, and ctf._expand's scale anchors, take the
        bounds = rect2, and truncate toward zero: (-5 + 0) / 2 = -2, not -3)."""
        o = self._page().objects[j]
        b = o.raw.get("_rect2")
        try:
            l, t, r, bt = (int(v) for v in b)
            if r < l:
                l, r = r, l
            if bt < t:
                t, bt = bt, t
            if r == l and bt == t:  # no bounds of its own (a 0, 0, 0, 0 marker)
                raise ValueError
        except TypeError, ValueError:
            g = o.geometry
            l, t = int(round(g.x)), int(round(g.y))
            r, bt = l + int(round(g.extent_x)), t + int(round(g.extent_y))
        return int((l + r) / 2), int((t + bt) / 2)

    def _state_matrix(self, j: int, st: ObjectState) -> Optional[QtGui.QTransform]:
        m = None
        if st.scale is not None:
            sx, sy = float(st.scale[0]), float(st.scale[1])
            if (sx, sy) != (1.0, 1.0):
                ox, oy = st.scale_origin if st.scale_origin is not None else self._centre(j)
                m = (
                    QtGui.QTransform.fromTranslate(-ox, -oy)
                    * QtGui.QTransform.fromScale(sx, sy)
                    * QtGui.QTransform.fromTranslate(ox, oy)
                )
        if st.rotate:
            ox, oy = st.rotate_origin if st.rotate_origin is not None else self._centre(j)
            r = (
                QtGui.QTransform.fromTranslate(-ox, -oy)
                * QtGui.QTransform().rotate(float(st.rotate))
                * QtGui.QTransform.fromTranslate(ox, oy)
            )
            m = r if m is None else m * r
        if st.move is not None and (st.move[0] or st.move[1]):
            t = QtGui.QTransform.fromTranslate(float(st.move[0]), float(st.move[1]))
            m = t if m is None else m * t
        return m

    def _chain_look(self, j: Optional[int], top: Optional[int], below: bool) -> "_Chain":
        """What record ``j`` and its ancestors give every record under them."""
        memo = self._chain_memo
        key = (j, top, below)
        if memo is not None:
            hit = memo.get(key)
            if hit is not None:
                return hit
        objs = self._page().objects
        sts = []
        full = []
        guard = 0
        while j is not None and 0 <= j < len(objs) and guard < 64:
            if j == top:
                below = False
            st = self._state_at(j)
            if st is not None:
                full.append((j, st))
                if below:
                    sts.append((j, st))
            j = objs[j].parent
            guard += 1
        hidden = False
        for j, st in sts:
            if not st.shown or (st.disabled and st.disable_style == DISABLE_HIDE):
                hidden = True
            elif st.symbol is not None or getattr(st, "symbol_state", None) is not None:
                hidden = True  # a symbol set's states give way to the shown art
        gray, disabled = None, False
        for j, st in reversed(sts):  # the outermost disabled record decides the look
            if st.disabled:
                gray, disabled = _GRAY.get(st.disable_style), True
                break
        m = None
        for j, st in sts:  # innermost first
            a = self._state_matrix(j, st)
            if a is not None:
                m = a if m is None else m * a
        colour = next((st.fill_colour for _j, st in full if st.fill_colour is not None), None)
        tooltip = next((st.tooltip for _j, st in full if st.tooltip is not None), None)
        reveal = []
        for j, st in sts:
            if st.fill_level is not None and objs[j].kind not in _CLOSED_KINDS:
                g = objs[j].geometry
                reveal.append(
                    (
                        g.x,
                        g.y,
                        g.x + g.extent_x,
                        g.y + g.extent_y,
                        _clamp01(st.fill_level),
                        st.fill_level_direction,
                    )
                )
        out = _Chain(hidden, gray, disabled, m, colour, tooltip, tuple(reveal), bool(full))
        if memo is not None:
            memo[key] = out
        return out

    def _look(self, i: int, top: Optional[int] = None) -> _Look:
        """Record ``i``'s look from its own state and its ancestors'."""
        objs = self._page().objects
        flash = (
            not self._flash_on and not self._suspended and top is None and i in self._flash_colour_records()
        )
        own = self._state_at(i) if 0 <= i < len(objs) else None
        # the ancestors' part is shared by every member of a changed root (_chain_look)
        parent = objs[i].parent if 0 <= i < len(objs) else None
        anc = self._chain_look(parent, top, below=i != top)
        if own is None and not anc.any:
            return _DEFAULT if not flash else _DEFAULT._replace(flash=True)
        below = i != top
        hidden = anc.hidden
        gray = anc.gray
        m_own = None
        reveal = list(anc.reveal)
        if own is not None and below:
            if not own.shown or (own.disabled and own.disable_style == DISABLE_HIDE):
                hidden = True
            if not anc.disabled and own.disabled:
                gray = _GRAY.get(own.disable_style)  # (no outer disabled record decides)
            m_own = self._state_matrix(i, own)
            if own.fill_level is not None and objs[i].kind not in _CLOSED_KINDS:
                g = objs[i].geometry
                reveal.insert(
                    0,
                    (
                        g.x,
                        g.y,
                        g.x + g.extent_x,
                        g.y + g.extent_y,
                        _clamp01(own.fill_level),
                        own.fill_level_direction,
                    ),
                )
        m = m_own if anc.matrix is None else (anc.matrix if m_own is None else m_own * anc.matrix)
        if m is not None and abs(m.determinant()) < _MIN_DET:
            # collapsed (a size factor of 0, the bottom of the default 0 .. 100 % range):
            # nothing to draw. Never give an item a singular transform: Qt crashes painting
            # one under a greyed / reveal effect.
            hidden, m = True, None
        colour = own.fill_colour if own is not None and own.fill_colour is not None else anc.colour
        tooltip = own.tooltip if own is not None and own.tooltip is not None else anc.tooltip
        level = text = vertex = symbol = trend = alarm = indication = None
        if own is not None:
            flag = getattr(own, "indication", None)
            flag = str(flag).lower() if flag is not None else None
            if flag in _DITHERED:
                shown = own.error if own.error is not None else own.text
                if not (
                    objs[i].kind in (ObjectKind.TEXT, ObjectKind.NUMBER) and str(shown or "").startswith("#")
                ):
                    indication = flag
            trend = _trend_look(getattr(own, "trend", None))
            alarm = _alarm_look(getattr(own, "alarm", None))
            if own.fill_level is not None:
                level = (_clamp01(own.fill_level), own.fill_level_direction, own.fill_level_colour)
            text = own.error if own.error is not None else own.text
            if own.vertex_offsets is not None:
                vertex = tuple((float(p[0]), float(p[1])) for p in own.vertex_offsets)
            state = getattr(own, "symbol_state", None)
            symbol = ("#%d" % int(state)) if state is not None else own.symbol
        return _Look(
            hidden,
            gray,
            _mtuple(m),
            colour,
            level,
            tuple(reveal),
            text,
            tooltip,
            vertex,
            symbol,
            trend,
            alarm,
            indication,
            flash,
        )

    def _visible(self, i: int, role: str, look: Optional[_Look] = None) -> bool:
        if i in self._scene._covered:
            return False
        if look is None:
            return self.shows(i, role)
        if role == "main":
            if self._runtime and self._non_drawable(i):
                return False
            if self._drawn is not None and self._drawn.hides(i):
                return False
            return not look.hidden and look.symbol is None
        if role == "ai" and self._runtime:
            # the runtime draws a group's alarm indicator while its equipment has an active
            # alarm (the editor always draws the frame)
            return look.alarm is not None and not look.hidden
        return not look.hidden

    # -- drawing one record ----------------------------------------------------
    def _snap(self, i: int, role, item) -> _Snap:
        key = (i, role)
        s = self._snaps.get(key)
        if s is None or s.item is not item:
            s = _Snap(item)
            self._snaps[key] = s
        return s

    def _refresh(self, i: int, force: bool = False) -> int:
        page = self._page()
        if page is None or not 0 <= i < len(page.objects):
            return 0
        sc = self._scene
        obj = page.objects[i]
        main = sc._items.get(i)
        top = None
        if main is None and obj.raw.get("_in_composite"):
            # a composite genie's member: its item lies in the genie's picture, drawn from the
            # cache element's record (in that element's coordinates)
            top = self._composite_root(i)
            part = self._composite_parts(top).get(i) if top is not None else None
            if part is not None and part[1] is not None:
                obj, main = part[0], part[1]
        look = _DEFAULT if self._suspended else self._look(i, top)
        prev = _DEFAULT if force else self._applied.get(i, _DEFAULT)
        if look == prev and not force:
            return 0
        ai = sc._ai_items.get(i)
        # 1. the swapped art
        old_swap = self._swap_active.get(i)
        new_swap = self._swap_item(i, look.symbol) if look.symbol else None
        if old_swap is not None and old_swap is not new_swap:
            self._reset_extra(i, ("swap", id(old_swap)), old_swap)
            old_swap.setVisible(False)
        if new_swap is None:
            self._swap_active.pop(i, None)
        else:
            self._swap_active[i] = new_swap
        swap_new = new_swap is not None and new_swap is not old_swap
        # 2. the record's own item
        if main is not None:
            s = self._snap(i, "main", main)
            s.outer = top
            text_changed = force or look.text != prev.text
            if text_changed:
                self._apply_text(s, obj, look.text)
            if text_changed or look.matrix != prev.matrix:
                # (a scaled polygon too, when no vertex animation moves its points: its
                # vertices truncated as the runtime's, _mapped)
                self._apply_matrix(
                    s,
                    look.matrix,
                    rebuild=obj.kind in (ObjectKind.RECTANGLE, ObjectKind.ELLIPSE)
                    or (obj.kind is ObjectKind.POLYGON and not look.vertex and s.shape is None),
                )
            if text_changed or (look.gray, look.reveal) != (prev.gray, prev.reveal):
                self._apply_effect(s, look.gray, look.reveal)
            vertex_changed = force or look.vertex != prev.vertex
            if vertex_changed:
                self._apply_vertex(s, obj, look.vertex)
            refill = (
                vertex_changed
                or (look.colour, look.level) != (prev.colour, prev.level)
                or (look.level is not None and look.matrix != prev.matrix)
            )
            reflash = refill or text_changed or look.flash != prev.flash
            if reflash:
                self._restore_flash(s)  # (the fill's snapshot takes the static colours)
            if refill:
                self._apply_fill(i, s, obj, look.colour, look.level)
            if reflash and look.flash and look.colour is None and look.level is None:
                self._apply_flash(s, obj)  # a colour the simulator sends wins
            if force or look.tooltip != prev.tooltip:
                self._apply_tooltip(s, look.tooltip)
            if force or look.trend != prev.trend:
                self._apply_trend(main, obj, look.trend)
            main.setVisible(self._visible(i, "main", look))
        # 3. the group's alarm indicator follows the group
        if ai is not None:
            s = self._snap(i, "ai", ai)
            if force or look.matrix != prev.matrix:
                self._apply_matrix(s, look.matrix)
            if force or look.alarm != prev.alarm:
                self._apply_alarm(i, s, ai, look.alarm)
            ai.setVisible(self._visible(i, "ai", look))
        # 4. the swapped art takes the record's movement, look and tool tip
        if new_swap is not None:
            s = self._snap(i, ("swap", id(new_swap)), new_swap)
            s.outer = self._swap_inside.get(id(new_swap))
            if swap_new or force or look.matrix != prev.matrix:
                self._apply_matrix(s, look.matrix)
            if swap_new or force or (look.gray, look.reveal) != (prev.gray, prev.reveal):
                self._apply_effect(s, look.gray, look.reveal)
            if swap_new or force or look.tooltip != prev.tooltip:
                self._apply_tooltip(s, look.tooltip)
            new_swap.setVisible(self._visible(i, "swap", look))
        # 5. a group's level background (in page coordinates: the page record's box)
        self._apply_bg(i, page.objects[i], look, prev, force)
        # 6. the quality indication's dither over the record
        self._apply_dither(i, page.objects[i], main if top is None else None, look, prev, force)
        if look == _DEFAULT:
            self._applied.pop(i, None)
            for role in ("main", "ai"):
                self._snaps.pop((i, role), None)
        else:
            self._applied[i] = look
        return 1

    def _reset_extra(self, i, role, item) -> None:
        s = self._snaps.pop((i, role), None)
        if s is None:
            return
        self._apply_matrix(s, None)
        self._apply_effect(s, None, ())
        self._apply_tooltip(s, None)

    # -- matrix ----------------------------------------------------------------
    def _static_scene(self, s: _Snap) -> QtGui.QTransform:
        """The item's scene transform without the animation: T X (X read from the item now,
        so a moved / relaid text item is followed)."""
        if s.s_base is None:
            item = s.item
            cur = item.transform()
            if cur == s.t_base:
                sm = item.sceneTransform()
            else:
                inv, ok = cur.inverted()
                if ok:
                    sm = s.t_base * inv * item.sceneTransform()
                else:
                    item.setTransform(s.t_base)
                    sm = item.sceneTransform()
            if s.outer is not None:
                # an item in a composite genie's picture: without the genie's own animation
                la = self._applied.get(s.outer)
                if la is not None and la.matrix is not None:
                    inv, ok = _matrix(la.matrix).inverted()
                    if ok:
                        sm = sm * inv
            s.s_base = sm
        return s.s_base

    # -- composite genies --------------------------------------------------------------
    @staticmethod
    def _draws_from_element(o) -> bool:
        """A record whose picture is its element's records (a composite genie drawn from its
        cache element, a genie or symbol the reader expanded from its library element:
        reader._expand_composite): its members' states go onto that picture's items."""
        return bool(o.children) and (o.kind is ObjectKind.COMPOSITE_GENIE or bool(o.raw.get("_resolved_ref")))

    def _composite_root(self, i: int) -> Optional[int]:
        """The composite genie (or genie drawn from its element) whose picture draws member
        ``i``."""
        page = self._page()
        objs = page.objects
        j, guard = objs[i].parent, 0
        while j is not None and 0 <= j < len(objs) and guard < 64:
            o = objs[j]
            if self._draws_from_element(o):
                return j
            j = o.parent
            guard += 1
        return None

    @staticmethod
    def _by_handle(records: list) -> dict:
        out = {}
        for c in records:
            h = c.raw.get("_handle")
            if (
                isinstance(h, int)
                and h not in out
                and c.raw.get("_type_code") not in (_codes.TC.TOUCH, _codes.TC.KEYBOARD)
            ):
                out[h] = c
        return out

    def _composite_parts(self, r: int) -> dict:
        """{member index: (cache record, item, coordinate item)} of a composite genie."""
        sc = self._scene
        if self._parts_serial != sc.build_serial:
            self._parts = {}
            self._parts_serial = sc.build_serial
        got = self._parts.get(r)
        if got is not None:
            return got
        out: dict = {}
        self._parts[r] = out
        page = self._page()
        root = sc._items.get(r)
        robj = page.objects[r]
        if root is None or not robj.children:
            return out
        by_id = {}
        for it in _walk(root):
            k = it.data(SIM_CHILD_ROLE)
            if isinstance(k, int) and k not in by_id:
                by_id[k] = it
        handles = {id(robj.children): self._by_handle(robj.children)}
        top_map = handles[id(robj.children)]
        owned = set()  # cache records already drawn for a member
        for j in page.subtree(r)[1:]:
            if page.objects[j].raw.get("_type_code") in (_codes.TC.TOUCH, _codes.TC.KEYBOARD):
                # a touch / keyboard record shares its owner's handle (and so its link): it
                # draws nothing, and two records on one item would each snapshot it
                continue
            link = page.objects[j].raw.get("_link_an")
            c = top_map.get(link) if isinstance(link, int) and link >= 0 else None
            if c is None:
                continue
            lst, frame = robj.children, root
            for _depth in range(6):
                if id(c) in by_id:
                    break
                # the nearest ancestor (in its own record list) drawn from its element
                a, anc = c.parent, None
                while a is not None and 0 <= a < len(lst):
                    if lst[a].children:
                        anc = lst[a]
                        break
                    a = lst[a].parent
                if anc is None or id(anc) not in by_id:
                    break
                hm = handles.get(id(anc.children))
                if hm is None:
                    hm = handles[id(anc.children)] = self._by_handle(anc.children)
                nxt = hm.get(c.raw.get("_link_an"))
                if nxt is None:
                    break
                c, lst, frame = nxt, anc.children, by_id[id(anc)]
            if id(c) in owned:
                continue
            owned.add(id(c))
            out[j] = (c, by_id.get(id(c)), frame)
        return out

    def record_under(self, i: int, items) -> int:
        """The page record the user points at."""
        page = self._page()
        if page is None or not 0 <= i < len(page.objects):
            return i
        o = page.objects[i]
        if not self._draws_from_element(o):
            return i
        owners = self._composite_owners(i)
        for it in items:
            r = it.data(SIM_RECORD_ROLE)
            if isinstance(r, int) and 0 <= r < len(page.objects):
                return r
            j = owners.get(it.data(SIM_CHILD_ROLE))
            if j is not None:
                return j
        return i

    def _composite_owners(self, r: int) -> dict:
        """{id(cache record): the member index drawing it} of composite genie ``r``."""
        key = ("owners", r)
        got = self._parts.get(key) if self._parts_serial == self._scene.build_serial else None
        if got is None:
            got = {id(c): j for j, (c, _it, _frame) in self._composite_parts(r).items()}
            self._parts[key] = got
        return got

    def _part(self, j: int):
        """(cache record, item or None, frame item) of composite member ``j``, or None."""
        top = self._composite_root(j)
        if top is None:
            return None
        return self._composite_parts(top).get(j)

    def _has_item(self, j: int) -> bool:
        sc = self._scene
        if j in sc._items or j in sc._ai_items:
            return True
        page = self._page()
        if not page.objects[j].raw.get("_in_composite"):
            return False
        got = self._part(j)
        return got is not None and got[1] is not None

    def _apply_matrix(self, s: _Snap, mt: Optional[tuple], rebuild: bool = False) -> None:
        """Draw an item through matrix ``mt`` after its static mapping."""
        item = s.item
        if s.geom is not None:
            for it, geom in s.geom:
                _set_geometry(it, geom)
            s.geom = None
        if mt is not None and rebuild and self._rebuild_geometry(s, _matrix(mt)):
            return
        if mt is None:
            if item.transform() != s.t_base:
                item.setTransform(s.t_base)
            return
        sm = self._static_scene(s)
        inv, ok = sm.inverted()
        a = _matrix(mt)
        item.setTransform((sm * a * inv * s.t_base) if ok else (s.t_base * a))

    def _rebuild_geometry(self, s: _Snap, a: QtGui.QTransform) -> bool:
        """Map the item's shapes through ``a`` (page coordinates) in their own coordinates;
        False (nothing changed) unless ``a`` is a pure scale + move and every item of the
        record is a rect / ellipse / path / line item under axis-aligned transforms."""
        if abs(a.m12()) > 1e-9 or abs(a.m21()) > 1e-9:
            return False
        if abs(a.m11() - 1.0) < 1e-9 and abs(a.m22() - 1.0) < 1e-9:
            return False  # a move: the transform keeps the pen anyway
        item = s.item
        items = _walk(item)
        kinds = (
            QtWidgets.QGraphicsRectItem,
            QtWidgets.QGraphicsEllipseItem,
            QtWidgets.QGraphicsPathItem,
            QtWidgets.QGraphicsLineItem,
            QtWidgets.QGraphicsPolygonItem,
        )
        if not all(isinstance(it, kinds) for it in items) or s.shape is not None:
            return False
        if item.transform() != s.t_base:
            item.setTransform(s.t_base)
            s.s_base = None
        sm = self._static_scene(s)
        parts = []
        for it in items:
            if it is item:
                rel = QtGui.QTransform()
            else:
                rel, ok = it.itemTransform(item)
                if not ok:
                    return False
            st = rel * sm
            if abs(st.m12()) > 1e-9 or abs(st.m21()) > 1e-9:
                return False
            inv, ok = st.inverted()
            if not ok:
                return False
            parts.append((it, st * a * inv))
        s.geom = []
        for it, m in parts:
            geom = _geometry(it)
            s.geom.append((it, geom))
            _set_geometry(it, _mapped(it, geom, m))
        return True

    # -- disabled look / reveal ----------------------------------------------------
    def _apply_effect(self, s: _Snap, gray: Optional[str], reveal: tuple) -> None:
        item = s.item
        if (gray is None and not reveal) or _is_anchor(item):
            if s.effect:
                item.setGraphicsEffect(None)
                s.effect = False
            return
        clip = None
        if reveal:
            region = None
            for l, t, r, b, f, d in reveal:
                rect = _level_rect(QtCore.QRectF(QtCore.QPointF(l, t), QtCore.QPointF(r, b)), f, d)
                p = QtGui.QPainterPath()
                p.addRect(rect)
                region = p if region is None else region.intersected(p)
            inv, ok = self._static_scene(s).inverted()
            clip = inv.map(region) if ok else region
        item.setGraphicsEffect(_StateEffect(gray, clip))
        s.effect = True

    # -- text --------------------------------------------------------------------
    @staticmethod
    def _text_parts(obj: GraphicObject, item):
        """(main text item, its 3D copies, mode) of a text / number record's item."""
        if obj.kind is ObjectKind.TEXT:
            if isinstance(item, QtWidgets.QGraphicsSimpleTextItem):
                return item, [], "top"
            texts = [c for c in item.childItems() if isinstance(c, QtWidgets.QGraphicsSimpleTextItem)]
            if texts:
                return texts[-1], texts[:-1], "group"
        elif obj.kind is ObjectKind.NUMBER:
            texts = [c for c in item.childItems() if isinstance(c, QtWidgets.QGraphicsSimpleTextItem)]
            if texts:
                return texts[-1], [], "label"
        return None, [], None

    def _apply_text(self, s: _Snap, obj: GraphicObject, text: Optional[str]) -> None:
        main, ghosts, mode = self._text_parts(obj, s.item)
        if main is None:
            return
        if s.texts is None:
            if text is None:
                return
            s.texts = [
                (
                    t,
                    t.text(),
                    QtCore.QPointF(t.pos()),
                    t.rotation(),
                    QtCore.QPointF(t.transformOriginPoint()),
                    QtGui.QTransform(t.transform()),
                )
                for t in [main] + ghosts
            ]
        # back to the static text first
        for t, txt, pos, rot, origin, tr in s.texts:
            t.setText(txt)
            t.setRotation(rot)
            t.setTransformOriginPoint(origin)
            t.setPos(pos)
            if t is not s.item:
                t.setTransform(tr)
        if mode == "top":
            s.t_base = QtGui.QTransform(s.t_static)
            s.s_base = None
            s.item.setTransform(s.t_base)  # the matrix is applied again after this
        if text is None:
            s.texts = None
            return
        from .scene import PageScene

        g = obj.geometry
        x, y, ex, ey = g.x, g.y, g.extent_x, g.extent_y
        text = str(text)
        if mode == "label":
            main.setText(text)
            br = main.boundingRect()
            main.setPos(x + max(0, ex - br.width() - 2), y + max(0, (ey - br.height()) / 2))
            return
        old = QtCore.QPointF(main.pos())
        main.setText(text)
        turned = PageScene._layout_text(main, obj, x, y, ex, ey)
        w = main.boundingRect().width()
        if not turned and w > ex:
            # wider than the record's box: the runtime lays a text out from its ANCHOR by the
            # justify (textmetrics: C = x - cx / 2, R = x - cx), which is the box's centre /
            # right edge -- so a wider value grows to the left / both ways, not to the right
            # (the static layout puts a too-wide text at the box's left edge)
            if obj.style.justify == TextJustify.RIGHT:
                main.setX(x + ex - w)
            elif obj.style.justify == TextJustify.CENTRE:
                main.setX(x + (ex - w) / 2.0)
        if mode == "top":
            s.t_base = QtGui.QTransform(main.transform())
            s.s_base = None
        d = main.pos() - old
        for t in ghosts:
            t.setText(text)
            t.setPos(t.pos() + d)

    # -- vertices ----------------------------------------------------------------
    def _apply_vertex(self, s: _Snap, obj: GraphicObject, offsets: Optional[tuple]) -> None:
        item = s.item
        if isinstance(item, QtWidgets.QGraphicsPolygonItem):
            if s.shape is None:
                if offsets is None:
                    return
                s.shape = QtGui.QPolygonF(item.polygon())
            base = s.shape
            if offsets is None:
                item.setPolygon(base)
                s.shape = None
                return
            pts = [
                QtCore.QPointF(
                    base[k].x() + (offsets[k][0] if k < len(offsets) else 0),
                    base[k].y() + (offsets[k][1] if k < len(offsets) else 0),
                )
                for k in range(base.count())
            ]
            item.setPolygon(QtGui.QPolygonF(pts))
        elif isinstance(item, QtWidgets.QGraphicsPathItem) and obj.kind in (
            ObjectKind.POLYGON,
            ObjectKind.LINE,
        ):
            if s.shape is None:
                if offsets is None:
                    return
                s.shape = QtGui.QPainterPath(item.path())
            base = s.shape
            if offsets is None:
                item.setPath(base)
                s.shape = None
                return
            out = QtGui.QPainterPath()
            for k in range(base.elementCount()):
                e = base.elementAt(k)
                dx, dy = offsets[k] if k < len(offsets) else (0.0, 0.0)
                if e.isMoveTo():
                    out.moveTo(e.x + dx, e.y + dy)
                elif e.isLineTo():
                    out.lineTo(e.x + dx, e.y + dy)
                else:
                    return  # a curve: not a vertex list
            item.setPath(out)

    # -- colour and fill level ----------------------------------------------------
    @staticmethod
    def _channel(obj: GraphicObject, item) -> Optional[str]:
        k = obj.kind
        if k is ObjectKind.LINE:
            return "pen"
        if k is ObjectKind.PIPE:
            return "pipe"
        if k in _CLOSED_KINDS and isinstance(item, QtWidgets.QAbstractGraphicsShapeItem):
            return "fill" if (obj.style.filled or item.brush().style() != QtCore.Qt.NoBrush) else "pen"
        if k is ObjectKind.TEXT:
            return "text"
        if k is ObjectKind.BUTTON:
            return "face"
        return None

    def _restore_flash(self, s: _Snap) -> None:
        if s.flashed is None:
            return
        for it, pen, brush, tc in s.flashed:
            if pen is not None:
                it.setPen(pen)
            if brush is not None:
                it.setBrush(brush)
            if tc is not None:
                it.setDefaultTextColor(tc)
        s.flashed = None

    def _apply_flash(self, s: _Snap, obj: GraphicObject) -> None:
        """The off flash phase: every pen, brush and text colour of the record's items that is
        one half of a flashing pair of the style block it was drawn from takes the OTHER half
        (so it alternates whichever half the editor drew); a pipe's shading is drawn again
        from the other halves of its highlight / lowlight."""
        saved = []
        own = obj.raw.get("_flash_slots")
        kids = {}
        stack = list(obj.children)
        while stack:
            c = stack.pop()
            if c.raw.get("_flash_slots") is not None:
                kids[id(c)] = c
            stack.extend(c.children)
        piped = set()
        if obj.kind is ObjectKind.PIPE and own is not None:
            piped = self._flash_pipe(s, obj, own, saved)
        for it in _walk(s.item):
            if id(it) in piped:
                continue
            r = it.data(SIM_CHILD_ROLE)
            slots = own if r is None else (kids[r].raw["_flash_slots"] if r in kids else None)
            if slots is None:
                continue
            if isinstance(it, QtWidgets.QAbstractGraphicsShapeItem):
                pen = brush = None
                p = it.pen()
                if p.style() != QtCore.Qt.NoPen:
                    q = _other_half(p.color(), slots)
                    if q is not None:
                        pen = QtGui.QPen(p)
                        np = QtGui.QPen(p)
                        np.setColor(q)
                        it.setPen(np)
                b = it.brush()
                if b.style() == QtCore.Qt.SolidPattern:
                    q = _other_half(b.color(), slots)
                    if q is not None:
                        brush = QtGui.QBrush(b)
                        it.setBrush(QtGui.QBrush(q))
                if pen is not None or brush is not None:
                    saved.append((it, pen, brush, None))
            elif isinstance(it, QtWidgets.QGraphicsTextItem):
                q = _other_half(it.defaultTextColor(), slots)
                if q is not None:
                    saved.append((it, None, None, QtGui.QColor(it.defaultTextColor())))
                    it.setDefaultTextColor(q)
        s.flashed = saved

    def _flash_pipe(self, s: _Snap, obj: GraphicObject, slots, saved: list) -> set:
        """A pipe whose highlight / lowlight flash: its strokes again from the other halves."""
        from .pipes import pipe_strokes
        from ..format.geometry import SLOT_HILIGHT, SLOT_LOLIGHT

        (h1, h2), (l1, l2) = slots[SLOT_HILIGHT], slots[SLOT_LOLIGHT]
        if h1 == h2 and l1 == l2:
            return set()
        st = obj.style
        drawn_hi = st.hilight_colour if st is not None else None
        on_now = drawn_hi is None or (int(drawn_hi) & 0xFFFFFF) == (h1 & 0xFFFFFF)
        hi, lo = (h2, l2) if on_now else (h1, l1)
        kids = [c for c in s.item.childItems() if isinstance(c, QtWidgets.QGraphicsPathItem)]
        g = obj.geometry
        if not kids or g is None or _is_transparent(hi):
            return set()
        nodes = g.nodes or [(g.x, g.y), (g.x + g.extent_x, g.y + g.extent_y)]
        strokes = list(
            pipe_strokes(
                [(int(round(px)), int(round(py))) for px, py in nodes],
                st.line_width or 1,
                hi & 0xFFFFFF,
                lo & 0xFFFFFF,
            )
        )
        if len(strokes) != len(kids):
            return set()
        for k, (_p, pen) in zip(kids, strokes):
            saved.append((k, QtGui.QPen(k.pen()), None, None))
            k.setPen(pen)
        return {id(k) for k in kids}

    def _restore_fill(self, s: _Snap) -> None:
        for it in s.extras:  # a button's coloured face: hidden, kept (_faces)
            it.setVisible(False)
        s.extras = []
        if s.fill is not None:
            pen, brush = s.fill
            s.item.setPen(pen)
            s.item.setBrush(brush)
            s.fill = None
        if s.text_brush is not None:
            t, brush = s.text_brush
            t.setBrush(brush)
            s.text_brush = None
        if s.pipe_pens is not None:
            for k, pen in s.pipe_pens:
                k.setPen(pen)
            s.pipe_pens = None

    def _apply_fill(self, i: int, s: _Snap, obj: GraphicObject, colour, level) -> None:
        self._restore_fill(s)
        if colour is None and level is None:
            return
        item = s.item
        ch = self._channel(obj, item)
        if ch is None:
            return
        qc = _qcolour(colour)
        if ch in ("fill", "pen"):
            s.fill = (QtGui.QPen(item.pen()), QtGui.QBrush(item.brush()))
            if colour is not None:
                if ch == "fill":
                    item.setBrush(QtGui.QBrush(qc) if qc is not None else QtGui.QBrush(QtCore.Qt.NoBrush))
                else:
                    pen = QtGui.QPen(item.pen())
                    if qc is None:
                        pen = QtGui.QPen(QtCore.Qt.NoPen)
                    else:
                        pen.setColor(qc)
                    item.setPen(pen)
            if level is not None and obj.kind in _CLOSED_KINDS:
                self._add_level(i, s, obj, ch, colour, level)
        elif ch == "text" and colour is not None:
            main, _g, _m = self._text_parts(obj, item)
            if main is not None:
                s.text_brush = (main, QtGui.QBrush(main.brush()))
                main.setBrush(QtGui.QBrush(qc if qc is not None else QtGui.QColor(0, 0, 0, 0)))
        elif ch == "pipe" and colour is not None:
            self._recolour_pipe(s, obj, colour)
        elif ch == "face" and colour is not None and qc is not None:
            self._button_face(i, s, obj, qc)

    def _add_level(self, i: int, s: _Snap, obj, ch: str, colour, level) -> None:
        """Fill level as a gradient with a hard stop."""
        item = s.item
        path = _shape_path(item)
        if path is None:
            return
        fraction, direction, bg = level
        if ch == "fill":
            fill = _brush_colour(item.brush())
        else:
            fill = (
                _qcolour(colour)
                if colour is not None
                else (
                    _qcolour(obj.style.fill_colour)
                    if obj.style.fill_colour is not None
                    else QtGui.QColor(item.pen().color())
                )
            )
        none = QtGui.QColor(0, 0, 0, 0)
        fill = fill if fill is not None else none
        rest = _qcolour(bg)
        rest = rest if rest is not None else none
        if fraction >= 1.0:
            item.setBrush(QtGui.QBrush(fill) if fill.alpha() else QtGui.QBrush(QtCore.Qt.NoBrush))
            return
        if fraction <= 0.0:
            item.setBrush(QtGui.QBrush(rest) if rest.alpha() else QtGui.QBrush(QtCore.Qt.NoBrush))
            return
        r = path.boundingRect()
        start, stop = {
            FILL_DOWN: (r.topLeft(), r.bottomLeft()),
            FILL_LEFT: (r.topRight(), r.topLeft()),
            FILL_RIGHT: (r.topLeft(), r.topRight()),
        }.get(direction, (r.bottomLeft(), r.topLeft()))  # FILL_UP: from the bottom
        g = QtGui.QLinearGradient(start, stop)
        g.setStops([(0.0, fill), (fraction, fill), (min(1.0, fraction + 1e-6), rest), (1.0, rest)])
        item.setBrush(QtGui.QBrush(g))

    def _recolour_pipe(self, s: _Snap, obj: GraphicObject, colour) -> None:
        from .pipes import pipe_strokes

        item = s.item
        kids = [c for c in item.childItems() if isinstance(c, QtWidgets.QGraphicsPathItem)]
        if not kids:
            return
        s.pipe_pens = [(k, QtGui.QPen(k.pen())) for k in kids]
        if _is_transparent(colour):
            for k in kids:
                k.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            return
        g = obj.geometry
        nodes = g.nodes or [(g.x, g.y), (g.x + g.extent_x, g.y + g.extent_y)]
        hi = int(colour) & 0xFFFFFF
        lo = ((((hi >> 16) & 0xFF) >> 1) << 16) | ((((hi >> 8) & 0xFF) >> 1) << 8) | ((hi & 0xFF) >> 1)
        strokes = list(
            pipe_strokes(
                [(int(round(px)), int(round(py))) for px, py in nodes], obj.style.line_width or 1, hi, lo
            )
        )
        if len(strokes) != len(kids):
            return
        for k, (_p, pen) in zip(kids, strokes):
            k.setPen(pen)

    def _button_face(self, i: int, s: _Snap, obj: GraphicObject, qc: QtGui.QColor) -> None:
        """A button's face in the colour: the NORMAL / XP_STYLE face inset by 2 / 4 (the
        scene's own-face rule). The BORDER_3D / BORDER / TARGET buttons have a transparent face
        (help 1172135): nothing to colour. The face is ONE child per button, made once and
        shown / hidden (adding and removing children under a greyed group crashes Qt)."""
        item = s.item
        mode = obj.raw.get("_button_mode", 0)
        if not isinstance(item, QtWidgets.QGraphicsRectItem) or mode not in (0, 4):
            return
        inset = 4 if mode == 4 else 2
        r = item.rect().adjusted(inset, inset, -inset, -inset)
        if r.width() <= 0 or r.height() <= 0:
            return
        got = self._faces.get(i)
        face = got[1] if got is not None and got[0] is item else None
        if face is None:
            face = QtWidgets.QGraphicsRectItem(r, item)
            face.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            face.setData(0, None)
            face.setData(PAGE_INDEX_ROLE, i)
            cap = next(
                (
                    c
                    for c in item.childItems()
                    if c is not face
                    and isinstance(c, (QtWidgets.QGraphicsSimpleTextItem, QtWidgets.QGraphicsTextItem))
                ),
                None,
            )
            if cap is not None:
                face.stackBefore(cap)
            self._faces[i] = (item, face)
        face.setRect(r)
        face.setBrush(QtGui.QBrush(qc))
        face.setVisible(True)
        s.extras.append(face)

    # -- tool tip ------------------------------------------------------------------
    @staticmethod
    def _apply_tooltip(s: _Snap, tip: Optional[str]) -> None:
        if s.tips is None:
            if tip is None:
                return
            s.tips = [(it, it.toolTip()) for it in _walk(s.item)]
        for it, base in s.tips:
            it.setToolTip(base if tip is None else str(tip))
        if tip is None:
            s.tips = None

    # -- trend pens ----------------------------------------------------------------
    @staticmethod
    def _apply_trend(item, obj: GraphicObject, trend: Optional[tuple]) -> None:
        """Draw the engine's samples on a trend's pen lines."""
        box, lines = None, []
        for it in _walk(item):
            role = it.data(TREND_PART_ROLE)
            if role == "box":
                box = it
            elif role == "pen":
                lines.append(it)
        if box is None or not lines:
            return
        r = box.rect()
        left, top = r.left(), r.top()
        right, bottom = r.right() - 1.0, r.bottom() - 1.0  # the box is one pixel wider / taller
        info = obj.raw.get("_trend") or {}
        try:
            sp = max(1, int(info.get("spacing") or 1))
        except TypeError, ValueError:
            sp = 1
        own = info.get("pens") or []
        for k, line in enumerate(lines):
            p = trend[k] if trend is not None and k < len(trend) else None
            if p is None:
                line.setVisible(False)
                line.setPath(QtGui.QPainterPath())
                continue
            num, colour, lo, hi, vals = p
            if colour is None and 0 < num <= len(own):
                try:
                    colour = int(own[num - 1][1])
                except TypeError, ValueError, IndexError:
                    colour = None
            qc = _qcolour(colour) if colour is not None else QtGui.QColor(0, 0, 0)
            if qc is None:  # a 'no colour' pen draws nothing
                line.setVisible(False)
                line.setPath(QtGui.QPainterPath())
                continue
            path = QtGui.QPainterPath()
            n, drawing = len(vals), False
            for j, v in enumerate(vals):
                x = right - (n - 1 - j) * sp
                if v is None or x < left:
                    drawing = False
                    continue
                y = bottom - (v - lo) / (hi - lo) * (bottom - top)
                y = max(top, min(bottom, y))
                pt = QtCore.QPointF(x + 0.5, y + 0.5)
                if drawing:
                    path.lineTo(pt)
                else:
                    path.moveTo(pt)
                    drawing = True
            pen = QtGui.QPen(qc)
            pen.setWidth(0)
            line.setPen(pen)
            line.setPath(path)
            line.setVisible(True)

    # -- alarm indicator -------------------------------------------------------------
    def _apply_alarm(self, i: int, s: _Snap, ai, alarm: Optional[tuple]) -> None:
        """Draw an active alarm's indicator."""
        strips = [it for it in ai.childItems() if it.data(AI_PART_ROLE) == "strip"]
        if s.fill is None:
            originals = [it for it in ai.childItems() if it.data(AI_PART_ROLE) == "flag"]
            s.fill = ([(it, QtGui.QBrush(it.brush())) for it in strips], originals)
        brushes, originals = s.fill
        added = {k: v for k, v in self._ai_flags.items() if k[0] == i}
        self._size_alarm_frame(i, ai, alarm is not None)
        if alarm is None:
            for it, brush in brushes:
                it.setBrush(brush)
            for it in originals:
                it.setVisible(True)
            for it in added.values():
                if it is not None:
                    it.setVisible(False)
            painted = self._ai_paint.pop(i, None)
            if painted is not None:
                painted[0].restore()
            return
        pr, _state, colour, _flash = alarm
        rows = self._priorities()
        row = next((r for r in rows if r[0] == pr), None)
        qc = _qcolour(colour) if colour is not None else None
        if qc is None and row is not None and row[1] is not None:
            qc = QtGui.QColor(*row[1])
        for it, brush in brushes:
            it.setBrush(QtGui.QBrush(qc) if qc is not None else brush)
        top = rows[0][0] if rows else None
        # the editor's flag is the top priority's of the page's OWN project (CtDraw's); the
        # runtime's rows take the include chain too, so it stands in only when it is the
        # same flag genie
        own_flag = top is None or (pr == top and (row is None or row[2] == self._editor_flag_ref()))
        for it in originals:
            it.setVisible(own_flag)
        flag = None
        if not own_flag:
            key = (i, pr)
            if key not in self._ai_flags:
                self._ai_flags[key] = self._build_flag(i, ai, row[2] if row is not None else None, pr)
            flag = self._ai_flags[key]
        for k, it in added.items():
            if it is not None and it is not flag:
                it.setVisible(False)
        if flag is not None:
            flag.setVisible(True)
        self._paint_flag(i, ai, pr, _state, flag, originals if own_flag else ())

    def _size_alarm_frame(self, i: int, ai, active: bool) -> None:
        """The runtime draws an active indicator round the group's CURRENT content: each
        member's Bounds moved / grown as its drawn item moved / grew (a wider value widens
        the frame). Inactive: the editor's frame."""
        page = self._page()
        sc = self._scene
        data = page.objects[i].raw.get("_alarm_indicator") if page is not None else None
        static = sc._ai_static.get(i) or {}
        saved = self._ai_geom.get(i)
        if saved is None:
            saved = self._ai_geom[i] = [
                (
                    it,
                    _geometry(it)
                    if isinstance(
                        it,
                        (
                            QtWidgets.QGraphicsRectItem,
                            QtWidgets.QGraphicsPathItem,
                            QtWidgets.QGraphicsLineItem,
                        ),
                    )
                    else None,
                    QtCore.QPointF(it.pos()),
                )
                for it in _walk(ai)
                if it is not ai
            ]
        U = None
        if active and data and data.get("members"):
            from ..format import alarmind

            rects = []
            for j, r2 in data["members"]:
                it = sc._items.get(j)
                st = static.get(j)
                if it is None or st is None:
                    rects.append(r2)
                    continue
                # a value text: the runtime frames the string it draws now, measured as GDI
                # lays it out from its anchor (the design mask '####.#' draws wider than both
                # its stored Bounds and the value)
                box = _value_box(page.objects[j], it) if j < len(page.objects) else None
                if box is not None:
                    rects.append(box)
                    continue
                cur = it.sceneBoundingRect()
                rects.append(
                    (
                        r2[0] + round(cur.left() - st.left()),
                        r2[1] + round(cur.top() - st.top()),
                        r2[2] + round(cur.right() - st.right()),
                        r2[3] + round(cur.bottom() - st.bottom()),
                    )
                )
            U = alarmind.union(rects)
            base = alarmind.union([r for _j, r in data["members"]])
            if U == base:
                U = None
        for it, geom, pos in saved:  # the editor's frame first
            if geom is not None:
                _set_geometry(it, geom)
            it.setPos(pos)
        if U is None:
            return
        from ..format import alarmind

        outer, inner, _strip = alarmind.frame(U, data["settings"])
        o0, in0 = data["outer"], data["inner"]
        strips = [it for it in ai.childItems() if it.data(AI_PART_ROLE) == "strip"]
        l, t, r, b = outer
        s = data["strip"]
        for it, (x, y, w, h) in zip(
            strips, ((l, t, s, b - t), (l, t, r - l, s), (r - s, t, s, b - t), (l, b - s, r - l, s))
        ):
            if isinstance(it, QtWidgets.QGraphicsRectItem):
                it.setRect(QtCore.QRectF(x, y, max(w, 0), max(h, 0)))
        lines = [
            it
            for it in ai.childItems()
            if it.data(AI_PART_ROLE) is None and isinstance(it, QtWidgets.QGraphicsRectItem)
        ]
        for it, (rl, rt, rr, rb) in zip(lines, (inner, outer)):
            x0, x1 = min(rl, rr), max(rl, rr)
            y0, y1 = min(rt, rb), max(rt, rb)
            it.setRect(QtCore.QRectF(x0 + 0.5, y0 + 0.5, max(x1 - x0 - 1, 0), max(y1 - y0 - 1, 0)))
        # the flag keeps its place against the frame's edge it sits on
        if data.get("flag_on"):
            loc = int(data.get("flag_loc") or 0)
            dx = (
                (outer[0] - o0[0])
                if loc in (0, 1, 2)
                else (outer[2] - o0[2])
                if loc in (5, 6, 7)
                else ((outer[0] + outer[2]) - (o0[0] + o0[2])) / 2.0
            )
            dy = (
                (outer[1] - o0[1])
                if loc == 3
                else (outer[3] - o0[3])
                if loc == 4
                else ((outer[1] + outer[3]) - (o0[1] + o0[3])) / 2.0
            )
            for it in ai.childItems():
                if it.data(AI_PART_ROLE) == "flag":
                    it.setPos(it.pos() + QtCore.QPointF(dx, dy))
        del in0

    def _paint_flag(self, i: int, ai, pr: int, state, flag, originals) -> None:
        """The shown flag genie in the alarm's state: its 'Label' text = the priority's
        SHORTNAME (almprior.DBF), its COL_ARR records in their pair for the state index (the
        runtime indicator sets the flag's Label / State metadata, sa_priorities.ctm), repainted
        on every flash phase (set_flash)."""
        from ..format import alarmind

        path = getattr(self._page(), "source_path", "") or ""
        try:
            info = alarmind.priority_details(path).get(pr) if path else None
        except Exception:  # noqa: BLE001 -- the flag keeps its own look
            _note_exc("sim_states.SimLayer._paint_flag")
            info = None
        label = (info or {}).get("short") or None
        old = self._ai_paint.get(i)
        target = flag if flag is not None else (originals[0] if originals else None)
        if target is None:
            if old is not None:
                old[0].restore()
                self._ai_paint.pop(i, None)
            return
        if old is not None and old[0].item is not target:
            old[0].restore()
            old = None
        if old is None:
            fobj = (
                self._ai_flag_objs.get((i, pr))
                if flag is not None
                else ((self._page().objects[i].raw.get("_alarm_indicator") or {}).get("flag"))
            )
            if not isinstance(fobj, GraphicObject):
                return
            paint = _alm.SymbolPaint(target, fobj)
        else:
            paint = old[0]
        idx = _alm.state_index(state)
        self._ai_paint[i] = (paint, idx, label)
        paint.paint(idx, self._flash_on, label)

    def _editor_flag_ref(self):
        """The (library, element) of the flag the editor draws (the top priority's of the
        page's own project: reader._ai_priority), or None."""
        from ..format import alarmind

        path = getattr(self._page(), "source_path", "") or ""
        try:
            return alarmind.top_priority(path, own=True)[1] if path else None
        except Exception:  # noqa: BLE001 -- the runtime's flag is built instead
            _note_exc("sim_states.SimLayer._editor_flag_ref")
            return None

    def _priorities(self) -> list:
        from ..format import alarmind

        path = getattr(self._page(), "source_path", "") or ""
        try:
            return alarmind.priorities(path) if path else []
        except Exception:  # noqa: BLE001 -- the editor's frame colour then stays
            _note_exc("sim_states.SimLayer._priorities")
            return []

    def _build_flag(self, i: int, ai, ref, ref_priority: int = 0):
        """The flag genie ``ref`` (library, element) of an alarm indicator, placed like the
        editor's flag, as a child of the indicator item ai; None when the
        indicator shows no flag."""
        from ..io import reader

        obj = self._page().objects[i]
        d = obj.raw.get("_alarm_indicator") or {}
        if not d.get("flag_on", True) or "outer" not in d:
            return None
        path = getattr(self._page(), "source_path", "") or ""
        if self._resolver is None or self._resolver_path != path:
            self._resolver = reader.build_resolver(path) if path else None
            self._resolver_path = path
        try:
            flag = reader._ai_flag_object(
                path,
                self._resolver,
                int(d.get("flag_loc", 0)),
                d["outer"],
                int(d.get("width", 3)),
                ref=ref,
                strict=False,
            )
        except Exception:  # noqa: BLE001
            _note_exc("sim_states.SimLayer._build_flag")
            flag = None
        if isinstance(flag, tuple) and flag and flag[0] == "default":
            return self._scene.default_flag_item(flag[1], flag[2], ai)
        if flag is not None and getattr(flag, "children", None):
            item = self._scene._make_symbol_item(flag)
            if item is not None:
                item.setParentItem(ai)
                self._ai_flag_objs[(i, ref_priority)] = flag
                return item
        return None

    # -- symbol swap -----------------------------------------------------------------
    def _swap_item(self, i: int, symbol: str):
        key = (i, _ref_core(symbol))
        if key in self._swap_cache:
            return self._swap_cache[key]
        item, inside = None, None
        try:
            item, inside = self._build_swap(i, symbol)
        except Exception:  # noqa: BLE001 -- a symbol that cannot be drawn draws nothing
            _note_exc("sim_states.SimLayer._swap_item")
            item = None
        self._swap_cache[key] = item
        if item is None:
            if not symbol.startswith("#"):
                self.missing_symbols.add(symbol)
            return None
        sc = self._scene
        _stamp(item, i)
        item.setVisible(False)
        if inside is not None:
            # a composite genie's state art is in its cache record's coordinates: drawn in
            # the genie's picture (setParentItem keeps those coordinates)
            top = self._composite_root(i)
            self._swap_inside[id(item)] = top
            item.setParentItem(inside)
            item.setData(0, None)
            for it in _walk(item):
                it.setData(PAGE_INDEX_ROLE, top)
                it.setData(SIM_RECORD_ROLE, i)
        else:
            sc.addItem(item)
            self._stack_after_unit(i, item)
        return item

    def _state_art(self, c: GraphicObject, objs=None):
        """A symbol-set state child's art as a new item (None: no art, a DummyMarker). A state
        that is a group-root symbol's instance forest (a 0x08 root over the element's members,
        CtDraw's form) draws its members (``objs`` = the list its ``members`` index)."""
        sc = self._scene
        if c.children:
            return sc._make_symbol_item(c)
        if c.raw.get("_embedded_bmp") is not None:
            return sc._make_item(c)
        if c.members and objs is not None:
            group = QtWidgets.QGraphicsItemGroup()
            todo = list(c.members)
            while todo:
                j = todo.pop(0)
                if not 0 <= j < len(objs):
                    continue
                m = objs[j]
                if m.raw.get("_non_drawable"):
                    continue
                if m.members and not m.children:
                    todo[0:0] = list(m.members)
                    continue
                it = sc._make_symbol_item(m) if m.children else sc._make_item(m)
                if it is not None:
                    group.addToGroup(it)
            return group if group.childItems() else None
        return None

    def _member_art(self, m: int):
        """(art item, the picture item to draw it in or None) of the state child ``m``: its
        own art, else (a composite genie's member) its cache record's, in that record's
        coordinates."""
        objs = self._page().objects
        art = self._state_art(objs[m], objs)
        if art is not None or not objs[m].raw.get("_in_composite"):
            return art, None
        part = self._part(m)
        if part is None:
            return None, None
        art = self._state_art(part[0])
        return (art, part[2]) if art is not None else (None, None)

    def _build_swap(self, i: int, symbol: str):
        """(the art item for ``symbol``, the composite genie it draws in or None)."""
        page = self._page()
        obj = page.objects[i]
        objs = page.objects
        if symbol.startswith("#"):
            # a state INDEX: the set's direct children in order, input records not counted
            # (format/symbolset.py)
            states = [
                m
                for m in obj.members
                if 0 <= m < len(objs)
                and objs[m].raw.get("_type_code") not in (_codes.TC.TOUCH, _codes.TC.KEYBOARD)
            ]
            try:
                k = int(symbol[1:])
            except ValueError:
                return None, None
            return self._member_art(states[k]) if 0 <= k < len(states) else (None, None)
        want = _ref_core(symbol)
        for m in obj.members:
            if not 0 <= m < len(objs):
                continue
            c = objs[m]
            part = self._part(m) if c.raw.get("_in_composite") else None
            twin = part[0] if part is not None else None
            refs = (
                c.raw.get("_resolved_ref") or c.raw.get("_ref") or "",
                (twin.raw.get("_resolved_ref") or twin.raw.get("_ref") or "") if twin is not None else "",
            )
            if any(r and _ref_core(r) == want for r in refs):
                art, inside = self._member_art(m)
                if art is not None:
                    return art, inside
        return self._resolved_item(obj, symbol), None

    def _resolved_item(self, obj: GraphicObject, symbol: str):
        """``symbol`` resolved through the page's include chain, drawn into the record's box
        (as the reader places a symbol: reader.expand_element)."""
        from ..format.symbols import reference_from_symbol_value
        from ..io import reader

        ref = reference_from_symbol_value(symbol)
        if ref is None:
            return None
        path = getattr(self._page(), "source_path", "") or ""
        if self._resolver is None or self._resolver_path != path:
            self._resolver = reader.build_resolver(path) if path else None
            self._resolver_path = path
        res = self._resolver
        if res is None:
            return None
        resolved = res.resolve(ref)
        if resolved is None:
            return None
        g = obj.geometry
        tmp = GraphicObject(kind=ObjectKind.SYMBOL, an=obj.an)
        tmp.geometry = Geometry(x=g.x, y=g.y, extent_x=g.extent_x, extent_y=g.extent_y)
        tmp.raw = {"_type_code": 0x07}
        for k in ("_local_xform", "_slot_scale"):
            if obj.raw.get(k) is not None:
                tmp.raw[k] = obj.raw[k]
        try:
            reader.set_page_context(path)
        except Exception:  # noqa: BLE001
            _note_exc("sim_states.SimLayer._resolved_item")
            pass
        # the runtime shows a label's native (or translated) text, never the editor's literal
        # '@(...)' (ui/simulate/window.runtime_label_mode)
        mode = reader.get_label_mode()
        with reader.derive_options(
            label_mode="native" if mode in ("literal", "") else None,
            value_placeholder=getattr(self._scene, "value_placeholder", None),
        ):
            reader.expand_element(tmp, resolved, res, 0)
        if not tmp.children:
            return None
        return self._scene._make_symbol_item(tmp)

    def _stack_after_unit(self, i: int, item) -> None:
        """Just before the first item drawn after record ``i``'s subtree (the stacking order
        is the record order)."""
        sc = self._scene
        if self._keys_serial != sc.build_serial:
            self._keys = sorted(sc._items)
            self._keys_serial = sc.build_serial
        sub = self._subtree(i)
        end = (max(sub) if sub else i) + 1
        k = bisect.bisect_left(self._keys, end)
        while k < len(self._keys):
            nxt = sc._items.get(self._keys[k])
            if nxt is not None:
                if nxt.zValue() == item.zValue():
                    item.stackBefore(nxt)
                return
            k += 1

    # -- a group's level background ---------------------------------------------------
    def _apply_bg(self, i: int, obj: GraphicObject, look: _Look, prev: _Look, force) -> None:
        want = (
            look.level is not None and obj.kind not in _CLOSED_KINDS and _qcolour(look.level[2]) is not None
        )
        it = self._bg_items.get(i)
        if not want:
            if it is not None:
                it.setData(QtCore.Qt.UserRole + 7, 0)
                it.setVisible(False)
                self._reset_extra(i, "bg", it)
            return
        g = obj.geometry
        box = QtCore.QRectF(g.x, g.y, max(g.extent_x, 1), max(g.extent_y, 1))
        filled = _level_rect(box, look.level[0], look.level[1])
        rest = QtGui.QPainterPath()
        rest.addRect(box)
        cut = QtGui.QPainterPath()
        cut.addRect(filled)
        rest = rest.subtracted(cut)
        new = it is None
        if new:
            it = QtWidgets.QGraphicsPathItem()
            it.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            _stamp(it, i)
            self._scene.addItem(it)
            self._stack_after_unit(i, it)
            self._bg_items[i] = it
        if new or force or look.level != prev.level:
            if self._snaps.get((i, "bg")) is not None:
                self._apply_matrix(self._snaps[(i, "bg")], None)
                self._snaps.pop((i, "bg"), None)
            it.setPath(rest)
            it.setBrush(QtGui.QBrush(_qcolour(look.level[2])))
        s = self._snap(i, "bg", it)
        self._apply_matrix(s, look.matrix)
        it.setData(QtCore.Qt.UserRole + 7, 1)
        it.setVisible(self._visible(i, "bg", look))

    # -- the quality indication -------------------------------------------------------
    def _apply_dither(self, i: int, obj: GraphicObject, main, look: _Look, prev: _Look, force) -> None:
        """A dotted overlay over record ``i`` while its indication is set (a top-level item of
        its own, stacked just above the record's unit, moved with its state's matrix)."""
        it = self._dither_items.get(i)
        if look.indication is None or look.hidden:
            if it is not None:
                it.setVisible(False)
                self._reset_extra(i, "dither", it)
            return
        new = it is None
        if new:
            it = QtWidgets.QGraphicsPathItem()
            it.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            it.setData(DITHER_ROLE, 1)
            _stamp(it, i)
            self._scene.addItem(it)
            self._stack_after_unit(i, it)
            self._dither_items[i] = it
        if new or force or look.indication != prev.indication:
            s0 = self._snaps.pop((i, "dither"), None)
            if s0 is not None:
                self._apply_matrix(s0, None)
            # the record's whole extent (rect1, both edges in), not its outline: the runtime
            # dots a polygon's notch and its 1-px border too
            g = obj.geometry
            path = QtGui.QPainterPath()
            path.addRect(QtCore.QRectF(g.x, g.y, max(g.extent_x, 0) + 1, max(g.extent_y, 0) + 1))
            it.setPath(path)
            it.setToolTip("")
        s = self._snap(i, "dither", it)
        self._apply_matrix(s, look.matrix)
        # the dots stay on the page's pixel grid (a GDI pattern brush is aligned to the
        # window, not to the object) also while the state moves the overlay
        colour, density = DITHER_DEFAULTS.get(look.indication, (0x000000, 2))
        inv, ok = it.sceneTransform().inverted()
        key = (
            (
                colour,
                density,
                tuple(round(v, 6) for v in (inv.m11(), inv.m12(), inv.m21(), inv.m22(), inv.dx(), inv.dy())),
            )
            if ok
            else None
        )
        if new or self._dither_keys.get(i) != key:
            brush = dither_brush(colour, density)
            if ok:
                brush.setTransform(inv)
            it.setBrush(brush)
            self._dither_keys[i] = key
        it.setVisible(not look.hidden)

    def dither_items(self) -> Dict[int, object]:
        """The quality overlays shown now, by record index (tests)."""
        return {i: it for i, it in self._dither_items.items() if it.isVisible()}


def _geometry(it):
    if isinstance(it, QtWidgets.QGraphicsPathItem):
        return QtGui.QPainterPath(it.path())
    if isinstance(it, QtWidgets.QGraphicsLineItem):
        return QtCore.QLineF(it.line())
    if isinstance(it, QtWidgets.QGraphicsPolygonItem):
        return QtGui.QPolygonF(it.polygon())
    return QtCore.QRectF(it.rect())


def _set_geometry(it, geom) -> None:
    if isinstance(geom, QtGui.QPainterPath):
        it.setPath(geom)
    elif isinstance(geom, QtCore.QLineF):
        it.setLine(geom)
    elif isinstance(geom, QtGui.QPolygonF):
        it.setPolygon(geom)
    else:
        it.setRect(geom)


def _mapped(it, geom, m: QtGui.QTransform):
    """``geom`` mapped through ``m``, truncated to whole pixels as the runtime does."""
    pen = it.pen()
    odd = pen.style() != QtCore.Qt.NoPen and int(round(pen.widthF() or 1.0)) % 2 == 1
    if isinstance(geom, QtCore.QRectF):
        off = 0.5 if odd else 0.0
        r = m.mapRect(geom.translated(-off, -off))
        l, t = _trunc(r.left()), _trunc(r.top())
        rr, b = _trunc(r.right()), _trunc(r.bottom())
        return QtCore.QRectF(l + off, t + off, max(rr - l, 1), max(b - t, 1))
    if isinstance(geom, QtCore.QLineF):
        return m.map(geom)
    off = 0.5 if odd else 0.0
    if isinstance(geom, QtGui.QPolygonF):
        q = m.map(geom.translated(-off, -off))
        return QtGui.QPolygonF([QtCore.QPointF(_trunc(pt.x()) + off, _trunc(pt.y()) + off) for pt in q])
    p = m.map(geom.translated(-off, -off))
    n = p.elementCount()
    if n and all(
        p.elementAt(k).type in (QtGui.QPainterPath.MoveToElement, QtGui.QPainterPath.LineToElement)
        for k in range(n)
    ):
        for k in range(n):
            e = p.elementAt(k)
            p.setElementPositionAt(k, _trunc(e.x), _trunc(e.y))
    return p.translated(off, off)


def _trunc(v: float) -> int:
    """C's integer cast of a page coordinate (toward zero; a hair under a whole number, the
    float error of a scale or the runtime look's STROKE_NUDGE, counts as that number)."""
    return int(math.trunc(v + (0.02 if v >= 0 else -0.02)))


#: how far the runtime look moves a 1 px slanted stroke off the pixel centres (_runtime_strokes)
STROKE_NUDGE = 0.01
#: the items it moves
_NUDGED_KINDS = (
    QtWidgets.QGraphicsPolygonItem,
    QtWidgets.QGraphicsPathItem,
    QtWidgets.QGraphicsEllipseItem,
    QtWidgets.QGraphicsLineItem,
)


def _straight(path: QtGui.QPainterPath) -> bool:
    """Whether a path is only straight segments (a polyline / polygon, no curve)."""
    n = path.elementCount()
    return n > 0 and all(
        path.elementAt(k).type in (QtGui.QPainterPath.MoveToElement, QtGui.QPainterPath.LineToElement)
        for k in range(n)
    )


def _brush_colour(b: QtGui.QBrush) -> Optional[QtGui.QColor]:
    """The colour a brush fills with (a gradient's first stop), None for no brush."""
    if b.style() == QtCore.Qt.NoBrush:
        return None
    g = b.gradient()
    if g is not None:
        stops = g.stops()
        return QtGui.QColor(stops[0][1]) if stops else None
    return QtGui.QColor(b.color())


def _shape_path(item) -> Optional[QtGui.QPainterPath]:
    """A closed shape item's outline in its own coordinates."""
    p = QtGui.QPainterPath()
    if isinstance(item, QtWidgets.QGraphicsRectItem):
        p.addRect(item.rect())
    elif isinstance(item, QtWidgets.QGraphicsEllipseItem):
        p.addEllipse(item.rect())
    elif isinstance(item, QtWidgets.QGraphicsPolygonItem):
        p.addPolygon(item.polygon())
        p.closeSubpath()
    elif isinstance(item, QtWidgets.QGraphicsPathItem):
        p = QtGui.QPainterPath(item.path())
    else:
        return None
    return p


#: the largest offset / coordinate a state may carry (the i16 page range, with room)
_COORD_MAX = 65535.0
#: the largest scale factor drawn (SZXMAXPC is an i16 percent: 327.67 at most)
_SCALE_MAX = 1000.0


def _finite(v, lo: float, hi: float) -> Optional[float]:
    """``v`` as a float clamped to ``lo .. hi``; None when it is not a finite number."""
    try:
        f = float(v)
    except TypeError, ValueError:
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return max(lo, min(hi, f))


def _point(p, lo: float = -_COORD_MAX, hi: float = _COORD_MAX) -> Optional[tuple]:
    """A finite (x, y) pair clamped to the range, else None (not a pair / NaN / infinite)."""
    try:
        x, y = p
    except TypeError, ValueError:
        return None
    x, y = _finite(x, lo, hi), _finite(y, lo, hi)
    return None if x is None or y is None else (x, y)


def _copy_state(st: ObjectState) -> ObjectState:
    """A private, drawable copy of a state (its fields copied as they are: ObjectState has
    no __post_init__, and dataclasses.replace is far slower; the fields below are then made
    safe to draw)."""
    c = object.__new__(type(st))
    c.__dict__.update(st.__dict__)
    if st.vertex_offsets is not None:
        c.vertex_offsets = [(_point(p) or (0.0, 0.0)) for p in st.vertex_offsets]
    for f in ("move", "rotate_origin", "scale_origin"):
        v = getattr(st, f)
        if v is not None:
            setattr(c, f, _point(v))
    if st.scale is not None:
        c.scale = _point(st.scale, -_SCALE_MAX, _SCALE_MAX)
    if st.rotate is not None:
        r = _finite(st.rotate, -1e9, 1e9)
        c.rotate = None if r is None else math.fmod(r, 360.0)
    return c
