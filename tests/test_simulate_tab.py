"""The Simulate window."""

import gc

import pytest
from qtkit import app as _app

#: the page the window tests run: FxSim's FxSimBig (tests/fixtures/FxInstall), 109 animated
#: objects over 56 tags (lamps, values, fill levels, symbol sets, groups, a login text with
#: '@(...)' labels)
BIG = ("FxSimBig", "FxSim")


@pytest.fixture
def big(fx_page):
    """The path of FxSimBig in the test's fx_install copy."""
    return fx_page(*BIG)


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


# -- the demo source ----------------------------------------------------------------------------
def test_expression_names_and_commands():
    from ctgedit.ui.simulate.sources import command_functions, expression_names

    assert expression_names("(Tank1.Level + Offset)^(0,200)") == ["Tank1.Level", "Offset"]
    assert expression_names('IF Pump1.Run AND NOT Fault THEN "a" END') == ["Pump1.Run", "Fault"]
    assert expression_names("(Fx_Choc):#.#") == ["Fx_Choc"]
    assert expression_names('TagGetProperty(Config, "Custom1") > 2') == ["Config"]
    assert expression_names("%Tag%.PV") == []
    assert command_functions('PageDisplay("FxSim2"); Beep(1)') == ["PageDisplay", "Beep"]


def test_fake_simulation_runs_steps_and_pauses():
    from ctgedit.ui.simulate.sources import FakeSimulation

    clk = _Clock()
    sim = FakeSimulation(clock=clk, tags=_tags())
    sim.start("", _rect_page())
    first = sim.poll()
    assert first is not None and 11 in first.objects
    assert sim.poll() is None  # nothing new
    sim.clock("run")
    clk.t += 1.0  # 4 scans of 250 ms
    st = sim.poll()
    assert sim.clock_state().scan == 4 and st.objects[11].move is not None
    assert st.objects[11].fill_colour in (0x00B050, 0xE03030)
    sim.clock("pause")
    clk.t += 5.0
    assert sim.poll() is None and sim.clock_state().scan == 4
    sim.clock("step")
    assert sim.poll() is not None and sim.clock_state().scan == 5
    sim.clock("speed", 10.0)
    sim.clock("run")
    clk.t += 1.0
    sim.poll()
    assert sim.clock_state().scan == 45 and sim.clock_state().running


def test_fake_simulation_tags_writes_and_log():
    from ctgedit.ui.simulate.sources import FakeSimulation, InputEvent

    sim = FakeSimulation(clock=_Clock(), tags=_tags())
    sim.start("", _rect_page())
    assert [t.name for t in sim.tags(page_only=True)] == ["Tank1.Level", "Pump1.Run", "Message"]
    s0 = sim.serial()
    sim.set_tag("Tank1.Level", 150.0, "Bad")
    assert sim.serial() > s0
    lvl = next(t for t in sim.tags() if t.name == "Tank1.Level")
    assert lvl.value == 150.0 and lvl.quality == "Bad"
    sim.input(InputEvent("up", an=11, ans=(11,)))
    log = sim.drain()
    assert [e.kind for e in log] == ["write", "log", "not_simulated", "not_simulated"]
    assert log[2].function == "PageDisplay" and log[3].function == "Beep"
    assert sim.drain() == []


def test_fake_finds_the_pages_animated_objects_and_tags(big):
    from ctgedit.io.reader import read_page
    from ctgedit.ui.simulate.sources import page_animations, page_tags

    page = read_page(big)
    anim = page_animations(page)
    assert len(anim) > 100
    assert any(a.text for a in anim) and any(a.colour for a in anim)
    assert any(a.states > 1 for a in anim) and any(a.level for a in anim)
    tags = page_tags(page)
    assert len(tags) > 50 and all(t.page_reads for t in tags)


