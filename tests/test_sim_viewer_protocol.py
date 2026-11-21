"""The viewer side of the protocol (models, dialogs, display strings)."""

import gc
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402
from test_sim_engine_source import _rect_page, _sim, wait_for  # noqa: E402
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
    path = proj / "FxSim1.ctg"
    path.write_bytes(b"")
    return str(path)


@pytest.fixture
def hub():
    h = C.Hub(linger=0)
    yield h
    h.shutdown()


def _texts(sim):
    return [e.text for e in sim.drain()]


# -- the client ----------------------------------------------------------------------------
def test_revision_10_and_the_viewer_features_in_hello(servers, page_file):
    assert C.PROTOCOL_REVISION >= 10
    srv = F.FakeServer(os.path.dirname(page_file), servers, pages=True).start()
    try:
        # a client that does not answer them (a tool) says nothing: the engine would hold a
        # Cicode-opened page 3 s and a dialog's task 60 s for an answer that never comes
        plain = C.open_session(os.path.dirname(page_file), start=False)
        try:
            assert wait_for(lambda: any(c.hello for c in srv.conns))
            hello = next(c.hello for c in srv.conns if c.hello)
            assert "models" not in hello and "dialogs" not in hello
        finally:
            plain.close()
        s = C.open_session(os.path.dirname(page_file), start=False, viewer=True)
        try:
            assert wait_for(lambda: any((c.hello or {}).get("models") for c in srv.conns))
            hello = next(c.hello for c in srv.conns if (c.hello or {}).get("models"))
            assert hello["models"] is True and hello["dialogs"] is True
            assert s.supports("alarms") is not False and s.supports("equipment") is not False
            al = s.alarms(page="FxSim1")
            assert al.total == 2 and {a["tag"] for a in al.alarms} == {"Pump1_Fault", "Tank1_High"}
            assert all(a["state"] == "off" for a in al.alarms)
            assert s.set_alarm("Pump1_Fault", "on")["state"] == "unack_on"
            eq = s.equipment(filter="tank")
            assert eq.equipment == [
                {
                    "name": "Tank1",
                    "type": "Tank",
                    "scheduled": True,
                    "MODE": "0",
                    "STATE": "Off",
                    "DRMODE": "0",
                }
            ]
            assert s.set_equipment("Tank1", {"STATE": "Active"})["STATE"] == "Active"
            with pytest.raises(C.RequestFailed):
                s.set_alarm("NoSuchAlarm", "on")
        finally:
            s.close()
    finally:
        srv.stop()


def test_argument_and_body_shapes():
    s = C.Session.__new__(C.Session)
    s.project, s.requested_root = "X", "Root"
    assert C.Session.load_args(s, startup="auto") == {"project": "X", "root": "Root", "startup": "auto"}
    assert C.Session.answer_args(3, choice=201) == {"id": 3, "choice": 201}
    assert C.Session.answer_args("d1", ok=True, text="x", values=[1, "b"]) == {
        "id": "d1",
        "ok": True,
        "text": "x",
        "values": ["1", "b"],
    }
    assert C.Session.alarms_args("pump", window=2, page="P") == {"filter": "pump", "window": 2}
    assert C.Session.set_equipment_args("E", {"MODE": 1}) == {"name": "E", "fields": {"MODE": "1"}}
    al = C.alarm_list_from(
        {
            "total": 9,
            "start": 0,
            "serial": 4,
            "alarms": [
                {"tag": "A", "on": True, "ack": True},
                {"name": "no tag"},
                "junk",
                {"tag": "B", "onTime": "2026", "on": False, "ack": False},
            ],
        }
    )
    assert [a["tag"] for a in al.alarms] == ["A", "no tag", "B"] and al.serial == 4
    assert al.alarms[0]["state"] == "ack_on" and al.alarms[2]["state"] == "unack_off"
    eq = C.equipment_list_from({"equipment": [{"name": "E1", "mode": 2, "State": "On"}]})
    assert eq.equipment[0]["MODE"] == "2" and eq.equipment[0]["STATE"] == "On"


