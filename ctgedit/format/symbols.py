"""Read symbol, genie and template libraries and resolve placed symbols."""

from __future__ import annotations

from ..infra.paths import path_key

import os
import re
import struct
import threading as _threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import fileio as _fileio
from .cnum import is_digits
from .container import Container
from .page import PageBlock, PageObject, parse_page
from .textcodec import CODEC, MULTIBYTE
from .zstream import inflate, inflate_some

# swallowed errors of the read path go to the activity log (rate-limited; control flow unchanged)
from ..infra.logsetup import note_exception as _note_exc, note_problem as _note_problem

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# single-letter library extension -> (file extension, semantic kind)
_EXT_KIND = {
    "l": ("ctl", "symbol"),
    "m": ("ctm", "genie"),
    "t": ("ctt", "template"),
}

# library file extensions we will open (lower-case, no dot)
LIBRARY_EXTENSIONS = ("ctl", "ctm", "ctt")

# The body marker that frames a placed reference: ".<ct" + one ASCII ext letter.
_BODY_REF_RE = re.compile(rb"\.<ct([a-zA-Z])")


# ---------------------------------------------------------------------------
# Reference
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolReference:
    """A placed symbol reference: library and element."""

    library: str
    element: str
    ext: str
    kind: str
    raw: str = ""

    @property
    def filename(self) -> str:
        """Default library file name (``<library>.<ext>``)."""
        return "%s.%s" % (self.library, self.ext)

    def __str__(self) -> str:  # compact id (cache key / label)
        return "%s.%s (%s)" % (self.library, self.element, self.ext)


def _split_lib_element(core: str) -> Tuple[str, str]:
    """Split <library>.<element> into (library, element) as CtDraw does:
    from the RIGHT (strrchr), so the element is the last dotted part and the library
    everything before it."""
    lib, _, element = core.rpartition(".")
    if not lib:
        return core, ""
    return lib, element


#: ``<library>.<element>`` split as CtDraw does (the last dot): the one rule every reader of
#: a link uses (the import tables, the usage index, the stamp checks)
split_lib_element = _split_lib_element


def reference_from_body(body: bytes) -> Optional[SymbolReference]:
    """The placed symbol reference in an object's body, or None."""
    for m in _BODY_REF_RE.finditer(body):
        ext_letter = chr(body[m.start() + 4]).lower()
        if ext_letter not in _EXT_KIND:
            continue
        end = m.end()  # one past the ext letter
        # walk back to the u16 length prefix that frames [start, end) as a
        # run of text bytes (cp1252 letters included: 'symbol.32_list_café')
        start = m.start()
        lo = max(0, m.start() - 256)
        found = None
        while start >= lo:
            if start - 2 >= 0:
                (ln,) = struct.unpack_from("<H", body, start - 2)
                if ln == end - start and _all_printable(body, start, end):
                    found = start
                    break
            start -= 1
        if found is None:
            # fall back to the longest text run ending at the marker
            s = m.start()
            while s > 0 and _IS_TEXT[body[s - 1]]:
                s -= 1
            found = s
        text = body[found:end].decode(CODEC, "replace")
        return _parse_ref_text(text, ext_letter)
    return None


def body_reference_span(body: bytes) -> Optional[Tuple[int, int]]:
    """Byte span of the body's reference string, or None."""
    for m in _BODY_REF_RE.finditer(body):
        ext_letter = chr(body[m.start() + 4]).lower()
        if ext_letter not in _EXT_KIND:
            continue
        end = m.end()
        start = m.start()
        lo = max(0, m.start() - 256)
        while start >= lo:
            if start - 2 >= 0:
                (ln,) = struct.unpack_from("<H", body, start - 2)
                if ln == end - start and _all_printable(body, start, end):
                    return (start - 2, end)
            start -= 1
        return None
    return None


def reference_from_symbol_value(value: str) -> Optional[SymbolReference]:
    """Parse a ``SYMBOL`` bag value (``<library>.<element>``, no extension; backing
    library is always ``.ctl``) -> a symbol ref, or ``None`` for an empty /
    dot-less value."""
    if not value or "." not in value:
        return None
    lib, element = _split_lib_element(value)
    if not lib or not element:
        return None
    return SymbolReference(library=lib, element=element, ext="ctl", kind="symbol", raw=value)


def _parse_ref_text(text: str, ext_letter: str) -> Optional[SymbolReference]:
    """Turn a raw ``<lib>.<element>.<ct<ext>`` string into a SymbolReference."""
    marker = ".<ct" + ext_letter
    idx = text.find(marker)
    core = text[:idx] if idx >= 0 else text
    lib, element = _split_lib_element(core)
    if not lib or not element:
        return None
    file_ext, kind = _EXT_KIND.get(ext_letter, ("ctl", "symbol"))
    return SymbolReference(library=lib, element=element, ext=file_ext, kind=kind, raw=text)


#: reference_from_object's per-record memo of the body's reference: (body_raw, the
#: reference or None), valid while the record holds that same immutable body object (the
#: reference is a function of the body alone)
BODY_REF_MEMO_ATTR = "_body_ref_memo"


def _body_reference(po: PageObject) -> Optional[SymbolReference]:
    """reference_from_body(po.body_raw), memoised on the record (BODY_REF_MEMO_ATTR)."""
    body = po.body_raw
    d = getattr(po, "__dict__", None) if type(body) is bytes else None
    if d is None:
        return reference_from_body(body)
    m = d.get(BODY_REF_MEMO_ATTR)
    if m is not None and m[0] is body:
        return m[1]
    ref = reference_from_body(body)
    d[BODY_REF_MEMO_ATTR] = (body, ref)
    return ref


def reference_from_object(po: PageObject) -> Optional[SymbolReference]:
    """Best-effort placed symbol reference for a PageObject."""
    ref = _body_reference(po) if (po.subtype_size & 0x1) else None
    if ref is not None:
        return ref
    try:
        raw = po.raw_properties()
    except Exception:  # noqa: BLE001
        _note_exc("symbols.reference_from_object")
        raw = {}
    sym = raw.get("SYMBOL") or legacy_first_symbol(po.type_code, raw)
    if sym:
        return reference_from_symbol_value(sym)
    return None


def legacy_first_symbol(type_code: int, raw: dict) -> str:
    """The state a V3/V4 symbol record shows in the editor: a Symbol (0x1007) its first
    SYMBnnn (the all-off state, SYMB000), an Animated Symbol (0x100D) its OFF_SYMBOL (else
    SYMBOL_1). '' for any other record."""
    if type_code == 0x1007:
        keys = sorted(k for k in raw if k.upper().startswith("SYMB") and is_digits(k[4:]))
        return str(raw.get(keys[0]) or "") if keys else ""
    if type_code == 0x100D:
        return str(raw.get("OFF_SYMBOL") or raw.get("SYMBOL_1") or "")
    return ""


