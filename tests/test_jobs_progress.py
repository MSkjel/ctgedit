"""Long jobs show progress and can be cancelled."""

import contextlib
import os
import shutil
import sys
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_update_pages_chain import _fake_report  # noqa: E402

from ctgedit.ui.jobs.run_stats import RunStats  # noqa: E402
from qtkit import app as _app


def _pump(n=6):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def app():
    return _app()


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.close()
    w.deleteLater()


@pytest.fixture()
def pages(tmp_path, fx_page):
    """Copies of FxShapes in tmp_path (beside no project tables)."""

    def make(name):
        p = str(tmp_path / name)
        shutil.copy2(fx_page("FxShapes"), p)
        return p

    return make


def _edit(doc):
    doc.move_objects([doc.page.roots()[-1]], 2, 2)
    assert doc.dirty


class _FakeProgress:
    """A JobProgress without a window: records its ticks; Cancel 'was clicked' after
    ``cancel_at`` ticks or waits (None: never)."""

    made: list = []
    cancel_at = None

    def __init__(self, parent, title, head, phase="", stats=None, cancel="Cancel", total=0, **kw):
        self.title, self.head, self.phase, self.cancel_text = title, head, phase, cancel
        self.kw = kw  # (show_after_ms, status_bar, lets_tabs_update ...)
        self.ticks, self.pulses, self.closed = [], 0, False
        self.stats = stats if stats is not None else RunStats.from_settings()
        if phase:
            self.stats.begin_phase(phase, int(total))
        self._cancelled = False
        # (the taskbar reporter reads a QProgressDialog: one that never shows)
        self.prog = QtWidgets.QProgressDialog(head, cancel, 0, 0, parent)
        self.prog.setMinimumDuration(10**8)
        type(self).made.append(self)

    def cancel(self):
        self._cancelled = True

    def _look(self, n):
        if self.cancel_at is not None and n > self.cancel_at:
            self._cancelled = True
        return not self._cancelled

    def tick(self, i, n, head=None):
        self.ticks.append((int(i), int(n), head))
        return self._look(len(self.ticks))

    def pulse(self, head=None):
        self.pulses += 1
        return self._look(self.pulses)

    def wait(self, ms):
        self.pulses += 1
        return self._look(self.pulses)

    @property
    def cancelled(self):
        return self._cancelled

    def set(self, head):
        self.head = head

    def close(self):
        self.closed = True
        self.prog.deleteLater()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@pytest.fixture()
def fake_progress(monkeypatch):
    from ctgedit.ui.jobs import job_progress

    _FakeProgress.made = []
    _FakeProgress.cancel_at = None
    monkeypatch.setattr(job_progress, "JobProgress", _FakeProgress)
    return _FakeProgress


# -- the window itself ----------------------------------------------------------------------
def test_the_progress_window_shows_the_figures_and_reads_cancel(app):
    from ctgedit.ui.jobs.job_progress import JobProgress

    c = Clock()
    p = JobProgress(
        None,
        "Used By",
        "Finding…",
        phase="index",
        total=4,
        stats=RunStats(clock=c, min_items=1, min_seconds=0),
    )
    try:
        assert p.prog.windowModality() == QtCore.Qt.WindowModal
        assert p.prog.windowTitle() == "Used By"
        c.t += 10
        assert p.tick(1, 4, "Reading a… (2 of 4)") is True
        text = p.prog.labelText()
        assert text.startswith("Reading a… (2 of 4)\nReading the pages and libraries: 1 of 4 done")
        assert "10 s so far" in text and "files a minute" in text
        p.prog.cancel()
        assert p.tick(2, 4) is False and p.cancelled
    finally:
        p.close()
    # without counts: the time so far only; Cancel can be left out
    q = JobProgress(
        None, "Compile", "Compiling…", cancel=None, stats=RunStats(clock=c, min_items=1, min_seconds=0)
    )
    try:
        assert q.figures() == ["Time: 0 s so far"]
        assert q.wait(5) is True
    finally:
        q.close()
    # (a closed QProgressDialog says it was cancelled: the job's answer stays what it was)
    _pump()
    assert not q.cancelled and p.cancelled


