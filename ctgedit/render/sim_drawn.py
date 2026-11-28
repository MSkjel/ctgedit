"""Draw what the page's Cicode drew."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from . import buttons as _btn
from . import rtf_html as _rtf
from .scene import PAGE_INDEX_ROLE, SIM_CHILD_ROLE, walk_items
from ..format import codes as _codes
from ..infra.logsetup import note_exception as _note_exc

#: drawn items lie above every page item (scene items are at z 0)
Z_DRAWN = 100000.0
#: [Page]AnmDelay without a value (ms)
ANM_DELAY_MS = 500
_CICODE_OBJECT = 0x1009
_FULL_SIZE = 32000.0
#: a rich text box's horizontal scroll unit and tab stop (pixels; the engine's RichTextView)
RICH_CHAR_PX = 8.0
RICH_TAB_PX = 48.0


def _num(v, default=None):
    if isinstance(v, bool) or v is None:
        return default
    try:
        f = float(v)
    except TypeError, ValueError:
        return default
    return f if f == f and abs(f) != float("inf") else default


def _colour(v) -> Optional[QtGui.QColor]:
    """0xRRGGBB (an int, or a '0x..' string) -> QColor; -1 / 0xFF high byte = transparent."""
    if isinstance(v, str):
        try:
            v = int(v, 0)
        except ValueError:
            return None
    n = _num(v)
    if n is None or n < 0:
        return None
    c = int(n) & 0xFFFFFFFF
    if (c >> 24) & 0xFF == 0xFF:
        return None
    return QtGui.QColor((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)


def _pair(v, default) -> Tuple[Optional[QtGui.QColor], Optional[QtGui.QColor]]:
    """fontInfo's [on, off] (a single value = both halves)."""
    if v is None:
        return default, default
    if isinstance(v, (list, tuple)):
        if not v:
            return default, default
        on = _colour(v[0])
        off = _colour(v[1]) if len(v) > 1 else on
        return on, off
    c = _colour(v)
    return c, c


def _clip_rect(v) -> Optional[QtCore.QRectF]:
    """args.clip 'l,t,r,b' (DspSetClip) as a rect; None when absent or empty."""
    if not v:
        return None
    try:
        l, t, r, b = (float(p) for p in str(v).split(","))
    except ValueError:
        return None
    if r <= l or b <= t:
        return None
    return QtCore.QRectF(l, t, r - l, b - t)


#: a plot pen's dashes by style (GDI's 1-pixel pens, in pixels)
_PLOT_DASHES = {1: [18.0, 6.0], 2: [3.0, 3.0], 3: [9.0, 6.0, 3.0, 6.0], 4: [9.0, 3.0, 3.0, 3.0, 3.0, 3.0]}
#: a plot text's turn by orientation (1 upwards, 2 right to left, 3 downwards)
_PLOT_TURN = {1: -90.0, 2: 180.0, 3: 90.0}


def _plot_pen(v) -> QtGui.QPen:
    """A plot primitive's pen {style, colour, width}: style 5 or a transparent colour draws no
    line; a pen wider than 1 draws solid."""
    if not isinstance(v, dict):
        return QtGui.QPen(QtCore.Qt.NoPen)
    style = int(_num(v.get("style"), 0) or 0)
    colour = _colour(v.get("colour"))
    if style == 5 or colour is None:
        return QtGui.QPen(QtCore.Qt.NoPen)
    width = max(1, min(32, int(_num(v.get("width"), 1) or 1)))
    pen = QtGui.QPen(colour)
    pen.setWidth(width)
    if width > 1:
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
    elif style in _PLOT_DASHES:
        pen.setDashPattern(_PLOT_DASHES[style])
    return pen


def _plot_brush(v) -> QtGui.QBrush:
    c = _colour(v)
    return QtGui.QBrush(c) if c is not None else QtGui.QBrush(QtCore.Qt.NoBrush)


def _plot_box(p: dict, x0: float, y0: float) -> QtCore.QRectF:
    x1, y1 = _num(p.get("x1"), 0.0), _num(p.get("y1"), 0.0)
    x2, y2 = _num(p.get("x2"), 0.0), _num(p.get("y2"), 0.0)
    return QtCore.QRectF(x0 + min(x1, x2), y0 + min(y1, y2), abs(x2 - x1), abs(y2 - y1))


def _plot_marker_path(style: int, x: float, y: float, size: float) -> QtGui.QPainterPath:
    """A built-in marker centred on (x, y): 1 / 5 triangle, 2 / 6 square, 3 / 7 circle, 4 / 8
    diamond; another style a square."""
    h = size / 2.0
    path = QtGui.QPainterPath()
    kind = style - 4 if style > 4 else style
    if kind == 1:
        path.addPolygon(
            QtGui.QPolygonF(
                [QtCore.QPointF(x, y - h), QtCore.QPointF(x + h, y + h), QtCore.QPointF(x - h, y + h)]
            )
        )
        path.closeSubpath()
    elif kind == 3:
        path.addEllipse(QtCore.QPointF(x, y), h, h)
    elif kind == 4:
        path.addPolygon(
            QtGui.QPolygonF(
                [
                    QtCore.QPointF(x, y - h),
                    QtCore.QPointF(x + h, y),
                    QtCore.QPointF(x, y + h),
                    QtCore.QPointF(x - h, y),
                ]
            )
        )
        path.closeSubpath()
    else:
        path.addRect(QtCore.QRectF(x - h, y - h, size, size))
    return path


def font_of(info: Optional[dict]) -> QtGui.QFont:
    """The QFont of a fontInfo (Arial 12 px without one)."""
    info = info if isinstance(info, dict) else {}
    f = QtGui.QFont(str(info.get("face") or "Arial"))
    h = _num(info.get("height"))
    if h is None or h == 0:
        pt = _num(info.get("pt"))
        h = (pt * 96.0 / 72.0) if pt else 12.0
    f.setPixelSize(max(1, int(round(abs(h)))))
    f.setBold(bool(info.get("bold")))
    f.setItalic(bool(info.get("italic")))
    f.setUnderline(bool(info.get("underline")))
    f.setStrikeOut(bool(info.get("strikeout")))
    return f


