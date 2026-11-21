"""Canvas settings."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402

from ctgedit.format import geometry as G  # noqa: E402
from qtkit import app as _app

#: the host page the window opens (copies of it)
HOST = "!FxSystem"

NOMOD = QtCore.Qt.NoModifier
LEFT = QtCore.Qt.LeftButton
NOBUTTON = QtCore.Qt.NoButton
#: the test's objects lie this far below the page, where nothing else is
OY = 2000


def _pump(n=3):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _setting(key, value):
    """What the Settings window does after a change: the key stored, its apply hooks run on
    every editor window."""
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.prefs.settings_pages import run_apply_hooks

    app_settings().setValue(key, value)
    run_apply_hooks([key])
    _pump(1)


@pytest.fixture(autouse=True)
def _defaults():
    """The module values the settings set go back to their defaults after each test."""
    yield
    from ctgedit.ui.editor import inplace_text, navigator, viewnav
    from ctgedit.ui.editor import canvas_look as C_look

    C_look.set_pointer_options()
    C_look.set_slow_mouse_factor(None)
    C_look.set_guide_colour(None)
    viewnav.set_edge_options(speed="normal")
    inplace_text.set_enter_stores(False)
    navigator.set_panel_options()


def _window(fx_install, copies=1):
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    for k in (
        "auto_placeholder_missing",
        "auto_update_genies",
        "auto_update_symbols",
        "auto_update_template",
        "props_on_new",
    ):
        st.setValue("opt/" + k, False)
    w = MainWindow()
    for k in range(copies):
        dst = os.path.join(fx_install.project("FxMain"), "fxhost%d.ctg" % k)
        shutil.copy2(fx_install.page(HOST), dst)
        w.open_document(dst)
    w.resize(1400, 1000)
    w.show()
    _pump()
    w.view.set_snap(False)
    w.view.set_smart_guides(False)
    return w


def _close(w):
    from ctgedit.ui.main_window import MainWindow

    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()


@pytest.fixture()
def win(fx_install):
    w = _window(fx_install)
    yield w
    _close(w)


def _me(view, kind, vp, button=LEFT, buttons=NOBUTTON, mods=NOMOD):
    vp = QtCore.QPoint(vp)
    g = view.viewport().mapToGlobal(vp)
    return QtGui.QMouseEvent(kind, QtCore.QPointF(vp), QtCore.QPointF(g), button, buttons, mods)


def _press(view, vp, mods=NOMOD):
    view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, vp, LEFT, LEFT, mods))


def _move(view, vp, mods=NOMOD, buttons=LEFT):
    view.mouseMoveEvent(_me(view, QtCore.QEvent.MouseMove, vp, NOBUTTON, buttons, mods))


def _release(view, vp, mods=NOMOD):
    view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, vp, LEFT, NOBUTTON, mods))


def _click(view, vp):
    _press(view, vp)
    _release(view, vp)
    _pump(1)


def _drag(view, a, b, steps=4, mods=NOMOD):
    a, b = QtCore.QPoint(a), QtCore.QPoint(b)
    _press(view, a, mods)
    for k in range(1, steps + 1):
        _move(view, a + (b - a) * (k / steps), mods)
    _release(view, b, mods)
    _pump(2)


def _key(view, key, mods=NOMOD, text=""):
    QtWidgets.QApplication.sendEvent(view, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, mods, text))
    _pump(2)


def _sp(view, x, y):
    """The viewport point of page point (x, y)."""
    return view.mapFromScene(QtCore.QPointF(x, y))


def _rect(win, l, t, r, b, select=True):
    i = win.doc.create_object("rectangle", (l, t + OY, r, b + OY), None, colour=0x3366CC)
    win._refresh()
    win.view.set_zoom(1.0)
    win.view.centerOn(QtCore.QPointF(420, OY + 320))
    _pump(1)
    if select:
        win._set_selection([i])
        win.view.select_units([[i]], reveal=False)
        _pump(1)
    return i


def _box(win, i):
    return G.norm_rect(G.parse_geom_header(win.doc.parsed.page.objects[i].body_raw).rect1)


# ================================================================== the Settings rows
def test_each_setting_has_one_row_showing_its_default_and_applies_live(win):
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.editor.canvas_options import CANVAS_AUDIT_KEYS
    from ctgedit.ui.prefs.settings import SETTINGS_KEYS
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win)
    dlg.show()
    _pump()
    try:
        rows = [r for r in dlg._tabs.rows() if r.key in CANVAS_AUDIT_KEYS]
        assert sorted(r.key for r in rows) == sorted(CANVAS_AUDIT_KEYS)  # one row each
        for key in CANVAS_AUDIT_KEYS:
            assert dlg._row_value(key) == SETTINGS_KEYS[key], key
        # the edge scroll speed is a sub row of the edge scroll, off with it
        edge = dlg._row_widgets["opt/edge_scroll_speed"]
        dlg._nav.autoscroll.setChecked(False)
        assert not edge.isEnabled()
        dlg._nav.autoscroll.setChecked(True)
        assert edge.isEnabled()
        # a choice applies at once
        snap = dlg._row_widgets["view/guide_snap"]
        snap.setCurrentIndex(snap.findData("far"))
        QtTest.QTest.qWait(400)
        assert win.view.GUIDE_SNAP_PX == 16.0
        # Guideline colour: Custom with a colour of its own
        colour = dlg._row_widgets["view/guide_colour"]
        assert not colour.button.isVisibleTo(colour)  # (the default: no button)
        colour.set_value("#ff0000")
        assert colour.button.isVisibleTo(colour)
        QtTest.QTest.qWait(400)
        c = C_look.guide_colour()
        assert (c.red(), c.green(), c.blue()) == (255, 0, 0)
        from ctgedit.ui.prefs.settings import app_settings

        assert app_settings().value("view/guide_colour") == "#ff0000"
    finally:
        dlg.close()
        dlg.deleteLater()


def test_undo_changes_and_reset_put_a_custom_colour_back(win):
    """A colour picked with Custom is part of its row's state: Undo Changes puts the one the
    window opened with back, and a Custom colour picked and left again is no change."""
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    _setting("view/guide_colour", "#112233")
    _setting("view/selection_colour", "#112233")
    dlg = SettingsDialog(win)
    dlg.show()
    _pump()
    try:
        colour = dlg._row_widgets["view/guide_colour"]
        row = next(r for r in dlg._tabs.rows() if r.key == "view/guide_colour")
        assert colour.value() == "#112233" and dlg.row_modified(row)

        def pick(button, rgb):
            button.set_pair(rgb)  # (what ColourButton.choose does after a pick)
            button.changed.emit()
            QtTest.QTest.qWait(400)

        pick(colour.button, 0x445566)
        assert app_settings().value("view/guide_colour") == "#445566"
        assert dlg.row_modified(row)
        # Selection colour, with its own drop-down and colour button
        pick(dlg._sel_custom, 0x445566)
        assert app_settings().value("view/selection_colour") == "#445566"
        dlg.undo_changes()
        _pump()
        assert colour.value() == "#112233"
        assert app_settings().value("view/guide_colour") == "#112233"
        assert C_look.guide_colour().rgb() == QtGui.QColor("#112233").rgb()
        assert app_settings().value("view/selection_colour") == "#112233"
        # Reset to Default; then Custom, a colour, and the default again: not changed
        dlg.reset_row(row)
        QtTest.QTest.qWait(400)
        assert app_settings().value("view/guide_colour", "default") == "default"
        assert not dlg.row_modified(row)
        colour.combo.setCurrentIndex(colour.combo.findData("custom"))
        pick(colour.button, 0x778899)
        assert dlg.row_modified(row)
        colour.combo.setCurrentIndex(0)
        QtTest.QTest.qWait(400)
        assert not dlg.row_modified(row)
        assert app_settings().value("view/guide_colour", "default") == "default"
    finally:
        dlg.close()
        dlg.deleteLater()


# ================================================================== canvas/pointer_reach
def test_pointer_reach_catches_a_handle_from_further_away(win):
    v = win.view
    i = _rect(win, 300, 300, 400, 350)
    out = _sp(v, 408, OY + 358)  # 8 px past the bottom-right handle
    assert v._handle_at(out) == (None, None)  # Near: as before
    tol = v._node_tol()
    _setting("canvas/pointer_reach", "far")
    hid, idx = v._handle_at(out)
    assert hid == 4 and idx == i
    assert abs(v._node_tol() - (tol + 6.0)) < 1e-6  # the nodes too
    # a press there resizes now (the corner goes where the pointer goes)
    _drag(v, out, _sp(v, 428, OY + 378))
    assert _box(win, i) == (300, OY + 300, 428, OY + 378)


# ================================================================== canvas/drag_start
def test_drag_start_makes_a_short_move_a_click(win):
    v = win.view
    i = _rect(win, 300, 300, 400, 350)
    _drag(v, _sp(v, 350, OY + 325), _sp(v, 355, OY + 325))
    assert _box(win, i) == (305, OY + 300, 405, OY + 350)  # 3 px: 5 px is a move
    _setting("canvas/drag_start", "long")
    _drag(v, _sp(v, 350, OY + 325), _sp(v, 355, OY + 325))
    assert _box(win, i) == (305, OY + 300, 405, OY + 350)  # 10 px: a click
    _drag(v, _sp(v, 350, OY + 325), _sp(v, 365, OY + 325))
    assert _box(win, i) == (320, OY + 300, 420, OY + 350)
    # a drawn box: 6 px is a rectangle with 3 px, a click (no box) with 10 px
    n = len(win.doc.page.objects)
    for value, made in (("short", 1), ("long", 0)):
        _setting("canvas/drag_start", value)
        win.tool_actions["rectangle"].trigger()
        _pump(1)
        _drag(v, _sp(v, 600, OY + 300), _sp(v, 606, OY + 306))
        assert len(win.doc.page.objects) == n + made, value
        n = len(win.doc.page.objects)


def test_the_selection_box_preview_uses_the_release_test(win):
    """A box too small to take anything when let go shows no objects and no count while it
    is dragged either."""
    v = win.view
    i = _rect(win, 306, 306, 309, 309, select=False)
    win._set_selection([])
    v.select_units([], reveal=False)
    for value, taken in (("short", True), ("long", False)):
        _setting("canvas/drag_start", value)
        _press(v, _sp(v, 302, OY + 302))
        for k in (5, 9):  # a 9 x 9 px box round it
            _move(v, _sp(v, 302 + k, OY + 302 + k))
        assert v._marquee is not None
        v._update_box_preview()  # (its timer's work)
        assert bool(v.box_preview()) == taken, value
        assert (v._pill == "1 object") == taken and (v._pill is None) != taken, value
        _release(v, _sp(v, 311, OY + 311))
        _pump(1)
        assert (i in v.selection_roots()) == taken, value
        win._set_selection([])
        v.select_units([], reveal=False)


# ================================================================== view/hover_outline
class _Pens:
    """A painter stand-in that counts the pens set."""

    def __init__(self):
        self.pens = 0

    def setPen(self, pen):  # noqa: N802 -- the painter's name
        self.pens += 1

    def __getattr__(self, name):
        return lambda *a, **k: None


def test_the_hover_outline_can_be_turned_off(win):
    v = win.view
    i = _rect(win, 300, 300, 400, 350, select=False)
    win._set_selection([])
    v.select_units([], reveal=False)
    _move(v, _sp(v, 350, OY + 325), buttons=NOBUTTON)
    assert v._hover_unit and i in v._hover_unit

    def pens():
        p = _Pens()
        v._paint_hover(p, QtCore.QRectF(0, 0, 4000, 4000), v.zoom(), QtGui.QColor(0, 0, 255))
        return p.pens

    assert pens() == 1  # the outline under the pointer
    _setting("view/hover_outline", False)
    assert pens() == 0
    _setting("view/hover_outline", True)
    assert pens() == 1


# ================================================================== view/guide_snap
def test_guide_snap_reach(win):
    v = win.view
    i = _rect(win, 300, 300, 400, 350)
    v.set_guidelines([500], [])
    v.set_guides_visible(True)
    v.set_snap_guides(True)
    _drag(v, _sp(v, 350, OY + 325), _sp(v, 444, OY + 325))
    assert _box(win, i) == (400, OY + 300, 500, OY + 350)  # 6 px off: 8 px snaps
    win.undo()
    _setting("view/guide_snap", "near")
    v.select_units([[i]], reveal=False)
    _drag(v, _sp(v, 350, OY + 325), _sp(v, 444, OY + 325))
    assert _box(win, i) == (394, OY + 300, 494, OY + 350)  # 4 px: no snap


# ================================================================== view/guide_colour
def test_guide_colour_draws_the_guidelines_and_their_ruler_marks(win):
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.style.theme import fixed

    assert C_look.guide_colour(170).rgb() == fixed("guide").rgb()
    assert C_look.guide_colour(170).alpha() == 170
    v = win.view
    v.set_guidelines([420], [])
    v.set_guides_visible(True)
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(420, OY + 320))
    _pump(2)
    col = _sp(v, 420, OY + 320).x()

    def red(c):
        return c.red() - max(c.green(), c.blue()) > 50

    def reds():
        """The reddish pixels along the guideline (its dashes are blended with the desk)."""
        img = v.viewport().grab().toImage()
        return sum(
            1
            for y in range(0, img.height(), 2)
            for x in (col - 1, col, col + 1)
            if red(QtGui.QColor(img.pixel(x, y)))
        )

    def ruler_reds():
        """The reddish pixels of the guideline's mark on the top ruler."""
        ruler = win.rulers.top
        if not ruler.isVisible():
            return None
        img = ruler.grab().toImage()
        return sum(
            1 for y in range(img.height()) for x in range(img.width()) if red(QtGui.QColor(img.pixel(x, y)))
        )

    before, marks = reds(), ruler_reds()
    _setting("view/guide_colour", "#ff0000")
    assert reds() > before + 20  # the dashes are red now
    if marks is not None:
        assert ruler_reds() > marks  # and the mark on the ruler
    assert C_look.guide_colour().rgb() == QtGui.QColor(255, 0, 0).rgb()
    _setting("view/guide_colour", "garbled")
    assert C_look.guide_colour().rgb() == fixed("guide").rgb()


