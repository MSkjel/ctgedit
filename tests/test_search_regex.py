"""The regex toggle on search boxes."""

from ctgedit.infra.textmatch import TextMatch
from qtkit import app as _app


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _sim(hub):
    """An engine simulation with the demo values on and a fake fallback (no engine)."""
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.sources import FakeSimulation

    sim = EngineSimulation(hub=hub, fake=FakeSimulation(clock=_Clock()))
    sim.demo_values = True
    return sim


def _entries():
    """Page browser entries of two projects (FxP, FxQ): names, titles, a comment."""
    from ctgedit.io.catalog import Entry

    mk = lambda name, **k: Entry(
        "page",
        k.pop("project", "FxP"),
        name,  # noqa: E731
        r"C:\x\%s.ctg" % name,
        **k,
    )
    return [
        mk("page10", title="Tenth", mtime_ns=5),
        mk("page2", title="Second", mtime_ns=9),
        mk("!FxPopup", title="A popup", mtime_ns=1),
        mk("FxShapes", title="Fx shapes", comment="milk", mtime_ns=3),
        mk("FxStale", registered=False, mtime_ns=2),
        mk("FxOther", project="FxQ", title="Elsewhere", mtime_ns=4),
    ]


# -- the rule -----------------------------------------------------------------------------------
def test_one_rule_plain_text_or_a_regular_expression():
    plain = TextMatch(" tank ")
    assert plain("Tank1_Level") and plain("x", "BigTANK") and not plain("Pump")
    assert TextMatch("")("anything") and TextMatch("", True)("anything")
    rx = TextMatch(r"^tank\d_(level|temp)$", True)
    assert rx("Tank1_Level") and rx("TANK2_TEMP") and not rx("Tank1_LevelHigh")
    assert rx("nope", "Tank3_Temp")  # any of the texts
    bad = TextMatch("(tank", True)
    assert not bad.ok and bad.error and not bad("tank")
    assert TextMatch("a.c", True).spans("xABCxabc") == [(1, 4), (5, 8)]
    assert TextMatch("ank").spans("Tank tank") == [(1, 4), (6, 9)]
    # the plain rule treats regex characters as text
    assert TextMatch("a.c")("a.c") and not TextMatch("a.c")("abc")


# -- the box ------------------------------------------------------------------------------------
def test_the_toggle_is_remembered_per_box_and_a_bad_expression_says_why():
    _app()
    from ctgedit.ui.style import theme
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().remove(theme.SEARCH_REGEX_SETTING)
    a = theme.SearchField("Search tags", tip="Tag, equipment or comment")
    assert not a.regex
    got = []
    a.searchChanged.connect(got.append)
    a.setText("(tank")
    a.set_regex(True)
    assert got == ["(tank"]  # (switching searches at once)
    assert a.property("regexInvalid") and "Not a regular expression" in a.toolTip()
    assert a.toolTip().startswith("Tag, equipment or comment")
    assert not a.matcher()("tank")
    a.setText("^tank")
    assert not a.property("regexInvalid") and a.toolTip() == "Tag, equipment or comment"
    assert a.matcher()("Tank1") and not a.matcher()("xTank")
    # another box of the same name starts with it on; a box of another name does not
    assert theme.SearchField("Search tags").regex
    assert not theme.SearchField("Search objects").regex
    a.set_regex(False)
    assert not theme.SearchField("Search tags").regex
    assert app_settings().value(theme.SEARCH_REGEX_SETTING, [], type=list) in ([], None)


def test_a_plain_line_edit_gets_the_same_toggle():
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.style import theme

    e = QtWidgets.QLineEdit()
    e.setAccessibleName("Some box")
    t = theme.RegexToggle(e)
    assert t.action in e.actions() and not t.on
    t.set_on(True)
    e.setText("[x")
    assert e.property("regexInvalid")
    t.set_on(False)
    assert not e.property("regexInvalid")


# -- the lists ----------------------------------------------------------------------------------
def test_find_and_replace_with_a_regular_expression_and_its_groups():
    from ctgedit.features import find_replace as FR

    o = FR.FindOptions(r"tank(\d)", regex=True)
    assert FR.match_spans("Tank1 + tank22", o.text, o.match, o.regex) == [(0, 5), (8, 13)]
    assert FR.replaced("Tank1 + tank22", o, r"Vessel\1") == "Vessel1 + Vessel22"
    w = FR.FindOptions(r"loop_\d", match=FR.MATCH_WORD, regex=True)
    assert FR.match_spans("LOOP_1 LOOP_10 x", w.text, w.match, True) == [(0, 6)]
    whole = FR.FindOptions(r"tank\d", match=FR.MATCH_WHOLE, regex=True)
    assert FR.replaced("Tank7", whole, "T") == "T" and FR.replaced("Tank7x", whole, "T") is None
    assert FR.match_spans("abc", "(a", FR.MATCH_PART, True) == []
    # a group that is not there: the replacement as typed
    assert FR.replaced("Tank1", o, r"\9") == r"\9"


