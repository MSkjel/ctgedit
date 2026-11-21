"""New GUIDs and save timestamps for saved files."""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import threading
import time as _time
import uuid
from typing import Optional

ENV = "CTG_FIXED_IDENTITY"

#: the time a fixed identity stands at (local time, as CtDraw's stamps are)
FIXED_TIME = _dt.datetime(2026, 1, 1, 0, 0, 0)
#: CtDraw's epoch: its save stamps are whole seconds since this local time
CTDRAW_EPOCH = _dt.datetime(1899, 12, 30)

_LOCK = threading.Lock()
#: [the seed the counter counts for, the next number]
_COUNTER: list = [None, 0]


def seed() -> Optional[str]:
    """The fixed identity's seed, or None (random GUIDs, the real clock)."""
    v = os.environ.get(ENV)
    return v if v else None


def fixed() -> bool:
    return seed() is not None


def reset() -> None:
    """Start the GUID counter again (the next GUID is the seed's first)."""
    with _LOCK:
        _COUNTER[0], _COUNTER[1] = None, 0


def _next(s: str) -> int:
    with _LOCK:
        if _COUNTER[0] != s:
            _COUNTER[0], _COUNTER[1] = s, 0
        n = _COUNTER[1]
        _COUNTER[1] += 1
        return n


def new_guid() -> uuid.UUID:
    """A new GUID: random (uuid4), or with a fixed identity the seed's next one (version 4
    form, from SHA-1 of seed and counter)."""
    s = seed()
    if s is None:
        return uuid.uuid4()
    raw = hashlib.sha1(("%s:%d" % (s, _next(s))).encode("utf-8")).digest()[:16]
    return uuid.UUID(bytes=raw, version=4)


def guid_bytes() -> bytes:
    """:func:`new_guid` as its 16 bytes (``uuid.bytes``, the order ctgedit stores)."""
    return new_guid().bytes


def now() -> _dt.datetime:
    """The local time now, whole seconds (with a fixed identity: :data:`FIXED_TIME`)."""
    if fixed():
        return FIXED_TIME
    return _dt.datetime.now().replace(microsecond=0)


def today() -> _dt.date:
    """Today's date (a database header's last-update date)."""
    return now().date()


def now_time() -> float:
    """Seconds since 1970 (``time.time()``; with a fixed identity, :data:`FIXED_TIME`'s)."""
    if fixed():
        return _time.mktime(FIXED_TIME.timetuple())
    return _time.time()


def now_stamp() -> int:
    """A save stamp in CtDraw's epoch: whole seconds since 1899-12-30 in LOCAL time, as a
    u32 (the container ts_modified / element identity stamp CtDraw writes)."""
    return int((now() - CTDRAW_EPOCH).total_seconds()) & 0xFFFFFFFF
