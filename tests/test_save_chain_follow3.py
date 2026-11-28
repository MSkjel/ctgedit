"""What follows a library save."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _make_win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    return MainWindow()


def _drop_win(w):
    from ctgedit.ui.main_window import MainWindow

    for ent in list(getattr(w, "_docs", ())):
        d = ent["doc"]
        if hasattr(d, "dirty"):
            d.dirty = False
    w.doc = None
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.close()


@pytest.fixture()
def win():
    w = _make_win()
    yield w
    _drop_win(w)


@pytest.fixture()
def win2():
    w = _make_win()
    yield w
    _drop_win(w)


@pytest.fixture(autouse=True)
def _quiet_chain():
    """The save prompts off, the chain after a save left (only the tabs follow)."""
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    st = app_settings()
    for k, v in (
        ("opt/save_symbol_warning", False),
        ("opt/save_supergenie_warning", False),
        ("opt/genie_save_pages", "never"),
        ("opt/symbol_save_pages", "never"),
        ("opt/auto_update_genies", True),
        ("opt/auto_update_symbols", True),
    ):
        st.setValue(k, v)
    yield
    UpdatePagesRun.users_busy = ""
    UpdatePagesRun.chain_busy = False
    UpdatePagesRun.tabs_following = False
    UpdatePagesRun.users_queue.clear()


def _no_jobs(w, monkeypatch):
    submitted = []
    monkeypatch.setattr(w._genie_job_runner(), "submit", lambda *a, **k: submitted.append(a))
    return submitted


def _shown_now(w) -> bool:
    """The window's canvas shows its document as it is now (_refresh set it)."""
    d = w.doc
    return w.scene.shown == (d, getattr(d, "derive_serial", 0), getattr(d, "touch_serial", 0))


def _marked_unsaved(w) -> bool:
    return w.windowTitle().split(" - ")[0].endswith("\u2022")


def _symbols_lib(fx):
    return fx.file("FxLib", "fxsym.ctl")


def _symbol_saved(fx, element, dx=40):
    """``element`` of fxsym.ctl moved and saved, not through a window (another program, or
    a save before the tab opened)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    s = ElementDocument.open_element(_symbols_lib(fx), element)
    s.move_objects([list(s.page.roots())[0]], dx, dx)
    s.save()
    libraries_changed()


def _place_box_in_n0(fx):
    """fxnest.n0 places the library symbol fxsym.fx_box directly, saved."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(fx.file("FxLib", "fxnest.ctm"), "n0")
    d.place_library_element("fxsym", "fx_box", "ctl", (80, 10))
    d.save()
    libraries_changed()


def _show_again(w, doc):
    """Another tab of ``w`` shown, then ``doc``'s again."""
    k = next(i for i, e in enumerate(w._docs) if e["doc"] is doc)
    w.doc_tabs.setCurrentIndex(0 if k else 1)
    w.doc_tabs.setCurrentIndex(k)
    assert w.doc is doc


# -- a symbol set the chain saved again -----------------------------------------------------
def _set_on_fxgenies(fx) -> str:
    """FxGenies places the symbol set fxsym.fx_set (its states place fx_on and fx_off),
    saved."""
    from ctgedit.editing import EditableDocument, libraries_changed

    page = fx.page("FxGenies")
    d = EditableDocument.open(page)
    d.place_library_element("fxsym", "fx_set", "ctl", (500, 400))
    d.save()
    libraries_changed()
    return page


@pytest.mark.parametrize("symbols", [True, False])
def test_the_chain_brings_an_open_pages_symbol_set_up_to_date(fx_install, win, win2, monkeypatch, symbols):
    """The chain brings an open page's symbol set up to date."""
    from ctgedit.ui.prefs.settings import app_settings

    page = _set_on_fxgenies(fx_install)
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    win2.open_document(page)
    pg = win2.doc
    assert not pg.stale_symbol_elements() and not pg._undo
    app_settings().setValue("opt/symbol_save_pages", "always")
    app_settings().setValue("opt/auto_update_symbols", symbols)
    win._open_element(_symbols_lib(fx_install), "fx_on")
    s = win.doc
    s.move_objects([list(s.page.roots())[0]], 3, 2)
    assert win._save_element_to()
    label = win._users_job.findChild(QtWidgets.QLabel).text()
    assert label.startswith("Updated") and "2 library symbols" in label, label
    if not symbols:
        assert pg.stale_symbol_elements() == {("fxsym", "fx_set")} and not pg._undo
        return
    assert not pg.stale_symbol_elements()
    assert [c.label for c in pg._undo] == ["update symbols from library"]
    assert win2._only_auto_changes(pg), "an automatic change: the bar's Undo, the close question"
    assert win2.doc is pg and _shown_now(win2) and _marked_unsaved(win2)


