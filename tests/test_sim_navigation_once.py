"""A page change in a window is applied once."""

from ctgedit.ui.simulate.ctsim_source import EngineSimulation

NAV = {"windows": [{"window": 0, "page": "FxSim2", "path": r"C:\x\FxSim2.ctg", "parent": -1}]}


class _Session:
    alive = True


def _sim():
    sim = EngineSimulation()
    sim.page_name = "FxSim1"
    with sim._lock:
        sim._window = 0
        sim._session = _Session()
        sim._live = True
    sim._open_page = lambda *a, **k: None  # (the reopen's worker: no engine here)
    return sim


def test_a_second_windows_list_while_a_new_tab_opens_is_not_a_second_navigation():
    sim = _sim()
    gen = sim._gen
    sim._windows_changed(NAV, gen)
    nav = sim.take_navigation()
    assert nav is not None and nav["page"] == "FxSim2"
    # the handler opens the new tab (this tab still owns window 0 meanwhile) ...
    sim._windows_changed(NAV, gen)
    # ... then this tab lets go of the window and opens its page in a free one
    assert sim.reopen_in_new_window()
    assert sim.take_navigation() is None
    # a windows list that comes after it does not name this tab's window any more
    sim._windows_changed(NAV, gen)
    assert sim.take_navigation() is None


def test_a_second_windows_list_while_the_tab_follows_is_not_a_second_navigation():
    sim = _sim()
    gen = sim._gen
    sim._windows_changed(NAV, gen)
    nav = sim.take_navigation()
    assert nav is not None
    sim._windows_changed(NAV, gen)  # before the tab shows the new page
    sim.page_name = "FxSim2"  # (navigated: the tab follows)
    assert sim.take_navigation() is None


def test_a_later_real_navigation_is_still_taken():
    sim = _sim()
    gen = sim._gen
    sim._windows_changed(NAV, gen)
    assert sim.take_navigation() is not None
    sim.page_name = "FxSim2"
    later = {"windows": [{"window": 0, "page": "FxSim3", "path": r"C:\x\FxSim3.ctg"}]}
    sim._windows_changed(later, gen)
    nav = sim.take_navigation()
    assert nav is not None and nav["page"] == "FxSim3"