# -- the window ----------------------------------------------------------------------------------
def test_simulate_window_runs_a_real_page_on_the_fake(big):
    _app()
    from PySide6 import QtWidgets
    from ctgedit.io.reader import read_page
    from ctgedit.model.document import ObjectKind
    from ctgedit.ui import help as H
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.ui.simulate.window import SimulateWindow

    clk = _Clock()
    win = SimulateWindow()
    try:
        page = read_page(big)
        sim = FakeSimulation(clock=clk)
        sp = win.open_page(big, page=page, sim=sim, demo=True)
        win.show()
        QtWidgets.QApplication.processEvents()
        assert win.tabs.count() == 1 and win.tabs.tabText(0) == "FxSimBig"
        assert win.note.isVisibleTo(win)
        assert sp.page is not page  # a copy: editor edits never reach it
        assert sp.scene.sim_layer().runtime_look()
        # a text the demo animates shows a value, not its mask
        a = next(
            a
            for a in sim._anim
            if a.text and a.index in sp.scene._items and sp.page.objects[a.index].kind is ObjectKind.TEXT
        )
        item = sp.scene._items[a.index]
        for _k in range(4):
            clk.t += 0.5
            win.tick()
        assert sp.scans >= 3 and sim.clock_state().scan == 8
        assert item.text() == sp.scene.sim_states()[a.an].text
        assert "Scan 8" in win.clock.status.text()
        assert not win.clock.act_run.isEnabled() and win.clock.act_pause.isEnabled()
        win.clock.act_pause.trigger()
        assert win.clock.act_step.isEnabled()
        win.clock.act_step.trigger()
        assert sim.clock_state().scan == 9
        win.clock.speed.setCurrentIndex(win.clock.speed.findData(5.0))
        assert sim.clock_state().speed == 5.0
        # the panels follow the tab
        assert win.inputs.model.rowCount() > 10
        win.tabs.currentWidget().canvas.grab()
        assert H.topic_for(win.tabs) == "simulate"
        assert H.topic_for(win.inputs) == "simulate#inputs"
        # the same page again = its tab
        assert win.open_page(big) is sp and win.tabs.count() == 1
        win.close_tab(0)
        assert win.tabs.count() == 0 and not win._timer.isActive()
    finally:
        win.close()
        win.deleteLater()
        QtWidgets.QApplication.processEvents()
        gc.collect()


