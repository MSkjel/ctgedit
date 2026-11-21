"""Build object records from their fields."""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from typing import Iterable, Optional, Sequence, Tuple

from .page import PageObject, parse_payload
from .textcodec import CODEC

Pair = Tuple[int, int]
Rect = Tuple[int, int, int, int]
Point = Tuple[int, int]

#: colour words (u32 0x00BBGGRR as stored; high byte 0xFF = no colour)
NO_COLOUR = 0xFF000000
BLACK = 0x00000000
WHITE = 0x00FFFFFF
GREY = 0x007F7F7F
NONE_PAIR: Pair = (NO_COLOUR, NO_COLOUR)

#: the an_str of a record with no library twin
NO_LINK = "-1"
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# dwFlags (5.5; codes.DWF)
LINK, STYLE, LOCK, BAGS, DYNAMIC = 0x1, 0x2, 0x4, 0x8, 0x10
BUTTON_FONT, BUTTON_TEXT, METADATA, GENIE = 0x40, 0x80, 0x100, 0x200

#: the size word overshoot of a free record's value bag: 11 on these
#: classes, else 10
OV11_TCS = frozenset({0x1007, 0x1009, 0x100B, 0x100D, 0x1010})


def value_overshoot(tc: int) -> int:
    return 11 if tc in OV11_TCS else 10


# --------------------------------------------------------------------------- primitives
def lpstr(s) -> bytes:
    """u16 length + the bytes (text in the ANSI code page)."""
    b = s if isinstance(s, (bytes, bytearray)) else str(s).encode(CODEC)
    return struct.pack("<H", len(b)) + bytes(b)


def pair(on: int, off: Optional[int] = None) -> bytes:
    return struct.pack("<II", on & 0xFFFFFFFF, (on if off is None else off) & 0xFFFFFFFF)


def point(p: Point) -> bytes:
    return struct.pack("<2h", int(p[0]), int(p[1]))


def rect(r: Rect) -> bytes:
    return struct.pack("<4h", *(int(v) for v in r))


def norm_rect(r: Rect) -> Rect:
    l, t, rr, b = (int(v) for v in r)
    return (min(l, rr), min(t, b), max(l, rr), max(t, b))


def slot(mode: int = 0, m=IDENTITY) -> bytes:
    """One transform slot: the mode byte + its doubles."""
    from .placement import encode_slot

    return encode_slot(int(mode), tuple(float(v) for v in m))


def slots(local=None) -> bytes:
    """The three slots: ``None`` = identity in all three, else a sequence of
    three (mode, matrix) pairs."""
    if local is None:
        return b"\x00\x00\x00"
    return b"".join(slot(m, x) for m, x in local)


def translation_slots(x: float, y: float) -> list:
    """Local = Mods = Total = T(x, y) (a top-level transform carrier)."""
    t = (1.0, 0.0, 0.0, 1.0, float(x), float(y))
    return [(1, t), (1, t), (1, t)]


def header(
    handle: int, anchor: Point, rect1: Rect, rect2: Rect, *, name: str = "", slot_list=None, reserved: int = 0
) -> bytes:
    """The geometry header."""
    return (
        struct.pack("<HH", reserved & 0xFFFF, int(handle) & 0xFFFF)
        + lpstr(name)
        + slots(slot_list)
        + point(anchor)
        + rect(rect1)
        + rect(rect2)
    )


def bag(pairs: Iterable[Tuple[str, str]] = (), ov: int = 0) -> bytes:
    """One bag: size word (with the overshoot), count, the pairs."""
    enc = [(str(k).encode(CODEC), str(v).encode(CODEC)) for k, v in pairs]
    size = 2 + sum(len(k) + len(v) + 4 for k, v in enc) + int(ov)
    out = bytearray(struct.pack("<HH", size & 0xFFFF, len(enc)))
    for k, v in enc:
        out += lpstr(k) + lpstr(v)
    return bytes(out)


def levels(level_list) -> bytes:
    """The property levels: ``level_list`` = levels of three bags, each
    bag a (pairs, overshoot) pair."""
    out = bytearray(struct.pack("<H", len(level_list) - 1))
    for lv in level_list:
        for pairs, ov in lv:
            out += bag(pairs, ov)
    return bytes(out)


def own_levels(pairs: Iterable[Tuple[str, str]] = (), ov: int = 10) -> bytes:
    """The one level of a free record: no declarations, ``pairs`` in force,
    no templates."""
    return levels([[((), 0), (tuple(pairs), ov), ((), 0)]])


def colour_state(
    state0: Pair = (BLACK, BLACK),
    *,
    maxon: Optional[Pair] = None,
    states: Sequence[Pair] = (),
    array: Sequence[Pair] = (),
    thresholds: Sequence[Tuple[int, int, int, int]] = (),
    filcol: Optional[Pair] = None,
) -> bytes:
    """The colour-state array. ``thresholds`` = (cmp, value, on, off)."""
    flag = 0x01
    out = bytearray(pair(*state0))
    if maxon is not None:
        flag |= 0x02 | 0x20
        out += pair(*maxon)
    if states:
        flag |= 0x04
        out += struct.pack("<H", len(states)) + b"".join(pair(*p) for p in states)
    if array:
        flag |= 0x08
        out += struct.pack("<H", len(array)) + b"".join(pair(*p) for p in array)
    if thresholds:
        flag |= 0x10
        out += struct.pack("<H", len(thresholds))
        for cmp_, value, on, off in thresholds:
            out += struct.pack("<BH", cmp_ & 0xFF, value & 0xFFFF) + pair(on, off)
    if maxon is not None:
        out += pair(*maxon)
    if filcol is not None:
        flag |= 0x40
        out += pair(*filcol)
    return struct.pack("<I", flag) + bytes(out)


