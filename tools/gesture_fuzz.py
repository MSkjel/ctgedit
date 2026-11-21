"""Random canvas gestures on copies of pages, then Undo back and compare.

python tools/gesture_fuzz.py [--out FILE] [--steps 300] [--seed 7] [--pages a,b]
"""

from __future__ import annotations

import argparse
import faulthandler
import os
import random
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sweep_guard as G  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.join(HERE, "_tests", "gesture_fuzz.txt"))
ap.add_argument("--root", default=os.path.dirname(HERE))
ap.add_argument("--steps", type=int, default=300)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--pages", default="Genies,Mixer,ExampleSA:Mixing_L3")
opt = ap.parse_args()
os.makedirs(os.path.dirname(os.path.abspath(opt.out)), exist_ok=True)
G.install(opt.out)
sys.path.insert(0, opt.root)

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

app = QtWidgets.QApplication([])
G.answer_qt(app)
from ctgedit.ui.style import theme  # noqa: E402
from ctgedit.ui.main_window import MainWindow  # noqa: E402

theme.apply_theme(app)
w = MainWindow()
w.resize(1400, 900)
w.show()
rng = random.Random(opt.seed)
info: list = []
M = QtCore.Qt
MODS = [
    M.NoModifier,
    M.ShiftModifier,
    M.ControlModifier,
    M.AltModifier,
    M.ShiftModifier | M.ControlModifier,
    M.ControlModifier | M.AltModifier,
]
KEYS = [
    M.Key_Left,
    M.Key_Right,
    M.Key_Up,
    M.Key_Down,
    M.Key_Delete,
    M.Key_Escape,
    M.Key_Tab,
    M.Key_D,
    M.Key_G,
    M.Key_Z,
    M.Key_Y,
    M.Key_A,
    M.Key_L,
    M.Key_H,
    M.Key_BracketRight,
]


def pump(n=2):
    for _ in range(n):
        app.processEvents()


def vp_of(pt):
    return w.view.mapFromScene(QtCore.QPointF(*pt))


def point_on(doc):
    objs, roots = doc.page.objects, doc.page.roots()
    if roots and rng.random() < 0.75:
        g = objs[rng.choice(roots)].geometry
        if g is not None:
            return (g.x + rng.uniform(0, max(1, g.extent_x)), g.y + rng.uniform(0, max(1, g.extent_y)))
    r = w.scene.sceneRect()
    return (rng.uniform(r.left(), r.right()), rng.uniform(r.top(), r.bottom()))