def _non_text_bytes() -> bytes:
    """Bytes that can't be text in a stored reference."""
    out = bytearray(range(0x20)) + b"\x7f"
    if not MULTIBYTE:
        for i in range(0x80, 0x100):
            ch = bytes([i]).decode(CODEC, "replace")
            if 0x80 <= ord(ch) <= 0x9F or 0xE000 <= ord(ch) <= 0xF8FF or ch == "\ufffd":
                out.append(i)
    return bytes(out)


_NON_TEXT = _non_text_bytes()
#: byte -> is a reference text byte (the fallback walk)
_IS_TEXT = tuple(i not in _NON_TEXT for i in range(256))


def _all_printable(b: bytes, a: int, z: int) -> bool:
    """``b[a:z]`` holds only text bytes (:func:`_non_text_bytes`)."""
    seg = b[a : min(z, len(b))]
    return len(seg.translate(None, _NON_TEXT)) == len(seg)


# ---------------------------------------------------------------------------
# Embedded bitmap (the pixel art of a tc=7 "Bitmap" object)
# ---------------------------------------------------------------------------
#
# A tc=7 Bitmap that is NOT a library reference (no ``.<ct?`` marker) carries its
# picture inline as a zlib-compressed ``BITMAPINFOHEADER`` (40 bytes, biSize==40)
# followed by 32-bit pixel rows = B,G,R + 1-byte AND-mask (negative biHeight ->
# top-down): a 40-byte header + w*h*4 bytes, deflate magic ``78 5e`` (a few
# ``78 01``); the dimensions match the element extent.
#
# The 4th byte per pixel is a Windows AND-mask (0x00 OPAQUE / 0xFF TRANSPARENT),
# NOT a straight alpha; the decoder inverts it. An all-zero mask is fully opaque
# (correct for solid rectangular icons like power_meter).

# zlib stream headers, priority order (CMF/FLG combinations Citect emits)
_ZLIB_MAGICS = (b"\x78\x5e", b"\x78\x9c", b"\x78\x01", b"\x78\xda")

_BMP_INFO_HEADER = 40  # BITMAPINFOHEADER size


@dataclass(frozen=True)
class EmbeddedBitmap:
    """A decoded inline picture: top-down RGBA pixels."""

    width: int
    height: int
    rgba: bytes
    has_alpha: bool


def _inflate_first(body: bytes, start: int) -> Optional[bytes]:
    """Inflate the first zlib stream found at/after ``start``. Never raises."""
    n = len(body)
    pos = start
    while pos < n:
        idx = -1
        for magic in _ZLIB_MAGICS:
            j = body.find(magic, pos)
            if j >= 0 and (idx < 0 or j < idx):
                idx = j
        if idx < 0:
            return None
        chunk = bytes(body[idx:])
        try:
            return inflate(chunk)
        except Exception:  # noqa: BLE001 -- partial / truncated streams happen
            _note_exc("symbols._inflate_first")
            try:
                out = inflate_some(chunk)
                if out:
                    return out
            except Exception:  # noqa: BLE001
                _note_exc("symbols._inflate_first")
                pass
        pos = idx + 2  # false-positive header; keep scanning
    return None


def find_stream(body: bytes, start: int):
    """Locate + inflate the first zlib stream at/after ``start``.
    Returns ``(offset, inflated)`` or ``None``. Never raises."""
    n = len(body)
    pos = start
    while pos < n:
        idx = -1
        for magic in _ZLIB_MAGICS:
            j = body.find(magic, pos)
            if j >= 0 and (idx < 0 or j < idx):
                idx = j
        if idx < 0:
            return None
        chunk = bytes(body[idx:])
        try:
            return idx, inflate(chunk)
        except Exception:  # swallow-ok: a zlib header found by its two bytes may be no stream
            try:
                out = inflate_some(chunk)
                if out:
                    return idx, out
            except Exception:  # swallow-ok: (the same: try the next candidate)
                pass
        pos = idx + 2
    return None


# The decoded pictures of a record body: the same DIBs are decoded again on every page open /
# derive (library element records, template copies). A pure function of (body bytes, start), so
# the results are memoised keyed by the body BYTES themselves (exact: dict equality) -- an
# EmbeddedBitmap is frozen and its pixels are immutable bytes, so sharing one is safe. Bounded by
# bytes (key body + pixels); read_page also runs on the page browser's preview threads -> locked.
_BMP_MEMO: "OrderedDict[tuple, tuple]" = OrderedDict()
_BMP_MEMO_BYTES = [0]
_BMP_MEMO_LIMIT = 64 * 1024 * 1024
_BMP_MEMO_LOCK = _threading.Lock()


def _memo_pictures(kind: str, body, start: int, compute):
    if type(body) is not bytes or type(start) is not int:
        return compute()
    key = (kind, start, body)
    with _BMP_MEMO_LOCK:
        hit = _BMP_MEMO.get(key)
        if hit is not None:
            _BMP_MEMO.move_to_end(key)
            return hit[0]
    val = compute()
    pics = val if isinstance(val, tuple) else (val,)
    size = len(body) + sum(len(p.rgba) for p in pics if p is not None)
    with _BMP_MEMO_LOCK:
        hit = _BMP_MEMO.get(key)
        if hit is not None:
            return hit[0]
        _BMP_MEMO[key] = (val, size)
        _BMP_MEMO_BYTES[0] += size
        while _BMP_MEMO_BYTES[0] > _BMP_MEMO_LIMIT and len(_BMP_MEMO) > 1:
            _k, (_v, s) = _BMP_MEMO.popitem(last=False)
            _BMP_MEMO_BYTES[0] -= s
    return val


def clear_picture_memo() -> None:
    with _BMP_MEMO_LOCK:
        _BMP_MEMO.clear()
        _BMP_MEMO_BYTES[0] = 0


def embedded_bitmaps_from_body(body: bytes, body_start: int = 0):
    """Decode a bitmap object's pictures: ``(primary, flash or None)``."""
    return _memo_pictures("pair", body, body_start, lambda: _embedded_bitmaps(body, body_start))


