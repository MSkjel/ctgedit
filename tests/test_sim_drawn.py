"""What the page's Cicode drew."""

import os
import sys
import types

import pytest
from qtkit import app as _app

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


W, H = 400, 300


def _page(objects=()):
    from ctgedit.model.document import Page

    p = Page(name="sim_drawn")
    p.appearance.width, p.appearance.height = W, H
    p.appearance.background_colour = 0xFFFFFF
    p.objects = list(objects)
    return p


def _scene(page=None):
    _app()
    from ctgedit.render.scene import PageScene

    sc = PageScene()
    sc.set_page(page if page is not None else _page())
    sc.set_runtime_look(True)
    return sc


def _text(an=5, x=100, y=50, text="Hello", **args):
    e = {
        "an": an,
        "kind": "text",
        "x": x,
        "y": y,
        "text": text,
        "font": 55,
        "args": dict({"length": -1, "align": 0, "trunc": 0}, **args),
        "serial": 1,
    }
    return e


def _text_item(item):
    from PySide6 import QtWidgets
    from ctgedit.render.sim_drawn import _walk

    return next(it for it in _walk(item) if isinstance(it, QtWidgets.QGraphicsSimpleTextItem))


def _render(sc, w=W, h=H):
    from PySide6 import QtCore, QtGui

    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor(255, 255, 255))
    p = QtGui.QPainter(img)
    sc.render(p, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(0, 0, w, h))
    p.end()
    return img


def _colours(img, rect):
    l, t, r, b = rect
    out = set()
    for y in range(t, b):
        for x in range(l, r):
            c = img.pixelColor(x, y)
            out.add((c.red(), c.green(), c.blue()))
    return out


# -- the wire format -------------------------------------------------------------------------
def test_page_state_keeps_the_drawn_list_by_an():
    from ctgedit.sim.state import drawn_from_json, page_state_from_json

    body = {
        "window": 0,
        "page": "Alarm",
        "objects": [],
        "drawn": [
            _text(an=7),
            {"an": 8, "kind": "sym", "x": 1, "y": 2, "text": "a.b"},
            {"an": "x", "kind": "text"},
            {"an": 9},
            "junk",
            _text(an=7, text="again"),
        ],
    }
    st = page_state_from_json(body)
    assert list(st.drawn) == [7, 8]
    assert st.drawn[7]["text"] == "again"  # the last entry of an AN wins
    assert page_state_from_json({"window": 0, "objects": []}).drawn == {}
    assert drawn_from_json(None) == {}
    from ctgedit.sim.state import PageState

    assert PageState(window=0, page="p").drawn is None  # the demo: no news


# -- text --------------------------------------------------------------------------------------
@pytest.mark.parametrize("align", [0, 1, 2])
def test_text_aligns_on_x_and_sits_on_its_baseline(align):
    from PySide6 import QtGui
    from ctgedit.render.sim_drawn import font_of

    sc = _scene()
    sc.sim_layer().apply_drawn({5: _text(align=align)})
    it = sc.sim_layer().drawn_layer().items()[5]
    t = _text_item(it)
    fm = QtGui.QFontMetricsF(font_of(None))
    w = fm.horizontalAdvance("Hello")
    left = t.scenePos().x()
    assert left == pytest.approx({0: 100, 1: 100 - w, 2: 100 - w / 2}[align])
    assert t.scenePos().y() == pytest.approx(50 - fm.ascent())


def test_text_in_a_pixel_field_aligns_in_the_field_and_is_cut():
    from PySide6 import QtGui, QtWidgets
    from ctgedit.render.sim_drawn import font_of

    sc = _scene()
    fm = QtGui.QFontMetricsF(font_of(None))
    long = "A rather long caption that does not fit"
    sc.sim_layer().apply_drawn(
        {
            5: _text(text="Hi", length=80, align=1),
            6: _text(an=6, y=90, text=long, length=60, trunc=0),
            7: _text(an=7, y=130, text=long, length=60, trunc=1),
        }
    )
    items = sc.sim_layer().drawn_layer().items()
    hi = _text_item(items[5])
    assert hi.scenePos().x() == pytest.approx(100 + 80 - fm.horizontalAdvance("Hi"))
    # mode 0: the whole text, clipped to the field
    cut = items[6]
    assert _text_item(cut).text() == long
    assert cut.flags() & QtWidgets.QGraphicsItem.ItemClipsChildrenToShape
    assert cut.rect().width() == pytest.approx(60)
    # mode 1: three full stops inside the field (GDI's DT_END_ELLIPSIS)
    el = _text_item(items[7]).text()
    assert el.endswith("...") and len(el) > 3 and fm.horizontalAdvance(el) <= 60
    assert long.startswith(el[:-3])


