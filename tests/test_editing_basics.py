"""Geometry, the object factory and EditableDocument basics."""

import pytest

from ctgedit.editing import EditableDocument
from ctgedit.format import factory, geometry as G, writer
from ctgedit.format.page import parse_ctg
from ctgedit.io.reader import read_page

#: the page the edits run on: FxMain's FxShapes in the fake install (one of each primitive,
#: FxLine and FxRect first, all top level); its project includes FxLib (fxsym.ctl)
PAGE = "FxShapes"


@pytest.fixture()
def page_path(fx_page):
    return fx_page(PAGE)


@pytest.fixture()
def doc(page_path):
    return EditableDocument.open(page_path)


def _named(doc, name):
    (i,) = doc.find_objects_by_name(name)
    return i


# --------------------------------------------------------------------------- factory
ALL_KINDS = [
    ("line", dict(nodes=[(50, 50), (200, 120)], colour=0xFF0000)),
    ("rectangle", dict(rect=(220, 50, 320, 120), colour=0x00FF00)),
    ("rectangle_outline", dict(rect=(340, 50, 440, 120), colour=0x4080FF)),
    ("ellipse", dict(rect=(50, 150, 150, 230), colour=0xFFA000)),
    ("ellipse_outline", dict(rect=(170, 150, 270, 230), colour=0x00FFFF)),
    ("polygon", dict(nodes=[(300, 150), (400, 160), (380, 230), (310, 220)], colour=0xA040FF)),
    ("pipe", dict(nodes=[(50, 260), (200, 260), (200, 320)], colour=0x808080)),
    ("text", dict(rect=(220, 260, 380, 280), text="Hello", colour=0xFFFFFF)),
    ("button", dict(rect=(400, 260, 500, 300), text="Press")),
    ("symbol", dict(rect=(50, 340, 114, 404), symbol=("fxsym", "fx_box", "ctl"))),
]


def test_factory_kinds_available():
    kinds = factory.available_kinds()
    for k in ("line", "rectangle", "ellipse", "polygon", "pipe", "text", "button", "symbol"):
        assert k in kinds


@pytest.mark.parametrize("kind,kw", ALL_KINDS)
def test_factory_create_serialize_reread(tmp_path, page_path, kind, kw):
    parsed = parse_ctg(page_path)
    base = len(parsed.page.objects)
    if kind == "symbol":
        # a placed symbol's placement lives in its Local transform, derived
        # from the resolved element (origin + native rect)
        from ctgedit.format import placement
        from ctgedit.format.symbols import SymbolResolver

        info = placement.element_info(SymbolResolver.for_page(page_path), *kw["symbol"])
        assert info is not None
        kw = dict(kw, link_info=info)
    parsed.page.objects.append(factory.create_object(kind, **kw))
    parsed.page.object_count = len(parsed.page.objects)
    out = tmp_path / ("f_%s.ctg" % kind)
    out.write_bytes(writer.serialize_ctg(parsed.container, parsed.page, parsed.attributes))

    rp = parse_ctg(str(out))
    assert len(rp.page.objects) == base + 1
    page = read_page(str(out))
    obj = page.objects[base]
    if kind == "symbol":
        # the engine derives rect1 = trunc(bbox(slot3 x N)) -- it lands on the box
        l, t, r, b = kw["rect"]
        gh = G.parse_geom_header(rp.page.objects[base].body_raw)
        assert all(abs(x - y) <= 1 for x, y in zip(gh.rect1, (l, t, r, b)))
        assert G.get_object_nodes(rp.page.objects[base]) in (None, [])
    if kind == "text":
        # a text's box is MEASURED from its string + font (the engine re-measures
        # on load); the font scales to the requested height, left edge kept
        from ctgedit.format import textmetrics

        gh = G.parse_geom_header(rp.page.objects[base].body_raw)
        want = textmetrics.text_extent(rp.page.objects[base])
        if want is not None:
            assert tuple(gh.rect1) == tuple(want) and gh.rect1[0] == kw["rect"][0]
    if "rect" in kw and kind not in ("symbol", "text"):
        l, t, r, b = kw["rect"]
        assert (obj.geometry.x, obj.geometry.y) == (l, t)
        assert (obj.geometry.extent_x, obj.geometry.extent_y) == (r - l, b - t)
    if "nodes" in kw:
        got = G.get_object_nodes(rp.page.objects[base])
        assert got == [tuple(p) for p in kw["nodes"]]
    if kw.get("text") and kind == "text":
        assert kw["text"] in obj.style.text
    if kw.get("colour") is not None and kind in ("rectangle", "ellipse"):
        assert obj.style.filled and obj.style.fill_colour == kw["colour"]
    if kind.endswith("_outline"):
        assert not obj.style.filled


def test_factory_strips_editor_refs():
    po = factory.create_object("rectangle", rect=(0, 0, 50, 50), colour=0x123456)
    keys = writer.object_property_keys(po)
    assert "!*PropSheet.Focus" not in keys
    assert not any(k.startswith("!*#") for k in keys)


