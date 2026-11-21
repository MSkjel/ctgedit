"""What follows a library save."""

import json
import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

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
        ("opt/genie_save_pages", "never"),
        ("opt/symbol_save_pages", "never"),
        ("opt/auto_update_genies", True),
        ("opt/auto_update_symbols", True),
    ):
        st.setValue(k, v)
    yield
    from ctgedit.ui.window.library import LibraryCommands

    LibraryCommands._chain_progress = ""
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


def _place_box_in_n0(fx):
    """fxnest.n0 places the library symbol fxsym.fx_box directly, saved."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(fx.file("FxLib", "fxnest.ctm"), "n0")
    d.place_library_element("fxsym", "fx_box", "ctl", (80, 10))
    d.save()
    libraries_changed()


def _save_symbol(w, fx, element, dx=40):
    """``element`` of fxsym.ctl moved and saved from window ``w``'s tab."""
    w._open_element(fx.file("FxLib", "fxsym.ctl"), element)
    d = w.doc
    d.move_objects([list(d.page.roots())[0]], dx, dx)
    assert w._save_element_to()


# -- F1: the page shown in another window ---------------------------------------------------
def test_a_genie_save_redraws_the_page_shown_in_another_window(fx_install, win, win2, monkeypatch):
    # (the page's update is its window's genie job, here at once: no event loop)
    _no_jobs(win2, monkeypatch)
    win.open_document(fx_install.page("FxBig"))
    page = win.doc
    assert _shown_now(win) and not page.dirty
    win2._open_element(fx_install.file("FxLib", "fxgenie.ctm"), "fx_pump")
    d = win2.doc
    text = [i for i, o in enumerate(d.page.objects) if o.kind.name == "TEXT"][0]
    d.move_objects([text], 3, 0)
    assert win2._save_element_to()
    assert page._undo, "the page's genie update landed"
    assert win.doc is page and _shown_now(win), "the canvas shows the update"
    assert win.windowTitle().split(" - ")[0].endswith("•")


def test_a_symbol_save_redraws_the_page_shown_in_another_window(fx_install, win, win2, monkeypatch):
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    page = win.doc
    stale = page.stale_symbol_elements()
    if stale:
        page.update_symbol_placements(stale)
        win._refresh()
    steps = len(page._undo)
    _save_symbol(win2, fx_install, "fx_box")
    assert len(page._undo) == steps + 1
    assert _shown_now(win)
    assert win.windowTitle().split(" - ")[0].endswith("•")


# -- F2: an element tab that places the symbol ----------------------------------------------
def test_an_element_tab_that_places_the_symbol_takes_its_save(fx_install, win, win2, monkeypatch):
    _place_box_in_n0(fx_install)
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    win._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    host = win.doc
    assert not host.stale_symbol_elements() and not host._undo
    _save_symbol(win2, fx_install, "fx_box")
    assert not host.stale_symbol_elements(), "the tab took the new symbol"
    assert host._undo and host.dirty
    whats = [w for w, _c in host.ui.auto_changes or ()]
    assert any("symbols updated from their library (fxsym.fx_box)" in w for w in whats)
    # (only automatic changes: the chain after a genie save may save it from its library)
    assert win._only_auto_changes(host)
    assert _shown_now(win)


