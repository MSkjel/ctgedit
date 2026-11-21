"""Reader: style block, colours, fills and text styles."""

from __future__ import annotations

import os
import re  # noqa: F401 (re-exported)
import struct
from ...format import geometry as _geom
from ...model.document import (
    GraphicObject,
    ObjectKind,
    Style,
    TextJustify,
)
import re as _re
import struct as _struct
from ...format import placement as _pl
from ...format import textmetrics as _tm
from ...infra.logsetup import note_exception as _note_exc
from . import common as _pc, style as _ps, symbols as _py, graft as _pg  # noqa: F401


# --------------------------------------------------------------------------- style
def _norm_colorref(value: int | None) -> int | None:
    """Normalise a COLORREF to 0x00BBGGRR, mapping the 0xFF-high-byte 'no colour' sentinel
    to None (transparent/inherit)."""
    if value is None:
        return None
    if (value >> 24) & 0xFF == 0xFF:
        return None
    return value & 0x00FFFFFF


def _design_colour(pair) -> int | None:
    """The half of a colour pair CtDraw's editor paints."""
    return _norm_colorref(pair[0])


def _pair_on(dv) -> int | None:
    """The static ('on') COLORREF from a decoded color_pair DecodedValue."""
    if dv is None or getattr(dv, "kind", "") != "color_pair":
        return None
    try:
        return _norm_colorref(dv.value.get("on"))
    except Exception:  # noqa: BLE001
        _note_exc("reader._pair_on")
        return None


def _pair_static(dv) -> int | None:
    """The colour a static render shows for a colour pair."""
    if dv is None or getattr(dv, "kind", "") != "color_pair":
        return None
    try:
        off = _norm_colorref(dv.value.get("off"))
        if off is not None:
            return off
        return _norm_colorref(dv.value.get("on"))
    except Exception:  # noqa: BLE001
        _note_exc("reader._pair_static")
        return None


# dwFlags bits of the u32 at record-payload offset 6 (base record loader reads it FIRST;
# PageObject exposes it as `subtype_size`).
_DWF_STYLE_BLOCK = 0x002  # 54-byte style block present


_DWF_METADATA = 0x100  # derived METADATA_n table present


_DWF_GENIE = 0x200  # classic genie ROOT (0x100 only marks the metadata table)


class OnceColourBlock:
    """One record's style block, located once for its whole derivation."""

    __slots__ = ("po", "_done", "_cb", "_exc")

    def __init__(self, po):
        self.po = po
        self._done = False
        self._cb = None
        self._exc = None

    def get(self):
        if not self._done:
            try:
                self._cb = _geom.find_colour_block(self.po)
            except Exception as e:  # noqa: BLE001  # swallow-ok: raised again to each caller below
                self._exc = e
            self._done = True
        if self._exc is not None:
            raise self._exc
        return self._cb


def _colour_block(po, once=None):
    return once.get() if once is not None and once.po is po else _geom.find_colour_block(po)


def _style_block_exact(po, geom_end: int, once=None):
    """The record's style block, located the way the write path does."""
    try:
        cb = _colour_block(po, once)
    except Exception:  # noqa: BLE001
        _note_exc("reader._style_block_exact")
        cb = None
    if cb is None:
        return None
    return (cb.flags, cb.slots)


def _find_panel_colours(body: bytes, geom_end: int):
    """Decode the panel rectangle colour layout."""
    o = geom_end or 0
    if o <= 0 or o + 48 > len(body):
        return None
    try:
        (w0,) = struct.unpack_from("<H", body, o)
        ints = struct.unpack_from("<4I", body, o + 2)
        f_on, f_off = struct.unpack_from("<2I", body, o + 18)
        (filled_flag,) = struct.unpack_from("<H", body, o + 28)
        l_on, l_off = struct.unpack_from("<2I", body, o + 32)
    except struct.error:
        return None
    if w0 != 0 or ints != (2, 0xC, 2, 1):
        return None
    for v in (f_on, f_off, l_on, l_off):
        if (v >> 24) & 0xFF not in (0x00, 0xFF):
            return None
    # u16 @ +28 discriminates FILLED (1) from FRAME-ONLY (0). For a frame the first pair is
    # the border colour.
    if filled_flag == 0:
        return (None, (f_on, f_off))
    return ((f_on, f_off), (l_on, l_off))


