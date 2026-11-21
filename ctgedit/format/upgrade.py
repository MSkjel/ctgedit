"""Upgrade old page files to the current form when they load."""

from __future__ import annotations

import struct
from typing import Optional

from . import links

#: the oldest form this module reads: CtDraw does not open a word2 4 page either
MIN_WORD2 = 9
#: the current form
CURRENT_WORD2 = 0x0C
CURRENT_VERSION = 9

_HEAD_LEGACY = 0x24
_HEAD = 0x34
_RECORD_HEADER = 8
_RECT_CLASSES = (0x0F, 0x11, 0x1001)  # rectangle, ellipse, button
_XFORM_DOUBLES = {0: 0, 1: 2, 2: 2, 3: 4, 8: 6}  # doubles per transform slot (page.py)


def is_legacy(word2: int) -> bool:
    return word2 < CURRENT_WORD2


def _upgrade_payload(payload: bytes, word2: int) -> bytes:
    """One record payload in the current form."""
    if len(payload) < 0x0C:
        return payload
    tc = struct.unpack_from("<I", payload, 2)[0]
    an_len = struct.unpack_from("<H", payload, 0x0A)[0]
    an_end = 0x0C + an_len
    if an_end > len(payload):
        return payload
    body = bytearray(payload[an_end:])
    if word2 < 10 and len(body) >= 4:
        body[4:4] = b"\x00\x00"  # the (empty) name lpstr
    if word2 < 0x0B and tc in _RECT_CLASSES:
        return _old_class_rect(bytes(payload[:an_end]) + bytes(body), tc)
    return bytes(payload[:an_end]) + bytes(body)


def _xp_style(po) -> bool:
    """The button draws XP_STYLE (its VIS_MODE, case-insensitive: == 4)."""
    v = po.raw_properties().get("VIS_MODE", "")
    return str(v or "").strip().upper() == "XP_STYLE"


def _old_class_rect(payload: bytes, tc: int) -> bytes:
    """The old class-rect rule for rectangles, ellipses and buttons."""
    from . import geometry as G, placement as P, writer
    from .page import parse_payload

    po = parse_payload(payload)
    if not po.subtype_size & 0x2:
        return payload  # no class part: nothing to read
    gh = G.parse_geom_header(po.body_raw)
    try:
        o = P.class_section_offset(po)
    except Exception:  # noqa: BLE001  swallow-ok: an odd record keeps its stored form
        return payload
    if gh is None or o is None or o + 8 > len(po.body_raw):
        return payload
    l, t, r, b = struct.unpack_from("<4h", po.body_raw, o)
    for _ in range(2 if tc == 0x1001 and _xp_style(po) else 1):
        if r > l:
            r -= 1
        if b > t:
            b -= 1
    body = bytearray(po.body_raw)
    struct.pack_into("<4h", body, o, l, t, r, b)
    po.body_raw = bytes(body)
    try:
        (_s1, _s2, (m3, T3, _o3, _n3)), _e = P.read_slots(po.body_raw)
    except Exception:  # noqa: BLE001  swallow-ok: a record without slots keeps its header
        return writer.object_payload(po)
    rect1 = tuple(int(v) for v in P.apply_rect(T3, m3, (l, t, r, b)))
    ax, ay = (l + (r - l - 1) // 2, t) if tc == 0x11 else (l, b)
    anchor = tuple(int(v) for v in P.apply_rect(T3, m3, (ax, ay, ax, ay))[:2])
    flags = G.style_flags(po)
    rect2 = G.predict_bounds(tc, rect1, flags) if flags is not None else None
    if rect2 is None:
        ol, ot, orr, ob = G.norm_rect(gh.rect1)
        l2, t2, r2, b2 = gh.rect2
        rect2 = (rect1[0] + l2 - ol, rect1[1] + t2 - ot, rect1[2] + r2 - orr, rect1[3] + b2 - ob)
    P.write_header_geom(po, anchor, rect1, rect2)
    if tc == 0x1001:
        props = po.raw_properties()
        for key, v in (("WIDTH", r - l), ("HEIGHT", b - t)):
            if key in props and props[key] != str(v):
                writer.set_object_property(po, key, str(v))
    return writer.object_payload(po)


def upgrade_page_block(data: bytes, word2: int, guid: Optional[bytes] = None) -> bytes:
    """A page / element block in the current form (``guid`` = the 16 bytes that follow the
    save stamp; zero when not given)."""
    if len(data) < _HEAD_LEGACY:
        return data
    objdata_size, count, thumb_size = struct.unpack_from("<III", data, 0)
    link_count = struct.unpack_from("<I", data, 0x1C)[0]
    data_start = _HEAD_LEGACY + link_count * links.LINK_ENTRY_SIZE
    t = links.parse_link_table(data[_HEAD_LEGACY:data_start], legacy=True) if link_count else None
    link_raw, new_links = links.serialize_link_table(t[0], t[1]) if t else (b"", 0)
    payloads = []
    off = data_start
    for _ in range(count):
        if off + _RECORD_HEADER > len(data):
            break
        size = struct.unpack_from("<I", data, off)[0]
        payloads.append(_upgrade_payload(data[off + _RECORD_HEADER : off + _RECORD_HEADER + size], word2))
        off += _RECORD_HEADER + size
    recs = bytearray()
    ends = []
    running = 0
    for pl in payloads:
        running += _RECORD_HEADER + len(pl)
        ends.append(running)
    for i, pl in enumerate(payloads):
        recs += struct.pack("<II", len(pl), ends[i] if i + 1 < len(payloads) else 0)
        recs += pl
    thumb_at = data_start + objdata_size
    rest = data[thumb_at:]  # the thumbnail and the zero pad
    head = bytearray(data[:0x24]) + bytes((guid or b"\x00" * 16)[:16]).ljust(16, b"\x00")
    struct.pack_into("<I", head, 0, len(recs))
    struct.pack_into("<I", head, 0x1C, new_links)
    return bytes(head) + link_raw + bytes(recs) + rest


def upgrade_attributes_block(data: bytes, guid: Optional[bytes] = None) -> bytes:
    """An attributes block in the current form: the GUID inserted at 0x24 (outer_len and
    inner_len are unchanged: both count from the field after the identity)."""
    if len(data) < _HEAD_LEGACY:
        return data
    return data[:0x24] + bytes((guid or b"\x00" * 16)[:16]).ljust(16, b"\x00") + data[0x24:]


def upgrade_container(c, guid: Optional[bytes] = None) -> bool:
    """Upgrade the ``page`` and ``attributes`` blocks of a parsed page container in place
    and bump its version / word2; False when it is current already."""
    if not is_legacy(c.word2):
        return False
    from ..infra import identity

    guid = guid or identity.new_guid().bytes_le
    pg = c.block("page")
    at = c.block("attributes")
    if pg is not None and pg.data:
        pg.data = upgrade_page_block(pg.data, c.word2, guid)
    if at is not None and at.data:
        at.data = upgrade_attributes_block(at.data, guid)
    c.upgraded_from = c.word2
    c.word2 = CURRENT_WORD2
    c.version = max(c.version, CURRENT_VERSION)
    c.relayout()
    return True
