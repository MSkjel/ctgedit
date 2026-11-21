"""Messages offer the next step."""

import glob
import os
import re
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest  # noqa: E402
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: a content page of the fake install (on a template, with genies)
PAGE = ("FxSaContent", "FxSa")
HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "ctgedit")


def _pump(n=4):
    app = _app()
    for _ in range(n):
        app.processEvents()


@pytest.fixture()
def win(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    _pump()
    w._fx = fx_install
    yield w
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()


def _open(w):
    w.open_document(w._fx.page(*PAGE))
    _pump()


@pytest.fixture()
def boxes(monkeypatch):
    """The error / result boxes shown, caught instead of run (ui/messages._exec)."""
    out = []
    from ctgedit.ui import messages

    monkeypatch.setattr(messages, "_exec", lambda box: out.append(box) or None)
    return out


def _labels(box):
    return [b.text() for b in box.buttons()]


def _run_action(box, label):
    for b, lab, fn in box._actions:
        if lab == label:
            fn()
            return True
    return False


# ------------------------------------------------------------------ 1: nothing says 'see the log'
def test_nothing_the_ui_shows_says_see_the_log():
    """A box has Open Log (ui/messages), a status line gets the Open Log button beside it."""
    bad = []
    for p in glob.glob(os.path.join(PKG, "**", "*.py"), recursive=True):
        src = open(p, encoding="utf-8").read()
        for m in re.finditer(r"\(see the log\)", src, re.IGNORECASE):
            line = src[: m.start()].count("\n") + 1
            bad.append("%s:%d" % (os.path.relpath(p, PKG), line))
    assert not bad, bad


def test_a_find_failure_puts_open_log_beside_the_status_line(win):
    _open(win)
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

    dlg = FindReplaceDialog(win.doc, win, replace_mode=True)
    try:
        assert dlg._b_log.isHidden() and dlg._b_backup.isHidden()
        dlg._set_closed_note("The closed pages could not be searched.", log=True)
        assert not dlg._b_log.isHidden() and "see the log" not in dlg._closed_note
        dlg._set_closed_note("")
        assert dlg._b_log.isHidden()
    finally:
        dlg.deleteLater()


def test_a_users_update_that_could_not_start_offers_the_log(win, monkeypatch):
    _open(win)
    from ctgedit.ui import notify
    from ctgedit.ui.window import library as L

    monkeypatch.setattr(L, "_write_text", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert win._start_users_run("pumps.ctl", "pump1", "pumps.pump1") is False
    msg = notify.centre(win).newest_first()[0]
    assert msg.level == "error" and "see the log" not in msg.text
    assert [a.label for a in msg.actions] == ["Open Log"]


# --------------------------------------------------- 2, 3, 4: the batch results offer what is next
# (the result of a run that wrote files is the jobs package's results window, ui/jobs/run_results,
# through MainWindow._show_run_results)
def test_the_update_pages_result_offers_compile_and_the_backup_folder(win, tmp_path, monkeypatch):
    from ctgedit.ui.jobs.run_results import RunResultsDialog

    backup = tmp_path / "update-pages-1"
    backup.mkdir()
    dlg = RunResultsDialog(
        "Update Pages",
        ["1 page updated.", "Compile the project to see the changes in the runtime."],
        win,
        backup=str(backup),
        compile_offer=True,
    )
    try:
        assert dlg.buttons() == ["Copy", "Open Log", "Open Backup Folder", "Compile Project"]
        opened = []
        monkeypatch.setattr(
            QtGui.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True
        )
        dlg.open_backup_folder()
        assert [os.path.normcase(x) for x in opened] == [os.path.normcase(str(backup))]
    finally:
        dlg.deleteLater()
    # nothing updated, no backup: the window's own buttons
    plain = RunResultsDialog("Update Pages", ["Nothing to update."], win)
    try:
        assert plain.buttons() == ["Copy", "Open Log"]
    finally:
        plain.deleteLater()


def test_the_update_pages_summary_says_registered_not_rows():
    from ctgedit.ui.window.library import LibraryCommands

    rep = types.SimpleNamespace(
        pages=2,
        projects=["A"],
        stale=2,
        updated=["p1", "p2"],
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
        rows_pending=["p1"],
        rows_written=["p2"],
        warnings=[],
        not_checked=0,
        errors=[],
        recovered=[],
        records=[],
        links_dropped=[],
        unchanged=[],
    )
    text = "\n".join(LibraryCommands._update_pages_summary(rep, None, (0, 0, 0, 0)))
    assert "not registered in their project yet" in text and "p1" in text
    assert "Registered in their project now" in text and "p2" in text


def test_the_pack_result_names_the_backup_and_offers_it(tmp_path):
    """Pack Libraries' lines (its results window gets Open Backup Folder from the folder)
    name the backup folder and Tools > Restore Backup, and the files left as they were."""
    from ctgedit.ui.window.library import LibraryCommands

    folder = str(tmp_path / "pack-1")
    text = "\n".join(LibraryCommands._pack_result_lines(2, 10, 1, True, folder, []))
    assert text.startswith("2 library files packed, 10 bytes reclaimed.")
    assert folder in text and "Restore Backup" in text
    assert "1 unused composite genie instance deleted" in text
    text = "\n".join(
        LibraryCommands._pack_result_lines(1, 5, 0, True, folder, [(str(tmp_path / "B.ctl"), "in use")])
    )
    assert "left as they were" in text and "B.ctl: in use" in text and folder in text
    # nothing packed: no backup to name
    text = "\n".join(LibraryCommands._pack_result_lines(0, 0, 0, False, folder, []))
    assert "backup" not in text.lower() and folder not in text


def test_restore_backup_with_none_names_their_folder_and_opens_it(win, boxes, monkeypatch):
    from ctgedit.infra import backups

    monkeypatch.setattr(backups, "list_backups", lambda *a, **k: [])
    win._restore_backup_dialog()
    box = boxes[-1]
    assert box.icon() == QtWidgets.QMessageBox.Information
    assert "Open Backup Folder" in _labels(box)
    assert backups.backups_root() in box.informativeText()
    assert "Replace All" in box.text()
    opened = []
    from ctgedit.ui import messages

    monkeypatch.setattr(messages, "open_folder", lambda p: opened.append(p))
    assert _run_action(box, "Open Backup Folder") and opened == [backups.backups_root()]


def test_the_restore_result_offers_compile_project(tmp_path):
    """Restore Backup's lines and whether its results window offers Compile Project."""
    from ctgedit.infra.backups import BackupInfo
    from ctgedit.ui.window.library import LibraryCommands

    folder = tmp_path / "update-pages-2"
    folder.mkdir()
    b = BackupInfo(str(folder), "update-pages", time.time(), [{"page": "P"}])
    lines, offer = LibraryCommands._restore_result_lines(b, ["P"], [])
    assert offer and "Compile the project" in "\n".join(lines) and "P" in lines[0]
    # a pack backup: nothing to compile
    lines, offer = LibraryCommands._restore_result_lines(
        BackupInfo(str(folder), "pack", time.time(), []), ["A.ctl"], ["B.ctl: in use"]
    )
    assert not offer and "Not restored:" in lines and "Compile" not in "\n".join(lines)


# ------------------------------------------------------------- 5: the guidelines are explained
def test_guidelines_setup_says_how_the_page_handles_them():
    _app()
    from ctgedit.ui.dialog.dialogs import GuidelinesDialog

    dlg = GuidelinesDialog([100, 200], [180], True, True)
    try:
        note = dlg.note.text()
        for want in ("drag a guideline to move it", "Ctrl", "page edge", "ruler", "right-click"):
            assert want in note, note
        dlg.select("h", 180)
        assert dlg._rb_h.isChecked() and dlg._list.currentItem().text() == "180"
        assert dlg._sp_pos.value() == 180
        dlg.select("v", 200)
        assert dlg._rb_v.isChecked() and dlg._list.currentItem().text() == "200"
    finally:
        dlg.deleteLater()


def test_the_guideline_menu_items_show_their_hints_in_the_status_bar(win):
    for a in (
        win.act_show_guides,
        win.act_rulers,
        win.act_guidelines,
        win.act_clear_guides,
        win.act_snap_guides,
    ):
        assert a.statusTip().endswith("."), a.text()
    assert "Ctrl" in win.act_show_guides.statusTip()
    assert "page edge" in win.act_show_guides.statusTip()
    assert "ruler" in win.act_rulers.statusTip()


def test_a_right_click_on_a_guideline_offers_its_position_and_delete(win):
    _open(win)
    view = win.view
    view.set_guidelines([], [180])
    view.set_guides_visible(True)
    from ctgedit.ui.window import context_menu

    # a spot on the guideline where no object is
    x = next(
        x for x in range(20, 1400, 7) if not view._topmost_unit(view.mapFromScene(QtCore.QPointF(x, 180)))
    )
    vp = view.mapFromScene(QtCore.QPointF(x, 180))
    assert view._guide_at(vp) == ("h", 180)
    gpos = view.viewport().mapToGlobal(vp)
    menu = context_menu.build_canvas_menu(win, gpos, [])
    texts = [a.text() for a in menu.actions()]
    assert texts[:2] == ["Guideline Position…", "Delete Guideline"], texts
    assert all(a.statusTip() for a in menu.actions()[:2])
    menu.actions()[0].trigger()
    dlg = win._dialogs.get("guides")
    assert dlg is not None and dlg._rb_h.isChecked() and dlg._sp_pos.value() == 180
    dlg.reject()
    menu.actions()[1].trigger()
    assert view.guidelines() == ([], [])
    assert "Deleted the guideline at 180 px" in win.statusBar().currentMessage()
    # off a guideline: no guideline items
    menu2 = context_menu.build_canvas_menu(
        win, view.viewport().mapToGlobal(view.mapFromScene(QtCore.QPointF(x, 60))), []
    )
    assert "Delete Guideline" not in [a.text() for a in menu2.actions()]
    menu.deleteLater()
    menu2.deleteLater()


# -------------------------------------------------------- 6: the capture group tip stays
def test_replace_with_keeps_the_group_tip(win):
    _open(win)
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog
    from ctgedit.ui.style.theme import RegexToggle

    dlg = FindReplaceDialog(win.doc, win, replace_mode=True)
    try:
        assert "\\1" in dlg._ed_repl.toolTip() and "\\g<name>" in dlg._ed_repl.toolTip()
        dlg._ed_repl.setText("tank")
        assert "\\1" in dlg._ed_repl.toolTip()  # (a placeholder would be gone)
        dlg._find_regex.set_on(True)
        assert "\\1" in dlg._find_regex.action.toolTip()
        assert "\\1" in dlg._ed_repl.placeholderText()
        assert "\\1" not in RegexToggle.TIP_ON  # (the other boxes: no Replace)
    finally:
        dlg._find_regex.set_on(False)
        dlg.deleteLater()


# ------------------------------------------------- 7, 9, 13, 14, 15: the menus describe themselves
def _menu_actions(bar):
    """(path, action) of every action under the menu bar, the dynamic menus filled."""
    out = []

    def walk(menu, path):
        with_show = getattr(menu, "aboutToShow", None)
        if with_show is not None:
            with_show.emit()
        for a in menu.actions():
            if a.isSeparator():
                continue
            name = a.text().replace("&", "")
            if a.menu() is not None:
                out.append((" > ".join(path + [name]) + " [menu]", a))
                walk(a.menu(), path + [name])
            else:
                out.append((" > ".join(path + [name]), a))

    for top in bar.actions():
        if top.menu() is not None:
            walk(top.menu(), [top.text().replace("&", "")])
    return out


def test_every_menu_bar_command_has_a_status_bar_line(win):
    missing = [p for p, a in _menu_actions(win.menuBar()) if not a.statusTip().strip()]
    assert not missing, "\n".join(missing)
    _open(win)
    missing = [p for p, a in _menu_actions(win.menuBar()) if not a.statusTip().strip()]
    assert not missing, "\n".join(missing)


def test_a_greyed_menu_command_says_what_it_needs_in_the_status_bar(win):
    from ctgedit.ui.style import tips

    assert win.doc is None and not win.act_goto.isEnabled()
    assert win.act_goto.statusTip().endswith(" Open a page first.")
    # the rich tip's sentence is the description alone: the need has a line of its own
    assert "Open a page first" not in tips.description(win.act_goto)
    assert tips.rich_tip(win.act_goto).count("Open a page first.") == 1
    assert win.act_edit_template.statusTip().endswith("Open a page with a template.")
    _open(win)
    assert win.act_goto.isEnabled()
    assert not win.act_goto.statusTip().endswith("Open a page first.")
    assert win.act_goto.statusTip() == tips.description(win.act_goto)


def test_the_labels_menu_explains_its_choices(win):
    assert "@( )" in win.labels_menu.menuAction().statusTip()
    items = [a for a in win.labels_menu.actions() if not a.isSeparator()]
    assert len(items) >= 3
    for a in items:
        assert a.statusTip(), a.text()
    assert "stored" in items[0].statusTip() and "native" in items[1].statusTip()


def test_cursor_keys_slow_speed_says_what_it_does(win):
    from ctgedit.ui.prefs.settings import app_settings

    assert "1 px" in win.act_slow_keys.statusTip()
    assert win.act_slow_mouse.statusTip()
    assert not win.act_slow_keys.isChecked()
    win.act_slow_keys.trigger()
    assert win.act_slow_keys.isChecked()
    assert "1 px" in win.statusBar().currentMessage()
    # the step changed in Settings while the switch is on: the status bar says which wins
    app_settings().setValue("view/nudge_step", 5)
    win.apply_canvas_options()
    msg = win.statusBar().currentMessage()
    assert "5 px" in msg and "Cursor Keys Slow Speed" in msg
    win.act_slow_keys.trigger()
    assert "5 px" in win.statusBar().currentMessage()
    app_settings().remove("view/nudge_step")


def test_the_tags_panel_command_is_named_for_what_it_is(win):
    from ctgedit.ui.prefs.keymap import KEYWORDS_PROP

    a = win.act_var_tags
    assert a.text() == "Tags Panel" and a.shortcut().toString() == "Ctrl+T"
    assert "variable tags" in list(a.property(KEYWORDS_PROP))
    assert "Tags panel" in a.statusTip()


# ------------------------------------------------------------------ 8: the magnifier's one name
def test_the_magnifier_has_one_name(win):
    from ctgedit.ui.editor import navbar
    from ctgedit.ui.dialog import palette
    from ctgedit.ui.prefs.keymap import KEYWORDS_PROP

    a = win.act_zoom_window
    assert a.text() == "Magnifier" and a.shortcut().toString() == "F10"
    assert "magnifier" in a.statusTip().lower()
    assert "show zoom" in list(a.property(KEYWORDS_PROP))
    assert [b for b in navbar.BUTTONS if b[1] == "act_zoom_window"][0][2] == "Magnifier"
    a.setChecked(True)
    win._toggle_zoom_window()
    assert win._zoom_win.windowTitle() == "Magnifier"
    a.setChecked(False)
    win._toggle_zoom_window()
    assert palette._alias_rank("show zoom", "Magnifier") >= 0


# ---------------------------------------------------- 10: Page History says where and offers Settings
def test_page_history_offers_settings_and_the_folder(win, monkeypatch):
    _open(win)
    from ctgedit.infra import history as H
    from ctgedit.ui.dialog import palette
    from ctgedit.ui.dialog.history_versions import PageHistoryDialog

    monkeypatch.setattr(H, "enabled", lambda: False)  # (versions off)
    dlg = PageHistoryDialog(win.doc, win)
    try:
        assert "Settings…" in dlg.intro.text() and "Editing > Safety" not in dlg.intro.text()
        assert dlg.btn_settings is not None and dlg.btn_folder is not None
        assert H.history_root() in dlg.use_label.text()
        assert not dlg.btn_clear.isEnabled()
        shown = []
        monkeypatch.setattr(
            palette,
            "show_setting",
            lambda w, page, search="", key="", exec_=True: shown.append((page, search, key)),
        )
        dlg.btn_settings.click()
        assert shown == [("Editing", "Keep earlier versions of each page", "opt/history_versions")]
        opened = []
        from ctgedit.ui import messages

        monkeypatch.setattr(messages, "open_folder", lambda p: opened.append(p))
        dlg.btn_folder.click()
        assert opened == [H.history_root()]  # (no versions yet: the root)
        assert dlg.versions_folder().startswith(H.history_root())
    finally:
        dlg.deleteLater()


# ---------------------------------------------------- 11: the Update Pages window's tips
def test_update_pages_tips_name_the_backup_folder_and_automatic(fx_install):
    _app()
    from ctgedit.infra import backups
    from ctgedit.ui.dialog.dialogs import UpdatePagesDialog
    from ctgedit.ui.prefs.settings import HELPER_MB

    dlg = UpdatePagesDialog(fx_install.project("FxMain"), None)
    try:
        assert backups.backups_root() in dlg._backup.toolTip()
        assert "Restore Backup" in dlg._backup.toolTip()
        tip = dlg._procs.toolTip()
        assert tip.startswith("Automatic:") and "at most 4" in tip
        assert "%d MB" % HELPER_MB in tip and "pages" in tip
    finally:
        dlg.deleteLater()


# ---------------------------------------------------- 12: the settings choices carry their figures
def test_settings_choices_carry_their_figures():
    from ctgedit.ui.editor import navigator, viewnav
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    rows = {r.key: r for r in SettingsDialog.ROWS}
    labels = {v: lab for v, lab in rows["view/handle_size"].choices}
    from ctgedit.ui.editor import canvas_look

    for name, px in canvas_look.HANDLE_SIZES.items():
        assert "(%d px)" % px in labels[name], labels
    labels = {v: lab for v, lab in rows["canvas/pointer_reach"].choices}
    assert "(" in labels["near"] and "3 px more" in labels["medium"]
    labels = {v: lab for v, lab in rows["view/navigator_size"].choices}
    for name, (w, h) in navigator.MAP_SIZES.items():
        assert "(%d x %d px)" % (w, h) in labels[name], labels
    labels = {v: lab for v, lab in rows["opt/edge_scroll_speed"].choices}
    for name, f in viewnav.EDGE_SPEED_FACTORS.items():
        assert "%d px" % int(viewnav.EDGE_SPEED * f) in labels[name], labels
    assert "backups folder" in rows["opt/backup_keep"].tip


# ---------------------------------------------------- 16: the recovery note offers the pages
def test_the_recovery_note_offers_the_pages_and_the_log(win, monkeypatch):
    page = win._fx.page(*PAGE)
    dbf = os.path.join(os.path.dirname(page), "pages.DBF")
    note = {"label": "register page %s" % PAGE[0], "restored": [dbf], "skipped": []}
    assert win._recovery_pages(note) == [page]
    opened = []
    monkeypatch.setattr(win, "open_files", lambda ps: opened.append(tuple(ps)) or [])
    win.report_journal_recovery([note])
    box = win._journal_box
    assert box.isVisible() and not box.isModal()
    assert "not registered in their project yet" in box.text()
    assert "database rows" not in box.text()
    labels = _labels(box)
    assert "Open These Pages" in labels and "Open Log" in labels
    btn = next(b for b in box.buttons() if b.text() == "Open These Pages")
    btn.click()
    assert opened == [(page,)]
    # the start-up note of pages saved but not registered carries their files too
    from ctgedit import app
    from ctgedit.io import pendingrows

    monkeypatch.setattr(pendingrows, "listing", lambda: [{"page": page}])
    notes = app.pending_rows_notes()
    assert notes[0]["pages"] == [page] and "not registered" in notes[0]["text"]
    win.report_journal_recovery(notes)
    box = win._journal_box
    assert box.windowTitle() == "Pages Not Registered Yet"
    assert "Open These Pages" in _labels(box)
    box.close()
    # a journal of another kind names no page: no button for it
    assert win._recovery_pages({"label": "delete rows in FxSa", "restored": [dbf]}) == []


# ---------------------------------------------------- 17: Replace All names its backup folder
def test_replace_all_in_closed_pages_names_the_backup_folder(win, tmp_path, monkeypatch):
    _open(win)
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog
    from ctgedit.features import find_replace as FR

    dlg = FindReplaceDialog(win.doc, win, replace_mode=True)
    try:
        folder = tmp_path / "replace-1"
        folder.mkdir()
        dlg._ed_find.setText("zzz-not-on-the-page")
        dlg._backup_folder = str(folder)
        job = object()
        dlg._job = job
        monkeypatch.setattr(FR, "replace_all", lambda *a, **k: FR.ReplaceReport())
        dlg._replaced_closed(job, {"updated": ["p1", "p2"], "replaced": 2}, dlg.options())
        text = dlg._status.text()
        assert str(folder) in text and "Restore Backup" in text
        assert not dlg._b_backup.isHidden()
        opened = []
        from ctgedit.ui import messages

        monkeypatch.setattr(messages, "open_folder", lambda p: opened.append(p))
        dlg._b_backup.click()
        assert opened == [str(folder)]
        assert "backups folder" in dlg._rb_project.toolTip()
        # one way back: Undo Replace All puts the closed pages back from this run's backup
        from ctgedit.io import updatepages

        put = []
        monkeypatch.setattr(
            updatepages,
            "restore_backup",
            lambda f, names, **k: (
                put.append((f, list(names), k.get("libraries")))
                or updatepages.RestoreReport(restored=list(names))
            ),
        )
        assert not dlg._b_undo_all.isHidden() and "Undo Replace All" in text
        dlg.undo_replace_all()
        assert put == [(str(folder), ["p1", "p2"], False)]
        assert "undone in 2 pages" in dlg._status.text() and dlg._b_undo_all.isHidden()
    finally:
        dlg.deleteLater()


def test_symbols_not_found_offers_the_log_for_an_unreadable_library():
    _app()
    from ctgedit.diag import open_report as OR
    from ctgedit.ui.panels.diag_ui import UnresolvedReferencesDialog

    u = types.SimpleNamespace(
        reference="lib.sym",
        library="lib",
        ext="ctl",
        reason=OR.REASON_UNREADABLE,
        count=1,
        files=[],
        text=lambda: "lib.sym",
    )
    rep = types.SimpleNamespace(path="P.ctg", unresolved=[u], search_paths=[], includes=[])
    dlg = UnresolvedReferencesDialog(rep)
    try:
        assert "see the log" not in OR.REASON_UNREADABLE
        assert dlg.btn_log.text() == "Open Log"
    finally:
        dlg.deleteLater()
    u.reason = OR.REASON_NO_ELEMENT
    dlg = UnresolvedReferencesDialog(rep)
    try:
        assert not hasattr(dlg, "btn_log")
    finally:
        dlg.deleteLater()
