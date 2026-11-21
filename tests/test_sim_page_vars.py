"""Page variables in the Simulate window."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402
from test_sim_engine_source import _rect_page, _sim, wait_for  # noqa: E402,F401
from qtkit import app as _app  # noqa: E402

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


def test_page_variables_are_listed_and_set(servers, hub, page_file):
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.status().connected and sim._window is not None)
        srv.set_page_vars(sim._window, {"ShowMenu": ("int", 0), "Mode": ("string", "Auto")})
        assert wait_for(lambda: {t.name for t in sim.page_vars()} == {"ShowMenu", "Mode"})
        pv = {t.name: t for t in sim.page_vars()}
        assert pv["ShowMenu"].type == "INT" and pv["ShowMenu"].page_var
        assert pv["Mode"].is_string() and pv["Mode"].value == "Auto"
        # they come with the page's tags, after them, also when only the page's own are shown
        rows, _total = sim.search_tags("", page_only=True)
        assert [t.name for t in rows if t.page_var] == ["ShowMenu", "Mode"]
        assert [t.name for t in sim.search_tags("show", page_only=True)[0] if t.page_var] == ["ShowMenu"]
        sim.set_page_var("ShowMenu", 1, "int")
        assert wait_for(lambda: any(cmd == "ctsim/setPageVar" for _c, cmd, _a in srv.requests))
        args = next(a for _c, cmd, a in srv.requests if cmd == "ctsim/setPageVar")
        assert args == {"window": sim._window, "name": "ShowMenu", "value": 1, "type": "int"}
        assert wait_for(lambda: {t.name: t.value for t in sim.page_vars()}["ShowMenu"] == 1)
        # a value that is not a whole number is not sent
        n = len(srv.requests)
        sim.set_page_var("ShowMenu", "abc", "int")
        assert not any(cmd == "ctsim/setPageVar" for _c, cmd, _a in srv.requests[n:])
    finally:
        sim.stop()
        srv.stop()


def test_inputs_panel_groups_and_sets_page_variables(servers, hub, page_file):
    _app()
    from ctgedit.ui.simulate.panels import InputsPanel, PAGE_VARS_GROUP

    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    sim = _sim(hub)
    panel = InputsPanel()
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.status().connected and sim._window is not None)
        srv.set_page_vars(sim._window, {"ShowMenu": ("int", 0), "PromptClose": ("int", 1)})
        assert wait_for(lambda: len(sim.page_vars()) == 2)
        panel.set_source(sim)
        panel.rebuild()
        m = panel.model
        groups = [m.item(r, 0).text() for r in range(m.rowCount())]
        assert PAGE_VARS_GROUP in groups
        g = m.item(groups.index(PAGE_VARS_GROUP), 0)
        assert [g.child(r, 0).text() for r in range(g.rowCount())] == ["ShowMenu", "PromptClose"]
        assert g.child(1, 1).text() == "1" and g.child(1, 2).text() == ""  # no quality
        # selecting one and typing a value sets the page variable, not a tag
        assert panel.select_tag("\x01pv:ShowMenu")
        t = panel.selected_tag()
        assert t.page_var and panel.quality.isHidden()  # (a page variable has no quality)
        panel._write(1)
        assert wait_for(lambda: any(cmd == "ctsim/setPageVar" for _c, cmd, _a in srv.requests))
        assert not any(cmd == "ctsim/setTag" for _c, cmd, _a in srv.requests)
    finally:
        panel.deleteLater()
        sim.stop()
        srv.stop()


def test_user_choice_and_locks(servers, hub, page_file):
    """The simulated user (ctsim/user, users, userChanged) and the locks (lockTag,
    setPageVar {locked}) through the Simulate window's source and the Inputs panel."""
    _app()
    from ctgedit.ui.simulate.panels import InputsPanel

    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    sim = _sim(hub)
    panel = InputsPanel()
    try:
        assert sim.want_user == "full"
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.status().connected and sim._window is not None)
        # a server ctgedit did not start keeps its login (the users reply says who)
        assert wait_for(lambda: sim.user_state()[1].get("mode") == "none")
        sim.set_user("full")
        assert wait_for(lambda: sim.user_state()[1].get("mode") == "full")
        assert wait_for(lambda: [u["name"] for u in sim.user_state()[2]] == ["Operator"])
        sim.set_user("user:Operator")
        assert wait_for(lambda: sim.user_state()[1].get("name") == "Operator")
        sim.set_user("none")
        assert wait_for(lambda: sim.user_state()[1].get("mode") == "none")
        # page variable lock
        srv.set_page_vars(sim._window, {"ShowMenu": ("int", 0)})
        assert wait_for(lambda: len(sim.page_vars()) == 1)
        panel.set_source(sim)
        panel.rebuild()
        assert panel.select_tag("\x01pv:ShowMenu")
        assert panel.lock.isVisible() is not None and panel.lock.isEnabled()
        panel.lock.setChecked(True)
        assert wait_for(
            lambda: any(cmd == "ctsim/setPageVar" and a.get("locked") is True for _c, cmd, a in srv.requests)
        )
        assert wait_for(lambda: sim.page_vars()[0].locked)
        # tag lock
        sim.set_locked("Abc", True)
        assert wait_for(
            lambda: any(
                cmd == "ctsim/lockTag" and a == {"name": "Abc", "locked": True} for _c, cmd, a in srv.requests
            )
        )
    finally:
        panel.deleteLater()
        sim.stop()
        srv.stop()


def test_parameters_the_page_read_are_listed_and_set(servers, hub, page_file):
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.status().connected)
        srv.set_params(
            [
                {
                    "section": "Page",
                    "name": "ScanTime",
                    "value": "250",
                    "default": "500",
                    "source": "param.dbf",
                    "reads": 2,
                    "users": ["tpl_scan", "AN 12 visible"],
                }
            ]
        )
        assert wait_for(lambda: (sim.poll(), [t.name for t in sim.params()])[1] == ["[Page]ScanTime"])
        t = sim.params()[0]
        assert (t.value, t.source, t.users, t.param) == (
            "250",
            "param.dbf",
            ("tpl_scan", "AN 12 visible"),
            True,
        )
        rows, _n = sim.search_tags("scantime", page_only=True)
        assert [r.name for r in rows] == ["[Page]ScanTime"]
        sim.set_param("[Page]ScanTime", "100")
        assert wait_for(lambda: srv.params[0]["value"] == "100")
        assert wait_for(lambda: (sim.poll(), sim.params()[0].source)[1] == "simulator")
        # the Inputs panel lists it under Parameters, the source in the Quality column
        _app()
        from ctgedit.ui.simulate.panels import PARAMS_GROUP, InputsPanel

        panel = InputsPanel()
        panel.set_source(sim)
        panel.refresh(force=True)
        m = panel.model
        groups = [m.item(k, 0).text() for k in range(m.rowCount())]
        assert PARAMS_GROUP in groups
        g = m.item(groups.index(PARAMS_GROUP), 0)
        assert g.child(0, 0).text() == "[Page]ScanTime" and g.child(0, 2).text() == "simulator"
        assert "tpl_scan" in g.child(0, 0).toolTip()
    finally:
        sim.stop()
        srv.stop()
