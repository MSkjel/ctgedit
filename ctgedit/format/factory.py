"""Create new drawing objects from blank records."""

from __future__ import annotations

import copy
import os
import struct
from functools import lru_cache
from typing import List, Optional, Tuple

from . import geometry as G
from . import records as R
from .page import PageObject, _parse_object
from . import writer
from .textcodec import CODEC

# editor-facing tool kinds -> blank record names (records.BUILDERS, else CONTROL_SOURCES)
TOOL_SEEDS = {
    "line": "line",
    "rectangle": "rectangle",
    "rectangle_outline": "rectangle_outline",
    "ellipse": "ellipse",
    "ellipse_outline": "ellipse_outline",
    "polygon": "polygon",
    "polyline": "polygon",  # open polygon = same record kind
    "pipe": "pipe",
    "text": "text",
    "number": "number",  # CtDraw 'number' = text w/ numeric display value
    "button": "button",
    "symbol": "symbol",
    # legacy V3/V4 library-class records
    "trend": "trend",  # 0x1003 pen chart
    "string": "string",  # 0x1005 two-state text
    "cicode_object": "cicode_object",  # 0x1009 AdvancedAnimation
    "animation_keyboard": "keyboard",  # 0x100B keyboard command record
    "pagewide_keyboard": "keyboard",
    "animated_symbol": "animated_symbol",  # 0x100D AnimSymbol
    "bar_graph": "bar_graph",  # 0x1002 Bar level indicator
    "slider": "slider",  # 0x100F Slider input
    "digital_text": "digital_text",  # 0x1006 TextAn
    "color_flood": "color_flood",  # 0x1008 ColourAn
    # ActiveX family: the body embeds the OLE persisted control state, so
    # each insert copies the control's record from the install (CONTROL_SOURCES)
    "activex": "activex",  # 0x0D generic (CiMeter)
    "process_analyst": "process_analyst",  # 0x0D Process Analyst control
    "scheduler": "scheduler",  # 0x0D Scheduler control
    "web_content": "web_content",  # 0x13 Web Content
    # on no page the install ships: available_kinds() leaves them out
    "db_exchange": "db_exchange",  # 0x0D DatabaseExchange.DataManager
    "pelco": "pelco",  # 0x0D Pelco camera viewer (deprecated)
}

# seed bag values a NEW object must not keep (tag expressions / commands)
_CLEAR_ON_CREATE = {
    "trend": (
        "PEN1",
        "PEN2",
        "PEN3",
        "PEN4",
        "PEN5",
        "PEN6",
        "PEN7",
        "PEN8",
        "SPCTag",
        "EXPR",
        "CMD",
        "COMMENT",
    ),
    "string": ("EXPR", "TAG", "COMMENT"),
    "cicode_object": (),  # removed instead: _DROP_ON_CREATE
    "animation_keyboard": ("KEY_SEQ", "CMD"),
    "pagewide_keyboard": ("KEY_SEQ", "CMD"),
    "animated_symbol": ("EXPR", "TAG", "COMMENT"),
    "bar_graph": ("EXPR", "TAG", "COMMENT"),
    "slider": ("TAG", "EXPR", "COMMENT", "Privilege"),
    "digital_text": ("EXPR", "TAG"),
    "color_flood": ("CMD", "EXPR"),
    "activex": ("ACTIVEX_Value",),
    "process_analyst": (),
    "scheduler": (),
    "web_content": (),
    "db_exchange": (),
    "pelco": (),
}

# seed bag keys a NEW object must not carry at all. A new BUTTON has no
# command. CtDraw writes ACTION
# KEY_LBUTTON only together with a command, and no button has an empty
# command key, so the keys are removed, not blanked.
_DROP_ON_CREATE = {
    "button": (
        "CMD",
        "DOWN_CMD",
        "REPEAT_CMD",
        "ACTION",
        "RPT_RATE",
        "MSG_LOG",
        "MSG_LOGDN",
        "MSG_LOGRPT",
        "PRIV",
    ),
}
# An ActiveX seed's Identification + tool tip are its DONOR's (the CiMeter's
# event class 'CiVBA_AN545' names the donor page's AN 545 -- two meters with it
# would share one set of Cicode event handlers; the Process Analyst's name
# 'EquipPA' is what the donor page's Cicode finds it by). A new control has
# none: its name and event class are then CtDraw's defaults 'AN<n>' /
# '<page>_AN<n>' (Access > Identification).
_CONTROL_IDENTITY = ("ACTIVEXEXT_*NAME", "ACTIVEXEXT_*EVENTCLASS", "COMMENT")
for _k in ("activex", "process_analyst", "scheduler", "db_exchange", "pelco"):
    _DROP_ON_CREATE[_k] = _CONTROL_IDENTITY
