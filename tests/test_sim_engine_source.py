"""The Simulate window on the simulator, against the fake server."""

import gc
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402
from ctgedit.ui.simulate import panels as P  # noqa: E402
from qtkit import app as _app  # noqa: E402
from timing import check_speed

pytestmark = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")


@pytest.fixture
def servers(tmp_path, monkeypatch):
    """The discovery folder of the fake servers. The client never finds a real engine: the
    repository root it searches for a built ctsim.exe is an empty folder and the engine path
    setting is unset, so a test that falls through gets 'no engine', never the real one."""
    folder = str(tmp_path / "servers")
    monkeypatch.setenv(C.SERVERS_ENV, folder)
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)  # these tests use the (fake) engine
    monkeypatch.setattr(C, "repo_root", lambda: str(tmp_path / "norepo"))
    monkeypatch.delenv(C.PATH_ENV, raising=False)
    return folder


@pytest.fixture
def page_file(tmp_path):
    proj = tmp_path / "User" / "Proj"
    proj.mkdir(parents=True)
    path = proj / "Mixer.ctg"
    path.write_bytes(b"")
    return str(path)


@pytest.fixture
def hub():
    h = C.Hub(linger=0)
    yield h
    h.shutdown()


def _rect_page():
    from ctgedit.model.document import Geometry, GraphicObject, ObjectKind, Page

    p = Page(name="Mixer")
    p.appearance.width, p.appearance.height = 400, 300
    p.appearance.background_colour = 0xFFFFFF
    o = GraphicObject(
        kind=ObjectKind.RECTANGLE, an=11, geometry=Geometry(x=100, y=100, extent_x=80, extent_y=60)
    )
    o.style.filled, o.style.fill_colour, o.style.line_colour = True, 0xFF0000, 0xFF0000
    o.raw["_type_code"] = 0x0F
    o.raw["_props"] = {"MVX": "(Tank1.Level)^(0,200)", "COL_A": "Pump1.Run"}
    p.objects = [o]
    return p


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _sim(hub, demo=True, **kw):
    """(these tests check the demo values too: the developer option demo_values on; the
    default, the page as it is saved, is test_no_engine_shows_the_page_as_saved)"""
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.sources import FakeSimulation

    sim = EngineSimulation(hub=hub, fake=FakeSimulation(clock=_Clock()), **kw)
    sim.demo_values = demo
    return sim


