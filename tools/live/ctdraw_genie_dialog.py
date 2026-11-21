"""Read the live Graphics Builder's genie parameter dialog.

tools/live/ctdraw_genie_dialog.py <project> <page> <index>:<x>,<y> ...
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import livepaths  # noqa: E402

livepaths.setup()  # (ctdraw_inspect is in tools/, not tools/live)
REPO = livepaths.REPO
import pythoncom  # noqa: E402
import win32com.client  # noqa: E402
import win32con  # noqa: E402
import win32gui  # noqa: E402

import ctdraw_inspect as ci  # noqa: E402
import ctedit_compare as cc  # noqa: E402
from ctgedit.format import geometry as G  # noqa: E402
from ctgedit.format.page import parse_ctg  # noqa: E402


proj, page = sys.argv[1], sys.argv[2]
idxs, PTS = [], {}
for a in sys.argv[3:]:
    k, _, pt = a.partition(":")
    idxs.append(int(k))
    PTS[int(k)] = tuple(int(v) for v in pt.split(",")) if pt else None
pobjs = parse_ctg(os.path.join(cc.ROOT, proj, page + ".ctg")).page.objects


def controls(dlg):
    out = []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return True
        cls = win32gui.GetClassName(h)
        txt = win32gui.GetWindowText(h)
        if cls in ("Edit", "ComboBox"):
            buf = win32gui.PyMakeBuffer(4096)
            n = win32gui.SendMessage(h, win32con.WM_GETTEXT, 2048, buf)
            txt = buf[: n * 2].tobytes().decode("utf-16-le", "replace") if n else ""
        l, t, _r, _b = win32gui.GetWindowRect(h)
        out.append((t, l, cls, txt))
        return True

    win32gui.EnumChildWindows(dlg, cb, None)
    out.sort()
    return out


pythoncom.CoInitialize()
c = win32com.client.GetActiveObject(cc.CLSID)
c.ProjectSelect(proj)
time.sleep(0.4)
try:
    c.PageClose()
except Exception:  # noqa: BLE001
    pass
c.PageOpen(proj, page)
time.sleep(1.5)
frame, _t, _c, pid = ci.main_frame()
res = {}
for i in idxs:
    an = G.get_handle(pobjs[i]) + 1
    try:
        sel = c.PageSelectObject(an)
    except Exception as e:  # noqa: BLE001
        res[i] = {"error": "select %s" % e}
        continue
    time.sleep(0.3)
    try:
        pos0 = (c.AttributeX(), c.AttributeY())
    except Exception:  # noqa: BLE001
        pos0 = None
    px, py = PTS[i]
    h = cc._canvas_hwnd(frame)
    before = set(ci.top_windows(pid))
    lp = (py & 0xFFFF) << 16 | (px & 0xFFFF)
    # the object is already selected: send ONLY the double-click (no button
    # press first, so no drag can start from wherever the real cursor is)
    win32gui.PostMessage(h, win32con.WM_LBUTTONDBLCLK, 1, lp)
    time.sleep(0.03)
    win32gui.PostMessage(h, win32con.WM_LBUTTONUP, 0, lp)
    found = ci.wait_new_window(pid, before, timeout=4.0)
    if not found:
        res[i] = {"error": "no dialog", "an": an, "select": str(sel)}
        continue
    dh, title, _cls = found
    time.sleep(0.4)
    ctl = controls(dh)
    labels = [t for _y, _x, k, t in ctl if k == "Static" and t]
    edits = [t for _y, _x, k, t in ctl if k in ("Edit", "ComboBox")]
    win32gui.PostMessage(dh, win32con.WM_COMMAND, win32con.IDCANCEL, 0)
    time.sleep(0.5)
    if win32gui.IsWindow(dh) and win32gui.IsWindowVisible(dh):
        win32gui.PostMessage(dh, win32con.WM_CLOSE, 0, 0)
        time.sleep(0.3)
    try:
        c.PageSelectObject(an)
        pos1 = (c.AttributeX(), c.AttributeY())
    except Exception:  # noqa: BLE001
        pos1 = None
    res[i] = {
        "an": an,
        "title": title,
        "rows": list(zip(labels, edits)),
        "moved": pos0 != pos1,
        "pos": [pos0, pos1],
    }
try:
    c.PageClose()
except Exception:  # noqa: BLE001
    pass
print(json.dumps(res, indent=1, ensure_ascii=False))