def test_no_text_draws_nothing_and_clears_the_old_one():
    sc = _scene()
    layer = sc.sim_layer()
    layer.apply_drawn({5: _text()})
    assert 5 in layer.drawn_layer().items()
    e = _text()
    del e["text"]
    layer.apply_drawn({5: e})
    assert 5 not in layer.drawn_layer().items()
    layer.apply_drawn({5: _text(text="")})
    assert 5 not in layer.drawn_layer().items()


def test_font_info_and_flashing_colours():
    sc = _scene()
    e = _text(x=50, y=80, text="WWWWWW")
    e["fontInfo"] = {
        "face": "Arial",
        "height": 30,
        "pt": 22,
        "bold": True,
        "italic": False,
        "underline": False,
        "strikeout": False,
        "fg": [0xFF0000, 0x0000FF],
        "bg": [0xFFFF00, -1],
        "flash": True,
    }
    layer = sc.sim_layer()
    layer.apply_drawn({5: e})
    t = _text_item(layer.drawn_layer().items()[5])
    assert t.font().pixelSize() == 30 and t.font().bold()
    box = (50, 60, 150, 84)
    on = _colours(_render(sc), box)
    assert (255, 0, 0) in on and (255, 255, 0) in on
    sc.set_flash_phase(False)
    off = _colours(_render(sc), box)
    assert (0, 0, 255) in off and (255, 0, 0) not in off and (255, 255, 0) not in off
    sc.set_flash_phase(True)
    assert (255, 0, 0) in _colours(_render(sc), box)


def test_steady_font_keeps_its_on_colour_in_the_off_phase():
    sc = _scene()
    e = _text(x=50, y=80, text="WWWWWW")
    e["fontInfo"] = {"face": "Arial", "height": 30, "fg": [0xFF0000, 0x0000FF], "flash": False}
    sc.sim_layer().apply_drawn({5: e})
    sc.set_flash_phase(False)
    assert (255, 0, 0) in _colours(_render(sc), (50, 60, 150, 84))


def test_text_drawn_in_the_off_phase_starts_in_its_off_colour():
    # a drawn layer made while the page is in the off phase draws a new flashing text off
    sc = _scene()
    sc.set_flash_phase(False)
    e = _text(x=50, y=80, text="WWWWWW")
    e["fontInfo"] = {"face": "Arial", "height": 30, "fg": [0xFF0000, 0x0000FF], "flash": True}
    sc.sim_layer().apply_drawn({5: e})
    off = _colours(_render(sc), (50, 60, 150, 84))
    assert (0, 0, 255) in off and (255, 0, 0) not in off


def test_an_array_with_a_serial_is_not_rebuilt_for_unchanged_cells():
    from ctgedit.render.sim_drawn import DrawnLayer

    data = [["r%d c%d" % (r, c) for c in range(64)] for r in range(500)]
    e = {
        "an": 9,
        "kind": "array",
        "x": 0,
        "y": 0,
        "args": {"array": 3},
        "cells": {"rows": 500, "cols": 64, "data": data, "firstRow": 0, "serial": 4},
    }
    sig = DrawnLayer._sig(e)
    assert "r499" not in sig and len(sig) < 400
    e2 = dict(e, cells=dict(e["cells"], serial=5))
    assert DrawnLayer._sig(e2) != sig
    e3 = dict(e, cells=dict(e["cells"], firstRow=10))
    assert DrawnLayer._sig(e3) != sig


# -- the set: replace, keep, remove, stacking, export ------------------------------------------
def test_the_list_replaces_keeps_unchanged_items_and_removes_gone_ones():
    from ctgedit.render.sim_drawn import Z_DRAWN

    sc = _scene()
    layer = sc.sim_layer()
    r = layer.apply_drawn({5: _text(), 6: _text(an=6, y=90)})
    assert r == {"drawn": 2, "changed": 2, "removed": 0}
    first = layer.drawn_layer().items()[5]
    again = dict(_text(), serial=99)  # the same output: a new serial only
    r = layer.apply_drawn({5: again})
    assert r == {"drawn": 1, "changed": 0, "removed": 1}
    items = layer.drawn_layer().items()
    assert items[5] is first and 6 not in items
    assert first.scene() is sc and first.zValue() >= Z_DRAWN
    r = layer.apply_drawn({5: _text(text="Changed")})
    assert r["changed"] == 1 and layer.drawn_layer().items()[5] is not first
    assert first.scene() is None
    layer.apply_drawn({})
    assert not layer.drawn_layer().items()
    assert not [it for it in sc.items() if it.zValue() >= Z_DRAWN]