# -- 1, 3, 8: the usage index ---------------------------------------------------------------
def test_build_index_reports_every_file_and_a_false_stops_it(fx_install):
    from ctgedit.io import genieusage as GU

    dirs = [fx_install.project("FxMain"), fx_install.project("FxLib")]
    seen = []
    idx = GU.build_index(dirs, progress=lambda i, n, name: seen.append((i, n, name)))
    assert not idx.stopped and seen
    n = seen[0][1]
    assert [s[0] for s in seen] == list(range(len(seen))) and len(seen) == n
    assert all(s[1] == n for s in seen)
    names = [s[2] for s in seen]
    assert "FxLib\\fxsym.ctl" in names and any(x.lower().endswith(".ctg") for x in names)
    stopped = GU.build_index(dirs, progress=lambda i, n, name: False)
    assert stopped.stopped and not stopped.referrers
    assert GU.element_users(
        fx_install.file("FxLib", "fxsym.ctl"), "fx_box", progress=lambda i, n, name: False
    ).stopped


def test_used_by_reads_behind_a_progress_window_and_cancel_gives_none(win, fake_progress, monkeypatch):
    from ctgedit.io import genieusage as GU

    calls = []

    def fake_users(lib, el, progress=None, **k):
        for i, name in enumerate(("P\\a.ctg", "P\\b.ctm")):
            calls.append(progress(i, 2, name))
        return types.SimpleNamespace(users=[], stopped=False)

    monkeypatch.setattr(GU, "element_users", fake_users)
    assert win._element_users("C:\\P\\lib.ctm", "e1") is not None
    p = fake_progress.made[-1]
    assert p.title == "Used By" and p.phase == "index" and p.closed
    assert [t[2] for t in p.ticks] == ["Reading P\\a.ctg… (1 of 2)", "Reading P\\b.ctm… (2 of 2)"]
    assert calls == [True, True]
    fake_progress.cancel_at = 1
    assert win._element_users("C:\\P\\lib.ctm", "e1", title="Genie Saved") is None
    assert fake_progress.made[-1].title == "Genie Saved"
    assert "Stopped" in win.statusBar().currentMessage()


def test_the_used_by_window_refreshes_through_the_editor_without_a_wait_cursor(app):
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.dialog.library_dialogs import ElementUsersDialog

    usage = GU.Usage(GU.Ref("element", "C:\\P\\lib.ctm", "e1", "P"))
    dlg = ElementUsersDialog("C:\\P\\lib.ctm", "e1", usage, on_refresh=lambda: None)
    dlg.refresh()  # (cancelled: the list stays)
    assert dlg.usage is usage and QtWidgets.QApplication.overrideCursor() is None
    dlg.close()


# -- 2: the pages open in tabs -----------------------------------------------------------
def test_the_open_tabs_step_has_a_window_and_cancel_leaves_the_rest(win, fake_progress, pages, tmp_path):
    a, b = pages("A.ctg"), pages("B.ctg")
    win.open_document(a)
    win.open_document(b)
    opts = {"genies": True, "templates": True, "symbols": True}
    assert (
        win._update_open_documents_shown([str(tmp_path / "none")], opts, "Update Pages").paths() == []
        and not fake_progress.made
    )  # (no page open: no window)
    res = win._update_open_documents_shown([str(tmp_path)], opts, "Update Pages")
    p = fake_progress.made[-1]
    assert p.title == "Update Pages" and p.phase == "tabs" and p.closed
    assert [t[2] for t in p.ticks] == ["Updating A in its tab… (1 of 2)", "Updating B in its tab… (2 of 2)"]
    assert sorted(res.paths()) == sorted([a, b]) and not res.left
    fake_progress.cancel_at = 1
    res = win._update_open_documents_shown([str(tmp_path)], opts, "Update Users")
    assert res.left == [b] and res.paths() == [a]
    assert any(
        "not updated because the run was stopped" in x for x in res.lines(lambda p: os.path.basename(p))
    )


