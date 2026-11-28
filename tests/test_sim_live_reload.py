"""Live reload while simulating."""

import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402
from test_sim_engine_source import _rect_page, _sim, wait_for  # noqa: E402
from qtkit import app as _app  # noqa: E402

windows_only = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")


def _pump():
    from PySide6 import QtWidgets

    QtWidgets.QApplication.processEvents()


@pytest.fixture
def servers(tmp_path, monkeypatch):
    folder = str(tmp_path / "servers")
    monkeypatch.setenv(C.SERVERS_ENV, folder)
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)
    return folder


@pytest.fixture
def hub():
    h = C.Hub(linger=0)
    yield h
    h.shutdown()


@pytest.fixture
def page_file(tmp_path):
    proj = tmp_path / "User" / "Proj"
    proj.mkdir(parents=True)
    path = proj / "FxPage.ctg"
    path.write_bytes(b"")
    return str(path)


def _write(path, text):
    with open(path, "w", encoding="cp1252", newline="") as f:
        f.write(text)


def _replace_like_an_editor(src, dst, tries=50):
    """An editor's save by a rename: another program reading ``dst`` that moment (a virus
    scanner, the indexer) makes Windows refuse it (WinError 5 / 32), and an editor tries
    again, as this does."""
    for k in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if k == tries - 1:
                raise
            time.sleep(0.02)


# -- the folder watch --------------------------------------------------------------------
def test_the_folder_watch_sees_saved_new_and_removed_files(tmp_path):
    _app()
    from ctgedit.ui.simulate.live_reload import FolderWatch

    a = str(tmp_path / "a.ci")
    _write(a, "FUNCTION A() END")
    _write(str(tmp_path / "notes.txt"), "x")
    w = FolderWatch([str(tmp_path)])
    seen = []
    w.changed.connect(lambda paths: seen.extend(os.path.basename(p) for p in paths))
    try:
        time.sleep(0.05)
        _write(a, "FUNCTION A() RETURN 2 END")
        assert wait_for(lambda: "a.ci" in seen, pump=_pump), seen
        _write(str(tmp_path / "lib.ctm"), "x")
        assert wait_for(lambda: "lib.ctm" in seen, pump=_pump), seen
        # an editor's save through a temporary file and a rename
        tmp = str(tmp_path / "a.ci.tmp")
        # (another size: two saves inside one clock tick of the file times look alike)
        _write(tmp, "FUNCTION A() RETURN 33 END")
        n = seen.count("a.ci")
        _replace_like_an_editor(tmp, a)
        # (the notice of a rename right after a burst can be missed: LiveReload's sweep
        # compares the folders again, as this does after a second)
        if not wait_for(lambda: seen.count("a.ci") > n, timeout=1.0, pump=_pump):
            w.rescan()
        assert seen.count("a.ci") > n, seen
        os.remove(str(tmp_path / "lib.ctm"))
        assert wait_for(lambda: seen.count("lib.ctm") >= 2, pump=_pump), seen
        assert "notes.txt" not in seen
    finally:
        w.close()


# -- Cicode: a stand-in session ------------------------------------------------------------
class _Session:
    def __init__(self, folder, attached=False):
        self.alive = True
        self.attached = attached
        self.project = folder
        self.unit = [("Proj", folder)]
        self.sent = []
        self.answer = {"accepted": True, "compiled": True, "errors": []}

    def reload_file(self, path, callback=None, text=None, timeout=0):
        self.sent.append((path, time.monotonic()))
        if callback is not None:
            callback(dict(self.answer, file=path), None)


class _Sim:
    def __init__(self):
        self.log = []

    def _add_log(self, e):
        self.log.append(e)

    def _now(self):
        return 1.0


