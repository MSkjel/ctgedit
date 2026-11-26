"""ActiveX text boxes and drawn buttons in the Simulate view."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402
from test_sim_engine_source import _rect_page, _sim, wait_for  # noqa: E402
from qtkit import app as _app  # noqa: E402


@pytest.fixture
def alarm_page(fx_page):
    """FxActiveX: the page of the text box."""
    return fx_page("FxActiveX")


@pytest.fixture
def shapes_page(fx_page):
    """FxShapes: any page for the drawn layer, and rectangles and texts with ANs."""
    return fx_page("FxShapes")


def _an_of(path, name):
    """The AN of the object named ``name`` on the page at ``path``."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(path)
    (i,) = doc.find_objects_by_name(name)
    return doc.page.objects[i].an


_PAGES = {}


def _page(path):
    from ctgedit.ui.simulate.window import runtime_page

    if path not in _PAGES:
        _PAGES[path] = runtime_page(path)
    return _PAGES[path]


def _scene(path):
    _app()
    from ctgedit.render.scene import PageScene

    sc = PageScene()
    sc.set_page(_page(path))
    sc.set_runtime_look(True)
    return sc


@pytest.fixture
def servers(tmp_path, monkeypatch):
    folder = str(tmp_path / "servers")
    monkeypatch.setenv(C.SERVERS_ENV, folder)
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)
    return folder


@pytest.fixture
def page_file(tmp_path):
    proj = tmp_path / "User" / "Proj"
    proj.mkdir(parents=True)
    path = proj / "Mixer.ctg"
    path.write_bytes(b"")
    return str(path)


@pytest.fixture
def hub():
    h = C.Hub(linger=0)
    yield h
    h.shutdown()


# -- the wire format -----------------------------------------------------------------------
def test_page_controls_are_parsed_and_absent_means_no_news():
    from ctgedit.sim.state import controls_from_json, page_state_from_json

    st = page_state_from_json(
        {
            "window": 0,
            "page": "P",
            "objects": [],
            "controls": [
                {"an": 527, "name": "SEARCH", "properties": {"FontSize": 12, "Text": "Pump"}, "serial": 2},
                {"an": "x"},
            ],
        }
    )
    assert st.controls == {
        527: {"an": 527, "name": "SEARCH", "serial": 2, "properties": {"FontSize": "12", "Text": "Pump"}}
    }
    assert page_state_from_json({"window": 0, "objects": []}).controls is None
    assert controls_from_json([]) == {}


# -- the text fields -----------------------------------------------------------------------
def test_the_search_box_is_a_field_that_sends_and_shows_the_text(alarm_page):
    from ctgedit.ui.simulate.controls import TEXT_CONTROLS, ControlFields, is_field

    an = _an_of(alarm_page, "FxTextBox")
    sc = _scene(alarm_page)
    sent = []
    fields = ControlFields(sc, send=lambda an, p, v: sent.append((an, p, v)))
    ans = fields.rebuild(_page(alarm_page))
    # the page's text box (ciText.TextBox) and nothing else of its 43 ActiveX records
    assert ans == [an]
    assert "{C21CD4A3-BAA4-4473-8A16-8D22B2664E6B}" in TEXT_CONTROLS
    f = fields.fields()[an]
    proxy = fields.proxies()[an]
    assert is_field(proxy) and proxy.scene() is sc
    o = next(o for o in _page(alarm_page).objects if o.an == an)
    assert (proxy.pos().x(), proxy.pos().y()) == (o.geometry.x, o.geometry.y)
    assert f.width() == o.geometry.extent_x + 1
    # typing sends the control's Text (the engine's property store)
    f.textEdited.emit("Pump")
    assert sent == [(an, "Text", "Pump")]
    # what the code wrote shows (a search box's clear handler writes '')
    fields.apply({an: {"properties": {"Text": "Valve"}}})
    assert f.text() == "Valve"
    fields.apply({an: {"properties": {"FontSize": "12"}}})
    assert f.text() == "Valve"  # no Text: the field keeps its text
    # a scene build drops the old proxies: rebuild makes new ones
    sc.set_page(_page(alarm_page))
    assert fields.rebuild(_page(alarm_page)) == [an]
    assert fields.fields()[an].text() == ""


