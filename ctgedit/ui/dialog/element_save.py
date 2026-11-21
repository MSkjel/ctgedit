"""Save a large library element without freezing the window."""

from __future__ import annotations

import contextlib
import json
import os
import time
from typing import Optional

from PySide6 import QtCore, QtWidgets

from ...infra import logsetup


class SaveHelper:
    """The helper process of one editor window for the saves of its large elements. It stays
    between saves, so a second save of the same element is faster (the composer's caches stay
    warm), and ends when idle as the genie helper does (Settings > Long jobs)."""

    #: how often the wait looks for the helper's answer and the window paints (s; as often
    #: as JobProgress.tick paints)
    POLL_S = 0.05

    def __init__(self, window):
        from ..jobs.genie_jobs import WarmWorker

        self.worker = WarmWorker()
        self._idle = QtCore.QTimer(window)
        self._idle.setInterval(30000)
        self._idle.timeout.connect(lambda: self.worker.idle_stop(False))
        self._idle.start()
        #: the time the last save waited for the helper (s; the tests and the log read it)
        self.last_wait_s = 0.0

    def stop(self) -> None:
        with contextlib.suppress(RuntimeError):
            self._idle.stop()
        self.worker.stop()

    def prepare(self, window, doc, refresh: bool, every_nested: bool, redraw: bool = False) -> Optional[dict]:
        """Get the save's memo from a helper process, or None for small elements."""
        from ..prefs.job_options import save_helper_records

        least = save_helper_records()
        objs = getattr(getattr(getattr(doc, "parsed", None), "page", None), "objects", None)
        if least <= 0 or objs is None or len(objs) < least:
            return None
        from ...workers import genie_worker as GW
        from ...infra.paths import data_dir

        req = GW.write_request(
            data_dir("genieupdate"),
            doc.path,
            doc.serialize(),
            {},
            element=doc.content_memo_request(refresh, every_nested, thumbnail=redraw),
        )
        ans_path = req[:-5] + ".answer.json"
        t0 = time.monotonic()
        try:
            self.worker.submit(req, ans_path)
            ans = self._wait(window, doc, ans_path)
        finally:
            self.last_wait_s = time.monotonic() - t0
            for p in (req, req + ".page", ans_path):
                with contextlib.suppress(OSError):
                    os.remove(p)
        if not isinstance(ans, dict) or ans.get("format") != GW.FORMAT or "memo" not in ans:
            if isinstance(ans, dict) and ans.get("error"):
                logsetup.get("genies").warning(
                    "the save of %s in the helper failed: %s\n%s",
                    doc.display_name,
                    ans.get("error"),
                    ans.get("trace", ""),
                )
            return None
        memo = doc.memo_from_helper(ans["memo"], refresh, every_nested)
        drawn = (ans["memo"] or {}).get("thumbnail_as_stored") if redraw else None
        if memo is not None and drawn:
            try:
                memo["thumbnail_as_stored"] = bytes.fromhex(drawn)
            except TypeError, ValueError:  # swallow-ok: the save draws it itself then
                pass
        logsetup.get("genies").info(
            "saved %s with the helper's normalisation (%.1f s)%s",
            doc.display_name,
            self.last_wait_s,
            "" if memo else ": not taken, made here",
        )
        return memo

    def _wait(self, window, doc, ans_path: str):
        """The helper's answer (None when it ended without one), the window alive meanwhile:
        it paints, the status bar says what it does, no input reaches it until the progress
        window shows (JobProgress), and the window's other tabs take their updates."""
        from ..prefs.job_options import save_window_ms
        from ..jobs.job_progress import JobProgress

        head = "Saving %s…" % doc.display_name
        bar = getattr(window, "statusBar", None)
        # (its line goes from the status bar when the save ends, and what the bar showed
        # comes back: JobProgress status_bar)
        prog = JobProgress(
            window,
            "Saving",
            head,
            cancel=None,
            show_after_ms=save_window_ms(),
            status_bar=bar() if callable(bar) else None,
            lets_tabs_update=True,
        )
        try:
            while not os.path.exists(ans_path) and self.worker.alive():
                prog.pulse()
                time.sleep(self.POLL_S)
        finally:
            prog.close()
        try:
            with open(ans_path, encoding="utf-8") as fh:
                return json.load(fh)
        except OSError, ValueError:  # swallow-ok: the helper ended without an answer
            return None


def helper_of(window) -> SaveHelper:
    """The window's :class:`SaveHelper`, made on first use."""
    helper = window.__dict__.get("_save_helper")
    if helper is None:
        helper = window._save_helper = SaveHelper(window)
    return helper


def stop(window) -> None:
    """The window closes: its save helper ends."""
    helper = window.__dict__.pop("_save_helper", None)
    if helper is not None:
        helper.stop()


def prepare_for(window):
    """ElementDocument.save's ``prepare`` for a save from ``window``'s tab: the window's
    :class:`SaveHelper` (None outside a running event loop: a script or a test saves in its
    own process)."""
    if QtCore.QThread.currentThread().loopLevel() <= 0 or QtWidgets.QApplication.instance() is None:
        return None
    helper = helper_of(window)
    return lambda doc, refresh, every_nested, redraw=False: helper.prepare(
        window, doc, refresh, every_nested, redraw
    )
