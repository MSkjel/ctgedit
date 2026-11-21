"""What follows a library save."""

import os
import sys
import types

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
    n = len(s.page.objects)
    s.create_object("rectangle", rect=(0, 0, 12, 8))
    s.save()
    libraries_changed()
    assert len(s.page.objects) == n + 1


def _select_after(w, first: int):
    """Select the last root of the page shown after record ``first``: (its index, its
    record)."""
    d = w.doc
    tgt = [r for r in d.page.roots() if r > first][-1]
    w._sel = [tgt]
    w.view.set_selection([tgt])
    w._refresh()
    return tgt, d.parsed.page.objects[tgt]


def _where(doc, po) -> list:
    return [i for i, o in enumerate(doc.parsed.page.objects) if o is po]


def _first_box(doc) -> int:
    return min(i for i, k, _x in doc.symbol_placements() if k[1].lower() == "fx_box")


# -- the selection follows its objects through an automatic update -------------------------
def test_the_selection_follows_its_object_through_the_library_watchs_symbol_update(
    fx_install, win, monkeypatch
):
    """FxSymbols shown, its last genie selected; another program saves fx_box (placed before
    it) with one more record: the watch's update moves the genie two records on (fx_box is
    placed twice), and the selection, the view and the Properties panel follow it."""
    from ctgedit.ui.window import libwatch

    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    d = win.doc
    tgt, po = _select_after(win, _first_box(d))
    kind = d.page.objects[tgt].kind
    _box_gains_a_record(fx_install)
    libwatch.libraries_changed_on_disk([_symbols_lib(fx_install)], first=win)
    assert [c.label for c in d._undo] == ["update symbols from library"]
    now = _where(d, po)
    assert now and now != [tgt], "the update moved the selected record"
    assert win._sel == now and d.page.objects[now[0]].kind == kind
    assert win.view.selection_roots() == now


def test_the_selection_follows_in_a_tab_updated_while_it_was_not_shown(fx_install, win, monkeypatch):
    """The same update while FxSymbols waits in another tab: shown again, its selection is
    on the object the user selected, not on the record now at its old index."""
    from ctgedit.ui.window import libwatch

    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    d = win.doc
    tgt, po = _select_after(win, _first_box(d))
    win.open_document(fx_install.page("FxShapes"))
    _box_gains_a_record(fx_install)
    libwatch.libraries_changed_on_disk([_symbols_lib(fx_install)], first=win)
    assert [c.label for c in d._undo] == ["update symbols from library"]
    k = next(i for i, e in enumerate(win._docs) if e["doc"] is d)
    win.doc_tabs.setCurrentIndex(k)
    assert win.doc is d
    now = _where(d, po)
    assert now != [tgt] and win._sel == now and win.view.selection_roots() == now


def test_the_selection_follows_through_a_symbol_save_in_another_window(fx_install, win, win2, monkeypatch):
    """fx_box gains a record and is saved in window 2: the save's update of FxSymbols in
    window 1 (_propagate_symbol) keeps the selection there on the same genie."""
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    d = win.doc
    tgt, po = _select_after(win, _first_box(d))
    win2._open_element(_symbols_lib(fx_install), "fx_box")
    win2.doc.create_object("rectangle", rect=(0, 0, 12, 8))
    assert win2._save_element_to()
    assert d._undo
    now = _where(d, po)
    assert now != [tgt] and win._sel == now


def test_the_selection_follows_through_a_genie_update_of_the_page(fx_install, win, win2):
    """n4 gains a record and is saved in window 2: FxGenies in window 1 composes its n4 again
    (the save's update of the page), and the selected genie after it stays selected."""
    win.open_document(fx_install.page("FxGenies"))
    d = win.doc
    first = min(i for i, (_a, b) in d.genie_instances() if b.lower() == "n4")
    tgt, po = _select_after(win, first)
    win2._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n4")
    win2.doc.create_object("rectangle", rect=(0, 0, 12, 8))
    assert win2._save_element_to()
    assert d._undo
    now = _where(d, po)
    assert now != [tgt] and win._sel == now


