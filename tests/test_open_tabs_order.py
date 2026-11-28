"""What the user looks at updates first, one step per save."""

from ctgedit.ui.main_window import MainWindow
import os
import sys
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtWidgets  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_save_chain_follow2 import _quiet_chain, win  # noqa: E402,F401
from test_element_nested_update import _lib  # noqa: E402
from test_save_chain_jobs import _worker_here  # noqa: E402
from test_update_pages_worker import OPTS, open_tab  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _one_helper():
    """One helper unless a test says otherwise: the order of the updates is the order they
    start in it (with a pool the first ones start together, in the same order)."""
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/genie_helpers", 1)
    yield
    app_settings().remove("opt/genie_helpers")


def _helpers(n: int) -> None:
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/genie_helpers", n)


def _name(doc) -> str:
    return getattr(doc, "display_name", "") or os.path.splitext(os.path.basename(doc.path))[0]


class _SlowHelper:
    """The window's helpers, slowed as on large pages (2 to 6 s a request): a job starts
    (recorded with its helper) and runs until :meth:`finish`."""

    def __init__(self, monkeypatch):
        from ctgedit.ui.jobs import genie_jobs as gj

        self.started = []
        monkeypatch.setattr(gj, "_in_event_loop", lambda: True)

        def start(job, worker):
            job.attempts += 1
            job.worker, job.env = worker, gj.environment_signature()
            self.started.append(job)

        monkeypatch.setattr(gj.GenieJob, "start", start)
        monkeypatch.setattr(gj.GenieJob, "done", lambda job: False)

    def names(self) -> list:
        return [_name(j.doc) for j in self.started]

    @staticmethod
    def finish(jobs, doc=None):
        """The running job (of ``doc``) ended: the next ones start."""
        from ctgedit.ui.jobs import tasks

        key = id(doc) if doc is not None else next(iter(jobs._jobs))
        job = jobs._jobs.pop(key)
        tasks.drop(job.task)
        jobs._start_more()
        return job

    def run_all(self, jobs) -> list:
        while jobs._jobs:
            self.finish(jobs)
        return self.names()


def _open(win, fx, pages=(), elements=()):
    for p in pages:
        win.open_document(fx.page(p))
    for lib, el in elements:
        win._open_element(fx.file("FxLib", lib), el)


def _save_n0_in(win, fx):
    """n0 opened in a tab of ``win`` (shown), its text changed and saved; the save brings the
    open tabs up to date (files._propagate_element)."""
    from ctgedit.editing import libraries_changed

    win._open_element(_lib(fx), "n0")
    d = win.doc
    i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    assert d.set_text(i, "%Label% CHANGED")
    d.save()
    libraries_changed()
    return d


def _tab(win, name):
    return next(k for k, e in enumerate(win._docs) if _name(e["doc"]) == name)


# -- the order: what the user looks at, the nearest, the small ------------------------------
def test_the_row_genie_goes_before_the_pages_opened_before_it(fx_install, win, monkeypatch):
    """The pages FxParts (places fx_row, which nests n1 holding n0), FxGenies and FxMembers
    were opened before the row genie, then n0 is saved: the row's update starts first, then the
    pages, smaller ones first."""
    _open(win, fx_install, ["FxParts", "FxGenies", "FxMembers"], [("fxparts.ctm", "fx_row")])
    helper = _SlowHelper(monkeypatch)
    d = _save_n0_in(win, fx_install)
    jobs = win._genie_job_runner()
    win._propagate_element(d)
    assert helper.names() == ["fxparts.fx_row"], "the row's update first, and alone"
    assert helper.run_all(jobs) == ["fxparts.fx_row", "FxMembers", "FxGenies", "FxParts"]


def test_the_element_tabs_nearest_the_saved_genie_go_first(fx_install, win, monkeypatch):
    """n4 holds n3 holding n2 holding n1 holding n0, opened in the order n4, n2, n1: after n0's
    save n1 (which nests it directly) goes first, then n2, then n4."""
    _open(win, fx_install, ["FxGenies"], [("fxnest.ctm", "n4"), ("fxnest.ctm", "n2"), ("fxnest.ctm", "n1")])
    helper = _SlowHelper(monkeypatch)
    d = _save_n0_in(win, fx_install)
    win._propagate_element(d)
    order = helper.run_all(win._genie_job_runner())
    assert order == ["fxnest.n1", "fxnest.n2", "fxnest.n4", "FxGenies"]
    assert [j.rank[1] for j in helper.started[:3]] == [1, 2, 4], "how deep each nests n0"


