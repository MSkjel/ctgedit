"""The colour-state array, written the way CtDraw writes it."""

from __future__ import annotations

import re
import struct
from typing import Optional

from .page import PageObject
from . import codes as _codes

# (at load, not in layout: PySide6 hooks __import__, so a function-level import is a Python
# call on every record; geometry does not import this module at load)
from .geometry import parse_geom_header
from ..infra.logsetup import note_exception as _note_exc

PIPE_TC, TEXT_TC, LINE_TC, BUTTON_TC = 0x0B, 0x05, 0x01, 0x1001
CLOSED_TCS = frozenset({0x0F, 0x0E, 0x03, 0x11, 0x04, 0x10, 0x0A})
NO_COLOUR = 0xFF000000
COLOUR_KEY = re.compile(r"^(COL_MAXON|COL_STA_\d+|COL_ARR_\d+|COL_THR_\d+|FILCOL)$")


# --------------------------------------------------------------------------- layout
_U16 = struct.Struct("<H").unpack_from
_U32 = struct.Struct("<I").unpack_from
#: the colour-state FLAG's counted arrays: (bit, offset key, count key, entry size)
_ARRAYS = ((4, "sta_at", "sta_n", 8), (8, "arr_at", "arr_n", 8), (0x10, "thr_at", "thr_n", 11))


def _lp_end(b: bytes, o: int) -> int:
    (n,) = _U16(b, o)
    return o + 2 + n


def _bag_end(b: bytes, o: int) -> int:
    u16 = _U16
    (cnt,) = u16(b, o)
    o += 2
    for _ in range(cnt):  # (key, value): two lpstrs
        (n,) = u16(b, o)
        o += 2 + n
        (n,) = u16(b, o)
        o += 2 + n
    return o


#: :func:`layout` by the record's (body bytes, dwFlags), of which it is a pure function: it is
#: asked for each record several times (the open's grafts, the save's register and .ctF, the
#: Simulate window's page model). A caller gets a copy of the dict. Bounded; emptied when full
_LAYOUT_MEMO: dict = {}
_LAYOUT_MEMO_MAX = 1 << 14
_NO_LAYOUT = object()
#: ... and on the record: ``(body_raw, dwFlags, the layout or None)``, valid while the record
#: holds that same body object and dwFlags (checked first: no hash of the body's key)
LAYOUT_MEMO_ATTR = "_cs_layout_memo"


def layout(po: PageObject) -> Optional[dict]:
    """Walk forward to the colour-state array and style block, and return their offsets."""
    b = po.body_raw
    fl = po.subtype_size
    if type(b) is not bytes:
        return _layout(b, fl)
    d = getattr(po, "__dict__", None)
    if d is not None:
        m = d.get(LAYOUT_MEMO_ATTR)
        if m is not None and m[0] is b and m[1] == fl:
            return None if m[2] is None else dict(m[2])
    key = (b, fl)
    L = _LAYOUT_MEMO.get(key, _NO_LAYOUT)
    if L is _NO_LAYOUT:
        L = _layout(b, fl)
        if len(_LAYOUT_MEMO) >= _LAYOUT_MEMO_MAX:
            _LAYOUT_MEMO.clear()
        _LAYOUT_MEMO[key] = L
    if d is not None:
        d[LAYOUT_MEMO_ATTR] = (b, fl, L)
    return None if L is None else dict(L)


def _layout(b: bytes, fl: int) -> Optional[dict]:
    try:
        gh = parse_geom_header(b)
        if gh is None:
            return None
        o = gh.end
        if fl & 1:
            o = _lp_end(b, o)
        if fl & 8:
            u16 = _U16
            (n_ent,) = u16(b, o)
            o += 2
            for _ in range((n_ent + 1) * 3):  # u16 size, then _bag_end (inline: the hot loop)
                (cnt,) = u16(b, o + 2)
                o += 4
                for _ in range(cnt):
                    (n,) = u16(b, o)
                    o += 2 + n
                    (n,) = u16(b, o)
                    o += 2 + n
        if fl & 0x40:
            o = _lp_end(b, o) + 2 + 24
        sec80 = None
        if fl & 0x80:
            sec80 = o  # a button's caption lpstr
            o = _lp_end(b, o)
        L = {"at": o}
        if sec80 is not None:
            L["sec80_at"] = sec80
        (flag,) = _U32(b, o)
        L["flag"] = flag
        o += 4
        L["state0_at"] = o
        o += 8
        if flag & 2:
            L["maxon_at"] = o
            o += 8
        for bit, at, count, size in _ARRAYS:
            if flag & bit:
                (n,) = _U16(b, o)
                L[at], L[count] = o + 2, n
                o += 2 + size * n
        if flag & 0x20:
            L["x20_at"] = o
            o += 8
        if flag & 0x40:
            L["x40_at"] = o
            o += 8
        if o > len(b):
            return None
        L["end"] = o
        if fl & 2:
            if o + 54 > len(b):
                return None
            L["style_at"] = o
        return L
    except struct.error:
        return None


