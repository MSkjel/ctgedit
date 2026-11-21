"""Disk notices and tasks in the status bar."""

import os
import shutil
import stat
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the small page and the large one
SMALL = "FxThumb"
LARGE = "FxMany"


def _pump(n=8):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture()
def win(monkeypatch):
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    for k in (
        "auto_placeholder_missing",
        "auto_update_genies",
        "auto_update_symbols",
        "auto_update_template",
    ):
        app_settings().setValue("opt/" + k, False)
    w = MainWindow()
    w.resize(1280, 760)
    w.show()
    _pump()
    yield w
    from ctgedit.ui.jobs import tasks

    for t in list(tasks.registry().tasks.values()):
        tasks.drop(t.token)
    for x in list(MainWindow._instances):
        with_watch = getattr(x, "_watcher", None)
        if with_watch is not None and with_watch.files():
            with_watch.removePaths(with_watch.files())
        x._disk_timer.stop()
        for e in getattr(x, "_docs", []):
            e["doc"].dirty = False
        if x in MainWindow._instances:
            MainWindow._instances.remove(x)
        x.hide()


def _copy(fx, name="Small", src=SMALL):
    """A copy of FxMain's page ``src`` named ``name`` in FxMain's folder of the fake
    install (unregistered: no pages.DBF row)."""
    p = os.path.join(fx.project("FxMain"), name + ".ctg")
    shutil.copyfile(fx.page(src), p)
    return p


def _touch(path, s=5):
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + s * 10**9))


def _top_units(doc, n):
    objs = doc.page.objects
    out = [
        k
        for k, o in enumerate(objs)
        if o.parent is None and not o.raw.get("_from_template") and o.geometry is not None
    ]
    return out[:n]


# ======================================================================= changed on disk
def test_reload_of_a_page_edited_since_the_notice_asks_first(fx_install, win, monkeypatch):
    """A notice shown on a clean page, then the user edits: Reload asks (revert's own
    question) and No keeps the edits and the notice."""
    p = _copy(fx_install, "A")
    win.open_document(p)
    _touch(p)
    win._check_disk_changes([p])
    assert [b.text() for b in win._auto_bar.row("disk").buttons] == ["Reload", "Ignore"]
    win.doc.create_object("rectangle", (5, 5, 30, 30))
    win._refresh()
    mine = win.doc
    asked = []
    monkeypatch.setattr(win, "_confirm", lambda *a, **k: asked.append(a[1]) or False)
    win._auto_bar.row("disk").button("Reload").click()
    _pump()
    assert asked and "Discard your unsaved changes" in asked[0]
    assert win.doc is mine and mine.dirty  # (No: the edits stay)
    assert "disk" in win._auto_bar.keys()  # (and so does the notice)
    monkeypatch.setattr(win, "_confirm", lambda *a, **k: True)
    win._auto_bar.row("disk").button("Reload").click()
    _pump()
    assert win.doc is not mine and not win.doc.dirty


def test_a_reload_by_itself_waits_while_a_dialog_is_open(fx_install, win):
    """'Reload it' (opt/disk_reload always) in the default bar mode: not under a modal box
    (the page would change under the code waiting for the box); once it closed, it does."""
    from ctgedit.ui.prefs.settings import app_settings, set_disk_reload

    set_disk_reload("always")
    app_settings().setValue("ui/disk_change_prompt", "bar")
    p = _copy(fx_install, "M")
    win.open_document(p)
    before = win.doc
    seen = {}

    def during_box():
        _touch(p)
        win._check_disk_changes([p])
        seen["replaced"] = win.doc is not before
        seen["pending"] = p in win._disk_pending
        m = QtWidgets.QApplication.activeModalWidget()
        if m is not None:
            m.close()

    QtCore.QTimer.singleShot(50, during_box)
    QtWidgets.QMessageBox.question(win, "Delete", "Delete 6 objects?")
    assert seen == {"replaced": False, "pending": True}
    win._disk_timer.stop()
    win._check_disk_changes()  # (the timer's turn, box closed)
    assert win.doc is not before and not win.doc.dirty