@pytest.mark.parametrize("n", [1, 2])
def test_a_tab_shown_while_its_update_waits_goes_first(fx_install, win, monkeypatch, n):
    """After the save the user shows the largest page, then goes back: its update goes next,
    before the smaller pages'. With two helpers the row genie and the smallest page start
    together, and the page shown takes the first helper that is free."""
    from ctgedit.infra import workers

    _helpers(n)
    monkeypatch.setattr(workers, "free_memory_mb", lambda: 8000)
    _open(win, fx_install, ["FxMembers", "FxGenies", "FxParts"], [("fxparts.ctm", "fx_row")])
    helper = _SlowHelper(monkeypatch)
    d = _save_n0_in(win, fx_install)
    jobs = win._genie_job_runner()
    win._propagate_element(d)
    first = ["fxparts.fx_row"] + (["FxMembers"] if n == 2 else [])
    assert helper.names() == first
    win.show_tab(_tab(win, "FxParts"))
    assert helper.names() == first, "every helper is busy: it waits"
    win.show_tab(_tab(win, "fxnest.n0"))
    assert win.doc is d
    helper.finish(jobs, helper.started[0].doc)
    assert helper.names()[len(first)] == "FxParts", "the tab the user showed"
    order = helper.run_all(jobs)
    assert order == ["fxparts.fx_row"] + (
        ["FxMembers", "FxParts", "FxGenies"] if n == 2 else ["FxParts", "FxMembers", "FxGenies"]
    )


def test_a_merged_update_of_the_tab_shown_goes_first(fx_install, win, monkeypatch):
    """A page's update queued, then asked again for the tab shown (first=True): the merged job
    goes first."""
    from ctgedit.editing import EditableDocument

    helper = _SlowHelper(monkeypatch)
    jobs = win._genie_job_runner()
    busy, small, big = (
        EditableDocument.open(fx_install.page(p)) for p in ("FxGenies", "FxMembers", "FxParts")
    )
    ch = {("fxnest", "n4"): None}
    jobs.submit(busy, ch, ["fxnest.n4"])
    jobs.submit(small, ch, ["fxnest.n4"])
    jobs.submit(big, ch, ["fxnest.n4"])
    jobs.submit(big, {("fxnest", "n2"): None}, ["fxnest.n2"], first=True)
    assert len(jobs._queue) == 2, "merged"
    helper.finish(jobs)
    assert helper.names() == ["FxGenies", "FxParts"]
    jobs.shutdown()


def test_in_tab_order_when_set(fx_install, win, monkeypatch):
    """Settings 'After a genie save, update the open tabs: In tab order': the tabs left to
    right (the tab shown first all the same)."""
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    rows = {r.key: r for r in SettingsDialog.ROWS}
    assert rows["opt/genie_jobs_order"].section == "When a page opens"
    assert [v for v, _t in rows["opt/genie_jobs_order"].choices] == ["near", "tabs"]
    assert rows["opt/genie_helpers"].kind == "int" and rows["opt/genie_helpers"].span == (1, 8)
    app_settings().setValue("opt/genie_jobs_order", "tabs")
    try:
        _open(win, fx_install, ["FxParts", "FxGenies", "FxMembers"], [("fxparts.ctm", "fx_row")])
        helper = _SlowHelper(monkeypatch)
        d = _save_n0_in(win, fx_install)
        win._propagate_element(d)
        assert helper.run_all(win._genie_job_runner()) == [
            "FxParts",
            "FxGenies",
            "FxMembers",
            "fxparts.fx_row",
        ]
    finally:
        app_settings().remove("opt/genie_jobs_order")


