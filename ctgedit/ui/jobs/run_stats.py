"""Progress figures for long runs: done, elapsed, time left and rate."""

from __future__ import annotations

import math
import time
from typing import Callable, Dict, Iterable, List, Optional

#: the phases a run names: (what the window says, one item, items)
PHASES = {
    "check": ("Checking the pages", "page", "pages"),
    "compose": ("Updating composite genies", "page", "pages"),
    "pages": ("Updating the pages", "page", "pages"),
    "genies": ("Saving the library genies", "genie", "genies"),
    "libraries": ("Reading the libraries", "library", "libraries"),
    "compare": ("Comparing the library genies", "genie", "genies"),
    # the usage index (Used By, a genie or symbol save), the pages open in
    # tabs after Update Pages / Update Users, Save All, Cut Link of a selection, Find and
    # Replace All in the closed pages, Find Duplicate Pictures
    "index": ("Reading the pages and libraries", "file", "files"),
    "tabs": ("Updating the pages open in tabs", "page", "pages"),
    "save": ("Saving the pages", "page", "pages"),
    "unlink": ("Cutting the links", "symbol", "symbols"),
    "search": ("Searching the closed pages", "page", "pages"),
    "replace": ("Replacing in the closed pages", "page", "pages"),
    "pictures": ("Reading the pages", "page", "pages"),
}

#: a page record's outcome (io/updatepages.OUTCOMES) -> the count it adds to (None: none;
#: 'deferred' is counted when the composite pass does the page)
OUTCOME_COUNTS = {
    "updated": "updated",
    "current": "unchanged",
    "unchanged": "unchanged",
    "skipped_open": "skipped",
    "open_in_builder": "skipped",
    "changed_meanwhile": "skipped",
    "db_in_use": "skipped",
    "failed": "failed",
    "deferred": None,
    "not_checked": None,
}
COUNT_NAMES = ("updated", "unchanged", "skipped", "failed")


def duration_text(seconds: float) -> str:
    """'12 s', '1 min 5 s', '3 min', '1 h 20 min'."""
    s = max(0, int(round(float(seconds))))
    if s < 60:
        return "%d s" % s
    if s < 3600:
        m, r = divmod(s, 60)
        return "%d min %d s" % (m, r) if r else "%d min" % m
    h, r = divmod(s, 3600)
    m = r // 60
    return "%d h %d min" % (h, m) if m else "%d h" % h


def left_text(seconds: float) -> str:
    """'a few seconds left', 'about 40 s left', 'about 2 min left', 'about 1 h 20 min
    left' (rounder the further it is: 5 s, a minute, 5 minutes)."""
    s = max(0.0, float(seconds))
    if s < 10:
        return "a few seconds left"
    if s < 55:
        return "about %d s left" % max(10, int(5 * round(s / 5)))
    if s < 3600 - 30:
        return "about %d min left" % max(1, int(round(s / 60)))
    m = int(5 * round(s / 300))
    h, m = divmod(m, 60)
    return "about %d h %d min left" % (h, m) if m else "about %d h left" % h


def rate_text(per_min: float, one: str, many: str) -> str:
    """'40 pages a minute', '2.5 pages a minute', '1 page a minute'."""
    if per_min >= 10:
        v = "%d" % int(round(per_min))
    else:
        v = ("%.1f" % per_min).rstrip("0").rstrip(".")
    return "%s %s a minute" % (v, one if v == "1" else many)