# -- 4: Save All ---------------------------------------------------------------------------
def test_save_all_keeps_the_shown_tab_and_can_stop(win, fake_progress, pages, monkeypatch):
    a, b = pages("A.ctg"), pages("B.ctg")
    win.open_document(a)
    doc_a = win.doc
    win.open_document(b)
    doc_b = win.doc
    win.set_document(doc_a)
    _edit(doc_a)
    _edit(doc_b)
    thumb = bytes(doc_b.parsed.page.thumbnail or b"")
    switches = []
    orig = win.set_document
    monkeypatch.setattr(win, "set_document", lambda d: switches.append(d) or orig(d))
    win.save_all()
    assert not doc_a.dirty and not doc_b.dirty
    assert win.doc is doc_a and switches == []  # (the shown tab never changed)
    assert bytes(doc_b.parsed.page.thumbnail or b"") != thumb  # (drawn without its tab)
    p = fake_progress.made[-1]
    assert p.title == "Save All" and p.phase == "save" and p.closed
    assert [t[2] for t in p.ticks] == ["Saving A.ctg… (1 of 2)", "Saving B.ctg… (2 of 2)"]
    assert win.statusBar().currentMessage().startswith("Saved 2 pages.")
    _edit(doc_a)
    _edit(doc_b)
    fake_progress.cancel_at = 1
    win.save_all()
    assert not doc_a.dirty and doc_b.dirty
    assert "1 left when Save All was stopped" in win.statusBar().currentMessage()


# -- 5: the compile ------------------------------------------------------------------------
def test_the_compiler_loop_waits_through_the_given_wait(monkeypatch, tmp_path):
    from ctgedit.backend import compiler, winui

    monkeypatch.setattr(compiler, "exe_path", lambda: str(tmp_path / "CtCmp32.exe"))
    monkeypatch.setattr(compiler, "active_ini", lambda: str(tmp_path / "Config" / "citect.ini"))
    monkeypatch.setattr(winui, "pids_of", lambda img: [])
    monkeypatch.setattr(winui, "windows_of", lambda pid: [])

    class Proc:
        returncode = 0
        pid = 1

        def __init__(self, cmd):
            self.n = 0

        def poll(self):
            self.n += 1
            return 0 if self.n > 3 else None

    monkeypatch.setattr(compiler.subprocess, "Popen", Proc)
    waits = []
    monkeypatch.setattr(compiler.time, "sleep", lambda s: waits.append(("sleep", s)))
    res = compiler.compile_project("FxMain", poll=0.2, wait=lambda s: waits.append(("wait", s)))
    assert res.outcome == "completed" and waits == [("wait", 0.2)] * 3


def test_the_compile_window_runs_the_event_loop_and_the_result_names_the_time(
    win, fake_progress, monkeypatch, fx_install
):
    from ctgedit.backend import compiler
    from ctgedit.ui.prefs import job_options
    from ctgedit.ui import notify

    seen = {}

    def fake_compile(root, on_tick=None, wait=None, poll=None, **k):
        seen["poll"] = poll
        assert on_tick() is True
        wait(0.01)
        return compiler.CompileResult(project=root, outcome="completed", exit_code=0, seconds=42.0)

    monkeypatch.setattr(compiler, "compile_project", fake_compile)
    monkeypatch.setattr(compiler, "root_project_for", lambda d: "FxMain")
    monkeypatch.setattr(compiler, "in_active_install", lambda d: True)
    monkeypatch.setattr(win, "_compile_target", lambda: (fx_install.project("FxMain"), ""))
    monkeypatch.setattr(win, "_show_compile_results", lambda r, p="": None)
    c = notify.centre(win)
    c.clear()
    assert win.compile_project(show="errors") is not None
    assert "completed in 42 s" in c.entries[-1].text
    p = fake_progress.made[-1]
    assert p.title == "Compile" and p.pulses >= 2 and p.closed
    assert seen["poll"] == pytest.approx(job_options.poll_ms() / 1000.0)


# -- 6: the display check ------------------------------------------------------------------
def test_check_page_display_is_window_modal_and_polls_from_the_loop(win, monkeypatch):
    from ctgedit.ui.prefs import job_options
    from ctgedit.ui.jobs import job_progress

    monkeypatch.setattr(job_options, "poll_ms", lambda: 5)
    modalities = []

    class Rec(job_progress.JobProgress):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            modalities.append(self.prog.windowModality())

    monkeypatch.setattr(job_progress, "JobProgress", Rec)

    class Chk:
        def __init__(self):
            self.polls, self.cancelled = 0, False

        def start(self):
            return True

        def poll(self):
            self.polls += 1
            return {"ok": 1} if self.polls >= 3 else None

        def cancel(self):
            self.cancelled = True

    assert win._wait_for_check(Chk()) == {"ok": 1}
    assert modalities == [QtCore.Qt.WindowModal]

    class Leaving(Rec):
        def wait(self, ms):
            self._cancelled = True
            return False

    monkeypatch.setattr(job_progress, "JobProgress", Leaving)
    chk = Chk()
    assert win._wait_for_check(chk) is False and chk.cancelled


