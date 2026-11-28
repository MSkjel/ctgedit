"""Draw the simulator's alarm lists, array lists and priority symbols."""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..model.document import GraphicObject, ObjectKind
from .scene import PAGE_INDEX_ROLE, SIM_CHILD_ROLE, walk_items
from ..infra.logsetup import note_exception as _note_exc

#: an alarm state -> the priority symbols' COL_ARR index (SA_Controls SAStateOffset)
STATE_INDEX = {"unack_on": 1, "unack_off": 2, "ack_on": 3, "disabled": 4}

#: the SA alarm list's defaults when the engine sends no fontInfo (SA_Include fonts.dbf:
#: SA_AlmRow / SA_AlmHeaderB = Calibri -12 = 16 px, 0x333333)
_ROW_FONT = {"face": "Calibri", "height": 16, "fg": [0x333333, 0x333333], "bg": [-1, -1]}
_HEADER_FONT = {"face": "Calibri", "height": 16, "bold": True, "fg": [0x333333, 0x333333], "bg": [-1, -1]}
#: a classic AlarmDsp row without fontInfo (Example fonts.dbf Alm*_Set*: -10 = 13 px)
_CLASSIC_FONT = {"face": "Arial", "height": 13, "fg": [0, 0], "bg": [-1, -1]}
#: the row height of an SA list whose genie gives none (every shipped HD1080 list: 24)
DEFAULT_ROW_HEIGHT = 24
#: the inset of a cell's text in its column (the runtime draws the glyphs 2 px right of the
#: column's left edge; Qt draws a glyph 1 px right of its item's x)
CELL_INSET = 2
_QT_GLYPH_X = 1

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def state_index(state) -> int:
    return STATE_INDEX.get(str(state or "").lower(), 0)


def column_title(field: str) -> str:
    """A list's header title for a format field: none for PriorityAndState (AlarmPage.ci
    _ArrayView_ClearColumnTitle), else the field split at its capitals ('OnDate' -> 'On
    Date', as the runtime's header shows it; guess for fields the capture does not show)."""
    f = str(field or "").strip()
    if f.lower().startswith("priorityandstate"):
        return ""
    return _CAMEL.sub(" ", f)


def _colour(v) -> Optional[QtGui.QColor]:
    """0xRRGGBB, -1 / None / 0xFF high byte = transparent (None)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        c = int(v)
    except TypeError, ValueError:
        return None
    if c < 0 or (c >> 24) & 0xFF:
        return None
    return QtGui.QColor((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)


def _pair(v, flash_on: bool, flashes: bool):
    if isinstance(v, (list, tuple)) and v:
        on = v[0]
        off = v[1] if len(v) > 1 else v[0]
        return on if (flash_on or not flashes) else off
    return v


def font_of(info: Optional[dict], default: dict) -> QtGui.QFont:
    """A QFont from a fontInfo ({face, height (pixels), bold, italic, underline, strikeout})."""
    fi = dict(default)
    if isinstance(info, dict):
        fi.update({k: v for k, v in info.items() if v is not None})
    f = QtGui.QFont(str(fi.get("face") or default.get("face") or "Arial"))
    try:
        px = int(fi.get("height") or 0)
    except TypeError, ValueError:
        px = 0
    f.setPixelSize(max(1, px or int(default.get("height") or 12)))
    f.setBold(bool(fi.get("bold")))
    f.setItalic(bool(fi.get("italic")))
    f.setUnderline(bool(fi.get("underline")))
    f.setStrikeOut(bool(fi.get("strikeout")))
    return f


def font_colours(
    info: Optional[dict], default: dict, flash_on: bool, row: Optional[dict] = None
) -> Tuple[Optional[QtGui.QColor], Optional[QtGui.QColor]]:
    """(text colour, background colour or None) of a fontInfo for the flash phase; a row
    without one keeps its own resolved 'colour' / 'background'."""
    if isinstance(info, dict) and ("fg" in info or "bg" in info):
        flashes = bool(info.get("flash")) and (row is None or bool(row.get("flash", True)))
        fg = _colour(_pair(info.get("fg", default.get("fg")), flash_on, flashes))
        bg = _colour(_pair(info.get("bg", default.get("bg")), flash_on, flashes))
        return fg, bg
    if row is not None and (row.get("colour") is not None or row.get("background") is not None):
        return (
            _colour(row.get("colour")) or _colour(_pair(default.get("fg"), True, False)),
            _colour(row.get("background")),
        )
    return _colour(_pair(default.get("fg"), True, False)), None


_METRICS: Dict[tuple, Optional[tuple]] = {}


def gdi_metrics(font: QtGui.QFont) -> Tuple[float, float]:
    """(tmHeight, tmAscent) of ``font`` as GDI makes it (CreateFont with -pixel height: the
    runtime's cell layout centres a row's text by them), else Qt's own metrics."""
    key = (font.family(), font.pixelSize(), font.bold(), font.italic())
    if key not in _METRICS:
        got = None
        try:
            from ..format import textmetrics

            g = textmetrics.Gdi.get()
            if g is not None and font.pixelSize() > 0:
                with g.system_aware():
                    f = g.gdi.CreateFontW(
                        -font.pixelSize(),
                        0,
                        0,
                        0,
                        700 if font.bold() else 400,
                        1 if font.italic() else 0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        font.family()[:31],
                    )
                    dc = g.user.GetDC(None)
                    old = g.gdi.SelectObject(dc, f)
                    try:
                        tm = g.TM()
                        g.gdi.GetTextMetricsW(dc, g.ct.byref(tm))
                        got = (float(tm.tmHeight), float(tm.tmAscent))
                    finally:
                        g.gdi.SelectObject(dc, old)
                        g.gdi.DeleteObject(f)
                        g.user.ReleaseDC(None, dc)
        except Exception:  # noqa: BLE001 -- Qt's metrics then
            _note_exc("sim_alarmlists.gdi_metrics")
            got = None
        _METRICS[key] = got
    got = _METRICS[key]
    if got is None:
        fm = QtGui.QFontMetricsF(font)
        return fm.height(), fm.ascent()
    return got