# -- the startup code ------------------------------------------------------------------------
def test_a_started_server_runs_the_startup_code_once(servers, hub, page_file):
    script = os.path.join(HERE, "fake_ctsim.py")
    py = getattr(sys, "_base_executable", "") or sys.executable
    # a project with a startup page: it shows in window 0, the startup code runs, then the page
    loc = C.EngineLocation(script, "test", argv=[py, script, "--pages", "--navigate", "--start-page", "Menu"])
    sim = _sim(hub, location=loc)
    other = None
    log = []
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(
            lambda: (log.extend(_texts(sim)), any("Startup: Startup runs as task 2" in t for t in log))[1],
            timeout=20,
        ), log
        assert any("Startup: _CitectBegin runs as task 1" in t for t in log)
        # a second tab of the same simulation asks nothing again
        other = _sim(hub, location=loc)
        other.start(page_file, _rect_page())
        assert wait_for(lambda: other.page_active(), timeout=10)
        assert not any("Startup" in t for t in _texts(other))
        assert not other.session.take_startup()
    finally:
        if other is not None:
            other.stop()
        sim.stop()


def test_no_startup_on_a_server_it_attached_to_or_when_turned_off(servers, hub, page_file):
    srv = F.FakeServer(os.path.dirname(page_file), servers, pages=True).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        assert not [a for a in srv.loaded if a.get("startup")]
    finally:
        sim.stop()
        srv.stop()
    # the setting off: _run_startup sends nothing even for the tab that drives
    s = type(
        "S",
        (),
        {
            "hello": {"protocolRevision": 10},
            "take_startup": lambda self: True,
            "request_async": lambda self, *a, **k: pytest.fail("sent"),
        },
    )()
    off = _sim(hub)
    off._own = True
    off.run_startup = False
    off._run_startup(s, off._gen)
    off.run_startup = True
    off._own = False
    off._run_startup(s, off._gen)  # not the driving tab
    # (a simulator of another protocol revision is refused before it gets here: open_session)


def test_pending_startup_and_notes_are_logged():
    sim = _sim(C.Hub(linger=0))
    sim._startup_done({"startupTasks": [], "pending": True, "notes": ["waits for window 0"]})
    t = _texts(sim)
    assert any("runs once window 0 shows a page" in x for x in t)
    assert any(x == "Startup: waits for window 0" for x in t)


# -- modelWanted ----------------------------------------------------------------------------
def test_a_cicode_opened_window_gets_its_drawing_once(servers, hub, page_file, fx_page):
    """The page the engine wants drawn is a copy of the fake install's FxSim2 (FxSim: a
    page with objects, so its model has some)."""
    app = _app()
    from ctgedit.ui.simulate.window import SimulateWindow

    proj = os.path.dirname(page_file)
    other = os.path.join(proj, "FxSim2.ctg")
    shutil.copyfile(fx_page("FxSim2", "FxSim"), other)
    srv = F.FakeServer(proj, servers, pages=True).start()
    win = SimulateWindow()
    pump = app.processEvents
    try:
        sim = _sim(hub)
        win.open_page(page_file, page=_rect_page(), sim=sim)
        assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
        mine = sim.window
        # PageDisplay / WinNew in the engine: it holds the window until its drawing comes
        srv.want_model(1, "FxSim2", other)
        assert wait_for(
            lambda: (win.tick(), any(w == 1 for w, _m in getattr(srv, "models", ())))[1], pump=pump
        )
        model = next(m for w, m in srv.models if w == 1)
        assert isinstance(model, dict) and model.get("objects")
        # then the window list names it as a child of the tab's window: a tab opens, and it
        # sends no second drawing
        srv.broadcast(
            "ctsim/windowsChanged",
            {
                "windows": [
                    {"window": mine, "page": "FxSim1", "parent": -1, "path": page_file, "hasModel": True},
                    {"window": 1, "page": "FxSim2", "parent": mine, "path": other, "hasModel": False},
                ]
            },
        )
        assert wait_for(lambda: (win.tick(), len(win.pages()) == 2)[1], timeout=15, pump=pump)
        pop = win.pages()[1]
        assert pop.attach_window == 1
        assert wait_for(lambda: (win.tick(), pop.sim.page_active())[1], pump=pump)
        for _k in range(10):
            win.tick()
            pump()
        assert [w for w, _m in srv.models] == [1]
        # more windowsChanged before the tab's own connection said its window: still one tab
        for _k in range(3):
            srv.broadcast(
                "ctsim/windowsChanged",
                {
                    "windows": [
                        {"window": mine, "page": "FxSim1", "parent": -1, "path": page_file, "hasModel": True},
                        {"window": 1, "page": "FxSim2", "parent": mine, "path": other, "hasModel": True},
                    ]
                },
            )
            assert wait_for(lambda: (win.tick(), True)[1], timeout=0.3, pump=pump)
        assert len(win.pages()) == 2
    finally:
        win.close()
        win.deleteLater()
        pump()
        srv.stop()
        gc.collect()


