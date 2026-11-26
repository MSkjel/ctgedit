"""Read, replace and import the pictures in bitmap and metafile records."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from .zstream import inflate
from .page import PageObject

BITMAP_TC = 0x07
METAFILE_TC = 0x09
_BIH = 40
#: the biggest picture the loader decodes (symbols._decode_dib)
MAX_SIDE = 8192


@dataclass
class DibSection:
    """Where a bitmap record's DIB section lies and what it holds."""

    rect_at: int  # offset of the native rect (4 x i16)
    start: int  # offset of the u32 flags word
    end: int  # offset past the last stream (== body end)
    flags: int
    uncompressed: int
    primary: bytes  # the stored (compressed) primary stream
    flash: Optional[bytes]  # the stored flash stream, None without 0x100


def _section_at(body: bytes, rect_at: int) -> Optional[DibSection]:
    start = rect_at + 8
    if start + 12 > len(body):
        return None
    flags, unc, n1 = struct.unpack_from("<III", body, start)
    if not flags & 0x1 or flags & ~0x101:
        return None
    o = start + 12
    if o + n1 > len(body):
        return None
    primary = bytes(body[o : o + n1])
    o += n1
    flash = None
    if flags & 0x100:
        if o + 4 > len(body):
            return None
        (n2,) = struct.unpack_from("<I", body, o)
        if o + 4 + n2 > len(body):
            return None
        flash = bytes(body[o + 4 : o + 4 + n2])
        o += 4 + n2
    if o != len(body) or primary[:1] != b"\x78":
        return None
    return DibSection(rect_at, start, o, flags, unc, primary, flash)


def dib_section(po: PageObject) -> Optional[DibSection]:
    """The record's own DIB section, or None."""
    if po.type_code != BITMAP_TC or (po.subtype_size & 0x1) or not po.body_raw:
        return None
    body = po.body_raw
    try:
        from .placement import class_section_offset

        sec = _section_at(body, class_section_offset(po))
        if sec is not None:
            return sec
    except Exception:  # noqa: BLE001 -- fall back to the scan
        pass
    from . import geometry as G
    from .symbols import _find_stream

    gh = G.parse_geom_header(body)
    hit = _find_stream(body, gh.end if gh else 0)
    if hit is None or hit[0] < 20:
        return None
    return _section_at(body, hit[0] - 20)


def _inflate(stream: bytes) -> Optional[bytes]:
    try:
        return inflate(stream)
    except zlib.error:
        return None


def read_pictures(po: PageObject):
    """``(primary, flash_or_None)`` :class:`~ctgedit.format.symbols.EmbeddedBitmap`
    of the record's own DIBs (``(None, None)`` without a DIB section)."""
    from .symbols import _decode_dib

    sec = dib_section(po)
    if sec is None:
        return None, None
    raw = _inflate(sec.primary)
    primary = _decode_dib(raw) if raw is not None else None
    if primary is None:
        return None, None
    flash = None
    if sec.flash is not None:
        fraw = _inflate(sec.flash)
        flash = _decode_dib(fraw) if fraw is not None else None
        if flash is not None and (flash.width, flash.height) != (primary.width, primary.height):
            flash = None
    return primary, flash


