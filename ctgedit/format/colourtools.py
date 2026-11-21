"""CtDraw's colour arithmetic: HLS, Swap Colors and Adjust Colors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

NO_COLOUR = 0xFF000000
#: citect.pal's 'Transparent' entry (255 255 255 255): the value CtDraw's
#: colour picker writes for Transparent (0xff000000 is the default
#: CtDraw writes for an unset background / fill level)
TRANSPARENT = 0xFFFFFFFF

Pair = Tuple[int, int]


def is_clear(c: int) -> bool:
    """The no-colour (transparent) sentinel: high byte 0xFF."""
    return (int(c) >> 24) & 0xFF == 0xFF


def same_colour(a: int, b: int) -> bool:
    """Two stored colours are the same colour: equal, or both 'no colour'
    (CtDraw compares the 32-bit values; ctgedit also treats 0xff000000 and
    0xffffffff as the one Transparent, so a From of Transparent finds the
    shapes ctgedit's own tools made hollow)."""
    a &= 0xFFFFFFFF
    b &= 0xFFFFFFFF
    return a == b or (is_clear(a) and is_clear(b))


def same_pair(a: Pair, b: Pair) -> bool:
    return same_colour(a[0], b[0]) and same_colour(a[1], b[1])


def as_pair(value) -> Pair:
    """An int (one colour) or an (on, off) pair as a pair of u32."""
    if isinstance(value, (tuple, list)):
        on, off = value[0], value[1] if len(value) > 1 else value[0]
    else:
        on = off = value
    return int(on) & 0xFFFFFFFF, int(off) & 0xFFFFFFFF


def _round(v: float) -> int:
    """CtDraw's rounding: halves away from zero."""
    return int(v - 0.5) if v < 0 else int(v + 0.5)


# ------------------------------------------------------------------------ HLS
def rgb_to_hls(r: int, g: int, b: int) -> Tuple[int, int, int]:
    """RGB to HLS: (hue in tenths of a degree, lightness 0..255,
    saturation 0..255). Grey (max == min) has hue 0."""
    R, G, B = (r & 0xFF) / 255.0, (g & 0xFF) / 255.0, (b & 0xFF) / 255.0
    mx = max(R, G, B)
    mn = min(R, G, B)
    delta = mx - mn
    total = mx + mn
    lum = total * 0.5
    if delta == 0.0:
        hue = sat = 0.0
    else:
        sat = delta / total if 0.5 > lum else delta / ((2.0 - mx) - mn)
        if R == mx:
            hue = (G - B) / delta
        elif G == mx:
            hue = (B - R) / delta + 2.0
        else:
            hue = (R - G) / delta + 4.0
        hue *= 60.0
        if 0.0 > hue:
            hue += 360.0
    return (_round(hue * 10.0) & 0xFFFF, _round(lum * 255.0) & 0xFF, _round(sat * 255.0) & 0xFF)


def _hls_value(n1: float, n2: float, hue: float) -> float:
    if 0.0 > hue:
        hue += 360.0
    if hue > 360.0:
        hue -= 360.0
    if 60.0 > hue:
        return n1 + (n2 - n1) * (hue / 60.0)
    if 180.0 > hue:
        return n2
    if 240.0 > hue:
        return n1 + ((n2 - n1) * (240.0 - hue)) / 60.0
    return n1


def hls_to_rgb(h10: int, lum: int, sat: int) -> Tuple[int, int, int]:
    """HLS to RGB: hue in tenths, lightness / saturation 0..255 -> (r, g, b)."""
    h = (h10 & 0xFFFF) / 10.0
    l = (lum & 0xFF) / 255.0
    s = (sat & 0xFF) / 255.0
    if s == 0.0:
        r = g = b = l
    else:
        m2 = (s + 1.0) * l if 0.5 > l else (l + s) - (l * s)
        m1 = (l + l) - m2
        r = _hls_value(m1, m2, h + 120.0)
        g = _hls_value(m1, m2, h)
        b = _hls_value(m1, m2, h - 120.0)
    return (_round(r * 255.0) & 0xFF, _round(g * 255.0) & 0xFF, _round(b * 255.0) & 0xFF)


def colour_hls(c: int) -> Tuple[int, int, int]:
    c &= 0xFFFFFFFF
    return rgb_to_hls((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)


def with_hls(c: int, h10: int, lum: int, sat: int) -> int:
    """``c`` with its RGB from (h10, lum, sat); the high byte kept."""
    r, g, b = hls_to_rgb(h10, lum, sat)
    return (int(c) & 0xFF000000) | (r << 16) | (g << 8) | b


# ------------------------------------------------------------- colour operations
def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _wrap_hue(h10: int, delta: int) -> int:
    """(H + delta + 3600) % 3600 with C's idiv (a remainder of the sign of the
    dividend), stored u16."""
    v = int(h10) + _s16(delta) + 3600
    r = v % 3600 if v >= 0 else -((-v) % 3600)
    return r & 0xFFFF


def hue_in_range(h10: int, frm: int, to: int) -> bool:
    """Hue_InRange: [frm, to] in tenths, wrapping past 0 when frm > to
    (unsigned 16-bit compares)."""
    h, frm, to = h10 & 0xFFFF, frm & 0xFFFF, to & 0xFFFF
    if frm > to:
        return h <= to or h >= frm
    return frm <= h <= to


def add_hue_delta(c: int, delta: int) -> Tuple[int, bool]:
    """Turn the hue by ``delta`` tenths."""
    if is_clear(c) or _s16(delta) == 0:
        return c, False
    h, l, s = colour_hls(c)
    return with_hls(c, _wrap_hue(h, delta), l, s), True


def add_hue_delta_in_range(c: int, delta: int, frm: int, to: int) -> Tuple[int, bool]:
    """Turn the hue within a hue range: True only when the colour's hue is in
    the range AND the delta is not 0 (a zero delta answers False: the
    'Selected Hues Only' lightness / saturation then does nothing on an
    object -- CtDraw's own quirk)."""
    if is_clear(c) or _s16(delta) == 0:
        return c, False
    h, l, s = colour_hls(c)
    if not hue_in_range(h, frm, to):
        return c, False
    return with_hls(c, _wrap_hue(h, delta), l, s), True


def _pct_ok(pct: int) -> bool:
    """The functions refuse (pct + 100) above 200 unsigned: -100..100."""
    return ((int(pct) + 100) & 0xFFFF) <= 200


def _scaled(v: int, pct: int) -> int:
    return min(255, v + _round(_s16(pct) / 100.0 * v))


def adjust_lightness(c: int, pct: int) -> Tuple[int, bool]:
    """Adjust the lightness: L + L*pct/100 (at most 255)."""
    if not _pct_ok(pct) or is_clear(c) or _s16(pct) == 0:
        return c, False
    h, l, s = colour_hls(c)
    return with_hls(c, h, _scaled(l, pct), s), True


def adjust_lightness_in_range(c: int, pct: int, frm: int, to: int) -> Tuple[int, bool]:
    if not _pct_ok(pct) or is_clear(c) or _s16(pct) == 0:
        return c, False
    h, l, s = colour_hls(c)
    if not hue_in_range(h, frm, to):
        return c, False
    return with_hls(c, h, _scaled(l, pct), s), True


def adjust_saturation(c: int, pct: int) -> Tuple[int, bool]:
    """Adjust the saturation: S + S*pct/100 (at most 255): a grey stays grey."""
    if not _pct_ok(pct) or is_clear(c) or _s16(pct) == 0:
        return c, False
    h, l, s = colour_hls(c)
    return with_hls(c, h, l, _scaled(s, pct)), True


def adjust_saturation_in_range(c: int, pct: int, frm: int, to: int) -> Tuple[int, bool]:
    if not _pct_ok(pct) or is_clear(c) or _s16(pct) == 0:
        return c, False
    h, l, s = colour_hls(c)
    if not hue_in_range(h, frm, to):
        return c, False
    return with_hls(c, h, l, _scaled(s, pct)), True


def add_saturation_delta(c: int, delta: int) -> Tuple[int, bool]:
    """Add to the saturation: S + delta, clamped 0..255."""
    if is_clear(c) or _s16(delta) == 0:
        return c, False
    h, l, s = colour_hls(c)
    return with_hls(c, h, l, max(0, min(255, s + _s16(delta)))), True


def grey_in_range(c: int, frm: int, to: int) -> Tuple[int, bool]:
    """Greyscale within a hue range: a colour of the hue range -> the grey of
    its lightness; a grey is left alone."""
    if is_clear(c):
        return c, False
    h, l, s = colour_hls(c)
    if s == 0 or not hue_in_range(h, frm, to):
        return c, False
    return with_hls(c, 0, l, 0), True


def is_grey(c: int) -> bool:
    """A grey: saturation 0 (no transparency check)."""
    return colour_hls(c)[2] == 0


# ------------------------------------------------------------------ adjustment
@dataclass(frozen=True)
class Adjustment:
    """The Adjust Colors settings: hues in TENTHS."""

    hue_from: int = 0
    hue_to: int = 0
    hue_delta: int = 0
    saturation: int = 0  # % (-100..100)
    lightness: int = 0  # % (-100..100)
    saturation_selected: bool = False
    lightness_selected: bool = False
    swap_saturation: int = 0  # Swap range only: the grey <-> colour delta

    @classmethod
    def from_dialog(
        cls,
        hue_from: int,
        hue_to: int,
        to_start: int,
        lightness: int,
        saturation: int,
        light_selected: bool = False,
        sat_selected: bool = False,
    ) -> "Adjustment":
        """The Adjust Colors dialog's values (degrees 0..359, percents) as
        CtDraw's OK builds the struct: the hues x10, the delta
        (To start - From start) x10."""
        return cls(
            hue_from * 10,
            hue_to * 10,
            (to_start - hue_from) * 10,
            int(saturation),
            int(lightness),
            bool(sat_selected),
            bool(light_selected),
        )

    def is_identity(self) -> bool:
        return (
            _s16(self.hue_delta) == 0
            and _s16(self.saturation) == 0
            and _s16(self.lightness) == 0
            and _s16(self.swap_saturation) == 0
        )


def swap_adjustment(frm: Pair, to: Pair) -> Optional[Adjustment]:
    """Swap range's adjustment, or None when CtDraw does a
    plain swap instead: a flashing From / To, or a transparent From. The
    hue range is From's hue alone; the delta turns it to To's hue."""
    frm, to = as_pair(frm), as_pair(to)
    if frm[0] != frm[1] or to[0] != to[1] or is_clear(frm[0]) or is_clear(frm[1]):
        return None
    fh, _fl, fs = colour_hls(frm[0])
    th, _tl, ts = colour_hls(to[0])
    if fs == 0:
        sat = ts
    elif ts == 0:
        sat = -fs
    else:
        sat = 0
    return Adjustment(fh, fh, _s16(th - fh), 0, 0, False, False, sat)


def adjust_colour(c: int, adj: Adjustment) -> int:
    """One colour of an OBJECT's channel through CtDraw's steps (the
    pair steps applied to one half)."""
    return adjust_pair((c, c), adj)[0]


def adjust_pair(pair: Pair, adj: Adjustment) -> Pair:
    """An object's colour pair through: the global lightness /
    saturation first (unless 'Selected Hues Only'), then each half's hue in
    the range turns and -- only then -- takes the selected-hues lightness /
    saturation; Swap range's grey steps instead when ``swap_saturation``."""
    on, off = as_pair(pair)
    ss = _s16(adj.swap_saturation)
    if ss == 0:
        if not adj.lightness_selected:
            on = adjust_lightness(on, adj.lightness)[0]
            off = adjust_lightness(off, adj.lightness)[0]
        if not adj.saturation_selected:
            on = adjust_saturation(on, adj.saturation)[0]
            off = adjust_saturation(off, adj.saturation)[0]
        out = []
        for c in (on, off):
            c, hit = add_hue_delta_in_range(c, adj.hue_delta, adj.hue_from, adj.hue_to)
            if hit:
                if adj.lightness_selected:
                    c = adjust_lightness(c, adj.lightness)[0]
                if adj.saturation_selected:
                    c = adjust_saturation(c, adj.saturation)[0]
            out.append(c)
        return out[0], out[1]
    if ss < 0:
        return (
            grey_in_range(on, adj.hue_from, adj.hue_to)[0],
            grey_in_range(off, adj.hue_from, adj.hue_to)[0],
        )
    out = []
    for c in (on, off):
        if is_grey(c):
            c = add_saturation_delta(c, ss)[0]
            c = add_hue_delta(c, adj.hue_delta)[0]
        out.append(c)
    return out[0], out[1]


def adjust_pixel_fn(adj: Adjustment) -> Callable[[int], int]:
    """The bitmap class's order: a lightness
    pass (in the hue range when selected), a saturation pass, then the hue
    turn -- or Swap range's greyscale / colourize. Per pixel, a
    transparent pixel (high byte 0xFF) left alone."""
    ss = _s16(adj.swap_saturation)

    def fn(c: int) -> int:
        if is_clear(c):
            return c
        if ss == 0:
            if adj.lightness_selected:
                c = adjust_lightness_in_range(c, adj.lightness, adj.hue_from, adj.hue_to)[0]
            else:
                c = adjust_lightness(c, adj.lightness)[0]
            if adj.saturation_selected:
                c = adjust_saturation_in_range(c, adj.saturation, adj.hue_from, adj.hue_to)[0]
            else:
                c = adjust_saturation(c, adj.saturation)[0]
            return add_hue_delta_in_range(c, adj.hue_delta, adj.hue_from, adj.hue_to)[0]
        if ss < 0:
            return grey_in_range(c, adj.hue_from, adj.hue_to)[0]
        # colourize (hue, saturation as a byte): every grey pixel
        # = HLS(hue, its lightness, saturation)
        h, l, s = colour_hls(c)
        if s != 0:
            return c
        return with_hls(c, adj.hue_delta & 0xFFFF, l, ss & 0xFF)

    return fn


def lightness_ramp(c: int, steps: int = 256) -> list:
    """The Swap range bar: c's hue and saturation at every
    lightness from white (255) down to black (0), packed 0x00RRGGBB."""
    h, _l, s = colour_hls(c)
    out = []
    for k in range(steps):
        lum = 255 - (k * 255) // max(1, steps - 1)
        r, g, b = hls_to_rgb(h, lum, s)
        out.append((r << 16) | (g << 8) | b)
    return out


def hue_bar(frm_deg: int, to_deg: int) -> list:
    """The Adjust Colors hue bar: 360 segments, HLS(i, 128,
    0xeb) for the selected hues, HLS(i, 128, 0x40) -- the greyed excluded
    range -- for the rest. A wrapping range (frm > to) is drawn as the
    grey [to, frm] (both ends grey), as CtDraw draws it."""
    out = []
    for deg in range(360):
        if frm_deg <= to_deg:
            inside = frm_deg <= deg <= to_deg
        else:
            inside = not (to_deg <= deg <= frm_deg)
        r, g, b = hls_to_rgb(deg * 10, 0x80, 0xEB if inside else 0x40)
        out.append((r << 16) | (g << 8) | b)
    return out