# --------------------------------------------------------------------------- geometry
def test_translate_moves_nodes_and_rects():
    po = factory.create_object("polygon", nodes=[(10, 10), (60, 10), (35, 50)])
    G.translate_object(po, 100, 200)
    assert G.get_object_nodes(po) == [(110, 210), (160, 210), (135, 250)]
    gh = G.parse_geom_header(po.body_raw)
    assert (gh.rect1[0], gh.rect1[1]) == (110, 210)


def test_resize_scales_nodes():
    po = factory.create_object("polygon", nodes=[(0, 0), (100, 0), (50, 100)])
    G.resize_object(po, (0, 0, 50, 50))
    assert G.get_object_nodes(po) == [(0, 0), (50, 0), (25, 50)]


def test_line_endpoints_preserve_orientation():
    """A free line keeps its DRAW-ORDER endpoints in the body tail (the line
    class's section); rect1 is their normalised extent and the
    anchor is p1: the shape the engine itself writes."""
    import struct

    po = factory.create_object("line", nodes=[(200, 10), (10, 150)])
    assert G.get_object_nodes(po) == [(200, 10), (10, 150)]
    gh = G.parse_geom_header(po.body_raw)
    assert gh.rect1 == (10, 10, 200, 150)
    assert gh.anchor == (200, 10)
    assert struct.unpack_from("<4h", po.body_raw, len(po.body_raw) - 8) == (200, 10, 10, 150)


# --------------------------------------------------------------------------- editing
def test_move_undo_redo(doc):
    i = _named(doc, "FxRect")
    before = G.parse_geom_header(doc.objects[i].body_raw).rect1
    doc.move_object(i, 7, 9)
    moved = G.parse_geom_header(doc.objects[i].body_raw).rect1
    assert moved == (before[0] + 7, before[1] + 9, before[2] + 7, before[3] + 9)
    doc.undo()
    assert G.parse_geom_header(doc.objects[i].body_raw).rect1 == before
    assert doc.can_redo()
    doc.redo()
    assert G.parse_geom_header(doc.objects[i].body_raw).rect1 == moved


def test_create_and_undo(doc):
    n = len(doc.objects)
    idx = doc.create_object("rectangle", rect=(10, 10, 60, 60), colour=0x336699)
    assert idx == n and len(doc.objects) == n + 1
    doc.undo()
    assert len(doc.objects) == n
    doc.redo()
    assert len(doc.objects) == n + 1


def test_zorder_front_back(doc):
    doc.create_object("rectangle", rect=(0, 0, 10, 10), colour=1)
    last = len(doc.objects) - 1
    new = doc.reorder([last], "back")
    assert new == [0]
    new = doc.reorder([0], "front")
    assert new == [len(doc.objects) - 1]


def test_clipboard_roundtrip(doc):
    n = len(doc.objects)
    first = _named(doc, "FxLine")
    payload = doc.copy_payload([first, _named(doc, "FxRect")])
    out = doc.paste_payload(payload, offset=5)
    assert len(out) == 2 and len(doc.objects) == n + 2
    src = doc.objects[first]
    dup = doc.objects[out[0]]
    assert dup.type_code == src.type_code
    r_src = G.parse_geom_header(src.body_raw).rect1
    r_dup = G.parse_geom_header(dup.body_raw).rect1
    assert r_dup == (r_src[0] + 5, r_src[1] + 5, r_src[2] + 5, r_src[3] + 5)


def test_align_left(doc):
    a = doc.create_object("rectangle", rect=(100, 10, 150, 40), colour=1)
    b = doc.create_object("rectangle", rect=(200, 60, 280, 90), colour=2)
    doc.align([a, b], "left")
    assert doc._obj_bbox(a)[0] == doc._obj_bbox(b)[0] == 100
    doc.undo()
    assert doc._obj_bbox(b)[0] == 200


def test_distribute_h(doc):
    a = doc.create_object("rectangle", rect=(0, 0, 10, 10), colour=1)
    b = doc.create_object("rectangle", rect=(13, 0, 23, 10), colour=2)
    c = doc.create_object("rectangle", rect=(100, 0, 110, 10), colour=3)
    doc.distribute([a, b, c], "h")
    assert doc._obj_bbox(b)[0] == 50


#: three top-level genies of FxMain's FxGenies with member subtrees, unevenly spaced:
#: FxPump1 and FxPump2 (3 members, left edges 20 and 120) and FxValue (2 members, at 400)
GENIES = ("FxPump1", "FxPump2", "FxValue")


def _top_genies(doc, names=GENIES):
    out = [_named(doc, n) for n in names]
    for i in out:
        o = doc.page.objects[i]
        assert o.members and doc.root_of(i) == i
        assert o.kind.name in ("GENIE", "COMPOSITE_GENIE")
    return out


def _rects(doc, indices):
    return {i: G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1 for i in indices}


