"""What follows a library save."""

import os
import shutil
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from qtkit import app as _app

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _key(p):
    return os.path.normcase(os.path.abspath(p))


def _later(path, seconds=3):
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 10**9))


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


def _no_jobs(w, monkeypatch):
    submitted = []
    monkeypatch.setattr(w._genie_job_runner(), "submit", lambda *a, **k: submitted.append(a))
    return submitted


def _other_program_saves(lib, element, tmp_path):
    """Another program's save of ``element``: changed in a copy, put in place by a plain
    file copy (not this process's write path)."""
    from ctgedit.editing import ElementDocument

    tmp = os.path.join(str(tmp_path), "other_" + os.path.basename(lib))
    shutil.copyfile(lib, tmp)
    e = ElementDocument.open_element(tmp, element)
    e.move_objects([list(e.page.roots())[0]], 6, 0)
    e.save()
    shutil.copyfile(tmp, lib)
    _later(lib)


# -- 1. two writes within one clock tick ----------------------------------------------------
def test_a_second_same_size_write_in_one_tick_is_a_new_version(fx_install):
    """fx_on saved, the library read, fx_off saved with the same size and (as within one
    15.6 ms tick) the same mtime: the caches read the file again, and the open page sees
    fx_off out of date."""
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.format import element as E
    from ctgedit.format import symbols as S
    from ctgedit.io import genieusage as GU

    lib = fx_install.file("FxLib", "fxsym.ctl")
    P = EditableDocument.open(fx_install.page("FxSymbols"))
    stale = P.stale_symbol_elements()
    if stale:
        P.update_symbol_placements(stale)
    assert not P.stale_symbol_elements()
    a = ElementDocument.open_element(lib, "fx_on")
    b = ElementDocument.open_element(lib, "fx_off")
    a.move_object(0, 1, 0)
    b.move_object(0, 1, 0)
    a.save()
    first = os.stat(lib)
    cached = S.open_library(lib)
    tables = GU.library_tables(lib)
    b.save()
    second = os.stat(lib)
    assert second.st_size == first.st_size
    os.utime(lib, ns=(second.st_atime_ns, first.st_mtime_ns))  # (one tick: the same mtime)
    disk = E.LibraryFile.open(lib)
    fresh = bytes(disk.block_data(disk.find("fx_off")))
    now = S.open_library(lib)
    assert now is not cached
    assert bytes(now._find_block("fx_off").data) == fresh
    assert GU.library_tables(lib) is not tables
    assert ("fxsym", "fx_off") in {(x[0].lower(), x[1].lower()) for x in P.stale_symbol_elements()}


def test_the_file_version_counts_the_writes_and_the_noticed_changes(tmp_path):
    from ctgedit.format import fileio

    p = str(tmp_path / "a.ctm")
    fileio.write_atomic(p, b"x" * 10)
    v1 = fileio.file_version(p)
    st = os.stat(p)
    fileio.write_atomic(p, b"y" * 10)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    v2 = fileio.file_version(p)
    assert v1[:2] == v2[:2] and v1 != v2
    fileio.note_changed(p)
    assert fileio.file_version(p) != v2


def test_a_genie_worker_adopts_the_editors_library_writes(tmp_path):
    """The editor's request names the libraries it wrote (their change serials): the warm
    worker counts a library written since its last request as changed, also when its
    (mtime, size) did not change."""
    import json
    from ctgedit.workers import genie_worker as GW
    from ctgedit.format import fileio

    lib = str(tmp_path / "fxnest.ctm")
    fileio.write_atomic(lib, b"x" * 10)
    req = GW.write_request(str(tmp_path / "req"), str(tmp_path / "P.ctg"), b"", {})
    with open(req, encoding="utf-8") as fh:
        sent = json.load(fh)["file_changes"]
    assert _key(lib) in sent
    fileio._CHANGES.clear()  # (as the worker process: none of its own)
    before = fileio.file_version(lib)
    fileio.adopt_changes(sent)
    once = fileio.file_version(lib)
    assert once != before
    fileio.adopt_changes(sent)  # (the same request state: nothing new)
    assert fileio.file_version(lib) == once
    fileio.adopt_changes({_key(lib): sent[_key(lib)] + 1})
    assert fileio.file_version(lib) != once


