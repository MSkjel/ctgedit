"""Edit a page with ctgedit and check the real CtDraw shows the edit.

python tools/live/ctdraw_live_rt.py edit
python tools/live/ctdraw_live_rt.py restore
"""

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import win32con
import win32gui

from tools.ctdraw_inspect import main_frame, shoot, _bmp_to_png, OUT
from ctgedit.editing import EditableDocument

LIVE = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example\!PUMP_A_ACC.ctg"
BAK = LIVE + ".ctgedit_bak"
MRU_ID = 244
SENTINEL = "CTGEDIT_LIVE_RT"
SIDE = ("ctf", "rdb")


def _sidecar_paths():
    _stem = os.path.splitext(LIVE)[0]
    out = []
    d = os.path.dirname(LIVE)
    base = os.path.splitext(os.path.basename(LIVE))[0].lower()
    for f in os.listdir(d):
        n, e = os.path.splitext(f)
        if n.lower() == base and e.lower().lstrip(".") in SIDE:
            out.append(os.path.join(d, f))
    return out


def do_edit():
    if not os.path.exists(BAK):
        shutil.copy2(LIVE, BAK)
        print("backup ->", BAK)
    else:
        print("backup already exists ->", BAK)
    # also stash sidecars so restore is complete
    for sc in _sidecar_paths():
        b = sc + ".ctgedit_bak"
        if not os.path.exists(b):
            shutil.copy2(sc, b)
            print("backup sidecar ->", b)

    doc = EditableDocument.open(LIVE)
    n0 = len(doc.page.objects)
    # a red banner and label on top, in the page's content band
    doc.create_object("rectangle", rect=(60, 35, 445, 95), colour=0x00FF0000)
    idx = doc.create_object("text", rect=(74, 52, 432, 86), text=SENTINEL, colour=0x00FFFF00)
    doc.save()
    print("edited : %d -> %d objects, sentinel at idx %d, saved to LIVE" % (n0, len(doc.page.objects), idx))

    frame = main_frame()
    if frame is None:
        print("!! CtDraw not running -- cannot drive the live editor")
        return 1
    hwnd = frame[0]
    print("CtDraw frame: %#x  %r" % (hwnd, frame[1]))
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.4)
    # open !PUMP_A_ACC via its MRU command (fresh disk read -- it is not open)
    win32gui.PostMessage(hwnd, win32con.WM_COMMAND, MRU_ID, 0)
    time.sleep(2.0)
    # screenshot the frame: the active MDI child renders into it
    bmp = os.path.join(OUT, "live_rt_ctdraw.bmp")
    shoot(hwnd, bmp)
    png = _bmp_to_png(bmp)
    print("screenshot ->", os.path.abspath(png))
    print(
        "\nLook for the RED banner with yellow text '%s' in the CtDraw"
        " window.\nRun `restore` when done." % SENTINEL
    )
    return 0


def do_restore():
    if os.path.exists(BAK):
        shutil.copy2(BAK, LIVE)
        os.remove(BAK)
        print("restored original ->", LIVE)
    else:
        print("no backup found -- nothing to restore")
    for b in [p + ".ctgedit_bak" for p in _sidecar_paths()]:
        pass
    # restore stashed sidecars (match *.ctgedit_bak next to the page)
    d = os.path.dirname(LIVE)
    for f in os.listdir(d):
        if f.endswith(".ctgedit_bak") and f != os.path.basename(BAK):
            src = os.path.join(d, f)
            dst = src[: -len(".ctgedit_bak")]
            shutil.copy2(src, dst)
            os.remove(src)
            print("restored sidecar ->", dst)
    # close the !PUMP_A_ACC window in CtDraw (no edits in CtDraw, so no save prompt)
    frame = main_frame()
    if frame is not None:
        hwnd = frame[0]
        try:
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            import pywinauto

            app = pywinauto.Application(backend="win32").connect(handle=hwnd)
            w = app.window(handle=hwnd)
            w.set_focus()
            w.type_keys("^{F4}", pause=0.1)  # Ctrl+F4 closes active MDI child
            print("closed the !PUMP_A_ACC window in CtDraw")
        except Exception as e:  # noqa: BLE001
            print("(could not auto-close CtDraw child: %s)" % e)
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "edit"
    raise SystemExit(do_restore() if mode == "restore" else do_edit())