def test_inputs_panel_groups_searches_and_writes():
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate.panels import InputsPanel
    from ctgedit.ui.simulate.sources import FakeSimulation

    sim = FakeSimulation(clock=_Clock(), tags=_tags())
    sim.start("", _rect_page())
    p = InputsPanel()
    try:
        p.set_source(sim)
        m = p.model
        top = sorted(m.item(r, 0).text() for r in range(m.rowCount()))
        assert top == ["Message", "Pump1", "Tank1"]  # equipment groups + loose tags
        assert "3 tags" in p.count.text()
        p.page_only.setChecked(False)
        assert sorted(m.item(r, 0).text() for r in range(m.rowCount())) == [
            "Flow",
            "Message",
            "Pump1",
            "Tank1",
        ]
        p.search.setText("level")
        p.search.flush()
        assert m.rowCount() == 1 and m.item(0, 0).text() == "Tank1"
        p.search.setText("")
        p.search.flush()
        # a number: the slider covers its range
        assert p.select_tag("Tank1.Level")
        assert p.stack.currentIndex() == 0 and p.editor_box.isEnabled()
        lvl = next(t for t in sim.tags() if t.name == "Tank1.Level")
        assert p.slider.value() == round(1000 * lvl.value / 200.0)
        p.slider.setValue(750)
        assert lvl.value == 150.0 and p.spin.value() == 150.0
        p.spin.setValue(25.0)
        assert lvl.value == 25.0 and p.slider.value() == 125
        assert p._rows["Tank1.Level"][0].text() == "25"
        # a digital tag: the check box; a double-click switches it
        assert p.select_tag("Pump1.Run")
        assert p.stack.currentWidget() is p.toggle
        run = next(t for t in sim.tags() if t.name == "Pump1.Run")
        was = run.value
        p.toggle.setChecked(not was)
        assert run.value == (0 if was else 1)
        p._toggle_digital(None)
        assert run.value == was
        # a string: Enter sets it; the quality menu
        assert p.select_tag("Message")
        p.text.setText("Hello")
        p.text.returnPressed.emit()
        msg = next(t for t in sim.tags() if t.name == "Message")
        assert msg.value == "Hello"
        p.quality.setCurrentText("Bad")
        assert msg.quality == "Bad" and p._rows["Message"][1].text() == "Bad"
        writes = [e for e in sim.drain() if e.kind == "write"]
        assert len(writes) >= 5
    finally:
        p.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_outputs_panel_lists_filters_and_counts():
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate.panels import OutputsPanel
    from ctgedit.ui.simulate.sources import LogEntry

    p = OutputsPanel()
    try:
        p.add_entries(
            [
                LogEntry(0.25, "write", "Tank1.Level = 3"),
                LogEntry(0.5, "log", "Hello", function="Main", file="demo.ci", line=12),
                LogEntry(0.5, "not_simulated", "SQLConnect is not simulated", function="SQLConnect", count=1),
                LogEntry(
                    0.75, "not_simulated", "SQLConnect is not simulated", function="SQLConnect", count=2
                ),
                LogEntry(1.0, "error", "Divide by zero"),
            ]
        )
        from ctgedit.ui.simulate.panels import COL_COUNT, COL_KIND, COL_WHERE

        p.group_box.setChecked(True)
        rows = p.entries()
        # (Group repeats: the two SQLConnect lines are one row that counts 2)
        assert [r.text(COL_KIND) for r in rows] == [
            "Tag write",
            "Cicode message",
            "Not simulated",
            "Simulator",
        ]
        assert rows[2].text(COL_COUNT) == "2"
        assert rows[1].text(COL_WHERE) == "Main, demo.ci:12"
        assert p.not_sim.topLevelItemCount() == 1 and p.not_sim.topLevelItem(0).text(1) == "2"
        # only the errors: warnings (not simulated) and info lines off
        p.set_severity_shown("warning", False)
        p.set_severity_shown("info", False)
        assert [r.isHidden() for r in rows] == [True, True, True, False]
        p.show_all()
        assert not any(r.isHidden() for r in rows)
        p.group_box.setChecked(False)
        assert [r.text(COL_KIND) for r in p.entries()] == [
            "Tag write",
            "Cicode message",
            "Not simulated",
            "Not simulated",
            "Simulator",
        ]
        p.clear()
        assert not p.entries() and p.not_sim.topLevelItemCount() == 0
    finally:
        p.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_page_view_sends_clicks_drags_and_keys():
    _app()
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtTest import QTest
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.ui.simulate.window import SimulateWindow

    win = SimulateWindow()
    try:
        sim = FakeSimulation(clock=_Clock(), tags=_tags())
        sp = win.open_page("", page=_rect_page(), sim=sim, run=False)
        win.show()
        QtWidgets.QApplication.processEvents()
        cv = sp.canvas
        cv.set_zoom(1.0)
        cv.centerOn(140, 130)
        QtWidgets.QApplication.processEvents()
        vp = cv.viewport()
        at = cv.mapFromScene(QtCore.QPointF(140, 130))
        QTest.mousePress(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, at)
        move = QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove,
            QtCore.QPointF(at + QtCore.QPoint(15, 4)),
            QtCore.QPointF(vp.mapToGlobal(at)),
            QtCore.Qt.LeftButton,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoModifier,
        )
        QtWidgets.QApplication.sendEvent(vp, move)
        QTest.mouseRelease(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, at + QtCore.QPoint(15, 4))
        kinds = [(e.kind, e.an) for e in sim.inputs]
        assert kinds == [("down", 11), ("drag", 11), ("up", 11)]
        drag = sim.inputs[1]
        assert abs(drag.dx - 15) < 1e-6 and abs(drag.dy - 4) < 1e-6
        assert abs(sim.inputs[0].x - 140) <= 1 and abs(sim.inputs[0].y - 130) <= 1
        # a click on the empty page has no AN
        QTest.mouseClick(
            vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, cv.mapFromScene(QtCore.QPointF(20, 20))
        )
        assert sim.inputs[-1].kind == "up" and sim.inputs[-1].an is None
        cv.setFocus()
        QTest.keyClick(cv, QtCore.Qt.Key_F2)
        assert sim.inputs[-1].kind == "key" and sim.inputs[-1].key == "F2"
        # the release ran the object's command: its functions are listed as not simulated
        # (the panels follow at most every PANEL_PERIOD_S: the window's own timer may have
        # just refreshed them)
        from test_sim_engine_source import wait_for

        assert wait_for(
            lambda: (win.tick(), win.outputs.not_sim.topLevelItemCount() == 2)[1],
            pump=QtWidgets.QApplication.processEvents,
        )
        # the object's tool tip: an animated one wins over its own
        sp.scene.apply_states(
            {11: __import__("ctgedit.sim.state", fromlist=["x"]).ObjectState(11, tooltip="Running")}
        )
        assert cv.tooltip_at(at) == "Running"
    finally:
        win.close()
        win.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_hover_never_deletes_items_while_states_apply(big):
    """The canvas hit test reads PAGE_INDEX_ROLE only (render.scene): hovering every item of a
    page with states applied -- swapped symbols, level overlays, greyed groups -- deletes
    nothing (PySide6 6.11 deletes a top-level item reached with parentItem())."""
    _app()
    import shiboken6
    from PySide6 import QtCore, QtGui, QtWidgets
    from ctgedit.sim.state import DISABLE_GRAY_PART, ObjectState
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.ui.simulate.window import SimulateWindow

    win = SimulateWindow()
    try:
        sp = win.open_page(big, sim=FakeSimulation(clock=_Clock()), run=False)
        win.resize(1500, 900)
        win.show()
        QtWidgets.QApplication.processEvents()
        sc, page = sp.scene, sp.page
        states = {}
        for i, o in enumerate(page.objects):
            if o.an <= 0 or o.an in states:
                continue
            if o.raw.get("_type_code") == 0x0C and o.members:
                states[o.an] = ObjectState(o.an, symbol_state=1)
            elif o.kind.value == "rectangle":
                states[o.an] = ObjectState(o.an, fill_level=0.5, fill_level_colour=0x808080)
            elif o.kind.value == "group":
                states[o.an] = ObjectState(o.an, disabled=True, disable_style=DISABLE_GRAY_PART)
        sc.apply_states(states)
        layer = sc.sim_layer()
        swaps = [it for it in layer._swap_active.values() if it is not None]
        assert len(swaps) > 10
        before = len(sc.items())
        cv = sp.canvas
        cv.fit_page()
        targets = [it.sceneBoundingRect().center() for it in sc._items.values()]
        targets += [it.sceneBoundingRect().center() for it in swaps]
        vpw = cv.viewport()
        for c in targets:
            vp = cv.mapFromScene(c)
            ev = QtGui.QMouseEvent(
                QtCore.QEvent.MouseMove,
                QtCore.QPointF(vp),
                QtCore.QPointF(vpw.mapToGlobal(vp)),
                QtCore.Qt.NoButton,
                QtCore.Qt.NoButton,
                QtCore.Qt.NoModifier,
            )
            QtWidgets.QApplication.sendEvent(vpw, ev)
            cv.tooltip_at(vp)
            cv.ans_at(vp)
        del targets
        gc.collect()
        QtWidgets.QApplication.processEvents()
        assert len(sc.items()) == before
        assert all(shiboken6.isValid(it) for it in swaps)
        sc.clear_states()
        assert all(shiboken6.isValid(it) and not it.isVisible() for it in swaps)
    finally:
        win.close()
        win.deleteLater()
        QtWidgets.QApplication.processEvents()
        gc.collect()


