"""Crash reports and the error log."""

from __future__ import annotations

import datetime
import faulthandler
import glob
import os
import re
import sys
import traceback
from typing import List, Optional

from .infra.paths import pid_alive

MAX_LOG_BYTES = 1 << 20  # errors.log / output.log roll over at 1 MB
KEEP_REPORTS = 10  # crash reports kept

_session = {"fh": None, "path": None}
_REPORT_RX = re.compile(r"crash-(\d{8}-\d{6})-(\d+)\.log$")


def log_dir() -> str:
    d = os.environ.get("CTG_LOG_DIR")
    if not d:
        from .infra.paths import data_dir  # %LOCALAPPDATA%\ctgedit (CTG_DATA_DIR)

        d = data_dir("logs")
    return d


def _identity() -> str:
    try:
        from .buildinfo import version_string

        return "ctgedit " + version_string()
    except Exception:  # noqa: BLE001 -- swallow-ok: a log header must never fail
        return "ctgedit"


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _roll(path: str) -> None:
    try:
        if os.path.getsize(path) > MAX_LOG_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass


def collect_previous_crashes() -> List[str]:
    """Crash files left by runs that ended without a clean exit, renamed to
    ``crash-<time>-<pid>.txt``; returns those report paths (newest last). A left-over
    file with nothing after its header (the process was killed, not crashed)
    is deleted. Only the newest :data:`KEEP_REPORTS` reports are kept."""
    d = log_dir()
    found = []
    for path in sorted(glob.glob(os.path.join(d, "crash-*-*.log"))):
        m = _REPORT_RX.search(os.path.basename(path))
        if not m or int(m.group(2)) == os.getpid() or pid_alive(int(m.group(2))):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        if len([ln for ln in body.splitlines() if ln.strip()]) <= 1:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        report = os.path.join(d, "crash-%s-%s.txt" % (m.group(1), m.group(2)))
        try:
            os.replace(path, report)  # fails while its process holds it open
        except OSError:
            continue
        found.append(report)
    reports = sorted(glob.glob(os.path.join(d, "crash-*.txt")))
    for old in reports[:-KEEP_REPORTS]:
        try:
            os.remove(old)
        except OSError:
            pass
    return [r for r in found if os.path.exists(r)]


def install() -> Optional[str]:
    """Start this run's crash report (faulthandler) and give a console-less
    build somewhere to write. Returns the crash file, None if the log folder
    cannot be written (the editor then runs without one)."""
    if _session["fh"] is not None:
        return _session["path"]
    d = log_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return None
    if sys.stdout is None or sys.stderr is None:
        out = os.path.join(d, "output.log")
        _roll(out)
        try:
            stream = open(out, "a", encoding="utf-8", errors="replace", buffering=1)
        except OSError:
            stream = open(os.devnull, "w")
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    path = os.path.join(
        d, "crash-%s-%d.log" % (datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), os.getpid())
    )
    try:
        fh = open(path, "w", encoding="utf-8", errors="replace")
        fh.write(
            "%s: started %s, pid %d, Python %s\n"
            % (_identity(), _stamp(), os.getpid(), sys.version.split()[0])
        )
        fh.flush()
        faulthandler.enable(file=fh, all_threads=True)
    except OSError, RuntimeError, ValueError:
        return None
    _session.update(fh=fh, path=path)
    return path


def uninstall() -> None:
    """A clean exit: stop faulthandler and delete this run's crash file."""
    fh, path = _session["fh"], _session["path"]
    if fh is None:
        return
    try:
        faulthandler.disable()
    except Exception:  # noqa: BLE001 -- swallow-ok: faulthandler was not on
        pass
    try:
        fh.close()
        os.remove(path)
    except OSError:
        pass
    _session.update(fh=None, path=None)


def log_exception(etype, value, tb) -> Optional[str]:
    """Append an unhandled exception to ``errors.log``; returns its path."""
    d = log_dir()
    path = os.path.join(d, "errors.log")
    try:
        os.makedirs(d, exist_ok=True)
        _roll(path)
        with open(path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write("\n--- %s  %s\n" % (_stamp(), _identity()))
            fh.write("".join(traceback.format_exception(etype, value, tb)))
    except OSError:
        return None
    return path
