"""Arrange: z-order, rotate and mirror."""

import os
import struct

import fxinstall
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from ctgedit.format import geometry as G, placement as PL  # noqa: E402
from qtkit import app as _app


@pytest.fixture()
def arrange(fx_page):
    """The path of FxMain's FxArrange page in the test's copy of the fake install."""
    return fx_page("FxArrange")


def _open(path):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(path)


def _named(doc, name):
    """The record index of the one object named ``name``."""
    hits = doc.find_objects_by_name(name)
    assert len(hits) == 1, (name, hits)
    return hits[0]


def _record_name(po):
    """A record's stored name from the records alone (as EditableDocument._stored_name
    reads it), for an oracle page parsed without a document."""
    for bag in po.bags:
        for k, v in bag.pairs:
            if k.upper() == "__AN_NAME__":
                return v
    hdr = G.parse_geom_header(po.body_raw)
    return hdr.name if hdr is not None else ""


def _oracle_page(step, unit):
    """CtDraw's saved page of an oracle unit, parsed: (records, {name: index})."""
    from ctgedit.format.page import parse_ctg

    objs = parse_ctg(fxinstall.oracle(step, unit, "page.ctg")).page.objects
    names = {}
    for i, po in enumerate(objs):
        n = _record_name(po)
        if n:
            names.setdefault(n, i)
    return objs, names


def _snap(doc):
    return [(bytes(po.body_raw), po.subtype_size, po.class_flag) for po in doc.parsed.page.objects]


def _box(po):
    return G.norm_rect(G.parse_geom_header(po.body_raw).rect1)


# ------------------------------------------------------------ z-order steps
def _chains(doc):
    """{record identity: tuple of ancestor identities} from the forest."""
    objs = doc.parsed.page.objects
    parents = PL.forest_parents(objs)
    out = {}
    for i, po in enumerate(objs):
        chain, p = [], parents[i]
        while p is not None:
            chain.append(id(objs[p]))
            p = parents[p]
        out[id(po)] = tuple(chain)
    return out


@pytest.mark.parametrize(
    "name,where",
    [
        ("FxFreeBefore", "forward"),  # a leaf in front of a 97-record group
        ("FxFreeAfter", "backward"),  # a leaf behind that group
        ("FxKeyText", "forward"),  # a text with its keyboard record past a leaf
        ("FxLoneText", "backward"),  # a leaf behind that two-record unit
        ("FxBigGroup", "forward"),  # a whole group past a leaf
        ("FxBigGroup", "backward"),  # a whole group behind a leaf
        ("+leaf", "forward"),  # a leaf past a leaf (two rectangles made here)
        ("+leaf", "backward"),
    ],
)
def test_one_step_z_order_keeps_every_parent_chain(arrange, name, where):
    doc = _open(arrange)
    if name == "+leaf":
        a = doc.create_object("rectangle", rect=(900, 600, 940, 640))
        b = doc.create_object("rectangle", rect=(960, 600, 1000, 640))
        root = a if where == "forward" else b
    else:
        root = _named(doc, name)
    objs = doc.parsed.page.objects
    before = _chains(doc)
    moving = objs[root]
    n = len(objs)
    # exactly one step: the moving root trades places with its neighbouring
    # root subtree, every other root keeps its order
    heads = [objs[r] for r in doc.page.roots()]
    k0 = heads.index(moving)
    j0 = k0 + 1 if where == "forward" else k0 - 1
    want = list(heads)
    want[k0], want[j0] = want[j0], want[k0]
    undo_ids = [id(po) for po in objs]
    new = doc.reorder([root], where)
    assert [doc.parsed.page.objects[r] for r in doc.page.roots()] == want
    after = _chains(doc)
    assert len(doc.parsed.page.objects) == n
    # the forest still parses: every record is somebody's child or a root
    assert sum(len(doc.subtree(r)) for r in doc.page.roots()) == n
    assert after == before  # nobody was absorbed into / ejected from a group
    k = next(i for i, po in enumerate(doc.parsed.page.objects) if po is moving)
    assert new == [k]
    step = len(doc.subtree(k))
    assert (k > root) == (where == "forward") and k != root and step >= 1
    doc.undo()
    assert [id(po) for po in doc.parsed.page.objects] == undo_ids


