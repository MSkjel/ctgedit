"""Close the Graphics Builder: it keeps the library elements it has read in memory and never
reads them again, so a library ctgedit wrote shows there only in a new one. The builder is
asked to close (it asks about pages it has not saved); one that is still open after a
while can be ended."""

from __future__ import annotations

from .names import name_list

import time
import weakref
from typing import List, Optional

from PySide6 import QtCore, QtWidgets

#: how long the builder gets to close (its save questions answered) before ctgedit asks
WAIT_S = 15.0
#: how often ctgedit looks whether it has closed (ms)
POLL_MS = 250

CLOSED = "The Graphics Builder is closed. It reads the libraries again when it next opens."
NOT_RUNNING = "The Graphics Builder is not running."
LEFT_OPEN = "The Graphics Builder was left open."


class BuilderCloser(QtCore.QObject):
    """One close of the Graphics Builder (:meth:`start`); ``finished(text)`` says how it
    ended."""

    finished = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget, wait_s: float = WAIT_S):
        # (the application's: the close goes on when the window that asked has gone)
        super().__init__(QtWidgets.QApplication.instance())
        self._parent = weakref.ref(parent) if parent is not None else (lambda: None)
        self._wait_s = wait_s
        self._pids: List[int] = []
        self._deadline = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        from ..backend import graphics_builder as GB

        if not GB.builders_running():
            self._done(NOT_RUNNING)
            return
        try:
            opened = GB.open_windows()
        except Exception:  # noqa: BLE001 -- not known: asked as if one were open
            from ..infra import logsetup

            logsetup.note_exception("builder_close.open_windows")
            opened = ["(not known)"]
        if opened and not self.confirm(opened):
            self._done(LEFT_OPEN)
            return
        try:
            self._pids = GB.ask_builders_to_close()
        except Exception:  # noqa: BLE001 -- no window could be asked: it is ended
            from ..infra import logsetup

            logsetup.note_exception("builder_close.start")
            self._pids = GB.builders_running()
        self._deadline = time.monotonic() + self._wait_s
        self._poll()
        if self._pids:
            self._timer.start()

    def _poll(self) -> None:
        from ..backend import graphics_builder as GB

        self._pids = GB.builders_running(self._pids)
        if not self._pids:
            self._done(CLOSED)
        elif time.monotonic() >= self._deadline:
            self._timer.stop()
            self._ask()

    def _ask(self) -> None:
        answer = self.question()
        if answer == "end":
            from ..backend import graphics_builder as GB

            GB.end_builders(self._pids)
            self._pids = GB.builders_running(self._pids)
            self._done(CLOSED if not self._pids else LEFT_OPEN)
        elif answer == "wait":
            self._deadline = time.monotonic() + self._wait_s
            self._timer.start()
        else:
            self._done(LEFT_OPEN)

    def confirm(self, opened: List[str]) -> bool:
        """Ask before closing a builder with ``opened`` (its open pages and elements)."""
        names = name_list(opened, 6)
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Warning,
            "Close Graphics Builder",
            "The Graphics Builder has open: %s.\n\nClose it? It asks about the changes you "
            "have not saved there." % names,
            QtWidgets.QMessageBox.NoButton,
            self._parent(),
        )
        close = box.addButton("Close It", QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(close)
        box.exec()
        return box.clickedButton() is close

    def question(self) -> str:
        """Ask what to do with a builder that is still open: 'end', 'wait' or 'cancel'."""
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Question,
            "Close Graphics Builder",
            "The Graphics Builder is still open. It may be asking whether to save a page: "
            "answer it there and choose Wait.\n\nEnd It closes the Graphics Builder now. "
            "Changes it has not saved are lost.",
            QtWidgets.QMessageBox.NoButton,
            self._parent(),
        )
        end = box.addButton("End It", QtWidgets.QMessageBox.DestructiveRole)
        wait = box.addButton("Wait", QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(wait)
        box.exec()
        hit = box.clickedButton()
        return "end" if hit is end else "wait" if hit is wait else "cancel"

    def _done(self, text: str) -> None:
        self._timer.stop()
        self.finished.emit(text)
        self.deleteLater()


def close_builder(parent: QtWidgets.QWidget, report=None) -> Optional[BuilderCloser]:
    """Close the Graphics Builder (see the module); ``report(text)`` gets the outcome (the
    window's status bar when not given)."""
    closer = BuilderCloser(parent)
    window = weakref.ref(parent) if parent is not None else (lambda: None)

    def said(text: str) -> None:
        if report is not None:
            report(text)
            return
        bar = getattr(window(), "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(text, 8000)
            except RuntimeError:  # swallow-ok: the window has gone
                pass

    closer.finished.connect(said)
    closer.start()
    return closer


def auto_close(parent: QtWidgets.QWidget, warn: str) -> str:
    """Settings > Pages and libraries > Close the Graphics Builder after saving a library:
    when ``warn`` (a save's warnings) says the builder holds old elements and the option is
    on, close it (asking first when it has a page open). Returns the rest of ``warn``."""
    from ..editing_base import CTDRAW_CACHE_WARNING
    from .prefs.settings import option

    if CTDRAW_CACHE_WARNING not in (warn or "") or not option("auto_close_builder"):
        return warn
    close_builder(parent)
    return "\n".join(ln for ln in warn.replace(CTDRAW_CACHE_WARNING, "").split("\n") if ln.strip())


def add_close_button(box: QtWidgets.QMessageBox, parent: QtWidgets.QWidget) -> QtWidgets.QAbstractButton:
    """Put **Close Graphics Builder** on a notice about the builder's old elements."""
    b = box.addButton("Close Graphics Builder", QtWidgets.QMessageBox.ActionRole)
    b.clicked.connect(lambda: close_builder(parent))
    return b