# -- 7, 16: the results window -------------------------------------------------------------
def test_the_results_window_has_copy_log_backup_and_compile(app, tmp_path):
    from ctgedit.ui.jobs.run_results import RunResultsDialog

    bk = str(tmp_path / "bk")
    os.makedirs(bk)
    dlg = RunResultsDialog("Update Pages", ["3 pages checked.", "Failed: x."], backup=bk, compile_offer=True)
    assert dlg.buttons() == ["Copy", "Open Log", "Open Backup Folder", "Compile Project"]
    assert dlg.text() == "3 pages checked.\nFailed: x."
    dlg.copy()
    assert QtWidgets.QApplication.clipboard().text() == "3 pages checked.\nFailed: x."
    dlg.show()
    dlg._compile.click()
    assert dlg.result() == RunResultsDialog.COMPILE
    assert RunResultsDialog("Pack Libraries", ["x"]).buttons() == ["Copy", "Open Log"]
    assert RunResultsDialog.RESIZE == "both"


def test_show_run_results_keeps_the_first_line_in_the_messages(win, monkeypatch):
    from ctgedit.ui import notify
    from ctgedit.ui.jobs import run_results

    monkeypatch.setattr(
        run_results.RunResultsDialog, "exec", lambda self: run_results.RunResultsDialog.COMPILE
    )
    c = notify.centre(win)
    c.clear()
    assert (
        win._show_run_results(
            "Update Pages", ["5 pages checked, 2 updated.", "Backup: x"], backup="C:\\bk", compile_offer=True
        )
        is True
    )
    assert c.entries[-1].text == "5 pages checked, 2 updated."
    assert c.entries[-1].level == "success" and "C:\\bk" in c.entries[-1].detail
    monkeypatch.setattr(run_results.RunResultsDialog, "exec", lambda self: 0)
    assert win._show_run_results("Update Pages", ["Failed: x."]) is False
    assert c.entries[-1].level == "warning"


def test_update_pages_tells_the_journal_recovery_after_the_result(win, monkeypatch, tmp_path):
    from ctgedit.io import pgregister
    from ctgedit.ui.dialog import dialogs

    pdir = tmp_path / "Proj"
    pdir.mkdir()

    class _Dlg:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return QtWidgets.QDialog.Accepted

        def options(self):
            return dict(
                templates=True, genies=True, symbols=True, backup=True, page_properties=False, chain=False
            )

        def projects(self):
            return [str(pdir)]

        def chain_projects(self):
            return [str(pdir)]

        def processes(self):
            return 1

    monkeypatch.setattr(dialogs, "UpdatePagesDialog", _Dlg)
    monkeypatch.setattr(pgregister, "probe_writable", lambda d: None)
    events = []
    rep = _fake_report()
    rep.recovered = [{"label": "an update", "restored": []}]
    monkeypatch.setattr(win, "_update_pages_in_worker", lambda job, parts=None: rep)
    monkeypatch.setattr(
        type(win),
        "_show_run_results",
        lambda self, title, lines, **k: (
            events.append(("box", k.get("compile_offer"), bool(k.get("backup")))) or True
        ),
    )
    monkeypatch.setattr(type(win), "report_journal_recovery", lambda self, notes: events.append("journal"))
    monkeypatch.setattr(win, "compile_project_command", lambda: events.append("compile"))
    win._update_pages()
    _pump()
    assert events == [("box", True, True), "journal", "compile"]


