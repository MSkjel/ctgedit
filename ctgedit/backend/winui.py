"""Win32 helpers for clicking through the dialogs of Plant SCADA's command-line tools."""

from __future__ import annotations

import ctypes
import subprocess
from typing import List, Optional

DIALOG_CLASS = "#32770"


def _mods():
    import win32con
    import win32gui
    import win32process

    return win32con, win32gui, win32process


def pids_of(image: str) -> List[int]:
    """PIDs of running processes with this image name: a Toolhelp process
    snapshot (the editor asks on every page open and save), tasklist
    only when the snapshot cannot be taken."""
    snap = _snapshot(image)
    if snap is not None:
        return list(snap)
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq %s" % image, "/FO", "CSV", "/NH"], capture_output=True, text=True
    ).stdout
    pids = []
    for line in out.splitlines():
        parts = [p.strip().strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == image.lower():
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def parent_pids(image: str) -> dict:
    """``{pid: parent pid}`` of the running processes with this image name
    (a Toolhelp process snapshot)."""
    return _snapshot(image) or {}


def _snapshot(image: str) -> Optional[dict]:
    """``{pid: parent pid}`` of the processes named ``image`` from a Toolhelp
    snapshot; None when no snapshot can be taken (not Windows)."""
    try:
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
    except ImportError, AttributeError, OSError:
        return None

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    out: dict = {}
    if not snap or snap == wintypes.HANDLE(-1).value:
        return None
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == image.lower():
                out[int(e.th32ProcessID)] = int(e.th32ParentProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def text(hwnd) -> str:
    """Window/control text via WM_GETTEXT (works across processes)."""
    win32con, _g, _p = _mods()
    user32 = ctypes.windll.user32
    try:
        n = user32.SendMessageW(hwnd, win32con.WM_GETTEXTLENGTH, 0, 0)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.SendMessageW(hwnd, win32con.WM_GETTEXT, n + 1, buf)
        return buf.value
    except Exception:  # noqa: BLE001
        return ""


def windows_of(pid: int, cls: Optional[str] = DIALOG_CLASS) -> list:
    """Visible top-level windows of a process (dialogs by default)."""
    _c, win32gui, win32process = _mods()
    hs = []

    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return
            if cls and win32gui.GetClassName(h) != cls:
                return
            _t, p = win32process.GetWindowThreadProcessId(h)
            if p == pid:
                hs.append(h)
        except Exception:  # noqa: BLE001
            pass

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:  # noqa: BLE001
        pass
    return hs


def children(hwnd) -> List[dict]:
    """[{hwnd, cls, text, enabled, id}] of a window's controls."""
    _c, win32gui, _p = _mods()
    kids: List[dict] = []

    def cb(c, _):
        try:
            kids.append(
                {
                    "hwnd": c,
                    "cls": win32gui.GetClassName(c),
                    "text": text(c),
                    "enabled": bool(win32gui.IsWindowEnabled(c)),
                    "id": win32gui.GetDlgCtrlID(c),
                }
            )
        except Exception:  # noqa: BLE001
            pass

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception:  # noqa: BLE001
        pass
    return kids


def is_message_box(kids: List[dict]) -> bool:
    """A MessageBox: only Static/Button children with a standard button id
    (OK=1, Cancel=2, Yes=6, No=7)."""
    return (
        bool(kids)
        and all(k["cls"] in ("Static", "Button") for k in kids)
        and any(k["cls"] == "Button" and k["id"] in (1, 2, 6, 7) for k in kids)
    )


def message_text(kids: List[dict]) -> str:
    return " ".join(k["text"] for k in kids if k["cls"] == "Static" and k["text"].strip()).strip()


def find_button(kids: List[dict], label: str):
    for k in kids:
        if k["cls"] == "Button" and k["text"].replace("&", "").strip().upper() == label.upper():
            return k["hwnd"]
    return None


def click(hwnd_button) -> None:
    win32con, win32gui, _p = _mods()
    try:
        win32gui.PostMessage(hwnd_button, win32con.BM_CLICK, 0, 0)
    except Exception:  # noqa: BLE001
        pass


def dismiss_message_box(hwnd, kids: List[dict], prefer: str = "Yes") -> str:
    """Answer a message box: OK when it has one, else the preferred Yes/No
    (CtCmp32 asks "Nothing to compile. Do you still want to compile anyway?"
    -- Yes compiles, which is what was asked for). Returns the button used."""
    win32con, win32gui, _p = _mods()
    for label in ("OK", prefer, "Yes", "No", "Cancel"):
        btn = find_button(kids, label)
        if btn:
            click(btn)
            return label
    try:
        win32gui.PostMessage(hwnd, win32con.WM_COMMAND, win32con.IDOK, 0)
    except Exception:  # noqa: BLE001
        pass
    return "IDOK"
