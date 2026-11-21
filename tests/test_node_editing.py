"""Node editing, the Vertices page and vertex animation."""

import os
import shutil

import fxinstall

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit.format import geometry as G  # noqa: E402
from ctgedit.format import vertexanim as VA  # noqa: E402
from qtkit import app as _app

#: the host page of the tests (FxMain): free from 0, 0 to 1024, 768 but for one text at
#: (20, 20, 63, 36)
HOST = "FxMaster"
#: FxMain's pages with free lines, pipes and polygons, and how many records of them
#: reshape_info knows (the generator draws them: FxShapes' FxLine, FxPolygon, FxPolyline
#: and FxPipe, FxBig's 30, the others' few)
RESHAPE_PAGES = ("FxAnim", "FxBig", "FxColours", "FxMany", "FxMotion", "FxShapes", "FxTplUnlinked")
RESHAPE_COUNT = 46

CTRL = QtCore.Qt.ControlModifier
NOMOD = QtCore.Qt.NoModifier
LEFT = QtCore.Qt.LeftButton


@pytest.fixture()
def doc(fx_page):
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(fx_page(HOST))
    d.derive_check = True  # every derive == a full one
    return d


@pytest.fixture()
def win(fx_page):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_page(HOST))
    w.resize(1300, 900)
    w.show()
    w.activateWindow()
    w.view.set_snap(False)
    w.view.set_smart_guides(False)
    # (zoom 1: the drags below are in page pixels; the fit zoom depends on the window's
    # layout, and at 1.05 a 20 px pointer move is 19 page pixels)
    w.view.set_zoom(1.0)
    QtWidgets.QApplication.processEvents()
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()


def _hdr(po):
    return G.parse_geom_header(po.body_raw)


def _nodes(doc, i):
    return G.get_object_nodes(doc.parsed.page.objects[i])


def _reload(doc):
    from ctgedit.format.page import parse_ctg_bytes

    return parse_ctg_bytes(doc.serialize())


# ------------------------------------------------------------------ canvas helpers
def _me(view, kind, sp, buttons=QtCore.Qt.NoButton, mods=NOMOD):
    vp = view.mapFromScene(QtCore.QPointF(*sp))
    g = view.viewport().mapToGlobal(vp)
    return QtGui.QMouseEvent(kind, QtCore.QPointF(vp), QtCore.QPointF(g), LEFT, buttons, mods)


def _press(view, sp, mods=NOMOD):
    view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, sp, LEFT, mods))


def _move(view, sp, mods=NOMOD, buttons=LEFT):
    view.mouseMoveEvent(_me(view, QtCore.QEvent.MouseMove, sp, buttons, mods))


def _release(view, sp, mods=NOMOD):
    view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, sp, QtCore.Qt.NoButton, mods))


def _click(view, sp, mods=NOMOD):
    _press(view, sp, mods)
    _release(view, sp, mods)


def _drag(view, a, b, mods=NOMOD):
    _press(view, a, mods)
    _move(view, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), mods)
    _move(view, b, mods)
    _release(view, b, mods)


def _key(view, key, mods=NOMOD, text=""):
    ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, mods, text)
    view.keyPressEvent(ev)
    QtWidgets.QApplication.processEvents()


def _new(w, kind, nodes):
    i = w.doc.create_object(kind, nodes=nodes)
    w._refresh()
    w._on_canvas_selection([i])
    w.view.set_selection([i])
    QtWidgets.QApplication.processEvents()
    return i


def _canvas_shortcut(w, key):
    seq = key if isinstance(key, QtGui.QKeySequence) else QtGui.QKeySequence(key)
    for sc in w.view.findChildren(QtGui.QShortcut):
        if sc.key() == seq:
            return sc
    raise AssertionError("no canvas shortcut %s" % key)


# ================================================================== format / document
def test_reshape_info_classes(doc):
    """Only lines, pipes and polygons / polylines reshape (CtDraw's node edits
    exist for those three classes only)."""
    line = doc.create_object("line", nodes=[(10, 10), (90, 40)])
    pipe = doc.create_object("pipe", nodes=[(10, 100), (90, 100), (90, 150)])
    poly = doc.create_object("polygon", nodes=[(200, 10), (260, 10), (230, 60)])
    pline = doc.create_object("polyline", nodes=[(300, 10), (360, 10), (330, 60)])
    rect = doc.create_object("rectangle", rect=(400, 10, 450, 60))
    info = {i: doc.reshape_info(i) for i in (line, pipe, poly, pline, rect)}
    assert info[rect] is None
    assert (info[line].kind, info[line].can_insert) == ("line", False)
    assert info[pipe].kind == "pipe" and not info[pipe].closed
    assert info[poly].kind == "polyline" and info[poly].closed  # closed polygon
    assert info[pline].kind == "polyline" and not info[pline].closed  # open + hollow
    assert info[poly].segments()[-1] == (2, 0)  # the closing segment
    assert info[line].nodes == [(10, 10), (90, 40)]


