"""Write a small OLE compound file."""

from __future__ import annotations

import struct
import uuid
from typing import Iterable, List, Tuple

SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")
SECTOR = 512
MINI = 64
CUTOFF = 4096
FREESECT, ENDOFCHAIN, FATSECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
NOSTREAM = 0xFFFFFFFF


def _sectors(n: int, unit: int) -> int:
    return (n + unit - 1) // unit


def _name_key(name: str):
    return (len(name), name.upper())


def _entry(
    name: str, kind: int, left: int, right: int, child: int, clsid: bytes, start: int, size: int
) -> bytes:
    nm = name.encode("utf-16-le") + b"\x00\x00" if name else b""
    if len(nm) > 64:
        raise ValueError("a stream name is at most 31 characters")
    return (
        nm.ljust(64, b"\x00")
        + struct.pack("<HBB", len(nm), kind, 1 if kind else 0)
        + struct.pack("<III", left, right, child)
        + clsid.ljust(16, b"\x00")
        + struct.pack("<I", 0)
        + bytes(16)
        + struct.pack("<IQ", start, size)
    )


def _tree(ids: List[int]) -> Tuple[int, dict]:
    """A balanced tree over ``ids`` (sorted): (its root, {id: (left, right)})."""
    links = {}

    def go(lo, hi):
        if lo >= hi:
            return NOSTREAM
        mid = (lo + hi) // 2
        links[ids[mid]] = (go(lo, mid), go(mid + 1, hi))
        return ids[mid]

    return go(0, len(ids)), links


def build(clsid: str, streams: Iterable[Tuple[str, bytes]] = ()) -> bytes:
    """A compound file whose root entry carries ``clsid`` ('{...}') and the
    ``streams`` (name, bytes) as its children."""
    streams = sorted(((str(n), bytes(d)) for n, d in streams), key=lambda s: _name_key(s[0]))
    root_clsid = uuid.UUID(clsid.strip("{}")).bytes_le
    small = [(n, d) for n, d in streams if len(d) < CUTOFF]
    mini_data = bytearray()
    mini_start = {}
    for n, d in small:
        mini_start[n] = len(mini_data) // MINI
        mini_data += d + bytes(-len(d) % MINI)
    n_mini = len(mini_data) // MINI
    n_minifat = _sectors(n_mini, SECTOR // 4)
    n_ministream = _sectors(len(mini_data), SECTOR)
    big = [(n, d) for n, d in streams if len(d) >= CUTOFF]
    n_dir = _sectors(1 + len(streams), SECTOR // 128)
    rest = n_dir + n_minifat + n_ministream + sum(_sectors(len(d), SECTOR) for _n, d in big)
    n_fat = 1
    while n_fat * (SECTOR // 4) < rest + n_fat:
        n_fat += 1
    if n_fat > 109:
        raise ValueError("compound file too large for a header-only DIFAT")
    fat = [FATSECT] * n_fat
    sectors: List[bytes] = []

    def alloc(data: bytes) -> int:
        """Append ``data`` in whole sectors chained in the FAT; its first sector."""
        if not data:
            return ENDOFCHAIN
        first = n_fat + len(sectors)
        n = _sectors(len(data), SECTOR)
        for i in range(n):
            sectors.append(data[i * SECTOR : (i + 1) * SECTOR].ljust(SECTOR, b"\x00"))
            fat.append(first + i + 1 if i + 1 < n else ENDOFCHAIN)
        return first

    # directory: the root, the streams in name order, then empty entries
    ids = list(range(1, len(streams) + 1))
    top, links = _tree(ids)
    dir_first = n_fat
    sectors.extend([b""] * n_dir)
    for i in range(n_dir):
        fat.append(dir_first + i + 1 if i + 1 < n_dir else ENDOFCHAIN)
    minifat = []
    for n, d in small:
        k = _sectors(len(d), MINI)
        s = mini_start[n]
        minifat += [s + i + 1 if i + 1 < k else ENDOFCHAIN for i in range(k)]
    minifat += [FREESECT] * (-len(minifat) % (SECTOR // 4))
    minifat_first = alloc(struct.pack("<%dI" % len(minifat), *minifat)) if minifat else ENDOFCHAIN
    ministream_first = alloc(bytes(mini_data)) if mini_data else ENDOFCHAIN
    starts = {n: alloc(d) for n, d in big}
    entries = [_entry("Root Entry", 5, NOSTREAM, NOSTREAM, top, root_clsid, ministream_first, len(mini_data))]
    for i, (n, d) in enumerate(streams, 1):
        left, right = links[i]
        start = mini_start[n] if len(d) < CUTOFF else starts[n]
        entries.append(_entry(n, 2, left, right, NOSTREAM, b"", start if d else ENDOFCHAIN, len(d)))
    while len(entries) < n_dir * (SECTOR // 128):
        entries.append(_entry("", 0, NOSTREAM, NOSTREAM, NOSTREAM, b"", 0, 0))
    blob = b"".join(entries)
    for i in range(n_dir):
        sectors[i] = blob[i * SECTOR : (i + 1) * SECTOR]
    fat += [FREESECT] * (n_fat * (SECTOR // 4) - len(fat))
    difat = list(range(n_fat)) + [FREESECT] * (109 - n_fat)
    head = (
        SIGNATURE
        + bytes(16)
        + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6)
        + bytes(6)
        + struct.pack(
            "<IIIIIIIII",
            0,
            n_fat,
            dir_first,
            0,
            CUTOFF,
            minifat_first,
            len(minifat) // (SECTOR // 4),
            ENDOFCHAIN,
            0,
        )
        + struct.pack("<109I", *difat)
    )
    assert len(head) == SECTOR
    fat_bytes = struct.pack("<%dI" % len(fat), *fat)
    return head + fat_bytes + b"".join(sectors)


def himetric(px: int) -> int:
    """MulDiv(px, 2540, 96): pixels at 96 dpi in HIMETRIC (0.01 mm), rounded."""
    return (int(px) * 2540 + 48) // 96


#: the Contents stream's tag (our own layout, no real control's)
CONTENTS_TAG = b"FXCT"


def control_storage(clsid: str, width_px: int, height_px: int, extra: bytes = None) -> bytes:
    """A made-up control's persisted state: ``clsid`` on the root and a
    Contents stream = 'FXCT', u32 1, the extent in HIMETRIC (i32 x 2), then
    ``extra``."""
    contents = CONTENTS_TAG + struct.pack("<Iii", 1, himetric(width_px), himetric(height_px)) + (extra or b"")
    return build(clsid, [("Contents", contents)])