def _ellipsis(fm: QtGui.QFontMetricsF, text: str, length: float) -> str:
    """``text`` cut to fit ``length`` pixels with '...' at its end (GDI's DT_END_ELLIPSIS
    draws three full stops); just '...' cut to the field when nothing else fits."""
    lo, hi = 0, len(text)
    while lo < hi:  # the longest prefix that fits with the dots
        mid = (lo + hi + 1) // 2
        if fm.horizontalAdvance(text[:mid] + "...") <= length:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "..."


def _holder(rect: Optional[QtCore.QRectF] = None) -> QtWidgets.QGraphicsRectItem:
    """An item that draws nothing (a parent for an entry's parts); with ``rect`` it clips its
    children to it."""
    h = QtWidgets.QGraphicsRectItem(rect if rect is not None else QtCore.QRectF())
    h.setPen(QtGui.QPen(QtCore.Qt.NoPen))
    h.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
    if rect is not None:
        h.setFlag(QtWidgets.QGraphicsItem.ItemClipsChildrenToShape, True)
    else:
        h.setFlag(QtWidgets.QGraphicsItem.ItemHasNoContents, True)
    return h


class _Built:
    """One AN's drawn item: its entry signature, the item, the colour halves to switch with
    the flash phase [(setter, on, off)], the frames of an animated symbol and the members of a
    genie DspSym placed."""

    __slots__ = ("sig", "item", "flash", "frames", "frame", "members")

    def __init__(self, sig: str, item):
        self.sig = sig
        self.item = item
        self.flash: List[Tuple[Callable, Any, Any]] = []
        self.frames: list = []
        self.frame = 0
        self.members: Optional["_Members"] = None


def _finite(v) -> Optional[float]:
    try:
        f = float(v)
    except TypeError, ValueError:
        return None
    return f if f == f and abs(f) < 1e7 else None