@pytest.mark.parametrize("case", ["3", "1", "short of memory"])
def test_the_open_tabs_are_updated_together_in_a_pool_of_helpers(fx_install, win, monkeypatch, case):
    """Open tabs update together in a pool of helpers."""
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.infra import workers

    helper = _SlowHelper(monkeypatch)
    _helpers(1 if case == "1" else 3)
    monkeypatch.setattr(workers, "free_memory_mb", lambda: 100 if case == "short of memory" else 8000)
    jobs = win._genie_job_runner()
    page = EditableDocument.open(fx_install.page("FxParts"))
    row = ElementDocument.open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_row")
    other, last = (EditableDocument.open(fx_install.page(p)) for p in ("FxGenies", "FxMembers"))
    try:
        jobs.submit(page, {("fxparts", "fx_row"): None}, ["fxparts.fx_row"])
        jobs.submit(row, {("fxnest", "n1"): None}, ["fxnest.n1"])
        jobs.submit(other, {("fxnest", "n4"): None}, ["fxnest.n4"])
        jobs.submit(last, {("fxnest", "n4"): None}, ["fxnest.n4"])
        if case == "3":
            assert helper.names() == ["FxParts", "fxparts.fx_row", "FxGenies"]
            workers_used = [j.worker for j in helper.started]
            assert workers_used[0] is jobs.worker and len(set(map(id, workers_used))) == 3
            assert jobs.helpers() == [jobs.worker] + jobs.extra and len(jobs.extra) == 2
            helper.finish(jobs, row)
            assert helper.names()[-1] == "FxMembers"
            assert helper.started[-1].worker is helper.started[1].worker, "the one now free"
        else:
            assert helper.names() == ["FxParts"] and not jobs.extra
            helper.finish(jobs, page)
            assert helper.names() == ["FxParts", "fxparts.fx_row"]
            assert helper.started[1].worker is jobs.worker
    finally:
        jobs.shutdown()


def test_the_save_queues_every_tab_before_the_first_update_starts(fx_install, win, monkeypatch):
    from ctgedit.editing import EditableDocument, ElementDocument

    helper = _SlowHelper(monkeypatch)
    jobs = win._genie_job_runner()
    page = EditableDocument.open(fx_install.page("FxParts"))
    row = ElementDocument.open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_row")
    with jobs.hold():
        jobs.submit(page, {("fxparts", "fx_row"): None}, ["fxparts.fx_row"])
        assert helper.started == [] and len(jobs._queue) == 1
        jobs.submit(row, {("fxnest", "n1"): None}, ["fxnest.n1"])
    assert helper.names()[0] == "fxparts.fx_row"
    jobs.shutdown()


# -- one step per save ---------------------------------------------------------------------
def _labels(doc) -> list:
    return [c.label for c in doc._undo]


def test_the_runs_end_update_of_a_page_that_took_the_save_is_part_of_its_step(fx_install, win):
    """n0 saved with Settings 'always': FxGenies (places n4, which nests n0) takes the change
    at the save, then the chain saves n1 to n4 again and at its end the page takes n4's new
    stamp. One undo step, one entry in the bar, the stamps current."""
    from ctgedit.ui.prefs.settings import app_settings

    win.open_document(fx_install.page("FxGenies"))
    p = win.doc
    app_settings().setValue("opt/genie_save_pages", "always")
    win._open_element(_lib(fx_install), "n0")
    d = win.doc
    i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    assert d.set_text(i, "%Label% CHANGED")
    assert win._save_element_to()
    assert _labels(p) == ["update genies from library"]
    assert [w for w, _c in p.ui.auto_changes] == ["fxnest.n0 updated from its library"]
    assert p.genie_changes() == {}, "the stamps the chain wrote"
    assert win._only_auto_changes(p) and p.ui.auto_count == 2
    step = p._undo[-1]
    win.show_tab(_tab(win, "FxGenies"))
    win.undo()
    assert not p._undo and not p.dirty, "one Undo takes the save's update back"
    win.redo()
    assert p._undo == [step] and p.genie_changes() == {}


def test_the_runs_end_update_says_nothing_while_it_runs(fx_install, win, monkeypatch, tmp_path):
    """The run's end update says nothing while it runs."""
    from ctgedit.editing import ElementDocument, libraries_changed

    win.open_document(fx_install.page("FxGenies"))
    p = win.doc
    _worker_here(monkeypatch, tmp_path, by_hand=True)
    jobs = win._genie_job_runner()
    d = ElementDocument.open_element(_lib(fx_install), "n0")
    i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    assert d.set_text(i, "%Label% CHANGED")
    d.save()
    libraries_changed()
    win._propagate_element(d)
    assert p.ui.genies_updating, "the save's update marks what changes"
    jobs._poll()
    assert _labels(p) == ["update genies from library"] and p.genie_changes()
    save_step = p._undo[-1]
    lines = []
    win.statusBar().messageChanged.connect(lines.append)
    assert win._start_users_run(_lib(fx_install), "n0", "fxnest.n0")
    assert not p.ui.genies_updating and not win._genies_updating(p), "nothing in amber"
    assert not any(x.startswith("Updating ") for x in lines), lines
    follow = [j for j in jobs._jobs.values() if j.doc is p]
    assert follow and getattr(follow[0], "why", "") == "follow" and follow[0].quiet
    jobs._poll()
    assert p._undo == [save_step], "part of the save's step"
    assert [w for w, _c in p.ui.auto_changes] == ["fxnest.n0 updated from its library"]
    assert p.genie_changes() == {}
    assert "Updating " not in win.statusBar().currentMessage()


