"""Fill > Color and Fill > Level."""

import copy
import glob
import os
import shutil
import struct

import pytest

import fxinstall

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402

from ctgedit.format import colourpalette as P  # noqa: E402
from ctgedit.format import colourstate as CS  # noqa: E402
from ctgedit.format import fillcolour as F  # noqa: E402
from ctgedit.format import writer  # noqa: E402
from ctgedit.format.page import parse_ctg, parse_ctg_bytes  # noqa: E402
from qtkit import app as _app

#: the FxMain pages the golden rules read as CtDraw saved them (each a 'pages' unit)
ORACLE_PAGES = (
    "FxColours",
    "FxAnim",
    "FxShapes",
    "FxGenies",
    "FxStyles",
    "FxTextKinds",
    "FxSymbols",
    "FxMembers",
)


@pytest.fixture(autouse=True)
def _derive_check(monkeypatch):
    # every re-derive after an edit is compared with a full one
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")


def _doc(fx_page, name="FxShapes"):
    """A page of FxMain in the fx_install copy (FxMain keeps its citect.pal)."""
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(fx_page(name))


def _sheet(doc, index, **kw):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    return ObjectPropertiesDialog(doc, index, **kw)


def _rect(doc, colour=0x336699, filled=True):
    return doc.create_object(
        "rectangle" if filled else "rectangle_outline", rect=(5, 5, 80, 60), colour=colour
    )


def _col(doc, i, prefixes=("COL", "FIL")):
    return {k: v for k, v in doc.object_properties(i).items() if k.upper().startswith(prefixes)}


def _state(po):
    """The record's colour-state array bytes and layout."""
    L = CS.layout(po)
    return po.body_raw[L["at"] : L["end"]], L


def _static(po):
    L = CS.layout(po)
    return CS.state0_source(po.body_raw, L["style_at"])


# ------------------------------------------------------------------ the palette
def _fx_pages(fx_project):
    """[(page name, records)] of every page of FxMain in the fx_install copy."""
    out = [
        (os.path.basename(f), parse_ctg(f).page.objects)
        for f in sorted(glob.glob(os.path.join(fx_project("FxMain"), "*.ctg")))
    ]
    assert len(out) > 40
    return out


def _oracle_pages():
    """[(page name, records)] of :data:`ORACLE_PAGES` as CtDraw saved them (skips while
    the capture is missing)."""
    return [
        (n, parse_ctg_bytes(fxinstall.oracle_bytes("pages", "FxMain/" + n, "page.ctg")).page.objects)
        for n in ORACLE_PAGES
    ]


def _array_defaults(table, pages):
    """(entries without a key, those not the palette's colour) over ``pages``."""
    seen = bad = 0
    for _name, objs in pages:
        for po in objs:
            vb = writer.value_bag(po) if po.raw_payload is None else None
            props = dict(vb or ())
            if not props.get("COL_ARR_0"):
                continue
            L = CS.layout(po)
            for i in range(1, L.get("arr_n", 0)):
                if not props.get("COL_ARR_%d" % i):
                    seen += 1
                    bad += CS.pair_at(po.body_raw, L["arr_at"] + 8 * i) != table[i]
    return seen, bad


def test_array_defaults_are_the_project_palette(fx_project):
    """Array defaults are the project palette."""
    table = P.table_for_project(fx_project("FxMain"))
    assert sorted(P.V1_ORDER) == list(range(256))
    seen, bad = _array_defaults(table, _fx_pages(fx_project))
    assert seen > 300 and bad == 0
    assert [table[i][0] for i in range(16)] == list(P.STANDARD16)


@pytest.mark.golden
def test_array_defaults_are_the_project_palette_in_ctdraws_saves(fx_project):
    """The same over CtDraw's PageOpen + PageSave of the pages (its saver writes the
    entries without a key)."""
    table = P.table_for_project(fx_project("FxMain"))
    seen, bad = _array_defaults(table, _oracle_pages())
    assert seen > 300 and bad == 0


def test_palette_file_parse():
    text = (
        '"Citect.Pal Version 2"\r\n0 0 0 0 0 0 0 0 "Black"\r\n'
        '255 0 0 0 0 0 0 0 "Flash Red"\r\n255 255 255 255 255 255 255 255 "Transparent"\r\n'
    )
    ents = P.parse(text)
    assert ents == [(0, 0), (0xFF0000, 0x000000), (0xFFFFFFFF, 0xFFFFFFFF)]
    t = P.v1_table(ents)
    # palette entry i holds colour number V1_ORDER[i]
    assert t[0] == (0, 0) and t[P.V1_ORDER[1]] == (0xFF0000, 0)
    assert t[P.V1_ORDER[2]] == (0xFFFFFFFF, 0xFFFFFFFF)
    assert P.standard_table()[12] == (0xFF0000, 0xFF0000)