def _find_tail_colour(body: bytes):
    """The chrome rect colour tail."""
    end = len(body)
    while end >= 4 and body[end - 4 : end] == b"\xff\xff\xff\xff":
        end -= 4
    if end < 12 or end == len(body):
        # require at least one no-colour pad dword: an unpadded tail is the plain
        # trailing-pair layout handled elsewhere
        return None
    try:
        c1, c2 = struct.unpack_from("<II", body, end - 8)
        flag = struct.unpack_from("<I", body, end - 12)[0]
    except struct.error:
        return None
    if c1 != c2 or (c1 >> 24) & 0xFF != 0 or flag > 0xFF:
        return None
    return c1


def _is_colour_animated(props: dict) -> bool:
    """Is the fill colour animated?"""
    cm = props.get("COLMODE")
    if cm is not None:
        try:
            if int(getattr(cm, "raw", cm)) != 0:
                return True
        except ValueError, TypeError:
            pass
    if any(k in props for k in ("COL_A", "COL_B", "COL_C", "COL_D", "COL_E", "COL_EXP")):
        return True
    if any(str(k).startswith(("COL_STA", "COL_ARR", "COL_THR")) for k in props):
        return True
    return False


def _is_widget_container(props: dict) -> bool:
    """A library widget / genie container (LibTable / LibTree / Calendar / grid): its content
    is drawn by Cicode at runtime, so its COL_MAXON is chrome/background colour the static render
    does NOT paint as a solid box."""
    if "Name" not in props:
        return False
    return any(
        k in props
        for k in (
            "InitFn",
            "ReloadFn",
            "ScalingFn",
            "CallbackFn",
            "Rows",
            "Cols",
            "Items",
            "Table",
            "Calendar",
            "ItemHeight",
            "RowHeight",
        )
    )


