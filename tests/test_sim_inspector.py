"""The Simulate Inspector."""

from qtkit import app as _app


# -- the stand-ins (the same as tests/test_simulate_tab.py's: an in-memory page and tags) --
class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _tags():
    from ctgedit.ui.simulate.sources import TagInfo

    return [
        TagInfo(
            "Tank1.Level",
            "REAL",
            0.0,
            eng_zero=0.0,
            eng_full=200.0,
            units="%",
            equipment="Tank1",
            item="Level",
            page_reads=True,
        ),
        TagInfo("Pump1.Run", "DIGITAL", 0, equipment="Pump1", item="Run", page_reads=True),
        TagInfo("Message", "STRING", "", page_reads=True),
        TagInfo("Flow", "REAL", 1.0, comment="Raw water flow", page_reads=False),
    ]


def _rect_page():
    from ctgedit.model.document import Geometry, GraphicObject, ObjectKind, Page

    p = Page(name="sim_ui")
    p.appearance.width, p.appearance.height = 400, 300
    p.appearance.background_colour = 0xFFFFFF
    o = GraphicObject(
        kind=ObjectKind.RECTANGLE, an=11, geometry=Geometry(x=100, y=100, extent_x=80, extent_y=60)
    )
    o.style.filled, o.style.fill_colour, o.style.line_colour = True, 0xFF0000, 0xFF0000
    o.raw["_type_code"] = 0x0F
    o.raw["_props"] = {
        "MVX": "(Tank1.Level)^(0,200)",
        "COL_A": "Pump1.Run",
        "CMD": 'PageDisplay("FxSim2"); Beep(1)',
    }
    p.objects = [o]
    return p


def _window():
    _app()
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.ui.simulate.window import SimulateWindow

    win = SimulateWindow()
    sim = FakeSimulation(clock=_Clock(), tags=_tags())
    sp = win.open_page("", page=_rect_page(), sim=sim, run=False)
    return win, sp


def test_objects_details_tags_and_the_filter():
    from PySide6 import QtGui, QtWidgets
    from ctgedit.sim.state import ObjectState

    win, sp = _window()
    try:
        sp.apply(
            type(
                "St",
                (),
                {
                    "objects": {11: ObjectState(11, fill_level=0.42, text="42")},
                    "replace": False,
                    "drawn": None,
                    "controls": None,
                    "flash_on": True,
                },
            )()
        )
        sp.canvas.note_error(11, "Hardware error (424) 'Tag not found'", "error")
        ins = win.open_inspector()
        QtWidgets.QApplication.processEvents()
        rows = ins.objects.model.rows
        assert [r[0] for r in rows] == ["11"]
        assert rows[0][2] == "Rectangle"
        assert "level 42%" in rows[0][4] and "text 42" in rows[0][4]
        assert rows[0][5].startswith("Hardware error (424)")
        # the details: the expressions, the tags they name with values, the state
        ins._object_selected(11)
        d = ins.details.toPlainText()
        assert "MVX = (Tank1.Level)^(0,200)" in d
        assert "Tank1.Level = " in d and "Pump1.Run = " in d
        assert "State now: " in d and "Error: Hardware error (424)" in d
        # Tags: where each comes from, the objects that name it
        ins.tabs.setCurrentWidget(ins.tags)
        ins.refresh()
        by = {r[0]: r for r in ins.tags.model.rows}
        assert by["Tank1.Level"][4] == "the page" and by["Tank1.Level"][5] == "11"
        assert by["Message"][5] == "" and "Flow" not in by  # (not a tag of the page)
        # the filter keeps the rows with the text in any column; the count says so
        assert ins.filter.placeholderText() == "Search tags"
        ins.filter.setText("pump")
        ins.filter.flush()  # (the search field's pause after typing)
        assert ins.tags.shown_count() == 1 and ins.count.text() == "1 of %d rows" % len(by)
        ins.filter.clear()
        ins.filter.flush()
        # copy: tab separated, the column names first
        ins.tags.view.selectRow(0)
        assert ins.tags.copy_rows() == 1
        text = QtGui.QGuiApplication.clipboard().text().splitlines()
        assert text[0].startswith("Name\tValue\tQuality")
        # Only with errors
        ins.tabs.setCurrentIndex(0)
        ins.errors_only.setChecked(True)
        assert ins.objects.shown_count() == 1
        sp.canvas.clear_errors()
        ins._filter_changed()
        assert ins.objects.shown_count() == 0
    finally:
        win.close()
        win.deleteLater()


