"""Checks for the sheet's expression and command fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

from ... import cicode_lex as L

ERROR, WARNING = "error", "warning"


@dataclass(frozen=True)
class Problem:
    level: str  # ERROR (refuses the value) or WARNING (advice)
    message: str


_HOOKS: List[Callable] = []


def register(hook: Callable) -> Callable:
    """Add an advice hook ``(text, field_kind, context) -> [Problem]``.
    Returns it (usable as a decorator)."""
    if hook not in _HOOKS:
        _HOOKS.append(hook)
    return hook


def unregister(hook: Callable) -> None:
    if hook in _HOOKS:
        _HOOKS.remove(hook)


def blocking(text: str, limit: Optional[int] = None, label: str = "The value") -> Optional[str]:
    """Why ``text`` cannot be stored at all (too long, a character the page
    code page cannot hold), or None."""
    if limit and len(text) > limit:
        return "%s is too long: %d characters. The maximum is %d; shorten it and apply again." % (
            label,
            len(text),
            limit,
        )
    from ...editing import UnencodableTextError, guard_text

    try:
        guard_text(text)
    except UnencodableTextError as exc:
        return "%s. Replace those characters and apply again." % exc
    return None


def structure(text: str, tokens=None) -> List[Problem]:
    """What the tokens say is wrong (a string or comment left open, a character Cicode
    does not use ...) and the bracket balance (the range suffix '^(min,max)' included)."""
    tokens = L.tokenize(text or "") if tokens is None else tokens
    out = [Problem(WARNING, t.problem) for t in tokens if t.problem]
    depth = 0
    for t in tokens:
        if t.kind != L.PUNCT:
            continue
        ch = text[t.start]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                out.append(Problem(WARNING, "There is a ')' without a matching '('."))
                depth = 0
    if depth > 0:
        out.append(Problem(WARNING, "%d '(' %s not closed." % (depth, "is" if depth == 1 else "are")))
    return out


def advice(text: str, field_kind: str = "expr", context=None) -> List[Problem]:
    """Every non-blocking hint for ``text``."""
    text = text or ""
    if not text.strip():
        return []
    tokens = L.tokenize(text)
    out = structure(text, tokens)
    if context is not None:
        # (the same lookups as the field's underlines: cicode_assist)
        from .cicode_assist import name_problems

        found = name_problems(text, tokens, context, field_kind, names=field_kind in ("expr", "command"))
        out.extend(Problem(WARNING, p.message) for p in found)
    for hook in list(_HOOKS):
        try:
            out.extend(hook(text, field_kind, context) or [])
        except Exception:  # noqa: BLE001 -- advice must never break a sheet
            pass
    return out


def summary(problems: Iterable[Problem]) -> str:
    return "\n".join(p.message for p in problems)
