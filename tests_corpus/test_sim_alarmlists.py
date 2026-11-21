"""Simulated alarm lists on the real corpus."""

import json
import os

import pytest

R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"
DPS = os.path.join(R2, "ExampleSA", "DPS_AlarmSOE_HD1080.ctg")
OVERVIEW = os.path.join(R2, "ExampleSA", "Overview_L2.ctg")
DEFAULT_ALARM = os.path.join(R2, "ExampleSA", "DefaultAlarm_HD1080.ctg")
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sim", "dps_alarmsoe_states.json")

pytestmark = pytest.mark.skipif(not os.path.exists(DPS), reason="ExampleSA (R2) not installed")


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


_PAGES = {}


def _scene(path):
    _app()
    from ctgedit.render.scene import PageScene
    from ctgedit.ui.simulate.window import runtime_page

    if path not in _PAGES:
        _PAGES[path] = runtime_page(path)
    sc = PageScene()
    sc.set_page(_PAGES[path])
    sc.set_runtime_look(True)
    return sc


def _dps_states():
    from ctgedit.sim.state import page_state_from_json

    if not os.path.isfile(FIXTURE):
        pytest.skip(
            "%s is not here: local data recorded from AVEVA's example projects, not in the repository"
            % os.path.basename(FIXTURE)
        )
    with open(FIXTURE, encoding="utf8") as f:
        return page_state_from_json(json.load(f), replace=True)


def _grab(sc, rect):
    from PySide6 import QtCore, QtGui

    r = QtCore.QRectF(*rect)
    img = QtGui.QImage(int(r.width()), int(r.height()), QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor(255, 255, 255))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setRenderHint(QtGui.QPainter.TextAntialiasing)
    sc.render(p, QtCore.QRectF(0, 0, r.width(), r.height()), r)
    p.end()
    return img


def _texts(ov):
    from PySide6 import QtWidgets

    return [t for t in ov.items if isinstance(t, QtWidgets.QGraphicsSimpleTextItem)]


# -- SA AlarmListCreate lists --------------------------------------------------------------
def test_sa_alarm_list_header_and_rows_where_the_runtime_draws_them():
    sc = _scene(DPS)
    sc.apply_states(_dps_states().objects, replace=True)
    lists = sc.sim_layer().alarm_lists()._lists
    assert set(lists) == {9, 72}
    texts = {t.text(): t for t in _texts(lists[9])}
    # PriorityandState has no title (AlarmPage.ci _ArrayView_ClearColumnTitle)
    assert {"On Time", "Item", "Name", "State", "AlmStatus", "ON"} <= set(texts)
    assert "PriorityandState" not in texts
    # columns side by side by their pixel widths (24, 90, 100, 400, 100) from the genie's x
    assert texts["Item"].x() == texts["On Time"].x() + 90
    assert texts["State"].x() == texts["Name"].x() + 400
    # the header row is the genie's top row, the first data row one row height (24) lower
    assert texts["AlmStatus"].y() - texts["Item"].y() == 24
    soe = {t.text() for t in _texts(lists[72])}
    assert {"Date", "Time", "Name", "Message", "State", "02-10-26"} <= soe
    # glyph rows of the header pinned to the live capture: 'On Time' rows 47..56 from x 26
    img = _grab(sc, (0, 40, 120, 26))
    dark = [(x, y) for y in range(img.height()) for x in range(20, 90) if (img.pixel(x, y) & 0xFF) < 120]
    ys = sorted({y for _x, y in dark})
    assert ys[0] + 40 == 47 and ys[-1] + 40 == 56
    assert min(x for x, _y in dark) == 26


def test_an_array_output_of_a_list_is_not_drawn_twice():
    """The SA list genie calls DspArrayByAn(%AN%) on its own AlarmListCreate array: while the
    AN has an alarm list, the list draws it; when the list goes, the array output shows."""
    from ctgedit.render.sim_alarmlists import draw_array

    sc = _scene(DPS)
    sc.apply_states(_dps_states().objects, replace=True)
    lay = sc.sim_layer()
    entry = {
        "an": 9,
        "kind": "array",
        "cells": {
            "rows": 2,
            "cols": 2,
            "layers": 16,
            "rowHeight": 24,
            "drawHeader": True,
            "data": [["", "Item"], ["", "AlmStatus"]],
            "columns": [{"col": 1, "field": "Item", "type": 1, "width": 100, "title": "Item"}],
        },
    }
    draw_array(lay, 9, entry)
    assert 9 not in lay.alarm_lists()._arrays and 9 in lay.alarm_lists()._lists
    sc.apply_states({9: None})
    assert 9 not in lay.alarm_lists()._lists
    assert {t.text() for t in _texts(lay.alarm_lists()._arrays[9])} == {"Item", "AlmStatus"}


