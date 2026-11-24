"""Drive the Graphics Builder (CtDraw32) through its COM automation server."""

from __future__ import annotations

import contextlib
import os
from typing import Optional

CLSID = "{EB087A43-4189-46A9-8819-F717B9963C31}"


class GraphicsBuilderError(RuntimeError):
    pass


_PARKED: list = []  # attached proxies kept alive until process exit


def _ctdraw_pids() -> list:
    try:
        from .winui import pids_of

        return pids_of("CtDraw32.exe")
    except Exception:  # noqa: BLE001
        return []


STUDIO_IMAGE = "CitectIDE.exe"


# -- ending the Graphics Builder ---------------------------------------------------------
# CtDraw keeps every library element it loaded in memory and never reads the file again
# (its lookup matches the project, library and element names only): a new Graphics Builder
# is the one that shows a library ctgedit wrote. Studio goes on without it.


def builder_windows(pid: int) -> list:
    """The main windows (class ctDraw) of Graphics Builder process ``pid``."""
    try:
        import win32gui

        from .winui import windows_of

        return [h for h in windows_of(pid, cls=None) if win32gui.GetClassName(h) == "ctDraw"]
    except Exception:  # noqa: BLE001  # swallow-ok: no window to ask
        return []


def ask_builders_to_close() -> list:
    """Close every running Graphics Builder. With nothing open there (open_windows) it has
    nothing to save and is ended at once; else it is asked as its Close button asks (it
    asks about the pages it has not saved). Returns the PIDs that still run."""
    import win32con
    import win32gui

    pids = _ctdraw_pids()
    if not open_windows():
        end_builders(pids)
        return builders_running(pids)
    left = []
    for pid in pids:
        wins = builder_windows(pid)
        if not wins:
            end_builders([pid])
            continue
        for h in wins:
            with contextlib.suppress(Exception):
                win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        left.append(pid)
    return left


