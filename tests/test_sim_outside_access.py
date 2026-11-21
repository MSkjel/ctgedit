"""DLLs and SQL databases in the simulator."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402
from test_sim_engine_source import _app, _rect_page, _sim, hub, wait_for  # noqa: E402,F401

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


def test_both_are_off_by_default_and_the_lists_are_kept_as_lists():
    from ctgedit.ui.simulate import options

    assert options.OPTIONS["dll_access"] == ("sim/dll_access", "off")
    assert options.OPTIONS["sql_access"] == ("sim/sql_access", "off")
    assert options.access("dll_access") == "off" and options.access("sql_access") == "off"
    assert options.get_list("dll_allowed") == [] and options.get_list("sql_allowed") == []
    options.put("dll_access", "maybe")  # (a damaged setting is Off)
    assert options.access("dll_access") == "off"
    options.put("dll_access", "ask")
    assert options.access("dll_access") == "ask"
    assert options.add_to_list("dll_allowed", r"C:\Lib\a.dll")
    assert not options.add_to_list("dll_allowed", r"c:\lib\A.DLL")  # (case ignored)
    assert not options.add_to_list("dll_allowed", "  ")
    assert options.add_to_list("dll_allowed", r"C:\Lib\b.dll")
    assert options.get_list("dll_allowed") == [r"C:\Lib\a.dll", r"C:\Lib\b.dll"]
    assert options.parse_list("not json") == [] and options.parse_list('["x", "X", ""]') == ["x"]


def test_the_settings_show_what_each_mode_does():
    _app()
    from ctgedit.ui.style import theme
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate import outside_access as OA
    from ctgedit.ui.simulate.settings_dialog import SimSettingsDialog

    dlg = SimSettingsDialog(None)
    try:
        for ctl, kind in ((dlg.dll_access, "dll"), (dlg.sql_access, "sql")):
            assert ctl.value() == "off"
            assert [ctl.mode.itemText(k) for k in range(ctl.mode.count())] == ["Off", "Ask", "On"]
            seen = []
            for mode, role in (("off", "warn"), ("ask", "caution"), ("on", "danger")):
                ctl.set_value(mode)
                assert ctl.notice.text() == OA.NOTICES[kind][mode]
                assert ctl.notice.text().startswith(mode.capitalize() + ":")
                assert ctl.notice.property("noteRole") == role
                seen.append(ctl.notice.styleSheet())
            # yellow, orange, red: three different colours
            assert len(set(seen)) == 3 and theme.DARK["danger"] in seen[2]
        # the notices are plain text: no dashes, no shouting
        for kind in OA.NOTICES.values():
            for text in kind.values():
                assert "\u2014" not in text and "\u2013" not in text and "!" not in text
        dlg.dll_access.set_value("ask")
        dlg.sql_access.set_value("on")
        dlg.dll_access.set_allowed([r"C:\Lib\a.dll"])
        assert dlg.dll_access.allowed_button.text() == "Allowed DLLs (1)…"
        assert dlg.sql_access.allowed_button.text() == "Allowed Databases…"
        # the DLL call time limit: no limit by default (as the runtime), seconds otherwise
        assert dlg.dll_access.timeout.value() == 0 and dlg.dll_access.timeout.specialValueText() == "No limit"
        assert dlg.sql_access.timeout is None
        dlg.dll_access.timeout.setValue(30)
        dlg.store()
        assert options.access("dll_access") == "ask" and options.access("sql_access") == "on"
        assert options.get_list("dll_allowed") == [r"C:\Lib\a.dll"]
        assert options.dll_timeout() == 30 and dlg.choices()["sim/dll_timeout"] == 30
        assert dlg.choices()["sim/dll_access"] == "ask"
        assert dlg.choices()["sim/dll_allowed"] == '["C:\\\\Lib\\\\a.dll"]'
        # Restore Defaults: Off again; the allowed lists are the user's data and stay
        dlg.defaults()
        assert dlg.dll_access.value() == "off" and dlg.sql_access.value() == "off"
        assert dlg.dll_access.allowed == [r"C:\Lib\a.dll"]
    finally:
        dlg.deleteLater()
    d2 = SimSettingsDialog(None)
    try:
        assert d2.dll_access.value() == "ask" and d2.sql_access.value() == "on"
        assert d2.dll_access.allowed == [r"C:\Lib\a.dll"]
        assert d2.dll_access.timeout.value() == 30
    finally:
        d2.deleteLater()


def test_the_allowed_list_window():
    _app()
    from ctgedit.ui.simulate.outside_access import AllowedListDialog

    d = AllowedListDialog("sql", ["DSN=Plant", "dsn=plant", ""])
    try:
        assert d.windowTitle() == "Allowed Databases" and d.items() == ["DSN=Plant"]
        assert not d.remove_button.isEnabled()
        assert d.add("Driver={SQL Server}; Server=srv1; Database=History")
        assert not d.add("DSN=PLANT") and not d.add("  ")
        d.list.setCurrentRow(0)
        assert d.remove_button.isEnabled()
        d.remove_button.click()
        assert d.items() == ["Driver={SQL Server}; Server=srv1; Database=History"]
    finally:
        d.deleteLater()
    d = AllowedListDialog("dll", [])
    try:
        assert d.windowTitle() == "Allowed DLLs" and d.items() == []
    finally:
        d.deleteLater()


class _Svc:
    closed = False

    def __init__(self):
        self.answers = []

    def answer(self, dialog_id, **kw):
        self.answers.append(dict(id=dialog_id, **kw))
        return True


def test_the_question_and_its_four_answers():
    app = _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate.outside_access import PermissionDialog
    from ctgedit.ui.simulate.sim_dialogs import DialogHost

    host_win = QtWidgets.QWidget()
    host = DialogHost(host_win, lambda svc, w: None)
    always = []
    host.on_always = lambda access, target: always.append((access, target))
    svc = _Svc()

    def ask(did, **body):
        host.show(
            svc,
            dict(
                {
                    "id": did,
                    "window": 0,
                    "kind": "permission",
                    "title": "t",
                    "text": "x",
                    "choices": ["once", "session", "always", "deny"],
                },
                **body,
            ),
        )
        return next(w for w in host.widgets() if isinstance(w, PermissionDialog))

    try:
        d = ask(
            1, access="dll", action="load", target=r"C:\Windows\system32\user32.dll", detail="MessageBoxA"
        )
        assert d.windowTitle() == "Load a DLL?"
        assert d.target_label.text() == r"C:\Windows\system32\user32.dll"
        assert "MessageBoxA" in d.detail_view.text()
        assert d.deny_button.isDefault()
        assert [b.text() for b in (d.once_button, d.session_button, d.always_button, d.deny_button)] == [
            "Allow Once",
            "Allow for This Simulation",
            "Always Allow",
            "Deny",
        ]
        d.once_button.click()
        app.processEvents()
        assert svc.answers[-1] == {"id": 1, "ok": True, "permission": "once"}
        assert host.open_count() == 0 and always == []
        # a statement that can change a database: the statement is shown
        d = ask(
            2, access="sql", action="write", target="DSN=Plant", detail="DELETE FROM Batches WHERE Id = 7"
        )
        assert d.windowTitle() == "Change a Database?"
        assert d.detail_view.toPlainText() == "DELETE FROM Batches WHERE Id = 7"
        d.session_button.click()
        app.processEvents()
        assert svc.answers[-1] == {"id": 2, "ok": True, "permission": "session"}
        # Always Allow: the window is told, so the allowed list gets the database
        d = ask(3, access="sql", action="connect", target="DSN=Plant", detail="SQLConnect")
        assert d.windowTitle() == "Connect to a Database?" and d.detail_view is None
        d.always_button.click()
        app.processEvents()
        assert svc.answers[-1] == {"id": 3, "ok": True, "permission": "always"}
        assert always == [("sql", "DSN=Plant")]
        # Deny, and closing the window, deny
        d = ask(4, access="dll", action="load", target=r"C:\x.dll", detail="f")
        d.deny_button.click()
        app.processEvents()
        assert svc.answers[-1] == {"id": 4, "ok": False, "permission": "deny"}
        d = ask(5, access="dll", action="load", target=r"C:\x.dll", detail="f")
        d.close()
        app.processEvents()
        assert svc.answers[-1] == {"id": 5, "ok": False, "permission": "deny"}
        # the tab closes: denied (the engine reads ok false as deny)
        ask(6, access="dll", action="load", target=r"C:\x.dll", detail="f")
        host.cancel_window(svc, 0)
        assert svc.answers[-1] == {"id": 6, "ok": False, "choice": 0}
        assert host.open_count() == 0 and always == [("sql", "DSN=Plant")]
    finally:
        host.cancel_all()
        host_win.deleteLater()


def test_a_change_of_the_settings_goes_to_the_simulation(servers, hub, page_file):
    srv = F.FakeServer(os.path.dirname(page_file), servers, pages=True).start()
    sim = _sim(hub)
    sim.set_outside("ask", "off", [r"C:\Lib\a.dll"], [])
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        # a simulation another program started keeps its own choices when a tab joins it
        assert not any("dll" in r for r in (getattr(srv, "option_requests", None) or ()))
        sim.set_outside("on", "ask", [r"C:\Lib\a.dll"], ["DSN=Plant"], 30)
        then = {
            "dll": "on",
            "sql": "ask",
            "dllAllowed": [r"C:\Lib\a.dll"],
            "sqlAllowed": ["DSN=Plant"],
            "dllTimeout": 30,
        }
        assert wait_for(lambda: then in (getattr(srv, "option_requests", None) or ()))
        assert sim.outside_settings() == ("on", "ask", (r"C:\Lib\a.dll",), ("DSN=Plant",), 30)
        # Always Allow sends the lists alone: the simulation's modes stay its own
        sim.set_outside_allowed([r"C:\Lib\a.dll", r"C:\Lib\b.dll"], ["DSN=Plant"])
        lists = {"dllAllowed": [r"C:\Lib\a.dll", r"C:\Lib\b.dll"], "sqlAllowed": ["DSN=Plant"]}
        assert wait_for(lambda: lists in (getattr(srv, "option_requests", None) or ()))
        sim.set_outside("sometimes", "ON", [], [])  # (anything else is Off)
        assert sim.outside_settings()[:2] == ("off", "off")
    finally:
        sim.stop()
        srv.stop()


def test_a_simulation_ctgedit_starts_gets_the_choices_before_its_startup_code(servers, hub, page_file):
    script = os.path.join(HERE, "fake_ctsim.py")
    py = getattr(sys, "_base_executable", "") or sys.executable
    loc = C.EngineLocation(script, "test", argv=[py, script, "--pages", "--navigate", "--start-page", "Menu"])
    sim = _sim(hub, location=loc)
    sim.set_outside("ask", "on", [r"C:\Lib\a.dll"], ["DSN=Plant"])
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active(), timeout=20)
        # (the server's options now, as another client's hello shows them)
        s = C.open_session(os.path.dirname(page_file), location=loc, start=False)
        try:
            opts = s.hello.get("options") or {}
            assert (opts.get("dll"), opts.get("sql")) == ("ask", "on")
            assert opts.get("dllAllowed") == [r"C:\Lib\a.dll"]
            assert opts.get("sqlAllowed") == ["DSN=Plant"]
        finally:
            s.close()
    finally:
        sim.stop()


def test_always_allow_reaches_the_settings_and_every_simulation():
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate.window import SimulateWindow

    class Sim:
        def __init__(self):
            self.got = ("off", "off", (), (), 0)

        def outside_settings(self):
            return self.got

        def set_outside(self, dll, sql, dll_allowed, sql_allowed, dll_timeout=0):
            self.got = (dll, sql, tuple(dll_allowed), tuple(sql_allowed), dll_timeout)

        def set_outside_allowed(self, dll_allowed, sql_allowed):
            # (the lists only: the simulation's modes stay what they are)
            self.got = self.got[:2] + (tuple(dll_allowed), tuple(sql_allowed)) + self.got[4:]

    class Page:
        def __init__(self):
            self.sim = Sim()

    class Win:
        def __init__(self):
            self._pages = [Page(), Page()]

        def pages(self):
            return self._pages

        apply_outside_settings = SimulateWindow.apply_outside_settings
        _always_allow = SimulateWindow._always_allow

    options.put("dll_access", "ask")
    w = Win()
    w._always_allow("dll", r"C:\Windows\system32\user32.dll")
    assert options.get_list("dll_allowed") == [r"C:\Windows\system32\user32.dll"]
    for p in w.pages():
        # the lists reach every simulation; the modes are not sent (a simulation another
        # program started with DLLs on Ask keeps that, whatever ctgedit's setting is)
        assert p.sim.got == ("off", "off", (r"C:\Windows\system32\user32.dll",), (), 0)
    w._always_allow("sql", "DSN=Plant")
    w._always_allow("sql", "dsn=plant")  # (there already)
    assert options.get_list("sql_allowed") == ["DSN=Plant"]
    assert w.pages()[1].sim.got[3] == ("DSN=Plant",)