def test_priority_and_state_cell_draws_the_priority_symbol():
    sc = _scene(DPS)
    sc.apply_states(_dps_states().objects, replace=True)
    ov = sc.sim_layer().alarm_lists()._lists[9]
    from PySide6 import QtWidgets

    icons = [
        it
        for it in ov.items
        if not isinstance(it, (QtWidgets.QGraphicsSimpleTextItem, QtWidgets.QGraphicsRectItem))
    ]
    assert len(icons) == 1  # one active alarm, priority 2 (sa_p2_small)
    r = icons[0].sceneBoundingRect()
    assert 0 <= r.left() and r.right() <= 24 and 64 <= r.top() and r.bottom() <= 88
    img = _grab(sc, (0, 64, 24, 24))
    yellow = sum(
        1
        for y in range(24)
        for x in range(24)
        if (img.pixel(x, y) >> 16 & 0xFF) > 200
        and (img.pixel(x, y) >> 8 & 0xFF) > 200
        and (img.pixel(x, y) & 0xFF) < 80
    )
    assert yellow > 5  # the unack-on state colour of priority 2


def test_alarm_lists_follow_clear_export_and_a_new_page():
    sc = _scene(DPS)
    sc.apply_states(_dps_states().objects, replace=True)
    lay = sc.sim_layer()
    boxes = [ov.box for ov in lay.alarm_lists()._lists.values()]
    with sc.export_only(None):
        assert not any(b.isVisible() for b in boxes)
    assert all(b.isVisible() for b in boxes)
    sc.clear_states()
    assert lay.alarm_lists()._lists == {}
    assert all(b.scene() is None for b in boxes)
    sc.apply_states(_dps_states().objects, replace=True)
    sc.set_page(_PAGES[DPS])  # the scene rebuilds: the lists are drawn again
    assert set(lay.alarm_lists()._lists) == {9, 72}
    assert all(ov.box.scene() is sc for ov in lay.alarm_lists()._lists.values())


def test_a_row_flashes_with_its_font():
    """A flashing unack row's text takes its fontInfo fg half for the phase."""
    _app()
    from ctgedit.sim.state import ObjectState

    sc = _scene(DPS)
    row = {
        "row": 0,
        "text": "TAG1  ON",
        "flash": True,
        "state": "unack_on",
        "fontInfo": {
            "face": "Arial",
            "height": 13,
            "fg": [0xFF0000, 0x0000FF],
            "bg": [0xFFFF00, -1],
            "flash": True,
        },
    }
    sc.apply_states({9: ObjectState(9, alarm_row=row)})
    ov = sc.sim_layer().alarm_lists()._lists[9]
    from PySide6 import QtWidgets

    (t,) = _texts(ov)
    assert t.text() == "TAG1  ON" and t.brush().color().name() == "#ff0000"
    assert t.font().pixelSize() == 13 and t.pos().x() == 0 and t.pos().y() == 40
    bg = [it for it in ov.items if isinstance(it, QtWidgets.QGraphicsRectItem)]
    assert bg and bg[0].brush().color().name() == "#ffff00"
    sc.set_flash_phase(False)
    ov = sc.sim_layer().alarm_lists()._lists[9]
    (t,) = _texts(ov)
    assert t.brush().color().name() == "#0000ff"
    assert not [it for it in ov.items if isinstance(it, QtWidgets.QGraphicsRectItem)]


def test_a_row_without_a_record_lies_under_its_list():
    _app()
    from ctgedit.sim.state import ObjectState

    sc = _scene(DPS)
    lay = sc.sim_layer()
    assert lay.index_of(31002) is None
    page = _PAGES[DPS]
    g = page.objects[lay.index_of(9)].geometry
    # a row AN with no record of its own: laid out under its list's first AN (AN - row = 9),
    # one font height (10 px) per row
    row = {"row": 30993, "text": "ROW", "fontInfo": {"face": "Arial", "height": 10}}
    sc.apply_states({31002: ObjectState(31002, alarm_row=row)})
    (t,) = _texts(lay.alarm_lists()._lists[31002])
    assert t.pos().x() == g.x and t.pos().y() == g.y + 30993 * 10


