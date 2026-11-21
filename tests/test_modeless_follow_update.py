"""A modeless window never loses typed text to an update."""

import os
import sys
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtTest  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_save_chain_follow2 import _drop_win, _make_win, _quiet_chain  # noqa: E402,F401
from test_save_chain_jobs import _worker_here  # noqa: E402

MARK = b"CHANGED1"


def _closed(w):
    """``w`` closed without its windows' typed text asking to be saved."""
    for dlg in list(w._dialogs.values()):
        dlg._ctg_dropped = True
        dlg.reject()
    _drop_win(w)


@pytest.fixture()
def win():
    w = _make_win()
    yield w
    _closed(w)


@pytest.fixture()
def win2():
    w = _make_win()
    yield w
    _closed(w)


def _spin(ms: int) -> None:
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _spin_until(cond, timeout: float = 30.0, first=None) -> bool:
    """A real event loop (QTimer steps) until ``cond()`` or ``timeout`` s; ``first`` runs
    from the loop's first step (a save made there sends its tab updates to the jobs)."""
    loop = QtCore.QEventLoop()
    t0 = time.time()

    def tick():
        if cond() or time.time() - t0 > timeout:
            loop.quit()
            return
        QtCore.QTimer.singleShot(20, tick)

    def start():
        if first is not None:
            first()
        tick()

    QtCore.QTimer.singleShot(0, start)
    loop.exec()
    return bool(cond())


def _handle(doc, i):
    from ctgedit.format import geometry as G

    return G.get_handle(doc.parsed.page.objects[i])


def _marked(doc) -> int:
    from ctgedit.format import writer

    return sum(MARK in writer.object_payload(o) for o in doc.parsed.page.objects)


def _change_n1(d, grow: bool = True) -> None:
    """n1's text says CHANGED1; ``grow``: n1 gains a rectangle too (every copy of it gains a
    record, so the genies after the first copy move)."""
    objs = d.parsed.page.objects
    i = next(k for k, o in enumerate(objs) if o.type_code == 0x05 and d.page.objects[k].parent is None)
    assert d.set_text(i, "%Name% CHANGED1")
    if grow:
        d.create_object("rectangle", rect=(0, 60, 30, 80), colour=0x336699)


def _name_field(sheet):
    return sheet._widgets["__AN_NAME__"][0][1]


def _type(w, text: str) -> None:
    w.setFocus()
    if hasattr(w, "end"):
        w.end(False)
    QtTest.QTest.keyClicks(w, text)


def _open(w, dlg) -> bool:
    """``dlg`` is still one of ``w``'s windows (not closed)."""
    return not getattr(dlg, "_ctg_dropped", False) and dlg in w._dialogs.values()


def _on_its_record(w, dlg, handle) -> bool:
    objs = w.doc.parsed.page.objects
    i = dlg._ctg_index
    return 0 <= i < len(objs) and objs[i] is dlg._ctg_target and _handle(w.doc, i) == handle


def test_windows_on_an_updated_genie_follow_it_and_keep_what_was_typed(
    fx_install, win, win2, monkeypatch, tmp_path
):
    """Windows on an updated genie follow it and keep typed text."""
    _worker_here(monkeypatch, tmp_path)
    win.open_document(fx_install.page("FxParts"))
    page = win.doc
    assert _spin_until(lambda: not win._genie_job_runner().busy(), 30)
    win2._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n1")
    r1 = page.find_objects_by_name("FxRow1")[0]
    win.object_properties(r1)
    sheet = win._dialogs.get(("objprops", r1))
    _type(_name_field(sheet), "zz")
    r2 = page.find_objects_by_name("FxRow2")[0]
    win._edit_genie(r2, page.page.objects[r2])
    params = win._dialogs.get(("genie", r2))
    _type(params.params_form.editors["RowNum"][1], "q")
    m = page.find_objects_by_name("FxMixed1")[0]
    win.object_properties(m)
    mixed = win._dialogs.get(("objprops", m))
    _type(_name_field(mixed), "mm")
    handles = {"sheet": _handle(page, r1), "params": _handle(page, r2), "mixed": _handle(page, m)}
    n0 = len(page.parsed.page.objects)
    _change_n1(win2.doc)
    assert _spin_until(lambda: _marked(page) == 3, 30, first=win2._save_element_to), (
        "the update landed on FxParts"
    )
    assert len(page.parsed.page.objects) == n0 + 3
    for dlg, h in ((sheet, handles["sheet"]), (params, handles["params"]), (mixed, handles["mixed"])):
        assert _open(win, dlg) and dlg.isVisible(), "kept open: %r" % dlg.windowTitle()
        assert _on_its_record(win, dlg, h), "on the record that took its place"
        assert dlg.has_pending_edits()
    assert params._ctg_index == r2 + 1 and mixed._ctg_index == m + 3
    assert _name_field(sheet).text() == "FxRow1zz"
    assert params.params_form.editors["RowNum"][0]() == "2q"
    assert _name_field(mixed).text() == "FxMixed1mm"
    # (the same window is found for that object again)
    assert win._modeless_for("objprops", r1) is sheet
    assert sheet.apply()
    assert page.find_objects_by_name("FxRow1zz") == [r1]
    assert _marked(page) == 3, "Apply kept the update"
    params.accept()
    i = page.find_objects_by_name("FxRow2")[0]
    assert page.genie_bindings(i).get("RowNum") == "2q"


