"""A library element follows a change to a genie nested in it."""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from ctgedit.format import element as E  # noqa: E402
from ctgedit.format import writer  # noqa: E402

MARK = b"CHANGED"


def _qt():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _lib(fx):
    return fx.file("FxLib", "fxnest.ctm")


def _marked(records) -> int:
    """How many records carry n0's edit."""
    return sum(MARK in writer.object_payload(o) for o in records)


def _stored(fx, name: str, lib: str = "") -> int:
    pe = E.open_element(lib or _lib(fx), name)
    return _marked(pe.tree)


def _page_marked(path: str) -> int:
    from ctgedit.format.page import parse_ctg_bytes

    with open(path, "rb") as fh:
        return _marked(parse_ctg_bytes(fh.read()).page.objects)


def _edit_n0(fx) -> None:
    """n0's text becomes '%Label% CHANGED', saved (not through a window)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(_lib(fx), "n0")
    i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    assert d.set_text(i, "%Label% CHANGED")
    d.save()
    libraries_changed()
    assert _stored(fx, "n0") == 1 and _stored(fx, "n1") == 0


@pytest.fixture
def window(fx_install, monkeypatch):
    """A main window whose prompts are answered by ``answers`` (button text -> click; the
    asked texts in ``asked``), Settings 'Update changed genies' on, the save chain asked."""
    _qt()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings, set_genie_save_pages
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    app_settings().setValue("opt/auto_update_genies", True)
    set_genie_save_pages("ask")
    asked, answers = [], {"click": None}

    def prompt(box):
        asked.append(box.text())
        want = answers["click"]
        for b in box.buttons():
            if want and b.text() == want:
                b.click()

    monkeypatch.setattr(MainWindow, "_exec_prompt", staticmethod(prompt))
    w = MainWindow()
    w.asked, w.answers = asked, answers
    try:
        yield w
    finally:
        for e in list(w._docs):
            e["doc"].dirty = False
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        UpdatePagesRun.users_busy = ""
        UpdatePagesRun.users_queue.clear()
        UpdatePagesRun.chain_busy = False


# ------------------------------------------------------------------- the element itself
def test_an_unedited_save_of_a_stale_host_writes_the_new_copy(fx_install):
    from ctgedit.editing import ElementDocument

    _edit_n0(fx_install)
    host = ElementDocument.open_element(_lib(fx_install), "n2")
    assert ("fxnest", "n0") in host.nested_genie_changes()
    assert _marked(host.parsed.page.objects) == 0  # (the stored copies, as read)
    host.save()
    assert host.save_files and _stored(fx_install, "n2") == 1
    assert _marked(host.parsed.page.objects) == 1 and not host.dirty


def test_the_update_keeps_unsaved_edits_as_one_undo_step(fx_install):
    from ctgedit.editing import ElementDocument

    _edit_n0(fx_install)
    d = ElementDocument.open_element(_lib(fx_install), "n3")
    text = next(
        k
        for k, o in enumerate(d.page.objects)
        if o.parent is None and d.parsed.page.objects[k].type_code == 0x05
    )
    d.move_objects([text], 7, 0)
    moved = writer.object_payload(d.parsed.page.objects[text])
    assert d.update_nested_genies() > 0
    assert _marked(d.parsed.page.objects) == 1
    assert writer.object_payload(d.parsed.page.objects[text]) == moved
    assert [c.label for c in d._undo] == ["move", "update genies from library"]
    d.undo()
    assert _marked(d.parsed.page.objects) == 0
    d.redo()
    d.save()
    assert _stored(fx_install, "n3") == 1


def test_the_workers_answer_equals_the_update_in_the_editor(fx_install, tmp_path):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument
    from ctgedit.ui.jobs.genie_jobs import apply_answer

    _edit_n0(fx_install)
    a = ElementDocument.open_element(_lib(fx_install), "n4")
    req = GW.write_request(
        str(tmp_path), a.path, a.serialize(), {("fxnest", "n0"): None}, element=a.nested_update_request()
    )
    with open(req, encoding="utf-8") as fh:
        r = json.load(fh)
    with open(req + ".page", "rb") as fh:
        ans = GW.compute_element(r["element"], fh.read())
    assert ans["format"] == GW.FORMAT and ans["ops"]
    assert apply_answer(a, ans) > 0
    b = ElementDocument.open_element(_lib(fx_install), "n4")
    assert b.update_nested_genies() > 0
    assert [writer.object_payload(o) for o in a.parsed.page.objects] == [
        writer.object_payload(o) for o in b.parsed.page.objects
    ]
    assert _marked(a.parsed.page.objects) == 1
    # another block in the library than the tab opened: the worker refuses
    ElementDocument.open_element(_lib(fx_install), "n4").save()
    with pytest.raises(ValueError):
        ElementDocument.for_nested_update(r["element"], b"")


# ------------------------------------------------------------------- tabs
def test_opening_a_stale_element_updates_it_in_its_tab(window, fx_install):
    _edit_n0(fx_install)
    for name in ("n2", "n4"):
        window._open_element(_lib(fx_install), name)
        doc = window.doc
        assert doc.element_name == name
        assert _marked(doc.parsed.page.objects) == 1, name
        assert doc.dirty and doc._undo[-1].label == "update genies from library"
        assert doc.ui.auto_changes and "fxnest.n0" in doc.ui.auto_changes[0][0]
        assert _stored(fx_install, name) == 0  # (nothing written until Save)
    from ctgedit.ui.prefs.settings import set_genie_save_pages

    set_genie_save_pages("never")
    assert window.save()
    assert _stored(fx_install, "n4") == 1 and not window.doc.dirty


def test_the_option_off_leaves_an_opened_element_alone(window, fx_install):
    from ctgedit.ui.prefs.settings import app_settings

    _edit_n0(fx_install)
    app_settings().setValue("opt/auto_update_genies", False)
    window._open_element(_lib(fx_install), "n2")
    assert _marked(window.doc.parsed.page.objects) == 0
    assert not window.doc.dirty and not window.doc._undo


def test_a_tab_shown_again_after_a_library_save_updates(window, fx_install, fx_page):
    window._open_element(_lib(fx_install), "n2")
    host = window.doc
    assert not host.dirty  # (nothing stale yet)
    window.open_document(fx_page("FxShapes"))
    _edit_n0(fx_install)  # (saved elsewhere)
    assert _marked(host.parsed.page.objects) == 0
    k = next(i for i, e in enumerate(window._docs) if e["doc"] is host)
    window.doc_tabs.setCurrentIndex(k)
    assert window.doc is host
    assert _marked(host.parsed.page.objects) == 1 and host.dirty


def test_an_element_tab_with_edits_follows_the_nested_save(window, fx_install):
    from ctgedit.ui.prefs.settings import set_genie_save_pages

    set_genie_save_pages("never")
    window._open_element(_lib(fx_install), "n3")
    host = window.doc
    text = next(
        k
        for k, o in enumerate(host.page.objects)
        if o.parent is None and host.parsed.page.objects[k].type_code == 0x05
    )
    host.move_objects([text], 5, 0)
    moved = writer.object_payload(host.parsed.page.objects[text])
    window._open_element(_lib(fx_install), "n0")
    n0 = window.doc
    i = next(k for k, o in enumerate(n0.parsed.page.objects) if o.type_code == 0x05)
    n0.set_text(i, "%Label% CHANGED")
    assert window.save()
    assert _marked(host.parsed.page.objects) == 1
    assert writer.object_payload(host.parsed.page.objects[text]) == moved
    assert [c.label for c in host._undo] == ["move", "update genies from library"]


def test_the_page_placing_the_host_follows_its_save(window, fx_install, fx_page):
    from ctgedit.ui.prefs.settings import set_genie_save_pages

    set_genie_save_pages("never")
    window.open_document(fx_page("FxMembers"))
    page = window.doc
    assert page.find_objects_by_name("FxNestedHost")
    _edit_n0(fx_install)
    window._open_element(_lib(fx_install), "n2")
    assert window.save()  # (the update of its open, saved)
    assert _stored(fx_install, "n2") == 1
    assert _marked(page.parsed.page.objects) == 1 and page.dirty
    # (n1 still quotes the old n0 until it is saved: once done, not composed again)
    assert window._update_stale_genies(page) == []


# ------------------------------------------------------------------- the chain after a save
def _order(labels):
    return {x: i for i, x in enumerate(labels)}


def test_the_chain_saves_the_genies_deepest_first_then_the_pages(fx_install, fx_page, tmp_path):
    from ctgedit.workers import genie_worker as GW

    _edit_n0(fx_install)
    # (FxGenies places n4; FxMembers places n2 but is not registered: Update Pages leaves it)
    before = {p: open(fx_page(p), "rb").read() for p in ("FxGenies", "FxMembers")}
    prog = tmp_path / "progress.json"
    res = GW.update_closed_pages(_lib(fx_install), "n0", backup=str(tmp_path / "bk"), progress_file=str(prog))
    saved = res["elements_saved"]
    o = _order(saved)
    assert o["fxnest.n1"] < o["fxnest.n2"] < o["fxnest.n3"] < o["fxnest.n4"]
    assert o["fxnest.n1"] < o["fxparts.fx_row"]
    for name in ("n1", "n2", "n3", "n4"):
        assert _stored(fx_install, name) == 1, name
    assert _stored(fx_install, "fx_row", fx_install.file("FxLib", "fxparts.ctm")) == 1
    assert fx_install.file("FxLib", "fxnest.ctm") in res["libraries"]
    assert res["updated"] == ["FxGenies"]
    assert open(fx_page("FxGenies"), "rb").read() != before["FxGenies"]
    assert _page_marked(fx_page("FxGenies")) >= 1
    assert open(fx_page("FxMembers"), "rb").read() == before["FxMembers"]
    assert json.loads(prog.read_text())["phase"] == "pages"
    # the libraries were backed up before their first write
    assert os.path.isfile(tmp_path / "bk" / "FxLib" / "fxnest.ctm")
    # everything is current now: a second run has nothing to save
    again = GW.update_closed_pages(_lib(fx_install), "n0")
    assert not again["elements_saved"] and not again["updated"]


def test_the_chain_saves_a_clean_tab_and_leaves_one_with_changes(fx_install, tmp_path):
    from ctgedit.workers import genie_worker as GW

    _edit_n0(fx_install)
    lib = _lib(fx_install)
    editors = tmp_path / "editors.txt"
    # (n2 open with no unsaved changes, n3 with some)
    editors.write_text("clean\t%s\tn2\ndirty\t%s\tn3\n" % (lib, lib), encoding="utf-8")
    n3 = E.open_element(lib, "n3").block.data
    res = GW.update_closed_pages(lib, "n0", editor_file=str(editors))
    assert res["elements_in_editor"] == ["fxnest.n2"]
    assert "fxnest.n2" not in res["elements_saved"] and _stored(fx_install, "n2") == 1
    assert any(x.startswith("fxnest.n3: it has unsaved changes") for x in res["elements_skipped"]), res
    assert "fxnest.n3" not in res["elements_saved"]
    assert E.open_element(lib, "n3").block.data == n3


def test_the_chain_stops_when_cancelled(fx_install, tmp_path):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.format import projectlock

    _edit_n0(fx_install)
    n1 = E.open_element(_lib(fx_install), "n1").block.data
    cancel = tmp_path / "cancel"
    cancel.write_text("")
    try:
        res = GW.update_closed_pages(_lib(fx_install), "n0", cancel_file=str(cancel))
    finally:
        projectlock.set_cancel(None)
    assert res["stopped"] and not res["elements_saved"] and not res["updated"]
    assert E.open_element(_lib(fx_install), "n1").block.data == n1


def _save_n0_in(window, fx):
    window._open_element(_lib(fx), "n0")
    n0 = window.doc
    i = next(k for k, o in enumerate(n0.parsed.page.objects) if o.type_code == 0x05)
    n0.set_text(i, "%Label% CHANGED")
    assert window.save()
    return n0


def test_always_runs_the_chain_after_a_genie_save(window, fx_install, fx_page):
    from ctgedit.ui.prefs.settings import set_genie_save_pages
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    window._open_element(_lib(fx_install), "n2")  # (clean: nothing stale yet)
    host = window.doc
    set_genie_save_pages("always")
    _save_n0_in(window, fx_install)
    assert not window.asked  # (not asked)
    for name in ("n1", "n2", "n3", "n4"):
        assert _stored(fx_install, name) == 1, name
    # the clean tab was saved there: it shows the stored element, unsaved changes none
    assert not host.dirty and _marked(host.parsed.page.objects) == 1
    assert not host.ui.auto_changes
    assert _page_marked(fx_page("FxGenies")) >= 1
    from PySide6 import QtWidgets

    label = window._users_job.findChild(QtWidgets.QLabel).text()
    assert "library genies" in label and "closed page" in label and "fxnest.n0" in label
    assert UpdatePagesRun.users_busy == ""


def test_the_chain_leaves_a_tab_with_unsaved_changes_and_says_so(window, fx_install):
    from ctgedit.ui.prefs.settings import set_genie_save_pages

    window._open_element(_lib(fx_install), "n2")
    host = window.doc
    text = next(
        k
        for k, o in enumerate(host.page.objects)
        if o.parent is None and host.parsed.page.objects[k].type_code == 0x05
    )
    host.move_objects([text], 5, 0)
    n2 = E.open_element(_lib(fx_install), "n2").block.data
    set_genie_save_pages("always")
    _save_n0_in(window, fx_install)
    assert E.open_element(_lib(fx_install), "n2").block.data == n2
    assert host.dirty and _marked(host.parsed.page.objects) == 1  # (its own update)
    assert _stored(fx_install, "n1") == 1
    res_text = window._users_run_details(
        {
            "elements_skipped": [
                "fxnest.n2: it has unsaved changes in the editor (save it there, then update again)"
            ]
        }
    )
    assert "Library elements left" in res_text
    from PySide6 import QtWidgets

    label = window._users_job.findChild(QtWidgets.QLabel).text()
    assert "left" in label


def test_the_prompt_names_genies_and_pages_and_never_leaves_everything(window, fx_install):
    from ctgedit.ui.prefs.settings import genie_save_pages, set_genie_save_pages

    n1 = E.open_element(_lib(fx_install), "n1").block.data
    window.answers["click"] = "Later"
    _save_n0_in(window, fx_install)
    assert (
        window.asked
        and "library genies" in window.asked[0]
        and "closed page" in window.asked[0]
        and "fxnest.n0" in window.asked[0]
    )
    assert E.open_element(_lib(fx_install), "n1").block.data == n1
    assert genie_save_pages() == "ask"
    set_genie_save_pages("never")
    window.asked.clear()
    n0 = window.doc
    i = next(k for k, o in enumerate(n0.parsed.page.objects) if o.type_code == 0x05)
    n0.set_text(i, "%Label% CHANGED TWICE")
    assert window.save()
    assert not window.asked
    assert E.open_element(_lib(fx_install), "n1").block.data == n1


def test_a_symbol_save_runs_the_same_chain(window, fx_install, fx_page):
    from PySide6 import QtWidgets

    lib = fx_install.file("FxLib", "fxsym.ctl")
    page = fx_page("FxSymbols")
    before = open(page, "rb").read()
    window._open_element(lib, "fx_box")
    sym = window.doc
    roots = [k for k, o in enumerate(sym.page.objects) if o.parent is None]
    sym.move_objects(roots, 0, 2)
    window.answers["click"] = "Update in Background"
    assert window.save()
    assert window.asked and "fxsym.fx_box" in window.asked[0]
    label = window._users_job.findChild(QtWidgets.QLabel).text()
    assert label.startswith("Updated") and "fxsym.fx_box" in label
    assert open(page, "rb").read() != before  # (it places fx_box: updated)


def test_one_run_at_a_time(window, fx_install):
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    _edit_n0(fx_install)
    n1 = E.open_element(_lib(fx_install), "n1").block.data
    UpdatePagesRun.chain_busy = True  # (Update Pages' chain is saving)
    window._open_element(_lib(fx_install), "n0")
    n0 = window.doc
    window._update_closed_pages(n0)
    assert UpdatePagesRun.users_queue  # (waits for its turn)
    assert E.open_element(_lib(fx_install), "n1").block.data == n1
    UpdatePagesRun.chain_busy = False
    window._next_users_run()
    assert not UpdatePagesRun.users_queue and _stored(fx_install, "n1") == 1
    # while a chain runs, Update Pages and Pack Libraries wait
    UpdatePagesRun.users_busy = "fxnest.n0"
    assert "fxnest.n0" in UpdatePagesRun.busy_text("Run Update Pages")
    assert UpdatePagesRun.busy_text("Pack the libraries").endswith(
        "Pack the libraries when that has finished."
    )


def _grow_n0(fx) -> None:
    """n0's text edited and duplicated (2 records -> 3), saved: its copies no longer line
    up with it."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(_lib(fx), "n0")
    i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    assert d.set_text(i, "%Label% CHANGED")
    d.duplicate_objects([i])
    d.save()
    libraries_changed()
    assert _stored(fx, "n0") == 2