@dataclass(frozen=True)
class Style:
    """The 54-byte style block. The defaults are CtDraw's plain style:
    no effect, solid line 1 px, hollow, depth automatic, black line and fill,
    no background, white hilight, grey lolight and shadow."""

    effects: int = 0
    line_style: int = 0
    fill_style: int = 0
    attribs: int = 0
    line_width: int = 1
    effect_depth: int = -1
    line: Pair = (BLACK, BLACK)
    fill: Pair = (BLACK, BLACK)
    background: Pair = NONE_PAIR
    hilight: Pair = (WHITE, WHITE)
    lolight: Pair = (GREY, GREY)
    shadow: Pair = (GREY, GREY)

    def flags(self) -> bytes:
        return struct.pack(
            "<5Bb",
            self.effects,
            self.line_style,
            self.fill_style,
            self.attribs,
            self.line_width,
            self.effect_depth,
        )

    def pack(self) -> bytes:
        return self.flags() + b"".join(
            pair(*p) for p in (self.line, self.fill, self.background, self.hilight, self.lolight, self.shadow)
        )

    @classmethod
    def unpack(cls, b: bytes) -> "Style":
        e, ls, fs, at, w, d = struct.unpack_from("<5Bb", b, 0)
        w6 = struct.unpack_from("<12I", b, 6)
        p = [(w6[2 * i], w6[2 * i + 1]) for i in range(6)]
        return cls(e, ls, fs, at, w, d, *p)

    def coloured(self, colour: int) -> "Style":
        """Line and fill both ``colour`` (both halves)."""
        return replace(self, line=(colour, colour), fill=(colour, colour))

    def state0(self) -> Pair:
        """State 0 of the colour-state array: the fill slot when the
        fill style is not hollow, else the line slot."""
        return self.fill if self.fill_style else self.line


DEFAULT_STYLE = Style()


def prefix(tc: int, dw: int, an: str = NO_LINK, child_count: int = 0) -> bytes:
    """The payload prefix."""
    return struct.pack("<HII", int(child_count) & 0xFFFF, tc, dw) + lpstr(an)


def record(
    tc: int,
    *,
    handle: int = 0,
    anchor: Point,
    rect1: Rect,
    rect2: Rect,
    name: str = "",
    slot_list=None,
    link: Optional[str] = None,
    props: Optional[Iterable[Tuple[str, str]]] = None,
    ov: Optional[int] = None,
    level_list=None,
    font: Optional[bytes] = None,
    caption: Optional[str] = None,
    cs: Optional[bytes] = None,
    style: Optional[Style] = None,
    cls: bytes = b"",
    dynamic: bool = False,
    lock: bool = False,
    dw: Optional[int] = None,
    an: str = NO_LINK,
    child_count: int = 0,
) -> bytes:
    """Build a whole payload from its parts."""
    if dw is None:
        dw = (
            (LINK if link is not None else 0)
            | (STYLE if style is not None else 0)
            | (LOCK if lock else 0)
            | (BAGS if props is not None or level_list is not None else 0)
            | (DYNAMIC if dynamic else 0)
            | (BUTTON_FONT if font is not None else 0)
            | (BUTTON_TEXT if caption is not None else 0)
        )
    body = bytearray(header(handle, anchor, rect1, rect2, name=name, slot_list=slot_list))
    if dw & LINK:
        body += lpstr(link or "")
    if dw & BAGS:
        if level_list is not None:
            body += levels(level_list)
        else:
            body += own_levels(props or (), value_overshoot(tc) if ov is None else ov)
    if dw & BUTTON_FONT:
        body += font or b""
    if dw & BUTTON_TEXT:
        body += lpstr(caption or "")
    if cs is None:
        cs = colour_state(style.state0() if style is not None else (BLACK, BLACK))
    body += cs
    if dw & STYLE:
        body += (style or DEFAULT_STYLE).pack()
    body += cls
    return prefix(tc, dw, an, child_count) + bytes(body)


def to_po(payload: bytes) -> PageObject:
    """The parsed record (index 0, offset 0) of a payload."""
    return parse_payload(payload)


def bounds(tc: int, rect1: Rect, style: Optional[Style], points: Optional[int] = None) -> Rect:
    """CtDraw's rect2 for rect1 + the style (5.15, geometry.predict_bounds);
    rect1 itself for a class without a modelled rule."""
    from .geometry import predict_bounds

    if style is None:
        return tuple(rect1)
    r2 = predict_bounds(tc, rect1, style.flags(), points)
    return tuple(rect1) if r2 is None else tuple(r2)


# --------------------------------------------------------------------------- class sections
def gradient_part(mode: int = 0, on: int = 0, off: int = 0) -> bytes:
    return struct.pack("<B", mode & 0xFF) + pair(on, off)