# ======================================================================= tasks
def test_the_display_check_task_goes_with_its_page_and_with_its_cancel(fx_install, win):
    from ctgedit.ui.jobs import tasks

    p = _copy(fx_install, "C")
    win.open_document(p)
    doc = win.doc

    class FakeCheck:
        stopped = 0

        def poll(self):
            return None

        def cancel(self):
            FakeCheck.stopped += 1

    win._display_running = (doc, FakeCheck(), "k")
    win._display_task = tasks.begin(
        "display", "Checking the display of C\u2026", cancel=win._cancel_display_check, window=win, quiet=True
    )
    doc.dirty = False
    win._close_tab_asked(next(i for i, e in enumerate(win._docs) if e["doc"] is doc))
    _pump()
    assert FakeCheck.stopped == 1 and win.__dict__.get("_display_running") is None
    assert tasks.running(win) == [] and win.status_tasks.isHidden()
    # a task whose check already ended: its Cancel still removes it
    tok = win._display_task = tasks.begin(
        "display", "Checking the display of D\u2026", cancel=win._cancel_display_check, window=win
    )
    assert tasks.cancel(tok)
    assert tok not in tasks.registry().tasks and win.status_tasks.isHidden()


def test_a_task_of_a_closed_window_shows_nowhere(win):
    import gc
    from ctgedit.ui.jobs import tasks

    class Gone:  # (a window that went: only its weak reference stays)
        pass

    other = Gone()
    reg = tasks.registry()
    tok = next(reg._tokens)
    reg.tasks[tok] = tasks.Task(tok, "x", "Elsewhere…", window=other)
    assert reg.tasks[tok].shows_in(other) and not reg.tasks[tok].shows_in(win)
    del other
    gc.collect()
    assert reg.tasks[tok].orphaned()
    assert all(x.token != tok for x in tasks.running(win))
    assert all(x.token != tok for x in tasks.running())
    # the next task drops it from the registry
    tasks.end(tasks.begin("y", "Here…", window=win), tell=False)
    assert tok not in reg.tasks
    _pump()
    assert win.status_tasks.isHidden()


def test_cancel_in_the_list_survives_the_tasks_progress(win):
    """The rows are updated in place: a progress between the press and the release of
    Cancel keeps the button, so the click cancels; the same progress again emits nothing."""
    from ctgedit.ui.jobs import tasks

    hits = []
    tok = tasks.begin("find", "Find in closed pages\u2026", cancel=lambda: hits.append(1), window=win)
    tasks.progress(tok, 1, 40)
    _pump()
    win.status_tasks.toggle_list()
    _pump()
    pop = win.status_tasks.popover
    btn = next(
        b for b in pop.rows.findChildren(QtWidgets.QToolButton) if b.text() == "Cancel" and b.isVisible()
    )
    bar = pop.rows.findChildren(QtWidgets.QProgressBar)[0]
    QtTest.QTest.mousePress(btn, QtCore.Qt.LeftButton)
    tasks.progress(tok, 2, 40)  # (the helper's next progress)
    _pump()
    assert btn.isVisible() and bar.value() == 2
    QtTest.QTest.mouseRelease(btn, QtCore.Qt.LeftButton)
    _pump()
    assert hits == [1] and not btn.isEnabled()  # (cancelled: greyed)
    emitted = []
    tasks.registry().changed.connect(lambda: emitted.append(1))
    tasks.progress(tok, 2, 40)
    assert emitted == []
    tasks.progress(tok, 3, 40, "Find in closed pages: 3 pages read\u2026")
    assert emitted == [1]
    assert any(
        lab.text() == "Find in closed pages: 3 pages read\u2026"
        for lab in pop.rows.findChildren(QtWidgets.QLabel)
    )
    pop.close()
    tasks.drop(tok)


def test_a_quiet_task_tells_nothing_and_alerts_only_when_it_failed(win, monkeypatch):
    from ctgedit.ui import notify
    from ctgedit.ui.jobs import tasks

    monkeypatch.setattr(tasks, "LONG_S", -1.0)
    alerts = []
    monkeypatch.setattr(tasks, "_alert", lambda w: alerts.append(w))
    c = notify.centre(win)
    c.clear()
    tasks.end(tasks.begin("display", "Checking the display of A\u2026", window=win, quiet=True))
    assert c.entries == [] and alerts == []
    tasks.end(tasks.begin("display", "Checking the display of A\u2026", window=win, quiet=True), ok=False)
    assert c.entries == [] and alerts == [win]
    tasks.end(tasks.begin("find", "Finding\u2026", window=win))  # (not quiet: told)
    assert [m.text for m in c.entries] == ["Finding: done."]


