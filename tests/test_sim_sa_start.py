"""SA pages run the way the runtime runs them."""

import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from ctgedit.sim import client as C  # noqa: E402
from test_sim_engine_source import _rect_page, _sim, wait_for  # noqa: E402
from qtkit import app as _app  # noqa: E402

#: the workspace startup page (FxSa) and a plain page (FxMain)
MASTER, PLAIN = "FxSaMaster", "FxShapes"
windows_only = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")


# -- which pages run in their workspace ------------------------------------------------------
def _project(tmp_path, name, ini, pages):
    """A project folder of copies: its citect.ini and the pages (``(source folder, page)``)
    with the source's pages.DBF."""
    proj = tmp_path / name
    proj.mkdir()
    (proj / "citect.ini").write_text(ini, encoding="cp1252")
    for src, page in pages:
        shutil.copyfile(os.path.join(src, page + ".ctg"), str(proj / (page + ".ctg")))
        shutil.copyfile(os.path.join(src, "pages.DBF"), str(proj / "pages.DBF"))
    return str(proj)


def test_the_startup_page_that_builds_a_workspace_is_found(fx_project, tmp_path, monkeypatch):
    from ctgedit.io import citect_config
    from ctgedit.ui.simulate import sa_start

    monkeypatch.setattr(citect_config, "ini_value", lambda sec, key, default="": default)
    proj = _project(
        tmp_path, "SA", "[MultiMonitors]\nStartupPageScreen1=%s\n" % MASTER, [(fx_project("FxSa"), MASTER)]
    )
    assert sa_start.startup_candidates(proj) == [MASTER, "Startup"]
    assert sa_start.is_workspace_page(os.path.join(proj, MASTER + ".ctg"))
    got = sa_start.workspace_start(proj, "FxSaContent")
    assert got == (MASTER, os.path.join(proj, MASTER + ".ctg"))
    # the startup page itself opens as before (its own READY_FUNC builds the workspace)
    assert sa_start.workspace_start(proj, MASTER) is None
    # [Page] Startup names a page that does not build a workspace: no SA start
    plain = _project(tmp_path, "Plain", "[Page]\nStartup=%s\n" % PLAIN, [(fx_project("FxMain"), PLAIN)])
    assert sa_start.startup_candidates(plain) == [PLAIN, "Startup"]
    assert not sa_start.is_workspace_page(os.path.join(plain, PLAIN + ".ctg"))
    assert sa_start.workspace_start(plain, "FxGenies") is None
    # no startup page of the project at all
    none = _project(tmp_path, "None", "[Page]\nStartup=NoSuchPage\n", [])
    assert sa_start.workspace_start(none, "X") is None


def test_the_client_asks_for_the_start_and_the_navigation():
    s = C.Session.__new__(C.Session)
    s.project, s.requested_root = "X", ""
    assert C.Session.load_args(s, startup="auto", start_page=True) == {
        "project": "X",
        "startup": "auto",
        "startPage": True,
    }
    assert C.PROTOCOL_REVISION >= 11  # load {startPage} and ctsim/navigate


# -- the Simulate tab over a (fake) engine ------------------------------------------------
@pytest.fixture
def servers(tmp_path, monkeypatch):
    folder = str(tmp_path / "servers")
    monkeypatch.setenv(C.SERVERS_ENV, folder)
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)
    return folder


@pytest.fixture
def hub():
    h = C.Hub(linger=0)
    yield h
    h.shutdown()


def _pages(tmp_path):
    proj = tmp_path / "User" / "SAProj"
    proj.mkdir(parents=True)
    for p in ("Master", "Content"):
        (proj / (p + ".ctg")).write_bytes(b"")
    return str(proj)


def _engine(*extra):
    script = os.path.join(HERE, "fake_ctsim.py")
    py = getattr(sys, "_base_executable", "") or sys.executable
    return C.EngineLocation(script, "test", argv=[py, script, "--pages"] + list(extra))


@windows_only
def test_an_sa_page_lists_the_tags_its_panes_cicode_used(servers, hub, tmp_path, monkeypatch):
    # the page's Cicode runs in its pane's window: the tags it used there (ctsim/usedTags of
    # the pane) join the page's list, not the ones the workspace's own page used (window 0)
    from ctgedit.ui.simulate import sa_start

    proj = _pages(tmp_path)
    master = os.path.join(proj, "Master.ctg")
    monkeypatch.setattr(sa_start, "workspace_start", lambda project, page="": ("Master", master))
    sim = _sim(
        hub,
        location=_engine(
            "--navigate",
            "--start-page",
            "Master",
            "--used",
            "0=Tag0009",
            "--used",
            "1=Tag0005;Ghost_LimitLow",
        ),
    )
    sim.PARAMS_EVERY = 0.05
    try:
        sim.start(os.path.join(proj, "Content.ctg"), _rect_page())
        assert wait_for(lambda: sim.focus_window == 1, timeout=20)

        def names():
            sim.poll()
            return {t.name for t in sim.search_tags("", page_only=True)[0]}

        assert wait_for(lambda: {"Tag0005", "Ghost_LimitLow"} <= names(), timeout=15), names()
        assert "Tag0009" not in names()
        ghost = [t for t in sim.search_tags("", page_only=True)[0] if t.name == "Ghost_LimitLow"][0]
        assert not ghost.defined and ghost.comment == "Used by the page's Cicode"
    finally:
        sim.stop()