def wait_for(cond, timeout=5.0, pump=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pump is not None:
            pump()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_no_engine_keeps_the_demo_and_says_why(tmp_path, servers, hub, page_file, monkeypatch):
    monkeypatch.setattr(C, "repo_root", lambda: str(tmp_path / "norepo"))
    monkeypatch.delenv(C.PATH_ENV, raising=False)
    sim = _sim(hub)
    sim.start(page_file, _rect_page())
    assert wait_for(lambda: not sim.status().busy)
    st = sim.status()
    assert st.demo and st.engine_missing and st.can_retry and not st.connected
    assert "ctsim.exe" in st.text and "Looked in" in st.detail
    first = sim.poll()
    assert first is not None and 11 in first.objects  # the demo draws the page
    assert {t.name for t in sim.tags()} >= {"Tank1.Level", "Pump1.Run"}  # the demo's
    sim.stop()


def test_no_engine_shows_the_page_as_saved(tmp_path, servers, hub, page_file, monkeypatch):
    """Without a simulator (and the developer option demo_values off, the default) the page
    shows as it is saved: no made-up values, no made-up tags."""
    monkeypatch.setattr(C, "repo_root", lambda: str(tmp_path / "norepo"))
    monkeypatch.delenv(C.PATH_ENV, raising=False)
    sim = _sim(hub, demo=False)
    sim.start(page_file, _rect_page())
    assert wait_for(lambda: not sim.status().busy)
    st = sim.status()
    assert st.engine_missing and "shows as it is saved" in st.text
    assert sim.poll() is None
    assert not [t for t in sim.tags() if t.name in ("Tank1.Level", "Pump1.Run")]
    sim.stop()


def test_turned_off_and_unsaved_pages_stay_on_the_demo(hub, monkeypatch, page_file):
    monkeypatch.setenv(C.DISABLE_ENV, "1")
    sim = _sim(hub)
    sim.start(page_file, _rect_page())
    assert sim.status().demo and "turned off" in sim.status().text
    sim.stop()
    monkeypatch.delenv(C.DISABLE_ENV)
    sim = _sim(hub)
    sim.start("", _rect_page())
    assert "not saved in a project" in sim.status().text
    sim.stop()


def test_tags_writes_log_and_clock_from_a_server_without_pages(servers, hub, page_file):
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=False).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.tags_active() and not sim.status().busy)
        st = sim.status()
        assert st.connected and st.demo and "cannot run pages yet" in st.text
        assert "'Proj'" in st.text and "already running" in st.text
        # the page's own tags (ctsim/tags {page})
        rows, total = sim.search_tags("", page_only=True)
        assert [t.name for t in rows] == ["Tank1.Level", "Pump1.Run", "Message"]
        assert total == 3 and rows[0].eng_full == 200.0 and rows[0].page_reads
        assert any(cmd == "ctsim/tags" and a.get("page") == "Mixer" for _c, cmd, a in srv.requests)
        # every tag: the server searches, one page of up to 2000, the total said
        rows, total = sim.search_tags("tag01", page_only=False)
        assert wait_for(lambda: sim.search_tags("tag01")[1] == 100)
        rows, total = sim.search_tags("tag01")
        assert len(rows) == 100 and rows[0].name == "Tag0100"
        rows, total = sim.search_tags("", page_only=False)
        assert wait_for(lambda: sim.search_tags("")[1] == 1237)
        rows, total = sim.search_tags("")
        assert len(rows) == 1237
        # the values follow the server (subscribe + tagsChanged)
        assert wait_for(lambda: any(c.subscribed for c in srv.conns))
        serial = sim.serial()
        sim.set_tag("Tank1.Level", 77.0)
        assert wait_for(lambda: srv.by_name["tank1.level"]["value"] == 77.0)
        assert sim.serial() != serial
        entries = []
        # (a write is logged once the tag's writes settle: a slider drag is one line)
        assert wait_for(
            lambda: any(
                e.kind == "write" and e.text == "Tank1.Level = 77.0"
                for e in (entries.extend(sim.drain()) or entries)
            )
        )
        sim.set_tag("NoSuchTag", 1)
        assert wait_for(
            lambda: any(
                e.kind == "error" and "NoSuchTag" in e.text for e in (entries.extend(sim.drain()) or entries)
            )
        )
        assert any(e.text == "NoSuchTag was not set: error 51 Tag not found" for e in entries)
        # the clock bar drives the server's clock
        sim.clock("step")
        assert wait_for(lambda: sim.clock_state().scan == 1)
        assert srv.scan == 1 and sim.clock_state().time == 0.25
        assert wait_for(
            lambda: next(t for t in sim.search_tags("", True)[0] if t.name == "Tank1.Level").value == 78.5
        )
        sim.clock("speed", 2.0)
        assert wait_for(lambda: sim.clock_state().speed == 2.0) and srv.speed == 2.0
        # the server's log events reach the Outputs panel
        s = sim.session
        s.call("Add(1,2)")
        s.call("Beep(1)")
        assert wait_for(
            lambda: (
                len([e for e in (entries.extend(sim.drain()) or entries) if e.kind == "not_simulated"]) == 1
            )
        )
        kinds = [(e.kind, e.text) for e in entries]
        assert ("log", "Add returned 3.0") in kinds
        assert ("not_simulated", "Beep is not simulated") in kinds
        assert any(k == "log" and t == "Task 7 ended (Add)" for k, t in kinds)
        # clicks are not simulated without the page: said once, kept off the server
        from ctgedit.ui.simulate.sources import InputEvent

        sim.input(InputEvent("down", an=11))
        sim.input(InputEvent("down", an=11))
        notes = [e for e in sim.drain() if "Clicks and keys" in e.text]
        assert len(notes) == 1 and not srv.inputs
        assert not sim.page_active()  # still the demo states
    finally:
        sim.stop()
        srv.stop()


def test_the_page_switches_to_the_servers_states(servers, hub, page_file):
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(
        proj, servers, pages=True, page_objects={11: {"move": [5, 0], "fillColour": 0x0000FF}}
    ).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        states = [sim.poll()]  # the demo's, or already
        assert states[0] is not None  # the server's
        assert wait_for(lambda: not sim.status().demo)
        assert sim.page_active() and srv.windows == {0: "Mixer"}
        assert wait_for(
            lambda: (
                states.append(sim.poll())
                or any(st is not None and st.replace and 11 in st.objects for st in states)
            )
        )
        st = next(st for st in states if st is not None and st.replace)
        assert st.objects[11].move == (5.0, 0.0) and st.objects[11].fill_colour == 0x0000FF
        assert sim.poll() is None
        # a step sends pageChanged for the window
        sim.clock("step")
        assert wait_for(lambda: sim.clock_state().scan == 1)
        sim.clock("step")
        got = []
        assert wait_for(
            lambda: (
                got.append(sim.poll())
                or any(g is not None and g.objects.get(11) and g.objects[11].move == (2.0, 0.0) for g in got)
            )
        )
        assert not any(g is not None and g.replace for g in got)
        # input goes to the server with the window and the AN chain
        from ctgedit.ui.simulate.sources import InputEvent

        sim.input(InputEvent("down", an=11, ans=(11,), x=120, y=130))
        assert wait_for(lambda: srv.inputs)
        assert srv.inputs[0]["window"] == 0 and srv.inputs[0]["an"] == 11
        assert wait_for(lambda: any(e.kind == "not_simulated" for e in sim.drain()))
    finally:
        sim.stop()
        srv.stop()


