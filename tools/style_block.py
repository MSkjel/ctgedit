"""Decode an object's style block."""

from __future__ import annotations

import struct
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctgedit.format.page import parse_ctg, _XFORM_DOUBLES  # noqa: E402


def geom_end(p, start=0):
    o = start
    n = len(p)
    if o + 6 > n:
        return None
    o += 6
    for _ in range(3):
        if o >= n:
            return None
        mode = p[o]
        o += 1
        o += _XFORM_DOUBLES.get(mode, 6 if mode > 3 else 0) * 8
    o += 20
    return o if o <= n else None


def colorref_rgb(v):
    if (v >> 24) & 0xFF == 0xFF:
        return None
    return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)


def try_style_block(p, o):
    """Parse a style block at offset ``o``."""
    if o + 6 + 6 * 8 > len(p):
        return None
    flags = p[o : o + 6]
    if any(b > 0x20 for b in flags):
        return None
    oo = o + 6
    slots = []
    for _ in range(6):
        on, off = struct.unpack_from("<II", p, oo)
        oo += 8
        for v in (on, off):
            if (v >> 24) & 0xFF not in (0x00, 0xFF):
                return None
        slots.append((on, off))
    return flags, slots, oo


def main():
    path = sys.argv[1]
    idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    parsed = parse_ctg(path)
    bg = parsed.attributes.bg_color0 if parsed.attributes else 0
    o = parsed.page.objects[idx]
    p = o.body_raw
    print("FILE %s obj %d type=0x%x bg=0x%08x body=%d" % (path, idx, o.type_code, bg, len(p)))
    ge = geom_end(p)
    print("geom_end=+%04x  bags=%s" % (ge, [(b.offset, b.keys) for b in o.bags]))
    # brute-force: find every offset where a clean 6-flag+6-pair block parses
    hits = []
    for off in range(ge or 0, len(p) - 54):
        r = try_style_block(p, off)
        if r:
            flags, slots, end = r
            hits.append((off, flags, slots, end))
    for off, flags, slots, end in hits:
        print("\n  style block @+%04x flags=%s end=+%04x" % (off, flags.hex(), end))
        names = ["line", "fill", "top/bg", "bottom/shadow", "hilite", "lolite"]
        for i, (on, off2) in enumerate(slots):
            print(
                "    slot%d %-13s on=0x%08x %-14s off=0x%08x %s"
                % (i, names[i], on, str(colorref_rgb(on)), off2, str(colorref_rgb(off2)))
            )


if __name__ == "__main__":
    main()