def _pair_at(b: bytes, o: int):
    return struct.unpack_from("<II", b, o)


def fmt_pair(p) -> str:
    return "0x%08x,0x%08x" % (p[0] & 0xFFFFFFFF, p[1] & 0xFFFFFFFF)


_PAIR_RE = re.compile(r"^\s*0[xX]([0-9A-Fa-f]+)\s*,\s*0[xX]([0-9A-Fa-f]+)")
_FIRST_RE = re.compile(r"^\s*0[xX]([0-9A-Fa-f]+)")


def _parse(s: str):
    """A "0x%x,0x%x" colour pair read as sscanf reads it, with the 0xff000000 defaults, so
    a value that is not '0x<hex>,0x<hex>' keeps the defaults for what did not match (a
    decimal '255,0' is two defaults, '0x1,x' one)."""
    m = _PAIR_RE.match(s or "")
    if m:
        return int(m.group(1), 16) & 0xFFFFFFFF, int(m.group(2), 16) & 0xFFFFFFFF
    m = _FIRST_RE.match(s or "")
    if m:
        return int(m.group(1), 16) & 0xFFFFFFFF, NO_COLOUR
    return NO_COLOUR, NO_COLOUR


#: the colour-array defaults (colour number -> (on, off)): the active
#: project's palette (format.colourpalette), installed by the
#: editing layer (use_palette); None = no palette (absent entries NO_COLOUR)
_palette = None


def use_palette(table) -> None:
    """Install the colour-array default table (256 (on, off) pairs, a
    format.colourpalette table) for the project being edited; None removes
    it (single-threaded UI: the document being edited)."""
    global _palette
    _palette = table


def array_default(i: int):
    """The default colour of array entry i: what CtDraw's
    saver writes for an entry without a COL_ARR_<i> bag."""
    t = _palette
    if t is not None and 0 <= i < len(t):
        return t[i]
    return (NO_COLOUR, NO_COLOUR)


def format_threshold(greater: bool, equal: bool, value: int, on: int, off: int) -> str:
    """A COL_THR_<n> value the way CtDraw writes it (the automation writer
    and the loader: "%s%s,%d,0x%0.8x,0x%0.8x",
    '>' / '<' then '=' for an inclusive comparison)."""
    return "%s%s,%d,0x%08x,0x%08x" % (
        ">" if greater else "<",
        "=" if equal else "",
        value,
        on & 0xFFFFFFFF,
        off & 0xFFFFFFFF,
    )


def parse_threshold(s: str):
    """A COL_THR_<n> value -> (greater, equal, value, on, off), read the way
    the saver does (_parse_thr: '>' anywhere in the comparison
    = greater, '=' = inclusive, C atoi / strtoul); None for an empty value."""
    if not s:
        return None
    parts = s.split(",")
    cmp_ = parts[0]
    try:
        value = int(re.match(r"\s*([+-]?\d+)", parts[1]).group(1)) if len(parts) > 1 else 0
    except AttributeError, ValueError:
        value = 0
    value = ((value + 0x8000) & 0xFFFF) - 0x8000  # stored as a short

    def col(k):
        try:
            return int(parts[k].strip(), 0) & 0xFFFFFFFF if len(parts) > k else 0
        except ValueError:
            return 0

    return (">" in cmp_, "=" in cmp_, value, col(2), col(3))


def _parse_thr(s: str) -> bytes:
    """COL_THR_: "<cmp>,<thr>,<on>,<off>" -> (u8 flags, u16, u32, u32)."""
    parts = s.split(",")
    cmp_ = parts[0]
    f = (1 if ">" in cmp_ else 0) | (2 if "=" in cmp_ else 0)
    m = re.match(r"^\s*[-+]?\d+", parts[1] if len(parts) > 1 else "")
    thr = (int(m.group(0)) if m else 0) & 0xFFFF  # atoi
    try:
        on = int(parts[2], 0) & 0xFFFFFFFF if len(parts) > 2 else 0
        off = int(parts[3], 0) & 0xFFFFFFFF if len(parts) > 3 else 0
    except ValueError:
        on = off = 0
    return struct.pack("<BHII", f, thr, on, off)