def test_drawn_items_are_above_every_page_item_and_take_no_clicks():
    from ctgedit.render.scene import PAGE_INDEX_ROLE
    from ctgedit.render.sim_drawn import Z_DRAWN, _walk

    sc = _scene()
    sc.sim_layer().apply_drawn({31999: _text(an=31999), 5: _text(y=90)})
    tops = [i for i in sc.items() if i.zValue() < Z_DRAWN]
    items = sc.sim_layer().drawn_layer().items()
    assert all(it.zValue() > max([t.zValue() for t in tops] or [0]) for it in items.values())
    # the first-drawn AN lies below the later one (the order the ANs were first drawn)
    assert items[31999].zValue() < items[5].zValue()
    for it in items.values():
        assert all(p.data(PAGE_INDEX_ROLE) is None for p in _walk(it))


def test_an_export_draws_the_static_page():
    sc = _scene()
    sc.sim_layer().apply_drawn({5: _text(x=50, y=80, text="WWWWWW")})
    it = sc.sim_layer().drawn_layer().items()[5]
    with sc.export_only(None):
        assert not it.isVisible()
    assert it.isVisible()


def test_clear_states_clears_the_drawn_output():
    sc = _scene()
    sc.sim_layer().apply_drawn({5: _text()})
    sc.clear_states()
    assert not sc.sim_layer().drawn_layer().items()


def test_a_scene_rebuild_draws_the_list_again():
    sc = _scene()
    sc.sim_layer().apply_drawn({5: _text()})
    sc.set_page(_page())
    it = sc.sim_layer().drawn_layer().items()[5]
    assert it.scene() is sc and _text_item(it).text() == "Hello"


def test_clip_cuts_any_kind():
    from PySide6 import QtWidgets

    sc = _scene()
    e = _text(text="WWWWWWWWWW", clip="100,30,130,60")
    sc.sim_layer().apply_drawn({5: e})
    it = sc.sim_layer().drawn_layer().items()[5]
    assert it.flags() & QtWidgets.QGraphicsItem.ItemClipsChildrenToShape
    assert it.rect().getRect() == (100, 30, 30, 30)


def test_button_and_boxes():
    sc = _scene()
    sc.sim_layer().apply_drawn(
        {
            5: {
                "an": 5,
                "kind": "button",
                "x": 20,
                "y": 20,
                "text": "Start",
                "args": {"width": 0, "height": 0},
            },
            6: {
                "an": 6,
                "kind": "button",
                "x": 20,
                "y": 60,
                "text": "Stop",
                "args": {"width": 90, "height": 30},
            },
            7: {"an": 7, "kind": "richText", "x": 200, "y": 20, "args": {"width": 80, "height": 40}},
            8: {
                "an": 8,
                "kind": "file",
                "x": 200,
                "y": 100,
                "text": "c:\\x.txt",
                "font": 3,
                "args": {"width": 10, "height": 2},
            },
            9: {"an": 9, "kind": "bar", "x": 300, "y": 200, "text": "bars.b", "args": {"value": 16000}},
            10: {"an": 10, "kind": "web", "x": 0, "y": 0, "text": "http://x"},
            11: {"an": 11, "kind": "trend", "x": 0, "y": 0, "text": "t", "args": {"values": "1,2"}},
        }
    )
    items = sc.sim_layer().drawn_layer().items()
    assert set(items) == {5, 6, 7, 9}  # (a file display without a file shows nothing)
    stop = items[6].childrenBoundingRect()
    assert stop.width() >= 90 and stop.height() >= 30
    assert _text_item(items[5]).text() == "Start"


