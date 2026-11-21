"""Page states around open, reload and navigation."""

import os
import sys
import time

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


class _Drawn:
    """What the tab draws: every PageState it hands out, merged as the scene merges them."""

    def __init__(self, sim):
        self.sim = sim
        self.objects = {}

    def pump(self):
        st = self.sim.poll()
        while st is not None:
            if st.replace:
                self.objects = dict(st.objects)
            else:
                self.objects.update(st.objects)
            st = self.sim.poll()
        return self

    def move(self, an):
        self.pump()
        st = self.objects.get(an)
        return tuple(st.move) if st is not None and st.move is not None else None


def _late_wake(monkeypatch, seconds=0.3):
    """The thread that waits for openPage takes the answer late (the GUI is busy, the
    machine slow): the events after the answer arrive meanwhile."""
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation

    # (the client's openPage wait)
    owner = EngineSimulation if hasattr(EngineSimulation, "_open_request") else C.Session
    name = "_open_request" if owner is EngineSimulation else "open_page_full"
    orig = getattr(owner, name)

    def late(self, *a, **kw):
        out = orig(self, *a, **kw)
        time.sleep(seconds)
        return out

    monkeypatch.setattr(owner, name, late)


def _live(sim, srv):
    sim.start(srv.project_page, _rect_page())
    assert wait_for(lambda: sim.page_active())


def _server(page_file, servers, **kw):
    srv = F.FakeServer(os.path.dirname(page_file), servers, pages=True).start()
    srv.project_page = page_file
    srv.page_objects = {11: {"move": [0, 0]}, 12: {"move": [0, 0]}}
    for k, v in kw.items():
        setattr(srv, k, v)
    return srv


def test_a_reload_draws_the_changes_that_come_while_its_answer_is_taken(servers, hub, page_file, monkeypatch):
    """Reload Page: the page's first scans after the open run while the open is answered; a
    pageChanged that comes before the waiting thread has taken the answer is not replaced by
    the answer's state."""
    srv = _server(page_file, servers)
    sim = _sim(hub)
    try:
        _live(sim, srv)
        drawn = _Drawn(sim).pump()
        srv.open_then_change = [{"an": 11, "state": {"move": [40, 7]}}]
        _late_wake(monkeypatch)
        sim.reload(sim.model)
        assert wait_for(lambda: drawn.move(11) == (40, 7)), drawn.objects.get(11)
        time.sleep(0.4)
        assert drawn.move(11) == (40, 7)
    finally:
        sim.stop()
        srv.stop()


def test_a_page_change_written_before_its_reply_is_drawn(servers, hub, page_file):
    """The engine writes events from its event thread and replies from the request thread: a
    pageChanged made after the pageState reply's state can arrive first: the reply does not
    replace it."""
    srv = _server(
        page_file, servers, change_before_state={"objects": [{"an": 12, "state": {"move": [5, 9]}}]}
    )
    sim = _sim(hub)
    try:
        _live(sim, srv)
        drawn = _Drawn(sim)
        assert wait_for(lambda: drawn.move(12) == (5, 9)), drawn.pump().objects.get(12)
    finally:
        sim.stop()
        srv.stop()


def test_page_variables_of_a_change_newer_than_the_reply_are_taken(servers, hub, page_file):
    srv = _server(
        page_file,
        servers,
        change_before_state={"objects": [], "pageVars": [{"name": "ShowMenu", "type": "int", "value": 1}]},
    )
    sim = _sim(hub)
    try:
        _live(sim, srv)
        assert wait_for(lambda: [(t.name, t.value) for t in sim.page_vars()] == [("ShowMenu", 1)]), [
            (t.name, t.value) for t in sim.page_vars()
        ]
    finally:
        sim.stop()
        srv.stop()


def test_window_serials_order_the_states_across_page_displays(servers, hub, page_file, monkeypatch):
    """Page serials grow per window: of the events before the reopen's answer, the newer ones (by serial) are drawn, the earlier display's
    not; once live, a late event no newer than the state drawn is left out."""
    srv = _server(page_file, servers)
    sim = _sim(hub)
    try:
        _live(sim, srv)
        drawn = _Drawn(sim).pump()
        srv.scan = 10
        srv.open_serial = 10
        srv.open_events_first = [
            (5, [{"an": 11, "state": {"move": [66, 66]}}]),
            (12, [{"an": 12, "state": {"move": [8, 8]}}]),
        ]
        srv.open_then_change = None
        sim.reload(sim.model)
        assert wait_for(lambda: drawn.move(12) == (8, 8)), drawn.pump().objects.get(12)
        assert drawn.move(11) == (0, 0)
        # a late older event once the state is drawn
        srv.broadcast(
            "ctsim/pageChanged",
            {"window": 0, "page": "Mixer", "serial": 9, "objects": [{"an": 11, "state": {"move": [1, 1]}}]},
        )
        srv.broadcast(
            "ctsim/pageChanged",
            {"window": 0, "page": "Mixer", "serial": 13, "objects": [{"an": 12, "state": {"move": [2, 2]}}]},
        )
        assert wait_for(lambda: drawn.move(12) == (2, 2))
        assert drawn.move(11) == (0, 0)
    finally:
        sim.stop()
        srv.stop()