def _embedded_bitmaps(body: bytes, body_start: int):
    hit = find_stream(body, body_start)
    if hit is None:
        return None, None
    off, raw = hit
    primary = decode_dib(raw)
    if primary is None:
        return None, None
    flash = None
    try:
        if off >= 12:
            flags, _uncomp, comp = struct.unpack_from("<III", body, off - 12)
            if (flags & 0xF00) == 0x100 and off + comp + 4 <= len(body):
                (comp2,) = struct.unpack_from("<I", body, off + comp)
                blob = body[off + comp + 4 : off + comp + 4 + comp2]
                flash = decode_dib(inflate(bytes(blob)))
    except Exception:  # noqa: BLE001 -- a malformed flash half never breaks the primary
        _note_exc("symbols._embedded_bitmaps")
        flash = None
    return primary, flash


def embedded_bitmap_from_body(body: bytes, body_start: int = 0) -> Optional[EmbeddedBitmap]:
    """Decode a bitmap object's picture, or None."""
    return _memo_pictures("one", body, body_start, lambda: _embedded_bitmap(body, body_start))


def _embedded_bitmap(body: bytes, body_start: int) -> Optional[EmbeddedBitmap]:
    raw = _inflate_first(body, body_start)
    if raw is None:
        return None
    return decode_dib(raw)


#: 0xFF - b for every byte value b
_INVERT_BYTES = bytes(range(255, -1, -1))


def decode_dib(raw: bytes) -> Optional[EmbeddedBitmap]:
    """Decode one inflated BIH(40)+32bpp+AND-mask DIB into an EmbeddedBitmap."""
    if raw is None or len(raw) < _BMP_INFO_HEADER:
        return None
    try:
        bi_size, w, h, planes, bpp = struct.unpack_from("<IiiHH", raw, 0)
    except struct.error:
        return None
    if bi_size != _BMP_INFO_HEADER or bpp != 32:
        return None
    if w <= 0 or w > 8192 or h == 0 or abs(h) > 8192:
        return None
    width = w
    height = abs(h)
    top_down = h < 0
    npix = width * height
    px_off = _BMP_INFO_HEADER
    need = npix * 4
    if px_off + need > len(raw):
        return None
    src = bytearray(raw[px_off : px_off + need])

    # Source rows: BGR + a Windows AND-mask byte (0x00 OPAQUE / 0xFF TRANSPARENT),
    # NOT a straight alpha: colour-bearing pixels are exactly mask==0. Render alpha =
    # inverse of the stored byte.
    #
    # Positive biHeight = bottom-up, negative (common Citect case) = top-down.
    # Normalise to a top-down RGBA buffer.
    row_bytes = width * 4

    mask = src[3::4]
    has_alpha = mask.count(0) != len(mask)  # any(mask), at C speed

    # BGRA -> RGBA: swap B and R in place
    src[0::4], src[2::4] = src[2::4], src[0::4]
    # invert AND-mask into straight alpha: opaque(0)->0xFF, clear(0xFF)->0x00
    # (0xFF - b per byte = a translate table)
    src[3::4] = mask.translate(_INVERT_BYTES)

    if top_down:
        out = bytes(src)
    else:
        out = b"".join(bytes(src[y * row_bytes : (y + 1) * row_bytes]) for y in range(height - 1, -1, -1))
    return EmbeddedBitmap(width=width, height=height, rgba=out, has_alpha=has_alpha)


# --------------------------------------------------------------------------- metafiles
# A tc=0x09 "MetaFile" record (catalog primitive[16]) carries a placed, UNCOMPRESSED memory
# Windows Metafile in its class section ([native rect][METAHEADER + records], mtSize words --
# bitmaps.metafile_bits). We render it to an RGBA
# picture on Windows exactly as CtDraw draws it (on screen, in print and in the .ctF
# bake): SetMapMode(MM_ANISOTROPIC), viewport origin = the object's EXTENT top-left,
# viewport extent = (w - 1, h - 1), SetMetaFileBitsEx + PlayMetaFile -- the metafile's own
# SetWindowOrg / SetWindowExt map its WHOLE window onto the extent, margins included.
# CtDraw plays straight into the page DC, so what the metafile PAINTS is opaque -- its
# white fills too -- and what it leaves alone shows the page: played twice, on white
# and on black, a pixel is transparent only where both backgrounds survive. CtDraw neither clips
# nor world-transforms a metafile (no rotation); a metafile whose ink falls outside its window draws
# nothing here. Returns None off-Windows or on any failure -- callers draw no art.
_WMF_MEMORY_HDR = bytes.fromhex("01000900")  # mtType=1 (memory), mtHeaderSize=9 words
_WMF_DISK_HDR = bytes.fromhex("02000900")  # mtType=2 (disk)
_META_SETWINDOWEXT = 0x020C
METAFILE_MAX_DIM = 4096  # a page extent is <= 32767


def wmf_window_extent(wmf: bytes) -> Optional[Tuple[int, int]]:
    """The (x, y) extent of the metafile's first SetWindowExt record, or None (the parameters are
    stored y first). Without one the MM_ANISOTROPIC window keeps GDI's default 1 x 1."""
    if len(wmf) < 18:
        return None
    o = struct.unpack_from("<H", wmf, 2)[0] * 2
    while o + 6 <= len(wmf):
        size, fn = struct.unpack_from("<IH", wmf, o)
        if size < 3 or fn == 0:
            return None
        if fn == _META_SETWINDOWEXT and o + 10 <= len(wmf):
            ey, ex = struct.unpack_from("<2h", wmf, o + 6)
            return int(ex), int(ey)
        o += size * 2
    return None


def render_metafile(
    body: bytes,
    body_start: int = 0,
    target_w: int = 0,
    target_h: int = 0,
    max_dim: int = METAFILE_MAX_DIM,
    wmf: Optional[bytes] = None,
) -> Optional[EmbeddedBitmap]:
    """Render a placed Windows metafile to an EmbeddedBitmap."""
    if wmf is None:
        off = body.find(_WMF_MEMORY_HDR, max(0, body_start))
        if off < 0:
            off = body.find(_WMF_DISK_HDR, max(0, body_start))
        if off < 0 or off + 18 > len(body):
            return None
        size_words = struct.unpack_from("<I", body, off + 6)[0]
        end = off + size_words * 2
        if size_words <= 0 or end > len(body):
            return None  # CtDraw's stream read fails there too
        wmf = bytes(body[off:end])
    if len(wmf) < 18:
        return None
    try:
        return _render_wmf_cached(bytes(wmf), int(target_w), int(target_h), int(max_dim))
    except Exception:  # noqa: BLE001 -- never crash a render on a bad metafile
        _note_exc("symbols.render_metafile")
        return None


