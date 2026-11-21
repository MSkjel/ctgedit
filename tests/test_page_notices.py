"""Page notices and status."""

import os
import shutil
import sys
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


@pytest.fixture()
def pages(fx_page):
    """The source pages in the fake install's copy: .small (FxThumb), .big (FxShapes)."""
    return types.SimpleNamespace(small=fx_page("FxThumb"), big=fx_page("FxShapes"))


def _copy(tmp_path, name="Small", src=None):
    assert src is not None
    d = tmp_path / "FxCopies"
    d.mkdir(exist_ok=True)
    p = d / (name + ".ctg")
    shutil.copyfile(src, p)
    return str(p)


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


# ======================================================================= page notices
def test_a_page_changed_on_disk_shows_the_bar_and_reload_reloads(pages, win, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda box: seen.append(box) or 0)
    p = _copy(tmp_path, src=pages.small)
    win.open_document(p)
    old = win.doc
    bar = win._auto_bar
    assert bar.isHidden()
    _touch(p)
    win._check_disk_changes([p])
    assert not seen  # no box: a notice
    assert not bar.isHidden() and "disk" in bar.keys()
    row = bar.row("disk")
    assert "changed by another program" in row.label.text()
    assert [b.text() for b in row.buttons] == ["Reload", "Ignore"]
    from ctgedit.ui import notify

    assert any("changed by another program" in m.text for m in notify.centre(win).entries)
    row.button("Reload").click()
    _pump()
    assert win.doc is not old and not win.doc.dirty  # reloaded
    assert "disk" not in bar.keys() and bar.isHidden()
    win._check_disk_changes([p])  # (told once per change)
    assert bar.isHidden()


def test_with_unsaved_changes_keep_mine_or_reload_and_discard(pages, win, tmp_path, monkeypatch):
    p = _copy(tmp_path, src=pages.small)
    win.open_document(p)
    win.doc.create_object("rectangle", (5, 5, 30, 30))
    win._refresh()
    mine = win.doc
    _touch(p)
    win._check_disk_changes([p])
    row = win._auto_bar.row("disk")
    assert [b.text() for b in row.buttons] == ["Reload and Discard Mine", "Keep Mine"]
    assert "unsaved changes" in row.label.text()
    row.button("Keep Mine").click()
    assert win.doc is mine and win.doc.dirty and "disk" not in win._auto_bar.keys()
    _touch(p, 10)
    win._check_disk_changes([p])
    win._auto_bar.row("disk").button("Reload and Discard Mine").click()
    _pump()
    assert win.doc is not mine and not win.doc.dirty


def test_the_dialog_choice_keeps_the_question_box(pages, win, tmp_path, monkeypatch):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("ui/disk_change_prompt", "dialog")
    seen = []
    monkeypatch.setattr(win, "_exec_disk_prompt", lambda box: seen.append(box.text()))
    p = _copy(tmp_path, src=pages.small)
    win.open_document(p)
    _touch(p)
    win._check_disk_changes([p])
    assert seen and "changed by another program" in seen[0]
    assert "disk" not in win._auto_bar.keys()


def test_each_tab_keeps_its_notices(pages, win, tmp_path):
    from ctgedit.ui.editor import notices

    a = _copy(tmp_path, "A", pages.small)
    b = _copy(tmp_path, "B", pages.small)
    win.open_document(a)
    doc_a = win.doc
    notices.add(doc_a, "test", "warning", "A note on A.", [("Do It", None)])
    assert "test" in win._auto_bar.keys()
    win.open_document(b)
    assert "test" not in win._auto_bar.keys() and win._auto_bar.isHidden()
    win.open_document(a)  # (shows its tab again)
    assert win.doc is doc_a and "test" in win._auto_bar.keys()
    row = win._auto_bar.row("test")
    assert row.label.text() == "A note on A."
    row.button("Do It").click()  # (no action: it only closes)
    assert notices.get(doc_a, "test") is None and win._auto_bar.isHidden()


