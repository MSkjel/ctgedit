"""Saving a genie recomposes only the copies of that genie on its users."""

import os


from ctgedit.format import genie_place as GP
from ctgedit.format import geometry as G
from ctgedit.format import writer

#: the page, the changed nested genie (library stem, element) and its library file
PAGE = "FxParts.ctg"
NESTED = ("fxnest", "n1")
LIBRARY = "fxnest.ctm"
#: the instance whose RowNum the genie dialog changes
ROW = "FxRow1"


def _test_copy(fx_install):
    """(the folder of FxParts, the page, fxnest.ctm) in the test's fx_install copy, the
    page brought up to date with its genies first (FxStale1 is older than its element)."""
    from ctgedit.editing import EditableDocument, libraries_changed

    libraries_changed()
    page = fx_install.page(PAGE[:-4])
    doc = EditableDocument.open(page)
    doc.update_genie_instances()
    with open(page, "wb") as fh:
        fh.write(doc.serialize())
    return os.path.dirname(page), page, fx_install.file("FxLib", LIBRARY)


def _change_nested(lib):
    """Move fxnest.n1's first root 6 to the right and save the element."""
    from ctgedit.editing import ElementDocument

    e = ElementDocument.open_element(lib, NESTED[1])
    e.move_objects([list(e.page.roots())[0]], 6, 0)
    e.save()
    return e


def _payloads(doc):
    return [writer.object_payload(p) for p in doc.parsed.page.objects]


def test_only_the_changed_nested_copies_are_composed_again(fx_install):
    from ctgedit.editing import EditableDocument

    _d, page, lib = _test_copy(fx_install)
    _change_nested(lib)
    stored = _payloads(EditableDocument.open(page))
    # the page sees the nested change (its own link table does not)
    doc = EditableDocument.open(page)
    ch = doc.genie_changes()
    assert any(v and NESTED in v for v in ch.values()), ch
    n = doc.update_nested_genie_copies({NESTED})
    partial = _payloads(doc)
    full_doc = EditableDocument.open(page)
    full_doc.update_genie_instances()
    full = _payloads(full_doc)
    assert n and len(partial) == len(stored) == len(full)
    changed = [i for i, (a, b) in enumerate(zip(stored, partial)) if a != b]
    assert changed
    # every record the partial update changes is the full re-composition's record
    assert all(partial[i] == full[i] for i in changed)
    # and every handle of the page is kept
    hs = sorted((G.get_handle(GP.to_po(x)) or 0) for x in stored)
    assert hs == sorted((G.get_handle(GP.to_po(x)) or 0) for x in partial)
    # one undo step puts the stored records back
    doc.undo()
    assert _payloads(doc) == stored


def test_worker_answer_lands_after_an_unrelated_edit(fx_install):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.jobs.genie_jobs import apply_answer

    _d, page, lib = _test_copy(fx_install)
    _change_nested(lib)
    doc = EditableDocument.open(page)
    ch = {k: v for k, v in doc.genie_changes().items() if v}
    ans = GW.compute(page, doc.serialize(), ch)
    assert ans["ops"] and not ans.get("error")
    # meanwhile: a rectangle of the user's own, unsaved
    doc.create_object("rectangle", (20, 20, 60, 40))
    mine = _payloads(doc)[-1]
    n = apply_answer(doc, ans)
    assert n and _payloads(doc)[-1] == mine
    ref = EditableDocument.open(page)
    ref.update_nested_genie_copies({k for v in ch.values() for k in v})
    assert _payloads(doc)[:-1] == _payloads(ref)


