"""Undefined tags and popup association tags."""

import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402
from test_sim_engine_source import _rect_page, _sim, wait_for  # noqa: E402,F401
from qtkit import app as _app  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")

UNDEFINED = [("Fx_11_PV01_SUMALM", "DIGITAL"), ("Fx_1_Step_stsStepNo", "INT")]


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


def _rows(sim):
    return {t.name: t for t in sim.search_tags("", page_only=True)[0]}


def test_client_shapes():
    d = C.normalise_tag(
        {
            "name": "X",
            "defined": False,
            "kind": "simulated",
            "simulated": True,
            "type": "INT",
            "typeFrom": "code",
            "via": "ToTW",
            "ref": "?TotW?",
        }
    )
    assert d["defined"] is False and d["simulated"] and d["type_from"] == "code"
    assert d["via"] == "ToTW" and d["ref"] == "?TotW?"
    assert C.normalise_tag({"name": "Y"})["defined"] is True
    assert C.Session.tag_args(None, None, 0, 10, window=2)["window"] == 2
    assert "window" not in C.Session.tag_args(None, "Mixer", 0, 10)
    assert C.Session.set_tag_args("X", 1, None, "DIGITAL") == {"name": "X", "value": 1, "type": "DIGITAL"}
    assert "type" not in C.Session.set_tag_args("X", 1)


def test_undefined_tags_are_listed_and_simulated_when_the_option_is_on(servers, hub, page_file):
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True, undefined_tags=UNDEFINED).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: "Fx_11_PV01_SUMALM" in _rows(sim))
        rows = _rows(sim)
        alm = rows["Fx_11_PV01_SUMALM"]
        assert not alm.defined and not alm.simulated and alm.type == "DIGITAL"
        assert rows["Fx_1_Step_stsStepNo"].type == "INT"
        assert rows["Tank1.Level"].defined
        # ctgedit asks with the window that shows the page
        assert any(cmd == "ctsim/tags" and a.get("window") == 0 for _c, cmd, a in srv.requests)
        # off (the default): a write is refused, nothing is turned on behind the user's back
        assert sim.simulating_undefined() is False
        sim.set_tag("Fx_11_PV01_SUMALM", 1)
        assert wait_for(lambda: any(e.kind == "error" and "Fx_11_PV01_SUMALM" in e.text for e in sim.drain()))
        assert not srv.simulate_undefined
        # on: the server makes a simulated tag of the guessed type; the list says so
        sim.set_simulate_undefined(True, user=True)
        assert wait_for(lambda: srv.simulate_undefined and sim.simulating_undefined())
        sim.set_tag("Fx_11_PV01_SUMALM", 1)
        assert wait_for(lambda: "fx_11_pv01_sumalm" in srv.simulated)
        assert srv.simulated["fx_11_pv01_sumalm"]["type"] == "DIGITAL"
        assert any(cmd == "ctsim/setTag" and a.get("type") == "DIGITAL" for _c, cmd, a in srv.requests)
        assert wait_for(lambda: _rows(sim)["Fx_11_PV01_SUMALM"].simulated)
        t = _rows(sim)["Fx_11_PV01_SUMALM"]
        assert t.value == 1 and not t.defined
        # another client turns it off: the tab follows the server
        srv.simulate_undefined = False
        srv.broadcast("ctsim/optionsChanged", {"simulateUndefined": False})
        assert wait_for(lambda: sim.simulating_undefined() is False)
        assert wait_for(lambda: not _rows(sim)["Fx_11_PV01_SUMALM"].simulated)
    finally:
        sim.stop()
        srv.stop()


def test_the_setting_turns_the_option_on_when_connecting(servers, hub, page_file):
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True, undefined_tags=UNDEFINED).start()
    sim = _sim(hub)
    try:
        sim.set_simulate_undefined(True)  # before the connection: sent on connect
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: srv.simulate_undefined)
        assert wait_for(lambda: sim.simulating_undefined() is True)
    finally:
        sim.stop()
        srv.stop()
    # a setting that is off never turns a server's option off on connect
    srv = F.FakeServer(proj, servers, pages=True, undefined_tags=UNDEFINED).start()
    srv.simulate_undefined = True
    sim = _sim(hub)
    try:
        sim.set_simulate_undefined(False)
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.simulating_undefined() is True)
        time.sleep(0.2)
        assert srv.simulate_undefined
        assert not any(cmd == "ctsim/options" for _c, cmd, _a in srv.requests)
    finally:
        sim.stop()
        srv.stop()


def test_a_popup_reads_its_tags_through_its_windows_associations(servers, hub, page_file):
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(
        proj,
        servers,
        pages=True,
        window_refs={1: [{"ref": "?TotW?", "via": "ToTW", "target": "Tag0007"}]},
        associations={1: {"ToTW": "Tag0007"}},
    ).start()
    srv.windows[1] = "!PM800Detail"
    sim = _sim(hub, attach_window=1, page_name="!PM800Detail")
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: "Tag0007" in _rows(sim))
        t = _rows(sim)["Tag0007"]
        assert t.via == "ToTW" and t.ref == "?TotW?" and t.defined
        assert any(
            cmd == "ctsim/tags" and a.get("window") == 1 and "page" not in a for _c, cmd, a in srv.requests
        )
        sim._windows_changed(srv.windows_body(), sim._gen)
        # the associations change: the tags follow
        srv.set_associations(
            1, {"ToTW": "Tag0009"}, refs=[{"ref": "?TotW?", "via": "ToTW", "target": "Tag0009"}]
        )
        assert wait_for(lambda: "Tag0009" in _rows(sim))
        assert "Tag0007" not in _rows(sim)
    finally:
        sim.stop()
        srv.stop()