# ------------------------------------------------------------------ threshold rows
def test_threshold_rows_read_and_write_ctdraws_form():
    t = F.Threshold.parse("<,5,0x000000FF,0x000000FF")  # a CtDraw default row
    assert (t.greater, t.equal, t.value, t.on, t.off) == (False, False, 5, 0xFF, 0xFF)
    assert t.text() == "<,5,0x000000ff,0x000000ff"  # %0.8x: the loader's form
    assert t.comparison == "<"
    t = F.Threshold.parse(">=,85,0x00ff8000,0x00800000")
    assert t.comparison == ">=" and t.pair == (0xFF8000, 0x800000)
    assert t.text() == ">=,85,0x00ff8000,0x00800000"
    # the saver's binary row agrees (u8 flags, u16 value, on, off)
    assert CS.parse_thr(t.text()) == struct.pack("<BHII", 3, 85, 0xFF8000, 0x800000)
    assert [r.text() for r in F.default_thresholds()] == [
        "<,5,0x000000ff,0x000000ff",
        "<,15,0x00000080,0x00000080",
        ">,85,0x00800000,0x00800000",
        ">,95,0x00ff0000,0x00ff0000",
    ]


# ------------------------------------------------------------------ every colour record
def _colour_records(pal, pages):
    for _name, objs in pages:
        for po in objs:
            if po.raw_payload is not None:
                continue
            vb = writer.value_bag(po)
            if vb and any(k.upper().startswith("COL") for k, _v in vb):
                yield pal, po, dict(vb)


def _survives_an_apply(records, short=None):
    """Check that Apply writes nothing when nothing changed."""
    from ctgedit.editing import EditableDocument

    modes = {}
    old = CS._palette
    try:
        for pal, po, props in records:
            CS.use_palette(pal)
            L = CS.layout(po)
            slot = EditableDocument.fill_static_slot(po)
            b = po.body_raw
            static = CS.state0_source(b, L["style_at"]) if slot is not None else CS.pair_at(b, L["state0_at"])
            arr = [CS.pair_at(b, L["arr_at"] + 8 * k) for k in range(L["arr_n"])] if "arr_at" in L else None
            sta = [CS.pair_at(b, L["sta_at"] + 8 * k) for k in range(L["sta_n"])] if "sta_at" in L else None
            fc = F.read(
                props,
                static,
                static_editable=slot is not None,
                palette=pal,
                binary_array=arr,
                binary_states=sta,
            )
            assert F.apply_edits(props, fc, fc.copy()) == ([], None)
            get = F.getter(props)
            target = F.target_values(fc, get)
            if fc.mode == F.THRESHOLD:
                for i, row in enumerate(fc.thresholds):
                    s = get("COL_THR_%d" % i)
                    if s and F.Threshold.parse(s) == row:
                        target["COL_THR_%d" % i] = s
            q = copy.copy(po)
            q.body_raw = bytes(po.body_raw)
            q.bags = list(po.bags)
            changes = [(k, v) for k, v in target.items() if get(k) != v]
            writer.set_object_properties(q, changes)
            CS.rebuild(q)
            same = _state(q)[0] == b[L["at"] : L["end"]]
            if (
                not same
                and short is not None
                and changes
                and all(not get(k) or v is None for k, v in changes)
            ):
                short.append((props.get("__AN_NAME__") or po.index, [k for k, _v in changes]))
                continue
            assert same, (props, fc.mode)
            modes[fc.mode] = modes.get(fc.mode, 0) + 1
    finally:
        CS.use_palette(old)
    return modes


def test_every_fixture_colour_record_survives_an_apply(fx_project):
    """Every FxMain record with a COL_ key (FxColours' FxCm0 .. FxCm4 give each type,
    FxAnim and FxStyles more)."""
    pal = P.table_for_project(fx_project("FxMain"))
    short = []
    modes = _survives_an_apply(_colour_records(pal, _fx_pages(fx_project)), short)
    # (the generator stores every colour key as CtDraw's Fill apply writes it)
    assert not short, "%d FxMain records lack keys the apply adds, such as %s" % (
        len(short),
        "; ".join("%s: %s" % (n, ", ".join(k)) for n, k in short[:3]),
    )
    assert set(modes) == set(F.TYPES) and sum(modes.values()) >= 20, modes


@pytest.mark.golden
def test_every_colour_record_of_ctdraws_saves_survives_an_apply(fx_project):
    """The same over CtDraw's PageOpen + PageSave of the pages: the arrays CtDraw wrote."""
    pal = P.table_for_project(fx_project("FxMain"))
    modes = _survives_an_apply(_colour_records(pal, _oracle_pages()))
    assert set(modes) == set(F.TYPES), modes