def test_the_automatic_display_check_ends_quietly(fx_install, win, monkeypatch):
    from ctgedit.diag import display_check
    from ctgedit.ui import notify
    from ctgedit.ui.jobs import tasks

    monkeypatch.setattr(tasks, "LONG_S", -1.0)
    win.open_document(_copy(fx_install, "Q"))
    doc = win.doc

    class FakeCheck:
        def __init__(self, path):
            self.path = path
            self.answer = None

        def start(self):
            return True

        def poll(self):
            return self.answer

        def cancel(self):
            pass

    monkeypatch.setattr(display_check, "DisplayCheck", FakeCheck)
    monkeypatch.setattr(win, "_display_check_wanted", lambda d: True)
    win._display_queue = [doc]
    win._display_tick()
    t = tasks.registry().tasks[win._display_task]
    assert t.quiet and t.kind == "display"
    c = notify.centre(win)
    c.clear()
    win._display_running[1].answer = {"error": "no process"}
    win._display_tick()
    assert "_display_task" not in win.__dict__ and tasks.running(win) == []
    assert not any("Checking the display" in m.text for m in c.entries)
    win.__dict__.get("_display_timer") and win._display_timer.stop()


# ======================================================================= notices
def test_a_notice_button_acts_on_the_page_shown(fx_install, win, monkeypatch):
    """Two pages with the same Graphics Builder notice: the bar reuses the rows, and Check
    Again on the tab shown looks at that page."""
    from ctgedit.ui.main_window import MainWindow

    paths = [_copy(fx_install, n) for n in ("A", "B")]
    for p in paths:
        win.open_document(p)
    _pump()
    looked = []
    monkeypatch.setattr(
        MainWindow, "_open_in_builder", staticmethod(lambda q: looked.append(os.path.basename(q)) or True)
    )
    docs = [e["doc"] for e in win._docs]
    for d, p in zip(docs, paths):
        win._builder_notice(d, p)
    win.show_tab(0)
    _pump()
    assert win.doc is docs[0] and "builder" in win._auto_bar.keys()
    win._auto_bar.row("builder").button("Check Again").click()
    assert looked == ["A.ctg"]


def test_a_cancelled_save_as_keeps_the_read_only_notice(fx_install, win):
    p = _copy(fx_install, "RO")
    os.chmod(p, stat.S_IREAD)
    try:
        win.open_document(p)
        assert win.doc.read_only and "readonly" in win._auto_bar.keys()
        ran = []
        win.act_save_as.triggered.disconnect()
        win.act_save_as.triggered.connect(lambda *_: ran.append(1))  # (the user cancels)
        win._auto_bar.row("readonly").button("Save As\u2026").click()
        _pump()
        assert ran == [1] and win.doc.read_only
        assert "readonly" in win._auto_bar.keys() and win._auto_bar.isVisible()
        win._auto_bar.show_for(win.doc)
        assert "readonly" in win._auto_bar.keys()  # (and on the tab shown again)
        win.doc.read_only = False  # (a Save As that went through)
        win._auto_bar.show_for(win.doc)
        assert "readonly" not in win._auto_bar.keys()
    finally:
        os.chmod(p, stat.S_IREAD | stat.S_IWRITE)


