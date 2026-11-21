"""Simulate pages on the real engine."""

import gc
import os
import shutil
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from ctgedit.sim import client as C  # noqa: E402
from livekit import R2_USER, write_dbf  # noqa: E402
from livekit import real_engine as _real_engine  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")

#: the projects copied (Example, ExampleSA and what they include, the implicit ones)
CHAIN = (
    "Example",
    "SxW_Style_Include",
    "Library_Equipment",
    "ExampleSA",
    "SA_Include",
    "SA_Library",
    "IndustrialGraphics_Include",
    "Include",
    "System",
)
#: the drawing files the tests read
PAGES = {
    "Example": ("Mixer", "Dynamic_1", "!EnergyStartStop", "ProcessLine"),
    "ExampleSA": ("Mixing_L3", "Supply_L3"),
}


def _wanted(project: str, name: str) -> bool:
    low = name.lower()
    if low.startswith("pg") and low.endswith(".dbf"):
        return False  # the page rows: the documents export their own
    if low.endswith((".dbf", ".ci", ".rdb", ".ini")):
        return True
    return low.endswith(".ctg") and os.path.splitext(name)[0].lower() in {
        p.lower() for p in PAGES.get(project, ())
    }


@pytest.fixture(scope="module")
def install(tmp_path_factory):
    loc, tried = _real_engine()
    if loc is None:
        pytest.skip("ctsim.exe is not built (looked in %s)" % ", ".join(tried))
    root = tmp_path_factory.mktemp("ctsim_pages")
    user = os.path.join(str(root), "inst", "User")
    rows = []
    for name in CHAIN:
        src = os.path.join(R2_USER, name)
        if not os.path.isdir(src):
            pytest.skip("the R2 project %s is not installed" % name)
        dst = os.path.join(user, name)
        os.makedirs(dst)
        for f in os.listdir(src):
            if _wanted(name, f) and os.path.isfile(os.path.join(src, f)):
                shutil.copyfile(os.path.join(src, f), os.path.join(dst, f))
        rows.append({"NAME": name, "PATH": dst})
    write_dbf(os.path.join(user, "MASTER.DBF"), [("NAME", 64), ("PATH", 200)], rows)
    yield {"loc": loc, "user": user, "root": str(root)}
    shutil.rmtree(str(root), ignore_errors=True)


@pytest.fixture
def env(install, monkeypatch, tmp_path):
    monkeypatch.setenv(C.SERVERS_ENV, str(tmp_path / "servers"))
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)
    # the art of the copied pages: the live libraries, read only
    monkeypatch.setenv("CTG_LIB_PATH", os.pathsep.join(os.path.join(R2_USER, p) for p in CHAIN))
    hub = C.Hub(linger=0)
    yield install, hub
    hub.shutdown()


def page_path(install, project, page):
    return os.path.join(install["user"], project, page + ".ctg")


