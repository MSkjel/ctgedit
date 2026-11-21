"""Update Pages runs in a worker process and keeps the window painting."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtWidgets  # noqa: E402

#: the fixture page the tests copy and start blank pages from
SEED = "FxShapes"


def test_update_pages_runs_in_the_worker_and_reports(tmp_path, monkeypatch, fx_page):
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.dialog import dialogs
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.io import updatepages

    d = tmp_path / "scratch"
    d.mkdir()
    for n in ("a", "b"):
        shutil.copy2(fx_page(SEED), d / ("%s.ctg" % n))
    # (the worker checks templates, genies and symbols against the fake install's FxMain)
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(fx_page(SEED)))
    called = []
    real = updatepages.update_pages
    monkeypatch.setattr(updatepages, "update_pages", lambda *a, **k: called.append(1) or real(*a, **k))

    class _Dlg:
        def __init__(self, start_dir, parent=None, projects=None, library_dir=None):
            self.pdir = start_dir

        def exec(self):
            return QtWidgets.QDialog.Accepted

        def options(self):
            return dict(templates=True, genies=True, symbols=True, backup=False, page_properties=False)

        def projects(self):
            return [self.pdir]

        def processes(self):
            return 1

    monkeypatch.setattr(dialogs, "UpdatePagesDialog", _Dlg)
    shown = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information", lambda *a, **k: shown.append(a[2]) or QtWidgets.QMessageBox.Ok
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *a, **k: shown.append("WARN " + a[2]) or QtWidgets.QMessageBox.Ok,
    )
    # (the result window of the run: its lines, as the box showed them)
    monkeypatch.setattr(
        MainWindow,
        "_show_run_results",
        lambda self, title, lines, **k: shown.append("\n".join(lines)) or False,
    )
    w = MainWindow()
    try:
        w.open_document(str(d / "a.ctg"))
        w._update_pages()
    finally:
        w.doc = None
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.close()
    assert not called  # not in this process
    assert shown and not shown[-1].startswith("WARN"), shown
    # the open page is left to its tab (checked there: counted, and its line says what
    # became of it); the other one was checked by the worker
    assert "2 pages checked in 1 project," in shown[-1], shown[-1]
    assert "Open in a tab, nothing to update: a." in shown[-1], shown[-1]
    assert not [p for p in os.listdir(d) if p.endswith((".json", ".progress", ".skip"))]


# -- the pages open in tabs (LibraryCommands._update_open_documents) -------------------------
def _counting_stamps(monkeypatch):
    from ctgedit.format import element as E

    now = [E.ctdraw_timestamp()]

    def stamp():
        now[0] += 1
        return now[0]

    monkeypatch.setattr(E, "ctdraw_timestamp", stamp)


@pytest.fixture
def open_tab(tmp_path, monkeypatch, fx_page):
    """Proj with tablib.ctm (genie g1) and p1.ctg / p2.ctg (p1 places g1), both open in a
    window; then g1 is changed in its library (not through the window: no automatic update
    ran), so p1 is out of date in its tab and p2 is not."""
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.main_window import MainWindow

    _counting_stamps(monkeypatch)
    proj = tmp_path / "Proj"
    proj.mkdir()
    monkeypatch.setenv("CTG_LIB_PATH", str(proj))
    lib = str(proj / "tablib.ctm")
    e = ElementDocument.new_element(lib, "g1", "genie", canvas=(300, 200))
    e.create_object("text", rect=(10, 10, 120, 26), text="G1")
    e.save()
    p1, p2 = str(proj / "p1.ctg"), str(proj / "p2.ctg")
    PAGE = fx_page(SEED)
    doc = EditableDocument.new_blank(PAGE, p1, register=False)
    assert doc.place_library_element("tablib", "g1", "ctm", (100, 100)) is not None
    doc.save()
    EditableDocument.new_blank(PAGE, p2, register=False).save()
    w = MainWindow()
    w.open_document(p2)
    w.open_document(p1)
    e = ElementDocument.open_element(lib, "g1")
    e.move_objects([list(e.page.roots())[0]], 6, 0)
    e.save()
    libraries_changed()
    GU.clear_cache()
    docs = {os.path.basename(x["doc"].path): x["doc"] for x in w._docs}
    assert docs["p1.ctg"].genie_changes() and not docs["p2.ctg"].genie_changes()
    try:
        yield w, str(proj), docs["p1.ctg"], docs["p2.ctg"]
    finally:
        # (the updated page is unsaved: the close question would wait for an answer)
        w._ask_save_on_close = lambda *a, **k: QtWidgets.QMessageBox.Discard
        w._save_changes_list = lambda *a, **k: True
        w.doc = None
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.close()
        w.deleteLater()


OPTS = {"genies": True, "templates": True, "symbols": True}


def _summary(tabs):
    import types
    from ctgedit.ui.window.library import LibraryCommands

    rep = types.SimpleNamespace(
        pages=0,
        projects=[],
        stale=0,
        updated=[],
        stopped=False,
        symbols=0,
        templates=0,
        supergenies=0,
        instances=0,
        composites=0,
        composites_skipped=0,
        failed=[],
        unregistered=[],
        open_in_builder=[],
        skipped_open=[],
        changed_meanwhile=[],
        db_in_use=[],
        rows_pending=[],
        rows_written=[],
        warnings=[],
        not_checked=0,
        errors=[],
        recovered=[],
        records=[],
        links_dropped=[],
        unchanged=[],
    )
    return LibraryCommands._update_pages_summary(rep, None, tabs)


def test_an_open_page_is_updated_in_memory_and_told(open_tab):
    """The tab step works on the document in memory (genie_changes / apply_genie_changes,
    as the helper does), one undo step, and the box names the page."""
    w, proj, d1, d2 = open_tab
    # (an edit of the user's on it: the page stays unsaved, the update one more step)
    d1.create_object("rectangle", rect=(5, 5, 20, 20))
    steps = len(d1._undo)
    tabs = w._update_open_documents([proj], OPTS, marks=w._open_tab_marks())
    assert tabs.updated == [d1.path] and tabs.nothing == [d2.path] and not tabs.failed
    assert len(d1._undo) == steps + 1 and not d1.genie_changes() and d1.dirty
    assert tabs.genies >= 1
    lines = _summary(tabs)
    assert lines[0] == "2 pages checked in 1 project, 1 out of date, 1 updated (1 in its tab, not saved yet)."
    assert "Open in a tab and updated there (save it to keep the changes): p1." in lines
    assert "Open in a tab, nothing to update: p2." in lines


def test_an_open_page_with_nothing_of_the_users_is_saved_after_its_update(open_tab):
    """Opened and not touched: Update Pages updates it in its tab and saves it there (its
    file on disk is up to date, the tab not unsaved), and the box says so."""
    from ctgedit.editing import EditableDocument

    w, proj, d1, d2 = open_tab
    tabs = w._update_open_documents([proj], OPTS, marks=w._open_tab_marks())
    assert tabs.saved == [d1.path] and not tabs.updated and tabs.nothing == [d2.path]
    assert not d1.dirty
    assert not EditableDocument.open(d1.path).genie_changes()
    lines = _summary(tabs)
    assert lines[0] == "2 pages checked in 1 project, 1 out of date, 1 updated."
    assert "Open in a tab with no changes of yours, updated and saved there: p1." in lines


def test_a_step_error_is_told_not_hidden(open_tab, monkeypatch, caplog):
    import logging

    w, proj, d1, _d2 = open_tab

    def broken(_changes, placeholders=None):
        raise RuntimeError("compose failed")

    monkeypatch.setattr(d1, "apply_genie_changes", broken)
    asked = []
    real = d1.template_status
    monkeypatch.setattr(d1, "template_status", lambda: asked.append(1) or real())
    with caplog.at_level(logging.WARNING):
        tabs = w._update_open_documents([proj], OPTS)
    assert tabs.failed == [d1.path] and d1.path not in tabs.updated
    assert asked  # (the other steps still ran)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "compose failed" in text and "could not update its genies" in text
    assert "Open in a tab, could not be updated (the log says why): p1." in _summary(tabs)


def test_a_pending_automatic_update_never_lands_on_top_of_the_run(open_tab):
    """An automatic update of the open page computed but not landed yet is dropped: the
    run's step does its work, nothing lands after it (one update, one undo step)."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui.jobs.genie_jobs import GenieJob

    w, proj, d1, _d2 = open_tab
    jobs = w._genie_job_runner()
    changes = d1.genie_changes()
    ans = GW.compute(d1.path, d1.serialize(), changes)
    assert ans.get("ops")
    job = GenieJob(d1, changes, ["tablib.g1"])
    jobs._ready.append((job, ans, 0.0))
    landed = []
    jobs.finished.connect(lambda *a: landed.append(a))
    steps = len(d1._undo)
    # (the line _genie_job_started shows with no timeout: it goes with the dropped job)
    w.statusBar().showMessage(
        "Updating tablib.g1 on this page. The genies marked in amber "
        "change when it is done (you can go on working)\u2026"
    )
    tabs = w._update_open_documents([proj], OPTS)
    assert tabs.saved == [d1.path]  # (updated; nothing on it was the user's: saved)
    assert not jobs.busy(d1)
    assert w.statusBar().currentMessage() == ""
    jobs._poll()
    assert not landed and len(d1._undo) == steps + 1