def test_an_update_after_a_step_of_the_users_is_its_own_step(fx_install, win):
    """A move of the user's after the save's update: the run's end update is its own
    automatic step (the user's step is not folded into anything)."""
    from ctgedit.ui.prefs.settings import app_settings

    win.open_document(fx_install.page("FxGenies"))
    p = win.doc
    app_settings().setValue("opt/genie_save_pages", "never")
    win._open_element(_lib(fx_install), "n0")
    d = win.doc
    i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    assert d.set_text(i, "%Label% CHANGED")
    assert win._save_element_to()
    assert len(p._undo) == 1 and getattr(p._undo[-1], "genie_save_step", False)
    p.move_objects([list(p.page.roots())[0]], 4, 0)
    mine = p._undo[-1]
    assert win._start_users_run(_lib(fx_install), "n0", "fxnest.n0")
    assert len(p._undo) == 3 and p._undo[1] is mine
    assert p._undo[-1].automatic and not mine.automatic


# -- the bar names a change once -----------------------------------------------------------
def test_a_change_named_twice_is_listed_once(fx_install, win, monkeypatch):
    """The open updates the row genie, a save updates a sub genie and the row again: the bar and
    the close question name each once; every step stays for the bar's Undo."""
    from PySide6 import QtWidgets as W

    win.open_document(fx_install.page("FxShapes"))
    p = win.doc
    roots = list(p.page.roots())
    row, sub = "fxparts.fx_row updated from its library", "fxnest.n1 updated from its library"
    steps = []
    for what in (row, sub, row):
        p.move_objects([roots[0]], 1, 0)
        steps.append(p._undo[-1])
        win._note_auto_change(p, what, steps[-1])
    assert len(p._undo) == 3 and p.ui.auto_count == 3
    bar = [n for n in win._auto_bar.notices_for(p) if n.key == "auto"]
    assert bar[0].text == "Updated automatically: %s; %s." % (row, sub)
    asked = []

    class Box:
        Question = W.QMessageBox.Question
        Save, Discard, Cancel = (W.QMessageBox.Save, W.QMessageBox.Discard, W.QMessageBox.Cancel)

        def __init__(self, _icon, _title, text, *_a):
            asked.append(text)

        def setDefaultButton(self, _b):
            pass

        def exec(self):
            return W.QMessageBox.Cancel

    monkeypatch.setattr(W, "QMessageBox", Box)
    win._ask_save_on_close(p, [])
    monkeypatch.undo()
    assert asked and asked[0].count(row) == 1 and sub in asked[0]
    # (the bar's Undo takes all three back)
    win._undo_auto_changes()
    assert not p._undo, "every automatic step undone"


def test_a_folded_update_counts_as_an_automatic_change(fx_install, win):
    """An update at the end of a run that became part of the save's step still counts as an
    automatic change of the page (Update Pages tells a page updated in its tab by one: it
    counted the bar's entries)."""
    from ctgedit.ui.jobs.genie_jobs import GenieJob

    win.open_document(fx_install.page("FxShapes"))
    p = win.doc
    r = list(p.page.roots())[0]
    p.move_objects([r], 1, 0)
    save_step = p._undo[-1]
    job = GenieJob(p, {("fxnest", "n0"): None}, ["fxnest.n0"], why="save")
    win._genie_job_finished(p, ["fxnest.n0"], 1, job, stepped=True)
    assert save_step.genie_save_step and p.ui.auto_count == 1
    marks = win._open_tab_marks()
    p.move_objects([r], 0, 1)
    follow = GenieJob(p, {("fxnest", "n4"): None}, ["fxnest.n4"], why="follow")
    follow.below = save_step
    win._genie_job_finished(p, ["fxnest.n4"], 1, follow, stepped=True)
    assert p._undo == [save_step] and len(p.ui.auto_changes) == 1
    assert p.ui.auto_count > marks[id(p)][1]