def test_a_wanted_page_is_found_through_the_projects():
    from ctgedit.ui.simulate.viewer import find_page_file, page_key
    import tempfile

    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "!Popup.ctg")
        open(p, "wb").close()
        assert find_page_file("Popup", "", [d]) == p
        assert find_page_file("!Popup", "", ["nowhere", d]) == p
        assert find_page_file("Other", "", [d]) == ""
        assert find_page_file("x", p, []) == p
        assert page_key("!PopUp ") == "popup"
    finally:
        shutil.rmtree(d)


def test_the_tab_key_counts_a_popup_tab_before_it_connects():
    from ctgedit.ui.simulate.window import SimulateWindow

    sess = object()
    tab = type("T", (), {})()
    tab.sim = type("Sim", (), {"session": None})()
    tab.engine_window = lambda: None
    tab.attach_window = 4
    tab.owner_session = sess
    assert SimulateWindow._tab_key(tab) == (id(sess), 4)
    tab.owner_session = None
    assert SimulateWindow._tab_key(tab) is None


# -- dialogs ----------------------------------------------------------------------------------
def test_the_page_dialogs_are_shown_and_answered(servers, hub, page_file):
    app = _app()
    from PySide6 import QtCore, QtWidgets
    from ctgedit.ui.simulate.window import SimulateWindow

    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    win = SimulateWindow()
    win.show()
    pump = app.processEvents

    def answer(did):
        return next((a for a in getattr(srv, "answers", ()) if a.get("id") == did), None)

    def shown(kind):
        assert wait_for(
            lambda: (win.tick(), any(isinstance(w, kind) for w in win.dialogs.widgets()))[1], pump=pump
        )
        return next(w for w in win.dialogs.widgets() if isinstance(w, kind))

    try:
        sim = _sim(hub)
        win.open_page(page_file, page=_rect_page(), sim=sim)
        assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
        w0 = sim.window
        # a popup menu (DspPopupMenu): submenus, disabled, checked, separators
        srv.show_dialog(
            {
                "id": 7,
                "window": w0,
                "kind": "popupMenu",
                "font": {"face": "Arial", "pt": 13.5, "height": 18, "bold": True},
                "x": 120,
                "y": 110,
                "items": [
                    {"label": "Open"},
                    {"separator": True},
                    {"label": "Off", "enabled": False},
                    {"label": "More", "menu": 1, "items": [{"label": "A"}, {"label": "B", "checked": True}]},
                ],
            }
        )
        menu = shown(QtWidgets.QMenu)
        acts = [a for a in menu.actions()]
        assert [a.text() for a in acts] == ["Open", "", "Off", "More"]
        assert acts[1].isSeparator() and not acts[2].isEnabled()
        sub = acts[3].menu()
        assert menu.font().family() == "Arial" and menu.font().pixelSize() == 18
        assert sub.font().bold() and sub.font().pixelSize() == 18
        b = sub.actions()[1]
        assert b.isChecked() and b.data() == 102
        b.trigger()
        assert wait_for(lambda: (win.tick(), answer(7) is not None)[1], pump=pump)
        assert answer(7) == {"id": 7, "choice": 102}
        # cancelled: the menu closes without a pick
        srv.show_dialog({"id": 8, "window": w0, "kind": "popupMenu", "items": [{"label": "Open"}]})
        menu = shown(QtWidgets.QMenu)
        assert menu.actions()[0].data() == 1
        menu.hide()
        assert wait_for(lambda: (win.tick(), answer(8) is not None)[1], pump=pump)
        assert answer(8) == {"id": 8, "choice": 0}
        # Message (MB_YESNO | MB_ICONQUESTION)
        srv.show_dialog(
            {
                "id": 9,
                "window": w0,
                "kind": "message",
                "mode": 4 | 0x20,
                "title": "Pump",
                "text": "Start the pump?",
            }
        )
        box = shown(QtWidgets.QMessageBox)
        assert box.text() == "Start the pump?" and box.windowTitle() == "Pump"
        assert box.windowModality() == QtCore.Qt.WindowModal
        box.button(QtWidgets.QMessageBox.Yes).click()
        assert wait_for(lambda: (win.tick(), answer(9) is not None)[1], pump=pump)
        assert answer(9) == {"id": 9, "ok": True, "choice": 6}
        # Input
        srv.show_dialog(
            {"id": 10, "window": w0, "kind": "input", "title": "Set point", "text": "Value:", "value": "12"}
        )
        dlg = shown(QtWidgets.QInputDialog)
        assert dlg.textValue() == "12" and dlg.labelText() == "Value:"
        dlg.setTextValue("42.5")
        dlg.accept()
        assert wait_for(lambda: (win.tick(), answer(10) is not None)[1], pump=pump)
        assert answer(10) == {"id": 10, "ok": True, "text": "42.5"}
        # a form
        from ctgedit.ui.simulate.sim_dialogs import SimFormDialog

        srv.show_dialog(
            {
                "id": 11,
                "window": w0,
                "kind": "form",
                "title": "Login",
                "fields": [
                    {"label": "", "value": "Log in", "kind": "prompt"},
                    {"label": "User", "value": "Op", "kind": "edit", "width": 16},
                    {"label": "Password", "value": "", "kind": "password"},
                    {"label": "Keep", "value": "0", "kind": "check"},
                ],
            }
        )
        form = shown(SimFormDialog)
        edits = form.findChildren(QtWidgets.QLineEdit)
        edits[1].setText("secret")
        form.findChildren(QtWidgets.QCheckBox)[0].setChecked(True)
        form.accept()
        assert wait_for(lambda: (win.tick(), answer(11) is not None)[1], pump=pump)
        assert answer(11) == {"id": 11, "ok": True, "values": [None, "Op", "secret", "1"]}
        # the engine stops waiting: the dialog closes without an answer
        srv.show_dialog({"id": 12, "window": w0, "kind": "message", "text": "x"})
        shown(QtWidgets.QMessageBox)
        srv.close_dialog(12)
        assert wait_for(lambda: (win.tick(), win.dialogs.open_count() == 0)[1], pump=pump)
        for _k in range(5):
            win.tick()
            pump()
        assert answer(12) is None
        # the simulation keeps running while a dialog is open; closing the tab cancels it
        srv.show_dialog({"id": 13, "window": w0, "kind": "input", "text": "?"})
        shown(QtWidgets.QInputDialog)
        win.close_tab(0)
        assert wait_for(lambda: answer(13) is not None, pump=pump)
        assert answer(13) == {"id": 13, "ok": False, "choice": 0}
        assert win.dialogs.open_count() == 0
    finally:
        win.close()
        win.deleteLater()
        pump()
        srv.stop()
        gc.collect()