def _move_text(doc, dx: int) -> None:
    """The element's top-level text moved by ``dx``."""
    text = next(
        k
        for k, o in enumerate(doc.page.objects)
        if o.parent is None and doc.parsed.page.objects[k].type_code == 0x05
    )
    doc.move_objects([text], dx, 0)


def test_an_object_added_to_the_nested_genie_reaches_its_hosts(fx_install):
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx_install)
    _grow_n0(fx_install)
    n1 = ElementDocument.open_element(lib, "n1")
    before = len(n1.parsed.page.objects)
    assert n1.nested_genie_changes() == {("fxnest", "n0")}
    assert n1.update_nested_genies() > 0
    assert len(n1.parsed.page.objects) == before + 1 and _marked(n1.parsed.page.objects) == 2
    # n2 opened while n1 is not saved again yet: its copy of n1 holds the new n0 too
    n2 = ElementDocument.open_element(lib, "n2")
    before2 = len(n2.parsed.page.objects)
    assert n2.update_nested_genies() > 0
    assert len(n2.parsed.page.objects) == before2 + 1 and _marked(n2.parsed.page.objects) == 2
    n1.save()
    libraries_changed()
    assert n1.save_files and _stored(fx_install, "n1") == 2
    assert not ElementDocument.open_element(lib, "n1").nested_genie_changes()
    # an unedited save of a stale host writes the new copy
    n3 = ElementDocument.open_element(lib, "n3")
    n3.save()
    libraries_changed()
    assert n3.save_files and _stored(fx_install, "n3") == 2


