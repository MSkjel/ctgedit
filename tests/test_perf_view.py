"""The Inspector's Performance tab."""

import os
from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _n(name, ms, kind="cicode", self_ms=None, calls=1, children=()):
    return {
        "name": name,
        "kind": kind,
        "ms": ms,
        "selfMs": ms if self_ms is None else self_ms,
        "calls": calls,
        "children": list(children),
    }


def _report():
    # (page code) -> A -> B -> Sleep ; (task T) -> B -> C ; recursion: (task R) -> B -> B
    tree = [
        _n(
            "(page code)",
            10,
            "origin",
            0,
            children=[
                _n("A", 10, self_ms=2, children=[_n("B", 8, self_ms=3, children=[_n("Sleep", 5, "builtin")])])
            ],
        ),
        _n("(task T)", 6, "origin", 0, children=[_n("B", 6, self_ms=4, children=[_n("C", 2)])]),
        _n("(task R)", 4, "origin", 0, children=[_n("B", 4, self_ms=1, children=[_n("B", 3)])]),
    ]
    funcs = [
        {
            "name": "B",
            "builtin": False,
            "file": r"C:\x\b.ci",
            "calls": 4,
            "ms": 18,
            "selfMs": 11,
            "avgUs": 1,
            "maxUs": 2,
            "callers": [{"name": "A", "calls": 1}],
        },
        {
            "name": "Sleep",
            "builtin": True,
            "file": "",
            "calls": 1,
            "ms": 5,
            "selfMs": 5,
            "avgUs": 1,
            "maxUs": 1,
            "callers": [{"name": "B", "calls": 1}],
        },
        {
            "name": "A",
            "builtin": False,
            "file": "",
            "calls": 1,
            "ms": 10,
            "selfMs": 2,
            "avgUs": 1,
            "maxUs": 1,
            "callers": [{"name": "(page code)", "calls": 1}],
        },
        {
            "name": "C",
            "builtin": False,
            "file": "",
            "calls": 1,
            "ms": 2,
            "selfMs": 2,
            "avgUs": 1,
            "maxUs": 1,
            "callers": [{"name": "B", "calls": 1}],
        },
    ]
    return {
        "running": False,
        "seconds": 3.0,
        "scans": [],
        "tasks": [{"name": "T", "count": 3, "ms": 6, "avgUs": 2, "maxUs": 3}],
        "functions": funcs,
        "tree": tree,
    }


def test_all_calls_of_a_function_together_and_who_calls_it():
    from ctgedit.ui.simulate import perf_view as P

    tree = P.prepare(_report()["tree"])
    m = P.merged_calls(tree, "B")
    # B's outermost calls: under A (8), under T (6), under R (4; its inner B is inside it)
    assert m["ms"] == 18 and m["calls"] == 3
    kids = {c["name"]: c for c in m["children"]}
    assert kids["Sleep"]["ms"] == 5 and kids["C"]["ms"] == 2 and kids["B"]["ms"] == 3
    who = P.callers_of(tree, "B")
    assert who["ms"] == 18
    up = {c["name"]: c["ms"] for c in who["children"]}
    assert up == {"A": 8, "(task T)": 6, "(task R)": 4}
    a = next(c for c in who["children"] if c["name"] == "A")
    assert a["children"][0]["name"] == "(page code)"
    assert P.merged_calls(tree, "nothing") is None


