"""Progress figures for long runs."""

import json
import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
from PySide6 import QtWidgets  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_update_pages_chain import _fake_report  # noqa: E402

from ctgedit.ui.jobs import run_stats as RS  # noqa: E402


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _fresh_notice(monkeypatch):
    """CtDraw's cache notice is the editor's (every window): each test starts with none
    shown and no run holding it."""
    from ctgedit.ui.window import files

    monkeypatch.setattr(files, "CACHE_NOTICE_RUN", {"holds": 0, "waiting": "", "shown": False})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _stats(clock, **kw):
    kw.setdefault("min_items", 3)
    kw.setdefault("min_seconds", 5)
    kw.setdefault("smoothing_s", 10)
    return RS.RunStats(clock=clock, **kw)


# -- the words ------------------------------------------------------------------------------
def test_the_times_read_plainly():
    assert RS.duration_text(12.4) == "12 s"
    assert RS.duration_text(65) == "1 min 5 s"
    assert RS.duration_text(180) == "3 min"
    assert RS.duration_text(4800) == "1 h 20 min"
    assert RS.duration_text(7200) == "2 h"
    assert RS.left_text(4) == "a few seconds left"
    assert RS.left_text(38) == "about 40 s left"
    assert RS.left_text(130) == "about 2 min left"
    assert RS.left_text(4790) == "about 1 h 20 min left"
    assert RS.rate_text(40.4, "page", "pages") == "40 pages a minute"
    assert RS.rate_text(2.5, "page", "pages") == "2.5 pages a minute"
    assert RS.rate_text(1.0, "genie", "genies") == "1 genie a minute"


# -- the estimate ---------------------------------------------------------------------------
def test_nothing_is_estimated_without_enough_data():
    """Both thresholds: enough items AND enough seconds."""
    c = Clock()
    st = _stats(c)
    st.begin_phase("check", 100)
    c.t += 1
    st.update(3)  # (3 items, 1 s: too soon)
    assert st.remaining() is None and st.rate_per_min() is None
    assert "left" not in st.time_text() and "a minute" not in st.time_text()
    c.t += 10
    st.update(2)  # (it never goes back: still 3)
    assert st.done == 3 and st.remaining() is not None
    st2 = _stats(Clock())
    st2.begin_phase("check", 100)
    st2._clock.t += 60
    st2.update(2)  # (a minute, but 2 items)
    assert st2.remaining() is None


def test_the_estimate_follows_the_rate_and_counts_down():
    c = Clock()
    st = _stats(c)
    st.begin_phase("check", 100)
    for k in range(1, 11):
        c.t += 2
        st.update(k)
    # 10 pages in 20 s: 30 a minute, 90 left = 180 s
    assert st.rate_per_min() == pytest.approx(30)
    assert st.remaining() == pytest.approx(180, rel=0.05)
    assert "about 3 min left" in st.time_text() and "30 pages a minute" in st.time_text()
    left = st.remaining()
    c.t += 4  # (no news: it counts down meanwhile)
    assert st.remaining() == pytest.approx(left - 4)


def test_one_slow_page_does_not_make_it_jump():
    """A page that takes 30 s instead of 2 s: the raw estimate goes up by 30 %; the shown one
    moves part of the way only."""
    c = Clock()
    st = _stats(c, smoothing_s=20)
    st.begin_phase("check", 100)
    for k in range(1, 21):
        c.t += 2
        st.update(k)
    before = st.remaining()  # (about 80 pages at 2 s: 160 s)
    c.t += 30
    st.update(21)
    raw = (100 - 21) * (c.t - 1000.0) / 21
    shown = st.remaining()
    assert raw > before * 1.3
    assert before - 30 < shown < raw
    assert abs(shown - before) < abs(raw - before) * 0.85
    # without smoothing it follows the rate at once
    c2 = Clock()
    st2 = _stats(c2, smoothing_s=0)
    st2.begin_phase("check", 100)
    for k in range(1, 21):
        c2.t += 2
        st2.update(k)
    c2.t += 30
    st2.update(21)
    assert st2.remaining() == pytest.approx(raw)


