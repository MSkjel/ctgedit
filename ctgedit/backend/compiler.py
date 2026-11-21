"""Compile a Plant SCADA project with CtCmp32.exe and read its error log."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import winui

IMAGE = "CtCmp32.exe"
_LIST_ID = 111  # the progress dialog's error list box
_MSG_RE = re.compile(
    r"^(?P<file>.*?)\((?P<line>\d*)\) : (?P<message>.*?) "
    r"\((?P<code>[A-Z]\d+)\)(?: :(?: (?P<context>.*))?)?\s*$"
)


class CompileError(RuntimeError):
    pass


@dataclass
class CompileMessage:
    project: str
    file: str
    line: Optional[int]
    message: str
    code: str
    severity: str  # error | warning | other
    context: str = ""


@dataclass
class CompileResult:
    project: str
    outcome: str  # completed | timeout | message_box | cancelled
    exit_code: Optional[int]
    seconds: float
    messages: List[CompileMessage] = field(default_factory=list)
    errs_rewritten: bool = False
    note: str = ""

    @property
    def errors(self) -> List[CompileMessage]:
        return [m for m in self.messages if m.severity == "error"]

    @property
    def warnings(self) -> List[CompileMessage]:
        return [m for m in self.messages if m.severity == "warning"]

    @property
    def success(self) -> bool:
        return self.outcome == "completed" and not self.errors and (self.exit_code in (0, None))


def scada_bin() -> str:
    """The Plant SCADA ``Bin`` folder (CTG_SCADA_BIN overrides; infra/install.bin_dir)."""
    from ..infra import install

    b = install.bin_dir()
    if b:
        return b
    raise CompileError("the Plant SCADA Bin folder was not found (set CTG_SCADA_BIN)")


def exe_path() -> str:
    p = os.path.join(scada_bin(), IMAGE)
    if not os.path.exists(p):
        raise CompileError("%s not found at %s" % (IMAGE, p))
    return p


def active_ini() -> str:
    """The ACTIVE install's citect.ini (io/citect_config.active_ini_path)."""
    from ..io.citect_config import active_ini_path

    ini = active_ini_path()
    if ini:
        return ini
    raise CompileError("citect.ini of the active install not found")


def errs_path(ini: str) -> str:
    return os.path.join(os.path.dirname(os.path.dirname(ini)), "Logs", "errs")


def root_projects(user_dir: str) -> List[str]:
    """Projects no other project includes (compile targets), MASTER.DBF order."""
    from ..format.projects import master_projects, included_project_names

    master = master_projects(user_dir)
    included = set()
    for name, path in master.items():
        if path and os.path.isdir(path):
            included.update(n.lower() for n in included_project_names(path))
    # MASTER.DBF keys come back lower-cased; hand the compiler the project's
    # real (folder) name, as Studio does
    return [
        os.path.basename(master[n].rstrip("\\/")) if master.get(n) else n
        for n in master
        if n.lower() not in included
    ]


def _registered_root(project_dir: str, user: str) -> Optional[str]:
    """The root project of the install in ``user`` whose include closure contains
    ``project_dir``, or None."""
    from ..format.projects import include_search_dirs, master_projects

    target = os.path.normcase(os.path.abspath(project_dir))
    master = master_projects(user)
    for name in root_projects(user):
        path = master.get(name.lower()) or master.get(name)
        if not path:
            continue
        dirs = [path] + include_search_dirs(path)
        if any(os.path.normcase(os.path.abspath(d)) == target for d in dirs):
            return name
    return None


def root_project_for(project_dir: str) -> str:
    """The root project whose include closure contains ``project_dir`` (the
    compiler compiles a root with everything it includes). Falls back to the
    page's own project."""
    from ..io.citect_config import live_user_dir

    user = live_user_dir()
    own = os.path.basename(project_dir.rstrip("\\/"))
    if not user:
        return own
    return _registered_root(project_dir, user) or own


def in_active_install(project_dir: str) -> bool:
    """Is ``project_dir`` part of the active install? The compiler and runtime only find projects
    there."""
    from ..io.citect_config import live_user_dir

    user = live_user_dir()
    return not user or _registered_root(project_dir, user) is not None


