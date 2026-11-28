"""The wait cursor of short work on the GUI thread."""

from __future__ import annotations

import contextlib

from PySide6 import QtCore, QtWidgets


@contextlib.contextmanager
def wait_cursor():
    """A wait cursor while the block runs (nested blocks stack)."""
    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
    try:
        yield
    finally:
        QtWidgets.QApplication.restoreOverrideCursor()
