"""Modal dialogs are deleted once answered."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402
import shiboken6  # noqa: E402
from qtkit import app as _app


def _answered(dlg):
    QtCore.QTimer.singleShot(0, dlg.accept)
    return dlg.exec()


def _close_modal():
    m = QtWidgets.QApplication.activeModalWidget()
    if m is not None:
        m.close()


def _as_a_command(fn, settle_ms=300):
    """Run ``fn`` as the app runs a command: from a timer in the (main) event loop, which
    then goes on for ``settle_ms`` (it waits for events in between: the reaper's moment)."""
    loop = QtCore.QEventLoop()
    out = {}

    def go():
        out["result"] = fn()
        QtCore.QTimer.singleShot(settle_ms, loop.quit)

    QtCore.QTimer.singleShot(0, go)
    loop.exec()
    return out.get("result")


def test_a_dialog_with_a_parent_goes_once_the_command_is_done():
    _app()
    from ctgedit.ui import dialog_release

    reaper = dialog_release.install(force=True)
    try:
        assert dialog_release.install(force=True) is reaper  # (once)
        parent = QtWidgets.QWidget()
        seen = {}

        def command():
            dlg = QtWidgets.QDialog(parent)
            field = QtWidgets.QLineEdit("typed", dlg)
            assert _answered(dlg) == QtWidgets.QDialog.Accepted
            # another modal before the command reads the first one, and a box Qt makes
            # and deletes itself: the first one is still there, nothing crashes
            _answered(QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, "t", "x", parent=parent))
            QtCore.QTimer.singleShot(0, _close_modal)
            QtWidgets.QMessageBox.information(parent, "t", "made by Qt")
            QtWidgets.QApplication.processEvents()  # (a command pumping events)
            seen["read"] = shiboken6.isValid(dlg) and field.text()
            kept = QtWidgets.QDialog(parent)
            kept.setProperty(dialog_release.KEEP_PROP, True)
            _answered(kept)
            free = QtWidgets.QDialog()
            _answered(free)
            return dlg, kept, free

        dlg, kept, free = _as_a_command(command)
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        assert seen["read"] == "typed"
        assert not shiboken6.isValid(dlg) and reaper.reaped >= 2
        assert parent.findChildren(QtWidgets.QDialog) == [kept]  # (the box went too)
        assert shiboken6.isValid(free)
        # with no event loop running (the start before app.exec()): kept
        late = QtWidgets.QDialog(parent)
        _answered(late)
        reaper.idle()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        assert shiboken6.isValid(late)
        parent.deleteLater()
    finally:
        dialog_release.uninstall()


def test_the_tests_run_without_it():
    """tests/conftest sets CTG_NO_DIALOG_REAP=1: a test may wait in an event loop of its own
    while it still reads a dialog."""
    _app()
    from ctgedit.ui import dialog_release

    assert os.environ.get("CTG_NO_DIALOG_REAP") == "1"
    assert dialog_release.install() is None