# ======================================================================= the busy note
def test_no_busy_note_while_the_step_waits_in_a_box(win, monkeypatch):
    if sys.version_info < (3, 12):
        pytest.skip("sys.monitoring needs Python 3.12")
    # (a longer wait than the window's 400 ms: on a loaded machine opening the box alone can
    # take longer than that, and then the note is right to show until the box is up)
    monkeypatch.setattr(type(win), "BUSY_NOTE_MS", 1500)
    from ctgedit.ui.toast import busy_note

    seen = []

    def close_box():
        m = QtWidgets.QApplication.activeModalWidget()
        # (what counts: no note WHILE the box waits for the user; once it closes the step
        # goes on, and a note then is right)
        seen.append(busy_note(win).isVisible())
        if m is not None:
            m.close()

    # (the box is made and polished first: on a loaded machine its first show alone can take
    # longer than the note's 400 ms, and the step is busy until it shows)
    box = QtWidgets.QMessageBox(
        QtWidgets.QMessageBox.Information, "Template Inputs", "Template X.Y has no inputs.", parent=win
    )
    box.show()
    _pump()
    box.hide()
    QtCore.QTimer.singleShot(2500, close_box)
    with win._busy("Opening the genie's parameters\u2026"):
        box.exec()
    assert seen == [False]
    box.deleteLater()
    # the step goes on after the box: the note comes then
    QtCore.QTimer.singleShot(100, close_box)
    with win._busy("Opening the genie's parameters\u2026"):
        QtWidgets.QMessageBox.information(win, "Template Inputs", "Template X.Y has no inputs.")
        time.sleep(1.8)
        sum(range(10))
    assert win._busy_note_shown


def test_a_shown_busy_note_hides_when_a_box_opens(win):
    from ctgedit.ui.toast import busy_note

    note = busy_note(win)
    note.show_over("Working\u2026")
    assert note.isVisible()
    seen = {}

    def look():
        seen["note shown under the box"] = note.isVisible()
        QtWidgets.QApplication.activeModalWidget().close()

    QtCore.QTimer.singleShot(50, look)
    QtWidgets.QMessageBox.information(win, "Template Inputs", "Template X.Y has no inputs.")
    assert seen == {"note shown under the box": False}


# ======================================================================= the error box
def test_the_error_box_closes_from_its_title_bar_after_copy_details(win):
    from ctgedit.ui.messages import ErrorBox

    box = ErrorBox(win, "Page Not Opened", "Broken.ctg could not be opened.")
    box.setModal(False)
    box.show()
    _pump()
    box.copy_btn.click()
    _pump()
    assert box.isVisible() and box.copy_btn.text() == "Copied"
    box.close()  # (the title bar's X, Alt+F4)
    _pump()
    assert not box.isVisible()


# ======================================================================= the messages
def test_a_failed_genie_update_on_the_page_shown_is_told_once(fx_install, win, monkeypatch):
    import ctgedit.ui.jobs.genie_jobs as gj
    from ctgedit.ui import notify
    from ctgedit.ui.jobs import tasks

    monkeypatch.setattr(tasks, "LONG_S", -1.0)
    monkeypatch.setattr(gj, "apply_answer", lambda doc, ans, label: -1)
    pa = _copy(fx_install, "PA")
    pb = _copy(fx_install, "PB")
    win.open_document(pa)
    doc_a = win.doc
    win.open_document(pb)
    doc_b = win.doc
    jobs = gj.GenieJobs(win)
    jobs.finished.connect(win._genie_job_finished)
    c = notify.centre(win)
    c.clear()
    job = gj.GenieJob(doc_b, {("pumps", "pump1"): set()}, ["pumps.pump1"])
    jobs._begin_task(job)
    jobs._ready.append((job, {}, time.monotonic()))
    jobs._poll()
    errors = [m.text for m in c.entries if m.level == "error"]
    assert len(errors) == 1 and "Could not update pumps.pump1 on this page" in errors[0]
    assert c.unread() == 1
    # a page in another tab: its task tells (the window says nothing for it)
    c.clear()
    job = gj.GenieJob(doc_a, {("pumps", "pump1"): set()}, ["pumps.pump1"])
    jobs._begin_task(job)
    jobs._ready.append((job, {}, time.monotonic()))
    jobs._poll()
    assert [m.text for m in c.entries] == ["Updating pumps.pump1 on PA: failed."]
    jobs.shutdown()


def test_a_key_counts_only_the_same_text():
    _app()
    from ctgedit.ui.notify import Message, MessageCentre

    c = MessageCentre()
    c.add(Message("Saved FxShapes (FxMain).", "success", key="k"))
    m = c.add(Message("Saved FxGenies (FxMain).", "success", key="k"))
    assert m.count == 1 and [x.text for x in c.entries] == ["Saved FxGenies (FxMain)."]
    m = c.add(Message("Saved FxGenies (FxMain).", "success", key="k"))
    assert m.count == 2 and len(c.entries) == 1


