"""Bounded zlib inflation for the pictures stored in records: a damaged or crafted stream
never makes a reader allocate more than :data:`MAX_INFLATE` bytes (a stream of about 1 MB
can otherwise inflate to gigabytes)."""

from __future__ import annotations

import zlib

#: the most a picture stream may inflate to: far past any DIB the (32-bit) Graphics Builder
#: holds
MAX_INFLATE = 0x10000000


def inflate(data: bytes, limit: int = MAX_INFLATE) -> bytes:
    """``zlib.decompress(data)`` that refuses to give more than ``limit`` bytes: the whole
    stream (bytes after its end are ignored), or zlib.error for a damaged, cut or too big one."""
    d = zlib.decompressobj()
    out = d.decompress(data, limit + 1)
    if len(out) > limit:
        raise zlib.error("the stream inflates past %d bytes" % limit)
    if not d.eof:
        raise zlib.error("incomplete or truncated stream")
    return out


def inflate_some(data: bytes, limit: int = MAX_INFLATE) -> bytes:
    """What a stream gives before it breaks off (a cut stream's first part), at most
    ``limit`` bytes; zlib.error for one that is damaged from the start."""
    return zlib.decompressobj().decompress(data, limit)