def test_a_server_crash_goes_back_to_the_demo(servers, hub, page_file):
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: not sim.status().demo)
        while sim.poll() is not None:
            pass
        srv.crash()
        assert wait_for(lambda: sim.status().demo)
        st = sim.status()
        assert "stopped" in st.text and st.can_retry and not st.connected
        back = sim.poll()
        assert back is not None and back.replace and 11 in back.objects  # demo, whole set
        assert not sim.tags_active() and sim.tags()  # the demo's tags
        # Reconnect: no server now, and none can be started here
        sim.reconnect()
        assert wait_for(lambda: not sim.status().busy)
        assert sim.status().demo
    finally:
        sim.stop()
        srv.stop()


def test_a_started_server_runs_the_clock_bar_and_stops_with_the_tab(servers, hub, page_file):
    script = os.path.join(HERE, "fake_ctsim.py")
    py = getattr(sys, "_base_executable", "") or sys.executable
    loc = C.EngineLocation(script, "test", argv=[py, script, "--pages"])
    sim = _sim(hub, location=loc)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: not sim.status().demo, timeout=15)
        st = sim.status()
        assert "started by ctgedit" in st.text and st.engine == "ctsim 0.0-fake"
        srv = sim.session.server
        assert srv.running()
        # the window starts the clock: a server of our own follows the bar
        sim.clock("run")
        assert wait_for(lambda: sim.clock_state().running)
        assert wait_for(lambda: sim.clock_state().scan >= 2, timeout=5)
    finally:
        sim.stop()
    assert wait_for(lambda: not srv.running(), timeout=5)


def test_the_simulate_window_on_the_fake_server(servers, hub, page_file, monkeypatch):
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate.window import SimulateWindow

    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True, page_objects={11: {"fillColour": 0x0000FF}}).start()
    win = SimulateWindow()
    pump = QtWidgets.QApplication.processEvents
    try:
        sim = _sim(hub)
        sp = win.open_page(page_file, page=_rect_page(), sim=sim)
        win.show()
        assert wait_for(
            lambda: (
                win.tick(),
                sim.page_active()
                and sp.scene.sim_states().get(11) is not None
                and sp.scene.sim_states()[11].fill_colour == 0x0000FF,
            )[1],
            pump=pump,
        )
        # all is well: no line over the page, the status bar names the simulator
        assert wait_for(
            lambda: (win.tick(), "Cicode simulator for 'Proj'" in win.sim_status.text())[1], pump=pump
        )
        assert not win.status_row.isVisibleTo(win)
        assert not win.reconnect_button.isVisibleTo(win)
        # the Inputs panel lists the server's tags (the page's own; they come over a few ticks)
        assert wait_for(
            lambda: (win.tick(), {"Tank1.Level", "Pump1.Run", "Message"} <= set(win.inputs._tags))[1],
            pump=pump,
        )
        assert wait_for(lambda: (win.tick(), win.inputs.select_tag("Tank1.Level"))[1], pump=pump)
        # every tag: the panel shows the server's search
        win.inputs.page_only.setChecked(False)
        win.inputs.search.setText("tag012")
        win.inputs.search.flush()
        assert wait_for(lambda: (win.tick(), "Tag0129" in win.inputs._tags)[1], pump=pump)
        assert "10 tags" in win.inputs.count.text()
        win.inputs.search.setText("")
        win.inputs.search.flush()
        assert wait_for(
            lambda: (win.tick(), "Showing 1237" in win.inputs.count.text() or len(win.inputs._tags) == 1237)[
                1
            ],
            pump=pump,
        )
        # a write from the panel reaches the server
        win.inputs.page_only.setChecked(True)
        assert wait_for(lambda: (win.tick(), win.inputs.select_tag("Pump1.Run"))[1], pump=pump)
        win.inputs.toggle.setChecked(True)
        assert wait_for(lambda: srv.by_name["pump1.run"]["value"] == 1, pump=pump)
        # the clock bar drives the server's clock
        win.clock.act_pause.trigger() if win.clock.act_pause.isEnabled() else None
        assert wait_for(lambda: (win.tick(), win.clock.act_step.isEnabled())[1], pump=pump)
        win.clock.act_step.trigger()
        assert wait_for(lambda: srv.scan >= 1, pump=pump)
        assert wait_for(lambda: (win.tick(), "Scan %d" % srv.scan in win.clock.status.text())[1], pump=pump)
        # the Outputs panel gets the server's log lines
        sim.session.call("Add(2,5)")
        assert wait_for(
            lambda: (
                win.tick(),
                any(it.text(P.COL_MSG) == "Add returned 7.0" for it in win.outputs.entries()),
            )[1],
            pump=pump,
        )
        # the server stops: back to the demo, the status line says so and offers Reconnect
        srv.crash()
        assert wait_for(lambda: (win.tick(), "stopped" in win.note.text())[1], pump=pump)
        assert win.status_row.isVisibleTo(win)  # a problem: the line comes back
        assert win.reconnect_button.isVisibleTo(win)
        win.close_tab(0)
    finally:
        win.close()
        win.deleteLater()
        pump()
        srv.stop()
        gc.collect()


