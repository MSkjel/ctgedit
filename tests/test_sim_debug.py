"""Debugging a simulated page."""

from test_simulate_tab import _Clock, _app, _rect_page, _tags
from ctgedit.ui.simulate import panels as P

_BODY = {
    "level": "error",
    "text": "Hardware error (424) 'Tag not found' ErrPage: 'TagRead'",
    "code": 424,
    "call": 'TagRead("NoSuch", 0, "")',
    "function": "GetVal",
    "file": r"C:\proj\Lib\lib.ci",
    "line": 12,
    "stack": [
        {"function": "GetVal", "file": r"C:\proj\Lib\lib.ci", "line": 12, "args": {"sTag": '"NoSuch"'}},
        {"function": "ShowIt", "file": r"C:\proj\Lib\show.ci", "line": 40},
        {"function": "<expression>"},
    ],
    "window": 0,
    "page": "sim_ui",
    "an": 11,
    "source": "visible",
    "task": 7,
}


def test_log_entry_keeps_the_traceback_and_the_object():
    from ctgedit.ui.simulate.ctsim_source import log_entry
    from ctgedit.ui.simulate.panels import details_text, where_text

    e = log_entry(1.0, _BODY)
    assert e.kind == "error" and e.level == "error" and e.code == 424
    # (the line as an engineer reads it; the simulator's own text is kept for the details)
    assert e.text == "Tag not found (424) in TagRead" and e.call == 'TagRead("NoSuch", 0, "")'
    assert e.raw.startswith("Hardware error (424) 'Tag not found'")
    assert e.stack[1] == ("ShowIt", r"C:\proj\Lib\show.ci", 40, ())
    assert e.stack[0][3] == (("sTag", '"NoSuch"'),)
    assert (e.window, e.page, e.an, e.source) == (0, "sim_ui", 11, "visible")
    d = details_text(e, lambda an, src: ['VISIBLE = GetVal("NoSuch")'] if an == 11 else [])
    assert 'Call: TagRead("NoSuch", 0, "")' in d
    assert "Traceback (most recent call first):" in d
    assert '  GetVal(sTag="NoSuch")    lib.ci line 12' in d
    assert "  ShowIt    show.ci line 40" in d
    assert "Object: AN 11 on sim_ui, window 0, visible" in d
    assert '  VISIBLE = GetVal("NoSuch")' in d
    # page code without a function: the column names the object
    e2 = log_entry(1.0, {"level": "warning", "text": "x", "an": 5, "source": "touch.down", "page": "P"})
    assert where_text(e2) == ("AN 5 touch.down", "AN 5 touch.down on P")
    # an old simulator: just the text
    e3 = log_entry(1.0, {"level": "info", "text": "hello"})
    assert e3.text == "hello" and e3.stack == () and e3.an == -1 and e3.call == ""


def test_errors_marked_on_the_page_and_show_on_page():
    _app()
    from PySide6 import QtCore, QtWidgets
    from ctgedit.ui.simulate.ctsim_source import log_entry
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.ui.simulate.window import SimulateWindow

    win = SimulateWindow()
    try:
        sim = FakeSimulation(clock=_Clock(), tags=_tags())
        sp = win.open_page("", page=_rect_page(), sim=sim, run=False)
        win.show()
        QtWidgets.QApplication.processEvents()
        e = log_entry(1.0, _BODY)
        other = log_entry(1.0, dict(_BODY, page="AnotherPage"))
        info = log_entry(1.0, dict(_BODY, level="info"))
        sp._mark_errors([e, e, other, info])
        assert sp.canvas.errors() == {11: (2, e.text, "error")}
        # the mark draws (a grab paints drawForeground) and the tool tip says it
        assert not sp.canvas.grab().isNull()
        at = sp.canvas.mapFromScene(QtCore.QPointF(140, 130))
        tip = sp.canvas.tooltip_at(at)
        assert "AN 11: 2 errors. Last: Tag not found (424)" in tip
        sp.canvas.show_numbers = True
        assert "Objects here: AN 11" in sp.canvas.tooltip_at(at)
        # the toggle hides the marks
        win.clock.act_errors.setChecked(False)
        assert not sp.canvas.show_errors
        assert "errors. Last" not in sp.canvas.tooltip_at(at)
        win.clock.act_errors.setChecked(True)
        # Outputs: the details and Show on Page
        win.outputs.add_entries([e])
        win.outputs.log.setCurrentItem(win.outputs.log.topLevelItem(0))
        assert win.outputs.show_button.isEnabled()
        assert "Traceback" in win.outputs.details.toPlainText()
        assert win.outputs.log.topLevelItem(0).text(P.COL_CALL) == 'TagRead("NoSuch", 0, "")'
        # the page's own expression of the object (the rect's MVX / COL_A / CMD)
        assert "MVX = (Tank1.Level)^(0,200)" in win.outputs.details.toPlainText()
        win.outputs.show_button.click()
        assert sp.canvas._shown_an == 11
        assert not win.show_log_object(log_entry(1.0, dict(_BODY, an=999)))
        # the log emptied to be filled again (a tab change): the marks stay; the user's Clear
        # takes them too, so the page shows what the log does; the error again marks again
        win.outputs.clear()
        assert sp.canvas.errors()
        win.outputs.add_entries([e])
        not_sim = win.outputs.not_sim.topLevelItemCount()
        win.outputs.clear_button.click()
        assert sp.canvas.errors() == {} and win.outputs.log.topLevelItemCount() == 0
        # (the Not simulated list, another tab, stays)
        assert win.outputs.not_sim.topLevelItemCount() == not_sim
        assert win.outputs.clear_button.text() == "Clear Log"
        sp._mark_errors([e])
        assert sp.canvas.errors() == {11: (1, e.text, "error")}
        # a new page in the tab: its marks go
        sp.canvas.clear_errors()
        assert sp.canvas.errors() == {}
    finally:
        win.close()
        win.deleteLater()