def test_z_order_step_moves_non_contiguous_selection_block_wise(arrange):
    doc = _open(arrange)
    objs = list(doc.parsed.page.objects)
    before = _chains(doc)
    ia, ib = _named(doc, "FxFreeBefore"), _named(doc, "FxKeyText")
    a, b = objs[ia], objs[ib]
    big, lone = objs[_named(doc, "FxBigGroup")], objs[_named(doc, "FxLoneText")]
    doc.reorder([ia, ib], "forward")
    order = doc.parsed.page.objects
    roots = [order[r] for r in doc.page.roots()]
    # FxFreeBefore passed the 97-record FxBigGroup, FxKeyText passed FxLoneText;
    # nothing else moved
    assert roots.index(a) == roots.index(big) + 1
    assert roots.index(b) == roots.index(lone) + 1
    assert _chains(doc) == before
    assert sum(len(doc.subtree(r)) for r in doc.page.roots()) == len(objs)


def test_send_backward_stops_at_the_page_template(fx_page):
    """FxTplLinked: the fx_style normal@XGA template and FxOwnRect above it."""
    doc = _open(fx_page("FxTplLinked"))
    tpl, _ref = doc.template_reference()
    first_above = next(r for r in doc.page.roots() if r > tpl)
    snap = _snap(doc)
    undo_depth = len(doc._undo)
    assert doc.reorder([first_above], "backward") == [first_above]
    assert _snap(doc) == snap and len(doc._undo) == undo_depth


# ------------------------------------------------------------ rotate / mirror
@pytest.mark.parametrize("name", ["FxBigGroup", "FxCarrier"])  # a nested plain group, a genie
def test_rotate_undo_is_byte_identical_and_four_turns_are_identity(arrange, name, monkeypatch):
    """(four turns are exact with Settings > Editing > reversible turns; the Graphics
    Builder's own truncation drifts a pixel, as ctgedit does by default)"""
    from ctgedit.doc.geometry import GeometryEdits

    monkeypatch.setattr(GeometryEdits, "REVERSIBLE_TURNS", True)
    doc = _open(arrange)
    root = _named(doc, name)
    doc._rerender_page = lambda: None  # structure is unchanged; keep it fast
    s0 = _snap(doc)
    doc.rotate_objects([root], clockwise=True)
    s1 = _snap(doc)
    assert s1 != s0
    assert doc._container_fits(root)  # the container is the union of its children
    doc.undo()
    assert _snap(doc) == s0
    doc.redo()
    assert _snap(doc) == s1
    doc.undo()
    for _ in range(4):
        doc.rotate_objects([root], clockwise=True)
    assert _snap(doc) == s0
    for _ in range(2):
        doc.rotate_objects([root], clockwise=False)
    for _ in range(2):
        doc.rotate_objects([root], clockwise=True)
    assert _snap(doc) == s0


def test_mirror_twice_of_a_plain_group_is_identity(arrange):
    doc = _open(arrange)
    root = _named(doc, "FxBigGroup")
    doc._rerender_page = lambda: None
    s0 = _snap(doc)
    doc.mirror_objects([root], horizontal=True)
    assert doc._container_fits(root) and _snap(doc) != s0
    doc.mirror_objects([root], horizontal=True)
    doc.mirror_objects([root], horizontal=False)
    doc.mirror_objects([root], horizontal=False)
    assert _snap(doc) == s0


def test_rotated_genie_takes_the_turn_on_its_local(arrange):
    """FxCarrier (fx_plain placed at (600, 20): a carrier, Local = translate): its Local
    composes the turn the engine's way (mode 0x15, quarter nibble), members keep
    identity Locals and re-derive their Total from it."""
    doc = _open(arrange)
    root = _named(doc, "FxCarrier")
    objs = doc.parsed.page.objects
    assert PL.read_slots(objs[root].body_raw)[0][0][0] == 0x01  # translate before
    locals0 = {j: PL.read_slots(objs[j].body_raw)[0][0][:2] for j in doc.subtree(root)[1:]}
    assert locals0
    doc.rotate_objects([root], clockwise=True)
    (m1, L, _o, _n), _s2, s3 = PL.read_slots(objs[root].body_raw)[0]
    assert m1 == 0x15 and (L[0], L[1], L[2], L[3]) == (-0.0, 1.0, -1.0, 0.0)
    for j, loc in locals0.items():
        (mj, Lj, _o, _n), _s2j, s3j = PL.read_slots(objs[j].body_raw)[0]
        assert (mj, Lj) == loc
        assert s3j[0] & 0x04  # the member's Total carries the turn


