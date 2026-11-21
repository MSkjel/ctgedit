"""Work out a text record's extent the way CtDraw does."""

from __future__ import annotations

import contextlib
import functools
import os
import re
import sys
import threading
from typing import Optional, Tuple

from .page import PageObject
from .textcodec import CODEC

# lfEscapement per orient = CtDraw's {0, 0, 1800, -900, 900}
# (orient 3 = 90 CW, 4 = 270 CW)
_ESCAPEMENT = {1: 0, 2: 1800, 3: -900, 4: 900}


class _Gdi:
    """Screen-DC GDI access (Windows); ``None`` elsewhere."""

    _inst = None

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ct = ctypes
        self.gdi = ctypes.WinDLL("gdi32")
        self.user = ctypes.WinDLL("user32")
        self.user.GetDC.restype = wintypes.HDC
        self.user.GetDC.argtypes = [wintypes.HWND]
        self.user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        g = self.gdi
        g.CreateFontW.restype = wintypes.HFONT
        g.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g.GetTextExtentPoint32A.argtypes = [wintypes.HDC, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
        g.GetTextMetricsW.argtypes = [wintypes.HDC, ctypes.c_void_p]
        g.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]

        class SIZE(ctypes.Structure):
            _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

        class TM(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_long)
                for n in (
                    "tmHeight",
                    "tmAscent",
                    "tmDescent",
                    "tmInternalLeading",
                    "tmExternalLeading",
                    "tmAveCharWidth",
                    "tmMaxCharWidth",
                    "tmWeight",
                    "tmOverhang",
                    "tmDigitizedAspectX",
                    "tmDigitizedAspectY",
                )
            ] + [
                ("tmFirstChar", wintypes.WCHAR),
                ("tmLastChar", wintypes.WCHAR),
                ("tmDefaultChar", wintypes.WCHAR),
                ("tmBreakChar", wintypes.WCHAR),
                ("tmItalic", ctypes.c_byte),
                ("tmUnderlined", ctypes.c_byte),
                ("tmStruckOut", ctypes.c_byte),
                ("tmPitchAndFamily", ctypes.c_byte),
                ("tmCharSet", ctypes.c_byte),
            ]

        self.SIZE, self.TM = SIZE, TM
        try:
            self._set_ctx = self.user.SetThreadDpiAwarenessContext
            self._set_ctx.restype = ctypes.c_void_p
            self._set_ctx.argtypes = [ctypes.c_void_p]
        except AttributeError:  # before Windows 10 1607: the process's own
            self._set_ctx = None
        forced = os.environ.get("CTG_TEXT_DPI", "").strip()
        with self.system_aware():
            dc = self.user.GetDC(None)
            self.measured_dpi = g.GetDeviceCaps(dc, 90)
            self.user.ReleaseDC(None, dc)
        self.logpixelsy = int(forced) if forced.isdigit() and int(forced) > 0 else self.measured_dpi

    @contextlib.contextmanager
    def system_aware(self):
        """Run GDI calls as a system-DPI-aware thread (CtDraw's context)."""
        prev = None
        if self._set_ctx is not None:
            # DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = (DPI_AWARENESS_CONTEXT)-2
            prev = self._set_ctx(self.ct.c_void_p(-2))
        try:
            yield
        finally:
            if prev:
                self._set_ctx(self.ct.c_void_p(prev))

    @classmethod
    def get(cls):
        if sys.platform != "win32":
            return None
        if cls._inst is None:
            try:
                cls._inst = cls()
            except Exception:  # noqa: BLE001
                return None
        return cls._inst


def _lines(raw: bytes):
    """The line walk: a CR, LF or CRLF ends a line; a newline at
    the very end starts no further line."""
    out, i, n = [], 0, len(raw)
    while True:
        j = i
        while j < n and raw[j] not in (0x0D, 0x0A):
            j += 1
        out.append(raw[i:j])
        if j >= n:
            break
        if raw[j] == 0x0D and j + 1 < n and raw[j + 1] == 0x0A:
            j += 1
        i = j + 1
        if i >= n:
            break
    return out