# ------------------------------------------------------------------ each type round-trips
def test_each_type_round_trips(fx_page):
    """Each type set on the sheet stores CtDraw's keys and the matching
    colour-state array; reopened (sheet and saved page) it shows the same."""
    from ctgedit.editing import EditableDocument

    doc = _doc(fx_page)
    i = _rect(doc)
    dlg = _sheet(doc, i)
    ed = dlg.fill_color_editor
    assert ed.current().mode == F.ON_OFF and ed.current().static == (0x336699, 0x336699)
    # an object without COL_MAXON shows CtDraw's green ON colour
    assert F.read({}, (0, 0)).on == (F.DEFAULT_ON, F.DEFAULT_ON)

    # On / Off
    ed._on_expr.setText("Tag_1 > 5")
    ed._on_col.pick(0xFF0000, 0x000000)  # a flashing ON colour
    n = len(doc._undo)
    assert dlg.apply() and len(doc._undo) == n + 1
    assert _col(doc, i) == {"COL_A": "Tag_1 > 5", "COL_MAXON": "0x00ff0000,0x00000000"}
    po = doc.parsed.page.objects[i]
    arr, L = _state(po)
    assert L["flag"] == 0x23 and CS.pair_at(po.body_raw, L["maxon_at"]) == (0xFF0000, 0)
    assert po.subtype_size & 0x10  # dynamic

    # Multi-state: condition A carries over, a second condition, a state colour
    ed._radios[F.MULTI_STATE].setChecked(True)
    assert ed.current().conditions[0] == "Tag_1 > 5"
    ed._add_condition()
    ed._cond_edit.setText("Tag_2")
    assert len(ed.current().states) == 4
    ed._states.setCurrentCell(3, 1)
    ed._state_col.pick(0x00FFFF)
    assert dlg.apply()
    got = _col(doc, i)
    assert got == {
        "COLMODE": "1",
        "COL_A": "Tag_1 > 5",
        "COL_B": "Tag_2",
        "COL_STA_0": "0x00336699,0x00336699",
        "COL_STA_1": "0x00ffff00,0x00ffff00",
        "COL_STA_2": "0x00ff0000,0x00ff0000",
        "COL_STA_3": "0x0000ffff,0x0000ffff",
    }
    _arr, L = _state(doc.parsed.page.objects[i])
    assert L["flag"] == 0x05 and L["sta_n"] == 4

    # Array: only colours that are not the default are stored
    ed._radios[F.ARRAY].setChecked(True)
    ed._arr_expr.setText("Mode_Tag")
    ed._array.setCurrentCell(5, 1)
    ed._array_col.pick(0x123456)
    assert dlg.apply()
    got = _col(doc, i)
    assert got == {
        "COLMODE": "2",
        "COL_ARR": "Mode_Tag",
        "COL_ARR_0": "0x00336699,0x00336699",
        "COL_ARR_5": "0x00123456,0x00123456",
    }
    po = doc.parsed.page.objects[i]
    _arr, L = _state(po)
    assert L["flag"] == 0x09 and L["arr_n"] == 256
    pal = doc.colour_palette()
    for k in (1, 6, 12, 200):  # the palette defaults
        assert CS.pair_at(po.body_raw, L["arr_at"] + 8 * k) == pal[k]

    # Threshold with a range; the four default rows
    ed._radios[F.THRESHOLD].setChecked(True)
    assert ed._thr_expr.text() == "Mode_Tag"  # carried over
    ed._thr_expr.setText("Level")
    rf = ed._thr_expr.range_field
    rf.check.setChecked(True)
    rf.minimum.setText("0")
    rf.maximum.setText("200")
    ed._thr.setCurrentCell(3, 2)
    ed._thr_cmp.setCurrentIndex(3)  # >=
    assert dlg.apply()
    got = _col(doc, i)
    assert got == {
        "COLMODE": "3",
        "COL_EXP": "(Level)^(0,200)",
        "COL_THR_0": "<,5,0x000000ff,0x000000ff",
        "COL_THR_1": "<,15,0x00000080,0x00000080",
        "COL_THR_2": ">,85,0x00800000,0x00800000",
        "COL_THR_3": ">=,95,0x00ff0000,0x00ff0000",
    }
    _arr, L = _state(doc.parsed.page.objects[i])
    assert L["flag"] == 0x11 and L["thr_n"] == 4

    # Gradient: the range carries over, at maximum = COL_MAXON
    ed._radios[F.GRADIENT].setChecked(True)
    assert ed._grad_expr.text() == "(Level)^(0,200)"
    ed._max_col.pick(0x0000FF)
    assert dlg.apply()
    assert _col(doc, i) == {
        "COLMODE": "4",
        "COL_EXP": "(Level)^(0,200)",
        "COL_MAXON": "0x000000ff,0x000000ff",
    }
    before = doc.fill_colour(i)
    doc.save()
    dlg.close()

    doc2 = EditableDocument.open(doc.path)
    after = doc2.fill_colour(i)
    assert (after.mode, after.expression, after.on, after.static) == (
        before.mode,
        before.expression,
        before.on,
        before.static,
    )
    dlg = _sheet(doc2, i)
    assert dlg.fill_color_editor.current().mode == F.GRADIENT
    assert dlg.tab_marked("Fill", "Colour")
    assert dlg.apply() is False  # nothing changed
    dlg.close()


