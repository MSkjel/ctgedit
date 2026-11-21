"""What follows a library save."""

import gc
import hashlib
import json
import os
import sys
import weakref

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtWidgets  # noqa: E402
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
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
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
    st.remove("opt/disk_reload")
    UpdatePagesRun.users_busy = ""
    UpdatePagesRun.chain_busy = False
    UpdatePagesRun.tabs_following = False
    UpdatePagesRun.users_queue.clear()


def _no_jobs(w, monkeypatch):
    submitted = []
    monkeypatch.setattr(w._genie_job_runner(), "submit", lambda *a, **k: submitted.append(a))
    return submitted


def _symbols_lib(fx):
    return fx.file("FxLib", "fxsym.ctl")


def _box_gains_a_record(fx):
    """fxsym.fx_box gains a rectangle and is saved, not through a window (another program)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    s = ElementDocument.open_element(_symbols_lib(fx), "fx_box")
    s.create_object("rectangle", rect=(0, 0, 12, 8))
    s.save()
    libraries_changed()


def _element_moved(fx, lib: str, element: str) -> None:
    """``element`` of ``lib`` moved 2 px and saved (not through a window)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(fx.file("FxLib", lib), element)
    d.move_objects([list(d.page.roots())[0]], 2, 0)
    d.save()
    libraries_changed()


def _entry(w, doc):
    return next(e for e in w._docs if e["doc"] is doc)


def _show(w, doc):
    w.doc_tabs.setCurrentIndex(w._entry_index(_entry(w, doc)))
    assert w.doc is doc


def _labels(doc):
    return [c.label for c in doc._undo]


# -- the text DPI and the fonts are part of the memo keys ------------------------------------
def test_the_genie_memo_keys_hold_what_texts_are_measured_with(fx_install, tmp_path, monkeypatch):
    """The answer a helper composed at one DPI is not replayed at another: the memo context
    holds the DPI the texts are measured at and the font folders' stamps."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument
    from ctgedit.format import textmetrics
    from ctgedit.infra import answer_memo

    _element_moved(fx_install, "fxgenie.ctm", "fx_pump")
    page = fx_install.page("FxGenies")
    data = open(page, "rb").read()
    d = EditableDocument.open(page)
    changes = d.genie_changes()
    assert changes
    key = d.genie_noop_key(changes)
    GW.compute(page, data, changes)  # (the helper's answer, at this DPI)
    assert answer_memo.get(key) is not None
    dpi = textmetrics.system_dpi() or 96
    monkeypatch.setattr(textmetrics, "system_dpi", lambda: dpi * 3 // 2)
    key144 = d.genie_noop_key(changes)
    assert key144 != key and answer_memo.get(key144) is None, (
        "an answer composed at another DPI is not replayed"
    )
    monkeypatch.setattr(textmetrics, "system_dpi", lambda: dpi)
    assert d.genie_noop_key(changes) == key
    # (a font installed: its folder's time moves)
    fonts = tmp_path / "Fonts"
    fonts.mkdir()
    monkeypatch.setattr(textmetrics, "_font_folders", lambda: [str(fonts)])
    k1 = d.genie_noop_key(changes)
    st = os.stat(fonts)
    os.utime(fonts, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert d.genie_noop_key(changes) != k1


# -- old-form pages: the editor and its genie job key them alike ------------------------------
def test_an_old_form_page_finds_the_answer_its_genie_job_left(fx_install, tmp_path, monkeypatch):
    """FxOld9 (word2 9) is read upgraded, with a new GUID: its bytes in the job are not its
    file's. The job takes the digest the editor keys the page by, so the next open finds
    the answer."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.doc import history
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import answer_memo

    # (the helper's main sets these for its own process: kept as they are here)
    monkeypatch.setattr(GW, "helper_waits", lambda: None)
    monkeypatch.setattr(history, "SAVE_THUMBNAIL", history.SAVE_THUMBNAIL)
    _element_moved(fx_install, "fxgenie.ctm", "fx_plain")
    page = fx_install.page("FxOld9")
    raw = open(page, "rb").read()
    d = EditableDocument.open(page)
    assert d.parsed.container.upgraded_from == 9
    changes = d.genie_changes()
    assert changes and not d._undo and not d.dirty
    key = d.genie_noop_key(changes)
    data = d.serialize()
    assert hashlib.sha1(data).digest() != hashlib.sha1(raw).digest()
    assert d.genie_page_digest(data) == hashlib.sha1(raw).digest()
    req = GW.write_request(str(tmp_path), page, data, changes, page_sha1=d.genie_page_digest(data).hex())
    ans = req[:-5] + ".answer.json"
    assert GW.main([req, ans]) == 0
    with open(ans, encoding="utf-8") as fh:
        out = json.load(fh)
    assert not out.get("error") and out["ops"]
    assert answer_memo.get(key) == out
    d2 = EditableDocument.open(page)  # (the next open: another GUID)
    assert d2.genie_noop_key(d2.genie_changes()) == key