def test_the_chain_brings_an_added_object_to_every_host(fx_install, fx_page):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument

    _grow_n0(fx_install)
    res = GW.update_closed_pages(_lib(fx_install), "n0")
    for name in ("n1", "n2", "n3", "n4"):
        assert _stored(fx_install, name) == 2, name
        assert not ElementDocument.open_element(_lib(fx_install), name).nested_genie_changes()
    assert _stored(fx_install, "fx_row", fx_install.file("FxLib", "fxparts.ctm")) == 2
    assert "FxGenies" in res["updated"] and _page_marked(fx_page("FxGenies")) >= 2


def test_a_copy_that_cannot_be_composed_stays_out_of_date(fx_install, monkeypatch):
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import genie_place as GP

    _grow_n0(fx_install)
    monkeypatch.setattr(GP, "_recompose_nested_copies", lambda *a, **k: 0)
    n1 = ElementDocument.open_element(_lib(fx_install), "n1")
    n1.save()
    libraries_changed()
    # (its import table keeps n0's old stamp: still reported, not marked current)
    assert ElementDocument.open_element(_lib(fx_install), "n1").nested_genie_changes() == {("fxnest", "n0")}


def _symbol_host(fx):
    from ctgedit.editing import ElementDocument

    return ElementDocument.open_element(fx.file("FxLib", "fxmore.ctl"), "fx_symsym")