def drag(vp, a, b, mods, button=M.LeftButton):
    QTest.mousePress(vp, button, mods, a)
    steps = rng.randint(2, 6)
    for k in range(1, steps + 1):
        p = QtCore.QPoint(a.x() + (b.x() - a.x()) * k // steps, a.y() + (b.y() - a.y()) * k // steps)
        QtWidgets.QApplication.sendEvent(
            vp,
            QtGui.QMouseEvent(
                QtCore.QEvent.MouseMove,
                QtCore.QPointF(p),
                QtCore.QPointF(vp.mapToGlobal(p)),
                M.NoButton,
                button,
                mods,
            ),
        )
    QTest.mouseRelease(vp, button, mods, b)


def gesture(doc, k):
    faulthandler.dump_traceback_later(60, exit=False)  # (a gesture stuck 60 s: its stack)
    vp = w.view.viewport()
    kind = rng.choice(
        [
            "click",
            "click",
            "drag",
            "drag",
            "drag",
            "marquee",
            "handle",
            "dbl",
            "wheel",
            "key",
            "key",
            "key",
            "right",
        ]
    )
    mods = rng.choice(MODS)
    a = vp_of(point_on(doc))
    G.state["where"] = "step %d: %s %s at %d,%d" % (k, kind, int(mods.value), a.x(), a.y())
    if kind == "click":
        QTest.mouseClick(vp, M.LeftButton, mods, a)
    elif kind == "drag":
        drag(vp, a, a + QtCore.QPoint(rng.randint(-120, 120), rng.randint(-90, 90)), mods)
    elif kind == "marquee":
        r = vp.rect()
        drag(
            vp,
            QtCore.QPoint(rng.randrange(r.width()), rng.randrange(r.height())),
            QtCore.QPoint(rng.randrange(r.width()), rng.randrange(r.height())),
            mods,
        )
    elif kind == "handle":
        sel = w.view.selection_roots()
        box = doc.selection_extent(sel, bounds=True) if sel else None
        if box is not None:
            l, t, r_, b_ = box
            c = vp_of(rng.choice([(l, t), (r_, t), (l, b_), (r_, b_), ((l + r_) / 2, t)]))
            drag(vp, c, c + QtCore.QPoint(rng.randint(-60, 60), rng.randint(-60, 60)), mods)
    elif kind == "dbl":
        QTest.mouseDClick(vp, M.LeftButton, mods, a)
    elif kind == "right":
        QTest.mouseClick(vp, M.RightButton, M.NoModifier, a)
    elif kind == "wheel":
        QtWidgets.QApplication.sendEvent(
            vp,
            QtGui.QWheelEvent(
                QtCore.QPointF(a),
                QtCore.QPointF(vp.mapToGlobal(a)),
                QtCore.QPoint(0, 0),
                QtCore.QPoint(0, rng.choice([-120, 120])),
                M.NoButton,
                mods,
                M.NoScrollPhase,
                False,
            ),
        )
    else:
        key = rng.choice(KEYS)
        kmods = rng.choice(
            [M.NoModifier, M.ControlModifier, M.ShiftModifier, M.ControlModifier | M.ShiftModifier]
        )
        G.state["where"] += " key %s" % QtGui.QKeySequence(key).toString()
        QTest.keyClick(w.view, key, kmods)
    pump()
    faulthandler.cancel_dump_traceback_later()


def run_page(path, tag):
    G.state["where"] = tag + " / open"
    w.open_document(path)
    pump(6)
    doc = w.doc
    if doc is None:
        G.problems.append((G.state["where"], "the page did not open"))
        return
    base = doc.serialize()
    t0 = time.time()
    for k in range(opt.steps):
        if w.doc is not doc:
            if any(e["doc"] is doc for e in w._docs):
                w.set_document(doc)
                pump()
            else:
                G.problems.append((G.state["where"], "the gesture closed the page"))
                return
        if k % 25 == 0:
            w.view.fit()
            pump(1)
        try:
            gesture(doc, k)
        except Exception:  # noqa: BLE001
            G.problems.append((G.state["where"], traceback.format_exc()[-1800:]))
        if hasattr(w, "commit_nudge"):
            w.commit_nudge()
    steps = len(doc._undo)
    G.state["where"] = "%s / undo all (%d steps)" % (tag, steps)
    try:
        if getattr(doc, "undo_trimmed", 0):
            info.append("%s: the undo cap dropped steps: not checked" % tag)
        else:
            n = 0
            while doc.can_undo() and n < 5000:
                w.undo()
                n += 1
            pump()
            if doc.serialize() != base:
                G.problems.append((G.state["where"], "UNDO OF EVERYTHING IS NOT THE OPENED PAGE"))
            m = 0
            while doc.can_redo() and m < 5000:
                w.redo()
                m += 1
            pump()
            while doc.can_undo() and n > 0:
                w.undo()
                n -= 1
            pump()
            if doc.serialize() != base:
                G.problems.append((tag + " / redo all, undo all", "NOT THE OPENED PAGE"))
    except Exception:  # noqa: BLE001
        G.problems.append((G.state["where"], traceback.format_exc()[-1800:]))
    info.append("%s: %d gestures, %d undo steps, %.0f s" % (tag, opt.steps, steps, time.time() - t0))
    doc.dirty = False


def run_all():
    try:
        by = {}
        for spec in opt.pages.split(","):
            proj, page = spec.split(":") if ":" in spec else ("Example", spec)
            by.setdefault(proj, []).append(page)
        for proj, pages in by.items():
            folder = G.copy_project(proj, pages)
            for page in pages:
                run_page(os.path.join(folder, page + ".ctg"), page)
    except BaseException:  # noqa: BLE001 -- the report is written whatever happens
        G.problems.append(("main / " + G.state["where"], traceback.format_exc()[-1800:]))
    QtCore.QTimer.singleShot(0, loop.quit)


loop = QtCore.QEventLoop()
QtCore.QTimer.singleShot(0, run_all)
loop.exec()
n = G.write_report(opt.out, info, "GESTURES DONE (seed %d):" % opt.seed)
print("%d problem(s) -> %s" % (n, opt.out))
G.finish(1 if n else 0)