def test_parallel_helpers_count_over_wall_time():
    """4 helpers, each finishing a page every 4 s: 1 page a second. The estimate waits for a
    page from each helper (4) before it says anything."""
    c = Clock()
    st = _stats(c, workers=4, min_items=2, min_seconds=0)
    st.begin_phase("check", 120)
    c.t += 4
    st.update(3)
    assert st.remaining() is None  # (3 of the 4 helpers have finished one)
    st.update(4)
    assert st.remaining() == pytest.approx(116 * 4 / 4)
    for k in range(5, 41):
        c.t += 1
        st.update(k)
    assert st.rate_per_min() == pytest.approx(60, rel=0.1)
    assert st.remaining() == pytest.approx(80, rel=0.15)


def test_a_new_phase_starts_its_own_count():
    c = Clock()
    st = _stats(c, min_items=1, min_seconds=0)
    st.begin_phase("genies", 4)
    c.t += 10
    st.update(2)
    assert st.lines()[0] == "Saving the library genies: 2 of 4 done"
    assert "genies a minute" in st.lines()[1]
    st.begin_phase("pages", 50)
    assert st.done == 0 and st.remaining() is None
    assert st.lines()[0] == "Updating the pages: 0 of 50 done"
    st.begin_phase("pages", 50)  # (the same phase: the clock goes on)
    c.t += 5
    st.update(5)
    assert st.rate_per_min() == pytest.approx(60)
    assert st.lines()[1].startswith("Time: 15 s so far")  # (the run's time)


def test_the_counts_so_far():
    st = _stats(Clock())
    st.begin_phase("check", 9)
    assert len(st.lines()) == 2  # (no page record yet: no counts)
    st.add_records(
        [
            {"outcome": o}
            for o in (
                "updated",
                "updated",
                "current",
                "unchanged",
                "skipped_open",
                "open_in_builder",
                "failed",
                "deferred",
                "not_checked",
            )
        ]
    )
    assert st.counts == {"updated": 2, "unchanged": 2, "skipped": 2, "failed": 1}
    assert st.lines()[2] == "So far: 2 updated, 2 unchanged, 2 skipped, 1 failed"


def test_the_figures_can_be_turned_off(app):
    from ctgedit.ui.prefs.settings import app_settings, run_figures

    st = app_settings()
    assert run_figures() == (True, 3, 5, 10)
    st.setValue("opt/run_figures", False)
    st.setValue("opt/run_estimate_items", 7)
    st.setValue("opt/run_estimate_seconds", "garbled")
    st.setValue("opt/run_estimate_smoothing", 30)
    assert run_figures() == (False, 7, 5, 30)
    s = RS.RunStats.from_settings(clock=Clock())
    assert (s.show, s.min_items, s.min_seconds, s.smoothing_s) == (False, 7, 5, 30)
    s.begin_phase("check", 10)
    assert s.lines() == [] and s.short_text() == "" and s.total_text() == ""
    for k in (
        "opt/run_figures",
        "opt/run_estimate_items",
        "opt/run_estimate_seconds",
        "opt/run_estimate_smoothing",
    ):
        st.remove(k)


def test_total_text_is_the_whole_run():
    c = Clock()
    st = _stats(c)
    c.t += 200
    assert st.total_text() == "Time taken: 3 min 20 s."


# -- the records of the helpers --------------------------------------------------------------
def test_the_tail_reads_complete_lines_once(tmp_path):
    from ctgedit.ui.jobs.helper_job import RecordTail

    p = str(tmp_path / "a.records")
    tail = RecordTail()
    assert tail.take([p]) == []  # (not written yet)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"i": 0, "outcome": "updated"}) + "\n")
        fh.write('{"i": 1, "outco')  # (being written)
    assert [r["i"] for r in tail.take([p])] == [0]
    assert tail.take([p]) == []
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('me": "current"}\nnot json\n')
    assert [r["i"] for r in tail.take([p])] == [1]


