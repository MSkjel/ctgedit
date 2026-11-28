"""Load thumbnails and summaries in the background."""

from __future__ import annotations

import collections
from typing import Dict, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ...format import pagesummary
from ...infra.logsetup import note_exception as _note_exc

#: the thumbnail sizes of every picker and list (use these, not own sizes):
#: grid cards, list rows, preview panes
CARD_SIZE = QtCore.QSize(168, 104)
ROW_SIZE = QtCore.QSize(48, 28)
PREVIEW_SIZE = QtCore.QSize(240, 150)


def card_wheel_delta(ev) -> Optional[int]:
    """How much Ctrl + wheel changes the card size."""
    mods = ev.modifiers()
    if not mods & QtCore.Qt.ControlModifier:
        return None
    ad = ev.angleDelta()
    units = ad.y() or ad.x()
    if not units:
        return 0
    from ..editor.view_consistency import card_wheel_step

    px = card_wheel_step() * units / 120.0 * (0.25 if mods & QtCore.Qt.AltModifier else 1.0)
    n = int(round(px))
    return n if n else (1 if px > 0 else -1)


# request kinds
_STD, _FULL, _META = "std", "full", "meta"
_PRIO_FULL, _PRIO_VISIBLE, _PRIO_META = 20, 10, -10


def load_summary(entry, want_thumbnail: bool = True):
    """PageSummary / ElementSummary for a catalog Entry (thread-safe, never raises)."""
    try:
        if entry.kind == "page":
            return pagesummary.read_page_summary(entry.path, want_thumbnail=want_thumbnail)
        off = entry.element_offset if entry.element_offset >= 0 else None
        s = pagesummary.read_element_summary(
            entry.path,
            entry.name,
            offset=off,
            size=entry.element_size if off is not None else None,
            word2=entry.word2 if off is not None else None,
            want_thumbnail=want_thumbnail,
        )
        return s
    except Exception:  # noqa: BLE001 -- a bad file must never kill a worker
        _note_exc("thumbs.load_summary", path=entry.path, name=entry.name)
        return None


def fit_image(img: QtGui.QImage, box: QtCore.QSize, max_upscale: float = 3.0) -> QtGui.QImage:
    """Scale ``img`` to fit ``box`` (aspect kept), enlarging tiny images (16 px
    icons) at most ``max_upscale`` times so they don't turn to mush."""
    if img.isNull() or img.width() <= 0 or img.height() <= 0:
        return img
    f = min(box.width() / img.width(), box.height() / img.height(), max_upscale)
    w, h = max(1, int(img.width() * f)), max(1, int(img.height() * f))
    if (w, h) == (img.width(), img.height()):
        return img
    mode = (
        QtCore.Qt.FastTransformation if f >= 2.0 and float(f).is_integer() else QtCore.Qt.SmoothTransformation
    )
    return img.scaled(w, h, QtCore.Qt.IgnoreAspectRatio, mode)


def bmp_to_qimage(bmp: bytes) -> QtGui.QImage:
    """BMP bytes -> QImage (null image when absent / undecodable)."""
    if not bmp:
        return QtGui.QImage()
    img = QtGui.QImage.fromData(bmp, "BMP")
    return img if not img.isNull() else QtGui.QImage()


class _Signals(QtCore.QObject):
    done = QtCore.Signal(object)


class _Job(QtCore.QRunnable):
    def __init__(self, key: str, what: str, entry, sig: _Signals, generation: int):
        super().__init__()
        self.setAutoDelete(True)
        self.key, self.what, self.entry, self.sig, self.gen = key, what, entry, sig, generation

    def run(self) -> None:  # worker thread
        res = {
            "key": self.key,
            "what": self.what,
            "gen": self.gen,
            "summary": None,
            "card": None,
            "row": None,
            "full": None,
        }
        want_img = self.what in (_STD, _FULL)
        s = load_summary(self.entry, want_thumbnail=want_img)
        if s is not None:
            if want_img:
                img = bmp_to_qimage(s.thumb_bmp)
                if not img.isNull():
                    if self.what == _FULL:
                        res["full"] = img
                    else:
                        res["card"] = fit_image(img, CARD_SIZE)
                        res["row"] = fit_image(img, ROW_SIZE)
                s = s.without_thumbnail()
            res["summary"] = s
        try:
            self.sig.done.emit(res)
        except RuntimeError:  # service already torn down
            pass