def _move_fx_box(fx) -> None:
    from ctgedit.editing import ElementDocument, libraries_changed

    sym = ElementDocument.open_element(fx.file("FxLib", "fxsym.ctl"), "fx_box")
    sym.move_objects([k for k, o in enumerate(sym.page.objects) if o.parent is None], 0, 2)
    sym.save()
    libraries_changed()


def test_a_saved_symbol_is_derived_again_in_the_symbols_that_place_it(fx_install):
    from ctgedit.workers import genie_worker as GW

    host = _symbol_host(fx_install)
    assert not host.stale_symbol_elements()
    before = [writer.object_payload(o) for o in host.parsed.page.objects]
    _move_fx_box(fx_install)
    assert _symbol_host(fx_install).stale_symbol_elements() == {("fxsym", "fx_box")}
    res = GW.update_closed_pages(fx_install.file("FxLib", "fxsym.ctl"), "fx_box")
    assert "fxmore.fx_symsym" in res["elements_saved"]
    after = _symbol_host(fx_install)
    assert not after.stale_symbol_elements()
    assert [writer.object_payload(o) for o in after.parsed.page.objects] != before


def test_a_serialisation_that_does_not_derive_the_symbol_keeps_its_stamp(fx_install):
    from ctgedit.format.element_session import open_element_session

    _move_fx_box(fx_install)
    host = _symbol_host(fx_install)
    content = host.serialize_element(refresh=True)  # (no re-derivation here)
    lib = E.LibraryFile(host.session.pe.library.to_bytes())
    lib.write_element("fx_symsym", content)
    _s, parsed = open_element_session(host.path, "fx_symsym", library=lib)
    assert parsed.page.link_table_raw == host.parsed.page.link_table_raw