def test_combo_shows_engine_items_and_sends_the_selected_index():
    from types import SimpleNamespace

    from PySide6 import QtCore, QtTest, QtWidgets
    from ctgedit.sim.state import controls_from_json
    from ctgedit.ui.simulate.controls import ControlFields

    app = _app()
    scene = QtWidgets.QGraphicsScene()
    view = QtWidgets.QGraphicsView(scene)
    sent = []
    fields = ControlFields(scene, send=lambda *args: sent.append(args))
    obj = SimpleNamespace(
        an=7,
        raw={"_type_code": 13, "_ole_clsid": "{E8A1DF62-3D7C-49B6-9344-2104253063A4}"},
        geometry=SimpleNamespace(x=5, y=5, extent_x=120, extent_y=24),
    )
    fields.rebuild(SimpleNamespace(objects=[obj]))
    state = {"an": 7, "items": ["Pump", "Valve", "Pump"], "properties": {"ListIndex": 0, "Text": "Pump"}}
    fields.apply(controls_from_json([state]))
    combo = fields.fields()[7]
    assert combo.count() == 3 and combo.currentIndex() == 0
    assert not sent
    view.show()
    combo.showPopup()
    app.processEvents()
    popup = combo.view()
    rect = popup.visualRect(popup.model().index(2, 0))
    QtTest.QTest.mouseClick(popup.viewport(), QtCore.Qt.LeftButton, pos=rect.center())
    assert sent == [(7, "ListIndex", "2")]
    fields.apply(controls_from_json([dict(state, items=[], properties={"ListIndex": -1, "Text": ""})]))
    assert combo.count() == 0 and combo.currentText() == ""
    QtTest.QTest.keyClicks(combo.lineEdit(), "New")
    assert sent[-1] == (7, "Text", "New")
    # (an update while the user types leaves the typed text, as a text field does)
    combo.lineEdit().setFocus()
    app.processEvents()
    if combo.lineEdit().hasFocus():
        fields.apply(controls_from_json([dict(state, items=[], properties={"ListIndex": -1, "Text": "Old"})]))
        assert combo.currentText() == "New"
    # (the drop-down list's own proxy counts as the field: clicks on it are not the page's)
    from ctgedit.ui.simulate.controls import is_field

    assert is_field(fields.proxies()[7])
    view.close()


def test_typing_in_a_control_reaches_the_engine_and_the_code_writes_back(servers, hub, page_file):
    srv = F.FakeServer(os.path.dirname(page_file), servers, pages=True).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        assert sim.set_object_property(6, "Text", "Pump")
        assert wait_for(
            lambda: srv.object_sets == [{"window": sim.window, "an": 6, "property": "Text", "value": "Pump"}]
        )
        # a refused one (no control there) says why, once
        assert sim.set_object_property(99, "Text", "x")
        assert wait_for(lambda: any("not taken" in e.text for e in sim.drain()))
        # the code writes the property: the next poll carries the controls
        srv.set_control(sim.window, 6, "Text", "")

        def got():
            st = sim.poll()
            return (
                st is not None
                and st.controls is not None
                and st.controls.get(6, {}).get("properties", {}).get("Text") == ""
            )

        assert wait_for(got)
    finally:
        sim.stop()
        srv.stop()


# -- trend history -------------------------------------------------------------------------
def test_the_trend_history_option_is_sent(servers, hub, page_file):
    srv = F.FakeServer(os.path.dirname(page_file), servers, pages=True).start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        # (an attached server: the connect sends nothing, the user's change does)
        assert srv.seed_sets == []
        sim.set_seed_trend_history(True)
        assert wait_for(lambda: srv.seed_sets == [True])
        sim.set_seed_trend_history(False)
        assert wait_for(lambda: srv.seed_sets == [True, False])
    finally:
        sim.stop()
        srv.stop()


def test_the_trend_history_setting():
    from ctgedit.ui.simulate import options

    # (on by default: the trends show history, like the runtime's archive)
    assert options.OPTIONS["seed_trend_history"] == ("sim/seed_trend_history", True)
    _app()
    from ctgedit.ui.simulate.settings_dialog import SimSettingsDialog

    dlg = SimSettingsDialog()
    try:
        assert dlg.trend_history.isChecked()
        dlg.trend_history.setChecked(False)
        dlg.store()
        assert options.get("seed_trend_history") is False
        dlg.defaults()
        assert dlg.trend_history.isChecked()
    finally:
        options.put("seed_trend_history", True)
        dlg.deleteLater()