def test_the_window_offers_to_choose_the_simulator(tmp_path, servers, hub, page_file, monkeypatch):
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate import window as SW
    from ctgedit.ui.simulate.window import SimulateWindow

    monkeypatch.setattr(C, "repo_root", lambda: str(tmp_path / "norepo"))
    monkeypatch.delenv(C.PATH_ENV, raising=False)
    win = SimulateWindow()
    pump = QtWidgets.QApplication.processEvents
    try:
        sim = _sim(hub)
        win.open_page(page_file, page=_rect_page(), sim=sim)
        win.show()
        assert wait_for(lambda: (win.tick(), sim.status().engine_missing)[1], pump=pump)
        win.tick()
        assert win.engine_button.isVisibleTo(win) and win.reconnect_button.isVisibleTo(win)
        assert "Demo values" in win.note.text() and "Looked in" in win.note.toolTip()
        # choosing a ctsim.exe stores the setting and reconnects with it
        script = os.path.join(HERE, "fake_ctsim.py")
        exe = tmp_path / "eng" / C.EXE_NAME
        exe.parent.mkdir()
        exe.write_bytes(b"")
        (exe.parent / C.DLL_NAME).write_bytes(b"")
        seen = []
        orig = sim.reconnect
        sim.reconnect = lambda engine_path=None: (seen.append(engine_path), orig(engine_path))
        win.set_engine_path(str(exe))
        assert SW.engine_setting() == str(exe) and seen == [str(exe)]
        assert sim.engine_path == str(exe)
        win.set_engine_path("")
        assert SW.engine_setting() == "" and seen[-1] == ""
        del script
    finally:
        win.close()
        win.deleteLater()
        pump()
        gc.collect()


def test_event_bodies_become_log_lines(hub, monkeypatch):
    """The engine's event bodies (engine/PROTOCOL.md) as Outputs lines, read without a
    server: log levels, a halted task, a scenario's result, the clock."""
    monkeypatch.setenv(C.DISABLE_ENV, "1")
    sim = _sim(hub)
    sim.start("", _rect_page())
    sim.drain()
    on = sim._event_listener(sim._gen)
    on(
        "ctsim/log",
        {"level": "warning", "text": "load note", "function": "F", "file": "a.ci", "line": 4, "task": 2},
    )
    on("ctsim/log", {"level": "error", "text": "Hardware error (259)"})
    on(
        "ctsim/log",
        {
            "level": "error",
            "text": "Hardware error (424) 'Tag not found'",
            "call": 'TagRead("X", 0, "")',
            "code": 424,
        },
    )
    on(
        "ctsim/taskExited",
        {
            "task": 6,
            "name": "PageDisplay",
            "function": "PageDisplay",
            "state": "Halt",
            "haltCode": 259,
            "haltReason": "Array overrun",
        },
    )
    on("ctsim/notSimulated", {"function": "PageDisplay", "count": 3})
    on(
        "ctsim/scenarioFinished",
        {
            "passed": 1,
            "failed": 1,
            "skipped": 0,
            "errors": 0,
            "failures": [{"t": 3.0, "kind": "expect", "target": "WTP_T201_LT", "want": 99, "got": 85}],
        },
    )
    on("ctsim/clockChanged", {"mode": "run", "speed": 2.0, "ms": 1000, "t": 1.0})
    entries = sim.drain()
    lines = [(e.kind, e.text) for e in entries]
    assert ("log", "load note") in lines and ("error", "Hardware error (259)") in lines
    # the runtime's own text, the call kept beside it
    assert ("error", "Tag not found (424)") in lines  # (the plain reading of the hardware error)
    assert any(e.call == 'TagRead("X", 0, "")' for e in entries)
    assert ("error", "Task 6 ended (PageDisplay): Array overrun (hardware error 259)") in lines
    assert ("not_simulated", "PageDisplay is not simulated") in lines
    assert ("error", "The scenario ended: 1 passed, 1 failed, 0 skipped, 0 errors") in lines
    assert ("error", "Scenario: expect WTP_T201_LT wanted 99, got 85") in lines
    cs = sim.clock_state()
    assert cs.running and cs.speed == 2.0 and cs.time >= 1.0 and cs.scan >= 4
    sim.stop()