@pytest.mark.parametrize("symbols, genies", [(True, True), (True, False), (False, True)])
def test_an_element_opened_after_its_symbol_changed_takes_it(fx_install, win, monkeypatch, symbols, genies):
    """fxparts.fx_sets places fxsym.fx_flash; fx_flash is saved elsewhere: opening fx_sets
    re-derives it, as a page's open does (Update changed symbols), also with Update changed
    genies off; with Update changed symbols off it is left."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.ui.prefs.settings import app_settings

    _no_jobs(win, monkeypatch)
    s = ElementDocument.open_element(fx_install.file("FxLib", "fxsym.ctl"), "fx_flash")
    s.move_objects([list(s.page.roots())[0]], 40, 40)
    s.save()
    libraries_changed()
    app_settings().setValue("opt/auto_update_symbols", symbols)
    app_settings().setValue("opt/auto_update_genies", genies)
    win._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_sets")
    d = win.doc
    if not symbols:
        assert d.stale_symbol_elements() == {("fxsym", "fx_flash")} and not d._undo
        return
    assert not d.stale_symbol_elements()
    assert d._undo and win._only_auto_changes(d) and _shown_now(win)


def _flash_saved(fx):
    """fxsym.fx_flash moved and saved (not through a window): a new version of it."""
    from ctgedit.editing import ElementDocument, libraries_changed

    s = ElementDocument.open_element(fx.file("FxLib", "fxsym.ctl"), "fx_flash")
    s.move_objects([list(s.page.roots())[0]], 40, 40)
    s.save()
    libraries_changed()


def _show_again(w, doc):
    """Another tab of ``w`` shown, then ``doc``'s again."""
    k = next(i for i, e in enumerate(w._docs) if e["doc"] is doc)
    w.doc_tabs.setCurrentIndex(0 if k else 1)
    w.doc_tabs.setCurrentIndex(k)
    assert w.doc is doc


def test_an_undone_symbol_update_in_an_element_tab_stays_undone(fx_install, win, monkeypatch):
    """The user undoes the automatic update of fx_sets' placed fx_flash (the bar's Undo):
    a later library change and tab show leave it undone (it took that version once); a new
    save of fx_flash is taken again."""
    from ctgedit.editing import libraries_changed

    _no_jobs(win, monkeypatch)
    _flash_saved(fx_install)
    win._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_sets")
    d = win.doc
    assert d._undo and not d.stale_symbol_elements()
    win._undo_auto_changes()
    assert not d._undo and d.stale_symbol_elements() == {("fxsym", "fx_flash")}
    win.open_document(fx_install.page("FxGenies"))
    libraries_changed()  # (another element saved somewhere)
    _show_again(win, d)
    assert not d._undo, "the update the user undid is not made again"
    _flash_saved(fx_install)  # (a new version of the symbol)
    _show_again(win, d)
    assert d._undo and not d.stale_symbol_elements()


def test_a_tab_shown_while_a_run_saves_library_genies_waits_for_its_end(fx_install, win, monkeypatch):
    """Update Users in the editor's process (or a chain) saves library genies one after
    another: a tab shown meanwhile does not compose its genies against libraries half
    written; it does on its next show after the run (or at the run's end)."""
    # (Settings 'Open tabs wait for the update of what uses a saved genie' on: the
    # behaviour this pins; off, the default, the tab takes it at once)
    from ctgedit.ui.prefs.settings import app_settings as _st

    _st().setValue("opt/tabs_wait_for_runs", True)
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    win._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n2")
    host = win.doc
    win.open_document(fx_install.page("FxShapes"))
    _edit_n0(fx_install)  # (a save of the run: the generation moves)
    composed = []
    monkeypatch.setattr(win, "_update_stale_genies", lambda doc, first=False: composed.append(doc) or [])
    UpdatePagesRun.users_busy = "fxnest.n0"
    _show_again(win, host)
    assert composed == []
    UpdatePagesRun.users_busy = ""
    _show_again(win, host)
    assert host in composed


# -- F3: Simulate's own library watch -------------------------------------------------------
def _sim_window(tmp_path, monkeypatch, lib):
    from ctgedit.ui.simulate import window as W
    from test_sim_engine_source import _rect_page
    from test_sim_live_reload import _Doc, _ReloadSim

    proj = tmp_path / "User" / "Proj"
    proj.mkdir(parents=True)
    page_file = str(proj / "FxPage.ctg")
    with open(page_file, "wb"):
        pass

    def page(path, parsed=None, doc=None):
        p = _rect_page()
        p._sim_libraries = [lib]
        return p

    monkeypatch.setattr(W, "runtime_page", page)
    doc = _Doc(page_file)
    doc._undo = []
    doc.ui = types.SimpleNamespace(auto_changes=[])
    win = W.SimulateWindow()
    sim = _ReloadSim()
    sp = win.open_page(page_file, sim=sim, doc=doc, parsed=doc.parsed)
    win.tick()
    return win, sp, sim, doc