# -- 2. a symbol save, the tabs of every window ---------------------------------------------
def test_a_symbol_save_queues_the_genie_updates_of_every_window(fx_install, win, win2, monkeypatch):
    """The copies of a symbol nested in placed genies and in element tabs, in every window,
    are composed again after its save, as after a genie save."""
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    win2.open_document(fx_install.page("FxParts"))
    asked = []
    monkeypatch.setattr(
        win, "_update_stale_genies", lambda doc, first=False: asked.append((win, doc, first)) or []
    )
    monkeypatch.setattr(
        win2, "_update_stale_genies", lambda doc, first=False: asked.append((win2, doc, first)) or []
    )

    class _Saved:
        kind = "symbol"
        library_name, element_name = "fxsym", "fx_on"
        path = fx_install.file("FxLib", "fxsym.ctl")
        previous_element = None
        display_name = "fxsym.fx_on"

    win._propagate_element(_Saved())
    assert (win2, win2.doc, True) in asked and (win, win.doc, True) in asked
    # (the setting off: nothing queued)
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/auto_update_genies", False)
    try:
        asked.clear()
        win._propagate_element(_Saved())
        assert asked == []
    finally:
        app_settings().setValue("opt/auto_update_genies", True)


def test_a_library_write_marks_the_tabs_of_every_window(fx_install, win, win2, monkeypatch):
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    win.open_document(fx_install.page("FxShapes"))
    win.open_document(fx_install.page("FxSymbols"))
    win2.open_document(fx_install.page("FxTextKinds"))
    win2.open_document(fx_install.page("FxParts"))
    for w in (win, win2):
        for e in w._docs:
            e.pop("stale", None)
    touched = []
    from ctgedit.io import catalog

    monkeypatch.setattr(
        catalog, "catalog_for", lambda *a, **k: types.SimpleNamespace(touch=lambda d: touched.append(_key(d)))
    )
    lib = fx_install.file("FxLib", "fxsym.ctl")
    other = os.path.join(fx_install.project("FxMain"), "fxmain.ctm")
    win._after_library_write(lib, other)
    for w in (win, win2):
        for e in w._docs:
            assert bool(e.get("stale")) == (e["doc"] is not w.doc), e["doc"].path
    assert touched == [_key(fx_install.project("FxLib")), _key(fx_install.project("FxMain"))]


def test_update_users_marks_every_library_it_wrote(fx_install, win, monkeypatch):
    """Update Users that saved genies of two libraries: the catalog of both folders and the
    tabs follow."""
    from ctgedit.io import genieusage as GU

    libs = [fx_install.file("FxLib", "fxnest.ctm"), os.path.join(fx_install.project("FxMain"), "fxmain.ctm")]
    users = [
        types.SimpleNamespace(ref=types.SimpleNamespace(label="fxnest.n1", path=libs[0])),
        types.SimpleNamespace(ref=types.SimpleNamespace(label="fxmain.g", path=libs[1])),
    ]
    monkeypatch.setattr(GU, "update_plan", lambda usage: GU.UpdatePlan(users, []))
    monkeypatch.setattr(
        GU,
        "run_update",
        lambda plan, **k: GU.UpdateUsersReport(
            elements_saved=["fxnest.n1", "fxmain.g"], libraries_written=list(libs)
        ),
    )
    monkeypatch.setattr(type(win), "_report_users_update", lambda self, text, **k: None)
    wrote = []
    monkeypatch.setattr(type(win), "_after_library_write", lambda self, *paths: wrote.append(paths))
    usage = types.SimpleNamespace(target=types.SimpleNamespace(label="fxnest.n0"))
    win._update_element_users_run(usage, confirm=False)
    assert wrote == [tuple(libs)]


