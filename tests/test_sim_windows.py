"""Windows the page's Cicode opens."""

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

PINNED = 0x80003 | 0x400  # WinNewPinAt (maintain size)
POPUP = 1 + 8 + 32 + 256 + 512  # an AssPopUp window


@pytest.fixture
def servers(tmp_path, monkeypatch):
    folder = str(tmp_path / "servers")
    monkeypatch.setenv(C.SERVERS_ENV, folder)
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)
    return folder


@pytest.fixture
def project(tmp_path, fx_page):
    small = fx_page("FxThumb")
    proj = tmp_path / "User" / "Proj"
    proj.mkdir(parents=True)
    main = proj / "FxTab.ctg"
    main.write_bytes(b"")
    pages = {}
    for name in ("PaneA", "PaneB", "!Pop"):
        p = proj / (name + ".ctg")
        shutil.copyfile(small, p)
        pages[name] = str(p)
    return str(main), pages


@pytest.fixture
def hub():
    h = C.Hub(linger=0)
    yield h
    h.shutdown()


def _open(app, servers, hub, main):
    from ctgedit.ui.simulate.window import SimulateWindow

    srv = F.FakeServer(os.path.dirname(main), servers, pages=True).start()
    win = SimulateWindow()
    win.resize(1200, 800)
    win.show()
    sim = _sim(hub)
    sp = win.open_page(main, page=_rect_page(), sim=sim)
    pump = app.processEvents
    assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
    return srv, win, sim, sp, pump


def _close(win, srv, app):
    win.close()
    win.deleteLater()
    app.processEvents()
    srv.stop()
    gc.collect()


def _inputs(srv):
    return [args for _c, cmd, args in list(srv.requests) if cmd == "ctsim/input"]


def _closed(srv):
    return [args.get("window") for _c, cmd, args in list(srv.requests) if cmd == "ctsim/closePage"]


def test_placement_rules():
    from ctgedit.ui.simulate import winhost as H

    assert H.placement({"mode": 0x80403, "parent": 0}) == H.EMBEDDED
    assert H.placement({"mode": 809, "parent": 0}) == H.POPUP
    assert H.placement({"mode": 0, "parent": -1}) == H.POPUP
    # a pinned bit without a parent is a window of its own
    assert H.placement({"mode": 0x80003, "parent": -1}) == H.POPUP
    assert H.placement({"mode": 0x80403, "parent": 0}, tab_per_window=True) == H.TAB
    # the page fits its window unless mode 1024 keeps its size; 4096 frees the aspect
    assert H.content_scale({"mode": 0, "w": 200, "h": 50}, 100, 100) == (0.5, 0.5)
    assert H.content_scale({"mode": 0x1000, "w": 200, "h": 50}, 100, 100) == (2.0, 0.5)
    assert H.content_scale({"mode": 0x400, "w": 200, "h": 50}, 100, 100) == (1.0, 1.0)
    assert H.view_size({"w": 0, "h": 0}, 30, 40) == (30.0, 40.0)


