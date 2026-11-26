"""The engine's check of a page field's Cicode (ctsim/checkExpression): one field, over a
session the caller holds. The editor's fields (ui/expression_check) and the project check
(workers/check_worker) both check through :func:`check_field`."""

from __future__ import annotations

from .. import cicode_lex as L

#: how long one check may take (a simulator that starts first takes seconds)
CHECK_TIMEOUT_S = 60.0

#: the compiler's type errors (E2024 Incompatible types): a genie parameter's type is
#: known when the genie is placed, the compiler reads the name it is given as a number
TYPE_ERRORS = frozenset({2024})


def check_field(session, text: str, kind: str) -> dict:
    """``text`` ('expression' or 'command') compiled by ``session``'s engine:
    ``{"ok", "errors", "warnings"}``. A genie's parameters and a super genie's associations
    are compiled as names, and what the compiler says about the names they make is left out
    (:func:`without_template`). Raises what the session raises."""
    filled, spans = L.fill_template(text)
    body = session.request("checkExpression", {"text": filled, "kind": kind}, timeout=CHECK_TIMEOUT_S) or {}
    result = {
        "ok": bool(body.get("ok")),
        "errors": list(body.get("errors") or ()),
        "warnings": list(body.get("warnings") or ()),
    }
    if spans:
        result = without_template(result, spans, [filled[s:e] for s, e in spans])
    return result


def without_template(result: dict, spans, names) -> dict:
    """``result`` without the errors at ``spans`` (the names fill_template made), the
    errors and warnings that name one of ``names``, and the type errors."""
    if result.get("error"):
        return result
    low = [n.lower() for n in names]

    def about_template(problem: dict) -> bool:
        msg = str(problem.get("message") or "").lower()
        if any(n in msg for n in low):
            return True
        if problem.get("code") in TYPE_ERRORS:
            return True
        col = int(problem.get("column") or 0) - 1
        return any(s <= col < e for s, e in spans)

    errors = [e for e in result.get("errors") or () if not about_template(e)]
    warnings = [w for w in result.get("warnings") or () if not about_template(w)]
    return {"ok": not errors, "errors": errors, "warnings": warnings}
