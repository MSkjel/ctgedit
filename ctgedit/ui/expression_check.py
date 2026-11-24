"""Check the Cicode typed in an expression field."""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Dict, Optional

from PySide6 import QtCore

from ..infra import gcguard, logsetup

log = logsetup.get("expression_check")

#: the simulator started for checks ends after this long without one (seconds)
IDLE_STOP_S = 300.0
#: how long one check may take (a simulator that starts first takes seconds)
CHECK_TIMEOUT_S = 60.0

Callback = Callable[[dict], None]


class ExpressionChecker(QtCore.QObject):
    """The checker of one project folder (:func:`checker_for`)."""

    #: (callback, result): delivered on the GUI thread
    _ready = QtCore.Signal(object, object)

    def __init__(self, project_dir: str, parent=None):
        super().__init__(parent)
        self.project_dir = project_dir
        self._lock = threading.Lock()
        self._session = None
        self._last_used = time.monotonic()
        self._ready.connect(self._deliver)
        self._idle = QtCore.QTimer(self)
        self._idle.setInterval(int(IDLE_STOP_S * 1000))
        self._idle.timeout.connect(self._stop_if_idle)
        #: the opener (tests inject a fake): (project_dir) -> a Session
        self.opener = None

    # -- the public part (GUI thread) --------------------------------------------------
    def check(self, text: str, kind: str, callback: Callback) -> None:
        """Compile ``text`` ('expression' or 'command') on a thread; ``callback(result)`` on
        the GUI thread with ``{"ok", "errors", "warnings"}`` or ``{"error": text}``. A genie's
        parameters and a super genie's associations are compiled as names, and what the
        compiler says about the names they make is left out (without_template)."""
        from .. import cicode_lex as L

        self._last_used = time.monotonic()
        self._idle.start()
        filled, spans = L.fill_template(text)
        if spans:
            names = [filled[s:e] for s, e in spans]
            callback = lambda result, done=callback: done(without_template(result, spans, names))  # noqa: E731
        t = threading.Thread(
            target=self._run, args=(filled, kind, callback), name="expression-check", daemon=True
        )
        t.start()

    def busy(self) -> bool:
        return self._lock.locked()

    def close(self) -> None:
        """End the session (and the simulator it started) now."""
        self._idle.stop()
        threading.Thread(target=self._close, name="expression-check-close", daemon=True).start()

    # -- the thread ----------------------------------------------------------------------
    def _run(self, text: str, kind: str, callback: Callback) -> None:
        gcguard.before_worker()
        with self._lock:
            try:
                s = self._open()
                body = (
                    s.request("checkExpression", {"text": text, "kind": kind}, timeout=CHECK_TIMEOUT_S) or {}
                )
                result = {
                    "ok": bool(body.get("ok")),
                    "errors": list(body.get("errors") or ()),
                    "warnings": list(body.get("warnings") or ()),
                }
            except Exception as e:  # noqa: BLE001  # swallow-ok: the field shows the error
                self._drop()
                result = {"error": str(e).strip() or type(e).__name__}
        self._ready.emit(callback, result)

    def _open(self):
        s = self._session
        if s is not None and getattr(s, "alive", True):
            return s
        self._drop()
        if self.opener is not None:
            s = self.opener(self.project_dir)
        else:
            from ..sim import client as C
            from .simulate.window import engine_setting

            s = C.open_session(self.project_dir, engine_path=engine_setting() or None, viewer=False)
        self._session = s
        return s

    def _drop(self) -> None:
        s, self._session = self._session, None
        if s is not None:
            try:
                s.close()
            except Exception:  # noqa: BLE001  # swallow-ok: a session that is gone already
                pass

    def _close(self) -> None:
        gcguard.before_worker()
        with self._lock:
            self._drop()

    # -- the GUI thread -------------------------------------------------------------------
    def _deliver(self, callback, result) -> None:
        try:
            callback(result)
        except Exception:  # noqa: BLE001
            logsetup.note_exception("expression_check.deliver")

    def _stop_if_idle(self) -> None:
        if time.monotonic() - self._last_used >= IDLE_STOP_S and not self.busy():
            self.close()


_CHECKERS: Dict[str, ExpressionChecker] = {}


def checker_for(project_dir: Optional[str]) -> Optional[ExpressionChecker]:
    """The checker of a project folder (one per folder, made on first use); None without
    one (a page outside a project)."""
    if not project_dir:
        return None
    key = os.path.normcase(os.path.abspath(project_dir))
    c = _CHECKERS.get(key)
    if c is None:
        c = _CHECKERS[key] = ExpressionChecker(project_dir)
    return c


def checker_for_document(doc) -> Optional[ExpressionChecker]:
    """The checker of ``doc``'s project (a page's folder; a library element's library folder)."""
    path = getattr(doc, "path", "") or ""
    if not path:
        return None
    return checker_for(os.path.dirname(os.path.abspath(path)))


def shutdown_all() -> None:
    """End every checker's session (the application ends)."""
    for c in list(_CHECKERS.values()):
        c.close()


#: the compiler's type errors (E2024 Incompatible types): a genie parameter's type is
#: known when the genie is placed, the compiler reads the name it is given as a number
TYPE_ERRORS = frozenset({2024})


def without_template(result: dict, spans, names) -> dict:
    """``result`` without the errors at ``spans`` (the names fill_template made), the
    errors and warnings that name one of ``names``, and the type errors."""
    if result.get("error"):
        return result
    low = [n.lower() for n in names]

    def about_template(problem: dict, at_column: bool) -> bool:
        msg = str(problem.get("message") or "").lower()
        if any(n in msg for n in low):
            return True
        if at_column and problem.get("code") in TYPE_ERRORS:
            return True
        col = int(problem.get("column") or 0) - 1
        return at_column and any(s <= col < e for s, e in spans)

    errors = [e for e in result.get("errors") or () if not about_template(e, True)]
    warnings = [w for w in result.get("warnings") or () if not about_template(w, False)]
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def summary(result: dict) -> str:
    """One line per problem of a check result ('' when it found none)."""
    if result.get("error"):
        return "Not checked: %s" % result["error"]
    lines = []
    for e in result.get("errors") or ():
        col = e.get("column") or 0
        name = e.get("name") or ""
        lines.append(
            "%s%s%s"
            % (name + " " if name else "", e.get("message") or "", (" (column %d)" % col) if col else "")
        )
    for w in result.get("warnings") or ():
        lines.append(str(w.get("message") or ""))
    return "\n".join(lines)


from ..infra import caches as _caches  # noqa: E402

_caches.register("expression_check.checkers", _CHECKERS, clear=shutdown_all, on=("tests",))
