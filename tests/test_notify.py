"""The message centre."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app


def _pump(n=8):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture()
def win(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1280, 720)
    yield w
    for x in list(MainWindow._instances):
        for e in getattr(x, "_docs", []):
            e["doc"].dirty = False
        if x in MainWindow._instances:
            MainWindow._instances.remove(x)
        x.hide()


def _small(fx_install):
    """FxShapes in the test's copy of the fake install (FxMain)."""
    return fx_install.page("FxShapes")


# ------------------------------------------------------------------ reading time, wording
def test_the_reading_time_grows_with_the_text_and_warnings_stay_ten_seconds():
    from ctgedit.ui import notify as N

    assert N.reading_ms("Saved.", factor=1.0) == 4000  # at least 4 s
    words = " ".join(["word"] * 20)
    assert N.reading_ms(words, "info", 1.0) == 2500 + 280 * 20
    assert N.reading_ms(words, "warning", 1.0) >= 10000  # a 20-word warning: 10 s
    assert N.reading_ms(" ".join(["w"] * 200), "info", 1.0) == 15000
    assert N.reading_ms(words, "info", 0.7) < N.reading_ms(words, "info", 1.6)


def test_the_message_time_setting_scales_it():
    _app()
    from ctgedit.ui import notify as N
    from ctgedit.ui.prefs.settings import app_settings

    words = " ".join(["word"] * 20)
    normal = N.reading_ms(words)
    app_settings().setValue("ui/message_time", "short")
    assert N.reading_ms(words) == int(normal * 0.7)
    app_settings().setValue("ui/message_time", "long")
    assert N.reading_ms(words) == int(normal * 1.6)
    app_settings().setValue("ui/message_time", "garbled")
    assert N.reading_ms(words) == normal


def test_a_message_gets_its_full_stop():
    from ctgedit.ui.notify import sentence

    assert sentence("Saved") == "Saved."
    assert sentence("Saved.") == "Saved."
    assert sentence("Replace them?") == "Replace them?"
    assert sentence("Working\u2026") == "Working\u2026"
    assert sentence("  ") == ""


def test_plural():
    from ctgedit.ui.names import plural, plural_word

    assert plural(1, "object") == "1 object"
    assert plural(2, "object") == "2 objects"
    assert plural(0, "object") == "0 objects"
    assert plural(3, "match") == "3 matches"
    assert plural(2, "library") == "2 libraries"
    assert plural(2, "day") == "2 days"
    assert plural(2, "child") == "2 children"
    assert plural(4, "unsaved change") == "4 unsaved changes"
    assert plural_word(2, "Has") == "Have"


# ------------------------------------------------------------------ the list and the bell
def test_notify_shows_the_level_and_keeps_the_message(win):
    from ctgedit.ui import notify as N

    sb = win.statusBar()
    N.notify(win, "Two objects not moved", "warning")
    assert sb.currentMessage() == "Two objects not moved."
    assert sb.current_level() == "warning"
    assert sb.field.icon.isVisibleTo(sb.field)
    assert sb._timer.isActive() and sb._timer.interval() >= 10000
    c = N.centre(win)
    assert c.entries[-1].text == "Two objects not moved." and c.entries[-1].level == "warning"
    assert c.unread() == 1
    assert "1 new" in win.status_bell.toolTip()


def test_a_key_replaces_the_earlier_message_and_a_repeat_is_counted(win):
    from ctgedit.ui import notify as N

    c = N.centre(win)
    c.clear()
    for k in range(5):
        N.notify(win, "Locked object not moved %d" % k, "warning", key="locked")
    assert [m.text for m in c.entries] == ["Locked object not moved 4."]
    # (another text of the key replaces it, counted only when it says the same again)
    assert c.entries[0].count == 1
    for _k in range(3):
        N.notify(win, "Locked object not moved", "warning", key="locked")
    assert [m.text for m in c.entries] == ["Locked object not moved."]
    assert c.entries[0].count == 3
    N.notify(win, "Same text")
    N.notify(win, "Same text")
    assert c.entries[-1].text == "Same text." and c.entries[-1].count == 2
    assert len(c.entries) == 2


def test_the_list_keeps_two_hundred(win):
    from ctgedit.ui import notify as N

    c = N.centre(win)
    for k in range(260):
        N.notify(win, "Message %d" % k)
    assert len(c.entries) == N.HISTORY_MAX == 200
    assert c.entries[-1].text == "Message 259."


def test_plain_status_messages_are_kept_but_not_hints_or_progress(win):
    from ctgedit.ui import notify as N

    c = N.centre(win)
    c.clear()
    sb = win.statusBar()
    sb.showMessage("Moved the guideline", 3000)  # a hint: not kept
    sb.showMessage("Compiling 3 of 9\u2026", 8000)  # progress: not kept
    sb.showMessage("Copied 2 objects.", 4000)
    assert [m.text for m in c.entries] == ["Copied 2 objects."]
    assert sb.currentMessage() == "Copied 2 objects."  # (the text as written)
    sb.clearMessage()
    assert sb.currentMessage() == ""


def test_the_bell_lists_newest_first_marks_read_and_clears(win):
    from ctgedit.ui import notify as N

    win.show()
    _pump()
    c = N.centre(win)
    c.clear()
    ran = []
    N.notify(win, "First")
    N.notify(win, "Second went wrong", "error", actions=[("Retry", lambda: ran.append(1))])
    assert c.unread() == 1 and c.worst_unread() == "error"
    win.status_bell.toggle_list()
    pop = win.status_bell.popover
    _pump()
    assert pop.isVisible()
    texts = [w.text() for w in pop.rows.findChildren(QtWidgets.QLabel) if w.wordWrap()]
    assert texts.index("Second went wrong.") < texts.index("First.")
    assert c.unread() == 0  # (opened: read)
    retry = [b for b in pop.rows.findChildren(QtWidgets.QToolButton) if b.text() == "Retry"]
    assert retry
    retry[0].click()
    assert ran == [1] and not pop.isVisible()  # (an action closes the list)
    win.status_bell.toggle_list()
    pop.clear_btn.click()
    _pump()
    assert c.entries == []
    pop.close()


