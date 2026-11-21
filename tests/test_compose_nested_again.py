"""Composing nested genie copies again."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from ctgedit.format import element as E  # noqa: E402
from ctgedit.format import genie_place as GP  # noqa: E402
from ctgedit.format import writer  # noqa: E402


def _lib(fx):
    return fx.file("FxLib", "fxnest.ctm")


def _tree(fx, name: str, lib: str = ""):
    return E.open_element(lib or _lib(fx), name).tree


def _has(records, mark: bytes) -> int:
    return sum(mark in writer.object_payload(o) for o in records)


def _texts(records):
    """(TXT_OFF value, rect1) of every text record."""
    out = []
    for p in records:
        if p.type_code == 0x05:
            r = GP.decode(p)
            out.append((dict(r.entries[0][1].pairs).get("TXT_OFF") if r.entries else None, tuple(r.rect1)))
    return out


def _add_text_to_n0(fx, caption: str = "NEWOBJ") -> None:
    """n0 gains a text (its record count changes): its copies no longer line up."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(_lib(fx), "n0")
    d.create_object("text", (10, 60, 120, 76), text=caption)
    d.save()
    libraries_changed()


def _save_keeping_stamps_current(doc) -> None:
    """Save ``doc`` with its nested copies as stored and its import table re-stamped as
    current (the file the command is for)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    changed = ElementDocument._changed_nested
    try:
        ElementDocument._changed_nested = lambda self, res: frozenset()
        doc.move_objects([0], 1, 0)  # (an edit of its own: the save writes)
        doc.save()
    finally:
        ElementDocument._changed_nested = changed
    libraries_changed()


# --------------------------------------------- Compose Again: structurally changed copies
def test_compose_again_composes_a_copy_that_no_longer_lines_up(fx_install):
    from ctgedit.editing import ElementDocument

    lib = _lib(fx_install)
    _add_text_to_n0(fx_install)
    _save_keeping_stamps_current(ElementDocument.open_element(lib, "n1"))
    n1 = ElementDocument.open_element(lib, "n1")
    assert not n1.nested_genie_changes() and _has(n1.parsed.page.objects, b"NEWOBJ") == 0
    report: dict = {}
    assert n1.update_nested_genies(label="compose nested genies again", every=True, report=report) > 0
    assert _has(n1.parsed.page.objects, b"NEWOBJ") == 1 and n1.dirty
    assert not report["stale_nested"]
    n1.save()
    assert _has(_tree(fx_install, "n1"), b"NEWOBJ") == 1
    # composed: nothing left to do
    again = ElementDocument.open_element(lib, "n1")
    assert again.nested_update_ops(every=True) == ([], None)


def test_compose_again_never_stamps_current_a_copy_it_left(fx_install):
    """Two nested genies changed, one of them in its record count: Compose Again composes
    the other and never writes the current stamp for the one it left."""
    from ctgedit.editing import ElementDocument, libraries_changed

    fx = fx_install
    folder = fx.project("FxLib")
    mine = os.path.join(folder, "mylib.ctm")
    d = ElementDocument.new_element(mine, "hh", "genie")
    d.create_object("text", (0, 0, 80, 16), text="HH")
    d.place_library_element("fxnest", "n0", "ctm", (10, 40), params={"Label": "L"})
    d.place_library_element("fxgenie", "fx_value", "ctm", (10, 100), params={"Description": "D"})
    d.save()
    libraries_changed()
    _add_text_to_n0(fx)
    g = ElementDocument.open_element(os.path.join(folder, "fxgenie.ctm"), "fx_value")
    t = next(i for i, o in enumerate(g.page.objects) if o.parent is None and g.objects[i].type_code == 0x05)
    g.set_text(t, "%Description% EDITED")
    g.save()
    libraries_changed()
    hh = ElementDocument.open_element(mine, "hh")
    assert hh.nested_genie_changes() == {("fxnest", "n0"), ("fxgenie", "fx_value")}
    assert hh.update_nested_genies(every=True) > 0
    hh.save()
    libraries_changed()
    stored = E.open_element(mine, "hh").tree
    assert _has(stored, b"NEWOBJ") == 1 and _has(stored, b"EDITED") == 1
    assert not ElementDocument.open_element(mine, "hh").nested_genie_changes()


def test_a_copy_compose_again_cannot_compose_is_said_and_keeps_its_stamp(fx_install, monkeypatch):
    from ctgedit.editing import ElementDocument

    def fail(*_a, **_k):
        raise ValueError("cannot")

    _add_text_to_n0(fx_install)
    monkeypatch.setattr(GP, "_build_nested_copy", fail)
    n1 = ElementDocument.open_element(_lib(fx_install), "n1")
    report: dict = {}
    n1.update_nested_genies(every=True, report=report)
    assert report["stale_nested"] == {("fxnest", "n0")}
    n1.save()
    # (its import table keeps n0's old stamp: still reported, not marked current)
    assert ElementDocument.open_element(_lib(fx_install), "n1").nested_genie_changes() == {("fxnest", "n0")}


def test_compose_again_names_a_genie_whose_library_cannot_be_read(fx_install):
    """A nested genie whose library is gone cannot be composed: the command names it."""
    from ctgedit.editing import ElementDocument, libraries_changed

    folder = fx_install.project("FxLib")
    mine = os.path.join(folder, "mylib.ctm")
    d = ElementDocument.new_element(mine, "hh", "genie")
    d.create_object("text", (0, 0, 80, 16), text="HH")
    d.place_library_element("fxgenie", "fx_value", "ctm", (10, 100), params={"Description": "D"})
    d.save()
    os.remove(os.path.join(folder, "fxgenie.ctm"))
    libraries_changed()
    hh = ElementDocument.open_element(mine, "hh")
    report: dict = {}
    assert hh.update_nested_genies(every=True, report=report) == 0
    assert report["stale_nested"] == {("fxgenie", "fx_value")}


def test_a_save_with_no_import_table_composes_a_changed_copy(fx_install, monkeypatch):
    """No import table to tell the changed genies (_changed_nested None): every copy is
    composed, the one that no longer lines up too."""
    from ctgedit.editing import ElementDocument

    _add_text_to_n0(fx_install)
    monkeypatch.setattr(ElementDocument, "_changed_nested", lambda self, res: None)
    n1 = ElementDocument.open_element(_lib(fx_install), "n1")
    n1.save(refresh=True)
    assert _has(_tree(fx_install, "n1"), b"NEWOBJ") == 1


def test_compose_again_in_the_window_says_what_it_could_not_compose(fx_install, monkeypatch):
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui import notify
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings, set_genie_save_pages

    set_genie_save_pages("never")
    app_settings().setValue("opt/auto_update_genies", False)  # (the tab opens as stored)
    _add_text_to_n0(fx_install)
    w = MainWindow()
    try:
        w._open_element(_lib(fx_install), "n1")
        doc = w.doc
        doc.dirty = False
        monkeypatch.setattr(
            GP, "_build_nested_copy", lambda *a, **k: (_ for _ in ()).throw(ValueError("cannot"))
        )
        w.act_compose_nested.trigger()
        text = notify.centre(w).newest_first()[0].text
        assert "fxnest.n0 could not be composed" in text and "already matches" not in text
    finally:
        for e in list(w._docs):
            e["doc"].dirty = False
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)


def test_the_chain_never_calls_a_host_it_could_not_compose_current(fx_install, monkeypatch):
    """The chain after a genie save: a host whose copy could not be composed again writes
    nothing (it keeps the old stamp) and is not counted as 'already matched what it places'."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import libraries_changed

    _add_text_to_n0(fx_install)
    monkeypatch.setattr(GP, "_build_nested_copy", lambda *a, **k: (_ for _ in ()).throw(ValueError("cannot")))
    res = GW.update_closed_pages(_lib(fx_install), "n0", elements=True)
    libraries_changed()
    assert "fxnest.n1" not in (res.get("elements_current") or []), res
    assert any(
        x.startswith("fxnest.n1: its copy of fxnest.n0 could not be composed")
        for x in res.get("elements_skipped") or []
    ), res


