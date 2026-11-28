"""Old V3/V4 library-class records: placement and creation."""

from __future__ import annotations

import struct
import threading
from typing import Optional, Tuple

from . import geometry as G
from .page import PageObject
from . import codes as _codes

# + the input classes Touch 0x1010 and 0x1000: the engine snaps a moved Touch back without it
# + Number 0x1004 (its class section holds ONE point, like String 0x1005)
POINT_TCS = frozenset(
    {0x1000, 0x1004, 0x1005, 0x1006, 0x1008, 0x1009, _codes.TC.KEYBOARD, 0x100D, _codes.TC.TOUCH}
)
TREND_TC = 0x1003
TREND_SECTION = 82  # point + u16 h, sp, u32, u16, count, 8 pens + 8 pairs
ANIMSYM_STATES = ("OFF_SYMBOL",) + tuple("SYMBOL_%d" % i for i in range(1, 9))

# engine-derived natural extents (rect2 - anchor) of a FRESH object
RECT2_DELTAS = {
    0x1009: (0, -16, 31, 1),  # AdvancedAnimation icon, no SYMBOL
    _codes.TC.KEYBOARD: (0, 0, 33, 23),  # Keyboard icon
    0x1008: (0, -14, 16, 1),  # ColourAn
}
COURIER_CHAR_W = 8  # DefaultFont / FontSP = Courier New 14


def _refresh(po: PageObject) -> None:
    from .writer import refresh_object_bags

    refresh_object_bags(po)


def class_tail(po: PageObject) -> Optional[Tuple[str, int]]:
    """('point', off) / ('trend', off) when the record carries a V3/V4 class
    position section consistent with its header, else None (library copies,
    transform-positioned keyboards, other classes)."""
    if not (po.subtype_size & 0x2):
        return None
    gh = G.parse_geom_header(po.body_raw)
    if gh is None:
        return None
    b = po.body_raw
    if (
        po.type_code in POINT_TCS
        and len(b) - 4 >= gh.end
        and struct.unpack_from("<2h", b, len(b) - 4) == tuple(gh.anchor)
    ):
        return "point", len(b) - 4
    if po.type_code == TREND_TC and len(b) - TREND_SECTION >= gh.end:
        off = len(b) - TREND_SECTION
        px, py = struct.unpack_from("<2h", b, off)
        npens = struct.unpack_from("<H", b, off + 16)[0]
        if npens == 8 and (px, py) == (gh.rect1[2] - 1, gh.rect1[3] - 1):
            return "trend", off
    return None


def shift_class_point(po: PageObject, off: int, dx: int, dy: int) -> None:
    body = bytearray(po.body_raw)
    x, y = struct.unpack_from("<2h", body, off)
    struct.pack_into("<2h", body, off, x + dx, y + dy)
    po.body_raw = bytes(body)


def place_point(po: PageObject, x: int, y: int) -> None:
    """Put a point-class record's class position at (x, y): anchor, 1x1
    rect1, rect2 (same natural-extent deltas) and the section point together."""
    body = bytearray(po.body_raw)
    gh = G.parse_geom_header(bytes(body))
    ax, ay = gh.anchor
    dx, dy = x - ax, y - ay
    l2, t2, r2, b2 = gh.rect2
    struct.pack_into("<2h", body, gh.anchor_off, x, y)
    struct.pack_into("<4h", body, gh.rect1_off, x, y, x + 1, y + 1)
    struct.pack_into("<4h", body, gh.rect2_off, l2 + dx, t2 + dy, r2 + dx, b2 + dy)
    struct.pack_into("<2h", body, len(body) - 4, x, y)
    po.body_raw = bytes(body)
    _refresh(po)


def set_point_extent(po: PageObject, deltas) -> None:
    """rect2 = anchor + deltas (the class's natural extent)."""
    body = bytearray(po.body_raw)
    gh = G.parse_geom_header(bytes(body))
    ax, ay = gh.anchor
    struct.pack_into(
        "<4h", body, gh.rect2_off, ax + deltas[0], ay + deltas[1], ax + deltas[2], ay + deltas[3]
    )
    po.body_raw = bytes(body)
    _refresh(po)