@windows_only
def test_an_sa_page_opens_in_its_workspace_and_the_tab_shows_window_0(servers, hub, tmp_path, monkeypatch):
    from ctgedit.ui.simulate import sa_start

    proj = _pages(tmp_path)
    master = os.path.join(proj, "Master.ctg")
    monkeypatch.setattr(sa_start, "workspace_start", lambda project, page="": ("Master", master))
    sim = _sim(hub, location=_engine("--navigate", "--start-page", "Master"))
    log = []
    try:
        sim.start(os.path.join(proj, "Content.ctg"), _rect_page())
        # the startup page shows in window 0, the page in its pane (a window of window 0)
        assert wait_for(
            lambda: any(
                w["window"] == 1 and w["page"] == "Content" and w.get("parent") == 0
                for w in sim.engine_windows()[1]
            ),
            timeout=20,
        ), sim.engine_windows()
        assert wait_for(lambda: sim.window == 0, timeout=15)
        assert sim.workspace_page == "Master" and sim.focus_window == 1
        # the tab follows window 0's page (a navigation to the startup page)
        nav = None
        for _k in range(200):
            nav = sim.take_navigation()
            if nav:
                break
            wait_for(lambda: False, timeout=0.02)
        assert nav and nav["page"] == "Master" and nav["window"] == 0, nav
        log.extend(e.text for e in sim.drain())
        assert any("Startup page Master in window 0" in t for t in log), log
    finally:
        sim.stop()


@windows_only
def test_without_a_workspace_or_navigate_the_page_opens_on_its_own(servers, hub, tmp_path, monkeypatch):
    from ctgedit.ui.simulate import sa_start

    proj = _pages(tmp_path)
    monkeypatch.setattr(sa_start, "workspace_start", lambda project, page="": None)
    sim = _sim(hub, location=_engine("--navigate"))
    try:
        sim.start(os.path.join(proj, "Content.ctg"), _rect_page())
        assert wait_for(lambda: sim.page_active(), timeout=20)
        assert sim.window == 0 and sim.focus_window is None and sim.workspace_page == ""
        assert [w["page"] for w in sim.engine_windows()[1]] in ([], ["Content"])
    finally:
        sim.stop()
    # a simulator without ctsim/navigate: the page opens on its own too
    master = os.path.join(proj, "Master.ctg")
    monkeypatch.setattr(sa_start, "workspace_start", lambda project, page="": ("Master", master))
    old = _sim(hub, location=_engine())
    try:
        old.start(os.path.join(proj, "Content.ctg"), _rect_page())
        assert wait_for(lambda: old.page_active(), timeout=20)
        assert old.window == 0 and old.workspace_page == ""
    finally:
        old.stop()


def test_the_pane_tab_draws_the_page_as_the_editor_has_it(tmp_path, monkeypatch):
    """The page went to its workspace pane: the pane's tab draws the tab's own drawing (the
    editor's, unsaved edits included: what the engine runs), not the file read again; once."""
    _app()
    from ctgedit.ui.simulate import window as W
    from ctgedit.ui.simulate.sources import FakeSimulation

    proj = _pages(tmp_path)
    content = os.path.join(proj, "Content.ctg")
    read = []
    monkeypatch.setattr(
        W, "runtime_page", lambda path, parsed=None, doc=None: read.append(path) or _rect_page()
    )
    monkeypatch.setattr(W, "page_model_cached", lambda path: None)
    monkeypatch.setattr(W, "page_model_file_job", lambda path: None)
    win = W.SimulateWindow()
    try:
        drawn = _rect_page()
        owner = W.SimulatedPage(content, page=drawn, sim=FakeSimulation(), parent=win.tabs)
        owner.sim.workspace_page = "Master"
        assert owner.opened_with == (content, drawn)
        sp = win.open_window(owner, {"window": 1, "page": "Content", "path": content})
        assert sp is not None and sp.page is drawn and content not in read
        assert owner.opened_with is None
        # a second window of the page reads its file
        sp2 = win.open_window(owner, {"window": 2, "page": "Content", "path": content})
        assert sp2 is not None and sp2.page is not drawn and content in read
        # not an SA start: a window's page is read from its file
        other = W.SimulatedPage(content, page=_rect_page(), sim=FakeSimulation(), parent=win.tabs)
        sp3 = win.open_window(other, {"window": 3, "page": "Content", "path": content})
        assert sp3 is not None and sp3.page is not other.page
    finally:
        win.close()
        win.deleteLater()