def wait_for(cond, timeout=30.0, pump=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pump is not None:
            pump()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _engine_sim(install, hub, path, doc=None, startup=True):
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.window import page_model, runtime_page

    sim = EngineSimulation(hub=hub, location=install["loc"])
    sim.run_startup = startup
    sim.model = page_model(path, doc)
    page = runtime_page(path, getattr(doc, "parsed", None))
    sim.start(path, page)
    sim.clock("run")  # as the Simulate window does when it opens a page
    return sim, page


def _wait_live(sim):
    assert wait_for(lambda: sim.page_active() or (sim.status().level == "warn" and not sim.status().busy)), (
        sim.status()
    )
    if not sim.page_active():
        caps = (sim.session.hello.get("capabilities") or {}) if sim.session else {}
        if not caps.get("pages"):
            pytest.skip("this ctsim.exe has no page animator (%s)" % sim.status().text)
        pytest.fail(sim.status().text)


class _States:
    """Every state the simulation sent the page, merged as the scene merges them."""

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

    def get(self, an):
        self.pump()
        return self.objects.get(an)


def _an_index(doc, an):
    for i, o in enumerate(doc.page.objects):
        if o.an == an:
            return i
    raise AssertionError("no AN %d on the page" % an)


def test_mixer_runs_on_the_engine_and_follows_a_tag(env):
    install, hub = env
    path = page_path(install, "Example", "Mixer")
    # without the project's startup code: it writes Mixer_Sugar itself (a recipe, 306.5),
    # sometimes after the test's write
    sim, _page = _engine_sim(install, hub, path, startup=False)
    try:
        _wait_live(sim)
        assert not sim.status().demo
        # a saved page whose .RDB is current runs its compiled code
        assert sim.model["compiled"]["current"] and sim.open_source == "rdb"
        states = _States(sim)
        assert wait_for(lambda: states.get(542) is not None)
        sim.set_tag("Mixer_Sugar", 42.5)
        assert wait_for(lambda: "42.5" in (states.get(542).text or "")), states.get(542)
        # the tags it reads: the code it runs
        assert wait_for(lambda: any(t.name == "Mixer_Sugar" for t in sim.tags(page_only=True)))
    finally:
        sim.stop()


def test_a_button_click_runs_its_command(env):
    """Example Dynamic_1: the 'Hide' button (AN 523, Toggle(Hide)) hides the objects whose
    Hidden when is Hide (pgdynobj VISIBLE 'Hide': ANs 518, 520 ... 528) and itself. (Other
    objects of the page show or hide meanwhile -- the Library_Controls tables initialise -- so
    the gated ANs are looked at, not how many are hidden.)"""
    install, hub = env
    path = page_path(install, "Example", "Dynamic_1")
    sim, _page = _engine_sim(install, hub, path)
    gated = (518, 520, 521, 522, 527, 528)
    try:
        _wait_live(sim)
        sim.set_tag("Hide", 0)
        states = _States(sim)
        from ctgedit.ui.simulate.sources import InputEvent

        def shown(an):
            st = states.objects.get(an)
            return st is None or st.shown

        assert wait_for(lambda: (states.pump(), all(shown(a) for a in gated + (523,)))[1]), {
            a: shown(a) for a in gated + (523,)
        }
        sim.input(InputEvent("down", an=523, ans=(523,), x=10, y=10))
        sim.input(InputEvent("up", an=523, ans=(523,), x=10, y=10))
        assert wait_for(lambda: (states.pump(), not any(shown(a) for a in gated + (523,)))[1]), {
            a: shown(a) for a in gated + (523,)
        }
        assert sim.session.subscribe(["Hide"])[0]["value"] in (1, 1.0, "1")
    finally:
        sim.stop()


def test_an_unsaved_edit_reaches_the_engine(env):
    """An edit in the editor (the sugar display now shows Mixer_Malt), not saved: the engine
    compiles the model's rows and runs them."""
    from ctgedit.editing import EditableDocument

    install, hub = env
    path = page_path(install, "Example", "Mixer")
    doc = EditableDocument.open(path)
    assert doc.set_property(_an_index(doc, 542), "TXT_STR", "Mixer_Malt")
    assert doc.dirty
    sim, _page = _engine_sim(install, hub, path, doc=doc)
    try:
        _wait_live(sim)
        assert sim.model["source"] == "unsaved"
        assert sim.open_source == "rows"
        states = _States(sim)
        sim.set_tag("Mixer_Sugar", 11.0)
        sim.set_tag("Mixer_Malt", 77.5)
        assert wait_for(lambda: "77.5" in (states.get(542).text or "")), states.get(542)
    finally:
        sim.stop()
    with open(path, "rb") as fh:
        assert fh.read() == open(os.path.join(R2_USER, "Example", "Mixer.ctg"), "rb").read()


def test_an_examplesa_page_runs_on_the_engine(env):
    """ExampleSA Mixing_L3 (super genie popups, SA library): its states come from the engine;
    the mixing tank's level (LS07.PV, conformance case tank_level) shows on its value text."""
    install, hub = env
    path = page_path(install, "ExampleSA", "Mixing_L3")
    sim, _page = _engine_sim(install, hub, path)
    try:
        _wait_live(sim)
        states = _States(sim)
        assert wait_for(lambda: (states.pump(), len(states.objects) > 100)[1])
        texts0 = {an: st.text for an, st in states.objects.items() if st.text}
        sim.set_tag("Company.TopMilk.Mixing.LS07.PV", 120)
        assert wait_for(
            lambda: (
                states.pump(),
                any(
                    st.text and "120" in st.text and texts0.get(an) != st.text
                    for an, st in states.objects.items()
                ),
            )[1]
        ), sorted((an, st.text) for an, st in states.objects.items() if st.text)[:40]
    finally:
        sim.stop()


def test_the_simulate_window_reloads_an_edited_page_and_closes_it(env):
    """The window: the page from the engine, an edit in the editor offered as Reload Page,
    the reload simulates the edit, closing the tab closes the page in the engine."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.window import SimulateWindow

    install, hub = env
    path = page_path(install, "Example", "Mixer")
    doc = EditableDocument.open(path)
    from ctgedit.ui.simulate import options as sim_options

    sim_options.put("reload_page", False)  # (the manual Reload Page path)
    win = SimulateWindow()
    pump = QtWidgets.QApplication.processEvents
    try:
        sim = EngineSimulation(hub=hub, location=install["loc"])
        sim.run_startup = False  # (its startup code writes the Mixer tags itself)
        sp = win.open_page(path, sim=sim, doc=doc, parsed=doc.parsed)
        win.show()
        assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
        sim.set_tag("Mixer_Malt", 33.0)
        assert not win.edit_row.isVisibleTo(win)
        assert doc.set_property(_an_index(doc, 542), "TXT_STR", "Mixer_Malt")
        win._show_edit_note()
        assert win.edit_row.isVisibleTo(win)
        win.reload_button.click()
        assert not win.edit_row.isVisibleTo(win)
        # the object shows the edited expression: Mixer_Malt's value NOW (the engine's
        # plant model moves the hopper on)

        def shows_malt():
            win.tick()
            try:
                shown = float(str(getattr(sp.scene.sim_states().get(542), "text", "") or ""))
            except ValueError:
                return False
            tag = sim._session.request("tags", {"names": ["Mixer_Malt"]}, 10)["tags"][0]
            return tag.get("value") is not None and abs(shown - float(tag["value"])) < 0.01

        assert wait_for(shows_malt, pump=pump)
        # closing the tab closes the page in the engine (its close events run)
        keep = hub.acquire(sim.project, location=install["loc"])
        w = sim.window
        assert any(x["window"] == w and x["page"] == "Mixer" for x in keep.windows())
        win.close_tab(0)
        assert wait_for(lambda: not any(x["window"] == w and x["page"] for x in keep.windows()))
        hub.release(keep)
    finally:
        win.close()
        win.deleteLater()
        pump()
        gc.collect()


def test_a_popup_gets_a_tab_and_a_page_change_is_followed(env):
    """The window on an engine with the display built-ins (WinNewAt, PageDisplay): a click on
    a Mixer power meter (AssMetaDataPopup) opens a window the tab's page owns: it gets a tab of
    its own, attached to it, and the simulation's clock keeps running; closing that tab frees
    the window; a click that runs PageDisplay shows the new page in the same tab."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.sources import InputEvent
    from ctgedit.ui.simulate.window import SimulateWindow

    install, hub = env
    path = page_path(install, "Example", "Mixer")
    win = SimulateWindow()
    pump = QtWidgets.QApplication.processEvents
    try:
        sim = EngineSimulation(hub=hub, location=install["loc"])
        sp = win.open_page(path, sim=sim)
        win.show()
        assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
        assert wait_for(lambda: sim.clock_state().running, pump=pump)
        sim.input(InputEvent("down", an=556, ans=(556,), x=100, y=100))
        sim.input(InputEvent("up", an=556, ans=(556,), x=100, y=100))
        assert wait_for(lambda: (win.tick(), len(win.pages()) > 1)[1], timeout=15, pump=pump), (
            "no popup (an engine without the display built-ins?): %s" % (sim.engine_windows(),)
        )
        pop = next(p for p in win.pages() if p.attach_window is not None)
        assert pop.title().lower() == "!energystartstop"
        assert wait_for(lambda: (win.tick(), pop.sim.page_active())[1], pump=pump)
        assert wait_for(lambda: (win.tick(), bool(pop.scene.sim_states()))[1], pump=pump)
        time = sim.clock_state().time
        assert wait_for(
            lambda: sim.clock_state().running and sim.clock_state().time > time + 0.5, pump=pump
        ), ("the popup paused the simulation", time, sim.clock_state(), sim.session.clock_request(""))
        w = pop.attach_window
        # a popup is a window of its own over the page (or a tab with 'a tab per window')
        if win.tabs.indexOf(pop) >= 0:
            win.close_tab(win.tabs.indexOf(pop))
        else:
            win.close_view(pop)
        assert wait_for(
            lambda: (win.tick(), not any(x["window"] == w for x in sim.engine_windows()[1]))[1], pump=pump
        )
        # AN 624: PageDisplay("ProcessLine")
        sim.input(InputEvent("down", an=624, ans=(624,), x=10, y=10))
        sim.input(InputEvent("up", an=624, ans=(624,), x=10, y=10))
        assert wait_for(lambda: (win.tick(), sp.title() == "ProcessLine")[1], pump=pump), sim.engine_windows()
        assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
        assert not sim.status().demo
        assert wait_for(lambda: (win.tick(), bool(sp.scene.sim_states()))[1], pump=pump)
    finally:
        win.close()
        win.deleteLater()
        pump()
        gc.collect()


def test_trends_have_history_when_the_page_opens(env):
    """Trend history (on by default): the simulation this tab drives is run ahead by the
    page's trend span, so the Supply TS01 trend shows the values the code produced, not the
    few samples since the start."""
    install, hub = env
    sim, _page = _engine_sim(install, hub, page_path(install, "ExampleSA", "Supply_L3"))
    try:
        _wait_live(sim)
        st = _States(sim)

        def varied():
            st.pump()
            for o in st.objects.values():
                tr = getattr(o, "trend", None)
                if not isinstance(tr, dict):
                    continue
                for pen in tr.get("pens") or ():
                    vals = pen.get("values") if isinstance(pen, dict) else pen
                    known = [v for v in (vals or []) if isinstance(v, (int, float))]
                    if len(known) > 50 and len({round(v, 2) for v in known}) > 5:
                        return True
            return False

        assert wait_for(varied, timeout=40.0)
    finally:
        sim.stop()


def test_the_simulator_asks_before_it_loads_a_dll(env):
    """Settings > Simulate > DLLs set to Ask, on the real engine: a DLLOpen in the simulation
    raises the question in the Simulate window; Deny fails the open, Always Allow loads the DLL
    (a real call into it answers), the allowed list of the settings gets its full path and
    another function of it is not asked about; Off again unloads it."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    import threading
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    from ctgedit.ui.simulate import options as sim_options
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.outside_access import PermissionDialog
    from ctgedit.ui.simulate.window import SimulateWindow

    install, hub = env
    path = page_path(install, "Example", "Mixer")
    sim_options.put("dll_access", "ask")
    win = SimulateWindow()
    pump = QtWidgets.QApplication.processEvents
    opened = 'DLLOpen("kernel32.dll", "lstrlenA", "JC")'
    try:
        sim = EngineSimulation(hub=hub, location=install["loc"])
        sim.run_startup = False
        win.open_page(path, sim=sim)
        win.show()
        assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
        s = sim._session
        if not (s.hello.get("capabilities") or {}).get("outside"):
            pytest.skip("this ctsim.exe is older than the DLL and SQL options")

        def call(expr):
            out = {}
            t = threading.Thread(
                target=lambda: out.update(s.request("call", {"expr": expr, "timeoutMs": 30000}, 40)),
                daemon=True,
            )
            t.start()
            return t, out

        def question():
            assert wait_for(
                lambda: (win.tick(), any(isinstance(w, PermissionDialog) for w in win.dialogs.widgets()))[1],
                pump=pump,
            )
            return next(w for w in win.dialogs.widgets() if isinstance(w, PermissionDialog))

        def ended(t):
            return wait_for(lambda: (win.tick(), not t.is_alive())[1], pump=pump)

        t, out = call(opened)
        d = question()
        assert d.windowTitle() == "Load a DLL?" and d.detail == "lstrlenA"
        assert d.target.lower().endswith("kernel32.dll") and os.path.isabs(d.target)
        d.deny_button.click()
        assert ended(t) and out["result"] == "-1"
        assert sim_options.get_list("dll_allowed") == []
        t, out = call(opened)
        question().always_button.click()
        assert ended(t)
        handle = int(out["result"])
        assert handle >= 0
        allowed = sim_options.get_list("dll_allowed")
        assert len(allowed) == 1 and allowed[0].lower().endswith("kernel32.dll")
        assert s.request("call", {"expr": 'DLLCall(%d, "^"hello^"")' % handle}, 20)["result"] == "5"
        # another function of the allowed DLL is not asked about
        tick = s.request("call", {"expr": 'DLLOpen("kernel32.dll", "GetTickCount", "J")'}, 20)
        assert int(tick["result"]) >= 0 and win.dialogs.open_count() == 0
        # Off in the settings, applied to the open simulation: nothing stays loaded
        sim_options.put("dll_access", "off")
        win.apply_outside_settings()
        assert wait_for(
            lambda: s.request("call", {"expr": 'DLLCall(%d, "^"hello^"")' % handle}, 20)["result"] == "",
            timeout=10,
        )
    finally:
        win.close()
        win.deleteLater()
        pump()
        gc.collect()
