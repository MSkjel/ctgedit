"""Header transforms: rotate and mirror."""

import os

import pytest

import fxinstall


def _oracle_doc(fx_install, unit, page):
    """The page CtDraw saved in turns unit ``unit``, opened in place of FxMain's ``page``
    in the fx_install copy (so its libraries resolve); the raw bytes too."""
    from ctgedit.editing import EditableDocument

    data = fxinstall.oracle_bytes("turns", unit, "page.ctg")
    path = os.path.join(fx_install.project("FxMain"), page + ".ctg")
    with open(path, "wb") as fh:
        fh.write(data)
    return EditableDocument.open(path), path, data


def _one(doc, name):
    found = doc.find_objects_by_name(name)
    assert len(found) == 1, (name, found)
    return found[0]


# ------------------------------------------------------------- slot parsing
@pytest.mark.golden
def test_ctdraw_rotated_symbol_slot1(fx_install):
    """CtDraw's 90deg CW turn of a linked symbol at its native size (FxSymRotate): slot1
    mode 0x15 (0,1,-1,0,...) and the reader exposes the orthogonal part."""
    from ctgedit.format import geometry as G
    from ctgedit.io.reader import read_page

    doc, path, _data = _oracle_doc(fx_install, "T14_symbol_turn", "FxMotion")
    i = _one(doc, "FxSymRotate")
    po = doc.parsed.page.objects[i]
    lt = G.get_local_transform(po)
    assert lt is not None
    mode, (a, b, c, d, h, k) = lt
    assert mode == 0x15
    assert (round(a), round(b), round(c), round(d)) == (0, 1, -1, 0)
    page = read_page(path)
    assert page.objects[i].raw.get("_local_xform") is not None


@pytest.mark.golden
def test_ctdraw_rotated_text_orient(fx_install):
    """CtDraw's one turn of FxTk1: orient=3, one CW quarter turn, and no other text of
    FxTextKinds turned."""
    from ctgedit.io.reader import read_page

    doc, path, _data = _oracle_doc(fx_install, "T23_text_turn", "FxTextKinds")
    i = _one(doc, "FxTk1")
    page = read_page(path)
    rotated = [j for j, o in enumerate(page.objects) if o.raw.get("_text_turns")]
    assert rotated == [i], rotated
    assert page.objects[i].raw["_text_turns"] == 1


@pytest.mark.golden
def test_ctdraw_turned_pages_round_trip(fx_install):
    """The CtDraw-saved pages with a turned and a mirrored text parse and our writer
    round-trips them byte-exactly (transform slots included)."""
    from ctgedit.format.page import parse_ctg
    from ctgedit.format.writer import serialize_ctg
    from ctgedit.format import geometry as G

    doc, path, data = _oracle_doc(fx_install, "T23_text_turn", "FxTextKinds")
    p = parse_ctg(path)
    assert serialize_ctg(p.container, p.page, p.attributes) == data
    # FxTk1 turned once: orient 3
    tail = G.parse_text_tail(p.page.objects[_one(doc, "FxTk1")])
    assert tail is not None and tail["orient"] == 3
    # mirrored text: slots mode 0x03 with A=-1
    doc, path, data = _oracle_doc(fx_install, "T09_text_mirror_move", "FxArrange")
    p = parse_ctg(path)
    assert serialize_ctg(p.container, p.page, p.attributes) == data
    lt = G.get_local_transform(p.page.objects[_one(doc, "FxLoneText")])
    assert lt is not None and lt[0] == 0x03 and lt[1][0] == -1.0


