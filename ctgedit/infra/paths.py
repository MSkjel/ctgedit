r"""ctgedit's data folder, ``%LOCALAPPDATA%\ctgedit`` (``CTG_DATA_DIR`` overrides it)."""

from __future__ import annotations

import os


def data_dir(*sub: str) -> str:
    """``<data folder>\\<sub...>`` (not created)."""
    base = os.environ.get("CTG_DATA_DIR")
    if not base:
        local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".ctgedit")
        base = os.path.join(local, "ctgedit")
    return os.path.join(base, *sub)


def scada_bin_dirs() -> list:
    """The Plant SCADA / Citect ``Bin`` folders installed here (CTG_SCADA_BIN overrides:
    that one only; CTG_SCADA_ROOT: the fake install's), oldest first by name
    (infra/install.bin_dirs)."""
    from . import install

    return install.bin_dirs()


def aveva_docs() -> str | None:
    """The installed AVEVA Plant SCADA help (its HTML topics), or None with no install: the
    built-in help links into it and the Cicode / genie catalogues read its pages
    (infra/install.docs_dir: CTG_AVEVA_HELP wins)."""
    from . import install

    return install.docs_dir()


def kill_point(point: str) -> None:
    """The tests' kill points (CTG_TEST_EXIT_AT; inert when unset): this process ends at
    once with exit code 3 when ``point`` is the one named ('after_ctg': a page save after
    its .ctg; 'in_register:<n>': the register after its n-th table; 'after_records:<n>:
    <part>': Update Pages part <part> when it has taken its next page after n records)."""
    if os.environ.get("CTG_TEST_EXIT_AT") == point:
        hard_exit(3)


def hard_exit(code: int) -> None:
    """End this process at once with ``code``, the way a kill does: no Python or DLL
    clean-up."""
    import sys

    sys.stdout.flush()
    sys.stderr.flush()
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        k32.TerminateProcess(k32.GetCurrentProcess(), int(code))
    os._exit(code)


_OWN_START: list = []


def process_started(pid: int = 0) -> int:
    """When process ``pid`` (default: this one) was created, as a Windows
    FILETIME; 0 when it cannot be told (not Windows, no such process). With
    the pid it identifies a process for good: Windows reuses pids soon."""
    if not pid or pid == os.getpid():
        if not _OWN_START:
            _OWN_START.append(_started(os.getpid()))
        return _OWN_START[0]
    return _started(pid)


def is_this_process(pid: int, started: int = 0) -> bool:
    """Was a file stamped (``pid``, ``started``) written by THIS process (not
    by an earlier one that had the same pid)?"""
    if pid != os.getpid():
        return False
    own = process_started()
    return not started or not own or int(started) == own


def _started(pid: int) -> int:
    if os.name != "nt":
        return 0
    import ctypes

    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return 0
    try:
        t = [ctypes.c_ulonglong() for _k in range(4)]
        if not k32.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
            return 0
        return int(t[0].value)
    finally:
        k32.CloseHandle(h)


def pid_alive(pid: int, started: int = 0) -> bool:
    """Is process ``pid`` still running? (This process always is.)
    ``started`` = its :func:`process_started` when it wrote the file being
    judged: a live process with that pid but another start time is a
    different program that got the pid of the one that ended."""
    if pid == os.getpid():
        return is_this_process(pid, started)
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            alive = bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259
        finally:
            k32.CloseHandle(h)
        if alive and started:
            now = _started(pid)
            alive = not now or now == int(started)
        return alive
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