# -- 3. the chain's helper writes are the editor's own --------------------------------------
def test_the_chains_helper_writes_are_not_another_programs(fx_install, win, tmp_path, monkeypatch):
    """While the chain after a genie save runs in its helper, a library it writes is held;
    at its end the libraries it reports are the editor's own: nothing is drawn again and the
    status bar says nothing about another program."""
    from ctgedit.format.fileio import written_stamp
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    # (Settings 'Open tabs wait for the update of what uses a saved genie' on: the hold this
    # pins; off, the default, the tabs take each write at once: test_open_tabs_follow.py)
    app_settings().setValue("opt/tabs_wait_for_runs", True)
    _no_jobs(win, monkeypatch)
    lib = fx_install.file("FxLib", "fxsym.ctl")
    win.open_document(fx_install.page("FxSymbols"))
    doc = win.doc
    serial = doc.derive_serial
    monkeypatch.setattr(UpdatePagesRun, "users_busy", "fxsym.fx_on")
    _other_program_saves(lib, "fx_on", tmp_path)  # (the helper's write: another process)
    win._lib_watch.rescan()
    assert win._check_library_changes() == []
    assert doc.derive_serial == serial
    monkeypatch.setattr(UpdatePagesRun, "users_busy", "")
    after = []
    monkeypatch.setattr(type(win), "_after_chain_saves", lambda self, res: after.append(res))
    win.statusBar().clearMessage()
    win._users_run_finished(
        {"libraries": [lib], "elements_saved": ["fxsym.fx_on"], "updated": []}, "fxsym.fx_on"
    )
    assert after, "the chain's end brings the open tabs up to date"
    st = os.stat(lib)
    assert written_stamp(lib) == (st.st_mtime_ns, st.st_size)
    assert "another program" not in win.statusBar().currentMessage()
    win._on_library_files_changed([lib])
    assert win._check_library_changes() == []


def test_a_library_another_program_saved_during_the_chain_is_looked_at_after_it(
    fx_install, win, tmp_path, monkeypatch
):
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from ctgedit.ui.window import libwatch

    # (with the wait setting on; off, it is taken at once: test_open_tabs_follow.py)
    app_settings().setValue("opt/tabs_wait_for_runs", True)
    _no_jobs(win, monkeypatch)
    lib = fx_install.file("FxLib", "fxsym.ctl")
    win.open_document(fx_install.page("FxSymbols"))
    doc = win.doc
    serial = doc.derive_serial
    monkeypatch.setattr(UpdatePagesRun, "users_busy", "fxnest.n0")
    _other_program_saves(lib, "fx_on", tmp_path)
    win._lib_watch.rescan()
    assert win._check_library_changes() == []
    monkeypatch.setattr(UpdatePagesRun, "users_busy", "")
    done = libwatch.chain_saves_settled([fx_install.file("FxLib", "fxnest.ctm")], first=win)
    assert doc in done and doc.derive_serial > serial
    assert "changed by another program" in win.statusBar().currentMessage()


# -- 4. the memos on disk -------------------------------------------------------------------
def test_the_memo_key_holds_the_composition_and_app_versions(monkeypatch):
    import ctgedit
    from ctgedit.infra import noop_memo

    k = noop_memo.key(b"page", {("a", "b"): None}, {"a.b": [["x", 1, 2]]})
    monkeypatch.setattr(noop_memo, "COMPOSITION_VERSION", noop_memo.COMPOSITION_VERSION + "x")
    k2 = noop_memo.key(b"page", {("a", "b"): None}, {"a.b": [["x", 1, 2]]})
    assert k2 != k
    monkeypatch.setattr(ctgedit, "__version__", ctgedit.__version__ + ".1")
    assert noop_memo.key(b"page", {("a", "b"): None}, {"a.b": [["x", 1, 2]]}) != k2
    assert noop_memo.key(b"page", {}, {}, context=("t",)) != noop_memo.key(b"page", {}, {})