# the pictures are large: bound the cache by BYTES, not count.
# read_page also runs on the page browser's preview worker threads: every cache access holds the
# lock, the GDI play runs outside it (two threads may render the same key; the first insert wins)
_WMF_CACHE: Dict[tuple, Optional[EmbeddedBitmap]] = {}
_WMF_CACHE_BYTES = [0]
_WMF_CACHE_LIMIT = 160 * 1024 * 1024
_WMF_LOCK = _threading.Lock()


def _render_wmf_cached(wmf: bytes, target_w: int, target_h: int, max_dim: int) -> Optional[EmbeddedBitmap]:
    import hashlib

    key = (hashlib.sha1(wmf).digest(), len(wmf), target_w, target_h, max_dim)
    with _WMF_LOCK:
        if key in _WMF_CACHE:
            eb = _WMF_CACHE.pop(key)
            _WMF_CACHE[key] = eb  # most recently used last
            return eb
    eb = _render_wmf(wmf, target_w, target_h, max_dim)
    with _WMF_LOCK:
        if key in _WMF_CACHE:  # another thread rendered it meanwhile
            return _WMF_CACHE[key]
        _WMF_CACHE[key] = eb
        _WMF_CACHE_BYTES[0] += len(eb.rgba) if eb is not None else 0
        while _WMF_CACHE_BYTES[0] > _WMF_CACHE_LIMIT and len(_WMF_CACHE) > 1:
            ob = _WMF_CACHE.pop(next(iter(_WMF_CACHE)))
            _WMF_CACHE_BYTES[0] -= len(ob.rgba) if ob is not None else 0
    return eb


def clear_metafile_cache() -> None:
    """Drop every cached metafile picture (tests; the bytes are recounted from zero)."""
    with _WMF_LOCK:
        _WMF_CACHE.clear()
        _WMF_CACHE_BYTES[0] = 0


_wmf_cache_clear = clear_metafile_cache
_render_wmf_cached.cache_clear = _wmf_cache_clear  # the lru_cache interface tests use


_GDI = None


def _gdi():
    """A private gdi32 instance with its prototypes set once (not the shared windll one, which
    other threads -- the page browser's preview workers -- use too)."""
    global _GDI
    if _GDI is None:
        import ctypes
        from ctypes import wintypes

        g = ctypes.WinDLL("gdi32")
        H, HDC, V = wintypes.HANDLE, wintypes.HDC, ctypes.c_void_p
        for name, res, args in (
            ("CreateCompatibleDC", HDC, [HDC]),
            ("CreateDIBSection", H, [HDC, V, wintypes.UINT, ctypes.POINTER(V), H, wintypes.DWORD]),
            ("SelectObject", H, [HDC, H]),
            ("DeleteObject", wintypes.BOOL, [H]),
            ("DeleteDC", wintypes.BOOL, [HDC]),
            (
                "PatBlt",
                wintypes.BOOL,
                [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD],
            ),
            ("SetMetaFileBitsEx", H, [wintypes.UINT, ctypes.c_char_p]),
            ("PlayMetaFile", wintypes.BOOL, [HDC, H]),
            ("DeleteMetaFile", wintypes.BOOL, [H]),
            ("SetMapMode", ctypes.c_int, [HDC, ctypes.c_int]),
            ("SetViewportOrgEx", wintypes.BOOL, [HDC, ctypes.c_int, ctypes.c_int, V]),
            ("SetViewportExtEx", wintypes.BOOL, [HDC, ctypes.c_int, ctypes.c_int, V]),
            ("GdiFlush", wintypes.BOOL, []),
        ):
            f = getattr(g, name)
            f.restype, f.argtypes = res, args
        _GDI = g
    return _GDI


def _play_wmf(g, hmf, W: int, H: int, vx: int, vy: int, rop: int):
    """One CtDraw play of ``hmf`` into a fresh W x H DIB filled with ``rop`` -> BGRA array."""
    import ctypes
    from ctypes import wintypes
    import numpy as np

    class BIH(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biX", wintypes.LONG),
            ("biY", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImp", wintypes.DWORD),
        ]

    bmi = BIH(biSize=40, biWidth=W, biHeight=-H, biPlanes=1, biBitCount=32)
    hdc = g.CreateCompatibleDC(None)
    if not hdc:
        return None
    ppv = ctypes.c_void_p()
    hbmp = old = None
    try:
        hbmp = g.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(ppv), None, 0)
        if not hbmp or not ppv.value:
            return None
        old = g.SelectObject(hdc, hbmp)
        if not old or not g.PatBlt(hdc, 0, 0, W, H, rop):
            return None
        g.SetMapMode(hdc, 8)  # MM_ANISOTROPIC
        g.SetViewportOrgEx(hdc, 0, 0, None)  # = the extent's top-left
        g.SetViewportExtEx(hdc, vx, vy, None)  # = (r - l) - 1, (b - t) - 1
        if not g.PlayMetaFile(hdc, hmf):
            return None
        g.GdiFlush()
        return (
            np.frombuffer((ctypes.c_ubyte * (W * H * 4)).from_address(ppv.value), np.uint8)
            .reshape(H, W, 4)
            .copy()
        )
    finally:
        if old:
            g.SelectObject(hdc, old)
        if hbmp:
            g.DeleteObject(hbmp)
        g.DeleteDC(hdc)