def test_a_file_display_draws_each_line_on_its_an():
    sc = _scene()
    lines = [{"an": 20, "x": 10, "y": 40, "text": "alpha"}, {"an": 22, "x": 10, "y": 80, "text": "three"}]
    entry = {"an": 20, "kind": "file", "x": 10, "y": 40, "text": "log.txt", "args": {"lines": lines}}
    sc.sim_layer().apply_drawn({20: entry})
    from PySide6 import QtWidgets
    from ctgedit.render.sim_drawn import _walk

    item = sc.sim_layer().drawn_layer().items()[20]
    texts = sorted(
        (it for it in _walk(item) if isinstance(it, QtWidgets.QGraphicsSimpleTextItem)),
        key=lambda t: t.pos().y(),
    )
    assert [t.text() for t in texts] == ["alpha", "three"]
    # each line sits on its own AN's baseline
    assert [t.pos().x() for t in texts] == [10, 10]
    assert texts[1].pos().y() - texts[0].pos().y() == 40 and texts[0].pos().y() < 40


_RTF = (
    r"{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0\fswiss Arial;}{\f1\fmodern Courier New;}}"
    r"{\colortbl ;\red0\green0\blue0;\red255\green0\blue0;}"
    r"{\stylesheet{\s0 Normal;}}{\info{\title Secret}}{\*\generator Msftedit;}" + "\r\n"
    r"\pard\qc\cf2\b\fs36 Title\b0\par" + "\r\n"
    r"\pard\cf1\fs20 one\tab two \i it\i0  \ul under\ulnone\par"
    r"\qr\f1 caf\'e9 \{x\} a\\b\par"
    r"\pard\par last}"
)


def test_rtf_reads_the_report_subset_as_one_paragraph_per_par():
    from ctgedit.render.rtf_html import rtf_paragraphs, rtf_to_html

    assert rtf_paragraphs(_RTF) == ["Title", "one\ttwo it under", "caf\u00e9 {x} a\\b", "", "last"]
    html = rtf_to_html(_RTF)
    assert html.count("<p ") == 5
    for hidden in ("Normal", "Secret", "Msftedit", "Arial;", "red255"):
        assert hidden not in html
    paras = html.split("<p ")[1:]
    assert "text-align:center" in paras[0] and "color:#ff0000" in paras[0]
    assert "font-weight:bold" in paras[0] and "font-size:24.00px" in paras[0]  # 18 pt
    assert "font-family:'Arial'" in paras[0]
    assert 'font-style:italic;color:#000000">it<' in paras[1]
    assert 'text-decoration:underline;color:#000000">under<' in paras[1]
    assert "color:#000000" in paras[1] and "font-size:13.33px" in paras[1]
    assert "text-align:right" in paras[2] and "font-family:'Courier New'" in paras[2]
    assert "{x} a\\b" in paras[2]
    assert "text-align:left" in paras[3] and "> </span>" in paras[3]  # an empty paragraph


def _rich(an=7, x=20, y=20, w=200, h=60, **args):
    return {
        "an": an,
        "kind": "richText",
        "x": x,
        "y": y,
        "text": "rep.rtf",
        "args": dict({"width": w, "height": h, "row": 0, "col": 0, "rtf": _RTF}, **args),
        "serial": 1,
    }


def _rich_text(item):
    from PySide6 import QtWidgets
    from ctgedit.render.sim_drawn import _walk

    return next(it for it in _walk(item) if isinstance(it, QtWidgets.QGraphicsTextItem))


def _red(img, rect):
    return any(r > 200 and g < 90 and b < 90 for r, g, b in _colours(img, rect))


def test_a_rich_text_box_shows_its_rtf_clipped_to_the_box():
    from PySide6 import QtWidgets

    sc = _scene()
    sc.sim_layer().apply_drawn({7: _rich()})
    box = sc.sim_layer().drawn_layer().items()[7]
    assert box.flags() & QtWidgets.QGraphicsItem.ItemClipsChildrenToShape
    assert box.rect().getRect() == (20, 20, 200, 60)
    t = _rich_text(box)
    assert t.document().blockCount() == 5
    assert t.document().findBlockByNumber(2).text() == "caf\u00e9 {x} a\\b"
    assert (t.pos().x(), t.pos().y()) == (20, 20)
    img = _render(sc)
    assert _red(img, (20, 20, 220, 80))  # the red title
    assert not _red(img, (0, 81, W, H))  # nothing below the box


def test_a_rich_text_box_scrolls_to_its_row_and_column():
    sc = _scene()
    sc.sim_layer().apply_drawn({7: _rich(row=2, col=2)})
    t = _rich_text(sc.sim_layer().drawn_layer().items()[7])
    doc = t.document()
    top = doc.documentLayout().blockBoundingRect(doc.findBlockByNumber(2)).top()
    assert top > 0
    assert (t.pos().x(), t.pos().y()) == (20 - 16, 20 - top)
    assert not _red(_render(sc), (0, 0, W, H))  # the title is scrolled out of the box


