"""CtDraw's button faces."""

from __future__ import annotations

import functools
import sys

from PySide6 import QtCore, QtGui

T = None  # transparent pixel in a corner patch

# face gradient (rows 2 .. H-6, stretched), then three fixed bottom rows
_FACE_STOPS = [
    (0.000, 0xFFFFFF),
    (0.067, 0xFEFEFE),
    (0.133, 0xFCFCFB),
    (0.200, 0xFAFAF9),
    (0.267, 0xF8F8F6),
    (0.333, 0xF6F6F3),
    (0.400, 0xF6F6F2),
    (0.467, 0xF5F5F1),
    (0.533, 0xF4F4F0),
    (0.600, 0xF3F3EF),
    (0.667, 0xF3F3EE),
    (0.733, 0xF2F2ED),
    (0.800, 0xF1F1ED),
    (0.867, 0xF1F1EC),
    (0.933, 0xF0F0EB),
    (1.000, 0xF0F0EA),
]
_FACE_BOTTOM = (0xECEBE6, 0xE2DFD6, 0xD6D0C5)
_RIGHT_SHADE = ((-5, (-1, -1, -1)), (-4, (-5, -6, -8)), (-3, (-11, -14, -16)))
_BORDER = 0x003C74
_HALO_TOP, _HALO_BOTTOM, _HALO_LEFT, _HALO_RIGHT = 0xE0DBC9, 0xF7F6EF, 0xE2DDCB, 0xF0EDE0

# corner patches: (row, col) with negative indices counted from H / W
_TL = {
    (0, 0): T,
    (0, 1): 0xE3DFCD,
    (0, 2): 0xE1DCC9,
    (1, 0): 0xE3DFCD,
    (1, 1): 0xE6E1D0,
    (1, 2): 0x7995A7,
    (1, 3): 0x245683,
    (2, 0): 0xE2DDCB,
    (2, 1): 0x7995A7,
    (2, 2): 0x557DA2,
    (2, 3): 0xC0CFDD,
    (3, 0): 0xE2DDCB,
    (3, 1): 0x245683,
    (3, 2): 0xBFCEDC,
    (4, 0): 0xE1DCC9,
}
_TR = {
    (0, -4): 0xE1DCC9,
    (0, -3): 0xE2DDCB,
    (0, -2): 0xE3DFCD,
    (0, -1): T,
    (1, -4): 0x154C7D,
    (1, -3): 0x66879F,
    (1, -2): 0xE8E4D2,
    (1, -1): 0xE7E4D1,
    (2, -4): 0xD5DFE8,
    (2, -3): 0x557DA2,
    (2, -2): 0x7A95A8,
    (2, -1): 0xE9E6D4,
    (3, -3): 0xBFCEDB,
    (3, -2): 0x245683,
    (3, -1): 0xECEAD9,
    (4, -1): 0xEFECDE,
}
_BL = {
    (-4, 0): 0xE3DECC,
    (-4, 1): 0x154C7D,
    (-4, 2): 0xBFC7CA,
    (-3, 0): 0xE5E0CF,
    (-3, 1): 0x65869F,
    (-3, 2): 0x4A7193,
    (-3, 3): 0xA3AEB4,
    (-2, 0): 0xE8E5D4,
    (-2, 1): 0xECE9D8,
    (-2, 2): 0x7D98AA,
    (-2, 3): 0x245683,
    (-1, 0): T,
    (-1, 1): 0xEFEDDF,
    (-1, 2): 0xF3F1E7,
    (-1, 3): 0xF5F4EC,
}
_BR = {
    (-5, -5): 0xE9E8E1,
    (-5, -4): 0xE4E2DA,
    (-5, -3): 0xE0DDD4,
    (-5, -1): 0xF0EEE1,
    (-4, -6): 0xE1DED5,
    (-4, -5): 0xDFDCD2,
    (-4, -4): 0xDBD7CD,
    (-4, -3): 0xB6BDBD,
    (-4, -2): 0x154C7D,
    (-4, -1): 0xF2F0E5,
    (-3, -6): 0xD5D0C4,
    (-3, -5): 0xD5CFC3,
    (-3, -4): 0xB1B7B6,
    (-3, -3): 0x486E90,
    (-3, -2): 0x6A8AA2,
    (-3, -1): 0xF5F4EB,
    (-2, -4): 0x154C7D,
    (-2, -3): 0x6C8CA3,
    (-2, -2): 0xF4F3E9,
    (-2, -1): 0xF6F5ED,
    (-1, -3): 0xF8F7F2,
    (-1, -2): 0xF7F6F0,
    (-1, -1): T,
}


def _lerp(stops, t):
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(
                int(round(((c0 >> s) & 0xFF) + (((c1 >> s) & 0xFF) - ((c0 >> s) & 0xFF)) * f))
                for s in (16, 8, 0)
            )
    c = stops[-1][1]
    return ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)