def test_read_only_damaged_and_builder_notices(pages, win, tmp_path, monkeypatch):
    import stat

    p = _copy(tmp_path, "RO", pages.small)
    os.chmod(p, stat.S_IREAD)
    try:
        win.open_document(p)
        assert win.doc.read_only
        assert "readonly" in win._auto_bar.keys()
        row = win._auto_bar.row("readonly")
        assert row.button("Save As…") is not None
        row.close_btn.click()  # closing hides it for this page
        assert "readonly" not in win._auto_bar.keys()
    finally:
        os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
    # the Graphics Builder notice: Check Again closes it once the page is closed there
    from ctgedit.ui.main_window import MainWindow

    state = {"open": True}
    monkeypatch.setattr(MainWindow, "_open_in_builder", staticmethod(lambda q: state["open"]))
    p2 = _copy(tmp_path, "GB", pages.small)
    win.open_document(p2)
    _pump()
    row = win._auto_bar.row("builder")
    assert row is not None and row.button("Check Again") is not None
    row.button("Check Again").click()  # still open there: kept
    assert "builder" in win._auto_bar.keys()
    state["open"] = False
    win._auto_bar.row("builder").button("Check Again").click()
    assert "builder" not in win._auto_bar.keys()


def test_a_damaged_page_has_a_notice_with_details(pages, win, tmp_path, monkeypatch):
    from ctgedit.format import writer
    from ctgedit.format.page import parse_ctg

    pp = parse_ctg(pages.small)
    pp.page.objects[-1].class_flag = 1
    d = tmp_path / "Proj"
    d.mkdir()
    out = str(d / "D.ctg")
    open(out, "wb").write(writer.serialize_ctg(pp.container, pp.page, pp.attributes))
    boxes = []
    from ctgedit.ui import messages

    monkeypatch.setattr(messages, "_exec", lambda box: boxes.append(box) or None)
    win.open_document(out)
    assert win.doc.damage
    row = win._auto_bar.row("damaged")
    assert row is not None and row.notice.level == "error"
    row.button("Details").click()
    assert boxes and "is damaged" in boxes[0].text()
    assert "damaged" in win._auto_bar.keys()  # (Details keeps the notice)


# ======================================================================= toasts
def test_deleting_six_objects_shows_a_toast_whose_undo_restores_them(pages, win, tmp_path):
    win.open_document(_copy(tmp_path, "Mx", pages.big))
    doc = win.doc
    units = _top_units(doc, 6)
    assert len(units) == 6
    n = len(doc.page.objects)
    win._delete_indices(units)
    t = win.__dict__.get("_ctg_toast")
    assert t is not None and not t.isHidden()
    assert t.label.text() == "Deleted 6 objects."
    assert t.parentWidget() is win.view.viewport()
    undo = t.buttons[0]
    assert undo.text() == "Undo" and undo.isEnabled()
    undo.click()
    assert len(doc.page.objects) == n and t.isHidden()
    # after another edit the toast's Undo is disabled
    win._delete_indices(_top_units(doc, 6))
    t = win._ctg_toast
    doc.create_object("rectangle", (5, 5, 30, 30))
    win._refresh()
    win._update_actions()
    assert not t.buttons[0].isEnabled()


def test_a_small_delete_has_no_toast_and_the_setting_turns_them_off(pages, win, tmp_path):
    from ctgedit.ui.prefs.settings import app_settings

    win.open_document(_copy(tmp_path, "Mx", pages.big))
    win._delete_indices(_top_units(win.doc, 2))
    assert win.__dict__.get("_ctg_toast") is None
    app_settings().setValue("ui/toasts", False)
    win._delete_indices(_top_units(win.doc, 6))
    assert win.__dict__.get("_ctg_toast") is None
    assert win.statusBar().currentMessage() == "Deleted 6 objects."


