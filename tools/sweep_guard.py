"""Safety net for the sweeps that drive the whole editor. Import it before PySide6 and ctgedit."""

from __future__ import annotations

import builtins
import faulthandler
import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2"
LIVE = os.path.normcase(R2)
#: the programs a sweep may start (by file name; this Python always)
ALLOWED_PROGRAMS = ("python.exe", "pythonw.exe", "ctsim.exe", "git", "git.exe")

problems: list = []  # (where, text)
refused: list = []  # (where, what)
started: list = []  # (where, program line)
state = {"where": ""}
TMP = ""
_fault = None


def _live(p) -> bool:
    try:
        if isinstance(p, int):
            return False
        return os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(p)))).startswith(LIVE)
    except Exception:  # noqa: BLE001
        return False


def _refuse(what, p):
    refused.append((state["where"], "%s %s" % (what, p)))
    raise PermissionError("SWEEP: a write into the live install refused: %s %s" % (what, p))


def _guard_writes() -> None:
    real_open = builtins.open

    def guarded_open(file, mode="r", *a, **k):
        if any(c in mode for c in "wax+") and _live(file):
            _refuse("open(%s)" % mode, file)
        return real_open(file, mode, *a, **k)

    builtins.open = guarded_open
    io.open = guarded_open
    real_os_open = os.open

    def guarded_os_open(path, flags, *a, **k):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC) and _live(path):
            _refuse("os.open", path)
        return real_os_open(path, flags, *a, **k)

    os.open = guarded_os_open

    def wrap(mod, name, args=(0,)):
        fn = getattr(mod, name)

        def g(*a, **k):
            for i in args:
                if i < len(a) and _live(a[i]):
                    _refuse("%s.%s" % (mod.__name__, name), a[i])
            for key in ("dst", "dir", "path"):
                if key in k and _live(k[key]):
                    _refuse("%s.%s" % (mod.__name__, name), k[key])
            return fn(*a, **k)

        setattr(mod, name, g)

    for n in ("remove", "unlink", "rmdir", "mkdir", "makedirs", "utime", "truncate"):
        wrap(os, n)
    for n in ("replace", "rename"):
        wrap(os, n, (0, 1))
    for n in ("copyfile", "copy", "copy2", "move", "copytree"):
        wrap(shutil, n, (1,))
    wrap(shutil, "rmtree")
    real_mkstemp = tempfile.mkstemp

    def guarded_mkstemp(*a, **k):
        d = k.get("dir") if "dir" in k else (a[2] if len(a) > 2 else None)
        if d is not None and _live(d):
            _refuse("mkstemp", d)
        return real_mkstemp(*a, **k)

    tempfile.mkstemp = guarded_mkstemp


def program_allowed(args) -> bool:
    """``args`` (a Popen argument: a list or a command line) starts an allowed program."""
    if isinstance(args, (list, tuple)):
        first = str(args[0]) if args else ""
    else:
        line = str(args).strip()
        first = line.split('"')[1] if line.startswith('"') else line.split(" ")[0]
    first = first.strip('"')
    if os.path.normcase(first) == os.path.normcase(sys.executable):
        return True
    return os.path.basename(first).lower() in ALLOWED_PROGRAMS


def _guard_programs() -> None:
    real_init = subprocess.Popen.__init__

    def guarded_init(self, args, *a, **k):
        started.append((state["where"], str(args)[:300]))
        if not program_allowed(args):
            refused.append((state["where"], "program %s" % str(args)[:300]))
            raise PermissionError("SWEEP: a program was not started: %s" % str(args)[:200])
        return real_init(self, args, *a, **k)

    subprocess.Popen.__init__ = guarded_init
    os.startfile = lambda *a, **k: refused.append((state["where"], "startfile %s" % (a,)))


def _isolate() -> None:
    global TMP
    TMP = tempfile.mkdtemp(prefix="ctgsweep-")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    os.environ["CTG_SETTINGS_INI"] = os.path.join(TMP, "settings.ini")
    os.environ["CTG_DATA_DIR"] = os.path.join(TMP, "data")
    for n in ("CTG_NO_RUNTIME", "CTG_NO_CTF_PRECHECK", "CTG_NO_SINGLE_INSTANCE", "CTG_NO_ANIMATION"):
        os.environ[n] = "1"
    os.environ.pop("CTG_NO_DIALOG_REAP", None)  # (the app's own dialog release)


def install(out_prefix: str) -> None:
    """The guards, the isolation and the native crash stack (``<out_prefix>.fault``)."""
    global _fault
    _isolate()
    _guard_writes()
    _guard_programs()
    _fault = open(out_prefix + ".fault", "w")
    faulthandler.enable(_fault, all_threads=False)