def _render_wmf(wmf: bytes, target_w: int, target_h: int, max_dim: int) -> Optional[EmbeddedBitmap]:
    import sys

    if not sys.platform.startswith("win"):
        return None
    import numpy as np

    if 2 <= target_w <= 32767 and 2 <= target_h <= 32767:
        bw, bh = target_w, target_h  # the extent it is drawn at
    else:
        wext = wmf_window_extent(wmf)
        if wext is None or abs(wext[0]) < 2 or abs(wext[1]) < 2:
            return None
        bw, bh = abs(wext[0]), abs(wext[1])  # the window's aspect (scaled down below)
    scale = min(1.0, max_dim / float(max(bw, bh)))
    W, H = max(1, int(round(bw * scale))), max(1, int(round(bh * scale)))
    # (w - 1, h - 1) at full size; clamped so a capped picture keeps the window's far edge
    vx = max(1, min(W - 1, int(round((bw - 1) * W / float(bw)))))
    vy = max(1, min(H - 1, int(round((bh - 1) * H / float(bh)))))
    g = _gdi()
    hmf = g.SetMetaFileBitsEx(len(wmf), wmf)
    if not hmf:
        _note_problem("symbols._render_wmf", "SetMetaFileBitsEx failed (%d bytes)" % len(wmf))
        return None
    try:
        on_white = _play_wmf(g, hmf, W, H, vx, vy, 0x00FF0062)  # WHITENESS
        on_black = _play_wmf(g, hmf, W, H, vx, vy, 0x00000042)  # BLACKNESS
    finally:
        g.DeleteMetaFile(hmf)
    if on_white is None or on_black is None:
        # a GDI call failed (out of GDI objects / memory): no picture, and the cache keeps it
        _note_problem(
            "symbols._render_wmf",
            "GDI could not play the metafile into a %dx%d picture; it is not drawn" % (W, H),
        )
        return None
    # untouched = both backgrounds survive; everything else was painted (opaque, white fills too;
    # a non-copy raster op keeps the white-background result). Composed in place (numpy
    # temporaries of a large plan cost memory)
    w32 = on_white.view("<u4").reshape(H, W)
    b32 = on_black.view("<u4").reshape(H, W)
    untouched = ((w32 & 0xFFFFFF) == 0xFFFFFF) & ((b32 & 0xFFFFFF) == 0)
    del on_black, b32, w32
    if untouched.all():
        return None  # nothing drew -> no art
    on_white[..., [0, 2]] = on_white[..., [2, 0]]  # BGRA -> RGBA
    on_white[..., 3] = np.where(untouched, 0, 255)
    return EmbeddedBitmap(width=W, height=H, rgba=on_white.tobytes(), has_alpha=True)


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------
#
# The growable last block. Every in-use directory entry has tag
# 0x0001 except the physically LAST block, which keeps the high 'may grow in
# place' byte (0x0101). CtDraw rewrites that block in place
# even when the new content is longer than its directory size: the file then
# ends at the content's end and the directory KEEPS the old size (LIVE,
# see format/element.py). A reader that slices
# ``data[offset:offset + size]`` silently loses the tail (records, the
# trailer, the thumbnail). The real extent is what the
# block's page head describes: 0x34 + links x 0x60 + objdata + thumb
# (:meth:`ctgedit.format.element.LibraryFile._extent_end`, the rule the writer
# allocates with). Only LIBRARY files: a .ctg's 0x0101 block is its
# 'attributes' block, which has no page head.

#: the element head bytes the extent rule reads (objdata @0, thumb @8,
#: link count @0x1C)
EXTENT_HEAD_SIZE = 0x20
#: page files: their 0x0101 block is 'attributes', never grown by this rule
PAGE_FILE_EXTENSIONS = (".ctg", ".ctf")


def grown_block_end(tag: int, offset: int, size: int, head: bytes, file_size: int, word2: int) -> int:
    """End offset of a library block's bytes."""
    end = offset + size
    if not ((tag & 0xFF) and offset and file_size > end and word2 >= 0x0C):
        return end
    if len(head) < EXTENT_HEAD_SIZE:
        return end
    objdata, _count, thumb = struct.unpack_from("<III", head, 0)
    (links,) = struct.unpack_from("<I", head, 0x1C)
    need = 0x34 + links * 0x60 + objdata + thumb
    if size < need and offset + need <= file_size:
        return offset + need
    return end


def is_page_file(path: str) -> bool:
    """``path`` names a page (.ctg / .ctF), not a library."""
    return os.path.splitext(path)[1].lower() in PAGE_FILE_EXTENSIONS


def read_grown_blocks(container: Container, data: bytes) -> int:
    """Give every LIVE block of a library ``container`` (parsed from
    ``data``) its full extent (:func:`grown_block_end`); ``Block.size`` keeps
    the directory's value, ``Block.data`` holds every byte of the element.
    Returns how many blocks grew. Never call it for a page file."""
    n = 0
    for b in container.blocks:
        if not (b.tag & 0xFF) or not b.size or b.is_deleted:
            continue
        end = grown_block_end(
            b.tag, b.offset, b.size, data[b.offset : b.offset + EXTENT_HEAD_SIZE], len(data), container.word2
        )
        if end > b.offset + b.size:
            b.data = data[b.offset : end]
            n += 1
    return n


@dataclass
class SymbolLibrary:
    """An opened symbol/genie/template library container: wraps a
    :class:`Container`, exposes its named element blocks, parses each on demand
    (cached) into a :class:`PageBlock` via :func:`parse_page`.
    """

    path: str
    container: Container
    _element_cache: Dict[str, Optional[PageBlock]] = field(default_factory=dict)
    #: the file's version it was read at (open_library: fileio.file_version); None when
    #: not known
    version: Optional[Tuple[int, int, int]] = None

    @classmethod
    def open(cls, path: str) -> "SymbolLibrary":
        with open(path, "rb") as fh:
            data = fh.read()
        return cls.from_bytes(path, data)

    @classmethod
    def from_bytes(cls, path: str, data: bytes) -> "SymbolLibrary":
        """A library from its file bytes."""
        c = Container.parse(data, keep_raw=False)
        if not is_page_file(path):
            read_grown_blocks(c, data)
        return cls(path=path, container=c)

    @property
    def name(self) -> str:
        """Library base name (file stem)."""
        return os.path.splitext(os.path.basename(self.path))[0]

    def element_names(self) -> List[str]:
        """All named element blocks in directory order (skipping empty stubs
        and elements CtDraw marked deleted)."""
        return [b.name for b in self.container.blocks if b.name and not b.is_deleted]

    def has_element(self, name: str) -> bool:
        return self._find_block(name) is not None

    def _find_block(self, name: str):
        # exact match first, then case-insensitive (blocks store original case,
        # references occasionally differ)
        blk = self.container.block(name)
        if blk is not None and not blk.is_deleted:
            return blk
        low = name.lower()
        for b in self.container.blocks:
            if b.name.lower() == low and not b.is_deleted:
                return b
        return None

    def element(self, name: str) -> Optional[PageBlock]:
        """Parse element ``name``, or None."""
        if name in self._element_cache:
            return self._element_cache[name]
        blk = self._find_block(name)
        pb: Optional[PageBlock] = None
        if blk is not None and blk.size > 0:
            try:
                # (the records' bags are scanned when first read: most element records
                # are read for their bodies only, page.LazyPageObject)
                pb = parse_page(blk, self.container.word2, lazy=True)
            except Exception:  # noqa: BLE001 -- never crash on a bad block
                # (the miss is cached: this element stays missing for the session)
                _note_exc("symbols.SymbolLibrary.element", element=name, library=self.path)
                pb = None
        # the first parse stored is the one every caller shares (another thread may have
        # parsed the element meanwhile: identity-keyed reuse downstream -- the template
        # alignment, genie_place's walks -- must see ONE PageBlock); setdefault is atomic
        return self._element_cache.setdefault(name, pb)

    def element_objects(self, name: str) -> Optional[List[PageObject]]:
        """The parsed drawing objects of element ``name`` (or ``None`` if absent)."""
        pb = self.element(name)
        return pb.objects if pb is not None else None


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


