"""Run garbage collection on the GUI thread only."""

from __future__ import annotations

import gc
import os
import threading

#: a full collection waits at least for this many young-collection survivors
FULL_MIN = 100_000
#: the timer's period (ms)
PERIOD_MS = 200
#: timer ticks without a young collection before the editor counts as quiet
QUIET_TICKS = 3

_state = {"owner": None, "pending": 0, "long_lived": 0, "timer": None, "young": 0, "full": 0, "quiet": 0}


def installed() -> bool:
    return _state["owner"] is not None


def install() -> bool:
    """Collect on THIS thread only from now on (idempotent). True when it was installed now.
    CTG_GC_GUARD=0 leaves the automatic collector alone (an A/B switch for diagnostics)."""
    if _state["owner"] is not None or os.environ.get("CTG_GC_GUARD") == "0":
        return False
    _state["owner"] = threading.get_ident()
    _state["pending"] = 0
    _state["quiet"] = 0
    _state["long_lived"] = len(gc.get_objects())
    gc.disable()
    return True


def install_timer(parent=None) -> None:
    """A Qt timer on the owner thread that runs :func:`tick` (idempotent; ``parent`` = the
    application, so it outlives every window)."""
    if _state["owner"] != threading.get_ident():
        return
    t = _state["timer"]
    if t is not None:
        try:
            t.isActive()  # still alive?
            return
        except RuntimeError:
            _state["timer"] = None
    from PySide6 import QtCore

    if parent is None:
        parent = QtCore.QCoreApplication.instance()
    t = QtCore.QTimer(parent)
    t.setInterval(PERIOD_MS)
    t.timeout.connect(tick)
    t.start()
    _state["timer"] = t


def before_worker() -> bool:
    """Call on the GUI thread before starting a worker that allocates Python objects."""
    import sys

    qtcore = sys.modules.get("PySide6.QtCore")
    if qtcore is None:
        return installed()
    app = qtcore.QCoreApplication.instance()
    # the application lives on the main thread (app.main, every test)
    if app is None or threading.current_thread() is not threading.main_thread():
        return installed()
    install()
    install_timer(app)
    return installed()


def uninstall() -> None:
    """The automatic collector again (after a full collection on this thread)."""
    if _state["owner"] is None:
        return
    t, _state["timer"] = _state["timer"], None
    if t is not None:
        try:
            t.stop()
            t.deleteLater()
        except RuntimeError:
            pass
    _state["owner"] = None
    gc.enable()


def _young() -> tuple:
    """(freed, collected?) -- the young generation when it is due."""
    thr0 = gc.get_threshold()[0] or 2000
    n = gc.get_count()[0]
    if n < thr0:
        return 0, False
    freed = gc.collect(0)
    _state["young"] += 1
    _state["pending"] += max(0, n - freed)
    return freed, True


def _full_due(urgent_only: bool) -> bool:
    ll = _state["long_lived"]
    if _state["pending"] > max(4 * FULL_MIN, ll):
        return True  # memory: whatever the moment
    return not urgent_only and _state["pending"] > max(FULL_MIN, ll // 4)


def _full() -> int:
    freed = gc.collect()
    _state["full"] += 1
    _state["long_lived"] = len(gc.get_objects())
    _state["pending"] = 0
    return freed


def collect_due(urgent_only: bool = False) -> int:
    """Collect what CPython's automatic collector would have by now -- only on the owner
    thread (anywhere else: nothing). Returns the number of objects freed. ``urgent_only``: a
    full collection only when memory needs it (see the module)."""
    if _state["owner"] != threading.get_ident():
        return 0
    freed, _y = _young()
    if _full_due(urgent_only):
        freed += _full()
    return freed


def checkpoint() -> int:
    """The hot-path hook (reader.derive_page): the young generation now; the full collection
    waits for a quiet moment (:func:`tick`) unless memory would pile up."""
    return collect_due(True)


def _buttons_down() -> bool:
    try:
        from PySide6 import QtGui

        return QtGui.QGuiApplication.mouseButtons() != QtGui.Qt.MouseButton.NoButton
    except Exception:  # noqa: BLE001 -- swallow-ok: no Qt application
        return False


def tick() -> int:
    """The timer: the young generation when due, the regular full collection when quiet."""
    if _state["owner"] != threading.get_ident():
        return 0
    freed, young = _young()
    _state["quiet"] = 0 if young else _state["quiet"] + 1
    if _full_due(True) or (_state["quiet"] >= QUIET_TICKS and _full_due(False) and not _buttons_down()):
        freed += _full()
    return freed


def stats() -> dict:
    """{installed, young, full, pending, long_lived} (diagnostics / tests)."""
    return {
        "installed": installed(),
        "young": _state["young"],
        "full": _state["full"],
        "pending": _state["pending"],
        "long_lived": _state["long_lived"],
    }