def test_a_server_without_window_tags_is_asked_by_page(servers, hub, page_file):
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True, window_tags=False).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: "Tank1.Level" in _rows(sim))
        assert not any(cmd == "ctsim/tags" and "window" in a for _c, cmd, a in srv.requests)
    finally:
        sim.stop()
        srv.stop()


class _Src:
    """A tag source for the panel alone."""

    def __init__(self, tags):
        self._tags = tags
        self.writes = []
        self.option = []

    def search_tags(self, text, page_only=False):
        return list(self._tags), len(self._tags)

    def serial(self):
        return 1

    def set_tag(self, name, value, quality=None):
        self.writes.append((name, value))

    def set_simulate_undefined(self, on, user=False):
        self.option.append((on, user))


def test_the_inputs_panel_groups_undefined_tags_and_shows_associations():
    _app()
    from ctgedit.ui.prefs import settings
    from ctgedit.ui.simulate import panels
    from ctgedit.ui.simulate.sources import TagInfo

    settings.app_settings().remove(panels.SIMULATE_UNDEFINED_SETTING)
    tags = [
        TagInfo("Tank1.Level", type="REAL", value=1.0, equipment="Tank1", item="Level"),
        TagInfo("Raw\\TotW", type="REAL", value=2.0, via="ToTW", ref="?TotW?"),
        TagInfo("Fx_SUMALM", type="DIGITAL", value=None, quality="Bad", defined=False),
    ]
    p = panels.InputsPanel()
    src = _Src(tags)
    p.set_source(src)
    assert not p.simulate_undefined.isChecked()
    assert src.option == [(False, False)]
    root = [p.model.item(r, 0).text() for r in range(p.model.rowCount())]
    assert root[-1] == panels.UNDEFINED_GROUP
    grp = p.model.item(p.model.rowCount() - 1, 0)
    assert grp.child(0, 0).text() == "Fx_SUMALM"
    assert not p.tree.isColumnHidden(3)
    via_row = next(r for r in range(p.model.rowCount()) if p.model.item(r, 0).text() == "Raw\\TotW")
    assert p.model.item(via_row, 3).text() == "ToTW"
    assert "ToTW" in p.model.item(via_row, 0).toolTip()
    # an undefined tag cannot be set while the option is off
    assert p.select_tag("Fx_SUMALM")
    assert not p.stack.isEnabled()
    assert "Turn on Simulate undefined tags" in p.tag_info.text()
    p._toggle_digital(None)
    assert src.writes == []
    # on: remembered, sent to the source, the tag can be set
    p.simulate_undefined.setChecked(True)
    assert src.option[-1] == (True, True)
    assert settings.app_settings().value(panels.SIMULATE_UNDEFINED_SETTING, False, type=bool)
    assert p.stack.isEnabled()
    p._toggle_digital(None)
    assert src.writes == [("Fx_SUMALM", 1)]
    # a new panel starts with the remembered choice
    q = panels.InputsPanel()
    assert q.simulate_undefined.isChecked()
    # without associations the column is hidden
    q.set_source(_Src(tags[:1]))
    assert q.tree.isColumnHidden(3)
    p.deleteLater()
    q.deleteLater()


def test_the_tags_the_pages_cicode_used_have_their_group_and_say_where():
    _app()
    from ctgedit.ui.simulate import panels
    from ctgedit.ui.simulate.sources import TagInfo

    tags = [
        TagInfo("Tank1_Level", type="REAL", value=1.0, page_reads=True),
        TagInfo(
            "Tank1_LimitLow",
            type="REAL",
            value=0.0,
            page_reads=True,
            cicode_used=True,
            used_by="TankLevel (tanks.ci line 32)",
        ),
        TagInfo(
            "Ghost_LimitHigh",
            type="REAL",
            value=None,
            page_reads=True,
            defined=False,
            cicode_used=True,
            used_by="TankLevel (tanks.ci line 33)",
        ),
    ]
    p = panels.InputsPanel()
    p.set_source(_Src(tags))
    root = [p.model.item(r, 0).text() for r in range(p.model.rowCount())]
    assert root == ["Tank1_Level", panels.CICODE_GROUP]
    grp = p.model.item(1, 0)
    kids = [grp.child(k, 0) for k in range(grp.rowCount())]
    assert [k.text() for k in kids] == ["Tank1_LimitLow", "Ghost_LimitHigh"]
    assert "First used by TankLevel (tanks.ci line 32)" in kids[0].toolTip()
    assert "Not defined in the project" in kids[1].toolTip()