def test_store_element_writes_only_over_the_block_it_was_read_from(fx_install, monkeypatch):
    from ctgedit.editing import ElementChangedMeanwhile, ElementDocument
    from ctgedit.format import projectlock

    lib = _lib(fx_install)
    locked = []
    real = projectlock.file

    def spy(path, timeout=None):
        locked.append(path)
        return real(path, timeout)

    monkeypatch.setattr(projectlock, "file", spy)
    stale = ElementDocument.open_element(lib, "n2")
    other = ElementDocument.open_element(lib, "n2")
    _move_text(other, 4)
    other.save()  # (saved elsewhere meanwhile)
    assert lib in locked  # (read, change and write: one step)
    saved = E.open_element(lib, "n2").block.data
    _move_text(stale, 9)
    with pytest.raises(ElementChangedMeanwhile):
        stale.save(expect_stored=True)
    assert E.open_element(lib, "n2").block.data == saved
    stale.save()  # (the user's own save overwrites)
    assert E.open_element(lib, "n2").block.data != saved


def test_update_users_leaves_an_element_saved_meanwhile(fx_install):
    from ctgedit.editing import ElementDocument
    from ctgedit.io import genieusage as GU

    lib = _lib(fx_install)
    _edit_n0(fx_install)
    tab = ElementDocument.open_element(lib, "n2")  # (a clean tab, read before ...)
    other = ElementDocument.open_element(lib, "n2")
    _move_text(other, 4)
    other.save()  # (... n2 is saved elsewhere)
    saved = E.open_element(lib, "n2").block.data
    plan = GU.update_plan(GU.element_users(lib, "n0"))
    rep = GU.run_update(
        GU.UpdatePlan(plan.elements, [], plan.skipped),
        open_element=lambda ref: tab if ref.element.lower() == "n2" else None,
    )
    assert any(x.startswith("fxnest.n2: it was saved somewhere else") for x in rep.skipped)
    assert "fxnest.n2" not in rep.elements_in_editor
    assert E.open_element(lib, "n2").block.data == saved