def _point_state(doc, root):
    objs = doc.parsed.page.objects
    pts = []
    for j in doc.subtree(root)[1:]:
        gh = G.parse_geom_header(objs[j].body_raw)
        pts.append((objs[j].type_code, tuple(gh.anchor), G.norm_rect(gh.rect1)))
    (_m, L, _o, _n), _s2, _s3 = PL.read_slots(objs[root].body_raw)[0]
    return pts, (L[4], L[5])


@pytest.mark.parametrize(
    "turns,points,local_hk",
    [
        (1, [(0x1007, (621, 80)), (0x100D, (621, 120))], (621.0, 100.0)),
        (2, [(0x1007, (642, 100)), (0x100D, (602, 100))], (622.0, 100.0)),
    ],
)
def test_point_unit_turns_about_its_extent_centre(arrange, turns, points, local_hk):
    """A genie of V3/V4 point records turns about its extent's centre."""
    doc = _open(arrange)
    root = _named(doc, "FxPointGenie")
    for _ in range(turns):
        doc.rotate_objects([root], clockwise=True)
    pts, hk = _point_state(doc, root)
    assert [(tc, a) for tc, a, _r in pts] == points
    for _tc, a, r in pts:
        assert r == (a[0], a[1], a[0] + 1, a[1] + 1)
    assert hk == local_hk


@pytest.mark.golden
@pytest.mark.parametrize("unit,turns", [("T01_point_turn", 1), ("T02_point_two_turns", 2)])
def test_point_unit_turns_like_ctdraw(arrange, unit, turns):
    """ctgedit's turn of FxPointGenie gives CtDraw's anchors, extents and Locals
    (the oracle's PositionRotate of the same genie, then PageSave)."""
    objs, names = _oracle_page("turns", unit)
    doc = _open(arrange)
    root = _named(doc, "FxPointGenie")
    for _ in range(turns):
        doc.rotate_objects([root], clockwise=True)
    ours = doc.parsed.page.objects
    theirs = names["FxPointGenie"]
    for k, j in enumerate(doc.subtree(root)):
        a, b = ours[j], objs[theirs + k]
        assert a.type_code == b.type_code
        ga, gb = G.parse_geom_header(a.body_raw), G.parse_geom_header(b.body_raw)
        assert (tuple(ga.anchor), G.norm_rect(ga.rect1)) == (tuple(gb.anchor), G.norm_rect(gb.rect1)), k
        assert PL.read_slots(a.body_raw)[0][0][:2] == PL.read_slots(b.body_raw)[0][0][:2], k


def _text_state(po):
    gh = G.parse_geom_header(po.body_raw)
    tail = G.parse_text_tail(po)
    (s1, _s2, s3), _e = PL.read_slots(po.body_raw)
    return (
        tuple(gh.anchor),
        G.norm_rect(gh.rect1),
        tuple(tail["echo"]) if tail else None,
        (s1[0], tuple(s1[1])),
        (s3[0], tuple(s3[1])),
    )


def test_mirrored_text_keeps_a_local_echo_like_ctdraw(arrange):
    """A mirrored text keeps its flip as a matrix, as CtDraw does."""
    doc = _open(arrange)
    doc._rerender_page = lambda: None
    objs = doc.parsed.page.objects
    k, lone = _named(doc, "FxKeyText"), _named(doc, "FxLoneText")
    assert _text_state(objs[k])[2] == (600, 193)  # the echo before: the anchor
    doc.mirror_objects([k], horizontal=True)
    M = (0x3, (-1.0, 0.0, 0.0, 1.0, 1229.0, 0.0))
    assert _text_state(objs[k]) == ((629, 193), (600, 180, 629, 196), (600, 193), M, M)
    kb = objs[doc.subtree(k)[1]]  # the text's keyboard record
    assert kb.type_code == 0x100B
    (s1, s2, s3), _e = PL.read_slots(kb.body_raw)
    assert (s1[0], s2[0], (s3[0], tuple(s3[1]))) == (0, 0, M)
    assert tuple(G.parse_geom_header(kb.body_raw).anchor) == (629, 193)
    assert struct.unpack("<2h", kb.body_raw[-4:]) == (600, 193)  # class point (600, 193)
    # a move keeps the echo local (the Total takes the delta)
    doc.move_objects([k], -5, 4)
    M = (0x3, (-1.0, 0.0, 0.0, 1.0, 1224.0, 4.0))
    assert _text_state(objs[k]) == ((624, 197), (595, 184, 624, 200), (600, 193), M, M)
    doc.mirror_objects([lone], horizontal=True)  # about its own centre
    doc.move_objects([lone], -5, 4)
    M = (0x3, (-1.0, 0.0, 0.0, 1.0, 1470.0, 4.0))
    assert _text_state(objs[lone]) == ((750, 197), (715, 184, 750, 200), (720, 193), M, M)