# ================================================================== opt/mouse_back_forward
def _side(view, back=True):
    b = QtCore.Qt.BackButton if back else QtCore.Qt.ForwardButton
    vp = view.viewport().rect().center()
    view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, vp, b, b))
    view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, vp, b, NOBUTTON))
    _pump(2)


def test_the_mouse_side_buttons(fx_install):
    w = _window(fx_install, copies=2)
    try:
        v = w.view
        second = w.doc
        v.set_zoom(1.0)
        v.set_zoom(2.0)
        _pump(1)
        assert v.can_go_back()
        _side(v)  # Previous View
        assert w.doc is second and v.can_go_forward()
        _side(v, back=False)  # Next View
        assert not v.can_go_forward()
        _setting("opt/mouse_back_forward", "tabs")
        _side(v)
        assert w.doc is not second  # the tab before
        _side(w.view, back=False)
        assert w.doc is second  # and back
        _setting("opt/mouse_back_forward", "nothing")
        back = w.view.can_go_back()
        _side(w.view)
        assert w.doc is second and w.view.can_go_back() == back
    finally:
        _close(w)


# ================================================================== opt/page_double_click
def test_a_double_click_on_the_empty_page(fx_install, monkeypatch):
    from ctgedit.ui.main_window import MainWindow

    opened = []
    monkeypatch.setattr(MainWindow, "page_properties", lambda self, *a, **k: opened.append(1))
    w = _window(fx_install)
    try:
        v = w.view
        v.set_zoom(1.0)
        v.centerOn(QtCore.QPointF(420, OY + 320))
        _pump(1)
        vp = _sp(v, 420, OY + 320)
        assert not v._hits_under(vp)  # (nothing there)

        def dbl():
            _click(v, vp)
            v.mouseDoubleClickEvent(_me(v, QtCore.QEvent.MouseButtonDblClick, vp, LEFT, LEFT))
            _release(v, vp)
            _pump(2)

        dbl()
        assert opened == []  # does nothing, as before
        _setting("opt/page_double_click", "page_props")
        dbl()
        assert opened == [1]  # File > Page Properties
        # on an object it is still the object's
        i = _rect(w, 300, 300, 400, 350, select=False)
        got = []
        v.objectActivated.connect(lambda h: got.append(h))
        vp = _sp(v, 350, OY + 325)
        _click(v, vp)
        v.mouseDoubleClickEvent(_me(v, QtCore.QEvent.MouseButtonDblClick, vp, LEFT, LEFT))
        _release(v, vp)
        _pump(2)
        assert got == [i] and opened == [1]
    finally:
        _close(w)