def state0_source(body: bytes, style_at: int):
    """The saver's state-0 pair: fill slot when fill_style != 0, else line."""
    line = _pair_at(body, style_at + 6)
    fill = _pair_at(body, style_at + 14)
    return fill if body[style_at + 2] else line


# --------------------------------------------------------------------------- rebuild
def rebuild(po: PageObject) -> bool:
    """Rebuild the colour-state array from the bags and style block, as CtDraw saves it."""
    from . import writer

    L = layout(po)
    if L is None:
        return False
    b = po.body_raw
    props = po.raw_properties()
    P = state0_source(b, L["style_at"]) if "style_at" in L else _pair_at(b, L["state0_at"])
    mx = props.get("COL_MAXON", "")
    flag = (
        1
        | (2 if mx else 0)
        | (4 if props.get("COL_STA_0", "") else 0)
        | (8 if props.get("COL_ARR_0", "") else 0)
        | (0x10 if props.get("COL_THR_0", "") else 0)
        | (0x20 if mx else 0)
        | (0x40 if props.get("FILCOL", "") else 0)
    )
    out = struct.pack("<I", flag) + struct.pack("<II", *P)
    if flag & 2:
        out += struct.pack("<II", *_parse(mx))
    if flag & 4:
        hi = max(i for i in range(32) if props.get("COL_STA_%d" % i, ""))  # the saver scans 31 .. 0
        out += struct.pack("<H", hi + 1) + struct.pack("<II", *P)
        for i in range(1, hi + 1):
            out += struct.pack("<II", *_parse(props.get("COL_STA_%d" % i, "")))
    if flag & 8:
        n_old = L.get("arr_n", 0)
        out += struct.pack("<H", 256) + struct.pack("<II", *P)
        for i in range(1, 256):
            v = props.get("COL_ARR_%d" % i, "")
            if v:
                pair = _parse(v)
            elif i < n_old:
                pair = _pair_at(b, L["arr_at"] + 8 * i)
            else:
                pair = array_default(i)
            out += struct.pack("<II", *pair)
    if flag & 0x10:
        thr = []
        for i in range(100):  # the saver stops at COL_THR_99
            v = props.get("COL_THR_%d" % i, "")
            if not v:
                break
            thr.append(_parse_thr(v))
        out += struct.pack("<H", len(thr)) + b"".join(thr)
    if flag & 0x20:
        out += struct.pack("<II", *_parse(mx))
    if flag & 0x40:
        out += struct.pack("<II", *_parse(props.get("FILCOL", "")))
    changed = out != b[L["at"] : L["end"]]
    if changed:
        po.body_raw = b[: L["at"]] + out + b[L["end"] :]
        writer.refresh_object_bags(po)
    for key, bit in (("COL_STA_0", 4), ("COL_ARR_0", 8)):
        if flag & bit and props.get(key, "").lower() != fmt_pair(P):
            writer.set_object_property(po, key, fmt_pair(P))
            changed = True
    return changed


# --------------------------------------------------------------------------- dwFlags 0x10
_VALUE_KEY = re.compile(
    r"^(COL_MAXON|COL_STA_\d+|COL_ARR_\d+|COL_THR_\d+|FILCOL|COLMODE|"
    r"GREY_STYLE|\w+(MINPC|MAXPC|MINPT|MAXPT)|\w+ORG|\w+SLMODE)$"
)


def dynamic_keys(props: dict) -> list:
    """Bag keys that make an object 'animated': any non-empty
    expression key; pure value / configuration keys do not count."""
    return sorted(k for k, v in props.items() if v and not k.startswith("!") and not _VALUE_KEY.match(k))


def flashing(po: PageObject, L: dict) -> bool:
    """Flashing: an on/off pair that differs."""
    b = po.body_raw
    s = L["style_at"]
    fl = b[s : s + 6]
    sl = [_pair_at(b, s + 6 + 8 * i) for i in range(6)]
    r = sl[0][0] != sl[0][1]
    if fl[2] & 0xF:
        r = r or sl[1][0] != sl[1][1]
    eff = fl[0] & 0xF
    if eff == 3:
        r = r or sl[5][0] != sl[5][1]
    elif eff:
        r = r or sl[3][0] != sl[3][1] or sl[4][0] != sl[4][1]
    return r