def encode_dib(rgba: bytes, width: int, height: int) -> bytes:
    """BITMAPINFOHEADER(40) + top-down 32bpp B,G,R + Windows AND-mask byte
    (0x00 opaque / 0xFF transparent -- the inverse of alpha, threshold 128).
    ``biSizeImage`` is 0 like the engine writes it."""
    import numpy as np

    n = width * height
    src = np.frombuffer(bytes(rgba[: n * 4]), dtype=np.uint8).reshape(n, 4)
    out = np.empty((n, 4), dtype=np.uint8)
    out[:, 0] = src[:, 2]
    out[:, 1] = src[:, 1]
    out[:, 2] = src[:, 0]
    out[:, 3] = np.where(src[:, 3] >= 128, 0x00, 0xFF)
    hdr = struct.pack("<IiiHHIIiiII", _BIH, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    return hdr + out.tobytes()


def normal_rgba(rgba: bytes) -> bytes:
    """The picture as the record can store it: a pixel is opaque (alpha 255)
    or transparent (all four bytes 0) -- the AND mask is one bit."""
    import numpy as np

    a = np.frombuffer(bytes(rgba), dtype=np.uint8).reshape(-1, 4).copy()
    clear = a[:, 3] < 128
    a[clear] = 0
    a[~clear, 3] = 255
    return a.tobytes()


def _same_picture(stored, rgba: bytes, width: int, height: int) -> bool:
    return (
        stored is not None
        and (stored.width, stored.height) == (width, height)
        and normal_rgba(stored.rgba) == normal_rgba(rgba)
    )


def _write_rect(po: PageObject, rect, rect_at: int) -> None:
    """The native rect (at ``rect_at``, where the DIB section was found) +
    header geometry CtDraw derives for a bitmap of rect (it
    keeps the top-left; the Bounds follow the class rule when the stored ones
    did, else keep their edge offsets)."""
    from . import geometry as G
    from .placement import write_header_geom

    gh = G.parse_geom_header(po.body_raw)
    old_r1, old_r2 = tuple(gh.rect1), tuple(gh.rect2)
    flags = G.style_flags(po)
    l, t, r, b = (int(v) for v in rect)
    body = bytearray(po.body_raw)
    struct.pack_into("<4h", body, rect_at, l, t, r, b)
    po.body_raw = bytes(body)
    write_header_geom(po, (l, b - 1), (l, t, r, b), (l, t, r, b))
    new_r1 = (l, t, r, b)
    p_old = G.predict_bounds(po.type_code, old_r1, flags) if flags else None
    if p_old is not None and tuple(p_old) == old_r2:
        r2 = G.predict_bounds(po.type_code, new_r1, flags)
    else:
        r2 = tuple(new_r1[k] + old_r2[k] - old_r1[k] for k in range(4))
    if tuple(r2) != new_r1:
        G._set_rect2(po, r2)


def set_pictures(
    po: PageObject, rgba: bytes, width: int, height: int, flash_rgba: Optional[bytes] = None, rect=None
) -> bool:
    """Put new pictures into a bitmap record in place."""
    from .ctf import _compress
    from .symbols import _decode_dib

    sec = dib_section(po)
    if sec is None or width < 1 or height < 1 or width > MAX_SIDE or height > MAX_SIDE:
        return False
    if len(rgba) < width * height * 4:
        raise ValueError(
            "the picture has %d bytes, a %d x %d picture needs %d"
            % (len(rgba), width, height, width * height * 4)
        )
    if flash_rgba is not None and _same_flash(rgba, flash_rgba, width, height):
        flash_rgba = None
    raw_old = _inflate(sec.primary)
    old_p = _decode_dib(raw_old) if raw_old is not None else None
    old_f = None
    if sec.flash is not None:
        fraw = _inflate(sec.flash)
        old_f = _decode_dib(fraw) if fraw is not None else None
    p_raw = encode_dib(rgba, width, height)
    if _same_picture(old_p, rgba, width, height) and len(raw_old) == len(p_raw):
        p_stream = sec.primary
    else:
        p_stream = _compress(p_raw)
    f_stream = None
    if flash_rgba is not None:
        if (
            sec.flash is not None
            and _same_picture(old_f, flash_rgba, width, height)
            and len(_inflate(sec.flash) or b"") == len(p_raw)
        ):
            f_stream = sec.flash
        else:
            f_stream = _compress(encode_dib(flash_rgba, width, height))
    flags = (sec.flags & ~0x100) | (0x100 if f_stream is not None else 0)
    section = struct.pack("<III", flags, len(p_raw), len(p_stream)) + p_stream
    if f_stream is not None:
        section += struct.pack("<I", len(f_stream)) + f_stream
    body = po.body_raw
    if section != body[sec.start : sec.end]:
        po.body_raw = body[: sec.start] + section
    native = struct.unpack_from("<4h", po.body_raw, sec.rect_at)
    if rect is None:
        if old_p is not None and (old_p.width, old_p.height) == (width, height):
            rect = native  # same size: a stretched bitmap stays stretched
        else:
            rect = (native[0], native[1], native[0] + width, native[1] + height)
    rect = tuple(int(v) for v in rect)
    if rect != tuple(native):
        _write_rect(po, rect, sec.rect_at)
    return True


def _same_flash(rgba: bytes, flash: bytes, width: int, height: int) -> bool:
    n = width * height * 4
    return normal_rgba(rgba[:n]) == normal_rgba(flash[:n])


def recolour(po: PageObject, fn: Callable[[int], int]) -> bool:
    """Map every OPAQUE pixel of both DIBs through ``fn`` (0x00RRGGBB in and
    out; transparent pixels stay) -- Tools > Swap / Adjust Colors on a
    bitmap. Each distinct colour is mapped once. True when a pixel changed."""
    import numpy as np

    primary, flash = read_pictures(po)
    if primary is None:
        return False
    cache: dict = {}

    def mapped(pic) -> Tuple[bytes, bool]:
        a = np.frombuffer(normal_rgba(pic.rgba), dtype=np.uint8).reshape(-1, 4).copy()
        opaque = a[:, 3] == 255
        if not opaque.any():
            return a.tobytes(), False
        rgb = (a[:, 0].astype(np.uint32) << 16) | (a[:, 1].astype(np.uint32) << 8) | a[:, 2].astype(np.uint32)
        cols = np.unique(rgb[opaque])
        new = np.array([cache.setdefault(int(c), fn(int(c)) & 0xFFFFFF) for c in cols], dtype=np.uint32)
        if np.array_equal(new, cols):
            return a.tobytes(), False
        idx = np.searchsorted(cols, rgb[opaque])
        out = new[idx]
        a[opaque, 0] = (out >> 16) & 0xFF
        a[opaque, 1] = (out >> 8) & 0xFF
        a[opaque, 2] = out & 0xFF
        return a.tobytes(), True

    p_rgba, p_hit = mapped(primary)
    f_rgba, f_hit = mapped(flash) if flash is not None else (None, False)
    if not (p_hit or f_hit):
        return False
    return set_pictures(po, p_rgba, primary.width, primary.height, f_rgba)


#: a masked (transparent) pixel as the colour functions of
#: :func:`recolour_states` see it -- CtDraw keeps such a pixel with the
#: top byte 0xFF, the no-colour sentinel every colour operation skips
CLEAR_PIXEL = 0xFFFFFFFF


def _map_pixels(pic_rgba: bytes, fn: Callable[[int], int], cache: dict) -> Tuple[bytes, bool]:
    """Every pixel of an RGBA picture through ``fn`` (0x00RRGGBB for an
    opaque pixel, :data:`CLEAR_PIXEL` for a transparent one; a result with
    the top byte 0xFF is transparent). Each distinct colour mapped once."""
    import numpy as np

    a = np.frombuffer(normal_rgba(pic_rgba), dtype=np.uint8).reshape(-1, 4).copy()
    opaque = a[:, 3] == 255
    px = (a[:, 0].astype(np.uint32) << 16) | (a[:, 1].astype(np.uint32) << 8) | a[:, 2].astype(np.uint32)
    px[~opaque] = CLEAR_PIXEL
    cols = np.unique(px)
    new = np.array([cache.setdefault(int(c), int(fn(int(c))) & 0xFFFFFFFF) for c in cols], dtype=np.uint32)
    if np.array_equal(new, cols):
        return a.tobytes(), False
    out = new[np.searchsorted(cols, px)]
    clear = (out >> 24) == 0xFF
    a[:, 0] = (out >> 16) & 0xFF
    a[:, 1] = (out >> 8) & 0xFF
    a[:, 2] = out & 0xFF
    a[:, 3] = 255
    a[clear] = 0
    return a.tobytes(), True


def recolour_states(
    po: PageObject,
    fn_primary: Callable[[int], int],
    fn_flash: Optional[Callable[[int], int]] = None,
    *,
    add_flash: bool = False,
) -> bool:
    """Swap / Adjust Colors on a bitmap's pixels."""
    primary, flash = read_pictures(po)
    if primary is None:
        return False
    fn_flash = fn_primary if fn_flash is None else fn_flash
    p_rgba, p_hit = _map_pixels(primary.rgba, fn_primary, {})
    f_src = flash.rgba if flash is not None else (primary.rgba if add_flash else None)
    f_rgba, f_hit = (None, False)
    if f_src is not None:
        f_rgba, f_hit = _map_pixels(f_src, fn_flash, {})
        if flash is None:
            f_hit = f_rgba != normal_rgba(primary.rgba)
    if not (p_hit or f_hit):
        return False
    return set_pictures(po, p_rgba, primary.width, primary.height, f_rgba)


def resample(rgba: bytes, width: int, height: int, new_w: int, new_h: int) -> bytes:
    """Nearest-neighbour stretch (what StretchBlt with COLORONCOLOR does):
    no colour a picture did not have, the transparency stays one bit."""
    import numpy as np

    if (width, height) == (new_w, new_h):
        return bytes(rgba[: width * height * 4])
    a = np.frombuffer(bytes(rgba[: width * height * 4]), dtype=np.uint8).reshape(height, width, 4)
    ys = (np.arange(new_h) * height) // new_h
    xs = (np.arange(new_w) * width) // new_w
    return a[ys][:, xs].tobytes()


# --------------------------------------------------------------------------- metafiles
_PLACEABLE_KEY = 0x9AC6CDD7
_META_SETWINDOWORG = 0x020B
_META_SETWINDOWEXT = 0x020C
_META_EOF = 0x0000


class MetafileError(ValueError):
    """A .wmf file this editor cannot place (with the reason in plain words)."""


def _records(wmf: bytes, hdr_words: int):
    """(offset, size_words, function) of each record after the header."""
    o = hdr_words * 2
    while o + 6 <= len(wmf):
        size, fn = struct.unpack_from("<IH", wmf, o)
        if size < 3 or o + size * 2 > len(wmf):
            raise MetafileError("a record runs past the end of the file")
        yield o, size, fn
        if fn == _META_EOF:
            return
        o += size * 2
    raise MetafileError("the file ends before its end-of-file record")


def wmf_from_file(data: bytes, dpi: int = 96) -> Tuple[bytes, int, int]:
    """Read a .wmf file as ``(metafile, width_px, height_px)``."""
    bbox = None
    inch = 0
    if len(data) >= 22 and struct.unpack_from("<I", data, 0)[0] == _PLACEABLE_KEY:
        l, t, r, b = struct.unpack_from("<4h", data, 6)
        inch = struct.unpack_from("<H", data, 14)[0]
        bbox = (l, t, r, b)
        data = data[22:]
    if len(data) < 18:
        raise MetafileError("the file is too short to be a Windows Metafile")
    mt_type, hdr_words, version, mt_size, n_obj, max_rec, _np = struct.unpack_from("<HHHIHIH", data, 0)
    if mt_type not in (1, 2) or hdr_words != 9:
        raise MetafileError(
            "the file is not a Windows Metafile (.wmf); an enhanced "
            "metafile (.emf) or another format cannot be placed"
        )
    if mt_size * 2 > len(data) or mt_size < 12:
        mt_size = len(data) // 2
    wmf = bytearray(data[: mt_size * 2])
    org = ext = None
    for o, _size, fn in _records(bytes(wmf), hdr_words):
        if fn == _META_SETWINDOWORG and org is None:
            y, x = struct.unpack_from("<2h", wmf, o + 6)
            org = (x, y)
        elif fn == _META_SETWINDOWEXT and ext is None:
            y, x = struct.unpack_from("<2h", wmf, o + 6)
            ext = (x, y)
    if bbox is not None:
        bw, bh = abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1])
        per_inch = inch or 1440
        width = max(1, round(bw * dpi / per_inch))
        height = max(1, round(bh * dpi / per_inch))
        if ext is None:
            add = struct.pack(
                "<IH2h", 5, _META_SETWINDOWORG, min(bbox[1], bbox[3]), min(bbox[0], bbox[2])
            ) + struct.pack("<IH2h", 5, _META_SETWINDOWEXT, bh, bw)
            at = hdr_words * 2
            wmf[at:at] = add
            mt_size += len(add) // 2
            max_rec = max(max_rec, 5)
    elif ext is not None and ext[0] and ext[1]:
        width, height = abs(ext[0]), abs(ext[1])
    else:
        raise MetafileError("the metafile states no size (it has no placeable header and no window extent)")
    struct.pack_into("<HHHIHIH", wmf, 0, 1, hdr_words, version, mt_size, n_obj, max_rec, _np)
    # a huge logical extent (twips, HIMETRIC) must not make a giant object
    big = max(width, height)
    if big > 4000:
        width = max(1, round(width * 4000 / big))
        height = max(1, round(height * 4000 / big))
    return bytes(wmf), int(width), int(height)


