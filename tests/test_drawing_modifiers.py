"""Drawing modifiers and the text tool."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from ctgedit.format import geometry as G  # noqa: E402
from qtkit import app as _app

#: the page every "win" test edits: FxMain's FxTplBlank in the fake install (an 800 x 600
#: page on fx_style blank@SVGA with one text at (40, 40)): the canvas from 60 to 500 is empty
PAGE = "FxTplBlank"

CTRL = QtCore.Qt.ControlModifier
SHIFT = QtCore.Qt.ShiftModifier
NOMOD = QtCore.Qt.NoModifier
LEFT = QtCore.Qt.LeftButton


# ------------------------------------------------------------------ helpers
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


def _dblclick(view, sp, mods=NOMOD):
    view.mouseDoubleClickEvent(_me(view, QtCore.QEvent.MouseButtonDblClick, sp, LEFT, mods))


def _drag(view, a, b, mods=NOMOD):
    _press(view, a, mods)
    _move(view, b, mods)
    _release(view, b, mods)


@pytest.fixture()
def canvas():
    """A bare canvas (no page): what the drawing tools commit."""
    _app()
    from ctgedit.ui.editor.canvas import CanvasView

    view = CanvasView()
    sc = QtWidgets.QGraphicsScene(0, 0, 800, 600)
    view.setScene(sc)
    view.resize(900, 700)
    view.set_snap(False)
    view.show()
    QtWidgets.QApplication.processEvents()
    got = []
    view.drawCommitted.connect(lambda tool, payload: got.append((tool, payload)))
    view.got = got
    yield view
    view.close()


@pytest.fixture()
def win(fx_page):
    _app()
    from ctgedit.ui.main_window import MainWindow

    dst = fx_page(PAGE)
    w = MainWindow()
    w.open_document(str(dst))
    w.resize(1300, 900)
    w.show()
    w.activateWindow()
    w.view.set_snap(False)
    w.view.set_smart_guides(False)
    # (at 100 % every scene point the tests press maps to a whole viewport pixel and back:
    # the fit zoom of this page would round 500 to 499)
    w.view.set_zoom(1.0)
    QtWidgets.QApplication.processEvents()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.view.finish_text_edit(False)
    w.hide()  # (close() would ask to save the edited page)


def _box(po):
    return G.norm_rect(G.parse_geom_header(po.body_raw).rect1)


def _caption(po):
    return G.parse_text_tail(po)["text"]


def _new(w, kind, rect, text=None):
    i = w.doc.create_object(kind, rect, None, text=text)
    w._refresh()
    w._on_canvas_selection([i])
    w.view.set_selection([i])
    return i


def _type(view, text):
    QTest.keyClicks(view, text)
    QtWidgets.QApplication.processEvents()


# ------------------------------------------------------------ draw modifiers
def test_ctrl_line_is_horizontal_or_vertical(canvas):
    canvas.set_tool("line")
    _drag(canvas, (100, 100), (180, 130), CTRL)
    _drag(canvas, (300, 300), (310, 390), CTRL)
    (t1, p1), (t2, p2) = canvas.got
    (x0, y0), (x1, y1) = p1["nodes"]
    assert (x0, y0) == (100, 100) and y1 == y0 and x1 == 180  # dy == 0
    (x0, y0), (x1, y1) = p2["nodes"]
    assert x1 == x0 and y1 == 390  # dx == 0
    # without Ctrl the line keeps its slope
    _drag(canvas, (100, 100), (180, 130))
    assert canvas.got[-1][1]["nodes"] == [(100, 100), (180, 130)]


@pytest.mark.parametrize("tool", ["rectangle", "ellipse", "rectangle_outline"])
def test_ctrl_box_is_square(canvas, tool):
    canvas.set_tool(tool)
    _drag(canvas, (100, 100), (160, 130), CTRL)
    l, t, r, b = canvas.got[-1][1]["rect"]
    assert r - l == b - t == 60 and (l, t) == (100, 100)
    _drag(canvas, (400, 400), (380, 300), CTRL)  # dragged up-left
    l, t, r, b = canvas.got[-1][1]["rect"]
    assert r - l == b - t == 100 and (r, b) == (400, 400)


def test_shift_box_is_centred_on_the_press(canvas):
    canvas.set_tool("rectangle")
    _drag(canvas, (200, 200), (240, 230), SHIFT)
    assert canvas.got[-1][1]["rect"] == (160, 170, 240, 230)
    # Ctrl+Shift: a square from its centre
    _drag(canvas, (200, 200), (240, 210), CTRL | SHIFT)
    l, t, r, b = canvas.got[-1][1]["rect"]
    assert (l + r) / 2 == 200 and (t + b) / 2 == 200 and r - l == b - t == 80


def test_preview_shows_the_constrained_box(canvas):
    canvas.set_tool("ellipse")
    _press(canvas, (100, 100), CTRL)
    _move(canvas, (150, 120), CTRL)
    assert canvas._pill == "50 × 50"
    r = canvas._box_rect(canvas._draw_start, canvas._snap_pt(canvas._draw_cur), canvas._draw_mods)
    assert r.width() == r.height() == 50
    canvas.grab()  # the preview paints
    _release(canvas, (150, 120), CTRL)


@pytest.mark.parametrize("tool", ["polygon", "pipe"])
def test_dragging_the_first_segment_adds_the_second_vertex(canvas, tool):
    canvas.set_tool(tool)
    _press(canvas, (50, 50))
    _move(canvas, (150, 50))
    _release(canvas, (150, 50))
    assert [(p.x(), p.y()) for p in canvas._nodes] == [(50, 50), (150, 50)]
    # later vertices are clicked; a double-click finishes
    _press(canvas, (150, 120))
    _release(canvas, (150, 120))
    _dblclick(canvas, (150, 120))
    tool_, payload = canvas.got[-1]
    assert tool_ == tool and payload["nodes"] == [(50, 50), (150, 50), (150, 120)]


def test_a_click_on_the_first_vertex_adds_nothing_extra(canvas):
    canvas.set_tool("polygon")
    _press(canvas, (50, 50))
    _release(canvas, (51, 50))  # under 3 px: a click
    assert len(canvas._nodes) == 1


def test_ctrl_polygon_segments_are_horizontal_or_vertical(canvas):
    canvas.set_tool("polygon")
    _press(canvas, (50, 50))
    _release(canvas, (50, 50))
    _press(canvas, (130, 62), CTRL)  # nearly horizontal
    _release(canvas, (130, 62), CTRL)
    _move(canvas, (140, 150), CTRL, buttons=QtCore.Qt.NoButton)
    assert (canvas._draw_cur.x(), canvas._draw_cur.y()) == (130, 150)  # preview
    _press(canvas, (140, 150), CTRL)  # nearly vertical
    _release(canvas, (140, 150), CTRL)
    canvas._commit_nodes()
    assert canvas.got[-1][1]["nodes"] == [(50, 50), (130, 50), (130, 150)]


def test_ctrl_pipe_first_drag_is_constrained(canvas):
    canvas.set_tool("pipe")
    _press(canvas, (20, 20), CTRL)
    _move(canvas, (90, 35), CTRL)
    _release(canvas, (90, 35), CTRL)
    assert [(p.x(), p.y()) for p in canvas._nodes] == [(20, 20), (90, 20)]


# ------------------------------------------------------------ slow mouse
def test_mouse_slow_speed_gears_a_drawn_box(canvas):
    canvas.set_slow_mouse(True)
    canvas.set_tool("rectangle")
    _drag(canvas, (100, 100), (180, 140))
    assert canvas.got[-1][1]["rect"] == (100, 100, 120, 110)  # a quarter
    canvas.set_slow_mouse(False)
    _drag(canvas, (100, 100), (180, 140))
    assert canvas.got[-1][1]["rect"] == (100, 100, 180, 140)


def test_mouse_slow_speed_moves_a_quarter(win):
    i = _new(win, "rectangle", (300, 300, 400, 350))
    win.act_slow_mouse.trigger()
    assert win.view.slow_mouse()
    _press(win.view, (350, 325))
    _move(win.view, (390, 325))
    _release(win.view, (390, 325))
    assert _box(win.doc.parsed.page.objects[i]) == (310, 300, 410, 350)


# ------------------------------------------------------------ Ctrl + corner handle
def test_ctrl_corner_handle_keeps_the_aspect_ratio(win):
    doc = win.doc
    i = _new(win, "rectangle", (300, 300, 400, 350))
    view = win.view
    _press(view, (400, 350))  # bottom-right handle
    assert view._resizing is not None
    _move(view, (460, 360), CTRL)
    _release(view, (460, 360), CTRL)
    QtWidgets.QApplication.processEvents()
    l, t, r, b = _box(doc.parsed.page.objects[i])
    assert (l, t) == (300, 300) and (r - l, b - t) == (160, 80)  # still 2:1
    # Ctrl on an edge handle keeps the proportions too: the other side grows about its
    # middle (the Graphics Builder's edge handles ignore Ctrl)
    win.view.set_selection([i])
    _press(view, (460, 340))  # right-middle
    _move(view, (480, 340), CTRL)
    _release(view, (480, 340), CTRL)
    QtWidgets.QApplication.processEvents()
    assert _box(doc.parsed.page.objects[i]) == (300, 295, 480, 385)
    # without a key the edge handle moves its edge only
    win.view.set_selection([i])
    _press(view, (480, 340))
    _move(view, (500, 340))
    _release(view, (500, 340))
    QtWidgets.QApplication.processEvents()
    assert _box(doc.parsed.page.objects[i]) == (300, 295, 500, 385)


# ------------------------------------------------------------ keyboard
def _canvas_shortcut(w, key):
    seq = key if isinstance(key, QtGui.QKeySequence) else QtGui.QKeySequence(key)
    for sc in w.view.findChildren(QtGui.QShortcut):
        if sc.key() == seq:
            return sc
    raise AssertionError("no canvas shortcut %s" % key)


@pytest.mark.parametrize(
    "key,dx,dy",
    [("Home", -1, -1), ("PgUp", 1, -1), ("End", -1, 1), ("PgDown", 1, 1), ("Left", -1, 0), ("Down", 0, 1)],
)
def test_diagonal_and_arrow_keys_move(win, key, dx, dy):
    i = _new(win, "rectangle", (300, 300, 400, 350))
    win._nudge_step = 5
    _canvas_shortcut(win, key).activated.emit()
    win.commit_nudge()  # (an arrow-key burst reaches the page in one move)
    assert _box(win.doc.parsed.page.objects[i]) == (300 + 5 * dx, 300 + 5 * dy, 400 + 5 * dx, 350 + 5 * dy)


def test_shift_arrow_resizes_anchored_top_left(win):
    doc = win.doc
    i = _new(win, "rectangle", (300, 300, 400, 350))
    win._nudge_step = 5
    n = len(doc._undo)
    _canvas_shortcut(win, "Shift+Right").activated.emit()
    assert _box(doc.parsed.page.objects[i]) == (300, 300, 405, 350)
    _canvas_shortcut(win, "Shift+Up").activated.emit()
    assert _box(doc.parsed.page.objects[i]) == (300, 300, 405, 345)
    _canvas_shortcut(win, "Shift+PgDown").activated.emit()  # both edges
    assert _box(doc.parsed.page.objects[i]) == (300, 300, 410, 350)
    assert len(doc._undo) == n + 3  # one step each
    doc.undo()
    assert _box(doc.parsed.page.objects[i]) == (300, 300, 405, 345)


def test_shift_arrow_can_move_ten_steps_instead(win):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/shift_arrow", "nudge10")
    i = _new(win, "rectangle", (300, 300, 400, 350))
    win._nudge_step = 2
    _canvas_shortcut(win, "Shift+Left").activated.emit()
    win.commit_nudge()
    assert _box(win.doc.parsed.page.objects[i]) == (280, 300, 380, 350)


def test_ctrl_shift_arrow_resizes_proportionally(win):
    doc = win.doc
    i = _new(win, "rectangle", (300, 300, 400, 350))
    win._nudge_step = 10
    _canvas_shortcut(win, "Ctrl+Shift+Right").activated.emit()
    assert _box(doc.parsed.page.objects[i]) == (300, 300, 410, 355)
    _canvas_shortcut(win, "Ctrl+Shift+Up").activated.emit()  # height's factor
    l, t, r, b = _box(doc.parsed.page.objects[i])
    assert (l, t, b - t) == (300, 300, 45) and r - l == round(110 * 45 / 55)


def test_keyboard_resize_scales_several_units_about_their_top_left(win):
    doc = win.doc
    a = doc.create_object("rectangle", (100, 100, 150, 150), None)
    b = doc.create_object("rectangle", (200, 100, 300, 150), None)
    win._refresh()
    win._on_canvas_selection([a, b])
    win.view.set_selection([a, b])
    win._nudge_step = 20
    n = len(doc._undo)
    win.resize_by_keys(1, 0)  # the pair is 200 wide: x1.1
    assert _box(doc.parsed.page.objects[a]) == (100, 100, 155, 150)
    assert _box(doc.parsed.page.objects[b]) == (210, 100, 320, 150)
    assert len(doc._undo) == n + 1


def test_minus_key_deletes_the_selection_once_given_to_delete(win):
    from ctgedit.ui.prefs import keymap

    # (Delete's minus keys are not given by default: Settings > Keyboard gives them)
    keymap.store_user_keys({"act_del": ["Del", "-", "Num+-"]})
    keymap.apply_keymap(win)
    doc = win.doc
    i = _new(win, "rectangle", (300, 300, 400, 350))
    po = doc.parsed.page.objects[i]
    _canvas_shortcut(win, "-").activated.emit()
    assert po not in doc.parsed.page.objects
    doc.undo()
    j = _new(win, "rectangle", (300, 300, 400, 350))
    po = doc.parsed.page.objects[j]
    _canvas_shortcut(win, QtGui.QKeySequence(QtCore.Qt.KeypadModifier | QtCore.Qt.Key_Minus)).activated.emit()
    assert po not in doc.parsed.page.objects


# ------------------------------------------------------------ in-place text
def test_f2_types_a_multi_line_caption_in_place(win):
    doc = win.doc
    i = _new(win, "text", (60, 60, 200, 82), text="Old")
    h0 = _box(doc.parsed.page.objects[i])
    n = len(doc._undo)
    assert win.act_edit_text.shortcut().toString() == "F2"
    win.act_edit_text.trigger()
    view = win.view
    assert view.text_editing()
    assert view.text_edit_item().toPlainText() == "Old"
    _type(view, "Line1")  # replaces the selected caption
    QTest.keyClick(view, QtCore.Qt.Key_Return)
    _type(view, "Line2-")  # '-' is typed, not a delete
    QTest.keyClick(view, QtCore.Qt.Key_Left)  # the caret, not a nudge
    QTest.keyClick(view, QtCore.Qt.Key_Delete)  # a character, not the object
    assert view.text_edit_item().toPlainText() == "Line1\nLine2"
    QTest.keyClick(view, QtCore.Qt.Key_Return, CTRL)  # commit
    QtWidgets.QApplication.processEvents()
    assert not view.text_editing()
    po = doc.parsed.page.objects[i]
    assert _caption(po) == "Line1\r\nLine2"  # CRLF, as CtDraw stores it
    assert doc.page.objects[i].style.text == "Line1\nLine2"
    l, t, r, b = _box(po)
    assert b - t > (h0[3] - h0[1]) * 1.6  # re-measured: two lines
    assert len(doc._undo) == n + 1
    doc.undo()
    assert _caption(doc.parsed.page.objects[i]) == "Old"


def test_escape_cancels_the_in_place_edit(win):
    doc = win.doc
    i = _new(win, "text", (60, 60, 200, 82), text="Keep")
    body = bytes(doc.parsed.page.objects[i].body_raw)
    n = len(doc._undo)
    assert win.edit_text_in_place()
    _type(win.view, "Gone")
    QTest.keyClick(win.view, QtCore.Qt.Key_Escape)
    QtWidgets.QApplication.processEvents()
    assert not win.view.text_editing()
    assert bytes(doc.parsed.page.objects[i].body_raw) == body and len(doc._undo) == n


def test_a_rebuild_or_a_save_keeps_the_typed_text(win):
    doc = win.doc
    i = _new(win, "text", (60, 60, 200, 82), text="A")
    assert win.edit_text_in_place()
    _type(win.view, "Typed")
    win._refresh()  # e.g. a ribbon command
    assert not win.view.text_editing()
    assert _caption(doc.parsed.page.objects[i]) == "Typed"
    assert win.edit_text_in_place()
    _type(win.view, "Saved")
    assert win.save()
    from ctgedit.format.page import parse_ctg

    caps = [
        G.parse_text_tail(po)["text"]
        for po in parse_ctg(doc.path).page.objects
        if po.type_code == 0x05 and G.parse_text_tail(po)
    ]
    assert "Saved" in caps


def test_text_tool_click_opens_the_editor_and_places_the_text(win):
    win._keep_tool = win.view.KEEP_TOOL = False  # (off: back to Select after each)
    doc = win.doc
    n_obj = len(doc.parsed.page.objects)
    n = len(doc._undo)
    win._set_tool("text")
    _press(win.view, (60, 300))
    _release(win.view, (60, 300))
    assert win.view.text_editing() and len(doc.parsed.page.objects) == n_obj
    _type(win.view, "Hi")
    QTest.keyClick(win.view, QtCore.Qt.Key_Return)
    _type(win.view, "there")
    _press(win.view, (500, 500))  # a click elsewhere commits
    QtWidgets.QApplication.processEvents()
    assert not win.view.text_editing()
    assert len(doc.parsed.page.objects) == n_obj + 1 and len(doc._undo) == n + 1
    (i,) = win._sel
    po = doc.parsed.page.objects[i]
    assert _caption(po) == "Hi\r\nthere"
    assert _box(po)[:2] == (60, 300)
    assert win.tool_actions["select"].isChecked()


def test_text_tool_with_nothing_typed_places_nothing(win):
    win._keep_tool = win.view.KEEP_TOOL = False  # (off: back to Select after each)
    doc = win.doc
    n_obj = len(doc.parsed.page.objects)
    win._set_tool("text")
    _press(win.view, (60, 300))
    _release(win.view, (60, 300))
    QTest.keyClick(win.view, QtCore.Qt.Key_Return, CTRL)
    QtWidgets.QApplication.processEvents()
    assert len(doc.parsed.page.objects) == n_obj and not doc.can_undo()
    assert win.tool_actions["select"].isChecked()


def test_text_tool_type_first_then_click_to_place(win):
    """CtDraw's own flow: pick the Text tool, type (the letters are text, not
    tool shortcuts), then click where it goes."""
    win._keep_tool = win.view.KEEP_TOOL = False  # (off: back to Select after each)
    doc = win.doc
    view = win.view
    win._set_tool("text")
    _move(view, (100, 100), buttons=QtCore.Qt.NoButton)
    _type(view, "Rev")  # R / E would be tool shortcuts
    assert view.text_editing() and view.tool() == "text"
    _move(view, (220, 330), buttons=QtCore.Qt.NoButton)  # it rides the cursor
    assert view.text_edit_item().pos() == QtCore.QPointF(220, 330)
    _press(view, (240, 340))  # the click places it
    QtWidgets.QApplication.processEvents()
    (i,) = win._sel
    po = doc.parsed.page.objects[i]
    assert _caption(po) == "Rev" and _box(po)[:2] == (240, 340)
    assert win.tool_actions["select"].isChecked()


def test_in_place_refuses_numbers_and_genie_text(win):
    _i = _new(win, "number", (60, 60, 140, 82), text="LOOP_1_PV")
    assert not win.edit_text_in_place()
    assert "Display Value" in win.statusBar().currentMessage()
    r = _new(win, "rectangle", (60, 100, 140, 140))
    assert not win.edit_text_in_place(r)
    assert not win.view.text_editing()


def test_double_click_edits_text_in_place_unless_set_off(win):
    from ctgedit.ui.prefs.settings import app_settings

    i = _new(win, "text", (60, 60, 200, 82), text="Dbl")
    win._on_canvas_activated(i)  # default: the words, on the page
    assert win.view.text_editing()
    win.view.finish_text_edit(False)
    app_settings().setValue("opt/dblclick_edits_text", False)  # off: the properties sheet
    win._on_canvas_activated(i)
    assert not win.view.text_editing() and ("objprops", i) in win._dialogs
    win._dialogs[("objprops", i)].reject()


def test_button_caption_in_place(win):
    doc = win.doc
    i = _new(win, "button", (60, 60, 160, 96), text="Go")
    assert win.edit_text_in_place()
    _type(win.view, "Start")
    QTest.keyClick(win.view, QtCore.Qt.Key_Return)
    _type(win.view, "Pump")
    win.view.finish_text_edit(True)
    assert doc.parsed.page.objects[i].raw_properties()["TEXT"] == "Start\r\nPump"


# ------------------------------------------------------------ multi-line editors
def test_properties_panel_caption_takes_several_lines(win):
    from ctgedit.ui.editor.caption_edit import CaptionEdit

    doc = win.doc
    i = _new(win, "text", (60, 60, 200, 82), text="One")
    ed = win.props.findChild(CaptionEdit)
    assert ed is not None and ed.text() == "One"
    ed.setFocus()
    QTest.keyClicks(ed, "X")
    QTest.keyClick(ed, QtCore.Qt.Key_Return)  # a new line, not "done"
    QTest.keyClicks(ed, "Y")
    assert _caption(doc.parsed.page.objects[i]) == "One"
    QTest.keyClick(ed, QtCore.Qt.Key_Return, CTRL)
    assert _caption(doc.parsed.page.objects[i]).endswith("X\r\nY")


def test_object_sheet_caption_takes_several_lines(win):
    from ctgedit.ui.editor.caption_edit import CaptionEdit

    doc = win.doc
    i = _new(win, "text", (60, 60, 200, 82), text="Sheet")
    win.object_properties(i)
    dlg = win._dialogs[("objprops", i)]
    assert isinstance(dlg._ed_caption, CaptionEdit)
    dlg._ed_caption.setText("Top\nBottom")
    dlg.apply()
    assert _caption(doc.parsed.page.objects[i]) == "Top\r\nBottom"
    dlg.reject()


def test_caption_edit_keeps_the_line_edit_calls():
    _app()
    from ctgedit.ui.editor.caption_edit import CaptionEdit

    ed = CaptionEdit("a")
    done = []
    ed.editingFinished.connect(lambda: done.append(ed.text()))
    ed.setText("x\ny")
    assert ed.text() == "x\ny"
    QTest.keyClick(ed, QtCore.Qt.Key_Return, CTRL)
    assert done == ["x\ny"]
    ed.setReadOnly(True)
    assert ed.isReadOnly()


# ------------------------------------------------------------ settings
def test_settings_round_trip_shift_arrow_and_double_click(win):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.prefs.settings import app_settings

    d = SettingsDialog(win)
    assert d._shift_arrow.currentData() == "resize"  # CtDraw default
    d._shift_arrow.setCurrentIndex(d._shift_arrow.findData("nudge10"))
    assert d._flags["dblclick_edits_text"].isChecked()  # (on by default)
    d._flags["dblclick_edits_text"].setChecked(False)
    d._apply()
    st = app_settings()
    assert st.value("opt/shift_arrow") == "nudge10"
    assert not st.value("opt/dblclick_edits_text", True, type=bool)
    assert SettingsDialog(win)._shift_arrow.currentData() == "nudge10"


def test_slow_speeds_are_remembered(win):
    win.act_slow_mouse.setChecked(True)
    win.act_slow_keys.setChecked(True)
    win.save_prefs()
    win.act_slow_mouse.setChecked(False)
    win.act_slow_keys.setChecked(False)
    win.restore_session(reopen=False)
    assert win.act_slow_mouse.isChecked() and win.view.slow_mouse()
    assert win.act_slow_keys.isChecked()


def test_altgr_characters_start_the_type_first_text(canvas):
    """Windows reports AltGr as Ctrl+Alt: '@' (the '@(Label)' captions), '{',
    '[' on Nordic / German keyboards must type, not be dropped."""
    from ctgedit.ui.editor.canvas import _typing_key

    altgr = CTRL | QtCore.Qt.AltModifier
    assert _typing_key(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_2, altgr, "@"))
    assert not _typing_key(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_S, altgr, ""))
    assert not _typing_key(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_C, CTRL, "\x03"))
    canvas.set_tool("text")
    canvas.setFocus()
    so = QtGui.QKeyEvent(QtCore.QEvent.ShortcutOverride, QtCore.Qt.Key_2, altgr, "@")
    QtWidgets.QApplication.sendEvent(canvas, so)
    assert so.isAccepted()  # no Ctrl+Alt shortcut steals it
    QtWidgets.QApplication.sendEvent(
        canvas, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_2, altgr, "@")
    )
    assert canvas.text_editing()
    assert canvas.text_edit_item().toPlainText() == "@"
    canvas.finish_text_edit(False)


def test_text_edit_abandons_a_half_drawn_box(canvas):
    canvas.set_tool("rectangle")
    _press(canvas, (100, 100))
    _move(canvas, (160, 150))
    assert canvas._draw_start is not None
    canvas.begin_text_edit(QtCore.QPointF(10, 10), "x", QtGui.QFont("Arial", 10), {"kind": "edit"})
    assert canvas._draw_start is None and canvas._pill is None
    _release(canvas, (160, 150))
    assert canvas.got == []  # the release drew nothing
    canvas.finish_text_edit(False)


def test_page_keys_scroll_when_nothing_is_selected(win):
    view = win.view
    win._on_canvas_selection([])
    view.set_selection([])
    view.zoom_by(4.0)
    QtWidgets.QApplication.processEvents()
    sb = view.verticalScrollBar()
    sb.setValue(sb.minimum())
    assert sb.maximum() > sb.minimum()
    _canvas_shortcut(win, "PgDown").activated.emit()
    assert sb.value() > sb.minimum()  # the page scrolled down
    v = sb.value()
    _canvas_shortcut(win, "PgUp").activated.emit()
    assert sb.value() < v


def test_undo_while_typing_takes_back_the_typing(win):
    """The ribbon / menu Undo does not take the focus from the editor: the
    typing is stored first, so Undo reverts it and Redo brings it back
    (stored after the undo it would land on the undone page and wipe the
    redo history)."""
    doc = win.doc
    i = _new(win, "text", (60, 60, 200, 82), text="Base")
    j = _new(win, "rectangle", (300, 300, 400, 350))  # an earlier step
    win._on_canvas_selection([i])
    win.view.set_selection([i])
    assert win.edit_text_in_place(i)
    _type(win.view, "Typed")
    win.undo()
    assert not win.view.text_editing()
    objs = doc.parsed.page.objects
    assert _caption(objs[i]) == "Base" and len(objs) > j  # rectangle kept
    assert doc.can_redo()
    win.redo()
    assert _caption(doc.parsed.page.objects[i]) == "Typed"


def test_placed_text_reaches_the_objects_tree_on_a_geometry_refresh(win):
    win._set_tool("text")
    _press(win.view, (60, 300))
    _release(win.view, (60, 300))
    _type(win.view, "Tree")
    win._refresh(rebuild_tree=False)  # e.g. a move commit
    (i,) = win._sel
    assert i in win.objects_dock._items


def test_caption_field_keeps_editing_keys_from_the_window_shortcuts(win):
    """Delete / Ctrl+A in the multi-line caption edit the text, not the page."""
    from ctgedit.ui.editor.caption_edit import CaptionEdit

    doc = win.doc
    i = _new(win, "text", (60, 60, 200, 82), text="Keep")
    po = doc.parsed.page.objects[i]
    ed = win.props.findChild(CaptionEdit)
    ed.setFocus()
    QtWidgets.QApplication.processEvents()
    ed.moveCursor(QtGui.QTextCursor.Start)
    QTest.keyClick(ed, QtCore.Qt.Key_Delete)
    QTest.keyClick(ed, QtCore.Qt.Key_A, CTRL)
    QtWidgets.QApplication.processEvents()
    assert po in doc.parsed.page.objects  # not deleted
    assert ed.text() == "eep"
    assert ed.textCursor().selectedText() == "eep"


def test_tab_commits_the_in_place_text_without_typing_a_tab(win):
    """QGraphicsTextItem types Tab from sceneEvent: it must leave the text
    (a caption holds no tab), not insert one."""
    doc = win.doc
    i = _new(win, "text", (60, 60, 200, 82), text="Old")
    assert win.edit_text_in_place()
    _type(win.view, "New")
    QTest.keyClick(win.view, QtCore.Qt.Key_Tab)
    QtWidgets.QApplication.processEvents()
    assert not win.view.text_editing()
    assert _caption(doc.parsed.page.objects[i]) == "New"


def test_f2_while_typing_stores_the_text(win):
    doc = win.doc
    i = _new(win, "text", (60, 60, 200, 82), text="Old")
    assert win.edit_text_in_place()
    _type(win.view, "First")
    QTest.keyClick(win.view, QtCore.Qt.Key_F2)
    QtWidgets.QApplication.processEvents()
    assert not win.view.text_editing()
    assert _caption(doc.parsed.page.objects[i]) == "First"
    # opening another edit while one is open shows the STORED caption
    assert win.edit_text_in_place(i)
    _type(win.view, "Second")
    assert win.edit_text_in_place(i)
    assert win.view.text_edit_item().toPlainText() == "Second"
    win.view.finish_text_edit(False)


@pytest.mark.parametrize("tool", ["polygon", "pipe"])
def test_esc_cancels_a_half_drawn_shape_and_backspace_takes_a_point_back(canvas, tool):
    """Esc cancels the shape being drawn, as the help and the shortcut list say
    ; Backspace takes the last point back."""
    canvas.set_tool(tool)
    msgs = []
    canvas.statusMessage.connect(msgs.append)
    for p in ((50, 50), (150, 50), (150, 120), (60, 130)):
        _press(canvas, p)
        _release(canvas, p)
    n = len(canvas._nodes)
    QTest.keyClick(canvas, QtCore.Qt.Key_Backspace)
    assert len(canvas._nodes) == n - 1
    QTest.keyClick(canvas, QtCore.Qt.Key_Escape)
    assert not canvas._nodes
    assert not [g for g in canvas.got if g[0] == tool]
    assert msgs and "cancelled" in msgs[-1]
