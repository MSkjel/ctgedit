"""Fire every editor command and menu entry on copies of pages, and report exceptions.

python tools/command_sweep.py [--out FILE] [--quick] [--only REGEX] [--theme-each]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sweep_guard as G  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.join(HERE, "_tests", "command_sweep.txt"))
ap.add_argument("--root", default=os.path.dirname(HERE))
ap.add_argument("--quick", action="store_true")
ap.add_argument("--only", default="")
ap.add_argument("--theme-each", action="store_true")
opt = ap.parse_args()
os.makedirs(os.path.dirname(os.path.abspath(opt.out)), exist_ok=True)
G.install(opt.out)
sys.path.insert(0, opt.root)
ONLY = re.compile(opt.only, re.I) if opt.only else None

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

app = QtWidgets.QApplication([])
G.answer_qt(app)

#: commands that leave the process, start programs or the engine, or open other windows
SKIP = re.compile(
    r"exit|quit|close_window|simulat|compile|(^|_)run($|_)|\brun\b|runtime|"
    r"restart|online|website|report|open_log|explorer|reveal|studio|ctdraw|"
    r"engine|about_qt|new_window|print",
    re.I,
)
#: skipped while a page is open (they close it: the no-page state covers them)
CLOSING = re.compile(r"(^|_)close|revert|\bclose\b", re.I)


def pump(n=2):
    for _ in range(n):
        app.processEvents()


from ctgedit.ui.style import theme  # noqa: E402
from ctgedit.ui.main_window import MainWindow  # noqa: E402

theme.apply_theme(app)
w = MainWindow()
w.resize(1400, 900)
w.show()
pump(4)
fired: dict = {}
slow: list = []
current = {"path": None, "sel": []}


def actions(win):
    out, seen = [], set()
    for n in sorted(dir(win)):
        if n.startswith("__"):
            continue
        try:
            a = getattr(win, n, None)
        except Exception:  # noqa: BLE001
            continue
        vals = a.values() if isinstance(a, dict) else a if isinstance(a, (list, tuple)) else [a]
        for x in vals:
            if isinstance(x, QtGui.QAction) and id(x) not in seen:
                seen.add(id(x))
                text = x.text().replace("&", "")
                if SKIP.search(n) or SKIP.search(text):
                    continue
                if ONLY is not None and not (ONLY.search(n) or ONLY.search(text)):
                    continue
                out.append((n, x))
    return out


def close_popups():
    for m in QtWidgets.QApplication.topLevelWidgets():
        if m is w:
            continue
        try:
            if m.isVisible() and (
                isinstance(m, (QtWidgets.QDialog, QtWidgets.QMenu))
                or (isinstance(m, QtWidgets.QMainWindow) and not isinstance(m, MainWindow))
            ):
                m.close()
        except RuntimeError:
            pass


def reopen():
    """Back to the page alone in the sweep's window (a command closed it, opened another
    document, or moved the tab to a new window), selected as before."""
    p = current["path"]
    if p is None:
        return
    key = os.path.normcase(p)
    others = [x for x in MainWindow.editor_windows() if x is not w]
    if not others and len(w._docs) == 1 and w.doc is not None and os.path.normcase(w.doc.path) == key:
        return
    try:
        for x in others:
            for e in list(x._docs):
                e["doc"].dirty = False
            x.close()
        pump()
        for k in range(len(w._docs) - 1, -1, -1):
            if os.path.normcase(w._docs[k]["doc"].path) != key:
                w._docs[k]["doc"].dirty = False
                w._close_doc_tab(k, ask=False)
        pump()
        if w.doc is None or os.path.normcase(w.doc.path) != key:
            w.open_document(p)
            pump(6)
        if w.doc is not None and current["sel"]:
            w._set_selection([i for i in current["sel"] if i < len(w.doc.page.objects)])
            pump()
    except Exception:  # noqa: BLE001
        G.problems.append((G.state["where"] + " / reopen", traceback.format_exc()[-1800:]))


def fire(tag):
    with_page = w.doc is not None
    for n, x in actions(w):
        text = x.text().replace("&", "")
        if with_page and (CLOSING.search(n) or CLOSING.search(text)):
            continue
        G.state["where"] = "%s / %s (%s)" % (tag, n, text)
        try:
            if x.isEnabled():
                fired[tag] = fired.get(tag, 0) + 1
                t0 = time.perf_counter()
                x.trigger()
                if x.isCheckable():
                    x.trigger()  # (and back)
                pump()
                if time.perf_counter() - t0 > 1.5:
                    slow.append((time.perf_counter() - t0, G.state["where"]))
        except Exception:  # noqa: BLE001
            G.problems.append((G.state["where"], traceback.format_exc()[-1800:]))
        close_popups()
        pump(1)
        if opt.theme_each:
            theme.apply_theme(app)
            pump(1)
        if with_page:
            reopen()


def _submenus(menu) -> dict:
    """menuAction pointer -> QMenu WITHOUT QAction.menu() (PySide6 deletes the submenu once
    that action's wrapper goes: ui/dialog/palette.harvest_actions)."""
    import shiboken6

    out = {}
    for m in menu.findChildren(QtWidgets.QMenu):
        try:
            out[shiboken6.getCppPointer(m.menuAction())[0]] = m
        except RuntimeError:
            continue
    return out


def menu_actions(menu, depth=0, subs=None):
    import shiboken6

    out = []
    if menu is None or depth > 3:
        return out
    subs = _submenus(menu) if subs is None else subs
    for a in menu.actions():
        try:
            if not shiboken6.isValid(a) or a.isSeparator():
                continue
            sub = subs.get(shiboken6.getCppPointer(a)[0])
        except RuntimeError:
            continue
        if sub is not None:
            out += menu_actions(sub, depth + 1, subs)
        else:
            out.append(a)
    return out


def fire_menus(tag):
    """The canvas, Objects panel and tab menus: each entry with a fresh menu and the
    selection as it was."""
    import shiboken6
    from ctgedit.ui.window import context_menu

    sel0 = list(w._sel or [])

    def build(k):
        reopen()
        if w.doc is None:
            return None, []
        w._set_selection([i for i in sel0 if i < len(w.doc.page.objects)])
        pump(1)
        gp = w.view.viewport().mapToGlobal(w.view.viewport().rect().center())
        makers = (
            lambda: context_menu.build_canvas_menu(w, gp, list(sel0)),
            lambda: w.objects_dock.build_menu(),
            lambda: w.build_tab_menu(w.doc_tabs.currentIndex()),
        )
        try:
            menu = makers[k]()
            if menu is None:
                return None, []
            menu.aboutToShow.emit()
        except Exception:  # noqa: BLE001
            G.problems.append((G.state["where"], traceback.format_exc()[-1800:]))
            return None, []
        return menu, [a for a in menu_actions(menu) if shiboken6.isValid(a)]

    for k, mtag in enumerate(("canvas menu", "objects menu", "tab menu")):
        G.state["where"] = "%s / build %s" % (tag, mtag)
        menu, acts = build(k)
        texts = [a.text() for a in acts]
        if menu is not None:
            menu.deleteLater()
        for text0 in texts:
            plain = text0.replace("&", "")
            if SKIP.search(plain) or re.search(r"close|move to|new window", plain, re.I):
                continue
            if ONLY is not None and not ONLY.search(plain):
                continue
            G.state["where"] = "%s / %s: %s" % (tag, mtag, plain)
            menu, acts = build(k)
            a = next((x for x in acts if x.text() == text0), None)
            try:
                if a is not None and a.isEnabled():
                    fired[tag + " / " + mtag] = fired.get(tag + " / " + mtag, 0) + 1
                    a.trigger()
                pump()
            except Exception:  # noqa: BLE001
                G.problems.append((G.state["where"], traceback.format_exc()[-1800:]))
            close_popups()
            pump(1)
            if menu is not None:
                try:
                    menu.deleteLater()
                except RuntimeError:
                    pass


def undo_redo(tag):
    G.state["where"] = tag + " / undo-redo"
    doc = w.doc
    if doc is None:
        return
    try:
        k = 0
        while doc._undo and k < 60:
            w.undo()
            k += 1
        pump()
        for _ in range(k):
            w.redo()
        pump()
        while doc._undo and k > 0:
            w.undo()
            k -= 1
        pump()
    except Exception:  # noqa: BLE001
        G.problems.append((G.state["where"], traceback.format_exc()[-1800:]))


def selections(doc):
    roots, objs = doc.page.roots(), doc.page.objects
    own = [i for i in roots if not objs[i].raw.get("_from_template")]
    plain = [
        i for i in own if objs[i].kind.value in ("rectangle", "ellipse", "text", "line", "polygon", "pipe")
    ]
    genies = [i for i in own if getattr(objs[i], "is_genie", False)]
    return [("one plain", plain[:1]), ("a genie", genies[:1]), ("many", own[:60])]


def sweep_page(path, tag):
    G.state["where"] = tag + " / open"
    w.open_document(path)
    pump(6)
    if w.doc is None:
        G.problems.append((G.state["where"], "the page did not open"))
        return
    current["path"], current["sel"] = path, []
    fire(tag + ", nothing selected")
    undo_redo(tag + ", nothing selected")
    for stag, sel in selections(w.doc):
        reopen()
        if not sel or w.doc is None:
            continue
        current["sel"] = list(sel)
        G.state["where"] = "%s, %s / select" % (tag, stag)
        w._set_selection(list(sel))
        pump()
        fire("%s, %s" % (tag, stag))
        undo_redo("%s, %s" % (tag, stag))
        if w.doc is not None:
            fire_menus("%s, %s" % (tag, stag))
            undo_redo("%s, %s menus" % (tag, stag))
        if opt.quick:
            break
    current["path"], current["sel"] = None, []


def run_all():
    global w
    try:
        fire("no page")
        ex = G.copy_project("Example", ["Genies", "Mixer"])
        sweep_page(os.path.join(ex, "Genies.ctg"), "Genies")
        if not opt.quick:
            sa = G.copy_project("ExampleSA", ["Mixing_L3"])
            sweep_page(os.path.join(sa, "Mixing_L3.ctg"), "Mixing_L3")
            G.state["where"] = "version tab / make"
            from ctgedit.infra import history as H

            H.configure(5, 200)
            w.open_document(os.path.join(ex, "Mixer.ctg"))
            pump(6)
            d = w.doc
            r = d.page.roots()[0]
            d.move_objects([r], 4, 0)
            w.save()
            d.move_objects([r], -4, 0)
            w.save()
            pump()
            vs = H.list_versions(d.path)
            if vs:
                w.open_version_read_only(vs[0])
                pump(6)
                fire("version tab")
                undo_redo("version tab")
            G.state["where"] = "restored session / make"
            from ctgedit.ui.prefs.settings import app_settings

            app_settings().setValue(
                "session/open_files",
                [
                    os.path.join(ex, "Genies.ctg"),
                    os.path.join(ex, "Mixer.ctg"),
                    os.path.join(sa, "Mixing_L3.ctg"),
                ],
            )
            w2 = MainWindow()
            w2.resize(1200, 800)
            w2.show()
            pump(4)
            w2.restore_session(defer=True)
            pump(6)
            w_main, w = w, w2
            fire("restored session")
            w = w_main
            for e in list(w2._docs):
                e["doc"].dirty = False
            w2.close()
            pump(4)
            G.state["where"] = "light theme"
            theme.apply_theme(app, "light")
            w.resize(800, 560)
            pump(4)
            if w.doc is not None:
                fire("light, small")
    except BaseException:  # noqa: BLE001 -- the report is written whatever happens
        G.problems.append(("main / " + G.state["where"], traceback.format_exc()[-1800:]))
    QtCore.QTimer.singleShot(0, loop.quit)


t0 = time.time()
loop = QtCore.QEventLoop()
QtCore.QTimer.singleShot(0, run_all)
loop.exec()
lines = ["FIRED %4d  %s" % (n, k) for k, n in fired.items()]
lines += ["SLOW %.1fs %s" % (dt, where) for dt, where in sorted(slow, reverse=True)[:30]]
n = G.write_report(
    opt.out, lines, "SWEEP DONE in %.0fs, %d commands fired:" % (time.time() - t0, sum(fired.values()))
)
for e in list(getattr(w, "_docs", [])):
    try:
        e["doc"].dirty = False
    except Exception:  # noqa: BLE001
        pass
print("%d problem(s), %d refused -> %s" % (n, len(G.refused), opt.out))
G.finish(1 if n else 0)
