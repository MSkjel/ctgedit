"""Which compiled project (ROOT) a page's project runs as."""

import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")

FAKE = os.path.join(HERE, "fake_ctsim.py")
PY = getattr(sys, "_base_executable", "") or sys.executable


def fake_engine(*extra) -> C.EngineLocation:
    return C.EngineLocation(FAKE, "test", argv=[PY, FAKE] + list(extra))


@pytest.fixture
def servers(tmp_path, monkeypatch):
    folder = str(tmp_path / "servers")
    monkeypatch.setenv(C.SERVERS_ENV, folder)
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)
    return folder


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "User" / "Lib"
    p.mkdir(parents=True)
    return str(p)


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_entries_match_by_project_and_root(project, servers):
    a = F.FakeServer(project, servers, root="RootA", roots=["RootA", "RootB"]).start()
    try:
        [e] = C.servers_for(project, servers)
        assert e.root == "RootA" and e.same_root("roota") and e.same_root(None)
        assert not e.same_root("RootB")
        assert C.servers_for(project, servers, root="RootA") == [e]
        assert C.servers_for(project, servers, root="RootB") == []
        # an entry that names no root runs the project itself
        old = C.ServerEntry(path="", project=project)
        assert old.runs_as() == "Lib" and old.same_root("lib") and not old.same_root("RootA")
    finally:
        a.stop()


def test_a_server_of_another_root_is_never_attached_to(project, servers):
    a = F.FakeServer(project, servers, root="RootA", roots=["RootA", "RootB"]).start()
    try:
        with pytest.raises(C.ServerUnavailable):
            C.open_session(project, start=False, root="RootB")
        s = C.open_session(project, start=False, root="RootA")
        try:
            assert s.attached and s.root == "RootA" and s.roots == ["RootA", "RootB"]
            assert s.root_reason == "only"
            assert a.conns and a.conns[-1].hello.get("root") == "RootA"
            s.load()
            assert a.loaded[-1].get("root") == "RootA"
        finally:
            s.close()
        # no root asked: whatever root it runs as
        s = C.open_session(project, start=False)
        assert s.root == "RootA" and s.requested_root == ""
        s.close()
    finally:
        a.stop()


def test_the_hello_refuses_another_root_and_a_load_too(project, servers):
    a = F.FakeServer(project, servers, root="RootA").start()
    try:
        [e] = C.servers_for(project, servers)
        with pytest.raises(C.CtsimError) as err:
            C.connect(e, root="RootB")
        assert "--root RootB" in str(err.value)
        conn, hello = C.connect(e, root="RootA")
        try:
            assert hello["root"] == "RootA"
            with pytest.raises(C.CtsimError):
                conn.request("load", {"project": project, "root": "RootB"}, 5)
        finally:
            conn.close()
    finally:
        a.stop()


def test_a_started_server_gets_the_root(project, servers):
    s = C.open_session(project, location=fake_engine(), root="FxRun")
    try:
        assert not s.attached and s.root == "FxRun" and s.entry.root == "FxRun"
        assert "--root" in s.server.proc.args and "FxRun" in s.server.proc.args
        # another client for the same root joins it; one for another root does not
        other = C.open_session(project, location=fake_engine(), root="FxRun")
        assert other.attached and other.pid == s.pid
        other.close()
        third = C.open_session(project, location=fake_engine(), root="Other")
        try:
            assert not third.attached and third.pid != s.pid and third.root == "Other"
        finally:
            third.close()
    finally:
        s.close()


def test_the_hub_keeps_one_session_per_project_and_root(project, servers):
    a = F.FakeServer(project, servers, root="RootA").start()
    b = F.FakeServer(project, servers, root="RootB").start()
    hub = C.Hub(linger=0)
    try:
        sa = hub.acquire(project, root="RootA")
        sb = hub.acquire(project, root="RootB")
        assert sa is not sb and sa.root == "RootA" and sb.root == "RootB"
        assert hub.acquire(project.upper(), root="roota") is sa
        hub.release(sa)
        hub.release(sa)
        assert not sa.alive and sb.alive
        hub.release(sb)
        assert not sb.alive
    finally:
        hub.shutdown()
        a.stop()
        b.stop()


# -- the Simulate window's source -------------------------------------------------------------
def _sim(hub):
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.sources import FakeSimulation

    return EngineSimulation(hub=hub, fake=FakeSimulation())


def _page():
    from ctgedit.model.document import Page

    p = Page(name="Pg")
    p.appearance.width, p.appearance.height = 100, 100
    return p


def test_the_status_names_the_root_and_a_from_change_reconnects(project, servers, monkeypatch):
    from ctgedit.ui.simulate import ctsim_source as CS

    page = os.path.join(project, "Pg.ctg")
    open(page, "wb").close()
    a = F.FakeServer(
        project, servers, root="RootA", roots=["RootA", "RootB"], root_reason="firstByName"
    ).start()
    b = F.FakeServer(project, servers, root="RootB", roots=["RootA", "RootB"]).start()
    choice = {"root": "RootA"}
    monkeypatch.setattr(CS, "simulation_root", lambda p: choice["root"])
    hub = C.Hub(linger=0)
    sim = _sim(hub)
    try:
        sim.start(page, _page())
        assert wait_for(lambda: sim.session is not None and sim.status().connected)
        assert sim.session.root == "RootA"
        assert "'Lib', compiled as part of RootA" in sim.status().text
        assert "Compiled as part of RootA (of RootA, RootB)." in sim.status().detail
        # the same choice: nothing to do
        assert CS.root_choice_changed(project) == 0
        choice["root"] = "RootB"
        assert CS.root_choice_changed(project) == 1
        assert wait_for(
            lambda: sim.session is not None and sim.session.root == "RootB" and sim.status().connected
        )
        assert "compiled as part of RootB" in sim.status().text
        assert any(c.hello and c.hello.get("root") == "RootB" for c in b.conns)
    finally:
        sim.stop()
        hub.shutdown()
        a.stop()
        b.stop()


def test_a_project_compiled_on_its_own_shows_no_root(project, servers, monkeypatch):
    from ctgedit.ui.simulate import ctsim_source as CS

    page = os.path.join(project, "Pg.ctg")
    open(page, "wb").close()
    a = F.FakeServer(project, servers).start()  # root = Lib itself
    monkeypatch.setattr(CS, "simulation_root", lambda p: "Lib")
    hub = C.Hub(linger=0)
    sim = _sim(hub)
    try:
        sim.start(page, _page())
        assert wait_for(lambda: sim.status().connected)
        assert "'Lib'" in sim.status().text and "compiled as part of" not in sim.status().text
    finally:
        sim.stop()
        hub.shutdown()
        a.stop()


def test_simulation_root_is_the_tag_scope_root(monkeypatch):
    from ctgedit.io import tagscope as TS
    from ctgedit.ui.simulate import ctsim_source as CS

    class _Scope:
        root = "FxRun"

    monkeypatch.setattr(TS, "scope", lambda p, root=None: _Scope())
    assert CS.simulation_root(r"C:\x\FxInc") == "FxRun"
    _Scope.root = None
    assert CS.simulation_root(r"C:\x\Copy") is None
    assert CS.simulation_root("") is None