def test_saves_of_two_pages_keep_a_message_each(fx_install, win):
    from ctgedit.ui import notify

    for name in ("SA", "SB"):
        win.open_document(_copy(fx_install, name))
        win.doc.create_object("rectangle", (5, 5, 30, 30))
        assert win.save()
    saved = [m for m in notify.centre(win).entries if m.text.startswith("Saved ")]
    assert len(saved) == 2 and {m.count for m in saved} == {1}
    assert "SA" in saved[0].text and "SB" in saved[1].text


# ======================================================================= toasts
def test_the_toast_of_a_page_goes_when_another_tab_is_shown(fx_install, win):
    pa = _copy(fx_install, "PA", LARGE)
    pb = _copy(fx_install, "PB", LARGE)
    win.open_document(pa)
    win.open_document(pb)
    win._delete_indices(_top_units(win.doc, 6))
    t = win.__dict__.get("_ctg_toast")
    assert t is not None and t.isVisible() and t.label.text() == "Deleted 6 objects."
    win.show_tab(next(i for i, e in enumerate(win._docs) if e["doc"].path == pa))
    _pump()
    assert t.isHidden()


def test_a_relink_on_the_linkage_page_shows_a_toast_with_undo(fx_install, win):
    from ctgedit.ui.objprops.sheet import ObjectPropertiesDialog

    win.open_document(fx_install.page("FxSymbols2"))
    doc = win.doc
    (PID_SYMBOL,) = doc.find_objects_by_name("FxThumbSymbol")
    old = doc.symbol_link(PID_SYMBOL)["element"]
    assert old == "pointer2_e_grey"
    dlg = ObjectPropertiesDialog(doc, PID_SYMBOL, parent=win)
    dlg.applied.connect(win._refresh)  # (as the window's modeless host does)
    try:
        dlg.linkage_editor._pending = ("thumbs", "pointer_s_red", "ctl")  # (Browse's pick)
        assert dlg.apply()
        assert doc.symbol_link(PID_SYMBOL)["element"] == "pointer_s_red"
        t = win.__dict__.get("_ctg_toast")
        assert t is not None and t.isVisible()
        assert t.label.text() == "Linked the object to thumbs.pointer_s_red."
        undo = t.buttons[0]
        assert undo.text() == "Undo" and undo.isEnabled()
        undo.click()
        assert doc.symbol_link(PID_SYMBOL)["element"] == old
    finally:
        dlg.deleteLater()


# ======================================================================= the taskbar
def test_turning_the_taskbar_setting_off_clears_the_button(win, monkeypatch):
    from ctgedit.ui.prefs import settings_pages
    from ctgedit.ui.jobs import taskbar, tasks
    from ctgedit.ui.prefs.settings import app_settings

    calls = []
    monkeypatch.setattr(taskbar, "available", lambda: True)
    monkeypatch.setattr(taskbar, "_taskbar", lambda: object())
    monkeypatch.setattr(taskbar, "_hwnd", lambda w: 4242)
    monkeypatch.setattr(taskbar, "_call", lambda ptr, slot, *a: calls.append((slot, a[-1].value)) or 0)
    monkeypatch.setattr(taskbar, "_shown", {})
    app_settings().setValue("ui/taskbar_progress", True)
    tok = tasks.begin("compile", "Compiling FxMain\u2026", window=win)
    assert taskbar._shown.get(4242) == (taskbar.INDETERMINATE, 0, 0)
    calls.clear()
    app_settings().setValue("ui/taskbar_progress", False)
    settings_pages.run_apply_hooks(["ui/taskbar_progress"])  # (the Settings window's apply)
    assert (taskbar._SET_STATE, taskbar.NOPROGRESS) in calls and 4242 not in taskbar._shown
    # off while a task runs, then it ends: the clear is sent too
    app_settings().setValue("ui/taskbar_progress", True)
    tasks.progress(tok, 1, 4)
    assert 4242 in taskbar._shown
    app_settings().setValue("ui/taskbar_progress", False)
    calls.clear()
    tasks.end(tok)
    assert (taskbar._SET_STATE, taskbar.NOPROGRESS) in calls and 4242 not in taskbar._shown
    app_settings().setValue("ui/taskbar_progress", True)