def metafile_bits(po: PageObject) -> Optional[bytes]:
    """The memory metafile a MetaFile record (tc 0x09) stores, or None."""
    if po.type_code != METAFILE_TC or not po.body_raw:
        return None
    try:
        from .placement import class_section_offset

        o = class_section_offset(po) + 8
    except Exception:  # noqa: BLE001
        return None
    b = po.body_raw
    if o + 18 > len(b) or struct.unpack_from("<HH", b, o) not in ((1, 9), (2, 9)):
        return None
    words = struct.unpack_from("<I", b, o + 6)[0]
    return bytes(b[o : o + words * 2]) if o + words * 2 <= len(b) else None


def set_metafile(po: PageObject, wmf: bytes, rect=None) -> bool:
    """Put a new memory Windows Metafile (:func:`wmf_from_file`) into the MetaFile record
    ``po`` IN PLACE (Replace Picture): the record's bags, name, AN and z-order stay;
    ``rect`` = its new extent (default: the stored one). False when the record holds no
    metafile."""
    old = metafile_bits(po)
    if old is None or len(wmf) < 18 or struct.unpack_from("<HH", wmf, 0) not in ((1, 9), (2, 9)):
        return False
    from .placement import class_section_offset

    rect_at = class_section_offset(po)
    start = rect_at + 8
    body = po.body_raw
    if bytes(body[start : start + len(old)]) != bytes(wmf):
        po.body_raw = body[:start] + bytes(wmf) + body[start + len(old) :]
    if rect is not None:
        rect = tuple(int(v) for v in rect)
        if rect != tuple(struct.unpack_from("<4h", po.body_raw, rect_at)):
            _write_rect(po, rect, rect_at)
    return True