_tag_format = None


def use_tag_formats(lookup) -> None:
    """Install the tag-database lookup for numeric displays bound to a bare
    tag: ``lookup(name)`` -> the tag's FORMAT ('' = none), or None when the
    tag is unknown. The editing layer installs its project's (single-threaded
    UI: the document being edited)."""
    global _tag_format
    _tag_format = lookup


def installed_tag_format():
    """The tag-database lookup :func:`use_tag_formats` installed (None: none)."""
    return _tag_format


_INSTALLED = object()


def measured_string(
    po: PageObject, caption: Optional[str], tag_format=_INSTALLED, props: Optional[dict] = None
) -> Optional[str]:
    """The string a text displays, or None when it needs a tag lookup that isn't installed."""
    lookup = _tag_format if tag_format is _INSTALLED else tag_format
    if props is None:
        props = po.raw_properties()
    try:
        mode = int((props.get("TXTMODE") or "0").strip() or "0")
    except ValueError:
        mode = 0
    if mode in (0, 1, 2) and caption is None:
        return None  # the caption lives in a library twin
    if mode == 0:
        # an EMPTY caption measures the TXT_ON text (mode 0 looks at the
        # caption's first byte and falls back to the TXT_ON value)
        s = caption or props.get("TXT_ON", "")
    elif mode in (1, 2):
        s = caption
        if not s:
            prefix = "STATE_" if mode == 1 else "ARRAY_"
            for i in range(1, 32 if mode == 1 else 256):
                v = props.get("%s%d" % (prefix, i), "")
                if v and v != "#\n":  # unset state
                    s = v
                    break
    elif mode == 3:
        # numeric: the expression's display mask -- an explicit ':format'
        # tail, else '####.#'; a bare tag takes ITS format from the tag
        # database (no lookup installed -> None). A tail after a BARE name
        # ('FX_LEVEL2:#!', 'FxPlant.Tank1.Level:###') does not count: CtDraw
        # measures the name's FORMAT or '####.#' (case 3 takes the compiled
        # expression's format), and only '(expr):fmt' measures its tail
        expr = (props.get("TXT_STR") or "").strip()
        m = _FORMAT_TAIL.search(expr)
        if m and _IDENT.match(expr[: m.start()].strip()):
            expr = expr[: m.start()].strip()
            m = None
        if not expr:
            if caption is None:
                return None
            s = caption  # nothing to parse: the caption
        elif m:
            s = m.group(1)
        elif _IDENT.match(expr):
            # a bare tag: ITS format is the mask, '####.#' when it has none --
            # and '####.#' for a tag the project does not define, WHATEVER the
            # caption (CtDraw re-measures every text on load)
            if lookup is None:
                return None
            fmt = lookup(expr)
            s = fmt or "####.#"
        else:
            # an expression: the format of the first variable tag in it that has one,
            # else '####.#'
            fmt = _expr_tag_format(expr, lookup) if EXPR_TAG_FORMATS else None
            s = fmt or "####.#"
    elif mode == 4:
        # string display: the engine echoes '=' + the expression
        expr = props.get("TXT_STR") or ""
        if not expr and caption is None:
            return None
        s = ("=" + expr) if expr else caption
    else:
        return None
    return s or " "


_FORMAT_TAIL = re.compile(r":([^:\"'()]+)$")
_IDENT = re.compile(r"^[A-Za-z_?][\w?.\\]*$")
#: names in an expression: a tag may hold '\' (IEC 61850 names) and '.' (Equip.Item)
_NAME = re.compile(r"[A-Za-z_][\w.\\]*")
_STRING = re.compile(r'"(?:\^.|[^"])*"')
#: (A / B switch for the corpus check of the expression rule)
EXPR_TAG_FORMATS = True