# -- 9: the background chain ---------------------------------------------------------------
def test_the_background_chain_is_a_task_and_its_end_stays_in_the_messages(win, monkeypatch, tmp_path):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui import notify
    from ctgedit.ui.jobs import tasks

    begun = []
    real_begin = tasks.begin
    monkeypatch.setattr(
        tasks, "begin", lambda kind, text, **k: begun.append((kind, text)) or real_begin(kind, text, **k)
    )
    bk = str(tmp_path / "bk")
    monkeypatch.setattr(
        GW,
        "update_closed_pages",
        lambda *a, **k: {
            "format": GW.FORMAT,
            "updated": ["p1"],
            "elements_saved": ["lib.g"],
            "backup": bk,
            "tabs_saved": [],
            "libraries": [],
        },
    )
    c = notify.centre(win)
    c.clear()
    assert win._start_users_run(str(tmp_path / "lib.ctm"), "g", "lib.g")
    assert begun and begun[-1][0] == "users" and "lib.g" in begun[-1][1]
    assert not [t for t in tasks.running() if t.kind == "users"]
    m = c.entries[-1]
    assert m.text.startswith("Updated 1 library genie and 1 closed page that use lib.g")
    assert "Backup: " + bk in m.detail and [a.label for a in m.actions] == ["Details"]
    assert m.level == "success"


# -- 10: Find and Replace All --------------------------------------------------------------
def test_find_has_a_helper_processes_box_and_a_time_line(fx_page):
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog
    from ctgedit.ui.prefs.settings import app_settings

    dlg = FindReplaceDialog(EditableDocument.open(fx_page("FxShapes")))
    try:
        assert dlg._procs.specialValueText() == "Automatic"
        dlg._rb_project.setChecked(True)
        assert dlg._procs.isEnabled()
        dlg._procs.setValue(3)
        assert int(app_settings().value("opt/find_processes")) == 3
        c = Clock()
        dlg._job_stats = RunStats(clock=c, min_items=1, min_seconds=0)
        dlg._job_stats.begin_phase("search", 10)
        c.t += 30
        line = dlg._job_line("Searching the closed pages: 3 of 10 (P)…", 2, 10)
        assert line.startswith("Searching the closed pages: 3 of 10 (P)… 30 s so far")
        assert "left" in line and "pages a minute" in line and line.endswith(".")
    finally:
        dlg.close()


# -- 11: the timings are settings ----------------------------------------------------------
def test_the_long_job_timings_are_settings_applied_live(win):
    from ctgedit.ui.jobs import genie_jobs, helper_job, tasks
    from ctgedit.ui.prefs import job_options
    from ctgedit.ui.browser import preview
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.simulate import modeljob
    from ctgedit.ui.window import files

    st = app_settings()
    try:
        assert job_options.values() == {
            "opt/task_report_s": 5,
            "opt/job_poll_ms": 300,
            "opt/worker_idle_s": 300,
            "opt/worker_low_memory_mb": 1500,
            "opt/preview_timeout_s": 120,
            "opt/model_wait_s": 120,
            "opt/busy_note_ms": 400,
            "opt/users_note_s": 60,
            "opt/users_read_window_ms": 2000,
            "opt/find_pages_per_helper": 8,
            "opt/save_helper_records": 1000,
            "opt/save_window_ms": 10000,
        }
        for k, v in (
            ("opt/task_report_s", 9),
            ("opt/job_poll_ms", 150),
            ("opt/worker_idle_s", 60),
            ("opt/worker_low_memory_mb", 2000),
            ("opt/preview_timeout_s", 30),
            ("opt/model_wait_s", 45),
            ("opt/busy_note_ms", 900),
            ("opt/users_note_s", 0),
            ("opt/find_pages_per_helper", 20),
        ):
            st.setValue(k, v)
        win.apply_job_options()
        assert tasks.LONG_S == 9 and helper_job.POLL_MS == 150 and job_options.poll_ms() == 150
        assert genie_jobs.WarmWorker.IDLE_S == 60 and genie_jobs.WarmWorker.LOW_MEMORY_MB == 2000
        assert preview._PreviewRenderer.TIMEOUT_S == 30 and modeljob.WAIT_S == 45
        assert files.PageFiles.BUSY_NOTE_MS == 900 and FindReplaceDialog.PAGES_PER_HELPER == 20
        assert job_options.users_note_s() == 0
        # out of its span or garbled: the default
        st.setValue("opt/job_poll_ms", 7)
        st.setValue("opt/task_report_s", "soon")
        win.apply_job_options()
        assert helper_job.POLL_MS == 300 and tasks.LONG_S == 5
        rows = {r.key: r for r in SettingsDialog.ROWS if r.section == "Long jobs"}
        assert set(rows) == set(job_options.KEYS) - {"opt/find_pages_per_helper"}
        assert all(r.apply == "window:apply_job_options" for r in rows.values())
        find = [r for r in SettingsDialog.ROWS if r.key == "opt/find_pages_per_helper"]
        assert find and find[0].section == "Find in closed pages"
        dlg = SettingsDialog(win)
        try:
            assert isinstance(dlg._row_widgets["opt/job_poll_ms"], QtWidgets.QSpinBox)
            assert dlg._row_widgets["opt/find_pages_per_helper"].value() == 20
        finally:
            dlg.close()
            dlg.deleteLater()
    finally:
        for k in job_options.KEYS:
            st.remove(k)
        job_options.load()


