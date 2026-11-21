"""Library reads that CtDraw's own writes can trip."""

import os
import struct


import corpus
from ctgedit.format import element as E


def _grown_id(row) -> str:
    if hasattr(row, "marks"):  # (the one skipped set when the role is not set)
        return "grown_blocks"
    return os.path.basename(row[0]) + ":" + row[1]


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _head(data: bytes, off: int):
    objdata, count, thumb = struct.unpack_from("<III", data, off)
    links = struct.unpack_from("<I", data, off + 0x1C)[0]
    return objdata, count, thumb, links


def _direct_children(objs, i):
    """The indices of record ``i``'s direct children in a depth-first forest."""
    j = i + 1
    for _ in range(objs[i].class_flag):
        yield j
        j = E.root_tree_end(objs, j)


def _content(data: bytes, off: int) -> int:
    """A block's real extent from its head: 0x34 + links x 0x60 + records + thumbnail."""
    objdata, _n, thumb, links = _head(data, off)
    return 0x34 + links * 0x60 + objdata + thumb


def _lpstr(text: bytes) -> bytes:
    return struct.pack("<H", len(text)) + text


def test_the_shipped_libraries_are_already_tiled():
    """CtDraw's retile (tests/test_library_growth.py,
    test_the_first_change_retiles_the_directory_as_ctdraws_load) changes none of the
    shipped libraries: they are already tiled."""
    import glob

    inc = os.path.join(corpus.R2_USER, "Include")
    for f in sorted(glob.glob(os.path.join(inc, "*.ct[lmt]"))):
        raw = open(f, "rb").read()
        lf = E.LibraryFile(raw)
        lf._retile()
        assert lf.to_bytes() == raw and not lf._dirty, f
