"""Checks for the sheet's expression and command fields."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

from ... import cicode_names as _cn

ERROR, WARNING = "error", "warning"


@dataclass(frozen=True)
class Problem:
    level: str  # ERROR (refuses the value) or WARNING (advice)
    message: str


# names by the compiler's rule (ctgedit.cicode_names: the code page's letters too)
_CALL = re.compile(r"(?<![%s.])(%s%s*)\s*\(" % (_cn.PART_CHARS, _cn.START, _cn.PART))
_IDENT = re.compile(r"(?<![%s.])%s[%s.]*" % (_cn.PART_CHARS, _cn.START, _cn.PART_CHARS))
#: Cicode operators / keywords that look like identifiers
KEYWORDS = frozenset(
    k.lower()
    for k in (
        "AND",
        "OR",
        "NOT",
        "XOR",
        "MOD",
        "BITAND",
        "BITOR",
        "BITXOR",
        "TRUE",
        "FALSE",
        "IF",
        "THEN",
        "ELSE",
        "END",
        "WHILE",
        "DO",
        "FOR",
        "TO",
        "SELECT",
        "CASE",
        "IS",
        "RETURN",
        "INT",
        "REAL",
        "STRING",
        "OBJECT",
        "LONG",
        "QUALITY",
        "TIMESTAMP",
        "GLOBAL",
        "MODULE",
        "PUBLIC",
        "PRIVATE",
        "FUNCTION",
        "BAD_HANDLE",
    )
)

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


def strip_strings(text: str) -> Tuple[str, bool]:
    """``text`` with every string literal blanked (same length), and whether
    a string is left open. Cicode escapes a quote inside a string as '^"'."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c != '"':
            out.append(c)
            i += 1
            continue
        j = i + 1
        while j < n:
            if text[j] == "^" and j + 1 < n:
                j += 2
                continue
            if text[j] == '"':
                break
            j += 1
        if j >= n:
            out.append(" " * (n - i))
            return "".join(out), True
        out.append(" " * (j - i + 1))
        i = j + 1
    return "".join(out), False


def structure(text: str) -> List[Problem]:
    """Bracket and quote balance (the range suffix '^(min,max)' included)."""
    body, open_string = strip_strings(text or "")
    out = []
    if open_string:
        out.append(Problem(WARNING, 'A string is not closed: a " is missing.'))
    depth = 0
    for ch in body:
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


def calls(text: str) -> List[str]:
    """The function names ``text`` calls (outside strings)."""
    body, _ = strip_strings(text or "")
    return [m.group(1) for m in _CALL.finditer(body) if m.group(1).lower() not in KEYWORDS]


def names(text: str) -> List[str]:
    """The bare identifiers ``text`` uses: outside strings, not calls, not
    keywords, not part of a number (1e5) or a genie parameter (%name%)."""
    body, _ = strip_strings(text or "")
    out = []
    for m in _IDENT.finditer(body):
        tok = m.group(0)
        if tok.lower() in KEYWORDS:
            continue
        if body[m.end() :].lstrip().startswith("("):
            continue
        if m.start() and (body[m.start() - 1].isdigit() or body[m.start() - 1] in "%?"):
            continue
        out.append(tok)
    return out


def advice(text: str, field_kind: str = "expr", context=None) -> List[Problem]:
    """Every non-blocking hint for ``text``."""
    text = text or ""
    if not text.strip():
        return []
    out = structure(text)
    template = "%" in text or "?" in text
    is_function = getattr(context, "is_function", None)
    if is_function is not None and not template:
        seen = set()
        for fn in calls(text):
            if fn.lower() in seen:
                continue
            seen.add(fn.lower())
            if is_function(fn) is False:
                out.append(Problem(WARNING, "'%s' is not a known Cicode function." % fn))
    is_name = getattr(context, "is_name", None)
    if is_name is not None and not template and field_kind == "expr":
        seen = set()
        for nm in names(text):
            if nm.lower() in seen:
                continue
            seen.add(nm.lower())
            if is_name(nm) is False:
                out.append(Problem(WARNING, "'%s' is not a known tag." % nm))
    for hook in list(_HOOKS):
        try:
            out.extend(hook(text, field_kind, context) or [])
        except Exception:  # noqa: BLE001 -- advice must never break a sheet
            pass
    return out


def summary(problems: Iterable[Problem]) -> str:
    return "\n".join(p.message for p in problems)
