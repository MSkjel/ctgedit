"""The Settings window."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.deleteLater()


def _dialog(win, show=True):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win)
    if show:
        dlg.show()
        QtWidgets.QApplication.processEvents()
    return dlg


def _wait_live(cond=None, ms=3000):
    """Past the live-apply pause (until ``cond()`` holds, at most ``ms``)."""
    QtTest.QTest.qWait(200)
    waited = 200
    while cond is not None and not cond() and waited < ms:
        QtTest.QTest.qWait(50)
        waited += 50


def _row(dlg, key):
    return next(r for r in dlg._tabs.rows() if r.key == key)


def test_settings_opens_with_ctrl_comma_and_has_a_gear(win):
    assert win.act_options.shortcut().toString() == "Ctrl+,"
    assert set(win.act_options.property("ctg_keywords")) >= {"preferences", "options", "configure"}
    from ctgedit.ui.style import theme

    assert "settings" in theme._GLYPHS
    dlg = _dialog(win, show=False)
    assert dlg.windowTitle() == "Settings - ctgedit"
    dlg.close()


def test_a_change_applies_at_once_and_undo_changes_puts_it_back(win):
    assert win.act_grid.isChecked()
    dlg = _dialog(win)
    box = dlg._row_widgets["view/grid"]
    assert isinstance(box, QtWidgets.QCheckBox) and box.isChecked()
    assert dlg._btn_undo is not None and not dlg._btn_undo.isEnabled()
    box.setChecked(False)
    _wait_live(lambda: not win.act_grid.isChecked())
    assert not win.act_grid.isChecked() and not win._show_grid  # at once
    assert dlg._btn_undo.isEnabled()
    dlg.undo_changes()
    assert win.act_grid.isChecked() and win._show_grid and box.isChecked()
    assert not dlg._btn_undo.isEnabled()
    # Close keeps what was applied
    dlg._paste_offset.setValue(25)
    _wait_live(lambda: win._paste_offset == 25)
    from ctgedit.ui.prefs.settings import paste_offset

    assert paste_offset() == 25 and win._paste_offset == 25
    dlg.close_live()
    assert paste_offset() == 25


def test_without_live_apply_nothing_applies_before_ok(win):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("ui/settings_live", False)
    dlg = _dialog(win)
    B = QtWidgets.QDialogButtonBox
    names = {b.text().replace("&", "") for b in dlg._buttons.buttons()}
    assert {"OK", "Cancel", "Apply"} <= names and dlg._btn_undo is None
    dlg._row_widgets["view/grid"].setChecked(False)
    _wait_live()
    assert win.act_grid.isChecked()  # not yet
    dlg._buttons.button(B.Apply).click()
    assert not win.act_grid.isChecked() and dlg.isVisible()  # Apply stays open
    dlg._row_widgets["view/grid"].setChecked(True)
    dlg._apply()
    assert win.act_grid.isChecked() and dlg.result() == QtWidgets.QDialog.Accepted


def test_the_search_shows_every_pages_hits_with_counts(win):
    from ctgedit.ui.help import dialog_topic

    dlg = _dialog(win)
    pages = dlg._tabs
    pages.search.setText("colour")
    pages.search.flush()
    shown = {
        pages.tabText(k): pages.list.item(k).text()
        for k in range(pages.count())
        if not pages.list.item(k).isHidden()
    }
    assert {"Editing", "Canvas", "Appearance", "Ribbon"} <= set(shown), shown
    assert shown["Appearance"].startswith("Appearance (") and shown["Appearance"].endswith(")")
    # every hit is on the one scroll at once: rows of several pages are visible together
    visible = [p.title for p in pages.pages if not p.isHidden()]
    assert len(visible) >= 4
    # the matched part of a row's name is marked in the accent colour
    from ctgedit.ui.style.theme import COLORS

    row = next(
        r
        for r in pages.widget(pages.indexOf("Editing")).rows()
        if r.label is not None and "colour" in r.label_text.lower()
    )
    assert COLORS["accent"] in row.label.text() and "<span" in row.label.text()
    # F1 opens the page under the top edge (the current one)
    pages.setCurrentIndex(pages.indexOf("Appearance"))
    assert dialog_topic(dlg) == "settings#appearance"
    pages.search.setText("zzzz no such setting")
    pages.search.flush()
    assert not pages.empty.isHidden()
    pages.search.setText("")
    pages.search.flush()
    assert all(pages.list.item(k).text() == pages.tabText(k) for k in range(pages.count()))
    # a click in the list scrolls to the page
    pages.setCurrentIndex(pages.indexOf("Advanced"))
    QtWidgets.QApplication.processEvents()
    assert pages.scroll.verticalScrollBar().value() > 0
    dlg.close()


def test_modified_marker_reset_to_default_and_at_modified(win):
    dlg = _dialog(win)
    row = _row(dlg, "opt/paste_offset")
    assert not row.modified and "Default: 10 px" in dlg._paste_offset.toolTip()
    dlg._paste_offset.setValue(30)
    QtWidgets.QApplication.processEvents()
    assert row.modified
    pages = dlg._tabs
    pages.search.setText("@modified")
    pages.search.flush()
    shown = [r.key for r in pages.rows() if not r.isHidden()]
    assert shown == ["opt/paste_offset"]
    # the funnel menu does the same
    pages.search.setText("")
    pages.search.flush()
    pages.act_modified_only.setChecked(True)
    assert [r.key for r in pages.rows() if not r.isHidden()] == ["opt/paste_offset"]
    pages.act_modified_only.setChecked(False)
    dlg.reset_row(row)
    assert dlg._paste_offset.value() == 10 and not row.modified
    # Copy Setting Name is the row's key
    assert row.key == "opt/paste_offset"
    sim = pages.widget(pages.indexOf("Simulate"))
    assert sum(1 for r in sim.rows() if r.key.startswith("sim/")) >= 10
    dlg.close()


def test_the_appearance_page_and_its_preview(win):
    dlg = _dialog(win)
    prev = dlg._preview
    assert prev.size() == QtCore.QSize(360, 90)
    before = prev.tokens()["accent"]
    # (the theme's own controls: one control a setting)
    dlg._accent.setCurrentIndex(dlg._accent.findData("green"))
    QtWidgets.QApplication.processEvents()
    assert prev.tokens()["accent"] != before and dlg._accent_choice() == "green"
    dlg._page_edge.setCurrentIndex(dlg._page_edge.findData("none"))
    assert dlg._look_value("view/page_edge") == "none"
    dlg._theme.setCurrentIndex(dlg._theme.findData("light"))
    assert prev.tokens()["scheme"] == "light"
    # the canvas and tab choices are on the page and the search finds them
    page = dlg._tabs.widget(dlg._tabs.indexOf("Appearance"))
    for key in (
        "ui/accent",
        "ui/density",
        "ui/focus_ring",
        "view/surround",
        "view/page_edge",
        "opt/page_shadow",
        "view/grid_style",
        "view/grid_major",
        "opt/grid_outside_page",
        "view/pixel_grid",
        "ui/tab_close_button",
    ):
        assert any(r.key == key for r in page.rows()), key
    # every setting has one control (a check box a ribbon group shares the group list)
    keys = [r.key for r in dlg._tabs.rows() if r.key and r.key != "ui/ribbon_hidden_groups"]
    assert len(keys) == len(set(keys)), sorted({k for k in keys if keys.count(k) > 1})
    dlg._tabs.search.setText("Major line")
    dlg._tabs.search.flush()
    assert [r.key for r in dlg._tabs.rows() if not r.isHidden()] == ["view/grid_major"]
    # theme tiles: three of them
    assert dlg._theme.count() == 3 and dlg._theme.itemText(2) == "Match Windows"
    dlg.undo_changes()
    dlg.close()


def test_export_one_group_and_merge_it_into_a_clean_store(win, tmp_path):
    from ctgedit.ui.prefs import keymap, settings_transfer as T
    from ctgedit.ui.prefs.settings import SETTINGS_KEYS, app_settings, key_group

    st = app_settings()
    keymap.store_user_keys({"act_genie_params": ["Ctrl+Alt+P"]})
    st.setValue("ui/prefs/keymap", "design")
    st.setValue("mru", [r"C:\x\Mine.ctg"])
    path = str(tmp_path / "keys.ini")
    n, groups = T.export_groups(path, ["Keyboard shortcuts"])
    assert groups == ["Keyboard shortcuts"] and n == 2
    # a clean store: other recent pages, no keys
    st.clear()
    st.setValue("mru", [r"C:\y\Theirs.ctg"])
    assert T.file_groups(path) == ["Keyboard shortcuts"]
    n, groups = T.import_groups(path, None, T.MERGE)
    assert n == 2 and groups == ["Keyboard shortcuts"]
    assert keymap.user_keys() == {"act_genie_params": ["Ctrl+Alt+P"]}
    assert st.value("ui/prefs/keymap") == "design"
    assert [str(x) for x in st.value("mru", [], type=list)] == [r"C:\y\Theirs.ctg"]
    assert T.summary(n, groups) == "Imported 2 settings: Keyboard shortcuts."
    # every declared key has a group
    assert all(key_group(k, None) for k in SETTINGS_KEYS)
    # the dialog: import leaves out the recent pages and the window layout
    dlg = T.SettingsTransferDialog(None, "import")
    assert "Recent pages and folders" not in dlg.groups()
    assert "Window layout and workspaces" not in dlg.groups()
    assert dlg.import_mode() == T.MERGE
    dlg.close()


def test_import_from_the_settings_window_says_what_it_took(win, tmp_path):
    from ctgedit.ui.prefs import settings_transfer as T
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    st.setValue("opt/paste_offset", 33)
    st.setValue("styles/list", '[{"name": "Mine", "appearance": {}}]')
    path = str(tmp_path / "all.ini")
    T.export_groups(path)
    st.setValue("opt/paste_offset", 10)
    dlg = _dialog(win)
    assert dlg.import_from(path, ["Options", "Styles"], T.MERGE)
    assert dlg._message.startswith("Imported ") and "Options, Styles." in dlg._message
    assert dlg._paste_offset.value() == 33
    dlg.close()


def test_the_keyboard_table_fits_a_small_settings_window(win):
    """At 700 x 520 the Keyboard table shows every column (Source too)
    and the settings scroll has no sideways scroll bar."""
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win, "Keyboard")
    dlg.resize(700, 520)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    dlg._tabs.show_page("Keyboard")
    QtTest.QTest.qWait(100)
    table = dlg._keys.table
    hdr = table.horizontalHeader()
    assert hdr.length() <= table.viewport().width() + 1
    assert hdr.sectionSize(4) >= table.fontMetrics().horizontalAdvance("Default")
    assert hdr.sectionSize(0) > hdr.sectionSize(4)
    scroll = table.parentWidget()
    while scroll is not None and not isinstance(scroll, QtWidgets.QScrollArea):
        scroll = scroll.parentWidget()
    assert scroll is not None and scroll.horizontalScrollBar().maximum() == 0
    dlg.close_live()
    dlg.deleteLater()


# ------------------------------------------------------ applying, resetting and searching
def test_a_drawing_choice_redraws_the_current_page_live_and_with_apply(win, fx_page):
    """Stacked labels and Draw a composite genie from derive the page shown again (live, and
    with Apply), not only the other open pages. The page is FxMain's FxComposite of the fake
    install (one FxTank composite genie)."""
    from ctgedit.io import reader
    from ctgedit.ui.prefs.settings import app_settings

    win.open_document(fx_page("FxComposite"))
    QtWidgets.QApplication.processEvents()
    calls = []
    real = win.doc._rerender_page
    win.doc._rerender_page = lambda: (calls.append(1), real())[1]

    def turn(combo):
        combo.setCurrentIndex((combo.currentIndex() + 1) % combo.count())

    dlg = _dialog(win)
    before = reader.get_render_option("stacked_labels")
    turn(dlg._stacked)
    _wait_live(lambda: calls)
    assert reader.get_render_option("stacked_labels") != before
    assert len(calls) == 1
    turn(dlg._composite)
    _wait_live(lambda: len(calls) > 1)
    assert len(calls) == 2
    # another change does not redraw the page
    dlg._paste_offset.setValue(dlg._paste_offset.value() + 5)
    _wait_live()
    assert len(calls) == 2
    dlg.undo_changes()
    dlg.close_live()
    calls.clear()
    app_settings().setValue("ui/settings_live", False)
    dlg = _dialog(win)
    turn(dlg._stacked)
    dlg._btn_apply.click()
    assert len(calls) == 1 and dlg.isVisible()
    dlg.reject()


def _active(dlg):
    dlg.show()
    dlg.activateWindow()
    QtTest.QTest.qWaitForWindowActive(dlg, 2000)
    QtWidgets.QApplication.processEvents()


def test_typed_text_and_numbers_apply_when_the_typing_is_finished(win):
    """Live apply never acts on a half-typed path or number: typed text
    applies on Enter, when the box is left or when the window closes; a text set by a button
    (Add…, Reset All Settings) applies at once."""
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.infra import caches

    dlg = SettingsDialog(win, "Pages and libraries")
    _active(dlg)
    applied = []
    real = dlg._apply
    dlg._apply = lambda accept=True: (applied.append(accept), real(accept))[1]
    cleared = []
    real_clear = caches.clear

    def counting_clear(*a, **kw):
        cleared.append(a[:1])
        return real_clear(*a, **kw)

    caches.clear = counting_clear
    try:
        box = dlg._lib_folders
        box.setFocus()
        QtTest.QTest.keyClicks(box, r"D:\Libs", QtCore.Qt.NoModifier, 40)
        _wait_live()
        assert applied == [] and cleared == []
        assert str(app_settings().value("opt/library_folders", "") or "") == ""
        assert _row(dlg, "opt/library_folders").modified  # the mark follows the typing
        QtTest.QTest.keyClick(box, QtCore.Qt.Key_Tab)  # the box is left
        _wait_live(lambda: applied)
        assert len(applied) == 1 and cleared.count(("libraries",)) == 1
        assert app_settings().value("opt/library_folders") == r"D:\Libs"
        # a text set by the window itself applies at once
        box.setText("")
        _wait_live(lambda: len(applied) > 1)
        assert len(applied) == 2 and not app_settings().value("opt/library_folders")
        # a typed number too: on Enter, the box left or the window closed
        spin = dlg._undo_mb
        assert not spin.keyboardTracking()
        spin.setFocus()
        spin.selectAll()
        QtTest.QTest.keyClicks(spin, "500", QtCore.Qt.NoModifier, 40)
        _wait_live()
        assert len(applied) == 2
        # the Simulate engine path, typed and the window closed
        engine = dlg._sim.engine
        engine.setFocus()  # (the number box is left)
        _wait_live(lambda: len(applied) > 2)
        assert len(applied) == 3
        QtTest.QTest.keyClicks(engine, r"C:\Tools\ctsim.exe", QtCore.Qt.NoModifier, 20)
        _wait_live()
        assert len(applied) == 3
        dlg.close_live()
        assert len(applied) == 4
    finally:
        caches.clear = real_clear
    from ctgedit.sim import client as C

    assert app_settings().value("opt/undo_mb", type=int) == 500
    assert app_settings().value(C.SETTING) == r"C:\Tools\ctsim.exe"
    app_settings().remove(C.SETTING)
    dlg.deleteLater()


def test_reset_all_settings_can_be_undone_to_the_values_just_before(win):
    """Reset All Settings resets the rows the search hides too; Undo Reset puts back the
    values from just before it (not the ones the window opened with)."""
    dlg = _dialog(win)
    try:
        dlg._nudge.setValue(9)
        dlg._tabs.search.setText("ribbon")
        assert not dlg._btn_undo_reset.isVisible()
        dlg.reset_all()
        assert dlg._nudge.value() == 1
        assert not dlg._btn_undo_reset.isHidden()
        dlg.undo_reset()
        assert dlg._nudge.value() == 9 and dlg._btn_undo_reset.isHidden()
        # a change after the reset: Undo Reset goes (it would undo that too)
        dlg.reset_all()
        dlg._nudge.setValue(4)
        dlg._update_marks()
        assert dlg._btn_undo_reset.isHidden()
    finally:
        dlg._tabs.search.setText("")
        dlg.close()


def test_a_refined_search_stays_on_a_page_that_still_matches(win):
    """Searching 'page', picking Pages and libraries and typing 'pages' stays there; a page
    the search drops goes to the first one that matches."""
    dlg = _dialog(win)
    try:
        tabs = dlg._tabs
        titles = [p.title for p in tabs.pages]
        k = titles.index("Pages and libraries")
        tabs.search.setText("page")
        tabs.search.flush()
        tabs.setCurrentIndex(k)
        tabs.search.setText("pages")
        tabs.search.flush()
        assert tabs.currentIndex() == k
        tabs.search.setText("zznomatch99")
        tabs.search.flush()
        tabs.search.setText("ribbon")
        tabs.search.flush()
        assert not tabs.list.item(tabs.currentIndex()).isHidden()
    finally:
        dlg._tabs.search.setText("")
        dlg.close()