del _k
# A Cicode object without a command is an animation point with NO CMD key: the
# Cicode page removes an empty command.
_DROP_ON_CREATE["cicode_object"] = ("CMD", "EXPR")

# the drawing kinds whose seed styling is reset to CtDraw's new-object defaults
_STYLE_DEFAULT_KINDS = frozenset(
    {
        "line",
        "rectangle",
        "rectangle_outline",
        "ellipse",
        "ellipse_outline",
        "polygon",
        "polyline",
        "pipe",
        "text",
        "number",
        "button",
    }
)


#: where a new ActiveX control comes from: {kind: (project, page, marker)}, the control
#: placed on that page of the install's Example project. Its record holds the control's own
#: saved state, which ctgedit cannot make up, so the record is copied from the user's install
#: when one is placed. records.activex builds a made-up control for the tests and fixtures.
CONTROL_SOURCES = {
    "activex": ("Example", "CiVBA.ctg", b"CiMeter"),
    "process_analyst": ("Example", "EquipPA.ctg", b"Analyst"),
    "scheduler": ("Example", "TimeScheduler.ctg", b"Scheduler"),
}
#: the kinds copied from the install; Database Exchange and the Pelco camera viewer are on no
#: page the install ships, so they are never available
HARVESTED = frozenset(CONTROL_SOURCES) | {"db_exchange", "pelco"}


def _control_page(kind: str) -> Optional[str]:
    """The install's page a new ``kind`` control is copied from, or None."""
    src = CONTROL_SOURCES.get(kind)
    if src is None:
        return None
    from ..infra import install

    user = install.user_dir()
    path = os.path.join(user, src[0], src[1]) if user else ""
    return path if path and os.path.isfile(path) else None


def _untransformed(po: PageObject) -> bool:
    """The record's three placement transforms are empty: each is its mode byte alone (mode
    0), so the anchor starts three bytes after the name ([lpstr][u16 handle][lpstr name])."""
    b = po.body_raw
    h = G.parse_geom_header(b)
    if h is None or len(b) < 6:
        return False
    first = struct.unpack_from("<H", b, 0)[0]
    if len(b) < 6 + first:
        return False
    name_end = 6 + first + struct.unpack_from("<H", b, 4 + first)[0]
    return h.anchor_off == name_end + 3


@lru_cache(maxsize=None)
def _control_record(kind: str, path: str) -> Optional[Tuple[int, int, bytes]]:
    """(type code, dwFlags, body) of the smallest free, untransformed ``kind`` control on the
    page at ``path``, or None."""
    from .page import parse_ctg

    marker = CONTROL_SOURCES[kind][2]
    try:
        objects = parse_ctg(path).page.objects
    except Exception:  # noqa: BLE001 -- a page that cannot be read gives no control
        from ..infra.logsetup import note_exception

        note_exception("factory.control_record", page=path)
        return None
    best = None
    for po in objects:
        # a free record (no library parent: dwFlags bit 0x2, link -1); a library copy
        # keeps its appearance in its library twin and collapses when placed alone
        if po.type_code != 0x0D or not (po.subtype_size & 0x2) or po.an_str != NO_LINK:
            continue
        if marker not in po.body_raw.replace(b"\x00", b"") or not _untransformed(po):
            continue
        if best is None or len(po.body_raw) < len(best[2]):
            best = (po.type_code, po.subtype_size, bytes(po.body_raw))
    return best


def _have(seed_name: str) -> bool:
    if seed_name in HARVESTED:
        return _control_page(seed_name) is not None
    return seed_name in R.BUILDERS


def available_kinds() -> List[str]:
    return [k for k, seed in TOOL_SEEDS.items() if _have(seed)]


# The payload an_str is the LIBRARY-LINK number (CtDraw: the handle of
# the twin record in the referenced library element), written by the saver
# CtDraw as a decimal number; an object without a library parent carries -1
# (reset it to 0xffff). A new object is never linked.
NO_LINK = "-1"