def test_a_selected_object_replaced_where_it_was_stays_selected_and_a_gone_one_goes(
    fx_install, win, monkeypatch
):
    """The selection follows records, and keeps an object whose record was replaced where it
    was (a new record at its index: a genie composed again, a symbol re-derived); an object
    removed while the next one moved to its index is let go, not swapped for that one. A
    selection set after the change is taken as it is."""
    import copy

    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxShapes"))
    d = win.doc
    a = list(d.page.roots())[1]
    objs = d.parsed.page.objects
    win._sel = [a]
    objs[a] = copy.copy(objs[a])  # (replaced where it was)
    d._derive(fresh=True)
    win._refresh()
    assert win._sel == [a]
    nxt = objs[a + 1]
    d.delete_objects([a])  # (the next object moves to its index)
    assert d.parsed.page.objects[a] is nxt
    win._refresh()
    assert win._sel == [], "not the object that moved to its index"
    win._sel = [a]  # (set after the change: as it is)
    win._refresh()
    assert win._sel == [a]


def test_the_symbol_step_waits_while_the_user_drags_or_a_dialog_is_open(fx_install, win, monkeypatch):
    """A symbol another program changed reaches the page shown while the user drags on it
    (the view is busy) or a modal dialog is open: the page is neither drawn again nor changed
    until they have finished (the release of a drag moved the records under the selection);
    then once. A tab shown meanwhile takes it at once."""
    from ctgedit.ui.window import libwatch

    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    d = win.doc
    busy = {"view": True, "modal": None}
    monkeypatch.setattr(win.view, "busy", lambda: busy["view"])
    monkeypatch.setattr(QtWidgets.QApplication, "activeModalWidget", staticmethod(lambda: busy["modal"]))
    serial = d.derive_serial
    _box_gains_a_record(fx_install)
    libwatch.libraries_changed_on_disk([_symbols_lib(fx_install)], first=win)
    assert not d._undo and d.derive_serial == serial, "nothing during the drag"
    from ctgedit.ui.simulate import live_reload

    assert live_reload.editor_genie_update_pending(d), "Simulate waits for it"
    busy.update(view=False, modal=object())  # (the drag ended, a dialog opened)
    assert win._idle_tick() == [] and not d._undo
    busy["modal"] = None
    assert win._idle_tick() == [d]
    assert [c.label for c in d._undo] == ["update symbols from library"]
    assert d.derive_serial != serial and not d.stale_symbol_elements()
    assert win._idle_tick() == [] and len(d._undo) == 1
    # (the chain's end, View > Refresh: the same wait)
    _box_gains_a_record(fx_install)
    busy["view"] = True
    assert win._follow_symbols_in_tabs() == 0 and len(d._undo) == 1
    # (the user shows another tab and comes back: the show does it)
    busy["view"] = False
    win.open_document(fx_install.page("FxShapes"))
    k = next(i for i, e in enumerate(win._docs) if e["doc"] is d)
    busy["view"] = True  # (a tab clicked: the button is down)
    win.doc_tabs.setCurrentIndex(k)
    assert win.doc is d and len(d._undo) == 2 and not d.stale_symbol_elements()
    assert not win._waits_until_idle(d)


def test_a_page_with_steps_to_redo_waits_for_its_symbol_step_up_to_a_minute(fx_install, win, monkeypatch):
    """The library watch draws the page shown again at once, but its symbol step would clear
    the steps the user can redo: it waits, as a genie update's answer does, for at most a
    minute."""
    from ctgedit.ui.window import libwatch

    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    d = win.doc
    d.move_objects([list(d.page.roots())[3]], 2, 2)
    win.undo()
    assert d.can_redo()
    _box_gains_a_record(fx_install)
    libwatch.libraries_changed_on_disk([_symbols_lib(fx_install)], first=win)
    assert d.can_redo() and win._waits_until_idle(d)
    assert win._idle_tick() == [] and d.can_redo()
    win._idle_docs[0][1] -= 61  # (a minute later)
    assert win._idle_tick() == [d] and not d.can_redo()
    assert d._undo[-1].label == "update symbols from library"


