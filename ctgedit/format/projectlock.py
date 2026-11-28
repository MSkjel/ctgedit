"""Locks between ctgedit processes over the files a page save writes."""

from __future__ import annotations

import contextlib
import hashlib
import os
import threading
import time
from typing import Callable, Dict, Iterable, Optional, Tuple

HELPER_WAITS = (120.0, 120.0)
#: the wait when nothing set one (tests and tools)
DEFAULT_WAIT = 30.0
#: how long a cancellable wait waits before it asks the cancel callback again
SLICE = 0.25

_WAITS: Dict[str, float] = {"project": DEFAULT_WAIT, "page": DEFAULT_WAIT, "file": DEFAULT_WAIT}
_CANCEL: list = [None]

WAIT_OBJECT_0 = 0x0
WAIT_ABANDONED = 0x80
WAIT_TIMEOUT = 0x102


class ProjectBusyError(OSError):
    """The project database stayed locked by another ctgedit process for the whole wait."""

    def __init__(self, project_dir: str):
        name = os.path.basename(os.path.normpath(project_dir)) or project_dir
        super().__init__("The project database of %s is busy: another ctgedit process is writing it." % name)
        self.project_dir = project_dir


class PageBusyError(OSError):
    """The page stayed locked by another ctgedit process (its save) for the whole wait."""

    def __init__(self, page_path: str):
        name = os.path.splitext(os.path.basename(page_path))[0] or page_path
        super().__init__("%s is being saved by another ctgedit process." % name)
        self.page_path = page_path


class FileBusyError(OSError):
    """A shared file (a backup manifest) stayed locked for the whole wait."""

    def __init__(self, path: str):
        super().__init__("%s is in use by another ctgedit process." % os.path.basename(path))
        self.path = path


class LockCancelled(OSError):
    """A cancellable wait was cancelled (Cancel of Update Pages): nothing was written."""

    def __init__(self, path: str = ""):
        super().__init__("Stopped while waiting for %s." % (os.path.basename(path) or "a lock"))
        self.path = path


_ERRORS = {"project": ProjectBusyError, "page": PageBusyError, "file": FileBusyError}


# -- the settings of this process ------------------------------------------------------------
def set_waits(project: Optional[float] = None, page: Optional[float] = None) -> None:
    """The waits of this process (seconds; None keeps one). The manifest lock waits as long
    as the project lock."""
    if project is not None:
        _WAITS["project"] = float(project)
        _WAITS["file"] = float(project)
    if page is not None:
        _WAITS["page"] = float(page)


def waits() -> Tuple[float, float]:
    return _WAITS["project"], _WAITS["page"]


def set_cancel(fn: Optional[Callable[[], bool]]) -> None:
    """``fn()`` True = stop waiting (LockCancelled); asked between wait slices."""
    _CANCEL[0] = fn


def cancel_requested() -> bool:
    """The cancel callback says stop (False inside :func:`uncancellable`)."""
    fn = _CANCEL[0]
    if fn is None or _local_int("uncancellable"):
        return False
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001  # swallow-ok: a broken callback never cancels a wait
        return False


@contextlib.contextmanager
def uncancellable():
    """No wait inside the block is cancelled (a save that has started writing finishes)."""
    _set_local_int("uncancellable", _local_int("uncancellable") + 1)
    try:
        yield
    finally:
        _set_local_int("uncancellable", _local_int("uncancellable") - 1)


# -- thread state ------------------------------------------------------------------------------
_TLS = threading.local()


def _local_int(name: str) -> int:
    return int(getattr(_TLS, name, 0) or 0)


def _set_local_int(name: str, v: int) -> None:
    setattr(_TLS, name, v)


def _depths() -> Dict[Tuple[str, str], int]:
    d = getattr(_TLS, "depths", None)
    if d is None:
        d = _TLS.depths = {}
    return d