def test_a_hidden_sheet_follows_an_update_made_while_another_tab_was_shown(fx_install, win, monkeypatch):
    """A hidden sheet follows an update made while another tab was shown."""
    win.open_document(fx_install.page("FxParts"))
    page = win.doc
    r1 = page.find_objects_by_name("FxRow1")[0]
    win.object_properties(r1)
    sheet = win._dialogs.get(("objprops", r1))
    _name_field(sheet).setText("FxRow1zz")
    r2 = page.find_objects_by_name("FxRow2")[0]
    win._edit_genie(r2, page.page.objects[r2])
    params = win._dialogs.get(("genie", r2))
    params.params_form.editors["RowNum"][1].setText("2q")
    inputs = {"PageLabel": "@(Control)", "MenuName": "Main"}
    monkeypatch.setattr(page, "template_inputs", lambda: dict(inputs))
    r3 = page.find_objects_by_name("FxRow3")[0]
    win._edit_template_inputs(
        r3, page.page.objects[r3], types.SimpleNamespace(library="fxtpl", element="normal")
    )
    tpl = next(d for d in win._dialogs.values() if d._ctg_kind == "tplinputs")
    tpl.params_form.editors["PageLabel"][1].setText("@(Typed)")
    win._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n1")
    assert sheet.isHidden() and params.isHidden() and tpl.isHidden()
    _change_n1(win.doc)
    assert win._save_element_to()
    assert _marked(page) == 3, "the hidden tab took the update"
    inputs["MenuName"] = "Other"  # (what the template's copy says now)
    win.set_document(page)
    for dlg in (sheet, params, tpl):
        assert _open(win, dlg) and dlg.isVisible(), "kept open: %r" % dlg.windowTitle()
        assert dlg._ctg_target is page.parsed.page.objects[dlg._ctg_index]
    assert _name_field(sheet).text() == "FxRow1zz" and sheet.has_pending_edits()
    assert params.params_form.editors["RowNum"][0]() == "2q"
    ed = tpl.params_form.editors
    assert ed["PageLabel"][0]() == "@(Typed)", "typed: kept"
    assert ed["MenuName"][0]() == "Other", "not typed: re-read"
    assert tpl.changed_values(tpl._ctg_loaded) == {"PageLabel": "@(Typed)"}


def test_a_sheet_in_the_row_genies_tab_follows_the_sub_genies_save(fx_install, win, win2):
    """The user's case: the row genie open in a tab with a sheet on its copy of the sub
    genie (a name typed); the sub genie is saved in another window; the row tab composes
    the copy again and the sheet stays on it with the name, which Apply then sets."""
    win._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_row")
    row = win.doc
    i = next(k for k, o in enumerate(row.page.objects) if o.parent is None and o.kind.name == "GENIE")
    win.object_properties(i)
    sheet = win._dialogs.get(("objprops", i))
    h = _handle(row, i)
    _name_field(sheet).setText("SubTyped")
    old = row.parsed.page.objects[i]
    win2._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n1")
    _change_n1(win2.doc)
    assert win2._save_element_to()
    assert _marked(row) == 1 and row.parsed.page.objects[i] is not old, "the copy was replaced"
    assert _open(win, sheet) and _on_its_record(win, sheet, h)
    assert _name_field(sheet).text() == "SubTyped" and sheet.has_pending_edits()
    assert sheet.apply()
    assert row.find_objects_by_name("SubTyped") == [i] and _marked(row) == 1


