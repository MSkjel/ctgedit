"""What the page's Cicode drew, on the real corpus."""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"
EX_ALARM = os.path.join(R2, "Example", "Alarm.ctg")
SA_ALARM = os.path.join(R2, "ExampleSA", "DefaultAlarm_HD1080.ctg")
DATA = os.path.join(HERE, "data", "sim")

W, H = 400, 300


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _fixture(name):
    path = os.path.join(DATA, name)
    if not os.path.isfile(path):
        pytest.skip(
            "%s is not here: local data recorded from AVEVA's example projects, not in the repository" % name
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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


# -- the wire format of the engine's lists -------------------------------------------------
def test_real_engine_lists_parse():
    from ctgedit.sim.state import page_state_from_json

    st = page_state_from_json(_fixture("drawn_example_alarm.json"))
    kinds = {e["kind"] for e in st.drawn.values()}
    assert len(st.drawn) == 206 and kinds == {"text", "sym"}


# -- symbols on real pages (read only) ---------------------------------------------------------
def _real(path):
    if not os.path.exists(path):
        pytest.skip("R2 page not installed")
    _app()
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    page = reader.read_page(path)
    sc = PageScene()
    sc.set_page(page)
    sc.set_runtime_look(True)
    return sc


def test_symbols_place_their_hot_spot_on_the_an_at_their_own_size():
    sc = _real(EX_ALARM)
    layer = sc.sim_layer()
    sym = {
        "an": 31998,
        "kind": "sym",
        "x": 1150,
        "y": 148,
        "text": "Icons_16x16.default",
        "args": {"mode": 1, "type": 0},
    }
    half = dict(
        sym, an=31997, args={"posX": 10, "posY": 0, "sizeX": 16000, "sizeY": 16000, "mode": 1, "type": 0}
    )
    zero = dict(sym, an=31996, args={"posX": 0, "posY": 0, "sizeX": 0, "sizeY": 32000})
    gone = dict(sym, an=31995, text="nolib.noelement")
    layer.apply_drawn({31998: sym, 31997: half, 31996: zero, 31995: gone})
    dl = layer.drawn_layer()
    items = dl.items()
    r = items[31998].sceneBoundingRect()
    # Icons_16x16's origin is the element's bottom-left (e (97, 118), N 97,103..113,119)
    assert (round(r.left()), round(r.bottom())) == (1150, 149)
    assert round(r.width()) == 16 and round(r.height()) == 16
    h = items[31997].sceneBoundingRect()
    assert round(h.width()) == 8 and round(h.left()) == 1160
    assert 31996 not in items and 31995 not in items
    assert "nolib.noelement" in dl.missing_symbols


def test_animated_symbols_cycle_their_frames():
    sc = _real(EX_ALARM)
    dl = sc.sim_layer().drawn_layer()
    sc.sim_layer().apply_drawn(
        {
            7: {
                "an": 7,
                "kind": "symAnm",
                "x": 100,
                "y": 100,
                "text": "Icons_16x16.default",
                "args": {"symbols": "Icons_16x16.default,Icons_16x16.page", "mode": 0},
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


def test_alarm_priority_symbols_by_number():
    sc = _real(SA_ALARM)
    dl = sc.sim_layer().drawn_layer()
    assert dl._symbol_name("1", 2) == "sa_priorities.sa_p1_normal"
    assert dl._symbol_name("1", 3) == "sa_priorities.sa_p1_small"
    assert dl._symbol_name("Shelved / Disabled", 4) == "sa_priorities.sa_disabled_normal"
    assert dl._symbol_name("9", 2) == ""


def test_the_example_alarm_page_draws_its_cicode_output():
    sc = _real(EX_ALARM)
    from ctgedit.sim.state import page_state_from_json

    st = page_state_from_json(_fixture("drawn_example_alarm.json"))
    r = sc.sim_layer().apply_drawn(st.drawn)
    dl = sc.sim_layer().drawn_layer()
    items = dl.items()
    assert r["drawn"] == 206 and not dl.missing_symbols
    texts = {_text_item(it).text() for an, it in items.items() if st.drawn[an]["kind"] == "text"}
    assert {"Time", "Tag", "Name", "Description", "What's New?", "Acknowledge page"} <= texts
    # the header row's captions lie inside the header bar (baseline at y)
    head = next(it for an, it in items.items() if st.drawn[an].get("text") == "Time")
    hb = head.sceneBoundingRect()
    assert 128 <= hb.top() and hb.bottom() <= 152


def test_drawn_output_hides_the_cicode_object_symbol():
    """SA 'Acknowledge Page': the Cicode object's sa_workspace.text_left symbol ('#####.#') is
    not drawn in the runtime look (the runtime draws a Cicode object only through its Cicode
    output: tc 0x1009 is never baked into the .ctF and the runtime reads no SYMBOL key), with
    or without drawn output (report_0 item 1); an export draws the static page."""
    sc = _real(SA_ALARM)
    layer = sc.sim_layer()
    i = layer.index_of(273)
    item = sc._items[i]
    assert sc._page.objects[i].raw.get("_type_code") == 0x1009 and not item.isVisible()
    from ctgedit.sim.state import page_state_from_json

    st = page_state_from_json(_fixture("drawn_examplesa_defaultalarm.json"))
    layer.apply_drawn(st.drawn)
    assert not item.isVisible()
    assert not layer.shows(i)
    from ctgedit.sim.state import ObjectState

    layer.apply({273: ObjectState(273, tooltip="tip")})  # a state of the record: still hidden
    assert not item.isVisible()
    layer.apply({273: None})
    assert not item.isVisible()
    with sc.export_only(None):
        assert item.isVisible()  # an export draws the static page
    assert not item.isVisible()
    layer.apply_drawn({})
    assert not item.isVisible()


EX_RTF = os.path.join(R2, "Example", "EXAMPLE.RTF")


def test_the_example_report_rtf_draws_in_a_rich_text_box():
    """The Example project's RTF report form: one block per paragraph, its red title and blue
    and green fields drawn in the box."""
    if not os.path.isfile(EX_RTF):
        pytest.skip("needs the Example project's EXAMPLE.RTF")
    with open(EX_RTF, encoding="cp1252") as f:
        rtf = f.read()
    from PySide6 import QtWidgets
    from ctgedit.render.rtf_html import rtf_paragraphs
    from ctgedit.render.sim_drawn import _walk

    paras = rtf_paragraphs(rtf)
    assert any("@(Example Report)" in p for p in paras)
    assert any("{LOOP_1_PV:#-###.#EU}" in p for p in paras)
    sc = _scene()
    entry = {"an": 7, "kind": "richText", "x": 0, "y": 0, "text": "EXAMPLE.RTF"}
    entry["args"] = {"width": W, "height": H, "row": 0, "col": 0, "rtf": rtf}
    sc.sim_layer().apply_drawn({7: entry})
    box = sc.sim_layer().drawn_layer().items()[7]
    t = next(it for it in _walk(box) if isinstance(it, QtWidgets.QGraphicsTextItem))
    assert t.document().blockCount() == len(paras)
    seen = _colours(_render(sc), (0, 0, W, H))
    assert any(r > 200 and g < 90 and b < 90 for r, g, b in seen)  # the red title
    assert any(r < 90 and g < 90 and b > 200 for r, g, b in seen)  # the blue heading
