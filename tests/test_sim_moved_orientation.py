"""A turned genie member keeps its turn through an edit."""

import gc
import os

import pytest

from ctgedit.format import placement as P
from qtkit import app as _app


#: the element-level turns of the nested valve: Arrange steps
TURNS = {
    "cw": ("cw",),
    "acw": ("acw",),
    "half": ("cw", "cw"),
    "mh": ("mh",),
    "mv": ("mv",),
    "cw_mh": ("cw", "mh"),
}

#: what each page edit does to a Total's linear part (a, b, c, d; x' = a x + c y)
EDIT_LINEAR = {
    "cw": (0.0, 1.0, -1.0, 0.0),
    "acw": (0.0, -1.0, 1.0, 0.0),
    "mh": (-1.0, 0.0, 0.0, 1.0),
    "mv": (1.0, 0.0, 0.0, -1.0),
}


def _arrange(doc, indices, step):
    if step == "cw":
        doc.rotate_objects(indices)
    elif step == "acw":
        doc.rotate_objects(indices, clockwise=False)
    elif step == "mh":
        doc.mirror_objects(indices)
    else:
        doc.mirror_objects(indices, horizontal=False)


def _turned_valve(fx_install, name, steps):
    """A genie of fxparts.ctm: fxgenie.fx_valve (a triangle and a caption) placed and turned
    by ``steps`` in the element, beside a grey box."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = os.path.join(fx_install.project("FxLib"), "fxparts.ctm")
    d = ElementDocument.new_element(lib, name, "genie")
    k = d.place_library_element("fxgenie", "fx_valve", "ctm", (0, 0), params={"TagPrefix": "FX_V1"})
    assert k is not None
    for s in steps:
        _arrange(d, [k], s)
    d.create_object("rectangle", (70, 0, 90, 20), colour=0x808080)
    d.save()
    libraries_changed()


def _drawn(page, root):
    """What the Simulate window draws of ``root``'s subtree: each record's item box, and a
    symbol set's box per state (the engine's symbol_state)."""
    from ctgedit.render.scene import PageScene
    from ctgedit.sim.state import ObjectState

    _app()
    sc = PageScene()
    sc.set_page(page)
    sc.set_runtime_look(True)
    out = []

    def box(it):
        if it is None:
            return None
        r = it.sceneBoundingRect()
        return (round(r.left()), round(r.top()), round(r.width()), round(r.height()))

    try:
        for j in page.subtree(root):
            o = page.objects[j]
            out.append((j, box(sc._items.get(j))))
            if o.raw.get("_type_code") == 0x0C and o.an:
                for st in range(len(o.members)):
                    sc.apply_states({o.an: ObjectState(o.an, symbol_state=st)})
                    out.append((j, st, box(sc.sim_layer()._swap_active.get(j))))
                sc.apply_states({o.an: None})
    finally:
        sc.clear()
    return out


def _shifted(drawn, dx, dy):
    out = []
    for row in drawn:
        b = row[-1]
        out.append(row[:-1] + ((b[0] + dx, b[1] + dy, b[2], b[3]) if b else None,))
    return out


def _same_drawing(a, b, tol: int = 1) -> bool:
    """Two drawings alike to ``tol`` pixels (a turned text's item box moves a pixel with its
    place on the page: Qt's glyph layout, not the record)."""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x[:-1] != y[:-1] or (x[-1] is None) != (y[-1] is None):
            return False
        if x[-1] is not None and any(abs(u - v) > tol for u, v in zip(x[-1], y[-1])):
            return False
    return True


def _linears(doc, root):
    """{record: its Total's linear part} of ``root``'s subtree."""
    objs = doc.parsed.page.objects
    out = {}
    for j in doc.page.subtree(root):
        (m, M, _o, _n) = P.read_slots(objs[j].body_raw)[0][2]
        out[j] = tuple(M[:4]) if m else (1.0, 0.0, 0.0, 1.0)
    return out


def _mul(R, A):
    a1, b1, c1, d1 = R
    a2, b2, c2, d2 = A
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2)


def _close(a, b):
    return all(abs(x - y) < 1e-6 for x, y in zip(a, b))


def _sim_and_fresh(doc, root):
    """(what the Simulate window draws of the edited document, what a fresh load of the saved
    page draws). The window derives the document's records in the runtime's modes
    (SimulatedPage.reload_page: runtime_page with the document's parse)."""
    from ctgedit.ui.simulate.window import runtime_page

    path = doc.path
    sim = _drawn(runtime_page(path, doc.parsed, doc), root)
    doc.save()
    fresh = _drawn(runtime_page(path), root)
    return sim, fresh


def _model_slots(model, an):
    for o in model.get("objects", ()):
        if o.get("an") == an:
            return o.get("slots")
    return None