def test_the_views_the_focus_and_the_search():
    from PySide6 import QtCore, QtGui
    from ctgedit.infra.textmatch import TextMatch
    from ctgedit.ui.simulate.perf_view import PerformanceView

    _app()
    v = PerformanceView(lambda a: None, lambda an: None, lambda an: None)
    v.resize(900, 500)
    v.show()
    try:
        v.set_report(_report(), [(5, "Pump", "Rectangle", "", "Fill level", "P", 3, 1.5, 2, 3)])
        assert v.f_model.rowCount() == 4
        # the costliest own time first, with its share
        assert v.f_model.item(0, 0).text() == "B" and v.f_model.item(0, 5).text() == "55.0"
        assert v.t_model.rowCount() == 3 and v.t_model.item(0, 0).text() == "(page code)"
        assert len(v.flame._boxes) == 10  # (every node of the tree)
        # focus: B's calls, who calls it; the flame graph from B; Functions of what it calls
        v.set_focus("B")
        assert not v.focus_bar.isHidden()
        assert [v.t_model.item(k, 0).text() for k in range(2)] == ["B: what it calls", "B: who calls it"]
        assert {v.f_model.item(k, 0).text() for k in range(v.f_model.rowCount())} == {"B", "Sleep", "C"}
        assert v.flame._boxes[0][1]["name"] == "B"
        v.set_focus("")
        assert v.focus_bar.isHidden() and v.f_model.rowCount() == 4
        # the search: rows, the tree's paths (with their callers), the flame graph's bars
        v.set_matcher(TextMatch("sleep"))
        assert v.f_model.rowCount() == 1
        assert v.t_model.rowCount() == 1  # ((page code) > A > B > Sleep)
        assert v.flame._match("Sleep") and not v.flame._match("C")
        v.set_matcher(TextMatch(""))
        # the flame graph: a click zooms into a bar, Backspace goes back
        a_box = next(r for r, n in v.flame._boxes if n["name"] == "A")
        v.flame.zoom_to(next(n for r, n in v.flame._boxes if n["name"] == "A"))
        assert v.flame._boxes[0][1]["name"] == "A"
        assert abs(v.flame._boxes[0][0].width() - (v.flame.width() - 2)) < 1
        ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Backspace, QtCore.Qt.NoModifier)
        v.flame.keyPressEvent(ev)
        assert v.flame._zoom["name"] == "(page code)"
        del a_box
        # copy: the view shown, its column names first
        v.switch.setCurrentIndex(0)
        assert v.copy_rows() == 4
        text = QtGui.QGuiApplication.clipboard().text().splitlines()
        assert text[0].startswith("Function\tKind\tCalls")
    finally:
        v.close()


def test_the_inputs_list_fits_its_panel():
    from PySide6 import QtGui, QtWidgets

    _app()
    from ctgedit.ui import tables

    view = QtWidgets.QTreeView()
    model = QtGui.QStandardItemModel(0, 4)
    model.setHorizontalHeaderLabels(["Tag", "Value", "Quality", "Association"])
    view.setModel(model)
    fit = tables.make_resizable(
        view, "Test/inputs", stretch=0, caps={1: 0.3, 3: 0.3}, fixed={2: "Uncertain"}, fit_view=True
    )
    view.resize(320, 300)
    view.show()
    try:
        for k in range(30):
            model.appendRow(
                [
                    QtGui.QStandardItem("Plant.Mixer.Strawberry.Feeder%d.Fault" % k),
                    QtGui.QStandardItem("12345.678"),
                    QtGui.QStandardItem("Good"),
                    QtGui.QStandardItem("?Tag? %d" % k),
                ]
            )
        QtWidgets.QApplication.processEvents()
        fit.fit()
        hdr = view.header()
        width = view.viewport().width()
        assert sum(hdr.sectionSize(c) for c in range(4)) <= width + 2
        fm = view.fontMetrics()
        assert hdr.sectionSize(2) <= fm.horizontalAdvance("Uncertain") + 40
        assert hdr.sectionSize(1) <= int(width * 0.3) + 1
        # a resize in code is not the user's: fitted again
        fitted = hdr.sectionSize(1)
        hdr.resizeSection(1, 90)
        fit.fit()
        assert hdr.sectionSize(1) == fitted and not tables.has_user_widths(view)
        # a divider the user drags stays where they put it, and is remembered
        from PySide6 import QtCore, QtTest

        vp = hdr.viewport()
        x = hdr.sectionViewportPosition(1) + hdr.sectionSize(1) - 1
        y = vp.height() // 2
        QtTest.QTest.mousePress(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, y))
        QtTest.QTest.mouseMove(vp, QtCore.QPoint(x - 10, y))
        QtTest.QTest.mouseRelease(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x - 10, y))
        for _k in range(3):
            QtWidgets.QApplication.processEvents()
        fit.fit()
        assert hdr.sectionSize(1) == fitted - 10 and tables.has_user_widths(view)
    finally:
        view.close()
