"""Reader constants, object kinds, derive options and geometry helpers."""

from __future__ import annotations

import os
import re  # noqa: F401 (re-exported)
import struct
from functools import lru_cache
from ...format.cnum import c_atoi
from ...format.page import _XFORM_DOUBLES, _geom_name_len
from ...model.document import (
    Animations,
    DisplayValue,
    FillColour,
    FillColourThreshold,
    FillLevel,
    GraphicObject,
    InputKeyboard,
    InputTouch,
    Movement,
    ObjectKind,
    SymbolSetAnim,
    Transform,
    Visibility,
)
from ...format.textcodec import CODEC
import re as _re  # noqa: F401 (re-exported)
import threading
import contextvars
import contextlib
from ...format import colourstate as _cs
from ...format import symbolset as _S
from ..cicode_rest import visible_at_rest
from ...infra.logsetup import note_exception as _note_exc
from ...format import codes as _codes
from . import common as _pc, style as _ps, symbols as _py, graft as _pg  # noqa: F401


_ICON_FOR_TYPE = None


def _class_icons() -> dict:
    """format.class_icons.ICON_FOR_TYPE (plain data: the reader stays Qt-free)."""
    global _ICON_FOR_TYPE
    if _ICON_FOR_TYPE is None:
        from ...format.class_icons import ICON_FOR_TYPE

        _ICON_FOR_TYPE = ICON_FOR_TYPE
    return _ICON_FOR_TYPE


# Win32 COLORREF "no colour / default / transparent" sentinel (high byte 0xFF).
_NO_COLOUR = 0xFF000000


# Closed shapes: a static colour fills them. Open shapes (line/pipe) take it as the stroke.
_CLOSED_KINDS = frozenset(
    {
        ObjectKind.RECTANGLE,
        ObjectKind.ELLIPSE,
        ObjectKind.POLYGON,
        ObjectKind.SYMBOL_SET,
        ObjectKind.NUMBER,
        ObjectKind.BUTTON,
    }
)


_OPEN_KINDS = frozenset({ObjectKind.LINE, ObjectKind.PIPE})


# Drawable LEAF kinds. A record of one of these that OWNS children is NOT a container: a
# drawable-shape parent's children are always behaviour records (Touch 0x1010 / Keyboard 0x100B),
# never drawable members (a clickable button face, an LCD caption), so it keeps its kind and paints
# its own art. A real group/genie is a tc=0x08 container (kind group/symbol_set/...), excluded here.
# A TREND owns Keyboard children the same way (its sheet has Keyboard Commands).
_LEAF_KINDS = frozenset(
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


# type_codes that carry a drawable static-colour block/bag worth style-decoding. Bitmap (7)
# and Group (8) containers are excluded: no fill of their own.
_STYLED_TYPE_CODES = frozenset({1, 2, 3, 4, 5, 11, 12, 15, 16, 17, 4097, 4100, 4109})


# type_codes that are NOT independently drawn (animation anchors, input behaviours, editor-tool
# sentinels). VERIFIED against CtDraw's object classes (catalog object_types[]): AnPoint 0x1000,
# AdvancedAnimation 0x1009, Keyboard 0x100b, Touch 0x1010, String 0x1005, TextAn 0x1006,
# ColourAn 0x1008, SizeMove 0x100e, Slider 0x100f, Sparkle 6, MetaFile 9, Draw 2, Rail 0x12,
# DummyMarker 0xffff, Select/Edit 0. No placeholder box for these. ActiveX/WebContent
# (0xd/0x13) ARE visible controls -> kept.
_NON_DRAWABLE_TYPE_CODES = frozenset(
    {
        0x1000,
        0x1009,
        _codes.TC.KEYBOARD,
        _codes.TC.TOUCH,
        0x1005,
        0x1006,
        0x1008,
        0x100E,
        0x100F,
        0x06,
        0x09,
        0x02,
        0x12,
        0xFFFF,
        0x00,
    }
)


CATALOG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "format", "catalog.json")