def test_pinned_children_are_drawn_inside_their_parent(servers, hub, project):
    """WinNewPinAt panes are drawn inside the tab at their place on the parent's page, follow
    its zoom, keep one view per engine window through repeated window lists, and send their
    input to their own window."""
    app = _app()
    main, pages = project
    srv, win, sim, sp, pump = _open(app, servers, hub, main)
    try:
        mine = sim.window
        srv.open_window(1, "PaneA", pages["PaneA"], parent=mine, mode=PINNED, x=40, y=30, w=200, h=120)
        srv.open_window(2, "PaneB", pages["PaneB"], parent=mine, mode=PINNED, x=260, y=30, w=100, h=80)
        assert wait_for(lambda: (win.tick(), len(win.embedded()) == 2)[1], timeout=15, pump=pump)
        assert win.tabs.count() == 1 and not win.popups()
        a = next(v for v in win.embedded() if v.attach_window == 1)
        b = next(v for v in win.embedded() if v.attach_window == 2)
        assert a.parent() is sp.canvas.viewport() and a.host == "embedded"
        assert wait_for(lambda: (win.tick(), a.sim.page_active() and b.sim.page_active())[1], pump=pump)
        win.tick()

        def placed(v, x, y, w, h):
            z = sp.canvas.transform().m11()
            tl = sp.canvas.mapFromScene(x, y)
            g = v.geometry()
            return (
                abs(g.x() - tl.x()) <= 1
                and abs(g.y() - tl.y()) <= 1
                and abs(g.width() - round(w * z)) <= 1
                and abs(g.height() - round(h * z)) <= 1
            )

        assert placed(a, 40, 30, 200, 120), (a.geometry(), sp.canvas.transform())
        assert placed(b, 260, 30, 100, 80)
        # the parent's zoom moves and sizes the panes with it
        sp.canvas.set_zoom(sp.canvas.zoom() * 1.5)
        pump()
        assert placed(a, 40, 30, 200, 120) and placed(b, 260, 30, 100, 80)
        assert abs(a.canvas.transform().m11() - sp.canvas.transform().m11()) < 1e-6
        # more window lists (the engine sends one per command): still one view per window
        for _k in range(3):
            srv.broadcast("ctsim/windowsChanged", srv.windows_body())
            assert wait_for(lambda: (win.tick(), True)[1], timeout=15, pump=pump)
        assert len(win.embedded()) == 2 and win.tabs.count() == 1
        # a click in a pane goes to the pane's window
        from ctgedit.ui.simulate.sources import InputEvent

        n = len(_inputs(srv))
        a.canvas.inputEvent.emit(InputEvent(kind="down", an=3, ans=(3,), x=5, y=5))
        assert wait_for(lambda: len(_inputs(srv)) > n, pump=pump)
        assert _inputs(srv)[-1].get("window") == 1
        assert win.panel_view() is a
        # the parent frees a pane: its view goes, nothing is sent for it
        srv.free_window(2)
        assert wait_for(lambda: (win.tick(), len(win.embedded()) == 1)[1], timeout=15, pump=pump)
        assert win.embedded()[0] is a and 2 not in _closed(srv)
    finally:
        _close(win, srv, app)