@pytest.mark.golden
def test_mirrored_text_and_move_match_ctdraw(arrange):
    """FxLoneText mirrored horizontally, then moved (CtDraw: PositionMirrorHorizontal +
    PositionAt(200, 200), turns:T09): ctgedit's mirror, moved onto CtDraw's resulting
    anchor, gives CtDraw's box, echo and slots."""
    objs, names = _oracle_page("turns", "T09_text_mirror_move")
    want = _text_state(objs[names["FxLoneText"]])
    doc = _open(arrange)
    lone = _named(doc, "FxLoneText")
    doc.mirror_objects([lone], horizontal=True)
    got = _text_state(doc.parsed.page.objects[lone])
    doc.move_objects([lone], want[0][0] - got[0][0], want[0][1] - got[0][1])
    assert _text_state(doc.parsed.page.objects[lone]) == want


def _set_states(doc, root):
    """The state roots of the symbol set ``root`` and each one's records (root first)."""
    states = list(doc.page.objects[root].members)
    return states, {s: doc.subtree(s) for s in states}


def test_linked_symbols_nested_in_a_symbol_set_turn_with_it(arrange):
    """Linked symbols in a symbol set turn with it."""
    doc = _open(arrange)
    root = _named(doc, "FxTripleSet")
    objs = doc.parsed.page.objects
    sub = doc.subtree(root)
    states, forests = _set_states(doc, root)
    assert len(states) == 3 and len(sub) == 7
    for s in states:
        assert [(objs[j].type_code, objs[j].subtype_size) for j in forests[s]] == [(0x08, 0x19), (0x11, 0x18)]
    l, t, r, b = _box(objs[root])
    cx, cy = (l + r) / 2.0, (t + b) / 2.0
    locals0 = {j: PL.read_slots(objs[j].body_raw)[0][0][:2] for j in sub[1:]}
    doc.rotate_objects([root], clockwise=True)
    want = (int(cx - (b - cy)), int(cy + (l - cx)), int(cx - (t - cy)), int(cy + (r - cx)))
    for j in sub[1:]:
        assert PL.read_slots(objs[j].body_raw)[0][0][:2] == locals0[j]
        assert all(abs(p - q) <= 1 for p, q in zip(_box(objs[j]), want)), (j, _box(objs[j]))
    assert _box(objs[root]) == doc._union_rects(states)[0] == doc._union_rects(sub[1:])[0]


@pytest.mark.golden
def test_symbol_set_of_forest_states_turns_like_ctdraw(arrange):
    """CtDraw's PositionRotate of FxTripleSet, then PageSave (turns:T13): ctgedit's turn of
    the set and its three instance-forest states gives CtDraw's records byte for byte."""
    from ctgedit.format.page import parse_ctg

    theirs = parse_ctg(fxinstall.oracle("turns", "T13_symbol_set_turn", "page.ctg")).page.objects
    names = {_record_name(po): i for i, po in enumerate(theirs) if _record_name(po)}
    doc = _open(arrange)
    root = _named(doc, "FxTripleSet")
    doc.rotate_objects([root], clockwise=True)
    ours = doc.parsed.page.objects
    sub = doc.subtree(root)
    got = [
        (ours[j].type_code, ours[j].subtype_size, ours[j].class_flag, bytes(ours[j].body_raw)) for j in sub
    ]
    k = names["FxTripleSet"]
    want = [
        (po.type_code, po.subtype_size, po.class_flag, bytes(po.body_raw)) for po in theirs[k : k + len(sub)]
    ]
    assert got == want


def _pie_quadrant(doc, i):
    """(right?, below?) of the drawn pie wedge's centre relative to its box
    centre, through the scene (the editor's own drawing)."""
    from ctgedit.render.scene import PageScene

    _app()
    sc = PageScene()
    sc.set_page(doc.page)
    wedge = sc._items[i].sceneBoundingRect().center()
    l, t, r, b = _box(doc.parsed.page.objects[i])
    return wedge.x() > (l + r) / 2.0, wedge.y() > (t + b) / 2.0