# -- array outputs (DspArrayByAn) ----------------------------------------------------------
def test_draw_array_lays_the_sa_cells_out():
    _app()
    from ctgedit.render.sim_alarmlists import draw_array

    sc = _scene(DEFAULT_ALARM)
    lay = sc.sim_layer()
    g = _PAGES[DEFAULT_ALARM].objects[lay.index_of(310)].geometry
    entry = {
        "an": 310,
        "kind": "array",
        "x": 6,
        "y": 709,
        "args": {"array": 3},
        "cells": {
            "rows": 4,
            "cols": 4,
            "layers": 16,
            "firstRow": 1,
            "rowHeight": 24,
            "drawHeader": True,
            "xOff": 0,
            "data": [
                ["", "Checked", "Equipment", "FullName"],
                ["", "1", "AG01", "Agitator 1"],
                ["", "0", "AG02", "Agitator 2"],
                ["", "0", "AG03", "Agitator 3"],
            ],
            "columns": [
                {"col": 1, "field": "Checked", "type": 5, "width": 24, "title": " "},
                {"col": 2, "field": "Equipment", "type": 1, "width": 122, "title": "Equipment"},
                {"col": 3, "field": "FullName", "type": 1, "width": 118, "title": "Full Name"},
            ],
            "rowInfo": [{"row": 1, "selected": False}],
            "serial": 7,
        },
    }
    draw_array(lay, 310, entry)
    ov = lay.alarm_lists()._arrays[310]
    texts = {t.text(): t for t in _texts(ov)}
    # the header from the titles, then every array row 1 .. Y - 1 (the array holds the rows
    # shown: firstRow is the scroll position they were filled from, GenericList.ci), a genie
    # column (type 5) left to the page's genies
    assert {"Equipment", "Full Name", "AG01", "AG02", "Agitator 3"} <= set(texts)
    assert "0" not in texts and "1" not in texts
    assert texts["Full Name"].x() == texts["Equipment"].x() + 122
    assert texts["AG01"].y() - texts["Equipment"].y() == 24
    assert texts["AG03"].y() - texts["AG01"].y() == 48
    assert ov.box.rect().left() == g.x and ov.box.rect().top() == g.y
    # a hidden array stays hidden when the same entry comes again, and after an export
    hidden = dict(entry, cells=dict(entry["cells"], hidden=True))
    draw_array(lay, 310, hidden)
    assert not lay.alarm_lists()._arrays[310].box.isVisible()
    draw_array(lay, 310, hidden)
    lay.suspend()
    lay.resume()
    sc.apply_states({})
    assert not lay.alarm_lists()._arrays[310].box.isVisible()
    draw_array(lay, 310, entry)
    ov = lay.alarm_lists()._arrays[310]
    assert ov.box.isVisible()
    draw_array(sc, 310, None)
    assert 310 not in lay.alarm_lists()._arrays and ov.box.scene() is None


def test_draw_array_symbol_column_takes_its_state_from_meta():
    """The SA treeview's PriorityAndState column (type 3): the cell holds the priority, the
    column's meta the symbol state (engine cells.columns[].meta); an unack state flashes."""
    _app()
    from PySide6 import QtWidgets
    from ctgedit.render.sim_alarmlists import draw_array

    sc = _scene(DEFAULT_ALARM)
    lay = sc.sim_layer()
    entry = {
        "an": 310,
        "kind": "array",
        "x": 6,
        "y": 709,
        "args": {"array": 3},
        "cells": {
            "rows": 3,
            "cols": 3,
            "layers": 16,
            "firstRow": 0,
            "rowHeight": 24,
            "drawHeader": False,
            "xOff": 0,
            "data": [["", "PriorityAndState", "Name"], ["", "2", "Cluster1"], ["", "", "Company"]],
            "columns": [
                {"col": 1, "field": "PriorityAndState", "type": 3, "width": 24, "title": ""},
                {"col": 2, "field": "Name", "type": 1, "width": 120, "title": "Name"},
            ],
            "serial": 1,
        },
    }
    draw_array(
        lay,
        310,
        dict(
            entry,
            cells=dict(
                entry["cells"],
                columns=[dict(entry["cells"]["columns"][0], meta=["6", ""]), entry["cells"]["columns"][1]],
            ),
        ),
    )
    ov = lay.alarm_lists()._arrays[310]
    icons = [
        it
        for it in ov.items
        if not isinstance(it, (QtWidgets.QGraphicsSimpleTextItem, QtWidgets.QGraphicsRectItem))
    ]
    assert len(icons) == 1 and ov.flashing  # row 1: priority 2, state 6 (unack)
    draw_array(lay, 310, entry)  # no meta: state 0, steady
    ov = lay.alarm_lists()._arrays[310]
    assert not ov.flashing


