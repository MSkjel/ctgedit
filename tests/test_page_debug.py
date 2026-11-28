"""Seeing what a page does without running it: Preview States, the Click Map, a tag's uses on
the page, the logic Check Page finds, and the fields a genie's inputs set on an object."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402


@pytest.fixture
def doc(fx_page, tmp_path):
    from ctgedit.editing import EditableDocument

    dst = tmp_path / "FxAnim.ctg"
    shutil.copyfile(fx_page("FxAnim"), dst)
    return EditableDocument.open(str(dst))


def _rect(doc, box, **props):
    """A rectangle at ``box`` with properties ``props``: its index."""
    i = doc.create_object("rectangle", rect=box, colour=0x336699)
    for k, v in props.items():
        assert doc.ensure_property(i, k, v)
    return i


# --------------------------------------------------------------------------- Preview States
def test_a_condition_set_by_hand_hides_and_colours_its_objects(doc):
    from ctgedit.features import state_preview as SP

    a = _rect(doc, (40, 40, 80, 80), VISIBLE="Pump_Run AND  Auto")
    b = _rect(doc, (100, 40, 140, 80), VISIBLE="pump_run and auto", COLMODE="0", COL_A="Alarm_On")
    m = SP.PreviewModel(doc)
    hide = m.conditions[SP.condition_id(SP.ON, "Pump_Run AND Auto")]
    # (the same text on two objects is one condition, whatever its case and spaces)
    assert set(hide.objects) == {a, b} and hide.kind == SP.ON
    on = m.conditions[SP.condition_id(SP.ON, "Alarm_On")]
    an_a, an_b = doc.page.objects[a].an, doc.page.objects[b].an
    # nothing set: every object as the page stores it
    assert m.states({}) == {}
    st = m.states({hide.id: True})
    assert not st[an_a].shown and not st[an_b].shown
    st = m.states({hide.id: False, on.id: True})
    assert an_a not in st  # (FALSE: as drawn)
    assert st[an_b].shown and st[an_b].fill_colour == 0x00FF00  # (COL_MAXON's default)
    st = m.states({on.id: False})
    assert st[an_b].fill_colour == 0x336699  # (OFF: the object's own colour)


def test_a_percent_moves_and_fills_with_the_engine_formulas(doc):
    from ctgedit.features import state_preview as SP

    i = _rect(doc, (40, 40, 80, 80), MVX="Level", MVXMINPT="0", MVXMAXPT="200", FIL="Level", FILMAXPC="100")
    m = SP.PreviewModel(doc)
    c = m.conditions[SP.condition_id(SP.PERCENT, "Level")]
    an = doc.page.objects[i].an
    st = m.states({c.id: 50})[an]
    assert st.move[0] == pytest.approx(100.0) and st.move[1] == 0
    assert st.fill_level == pytest.approx(0.5)
    st = m.states({c.id: 150})[an]  # (clamped to the range)
    assert st.move[0] == pytest.approx(200.0) and st.fill_level == pytest.approx(1.0)


def test_the_gradient_and_thresholds_follow_the_engine():
    from ctgedit.features import state_preview as SP

    def near(a, b):
        # (HLS and back rounds a channel by one, as the runtime's does)
        return all(abs(((a >> s) & 0xFF) - ((b >> s) & 0xFF)) <= 2 for s in (0, 8, 16))

    assert near(SP.gradient(0, 0xFF0000, 0x0000FF), 0xFF0000)
    assert near(SP.gradient(100, 0xFF0000, 0x0000FF), 0x0000FF)
    # red (hue 0) to blue (hue 240) passes through green: HLS, not RGB
    assert near(SP.gradient(50, 0xFF0000, 0x0000FF), 0x00FF00)
    assert SP.gradient(150, 0xFF0000, 0x0000FF) == SP.gradient(100, 0xFF0000, 0x0000FF)
    # (the engine's ColourTests.ThresholdsSortThenTheFirstMatchWins)
    rows = SP._threshold_order(list(SP._DEFAULT_THRESHOLDS))
    got = [SP._threshold_colour(v, rows) for v in (3, 10, 50, 90, 99, 85)]
    assert got == [0x0000FF, 0x000080, None, 0x800000, 0xFF0000, None]
    rows = SP._threshold_order([(0, 15, 2), (1, 85, 3), (0, 5, 1), (1, 95, 4)])
    assert SP._threshold_colour(1, rows) == 1 and SP._threshold_colour(97, rows) == 4
    assert SP._threshold_order([(2, 10, 7), (0, 10, 8)])[0][0] == 2
    assert SP._threshold_order([(3, 50, 5), (1, 50, 6)])[0][0] == 3


def test_the_panel_sets_values_and_draws_the_page_again(doc):
    from qtkit import app as _app

    _app()
    from ctgedit.features import state_preview as SP
    from ctgedit.ui.panels.preview_states import PreviewStatesPanel

    a = _rect(doc, (40, 40, 80, 80), VISIBLE="Hide_Me")
    panel = PreviewStatesPanel()
    try:
        values = {}
        panel.set_model(SP.PreviewModel(doc), values)
        cid = SP.condition_id(SP.ON, "Hide_Me")
        it = panel._items[cid]
        assert it.text(0) == "False" and it.text(2) == "1"
        fired = []
        panel.valuesChanged.connect(lambda: fired.append(1))
        it.setCheckState(0, QtCore.Qt.Checked)
        assert values == {cid: True} and fired and it.text(0) == "True"
        chosen = []
        panel.objectsChosen.connect(chosen.append)
        panel._activated(it.child(0), 1)
        assert chosen == [[a]]
        panel.filter.setText("no such text")
        assert it.isHidden()
        panel.filter.setText("hide_me")
        assert not it.isHidden()
        panel.reset()
        assert values == {} and it.checkState(0) == QtCore.Qt.Unchecked
    finally:
        panel.deleteLater()


@pytest.fixture
def win(fx_page, tmp_path):
    from qtkit import app as _app

    _app()
    from ctgedit.ui.main_window import MainWindow

    dst = tmp_path / "FxAnim.ctg"
    shutil.copyfile(fx_page("FxAnim"), dst)
    w = MainWindow()
    w.open_document(str(dst))
    w.resize(1300, 900)
    w.show()
    QtWidgets.QApplication.processEvents()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()


def test_view_preview_states_draws_the_page_in_the_states_set(win):
    from ctgedit.features import state_preview as SP

    doc = win.doc
    a = _rect(doc, (40, 40, 80, 80), VISIBLE="Hide_Me")
    win._refresh()
    assert win.act_preview_states.isEnabled()
    win.act_preview_states.trigger()
    assert not win.dock_preview_states.isHidden()
    panel = win.preview_states
    cid = SP.condition_id(SP.ON, "Hide_Me")
    panel.set_value(cid, True)
    an = doc.page.objects[a].an
    assert not win.scene.sim_states()[an].shown
    assert doc.ui.preview_values == {cid: True}
    # an edit keeps the value and the state
    doc.set_object_rect(a, (40, 40, 90, 90))
    win._refresh()
    assert not win.scene.sim_states()[an].shown
    # off: the page as it is stored
    win.act_preview_states.trigger()
    assert win.dock_preview_states.isHidden() and win.scene.sim_states() == {}


# --------------------------------------------------------------------------- the Click Map
def test_the_covered_part_of_a_box_is_exact():
    from ctgedit.features.click_map import covered_fraction

    assert covered_fraction((0, 0, 10, 10), []) == 0.0
    assert covered_fraction((0, 0, 10, 10), [(0, 0, 5, 10)]) == pytest.approx(0.5)
    assert covered_fraction((0, 0, 10, 10), [(0, 0, 5, 10), (5, 0, 20, 10)]) == 1.0
    assert covered_fraction((0, 0, 10, 10), [(-5, -5, 5, 5), (5, 5, 15, 15)]) == pytest.approx(0.5)


def test_a_button_over_another_takes_its_clicks(doc):
    from ctgedit.features import click_map as CM

    low = doc.create_object("button", rect=(40, 40, 140, 80), text="Low")
    doc.set_input_commands(low, touch={"CMD": "Low_Cmd()"})
    high = doc.create_object("button", rect=(30, 30, 150, 90), text="High")
    doc.set_input_commands(high, touch={"CMD": "High_Cmd()"})
    by = CM.by_index(CM.click_targets(doc))
    assert by[low].blocked and by[low].above == [high] and not by[high].blocked
    taker, under = CM.target_at(list(by.values()), 60, 60)
    assert taker.index == high and [t.index for t in under] == [low]
    from ctgedit.ui.editor.click_map import tip_at

    tip = tip_at(list(by.values()), 60, 60)
    assert "High_Cmd()" in tip and "never reaches" in tip
    # an object over it that can hide covers it only some of the time
    assert doc.ensure_property(high, "VISIBLE", "Night")
    by = CM.by_index(CM.click_targets(doc))
    assert not by[low].blocked and by[low].sometimes
    # ... and Preview States knows when
    by = CM.by_index(CM.click_targets(doc, settled={"night"}))
    assert by[low].blocked


def test_view_click_map_marks_the_page(win):
    from ctgedit.ui.editor import click_map as CMV

    win.act_click_map.trigger()
    assert win.act_click_map.isChecked()
    targets = CMV.targets_of(win.view)
    assert targets is not None
    assert "Click Map" in win.statusBar().currentMessage()
    win.act_click_map.trigger()
    assert CMV.targets_of(win.view) is None


# --------------------------------------------------------------------------- a tag's uses
def test_the_uses_of_a_tag_say_read_or_write():
    from ctgedit.features.tag_uses import _names

    got = _names('Pump1.Run = 1; Toggle(Valve_2); TagWrite("Tank_L", 5); IF A = B THEN X = 2; END', True)
    assert got == [
        ("Pump1.Run", True),
        ("Valve_2", True),
        ("Tank_L", True),
        ("A", False),
        ("B", False),
        ("X", True),
    ]
    assert _names('Tank_L > 50 AND TagRead("Pump9")', False) == [("Tank_L", False), ("Pump9", False)]


def test_the_tags_panel_lists_and_marks_a_tags_uses(win):
    from ctgedit.features.tag_uses import WRITES, PageTagUses
    from ctgedit.ui.editor import tag_marks as TM

    doc = win.doc
    a = _rect(doc, (40, 40, 80, 80), VISIBLE="Valve_7.Open")
    b = doc.create_object("button", rect=(100, 40, 160, 80), text="Go")
    doc.set_input_commands(b, touch={"CMD": "Valve_7.Open = 1"})
    uses = PageTagUses(doc).uses("valve_7")
    assert [(u.index, u.access) for u in uses] == [(a, "reads"), (b, WRITES)]
    win._refresh()
    win.dock_tags.show()
    win.dock_tags.raise_()
    QtWidgets.QApplication.processEvents()
    win.tags_dock.current_tag = lambda: "Valve_7.Open"
    win._tag_marks_refresh()
    tree = win.tags_dock._uses
    assert tree.topLevelItemCount() == 2 and tree.topLevelItem(1).text(0) == "Writes"
    tag, marks = TM.marks_of(win.view)
    assert tag == "Valve_7.Open" and len(marks) == 2
    chosen = []
    win.tags_dock.usesChosen.connect(chosen.append)
    win.tags_dock._use_chosen(tree.topLevelItem(1), 0)
    assert chosen == [[b]] and win._sel == [b]


# --------------------------------------------------------------------------- Check Page
def test_a_constant_condition_is_found():
    from ctgedit.features.page_checks import constant_value

    assert constant_value("1") is True and constant_value(" TRUE ") is True
    assert constant_value("NOT 0") is True and constant_value("(0)") is False
    assert constant_value("FALSE") is False and constant_value("Tag1") is None
    assert constant_value("1 + Tag") is None


def test_check_page_finds_logic_that_compiles_but_cannot_work(doc):
    from ctgedit.features import page_checks as PC

    always = _rect(doc, (40, 40, 80, 80), VISIBLE="1")
    never = _rect(doc, (100, 40, 140, 80), DISABLED="FALSE")
    states = _rect(doc, (160, 40, 200, 80), COLMODE="1", COL_A="Run", COL_C="Fault")
    same = _rect(doc, (220, 40, 260, 80), COLMODE="1", COL_A="Run", COL_B="run")
    low = doc.create_object("button", rect=(40, 100, 140, 140), text="Low")
    doc.set_input_commands(low, touch={"CMD": "Low_Cmd()"})
    high = doc.create_object("button", rect=(30, 90, 150, 150), text="High")
    doc.set_input_commands(high, touch={"CMD": "High_Cmd()"})
    rep = PC.check_document(doc, measure=lambda *a: None)
    got = {(f.code, f.index, f.level) for f in rep.findings}
    assert ("constant_condition", always, PC.WARNING) in got
    assert ("constant_condition", never, PC.INFO) in got
    assert ("state_gap", states, PC.INFO) in got
    assert ("state_repeat", same, PC.INFO) in got
    assert ("click_covered", low, PC.WARNING) in got
    assert not any(f.index == high and f.code == "click_covered" for f in rep.findings)


def test_an_empty_input_is_found_only_where_it_breaks_the_cicode():
    from ctgedit.features.genie_uses import breaks_when_empty as breaks

    assert breaks("%Equip%.Value > 5", "Equip")
    assert breaks("%Level% > 50", "Level")
    assert breaks("NOT %Hide%", "Hide")
    assert not breaks("%Hide%", "Hide")  # (the whole field: no condition)
    assert not breaks('F("%Text%")', "Text")  # (an empty string is a value)
    assert not breaks("%Tag%_PV", "Tag")  # (an optional part of a name)
    assert not breaks("%Equipment%.%Prefix%OP", "Prefix")
    assert not breaks("F(%Arg%, 1)", "Arg")
    assert not breaks("IF X THEN %Fn%; END", "Fn", command=True)


# --------------------------------------------------------------------------- Set by the genie
def _placed(fx_install):
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(fx_install.page("FxGenies"))
    g = d.place_library_element(
        "fxnest", "n4", "ctm", (800, 400), params={"Name": "FX_T201", "Extra": "FX_T201 tip"}
    )
    assert g is not None
    return d, g


def test_an_object_in_a_genie_says_which_inputs_set_its_fields(fx_install):
    from ctgedit.ui.panels.properties_inputs import object_sources

    d, g = _placed(fx_install)
    texts = [
        j
        for j in d.page.subtree(g)[1:]
        if d.page.objects[j].kind.value == "text" and d.page.objects[j].style.text == "FX_T201 3 L1"
    ]
    assert texts
    root, uses, values = object_sources(d, texts[0])
    assert root == g and values["name"] == "FX_T201"
    (u,) = uses
    assert u.value == "FX_T201 3 L1" and u.names() == ["Name"]
    assert "%Name%" in u.template and "L1" in u.template
    # the genie itself and an object outside it have none
    assert object_sources(d, g) is None


def test_the_properties_panel_shows_what_the_genie_sets(fx_install):
    from qtkit import app as _app

    _app()
    from ctgedit.ui.panels.properties import PropertyPanel
    from ctgedit.ui.panels.properties_inputs import SourcesTree

    d, g = _placed(fx_install)
    j = next(
        j
        for j in d.page.subtree(g)[1:]
        if d.page.objects[j].kind.value == "text" and d.page.objects[j].style.text == "FX_T201 3 L1"
    )
    p = PropertyPanel()
    try:
        p.set_target(d, [j])
        boxes = [b for b in p.findChildren(QtWidgets.QWidget) if getattr(b, "set_lazy", None)]
        for b in boxes:
            if hasattr(b, "expand"):
                b.expand()
        (tree,) = p.findChildren(SourcesTree)
        top = tree.topLevelItem(0)
        assert top.text(1) == "FX_T201 3 L1"
        rows = [top.child(k) for k in range(top.childCount())]
        assert rows[0].text(0).startswith("From ") and "%Name%" in rows[0].text(0)
        assert (rows[1].text(0), rows[1].text(1)) == ("Name", "FX_T201")
        chosen = []
        p.selectObjects.connect(chosen.append)
        tree.itemActivated.emit(rows[1], 0)
        assert chosen == [[g]]
    finally:
        p.deleteLater()