# ------------------------------------------------------------- write path
def test_rotate_text_sets_orient_and_bakes_rect(fx_page, tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G

    doc = EditableDocument.open(fx_page("FxShapes"))
    i = doc.create_object("text", (100, 100, 180, 120), None, text="Rot")
    g0 = doc.page.objects[i].geometry
    w0, h0 = g0.extent_x, g0.extent_y  # the MEASURED box (engine rule)
    doc.rotate_objects([i], clockwise=True)
    po = doc.parsed.page.objects[i]
    tail = G.parse_text_tail(po)
    assert tail is not None and tail["orient"] == 3
    g = doc.page.objects[i].geometry
    # W/H swap (font kept); the box is the engine's re-measure of the rotated
    # font, which GDI hints a pixel or two differently
    from ctgedit.format import textmetrics

    gh = G.parse_geom_header(po.body_raw)
    assert tuple(gh.rect1) == tuple(textmetrics.text_extent(po))
    assert abs(g.extent_x - h0) <= 3 and abs(g.extent_y - w0) <= 3
    # rotating 3 more times returns to orient 1
    for _ in range(3):
        doc.rotate_objects([i], clockwise=True)
    assert G.parse_text_tail(doc.parsed.page.objects[i])["orient"] == 1
    # undo restores
    doc.undo()
    assert G.parse_text_tail(doc.parsed.page.objects[i])["orient"] == 4
    # save survives a reparse
    out = str(tmp_path / "rot.ctg")
    doc.save(out)
    doc2 = EditableDocument.open(out)
    assert G.parse_text_tail(doc2.parsed.page.objects[i]) is not None


def test_mirror_text_writes_flip_matrix(fx_page):
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G

    doc = EditableDocument.open(fx_page("FxShapes"))
    i = doc.create_object("text", (100, 100, 180, 120), None, text="Mir")
    doc.mirror_objects([i], horizontal=True)
    lt = G.get_local_transform(doc.parsed.page.objects[i])
    assert lt is not None
    mode, m = lt
    assert mode == 0x03 and m[0] == -1.0 and m[3] == 1.0
    assert doc.page.objects[i].raw.get("_local_xform") == (-1.0, 0.0, 0.0, 1.0)


def test_rotate_symbol_composes_slot1(fx_page):
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G

    doc = EditableDocument.open(fx_page("FxShapes"))
    i = doc.create_object("symbol", (100, 100, 164, 164), None, symbol=("fxsym", "fx_box", "ctl"))
    doc.rotate_objects([i], clockwise=True)
    lt = G.get_local_transform(doc.parsed.page.objects[i])
    assert lt is not None
    # Local carries the placement SCALE (box / native element) composed with
    # the quarter turn: (0, +s, -s', 0) -- only the signs/zeros are fixed
    mode, (a, b, c, d, _h, _k) = lt
    assert abs(a) < 1e-9 and abs(d) < 1e-9 and b > 0 and c < 0
    assert mode & 0x04 and (mode >> 4) == 1
    # the rotated footprint stays on the rotation centre (engine re-derives rect1)
    l, t, r, b_ = G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1
    assert abs((l + r) / 2 - 132) <= 1 and abs((t + b_) / 2 - 132) <= 1
    # a second rotation -> 180
    doc.rotate_objects([i], clockwise=True)
    _m2, (a, b, c, d, _h, _k) = G.get_local_transform(doc.parsed.page.objects[i])
    assert a < 0 and d < 0 and abs(b) < 1e-9 and abs(c) < 1e-9


def test_rotate_bitmap_transforms_pixels(fx_page):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxShapes"))
    # 2 wide x 4 tall: top row red,green; below blue
    red = bytes([255, 0, 0, 255])
    green = bytes([0, 255, 0, 255])
    blue = bytes([0, 0, 255, 255])
    rgba = red + green + blue * 6
    i = doc.add_bitmap_object((10, 10, 12, 14), rgba, 2, 4)
    doc.rotate_objects([i], clockwise=True)
    got = doc.page.objects[i].raw.get("_embedded_bmp")
    assert got is not None
    w, h, px, _a = got
    assert (w, h) == (4, 2)
    # CW: old top-left (red) lands at the new top-RIGHT
    assert px[3 * 4 : 4 * 4] == red
    assert px[0:4] == blue
    g = doc.page.objects[i].geometry
    assert (g.extent_x, g.extent_y) == (4, 2)