def test_view_messages_opens_the_list_too(win):
    win.show()
    _pump()
    win.act_messages.trigger()
    _pump()
    assert win.status_bell.popover is not None and win.status_bell.popover.isVisible()
    win.status_bell.popover.close()


def test_a_message_of_a_dialog_goes_to_its_editor_window(win):
    from ctgedit.ui import notify as N

    dlg = QtWidgets.QDialog(win)
    N.notify(dlg, "From the dialog")
    assert win.statusBar().currentMessage() == "From the dialog."


def test_a_warning_goes_to_the_log(win, caplog):
    import logging
    from ctgedit.ui import notify as N

    with caplog.at_level(logging.INFO, logger="ctgedit.messages"):
        N.notify(win, "Something to know", "warning")
    assert any("Something to know" in r.getMessage() for r in caplog.records)


def test_the_message_field_keeps_a_message_under_the_pointer(win):
    from ctgedit.ui import notify as N

    sb = win.statusBar()
    N.notify(win, "Keep me")
    sb.hold(True)
    assert not sb._timer.isActive()
    sb.hold(False)
    assert sb._timer.isActive() and sb._timer.interval() >= sb.HOLD_MIN_MS


# ------------------------------------------------------------------ the open checks, Undo
def test_every_open_check_message_is_kept_in_the_list(win, fx_install, monkeypatch):
    """Each of the open checks' status messages is a message of its own in the bell's list."""
    from ctgedit.ui import notify as N

    sent = []
    real = N.notify

    def spy(window, text, *a, **k):
        msg = real(window, text, *a, **k)
        sent.append(msg.text)
        return msg

    monkeypatch.setattr(N, "notify", spy)
    from ctgedit.ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_open_in_builder", staticmethod(lambda p: True))
    from ctgedit.editing import EditableDocument

    monkeypatch.setattr(EditableDocument, "template_status", lambda self: ("stale", "tpl.normal"))
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/auto_update_template", False)
    p = _small(fx_install)
    win.open_document(p)
    _pump()
    texts = [m.text for m in N.centre(win).entries]
    assert any(t.startswith("Opened FxShapes (FxMain)") for t in texts), texts
    assert any("open in the Graphics Builder" in t for t in texts)
    assert any("template tpl.normal" in t for t in texts)
    assert sent and all(t in texts for t in sent), (sent, texts)
    # the open message names the page and its project, the path is in its tool tip
    opened = next(m for m in N.centre(win).entries if m.text.startswith("Opened"))
    assert opened.detail == p
    # the template that was not applied offers Update Template
    tpl = next(m for m in N.centre(win).entries if "template tpl.normal" in m.text)
    assert [a.label for a in tpl.actions] == ["Update Template"]


def test_undo_runs_only_while_its_step_is_the_newest(win, fx_install):
    from ctgedit.ui import notify as N

    win.open_document(_small(fx_install))
    doc = win.doc
    doc.create_object("rectangle", (5, 5, 30, 30))
    win._refresh()
    act = N.undo_action(win)
    assert act.can_run()
    doc.create_object("rectangle", (40, 40, 60, 60))
    assert not act.can_run()  # another step came after it
    n = len(doc._undo)
    act.run()
    assert len(doc._undo) == n  # nothing undone
    act2 = N.undo_action(win)
    act2.run()
    assert len(doc._undo) == n - 1


def test_level_icons_follow_the_theme():
    _app()
    from ctgedit.ui import notify as N

    for level in N.LEVELS:
        ic = N.level_icon(level, 16)
        assert not ic.isNull()
        assert ic is N.level_icon(level, 16)  # (made once)


def test_a_hint_shows_but_is_not_kept(win):
    """The first run's keys hint is a hint: shown, not in the bell's list."""
    from ctgedit.ui import notify as N

    assert not any(m.text.startswith("Ready.") for m in N.centre(win).entries)
    sb = win.statusBar()
    sb.show_hint("Ctrl+K finds any command.", 6000)
    assert sb.currentMessage() == "Ctrl+K finds any command."
    assert not any(m.text.startswith("Ready.") for m in N.centre(win).entries)


def test_opening_a_content_page_keeps_every_open_check_message(win, fx_install, monkeypatch):
    """FxSaContent opened with its open checks on (the template, the symbols, the placeholders;
    not the genie helper process): every message the open posted is still in the bell's list
    afterwards."""
    from ctgedit.ui import notify as N

    sent = []
    real = N.notify

    def spy(window, text, *a, **k):
        msg = real(window, text, *a, **k)
        sent.append(msg.text)
        return msg

    monkeypatch.setattr(N, "notify", spy)
    from ctgedit.ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_open_in_builder", staticmethod(lambda p: True))
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/auto_update_genies", False)
    win.open_document(fx_install.page("FxSaContent", "FxSa"))
    _pump(20)
    texts = [m.text for m in N.centre(win).entries]
    assert any(t.startswith("Opened FxSaContent (FxSa)") for t in texts), texts
    assert any("also open in the Graphics Builder" in t for t in texts), texts
    assert len(sent) >= 2 and all(t in texts for t in sent), (sent, texts)
    assert "builder" in win._auto_bar.keys()  # (and the page's notice)
