"""Engine windows in the Simulate window: popups and navigation."""

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
    # (never a real engine: no built ctsim.exe is found, no engine path is set)
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


def _drain(sim):
    while sim.poll() is not None:
        pass


def _clock_modes(srv):
    return [
        args.get("mode")
        for _conn, cmd, args in list(srv.requests)
        if cmd == "ctsim/clock" and args.get("mode")
    ]


def test_a_popup_tab_leaves_the_shared_clock_alone(servers, hub, page_file):
    """A tab attached to a window the page opened (and any later tab of the same simulation)
    joins a running simulation: it neither pauses it nor resets its speed."""
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    first = _sim(hub)
    popup = None
    try:
        first.start(page_file, _rect_page())
        assert wait_for(lambda: first.page_active())
        first.clock("speed", 4.0)
        first.clock("run")
        assert wait_for(lambda: srv.running and srv.speed == 4.0)
        n = len(_clock_modes(srv))
        popup = _sim(hub, attach_window=0, page_name="Mixer")
        popup.start(page_file, _rect_page())
        assert wait_for(lambda: popup.page_active())
        assert wait_for(lambda: popup.clock_state().running)
        assert srv.running and srv.speed == 4.0, _clock_modes(srv)[n:]
        assert not [m for m in _clock_modes(srv)[n:] if m in ("pause", "speed", "run")]
    finally:
        if popup is not None:
            popup.stop()
        first.stop()
        srv.stop()


def test_a_navigation_holds_the_page_instead_of_the_demo(servers, hub, page_file, tmp_path):
    """The page's Cicode shows another page in the tab's window (its first scan comes before
    the windowsChanged that names its drawing file): until the tab follows it nothing new is
    drawn, never the demo values; then the tab shows the new page with its states."""
    proj = os.path.dirname(page_file)
    other = os.path.join(proj, "Overview.ctg")
    with open(other, "wb"):
        pass
    srv = F.FakeServer(proj, servers, pages=True).start()
    srv.paths["overview"] = other
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        _drain(sim)
        w = sim._window
        on = sim._event_listener(sim._gen)
        on(
            "ctsim/windowsChanged",
            {"windows": [{"window": w, "page": "Mixer", "parent": -1, "path": page_file, "hasModel": True}]},
        )
        srv.windows[w] = "Overview"  # PageDisplay("Overview") in the window
        on(
            "ctsim/pageChanged",
            {"window": w, "page": "Overview", "objects": [{"an": 11, "state": {"move": [50, 50]}}]},
        )
        for _k in range(10):
            assert sim.poll() is None  # held: not the demo, not Overview's
        assert not sim.status().demo
        # the windows (asked, or the windowsChanged that follows) name the new page's file
        assert wait_for(lambda: sim._nav is not None)
        nav = sim.take_navigation()
        assert nav["page"] == "Overview" and nav["path"] == other
        assert sim.poll() is None
        page = _rect_page()
        page.name = "Overview"
        sim.navigated("Overview", other, page, None)
        assert wait_for(lambda: sim.page_active())
        st = sim.poll()
        assert st is not None and st.replace and st.page == "Overview" and 11 in st.objects
    finally:
        sim.stop()
        srv.stop()


def test_a_late_state_of_another_page_is_checked_with_the_windows(servers, hub, page_file):
    """A pageChanged that names another page but no windowsChanged follows (it was sent
    before this tab's page opened in the window): the tab asks the windows and goes on with
    its page."""
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        _drain(sim)
        w = sim._window
        on = sim._event_listener(sim._gen)
        on(
            "ctsim/windowsChanged",
            {"windows": [{"window": w, "page": "Mixer", "parent": -1, "path": page_file, "hasModel": True}]},
        )
        on(
            "ctsim/pageChanged",
            {"window": w, "page": "Dynamic_1", "objects": [{"an": 11, "state": {"move": [50, 50]}}]},
        )
        assert wait_for(lambda: sim.page_active(), timeout=5)
        assert not sim.status().demo
    finally:
        sim.stop()
        srv.stop()


