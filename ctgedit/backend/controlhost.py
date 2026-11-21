"""Run a page's embedded controls (ActiveX, Process Analyst ...) in the 32-bit control host.

The host (ctgedit/native/control_host.cs) is compiled on first use with the .NET Framework's
csc.exe into the data folder. It loads a private copy of a control's saved state and either
captures a picture of the control or shows it with its properties and writes the new state.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import threading
from typing import Optional

from ..infra import caches as _caches
from ..infra import logsetup
from ..infra.paths import data_dir

SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "native", "control_host.cs"
)
CSC = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), r"Microsoft.NET\Framework\v4.0.30319\csc.exe")
#: a picture the control did not finish within this many seconds is not taken
DRAW_TIMEOUT = 30.0
#: the host's exit codes
DONE, FAILED, CANCELLED, NO_PICTURE = 0, 1, 2, 3

_BUILD_LOCK = threading.Lock()
_PICTURE_LOCK = threading.Lock()
#: picture key -> PNG path, or '' (the control paints nothing outside its editor)
_PICTURES: dict = {}
_caches.register("controlhost.pictures", _PICTURES, on=("tests",))


class ControlHostError(RuntimeError):
    """The control host could not be built or could not run the control."""


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def host_exe() -> str:
    """The compiled host, built from SOURCE when this version of it is not there yet."""
    with open(SOURCE, "rb") as f:
        src = f.read()
    folder = data_dir("controlhost", hashlib.sha1(src).hexdigest()[:12])
    exe = os.path.join(folder, "ctgctl.exe")
    if os.path.isfile(exe):
        return exe
    with _BUILD_LOCK:
        if os.path.isfile(exe):
            return exe
        if not os.path.isfile(CSC):
            raise ControlHostError("The .NET Framework 4 compiler is not installed: %s" % CSC)
        os.makedirs(folder, exist_ok=True)
        part = exe + ".part"
        r = subprocess.run(
            [CSC, "-nologo", "-target:winexe", "-platform:x86", "-out:" + part,
             "-reference:System.Windows.Forms.dll", "-reference:System.Drawing.dll", "-reference:System.Core.dll", SOURCE],
            capture_output=True, text=True, creationflags=_no_window(), timeout=120,
        )  # fmt: skip
        if r.returncode != 0 or not os.path.isfile(part):
            raise ControlHostError("The control host did not build: %s" % (r.stdout + r.stderr).strip())
        os.replace(part, exe)
    return exe


def available() -> bool:
    """The host can run here (Windows with the .NET Framework 4)."""
    return os.name == "nt" and os.path.isfile(CSC) and os.path.isfile(SOURCE)


def _environment() -> dict:
    env = dict(os.environ)
    try:
        from ..infra import install

        b = install.bin_dir()
    except Exception:  # noqa: BLE001 -- the controls may still load from their own folders
        logsetup.note_exception("controlhost._environment")
        b = None
    if b:
        env["CTG_CONTROL_BIN"] = b
    return env


def _run(args, timeout: Optional[float]) -> subprocess.CompletedProcess:
    exe = host_exe()
    try:
        return subprocess.run(
            [exe] + [str(a) for a in args], capture_output=True, text=True, env=_environment(),
            timeout=timeout, creationflags=_no_window(),
        )  # fmt: skip
    except subprocess.TimeoutExpired as e:
        raise ControlHostError("The control did not answer within %d seconds." % int(timeout or 0)) from e


def picture_key(clsid: str, state: bytes, width: int, height: int) -> str:
    h = hashlib.sha1(clsid.upper().encode("ascii") + b"|%d|%d|" % (width, height))
    h.update(state)
    return h.hexdigest()


def cached_picture(key: str) -> Optional[str]:
    """The PNG of a picture already taken ('' = the control has none), None = not taken yet."""
    with _PICTURE_LOCK:
        if key in _PICTURES:
            return _PICTURES[key]
    folder = data_dir("controlhost", "pictures")
    png = os.path.join(folder, key + ".png")
    if os.path.isfile(png):
        found = png
    elif os.path.isfile(os.path.join(folder, key + ".none")):
        found = ""
    else:
        return None
    with _PICTURE_LOCK:
        _PICTURES[key] = found
    return found


def take_picture(clsid: str, state: bytes, width: int, height: int) -> str:
    """Run the control and capture it at ``width`` x ``height`` pixels: the PNG's path, or ''
    when the control paints nothing outside its own editor. Kept on disk by the control's
    class, state and size, so each picture is taken once. Not on the GUI thread: it takes a
    second or more."""
    key = picture_key(clsid, state, width, height)
    have = cached_picture(key)
    if have is not None:
        return have
    folder = data_dir("controlhost", "pictures")
    os.makedirs(folder, exist_ok=True)
    png = os.path.join(folder, key + ".png")
    with tempfile.TemporaryDirectory(prefix="ctgctl_") as tmp:
        stg = os.path.join(tmp, "state.stg")
        with open(stg, "wb") as f:
            f.write(state)
        part = os.path.join(tmp, "picture.png")
        r = _run(["draw", clsid, stg, part, max(1, int(width)), max(1, int(height))], DRAW_TIMEOUT)
        if r.returncode == DONE and os.path.isfile(part):
            os.replace(part, png)
            found = png
        elif r.returncode == NO_PICTURE:
            with open(os.path.join(folder, key + ".none"), "w") as f:
                f.write(clsid)
            found = ""
        else:
            raise ControlHostError((r.stderr or "").strip() or "The control host failed (%d)." % r.returncode)
    with _PICTURE_LOCK:
        _PICTURES[key] = found
    return found


def edit(clsid: str, state: bytes, width: int, height: int, owner: int = 0) -> Optional[bytes]:
    """Show the control with its properties (the window is owned by window handle ``owner``)
    and return its new saved state, or None when the user cancelled."""
    with tempfile.TemporaryDirectory(prefix="ctgctl_") as tmp:
        stg = os.path.join(tmp, "state.stg")
        with open(stg, "wb") as f:
            f.write(state)
        out = os.path.join(tmp, "new.stg")
        args = ["edit", clsid, stg, out, max(1, int(width)), max(1, int(height))]
        if owner:
            args.append(int(owner))
        r = _run(args, None)
        if r.returncode == CANCELLED:
            return None
        if r.returncode != DONE or not os.path.isfile(out):
            raise ControlHostError((r.stderr or "").strip() or "The control host failed (%d)." % r.returncode)
        with open(out, "rb") as f:
            return f.read()