def text_top(font: QtGui.QFont, top: float, height: float) -> float:
    """The y of a QGraphicsSimpleTextItem whose baseline sits where GDI's vertically centred
    text in the cell ``top`` .. ``top + height`` puts it (DT_VCENTER: (h - tmHeight) / 2, then
    tmAscent down to the baseline; the item draws its baseline ceil(ascent) below its top)."""
    th, ta = gdi_metrics(font)
    base = top + int((height - th) // 2) + ta if height else top + ta
    return base - math.ceil(QtGui.QFontMetricsF(font).ascent())


def _flashes(info) -> bool:
    return isinstance(info, dict) and bool(info.get("flash"))


def _num(v, default: int = 0) -> int:
    try:
        return int(v)
    except TypeError, ValueError:
        return default


# -- priority symbols --------------------------------------------------------------------
def _descendants(obj: GraphicObject) -> Dict[int, GraphicObject]:
    out, stack = {}, list(obj.children or ())
    while stack:
        c = stack.pop()
        out[id(c)] = c
        stack.extend(c.children or ())
    return out


class SymbolPaint:
    """A priority symbol's items (a flag / icon built from a resolved element, ``obj`` its
    GraphicObject) painted for an alarm state: COL_ARR records take the state's pair (the
    flash phase's half), 'Label' texts the priority's short name. ``restore`` puts the
    items' own look back."""

    def __init__(self, item, obj: GraphicObject):
        self.item = item
        self.parts: List[tuple] = []  # (item, record, kind 'shape' | 'text')
        self._saved: Dict[int, tuple] = {}
        recs = _descendants(obj)
        for it in walk_items(item):
            c = recs.get(it.data(SIM_CHILD_ROLE))
            if c is None:
                continue
            for sub in walk_items(it):
                if isinstance(sub, QtWidgets.QGraphicsSimpleTextItem):
                    self.parts.append((sub, c, "text"))
                elif isinstance(sub, QtWidgets.QAbstractGraphicsShapeItem) and (
                    sub is it or sub.data(SIM_CHILD_ROLE) is None
                ):
                    self.parts.append((sub, c, "shape"))

    @staticmethod
    def _props(c: GraphicObject) -> dict:
        p = c.raw.get("_props") or {}
        return {str(k).upper(): v for k, v in p.items()}

    def paint(self, index: int, flash_on: bool, label: Optional[str] = None) -> None:
        for it, c, kind in self.parts:
            props = self._props(c)
            key = id(it)
            if key not in self._saved:
                if kind == "text":
                    self._saved[key] = (it, QtGui.QBrush(it.brush()), it.text(), QtCore.QPointF(it.pos()))
                else:
                    self._saved[key] = (it, QtGui.QPen(it.pen()), QtGui.QBrush(it.brush()))
            qc = None
            if "COL_ARR" in props:
                v = props.get("COL_ARR_%d" % index)
                if v is not None:
                    halves = [h.strip() for h in str(v).split(",")]
                    try:
                        vals = [int(h, 16) if h.lower().startswith("0x") else int(h) for h in halves if h]
                    except ValueError:
                        vals = []
                    if vals:
                        on, off = vals[0], vals[1] if len(vals) > 1 else vals[0]
                        qc = _colour(on if flash_on else off)
                        if qc is None and ((on if flash_on else off) >> 24) & 0xFF:
                            qc = QtGui.QColor(0, 0, 0, 0)
            if kind == "text":
                if qc is not None:
                    it.setBrush(QtGui.QBrush(qc))
                if label is not None and "label" in str(props.get("TXT_STR", "")).lower():
                    _set_text_centred(it, self._saved[key], label)
            elif qc is not None:
                if it.brush().style() != QtCore.Qt.NoBrush or c.style.filled:
                    it.setBrush(QtGui.QBrush(qc))
                else:
                    pen = QtGui.QPen(it.pen())
                    pen.setColor(qc)
                    it.setPen(pen)

    def restore(self) -> None:
        for saved in self._saved.values():
            it = saved[0]
            if isinstance(it, QtWidgets.QGraphicsSimpleTextItem):
                it.setBrush(saved[1])
                it.setText(saved[2])
                it.setPos(saved[3])
            else:
                it.setPen(saved[1])
                it.setBrush(saved[2])
        self._saved = {}


def _set_text_centred(it, saved, text: str) -> None:
    """``it`` showing ``text``, centred where its static text was (the flag's label is centred
    in the genie's shape)."""
    _it, _brush, old_text, old_pos = saved
    it.setText(old_text)
    it.setPos(old_pos)
    c = it.mapToParent(it.boundingRect().center())
    it.setText(str(text))
    br = it.boundingRect()
    it.setPos(old_pos + (c - it.mapToParent(br.center())))


def priority_symbol(layer, ref, x: float, y: float, w: float = 0, h: float = 0):
    """(item, GraphicObject) of the priority symbol ``ref`` (library, element) with its top-left
    at (x, y), at its own size or fitted into w x h; (None, None) when it does not resolve."""
    from ..format.symbols import SymbolReference
    from ..io import reader

    if ref is None:
        return None, None
    resolver = layer._symbol_resolver()
    if resolver is None:
        return None, None
    try:
        resolved = resolver.resolve(SymbolReference(ref[0], ref[1], "ctm", "genie"))
    except Exception:  # noqa: BLE001 -- a symbol that cannot be drawn draws nothing
        _note_exc("sim_alarmlists.priority_symbol")
        resolved = None
    if resolved is None or not (resolved.width or 0) > 0 or not (resolved.height or 0) > 0:
        return None, None
    ew, eh = resolved.width, resolved.height
    if w and h and (ew > w or eh > h):
        s = min(w / ew, h / eh)
        ew, eh = max(1, int(ew * s)), max(1, int(eh * s))
    obj = GraphicObject(kind=ObjectKind.SYMBOL)
    obj.geometry.x, obj.geometry.y = int(x), int(y)
    obj.geometry.extent_x, obj.geometry.extent_y = ew, eh
    try:
        reader.expand_element(obj, resolved, resolver, 1)
    except Exception:  # noqa: BLE001
        _note_exc("sim_alarmlists.priority_symbol")
        return None, None
    if not obj.children:
        return None, None
    item = layer._scene._make_symbol_item(obj)
    if item is None:
        return None, None
    for it in walk_items(item):
        it.setData(0, None)
        it.setData(PAGE_INDEX_ROLE, None)
    return item, obj


# -- the painter ---------------------------------------------------------------------------
class _Overlay:
    """One list's items: a clipping container with its text and icon items."""

    def __init__(self, index: Optional[int]):
        self.index = index
        self.key = None
        self.box = QtWidgets.QGraphicsRectItem()
        self.box.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        self.box.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        self.box.setFlag(QtWidgets.QGraphicsItem.ItemClipsChildrenToShape, True)
        self.box.setData(0, None)
        self.items: list = []  # every child (kept referenced)
        self.flashing = False
        self.off = False  # the array says hidden (cells.hidden)


class AlarmListPainter:
    """The alarm lists, alarm rows and array outputs of one SimLayer."""

    def __init__(self, layer):
        self.layer = layer
        self._lists: Dict[int, _Overlay] = {}  # AN -> overlay (alarm_list / alarm_row)
        self._arrays: Dict[int, _Overlay] = {}  # AN -> overlay (draw_array)
        self._array_entries: Dict[int, dict] = {}
        self._hidden = False

    # -- the layer's hooks --------------------------------------------------------------
    def _tf(self, f):
        """``f`` unsmoothed when the scene draws text unsmoothed (scene.text_font)."""
        tf = getattr(self.layer._scene, "text_font", None)
        return tf(f) if tf is not None else f

    def sync(self) -> None:
        """Draw what the layer's states say now (called after every apply / clear)."""
        want: Dict[int, tuple] = {}
        for an, st in self.layer.states.items():
            al = getattr(st, "alarm_list", None)
            ar = getattr(st, "alarm_row", None)
            if isinstance(al, dict) and al.get("array"):
                want[an] = ("list", al)
            elif isinstance(ar, dict):
                want[an] = ("row", ar)
        for an in [a for a in self._lists if a not in want]:
            self._drop(self._lists.pop(an))
        for an, (kind, data) in want.items():
            self._draw(an, kind, data)
        for an, entry in list(self._array_entries.items()):
            if want.get(an, ("",))[0] == "list":
                ov = self._arrays.pop(an, None)  # the list draws it (draw_array)
                if ov is not None:
                    self._drop(ov)
            elif an not in self._arrays:
                self.draw_array(an, entry)  # the list went: its array output again
        for ov in self._arrays.values():  # an array follows its genie's hidden state too
            ov.box.setVisible(not self._hidden and not ov.off and self._shows(ov.index))

    def flash(self) -> None:
        """The flash phase changed: redraw what flashes."""
        for an, ov in list(self._lists.items()):
            if ov.flashing:
                st = self.layer.states.get(an)
                if st is None:
                    continue
                al = getattr(st, "alarm_list", None)
                if isinstance(al, dict) and al.get("array"):
                    self._draw(an, "list", al)
                elif isinstance(getattr(st, "alarm_row", None), dict):
                    self._draw(an, "row", st.alarm_row)
        for an, ov in list(self._arrays.items()):
            if ov.flashing and an in self._array_entries:
                self.draw_array(an, self._array_entries[an])

    def set_hidden(self, hidden: bool) -> None:
        self._hidden = bool(hidden)
        for ov in list(self._lists.values()) + list(self._arrays.values()):
            ov.box.setVisible(not hidden and not ov.off and self._shows(ov.index))

    def forget(self, indices=None) -> None:
        """The scene drops the items of ``indices`` (None = all: scene.clear deletes ours)."""
        if indices is None:
            self._lists = {}
            self._arrays = {}
            return
        gone = set(indices)
        for d in (self._lists, self._arrays):
            for an in [a for a, ov in d.items() if ov.index in gone]:
                self._drop(d.pop(an))

    def redraw(self) -> None:
        for d in (self._lists, self._arrays):
            for ov in d.values():
                ov.key = None
        self.sync()
        for an, entry in list(self._array_entries.items()):
            self.draw_array(an, entry)

    def _list_at(self, an: int) -> bool:
        st = self.layer.states.get(an)
        al = getattr(st, "alarm_list", None) if st is not None else None
        return isinstance(al, dict) and bool(al.get("array"))

    def clear_array(self, an: int) -> None:
        self._array_entries.pop(int(an), None)
        ov = self._arrays.pop(int(an), None)
        if ov is not None:
            self._drop(ov)

    # -- helpers ----------------------------------------------------------------------------
    def _drop(self, ov: _Overlay) -> None:
        sc = ov.box.scene()
        if sc is not None:
            sc.removeItem(ov.box)
        ov.items = []

    def _shows(self, index: Optional[int]) -> bool:
        if index is None:
            return True
        try:
            return not self.layer._look(index).hidden
        except Exception:  # noqa: BLE001 -- a record the layer cannot look at shows
            _note_exc("sim_alarmlists._shows")
            return True

    def _overlay(self, store: dict, an: int, index: Optional[int]) -> _Overlay:
        ov = store.get(an)
        if ov is None or ov.index != index or ov.box.scene() is None:
            if ov is not None:
                self._drop(ov)
            ov = _Overlay(index)
            store[an] = ov
            self.layer._scene.addItem(ov.box)
            if index is not None:
                self.layer._stack_after_unit(index, ov.box)
        return ov

    def _record(self, an: int):
        page = self.layer._page()
        i = self.layer.index_of(an)
        if page is None or i is None or not 0 <= i < len(page.objects):
            return None, None
        return i, page.objects[i]

    def _scaled(self, i: int, an: int, key: str, func: str, axis: int) -> Optional[float]:
        """The pixel size the list genie's own part driven by ``func(an)`` through ``key``
        (SZX / SZY) has now: the part's extent times its state's scale."""
        page = self.layer._page()
        want = "%s(%d)" % (func.lower(), an)
        for j in self.layer._subtree(i):
            o = page.objects[j]
            v = str((o.raw.get("_props") or {}).get(key, "")).replace(" ", "").lower()
            if want not in v:
                continue
            st = self.layer.states.get(o.an)
            sc = getattr(st, "scale", None) if st is not None else None
            if not sc:
                continue
            ext = o.geometry.extent_x if axis == 0 else o.geometry.extent_y
            try:
                size = float(sc[axis]) * float(ext)
            except TypeError, ValueError:
                continue
            if size > 0:
                return size
        return None

    def _text(
        self, ov: _Overlay, text: str, font: QtGui.QFont, colour, x, y, w, h, align: str = "left"
    ) -> None:
        t = QtWidgets.QGraphicsSimpleTextItem(ov.box)
        t.setFont(font)
        fm = QtGui.QFontMetricsF(font)
        s = str(text)
        if w is not None and w > 0:
            s = fm.elidedText(s, QtCore.Qt.ElideRight, max(0.0, w - 2 * CELL_INSET))
        t.setText(s)
        t.setBrush(QtGui.QBrush(colour if colour is not None else QtGui.QColor(0, 0, 0)))
        tw = fm.horizontalAdvance(s)
        tx = x + CELL_INSET - _QT_GLYPH_X
        if align == "right" and w:
            tx = x + w - CELL_INSET - tw
        t.setPos(tx, text_top(font, y, h))
        t.setData(0, None)
        ov.items.append(t)

    def _icon(self, ov: _Overlay, cell: str, x, y, w, h) -> None:
        """A PriorityAndState cell ('priority,state'): the priority's small symbol."""
        from ..format import alarmind

        parts = str(cell or "").split(",")
        pr = _num(parts[0].strip(), 0) if parts and parts[0].strip() else 0
        si = _num(parts[1].strip(), 0) if len(parts) > 1 else 0
        if pr <= 0:
            return
        path = getattr(self.layer._page(), "source_path", "") or ""
        info = alarmind.priority_details(path).get(pr) if path else None
        ref = (info or {}).get("thumbnail") or (info or {}).get("flag")
        item, obj = priority_symbol(self.layer, ref, x, y, w - 2, h - 2)
        if item is None:
            return
        br = item.sceneBoundingRect()
        item.setPos(
            item.pos()
            + QtCore.QPointF(x + (w - br.width()) / 2.0 - br.x(), y + (h - br.height()) / 2.0 - br.y())
        )
        item.setParentItem(ov.box)
        paint = SymbolPaint(item, obj)
        paint.paint(si, self.layer._flash_on, (info or {}).get("short"))
        ov.items.append(item)
        if si in (1, 2, 6, 7):
            ov.flashing = True

    def _clear_children(self, ov: _Overlay) -> None:
        sc = ov.box.scene()
        for it in ov.items:
            if sc is not None:
                sc.removeItem(it)
        ov.items = []
        ov.flashing = False

    # -- an alarm list / row ------------------------------------------------------------
    def _draw(self, an: int, kind: str, data: dict) -> None:
        i, obj = self._record(an)
        flash_on = self.layer._flash_on
        if kind == "row" and obj is None:
            # a row AN that is no record: under the list's first AN
            row = _num(data.get("row"), 0)
            li, lobj = self._record(an - row) if row > 0 else (None, None)
            if lobj is None:
                return
            fi = data.get("fontInfo")
            h = (
                _num((fi or {}).get("height"), _CLASSIC_FONT["height"])
                if isinstance(fi, dict)
                else _CLASSIC_FONT["height"]
            )
            pos = (lobj.geometry.x, lobj.geometry.y + row * h)
            index = li
        elif obj is None:
            return
        else:
            pos = (obj.geometry.x, obj.geometry.y)
            index = i
        key = (kind, repr(data), pos, flash_on if self._flash_dependent(kind, data) else None)
        ov = self._overlay(self._lists, an, index)
        if ov.key == key:
            ov.box.setVisible(not self._hidden and self._shows(index))
            return
        ov.key = key
        self._clear_children(ov)
        if kind == "row":
            self._draw_row(ov, data, pos, flash_on)
        else:
            self._draw_list(ov, an, index, data, pos, flash_on)
        ov.box.setVisible(not self._hidden and self._shows(index))

    @staticmethod
    def _flash_dependent(kind: str, data: dict) -> bool:
        if kind == "row":
            return bool(data.get("flash")) and _flashes(data.get("fontInfo"))
        for r in data.get("rows") or ():
            if (r.get("flash") and _flashes(r.get("fontInfo"))) or state_index(r.get("state")) in (1, 2):
                return True
        return False

    def _draw_row(self, ov: _Overlay, row: dict, pos, flash_on: bool) -> None:
        fi = row.get("fontInfo")
        font = self._tf(font_of(fi, _CLASSIC_FONT))
        fg, bg = font_colours(fi, _CLASSIC_FONT, flash_on, row)
        text = str(row.get("text") or "").replace("\t", " ").replace("\x11", " ").rstrip()
        fm = QtGui.QFontMetricsF(font)
        w, h = fm.horizontalAdvance(text) + 1, fm.height()
        x, y = pos
        ov.box.setRect(QtCore.QRectF(x, y, max(w, 1), max(h, 1)))
        if bg is not None:
            r = QtWidgets.QGraphicsRectItem(x, y, w, h, ov.box)
            r.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            r.setBrush(QtGui.QBrush(bg))
            r.setData(0, None)
            ov.items.append(r)
        t = QtWidgets.QGraphicsSimpleTextItem(text, ov.box)
        t.setFont(font)
        t.setBrush(QtGui.QBrush(fg if fg is not None else QtGui.QColor(0, 0, 0)))
        t.setPos(x, y)
        t.setData(0, None)
        ov.items.append(t)
        ov.flashing = bool(row.get("flash")) and _flashes(fi)

    def _draw_list(self, ov: _Overlay, an: int, index: int, al: dict, pos, flash_on) -> None:
        cols = []
        for c in al.get("columns") or ():
            if isinstance(c, (list, tuple)) and c:
                cols.append((str(c[0]), max(0, _num(c[1] if len(c) > 1 else 0, 0)), 1))
        rh = _num(al.get("rowHeight"), 0) or int(
            round(self._scaled(index, an, "SZY", "_ArrayView_RowHeight", 1) or DEFAULT_ROW_HEIGHT)
        )
        width = _num(al.get("width"), 0) or self._scaled(index, an, "SZX", "_ArrayView_Width", 0)
        header = al.get("drawHeader", al.get("header", True))
        rows = []
        for r in al.get("rows") or ():
            fields = r.get("fields") or ()
            vals = {}
            for f in fields:
                if isinstance(f, (list, tuple)) and len(f) >= 2:
                    vals.setdefault(str(f[0]).lower(), str(f[1]))
            rows.append((r, [vals.get(c[0].lower(), "") for c in cols]))
        # the engine's titles (the list's array row 0 z 4, localised; what the page's
        # code cleared stays cleared), else made from the field names
        sent = al.get("titles")
        if isinstance(sent, (list, tuple)) and len(sent) == len(cols):
            titles = [str(t).strip() if t is not None else "" for t in sent]
        else:
            titles = [column_title(c[0]) for c in cols]
        self._grid(
            ov,
            index,
            pos,
            cols,
            titles,
            rows,
            rh,
            width,
            bool(header),
            al.get("headerFontInfo"),
            al.get("fontInfo"),
            flash_on,
            _num(al.get("xOff"), 0),
            max(0, _num(al.get("count"), 0)),
        )

    def _grid(
        self,
        ov: _Overlay,
        index,
        pos,
        cols,
        titles,
        rows,
        rh,
        width,
        header: bool,
        header_fi,
        row_fi,
        flash_on,
        xoff: int = 0,
        page_rows: int = 0,
    ) -> None:
        """Header + rows: ``cols`` [(field, width, type)], ``rows`` [(row dict | None, [cell
        text per column])]."""
        x0, y0 = pos
        total = sum(c[1] for c in cols)
        w = width if width and width > 0 else total
        n = len(rows) if not page_rows else min(len(rows), page_rows)
        h = rh * (n + (1 if header else 0))
        ov.box.setRect(QtCore.QRectF(x0, y0, max(w, 1), max(h, rh, 1)))
        hfont = self._tf(font_of(header_fi, _HEADER_FONT))
        hfg, _hbg = font_colours(header_fi, _HEADER_FONT, flash_on)
        y = y0
        if header:
            x = x0 - xoff
            for (field, cw, _t), title in zip(cols, titles):
                if title:
                    self._text(ov, title, hfont, hfg, x, y, cw, rh)
                x += cw
            y += rh
        for k, (row, cells) in enumerate(rows[:n]):
            fi = (row or {}).get("fontInfo") if isinstance(row, dict) else None
            fi = fi if isinstance(fi, dict) else row_fi
            font = self._tf(font_of(fi, _ROW_FONT))
            fg, bg = font_colours(fi, _ROW_FONT, flash_on, row if isinstance(row, dict) else None)
            if isinstance(row, dict) and row.get("flash") and _flashes(fi):
                ov.flashing = True
            if bg is not None:
                r = QtWidgets.QGraphicsRectItem(x0, y, w, rh, ov.box)
                r.setPen(QtGui.QPen(QtCore.Qt.NoPen))
                r.setBrush(QtGui.QBrush(bg))
                r.setData(0, None)
                ov.items.append(r)
            x = x0 - xoff
            for (field, cw, ctype), cell in zip(cols, cells):
                if field.lower().startswith("priorityandstate") or ctype == 3:
                    # 'priority,state' (an alarm list's field); an array cell holds the
                    # priority, its state comes from the column's meta (z 2, draw_array)
                    self._icon(ov, cell, x, y, cw, rh)
                elif ctype in (5, 7):
                    pass  # a genie / shelved-icon column: drawn by the page's genies
                elif cell:
                    self._text(ov, cell, font, fg, x, y, cw, rh, "right" if ctype == 2 else "left")
                x += cw
            y += rh

    # -- array outputs (DspArrayByAn) ----------------------------------------------------
    def draw_array(self, an: int, entry: dict) -> None:
        """Draw the PAGE.drawn 'array' entry ``entry`` of AN ``an`` (cells: rows, cols, data
        [[str ...]] row major, firstRow, serial, and for an SA ArrayView array xOff,
        rowHeight, drawHeader, rowFont / headerFont (+ rowFontInfo / headerFontInfo), hidden,
        columns [{col, field, type, width, title}], rowInfo [{row, font, selected}])."""
        an = int(an)
        if not isinstance(entry, dict):
            self.clear_array(an)
            return
        self._array_entries[an] = entry
        if self._list_at(an):
            # the SA list genie calls DspArrayByAn(%AN%) on its AlarmListCreate array: the
            # list's own state draws it (with each row's alarm state, which the cells lack),
            # never both
            ov = self._arrays.pop(an, None)
            if ov is not None:
                self._drop(ov)
            return
        cells = entry.get("cells") if isinstance(entry.get("cells"), dict) else {}
        i, obj = self._record(an)
        pos = (
            (obj.geometry.x, obj.geometry.y)
            if obj is not None
            else (_num(entry.get("x"), 0), _num(entry.get("y"), 0))
        )
        flash_on = self.layer._flash_on
        key = (repr(entry), pos, flash_on)
        ov = self._overlay(self._arrays, an, i)
        if ov.key == key:
            ov.box.setVisible(not self._hidden and not ov.off and self._shows(i))
            return
        ov.key = key
        self._clear_children(ov)
        ov.off = bool(cells.get("hidden"))
        if ov.off:
            ov.box.setVisible(False)
            return
        data = [r if isinstance(r, list) else [] for r in (cells.get("data") or [])]
        columns = [c for c in (cells.get("columns") or []) if isinstance(c, dict)]
        if columns:
            # an SA ArrayView array: row 0 the columns, column 0 the rows' settings
            cols = [
                (str(c.get("field") or ""), max(0, _num(c.get("width"), 0)), _num(c.get("type"), 1))
                for c in columns
            ]
            titles = []
            for c in columns:
                t = c.get("title")
                titles.append(
                    str(t).strip() if t not in (None, "") else column_title(str(c.get("field") or ""))
                )
            rh = _num(cells.get("rowHeight"), 0) or DEFAULT_ROW_HEIGHT
            header = bool(cells.get("drawHeader", True))
            info = {_num(r.get("row"), -1): r for r in (cells.get("rowInfo") or []) if isinstance(r, dict)}
            # the array holds the rows SHOWN: rows 1 .. Y - 1 (GenericList.ci sizes it to
            # the list's height; _ArrayView_Row_ColorIndex / _GenericView_Row_LeftClick read
            # row nRowOffset + nRow without the y offset, _ArrayView_PageRows = Y - 1 with or
            # without the header). firstRow (z 2) is the scroll position the list's provider
            # filled them from, not a row to skip.
            idx = [max(0, _num(c.get("col"), k + 1)) for k, c in enumerate(columns)]
            # a symbol column's state (the cells' z 2: the treeview's priority icons, engine
            # 'meta'); a cell is then 'priority,state' like an alarm list's PriorityAndState
            metas = [c.get("meta") if isinstance(c.get("meta"), list) else None for c in columns]
            rows = []
            for y in range(1, len(data)):
                cells_y = []
                for x, meta in zip(idx, metas):
                    v = str(data[y][x]) if x < len(data[y]) else ""
                    if meta is not None and v.strip() and y - 1 < len(meta) and str(meta[y - 1]).strip():
                        v = "%s,%s" % (v.strip(), str(meta[y - 1]).strip())
                    cells_y.append(v)
                rows.append((info.get(y), cells_y))
            self._grid(
                ov,
                i,
                pos,
                cols,
                titles,
                rows,
                rh,
                None,
                header,
                cells.get("headerFontInfo"),
                cells.get("rowFontInfo"),
                flash_on,
                _num(cells.get("xOff"), 0),
            )
        else:
            # a plain array: its rows as text, 100 px a column (guess: the layout of a
            # two-dimensional array the runtime draws is not read)
            rh = _num(cells.get("rowHeight"), 0) or DEFAULT_ROW_HEIGHT
            ncols = max((len(r) for r in data), default=0)
            cols = [("", 100, 1)] * ncols
            rows = [(None, [str(v) for v in r] + [""] * (ncols - len(r))) for r in data]
            self._grid(
                ov,
                i,
                pos,
                cols,
                [""] * ncols,
                rows,
                rh,
                None,
                False,
                None,
                cells.get("rowFontInfo"),
                flash_on,
            )
        ov.box.setVisible(not self._hidden and self._shows(i))


def draw_array(scene_layer, an: int, entry: Optional[dict]) -> None:
    """Draw (None: remove) the PAGE.drawn 'array' entry of AN ``an`` over the scene of
    ``scene_layer`` (a SimLayer, or a PageScene) into the SA ArrayView row grid."""
    layer = scene_layer.sim_layer() if hasattr(scene_layer, "sim_layer") else scene_layer
    painter = layer.alarm_lists()
    if entry is None:
        painter.clear_array(int(an))
    else:
        painter.draw_array(int(an), entry)


from ..infra import caches as _caches  # noqa: E402

# (font metrics: a pure function of the font)
_caches.register("sim_alarmlists.metrics", _METRICS, on=("tests",))