def _expr_tag_format(expr: str, lookup) -> Optional[str]:
    """The FORMAT of the first variable tag named in ``expr`` (a function name -- a name
    followed by '(' -- and Cicode strings are passed over)."""
    if lookup is None:
        return None
    text = _STRING.sub('""', expr)
    for m in _NAME.finditer(text):
        rest = text[m.end() :].lstrip()
        if rest.startswith("("):
            continue
        try:
            fmt = lookup(m.group(0))
        except Exception:  # noqa: BLE001 -- a lookup failure: no format
            fmt = None
        if fmt:
            return fmt
    return None


def system_dpi() -> Optional[int]:
    """The LOGPIXELSY text extents are measured at: the system DPI a
    system-aware process (CtDraw) sees. None without GDI."""
    g = _Gdi.get()
    return None if g is None else int(g.logpixelsy)


def _font_folders() -> list:
    """The folders Windows installs fonts in: the system's and the user's own."""
    out = []
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or ""
    if windir:
        out.append(os.path.join(windir, "Fonts"))
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        out.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    return out


def measure_context() -> tuple:
    """What a text's measurement depends on besides its own record."""
    from . import textcodec

    fonts = []
    for p in _font_folders():
        try:
            fonts.append((os.path.normcase(p), os.stat(p).st_mtime_ns))
        except OSError:  # swallow-ok: no such folder (no user fonts): left out
            continue
    return (system_dpi() or 0, tuple(fonts), textcodec.CODEPAGE)


def dpi_report() -> dict:
    """Diagnostics: the DPI text extents use, what GDI reports for this
    process's own awareness, and that awareness (0 unaware, 1 system,
    2 per monitor)."""
    out = {"text_dpi": system_dpi(), "process_dpi": None, "awareness": None}
    g = _Gdi.get()
    if g is None:
        return out
    try:
        dc = g.user.GetDC(None)
        out["process_dpi"] = int(g.gdi.GetDeviceCaps(dc, 90))
        g.user.ReleaseDC(None, dc)
        u = g.user
        u.GetThreadDpiAwarenessContext.restype = g.ct.c_void_p
        u.GetAwarenessFromDpiAwarenessContext.argtypes = [g.ct.c_void_p]
        out["awareness"] = int(u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext()))
    except Exception:  # noqa: BLE001 -- diagnostics only
        pass
    return out


#: (text, face, points, attribs, orient, charset, dpi) -> the box laid out from (0, 0), or
#: None for no box. The box moves with the anchor, so one GDI layout serves every place a
#: text is measured at. Emptied when it grows past _EXTENTS_MAX.
_EXTENTS: dict = {}
_EXTENTS_MAX = 50000
_MISS = object()


def extent(
    text: str, face: str, points: int, attribs: int, orient: int, charset: int, anchor: Tuple[int, int]
) -> Optional[Tuple[int, int, int, int]]:
    """The union box of ``text`` laid out from the baseline ``anchor``."""
    g = _Gdi.get()
    if g is None:
        return None
    key = (text, face, points, attribs, orient, charset, g.logpixelsy)
    box = _EXTENTS.get(key, _MISS)
    if box is _MISS:
        with g.system_aware():
            box = _extent(g, text, face, points, attribs, orient, charset, (0, 0))
        if len(_EXTENTS) >= _EXTENTS_MAX:
            _EXTENTS.clear()
        _EXTENTS[key] = box
    if box is None:
        return (0, 0, 0, 0)
    x, y = anchor
    return (box[0] + x, box[1] + y, box[2] + x, box[3] + y)


#: per thread: the GDI fonts _extent measures with, by their CreateFontW arguments (GDI
#: objects are not shared across threads); all deleted when it reaches _FONTS_MAX
_FONTS = threading.local()
_FONTS_MAX = 64


def _font(g, height: int, esc: int, attribs: int, charset: int, face: str):
    fonts = getattr(_FONTS, "by_key", None)
    if fonts is None:
        fonts = _FONTS.by_key = {}
    key = (height, esc, attribs & 0xF0, charset & 0xFF, face[:31])
    f = fonts.get(key)
    if f is None:
        if len(fonts) >= _FONTS_MAX:
            for old in fonts.values():
                g.gdi.DeleteObject(old)
            fonts.clear()
        f = fonts[key] = g.gdi.CreateFontW(
            -height,
            0,
            esc,
            esc,
            700 if attribs & 0x10 else 400,
            1 if attribs & 0x20 else 0,
            1 if attribs & 0x40 else 0,
            1 if attribs & 0x80 else 0,
            charset & 0xFF,
            0,
            0,
            0,
            0,
            face[:31],
        )
    return f


