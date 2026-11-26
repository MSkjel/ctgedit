"""Simulated alarm lists, array lists and priority flags."""

import os
import time

import pytest
from qtkit import app as _app

#: the columns of the two lists ([field, pixel width], as AlarmListCreate sends them)
ALARM_COLUMNS = [["PriorityandState", 24], ["OnTime", 90], ["Item", 100], ["Name", 400], ["State", 100]]
SOE_COLUMNS = [
    ["PriorityAndState", 24],
    ["Date", 120],
    ["Time", 130],
    ["Name", 400],
    ["Message", 600],
    ["State", 100],
]
#: fx_p2_small's colour (tools/make_fixtures.py PRIORITY_FLAGS: priority 2 is 0xFF8000)
P2_COLOUR = 0xFF8000


class _Alarms:
    """The page FxSimAlarms as the Simulate window draws it, and its list genies' ANs."""

    def __init__(self, fx):
        from ctgedit.ui.simulate.window import runtime_page

        _app()
        self.path = fx.page("FxSimAlarms", "FxSim")
        self.page = runtime_page(self.path)
        names = {}
        for i, o in enumerate(self.page.objects):
            n = (o.raw.get("_props") or {}).get("__AN_NAME__")
            if n:
                names[n] = i
        self.index = names
        self.an = {n: self.page.objects[i].an for n, i in names.items()}

    def scene(self):
        from ctgedit.render.scene import PageScene

        sc = PageScene()
        sc.set_page(self.page)
        sc.set_runtime_look(True)
        return sc

    def geometry(self, name):
        return self.page.objects[self.index[name]].geometry

    def states(self):
        """The pageState of the page with both lists filled: FxAlarmList1 an alarm summary
        (one active alarm, priority 2, unacknowledged), FxAlarmList2 a sequence of events."""
        from ctgedit.sim.state import page_state_from_json

        a1, a2 = self.an["FxAlarmList1"], self.an["FxAlarmList2"]
        row = {
            "row": 0,
            "text": "2,1  04:15:49 PM  AlmStatus  Fx valve status  ON",
            "fields": [
                ["PriorityandState", "2,1"],
                ["OnTime", "04:15:49 PM"],
                ["Item", "AlmStatus"],
                ["Name", "Fx valve status"],
                ["State", "ON"],
            ],
            "tag": "FX_DA_1",
            "priority": 2,
            "category": 2,
            "state": "unack_on",
            "font": "",
            "font_handle": 49,
            "colour": None,
            "background": None,
            "flash": True,
        }
        soe = {
            "row": 0,
            "text": "2,1  02-10-26  04:15:49 PM",
            "fields": [
                ["PriorityAndState", "2,1"],
                ["Date", "02-10-26"],
                ["Time", "04:15:49 PM"],
                ["Name", "Fx valve status"],
                ["Message", "Alarm ON: Fx valve status"],
                ["State", "ON"],
            ],
            "tag": "FX_DA_1",
            "priority": 2,
            "category": 2,
            "state": "on",
            "font": "",
            "font_handle": 49,
            "flash": False,
        }
        body = {
            "window": 0,
            "page": "FxSimAlarms",
            "serial": 4,
            "flashOn": True,
            "objects": [
                {
                    "an": a1,
                    "state": {
                        "alarm_list": {
                            "type": 0,
                            "count": 5,
                            "offset": 0,
                            "total": 1,
                            "array": True,
                            "no_draw": True,
                            "columns": ALARM_COLUMNS,
                            "rows": [row],
                        }
                    },
                },
                {
                    "an": a2,
                    "state": {
                        "alarm_list": {
                            "type": 15,
                            "count": 19,
                            "offset": 0,
                            "total": 1,
                            "array": True,
                            "no_draw": True,
                            "columns": SOE_COLUMNS,
                            "rows": [soe],
                        }
                    },
                },
            ],
        }
        return page_state_from_json(body, replace=True)


@pytest.fixture()
def alarms(fx_install):
    return _Alarms(fx_install)


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


# -- the wire format -----------------------------------------------------------------------
def test_alarm_list_and_row_are_parsed():
    from ctgedit.sim.state import object_state_from_json

    st = object_state_from_json(
        9,
        {
            "alarmList": {"type": 0, "array": True, "rows": [{"row": 0, "text": "x"}, "junk"]},
            "alarm_row": {"row": 1, "text": "y"},
        },
    )
    assert st.alarm_list["type"] == 0 and st.alarm_list["rows"] == [{"row": 0, "text": "x"}]
    assert st.alarm_row == {"row": 1, "text": "y"}
    assert object_state_from_json(9, {"alarm_list": "nope"}).alarm_list is None
    assert not st.is_default()


@pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")
def test_alarm_list_states_arrive_over_the_protocol(tmp_path, monkeypatch):
    """A pageChanged carrying an AlarmDsp list (fake server, tests/fake_ctsim.py) reaches the
    Simulate source as ObjectState.alarm_list on the list AN and alarm_row on each row AN."""
    import sys

    sys.path.insert(0, os.path.dirname(__file__))
    import fake_ctsim as F
    from ctgedit.sim import client as C
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.model.document import Geometry, GraphicObject, ObjectKind, Page

    monkeypatch.setenv(C.SERVERS_ENV, str(tmp_path / "servers"))
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)
    proj = tmp_path / "User" / "FxProj"
    proj.mkdir(parents=True)
    page_file = proj / "FxSim1.ctg"
    page_file.write_bytes(b"")
    page = Page(name="FxSim1")
    page.objects = [
        GraphicObject(kind=ObjectKind.RECTANGLE, an=11, geometry=Geometry(x=10, y=10, extent_x=8, extent_y=6))
    ]
    hub = C.Hub(linger=0)
    srv = F.FakeServer(str(proj), str(tmp_path / "servers"), pages=True).start()
    sim = EngineSimulation(hub=hub, fake=FakeSimulation())
    try:
        sim.start(str(page_file), page)
        end = time.monotonic() + 15.0
        while not sim.page_active() and time.monotonic() < end:
            time.sleep(0.02)
        assert sim.page_active()
        while sim.poll() is not None:
            pass
        rows = [
            {
                "row": 0,
                "text": "02-10-26 TAG1",
                "state": "unack_on",
                "flash": True,
                "fontInfo": {"face": "Arial", "height": 13, "fg": [0xFF0000, 0x0000FF]},
            },
            {"row": 1, "text": "02-10-26 TAG2", "state": "ack_on", "flash": False},
        ]
        srv.push_alarm_list(
            sim._window,
            21,
            {
                "type": 0,
                "count": 3,
                "offset": 0,
                "total": 2,
                "array": False,
                "no_draw": False,
                "columns": [],
                "rows": rows,
            },
            rows_on_ans=True,
        )
        got = {}
        end = time.monotonic() + 15.0
        while 22 not in got and time.monotonic() < end:
            st = sim.poll()
            if st is not None:
                got.update(st.objects)
            time.sleep(0.02)
        assert got[21].alarm_list["total"] == 2 and len(got[21].alarm_list["rows"]) == 2
        assert got[21].alarm_row["text"] == "02-10-26 TAG1"
        assert got[21].alarm_row["fontInfo"]["fg"] == [0xFF0000, 0x0000FF]
        assert got[22].alarm_row["state"] == "ack_on"
    finally:
        sim.stop()
        srv.stop()
        hub.shutdown()


# -- SA AlarmListCreate lists --------------------------------------------------------------
def test_sa_alarm_list_header_and_rows_where_the_runtime_draws_them(alarms):
    sc = alarms.scene()
    sc.apply_states(alarms.states().objects, replace=True)
    a1, a2 = alarms.an["FxAlarmList1"], alarms.an["FxAlarmList2"]
    lists = sc.sim_layer().alarm_lists()._lists
    assert set(lists) == {a1, a2}
    texts = {t.text(): t for t in _texts(lists[a1])}
    # PriorityandState has no title (AlarmPage.ci _ArrayView_ClearColumnTitle)
    assert {"On Time", "Item", "Name", "State", "AlmStatus", "ON"} <= set(texts)
    assert "PriorityandState" not in texts
    # columns side by side by their pixel widths (24, 90, 100, 400, 100) from the genie's x
    assert texts["Item"].x() == texts["On Time"].x() + 90
    assert texts["State"].x() == texts["Name"].x() + 400
    # the header row is the genie's top row, the first data row one row height (24) lower
    assert texts["AlmStatus"].y() - texts["Item"].y() == 24
    g = alarms.geometry("FxAlarmList1")
    box = lists[a1].box.rect()
    assert (box.left(), box.top()) == (g.x, g.y)
    assert box.width() == sum(w for _f, w in ALARM_COLUMNS) and box.height() == 2 * 24
    # the first column's text starts 2 px into its column (CELL_INSET), Qt's glyph 1 px on
    assert texts["On Time"].x() == g.x + 24 + 2 - 1
    soe = {t.text() for t in _texts(lists[a2])}
    assert {"Date", "Time", "Name", "Message", "State", "02-10-26"} <= soe
    # the header's glyph rows where the live capture of an SA list has them: 'On Time'
    # rows 7 .. 16 of the header row, from 2 px into its column (the genie's own frame
    # line at its top and left edge is left out)
    img = _grab(sc, (g.x, g.y, 120, 24))
    dark = [(x, y) for y in range(3, img.height()) for x in range(20, 90) if (img.pixel(x, y) & 0xFF) < 120]
    ys = sorted({y for _x, y in dark})
    assert ys[0] == 7 and ys[-1] == 16
    assert min(x for x, _y in dark) == 24 + 2


