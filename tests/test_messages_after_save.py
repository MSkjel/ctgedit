"""Messages after a library save."""

from ctgedit.ui.window import open_tabs as open_tabs_mod
from ctgedit.ui.jobs.update_run import UpdatePagesRun
import os
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6 import QtCore, QtWidgets  # noqa: E402


def _qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(fx_install, monkeypatch):
    """A main window whose save prompts are answered by ``answers['click']`` (the asked
    (text, informative text, status bar line) in ``asked``); the close questions answered
    Cancel, their texts in ``closing``; the notes in ``said``."""
    _qt()
    from ctgedit.ui import notify as N
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings, set_genie_save_pages
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    st = app_settings()
    st.setValue("opt/auto_update_genies", True)
    st.setValue("opt/auto_update_symbols", True)
    set_genie_save_pages("ask")
    st.setValue("opt/symbol_save_pages", "ask")
    asked, answers, closing, said = [], {"click": None}, [], []
    holder = {}

    def prompt(box):
        w = holder.get("w")
        asked.append(
            (box.text(), box.informativeText(), w.statusBar().currentMessage() if w is not None else "")
        )
        for b in box.buttons():
            if answers["click"] and b.text() == answers["click"]:
                b.click()

    monkeypatch.setattr(MainWindow, "_exec_prompt", staticmethod(prompt))

    def box_exec(box):
        closing.append(box.text())
        return QtWidgets.QMessageBox.Cancel

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", box_exec)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        staticmethod(
            lambda parent, title, text, *a, **k: closing.append(text) or QtWidgets.QMessageBox.Cancel
        ),
    )
    real_notify = N.notify

    def note(win, text, level="info", **kw):
        said.append((text, level))
        return real_notify(win, text, level, **kw)

    monkeypatch.setattr(N, "notify", note)
    w = MainWindow()
    holder["w"] = w
    w.asked, w.answers, w.closing, w.said = asked, answers, closing, said
    try:
        yield w
    finally:
        for e in list(w._docs):
            e["doc"].dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        UpdatePagesRun.users_busy = ""
        UpdatePagesRun.users_queue.clear()
        UpdatePagesRun.chain_busy = False
        w.close()


def _tab(w, doc) -> int:
    return next(k for k, e in enumerate(w._docs) if e["doc"] is doc)


def _edit_text(doc, text: str) -> None:
    objs = doc.parsed.page.objects
    i = next(k for k, o in enumerate(objs) if o.type_code == 0x05 and doc.page.objects[k].parent is None)
    assert doc.set_text(i, text)


def _save_sub(w, fx, element="n1"):
    """``element`` of fxnest.ctm edited and saved from ``w``'s tab (the Genie Saved
    question answered Later)."""
    w.answers["click"] = "Later"
    w._open_element(fx.file("FxLib", "fxnest.ctm"), element)
    _edit_text(w.doc, "%Name% CHANGED1")
    assert w.save()
    return w.doc


def _pump(ms: int = 50) -> None:
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()


# -- (1) the close question -----------------------------------------------------------------
def test_the_close_question_names_the_element_and_no_open_for_a_save_s_update(fx_install, window):
    """A row genie and a page that places it, open in tabs, take a sub genie's save as an
    automatic change: closing them names the row by its element and says the update without
    'when it was opened' (the page was open long before the save)."""
    window.open_document(fx_install.page("FxParts"))
    page = window.doc
    window._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_row")
    row = window.doc
    _save_sub(window, fx_install)
    assert window._only_auto_changes(row), [w for w, _c in row.ui.auto_changes]
    assert not window._ask_to_close(_tab(window, row))
    text = window.closing[-1]
    assert text.startswith(
        "Genie fxparts.fx_row (FxLib) was updated automatically: fxnest.n1 updated from its library"
    ), text
    assert "when it was opened" not in text and ".ctm" not in text
    assert window._only_auto_changes(page)
    assert not window._ask_to_close(_tab(window, page))
    text = window.closing[-1]
    assert text.startswith("FxParts was updated automatically: "), text
    assert "fxnest.n1 updated from its library" in text and "when it was opened" not in text


def test_the_close_question_of_an_edited_element_names_the_element(fx_install, window):
    window._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_row")
    row = window.doc
    _edit_text(row, "%RowNum% EDITED")
    assert not window._ask_to_close(_tab(window, row))
    assert window.closing[-1].startswith("Save changes to Genie fxparts.fx_row (FxLib) before closing it?")


# -- (2) the read behind the delay ----------------------------------------------------------
class _ShowCount(QtCore.QObject):
    def __init__(self):
        super().__init__()
        self.shown = []

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.Show and isinstance(obj, QtWidgets.QProgressDialog):
            self.shown.append(obj.labelText())
        return False