def test_a_rich_text_box_without_rtf_is_an_empty_box():
    sc = _scene()
    sc.sim_layer().apply_drawn({7: _rich(rtf="")})
    box = sc.sim_layer().drawn_layer().items()[7]
    assert box.rect().getRect() == (20, 20, 200, 60)
    from PySide6 import QtWidgets
    from ctgedit.render.sim_drawn import _walk

    assert not [it for it in _walk(box) if isinstance(it, QtWidgets.QGraphicsTextItem)]


def test_a_plot_draws_its_primitives_relative_to_its_an():
    from PySide6 import QtCore, QtWidgets
    from ctgedit.render.sim_drawn import _walk

    pen = {"style": 0, "colour": 0x000000, "width": 1}
    prims = [
        {"op": "rect", "x1": 0, "y1": 0, "x2": 100, "y2": 50, "pen": dict(pen, style=5), "fill": 0xFFFF00},
        {"op": "line", "x1": 0, "y1": 25, "x2": 100, "y2": 25, "pen": dict(pen, colour=0x0000FF)},
        {"op": "rect", "x1": 100, "y1": 50, "x2": 0, "y2": 0, "pen": pen, "fill": -1},
        {"op": "polyline", "points": [0, 50, 50, 0, 100, 10], "pen": dict(pen, colour=0x008000, width=3)},
        {"op": "ellipse", "x1": 110, "y1": 0, "x2": 130, "y2": 20, "pen": pen, "fill": 0xFF0000},
        {"op": "point", "x": 5, "y": 5, "pen": pen},
        {"op": "marker", "x": 50, "y": 0, "marker": 6, "colour": 0xFF0000, "size": 8},
        {
            "op": "marker",
            "x": 0,
            "y": 50,
            "marker": 20,
            "colour": 0xFF0000,
            "size": 8,
            "symbol": "nolib.none",
        },
        {
            "op": "text",
            "x": 0,
            "y": 70,
            "text": "Flow",
            "font": 1,
            "orient": 1,
            "fontInfo": {"face": "Arial", "height": 12, "fg": [0x000080, 0x000080], "bg": [-1, -1]},
        },
    ]
    entry = {
        "an": 30,
        "kind": "plot",
        "x": 50,
        "y": 60,
        "args": {"mode": 1, "output": "Display", "prims": prims},
    }
    sc = _scene()
    sc.sim_layer().apply_drawn({30: entry})
    item = sc.sim_layer().drawn_layer().items()[30]
    parts = item.childItems()
    assert len(parts) == len(prims)
    # the shapes in page coordinates: the AN at (50, 60)
    assert parts[0].rect().getRect() == (50, 60, 100, 50)
    assert parts[0].brush().color().name() == "#ffff00" and parts[0].pen().style() == QtCore.Qt.NoPen
    assert (parts[1].line().y1(), parts[1].pen().color().name()) == (85, "#0000ff")
    assert parts[2].rect().getRect() == (50, 60, 100, 50)  # corners in any order
    assert parts[2].brush().style() == QtCore.Qt.NoBrush
    path = parts[3].path()
    assert [(path.elementAt(k).x, path.elementAt(k).y) for k in range(path.elementCount())] == [
        (50, 110),
        (100, 60),
        (150, 70),
    ]
    assert parts[3].pen().width() == 3
    assert isinstance(parts[4], QtWidgets.QGraphicsEllipseItem)
    # a filled square marker centred on its point; an unknown symbol marker as a dashed square
    assert parts[6].path().boundingRect().getRect() == (96, 56, 8, 8)
    assert parts[6].brush().color().name() == "#ff0000"
    assert parts[7].pen().style() == QtCore.Qt.DashLine
    # the text turned upwards with its baseline's start on (x, y)
    texts = [it for it in _walk(parts[8]) if isinstance(it, QtWidgets.QGraphicsSimpleTextItem)]
    assert [t.text() for t in texts] == ["Flow"]
    assert (parts[8].pos().x(), parts[8].pos().y(), parts[8].rotation()) == (50, 130, -90)
    assert texts[0].pos().y() < 0 and texts[0].brush().color().name() == "#000080"
    img = _render(sc)
    assert (255, 255, 0) in _colours(img, (55, 65, 60, 70))  # the frame's fill


