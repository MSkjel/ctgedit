"""Pictures of a page's embedded controls, taken by the control host on a worker thread.

The canvas shows a control's placeholder until its picture is ready, then the picture
(:class:`ControlItem`). Each picture is taken once per control state and size
(backend/controlhost.py keeps them on disk).
"""

from __future__ import annotations

import os
import queue
import threading
import weakref
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..backend import controlhost
from ..infra import caches as _caches
from ..infra import gcguard, logsetup

#: QPixmaps of the pictures shown, by picture key
_PIXMAPS: dict = {}
_caches.register("control_pictures.pixmaps", _PIXMAPS, on=("refresh", "tests"))


#: Settings > Canvas > "Draw embedded controls as they look" (view/control_pictures)
_ENABLED = [True]


def set_enabled(on: bool) -> None:
    """The setting: off, controls show as boxes (pages drawn from now on; the pictures kept)."""
    _ENABLED[0] = bool(on)


def enabled() -> bool:
    """Pictures are taken and shown (the setting; CTG_NO_CONTROL_HOST=1 turns them off for the
    tests)."""
    return _ENABLED[0] and os.environ.get("CTG_NO_CONTROL_HOST") != "1" and controlhost.available()


class _Service(QtCore.QObject):
    """One worker thread runs the host for each picture asked for, one at a time."""

    ready = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._jobs: "queue.Queue" = queue.Queue()
        self._asked: set = set()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._items: "weakref.WeakSet" = weakref.WeakSet()
        self.ready.connect(self._show)

    def ask(self, key: str, clsid: str, state: bytes, width: int, height: int) -> None:
        with self._lock:
            if key in self._asked:
                return
            self._asked.add(key)
            self._jobs.put((key, clsid, state, width, height))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="control-pictures", daemon=True)
                self._thread.start()

    def watch(self, item: "ControlItem") -> None:
        self._items.add(item)

    def _work(self) -> None:
        gcguard.before_worker()
        while True:
            try:
                key, clsid, state, width, height = self._jobs.get(timeout=30)
            except queue.Empty:
                return
            try:
                controlhost.take_picture(clsid, state, width, height)
            except Exception:  # noqa: BLE001 -- the placeholder stays; said in the log
                logsetup.note_exception("control_pictures.take", clsid=clsid)
            self.ready.emit(key)

    @QtCore.Slot(str)
    def _show(self, key: str) -> None:
        for item in list(self._items):
            try:
                if item.key == key:
                    item.update()
            except RuntimeError:  # swallow-ok: the scene deleted the item
                continue


_SERVICE: Optional[_Service] = None


def _service() -> _Service:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = _Service()
    return _SERVICE


def pixmap(key: str) -> Optional[QtGui.QPixmap]:
    """The picture of ``key`` when it has been taken (None: not yet, or the control paints
    nothing outside its own editor)."""
    if key in _PIXMAPS:
        return _PIXMAPS[key]
    path = controlhost.cached_picture(key)
    if path is None:
        return None
    pm = QtGui.QPixmap(path) if path else None
    _PIXMAPS[key] = pm if pm is not None and not pm.isNull() else None
    return _PIXMAPS[key]


class ControlItem(QtWidgets.QGraphicsRectItem):
    """An embedded control on the canvas: its picture once taken, its placeholder box until
    then (the brush and pen the scene gives it)."""

    def __init__(self, x: float, y: float, w: float, h: float, clsid: str = "", state: bytes = b"") -> None:
        super().__init__(x, y, w, h)
        self.key = ""
        width, height = max(1, int(round(w))), max(1, int(round(h)))
        if clsid and state and enabled():
            self.key = controlhost.picture_key(clsid, state, width, height)
            if pixmap(self.key) is None and controlhost.cached_picture(self.key) is None:
                svc = _service()
                svc.watch(self)
                svc.ask(self.key, clsid, state, width, height)

    def has_picture(self) -> bool:
        return bool(self.key) and pixmap(self.key) is not None

    def paint(self, painter: QtGui.QPainter, option, widget=None) -> None:
        pm = pixmap(self.key) if self.key and enabled() else None
        if pm is None:
            super().paint(painter, option, widget)
            return
        # (the box is laid on pixel centres for its 1 px frame: the picture goes on the pixels)
        r = self.rect()
        target = QtCore.QRectF(round(r.x() - 0.5), round(r.y() - 0.5), pm.width(), pm.height())
        painter.drawPixmap(target, pm, QtCore.QRectF(pm.rect()))
