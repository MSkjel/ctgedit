"""The thumbnail embedded in a page block."""

from __future__ import annotations

import struct

MAX_SIDE = 200
MAX_SCALE = 0.2


def thumb_size(page_w: int, page_h: int) -> tuple:
    if page_w <= 0 or page_h <= 0:
        return 0, 0
    s = min(MAX_SIDE / page_w, MAX_SIDE / page_h, MAX_SCALE)
    return max(1, int(page_w * s)), max(1, int(page_h * s))


def encode(bgrx: bytes, width: int, height: int) -> bytes:
    """``bgrx``: top-down rows of B,G,R,X bytes (X is written as 0)."""
    px = bytearray(bgrx[: width * height * 4])
    px[3::4] = bytes(len(px) // 4)
    head = b"TH" + struct.pack("<IHHI", 14, 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    return head + info + bytes(px)
