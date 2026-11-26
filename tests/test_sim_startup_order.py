"""Start-up order in the Simulate window."""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402
from test_sim_engine_source import _rect_page, _sim, wait_for  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")


@pytest.fixture
def servers(tmp_path, monkeypatch):
    folder = str(tmp_path / "servers")
    monkeypatch.setenv(C.SERVERS_ENV, folder)
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)
    return folder


@pytest.fixture
def hub():
    h = C.Hub(linger=0)
    yield h
    h.shutdown()


@pytest.fixture
def page_file(tmp_path, monkeypatch):
    proj = tmp_path / "User" / "Proj"
    proj.mkdir(parents=True)
    for p in ("Mixer", "Menu"):
        (proj / (p + ".ctg")).write_bytes(b"")
    # not an SA workspace page (whatever the computer's citect.ini names)
    from ctgedit.ui.simulate import sa_start

    monkeypatch.setattr(sa_start, "workspace_start", lambda project, page="": None)
    return str(proj / "Mixer.ctg")


def _engine(record, *extra):
    script = os.path.join(HERE, "fake_ctsim.py")
    py = getattr(sys, "_base_executable", "") or sys.executable
    return C.EngineLocation(script, "test", argv=[py, script, "--pages", "--record", record] + list(extra))


def _requests(record):
    with open(record, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def _names(reqs):
    return [r["cmd"][6:] for r in reqs]


def _texts(sim):
    return [e.text for e in sim.drain()]


def _run(hub, loc, page_file, **attrs):
    sim = _sim(hub, location=loc)
    for k, v in attrs.items():
        setattr(sim, k, v)
    log = []
    sim.start(page_file, _rect_page())
    assert wait_for(lambda: (log.extend(_texts(sim)), sim.page_active())[1], timeout=20), (sim.status(), log)
    log.extend(_texts(sim))
    return sim, log


def test_the_startup_page_and_its_code_come_before_the_page(servers, hub, page_file, tmp_path):
    record = str(tmp_path / "requests.jsonl")
    sim, log = _run(hub, _engine(record, "--navigate", "--start-page", "Menu"), page_file)
    try:
        assert sim.window == 0 and sim.workspace_page == "" and sim.focus_window is None
        assert wait_for(
            lambda: [w["page"] for w in sim.engine_windows()[1] if w["window"] == 0] == ["Mixer"], timeout=15
        )
        reqs = _requests(record)
        names = _names(reqs)
        # the start first: the startup page in window 0 with the startup code ...
        load = names.index("load")
        assert reqs[load]["args"].get("startup") == "auto" and reqs[load]["args"].get("startPage")
        # ... then the page, by PageDisplay in window 0, after the startup code ran
        nav = names.index("navigate")
        assert load < nav
        assert reqs[nav]["args"] == {"page": "Mixer", "window": 0, "how": "pageDisplay"}
        assert reqs[nav]["window0"] == "Menu" and reqs[nav]["startupRan"]
        # the page is not opened on its own (the PageDisplay showed it) and the startup code
        # is asked for once
        assert "openPage" not in names
        assert names.count("load") == 1
        assert any("Startup page Menu in window 0" in t for t in log), log
        assert any("Startup: _CitectBegin runs as task 1" in t for t in log), log
        assert any("the startup code ran (1.0 s simulated) before the page showed" in t for t in log), log
    finally:
        sim.stop()


def test_the_startup_page_itself_is_not_shown_twice(servers, hub, page_file, tmp_path):
    record = str(tmp_path / "requests.jsonl")
    sim, log = _run(hub, _engine(record, "--navigate", "--start-page", "Mixer"), page_file)
    try:
        names = _names(_requests(record))
        assert "load" in names and "navigate" not in names and "openPage" not in names
        assert sim.window == 0
        assert any("Mixer is the project's startup page" in t for t in log), log
    finally:
        sim.stop()


def test_without_a_startup_page_the_page_opens_first(servers, hub, page_file, tmp_path):
    record = str(tmp_path / "requests.jsonl")
    sim, log = _run(hub, _engine(record, "--navigate", "--start-page", ""), page_file)
    try:
        reqs = _requests(record)
        names = _names(reqs)
        assert "navigate" not in names
        # the load came first and waits; the page's open runs the startup code after it
        assert names.index("load") < names.index("openPage")
        assert wait_for(
            lambda: (log.extend(_texts(sim)), any("startup: Startup() runs as task 2" in t for t in log))[1],
            timeout=15,
        ), log
        assert any("did not show (the project has no startup page)" in t for t in log), log
    finally:
        sim.stop()


def test_setting_off_keeps_the_old_order_and_an_older_simulator_is_refused(servers, hub, page_file, tmp_path):
    # the setting off: no startup code, the page opens alone
    record = str(tmp_path / "off.jsonl")
    sim, _log = _run(hub, _engine(record, "--navigate", "--start-page", "Menu"), page_file, run_startup=False)
    try:
        names = _names(_requests(record))
        assert "openPage" in names and "navigate" not in names
        assert not [r for r in _requests(record) if r["cmd"] == "ctsim/load" and r["args"].get("startup")]
    finally:
        sim.stop()
    hub.shutdown()
    # a simulator of another protocol revision is refused, also when ctgedit starts it
    record = str(tmp_path / "old.jsonl")
    sim = _sim(hub, location=_engine(record, "--navigate", "--start-page", "Menu", "--revision", "1"))
    sim.start(page_file, _rect_page())
    try:
        assert wait_for(lambda: "protocol revision" in sim.status().text, timeout=15), sim.status().text
        assert "openPage" not in _names(_requests(record))
    finally:
        sim.stop()


def test_a_server_another_client_started_is_left_as_it_runs(servers, hub, page_file):
    srv = F.FakeServer(
        os.path.dirname(page_file), servers, pages=True, navigate=True, start_page="Menu"
    ).start()
    try:
        sim, _log = _run(hub, None, page_file)
        try:
            names = [cmd[6:] for _c, cmd, _a in srv.requests]
            assert "openPage" in names and "navigate" not in names
            assert not [a for _c, cmd, a in srv.requests if cmd == "ctsim/load" and a.get("startup")]
        finally:
            sim.stop()
    finally:
        srv.stop()


def test_a_paused_clock_runs_for_the_startup_code_and_is_paused_again(servers, hub, page_file, tmp_path):
    # with the clock paused the startup would settle at once, before the startup code set its
    # values: the clock runs for the start, and is paused once the page shows
    record = str(tmp_path / "requests.jsonl")
    sim, log = _run(
        hub, _engine(record, "--navigate", "--start-page", "Menu"), page_file, _want_running=False
    )
    try:
        assert wait_for(
            lambda: any("paused again" in t for t in (log.extend(_texts(sim)) or log)), timeout=15
        ), log
        reqs = _requests(record)
        names = _names(reqs)
        clocks = [(k, r["args"].get("mode")) for k, r in enumerate(reqs) if r["cmd"] == "ctsim/clock"]
        load, nav = names.index("load"), names.index("navigate")
        assert any(k < load and m == "run" for k, m in clocks), clocks
        assert any(k > nav and m == "pause" for k, m in clocks), clocks
    finally:
        sim.stop()
    # a running clock is left as it is
    record2 = str(tmp_path / "requests2.jsonl")
    sim, log = _run(
        hub, _engine(record2, "--navigate", "--start-page", "Menu"), page_file, _want_running=True
    )
    try:
        reqs = _requests(record2)
        nav = _names(reqs).index("navigate")
        assert not [r for r in reqs[nav:] if r["cmd"] == "ctsim/clock" and r["args"].get("mode") == "pause"]
        assert not any("paused again" in t for t in log)
    finally:
        sim.stop()