def test_a_saved_ci_file_is_reloaded_after_the_last_change(tmp_path):
    _app()
    from ctgedit.ui.simulate.live_reload import LiveReload, CICODE_DEBOUNCE_MS

    f = str(tmp_path / "Pumps.ci")
    _write(f, "INT FUNCTION Level() RETURN 1; END")
    s, sim = _Session(str(tmp_path)), _Sim()
    lr = LiveReload(lambda sess: sim if sess is s else None)
    try:
        lr.sync([s])
        time.sleep(0.05)
        t0 = time.monotonic()
        for k in range(3):  # three saves in a row: one reload
            _write(f, "INT FUNCTION Level() RETURN %d; END" % (k + 2))
            wait_for(lambda: False, timeout=0.1, pump=_pump)
        assert wait_for(lambda: s.sent, timeout=15, pump=_pump)
        wait_for(lambda: False, timeout=0.8, pump=_pump)
        assert [os.path.basename(p) for p, _t in s.sent] == ["Pumps.ci"]
        assert s.sent[0][1] - t0 >= CICODE_DEBOUNCE_MS / 1000.0
        # an answer with compile errors: an error line per error, with file and line
        s.answer = {
            "accepted": True,
            "compiled": False,
            "reason": "1 compile error(s)",
            "errors": [{"code": "E2041", "line": 12, "column": 5, "message": "Unknown variable 'Lvl'"}],
        }
        _write(f, "INT FUNCTION Level() RETURN Lvl; END")
        assert wait_for(lambda: len(s.sent) == 2, timeout=15, pump=_pump)
        errs = [e for e in sim.log if e.kind == "error"]
        assert errs[0].file == f and errs[0].line == 12 and "E2041" in errs[0].text, errs
        assert errs[-1].text == "Pumps.ci was not reloaded: 1 compile error(s)."
        # the setting off: no reload
        from ctgedit.ui.simulate import options

        options.put("reload_cicode", False)
        _write(f, "INT FUNCTION Level() RETURN 9; END")
        wait_for(lambda: False, timeout=1.0, pump=_pump)
        assert len(s.sent) == 2
        # ... Reload Cicode sends the changed files anyway
        assert lr.reload_now(s) == 1 and len(s.sent) == 3
    finally:
        from ctgedit.ui.simulate import options

        options.put("reload_cicode", True)
        lr.close()


def test_a_simulation_another_program_started_is_not_reloaded_unless_asked(tmp_path):
    _app()
    from ctgedit.ui.simulate.live_reload import LiveReload

    f = str(tmp_path / "Pumps.ci")
    _write(f, "x")
    s, sim = _Session(str(tmp_path), attached=True), _Sim()
    lr = LiveReload(lambda sess: sim)
    try:
        lr.sync([s])
        assert lr.reload_now(s) == 0
        assert "no .ci file" in sim.log[-1].text
        time.sleep(0.05)
        _write(f, "y")
        assert wait_for(lambda: any("Reload Cicode" in e.text for e in sim.log), pump=_pump)
        wait_for(lambda: False, timeout=15, pump=_pump)
        assert s.sent == []
        assert lr.reload_now(s) == 1 and s.sent[0][0] == f
        # a session gone: its watch goes
        s.alive = False
        lr.sync([s])
        assert lr.watch_of(s) is None
    finally:
        lr.close()


@windows_only
def test_reload_goes_to_the_simulator_and_its_errors_come_back(servers, hub, page_file):
    """Over the (fake) simulator's pipe: hello's unit folders are watched, Reload Cicode sends
    ctsim/reload, the errors of the answer are Outputs lines."""
    _app()
    from ctgedit.ui.simulate.live_reload import LiveReload

    proj = os.path.dirname(page_file)
    f = os.path.join(proj, "Tanks.ci")
    _write(f, "x")
    srv = F.FakeServer(proj, servers, pages=True).start()
    srv.reload_errors = {"tanks.ci": [{"code": "E1004", "line": 3, "column": 1, "message": "Syntax error"}]}
    sim = _sim(hub)
    lr = LiveReload(lambda sess: sim)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        s = sim.session
        assert s.unit == [("Proj", proj)]
        lr.sync([s])
        time.sleep(0.05)
        _write(f, "y")
        assert lr.reload_now(s) == 1
        assert wait_for(lambda: srv.reloads == [f])
        got = []
        assert wait_for(
            lambda: got.extend(sim.drain()) or any(e.line == 3 and e.file == f for e in got), timeout=15
        ), [(e.kind, e.text) for e in got]
    finally:
        lr.close()
        sim.stop()
        srv.stop()


# -- the page: a reload after an edit --------------------------------------------------------
class _Doc:
    """An editor document as the Simulate window reads it."""

    def __init__(self, path):
        self.path = path
        self.parsed = object()
        self.edit_serial = 0
        self.derive_serial = 0
        self.dirty = False
        self.exported = 0

    def export_page_model(self):
        self.exported += 1
        return {"format": "ctsim-page/0", "page": "FxPage", "n": self.exported}


