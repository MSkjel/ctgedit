"""A narrow expression field stays a usable text box."""

from qtkit import app as _app


def test_narrow_field_hides_the_icon_and_keeps_its_text():
    app = _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.objprops.expression import ExpressionField

    # (in a host widget: on the Windows platform an earlier test may have chosen, a
    # top-level window is never narrower than about 120 px)
    host = QtWidgets.QWidget()
    host.resize(600, 80)
    f = ExpressionField(None, host, wizard=("tag",), field_kind="tag")
    f.setText("21")
    host.show()
    f.show()
    # (the room the icon needs follows the font: an earlier test may have made the
    # application without the fonts folder, where the average character is narrower)
    room = 28 + 8 * f.fontMetrics().averageCharWidth()
    f.resize(max(20, room - 10), f.sizeHint().height())
    app.processEvents()
    assert not f._wizard.isVisible()
    assert f.text() == "21"
    f.resize(room + 200, f.sizeHint().height())
    app.processEvents()
    assert f._wizard.isVisible()
    f.setReadOnly(True)
    assert not f._wizard.isVisible()
    f.setReadOnly(False)
    assert f._wizard.isVisible()
    host.close()


def test_insert_is_on_the_right_click_menu():
    app = _app()
    from PySide6 import QtCore, QtGui, QtWidgets
    from ctgedit.ui.objprops.expression import ExpressionField

    f = ExpressionField(None, wizard=("tag",), field_kind="tag")
    f.show()
    f.resize(52, f.sizeHint().height())
    app.processEvents()
    seen = []

    def grab_menu():
        for w in QtWidgets.QApplication.topLevelWidgets():
            if isinstance(w, QtWidgets.QMenu) and w.isVisible():
                seen.append([a.text() for a in w.actions()])
                w.close()

    QtCore.QTimer.singleShot(50, grab_menu)
    ev = QtGui.QContextMenuEvent(
        QtGui.QContextMenuEvent.Mouse, QtCore.QPoint(5, 5), f.mapToGlobal(QtCore.QPoint(5, 5))
    )
    f.contextMenuEvent(ev)
    assert seen and "Insert" in seen[0]
    f.close()