# -- 12: the Tags panel --------------------------------------------------------------------
def test_a_tag_read_can_be_stopped(fx_install):
    from ctgedit.infra import caches
    from ctgedit.io import project as PR

    caches.clear("tests")
    with pytest.raises(PR.TagIndexStopped):
        PR.tag_index(fx_install.project("FxMain"), progress=lambda n, m, p: False)
    assert PR.cached_tag_index(fx_install.project("FxMain")) is None  # (nothing half read)
    assert PR.tag_index(fx_install.project("FxMain")) is not None


def test_the_tags_panel_load_is_a_task_with_cancel(fx_install, monkeypatch):
    _app()
    from ctgedit.infra import caches
    from ctgedit.io import project as PR
    from ctgedit.ui.panels import tags_dock as TD
    from ctgedit.ui.jobs import tasks

    caches.clear("tests")
    real = PR.tag_index

    def slow(pdir, progress=None, near_only=False):
        def p(n, m, name):
            time.sleep(0.1)
            return progress(n, m, name)

        return real(pdir, progress=p if progress is not None else None, near_only=near_only)

    monkeypatch.setattr(PR, "tag_index", slow)
    d = TD.TagsDock()
    d.show()
    _pump()
    d.set_project_dir(fx_install.project("FxMain"))
    for _ in range(50):
        _pump()
        if d._loading:
            break
    assert d._loading
    # (this panel's task: a window an earlier test left may still read its own tags)
    token = d._task
    t = [t for t in tasks.running() if t.kind == "tags" and t.token == token]
    assert t and t[0].cancel is not None and t[0].quiet
    tasks.cancel(t[0].token)
    for _ in range(200):
        QtTest.QTest.qWait(20)
        if not d._loading:
            break
    assert not d._loading and "stopped" in d._count.text().lower()
    assert not [t for t in tasks.running() if t.token == token]
    assert d._loaded_key is None  # (read again when shown next)
    # the page closes while the read goes on: the read ends with it, its task too
    d.set_project_dir(fx_install.project("FxMain"))
    for _ in range(50):
        _pump()
        if d._loading:
            break
    token = d._task
    assert d._loading and [t for t in tasks.running() if t.token == token]
    d.set_project_dir(None)
    assert not d._loading and not [t for t in tasks.running() if t.token == token]
    assert d._dir is None and d._model.total() == 0
    QtTest.QTest.qWait(400)  # (the late answer changes nothing)
    assert d._dir is None and "stopped" not in d._count.text().lower()
    d.close()
    d.deleteLater()


def test_choosing_a_root_with_the_panel_hidden_reads_nothing_now(fx_install, monkeypatch):
    _app()
    from ctgedit.io import tagscope as TS
    from ctgedit.ui.panels import tags_dock as TD

    d = TD.TagsDock()
    d._dir = fx_install.project("FxMain")
    monkeypatch.setattr(
        d, "ensure_loaded", lambda: (_ for _ in ()).throw(AssertionError("read on the GUI thread"))
    )
    monkeypatch.setattr(TS, "chosen_root", lambda p: "")
    monkeypatch.setattr(TS, "choose_root", lambda p, r: None)
    d._root.addItem("FxMain", "FxMain")
    d._root.setCurrentIndex(d._root.count() - 1)
    d._root_chosen(0)
    assert d._loaded_key is None
    d.deleteLater()