def test_a_window_whose_object_the_update_removed_says_so_after_the_update(fx_install, win, win2):
    """No record took its place (n1 lost the text the sheet showed): the sheet closes, and
    its note is said again with the update's own message, which came after it."""
    win.open_document(fx_install.page("FxParts"))
    page = win.doc
    root = page.find_objects_by_name("FxRow1")[0]
    k = next(
        j
        for j in page.subtree(root)
        if page.parsed.page.objects[j].type_code == 0x05
        and (page.text_display(j).static or "").startswith("Row")
    )
    win.object_properties(k)
    sheet = win._dialogs.get(("objprops", k))
    win2._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n1")
    d = win2.doc
    j = next(
        x
        for x, o in enumerate(d.parsed.page.objects)
        if o.type_code == 0x05 and d.page.objects[x].parent is None
    )
    d.delete_objects([j])
    seen = []
    win.statusBar().messageChanged.connect(seen.append)
    assert win2._save_element_to()
    assert not _open(win, sheet)
    note = "The object was removed, so its Properties closed."
    assert seen.index(note) < seen.index("Updated fxnest.n1 on FxParts."), seen
    _spin(50)
    assert win.statusBar().currentMessage() == "Updated fxnest.n1 on FxParts. " + note


def test_a_window_closed_with_typed_text_says_it_was_not_applied(fx_install, win):
    """Its object deleted with typed text in its window: the note says the text was not
    applied, as a warning in the bell's list; a page Save after that does not apply the
    closed window's text to whatever sits at its old index now."""
    from ctgedit.ui.notify import centre
    from test_modeless import _rects, _stub

    win.open_document(fx_install.page("FxShapes"))
    a, b = _rects(win, (20, 20, 60, 50), (80, 20, 120, 50))
    dlg = _stub(win, a)
    dlg.pending = True
    dlg.edit.setText("typed")
    pending = win._pending_windows(win.doc)
    assert pending == [(dlg, dlg.windowTitle() or "Properties")]
    win._delete_indices([a])
    text = "The object was removed, so its Properties closed. What you typed there was not applied."
    assert win.statusBar().currentMessage() == text
    newest = centre(win).newest_first()[0]
    assert newest.text == text and newest.level == "warning"
    assert win._apply_pending_windows(pending) is True
    assert "typed" not in str(win.doc.object_properties(a).get("COMMENT", ""))


def _rows_by_name(page):
    return {n: page.find_objects_by_name(n)[0] for n in ("FxRow1", "FxRow2", "FxRow3")}


def test_find_rows_follow_their_genies_through_an_update(fx_install, win, win2):
    """The Find window lists FxRow1..3 of FxParts; n1 is saved in another window and the
    update composes the row copies again (FxRow2 and FxRow3 move). The rows still name
    their genies: Go To selects FxRow3 where it is now and Replace changes its name."""
    from ctgedit.features import find_replace as FR

    win.open_document(fx_install.page("FxParts"))
    page = win.doc
    before = _rows_by_name(page)
    win._find_dialog(False)
    dlg = win._find_dlg
    dlg._ed_find.setText("FxRow")
    hits = dlg.find_all()
    k = next(
        n
        for n in range(dlg._list.topLevelItemCount())
        if hits[dlg._list.topLevelItem(n).data(0, QtCore.Qt.UserRole)].value == "FxRow3"
    )
    stale = hits[dlg._list.topLevelItem(k).data(0, QtCore.Qt.UserRole)]
    win2._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n1")
    _change_n1(win2.doc)
    assert win2._save_element_to()
    assert _marked(page) == 3 and page.parsed.page.objects[before["FxRow3"]] is not stale.po
    now = _rows_by_name(page)
    assert now["FxRow3"] == before["FxRow3"] + 2, "FxRow1's and FxRow2's copies grew"
    assert FR._resolve(page, stale) == now["FxRow3"]
    dlg._list.setCurrentItem(dlg._list.topLevelItem(k))
    dlg.go_to()
    assert "no longer" not in dlg._status.text()
    assert win._sel == [now["FxRow3"]]
    rep = FR.replace_hits(page, [stale], dlg.options(), "FxLine")
    assert rep.replaced == 1 and not rep.gone
    assert page.find_objects_by_name("FxLine3") == [now["FxRow3"]]
    dlg.reject()


