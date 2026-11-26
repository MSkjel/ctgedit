"""The ctsim client against a fake server."""

import json
import os
import subprocess
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")

FAKE = os.path.join(HERE, "fake_ctsim.py")
#: the interpreter itself, not the venv's redirector (so the server pid is the process pid)
PY = getattr(sys, "_base_executable", "") or sys.executable


def fake_engine(*extra) -> C.EngineLocation:
    return C.EngineLocation(FAKE, "test", argv=[PY, FAKE] + list(extra))


@pytest.fixture
def servers(tmp_path, monkeypatch):
    folder = str(tmp_path / "servers")
    monkeypatch.setenv(C.SERVERS_ENV, folder)
    return folder


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "User" / "Proj"
    p.mkdir(parents=True)
    return str(p)


@pytest.fixture
def fake(project, servers):
    srv = F.FakeServer(project, servers).start()
    yield srv
    srv.stop()


def wait_for(cond, timeout=15.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


# -- framing ------------------------------------------------------------------------------------
def test_frames_split_anywhere_and_headers_are_case_insensitive():
    msgs = [{"seq": k, "type": "event", "event": "ctsim/log", "body": {"text": "x" * k}} for k in range(1, 6)]
    data = b"".join(C.encode(m) for m in msgs)
    data = data.replace(b"Content-Length", b"content-length", 1)
    data = b"Content-Type: application/json\r\n" + data  # another header is ignored
    for chunk in (1, 2, 7, 64, len(data)):
        p = C.FrameParser()
        out = []
        for i in range(0, len(data), chunk):
            out += p.feed(data[i : i + chunk])
        assert [json.loads(b) for b in out] == msgs
        assert p.at_frame_start()
    # a bare LF header end is accepted too
    p = C.FrameParser()
    assert p.feed(b"Content-Length: 2\n\n{}") == [b"{}"]
    assert json.loads(C.encode({"text": "\u00e6\u00f8\u00e5"}).split(b"\r\n\r\n", 1)[1]) == {
        "text": "\u00e6\u00f8\u00e5"
    }


@pytest.mark.parametrize(
    "head", [b"Content-Type: x\r\n\r\n", b"Content-Length: x1\r\n\r\n", b"no colon here\r\n\r\n"]
)
def test_broken_headers_are_framing_errors(head):
    with pytest.raises(C.FramingError):
        C.FrameParser().feed(head)
    with pytest.raises(C.FramingError):
        C.FrameParser(max_body=10).feed(b"Content-Length: 11\r\n\r\n")


def test_start_times_of_every_discovery_shape():
    ft = 134353642652268331  # a FILETIME
    unix = (ft - 116444736000000000) / 1e7
    assert C.filetime_of(ft) == ft
    assert C.filetime_of(ft + 504911232000000000) == ft  # .NET ticks
    assert abs(C.filetime_of(unix) - ft) < 10  # Unix seconds
    assert abs(C.filetime_of(int(unix * 1000)) - ft) < 10_000  # Unix milliseconds
    assert abs(C.filetime_of(str(int(unix))) - ft) < 10_000_000
    from datetime import datetime, timezone

    iso = datetime.fromtimestamp(unix, timezone.utc).isoformat()
    assert abs(C.filetime_of(iso) - ft) < 10
    assert abs(C.filetime_of(iso.replace("+00:00", "Z")) - ft) < 10
    assert C.filetime_of("soon") is None and C.filetime_of(None) is None
    assert C.filetime_of(True) is None


# -- finding the engine -----------------------------------------------------------------------
def _engine_dir(root, with_dll=True):
    os.makedirs(root, exist_ok=True)
    open(os.path.join(root, C.EXE_NAME), "wb").close()
    if with_dll:
        open(os.path.join(root, C.DLL_NAME), "wb").close()
    return os.path.join(root, C.EXE_NAME)


def test_find_engine_order_setting_environment_checkout(tmp_path, monkeypatch):
    monkeypatch.delenv(C.PATH_ENV, raising=False)
    monkeypatch.setattr(C, "repo_root", lambda: str(tmp_path / "repo"))
    with pytest.raises(C.EngineNotFound) as e:
        C.find_engine()
    assert "was not found" in str(e.value)
    assert any("net48" in p for p in e.value.searched)
    dev = _engine_dir(C.checkout_build_dir(str(tmp_path / "repo")))
    assert C.find_engine().exe == dev and C.find_engine().origin == "checkout"
    env = _engine_dir(str(tmp_path / "env"))
    monkeypatch.setenv(C.PATH_ENV, os.path.dirname(env))  # a folder is completed
    assert C.find_engine().exe == env and C.find_engine().origin == "environment"
    chosen = _engine_dir(str(tmp_path / "chosen"))
    loc = C.find_engine(chosen)
    assert loc.exe == chosen and loc.origin == "setting" and loc.dll.endswith(C.DLL_NAME)
    # a setting that is not there is an error of its own, not passed over
    with pytest.raises(C.EngineNotFound) as e:
        C.find_engine(str(tmp_path / "gone" / C.EXE_NAME))
    assert "not there any more" in str(e.value)
    lone = _engine_dir(str(tmp_path / "lone"), with_dll=False)
    with pytest.raises(C.EngineNotFound) as e:
        C.find_engine(lone)
    assert C.DLL_NAME in str(e.value)


def test_frozen_build_finds_the_engine_next_to_ctgedit_exe(tmp_path, monkeypatch):
    monkeypatch.delenv(C.PATH_ENV, raising=False)
    monkeypatch.setattr(C, "repo_root", lambda: str(tmp_path / "repo"))
    app = tmp_path / "dist" / "ctgedit"
    exe = _engine_dir(str(app))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "ctgedit.exe"))
    loc = C.find_engine()
    assert loc.exe == exe and loc.origin == "bundled"