def _value_marked(fx) -> str:
    """fxgenie.fx_value's text becomes MARKXYZ, saved (not through a window); its library."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = fx.file("FxLib", "fxgenie.ctm")
    d = ElementDocument.open_element(lib, "fx_value")
    i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    assert d.set_text(i, "MARKXYZ")
    d.save()
    libraries_changed()
    return lib


def _marked(doc) -> int:
    from ctgedit.format import writer

    return sum(b"MARKXYZ" in writer.object_payload(o) for o in doc.parsed.page.objects)


def test_the_chains_template_step_waits_while_the_user_drags(fx_install, win, monkeypatch):
    """The chain's template step waits while the user drags."""
    from ctgedit.ui.simulate import live_reload

    submitted = _no_jobs(win, monkeypatch)
    busy = {"view": False, "modal": None}
    monkeypatch.setattr(win.view, "busy", lambda: busy["view"])
    monkeypatch.setattr(QtWidgets.QApplication, "activeModalWidget", staticmethod(lambda: busy["modal"]))
    win.open_document(fx_install.page("FxTplLast"))
    page = win.doc
    assert _marked(page) == 0 and page.template_status()[0] == "current"
    lib = _value_marked(fx_install)

    class Saved:  # (what _update_closed_pages reads)
        path, element_name, display_name = lib, "fx_value", "fxgenie.fx_value"

    busy["view"] = True  # (the user drags on FxTplLast)
    win._update_closed_pages(Saved())  # (no event loop: the chain runs inline)
    assert _marked(page) == 0 and page.template_status()[0] == "stale", "nothing during the drag"
    assert win._waits_until_idle(page) and live_reload.editor_genie_update_pending(page)
    assert not any(a[0] is page for a in submitted), "its genies wait after the template"
    busy["view"] = False
    assert win._idle_tick() == [page]
    assert _marked(page) > 0 and page.template_status()[0] == "current"
    assert page.ui.auto_changes[0][0].startswith("template ")
    # (the same with a dialog open while the page waits in another tab: shown, it takes it)
    from ctgedit.format import template_place as TP

    root = TP.template_roots(page.parsed.page.objects)[0][2]
    win.undo()
    assert page.template_status()[0] == "stale"
    win.open_document(fx_install.page("FxShapes"))
    busy["modal"] = object()
    assert win._chain_templates_in_tabs(["%s.%s" % (root.library, root.element)]) == 0
    assert win._waits_until_idle(page) and page.template_status()[0] == "stale"
    assert win._idle_tick() == [] and page.template_status()[0] == "stale"
    k = next(i for i, e in enumerate(win._docs) if e["doc"] is page)
    win.doc_tabs.setCurrentIndex(k)
    assert win.doc is page and page.template_status()[0] == "current"
    assert _marked(page) > 0 and not win._waits_until_idle(page)


# -- Simulate during the editor's run -------------------------------------------------------
def test_simulate_does_not_reload_a_waiting_page_while_a_run_writes_libraries(tmp_path, monkeypatch):
    """A Simulate page waits for the editor's genie update of its document after a library
    change; the chain after the save starts before the update lands, and the update lands
    while the chain runs: the page does not reload then, but once at the run's end."""
    _app()
    from ctgedit import editing
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.simulate import live_reload
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from test_save_chain_follow2 import _lands, _sim_window
    from test_sim_engine_source import wait_for

    # (Settings 'Open tabs wait for the update of what uses a saved genie' on: the wait this
    # pins; off, the default, the page follows the editor: test_open_tabs_follow.py)
    app_settings().setValue("opt/tabs_wait_for_runs", True)
    lib = str(tmp_path / "pumps.ctm")
    with open(lib, "w") as fh:
        fh.write("a")
    pending = {"on": True}
    monkeypatch.setattr(
        live_reload, "editor_genie_update_pending", lambda doc: pending["on"] and doc is not None
    )
    win, sp, sim, doc = _sim_window(tmp_path, monkeypatch, lib)
    try:
        with open(lib, "w") as fh:  # (the save)
            fh.write("bbb")
        editing._LIBRARY_GENERATION[0] += 1
        win.tick()
        assert sp.lib_wait and sim.reloads == []
        UpdatePagesRun.users_busy = "fxnest.n0"  # (the chain starts)
        pending["on"] = False
        _lands(doc)  # (the update lands mid-run)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.6)
        assert sim.reloads == [], "no reload while the run writes libraries"
        UpdatePagesRun.users_busy = ""
        editing._LIBRARY_GENERATION[0] += 1  # (the run's saves)
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=3)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert len(sim.reloads) == 1 and not sp.auto_behind
    finally:
        win.close()
        win.deleteLater()


