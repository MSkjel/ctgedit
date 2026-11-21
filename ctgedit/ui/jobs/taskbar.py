"""Progress on the taskbar button."""

from __future__ import annotations

import os
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..prefs.settings import app_settings

#: ITaskbarList3::SetProgressState flags
NOPROGRESS, INDETERMINATE, NORMAL, ERROR, PAUSED = 0, 1, 2, 4, 8
#: ms an error state stays on the button
ERROR_MS = 3000

_CLSID_TASKBARLIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
_IID_ITASKBARLIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"
#: the vtable slots used (IUnknown 0-2, ITaskbarList 3-7, ITaskbarList2 8, ITaskbarList3 9-)
_HRINIT, _SET_VALUE, _SET_STATE = 3, 9, 10

_state = {"tried": False, "ptr": None}
#: what each window shows now, by HWND: (state, done, total) (so a repeat costs nothing)
_shown: dict = {}


def available() -> bool:
    """The platform has taskbar buttons (Windows, Qt's windows platform)."""
    return os.name == "nt" and QtGui.QGuiApplication.platformName() == "windows"


def enabled() -> bool:
    """Progress is shown: :func:`available` and Settings > Appearance > 'Show progress on
    the taskbar button'."""
    if not available():
        return False
    return bool(app_settings().value("ui/taskbar_progress", True, type=bool))


def _taskbar():
    """The ITaskbarList3 pointer (made once), None when it cannot be had."""
    if _state["tried"]:
        return _state["ptr"]
    _state["tried"] = True
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("a", wintypes.DWORD),
                ("b", wintypes.WORD),
                ("c", wintypes.WORD),
                ("d", ctypes.c_ubyte * 8),
            ]

        ole = ctypes.oledll.ole32

        def guid(s):
            g = GUID()
            ole.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
            return g

        ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # (S_FALSE when Qt did it)
        ptr = ctypes.c_void_p()
        hr = ctypes.windll.ole32.CoCreateInstance(
            ctypes.byref(guid(_CLSID_TASKBARLIST)),
            None,
            0x1 | 0x4,
            ctypes.byref(guid(_IID_ITASKBARLIST3)),
            ctypes.byref(ptr),
        )
        if hr != 0 or not ptr.value:
            return None
        if _call(ptr, _HRINIT) != 0:
            return None
        _state["ptr"] = ptr
    except Exception:  # noqa: BLE001 -- no taskbar progress then
        from ...infra.logsetup import note_exception

        note_exception("taskbar.create")
        _state["ptr"] = None
    return _state["ptr"]


def _call(ptr, slot: int, *args) -> int:
    import ctypes

    vtbl = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    argtypes = [ctypes.c_void_p] + [type(a) for a in args]
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, *argtypes)(vtbl[slot])
    return fn(ptr, *args)


def _hwnd(window) -> Optional[int]:
    try:
        return int(window.window().winId())
    except RuntimeError, AttributeError, TypeError:  # swallow-ok: no native window
        return None


def _apply(window, state: int, done: int = 0, total: int = 0) -> bool:
    # (clearing is sent whatever the setting: a bar shown before it was turned off goes)
    if not (enabled() if state != NOPROGRESS else available()):
        return False
    h = _hwnd(window)
    tb = _taskbar() if h else None
    if tb is None:
        return False
    key = (state, int(done), int(total))
    if _shown.get(h) == key:
        return True
    import ctypes

    try:
        if state == NORMAL and total > 0:
            _call(
                tb,
                _SET_VALUE,
                ctypes.c_void_p(h),
                ctypes.c_ulonglong(int(done)),
                ctypes.c_ulonglong(int(total)),
            )
        _call(tb, _SET_STATE, ctypes.c_void_p(h), ctypes.c_int(state))
    except Exception:  # noqa: BLE001 -- a hint only
        from ...infra.logsetup import note_exception

        note_exception("taskbar.apply")
        return False
    _shown[h] = key
    return True


def set_progress(window, done: int, total: int) -> bool:
    """``done`` of ``total`` on the window's taskbar button."""
    if total <= 0:
        return set_busy(window)
    return _apply(window, NORMAL, max(0, min(done, total)), total)


def set_busy(window) -> bool:
    return _apply(window, INDETERMINATE)


def clear(window, force: bool = False) -> bool:
    """The window's button shows no progress (``force``: an error state too)."""
    h = _hwnd(window)
    if h is not None and window.__dict__.get("_ctg_taskbar_error") and not force:
        return False  # (the error stays its few seconds)
    if h is not None and h not in _shown:
        return True
    ok = _apply(window, NOPROGRESS)
    if h is not None:
        _shown.pop(h, None)
    return ok


def setting_changed(window) -> None:
    """Settings > Appearance > 'Show progress on the taskbar button' changed: turned off,
    the button shows no progress at once; turned on, it shows the tasks running."""
    if not enabled():
        clear(window, force=True)
        return
    from . import tasks

    tasks.sync_taskbars()


def flash_error(window) -> None:
    """A failed task: the button turns red for :data:`ERROR_MS`, then clears."""
    if not _apply(window, ERROR, 1, 1):
        return
    window.__dict__["_ctg_taskbar_error"] = True

    def done():
        window.__dict__["_ctg_taskbar_error"] = False
        from . import tasks

        if tasks.running(window):
            tasks.sync_taskbars()  # (the tasks still running show again)
        else:
            clear(window)

    QtCore.QTimer.singleShot(ERROR_MS, window, done)


class ProgressReporter(QtCore.QObject):
    """A QProgressDialog's progress on the taskbar button of its window, as long as it is
    shown (busy when it has no range)."""

    def __init__(self, dialog: QtWidgets.QProgressDialog, window=None):
        super().__init__(dialog)
        self._dlg = dialog
        self._win = window or dialog.parentWidget()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.update)
        self._timer.start()
        self.update()

    def update(self) -> None:
        try:
            d = self._dlg
            if d.maximum() > d.minimum():
                set_progress(self._win, d.value() - d.minimum(), d.maximum() - d.minimum())
            else:
                set_busy(self._win)
        except RuntimeError:  # swallow-ok: the dialog closed
            self._timer.stop()

    def finish(self) -> None:
        self._timer.stop()
        from . import tasks

        if not tasks.running(self._win):
            clear(self._win)


def _register_setting() -> None:
    from ..prefs.settings_pages import register_apply

    register_apply("ui/taskbar_progress", setting_changed)


_register_setting()