def _decode_style(po, kind: ObjectKind, geom_end: int | None, props: dict | None = None, once=None) -> Style:
    """Fill a Style from the on-disk colour data."""
    st = Style()
    if props is None:
        try:
            props = po.properties()
        except Exception:  # noqa: BLE001
            _note_exc("reader._decode_style")
            props = {}
    raw_props = {}
    try:
        raw_props = po.raw_properties()
    except Exception:  # noqa: BLE001
        _note_exc("reader._decode_style")
        raw_props = {}

    static = _pair_static(props.get("COL_MAXON")) or _pair_static(props.get("!COL_STATIC"))
    fill_level = _pair_on(props.get("FILCOL"))
    # first state-array colour as a further fallback
    state0 = _pair_static(props.get("COL_STA_0")) or _pair_static(props.get("COL_ARR_0"))

    # a LITERAL gate COL_A='FALSE' is statically OFF: the bag pair never paints
    if str(raw_props.get("COL_A", "")).strip().upper() == "FALSE":
        static = None
    fill_level_animated = any(k.startswith(("FILMODE", "FILMINPC", "FILMAXPC", "FILCOL")) for k in raw_props)

    _slot_static = _design_colour

    hi = sh = lo = line_blk = None
    blk_static = None
    hollow = False
    line_null = False
    # closed shapes ALWAYS consult the runtime colour sources (their static face never comes
    # from the bag -- see below)
    need_block = (
        (kind in _pc._CLOSED_KINDS)
        or (static is None and fill_level is None and state0 is None)
        or fill_level_animated
    )
    # an OPEN shape's stroke, pen width / style and 3D effect are its own style block's too
    # (fill rule: the stroke is the LINE slot, COL_MAXON only the runtime one)
    if kind in _pc._OPEN_KINDS and po.subtype_size & 0x2:
        need_block = True
    if need_block and geom_end is not None:
        # locator (dwFlags bit 0x2 + the forward walk). Slot order, in
        # CtDraw's own words:
        # [0]=line, [1]=fill, [2]=background, [3]=hilight, [4]=lolight, [5]=shadow.
        exact = _style_block_exact(po, geom_end, once)
        slots = exact[1] if exact is not None else None
        if exact is not None:
            f6 = exact[0]
            st.style_flags = bytes(f6[:6])
            st.line_style = f6[1] & 0x0F  # GDI PS_*: 0 solid 1 dash 2 dot ...
            if f6[4]:
                st.line_width = f6[4]  # pen width byte, never 0 on disk
            hollow = f6[2] == 0  # fill style: 0 hollow / 1 solid
            # 3D border effect + per-kind 'attribs' (CtDraw's classes: rect -> RectangleStyle<<4,
            # ellipse -> 0 ellipse/1 pie/2 arc, polygon -> 1 open/0 closed)
            st.three_d = f6[0] & 0x0F
            # effect_depth is stored (int)(char) by CtDraw, so 0xFF = -1
            # (a 1px inward bevel), not 0: sign-extend.
            st.three_d_depth = (f6[5] - 256) if f6[5] >= 0x80 else f6[5]
            if po.type_code == 0x0F:
                st.rectangle_style = (f6[3] >> 4) & 0x3
            elif po.type_code == 0x11:
                st.ellipse_style = f6[3] & 0x0F if (f6[3] & 0x0F) <= 2 else 0
            elif po.type_code in (0x0A, 0x10):
                st.polygon_open = bool(f6[3] & 0x01)
            elif po.type_code == _geom.FREEHAND_TC:
                # a free-hand stroke is never closed: CtDraw draws no closing segment
                # although its attribs bit 0 is 0; a filled one still fills its region
                # (the open-polygon fill rule)
                st.polygon_open = True
        if slots:
            line_blk = _slot_static(slots[0])
            # the record's OWN style block says 'no colour' for the line (0xFF high byte):
            # CtDraw draws no border, not a default pen
            line_null = exact is not None and line_blk is None
            blk_static = _slot_static(slots[1])
            hi = _slot_static(slots[3])
            lo = _slot_static(slots[4])
            sh = _slot_static(slots[5])
        elif po.type_code == 0x0F:
            # SA-style panel RECTANGLES use a different colour layout (other shapes can alias
            # the signature)
            panel = _find_panel_colours(po.body_raw, geom_end)
            if panel is not None:
                line_blk = _slot_static(panel[1])
                if panel[0] is not None:
                    blk_static = _slot_static(panel[0])

    # trailing static-colour pair (minimal line / polygon / panel-rect layout;
    # see format.geometry.find_trailing_colour_pair)
    if blk_static is None and line_blk is None:
        tp = _geom.find_trailing_colour_pair(po)
        if tp is not None:
            pair_on = _struct.unpack_from("<I", po.body_raw, tp)[0]
            blk_static = _norm_colorref(pair_on)
    # SxW chrome tail (flag + pair + 0xFFFFFFFF padding at body end)
    if blk_static is None and line_blk is None and po.type_code in (0x0F, 0x10):
        tc_col = _find_tail_colour(po.body_raw)
        if tc_col is not None:
            blk_static = _norm_colorref(tc_col)

    # per-kind shape variant tail: corner radius / pie-arc angles / gradient
    try:
        stail = _geom.parse_shape_tail(po)
    except Exception:  # noqa: BLE001
        _note_exc("reader._decode_style")
        stail = None
    if stail is not None:
        if stail["grad_mode"] <= 8:
            st.gradient_mode = stail["grad_mode"]
            st.gradient_colour = _design_colour((stail["grad_on"], stail["grad_off"]))
        if stail["kind"] == "rect" and stail["radius"] <= 32:
            st.corner_radius = stail["radius"]
        elif stail["kind"] == "ellipse":
            st.start_angle = float(stail["start"])
            st.end_angle = float(stail["end"])

    # CLOSED-shape fill sources, in CtDraw's editor precedence:
    #   1. a COL_MAXON/!COL_STATIC pair surviving the suppression rules (ungated, not
    #      animation-driven);
    #   2. else the runtime face (colour block / panel layout / trailing pair).
    # COL_STA_*/COL_ARR_* state arrays and FILCOL fill levels NEVER fill: they are runtime state
    # colours.
    bag_primary = static if static is not None else (fill_level if fill_level is not None else state0)
    # A colour-animated shape's TRAILING colour-state array (0x23 marker + state colours): the EDITOR
    # draws state-0 (the resting/default) when dwFlags bit 0x10 is CLEAR; when bit 0x10 is SET the
    # colour is fully runtime-animated and the editor leaves the shape UNFILLED. Only used when
    # there is no explicit binary static face and the gate is not a literal COL_A.
    resting_run = None
    resting_none = False  # the array's state-0 IS the no-colour sentinel (Transparent)
    if kind in _pc._CLOSED_KINDS and blk_static is None:
        rr = _geom.trailing_colour_run(po)
        if rr is not None:
            resting_run = _norm_colorref(rr)
            resting_none = resting_run is None

    array_rest = False
    if kind in _pc._CLOSED_KINDS:
        # closed-shape fill the way CtDraw's loader does it. BASE resting fill = style-block fill
        # slot when dwFlags&2 (the block overwrites the colour-state-array state-0 slot at load),
        # ELSE the colour-state-array STATE-0: resting_run (FLAG=0x23) / blk_static (FLAG=0x01
        # single pair via find_trailing_colour_pair) / COL_STA_0|COL_ARR_0 (the array's first
        # state). COL_MAXON / !COL_STATIC is the RUNTIME ON colour and never the
        # design-time fill, not even for a LITERAL COL_A.
        # _norm_colorref already maps the 0xFF high-byte sentinel to None (transparent).
        if po.subtype_size & 0x2:
            primary = blk_static
        else:
            # no style block -> the colour-state-array STATE-0 is the fill. Prefer the array's
            # first state (resting_run = FLAG-0x23 trailing scan; state0 = COL_STA_0/COL_ARR_0 bag)
            # over blk_static, which on a state-array shape is a mis-read trailing/panel/SxW colour.
            # A Transparent state-0 (0xFFFFFFFF) is a see-through shape, not a cue to fall back
            # to another colour.
            primary = (
                None
                if resting_none
                else resting_run
                if resting_run is not None
                else state0
                if state0 is not None
                else blk_static
            )
        array_rest = primary is not None
        static_fill_ok = primary is not None
    else:
        primary = bag_primary if bag_primary is not None else blk_static

    if kind in _pc._OPEN_KINDS:
        # line / pipe: the stroke is the LINE slot (slot 0), not the FILL slot. Prefer a bag
        # colour (COL_MAXON = the line's animated stroke), then the runtime LINE slot, then the
        # fill slot as a last resort. A VISIBLE-gated line is hidden in the static render (caller skips it); keep the colour.
        if line_null and kind is ObjectKind.LINE:
            # a free line whose own line colour is Transparent (the colour picker's
            # 0xFF.. value) draws nothing -- not the fill slot's colour
            st.line_colour = None
            st.line_style = 5
        else:
            # the stroke is the LINE slot (slot 0); COL_MAXON is the runtime stroke only.
            st.line_colour = line_blk if line_blk is not None else blk_static
        st.filled = False
    elif kind is ObjectKind.BUTTON:
        # a button's COL_MAXON pair is its CAPTION colour, not the face -- the face stays the
        # default 3D control colour. _apply_text_style handles the caption colour.
        if line_blk is not None:
            st.line_colour = line_blk
        st.filled = False
    elif kind in _pc._CLOSED_KINDS:
        if primary is not None and (static_fill_ok or array_rest) and not hollow:
            st.fill_colour = primary
            st.filled = True
        # edge: explicit block line slot, else the static fill colour (Citect draws the border
        # in the fill colour for flat 2D shapes). A HOLLOW shape (fill-style byte 0) keeps its
        # outline colour only.
        if line_null:
            st.line_colour = None
            st.line_style = 5  # GDI PS_NULL: no border
        elif resting_none and not (po.subtype_size & 0x2):
            # a Transparent state-0 on an art-stripped shape: its twin's line is the sentinel too
            # (CtDraw's .ctF record draws no pen); a twin with a
            # REAL line grafts it later (_graft_template_member_colours)
            st.line_colour = None
            st.line_style = 5
        elif hollow:
            st.line_colour = line_blk if line_blk is not None else primary
        else:
            st.line_colour = (
                line_blk if line_blk is not None else (primary if (static_fill_ok or array_rest) else None)
            )
    else:
        st.line_colour = primary if primary is not None else line_blk

    st.hilight_colour = hi
    st.shadow_colour = sh
    st.lolight_colour = lo
    return st