class RunStats:
    """The figures of one run. :meth:`begin_phase` at each phase ('check', 'compose',
    'genies', 'pages', 'libraries'), :meth:`update` with the items done in it,
    :meth:`add_records` with the page records seen; :meth:`lines` / :meth:`short_text` say
    them, :meth:`total_text` at the end. ``clock`` = seconds (time.monotonic)."""

    def __init__(
        self,
        clock: Optional[Callable[[], float]] = None,
        show: bool = True,
        min_items: int = 3,
        min_seconds: float = 5.0,
        smoothing_s: float = 10.0,
        workers: int = 1,
    ):
        self._clock = clock or time.monotonic
        self.show = bool(show)
        self.min_items = max(1, int(min_items))
        self.min_seconds = max(0.0, float(min_seconds))
        self.smoothing_s = max(0.0, float(smoothing_s))
        self.workers = max(1, int(workers))
        self.t0 = self._clock()
        self.phase = ""
        self.total = 0
        self.done = 0
        self._phase_t0 = self.t0
        self._eta: Optional[float] = None
        self._eta_t = self.t0
        self.counts: Dict[str, int] = {k: 0 for k in COUNT_NAMES}
        self.pages_seen = False

    @classmethod
    def from_settings(cls, clock=None, workers: int = 1) -> "RunStats":
        """A RunStats with Settings > Pages and libraries > Progress figures."""
        from ..prefs.settings import run_figures

        show, items, seconds, smoothing = run_figures()
        return cls(
            clock=clock,
            show=show,
            min_items=items,
            min_seconds=seconds,
            smoothing_s=smoothing,
            workers=workers,
        )

    # -- feeding it ------------------------------------------------------------------------
    def begin_phase(self, phase: str, total: int) -> None:
        """A phase starts (again: the same phase with another total keeps its clock)."""
        if phase == self.phase:
            self.total = max(0, int(total))
            return
        self.phase, self.total, self.done = phase, max(0, int(total)), 0
        self._phase_t0 = self._clock()
        self._eta, self._eta_t = None, self._phase_t0

    def update(self, done: int, total: Optional[int] = None) -> None:
        """``done`` items of the phase are finished (it never goes back)."""
        if total is not None:
            self.total = max(0, int(total))
        self.done = max(self.done, min(int(done), self.total or int(done)))
        now = self._clock()
        if not self.enough(now):
            self._eta, self._eta_t = None, now
            return
        spent = now - self._phase_t0
        raw = max(0, self.total - self.done) * spent / self.done
        if self._eta is None or self.smoothing_s <= 0:
            eta = raw
        else:
            dt = max(0.0, now - self._eta_t)
            guess = max(0.0, self._eta - dt)  # (it counts down meanwhile)
            eta = guess + (1.0 - math.exp(-dt / self.smoothing_s)) * (raw - guess)
        self._eta, self._eta_t = (0.0 if self.done >= self.total else eta), now

    def add_records(self, records: Iterable[dict]) -> None:
        """Count page records (io/updatepages.page_record) into the outcomes so far."""
        for r in records:
            self.pages_seen = True
            k = OUTCOME_COUNTS.get(str((r or {}).get("outcome") or ""))
            if k:
                self.counts[k] += 1

    # -- reading it ------------------------------------------------------------------------
    def elapsed(self) -> float:
        """Seconds since the run started."""
        return max(0.0, self._clock() - self.t0)

    def enough(self, now: Optional[float] = None) -> bool:
        """The phase has done enough items and run long enough for a rate and an
        estimate."""
        now = self._clock() if now is None else now
        return (
            self.done >= max(self.min_items, self.workers)
            and self.total > 0
            and now - self._phase_t0 >= self.min_seconds
        )

    def rate_per_min(self) -> Optional[float]:
        """Items a minute in this phase (wall time), None without enough data."""
        now = self._clock()
        if not self.enough(now):
            return None
        return self.done * 60.0 / max(1e-6, now - self._phase_t0)

    def remaining(self) -> Optional[float]:
        """Seconds left in this phase (smoothed), None without enough data."""
        if self._eta is None:
            return None
        return max(0.0, self._eta - max(0.0, self._clock() - self._eta_t))

    def _words(self):
        return PHASES.get(self.phase, ("Working", "item", "items"))

    def time_text(self) -> str:
        """'1 min 5 s so far, about 2 min left, 40 pages a minute' (the estimate and the
        rate once there is enough data; '' when the figures are off)."""
        if not self.show:
            return ""
        _title, one, many = self._words()
        text = "%s so far" % duration_text(self.elapsed())
        left, rate = self.remaining(), self.rate_per_min()
        if left is not None:
            text += ", " + left_text(left)
        if rate is not None:
            text += ", " + rate_text(rate, one, many)
        return text

    def counts_text(self) -> str:
        """'3 updated, 2 unchanged, 0 skipped, 0 failed' ('' before the first page record,
        or when the figures are off)."""
        if not self.show or not self.pages_seen:
            return ""
        return ", ".join("%d %s" % (self.counts[k], k) for k in COUNT_NAMES)

    def lines(self) -> List[str]:
        """'Checking the pages: 5 of 120 done', 'Time: 1 min 5 s so far, about 2 min left,
        40 pages a minute', 'So far: 3 updated, 2 unchanged, 0 skipped, 0 failed' (none when
        the figures are off)."""
        if not self.show:
            return []
        title = self._words()[0]
        out = ["%s: %d of %d done" % (title, self.done, self.total), "Time: " + self.time_text()]
        counts = self.counts_text()
        if counts:
            out.append("So far: " + counts)
        return out

    def short_text(self) -> str:
        """The status bar's words: '5 of 120 pages, about 2 min left' ('' when off)."""
        if not self.show:
            return ""
        _title, one, many = self._words()
        text = "%d of %d %s" % (self.done, self.total, one if self.total == 1 else many)
        left = self.remaining()
        return text + (", " + left_text(left) if left is not None else "")

    def step_text(self) -> str:
        """The background task's words: 'checking the pages, about 2 min left' (the step
        named, lower case; the estimate once there is enough data and the figures are on)."""
        text = self._words()[0]
        text = text[:1].lower() + text[1:]
        left = self.remaining() if self.show else None
        return text + (", " + left_text(left) if left is not None else "")

    # -- the widest it says ----------------------------------------------------------------
    @staticmethod
    def widest_times(most: int = 9999) -> List[str]:
        """Time texts as wide as :meth:`time_text` makes them (the longest times and
        rates of every phase, ``most`` items a minute): what a window measures to keep
        its width while the figures change."""
        out: List[str] = []
        for _title, one, many in PHASES.values():
            rate = rate_text(float(max(10, int(most))), one, many)
            for spent in (59 * 60 + 59, 10 * 3600 + 59 * 60):
                for left in (59 * 60 - 31, 3600 + 55 * 60):
                    t = "%s so far, %s, %s" % (duration_text(spent), left_text(left), rate)
                    if t not in out:
                        out.append(t)
        return out

    @classmethod
    def widest_lines(cls, most: int = 9999) -> List[str]:
        """Lines as wide as :meth:`lines` makes them (every phase's words, the widest
        times, ``most`` items) for the window to measure."""
        num = str(max(1, int(most)))
        out = ["%s: %s of %s done" % (title, num, num) for title, _o, _m in PHASES.values()]
        out += ["Time: " + t for t in cls.widest_times(most)]
        out.append("So far: " + ", ".join("%s %s" % (num, k) for k in COUNT_NAMES))
        return out

    #: the lines :meth:`lines` gives at most (a window keeps room for them all)
    LINES = 3

    def total_text(self) -> str:
        """'Time taken: 3 min 20 s.' ('' when the figures are off)."""
        return "Time taken: %s." % duration_text(self.elapsed()) if self.show else ""