@lru_cache(maxsize=1)
def _catalog() -> dict:
    import json

    try:
        with open(os.path.normpath(CATALOG_PATH), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        _note_exc("reader._catalog")
        return {}


@lru_cache(maxsize=1)
def _key_family() -> dict:
    """Map serialized property key -> animation family (from the catalog)."""
    out = {}
    for p in _catalog().get("properties", []):
        k = p.get("key")
        if k:
            out[k] = p.get("family", "")
    return out


@lru_cache(maxsize=1)
def _visibility_keys() -> frozenset:
    """The keys of the catalog's Visibility family (_apply_animations)."""
    return frozenset(k for k, f in _key_family().items() if f == "Visibility")


# Catalog `kind` strings -> ObjectKind enum values (spellings differ, e.g. symbol_set vs
# the catalog's symbolset).
_CATALOG_KIND_TO_ENUM = {
    "line": ObjectKind.LINE,
    "rectangle": ObjectKind.RECTANGLE,
    "ellipse": ObjectKind.ELLIPSE,
    "polygon": ObjectKind.POLYGON,
    "pipe": ObjectKind.PIPE,
    "text": ObjectKind.TEXT,
    "number": ObjectKind.NUMBER,
    "button": ObjectKind.BUTTON,
    "symbolset": ObjectKind.SYMBOL_SET,
    "symbol_set": ObjectKind.SYMBOL_SET,
    "trend": ObjectKind.TREND,
    "cicode": ObjectKind.CICODE_OBJECT,
    "cicode_object": ObjectKind.CICODE_OBJECT,
    "symbol": ObjectKind.SYMBOL,
    "genie": ObjectKind.GENIE,
    "composite_genie": ObjectKind.COMPOSITE_GENIE,
    "group": ObjectKind.GROUP,
    # kinds with no dedicated enum member -> UNKNOWN:
    "web": ObjectKind.UNKNOWN,
    "advanced": ObjectKind.UNKNOWN,
    "template": ObjectKind.UNKNOWN,
    "bitmap": ObjectKind.UNKNOWN,
    "unknown": ObjectKind.UNKNOWN,
}


@lru_cache(maxsize=1)
def _typecode_kind() -> dict:
    """type_code -> ObjectKind."""
    out = {}
    for ot in _catalog().get("object_types", []):
        kind = _CATALOG_KIND_TO_ENUM.get((ot.get("kind") or "").strip(), ObjectKind.UNKNOWN)
        for tc in ot.get("type_codes") or []:
            out.setdefault(int(tc), kind)
    return out


# --------------------------------------------------------------------------- geometry
def _read_geometry(payload: bytes, body_offset: int):
    """Decode the geometry header. None on overrun."""
    n = len(payload)
    o = body_offset
    if o + 6 > n:
        return None
    reserved = struct.unpack_from("<H", payload, o)[0]
    handle = struct.unpack_from("<H", payload, o + 2)[0]
    name_end = _geom_name_len(payload, o)
    if name_end == o:
        return None
    name = payload[o + 6 : name_end].decode(CODEC, "replace") if name_end > o + 6 else ""
    o = name_end
    transforms = []
    for _ in range(3):
        if o >= n:
            return None
        mode = payload[o]
        o += 1
        ndoubles = _XFORM_DOUBLES.get(mode, 6 if mode > 3 else 0)
        if o + ndoubles * 8 > n:
            return None
        vals = struct.unpack_from("<%dd" % ndoubles, payload, o) if ndoubles else ()
        transforms.append((mode, vals))
        o += ndoubles * 8
    if o + 4 + 8 + 8 > n:
        return None
    anchor = struct.unpack_from("<2h", payload, o)
    o += 4
    rect1 = struct.unpack_from("<4h", payload, o)
    o += 8
    rect2 = struct.unpack_from("<4h", payload, o)
    o += 8
    return {
        "reserved": reserved,
        "handle": handle,
        "name": name,
        "transforms": transforms,
        "anchor": anchor,
        "rect1": rect1,
        "rect2": rect2,
        "end": o,
    }


def _transform_from_header(transforms) -> Transform:
    """Slot 1 (Local) as a 2x3 affine."""
    if not transforms:
        return Transform()
    mode, vals = transforms[0]
    if mode == 0 or not vals:
        return Transform()
    a, b, c, d, h, k = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
    if len(vals) >= 6:
        a, b, c, d, h, k = vals[:6]
    elif len(vals) == 4:
        a, d, h, k = vals
    elif len(vals) == 2:
        if mode & 0x0F == 2:
            a, d = vals
        else:
            h, k = vals
    return Transform(a, b, c, d, h, k)


# ---- multi-language label mode (see io/lang.py) ---------------------------
# `@(Native)` is Citect's translatable-label marker. Render mode is set globally by the app and
# read here at parse time:
#   'literal'  -> '@(Full Milk)' verbatim, like CtDraw's editor canvas
#   'native'   -> 'Full Milk' (native string == runtime render when untranslated; default for
#                 headless/corpus parsing so render_compare + tests are unaffected)
#   '<Language display name>' -> the LOCAL translation, e.g. German -> 'Vollmilch'
# The app flips the user-facing default to 'literal' and offers the languages.
_LABEL_MODE = "native"


#: the per-page context a derive sets (_set_page_context) and its text displays read: .lang_db
#: (the language tables a translated label mode reads) and .tag_formats (the tag formats a
#: literal display measures with) -- per THREAD, so a page derived on another thread (a test, a
#: script) never swaps the context of a derive in progress
_PAGE_CTX = threading.local()


#: scoped overrides of the label mode / composite drawing (derive_options); None = the
#: process setting. contextvars, so an override is the caller's own (its thread / context)
#: and is always put back, never a global flipped and restored by hand.
_LABEL_MODE_VAR: "contextvars.ContextVar" = contextvars.ContextVar("ctg_label_mode", default=None)


_COMPOSITE_VAR: "contextvars.ContextVar" = contextvars.ContextVar("ctg_composite_members", default=None)

#: the derive is a library ELEMENT WINDOW (ElementDocument): a text shows the template side of
#: its bags like CtDraw's element editor (its own template as typed for a direct member,
#: '%Label%' / '=dateTimeRead(%TAG%)'; the binding chain for a member of a nested genie,
#: '%Caption=Example%'), not the value substituted with the element's unbound level.
_ELEMENT_VAR: "contextvars.ContextVar" = contextvars.ContextVar("ctg_element_window", default=False)


def get_element_window() -> bool:
    return bool(_ELEMENT_VAR.get())


#: the text a string display whose value comes from code shows before it has one, outside the
#: editor's As Stored labels (the Simulate window until the simulator sends the value): the
#: generic 'abc' or nothing (Simulate Settings). Part of the derive context.
_VALUE_PLACEHOLDER = "abc"
_PLACEHOLDER_VAR: "contextvars.ContextVar" = contextvars.ContextVar("ctg_value_placeholder", default=None)

#: the editor's drawing choices (Settings > Canvas), part of the derive context:
#: 'stacked_labels' = 'top' (only the topmost copy of a stack of identical labels, each gated
#: by its own visibility: ctgedit's clean look) or 'all' (every copy, as the Graphics Builder
#: draws them: a bold blur where they overprint)
_RENDER_OPTIONS = {"stacked_labels": "top"}
_RENDER_CHOICES = {"stacked_labels": ("top", "all")}


def set_render_option(name: str, value: str) -> None:
    """Set one of the editor's drawing choices (:data:`_RENDER_OPTIONS`); an unknown value
    keeps the current one."""
    if value in _RENDER_CHOICES.get(name, ()):
        _RENDER_OPTIONS[name] = value


def get_render_option(name: str) -> str:
    return _RENDER_OPTIONS.get(name, "")


def render_options_key() -> tuple:
    """The drawing choices as a derive-context key (a change derives every unit again)."""
    return tuple(sorted(_RENDER_OPTIONS.items()))


def set_value_placeholder(text: str) -> None:
    """The PROCESS value placeholder ('abc' or ''); a derive that needs another one uses
    :func:`derive_options`."""
    global _VALUE_PLACEHOLDER
    _VALUE_PLACEHOLDER = "" if not text else "abc"


def get_value_placeholder() -> str:
    v = _PLACEHOLDER_VAR.get()
    return _VALUE_PLACEHOLDER if v is None else v


def set_label_mode(mode: str) -> None:
    """Set the PROCESS label render mode ('literal' | 'native' | language name): the editor's
    choice (View > Labels). A derive that needs another one uses :func:`derive_options`."""
    global _LABEL_MODE
    _LABEL_MODE = (mode or "native").strip() or "native"


def get_label_mode() -> str:
    v = _LABEL_MODE_VAR.get()
    return _LABEL_MODE if v is None else v


@contextlib.contextmanager
def derive_options(
    label_mode: "str | None" = None,
    composite_members: "bool | None" = None,
    value_placeholder: "str | None" = None,
    element_window: "bool | None" = None,
):
    """Derive with another label mode / composite drawing inside the ``with`` block (the
    Simulate window's runtime look, a digest in a given mode); the process settings are not
    touched. ``element_window``: the derive is an element window
    (:func:`get_element_window`)."""
    tokens = []
    if element_window is not None:
        tokens.append((_ELEMENT_VAR, _ELEMENT_VAR.set(bool(element_window))))
    if label_mode is not None:
        tokens.append((_LABEL_MODE_VAR, _LABEL_MODE_VAR.set((label_mode or "native").strip() or "native")))
    if composite_members is not None:
        tokens.append((_COMPOSITE_VAR, _COMPOSITE_VAR.set(bool(composite_members))))
    if value_placeholder is not None:
        tokens.append((_PLACEHOLDER_VAR, _PLACEHOLDER_VAR.set("abc" if value_placeholder else "")))
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


#: how a composite genie is drawn: False = from its composition cache element (its records
#: expanded into the placement box, the instance's own member records suppressed), True = from its own member records with the appearance of the records
#: that OWN them (the runtime: its .ctF holds the page's member records with that appearance
#: baked in, ctf._Owners -- the Simulate window's runtime look). Part of the derive context.
_COMPOSITE_MEMBERS = True


def set_composite_members(on: bool) -> None:
    """Draw composite genies from their own member records (True, the default: what the
    runtime and the Graphics Builder's editor show) or from their composition cache element
    (False)."""
    global _COMPOSITE_MEMBERS
    _COMPOSITE_MEMBERS = bool(on)


def get_composite_members() -> bool:
    v = _COMPOSITE_VAR.get()
    return _COMPOSITE_MEMBERS if v is None else v


# --------------------------------------------------------------------------- kind
def _kind_for(type_code: int, raw_props: dict) -> ObjectKind:
    # type_code -> kind comes from CtDraw's object classes (the on-disk
    # type_code names the class).
    tcmap = _typecode_kind()
    mapped = tcmap.get(type_code)
    if mapped is not None and mapped is not ObjectKind.UNKNOWN:
        return mapped
    # An AdvancedAnimation (0x1009) carrying a SYMBOL bag value DISPLAYS that symbol state (the
    # chevron value art 'sa_workspace.text_left', classic 'icons_16x16.alarm_act' sets) -- CtDraw
    # draws the active state, so treat it as a drawable placed symbol-set.
    if type_code == 0x1009 and raw_props.get("SYMBOL"):
        return ObjectKind.SYMBOL_SET
    # a V3/V4 String (0x1005) shows its OFF text in a box like a Number (Example Genies:
    # '@(OK)' in FontSP); without texts it is a genie template's placeholder
    if type_code == 0x1005 and (raw_props.get("OFF_TEXT") or raw_props.get("ON_TEXT")):
        return ObjectKind.NUMBER
    # An explicit catalog mapping to "unknown" is a VERIFIED non-drawable: animation/behavior/
    # anchor classes (AnPoint 0x1000, Keyboard 0x100b, Touch 0x1010, ColourAn, SizeMove, ...),
    # the ActiveX/WebContent controls, the 0xffff DummyMarker sentinel. NOT placed symbols, so
    # skip the SYMBOL heuristic below.
    if mapped is ObjectKind.UNKNOWN:
        return ObjectKind.UNKNOWN
    if type_code == 5:
        return ObjectKind.TEXT
    # a library/symbol-backed code (high nibble 0x1) absent from the registry table is most
    # likely an unseen placed-symbol variant
    if (type_code & 0xF000) == 0x1000:
        return ObjectKind.SYMBOL
    # heuristics from property keys present (fallback for unmapped primitives)
    keys = set(raw_props)
    if "TXT_OFF" in keys:
        return ObjectKind.TEXT
    if {"TXT_A", "TXT_B", "TXT_C"} & keys:
        return ObjectKind.NUMBER
    if any(k.startswith("MVX") or k.startswith("MVY") for k in keys):
        return ObjectKind.UNKNOWN  # animated primitive; kind still unknown
    return ObjectKind.UNKNOWN


# --------------------------------------------------------------------------- animations
#: the records whose CMD is a touch command: a Touch input record and a V3 / V4 button (a
#: Cicode object's CMD runs while the page shows, a Keyboard record's on its key sequence, a
#: trend's on its pens)
_TOUCH_TCS = frozenset({0x1010, 0x1001})


def _text_display_anim(raw_props: dict) -> DisplayValue:
    """A text's Display Value as the object sheet reads it (format/dspval.read, by its
    TXTMODE): On / Off, Multi-state and Array show TXT_A, Numeric the expression of its
    TXT_STR '(expr):format' (the format apart), String TXT_STR."""
    from ...format import dspval as _dsp

    mode = c_atoi(raw_props.get("TXTMODE"))
    txt_str = str(raw_props.get("TXT_STR") or "")
    fmt = ""
    if mode == _dsp.NUMERIC:
        expr, fmt = _dsp.split_numeric(txt_str)
    elif mode == _dsp.STRING:
        expr = txt_str
    else:
        expr = str(raw_props.get("TXT_A") or "")
    return DisplayValue(expression=expr, is_text=mode != _dsp.NUMERIC, format=fmt)


def _fill_colour_anim(raw_props: dict) -> FillColour:
    """A fill colour animation as the object sheet reads it (format/fillcolour, by its
    COLMODE): its expression key (COL_A, COL_ARR or COL_EXP); a Threshold or Gradient
    animation also has its COL_THR_n rows (operator bit 0x1 = greater, 0x2 = or equal)."""
    from ...format import fillcolour as _fc

    mode = c_atoi(raw_props.get("COLMODE"))
    expr = str(raw_props.get(_fc.EXPRESSION_KEY.get(mode, "COL_A")) or "")
    rows = []
    if mode in _fc.RANGED:
        for i in range(_fc.MAX_THRESHOLDS):
            t = _cs.parse_threshold(str(raw_props.get("COL_THR_%d" % i) or ""))
            if t is None:
                continue
            greater, equal, value, on, _off = t
            rows.append(
                FillColourThreshold(
                    expression=expr,
                    colour=on & 0xFFFFFF,
                    limit=float(value),
                    operator=(0x1 if greater else 0) | (0x2 if equal else 0),
                )
            )
    return FillColour(range_on=mode in _fc.RANGED, thresholds=rows or [FillColourThreshold(expression=expr)])


def _apply_animations(obj: GraphicObject, raw_props: dict, type_code=None) -> None:
    a: Animations = obj.animations
    # (a key of the Visibility family: a set test in C, not the family of every key)
    if "VISIBLE" in raw_props or not _visibility_keys().isdisjoint(raw_props):
        obj_expr = raw_props.get("VISIBLE") or raw_props.get("VISIBLE_") or ""
        a.visibility = Visibility(expression=str(obj_expr))
        # rest-state verdict: True (shown), False (hidden), None (not statically evaluable).
        # The renderer hides rest-False objects, as Citect's static render does.
        rv = visible_at_rest(str(obj_expr))
        if rv is not None:
            obj.raw["_rest_visible"] = rv
    # one pass over the keys for the prefixes (hot path)
    moved = colour = fill = False
    moving = {}
    for k, v in raw_props.items():
        p2 = k[:2]
        if p2 == "MV" or p2 == "SZ" or k[:3] == "ROT":
            moved = True
            moving[k] = v
        elif k[:3] == "COL":
            colour = True
        elif k[:3] == "FIL":
            fill = True
    tc = type_code if type_code is not None else obj.raw.get("_type_code")
    if moved:
        a.movement = Movement(
            rotation_expr=str(raw_props.get("ROT", "")),
            scale_h_expr=str(raw_props.get("SZX", "")),
            scale_v_expr=str(raw_props.get("SZY", "")),
            move_h_expr=str(raw_props.get("MVX", "")),
            move_v_expr=str(raw_props.get("MVY", "")),
            raw=moving,
        )
    if colour:
        a.fill_colour = _fill_colour_anim(raw_props)
    if fill:
        a.fill_level = FillLevel(expression=str(raw_props.get("FIL", "")))
    if tc in (0x1004, 0x1005) and "EXPR" in raw_props:
        # a V3 / V4 Number or String shows its EXPR
        a.display_value = DisplayValue(expression=str(raw_props.get("EXPR") or ""), is_text=tc == 0x1005)
    elif "TXT_OFF" in raw_props or "TXTMODE" in raw_props:
        a.display_value = _text_display_anim(raw_props)
    if ("CMD" in raw_props or "ACTION" in raw_props) and tc in _TOUCH_TCS:
        a.touch.append(InputTouch(command=str(raw_props.get("CMD", ""))))
    if "KEY_SEQ" in raw_props:
        a.keyboard.append(
            InputKeyboard(
                key_sequence=str(raw_props.get("KEY_SEQ", "")), command=str(raw_props.get("CMD", ""))
            )
        )


def _apply_symbol_set(obj: GraphicObject, raw_props: dict) -> None:
    """A symbol set's (tc 0x0C) TXTMODE is its TYPE (0 On / Off, 1 Multi-state,
    2 Array, 3 Animated) and TXT_A..TXT_E / SET_ARR its expressions -- not a
    text display (format/symbolset.py)."""
    obj.animations.symbol_set = SymbolSetAnim(
        type=_S.set_type(raw_props), expressions=_S.expressions(raw_props)
    )
    if "TXT_OFF" not in raw_props:
        obj.animations.display_value = None