def gradient_flashing(po: PageObject) -> bool:
    """The rectangle / ellipse / polyline classes flash on the
    base rule OR a flashing GRADIENT colour: a gradient mode != 0 whose
    on / off halves differ."""
    if po.type_code not in (0x0F, 0x11, 0x10):
        return False
    from .geometry import parse_shape_tail

    try:
        t = parse_shape_tail(po)
    except Exception:  # noqa: BLE001
        _note_exc("colourstate.gradient_flashing")
        return False
    return bool(t and t["grad_mode"] and t["grad_on"] != t["grad_off"])


def bitmap_flashing(po: PageObject) -> Optional[bool]:
    """Is the bitmap flashing (a second DIB stored and different)?"""
    b = po.body_raw
    if po.type_code != 0x07 or not b:
        return None
    import zlib
    from . import geometry as G
    from .symbols import _find_stream

    gh = G.parse_geom_header(b)
    hit = _find_stream(b, gh.end if gh else 0)
    if hit is None:
        return None
    off, raw = hit
    if off < 12 or raw[:4] != b"\x28\x00\x00\x00":
        return None
    flags, uncomp, comp = struct.unpack_from("<III", b, off - 12)
    if not flags & 0x1 or uncomp != len(raw):
        return None
    if not flags & 0x100:
        return False
    try:
        (comp2,) = struct.unpack_from("<I", b, off + comp)
        flash = zlib.decompress(bytes(b[off + comp + 4 : off + comp + 4 + comp2]))
    except struct.error, zlib.error:
        return False
    return flash != raw


#: classes CtDraw always treats as dynamic: every V3/V4 library class
#: (tc >= 0x1000), text, ActiveX and web content: they carry
#: 0x10 with or without keys
ALWAYS_DYNAMIC_TCS = frozenset({TEXT_TC, 0x0D, 0x13})
#: containers whose dynamic state their members inherit (CtDraw asks the
#: parent when it is a group or a symbol set)
INHERIT_TCS = frozenset({0x08, 0x0C})
INPUT_TCS = _codes.INPUT_TCS
GROUP_TC = 0x08


def _atoi(s: str) -> int:
    m = re.match(r"\s*([+-]?\d+)", s or "")
    return int(m.group(1)) if m else 0


def is_dynamic(
    po: PageObject, values: Optional[dict] = None, *, parent_dynamic: bool = False, input_child: bool = False
) -> bool:
    """Does the saver set dwFlags 0x10 (dynamic) on this record?"""
    tc = po.type_code
    # (the point classes 0x1000.. are; a DummyMarker 0xFFFF is not by its class: a symbol
    # set's state is dynamic through its set, a free one or a placeholder genie's is not)
    if (tc >= 0x1000 and tc != 0xFFFF) or tc in ALWAYS_DYNAMIC_TCS or input_child:
        return True
    if values is None:
        from .writer import value_bag

        vb = value_bag(po)
        values = dict(vb) if vb is not None else po.raw_properties()
    ai_on = _atoi(values.get("AI_ON", "")) != 0
    for k in dynamic_keys(values):
        if k == "OrphanedFrom":
            continue
        if tc == 0xFFFF and k.startswith("_"):
            # a PLACEHOLDER genie's '_Library' / '_Transform_*' keys are no animation
            continue
        if k.upper().startswith("AI_") and not ai_on:
            continue
        return True
    bf = bitmap_flashing(po)
    if bf is not None:  # the bitmap class has its own flashing rule
        return bf or parent_dynamic
    if tc != GROUP_TC and po.subtype_size & 0x2:
        L = layout(po)
        if L is not None and "style_at" in L and flashing(po, L):
            return True
        if gradient_flashing(po):
            return True
    return parent_dynamic


def derive_dynamic_flag(po: PageObject, *, parent_dynamic: bool = False, input_child: bool = False) -> bool:
    """Set / clear dwFlags bit 0x10 of one record the way the saver derives it
    (:func:`is_dynamic`; any record class, root or member -- pass the
    parent / child context for a member). Returns True when it changed."""
    want = is_dynamic(po, parent_dynamic=parent_dynamic, input_child=input_child)
    old = po.subtype_size
    po.subtype_size = (old | 0x10) if want else (old & ~0x10)
    return po.subtype_size != old


