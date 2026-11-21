"""Compare a .ctF we build with the one CtDraw wrote."""

from __future__ import annotations

import struct
import zlib

from ctgedit.format import ctf as CTF
from ctgedit.format.container import Container


def has_classic_zlib() -> bool:
    """ctgedit deflates as CtDraw does (the install's zlib.dll loads)."""
    return bool(CTF._zlib_lib())


def _inflate(b: bytes, at: int, n: int, size: int):
    """The DIB of the n-byte zlib stream at ``at`` when it inflates to exactly ``size``
    bytes and ends there; None otherwise."""
    if n < 2 or at + n > len(b) or b[at] != 0x78 or ((b[at] << 8) | b[at + 1]) % 31:
        return None
    d = zlib.decompressobj()
    try:
        raw = d.decompress(b[at : at + n])
    except zlib.error:
        return None
    if not d.eof or d.unused_data or len(raw) != size:
        return None
    return raw


def dib_sections(payload: bytes):
    """Every deflated DIB section of a record payload: (start, end, flags, raws). A section
    is u32 flags (bit 0x1 zlib, bit 0x100 a flash DIB follows), u32 the size of one DIB,
    u32 n1 + zlib(primary) [, u32 n2 + zlib(flash)] (bitmaps.py; the background record
    has flags 1)."""
    out = []
    i = payload.find(b"\x78", 12)
    while i >= 0:
        hit = None
        if i >= 12:
            flags, size, n1 = struct.unpack_from("<III", payload, i - 12)
            raw = _inflate(payload, i, n1, size) if flags & 0x1 else None
            if raw is not None:
                raws, end = [raw], i + n1
                if flags & 0x100 and end + 4 <= len(payload):
                    (n2,) = struct.unpack_from("<I", payload, end)
                    raw2 = _inflate(payload, end + 4, n2, size)
                    if raw2 is not None:
                        raws.append(raw2)
                        end += 4 + n2
                hit = (i - 12, end, flags, raws)
        if hit is not None:
            out.append(hit)
            i = payload.find(b"\x78", hit[1] + 12)
        else:
            i = payload.find(b"\x78", i + 1)
    return out


def _redeflate(payload: bytes) -> bytes:
    secs = dib_sections(payload)
    if not secs:
        return payload
    out, pos = bytearray(), 0
    for start, end, flags, raws in secs:
        out += payload[pos:start]
        z = [CTF._compress(r) for r in raws]
        out += struct.pack("<III", flags, len(raws[0]), len(z[0])) + z[0]
        if len(z) > 1:
            out += struct.pack("<I", len(z[1])) + z[1]
        pos = end
    out += payload[pos:]
    return bytes(out)


def records(data: bytes):
    """(container, page block, records start, [payload, ...], records end) of a .ctF."""
    c = Container.parse(data)
    blk = c.block("page")
    b = blk.data
    count, links = struct.unpack_from("<I", b, 0x04)[0], struct.unpack_from("<I", b, 0x1C)[0]
    start = pos = 0x34 + links * 0x60
    payloads = []
    for _k in range(count):
        n, nxt = struct.unpack_from("<II", b, pos)
        payloads.append(b[pos + 8 : pos + 8 + n])
        pos += 8 + n
        if not nxt:
            break
    return c, blk, start, payloads, pos


def redeflated(data: bytes) -> bytes:
    """``data`` with every DIB of its records deflated again by ``ctf._compress``."""
    c, blk, start, payloads, end = records(data)
    new = [_redeflate(p) for p in payloads]
    if new == payloads:
        return data
    objdata = bytearray()
    running = 0
    for k, p in enumerate(new):
        running += 8 + len(p)
        objdata += struct.pack("<II", len(p), running if k + 1 < len(new) else 0) + p
    head = bytearray(blk.data[:start])
    struct.pack_into("<I", head, 0x00, len(objdata))
    blk.data = bytes(head) + bytes(objdata) + blk.data[end:]
    c.relayout()
    return c.to_bytes()


def comparable(ours: bytes, ctdraw: bytes):
    """The two files as they compare: as they are with the classic zlib, else both with
    every DIB deflated again by ``ctf._compress``."""
    if has_classic_zlib():
        return ours, ctdraw
    return redeflated(ours), redeflated(ctdraw)


def first_difference(a: bytes, b: bytes) -> str:
    if a == b:
        return "equal"
    n = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return "sizes %d / %d, first difference at 0x%x: %s / %s" % (
        len(a),
        len(b),
        n,
        a[n : n + 8].hex(),
        b[n : n + 8].hex(),
    )


def page_identity(data: bytes) -> bytes:
    """The page identity of a page file: the u32 save stamp, then the 16-byte page VERSION
    (CtDraw's staleness check compares the version of the .ctg and the .ctF)."""
    blk = Container.parse(data).block("page")
    return bytes(blk.data[0x20:0x34])


def with_save_stamps(ours: bytes, ctdraw: bytes) -> bytes:
    """``ours`` with the save stamps of CtDraw's .ctF."""
    if len(ours) < 0x10 or len(ctdraw) < 0x10:
        return ours
    c = Container.parse(ours)
    d = Container.parse(ctdraw)
    fixed = bytearray(ours)
    fixed[4:12] = ctdraw[4:12]
    for name in ("page", "attributes"):
        a, b = c.block(name), d.block(name)
        if a is None or b is None or a.size < 0x24 or b.size < 0x24:
            return ours
        oa, ob = a.offset + 0x20, b.offset + 0x20
        if ours[oa : oa + 4] != ours[8:12] or ctdraw[ob : ob + 4] != ctdraw[8:12]:
            return ours
        fixed[oa : oa + 4] = ctdraw[ob : ob + 4]
    return bytes(fixed)


def same_ctf(ours: bytes, ctdraw: bytes, *, stamps: bool = False, session: bool = False) -> bool:
    """``ours`` equals CtDraw's .ctF: byte for byte; ``stamps``: up to the save stamps
    (:func:`with_save_stamps`); ``session``: up to the stamps and the state of the session
    that wrote it (editing_base.ctf_matches). See the module docstring for the deflate
    streams."""
    from ctgedit.editing_base import ctf_matches

    a, b = comparable(ours, ctdraw)
    if session:
        return ctf_matches(a, b)
    if stamps:
        a = with_save_stamps(a, b)
    return a == b


def assert_same_ctf(ours: bytes, ctdraw: bytes, *, stamps: bool = False, session: bool = False) -> None:
    if not same_ctf(ours, ctdraw, stamps=stamps, session=session):
        a, b = comparable(ours, ctdraw)
        if stamps or session:
            a = with_save_stamps(a, b)
        raise AssertionError(
            "the .ctF differs from CtDraw's (%s; %s)"
            % ("byte for byte" if has_classic_zlib() else "the DIBs deflated again", first_difference(a, b))
        )