def test_simulate_holds_a_library_reload_due_when_a_run_starts(tmp_path, monkeypatch):
    """A library reload is due (nothing to wait for in the editor) when Update Pages' chain
    starts: it waits for the run's end; an editor edit's reload does not."""
    _app()
    from ctgedit import editing
    from ctgedit.ui.simulate import live_reload
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from test_save_chain_follow2 import _sim_window
    from test_sim_engine_source import wait_for

    lib = str(tmp_path / "pumps.ctm")
    with open(lib, "w") as fh:
        fh.write("a")
    monkeypatch.setattr(live_reload, "editor_genie_update_pending", lambda doc: False)
    win, sp, sim, _doc = _sim_window(tmp_path, monkeypatch, lib)
    try:
        with open(lib, "w") as fh:  # (the save)
            fh.write("bbb")
        editing._LIBRARY_GENERATION[0] += 1
        win.tick()
        assert sp.reload_due is not None and sp.reload_lib
        UpdatePagesRun.chain_busy = True
        wait_for(lambda: (win.tick(), False)[1], timeout=1.6)
        assert sim.reloads == []
        UpdatePagesRun.chain_busy = False
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=2)
        assert len(sim.reloads) == 1
    finally:
        win.close()
        win.deleteLater()


# -- one composition per save: the open tabs' updates wait for the chain ---------------------
class _Jobs:
    """A window's genie jobs with one automatic update of each listed page not landed yet
    (``waiting``); what is submitted is recorded, not composed."""

    def __init__(self, docs):
        self.waiting = list(docs)
        self.cancelled = []
        self.submitted = []

    def cancel_automatic(self, doc, genies=True, templates=True):
        self.cancelled.append((doc, genies, templates))
        if any(d is doc for d in self.waiting):
            self.waiting = [d for d in self.waiting if d is not doc]
            return 1
        return 0

    def submit(self, doc, changes, names, first=False, **_k):
        self.submitted.append(doc)

    def busy(self, doc=None):
        return any(d is doc for d in self.waiting)

    def cancel(self, doc):
        self.cancel_automatic(doc)

    def shutdown(self):
        self.waiting = []


