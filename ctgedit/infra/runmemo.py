"""Memos that last for one batch run."""

from __future__ import annotations

import contextlib
from typing import Dict, Iterator, Optional

_ACTIVE: Optional[Dict[str, dict]] = None


def table(name: str) -> Optional[dict]:
    """The running batch's memo ``name`` (a dict), None when no run keeps memos."""
    run = _ACTIVE
    if run is None:
        return None
    t = run.get(name)
    if t is None:
        t = run[name] = {}
    return t


@contextlib.contextmanager
def kept() -> Iterator[None]:
    """Keep the run memos while the block runs (inside a run: that run's memos)."""
    global _ACTIVE
    outer = _ACTIVE
    if outer is None:
        _ACTIVE = {}
    try:
        yield
    finally:
        _ACTIVE = outer