def test_the_run_counts_the_records_of_every_helper(app, tmp_path):
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    files = [str(tmp_path / ("%d.records" % k)) for k in range(2)]
    from ctgedit.ui.jobs.helper_job import RecordTail

    c = Clock()
    run = UpdatePagesRun(None, [], {}, clock=c)
    tail = RecordTail()
    run._helper = types.SimpleNamespace(new_records=lambda: tail.take(files))
    run.stats.workers = 2
    run.stats.min_seconds = 0
    run.stats.begin_phase("check", 10)
    seen = []
    run.progress.connect(lambda i, n, name, phase: seen.append((i, phase, run.stats.done)))
    for k, outs in enumerate((("updated", "current"), ("failed", "deferred", "updated"))):
        with open(files[k], "w", encoding="utf-8") as fh:
            for j, o in enumerate(outs):
                fh.write(json.dumps({"i": k * 5 + j, "outcome": o}) + "\n")
    c.t += 10
    run._progressed(3, 10, "p3", "check")
    assert seen == [(3, "check", 5)]
    assert run.stats.counts == {"updated": 2, "unchanged": 1, "skipped": 0, "failed": 1}
    assert run.stats.rate_per_min() == pytest.approx(30)
    run._progressed(4, 10, "p4", "check")  # (nothing new: counted once)
    assert run.stats.done == 5


# -- CtDraw's cache notice ------------------------------------------------------------------
def _window(app, monkeypatch):
    from ctgedit.ui.main_window import MainWindow

    shown = []
    monkeypatch.setattr(MainWindow, "_show_cache_notice_box", lambda self, warn: shown.append(warn))
    w = MainWindow()
    return w, shown


def _close(w):
    from ctgedit.ui.main_window import MainWindow

    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.close()
    w.deleteLater()


def test_a_single_save_says_it_at_once_and_once(app, monkeypatch):
    w, shown = _window(app, monkeypatch)
    try:
        w._library_cache_notice("")
        assert shown == []
        w._library_cache_notice("restart it")
        w._library_cache_notice("restart it")
        assert shown == ["restart it"]  # (then the status bar only)
    finally:
        _close(w)


def test_a_held_notice_waits_for_the_outermost_run(app, monkeypatch):
    w, shown = _window(app, monkeypatch)
    try:
        with w._cache_notice_held():
            w._library_cache_notice("first")
            with w._cache_notice_held():
                w._library_cache_notice("second")
            assert shown == []
        assert shown == ["first"]
        with w._cache_notice_held():
            pass
        assert shown == ["first"]
    finally:
        _close(w)


def _update_pages(app, monkeypatch, tmp_path, stopped=False):
    """Tools > Update Pages with its chain (faked as test_update_pages_chain does): the chain
    saves a library genie and says CtDraw's warning. The events in order."""
    from ctgedit.io import genieusage as GU
    from ctgedit.io import pgregister
    from ctgedit.ui.dialog import dialogs

    pdir = tmp_path / "Proj"
    pdir.mkdir(exist_ok=True)
    events = []

    class _Dlg:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return QtWidgets.QDialog.Accepted

        def options(self):
            return dict(
                templates=True, genies=True, symbols=True, backup=True, page_properties=False, chain=True
            )

        def projects(self):
            return [str(pdir)]

        def chain_projects(self):
            return [str(pdir)]

        def processes(self):
            return 1

    monkeypatch.setattr(dialogs, "UpdatePagesDialog", _Dlg)
    monkeypatch.setattr(pgregister, "probe_writable", lambda d: None)
    b = GU.Ref("element", str(pdir / "chainlib.ctm"), "b", "Proj")
    plan = GU.UpdatePlan([GU.User(b, [], (), True, GU.OUT_OF_DATE)], [])
    monkeypatch.setattr(GU, "chain_plan", lambda dirs, **k: plan)

    def run_update(p, **k):
        events.append("chain saved")
        # (stopped: Cancel after the genie was saved, before the pages)
        return GU.UpdateUsersReport(
            elements_saved=["chainlib.b"],
            stopped=stopped,
            backup=k.get("backup"),
            save_warning="restart the Graphics Builder",
        )

    monkeypatch.setattr(GU, "run_update", run_update)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "information",
        lambda *a, **k: events.append("box: " + a[2]) or QtWidgets.QMessageBox.Ok,
    )
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Yes)
    w, shown = _window(app, monkeypatch)
    monkeypatch.setattr(type(w), "_show_cache_notice_box", lambda self, warn: events.append("notice"))
    # (the result window of the run: its lines, as the box showed them)
    monkeypatch.setattr(
        type(w),
        "_show_run_results",
        lambda self, title, lines, **k: events.append("box: " + "\n".join(lines)) or False,
    )

    def pages(job, parts=None):
        events.append("pages")
        return _fake_report()

    w._update_pages_in_worker = pages
    try:
        w._update_pages()
    finally:
        _close(w)
    return events


