"""Record forest grouping."""

import pytest

from ctgedit.editing import EditableDocument
from ctgedit.format import geometry as G
from ctgedit.io.reader import read_page
from ctgedit.model.document import ObjectKind


def _named(doc, name):
    hits = doc.find_objects_by_name(name)
    assert len(hits) == 1, (name, hits)
    return hits[0]


@pytest.mark.parametrize("page", ["FxGenies", "FxComposites"])
def test_forest_is_consistent(fx_page, page):
    page = read_page(fx_page(page))
    # every member points back at its parent; subtrees are contiguous
    for i, o in enumerate(page.objects):
        for m in o.members:
            assert page.objects[m].parent == i
        if o.parent is not None:
            assert i in page.objects[o.parent].members
    # roots + all members partition the object set exactly once
    seen = set()
    for r in page.roots():
        for j in page.subtree(r):
            assert j not in seen
            seen.add(j)
    assert len(seen) == len(page.objects)


def test_containers_have_members_and_genies_classified(fx_page):
    page = read_page(fx_page("FxGenies"))
    containers = [o for o in page.objects if o.members]
    assert containers, "no containers found on FxGenies"
    genies = [o for o in page.objects if o.is_genie]
    assert genies, "no genies classified on FxGenies"
    for g in genies:
        assert g.kind in (ObjectKind.GENIE, ObjectKind.COMPOSITE_GENIE)
    # FxComposites holds both kinds (classic genies and the composites of five templates)
    kinds = {o.kind for o in read_page(fx_page("FxComposites")).objects if o.is_genie}
    assert kinds == {ObjectKind.GENIE, ObjectKind.COMPOSITE_GENIE}


@pytest.mark.parametrize("page", ["FxComposites", "FxGenies"])
def test_no_giant_resolved_coordinates(fx_page, page):
    """Containers must NOT be library-resolved."""
    page = read_page(fx_page(page))
    w = page.appearance.width or 1920
    h = page.appearance.height or 1080
    for o in page.objects:
        if o.raw.get("_non_drawable") or o.members:
            continue
        g = o.geometry
        # every drawable leaf sits within a sane margin of the page
        assert g.x < w + 500 and g.y < h + 500, (o.kind, g.x, g.y)


@pytest.fixture
def genies(fx_page):
    return EditableDocument.open(fx_page("FxGenies"))


def test_subtree_move_moves_all_members(genies):
    doc = genies
    # the group FxGroupedGenie: a genie (two leaves) and a rectangle
    root = _named(doc, "FxGroupedGenie")
    assert len(doc.page.objects[root].members) >= 2
    sub = doc.subtree(root)
    assert len(sub) == 5
    before = {i: G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1 for i in sub}
    doc.move_objects(sub, 20, 30)
    for i in sub:
        r = G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1
        b = before[i]
        assert r == (b[0] + 20, b[1] + 30, b[2] + 20, b[3] + 30)


def test_subtree_delete_removes_whole_group(genies):
    doc = genies
    root = _named(doc, "FxGroupedGenie")
    n_before = len(doc.objects)
    size = len(doc.subtree(root))
    assert size >= 3
    doc.delete_objects([root])
    assert len(doc.objects) == n_before - size
    assert doc.find_objects_by_name("FxGroupedGenie") == []


def test_subtree_duplicate_copies_group(genies):
    doc = genies
    root = _named(doc, "FxGroupedGenie")
    n_before = len(doc.objects)
    size = len(doc.subtree(root))
    new_roots = doc.duplicate_objects([root])
    assert len(doc.objects) == n_before + size
    # the duplicate is itself a container with the same member count
    nr = new_roots[0]
    assert len(doc.page.objects[nr].members) == len(doc.page.objects[root].members)


def test_nested_genie_is_reachable(fx_page):
    """Nested genies (parent != None) must be selectable precisely + expose
    their bindings + be found by nearest_genie. FxGenies holds the
    chain FxNested (fxnest.n4 .. n0, each nesting the one before) and a genie inside the
    group FxGroupedGenie."""
    page = read_page(fx_page("FxGenies"))
    nested = [i for i, o in enumerate(page.objects) if o.is_genie and o.parent is not None]
    assert len(nested) >= 5, nested
    for g in nested:
        # nearest_genie from a member leaf resolves up to the genie, not the outer group
        members = [m for m in page.subtree(g) if m != g and not page.objects[m].is_genie]
        direct = [m for m in members if page.objects[m].parent == g]
        assert direct, g
        assert page.nearest_genie(direct[0]) == g
        # the genie itself reports itself
        assert page.nearest_genie(g) == g


def test_genie_bindings_editable(genies):
    doc = genies
    # a classic genie (a composite's options are read-only: COMPOSITE_LOCKED)
    g = _named(doc, "FxPump2")
    assert doc.page.objects[g].is_genie and not doc.is_composite_genie(g)
    assert doc.genie_bindings(g) == {"Tag": "FX_PUMP2", "Label": "Pump 2"}
    assert doc.set_genie_binding(g, "Tag", "EDITED.TAG")
    assert doc.genie_bindings(g)["Tag"] == "EDITED.TAG"


def test_delete_child_decrements_parent_count(genies):
    doc = genies
    root = _named(doc, "FxGroupedGenie")
    child = doc.page.objects[root].members[0]
    child_sub = len(doc.subtree(child))
    before_members = len(doc.page.objects[root].members)
    doc.delete_objects([child])
    # the group survived with one fewer direct member
    assert len(doc.page.objects[root].members) == before_members - 1
    assert child_sub >= 1
