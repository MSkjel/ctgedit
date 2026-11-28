"""Panel title bars: dock and float."""

import os

import pytest


@pytest.fixture()
def editor():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    yield w
    w.doc = None
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.close()
    app.processEvents()


def test_every_editor_panel_has_the_title_bar(editor):
    from PySide6 import QtWidgets
    from ctgedit.ui.panels import docking

    docks = editor.findChildren(QtWidgets.QDockWidget)
    assert len(docks) >= 5
    for d in docks:
        assert isinstance(d.titleBarWidget(), docking.DockTitleBar), d.objectName()
    assert editor.dockOptions() & QtWidgets.QMainWindow.AnimatedDocks


def test_button_and_double_click_dock_and_float(editor):
    from PySide6 import QtCore, QtTest, QtWidgets

    d = editor.dock_props
    bar = d.titleBarWidget()
    area = editor.dockWidgetArea(d)
    assert not d.isFloating() and bar.float_btn.accessibleName() == "Float"
    bar.float_btn.click()
    QtWidgets.QApplication.processEvents()
    assert d.isFloating() and bar.float_btn.accessibleName() == "Dock"
    bar.float_btn.click()  # Dock: back where it was
    QtWidgets.QApplication.processEvents()
    assert not d.isFloating() and editor.dockWidgetArea(d) == area
    # a double-click on the title: floats it, and docks it again
    # (the dock moves during the double-click, so its release is sent again: else Qt keeps
    # the button down for the next test)
    QtTest.QTest.mouseDClick(bar.label, QtCore.Qt.LeftButton)
    QtTest.QTest.mouseRelease(bar.label, QtCore.Qt.LeftButton)
    QtWidgets.QApplication.processEvents()
    assert d.isFloating()
    QtTest.QTest.mouseDClick(bar.label, QtCore.Qt.LeftButton)
    QtTest.QTest.mouseRelease(bar.label, QtCore.Qt.LeftButton)
    QtWidgets.QApplication.processEvents()
    assert not d.isFloating() and editor.dockWidgetArea(d) == area
    assert QtWidgets.QApplication.mouseButtons() == QtCore.Qt.NoButton
    # Close hides the panel
    bar.close_btn.click()
    QtWidgets.QApplication.processEvents()
    assert not d.isVisible()


def test_a_floating_panel_maximizes_and_restores(editor):
    """Windows gives a floating panel (its own title bar, no Windows frame) no Maximize: the
    title bar has one. Maximized = the screen's work area; Restore gives back its size and
    place; moving it makes it an ordinary window again; a docked panel has none."""
    from PySide6 import QtCore, QtWidgets

    d = editor.dock_props
    bar = d.titleBarWidget()
    assert not bar.max_btn.isVisibleTo(bar)
    bar.float_btn.click()
    QtWidgets.QApplication.processEvents()
    d.setGeometry(100, 120, 300, 400)
    QtWidgets.QApplication.processEvents()
    assert bar.max_btn.isVisibleTo(bar) and bar.max_btn.accessibleName() == "Maximize"
    before = QtCore.QRect(d.geometry())
    bar.max_btn.click()
    QtWidgets.QApplication.processEvents()
    work = (d.screen() or QtWidgets.QApplication.primaryScreen()).availableGeometry()
    assert d.geometry() == work and bar.is_maximized()
    assert bar.max_btn.accessibleName() == "Restore"
    bar.max_btn.click()
    QtWidgets.QApplication.processEvents()
    assert d.geometry() == before and not bar.is_maximized()
    bar.max_btn.click()
    QtWidgets.QApplication.processEvents()
    d.move(d.pos() + QtCore.QPoint(40, 30))  # dragged away: an ordinary window again
    QtWidgets.QApplication.processEvents()
    assert not bar.is_maximized() and bar.max_btn.accessibleName() == "Maximize"
    bar.float_btn.click()  # docked: no Maximize
    QtWidgets.QApplication.processEvents()
    assert not d.isFloating() and not bar.max_btn.isVisibleTo(bar)