def key_of(path: str) -> str:
    """The lock key of a path: its real absolute path in normal case."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def lock_name(kind: str, path: str) -> str:
    return "Local\\ctgedit-%s-%s" % (kind, hashlib.sha1(key_of(path).encode("utf-8")).hexdigest()[:32])


def held(kind: str, path: str) -> bool:
    """This thread holds the ``kind`` lock of ``path``."""
    return _depths().get((kind, key_of(path)), 0) > 0


def any_held(kinds: Iterable[str]) -> bool:
    """This thread holds a lock of one of ``kinds``."""
    want = set(kinds)
    return any(n > 0 and k[0] in want for k, n in _depths().items())


# -- Windows: named mutexes ----------------------------------------------------------------
#: name -> the mutex handle (opened once, kept for the life of the process: state)
_HANDLES: Dict[str, int] = {}
_HANDLES_LOCK = threading.Lock()
_K32: list = [None]


def _kernel32():
    k = _K32[0]
    if k is None:
        import ctypes
        from ctypes import wintypes

        # (a DLL object of its own: the shared ctypes.windll keeps its own prototypes)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexExW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        k.CreateMutexExW.restype = ctypes.c_void_p
        k.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k.OpenMutexW.restype = ctypes.c_void_p
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.LocalFree.argtypes = [ctypes.c_void_p]
        k.LocalFree.restype = ctypes.c_void_p
        k.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.ReleaseMutex.argtypes = [ctypes.c_void_p]
        k.ReleaseMutex.restype = wintypes.BOOL
        _K32[0] = k
    return k


#: the rights a lock's handle is opened with: wait for it, release it (an elevated
#: ctgedit's mutex opened by a normal one asks for no more than its DACL grants)
_MUTEX_ACCESS = 0x00100000 | 0x0001  # SYNCHRONIZE | MUTEX_MODIFY_STATE
ERROR_ACCESS_DENIED = 5
#: the SECURITY_ATTRIBUTES every lock is made with (built once, kept: the mutexes made with
#: it point at its security descriptor while they are made)
_SA: list = []


def _user_sid() -> str:
    """This process's user as a string SID ('S-1-5-21-...'): the same in an elevated and a
    normal process of one user."""
    import ctypes
    from ctypes import wintypes

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.OpenProcessToken.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    adv.GetTokenInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    k = _kernel32()
    tok = ctypes.c_void_p()
    if not adv.OpenProcessToken(k.GetCurrentProcess(), 0x0008, ctypes.byref(tok)):  # QUERY
        raise OSError(ctypes.get_last_error(), "the process token could not be opened")
    try:
        size = wintypes.DWORD(0)
        adv.GetTokenInformation(tok, 1, None, 0, ctypes.byref(size))  # TokenUser
        buf = ctypes.create_string_buffer(max(int(size.value), 64))
        if not adv.GetTokenInformation(tok, 1, buf, len(buf), ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "the process token could not be read")
        psid = ctypes.c_void_p.from_buffer(buf).value  # TOKEN_USER.User.Sid
        out = ctypes.c_void_p()
        if not adv.ConvertSidToStringSidW(psid, ctypes.byref(out)):
            raise OSError(ctypes.get_last_error(), "the user's SID could not be read")
        try:
            return ctypes.wstring_at(out.value)
        finally:
            k.LocalFree(out)
    finally:
        ctypes.WinDLL("kernel32").CloseHandle(tok)


def _security():
    """A pointer to the SECURITY_ATTRIBUTES of the locks: a DACL for SYSTEM and this user
    only, so the mutex an elevated ctgedit made opens in a normal one of the same user (the
    default DACL of an elevated process names only the administrators). None (the default
    security) when it cannot be built."""
    if _SA:
        return _SA[0]
    import ctypes
    from ctypes import wintypes

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    sa = None
    try:
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        conv = adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
        conv.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        psd = ctypes.c_void_p()
        sddl = "D:(A;;0x1F0001;;;SY)(A;;0x1F0001;;;%s)" % _user_sid()  # MUTEX_ALL_ACCESS
        if conv(sddl, 1, ctypes.byref(psd), None):
            s = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), psd.value, False)
            sa = (s, ctypes.pointer(s))
    except OSError, AttributeError:  # swallow-ok: the default security then
        sa = None
    _SA.append(None if sa is None else sa[1])
    _SA.append(sa)  # (keeps the structure alive)
    return _SA[0]


def _mutex(name: str) -> int:
    with _HANDLES_LOCK:
        h = _HANDLES.get(name)
        if h:
            return h
        import ctypes

        k = _kernel32()
        h = k.CreateMutexExW(_security(), name, 0, _MUTEX_ACCESS)
        if not h:
            err = ctypes.get_last_error()
            if err == ERROR_ACCESS_DENIED:
                # (made by a process whose DACL this user's request does not pass: open it
                # with the two rights a wait and a release need)
                h = k.OpenMutexW(_MUTEX_ACCESS, False, name)
                err = ctypes.get_last_error() if not h else 0
            if not h:
                raise OSError(err, "the lock %s could not be made" % name)
        _HANDLES[name] = h
        return h


def _acquire_win(kind: str, path: str, timeout: float) -> None:
    import ctypes

    h = _mutex(lock_name(kind, path))
    k = _kernel32()
    cancellable = _CANCEL[0] is not None and not _local_int("uncancellable")
    if not cancellable:
        r = k.WaitForSingleObject(h, int(max(0.0, timeout) * 1000))
    else:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            left = deadline - time.monotonic()
            r = k.WaitForSingleObject(h, int(max(0.0, min(SLICE, left)) * 1000))
            if r != WAIT_TIMEOUT or left <= SLICE:
                break
            if cancel_requested():
                raise LockCancelled(path)
    if r in (WAIT_OBJECT_0, WAIT_ABANDONED):
        return
    if r == WAIT_TIMEOUT:
        raise _ERRORS[kind](path)
    raise OSError(ctypes.get_last_error(), "waiting for the lock of %s failed" % path)


def _release_win(kind: str, path: str) -> None:
    h = _HANDLES.get(lock_name(kind, path))
    if h:
        _kernel32().ReleaseMutex(h)


# -- elsewhere: flock + a lock per process ---------------------------------------------------
_RLOCKS: Dict[str, threading.RLock] = {}
_FDS: Dict[str, int] = {}


def _acquire_posix(kind: str, path: str, timeout: float) -> None:
    import fcntl
    import tempfile

    name = lock_name(kind, path).split("\\", 1)[1]
    with _HANDLES_LOCK:
        rl = _RLOCKS.setdefault(name, threading.RLock())
    deadline = time.monotonic() + max(0.0, timeout)
    while not rl.acquire(timeout=SLICE):
        if time.monotonic() >= deadline:
            raise _ERRORS[kind](path)
        if cancel_requested():
            raise LockCancelled(path)
    try:
        folder = os.path.join(tempfile.gettempdir(), "ctgedit-locks")
        os.makedirs(folder, exist_ok=True)
        fd = os.open(os.path.join(folder, name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise _ERRORS[kind](path) from None
                if cancel_requested():
                    os.close(fd)
                    raise LockCancelled(path) from None
                time.sleep(0.05)
        _FDS[name] = fd
    except BaseException:
        rl.release()
        raise


def _release_posix(kind: str, path: str) -> None:
    import fcntl

    name = lock_name(kind, path).split("\\", 1)[1]
    fd = _FDS.pop(name, None)
    if fd is not None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    rl = _RLOCKS.get(name)
    if rl is not None:
        rl.release()


# -- the locks -------------------------------------------------------------------------------
def _acquire(kind: str, path: str, timeout: Optional[float]) -> None:
    key = (kind, key_of(path))
    depths = _depths()
    n = depths.get(key, 0)
    if n:
        depths[key] = n + 1  # this thread holds it: one more level
        return
    wait = _WAITS.get(kind, DEFAULT_WAIT) if timeout is None else float(timeout)
    if os.name == "nt":
        _acquire_win(kind, path, wait)
    else:
        _acquire_posix(kind, path, wait)
    depths[key] = 1
    if kind == "project":
        # the journals a dead writer left for this project are rolled back first
        try:
            from . import journal

            journal.recover_project(path)
        except BaseException:
            _release(kind, path)
            raise


def _release(kind: str, path: str) -> None:
    key = (kind, key_of(path))
    depths = _depths()
    n = depths.get(key, 0)
    if n > 1:
        depths[key] = n - 1
        return
    depths.pop(key, None)
    if n <= 0:
        return
    if os.name == "nt":
        _release_win(kind, path)
    else:
        _release_posix(kind, path)


@contextlib.contextmanager
def _lock(kind: str, path: str, timeout: Optional[float]):
    _acquire(kind, path, timeout)
    try:
        yield path
    finally:
        _release(kind, path)


def project(project_dir: str, timeout: Optional[float] = None):
    """Hold the project database lock of ``project_dir`` (re-entrant in a thread).
    ProjectBusyError after the wait; journal.JournalRecoveryError when a dead writer's
    journal of the project cannot be rolled back."""
    return _lock("project", project_dir, timeout)


def page(page_path: str, timeout: Optional[float] = None):
    """Hold the lock of one page file (its save). PageBusyError after the wait."""
    return _lock("page", page_path, timeout)


def file(path: str, timeout: Optional[float] = None):
    """Hold the lock of one shared file (a backup manifest). FileBusyError after the
    wait."""
    return _lock("file", path, timeout)
