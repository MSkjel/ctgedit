"""The library watch and the Simulate window follow what is open."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_save_chain_follow import _other_program_saves  # noqa: E402
from test_save_chain_follow2 import _lands, _no_jobs, _quiet_chain, _sim_window, win  # noqa: E402,F401
from qtkit import app as _app  # noqa: E402


@pytest.fixture(autouse=True)
def _watch_fresh():
    """The library watch's holds start empty (they are emptied between modules only)."""
    from ctgedit.ui.window import libwatch

    libwatch._HELD.clear()
    yield
    libwatch._HELD.clear()


def _spin_until(cond, timeout_ms=4000) -> bool:
    """A real event loop (QEventLoop, QTimer steps) until ``cond()`` or the time is up."""
    loop = QtCore.QEventLoop()
    timer = QtCore.QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: cond() and loop.quit())
    QtCore.QTimer.singleShot(timeout_ms, loop.quit)
    timer.start()
    loop.exec()
    timer.stop()
    return bool(cond())


def _setting(key, value):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue(key, value)


# -- the library watch during the background update -----------------------------------------
def test_the_open_tabs_take_each_library_the_helper_writes_as_it_is_written(
    fx_install, win, tmp_path, monkeypatch
):
    """Open tabs take each library the helper writes, as it's written."""
    from ctgedit.format.fileio import file_version
    from ctgedit.ui.simulate.live_reload import editor_wrote
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from ctgedit.ui.window import libwatch

    _setting("opt/library_watch_delay_ms", 100)
    win._apply_library_watch()
    lib = fx_install.file("FxLib", "fxnest.ctm")
    win._open_element(lib, "n2")
    host = win.doc
    win.open_document(fx_install.page("FxShapes"))  # (n2's tab is not shown)
    submitted = _no_jobs(win, monkeypatch)
    UpdatePagesRun.users_busy = "fxnest.n0"  # (the run in its helper)
    _other_program_saves(lib, "n1", tmp_path)  # (the helper's save of n1)
    QtCore.QTimer.singleShot(0, lambda: win._on_library_files_changed([lib]))
    assert _spin_until(lambda: any(a[0] is host for a in submitted)), (
        "the n2 tab composes the n1 the helper saved, while the run goes on"
    )
    assert UpdatePagesRun.users_busy == "fxnest.n0"
    text = win.statusBar().currentMessage()
    assert "changed during the update of what uses fxnest.n0" in text, text
    assert "another program" not in text
    assert editor_wrote(lib), "the editor's own from now on (Simulate leaves it to it)"
    # the run ends: the file is as the tab took it
    UpdatePagesRun.users_busy = ""
    version = file_version(lib)
    n = len(submitted)
    assert libwatch.chain_saves_settled([lib], first=win) == []
    assert file_version(lib) == version, "not counted as a change a second time"
    win._on_library_files_changed([lib])
    assert win._check_library_changes() == [] and len(submitted) == n