# -- buttons Cicode drew -------------------------------------------------------------------
def test_a_drawn_button_takes_the_click(shapes_page):
    from PySide6 import QtCore
    from ctgedit.ui.simulate.canvas import SimCanvas

    sc = _scene(shapes_page)
    entry = {
        "an": 31001,
        "kind": "button",
        "x": 40,
        "y": 50,
        "text": "Mixer",
        "args": {
            "width": 130,
            "height": 35,
            "upFunction": "_PageMenuFn",
            "downFunction": "",
            "repeatFunction": "",
            "style": 0,
        },
        "serial": 1,
    }
    sc.sim_layer().apply_drawn({31001: entry})
    d = sc.sim_layer().drawn_layer()
    assert d.buttons_at(QtCore.QPointF(60, 60)) == [31001]
    assert d.buttons_at(QtCore.QPointF(10, 10)) == []
    view = SimCanvas(sc)
    view.resize(800, 600)
    view.set_zoom(1.0)
    view.centerOn(100, 100)
    pos = view.mapFromScene(QtCore.QPointF(60, 60))
    ans = view.ans_at(pos)
    assert ans[0] == 31001  # above every page object
    # a text Cicode drew is no button
    sc.sim_layer().apply_drawn(
        {31002: {"an": 31002, "kind": "text", "x": 40, "y": 50, "text": "x", "args": {}, "serial": 1}}
    )
    assert d.buttons_at(QtCore.QPointF(60, 60)) == []
    view.deleteLater()


# -- alarm list titles and layout ----------------------------------------------------------
def test_an_alarm_list_takes_its_titles_and_layout_from_the_engine(fx_page):
    """On FxSim's FxSimAlarms: the engine's list state lands on the alarm-list-like genie
    FxAlarmList1."""
    from PySide6 import QtWidgets
    from ctgedit.sim.state import ObjectState

    page = fx_page("FxSimAlarms", "FxSim")
    an = _an_of(page, "FxAlarmList1")
    sc = _scene(page)
    cols = [["PriorityAndState", 25], ["OnDate", 80], ["Comment", 250]]
    al = {
        "type": 0,
        "count": 27,
        "offset": 0,
        "total": 1,
        "array": True,
        "no_draw": True,
        "columns": cols,
        "drawHeader": True,
        "rowHeight": 24,
        "width": 1098,
        "height": 672,
        "titles": [" ", "Date on", "User Comment"],
        "rows": [
            {
                "row": 0,
                "fields": [["OnDate", "02-10-26"], ["Comment", "c"]],
                "state": "unack_on",
                "priority": 1,
            }
        ],
    }
    sc.apply_states({an: ObjectState(an, alarm_list=al)})
    ov = sc.sim_layer().alarm_lists()._lists[an]
    texts = {t.text() for t in ov.items if isinstance(t, QtWidgets.QGraphicsSimpleTextItem)}
    assert {"Date on", "User Comment", "02-10-26"} <= texts
    assert "On Date" not in texts and "Comment" not in texts  # not made from the names
    # the engine's width is the list's
    assert ov.box.rect().width() == 1098
    # no header: the first row draws in the header's place
    al2 = dict(al, drawHeader=False)
    sc.apply_states({an: ObjectState(an, alarm_list=al2)})
    ov = sc.sim_layer().alarm_lists()._lists[an]
    assert not any(
        t.text() == "Date on" for t in ov.items if isinstance(t, QtWidgets.QGraphicsSimpleTextItem)
    )