def test_dragging_vertex_2_moves_exactly_that_node(doc):
    """Acceptance: vertex 2 moves, nothing else; anchor = node 1, rect1 = the
    bbox, rect2 = the Bounds rule; one undo step; survives save + reload."""
    i = doc.create_object("polygon", nodes=[(100, 100), (200, 100), (200, 180), (100, 180)])
    po = doc.parsed.page.objects[i]
    before = bytes(po.body_raw)
    n = len(doc._undo)
    assert doc.move_object_nodes(i, [1], 25, -30)
    assert len(doc._undo) == n + 1
    assert _nodes(doc, i) == [(100, 100), (225, 70), (200, 180), (100, 180)]
    h = _hdr(doc.parsed.page.objects[i])
    assert h.anchor == (100, 100) and h.rect1 == (100, 70, 225, 180)
    assert h.rect2 == G.predict_bounds(0x10, h.rect1, G.style_flags(doc.parsed.page.objects[i]))
    back = _reload(doc)
    assert G.get_object_nodes(back.page.objects[i]) == [(100, 100), (225, 70), (200, 180), (100, 180)]
    doc.undo()
    assert bytes(doc.parsed.page.objects[i].body_raw) == before


def test_tilting_a_line_gives_its_endpoints_after_reload(doc):
    """Acceptance: a flat line's end node dragged off the axis tilts it; the
    draw-order endpoints (last 8 bytes), the normalised extent and the anchor
    (= the first point) follow the free-line rule, also for an anti-diagonal."""
    i = doc.create_object("line", nodes=[(300, 300), (400, 300)])
    assert doc.move_object_nodes(i, [1], -150, 60)  # now right-to-left
    po = _reload(doc).page.objects[i]
    assert G.get_object_nodes(po) == [(300, 300), (250, 360)]
    h = _hdr(po)
    assert h.anchor == (300, 300) and h.rect1 == (250, 300, 300, 360)
    assert G.parse_geom_header(po.body_raw).rect2 == G.predict_bounds(0x01, h.rect1, G.style_flags(po))
    assert doc.move_object_nodes(i, [0], 0, 100)  # the first endpoint
    assert _nodes(doc, i) == [(300, 400), (250, 360)]
    assert _hdr(doc.parsed.page.objects[i]).anchor == (300, 400)


def test_insert_node_on_the_outline_and_not_on_a_line(doc):
    i = doc.create_object("pipe", nodes=[(100, 100), (300, 100), (300, 200)])
    info = doc.reshape_info(i)
    hit = G.node_insert_point(info, (201.4, 102.0), 4.0)
    assert hit == (1, (201, 100))  # the foot on segment 0-1
    assert G.node_insert_point(info, (200, 150), 4.0) is None  # off the outline
    assert G.node_insert_point(info, (100, 101), 4.0) is None  # on an end node
    assert doc.insert_object_node(i, *hit)
    assert _nodes(doc, i) == [(100, 100), (201, 100), (300, 100), (300, 200)]
    # a closed polygon's closing segment takes a node at the end
    p = doc.create_object("polygon", nodes=[(400, 100), (500, 100), (500, 200)])
    hit = G.node_insert_point(doc.reshape_info(p), (450, 150), 3.0)
    assert hit == (3, (450, 150))
    line = doc.create_object("line", nodes=[(10, 10), (90, 10)])
    assert G.node_insert_point(doc.reshape_info(line), (50, 10), 4.0) is None
    assert not doc.insert_object_node(line, 1, (50, 10))


