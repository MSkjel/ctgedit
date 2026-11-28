"""When a nested genie copy needs composing again."""

from ctgedit.ui.main_window import MainWindow
import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from ctgedit.format import copy_compare as CC  # noqa: E402
from ctgedit.format import element as E  # noqa: E402
from ctgedit.format import genie_place as GP  # noqa: E402
from ctgedit.format import writer  # noqa: E402
from test_element_nested_update import window  # noqa: E402,F401  (the fixture)


def _nest(fx):
    return fx.file("FxLib", "fxnest.ctm")


def _genies(fx):
    return fx.file("FxLib", "fxgenie.ctm")


def _rec(po, change):
    """``po`` with its decoded record passed through ``change(r)``."""
    r = GP.decode(po)
    change(r)
    return GP.to_po(GP.encode(r))


def _grow_size_words(r):
    """Every bag's size word grown (a CtDraw save's 36 where ctgedit writes 10)."""
    for lvl in r.entries or ():
        for b in lvl:
            b.ov += 26


def _poke(lib, name, change, which=None) -> None:
    """Write element ``name`` back with the records of its tree at ``which`` (default: all
    with bags) passed through ``change``: its identity and import table as they are, so its
    stamps stay current."""
    from ctgedit.editing import libraries_changed

    pe = E.open_element(lib, name)
    tree = [
        (
            _rec(po, change)
            if (which is None and po.subtype_size & 8) or (which is not None and k in which)
            else po
        )
        for k, po in enumerate(pe.tree)
    ]
    E.save_element(pe, E.element_content(pe, objects=tree))
    libraries_changed()