@pytest.mark.parametrize("how", ["show users", "used by", "update users"])
def test_reading_what_uses_an_element_shows_no_window_when_it_is_quick(fx_install, window, monkeypatch, how):
    """Show Users in the Genie Saved question (right after the save), Used By and Update
    Users read behind Settings > Long jobs: a quick read shows no progress window; its status
    bar line goes when it ends."""
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/users_read_window_ms", 60000)
    monkeypatch.setattr(MainWindow, "_update_element_users", lambda self, usage, confirm=True: None)
    spy = _ShowCount()
    app = QtWidgets.QApplication.instance()
    app.installEventFilter(spy)
    try:
        if how == "show users":
            window.answers["click"] = "Show Users…"
            window._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n0")
            _edit_text(window.doc, "%Name% CHANGED1")
            assert window.save()
            assert window.asked, "the Genie Saved question was asked"
        elif how == "used by":
            window._show_used_by(fx_install.file("FxLib", "fxnest.ctm"), "n0")
        else:
            window._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n0")
            window._update_users_current()
    finally:
        app.removeEventFilter(spy)
    assert spy.shown == []
    assert not window.statusBar().currentMessage().startswith("Reading ")


def test_the_read_takes_its_delay_from_the_settings(fx_install, window, monkeypatch):
    from ctgedit.ui.jobs import job_progress
    from ctgedit.ui.prefs.settings import app_settings

    made = []
    real = job_progress.JobProgress

    def spy(*a, **k):
        made.append(k)
        return real(*a, **k)

    monkeypatch.setattr(job_progress, "JobProgress", spy)
    app_settings().setValue("opt/users_read_window_ms", 3500)
    for title in ("Genie Saved", "Used By"):
        usage, why = window._element_users_read(fx_install.file("FxLib", "fxnest.ctm"), "n0", title=title)
        assert why == "" and usage is not None
        assert made[-1]["show_after_ms"] == 3500 and made[-1]["lets_tabs_update"] is True
        assert made[-1]["status_bar"] is window.statusBar()
    app_settings().setValue("opt/tabs_wait_for_runs", True)
    window._element_users_read(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    assert made[-1]["lets_tabs_update"] is False, "the old waits keep working"


# -- (3) the status bar line ----------------------------------------------------------------
def test_the_search_s_line_is_gone_while_the_genie_saved_question_is_up(fx_install, window):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/users_read_window_ms", 60000)
    window._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    _edit_text(window.doc, "%Name% CHANGED1")
    window.answers["click"] = "Later"
    assert window.save()
    text, _info, line = window.asked[-1]
    assert "use fxnest.n0" in text
    assert not line.startswith("Reading "), line


def test_the_job_s_line_goes_only_when_the_bar_still_shows_it(window):
    from ctgedit.ui.jobs.job_progress import JobProgress

    bar = window.statusBar()
    bar.clearMessage()
    p = JobProgress(
        window, "Used By", "Finding what uses x…", phase="index", show_after_ms=60000, status_bar=bar
    )
    try:
        assert p.tick(0, 2, "Reading a… (1 of 2)")
        assert p.tick(1, 2, "Reading b… (2 of 2)")
        assert bar.currentMessage() == "Reading b… (2 of 2)"
    finally:
        p.close()
    assert bar.currentMessage() == "", "the line went with the job"
    q = JobProgress(
        window, "Used By", "Finding what uses x…", phase="index", show_after_ms=60000, status_bar=bar
    )
    try:
        assert q.tick(1, 2, "Reading b… (2 of 2)")
        bar.showMessage("Something else.")
    finally:
        q.close()
    assert bar.currentMessage() == "Something else.", "a message told since stays"
    bar.clearMessage()
    r = JobProgress(window, "Used By", "Finding what uses x…", phase="index", show_after_ms=1, status_bar=bar)
    try:
        r.set("Reading c… (1 of 9)")
        assert bar.currentMessage() == "Reading c… (1 of 9)"
        import time

        time.sleep(0.01)
        r.tick(8, 9, "Reading d… (9 of 9)")
        assert r.shown and bar.currentMessage() == "", "the window shows the line now"
    finally:
        r.close()


def test_the_message_under_the_job_s_line_comes_back(window):
    """The message the job's line was told over comes back with its level, its buttons and
    the time it had left; one whose time ran out meanwhile goes; a message told during the
    job is the one that comes back."""
    import time
    from ctgedit.ui.jobs.job_progress import JobProgress
    from ctgedit.ui.notify import Action

    bar = window.statusBar()
    act = Action("Undo", lambda: None)
    bar.show_message("Saved FxParts (FxMain).", 60000, level="success", actions=(act,), detail="a detail")
    p = JobProgress(
        window, "Used By", "Finding what uses x…", phase="index", show_after_ms=60000, status_bar=bar
    )
    try:
        assert p.tick(1, 2, "Reading b… (2 of 2)")
        assert bar.currentMessage() == "Reading b… (2 of 2)"
    finally:
        p.close()
    assert bar.currentMessage() == "Saved FxParts (FxMain)."
    text, level, actions, detail, until = bar.message_state()
    assert level == "success" and actions == (act,) and detail == "a detail"
    assert 0 < until - time.monotonic() <= 60.0 * 1.6
    # (its time ran out while the job's line was up: it goes)
    bar.show_message("Short.", 20)
    q = JobProgress(
        window, "Used By", "Finding what uses x…", phase="index", show_after_ms=60000, status_bar=bar
    )
    try:
        assert q.tick(1, 2, "Reading b… (2 of 2)")
        time.sleep(0.05)
    finally:
        q.close()
    assert bar.currentMessage() == ""
    # (a message told between two of its lines comes back, not the one before it)
    bar.showMessage("Before.")
    r = JobProgress(
        window, "Used By", "Finding what uses x…", phase="index", show_after_ms=60000, status_bar=bar
    )
    try:
        r.set("Reading a… (1 of 2)")
        bar.showMessage("Updated fxnest.n0 on FxParts.")
        r.set("Reading b… (2 of 2)")
    finally:
        r.close()
    assert bar.currentMessage() == "Updated fxnest.n0 on FxParts."
    # (the window shows: the message comes back under it)
    bar.showMessage("Before the window.")
    s = JobProgress(window, "Used By", "Finding what uses x…", phase="index", show_after_ms=1, status_bar=bar)
    try:
        s.set("Reading c… (1 of 9)")
        time.sleep(0.01)
        s.tick(8, 9, "Reading d… (9 of 9)")
        assert s.shown and bar.currentMessage() == "Before the window."
    finally:
        s.close()


def test_a_page_save_s_message_stays_after_a_used_by_window_reads_again(fx_install, window):
    """A Used By window open: a page save makes it read its users again (behind the delay,
    its line in the status bar); the page save's 'Saved ...' comes back when that read
    ends."""
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/users_read_window_ms", 60000)
    window.open_document(fx_install.page("FxParts"))
    page = window.doc
    window._show_used_by(fx_install.file("FxLib", "fxparts.ctm"), "fx_row")
    dlgs = open_tabs_mod.used_by_windows()
    assert dlgs
    dlgs[0].show()  # (the window under test is not shown: its Used By window is)
    window.show_tab(_tab(window, page))
    roots = [k for k, o in enumerate(page.page.objects) if o.parent is None]
    page.move_objects(roots[:1], 0, 4)
    assert window.save()
    saved = window.statusBar().currentMessage()
    assert saved.startswith("Saved FxParts"), saved
    assert dlgs[0].stale
    seen = []
    window.statusBar().messageChanged.connect(seen.append)
    for _k in range(40):
        _pump(50)
        if not dlgs[0].stale:
            break
    assert not dlgs[0].stale, "the Used By window read its users again"
    assert any(s.startswith("Finding what uses fxparts.fx_row") for s in seen), seen
    assert window.statusBar().currentMessage() == saved, seen


def test_a_used_by_window_reads_again_after_the_save_s_read_not_inside_it(fx_install, window, monkeypatch):
    """A Used By window open on the genie being saved: its own read after the save waits for
    the save's read (whose progress lets timers run) and reads the users after it, once."""
    from ctgedit.io import genieusage as GU

    lib = fx_install.file("FxLib", "fxnest.ctm")
    window._open_element(lib, "n0")
    window._show_used_by(lib, "n0")
    dlgs = open_tabs_mod.used_by_windows()
    assert dlgs
    dlgs[0].show()  # (the window under test is not shown: its Used By window is)
    depth, most, reads = [0], [0], []
    real = GU.element_users

    def counted(*a, **k):
        depth[0] += 1
        most[0] = max(most[0], depth[0])
        reads.append(depth[0])
        try:
            return real(*a, **k)
        finally:
            depth[0] -= 1

    monkeypatch.setattr(GU, "element_users", counted)
    _edit_text(window.doc, "%Name% CHANGED1")
    window.answers["click"] = "Later"
    assert window.save()
    for _k in range(40):
        _pump(50)
        if not dlgs[0].stale:
            break
    assert not dlgs[0].stale, "the Used By window read its users again"
    assert most[0] == 1, reads
    assert len(reads) == 2, reads  # (the save's read, then the window's)
    assert UpdatePagesRun.users_reads == 0


# -- (4) a Cancel of the search ---------------------------------------------------------------
_LEFT = (
    "fxnest.n0 saved. Finding what uses it was stopped, so the library genies and closed "
    "pages that use it were not updated."
)


@pytest.mark.parametrize(
    "kind,auto,tab,want",
    [
        ("genie", True, "page", _LEFT + " Pages and genies open in tabs that use it take the change there."),
        (
            "genie",
            True,
            "element",
            _LEFT + " Pages and genies open in tabs that use it take the change there.",
        ),
        (
            "genie",
            False,
            "page",
            _LEFT + " Pages open in tabs that use it take it there only where "
            "they place it directly (Update changed genies is off).",
        ),
        (
            "template",
            True,
            "page",
            "fx_style.normal@XGA saved. Finding what uses it was stopped, so "
            "the templates built on it and the closed pages that use it were not updated. Pages and "
            "templates open in tabs that use it take the change there.",
        ),
        # (no other tab open: nothing to say of the open tabs, as the read path says nothing)
        ("genie", True, "own", _LEFT),
        ("genie", True, None, _LEFT),
    ],
)
def test_a_cancelled_search_says_what_was_left_and_what_the_open_tabs_do(
    fx_install, window, monkeypatch, kind, auto, tab, want
):
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/auto_update_genies", auto)
    app_settings().setValue("opt/auto_update_template", auto)
    app_settings().setValue("opt/template_save_pages", "ask")
    app_settings().setValue("opt/save_template_warning", True)
    if tab == "page":
        window.open_document(fx_install.page("FxParts"))
    elif tab == "element":
        window._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_row")
    elif tab == "own":
        window._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n0")

    def stopped(lib, el, progress=None, **k):
        progress(0, 3, "FxMain\\FxParts.ctg")
        return types.SimpleNamespace(users=[], stopped=True, problems=[])

    monkeypatch.setattr(GU, "element_users", stopped)

    class Saved:
        path = fx_install.file("FxLib", "fxnest.ctm")
        element_name, display_name = "n0", "fxnest.n0"

    if kind == "template":
        Saved.path = fx_install.file("FxStyle", "fx_style.ctt")
        Saved.element_name, Saved.display_name = "normal@XGA", "fx_style.normal@XGA"
    window._users_saved_prompt(Saved())
    _pump()
    assert not window.asked
    texts = [t for t, _lv in window.said if "stopped" in t]
    assert texts == [want]
    assert "nothing that uses it was updated" not in " ".join(texts)


# -- (5) a symbol save's update of an open page -----------------------------------------------
def test_a_symbol_save_s_update_of_an_open_page_is_an_automatic_change(fx_install, window):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/symbol_save_pages", "never")
    window.open_document(fx_install.page("FxSymbols"))
    page = window.doc
    assert not page.dirty
    window._open_element(fx_install.file("FxLib", "fxsym.ctl"), "fx_box")
    box = window.doc
    box.move_object(0, 3, 2)
    assert window.save()
    assert page.dirty
    whats = [w for w, _c in page.ui.auto_changes]
    assert whats and whats[0] == "symbols updated from their library (fxsym.fx_box)", whats
    assert window._only_auto_changes(page)
    assert all(getattr(c, "automatic", False) for c in page._undo)
    assert not window._ask_to_close(_tab(window, page))
    assert window.closing[-1].startswith(
        "FxSymbols was updated automatically: symbols updated from their library (fxsym.fx_box). Save"
    )
    # (the bar's Undo takes it back, as for an element tab)
    window.show_tab(_tab(window, page))
    window._undo_auto_changes()
    assert not page._undo and not page.ui.auto_changes


# -- (6) a page open in a tab is not 'left alone' --------------------------------------------
def test_a_page_open_in_a_tab_is_not_named_as_left_alone(fx_install, window):
    """fxparts.fx_row: only FxParts places it, and FxParts is not registered in its project.
    Open in a tab, it takes the change there: nothing is said to be left."""
    window.open_document(fx_install.page("FxParts"))
    window._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_row")
    row = window.doc
    roots = [k for k, o in enumerate(row.page.objects) if o.parent is None]
    row.move_objects(roots, 0, 2)
    assert window.save()
    assert not window.asked  # (nothing to update: not asked)
    assert not [t for t, _lv in window.said if "left alone" in t], window.said


def test_the_genie_saved_question_names_only_the_closed_pages_left_alone(fx_install, window):
    window.open_document(fx_install.page("FxParts"))
    _save_sub(window, fx_install, "n0")
    _text, info, _line = window.asked[-1]
    assert "1 page not registered in its project is left alone: FxMembers." in info, info
    assert "FxParts" not in info