def _extent(g, text, face, points, attribs, orient, charset, anchor):
    """The box of :func:`extent` from ``anchor``; None for no box (no height, or no line
    with characters), which :func:`extent` reports as (0, 0, 0, 0) at any anchor."""
    height = (g.logpixelsy * points) // 72
    if height <= 0:
        return None
    orient = orient if orient in _ESCAPEMENT else 1
    esc = _ESCAPEMENT[orient]
    f = _font(g, height, esc, attribs, charset, face)
    dc = g.user.GetDC(None)
    old = g.gdi.SelectObject(dc, f)
    try:
        tm = g.TM()
        g.gdi.GetTextMetricsW(dc, g.ct.byref(tm))
        asc, lh = tm.tmAscent, tm.tmHeight
        just = attribs & 0xF
        x, y = anchor
        box = None
        for line in _lines(text.encode(CODEC, "replace")):
            if line:
                sz = g.SIZE()
                g.gdi.GetTextExtentPoint32A(dc, line, len(line), g.ct.byref(sz))
                cx, cy = sz.cx, sz.cy
                if orient == 1:
                    if just == 1:
                        r = (x - cx, y - asc, x, y - asc + cy)
                    elif just == 2:
                        l0 = x - cx // 2
                        r = (l0, y - asc, l0 + cx, y - asc + cy)
                    else:
                        r = (x, y - asc, x + cx, y - asc + cy)
                elif orient == 2:
                    if just == 1:
                        r = (x, y + asc - cy, x + cx, y + asc)
                    elif just == 2:
                        rr = x + int(cx / 2)
                        r = (rr - cx, y + asc - cy, rr, y + asc)
                    else:
                        r = (x - cx, y + asc - cy, x, y + asc)
                elif orient == 3:
                    if just == 1:
                        r = (x + asc - cy, y - cx, x + asc, y)
                    elif just == 2:
                        t0 = y - cx // 2
                        r = (x + asc - cy, t0, x + asc, t0 + cx)
                    else:
                        r = (x + asc - cy, y, x + asc, y + cx)
                else:
                    if just == 1:
                        r = (x - asc, y, x - asc + cy, y + cx)
                    elif just == 2:
                        b0 = y + int(cx / 2)
                        r = (x - asc, b0 - cx, x - asc + cy, b0)
                    else:
                        r = (x - asc, y - cx, x - asc + cy, y)
                box = (
                    r
                    if box is None
                    else (min(box[0], r[0]), min(box[1], r[1]), max(box[2], r[2]), max(box[3], r[3]))
                )
            x += (lh if orient == 4 else 0) - (lh if orient == 3 else 0)
            y += (lh if orient == 1 else 0) - (lh if orient == 2 else 0)
        return box
    finally:
        g.gdi.SelectObject(dc, old)
        g.user.ReleaseDC(None, dc)


def caption_metrics(lines, face: str, points: int, attribs: int, charset: int = 0):
    """Measure a button caption's lines with the button font."""
    g = _Gdi.get()
    if g is None:
        return None
    with g.system_aware():
        return _caption_metrics(g, lines, face, points, attribs, charset)


