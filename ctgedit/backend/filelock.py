"""Find which programs hold a file open, so a failed save can say who locked it."""

from __future__ import annotations

import os
from typing import List, Tuple

_ERROR_MORE_DATA = 234
_CCH_RM_SESSION_KEY = 32
_CCH_RM_MAX_APP_NAME = 255
_CCH_RM_MAX_SVC_NAME = 63


def lockers(path: str, *, include_self: bool = False) -> List[Tuple[str, int]]:
    """[(program name, pid), ...] of the processes that have ``path`` open
    (this process left out unless ``include_self``)."""
    if os.name != "nt":
        return []
    try:
        return _query(os.path.abspath(path), include_self)
    except Exception:  # noqa: BLE001 -- a hint only
        return []


def _query(path: str, include_self: bool) -> List[Tuple[str, int]]:
    import ctypes
    from ctypes import wintypes

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [
            ("Process", RM_UNIQUE_PROCESS),
            ("strAppName", wintypes.WCHAR * (_CCH_RM_MAX_APP_NAME + 1)),
            ("strServiceShortName", wintypes.WCHAR * (_CCH_RM_MAX_SVC_NAME + 1)),
            ("ApplicationType", ctypes.c_int),
            ("AppStatus", wintypes.ULONG),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]

    rm = ctypes.WinDLL("rstrtmgr")
    session = wintypes.DWORD(0)
    key = ctypes.create_unicode_buffer(_CCH_RM_SESSION_KEY + 1)
    if rm.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []
    try:
        files = (wintypes.LPCWSTR * 1)(path)
        if rm.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return []
        needed, count = wintypes.UINT(0), wintypes.UINT(0)
        reasons = wintypes.DWORD(0)
        infos = None
        for _attempt in range(3):
            infos = (RM_PROCESS_INFO * max(needed.value, 1))()
            count = wintypes.UINT(len(infos))
            rc = rm.RmGetList(
                session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons)
            )
            if rc == 0:
                break
            if rc != _ERROR_MORE_DATA:
                return []
        else:
            return []
        out = []
        me = os.getpid()
        for info in infos[: count.value]:
            pid = int(info.Process.dwProcessId)
            if pid == me and not include_self:
                continue
            out.append((info.strAppName or "process %d" % pid, pid))
        return out
    finally:
        rm.RmEndSession(session)


def describe(path: str) -> str:
    """'the Graphics Builder (PID 1234)' / 'A (PID 1), B (PID 2)' / ''."""
    return ", ".join("%s (PID %d)" % (name, pid) for name, pid in lockers(path))