def end_builders(pids) -> int:
    """End the Graphics Builder processes ``pids`` now (what they have not saved is lost).
    Returns how many ended."""
    import subprocess

    ended = 0
    for pid in pids:
        r = subprocess.run(
            ["taskkill", "/F", "/PID", str(int(pid))],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        ended += (r.stdout or "").count("SUCCESS")
    return ended


def builders_running(pids=None) -> list:
    """The running Graphics Builder PIDs (of ``pids`` when given)."""
    now = _ctdraw_pids()
    return [p for p in now if pids is None or p in set(pids)]


def studio_running() -> bool:
    try:
        from .winui import pids_of

        return bool(pids_of(STUDIO_IMAGE))
    except Exception:  # noqa: BLE001
        return False


def ensure_studio(timeout: float = 120.0) -> bool:
    """Start Plant SCADA Studio if needed and wait until it's ready. The Graphics Builder needs it."""
    import os
    import subprocess
    import time

    if studio_running():
        return True
    if os.environ.get("CTG_NO_STUDIO") == "1":
        return False
    try:
        from .compiler import scada_bin

        exe = os.path.join(scada_bin(), "CitectIDE", STUDIO_IMAGE)
    except Exception:  # noqa: BLE001
        return False
    if not os.path.exists(exe):
        return False
    subprocess.Popen([exe], close_fds=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2.0)
        # ready = Studio up AND its Graphics Builder spawned
        if studio_running() and _ctdraw_pids():
            time.sleep(5.0)  # let it finish opening the project
            return True
    return studio_running()


class GraphicsBuilder:
    def __init__(self, visible: bool = False):
        self._want_visible = visible
        self._gb = None
        self._pumped = False
        # Dispatch ATTACHES to an already-running Graphics Builder (the user's
        # window / Studio's embedded builder). Only an instance WE started may
        # be hidden, Quit or killed: the user's holds their unsaved pages.
        self.spawned_pids: set = set()
        self.attached = False

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "GraphicsBuilder":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> "GraphicsBuilder":
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self._pumped = True
        ensure_studio()
        before = set(_ctdraw_pids())
        err = None
        for attempt in range(3):
            try:
                self._gb = win32com.client.Dispatch(CLSID)
                err = None
                break
            except Exception as e:  # noqa: BLE001 -- Studio still coming up
                err = e
                import time

                time.sleep(3.0 * (attempt + 1))
                ensure_studio()
        if err is not None:
            raise GraphicsBuilderError(
                "could not start GraphicsBuilder COM server (is Plant SCADA Studio running?): %s" % err
            ) from err
        self._wait_ready()
        self.spawned_pids = set(_ctdraw_pids()) - before
        self.attached = bool(before) and not self.spawned_pids
        if not self.attached:
            with contextlib.suppress(Exception):
                self._gb.Visible = bool(self._want_visible)
        return self

    def _wait_ready(self, timeout: float = 20.0) -> None:
        """Wait for the CtDraw32 COM server to finish starting."""
        import time
        import pythoncom

        deadline = time.time() + timeout
        while time.time() < deadline:
            pythoncom.PumpWaitingMessages()
            try:
                _ = self._gb.Visible  # noqa: F841 -- liveness probe
                break
            except Exception:  # noqa: BLE001 -- still booting
                time.sleep(0.1)
        # Visible answers before the document subsystem is ready; keep pumping
        # for a settle window.
        settle = time.time() + 3.0
        while time.time() < settle:
            pythoncom.PumpWaitingMessages()
            time.sleep(0.05)

    def close(self) -> None:
        if self.attached:
            # never Quit / release into / kill the user's builder: park the
            # proxy (a cross-process Release can crash an attached builder)
            if self._gb is not None:
                _PARKED.append(self._gb)
            self._gb = None
            return
        if self._gb is not None:
            with contextlib.suppress(Exception):
                self._gb.Quit()
            self._gb = None
        if self._pumped:
            with contextlib.suppress(Exception):
                import pythoncom

                pythoncom.CoUninitialize()
            self._pumped = False
        self.reap(self.spawned_pids)

    @staticmethod
    def reap(pids=None) -> int:
        """Kill the CtDraw32.exe processes in ``pids`` (the ones a helper
        spawned); with ``pids=None`` every CtDraw32 EXCEPT the one Plant SCADA
        Studio owns (for the DEV tools: that one is the user's, with their
        unsaved pages). Returns count killed."""
        killed = 0
        try:
            import subprocess

            if pids is None:
                from .winui import parent_pids, pids_of

                studio = set(pids_of(STUDIO_IMAGE))
                pids = [p for p, parent in parent_pids("CtDraw32.exe").items() if parent not in studio]
            cmds = [["taskkill", "/F", "/PID", str(p)] for p in pids]
            for cmd in cmds:
                r = subprocess.run(cmd, capture_output=True, text=True)
                killed += (r.stdout or "").count("SUCCESS")
        except Exception:  # noqa: BLE001
            pass
        return killed

    # -- raw access --------------------------------------------------------
    @property
    def com(self):
        if self._gb is None:
            raise GraphicsBuilderError("backend not open")
        return self._gb

    def __getattr__(self, item):
        # proxy unknown attributes to the COM object
        if item.startswith("_"):
            raise AttributeError(item)
        gb = self.__dict__.get("_gb")
        if gb is None:
            raise GraphicsBuilderError("backend not open (accessing %r)" % item)
        return getattr(gb, item)

    # -- high-level helpers ------------------------------------------------
    def select_project(self, name: str) -> None:
        """ProjectSelect is void (success == no exception); verify through
        ProjectSelected."""
        self.com.ProjectSelect(name)
        with contextlib.suppress(Exception):
            got = str(self.com.ProjectSelected())
            if got and got.lower() != name.lower():
                raise GraphicsBuilderError("ProjectSelect(%r) left %r selected" % (name, got))

    def compile_project(self, project: str | None = None) -> bool:
        """Compile the selected project. ProjectCompile is void -> success == no exception."""
        if project:
            self.select_project(project)
        self.com.ProjectCompile()
        return True

    # -- page create / duplicate (engine handles project registration) ----
    def open_page(self, project: str, element: str) -> bool:
        with contextlib.suppress(Exception):
            self.com.PageOpen(project, element)
        with contextlib.suppress(Exception):
            return element.lower() in str(self.com.PageName()).lower()
        return False

    def save_page_as(self, project: str, element: str) -> None:
        self.com.PageSaveAs(project, element)

    def delete_page(self, project: str, element: str) -> None:
        with contextlib.suppress(Exception):
            self.com.PageDelete(project, element, True)

    def duplicate_page(self, project: str, src_element: str, new_element: str) -> bool:
        """Create a new *registered* page by copying an existing one (PageSaveAs)."""
        self.select_project(project)
        if not self.open_page(project, src_element):
            raise GraphicsBuilderError("could not open source page %r" % src_element)
        self.save_page_as(project, new_element)
        with contextlib.suppress(Exception):
            self.com.PageClose()
        return True


def smoke() -> dict:
    """Quick liveness check used by tests/CI."""
    info = {}
    with GraphicsBuilder(visible=False) as gb:
        info["created"] = True
        with contextlib.suppress(Exception):
            info["visible"] = bool(gb.com.Visible)
    info["reaped_after"] = GraphicsBuilder.reap()
    return info


if __name__ == "__main__":
    print(smoke())


def _builder_window() -> bool:
    """Is there a top-level 'ctDraw' window (a Graphics Builder frame) on
    this desktop? A cheap check: the page-open / save checks ask it first and
    enumerate nothing when there is none. True when it cannot be told (not
    Windows)."""
    try:
        import ctypes

        find = ctypes.windll.user32.FindWindowW
    except ImportError, AttributeError, OSError:
        return True
    try:
        return bool(find("ctDraw", None))
    except Exception:  # noqa: BLE001
        return True


def open_pages() -> list:
    """(project, page) of every page open in a running Graphics Builder."""
    out = []
    for title in open_windows():
        proj, sep, page = title.partition(" - ")
        if sep:
            out.append((proj.strip(), page.strip()))
    return out


def open_windows() -> list:
    """The titles of the pages and library elements open in a running Graphics Builder
    ('Project - Page', or the element's)."""
    out = []
    if not _builder_window():
        return out  # no Graphics Builder frame: nothing is open there
    try:
        import win32gui
        import win32process
    except Exception:  # noqa: BLE001
        return out
    pids = set(_ctdraw_pids())
    if not pids:
        return out

    def top(h, _):
        try:
            _t, pid = win32process.GetWindowThreadProcessId(h)
            if pid in pids and win32gui.GetClassName(h) == "ctDraw":
                win32gui.EnumChildWindows(h, child, None)
        except Exception:  # noqa: BLE001
            pass
        return True

    def child(h, _):
        try:
            if win32gui.GetClassName(h) == "ctDraw.canvas":
                out.append(win32gui.GetWindowText(h).strip())
        except Exception:  # noqa: BLE001
            pass
        return True

    win32gui.EnumWindows(top, None)
    return out


def page_open_in_builder(path: str, opened: Optional[list] = None) -> bool:
    """Is the page file ``path`` (its project folder + page name) open in a
    running Graphics Builder? ``opened`` = an ``open_pages()`` result to test
    against (default: ask now). The editor asks BEFORE it saves such a page:
    the Builder keeps its own copy and its next save would overwrite ours."""
    proj = os.path.basename(os.path.dirname(os.path.abspath(path))).lower()
    page = os.path.splitext(os.path.basename(path))[0].lower()
    if opened is None:
        opened = open_pages()
    return any(str(p).strip().lower() == proj and str(n).strip().lower() == page for p, n in opened)