def test_fold_last_step_keeps_the_first_step(fx_install):
    from ctgedit.editing import EditableDocument

    p = EditableDocument.open(fx_install.page("FxShapes"))
    r = list(p.page.roots())[0]
    before = p.serialize()
    p.move_objects([r], 2, 0)
    first = p._undo[-1]
    p.move_objects([r], 0, 2)
    after = p.serialize()
    assert p.fold_last_step() and p._undo == [first]
    p.undo()
    assert p.serialize() == before and not p.dirty
    p.redo()
    assert p.serialize() == after
    assert not p.fold_last_step(), "one step only"
    p.mark_saved()
    p.move_objects([r], 1, 1)
    assert not p.fold_last_step(), "the saved state lies between them"


def test_simulate_takes_a_folded_update_as_the_editors_own(fx_install, win):
    """A simulation runs the page after it took the save; the end of the run's update becomes
    part of that step: the Simulate window counts it as the editor's own update (the note
    offers Reload Page), not as an edit of the user's that reloads the page and its values.
    An Undo and Redo of the user's still counts as theirs."""
    import types
    from ctgedit.ui.jobs.genie_jobs import GenieJob
    from ctgedit.ui.simulate import window as SW

    win.open_document(fx_install.page("FxShapes"))
    p = win.doc
    r = list(p.page.roots())[0]
    p.move_objects([r], 1, 0)
    save_step = p._undo[-1]
    win._genie_job_finished(
        p, ["fxnest.n0"], 1, GenieJob(p, {("fxnest", "n0"): None}, ["fxnest.n0"], why="save"), stepped=True
    )
    sp = types.SimpleNamespace(doc=p, doc_steps=SW._doc_steps(p))
    p.move_objects([r], 0, 1)
    follow = GenieJob(p, {("fxnest", "n4"): None}, ["fxnest.n4"], why="follow")
    follow.below = save_step
    win._genie_job_finished(p, ["fxnest.n4"], 1, follow, stepped=True)
    assert p._undo == [save_step] and SW._auto_only(sp)
    sp.doc_steps = SW._doc_steps(p)
    p.undo()
    p.redo()
    assert not SW._auto_only(sp), "the user's Undo and Redo"


def test_update_pages_tells_a_page_whose_update_became_part_of_the_step_before(open_tab):
    """The page took a save (its step), Update Pages starts, the end of the save's run
    updates the page again and that becomes part of the step: Update Pages' step for the open
    pages says the page was updated in its tab by the automatic update."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.ui.jobs import genie_jobs
    from ctgedit.ui.jobs.genie_jobs import GenieJob

    w, proj, d1, _d2 = open_tab

    def land(why):
        below = genie_jobs.undo_top(d1)
        ans = GW.compute(d1.path, d1.serialize(), d1.genie_changes())
        n = genie_jobs.apply_answer(d1, ans)
        assert n and n > 0
        job = GenieJob(d1, {}, ["tablib.g1"], why=why)
        job.below = below
        w._genie_job_finished(d1, ["tablib.g1"], n, job, stepped=True)

    land("save")
    marks = w._open_tab_marks()
    e = ElementDocument.open_element(os.path.join(proj, "tablib.ctm"), "g1")
    e.move_objects([list(e.page.roots())[0]], 0, 5)
    e.save()
    libraries_changed()
    land("follow")
    assert len(d1.ui.auto_changes) == 1 and len(d1._undo) == 1, "one step"
    tabs = w._update_open_documents([proj], OPTS, marks=marks)
    # (only the automatic update changed it: Update Pages saves it in its tab)
    assert tabs.saved == [d1.path] and not tabs.auto and not d1.dirty


# -- what the run's end says ---------------------------------------------------------------
def test_the_runs_end_update_after_a_move_of_the_users_says_nothing(fx_install, win, monkeypatch, tmp_path):
    """The run's end update after a user move says nothing."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.ui.editor.notices import auto_change_names

    win.open_document(fx_install.page("FxGenies"))
    p = win.doc
    _worker_here(monkeypatch, tmp_path, by_hand=True)
    jobs = win._genie_job_runner()
    d = ElementDocument.open_element(_lib(fx_install), "n0")
    i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    assert d.set_text(i, "%Label% CHANGED")
    d.save()
    libraries_changed()
    win._propagate_element(d)
    jobs._poll()
    save_step = p._undo[-1]
    assert save_step.genie_save_step
    p.move_objects([list(p.page.roots())[0]], 4, 0)
    mine = p._undo[-1]
    lines = []
    win.statusBar().messageChanged.connect(lines.append)
    assert win._start_users_run(_lib(fx_install), "n0", "fxnest.n0")
    assert not p.ui.genies_updating and not win._genies_updating(p), "nothing in amber"
    assert not any(x.startswith("Updating ") for x in lines), lines
    jobs._poll()
    assert p._undo[:2] == [save_step, mine] and len(p._undo) == 3, _labels(p)
    assert p._undo[-1].automatic and not mine.automatic
    assert auto_change_names(p.ui.auto_changes) == ["fxnest.n0 updated from its library"]
    assert p.genie_changes() == {}, "the stamps the run wrote"
    assert "Updating " not in win.statusBar().currentMessage()