def test_array_entries_go_to_the_alarm_list_hook(monkeypatch):
    from ctgedit import render

    entry = {"an": 403, "kind": "array", "x": 5, "y": 181, "args": {"array": 4}}
    seen = []

    def draw_array(layer, an, e):
        seen.append((layer, an, e))

    mod = types.ModuleType("ctgedit.render.sim_alarmlists")
    mod.draw_array = draw_array
    monkeypatch.setitem(sys.modules, "ctgedit.render.sim_alarmlists", mod)
    monkeypatch.setattr(render, "sim_alarmlists", mod, raising=False)
    sc = _scene()
    sc.sim_layer().apply_drawn({403: entry})
    # the alarm-list layer draws the grid into its own items: (sim layer, AN, entry)
    assert seen == [(sc.sim_layer(), 403, entry)]
    assert 403 not in sc.sim_layer().drawn_layer().items()
    # the entry goes: the grid is removed
    sc.sim_layer().apply_drawn({})
    assert seen[-1] == (sc.sim_layer(), 403, None)


# -- symbols on the fake install's pages -------------------------------------------------------
def _fx(fx_page, page, project):
    _app()
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    page = reader.read_page(fx_page(page, project))
    sc = PageScene()
    sc.set_page(page)
    sc.set_runtime_look(True)
    return sc


#: an alarm page's drawn list as the engine sends it: the header row's
#: captions (font 55) with their values (font 56), a symbol, a priority flag and an array
ALARM_HEADERS = ("Time", "Tag", "Name", "Description", "What's New?", "Acknowledge page")


def _alarm_list():
    out = []
    serial = 0
    for k, cap in enumerate(ALARM_HEADERS):
        serial += 1
        out.append(
            {
                "an": 31990 - 2 * k,
                "kind": "text",
                "x": 20 + 150 * k,
                "y": 145,
                "text": cap,
                "font": 55,
                "args": {"length": 140, "align": 0, "trunc": 0},
                "serial": serial,
            }
        )
        serial += 1
        out.append(
            {
                "an": 31989 - 2 * k,
                "kind": "text",
                "x": 20 + 150 * k,
                "y": 165,
                "font": 56,
                "args": {"length": 92, "align": 0, "trunc": 0},
                "serial": serial,
            }
        )
    out.append(
        {
            "an": 303,
            "kind": "sym",
            "x": 900,
            "y": 40,
            "text": "fxicons16.page",
            "args": {"mode": 1, "type": 0},
            "serial": serial + 1,
        }
    )
    out.append(
        {
            "an": 304,
            "kind": "sym",
            "x": 900,
            "y": 80,
            "text": "1",
            "args": {"mode": 1, "type": 2},
            "serial": serial + 2,
        }
    )
    return out


def test_the_engines_wire_format_parses():
    """A drawn list in the engine's format: texts with and without a caption, symbols by
    name and by alarm priority; every AN kept once."""
    from ctgedit.sim.state import page_state_from_json

    body = {"page": "FxSimAlarms", "window": 0, "objects": [], "drawn": _alarm_list()}
    st = page_state_from_json(body)
    kinds = {e["kind"] for e in st.drawn.values()}
    assert len(st.drawn) == 2 * len(ALARM_HEADERS) + 2 and kinds == {"text", "sym"}
    assert st.drawn[31990]["text"] == "Time" and "text" not in st.drawn[31989]