def test_editor_tools_simulate_page_opens_the_page(big):
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.simulate import window as SW

    w = MainWindow()
    try:
        w.open_document(big)
        assert w.act_simulate.isEnabled()
        tools = next(m for m in w.menuBar().findChildren(QtWidgets.QMenu) if m.title() == "&Tools")
        assert w.act_simulate in tools.actions()
        w.act_simulate.trigger()
        sw = SW.simulate_window(create=False)
        assert sw is not None and sw.find(big) is not None
        assert sw.current().page_path == big
        # the editor draws CtDraw's literal labels; the simulated page the runtime's
        from ctgedit.io import reader

        if reader.get_label_mode() == "literal":
            assert any("@(" in (o.style.text or "") for o in w.doc.page.objects)
        assert not any("@(" in (o.style.text or "") for o in sw.current().page.objects)
        sw.close()
    finally:
        w.doc = None
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.close()
        QtWidgets.QApplication.processEvents()
        gc.collect()


def test_simulated_page_shows_runtime_labels_not_the_editors_literal_text(big):
    """The editor's literal label mode draws the Graphics Builder's text ('="@(Login...)"',
    '@(...)'); the runtime draws the native string. A simulated page is derived again in the
    runtime's mode (from the editor's records, unsaved edits included), the editor's mode is
    left as it was."""
    _app()
    from PySide6 import QtWidgets
    from ctgedit.format.page import parse_ctg
    from ctgedit.io import reader
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.ui.simulate.window import SimulateWindow, runtime_label_mode, runtime_page

    def literal(page):
        return [
            o.style.text
            for o in page.objects
            if o.style.text and ("@(" in o.style.text or o.style.text.startswith("="))
        ]

    old = reader.get_label_mode()
    reader.set_label_mode("literal")
    win = SimulateWindow()
    try:
        assert runtime_label_mode() == "native"
        editor_page = reader.read_page(big)
        assert literal(editor_page)  # the editor's look
        assert not literal(runtime_page(big))
        assert reader.get_label_mode() == "literal"  # put back
        sp = win.open_page(
            big, page=editor_page, parsed=parse_ctg(big), sim=FakeSimulation(clock=_Clock()), run=False
        )
        assert not literal(sp.page) and sp.page is not editor_page
        reader.set_label_mode("German")
        assert runtime_label_mode() == "German"  # a language the editor shows
    finally:
        reader.set_label_mode(old)
        win.close()
        win.deleteLater()
        QtWidgets.QApplication.processEvents()
        gc.collect()