def answer_qt(app) -> None:
    """The Qt stand-ins (call once the QApplication exists): every modal answers at once,
    a watchdog closes what slipped through, exceptions and swallowed errors are recorded."""
    from PySide6 import QtCore, QtGui, QtWidgets

    def hook(t, v, tb):
        problems.append((state["where"], "".join(traceback.format_exception(t, v, tb))[-1800:]))

    sys.excepthook = hook
    QtWidgets.QDialog.exec = lambda self, *a, **k: 0
    QtWidgets.QMenu.exec = lambda self, *a, **k: None
    for name in ("warning", "information", "critical", "question"):
        setattr(
            QtWidgets.QMessageBox,
            name,
            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Cancel),
        )
    QtWidgets.QMessageBox.exec = lambda self, *a, **k: int(QtWidgets.QMessageBox.Cancel)
    F = QtWidgets.QFileDialog
    F.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
    F.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
    F.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
    F.getExistingDirectory = staticmethod(lambda *a, **k: "")
    QtWidgets.QColorDialog.getColor = staticmethod(lambda *a, **k: QtGui.QColor())
    I = QtWidgets.QInputDialog
    I.getText = staticmethod(lambda *a, **k: ("", False))
    I.getInt = staticmethod(lambda *a, **k: (0, False))
    I.getDouble = staticmethod(lambda *a, **k: (0.0, False))
    I.getItem = staticmethod(lambda *a, **k: ("", False))
    QtWidgets.QFontDialog.getFont = staticmethod(lambda *a, **k: (False, QtGui.QFont()))
    QtGui.QDesktopServices.openUrl = staticmethod(
        lambda *a, **k: refused.append((state["where"], "openUrl %s" % (a,))) or True
    )

    def no_detached(*a, **k):
        started.append((state["where"], "QProcess %s" % (a,)))
        refused.append((state["where"], "QProcess.startDetached %s" % (a,)))
        return False

    QtCore.QProcess.startDetached = staticmethod(no_detached)

    def watchdog():
        for getter in (QtWidgets.QApplication.activeModalWidget, QtWidgets.QApplication.activePopupWidget):
            m = getter()
            if m is not None:
                try:
                    m.close()
                except RuntimeError:
                    pass

    app._sweep_watchdog = QtCore.QTimer(app)
    app._sweep_watchdog.timeout.connect(watchdog)
    app._sweep_watchdog.start(500)

    class Swallowed(logging.Handler):
        def emit(self, rec):
            msg = rec.getMessage()
            if "swallowed" in msg or rec.levelno >= logging.ERROR:
                problems.append((state["where"], "LOG: " + msg[:900]))

    logging.getLogger("ctgedit").addHandler(Swallowed())


def copy_project(project: str, pages, dst_root: str = "") -> str:
    """A copy of R2 User\\<project>: its tables (not the 100+ MB page register) and libraries,
    and the pages ``pages`` (names without .ctg). CTG_LIB_PATH gets the live folder (read)."""
    src = os.path.join(R2, "User", project)
    dst = os.path.join(dst_root or TMP, project)
    os.makedirs(dst, exist_ok=True)
    skip = (
        "pgdynobj",
        "pgtext",
        "pgsym",
        "pgcol",
        "pgadv",
        "pgansym",
        "pgsizmve",
        "pgdisp",
        "pgrecipe",
        "pgtrend",
        "errlog",
    )
    for f in os.listdir(src):
        fp = os.path.join(src, f)
        low = f.lower()
        if (
            os.path.isfile(fp)
            and low.endswith((".dbf", ".ctm", ".ctl", ".ctt", ".ini"))
            and not low.startswith(skip)
            and os.path.getsize(fp) < 30_000_000
        ):
            shutil.copyfile(fp, os.path.join(dst, f))
    for p in pages:
        shutil.copyfile(os.path.join(src, p + ".ctg"), os.path.join(dst, p + ".ctg"))
    os.environ["CTG_LIB_PATH"] = os.pathsep.join(x for x in (os.environ.get("CTG_LIB_PATH", ""), src) if x)
    return dst


def write_report(path: str, extra_lines=(), summary: str = "") -> int:
    """The report: the problems (each once), the refused writes and programs, ``extra_lines``
    and ``summary``. Returns the number of distinct problems."""
    import re

    seen = set()
    with open(path, "w", encoding="utf-8") as f:
        for where, text in problems:
            key = re.sub(r"0x[0-9a-fA-F]+|step \d+", "", text[-400:])
            if key in seen:
                continue
            seen.add(key)
            f.write("===== %s\n%s\n" % (where, text))
        for where, what in refused:
            f.write("REFUSED %s: %s\n" % (where, what))
        for line in extra_lines:
            f.write(line.rstrip("\n") + "\n")
        f.write("%s %d problem(s), %d refused\n" % (summary or "DONE:", len(seen), len(refused)))
    return len(seen)


def finish(code: int) -> None:
    """Leave without Qt's teardown (it can crash after the report on Windows)."""
    sys.stdout.flush()
    sys.stderr.flush()
    shutil.rmtree(TMP, ignore_errors=True)
    from ctgedit.infra.paths import hard_exit

    hard_exit(code)
