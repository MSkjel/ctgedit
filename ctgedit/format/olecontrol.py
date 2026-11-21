"""The property list of an ActiveX control record."""

from __future__ import annotations

import struct
from typing import List, Optional, Tuple

from .page import PageObject
from .textcodec import CODEC

_PREFIXES = (("ACTIVEXEXT_", len("ACTIVEXEXT_")), ("ACTIVEX_", len("ACTIVEX_")))


def _lpstr(body: bytes, o: int) -> Tuple[str, int]:
    n = struct.unpack_from("<H", body, o)[0]
    if o + 2 + n > len(body):
        raise ValueError("lpstr past the body end")
    return body[o + 2 : o + 2 + n].decode(CODEC), o + 2 + n


def find_list(body: bytes):
    """(start of the ProgID lpstr, ProgID, start of the u16 count, pairs), or
    None when the body does not end with such a list. Scans back from the end
    for a printable ProgID containing '.' whose list ends exactly at the body
    end (the list is the last thing a control record stores)."""
    for start in range(len(body) - 4, max(-1, len(body) - 8192), -1):
        try:
            prog, o = _lpstr(body, start)
        except ValueError, struct.error, UnicodeDecodeError:
            continue
        if not prog or "." not in prog or not all(32 <= ord(c) < 127 for c in prog):
            continue
        try:
            count_at = o
            n = struct.unpack_from("<H", body, o)[0]
            o += 2
            pairs = []
            for _ in range(n):
                k, o = _lpstr(body, o)
                v, o = _lpstr(body, o)
                pairs.append((k, v))
        except ValueError, struct.error, UnicodeDecodeError:
            continue
        if o == len(body):
            return start, prog, count_at, pairs
    return None


def wanted_pairs(props) -> List[Tuple[str, str]]:
    """The list CtDraw writes for these bag pairs (in their order)."""
    out = []
    for k, v in props:
        for prefix, n in _PREFIXES:
            if k.upper().startswith(prefix):
                out.append((k[n:], v))
                break
    return out


def rebuild_list(po: PageObject, props=None) -> bool:
    """Rewrite ``po``'s property list from its bag keys; True when it changed.
    A record whose body does not end with a list is left alone."""
    if po.type_code != 0x0D:
        return False
    found = find_list(po.body_raw)
    if found is None:
        return False
    _start, _prog, count_at, pairs = found
    if props is None:
        props = list(po.raw_properties().items())
    want = wanted_pairs(props)
    if want == pairs:
        return False
    enc = bytearray(struct.pack("<H", len(want)))
    for k, v in want:
        for s in (k, v):
            b = s.encode(CODEC)
            enc += struct.pack("<H", len(b)) + b
    po.body_raw = bytes(po.body_raw[:count_at]) + bytes(enc)
    return True


# --------------------------------------------------------------------------- the saved state
#: the OLE compound file signature the control's saved state starts with
CFB_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")


def storage_span(po: PageObject) -> Optional[Tuple[int, int]]:
    """(start, end) of the control's saved state (its OLE compound file) in ``po``'s body, or
    None. The class section is the native rect (8 bytes), a u32 size and the compound file."""
    if po.type_code != 0x0D:
        return None
    from .placement import class_section_offset

    body = po.body_raw
    try:
        o = class_section_offset(po)
        size = struct.unpack_from("<I", body, o + 8)[0]
    except struct.error, ValueError, KeyError, IndexError:
        return None
    start = o + 12
    end = start + size
    if size < 512 or end > len(body) or body[start : start + 8] != CFB_SIGNATURE:
        return None
    return start, end


def storage(po: PageObject) -> Optional[bytes]:
    """The control's saved state (an OLE compound file), or None."""
    span = storage_span(po)
    return None if span is None else bytes(po.body_raw[span[0] : span[1]])


def replace_storage(po: PageObject, data: bytes) -> bool:
    """Put ``data`` (a compound file) in place of the control's saved state, with its size;
    the rest of the record stays as it is. True when the body changed."""
    if data[:8] != CFB_SIGNATURE:
        raise ValueError("not an OLE compound file")
    span = storage_span(po)
    if span is None:
        return False
    start, end = span
    body = po.body_raw
    if body[start:end] == data:
        return False
    po.body_raw = bytes(body[: start - 4]) + struct.pack("<I", len(data)) + bytes(data) + bytes(body[end:])
    return True