def test_an_automatic_update_that_landed_during_the_run_is_told(open_tab):
    """The reverse: the automatic update landed first (after the run started); the step
    finds nothing to do and the box says the page was updated in its tab by it."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui.jobs import genie_jobs

    w, proj, d1, d2 = open_tab
    marks = w._open_tab_marks()
    changes = d1.genie_changes()
    ans = GW.compute(d1.path, d1.serialize(), changes)
    n = genie_jobs.apply_answer(d1, ans)
    assert n and n > 0
    w._genie_job_finished(d1, ["tablib.g1"], n)  # (as a landed job says it)
    steps = len(d1._undo)
    tabs = w._update_open_documents([proj], OPTS, marks=marks)
    # (nothing on it was the user's: the automatic update's work is saved there)
    assert tabs.saved == [d1.path] and tabs.nothing == [d2.path]
    assert not tabs.updated and not tabs.auto and not d1.dirty
    assert len(d1._undo) == steps
    lines = _summary(tabs)
    assert lines[0].startswith("2 pages checked in 1 project, 1 out of date, 1 updated")
    assert "Open in a tab with no changes of yours, updated and saved there: p1." in lines


def test_a_genie_dialogs_line_stays_when_an_automatic_update_goes(open_tab):
    w, _proj, d1, _d2 = open_tab
    text = "Applying the parameters of tablib.g1 (you can go on working)\u2026"
    w.statusBar().showMessage(text)
    w._clear_auto_update_line(d1)
    assert w.statusBar().currentMessage() == text
    w.statusBar().showMessage("Applying the template tpl again (you can go on working)\u2026")
    w._clear_auto_update_line(d1, templates=False)
    assert w.statusBar().currentMessage()
    w._clear_auto_update_line(d1)
    assert w.statusBar().currentMessage() == ""


def test_a_run_without_genies_keeps_the_pending_genie_update(open_tab):
    """Genies unticked: the step does not redo the page's genies, so their pending automatic
    update is kept and lands after the run."""
    from ctgedit import editing
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui.jobs.genie_jobs import GenieJob

    w, proj, d1, _d2 = open_tab
    jobs = w._genie_job_runner()
    changes = d1.genie_changes()
    ans = GW.compute(d1.path, d1.serialize(), changes)
    assert ans.get("ops")
    jobs._ready.append((GenieJob(d1, changes, ["tablib.g1"]), ans, 0.0))
    entry = next(e for e in w._docs if e["doc"] is d1)
    gen = entry.get("genie_gen")
    assert gen != editing._LIBRARY_GENERATION[0]
    steps = len(d1._undo)
    tabs = w._update_open_documents([proj], {"genies": False, "templates": True, "symbols": True})
    assert jobs.busy(d1) and len(d1._undo) == steps
    assert d1.path in tabs.nothing
    assert entry.get("genie_gen") == gen  # (a tab switch still looks for genies)
    jobs._poll()
    assert not jobs.busy(d1)
    assert not d1.genie_changes() and len(d1._undo) == steps + 1


def test_an_update_is_seen_when_the_undo_cap_drops_an_old_step(open_tab, monkeypatch):
    """The page's history is at the undo cap: the update's step drops the oldest one, so the
    undo stack keeps its length. The page is still updated. (The genie step here is one plain edit: the cap
    is checked on each edit, not on a batch's step.)"""
    from ctgedit import editing
    from ctgedit.editing_base import _Command

    w, proj, d1, _d2 = open_tab

    def apply(_ch, placeholders=None):
        d1.move_objects([list(d1.page.roots())[0]], 1, 0)
        return 1

    monkeypatch.setattr(d1, "apply_genie_changes", apply)
    big = 50 * 1024 * 1024  # (a number only: nothing is held)
    for k in range(20):
        d1._undo.append(_Command("old step %d" % k, lambda: None, lambda: None, nbytes=big))
    total = sum(editing._command_bytes(c) for c in d1._undo)
    monkeypatch.setattr(editing, "UNDO_BYTES_TOTAL", total * 3 // 2 + 3)
    assert editing._undo_page_cap() >= total
    steps, trimmed = len(d1._undo), getattr(d1, "undo_trimmed", 0)
    tabs = w._update_open_documents([proj], OPTS)
    assert len(d1._undo) == steps and d1.undo_trimmed == trimmed + 1  # (the case)
    assert tabs.saved == [d1.path] and d1.path not in tabs.nothing + tabs.auto + tabs.updated


def test_an_open_page_out_of_date_that_changes_in_nothing_is_told(open_tab, monkeypatch, caplog):
    """genie_changes reports the page out of date, and applying it changes nothing: its own
    outcome and line, counted out of date, the reason in the log."""
    import logging

    w, proj, d1, d2 = open_tab
    monkeypatch.setattr(d1, "apply_genie_changes", lambda _ch, placeholders=None: 0)
    steps = len(d1._undo)
    with caplog.at_level(logging.INFO, logger="ctgedit.update-pages"):
        tabs = w._update_open_documents([proj], OPTS)
    assert tabs.unchanged == [d1.path] and tabs.nothing == [d2.path]
    assert not tabs.updated and tabs.stale == 1 and len(d1._undo) == steps
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "open in a tab, out of date, but nothing on it changed" in text
    assert "tablib.g1 could not be composed again" in text
    lines = _summary(tabs)
    assert lines[0].startswith("2 pages checked in 1 project, 1 out of date, 0 updated")
    assert "Open in a tab, out of date, but nothing on it could be updated (the log says why): p1." in lines


def test_update_pages_shows_the_update_in_every_window(open_tab, monkeypatch):
    """Run from another window, Update Pages updates the page the first window shows in
    memory; that window shows it again."""
    import types
    from ctgedit.ui.dialog import dialogs
    from ctgedit.ui.main_window import MainWindow

    w, proj, d1, _d2 = open_tab
    assert w.doc is d1
    # (a change of the user's on it: updated in its tab, not saved there)
    d1.move_objects([list(d1.page.roots())[0]], 1, 0)
    steps = len(d1._undo)
    seen = []
    real = w._refresh

    def refresh(*a, **k):
        seen.append(len(d1._undo))
        return real(*a, **k)

    monkeypatch.setattr(w, "_refresh", refresh)

    class _Dlg:
        def __init__(self, start_dir, parent=None, projects=None, library_dir=None):
            pass

        def exec(self):
            return QtWidgets.QDialog.Accepted

        def options(self):
            return dict(templates=True, genies=True, symbols=True, backup=False, page_properties=False)

        def projects(self):
            return [proj]

        def processes(self):
            return 1

    monkeypatch.setattr(dialogs, "UpdatePagesDialog", _Dlg)
    rep = types.SimpleNamespace(
        pages=0,
        projects=["Proj"],
        stale=0,
        updated=[],
        stopped=False,
        symbols=0,
        templates=0,
        supergenies=0,
        instances=0,
        composites=0,
        composites_skipped=0,
        failed=[],
        unregistered=[],
        open_in_builder=[],
        skipped_open=[],
        changed_meanwhile=[],
        db_in_use=[],
        rows_pending=[],
        rows_written=[],
        warnings=[],
        not_checked=0,
        errors=[],
        recovered=[],
        records=[],
        links_dropped=[],
        unchanged=[],
        open_at_start=[],
    )
    shown = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information", lambda *a, **k: shown.append(a[2]) or QtWidgets.QMessageBox.Ok
    )
    monkeypatch.setattr(
        MainWindow,
        "_show_run_results",
        lambda self, title, lines, **k: shown.append("\n".join(lines)) or False,
    )
    w2 = MainWindow()
    try:
        monkeypatch.setattr(w2, "_update_pages_in_worker", lambda job, parts=None: rep)
        w2._update_pages()
    finally:
        if w2 in MainWindow._instances:
            MainWindow._instances.remove(w2)
        w2.close()
        w2.deleteLater()
    assert len(d1._undo) == steps + 1 and steps + 1 in seen, seen
    assert shown and "Open in a tab and updated there (save it to keep the changes): p1." in shown[-1], shown
