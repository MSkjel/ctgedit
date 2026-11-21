"""Undefined tags and popup tags on the real engine."""

import gc
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_sim_pages_live import env, install, page_path, wait_for  # noqa: E402,F401

pytestmark = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")


def _needs_window_tags(session):
    caps = session.hello.get("capabilities") or {}
    if not caps.get("windowTags") or not caps.get("simulateUndefined"):
        pytest.skip("this ctsim.exe has no window tags / simulateUndefined")


def test_a_popup_tab_lists_the_tags_of_its_associations(env):
    """A pump click on Mixer opens !EnergyStartStop in its own window: its tab asks for the
    window's tags, which come through the popup's associations (via)."""
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
        win.open_page(path, sim=sim)
        win.show()
        assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
        _needs_window_tags(sim.session)
        sim.input(InputEvent("down", an=524, ans=(524,), x=100, y=100))
        sim.input(InputEvent("up", an=524, ans=(524,), x=100, y=100))
        assert wait_for(lambda: (win.tick(), len(win.pages()) > 1)[1], timeout=20, pump=pump), (
            sim.engine_windows()
        )
        pop = next(p for p in win.pages() if p.attach_window is not None)
        assert pop.title().lower() == "!energystartstop"

        def via_rows():
            win.tick()
            return [t for t in pop.sim.search_tags("", page_only=True)[0] if t.via]

        assert wait_for(lambda: len(via_rows()) > 0, timeout=20, pump=pump)
        rows = via_rows()
        assert any(t.name.lower() == "mixer_line1pump_on" and t.via == "1" for t in rows), [
            (t.name, t.via) for t in rows
        ]
    finally:
        win.close()
        win.deleteLater()
        pump()
        gc.collect()
