"""Open tabs get updated first after a genie save."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtWidgets  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_save_chain_follow2 import _quiet_chain, _show_again, win  # noqa: E402,F401
from test_element_nested_update import _edit_n0  # noqa: E402


def test_a_tab_shown_while_a_run_saves_library_genies_composes_at_once(fx_install, win, monkeypatch):
    """The counterpart of test_save_chain_follow2's wait: by default the tab shown during a
    run composes its genies at once (the run's end brings it up to date again)."""
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    win._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n2")
    host = win.doc
    win.open_document(fx_install.page("FxShapes"))
    _edit_n0(fx_install)
    composed = []
    monkeypatch.setattr(win, "_update_stale_genies", lambda doc, first=False: composed.append(doc) or [])
    UpdatePagesRun.users_busy = "fxnest.n0"
    _show_again(win, host)
    assert host in composed, "composed during the run"
    assert not any(d is host for d in win.__dict__.get("_after_run_docs") or ())


def test_an_update_lands_under_the_save_prompts_but_not_under_other_dialogs(fx_install, win, monkeypatch):
    from ctgedit.ui.prefs.settings import app_settings

    win.open_document(fx_install.page("FxShapes"))
    d = win.doc
    box = QtWidgets.QMessageBox(win)
    box.setProperty("ctg_lets_open_tabs_update", True)
    other = QtWidgets.QDialog(win)
    try:
        monkeypatch.setattr(QtWidgets.QApplication, "activeModalWidget", staticmethod(lambda: box))
        assert win._genie_may_land(d)
        monkeypatch.setattr(QtWidgets.QApplication, "activeModalWidget", staticmethod(lambda: other))
        assert not win._genie_may_land(d), "a dialog the user works in holds it"
        app_settings().setValue("opt/tabs_wait_for_runs", True)
        assert win._tabs_wait_for_runs() is True
    finally:
        box.deleteLater()
        other.deleteLater()


@pytest.mark.parametrize("wait", [False, True])
def test_the_genie_saved_question_lets_the_open_tabs_take_the_save(fx_install, win, monkeypatch, wait):
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    monkeypatch.setattr(win._genie_job_runner(), "submit", lambda *a, **k: None)
    app_settings().setValue("opt/tabs_wait_for_runs", wait)
    lib = fx_install.file("FxLib", "fxsym.ctl")
    d = ElementDocument.open_element(lib, "fx_on")
    d.move_object(0, 3, 2)
    d.save()
    libraries_changed()
    app_settings().setValue("opt/symbol_save_pages", "ask")
    seen = []
    monkeypatch.setattr(
        MainWindow,
        "_exec_prompt",
        staticmethod(lambda box: seen.append(bool(box.property("ctg_lets_open_tabs_update")))),
    )
    win._users_saved_prompt(d)
    assert seen == [not wait]


def test_the_search_after_a_save_shows_its_window_only_when_it_is_long(win):
    from ctgedit.ui.jobs.job_progress import JobProgress

    p = JobProgress(
        win,
        "Genie Saved",
        "Finding what uses x…",
        show_after_ms=60000,
        status_bar=win.statusBar(),
        lets_tabs_update=True,
    )
    try:
        assert p.tick(0, 3, "Reading a… (1 of 3)")
        assert not p.shown and not p.prog.isVisible()
        assert win.statusBar().currentMessage() == "Reading a… (1 of 3)", "the status bar shows it"
        assert p.prog.property("ctg_lets_open_tabs_update") is True
    finally:
        p.close()
    q = JobProgress(win, "Genie Saved", "Finding what uses x…", show_after_ms=1)
    try:
        import time

        time.sleep(0.02)
        q.tick(1, 3)
        assert q.shown and q.prog.isVisible()
        assert not q.prog.property("ctg_lets_open_tabs_update")
    finally:
        q.close()
    r = JobProgress(win, "Used By", "Finding what uses x…")
    try:
        assert r.shown and r.prog.isVisible(), "Used By's own search shows at once"
    finally:
        r.close()


def test_the_read_after_a_save_takes_its_delay_from_the_settings(fx_install, win, monkeypatch):
    from ctgedit.ui.jobs import job_progress
    from ctgedit.ui.prefs.settings import app_settings

    made = []
    real = job_progress.JobProgress

    def spy(*a, **k):
        made.append(k)
        return real(*a, **k)

    monkeypatch.setattr(job_progress, "JobProgress", spy)
    app_settings().setValue("opt/users_read_window_ms", 3500)
    usage, why = win._element_users_read(fx_install.file("FxLib", "fxnest.ctm"), "n0", title="Genie Saved")
    assert why == "" and usage is not None
    assert made[-1]["show_after_ms"] == 3500 and made[-1]["lets_tabs_update"] is True
    win._element_users_read(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    assert made[-1]["show_after_ms"] == 3500, "Used By's own search takes the delay too"


def test_opening_an_element_reads_what_uses_its_library_on_a_worker(fx_install, win, monkeypatch):
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.window import library as L

    started = []
    monkeypatch.setattr(L, "warm_element_users", lambda p: started.append(p))
    lib = fx_install.file("FxLib", "fxnest.ctm")
    win._open_element(lib, "n1")
    assert started == [lib]
    monkeypatch.undo()
    GU._LIB_CACHE.clear()
    L.warm_element_users(lib)
    key = os.path.normcase(os.path.dirname(os.path.abspath(lib)))
    L._USERS_WARMING[key].join(60)
    assert lib in GU._LIB_CACHE, "its library's tables were read on the worker"