def test_the_memo_key_follows_the_tag_tables_and_library_settings(fx_install, monkeypatch):
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(fx_install.page("FxGenies"))
    inst = d.genie_instances()
    assert inst
    lib, el = inst[0][1][0], inst[0][1][1]
    changes = {(lib.lower(), el.lower()): None}
    k1 = d.genie_noop_key(changes)
    assert k1 is not None
    tags = os.path.join(fx_install.project("FxMain"), "variable.DBF")
    _later(tags)
    k2 = d.genie_noop_key(changes)
    assert k2 != k1
    monkeypatch.setenv("CTG_TEXT_REPLACE", "1")
    assert d.genie_noop_key(changes) != k2


def test_view_refresh_empties_the_memos_on_disk(fx_install, win, monkeypatch):
    from ctgedit.infra import answer_memo, noop_memo

    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxShapes"))
    k = noop_memo.key(b"page", {("a", "b"): None}, {})
    noop_memo.remember(k)
    answer_memo.put(k, {"format": 1, "ops": []})
    assert noop_memo.known(k) and answer_memo.get(k) is not None
    queued = []
    monkeypatch.setattr(type(win), "_queue_open_genie_updates", lambda self: queued.append(self))
    done = {"x": 1}
    win.doc.ui.genie_sources_done = done
    win._refresh_view()
    assert not noop_memo.known(k) and answer_memo.get(k) is None
    assert queued and done == {}


# -- 5. the warm worker ---------------------------------------------------------------------
class _In:
    def __init__(self):
        self.lines, self.closed = [], False

    def write(self, b):
        self.lines.append(b)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _Proc:
    def __init__(self):
        self.stdin, self.ended = _In(), False

    def poll(self):
        return 0 if self.ended else None

    def wait(self, timeout=None):
        self.ended = True

    def kill(self):
        self.ended = True


def test_the_warm_worker_starts_again_when_a_setting_changed(monkeypatch):
    from ctgedit.infra import workers
    from ctgedit.ui.jobs import genie_jobs as GJ

    procs = []
    monkeypatch.setattr(workers, "start", lambda cmd, **k: procs.append(_Proc()) or procs[-1])
    monkeypatch.delenv("CTG_GENIE_NESTED_COPIES", raising=False)
    w = GJ.WarmWorker()
    w.submit("r1", "a1")
    w.submit("r2", "a2")
    assert len(procs) == 1 and w.current()
    # Settings > Pages and libraries: the nested-copies mode (apply_library_folders)
    monkeypatch.setenv("CTG_GENIE_NESTED_COPIES", "1")
    assert not w.current()
    w.submit("r3", "a3")
    assert len(procs) == 2 and procs[0].stdin.closed and w.current()
    monkeypatch.setenv("CTG_LIB_PATH", "C:\\Libs")
    w.submit("r4", "a4")
    assert len(procs) == 3


def test_an_answer_composed_with_old_settings_is_asked_again(monkeypatch):
    _app()
    from ctgedit.ui.jobs import genie_jobs as GJ

    jobs = GJ.GenieJobs()
    try:
        started, finished = [], []
        jobs.finished.connect(lambda *a: finished.append(a))

        class _Job:
            doc = object()
            names = ["fxnest.n1"]
            attempts = 1
            env = ("old settings",)
            label = "update genies from library"
            task = None
            template = False
            bindings = None

            def done(self):
                return True

            def answer(self):
                return {"ops": []}

            def cleanup(self):
                pass

            def start(self, worker):
                started.append(self)
                self.attempts += 1
                self.env = GJ.environment_signature()

        job = _Job()
        jobs._jobs[id(job.doc)] = job
        jobs._poll()
        assert started == [job] and finished == []
    finally:
        jobs._jobs.clear()
        jobs._queue.clear()
        jobs._timer.stop()
        jobs._idle.stop()