def test_a_genie_job_sends_the_digest_the_editor_keys_the_page_by(fx_install):
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.jobs import genie_jobs

    page = fx_install.page("FxOld10")
    d = EditableDocument.open(page)

    class Worker:
        env = None

        def submit(self, req, ans):
            self.req = req

    job = genie_jobs.GenieJob(d, {("fxgenie", "fx_plain"): None}, ["fxgenie.fx_plain"])
    w = Worker()
    try:
        job.start(w)
        with open(w.req, encoding="utf-8") as fh:
            req = json.load(fh)
        with open(page, "rb") as fh:
            raw = fh.read()
        assert req["page_sha1"] == hashlib.sha1(raw).hexdigest()
    finally:
        job.cleanup()


# -- a tab shown again keeps the user's steps to redo ---------------------------------------
def test_a_tab_shown_again_keeps_the_steps_to_redo_its_symbol_step_waits_for(
    fx_install, win, win2, monkeypatch
):
    """FxSymbols has a step to redo when another program changes fx_box, while another tab
    is shown: the symbol step waits. Shown again, the page is drawn again but the step goes on
    waiting (the show made it and cleared the redo); a minute after the first wait, or moved
    to another window, the minute is the same."""
    from ctgedit.ui.window import libwatch

    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    win.open_document(fx_install.page("FxShapes"))
    shapes = win.doc
    win.open_document(fx_install.page("FxSymbols"))
    d = win.doc
    d.move_objects([list(d.page.roots())[3]], 2, 2)
    win.undo()
    assert d.can_redo()
    _show(win, shapes)
    _box_gains_a_record(fx_install)
    libwatch.libraries_changed_on_disk([_symbols_lib(fx_install)], first=win)
    assert win._waits_until_idle(d) and d.can_redo() and not d._undo
    since = next(item[1] for item in win._idle_docs if item[0] is d)
    serial = d.derive_serial
    _show(win, d)
    assert d.can_redo() and not d._undo, "the show keeps the redo"
    assert win._waits_until_idle(d) and d.derive_serial != serial, "drawn again"
    assert next(item[1] for item in win._idle_docs if item[0] is d) == since
    # (moved to another window: the same minute)
    win._move_entry_to(_entry(win, d), win2)
    assert win2.doc is d and d.can_redo() and not d._undo
    assert next(item[1] for item in win2._idle_docs if item[0] is d) == since
    win2._idle_docs[0][1] -= 61  # (a minute later)
    assert win2._idle_tick() == [d]
    assert _labels(d) == ["update symbols from library"] and not d.can_redo()


def test_an_undone_automatic_update_is_no_step_of_the_users(fx_install, win, monkeypatch):
    """The steps to redo that hold an automatic change are the user's: an automatic update
    the user undid (the bar's Undo) does not hold a new one."""
    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    d = win.doc
    d.create_object("rectangle", rect=(0, 0, 10, 10))
    win._note_auto_change(d, "a test update", d._undo[-1])
    assert d._undo[-1].automatic
    win._undo_auto_changes()
    assert d.can_redo() and not win._user_can_redo(d)
    assert win._genie_may_land(d, 0.0) and not win._redo_holds(d)
    d.redo()
    d.create_object("rectangle", rect=(20, 0, 30, 10))
    d.undo()
    assert win._user_can_redo(d) and not win._genie_may_land(d, 0.0)
    assert win._redo_holds(d)