class _ReloadSim:
    """A FakeSimulation that records its reloads."""

    def __init__(self):
        from ctgedit.ui.simulate.sources import FakeSimulation

        self.fake = FakeSimulation()
        self.reloads = []

    def __getattr__(self, name):
        return getattr(self.fake, name)

    def reload(self, model, page=None):
        self.reloads.append((model, time.monotonic()))


def test_a_page_edited_in_the_editor_reloads_a_second_after_the_last_edit(page_file, monkeypatch):
    _app()
    from ctgedit.ui.simulate import window as W
    from ctgedit.ui.simulate.live_reload import PAGE_DEBOUNCE_S

    monkeypatch.setattr(W, "runtime_page", lambda path, parsed=None, doc=None: _rect_page())
    doc = _Doc(page_file)
    win = W.SimulateWindow()
    try:
        sim = _ReloadSim()
        sp = win.open_page(page_file, sim=sim, doc=doc, parsed=doc.parsed)
        win.tick()
        assert sim.reloads == []
        t0 = time.monotonic()
        doc.edit_serial += 1
        end = t0 + 0.5
        while time.monotonic() < end:  # more edits: each puts the reload off
            win.tick()
            time.sleep(0.05)
        doc.edit_serial += 1
        t1 = time.monotonic()
        win.tick()
        win._show_edit_note()
        # (the automatic reload on: no line over the page, the reload says so on the page)
        assert not win.edit_row.isVisibleTo(win)
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=15)
        assert len(sim.reloads) == 1 and sim.reloads[0][1] - t1 >= PAGE_DEBOUNCE_S - 0.05
        assert sim.reloads[0][0]["page"] == "FxPage"
        assert not sp.doc_changed() and not win.edit_row.isVisibleTo(win)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.2)
        assert len(sim.reloads) == 1
        # the setting off: the note and Reload Page
        from ctgedit.ui.simulate import options

        options.put("reload_page", False)
        doc.edit_serial += 1
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert len(sim.reloads) == 1
        win._show_edit_note()
        assert win.edit_note.text() == "The page changed in the editor."
        win.reload_button.click()
        assert len(sim.reloads) == 2
    finally:
        from ctgedit.ui.simulate import options

        options.put("reload_page", True)
        win.close()
        win.deleteLater()


def test_a_saved_library_reloads_the_pages_that_use_it(tmp_path, page_file, monkeypatch):
    _app()
    from ctgedit import editing
    from ctgedit.ui.simulate import window as W

    lib = str(tmp_path / "pumps.ctm")
    other = str(tmp_path / "valves.ctm")
    _write(lib, "a")
    _write(other, "a")

    def page(path, parsed=None):
        p = _rect_page()
        p._sim_libraries = [lib]
        return p

    monkeypatch.setattr(W, "runtime_page", page)
    monkeypatch.setattr(W, "page_model", lambda path, doc=None: {"page": "FxPage"})
    win = W.SimulateWindow()
    try:
        sim = _ReloadSim()
        sp = win.open_page(page_file, sim=sim)
        assert sp.lib_stamps is not None and list(sp.lib_stamps) == [lib]
        # another library saved by ctgedit: nothing
        _write(other, "bb")
        editing.LIBRARY_GENERATION[0] += 1
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert sim.reloads == []
        # its library saved (by ctgedit's element editor: the library generation)
        _write(lib, "bbb")
        editing.LIBRARY_GENERATION[0] += 1
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=15)
        # ... or on disk (the folder watch): the page reloads again
        win._libraries_changed([lib])
        assert wait_for(lambda: (win.tick(), len(sim.reloads) == 2)[1], timeout=15)
        win._libraries_changed([other])
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert len(sim.reloads) == 2
    finally:
        win.close()
        win.deleteLater()