# -- 6. Simulate ----------------------------------------------------------------------------
def test_simulate_reloads_once_after_the_editors_genie_update_landed(tmp_path, monkeypatch):
    """A library of a page that follows an editor document changed while the editor's genie
    update of that document had not landed: no reload until it has, then one reload with
    the update, and no 'the editor updated this page' note."""
    _app()
    from ctgedit import editing
    from ctgedit.ui.simulate import live_reload
    from ctgedit.ui.simulate import window as W
    from test_sim_engine_source import _rect_page, wait_for
    from test_sim_live_reload import _Doc, _ReloadSim

    proj = tmp_path / "User" / "Proj"
    proj.mkdir(parents=True)
    page_file = str(proj / "FxPage.ctg")
    with open(page_file, "wb"):
        pass
    lib = str(tmp_path / "pumps.ctm")
    with open(lib, "w") as fh:
        fh.write("a")

    def page(path, parsed=None, doc=None):
        p = _rect_page()
        p._sim_libraries = [lib]
        return p

    monkeypatch.setattr(W, "runtime_page", page)
    pending = {"on": False}
    monkeypatch.setattr(
        live_reload,
        "editor_genie_update_pending",
        lambda doc: pending["on"] and doc is not None,
        raising=False,
    )
    doc = _Doc(page_file)
    doc._undo = []
    doc.ui = types.SimpleNamespace(auto_changes=[])
    win = W.SimulateWindow()
    try:
        sim = _ReloadSim()
        sp = win.open_page(page_file, sim=sim, doc=doc, parsed=doc.parsed)
        win.tick()
        # the genie saved: its library written, the editor's update of the page queued
        with open(lib, "w") as fh:
            fh.write("bbb")
        pending["on"] = True
        editing.LIBRARY_GENERATION[0] += 1
        wait_for(lambda: (win.tick(), False)[1], timeout=1.5)
        assert sim.reloads == []
        # the update lands (an automatic step of the editor's)
        c = object()
        doc._undo.append(c)
        doc.ui.auto_changes.append(("genies updated from their library", c))
        doc.edit_serial += 1
        doc.derive_serial += 1
        pending["on"] = False
        assert wait_for(lambda: (win.tick(), sim.reloads)[1], timeout=15)
        wait_for(lambda: (win.tick(), False)[1], timeout=1.3)
        assert len(sim.reloads) == 1 and not sp.auto_behind
    finally:
        win.close()
        win.deleteLater()


def test_the_editors_pending_genie_update_is_seen(fx_install, win, monkeypatch):
    from ctgedit.ui.simulate.live_reload import editor_genie_update_pending

    win.open_document(fx_install.page("FxShapes"))
    doc = win.doc
    jobs = win._genie_job_runner()
    assert not editor_genie_update_pending(doc)
    jobs._queue.append(types.SimpleNamespace(doc=doc))
    try:
        assert editor_genie_update_pending(doc)
    finally:
        jobs._queue.clear()
    assert not editor_genie_update_pending(doc)


# -- 7. composition settings, memos and failed updates --------------------------------------
def test_the_memo_key_holds_the_digest_of_the_composition_code(tmp_path, monkeypatch):
    """A change of the composition code in a source run (COMPOSITION_VERSION unchanged)
    makes new memo keys: the key holds the digest of the composition's
    source files."""
    from ctgedit.infra import noop_memo

    root = os.path.dirname(os.path.dirname(os.path.abspath(noop_memo.__file__)))
    names = {os.path.relpath(p, root).replace(os.sep, "/") for p in noop_memo._source_files(root)}
    assert {
        "format/genie_place.py",
        "format/genie_norm.py",
        "doc/library.py",
        "workers/genie_worker.py",
    } <= names
    src = tmp_path / "genie_place.py"
    src.write_text("x = 1\n")
    monkeypatch.setattr(noop_memo, "COMPOSITION_SOURCES", (str(src),))
    monkeypatch.setitem(noop_memo._CODE, "digest", None)  # (the real digest after it)
    noop_memo._CODE.clear()
    k1 = noop_memo.key(b"page", {("a", "b"): None}, {"a.b": [["x", 1, 2]]})
    src.write_text("x = 2\n")  # (the change)
    noop_memo._CODE.clear()  # (the next start of the editor)
    k2 = noop_memo.key(b"page", {("a", "b"): None}, {"a.b": [["x", 1, 2]]})
    assert k2 != k1
    assert noop_memo.key(b"page", {("a", "b"): None}, {"a.b": [["x", 1, 2]]}) == k2