def test_update_pages_says_the_notice_after_the_whole_run(app, monkeypatch, tmp_path):
    events = _update_pages(app, monkeypatch, tmp_path)
    assert [e if not e.startswith("box: ") else "box" for e in events] == [
        "chain saved",
        "pages",
        "box",
        "notice",
    ]
    assert "Time taken: " in events[2]


def test_a_cancelled_chain_says_it_after_its_box(app, monkeypatch, tmp_path):
    events = _update_pages(app, monkeypatch, tmp_path, stopped=True)
    assert events[0] == "chain saved" and "pages" not in events
    assert events[1].startswith("box: ") and "Stopped before the pages" in events[1]
    assert events[2:] == ["notice"]


def test_the_chain_after_a_genie_save_says_it_at_its_end(app, monkeypatch):
    """_after_chain_saves (the end of the background chain) inside the chain's hold: the
    notice comes when the hold ends."""
    w, shown = _window(app, monkeypatch)
    try:
        with w._cache_notice_held():
            w._after_chain_saves({"save_warning": "restart", "libraries": []})
            assert shown == []
        assert shown == ["restart"]
    finally:
        _close(w)


# -- Update Users' pages step ---------------------------------------------------------------
def test_update_users_pages_step_reports_each_page_and_can_stop(monkeypatch, tmp_path):
    from ctgedit.io import genieusage as GU
    from ctgedit.io import updatepages as UP

    pages = [str(tmp_path / ("p%d.ctg" % k)) for k in range(3)]

    def fake_update_pages(dirs, progress=None, on_record=None, **k):
        rep = UP.UpdateReport()
        for i, p in enumerate(pages):
            if progress is not None and progress(i, len(pages), "p%d" % i) is False:
                break
            rec = UP.page_record(i, p, "updated", checked=1, stale=1)
            rep.add(rec)
            if on_record is not None:
                on_record(rec)
        return rep

    monkeypatch.setattr(UP, "update_pages", fake_update_pages)
    plan = GU.UpdatePlan(
        [], [GU.User(GU.Ref("page", p, "", "Proj"), [], (), True, GU.OUT_OF_DATE) for p in pages]
    )
    seen, recs = [], []
    rep = GU.run_update(plan, page_progress=lambda i, n, name: seen.append(i) or i < 2, on_record=recs.append)
    assert seen == [0, 1, 2] and len(recs) == 2
    assert rep.stopped and rep.pages.not_checked == 1
    rep = GU.run_update(plan)  # (no page_progress)
    assert not rep.stopped and len(rep.pages.updated) == 3


def test_a_helper_s_last_records_are_counted_after_it_ends(app, tmp_path):
    """The records a part wrote after the last poll are read before its files go: the next
    look still gets them (the pages done and the outcomes are not left short)."""
    from ctgedit.ui.jobs.helper_job import HelperJob, HelperJobs

    class Proc:
        code = None

        def poll(self):
            return self.code

    def job(k):
        j = HelperJob(None, lambda req, ans: [], {}, str(tmp_path), stream=True)
        base = str(tmp_path / ("part%d" % k))
        j._files = {"records_file": base + ".records", "progress_file": base + ".progress"}
        j._answer = base + ".answer.json"
        with open(j._answer, "w", encoding="utf-8") as fh:
            json.dump({"format": 1}, fh)
        j._paths = [j._answer] + list(j._files.values())
        j._proc, j._timer = Proc(), types.SimpleNamespace(stop=lambda: None)
        return j

    def write(j, *outs):
        with open(j._files["records_file"], "a", encoding="utf-8") as fh:
            for o in outs:
                fh.write(json.dumps({"i": 0, "outcome": o}) + "\n")

    jobs = HelperJobs(None, lambda req, ans: [], {}, str(tmp_path), 2, 10, lambda a: {})
    jobs._jobs = [job(0), job(1)]
    a, b = jobs._jobs
    write(a, "updated")
    write(b, "current")
    assert [r["outcome"] for r in jobs.new_records()] == ["updated", "current"]
    write(a, "failed", "updated")  # (after the last poll, then it ends)
    a._proc.code = 0
    finished = []
    a.finished.connect(finished.append)
    a._poll()
    assert finished and not os.path.exists(a._files["records_file"])
    assert len(finished[0]["streamed"]) == 3
    write(b, "updated")
    assert sorted(r["outcome"] for r in jobs.new_records()) == ["failed", "updated", "updated"]
    assert jobs.new_records() == []  # (each once)


