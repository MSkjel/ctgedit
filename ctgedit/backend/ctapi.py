"""Run Cicode in a running Plant SCADA runtime through CtAPI."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from typing import List, Optional

from .compiler import scada_bin

_SRC = r"""
using System;
using System.Runtime.InteropServices;
using System.Text;

class CtApiCmd {
    [DllImport("CtApi.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern IntPtr ctOpen(string sComputer, string sUser, string sPassword, uint nMode);
    [DllImport("CtApi.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern uint ctCicode(IntPtr h, string sCmd, uint hWin, uint nMode, StringBuilder sResult,
                                uint dwLength, IntPtr pOverlapped);
    [DllImport("CtApi.dll", SetLastError = true)]
    static extern bool ctClose(IntPtr h);

    static int Main(string[] args) {
        IntPtr h = ctOpen(null, null, null, 0);
        if (h == IntPtr.Zero) { Console.WriteLine("ERR open " + Marshal.GetLastWin32Error()); return 2; }
        foreach (string cmd in args) {
            StringBuilder sb = new StringBuilder(4096);
            // '@<n>:<cmd>' runs <cmd> as a task of display window n (hWin)
            uint win = 0; string c = cmd;
            if (c.StartsWith("@") && c.IndexOf(':') > 1) {
                int k = c.IndexOf(':');
                win = uint.Parse(c.Substring(1, k - 1)); c = c.Substring(k + 1);
            }
            uint ok = ctCicode(h, c, win, 0, sb, 4096, IntPtr.Zero);
            Console.WriteLine(ok != 0 ? sb.ToString() : "ERR " + Marshal.GetLastWin32Error());
        }
        ctClose(h);
        return 0;
    }
}
"""
CSC = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"


def _bin64() -> str:
    return os.path.join(scada_bin(), "Bin (x64)")


def helper_path() -> Optional[str]:
    """The CtAPI helper exe, built on first use (None when this machine has
    no 64-bit CtApi.dll or no .NET Framework compiler)."""
    src_dir = _bin64()
    dll = os.path.join(src_dir, "CtApi.dll")
    if not os.path.exists(dll) or not os.path.exists(CSC):
        return None
    st = os.stat(dll)
    tag = hashlib.md5(("%s|%d|%d|%s" % (dll, st.st_size, st.st_mtime_ns, _SRC)).encode()).hexdigest()[:10]
    base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ctgedit", "ctapi-" + tag)
    exe = os.path.join(base, "ctapi_cmd.exe")
    if os.path.exists(exe):
        return exe
    os.makedirs(base, exist_ok=True)
    for name in os.listdir(src_dir):
        p = os.path.join(src_dir, name)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(base, name))
    cs = os.path.join(base, "ctapi_cmd.cs")
    with open(cs, "w", encoding="ascii") as fh:
        fh.write(_SRC)
    r = subprocess.run(
        [CSC, "/nologo", "/platform:x64", "/out:" + exe, cs], cwd=base, capture_output=True, text=True
    )
    return exe if r.returncode == 0 and os.path.exists(exe) else None


def cicode(*commands: str, timeout: float = 20.0) -> List[str]:
    """Run Cicode statements in the runtime; one result line each. ``@n:`` runs in window n."""
    exe = helper_path()
    if exe is None:
        raise RuntimeError("CtAPI is not available on this machine")
    r = subprocess.run(
        [exe, *commands],
        cwd=os.path.dirname(exe),
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = r.stdout.splitlines()
    if r.returncode != 0 or (out and out[0].startswith("ERR open")):
        raise RuntimeError("the runtime did not answer CtAPI (%s)" % (out[0] if out else r.returncode))
    return out


#: display window numbers probed for the page they show (Cicode numbers a
#: client's windows from 0; a freed number leaves a gap)
MAX_WINDOWS = 16


def window_pages(max_windows: int = MAX_WINDOWS) -> dict:
    """{window number: page name} for the running client's windows."""
    cmds = []
    for n in range(max_windows):
        cmds += ["@%d:WinNumber()" % n, "@%d:PageInfo(0)" % n]
    out = cicode(*cmds)
    pages = {}
    for n in range(max_windows):
        row = out[2 * n : 2 * n + 2]
        if len(row) < 2 or row[0].strip() != str(n):
            continue
        name = row[1].rstrip()
        if name.startswith(" ") and name.strip():
            name = "!" + name.lstrip()
        name = name.strip()
        if name and not name.startswith("ERR"):
            pages[n] = name
    return pages


def _captions() -> List[str]:
    """The captions of the runtime's visible client windows (the page TITLE
    when the page has one -- 'Home' for Home_HD1080 -- else its name)."""
    try:
        import win32gui
        import win32process
        from . import winui
    except Exception:  # noqa: BLE001
        return []
    pids = set(winui.pids_of("Citect32.exe")) | set(winui.pids_of("Citect.exe"))
    out = []

    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h) and win32gui.GetClassName(h) == "ctDSP":
                _t, pid = win32process.GetWindowThreadProcessId(h)
                if pid in pids:
                    out.append(win32gui.GetWindowText(h))
        except Exception:  # noqa: BLE001
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return out


def displayed_pages() -> List[str]:
    """Names of the pages the runtime's client windows show, asked from the
    runtime (a window's caption is the page's TITLE, not its name). Falls back
    to the captions without CtAPI."""
    if not _captions():
        return []
    try:
        return list(window_pages().values())
    except Exception:  # noqa: BLE001 -- no CtAPI: the captions
        return _captions()


def redisplay(page: str) -> bool:
    """Re-display ``page`` in the runtime window that shows it (the client
    then re-reads the page's files). False when no window shows it."""
    if not _captions():
        return False
    want = page.lower()
    for n, name in sorted(window_pages().items()):
        if name.lower() == want:
            # a task of window n (a WinSelect would not carry over: cicode)
            res = cicode('@%d:PageDisplay("%s")' % (n, name.replace('"', "")))
            return not any(r.startswith("ERR") for r in res)
    return False
