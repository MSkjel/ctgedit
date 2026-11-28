"""The History panel in the editor window."""

import os

import pytest
from PySide6 import QtCore, QtGui, QtWidgets
from qtkit import app as _app


@pytest.fixture
def win(fx_page):
    """The editor on the fake install's FxShapes (FxMain: one of each primitive, so
    several movable roots and the rectangle FxRect)."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_page("FxShapes"))
    w.resize(1300, 900)
    w.show()
    QtWidgets.QApplication.processEvents()
    if w.doc.dirty:
        w.save()
    w.dock_history.show()
    QtWidgets.QApplication.processEvents()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()


def _state(hd, row):
    from ctgedit.ui.panels.history_dock import ROLE_STATE

    return hd.list.item(row).data(ROLE_STATE)


def _select_and_move(win, roots, dx, dy):
    win._on_canvas_selection(list(roots))
    win.view.set_selection(list(roots))
    win.doc.move_objects(list(roots), dx, dy)
    win._refresh()


def test_the_saved_badge_follows_the_save_and_undone_rows_are_marked(win):
    from ctgedit.ui.panels.history_dock import ROLE_TIME

    doc, hd = win.doc, win.history_dock
    r = doc.page.roots()
    _select_and_move(win, r[:1], 5, 0)
    _select_and_move(win, r[1:2], 0, 5)
    hd.show_document(doc)
    assert _state(hd, 0)["saved"] and not _state(hd, 2)["saved"]
    win.save()
    hd.show_document(doc)
    assert [hd.list.item(k).text() for k in range(3)] == ["Opened", "Move", "Move"]
    assert _state(hd, 2)["saved"] and _state(hd, 2)["current"] and not _state(hd, 0)["saved"]
    assert hd.note.text() == "2 steps. Saved at step 2."
    # a time on every row, the tool tip says when
    assert all(hd.list.item(k).data(ROLE_TIME) for k in range(3))
    assert hd.list.item(1).toolTip().startswith("Made at ")
    win.undo()
    hd.show_document(doc)
    assert _state(hd, 2)["undone"] and _state(hd, 2)["first_undone"] and _state(hd, 2)["saved"]
    assert "Undone: click to do it again" in hd.list.item(2).toolTip()
    assert hd.note.text() == "1 step. 1 can be redone. Saved at step 2."
    assert doc.dirty
    # back to the saved state: nothing unsaved, the tab's dot and the title's mark go
    win.go_to_history(2)
    assert not doc.dirty and "•" not in win.windowTitle()


def test_the_panel_menu_keys_and_footer(win, monkeypatch):
    doc, hd = win.doc, win.history_dock
    r = doc.page.roots()
    _select_and_move(win, r[:1], 5, 0)
    _select_and_move(win, r[1:2], 0, 5)
    hd.show_document(doc)
    # the footer's buttons are Edit > Undo and Redo themselves
    assert hd.undo_btn.defaultAction() is win.act_undo
    assert hd.redo_btn.defaultAction() is win.act_redo
    # Up moves the highlight only; Enter goes to the step
    hd.list.setFocus()
    QtCore.QCoreApplication.processEvents()
    QtWidgets.QApplication.sendEvent(
        hd.list, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Up, QtCore.Qt.NoModifier)
    )
    assert hd.list.currentRow() == 1 and len(doc._undo) == 2
    QtWidgets.QApplication.sendEvent(
        hd.list, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Return, QtCore.Qt.NoModifier)
    )
    assert len(doc._undo) == 1 and len(doc._redo) == 1
    # Copy Steps: one tab-separated row per state
    hd.show_document(doc)
    hd.copy_steps()
    rows = QtWidgets.QApplication.clipboard().text().split("\n")
    assert rows[0].startswith("0\tOpened\t") and rows[2].startswith("2\tMove\t")
    assert rows[2].endswith("undone") and "current" in rows[1]
    # Select Objects of This Step selects what the step moved
    win._on_canvas_selection([])
    hd.selectRequested.emit(1)
    assert win._sel == [r[0]]


def test_undo_selects_and_shows_what_it_changed(win):
    doc = win.doc
    r = doc.page.roots()
    target = r[0]
    _select_and_move(win, [target], 6, 0)
    # click away and scroll the object out of view
    win._on_canvas_selection([])
    win.view.set_selection([])
    box = doc.selection_extent([target], bounds=True)
    win.view.set_zoom(4.0)
    QtWidgets.QApplication.processEvents()
    win.view.centerOn(box[2] + 3000, box[3] + 3000)
    QtWidgets.QApplication.processEvents()

    def in_view():
        b = doc.selection_extent([target], bounds=True)
        seen = win.view.mapToScene(win.view.viewport().rect()).boundingRect()
        return seen.intersects(QtCore.QRectF(b[0], b[1], b[2] - b[0], b[3] - b[1]))

    assert not in_view()
    z = win.view.zoom()
    win.undo()
    assert win._sel == [target] and in_view()
    assert abs(win.view.zoom() - z) < 1e-6  # no zoom change
    from ctgedit.ui.panels import history_dock as HD

    assert win.view in HD._FLASH  # the flash
    win.redo()
    assert win._sel == [target]


def test_the_reveal_setting_turns_it_off(win):
    from ctgedit.ui.prefs.settings import app_settings

    doc = win.doc
    target = doc.page.roots()[0]
    _select_and_move(win, [target], 6, 0)
    win._on_canvas_selection([])
    app_settings().setValue("ui/undo_reveal", False)
    try:
        win.undo()
        assert win._sel == []
    finally:
        app_settings().remove("ui/undo_reveal")


def test_a_property_edit_from_a_window_is_found_without_a_selection(win):
    """No selection: the step's objects are the units the page says it changed."""
    doc = win.doc
    from ctgedit.model.document import ObjectKind

    rect = next(i for i in doc.page.roots() if doc.page.objects[i].kind is ObjectKind.RECTANGLE)
    # (the first derive after the open is a whole one: the page cannot say what it changed)
    assert doc.set_line_width(rect, 3)
    win._refresh()
    win._on_canvas_selection([])
    win._update_actions()
    assert doc.set_line_width(rect, 4)
    win._refresh()
    win.undo()
    assert win._sel == [rect]


def test_settings_rows_and_keys():
    from ctgedit.ui.prefs import settings as ST
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    assert ST.SETTINGS_KEYS["ui/undo_reveal"] is True
    assert ST.SETTINGS_KEYS["opt/history_versions"] == 10
    assert ST.SETTINGS_KEYS["opt/history_mb"] == 200
    rows = {r.key: r for r in SettingsDialog.ROWS}
    assert rows["ui/undo_reveal"].page == "Canvas"
    assert rows["opt/history_versions"].page == "Editing"
    assert rows["opt/history_versions"].section == rows["opt/history_mb"].section == "Safety"


def test_the_help_has_the_sections():
    here = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit", "help", "pages"
    )
    window = open(os.path.join(here, "the-window.md"), encoding="utf-8").read()
    for head in (
        "## History panel",
        "## Changes panel",
        "## Mark Changes Since Save",
        "## Undo and the saved state",
    ):
        assert head in window, head
    assert "Saved" in window and "[[Alt+F5]]" in window
    saving = open(os.path.join(here, "opening-and-saving.md"), encoding="utf-8").read()
    assert "## Earlier versions of a page" in saving and "Restore…" in saving
