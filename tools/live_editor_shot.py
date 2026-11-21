"""Screenshot the real CtDraw canvas for a page, next to ours.

python tools/live_editor_shot.py <project> <page> [out_dir]
"""

from __future__ import annotations

import contextlib
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _frame_of(pids):
    import win32gui
    import win32process

    frames = []

    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return True
            _t, pid = win32process.GetWindowThreadProcessId(h)
            if pid in pids and win32gui.GetWindowText(h):
                frames.append(h)
        except Exception:  # noqa: BLE001
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return frames


def _canvas(frame):
    import win32gui

    res = []

    def cb(c, _):
        if win32gui.GetClassName(c) == "ctDraw.canvas" and win32gui.IsWindowVisible(c):
            res.append(c)
        return True

    win32gui.EnumChildWindows(frame, cb, None)
    return res[0] if res else None


def _shoot(hwnd, path):
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image

    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    hdc = win32gui.GetWindowDC(hwnd)
    mdc = win32ui.CreateDCFromHandle(hdc)
    sdc = mdc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mdc, w, h)
    sdc.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, sdc.GetSafeHdc(), 0)
    info = bmp.GetInfo()
    img = Image.frombuffer(
        "RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1
    )
    img.save(path)
    win32gui.DeleteObject(bmp.GetHandle())
    sdc.DeleteDC()
    mdc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hdc)
    return img.size


def shoot_editor(project: str, page: str, out: str) -> str:
    from ctgedit.backend.graphics_builder import GraphicsBuilder
    from ctgedit.backend.winui import pids_of

    path = os.path.join(out, "%s_ctdraw.png" % page.replace("!", ""))
    with GraphicsBuilder(visible=True) as gb:
        c = gb.com
        with contextlib.suppress(Exception):
            c.ProjectSelect(project)
        c.PageOpen(project, page)
        time.sleep(2.5)
        pids = set(pids_of("CtDraw32.exe"))
        frame = next(iter(_frame_of(pids)), None)
        cv = _canvas(frame) if frame else None
        if cv is None:
            raise RuntimeError("no ctDraw.canvas window found")
        import win32gui

        with contextlib.suppress(Exception):
            win32gui.ShowWindow(frame, 3)  # maximise
            win32gui.SetForegroundWindow(frame)
        time.sleep(1.0)
        size = _shoot(cv, path)
        with contextlib.suppress(Exception):
            c.PageClose()
    print("editor canvas", size, "->", path)
    return path


def render_ours(project_dir: str, page: str, out: str) -> str:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtCore, QtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    reader.set_label_mode("literal")
    pg = reader.read_page(os.path.join(project_dir, page + ".ctg"))
    sc = PageScene()
    sc.set_page(pg)
    w, h = pg.appearance.width or 1920, pg.appearance.height or 1080
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor("white"))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
    sc.render(p, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(0, 0, w, h))
    p.end()
    path = os.path.join(out, "%s_ours.png" % page.replace("!", ""))
    img.save(path)
    print("ours", (w, h), "->", path)
    return path


def main(argv):
    project, page = argv[1], argv[2]
    out = argv[3] if len(argv) > 3 else os.path.join(os.path.dirname(__file__), "_ctdraw", "live")
    os.makedirs(out, exist_ok=True)
    from ctgedit.io.citect_config import live_project_dir

    pdir = live_project_dir(project)
    shoot_editor(project, page, out)
    render_ours(pdir, page, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