# -- 13: Run's wait ------------------------------------------------------------------------
def test_runs_wait_can_be_left_and_the_end_is_told(win, fake_progress, monkeypatch, fx_install):
    from ctgedit.backend import compiler, runtime
    from ctgedit.ui.prefs import job_options

    monkeypatch.setattr(job_options, "poll_ms", lambda: 10)
    monkeypatch.setattr(win, "_compile_target", lambda: (fx_install.project("FxMain"), ""))
    monkeypatch.setattr(compiler, "in_active_install", lambda d: True)
    monkeypatch.setattr(compiler, "root_project_for", lambda d: "FxMain")
    monkeypatch.setattr(win, "_ask_compile_before_run", lambda: False)
    monkeypatch.setattr(runtime, "is_running", lambda: True)
    monkeypatch.setattr(runtime, "runs_project", lambda d: False)
    monkeypatch.setattr(runtime, "running_project", lambda: "Other")
    monkeypatch.setattr(win, "_confirm", lambda *a, **k: True)

    def start(pdir):
        time.sleep(0.3)
        return ["started"]

    monkeypatch.setattr(runtime, "start", start)
    fake_progress.cancel_at = 0  # (Stop Waiting at once)
    win._run_project()
    p = fake_progress.made[-1]
    assert p.title == "Run" and p.cancel_text == "Stop Waiting" and p.closed
    assert "still being stopped and started in the background" in win.statusBar().currentMessage()
    for _ in range(100):
        QtTest.QTest.qWait(20)
        if win.statusBar().currentMessage().startswith("Runtime starting"):
            break
    assert win.statusBar().currentMessage() == "Runtime starting: started."


# -- 14: Cut Link on a selection -----------------------------------------------------------
def test_cut_link_on_a_selection_cuts_every_symbol_as_one_step(win, fx_page, fake_progress, monkeypatch):
    from ctgedit.format import genie_place as GP, links

    win.open_document(fx_page("FxSymbols"))
    doc = win.doc
    roots = [
        i
        for i, po in enumerate(doc.objects)
        if doc.page.objects[i].parent is None
        and po.subtype_size & 0x1
        and po.type_code in (0x07, 0x08)
        and links.strip_stamp(GP.decode(po).link or "").lower().endswith(".<ctl")
        and (doc.symbol_link(i) or {}).get("path")
    ]
    assert len(roots) >= 3
    chosen = roots[:3]
    told = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information", lambda *a, **k: told.append(a[2]) or QtWidgets.QMessageBox.Ok
    )
    win._sel = list(chosen)
    win._update_actions()
    assert win.act_cut_link.isEnabled()
    steps = len(doc._undo)
    win._cut_link()
    assert len(doc._undo) == steps + 1  # (one undo step)
    assert len(win._sel) == 3 and not told
    assert all(doc.symbol_link(i) is None for i in win._sel)
    p = fake_progress.made[-1]
    assert p.title == "Cut Link" and p.phase == "unlink" and len(p.ticks) == 3 and p.closed
    assert win.statusBar().currentMessage().startswith("3 symbols are now groups of their own")
    win.act_undo.trigger()
    assert all(doc.symbol_link(i) is not None for i in chosen)


# -- 15: the composites behind the busy note -----------------------------------------------
def test_composite_steps_show_the_busy_note(win, fx_page, monkeypatch):
    win.open_document(fx_page("FxShapes"))
    notes = []

    @contextlib.contextmanager
    def busy(message=""):
        notes.append(message)
        yield

    monkeypatch.setattr(win, "_busy", busy)
    monkeypatch.setattr(win, "_after_composite_write", lambda: None)
    monkeypatch.setattr(win.doc, "composite_info", lambda r: None)
    monkeypatch.setattr(win.doc, "update_composite", lambda r: True)
    win._update_composite(0)
    monkeypatch.setattr(win.doc, "paste_composite_genie", lambda g, pos, params: 0)
    win._place_composite("{guid}", (10, 10), {})
    monkeypatch.setattr(win.doc, "set_composite_options", lambda r, v: True)
    win._apply_composite_options(0, types.SimpleNamespace(values=lambda: {}, _ctg_target=None))
    assert notes == ["Composing the composite genie…"] * 3