# -- quality indication --------------------------------------------------------------------
def test_a_bad_quality_object_is_dithered_and_a_text_with_its_error_is_not(shapes_page):
    from ctgedit.model.document import ObjectKind
    from ctgedit.render.sim_states import DITHER_ROLE, dither_brush
    from ctgedit.sim.state import ObjectState

    rect_an, num_an = _an_of(shapes_page, "FxRect"), _an_of(shapes_page, "FxNumber")
    sc = _scene(shapes_page)
    page = _page(shapes_page)
    layer = sc.sim_layer()
    rect = next(o for o in page.objects if o.an == rect_an)
    assert rect.kind is ObjectKind.RECTANGLE and rect.parent is None
    assert sc._items.get(page.objects.index(rect)) is not None
    i = page.objects.index(rect)
    sc.apply_states({rect.an: ObjectState(rect.an, indication="bad")})
    items = layer.dither_items()
    assert i in items and items[i].data(DITHER_ROLE) == 1
    r = items[i].path().boundingRect()
    g = rect.geometry
    assert abs(r.left() - g.x) <= 1 and abs(r.top() - g.y) <= 1
    # moved with the object's state
    sc.apply_states({rect.an: ObjectState(rect.an, indication="bad", move=(30.0, 0.0))})
    assert abs(layer.dither_items()[i].sceneBoundingRect().left() - (g.x + 30)) <= 1
    # back to good: gone
    sc.apply_states({rect.an: ObjectState(rect.an)})
    assert i not in layer.dither_items()
    # a number showing its error text ('#BAD', [Page]ShowBadText 1) is not dithered
    num = next(o for o in page.objects if o.an == num_an)
    assert num.kind in (ObjectKind.NUMBER, ObjectKind.TEXT)
    k = page.objects.index(num)
    assert sc._items.get(k) is not None
    sc.apply_states({num.an: ObjectState(num.an, indication="bad", text="#BAD")})
    assert k not in layer.dither_items()
    sc.apply_states({num.an: ObjectState(num.an, indication="override", text="12.5")})
    assert k in layer.dither_items()
    # an export draws the static page
    with sc.export_only(None):
        assert k not in layer.dither_items()
    assert k in layer.dither_items()
    layer.clear()
    assert layer.dither_items() == {}
    # the dot pattern of density 2 = the live runtime's (a capture of ExampleSA's
    # Overview_L2): a 4 x 8 tile, dots at (2, 0) and (0, 4); density 1 a 2 x 4 one
    img2 = dither_brush(0, 2).textureImage()
    assert (img2.width(), img2.height()) == (4, 8)
    assert {(x, y) for y in range(8) for x in range(4) if img2.pixelColor(x, y).alpha() > 0} == {
        (2, 0),
        (0, 4),
    }
    img1 = dither_brush(0, 1).textureImage()
    assert {(x, y) for y in range(4) for x in range(2) if img1.pixelColor(x, y).alpha() > 0} == {
        (1, 0),
        (0, 2),
    }


# -- Simulate Settings: the debugger's stop on page-code errors, the fast startup clock --------
def test_the_page_code_error_stop_and_the_fast_startup_are_sent(servers, hub, page_file):
    srv = F.FakeServer(os.path.dirname(page_file), servers, pages=True).start()
    sim = _sim(hub)
    sim.fast_startup = False
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        sim.set_foreground_error_break(True)
        assert wait_for(lambda: {"foregroundErrorBreak": True} in srv.option_requests)
        sim.set_foreground_error_break(False)
        assert wait_for(lambda: {"foregroundErrorBreak": False} in srv.option_requests)
    finally:
        sim.stop()
        srv.stop()
    # load {fastStartup: false} only when the setting is off (the engine's default is fast)
    s = C.Session.__new__(C.Session)
    s.project, s.requested_root = "P", ""
    assert C.Session.load_args(s, startup="auto", fast_startup=False)["fastStartup"] is False
    assert "fastStartup" not in C.Session.load_args(s, startup="auto", fast_startup=True)
    assert "fastStartup" not in C.Session.load_args(s, startup="auto")


def test_the_new_simulate_settings_and_their_defaults():
    from ctgedit.ui.simulate import options

    assert options.OPTIONS["foreground_error_break"] == ("sim/foreground_error_break", False)
    assert options.OPTIONS["fast_startup"] == ("sim/fast_startup", True)
    assert options.OPTIONS["value_placeholder"] == ("sim/value_placeholder", "abc")
    _app()
    from ctgedit.ui.simulate.settings_dialog import SimSettingsDialog

    dlg = SimSettingsDialog(None)
    try:
        assert dlg.fast_startup.isChecked() and not dlg.fg_error_break.isChecked()
        assert dlg.placeholder.currentData() == "abc"
        dlg.placeholder.setCurrentIndex(dlg.placeholder.findData(""))
        dlg.fg_error_break.setChecked(True)
        dlg.store()
        assert options.get("value_placeholder") == ""  # (nothing is a value)
        assert options.get("foreground_error_break") is True
        dlg.defaults()
        assert dlg.placeholder.currentData() == "abc" and not dlg.fg_error_break.isChecked()
    finally:
        dlg.deleteLater()