def test_switching_type_removes_the_other_types_keys(fx_page):
    doc = _doc(fx_page)
    i = _rect(doc)
    dlg = _sheet(doc, i)
    ed = dlg.fill_color_editor
    ed._radios[F.MULTI_STATE].setChecked(True)
    ed._cond_edit.setText("A_Tag")
    ed._conds.setCurrentCell(1, 1)
    ed._cond_edit.setText("B_Tag")
    assert dlg.apply()
    # (condition C stayed blank: it is not stored, 2 conditions = 4 states)
    got = _col(doc, i)
    assert {"COL_A", "COL_B", "COL_STA_3"} <= set(got) and "COL_STA_4" not in got
    ed._radios[F.ARRAY].setChecked(True)
    assert ed._arr_expr.text() == "A_Tag"  # the expression carries
    assert dlg.apply()
    got = _col(doc, i)
    assert got["COLMODE"] == "2" and got["COL_ARR"] == "A_Tag"
    assert not [k for k in got if k.startswith(("COL_STA_", "COL_B")) or k == "COL_A"]
    _arr, L = _state(doc.parsed.page.objects[i])
    assert not L["flag"] & 0x04 and L["flag"] & 0x08
    # back to On / Off: COLMODE removed (CtDraw's for type 0)
    ed._radios[F.ON_OFF].setChecked(True)
    assert dlg.apply()
    got = _col(doc, i)
    assert got == {"COL_A": "A_Tag", "COL_MAXON": "0x0000ff00,0x0000ff00"}
    _arr, L = _state(doc.parsed.page.objects[i])
    assert L["flag"] == 0x23  # no array section left
    dlg.close()


def test_stored_ranged_expression_becomes_condition_a_without_its_range(fx_page):
    """A stored Threshold / Gradient expression carries its range suffix;
    switched to Multi-state (or any other type) its condition A / expression
    is the expression alone -- the range is its own field."""
    assert F.read({"COLMODE": "3", "COL_EXP": "(Level)^(0,200)"}, (0, 0)).conditions[0] == "Level"
    assert F.read({"COLMODE": "4", "COL_EXP": "Speed"}, (0, 0)).conditions[0] == "Speed"
    doc = _doc(fx_page)
    i = _rect(doc)
    assert doc.set_fill_keys(i, [("COLMODE", "3"), ("COL_EXP", "(Level)^(0,200)")])
    dlg = _sheet(doc, i)
    ed = dlg.fill_color_editor
    assert ed._thr_expr.text() == "(Level)^(0,200)"
    ed._radios[F.MULTI_STATE].setChecked(True)
    assert ed._cond_edit.text() == "Level" and ed.current().conditions[0] == "Level"
    assert dlg.apply()
    got = _col(doc, i)
    assert got["COLMODE"] == "1" and got["COL_A"] == "Level" and "COL_EXP" not in got
    dlg.close()


def test_fill_keys_refused_on_a_placed_genie_root(fx_page):
    """A placed genie ROOT keeps no colour / level animation of its own
    (CtDraw's page load re-composes the instance and drops it): the fill
    writer refuses it like ensure_property does, and writes nothing."""
    doc = _doc(fx_page, "FxGenies")
    root = next(k for k in range(len(doc.parsed.page.objects)) if doc._genie_root_drops(k, "COL_A"))
    before = bytes(doc.parsed.page.objects[root].body_raw)
    n = len(doc._undo)
    assert doc.set_fill_keys(root, [("COL_A", "Alarm"), ("COL_MAXON", "0x00ff0000,0x00ff0000")]) is False
    assert doc.set_fill_keys(root, [("FIL", "Level")], label="fill level") is False
    assert bytes(doc.parsed.page.objects[root].body_raw) == before and len(doc._undo) == n