def _answer_file(tmp_path, name) -> str:
    """A worker's answer that changes nothing (the job lands with 0 ranges)."""
    import json
    from ctgedit.workers import genie_worker as GW

    path = str(tmp_path / ("%s.answer.json" % name))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"format": GW.FORMAT, "ops": [], "link_table": None}, fh)
    return path


def test_a_quiet_update_never_says_how_it_ended(fx_install, win, monkeypatch, tmp_path):
    """The run's end update of a page in another tab that took the save is quiet even when it runs
    longer than Long jobs' report time; merged into a save's update its task says how it ended."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.jobs import genie_jobs as gj
    from ctgedit.ui import notify as NT
    from ctgedit.ui.jobs import tasks

    helper = _SlowHelper(monkeypatch)
    jobs = win._genie_job_runner()
    win.open_document(fx_install.page("FxShapes"))
    told = []
    monkeypatch.setattr(NT, "notify", lambda _p, text, *a, **k: told.append(text))
    monkeypatch.setattr(gj.GenieJob, "done", lambda j: bool(j.ans) and os.path.exists(j.ans))
    docs = {}
    for name, why, quiet in (("FxGenies", "follow", True), ("FxMembers", "save", False)):
        docs[name] = EditableDocument.open(fx_install.page(name))
        jobs.submit(docs[name], {("fxnest", "n4"): None}, ["fxnest.n4"], why=why, quiet=quiet)
        job = helper.started[-1]
        assert tasks.registry().tasks[job.task].quiet is quiet
        tasks.registry().tasks[job.task].started -= 60  # (it ran long)
        job.ans = _answer_file(tmp_path, name)
        jobs._poll()
    assert told == ["Updating fxnest.n4 on FxMembers: done."], told
    # (a quiet job merged into a save's update: its task says how it ended again)
    page = EditableDocument.open(fx_install.page("FxParts"))
    other = EditableDocument.open(fx_install.page("FxGenies"))
    jobs.submit(other, {("fxnest", "n4"): None}, ["fxnest.n4"], why="save")
    jobs.submit(page, {("fxparts", "fx_row"): None}, ["fxparts.fx_row"], why="follow", quiet=True)
    queued = next(j for j in jobs._queue if j.doc is page)
    assert tasks.registry().tasks[queued.task].quiet
    jobs.submit(page, {("fxnest", "n1"): None}, ["fxnest.n1"], why="save")
    assert not queued.quiet and not tasks.registry().tasks[queued.task].quiet
    jobs.shutdown()


def test_the_runs_end_update_merged_into_the_saves_is_named_as_it(fx_install, win, monkeypatch):
    """The background update ends before a page's update of the save starts: the run's end update
    becomes part of it and is named as the save's update (not as fxnest.n4, which the run saved)."""
    from ctgedit.editing import EditableDocument

    helper = _SlowHelper(monkeypatch)
    jobs = win._genie_job_runner()
    busy, page = (EditableDocument.open(fx_install.page(p)) for p in ("FxParts", "FxGenies"))
    jobs.submit(busy, {("fxparts", "fx_row"): None}, ["fxparts.fx_row"], why="save")
    jobs.submit(page, {("fxnest", "n4"): [("fxnest", "n0")]}, ["fxnest.n0"], why="save")
    assert helper.names() == ["FxParts"]
    queued = next(j for j in jobs._queue if j.doc is page)
    jobs.submit(page, {("fxnest", "n4"): None}, ["fxnest.n4"], why="follow", quiet=True)
    assert jobs._queue == [queued] and queued.why == "save" and not queued.quiet
    assert queued.changes == {("fxnest", "n4"): None}, "composed as the run left n4"
    assert queued.names == ["fxnest.n0"], "named as the save's update"
    jobs.shutdown()