# -- a closed window -----------------------------------------------------------------------
def test_a_closed_window_stops_its_timers_and_lets_its_pages_go(fx_install, win2, monkeypatch):
    """FxSymbols in a second window waits for its steps to redo when the window is closed:
    its timers stop, its waits and tabs go, and nothing lands on the page afterwards."""
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.window import libwatch

    _no_jobs(win2, monkeypatch)
    win2.show()  # (laid out: no first-view step waits for the window)
    win2.open_document(fx_install.page("FxSymbols"))
    d = win2.doc
    d.move_objects([list(d.page.roots())[3]], 2, 2)
    win2.undo()
    _box_gains_a_record(fx_install)
    libwatch.libraries_changed_on_disk([_symbols_lib(fx_install)], first=win2)
    assert win2._waits_until_idle(d) and win2._idle_timer.isActive()
    win2._on_library_files_changed([_symbols_lib(fx_install)])
    assert win2._lib_timer.isActive()
    ref = weakref.ref(d)
    d.dirty = False
    win2.close()
    assert win2 not in MainWindow._instances
    assert not win2._idle_timer.isActive() and not win2._lib_timer.isActive()
    assert not win2._docs and win2.doc is None and not win2._idle_docs
    assert win2._idle_tick() == [] and not d._undo, "nothing lands on the closed page"
    d = None
    gc.collect()
    assert ref() is None, "the closed window let its document go"


def test_a_closed_window_is_deleted_once_its_tasks_have_ended(fx_install):
    """In the event loop a closed window is deleted, after the tasks it runs have ended (the
    chain after a save goes on on a timer of the window)."""
    import shiboken6
    from ctgedit.ui.jobs import tasks
    from ctgedit.ui.prefs.job_options import poll_ms

    w = _make_win()
    w.open_document(fx_install.page("FxShapes"))
    token = tasks.begin("users", "A test run…", window=w)

    def spin(ms, before=None):
        loop = QtCore.QEventLoop()

        def go():
            if before is not None:
                before()
            QtCore.QTimer.singleShot(ms, loop.quit)

        QtCore.QTimer.singleShot(0, go)
        loop.exec()

    try:
        spin(100, w.close)
        assert shiboken6.isValid(w), "kept while its task runs"
        tasks.end(token, tell=False)
        token = None
        spin(poll_ms() + 300)
        assert not shiboken6.isValid(w), "deleted once its task ended"
    finally:
        if token is not None:
            tasks.drop(token)


# -- a moved tab keeps its mark to be drawn again -------------------------------------------
def test_a_moved_tab_marked_to_be_drawn_again_is_drawn_again(fx_install, win, win2, monkeypatch):
    """FxParts waits in a hidden tab, marked to be drawn again after fx_flash (a state of its
    symbol sets) is saved in an element tab of the window (genies off: no update redraws it);
    moved to another window, it shows the symbol as saved."""
    from ctgedit import editing
    from ctgedit.format.page import parse_ctg_bytes
    from ctgedit.io.reader import derive_page, model_difference
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/auto_update_genies", False)
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    win.open_document(fx_install.page("FxParts"))
    pd = win.doc
    win2.open_document(fx_install.page("FxShapes"))
    win._open_element(_symbols_lib(fx_install), "fx_flash")
    el = win.doc
    el.move_objects([list(el.page.roots())[0]], 6, 0)
    assert win._save_element_to()
    e = _entry(win, pd)
    assert e.get("stale")
    win._move_entry_to(e, win2)
    assert win2.doc is pd
    full, _ = derive_page(
        parse_ctg_bytes(pd.serialize()),
        pd.path,
        records_fresh=True,
        generation=editing._LIBRARY_GENERATION[0],
    )
    assert not model_difference(pd.page, full), "drawn from the library as saved"


# -- an automatic reload waits for the user -------------------------------------------------
def _saved_by_another_program(path: str) -> None:
    from ctgedit.editing import EditableDocument

    other = EditableDocument.open(path)
    other.create_object("rectangle", rect=(300, 300, 340, 330))
    other.save()


def _edit_in_place(w, typed: str) -> int:
    d = w.doc
    idx = next(
        k for k, o in enumerate(d.page.objects) if o.kind.name == "TEXT" and (o.style.text or "").strip()
    )
    assert w.edit_text_in_place(idx)
    w.view.text_edit_item().setPlainText(typed)
    assert w.view.text_editing() and w.view.busy() and not d.dirty
    return idx


def test_the_chains_reload_waits_for_an_in_place_edit(fx_install, win, monkeypatch):
    """The chain's helper saved a page whose tab has no changes of the user's while they
    type in an in-place editor on it: the tab is not reloaded under the edit (the typed text
    went with the version thrown away); once stored, the text is the user's change and the
    tab keeps it."""
    _no_jobs(win, monkeypatch)
    page = fx_install.page("FxShapes")
    win.open_document(page)
    d = win.doc
    idx = _edit_in_place(win, "TYPED BY USER")
    _saved_by_another_program(page)
    assert d.changed_on_disk()
    assert win._settle_unlisted_tabs({"pages": [page]}) == 1
    assert win.doc is d and win.view.text_editing() and win._waits_until_idle(d)
    assert win._idle_tick() == [] and win.doc is d, "nothing while the user types"
    win._flush_text_edit()  # (the edit is stored)
    assert d.dirty and d.page.objects[idx].style.text == "TYPED BY USER"
    win._idle_tick()
    assert win.doc is d and d.page.objects[idx].style.text == "TYPED BY USER"