def test_record_ops_refuse_a_page_that_changed_under_them(fx_page):
    """FxShapes' FxRect (a rectangle record, tc 0x0F)."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxShapes"))
    before = _payloads(doc)
    (i,) = doc.find_objects_by_name("FxRect")
    assert doc.parsed.page.objects[i].type_code == 0x0F
    after = list(before)
    po = GP.to_po(before[i])
    G.set_handle(po, G.get_handle(po))  # (a payload of its own)
    raw = bytearray(writer.object_payload(po))
    raw[-1] ^= 1
    after[i] = bytes(raw)
    ops = GW.record_ops(before, after)
    assert len(ops) == 1
    # an edit somewhere else: the answer still lands, where its record is now
    doc.create_object("rectangle", (5, 5, 30, 30))
    doc.move_objects([doc.parsed.page.objects.index(doc.parsed.page.objects[-1])], 3, 0)
    assert doc.apply_record_ops(ops) == 1
    doc.undo()
    # an edit OF its record: nothing changes
    doc.move_objects([i], 4, 0) if doc.page.root_of(i) == i else doc.move_objects([doc.page.root_of(i)], 4, 0)
    now = _payloads(doc)
    assert doc.apply_record_ops(ops) is None
    assert _payloads(doc) == now


def test_closed_pages_setting(tmp_path, monkeypatch):
    from ctgedit.ui.prefs.settings import app_settings, genie_save_pages, set_genie_save_pages

    st = app_settings()
    st.remove("opt/genie_save_pages")
    assert genie_save_pages() == "ask"
    set_genie_save_pages("never")
    assert genie_save_pages() == "never"


def test_closed_pages_leave_a_page_opened_meanwhile(tmp_path, fx_install):
    """Every page that uses the changed fxnest.n1 (FxParts, FxMembers, and FxGenies
    through its nested chain) is opened in an editor meanwhile (the skip file): each is
    left. Update Pages takes the registered pages (FxGenies); FxParts and FxMembers have
    no pages.DBF row, so it reports them unregistered and leaves them as well."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.io import genieusage as GU
    from ctgedit.io.updatepages import registered_names

    _d, page, lib = _test_copy(fx_install)
    _change_nested(lib)
    users = sorted(
        u.ref.path for u in GU.element_users(lib, NESTED[1]).users if u.ref.kind == "page" and u.needs_update
    )
    assert page in users
    before = {p: open(p, "rb").read() for p in users}
    skip = tmp_path / "skip.txt"
    skip.write_text("\n".join(users), encoding="utf-8")
    res = GW.update_closed_pages(lib, NESTED[1], progress_file=str(tmp_path / "p.json"), skip_file=str(skip))
    assert page in res["pages"]
    reg = registered_names(os.path.dirname(page))
    want = sorted(n for n in (os.path.splitext(os.path.basename(p))[0] for p in users) if n.lower() in reg)
    assert want == ["FxGenies"]
    assert sorted(res["skipped_open"]) == want
    assert not res["updated"]
    assert {p: open(p, "rb").read() for p in users} == before
    # cancelled before the first page: nothing is written either
    (tmp_path / "cancel").write_text("")
    res = GW.update_closed_pages(lib, NESTED[1], cancel_file=str(tmp_path / "cancel"))
    assert res["stopped"] and not res["updated"]
    assert {p: open(p, "rb").read() for p in users} == before