def test_the_chain_s_status_note_reads_plainly():
    from ctgedit.ui.window.library import _with_figures

    head = "Updating the closed pages that use lib.x: 3 of 40 (Page)"
    assert _with_figures(head, "12 s so far, about 2 min left") == head + ". 12 s so far, about 2 min left."
    assert _with_figures(head, "") == head


def test_the_progress_window_keeps_its_size(app):
    """The figures, longer page names and the Stop text never resize the window."""
    from ctgedit.ui.progress_text import SteadyProgressLabel

    c = Clock()
    st = _stats(c, min_items=1, min_seconds=0)
    prog = QtWidgets.QProgressDialog("Finding the pages to check…", "Cancel", 0, 0)
    prog.setMinimumDuration(0)
    try:
        steady = SteadyProgressLabel(prog, st)
        steady.set(["Finding the pages to check…"])
        prog.show()
        app.processEvents()
        sizes = {(prog.width(), prog.height())}
        st.begin_phase("check", 120)
        for k in range(1, 120):
            c.t += 37
            st.update(k)
            if k > 50:
                st.add_records([{"outcome": "updated"}])
            steady.set(["Checking %s… (%d of 120)" % ("P" * (k % 90), k)] + st.lines())
            app.processEvents()
            sizes.add((prog.width(), prog.height()))
        steady.set(["Stopping after the pages being updated…"])
        app.processEvents()
        sizes.add((prog.width(), prog.height()))
        assert len(sizes) == 1, sizes
        assert len(steady.lines(["a"])) == 1 + st.LINES
    finally:
        prog.close()
        prog.deleteLater()


def test_the_status_note_grows_but_never_shrinks(app):
    from ctgedit.ui.progress_text import hold_width

    label = QtWidgets.QLabel("Updating: 1 of 9 (A). 59 s so far.")
    widest = [label.text() + " about 1 h 55 min left, 9999 pages a minute"]
    hold_width(label, widest)
    room = label.minimumWidth()
    assert room >= label.fontMetrics().horizontalAdvance(widest[0])
    for text in (
        "Updating: 2 of 9 (A). 1 min so far.",
        "Updating: 3 of 9 (A). 1 min 1 s so far, about 40 s left, 12 pages a minute.",
    ):
        label.setText(text)
        hold_width(label, widest)
        assert label.minimumWidth() == room  # (it had room for these)
    label.setText("Updating: 4 of 9 (%s)." % ("W" * 200))
    hold_width(label, widest)
    assert label.minimumWidth() > room  # (a longer name: it grows, once)


def test_step_text_names_the_step():
    c = Clock()
    st = _stats(c, min_items=1, min_seconds=0)
    st.begin_phase("compose", 40)
    assert st.step_text() == "updating composite genies"
    c.t += 10
    st.update(10)
    assert st.step_text() == "updating composite genies, about 30 s left"
    st.show = False
    assert st.step_text() == "updating composite genies"


def test_the_task_names_its_step_and_stops_estimating_on_cancel(app, monkeypatch):
    """The status bar task says the step whose time left it gives; after Cancel it says it
    is stopping, with no estimate."""
    from PySide6 import QtCore
    from ctgedit.ui.jobs import tasks, update_run

    c = Clock()
    said = []
    monkeypatch.setattr(tasks, "progress", lambda tok, i, n, text=None: said.append(text))

    class Run(QtCore.QObject):
        progress = QtCore.Signal(int, int, str, str)
        finished = QtCore.Signal(dict)
        active = None

        def __init__(self, parent, dirs, job, skip_paths=None, parts=None):
            super().__init__(parent)
            self.stats = _stats(c, min_items=1, min_seconds=0)
            self.done = self.stopping = False
            self._win = parent

        def stop(self):
            self.stopping = True

        def start(self):
            self.stats.begin_phase("check", 100)
            c.t += 10
            self.stats.update(10)
            self.progress.emit(9, 100, "p9", "check")
            prog = self._win.findChildren(QtWidgets.QProgressDialog)[-1]
            prog.findChild(QtWidgets.QPushButton).click()  # (Cancel)
            c.t += 10
            self.stats.update(20)
            self.progress.emit(19, 100, "p19", "check")
            self.done = True
            self.finished.emit({"stopped": True})
            return True

    monkeypatch.setattr(update_run, "UpdatePagesRun", Run)
    w, _shown = _window(app, monkeypatch)
    try:
        rep = w._update_pages_in_worker({"dirs": []})
    finally:
        _close(w)
    assert rep.stopped
    assert said[0] == "Update Pages: checking the pages, about 2 min left…"
    assert said[1:] == ["Stopping Update Pages…"]