def _lands(doc):
    """The editor's genie update of ``doc`` lands (an automatic step)."""
    c = object()
    doc._undo.append(c)
    doc.ui.auto_changes.append(("genies updated from their library", c))
    doc.edit_serial += 1
    doc.derive_serial += 1


def test_simulate_waits_while_the_chain_runs_and_reloads_once_after_it(tmp_path, monkeypatch):
    """The chain's helper writes the page's library while it runs: Simulate's watch sees it,
    but neither reloads the page nor moves the editor's library generation. At the chain's
    end its writes are the editor's own (chain_saves_settled), the editor composes the
    document again: one reload, after that update has landed."""
    _app()
    from ctgedit import editing
    from ctgedit.format.fileio import note_written
    from ctgedit.ui.simulate import live_reload
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from test_sim_engine_source import wait_for

    lib = str(tmp_path / "pumps.ctm")
    with open(lib, "w") as fh:
        fh.write("a")
    pending = {"on": False}
    monkeypatch.setattr(
        live_reload, "editor_genie_update_pending", lambda doc: pending["on"] and doc is not None
    )
    win, sp, sim, doc = _sim_window(tmp_path, monkeypatch, lib)
    try:
        UpdatePagesRun.users_busy = "fxnest.n0"
        gen = editing._LIBRARY_GENERATION[0]
        with open(lib, "w") as fh:  # (the helper's write)
            fh.write("bbb")
        win._libraries_changed([lib])  # (the watch)
        assert editing._LIBRARY_GENERATION[0] == gen
        wait_for(lambda: (win.tick(), False)[1], timeout=1.5)
        assert sim.reloads == [] and sp.reload_due is None
        # the chain ends: its libraries are the editor's own, the editor's generation moves
        # and its update of the document is queued
        UpdatePagesRun.users_busy = ""
        note_written(lib)
        pending["on"] = True
        editing._LIBRARY_GENERATION[0] += 1
        wait_for(lambda: (win.tick(), False)[1], timeout=1.5)
        assert sim.reloads == [] and sp.lib_wait
        pending["on"] = False
        _lands(doc)
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=3)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert len(sim.reloads) == 1 and not sp.auto_behind
    finally:
        win.close()
        win.deleteLater()


def test_simulate_leaves_the_editors_own_library_writes_to_its_generation(tmp_path, monkeypatch):
    _app()
    from ctgedit import editing
    from ctgedit.format import fileio

    lib = str(tmp_path / "pumps.ctm")
    with open(lib, "w") as fh:
        fh.write("a")
    win, sp, sim, _doc = _sim_window(tmp_path, monkeypatch, lib)
    try:
        fileio.write_atomic(lib, b"bbbb")  # (an editor save)
        gen = editing._LIBRARY_GENERATION[0]
        win._libraries_changed([lib])
        assert editing._LIBRARY_GENERATION[0] == gen and sp.reload_due is None
    finally:
        win.close()
        win.deleteLater()