def test_a_library_the_running_update_writes_follows_the_save(fx_install, win, tmp_path, monkeypatch):
    """The open tab's update for a file the helper wrote during the run is the save's follow
    (GenieJob.why 'follow', as at the run's end): a tab that took the save gets a quiet job
    whose step becomes part of the save's. A file another program writes outside a run is not."""
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    _setting("opt/library_watch_delay_ms", 100)
    win._apply_library_watch()
    lib = fx_install.file("FxLib", "fxnest.ctm")
    win._open_element(lib, "n2")
    host = win.doc
    win.open_document(fx_install.page("FxShapes"))
    calls = []
    monkeypatch.setattr(win._genie_job_runner(), "submit", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(type(win), "_took_the_save", lambda self, d: d is host)
    UpdatePagesRun.users_busy = "fxnest.n0"
    _other_program_saves(lib, "n1", tmp_path)
    QtCore.QTimer.singleShot(0, lambda: win._on_library_files_changed([lib]))
    assert _spin_until(lambda: any(a[0] is host for a, _k in calls))
    a, k = next((a, k) for a, k in calls if a[0] is host)
    assert k.get("why") == "follow" and k.get("quiet") is True, k
    UpdatePagesRun.users_busy = ""
    from ctgedit.ui.window import libwatch

    libwatch.chain_saves_settled([lib], first=win)
    calls.clear()
    _other_program_saves(lib, "n1", tmp_path)  # (no run now)
    QtCore.QTimer.singleShot(0, lambda: win._on_library_files_changed([lib]))
    assert _spin_until(lambda: any(a[0] is host for a, _k in calls))
    a, k = next((a, k) for a, k in calls if a[0] is host)
    assert k.get("why", "") == "" and not k.get("quiet"), k


def test_with_the_wait_setting_the_helpers_writes_wait_for_the_runs_end(
    fx_install, win, tmp_path, monkeypatch
):
    """Settings 'Open tabs wait for the update of what uses a saved genie' on: the watch holds
    the helper's write until the run's end, which notes it as the
    editor's own."""
    from ctgedit.format.fileio import written_stamp
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from ctgedit.ui.window import libwatch

    _setting("opt/tabs_wait_for_runs", True)
    lib = fx_install.file("FxLib", "fxnest.ctm")
    win._open_element(lib, "n2")
    host = win.doc
    win.open_document(fx_install.page("FxShapes"))
    submitted = _no_jobs(win, monkeypatch)
    UpdatePagesRun.users_busy = "fxnest.n0"
    _other_program_saves(lib, "n1", tmp_path)
    win._on_library_files_changed([lib])
    assert win._check_library_changes() == []
    assert not any(a[0] is host for a in submitted) and libwatch._key(lib) in libwatch._HELD
    UpdatePagesRun.users_busy = ""
    assert libwatch.chain_saves_settled([lib], first=win) == []
    st = os.stat(lib)
    assert written_stamp(lib) == (st.st_mtime_ns, st.st_size)


def test_update_users_in_this_process_holds_the_watch_whatever_the_setting(
    fx_install, win, tmp_path, monkeypatch
):
    """Update Users runs in the editor's process and brings the open tabs up to date after
    it (tabs_following): a library changed meanwhile waits for its end, also with the
    setting off."""
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from ctgedit.ui.window import libwatch

    lib = fx_install.file("FxLib", "fxnest.ctm")
    win._open_element(lib, "n2")
    win.open_document(fx_install.page("FxShapes"))
    submitted = _no_jobs(win, monkeypatch)
    UpdatePagesRun.users_busy = "fxnest.n0"
    UpdatePagesRun.tabs_following = True
    _other_program_saves(lib, "n1", tmp_path)
    win._on_library_files_changed([lib])
    assert win._check_library_changes() == [] and submitted == []
    assert libwatch._key(lib) in libwatch._HELD


def test_a_library_saved_meanwhile_by_another_program_is_taken_at_once(
    fx_install, win, tmp_path, monkeypatch
):
    """Another program saves a library the page shown uses while the background update
    runs: the page is drawn again from it at once (a derive that is no edit of the page)."""
    from ctgedit.ui.simulate.window import _model_key
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    _no_jobs(win, monkeypatch)
    lib = fx_install.file("FxLib", "fxsym.ctl")
    win.open_document(fx_install.page("FxSymbols"))
    doc = win.doc
    serial, model = doc.derive_serial, _model_key(doc)
    UpdatePagesRun.users_busy = "fxnest.n0"
    _other_program_saves(lib, "fx_on", tmp_path)
    win._on_library_files_changed([lib])
    assert doc in win._check_library_changes()
    assert doc.derive_serial > serial and doc.library_derives == 1
    assert _model_key(doc) != model, "its page model is made again"
    # (the watch cannot tell the helper's write from another program's, so it says when, not who)
    text = win.statusBar().currentMessage()
    assert "changed during the update of what uses fxnest.n0" in text, text
    assert "saved during" not in text


def test_the_runs_end_leaves_a_library_the_user_saved_after_the_chains_last_write(
    fx_install, win, tmp_path, monkeypatch
):
    """The run's end leaves a library the user saved after the chain's last write."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format.fileio import file_version, written_stamp
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from ctgedit.ui.window import libwatch

    lib = fx_install.file("FxLib", "fxnest.ctm")
    win._open_element(lib, "n2")
    win.open_document(fx_install.page("FxShapes"))
    _no_jobs(win, monkeypatch)
    UpdatePagesRun.users_busy = "fxnest.n0"
    _other_program_saves(lib, "n1", tmp_path)  # (the helper's save of n1)
    win._on_library_files_changed([lib])
    win._check_library_changes()  # (the tabs take it)
    sub = ElementDocument.open_element(lib, "n0")  # (the user's second save)
    sub.move_objects([list(sub.page.roots())[0]], 3, 0)
    sub.save()
    st = os.stat(lib)
    assert written_stamp(lib) == (st.st_mtime_ns, st.st_size)
    version = file_version(lib)
    UpdatePagesRun.users_busy = ""  # (the run's end)
    assert libwatch.chain_saves_settled([lib], first=win) == []
    assert file_version(lib) == version, "the user's save is not counted a second time"
    win._on_library_files_changed([lib])
    assert win._check_library_changes() == []


# -- a derive that only reads the libraries again -------------------------------------------
def test_a_derive_for_the_libraries_is_counted_apart_from_the_edits(fx_install):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    serial = doc.derive_serial
    doc.rerender_for_libraries()
    assert doc.derive_serial == serial + 1 and doc.library_derives == 1
    # (a re-render inside a batch is the batch's: an edit's)
    with doc.batch("two"):
        doc.rerender_for_libraries()
    assert doc.library_derives == 1
    doc._rerender_page()
    assert doc.library_derives == 1


# -- the Simulate window during the background update ---------------------------------------
def _second_page(win, tmp_path, monkeypatch, lib):
    """A tab of the Simulate window that follows a FILE (no editor document) reading ``lib``."""
    from ctgedit.ui.simulate import window as W
    from test_sim_live_reload import _ReloadSim

    path = str(tmp_path / "User" / "Proj" / "FxFile.ctg")
    with open(path, "wb"):
        pass
    monkeypatch.setattr(
        W,
        "page_model",
        lambda p, doc=None: doc.export_page_model() if doc is not None else {"format": "ctsim-page/0"},
    )
    sim = _ReloadSim()
    sp = win.open_page(path, sim=sim)
    assert sp.doc is None and sp.lib_stamps
    return sp, sim


def test_simulate_follows_the_editor_during_the_background_update(tmp_path, monkeypatch):
    """The user saves the genie and the background update starts at once (Settings: always):
    the Simulate tab that follows the editor's page reloads once the editor's update of the
    page has landed, while the run goes on; a tab that follows a file waits for the run's
    end. The run's end reloads neither again for the same write."""
    _app()
    from ctgedit import editing
    from ctgedit.ui.simulate import live_reload
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from test_sim_engine_source import wait_for

    lib = str(tmp_path / "pumps.ctm")
    with open(lib, "w") as fh:
        fh.write("a")
    pending = {"on": True}
    monkeypatch.setattr(
        live_reload, "editor_genie_update_pending", lambda doc: pending["on"] and doc is not None
    )
    win, sp, sim, doc = _sim_window(tmp_path, monkeypatch, lib)
    fsp, fsim = _second_page(win, tmp_path, monkeypatch, lib)
    try:
        UpdatePagesRun.users_busy = "fxnest.n0"  # (the run, started by the save)
        with open(lib, "w") as fh:  # (the save)
            fh.write("bbb")
        editing._LIBRARY_GENERATION[0] += 1
        win.tick()
        assert sp.lib_wait and sim.reloads == []
        pending["on"] = False
        _lands(doc)  # (the editor's tab took it)
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=3), "reloaded while the run goes on"
        assert UpdatePagesRun.users_busy and fsim.reloads == [], "the file's tab waits"
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert len(sim.reloads) == 1 and fsim.reloads == []
        UpdatePagesRun.users_busy = ""  # (the run's end)
        editing._LIBRARY_GENERATION[0] += 1
        assert wait_for(lambda: (win.tick(), fsim.reloads)[1], timeout=3)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert len(sim.reloads) == 1 and len(fsim.reloads) == 1 and not sp.auto_behind
    finally:
        win.close()
        win.deleteLater()