def test_delete_nodes_keeps_two(doc):
    """a delete that would leave fewer than two
    nodes does nothing; a line's nodes never go one at a time."""
    i = doc.create_object("pipe", nodes=[(100, 100), (200, 100), (200, 200)])
    n = len(doc._undo)
    assert not doc.delete_object_nodes(i, [0, 1])
    assert len(doc._undo) == n
    assert doc.delete_object_nodes(i, [1])
    assert _nodes(doc, i) == [(100, 100), (200, 200)]
    assert not doc.delete_object_nodes(i, [0])  # two left: refused
    p = doc.create_object("polygon", nodes=[(0, 0), (50, 0), (50, 50), (0, 50)])
    assert doc.delete_object_nodes(p, [0, 2])
    assert _nodes(doc, p) == [(50, 0), (0, 50)]
    assert _hdr(doc.parsed.page.objects[p]).anchor == (50, 0)
    line = doc.create_object("line", nodes=[(10, 10), (90, 10)])
    assert not doc.delete_object_nodes(line, [0])


def test_vertex_animation_keys_follow_their_vertex(doc):
    i = doc.create_object("polygon", nodes=[(0, 0), (100, 0), (100, 100), (0, 100)])
    for k, v in (
        ("VERTEX_VERTFROMX2", "0"),
        ("VERTEX_VERTTOY2", "-40"),
        ("VERTEX_VERTEXP2", "Tag2"),
        ("VERTEX_VERTEXP3", "Tag3"),
    ):
        assert doc.ensure_property(i, k, v)
    assert doc.parsed.page.objects[i].subtype_size & 0x10  # animated: dynamic
    n = len(doc._undo)
    assert doc.insert_object_node(i, 1, (50, 0))  # before vertex 2
    vk = {k: v for k, v in doc.object_properties(i).items() if k.startswith("VERTEX_")}
    assert vk == {
        "VERTEX_VERTFROMX3": "0",
        "VERTEX_VERTTOY3": "-40",
        "VERTEX_VERTEXP3": "Tag2",
        "VERTEX_VERTEXP4": "Tag3",
    }
    assert len(doc._undo) == n + 1  # nodes + keys: one step
    assert doc.delete_object_nodes(i, [3])  # Tag2's vertex goes
    vk = {k: v for k, v in doc.object_properties(i).items() if k.startswith("VERTEX_")}
    assert vk == {"VERTEX_VERTEXP3": "Tag3"}
    doc.undo()
    doc.undo()
    vk = {k: v for k, v in doc.object_properties(i).items() if k.startswith("VERTEX_")}
    assert vk == {
        "VERTEX_VERTFROMX2": "0",
        "VERTEX_VERTTOY2": "-40",
        "VERTEX_VERTEXP2": "Tag2",
        "VERTEX_VERTEXP3": "Tag3",
    }
    assert _nodes(doc, i) == [(0, 0), (100, 0), (100, 100), (0, 100)]


def test_reshape_refuses_off_page_coordinates(doc):
    i = doc.create_object("polygon", nodes=[(0, 0), (100, 0), (100, 100)])
    assert not doc.move_object_nodes(i, [0], -40000, 0)
    assert not doc.reshape_nodes(i, [(0, 0)])  # fewer than two
    assert _nodes(doc, i) == [(0, 0), (100, 0), (100, 100)]


def test_vertexanim_edits_write_companions_in_ctdraw_order():
    old = VA.read({}, 3)
    new = [a.copy() for a in old]
    new[1].expr = "(Tag1)^(0,50)"
    new[2].offsets[VA.TO_Y] = "25"
    assert VA.edits({}, old, new) == [
        ("VERTEX_VERTFROMX1", "0"),
        ("VERTEX_VERTFROMY1", "0"),
        ("VERTEX_VERTTOX1", "0"),
        ("VERTEX_VERTTOY1", "0"),
        ("VERTEX_VERTEXP1", "(Tag1)^(0,50)"),
        ("VERTEX_VERTTOY2", "25"),
    ]
    props = {"vertex_vertexp1": "Tag", "VERTEX_VERTFROMX1": "5"}
    old = VA.read(props, 2)
    assert old[1].expr == "Tag" and old[1].offsets[VA.FROM_X] == "5"  # case-insensitive
    new = [a.copy() for a in old]
    new[1].expr = None  # emptied: removed, offsets stay
    assert VA.edits(props, old, new) == [("vertex_vertexp1", None)]
    assert VA.renumber_edits({"VERTEX_VERTEXP0": "A", "VERTEX_VERTEXP1": "B"}, VA.delete_mapping(2, [0])) == [
        ("VERTEX_VERTEXP1", None),
        ("VERTEX_VERTEXP0", "B"),
    ]