class _Members:
    """The records of a placed DspSym genie, drawn with their members' states."""

    def __init__(self, records: list, item, xf: tuple, members: list):
        self.records = records
        self.handle_an: Dict[int, int] = {}
        for m in members or ():
            try:
                self.handle_an.setdefault(int(m["handle"]), int(m["an"]))
            except KeyError, TypeError, ValueError:
                continue
        self.ans = set(self.handle_an.values())
        ids = {id(c): k for k, c in enumerate(records)}
        self.items: Dict[int, list] = {}
        for it in walk_items(item):
            k = ids.get(it.data(SIM_CHILD_ROLE))
            if k is not None:
                self.items.setdefault(k, []).append(it)
        ox, oy, sx, sy = xf
        self.g = QtGui.QTransform(sx or 1.0, 0, 0, sy or 1.0, ox, oy)
        self.g_inv, ok = self.g.inverted()
        if not ok:
            self.g_inv = QtGui.QTransform()
        # the items' own look before any state: transform and visibility (a symbol set's
        # other states hidden: the reader shows its first, sim_drawn built them all); texts
        # and pens / brushes are kept when a state first changes them
        self.base: Dict[int, tuple] = {}
        for k, its in self.items.items():
            shown = not records[k].raw.get("_state_hidden")
            for it in its:
                if not shown:
                    it.setVisible(False)
                self.base[id(it)] = (QtGui.QTransform(it.transform()), it.isVisible())
        self.texts: Dict[int, tuple] = {}
        self.fills: Dict[int, tuple] = {}

    def _state(self, states: dict, rec):
        an = self.handle_an.get(rec.raw.get("_handle"))
        return states.get(an) if an is not None else None

    def _origin(self, rec, given) -> Tuple[float, float]:
        if given is not None:
            try:
                return float(given[0]), float(given[1])
            except TypeError, ValueError, IndexError:
                pass
        g = rec.geometry
        c = self.g.map(QtCore.QPointF(g.x + g.extent_x / 2.0, g.y + g.extent_y / 2.0))
        return c.x(), c.y()

    def _own(self, rec, st) -> Tuple[bool, Optional[QtGui.QTransform]]:
        """(shown, the state's page matrix) of one record (None = no matrix)."""
        if st is None:
            return True, None
        shown = bool(st.shown)
        m = None
        if st.scale is not None:
            sx = _finite(st.scale[0])
            sy = _finite(st.scale[1] if len(st.scale) > 1 else 1.0)
            if sx is None or sy is None or sx == 0 or sy == 0:
                return False, None  # a size factor 0: nothing (never singular)
            if (sx, sy) != (1.0, 1.0):
                ox, oy = self._origin(rec, st.scale_origin)
                m = (
                    QtGui.QTransform.fromTranslate(-ox, -oy)
                    * QtGui.QTransform.fromScale(sx, sy)
                    * QtGui.QTransform.fromTranslate(ox, oy)
                )
        r = _finite(st.rotate) if st.rotate else None
        if r:
            ox, oy = self._origin(rec, st.rotate_origin)
            t = (
                QtGui.QTransform.fromTranslate(-ox, -oy)
                * QtGui.QTransform().rotate(r)
                * QtGui.QTransform.fromTranslate(ox, oy)
            )
            m = t if m is None else m * t
        if st.move is not None:
            dx = _finite(st.move[0])
            dy = _finite(st.move[1] if len(st.move) > 1 else 0.0)
            if dx is not None and dy is not None and (dx or dy):
                t = QtGui.QTransform.fromTranslate(dx, dy)
                m = t if m is None else m * t
        return shown, m

    def apply(self, states: dict) -> None:
        """Draw every record with its member's state now (``states`` = {AN: ObjectState})."""
        from .sim_states import SimLayer, qcolour

        recs = self.records
        memo: Dict[int, Tuple[bool, Optional[QtGui.QTransform]]] = {}

        picked: Dict[int, bool] = {}  # a symbol set state its set's symbol_state picks

        def total(k: int, depth: int = 0):
            if k in memo:
                return memo[k]
            rec = recs[k]
            shown, m = self._own(rec, self._state(states, rec))
            p = rec.parent
            if isinstance(p, int) and 0 <= p < len(recs) and p != k and depth < 64:
                pshown, pm = total(p, depth + 1)
                shown = shown and pshown
                if pm is not None:
                    m = pm if m is None else m * pm
                if p in picked:
                    # a member of a state that is a group-root symbol's instance forest
                    # shows with its state
                    picked[k] = picked[p]
                # a symbol set shows the state its symbol_state picks (its children in order,
                # its touch / keyboard records not counted)
                pst = self._state(states, recs[p])
                if pst is not None and pst.symbol_state is not None:
                    kids = [
                        c
                        for c in (recs[p].members or ())
                        if isinstance(c, int)
                        and 0 <= c < len(recs)
                        and recs[c].raw.get("_type_code") not in (_codes.TC.TOUCH, _codes.TC.KEYBOARD)
                    ]
                    if k in kids:
                        picked[k] = kids.index(k) == int(pst.symbol_state)
                        shown = shown and picked[k]
            memo[k] = (shown, m)
            return memo[k]

        for k, its in self.items.items():
            shown, m = total(k)
            x = None if m is None else self.g * m * self.g_inv
            rec = recs[k]
            st = self._state(states, rec)
            for it in its:
                base, vis = self.base.get(id(it), (QtGui.QTransform(), True))
                if k in picked:
                    vis = True  # the picked state shows, its static look aside
                try:
                    it.setVisible(vis and shown)
                    it.setTransform(base if x is None else base * x)
                    self._text(rec, it, st.text if st is not None else None, SimLayer)
                    self._fill(rec, it, st.fill_colour if st is not None else None, SimLayer, qcolour)
                except RuntimeError:
                    continue

    def _text(self, rec, it, text, SimLayer) -> None:
        from .scene import PageScene

        main, _ghosts, _mode = SimLayer.text_parts(rec, it)
        if main is None:
            return
        key = id(main)
        if key not in self.texts:
            if text is None:
                return
            self.texts[key] = (main.text(), QtCore.QPointF(main.pos()))
        orig, pos = self.texts[key]
        main.setPos(pos)
        if text is None:
            main.setText(orig)
            return
        main.setText(str(text))
        g = rec.geometry
        try:
            PageScene.layout_text(main, rec, g.x, g.y, g.extent_x, g.extent_y)
        except Exception:  # noqa: BLE001 -- keep the static place
            _note_exc("sim_drawn._text")
            main.setPos(pos)

    def _fill(self, rec, it, colour, SimLayer, qcolour) -> None:
        ch = SimLayer.channel(rec, it)
        if ch not in ("fill", "pen", "text"):
            return
        target = it
        if ch == "text":
            target, _g, _m = SimLayer.text_parts(rec, it)
            if target is None:
                return
        key = id(target)
        if key not in self.fills:
            if colour is None:
                return
            pen = QtGui.QPen(target.pen()) if hasattr(target, "pen") else None
            self.fills[key] = (pen, QtGui.QBrush(target.brush()))
        pen, brush = self.fills[key]
        if pen is not None:
            target.setPen(pen)
        target.setBrush(brush)
        if colour is None:
            return
        qc = qcolour(colour)
        if ch == "fill":
            target.setBrush(QtGui.QBrush(qc) if qc is not None else QtGui.QBrush(QtCore.Qt.NoBrush))
        elif ch == "pen" and pen is not None:
            p = QtGui.QPen(pen)
            if qc is None:
                p = QtGui.QPen(QtCore.Qt.NoPen)
            else:
                p.setColor(qc)
            target.setPen(p)
        else:
            target.setBrush(QtGui.QBrush(qc if qc is not None else QtGui.QColor(0, 0, 0, 0)))


