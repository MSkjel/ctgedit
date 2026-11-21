"""Start and stop the editor's helper processes."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import List, Optional

#: CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
CREATION_FLAGS = (0x08000000 | 0x00004000) if os.name == "nt" else 0


def command(option: str, module: str, function: str, args: List[str]) -> List[str]:
    """``ctgedit.exe <option> <args>`` in the frozen build, else ``python -c`` calling
    ``module.function(argv)`` with the args (its return value is the exit code)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, option] + list(args)
    return [
        sys.executable,
        "-c",
        "import sys; from %s import %s as _f; sys.exit(_f(sys.argv[1:]))" % (module, function),
    ] + list(args)


def environment() -> dict:
    """This environment with the source tree on PYTHONPATH (a source run's helper finds
    the package)."""
    env = dict(os.environ)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env["PYTHONPATH"] = os.pathsep.join(p for p in (root, env.get("PYTHONPATH", "")) if p)
    return env


def start(
    cmd: List[str],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    kill_on_close: bool = True,
    low_priority: bool = True,
) -> subprocess.Popen:
    """Start a helper process (no window, below normal unless ``low_priority`` is False,
    ended with the editor)."""
    flags = CREATION_FLAGS if low_priority else (0x08000000 if os.name == "nt" else 0)
    proc = subprocess.Popen(
        cmd, env=environment(), stdin=stdin, stdout=stdout, stderr=stderr, creationflags=flags, close_fds=True
    )
    if kill_on_close:
        KillOnClose.assign(proc)
    return proc


class KillOnClose:
    """A Windows job object that kills its processes when this process ends."""

    _handle = None
    _tried = False
    _lock = threading.Lock()

    @classmethod
    def assign(cls, proc: subprocess.Popen) -> bool:
        if os.name != "nt":
            return False
        import ctypes
        from ctypes import wintypes

        with cls._lock:
            if not cls._tried:
                cls._tried = True
                cls._handle = cls._create(ctypes, wintypes)
            h = cls._handle
        if not h:
            return False
        try:
            return bool(
                ctypes.windll.kernel32.AssignProcessToJobObject(
                    wintypes.HANDLE(h), wintypes.HANDLE(int(proc._handle))
                )
            )
        except Exception:  # noqa: BLE001 -- a safety net only
            return False

    @staticmethod
    def _create(ctypes, wintypes) -> Optional[int]:
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_ulonglong)
                for n in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        try:
            k32 = ctypes.windll.kernel32
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            h = k32.CreateJobObjectW(None, None)
            if not h:
                return None
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(
                wintypes.HANDLE(h), 9, ctypes.byref(info), ctypes.sizeof(info)
            ):
                k32.CloseHandle(wintypes.HANDLE(h))
                return None
            return h
        except Exception:  # noqa: BLE001
            return None


# -- one helper at a time --------------------------------------------------------------
# The helpers are separate processes of 100 to 650 MB each. The checks wait while another
# helper works: they are idle-time work, started again by their timers.
_RUNNING: dict = {}  # kind -> [Popen]: the helpers started for each kind
_BUSY: set = set()  # kinds a long-lived helper is working for now
_RUNNING_LOCK = threading.Lock()


def track(kind: str, proc: subprocess.Popen) -> subprocess.Popen:
    """Count ``proc`` as a helper of ``kind`` while it runs."""
    with _RUNNING_LOCK:
        _RUNNING.setdefault(kind, []).append(proc)
    return proc


def set_busy(kind: str, on: bool) -> None:
    """A long-lived helper (the genie worker) is working for ``kind`` now, or idle."""
    with _RUNNING_LOCK:
        if on:
            _BUSY.add(kind)
        else:
            _BUSY.discard(kind)


def others_busy(kind: str) -> bool:
    """A helper of another kind than ``kind`` runs or works now."""
    with _RUNNING_LOCK:
        for k in list(_RUNNING):
            _RUNNING[k] = [p for p in _RUNNING[k] if p.poll() is None]
            if not _RUNNING[k]:
                del _RUNNING[k]
        return any(k != kind for k in _RUNNING) or any(k != kind for k in _BUSY)


def running_kinds() -> dict:
    """{kind: helper processes of that kind running now} (the status bar's tasks list says
    which helpers work: ui/jobs/tasks.py)."""
    with _RUNNING_LOCK:
        out = {}
        for k in list(_RUNNING):
            alive = [p for p in _RUNNING[k] if p.poll() is None]
            _RUNNING[k] = alive
            if alive:
                out[k] = len(alive)
            else:
                del _RUNNING[k]
        for k in _BUSY:
            out.setdefault(k, 1)
        return out


def free_memory_mb() -> Optional[int]:
    """The machine's available physical memory in MB (None when it cannot be read)."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return int(st.ullAvailPhys // (1024 * 1024))
    except Exception:  # noqa: BLE001 -- a hint only
        return None