def test_simulate_lets_the_editors_library_watch_look_first(tmp_path, monkeypatch):
    """Another program wrote the library of a page that follows an editor document whose
    window watches that folder: Simulate tells the window's watch and waits for its look and
    the genie update it starts; one reload then."""
    _app()
    from ctgedit import editing
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.simulate import live_reload
    from test_sim_engine_source import wait_for

    lib = str(tmp_path / "pumps.ctm")
    with open(lib, "w") as fh:
        fh.write("a")
    pending = {"on": False}
    monkeypatch.setattr(
        live_reload, "editor_genie_update_pending", lambda doc: pending["on"] and doc is not None
    )
    win, sp, sim, doc = _sim_window(tmp_path, monkeypatch, lib)
    told = []

    class _Timer:
        active = False

        def isActive(self):  # noqa: N802 -- Qt naming
            return self.active

    editor = types.SimpleNamespace(
        _docs=[{"doc": doc}],
        _lib_watch=types.SimpleNamespace(folders=[str(tmp_path)]),
        _lib_pending=set(),
        _lib_timer=_Timer(),
    )
    editor._on_library_files_changed = lambda paths: (
        told.append(list(paths)),
        editor._lib_pending.update(paths),
    )
    monkeypatch.setattr(MainWindow, "_instances", [editor])
    try:
        with open(lib, "w") as fh:
            fh.write("bbb")
        win._libraries_changed([lib])
        assert told == [[lib]] and sp.lib_wait
        wait_for(lambda: (win.tick(), False)[1], timeout=1.5)
        assert sim.reloads == []
        # the editor's watch looks: the library changed, its genie update is queued
        editor._lib_pending.clear()
        pending["on"] = True
        editing._LIBRARY_GENERATION[0] += 1
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert sim.reloads == []
        pending["on"] = False
        _lands(doc)
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=3)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert len(sim.reloads) == 1 and not sp.auto_behind
    finally:
        win.close()
        win.deleteLater()


def test_simulate_waits_while_a_run_in_the_editor_brings_its_tabs_up_to_date(tmp_path, monkeypatch):
    """Simulate waits while an editor run brings its tabs up to date."""
    _app()
    from ctgedit import editing
    from ctgedit.ui.simulate import live_reload
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from test_sim_engine_source import wait_for

    lib = str(tmp_path / "pumps.ctm")
    with open(lib, "w") as fh:
        fh.write("a")
    pending = {"on": False}
    monkeypatch.setattr(
        live_reload, "editor_genie_update_pending", lambda doc: pending["on"] and doc is not None
    )
    win, sp, sim, doc = _sim_window(tmp_path, monkeypatch, lib)
    try:
        UpdatePagesRun.tabs_following = True
        with open(lib, "w") as fh:  # (a save of the run)
            fh.write("bbb")
        editing._LIBRARY_GENERATION[0] += 1
        wait_for(lambda: (win.tick(), False)[1], timeout=1.5)
        assert sim.reloads == [] and sp.reload_due is None and not sp.lib_wait
        # the run's end: the element tabs' updates are queued
        pending["on"] = True
        UpdatePagesRun.tabs_following = False
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert sim.reloads == [] and sp.lib_wait
        pending["on"] = False
        _lands(doc)
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=3)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert len(sim.reloads) == 1 and not sp.auto_behind
    finally:
        win.close()
        win.deleteLater()


def _run_steps(cls, monkeypatch, seen):
    """The steps of a run after its saves, each with whether Simulate's library changes
    wait (live_reload.library_run_going)."""
    from ctgedit.ui.simulate.live_reload import library_run_going
    from ctgedit.ui.window.library import OpenTabsUpdate

    monkeypatch.setattr(
        cls,
        "_update_open_documents_shown",
        lambda self, dirs, opts, title, **k: seen.append(("tabs", library_run_going())) or OpenTabsUpdate(),
    )
    monkeypatch.setattr(
        cls, "_queue_open_genie_updates", lambda self: seen.append(("queue", library_run_going()))
    )


def test_update_users_holds_simulate_until_its_open_tabs_have_followed(fx_install, win, monkeypatch):
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.simulate.live_reload import library_run_going

    lib = fx_install.file("FxLib", "fxnest.ctm")
    users = [
        types.SimpleNamespace(
            ref=types.SimpleNamespace(label="FxGenies", path=fx_install.page("FxGenies"), kind="page"),
            needs_update=True,
            links=[],
        )
    ]
    monkeypatch.setattr(GU, "update_plan", lambda usage: GU.UpdatePlan([], users))
    seen = []
    monkeypatch.setattr(
        GU,
        "run_update",
        lambda plan, **k: (
            seen.append(("run", library_run_going()))
            or GU.UpdateUsersReport(elements_saved=["fxnest.n1"], libraries_written=[lib])
        ),
    )
    _run_steps(type(win), monkeypatch, seen)
    monkeypatch.setattr(
        type(win),
        "_report_users_update",
        lambda self, text, **k: seen.append(("report", library_run_going())),
    )
    usage = types.SimpleNamespace(target=types.SimpleNamespace(label="fxnest.n0"))
    win._update_element_users(usage, confirm=False)
    assert seen == [("run", True), ("tabs", True), ("queue", True), ("report", False)]


