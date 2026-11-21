"""Open the click-only dialogs in the live CtDraw with the mouse."""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import win32api
import win32con
import win32gui

from tools.ctdraw_inspect import (
    main_frame,
    shoot,
    _bmp_to_png,
    OUT,
    top_windows,
    wait_new_window,
    close_dialog,
    click_screen,
)

frame = main_frame()
if frame is None:
    print("CtDraw not running")
    raise SystemExit(1)
hwnd, title, cls, pid = frame
print("frame %#x pid=%d %r" % (hwnd, pid, title))


def capture(tag, opener, *, is_doubleclick=False):
    """Run `opener` (which triggers a dialog), capture the new dialog window."""
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    before = set(top_windows(pid))
    opener()
    found = wait_new_window(pid, before, timeout=4.0)
    if not found:
        # no dialog -> maybe a mode change (composite drill-in); snapshot frame
        bmp = os.path.join(OUT, "dlg_%s_NONE.bmp" % tag)
        shoot(hwnd, bmp)
        print("%-14s NO DIALOG -> %s" % (tag, os.path.basename(_bmp_to_png(bmp))))
        win32api.keybd_event(0x1B, 0, 0, 0)
        win32api.keybd_event(0x1B, 0, 2, 0)
        return None
    dh, dtitle, dcls = found
    time.sleep(0.4)  # let it fully paint
    bmp = os.path.join(OUT, "dlg_%s.bmp" % tag)
    shoot(dh, bmp)
    png = _bmp_to_png(bmp)
    print("%-14s -> dialog %r (class %s) -> %s" % (tag, dtitle, dcls, os.path.basename(png)))
    close_dialog(dh)
    time.sleep(0.3)
    return dtitle


# 1) PAGE PROPERTIES via File>Properties (command id 111)
capture("page_props", lambda: win32gui.PostMessage(hwnd, win32con.WM_COMMAND, 111, 0))

# 2) GENIE: double-click the big 'M' pump genie (screen ~252,620)
capture("genie_pump", lambda: click_screen(252, 620, double=True), is_doubleclick=True)

# 3) a second genie/object: the lower pump (screen ~252,702)
capture("genie_pump2", lambda: click_screen(252, 702, double=True), is_doubleclick=True)

# 4) TEXT/label: double-click a '@(Tank)' style label (screen ~95,302)
capture("text_label", lambda: click_screen(95, 302, double=True), is_doubleclick=True)

# make sure we leave no dialog/mode lingering
win32gui.SetForegroundWindow(hwnd)
win32api.keybd_event(0x1B, 0, 0, 0)
win32api.keybd_event(0x1B, 0, 2, 0)
print("done")