def test_off_colour_is_the_appearance_fill(fx_page):
    """The OFF colour / state 0 / array colour 0 / at-minimum colour is the
    Appearance page's fill colour (CtDraw's FillColor_StaticColor): editing
    one shows in the other, Apply stores it once (one undo step) in the
    style block, and state 0 follows; a hollow shape's is its line colour."""
    from ctgedit.format import geometry as G

    doc = _doc(fx_page)
    i = _rect(doc)
    dlg = _sheet(doc, i)
    ed = dlg.fill_color_editor
    ed._on_expr.setText("Run")
    ed._off_col.pick(0x00AA00)
    assert dlg._col_fill.pair() == (0x00AA00, 0x00AA00)
    assert ed._min_col.pair() == (0x00AA00, 0x00AA00)
    n = len(doc._undo)
    assert dlg.apply() and len(doc._undo) == n + 1
    po = doc.parsed.page.objects[i]
    assert _static(po) == (0x00AA00, 0x00AA00)
    assert CS.pair_at(po.body_raw, CS.layout(po)["state0_at"]) == (0x00AA00, 0x00AA00)
    # the Appearance field edits the static colour too
    dlg._col_fill.pick(0x445566)
    assert ed._off_col.pair() == (0x445566, 0x445566)
    ed._radios[F.ARRAY].setChecked(True)
    assert ed.current().array[0] == (0x445566, 0x445566)
    ed._radios[F.ON_OFF].setChecked(True)
    assert dlg.apply()
    assert _static(doc.parsed.page.objects[i]) == (0x445566, 0x445566)
    doc.undo()
    assert _static(doc.parsed.page.objects[i]) == (0x00AA00, 0x00AA00)
    dlg.close()
    # a hollow rectangle: the static colour is the LINE colour
    j = _rect(doc, colour=0x224466, filled=False)
    po = doc.parsed.page.objects[j]
    assert doc.fill_static_slot(po) == G.SLOT_LINE
    dlg = _sheet(doc, j)
    ed = dlg.fill_color_editor
    assert ed.current().static == (0x224466, 0x224466)
    ed._on_expr.setText("Run")
    ed._off_col.pick(0x113355)
    assert dlg._col_line.pair() == (0x113355, 0x113355)
    assert dlg.apply()
    cb = G.find_colour_block_exact(doc.parsed.page.objects[j])
    assert tuple(cb.slots[G.SLOT_LINE]) == (0x113355, 0x113355)
    dlg.close()


def test_fill_page_on_every_shape_class(fx_page):
    """The static colour is the class's own channel: a pipe's highlight (its
    load copies it to fill + line), a text's colour, a line's line colour, a
    closed shape's fill; the matching Appearance field follows the OFF colour."""
    from ctgedit.format import geometry as G

    doc = _doc(fx_page)
    cases = [
        ("ellipse", dict(rect=(5, 5, 60, 40)), G.SLOT_FILL, "_col_fill"),
        ("polygon", dict(nodes=[(5, 5), (60, 5), (30, 50)]), G.SLOT_FILL, "_col_fill"),
        ("line", dict(nodes=[(5, 5), (60, 40)]), G.SLOT_LINE, "_col_line"),
        ("pipe", dict(nodes=[(5, 5), (60, 5), (60, 40)]), G.SLOT_HILIGHT, "_col_hilight"),
        ("text", dict(rect=(5, 5, 60, 20), text="Tank"), G.SLOT_FILL, "_col_fore"),
    ]
    for kind, kw, slot, field in cases:
        i = doc.create_object(kind, colour=0x224466, **kw)
        po = doc.parsed.page.objects[i]
        assert doc.fill_static_slot(po) == slot, kind
        dlg = _sheet(doc, i)
        assert "fill_color" in dlg.page_ids(), kind
        ed = dlg.fill_color_editor
        ed._on_expr.setText("Alarm_On")
        ed._off_col.pick(0x00CC00)
        assert getattr(dlg, field).pair() == (0x00CC00, 0x00CC00), kind
        assert dlg.apply(), (kind, dlg._status.text())
        po = doc.parsed.page.objects[i]
        cb = G.find_colour_block_exact(po)
        assert tuple(cb.slots[slot]) == (0x00CC00, 0x00CC00), kind
        assert _static(po) == (0x00CC00, 0x00CC00), kind
        assert doc.object_properties(i)["COL_A"] == "Alarm_On"
        dlg.close()


def test_multi_state_conditions_and_state_labels(fx_page):
    doc = _doc(fx_page)
    i = _rect(doc)
    dlg = _sheet(doc, i)
    ed = dlg.fill_color_editor
    ed._radios[F.MULTI_STATE].setChecked(True)
    assert len(ed.current().conditions) == 3 and ed._states.rowCount() == 8  # CtDraw's 3
    assert ed._states.item(1, 0).toolTip().startswith("A true, B false, C false")
    for _ in range(3):
        ed._add_condition()
    assert len(ed.current().conditions) == 5 and not ed._btn_add.isEnabled()
    assert ed._states.rowCount() == 32
    ed._conds.setCurrentCell(4, 1)
    ed._delete_condition()
    assert ed._states.rowCount() == 16
    ed._conds.setCurrentCell(1, 1)
    ed._cond_edit.setText("X" * 129)  # 128 characters at most
    assert ed._cond_edit.text() == "X" * 128
    fc = ed.current()
    fc.conditions[1] = "X" * 129  # (a longer stored one)
    assert "Condition B is too long" in F.problems(fc)[0][1]
    ed._cond_edit.setText("Valve_Open")
    ed._conds.setCurrentCell(0, 1)
    ed._cond_edit.setText("Valve_Closed")
    assert dlg.apply()
    got = _col(doc, i)
    assert (got["COL_A"], got["COL_B"]) == ("Valve_Closed", "Valve_Open")
    # blank conditions at the end are not stored: the reload shows two
    assert "COL_C" not in got and len([k for k in got if k.startswith("COL_STA_")]) == 4
    assert len(ed.current().conditions) == 2
    dlg.close()