def parse_errs(path: str) -> List[CompileMessage]:
    if not os.path.exists(path):
        return []
    out: List[CompileMessage] = []
    project = ""
    with open(path, encoding="cp1252", errors="replace") as fh:
        lines = fh.read().splitlines()
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        if line.rstrip().endswith("\\") and "(" not in line:
            project = os.path.basename(line.rstrip().rstrip("\\"))
            continue
        m = _MSG_RE.match(line)
        if not m:
            out.append(CompileMessage(project, "", None, line, "", "other"))
            continue
        code = m["code"]
        fpath = m["file"]
        own = os.path.basename(os.path.dirname(fpath)) if os.path.isabs(fpath) else ""
        fname = os.path.basename(fpath)
        out.append(
            CompileMessage(
                own or project,
                "" if fname == ".ci" else fname,
                int(m["line"]) if m["line"] else None,
                m["message"],
                code,
                "error" if code[0] in "EF" else "warning",
                (m["context"] or "").strip(),
            )
        )
    return out


def compile_project(
    project: str,
    *,
    timeout: float = 1800.0,
    poll: float = 0.4,
    on_tick: Optional[Callable[[], bool]] = None,
    wait: Optional[Callable[[float], None]] = None,
) -> CompileResult:
    """Compile ``project`` and parse the error log. ``on_tick()`` returning False cancels."""
    exe, ini = exe_path(), active_ini()
    if winui.pids_of(IMAGE):
        raise CompileError("%s is already running. Wait for it to finish." % IMAGE)
    errs = errs_path(ini)
    before = os.path.getmtime(errs) if os.path.exists(errs) else None
    cmd = '"%s" /I"%s" "%s"' % (exe, ini, project)
    t0 = time.time()
    proc = subprocess.Popen(cmd)
    outcome = None
    note = ""
    while True:
        if proc.poll() is not None:
            outcome = "completed"
            break
        for h in winui.windows_of(proc.pid):
            kids = winui.children(h)
            if winui.is_message_box(kids):
                note = winui.message_text(kids)
                winui.dismiss_message_box(h, kids)
        if on_tick is not None and on_tick() is False:
            for h in winui.windows_of(proc.pid):
                btn = winui.find_button(winui.children(h), "Cancel")
                if btn:
                    winui.click(btn)
            try:
                proc.wait(20)
            except subprocess.TimeoutExpired:
                proc.kill()
            outcome = "cancelled"
            break
        if time.time() - t0 > timeout:
            for h in winui.windows_of(proc.pid):
                btn = winui.find_button(winui.children(h), "Cancel")
                if btn:
                    winui.click(btn)
            try:
                proc.wait(20)
            except subprocess.TimeoutExpired:
                proc.kill()
            outcome = "timeout"
            break
        (wait or time.sleep)(poll)
    after = os.path.getmtime(errs) if os.path.exists(errs) else None
    rewritten = bool(after and after != before)
    # the compiler leaves Logs\errs untouched when it has nothing to report;
    # <project>\compilersummary.xml (the RDBs written) proves it ran
    summary_new = False
    try:
        from ..format.projects import master_projects
        from ..io.citect_config import live_user_dir

        pdir = master_projects(live_user_dir() or "").get(project.lower())
        cs = os.path.join(pdir or "", "compilersummary.xml")
        summary_new = os.path.exists(cs) and os.path.getmtime(cs) >= t0 - 1
    except Exception:  # noqa: BLE001
        pass
    if outcome == "completed" and not rewritten:
        note = (note + " " if note else "") + (
            "clean compile, no messages"
            if summary_new
            else "the compiler wrote neither Logs\\errs nor compilersummary.xml"
        )
    result = CompileResult(
        project=project,
        outcome=outcome,
        exit_code=proc.returncode,
        seconds=round(time.time() - t0, 1),
        messages=parse_errs(errs) if rewritten else [],
        errs_rewritten=rewritten,
        note=note,
    )
    from ..infra import logsetup

    logsetup.get("compile").info(
        "compiled %s: %s in %.1f s, exit code %s, %d error(s), %d warning(s)%s",
        project,
        outcome,
        result.seconds,
        proc.returncode,
        len(result.errors),
        len(result.warnings),
        "; %s" % note if note else "",
    )
    return result