def _instantiate(type_code: int, dwflags: int, body: bytes) -> PageObject:
    """A control record copied from the install as a PageObject: its body under a fresh
    prefix (no children, an_str -1)."""
    an = NO_LINK.encode(CODEC)
    payload = (
        struct.pack("<H", 0)
        + struct.pack("<I", type_code)
        + struct.pack("<I", dwflags)
        + struct.pack("<H", len(an))
        + an
        + body
    )
    return _parse_object(0, 0, len(payload), 0, payload)


#: the thumb a new V3/V4 slider draws and drags: the Include library's pointer. ctgedit has
#: no field that sets THUMB, so a slider without it would have no thumb at all. The record builder
#: (records.SLIDER_PROPS) stays neutral for the fixtures and tests.
SLIDER_THUMB = "thumbs.pointer2_e_r"
#: the builder fields the app's new objects add to records.py's neutral defaults
_NEW_FIELDS = {
    "slider": {"props": tuple((k, SLIDER_THUMB if k == "THUMB" else v) for k, v in R.SLIDER_PROPS)},
}


def _new(seed_name: str) -> PageObject:
    """The blank record a new ``seed_name`` object starts from: built from its
    fields (records.py, plus :data:`_NEW_FIELDS`), or a control copied from the install
    (:data:`CONTROL_SOURCES`)."""
    if seed_name in HARVESTED:
        path = _control_page(seed_name)
        found = _control_record(seed_name, path) if path else None
        if found is None:
            raise ValueError("no %s control found in the install's Example project" % seed_name)
        return _instantiate(*found)
    return R.new_record(seed_name, **_NEW_FIELDS.get(seed_name, {}))


def _strip_editor_refs(po: PageObject) -> None:
    """Remove '!*PropSheet.Focus' / '!*#NNNN' editor-state pairs from the clone
    (they reference other objects' ANs on the donor page). Harvesting guarantees
    each bag keeps at least one real pair."""
    for key in list(writer.object_property_keys(po)):
        if key == "!*PropSheet.Focus" or key.startswith("!*#"):
            writer.remove_object_property(po, key)


def _set_handle(po: PageObject, handle: int) -> None:
    G.set_handle(po, handle)


def _apply_style_defaults(po: PageObject, hollow: bool = False) -> None:
    """Give a new shape CtDraw's default styling instead of its seed's."""
    cb = G.find_colour_block(po)
    if cb is None:
        return
    if hollow and cb.flags[G.FLAG_FILL_STYLE]:
        G.set_colour_block_flag(po, G.FLAG_FILL_STYLE, 0)
    if cb.flags[G.FLAG_EFFECTS] & 0x0F:
        G.set_colour_block_flag(po, G.FLAG_EFFECTS, cb.flags[G.FLAG_EFFECTS] & 0xF0)
    if cb.flags[G.FLAG_EFFECT_DEPTH] != 0xFF:
        G.set_colour_block_flag(po, G.FLAG_EFFECT_DEPTH, 0xFF)
    tail = G.parse_shape_tail(po)
    if tail is None:
        return
    if tail["kind"] == "rect" and tail["radius"]:
        G.set_corner_radius(po, 0)
    elif tail["kind"] == "ellipse":
        if cb.flags[G.FLAG_ATTRIBS] & 0x0F:  # pie / arc mode -> ellipse
            G.set_colour_block_flag(po, G.FLAG_ATTRIBS, cb.flags[G.FLAG_ATTRIBS] & 0xF0)
        if tail["start"] or tail["end"]:
            G.set_ellipse_angles(po, 0, 0)
    if tail["grad_mode"]:
        G.set_gradient(po, 0)


NO_COLOUR = 0xFF000000  # high-byte sentinel = no colour / transparent


def set_object_colour(
    po: PageObject, colour: int, *, line: bool = False, outline_only: bool = False, match_line: bool = False
) -> bool:
    """Set an object's static colour."""
    from . import colourstate

    return colourstate.set_static_colour(
        po, colour, line=line, outline_only=outline_only, match_line=match_line
    )


# the embedded-picture record's sections between rect2 and its class part (bitmap
# and metafile): the empty level, the state-0 pair and the style block in the
# picture colour (records.PICTURE_TAIL; the engine treats it as opaque style data)
_BITMAP_MID = R.PICTURE_TAIL
_BITMAP_DWFLAGS = 0x0A