def test_the_manual_searches_with_a_regular_expression():
    from ctgedit import help as H

    hits = H.search(r"genie\w*", regex=True)
    assert hits
    assert H.search("(genie", regex=True) == []


def test_the_simulators_tag_search_takes_the_regex(tmp_path, monkeypatch):
    from ctgedit.sim import client as C
    from ctgedit.ui.simulate.sources import TagInfo

    hub = C.Hub(linger=0)
    try:
        sim = _sim(hub)
        sim._page_tags = [
            TagInfo("Tank1_Level", type="REAL", page_reads=True),
            TagInfo("Tank2_Temp", type="REAL", page_reads=True),
            TagInfo("Pump1_Run", type="DIGITAL", page_reads=True, equipment="Plant.Tank1", item="Run"),
        ]
        rows, total = sim.search_tags(r"^tank\d_", page_only=True, regex=True)
        assert [t.name for t in rows] == ["Tank1_Level", "Tank2_Temp"]
        rows, _t = sim.search_tags(r"tank1\.run$", page_only=True, regex=True)
        assert [t.name for t in rows] == ["Pump1_Run"]  # (equipment.item too)
        assert sim.search_tags("(tank", page_only=True, regex=True)[0] == []
        assert sim.search_tags("(tank", page_only=False, regex=True) == ([], 0)
        # every tag: the simulator is asked with the regex
        sent = []

        class S:
            def request_async(self, name, args, callback=None, timeout=None):
                sent.append((name, dict(args)))

        sim._session = S()
        sim.search_tags(r"^tank\d", page_only=False, regex=True)
        assert sent and sent[-1][1].get("regex") is True and sent[-1][1]["filter"] == r"^tank\d"
    finally:
        hub.shutdown()


def test_the_tag_index_matches_a_regular_expression(fx_project):
    """FxMain's variable.DBF in the fake install: FX_T201_LT .. FX_T226_OUT (26 tanks of LT,
    PV, SP, OUT, with equipment FxPlant.T2xx) among the other FX_ tags."""
    from ctgedit.io import project as PR

    idx = PR.tag_index(fx_project("FxMain"))
    order = idx.order()
    _names = lambda codes: {idx.cells(c)[0] for c in codes}  # noqa: E731
    # (an Equip.Item reference is listed under its tag: it matches the tag's name, as plain
    # text does too)
    tags = lambda codes: {idx.cells(c)[0] for c in codes if c >= 0}  # noqa: E731
    hit = tags(idx.filter(order, r"^fx_t20[0-9]_lt$", regex=True))
    assert hit == {"FX_T20%d_LT" % n for n in range(1, 10)}
    assert len(idx.filter(order, "(fx_t", regex=True)) == 0
    # plain text: regex characters as text
    assert len(idx.filter(order, "^fx_t", regex=False)) == 0
    # the per-code path (few codes) gives the same
    few = idx.filter(order, "fx_t20", regex=False)
    assert tags(idx.filter(few, r"_lt$", regex=True)) == {n for n in tags(few) if n.lower().endswith("_lt")}
    assert len(tags(few)) == 9 * 4  # FX_T201 .. FX_T209: LT, PV, SP, OUT


def test_the_page_browser_takes_one_regular_expression():
    _app()
    from ctgedit.ui.browser.model import CatalogModel

    m = CatalogModel(None)
    m.set_entries(_entries(), "page")
    names = lambda: [e.name for e in m.entries()]  # noqa: E731
    m.set_query(r"^page\d+$", regex=True)
    assert names() == ["page2", "page10"]
    m.set_query("milk|elsewhere", regex=True)  # (comments and titles too)
    assert set(names()) == {"FxShapes", "FxOther"}
    m.set_query("proj:fxq", regex=True)  # (no fields: the text is the expression)
    assert names() == []
    m.set_query("(page", regex=True)
    assert names() == []
    m.set_query("proj:fxq")  # the words again
    assert names() == ["FxOther"]


def test_the_command_palette_takes_the_regex():
    _app()
    from PySide6 import QtGui, QtWidgets
    from ctgedit.ui.dialog import palette
    from ctgedit.ui.style import theme
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().remove(theme.SEARCH_REGEX_SETTING)
    win = QtWidgets.QMainWindow()
    menu = win.menuBar().addMenu("Edit")
    for t in ("Undo", "Redo", "Select All", "Find..."):
        menu.addAction(QtGui.QAction(t, win))
    p = palette.CommandPalette(win)
    p._actions = palette.harvest_actions(win.menuBar())
    labels = lambda: [
        p._list.item(k).text().split("    ")[0]  # noqa: E731
        for k in range(p._list.count())
    ]
    p._regex.set_on(True)
    p._edit.setText("^(un|re)do$")
    assert sorted(labels()) == ["Redo", "Undo"]
    p._edit.setText("(undo")
    assert len(labels()) == 1 and labels()[0].startswith("Not a regular expression")
    p._regex.set_on(False)
    p._edit.setText("undo")
    assert "Undo" in labels()