def test_engine_shaped_dialogs():
    """Dialogs in the engine's shapes."""
    app = _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate.sim_dialogs import DialogHost, SimFormDialog

    class Svc:
        closed = False

        def __init__(self):
            self.answers = []

        def answer(self, dialog_id, **kw):
            self.answers.append(dict(id=dialog_id, **kw))
            return True

    host_win = QtWidgets.QWidget()
    host = DialogHost(host_win, lambda svc, w: None)
    svc = Svc()
    try:
        body = {
            "id": 1,
            "window": 0,
            "kind": "form",
            "title": "Recipe",
            "mode": 0,
            "fields": [
                {"index": 0, "label": "Recipe", "value": "", "kind": "text"},
                {"index": 1, "label": "Number", "value": "12", "kind": "edit", "width": 8, "maxLength": 4},
                {"index": 2, "label": "Auto", "value": "1", "kind": "check"},
                {"index": 3, "label": "Load", "value": "", "kind": "button"},
                {"index": 4, "label": "OK", "value": "", "kind": "ok"},
                {"index": 5, "label": "Cancel", "value": "", "kind": "cancel"},
            ],
            "buttons": ["Load", "OK", "Cancel"],
        }
        host.show(svc, body)
        form = next(w for w in host.widgets() if isinstance(w, SimFormDialog))
        edit = form.findChildren(QtWidgets.QLineEdit)[0]
        assert edit.text() == "12" and edit.maxLength() == 4
        bb = form.findChildren(QtWidgets.QDialogButtonBox)[0]
        # (a Help button may be added to every dialog once help is wired up)
        assert {"Load", "OK", "Cancel"} <= {b.text() for b in bb.buttons()}
        load = next(b for b in bb.buttons() if b.text() == "Load")
        load.click()
        app.processEvents()
        assert svc.answers == [{"id": 1, "press": 3}] and host.open_count() == 1
        # the button's function changed a field: the engine sends the form again
        edit.setText("99")
        body2 = dict(body, fields=[dict(f) for f in body["fields"]])
        body2["fields"][2]["value"] = "0"
        host.show(svc, body2)
        assert host.open_count() == 1 and edit.text() == "99"
        assert not form.findChildren(QtWidgets.QCheckBox)[0].isChecked()
        next(b for b in bb.buttons() if b.text() == "OK").click()
        app.processEvents()
        assert svc.answers[-1] == {
            "id": 1,
            "ok": True,
            "values": [None, "99", "0", None, None, None],
            "choice": None,
        }
        assert host.open_count() == 0
        # Message with the engine's named buttons = standard buttons (Enter / Esc work)
        host.show(
            svc,
            {
                "id": 2,
                "window": 0,
                "kind": "message",
                "title": "T",
                "text": "Go?",
                "mode": 1,
                "buttons": ["OK", "Cancel"],
            },
        )
        box = next(w for w in host.widgets() if isinstance(w, QtWidgets.QMessageBox))
        assert box.button(QtWidgets.QMessageBox.Cancel) is not None
        box.button(QtWidgets.QMessageBox.Cancel).click()
        app.processEvents()
        assert svc.answers[-1] == {"id": 2, "ok": False, "choice": 2}
        # Input: the prompt is the field's label when 'text' is empty
        host.show(
            svc,
            {
                "id": 3,
                "window": 0,
                "kind": "input",
                "title": "Set",
                "text": "",
                "fields": [{"label": "Value:", "value": "7", "kind": "edit", "width": 254}],
                "buttons": ["OK", "Cancel"],
            },
        )
        dlg = next(w for w in host.widgets() if isinstance(w, QtWidgets.QInputDialog))
        assert dlg.labelText() == "Value:" and dlg.textValue() == "7"
        host.cancel_window(svc, None)  # a tab without an engine window: nothing goes
        assert host.open_count() == 1
        host.cancel_window(svc, 0)
        assert svc.answers[-1] == {"id": 3, "ok": False, "choice": 0}
        # the engine's popup items: a separator item, a link to itself (no items)
        menu = QtWidgets.QMenu()
        from ctgedit.ui.simulate.sim_dialogs import build_menu

        build_menu(
            menu,
            [
                {"label": "A", "enabled": True, "checked": False, "separator": False, "id": 1},
                {"label": "", "enabled": False, "checked": False, "separator": True, "id": 0},
                {
                    "label": "Loop",
                    "enabled": True,
                    "checked": False,
                    "separator": False,
                    "id": 0,
                    "items": [],
                },
            ],
        )
        acts = menu.actions()
        assert acts[0].data() == 1 and acts[1].isSeparator()
        assert acts[2].menu() is not None and not acts[2].menu().isEnabled()
    finally:
        host.cancel_all()
        host_win.deleteLater()
        app.processEvents()