@functools.lru_cache(maxsize=256)
def xp_button_image(W: int, H: int) -> QtGui.QImage:
    """The XP push-button bitmap, W x H pixels (= rect1 width + 1, height + 1)."""
    W, H = max(2, int(W)), max(2, int(H))
    img = QtGui.QImage(W, H, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    px = [[None] * W for _ in range(H)]

    def put(r, c, rgb):
        if 0 <= r < H and 0 <= c < W:
            px[r][c] = rgb

    if W < 8 or H < 8:  # tiny: border + flat face
        for r in range(H):
            for c in range(W):
                edge = r in (0, H - 1) or c in (0, W - 1)
                put(r, c, _BORDER if edge else 0xF4F4F0)
    else:
        grad_rows = max(1, H - 8)
        for r in range(2, H - 2):
            if r >= H - 5:
                base = _FACE_BOTTOM[r - (H - 5)]
                rgb = ((base >> 16) & 0xFF, (base >> 8) & 0xFF, base & 0xFF)
            else:
                rgb = _lerp(_FACE_STOPS, (r - 2) / float(grad_rows))
            for c in range(2, W - 2):
                put(r, c, (rgb[0] << 16) | (rgb[1] << 8) | rgb[2])
            if r < H - 5:
                for dc, (dr_, dg, db) in _RIGHT_SHADE:
                    c = W + dc
                    if c > 2:
                        put(
                            r,
                            c,
                            (max(0, rgb[0] + dr_) << 16) | (max(0, rgb[1] + dg) << 8) | max(0, rgb[2] + db),
                        )
        for c in range(1, W - 1):
            put(1, c, _BORDER)
            put(H - 2, c, _BORDER)
            put(0, c, _HALO_TOP)
            put(H - 1, c, _HALO_BOTTOM)
        for r in range(1, H - 1):
            put(r, 1, _BORDER)
            put(r, W - 2, _BORDER)
            put(r, 0, _HALO_LEFT)
            put(r, W - 1, _HALO_RIGHT)
        for patch in (_TL, _TR, _BL, _BR):
            for (r, c), rgb in patch.items():
                rr = r if r >= 0 else H + r
                cc = c if c >= 0 else W + c
                if 0 <= rr < H and 0 <= cc < W:
                    px[rr][cc] = rgb
    for r in range(H):
        row = px[r]
        for c in range(W):
            rgb = row[c]
            if rgb is not None:
                img.setPixel(c, r, 0xFF000000 | rgb)
    return img


# --------------------------------------------------------------- system colours
COLOR_WINDOWFRAME, COLOR_BTNFACE, COLOR_BTNSHADOW, COLOR_BTNTEXT = 6, 15, 16, 18
COLOR_3DHILIGHT, COLOR_3DDKSHADOW, COLOR_3DLIGHT = 20, 21, 22
_SYS_DEFAULTS = {
    COLOR_WINDOWFRAME: 0x646464,
    COLOR_BTNFACE: 0xF0F0F0,
    COLOR_BTNSHADOW: 0xA0A0A0,
    COLOR_BTNTEXT: 0x000000,
    COLOR_3DHILIGHT: 0xFFFFFF,
    COLOR_3DDKSHADOW: 0x696969,
    COLOR_3DLIGHT: 0xE3E3E3,
}


@functools.lru_cache(maxsize=None)
def sys_colour(idx: int) -> QtGui.QColor:
    """GetSysColor(idx) as CtDraw reads it (Windows 10 defaults elsewhere)."""
    rgb = _SYS_DEFAULTS.get(idx, 0)
    if sys.platform == "win32":
        try:
            import ctypes

            c = ctypes.windll.user32.GetSysColor(idx)
            rgb = ((c & 0xFF) << 16) | (c & 0xFF00) | ((c >> 16) & 0xFF)
        except Exception:  # noqa: BLE001
            pass
    return QtGui.QColor((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)


def frame_lines(l: int, t: int, r: int, b: int):
    """The GDI frame draws for BORDER / TARGET (rect r,b =
    rect1's +1): bottom, right, top, left -- one continuous path so a dash
    pattern runs on across the corners like GDI's."""
    path = QtGui.QPainterPath(QtCore.QPointF(l + 1.5, b + 0.5))
    path.lineTo(r + 0.5, b + 0.5)
    path.lineTo(r + 0.5, t + 0.5)
    path.lineTo(l + 0.5, t + 0.5)
    path.lineTo(l + 0.5, b + 0.5)
    return path


def raised_edges(l: int, t: int, r: int, b: int, soft: bool):
    """DrawEdge(EDGE_RAISED) as (path, colour) pairs."""
    out_tl = sys_colour(COLOR_3DHILIGHT if soft else COLOR_3DLIGHT)
    in_tl = sys_colour(COLOR_3DLIGHT if soft else COLOR_3DHILIGHT)

    def tl(i):
        p = QtGui.QPainterPath(QtCore.QPointF(l + i + 0.5, b - i - 0.5))
        p.lineTo(l + i + 0.5, t + i + 0.5)
        p.lineTo(r - i - 0.5, t + i + 0.5)
        return p

    def br(i):
        p = QtGui.QPainterPath(QtCore.QPointF(l + i + 0.5, b - i + 0.5))
        p.lineTo(r - i + 0.5, b - i + 0.5)
        p.lineTo(r - i + 0.5, t + i + 0.5)
        return p

    return [
        (tl(0), out_tl),
        (br(0), sys_colour(COLOR_3DDKSHADOW)),
        (tl(1), in_tl),
        (br(1), sys_colour(COLOR_BTNSHADOW)),
    ]


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("buttons.xp_images", xp_button_image, on=())
_caches.register("buttons.sys_colours", sys_colour, on=())