def _refresh_changes(lib, name) -> int:
    """How many record ranges a refresh save with every copy composed would change (the
    old compose-all test: any of them flagged the element)."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument
    from ctgedit.format.element_session import open_element_session

    doc = ElementDocument.open_element(lib, name)
    content = doc.serialize_element(refresh=True, every_nested=True)
    lf = E.LibraryFile(doc.session.pe.library.to_bytes())
    lf.write_element(doc.session.pe.name, content)
    _s, parsed = open_element_session(doc.path, doc.session.pe.name, library=lf)
    return len(
        GW.record_ops(
            [writer.object_payload(o) for o in doc.parsed.page.objects],
            [writer.object_payload(o) for o in parsed.page.objects],
        )
    )


def _labels(plan):
    return [u.ref.label for u in plan.elements]


# ---------------------------------------------------------------- the comparison itself
def _copy_records(fx):
    from ctgedit.editing import ElementDocument

    objs = list(ElementDocument.open_element(_nest(fx), "n2").parsed.page.objects)
    assert CC.copy_spans(objs) == [(1, 5)]  # (a text, then the copy of n1)
    return objs


def test_a_save_s_normalisation_is_no_content_difference(fx_install):
    objs = _copy_records(fx_install)
    assert CC.changed_copies(objs, objs) == []

    def ulp(r):
        m, (a, b, c, d, h, k) = r.slots[1]
        r.slots[1] = (m, (a, b, c, d, h + abs(h) * 2.2e-16 + 1e-300, k))

    text = next(k for k in range(1, 5) if objs[k].type_code == 0x05)

    def remeasured(r):
        x0, y0, x1, y1 = r.rect1
        r.rect1, r.rect2 = (x0, y0, x1 - 6, y1), (x0, y0, x1 - 6, y1)

    def sheet_and_order(r):
        b = r.entries[0][1]
        b.pairs = list(reversed(b.pairs)) + [("!*PropSheet.Focus", "#1408.#1412")]

    def refit(r):
        # (a container's box follows its members: a text re-measured narrower moved a copy
        # root's edge 38 pixels in a shipped genie)
        x0, y0, x1, y1 = r.rect2
        r.rect1, r.rect2 = (x0, y0, x1 - 38, y1), (x0, y0, x1 - 38, y1)

    assert objs[1].class_flag
    for change, at in (
        (_grow_size_words, 1),
        (ulp, 1),
        (remeasured, text),
        (sheet_and_order, text),
        (refit, 1),
    ):
        other = list(objs)
        other[at] = _rec(objs[at], change)
        assert other[at].body_raw != objs[at].body_raw
        assert CC.changed_copies(objs, other) == [], change.__name__
    # the element's own records are not compared
    other = list(objs)
    other[0] = _rec(objs[0], lambda r: setattr(r, "anchor", (r.anchor[0] + 40, r.anchor[1])))
    assert CC.changed_copies(objs, other) == []


def test_a_name_key_that_repeats_the_name_is_no_content_difference(fx_install):
    """CtDraw's load + save gives a named record '__AN_NAME__' = its name where it lacks it
    (genie_place.with_name_keys, LIVE), so adding it is no change; another value is
    content."""
    objs = _copy_records(fx_install)
    text = next(k for k in range(1, 5) if objs[k].type_code == 0x05)
    named = list(objs)
    named[text] = _rec(objs[text], lambda r: setattr(r, "name", "Lamp1"))
    keyed = GP.with_name_keys(named)  # (as a save writes it)
    assert keyed[text].body_raw != named[text].body_raw
    assert GP.decode(keyed[text]).entries[0][1].get("__AN_NAME__") == "Lamp1"
    assert CC.changed_copies(named, keyed) == []
    assert CC.changed_copies(keyed, named) == []

    def other_value(r):
        r.entries[0][1].set("__AN_NAME__", "Other")

    other = list(keyed)
    other[text] = _rec(keyed[text], other_value)
    assert CC.changed_copies(keyed, other) == [("fxnest", "n1")]
    assert CC.changed_copies(named, other) == [("fxnest", "n1")]


def test_a_content_difference_names_the_genie(fx_install):
    objs = _copy_records(fx_install)
    text = next(k for k in range(1, 5) if objs[k].type_code == 0x05)

    def moved(r):
        r.anchor = (r.anchor[0] + 3, r.anchor[1])

    def value(r):
        b = r.entries[0][1]
        b.pairs = [(k, v + " OLD") for k, v in b.pairs] or [("TXT_OFF", "OLD")]

    def scaled(r):
        _m, (a, b, c, d, h, k) = r.slots[1]
        r.slots[1] = (3, (2.5, b, c, d, h, k))

    for change, at in ((moved, text), (value, text), (scaled, 1)):
        other = list(objs)
        other[at] = _rec(objs[at], change)
        assert CC.changed_copies(objs, other) == [("fxnest", "n1")], change.__name__
    # a copy gained a record (composed structurally)
    grown = objs[:1] + [_rec(objs[1], lambda r: setattr(r, "cf", r.cf + 1))] + objs[2:5] + [objs[4]]
    assert CC.copy_spans(grown) == [(1, 6)]
    assert CC.changed_copies(objs, grown) == [("fxnest", "n1")]
    # a copy more or fewer: every genie nested is named
    assert CC.changed_copies(objs, objs[:1]) == [("fxnest", "n1")]


def test_a_dummy_marker_s_geometry_is_no_content(fx_install):
    """Every save takes a nested DummyMarker's box and slots from its genie's current
    definition (genie_place._refresh_nested, the copies that did not change too; LIVE:
    CtDraw's load + save derives its box), so neither is content; its bags are."""
    tree = E.open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_dummy").tree
    po = tree[2]
    assert po.type_code == 0xFFFF

    def mirrored(r):
        r.slots[2] = (3, (-1.0, 0.0, 0.0, 1.0, 199.0, 450.0))
        r.anchor = (r.anchor[0] - 300, r.anchor[1] + 8)
        r.rect1 = (r.anchor[0], r.anchor[1], r.anchor[0] + 1, r.anchor[1] + 1)
        r.rect2 = r.rect1

    def valued(r):
        r.entries[0][1].set("TAG", "Pump1")

    assert CC.same_content(po, _rec(po, mirrored))
    assert not CC.same_content(po, _rec(po, valued))


def test_a_button_box_follows_the_rounding_rule(fx_install):
    """A button's box is also its WIDTH / HEIGHT and the end of its class section: a pixel
    there is rounding, five are content."""
    po = E.open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_goto").tree[1]
    assert po.type_code == GP.BUTTON_TC

    def grown(d):
        def change(r):
            x0, y0, x1, y1 = r.rect1
            r.rect1 = (x0, y0, x1 + d, y1)
            r.rect2 = (r.rect2[0], r.rect2[1], r.rect2[2] + d, r.rect2[3])
            for b in r.entries[0][1:]:
                b.pairs = [(k, str(int(v) + d) if k.upper() == "WIDTH" else v) for k, v in b.pairs]
            r.cls = r.cls[:-8] + struct.pack("<4h", *r.rect1)

        return change

    assert CC.same_content(po, _rec(po, grown(1)))
    assert not CC.same_content(po, _rec(po, grown(5)))


def test_an_object_with_an_input_command_keeps_its_own_box(fx_install):
    """fx_pump's rectangle owns its touch command as a child record: it has children, but its
    box is its own (only a group's or symbol set's is its members' union), so a move is
    content."""
    tree = E.open_element(_genies(fx_install), "fx_pump").tree
    po = tree[1]
    assert po.class_flag and po.type_code not in CC.UNION_TCS
    assert tree[2].type_code == 0x1010  # (its touch command)

    def moved(d):
        def change(r):
            r.anchor = (r.anchor[0] + d, r.anchor[1])
            r.rect1 = (r.rect1[0] + d, r.rect1[1], r.rect1[2] + d, r.rect1[3])
            r.rect2 = (r.rect2[0] + d, r.rect2[1], r.rect2[2] + d, r.rect2[3])

        return change

    assert CC.same_content(po, _rec(po, moved(1)))
    assert not CC.same_content(po, _rec(po, moved(40)))
    # a group's box is its members' union: not compared (its members are)
    root = tree[0]
    assert root.type_code in CC.UNION_TCS
    assert CC.same_content(root, _rec(root, moved(40)))


def test_a_scale_is_compared_up_to_float_noise(fx_install):
    """The linear part of a slot: a one-ULP drift is noise (a turned copy composed twice),
    a real scale is content."""
    objs = _copy_records(fx_install)

    def with_scale(a):
        def change(r):
            _m, (_a, b, c, d, h, k) = r.slots[1]
            r.slots[1] = (3, (a, b, c, 1.0, h, k))

        return change

    base = list(objs)
    base[1] = _rec(objs[1], with_scale(1.0))
    for a, differs in ((1.0 + 2.2e-16, False), (1.001, True)):
        other = list(objs)
        other[1] = _rec(objs[1], with_scale(a))
        assert other[1].body_raw != base[1].body_raw
        assert bool(CC.changed_copies(base, other)) is differs, a


# ------------------------------------------- compose-all: what a save normalises is no reason
def test_compose_all_leaves_an_element_whose_save_only_normalises(fx_install):
    """n2's size words grown, fx_lamp's too (it places symbols only): a refresh save changes
    both, and compose-all leaves them alone."""
    from ctgedit.editing import ElementDocument
    from ctgedit.io import genieusage as GU

    _poke(_nest(fx_install), "n2", _grow_size_words)
    _poke(_genies(fx_install), "fx_lamp", _grow_size_words)
    dirs = [fx_install.project("FxLib")]
    assert not {"fxnest.n2", "fxgenie.fx_lamp"} & set(_labels(GU.chain_plan(dirs)))
    assert _refresh_changes(_nest(fx_install), "n2") > 0
    assert _refresh_changes(_genies(fx_install), "fx_lamp") > 0
    lamp = ElementDocument.open_element(_genies(fx_install), "fx_lamp")
    assert lamp.parsed.page.link_count and not GP.nested_keys(lamp.parsed.page.objects)
    report: dict = {}
    n2 = ElementDocument.open_element(_nest(fx_install), "n2")
    assert n2.nested_update_ops(every=True, report=report) == ([], None)
    assert report["changed_copies"] == [] and not report["stale_nested"]
    assert lamp.nested_update_ops(every=True) == ([], None)
    plan = GU.chain_plan(dirs, compose_all=True)
    assert not {"fxnest.n2", "fxgenie.fx_lamp"} & set(_labels(plan)), _labels(plan)
    assert not plan.not_compared and not plan.not_composed


def test_compose_again_in_a_tab_leaves_a_normalisation_alone(window, fx_install):
    from ctgedit.ui import notify
    from ctgedit.ui.prefs.settings import set_genie_save_pages

    set_genie_save_pages("never")
    _poke(_nest(fx_install), "n2", _grow_size_words)
    _poke(_genies(fx_install), "fx_lamp", _grow_size_words)
    window._open_element(_genies(fx_install), "fx_lamp")
    assert not window.doc.dirty and window.act_compose_nested.isEnabled()
    window.act_compose_nested.trigger()
    assert not window.doc.dirty and not window.doc._undo
    text = notify.centre(window).newest_first()[0].text
    assert "No genie is nested in" in text and "Save to keep it" not in text
    window._open_element(_nest(fx_install), "n2")
    window.act_compose_nested.trigger()
    assert not window.doc.dirty and not window.doc._undo
    assert "already matches" in notify.centre(window).newest_first()[0].text


# ----------------------------------------- compose-all: an element out of date by its stamps
def _host(
    fx, name="hh", genies=(("fxnest", "n0", {"Label": "L"}), ("fxgenie", "fx_value", {"Description": "D"}))
):
    """A genie of FxLib's mylib.ctm placing ``genies``."""
    from ctgedit.editing import ElementDocument, libraries_changed

    mine = os.path.join(fx.project("FxLib"), "mylib.ctm")
    d = ElementDocument.new_element(mine, name, "genie")
    d.create_object("text", (0, 0, 80, 16), text="HH")
    for i, (lib, el, params) in enumerate(genies):
        d.place_library_element(lib, el, "ctm", (10, 40 + 60 * i), params=params)
    d.save()
    libraries_changed()
    return mine


def _edit_text(lib, name, text) -> None:
    from ctgedit.editing import ElementDocument, libraries_changed

    g = ElementDocument.open_element(lib, name)
    t = next(i for i, o in enumerate(g.page.objects) if o.parent is None and g.objects[i].type_code == 0x05)
    assert g.set_text(t, text)
    g.save()
    libraries_changed()


def _save_keeping_stamps_current(lib, name) -> None:
    """Save with the nested copies as stored and the import table re-stamped as current
    (copies out of date with current stamps)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    doc = ElementDocument.open_element(lib, name)
    changed = ElementDocument._changed_nested
    try:
        ElementDocument._changed_nested = lambda self, res: frozenset()
        doc.move_objects([0], 1, 0)  # (an edit of its own: the save writes)
        doc.save()
    finally:
        ElementDocument._changed_nested = changed
    libraries_changed()


def _stored_has(lib, name, mark: bytes) -> bool:
    return any(mark in writer.object_payload(o) for o in E.open_element(lib, name).tree)


def test_an_element_out_of_date_by_its_stamps_gets_every_copy_composed(fx_install):
    from ctgedit.editing import ElementDocument
    from ctgedit.io import genieusage as GU

    fx = fx_install
    mine = _host(fx)
    _edit_text(_genies(fx), "fx_value", "%Description% EDITED")
    _save_keeping_stamps_current(mine, "hh")  # (its copy of fx_value kept)
    _edit_text(_nest(fx), "n0", "%Label% CHANGED")  # (out of date by n0's stamp now)
    assert ElementDocument.open_element(mine, "hh").nested_genie_changes() == {("fxnest", "n0")}
    assert not _stored_has(mine, "hh", b"EDITED")
    dirs = [fx.project("FxLib")]
    plan = GU.chain_plan(dirs, compose_all=True)
    hh = next(u for u in plan.elements if u.ref.label == "mylib.hh")
    assert hh.status == GU.OUT_OF_DATE and hh.compose_all
    # (every genie or template the plan saves composes every copy)
    assert all(
        u.compose_all for u in plan.elements if os.path.splitext(u.ref.path)[1].lower() in (".ctm", ".ctt")
    )
    rep = GU.run_update(GU.UpdatePlan(plan.elements, [], plan.skipped))
    assert "mylib.hh" in rep.elements_saved, (rep.elements_saved, rep.skipped, rep.failed)
    assert _stored_has(mine, "hh", b"EDITED") and _stored_has(mine, "hh", b"CHANGED")
    again = GU.chain_plan(dirs, compose_all=True)
    assert "mylib.hh" not in _labels(again), _labels(again)
    # without the box the stamps alone decide, as before
    _edit_text(_nest(fx), "n0", "%Label% AGAIN")
    plain = GU.chain_plan(dirs)
    assert not any(u.compose_all for u in plain.elements)


# --------------------------------------------- compose-all: what it could not do is said
def _add_text_to_n0(fx) -> None:
    """n0 gains a text: its copies no longer line up (composed structurally)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(_nest(fx), "n0")
    d.create_object("text", (10, 60, 120, 76), text="NEWOBJ")
    d.save()
    libraries_changed()


def test_a_copy_compose_all_could_not_compose_is_in_the_report(fx_install, monkeypatch):
    from ctgedit.io import genieusage as GU

    fx = fx_install
    mine = _host(fx)
    _host(fx, "h3", genies=(("fxnest", "n0", {"Label": "L"}),))
    _add_text_to_n0(fx)
    _edit_text(_genies(fx), "fx_value", "%Description% EDITED")
    _save_keeping_stamps_current(mine, "hh")
    _save_keeping_stamps_current(mine, "h3")

    def fail(*_a, **_k):
        raise ValueError("cannot")

    monkeypatch.setattr(GP, "_build_nested_copy", fail)
    dirs = [fx.project("FxLib")]
    plan = GU.chain_plan(dirs, compose_all=True)
    # hh: its copy of fx_value differs, so it is saved; h3's only difference cannot be
    # composed, so it is left and named
    assert "mylib.hh" in _labels(plan) and "mylib.h3" not in _labels(plan)
    assert ("mylib.h3", ["fxnest.n0"]) in plan.not_composed
    rep = GU.run_update(GU.UpdatePlan(plan.elements, [], plan.skipped))
    assert "mylib.hh" in rep.elements_saved
    assert ("mylib.hh", ["fxnest.n0"]) in rep.not_composed, rep.not_composed
    assert _stored_has(mine, "hh", b"EDITED") and not _stored_has(mine, "hh", b"NEWOBJ")


def test_a_genie_compose_all_could_not_compare_is_in_the_plan(fx_install, monkeypatch):
    from ctgedit.editing import ElementDocument
    from ctgedit.io import genieusage as GU

    real = ElementDocument.nested_update_ops

    def boom(self, every=False, report=None):
        if self.element_name == "n2":
            raise ValueError("boom")
        return real(self, every=every, report=report)

    monkeypatch.setattr(ElementDocument, "nested_update_ops", boom)
    plan = GU.chain_plan([fx_install.project("FxLib")], compose_all=True)
    assert plan.not_compared == ["fxnest.n2: boom"]
    assert "fxnest.n2: boom" in plan.problems


def test_the_chain_box_names_what_compose_all_left():
    from ctgedit.io import genieusage as GU

    chain = GU.UpdateUsersReport(
        elements_saved=["mylib.hh"],
        not_composed=[("mylib.hh", ["fxnest.n0"]), ("mylib.h3", ["fxnest.n0", "fxa.b"])],
        not_compared=["fxnest.n2: boom"],
    )
    lines = MainWindow._chain_summary(chain)
    assert lines == [
        "1 library genie saved again first: mylib.hh.",
        "Nested copies that could not be composed again and stay as stored: mylib.hh (its "
        "copy of fxnest.n0), mylib.h3 (its copy of fxnest.n0, fxa.b).",
        "Not compared with their libraries, because of an error (the log says why): fxnest.n2.",
    ]


def test_update_pages_carries_the_compare_s_findings_into_the_box(tmp_path, monkeypatch):
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from test_update_pages_chain import _run
    from ctgedit.io import genieusage as GU

    b = GU.Ref("element", str(tmp_path / "Proj" / "chainlib.ctm"), "b", "Proj")
    plan = GU.UpdatePlan(
        [GU.User(b, [], (), True, GU.OUT_OF_DATE)],
        [],
        problems=["chainlib.c: boom"],
        not_compared=["chainlib.c: boom"],
        not_composed=[("chainlib.d", ["fxnest.n0"])],
    )
    _calls, shown, _w = _run(tmp_path, monkeypatch, True, plan=plan)
    text = shown[-1]
    assert (
        "Nested copies that could not be composed again and stay as stored: chainlib.d "
        "(its copy of fxnest.n0)." in text
    )
    assert "Not compared with their libraries, because of an error (the log says why): chainlib.c." in text