def test_draw_array_plain_array():
    _app()
    from ctgedit.render.sim_alarmlists import draw_array

    sc = _scene(DPS)
    draw_array(
        sc,
        30500,
        {
            "an": 30500,
            "kind": "array",
            "x": 100,
            "y": 200,
            "cells": {"rows": 2, "cols": 2, "data": [["a", "b"], ["c", "d"]]},
        },
    )
    ov = sc.sim_layer().alarm_lists()._arrays[30500]
    assert sorted(t.text() for t in _texts(ov)) == ["a", "b", "c", "d"]
    assert ov.box.rect().left() == 100 and ov.box.rect().top() == 200


# -- priority flags of the alarm indicator --------------------------------------------------
def _flag_an(sc):
    """The AN of Overview_L2's first alarm indicator that shows a flag (AG01's)."""
    page = sc._page
    for o in page.objects:
        ai = o.raw.get("_alarm_indicator") or {}
        if ai.get("flag_on", True) and "outer" in ai and o.an > 0:
            return o.an
    pytest.skip("no flagged alarm indicator")


def _flag_states(an, prio):
    from ctgedit.sim.state import ObjectState

    return {an: ObjectState(an, alarm=dict(prio, flash=False))}


def _flag_parts(sc, index):
    lay = sc.sim_layer()
    paint = lay._ai_paint[index][0]
    texts = [it for it, _c, k in paint.parts if k == "text"]
    shapes = [it for it, _c, k in paint.parts if k == "shape"]
    assert texts and shapes
    return texts[0], shapes, paint


def test_priority_flag_shows_its_short_name_and_state_colour():
    if not os.path.exists(OVERVIEW):
        pytest.skip("Overview_L2 not installed")
    sc = _scene(OVERVIEW)
    lay = sc.sim_layer()
    an = _flag_an(sc)
    sc.apply_states(_flag_states(an, {"priority": 3, "state": "unack_on", "colour": 0x00FFFF}))
    i = lay.index_of(an)
    text, shapes, _p = _flag_parts(sc, i)
    assert text.text() == "L"  # almprior SHORTNAME of priority 3
    # sa_p3_normal COL_ARR_1 = 0x0000ffff,0x00ffffff: cyan in the on phase, white off
    assert any(s.brush().color().name() == "#00ffff" for s in shapes)
    sc.set_flash_phase(False)
    assert any(s.brush().color().name() == "#ffffff" for s in shapes)
    sc.set_flash_phase(True)
    # acknowledged: the steady pair (COL_ARR_3)
    sc.apply_states(_flag_states(an, {"priority": 3, "state": "ack_on", "colour": 0x00FFFF}))
    sc.set_flash_phase(False)
    assert any(s.brush().color().name() == "#00ffff" for s in shapes)


def test_top_priority_flag_is_painted_too_and_restored():
    if not os.path.exists(OVERVIEW):
        pytest.skip("Overview_L2 not installed")
    sc = _scene(OVERVIEW)
    lay = sc.sim_layer()
    an = _flag_an(sc)
    i = lay.index_of(an)
    sc.apply_states(_flag_states(an, {"priority": 1, "state": "unack_on", "colour": 0xFF0000}))
    text, shapes, paint = _flag_parts(sc, i)
    # (the top priority's flag is the scene's own static flag item, painted in place)
    static = [(it.brush().color().name(), it.pen().color().name()) for it in shapes]
    assert text.text() == "E"
    assert any(s.brush().color().name() == "#ff0000" for s in shapes)
    assert text.brush().color().name() == "#ffffff"  # sa_p1_normal's label COL_ARR_1 on half
    sc.clear_states()
    assert i not in lay._ai_paint
    after = [(it.brush().color().name(), it.pen().color().name()) for it in shapes]
    assert after != static and "#ff0000" not in [b for b, _p in after]
    assert text.brush().color().name() != "#ffffff"  # the editor's static flag again


# -- runtime-look pens ---------------------------------------------------------------------
def test_scaled_pens_keep_their_width():
    from PySide6 import QtWidgets

    sc = _scene(DPS)
    sc.apply_states(_dps_states().objects, replace=True)
    lay = sc.sim_layer()
    row = sc._items[lay.index_of(13)]  # an alarm row bar scaled 13.23 x 1.5
    assert row.transform().isIdentity()
    assert row.pen().widthF() == 1.0
    r = row.path().boundingRect() if isinstance(row, QtWidgets.QGraphicsPathItem) else row.rect()
    assert (r.left(), r.top(), r.width(), r.height()) == (0.5, 64.5, 1323.0, 24.0)
    sc.clear_states()
    r2 = row.path().boundingRect() if isinstance(row, QtWidgets.QGraphicsPathItem) else row.rect()
    assert r2.width() < 200  # its static size again