def test_ellipse_angles_are_stored_in_ctdraws_range():
    """CtDraw's PageOpen + PageSave of ctgedit pies (live): every
    angle comes back folded into [-90, 270]."""
    live = {
        0: 0,
        360: 0,
        -360: 0,
        270: 270,
        300: -60,
        -270: 90,
        -200: 160,
        -180: 180,
        180: 180,
        -90: -90,
        200: 200,
        350: -10,
        45: 45,
        -45: -45,
        -181: 179,
        181: 181,
        359: -1,
        -359: 1,
        90: 90,
    }
    assert {a: G.ctdraw_angle(a) for a in live} == live


def _pies(objs):
    out = []
    for po in objs:
        tail = G.parse_shape_tail(po) if po.type_code == 0x11 else None
        if tail and "start" in tail:
            out.append((tail["start"], tail["end"]))
    return out


@pytest.mark.golden
def test_ellipse_angle_fold_matches_ctdraws_save():
    """The pies capture (pies:FxOraclePies): a page of 19 pies whose angles are stored
    unfolded (-360 to 360), and CtDraw's save of it. ctdraw_angle gives CtDraw's
    stored angles."""
    from ctgedit.format.page import parse_ctg

    before = _pies(parse_ctg(fxinstall.oracle("pies", "FxOraclePies", "input.ctg")).page.objects)
    after = _pies(_oracle_page("pies", "FxOraclePies")[0])
    assert before and len(before) == len(after)
    assert [(G.ctdraw_angle(s), G.ctdraw_angle(e)) for s, e in before] == after


def test_pie_angles_and_gradient_turn_with_the_box(arrange):
    """Pie angles and gradient direction turn with the box."""
    doc = _open(arrange)
    e = doc.create_object("ellipse", rect=(300, 500, 400, 560), colour=0x3366CC)
    assert doc.set_ellipse(e, style=1, start=0, end=90)
    assert _pie_quadrant(doc, e) == (True, False)  # up-right
    doc.rotate_objects([e], clockwise=True)
    tail = G.parse_shape_tail(doc.parsed.page.objects[e])
    assert (tail["start"], tail["end"]) == (90, 180)  # the wedge points down
    assert _box(doc.parsed.page.objects[e]) == (320, 480, 380, 580)
    assert _pie_quadrant(doc, e) == (True, True)  # down-right
    doc.mirror_objects([e], horizontal=True)
    tail = G.parse_shape_tail(doc.parsed.page.objects[e])
    assert (tail["start"], tail["end"]) == (180, -90)  # sweep reversed
    assert _pie_quadrant(doc, e) == (False, True)  # down-left
    doc.mirror_objects([e], horizontal=False)
    tail = G.parse_shape_tail(doc.parsed.page.objects[e])
    assert (tail["start"], tail["end"]) == (270, 0)  # up-left
    assert _pie_quadrant(doc, e) == (False, False)
    for _ in range(4):
        doc.undo()
    # a full ellipse's angles turn too (CtDraw: (0, 0) -> (90, 90) on a CW
    # turn, the golden test below); four turns give them back
    f = doc.create_object("ellipse", rect=(500, 600, 560, 630), colour=0x3366CC)
    t0 = G.parse_shape_tail(doc.parsed.page.objects[f])
    doc.rotate_objects([f], clockwise=True)
    t1 = G.parse_shape_tail(doc.parsed.page.objects[f])
    assert (t1["start"], t1["end"]) == (t0["start"] + 90, t0["end"] + 90)
    assert _box(doc.parsed.page.objects[f]) == (515, 585, 545, 645)
    for _ in range(3):
        doc.rotate_objects([f], clockwise=True)
    t4 = G.parse_shape_tail(doc.parsed.page.objects[f])
    assert (t4["start"], t4["end"]) == (t0["start"], t0["end"])
    for _ in range(4):
        doc.undo()
    r = doc.create_object("rectangle", rect=(100, 500, 160, 530), colour=0x00FF00)
    assert doc.set_gradient(r, 1, 0x0000FF)  # left -> right
    doc.rotate_objects([r], clockwise=True)
    assert G.parse_shape_tail(doc.parsed.page.objects[r])["grad_mode"] == 3  # top -> bottom
    doc.mirror_objects([r], horizontal=False)
    assert G.parse_shape_tail(doc.parsed.page.objects[r])["grad_mode"] == 4


