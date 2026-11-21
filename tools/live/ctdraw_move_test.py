"""Move a genie with ctgedit and check CtDraw shows it moved.

python tools/live/ctdraw_move_test.py move
python tools/live/ctdraw_move_test.py restore
"""

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from tools.ctdraw_inspect import (
    main_frame,
    shoot,
    _bmp_to_png,
    OUT,
    ensure_front,
    close_all_children,
    open_page,
)
from ctgedit.editing import EditableDocument

LIVE = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example\Mixer.ctg"
BAK = LIVE + ".ctgedit_movebak"
AGITATOR_ROOT = 1005  # energydemo.agitator_mixer (central mixer agitator)
DX, DY = 0, 240


def _shot(tag, hwnd):
    ensure_front(hwnd)
    bmp = os.path.join(OUT, "move_%s.bmp" % tag)
    shoot(hwnd, bmp)
    p = os.path.abspath(_bmp_to_png(bmp))
    print("  %-7s -> %s" % (tag, p))
    return p


def _backup():
    if not os.path.exists(BAK):
        shutil.copy2(LIVE, BAK)
    d = os.path.dirname(LIVE)
    for f in os.listdir(d):
        n, e = os.path.splitext(f)
        if n.lower() == "mixer" and e.lower().lstrip(".") in ("ctf", "rdb"):
            b = os.path.join(d, f) + ".movebak"
            if not os.path.exists(b):
                shutil.copy2(os.path.join(d, f), b)


def do_move():
    frame = main_frame()
    if frame is None:
        print("CtDraw not running")
        return 1
    hwnd, _t, _c, pid = frame

    print("clean slate + open Mixer (original)...")
    close_all_children(hwnd, pid)
    if not open_page(hwnd, "Mixer"):
        print("!! could not open Mixer")
        return 1
    _shot("before", hwnd)

    print("editing with ctgedit: move agitator genie...")
    _backup()
    doc = EditableDocument.open(LIVE)
    sub = doc.subtree(AGITATOR_ROOT)
    g0 = doc.page.objects[AGITATOR_ROOT].geometry
    doc.move_objects(sub, DX, DY)
    g1 = doc.page.objects[AGITATOR_ROOT].geometry
    print(
        "  agitator root %d: (%d,%d) -> (%d,%d), %d records moved"
        % (AGITATOR_ROOT, g0.x, g0.y, g1.x, g1.y, len(sub))
    )
    doc.save()
    print("  saved to LIVE Mixer.ctg")

    print("reload Mixer in CtDraw (close + reopen, fresh disk read)...")
    close_all_children(hwnd, pid)
    if not open_page(hwnd, "Mixer"):
        print("!! could not reopen Mixer")
        return 1
    time.sleep(0.6)
    _shot("after", hwnd)
    print(
        "\nCompare move_before.png vs move_after.png: the central agitator"
        " dropped %dpx in the REAL CtDraw. Run `restore` when done." % DY
    )
    return 0


def do_restore():
    if os.path.exists(BAK):
        shutil.copy2(BAK, LIVE)
        os.remove(BAK)
        print("restored original Mixer.ctg")
    d = os.path.dirname(LIVE)
    for f in os.listdir(d):
        if f.endswith(".movebak"):
            src = os.path.join(d, f)
            shutil.copy2(src, src[: -len(".movebak")])
            os.remove(src)
            print("restored ->", os.path.basename(src[: -len(".movebak")]))
    frame = main_frame()
    if frame is not None:
        hwnd, _t, _c, pid = frame
        close_all_children(hwnd, pid)
        open_page(hwnd, "Mixer")
        print("CtDraw now shows the original Mixer")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "move"
    raise SystemExit(do_restore() if mode == "restore" else do_move())