def test_a_page_saved_by_another_program_reloads_from_its_file(page_file, monkeypatch):
    """The Graphics Builder saved the simulated page (no editor document follows it): the tab
    reloads from the file; with the automatic reload off it says so, and Reload Page reads the
    file."""
    _app()
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate import window as W

    monkeypatch.setattr(W, "runtime_page", lambda path, parsed=None, doc=None: _rect_page())
    monkeypatch.setattr(W, "page_model", lambda path, doc=None: {"page": "FxPage"})
    win = W.SimulateWindow()
    try:
        sim = _ReloadSim()
        sp = win.open_page(page_file, sim=sim)
        assert sp.file_stamp is not None
        win._pages_changed([page_file])  # (a watch event, the file as it was)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert sim.reloads == []
        _write(page_file, "saved by the Graphics Builder")
        win._pages_changed([page_file])
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=15)
        assert len(sim.reloads) == 1 and not sp.file_changed
        assert sp.reload_why == "its file was saved by another program"
        # the automatic reload off: the note, and Reload Page reads the file
        options.put("reload_page", False)
        _write(page_file, "saved again, longer than before")
        win._pages_changed([page_file])
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert len(sim.reloads) == 1
        win._show_edit_note()
        assert win.edit_row.isVisibleTo(win)
        assert win.edit_note.text() == "The page was saved by another program."
        win.reload_button.click()
        assert len(sim.reloads) == 2 and not sp.file_changed
    finally:
        options.put("reload_page", True)
        win.close()
        win.deleteLater()


def test_a_tab_follows_the_document_the_editor_reloaded(page_file, monkeypatch):
    """The editor reloaded the page from disk (Changed on Disk, Revert to Saved): a new
    document in its tab. The Simulate tab follows the new document and reloads; a file event
    for a page the editor has open is the editor's to handle."""
    _app()
    from ctgedit.ui.simulate import live_reload as L
    from ctgedit.ui.simulate import window as W

    monkeypatch.setattr(W, "runtime_page", lambda path, parsed=None, doc=None: _rect_page())
    old, new = _Doc(page_file), _Doc(page_file)
    open_docs = [old]
    monkeypatch.setattr(L, "editor_documents", lambda path: list(open_docs))
    win = W.SimulateWindow()
    try:
        sim = _ReloadSim()
        sp = win.open_page(page_file, sim=sim, doc=old, parsed=old.parsed)
        _write(page_file, "saved by the Graphics Builder")
        win._pages_changed([page_file])  # the editor has it open: not reloaded here
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert sim.reloads == [] and sp.doc is old and not sp.file_changed
        open_docs[:] = [new]  # the editor reloaded it
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=15)
        assert sp.doc is new and len(sim.reloads) == 1
        assert sp.reload_why == "the editor reloaded it from disk"
        wait_for(lambda: (win.tick(), False)[1], timeout=1.2)
        assert len(sim.reloads) == 1
    finally:
        win.close()
        win.deleteLater()


# -- the reload in the simulator: a popup's window, an SA page's workspace ---------------------
@windows_only
def test_a_popup_reloads_in_its_own_window(servers, hub, page_file):
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    srv.open_window(3, "FxPage", page_file, parent=-1)
    srv.set_associations(3, {"Tag": "Pump1"})
    sim = _sim(hub, attach_window=3, page_name="FxPage")
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        sim.reload({"format": "ctsim-page/0", "page": "FxPage"}, _rect_page())
        assert wait_for(lambda: srv.opened)
        args = srv.opened[-1]
        assert args["window"] == 3 and args["name"] == "FxPage"
        assert args["model"]["page"] == "FxPage" and args["associations"] == {"Tag": "Pump1"}
        assert wait_for(lambda: sim.page_active() and sim.window == 3)
    finally:
        sim.stop()
        srv.stop()


@windows_only
def test_an_sa_page_reloads_through_its_workspace(servers, hub, page_file):
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True, navigate=True).start()
    srv.windows[0] = "Master"
    srv.windows[1] = "FxPage"
    srv.parents[1] = 0
    srv.paths["mixer"] = page_file
    pane = _sim(hub, attach_window=1, page_name="FxPage")
    pane.sa_pane = True
    tab = _sim(hub, page_name="FxPage")
    try:
        pane.start(page_file, _rect_page())
        assert wait_for(lambda: pane.page_active())
        pane.reload({"format": "ctsim-page/0", "page": "FxPage", "edited": 1}, _rect_page())
        assert wait_for(lambda: srv.navigated)
        nav = srv.navigated[-1]
        assert nav["page"] == "FxPage" and nav["model"]["edited"] == 1
        assert not srv.opened  # not opened over the workspace
        assert wait_for(lambda: pane.page_active() and pane.window == 1)
        # the startup page's tab (window 0): it re-shows the page in its workspace too, and
        # keeps showing window 0
        tab.workspace_page = "Master"
        tab.start(page_file, _rect_page())
        assert wait_for(lambda: tab.session is not None)
        n = len(srv.navigated)
        tab.reload({"format": "ctsim-page/0", "page": "FxPage", "edited": 2}, _rect_page())
        assert wait_for(lambda: len(srv.navigated) > n)
        assert srv.navigated[-1]["model"]["edited"] == 2 and srv.windows[0] == "Master"
    finally:
        tab.stop()
        pane.stop()
        srv.stop()