def test_symbols_place_their_hot_spot_on_the_an_at_their_own_size(fx_page):
    """Include's fxicons16.default: a 16 x 16 bitmap whose hot spot is its bottom-left
    corner, (0, 16): the picture's bottom edge lands on y. Include's cicode.default (a 31 x
    17 bitmap, hot spot (0, 16): its origin one row above its picture's bottom edge) lands
    its bottom on y + 1."""
    sc = _fx(fx_page, "FxSimAlarms", "FxSim")
    layer = sc.sim_layer()
    sym = {
        "an": 31998,
        "kind": "sym",
        "x": 1150,
        "y": 148,
        "text": "fxicons16.default",
        "args": {"mode": 1, "type": 0},
    }
    half = dict(
        sym, an=31997, args={"posX": 10, "posY": 0, "sizeX": 16000, "sizeY": 16000, "mode": 1, "type": 0}
    )
    zero = dict(sym, an=31996, args={"posX": 0, "posY": 0, "sizeX": 0, "sizeY": 32000})
    gone = dict(sym, an=31995, text="nolib.noelement")
    one = dict(sym, an=31994, text="cicode.default")
    layer.apply_drawn({31998: sym, 31997: half, 31996: zero, 31995: gone, 31994: one})
    dl = layer.drawn_layer()
    items = dl.items()
    r = items[31998].sceneBoundingRect()
    # the element's origin is its bottom-left
    assert (round(r.left()), round(r.bottom())) == (1150, 148)
    assert round(r.width()) == 16 and round(r.height()) == 16
    c = items[31994].sceneBoundingRect()
    assert (round(c.left()), round(c.bottom())) == (1150, 149)
    assert round(c.width()) == 31 and round(c.height()) == 17
    h = items[31997].sceneBoundingRect()
    assert round(h.width()) == 8 and round(h.left()) == 1160
    assert 31996 not in items and 31995 not in items
    assert "nolib.noelement" in dl.missing_symbols


def test_animated_symbols_cycle_their_frames(fx_page):
    sc = _fx(fx_page, "FxSimAlarms", "FxSim")
    dl = sc.sim_layer().drawn_layer()
    sc.sim_layer().apply_drawn(
        {
            7: {
                "an": 7,
                "kind": "symAnm",
                "x": 100,
                "y": 100,
                "text": "fxicons16.default",
                "args": {"symbols": "fxicons16.default,fxicons16.page", "mode": 0},
            }
        }
    )
    b = dl._built[7]
    assert len(b.frames) == 2 and b.frames[0].isVisible() and not b.frames[1].isVisible()
    assert dl._timer is not None and dl._timer.isActive()
    dl.next_frame()
    assert b.frames[1].isVisible() and not b.frames[0].isVisible()
    sc.sim_layer().apply_drawn({})
    assert not dl._timer.isActive()


def test_alarm_priority_symbols_by_number(fx_page):
    """Include's almprior.DBF: priorities 1 to 3 with fxprio's flags and thumbnails."""
    sc = _fx(fx_page, "FxSimAlarms", "FxSim")
    dl = sc.sim_layer().drawn_layer()
    assert dl._symbol_name("1", 2) == "fxprio.fx_p1"
    assert dl._symbol_name("1", 3) == "fxprio.fx_p1_small"
    assert dl._symbol_name("3", 2) == "fxprio.fx_p3"
    assert dl._symbol_name("9", 2) == ""


def test_alarm_mode_symbols_by_name(fx_page):
    """An alarm mode's symbol by its display name (almmodes.DBF DISPNAME)."""
    from ctgedit.format import dbf

    with dbf.Dbf(
        os.path.join(
            os.path.dirname(os.path.dirname(fx_page("FxSimAlarms", "FxSim"))), "Include", "almmodes.DBF"
        ),
        writable=False,
    ) as t:
        names = [n for n, _o, _w in t.fields]
    if "DISPNAME" not in names:
        pytest.skip(
            "needs fixture: a DISPNAME column in Include's almmodes.DBF (shared "
            "request: 'Fx Shelved' -> fxprio.fx_shelved, 'Fx Disabled' -> "
            "fxprio.fx_disabled)"
        )
    sc = _fx(fx_page, "FxSimAlarms", "FxSim")
    dl = sc.sim_layer().drawn_layer()
    assert dl._symbol_name("Fx Disabled", 4) == "fxprio.fx_disabled"
    assert dl._symbol_name("fx shelved", 4) == "fxprio.fx_shelved"
    assert dl._symbol_name("Nothing", 4) == ""


def test_the_alarm_page_draws_its_cicode_output(fx_page):
    sc = _fx(fx_page, "FxSimAlarms", "FxSim")
    from ctgedit.sim.state import page_state_from_json

    st = page_state_from_json({"page": "FxSimAlarms", "window": 0, "objects": [], "drawn": _alarm_list()})
    r = sc.sim_layer().apply_drawn(st.drawn)
    dl = sc.sim_layer().drawn_layer()
    items = dl.items()
    assert r["drawn"] == len(st.drawn) and not dl.missing_symbols
    texts = {_text_item(it).text() for an, it in items.items() if st.drawn[an]["kind"] == "text"}
    assert set(ALARM_HEADERS) <= texts
    # the header row's captions sit on their baseline at y (145)
    head = next(it for an, it in items.items() if st.drawn[an].get("text") == "Time")
    hb = head.sceneBoundingRect()
    assert 128 <= hb.top() and hb.bottom() <= 152
    # the priority flag: fxprio.fx_p1, a 20 x 12 rectangle drawn inclusive of its right and
    # bottom edge
    flag = items[304].sceneBoundingRect()
    assert (round(flag.width()), round(flag.height())) == (21, 13)


