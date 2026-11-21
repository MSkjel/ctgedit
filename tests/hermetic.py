"""The hermetic guard: with ``CTG_HERMETIC=1`` a test fails if it touches the real install,
COM, a Citect program or the network."""

from __future__ import annotations

import os
import shlex
import sys
import threading
from contextlib import contextmanager

ENV = "CTG_HERMETIC"
#: the install a hermetic test sees (CTG_SCADA_ROOT): none, unless it asks for the
#: fx_install fixture (tests/shared_fixtures.py: a copy of tests/fixtures/FxInstall)
FAKE_ROOT = "none"

#: folders no test may touch (lower case, backslashes; a prefix match, so
#: 'c:\\programdata\\aveva' also covers 'AVEVA Plant SCADA 2023 R2')
BLOCKED_DIRS = (
    "c:\\programdata\\aveva",
    "c:\\programdata\\citect",
    "c:\\programdata\\schneider electric",
    "c:\\program files\\aveva",
    "c:\\program files (x86)\\aveva",
    "c:\\program files\\schneider electric",
    "c:\\program files (x86)\\schneider electric",
    "c:\\program files\\citect",
    "c:\\program files (x86)\\citect",
)
#: programs no test may start (lower case, no .exe)
BLOCKED_EXES = frozenset(
    (
        "ctdraw32",
        "citectide",
        "ctcmp32",
        "runtimemanager",
        "citect32",
        "citect",
        "ctexplor",
        "ctedit32",
        "ctcicode",
        "ctsetup",
        "ctapi",
        "ctraceex",
        "computer setup wizard",
    )
)
#: programs that start the program their arguments name
LAUNCHERS = frozenset(("cmd", "powershell", "pwsh", "start", "explorer", "rundll32"))
#: packages no test may import (the COM bridges)
BLOCKED_MODULES = frozenset(("win32com", "pythoncom", "comtypes"))
#: words of a registry key no test may open
BLOCKED_KEYS = ("citect", "aveva", "schneider electric")
#: the environment a hermetic test runs in (None = removed)
FORCED_ENV = {
    "CTG_SCADA_ROOT": FAKE_ROOT,
    "CTG_SCADA_BIN": None,
    "CTG_AVEVA_HELP": None,
    "CICODESIM_SCADA_BIN": None,
    "CICODESIM_FUNC0": None,
    "CICODESIM_CICODE_USER": None,
    "CICODESIM_INI": "",
    "CTG_NO_STUDIO": "1",
    "CTG_NO_RUNTIME": "1",
    "CTG_NO_SIM_ENGINE": "1",
    # no library folder of the machine (fx_install names its own through the fake install)
    "CTG_LIB_PATH": None,
    # the conditions the fixtures were written under (tools/make_fixtures.py): text
    # measured at 96 dpi, page text in code page 1252, whatever the machine's
    "CTG_TEXT_DPI": "96",
    "CTG_CODEPAGE": "1252",
}


class HermeticViolation(PermissionError):
    """A hermetic test reached for the real install, COM or a Citect program."""


class _State:
    active = False
    hooked = False
    #: where violations are noted (the running test's list; guarding() swaps its own in)
    sink: list = []
    #: what was noted between tests (a background thread): reported at the session's end
    between: list = []
    lock = threading.Lock()


_S = _State()


# --------------------------------------------------------------------------- the checks
def _norm(path) -> str:
    """``path`` as a lower-case absolute Windows path string ('' when it is not a path)."""
    if isinstance(path, int):
        return ""
    try:
        p = os.fspath(path)
    except TypeError:
        return ""
    if isinstance(p, bytes):
        p = p.decode("utf-8", "replace")
    p = p.replace("/", "\\").lower()
    if p.startswith("\\\\?\\"):
        p = p[4:]
    if len(p) < 3 or p[1] != ":":
        if not p or p.startswith("\\\\"):
            return ""
        try:
            p = os.path.abspath(p).lower()
        except ValueError, OSError:
            return ""
    return p


def blocked_path(path) -> bool:
    """Is ``path`` in the real install's folders?"""
    p = _norm(path)
    return bool(p) and p.startswith(BLOCKED_DIRS)


def _stem(token) -> str:
    t = str(token).strip().strip('"').strip("'")
    base = t.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def _tokens(args) -> list:
    if args is None:
        return []
    if isinstance(args, (str, bytes)):
        s = args.decode("utf-8", "replace") if isinstance(args, bytes) else args
        try:
            toks = shlex.split(s, posix=False)
        except ValueError:
            toks = s.split()
        return [t.strip('"').strip("'") for t in toks]
    try:
        return [os.fsdecode(a) if isinstance(a, (bytes, os.PathLike)) else str(a) for a in args]
    except TypeError:
        return []


