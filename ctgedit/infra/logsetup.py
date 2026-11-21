r"""The activity log, ``<data folder>\logs\ctgedit.log``."""

from __future__ import annotations

import collections
import contextlib
import logging
import logging.handlers
import os
import sys
import threading
import time
from typing import Dict, Iterable, List, Optional, Tuple

LOG_NAME = "ctgedit.log"
MAX_BYTES = 1 << 20
BACKUPS = 5

root = logging.getLogger("ctgedit")
root.addHandler(logging.NullHandler())
_state = {"handler": None, "prev_thread_hook": None}


def log_dir() -> str:
    from .. import crashlog

    return crashlog.log_dir()


def log_path() -> str:
    return os.path.join(log_dir(), LOG_NAME)


def get(area: str) -> logging.Logger:
    return logging.getLogger("ctgedit." + area)


def install(level: int = logging.INFO) -> Optional[str]:
    """Start writing the activity log (idempotent); also routes uncaught
    exceptions of background threads into it. Returns the log file, None
    when the log folder cannot be written."""
    if _state["handler"] is not None:
        return _state["handler"].baseFilename
    d = log_dir()
    try:
        os.makedirs(d, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(
            os.path.join(d, LOG_NAME), maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True
        )
    except OSError:
        return None
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(h)
    root.setLevel(level)
    _state["handler"] = h
    try:
        from ..buildinfo import version_string

        ident = version_string()
    except Exception:  # noqa: BLE001 -- a log header must never fail
        ident = "?"
    root.info("ctgedit %s started (pid %d, Python %s)", ident, os.getpid(), sys.version.split()[0])

    prev = threading.excepthook
    _state["prev_thread_hook"] = prev

    def thread_hook(args):
        if args.exc_type is not SystemExit:
            root.error(
                "unhandled exception in thread %s",
                getattr(args.thread, "name", "?"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        prev(args)

    threading.excepthook = thread_hook
    return h.baseFilename


def uninstall() -> None:
    h = _state["handler"]
    if h is None:
        return
    root.info("ctgedit exited normally")
    root.removeHandler(h)
    h.close()
    _state["handler"] = None
    if _state["prev_thread_hook"] is not None:
        threading.excepthook = _state["prev_thread_hook"]
        _state["prev_thread_hook"] = None


def log_exception(etype, value, tb) -> None:
    """An exception that escaped a command (the app's error hook)."""
    root.error("command failed: %s: %s", etype.__name__, value, exc_info=(etype, value, tb))


# ---------------------------------------------------------------------------
# Swallowed errors on the read / render path.
#
# The reader, the symbol resolver and the graft code keep going when a record,
# a library or an element cannot be read (``except Exception: pass`` -- a page
# must always open), so a failure there only shows as missing art. Those
# blocks call :func:`note_exception`: a WARNING with the traceback in the
# activity log, at most :data:`SWALLOW_LOG_MAX` times per place and exception
# type in a window (a page open -- :func:`error_scope` -- or
# :data:`SWALLOW_WINDOW_S` seconds), then one line with how many more were not
# logged. Control flow never changes: the helper never raises. The counts of
# the current scope (per thread) go into the page's open line.

SWALLOW_LOG_MAX = 3
SWALLOW_WINDOW_S = 60.0
swallowed_log = logging.getLogger("ctgedit.swallowed")
_sw_lock = threading.Lock()
#: (where, exception type) -> [logged in window, not logged in window, window start, epoch]
_sw_state: Dict[Tuple[str, str], list] = {}
_sw_total: "collections.Counter" = collections.Counter()
_sw_epoch = [0]
_tls = threading.local()
#: callables told of every swallowed error noted on their thread (where, etype): the genie
#: composition's memo drops what the open computations would keep (format/genie_place: a
#: composition that swallowed a nested load's failure is not the library files' answer)
_note_listeners: list = []


def on_note(fn) -> None:
    """Call ``fn(where, etype)`` on the noting thread for every swallowed error noted
    (:func:`note_exception`, :func:`note_problem`); it must never raise."""
    if fn not in _note_listeners:
        _note_listeners.append(fn)


def note_exception(where: str, exc: Optional[BaseException] = None, **ctx) -> None:
    """Log an exception a read / render path swallowed (call it in the
    ``except`` block; ``exc`` defaults to the one being handled). ``ctx`` =
    what it was about (a library, an element, a record index). Never raises."""
    try:
        if exc is None:
            exc = sys.exc_info()[1]
        etype = type(exc).__name__ if exc is not None else "error"
        _note(where, etype, exc, None, ctx)
    except Exception:  # noqa: BLE001 -- a diagnostic must never fail its caller
        pass


def note_problem(where: str, message: str, **ctx) -> None:
    """A failure that raised nothing (a GDI call that returned 0, a cache that
    handed back a placeholder): logged like :func:`note_exception`, without a
    traceback."""
    try:
        _note(where, "problem", None, message, ctx)
    except Exception:  # noqa: BLE001
        pass


def _note(where: str, etype: str, exc, message, ctx) -> None:
    key = (where, etype)
    now = time.monotonic()
    scope = getattr(_tls, "scope", None)
    if scope is not None:
        scope[key] = scope.get(key, 0) + 1
    for fn in _note_listeners:
        try:
            fn(where, etype)
        except Exception:  # noqa: BLE001 -- swallow-ok: a listener never fails the note
            pass
    flush = 0
    with _sw_lock:
        _sw_total[key] += 1
        st = _sw_state.get(key)
        if st is None:
            st = _sw_state[key] = [0, 0, now, _sw_epoch[0]]
        elif st[3] != _sw_epoch[0] or now - st[2] > SWALLOW_WINDOW_S:
            flush = st[1]
            st[:] = [0, 0, now, _sw_epoch[0]]
        if st[0] < SWALLOW_LOG_MAX:
            st[0] += 1
            show = True
        else:
            st[1] += 1
            show = False
    if flush:
        swallowed_log.warning("%s: %s happened %d more time(s) (not logged)", where, etype, flush)
    if not show:
        return
    about = ", ".join("%s=%s" % (k, _short(v)) for k, v in sorted(ctx.items()))
    if exc is not None:
        swallowed_log.warning(
            "%s: swallowed %s: %s%s",
            where,
            etype,
            _short(exc, 300),
            " (%s)" % about if about else "",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    else:
        swallowed_log.warning("%s: %s%s", where, message, " (%s)" % about if about else "")


def _short(v, n: int = 160) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 3] + "..."


def begin_error_scope() -> dict:
    """Start counting this thread's swallowed errors (a page open) and let
    every place log its first few again. Returns the counts dict
    (``(where, type) -> n``); :func:`end_error_scope` stops it."""
    counts: dict = {}
    prev = getattr(_tls, "scope", None)
    stack = getattr(_tls, "stack", None)
    if stack is None:
        stack = _tls.stack = []
    stack.append(prev)
    _tls.scope = counts
    with _sw_lock:
        _sw_epoch[0] += 1
    return counts


def end_error_scope(counts: dict) -> dict:
    """Stop the scope :func:`begin_error_scope` returned (the one before it
    counts again); the counts, also added to an enclosing scope."""
    stack = getattr(_tls, "stack", None) or []
    if getattr(_tls, "scope", None) is counts:
        _tls.scope = stack.pop() if stack else None
        if _tls.scope is not None:
            for k, v in counts.items():
                _tls.scope[k] = _tls.scope.get(k, 0) + v
    return counts


@contextlib.contextmanager
def error_scope():
    """``with error_scope() as counts:`` -- :func:`begin_error_scope` /
    :func:`end_error_scope` around a block."""
    counts = begin_error_scope()
    try:
        yield counts
    finally:
        end_error_scope(counts)


def swallowed_totals() -> Dict[Tuple[str, str], int]:
    """Every swallowed error of this process so far, by (where, type)."""
    with _sw_lock:
        return dict(_sw_total)


def format_counts(counts: dict, limit: int = 8) -> str:
    """'where Type x3, ...' of a scope's counts (most frequent first)."""
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out = ["%s %s x%d" % (w, t, n) for (w, t), n in items[:limit]]
    if len(items) > limit:
        out.append("… %d more place(s)" % (len(items) - limit))
    return ", ".join(out)


# ---------------------------------------------------------------------------
# What happened recently (Help > Copy Diagnostics, a display-check warning):
# page opens / closes and display-check results of this run.

_EVENTS: "collections.deque" = collections.deque(maxlen=40)
_EVENTS_LOCK = threading.Lock()


def remember(kind: str, text: str) -> None:
    """Keep ``text`` in the recent-events list (kind 'open', 'close',
    'switch', 'auto', 'check', 'recovery')."""
    with _EVENTS_LOCK:
        _EVENTS.append((time.strftime("%H:%M:%S"), kind, text))


def recent_events(kinds: Optional[Iterable[str]] = None, last: int = 40) -> List[str]:
    want = set(kinds) if kinds is not None else None
    with _EVENTS_LOCK:
        evs = list(_EVENTS)
    out = ["%s %s: %s" % (t, k, s) for t, k, s in evs if want is None or k in want]
    return out[-last:]


def undo_labels(doc, last: int = 8) -> List[str]:
    """The labels of ``doc``'s last undo steps (oldest first)."""
    try:
        return [str(getattr(c, "label", "?")) for c in list(getattr(doc, "_undo", []))[-last:]]
    except Exception:  # noqa: BLE001
        return []


def files_written(what: str, paths: Iterable[str], note: str = "") -> None:
    """One line per save-like operation: what, and every file it wrote."""
    paths = [p for p in paths if p]
    get("files").info(
        "%s: %d file(s)%s:\n    %s",
        what,
        len(paths),
        " (%s)" % note if note else "",
        "\n    ".join(paths) or "(none)",
    )


def tail(path: str, lines: int = 60) -> List[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()[-64 * 1024 :].splitlines()[-lines:]
    except OSError:
        return []


#: functions giving lines for the top of a diagnostics report (the build and system, the
#: active user folder): registered by the application (app.py), so this module needs
#: nothing above it
_DETAIL_SOURCES: List = []


def add_details_source(fn) -> None:
    """``fn()`` -> text: put at the top of :func:`diagnostics_text`."""
    if fn not in _DETAIL_SOURCES:
        _DETAIL_SOURCES.append(fn)


def diagnostics_text(documents: Iterable = (), lines: int = 60) -> str:
    """What a problem report needs: the build and system (Help > About's
    details), where the logs are, the open documents and the end of the
    activity and error logs."""
    out: List[str] = []
    if not _DETAIL_SOURCES:
        out.append("ctgedit\nPython: %s" % sys.version.split()[0])
    for fn in list(_DETAIL_SOURCES):
        try:
            text = fn()
        except Exception:  # noqa: BLE001 -- the rest of the report still goes
            text = ""
        if text:
            out.append(text)
    out.append("Platform: %s" % sys.platform)
    out.append("Activity log: %s" % log_path())
    docs = list(documents)
    out.append("")
    out.append("Open documents (%d):" % len(docs))
    for d in docs:
        flags = [
            f
            for f, on in (
                ("unsaved changes", getattr(d, "dirty", False)),
                ("read-only", getattr(d, "read_only", False)),
                ("damaged when opened", bool(getattr(d, "damage", None))),
            )
            if on
        ]
        # a page has no display name: its file name (the path follows)
        name = getattr(d, "display_name", None) or os.path.basename(getattr(d, "path", "") or "?")
        out.append("  %s%s: %s" % (name, " [%s]" % ", ".join(flags) if flags else "", getattr(d, "path", "")))
        labels = undo_labels(d, 6)
        if labels:
            out.append("    last undo steps: %s" % " | ".join(labels))
        rep = getattr(getattr(d, "ui", None), "diag_report", None)
        if rep is not None and getattr(rep, "unresolved", None):
            out.append(
                "    %d reference(s) not found: %s"
                % (len(rep.unresolved), "; ".join(u.text() for u in rep.unresolved[:5]))
            )
        chk = getattr(getattr(d, "ui", None), "display_check", None)
        if chk is not None and getattr(chk, "differences", None):
            out.append(
                "    display check: %d object(s) differ from a fresh read (%s)"
                % (len(chk.differences), "; ".join(chk.differences[:3]))
            )
    for title, kinds in (
        ("page opens / closes / automatic changes", ("open", "switch", "close", "auto", "recovery")),
        ("display checks", ("check",)),
    ):
        evs = recent_events(kinds, 20)
        out.append("")
        out.append("Recent %s (%d):" % (title, len(evs)) if evs else "No %s this run." % title)
        out.extend("  " + e for e in evs)
    totals = swallowed_totals()
    if totals:
        out.append("")
        out.append("Errors the reader / renderer skipped this run: %s" % format_counts(totals, 12))
    for title, path in (("activity log", log_path()), ("error log", os.path.join(log_dir(), "errors.log"))):
        t = tail(path, lines)
        out.append("")
        out.append("Last %d lines of the %s:" % (len(t), title) if t else "The %s is empty." % title)
        out.extend("  " + ln for ln in t)
    return "\n".join(out)