def test_drawn_output_hides_the_cicode_object_symbol(fx_page):
    """A Cicode object's symbol (FxCicode's FxCicodePage: fxicons16.page) is not drawn in
    the runtime look (the runtime draws a Cicode object only through its Cicode output: tc
    0x1009 is never baked into the .ctF and the runtime reads no SYMBOL key), with or without
    drawn output; an export draws the static page."""
    sc = _fx(fx_page, "FxCicode", "FxMain")
    layer = sc.sim_layer()
    page = sc._page
    i = next(k for k, o in enumerate(page.objects) if o.raw.get("_name") == "FxCicodePage")
    an = page.objects[i].an
    assert layer.index_of(an) == i
    item = sc._items[i]
    assert page.objects[i].raw.get("_type_code") == 0x1009 and not item.isVisible()
    from ctgedit.sim.state import page_state_from_json

    st = page_state_from_json(
        {
            "page": "FxCicode",
            "window": 0,
            "objects": [],
            "drawn": [
                {
                    "an": an,
                    "kind": "text",
                    "x": 200,
                    "y": 27,
                    "text": "Acknowledge Page",
                    "font": 93,
                    "args": {"length": -1, "align": 2, "trunc": 0},
                    "serial": 1,
                }
            ],
        }
    )
    layer.apply_drawn(st.drawn)
    assert not item.isVisible()
    assert not layer.shows(i)
    from ctgedit.sim.state import ObjectState

    layer.apply({an: ObjectState(an, tooltip="tip")})  # a state of the record: still hidden
    assert not item.isVisible()
    layer.apply({an: None})
    assert not item.isVisible()
    with sc.export_only(None):
        assert item.isVisible()  # an export draws the static page
    assert not item.isVisible()
    layer.apply_drawn({})
    assert not item.isVisible()


# -- the way from the engine to the tab --------------------------------------------------------
def test_a_tab_draws_the_drawn_list_and_the_demo_clears_it():
    _app()
    from ctgedit.sim.state import PageState
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.ui.simulate.window import SimulatedPage

    tab = SimulatedPage("", page=_page(), sim=FakeSimulation())
    try:
        dl = tab.scene.sim_layer().drawn_layer()
        tab.apply(PageState(window=0, page="sim_drawn", drawn={5: _text()}))
        assert 5 in dl.items()
        tab.apply(PageState(window=0, page="sim_drawn"))  # no news: kept
        assert 5 in dl.items()
        tab.apply(PageState(window=0, page="sim_drawn", replace=True))  # the demo again
        assert not dl.items()
    finally:
        tab.sim.stop()
        tab.deleteLater()


@pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")
def test_the_engine_source_hands_the_drawn_list_over(tmp_path, monkeypatch):
    import fake_ctsim as F
    from ctgedit.sim import client as C
    from test_sim_engine_source import _rect_page, _sim, wait_for

    folder = str(tmp_path / "servers")
    monkeypatch.setenv(C.SERVERS_ENV, folder)
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)
    proj = tmp_path / "User" / "Proj"
    proj.mkdir(parents=True)
    page_file = str(proj / "FxSim1.ctg")
    open(page_file, "wb").close()
    hub = C.Hub(linger=0)
    srv = F.FakeServer(str(proj), folder, pages=True).start()
    srv.drawn = {0: [_text(an=21, text="before")]}
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        st = sim.poll()
        assert st is not None and st.drawn is not None and st.drawn[21]["text"] == "before"
        srv.set_drawn(sim._window, [_text(an=21, text="after"), _text(an=22, y=80)])
        got = {}

        def took():
            s = sim.poll()
            if s is not None and s.drawn is not None:
                got.update(s.drawn)
            return got.get(21, {}).get("text") == "after"

        assert wait_for(took)
        assert set(got) == {21, 22}
        srv.set_drawn(sim._window, [])
        got.clear()

        def emptied():
            s = sim.poll()
            return s is not None and s.drawn == {}

        assert wait_for(emptied)
    finally:
        sim.stop()
        srv.stop()
        hub.shutdown()