@dataclass
class ResolvedSymbol:
    """A successfully resolved placed symbol: its reference + parsed content."""

    reference: SymbolReference
    library_path: str
    page: PageBlock

    @property
    def objects(self) -> List[PageObject]:
        return self.page.objects

    @property
    def width(self) -> int:
        return self.page.page_width

    @property
    def height(self) -> int:
        return self.page.page_height


#: directory -> (st_mtime_ns, {lower-case file name: path}): a listing shared by every resolver
#: (each read_page / derive builds one, and its search path lists every sibling project).
#: Re-validated by ONE stat of the directory: NTFS updates a
#: directory's modification time on every create / delete / rename in it. A listing is kept only
#: when the directory was last modified well BEFORE it was listed (_DIR_RACY_NS): a change in the
#: same clock tick as the listing would leave the time unchanged (the file-system time has a
#: granularity of up to 2 s -- FAT); such a directory is listed again next time. The dicts are
#: shared: never mutate one.
_DIR_INDEX_CACHE: Dict[str, Tuple[int, Dict[str, str]]] = {}
_DIR_INDEX_LOCK = _threading.Lock()
_DIR_RACY_NS = 3_000_000_000


def _directory_index(directory: str) -> Dict[str, str]:
    """{lower-case file name: path} of ``directory`` ({} when it cannot be listed)."""
    try:
        mtime = os.stat(directory).st_mtime_ns
    except OSError:
        mtime = None
    if mtime is not None:
        with _DIR_INDEX_LOCK:
            hit = _DIR_INDEX_CACHE.get(directory)
        if hit is not None and hit[0] == mtime:
            return hit[1]
    listed_at = time.time_ns()
    idx: Dict[str, str] = {}
    try:
        names = os.listdir(directory)
    except OSError:
        return idx
    for fn in names:
        idx[fn.lower()] = os.path.join(directory, fn)
    if mtime is not None and mtime < listed_at - _DIR_RACY_NS:
        with _DIR_INDEX_LOCK:
            _DIR_INDEX_CACHE[directory] = (mtime, idx)
    return idx


def clear_directory_index_cache() -> None:
    with _DIR_INDEX_LOCK:
        _DIR_INDEX_CACHE.clear()


class SymbolResolver:
    """Resolve placed symbol references against a library search path."""

    def __init__(self, search_paths: Optional[List[str]] = None):
        self.search_paths: List[str] = [p for p in (search_paths or []) if p]
        # lower-case "<library>.<ext>" -> ALL same-named libraries on the path, in
        # search order. A name is not unique across projects (sa_controls.ctm ships
        # in SA_Include AND SA_Controls with DISJOINT element sets), so resolution
        # must consider every copy.
        self._libs_cache: Dict[str, List[SymbolLibrary]] = {}
        # directory listings (lower-case filename -> real path), built lazily
        self._dir_index: Dict[str, Dict[str, str]] = {}
        #: every library file opened, normcase path -> the version it was read at
        #: (fileio.file_version: (mtime_ns, size, the last change this process knows of)),
        #: and the files each '<library>.<ext>' asked for found (normcase paths, in order):
        #: reader.derive_page compares them with the disk after a library change
        self.opened: Dict[str, Tuple[int, int, int]] = {}
        self.found: Dict[str, List[str]] = {}

    @classmethod
    def for_page(cls, page_path: str, extra_paths: Optional[List[str]] = None) -> "SymbolResolver":
        """A resolver that searches the folders CtDraw would."""
        paths: List[str] = []
        d = os.path.dirname(os.path.abspath(page_path))
        if d:
            paths.append(d)
        for p in extra_paths or []:
            if p and p not in paths:
                paths.append(p)
        # included projects, recursively (CtDraw's real search chain). Walk the
        # chain for the page's dir AND every extra path: an edit re-render parses
        # TEMP bytes with the real project dir passed via CTG_LIB_PATH, and that
        # project's includes must still resolve.
        try:
            from .projects import include_search_dirs

            for root in [d] + [p for p in (extra_paths or []) if p]:
                for p in include_search_dirs(root):
                    if p not in paths:
                        paths.append(p)
        except Exception:  # noqa: BLE001 -- never let topology kill resolution
            _note_exc("symbols.SymbolResolver.for_page", page=page_path)
            pass
        # sibling projects (refs outside the include chain; lowest priority): the Graphics
        # Builder looks in the include chain only, so it is an option
        if os.environ.get("CTG_LIB_SIBLINGS") == "1":
            parent = os.path.dirname(d)
            try:
                for sib in sorted(os.listdir(parent)):
                    sp = os.path.join(parent, sib)
                    if sp != d and os.path.isdir(sp) and sp not in paths:
                        paths.append(sp)
            except OSError:
                pass
        return cls(paths)

    @classmethod
    def for_page_env(cls, page_path: str) -> Optional["SymbolResolver"]:
        """:meth:`for_page` with the extra library folders (CTG_LIB_PATH); None with
        CTG_NO_SYMBOLS=1 or when it cannot be made (noted)."""
        if os.environ.get("CTG_NO_SYMBOLS") == "1":
            return None
        from ..infra.paths import lib_path_dirs

        try:
            return cls.for_page(page_path, extra_paths=lib_path_dirs())
        except Exception:  # noqa: BLE001
            _note_exc("symbols.SymbolResolver.for_page_env", page=page_path)
            return None

    # -- library lookup ---------------------------------------------------
    def _index_dir(self, directory: str) -> Dict[str, str]:
        idx = self._dir_index.get(directory)
        if idx is not None:
            return idx
        idx = _directory_index(directory)
        self._dir_index[directory] = idx
        return idx

    def _find_library_files(self, library: str, ext: str) -> List[str]:
        """Every ``<library>.<ext>`` on the search path, in order."""
        out: List[str] = []
        if "\\" in library or "/" in library:
            rel = library.replace("/", os.sep).replace("\\", os.sep) + "." + ext
            for directory in self.search_paths:
                cand = os.path.join(directory, rel)
                if os.path.isfile(cand) and cand not in out:
                    out.append(cand)
            return out
        target = ("%s.%s" % (library, ext)).lower()
        for directory in self.search_paths:
            hit = self._index_dir(directory).get(target)
            if hit is not None and hit not in out:
                out.append(hit)
        return out

    def library_files(self, library: str, ext: str) -> List[str]:
        """The ``<library>.<ext>`` files on the search path, in search order (the files
        :meth:`libraries` opens), not opened."""
        return list(self._find_library_files(library, ext))

    def libraries(self, library: str, ext: str) -> List[SymbolLibrary]:
        """Open every same-named library on the path, in order."""
        key = ("%s.%s" % (library, ext)).lower()
        hold = getattr(_HOLD, "hold", None)
        if key in self._libs_cache:
            libs = self._libs_cache[key]
            if hold is not None:
                # (inside held_libraries: the version this composition read of each, the
                # resolver's own cache as it was)
                libs = [_held_cached(L, hold) for L in libs]
                hold.found[(tuple(self.search_paths), key)] = self.found.get(key, [])
            _trail(tuple(self.search_paths), key)
            return libs
        libs: List[SymbolLibrary] = []
        for path in self._find_library_files(library, ext):
            try:
                # use the PROCESS-level cache so a library file (and its lazily-parsed element
                # cache) is parsed ONCE per session, not rebuilt for every SymbolResolver / every
                # read_page (template / genie resolution would otherwise re-parse the element
                # records on EVERY render).
                if hold is not None:
                    lib = _held(path, hold)
                    ver = lib.version
                else:
                    ver = _fileio.file_version(path)
                    lib = _open_library_version(path, *ver)
                libs.append(lib)
                self.opened[path_key(path)] = ver
            except Exception:  # noqa: BLE001
                _note_exc("symbols.SymbolResolver.libraries", library=path)
                continue
        self._libs_cache[key] = libs
        self.found[key] = [path_key(p) for p in self._find_library_files(library, ext)]
        if hold is not None:
            hold.found[(tuple(self.search_paths), key)] = self.found[key]
        _trail(tuple(self.search_paths), key)
        return libs

    def library(self, library: str, ext: str) -> Optional[SymbolLibrary]:
        """The first matching library on the path, or ``None``."""
        libs = self.libraries(library, ext)
        return libs[0] if libs else None

    # -- resolution -------------------------------------------------------
    def resolve(self, ref: Optional[SymbolReference], strict: bool = False) -> Optional[ResolvedSymbol]:
        """Resolve a reference to its parsed element, or None."""
        if ref is None:
            return None
        exts = [ref.ext] if strict else ([ref.ext] + [e for e in LIBRARY_EXTENSIONS if e != ref.ext])
        for ext in exts:
            for lib in self.libraries(ref.library, ext):
                pb = lib.element(ref.element)
                if pb is not None:
                    return ResolvedSymbol(reference=ref, library_path=lib.path, page=pb)
        return None


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