def _fixed_points(name, parsed):
    """The number of reshapeable records of ``parsed`` (page ``name``); each keeps its
    bytes when given its own nodes."""
    n = 0
    for po in parsed.page.objects:
        info = G.reshape_info(po)
        if info is None:
            continue
        body = bytes(po.body_raw)
        assert G.set_object_nodes(po, info.nodes), (name, po.type_code)
        assert bytes(po.body_raw) == body, (name, po.type_code)
        n += 1
    return n


def test_reshaping_to_the_same_nodes_is_a_fixed_point_on_the_fixture_records(fx_page):
    """The header rules reproduce the records the fixtures hold: every free line,
    pipe and polygon of RESHAPE_PAGES keeps its bytes when given its own nodes."""
    from ctgedit.format.page import parse_ctg

    n = sum(_fixed_points(name, parse_ctg(fx_page(name))) for name in RESHAPE_PAGES)
    assert n == RESHAPE_COUNT


@pytest.mark.golden
def test_reshaping_to_the_same_nodes_is_a_fixed_point_on_engine_records():
    """The header rules reproduce the engine's own records: every free line,
    pipe and polygon of CtDraw's saves of RESHAPE_PAGES (oracle step 'pages') keeps
    its bytes when given its own nodes."""
    from ctgedit.format.page import parse_ctg_bytes

    n = 0
    for name in RESHAPE_PAGES:
        data = fxinstall.oracle_bytes("pages", "FxMain/" + name, "page.ctg")
        n += _fixed_points(name, parse_ctg_bytes(data))
    assert n > 40  # (the fixture's 46, less any CtDraw folds)


# ================================================================== canvas
def test_canvas_drag_one_node_of_a_polygon(win):
    i = _new(win, "polygon", [(300, 300), (400, 300), (400, 380), (300, 380)])
    view = win.view
    idx, info = view.reshape_target()
    assert idx == i and len(info.nodes) == 4
    n = len(win.doc._undo)
    _drag(view, (400, 300), (430, 270))
    assert _nodes(win.doc, i) == [(300, 300), (430, 270), (400, 380), (300, 380)]
    assert len(win.doc._undo) == n + 1
    assert view.node_selection() == (i, [1])  # the node stays selected
    win.doc.undo()
    win._refresh()
    assert _nodes(win.doc, i) == [(300, 300), (400, 300), (400, 380), (300, 380)]


def test_canvas_node_selection_click_ctrl_and_elsewhere(win):
    i = _new(win, "polygon", [(300, 300), (400, 300), (400, 380), (300, 380)])
    view = win.view
    _click(view, (300, 300))
    assert view.node_selection() == (i, [0])
    _click(view, (400, 380))  # another node: only that one
    assert view.node_selection() == (i, [2])
    _click(view, (300, 380), CTRL)  # Ctrl adds
    assert view.node_selection() == (i, [2, 3])
    _click(view, (400, 380), CTRL)  # Ctrl on a selected one removes it
    assert view.node_selection() == (i, [3])
    _click(view, (300, 380))  # the same node again: toggles off
    assert view.node_selection() is None
    _click(view, (300, 300))
    _click(view, (350, 340))  # anywhere else: every node off
    assert view.node_selection() is None
    assert view.selection_roots() == [i]


def test_canvas_multi_node_drag_keeps_their_layout(win):
    i = _new(win, "pipe", [(300, 300), (400, 300), (400, 380), (500, 380)])
    view = win.view
    _click(view, (400, 300))
    _click(view, (400, 380), CTRL)
    _drag(view, (400, 380), (420, 390))
    assert _nodes(win.doc, i) == [(300, 300), (420, 310), (420, 390), (500, 380)]


def test_canvas_tilts_a_flat_line_by_its_end_node(win):
    """A line's end sizing handle gives way to its node: the endpoint moves
    freely (help 1172188) and the saved line has the tilted endpoints."""
    i = _new(win, "line", [(300, 300), (400, 300)])
    view = win.view
    _drag(view, (400, 300), (420, 360))
    assert view._resizing is None
    po = _reload(win.doc).page.objects[i]
    assert G.get_object_nodes(po) == [(300, 300), (420, 360)]
    assert _hdr(po).rect1 == (300, 300, 420, 360)