@pytest.mark.golden
def test_full_ellipse_turn_matches_ctdraw(fx_page):
    """FxShapes' FxEllipse turned once clockwise (turns:T17): ctgedit stores CtDraw's
    angles and box."""
    objs, names = _oracle_page("turns", "T17_full_ellipse_turn")
    doc = _open(fx_page("FxShapes"))
    e = _named(doc, "FxEllipse")
    doc.rotate_objects([e], clockwise=True)
    ours, theirs = doc.parsed.page.objects[e], objs[names["FxEllipse"]]
    ta, tb = G.parse_shape_tail(ours), G.parse_shape_tail(theirs)
    assert (ta["start"], ta["end"], _box(ours)) == (tb["start"], tb["end"], _box(theirs))


@pytest.mark.parametrize(
    "name",
    [
        "FxTouchFill",  # a rectangle face
        "FxTouchKeyButton",
    ],
)  # a button
def test_touch_record_stays_on_its_owners_hotspot(arrange, name):
    """An input record (Touch / Keyboard) sits on its owner's hotspot. A turned rect /
    button re-derives its hotspot from the new box, so the input record must follow it, not
    land on another corner."""
    doc = _open(arrange)
    root = _named(doc, name)
    objs = doc.parsed.page.objects
    kids = [
        j
        for j in doc.subtree(root)
        if doc.page.objects[j].parent == root and objs[j].type_code in G.INPUT_TYPE_CODES
    ]
    assert kids

    def hotspot(i):
        return tuple(G.parse_geom_header(objs[i].body_raw).anchor)

    assert all(hotspot(k) == hotspot(root) for k in kids)
    for op in ("cw", "acw", "mh", "mv"):
        if op in ("cw", "acw"):
            doc.rotate_objects([root], clockwise=op == "cw")
        else:
            doc.mirror_objects([root], horizontal=op == "mh")
        assert all(hotspot(k) == hotspot(root) for k in kids), op
        doc.undo()


def test_a_record_that_cannot_turn_aborts_the_rotate_unchanged(arrange, monkeypatch):
    from ctgedit.editing import TransformError

    doc = _open(arrange)
    doc._rerender_page = lambda: None
    root = _named(doc, "FxBigGroup")
    s0 = _snap(doc)
    depth = len(doc._undo)
    real = doc._transform_one
    k = doc.subtree(_named(doc, "FxBigInner"))[30]  # a rectangle deep in the group
    victim = doc.parsed.page.objects[k]

    def boom(po, *a, **kw):
        if po is victim:
            raise RuntimeError("unsupported record")
        return real(po, *a, **kw)

    monkeypatch.setattr(doc, "_transform_one", boom)
    with pytest.raises(TransformError) as ei:
        doc.rotate_objects([root], clockwise=True)
    assert "Object %d" % k in str(ei.value) and "Nothing was changed" in str(ei.value)
    assert _snap(doc) == s0 and len(doc._undo) == depth


# ------------------------------------------------------------ same size / ungroup
def test_make_same_size_scales_a_whole_genie(arrange):
    doc = _open(arrange)
    objs = doc.parsed.page.objects
    s0 = _snap(doc)
    genie, ref = _named(doc, "FxCarrier"), _named(doc, "FxSameSize")  # a free rectangle
    rl, rt, rr, rb = _box(objs[ref])
    doc.make_same_size([genie, ref], ref)
    l, t, r, b = _box(objs[genie])
    assert (r - l, b - t) == (rr - rl, rb - rt)
    assert doc._container_fits(genie)  # the members were scaled with it
    assert PL.read_slots(objs[genie].body_raw)[0][0][0] & 0x02  # Local scales
    doc.undo()  # one step
    assert _snap(doc) == s0


def test_ungroup_dissolves_every_selected_group_in_one_step(arrange):
    doc = _open(arrange)
    a = doc.create_object("rectangle", rect=(10, 510, 40, 540))
    b = doc.create_object("rectangle", rect=(50, 510, 80, 540))
    g1 = doc.group_objects([a, b])
    c = doc.create_object("rectangle", rect=(10, 550, 40, 580))
    d = doc.create_object("rectangle", rect=(50, 550, 80, 580))
    g2 = doc.group_objects([c, d])
    n_groups = sum(1 for r in doc.page.roots() if doc.page.objects[r].members)
    s0 = _snap(doc)
    members = doc.ungroup_objects([g1, g2])
    assert len(members) == 4
    assert sum(1 for r in doc.page.roots() if doc.page.objects[r].members) == n_groups - 2
    assert all(doc.page.objects[m].parent is None for m in members)
    doc.undo()
    assert _snap(doc) == s0