# ================================================================== view/frame_zoom_max
def test_zoom_to_selection_cap(win):
    v = win.view
    _rect(win, 300, 300, 302, 302)
    win.act_zoom_sel.trigger()
    v.finish_view_animation()
    _pump(2)
    assert v.zoom() == pytest.approx(4.0)  # 400 %, as before
    for value, zoom in (("800", 8.0), ("100", 1.0)):
        _setting("view/frame_zoom_max", value)
        v.set_zoom(0.5)
        win.act_zoom_sel.trigger()
        v.finish_view_animation()
        _pump(2)
        assert v.zoom() == pytest.approx(zoom), value
    _setting("view/frame_zoom_max", "0")  # No limit
    win.act_zoom_sel.trigger()
    v.finish_view_animation()
    _pump(2)
    assert v.zoom() > 8.0


# ================================================================== opt/edge_scroll_speed
def test_edge_scroll_speed(win):
    v = win.view
    _rect(win, 300, 300, 400, 350, select=False)
    win._set_selection([])
    v.select_units([], reveal=False)

    def scrolled():
        v.set_zoom(1.0)
        v.centerOn(QtCore.QPointF(420, OY + 320))
        _pump(1)
        r = v.viewport().rect()
        a = QtCore.QPoint(r.center().x() - 100, r.center().y() + 100)  # (empty: a box)
        b = QtCore.QPoint(r.width() - 2, r.center().y() + 100)
        _press(v, a)
        _move(v, a + QtCore.QPoint(20, 0))
        _move(v, b)
        assert v._marquee is not None
        hs = v.horizontalScrollBar()
        x0 = hs.value()
        v._edge_tick()
        x1 = hs.value()
        _release(v, b)
        _pump(1)
        return x1 - x0

    normal = scrolled()
    assert normal > 10
    _setting("opt/edge_scroll_speed", "fast")
    assert scrolled() >= normal * 1.8
    _setting("opt/edge_scroll_speed", "slow")
    assert scrolled() <= normal * 0.6


