"""The engine's check of a field's Cicode (ctsim/checkExpression)."""

from __future__ import annotations

from .. import cicode_lex as L

#: seconds one check may take (a simulator starting up takes a few)
CHECK_TIMEOUT_S = 60.0

#: E2024 Incompatible types: a genie parameter's type is only known once it is placed
TYPE_ERRORS = frozenset({2024})


def check_field(session, text: str, kind: str) -> dict:
    """``text`` compiled by ``session``'s engine: {ok, errors, warnings}. Genie parameters and
    super genie associations compile as placeholder names, and what is said of those is dropped."""
    filled, spans = L.fill_template(text)
    result = check_text(session, filled, kind)
    if spans:
        result = without_template(result, spans, [filled[s:e] for s, e in spans])
    return result


def check_text(session, text: str, kind: str) -> dict:
    """``text`` compiled as it is by ``session``'s engine: {ok, errors, warnings}."""
    body = session.request("checkExpression", {"text": text, "kind": kind}, timeout=CHECK_TIMEOUT_S) or {}
    return {
        "ok": bool(body.get("ok")),
        "errors": list(body.get("errors") or ()),
        "warnings": list(body.get("warnings") or ()),
    }


def without_template(result: dict, spans, names) -> dict:
    """``result`` without the problems about the placeholder names at ``spans`` (``names``)."""
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