def test_goto_object_rows_follow_their_genies_through_an_update(fx_install, win, win2):
    """Goto Object open on FxParts with FxRow3's row picked and nothing selected on the page
    (so the tree is rebuilt only a moment after a change); the update of n1 lands and the
    row is used before that: it acts on FxRow3 where it is now."""
    win.resize(1200, 800)
    win.show()
    win.open_document(fx_install.page("FxParts"))
    page = win.doc
    before = _rows_by_name(page)
    win._goto_dialog()
    dlg = win._dialogs.get("goto")
    dlg._tree.setCurrentItem(dlg._items[before["FxRow3"]])
    win._set_selection([])
    win2._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n1")
    _change_n1(win2.doc)
    assert win2._save_element_to()
    now = _rows_by_name(page)
    assert _marked(page) == 3 and now["FxRow3"] != before["FxRow3"]
    assert dlg._serial != page.derive_serial, "not rebuilt yet"
    assert dlg.current_index() == before["FxRow3"]
    assert win._goto_index(before["FxRow3"]) == now["FxRow3"]
    dlg._go_to()
    assert win._sel == [now["FxRow3"]]
    _spin(50)


def test_successor_index_is_the_same_class_and_handle_nearest_its_place():
    from ctgedit.format import geometry as G
    from ctgedit.ui.modeless import successor_index

    assert successor_index is G.successor_index, "one rule for the windows and Find"

    class R:
        def __init__(self, tc, h):
            self.type_code = tc
            self.body_raw = b"\x00\x00" + bytes((h & 0xFF, h >> 8)) + b"\x00" * 4

    old = R(0x05, 7)
    objs = [R(0x05, 1), R(0x0F, 7), R(0x05, 7), R(0x05, 3), R(0x05, 7)]
    assert G.get_handle(old) == 7
    assert successor_index(objs, old, 2) == 2
    assert successor_index(objs, old, 3) in (2, 4)
    assert successor_index(objs, old, 1) == 2, "a touch command shares the handle, not the class"
    assert successor_index(objs, R(0x05, 9), 0) is None
    assert successor_index(objs, object(), 0) is None
    assert successor_index(objs, None, 0) is None
    assert successor_index([R(0x05, 0xFFFF)], R(0x05, 0xFFFF), 0) is None, "no handle"


def _typed_stub(win, index=0):
    """An object window on the page shown with a typed value not applied."""
    from test_modeless import _stub

    dlg = _stub(win, index)
    dlg.pending = True
    dlg.edit.setText("typed")
    return dlg


def test_a_page_is_not_read_again_from_disk_under_a_window_holding_typed_values(fx_install, win):
    """Settings: reload a page another program changed by itself. A window of the page holds
    typed values not applied: the page is read again once the window has closed."""
    from ctgedit.ui.prefs.settings import set_disk_reload

    set_disk_reload("always")
    win.open_document(fx_install.page("FxShapes"))
    doc = win.doc
    dlg = _typed_stub(win)
    doc.changed_on_disk = lambda: True
    win._check_disk_changes([doc.path])
    assert win.doc is doc and _open(win, dlg) and dlg.edit.text() == "typed"
    assert doc.path in win._disk_pending
    dlg.pending = False
    win._check_disk_changes([doc.path])
    assert win.doc is not doc, "read again once nothing typed is left"


def test_a_page_the_chain_wrote_keeps_its_window_with_typed_values(fx_install, win, monkeypatch):
    """A clean page the update of what uses a genie wrote, whose reload waited for the user
    (files._reload_that_waited): with a window holding typed values it is kept and takes the
    helper's file as its baseline, as a tab with a change of the user's does; without one it
    is read again."""
    win.open_document(fx_install.page("FxShapes"))
    doc = win.doc
    dlg = _typed_stub(win)
    doc.changed_on_disk = lambda: True
    took, reverted = [], []
    monkeypatch.setattr(
        type(win), "_take_chain_write", lambda self, d, versions=None, **k: took.append(d) or True
    )
    monkeypatch.setattr(
        type(win), "revert_document", lambda self, d=None, ask=True: reverted.append(d) or True
    )
    assert win._reload_that_waited(doc, "page") is False
    assert took == [doc] and reverted == [] and _open(win, dlg)
    dlg.pending = False
    assert win._reload_that_waited(doc, "page") is True
    assert reverted == [doc]


def test_a_sheet_closes_when_the_user_reverts_its_tab(fx_install, win):
    """File > Revert to Saved, asked and answered: the windows show the version thrown
    away, so they close."""
    win.open_document(fx_install.page("FxShapes"))
    doc = win.doc
    dlg = _typed_stub(win)
    doc.move_objects([0], 4, 0)
    win._confirm = lambda *a, **k: True
    assert win.revert_document(doc)
    assert not _open(win, dlg)
