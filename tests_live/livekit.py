"""Helpers for the live suite."""

from __future__ import annotations

import contextlib
import datetime
import os
import shutil
import struct
import subprocess
import time
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

import pytest

# the local roles (tests_corpus/corpus.py, tests_corpus/local.json): a live test that needs
# one of the local projects or pages names a role, never the name (tests_live's conftest
# puts tests_corpus/ on sys.path)
from corpus import (  # noqa: F401
    install_path,
    missing,
    need_roles,
    not_configured,
    role,
    role_params,
    role_path,
    role_path_or_none,
    role_paths,
    role_value,
    roles,
    user_projects,
)

#: the Plant SCADA 2023 R2 install, in its default folders
R2_ROOT = os.path.join("C:\\", "ProgramData", "AVEVA Plant SCADA 2023 R2")
R2_USER = os.path.join(R2_ROOT, "User")
#: the programs a COM session may leave running (stop_studio ends them)
STUDIO_IMAGES = ("CitectIDE.exe", "CtDraw32.exe", "CtCmp32.exe")


# ---------------------------------------------------------------------------- the machine
def need_windows() -> None:
    if os.name != "nt":
        pytest.skip("a live test needs Windows")


def r2_project(name: str) -> str:
    """The folder of the R2 project ``name`` (read only), or skip."""
    path = os.path.join(R2_USER, name)
    if not os.path.isdir(path):
        pytest.skip("the R2 project %s is not installed" % name)
    return path


def r2_page(project: str, page: str) -> str:
    """The .ctg of an R2 page (``page`` with or without its extension), or skip."""
    name = page if page.lower().endswith(".ctg") else page + ".ctg"
    path = os.path.join(r2_project(project), name)
    if not os.path.isfile(path):
        pytest.skip("the R2 page %s\\%s is not there" % (project, name))
    return path


# ---------------------------------------------------------------------------- the engine
def real_engine():
    """(the built ctsim engine as a client.EngineLocation or None, the paths tried): found
    like the editor finds it (CTG_CTSIM_PATH, the checkout's external/cicode-sim net48
    Release output); the exe needs its DLL next to it."""
    from ctgedit.sim import client as C

    tried = []
    for path in [os.environ.get(C.PATH_ENV, ""), C.checkout_build_dir()]:
        if not path:
            continue
        exe = (
            os.path.join(path, C.EXE_NAME)
            if os.path.isdir(path) or not path.lower().endswith(".exe")
            else path
        )
        tried.append(exe)
        if os.path.isfile(exe) and os.path.isfile(os.path.join(os.path.dirname(exe), C.DLL_NAME)):
            return C.EngineLocation(exe, "test"), tried
    return None, tried


def need_engine():
    """The built engine, or skip with the paths tried."""
    loc, tried = real_engine()
    if loc is None:
        pytest.skip(
            "ctsim.exe is not built (tried %s): dotnet build "
            "external\\cicode-sim\\engine\\src\\ctsim\\ctsim.csproj -c Release -f net48" % ", ".join(tried)
        )
    return loc


# ---------------------------------------------------------------------------- copies
def write_dbf(path: str, fields: Sequence[Tuple[str, int]], rows: Iterable[dict]) -> None:
    """A dBASE III table of character fields (``fields`` = [(name, width)]) with ``rows``."""
    from ctgedit.format.dbf import Dbf

    desc = b"".join(
        n.encode("ascii").ljust(11, b"\0") + b"C" + b"\0" * 4 + bytes([ln]) + b"\0" * 15 for n, ln in fields
    )
    hlen = 32 + len(desc) + 1
    rlen = 1 + sum(ln for _n, ln in fields)
    d = datetime.date.today()
    head = struct.pack("<BBBBIHH20x", 3, d.year - 1900, d.month, d.day, 0, hlen, rlen)
    with open(path, "wb") as fh:
        fh.write(head + desc + b"\x0d" + b"\x1a")
    with Dbf(path) as t:
        t.append([t.encode(r) for r in rows])


def copy_r2_projects(
    dest: str, projects: Sequence[str], wanted: Optional[Callable[[str], bool]] = None
) -> str:
    """A COPY of the R2 ``projects`` under ``<dest>\\inst\\User`` with its own MASTER.DBF
    (never the live folders): the files ``wanted(name)`` accepts (default: every file).
    Skips when a project is not installed. Returns the copy's User folder."""
    user = os.path.join(str(dest), "inst", "User")
    rows = []
    for name in projects:
        src = r2_project(name)
        dst = os.path.join(user, name)
        os.makedirs(dst)
        for f in os.listdir(src):
            p = os.path.join(src, f)
            if os.path.isfile(p) and (wanted is None or wanted(f)):
                shutil.copyfile(p, os.path.join(dst, f))
        rows.append({"NAME": name, "PATH": dst})
    write_dbf(os.path.join(user, "MASTER.DBF"), [("NAME", 64), ("PATH", 200)], rows)
    return user


# ---------------------------------------------------------------------------- COM, Studio
def need_com() -> None:
    """Skip unless the COM bridge (pywin32) can be imported on Windows."""
    need_windows()
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError as e:
        pytest.skip("no COM bridge: %s" % e)


def studio_running() -> bool:
    from ctgedit.backend.graphics_builder import studio_running as running

    return running()


@contextlib.contextmanager
def graphics_builder(visible: bool = False):
    """The Graphics Builder over COM (ctgedit.backend.graphics_builder: it starts Studio when
    it is not running); Studio is stopped afterwards when this block started it (it holds a
    lot of memory)."""
    need_com()
    from ctgedit.backend.graphics_builder import GraphicsBuilder, GraphicsBuilderError

    was_running = studio_running()
    gb = GraphicsBuilder(visible=visible)
    try:
        try:
            gb.open()
        except GraphicsBuilderError as e:
            pytest.skip("the Graphics Builder did not start: %s" % e)
        yield gb
    finally:
        with contextlib.suppress(Exception):
            gb.close()
        if not was_running:
            stop_studio()


def stop_studio(timeout: float = 60.0) -> None:
    """Close Plant SCADA Studio (WM_CLOSE to its windows), then end what still runs of it, the
    Graphics Builder and the compiler (taskkill)."""
    from ctgedit.backend import winui

    pids = winui.pids_of("CitectIDE.exe")
    if pids:
        with contextlib.suppress(Exception):
            import win32con
            import win32gui
            import win32process

            def close(h, _):
                with contextlib.suppress(Exception):
                    if win32gui.IsWindowVisible(h) and win32process.GetWindowThreadProcessId(h)[1] in pids:
                        win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
                return True

            win32gui.EnumWindows(close, None)
        deadline = time.time() + timeout
        while time.time() < deadline and winui.pids_of("CitectIDE.exe"):
            time.sleep(1.0)
    for image in STUDIO_IMAGES:
        for pid in winui.pids_of(image):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, text=True)


def running_images() -> List[str]:
    """Which of Studio, the Graphics Builder and the compiler run now."""
    from ctgedit.backend import winui

    return [i for i in STUDIO_IMAGES if winui.pids_of(i)]