def _check_edits(doc, root, label):
    """Every edit of the edit run: the Simulate window draws what a fresh load draws, a move
    (and its undo / redo) shifts the drawing it had, an Arrange step turns every Total."""
    sim0, fresh0 = _sim_and_fresh(doc, root)
    assert sim0 == fresh0, label
    lin0 = _linears(doc, root)
    steps = [
        ("move", lambda: doc.move_objects([root], 17, 9), (17, 9)),
        ("nudge", lambda: doc.move_objects([root], 1, 0), (1, 0)),
        ("undo", doc.undo, (-1, 0)),
        ("redo", doc.redo, (1, 0)),
        ("cw", lambda: doc.rotate_objects([root]), "cw"),
        ("acw", lambda: doc.rotate_objects([root], clockwise=False), "acw"),
        ("mh", lambda: doc.mirror_objects([root]), "mh"),
        ("mv", lambda: doc.mirror_objects([root], horizontal=False), "mv"),
        ("move back", lambda: doc.move_objects([root], -18, -9), (-18, -9)),
    ]
    before, lin = sim0, lin0
    for name, edit, how in steps:
        edit()
        sim, fresh = _sim_and_fresh(doc, root)
        assert sim == fresh, (label, name)
        now = _linears(doc, root)
        if isinstance(how, tuple):
            assert _same_drawing(sim, _shifted(before, *how)), (label, name, sim, before)
            assert all(_close(now[j], lin[j]) for j in now), (label, name)
        else:
            R = EDIT_LINEAR[how]
            assert all(_close(now[j], _mul(R, lin[j])) for j in now), (label, name)
        before, lin = sim, now
    # a resize keeps every turn (an axis scale: the zero pattern of each Total stays)
    g = doc.page.objects[root].geometry
    doc.set_object_rect(root, (int(g.x), int(g.y), int(g.x + 2 * g.extent_x), int(g.y + g.extent_y)))
    sim, fresh = _sim_and_fresh(doc, root)
    assert sim == fresh, (label, "resize")
    now = _linears(doc, root)
    for j in now:
        assert [abs(v) > 1e-9 for v in now[j]] == [abs(v) > 1e-9 for v in lin[j]], (label, "resize", j)
    doc.undo()
    sim, fresh = _sim_and_fresh(doc, root)
    assert sim == fresh, (label, "resize undone")
    assert _same_drawing(sim, before), (label, "resize undone")
    # the page model the engine reads (pagemodel slots) is the fresh load's
    from ctgedit.editing import EditableDocument

    doc.move_objects([root], 3, 4)
    model = doc.export_page_model()
    doc.save()
    fresh_model = EditableDocument.open(doc.path).export_page_model()
    for j in doc.page.subtree(root):
        an = doc.page.objects[j].an
        if isinstance(an, int) and an > 0:
            assert _model_slots(model, an) == _model_slots(fresh_model, an), (label, j)


@pytest.mark.parametrize("turn", sorted(TURNS))
def test_a_genie_with_a_turned_nested_valve_keeps_its_turn(fx_install, fx_page, turn):
    from ctgedit.editing import EditableDocument

    name = "fx_valve_%s" % turn
    _turned_valve(fx_install, name, TURNS[turn])
    path = fx_page("FxSymbols")
    doc = EditableDocument.open(path)
    root = doc.place_library_element("fxparts", name, "ctm", (400, 300))
    doc.save()
    doc = EditableDocument.open(path)
    # the nested valve's page record: an identity Local, the turn in its slot 2 alone
    nested = [j for j in doc.page.subtree(root)[1:] if P.whole_slot2(doc.parsed.page.objects[j]) is not None]
    assert nested, turn
    _check_edits(doc, root, turn)
    gc.collect()


@pytest.mark.parametrize(
    "page,name",
    [
        ("FxSymbols", "FxBitmapSymbol"),
        ("FxParity", "FxTurnedPic1"),
        ("FxMotion", "FxSymRotate"),
        ("FxMotion", "FxSymStretched"),
        ("FxGenies", "FxValve1"),
    ],
)
def test_turned_symbols_and_genies_of_the_fixtures(fx_page, page, name):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page(page))
    (root,) = doc.find_objects_by_name(name)
    _check_edits(doc, root, name)
    gc.collect()


def test_a_turned_symbol_set(fx_page):
    """FxSymbols' FxOnOffSet turned a quarter: each state the simulator shows."""
    from ctgedit.editing import EditableDocument

    path = fx_page("FxSymbols")
    doc = EditableDocument.open(path)
    (k,) = doc.find_objects_by_name("FxOnOffSet")
    doc.rotate_objects([k])
    doc.save()
    doc = EditableDocument.open(path)
    (k,) = doc.find_objects_by_name("FxOnOffSet")
    _check_edits(doc, k, "FxOnOffSet")
    gc.collect()


def test_translate_records_keeps_a_whole_slot2():
    """format level: a member whose slot 2 turns while its Local is the identity keeps the
    turn when its genie moves; its Total and its children's follow."""
    from ctgedit.format import records as R

    root = R.to_po(
        R.record(
            0x08,
            handle=1,
            anchor=(100, 119),
            rect1=(100, 100, 140, 120),
            rect2=(100, 100, 140, 120),
            child_count=1,
        )
    )
    member = R.to_po(
        R.record(0x08, handle=2, anchor=(100, 119), rect1=(100, 100, 120, 140), rect2=(100, 100, 120, 140))
    )
    P.write_slots(
        root,
        [
            (0x01, (1.0, 0.0, 0.0, 1.0, 100.0, 100.0)),
            (0x01, (1.0, 0.0, 0.0, 1.0, 100.0, 100.0)),
            (0x01, (1.0, 0.0, 0.0, 1.0, 100.0, 100.0)),
        ],
    )
    turn = (0.0, 1.0, -1.0, 0.0, 20.0, 0.0)
    P.write_slots(member, [(0, P.IDENT), (0x15, turn), (0x15, (0.0, 1.0, -1.0, 0.0, 120.0, 100.0))])
    assert P.whole_slot2(member) == (turn, 0x15)
    assert P.link_of(member) == (turn, 0x15, (0, 0))
    P.translate_records([root, member], [None, 0], 5, 7)
    (s1, s2, s3), _end = P.read_slots(member.body_raw)
    assert s1[:2] == (0, P.IDENT)
    assert s2[:2] == (0x15, turn)
    assert s3[0] == 0x15 and _close(s3[1][:4], turn[:4])
    assert s3[1][4:] == (125.0, 107.0)