def test_the_last_pointer_position_reaches_the_simulation():
    """Pointer moves go at most every MOVE_EVERY s; the position a throttled move left unsent
    goes when the interval is over (DspGetMouse, the mouse-over looks follow the pointer to
    where it stopped)."""
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    from ctgedit.render.scene import PageScene
    from ctgedit.ui.simulate import canvas as CV

    scene = PageScene()
    scene.set_page(_rect_page())
    view = CV.SimCanvas(scene)
    view.resize(500, 400)
    got = []
    view.inputEvent.connect(got.append)
    try:
        for x in (10, 20, 30, 40):
            view._pointer(QtCore.QPoint(x, 10))
        assert [e.kind for e in got] == ["move"]  # the first; the others throttled
        end = time.monotonic() + 2.0
        while time.monotonic() < end and len(got) < 2:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.005)
        assert len(got) == 2
        last = view.mapToScene(QtCore.QPoint(40, 10))
        assert (got[-1].x, got[-1].y) == (float(int(last.x() // 1)), float(int(last.y() // 1)))
        view._pointer(None)  # it left the page: at once
        assert (got[-1].x, got[-1].y) == (-1.0, -1.0)
    finally:
        view.deleteLater()
        QtWidgets.QApplication.processEvents()


def _hit_page():
    """A button under a static frame, a group whose (invisible) extent lies over it, a
    transparent target rect with a touch record that draws nothing, a touch rect under a
    hidden cover."""
    from ctgedit.model.document import Geometry, GraphicObject, ObjectKind as K, Page

    def obj(kind, an, x, y, w, h, tc, **style):
        o = GraphicObject(kind=kind, an=an, geometry=Geometry(x=x, y=y, extent_x=w, extent_y=h))
        for k, v in style.items():
            setattr(o.style, k, v)
        o.raw["_type_code"] = tc
        return o

    p = Page(name="hits")
    p.appearance.width, p.appearance.height = 400, 300
    p.appearance.background_colour = 0xFFFFFF
    btn = obj(K.BUTTON, 7, 20, 20, 80, 40, 0x1001)
    frame = obj(K.RECTANGLE, 8, 10, 10, 100, 60, 0x0F, filled=True, fill_colour=0xC0C0C0)
    group = obj(K.GROUP, 9, 0, 0, 390, 290, 0x08)
    inner = obj(K.RECTANGLE, 10, 300, 200, 20, 20, 0x0F, filled=True, fill_colour=0x00FF00)
    target = obj(K.RECTANGLE, 11, 200, 20, 60, 30, 0x0F, filled=False, line_style=5)
    target.raw["_no_outline"] = True
    touch = obj(K.UNKNOWN, 11, 0, 0, 0, 0, 0x1010)
    under = obj(K.RECTANGLE, 12, 200, 120, 60, 30, 0x0F, filled=True, fill_colour=0x0000FF)
    utouch = obj(K.UNKNOWN, 12, 0, 0, 0, 0, 0x1010)
    p.objects = [btn, frame, group, inner, target, touch, under, utouch]
    group.members, inner.parent = [3], 2
    target.members, touch.parent = [5], 4
    under.members, utouch.parent = [7], 6
    return p


def test_a_click_reaches_the_object_that_takes_input():
    """A click reaches the object that takes input."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    from ctgedit.render.scene import PageScene
    from ctgedit.sim.state import ObjectState
    from ctgedit.ui.simulate import canvas as CV

    page = _hit_page()
    scene = PageScene()
    scene.set_page(page)
    scene.set_runtime_look(True)
    view = CV.SimCanvas(scene)
    view.resize(400, 300)
    view.setSceneRect(0, 0, 400, 300)
    view.show()
    QtWidgets.QApplication.processEvents()
    inputs = {7, 11, 12}

    def picked(x, y):
        chain = view.ans_at(view.mapFromScene(QtCore.QPointF(x, y)))
        return next((a for a in chain if a in inputs), None), chain

    try:
        got, chain = picked(60, 40)  # the frame and the group over the button
        assert got == 7 and chain[0] == 8, chain
        assert picked(230, 35)[0] == 11  # the target draws nothing
        assert picked(230, 135)[0] == 12
        assert picked(310, 210) == (None, (10, 9))  # the group's member, then the group
        scene.apply_states({12: ObjectState(12, shown=False)})
        assert picked(230, 135)[0] is None  # hidden: no input
        scene.apply_states({12: ObjectState(12, move=(0.0, 100.0))}, replace=True)
        assert picked(230, 235)[0] == 12  # moved: its pick area moved too
        assert picked(230, 135)[0] is None
    finally:
        view.deleteLater()
        QtWidgets.QApplication.processEvents()


@pytest.mark.parametrize("name, at_least", [("FxButtons", 15), ("FxInputs", 9)])
def test_every_input_object_of_an_sa_page_can_be_clicked(fx_page, name, at_least):
    """Every input object of an SA page can be clicked."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    from ctgedit.editing import EditableDocument
    from ctgedit.render.scene import PageScene
    from ctgedit.ui.simulate import canvas as CV
    from ctgedit.ui.simulate.window import runtime_page

    path = fx_page(name)
    model = EditableDocument.open(path).export_page_model()
    t = model["tables"]
    inputs = {
        int(r["AN"])
        for name in ("pgtouch", "pgbutton", "pgkey")
        for r in t.get(name) or ()
        if str(r.get("AN") or "").strip().isdigit() and int(r["AN"]) > 0
    }
    rects = {o["an"]: o.get("rect") for o in model["objects"]}
    scene = PageScene()
    scene.set_page(runtime_page(path))
    scene.set_runtime_look(True)
    view = CV.SimCanvas(scene)
    w, h = model["size"]
    view.resize(w + 40, h + 40)
    view.setSceneRect(0, 0, w, h)
    view.show()
    QtWidgets.QApplication.processEvents()
    missed = []
    try:
        for an in sorted(inputs):
            l, t_, r, b = rects[an]
            hit = False
            for fx in (0.2, 0.5, 0.8):
                for fy in (0.2, 0.5, 0.8):
                    p = QtCore.QPointF(l + (r - l) * fx + 0.5, t_ + (b - t_) * fy + 0.5)
                    chain = view.ans_at(view.mapFromScene(p))
                    if next((a for a in chain if a in inputs), None) == an:
                        hit = True
            if not hit:
                missed.append(an)
        assert len(inputs) >= at_least and not missed, missed
    finally:
        view.deleteLater()
        QtWidgets.QApplication.processEvents()


def test_reconnect_closes_the_page_it_opened(servers, hub, page_file):
    """Reconnect lets go of the page the tab opened (closePage) before it opens it again: a
    server that goes on running never collects one window more per Reconnect."""
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    sim = _sim(hub)
    keep = None
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        keep = hub.acquire(proj)  # another user keeps the server's session
        for _k in range(3):
            sim.reconnect()
            assert wait_for(lambda: sim.page_active())
        assert srv.windows == {0: "Mixer"}, srv.windows
    finally:
        sim.stop()
        if keep is not None:
            hub.release(keep)
        srv.stop()


def test_reload_page_holds_the_drawing_until_the_new_states(servers, hub, page_file):
    """Reload Page opens the page again: until its new states come nothing new is drawn, then
    the whole set replaces the old."""
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        _drain(sim)
        orig = srv._r_pageState

        def slow(conn, msg, args):
            import threading

            threading.Timer(0.3, orig, (conn, msg, args)).start()

        srv._r_pageState = slow
        sim.reload({"format": "ctsim-page/0", "page": "Mixer"}, _rect_page())
        seen = []
        assert wait_for(lambda: seen.append(sim.poll()) or sim.page_active(), timeout=5)
        assert not any(st is not None for st in seen[:-1]), seen[:3]
        st = sim.poll() if seen[-1] is None else seen[-1]
        assert st is not None and st.replace and 11 in st.objects
    finally:
        sim.stop()
        srv.stop()


def test_page_changes_do_not_stop_the_clock_bar(servers, hub, page_file):
    """pageChanged carries the page's scan count ('scan'), not the clock: the clock bar's time
    keeps running between the clock's own answers while page states come."""
    import time

    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        sim.clock("run")
        assert wait_for(lambda: sim.clock_state().running)
        t0 = sim.clock_state().time
        on = sim._event_listener(sim._gen)
        end = time.monotonic() + 0.6
        k = 0
        while time.monotonic() < end:
            k += 1
            on(
                "ctsim/pageChanged",
                {
                    "window": sim._window,
                    "page": "Mixer",
                    "serial": k,
                    "scan": 7,
                    "flashOn": True,
                    "objects": [{"an": 11, "state": {"move": [k, 0]}}],
                },
            )
            time.sleep(0.02)
        st = sim.clock_state()
        assert st.time > t0 + 0.4, (t0, st)
        assert st.scan != 7 or st.time >= 7 * 0.25
    finally:
        sim.stop()
        srv.stop()


def test_new_tab_per_page_opens_the_page_changed_to_in_its_own_tab(servers, hub, page_file, fx_page):
    """Simulate Settings > Open each page in a new tab: the page the window changed to gets a
    tab on that window; the old tab's page opens again in a free window and runs on. The
    page changed to is FxMain's FxThumb, copied as Overview."""
    import shutil

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate.window import SimulateWindow

    proj = os.path.dirname(page_file)
    other = os.path.join(proj, "Overview.ctg")
    shutil.copyfile(fx_page("FxThumb"), other)
    srv = F.FakeServer(proj, servers, pages=True).start()
    srv.paths["overview"] = other
    options.put("new_tab_per_page", True)
    win = SimulateWindow()
    sim = _sim(hub)
    try:
        old = win.open_page(page_file, page=_rect_page(), sim=sim)
        assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=app.processEvents)
        w = sim._window
        on = sim._event_listener(sim._gen)
        on(
            "ctsim/windowsChanged",
            {"windows": [{"window": w, "page": "Mixer", "parent": -1, "path": page_file, "hasModel": True}]},
        )
        srv.windows[w] = "Overview"
        on("ctsim/pageChanged", {"window": w, "page": "Overview", "objects": []})
        assert wait_for(lambda: (win.tick(), win.tabs.count())[1] == 2, pump=app.processEvents)
        new = win.tabs.widget(1)
        assert new.attach_window == w and new.title() == "Overview"
        assert win.current() is new and old.title() == "Mixer"
        # the old page runs on: opened again in a free window of its own
        assert wait_for(
            lambda: (win.tick(), sim.page_active() and sim._window not in (None, w))[1],
            pump=app.processEvents,
        )
        assert srv.windows.get(sim._window) == "Mixer" and srv.windows.get(w) == "Overview"
        # off: the tab follows the page itself
        options.put("new_tab_per_page", False)
        assert not options.get("new_tab_per_page")
    finally:
        options.put("new_tab_per_page", False)
        win.close()
        sim.stop()
        srv.stop()


def test_simulate_settings_dialog_round_trip():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate.settings_dialog import SimSettingsDialog

    d = SimSettingsDialog()
    assert not d.new_tab.isChecked() and d.autorun.isChecked() and d.errors.isChecked()
    d.new_tab.setChecked(True)
    d.autorun.setChecked(False)
    d.log_lines.setValue(20000)
    d.user.setCurrentIndex(d.user.findData("none"))
    d.store()
    assert options.get("new_tab_per_page") and not options.get("autorun")
    assert options.get("log_lines") == 20000 and options.get("user") == "none"
    d2 = SimSettingsDialog()
    assert d2.new_tab.isChecked() and d2.log_lines.value() == 20000
    d2.defaults()
    d2.store()
    assert not options.get("new_tab_per_page") and options.get("autorun")
    assert options.get("user") == "full"


def test_changes_sent_while_the_page_opens_are_kept(servers, hub, page_file):
    """The page's first scans change objects right after the open is answered, before the tab
    knows its window: those changes are drawn, not dropped."""
    proj = os.path.dirname(page_file)
    srv = F.FakeServer(proj, servers, pages=True).start()
    srv.page_objects = {11: {"move": [0, 0]}}
    srv.open_then_change = [{"an": 11, "state": {"move": [40, 7]}}]
    for _k in range(5):
        sim = _sim(hub)
        try:
            sim.start(page_file, _rect_page())
            assert wait_for(lambda: sim.page_active())
            seen = {}
            assert wait_for(
                lambda: (
                    (
                        seen.update((sim.poll() or type("S", (), {"objects": {}})).objects),
                        getattr(seen.get(11), "move", None),
                    )[1]
                    is not None
                )
            )
            assert tuple(seen[11].move) == (40, 7)
        finally:
            sim.stop()
    srv.stop()