@pytest.mark.parametrize("second", [True, False])
def test_a_dropped_update_keeps_its_helper_busy(fx_install, win, monkeypatch, second):
    # (``second``: a pool of two helpers, else one)
    """A dropped update keeps its helper busy."""
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import workers
    from ctgedit.ui.jobs import genie_jobs as gj
    from ctgedit.ui.prefs.settings import app_settings

    helper = _SlowHelper(monkeypatch)
    answered = set()
    monkeypatch.setattr(gj.WarmWorker, "alive", lambda self: True)
    monkeypatch.setattr(gj.GenieJob, "done", lambda j: id(j) in answered)
    monkeypatch.setattr(workers, "free_memory_mb", lambda: 8000)
    ended = []
    monkeypatch.setattr(gj.WarmWorker, "kill_now", lambda self: ended.append(self))
    app_settings().setValue("opt/genie_helpers", 2 if second else 1)
    jobs = win._genie_job_runner()
    try:
        gone, nxt = (EditableDocument.open(fx_install.page(p)) for p in ("FxGenies", "FxParts"))
        jobs.submit(gone, {("fxnest", "n4"): None}, ["fxnest.n4"])
        dropped = helper.started[-1]
        dropped.ans = "x.answer.json"
        app_settings().setValue("opt/genie_helpers", 1)  # (it queues behind the first)
        jobs.submit(nxt, {("fxparts", "fx_row"): None}, ["fxparts.fx_row"])
        assert helper.names() == ["FxGenies"]
        app_settings().setValue("opt/genie_helpers", 2 if second else 1)
        jobs.cancel(gone)
        jobs._poll()  # (the window's timer)
        if second:
            assert helper.names() == ["FxGenies", "FxParts"]
            assert helper.started[-1].worker is jobs.extra[0] is not jobs.worker
            assert ended == [] and dropped in jobs._dead, "a free helper first"
        else:
            # (its helper computes only the dropped request: ended, and it takes the next)
            assert helper.names() == ["FxGenies", "FxParts"]
            assert ended == [jobs.worker] and helper.started[-1].worker is jobs.worker
            assert not jobs._dead
    finally:
        jobs.shutdown()


def test_tabs_read_again_one_after_another_start_nothing_in_between(fx_install, win, monkeypatch):
    """The background update's end reads the element tabs it saved again one after another,
    each dropping its update: what starts next starts once they are all read (the next turn of the
    event loop): the page's update."""
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.infra import workers
    from ctgedit.ui.jobs import genie_jobs as gj

    helper = _SlowHelper(monkeypatch)
    monkeypatch.setattr(gj.WarmWorker, "alive", lambda self: True)
    monkeypatch.setattr(workers, "free_memory_mb", lambda: 8000)
    jobs = win._genie_job_runner()
    lib = _lib(fx_install)
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/genie_helpers", 1)  # (the three queue in one)
    try:
        n2, n3 = (ElementDocument.open_element(lib, n) for n in ("n2", "n3"))
        page = EditableDocument.open(fx_install.page("FxGenies"))
        for d in (n2, n3, page):
            jobs.submit(d, {("fxnest", "n1"): None}, ["fxnest.n1"])
        assert helper.names() == ["fxnest.n2"]
        helper.started[-1].ans = "x.answer.json"
        jobs.cancel_automatic(n2)
        jobs.cancel_automatic(n3)
        assert helper.names() == ["fxnest.n2"], "nothing started in between"
        app_settings().setValue("opt/genie_helpers", 2)
        QtWidgets.QApplication.processEvents()
        assert helper.names() == ["fxnest.n2", "FxGenies"]
        # (a new helper while the pool has room: the window's computes n2's dropped update)
        assert helper.started[-1].worker is jobs.extra[0], "the first helper is busy"
    finally:
        jobs.shutdown()