def test_a_workers_answer_for_an_element_saved_meanwhile_is_nothing_to_do(fx_install, tmp_path):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument
    from ctgedit.ui.jobs.genie_jobs import apply_answer

    _edit_n0(fx_install)
    tab = ElementDocument.open_element(_lib(fx_install), "n4")
    req = GW.write_request(
        str(tmp_path),
        tab.path,
        tab.serialize(),
        {("fxnest", "n0"): None},
        element=tab.nested_update_request(),
    )
    ElementDocument.open_element(_lib(fx_install), "n4").save()  # (the chain saved it)
    with open(req, encoding="utf-8") as fh:
        r = json.load(fh)
    with open(req + ".page", "rb") as fh:
        ans = GW.compute_element(r["element"], fh.read())
    assert not ans.get("error") and ans["ops"] == [] and ans["changed_on_disk"]
    assert apply_answer(tab, ans) == 0 and not tab._undo


def test_a_tab_with_changes_no_step_holds_is_not_clean(window, fx_install):
    _edit_n0(fx_install)
    window._open_element(_lib(fx_install), "n2")
    doc = window.doc
    assert doc.dirty and window._only_auto_changes(doc)
    assert window._tab_clean(doc)
    doc.dirty = True  # (a restored recovery copy: no undo step holds it)
    assert doc.has_unstepped_changes and not window._tab_clean(doc)
    assert window._element_tab_states().startswith("dirty\t")


def test_update_users_saves_a_tab_with_only_automatic_changes(window, fx_install, monkeypatch):
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.prefs.settings import set_genie_save_pages
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    set_genie_save_pages("never")
    _edit_n0(fx_install)
    window._open_element(_lib(fx_install), "n2")
    host = window.doc
    assert host.dirty and host.ui.auto_changes
    busy, real = [], GU.run_update

    def run(*a, **k):
        busy.append(UpdatePagesRun.users_busy)
        return real(*a, **k)

    monkeypatch.setattr(GU, "run_update", run)
    reports = []
    monkeypatch.setattr(type(window), "_report_users_update", lambda self, text, **k: reports.append(text))
    rep = window._update_element_users(window._element_users(_lib(fx_install), "n0"), confirm=False)
    assert busy == ["fxnest.n0"] and UpdatePagesRun.users_busy == ""
    assert "fxnest.n2" in rep.elements_in_editor
    assert not any(x.startswith("fxnest.n2") for x in rep.skipped)
    assert _stored(fx_install, "n2") == 1
    assert not host.dirty and not host.ui.auto_changes


def test_the_saved_tabs_are_found_by_their_file(window, fx_install, tmp_path):
    from ctgedit.ui.editor import notices

    _edit_n0(fx_install)
    window._open_element(_lib(fx_install), "n2")
    host = window.doc
    assert host.dirty and host.ui.auto_changes
    # (a library of the same name in another project: not this tab)
    window._reload_saved_tabs([[str(tmp_path / "Other" / "fxnest.ctm"), "n2"]])
    assert host.dirty and host.ui.auto_changes
    notices.add(host, "disk", "warning", "fxnest.n2 was changed by another program.", [])
    # (the chain's helper saved it from the library after n1, composing n0 as it is: a
    # stored block behind the tab's own update is not read again)
    from ctgedit.editing import ElementDocument, libraries_changed

    for name in ("n1", "n2"):
        ElementDocument.open_element(_lib(fx_install), name).save(refresh=True)
        libraries_changed()
    window._reload_saved_tabs([[_lib(fx_install), "n2"]])
    assert not host.dirty and not host.ui.auto_changes
    assert not any(n.key == "disk" for n in notices.of(host))


