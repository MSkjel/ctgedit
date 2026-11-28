"""Background tasks."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app


def _pump(n=8):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1280, 720)
    w.show()
    _pump()
    yield w
    from ctgedit.ui.jobs import tasks

    for t in list(tasks.running()):
        tasks.drop(t.token)
    for x in list(MainWindow.instances):
        if x in MainWindow.instances:
            MainWindow.instances.remove(x)
        x.hide()


def test_a_task_shows_the_button_with_its_text_and_hides_on_end(win):
    from ctgedit.ui.jobs import tasks

    b = win.status_tasks
    assert b.isHidden()
    t = tasks.begin("test", "Updating 2 genies on Mixer\u2026", window=win)
    _pump()
    assert not b.isHidden() and "Updating 2 genies on Mixer" in b.text()
    assert b._spin.isActive()  # the mark turns
    tasks.progress(t, 240, 414)
    assert "240 of 414" in b.text()
    tasks.end(t)
    _pump()
    assert b.isHidden() and not b._spin.isActive()


def test_two_tasks_show_the_newest_and_a_count(win):
    from ctgedit.ui.jobs import tasks

    a = tasks.begin("a", "First\u2026", window=win)
    b = tasks.begin("b", "Second\u2026", window=win)
    _pump()
    assert win.status_tasks.text().startswith("Second") and "(+1)" in win.status_tasks.text()
    tasks.end(b)
    assert win.status_tasks.text().startswith("First")
    tasks.end(a)


def test_a_task_of_another_window_does_not_show_here(win):
    from ctgedit.ui.jobs import tasks
    from ctgedit.ui.main_window import MainWindow

    other = MainWindow()
    try:
        t = tasks.begin("x", "Elsewhere\u2026", window=other)
        _pump()
        assert win.status_tasks.isHidden() and not other.status_tasks.isHidden()
        tasks.end(t)
    finally:
        MainWindow.instances.remove(other)
        other.hide()


def test_cancel_calls_the_callback_from_the_list(win):
    from ctgedit.ui.jobs import tasks

    called = []
    t = tasks.begin("x", "Finding in closed pages\u2026", cancel=lambda: called.append(1), window=win)
    tasks.progress(t, 3, 10)
    _pump()
    win.status_tasks.toggle_list()
    pop = win.status_tasks.popover
    _pump()
    assert pop.isVisible()
    bars = pop.rows.findChildren(QtWidgets.QProgressBar)
    assert bars and bars[0].maximum() == 10 and bars[0].value() == 3
    cancel = [b for b in pop.rows.findChildren(QtWidgets.QToolButton) if b.text() == "Stop"]
    cancel[0].click()
    assert called == [1] and tasks.registry().tasks[t].cancelled
    tasks.end(t)
    _pump()
    assert win.status_tasks.isHidden()
    pop.close()


def test_a_busy_task_has_a_moving_bar(win):
    from ctgedit.ui.jobs import tasks

    t = tasks.begin("x", "Checking the display\u2026", window=win)
    win.status_tasks.toggle_list()
    bar = win.status_tasks.popover.rows.findChildren(QtWidgets.QProgressBar)[0]
    assert bar.maximum() == 0  # busy: no counts
    win.status_tasks.popover.close()
    tasks.end(t)


def test_a_long_task_says_how_it_ended(win, monkeypatch):
    from ctgedit.ui import notify
    from ctgedit.ui.jobs import tasks

    monkeypatch.setattr(tasks, "LONG_S", -1.0)
    c = notify.centre(win)
    c.clear()
    t = tasks.begin("find", "Finding in closed pages\u2026", window=win)
    tasks.end(t, summary="Found 12 matches in 4 pages.")
    assert c.entries[-1].text == "Found 12 matches in 4 pages."
    assert c.entries[-1].level == "success"
    t = tasks.begin("find", "Finding in closed pages\u2026", window=win)
    tasks.end(t)
    assert c.entries[-1].text == "Finding in closed pages: done."
    t = tasks.begin("find2", "Checking\u2026", window=win)
    tasks.end(t, ok=False)
    assert c.entries[-1].level == "error" and "failed" in c.entries[-1].text


def test_a_short_task_ends_without_a_word(win):
    from ctgedit.ui import notify
    from ctgedit.ui.jobs import tasks

    c = notify.centre(win)
    c.clear()
    t = tasks.begin("x", "Quick\u2026", window=win)
    tasks.end(t)
    assert c.entries == []


def test_the_taskbar_is_quiet_off_windows_platform(win):
    from ctgedit.ui.jobs import taskbar

    assert not taskbar.enabled()  # (offscreen)
    assert taskbar.set_progress(win, 1, 2) is False
    assert taskbar.set_busy(win) is False
    taskbar.flash_error(win)
    dlg = QtWidgets.QProgressDialog("x", "Cancel", 0, 0, win)
    rep = taskbar.ProgressReporter(dlg, win)
    rep.update()
    rep.finish()
    dlg.close()


def test_a_helper_job_is_a_task_until_it_ends(win, tmp_path):
    """Find / Replace in closed pages and Find Duplicate Pictures run in a helper process
    (ui/jobs/helper_job): the job is a task with its progress, Cancel asks the helper to stop."""
    from ctgedit.ui.jobs import tasks
    from ctgedit.ui.jobs.helper_job import HelperJob

    script = tmp_path / "helper.py"
    script.write_text(
        "import json, sys, time\n"
        "req = json.load(open(sys.argv[1]))\n"
        "json.dump({'i': 1, 'n': 4, 'name': 'A'}, open(req['progress_file'], 'w'))\n"
        "time.sleep(0.8)\n"
        "json.dump({'found': 3}, open(sys.argv[2], 'w'))\n",
        encoding="utf-8",
    )
    dlg = QtWidgets.QDialog(win)
    job = HelperJob(
        dlg,
        lambda req, ans: [sys.executable, str(script), req, ans],
        {},
        str(tmp_path / "jobs"),
        label="Find in closed pages",
    )
    got = []
    job.finished.connect(got.append)
    assert job.start()
    _pump()
    t = tasks.registry().tasks.get(job.task)
    assert t is not None and t.window is win and t.cancel is not None
    for _ in range(60):
        QtTest.QTest.qWait(100)
        if got:
            break
    assert got and got[0] == {"found": 3}
    assert job.task is None and not any(x.kind.startswith("helper:") for x in tasks.running())


def test_a_genie_job_is_a_task_and_cancel_leaves_the_page(win):
    from types import SimpleNamespace
    from ctgedit.ui.jobs import tasks
    from ctgedit.ui.jobs.genie_jobs import GenieJob, GenieJobs

    jobs = GenieJobs(win)
    doc = SimpleNamespace(path=r"C:\x\Mixer.ctg", ui=None)
    job = GenieJob(doc, {("lib", "pump"): set()}, ["lib.pump", "lib.valve", "lib.tank"])
    assert job.text() == "Updating 3 genies on Mixer\u2026"
    jobs._begin_task(job)
    jobs._queue.append(job)
    t = tasks.registry().tasks[job.task]
    assert t.window is win and t.text == job.text()
    assert tasks.cancel(job.task)  # the user's Cancel
    assert job not in jobs._queue and job.task not in tasks.registry().tasks
    # a page that closes: its job's task just goes
    job2 = GenieJob(doc, {("lib", "pump"): set()}, ["lib.pump"])
    jobs._begin_task(job2)
    jobs._queue.append(job2)
    jobs.cancel(doc)
    assert job2.task not in tasks.registry().tasks
    jobs.shutdown()


def test_the_expression_check_of_check_page_is_a_task(win, tmp_path, monkeypatch):
    """Check Page compiles every expression one after another: a task 'Checking expressions
    of <page>: i of n', Cancel stops it."""
    from types import SimpleNamespace
    from ctgedit.ui.jobs import tasks
    from ctgedit.features import page_checks as PC

    fields = [SimpleNamespace(text="A + %d" % k, kind="expr") for k in range(5)]
    monkeypatch.setattr(PC, "engine_findings", lambda doc, f, res: [])
    pending = []

    class Checker:
        def check(self, text, kind, done):
            pending.append(done)

    from ctgedit.ui import expression_check

    monkeypatch.setattr(expression_check, "checker_for_document", lambda doc: Checker())
    from ctgedit.editing import DocumentUiState

    doc = SimpleNamespace(path=str(tmp_path / "Pg.ctg"), ui=DocumentUiState())
    rep = SimpleNamespace(expressions=0, compiled=0, engine_note="", findings=[], fields=list(fields))
    monkeypatch.setattr(win, "_show_page_report", lambda d=None: None)
    win._compile_page_fields(doc, rep)
    task = next(t for t in tasks.running(win) if t.kind == "expressions")
    assert task.n == 5 and task.text.startswith("Checking expressions of Pg")
    pending.pop(0)({})
    assert task.i == 1
    tasks.cancel(task.token)  # stops after the one being compiled
    assert task.token not in tasks.registry().tasks
    assert "stopped" in rep.engine_note