def test_closing_the_simulate_window_keeps_it_for_the_next_page():
    """A closed Simulate window is hidden and shown again by the next Simulate Page."""
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate import window as SW
    from ctgedit.ui.simulate.sources import FakeSimulation

    sp = SW.open_simulation("", page=_rect_page(), sim=FakeSimulation(clock=_Clock()))
    w = SW.simulate_window(create=False)
    try:
        assert w is not None and w.tabs.count() == 1
        w.close()
        QtWidgets.QApplication.processEvents()
        assert w.tabs.count() == 0 and not w._timer.isActive()
        assert SW.simulate_window(create=False) is w
        SW.open_simulation("", page=_rect_page(), sim=FakeSimulation(clock=_Clock()))
        assert SW.simulate_window() is w and w.isVisible() and w.tabs.count() == 1
        assert len(SW._WINDOWS) == 1
    finally:
        w.close()
        QtWidgets.QApplication.processEvents()
        del sp
        gc.collect()


def test_a_stacked_label_copy_the_editor_drops_is_drawn_in_the_simulate_look():
    # a tab genie's two captions: the same text at the same place, one hidden when its page
    # is shown and one when it is not. The editor draws only the topmost (it cannot run the
    # gates); the Simulate look builds both, so the copy the simulator shows is there
    _app()
    from ctgedit.io import reader
    from ctgedit.model.document import Geometry, GraphicObject, ObjectKind, Page, Visibility
    from ctgedit.render.scene import PageScene
    from ctgedit.sim.state import ObjectState

    def page():
        p = Page(name="tabs")
        p.appearance.width, p.appearance.height = 400, 300
        for an, op in ((21, "="), (22, "<>")):
            o = GraphicObject(
                kind=ObjectKind.TEXT, an=an, geometry=Geometry(x=100, y=100, extent_x=60, extent_y=16)
            )
            o.style.text = "Filter 2"
            o.raw["_type_code"] = 0x05
            o.animations.visibility = Visibility(expression='PageInfo(0) %s "F2"' % op)
            p.objects.append(o)
        reader._suppress_overlapping_duplicates(p)
        return p

    p = page()
    assert p.objects[0].raw.get("_dup_suppressed") and not p.objects[1].raw.get("_dup_suppressed")
    editor = PageScene()
    editor.set_page(p)
    assert 0 not in editor._items and 1 in editor._items
    sim = PageScene()
    sim.set_runtime_look(True)
    sim.set_page(page())
    assert 0 in sim._items and 1 in sim._items
    # the simulator hides the topmost: the other copy shows
    sim.apply_states({22: ObjectState(22, shown=False)})
    assert sim._items[0].isVisible() and not sim._items[1].isVisible()