def test_canvas_insert_and_plus_add_a_node(win):
    i = _new(win, "polygon", [(300, 300), (400, 300), (400, 380), (300, 380)])
    view = win.view
    _move(view, (330, 301), buttons=QtCore.Qt.NoButton)  # (not on the mid handle)
    assert view.viewport().cursor().shape() == QtCore.Qt.PointingHandCursor
    _key(view, QtCore.Qt.Key_Insert)
    assert _nodes(win.doc, i) == [(300, 300), (330, 300), (400, 300), (400, 380), (300, 380)]
    assert view.node_selection() == (i, [1])  # the new node is selected
    _move(view, (401, 340), buttons=QtCore.Qt.NoButton)
    _key(view, QtCore.Qt.Key_Plus, text="+")
    assert (401, 340) not in _nodes(win.doc, i) and (400, 340) in _nodes(win.doc, i)
    # off the outline: nothing, and the reason in the status bar
    n = len(win.doc._undo)
    _move(view, (350, 340), buttons=QtCore.Qt.NoButton)
    _key(view, QtCore.Qt.Key_Insert)
    assert len(win.doc._undo) == n
    assert "outline" in win.statusBar().currentMessage()


def test_canvas_insert_on_a_line_is_refused(win):
    i = _new(win, "line", [(300, 300), (400, 300)])
    view = win.view
    _move(view, (350, 300), buttons=QtCore.Qt.NoButton)
    _key(view, QtCore.Qt.Key_Insert)
    assert _nodes(win.doc, i) == [(300, 300), (400, 300)]
    assert "polyline" in win.statusBar().currentMessage()


def test_delete_and_minus_delete_the_selected_nodes(win):
    from ctgedit.ui.prefs import keymap

    # (Delete's minus keys are not given by default: Settings > Keyboard gives them)
    keymap.store_user_keys({"act_del": ["Del", "-", "Num+-"]})
    keymap.apply_keymap(win)
    doc = win.doc
    i = _new(win, "polygon", [(300, 300), (400, 300), (400, 380), (300, 380)])
    view = win.view
    _click(view, (400, 300))
    win.act_del.trigger()  # Delete
    assert _nodes(doc, i) == [(300, 300), (400, 380), (300, 380)]
    assert view.node_selection() is None
    _click(view, (400, 380))
    _canvas_shortcut(win, "-").activated.emit()  # minus
    assert _nodes(doc, i) == [(300, 300), (300, 380)]
    # the minimum: two stay
    _click(view, (300, 300))
    n = len(doc._undo)
    win.act_del.trigger()
    assert len(doc._undo) == n and len(_nodes(doc, i)) == 2
    assert "at least 2 nodes" in win.statusBar().currentMessage()
    # no node selected: Delete deletes the object
    po = doc.parsed.page.objects[i]
    view.clear_node_selection()
    win.act_del.trigger()
    assert po not in doc.parsed.page.objects


def test_deleting_a_line_node_deletes_the_line(win):
    doc = win.doc
    i = _new(win, "line", [(300, 300), (400, 340)])
    po = doc.parsed.page.objects[i]
    _click(win.view, (300, 300))
    assert win.view.node_selection() == (i, [0])
    win.act_del.trigger()
    assert po not in doc.parsed.page.objects


def test_cut_deletes_the_object_not_its_nodes(win):
    doc = win.doc
    i = _new(win, "polygon", [(300, 300), (400, 300), (400, 380)])
    po = doc.parsed.page.objects[i]
    _click(win.view, (400, 300))
    win.cut_selected()
    assert po not in doc.parsed.page.objects


def test_no_nodes_for_a_locked_object(win):
    i = _new(win, "polygon", [(300, 300), (400, 300), (400, 380)])
    assert win.view.reshape_target()[0] == i
    win._locked.add(i)
    win.view.set_selection([i])
    assert win.view.reshape_target() == (None, None)
    win._locked.discard(i)


def test_sizing_handles_give_way_to_nodes(win):
    """A polygon corner handle on a vertex is the vertex; the edge handles
    still resize."""
    i = _new(win, "polygon", [(300, 300), (400, 300), (400, 380), (300, 380)])
    view = win.view
    _idx, box = view._resize_target()
    ids = {hid for hid, _x, _y in view._resize_handles(box)}
    assert ids == {1, 3, 5, 7}  # the corners are nodes
    _drag(view, (400, 340), (420, 340))  # right-middle: a resize
    assert G.norm_rect(_hdr(win.doc.parsed.page.objects[i]).rect1) == (300, 300, 420, 380)


