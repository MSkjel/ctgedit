"""A recomposed genie still draws its art."""

import importlib.util
import os
import shutil

import pytest

import fxinstall

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")


def _sweep():
    spec = importlib.util.spec_from_file_location(
        "genie_recompose_sweep", os.path.join(TOOLS, "genie_recompose_sweep.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _sweep()

#: (page, the instance's name, its library, its element): small genies whose members take
#: their art from owner / twin records
CASES = [
    ("FxGenies", "FxLamp", "fxgenie", "fx_lamp"),
    ("FxParts", "FxPicture1", "fxparts", "fx_picture"),
    ("FxParts", "FxSets1", "fxparts", "fx_sets"),
]


def _doc(fx_install, page):
    """The page of the test's fx_install copy, opened, and its bytes."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page(page))
    return doc, doc.serialize()


def _instance(doc, name, library, element):
    (i,) = doc.find_objects_by_name(name)
    assert dict(doc.genie_instances()).get(i) is not None, name
    lib, el = dict(doc.genie_instances())[i]
    assert (lib.lower(), el.lower()) == (library, element)
    return i


def _current_count(doc, root):
    """The record count of the instance's CURRENT element as a page composes it: a stored
    instance older than its element (its library edited since the page was saved) is
    re-composed to it (LIVE: CtDraw's load gives a stale instance its element's current
    records)."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format.symbols import reference_from_object

    ref = reference_from_object(doc.parsed.page.objects[root])
    rr = doc._resolver().resolve(ref)
    return len(GP.load_element(rr.library_path, ref.element, compose=True).records)


def _run(doc, original, root, kind, fn):
    before = S.subtree_stats(doc.page, root)
    assert before["art"] > 0, "the stored instance draws no art -- not a useful case"
    res = S._step(doc, root, before, kind, fn, True, original)
    assert res["outcome"] != "error", res.get("error")
    assert res["outcome"] != "not-applicable", "the genie was not re-composed"
    if res["outcome"] == "changed":
        assert res["undo_restores_bytes"] and res["undo_restores_model"], res
        # what a reload of the re-composed page draws = what the session draws
        assert res["fresh_matches_session"], res
    assert doc.serialize() == original
    return before, res


@pytest.mark.parametrize("page,name,library,element", CASES, ids=[c[1] for c in CASES])
def test_identity_recompose_keeps_every_art_member(fx_install, page, name, library, element):
    """Re-composing the instance with its own values (Update Pages / CtDraw's load) leaves
    every art-bearing member drawing its art -- and nothing drawn changes."""
    doc, original = _doc(fx_install, page)
    root = _instance(doc, name, library, element)
    want = _current_count(doc, root)
    before, res = _run(doc, original, root, "identity", lambda: doc._recompose_genie(root, {}, "identity"))
    if res["outcome"] == "changed":
        assert res["records"][1] == want, res
        assert res["art"][1] >= res["art"][0], res
        if res["records"][0] == want:
            # a current instance: nothing drawn changes (a stale one follows its element)
            assert res["art_lost_members"] == 0, res
            assert res.get("changed_members") == 0, res.get("changed_samples")


@pytest.mark.parametrize("page,name,library,element", CASES, ids=[c[1] for c in CASES])
def test_parameter_change_keeps_the_art(fx_install, page, name, library, element):
    """The genie dialog's OK with one parameter changed (numeric +1, else 'X' appended)
    re-composes the instance: it keeps its members and every one of their art."""
    doc, original = _doc(fx_install, page)
    root = _instance(doc, name, library, element)
    pick = S.pick_parameter(doc, root, {})
    assert pick is not None, "the genie declares no parameters"
    pname, _old, new = pick
    want = _current_count(doc, root)
    before, res = _run(doc, original, root, "param", lambda: doc.set_genie_bindings(root, {pname: new}))
    assert res["outcome"] == "changed", "a changed parameter re-composed nothing"
    assert res["records"][1] == want, res
    assert res["art"][1] >= res["art"][0], res
    assert res["art_lost_members"] == 0, res


# -- a re-composition that CHANGES the instance's structure -------------------------------------
def test_restructured_recompose_links_every_member_to_its_element_record(fx_install):
    """A restructured recompose links every member to its element record."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format import geometry as G
    from ctgedit.format import placement as P
    from ctgedit.format.symbols import reference_from_object

    doc, original = _doc(fx_install, "FxParts")
    root = _instance(doc, "FxStale1", "fxparts", "fx_stale")
    objs = doc.parsed.page.objects
    stored = [objs[i] for i in doc.subtree(root)]
    ref = reference_from_object(stored[0])
    el = GP.load_element(doc._resolver().resolve(ref).library_path, ref.element, compose=True)
    assert not GP._aligns(el.records, stored, 0, len(stored)), "not a restructured case any more"
    kept = {G.get_handle(po) for po in stored[1:]}
    assert doc._recompose_genie(root, {}, "identity")
    try:
        objs = doc.parsed.page.objects
        inst = [objs[i] for i in doc.subtree(root)]
        assert GP._aligns(el.records, inst, 0, len(inst))
        ep = P.forest_parents(el.records)
        bad = [
            (k, inst[k].an_str, G.get_handle(el.records[k]))
            for k in range(1, len(inst))
            if not (el.records[k].type_code in GP.INPUT_TCS and ep[k] is not None)
            and inst[k].an_str != str(G.get_handle(el.records[k]))
        ]
        assert not bad, bad[:8]
        # every element record a stored member names (AN string + class) keeps that member's
        # handle (its page AN = handle + 1 stays): ctdraw_instance_handles' first rule, as
        # CtDraw's own load + save does
        by = {}
        for po in stored[1:]:
            by.setdefault((po.an_str, po.type_code), []).append(G.get_handle(po))
        want = []
        for k in range(1, len(el.records)):
            q = by.get((str(G.get_handle(el.records[k])), el.records[k].type_code))
            if q:
                want.append((k, q.pop(0)))
        assert want and all(G.get_handle(inst[k]) == h for k, h in want), [
            (k, h, G.get_handle(inst[k])) for k, h in want if G.get_handle(inst[k]) != h
        ][:8]
        assert kept
    finally:
        doc.undo()
    assert doc.serialize() == original


def _structure(doc, root):
    """Each record of an instance: its type, AN string, child count and handle."""
    from ctgedit.format import geometry as G

    objs = doc.parsed.page.objects
    return [
        (objs[i].type_code, objs[i].an_str, objs[i].class_flag, G.get_handle(objs[i]))
        for i in doc.subtree(root)
    ]


@pytest.mark.golden
def test_a_stale_instance_recomposes_like_ctdraws_load(fx_install):
    """CtDraw's PageOpen + PageSave of FxParts re-composes FxStale1 to its element as it is
    now: ctgedit's identity re-composition gives the same records (types, AN strings, child
    counts, handles), and the current instances (FxLamp's kind: FxPicture1, FxSets1) keep
    theirs."""
    from ctgedit.editing import EditableDocument

    src = fxinstall.oracle("pages", "FxMain/FxParts", "page.ctg")
    dst = os.path.join(fx_install.project("FxMain"), "FxOracleParts.ctg")
    shutil.copyfile(src, dst)
    theirs = EditableDocument.open(dst)
    doc, _original = _doc(fx_install, "FxParts")
    for name, library, element in (
        ("FxStale1", "fxparts", "fx_stale"),
        ("FxPicture1", "fxparts", "fx_picture"),
        ("FxSets1", "fxparts", "fx_sets"),
    ):
        root = _instance(doc, name, library, element)
        doc._recompose_genie(root, {}, "identity")
        root = _instance(doc, name, library, element)
        assert _structure(doc, root) == _structure(theirs, _instance(theirs, name, library, element)), name


def test_a_restructured_instance_takes_the_handles_ctdraws_load_gives():
    """genie_place.ctdraw_instance_handles against CtDraw's own load + save of a project
    page (tools/engine_roundtrip.py; its table genie grew from 1514 to 2398 records): every
    handle CtDraw gave is reproduced."""
    import gzip
    import json
    import struct
    from ctgedit.format import genie_place as GP

    class _Rec:
        def __init__(self, h, an_str, tc, cf):
            self.body_raw = struct.pack("<HH", 0, h)
            self.an_str, self.type_code, self.class_flag, self.subtype_size = an_str, tc, cf, 0

    path = os.path.join(os.path.dirname(__file__), "data", "ctdraw_instance_handles.json.gz")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    for case in data["cases"]:
        stored = [_Rec(*r) for r in case["stored"]]
        el = [_Rec(*r) for r in case["element_records"]]
        got = GP.ctdraw_instance_handles(stored, el, set(case["outside"]))
        bad = [(k, g, w) for k, (g, w) in enumerate(zip(got, case["ctdraw"])) if g != w]
        assert len(got) == len(case["ctdraw"]) and not bad, (case["element"], bad[:8])