def test_a_clean_tab_is_not_called_changed_while_the_chain_runs(window, fx_install):
    from ctgedit.editing import ElementDocument
    from ctgedit.ui.editor import notices
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    window._open_element(_lib(fx_install), "n2")
    host = window.doc
    assert not host.dirty
    other = ElementDocument.open_element(_lib(fx_install), "n2")
    _move_text(other, 3)
    other.save()
    assert host.changed_on_disk()
    UpdatePagesRun.users_busy = "fxnest.n0"
    window._check_disk_changes([host.path])
    assert not any(n.key == "disk" for n in notices.of(host))
    assert host.path in window._disk_pending
    UpdatePagesRun.users_busy = ""
    window._check_disk_changes([host.path])
    assert any(n.key == "disk" for n in notices.of(host))


def test_the_genies_saved_survive_a_failed_pages_phase(fx_install, tmp_path, monkeypatch):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.io import updatepages

    def boom(*a, **k):
        raise RuntimeError("pages phase")

    monkeypatch.setattr(updatepages, "update_pages", boom)
    _edit_n0(fx_install)
    prog = tmp_path / "run.progress"
    res = GW.update_closed_pages(_lib(fx_install), "n0", progress_file=str(prog), backup=str(tmp_path / "bk"))
    assert "pages phase" in res["error"]
    assert "fxnest.n1" in res["elements_saved"] and res["libraries"] and res["backup"]
    side = json.loads((tmp_path / ("run.progress" + GW.GENIES_SUFFIX)).read_text())
    assert side["elements_saved"] == res["elements_saved"]


def test_the_editor_follows_saved_genies_after_an_error(window, fx_install):
    window._open_element(_lib(fx_install), "n2")
    host = window.doc
    _edit_n0(fx_install)
    text = window._users_run_finished(
        {
            "error": "the update gave no answer",
            "elements_saved": ["fxnest.n1"],
            "libraries": [_lib(fx_install)],
            "backup": "x",
        },
        "fxnest.n0",
    )
    assert text.startswith("Saved 1 library genie using fxnest.n0") and "failed" in text
    # (the open element tab composes the genie the helper saved)
    assert _marked(host.parsed.page.objects) == 1


def test_a_moved_element_tab_is_composed_in_its_new_window(window, fx_install):
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    _edit_n0(fx_install)
    app_settings().setValue("opt/auto_update_genies", False)
    window._open_element(_lib(fx_install), "n2")
    host = window.doc
    assert _marked(host.parsed.page.objects) == 0
    app_settings().setValue("opt/auto_update_genies", True)
    other = MainWindow()
    try:
        entry = next(e for e in window._docs if e["doc"] is host)
        window._move_entry_to(entry, other)
        assert any(e["doc"] is host for e in other._docs)
        assert _marked(host.parsed.page.objects) == 1
    finally:
        for e in list(other._docs):
            e["doc"].dirty = False
        if other in MainWindow._instances:
            MainWindow._instances.remove(other)


def test_the_lists_for_the_worker_are_replaced_whole(tmp_path):
    from ctgedit.ui.window.library import _write_text

    p = tmp_path / "x.editors"
    p.write_text("old", encoding="utf-8")
    _write_text(str(p), "clean\ta\tb")
    assert p.read_text(encoding="utf-8") == "clean\ta\tb"
    assert not (tmp_path / "x.editors.tmp").exists()


# ------------------------------------------- a genie moved inside the nested genie's definition
def _move_n0_in_n1(fx) -> None:
    """n1's copy of n0 moves by (7, 5) inside n1's definition, saved (not through a window)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(_lib(fx), "n1")
    root = next(k for k, o in enumerate(d.page.objects) if o.kind.name == "GENIE")
    assert (d.page.objects[root].geometry.x, d.page.objects[root].geometry.y) == (10, 40)
    d.move_objects([root], 7, 5)
    d.save()
    libraries_changed()
    d2 = ElementDocument.open_element(_lib(fx), "n1")
    assert (d2.page.objects[root].geometry.x, d2.page.objects[root].geometry.y) == (17, 45)


def _inner_roots(objs) -> list:
    """(x, y) of the genie roots nested two levels down (a copy of n0 inside a copy of n1)."""
    return sorted(
        (o.geometry.x, o.geometry.y)
        for k, o in enumerate(objs)
        if o.kind.name == "GENIE"
        and k > 0
        and any(objs[p].kind.name == "GENIE" for p in range(k) if objs[p].raw.get("_class_flag"))
    )


def test_a_genie_moved_inside_the_nested_genie_moves_in_its_hosts(fx_install):
    """A genie nested in a genie was moved: the current element's member has a Local of its
    own, and the hosts' copies take it."""
    from ctgedit.editing import ElementDocument

    lib = _lib(fx_install)
    n2 = ElementDocument.open_element(lib, "n2")
    inner = [k for k, o in enumerate(n2.page.objects) if o.kind.name == "GENIE"][1]
    assert (n2.page.objects[inner].geometry.x, n2.page.objects[inner].geometry.y) == (10, 70)
    _move_n0_in_n1(fx_install)
    n2 = ElementDocument.open_element(lib, "n2")
    assert n2.nested_genie_changes() == {("fxnest", "n1")}
    assert n2.update_nested_genies() > 0
    g = n2.page.objects[inner].geometry
    assert (g.x, g.y) == (17, 75), (g.x, g.y)  # (the inner copy moved too)
    t = n2.page.objects[inner + 1].geometry
    assert (t.x, t.y) == (17, 75), (t.x, t.y)  # (and its member with it)
    # an unedited save of the stale host writes the moved copy
    n2 = ElementDocument.open_element(lib, "n2")
    n2.save()
    g = ElementDocument.open_element(lib, "n2").page.objects[inner].geometry
    assert (g.x, g.y) == (17, 75)


