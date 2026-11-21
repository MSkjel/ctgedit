"""Bitmap deflate with the install's zlib.dll."""

import os
import struct

import pytest

from ctgedit.format import factory


@pytest.mark.skipif(
    not os.path.exists(r"C:\Program Files (x86)\AVEVA Plant SCADA\Bin\Bin (x64)\zlib.dll"),
    reason="AVEVA zlib.dll missing",
)
def test_factory_bitmap_deflate_is_ctdraws():
    """The 8x8 red factory bitmap's DIB stream exactly as the live engine
    re-deflated it on PageSave (engine_roundtrip factory)."""
    from ctgedit.format.symbols import _find_stream

    po = factory.make_bitmap_record(925, (95, 95, 103, 103), bytes([255, 0, 0, 255]) * 64, 8, 8)
    off, _raw = _find_stream(po.body_raw, 0)
    comp = struct.unpack_from("<I", po.body_raw, off - 4)[0]
    assert po.body_raw[off : off + comp].hex() == "785ed3606060e000e21fffffff67645060c00dfe8f680c00aca64407"
