"""The progress window for long jobs."""

from __future__ import annotations

import time
from typing import List, Optional

from PySide6 import QtCore, QtWidgets

from ..progress_text import SteadyProgressLabel
from .run_stats import RunStats


class JobProgress:
    """The progress window of a long job."""

    def __init__(
        self,
        parent,
        title: str,
        head: str,
        phase: str = "",
        stats: Optional[RunStats] = None,
        cancel: Optional[str] = "Cancel",
        total: int = 0,
        show_after_ms: int = 0,
        lets_tabs_update: bool = False,
        status_bar=None,
    ):
        self.stats = stats if stats is not None else RunStats.from_settings()
        self.phase = phase
        self.head = head
        self._cancelled = False
        self._closed = False
        prog = QtWidgets.QProgressDialog(head, cancel, 0, max(1, int(total)) if total else 0, parent)
        prog.setAttribute(QtCore.Qt.WA_DeleteOnClose)  # (gone once closed)
        prog.setWindowTitle(title)
        prog.setWindowModality(QtCore.Qt.WindowModal)
        self._show_at = time.monotonic() + max(0, int(show_after_ms)) / 1000.0
        self._shown = int(show_after_ms) <= 0
        self._bar = status_bar
        self._bar_line = ""  # (the line it wrote to ``status_bar``)
        self._bar_prev = None  # (what the bar showed under it: given back)
        self._bar_took = False
        self._painted = 0.0  # (the last paint: ticks paint 20 times a second)
        # (Qt's own timer, started by the constructor, shows the window after its default
        # 4 s whatever the delay says: reset stops it)
        prog.reset()
        # (no show by setValue before the delay: the window shows when the job is long)
        prog.setMinimumDuration(0 if self._shown else 2**30)
        prog.setProperty("ctg_lets_open_tabs_update", bool(lets_tabs_update))
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        self.prog = prog
        self.steady = SteadyProgressLabel(prog, self.stats)
        if phase:
            self.stats.begin_phase(phase, int(total))
        prog.canceled.connect(self._on_cancel)
        self.set(head)
        if self._shown:
            prog.show()

    # -- the window --------------------------------------------------------------------------
    def _on_cancel(self) -> None:
        self._cancelled = True

    def cancel(self) -> None:
        """Cancel the job as its button does (the tasks button's Cancel)."""
        self._cancelled = True
        try:
            self.prog.cancel()
        except RuntimeError:  # swallow-ok: the window is gone
            pass

    @property
    def cancelled(self) -> bool:
        """Cancel was clicked (also read by :meth:`tick`, :meth:`pulse` and :meth:`wait`).
        After :meth:`close` it stays what it was: a closed QProgressDialog counts as
        cancelled by itself (its closeEvent emits canceled), which no job is."""
        if self._closed:
            return self._cancelled
        try:
            return self._cancelled or self.prog.wasCanceled()
        except RuntimeError:  # swallow-ok: the window is gone: the job goes on without it
            return self._cancelled

    def figures(self) -> List[str]:
        """The lines under the job's own: the phase's figures, or the time so far."""
        if self.phase:
            return self.stats.lines()
        t = self.stats.time_text()
        return ["Time: " + t] if t else []

    def _events(self) -> None:
        """Paint and read Cancel; the window shows once the job is long. Before it shows,
        input waits (nothing behind it may change while the job reads)."""
        if not self._shown and time.monotonic() >= self._show_at and not self._closed:
            self._shown = True
            self._clear_bar_line()  # (the window shows the job's line now)
            try:
                self.prog.show()
            except RuntimeError:  # swallow-ok: the window is gone
                pass
        if self._shown:
            QtWidgets.QApplication.processEvents()
        else:
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)

    @property
    def shown(self) -> bool:
        """The window has shown (a job that ended before ``show_after_ms`` never shows)."""
        return self._shown

    def set(self, head: str) -> None:
        """The job's own line (the figures follow it)."""
        self.head = head
        if not self._shown and self._bar is not None:
            try:
                if not self._bar_took or self._bar.currentMessage() != self._bar_line:
                    # (what the bar shows before its first line, or a message told since its
                    # last one: given back when the job's line goes, _clear_bar_line)
                    get = getattr(self._bar, "message_state", None)
                    self._bar_prev = get() if callable(get) else None
                    self._bar_took = True
                self._bar.showMessage(head)
                self._bar_line = head
            except RuntimeError:  # swallow-ok: the status bar is gone
                self._bar = None
        try:
            self.steady.set([head] + self.figures())
        except RuntimeError:  # swallow-ok: the window is gone
            pass

    #: the shortest time between two paints of :meth:`tick` (s): painting the window and the
    #: status bar and handling events after every item can take far longer than the items
    PAINT_EVERY_S = 0.05

    def tick(self, i: int, n: int, head: Optional[str] = None) -> bool:
        """``i`` of ``n`` items are done (``head`` = the new line); the window paints and
        Cancel is read, at most every :attr:`PAINT_EVERY_S` (the last item always).
        False once Cancel was clicked."""
        n = max(1, int(n))
        now = time.monotonic()
        if now - self._painted < self.PAINT_EVERY_S and int(i) < n - 1:
            return not self.cancelled
        self._painted = now
        try:
            self.prog.setMaximum(n)
            self.prog.setValue(min(max(0, int(i)), n))
        except RuntimeError:  # swallow-ok: the window is gone
            pass
        if self.phase:
            self.stats.begin_phase(self.phase, n)
            self.stats.update(int(i))
        self.set(head if head is not None else self.head)
        self._events()
        return not self.cancelled

    def pulse(self, head: Optional[str] = None) -> bool:
        """The same for a job without counts: the time so far is refreshed."""
        self.set(head if head is not None else self.head)
        self._events()
        return not self.cancelled

    def wait(self, ms: int) -> bool:
        """Run the event loop for ``ms`` (the window stays alive, Cancel is read), then
        refresh the time so far. False once Cancel was clicked."""
        if self.cancelled:
            return False
        loop = QtCore.QEventLoop()
        # (the timer belongs to the loop: a loop that ends before it fires, an application
        # quit say, deletes it with itself, and no late quit() reaches a deleted loop)
        timer = QtCore.QTimer(loop)
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(max(0, int(ms)))
        loop.exec()
        timer.stop()
        return self.pulse()

    def _clear_bar_line(self) -> None:
        """Clear the line this job wrote to the status bar."""
        line, self._bar_line = self._bar_line, ""
        prev, self._bar_prev, self._bar_took = self._bar_prev, None, False
        if not line or self._bar is None:
            return
        try:
            if self._bar.currentMessage() == line:
                put = getattr(self._bar, "restore_message", None)
                if prev is not None and callable(put):
                    put(prev)
                else:
                    self._bar.clearMessage()
        except RuntimeError:  # swallow-ok: the status bar is gone
            self._bar = None

    def close(self) -> None:
        """Close the window; what Cancel said stays readable (:attr:`cancelled`). The job's
        line goes from ``status_bar`` (when the bar still shows it)."""
        if self._closed:
            return
        self._cancelled = self.cancelled
        self._closed = True
        self._clear_bar_line()
        try:
            self.prog.canceled.disconnect(self._on_cancel)
            self.prog.close()
        except RuntimeError, TypeError:  # swallow-ok: closed already
            pass

    def __enter__(self) -> "JobProgress":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