def test_another_page_in_this_window_is_not_drawn(servers, hub, page_file):
    """pageChanged / pageState carry the page's name (engine/PROTOCOL.md PAGE): states of
    another page in this tab's window (another tab of the same simulation opened its page
    there) are not drawn on this page; the tab goes back to the demo and says why."""
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        while sim.poll() is not None:
            pass
        w = sim._window
        on = sim._event_listener(sim._gen)
        on(
            "ctsim/pageChanged",
            {"window": w, "page": "mixer", "objects": [{"an": 11, "state": {"move": [9, 9]}}]},
        )  # the same page, other case
        st = sim.poll()
        assert st is not None and st.objects[11].move == (9.0, 9.0)
        on(
            "ctsim/pageChanged",
            {"window": w, "page": "Overview", "objects": [{"an": 11, "state": {"move": [50, 50]}}]},
        )
        assert not sim.page_active() and sim.status().demo
        assert "Overview" in sim.status().text and sim.status().can_retry
        back = sim.poll()
        assert back is not None and back.replace  # the demo, whole set
        assert back.objects.get(11) is None or back.objects[11].move != (50.0, 50.0)
    finally:
        sim.stop()
        srv.stop()


def test_a_stop_racing_the_connect_leaves_no_listener_and_no_window(servers, hub, page_file):
    """The tab stops (another thread) while its worker hooks up the session it got: the
    listener must not stay on the session (a shared one lives on) and no window is opened."""
    import threading

    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    sim = _sim(hub)
    keep = hub.acquire(proj)  # another user of the session
    orig_add = keep.conn.add_listener

    def add(fn):
        t = threading.Thread(target=sim.stop)  # the tab closes now
        t.start()
        t.join(0.3)
        orig_add(fn)

    keep.conn.add_listener = add
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim._stopped and sim.session is None, timeout=5)
        time.sleep(0.5)
        assert not keep.conn._listeners  # nothing left behind
        assert not keep.conn._close_listeners
        assert srv.windows == {} and keep.alive
    finally:
        keep.conn.add_listener = orig_add
        hub.release(keep)
        srv.stop()


def test_a_stopped_tab_is_not_kept_alive_by_a_shared_session(servers, hub, page_file):
    """Two tabs of one project share the session; the one that stops leaves no listener or
    close listener on it (its closures held the tab and its page model while the other tab
    kept the session)."""
    import weakref

    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=False).start()
    a, b = _sim(hub), _sim(hub)
    try:
        a.start(page_file, _rect_page())
        b.start(page_file, _rect_page())
        assert wait_for(lambda: a.tags_active() and b.tags_active())
        s = b.session
        assert s is a.session and len(s.conn._close_listeners) == 2
        a.stop()
        # (b's listener, and the session's viewer services while b uses the session)
        own = [
            fn for fn in s.conn._listeners if type(getattr(fn, "__self__", None)).__name__ != "ViewerServices"
        ]
        assert len(s.conn._close_listeners) == 1 and len(own) == 1
        assert len(s.conn._listeners) == 2
        # (the reader thread holds its copy of the listeners while it hands an event out, a's
        # among them if a stopped meanwhile: one request answered on the session means the
        # reader has handled every message before it, that copy included)
        s.request("tasks")
        ref = weakref.ref(a)
        a = None  # (released: the session must outlive it)
        gc.collect()
        assert ref() is None
        assert s.alive  # b still has it
    finally:
        b.stop()
        srv.stop()