def test_ctrl_arrow_moves_like_an_arrow(win):
    i = win.doc.create_object("rectangle", (300, 300, 400, 350), None)
    win._refresh()
    win._on_canvas_selection([i])
    win.view.set_selection([i])
    win._nudge_step = 5
    _canvas_shortcut(win, "Ctrl+Right").activated.emit()
    win.commit_nudge()  # (an arrow-key burst reaches the page in one move)
    assert G.norm_rect(_hdr(win.doc.parsed.page.objects[i]).rect1) == (305, 300, 405, 350)


# ================================================================== the Vertices page
def _sheet(doc, index):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    return ObjectPropertiesDialog(doc, index)


def test_vertices_page_lists_and_moves_vertices(doc):
    i = doc.create_object("polygon", nodes=[(100, 100), (200, 100), (150, 180)])
    dlg = _sheet(doc, i)
    assert "vertices" in dlg.page_ids()
    ed = dlg.vertices_editor
    t = ed.table
    assert [(t.item(r, 0).text(), t.item(r, 1).text(), t.item(r, 2).text()) for r in range(t.rowCount())] == [
        ("1", "100", "100"),
        ("2", "200", "100"),
        ("3", "150", "180"),
    ]
    ed.set_vertex(1, 210, 90)
    n = len(doc._undo)
    assert dlg.apply()
    assert _nodes(doc, i) == [(100, 100), (210, 90), (150, 180)]
    assert len(doc._undo) == n + 1
    assert not any(k.startswith("VERTEX_") for k in doc.object_properties(i))


def test_vertices_page_writes_the_vertex_animation(doc):
    from ctgedit.io import pgregister

    i = doc.create_object("polygon", nodes=[(100, 100), (200, 100), (150, 180)])
    dlg = _sheet(doc, i)
    ed = dlg.vertices_editor
    ed.select_vertex(1)
    ed.expr.setText("Tag1")
    ed.expr.range_field.check.setChecked(True)
    ed.expr.range_field.minimum.setText("0")
    ed.expr.range_field.maximum.setText("50")
    ed.sp[VA.TO_Y].setValue(-40)
    assert dlg.tab_marked("Appearance", "Vertices")  # follows the typing
    n = len(doc._undo)
    assert dlg.apply()
    assert len(doc._undo) == n + 1
    items = [(k, v) for k, v in doc.object_properties(i).items() if k.startswith("VERTEX_")]
    assert items == [
        ("VERTEX_VERTFROMX1", "0"),
        ("VERTEX_VERTFROMY1", "0"),
        ("VERTEX_VERTTOX1", "0"),
        ("VERTEX_VERTTOY1", "-40"),
        ("VERTEX_VERTEXP1", "(Tag1)^(0,50)"),
    ]
    po = doc.parsed.page.objects[i]
    assert po.subtype_size & 0x10  # dynamic: registered
    rows = pgregister.page_rows(doc.parsed, "P")["pgvertex"]
    assert [(r["INDEX"], r["EXPR"], r["X1"], r["Y1"], r["X2"], r["Y2"]) for r in rows] == [
        ("1", "(Tag1)^(0,50)", "0", "0", "0", "-40")
    ]
    # reopened: the page shows what was stored
    dlg = _sheet(doc, i)
    ed = dlg.vertices_editor
    ed.select_vertex(1)
    assert ed.expr.inner_text() == "Tag1" and ed.expr.range_field.specified()
    assert ed.sp[VA.TO_Y].value() == -40 and ed.table.item(1, 3).text() == "(Tag1)^(0,50)"
    # Clear Property removes every vertex key
    ed._btn_clear.click()
    assert dlg.apply()
    assert not any(k.startswith("VERTEX_") for k in doc.object_properties(i))


def test_vertices_page_range_problem_blocks_apply(doc):
    i = doc.create_object("polygon", nodes=[(100, 100), (200, 100), (150, 180)])
    dlg = _sheet(doc, i)
    ed = dlg.vertices_editor
    ed.select_vertex(2)
    ed.expr.setText("Tag1")
    ed.expr.range_field.check.setChecked(True)
    ed.expr.range_field.minimum.setText("")
    ed.select_vertex(0)  # the problem is found on another row too
    n = len(doc._undo)
    assert not dlg.apply()
    assert len(doc._undo) == n
    assert "Vertex 3" in dlg._status.text()


