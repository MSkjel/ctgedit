"""Search ranking for library elements."""

from __future__ import annotations

import re
from typing import Optional, Sequence, Tuple

_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")

#: a token of 3 letters or more also matches a name that has its letters in order ('mtr'
#: finds 'motor', ranked last)
FUZZY = True


def words(name: str) -> Tuple[str, ...]:
    """``'agitator_motor_Grey2'`` -> ``('agitator', 'motor', 'grey', '2')``."""
    return tuple(w.lower() for w in _WORD_RE.findall(name))


def subsequence(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(c in it for c in needle)


def prep(e) -> tuple:
    """Precomputed search fields of an Entry: (name, library, project, words)."""
    return (e.name.lower(), e.library.lower(), e.project.lower(), words(e.name))


def token_score(t: str, name: str, lib: str, proj: str, wds: Tuple[str, ...]) -> Optional[int]:
    if "." in t:  # 'lib.elem' (a pasted reference)
        a, _dot, b = t.partition(".")
        if a and a not in lib:
            return None
        if not b:
            return 4
        s = token_score(b, name, "", "", wds)
        return None if s is None or s > 3 else s
    if t == name:
        return 0
    if name.startswith(t):
        return 1
    if t in name:
        return 2 if any(w.startswith(t) for w in wds) else 3
    if t == lib:
        return 4
    if t in lib:
        return 5
    if t in proj:
        return 6
    if FUZZY and len(t) >= 3 and subsequence(t, name):
        return 7
    return None


def score_tokens(prepped: tuple, tokens: Sequence[str]) -> Optional[Tuple[int, int]]:
    """``(worst, total)`` token rank of one entry (lower = better), or None when
    a token does not match at all."""
    name, lib, proj, wds = prepped
    worst = total = 0
    for t in tokens:
        s = token_score(t, name, lib, proj, wds)
        if s is None:
            return None
        worst = max(worst, s)
        total += s
    return worst, total