def test_a_project_the_engine_refuses_says_why(servers, hub, page_file):
    """ctsim serve exits at once for a project that is not compiled; the status line gives
    the engine's own reason, not only 'ended at its start'."""
    script = os.path.join(HERE, "fake_ctsim.py")
    py = getattr(sys, "_base_executable", "") or sys.executable
    sim = _sim(hub, location=C.EngineLocation(script, "test", argv=[py, script, "--no-rdb"]))
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: not sim.status().busy, timeout=15)
        st = sim.status()
        assert st.demo and st.can_retry and not st.connected
        assert "not compiled" in st.text and "_LIBRARY.RDB" in st.detail
        assert "exit code 1" in st.detail
    finally:
        sim.stop()


def test_inf_and_nan_tag_values_show_in_the_inputs_panel():
    """A REAL tag can hold INF or NaN in the simulator (live: Pow(10, 400) gives INF, a
    scenario or another client can set NaN): the Inputs panel shows them without raising."""
    _app()
    from ctgedit.ui.simulate import panels as P
    from ctgedit.ui.simulate.sources import TagInfo

    class Src:
        rows = [
            TagInfo(name="A", type="REAL", value=float("nan")),
            TagInfo(name="B", type="REAL", value=float("inf"), eng_zero=0, eng_full=10),
            TagInfo(name="C", type="REAL", value=float("-inf")),
        ]

        def tags(self, page_only=False):
            return list(self.rows)

        def serial(self):
            return 1

        def set_tag(self, *a):
            pass

    p = P.InputsPanel()
    p.set_source(Src())
    assert [p._rows[n][0].text() for n in "ABC"] == ["NaN", "INF", "-INF"]
    for n in "ABC":
        assert p.select_tag(n)
    assert P._fmt(2.5) == "2.5" and P._fmt(3.0) == "3"
    t = C.normalise_tag({"name": "X", "engZero": float("-inf"), "engFull": float("nan")})
    assert t["eng_zero"] is None and t["eng_full"] is None
    p.deleteLater()