def test_a_popup_floats_over_the_window_and_closes_its_engine_window(servers, hub, project):
    app = _app()
    main, pages = project
    srv, win, sim, sp, pump = _open(app, servers, hub, main)
    try:
        mine = sim.window
        srv.open_window(
            3, "!Pop", pages["!Pop"], parent=mine, mode=POPUP, x=40, y=40, w=60, h=100, title="Fx Pop"
        )
        assert wait_for(lambda: (win.tick(), len(win.popups()) == 1)[1], timeout=15, pump=pump)
        pop = win.popups()[0]
        assert win.tabs.count() == 1 and pop.attach_window == 3
        assert pop.frame is not None and pop.frame.isVisible() and pop.frame.isWindow()
        assert pop.frame.title.text() == "Fx Pop"
        # the window's top-left corner (its caption included, WinNewAt's x, y) sits on the
        # page point (x, y) of its parent window, the page below the caption
        at = sp.canvas.viewport().mapToGlobal(sp.canvas.mapFromScene(40, 40))
        got = pop.frame.geometry().topLeft()
        assert abs(got.x() - at.x()) <= 2 and abs(got.y() - at.y()) <= 2, (got, at)
        assert pop.mapToGlobal(pop.rect().topLeft()).y() >= at.y() + pop.frame.CAPTION
        # the tab's zoom moves it with the page (no tick needed)
        sp.canvas.set_zoom(sp.canvas.zoom() * 0.8)
        pump()
        at = sp.canvas.viewport().mapToGlobal(sp.canvas.mapFromScene(40, 40))
        got = pop.frame.geometry().topLeft()
        assert abs(got.x() - at.x()) <= 2 and abs(got.y() - at.y()) <= 2, (got, at)
        # a child window of the popup (AssPopUp from a button on it) is placed from the
        # popup's page, not from the tab's
        srv.open_window(
            4, "PaneA", pages["PaneA"], parent=3, mode=POPUP, x=10, y=5, w=60, h=60, title="Child"
        )
        assert wait_for(lambda: (win.tick(), len(win.popups()) == 2)[1], timeout=15, pump=pump)
        child = [v for v in win.popups() if v.attach_window == 4][0]
        pump()
        win._layout_views()
        at = pop.canvas.viewport().mapToGlobal(pop.canvas.mapFromScene(10, 5))
        got = child.frame.geometry().topLeft()
        assert abs(got.x() - at.x()) <= 2 and abs(got.y() - at.y()) <= 2, (got, at)
        srv.free_window(4)
        assert wait_for(lambda: (win.tick(), len(win.popups()) == 1)[1], timeout=15, pump=pump)
        # another tab hides it, its own tab shows it again
        win.tabs.addTab(type(sp.canvas)(sp.scene), "other")
        win.tabs.setCurrentIndex(1)
        pump()
        assert not pop.frame.isVisible()
        win.tabs.setCurrentIndex(0)
        pump()
        assert pop.frame.isVisible()
        win.tabs.removeTab(1)
        # moved by the user, a new position from the simulation leaves it where it is
        pop.frame.move(pop.frame.pos() + pop.frame.rect().bottomRight() * 0 + type(at)(30, 20))
        pop.frame.moved_by_user = True
        here = pop.frame.pos()
        srv.open_window(
            3, "!Pop", pages["!Pop"], parent=mine, mode=POPUP, x=300, y=40, w=60, h=100, title="Fx Pop"
        )
        assert wait_for(lambda: (win.tick(), pop.engine_info.get("x") == 300)[1], pump=pump)
        assert pop.frame.pos() == here
        # its Close button closes the window in the simulator; the engine's list from before
        # its closePage arrived does not open it again
        stale = sim.engine_windows()
        assert 3 in [w["window"] for w in stale[1]]
        sim.engine_windows = lambda: stale
        try:
            pop.frame.close_button.click()
            win.tick()
            pump()
            assert not win.popups()
        finally:
            del sim.engine_windows
        assert wait_for(lambda: 3 in _closed(srv), pump=pump)
        assert wait_for(lambda: (win.tick(), not win._closed_here)[1], pump=pump)
        assert not win.popups() and win.tabs.count() == 1
        # the number opened again is a new window: shown
        srv.open_window(
            3, "!Pop", pages["!Pop"], parent=mine, mode=POPUP, x=40, y=40, w=60, h=100, title="Fx Pop"
        )
        assert wait_for(lambda: (win.tick(), len(win.popups()) == 1)[1], timeout=15, pump=pump)
    finally:
        _close(win, srv, app)


def test_a_window_opened_from_a_popup_still_connecting_is_shown(servers, hub, project):
    """A popup's own connection asks for the windows once it is attached: a window its page
    opened while it was still connecting (an AssPopUp from a button on it at once, the
    windowsChanged gone before the connection) is shown, not only after the next window
    change."""
    app = _app()
    main, pages = project
    srv, win, sim, sp, pump = _open(app, servers, hub, main)
    try:
        mine = sim.window
        # both at once: the popup's view and connection come after the second windowsChanged
        srv.open_window(
            3, "!Pop", pages["!Pop"], parent=mine, mode=POPUP, x=40, y=40, w=60, h=100, title="Fx Pop"
        )
        srv.open_window(
            4, "PaneA", pages["PaneA"], parent=3, mode=POPUP, x=10, y=5, w=60, h=60, title="Child"
        )
        assert wait_for(lambda: (win.tick(), len(win.popups()) == 2)[1], timeout=15, pump=pump)
        child = [v for v in win.popups() if v.attach_window == 4][0]
        assert child.parent_view is not None and child.parent_view.attach_window == 3
    finally:
        _close(win, srv, app)