def picture_digest(po: PageObject) -> Optional[str]:
    """A fingerprint of the record's OWN picture: its size and pixels (both flash states;
    transparency as stored, one bit), so two copies of one picture match whoever wrote
    them (the compressed bytes differ between the engine and ctgedit). None without a
    DIB section."""
    import hashlib

    primary, flash = read_pictures(po)
    if primary is None:
        return None
    h = hashlib.sha1(struct.pack("<II", primary.width, primary.height))
    h.update(normal_rgba(primary.rgba))
    if flash is not None:
        h.update(b"flash")
        h.update(normal_rgba(flash.rgba))
    return h.hexdigest()


def stored_size(po: PageObject) -> int:
    """The bytes the record's own picture takes in the page (its compressed DIBs, or its
    metafile); 0 when it holds none."""
    sec = dib_section(po)
    if sec is not None:
        return len(sec.primary) + (len(sec.flash) + 4 if sec.flash is not None else 0)
    wmf = metafile_bits(po)
    return len(wmf) if wmf is not None else 0


def placeable_wmf(wmf: bytes, width_px: int, height_px: int, dpi: int = 96) -> bytes:
    """A MetaFile record's memory metafile as a .wmf FILE (Export Picture): a placeable
    header whose box is the metafile's window (SetWindowOrg / SetWindowExt) and whose units
    per inch make it ``width_px`` x ``height_px`` at ``dpi``."""
    hdr_words = struct.unpack_from("<H", wmf, 2)[0] if len(wmf) >= 18 else 9
    org, ext = (0, 0), None
    for o, _size, fn in _records(wmf, hdr_words):
        if fn == _META_SETWINDOWORG:
            y, x = struct.unpack_from("<2h", wmf, o + 6)
            org = (x, y)
        elif fn == _META_SETWINDOWEXT and ext is None:
            y, x = struct.unpack_from("<2h", wmf, o + 6)
            ext = (x, y)
    if ext is None or not ext[0] or not ext[1]:
        ext = (max(1, width_px) * 15, max(1, height_px) * 15)
    bw, bh = abs(ext[0]), abs(ext[1])
    inch = max(1, min(0xFFFF, round(bw * dpi / max(1, width_px))))
    l, t = org
    head = struct.pack("<IH4hHI", _PLACEABLE_KEY, 0, l, t, l + bw, t + bh, inch, 0)
    check = 0
    for (word,) in struct.iter_unpack("<H", head):
        check ^= word
    return head + struct.pack("<H", check) + bytes(wmf)