def test_a_composition_setting_change_looks_at_the_open_genies_again(fx_install, win, monkeypatch):
    """Settings > Pages and libraries: the nested-copies mode changed. The open pages that
    were updated in the old mode are looked at again (their memo emptied, the genie updates
    queued), as View > Refresh does; a setting the composition does not read leaves them."""
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.prefs.settings import app_settings

    _no_jobs(win, monkeypatch)
    monkeypatch.setenv("CTG_GENIE_NESTED_COPIES", "1")
    monkeypatch.delenv("CTG_GENIE_NESTED_COPIES")  # (restored after the test)
    app_settings().setValue("opt/genie_nested_full", True)
    win.open_document(fx_install.page("FxShapes"))
    queued = []
    monkeypatch.setattr(type(win), "_queue_open_genie_updates", lambda self: queued.append(self))
    done = {"x": 1}
    win.doc.ui.genie_sources_done = done
    for k in ("CTG_LIB_PATH", "CTG_LIB_SIBLINGS", "CTG_TEXT_REPLACE"):
        monkeypatch.setenv(k, os.environ.get(k, ""))  # (restored after the test)
    from ctgedit.ui.prefs.settings import apply_library_folders

    apply_library_folders()  # (the environment as the settings say)
    dlg = SettingsDialog(win)
    try:
        dlg._flags["show_auto_changes"].setChecked(not dlg._flags["show_auto_changes"].isChecked())
        dlg._apply(accept=False)
        assert queued == [] and done == {"x": 1}
        dlg._flags["genie_nested_full"].setChecked(False)
        dlg._apply(accept=False)
        assert os.environ.get("CTG_GENIE_NESTED_COPIES") == "1"
        assert queued and done == {}
    finally:
        dlg.close()


def test_the_declared_parameters_follow_a_same_tick_save(fx_install):
    """The genie dialog's declared parameters are cached by the library's file version: a
    second write of one size within one clock tick is another version."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import fileio

    d = EditableDocument.open(fx_install.page("FxGenies"))
    idx = d.genie_instances()[0][0]
    assert d.genie_declared_params(idx) is not None
    keys = set(d.__dict__.get("_declared_cache", {}))
    res = d._resolver()
    from ctgedit.format.symbols import reference_from_object

    lib = res.resolve(reference_from_object(d.parsed.page.objects[idx])).library_path
    fileio.note_changed(lib)  # (as a write that kept the file's mtime and size)
    d.genie_declared_params(idx)
    assert set(d.__dict__["_declared_cache"]) - keys


def test_a_failed_update_users_looks_at_what_changed_while_it_ran(fx_install, win, tmp_path, monkeypatch):
    """Update Users (in this process) failed: a library another program saved while it ran
    (held by the watch) is looked at all the same, not left until the next chain ends."""
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.window import libwatch

    _no_jobs(win, monkeypatch)
    lib = fx_install.file("FxLib", "fxsym.ctl")
    win.open_document(fx_install.page("FxSymbols"))
    doc = win.doc
    serial = doc.derive_serial
    users = [
        types.SimpleNamespace(
            ref=types.SimpleNamespace(label="fxnest.n1", path=fx_install.file("FxLib", "fxnest.ctm"))
        )
    ]
    monkeypatch.setattr(GU, "update_plan", lambda usage: GU.UpdatePlan(users, []))

    def failing(plan, **k):
        _other_program_saves(lib, "fx_on", tmp_path)
        win._lib_watch.rescan()
        assert win._check_library_changes() == []  # (held while it runs)
        raise RuntimeError("the update failed")

    monkeypatch.setattr(GU, "run_update", failing)
    usage = types.SimpleNamespace(target=types.SimpleNamespace(label="fxnest.n0"))
    with pytest.raises(RuntimeError):
        win._update_element_users_run(usage, confirm=False)
    assert not libwatch._HELD
    assert doc.derive_serial > serial