# -- discovery ---------------------------------------------------------------------------------
def _dead_pid() -> int:
    p = subprocess.Popen([PY, "-c", "pass"])
    p.wait()
    return p.pid


def test_discovery_keeps_live_servers_and_ignores_stale_ones(servers, project):
    os.makedirs(servers)

    def write(name, **data):
        with open(os.path.join(servers, name + ".json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        time.sleep(0.02)

    me = os.getpid()
    write("dead", pid=_dead_pid(), pipe="\\\\.\\pipe\\ctsim.dead", project=project)
    write("reused", pid=me, processStart=12345678901234567, pipe="\\\\.\\pipe\\ctsim.r", project=project)
    write("nopipe", pid=me, project=project)
    with open(os.path.join(servers, "junk.json"), "w") as fh:
        fh.write("{not json")
    write("other", pid=me, pipe="\\\\.\\pipe\\ctsim.other", project=project + "2")
    # 'started' = when the server began serving (not the process identity): ignored for that
    write(
        "live",
        format="ctsim-server/0",
        pid=me,
        processStart=C.paths.process_started(),
        started="2001-01-01T00:00:00Z",
        pipe="\\\\.\\pipe\\ctsim.live",
        project=project.upper(),
    )
    write("future", format="other-tool/9", pid=me, pipe="\\\\.\\pipe\\ctsim.f", project=project)
    write("tcp", pid=me, transport="tcp", address="127.0.0.1:9", project=project)
    live = C.read_servers(servers)
    assert [e.id for e in live] == ["tcp", "live", "other"]  # newest first
    assert [e.id for e in C.servers_for(project, servers)] == ["tcp", "live"]
    e = C.servers_for(project, servers)[1]
    assert e.transport == "pipe" and e.address.endswith("ctsim.live") and e.pid == me
    assert len(C.read_servers(servers, alive_only=False)) == 5


def test_attach_through_discovery_and_the_requests(fake, project, servers):
    s = C.open_session(project, start=False)
    try:
        assert s.attached and s.server is None and s.describe() == "ctsim 0.0-fake"
        assert s.pid == os.getpid() and s.entry.address == fake.pipe
        hello = next(a for _c, cmd, a in fake.requests if cmd == "ctsim/hello")
        assert hello["client"] == "ctgedit" and hello["protocol"] == C.PROTOCOL
        # paged tags
        before = len(fake.requests)
        rows, total = s.all_tags(page_size=100)
        assert total == 1237 and len(rows) == 1237 and rows[0]["name"] == "Tank1.Level"
        assert len(fake.requests) - before == 13  # 13 pages of 100
        assert rows[0]["eng_full"] == 200.0 and rows[2]["quality"] == "Good"  # 192 = good
        page = s.tags(page="FxSim1")
        assert page.total == 3 and all(t["page_reads"] for t in page.tags)
        found = s.tags(filter="tag012", count=5)
        assert found.total == 10 and [t["name"] for t in found.tags][:2] == ["Tag0120", "Tag0121"]
        # setTag -> tagsChanged to a subscriber
        events = []
        s.add_listener(lambda n, b: events.append((n, b)))
        s.subscribe(["Tank1.Level"])
        assert s.set_tag("Tank1.Level", 55.5, "Uncertain") == {
            "name": "Tank1.Level",
            "value": 55.5,
            "quality": "Uncertain",
        }
        assert wait_for(lambda: any(n == "ctsim/tagsChanged" for n, _b in events))
        ch = C.tag_changes_from(next(b for n, b in events if n == "ctsim/tagsChanged"))
        assert ch == [{"name": "Tank1.Level", "value": 55.5, "quality": "Uncertain", "timestamp": None}]
        with pytest.raises(C.RequestFailed) as e:
            s.set_tag("NoSuchTag", 1)
        assert "not found" in str(e.value) and not isinstance(e.value, C.NotAvailable)
        # call: a value, a hardware error, the task and log events
        assert s.call("Add(1,2)") == 3.0
        with pytest.raises(C.RequestFailed) as e:
            s.call("Fail()")
        assert "274" in str(e.value) and "invalid argument" in str(e.value)
        assert e.value.body["state"] == "halt"
        assert wait_for(lambda: any(n == "ctsim/taskExited" for n, _b in events))
        assert any(n == "ctsim/log" and b["file"] == "fake.ci" for n, b in events)
        # the clock
        c = s.clock_request("step")
        assert (c.scan, c.time, c.running, c.known) == (1, 0.25, False, True)
        assert s.clock_request("speed", 5).speed == 5.0
        s.scenario("load", "C:\\scenario.json")
        assert fake.scenarios == [{"action": "load", "path": "C:\\scenario.json"}]
        with pytest.raises(C.RequestFailed):
            s.scenario("rewind", "C:\\scenario.json")  # the engine's actions only
        # a request the server does not have
        # the hello's capabilities are not read (the protocol revision decides): a request
        # the server refuses as unknown is remembered
        assert s.supports("openPage") is None and s.supports("tags") is None
        assert s.clock_request().known  # '' = only ask
        with pytest.raises(C.NotAvailable):
            s.open_page("FxSim1")
        assert s.supports("openPage") is False
    finally:
        s.close()
    assert wait_for(lambda: not fake.conns)  # the connection went away
    assert not fake.stopping  # an attached server stays


def test_a_server_that_lists_nothing_is_asked_and_remembers_the_answer(project, servers):
    srv = F.FakeServer(project, servers, capabilities=False).start()
    try:
        s = C.open_session(project, start=False)
        assert s.supports("openPage") is None  # it did not say
        with pytest.raises(C.NotAvailable):
            s.open_page("FxSim1")
        assert s.supports("openPage") is False
        n = len(srv.requests)
        with pytest.raises(C.NotAvailable):  # not asked again
            s.open_page("FxSim1")
        assert len(srv.requests) == n
        s.close()
    finally:
        srv.stop()


def test_pages_requests_when_the_server_has_them(project, servers):
    srv = F.FakeServer(project, servers, pages=True).start()
    try:
        s = C.open_session(project, start=False)
        assert s.supports("openPage") is not False
        w = s.open_page("FxSim1")
        st = s.page_state(w)
        assert st.replace and st.window == w and st.page == "FxSim1"
        assert st.objects[11].fill_colour == 0x00FF00 and st.objects[12].text == "12.5"
        from ctgedit.ui.simulate.sources import InputEvent

        s.request("input", C.Session.input_args(w, InputEvent("down", an=11, ans=(11, 3), x=5, y=6)))
        assert srv.inputs == [{"window": w, "kind": "down", "an": 11, "ans": [11, 3], "x": 5.0, "y": 6.0}]
        s.close()
    finally:
        srv.stop()


def test_timeouts_leave_the_connection_usable(fake, project):
    s = C.open_session(project, start=False)
    try:
        t0 = time.monotonic()
        with pytest.raises(C.RequestTimeout) as e:
            s.call("Hang()", timeout=0.4)
        assert 0.35 < time.monotonic() - t0 < 2.0 and "within 0.4 s" in str(e.value)
        assert s.call("Add(2,2)") == 4.0
        # an async request times out on the reader thread
        got = []
        done = threading.Event()
        s.request_async(
            "call", {"expr": "Hang()"}, callback=lambda b, err: (got.append(err), done.set()), timeout=0.3
        )
        assert done.wait(3) and isinstance(got[0], C.RequestTimeout)
        p = s.request_async("call", {"expr": "Add(5,5)"})
        assert p.wait(5)["value"] == 10.0
    finally:
        s.close()


def test_stale_entries_are_passed_over_and_a_server_is_started(project, servers):
    os.makedirs(servers)
    me = os.getpid()
    for name, pid, pipe in (("dead", _dead_pid(), "ctsim.dead"), ("gone", me, "ctsim.nobody%d" % me)):
        with open(os.path.join(servers, name + ".json"), "w") as fh:
            json.dump({"pid": pid, "pipe": "\\\\.\\pipe\\" + pipe, "project": project}, fh)
    assert [e.id for e in C.servers_for(project, servers)] == ["gone"]  # alive, no pipe
    with pytest.raises(C.ServerUnavailable):
        C.open_session(project, start=False)
    s = C.open_session(project, location=fake_engine())
    try:
        assert not s.attached and s.server.running() and s.pid == s.server.pid
        assert s.call("Add(1,1)") == 2.0
        assert s.entry.pid == s.server.pid  # its own discovery file
    finally:
        s.close()
    assert not s.server.running()


def test_a_started_server_stops_with_its_owner_never_with_an_attached_client(project, servers):
    owner = C.open_session(project, location=fake_engine())
    try:
        other = C.open_session(project, location=fake_engine())
        assert other.attached and other.pid == owner.pid  # joined, not a second one
        other.close()
        time.sleep(0.2)
        assert owner.server.running() and owner.call("Add(3,4)") == 7.0
    finally:
        owner.close()
    assert owner.server.proc.returncode == 0  # it ended itself (ctsim/shutdown)
    assert not os.path.exists(owner.entry.path)  # and took its discovery file


def test_no_engine_is_a_plain_message(project, servers, tmp_path, monkeypatch):
    monkeypatch.delenv(C.PATH_ENV, raising=False)
    monkeypatch.setattr(C, "repo_root", lambda: str(tmp_path / "norepo"))
    with pytest.raises(C.EngineNotFound) as e:
        C.open_session(project)
    assert "ctsim.exe" in str(e.value) and e.value.searched


def test_a_server_crash_fails_the_pending_requests_and_says_why(project, servers):
    s = C.open_session(project, location=fake_engine())
    closed = []
    s.on_close(closed.append)
    pending = s.request_async("call", {"expr": "Hang()"}, timeout=30)
    t0 = time.monotonic()
    with pytest.raises(C.ConnectionLost):
        s.call("Crash()", timeout=10)
    assert time.monotonic() - t0 < 5
    with pytest.raises(C.ConnectionLost):
        pending.wait(5)
    assert wait_for(lambda: closed) and closed[0]  # a reason (not ours)
    assert wait_for(lambda: not s.server.running())
    assert "exit code 3" in s.stop_reason()
    with pytest.raises(C.ConnectionLost):
        s.call("Add(1,1)")
    s.close()


def test_a_crash_of_an_in_process_server_is_seen_at_once(fake, project):
    s = C.open_session(project, start=False)
    closed = threading.Event()
    s.on_close(lambda r: closed.set())
    fake.crash()
    assert closed.wait(3)
    assert not s.alive and s.conn.close_reason
    s.close()


def test_the_hub_shares_one_session_per_project(fake, project):
    hub = C.Hub(linger=0)
    a = hub.acquire(project, folder=None)
    b = hub.acquire(project.upper())  # the same project
    assert a is b and hub.sessions() == [a]
    hub.release(a)
    assert a.alive  # still used once
    hub.release(b)
    assert not a.alive and hub.sessions() == []
    assert not fake.stopping  # attached: never stopped
    c = hub.acquire(project)
    assert c is not a and c.alive
    hub.shutdown()
    assert not c.alive


def test_the_hub_stops_a_started_server_after_it_lingered(project, servers):
    hub = C.Hub(linger=3.0)
    s = hub.acquire(project, location=fake_engine())
    hub.release(s)
    again = hub.acquire(project, location=fake_engine())  # within the linger: reused
    assert again is s and s.server.running()
    hub.release(again, linger=2.0)
    assert s.server.running()  # it lingers first
    assert wait_for(lambda: not s.server.running(), timeout=15)
    hub.shutdown()


def test_a_dead_session_is_replaced(fake, project):
    hub = C.Hub(linger=0)
    a = hub.acquire(project)
    for c in list(fake.conns):
        c.stream.close()  # the link drops
    assert wait_for(lambda: not a.alive)
    b = hub.acquire(project)
    assert b is not a and b.alive and b.call("Add(1,2)") == 3.0
    hub.shutdown()


def test_tcp_transport(project, servers):
    srv = F.FakeServer(project, servers, transport="tcp").start()
    try:
        e = C.servers_for(project)[0]
        assert e.transport == "tcp" and e.address.startswith("127.0.0.1:")
        s = C.open_session(project, start=False)
        assert s.call("Add(20,22)") == 42.0
        s.close()
    finally:
        srv.stop()


def test_input_arguments_follow_spec_section_7():
    from ctgedit.ui.simulate.sources import InputEvent

    a = C.Session.input_args(2, InputEvent("key", an=7, key="F2", text="", modifiers=2))
    assert a == {"window": 2, "kind": "key", "an": 7, "x": 0.0, "y": 0.0, "key": "F2", "modifiers": 2}
    a = C.Session.input_args(0, InputEvent("drag", an=None, x=1, y=2, dx=3, dy=4, button="right"))
    assert a == {"window": 0, "kind": "drag", "x": 1.0, "y": 2.0, "dx": 3.0, "dy": 4.0, "button": "right"}


def test_tag_shapes_are_read_in_either_case():
    tp = C.tag_page_from(
        {
            "equipment": [
                {"name": "Tank1", "tags": [{"Name": "Level", "eng_zero": "0", "ENG_FULL": "", "quality": 64}]}
            ],
            "total": "9",
        }
    )
    assert tp.total == 9 and tp.tags[0]["equipment"] == "Tank1"
    assert tp.tags[0]["quality"] == "Uncertain" and tp.tags[0]["eng_full"] is None
    assert C.tag_changes_from({"values": {"A": 1}}) == [
        {"name": "A", "value": 1, "quality": None, "timestamp": None}
    ]
    assert C.quality_text(0) == "Bad" and C.quality_text("GOOD (local)") == "Good"


def test_the_page_states_parse_from_json():
    from ctgedit.sim.state import object_state_from_json, page_state_from_json

    st = object_state_from_json(
        5,
        {
            "fillColor": "0x0000ff",
            "move": {"x": 1, "y": 2},
            "rotate": float("nan"),
            "scale": [2, "a"],
            "shown": 0,
            "vertexOffsets": [[1, 2]],
            "unknown": 3,
        },
    )
    assert (st.fill_colour, st.move, st.rotate, st.scale, st.shown, st.vertex_offsets) == (
        0xFF,
        (1.0, 2.0),
        None,
        None,
        False,
        [(1.0, 2.0)],
    )
    ps = page_state_from_json({"window": 3, "objects": {"7": None, "x": {}}, "flashOn": False})
    assert ps.window == 3 and list(ps.objects) == [7] and ps.objects[7].is_default()
    assert not ps.flash_on and not ps.replace


def test_disabled_switch(monkeypatch):
    monkeypatch.setenv(C.DISABLE_ENV, "1")
    assert C.disabled()
    monkeypatch.setenv(C.DISABLE_ENV, "0")
    assert not C.disabled()


def test_a_started_server_is_found_by_its_ready_line(project, servers, tmp_path):
    # the discovery file goes where the client does not look: 'ctsim serve' printed its
    # ready line ({"event": "ready", "pipe", ...}), which is what the client takes
    s = C.open_session(project, location=fake_engine(), folder=str(tmp_path / "elsewhere"))
    try:
        assert s.server.ready["event"] == "ready" and s.entry.address == s.server.ready["pipe"]
        assert s.entry.pid == s.server.pid and s.call("Add(2,5)") == 7.0
    finally:
        s.close()


def test_a_server_is_found_by_the_pipe_it_prints(project, servers, tmp_path):
    # no ready line and the discovery file elsewhere: the pipe the server printed
    s = C.open_session(project, location=fake_engine("--plain-ready"), folder=str(tmp_path / "elsewhere"))
    try:
        assert s.server.ready is None
        assert s.server.announced.lower().startswith("\\\\.\\pipe\\ctsim.")
        assert s.entry.path == "" and s.entry.address == s.server.announced
        assert s.call("Add(1,5)") == 6.0
    finally:
        s.close()


def _raw_pipe_server(name):
    """A bare pipe server: (handle future) for one client, to send it anything."""
    import _winapi

    h = _winapi.CreateNamedPipe(name, 0x3 | 0x40000000, 0, 255, 65536, 65536, 0, _winapi.NULL)
    ov = _winapi.ConnectNamedPipe(h, overlapped=True)
    return h, ov


def test_a_broken_frame_from_the_server_ends_the_connection_with_a_reason():
    import _winapi

    name = "\\\\.\\pipe\\ctsim.rawtest%d" % os.getpid()
    h, ov = _raw_pipe_server(name)
    stream = C.open_pipe(name)
    assert _winapi.WaitForMultipleObjects([ov.event], False, 2000) == 0
    ov.GetOverlappedResult(True)
    srv = C.PipeStream(h, "raw")
    conn = C.Connection(stream, "raw").start()
    closed = []
    conn.on_close(closed.append)
    p = conn.request_async("hello", {})
    srv.write(b"Content-Length: nope\r\n\r\n")
    with pytest.raises(C.ConnectionLost) as e:
        p.wait(5)
    assert wait_for(lambda: closed) and "broken message" in closed[0]
    assert "broken message" in str(e.value)
    srv.close()
    conn.close()


def test_a_reverse_request_is_refused_and_unknown_responses_ignored():
    import _winapi

    name = "\\\\.\\pipe\\ctsim.rawtest2_%d" % os.getpid()
    h, ov = _raw_pipe_server(name)
    stream = C.open_pipe(name)
    assert _winapi.WaitForMultipleObjects([ov.event], False, 2000) == 0
    ov.GetOverlappedResult(True)
    srv = C.PipeStream(h, "raw")
    conn = C.Connection(stream, "raw").start()
    srv.write(C.encode({"seq": 1, "type": "response", "request_seq": 999, "success": True}))
    srv.write(C.encode({"seq": 2, "type": "request", "command": "runInTerminal"}))
    parser = C.FrameParser()
    got = []
    end = time.monotonic() + 15
    while time.monotonic() < end and not got:
        data = srv.read(timeout=0.2)
        if data:
            got += [json.loads(b) for b in parser.feed(data)]
    assert got and got[0]["type"] == "response" and got[0]["request_seq"] == 2
    assert got[0]["success"] is False and not conn.closed
    srv.close()
    conn.close()


class _StubConn:
    """A connection that answers every request with one body (unit tests of Session)."""

    closed = False

    def __init__(self, body):
        self.body = body
        self.sent = []

    def request(self, cmd, args, timeout):
        self.sent.append((cmd, args))
        return self.body


def test_a_halted_call_outcome_raises_even_when_answered_as_success():
    s = C.Session(
        "x", _StubConn({"task": 3, "state": "halt", "error": 274, "errorText": "invalid argument"}), {}
    )
    with pytest.raises(C.RequestFailed) as e:
        s.call("X()")
    assert str(e.value) == "invalid argument (hardware error 274)"
    s = C.Session("x", _StubConn({"task": 3, "state": "stop", "result": "42", "value": None}), {})
    assert s.call("X()") == "42"  # no value: the result text


def test_the_engines_clock_state_reads_as_the_clock_bar_needs():
    c = C.clock_from(
        {
            "mode": "paused",
            "speed": 2.0,
            "ms": 1500,
            "t": 1.5,
            "time": "2026-10-01T12:00:01.500Z",
            "start": "2026-10-01T12:00:00Z",
            "stepSize": 0.25,
        }
    )
    assert (c.running, c.speed, c.time, c.scan, c.known) == (False, 2.0, 1.5, 6, True)
    c = C.clock_from({"clock": {"mode": "run", "ms": 250}}, c)
    assert c.running and c.time == 0.25 and c.scan == 1
    assert C.Session.clock_args("") == {} and C.Session.clock_args("step", 0.5) == {
        "mode": "step",
        "value": 0.5,
    }


def test_requests_follow_the_servers_argument_names():
    stub = _StubConn({"subscribed": 1, "tags": [{"name": "A", "value": 1, "quality": "good"}], "unknown": []})
    s = C.Session("x", stub, {"capabilities": {"pages": True, "scenarios": False}})
    assert s.subscribe(["A"]) == [{"name": "A", "value": 1, "quality": "Good", "timestamp": None}]
    assert stub.sent[-1] == ("ctsim/subscribe", {"names": ["A"], "replace": True})
    s.tags(names=["A", "B"], equipment="Tank1")
    assert stub.sent[-1][1]["names"] == ["A", "B"] and stub.sent[-1][1]["equipment"] == "Tank1"
    assert s.supports("pageState") is None and s.supports("scenario") is None
    assert C.written_tag({"error": 0, "tag": {"name": "A", "value": 3, "quality": "bad_comm"}}) == {
        "name": "A",
        "value": 3,
        "quality": "Bad",
    }


def test_a_started_server_never_outlives_its_client(project, servers):
    """The started server sits in a kill-on-close job object: a client process that dies
    (a crash, a kill) takes its server with it, no orphan keeps the project's pipe."""
    code = (
        "from ctgedit.infra.paths import hard_exit\n"
        "from ctgedit.sim import client as C\n"
        "loc = C.EngineLocation(%r, 'test', argv=[%r, %r])\n"
        "s = C.open_session(%r, location=loc)\n"
        "print(s.pid, flush=True)\n"
        "hard_exit(0)\n" % (FAKE, PY, FAKE, project)
    )
    env = dict(os.environ, PYTHONPATH=os.path.dirname(HERE))
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, timeout=60, env=env)
    pid = int(r.stdout.strip().splitlines()[-1])
    assert wait_for(lambda: not C.paths.pid_alive(pid), timeout=15)


def test_a_failure_that_only_says_not_available_keeps_the_request_on(fake, project):
    """Only the engine's 'unknown request' / body.notImplemented mean 'not in this server':
    a hardware error titled 'Not available outside process' (420) halting a call is an
    ordinary failure, and ctsim/call stays usable."""
    s = C.open_session(project, start=False)
    try:
        with pytest.raises(C.RequestFailed) as e:
            s.call("OutOfProcess()")
        assert not isinstance(e.value, C.NotAvailable) and "420" in str(e.value)
        assert e.value.body["error"] == 420
        assert s.call("Add(1,1)") == 2.0  # still on
        assert not C._is_unknown_request("setTag Unknown: error 424 Tag not found")
        assert not C._is_unknown_request("the call halted: unsupported opcode Foo at 0x10")
        assert C._is_unknown_request("unknown request 'ctsim/frobnicate'")
        assert C._is_unknown_request("not implemented yet: ...", {"notImplemented": True})
        with pytest.raises(C.NotAvailable):
            s.request("frobnicate", {})
    finally:
        s.close()


def test_a_dead_session_stops_its_own_server_before_a_new_one(project, servers):
    """The link to a server ctgedit started breaks while the server lives on: the next
    acquire stops that server (it would otherwise be attached to as a stranger and run until
    ctgedit exits) and starts a new one."""
    hub = C.Hub(linger=30.0)
    a = hub.acquire(project, location=fake_engine())
    old = a.server
    try:
        a.conn._stream.close()  # the link drops, not the server
        assert wait_for(lambda: not a.alive)
        assert old.running()
        b = hub.acquire(project, location=fake_engine())
        assert b is not a and not b.attached and b.pid != old.pid
        assert not old.running()  # ours, stopped, not joined
        assert b.call("Add(2,3)") == 5.0
    finally:
        hub.shutdown()
    assert not old.running() and not b.server.running()


def test_a_server_being_stopped_is_never_attached(fake, project):
    C._stopping_add(fake.pid)
    try:
        assert C.stopping(fake.pid)
        with pytest.raises(C.ServerUnavailable):
            C.open_session(project, start=False)
    finally:
        C._stopping_discard(fake.pid)
    s = C.open_session(project, start=False)
    s.close()


def test_a_killed_server_leaves_no_discovery_file(project, servers):
    s = C.open_session(project, location=fake_engine())
    path = s.server.discovery_path
    assert path and os.path.exists(path)
    s.conn.close()
    s.server.stop(timeout=3, grace=0)  # TerminateProcess: no cleanup
    assert not s.server.running() and not os.path.exists(path)
    assert not C.stopping(s.server.pid)
    s.close()


def test_big_messages_both_ways_over_tcp(project, servers):
    srv = F.FakeServer(project, servers, transport="tcp").start()
    try:
        s = C.open_session(project, start=False)
        big = "x" * (3 << 20)
        assert s.set_tag("Message", big)["value"] == big  # 3 MB out, 3 MB back
        assert s.call("Add(1,2)") == 3.0
        s.close()
    finally:
        srv.stop()


def test_concurrent_closes_close_once(fake, project):
    s = C.open_session(project, start=False)
    ts = [threading.Thread(target=s.close) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert not s.alive and not any(t.is_alive() for t in ts)


def test_a_started_server_another_client_uses_is_left_running(project, servers):
    """One simulation shared by ctgedit and a VS Code debugger. When ctgedit
    lets go of a server it started while another client is connected, the server stays (it
    still ends with ctgedit through the job object)."""
    owner = C.open_session(project, location=fake_engine())
    srv = owner.server
    other = C.open_session(project, location=fake_engine())  # a second client
    try:
        assert other.attached and other.pid == owner.pid
        assert owner.other_clients() == 1
        owner.close()
        assert srv.running() and not C.stopping(srv.pid)
        assert other.call("Add(4,4)") == 8.0  # still served
        other.close()
        assert srv.running()  # an attached one never stops it
    finally:
        srv.stop(timeout=3, grace=0)
    assert not srv.running()


def test_server_output_lines_decode_utf8_or_the_console_code_page():
    """The engine's ready line is UTF-8; its stderr (a failed start's message) is the OEM
    code page when it runs without a console (live, net48: cp850 bytes)."""
    import codecs

    D = C.StartedServer.decode_line
    ready = '{"event":"ready","project":"C:\\\\Prosj\u00e6"}'
    assert D(ready.encode("utf-8")) == ready
    try:
        codecs.lookup("oem")
    except LookupError:
        pytest.skip("no OEM code page here")
    text = "ctsim serve: no compiled code: C:\\Prosj\u00e6\u00e5\\_LIBRARY.RDB"
    raw = text.encode("oem", "replace")
    assert D(raw) == raw.decode("oem") and "\ufffd" not in D(raw)


def test_closing_from_the_reader_thread_does_not_wait_for_answers(project, servers):
    """A listener (the reader thread) that closes the session: no response can arrive while
    the reader waits, so the close must not wait for the hello / shutdown answers, and the
    server it started still ends."""
    s = C.open_session(project, location=fake_engine())
    took = []

    def on_event(name, body):
        if name == "ctsim/log" and not took:
            t0 = time.monotonic()
            s.close()
            took.append(time.monotonic() - t0)

    s.add_listener(on_event)
    with pytest.raises(C.ConnectionLost):
        s.call("Add(1,2)", timeout=10)
    assert wait_for(lambda: took, timeout=15)
    assert took[0] < 1.5, took
    assert wait_for(lambda: not s.server.running(), timeout=15)


def test_a_listener_cannot_wait_for_an_answer(fake, project):
    """A synchronous request from a listener (the reader thread) fails at once instead of
    stalling the connection for its timeout; request_async works there."""
    s = C.open_session(project, start=False)
    got = {}

    def on_event(name, body):
        if name == "ctsim/log" and "sync" not in got:
            t0 = time.monotonic()
            try:
                s.call("Add(1,1)", timeout=5)
                got["sync"] = "answered"
            except C.CtsimError as e:
                got["sync"] = str(e)
            got["took"] = time.monotonic() - t0
            s.request_async("call", {"expr": "Add(2,2)"}, callback=lambda b, err: got.setdefault("async", b))

    s.add_listener(on_event)
    try:
        assert s.call("Add(3,3)") == 6.0
        assert wait_for(lambda: "async" in got)
        assert "request_async" in got["sync"] and got["took"] < 0.5
        assert got["async"]["value"] == 4.0
    finally:
        s.close()


def test_a_started_server_that_does_not_answer_is_left_running(project, servers):
    """A debugger holding the simulation in page code makes every other request wait: the hello
    that counts the other clients times out, and a server that does not answer is left
    running."""
    owner = C.open_session(project, location=fake_engine())
    srv = owner.server
    try:

        def held(cmd, args=None, timeout=None):
            raise C.RequestTimeout("ctsim/hello", timeout or 0)

        real = owner.conn.request
        owner.conn.request = lambda cmd, args=None, timeout=None: (
            held(cmd, args, timeout) if C.command(cmd) == "ctsim/hello" else real(cmd, args, timeout)
        )
        assert owner.other_clients() == -1
        owner.close()
        assert srv.running() and not C.stopping(srv.pid)
    finally:
        srv.stop(timeout=3, grace=0)


def test_releasing_a_session_the_hub_let_go_never_blocks_the_caller(project, servers):
    """A tab that stops (the GUI thread) releases a session the hub no longer holds (replaced,
    or let go for a new simulator choice): its close (hello, shutdown, the process ending)
    runs on a worker, the caller returns at once."""
    hub = C.Hub(linger=30.0)
    s = hub.acquire(project, location=fake_engine())
    srv = s.server
    slow = s.other_clients
    s.other_clients = lambda timeout=1.0: (time.sleep(2.0), slow(timeout))[1]
    with hub._lock:
        hub._sessions.clear()  # the hub let go of it
        hub._refs.clear()
    t0 = time.monotonic()
    hub.release(s)
    assert time.monotonic() - t0 < 0.5
    assert wait_for(lambda: not srv.running(), timeout=15)
    hub.shutdown()


def test_a_double_click_is_sent_as_the_engines_dblclick():
    from ctgedit.ui.simulate.sources import InputEvent

    a = C.Session.input_args(1, InputEvent("double", an=5, x=3, y=4))
    assert a["kind"] == "dblclick" and a["an"] == 5
    assert C.Session.input_args(1, InputEvent("down", an=5))["kind"] == "down"


def test_a_server_of_another_protocol_revision_is_not_attached_to(project, servers):
    """A running server below the protocol revision this client needs (another ctgedit's, a
    VS Code extension's) is passed over; a current one is used."""
    old = F.FakeServer(project, servers)
    old.revision = C.PROTOCOL_REVISION - 1
    old.start()
    try:
        assert C.protocol_revision({}) == 0
        assert C.protocol_revision({"protocolRevision": 5}) == 5
        assert (
            C.build_text({"commit": "f69e50ea61cdf1eb", "buildTime": "2026-10-02T19:59:54Z"})
            == "commit f69e50ea61cd, built 2026-10-02 19:59 UTC"
        )
        with pytest.raises(C.ServerUnavailable):
            C.open_session(project, start=False)  # only the other one runs
        new = F.FakeServer(project, servers).start()
        try:
            s = C.open_session(project, start=False)
            assert s.pid == new.pid or s.hello.get("id") == new.id
            assert C.protocol_revision(s.hello) >= C.PROTOCOL_REVISION
            s.close()
        finally:
            new.stop()
    finally:
        old.stop()


def test_a_held_server_neither_times_out_requests_nor_ends_the_connection():
    """A debugger holding the simulation in page code (ctsim/debugPaused):
    the server reads nothing else until it goes on. While it is held, a request does not time
    out and the connection does not end."""
    import json as _json

    conn = C.Connection.__new__(C.Connection)
    C.Connection.__init__(conn, _NullStream())
    p = C._Pending(1, "ctsim/alarms", 0.01)
    conn._pending[1] = p
    ev = lambda paused: _json.dumps(
        {"type": "event", "event": "ctsim/debugPaused", "body": {"paused": paused}}
    ).encode()
    conn._handle(ev(True))
    assert conn.held
    p.deadline = time.monotonic() - 1.0  # (its timeout is over)
    conn._expire()
    assert not p.event.is_set()  # no timeout while held
    conn._handle(ev(False))
    assert not conn.held and p.deadline > time.monotonic()  # its whole timeout again


class _NullStream:
    def read(self, timeout=None, size=65536):
        return None

    def write(self, data, timeout=30.0, hold=None):
        pass

    def close(self):
        pass

    closed = False