def test_the_chains_reload_comes_once_the_user_has_finished(fx_install, win, monkeypatch):
    """The same with an in-place edit cancelled: once the user has finished, the clean tab
    takes what the helper saved."""
    _no_jobs(win, monkeypatch)
    page = fx_install.page("FxShapes")
    win.open_document(page)
    d = win.doc
    _edit_in_place(win, "NOT KEPT")
    _saved_by_another_program(page)
    assert win._settle_unlisted_tabs({"pages": [page]}) == 1
    assert win.doc is d
    win.view.finish_text_edit(False)  # (Esc)
    assert not d.dirty
    win._idle_tick()
    assert win.doc is not d and not win.doc.changed_on_disk(), "reloaded"


def test_an_element_tab_reads_the_store_again_once_the_drag_ends(fx_install, win, monkeypatch):
    """An element tab the chain's helper saved from its clean state reads the store again,
    not in the middle of a drag on it (its records changed under the drag)."""
    from ctgedit.editing import ElementDocument

    _no_jobs(win, monkeypatch)
    win._open_element(_symbols_lib(fx_install), "fx_box")
    d = win.doc
    reads = []
    real = ElementDocument.reload_stored
    monkeypatch.setattr(
        ElementDocument, "reload_stored", lambda self, *a, **k: (reads.append(self), real(self, *a, **k))[1]
    )
    busy = {"view": True}
    monkeypatch.setattr(win.view, "busy", lambda: busy["view"])
    win._settle_saved_tabs([[d.path, d.element_name]], reload=True)
    assert reads == [] and win._waits_until_idle(d)
    assert win._idle_tick() == [] and reads == []
    busy["view"] = False
    assert win._idle_tick() == [d] and reads == [d]


def test_the_disk_checks_reload_waits_for_an_in_place_edit(fx_install, win, monkeypatch):
    """Settings 'reload it' for a page another program changed: not under an in-place edit
    on it; the edit stored, the page has the user's change and is left."""
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import set_disk_reload

    _no_jobs(win, monkeypatch)
    asked = []
    monkeypatch.setattr(MainWindow, "_exec_disk_prompt", staticmethod(asked.append))
    set_disk_reload("always")
    page = fx_install.page("FxShapes")
    win.open_document(page)
    d = win.doc
    idx = _edit_in_place(win, "TYPED BY USER")
    _saved_by_another_program(page)
    win._check_disk_changes([page])
    assert win.doc is d and win.view.text_editing()
    win._flush_text_edit()
    win._check_disk_changes([page])
    assert win.doc is d and d.page.objects[idx].style.text == "TYPED BY USER"
    assert d.dirty and not asked


def test_revert_to_saved_asks_about_an_in_place_edit(fx_install, win, monkeypatch):
    """File > Revert to Saved with an in-place edit open: the typed text is a change the
    question asks about (it went into the version thrown away, unasked)."""
    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxShapes"))
    d = win.doc
    idx = _edit_in_place(win, "TYPED BY USER")
    asked = []
    monkeypatch.setattr(win, "_confirm", lambda *a, **k: asked.append(a[0]) or False)
    assert not win.revert_document()
    assert asked == ["Revert to Saved"]
    assert win.doc is d and d.page.objects[idx].style.text == "TYPED BY USER"


# -- the library watch's wording -----------------------------------------------------------
def test_the_library_watch_says_the_page_shown_waits(fx_install, win, monkeypatch):
    import types
    from ctgedit.ui.window import libwatch

    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    d = win.doc
    serial = d.derive_serial
    monkeypatch.setattr(win.view, "busy", lambda: True)  # (the user drags on it)
    _box_gains_a_record(fx_install)
    assert libwatch.libraries_changed_on_disk([_symbols_lib(fx_install)], first=win) == []
    assert win.statusBar().currentMessage() == (
        "fxsym.ctl was changed by another program: the page shown is updated when you have finished."
    )
    assert d.derive_serial == serial and win._waits_until_idle(d)
    a, b = types.SimpleNamespace(), types.SimpleNamespace()
    assert (
        libwatch._watch_result([a], [], [b])
        == "1 other page was drawn again, and the page shown is updated when you have finished"
    )
    assert libwatch._watch_result([a], [a], [b]) == (
        "1 other page was drawn again and the symbols on 1 other page were updated, and "
        "the page shown is updated when you have finished"
    )
    assert libwatch._watch_result([], [a, b], [d]) == (
        "the symbols on 2 other pages were updated, and the page shown is updated when you have finished"
    )