def derive_dynamic_flags(objs, index: int, parents=None) -> list:
    """Re-derive the dynamic flag of a record and its ancestors."""
    if parents is None:
        from .placement import forest_parents

        parents = forest_parents(objs)
    owners = {parents[k] for k, o in enumerate(objs) if o.type_code in INPUT_TCS and parents[k] is not None}

    def ctx(j):
        p = parents[j]
        pdyn = p is not None and objs[p].type_code in INHERIT_TCS and bool(objs[p].subtype_size & 0x10)
        return pdyn, j in owners

    changed = []
    pdyn, inp = ctx(index)
    if objs[index].raw_payload is None and derive_dynamic_flag(
        objs[index], parent_dynamic=pdyn, input_child=inp
    ):
        changed.append(index)
    if not changed or not objs[index].class_flag:
        return changed
    return changed + derive_member_flags(objs, index, parents)


def derive_member_flags(objs, index: int, parents=None) -> list:
    """Re-derive 0x10 of every record below ``objs[index]`` (its contiguous DFS subtree)
    in its page context, parents first: a member of a dynamic group or symbol set inherits
    the bit. Returns the indices whose flag changed."""
    if parents is None:
        from .placement import forest_parents

        parents = forest_parents(objs)
    owners = {parents[k] for k, o in enumerate(objs) if o.type_code in INPUT_TCS and parents[k] is not None}
    changed = []
    end, pending = index + 1, objs[index].class_flag
    while pending > 0 and end < len(objs):
        pending += objs[end].class_flag - 1
        end += 1
    for j in range(index + 1, end):
        if objs[j].raw_payload is not None:
            continue
        p = parents[j]
        pdyn = p is not None and objs[p].type_code in INHERIT_TCS and bool(objs[p].subtype_size & 0x10)
        if derive_dynamic_flag(objs[j], parent_dynamic=pdyn, input_child=j in owners):
            changed.append(j)
    return changed


# --------------------------------------------------------------------------- static colour
def _half(c: int) -> int:
    """A pipe's lowlight: each colour byte >> 1, top byte 0."""
    return (((c >> 17) & 0x7F) << 16) | (((c >> 9) & 0x7F) << 8) | ((c & 0xFF) >> 1)


def _set_slot(po: PageObject, L: dict, slot: int, on: int, off: Optional[int] = None) -> None:
    body = bytearray(po.body_raw)
    struct.pack_into(
        "<II", body, L["style_at"] + 6 + 8 * slot, on & 0xFFFFFFFF, (on if off is None else off) & 0xFFFFFFFF
    )
    po.body_raw = bytes(body)


def _set_flag(po: PageObject, L: dict, idx: int, val: int) -> None:
    body = bytearray(po.body_raw)
    body[L["style_at"] + idx] = val & 0xFF
    po.body_raw = bytes(body)


def set_static_colour(
    po: PageObject, colour: int, *, line: bool = False, outline_only: bool = False, match_line: bool = False
) -> bool:
    """Set the static colour through the channel CtDraw keeps."""
    from .geometry import SLOT_FILL, SLOT_HILIGHT, SLOT_LINE, SLOT_LOLIGHT, FLAG_FILL_STYLE
    from . import writer

    if not (po.subtype_size & 0x2):
        return False
    L = layout(po)
    if L is None or "style_at" not in L:
        return False
    c = colour & 0xFFFFFF
    tc = po.type_code
    if tc == PIPE_TC:
        _set_slot(po, L, SLOT_HILIGHT, c)
        _set_slot(po, L, SLOT_LOLIGHT, _half(c))
        _set_slot(po, L, SLOT_LINE, c)
        _set_slot(po, L, SLOT_FILL, c)
    elif tc in (TEXT_TC, BUTTON_TC):
        _set_slot(po, L, SLOT_FILL, c)
        _set_slot(po, L, SLOT_LINE, c)
    elif tc == LINE_TC:
        _set_slot(po, L, SLOT_LINE, c)
    elif tc in CLOSED_TCS:
        if outline_only:
            _set_slot(po, L, SLOT_FILL, NO_COLOUR)
            _set_flag(po, L, FLAG_FILL_STYLE, 0)
            _set_slot(po, L, SLOT_LINE, c)
        elif line:
            _set_slot(po, L, SLOT_LINE, c)
        else:
            _set_slot(po, L, SLOT_FILL, c)
            _set_flag(po, L, FLAG_FILL_STYLE, 1)
            if match_line:
                _set_slot(po, L, SLOT_LINE, c)
    else:
        _set_slot(po, L, SLOT_LINE if line else SLOT_FILL, c)
    rebuild(po)
    writer.refresh_object_bags(po)
    return True