# ------------------------------------------------------------ edit atomicity
def test_failing_body_edit_restores_the_record(arrange):
    doc = _open(arrange)
    i = doc.create_object("rectangle", rect=(10, 510, 60, 540))
    po = doc.parsed.page.objects[i]
    body, flags = bytes(po.body_raw), po.subtype_size
    depth = len(doc._undo)

    def half_then_fail(p):
        p.body_raw = p.body_raw[:-4] + b"\0\0\0\0"
        p.subtype_size ^= 0x10
        raise RuntimeError("mutator failed half-way")

    with pytest.raises(RuntimeError):
        doc._body_edit("broken", i, half_then_fail)
    assert bytes(po.body_raw) == body and po.subtype_size == flags
    assert len(doc._undo) == depth


# ------------------------------------------------------------ 8-handle resize (UI)
@pytest.fixture()
def win(arrange):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(arrange)
    w.resize(1400, 1000)
    w.show()
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)


def _drag_handle(w, idx, start, end):
    from PySide6 import QtCore, QtGui, QtWidgets

    view = w.view
    view._set_units([[idx]])
    QtWidgets.QApplication.processEvents()

    def ev(kind, sp, buttons):
        vp = view.mapFromScene(QtCore.QPointF(*sp))
        g = view.viewport().mapToGlobal(vp)
        return QtGui.QMouseEvent(
            kind, QtCore.QPointF(vp), QtCore.QPointF(g), QtCore.Qt.LeftButton, buttons, QtCore.Qt.NoModifier
        )

    view.mousePressEvent(ev(QtCore.QEvent.MouseButtonPress, start, QtCore.Qt.LeftButton))
    # (a line's end handle gives way to its end NODE, which drags freely)
    assert view._resizing is not None or view._node_drag is not None, "no handle at %s" % (start,)
    view.mouseMoveEvent(ev(QtCore.QEvent.MouseMove, end, QtCore.Qt.LeftButton))
    view.mouseReleaseEvent(ev(QtCore.QEvent.MouseButtonRelease, end, QtCore.Qt.NoButton))
    QtWidgets.QApplication.processEvents()


def test_resize_handle_box_is_the_extent_not_the_pen(win):
    doc = win.doc
    i = doc.create_object("rectangle", rect=(300, 500, 400, 550), colour=0x3366CC)
    assert doc.set_line_width(i, 5)
    win._refresh()
    win.view._snap, win.view._grid = True, 10
    win.view._set_units([[i]])
    _idx, box = win.view._resize_target()
    assert (box.left(), box.top(), box.right(), box.bottom()) == (300, 500, 400, 550)
    _drag_handle(win, i, (400, 525), (410, 525))  # right-middle handle, +10
    assert _box(doc.parsed.page.objects[i]) == (300, 500, 410, 550)


def test_resize_a_flat_line_along_its_length(win):
    doc = win.doc
    i = doc.create_object("line", nodes=[(300, 500), (400, 500)])
    assert doc.set_line_width(i, 5)
    win._refresh()
    win.view._snap, win.view._grid = True, 10
    _drag_handle(win, i, (400, 500), (410, 500))
    assert G.get_object_nodes(doc.parsed.page.objects[i]) == [(300, 500), (410, 500)]


def test_ungroup_command_takes_every_selected_group(win):
    doc = win.doc
    a = doc.create_object("rectangle", rect=(10, 510, 40, 540))
    b = doc.create_object("rectangle", rect=(50, 510, 80, 540))
    g1 = doc.group_objects([a, b])
    c = doc.create_object("rectangle", rect=(10, 550, 40, 580))
    d = doc.create_object("rectangle", rect=(50, 550, 80, 580))
    g2 = doc.group_objects([c, d])
    win._refresh()
    win._on_canvas_selection([g1, g2])
    assert win.act_ungroup.isEnabled()  # two units, both groups
    depth = len(doc._undo)
    win.act_ungroup.trigger()
    assert not doc.page.objects[doc.root_of(win._sel[0])].members
    assert len(win._sel) == 4 and len(doc._undo) == depth + 1