def line_part(p1: Point, p2: Point) -> bytes:
    """6.1: the endpoints in draw order."""
    return point(p1) + point(p2)


def nodes_part(nodes: Sequence[Point]) -> bytes:
    """6.2: u16 count + the vertices."""
    return struct.pack("<H", len(nodes)) + b"".join(point(p) for p in nodes)


def rect_part(r: Rect, radius: int = 0, gradient: Tuple[int, int, int] = (0, 0, 0)) -> bytes:
    """6.3: rect1 echo, corner radius, gradient."""
    return rect(r) + struct.pack("<B", radius & 0xFF) + gradient_part(*gradient)


def ellipse_part(r: Rect, start: int = 0, end: int = 0, gradient: Tuple[int, int, int] = (0, 0, 0)) -> bytes:
    """6.4: rect1 echo, start and end angle, gradient."""
    return rect(r) + struct.pack("<2h", start, end) + gradient_part(*gradient)


def polygon_part(nodes: Sequence[Point], gradient: Tuple[int, int, int] = (0, 0, 0)) -> bytes:
    """6.5: the vertex array + the gradient."""
    return nodes_part(nodes) + gradient_part(*gradient)


#: the text string section's flag by TXTMODE
_TEXT_STRING_FLAG = {0: 0x3, 1: 0x5, 2: 0x9}


def text_strings(
    txtmode: int, off: str, on: str = "", states: Sequence[str] = (), array: Sequence[str] = ()
) -> bytes:
    """6.7, the string section: flag, TXT_OFF, [TXT_ON], [STATE_n],
    [ARRAY_n]."""
    flag = _TEXT_STRING_FLAG.get(int(txtmode), 0x1)
    out = bytearray(struct.pack("<I", flag) + lpstr(off))
    if flag & 0x2:
        out += lpstr(on)
    if flag & 0x4:
        out += struct.pack("<H", len(states)) + b"".join(lpstr(s) for s in states)
    if flag & 0x8:
        out += struct.pack("<H", len(array)) + b"".join(lpstr(s) for s in array)
    return bytes(out)


def text_part(
    anchor: Point,
    caption: str,
    face: str = "Arial",
    size: int = 11,
    orient: int = 1,
    charset: Optional[int] = None,
) -> bytes:
    """6.7, the appearance part: anchor, orient (| 0x80 + charset << 8 for a
    charset that is not the system's), point size, caption, face."""
    word = orient & 0x7F
    if charset is not None:
        word |= 0x80 | (charset & 0xFF) << 8
    return point(anchor) + struct.pack("<HB", word, size & 0xFF) + lpstr(caption) + lpstr(face)


def button_font(
    face: str = "Arial",
    charset: int = 0,
    attribs: int = 0x12,
    flags: int = 1,
    size: int = 10,
    text: Pair = (BLACK, BLACK),
    background: Pair = NONE_PAIR,
) -> bytes:
    """5.11, the dwFlags 0x40 font block. ``attribs`` 0x12 = bold, centred."""
    return (
        lpstr(face)
        + struct.pack("<BB", charset & 0xFF, attribs & 0xFF)
        + struct.pack("<II", flags & 0xFFFFFFFF, size)
        + pair(*text)
        + pair(*background)
    )


def point_part(p: Point) -> bytes:
    """6.16: a point class's position (the last 4 bytes)."""
    return point(p)


#: the trend's two undecoded words after its spacing (u32 at +8, u16 at +12)
TREND_WORDS = (0, 4)