# ================================================================== the navigator
def test_navigator_size_and_corner(win):
    from ctgedit.ui.style.theme import px

    nav = win.navigator
    navbar = getattr(win, "navbar", None)
    if navbar is not None:
        navbar.hide()
    assert nav._open_size()[0] == px(200) + 8  # Medium, as before
    assert nav.map.sizeHint() == QtCore.QSize(px(200), px(120))
    vp = win.view.viewport()
    nav.reposition()
    assert nav.x() == max(2, vp.width() - nav.width() - 14)  # bottom right
    assert nav.y() == max(2, vp.height() - nav.height() - 14)
    _setting("view/navigator_size", "large")
    assert nav._open_size()[0] == px(280) + 8
    assert nav.map.sizeHint() == QtCore.QSize(px(280), px(168))
    if not nav.shown_collapsed():
        assert nav.width() == px(280) + 8
    _setting("view/navigator_size", "small")
    assert nav._open_size()[0] == px(150) + 8
    _setting("view/navigator_corner", "top_left")
    assert (nav.x(), nav.y()) == (14, 14)
    _setting("view/navigator_corner", "top_right")
    assert (nav.x(), nav.y()) == (max(2, vp.width() - nav.width() - 14), 14)
    _setting("view/navigator_corner", "bottom_left")
    assert (nav.x(), nav.y()) == (14, max(2, vp.height() - nav.height() - 14))