def _encode_dib(rgba: bytes, width: int, height: int) -> bytes:
    """BITMAPINFOHEADER(40) + top-down 32bpp B,G,R + Windows AND-mask byte
    (0x00 opaque / 0xFF transparent, the inverse of alpha). ``biSizeImage``
    is 0 like the engine writes it (legal for BI_RGB)."""
    from .bitmaps import encode_dib

    return encode_dib(rgba, width, height)


def make_bitmap_record(
    handle: int, rect, rgba: bytes, width: int, height: int, flash_rgba: bytes | None = None, name: str = ""
) -> PageObject:
    """Create an embedded bitmap record from RGBA pixels."""
    from . import colourstate
    from .ctf import _compress
    from .page import _parse_object

    l, t, r, b = (int(v) for v in rect)
    if r <= l:
        r = l + max(1, width)
    if b <= t:
        b = t + max(1, height)
    raw = _encode_dib(rgba, width, height)
    z = _compress(raw)
    section = struct.pack("<III", 0x101 if flash_rgba else 0x001, len(raw), len(z)) + z
    if flash_rgba:
        raw2 = _encode_dib(flash_rgba, width, height)
        z2 = _compress(raw2)
        section += struct.pack("<I", len(z2)) + z2
    nm = name.encode(CODEC, "replace")
    body = (
        struct.pack("<HH", 0, handle & 0xFFFF)  # empty lpstr + handle
        + struct.pack("<H", len(nm))
        + nm  # object name lpstr
        + b"\x00\x00\x00"  # identity transforms
        + struct.pack("<2h", l, b - 1)  # anchor (template style)
        + struct.pack("<4h", l, t, r, b)  # rect1
        + struct.pack("<4h", l, t, r, b)  # rect2
        + _BITMAP_MID
        + struct.pack("<4h", l, t, r, b)  # rect1 echo
        + section
    )
    an = NO_LINK.encode(CODEC)
    payload = (
        struct.pack("<H", 0)
        + struct.pack("<I", 0x07)
        + struct.pack("<I", _BITMAP_DWFLAGS)
        + struct.pack("<H", len(an))
        + an
        + body
    )
    po = _parse_object(0, 0, len(payload), 0, payload)
    colourstate.derive_dynamic_flag(po)
    return po


def make_metafile_record(handle: int, rect, wmf: bytes) -> PageObject:
    """Create a metafile record from a Windows metafile."""
    from .page import _parse_object

    l, t, r, b = (int(v) for v in rect)
    if r <= l:
        r = l + 1
    if b <= t:
        b = t + 1
    if len(wmf) < 18 or struct.unpack_from("<HH", wmf, 0) not in ((1, 9), (2, 9)):
        raise ValueError("not a memory Windows Metafile")
    body = (
        struct.pack("<HH", 0, handle & 0xFFFF)  # empty lpstr + handle
        + struct.pack("<H", 0)  # no object name
        + b"\x00\x00\x00"  # identity transforms
        + struct.pack("<2h", l, b - 1)  # anchor
        + struct.pack("<4h", l, t, r, b)  # rect1
        + struct.pack("<4h", l, t, r, b)  # rect2
        + _BITMAP_MID
        + struct.pack("<4h", l, t, r, b)  # native rect
        + bytes(wmf)
    )
    an = NO_LINK.encode(CODEC)
    payload = (
        struct.pack("<H", 0)
        + struct.pack("<I", 0x09)
        + struct.pack("<I", _BITMAP_DWFLAGS)
        + struct.pack("<H", len(an))
        + an
        + body
    )
    return _parse_object(0, 0, len(payload), 0, payload)


# GROUP record = the shape CtDraw itself writes for a FREE group (Arrange >
# Group on page objects): dwFlags 0xA (0x8 bags + 0x2 own appearance), a
# 109-byte body = geometry header + this 80-byte tail: an empty animation
# section [u16 0][bag size 2][bag size 12 (+10 engine-internal)][bag size 2],
# the colour-state list (state 0 = black) and the 54-byte style block (w=1,
# depth auto, slots black/black/none/white/grey/grey). No NOSELECT (bit 0x4):
# a group with it is LOCKED in CtDraw.
_GROUP_TAIL = R.GROUP_TAIL
assert len(_GROUP_TAIL) == 80
_GROUP_SUBTYPE_SIZE = 0x0A