def test_the_pane_takes_the_document_of_the_sa_page(tmp_path, monkeypatch):
    """The SA page went to its workspace pane: the pane's view takes the tab's document, so an
    edit in the editor reloads the pane."""
    _app()
    from ctgedit.ui.simulate import window as W
    from ctgedit.ui.simulate.sources import FakeSimulation

    proj = tmp_path / "SA"
    proj.mkdir()
    content = str(proj / "Content.ctg")
    master = str(proj / "Master.ctg")
    for p in (content, master):
        open(p, "wb").close()
    monkeypatch.setattr(W, "runtime_page", lambda path, parsed=None, doc=None: _rect_page())
    monkeypatch.setattr(W, "page_model_cached", lambda path: None)
    monkeypatch.setattr(W, "page_model_file_job", lambda path: None)
    win = W.SimulateWindow()
    try:
        doc = _Doc(content)
        owner = W.SimulatedPage(content, page=_rect_page(), sim=FakeSimulation(), parent=win.tabs, doc=doc)
        owner.sim.workspace_page = "Master"
        # the tab follows window 0 to the startup page: its document is kept for the pane
        owner.sim.take_navigation = lambda: {"window": 0, "page": "Master", "path": master}
        owner.sim.navigated = lambda *a: None
        assert owner.follow_navigation() and owner.doc is None
        sp = win.open_window(owner, {"window": 1, "page": "Content", "path": content})
        assert sp is not None and sp.doc is doc and sp.sim.sa_pane
        assert not sp.doc_changed()
        doc.edit_serial += 1
        assert sp.doc_changed()
    finally:
        win.close()
        win.deleteLater()


def test_the_editors_own_update_does_not_reload_a_running_simulation(page_file, monkeypatch):
    """The editor's background genie update came after Simulate Page (the genies' libraries
    changed since the page was saved): the simulation goes on with the page the user saw and
    the note offers Reload Page. An edit of the user's reloads, also one an
    automatic update follows."""
    _app()
    import types
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate import window as W

    monkeypatch.setattr(W, "runtime_page", lambda path, parsed=None, doc=None: _rect_page())
    doc = _Doc(page_file)
    doc._undo = []
    doc.ui = types.SimpleNamespace(auto_changes=[])

    def step(auto):
        c = object()
        doc._undo.append(c)
        if auto:
            doc.ui.auto_changes.append(("genies updated from their library", c))
        doc.edit_serial += 1
        doc.derive_serial += 1

    win = W.SimulateWindow()
    try:
        sim = _ReloadSim()
        sp = win.open_page(page_file, sim=sim, doc=doc, parsed=doc.parsed)
        win.tick()
        step(auto=True)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert sim.reloads == [] and sp.auto_behind and not sp.doc_changed()
        win._show_edit_note()
        assert win.edit_row.isVisibleTo(win)
        assert win.edit_note.text() == "The editor updated this page from its libraries."
        # the user's edit: reloaded, the note goes
        step(auto=False)
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=15)
        assert len(sim.reloads) == 1 and not sp.auto_behind
        win._show_edit_note()
        assert not win.edit_row.isVisibleTo(win)
        # an edit, then an automatic update before the reload came: reloaded
        step(auto=False)
        win.tick()
        step(auto=True)
        assert wait_for(lambda: (win.tick(), len(sim.reloads) == 2)[1], timeout=15)
        # Undo of the automatic step is the user's: reloaded
        step(auto=True)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert len(sim.reloads) == 2 and sp.auto_behind
        doc._undo.pop()
        doc.edit_serial += 1
        assert wait_for(lambda: (win.tick(), len(sim.reloads) == 3)[1], timeout=15)
        assert not sp.auto_behind
        # Settings > Simulate: also reload for the editor's own updates
        options.put("reload_auto_updates", True)
        step(auto=True)
        assert wait_for(lambda: (win.tick(), len(sim.reloads) == 4)[1], timeout=15)
        assert not sp.auto_behind
    finally:
        options.put("reload_auto_updates", False)
        win.close()
        win.deleteLater()