def test_show_on_page_and_inspect_from_the_log():
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate.ctsim_source import log_entry

    win, sp = _window()
    try:
        ins = win.open_inspector(11)
        QtWidgets.QApplication.processEvents()
        assert ins.objects.selected() == 11 and "AN 11" in ins.details.toPlainText()
        ins._show_object(11)
        assert "AN 11" in ins.note.text()
        ins.refresh()  # (what a command did stays until the next one)
        assert "AN 11" in ins.note.text()
        # the log's Inspect Object opens it at the line's object
        e = log_entry(1.0, {"level": "error", "text": "x", "an": 11, "window": 0})
        win.outputs.inspectObject.emit(e)
        assert win.open_inspector() is ins and ins.objects.selected() == 11
        # the Page choice: the tab (no windows of its own here)
        assert ins.page_pick.count() == 1 and ins.page_pick.itemText(0).startswith("sim_ui")
    finally:
        win.close()
        win.deleteLater()


def test_the_inspector_is_a_window_of_the_editor_kind():
    """The search field of every list (Esc clears it, then closes the window), its F1 topic,
    the tables' columns and a size and place that are remembered."""
    from PySide6 import QtCore, QtTest, QtWidgets
    from ctgedit.ui import help as H, tables
    from ctgedit.ui.style import theme
    from ctgedit.ui.prefs.settings import app_settings

    win, sp = _window()
    try:
        ins = win.open_inspector()
        QtWidgets.QApplication.processEvents()
        assert isinstance(ins.filter, theme.SearchField)
        assert H.help_of(ins) == "simulate#the-inspector"
        for t in (ins.objects, ins.tags, ins.assocs, ins.vars, ins.windows):
            assert getattr(t.view, tables.ATTR, None) is not None
        ins.filter.setText("x")
        QtTest.QTest.keyClick(ins.filter, QtCore.Qt.Key_Escape)
        assert ins.filter.text() == "" and ins.isVisible()
        ins.resize(700, 500)
        ins.move(40, 60)
        QtTest.QTest.keyClick(ins.filter, QtCore.Qt.Key_Escape)
        assert not ins.isVisible()
        stored = str(app_settings().value("geometry/PageInspector"))
        assert stored.endswith(",700,500")
        win._inspector = None
        again = win.open_inspector()
        QtWidgets.QApplication.processEvents()
        assert again is not ins and again.size() == QtCore.QSize(700, 500)
        again.close()
    finally:
        win.close()
        win.deleteLater()