class ThumbnailService(QtCore.QObject):
    """Asynchronous thumbnail / summary loader with a byte-capped pixmap LRU."""

    thumbnailReady = QtCore.Signal(str)
    summaryReady = QtCore.Signal(str, object)

    _shared: Optional["ThumbnailService"] = None

    @classmethod
    def shared(cls) -> "ThumbnailService":
        """One app-wide service (cache survives between browser openings)."""
        svc = cls._shared
        if svc is not None:
            try:
                svc.objectName()  # still alive?
                return svc
            except RuntimeError:
                cls._shared = None
        app = QtWidgets.QApplication.instance()
        svc = cls(app)
        cls._shared = svc
        if app is not None:
            app.aboutToQuit.connect(svc.shutdown)
        return svc

    def __init__(
        self,
        parent: Optional[QtCore.QObject] = None,
        threads: int = 4,
        cache_bytes: int = 64 * 1024 * 1024,
        max_summaries: int = 20000,
    ):
        super().__init__(parent)
        # the workers allocate Python objects: only the GUI thread may collect
        # garbage (a worker's collection runs Qt destructors off it -- infra.gcguard)
        from ...infra import gcguard

        gcguard.install()
        gcguard.install_timer(QtWidgets.QApplication.instance())
        # the pool first: children are destroyed in creation order, so the pool
        # (whose destructor waits for running jobs) goes before the signals
        self._pool = QtCore.QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, threads))
        self._sig = _Signals(self)
        self._sig.done.connect(self._on_done)
        self._cache_limit = int(cache_bytes)
        self._pix: "collections.OrderedDict[Tuple[str, str], QtGui.QPixmap]" = collections.OrderedDict()
        self._pix_bytes = 0
        self._summaries: "collections.OrderedDict[str, object]" = collections.OrderedDict()
        self._max_summaries = max_summaries
        self._pending: Dict[Tuple[str, str], int] = {}
        self._no_thumb: set = set()
        self._generation = 0
        self._closed = False
        # prefetch is fed to the pool in small batches so a big scope (5k
        # symbols) never floods the queue ahead of the rows being painted
        self._prefetch_q: "collections.deque" = collections.deque()
        self._prefetch_timer = QtCore.QTimer(self)
        self._prefetch_timer.setInterval(10)
        self._prefetch_timer.timeout.connect(self._prefetch_tick)

    # -- queries --------------------------------------------------------------
    def pixmap(self, entry, which: str = "card") -> Optional[QtGui.QPixmap]:
        """Cached pixmap (``card`` / ``row`` / ``full``) or None -- no request."""
        pm = self._pix.get((entry.key, which))
        if pm is not None:
            self._pix.move_to_end((entry.key, which))
        return pm

    def summary(self, entry):
        """Cached PageSummary / ElementSummary (without BMP bytes) or None."""
        return self._summaries.get(entry.key)

    def state(self, entry) -> str:
        """``ready`` / ``loading`` / ``none`` (no embedded thumbnail) / ``idle``."""
        k = entry.key
        if (k, "card") in self._pix:
            return "ready"
        if k in self._no_thumb:
            return "none"
        if (k, _STD) in self._pending:
            return "loading"
        return "idle"

    def has_no_thumbnail(self, entry) -> bool:
        """True once a load found no embedded thumbnail for ``entry``."""
        return entry.key in self._no_thumb

    # -- requests -------------------------------------------------------------
    def _start(self, entry, what: str, priority: int) -> None:
        if self._closed:
            return
        pk = (entry.key, what)
        if pk in self._pending:
            return
        self._pending[pk] = self._generation
        self._pool.start(_Job(entry.key, what, entry, self._sig, self._generation), priority)

    def request(self, entry, which: str = "card", priority: int = _PRIO_VISIBLE) -> Optional[QtGui.QPixmap]:
        """The ``card`` / ``row`` pixmap if cached, else queue a load and
        return None (``thumbnailReady(entry.key)`` fires when it lands)."""
        pm = self.pixmap(entry, which)
        if pm is not None or entry.key in self._no_thumb:
            return pm
        self._start(entry, _STD, priority)
        return None

    def request_full(self, entry, priority: int = _PRIO_FULL) -> Optional[QtGui.QPixmap]:
        """The unscaled embedded thumbnail (the preview pane)."""
        pm = self.pixmap(entry, "full")
        if pm is not None or entry.key in self._no_thumb:
            return pm
        self._start(entry, _FULL, priority)
        return None

    def request_summary(self, entry, priority: int = _PRIO_META):
        """Cached summary, else queue a metadata-only read (no image decode)."""
        s = self._summaries.get(entry.key)
        if s is None and (entry.key, _STD) not in self._pending:
            self._start(entry, _META, priority)
        return s

    def prefetch(self, entries, limit: int = 6000) -> None:
        """Low-priority metadata reads for a scope (fills template / size
        columns); replaces any earlier prefetch backlog."""
        self._prefetch_q = collections.deque(e for e in list(entries)[:limit] if e.key not in self._summaries)
        if self._prefetch_q and not self._closed:
            self._prefetch_timer.start()
            self._prefetch_tick()

    def _prefetch_tick(self, batch: int = 64) -> None:
        # keep only a short queue of metadata jobs in the pool at any time
        room = batch - sum(1 for (_k, w) in self._pending if w == _META)
        while self._prefetch_q and room > 0:
            e = self._prefetch_q.popleft()
            if e.key not in self._summaries and (e.key, _STD) not in self._pending:
                self._start(e, _META, _PRIO_META)
                room -= 1
        if not self._prefetch_q:
            self._prefetch_timer.stop()

    def cancel_pending(self) -> None:
        """Drop queued (not yet running) jobs -- a scope change. Running jobs
        still finish and cache their (valid) result."""
        self._prefetch_q.clear()
        self._prefetch_timer.stop()
        self._pool.clear()
        self._pending.clear()
        self._generation += 1

    def wait_idle(self, timeout_ms: int = 5000) -> bool:
        """Pump events until every queued job has landed (tests / scripts)."""
        timer = QtCore.QElapsedTimer()
        timer.start()
        app = QtCore.QCoreApplication.instance()
        while (self._pending or self._prefetch_q) and timer.elapsed() < timeout_ms:
            self._pool.waitForDone(10)
            if app is not None:
                app.processEvents()
        if app is not None:
            app.processEvents()
        return not self._pending and not self._prefetch_q

    def clear_cache(self) -> None:
        self._pix.clear()
        self._pix_bytes = 0
        self._summaries.clear()
        self._no_thumb.clear()

    def shutdown(self) -> None:
        """Stop accepting work and wait (briefly) for running jobs."""
        self._closed = True
        self._prefetch_q.clear()
        try:
            self._prefetch_timer.stop()
        except RuntimeError:
            pass
        try:
            self._pool.clear()
            self._pool.waitForDone(2000)
        except RuntimeError:
            pass

    # -- results (GUI thread) --------------------------------------------------
    def _store(self, key: str, which: str, img: Optional[QtGui.QImage]) -> None:
        if img is None or img.isNull():
            return
        pm = QtGui.QPixmap.fromImage(img)
        old = self._pix.pop((key, which), None)
        if old is not None:
            self._pix_bytes -= old.width() * old.height() * 4
        self._pix[(key, which)] = pm
        self._pix_bytes += pm.width() * pm.height() * 4
        while self._pix_bytes > self._cache_limit and len(self._pix) > 1:
            (_k, _w), ev = self._pix.popitem(last=False)
            self._pix_bytes -= ev.width() * ev.height() * 4

    def _on_done(self, res: dict) -> None:
        key, what = res["key"], res["what"]
        if self._pending.get((key, what)) == res["gen"]:
            del self._pending[(key, what)]
        s = res["summary"]
        if s is not None:
            self._summaries[key] = s
            self._summaries.move_to_end(key)
            while len(self._summaries) > self._max_summaries:
                self._summaries.popitem(last=False)
        if what == _STD:
            if res["card"] is None:
                self._no_thumb.add(key)
            self._store(key, "card", res["card"])
            self._store(key, "row", res["row"])
        elif what == _FULL:
            if res["full"] is None:
                self._no_thumb.add(key)
            self._store(key, "full", res["full"])
        if s is not None:
            self.summaryReady.emit(key, s)
        if what != _META:
            self.thumbnailReady.emit(key)