class DrawnLayer:
    """The drawn output of one PageScene (``SimLayer.drawn_layer``)."""

    def __init__(self, sim_layer):
        self._layer = sim_layer
        self._scene = sim_layer._scene
        self.entries: Dict[int, dict] = {}
        self._built: Dict[int, _Built] = {}
        self._hidden: Dict[int, object] = {}  # record index -> the item hidden for it
        self._flash_on = bool(getattr(sim_layer, "_flash_on", True))
        self._suspended = False
        self._dbf: Dict[tuple, list] = {}
        self._timer: Optional[QtCore.QTimer] = None
        #: the symbols that did not resolve ('lib.element')
        self.missing_symbols: set = set()

    # -- public ------------------------------------------------------------------------
    def active(self) -> bool:
        return bool(self.entries)

    def hides(self, i: int) -> bool:
        """Record ``i``'s own item is hidden (its Cicode object's AN has drawn output)."""
        return i in self._hidden

    def items(self) -> Dict[int, object]:
        """The drawn item of each AN (tests)."""
        return {an: b.item for an, b in self._built.items() if b.item is not None}

    def buttons_at(self, point) -> List[int]:
        """The ANs of the buttons Cicode drew (DspButton / DspButtonFn) whose box holds the
        page ``point``, topmost first: they take the pointer's input
        (ctsim/input on the button's AN runs its up / down function or key)."""
        out: List[Tuple[float, int]] = []
        for an, e in self.entries.items():
            if e.get("kind") != "button":
                continue
            b = self._built.get(an)
            if b is None or b.item is None or not b.item.isVisible():
                continue
            it = b.item
            box = it.mapRectToScene(it.boundingRect() | it.childrenBoundingRect())
            if box.contains(point):
                out.append((b.item.zValue(), an))
        out.sort(key=lambda zv: -zv[0])
        return [an for _z, an in out]

    def _tf(self, f):
        """``f`` unsmoothed when the scene draws text unsmoothed (scene.text_font)."""
        tf = getattr(self._scene, "text_font", None)
        return tf(f) if tf is not None else f

    def apply(self, drawn) -> dict:
        """``drawn`` = the whole list ({AN: entry}, or the engine's JSON list): new and changed
        ANs are drawn again, gone ones removed. Returns {'drawn', 'changed', 'removed'}."""
        if isinstance(drawn, (list, tuple)):
            from ..sim.state import drawn_from_json

            drawn = drawn_from_json(drawn)
        drawn = dict(drawn or {})
        changed = removed = 0
        for an in [a for a in self.entries if a not in drawn]:
            self._drop(an)
            removed += 1
        old, self.entries = self.entries, drawn
        for an, e in drawn.items():
            sig = self._sig(e)
            b = self._built.get(an)
            if b is not None and b.sig == sig:
                continue
            if b is not None:
                self._remove_item(b)
            if (old.get(an) or {}).get("kind") == "array" and e.get("kind") != "array":
                self._clear_array(an)
            nb = self._built[an] = self._build(an, e, sig)
            if nb.members is not None:
                nb.members.apply(self._layer.states)
            changed += 1
        self._restack()
        self._update_hidden()
        self._update_timer()
        return {"drawn": len(drawn), "changed": changed, "removed": removed}

    def clear(self) -> None:
        self.apply({})

    def member_states(self, states: dict, ans=None) -> int:
        """The states of the members of the genies DspSym placed changed (``ans``: those ANs,
        None = any): their records are drawn again. The number of genies drawn again."""
        n = 0
        for b in self._built.values():
            m = b.members
            if m is None or b.item is None or (ans is not None and not (m.ans & ans)):
                continue
            m.apply(states)
            n += 1
        return n

    def set_flash(self, on: bool) -> None:
        on = bool(on)
        if on == self._flash_on:
            return
        self._flash_on = on
        for b in self._built.values():
            for setter, c_on, c_off in b.flash:
                setter(c_on if on else c_off)

    def suspend(self) -> None:
        """The static page for an export: the drawn items hidden, the Cicode symbols shown."""
        if self._suspended:
            return
        self._suspended = True
        for b in self._built.values():
            if b.item is not None:
                b.item.setVisible(False)
        self._update_hidden()

    def resume(self) -> None:
        if not self._suspended:
            return
        self._suspended = False
        for b in self._built.values():
            if b.item is not None:
                b.item.setVisible(True)
        self._update_hidden()

    def forget(self) -> None:
        """scene.clear() deleted every item (ours too): drop the references; ``rebind`` draws
        the entries again."""
        self._built = {}
        self._hidden = {}

    def rebind(self) -> None:
        """Draw the entries again over the scene's (new) items: lost items rebuilt, the
        Cicode symbols hidden again."""
        for an, e in self.entries.items():
            b = self._built.get(an)
            if b is None or (b.item is not None and b.item.scene() is not self._scene):
                self._built[an] = self._build(an, e, self._sig(e))
        self._restack()
        self._hidden = {}
        self._update_hidden()

    # -- items -------------------------------------------------------------------------
    @staticmethod
    def _sig(e: dict) -> str:
        d = {k: v for k, v in e.items() if k != "serial"}
        cells = d.get("cells")
        if isinstance(cells, dict) and cells.get("serial") is not None:
            # an array's cells (up to 500 x 64 strings) change with their serial: the serial
            # stands for the data, so a poll does not serialise the whole table
            d["cells"] = {k: v for k, v in cells.items() if k != "data"}
        return json.dumps(d, sort_keys=True, default=str)

    def _remove_item(self, b: _Built) -> None:
        if b.item is not None and b.item.scene() is self._scene:
            self._scene.removeItem(b.item)
        b.item = None

    def _drop(self, an: int) -> None:
        b = self._built.pop(an, None)
        if b is not None:
            self._remove_item(b)
        if (self.entries.get(an) or {}).get("kind") == "array":
            self._clear_array(an)

    def _clear_array(self, an: int) -> None:
        """An array output went (or its AN draws another kind now): the alarm-list layer's
        grid of it goes too."""
        try:
            from . import sim_alarmlists
        except ImportError:
            return
        sim_alarmlists.draw_array(self._layer, an, None)

    def _build(self, an: int, e: dict, sig: str) -> _Built:
        b = _Built(sig, None)
        kind = e.get("kind")
        fn = getattr(self, "_k_" + str(kind), None) if isinstance(kind, str) else None
        item = None
        if fn is not None:
            try:
                item = fn(b, e)
            except Exception:  # noqa: BLE001 -- an entry that cannot be drawn draws nothing
                _note_exc("sim_drawn._build")
                item = None
        if item is None:
            b.flash = []
            b.frames = []
            return b
        clip = _clip_rect((e.get("args") or {}).get("clip"))
        if clip is not None:
            h = _holder(clip)
            item.setParentItem(h)
            item = h
        for it in walk_items(item):
            it.setData(PAGE_INDEX_ROLE, None)
            it.setData(0, None)
        if item.scene() is None:
            self._scene.addItem(item)
        item.setVisible(not self._suspended)
        b.item = item
        return b

    def _restack(self) -> None:
        for k, an in enumerate(self.entries):
            b = self._built.get(an)
            if b is not None and b.item is not None:
                b.item.setZValue(Z_DRAWN + k)

    # -- the hidden Cicode object symbols -------------------------------------------------
    def _update_hidden(self) -> None:
        layer = self._layer
        page = layer._page()
        want: Dict[int, object] = {}
        if page is not None and not self._suspended:
            layer._ensure_map()
            for an in self.entries:
                i = layer._index_of.get(an)
                if i is None or not 0 <= i < len(page.objects):
                    continue
                if page.objects[i].raw.get("_type_code") != _CICODE_OBJECT:
                    continue
                item = self._scene._items.get(i)
                if item is not None:
                    want[i] = item
        old, self._hidden = self._hidden, want
        for i, item in old.items():
            if want.get(i) is not item:
                try:
                    item.setVisible(layer._visible(i, "main"))
                except RuntimeError:
                    pass
        for i, item in want.items():
            item.setVisible(False)

    # -- the kinds ----------------------------------------------------------------------
    def _k_text(self, b: _Built, e: dict):
        text = e.get("text")
        if not isinstance(text, str) or not text:
            return None
        info = e.get("fontInfo") if isinstance(e.get("fontInfo"), dict) else {}
        args = e.get("args") or {}
        font = self._tf(font_of(info))
        fm = QtGui.QFontMetricsF(font)
        x, y = _num(e.get("x"), 0.0), _num(e.get("y"), 0.0)
        length = _num(args.get("length"), -1.0)
        align = int(_num(args.get("align"), 0) or 0)
        trunc = int(_num(args.get("trunc"), 0) or 0)
        field = length is not None and length > 0 and trunc in (0, 1)
        if field and trunc == 1 and fm.horizontalAdvance(text) > length:
            text = _ellipsis(fm, text, length)
        w = fm.horizontalAdvance(text)
        h = fm.height()
        top = y - fm.ascent()  # (x, y) = the text's baseline
        if field:
            left = x + {1: length - w, 2: (length - w) / 2.0}.get(align, 0.0)
        else:
            left = x - {1: w, 2: w / 2.0}.get(align, 0.0)
        holder = _holder(QtCore.QRectF(x, top, length, h) if field and trunc == 0 else None)
        fg_on, fg_off = _pair(info.get("fg"), QtGui.QColor(0, 0, 0))
        bg_on, bg_off = _pair(info.get("bg"), None)
        flash = bool(info.get("flash"))
        if bg_on is not None or bg_off is not None:
            bx, bw = (x, length) if field and trunc == 0 else (left, w)
            bg = QtWidgets.QGraphicsRectItem(bx, top, bw, h, holder)
            bg.setPen(QtGui.QPen(QtCore.Qt.NoPen))

            def set_bg(c, item=bg):
                item.setBrush(QtGui.QBrush(c) if c is not None else QtGui.QBrush(QtCore.Qt.NoBrush))

            set_bg(bg_on if self._flash_on or not flash else bg_off)
            if flash and bg_on != bg_off:
                b.flash.append((set_bg, bg_on, bg_off))
        t = QtWidgets.QGraphicsSimpleTextItem(text, holder)
        t.setFont(font)
        t.setPos(left, top)
        t.setPen(QtGui.QPen(QtCore.Qt.NoPen))

        def set_fg(c, item=t):
            item.setBrush(QtGui.QBrush(c) if c is not None else QtGui.QBrush(QtCore.Qt.NoBrush))

        set_fg(fg_on if self._flash_on or not flash else fg_off)
        if flash and fg_on != fg_off:
            b.flash.append((set_fg, fg_on, fg_off))
        return holder

    def _k_sym(self, b: _Built, e: dict):
        args = e.get("args") or {}
        x, y = _num(e.get("x"), 0.0), _num(e.get("y"), 0.0)
        x += _num(args.get("posX"), 0.0)
        y += _num(args.get("posY"), 0.0)
        sx = sy = 1.0
        if "sizeX" in args or "sizeY" in args:
            sx = _num(args.get("sizeX"), _FULL_SIZE) / _FULL_SIZE
            sy = _num(args.get("sizeY"), _FULL_SIZE) / _FULL_SIZE
            if sx <= 0 or sy <= 0:
                return None  # a size factor 0: nothing to draw (never singular)
        ref = self._symbol_name(str(e.get("text") or ""), int(_num(args.get("type"), 0) or 0))
        if not ref:
            return None
        members = args.get("members")
        if not isinstance(members, list) or not members:
            return self._symbol_item(ref, x, y, sx, sy)
        got = self._symbol_item(ref, x, y, sx, sy, keep=True)
        if got is None:
            return None
        item, records, xf = got
        b.members = _Members(records, item, xf, members)
        return item

    def _k_symAnm(self, b: _Built, e: dict):  # noqa: N802 -- the engine's kind name
        args = e.get("args") or {}
        names = [s.strip() for s in str(args.get("symbols") or e.get("text") or "").split(",") if s.strip()]
        x, y = _num(e.get("x"), 0.0), _num(e.get("y"), 0.0)
        holder = _holder()
        for n in names:
            it = self._symbol_item(n, x, y, 1.0, 1.0)
            if it is None:
                continue
            it.setParentItem(holder)
            it.setVisible(not b.frames)
            b.frames.append(it)
        return holder if b.frames else None

    def _k_button(self, b: _Built, e: dict):
        args = e.get("args") or {}
        text = str(e.get("text") or "")
        font = self._tf(font_of(e.get("fontInfo"))) if isinstance(e.get("fontInfo"), dict) else None
        if font is None:
            font = self._tf(QtGui.QFont("Arial"))
            font.setPixelSize(14)
        fm = QtGui.QFontMetricsF(font)
        x, y = int(_num(e.get("x"), 0.0)), int(_num(e.get("y"), 0.0))
        w = int(_num(args.get("width"), 0) or 0)
        h = int(_num(args.get("height"), 0) or 0)
        if w <= 0:
            w = int(fm.horizontalAdvance(text)) + 16
        if h <= 0:
            h = int(fm.height()) + 8
        l, t, r, bt = x, y, x + w - 1, y + h - 1
        holder = _holder()
        face = QtWidgets.QGraphicsRectItem(l, t, w, h, holder)
        face.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        face.setBrush(QtGui.QBrush(_btn.sys_colour(_btn.COLOR_BTNFACE)))
        for path, colour in _btn.raised_edges(l, t, r, bt, soft=True):
            p = QtWidgets.QGraphicsPathItem(path, holder)
            pen = QtGui.QPen(colour)
            pen.setWidth(0)
            p.setPen(pen)
        if text:
            cap = QtWidgets.QGraphicsSimpleTextItem(text, holder)
            cap.setFont(font)
            cap.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            info = e.get("fontInfo") if isinstance(e.get("fontInfo"), dict) else {}
            fg, _off = _pair(info.get("fg"), None)
            cap.setBrush(QtGui.QBrush(fg or _btn.sys_colour(_btn.COLOR_BTNTEXT)))
            cap.setPos(l + int((w - fm.horizontalAdvance(text)) / 2), t + int((h - fm.height()) / 2))
        return holder

    def _k_bmp(self, b: _Built, e: dict):
        path = self._find_file(str(e.get("text") or ""))
        if not path:
            return None
        pm = QtGui.QPixmap(path)
        if pm.isNull():
            return None
        it = QtWidgets.QGraphicsPixmapItem(pm)
        it.setPos(_num(e.get("x"), 0.0), _num(e.get("y"), 0.0))
        return it

    def _box(self, e: dict, w: float, h: float, label: str):
        x, y = _num(e.get("x"), 0.0), _num(e.get("y"), 0.0)
        holder = _holder()
        r = QtWidgets.QGraphicsRectItem(x, y, max(w, 8.0), max(h, 8.0), holder)
        pen = QtGui.QPen(QtGui.QColor(0x80, 0x80, 0x80))
        pen.setWidth(0)
        r.setPen(pen)
        r.setBrush(QtGui.QBrush(QtGui.QColor(0xFF, 0xFF, 0xFF)))
        if label:
            inner = _holder(QtCore.QRectF(x, y, max(w, 8.0), max(h, 8.0)))
            inner.setParentItem(holder)
            t = QtWidgets.QGraphicsSimpleTextItem(label, inner)
            f = self._tf(QtGui.QFont("Arial"))
            f.setPixelSize(12)
            t.setFont(f)
            t.setBrush(QtGui.QBrush(QtGui.QColor(0x60, 0x60, 0x60)))
            t.setPos(x + 3, y + 2)
        return holder

    def _k_file(self, b: _Built, e: dict):
        """A file display: the lines its window shows, each on its own AN (args.lines)."""
        lines = (e.get("args") or {}).get("lines")
        if not isinstance(lines, list):
            return None
        holder = _holder()
        for line in lines:
            if not isinstance(line, dict):
                continue
            item = self._k_text(
                b,
                {
                    "text": line.get("text"),
                    "x": line.get("x"),
                    "y": line.get("y"),
                    "fontInfo": e.get("fontInfo"),
                },
            )
            if item is not None:
                item.setParentItem(holder)
        return holder if holder.childItems() else None

    def _k_plot(self, b: _Built, e: dict):
        """A plot PlotClose showed: its primitives (args.prims) in order, each at coordinates
        relative to the AN."""
        prims = (e.get("args") or {}).get("prims")
        if not isinstance(prims, list):
            return None
        x0, y0 = _num(e.get("x"), 0.0), _num(e.get("y"), 0.0)
        holder = _holder()
        for p in prims:
            if not isinstance(p, dict):
                continue
            item = self._plot_item(p, x0, y0)
            if item is not None:
                item.setParentItem(holder)
        return holder if holder.childItems() else None

    def _plot_item(self, p: dict, x0: float, y0: float):
        op = p.get("op")
        pen = _plot_pen(p.get("pen"))
        if op in ("rect", "ellipse"):
            box = _plot_box(p, x0, y0)
            it = QtWidgets.QGraphicsRectItem(box) if op == "rect" else QtWidgets.QGraphicsEllipseItem(box)
            it.setPen(pen)
            it.setBrush(_plot_brush(p.get("fill")))
            return it
        if op == "line":
            it = QtWidgets.QGraphicsLineItem(
                x0 + _num(p.get("x1"), 0.0),
                y0 + _num(p.get("y1"), 0.0),
                x0 + _num(p.get("x2"), 0.0),
                y0 + _num(p.get("y2"), 0.0),
            )
            it.setPen(pen)
            return it
        if op == "point":
            if pen.style() == QtCore.Qt.NoPen:
                return None
            w = float(pen.width())
            x, y = x0 + _num(p.get("x"), 0.0), y0 + _num(p.get("y"), 0.0)
            it = QtWidgets.QGraphicsRectItem(x - (w - 1) // 2, y - (w - 1) // 2, w, w)
            it.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            it.setBrush(QtGui.QBrush(pen.color()))
            return it
        if op == "polyline":
            pts = p.get("points")
            if not isinstance(pts, list) or len(pts) < 4:
                return None
            path = QtGui.QPainterPath()
            for k in range(0, len(pts) - 1, 2):
                q = QtCore.QPointF(x0 + _num(pts[k], 0.0), y0 + _num(pts[k + 1], 0.0))
                if k == 0:
                    path.moveTo(q)
                else:
                    path.lineTo(q)
            it = QtWidgets.QGraphicsPathItem(path)
            it.setPen(pen)
            it.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
            return it
        if op == "text":
            return self._plot_text(p, x0, y0)
        if op == "marker":
            return self._plot_marker(p, x0, y0)
        return None

    def _plot_text(self, p: dict, x0: float, y0: float):
        """A plot text: its baseline starts at (x, y), turned by its orientation."""
        text = p.get("text")
        if not isinstance(text, str) or not text:
            return None
        info = p.get("fontInfo") if isinstance(p.get("fontInfo"), dict) else {}
        font = self._tf(font_of(info))
        fm = QtGui.QFontMetricsF(font)
        g = _holder()
        g.setPos(x0 + _num(p.get("x"), 0.0), y0 + _num(p.get("y"), 0.0))
        g.setRotation(_PLOT_TURN.get(int(_num(p.get("orient"), 0) or 0), 0.0))
        fg, _off = _pair(info.get("fg"), QtGui.QColor(0, 0, 0))
        bg, _off = _pair(info.get("bg"), None)
        if bg is not None:
            r = QtWidgets.QGraphicsRectItem(0.0, -fm.ascent(), fm.horizontalAdvance(text), fm.height(), g)
            r.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            r.setBrush(QtGui.QBrush(bg))
        t = QtWidgets.QGraphicsSimpleTextItem(text, g)
        t.setFont(font)
        t.setPos(0.0, -fm.ascent())
        t.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        t.setBrush(QtGui.QBrush(fg) if fg is not None else QtGui.QBrush(QtCore.Qt.NoBrush))
        return g

    def _plot_marker(self, p: dict, x0: float, y0: float):
        """A plot marker centred on (x, y): a built-in shape, or the registered symbol with its
        hot spot there (a dashed square when the symbol cannot be drawn)."""
        x, y = x0 + _num(p.get("x"), 0.0), y0 + _num(p.get("y"), 0.0)
        style = int(_num(p.get("marker"), 0) or 0)
        size = max(2.0, _num(p.get("size"), 8.0) or 8.0)
        colour = _colour(p.get("colour"))
        if style >= 20:
            sym = p.get("symbol")
            item = self._symbol_item(sym, x, y, 1.0, 1.0) if isinstance(sym, str) and sym else None
            if item is not None:
                return item
        it = QtWidgets.QGraphicsPathItem(_plot_marker_path(style, x, y, size))
        pen = QtGui.QPen(colour) if colour is not None else QtGui.QPen(QtCore.Qt.NoPen)
        pen.setWidth(0)
        if style >= 20:
            pen.setStyle(QtCore.Qt.DashLine)
        it.setPen(pen)
        filled = 5 <= style <= 8 and colour is not None
        it.setBrush(QtGui.QBrush(colour) if filled else QtGui.QBrush(QtCore.Qt.NoBrush))
        return it

    def _k_richText(self, b: _Built, e: dict):  # noqa: N802 -- the engine's kind name
        """A rich text box: its RTF (args.rtf) on a white box of args.width x args.height at the
        AN, clipped to it, scrolled to paragraph args.row and args.col characters of
        RICH_CHAR_PX. Read-only."""
        args = e.get("args") or {}
        x, y = _num(e.get("x"), 0.0), _num(e.get("y"), 0.0)
        w = max(1.0, _num(args.get("width"), 0.0) or 0.0)
        h = max(1.0, _num(args.get("height"), 0.0) or 0.0)
        holder = _holder(QtCore.QRectF(x, y, w, h))
        holder.setBrush(QtGui.QBrush(QtGui.QColor(0xFF, 0xFF, 0xFF)))
        rtf = args.get("rtf")
        if isinstance(rtf, str) and rtf:
            t = QtWidgets.QGraphicsTextItem()
            doc = t.document()
            doc.setDocumentMargin(1.0)
            doc.setDefaultFont(self._tf(QtGui.QFont("Arial")))
            opt = doc.defaultTextOption()
            opt.setTabStopDistance(RICH_TAB_PX)
            opt.setWrapMode(QtGui.QTextOption.NoWrap)
            doc.setDefaultTextOption(opt)
            t.setHtml(_rtf.rtf_to_html(rtf))
            dy = 0.0
            row = int(_num(args.get("row"), 0) or 0)
            block = doc.findBlockByNumber(row) if row > 0 else QtGui.QTextBlock()
            if block.isValid():
                dy = doc.documentLayout().blockBoundingRect(block).top()
            dx = max(0, int(_num(args.get("col"), 0) or 0)) * RICH_CHAR_PX
            t.setPos(x - dx, y - dy)
            t.setParentItem(holder)
        frame = QtWidgets.QGraphicsRectItem(x, y, w, h, holder)
        pen = QtGui.QPen(QtGui.QColor(0x80, 0x80, 0x80))
        pen.setWidth(0)
        frame.setPen(pen)
        frame.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        return holder

    def _k_bar(self, b: _Built, e: dict):
        args = e.get("args") or {}
        v = max(0.0, min(1.0, (_num(args.get("value"), 0.0) or 0.0) / _FULL_SIZE))
        x, y = _num(e.get("x"), 0.0), _num(e.get("y"), 0.0)
        holder = _holder()
        frame = QtWidgets.QGraphicsRectItem(x, y, 10, 50, holder)
        pen = QtGui.QPen(QtGui.QColor(0x40, 0x40, 0x40))
        pen.setWidth(0)
        frame.setPen(pen)
        if v > 0:
            fill = QtWidgets.QGraphicsRectItem(x, y + 50 * (1 - v), 10, 50 * v, holder)
            fill.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            fill.setBrush(QtGui.QBrush(QtGui.QColor(0x60, 0x60, 0x60)))
        return holder

    def _k_array(self, b: _Built, e: dict):
        # the alarm-list layer draws the grid into items of its own (it follows the array's
        # genie and the list states); nothing of this layer's
        try:
            from . import sim_alarmlists
        except ImportError:
            return None
        an = _num(e.get("an"), None)
        if an is None:
            return None
        sim_alarmlists.draw_array(self._layer, int(an), e)
        return None

    # -- symbols ------------------------------------------------------------------------
    def _page_path(self) -> str:
        return getattr(self._layer._page(), "source_path", "") or ""

    def resolver(self):
        """The page's SymbolResolver (its include chain), made once per page path and shared
        with the SimLayer's symbol swaps."""
        from ..io import reader

        layer = self._layer
        path = self._page_path()
        if getattr(layer, "_resolver", None) is None or getattr(layer, "_resolver_path", None) != path:
            layer._resolver = reader.build_resolver(path) if path else None
            layer._resolver_path = path
        return layer._resolver

    def _rows(self, table: str) -> list:
        """The rows of a project table (almprior / almmodes) of the page's project and the
        projects it includes, as the compiler joins them."""
        path = self._page_path()
        key = (path, table)
        if key in self._dbf:
            return self._dbf[key]
        rows: list = []
        if path:
            try:
                from ..format.alarmind import chain_rows

                rows = chain_rows(path, table)
            except Exception:  # noqa: BLE001
                _note_exc("sim_drawn._rows")
                rows = []
        self._dbf[key] = rows
        return rows

    def _symbol_name(self, text: str, typ: int) -> str:
        """The 'lib.element' a DspSym shows: the name itself (types 0 / 1), the alarm
        priority's genie / thumbnail (2 / 3) or the alarm mode's (4 / 5)."""
        if typ in (2, 3, 4, 5):
            col = "LIBOBJNAME" if typ in (2, 4) else "THUMBNAIL"
            if typ in (2, 3):
                rows = self._rows("almprior")
                pick = [r for r in rows if str(r.get("PRIORITY") or "").strip() == text.strip()]
            else:
                rows = self._rows("almmodes")
                pick = [
                    r for r in rows if str(r.get("DISPNAME") or "").strip().lower() == text.strip().lower()
                ]
            if not pick:
                return ""
            lib = str(pick[0].get("LIBNAME") or "").strip()
            el = str(pick[0].get(col) or "").strip()
            return "%s.%s" % (lib, el) if lib and el else ""
        return text.strip()

    def _symbol_item(self, name: str, x: float, y: float, sx: float, sy: float, keep: bool = False):
        """The element ``name`` with its hot spot at (x, y), scaled (sx, sy) about it;
        ``keep``: (the item, the element's records, the element -> page map) instead."""
        from ..format import placement
        from ..format.symbols import reference_from_symbol_value
        from ..io import reader
        from ..model.document import Geometry, GraphicObject, ObjectKind

        ref = reference_from_symbol_value(name)
        res = self.resolver()
        if ref is None or res is None:
            self.missing_symbols.add(name)
            return None
        resolved = res.resolve(ref)
        if resolved is None:
            self.missing_symbols.add(name)
            return None
        ext = os.path.splitext(resolved.library_path)[1].lstrip(".").lower() or ref.ext
        info = placement.element_info(res, ref.library, ref.element, ext)
        tmp = GraphicObject(kind=ObjectKind.SYMBOL, an=0)
        tmp.geometry = Geometry(x=x, y=y, extent_x=resolved.width or 1, extent_y=resolved.height or 1)
        tmp.raw = {"_type_code": 0x07}
        # the element's texts as the runtime shows them: the native (or translated) string,
        # never the editor's literal '@(...)' (ui/simulate/window.runtime_label_mode)
        mode = reader.get_label_mode()
        with reader.derive_options(
            label_mode="native" if mode in ("literal", "") else None,
            value_placeholder=getattr(self._scene, "value_placeholder", None),
        ):
            try:
                reader.set_page_context(self._page_path())
            except Exception:  # noqa: BLE001
                _note_exc("sim_drawn._symbol_item")
            reader.expand_element(tmp, resolved, res, 0)
        if not tmp.children:
            self.missing_symbols.add(name)
            return None
        ex, ey = info["e"] if info else (tmp.children[0].geometry.x, tmp.children[0].geometry.y)
        # element coordinates -> page: (p - e) * s + (x, y)
        xf = (x - ex * sx, y - ey * sy, sx, sy)
        for c in tmp.children:
            c.raw["_symbol_xform"] = xf
        tmp.raw["_symbol_xform"] = xf
        hidden_states = []
        if keep:
            # every state of a symbol set is drawn (hidden but its first): the members'
            # symbol_state picks the one shown (_Members)
            for c in tmp.children:
                if c.raw.get("_state_hidden"):
                    c.raw["_state_hidden"] = False
                    hidden_states.append(c)
        item = self._scene._make_symbol_item(tmp)
        for c in hidden_states:
            c.raw["_state_hidden"] = True
        if keep:
            return None if item is None else (item, list(tmp.children), xf)
        return item

    def _find_file(self, name: str) -> str:
        """A DspBmp file: as given when it exists, else in the page's project folder and its
        include chain."""
        if not name:
            return ""
        if os.path.isabs(name):
            return name if os.path.exists(name) else ""
        path = self._page_path()
        if not path:
            return ""
        try:
            from ..format.projects import include_search_dirs

            base = os.path.dirname(os.path.abspath(path))
            dirs = [base] + include_search_dirs(base)
        except Exception:  # noqa: BLE001
            _note_exc("sim_drawn._find_file")
            dirs = [os.path.dirname(os.path.abspath(path))]
        for d in dirs:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
        return ""

    # -- animated symbols ---------------------------------------------------------------
    def _update_timer(self) -> None:
        anm = any(len(b.frames) > 1 for b in self._built.values())
        if not anm:
            if self._timer is not None:
                self._timer.stop()
            return
        if self._timer is None:
            # parented to the scene: it stops and goes with the page's scene
            self._timer = QtCore.QTimer(self._scene)
            self._timer.timeout.connect(self.next_frame)
            self._timer.setInterval(anm_delay_ms())
        if not self._timer.isActive():
            self._timer.start()

    def next_frame(self) -> None:
        """Every animated symbol shows its next frame."""
        for b in self._built.values():
            if len(b.frames) > 1:
                try:
                    b.frames[b.frame].setVisible(False)
                    b.frame = (b.frame + 1) % len(b.frames)
                    b.frames[b.frame].setVisible(True)
                except RuntimeError:
                    pass


def anm_delay_ms() -> int:
    """[Page]AnmDelay of the active citect.ini (ms), else 500."""
    try:
        from ..io.citect_config import ini_value

        v = int(str(ini_value("Page", "AnmDelay", "") or "").strip() or 0)
    except Exception:  # noqa: BLE001
        _note_exc("sim_drawn.anm_delay_ms")
        v = 0
    return v if v > 0 else ANM_DELAY_MS