def trend_part(
    r: Rect, spacing: int = 1, pens: Sequence[Pair] = (), words: Tuple[int, int] = TREND_WORDS
) -> bytes:
    """6.16, the trend's 82-byte part for the box ``r``: point (r-1, b-1),
    height, spacing, two undecoded words, the sample count (from the width), 8
    pens and their colours (missing pens: CtDraw's default colours)."""
    from .legacy import DEFAULT_PEN_COLOURS

    l, t, rr, b = (int(v) for v in r)
    count = max(1, (rr - l) // max(1, spacing) + 1)
    pens = list(pens) + [(c, c) for c in DEFAULT_PEN_COLOURS[len(pens) :]]
    return (
        point((rr - 1, b - 1))
        + struct.pack("<HHIHHH", b - t, spacing, words[0], words[1], count, 8)
        + b"".join(pair(*p) for p in pens[:8])
    )


#: the bar's class part after its rect: not decoded, a structural template
#: with no strings (u32, u16 x3, i16 x2 = 32767, a colour pair = green, 16
#: zero bytes)
BAR_REST = struct.pack("<IHHHhh", 0, 1, 2, 0, 32767, 32767) + pair(0x0000FF00) + bytes(16)


def bar_part(r: Rect, rest: bytes = BAR_REST) -> bytes:
    """6.16: the bar's rect, then the undecoded rest."""
    return rect(r) + rest


def slider_part(p1: Point, p2: Point) -> bytes:
    """6.16: the two track points."""
    return point(p1) + point(p2)


def web_content_part(url: str, enable_print: int, r: Rect) -> bytes:
    """6.15: lpstr URL, u8 ENABLEPRINT, the class rect."""
    return lpstr(url) + struct.pack("<B", enable_print & 0xFF) + rect(r)


def symbol_set_part(declared: int, states: int, hotspot: Optional[Point] = None) -> bytes:
    """6.13: u16 declared, u16 states, [the hotspot with dwFlags 0x2]."""
    out = struct.pack("<2H", declared & 0xFFFF, states & 0xFFFF)
    return out + (point(hotspot) if hotspot is not None else b"")


def dummy_part(r: Rect, p: Point) -> bytes:
    """6.18: the hotspot rect + point."""
    return rect(r) + point(p)


def activex_part(r: Rect, storage: bytes, prog_id: str, properties: Sequence[Tuple[str, str]] = ()) -> bytes:
    """6.14: native rect, u32 size + the OLE compound file, the property list
    (ProgID, count, name / value pairs)."""
    out = bytearray(rect(r) + struct.pack("<I", len(storage)) + bytes(storage))
    out += lpstr(prog_id) + struct.pack("<H", len(properties))
    for k, v in properties:
        out += lpstr(k) + lpstr(v)
    return bytes(out)


# --------------------------------------------------------------------------- kind builders
# Every builder returns a payload (prefix + body). Its defaults are a neutral
# new object; the factory moves, sizes, colours and captions it.


def line(
    *,
    handle: int = 0,
    p1: Point = (0, 0),
    p2: Point = (10, 10),
    style: Style = DEFAULT_STYLE,
    props=(),
    rect2: Optional[Rect] = None,
    dynamic: bool = False,
) -> bytes:
    """A free line: anchor = p1, rect1 = the normalised extent."""
    r1 = norm_rect((p1[0], p1[1], p2[0], p2[1]))
    return record(
        0x01,
        handle=handle,
        anchor=p1,
        rect1=r1,
        rect2=rect2 or bounds(0x01, r1, style),
        props=props,
        style=style,
        cls=line_part(p1, p2),
        dynamic=dynamic,
    )


def _nodes_box(nodes: Sequence[Point]) -> Rect:
    xs, ys = [p[0] for p in nodes], [p[1] for p in nodes]
    return (min(xs), min(ys), max(xs), max(ys))


#: CtDraw-like pipe: 3 px, white
PIPE_STYLE = Style(line_width=3, line=(WHITE, WHITE), fill=(WHITE, WHITE), hilight=(WHITE, WHITE))


def pipe(
    *,
    handle: int = 0,
    nodes: Sequence[Point] = ((0, 0), (0, 19), (0, 19)),
    style: Style = PIPE_STYLE,
    props=(),
    anchor: Optional[Point] = None,
    rect2: Optional[Rect] = None,
    dynamic: bool = False,
) -> bytes:
    """A pipe: anchor = the first vertex, rect1 = their box."""
    r1 = _nodes_box(nodes)
    return record(
        0x0B,
        handle=handle,
        anchor=anchor or tuple(nodes[0]),
        rect1=r1,
        rect2=rect2 or bounds(0x0B, r1, style),
        props=props,
        style=style,
        cls=nodes_part(nodes),
        dynamic=dynamic,
    )


#: a solid polygon, dark red line and fill
POLYGON_STYLE = Style(fill_style=1).coloured(0x00000080)


def polygon(
    *,
    handle: int = 0,
    nodes: Sequence[Point] = ((15, 0), (15, 10), (0, 5), (15, 0)),
    style: Style = POLYGON_STYLE,
    gradient=(0, 0, 0),
    props=(),
    anchor: Optional[Point] = None,
    rect2: Optional[Rect] = None,
    dynamic: bool = False,
) -> bytes:
    """A polygon, closed unless ``style.attribs`` bit 0."""
    r1 = _nodes_box(nodes)
    return record(
        0x10,
        handle=handle,
        anchor=anchor or tuple(nodes[0]),
        rect1=r1,
        rect2=rect2 or bounds(0x10, r1, style),
        props=props,
        style=style,
        cls=polygon_part(nodes, gradient),
        dynamic=dynamic,
    )


#: the outline rectangle: hollow
RECT_OUTLINE_STYLE = Style(lolight=(0x00999999, 0x00999999))
#: the filled rectangle: solid light grey
RECT_STYLE = Style(fill_style=1, lolight=(0x00BFBFBF, 0x00BFBFBF)).coloured(0x00E5E5E5)


def rectangle(
    *,
    handle: int = 0,
    rect1: Rect = (0, 0, 40, 20),
    style: Style = RECT_STYLE,
    radius: int = 0,
    gradient=(0, 0, 0),
    props=(),
    maxon: Optional[Pair] = None,
    anchor: Optional[Point] = None,
    rect2: Optional[Rect] = None,
    dynamic: bool = False,
) -> bytes:
    """A rectangle: anchor = (l, b), the rect1 echo, radius, gradient.
    ``maxon`` = a COL_MAXON pair mirrored in the colour state."""
    r1 = norm_rect(rect1)
    cs = colour_state(style.state0(), maxon=maxon)
    return record(
        0x0F,
        handle=handle,
        anchor=anchor or (r1[0], r1[3]),
        rect1=r1,
        rect2=rect2 or bounds(0x0F, r1, style),
        props=props,
        cs=cs,
        style=style,
        cls=rect_part(r1, radius, gradient),
        dynamic=dynamic,
    )


#: the outline ellipse: hollow, yellow
ELLIPSE_OUTLINE_STYLE = Style(hilight=(0x00FFFF00, 0x00FFFF00), lolight=(0x00D5D500, 0x00D5D500)).coloured(
    0x00D5D500
)
#: the filled ellipse: solid teal
ELLIPSE_STYLE = Style(
    fill_style=1, hilight=(0x00D5FFFF, 0x00D5FFFF), lolight=(0x00002B2B, 0x00002B2B), shadow=(BLACK, BLACK)
).coloured(0x0000AAAA)


def ellipse(
    *,
    handle: int = 0,
    rect1: Rect = (0, 0, 40, 40),
    style: Style = ELLIPSE_STYLE,
    start: int = 0,
    end: int = 0,
    gradient=(0, 0, 0),
    props=(),
    maxon: Optional[Pair] = None,
    anchor: Optional[Point] = None,
    rect2: Optional[Rect] = None,
    dynamic: bool = False,
) -> bytes:
    """An ellipse: anchor = (l + (w-1)//2, t), the rect1 echo, angles,
    gradient."""
    r1 = norm_rect(rect1)
    cs = colour_state(style.state0(), maxon=maxon)
    if anchor is None:
        anchor = (r1[0] + (r1[2] - r1[0] - 1) // 2, r1[1])
    return record(
        0x11,
        handle=handle,
        anchor=anchor,
        rect1=r1,
        rect2=rect2 or bounds(0x11, r1, style),
        props=props,
        cs=cs,
        style=style,
        cls=ellipse_part(r1, start, end, gradient),
        dynamic=dynamic,
    )


DEFAULT_TEXT = "Text"


def text(
    *,
    handle: int = 0,
    caption: str = DEFAULT_TEXT,
    anchor: Point = (0, 13),
    rect1: Rect = (0, 0, 22, 16),
    face: str = "Arial",
    size: int = 11,
    orient: int = 1,
    charset: Optional[int] = None,
    txtmode: int = 0,
    on: str = "",
    states: Sequence[str] = (),
    array: Sequence[str] = (),
    props=None,
    style: Style = DEFAULT_STYLE,
    rect2: Optional[Rect] = None,
    dynamic: bool = True,
) -> bytes:
    """A text. ``props`` defaults to TXT_OFF = the caption. rect1 is the
    measured box (the factory measures it again); a text is always dynamic."""
    if props is None:
        props = (("TXT_OFF", caption),)
    cls = text_strings(txtmode, caption, on, states, array) + text_part(
        anchor, caption, face, size, orient, charset
    )
    return record(
        0x05,
        handle=handle,
        anchor=anchor,
        rect1=rect1,
        rect2=rect2 or bounds(0x05, rect1, style, size),
        props=props,
        style=style,
        cls=cls,
        dynamic=dynamic,
    )


DEFAULT_BUTTON = "Button"
#: a green XP button
BUTTON_STYLE = Style().coloured(0x0060CA95)


def button(
    *,
    handle: int = 0,
    caption: str = DEFAULT_BUTTON,
    rect1: Rect = (0, 0, 60, 20),
    props=None,
    font: Optional[bytes] = None,
    style: Style = BUTTON_STYLE,
    anchor: Optional[Point] = None,
    rect2: Optional[Rect] = None,
    dynamic: bool = True,
) -> bytes:
    """A button: the bags, the font block, the caption (= TEXT), the
    rect1 echo. ``props`` defaults to TEXT, FONT, WIDTH, HEIGHT, VIS_MODE."""
    r1 = norm_rect(rect1)
    if props is None:
        props = (
            ("TEXT", caption),
            ("FONT", "ButtonFont"),
            ("WIDTH", str(r1[2] - r1[0])),
            ("HEIGHT", str(r1[3] - r1[1])),
            ("VIS_MODE", "XP_STYLE"),
        )
    return record(
        0x1001,
        handle=handle,
        anchor=anchor or (r1[0], r1[3]),
        rect1=r1,
        rect2=rect2 or bounds(0x1001, r1, style),
        props=props,
        font=button_font() if font is None else font,
        caption=caption,
        style=style,
        cls=rect(r1),
        dynamic=dynamic,
    )


DEFAULT_SYMBOL_LINK = "fxlib.fxsymbol.<ctl"


def symbol(
    *,
    handle: int = 0,
    link: str = DEFAULT_SYMBOL_LINK,
    slot_list=None,
    anchor: Point = (0, 15),
    rect1: Rect = (0, 0, 16, 16),
    rect2: Optional[Rect] = None,
    state0: Pair = (BLACK, BLACK),
    props=(),
    an: str = NO_LINK,
    dynamic: bool = True,
) -> bytes:
    """A placed library symbol: linked, no style block (its art is in
    the library), the position in Local (``slot_list``)."""
    return record(
        0x07,
        handle=handle,
        anchor=anchor,
        rect1=rect1,
        rect2=rect2 or rect1,
        slot_list=slot_list or translation_slots(0, 0),
        link=link,
        props=props,
        cs=colour_state(state0),
        an=an,
        dynamic=dynamic,
    )


#: the V3 / V4 classes' style: red
LEGACY_STYLE = Style().coloured(0x000000FF)


def point_record(
    tc: int,
    *,
    handle: int = 0,
    at: Point = (0, 0),
    deltas: Rect = (0, 0, 1, 1),
    props=(),
    style: Style = LEGACY_STYLE,
    ov: Optional[int] = None,
    dynamic: bool = True,
) -> bytes:
    """A V3 / V4 point class: anchor = the point, rect1 = 1 x 1, rect2 =
    the natural extent ``deltas`` about the point, the class part = the point."""
    x, y = (int(v) for v in at)
    r2 = (x + deltas[0], y + deltas[1], x + deltas[2], y + deltas[3])
    return record(
        tc,
        handle=handle,
        anchor=(x, y),
        rect1=(x, y, x + 1, y + 1),
        rect2=r2,
        props=props,
        ov=ov,
        style=style,
        cls=point_part((x, y)),
        dynamic=dynamic,
    )


def cicode_object(*, handle: int = 0, at: Point = (0, 16), props=(), **kw) -> bytes:
    """A Cicode object (0x1009): no command, the 31 x 17 icon."""
    return point_record(0x1009, handle=handle, at=at, deltas=(0, -16, 31, 1), props=props, **kw)


#: a new keyboard command's keys, blank
KEYBOARD_PROPS = (("KEY_SEQ", ""), ("CMD", ""))


def keyboard(
    *, handle: int = 0, at: Point = (0, 0), props=KEYBOARD_PROPS, style: Style = DEFAULT_STYLE, **kw
) -> bytes:
    """A keyboard command (0x100B, 6.17): the 33 x 23 icon."""
    return point_record(0x100B, handle=handle, at=at, deltas=(0, 0, 33, 23), props=props, style=style, **kw)


#: a new touch command: the left button, CtDraw's repeat rate
TOUCH_PROPS = (("ACTION", "KEY_LBUTTON"), ("RPT_RATE", "500"))


def touch(
    *, handle: int = 0, at: Point = (0, 0), props=TOUCH_PROPS, style: Style = DEFAULT_STYLE, **kw
) -> bytes:
    """A touch command (0x1010, 6.17): the 33 x 23 icon."""
    return point_record(0x1010, handle=handle, at=at, deltas=(0, 0, 33, 23), props=props, style=style, **kw)


ANIMATED_SYMBOL_PROPS = (("EXPR", ""), ("COMMENT", ""), ("MODE", "ANM_ARRAY"))


def animated_symbol(
    *, handle: int = 0, at: Point = (0, 17), props=ANIMATED_SYMBOL_PROPS, deltas: Rect = (0, -17, 20, 1), **kw
) -> bytes:
    """An animated symbol (0x100D): no states yet."""
    return point_record(0x100D, handle=handle, at=at, deltas=deltas, props=props, **kw)


STRING_PROPS = (
    ("OFF_FONT", "DefaultFont"),
    ("ON_FONT", "DefaultFont"),
    ("EXPR", ""),
    ("OFF_TEXT", ""),
    ("ON_TEXT", ""),
    ("COMMENT", ""),
)


def string(
    *, handle: int = 0, at: Point = (0, 14), props=STRING_PROPS, deltas: Rect = (0, -12, 64, 5), **kw
) -> bytes:
    """A string (0x1005): DefaultFont, no texts yet."""
    return point_record(0x1005, handle=handle, at=at, deltas=deltas, props=props, **kw)


DIGITAL_TEXT_PROPS = (("EXPR", ""), ("COMMENT", ""), ("STR000", ""), ("FONT000", "DefaultFont"))


def digital_text(
    *,
    handle: int = 0,
    at: Point = (0, 12),
    props=DIGITAL_TEXT_PROPS,
    deltas: Rect = (0, -12, 64, 5),
    ov: int = 11,
    **kw,
) -> bytes:
    """A digital text (0x1006, TextAn)."""
    return point_record(0x1006, handle=handle, at=at, deltas=deltas, props=props, ov=ov, **kw)


def color_flood(*, handle: int = 0, at: Point = (0, 14), props=(("CMD", ""),), ov: int = 11, **kw) -> bytes:
    """A color flood (0x1008, ColourAn): the 16 x 15 icon."""
    return point_record(0x1008, handle=handle, at=at, deltas=(0, -14, 16, 1), props=props, ov=ov, **kw)


def trend(
    *,
    handle: int = 0,
    rect1: Rect = (0, 0, 201, 188),
    spacing: int = 1,
    pens: Sequence[Pair] = (),
    words: Tuple[int, int] = TREND_WORDS,
    anchor: Optional[Point] = None,
    rect2: Optional[Rect] = None,
    props=(),
    style: Style = LEGACY_STYLE,
    dynamic: bool = True,
) -> bytes:
    """A trend (0x1003, always dynamic): the 82-byte part; anchor and rect2 in
    the 'current' form (anchor (l, b), rect2 = rect1 + (0, 0, 1, 1))."""
    l, t, r, b = norm_rect(rect1)
    if anchor is None:
        anchor = (l, b)
    if rect2 is None:
        rect2 = (l, t, r + 1, b + 1)
    return record(
        0x1003,
        handle=handle,
        anchor=anchor,
        rect1=(l, t, r, b),
        rect2=rect2,
        props=props,
        style=style,
        cls=trend_part((l, t, r, b), spacing, pens, words),
        dynamic=dynamic,
    )


BAR_PROPS = (("EXPR", ""), ("COMMENT", ""))


def bar_graph(
    *,
    handle: int = 0,
    rect1: Rect = (0, 0, 19, 153),
    props=BAR_PROPS,
    rest: bytes = BAR_REST,
    style: Style = LEGACY_STYLE,
    dynamic: bool = True,
) -> bytes:
    """A bar graph (0x1002): rect1 = rect2 = the class rect, anchor (l, b - 1)."""
    r1 = norm_rect(rect1)
    return record(
        0x1002,
        handle=handle,
        anchor=(r1[0], r1[3] - 1),
        rect1=r1,
        rect2=r1,
        props=props,
        style=style,
        cls=bar_part(r1, rest),
        dynamic=dynamic,
    )


SLIDER_PROPS = (("TAG", ""), ("THUMB", ""), ("Y_SPAN", "-153"), ("MODE", "WRITE_ON_DRAG"), ("COMMENT", ""))


def slider(
    *,
    handle: int = 0,
    p1: Point = (11, 153),
    p2: Point = (11, 0),
    props=SLIDER_PROPS,
    thumb: Rect = (-11, 0, 11, 0),
    style: Style = LEGACY_STYLE,
    ov: int = 11,
    dynamic: bool = True,
) -> bytes:
    """A slider (0x100F): the two track points; rect1 = their box + 1, anchor
    = p1, rect2 = rect1 grown by the thumb deltas (l, t, r, b)."""
    l, t, r, b = norm_rect((p1[0], p1[1], p2[0], p2[1]))
    r1 = (l, t, r + 1, b + 1)
    r2 = (r1[0] + thumb[0], r1[1] + thumb[1], r1[2] + thumb[2], r1[3] + thumb[3])
    return record(
        0x100F,
        handle=handle,
        anchor=p1,
        rect1=r1,
        rect2=r2,
        props=props,
        ov=ov,
        style=style,
        cls=slider_part(p1, p2),
        dynamic=dynamic,
    )


def web_content(
    *,
    handle: int = 0,
    rect1: Rect = (0, 0, 160, 90),
    name: Optional[str] = None,
    url: str = "",
    enable_print: int = 0,
    props=None,
    style: Style = DEFAULT_STYLE,
    dynamic: bool = True,
) -> bytes:
    """A Web Content object: named WebContent_<handle> (the header name
    and __AN_NAME__), the URL string section, the class rect."""
    r1 = norm_rect(rect1)
    if name is None:
        name = "WebContent_%d" % (handle or 1)
    if props is None:
        props = (("__AN_NAME__", name),)
    return record(
        0x13,
        handle=handle,
        anchor=(r1[0], r1[3]),
        rect1=r1,
        rect2=r1,
        name=name,
        props=props,
        style=style,
        cls=web_content_part(url, enable_print, r1),
        dynamic=dynamic,
    )


#: a new set's colour: a teal line and fill
SYMBOL_SET_STYLE = Style().coloured(0x008080FF)
SYMBOL_SET_PROPS = (("TXT_A", ""), ("TXTMODE", "0"))


def symbol_set(
    *,
    handle: int = 0,
    hotspot: Point = (0, 0),
    declared: int = 0,
    states: int = 0,
    local: Point = (0, 0),
    props=SYMBOL_SET_PROPS,
    anchor: Optional[Point] = None,
    rect1: Optional[Rect] = None,
    rect2: Optional[Rect] = None,
    style: Style = SYMBOL_SET_STYLE,
    child_count: int = 0,
    dynamic: bool = True,
) -> bytes:
    """A free symbol set: a transform carrier (Local = T(local)), the
    hotspot in its frame, ``declared`` / ``states`` counts. With no geometry
    given it is the empty set's: anchor = Total(hotspot), a 1 x 1 extent and a
    22 x 22 bounds box."""
    ax, ay = (int(local[0] + hotspot[0]), int(local[1] + hotspot[1]))
    anchor = anchor or (ax, ay)
    rect1 = rect1 or (anchor[0], anchor[1], anchor[0] + 1, anchor[1] + 1)
    rect2 = rect2 or (anchor[0], anchor[1], anchor[0] + 22, anchor[1] + 22)
    return record(
        0x0C,
        handle=handle,
        anchor=anchor,
        rect1=rect1,
        rect2=rect2,
        slot_list=translation_slots(*local),
        props=props,
        style=style,
        cls=symbol_set_part(declared, states, hotspot),
        child_count=child_count,
        dynamic=dynamic,
    )


def dummy(
    *,
    handle: int = 0,
    hotspot: Point = (0, 0),
    slot_list=None,
    style: Style = DEFAULT_STYLE,
    props=(),
    cs: Optional[bytes] = None,
    dynamic: bool = True,
) -> bytes:
    """A DummyMarker for an empty symbol-set state at ``hotspot``: Local
    = T(hotspot) (``slot_list`` adds the set's frame in Total), the class part
    = the hotspot rect + point, the header = that rect under Total."""
    from .placement import apply_rect

    hx, hy = (int(v) for v in hotspot)
    r = (hx, hy, hx + 1, hy + 1)
    slot_list = slot_list or translation_slots(hx, hy)
    m3, s3 = slot_list[2]
    R = apply_rect(s3, m3, r)
    r1 = tuple(int(v) for v in R)
    return record(
        0xFFFF,
        handle=handle,
        anchor=r1[:2],
        rect1=r1,
        rect2=r1,
        slot_list=slot_list,
        props=props,
        cs=cs,
        style=style,
        cls=dummy_part(r, (hx, hy)),
        dynamic=dynamic,
    )


#: the made-up control of :func:`activex` (no real control has this CLSID)
FX_CONTROL_CLSID = "{6A3F1C2E-5B7D-4E8A-9C0F-1D2E3F405162}"
FX_CONTROL_PROGID = "FxControls.FxGauge"


def activex(
    *,
    handle: int = 0,
    rect1: Rect = (0, 0, 100, 100),
    clsid: str = FX_CONTROL_CLSID,
    prog_id: str = FX_CONTROL_PROGID,
    properties: Sequence[Tuple[str, str]] = (),
    contents: Optional[bytes] = None,
    props=(),
    storage: Optional[bytes] = None,
    style: Style = DEFAULT_STYLE,
    dynamic: bool = True,
) -> bytes:
    """An ActiveX record around a synthetic OLE compound file
    (:func:`ctgedit.format.cfb.control_storage`: ``clsid`` + a Contents stream
    with the extent in HIMETRIC). ``properties`` = the trailing property list;
    the record's own ``ACTIVEX_`` / ``ACTIVEXEXT_`` keys go in ``props``."""
    from . import cfb

    r1 = norm_rect(rect1)
    if storage is None:
        storage = cfb.control_storage(clsid, r1[2] - r1[0], r1[3] - r1[1], contents)
    return record(
        0x0D,
        handle=handle,
        anchor=(r1[0], r1[3]),
        rect1=r1,
        rect2=r1,
        props=props,
        style=style,
        cls=activex_part(r1, storage, prog_id, properties),
        dynamic=dynamic,
    )


def group(
    *,
    handle: int = 0,
    rect1: Rect = (0, 0, 1, 1),
    rect2: Optional[Rect] = None,
    child_count: int = 0,
    style: Style = DEFAULT_STYLE,
) -> bytes:
    """A free group (6.11; CtDraw's Arrange > Group): dwFlags 0xA, anchor =
    (l, b - 1), rect1 / rect2 = the union of the members'."""
    l, t, r, b = (int(v) for v in rect1)
    return record(
        0x08,
        handle=handle,
        anchor=(l, b - 1),
        rect1=(l, t, r, b),
        rect2=tuple(rect2 or rect1),
        props=(),
        style=style,
        child_count=child_count,
    )


#: the tail of a free group (empty level, black state 0, the plain style):
#: also the Dynamic CTF Bitmap's (ctf.background_record)
GROUP_TAIL = own_levels() + colour_state() + DEFAULT_STYLE.pack()
#: the embedded picture's tail before its class part (bitmap and metafile):
#: the empty level, the state-0 pair and the style in CtDraw's picture colour
PICTURE_COLOUR = 0x002B2BFF
PICTURE_STYLE = Style().coloured(PICTURE_COLOUR)
PICTURE_TAIL = own_levels() + colour_state(PICTURE_STYLE.state0()) + PICTURE_STYLE.pack()


#: kind -> builder (the factory's object kinds; 'number' is a text,
#: 'polyline' a polygon, the two keyboards one class)
BUILDERS = {
    "text": text,
    "number": text,
    "rectangle": rectangle,
    "rectangle_outline": rectangle,
    "ellipse": ellipse,
    "ellipse_outline": ellipse,
    "ellipse_block": ellipse,
    "line": line,
    "button": button,
    "pipe": pipe,
    "polygon": polygon,
    "symbol": symbol,
    "cicode_object": cicode_object,
    "keyboard": keyboard,
    "touch": touch,
    "animated_symbol": animated_symbol,
    "string": string,
    "trend": trend,
    "bar_graph": bar_graph,
    "slider": slider,
    "web_content": web_content,
    "digital_text": digital_text,
    "color_flood": color_flood,
    "symbol_set": symbol_set,
    "symbol_set_dummy": dummy,
    "activex": activex,
    "group": group,
}
#: the per-kind fields that make a variant of a shared builder
VARIANTS = {
    "number": {"caption": "####.#"},  # CtDraw's value placeholder
    "rectangle_outline": {"style": RECT_OUTLINE_STYLE, "rect1": (0, 0, 148, 5)},
    "ellipse_outline": {"style": ELLIPSE_OUTLINE_STYLE},
    "ellipse_block": {"style": ELLIPSE_OUTLINE_STYLE},
}


def build(kind: str, **fields) -> bytes:
    """The payload of a new ``kind`` record (``fields`` override the
    builder's defaults). Raises KeyError for an unknown kind."""
    fn = BUILDERS[kind]
    kw = dict(VARIANTS.get(kind, {}))
    kw.update(fields)
    return fn(**kw)


def new_record(kind: str, **fields) -> PageObject:
    """:func:`build` parsed into a PageObject."""
    return to_po(build(kind, **fields))