def _caption_metrics(g, lines, face, points, attribs, charset):
    height = (g.logpixelsy * points) // 72
    if height <= 0:
        return None
    f = g.gdi.CreateFontW(
        -height,
        0,
        0,
        0,
        700 if attribs & 0x10 else 400,
        1 if attribs & 0x20 else 0,
        1 if attribs & 0x40 else 0,
        1 if attribs & 0x80 else 0,
        charset & 0xFF,
        0,
        0,
        0,
        0,
        (face or "Arial")[:31],
    )
    dc = g.user.GetDC(None)
    old = g.gdi.SelectObject(dc, f)
    try:
        tm = g.TM()
        g.gdi.GetTextMetricsW(dc, g.ct.byref(tm))
        widths, cy = [], tm.tmHeight
        for line in lines:
            raw = (line or " ").encode(CODEC, "replace")
            sz = g.SIZE()
            g.gdi.GetTextExtentPoint32A(dc, raw, len(raw), g.ct.byref(sz))
            widths.append(sz.cx)
            cy = sz.cy
        return widths, cy, tm.tmAscent
    finally:
        g.gdi.SelectObject(dc, old)
        g.gdi.DeleteObject(f)
        g.user.ReleaseDC(None, dc)


def text_extent(po: PageObject) -> Optional[Tuple[int, int, int, int]]:
    """The engine's rect1 for a free text record, or None when it cannot be
    derived here (no style block / tail, a value display, no GDI)."""
    from . import geometry as G

    if po.type_code != 0x05 or not (po.subtype_size & 0x2):
        return None
    tail = G.parse_text_tail(po)
    cb = G.find_colour_block(po)
    if tail is None or cb is None:
        return None
    s = measured_string(po, tail["text"])
    if s is None:
        return None
    box = extent(
        s,
        tail["face"],
        tail["size"],
        cb.flags[G.FLAG_ATTRIBS],
        tail["orient"],
        tail["charset"],
        tuple(tail["echo"]),
    )
    m6 = G.text_total(po)
    if box is None or m6 is None:
        return box
    # a mirrored text is measured in its local frame (the echo) and mapped
    # through its Total, truncated toward zero
    a, b, c, d, h, k = m6
    xs, ys = [], []
    for x, y in ((box[0], box[1]), (box[2], box[3])):
        xs.append(a * x + c * y + h)
        ys.append(b * x + d * y + k)

    def cut(v):
        n = round(v)
        return int(n) if abs(v - n) < 1e-9 else int(v)

    return (cut(min(xs)), cut(min(ys)), cut(max(xs)), cut(max(ys)))


#: (font spec, size, dpi, strings) -> named_font_lines' answer; and (face,) -> the LOGFONT
#: EnumFontsA gives for a face (None: not installed). Emptied past _EXTENTS_MAX.
_NAMED: dict = {}
#: GetStockObject(ANSI_FIXED_FONT): the font of a named font no fonts table defines
_ANSI_FIXED_FONT = 11


def named_font_lines(font: Optional[Tuple[str, int]], strings) -> Optional[tuple]:
    """Measure strings in a project's named font."""
    g = _Gdi.get()
    if g is None:
        return None
    strings = tuple(strings)
    key = (None if font is None else (str(font[0]), int(font[1])), g.logpixelsy, strings)
    got = _NAMED.get(key, _MISS)
    if got is _MISS:
        with g.system_aware():
            got = _named_font_lines(g, key[0], strings)
        if len(_NAMED) >= _EXTENTS_MAX:
            _NAMED.clear()
        _NAMED[key] = got
    return got