def _justify_from_props(props) -> TextJustify:
    """Text justification. The runtime bitfield is not byte-pinned; default LEFT. Hook so
    callers get a TextJustify, never None."""
    return TextJustify.LEFT


def _text_font_size(po) -> int:
    """Font pixel size from the serialized font metrics. The Citect font block is NOT a clean
    Win32 LOGFONT (the face is a separate lpstr) and lfHeight is not byte-pinned, so returns 0
    ('use renderer default'). Hook for a future byte-pin pass."""
    return 0


# Known font-face names: accept a real face, reject the text-extras heuristic's false positives
# (enum tokens 'FALSE'/'ENTER', stray words 'mt'/'asd', timestamps). Unrecognised -> renderer
# default (Arial).
_KNOWN_FONTS = frozenset(
    {
        "arial",
        "tahoma",
        "times new roman",
        "courier new",
        "verdana",
        "segoe ui",
        "ms sans serif",
        "small fonts",
        "calibri",
        "consolas",
        "lucida console",
        "wingdings",
        "symbol",
        "system",
        "fixedsys",
        "terminal",
        "comic sans ms",
        "georgia",
        "trebuchet ms",
        "impact",
        "microsoft sans serif",
        "webdings",
    }
)


def _norm_nl(s: str) -> str:
    """Normalise the CRLF that native strings store (e.g. 'Copper\\r\\nConc')."""
    return s.replace("\r\n", "\n").replace("\r", "\n")