# -- the template step that waited, during a run --------------------------------------------
def test_the_template_step_that_waited_waits_for_a_run(fx_install, win, monkeypatch):
    """The chain's template step of FxTplLast waited for a drag; the drag ends while a run
    saves library elements: the step waits for the run's end (it reads the template's
    library), as the symbol and genie steps do; then it lands."""
    # (Settings 'Open tabs wait for the update of what uses a saved genie' on: the
    # behaviour this pins; off, the default, the tab takes it at once)
    from ctgedit.ui.prefs.settings import app_settings as _st

    _st().setValue("opt/tabs_wait_for_runs", True)
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    _no_jobs(win, monkeypatch)
    busy = {"view": False}
    monkeypatch.setattr(win.view, "busy", lambda: busy["view"])
    win.open_document(fx_install.page("FxTplLast"))
    page = win.doc
    lib = fx_install.file("FxLib", "fxgenie.ctm")
    el = ElementDocument.open_element(lib, "fx_value")
    i = next(k for k, o in enumerate(el.parsed.page.objects) if o.type_code == 0x05)
    assert el.set_text(i, "MARKXYZ")
    el.save()
    libraries_changed()

    class Saved:
        path, element_name, display_name = lib, "fx_value", "fxgenie.fx_value"

    busy["view"] = True
    win._update_closed_pages(Saved())
    assert win._waits_until_idle(page) and page.template_status()[0] == "stale"
    busy["view"] = False
    UpdatePagesRun.users_busy = "fxgenie.fx_other"
    assert win._idle_tick() == [] and page.template_status()[0] == "stale"
    # (shown meanwhile: it goes on waiting)
    win.open_document(fx_install.page("FxShapes"))
    _show(win, page)
    assert win._waits_until_idle(page) and page.template_status()[0] == "stale"
    UpdatePagesRun.users_busy = ""
    assert win._idle_tick() == [page]
    assert page.template_status()[0] == "current"


# -- more of what follows a library save -----------------------------------------------------------
def test_a_chain_a_closed_window_queued_starts_in_another_window(fx_install, win, monkeypatch):
    """A save in a second window queues the chain after it while another run's tabs follow
    (the queue looks again each second); the window is closed and deleted meanwhile. The
    chain starts in a window still open."""
    import shiboken6
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    started = []
    monkeypatch.setattr(MainWindow, "_start_users_run", lambda self, *t: started.append((self, t)) or True)
    w2 = _make_win()
    UpdatePagesRun.tabs_following = True

    class Saved:
        path, element_name, display_name = _symbols_lib(fx_install), "fx_box", "fxsym.fx_box"

    def spin(ms, before=None):
        loop = QtCore.QEventLoop()

        def go():
            if before is not None:
                before()
            QtCore.QTimer.singleShot(ms, loop.quit)

        QtCore.QTimer.singleShot(0, go)
        loop.exec()

    w2._update_closed_pages(Saved())
    assert UpdatePagesRun.users_queue and not started
    spin(200, w2.close)
    assert not shiboken6.isValid(w2), "the closed window is deleted"
    UpdatePagesRun.tabs_following = False
    spin(1400)
    assert len(started) == 1 and started[0][1][1] == "fx_box"
    assert started[0][0] is win and shiboken6.isValid(started[0][0])