def test_edit_in_editor_opens_the_object_in_the_editor(big):
    """Shift + right-click's Edit in Editor (SimulateWindow.edit_in_editor): the page shown in
    the editor window the simulation came from, the object selected and its sheet open."""
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.objprops.sheet import ObjectPropertiesDialog
    from ctgedit.ui.simulate import window as SW

    w = MainWindow()
    try:
        w.open_document(big)
        w.act_simulate.trigger()
        sw = SW.simulate_window(create=False)
        assert sw is not None and sw.current() is not None
        sp = sw.current()
        # a text object with an AN: its sheet opens
        pick = next(
            i
            for i, o in enumerate(w.doc.page.objects)
            if o.an > 0 and o.kind.value == "text" and o.parent is None
        )
        an = w.doc.page.objects[pick].an
        assert sw.edit_in_editor(sp, an)
        assert w._sel == [pick]
        sheets = [
            d
            for d in QtWidgets.QApplication.topLevelWidgets()
            if isinstance(d, ObjectPropertiesDialog) and d.isVisible()
        ]
        assert len(sheets) == 1
        for d in sheets:
            d.close()
        # Show in Editor: selected, no sheet
        assert sw.edit_in_editor(sp, an, sheet=False)
        assert w._sel == [pick]
        assert not [
            d
            for d in QtWidgets.QApplication.topLevelWidgets()
            if isinstance(d, ObjectPropertiesDialog) and d.isVisible()
        ]
        # an object that is not on the page in the editor: said, nothing opens
        assert not sw.edit_in_editor(sp, 999999)
        assert "not on the page" in sw.statusBar().currentMessage()
        # the Outputs line's Edit in Editor goes the same way
        from ctgedit.ui.simulate.sources import LogEntry

        assert sw.edit_log_object(LogEntry(1.0, "log", "x", an=an, page=sp.title()))
        for d in QtWidgets.QApplication.topLevelWidgets():
            if isinstance(d, ObjectPropertiesDialog):
                d.close()
    finally:
        sw = SW.simulate_window(create=False)
        if sw is not None:
            sw.shut_down()
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.close()