def test_the_nesting_depth_is_the_shortest_way(fx_install):
    """An element tab that holds a genie nesting the saved genie before a copy of the saved
    genie itself: its depth is the shortest way (1), not the first copy's (2)."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import genie_place as GP

    lib = _lib(fx_install)
    n2, n1 = (ElementDocument.open_element(lib, n) for n in ("n2", "n1"))
    records = list(n2.parsed.page.objects) + list(n1.parsed.page.objects)
    res = n2._resolver()
    tab = types.SimpleNamespace(
        parsed=types.SimpleNamespace(page=types.SimpleNamespace(objects=records)), _resolver=lambda: res
    )
    assert MainWindow._nesting_depth(tab, [(lib, "n0")]) == 1
    assert len(GP.records_nesting_chain(records, lib, "n0", res)) == 2, "the first way"
    assert GP.records_nesting_chain(records, lib, "n0", res, shortest=True) == ["fxnest.n0"]
    assert MainWindow._nesting_depth(n2, [(lib, "n0")]) == 2, "n2 holds n1 holding n0"


# -- in the event loop, with the window's real helper ----------------------------------------
def test_in_the_event_loop_the_row_lands_before_the_pages(fx_install, win):
    """The window in a running event loop, its real helper process: the row genie opened
    after three pages takes n0's save first, then the pages, smaller ones first."""
    _open(win, fx_install, ["FxParts", "FxGenies", "FxMembers"], [("fxparts.ctm", "fx_row")])
    jobs = win._genie_job_runner()
    order, landed = [], {}
    jobs.started.connect(lambda doc, names: order.append(_name(doc)))
    jobs.finished.connect(lambda doc, names, n, job: landed.setdefault(_name(doc), time.monotonic()))
    d = _save_n0_in(win, fx_install)
    loop = QtCore.QEventLoop()
    t0 = time.monotonic()

    def step():
        if time.monotonic() - t0 > 90 or (order and not jobs.busy()):
            loop.quit()
            return
        QtCore.QTimer.singleShot(50, step)

    QtCore.QTimer.singleShot(0, lambda: win._propagate_element(d))
    QtCore.QTimer.singleShot(50, step)
    loop.exec()
    try:
        assert order == ["fxparts.fx_row", "FxMembers", "FxGenies", "FxParts"], order
        assert landed["fxparts.fx_row"] <= min(landed[x] for x in ("FxMembers", "FxGenies", "FxParts"))
        row = win._docs[_tab(win, "fxparts.fx_row")]["doc"]
        assert row._undo and row._undo[-1].genie_save_step
    finally:
        jobs.shutdown()


def test_an_update_the_tab_waits_for_takes_the_place_of_its_quiet_one(fx_install, win, monkeypatch):
    """An update the tab waits for replaces its quiet one."""
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import workers
    from ctgedit.ui.jobs import genie_jobs as gj

    helper = _SlowHelper(monkeypatch)
    monkeypatch.setattr(gj.WarmWorker, "alive", lambda self: True)
    monkeypatch.setattr(workers, "free_memory_mb", lambda: 8000)
    ended = []
    monkeypatch.setattr(gj.WarmWorker, "kill_now", lambda self: ended.append(self))
    _helpers(2)
    jobs = win._genie_job_runner()
    page, other = (EditableDocument.open(fx_install.page(p)) for p in ("FxParts", "FxGenies"))
    try:
        jobs.submit(page, {("fxparts", "fx_row"): None}, ["fxparts.fx_row"], why="follow", quiet=True)
        jobs.submit(other, {("fxnest", "n4"): None}, ["fxnest.n4"])
        quiet_one = helper.started[0]
        assert helper.names() == ["FxParts", "FxGenies"] and quiet_one.quiet
        jobs.submit(page, {("fxnest", "n1"): None}, ["fxnest.n1"], why="save")
        assert helper.names() == ["FxParts", "FxGenies", "FxParts"], "at once"
        new = helper.started[-1]
        assert new is not quiet_one and not new.quiet and new.why == "save"
        assert set(new.changes) == {("fxparts", "fx_row"), ("fxnest", "n1")}
        assert ended == [quiet_one.worker] and new.worker is quiet_one.worker
        assert quiet_one not in jobs._dead and len(jobs.helpers()) == 2
        # (an update that is not quiet keeps its place: the next one waits for it)
        jobs.submit(other, {("fxnest", "n2"): None}, ["fxnest.n2"], why="save")
        assert helper.names()[-1] == "FxParts" and any(j.doc is other for j in jobs._queue)
    finally:
        jobs.shutdown()