# A Cicode FUNCTION CALL: an identifier (letter/digit/underscore/dot) glued directly to '('
# ('DspParetoText(', 'tag.PV('). A plain caption keeps whitespace before a parenthetical word
# ('Memory (MB)'), so this does not match it.
_re_expr = __import__("re").compile(r"[A-Za-z0-9_.]\(")


def _clean_display_text(raw: str) -> str:
    """Resolve a display expression's ``@(...)`` markers per the current label mode. Plain
    Cicode expressions (value placeholders) are left as-is."""
    s = raw.strip()
    if "@(" not in s:
        return s
    mode = _pc.get_label_mode()
    if mode == "literal" or os.environ.get("CTG_RAW_LABELS") == "1":
        return s  # keep '@(...)' verbatim (editor look)
    db = getattr(_pc._PAGE_CTX, "lang_db", None)
    if mode == "native" or db is None:
        repl = lambda m: m.group(1)  # noqa: E731
    else:
        repl = lambda m: db.translate(m.group(1), mode)  # noqa: E731
    cleaned = _re.sub(r"@\(([^()]*)\)", repl, s)
    return cleaned if "@(" not in cleaned else s


def _text_tail_colour(body: bytes) -> int | None:
    """A text record's own colour pair, from its body tail."""
    n = len(body)
    lo = max(4, n - 64)
    for off in range(n - 8, lo - 1, -1):
        try:
            c1, c2 = struct.unpack_from("<II", body, off)
        except struct.error:
            continue
        if c1 != c2 or c1 == 0 or (c1 >> 24) & 0xFF not in (0x00, 0xFF):
            continue
        # framed by the flag word: u16 1 right before the pair, or u32 1
        # (the Mixer 'M' caption stores u32 1 + magenta,magenta + lpstr 'M')
        if struct.unpack_from("<H", body, off - 2)[0] == 1 or struct.unpack_from("<I", body, off - 4)[0] == 1:
            return _norm_colorref(c1)
    return None


#: the display keys an element window takes from the template side
_DISPLAY_KEYS = ("TXT_OFF", "TXT_ON", "TXT_STR", "TEXT", "FORMAT")


def _element_window_props(po, props):
    """(props, raw overrides) of a text in an element window."""
    if not _pc.get_element_window() or po.type_code not in (0x05, 0x1001):
        return props, None
    at = getattr(_pc._PAGE_CTX, "record_at", None)
    if at is None or at[0] is not po:
        return props, None
    try:
        from ...format import genie_place as _gp

        r = _gp.decode(po)
    except Exception:  # noqa: BLE001  # swallow-ok: a record without bags shows as a page would
        return props, None
    if not r.entries:
        return props, None
    nested = at[1] in (getattr(_pc._PAGE_CTX, "nested_members", None) or ())
    src = r.entries[0][1] if nested else r.entries[0][2]
    if not src.pairs and not nested:
        return props, None  # no template bag (a plain element): nothing to swap
    from ...format.page import decode_value

    out = dict(props)
    raw = po.raw_properties()
    for k in list(out):
        if k.upper() in _DISPLAY_KEYS or k.upper().startswith(("STATE_", "ARRAY_")):
            del out[k]
            raw.pop(k, None)
    for k, v in src.pairs:
        if k.upper() in _DISPLAY_KEYS or k.upper().startswith(("STATE_", "ARRAY_")):
            out[k] = decode_value(k, v)
            raw[k] = v
    return out, raw


def _literal_display(po, caption, props=None):
    """The string CtDraw's editor draws for a text (what its extent measures):
    only in the literal (editor) label mode; None when not derivable. ``props``: raw
    property values in place of the record's (an element window's template side)."""
    if not (_pc.get_label_mode() == "literal" or os.environ.get("CTG_RAW_LABELS") == "1"):
        return None
    try:
        # the page's tag formats, else the lookup the editing layer installed -- passed, not
        # installed: nothing process-wide changes while a page derives
        tf = getattr(_pc._PAGE_CTX, "tag_formats", None)
        if tf is not None:
            return _tm.measured_string(po, caption, tag_format=tf, props=props)
        return _tm.measured_string(po, caption, props=props)
    except Exception:  # noqa: BLE001
        _note_exc("reader._literal_display")
        return None