def test_an_undone_symbol_update_on_a_page_stays_undone(fx_install, win, win2, monkeypatch):
    """The page's automatic update of fx_set after the chain is undone (the bar's Undo): the
    end of another chain, View > Refresh and another program's library write leave it undone;
    so does a symbol save's update of a page, and the open's."""
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.window import libwatch

    page = _set_on_fxgenies(fx_install)
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    win2.open_document(page)
    pg = win2.doc
    app_settings().setValue("opt/symbol_save_pages", "always")
    win._open_element(_symbols_lib(fx_install), "fx_on")
    s = win.doc
    s.move_objects([list(s.page.roots())[0]], 3, 2)
    assert win._save_element_to()
    assert pg._undo
    win2._undo_auto_changes()
    assert not pg._undo and pg.stale_symbol_elements() == {("fxsym", "fx_set")}
    win._after_chain_saves({"elements_saved": ["fxnest.n1"], "libraries": []})
    win2._look_at_open_genies_again()
    libwatch.libraries_changed_on_disk([fx_install.file("FxLib", "fxnest.ctm")], first=win2)
    assert not pg._undo, "the update the user undid is not made again"
    # (a direct placement updated by the symbol's save in another window, then undone)
    win2.open_document(fx_install.page("FxSymbols"))
    sym_page = win2.doc
    steps = len(sym_page._undo)
    app_settings().setValue("opt/symbol_save_pages", "never")
    win._open_element(_symbols_lib(fx_install), "fx_box")
    b = win.doc
    b.move_objects([list(b.page.roots())[0]], 40, 40)
    assert win._save_element_to()
    assert len(sym_page._undo) == steps + 1
    win2.undo()
    assert len(sym_page._undo) == steps and sym_page.stale_symbol_elements()
    libwatch.libraries_changed_on_disk([_symbols_lib(fx_install)], first=win2)
    assert len(sym_page._undo) == steps


def test_a_pages_tab_shown_again_takes_a_changed_symbol(fx_install, win, monkeypatch):
    """A symbol saved while the page's tab was not shown (by a save outside the editor's
    windows): showing the tab again re-derives it, as an element tab's show does."""
    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    page = win.doc
    steps = len(page._undo)
    win.open_document(fx_install.page("FxShapes"))
    _symbol_saved(fx_install, "fx_box")
    assert page.stale_symbol_elements() == {("fxsym", "fx_box")}
    _show_again(win, page)
    assert not page.stale_symbol_elements() and len(page._undo) == steps + 1
    assert win._only_auto_changes(page) and _shown_now(win)


# -- Compose Nested Genies Again -------------------------------------------------------------
def test_compose_nested_genies_again_draws_the_tab_again(fx_install, win, monkeypatch):
    """n2 holds a stale copy of n1 that its stamps call current: the command composes it, and
    the canvas, the title's unsaved mark and Edit > Undo follow, as after any edit."""
    from test_element_nested_update import _lib, _stale_but_current

    _stale_but_current(fx_install)
    _no_jobs(win, monkeypatch)
    win._open_element(_lib(fx_install), "n2")
    d = win.doc
    assert not d.dirty and _shown_now(win) and not win.act_undo.isEnabled()
    win._compose_nested_again()
    assert [c.label for c in d._undo] == ["compose nested genies again"]
    assert _shown_now(win), "the canvas shows the new records"
    assert win.act_undo.isEnabled() and _marked_unsaved(win)