def test_navigator_size_leaves_the_panel_alone(win):
    """Navigator size is the map over the canvas: the View > Panels > Navigator panel keeps
    its size, and the picture they share is never made smaller than the panel's."""
    from ctgedit.ui.editor.navigator import MAP_H, MAP_W
    from ctgedit.ui.style.theme import px

    dock = win.dock_navigator.widget()
    src = win.nav_source
    nav = win.navigator
    nav.set_wanted(False)  # (the panel's map only)
    win.dock_navigator.show()
    _pump()
    assert dock.isVisible()
    for value in ("small", "large"):
        _setting("view/navigator_size", value)
        assert dock.sizeHint() == QtCore.QSize(px(MAP_W), px(MAP_H)), value
        src.rebuild()
        assert src._box == (MAP_W, MAP_H), value  # as before
    _setting("view/navigator_size", "small")
    small = nav.map.sizeHint()
    assert small.width() < px(MAP_W)  # (the map over the canvas)
    # a Large map over the canvas, shown: the picture is made for it
    _setting("view/navigator_size", "large")
    nav.autohide = False
    nav._collapsed = False
    nav._force_open = True
    nav.set_wanted(True)
    _pump()
    if not nav.map.isVisible():
        pytest.skip("the navigator over the canvas does not show here")
    src.ensure()
    _pump()
    assert src._box == (280, 168)
    assert small.width() < nav.map.sizeHint().width()