def test_the_changed_on_disk_box_waits_for_an_in_place_edit(fx_install, win, monkeypatch):
    """Settings 'Ask me' with the box: a page another program changed while the user types in
    a text on it is not asked about in the middle of the edit; once the edit is stored, the
    box says the page has changes of the user's."""
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings, set_disk_reload

    _no_jobs(win, monkeypatch)
    asked = []

    def answer(box):
        asked.append(box.text())
        box.buttons()[0].click()  # (Reload)

    monkeypatch.setattr(MainWindow, "_exec_disk_prompt", staticmethod(answer))
    set_disk_reload("ask")
    app_settings().setValue("ui/disk_change_prompt", "dialog")
    try:
        page = fx_install.page("FxShapes")
        win.open_document(page)
        d = win.doc
        _edit_in_place(win, "TYPED BY USER")
        _saved_by_another_program(page)
        win._check_disk_changes([page])
        assert asked == [] and win.doc is d and win.view.text_editing(), (
            "no question in the middle of the edit"
        )
        assert win._disk_timer.isActive() and page in win._disk_pending
        win._flush_text_edit()
        win._check_disk_changes()
        assert len(asked) == 1 and "unsaved changes" in asked[0]
    finally:
        app_settings().remove("ui/disk_change_prompt")


def test_the_genie_memo_keys_hold_the_code_page(fx_install, monkeypatch):
    """The code page texts are encoded and measured in (Settings: a start with another code
    page) is part of the memo keys: an answer composed with cp1252 is not replayed with
    cp1250."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import textcodec

    _element_moved(fx_install, "fxgenie.ctm", "fx_pump")
    d = EditableDocument.open(fx_install.page("FxGenies"))
    changes = d.genie_changes()
    key = d.genie_noop_key(changes)
    monkeypatch.setattr(textcodec, "CODEPAGE", 1250 if textcodec.CODEPAGE != 1250 else 1252)
    assert d.genie_noop_key(changes) != key


def test_a_reload_that_waited_draws_the_page_kept_for_an_edit(fx_install, win, monkeypatch):
    """The page shown waits with the chain's reload and the library watch's redraw while the
    user types in a text on it; the typed text is stored, so the tab is kept: it is still
    drawn again from the library files as they are (the redraw went with the reload)."""
    _no_jobs(win, monkeypatch)
    page = fx_install.page("FxShapes")
    win.open_document(page)
    d = win.doc
    _edit_in_place(win, "TYPED BY USER")
    _saved_by_another_program(page)
    assert win._settle_unlisted_tabs({"pages": [page]}) == 1
    win._wait_until_idle(d, redraw=True)  # (the library watch, during the edit)
    win._flush_text_edit()
    assert d.dirty
    serial = d.derive_serial
    win._idle_tick()
    assert win.doc is d and d.derive_serial != serial, "drawn again"


def test_the_library_watch_counts_the_pages_shown_that_wait(fx_install, win, win2, monkeypatch):
    import types
    from ctgedit.ui.window import libwatch

    a, b = types.SimpleNamespace(), types.SimpleNamespace()
    assert libwatch._watch_result([], [], [a, b]) == "the pages shown are updated when you have finished"
    assert (
        libwatch._watch_result([a], [], [a, b])
        == "1 other page was drawn again, and the pages shown are updated when you have finished"
    )


def test_how_long_an_update_waits_for_the_steps_to_redo_is_a_setting(fx_install, win, monkeypatch):
    """Settings > Pages and libraries > When a page opens: 'An update waits for your steps to
    redo' (60 s; 0: no wait). The genie answer, the symbol step at a show and the idle wait
    read it; a look that leaves the steps to redo out does so whatever it is."""
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.window import files

    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxShapes"))
    d = win.doc
    d.create_object("rectangle", rect=(0, 0, 10, 10))
    d.undo()
    st = app_settings()
    try:
        assert files.redo_wait_s() == 60
        assert not win._genie_may_land(d, 59.0) and win._genie_may_land(d, 61.0)
        st.setValue("opt/auto_update_redo_wait_s", 120)
        assert not win._genie_may_land(d, 90.0) and win._redo_holds(d)
        assert win._may_change_now(d, redo=False), "the steps to redo left out"
        st.setValue("opt/auto_update_redo_wait_s", 0)
        assert win._genie_may_land(d, 0.0) and not win._redo_holds(d)
        st.setValue("opt/auto_update_redo_wait_s", -5)  # (out of its span)
        assert files.redo_wait_s() == 60
    finally:
        st.remove("opt/auto_update_redo_wait_s")
    rows = [r for r in SettingsDialog.ROWS if r.key == "opt/auto_update_redo_wait_s"]
    assert rows and rows[0].section == "When a page opens" and rows[0].span == (0, 3600)
    dlg = SettingsDialog(win)
    try:
        assert isinstance(dlg._row_widgets["opt/auto_update_redo_wait_s"], QtWidgets.QSpinBox)
        assert dlg._row_widgets["opt/auto_update_redo_wait_s"].value() == 60
    finally:
        dlg.close()
