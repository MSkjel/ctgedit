"""A move shifts the model in place without a full derive."""

import itertools
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from ctgedit.format import element as E  # noqa: E402
from ctgedit.format import geometry as G  # noqa: E402

HEADER = ("_anchor", "_rect1", "_rect2")


@pytest.fixture
def fx(fx_install, monkeypatch):
    c = itertools.count(E.ctdraw_timestamp())
    monkeypatch.setattr(E, "ctdraw_timestamp", lambda: next(c))
    return fx_install


def _full(d):
    """A full derive of the document's records as saved."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format.page import parse_ctg_bytes
    from ctgedit.io.reader import derive_page
    from ctgedit.io.readerparts.common import derive_options

    with derive_options(element_window=isinstance(d, ElementDocument)):
        full, _ = derive_page(parse_ctg_bytes(d.serialize()), d.path, records_fresh=True)
    return full


def _same_as_a_derive(d):
    from ctgedit.io.reader import model_difference

    assert model_difference(d.page, _full(d)) == ""


def _rects(page, idx):
    from PySide6 import QtWidgets

    from ctgedit.render.scene import PageScene

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    sc = PageScene()
    sc.set_page(page)
    out = {}
    for i in idx:
        it = sc._items.get(i)
        if it is not None:
            r = it.sceneBoundingRect()
            out[i] = (r.x(), r.y(), r.width(), r.height())
    return out


def test_a_moved_missing_genie_is_drawn_where_it_went(fx):
    """FxMissing's unresolved genie: its X box is drawn from '_rect1', at its new place after
    a move."""
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(fx.page("FxMissing"))
    o = d.page.objects[0]
    assert o.raw.get("_missing_element")
    before = dict((k, o.raw[k]) for k in HEADER)
    d.move_objects([0], 3, 2)
    full = _full(d)
    assert [d.page.objects[0].raw[k] for k in HEADER] == [full.objects[0].raw[k] for k in HEADER]
    assert d.page.objects[0].raw["_rect1"] != before["_rect1"]
    assert d.page.objects[0].geometry == full.objects[0].geometry
    span = range(0, len(d.page.subtree(0)))
    moved, derived = _rects(d.page, span), _rects(full, span)
    assert moved == derived and moved


def test_a_moved_animation_point_has_its_marker_where_it_went(fx):
    """FxAnPoints' free animation points: the marker is drawn at '_anchor'."""
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(fx.page("FxAnPoints"))
    k = next(
        i for i, o in enumerate(d.page.objects) if o.parent is None and o.raw.get("_type_code") == 0x1000
    )
    d.move_objects([k], 7, 5)
    full = _full(d)
    assert [d.page.objects[k].raw[h] for h in HEADER] == [full.objects[k].raw[h] for h in HEADER]
    moved, derived = _rects(d.page, [k]), _rects(full, [k])
    assert moved == derived and moved


@pytest.mark.parametrize("which", ["top", "hidden"])
def test_a_label_moved_off_a_stack_shows_the_stack_as_a_derive_does(fx, which):
    """FxTextKinds' three gated 'Login...' labels stacked at one place: only the topmost is
    drawn (Settings > Canvas, stacked labels). A label moved off the stack leaves the stack
    as a full derive shows it."""
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(fx.page("FxTextKinds"))
    stack = [
        i
        for i, o in enumerate(d.page.objects)
        if o.kind.name == "TEXT" and o.style.text == "Login..." and (o.geometry.x, o.geometry.y) == (20, 60)
    ]
    assert len(stack) == 3
    assert [bool(d.page.objects[i].raw.get("_dup_suppressed")) for i in stack] == [True, True, False]
    moved = stack[-1] if which == "top" else stack[0]
    d.move_objects([moved], 0, 40)
    full = _full(d)
    flags = [bool(d.page.objects[i].raw.get("_dup_suppressed")) for i in stack]
    assert flags == [bool(full.objects[i].raw.get("_dup_suppressed")) for i in stack]
    assert flags == ([True, False, False] if which == "top" else [False, True, False])
    assert _rects(d.page, stack) == _rects(full, stack)
    d.undo()
    assert [bool(d.page.objects[i].raw.get("_dup_suppressed")) for i in stack] == [True, True, False]


@pytest.mark.parametrize("host", ["n1", "n3"])
def test_a_moved_genie_copy_keeps_its_model_with_its_record(fx, host):
    """A genie copy moved in an element window: its root's model transform takes the H, K
    its record's Local now holds; so after the move's undo and redo."""
    from ctgedit.editing import ElementDocument

    d = ElementDocument.open_element(fx.file("FxLib", "fxnest.ctm"), host)
    root = next(
        k for k, o in enumerate(d.parsed.page.objects) if o.class_flag and (o.subtype_size & 0x201) == 0x201
    )
    t0 = d.page.objects[root].geometry.transform
    assert not t0.is_identity()
    h, k = t0.h, t0.k
    d.move_objects([root], 3, 2)
    t = d.page.objects[root].geometry.transform
    assert (t.h, t.k) == (h + 3, k + 2)
    _same_as_a_derive(d)
    d.undo()
    _same_as_a_derive(d)
    d.redo()
    _same_as_a_derive(d)