def make_group_record(handle: int, bbox, child_count: int, bounds=None) -> PageObject:
    """Create a group record wrapping ``child_count`` subtrees."""
    from .page import _parse_object

    l, t, r, b = (int(v) for v in bbox)
    # rect1 (Extent) = union of the children's rect1, rect2 (Bounds) = union of
    # their rect2 (the union of each child's bounds)
    bl, bt, br, bb = (int(v) for v in (bounds or bbox))
    body = (
        struct.pack("<HH", 0, handle & 0xFFFF)  # empty lpstr + handle
        + struct.pack("<H", 0)  # empty object-name lpstr
        + b"\x00\x00\x00"  # 3 identity transforms
        + struct.pack("<2h", l, b - 1)  # anchor = (left, bottom-1)
        + struct.pack("<4h", l, t, r, b)  # rect1 = union child rect1
        + struct.pack("<4h", bl, bt, br, bb)  # rect2 = union child rect2
        + _GROUP_TAIL
    )
    payload = (
        struct.pack("<H", child_count)
        + struct.pack("<I", 0x08)
        + struct.pack("<I", _GROUP_SUBTYPE_SIZE)
        + struct.pack("<H", len(NO_LINK))
        + NO_LINK.encode(CODEC)
        + body
    )
    return _parse_object(0, 0, len(payload), 0, payload)


def set_symbol_reference(po: PageObject, library: str, element: str, ext: str = "ctl") -> bool:
    """Point a placed symbol record at ``library.element``."""
    return writer.set_body_reference(po, "%s.%s.<%s" % (library, element, ext))


# V3/V4 library-class kinds: positioned by a class-private section, see
# ctgedit.format.legacy (their blank records are free: dwFlags 0x2 set)
_LEGACY_KINDS = frozenset(
    {
        "string",
        "animated_symbol",
        "cicode_object",
        "animation_keyboard",
        "pagewide_keyboard",
        "trend",
        "digital_text",
        "color_flood",
    }
)


def _create_legacy(kind: str, seed_name: str, rect, text, symbol, handle, symbol_size=None) -> PageObject:
    """Create a V3/V4 record the way CtDraw writes a fresh one."""
    from . import legacy as L

    po = _new(seed_name)
    po.class_flag = 0  # children are never cloned
    _strip_editor_refs(po)
    if handle is not None:
        _set_handle(po, handle)
    for key in _CLEAR_ON_CREATE.get(kind, ()):
        writer.set_object_property(po, key, "")
    for key in _DROP_ON_CREATE.get(kind, ()):
        writer.remove_object_property(po, key)
    if po.type_code == 0x0D:
        # the control's property list mirrors its ACTIVEX_ / ACTIVEXEXT_ keys
        from . import olecontrol

        olecontrol.rebuild_list(po)
    rect = tuple(int(v) for v in (rect or (0, 0, 1, 1)))
    if po.type_code == L.TREND_TC:
        # CtDraw's new trend: one pixel per sample and
        # the default pen colours, both halves
        L.set_trend_spacing(po, 1)
        L.set_trend_pen_colours(po, [(c, c) for c in L.DEFAULT_PEN_COLOURS])
        L.place_trend(po, rect, "current")
        return po
    if kind == "string":
        # ON_TEXT/OFF_TEXT are Cicode: a caption is a QUOTED literal (bare,
        # the engine shows the '########' placeholder); CtDraw's own new-String
        # default font is DefaultFont
        lit = ('"%s"' % text) if text else ""
        for key, val in (
            ("OFF_TEXT", lit),
            ("ON_TEXT", lit),
            ("OFF_FONT", "DefaultFont"),
            ("ON_FONT", "DefaultFont"),
        ):
            writer.ensure_object_property(po, key, val)
        d2 = L.text_extent_deltas(text or None)
    elif kind == "digital_text":
        # TextAn's state keys are STR000..STR111 / FONT000..FONT111 (raw
        # text, measured verbatim by the engine)
        writer.ensure_object_property(po, "STR000", text or "")
        writer.ensure_object_property(po, "FONT000", "DefaultFont")
        d2 = L.text_extent_deltas(text or None)
    elif kind == "animated_symbol":
        # the class's states are exactly OFF_SYMBOL + SYMBOL_1..8 (never
        # 'SYMBOL'); drop the seed's. The engine clears a state
        # that does not resolve to a Bitmap-class element.
        for key in L.ANIMSYM_STATES:
            writer.remove_object_property(po, key)
        if symbol is not None:
            writer.ensure_object_property(po, "OFF_SYMBOL", "%s.%s" % (symbol[0], symbol[1]))
        d2 = L.animsym_extent_deltas(*(symbol_size or (20, 18)))
    else:
        d2 = L.RECT2_DELTAS[po.type_code]
    # the drawn box (anchor + natural extent) starts at the rect's top-left
    x, y = rect[0] - d2[0], rect[1] - d2[1]
    L.place_point(po, x, y)
    L.set_point_extent(po, d2)
    return po