def test_closing_a_tab_closes_the_windows_shown_in_it(servers, hub, project):
    app = _app()
    main, pages = project
    srv, win, sim, sp, pump = _open(app, servers, hub, main)
    try:
        mine = sim.window
        srv.open_window(1, "PaneA", pages["PaneA"], parent=mine, mode=PINNED, x=0, y=0, w=200, h=100)
        srv.open_window(2, "!Pop", pages["!Pop"], parent=mine, mode=POPUP, x=10, y=10)
        assert wait_for(lambda: (win.tick(), len(win.views()) == 2)[1], timeout=15, pump=pump)
        win.close_tab(0)
        pump()
        assert not win.views() and win.tabs.count() == 0
    finally:
        _close(win, srv, app)


def test_tab_per_window_setting_keeps_the_old_tabs(servers, hub, project):
    from ctgedit.ui.simulate import options

    app = _app()
    main, pages = project
    options.put("tab_per_window", True)
    srv, win, sim, sp, pump = _open(app, servers, hub, main)
    try:
        mine = sim.window
        srv.open_window(1, "PaneA", pages["PaneA"], parent=mine, mode=PINNED, x=0, y=0)
        srv.open_window(2, "!Pop", pages["!Pop"], parent=mine, mode=POPUP, x=10, y=10)
        assert wait_for(lambda: (win.tick(), win.tabs.count() == 3)[1], timeout=15, pump=pump)
        assert not win.views()
        for _k in range(3):
            srv.broadcast("ctsim/windowsChanged", srv.windows_body())
            assert wait_for(lambda: (win.tick(), True)[1], timeout=15, pump=pump)
        assert win.tabs.count() == 3
    finally:
        options.put("tab_per_window", False)
        _close(win, srv, app)


def test_a_window_of_its_own_gets_a_popup_once_every_tab_knows_its_window(servers, hub, project):
    """A WinNew window (no parent) nobody shows is a popup of the tab; the tab's own window
    and windows other tabs show are not."""
    app = _app()
    main, pages = project
    srv, win, sim, sp, pump = _open(app, servers, hub, main)
    try:
        srv.open_window(4, "!Pop", pages["!Pop"], mode=0, x=0, y=0)
        assert wait_for(lambda: (win.tick(), len(win.popups()) == 1)[1], timeout=15, pump=pump)
        assert win.popups()[0].attach_window == 4
        for _k in range(3):
            srv.broadcast("ctsim/windowsChanged", srv.windows_body())
            assert wait_for(lambda: (win.tick(), True)[1], timeout=15, pump=pump)
        assert len(win.popups()) == 1 and not win.embedded() and win.tabs.count() == 1
    finally:
        _close(win, srv, app)


def test_another_viewers_window_is_never_shown(servers, hub, project):
    """A window of its own that was open before the tab's window (another viewer's, on a
    shared simulator) gets no popup; one opened later does."""
    app = _app()
    main, pages = project
    from ctgedit.ui.simulate.window import SimulateWindow

    srv = F.FakeServer(os.path.dirname(main), servers, pages=True).start()
    srv.open_window(5, "!Pop", pages["!Pop"], mode=0, x=0, y=0)
    win = SimulateWindow()
    win.resize(1200, 800)
    win.show()
    sim = _sim(hub)
    win.open_page(main, page=_rect_page(), sim=sim)
    pump = app.processEvents
    try:
        assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
        for _k in range(3):
            srv.broadcast("ctsim/windowsChanged", srv.windows_body())
            assert wait_for(lambda: (win.tick(), True)[1], timeout=15, pump=pump)
        assert not win.views()
        srv.open_window(6, "!Pop", pages["!Pop"], mode=0, x=0, y=0)
        assert wait_for(lambda: (win.tick(), len(win.popups()) == 1)[1], timeout=15, pump=pump)
        assert win.popups()[0].attach_window == 6
    finally:
        _close(win, srv, app)