# -- a symbol another program saved ----------------------------------------------------------
@pytest.mark.parametrize("symbols, genies", [(True, True), (True, False), (False, True)])
def test_a_symbol_another_program_saved_reaches_the_open_tabs(
    fx_install, win, win2, monkeypatch, symbols, genies
):
    """FxSymbols is open in window 1 and n0 (placing fx_box) in window 2. Another program saves
    fx_box: the library watch has both re-derive it (Update changed symbols, whatever Update
    changed genies says); the page shown is drawn again. Off: both are left."""
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.window import libwatch

    _place_box_in_n0(fx_install)
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    page = win.doc
    win2._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    elem = win2.doc
    assert not page.stale_symbol_elements() and not elem.stale_symbol_elements()
    steps, esteps = len(page._undo), len(elem._undo)
    _symbol_saved(fx_install, "fx_box")
    app_settings().setValue("opt/auto_update_symbols", symbols)
    app_settings().setValue("opt/auto_update_genies", genies)
    assert libwatch.libraries_changed_on_disk([_symbols_lib(fx_install)], first=win)
    if not symbols:
        assert page.stale_symbol_elements() == {("fxsym", "fx_box")}
        assert elem.stale_symbol_elements() == {("fxsym", "fx_box")}
        assert len(page._undo) == steps and len(elem._undo) == esteps
        return
    assert not page.stale_symbol_elements() and len(page._undo) == steps + 1
    assert not elem.stale_symbol_elements() and len(elem._undo) == esteps + 1
    assert win._only_auto_changes(page) and win2._only_auto_changes(elem)
    assert _shown_now(win) and _shown_now(win2) and _marked_unsaved(win)


# -- a super genie's pages in every window ---------------------------------------------------
@pytest.mark.parametrize("same", [False, True])
def test_a_super_genie_save_makes_its_pages_in_every_window_again(fx_install, win, win2, monkeypatch, same):
    """!fx_detail is made from fxsuper.!fx_detail@SUPER and open in window 2 (or in the saving
    window): saving the super genie in window 1 makes it again in its tab, drawn again there,
    and the note counts it."""
    from ctgedit.ui import notify
    from ctgedit.ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_exec_supergenie_dialog", staticmethod(lambda dlg: "edit"))
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    holder = win if same else win2
    holder.open_document(fx_install.page("!fx_detail"))
    page = holder.doc
    assert page.supergenie_status()[0] == "current" and not page._undo
    win._open_element(fx_install.file("FxLib", "fxsuper.ctm"), "!fx_detail@SUPER")
    d = win.doc
    roots = [i for i, o in enumerate(d.page.objects) if o.parent is None]
    d.move_objects([roots[0]], 9, 0)
    c = notify.centre(win)
    c.clear()
    assert win._save_element_to()
    assert [s.label for s in page._undo] == ["update super genie page"]
    assert page.supergenie_status()[0] == "current"
    assert any(m.text.startswith("Updated 1 open page from super genie") for m in c.entries)
    if not same:
        assert win2.doc is page and _shown_now(win2) and _marked_unsaved(win2)


# -- Revert to Saved runs the open checks ----------------------------------------------------
def test_revert_to_saved_runs_the_open_checks_of_a_page(fx_install, win, monkeypatch):
    """FxSymbols was saved before fx_box changed: its open re-derives fx_box, and so does
    Revert to Saved (it opens the page from disk again)."""
    _no_jobs(win, monkeypatch)
    _symbol_saved(fx_install, "fx_box")
    win.open_document(fx_install.page("FxSymbols"))
    p = win.doc
    assert [c.label for c in p._undo] == ["update symbols from library"]
    assert win.revert_document(p, ask=False)
    p2 = win.doc
    assert p2 is not p
    assert [c.label for c in p2._undo] == ["update symbols from library"]
    assert not p2.stale_symbol_elements() and win._only_auto_changes(p2)
    assert _shown_now(win)