def create_object(
    kind: str,
    rect: Optional[Tuple[int, int, int, int]] = None,
    nodes: Optional[List[Tuple[int, int]]] = None,
    *,
    colour: Optional[int] = None,
    text: Optional[str] = None,
    symbol: Optional[Tuple[str, str, str]] = None,
    handle: int = 0,
    link_info: Optional[dict] = None,
) -> PageObject:
    """Build a new PageObject of ``kind``."""
    seed_name = TOOL_SEEDS.get(kind)
    if seed_name is None or not _have(seed_name):
        raise ValueError("no seed for object kind %r" % kind)
    if kind in _LEGACY_KINDS:
        return _create_legacy(kind, seed_name, rect, text, symbol, handle)
    po = _new(seed_name)
    _strip_editor_refs(po)
    # a NEW object has no ROT / MV / SZ / VISIBLE animation (the reader would treat the
    # fresh colour pair as runtime chrome)
    if kind not in ("bar_graph", "slider"):  # their MV/SZ config is the tool
        for key in (
            "ROT",
            "ROTMINPC",
            "ROTMAXPC",
            "ROTORG",
            "MVX",
            "MVY",
            "MVXMINPT",
            "MVXMAXPT",
            "MVYMINPT",
            "MVYMAXPT",
            "SZX",
            "SZY",
            "SZXMINPC",
            "SZXMAXPC",
            "SZYMINPC",
            "SZYMAXPC",
            "SZXORG",
            "SZYORG",
            "VISIBLE",
            "DISABLED",
        ):
            try:
                writer.remove_object_property(po, key)
            except Exception:  # noqa: BLE001
                pass
    for key in _DROP_ON_CREATE.get(kind, ()):
        writer.remove_object_property(po, key)
    if po.type_code == 0x0D:
        # the control's property list mirrors its ACTIVEX_ / ACTIVEXEXT_ keys
        from . import olecontrol

        olecontrol.rebuild_list(po)
    if kind in _STYLE_DEFAULT_KINDS:
        # CtDraw's own new shape stores no colour animation and no property-
        # sheet state: a lone COL_MAXON (no COL_A / COLMODE) and the '!*' keys
        # go; a bagless rect keeps dwFlags 0xA.
        keys = set(writer.object_property_keys(po))
        if "COL_MAXON" in keys and not keys & {"COL_A", "COLMODE", "COL_ARR", "COL_EXP"}:
            writer.remove_object_property(po, "COL_MAXON")
        for key in keys:
            if key.startswith("!*"):
                writer.remove_object_property(po, key)
    if handle is not None:
        _set_handle(po, handle)

    # ---- placed library symbol: position + size live in Local ---------------
    if kind == "symbol":
        from . import placement

        if symbol is None or link_info is None:
            raise ValueError("The symbol's library element was not found.")
        lib, element, ext = symbol
        if not set_symbol_reference(po, lib, element, ext or "ctl"):
            raise ValueError("symbol seed carries no link lpstr")
        n = link_info["N"]
        target = rect or (n[0], n[1], n[2], n[3])
        placement.init_linked_symbol(po, target, link_info)
        # the link number = the element root record's handle (what CtDraw
        # writes for a top-level placed symbol)
        po.an_str = str(link_info.get("root_handle", 0))
        from .writer import refresh_object_bags

        refresh_object_bags(po)
        return po

    # ---- geometry ----------------------------------------------------------
    if kind == "line":
        pts = nodes or [(rect[0], rect[1]), (rect[2], rect[3])] if (nodes or rect) else None
        if pts:
            G.set_object_nodes(po, [tuple(map(int, p)) for p in pts[:2]])
    elif kind in ("polygon", "polyline", "pipe"):
        if nodes and len(nodes) >= 2:
            G.set_object_nodes(po, [tuple(map(int, p)) for p in nodes])
        elif rect:
            G.resize_object(po, rect)
        if kind in ("polygon", "polyline"):
            # attribs bit0: 1 = open (polyline / Free Hand Line), 0 = closed;
            # pinned explicitly so the seed's own state never leaks through
            cb = G.find_colour_block(po)
            if cb is not None:
                a = (cb.flags[3] | 0x01) if kind == "polyline" else (cb.flags[3] & ~0x01)
                G.set_colour_block_flag(po, G.FLAG_ATTRIBS, a)
                if kind == "polyline":
                    # a polyline / Free Hand Line is HOLLOW: CtDraw's polygon draw
                    # FILLS an open polygon whose fill_style != 0 (NULL pen + Polygon, then
                    # Polyline), and the polygon seed is solid.
                    G.set_colour_block_flag(po, G.FLAG_FILL_STYLE, 0)
    elif rect:
        if G.fit_new_text(po, rect):
            # (a new text takes the box's height as its point size, never a matrix)
            G.refresh_bags(po)
        else:
            G.resize_object(po, rect)
    if kind in _STYLE_DEFAULT_KINDS:
        _apply_style_defaults(po, hollow=kind.endswith("_outline"))

    # ---- colour ------------------------------------------------------------
    from . import colourstate

    colourstate.rebuild(po)  # the seed's stale state-0 pair
    if colour is not None:
        set_object_colour(
            po,
            colour,
            line=kind in ("line", "polyline"),
            outline_only=kind.endswith("_outline"),
            match_line=True,
        )
        if po.raw_properties().get("COL_MAXON"):
            # the seed's colour-animation ON colour follows (bag + binary)
            colourstate.set_animation_on_colour(po, colour)
    # dwFlags bit 0x10 is derived by the saver: the seed's stripped animations
    # (ROT/MVX...) must not leave it set
    colourstate.derive_dynamic_flag(po)

    # ---- captions / display ------------------------------------------------
    if text is not None and kind != "number":
        if po.type_code == 0x05:
            # stored as typed: a plain caption, a translatable label only when
            # the user wrote '@(...)'
            writer.set_object_text(po, text, label=False)
        else:
            writer.ensure_object_property(po, "TEXT", text)
    if kind == "number":
        # a numeric display value: TXTMODE 3 + the expression in TXT_STR
        # ('expr:format', or a bare tag = the tag's own format), as CtDraw
        # writes it (no TXT_A / FORMAT); the caption is CtDraw's '####.#' placeholder
        writer._set_plain_caption(po, "####.#")  # plain, not @(...)
        writer.ensure_object_property(po, "TXTMODE", "3")
        writer.ensure_object_property(po, "TXT_STR", text or "")
    if po.type_code == 0x05:
        # the string section follows TXTMODE; the engine measures the box from
        # the string + font on load
        writer.rebuild_text_strings(po)
        G.refit_text(po)
        writer.refresh_object_bags(po)
    if po.type_code == 0x1001:
        writer.sync_button_caption(po)
    if po.type_code == 0x1001 and rect:
        # a button's WIDTH / HEIGHT bag values are its extent (engine-rewritten)
        l, t, r, b = G.norm_rect(rect)
        for key, v in (("WIDTH", r - l), ("HEIGHT", b - t)):
            if key in po.raw_properties():
                writer.set_object_property(po, key, str(v))

    # ---- legacy V3/V4: drop the seed's tag expressions / commands ------------
    for key in _CLEAR_ON_CREATE.get(kind, ()):
        try:
            writer.set_object_property(po, key, "")
        except Exception:  # noqa: BLE001 -- key absent on this seed variant
            pass
    if kind == "string" and text is not None:
        writer.ensure_object_property(po, "ON_TEXT", text)
        writer.ensure_object_property(po, "OFF_TEXT", text)
    if kind == "web_content":
        name = text or "WebContent_%d" % (handle or 1)
        writer.set_object_property(po, "__AN_NAME__", name)
        G.set_object_name(po, name)  # the header name mirrors the bag
        from .writer import refresh_object_bags

        refresh_object_bags(po)

    # ---- symbol reference ----------------------------------------------------
    if symbol is not None:
        lib, element, ext = symbol
        if kind == "animated_symbol":
            ref = "%s.%s" % (lib, element)
            writer.ensure_object_property(po, "SYMBOL", ref)
            writer.ensure_object_property(po, "OFF_SYMBOL", ref)
        else:
            set_symbol_reference(po, lib, element, ext or "ctl")

    return po