def test_views_close_when_the_simulator_stops(servers, hub, project):
    app = _app()
    main, pages = project
    srv, win, sim, sp, pump = _open(app, servers, hub, main)
    try:
        mine = sim.window
        srv.open_window(1, "PaneA", pages["PaneA"], parent=mine, mode=PINNED, x=0, y=0, w=200, h=100)
        srv.open_window(2, "!Pop", pages["!Pop"], parent=mine, mode=POPUP, x=10, y=10)
        assert wait_for(
            lambda: (
                win.tick(),
                len(win.views()) == 2 and all(v.sim.session is not None for v in win.views()),
            )[1],
            timeout=15,
            pump=pump,
        )
        win.tick()
        srv.stop()
        assert wait_for(lambda: (win.tick(), not win.views())[1], timeout=15, pump=pump)
        assert win.tabs.count() == 1
    finally:
        _close(win, srv, app)


def test_settings_dialog_has_the_tab_per_window_option():
    _app()
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate.settings_dialog import SimSettingsDialog

    d = SimSettingsDialog()
    assert not d.tab_window.isChecked()
    d.tab_window.setChecked(True)
    d.store()
    assert options.get("tab_per_window")
    d2 = SimSettingsDialog()
    assert d2.tab_window.isChecked()
    d2.defaults()
    d2.store()
    assert not options.get("tab_per_window")


def _rendered(scene, src, w, h):
    from PySide6 import QtCore, QtGui

    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QtGui.QPainter(img)
    p.setRenderHints(
        QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing | QtGui.QPainter.SmoothPixmapTransform
    )
    scene.render(p, QtCore.QRectF(0, 0, w, h), src, QtCore.Qt.IgnoreAspectRatio)
    p.end()
    return img


def _same_share(img, ox, oy, ref, step=7):
    """The share of sampled pixels of ``ref`` that ``img`` has at (ox, oy) + the pixel."""
    same = total = 0
    for y in range(2, ref.height() - 2, step):
        for x in range(2, ref.width() - 2, step):
            a, b = img.pixel(ox + x, oy + y), ref.pixel(x, y)
            total += 1
            same += all(abs(((a >> s) & 0xFF) - ((b >> s) & 0xFF)) <= 24 for s in (0, 8, 16))
    return same / max(1, total)