def test_the_chain_moves_the_inner_genie_in_every_host_and_page(fx_install, fx_page):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument, ElementDocument

    lib = _lib(fx_install)
    page = fx_page("FxGenies")  # (places n4: FxNested)
    before = _inner_roots(EditableDocument.open(page).page.objects)
    _move_n0_in_n1(fx_install)
    res = GW.update_closed_pages(lib, "n1")
    for name in ("n2", "n3", "n4"):
        d = ElementDocument.open_element(lib, name)
        assert not d.nested_genie_changes(), name
        roots = [o for o in d.page.objects if o.kind.name == "GENIE"]
        # n1's copy is at (10, 40) in n2 and deeper in n3 / n4; n0's copy inside it moved
        # from (10, 70) to (17, 75) relative to the same frame
        xs = sorted((o.geometry.x, o.geometry.y) for o in roots)
        assert (17, 75) in xs or any(x - y == 7 for (x, _a), (y, _b) in zip(xs[1:], xs)), (name, xs)
    assert "FxGenies" in res["updated"]
    after = _inner_roots(EditableDocument.open(page).page.objects)
    assert after != before
    assert any((x + 7, y + 5) in after for x, y in before), (before, after)


def _stale_but_current(fx) -> None:
    """n2's copy of n1 as stored BEFORE n1's inner genie moved,
    with n2's import table re-stamped as current (an earlier save that kept the copy)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx)
    n2 = ElementDocument.open_element(lib, "n2")
    _move_n0_in_n1(fx)
    assert n2.nested_genie_changes() == {("fxnest", "n1")}
    n2.move_objects([0], 1, 0)  # (an edit of its own: the save writes)
    changed = ElementDocument._changed_nested
    try:
        ElementDocument._changed_nested = lambda self, res: frozenset()  # (nothing moved)
        n2.save()
    finally:
        ElementDocument._changed_nested = changed
    assert n2.save_files
    libraries_changed()
    again = ElementDocument.open_element(lib, "n2")
    assert not again.nested_genie_changes()  # (the stamps say current)
    inner = [k for k, o in enumerate(again.page.objects) if o.kind.name == "GENIE"][1]
    g = again.page.objects[inner].geometry
    assert (g.x, g.y) == (10, 70), (g.x, g.y)  # (the copy stayed as stored)


def test_compose_nested_genies_again_composes_every_copy(window, fx_install):
    """Edit > Library > Compose Nested Genies Again: the copies composed again whatever the
    stamps say (the remedy for a copy that stayed as stored through a save), one undo step,
    Save keeps it; nothing to do when every copy matches."""
    from ctgedit.editing import ElementDocument
    from ctgedit.ui import notify
    from ctgedit.ui.prefs.settings import set_genie_save_pages

    lib = _lib(fx_install)
    set_genie_save_pages("never")
    _stale_but_current(fx_install)
    window._open_element(lib, "n2")
    doc = window.doc
    assert doc.element_name == "n2" and not doc.dirty
    assert window.act_compose_nested.isEnabled()
    inner = [k for k, o in enumerate(doc.page.objects) if o.kind.name == "GENIE"][1]
    window.act_compose_nested.trigger()
    g = doc.page.objects[inner].geometry
    assert (g.x, g.y) == (17, 75), (g.x, g.y)
    assert doc.dirty and doc._undo[-1].label == "compose nested genies again"
    assert "Save to keep it" in notify.centre(window).newest_first()[0].text
    assert window.save()
    g = ElementDocument.open_element(lib, "n2").page.objects[inner].geometry
    assert (g.x, g.y) == (17, 75)
    # every copy current: nothing to do, said
    window.act_compose_nested.trigger()
    assert not window.doc.dirty
    assert "already matches" in notify.centre(window).newest_first()[0].text
    # the command is for library elements only
    window.open_document(fx_install.page("FxShapes"))
    assert not window.act_compose_nested.isEnabled()