def input_chain_supported(chain) -> bool:
    """Whether :func:`make_input_record` can place a record under this owner
    chain: the page frame, or a pure TRANSLATION (the owner's Total has no
    scale / rotation / flip)."""
    from . import placement as P

    total = P.chain_total(chain) if chain else None
    if total is None:
        return True
    mode, (a, b, c, d, _h, _k) = total
    return mode & 0x0F in (0, 1) and (a, b, c, d) == (1.0, 0.0, 0.0, 1.0)


def make_input_record(
    kind: str, owner_handle: int, anchor: Tuple[int, int], values: Optional[dict] = None, chain=None
) -> PageObject:
    """A new Touch or Keyboard child record."""
    from . import inputs as IN
    from . import legacy as L
    from . import placement as P

    if kind not in ("touch", "keyboard"):
        raise ValueError("no seed for input record kind %r" % kind)
    po = _new(kind)
    po.class_flag = 0
    _strip_editor_refs(po)
    _set_handle(po, owner_handle)
    L.place_point(po, int(anchor[0]), int(anchor[1]))
    if kind == "touch":
        keys = IN.TOUCH_KEYS
        base = {k: "" for k in keys}
        base["ACTION"] = IN.TOUCH_ACTION
        base["RPT_RATE"] = IN.DEFAULT_RPT_RATE
    else:
        keys = IN.KEYBOARD_KEYS
        base = {k: "" for k in keys}
    for k, v in (values or {}).items():
        base[k] = "" if v is None else str(v)
    total = P.chain_total(chain) if chain else None
    if total is not None:
        if not input_chain_supported(chain):
            raise ValueError("the owner is scaled or rotated")
        h, k = total[1][4], total[1][5]  # the owner's frame origin
        x, y = int(anchor[0]), int(anchor[1])
        loc = (1.0, 0.0, 0.0, 1.0, float(x) - h, float(y) - k)
        s3, m3 = P.apply_parent(loc, 1, chain)
        P.write_slots(po, [(1, loc), (1, loc), (m3, s3)])
        body = bytearray(po.body_raw)
        struct.pack_into("<2h", body, len(body) - 4, 0, 0)
        po.body_raw = bytes(body)
    return IN.with_values(po, base, keys)