def test_set_alarm_shows_the_listed_state_at_once():
    from ctgedit.ui.simulate import ctsim_source as CS

    assert CS.LISTED_STATE == {
        "on": "unack_on",
        "on_ack": "ack_on",
        "off_unack": "unack_off",
        "off": "off",
        "disabled": "disabled",
    }
    from ctgedit.ui.simulate.alarm_panels import STATE_SHOWN

    assert set(CS.LISTED_STATE.values()) <= set(STATE_SHOWN)


def test_menu_numbers_follow_dsppopupmenu():
    from ctgedit.ui.simulate.sim_dialogs import menu_choice

    assert menu_choice({"label": "x"}, 0, 3) == 3
    assert menu_choice({"label": "x"}, 2, 1) == 201
    assert menu_choice({"label": "x", "id": 507}, 0, 1) == 507


# -- alarms and equipment --------------------------------------------------------------------
def test_alarm_and_equipment_rows_from_the_simulation(servers, hub, page_file):
    srv = F.FakeServer(os.path.dirname(page_file), servers, pages=True).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        assert sim.alarms_note() == ""
        assert wait_for(lambda: sim.alarm_rows("", True)[0] is not None)
        rows, total = sim.alarm_rows("", True)
        assert total == 2 and srv.alarm_queries[-1].get("window") == sim.window
        rows, total = sim.alarm_rows("", False)
        assert wait_for(lambda: sim.alarm_rows("", False)[1] == 3)
        sim.set_alarm("Tank1_High", "on_ack")
        # shown at once in the list's own word (ack_on, not setAlarm's on_ack)
        assert any(
            a["tag"] == "Tank1_High" and a["state"] == "ack_on" for a in (sim.alarm_rows("", False)[0] or ())
        )
        assert wait_for(
            lambda: getattr(srv, "alarm_sets", None) == [{"name": "Tank1_High", "state": "on_ack"}]
        )
        assert wait_for(
            lambda: any(
                a["tag"] == "Tank1_High" and a["state"] == "ack_on"
                for a in (sim.alarm_rows("", False)[0] or ())
            )
        )
        assert any("alarm Tank1_High = Acknowledged" in e.text for e in sim.drain())
        # an alarmsChanged from elsewhere (a scan, another client) asks again
        srv._alarm_rows()
        srv.alarm_states["pump1_fault"] = "on"
        srv.broadcast("ctsim/alarmsChanged", {"serial": 99})
        assert wait_for(
            lambda: any(
                a["tag"] == "Pump1_Fault" and a["state"] == "unack_on"
                for a in (sim.alarm_rows("", False)[0] or ())
            )
        )
        assert wait_for(lambda: sim.equipment_rows("")[0] is not None)
        sim.set_equipment("Pump1", "MODE", "2")
        assert wait_for(
            lambda: any(e["name"] == "Pump1" and e["MODE"] == "2" for e in (sim.equipment_rows("")[0] or ()))
        )
        assert wait_for(
            lambda: getattr(srv, "equipment_sets", None) == [{"name": "Pump1", "fields": {"MODE": "2"}}]
        ), [e.text for e in sim.drain()]
        assert set(sim.page_equipment()) >= {"Pump1", "Tank1"}
    finally:
        sim.stop()
        srv.stop()