def blocked_program(executable, args) -> str:
    """Why starting ``executable`` / ``args`` is refused, or ''."""
    toks = _tokens(args)
    first = [executable] if executable else []
    first += toks[:1]
    for t in first:
        if blocked_path(t):
            return "a program of the install (%s)" % t
        if _stem(t) in BLOCKED_EXES:
            return "a Citect program (%s)" % t
    if toks and _stem(executable or toks[0]) in LAUNCHERS:
        for t in toks[1:]:
            if _stem(t) in BLOCKED_EXES or blocked_path(t):
                return "a Citect program through %s (%s)" % (_stem(executable or toks[0]), t)
    return ""


def _note(what: str) -> None:
    with _S.lock:
        _S.sink.append(what)
    raise HermeticViolation("hermetic test: " + what)


def _path_arg(event, args):
    if args and blocked_path(args[0]):
        _note("%s of %s" % (event, args[0]))


def _two_paths(event, args):
    for a in args[:2]:
        if blocked_path(a):
            _note("%s of %s" % (event, a))


def _popen(event, args):
    why = blocked_program(args[0] if args else None, args[1] if len(args) > 1 else None)
    if why:
        _note("%s of %s" % (event, why))


def _system(event, args):
    cmd = args[0] if args else ""
    for t in _tokens(cmd):
        if _stem(t) in BLOCKED_EXES or blocked_path(t):
            _note("%s of a Citect program (%s)" % (event, t))


def _spawn(event, args):
    why = blocked_program(args[0] if args else None, args[1] if len(args) > 1 else None)
    if why:
        _note("%s of %s" % (event, why))


def _reg(event, args):
    sub = args[1] if len(args) > 1 else None
    if isinstance(sub, str):
        low = sub.lower()
        if any(k in low for k in BLOCKED_KEYS):
            _note("%s of the registry key %s" % (event, sub))


def _host_of(event, args):
    if event == "socket.getaddrinfo":
        return args[0] if args else None
    addr = args[1] if len(args) > 1 else None
    return addr[0] if isinstance(addr, tuple) and addr else None


def _network(event, args):
    host = _host_of(event, args)
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return
    h = host.lower()
    if h in ("", "localhost", "::1") or h.startswith("127."):
        return
    _note("%s to %s (no network)" % (event, host))


#: audit event -> its check (one look-up per event: the guard's whole cost)
_HANDLERS = {
    "open": _path_arg,
    "os.listdir": _path_arg,
    "os.scandir": _path_arg,
    "os.chdir": _path_arg,
    "os.mkdir": _path_arg,
    "os.remove": _path_arg,
    "os.rmdir": _path_arg,
    "os.truncate": _path_arg,
    "os.utime": _path_arg,
    "os.startfile": _path_arg,
    "os.add_dll_directory": _path_arg,
    "glob.glob": _path_arg,
    "glob.glob/2": _path_arg,
    "ctypes.dlopen": _path_arg,
    "os.rename": _two_paths,
    "os.link": _two_paths,
    "os.symlink": _two_paths,
    "shutil.copyfile": _two_paths,
    "shutil.copymode": _two_paths,
    "shutil.copystat": _two_paths,
    "shutil.copytree": _two_paths,
    "shutil.move": _two_paths,
    "shutil.rmtree": _path_arg,
    "subprocess.Popen": _popen,
    "os.system": _system,
    "os.spawn": _spawn,
    "os.exec": _spawn,
    "os.posix_spawn": _spawn,
    "winreg.OpenKey": _reg,
    "winreg.CreateKey": _reg,
    "socket.connect": _network,
    "socket.getaddrinfo": _network,
}


def _audit(event, args):
    h = _HANDLERS.get(event)
    if h is not None and _S.active:
        h(event, args)


class _ComBlocker:
    """A meta path finder that refuses the COM packages while the guard is on."""

    @staticmethod
    def find_spec(name, path=None, target=None):
        if _S.active and name.split(".", 1)[0] in BLOCKED_MODULES:
            with _S.lock:
                _S.sink.append("import of %s" % name)
            raise ImportError("hermetic test: no COM (%s cannot be imported)" % name)
        return None


_BLOCKER = _ComBlocker()


def install_hooks() -> None:
    """Install the audit hook and the import blocker (once per process; both do nothing
    while the guard is off)."""
    if _S.hooked:
        return
    _S.hooked = True
    sys.addaudithook(_audit)
    sys.meta_path.insert(0, _BLOCKER)


def _drop_com_modules() -> dict:
    """Take the COM packages out of sys.modules (an import must go through the blocker);
    returns them, to put back."""
    gone = {}
    for k in list(sys.modules):
        if k.split(".", 1)[0] in BLOCKED_MODULES:
            gone[k] = sys.modules.pop(k)
    return gone


def force_environment(environ=None) -> None:
    """Set :data:`FORCED_ENV` in ``environ`` (default os.environ)."""
    env = os.environ if environ is None else environ
    for k, v in FORCED_ENV.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v