#: what an ActiveX control copied from another page as a NEW object does not
#: keep (its Access / Identification defaults follow its new AN, and the
#: donor's tag associations / metadata / animations name the donor's tags)
_CONTROL_DROP_PREFIXES = ("ACTIVEX_", "ACTIVEXEXT_", "METADATA_", "!*")
_CONTROL_DROP_KEYS = frozenset(
    {
        "COMMENT",
        "DESC",
        "AREA",
        "PRIV",
        "DEVICE",
        "NAME",
        "__AN_NAME__",
        "VISIBLE",
        "DISABLED",
        "DISMODE",
        "GREY_STYLE",
        "MVX",
        "MVY",
        "MVXMINPT",
        "MVXMAXPT",
        "MVYMINPT",
        "MVYMAXPT",
        "SZX",
        "SZY",
        "SZXMINPC",
        "SZXMAXPC",
        "SZYMINPC",
        "SZYMAXPC",
        "SZXORG",
        "SZYORG",
        "SZXMODE",
        "SZYMODE",
    }
)


def clone_control(donor: PageObject, rect=None, handle: int = 0) -> PageObject:
    """A new ActiveX object copied from an existing one."""
    from . import colourstate

    if donor.type_code != 0x0D or not donor.subtype_size & 0x2:
        raise ValueError("only a free ActiveX control record can be copied as a new control")
    po = clone_object(donor)
    po.class_flag = 0
    po.an_str = NO_LINK
    # a new object is not locked: the donor's Edit > Lock (dwFlags 0x4, the
    # scheduler of a page) stays with the donor
    po.subtype_size &= ~0x4
    for key in list(writer.object_property_keys(po)):
        if key.upper().startswith(_CONTROL_DROP_PREFIXES) or key.upper() in _CONTROL_DROP_KEYS:
            writer.remove_object_property(po, key)
    from . import olecontrol

    olecontrol.rebuild_list(po)  # the list mirrors the (now dropped) keys
    if po.subtype_size & 0x100:
        writer.rebuild_metadata_section(po)
    G.set_object_name(po, "")
    if handle is not None:
        _set_handle(po, handle)
    if rect:
        G.resize_object(po, rect)
    colourstate.derive_dynamic_flag(po)
    return po


def clone_object(po: PageObject) -> PageObject:
    """Deep-copy an existing record (used by duplicate / clipboard paste)."""
    return copy.deepcopy(po)


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("factory.control_records", _control_record, on=("refresh", "tests"))
