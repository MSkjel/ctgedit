"""Start or restart the Plant SCADA runtime for a project."""

from __future__ import annotations

from ..infra.paths import path_key

import os
import subprocess
import time
from typing import List

from ..format.fileio import write_atomic
from . import winui
from .compiler import active_ini, root_project_for, scada_bin

IMAGES = ("Citect32.exe", "Citect.exe", "RuntimeManager.exe", "SE.SCADA.RuntimeManager.BackEndHost.exe")


def running_processes() -> dict:
    """{image: [pid, ...]} of the runtime's processes that are running."""
    out = {}
    for im in IMAGES:
        pids = winui.pids_of(im)
        if pids:
            out[im] = pids
    return out


def is_running() -> bool:
    return bool(winui.pids_of("Citect32.exe") or winui.pids_of("Citect.exe"))


def running_project() -> str:
    """Name of the project the running runtime was started with -- the root
    project ``[CtEdit] Run`` names (what the Runtime Manager starts); '' when
    the key is empty (the Graphics Builder's automation can clear it)."""
    from ..io.citect_config import ini_value

    run = ini_value("CtEdit", "Run", "").strip().rstrip("\\/")
    return os.path.basename(run) if run else ""


BACKUP_SUFFIX = ".ctgedit-bak"


def set_run_project(project_dir: str) -> str:
    """Point ``[CtEdit] Run`` in citect.ini at the root project of ``project_dir``."""
    from ..format.projects import master_projects
    from ..io.citect_config import live_user_dir

    root = root_project_for(project_dir)
    path = project_dir
    user = live_user_dir()
    if user:
        master = master_projects(user)
        path = master.get(root.lower()) or master.get(root) or project_dir
    ini = active_ini()
    raw = open(ini, "rb").read()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    lines = raw.decode("cp1252").split(nl)
    sec, done, ctedit_at = None, False, None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1].strip().lower()
            if sec == "ctedit":
                ctedit_at = i
            continue
        if sec == "ctedit" and s.split("=", 1)[0].strip().lower() == "run":
            lines[i] = "Run =" + path
            done = True
    if not done:
        if ctedit_at is None:
            lines += ["[CtEdit]", "Run =" + path]
        else:
            lines.insert(ctedit_at + 1, "Run =" + path)
    new = nl.join(lines).encode("cp1252")
    if new != raw:
        write_atomic(ini + BACKUP_SUFFIX, raw)
        write_atomic(ini, new)
        from ..infra import logsetup

        logsetup.files_written(
            "[CtEdit] Run = %s" % path,
            [ini + BACKUP_SUFFIX, ini],
            "the old citect.ini is kept as the .ctgedit-bak copy",
        )
    return path


def runs_project(project_dir: str) -> bool:
    """Is ``[CtEdit] Run`` (what the runtime runs) ``project_dir``'s root
    project?"""
    from ..format.projects import master_projects
    from ..io.citect_config import ini_value, live_user_dir

    run = ini_value("CtEdit", "Run", "").strip().rstrip("\\/")
    if not run:
        return False
    root = root_project_for(project_dir)
    user = live_user_dir()
    path = (master_projects(user).get(root.lower()) if user else None) or project_dir
    return path_key(run) == path_key(path.rstrip("\\/"))


def _wait_until(done, seconds: float) -> bool:
    end = time.time() + seconds
    while True:
        if done():
            return True
        if time.time() >= end:
            return False
        time.sleep(0.3)


def _wait_gone(seconds: float) -> bool:
    return _wait_until(lambda: not running_processes(), seconds)


def stop(timeout: float = 30.0, graceful: float = 20.0) -> str:
    """Shut the runtime down, gently first."""
    if not running_processes():
        return "not running"
    shut_down = False
    if is_running():
        try:
            from . import ctapi

            res = ctapi.cicode("DelayShutdown(1000)", timeout=15.0)
        except Exception:  # noqa: BLE001 -- swallow-ok: no CtAPI: the next steps
            res = ["ERR"]
        if not any(r.startswith("ERR") for r in res):
            shut_down = _wait_until(lambda: not is_running(), graceful)
            if shut_down and _wait_gone(5.0):
                return "shut down"
    for im in IMAGES:
        subprocess.run(["taskkill", "/IM", im], capture_output=True)
    if _wait_gone(min(10.0, timeout)):
        return "shut down" if shut_down else "closed"
    for im in IMAGES:
        subprocess.run(["taskkill", "/F", "/IM", im], capture_output=True)
    _wait_gone(timeout)
    from ..infra import logsetup

    logsetup.get("runtime").warning("the runtime had to be force-killed (taskkill /F)")
    return "shut down" if shut_down else "forced"


def start(project_dir: str, restart: bool = True) -> List[str]:
    """Run ``project_dir`` (its root project): set ``[CtEdit] Run``, stop a
    runtime already running when ``restart``, then launch the Runtime Manager.
    Returns a list of notes for the status line. The CALLER asks the user
    before a running runtime is stopped (ui MainWindow._run_project)."""
    notes = []
    path = set_run_project(project_dir)
    notes.append("run project: %s" % os.path.basename(path.rstrip("\\/")))
    if restart and running_processes():
        how = stop()
        notes.append("stopped the running runtime%s" % (" (it had to be forced)" if how == "forced" else ""))
    exe = os.path.join(scada_bin(), "RuntimeManager.exe")
    if not os.path.exists(exe):
        raise FileNotFoundError(exe)
    subprocess.Popen(
        [exe], cwd=os.path.dirname(exe), creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
    )
    from ..infra import logsetup

    logsetup.get("runtime").info("started the Runtime Manager for %s (%s)", project_dir, "; ".join(notes))
    return notes