def test_a_task_whose_caller_tells_the_result_posts_nothing(win, monkeypatch):
    """A compile says its result in its own message: its task's end adds none (no second
    copy in the bell's list)."""
    from ctgedit.ui import notify
    from ctgedit.ui.jobs import tasks

    monkeypatch.setattr(tasks, "LONG_S", -1.0)
    c = notify.centre(win)
    c.clear()
    t = tasks.begin("compile", "Compiling FxMain\u2026", window=win)
    tasks.end(t, ok=False, tell=False)
    assert c.entries == [] and t not in tasks.registry().tasks


def test_a_newer_expression_check_drops_the_older_task(win, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ctgedit.ui import notify
    from ctgedit.ui.jobs import tasks
    from ctgedit.features import page_checks as PC

    monkeypatch.setattr(tasks, "LONG_S", -1.0)
    fields = [SimpleNamespace(text="A", kind="expr")] * 3
    monkeypatch.setattr(PC, "engine_findings", lambda doc, f, res: [])
    pending = []

    class Checker:
        def check(self, text, kind, done):
            pending.append(done)

    from ctgedit.ui import expression_check

    monkeypatch.setattr(expression_check, "checker_for_document", lambda doc: Checker())
    from ctgedit.editing import DocumentUiState

    doc = SimpleNamespace(path=str(tmp_path / "Pg.ctg"), ui=DocumentUiState())
    monkeypatch.setattr(win, "_show_page_report", lambda d=None: None)
    win._compile_page_fields(
        doc, SimpleNamespace(expressions=0, compiled=0, engine_note="", findings=[], fields=list(fields))
    )
    first = pending.pop(0)
    c = notify.centre(win)
    c.clear()
    win._compile_page_fields(
        doc, SimpleNamespace(expressions=0, compiled=0, engine_note="", findings=[], fields=list(fields))
    )
    first({})  # the older check's answer comes late
    checks = [t for t in tasks.running(win) if t.kind == "expressions"]
    assert len(checks) == 1  # only the newer one
    assert not any("Checked" in m.text for m in c.entries)  # the older one said nothing


def test_a_finished_find_or_replace_says_what_it_found():
    from ctgedit.ui.jobs.helper_job import summary_of

    assert (
        summary_of("Find in closed pages", {"rows": [1] * 12, "searched": 40})
        == "Find in closed pages: 12 matches in 40 pages."
    )
    assert (
        summary_of("Find in closed pages", {"rows": [1], "searched": 1, "more": True})
        == "Find in closed pages: at least 1 match in 1 page."
    )
    assert (
        summary_of("Replace All in closed pages", {"replaced": 14, "updated": ["A", "B", "C"]})
        == "Replace All in closed pages: 14 replacements in 3 pages."
    )
    assert (
        summary_of("Find in closed pages", {"rows": [], "searched": 9}, stopped=True)
        == "Find in closed pages (stopped): 0 matches in 9 pages."
    )
    assert summary_of("Find Duplicate Pictures", {"found": 3}) is None


def test_update_pages_is_a_task_with_its_progress(win, tmp_path, monkeypatch):
    """Tools > Update Pages runs in a helper process behind its progress window: it is a
    background task too (with the progress on the taskbar button), gone when it ends."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui.jobs import tasks

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "Mixer.ctg").write_bytes(b"page")  # (never read: a fake helper)
    script = tmp_path / "pages.py"
    script.write_text(
        "import json, sys, time\n"
        "req = json.load(open(sys.argv[1]))\n"
        "json.dump({'i': 2, 'n': 5, 'name': 'Mixer'}, open(req['progress_file'], 'w'))\n"
        "time.sleep(1.0)\n"
        "json.dump({'format': %r, 'records': [{'i': 0, 'name': 'Mixer', 'project': 'proj',"
        " 'outcome': 'current', 'checked': 1}]}, open(sys.argv[2], 'w'))\n" % GW.FORMAT,
        encoding="utf-8",
    )
    monkeypatch.setattr(GW, "users_command", lambda req, ans: [sys.executable, str(script), req, ans])
    seen = []
    real = tasks.progress

    def spy(token, i, n, text=None):
        t = tasks.registry().tasks.get(token)
        seen.append((t.kind if t else None, i, n))
        real(token, i, n, text)

    monkeypatch.setattr(tasks, "progress", spy)
    rep = win._update_pages_in_worker({"dirs": [str(proj)]}, parts=1)
    assert rep is not None and rep.pages == 1
    assert ("update-pages", 2, 5) in seen
    assert not any(t.kind == "update-pages" for t in tasks.running())