def test_the_inputs_panel_follows_thousands_of_changing_tags():
    """The Cicode simulator changes every subscribed tag each scan (up to the 2000 rows the
    panel shows): the columns are Interactive, so a setText never re-measures them."""
    import dataclasses

    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate import panels as P
    from ctgedit.ui.simulate.sources import TagInfo

    class Src:
        rows = [
            TagInfo(name="T%05d" % k, type="REAL", value=float(k), equipment="E%d" % (k // 50))
            for k in range(2000)
        ]
        s = 0

        def search_tags(self, text, page_only=False):
            return [dataclasses.replace(t) for t in self.rows], len(self.rows)

        def tags(self, page_only=False):
            return self.search_tags("", page_only)[0]

        def serial(self):
            return self.s

        def set_tag(self, *a):
            pass

    src = Src()
    p = P.InputsPanel()
    p.resize(400, 600)
    p.show()
    p.page_only.setChecked(False)
    p.set_source(src)
    QtWidgets.QApplication.processEvents()
    hdr = p.tree.header()
    t0 = time.perf_counter()
    for _k in range(3):
        src.s += 1
        for t in src.rows:
            t.value += 1000.125
        p.refresh()
        QtWidgets.QApplication.processEvents()
    took = (time.perf_counter() - t0) / 3
    check_speed(took, 1.0, "a refresh")
    # the columns are Interactive (ui/tables.py): a value change never re-measures them
    assert hdr.sectionResizeMode(1) == QtWidgets.QHeaderView.Interactive
    assert p._rows["T00000"][0].text() == "3000.375"
    p.deleteLater()


def test_a_new_simulator_choice_is_not_answered_by_the_old_server(servers, page_file):
    """Choose Simulator / reconnect(engine_path=...): a server ctgedit started with the
    previous ctsim.exe is let go, and another tab on it falls back to the demo and says so."""
    script = os.path.join(HERE, "fake_ctsim.py")
    py = getattr(sys, "_base_executable", "") or sys.executable
    loc = C.EngineLocation(script, "test", argv=[py, script, "--pages"])
    hub = C.Hub(linger=30.0)
    a, b = _sim(hub, location=loc), _sim(hub, location=loc)
    try:
        a.start(page_file, _rect_page())
        assert wait_for(lambda: a.page_active(), timeout=15)
        b.start(page_file, _rect_page())
        assert wait_for(lambda: b.page_active(), timeout=15)
        old = a.session.server
        assert b.session is a.session and old.running()
        a.reconnect(engine_path=r"C:\other\ctsim.exe")
        assert wait_for(lambda: not old.running(), timeout=10)
        assert wait_for(
            lambda: (
                a.page_active()
                and a.session is not None
                and a.session.server is not None
                and a.session.server.pid != old.pid
            ),
            timeout=15,
        )
        assert wait_for(lambda: b.status().demo and "closed the connection" in b.status().text)
        assert b.status().can_retry
    finally:
        a.stop()
        b.stop()
        hub.shutdown()


def test_a_task_event_flood_becomes_one_line_a_second(hub, monkeypatch, page_file):
    """Thousands of task events a second (an SA alarm page's tasks) add TASK_BURST lines
    and one count line, a halted task always its own line; a drain hands over at most
    DRAIN_MAX lines."""
    from ctgedit.ui.simulate import ctsim_source as CS

    monkeypatch.setenv("CTG_NO_SIM_ENGINE", "1")
    sim = _sim(hub)
    t = [1000.0]
    monkeypatch.setattr(CS.time, "monotonic", lambda: t[0])
    for k in range(3000):
        sim._task_event("taskStarted", {"task": k, "function": "_LibAlmTable_GetData"})
    sim._task_event(
        "taskExited", {"task": 1, "function": "Broken", "state": "halt", "haltReason": "bad handle"}
    )
    with sim._lock:
        lines = list(sim._log)
    assert len(lines) == CS.TASK_BURST + 1
    assert lines[-1].kind == "error" and "bad handle" in lines[-1].text
    t[0] += 1.5
    sim._task_event("taskStarted", {"task": 9, "function": "Next"})
    with sim._lock:
        texts = [e.text for e in sim._log]
    summary = [x for x in texts if "more task events" in x]
    assert summary and "2960 more task events" in summary[0] and "_LibAlmTable_GetData" in summary[0]
    with sim._lock:
        sim._log = [CS.S.LogEntry(0.0, "log", "x%d" % k) for k in range(CS.DRAIN_MAX + 500)]
    out = sim.drain()
    assert len(out) <= CS.DRAIN_MAX + 1 + 10 and "left out" in out[0].text


def test_task_events_are_asked_for_only_while_outputs_shows_tasks(servers, hub, page_file, monkeypatch):
    """hello {events} leaves 'task' out while the Outputs panel hides Task lines; showing
    them again sends a hello with every kind."""
    from ctgedit.ui.simulate import ctsim_source as CS, options

    options.put("log_types_off", "task")
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=False).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.tags_active() and not sim.status().busy)
        hellos = [a for _c, cmd, a in srv.requests if cmd == "ctsim/hello"]
        assert hellos and "task" not in hellos[-1].get("events", []) and "page" in hellos[-1]["events"]
        options.put("log_types_off", "")
        sim._events_checked = 0.0
        sim.drain()
        assert wait_for(lambda: any("events" not in a for _c, cmd, a in srv.requests if cmd == "ctsim/hello"))
    finally:
        sim.stop()
        srv.stop()
        options.put("log_types_off", "")
        CS.C.viewer_events = None


def test_used_tags_of_another_page_are_not_taken_and_the_page_list_starts_again(hub):
    # the tab's window shows another page at first (the startup page, the page an attached
    # server showed): its used tags are not this page's, and when this page shows, the
    # engine's list for the window starts again from 0
    sim = _sim(hub)
    sim.page_name = "Mixer"
    sim._window = 0
    sim._page_tags = []
    answers = {"usedTags": None}

    class Stub:
        def request_async(self, name, args, cb, **kw):
            if name == "usedTags":
                page, names = answers["usedTags"]
                frm = int(args.get("from", 0))
                by = [{"function": "TankLevel", "file": r"C:\P\tanks.ci", "line": 32} for _n in names]
                cb(
                    {"window": 0, "page": page, "count": len(names), "names": names[frm:], "by": by[frm:]},
                    None,
                )
            elif name == "tags":
                cb({"tags": [{"name": n, "type": "REAL"} for n in args["names"]]}, None)

    s = Stub()
    sim._subscribe = lambda *a: None
    answers["usedTags"] = ("Startup", ["S1", "S2", "S3"])
    sim._poll_used(s, sim._gen)
    assert sim._used_from == 0 and not sim._page_tags
    answers["usedTags"] = ("Mixer", ["Lim_LimitLow", "Lim_LimitHigh"])
    sim._poll_used(s, sim._gen)
    assert [t.name for t in sim._page_tags] == ["Lim_LimitLow", "Lim_LimitHigh"]
    assert sim._used_from == 2
    # where each was first used
    assert all(t.cicode_used and t.used_by == "TankLevel (tanks.ci line 32)" for t in sim._page_tags)
    # the page shown again (its list from 0, shorter than what was taken): from the start
    answers["usedTags"] = ("Mixer", ["Lim_LimitLow"])
    sim._poll_used(s, sim._gen)
    assert sim._used_from == 0