def test_the_navigator_arrow_points_into_its_corner(win):
    """The collapse arrow points where the map folds: down in a bottom corner, up in a top
    one; collapsed, it points the other way."""
    nav = win.navigator
    up, down = QtCore.Qt.UpArrow, QtCore.Qt.DownArrow
    for corner, fold, unfold in (
        ("bottom_right", down, up),
        ("top_left", up, down),
        ("top_right", up, down),
        ("bottom_left", down, up),
    ):
        _setting("view/navigator_corner", corner)
        for shut in (False, True):
            nav._collapsed = shut
            nav._force_open = not shut
            nav._apply_collapsed()
            assert nav.shown_collapsed() == shut
            assert nav._chevron.arrowType() == (unfold if shut else fold), (corner, shut)
    nav._collapsed = False
    nav._force_open = False


# ================================================================== opt/esc_while_drawing
def test_esc_while_drawing_point_by_point(win):
    v = win.view
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(420, OY + 320))
    _pump(1)
    n0 = len(win.doc.page.objects)

    def draw_then_esc():
        win.tool_actions["polygon"].trigger()
        _pump(1)
        for x, y in ((300, 300), (400, 300), (400, 380)):
            _click(v, _sp(v, x, OY + y))
        assert len(v._nodes) >= 3
        _key(v, QtCore.Qt.Key_Escape)

    draw_then_esc()
    assert len(win.doc.page.objects) == n0 and not v._nodes  # thrown away
    _setting("opt/esc_while_drawing", "finish")
    draw_then_esc()
    assert len(win.doc.page.objects) == n0 + 1  # kept, as Enter keeps it
    from ctgedit.model.document import ObjectKind

    assert win.doc.page.objects[-1].kind == ObjectKind.POLYGON


# ================================================================== view/slow_mouse_factor
def test_mouse_slow_speed_gearing(win):
    v = win.view
    i = _rect(win, 300, 300, 400, 350)
    win.act_slow_mouse.trigger()
    assert v.slow_mouse()
    left = 300
    for value, step in ((None, 10), ("half", 20), ("tenth", 4)):
        if value is not None:
            _setting("view/slow_mouse_factor", value)
        v.select_units([[i]], reveal=False)
        _drag(v, _sp(v, left + 50, OY + 325), _sp(v, left + 90, OY + 325), steps=8)
        assert _box(win, i)[0] == left + step, value  # 40 px of the pointer
        left += step


# ================================================================== opt/text_enter
@pytest.fixture()
def view():
    _app()
    from ctgedit.ui.editor.canvas import CanvasView

    v = CanvasView()
    v.setScene(QtWidgets.QGraphicsScene(-2000, -2000, 4000, 4000, v))
    v.resize(500, 400)
    v.show()
    _pump()
    yield v
    v.close()
    v.deleteLater()


