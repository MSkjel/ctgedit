"""Delete modal dialogs once they are answered."""

from __future__ import annotations

import os

from PySide6 import QtCore, QtWidgets

#: a dialog with this property set (True) is kept after it closes
KEEP_PROP = "ctgKeepAfterExec"


def _collectable(obj) -> bool:
    import shiboken6

    try:
        if not isinstance(obj, QtWidgets.QDialog) or not obj.isWindow():
            return False
        if not shiboken6.createdByPython(obj) or obj.parent() is None:
            return False
        if not (obj.isModal() or obj.windowModality() != QtCore.Qt.NonModal):
            return False
        return not obj.property(KEEP_PROP) and not obj.testAttribute(QtCore.Qt.WA_DeleteOnClose)
    except RuntimeError:  # swallow-ok: a dialog being destroyed
        return False


class DialogReaper(QtCore.QObject):
    """See the module notes. ``handle`` is the app event filter's hook (Hide events)."""

    TYPES = frozenset((QtCore.QEvent.Type.Hide,))

    def __init__(self, parent=None):
        super().__init__(parent)
        self._done: list = []
        self.reaped = 0  # (how many it deleted: tests, diagnostics)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        return self.handle(obj, ev, ev.type())  # (installed alone: no dispatcher)

    def handle(self, obj, _ev, t) -> bool:
        if t == QtCore.QEvent.Type.Hide and _collectable(obj) and not any(d is obj for d in self._done):
            self._done.append(obj)
        return False

    def idle(self) -> None:
        """The main loop waits for events: delete the dialogs answered since."""
        if not self._done or QtCore.QThread.currentThread().loopLevel() != 1:
            return
        import shiboken6

        done, self._done = self._done, []
        for d in done:
            try:
                if shiboken6.isValid(d) and not d.isVisible():
                    d.deleteLater()
                    self.reaped += 1
                elif shiboken6.isValid(d):
                    self._done.append(d)  # (shown again: later)
            except RuntimeError:  # swallow-ok: deleted meanwhile (its parent went)
                continue


def uninstall() -> None:
    """Remove the application's reaper (tests)."""
    app = QtWidgets.QApplication.instance()
    reaper = getattr(app, "_ctg_dialog_reaper", None) if app is not None else None
    if reaper is None:
        return
    disp = QtCore.QAbstractEventDispatcher.instance()
    try:
        if disp is not None:
            disp.aboutToBlock.disconnect(reaper.idle)
    except RuntimeError, TypeError:  # swallow-ok: not connected
        pass
    app.removeEventFilter(reaper)
    app._ctg_dialog_reaper = None
    reaper._done = []


def install(dispatcher=None, force: bool = False):
    """The application's reaper (made once), or None when CTG_NO_DIALOG_REAP=1 (unless
    ``force``). ``dispatcher``: the editor's app event filter (main_window_base)."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    if os.environ.get("CTG_NO_DIALOG_REAP") == "1" and not force:
        return None
    reaper = getattr(app, "_ctg_dialog_reaper", None)
    if reaper is not None:
        return reaper
    reaper = DialogReaper(app)
    app._ctg_dialog_reaper = reaper
    if dispatcher is not None:
        dispatcher.add(reaper, reaper.TYPES)
    else:
        app.installEventFilter(reaper)
    disp = QtCore.QAbstractEventDispatcher.instance()
    if disp is not None:
        disp.aboutToBlock.connect(reaper.idle)
    return reaper