def test_a_group_refitted_around_a_moved_member_takes_its_new_header(fx):
    """FxMembers' group 0 refits when its member moves: its model anchor and rects follow
    the rewritten record."""
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(fx.page("FxMembers"))
    g = d.page.objects[0]
    assert g.kind.name == "GROUP" and g.members
    before = g.raw["_rect1"]
    d.move_objects([g.members[0]], 5, 4)
    assert d.page.objects[0].raw["_rect1"] != before
    _same_as_a_derive(d)
    d.undo()
    _same_as_a_derive(d)


def test_a_polygon_whose_first_x_is_its_node_count_reads_its_own_nodes(fx):
    """A polygon whose first x is its node count reads its own nodes, not a run 2 bytes into
    the real one (the count read from the first x), which also passes the bbox test."""
    pe = E.open_element(fx.file("FxLib", "fxgenie.ctm"), "fx_valve")
    po = next(o for o in pe.tree if o.type_code in G.POLYGON_TCS)
    for pts in (
        [(3, 2), (33, 17), (3, 32)],
        [(0, 0), (30, 15), (0, 30)],
        [(4, 1), (4, 30), (30, 15), (5, 3)],
    ):
        assert G.set_object_nodes(po, pts)
        assert G.get_object_nodes(po) == pts


def test_a_moved_valve_keeps_its_triangle(fx):
    """fx_valve's triangle moved by (3, 2) in its element window: the derive after the move
    draws the moved triangle."""
    from ctgedit.editing import ElementDocument

    d = ElementDocument.open_element(fx.file("FxLib", "fxgenie.ctm"), "fx_valve")
    k = next(i for i, o in enumerate(d.parsed.page.objects) if o.type_code in G.POLYGON_TCS)
    nodes = d.page.objects[k].geometry.nodes
    d.move_objects([k], 3, 2)
    want = [(x + 3, y + 2) for x, y in nodes]
    assert d.page.objects[k].geometry.nodes == want
    assert _full(d).objects[k].geometry.nodes == want
    _same_as_a_derive(d)


#: node lists a run INSIDE the real array also passes for, its bbox rect1's exactly: the
#: count read from the first x ('first x'), or
#: from a y equal to the number of nodes after it, the run then the last three nodes ('suffix')
_INNER_RUNS = {
    "first x": [(3, 0), (0, 3), (3, 3)],
    "suffix": [(10, 10), (20, 3), (0, 0), (30, 0), (30, 30)],
    "suffix, within the tolerance": [(10, 10), (20, 3), (1, 1), (30, 0), (30, 30)],
}


@pytest.mark.parametrize("record", [5, 7], ids=["polygon", "pipe"])
@pytest.mark.parametrize("name", sorted(_INNER_RUNS))
def test_a_node_array_is_read_where_its_class_section_starts(fx, record, name):
    """A node array is read where its class section starts."""
    import copy

    from ctgedit.format import colourstate as CS
    from ctgedit.format.page import parse_ctg_bytes

    with open(fx.page("FxShapes"), "rb") as fh:
        po = copy.deepcopy(parse_ctg_bytes(fh.read()).page.objects[record])
    pts = _INNER_RUNS[name]
    assert G.set_object_nodes(po, pts)
    assert G.get_object_nodes(po) == pts
    na = G.find_node_array(po)
    assert na.offset == CS.layout(po)["style_at"] + 54
    # a polygon keeps its 9-byte gradient part after the array, a pipe ends with it
    assert len(po.body_raw) - na.end == (9 if po.type_code in G.POLYGON_TCS else 0)
    moved = [(x + 2, y + 1) for x, y in pts]
    assert G.set_object_nodes(po, moved) and G.get_object_nodes(po) == moved


def test_a_reshaped_polygon_keeps_its_nodes_through_a_save(fx):
    """The editor's reshape of FxShapes' polygon to nodes an inner run fits: the model, a
    derive after the edit and the page opened again all have the nodes given."""
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(fx.page("FxShapes"))
    pts = _INNER_RUNS["suffix"]
    assert d.set_object_nodes(5, pts)
    assert d.page.objects[5].geometry.nodes == pts
    _same_as_a_derive(d)
    d.save()
    again = EditableDocument.open(fx.page("FxShapes"))
    assert again.page.objects[5].geometry.nodes == pts