#: path -> ((mtime_ns, size), SymbolLibrary), least recently used first. ONE entry per path: a
#: newer version of the file replaces the old entry.
_LIBRARY_CACHE: "OrderedDict[str, Tuple[Tuple[int, int], SymbolLibrary]]" = OrderedDict()
_LIBRARY_CACHE_MAX = 512
#: the block bytes the cache may hold: a page's derive opens many large libraries; past this
#: the least recently used libraries go (their parsed elements stay in _element_cache)
_LIBRARY_CACHE_MAX_BYTES = 96 << 20
_LIBRARY_CACHE_BYTES = 0
_LIBRARY_LOCK = _threading.Lock()


def _library_bytes(lib: "SymbolLibrary") -> int:
    try:
        return sum(len(b.data) for b in lib.container.blocks) + len(lib.container.raw)
    except Exception:  # noqa: BLE001  # swallow-ok: a size for the bound only
        return 0


def _open_library_version(path: str, mtime_ns: int, size: int, change: int = 0) -> SymbolLibrary:
    """The process-cached library of ``path`` as of version ``(mtime_ns, size, change)``
    (fileio.file_version: ``change`` = the last change of the file this process knows of,
    so a second write of one size within one clock tick is not served the first one)."""
    stamp = (mtime_ns, size, change)
    with _LIBRARY_LOCK:
        hit = _LIBRARY_CACHE.get(path)
        if hit is not None and hit[0] == stamp:
            _LIBRARY_CACHE.move_to_end(path)
            return hit[1]
    lib = SymbolLibrary.open(path)
    lib.version = stamp
    size = _library_bytes(lib)
    global _LIBRARY_CACHE_BYTES
    with _LIBRARY_LOCK:
        hit = _LIBRARY_CACHE.get(path)
        if hit is not None and hit[0] == stamp:  # another thread opened it meanwhile
            _LIBRARY_CACHE.move_to_end(path)
            return hit[1]
        if hit is not None:
            _LIBRARY_CACHE_BYTES -= _library_bytes(hit[1])
        _LIBRARY_CACHE[path] = (stamp, lib)
        _LIBRARY_CACHE.move_to_end(path)
        _LIBRARY_CACHE_BYTES += size
        while _LIBRARY_CACHE and (
            len(_LIBRARY_CACHE) > _LIBRARY_CACHE_MAX or _LIBRARY_CACHE_BYTES > _LIBRARY_CACHE_MAX_BYTES
        ):
            _p, (_s, old) = _LIBRARY_CACHE.popitem(last=False)
            _LIBRARY_CACHE_BYTES -= _library_bytes(old)
        if _LIBRARY_CACHE_BYTES < 0 or not _LIBRARY_CACHE:
            _LIBRARY_CACHE_BYTES = sum(_library_bytes(v[1]) for v in _LIBRARY_CACHE.values())
    return lib


def _clear_library_cache() -> None:
    global _LIBRARY_CACHE_BYTES
    with _LIBRARY_LOCK:
        _LIBRARY_CACHE.clear()
        _LIBRARY_CACHE_BYTES = 0


_open_library_version.cache_clear = _clear_library_cache  # the lru_cache interface tests use


def open_library(path: str) -> SymbolLibrary:
    """Open a library by path, cached. Raises on I/O or parse errors."""
    hold = getattr(_HOLD, "hold", None)
    if hold is not None:
        return _held(path, hold)
    return _open_library_version(path, *_fileio.file_version(path))