# ------------------------------------------------------------- a genie redrawn in full
def test_a_copy_where_only_the_root_matches_is_built_in_full(fx_install):
    """n0's text replaced by a new one (a new handle): a copy where only the root matches is
    built in full."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import geometry as G

    lib = _lib(fx_install)

    def handles():
        return [G.get_handle(p) for p in _tree(fx_install, "n0")[1:]]

    old = handles()
    d = ElementDocument.open_element(lib, "n0")
    t = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    # a new text (a handle of its own) in a new group, the old text deleted: nothing but
    # the root matches by AN and class
    new = d.create_object("text", (10, 10, 120, 26), text="%Label% NEW")
    d.create_object("rectangle", (10, 30, 40, 50))
    d.group_objects([new, len(d.page.objects) - 1])
    d.delete_objects([t])
    d.save()
    libraries_changed()
    assert [p.type_code for p in _tree(fx_install, "n0")] == [0x08, 0x08, 0x05, 0x0F]
    assert not set(handles()) & set(old)
    n1 = ElementDocument.open_element(lib, "n1")
    assert n1.nested_genie_changes() == {("fxnest", "n0")}
    assert n1.update_nested_genies() > 0
    n1.save()
    libraries_changed()
    # the copy is the new n0, its text bound as the copy binds n0 ('%Name% L1')
    recs = _tree(fx_install, "n1")
    assert [p.type_code for p in recs] == [0x08, 0x05, 0x08, 0x08, 0x05, 0x0F]
    assert [v for v, _r in _texts(recs)][1] == "%Name% L1 NEW"
    assert not ElementDocument.open_element(lib, "n1").nested_genie_changes()
    # a page composition of the host shows it too
    assert len(GP.load_element(lib, "n1", compose=True).records) == 6


# ------------------------------------------------------------ a group in the definition
def test_a_new_group_never_takes_a_nested_genie_roots_record(fx_install):
    """n2's text and its copy of n1 grouped in n2's definition: in n3's copy of n2 the new
    group never takes the stale n1 root (both type 0x08), so the n1 copy keeps its place and
    its binding."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx_install)
    before = _texts(_tree(fx_install, "n3"))
    d = ElementDocument.open_element(lib, "n2")
    root = next(k for k, o in enumerate(d.page.objects) if o.kind.name == "GENIE")
    txt = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    d.group_objects([txt, root])
    d.save()
    libraries_changed()
    n3 = ElementDocument.open_element(lib, "n3")
    assert n3.nested_genie_changes() == {("fxnest", "n2")}
    assert n3.update_nested_genies() > 0
    n3.save()
    libraries_changed()
    recs = _tree(fx_install, "n3")
    after = _texts(recs)
    assert after == before, (before, after)  # (same places, same bindings)
    roots = [p for p in recs if p.class_flag and p.subtype_size & 0x200]
    assert len(roots) == 3  # (n2, n1 and n0 copies, one each)
    groups = [p for p in recs if p.type_code == 0x08 and not p.subtype_size & 0x200 and p.an_str != "-1"]
    assert len(groups) == 1 and GP.decode(groups[0]).slots[1][0] == 0  # (no Local)