def _vertex_polygon(doc):
    """FxAnim's FxVertex: a free polygon (tc 0x10, dwFlags 0x2) with VERTEX_VERTEXP1."""
    hits = doc.find_objects_by_name("FxVertex")
    assert len(hits) == 1
    idx = hits[0]
    po = doc.parsed.page.objects[idx]
    assert po.type_code == 0x10 and po.subtype_size & 0x2
    return idx


def test_vertices_page_shows_a_stored_polygon(fx_page):
    """A polygon the page holds (FxAnim's FxVertex, vertex 2 animated by FX_LEVEL1,
    no offsets: an absent offset shows 0) opens on the Vertices page untouched."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxAnim"))
    idx = _vertex_polygon(doc)
    assert "VERTEX_VERTEXP1" in doc.object_properties(idx)
    dlg = _sheet(doc, idx)
    assert dlg.tab_marked("Appearance", "Vertices")
    ed = dlg.vertices_editor
    ed.select_vertex(1)
    assert ed.expr.inner_text() == "FX_LEVEL1"
    assert all(ed.sp[k].value() == 0 for k in VA.OFFSETS)
    assert VA.edits(doc.object_properties(idx), ed._anim0, ed._anim) == []  # untouched


def test_vertices_page_shows_stored_offsets(fx_page):
    """The offsets a page stores (FxVertex given VERTEX_VERTFROMX1 0 and
    VERTEX_VERTTOY1 -130, the form CtDraw's apply writes, saved and opened again)
    show on the Vertices page, and opening it changes nothing."""
    from ctgedit.editing import EditableDocument

    path = fx_page("FxAnim")
    doc = EditableDocument.open(path)
    idx = _vertex_polygon(doc)
    for k, v in (
        (VA.key(VA.FROM_X, 1), "0"),
        (VA.key(VA.FROM_Y, 1), "0"),
        (VA.key(VA.TO_X, 1), "0"),
        (VA.key(VA.TO_Y, 1), "-130"),
    ):
        assert doc.ensure_property(idx, k, v)
    doc.save()
    doc = EditableDocument.open(path)
    idx = _vertex_polygon(doc)
    dlg = _sheet(doc, idx)
    ed = dlg.vertices_editor
    ed.select_vertex(1)
    assert ed.expr.inner_text() == "FX_LEVEL1"
    assert ed.sp[VA.TO_Y].value() == -130 and ed.sp[VA.FROM_X].value() == 0
    assert VA.edits(doc.object_properties(idx), ed._anim0, ed._anim) == []  # untouched


def test_stale_vertex_keys_past_the_last_node_never_overwrite_a_moved_vertex(doc):
    """CtDraw's own node delete leaves vertex keys by number, so real polygons
    carry keys past their last vertex. An insert moves vertex 2's keys to 3: the stale
    key 3 must move on to 4, not overwrite them; a delete moves it down."""
    i = doc.create_object("polygon", nodes=[(0, 0), (100, 0), (100, 100)])
    for k, v in (
        ("VERTEX_VERTFROMX2", "5"),
        ("VERTEX_VERTEXP2", "Tag2"),
        ("VERTEX_VERTFROMX3", "0"),
    ):  # key 3: past the last node
        assert doc.ensure_property(i, k, v)
    assert doc.insert_object_node(i, 1, (50, 0))
    vk = {k: v for k, v in doc.object_properties(i).items() if k.startswith("VERTEX_")}
    assert vk == {"VERTEX_VERTFROMX3": "5", "VERTEX_VERTEXP3": "Tag2", "VERTEX_VERTFROMX4": "0"}
    assert doc.delete_object_nodes(i, [0])
    vk = {k: v for k, v in doc.object_properties(i).items() if k.startswith("VERTEX_")}
    assert vk == {"VERTEX_VERTFROMX2": "5", "VERTEX_VERTEXP2": "Tag2", "VERTEX_VERTFROMX3": "0"}
    assert VA.renumber_edits({"VERTEX_VERTFROMX3": "0"}, VA.insert_mapping(3, 3), 4) == [
        ("VERTEX_VERTFROMX3", None),
        ("VERTEX_VERTFROMX4", "0"),
    ]


def test_vertices_page_opened_before_a_canvas_reshape(doc):
    """The sheet is modeless: the canvas can reshape the polygon while it is
    open. Same node count -> only the vertices typed on the page change; a
    node added / deleted on the canvas -> Apply is refused with the reason
    (the page's list and vertex numbers no longer match the object)."""
    i = doc.create_object("polygon", nodes=[(100, 100), (200, 100), (150, 180)])
    dlg = _sheet(doc, i)
    assert doc.move_object_nodes(i, [0], -10, 0)  # the canvas, sheet still open
    dlg.vertices_editor.set_vertex(1, 210, 90)
    assert dlg.apply()
    assert _nodes(doc, i) == [(90, 100), (210, 90), (150, 180)]
    dlg = _sheet(doc, i)
    ed = dlg.vertices_editor
    assert doc.insert_object_node(i, 1, (150, 95))
    ed.select_vertex(2)
    ed.expr.setText("Tag1")
    n = len(doc._undo)
    assert not dlg.apply()
    assert len(doc._undo) == n
    assert "changed on the canvas" in dlg._status.text()
    assert _nodes(doc, i) == [(90, 100), (150, 95), (210, 90), (150, 180)]
    assert not any(k.startswith("VERTEX_") for k in doc.object_properties(i))


def test_undo_and_tab_switch_clear_the_selected_nodes(win, fx_page, tmp_path):
    """Node numbers refer to one version of one page: after an undo / redo
    or on another tab a kept selection would make Delete remove whichever
    node now has that number."""
    i = _new(win, "polygon", [(300, 300), (400, 300), (400, 380), (300, 380)])
    view = win.view
    _click(view, (400, 300))
    assert win.doc.insert_object_node(i, 2, (400, 340))
    win._refresh()
    view.set_node_selection(i, [2])
    win.undo()
    assert view.node_selection() is None
    win.redo()
    view.set_node_selection(i, [2])
    other = tmp_path / "other" / (HOST + ".ctg")
    other.parent.mkdir()
    shutil.copyfile(fx_page(HOST), other)
    win.open_document(str(other))
    assert view._node_owner is None and not view._node_sel


def test_arrow_keys_move_the_selected_nodes(win):
    """CtDraw's canvas WM_KEYDOWN: while nodes are selected (state 0x200) every
    arrow / diagonal key -- plain, Shift, Ctrl, Ctrl+Shift -- goes to
    and moves the selected nodes by the key step (no resize);
    without selected nodes the keys move / resize the object as before."""
    doc = win.doc
    i = _new(win, "polygon", [(300, 300), (400, 300), (400, 380), (300, 380)])
    win._nudge_step = 5
    _click(win.view, (400, 300))
    n = len(doc._undo)
    _canvas_shortcut(win, "Right").activated.emit()
    assert _nodes(doc, i) == [(300, 300), (405, 300), (400, 380), (300, 380)]
    assert len(doc._undo) == n + 1  # one undo step per key
    _canvas_shortcut(win, "Shift+Down").activated.emit()  # not a resize here
    _canvas_shortcut(win, "Ctrl+Up").activated.emit()
    _canvas_shortcut(win, "Ctrl+Shift+Left").activated.emit()
    _canvas_shortcut(win, "Home").activated.emit()  # diagonal: -1, -1
    assert _nodes(doc, i) == [(300, 300), (395, 295), (400, 380), (300, 380)]
    assert win.view.node_selection() == (i, [1])
    win.view.clear_node_selection()
    _canvas_shortcut(win, "Right").activated.emit()  # no nodes: the object moves
    win.commit_nudge()
    assert _nodes(doc, i) == [(305, 300), (400, 295), (405, 380), (305, 380)]


def test_right_click_away_from_a_node_deselects_the_nodes(win):
    """'To deselect every node, click anywhere other than on a node': a right
    click too, so the context menu's Delete deletes the object the user sees
    selected. A right click ON a selected node keeps it."""
    i = _new(win, "polygon", [(300, 300), (400, 300), (400, 380), (300, 380)])
    view = win.view
    view.contextRequested.disconnect()  # no modal menu in the test
    right = QtCore.Qt.RightButton

    def right_press(sp):
        vp = view.mapFromScene(QtCore.QPointF(*sp))
        view.mousePressEvent(
            QtGui.QMouseEvent(
                QtCore.QEvent.MouseButtonPress,
                QtCore.QPointF(vp),
                QtCore.QPointF(view.viewport().mapToGlobal(vp)),
                right,
                right,
                NOMOD,
            )
        )

    _click(view, (400, 300))
    right_press((400, 300))
    assert view.node_selection() == (i, [1])
    right_press((350, 340))
    assert view.node_selection() is None
    assert view.selection_roots() == [i]