def test_the_alarms_and_equipment_tabs(servers, hub, page_file):
    app = _app()
    from ctgedit.ui.simulate.window import SimulateWindow
    from ctgedit.ui.simulate import alarm_panels as AP

    srv = F.FakeServer(os.path.dirname(page_file), servers, pages=True).start()
    win = SimulateWindow()
    win.show()
    pump = app.processEvents
    try:
        sim = _sim(hub)
        win.open_page(page_file, page=_rect_page(), sim=sim)
        assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
        assert [win.input_tabs.tabText(k) for k in range(3)] == ["Tags", "Alarms", "Equipment"]
        win.input_tabs.setCurrentWidget(win.alarms)
        panel = win.alarms
        assert wait_for(lambda: (win.tick(), panel.model.rowCount() == 2)[1], pump=pump)
        assert panel.count.text() == "2 alarms"
        panel.page_only.setChecked(False)
        assert wait_for(lambda: (win.tick(), panel.model.rowCount() == 3)[1], pump=pump)
        panel._select("Pump1_Fault")
        assert panel.state.isEnabled() and panel.state.currentData() == "off"
        k = panel.state.findData("on")
        panel.state.setCurrentIndex(k)
        panel._state_chosen(k)
        assert wait_for(lambda: srv.alarm_sets[-1:] == [{"name": "Pump1_Fault", "state": "on"}], pump=pump)
        assert wait_for(lambda: (win.tick(), panel.model.item(0, 1).text() == "On")[1], pump=pump)
        assert not panel.model.item(0, 1).icon().isNull()
        # the search reaches the simulator
        panel.search.setText("tank")
        panel.search.flush()
        assert wait_for(lambda: (win.tick(), panel.model.rowCount() == 1)[1], pump=pump)
        # equipment: the page's own (and their parents), MODE / STATE / DRMODE editable
        win.input_tabs.setCurrentWidget(win.equipment)
        ep = win.equipment
        assert wait_for(lambda: (win.tick(), ep.model.rowCount() == 2)[1], pump=pump)
        names = [ep.model.item(r, 0).text() for r in range(ep.model.rowCount())]
        assert names == ["Pump1", "Tank1"]
        ep.page_only.setChecked(False)
        assert wait_for(lambda: (win.tick(), ep.model.rowCount() == 3)[1], pump=pump)
        state = ep.model.item(1, 3)
        assert state.isEditable() and not ep.model.item(1, 0).isEditable()
        state.setText("Active")
        assert wait_for(
            lambda: (
                getattr(srv, "equipment_sets", [])[-1:] == [{"name": "Tank1", "fields": {"STATE": "Active"}}]
            ),
            pump=pump,
        )
        assert AP._in_page("Area9", ["Area9.Fan"]) and not AP._in_page("Area", ["Area9.Fan"])
    finally:
        win.close()
        win.deleteLater()
        pump()
        srv.stop()
        gc.collect()