@pytest.mark.parametrize("wait", [False, True])
def test_the_chain_start_holds_the_open_tabs_updates_only_when_set(
    fx_install, win, wait, monkeypatch, tmp_path
):
    """The chain start holds open tab updates only when set."""
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/tabs_wait_for_runs", wait)
    from test_element_nested_update import _edit_n0
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui.simulate import live_reload
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    _edit_n0(fx_install)
    win.open_document(fx_install.page("FxShapes"))
    other = win.doc
    win.open_document(fx_install.page("FxGenies"))
    p = win.doc
    assert p.genie_changes()
    p.ui.genie_sources_done[("fxnest", "n1")] = frozenset({"x"})
    jobs = _Jobs([p])
    win._genie_jobs = jobs
    seen = []

    def run(*a, **k):
        # (the chain's helper, while it runs)
        seen.append(
            (
                UpdatePagesRun.users_busy,
                list(jobs.cancelled),
                dict(p.ui.genie_sources_done),
                any(d is p for d in win.__dict__.get("_after_run_docs") or ()),
                any(d is other for d in win.__dict__.get("_after_run_docs") or ()),
                live_reload.editor_genie_update_pending(p),
                list(jobs.submitted),
            )
        )
        return {
            "format": GW.FORMAT,
            "updated": [],
            "elements_saved": ["fxnest.n1"],
            "backup": str(tmp_path / "bk"),
            "tabs_saved": [],
            "libraries": [],
        }

    monkeypatch.setattr(GW, "update_closed_pages", run)
    assert win._start_users_run(fx_install.file("FxLib", "fxnest.ctm"), "n0", "fxnest.n0")
    busy, cancelled, done, waits, other_waits, pending, submitted = seen[0]
    assert busy == "fxnest.n0"
    if not wait:
        assert cancelled == [], "its genie update is kept: it lands when it is done"
        assert done and not waits and not other_waits and submitted == []
        assert UpdatePagesRun.users_busy == ""
        app_settings().remove("opt/tabs_wait_for_runs")
        return
    assert (p, True, False) in cancelled, "its genie update dropped (not a template's)"
    assert not done, "composed again at the end, from the files as they are then"
    assert waits and not other_waits and pending and submitted == []
    assert UpdatePagesRun.users_busy == ""
    win._library_run_over()
    assert jobs.submitted == [p], "one composition, at the run's end"
    app_settings().remove("opt/tabs_wait_for_runs")


# -- the genie memo keys ----------------------------------------------------------------------
def test_the_composition_source_holds_io():
    """io/project's tag-format parsing measures texts during the composition: a change of it
    makes new memo keys, as one of format/ or doc/ does."""
    from ctgedit.infra import noop_memo

    root = os.path.dirname(os.path.dirname(os.path.abspath(noop_memo.__file__)))
    files = {os.path.relpath(p, root).replace(os.sep, "/") for p in noop_memo._source_files(root)}
    assert "io/project.py" in files and "io/updatepages.py" in files
    assert "format/genie_place.py" in files


def test_the_memo_context_holds_the_project_palette(fx_install):
    """The palette file a new colour array's defaults come from (citect.pal of the page's
    project) is part of the genie memo's context: changed, the page's key is another."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import colourpalette

    d = EditableDocument.open(fx_install.page("FxGenies"))
    pal = colourpalette.palette_file(os.path.dirname(fx_install.page("FxGenies")))
    assert pal and os.path.isfile(pal)
    before = d._genie_memo_context()
    assert any(isinstance(x, tuple) and x and str(x[0]).lower().endswith("citect.pal") for x in before)
    st = os.stat(pal)
    with open(pal, "ab") as fh:
        fh.write(b"\n")
    os.utime(pal, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert d._genie_memo_context() != before


def test_two_versions_of_one_stamp_are_told_apart(fx_install):
    """A library written twice within one tick of the file system's clock, of one size: its
    (mtime, size) are the same, but the open tabs' memo (genie_sources) and the genie memo
    keys tell the versions apart (the content tag of a file this process knows changed)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import fileio
    from test_element_nested_update import _edit_n0

    _edit_n0(fx_install)
    d = EditableDocument.open(fx_install.page("FxGenies"))
    changes = d.genie_changes()
    assert changes
    lib = fx_install.file("FxLib", "fxnest.ctm")
    src1 = d.genie_sources(set(changes))
    key1 = d.genie_noop_key(changes)
    st = os.stat(lib)
    with open(lib, "rb") as fh:
        data = bytearray(fh.read())
    data[-1] ^= 0x01  # (another version of the same size)
    with open(lib, "wb") as fh:
        fh.write(bytes(data))
    os.utime(lib, ns=(st.st_atime_ns, st.st_mtime_ns))
    fileio.note_written(lib)
    assert os.stat(lib).st_mtime_ns == st.st_mtime_ns and os.stat(lib).st_size == st.st_size
    src2 = d.genie_sources(set(changes))
    assert src2 != src1
    assert all(len(x) == 4 for v in src2.values() for x in v)
    assert d.genie_noop_key(changes) != key1
    # (the same bytes again: the same tag, the memo holds)
    assert d.genie_sources(set(changes)) == src2
    assert fileio.content_tag(lib) and fileio.content_tag(lib) == fileio.content_tag(lib)