def test_simulate_waits_for_the_background_update_with_the_wait_setting(tmp_path, monkeypatch):
    _app()
    from ctgedit import editing
    from ctgedit.ui.simulate import live_reload
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from test_sim_engine_source import wait_for

    _setting("opt/tabs_wait_for_runs", True)
    lib = str(tmp_path / "pumps.ctm")
    with open(lib, "w") as fh:
        fh.write("a")
    monkeypatch.setattr(live_reload, "editor_genie_update_pending", lambda doc: False)
    win, sp, sim, doc = _sim_window(tmp_path, monkeypatch, lib)
    try:
        UpdatePagesRun.users_busy = "fxnest.n0"
        with open(lib, "w") as fh:
            fh.write("bbb")
        editing._LIBRARY_GENERATION[0] += 1
        _lands(doc)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.6)
        assert sim.reloads == []
        UpdatePagesRun.users_busy = ""
        editing._LIBRARY_GENERATION[0] += 1
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=3)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert len(sim.reloads) == 1
    finally:
        win.close()
        win.deleteLater()


def test_one_library_save_reloads_a_running_simulation_once(fx_install, win, tmp_path, monkeypatch):
    """A page open in the editor runs in the Simulate window; a library it uses is saved
    while its tab is not shown (Later): Simulate reloads it once for the library. Showing the
    tab again draws it from the libraries (its stale mark): no second reload, and no 'it
    changed in the editor'. An edit then still reloads it."""
    from ctgedit import editing
    from ctgedit.format import fileio
    from ctgedit.ui.simulate import live_reload
    from ctgedit.ui.simulate import window as W
    from test_sim_engine_source import _rect_page, wait_for
    from test_sim_live_reload import _ReloadSim

    lib = str(tmp_path / "pumps.ctm")
    with open(lib, "w") as fh:
        fh.write("a")

    def page(path, parsed=None, doc=None):
        p = _rect_page()
        p._sim_libraries = [lib]
        return p

    monkeypatch.setattr(W, "runtime_page", page)
    monkeypatch.setattr(W, "page_model_job", lambda path, doc: {"format": "ctsim-page/0"})
    monkeypatch.setattr(live_reload, "editor_library_check_pending", lambda doc: False)
    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxShapes"))
    doc = win.doc
    k = next(i for i, e in enumerate(win._docs) if e["doc"] is doc)
    sw = W.SimulateWindow()
    sim = _ReloadSim()
    try:
        sp = sw.open_page(doc.path, sim=sim, doc=doc, parsed=doc.parsed)
        sw.tick()
        win.open_document(fx_install.page("FxSymbols"))  # (the page's tab hidden)
        fileio.write_atomic(lib, b"bbb")  # (the save in another tab)
        editing.libraries_changed()
        win._after_library_write(lib)
        assert win._docs[k].get("stale")
        assert wait_for(lambda: (sw.tick(), sim.reloads)[1], timeout=3)
        wait_for(lambda: (sw.tick(), False)[1], timeout=0.5)
        assert len(sim.reloads) == 1
        serial = doc.derive_serial
        win.doc_tabs.setCurrentIndex(k)  # (back to the page's tab)
        assert win.doc is doc and doc.derive_serial > serial, "drawn again from the files"
        wait_for(lambda: (sw.tick(), False)[1], timeout=1.6)
        assert len(sim.reloads) == 1, "no second reload for one save"
        assert not sp.doc_changed() and sp.reload_due is None
        doc.move_objects([list(doc.page.roots())[0]], 4, 0)  # (an edit)
        assert wait_for(lambda: (sw.tick(), len(sim.reloads) > 1)[1], timeout=3)
    finally:
        sw.close()
        sw.deleteLater()
