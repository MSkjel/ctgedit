"""Which pages run inside their SA workspace."""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from ...infra import logsetup

#: the startup page's own page events that build an SA workspace
WORKSPACE_INIT = "workspace_init"


def _ini_has(path: str, section: str, key: str) -> Tuple[bool, str]:
    """(the file has the key, its value): GetPrivateProfileString's case-insensitive look."""
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("cp1252", "replace")
    except OSError:
        return False, ""
    sec = None
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1].strip().lower()
        elif sec == section.lower() and "=" in s:
            k, v = s.split("=", 1)
            if k.strip().lower() == key.lower():
                return True, v.strip()
    return False, ""


def startup_candidates(project_dir: str) -> List[str]:
    """The startup page names in the engine's order (engine Sim/RuntimeStartup.cs
    StartupPage): [MultiMonitors] StartupPageScreen1, [Page] Startup, then the default
    'Startup'. Each from the project folder's citect.ini when it has the key, else the active
    install's citect.ini (the computer's: ParameterGet)."""
    from ...io import citect_config

    own = os.path.join(project_dir, "citect.ini")
    out: List[str] = []
    for sec, key in (("MultiMonitors", "StartupPageScreen1"), ("Page", "Startup")):
        has, v = _ini_has(own, sec, key) if os.path.isfile(own) else (False, "")
        if not has:
            try:
                v = citect_config.ini_value(sec, key, "")
            except OSError:
                v = ""
        v = (v or "").strip()
        if v and v.lower() not in (x.lower() for x in out):
            out.append(v)
    if "startup" not in (x.lower() for x in out):
        out.append("Startup")
    return out


def _find_page(name: str, dirs: List[str]) -> str:
    for d in dirs:
        p = os.path.join(d, name + ".ctg")
        if os.path.isfile(p):
            return p
    return ""


def is_workspace_page(page_path: str) -> bool:
    """The page builds an SA workspace: its pages.DBF row's page events call Workspace_Init
    (ExampleSA Master_PageMenu1_HD1080: READY_FUNC 'Workspace_Init()')."""
    from ...io.project import Project

    name = os.path.splitext(os.path.basename(page_path))[0]
    try:
        rec = Project(os.path.dirname(page_path)).page_record(name) or {}
    except Exception:  # noqa: BLE001 -- a table that cannot be read: not a workspace page
        logsetup.note_exception("sim.sa_start.is_workspace_page")
        return False
    for f in ("READY_FUNC", "ENTRY_FUNC", "ACT_FUNC"):
        if WORKSPACE_INIT in str(rec.get(f) or "").replace(" ", "").lower():
            return True
    return False


def workspace_start(project_dir: str, page_name: str = "") -> Optional[Tuple[str, str]]:
    """(name, path) of the project's startup page when it builds an SA workspace and is not
    ``page_name`` itself, else None. The first candidate that is a page of the project or its
    include chain decides (the engine shows that one)."""
    if not project_dir or not os.path.isdir(project_dir):
        return None
    try:
        from ...format.projects import include_search_dirs

        dirs = [project_dir] + include_search_dirs(project_dir)
    except Exception:  # noqa: BLE001
        logsetup.note_exception("sim.sa_start.workspace_start")
        dirs = [project_dir]
    for name in startup_candidates(project_dir):
        path = _find_page(name, dirs)
        if not path:
            continue
        if page_name and name.strip().lstrip("!").lower() == page_name.strip().lstrip("!").lower():
            return None
        return (name, path) if is_workspace_page(path) else None
    return None