def test_copy_image_includes_the_panes_and_popups(servers, hub, project):
    """Copy Image: the tab's whole page at 1:1 whatever the zoom, a pinned pane drawn at its
    place, a popup (with its caption) over it, the picture widened where the popup leaves
    the page; the same picture goes to the clipboard."""
    app = _app()
    main, pages = project
    srv, win, sim, sp, pump = _open(app, servers, hub, main)
    try:
        from PySide6 import QtCore, QtGui
        from ctgedit.ui.simulate import winhost as H

        mine = sim.window
        srv.open_window(1, "PaneA", pages["PaneA"], parent=mine, mode=PINNED, x=40, y=30, w=200, h=120)
        srv.open_window(
            3, "!Pop", pages["!Pop"], parent=mine, mode=POPUP, x=250, y=150, w=120, h=200, title="Fx Pop"
        )
        assert wait_for(
            lambda: (win.tick(), len(win.embedded()) == 1 and len(win.popups()) == 1)[1],
            timeout=15,
            pump=pump,
        )
        pane, pop = win.embedded()[0], win.popups()[0]
        assert wait_for(lambda: (win.tick(), pane.sim.page_active() and pop.sim.page_active())[1], pump=pump)
        win.tick()
        sp.canvas.set_zoom(0.6)  # the picture does not follow the zoom
        pump()
        img = win.tab_image()
        assert img is not None
        pr = sp.scene.page_rect()
        pw, ph = pop.scene.page_rect().width(), pop.scene.page_rect().height()
        sx, sy = H.content_scale(pop.engine_info, pw, ph)
        vw, vh = H.view_size(pop.engine_info, pw, ph)
        # the page at 1:1, widened downwards by the popup (its frame + caption leave the page)
        assert img.width() >= pr.width() and img.width() <= 400
        assert img.height() == 150 + int(vh) + H.PopupFrame.CAPTION + 2
        # the tab's own page where neither covers it (the rectangle's lower edge included)
        whole = _rendered(sp.scene, QtCore.QRectF(pr), int(pr.width()), int(pr.height()))
        assert _same_share(img, 0, 152, whole.copy(0, 152, 240, int(pr.height()) - 152)) > 0.95
        assert img.pixel(170, 155) == whole.pixel(170, 155) != whole.pixel(5, 295)
        # the pane: its page at (40, 30), 200 x 120 (it keeps its size)
        ref = _rendered(pane.scene, QtCore.QRectF(0, 0, 200, 120), 200, 120)
        assert _same_share(img, 40, 30, ref) > 0.95
        assert _same_share(whole, 40, 30, ref) < 0.8  # (not the tab's page there)
        # the popup: its page under the caption, fitted to its window
        ref = _rendered(pop.scene, QtCore.QRectF(0, 0, vw / sx, vh / sy), int(vw), int(vh))
        assert _same_share(img, 251, 150 + H.PopupFrame.CAPTION + 1, ref) > 0.9
        # Copy Image puts the same picture on the clipboard
        assert win.copy_image()
        got = QtGui.QGuiApplication.clipboard().image()
        assert (got.width(), got.height()) == (img.width(), img.height())
        assert win.clock.act_copy.isEnabled()
    finally:
        QtGui.QGuiApplication.clipboard().clear()
        _close(win, srv, app)


def test_a_popup_scales_on_its_own(servers, hub, project):
    # the wheel over a popup scales it (not the page under it); once scaled it keeps its
    # size when the page zooms, its corner grip scales its page to fit, and a double-click
    # on its caption follows the page's zoom again
    from PySide6 import QtCore

    app = _app()
    main, pages = project
    srv, win, sim, sp, pump = _open(app, servers, hub, main)
    try:
        srv.open_window(
            3, "!Pop", pages["!Pop"], parent=sim.window, mode=POPUP, x=40, y=40, w=60, h=100, title="Fx Pop"
        )
        assert wait_for(lambda: (win.tick(), len(win.popups()) == 1)[1], timeout=15, pump=pump)
        pop = win.popups()[0]
        pump()
        page_zoom = sp.canvas.zoom()
        z0 = pop.canvas.transform().m11()  # (the page's zoom x the window's own scale)
        content = z0 / page_zoom
        w0 = pop.width()
        pop.canvas.zoomRequested.emit(2.0)  # (two wheel steps in, over the popup)
        pump()
        assert abs(pop.canvas.transform().m11() - z0 * 1.15**2) < 1e-6
        assert pop.width() > w0
        assert abs(sp.canvas.zoom() - page_zoom) < 1e-9  # the page stayed
        # the page's zoom no longer scales it (it still moves with the page)
        z1 = pop.canvas.transform().m11()
        sp.canvas.set_zoom(page_zoom * 0.5)
        pump()
        assert abs(pop.canvas.transform().m11() - z1) < 1e-6
        # the corner grip: its page fits the new size, its shape kept
        win._popup_resized(pop, QtCore.QSize(pop.width() * 2, pop.height() * 3))
        pump()
        assert abs(pop.canvas.transform().m11() - z1 * 2) < 0.05
        # a double-click on the caption: the page's zoom again
        pop.frame.resetZoom.emit()
        pump()
        assert pop.own_zoom is None
        assert abs(pop.canvas.transform().m11() - sp.canvas.zoom() * content) < 1e-6
    finally:
        _close(win, srv, app)