def test_enter_in_a_text_on_the_page(view):
    from ctgedit.ui.editor import inplace_text

    done = []
    view.textEditFinished.connect(lambda *a: done.append(a))

    def edit():
        view.begin_text_edit(QtCore.QPointF(10, 10), "", QtGui.QFont("Arial", 10), {"kind": "edit"})
        _key(view, QtCore.Qt.Key_A, text="a")

    edit()
    _key(view, QtCore.Qt.Key_Return, text="\r")
    assert view.text_editing() and not done  # a new line, as before
    assert view.text_edit_item().toPlainText() == "a\n"
    _key(view, QtCore.Qt.Key_Return, QtCore.Qt.ControlModifier, "\r")
    assert not view.text_editing() and len(done) == 1  # Ctrl+Enter stores it
    inplace_text.set_enter_stores(True)  # (as the window applies it)
    edit()
    _key(view, QtCore.Qt.Key_Return, QtCore.Qt.ShiftModifier, "\r")
    assert view.text_editing() and view.text_edit_item().toPlainText() == "a\n"
    _key(view, QtCore.Qt.Key_Return, text="\r")
    assert not view.text_editing() and len(done) == 2  # Enter stores it


def test_enter_in_the_text_fields_follows_the_setting(win):
    from ctgedit.ui.editor.caption_edit import CaptionEdit

    holder = QtWidgets.QWidget(win)
    ed = CaptionEdit("", holder)
    stored = []
    ed.editingFinished.connect(lambda: stored.append(ed.text()))
    assert ed.toolTip().startswith("Enter starts a new line")
    QtTest.QTest.keyClick(ed, QtCore.Qt.Key_A)
    QtTest.QTest.keyClick(ed, QtCore.Qt.Key_Return)
    assert ed.text() == "a\n" and not stored  # as before
    _setting("opt/text_enter", "store")
    ed2 = CaptionEdit("", holder)
    assert ed2.toolTip().startswith("Shift+Enter starts a new line")
    ed2.editingFinished.connect(lambda: stored.append(ed2.text()))
    QtTest.QTest.keyClick(ed2, QtCore.Qt.Key_B)
    QtTest.QTest.keyClick(ed2, QtCore.Qt.Key_Return, QtCore.Qt.ShiftModifier)
    assert ed2.text() == "b\n" and not stored
    QtTest.QTest.keyClick(ed2, QtCore.Qt.Key_Return)
    assert stored == ["b\n"]
    holder.deleteLater()


def test_the_text_fields_tips_follow_the_setting(win):
    """A text field built before 'In a text, Enter' changes names the new keys: the window's
    own at once, any other when the pointer comes over it or it takes the focus. A fixed tip
    stays."""
    from ctgedit.ui.editor.caption_edit import CaptionEdit
    from ctgedit.ui.editor.inplace_text import enter_hint

    holder = QtWidgets.QWidget(win)
    ed = CaptionEdit("", holder)  # (as the Properties panel's Caption)
    own = CaptionEdit("", holder)
    own.set_tip(lambda: "The text while the condition is TRUE. " + enter_hint())
    fixed = CaptionEdit("", holder)
    fixed.setToolTip("A fixed tip")
    alone = CaptionEdit("")  # (in no window yet)
    assert ed.toolTip().startswith("Enter starts a new line")
    assert own.toolTip().endswith(" Enter starts a new line.")
    _setting("opt/text_enter", "store")
    assert ed.toolTip().startswith("Shift+Enter starts a new line")
    assert own.toolTip().endswith("Shift+Enter starts a new line.")
    assert fixed.toolTip() == "A fixed tip"
    assert alone.toolTip().startswith("Enter starts a new line")  # (not shown yet)
    pt = QtCore.QPointF(5, 5)
    QtWidgets.QApplication.sendEvent(alone, QtGui.QEnterEvent(pt, pt, pt))
    assert alone.toolTip().startswith("Shift+Enter starts a new line")
    _setting("opt/text_enter", "newline")
    QtWidgets.QApplication.sendEvent(alone, QtGui.QFocusEvent(QtCore.QEvent.FocusIn))
    assert alone.toolTip().startswith("Enter starts a new line")
    alone.deleteLater()
    holder.deleteLater()