# -- the real simulator (optional): a .ci function's new return value shows on the page -----
def _fx_engine():
    """The built ctsim engine (an EngineLocation), or skip with the paths tried."""
    from test_sim_ctsim_live import _real_engine

    loc, tried = _real_engine()
    if loc is None:
        pytest.skip(
            "ctsim.exe is not built (looked in %s): dotnet build "
            r"external\cicode-sim\engine\src\ctsim\ctsim.csproj -c Release -f net48" % ", ".join(tried)
        )
    return loc


@windows_only
def test_live_a_changed_ci_function_shows_on_the_running_page(fx_install, servers, hub):
    """A changed .ci function shows on the running page."""
    loc = _fx_engine()
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.window import SimulateWindow

    probe = os.path.join(fx_install.project("FxSim"), "FxReloadProbe.ci")
    _write(probe, "INT FUNCTION FxReloadProbe()\r\n\tRETURN 111;\r\nEND\r\n")
    path = fx_install.page("FxSim1", "FxSim")
    doc = EditableDocument.open(path)
    found = doc.find_objects_by_name("FxCount")
    assert len(found) == 1, found
    i = found[0]
    an = doc.page.objects[i].an
    assert an and doc.set_property(i, "TXT_STR", "FxReloadProbe()")
    # (a registered page runs from its pg rows: the save writes the new expression there)
    doc.save()
    win = SimulateWindow()
    try:
        sim = EngineSimulation(hub=hub, location=loc)
        sp = win.open_page(path, sim=sim, doc=doc, parsed=doc.parsed)
        win.show()

        def text():
            win.tick()
            return str(getattr(sp.scene.sim_states().get(an), "text", "") or "")

        assert wait_for(
            lambda: sim.page_active() or (sim.status().level == "warn" and not sim.status().busy),
            timeout=90,
            pump=_pump,
        )
        if not sim.page_active():
            if "no server yet" in sim.status().text:
                pytest.skip(sim.status().text)
            pytest.fail(sim.status().text)
        assert wait_for(lambda: "111" in text(), timeout=30, pump=_pump), (
            text(),
            [e.text for e in sp.log][-10:],
        )
        unit = [os.path.normcase(d) for _n, d in sim.session.unit]
        assert os.path.normcase(os.path.dirname(probe)) in unit, unit
        time.sleep(0.1)
        _write(probe, "INT FUNCTION FxReloadProbe()\r\n\tRETURN 222;\r\nEND\r\n")
        assert wait_for(lambda: "222" in text(), timeout=90, pump=_pump), (
            text(),
            [e.text for e in sp.log][-10:],
        )
        assert sim.page_active()
    finally:
        win.close()
        win.deleteLater()


# a closed live reload dropped by Python, then the deferred deletions: it aborted the process
_TEARDOWN = r"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtWidgets
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from ctgedit.ui.simulate.live_reload import LiveReload
class S:
    alive, attached = True, False
    def __init__(self, d):
        self.unit, self.project = [("u", d)], d
d = tempfile.mkdtemp(dir=sys.argv[1])
open(os.path.join(d, "Pumps.ci"), "w").write("x")
lr = LiveReload(lambda sess: None)
lr.sync([S(d)])
lr.close()
del lr
QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
app.processEvents()
print("teardown ok")
"""


def test_a_closed_live_reload_dropped_before_its_deletions_run_does_not_abort(tmp_path):
    """LiveReload's handlers hold it: a watch dropped while they were still connected took the
    last reference to it with its deferred deletion, and its C++ object deleted the watch a
    second time while that was being deleted (Fatal Python error: Aborted). In a process of
    its own."""
    import subprocess

    script = tmp_path / "teardown.py"
    script.write_text(_TEARDOWN, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
        + [p for p in (env.get("PYTHONPATH") or "").split(os.pathsep) if p]
    )
    r = subprocess.run(
        [sys.executable, str(script), str(tmp_path)], env=env, capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 0 and "teardown ok" in r.stdout, (r.returncode, r.stderr[-800:])