def test_the_panels_say_why_on_demo_values():
    _app()
    from ctgedit.ui.simulate.alarm_panels import AlarmsPanel, EquipmentPanel
    from ctgedit.ui.simulate.sources import FakeSimulation

    for cls in (AlarmsPanel, EquipmentPanel):
        p = cls()
        p.set_source(FakeSimulation())
        assert "demo" in p.count.text()
        p.set_source(None)
        assert p.count.text() == ""


def test_the_startup_setting_round_trip():
    _app()
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate.settings_dialog import SimSettingsDialog

    d = SimSettingsDialog()
    assert d.startup.isChecked() and options.get("run_startup") is True
    d.startup.setChecked(False)
    d.store()
    assert options.get("run_startup") is False
    d2 = SimSettingsDialog()
    assert not d2.startup.isChecked()
    d2.defaults()
    d2.store()
    assert options.get("run_startup") is True


def test_a_wanted_drawing_of_an_editor_page_is_never_made_again_on_the_gui_thread(monkeypatch, tmp_path):
    # the document's model is made in a helper process (page_model_job): while the job runs the
    # answer waits for the next tick
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.simulate import window as W

    page = tmp_path / "Big.ctg"
    page.write_bytes(b"x")

    class Job:
        def __init__(self):
            self.ready = False

        def done(self):
            return self.ready

        def result(self, timeout=None):
            return {"format": "ctsim-page/0", "page": "Big"}

    job = Job()
    made = []
    monkeypatch.setattr(W, "page_model_job", lambda path, doc: job)
    monkeypatch.setattr(W, "page_model", lambda *a, **k: made.append(a) or {})

    class Tab:
        doc = object()
        page_path = str(page)
        sim = type("Sim", (), {"_add_log": lambda self, e: None, "_now": lambda self: 0.0})()

    class Svc:
        closed = False

        def __init__(self):
            self.sent = []

        def search_dirs(self):
            return [str(tmp_path)]

        def send_model(self, window, pg, model, done=None):
            self.sent.append((window, pg, model))
            return True

        def send_no_model(self, window, pg):
            self.sent.append((window, pg, None))
            return True

    win = W.SimulateWindow()
    try:
        monkeypatch.setattr(win, "pages", lambda: [Tab()])
        svc = Svc()
        win._send_wanted_model(svc, {"window": 3, "page": "Big", "path": str(page)}, Tab())
        assert svc.sent == [] and len(win._wanted_later) == 1
        monkeypatch.setattr(win, "_services", lambda: [])
        win._serve_viewers()  # still running: asked again later
        assert svc.sent == [] and len(win._wanted_later) == 1
        job.ready = True
        win._serve_viewers()
        assert svc.sent == [(3, "Big", {"format": "ctsim-page/0", "page": "Big"})]
        assert win._wanted_later == [] and made == []
    finally:
        monkeypatch.undo()
        win.close()
