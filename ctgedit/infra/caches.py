"""Registry of the process-wide caches and when each is emptied."""

from __future__ import annotations

import threading
from typing import Callable, Dict, Iterable, List, Optional, Tuple

EVENTS = ("libraries", "refresh", "tests")

#: name -> (events, clear function, the object cached: a dict / list / lru_cache function)
_REGISTRY: Dict[str, Tuple[Tuple[str, ...], Callable[[], None], object]] = {}
_LOCK = threading.Lock()


def register(
    name: str, target, clear: Optional[Callable[[], None]] = None, on: Iterable[str] = ("refresh", "tests")
) -> None:
    """Register cache ``name``: ``target`` = the container (its ``clear()`` empties it) or an
    lru_cache function (its ``cache_clear()``); ``clear`` = another way to empty it (one
    that takes its lock, for instance); ``on`` = the events that empty it (() = a pure
    function's cache: never). Registering a name again replaces it (a module reloaded)."""
    events = tuple(on)
    bad = [e for e in events if e not in EVENTS]
    if bad:
        raise ValueError("unknown cache event(s): %s" % ", ".join(bad))
    if clear is None:
        fn = getattr(target, "cache_clear", None) or getattr(target, "clear", None)
        if fn is None:
            raise TypeError("cache %s has no clear() / cache_clear(): pass clear=" % name)
        clear = fn
    with _LOCK:
        _REGISTRY[name] = (events, clear, target)


def clear(*events: str) -> List[str]:
    """Empty the caches registered for any of ``events``; the names emptied. A clear that
    fails is skipped (the others still go)."""
    with _LOCK:
        items = list(_REGISTRY.items())
    done = []
    for name, (on, fn, _target) in items:
        if any(e in on for e in events):
            try:
                fn()
                done.append(name)
            except Exception:  # noqa: BLE001 -- one cache never stops the others
                from .logsetup import note_exception

                note_exception("caches.clear", name=name)
    return done


def registered() -> Dict[str, Tuple[Tuple[str, ...], object]]:
    """name -> (events, target), for the registry test."""
    with _LOCK:
        return {n: (on, t) for n, (on, _fn, t) in _REGISTRY.items()}