#: button VIS_MODE -> CtDraw's draw style (CtDraw's table)
BUTTON_MODES = ("NORMAL", "BORDER_3D", "BORDER", "TARGET", "XP_STYLE")


def _apply_button_style(obj: GraphicObject, po, props) -> None:
    """A button's look as CtDraw draws it."""
    st = obj.style
    try:
        raw = po.raw_properties()
    except Exception:  # noqa: BLE001
        _note_exc("reader._apply_button_style")
        raw = {}
    mode = str(raw.get("VIS_MODE") or "").strip().upper()
    obj.raw["_button_mode"] = BUTTON_MODES.index(mode) if mode in BUTTON_MODES else 0
    if po.subtype_size & 0x2:
        # own face colour: the style block's fill slot, used only
        # when its attribs byte has bit 0x4
        try:
            cb = _geom.find_colour_block(po)
        except Exception:  # noqa: BLE001
            _note_exc("reader._apply_button_style")
            cb = None
        if cb is not None and cb.flags[3] & 0x04:
            face = _norm_colorref(cb.slots[1][0])
            if face is not None:
                obj.raw["_button_face"] = face
    try:
        font = _pl.walk(po).get("font")
    except Exception:  # noqa: BLE001
        _note_exc("reader._apply_button_style")
        font = None
    if not font:
        return
    if font["face"].strip():
        st.font = font["face"]
    if 0 < font["size"] < 0x80:
        st.font_size = font["size"]
    at = font["attribs"]
    st.bold = bool(at & 0x10)
    st.italic = bool(at & 0x20)
    st.underline = bool(at & 0x40)
    st.strikeout = bool(at & 0x80)
    # the low nibble aligns the lines of a multi-line caption within its block
    # (1 DT_RIGHT, 2 DT_CENTER, else DT_LEFT)
    st.justify = (
        TextJustify.RIGHT
        if (at & 0x0F) == 1
        else TextJustify.CENTRE
        if (at & 0x0F) == 2
        else TextJustify.LEFT
    )
    col = _design_colour(font["text"])
    if col is not None:
        st.text_colour = col
    obj.raw["_button_font"] = True


