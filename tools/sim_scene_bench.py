"""Time PageScene.apply_states on a big page.

python tools/sim_scene_bench.py [page.ctg] [--count 500] [--scans 20]
"""

from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests_corpus"))


def default_page():
    """The page timed when none is given: the role 'bench_page', else the biggest page of a project that is not shipped."""
    import corpus  # (the local roles: tests_corpus/local.json)

    return corpus.role_path_or_none("bench_page") or corpus.biggest_user_file(".ctg")


def pick(page, scene, count: int):
    """``count`` drawn records with an AN of their own, spread over the page."""
    from ctgedit.model.document import ObjectKind

    seen, out = set(), []
    for i, o in enumerate(page.objects):
        if i not in scene._items or o.an <= 0 or o.an in seen or o.raw.get("_non_drawable"):
            continue
        if o.kind in (ObjectKind.GROUP, ObjectKind.GENIE):
            continue
        seen.add(o.an)
        out.append((i, o))
    if len(out) > count:
        step = len(out) / float(count)
        out = [out[int(k * step)] for k in range(count)]
    return out


def states_for(picked, scan: int):
    from ctgedit.model.document import ObjectKind
    from ctgedit.sim.state import ObjectState

    out = {}
    for k, (i, o) in enumerate(picked):
        ph = (scan + k) * 0.35
        if o.kind is ObjectKind.TEXT:
            out[o.an] = ObjectState(o.an, text="%.1f" % (50 + 40 * math.sin(ph)))
        else:
            c = (int(127 + 120 * math.sin(ph)) << 16) | (int(127 + 120 * math.cos(ph)) << 8) | 64
            out[o.an] = ObjectState(o.an, move=(round(6 * math.sin(ph)), 0), fill_colour=c)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        epilog="Without a page: the role 'bench_page' of tests_corpus/local.json, else the "
        "biggest page of a project that is not shipped.",
    )
    ap.add_argument("page", nargs="?", default=None)
    ap.add_argument("--count", type=int, default=500)
    ap.add_argument("--scans", type=int, default=20)
    a = ap.parse_args(argv)
    if a.page is None:
        a.page = default_page()
        if a.page is None:
            ap.error("give a page, or set bench_page in tests_corpus/local.json")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.io.reader import read_page
    from ctgedit.render.scene import PageScene

    t0 = time.perf_counter()
    page = read_page(a.page)
    t1 = time.perf_counter()
    sc = PageScene()
    sc.set_page(page)
    t2 = time.perf_counter()
    view = QtWidgets.QGraphicsView(sc)
    view.resize(1600, 900)
    view.fitInView(sc.page_rect())
    view.show()
    app.processEvents()
    picked = pick(page, sc, a.count)
    print(
        "page %s: %d objects, %d items; read %.0f ms, scene %.0f ms; %d animated records"
        % (
            os.path.basename(a.page),
            len(page.objects),
            len(sc._items),
            (t1 - t0) * 1000,
            (t2 - t1) * 1000,
            len(picked),
        )
    )
    times = []
    first = None
    for scan in range(a.scans):
        st = states_for(picked, scan)
        t = time.perf_counter()
        r = sc.apply_states(st)
        dt = (time.perf_counter() - t) * 1000
        if scan == 0:
            first = (dt, r)
        else:
            times.append(dt)
    t = time.perf_counter()
    r_same = sc.apply_states(states_for(picked, a.scans - 1))
    same = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    view.viewport().repaint()
    paint = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    sc.clear_states()
    clear = (time.perf_counter() - t) * 1000
    print("first scan %.1f ms (%s)" % first)
    print(
        "changing scans: mean %.1f ms, max %.1f ms over %d scans of %d states"
        % (statistics.mean(times), max(times), len(times), len(picked))
    )
    print("unchanged scan %.1f ms (%s); view paint %.0f ms; clear %.1f ms" % (same, r_same, paint, clear))
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
