"""Headless open / edit / save / close smoke test (``ctgedit --smoke-test``)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from typing import List, Optional

MOVE = 8


class SmokeError(Exception):
    pass


def _patch_dialogs(seen: list) -> None:
    """Record every message box / dialog instead of waiting for a click."""
    from PySide6 import QtWidgets

    M = QtWidgets.QMessageBox

    def _static(kind, answer):
        def f(parent, title, text, *a, **k):
            seen.append("%s: %s: %s" % (kind, title, text))
            return answer

        return staticmethod(f)

    M.warning = _static("warning", M.Ok)
    M.critical = _static("critical", M.Ok)
    M.information = _static("information", M.Ok)
    M.question = _static("question", M.Cancel)
    M.about = _static("about", None)

    def _exec(self, *a, **k):
        text = self.text() if isinstance(self, M) else ""
        seen.append("dialog: %s: %s" % (self.windowTitle() or type(self).__name__, text))
        return 0

    QtWidgets.QDialog.exec = _exec
    M.exec = _exec


def _not_blank(scene) -> int:
    """Pixels that differ from the page background in a small render of the
    PAGE (the page as printed: the scene's rect is the whole work area, whose
    surround would make an empty page look drawn)."""
    from PySide6 import QtCore, QtGui

    page_rect = getattr(scene, "page_rect", None)
    rect = page_rect() if page_rect is not None else scene.sceneRect()
    if rect.isEmpty():
        return 0
    scale = 400.0 / max(rect.width(), rect.height())
    img = QtGui.QImage(
        max(1, int(rect.width() * scale)), max(1, int(rect.height() * scale)), QtGui.QImage.Format_ARGB32
    )
    img.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(img)
    export = getattr(scene, "export_only", None)
    if export is not None:
        with export(None):  # the page and its objects only
            scene.render(p, QtCore.QRectF(img.rect()), rect)
    else:
        scene.render(p, QtCore.QRectF(img.rect()), rect)
    p.end()
    bg = QtGui.QColor(getattr(scene, "_page_bg", QtGui.QColor(img.pixel(0, 0)))).rgba()
    step = 3
    return sum(
        1 for y in range(0, img.height(), step) for x in range(0, img.width(), step) if img.pixel(x, y) != bg
    )


def _pick_root(win) -> Optional[int]:
    """The last top-level unit the user could move (not the template, not
    locked, with an extent: a drawn thing, not a page-wide keyboard command)
    -- a real edit, like dragging it on the canvas."""
    objs = win.doc.page.objects
    for r in reversed(win.doc.page.roots()):
        g = objs[r].geometry
        if g.extent_x > 0 and g.extent_y > 0 and not win._unit_locked(r) and not win._is_template_obj(r):
            return r
    return None


def _smoke_page(win, src: str, workdir: str, seen: list) -> dict:
    from .editing import EditableDocument

    res = {"page": src, "ok": False, "error": "", "dialogs": [], "ms": {}}
    before_dialogs = len(seen)
    t = time.perf_counter()

    def lap(name):
        nonlocal t
        now = time.perf_counter()
        res["ms"][name] = int((now - t) * 1000)
        t = now

    try:
        if not os.path.isfile(src):
            raise SmokeError("the page does not exist")
        dst = os.path.join(workdir, os.path.basename(src))
        shutil.copy2(src, dst)
        prev = os.environ.get("CTG_LIB_PATH")
        lib = os.path.dirname(os.path.abspath(src))
        os.environ["CTG_LIB_PATH"] = lib + (os.pathsep + prev if prev else "")
        try:
            failed = win.open_files([dst])
            if failed:
                raise SmokeError("open failed: %s" % failed[0][1])
            doc = win.doc
            n = len(doc.page.objects)
            res["objects"] = n
            res["scene_items"] = len(win.scene.items())
            if not n or not res["scene_items"]:
                raise SmokeError("the page opened with no objects")
            lap("open")
            res["drawn_pixels"] = _not_blank(win.scene)
            if res["drawn_pixels"] < 20:
                raise SmokeError("the page rendered blank")
            lap("render")
            root = _pick_root(win)
            if root is None:
                raise SmokeError("no movable object on the page")
            g = doc.page.objects[root].geometry
            where = (g.x, g.y)
            doc.move_objects([root], MOVE, MOVE)
            win._refresh()
            if not doc.dirty:
                raise SmokeError("the move did not mark the page as edited")
            lap("edit")
            if not win.save():
                raise SmokeError("File > Save failed")
            lap("save")
            win.close_current_document()
            if win.doc is not None:
                raise SmokeError("the page did not close")
            lap("close")
            again = EditableDocument.open(dst)
            if len(again.page.objects) != n:
                raise SmokeError("the saved page has %d records, expected %d" % (len(again.page.objects), n))
            g2 = again.page.objects[root].geometry
            if (g2.x, g2.y) != (where[0] + MOVE, where[1] + MOVE):
                raise SmokeError(
                    "the moved object is at %s after saving, expected %s"
                    % ((g2.x, g2.y), (where[0] + MOVE, where[1] + MOVE))
                )
            lap("reopen")
        finally:
            if prev is None:
                os.environ.pop("CTG_LIB_PATH", None)
            else:
                os.environ["CTG_LIB_PATH"] = prev
        res["dialogs"] = seen[before_dialogs:]
        if res["dialogs"]:
            raise SmokeError("an unexpected message appeared: %s" % res["dialogs"][0])
        res["ok"] = True
    except SmokeError as e:
        res["error"] = str(e)
    except Exception as e:  # noqa: BLE001 -- reported, the run goes on
        res["error"] = "%s: %s" % (type(e).__name__, e)
        res["traceback"] = traceback.format_exc()
    res["dialogs"] = seen[before_dialogs:]
    # leave the window clean for the next page
    try:
        while win.doc is not None:
            win.doc.dirty = False
            win.close_current_document()
    except Exception:  # noqa: BLE001
        pass
    return res


def run(pages: List[str], report: Optional[str] = None) -> int:
    """Smoke-test ``pages`` in one headless editor; 0 when all passed."""
    tmp = tempfile.mkdtemp(prefix="ctgedit-smoke-")
    # never reach a running runtime, never wait on a background .ctF check,
    # never read or write the user's settings
    os.environ["CTG_NO_RUNTIME"] = "1"
    os.environ["CTG_NO_CTF_PRECHECK"] = "1"
    os.environ.setdefault("CTG_SETTINGS_INI", os.path.join(tmp, "settings.ini"))
    # (ctgedit's own files, the earlier versions of the pages it saves among them, go to
    # the smoke test's folder: never the user's history, which shares one size cap)
    os.environ.setdefault("CTG_DATA_DIR", os.path.join(tmp, "data"))
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["ctgedit"])
    seen: list = []
    _patch_dialogs(seen)
    from . import buildinfo

    # "frozen": the release gate checks that a --frozen run really ran the
    # bundled code (not a source tree found through the environment)
    out = {
        "version": buildinfo.version_string(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "pages": [],
    }
    try:
        from .ui.main_window import MainWindow

        win = MainWindow()
        for i, src in enumerate(pages):
            work = os.path.join(tmp, str(i))
            os.makedirs(work, exist_ok=True)
            out["pages"].append(_smoke_page(win, src, work, seen))
        win.close()
        if win in MainWindow._instances:
            MainWindow._instances.remove(win)
        app.processEvents()
    except Exception as e:  # noqa: BLE001
        out["error"] = "%s: %s" % (type(e).__name__, e)
        out["traceback"] = traceback.format_exc()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out["ok"] = bool(out["pages"]) and "error" not in out and all(p["ok"] for p in out["pages"])
    text = json.dumps(out, indent=1)
    if report:
        with open(report, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    return 0 if out["ok"] else 1