def test_clear_property_removes_the_colour_animation(fx_page):
    doc = _doc(fx_page)
    i = _rect(doc)
    dlg = _sheet(doc, i)
    ed = dlg.fill_color_editor
    ed._radios[F.THRESHOLD].setChecked(True)
    ed._thr_expr.setText("Level")
    assert dlg.apply() and _col(doc, i)["COLMODE"] == "3"
    assert dlg.tab_marked("Fill", "Colour")
    ed._clear()
    assert ed.current().mode == F.ON_OFF
    assert dlg.apply()
    assert _col(doc, i) == {}
    _arr, L = _state(doc.parsed.page.objects[i])
    assert L["flag"] == 0x01
    assert not dlg.tab_marked("Fill", "Colour")
    assert _static(doc.parsed.page.objects[i]) == (0x336699, 0x336699)  # the fill stays
    dlg.close()


def test_thresholds_add_edit_delete(fx_page):
    doc = _doc(fx_page)
    i = _rect(doc)
    dlg = _sheet(doc, i)
    ed = dlg.fill_color_editor
    ed._radios[F.THRESHOLD].setChecked(True)
    ed._thr_expr.setText("Speed")
    assert ed._thr.rowCount() == 4  # the defaults
    ed._thr.setCurrentCell(1, 2)
    ed._add_threshold()
    assert ed._thr.rowCount() == 5 and ed._thr.currentRow() == 2
    ed._thr_val.setValue(50)
    ed._thr_cmp.setCurrentIndex(1)  # <=
    ed._thr_col.pick(0x808080)
    ed._thr.setCurrentCell(0, 2)
    ed._delete_threshold()
    assert dlg.apply()
    got = _col(doc, i)
    rows = [got["COL_THR_%d" % k] for k in range(4)]
    assert rows == [
        "<,15,0x00000080,0x00000080",
        "<=,50,0x00808080,0x00808080",
        ">,85,0x00800000,0x00800000",
        ">,95,0x00ff0000,0x00ff0000",
    ]
    assert "COL_THR_4" not in got
    _arr, L = _state(doc.parsed.page.objects[i])
    assert L["thr_n"] == 4
    dlg.close()


def test_fill_level_direction_background_and_defaults(fx_page):
    """Fill > Level: FILMODE is the direction radio (0 Up = no key, 1 Down,
    2 Left, 3 Right); a new level expression stores CtDraw's
    FILMINPC 0 / FILMAXPC 100 / FILCOL (transparent) with it; Clear Property
    removes every level key."""
    doc = _doc(fx_page)
    i = _rect(doc)
    dlg = _sheet(doc, i)
    lv = dlg.fill_level_editor
    assert lv.direction() == F.UP and lv._bg.is_transparent()
    fil = dlg._editors["FIL"]
    fil.setText("Tank_Level")
    fil.range_field.check.setChecked(True)
    fil.range_field.minimum.setText("0")
    fil.range_field.maximum.setText("5000")
    n = len(doc._undo)
    assert dlg.apply() and len(doc._undo) == n + 1
    assert _col(doc, i, ("FIL",)) == {
        "FIL": "(Tank_Level)^(0,5000)",
        "FILMINPC": "0",
        "FILMAXPC": "100",
        "FILCOL": "0xff000000,0xff000000",
    }
    assert dlg.tab_marked("Fill", "Level")
    lv._buttons[F.RIGHT].setChecked(True)
    lv._bg.pick(0x00FFFFFF)
    dlg._editors["FILMAXPC"].setValue(90)
    assert dlg.apply()
    got = _col(doc, i, ("FIL",))
    assert (got["FILMODE"], got["FILCOL"], got["FILMAXPC"]) == ("3", "0x00ffffff,0x00ffffff", "90")
    _arr, L = _state(doc.parsed.page.objects[i])
    assert L["flag"] & 0x40  # FILCOL in the runtime too
    lv._buttons[F.UP].setChecked(True)
    lv._bg.pick_transparent()
    assert dlg.apply()
    got = _col(doc, i, ("FIL",))
    # Transparent PICKED is the colour picker's value, citect.pal's Transparent
    # 0xffffffff (0xff000000 is the default CtDraw's apply writes for a new level, above)
    assert "FILMODE" not in got and got["FILCOL"] == "0xffffffff,0xffffffff"
    dlg2 = _sheet(doc, i)
    assert dlg2.fill_level_editor.direction() == F.UP
    dlg2.close()
    for d, name in F.DIRECTIONS:
        assert F.direction({"FILMODE": str(d)}) == d, name
    assert F.direction({"FILMODE": "7"}) == F.UP  # out of range reads Up
    # Clear Property
    btn = next(
        b
        for b in dlg.page_widget("fill_level").findChildren(QtWidgets.QPushButton)
        if b.text() == "Clear Property"
    )
    btn.click()
    assert dlg.apply()
    assert _col(doc, i, ("FIL",)) == {}
    dlg.close()