def test_used_where_says_the_function_file_and_line():
    from ctgedit.ui.simulate.ctsim_source import used_where

    got = used_where({"function": "TankLevel", "file": r"C:\P\tanks.ci", "line": 32})
    assert got == "TankLevel (tanks.ci line 32)"
    assert used_where({"function": "", "file": "", "line": 0}) == "the page"
    assert used_where({"function": "F", "file": "", "line": 0}) == "F"
    assert used_where(None) == ""


# -- races -----------------------------------------------------------------------------------
class _AliveSession:
    alive = True
    attached = False

    def supports(self, _what):
        return True


def test_reloads_run_one_at_a_time_and_the_newest_waiting_one_wins(hub):
    import threading as th

    sim = _sim(hub)
    sim._session, sim._window = _AliveSession(), 0
    running, ran, overlap = [], [], []
    first_in = th.Event()
    go = th.Event()

    def open_page(s, gen, window=None):
        if running:
            overlap.append(True)
        running.append(1)
        ran.append(sim.model)
        if len(ran) == 1:
            first_in.set()
            go.wait(5)
        running.pop()

    sim._open_page = open_page
    sim._load_page_tags = lambda s, gen: None
    sim.reload({"v": 1})
    assert first_in.wait(5)
    sim.reload({"v": 2})  # waits for the first
    sim.reload({"v": 3})  # replaces the second while it waits
    go.set()
    assert wait_for(lambda: len(ran) == 2 and not running, timeout=5)
    time.sleep(0.2)
    assert ran == [{"v": 1}, {"v": 3}] and not overlap


def test_an_older_page_tag_list_that_comes_last_is_not_taken(hub):
    import threading as th

    sim = _sim(hub)
    sim._window = 0
    slow = th.Event()

    class S:
        def all_tags(self, window=None, page=None, limit=0):
            if not slow.is_set():
                slow.set()
                time.sleep(0.3)  # the first answer comes after the second
                return [{"name": "Old_Tag", "type": "REAL"}], 1
            return [{"name": "New_Tag", "type": "REAL"}], 1

    sim._subscribe = lambda *a: None
    t1 = th.Thread(target=sim._load_page_tags, args=(S(), sim._gen))
    t1.start()
    assert slow.wait(5)
    sim._load_page_tags(S(), sim._gen)
    t1.join(5)
    assert [t.name for t in sim._page_tags] == ["New_Tag"]


def test_tag_writes_and_the_log_flush_from_other_threads_lose_nothing(hub):
    import threading as th

    sim = _sim(hub)
    errors = []

    def write(k):
        try:
            for i in range(400):
                sim._note_write("T%d" % (i % 7), i, "Good" if i % 3 else "Bad")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [th.Thread(target=write, args=(k,)) for k in range(4)]
    for t in ts:
        t.start()
    while any(t.is_alive() for t in ts):
        sim._flush_writes(everything=True)
    for t in ts:
        t.join()
    sim._flush_writes(everything=True)
    assert not errors
    assert all(e.kind == "write" for e in sim.drain() if e.text.startswith("T"))


def test_a_new_server_starts_the_used_tags_and_parameters_over(hub):
    sim = _sim(hub)
    sim._used_for, sim._used_from = (0, "mixer"), 40
    sim._params_serial, sim._params_off = 7, True
    from ctgedit.ui.simulate.sources import TagInfo

    sim._params = [TagInfo(name="[A]B", param=True)]
    sim._drop_session()
    assert sim._used_for is None and sim._used_from == 0 and sim._used_rows == []
    assert sim._params_serial == -1 and not sim._params_off and sim._params == []


def test_a_compiled_page_of_another_version_is_said_in_outputs(hub):
    # a restored project's .RDB of another version of the page: the
    # engine runs the rows and says so; Outputs says it, and that a compile puts it right
    sim = _sim(hub)
    sim._note_open(
        {
            "source": "rows",
            "notes": [
                "the compiled page X.RDB is of another version of the page (pgdynobj: 1 objects "
                "compiled, 2 in the page): the page's rows run"
            ],
        }
    )
    lines = [e for e in sim.drain() if "another version" in e.text]
    assert len(lines) == 1 and lines[0].level == "warning"
    assert lines[0].text.startswith("The compiled page X.RDB") and "Compile the project" in lines[0].text