def test_a_disk_change_reload_runs_the_open_checks(fx_install, win, monkeypatch):
    """Settings: reload a page another program changed. FxSymbols is open and current; fx_box
    is saved outside the editor, then another program saves the page (still with the old
    fx_box): the reload takes the new fx_box, as an open does."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.prefs.settings import set_disk_reload

    _no_jobs(win, monkeypatch)
    path = fx_install.page("FxSymbols")
    win.open_document(path)
    p = win.doc
    assert not p._undo and not p.dirty
    _symbol_saved(fx_install, "fx_box")
    other = EditableDocument.open(path)
    other.move_objects([list(other.page.roots())[0]], 1, 0)
    other.save()
    set_disk_reload("always")
    win._check_disk_changes([path])
    p2 = win.doc
    assert p2 is not p, "reloaded"
    assert "update symbols from library" in [c.label for c in p2._undo]
    assert not p2.stale_symbol_elements()


def test_revert_to_saved_of_an_element_takes_its_changed_symbols_again(fx_install, win, monkeypatch):
    _place_box_in_n0(fx_install)
    _no_jobs(win, monkeypatch)
    _symbol_saved(fx_install, "fx_box")
    win._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    d = win.doc
    assert d._undo and not d.stale_symbol_elements()
    assert win.revert_document(d, ask=False)
    d2 = win.doc
    assert d2 is not d
    assert [c.label for c in d2._undo] == ["update symbols from library"]
    assert not d2.stale_symbol_elements() and _shown_now(win)


# -- the undone symbol update and a genie update that did not land ---------------------------
@pytest.mark.parametrize("how", ["failed", "cancelled", "moved"])
def test_an_undone_symbol_update_stays_undone_after_a_genie_update_that_did_not_land(
    fx_install, win, win2, monkeypatch, how
):
    """fxparts.fx_sets places fxsym.fx_flash; its automatic update is undone. A genie update
    of the tab then fails, is cancelled from the tasks button, or goes with the tab to
    another window: each empties the tab's genie memo, never the symbol versions it took, so
    a later library change and show leave the symbol update undone."""
    from ctgedit.editing import libraries_changed
    from ctgedit.ui.jobs.genie_jobs import GenieJob

    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    _symbol_saved(fx_install, "fx_flash")
    win._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_sets")
    d = win.doc
    assert d._undo and not d.stale_symbol_elements()
    win._undo_auto_changes()
    assert not d._undo and d.stale_symbol_elements() == {("fxsym", "fx_flash")}
    d.ui.genie_sources_done[("fxnest", "n0")] = frozenset({"x"})
    holder = win
    if how == "failed":
        win._genie_job_finished(d, ["fxnest.n0"], -1)
    elif how == "cancelled":
        win._genie_job_runner().stop_job(GenieJob(d, {}, ["fxnest.n0"]))
    else:
        monkeypatch.setattr(win._genie_job_runner(), "busy", lambda doc=None: True)
        win._move_entry_to(next(e for e in win._docs if e["doc"] is d), win2)
        holder = win2
    assert ("fxnest", "n0") not in d.ui.genie_sources_done, "the genie memo was emptied"
    assert not d._undo, "the update the user undid is not made again"
    holder.open_document(fx_install.page("FxShapes"))
    libraries_changed()  # (another element saved somewhere)
    _show_again(holder, d)
    assert not d._undo and d.stale_symbol_elements() == {("fxsym", "fx_flash")}


# -- a run that saves library genies ---------------------------------------------------------
def test_a_page_opened_while_a_run_saves_library_genies_waits_for_its_end(fx_install, win, monkeypatch):
    """n0 changed; Update Pages' chain saves its hosts one after another. FxGenies, opened
    meanwhile, is not composed against the libraries half written: it is when the run has
    ended (once)."""
    # (Settings 'Open tabs wait for the update of what uses a saved genie' on: the
    # behaviour this pins; off, the default, the tab takes it at once)
    from ctgedit.ui.prefs.settings import app_settings as _st

    _st().setValue("opt/tabs_wait_for_runs", True)
    from test_element_nested_update import _edit_n0
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    _edit_n0(fx_install)
    submitted = _no_jobs(win, monkeypatch)
    app_settings().setValue("opt/job_poll_ms", 150)  # (Long jobs: how often it looks)
    UpdatePagesRun.chain_busy = True
    win.open_document(fx_install.page("FxGenies"))
    p = win.doc
    assert submitted == [] and p.genie_changes()
    assert win._after_run_timer.isActive() and win._after_run_timer.interval() == 150
    win._queue_open_genie_updates()  # (another genie saved meanwhile)
    assert submitted == []
    assert win._library_run_over() == []  # (still running)
    UpdatePagesRun.chain_busy = False
    UpdatePagesRun.tabs_following = True  # (a run here: its open tabs' step)
    assert win._library_run_over() == []
    UpdatePagesRun.tabs_following = False
    assert win._library_run_over() == [p]
    assert [a[0] for a in submitted] == [p]


@pytest.mark.parametrize("genies", [True, False])
def test_an_element_first_shown_while_a_run_saves_waits_for_its_end(fx_install, win, monkeypatch, genies):
    """The chain after a library save runs; n1, opened meanwhile, composes its copy of n0 (or
    with Update changed genies off re-derives its symbols) when the run has ended."""
    # (Settings 'Open tabs wait for the update of what uses a saved genie' on: the
    # behaviour this pins; off, the default, the tab takes it at once)
    from ctgedit.ui.prefs.settings import app_settings as _st

    _st().setValue("opt/tabs_wait_for_runs", True)
    from test_element_nested_update import _edit_n0
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    app_settings().setValue("opt/auto_update_genies", genies)
    if genies:
        _edit_n0(fx_install)
    else:
        _symbol_saved(fx_install, "fx_flash")
    submitted = _no_jobs(win, monkeypatch)
    UpdatePagesRun.users_busy = "fxnest.n0"
    if genies:
        win._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n1")
    else:
        win._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_sets")
    host = win.doc
    assert submitted == [] and not host._undo
    UpdatePagesRun.users_busy = ""
    assert win._library_run_over() == [host]
    if genies:
        assert [a[0] for a in submitted] == [host]
    else:
        assert host._undo and not host.stale_symbol_elements()


def _link_table_answer(doc) -> tuple:
    """A worker answer for ``doc`` that replaces no record and changes one stamp of its link
    table (as an answer that only takes the genies' current stamps does); its new table."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.format import links

    pb = doc.parsed.page
    area, ents = links.parse_link_table(pb.link_table_raw)
    ents = list(ents)
    g, name = ents[-1]
    ents[-1] = (g, name[:-1] + ("0" if name[-1] != "0" else "1"))
    raw, cnt = links.serialize_link_table(area, ents)
    return {
        "format": GW.FORMAT,
        "ops": [],
        "link_table": [raw.hex(), cnt, (pb.link_table_raw or b"").hex(), pb.link_count],
    }, raw


def test_a_genie_update_that_changes_only_the_link_table_is_an_automatic_change(fx_install, win):
    """A worker answer that replaces no record but gives the page's link table the genies'
    current stamps makes an undo step: it is listed as an automatic change (the bar's Undo,
    the close question and the chain call the page changed only by them), the title marked."""
    from ctgedit.ui.jobs.genie_jobs import GenieJob

    win.open_document(fx_install.page("FxGenies"))
    p = win.doc
    assert not p._undo
    pb = p.parsed.page
    ans, raw = _link_table_answer(p)
    jobs = win._genie_job_runner()
    jobs._ready.append((GenieJob(p, {("fxnest", "n4"): None}, ["fxnest.n4"]), ans, 0.0))
    jobs._poll()
    assert len(p._undo) == 1 and p.dirty and pb.link_table_raw == raw
    assert win._only_auto_changes(p)
    assert [w for w, _c in p.ui.auto_changes] == ["fxnest.n4 updated from its library"]
    assert _marked_unsaved(win) and win.act_undo.isEnabled()


def test_a_remembered_link_table_answer_applied_at_the_open_is_an_automatic_change(
    fx_install, win, monkeypatch
):
    """The remembered answer of a stale page's genie update replaces no record and only
    changes its link table: the open applies it before the page's derive (one undo step) and
    lists it as an automatic change, quietly (nothing on the page changed)."""
    from test_element_nested_update import _edit_n0
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import answer_memo
    from ctgedit.ui import notify

    _edit_n0(fx_install)
    path = fx_install.page("FxGenies")
    probe = EditableDocument.open(path)
    changes = probe.genie_changes()
    assert changes
    ans, raw = _link_table_answer(probe)
    answer_memo.put(probe.genie_noop_key(changes), ans)
    _no_jobs(win, monkeypatch)
    c = notify.centre(win)
    c.clear()
    win.open_document(path)
    p = win.doc
    assert p.parsed.page.link_table_raw == raw and len(p._undo) == 1
    assert win._only_auto_changes(p)
    assert not any(m.text.startswith("Updated ") for m in c.entries)


# -- more runs that save library genies, and symbols saved elsewhere --------------------------
def test_a_page_waiting_for_the_runs_end_has_an_update_on_its_way_for_simulate(fx_install, win, monkeypatch):
    """A page opened while a run saves library genies waits for the run's end, then a moment
    more (the window's timer looks as often as Long jobs says). Simulate, which reloads a
    page that follows the document once the editor's update of it has landed, counts that
    wait as an update on its way."""
    # (Settings 'Open tabs wait for the update of what uses a saved genie' on: the
    # behaviour this pins; off, the default, the tab takes it at once)
    from ctgedit.ui.prefs.settings import app_settings as _st

    _st().setValue("opt/tabs_wait_for_runs", True)
    from test_element_nested_update import _edit_n0
    from ctgedit.ui.simulate import live_reload
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    _edit_n0(fx_install)
    submitted = _no_jobs(win, monkeypatch)
    UpdatePagesRun.chain_busy = True
    win.open_document(fx_install.page("FxGenies"))
    p = win.doc
    assert submitted == [] and live_reload.editor_genie_update_pending(p)
    UpdatePagesRun.chain_busy = False
    assert live_reload.editor_genie_update_pending(p), "until the window has looked"
    assert win._library_run_over() == [p]
    assert [a[0] for a in submitted] == [p]
    assert not live_reload.editor_genie_update_pending(p)


def test_a_remembered_answer_waits_too_when_a_page_opens_while_a_run_saves(fx_install, win, monkeypatch):
    """The remembered answer of a stale page's genie update is not applied before the derive
    of a page opened while a run saves library genies (the libraries are part written): the
    open's genie step waits for the run's end, and the answer is applied then, once."""
    # (Settings 'Open tabs wait for the update of what uses a saved genie' on: the
    # behaviour this pins; off, the default, the tab takes it at once)
    from ctgedit.ui.prefs.settings import app_settings as _st

    _st().setValue("opt/tabs_wait_for_runs", True)
    from test_element_nested_update import _edit_n0
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import answer_memo
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    _edit_n0(fx_install)
    path = fx_install.page("FxGenies")
    probe = EditableDocument.open(path)
    changes = probe.genie_changes()
    assert changes
    ans, raw = _link_table_answer(probe)
    answer_memo.put(probe.genie_noop_key(changes), ans)
    submitted = _no_jobs(win, monkeypatch)
    UpdatePagesRun.users_busy = "fxnest.n0"
    win.open_document(path)
    p = win.doc
    assert not p._undo and p.parsed.page.link_table_raw != raw, "not applied mid-run"
    UpdatePagesRun.users_busy = ""
    assert win._library_run_over() == [p]
    assert submitted == [] and len(p._undo) == 1 and p.parsed.page.link_table_raw == raw
    assert win._only_auto_changes(p)


def test_a_link_table_only_update_composed_at_once_is_an_automatic_change(fx_install, win, monkeypatch):
    """With no event loop (a script) the genie update is composed at once, not in the
    worker: an update that changed only the page's link table is an automatic change there
    too, as a worker's answer is."""
    from test_element_nested_update import _edit_n0
    from ctgedit.ui.jobs import genie_jobs

    _edit_n0(fx_install)
    win.open_document(fx_install.page("FxGenies"))
    p = win.doc
    while p._undo:  # (the open's own update undone)
        p.undo()
    p.ui.auto_changes = []
    ans, raw = _link_table_answer(p)
    monkeypatch.setattr(p, "apply_genie_changes", lambda changes: genie_jobs.apply_answer(p, ans))
    win._genie_job_runner().submit(p, {("fxnest", "n4"): None}, ["fxnest.n4"])
    assert len(p._undo) == 1 and p.parsed.page.link_table_raw == raw
    assert win._only_auto_changes(p)
    assert [w for w, _c in p.ui.auto_changes] == ["fxnest.n4 updated from its library"]


@pytest.mark.parametrize("kind", ["element", "page"])
def test_a_tab_opened_while_the_chain_runs_takes_what_its_worker_saved(
    fx_install, win, win2, monkeypatch, kind
):
    """A tab opened while the chain runs takes what its worker saved."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui.prefs.settings import app_settings
    from test_element_nested_update import _lib

    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    app_settings().setValue("opt/genie_save_pages", "always")
    win._open_element(_lib(fx_install), "n0")
    d0 = win.doc
    i = next(k for k, o in enumerate(d0.parsed.page.objects) if o.type_code == 0x05)
    assert d0.set_text(i, "%Label% CHANGED")
    orig = GW.update_closed_pages
    opened = {}

    def run(*a, **k):
        # (opened now: the lists the worker reads were written before)
        if kind == "element":
            win2._open_element(_lib(fx_install), "n1")
        else:
            win2.open_document(fx_install.page("FxGenies"))
        opened["doc"] = win2.doc
        return orig(*a, **k)

    monkeypatch.setattr(GW, "update_closed_pages", run)
    assert win._save_element_to()
    label = win._users_job.findChild(QtWidgets.QLabel).text()
    assert label.startswith("Updated"), label
    d = win2.doc
    assert not d.changed_on_disk() and not d.dirty and not d._undo
    if kind == "element":
        assert d is opened["doc"] and not d.nested_genie_changes()
    else:
        assert d is not opened["doc"], "reloaded from the file the worker saved"
        assert not d.genie_changes()