def _logfont(g, spec: str, size: int):
    """The LOGFONTA for fonts.dbf TYPE spec and SIZE size."""
    ct = g.ct
    from ctypes import wintypes

    class LOGFONTA(ct.Structure):
        _fields_ = [
            ("lfHeight", ct.c_long),
            ("lfWidth", ct.c_long),
            ("lfEscapement", ct.c_long),
            ("lfOrientation", ct.c_long),
            ("lfWeight", ct.c_long),
            ("lfItalic", ct.c_byte),
            ("lfUnderline", ct.c_byte),
            ("lfStrikeOut", ct.c_byte),
            ("lfCharSet", ct.c_byte),
            ("lfOutPrecision", ct.c_byte),
            ("lfClipPrecision", ct.c_byte),
            ("lfQuality", ct.c_byte),
            ("lfPitchAndFamily", ct.c_byte),
            ("lfFaceName", ct.c_char * 32),
        ]

    parts = spec.split(",")
    face = parts[0].encode(CODEC, "replace")[:31]
    fkey = ("face", face)
    raw = _NAMED.get(fkey, _MISS)
    if raw is _MISS:
        found = []
        proc_t = ct.WINFUNCTYPE(ct.c_int, ct.POINTER(LOGFONTA), ct.c_void_p, wintypes.DWORD, wintypes.LPARAM)

        def cb(lf, _tm, _ft, _lp):
            found.append(bytes(ct.string_at(lf, ct.sizeof(LOGFONTA))))
            return 0  # the first face is the one (EnumFontsA's order)

        proc = proc_t(cb)
        g.gdi.EnumFontsA.argtypes = [wintypes.HDC, ct.c_char_p, ct.c_void_p, wintypes.LPARAM]
        dc = g.user.GetDC(None)
        try:
            g.gdi.EnumFontsA(dc, face, proc, 0)
        finally:
            g.user.ReleaseDC(None, dc)
        raw = found[0] if found else None
        _NAMED[fkey] = raw
    lf = LOGFONTA.from_buffer_copy(raw) if raw is not None else LOGFONTA()
    lf.lfFaceName = face
    # the character set and output precision are CtDraw's, never the face's own (the
    # character set: the code page's, ANSI_CHARSET 0 on cp1252; the output precision:
    # 0 without a foreign language): a
    # symbol face such as Wingdings, enumerated with SYMBOL_CHARSET, maps to a text face
    from .geometry import DEFAULT_CHARSET

    lf.lfCharSet = DEFAULT_CHARSET & 0xFF
    lf.lfOutPrecision = 0
    # (C's division truncates toward zero: -10 points at 96 dpi is -13, not -14)
    lf.lfHeight = -size if size >= 1 else -((g.logpixelsy * -size) // 72)
    lf.lfWidth = 0
    for tok in " ".join(parts[1:]).replace(",", " ").split():
        c = tok[:1].upper()
        if c == "B":
            lf.lfWeight = 700
        elif c == "I":
            lf.lfItalic = 1
        elif c == "S":
            lf.lfStrikeOut = 1
        elif c == "U":
            lf.lfUnderline = 1
    return lf


def _named_font_lines(g, font, strings):
    ct = g.ct
    from ctypes import wintypes

    made = None
    if font is None:
        g.gdi.GetStockObject.restype = wintypes.HGDIOBJ
        h = g.gdi.GetStockObject(_ANSI_FIXED_FONT)
    else:
        lf = _logfont(g, font[0], font[1])
        g.gdi.CreateFontIndirectA.restype = wintypes.HFONT
        g.gdi.CreateFontIndirectA.argtypes = [ct.c_void_p]
        h = made = g.gdi.CreateFontIndirectA(ct.byref(lf))
    dc = g.user.GetDC(None)
    old = g.gdi.SelectObject(dc, h)
    try:
        tm = g.TM()
        g.gdi.GetTextMetricsW(dc, ct.byref(tm))
        out = []
        for s in strings:
            raw = s.encode(CODEC, "replace")
            sz = g.SIZE()
            g.gdi.GetTextExtentPoint32A(dc, raw, len(raw), ct.byref(sz))
            out.append((int(sz.cx), int(sz.cy)))
        return int(tm.tmAscent), int(tm.tmDescent), out
    finally:
        g.gdi.SelectObject(dc, old)
        if made:
            g.gdi.DeleteObject(made)
        g.user.ReleaseDC(None, dc)


@functools.lru_cache(maxsize=1024)
def font_metrics(face: str, points: int, attribs: int = 0, charset: int = 0) -> Optional[Tuple[int, int]]:
    """GDI (ascent, line height) for a CtDraw font."""
    m = caption_metrics([" "], face, points, attribs, charset)
    return None if m is None else (int(m[2]), int(m[1]))


from ..infra import caches as _caches  # noqa: E402

_caches.register("textmetrics.font_metrics", font_metrics, on=())
_caches.register("textmetrics.extents", _EXTENTS, on=())
_caches.register("textmetrics.named_fonts", _NAMED, on=())