def test_align_moves_whole_genie_subtree(fx_page):
    """Aligning genies moves each one's FULL subtree as a unit: every member shifts by the
    unit delta, edges line up, undo restores."""
    doc = EditableDocument.open(fx_page("FxGenies"))
    genies = _top_genies(doc)
    assert len(genies) == 3
    subs = {r: doc.subtree(r) for r in genies}
    before = {r: _rects(doc, subs[r]) for r in genies}
    lefts0 = {r: doc._unit_bbox(subs[r])[0] for r in genies}
    target = min(lefts0.values())

    doc.align(genies, "left")

    for r in genies:
        assert doc._unit_bbox(doc.subtree(r))[0] == target
        dx = target - lefts0[r]
        now = _rects(doc, subs[r])
        for i in subs[r]:
            b0, b1 = before[r][i], now[i]
            assert (b1[0] - b0[0], b1[2] - b0[2]) == (dx, dx), i
            assert (b1[1], b1[3]) == (b0[1], b0[3]), i  # no vertical drift
    doc.undo()
    for r in genies:
        assert doc._unit_bbox(doc.subtree(r))[0] == lefts0[r]


def test_distribute_moves_whole_genie_subtree(fx_page):
    doc = EditableDocument.open(fx_page("FxGenies"))
    genies = _top_genies(doc)
    assert len(genies) == 3
    subs = {r: doc.subtree(r) for r in genies}
    before = {r: _rects(doc, subs[r]) for r in genies}

    doc.distribute(genies, "h")

    # each genie subtree moved rigidly by its unit's dx
    for r in genies:
        now = _rects(doc, subs[r])
        dxs = {now[i][0] - before[r][i][0] for i in subs[r]}
        assert len(dxs) == 1  # one delta for all
    # middle unit's CENTRE is evenly spaced between the outer two (CtDraw's
    # Even spaces midpoints)
    mids = sorted(sum(doc._unit_bbox(doc.subtree(r))[0::2]) / 2 for r in genies)
    assert abs(mids[1] - (mids[0] + mids[2]) / 2) <= 1
    # (the fixture's three are not evenly spaced, so the middle one moved)
    mid = genies[1]
    moved = {r[0] for r in _rects(doc, subs[mid]).values()}
    assert moved != {b[0] for b in before[mid].values()}


def test_set_colour_and_text(doc):
    r = doc.create_object("rectangle", rect=(0, 0, 40, 40), colour=0x111111)
    assert doc.set_colour(r, 0xAA22CC)
    assert doc.page.objects[r].style.fill_colour == 0xAA22CC
    t = doc.create_object("text", rect=(0, 50, 100, 70), text="abc")
    assert doc.set_text(t, "Changed!")
    assert "Changed!" in doc.page.objects[t].style.text


def test_save_reload_keeps_new_objects(doc, tmp_path):
    doc.create_object("pipe", nodes=[(10, 10), (80, 10), (80, 80)], colour=0x445566)
    out = tmp_path / "saved.ctg"
    doc.save(str(out))
    doc2 = EditableDocument.open(str(out))
    assert len(doc2.objects) == len(doc.objects)
    assert G.get_object_nodes(doc2.objects[-1]) == [(10, 10), (80, 10), (80, 80)]


def test_page_background(doc):
    doc.set_page_background(0x00224466)
    assert doc.parsed.attributes.bg_color0 == 0x00224466
    doc.undo()
    assert doc.parsed.attributes.bg_color0 != 0x00224466


def test_an_edit_past_the_page_coordinates_changes_nothing(doc):
    """Move, scale, resize, duplicate and create past -32768 .. 32767: refused with
    TransformError (said, not a crash), the records and the model as they were, no undo
    step; a move of two where only the second does not fit leaves the first where it was."""
    from ctgedit.editing_base import TransformError

    a = doc.create_object("rectangle", rect=(100, 100, 200, 150))
    b = doc.create_object("rectangle", rect=(32000, 100, 32700, 150))

    def state():
        return (
            [bytes(o.body_raw) for o in doc.parsed.page.objects],
            len(doc._undo),
            doc.page.objects[a].geometry.x,
        )

    before = state()
    for edit in (
        lambda: doc.move_objects([a, b], 3000, 0),
        lambda: doc.adjust_position_and_size([a, b], 0, 0, 3.0, 1.0),
        lambda: doc.set_object_rect(b, (32000, 100, 40000, 150)),
        lambda: doc.duplicate_objects([b], offset=2000),
        lambda: doc.create_object("rectangle", rect=(40000, 0, 40100, 50)),
    ):
        with pytest.raises(TransformError):
            edit()
        assert state() == before


def test_a_batch_that_fails_part_way_takes_back_its_steps(doc):
    a = doc.create_object("rectangle", rect=(100, 100, 200, 150))
    raw = bytes(doc.parsed.page.objects[a].body_raw)
    steps = len(doc._undo)
    with pytest.raises(RuntimeError):
        with doc.batch("two moves"):
            doc.move_objects([a], 10, 0)
            raise RuntimeError("the second edit failed")
    assert bytes(doc.parsed.page.objects[a].body_raw) == raw and len(doc._undo) == steps
    assert doc.page.objects[a].geometry.x == 100
