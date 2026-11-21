"""Helpers the object sheet's readers share (format/dspval, format/fillcolour)."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence


def getter(props: Dict[str, str]) -> Callable[[str], Optional[str]]:
    """Case-insensitive lookup (CtDraw's property index ignores ASCII case); None =
    the key is absent."""
    low: Dict[str, str] = {}
    for k, v in props.items():
        low.setdefault(k.lower(), v)

    def get(key: str) -> Optional[str]:
        v = props.get(key)
        return low.get(key.lower()) if v is None else v

    return get


def written_conditions(conditions: Sequence[str]) -> List[str]:
    """The conditions Apply stores: blank ones at the END are dropped (they
    would be gone on the next load, which counts to the last non-blank);
    all blank keeps them (a reload shows CtDraw's default 3)."""
    conds = list(conditions) or [""]
    n = len(conds)
    while n > 1 and not conds[n - 1].strip():
        n -= 1
    if n == 1 and not conds[0].strip():
        return conds
    return conds[:n]


def note_too_long(out: list, name: str, label: str, text: Optional[str], limit: int) -> None:
    """Add a 'too long' problem to ``out`` when ``text`` passes ``limit`` characters."""
    if len(text or "") > limit:
        out.append(
            (
                name,
                "%s is too long: %d characters. The maximum is %d; shorten it "
                "and apply again." % (label, len(text), limit),
            )
        )