def test_record_follows_the_tab_that_records():
    """Scenario > Record shows the current tab's recording: page B never shows A's, A keeps
    its recording (and its step) when B is visited, and the recording tab says so."""
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate import sim_tools as T
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.ui.simulate.window import SimulateWindow

    class _Session:
        alive = True

        def request_async(self, *_a, **_k):
            pass

    class _Stub:
        def __init__(self):
            import threading

            self._lock = threading.Lock()
            self._session = _Session()
            self._window = 0
            self.time = 0.0
            self.project = "FxSim"
            self.page_name = "FxSim1"

        def clock_state(self):
            return self

        def _add_log(self, _e):
            pass

        def _now(self):
            return 0.0

    def _sim():
        sim = FakeSimulation(clock=_Clock(), tags=_tags())
        sim._session, sim._add_log = _Session(), (lambda _e: None)
        stub = _Stub()
        sim._tools = T.SimTools(stub)
        return sim

    win = SimulateWindow()
    try:
        a = win.open_page("", page=_rect_page(), sim=_sim(), run=False)
        b = win.open_page("", page=_rect_page(), sim=_sim(), run=False)
        win.tabs.setCurrentWidget(a)
        win.clock.act_record.setChecked(True)
        ta = a.sim._tools
        assert ta.recording and "(recording)" in win.tabs.tabText(win.tabs.indexOf(a))
        ta.note_write("Level", 5.0, "Good")
        win.tabs.setCurrentWidget(b)
        assert not win.clock.act_record.isChecked()
        assert "(recording)" not in win.tabs.tabText(win.tabs.indexOf(b))
        win.tabs.setCurrentWidget(a)
        assert win.clock.act_record.isChecked() and ta.recording
        saved = []
        win.save_scenario = lambda sc: saved.append(sc) or "rec.json"
        win.clock.act_record.setChecked(False)
        assert not ta.recording and saved and len(saved[0]["steps"]) == 1
        assert "(recording)" not in win.tabs.tabText(win.tabs.indexOf(a))
    finally:
        win.close()
        win.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_a_stopped_recording_is_only_lost_on_discard(monkeypatch):
    """Stop with the save cancelled (or failed): asked again; Save… saves it, Discard
    drops it."""
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate.window import SimulateWindow

    class _Tools:
        recording = True

        def available(self):
            return True

        def stop_recording(self):
            self.recording = False
            return {"steps": [{"at": 0.0, "set": {"Level": 1}}]}

    win = SimulateWindow()
    try:
        tools = _Tools()
        monkeypatch.setattr(win, "_sim_tools", lambda say=True: tools)
        monkeypatch.setattr(win, "_sync_record", lambda: None)
        answers = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: None)
        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "clickedButton",
            lambda self, pick=lambda t, bs: next(b for b in bs if b.text() == t): pick(
                answers.pop(0), self.buttons()
            ),
        )
        tries = []
        results = [None, "rec.json"]
        win.save_scenario = lambda sc: tries.append(sc) or results.pop(0)
        answers.append("Save…")
        win._record_toggled(False)
        assert len(tries) == 2 and not answers
        # cancelled, then Discard: one try, dropped
        tools.recording = True
        tries.clear()
        win.save_scenario = lambda sc: tries.append(sc)
        answers.append("Discard")
        win._record_toggled(False)
        assert len(tries) == 1 and not answers
    finally:
        win.close()
        win.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_an_empty_outputs_panel_stays_compact_until_lines_come():
    """Empty, Outputs takes only what its controls need; the first lines give it its usual
    height, once."""
    _app()
    from PySide6 import QtCore, QtWidgets

    from ctgedit.ui.simulate.sources import LogEntry
    from ctgedit.ui.simulate.window import SimulateWindow

    win = SimulateWindow()
    try:
        win.show()
        QtWidgets.QApplication.processEvents()
        dock = win.dock_outputs
        assert abs(dock.height() - win._outputs_compact) <= 4
        win.outputs.add_entries([LogEntry(0.25, "write", "Tank1.Level = 3")])
        QtWidgets.QApplication.processEvents()
        assert dock.height() >= win._outputs_usual - 4
        # once: a hand-made height stays after that
        win.resizeDocks([dock], [win._outputs_compact + 30], QtCore.Qt.Vertical)
        QtWidgets.QApplication.processEvents()
        h = dock.height()
        win.outputs.add_entries([LogEntry(0.5, "write", "Tank1.Level = 4")])
        QtWidgets.QApplication.processEvents()
        assert dock.height() == h
    finally:
        win.close()