def place_trend(po: PageObject, rect, convention: Optional[str] = None):
    """Size a trend to a rect."""
    l, t, r, b = (int(v) for v in G.norm_rect(rect))
    off = len(po.body_raw) - TREND_SECTION
    body = bytearray(po.body_raw)
    gh = G.parse_geom_header(bytes(body))
    if convention is None:
        convention = "legacy" if tuple(gh.anchor) == (gh.rect1[0], gh.rect1[3] - 1) else "current"
    sp = max(1, struct.unpack_from("<H", body, off + 6)[0])
    count = max(1, (r - l) // sp + 1)
    l = r - sp * (count - 1)
    if convention == "current":
        anchor, r2 = (l, b), (l, t, r + 1, b + 1)
    else:
        anchor, r2 = (l, b - 1), (l, t, r, b)
    struct.pack_into("<2h", body, gh.anchor_off, *anchor)
    struct.pack_into("<4h", body, gh.rect1_off, l, t, r, b)
    struct.pack_into("<4h", body, gh.rect2_off, *r2)
    struct.pack_into("<2h", body, off, r - 1, b - 1)
    struct.pack_into("<H", body, off + 4, b - t)
    struct.pack_into("<H", body, off + 14, count)
    po.body_raw = bytes(body)
    _refresh(po)
    return (l, t, r, b)


# -- the Trend's pens and samples (CtDraw's Trend page: load /
# apply) -----------------------------------------------------------
#: CtDraw's default pen colours (0x00RRGGBB): a new trend's
#: pens, and what the apply resets a pen to that has no row in the
#: page's pen list (the list always holds all 8, so an emptied row keeps its
#: colour: only its PEN<n> key goes)
DEFAULT_PEN_COLOURS = (0x00FF00, 0xFFFF00, 0xFF0000, 0x00FFFF, 0xFF00FF, 0xFFFFFF, 0x0000FF, 0xBFBFBF)
TREND_PENS = 8
MAX_PEN = 79  # the pen row's edit takes 79 characters
MAX_SAMPLES = 32767  # 'Number of samples' spin (1 .. 32767)
MAX_SPACING = 32  # 'Pixels per sample' spin (1 .. 32)


def trend_section(po: PageObject) -> Optional[int]:
    """Offset of a free Trend's 82-byte class section, None for a library
    copy (dwFlags 0x2 clear: its pens come from the library twin)."""
    ct = class_tail(po)
    return ct[1] if ct is not None and ct[0] == "trend" else None


def trend_info(po: PageObject) -> Optional[dict]:
    """The Trend class section: ``spacing`` (pixels per sample), ``samples``
    (the sample count), ``height`` and ``pens`` = 8 (on, off) colour pairs
    (0x00RRGGBB; high byte 0xFF = no colour: that pen draws nothing). None
    when the record has no free class section."""
    off = trend_section(po)
    if off is None:
        return None
    b = po.body_raw
    h, sp = struct.unpack_from("<HH", b, off + 4)
    count = struct.unpack_from("<H", b, off + 14)[0]
    pairs = struct.unpack_from("<16I", b, off + 18)
    return {
        "height": h,
        "spacing": sp,
        "samples": count,
        "pens": [(pairs[2 * i], pairs[2 * i + 1]) for i in range(TREND_PENS)],
    }


def set_trend_pen_colours(po: PageObject, pens) -> bool:
    """Write the 8 pen colour pairs of the class section (``pens`` = up to 8
    (on, off) pairs; a missing pair keeps the stored one). The class saver
    writes them after the sample count; the runtime draws the
    pens in them. False when the record has no free class section."""
    off = trend_section(po)
    if off is None:
        return False
    body = bytearray(po.body_raw)
    for i, pair in enumerate(list(pens)[:TREND_PENS]):
        if pair is None:
            continue
        on, off_c = pair
        struct.pack_into("<II", body, off + 18 + 8 * i, on & 0xFFFFFFFF, off_c & 0xFFFFFFFF)
    po.body_raw = bytes(body)
    return True


def set_trend_spacing(po: PageObject, spacing: int) -> bool:
    """Store 'Pixels per sample' (the class section's u16 at +6) without
    moving the trend; :func:`place_trend` / :func:`set_trend_samples` then
    size it on that grid. False without a class section."""
    off = trend_section(po)
    if off is None:
        return False
    body = bytearray(po.body_raw)
    struct.pack_into("<H", body, off + 6, max(1, min(MAX_SPACING, int(spacing))))
    po.body_raw = bytes(body)
    return True


def set_trend_samples(po: PageObject, samples: int, spacing: int):
    """CtDraw's apply of 'Number of samples' / 'Pixels per sample'
    : the RIGHT edge stays, left = right - spacing x (samples
        - 1); the class saver derives the count back from the width.
        Returns the new rect, None without a class section."""
    off = trend_section(po)
    if off is None:
        return None
    samples = max(1, min(MAX_SAMPLES, int(samples)))
    gh = G.parse_geom_header(po.body_raw)
    _l, t, r, b = G.norm_rect(gh.rect1)
    set_trend_spacing(po, spacing)
    return place_trend(po, (r - max(1, min(MAX_SPACING, int(spacing))) * (samples - 1), t, r, b))


def move_to_rect(po: PageObject, rect) -> bool:
    """Resize semantics for class-positioned records: a Trend takes the new
    rect; a point class has an engine-derived size, so it MOVES so its drawn
    box starts at the rect's top-left. False when not class-positioned."""
    ct = class_tail(po)
    if ct is None:
        return False
    if ct[0] == "trend":
        place_trend(po, rect)
        return True
    gh = G.parse_geom_header(po.body_raw)
    l, t = G.norm_rect(rect)[:2]
    d2l, d2t = gh.rect2[0] - gh.anchor[0], gh.rect2[1] - gh.anchor[1]
    place_point(po, l - d2l, t - d2t)
    return True


def text_extent_deltas(display: Optional[str], placeholder: str = "########"):
    """Natural extent of a String / TextAn caption in DefaultFont (Courier New
    14, 8 px/char, rows -12..+5): the placeholder when the value is not a
    literal / empty; width at least 10."""
    txt = display if display else placeholder
    return (0, -12, max(10, COURIER_CHAR_W * len(txt)), 5)


def animsym_extent_deltas(w: int, h: int):
    """AnimSymbol natural extent = the first resolvable state's bitmap, its
    bottom-left at the anchor."""
    return (0, -(h - 1), w, 1)


# -- the Bounds CtDraw's load derives for a Number (0x1004) and a String (0x1005) ---------
NUMBER_TC, STRING_TC = 0x1004, 0x1005
#: the Number's mask when its expression gives none
NUMBER_MASK = "####.#"
#: the String's text when its OFF / ON text is no literal
STRING_MASK = "########"
#: the font a Number or String without one names (CtDraw's default)
DEFAULT_FONT = "DefaultFont"

#: the fonts.dbf stamps of the folders searched -> {name (lower case): its row}
_FONT_ROWS: dict = {}
_FONT_ROWS_LOCK = threading.Lock()


def font_rows(dirs) -> dict:
    """The fonts.dbf rows of the folders ``dirs``, searched in order (the first definition of
    a name wins, as CtDraw looks them up): name (lower case) -> its row. Cached while the
    tables are unchanged."""
    from .fileio import file_stamp, find_ci
    from .projects import read_dbf_rows

    key = tuple(s for s in (file_stamp(find_ci(d, "fonts.dbf")) for d in dirs) if s is not None)
    with _FONT_ROWS_LOCK:
        got = _FONT_ROWS.get(key)
    if got is not None:
        return got
    out: dict = {}
    for f, _m, _s in key:
        try:
            rows = read_dbf_rows(f)
        except Exception:  # noqa: BLE001
            from ..infra.logsetup import note_exception

            note_exception("legacy.font_rows", page=f)
            continue
        for row in rows:
            name = (row.get("NAME") or "").strip().lower()
            if name and name not in out:
                out[name] = row
    with _FONT_ROWS_LOCK:
        if len(_FONT_ROWS) > 64:
            _FONT_ROWS.clear()
        return _FONT_ROWS.setdefault(key, out)


def font_size(row: dict) -> int:
    """A fonts.dbf row's SIZE: pixels when positive, points when not (0 when unreadable)."""
    try:
        return int(float((row.get("SIZE") or "0").strip() or "0"))
    except ValueError:
        return 0


def named_fonts(path: str) -> dict:
    """The fonts.dbf fonts a page or library at ``path`` names (its project, then its
    include chain, the implicit Include last): name (lower case) -> (TYPE, SIZE), TYPE as
    stored ('Arial,B'), SIZE as :func:`font_size` reads it."""
    import os
    from .projects import include_search_dirs

    d = os.path.dirname(os.path.abspath(path or "page.ctg"))
    try:
        dirs = [d] + include_search_dirs(d)
    except Exception:  # noqa: BLE001
        from ..infra.logsetup import note_exception

        note_exception("legacy.named_fonts/dirs", page=path)
        dirs = [d]
    return {n: ((r.get("TYPE") or "").strip(), font_size(r)) for n, r in font_rows(dirs).items()}


def display_mask(expr: str, tag_format=None) -> str:
    """The display mask of a Number or String."""
    if not expr:
        return ""
    if expr[0] == "#":
        return expr
    i = expr.find(":")
    if i >= 0:
        j = expr.find("#", i)
        if j >= 0:
            return expr[j:]
    name = (expr if i < 0 else expr[:i])[:254].strip()
    if not name or tag_format is None:
        return ""
    try:
        fmt = tag_format(name)
    except Exception:  # noqa: BLE001
        from ..infra.logsetup import note_exception

        note_exception("legacy.display_mask")
        fmt = None
    return fmt or ""


def string_display(values: dict, tag_format=None) -> Tuple[str, str]:
    """(the text, the font name) a String measures. The state's text is
    OFF_TEXT, else ON_TEXT when OFF_TEXT is empty, with that state's font (OFF_FONT /
    ON_FONT); a Cicode string literal measures its characters up to the closing quote, any
    other text its :func:`display_mask`. FONT is never read."""
    state = None
    for s, (tk, _fk) in enumerate((("OFF_TEXT", "OFF_FONT"), ("ON_TEXT", "ON_FONT"))):
        if values.get(tk):
            state = s
            break
    if state is None:
        return "", values.get("OFF_FONT") or ""
    tk, fk = (("OFF_TEXT", "OFF_FONT"), ("ON_TEXT", "ON_FONT"))[state]
    text, font = values.get(tk) or "", values.get(fk) or ""
    if text[:1] == '"':
        j = text.find('"', 1)
        return (text[1:] if j < 0 else text[1:j]), font
    return display_mask(text, tag_format), font


def _string_lines(text: str):
    """A text's lines: '^n' / '^N' ends a line; the end of the text ends the
    last one (a '^n' at the very end starts none)."""
    out, start, i, n = [], 0, 0, len(text)
    while start < n:
        if i >= n:
            out.append(text[start:])
            break
        if text[i] == "^" and i + 1 < n and text[i + 1] in "nN":
            out.append(text[start:i])
            start = i = i + 2
            continue
        i += 1
    return out


_INSTALLED = object()


def value_extent(po: PageObject, fonts: dict, tag_format=_INSTALLED) -> Optional[Tuple[int, int, int, int]]:
    """The bounds CtDraw's load derives for a Number or String record."""
    if po.type_code not in (NUMBER_TC, STRING_TC) or po.raw_payload is not None:
        return None
    from . import genie_place as GP
    from . import textmetrics as TM

    try:
        r = GP.decode(po)
    except Exception:  # noqa: BLE001  # swallow-ok: an undecodable record keeps its bounds
        return None
    if r.anchor is None or r.rect2 is None:
        return None
    for m, x in r.slots or ():
        if m and tuple(x[:4]) != (1.0, 0.0, 0.0, 1.0):
            return None
    lookup = TM.installed_tag_format() if tag_format is _INSTALLED else tag_format
    values = dict(r.entries[0][1].pairs) if r.entries else {}
    x, y = r.anchor
    if po.type_code == NUMBER_TC:
        text = display_mask(values.get("EXPR") or "", lookup) or NUMBER_MASK
        name = values.get("FONT") or DEFAULT_FONT
        got = TM.named_font_lines(fonts.get(name.strip().lower()), [text])
        if got is None:
            return None
        _asc, desc, ((cx, cy),) = got
        return (x, y - cy + desc, x + cx, y + desc)
    text, name = string_display(values, lookup)
    text = text or STRING_MASK
    lines = _string_lines(text)
    got = TM.named_font_lines(fonts.get((name or DEFAULT_FONT).strip().lower()), [text] + lines)
    if got is None:
        return None
    _asc, desc, sizes = got
    top = y + 1 + desc - sizes[0][1]
    width = max([cx for cx, _cy in sizes[1:]] or [0])
    bottom = top + sum(cy for _cx, cy in sizes[1:])
    return (x, top, x + max(10, width), top + max(10, bottom - top))


def trend_extent(po: PageObject):
    """(anchor, rect2) CtDraw gives a trend stored in the old library form."""
    if po.type_code != TREND_TC or po.raw_payload is not None:
        return None
    gh = G.parse_geom_header(po.body_raw)
    if gh is None:
        return None
    l, t, r, b = gh.rect1
    if tuple(gh.anchor) != (l, b - 1) or tuple(gh.rect2) != (l, t, r, b):
        return None
    return (l, b), (l, t, r + 1, b + 1)


def save_extents(objs, fonts: Optional[dict], tag_format=_INSTALLED):
    """``(records, changed indexes)``: ``objs`` with every Number and String whose Bounds
    CtDraw's load derives otherwise (:func:`value_extent`) and every Trend in the old form
    (:func:`trend_extent`) written so (copies; the records given are not changed); ``objs``
    itself when nothing changes. ``fonts`` None: the Numbers and Strings stay as they are."""
    from . import genie_place as GP

    out, changed = None, []
    for k, po in enumerate(objs):
        tc = po.type_code
        if tc == TREND_TC:
            got = trend_extent(po)
            if got is None:
                continue
            r = GP.decode(po)
            r.anchor, r.rect2 = got
        elif tc in (NUMBER_TC, STRING_TC) and fonts is not None:
            r2 = value_extent(po, fonts, tag_format)
            if r2 is None:
                continue
            gh = G.parse_geom_header(po.body_raw)
            if gh is None or tuple(gh.rect2) == tuple(r2):
                continue
            r = GP.decode(po)
            r.rect2 = tuple(r2)
        else:
            continue
        if out is None:
            out = list(objs)
        out[k] = GP.to_po(GP.encode(r))
        changed.append(k)
    return (out if out is not None else objs), changed


from ..infra import caches as _caches  # noqa: E402

_caches.register("legacy.font_rows", _FONT_ROWS, on=("libraries", "refresh", "tests"))