def test_performance_names_each_object_and_lists_the_functions(monkeypatch):
    # an expression row says the object (name, type, genie) and its animation in words; the
    # Functions table lists each Cicode function and built-in with its callers
    from PySide6 import QtCore, QtWidgets
    from ctgedit.ui.simulate import sim_tools
    from ctgedit.ui.simulate.inspector import animation_text

    win, sp = _window()

    class Tools:
        profile_error = ""
        profile = {
            "running": False,
            "seconds": 2.0,
            "scans": [],
            "tasks": [],
            "expressions": [
                {
                    "window": 0,
                    "page": "sim_ui",
                    "an": 11,
                    "class": "fill_colour_onoff",
                    "count": 4,
                    "ms": 1.5,
                    "avgUs": 375.0,
                    "maxUs": 400.0,
                }
            ],
            "functions": [
                {
                    "name": "FillLevel",
                    "builtin": False,
                    "file": r"C:\x\Genie.ci",
                    "calls": 4,
                    "ms": 1.2,
                    "selfMs": 0.7,
                    "avgUs": 300.0,
                    "maxUs": 420.0,
                    "callers": [{"name": "(page code)", "calls": 4}],
                },
                {
                    "name": "DspGetAnExtent",
                    "builtin": True,
                    "file": "",
                    "calls": 8,
                    "ms": 0.5,
                    "selfMs": 0.5,
                    "avgUs": 62.5,
                    "maxUs": 90.0,
                    "callers": [{"name": "FillLevel", "calls": 8}],
                },
            ],
        }

        def available(self):
            return True

        def supports(self, name):
            return True

        def ask_profile(self, action="get", top=200):
            return True

    monkeypatch.setattr(sim_tools, "tools_of", lambda sim: Tools())
    monkeypatch.setattr(sp, "engine_window", lambda: 0)
    try:
        ins = win.open_inspector()
        QtWidgets.QApplication.processEvents()
        ins._fill_performance(sp)
        perf = ins.perf
        assert [perf.e_model.headerData(c, QtCore.Qt.Horizontal) for c in range(5)] == [
            "AN",
            "Object",
            "Type",
            "Genie",
            "Animation",
        ]
        row = [perf.e_model.item(0, c).text() for c in range(5)]
        assert row[0] == "11" and row[2] == "Rectangle" and row[4] == "Fill colour (on/off)"
        fn = {
            perf.f_model.item(r, 0).text(): [
                perf.f_model.item(r, c).text() for c in range(perf.f_model.columnCount())
            ]
            for r in range(perf.f_model.rowCount())
        }
        assert fn["FillLevel"][1] == "Cicode" and fn["FillLevel"][4] == "0.700"
        assert fn["FillLevel"][8] == "(page code) (4)" and fn["FillLevel"][9] == "Genie.ci"
        assert fn["DspGetAnExtent"][1] == "Built-in" and fn["DspGetAnExtent"][8] == "FillLevel (8)"
        # the Inspector's search filters the Performance views
        ins.filter.setText("dspget")
        ins.filter.flush()
        assert perf.f_model.rowCount() == 1 and perf.f_model.item(0, 0).text() == "DspGetAnExtent"
        ins.filter.clear()
        ins.filter.flush()
        # the animation words
        assert animation_text("fill_level") == "Fill level"
        assert animation_text("display_multi") == "Text (multi-state)"
        assert animation_text("symbolset_array") == "Symbol (array)"
        assert animation_text("something_new") == "Something new"
    finally:
        win.close()
        win.deleteLater()


def test_the_tables_sort_by_their_kept_keys_as_by_the_values():
    """A table's sort reads each row's sort value from the model's kept keys: the order is the
    one the comparison of data()'s values gives (kept here as it was), before and after rows
    change in place."""
    import random
    from PySide6 import QtCore
    from ctgedit.ui.simulate import inspector as I

    _app()
    rnd = random.Random(5)

    class Before(I._Filter):
        def lessThan(self, a, b):  # noqa: N802
            x, y = a.data(QtCore.Qt.UserRole + 1), b.data(QtCore.Qt.UserRole + 1)
            if type(x) is not type(y):
                x, y = str(x), str(y)
            return x < y

    def cell():
        return rnd.choice(
            [
                str(rnd.randint(-50, 50)),
                "%.2f" % rnd.uniform(-9, 9),
                "",
                rnd.choice(["Pump", "pump", "Valve", "AN 3", "x", "1e3", "nan"]),
            ]
        )

    rows = [("%d" % k, cell(), cell(), cell()) for k in range(400)]
    models = [I.RowsModel(["AN", "a", "b", "c"]) for _ in range(2)]
    proxies = [I._Filter(), Before()]
    for m, p in zip(models, proxies):
        p.setSourceModel(m)
        m.set_rows(list(rows), list(range(len(rows))))

    def order(p):
        return [p.mapToSource(p.index(k, 0)).row() for k in range(p.rowCount())]

    for col in range(4):
        for p in proxies:
            p.sort(col, QtCore.Qt.AscendingOrder)
        assert order(proxies[0]) == order(proxies[1])
    # the same rows, some values changed in place (the Inspector's refresh)
    rows2 = [r if k % 7 else (r[0], cell(), cell(), cell()) for k, r in enumerate(rows)]
    for m in models:
        m.set_rows(list(rows2), list(range(len(rows2))))
    assert order(proxies[0]) == order(proxies[1])
    for p in proxies:
        p.sort(3, QtCore.Qt.DescendingOrder)
    assert order(proxies[0]) == order(proxies[1])