def test_register_rows_carry_colmode_and_the_keys(fx_page):
    """The saved page's pgdynobj row (what the compiler reads) carries
    COLMODE, the expression and the level keys."""
    from ctgedit.io import pgregister

    doc = _doc(fx_page)
    i = _rect(doc)
    dlg = _sheet(doc, i)
    ed = dlg.fill_color_editor
    ed._radios[F.MULTI_STATE].setChecked(True)
    ed._cond_edit.setText("Pump_Run")
    dlg._editors["FIL"].setText("Tank_Level")
    dlg.fill_level_editor._buttons[F.DOWN].setChecked(True)
    assert dlg.apply()
    dlg.close()
    po = doc.parsed.page.objects[i]
    an = str(struct.unpack_from("<H", po.body_raw, 2)[0] + 1)
    rows = pgregister.page_rows(doc.serialize(), "FxShapes")["pgdynobj"]
    row = next(r for r in rows if r["AN"] == an)
    assert (row["COLMODE"], row["COL_A"]) == ("1", "Pump_Run")
    assert (row["FIL"], row["FILMINPC"], row["FILMAXPC"], row["FILMODE"]) == ("Tank_Level", "0", "100", "1")


def test_record_without_own_colour_and_genie_member(fx_page):
    """A record without its own colour, and genie members."""
    doc = _doc(fx_page, "FxColours")
    objs = doc.parsed.page.objects
    (arr,) = doc.find_objects_by_name("FxArrGroup")
    assert objs[arr].type_code == 0x08 and objs[arr].class_flag
    assert doc.object_properties(arr).get("COLMODE") == "2"
    fc = doc.fill_colour(arr)
    assert fc.mode == F.ARRAY
    assert fc.array[1] == F.parse_pair(doc.object_properties(arr)["COL_ARR_1"])
    dlg = _sheet(doc, arr)
    ed = dlg.fill_color_editor
    ed._radios[F.ON_OFF].setChecked(True)
    ed._on_expr.setText("Group_Alarm")
    assert dlg.apply()
    got = _col(doc, arr)
    assert got["COL_A"] == "Group_Alarm" and "COLMODE" not in got
    assert not [k for k in got if k.startswith("COL_ARR")]
    assert doc.parsed.page.objects[arr].subtype_size & 0x10  # dynamic
    dlg.close()
    # a genie member without an own style block: no static colour; read-only sheet
    doc = _doc(fx_page, "FxGenies")
    objs = doc.parsed.page.objects
    member = next(
        k
        for k in range(len(objs))
        if doc.page.objects[k] is not None
        and not objs[k].subtype_size & 0x2
        and any(objs[x].subtype_size & 0x200 for x in doc.page.ancestors(k))
        and doc.page.objects[k].kind.value in ("rectangle", "ellipse", "polygon")
    )
    assert not doc.fill_colour(member).static_editable
    assert doc.fill_static_slot(objs[member]) is None
    dlg = _sheet(doc, member)
    ed = dlg.fill_color_editor
    assert dlg.is_read_only()
    assert not ed._radios[F.ON_OFF].isEnabled() and ed._on_expr.isReadOnly()
    assert not ed._off_col.swatch.isEnabled()
    dlg.close()
    # an editable sheet on such a record keeps the OFF colour read-only
    dlg = _sheet(doc, member, read_only=False)
    ed = dlg.fill_color_editor
    assert not ed._off_col.swatch.isEnabled() and ed._on_col.swatch.isEnabled()
    dlg.close()


def _genie_library(fx_install):
    """fxgenie.ctm of the fx_install copy, with FxMain's citect.pal beside it (the
    project palette of the element's project)."""
    shutil.copyfile(
        fx_install.file("FxMain", "citect.pal"), os.path.join(fx_install.project("FxLib"), "citect.pal")
    )
    return fx_install.file("FxLib", "fxgenie.ctm")


#: the genie element whose rectangle is a %TEMPLATE% member with no colour keys (it
#: declares %Description%)
GENIE = "fx_value"