def enabled() -> bool:
    return os.environ.get(ENV) == "1"


def activate() -> None:
    """Turn the guard on for the rest of this process (CTG_HERMETIC=1)."""
    install_hooks()
    _drop_com_modules()
    force_environment()
    _S.active = True


def guard_child() -> None:
    """The guard on in a tool a hermetic test runs as a child process (it inherits
    CTG_HERMETIC=1): the hooks and the COM block as in the test, the environment the test
    set (its fake install) kept. A refusal raises where it happens; one the tool swallowed
    is printed at exit, and the process then exits with status 70, so the test fails."""
    import atexit

    install_hooks()
    _drop_com_modules()
    noted: list = []
    with _S.lock:
        _S.sink, _S.active = noted, True

    def _report() -> None:
        if noted:
            sys.stderr.write("hermetic child: %d refusals:\n  %s\n" % (len(noted), "\n  ".join(noted[:10])))
            sys.stderr.flush()
            os._exit(70)

    atexit.register(_report)


@contextmanager
def guarding():
    """The guard on inside the block, its violations noted in the list it yields (not the
    running test's); the state before is restored after (tests/test_hermetic.py)."""
    install_hooks()
    gone = _drop_com_modules()
    mine: list = []
    with _S.lock:
        old_sink, old_active = _S.sink, _S.active
        _S.sink, _S.active = mine, True
    try:
        yield mine
    finally:
        with _S.lock:
            _S.sink, _S.active = old_sink, old_active
        for k, m in gone.items():
            sys.modules.setdefault(k, m)


def take_violations() -> list:
    with _S.lock:
        out = list(_S.sink)
        _S.sink.clear()
    return out


if enabled():
    activate()


# --------------------------------------------------------------------------- pytest hooks
try:
    import pytest
except ImportError:  # (the module is also usable without pytest)
    pytest = None


if pytest is not None:

    def _check(when: str) -> None:
        bad = take_violations()
        if bad:
            shown = "\n  ".join(bad[:8]) + ("\n  ..." if len(bad) > 8 else "")
            pytest.fail(
                "hermetic test (%s): it reached outside its fake install:\n  %s" % (when, shown),
                pytrace=False,
            )

    _NOTES_KEY = pytest.StashKey[list]()

    def _wrap(item, when):
        """One phase of a test (a ``wrapper=True`` hook's body): its notes are taken on every
        path. A pass, a skip or an xfail that reached out fails here, named by its own
        phase; a phase that failed anyway keeps its notes for its report (not for the
        teardown check)."""
        res = skipped = None
        try:
            res = yield
        except (pytest.skip.Exception, pytest.xfail.Exception) as e:
            skipped = e
        except BaseException:
            if _S.active:
                bad = take_violations()
                if bad:
                    item.stash.setdefault(_NOTES_KEY, []).extend(bad)
            raise
        if _S.active:
            _check(when)  # (outside the except: no chained skip shown)
        if skipped is not None:
            raise skipped
        return res

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_setup(item):
        if _S.active:
            between = take_violations()  # what a background thread did between tests
            if between:
                with _S.lock:
                    _S.between.extend(between)
        return (yield from _wrap(item, "setup"))

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(item):
        return (yield from _wrap(item, "call"))

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_teardown(item, nextitem):
        return (yield from _wrap(item, "teardown"))

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(item, call):
        rep = yield
        notes = item.stash.get(_NOTES_KEY, None)
        if notes and rep.failed:
            rep.sections.append(
                (
                    "hermetic (%s)" % call.when,
                    "it also reached outside its fake install:\n  " + "\n  ".join(notes[:8]),
                )
            )
            notes.clear()
        return rep

    def pytest_sessionfinish(session, exitstatus):
        if not _S.active:
            return
        late = take_violations()  # (after the last test's teardown)
        with _S.lock:
            _S.between.extend(late)
        if _S.between:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(terminalreporter, exitstatus, config):
        with _S.lock:
            bad = list(_S.between)
        if not bad:
            return
        tr = terminalreporter
        tr.section("hermetic", sep="=", red=True)
        tr.line("between tests, a background thread reached outside the fake install (the run fails):")
        for what in bad[:8]:
            tr.line("  " + what)
        if len(bad) > 8:
            tr.line("  ... (%d in all)" % len(bad))

    @pytest.fixture(autouse=True)
    def _hermetic_environment(monkeypatch):
        """Each hermetic test starts in :data:`FORCED_ENV` (a test may change it; the
        fx_install fixture will set its own CTG_SCADA_ROOT)."""
        if _S.active:
            for k, v in FORCED_ENV.items():
                if v is None:
                    monkeypatch.delenv(k, raising=False)
                else:
                    monkeypatch.setenv(k, v)
        yield