# ------------------------------------------------- a Local the definition took in
def test_cut_link_inside_a_genie_moves_the_member_once_in_its_hosts(fx_install, monkeypatch):
    """fx_box placed in n0 at (80, 10), then Cut Link on it in n0: the definition holds the
    placement in the box's coordinates and drops its Local; n1's copy drops the old Local
    too, so the box moves once."""
    import itertools
    from ctgedit.editing import ElementDocument, libraries_changed

    c = itertools.count(E.ctdraw_timestamp())  # (each save a stamp of its own)
    monkeypatch.setattr(E, "ctdraw_timestamp", lambda: next(c))
    lib = _lib(fx_install)

    def compose_n1():
        n1 = ElementDocument.open_element(lib, "n1")
        assert n1.nested_genie_changes() == {("fxnest", "n0")}
        n1.update_nested_genies()
        n1.save()
        libraries_changed()

    def boxes():
        return [tuple(GP.decode(p).rect1) for p in _tree(fx_install, "n1") if p.type_code == 0x0F]

    d = ElementDocument.open_element(lib, "n0")
    d.place_library_element("fxsym", "fx_box", "ctl", (80, 10))
    d.save()
    libraries_changed()
    compose_n1()
    placed = boxes()
    assert placed == [(80, 40, 120, 70)], placed
    d = ElementDocument.open_element(lib, "n0")
    i = next(
        k for k, o in enumerate(d.parsed.page.objects) if k > 0 and o.type_code == 0x08 and o.subtype_size & 1
    )
    assert d.break_library_link(i) is not None
    d.save()
    libraries_changed()
    compose_n1()
    assert boxes() == placed  # (where it was, not 80 px further)
    recs = _tree(fx_install, "n1")
    group = recs[next(k for k, p in enumerate(recs) if p.type_code == 0x0F) - 1]
    assert GP.decode(group).slots[1][0] == 0  # (the copy took the element's slots)


def test_a_members_own_local_is_kept_where_the_definition_never_had_one():
    """The LIVE rule (a state set kept T(2, 0)): a stored Local stays where the
    current element's record has none and keeps none of its own kind; one the definition
    placed (a group copy over a cut-linked plain group) goes, whatever the boxes say."""
    from ctgedit.format import placement as P
    from ctgedit.format.page import PageObject

    rebuilt = GP.Rec.__new__(GP.Rec)
    rebuilt.slots = [(0, P.IDENT), (0, P.IDENT), (1, (1.0, 0.0, 0.0, 1.0, 0.0, 30.0))]

    def element_record(tc, dw):
        return PageObject(
            index=0,
            record_offset=0,
            payload_size=0,
            next_link=0,
            class_flag=1,
            type_code=tc,
            subtype_size=dw,
            an_str="1",
            body_offset=0,
            body_raw=b"",
        )

    own = GP.Rec.__new__(GP.Rec)
    own.tc, own.dw = 0x0C, 0x18  # (a symbol set)
    own.slots = [(0, P.IDENT), (1, (1.0, 0.0, 0.0, 1.0, 2.0, 0.0)), (1, (1.0, 0.0, 0.0, 1.0, 2.0, 30.0))]
    assert GP._own_local(own, rebuilt, element_record(0x0C, 0x18))
    baked = GP.Rec.__new__(GP.Rec)
    baked.tc, baked.dw = 0x08, 0x08
    baked.slots = [(0, P.IDENT), (1, (1.0, 0.0, 0.0, 1.0, 80.0, 10.0)), (1, (1.0, 0.0, 0.0, 1.0, 80.0, 40.0))]
    assert not GP._own_local(baked, rebuilt, element_record(0x08, 0x0A))


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