_PAUSED = {
    "paused": True,
    "task": 12,
    "function": "fillLevel",
    "file": r"C:\proj\Test\Genie.ci",
    "line": 18,
    "reason": "breakpoint",
    "window": 0,
    "an": 44,
    "page": "FxSim1",
    "source": "fill_level",
}


def test_debug_pause_where_names_the_function_line_and_object():
    from ctgedit.ui.simulate.ctsim_source import debug_pause_where

    assert debug_pause_where(_PAUSED) == "fillLevel, Genie.ci:18 (AN 44 on FxSim1)"
    assert debug_pause_where({"function": "Startup", "file": "a.ci"}) == "Startup, a.ci"
    assert debug_pause_where({}) == "page code"


def test_a_debugger_pause_holds_the_tabs_requests_and_shows_over_the_page():
    """ctsim/debugPaused: page code the debugger stopped holds the whole
    simulation; the source sends nothing it would wait for, the tab says where it stopped,
    and both end with paused false."""
    _app()
    from ctgedit.ui.simulate import window as W
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.sources import FakeSimulation

    sim = EngineSimulation(fake=FakeSimulation(clock=_Clock()))
    sp = W.SimulatedPage("FxSim1.ctg", page=_rect_page(), sim=sim)
    try:
        on_event = sim._event_listener(sim._gen)
        assert sim.debug_paused() is None
        on_event("ctsim/debugPaused", dict(_PAUSED))
        assert sim.debug_paused()["line"] == 18
        with sim._lock:
            texts = [e.text for e in sim._log]
        assert any(t.startswith("Paused by the debugger in fillLevel, Genie.ci:18") for t in texts)
        assert sim._held_by_debugger("a tag write")
        sp._show_debug_pause()
        lab = sp._pause_overlay
        assert not lab.isHidden() and "fillLevel, Genie.ci:18 (AN 44 on FxSim1)" in lab.text()
        on_event("ctsim/debugPaused", {"paused": False, "task": 12})
        assert sim.debug_paused() is None and not sim._held_by_debugger("a tag write")
        sp._show_debug_pause()
        assert lab.isHidden()
    finally:
        sp.stop()
        sp.deleteLater()


def test_a_slider_drag_is_one_log_line():
    """A tag written many times in a row (a slider drag) is one 'write' line once it settles,
    with the count and the first value."""
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.sources import FakeSimulation

    sim = EngineSimulation(fake=FakeSimulation(clock=_Clock()))
    for v in (0.0, 2.0, 4.0, 68.0):
        sim._note_write("Fx_11_LT01_pvAnalogHMI", v, "Good")
    sim._note_write("Pump1.Run", 1, "Good")
    with sim._lock:
        assert not [e for e in sim._log if e.kind == "write"]  # not settled yet
    sim._flush_writes(everything=True)
    with sim._lock:
        texts = [e.text for e in sim._log if e.kind == "write"]
    assert texts == ["Fx_11_LT01_pvAnalogHMI = 68.0 (4 writes, from 0.0)", "Pump1.Run = 1"]