def test_update_pages_holds_simulate_until_its_open_tabs_have_followed(tmp_path, monkeypatch):
    """Update Pages with its chain: Simulate's library changes wait through the pages
    (helpers: events go through) and the open pages' step, until the element tabs' updates
    are queued."""
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.simulate.live_reload import library_run_going
    from test_update_pages_chain import _run

    seen = []
    _run_steps(MainWindow, monkeypatch, seen)
    calls, _shown, _w = _run(
        tmp_path, monkeypatch, True, on_save=lambda w: seen.append(("save", library_run_going()))
    )
    assert [c[0] for c in calls] == ["plan", "save", "pages"]
    assert seen == [("save", True), ("tabs", True), ("queue", True)]
    assert not library_run_going()


# -- F4 / O5: the runs in the editor's process ----------------------------------------------
def _edit_n0(fx):
    from test_element_nested_update import _edit_n0 as edit

    edit(fx)


def test_update_users_composes_the_element_tabs_that_nest_what_it_saved(fx_install, win, monkeypatch):
    """Update Users of n0 in the editor's process saves n1 .. n4 again; n2 is open with
    unsaved changes (left by the run): its copy of n1 is composed in its tab after the run,
    its edit kept, as after the chain in the background."""
    from ctgedit.io import genieusage as GU
    from test_element_nested_update import _marked

    monkeypatch.setattr(type(win), "_report_users_update", lambda self, text, **k: None)
    win._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n2")
    host = win.doc
    text = next(
        k
        for k, o in enumerate(host.page.objects)
        if o.parent is None and host.parsed.page.objects[k].type_code == 0x05
    )
    host.move_objects([text], 5, 0)
    assert host.dirty
    _edit_n0(fx_install)
    usage = GU.element_users(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    rep = win._update_element_users_run(usage, confirm=False)
    assert rep is not None and "fxnest.n1" in rep.elements_saved
    assert any("n2" in s for s in rep.skipped)
    assert _marked(host.parsed.page.objects) == 1, "its copy took the saved genies"
    assert host.dirty


def test_update_pages_chain_composes_the_open_element_tabs(tmp_path, monkeypatch):
    """Update Pages saved the library genies in its chain first: the open tabs that nest
    them are composed after the run (the open pages' step does pages only)."""
    _app()
    from ctgedit.ui.main_window import MainWindow
    from test_update_pages_chain import _run

    queued = []
    monkeypatch.setattr(MainWindow, "_queue_open_genie_updates", lambda self: queued.append(self))
    calls, _shown, w = _run(tmp_path, monkeypatch, True)
    assert [c[0] for c in calls] == ["plan", "save", "pages"]
    assert queued == [w]


def test_update_users_gives_open_tabs_the_template_page_properties_as_set(fx_install, win, monkeypatch):
    """[Animator] TemplateUpdate decides the template's page properties for the open tabs of
    Update Users as for its closed pages (update_template(page_properties=None))."""
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.window.library import OpenTabsUpdate

    users = [
        types.SimpleNamespace(
            ref=types.SimpleNamespace(label="FxTplLinked", path=fx_install.page("FxTplLinked"), kind="page")
        )
    ]
    monkeypatch.setattr(GU, "update_plan", lambda usage: GU.UpdatePlan([], users))
    monkeypatch.setattr(GU, "run_update", lambda plan, **k: GU.UpdateUsersReport())
    monkeypatch.setattr(type(win), "_report_users_update", lambda self, text, **k: None)
    seen = []
    monkeypatch.setattr(
        type(win),
        "_update_open_documents_shown",
        lambda self, dirs, opts, title, **k: seen.append(dict(opts)) or OpenTabsUpdate(),
    )
    usage = types.SimpleNamespace(target=types.SimpleNamespace(label="fx_style.normal"))
    win._update_element_users_run(usage, confirm=False)
    assert seen
    asked = []
    page = types.SimpleNamespace(
        template_status=lambda: ("stale", "fx_style.normal"),
        update_template=lambda page_properties=False: asked.append(page_properties) or True,
    )
    type(win)._update_open_document(page, dict(seen[0], genies=False, symbols=False), OpenTabsUpdate())
    assert asked == [None]


# -- O6: a helper that dies in the genies phase ---------------------------------------------
class _Died(BaseException):
    """The helper process ends (a kill, a native crash): nothing after it runs."""


def test_the_chains_helper_writes_what_it_saved_after_each_element(fx_install, tmp_path, monkeypatch):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument

    _edit_n0(fx_install)
    real = ElementDocument.save
    count = {"n": 0}

    def save(self, *a, **k):
        count["n"] += 1
        if count["n"] == 2:
            raise _Died()
        return real(self, *a, **k)

    monkeypatch.setattr(ElementDocument, "save", save)
    prog = tmp_path / "run.progress"
    with pytest.raises(_Died):
        GW.update_closed_pages(
            fx_install.file("FxLib", "fxnest.ctm"), "n0", progress_file=str(prog), backup=str(tmp_path / "bk")
        )
    side = json.loads((tmp_path / ("run.progress" + GW.GENIES_SUFFIX)).read_text())
    assert side["elements_saved"] == ["fxnest.n1"]
    assert side["libraries"] and side["backup"] == str(tmp_path / "bk")
    assert side["element_kinds"]["fxnest.n1"] == "genie"


def test_the_editor_follows_the_partial_answer_of_a_failed_helper(fx_install, win, monkeypatch):
    """The helper answered with an error only (or gave no answer): the editor reads what the
    genies phase wrote after each element (the genies saved, the backup) and says so."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui import notify

    def died(lib_path, element, skip, builder_open, backup, progress_file, *a, **k):
        GW._write_json(
            progress_file + GW.GENIES_SUFFIX,
            {
                "format": GW.FORMAT,
                "elements_saved": ["fxnest.n1"],
                "elements_in_editor": [],
                "libraries": [lib_path],
                "backup": backup,
                "tabs_saved": [],
                "element_kinds": {"fxnest.n1": "genie"},
            },
        )
        raise MemoryError("out of memory")

    monkeypatch.setattr(GW, "update_closed_pages", died)
    c = notify.centre(win)
    c.clear()
    assert win._start_users_run(fx_install.file("FxLib", "fxnest.ctm"), "n0", "fxnest.n0")
    m = c.entries[-1]
    assert m.text.startswith("Saved 1 library genie using fxnest.n0, then the update failed."), m.text
    assert "Backup: " in m.detail


# -- O8: counted by kind --------------------------------------------------------------------
def test_the_symbol_prompt_counts_by_kind_what_the_chain_updates(fx_install, win, monkeypatch):
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    _no_jobs(win, monkeypatch)
    lib = fx_install.file("FxLib", "fxsym.ctl")
    d = ElementDocument.open_element(lib, "fx_on")
    d.move_object(0, 3, 2)
    d.save()
    libraries_changed()
    win.open_document(fx_install.page("FxSymbols"))  # (open: it takes it in its tab)
    app_settings().setValue("opt/symbol_save_pages", "ask")
    asked = []
    monkeypatch.setattr(MainWindow, "_exec_prompt", staticmethod(lambda box: asked.append(box.text())))
    win._users_saved_prompt(d)
    assert asked == [
        "1 super genie, 1 template, 2 library symbols and 5 closed pages use "
        "fxsym.fx_on and still show its old version."
    ], asked


def test_the_chains_result_counts_by_kind(win):
    res = {
        "elements_saved": [
            "fxmore.fx_dspsym",
            "fxsuper.!fx_detail@SUPER",
            "fxsym.fx_set",
            "fx_alt.sets@-XGA",
        ],
        "elements_skipped": ["fxnest.n2: it has unsaved changes in the editor"],
        "element_kinds": {
            "fxmore.fx_dspsym": "symbol",
            "fxsuper.!fx_detail@SUPER": "super genie",
            "fxsym.fx_set": "symbol",
            "fx_alt.sets@-XGA": "template",
            "fxnest.n2": "genie",
        },
        "updated": ["a", "b"],
        "libraries": [],
        "tabs_saved": [],
    }
    text = win._users_run_finished(res, "fxsym.fx_on")
    assert text.startswith(
        "Updated 1 super genie, 1 template, 2 library symbols and 2 closed "
        "pages that use fxsym.fx_on. 1 library genie left"
    ), text
    err = win._users_run_finished(dict(res, error="the update gave no answer", updated=[]), "fxsym.fx_on")
    assert err == (
        "Saved 1 super genie, 1 template and 2 library symbols using fxsym.fx_on, then the update failed."
    )
    # (and the closed pages it saved before it failed)
    err = win._users_run_finished(dict(res, error="the update gave no answer"), "fxsym.fx_on")
    assert err == (
        "Saved 1 super genie, 1 template, 2 library symbols and 2 closed pages "
        "using fxsym.fx_on, then the update failed."
    )


def test_the_chains_details_name_each_elements_kind(win):
    res = {
        "elements_saved": ["fxmore.fx_dspsym", "fxnest.n1", "fx_alt.sets@-XGA"],
        "elements_skipped": ["fxsuper.!fx_detail@SUPER: it has unsaved changes in the editor"],
        "element_kinds": {
            "fxmore.fx_dspsym": "symbol",
            "fxnest.n1": "genie",
            "fx_alt.sets@-XGA": "template",
            "fxsuper.!fx_detail@SUPER": "super genie",
        },
    }
    text = win._users_run_details(res)
    assert "Library genies" not in text
    assert (
        "Library elements saved again:\n  fxmore.fx_dspsym (symbol)\n  fxnest.n1\n"
        "  fx_alt.sets@-XGA (template)"
    ) in text, text
    assert (
        "Library elements left:\n  fxsuper.!fx_detail@SUPER (super genie): it has "
        "unsaved changes in the editor"
    ) in text, text


def test_a_failed_chain_reads_again_the_tabs_it_found_current(win, monkeypatch):
    """The helper failed after it found an element open in a clean tab current (nothing
    saved): the tab reads it again as after a run that went well (its automatic changes are
    in the store)."""
    read = []
    monkeypatch.setattr(
        type(win), "_reload_saved_tabs", lambda self, tabs, res=None: read.append([list(t) for t in tabs])
    )
    res = {
        "error": "the update gave no answer",
        "tabs_saved": [["C:/x/fxnest.ctm", "n2"]],
        "elements_saved": [],
        "libraries": [],
    }
    assert win._users_run_finished(res, "fxnest.n0") == "Could not update what uses fxnest.n0."
    assert read == [[["C:/x/fxnest.ctm", "n2"]]]


def test_the_chains_answer_names_each_elements_kind(fx_install, tmp_path, monkeypatch):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = fx_install.file("FxLib", "fxsym.ctl")
    d = ElementDocument.open_element(lib, "fx_on")
    d.move_object(0, 3, 2)
    d.save()
    libraries_changed()
    monkeypatch.setattr(GW, "_closed_pages_phase", lambda out, *a, **k: out)
    res = GW.update_closed_pages(lib, "fx_on", backup=str(tmp_path / "bk"))
    kinds = res["element_kinds"]
    assert kinds["fxsuper.!fx_detail@SUPER"] == "super genie"
    assert kinds["fxsym.fx_set"] == "symbol" and kinds["fx_alt.sets@-XGA"] == "template"