def test_a_genie_dialog_ok_in_the_worker_equals_the_editors(fx_install):
    """FxRow1's RowNum (fxparts.fx_row) set to 3 by the worker and by the editor."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.jobs.genie_jobs import apply_answer

    _d, page, _lib = _test_copy(fx_install)
    doc = EditableDocument.open(page)
    (root,) = doc.find_objects_by_name(ROW)
    assert root in [g[0] for g in doc.genie_instances()]
    ans = GW.compute(page, doc.serialize(), bindings={"index": root, "values": {"RowNum": "3"}})
    assert ans["updated"] == 1 and not ans.get("error")
    assert apply_answer(doc, ans, "genie parameters")
    ref = EditableDocument.open(page)
    # (the editor's genie dialog OK, ui/window/canvas_edits: it writes the placed level's
    # AN first, as the worker's does; FxRow1 was placed without one)
    assert ref.set_genie_bindings(ref.find_objects_by_name(ROW)[0], {"RowNum": "3"}, dialog=True)
    assert _payloads(doc) == _payloads(ref)
    assert doc._undo[-1].label == "genie parameters"


def test_a_no_change_update_is_remembered(tmp_path):
    from ctgedit.infra import noop_memo

    k = noop_memo.key(b"page", {("lib", "g"): frozenset({("lib", "n")})}, {"lib.g": [["f", 1, 2]]})
    assert not noop_memo.known(k)
    noop_memo.remember(k)
    assert noop_memo.known(k)
    # another library file version: another key
    k2 = noop_memo.key(b"page", {("lib", "g"): frozenset({("lib", "n")})}, {"lib.g": [["f", 2, 2]]})
    assert k2 != k and not noop_memo.known(k2)


def test_a_library_change_derives_only_the_units_it_reached(fx_install, monkeypatch):
    """After a library change only the units that read a changed library file are derived
    again; the model
    equals a full derive (derive_check). A file nothing on the page reads (FxLib's
    fxempty.ctl, whose only element is deleted) changes nothing."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.io import reader

    _d, page_path, lib = _test_copy(fx_install)
    page = EditableDocument.open(page_path)
    page.derive_check = True
    seen = []
    real = reader._library_stale_records
    monkeypatch.setattr(
        reader, "_library_stale_records", lambda prev, path: (lambda r: seen.append(r) or r)(real(prev, path))
    )
    # an unrelated library file changes: nothing is derived again
    other = fx_install.file("FxLib", "fxempty.ctl")
    with open(other, "ab") as fh:
        fh.write(b"\0")
    libraries_changed()
    page._derive()
    assert seen and seen[-1] is not None and not seen[-1][1]
    # the nested genie the page's rows place is saved: its units are derived again
    _change_nested(lib)
    libraries_changed()
    page._derive()  # (derive_check: equals a full derive)
    assert seen[-1] is not None and seen[-1][1]
    assert page._derivation is not None and not page._derivation.full


def test_a_stale_pages_update_is_applied_from_its_remembered_answer(fx_install, monkeypatch):
    """The worker keeps its answer under the page's no-op key (infra/answer_memo); the next
    open of the same stale page applies it at once, no worker job."""
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import answer_memo
    from ctgedit.format import writer
    from ctgedit.ui.main_window import MainWindow

    _d, page, lib = _test_copy(fx_install)
    _change_nested(lib)
    doc = EditableDocument.open(page)
    ch = doc.genie_changes()
    assert ch
    ans = GW.compute(page, open(page, "rb").read(), ch)
    assert ans["updated"] and answer_memo.get(doc.genie_noop_key(ch)) == ans
    # a window opening the page applies it without the worker
    w = MainWindow()
    try:
        submitted = []
        monkeypatch.setattr(w._genie_job_runner(), "submit", lambda *a, **k: submitted.append(a))
        w.open_document(page)
        w._update_stale_genies(w.doc)
        assert not submitted
        want = EditableDocument.open(page)
        want.apply_genie_changes(want.genie_changes())
        assert [writer.object_payload(p) for p in w.doc.parsed.page.objects] == [
            writer.object_payload(p) for p in want.parsed.page.objects
        ]
    finally:
        for ent in w._docs:  # (the update made it dirty: no save question)
            ent["doc"].dirty = False
        w.doc = None
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.close()


def test_a_genie_save_keeps_its_previous_version_for_the_open_pages(fx_install):
    """A genie save keeps the version it overwrites (ElementDocument.previous_element, made
    on first use from the library bytes as they were): it equals the element as stored before
    the save, after a second save too."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import genie_place as GP, writer

    _d, _page, lib = _test_copy(fx_install)
    for _ in range(2):
        before = GP.load_element(lib, NESTED[1], compose=True)
        e = ElementDocument.open_element(lib, NESTED[1])
        e.move_objects([list(e.page.roots())[0]], 6, 0)
        e.save()
        prev = e.previous_element
        assert prev is not None
        assert [writer.object_payload(p) for p in prev.records] == [
            writer.object_payload(p) for p in before.records
        ]
        assert prev.lib_path == e.path