# ------------------------------------------------------------------ one read per composition
# A composition reads a library more than once: an element's save composes the copies of the
# genies nested in it (genie_norm.normalise_records), then reads each one's stamp for its
# import table (rebuild_link_table); a page's update loads a genie when it collects its
# instances, composes them, then reads the stamp (doc/library.update_genie_instances). A
# library another process rewrites in between (the chain's helper saving a genie, the user's
# second save of a nested one) would leave copies composed from the OLD version under the NEW
# stamp, which every later look takes as current. Inside held_libraries()
# every library file this thread reads is read ONCE: the first version read is the one
# every later read in the hold gets (open_library, SymbolResolver.libraries and
# library_version, which the caches keyed by a library's version use), so each copy and its
# stamp come from the same bytes. The pins are this thread's own. A hold never spans a
# write of a library it reads (the caller ends it first: an
# element save holds its normalisation, serialize_element, not its store), nor a wait that
# lets the window run (a callback that paints or takes other tabs' updates: what the window
# does meanwhile would read the hold's versions, not the files as they are).
_HOLD = _threading.local()


class LibraryHold:
    """What one composition read (:func:`held_libraries`): normcase path -> the
    SymbolLibrary its first read gave."""

    __slots__ = ("pins", "found")

    def __init__(self):
        self.pins: Dict[str, SymbolLibrary] = {}
        #: (a resolver's search path, '<library>.<ext>') -> the files it found (normcase
        #: paths, in order): what a library of the same name put nearer would change
        self.found: Dict[Tuple[Tuple[str, ...], str], List[str]] = {}

    def changed(self) -> List[str]:
        """The library files read in the hold whose version on disk is another one now
        (another process or thread wrote them since): what was composed from them is not the
        current version, so it is not remembered as such (the genie memo, the answers)."""
        out = []
        for lib in list(self.pins.values()):
            try:
                now = _fileio.file_version(lib.path)
            except OSError:
                now = None
            if now != lib.version:
                out.append(lib.path)
        return out


class _Held:
    """The context manager :func:`held_libraries` returns."""

    __slots__ = ("hold", "outer")

    def __enter__(self) -> LibraryHold:
        hold = getattr(_HOLD, "hold", None)
        self.outer = hold is None
        if hold is None:
            hold = _HOLD.hold = LibraryHold()
        self.hold = hold
        return hold

    def __exit__(self, *exc) -> bool:
        if self.outer:
            _HOLD.hold = None
        return False


def held_libraries() -> _Held:
    """``with held_libraries() as hold:`` every library file this thread reads inside is the
    version it first read there (see above); a hold inside another is the outer one."""
    return _Held()


def current_hold() -> Optional[LibraryHold]:
    """This thread's hold, None outside one."""
    return getattr(_HOLD, "hold", None)


def note_lookup(search_paths, library: str, ext: str) -> None:
    """Inside a hold: '<library>.<ext>' looked up on ``search_paths`` counts as a lookup of the
    hold (LibraryHold.found) though no resolver ran it now (a result reused from a memo that
    made it: genie_place._merge_deps, the run's declared parameters)."""
    sp = tuple(search_paths or ())
    key = ("%s.%s" % (library, ext)).lower()
    _trail(sp, key)
    hold = getattr(_HOLD, "hold", None)
    if hold is not None and (sp, key) not in hold.found:
        hold.found[(sp, key)] = [path_key(p) for p in SymbolResolver(list(sp)).library_files(library, ext)]


def _trail(sp: tuple, key: str) -> None:
    for t in getattr(_HOLD, "trails", None) or ():
        t.add((sp, key))


class lookups_made:
    """``with lookups_made() as looks:`` the (search path, '<library>.<ext>') lookups this
    thread makes inside, for a memo that keeps a result to replay them with it
    (:func:`note_lookup`)."""

    def __enter__(self) -> set:
        self.looks: set = set()
        trails = getattr(_HOLD, "trails", None)
        if trails is None:
            trails = _HOLD.trails = []
        trails.append(self.looks)
        return self.looks

    def __exit__(self, *exc) -> bool:
        trails = getattr(_HOLD, "trails", None) or []
        if trails and trails[-1] is self.looks:
            trails.pop()
        elif self.looks in trails:
            trails.remove(self.looks)
        return False


def one_read(fn):
    """A function (a composition) run inside :func:`held_libraries`: each library it reads
    is read once."""
    import functools

    @functools.wraps(fn)
    def run(*a, **k):
        with held_libraries():
            return fn(*a, **k)

    return run


def _pin_key(path: str) -> str:
    return path_key(path)


def _held(path: str, hold: LibraryHold, ver=None) -> SymbolLibrary:
    """The hold's version of the library at ``path``: read now (at ``ver`` when given) when
    the hold has not read it yet. Raises as open_library."""
    k = _pin_key(path)
    lib = hold.pins.get(k)
    if lib is None:
        lib = _open_library_version(path, *(ver if ver is not None else _fileio.file_version(path)))
        hold.pins[k] = lib
    return lib


def _held_cached(lib: SymbolLibrary, hold: LibraryHold) -> SymbolLibrary:
    """A library a resolver cached before the hold, as the hold reads it: the resolver's own
    when it is the file's current version (or the hold read none yet and the file went)."""
    k = _pin_key(lib.path)
    got = hold.pins.get(k)
    if got is not None:
        return got
    try:
        ver = _fileio.file_version(lib.path)
    except OSError:  # swallow-ok: gone since the resolver read it: what the resolver read
        return lib
    if ver == lib.version:
        hold.pins[k] = lib
        return lib
    try:
        return _held(lib.path, hold, ver)
    except Exception:  # noqa: BLE001 -- what the resolver read then
        _note_exc("symbols._held_cached", library=lib.path)
        return lib


def library_version(path: str) -> tuple:
    """:func:`fileio.file_version` of a library file, the version a cache of what was read
    from it is keyed by; inside :func:`held_libraries` the version the hold reads (the file
    read now when the hold has not read it yet). Raises OSError as os.stat."""
    hold = getattr(_HOLD, "hold", None)
    if hold is None:
        return _fileio.file_version(path)
    lib = hold.pins.get(_pin_key(path))
    if lib is not None:
        return lib.version
    ver = _fileio.file_version(path)
    try:
        return _held(path, hold, ver).version
    except Exception:  # noqa: BLE001  # swallow-ok: not a library that opens: its file's version
        return ver


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("symbols.libraries", _LIBRARY_CACHE, clear=_clear_library_cache, on=("refresh", "tests"))
_caches.register("symbols.pictures", _BMP_MEMO, clear=clear_picture_memo, on=("refresh", "tests"))
_caches.register("symbols.metafiles", _WMF_CACHE, clear=clear_metafile_cache, on=("refresh", "tests"))
_caches.register(
    "symbols.folders",
    _DIR_INDEX_CACHE,
    clear=clear_directory_index_cache,
    on=("libraries", "refresh", "tests"),
)
