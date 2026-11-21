"""Background tasks."""

from __future__ import annotations

import itertools
import time
import weakref
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..notify import StatusPopover

#: a task that ran longer than this (s) says how it ended in the window's messages
#: (Settings > Pages and libraries > Long jobs, ui/prefs/job_options)
LONG_S = 5.0
#: ms between frames of the turning mark
SPIN_MS = 90


class Task:
    __slots__ = ("token", "kind", "text", "i", "n", "cancel", "started", "_window", "cancelled", "quiet")

    def __init__(self, token: int, kind: str, text: str, cancel=None, window=None, quiet: bool = False):
        self.token = token
        self.kind = kind
        self.text = text
        self.i = 0
        self.n = 0
        self.cancel = cancel
        self.started = time.monotonic()
        self._window = weakref.ref(window) if window is not None else None
        self.cancelled = False
        self.quiet = bool(quiet)

    @property
    def window(self):
        return self._window() if self._window is not None else None

    def orphaned(self) -> bool:
        """Its window is gone (the task shows nowhere): collected, or its Qt object deleted
        while a Python reference keeps the wrapper (a closed window waiting for its
        deleteLater)."""
        if self._window is None:
            return False
        w = self._window()
        if w is None:
            return True
        try:
            import shiboken6

            return not shiboken6.isValid(w)
        except ImportError:  # swallow-ok: no shiboken (a stand-in window in a test)
            return False

    def shows_in(self, window) -> bool:
        if self._window is None:
            return True  # (no window of its own: every window)
        w = self._window()
        return w is not None and w is window

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def progress_text(self) -> str:
        return "%d of %d" % (self.i, self.n) if self.n > 0 else ""


