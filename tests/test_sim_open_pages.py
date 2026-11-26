"""Opening other pages in the Simulate window."""

import gc
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from test_sim_engine_source import (  # noqa: E402
    _app,
    _rect_page,
    _sim,
    hub,  # noqa: F401 (a fixture)
    page_file,  # noqa: E402,F401
    servers,  # noqa: F401 (a fixture)
    wait_for,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")


@pytest.fixture
def window(servers, hub, page_file, monkeypatch):
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate import window as W

    monkeypatch.setattr(W, "runtime_page", lambda path, parsed=None, doc=None: _rect_page())
    srv = F.FakeServer(
        os.path.dirname(page_file),
        servers,
        pages=True,
        navigate=True,
        page_objects={11: {"fillColour": 0x0000FF}},
    ).start()
    win = W.SimulateWindow()
    pump = QtWidgets.QApplication.processEvents
    sim = _sim(hub)
    sp = win.open_page(page_file, page=_rect_page(), sim=sim)
    win.show()
    assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
    other = os.path.join(os.path.dirname(page_file), "Genies.ctg")
    with open(other, "wb"):
        pass
    try:
        yield win, sp, srv, other, pump
    finally:
        win.close()
        win.deleteLater()
        pump()
        srv.stop()
        gc.collect()


def test_open_page_runs_in_the_same_simulation(window):
    win, sp, srv, other, pump = window
    sp2 = win.open_other_page(other)
    assert sp2 is not None and sp2 is not sp
    assert win.tabs.count() == 2 and win.current() is sp2
    assert wait_for(lambda: (win.tick(), sp2.sim.session is not None)[1], pump=pump)
    assert sp2.sim.session is sp.sim.session  # one server, one simulation
    assert wait_for(lambda: any(a.get("name") == "Genies" for a in getattr(srv, "opened", [])), pump=pump)
    # a page open here already is shown, not opened twice
    assert win.open_other_page(other) is sp2 and win.tabs.count() == 2


def test_a_reload_shows_reloading_until_the_new_states_come(window):
    win, sp, srv, other, pump = window
    sp.reload_page()
    ov = sp._reload_overlay
    assert ov is not None and not ov.isHidden() and "Reloading" in ov.text()
    assert wait_for(lambda: (win.tick(), ov.isHidden())[1], pump=pump, timeout=15)
    assert sp.sim.page_active()


def test_a_page_shown_in_a_pane_is_not_opened_again_in_a_tab(window):
    """An SA page opens in its workspace: the page shows in a pane of the workspace's tab.
    Simulating it again (the editor, Open Page) shows that tab, not a second tab."""
    win, sp, srv, other, pump = window
    from ctgedit.ui.simulate import window as W
    from ctgedit.ui.simulate import winhost

    pane = W.SimulatedPage(
        other,
        page=_rect_page(),
        sim=_sim(win.current().sim._hub) if hasattr(win.current().sim, "_hub") else None,
    )
    pane.host = winhost.EMBEDDED
    pane.parent_view = sp
    sp.child_views.append(pane)
    win._views.append(pane)
    sp2 = win.open_other_page(other)
    assert sp2 is pane and win.tabs.count() == 1 and win.current() is sp
    assert win.open_page(other) is pane and win.tabs.count() == 1
    win._views.remove(pane)
    sp.child_views.remove(pane)
    pane.stop()