def test_an_array_output_of_a_list_is_not_drawn_twice(alarms):
    """The SA list genie calls DspArrayByAn(%AN%) on its own AlarmListCreate array: while the
    AN has an alarm list, the list draws it; when the list goes, the array output shows."""
    from ctgedit.render.sim_alarmlists import draw_array

    sc = alarms.scene()
    sc.apply_states(alarms.states().objects, replace=True)
    a1 = alarms.an["FxAlarmList1"]
    lay = sc.sim_layer()
    entry = {
        "an": a1,
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
    draw_array(lay, a1, entry)
    assert a1 not in lay.alarm_lists()._arrays and a1 in lay.alarm_lists()._lists
    sc.apply_states({a1: None})
    assert a1 not in lay.alarm_lists()._lists
    assert {t.text() for t in _texts(lay.alarm_lists()._arrays[a1])} == {"Item", "AlmStatus"}


def test_priority_and_state_cell_draws_the_priority_symbol(alarms):
    """The active alarm's PriorityandState cell ('2,1'): priority 2's thumbnail
    (almprior.DBF THUMBNAIL fxprio.fx_p2_small, an 8 x 8 rectangle of the priority's
    colour), centred in the first cell of the first data row."""
    sc = alarms.scene()
    sc.apply_states(alarms.states().objects, replace=True)
    a1 = alarms.an["FxAlarmList1"]
    g = alarms.geometry("FxAlarmList1")
    ov = sc.sim_layer().alarm_lists()._lists[a1]
    from PySide6 import QtWidgets

    icons = [
        it
        for it in ov.items
        if not isinstance(it, (QtWidgets.QGraphicsSimpleTextItem, QtWidgets.QGraphicsRectItem))
    ]
    assert len(icons) == 1  # one active alarm, priority 2 (fx_p2_small)
    r = icons[0].sceneBoundingRect()
    assert g.x <= r.left() and r.right() <= g.x + 24
    assert g.y + 24 <= r.top() and r.bottom() <= g.y + 48
    img = _grab(sc, (g.x, g.y + 24, 24, 24))
    want = ((P2_COLOUR >> 16) & 0xFF, (P2_COLOUR >> 8) & 0xFF, P2_COLOUR & 0xFF)
    near = sum(
        1
        for y in range(24)
        for x in range(24)
        if all(abs(((img.pixel(x, y) >> s) & 0xFF) - c) < 40 for s, c in zip((16, 8, 0), want))
    )
    assert near > 5  # the thumbnail's own colour


def test_alarm_lists_follow_clear_export_and_a_new_page(alarms):
    sc = alarms.scene()
    sc.apply_states(alarms.states().objects, replace=True)
    lay = sc.sim_layer()
    boxes = [ov.box for ov in lay.alarm_lists()._lists.values()]
    with sc.export_only(None):
        assert not any(b.isVisible() for b in boxes)
    assert all(b.isVisible() for b in boxes)
    sc.clear_states()
    assert lay.alarm_lists()._lists == {}
    assert all(b.scene() is None for b in boxes)
    sc.apply_states(alarms.states().objects, replace=True)
    sc.set_page(alarms.page)  # the scene rebuilds: the lists are drawn again
    assert set(lay.alarm_lists()._lists) == {alarms.an["FxAlarmList1"], alarms.an["FxAlarmList2"]}
    assert all(ov.box.scene() is sc for ov in lay.alarm_lists()._lists.values())


def test_a_row_flashes_with_its_font(alarms):
    """A flashing unack row's text takes its fontInfo fg half for the phase."""
    from ctgedit.sim.state import ObjectState

    sc = alarms.scene()
    a1 = alarms.an["FxAlarmList1"]
    g = alarms.geometry("FxAlarmList1")
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
    sc.apply_states({a1: ObjectState(a1, alarm_row=row)})
    ov = sc.sim_layer().alarm_lists()._lists[a1]
    from PySide6 import QtWidgets

    (t,) = _texts(ov)
    assert t.text() == "TAG1  ON" and t.brush().color().name() == "#ff0000"
    # (at the list genie's top-left: FxAlarmList1 is placed at 20, 20)
    assert t.font().pixelSize() == 13 and (t.pos().x(), t.pos().y()) == (g.x, g.y) == (20, 20)
    bg = [it for it in ov.items if isinstance(it, QtWidgets.QGraphicsRectItem)]
    assert bg and bg[0].brush().color().name() == "#ffff00"
    sc.set_flash_phase(False)
    ov = sc.sim_layer().alarm_lists()._lists[a1]
    (t,) = _texts(ov)
    assert t.brush().color().name() == "#0000ff"
    assert not [it for it in ov.items if isinstance(it, QtWidgets.QGraphicsRectItem)]


def test_a_row_without_a_record_lies_under_its_list(alarms):
    from ctgedit.sim.state import ObjectState

    sc = alarms.scene()
    lay = sc.sim_layer()
    a1 = alarms.an["FxAlarmList1"]
    row_an = a1 + 30993
    assert lay.index_of(row_an) is None
    g = alarms.page.objects[lay.index_of(a1)].geometry
    # a row AN with no record of its own: laid out under its list's first AN (AN - row),
    # one font height (10 px) per row
    row = {"row": 30993, "text": "ROW", "fontInfo": {"face": "Arial", "height": 10}}
    sc.apply_states({row_an: ObjectState(row_an, alarm_row=row)})
    (t,) = _texts(lay.alarm_lists()._lists[row_an])
    assert t.pos().x() == g.x and t.pos().y() == g.y + 30993 * 10


# -- array outputs (DspArrayByAn) ----------------------------------------------------------
def test_draw_array_lays_the_sa_cells_out(alarms):
    from ctgedit.render.sim_alarmlists import draw_array

    sc = alarms.scene()
    lay = sc.sim_layer()
    an = alarms.an["FxTreeList"]
    g = alarms.page.objects[lay.index_of(an)].geometry
    entry = {
        "an": an,
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
                ["", "1", "FX01", "Fx unit 1"],
                ["", "0", "FX02", "Fx unit 2"],
                ["", "0", "FX03", "Fx unit 3"],
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
    draw_array(lay, an, entry)
    ov = lay.alarm_lists()._arrays[an]
    texts = {t.text(): t for t in _texts(ov)}
    # the header from the titles, then every array row 1 .. Y - 1 (the array holds the rows
    # shown: firstRow is the scroll position they were filled from, GenericList.ci), a genie
    # column (type 5) left to the page's genies
    assert {"Equipment", "Full Name", "FX01", "FX02", "Fx unit 3"} <= set(texts)
    assert "0" not in texts and "1" not in texts
    assert texts["Full Name"].x() == texts["Equipment"].x() + 122
    assert texts["FX01"].y() - texts["Equipment"].y() == 24
    assert texts["FX03"].y() - texts["FX01"].y() == 48
    assert ov.box.rect().left() == g.x and ov.box.rect().top() == g.y
    # a hidden array stays hidden when the same entry comes again, and after an export
    hidden = dict(entry, cells=dict(entry["cells"], hidden=True))
    draw_array(lay, an, hidden)
    assert not lay.alarm_lists()._arrays[an].box.isVisible()
    draw_array(lay, an, hidden)
    lay.suspend()
    lay.resume()
    sc.apply_states({})
    assert not lay.alarm_lists()._arrays[an].box.isVisible()
    draw_array(lay, an, entry)
    ov = lay.alarm_lists()._arrays[an]
    assert ov.box.isVisible()
    draw_array(sc, an, None)
    assert an not in lay.alarm_lists()._arrays and ov.box.scene() is None


def test_draw_array_symbol_column_takes_its_state_from_meta(alarms):
    """The SA treeview's PriorityAndState column (type 3): the cell holds the priority, the
    column's meta the symbol state (engine cells.columns[].meta); an unack state flashes."""
    from PySide6 import QtWidgets
    from ctgedit.render.sim_alarmlists import draw_array

    sc = alarms.scene()
    lay = sc.sim_layer()
    an = alarms.an["FxTreeList"]
    entry = {
        "an": an,
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
            "data": [["", "PriorityAndState", "Name"], ["", "2", "FxCluster"], ["", "", "FxSim"]],
            "columns": [
                {"col": 1, "field": "PriorityAndState", "type": 3, "width": 24, "title": ""},
                {"col": 2, "field": "Name", "type": 1, "width": 120, "title": "Name"},
            ],
            "serial": 1,
        },
    }
    draw_array(
        lay,
        an,
        dict(
            entry,
            cells=dict(
                entry["cells"],
                columns=[dict(entry["cells"]["columns"][0], meta=["6", ""]), entry["cells"]["columns"][1]],
            ),
        ),
    )
    ov = lay.alarm_lists()._arrays[an]
    icons = [
        it
        for it in ov.items
        if not isinstance(it, (QtWidgets.QGraphicsSimpleTextItem, QtWidgets.QGraphicsRectItem))
    ]
    assert len(icons) == 1 and ov.flashing  # row 1: priority 2, state 6 (unack)
    draw_array(lay, an, entry)  # no meta: state 0, steady
    ov = lay.alarm_lists()._arrays[an]
    assert not ov.flashing


def test_draw_array_plain_array(alarms):
    from ctgedit.render.sim_alarmlists import draw_array

    sc = alarms.scene()
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
def _flag_an(alarms):
    """The AN of the indicator group of FxAlarmFlag (fxparts.fx_alarm: its group's
    indicator is on, with its flag)."""
    page = alarms.page
    for j in page.subtree(alarms.index["FxAlarmFlag"]):
        o = page.objects[j]
        ai = o.raw.get("_alarm_indicator") or {}
        if ai.get("flag_on", True) and "outer" in ai and o.an > 0:
            return o.an
    raise AssertionError("FxAlarmFlag shows no flagged alarm indicator")


def _flag_states(an, prio):
    from ctgedit.sim.state import ObjectState

    return {an: ObjectState(an, alarm=dict(prio, flash=False))}


def _flag_parts(sc, index):
    paint = sc.sim_layer()._ai_paint[index][0]
    texts = [it for it, _c, k in paint.parts if k == "text"]
    shapes = [it for it, _c, k in paint.parts if k == "shape"]
    assert texts and shapes
    return texts[0], shapes, paint


def test_priority_flag_shows_its_short_name_and_state_colour(alarms):
    """An alarm's flag is its priority's flag genie in the alarm's state: the text whose
    TXT_STR reads the Label shows the priority's SHORTNAME (Include's almprior.DBF: P3),
    the COL_ARR records take the state's pair (fx_p3: unacknowledged on 0xFFFF00 / white,
    acknowledged on the steady 0xFFFF00)."""
    sc = alarms.scene()
    lay = sc.sim_layer()
    an = _flag_an(alarms)
    sc.apply_states(_flag_states(an, {"priority": 3, "state": "unack_on", "colour": 0xFFFF00}))
    i = lay.index_of(an)
    text, shapes, _p = _flag_parts(sc, i)
    assert text.text() == "P3"  # almprior SHORTNAME of priority 3
    assert any(s.brush().color().name() == "#ffff00" for s in shapes)
    sc.set_flash_phase(False)
    assert any(s.brush().color().name() == "#ffffff" for s in shapes)
    sc.set_flash_phase(True)
    # acknowledged: the steady pair (COL_ARR_3)
    sc.apply_states(_flag_states(an, {"priority": 3, "state": "ack_on", "colour": 0xFFFF00}))
    sc.set_flash_phase(False)
    assert any(s.brush().color().name() == "#ffff00" for s in shapes)


def test_top_priority_flag_is_painted_too_and_restored(alarms):
    """The top priority flag is painted too, and restored."""
    sc = alarms.scene()
    lay = sc.sim_layer()
    an = _flag_an(alarms)
    i = lay.index_of(an)
    sc.apply_states(_flag_states(an, {"priority": 1, "state": "unack_off", "colour": 0xFF0000}))
    text, shapes, _paint = _flag_parts(sc, i)
    painted = [it.brush().color().name() for it in shapes]
    assert text.text() == "P1"
    assert "#ffffff" in painted and "#ff0000" not in painted
    sc.set_flash_phase(False)
    assert any(s.brush().color().name() == "#ff0000" for s in shapes)
    sc.set_flash_phase(True)
    sc.clear_states()
    assert i not in lay._ai_paint
    after = [it.brush().color().name() for it in shapes]
    assert after != painted and "#ff0000" in after  # fx_p1's own colours again
    built = lay._ai_flags.get((i, 1))
    from ctgedit.render.scene import AI_PART_ROLE

    root = sc._ai_items[i]  # (hidden while no alarm is on)
    assert built is not None and not built.isVisibleTo(root)
    placeholder = [it for it in root.childItems() if it.data(AI_PART_ROLE) == "flag" and it is not built]
    assert placeholder and all(it.isVisibleTo(root) for it in placeholder)  # the editor's
    assert text.text() != "P1"


def test_own_project_top_flag_is_painted_in_place(fx_install):
    """The own project's top flag is painted in place."""
    import shutil
    from ctgedit.editing import libraries_changed

    inc, sim = fx_install.project("Include"), fx_install.project("FxSim")
    for name in ("almprior.DBF", "almprior.NDX"):
        shutil.copy(os.path.join(inc, name), os.path.join(sim, name))
    libraries_changed()
    alarms = _Alarms(fx_install)
    sc = alarms.scene()
    lay = sc.sim_layer()
    an = _flag_an(alarms)
    i = lay.index_of(an)
    ai = alarms.page.objects[i].raw["_alarm_indicator"]
    assert not isinstance(ai["flag"], tuple) and ai["flag"].children  # fx_p1, no placeholder
    sc.apply_states(_flag_states(an, {"priority": 1, "state": "unack_off", "colour": 0xFF0000}))
    text, shapes, paint = _flag_parts(sc, i)
    root = sc._ai_items[i]
    assert text.text() == "P1" and (i, 1) not in lay._ai_flags  # painted in place
    assert paint.item.isVisibleTo(root)
    sc.apply_states(_flag_states(an, {"priority": 3, "state": "unack_on", "colour": 0xFFFF00}))
    built = lay._ai_flags.get((i, 3))
    assert built is not None and built.isVisibleTo(root) and not paint.item.isVisibleTo(root)
    sc.clear_states()
    assert paint.item.isVisibleTo(root) and not built.isVisibleTo(root)


# -- runtime-look pens ---------------------------------------------------------------------
def test_scaled_pens_keep_their_width(alarms):
    """A filled rectangle scaled 13.23 x 1.5 by its state (an SA alarm row bar is; here
    FxAlarmList1's scroll bar, 12 x 30 at 304, 0 in the genie) is drawn at its scaled size
    with a 1 px pen, not a scaled pen; without the state it has its own size again."""
    from PySide6 import QtWidgets
    from ctgedit.model.document import ObjectKind
    from ctgedit.sim.state import page_state_from_json

    sc = alarms.scene()
    _lay = sc.sim_layer()
    root = alarms.index["FxAlarmList1"]
    bar = next(
        j
        for j in alarms.page.subtree(root)
        if alarms.page.objects[j].kind is ObjectKind.RECTANGLE and alarms.page.objects[j].style.filled
    )
    o = alarms.page.objects[bar]
    g = o.geometry
    item = sc._items[bar]

    def rect():
        return item.path().boundingRect() if isinstance(item, QtWidgets.QGraphicsPathItem) else item.rect()

    r0 = rect()
    body = {
        "window": 0,
        "page": "FxSimAlarms",
        "objects": [
            {
                "an": o.an,
                "state": {"scale": [13.23, 1.5], "scale_origin": [g.x, g.y], "fill_colour": 16711422},
            }
        ],
    }
    sc.apply_states(page_state_from_json(body, replace=True).objects, replace=True)
    assert item.transform().isIdentity()
    assert item.pen().widthF() == 1.0
    r = rect()
    # (the bar sits at 324, 20 on the page: 12 x 30 scaled is 158 x 45 whole pixels)
    assert (r.left(), r.top()) == (r0.left(), r0.top()) == (g.x + 0.5, g.y + 0.5)
    assert (r.width(), r.height()) == (158.0, 45.0) == (int(g.extent_x * 13.23), g.extent_y * 1.5)
    sc.clear_states()
    r2 = rect()
    assert (r2.width(), r2.height()) == (r0.width(), r0.height())  # its static size again