# -- 17: Find Duplicate Pictures -----------------------------------------------------------
def test_duplicate_pictures_status_says_the_time(win, fx_install, monkeypatch):
    from ctgedit.ui.jobs import helper_job

    class FakeJob(QtCore.QObject):
        progress = QtCore.Signal(int, int, str)
        finished = QtCore.Signal(dict)
        last = None

        def __init__(self, parent, *a, **k):
            super().__init__(parent)
            self.stopping = False
            FakeJob.last = self

        def start(self):
            return True

        def stop(self):
            self.stopping = True

        def wait(self, seconds=30):
            pass

    monkeypatch.setattr(helper_job, "HelperJob", FakeJob)
    from ctgedit.ui.dialog.duplicate_pictures import DuplicatePicturesDialog

    dlg = DuplicatePicturesDialog(win, fx_install.project("FxMain"))
    try:
        _pump()
        job = FakeJob.last
        assert job is not None and dlg.busy()
        job.progress.emit(2, 10, "FxShapes")
        text = dlg.status.text()
        assert text.startswith("Reading the pages: page 3 of 10 (FxShapes). ")
        assert "so far" in text and text.endswith(".")
    finally:
        dlg.close()
        dlg.deleteLater()


def test_save_all_of_an_element_not_shown_leaves_the_canvas_on_the_shown_tab(
    win, fake_progress, pages, fx_install, monkeypatch
):
    # (an element's save while the window's document is swapped to it must not show the
    # element over the page's tab)
    a = pages("A.ctg")
    win.open_document(a)
    doc_a = win.doc
    win._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_agitator_mixer")
    _pump()
    doc_e = win.doc
    assert doc_e is not doc_a
    win.set_document(doc_a)
    _edit(doc_e)
    monkeypatch.setattr(win, "_users_saved_prompt", lambda d: None)
    win.save_all()
    assert not doc_e.dirty and win.doc is doc_a
    assert win._page is doc_a.page and win.scene.shown[0] is doc_a
    assert win.statusBar().currentMessage().startswith("Saved 1 page.")


def test_the_finished_note_of_a_genie_update_stays_as_long_as_the_setting_says(win, monkeypatch, tmp_path):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui.prefs.settings import app_settings

    monkeypatch.setattr(
        GW,
        "update_closed_pages",
        lambda *a, **k: {
            "format": GW.FORMAT,
            "updated": ["p1"],
            "elements_saved": [],
            "backup": "",
            "tabs_saved": [],
            "libraries": [],
        },
    )
    st = app_settings()
    try:
        st.setValue("opt/users_note_s", 0)  # (until its Close)
        assert win._start_users_run(str(tmp_path / "lib.ctm"), "g", "lib.g")
        job = win._users_job

        def alive(w):
            try:
                return w.parent() is not None
            except RuntimeError:  # (removed and deleted)
                return False

        QtTest.QTest.qWait(150)
        assert alive(job)
        st.setValue("opt/users_note_s", 1)
        assert win._start_users_run(str(tmp_path / "lib.ctm"), "g", "lib.g")
        job2 = win._users_job
        assert job2 is not job
        for _ in range(60):
            QtTest.QTest.qWait(50)
            if not alive(job2):
                break
        assert not alive(job2)  # (removed after a second)
        assert alive(job)  # (the first one stays)
    finally:
        st.remove("opt/users_note_s")


def test_the_results_window_shows_what_needs_you_and_the_rest_on_demand(app):
    """The headline and the lines needing attention (a long one cut short, its whole text in
    the tool tip); every line only in the details, hidden by default and remembered."""
    from ctgedit.ui.jobs.run_results import RunResultsDialog

    long = "Failed: " + ", ".join("Page%03d" % k for k in range(60)) + "."
    lines = ["9 pages checked, 3 updated.", "Genies saved again first: a.", long, "Compile the project."]
    dlg = RunResultsDialog("Update Pages", lines, attention=[long])
    dlg.show()
    assert dlg.headline.text() == lines[0]
    assert len(dlg.attention) == 1 and len(dlg.attention[0].text()) < len(long)
    assert dlg.attention[0].toolTip() == long
    assert not dlg.details_shown() and dlg.details.text() == "Show Details"
    dlg.details.click()
    assert dlg.details_shown() and dlg.details.text() == "Hide Details"
    again = RunResultsDialog("Update Pages", lines, attention=[long])
    assert again.details_shown()  # (as left last time)
    again.details.click()
    assert not RunResultsDialog("Update Pages", lines).details_shown()
    # nothing left undone: said so, no warning lines
    ok = RunResultsDialog("Update Pages", lines[:2])
    assert ok.attention == [] and ok.note.text() == "Nothing needs your attention."
    # one line: nothing more to show
    assert RunResultsDialog("Pack Libraries", ["x"]).details.isHidden()