class Registry(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tasks: Dict[int, Task] = {}
        self._tokens = itertools.count(1)

    def running(self, window=None) -> List[Task]:
        out = [t for t in self.tasks.values() if not t.orphaned()]
        return out if window is None else [t for t in out if t.shows_in(window)]


_REGISTRY: List[Registry] = []


def registry() -> Registry:
    if not _REGISTRY:
        _REGISTRY.append(Registry(QtWidgets.QApplication.instance()))
        _REGISTRY[0].changed.connect(sync_taskbars)
    return _REGISTRY[0]


def begin(kind: str, text: str, cancel: Optional[Callable] = None, window=None, quiet: bool = False) -> int:
    """Start showing a task."""
    from ..notify import editor_window

    reg = registry()
    win = editor_window(window) if window is not None else None
    for t in [t for t in reg.tasks.values() if t.orphaned()]:
        reg.tasks.pop(t.token, None)  # (its window closed: nothing shows it)
    token = next(reg._tokens)
    reg.tasks[token] = Task(token, kind, str(text or kind), cancel, win, quiet)
    reg.changed.emit()
    return token


def progress(token: Optional[int], i: int, n: int, text: Optional[str] = None) -> None:
    """``i`` of ``n`` done (n = 0: busy, no counts); ``text`` replaces the task's text.
    The same progress again changes nothing (no signal)."""
    reg = registry()
    t = reg.tasks.get(token) if token is not None else None
    if t is None:
        return
    i, n = max(0, int(i)), max(0, int(n))
    text = str(text) if text else t.text
    if (t.i, t.n, t.text) == (i, n, text):
        return
    t.i, t.n, t.text = i, n, text
    reg.changed.emit()


def end(token: Optional[int], ok: bool = True, summary: Optional[str] = None, tell: bool = True) -> None:
    """The task is over."""
    reg = registry()
    t = reg.tasks.pop(token, None) if token is not None else None
    if t is None:
        return
    reg.changed.emit()
    long = t.elapsed() > LONG_S
    win = t.window
    if win is None:
        from ..notify import editor_window

        win = editor_window()
    if win is None:
        return
    failed = not ok and not t.cancelled
    if tell and not t.quiet and (long or not ok):
        from ..notify import notify

        what = t.text.rstrip("….: ")
        text = summary or ("%s: %s." % (what, "stopped" if t.cancelled else ("done" if ok else "failed")))
        # (a key of the task's own text: the tasks of two pages keep a message each)
        notify(
            win,
            text,
            "success" if ok and not t.cancelled else ("info" if t.cancelled else "error"),
            key="task:%s:%s" % (t.kind, what),
        )
    if failed:
        from . import taskbar

        taskbar.flash_error(win)
    if failed or (not t.quiet and (long or not ok)):
        _alert(win)


def drop(token: Optional[int]) -> None:
    """The task is gone without a word (its page closed: nothing to tell)."""
    reg = registry()
    if token is not None and reg.tasks.pop(token, None) is not None:
        reg.changed.emit()


def cancel(token: int) -> bool:
    """Cancel the task (its cancel callback); False when it cannot be cancelled."""
    t = registry().tasks.get(token)
    if t is None or t.cancel is None:
        return False
    t.cancelled = True
    try:
        t.cancel()
    except Exception:  # noqa: BLE001 -- a task that cannot stop says so in the log
        from ...infra.logsetup import note_exception

        note_exception("tasks.cancel:%s" % t.kind)
    registry().changed.emit()
    return True


def running(window=None) -> List[Task]:
    return registry().running(window) if _REGISTRY else []


def _alert(win) -> None:
    """Flash the taskbar button of ``win`` when ctgedit is not the active program."""
    try:
        if QtWidgets.QApplication.activeWindow() is None or not win.isActiveWindow():
            QtWidgets.QApplication.alert(win, 0)
    except RuntimeError:  # swallow-ok: the window is being destroyed
        return


def sync_taskbars() -> None:
    """Every editor window's taskbar button shows the progress of its tasks."""
    from . import taskbar

    try:
        from ..main_window import MainWindow

        wins = MainWindow.editor_windows()
    except ImportError:  # swallow-ok: no editor windows (a tool)
        return
    for w in wins:
        try:
            ts = running(w)
        except RuntimeError:  # swallow-ok: a window being destroyed
            continue
        if not ts:
            taskbar.clear(w)
            continue
        counted = [t for t in ts if t.n > 0]
        if counted and len(counted) == len(ts):
            taskbar.set_progress(w, sum(t.i for t in counted), sum(t.n for t in counted))
        else:
            taskbar.set_busy(w)


# --------------------------------------------------------------------------- the button
def _spinner_pixmap(angle: float, size: int, dpr: float) -> QtGui.QPixmap:
    from ..style.theme import icon

    n = max(8, int(size))
    base = icon("sync").pixmap(QtCore.QSize(n, n), dpr)
    pm = QtGui.QPixmap(int(n * dpr), int(n * dpr))
    pm.fill(QtCore.Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
    p.translate(n / 2.0, n / 2.0)
    p.rotate(angle)
    p.translate(-n / 2.0, -n / 2.0)
    p.drawPixmap(QtCore.QRectF(0, 0, n, n), base, QtCore.QRectF(base.rect()))
    p.end()
    return pm


class TaskButton(QtWidgets.QToolButton):
    """The status bar's tasks button: a turning mark and the newest task's text, hidden while
    nothing runs; a click lists the tasks (:class:`TasksPopover`)."""

    ICON_PX = 14
    MAX_TEXT_PX = 280

    def __init__(self, window):
        super().__init__(window)
        self._win = window
        self.setObjectName("StatusTasks")
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.setIconSize(QtCore.QSize(self.ICON_PX, self.ICON_PX))
        self.setAccessibleName("Background tasks")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.clicked.connect(self.toggle_list)
        self.popover: Optional["TasksPopover"] = None
        self._angle = 0.0
        self._spin = QtCore.QTimer(self)
        self._spin.setInterval(SPIN_MS)
        self._spin.timeout.connect(self._turn)
        registry().changed.connect(self._changed)
        self._changed()

    def _changed(self) -> None:
        try:
            ts = running(self._win)
        except RuntimeError:  # swallow-ok: the window is being destroyed
            return
        if not ts:
            self._spin.stop()
            self.setVisible(False)
            if self.popover is not None and self.popover.isVisible():
                self.popover.rebuild()
            return
        newest = ts[-1]
        text = newest.text
        if newest.n > 0:
            text = "%s %d of %d" % (text.rstrip("…"), newest.i, newest.n)
        if len(ts) > 1:
            text += "  (+%d)" % (len(ts) - 1)
        fm = QtGui.QFontMetrics(self.font())
        from ..style.theme import px

        self.setText(fm.elidedText(text, QtCore.Qt.ElideRight, px(self.MAX_TEXT_PX)))
        self.setToolTip(
            "\n".join(t.text + ("  " + t.progress_text() if t.n else "") for t in reversed(ts))
            + "\nClick to see the tasks."
        )
        self.setAccessibleDescription(self.toolTip())
        self._turn()
        if not self._spin.isActive():
            self._spin.start()
        self.setVisible(True)
        if self.popover is not None and self.popover.isVisible():
            self.popover.rebuild()

    def _turn(self) -> None:
        self._angle = (self._angle + 30.0) % 360.0
        dpr = self.devicePixelRatioF() or 1.0
        self.setIcon(QtGui.QIcon(_spinner_pixmap(self._angle, self.ICON_PX, dpr)))

    def toggle_list(self) -> None:
        if self.popover is not None and self.popover.isVisible():
            self.popover.close()
            return
        if self.popover is None:
            self.popover = TasksPopover(self._win)
        self.popover.rebuild()
        self.popover.open_at(self)


class TasksPopover(StatusPopover):
    """The running tasks: each one's text, a thin progress bar (busy, or i of n) and
    Cancel. A change of a task's progress updates its row in place (the rows are made again
    only when a task comes or goes: a Cancel under the pointer stays the same button)."""

    def __init__(self, window):
        super().__init__(window, "TasksPopover", "Background tasks")
        self._win = window
        #: the tasks the rows show, newest first, and each row's parts by token
        self._shown: tuple = ()
        self._parts: Dict[int, dict] = {}
        self._helpers: Optional[QtWidgets.QLabel] = None

    def rebuild(self) -> None:
        ts = list(reversed(running(self._win)))
        helpers = helper_text()
        layout = (tuple((t.token, t.cancel is not None) for t in ts), bool(helpers), bool(ts))
        if layout == self._shown and self._parts:
            if self._update(ts, helpers):
                self.fit()
            return
        self._build(ts, helpers)
        self._shown = layout

    def _build(self, ts, helpers: str) -> None:
        from ..style.theme import empty_state, set_note_role, sp, px

        self.clear_rows()
        self._parts = {}
        self._helpers = None
        if not ts:
            self.add_row(empty_state("sync", "Nothing is running."))
            self.fit()
            return
        for t in ts:
            row = QtWidgets.QWidget(self.rows)
            vl = QtWidgets.QVBoxLayout(row)
            vl.setContentsMargins(sp(1), sp(1), sp(1), sp(1))
            vl.setSpacing(sp(1))
            top = QtWidgets.QHBoxLayout()
            lab = QtWidgets.QLabel(row)
            lab.setWordWrap(True)
            top.addWidget(lab, 1)
            btn = None
            if t.cancel is not None:
                btn = QtWidgets.QToolButton(row)
                btn.setText("Cancel")
                btn.setToolTip("Stop this task")
                btn.clicked.connect(lambda _=False, k=t.token: cancel(k))
                top.addWidget(btn)
            vl.addLayout(top)
            bar = QtWidgets.QProgressBar(row)
            bar.setTextVisible(False)
            bar.setFixedHeight(px(4))
            vl.addWidget(bar)
            sub = QtWidgets.QLabel(row)
            set_note_role(sub, "text_dim")
            vl.addWidget(sub)
            self._parts[t.token] = {"row": row, "label": lab, "cancel": btn, "bar": bar, "sub": sub}
            self.add_row(row)
        if helpers:
            note = QtWidgets.QLabel(helpers, self.rows)
            note.setWordWrap(True)
            set_note_role(note, "text_dim")
            self._helpers = note
            self.add_row(note)
        self.rows_lay.addStretch(1)
        self._update(ts, helpers)
        self.fit()

    def _update(self, ts, helpers: str) -> bool:
        """Every row shows its task as it is now; True when a text changed (the list may
        need another height)."""
        grew = False
        for t in ts:
            p = self._parts.get(t.token)
            if p is None:
                continue
            if p["label"].text() != t.text:
                p["label"].setText(t.text)
                p["row"].setAccessibleName(t.text)
                p["bar"].setAccessibleName("Progress of %s" % t.text)
                grew = True
            if p["cancel"] is not None:
                p["cancel"].setEnabled(not t.cancelled)
            bar = p["bar"]
            if t.n > 0:
                if bar.maximum() != t.n or bar.minimum() != 0:
                    bar.setRange(0, t.n)
                bar.setValue(min(t.i, t.n))
            elif bar.maximum() != 0:
                bar.setRange(0, 0)
            info = (
                "%s  ·  %s" % (t.progress_text(), _elapsed_text(t.elapsed()))
                if t.n
                else _elapsed_text(t.elapsed())
            )
            p["sub"].setText(info)
        if self._helpers is not None and self._helpers.text() != helpers:
            self._helpers.setText(helpers)
            grew = True
        return grew


#: the helper processes' kinds in words (infra/workers.track / set_busy)
HELPER_NAMES = {
    "genie": "genie updates",
    "pages": "closed pages",
    "ctf": "fast display check",
    "display": "display check",
    "preview": "previews",
    "pictures": "pictures",
}


def helper_text() -> str:
    """'Helper processes running: genie updates, closed pages.' ('' when none)."""
    from ...infra import workers

    kinds = workers.running_kinds()
    if not kinds:
        return ""
    names = [HELPER_NAMES.get(k, k) + (" (%d)" % n if n > 1 else "") for k, n in sorted(kinds.items())]
    return "Helper processes running: %s." % ", ".join(names)


def _elapsed_text(s: float) -> str:
    s = int(s)
    if s < 60:
        return "%d s" % s
    return "%d min %02d s" % (s // 60, s % 60)