# -- a genie DspSym placed, drawn with its members' states ----------------------------------
TREEVIEW = "fxmore.fx_dspsym"
#: the element's record handles (fxmore.ctl fx_dspsym, in record order: the group 12, the
#: hollow box 18, the label 2, the expand mark 16, the symbol set 9 and its six states; each
#: state's ellipse, 3, 4, 7, 10, 15, 17, has no AN of its own here)
#: -> the member ANs given (31900 is the DspSym's own AN)
HANDLES = {
    12: 31899,
    18: 31898,
    2: 31897,
    16: 31896,
    9: 31895,
    1: 31894,
    5: 31893,
    11: 31892,
    6: 31891,
    8: 31890,
    13: 31889,
}


def _members_scene(fx_page):
    _app()
    from ctgedit.render.scene import PageScene
    from ctgedit.ui.simulate.window import runtime_page

    page = runtime_page(fx_page(PLAIN))
    sc = PageScene()
    sc.set_page(page)
    sc.set_runtime_look(True)
    entry = {
        "an": 31900,
        "kind": "sym",
        "x": 100,
        "y": 200,
        "text": TREEVIEW,
        "args": {
            "mode": 1,
            "type": 1,
            "members": [{"an": an, "handle": h, "name": "", "type": 0} for h, an in HANDLES.items()],
        },
        "serial": 1,
    }
    sc.sim_layer().apply_drawn({31900: entry})
    b = sc.sim_layer().drawn_layer()._built[31900]
    assert b.members is not None
    return sc, b.members


def _items(m, handle):
    k = next(k for k, r in enumerate(m.records) if r.raw.get("_handle") == handle)
    return m.items.get(k, [])


def _state_items(m, handle):
    """The items of a symbol-set state and its members (a state of fxsym's group-root
    symbols is its instance forest: the root draws nothing, its ellipse draws)."""
    k = next(k for k, r in enumerate(m.records) if r.raw.get("_handle") == handle)
    out, todo = [], [k]
    while todo:
        j = todo.pop()
        out.extend(m.items.get(j, []))
        todo.extend(c for c in (m.records[j].members or ()) if isinstance(c, int))
    return out


def test_a_dspsym_genie_draws_its_members_states(fx_page):
    from ctgedit.render.sim_states import SimLayer
    from ctgedit.sim.state import ObjectState

    sc, m = _members_scene(fx_page)
    label = _items(m, 2)[0]
    main, _g, _mode = SimLayer.text_parts(
        m.records[next(k for k, r in enumerate(m.records) if r.raw.get("_handle") == 2)], label
    )
    static = main.text()
    base = label.transform().dx()
    # the label's text, its group moved (the indent), the expand mark turned, a check state
    sc.apply_states(
        {
            31897: ObjectState(31897, text="Cluster1"),
            31899: ObjectState(31899, move=(24.0, 0.0)),
            31896: ObjectState(31896, rotate=90.0, rotate_origin=(110.0, 210.0)),
            31895: ObjectState(31895, symbol_state=1),
        }
    )
    assert main.text() == "Cluster1"
    assert label.transform().dx() == pytest.approx(base + 24.0)
    mark = _items(m, 16)[0]
    assert abs(mark.transform().m12()) == pytest.approx(1.0)  # a quarter turn
    states = [_state_items(m, h) for h in (1, 5, 11, 6, 8, 13)]
    assert all(states)  # every state is built
    shown = [any(i.isVisible() for i in its) for its in states]
    assert shown == [False, True, False, False, False, False], shown
    # the group hidden: its members go; a default state: the static look again
    sc.apply_states({31899: ObjectState(31899, shown=False)})
    assert not label.isVisible()
    sc.apply_states({31899: None, 31897: None, 31896: None, 31895: None})
    assert label.isVisible() and main.text() == static
    assert label.transform().dx() == pytest.approx(base)
    # no state for the set: its first state shows again, as the static drawing has it
    shown = [any(i.isVisible() for i in its) for its in states]
    assert shown == [True, False, False, False, False, False], shown


def test_a_member_colour_and_a_size_factor_0(fx_page):
    from ctgedit.sim.state import ObjectState

    sc, m = _members_scene(fx_page)
    props = _items(m, 18)[0]
    sc.apply_states({31898: ObjectState(31898, fill_colour=0xFF0000)})
    # a hollow shape's animated colour is its line's (a filled one's its fill)
    got = {props.brush().color().name(), props.pen().color().name()}
    assert "#ff0000" in got, got
    # a size factor 0 hides the record (never a singular transform)
    sc.apply_states({31898: ObjectState(31898, scale=(0.0, 1.0))})
    assert not props.isVisible()
    assert props.transform().isInvertible()
    sc.apply_states({31898: None})
    assert props.isVisible()