def test_the_content_tag_is_the_files_digest_in_any_process(tmp_path, monkeypatch):
    """The tag is the SHA-1 of the file's bytes, whether this process knows of a change of
    the file or not (the keys are kept on disk and made by the helpers too), read once per
    version."""
    import hashlib
    from ctgedit.format import fileio

    p = str(tmp_path / "x.ctm")
    with open(p, "wb") as fh:
        fh.write(b"abc")
    assert fileio.content_tag(p) == hashlib.sha1(b"abc").hexdigest()
    fileio.note_changed(p)
    assert fileio.content_tag(p) == hashlib.sha1(b"abc").hexdigest()
    reads = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda *a, **k: (reads.append(a[0]), real_open(*a, **k))[1])
    assert fileio.content_tag(p) and reads == [], "read once per version"


def test_a_memo_key_is_the_same_in_the_next_session(fx_install, monkeypatch):
    """The genie memo and answer keys are kept on disk: the next session (or a helper),
    which knows of no change of the library this session saved, makes the same key for the
    page."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import fileio
    from test_element_nested_update import _edit_n0

    _edit_n0(fx_install)  # (saved in this process: a change it knows)
    lib = fx_install.file("FxLib", "fxnest.ctm")
    assert fileio.file_version(lib)[2]
    d = EditableDocument.open(fx_install.page("FxGenies"))
    changes = d.genie_changes()
    assert changes
    key = d.genie_noop_key(changes)
    monkeypatch.setattr(fileio, "_CHANGES", {})  # (another process: no change known)
    monkeypatch.setattr(fileio, "_DIGESTS", {})
    assert not fileio.file_version(lib)[2]
    d2 = EditableDocument.open(fx_install.page("FxGenies"))
    assert d2.genie_noop_key(d2.genie_changes()) == key


# -- the library watch's status line ---------------------------------------------------------
def test_the_library_watch_says_it_updated_the_symbols(fx_install, win, monkeypatch):
    from ctgedit.ui.window import libwatch

    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    _box_gains_a_record(fx_install)
    libwatch.libraries_changed_on_disk([_symbols_lib(fx_install)], first=win)
    msg = win.statusBar().currentMessage()
    assert msg == (
        "fxsym.ctl was changed by another program: the page was drawn again and its symbols were updated."
    ), msg
    a, b = types.SimpleNamespace(), types.SimpleNamespace()
    assert libwatch._watch_result([a], []) == "the page was drawn again"
    assert (
        libwatch._watch_result([a, b], [a])
        == "2 pages were drawn again and the symbols on the page were updated"
    )
    assert libwatch._watch_result([], [a, b]) == "the symbols on 2 pages were updated"


# -- a page moved to another window before its open checks ran ------------------------------
def test_a_page_moved_before_its_open_checks_ran_takes_them_along(fx_install, win, win2, monkeypatch):
    """FxSymbols places fx_box, changed since the page was saved; the page is moved to
    another window before its open checks ran (after its first paint): the new window runs
    them, so the symbol is updated (only the genie step ran)."""
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    _box_gains_a_record(fx_install)
    monkeypatch.setattr(win, "DEFER_POST_OPEN", True)
    monkeypatch.setattr(win, "_open_in_builder", lambda path: False)
    win.open_document(fx_install.page("FxSymbols"))
    d = win.doc
    assert any(p[0] is d for p in win._post_open_pending)
    assert d.stale_symbol_elements() and not d._undo
    entry = next(e for e in win._docs if e["doc"] is d)
    win._move_entry_to(entry, win2)
    assert not any(p[0] is d for p in getattr(win, "_post_open_pending", []))
    assert win2.doc is d
    assert [c.label for c in d._undo] == ["update symbols from library"]
    assert not d.stale_symbol_elements() and win2._only_auto_changes(d)