def set_slot(po: PageObject, slot: int, on: int, off: Optional[int] = None) -> bool:
    """Write one style-block colour pair."""
    from .geometry import SLOT_FILL, SLOT_HILIGHT, SLOT_LINE
    from . import writer

    if not (po.subtype_size & 0x2) or not (0 <= slot < 6):
        return False
    L = layout(po)
    if L is None or "style_at" not in L:
        return False
    off = on if off is None else off
    _set_slot(po, L, slot, on, off)
    tc = po.type_code
    if tc == PIPE_TC and slot == SLOT_HILIGHT:
        _set_slot(po, L, SLOT_FILL, on, off)
        _set_slot(po, L, SLOT_LINE, on, off)
    elif tc == TEXT_TC and slot == SLOT_FILL:
        _set_slot(po, L, SLOT_LINE, on, off)
    rebuild(po)
    writer.refresh_object_bags(po)
    return True


def set_pipe_colour(po: PageObject, on: int, off: Optional[int] = None) -> bool:
    """A PIPE's colour as CtDraw's colour picker sets it: the
    highlight pair = (on, off), the lowlight = each colour byte >> 1 (top byte
    0), fill = line = (on, off) -- the pair form of the pipe's static colour.
    False when ``po`` is no pipe with a style block."""
    from .geometry import SLOT_FILL, SLOT_HILIGHT, SLOT_LINE, SLOT_LOLIGHT
    from . import writer

    if po.type_code != PIPE_TC or not (po.subtype_size & 0x2):
        return False
    L = layout(po)
    if L is None or "style_at" not in L:
        return False
    off = on if off is None else off
    _set_slot(po, L, SLOT_HILIGHT, on, off)
    _set_slot(po, L, SLOT_LOLIGHT, _half(on), _half(off))
    _set_slot(po, L, SLOT_FILL, on, off)
    _set_slot(po, L, SLOT_LINE, on, off)
    rebuild(po)
    writer.refresh_object_bags(po)
    return True


def colour_channels(po: PageObject) -> Optional[dict]:
    """The colour pairs Swap / Adjust Colors change on this record."""
    from .geometry import SLOT_FILL, SLOT_HILIGHT, SLOT_LINE

    if not (po.subtype_size & 0x2):
        return None
    L = layout(po)
    if L is None or "style_at" not in L:
        return None
    b = po.body_raw
    s = L["style_at"]

    def slot(i):
        return _pair_at(b, s + 6 + 8 * i)

    if po.type_code == PIPE_TC:
        return {"class": slot(SLOT_HILIGHT)}
    fill = slot(SLOT_FILL)
    line = fill if po.type_code == TEXT_TC else slot(SLOT_LINE)
    return {"fill": fill, "line": line}


def map_colour_channels(po: PageObject, fn) -> bool:
    """Map every Swap / Adjust colour channel through ``fn`` and write the changed ones."""
    from .geometry import SLOT_FILL, SLOT_LINE

    ch = colour_channels(po)
    if ch is None:
        return False
    changed = False
    if "class" in ch:
        new = tuple(int(v) & 0xFFFFFFFF for v in fn(ch["class"]))
        if new != tuple(ch["class"]):
            changed = set_pipe_colour(po, new[0], new[1])
        return changed
    new_fill = tuple(int(v) & 0xFFFFFFFF for v in fn(ch["fill"]))
    new_line = new_fill if po.type_code == TEXT_TC else tuple(int(v) & 0xFFFFFFFF for v in fn(ch["line"]))
    if new_fill != tuple(ch["fill"]):
        changed = set_slot(po, SLOT_FILL, new_fill[0], new_fill[1]) or changed
    if po.type_code != TEXT_TC and new_line != tuple(ch["line"]):
        changed = set_slot(po, SLOT_LINE, new_line[0], new_line[1]) or changed
    return changed


def set_animation_on_colour(po: PageObject, colour: int) -> bool:
    """COL_MAXON written consistently: the bag + the runtime bit-0x02 and
    bit-0x20 pairs (a bag-only write is reverted by the loader)."""
    from . import writer

    writer.ensure_object_property(po, "COL_MAXON", fmt_pair((colour & 0xFFFFFF,) * 2))
    rebuild(po)
    return True


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

# a pure function of its keys, bounded and self-emptying (no event empties it)
_caches.register("colourstate.layouts", _LAYOUT_MEMO, on=())