# -- one run at a time, and the notice of every window ---------------------------------------
def test_update_users_waits_for_update_pages(app, monkeypatch):
    """Update Users while Update Pages (or its chain) runs in another window: refused, as
    with the chain after a genie save."""
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    told = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information", lambda *a, **k: told.append(a[2]) or QtWidgets.QMessageBox.Ok
    )
    monkeypatch.setattr(GU, "update_plan", lambda usage: pytest.fail("it ran"))
    usage = types.SimpleNamespace(target=types.SimpleNamespace(label="lib.a"))
    w, _shown = _window(app, monkeypatch)
    try:
        monkeypatch.setattr(UpdatePagesRun, "chain_busy", True)
        assert w._update_element_users(usage, confirm=False) is None
        monkeypatch.setattr(UpdatePagesRun, "chain_busy", False)
        monkeypatch.setattr(UpdatePagesRun, "active", object())
        assert w._update_element_users(usage, confirm=False) is None
    finally:
        _close(w)
    assert len(told) == 2 and all("Update Pages is running" in t for t in told)


def test_a_save_in_another_window_waits_for_the_run(app, monkeypatch):
    """The hold is the editor's: window B's save during window A's run says the notice at
    the run's end, once; a later save says it on the status bar only."""
    from ctgedit.ui.main_window import MainWindow

    a, shown = _window(app, monkeypatch)
    b = MainWindow()
    try:
        with a._cache_notice_held():
            b._library_cache_notice("restart it")
            assert shown == []
        assert shown == ["restart it"]
        b._library_cache_notice("restart it")
        assert shown == ["restart it"]
    finally:
        _close(a)
        _close(b)


def test_update_users_figures_and_pages_opened_meanwhile(app, monkeypatch, tmp_path):
    """The pages step's first line counts the pages (not 'Working', not the last genie as
    not done), and a page opened in another window during the step is left."""
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.progress_text import SteadyProgressLabel

    pages = [str(tmp_path / ("p%d.ctg" % k)) for k in range(3)]
    plan = GU.UpdatePlan(
        [
            GU.User(
                GU.Ref("element", str(tmp_path / "lib.ctm"), "g%d" % k, "Proj"), [], (), True, GU.OUT_OF_DATE
            )
            for k in range(2)
        ],
        [GU.User(GU.Ref("page", p, "", "Proj"), [], (), True, GU.OUT_OF_DATE) for p in pages],
    )
    monkeypatch.setattr(GU, "update_plan", lambda usage: plan)
    shown_lines = []
    real_set = SteadyProgressLabel.set
    monkeypatch.setattr(
        SteadyProgressLabel,
        "set",
        lambda self, lines: shown_lines.append(list(lines)) or real_set(self, lines),
    )
    left = []

    def run_update(p, progress=None, page_progress=None, skip_now=None, **k):
        progress(0, 3, "lib.g0")
        progress(1, 3, "lib.g1")
        progress(2, 3, "pages")
        left.append([skip_now(x) for x in pages])
        return GU.UpdateUsersReport()

    monkeypatch.setattr(GU, "run_update", run_update)
    w, _shown = _window(app, monkeypatch)
    monkeypatch.setattr(type(w), "_report_users_update", lambda self, text, **k: None)
    # (p1 opened in another window during the pages step)
    w._open_page_paths = lambda: [pages[1]]
    usage = types.SimpleNamespace(target=types.SimpleNamespace(label="lib.a"))
    try:
        w._update_element_users(usage, confirm=False)
    finally:
        _close(w)
    pages_step = [x for x in shown_lines if x and x[0] == "Updating the pages…"]
    assert pages_step and pages_step[0][1] == "Updating the pages: 0 of 3 done"
    assert left == [[False, True, False]]
