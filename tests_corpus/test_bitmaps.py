"""Bitmaps over the real corpus."""

import glob
import os

import pytest

from ctgedit.format import bitmaps, placement

R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"
AGITATOR = os.path.join(R2, "Include", "AGITATOR.CTL")
LIBCTL = os.path.join(R2, "Library_Controls", "lib_controls.ctm")
need_agit = pytest.mark.skipif(not os.path.exists(AGITATOR), reason="Include corpus missing")

GREEN = (0, 255, 0, 255)


def _flashing_record_of_the_corpus():
    from ctgedit.format.symbols import open_library

    lib = open_library(AGITATOR)
    for name in lib.element_names():
        for po in lib.element_objects(name) or []:
            sec = bitmaps.dib_section(po)
            if sec is not None and sec.flash is not None:
                return po
    return None


@pytest.mark.skipif(not os.path.isdir(os.path.join(R2, "Example")), reason="corpus missing")
def test_unchanged_pictures_leave_every_corpus_bitmap_byte_identical():
    """Putting a bitmap's own pictures back changes nothing -- the engine's
    deflate stream, the flash DIB and the extent of a stretched bitmap stay."""
    from ctgedit.format.page import parse_ctg_bytes
    from ctgedit.format.symbols import open_library

    groups = []
    for path in sorted(glob.glob(os.path.join(R2, "Example", "*.ctg")))[:40]:
        pg = parse_ctg_bytes(open(path, "rb").read())
        groups.append(pg.page.objects if pg.page else [])
    if os.path.exists(AGITATOR):
        lib = open_library(AGITATOR)
        groups += [lib.element_objects(e) or [] for e in lib.element_names()]
    n = flashing = 0
    for objs in groups:
        for po in objs:
            if bitmaps.dib_section(po) is None:
                continue
            p, f = bitmaps.read_pictures(po)
            before = po.body_raw
            assert bitmaps.set_pictures(po, p.rgba, p.width, p.height, f.rgba if f is not None else None)
            assert po.body_raw == before
            n += 1
            flashing += f is not None
    assert n > 20


@need_agit
def test_editing_one_state_of_a_corpus_flashing_bitmap_keeps_the_rest():
    po = _flashing_record_of_the_corpus()
    assert po is not None
    sec = bitmaps.dib_section(po)
    p, f = bitmaps.read_pictures(po)
    before = po.body_raw
    new = bytearray(p.rgba)
    new[0:4] = bytes(GREEN)
    assert bitmaps.set_pictures(po, bytes(new), p.width, p.height, f.rgba)
    sec2 = bitmaps.dib_section(po)
    assert sec2.flags == 0x101 and sec2.flash == sec.flash  # flash bytes kept
    assert sec2.primary != sec.primary
    assert po.body_raw[: sec.start] == before[: sec.start]  # bags, name, rect
    p2, f2 = bitmaps.read_pictures(po)
    assert p2.rgba[:4] == bytes(GREEN) and f2.rgba == f.rgba


@pytest.mark.skipif(not os.path.exists(LIBCTL), reason="Library_Controls corpus missing")
def test_class_section_follows_the_metadata_bag():
    """dwFlags 0x100: the loader reads the metadata bag after
    the style block and BEFORE the class section -- the native rect / DIB
    section of a lib_controls bitmap sits past it."""
    from ctgedit.format.symbols import open_library

    lib = open_library(LIBCTL)
    hits = 0
    for e in ("equiptree", "browsetable"):
        for po in lib.element_objects(e) or []:
            sec = bitmaps.dib_section(po)
            if sec is None or not po.subtype_size & 0x100:
                continue
            assert placement.class_section_offset(po) == sec.rect_at
            hits += 1
    assert hits
