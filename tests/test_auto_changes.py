"""Changes ctgedit makes on its own, and how they're shown."""

import os

import pytest


@pytest.fixture()
def window(fx_page):
    """The main window on FxMain's FxShapes in the fake install's copy (a page that opens
    with no automatic change: tests/test_fixtures.py checks every fixture page)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    p = fx_page("FxShapes")
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    w.open_files([str(p)])
    app.processEvents()
    yield w
    w.doc.dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.close()
    app.processEvents()


def test_an_automatic_step_is_listed_and_undone_from_the_bar(window):
    w = window
    doc = w.doc
    assert not doc.dirty and w._auto_bar.isHidden()
    w._auto_step(doc, "a test update", doc.create_object, "rectangle", (5, 5, 30, 30))
    assert doc.dirty and not w._auto_bar.isHidden()
    assert "a test update" in w._auto_bar.label.text()
    assert w._only_auto_changes(doc)
    w._undo_auto_changes()
    assert not doc.can_undo() and w._auto_bar.isHidden()


def test_the_bar_can_be_turned_off(window):
    """Settings > Pages and libraries > Show a bar listing the automatic updates: off, the bar
    stays hidden; the step is still an automatic change and the close question knows it."""
    from ctgedit.ui.prefs.settings import app_settings

    w = window
    doc = w.doc
    st = app_settings()
    st.setValue("opt/show_auto_changes", False)
    try:
        w._auto_step(doc, "a test update", doc.create_object, "rectangle", (5, 5, 30, 30))
        assert doc.dirty and w._auto_bar.isHidden()
        assert w._only_auto_changes(doc)
        st.setValue("opt/show_auto_changes", True)
        w._auto_bar.show_for(doc)
        assert not w._auto_bar.isHidden()
    finally:
        st.remove("opt/show_auto_changes")


def test_a_user_edit_after_it_is_not_an_automatic_change(window):
    w = window
    doc = w.doc
    w._auto_step(doc, "a test update", doc.create_object, "rectangle", (5, 5, 30, 30))
    doc.create_object("rectangle", (40, 40, 60, 60))
    assert not w._only_auto_changes(doc)
    n = len(doc._undo)
    w._undo_auto_changes()  # the user's step is on top: nothing undone
    assert len(doc._undo) == n


def test_a_background_update_waits_while_the_canvas_is_busy(window):
    w = window
    doc = w.doc
    assert w._genie_may_land(doc, 0.0)
    w.view._nodes = [object()]  # a shape being drawn point by point
    try:
        assert w.view.busy() and not w._genie_may_land(doc, 0.0)
    finally:
        w.view._nodes = []
    doc.create_object("rectangle", (5, 5, 30, 30))
    doc.undo()
    assert doc.can_redo() and not w._genie_may_land(doc, 1.0)
    assert w._genie_may_land(doc, 61.0)  # (at most a minute)