def test_genie_element_member_fill_goes_to_the_template_bag(fx_install):
    """In a genie element the Fill page writes the member's %TEMPLATE% bag
    (CtDraw's genie editor), %params% allowed; it survives save + reopen."""
    from ctgedit.editing import ElementDocument

    lib = _genie_library(fx_install)
    d = ElementDocument.open_element(lib, GENIE)
    i = next(k for k, o in enumerate(d.objects) if o.type_code == 0x0F and d._template_record(k))
    dlg = _sheet(d, i)
    ed = dlg.fill_color_editor
    ed._radios[F.MULTI_STATE].setChecked(True)
    ed._cond_edit.setText("%Description% > 1")
    assert dlg.apply(), dlg._status.text()
    dlg.close()
    want = {"COLMODE": "1", "COL_A": "%Description% > 1"}
    got = d.object_properties(i)
    assert {k: got.get(k) for k in want} == want and "COL_STA_1" in got
    d.save()
    got = ElementDocument.open_element(lib, GENIE).object_properties(i)
    assert {k: got.get(k) for k in want} == want


def test_genie_element_member_fill_on_fx_pump_keeps_the_derive(fx_install):
    """The same fill apply on fxgenie.fx_pump's %TEMPLATE% rectangle (on %Tag%): the
    incremental re-derive must equal a full one (CTG_DERIVE_CHECK, on in this file)."""
    from ctgedit.editing import ElementDocument

    d = ElementDocument.open_element(_genie_library(fx_install), "fx_pump")
    i = next(k for k, o in enumerate(d.objects) if o.type_code == 0x0F and d._template_record(k))
    dlg = _sheet(d, i)
    try:
        dlg.fill_color_editor._radios[F.MULTI_STATE].setChecked(True)
        dlg.fill_color_editor._cond_edit.setText("%Tag% > 1")
        assert dlg.apply(), dlg._status.text()
    finally:
        dlg.close()


def test_genie_element_member_new_array_takes_the_palette_defaults(fx_install):
    """A genie element member switched to Array gets a NEW colour array whose
    entries without a key are the project palette's colours (what CtDraw's
    saver writes) -- at the edit and after save + reopen, through the genie
    element's own colour-state re-derive (genie_place.rebuild_colour_state)."""
    from ctgedit.editing import ElementDocument

    lib = _genie_library(fx_install)
    old = CS._palette
    CS.use_palette(None)  # nothing installed beforehand
    try:
        d = ElementDocument.open_element(lib, GENIE)
        i = next(k for k, o in enumerate(d.objects) if o.type_code == 0x0F and d._template_record(k))
        assert "arr_at" not in CS.layout(d.parsed.page.objects[i])
        dlg = _sheet(d, i)
        ed = dlg.fill_color_editor
        ed._radios[F.ARRAY].setChecked(True)
        ed._arr_expr.setText("%Description%")
        ed._array.setCurrentCell(5, 1)
        ed._array_col.pick(0x123456)
        assert dlg.apply(), dlg._status.text()
        dlg.close()
        pal = d.colour_palette()

        def entries(po):
            L = CS.layout(po)
            return [CS.pair_at(po.body_raw, L["arr_at"] + 8 * k) for k in range(256)]

        got = entries(d.parsed.page.objects[i])
        assert got[5] == (0x123456, 0x123456)
        assert [got[k] for k in (1, 6, 12, 100)] == [pal[k] for k in (1, 6, 12, 100)]
        d.save()
        again = ElementDocument.open_element(lib, GENIE)
        assert entries(again.parsed.page.objects[i])[1:] == got[1:]
    finally:
        CS.use_palette(old)


# ------------------------------------------------------------------ Properties panel
def test_properties_panel_labels_the_on_colour_phases(fx_page):
    """The panel shows COL_MAXON as the ON colour and its flash phase, not
    as 'ON' / 'OFF' (the OFF colour is the object's own fill colour)."""
    from ctgedit.ui.panels.properties import PropertyPanel

    doc = _doc(fx_page)
    i = _rect(doc)
    assert doc.set_fill_keys(i, [("COL_A", "Run"), ("COL_MAXON", "0x00ff0000,0x00ff0000")])
    _app()
    panel = PropertyPanel()
    panel.set_target(doc, [i])
    box = next(b for b in panel.widget().findChildren(QtWidgets.QGroupBox) if b.title() == "Fill colour")
    from ctgedit.ui.panels.properties_widgets import LabelledSwatch

    # the two phases are labelled colour swatches (ColourButtons)
    texts = {w.text() for w in box.findChildren(LabelledSwatch)} | {
        btn.text() for btn in box.findChildren(QtWidgets.QPushButton)
    }
    labels = {
        box.layout().itemAt(r, QtWidgets.QFormLayout.LabelRole).widget().text()
        for r in range(box.layout().rowCount())
        if box.layout().itemAt(r, QtWidgets.QFormLayout.LabelRole) is not None
    }
    assert {"Colour", "Flash to"} <= texts and not {"ON", "OFF"} & texts
    assert {"ON colour", "ON colour when", "Type"} <= labels