def test_the_toast_sits_above_the_navigation_bar_and_esc_closes_it(pages, win, tmp_path):
    from ctgedit.ui.toast import show_toast

    win.open_document(_copy(tmp_path, src=pages.small))
    _pump()
    t = show_toast(win, "Pasted 12 objects.", "success", (), 6000)
    nb = win.navbar
    if nb.isVisible():
        assert t.geometry().bottom() < nb.geometry().top()
    vp = win.view.viewport()
    assert abs((t.x() + t.width() / 2) - vp.width() / 2) <= 2
    t.enterEvent(None)
    assert not t._timer.isActive()  # the pointer over it keeps it
    t.leaveEvent(None)
    assert t._timer.isActive()
    QtTest.QTest.keyClick(win.view, QtCore.Qt.Key_Escape)
    assert t.isHidden()


# ======================================================================= error boxes
def test_a_page_that_does_not_open_has_copy_details_and_show_in_explorer(pages, win, tmp_path, monkeypatch):
    bad = str(tmp_path / "Half.ctg")
    data = open(pages.small, "rb").read()
    open(bad, "wb").write(data[: len(data) // 2])
    boxes = []
    from ctgedit.ui import messages

    monkeypatch.setattr(messages, "_exec", lambda box: boxes.append(box) or None)
    assert win.open_page(bad) is False
    box = boxes[0]
    labels = [b.text() for b in box.buttons()]
    assert "Copy Details" in labels and "Open Log" in labels and "Show in Explorer" in labels
    assert "Half.ctg could not be opened" in box.text()
    assert box.informativeText()  # the next step
    assert "see the log" not in (box.text() + box.informativeText()).lower()
    box._help_clicked(box.copy_btn)
    clip = QtWidgets.QApplication.clipboard().text()
    assert "Half.ctg" in clip and "Open documents" in clip
    shown = []
    monkeypatch.setattr(win, "_reveal_in_explorer", lambda p: shown.append(p))
    for b, label, fn in box._actions:
        if label == "Show in Explorer":
            fn()
    assert shown == [bad]


def test_the_error_box_keeps_open_on_copy_details_and_returns_the_action():
    _app()
    from ctgedit.ui.messages import ErrorBox

    ran = []
    box = ErrorBox(
        None,
        "Not Saved",
        "A.ctg is read-only.",
        exc=PermissionError("denied"),
        next_steps=("Use Save As.",),
        actions=(("Save As…", lambda: ran.append(1)),),
    )
    assert "PermissionError: denied" in box.detailedText()
    box.show()
    _pump()
    box.copy_btn.click()
    _pump()
    assert box.isVisible()  # (Copy Details keeps it open)
    save_as = next(b for b, label, _f in box._actions if label == "Save As…")
    QtCore.QTimer.singleShot(0, save_as.click)
    assert box.run() == "Save As…" and ran == [1]


def test_a_failed_save_offers_save_as_in_the_error_box(pages, win, tmp_path, monkeypatch):
    import stat

    p = _copy(tmp_path, src=pages.small)
    win.open_document(p)
    win.doc.create_object("rectangle", (5, 5, 30, 30))
    os.chmod(p, stat.S_IREAD)
    try:
        boxes = []
        from ctgedit.ui import messages

        monkeypatch.setattr(messages, "_exec", lambda box: boxes.append(box) or None)
        assert win.save() is False
        box = boxes[0]
        labels = [b.text() for b in box.buttons()]
        assert "Save As…" in labels and "Copy Details" in labels
        assert "read-only" in box.text()
    finally:
        os.chmod(p, stat.S_IREAD | stat.S_IWRITE)


# ======================================================================= the busy note
def test_a_slow_step_shows_the_busy_note_and_a_quick_one_does_not(win):
    if sys.version_info < (3, 12):
        pytest.skip("sys.monitoring needs Python 3.12")
    with win._busy("Opening the genie's parameters\u2026"):
        time.sleep(0.6)
        sum(range(10))  # (a call: the note shows there)
    assert win._busy_note_shown
    note = win.__dict__["_ctg_busy_note"]
    assert note.text == "Opening the genie's parameters\u2026" and note.isHidden()
    with win._busy("Quick\u2026"):
        time.sleep(0.1)
    assert not win._busy_note_shown
    assert note.shown_count == 1


# ======================================================================= no page open
def test_without_a_page_the_drawing_tools_and_page_commands_are_unavailable(win):
    win._update_actions()
    assert not win.tool_actions["rectangle"].isEnabled()
    assert not win.tool_actions["text"].isEnabled()
    assert win.tool_actions["select"].isEnabled()
    for name in (
        "act_paste_linked",
        "act_break_lock",
        "act_refresh",
        "act_compile",
        "act_run",
        "act_simulate",
    ):
        assert not getattr(win, name).isEnabled(), name
    # the ribbon's Rectangle button is the same command
    from PySide6.QtWidgets import QToolButton

    rect = [b for b in win.findChildren(QToolButton) if b.defaultAction() is win.tool_actions["rectangle"]]
    assert rect and not any(b.isEnabled() for b in rect)


def test_with_a_page_they_are_available(pages, win, tmp_path):
    win.open_document(_copy(tmp_path, src=pages.small))
    assert win.tool_actions["rectangle"].isEnabled() and win.act_refresh.isEnabled()


# ======================================================================= the wording rules
def test_the_message_rules_find_plurals_and_missing_full_stops(tmp_path):
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import check_ui_text as C

    py = tmp_path / "sample.py"
    py.write_text(
        'sb.showMessage("Moved %d object(s)." % n, 4000)\n'
        'sb.showMessage("Select a genie first", 4000)\n'
        'sb.showMessage("Saved %s" % name, 4000)\n'
        'sb.showMessage("Saved.", 4000)\n'
        'notify(w, "Replace them?")\n'
        'notify(w, "Working\u2026")\n'
        'QtWidgets.QMessageBox.warning(self, "Title Without Stop", "No stop here")\n'
        'box.setInformativeText("Run Update Pages now")\n'
        'error_box(self, "Save", "Two error(s) found.")\n'
        'sb.showMessage("Wrong", 4000)  # text-ok\n',
        encoding="utf-8",
    )
    got = sorted((ln, what.split(":")[0]) for _r, ln, what in C.check_python(str(py)))
    assert got == [
        (1, C.RULE_PLURAL),
        (2, C.RULE_STOP),
        (7, C.RULE_STOP),
        (8, C.RULE_STOP),
        (9, C.RULE_PLURAL),
    ], got


def test_the_tree_passes_with_its_baseline():
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import check_ui_text as C

    problems = [p for p in C.check() if "message" in p[2]]
    assert not problems, "\n".join("%s:%d: %s" % p for p in problems)
    base = C.load_baseline()
    assert base, "the baseline lists the older offenders"
    # the files of this package have none left
    for line in base:
        assert not any(
            line.startswith(f)
            for f in (
                "ctgedit/ui/window/tabs.py",
                "ctgedit/ui/window/files.py",
                "ctgedit/ui/window/workspace.py",
                "ctgedit/ui/panels/diag_ui.py",
                "ctgedit/ui/notify.py",
                "ctgedit/ui/main_window.py",
            )
        ), line


# ======================================================================= the Settings rows
def test_the_settings_rows(win):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win)
    try:
        rows = {r.key: r for r in dlg._tabs.rows()}
        for key in ("ui/message_time", "ui/toasts", "ui/taskbar_progress", "ui/disk_change_prompt"):
            assert key in rows, key
        w = dlg._row_widgets["ui/message_time"]
        assert [w.itemData(k) for k in range(w.count())] == ["short", "normal", "long"]
        assert w.currentData() == "normal"
        assert dlg._row_widgets["ui/disk_change_prompt"].currentData() == "bar"
        assert dlg._row_widgets["ui/toasts"].isChecked()
    finally:
        dlg.close()
        dlg.deleteLater()