def _apply_text_style(obj: GraphicObject, po, props, once=None) -> None:
    """Fill in text style fields."""
    st = obj.style
    tail = None
    # an element window shows the template side of the display keys (common.get_element_window)
    props, raw_over = _element_window_props(po, props)
    if po.type_code == 0x05:
        try:
            tail = _geom.parse_text_tail(po)
        except Exception:  # noqa: BLE001
            _note_exc("reader._apply_text_style")
            tail = None
    if tail is not None:
        if tail["face"].strip():
            st.font = tail["face"]
        if 0 < tail["size"] < 0x80:
            st.font_size = tail["size"]
        st.vertical = tail["orient"] == 3
        # the tail 'orient' IS the rotation: 1=0deg, 3=90CW, 2=180, 4=270CW
        turns = _geom.ORIENT_TO_TURNS.get(tail["orient"], 0)
        if turns:
            obj.raw["_text_turns"] = turns
        try:
            # a text that keeps a matrix (a mirrored text resized) is drawn through it,
            # its font scaled by the matrix
            ts = _geom.text_scale(po)
        except Exception:  # noqa: BLE001
            _note_exc("reader._apply_text_style")
            ts = None
        if ts is not None:
            obj.raw["_text_scale"] = ts
        try:
            cb = _colour_block(po, once)
        except Exception:  # noqa: BLE001
            _note_exc("reader._apply_text_style")
            cb = None
        if cb is not None:
            attribs = cb.flags[3]
            st.justify = (
                TextJustify.RIGHT
                if (attribs & 0x0F) == 1
                else TextJustify.CENTRE
                if (attribs & 0x0F) == 2
                else TextJustify.LEFT
            )
            st.bold = bool(attribs & 0x10)
            st.italic = bool(attribs & 0x20)
            st.underline = bool(attribs & 0x40)
            st.strikeout = bool(attribs & 0x80)
            st.three_d = cb.flags[0] & 0x0F
            # the block's LINE slot is the text colour; the no-colour sentinel (Transparent)
            # draws nothing (COM: CtDraw's text colour -1)
            col = _design_colour(cb.slots[0])
            if col is not None:
                st.text_colour = col
            else:
                obj.raw["_text_transparent"] = True
    if po.type_code == 0x1001:
        _apply_button_style(obj, po, props)
    # font face fallback: only accept a recognised Windows face name (the page-level heuristic
    # yields false positives on this type)
    if not st.font and po.font_face and po.type_code in (0x05, 0x1004, 0x1001):
        if po.font_face.strip().lower() in _KNOWN_FONTS:
            st.font = po.font_face

    # display text: prefer the first @() expression, else a TXT_*/TEXT bag value (buttons carry
    # their caption in TEXT). Icon-reference captions (#icons.* / @ exprs) are excluded.
    def _label(val: str) -> str:
        raw = str(val)
        s = _clean_display_text(raw)
        if not s or s == "a":
            return ""
        # a TRANSLATABLE label (raw carried '@(...)') is a real caption in every mode -- keep it
        # (literal, native, or translation) even though the literal form has parentheses.
        if "@(" in raw:
            return _norm_nl(s)
        # a bare cicode/value expression (or the 1-char 'analog' default 'a') has no static
        # label -> the numeric placeholder below stands in. A 1-char UPPERCASE caption is a real
        # label (the Mixer motors' 'M').
        # Parens alone do NOT make it an expression: a plain caption can carry a trailing
        # parenthetical word ('Free Virtual Memory (MB)', '(0 - 30 t)'). Only reject a genuine
        # Cicode reference: a FUNCTION CALL ('DspParetoText(3)', 'MultiSignatureTagWrite(...)' --
        # an identifier glued to '('), or a FORMATTED VALUE ('(tag.PV):#.#' -- a '):' format tail).
        if s.startswith(("@", "#", "$")):
            return ""
        # (a format tail is something after the colon: a caption ending in a parenthetical
        # and a colon, 'Flow since last flush (calculated):', is a caption)
        if _re_expr.search(s) or _re.search(r"\):[^():]{1,16}$", s):
            return ""
        return _norm_nl(s)

    # A runtime VALUE display (TXT_STR present, no static TXT_OFF/TXT_ON/TEXT
    # caption) shows its value placeholder -- the format mask after ':' or the
    # cicode echo -- NOT a harvested @() string. On these records the only @()
    # in the body is the COMMENT bag (a translatable description that
    # _parse_text_extras also collects), so letting text_exprs[0] win here
    # paints the comment over the value.
    _value_display = "TXT_STR" in props and not ({"TXT_OFF", "TXT_ON", "TEXT"} & set(props))
    if _value_display:
        # The caption a value display shows is the @("...") literal EMBEDDED in
        # TXT_STR (CtDraw resolves StrToLocalText("@(Go to the top)") -> the
        # caption text), NOT the COMMENT bag's @() that _parse_text_extras also
        # harvests. Pull that embedded literal when present; otherwise fall
        # through to the mask / cicode-echo placeholder below.
        em = _re.search(r'"(@\([^"]*?\))"', str(props["TXT_STR"].raw))
        if em:
            st.text = _label(em.group(1))
    if po.text_exprs and not _value_display:
        st.text = _label(po.text_exprs[0])
    if not st.text and "TXT_OFF" in props:
        st.text = _label(props["TXT_OFF"].raw)
    if not st.text and "TXT_ON" in props:
        st.text = _label(props["TXT_ON"].raw)
    if not st.text and "TEXT" in props:
        st.text = _label(props["TEXT"].raw)

    # numeric display expression "(<cicode>):<format>": CtDraw shows the FORMAT tail as the
    # placeholder ('(...FLW01.PV):#.#' -> '#.#', ':#!' -> '#!').
    if not st.text and "TXT_STR" in props:
        m = _re.search(r"\):([^():]{1,16})$", str(props["TXT_STR"].raw))
        if m and m.group(1).strip():
            st.text = m.group(1).strip()

    # numeric / value display: CtDraw shows the FORMAT mask ('####.#') as the placeholder for a
    # runtime-valued number. The mask can live in FORMAT, in TXT_OFF (the Mixer level displays
    # store TXT_OFF='####.#' + TXT_STR=<tag>), or after the ':' in TXT_STR.
    if not st.text:
        rp = po.raw_properties() if hasattr(po, "raw_properties") else {}

        def _mask(s):
            s = str(s or "").strip()
            return s if (s and set(s) <= set("#0.,-+ !")) else ""

        # TXTMODE is the display-value TYPE digit (catalog: 0..4; factory writes 3 for a numeric
        # display). TXTMODE=4 is a STRING/cicode-expression display -- it shows its TXT_STR
        # expression, NOT a numeric mask, even when TXT_OFF carries a leftover '####.#'
        # (CtDraw echoes '=<expr>'). Only TXTMODE 3/numeric paths take the mask.
        txtmode = str(rp.get("TXTMODE", "")).strip()
        numeric = txtmode != "4" and (
            obj.kind is ObjectKind.NUMBER
            or "FORMAT" in rp
            or _mask(rp.get("TXT_OFF"))
            or any(
                k in rp for k in ("TXT_A", "TXT_B", "TXT_C", "ENG_ZERO", "ENG_FULL", "RAW_ZERO", "RAW_FULL")
            )
        )
        has_value_expr = bool(po.text_exprs) or any(
            k in rp for k in ("TXT_OFF", "TXT_ON", "TXT_STR", "TXT_A", "TXT_B", "TXT_C", "FORMAT")
        )
        if numeric and has_value_expr:
            st.text = _mask(rp.get("FORMAT")) or _mask(rp.get("TXT_OFF")) or "####.#"
        elif has_value_expr:
            # a string-mode display whose value is a runtime cicode expression:
            # CtDraw's EDITOR echoes the expression with a leading '=' (the
            # literal/design-time look); runtime/native keeps a generic 'abc'.
            expr = str(props["TXT_STR"].raw).strip() if "TXT_STR" in props else ""
            editor = _pc.get_label_mode() == "literal" or os.environ.get("CTG_RAW_LABELS") == "1"
            if expr and editor:
                st.text = "=" + _norm_nl(expr)
            elif editor:
                # no expression -> an EMPTY string display: CtDraw's editor draws
                # NOTHING, so leave it blank rather than the generic 'abc' placeholder.
                st.text = ""
            else:
                # (Simulate Settings: 'abc' or nothing until the simulator sends the value)
                st.text = _pc.get_value_placeholder()
    # CtDraw's EDITOR draws exactly the string its extent measures (the text
    # draw and the extent both take):
    # a static caption literally, a numeric display's FORMAT mask (the tag's
    # own for a bare tag), a string display '=' + TXT_STR. In the editor
    # (literal) label mode show that; the other modes keep the translated /
    # native captions and placeholders above.
    _rp = po.raw_properties() if hasattr(po, "raw_properties") else {}
    _mode = str(_rp.get("TXTMODE", "")).strip()
    # a value display (3 numeric / 4 string) needs no caption: its string is
    # derived from TXT_STR even on an art-stripped genie member (no tail)
    if po.type_code == 0x05 and (tail is not None or _mode in ("3", "4")):
        shown = _literal_display(po, tail["text"] if tail else None, raw_over)
        if shown is not None:
            st.text = "" if not shown.strip() else _norm_nl(shown).rstrip("\n")
    # text colour: use COL_MAXON.on only when it is NOT an animation 'on' state (animated text
    # colour defaults to black statically, like the thumbnail's labels). A text record's own
    # colour lives in its trailing colour pair (the Mixer motor 'M' carries magenta 0x00FF00FF).
    # Else None so the renderer picks a dark contrast colour (Citect's static labels are mostly
    # black).
    if po.type_code == 0x1001 and obj.raw.get("_button_font"):
        return  # a button's caption colour is its font block's
    raw_p = po.raw_properties() if hasattr(po, "raw_properties") else {}
    if tail is not None and st.text_colour is not None and (po.subtype_size & 0x2):
        # the text's OWN style-block colour is what the editor draws: COL_MAXON is applied only
        # at runtime, so it must not replace it, nor may an absent bag /
        # tail pair blank it to black
        pass
    elif not _is_colour_animated(raw_p):
        # a "!*#1403"-style key LINKS this record's COL_MAXON to an
        # AdvancedAnimation anchor that drives its colour at RUNTIME, so the bag
        # colour is the animation's active state, not the static one CtDraw draws.
        # Use the record's own trailing pair only.
        link_driven = ("COL_MAXON" in raw_p or "!COL_STATIC" in raw_p) and any(
            str(k).startswith("!*#") for k in raw_p
        )
        if link_driven:
            st.text_colour = _text_tail_colour(po.body_raw)
        else:
            st.text_colour = (
                _pair_on(props.get("COL_MAXON"))
                or _pair_on(props.get("!COL_STATIC"))
                or _text_tail_colour(po.body_raw)
            )
    else:
        # a COL_STA/COL_ARR-driven text rests in STATE 0, like shapes do
        state0 = _pair_static(props.get("COL_STA_0")) or _pair_static(props.get("COL_ARR_0"))
        if state0 is not None:
            st.text_colour = state0
