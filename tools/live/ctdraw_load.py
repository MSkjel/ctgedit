"""Load a page in the live Graphics Builder and screenshot it.

ctdraw_load.py <project> <page> [--out <dir>]
"""

import os
import sys
import time

import win32gui

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import livepaths  # noqa: E402

livepaths.setup()
import ctdraw_inspect as ci  # noqa: E402
from ctgedit.backend.graphics_builder import CLSID  # noqa: E402

PROJECT = sys.argv[1]
PAGE = sys.argv[2]
OUT = livepaths.out_dir("ctdraw_load")

import pythoncom  # noqa: E402
import win32com.client  # noqa: E402

pythoncom.CoInitialize()
try:
    c = win32com.client.GetActiveObject(CLSID)
    try:
        c.ProjectSelect(PROJECT)
    except Exception as e:  # noqa: BLE001
        print("ProjectSelect:", e)
    try:
        c.PageClose()
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.5)
    try:
        c.PageOpen(PROJECT, PAGE)
        print("PageName ->", c.PageName())
    except Exception as e:  # noqa: BLE001
        print("PageOpen error:", e)
    time.sleep(1.5)
    c = None
finally:
    pythoncom.CoUninitialize()

frame = ci.main_frame()
hwnd = frame[0]
print("frame:", win32gui.GetWindowText(hwnd))
ci.ensure_front(hwnd)
time.sleep(0.5)
try:
    import pywinauto

    w = pywinauto.Application(backend="win32").connect(handle=hwnd).window(handle=hwnd)
    w.set_focus()
    w.type_keys("^{F10}", pause=0.2)
except Exception:  # noqa: BLE001
    pass
time.sleep(1.0)
bmp = os.path.join(OUT, "ctdraw_%s.bmp" % PAGE.replace("!", ""))
ci.shoot(hwnd, bmp)
try:
    from PIL import Image

    png = os.path.splitext(bmp)[0] + ".png"
    Image.open(bmp).save(png)
    os.remove(bmp)
    print("screenshot ->", png)
except Exception as e:  # noqa: BLE001
    print("convert failed:", e, "bmp at", bmp)
