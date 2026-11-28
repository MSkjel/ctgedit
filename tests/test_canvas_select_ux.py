"""Selecting on the canvas."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the near-empty page (the synthetic rectangles go OY below it) and the page of many units
BLANK = "FxTplBlank"
MANY = "FxMany"

CTRL = QtCore.Qt.ControlModifier
SHIFT = QtCore.Qt.ShiftModifier
ALT = QtCore.Qt.AltModifier
NOMOD = QtCore.Qt.NoModifier
LEFT = QtCore.Qt.LeftButton


def _pump(n=4):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _open(tmp_path, src):
    _app()
    dst = tmp_path / os.path.basename(src)
    shutil.copy2(src, dst)
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(str(dst))
    w.resize(1400, 1000)
    w.show()
    _pump()
    return w


def _close(w):
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.main_window import MainWindow

    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.view.finish_text_edit(False)
    w.hide()
    C_look.set_modifier_scheme("builder", "ctrl")
    C_look.set_handle_size("medium")


@pytest.fixture()
def many(tmp_path, fx_page):
    """The main window on a copy of FxMain's FxMany."""
    w = _open(tmp_path, fx_page(MANY))
    yield w
    _close(w)


@pytest.fixture()
def about(tmp_path, fx_page):
    """The main window on a copy of FxMain's FxTplBlank, snap and smart guides off."""
    w = _open(tmp_path, fx_page(BLANK))
    w.view.set_snap(False)
    w.view.set_smart_guides(False)
    yield w
    _close(w)


def _me(view, kind, vp, buttons=QtCore.Qt.NoButton, mods=NOMOD):
    vp = QtCore.QPoint(vp)
    g = view.viewport().mapToGlobal(vp)
    return QtGui.QMouseEvent(kind, QtCore.QPointF(vp), QtCore.QPointF(g), LEFT, buttons, mods)


def _press(view, vp, mods=NOMOD):
    view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, vp, LEFT, mods))


def _move(view, vp, mods=NOMOD):
    view.mouseMoveEvent(_me(view, QtCore.QEvent.MouseMove, vp, LEFT, mods))


def _release(view, vp, mods=NOMOD):
    view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, vp, QtCore.Qt.NoButton, mods))


def _drag(view, a, b, mods=NOMOD, steps=4):
    a, b = QtCore.QPoint(a), QtCore.QPoint(b)
    _press(view, a, mods)
    for k in range(1, steps + 1):
        _move(view, a + (b - a) * (k / steps), mods)
    _release(view, b, mods)
    _pump(2)


def _click(view, vp, mods=NOMOD):
    _press(view, vp, mods)
    _release(view, vp, mods)
    _pump(1)


def _vp(view, x, y):
    return view.mapFromScene(QtCore.QPointF(x, y))


def _centre_of(win, root):
    g = win.doc.page.objects[root].geometry
    return g.x + g.extent_x / 2.0, g.y + g.extent_y / 2.0


def _show(view, x, y, zoom=None):
    if zoom is not None:
        view.set_zoom(zoom)
    view.centerOn(QtCore.QPointF(x, y))
    _pump(2)


#: the synthetic objects lie this far below the page, where nothing else is
OY = 2000


def _new_rect(win, rect, colour=0x3366CC, off=True):
    """A rectangle at ``rect`` (moved OY below the page with ``off``), the view on it."""
    l, t, r, b = rect
    dy = OY if off else 0
    i = win.doc.create_object("rectangle", (l, t + dy, r, b + dy), None, colour=colour)
    win._refresh()
    if off:
        win.view.set_zoom(1.0)
        win.view.centerOn(QtCore.QPointF(420, OY + 320))
        _pump(1)
    return i


def _at(view, x, y):
    """The viewport point of (x, y) of the off-page area."""
    return view.mapFromScene(QtCore.QPointF(x, y + OY))


def _named(win, name):
    """The record of the object named ``name`` (the catalogue's names are unique)."""
    found = win.doc.find_objects_by_name(name)
    assert len(found) == 1, (name, found)
    return found[0]


#: the thin bar: 12 x 133, across the middle of the stack FxStackA .. FxStackC (FxMany's
#: 690 .. 780 x 390 .. 460), so its centre (735, 426) has four units under it
BAR = (729, 360, 741, 493)


def _thin_bar(win):
    """A 12 x 133 filled rectangle drawn over FxMany's stack."""
    l, t, r, b = BAR
    i = win.doc.create_object("rectangle", (l, t, r, b), None, colour=0x20A040)
    win._refresh()
    g = win.doc.page.objects[i].geometry
    assert (g.extent_x, g.extent_y) == (12, 133)
    return i


# ================================================================== Alt+click and Alt+drag
def _stack_point(win):
    """A point of the canvas with two or more selectable units under it (FxMany's stack
    FxStackA .. FxStackC is in the first view)."""
    v = win.view
    vp = v.viewport().rect()
    for y in range(40, vp.height() - 40, 12):
        for x in range(40, vp.width() - 40, 12):
            units = v.units_under(QtCore.QPoint(x, y))
            if len(units) >= 2:
                return QtCore.QPoint(x, y), units
    pytest.fail("no stack of objects in view (FxMany has FxStackA .. FxStackC)")


def test_alt_drag_moves_the_selected_unit_not_the_one_on_top(many):
    w, v = many, many.view
    v.set_snap(False)
    at, units = _stack_point(w)
    under = units[1]  # the unit BELOW the topmost one
    v.select_units([under], reveal=False)
    root = min(under)
    top = min(units[0])
    g0 = w.doc.page.objects[root].geometry
    before = (g0.x, g0.y)
    other = w.doc.page.objects[top].geometry
    other0 = (other.x, other.y)
    _drag(v, at, at + QtCore.QPoint(0, 40), ALT)
    g = w.doc.page.objects[root].geometry
    assert (g.x, g.y) != before and g.x == before[0]  # the selected unit moved
    o = w.doc.page.objects[top].geometry
    assert (o.x, o.y) == other0  # the one on top did not
    assert v.selection_roots() == [root]


def test_alt_click_cycles_through_the_stack_with_a_pill(many):
    """The thin bar lies on the stack FxStackA .. FxStackC: five Alt+clicks go round the
    four units under its middle."""
    w, v = many, many.view
    bar = _thin_bar(w)
    cx, cy = _centre_of(w, bar)
    _show(v, cx, cy, 1.09)
    at = _vp(v, cx, cy)
    stack = [min(u) for u in v.units_under(at)]
    assert len(stack) == 4 and bar in stack
    v.select_units([w.view._resolve_unit(bar)], reveal=False)
    seen = []
    for _k in range(5):
        _click(v, at, ALT)
        assert v._resizing is None  # (the bar's middle is no handle)
        seen.append(v.selection_roots()[0])
    k0 = stack.index(bar)
    assert seen == [stack[(k0 + 1 + k) % len(stack)] for k in range(5)]
    text = v._flash_pill[0]
    assert text.startswith("%d of %d: " % ((k0 + 5) % len(stack) + 1, len(stack)))
    assert v._flash_unit  # outlined over everything for a moment
    v._end_flash_unit()
    v._end_flash_pill()
    assert not v._flash_unit and v._flash_pill is None


def test_a_press_in_the_middle_of_a_thin_bar_moves_it(many):
    w, v = many, many.view
    bar = _thin_bar(w)
    cx, cy = _centre_of(w, bar)
    _show(v, cx, cy, 1.09)
    v.select_units([v._resolve_unit(bar)], reveal=False)
    at = _vp(v, cx, cy)
    assert v._handle_at(at) == (None, None)
    _, box = v._handle_box()
    ids = {hid for hid, _x, _y in v._resize_handles(box)}
    assert ids.isdisjoint({1, 5})  # (no top / bottom middle handles)
    for hid, hx, hy, _s in v._handle_specs(box):
        if hid in (3, 7):  # left / right: just outside the bar
            assert not box.left() < hx < box.right()
    _press(v, at)
    assert v._resizing is None and v._maybe_drag
    _release(v, at)
    # a handle outside the bar still resizes it
    hid, hx, hy, _s = next(s for s in v._handle_specs(box) if s[0] == 3)
    assert v._handle_at(v.mapFromScene(QtCore.QPointF(hx, hy)))[0] == 3


# ================================================================== the selection box
def test_shift_box_adds_ctrl_box_toggles_and_a_plain_box_replaces(about):
    w, v = about, about.view
    a = _new_rect(w, (300, 300, 340, 330))
    b = _new_rect(w, (400, 300, 440, 330))
    c = _new_rect(w, (500, 300, 540, 330))
    v.select_units([[a], [b]], reveal=False)
    # Shift + a box round the third: three
    _drag(v, _at(v, 490, 290), _at(v, 550, 340), SHIFT)
    assert v.selection_roots() == [a, b, c]
    assert w._sel == [a, b, c]
    # Ctrl + a box round b and c: they go, a stays
    _drag(v, _at(v, 390, 290), _at(v, 550, 340), CTRL)
    assert v.selection_roots() == [a]
    # Ctrl + a box round b again: it comes back
    _drag(v, _at(v, 390, 290), _at(v, 450, 340), CTRL)
    assert v.selection_roots() == [a, b]
    # a plain box replaces
    _drag(v, _at(v, 490, 290), _at(v, 550, 340))
    assert v.selection_roots() == [c]


def test_the_box_previews_what_it_takes_and_draws_enclose_solid_touch_dashed(about):
    w, v = about, about.view
    a = _new_rect(w, (300, 300, 340, 330))
    b = _new_rect(w, (400, 300, 440, 330))
    v.set_marquee_mode("auto")
    _press(v, _at(v, 290, 290))
    _move(v, _at(v, 360, 340))
    _move(v, _at(v, 450, 340))
    assert v._box_timer.isActive()
    v._update_box_preview()
    assert sorted(min(u) for u in v.box_preview()) == [a, b]
    assert v._pill == "2 objects" and not v._marquee_touch
    v.viewport().grab()  # (the preview paints)
    _release(v, _at(v, 450, 340))
    assert v.box_preview() == [] and v._pill is None
    # right to left: Touch
    _press(v, _at(v, 420, 290))
    _move(v, _at(v, 330, 340))
    v._update_box_preview()
    assert v._marquee_touch and v._pill == "2 objects touched"
    v.viewport().grab()
    _release(v, _at(v, 330, 340))
    # the preview can be turned off
    v.BOX_PREVIEW = False
    _press(v, _at(v, 290, 290))
    _move(v, _at(v, 450, 340))
    assert not v._box_timer.isActive() and v.box_preview() == []
    _release(v, _at(v, 450, 340))


def test_esc_during_a_shift_box_gives_the_selection_back(about):
    w, v = about, about.view
    a = _new_rect(w, (300, 300, 340, 330))
    _new_rect(w, (400, 300, 440, 330))
    v.select_units([[a]], reveal=False)
    _press(v, _at(v, 390, 290), SHIFT)
    _move(v, _at(v, 450, 340), SHIFT)
    assert v._marquee is not None and v._marquee_mods & SHIFT
    v.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, NOMOD))
    _release(v, _at(v, 450, 340), SHIFT)
    assert v.selection_roots() == [a]


def test_shift_drag_inside_a_background_rectangle_boxes_what_is_on_it(many):
    """A filled panel drawn under FxMany's six pumps (FxManyPump1 .. 6 lie in 420 .. 700 x
    520 .. 708) and sent to the back: a Shift+drag from the panel boxes the pumps."""
    w, v = many, many.view
    made = w.doc.create_object("rectangle", (405, 505, 715, 723), None, colour=0x708090)
    w._reorder([made], "back")
    (panel,) = w._sel
    o = w.doc.page.objects[panel]
    assert o.kind.value == "rectangle" and panel < _named(w, "FxManyPump1")
    w._set_selection([])
    g = o.geometry
    at0 = (g.x, g.y)
    _show(v, g.x + g.extent_x / 2.0, g.y + g.extent_y / 2.0, 1.0)
    # a press where the panel itself is on top, near its top-left corner
    a = None
    for k in range(4, 80, 3):
        p = _vp(v, g.x + k, g.y + k)
        u = v._topmost_unit(p)
        if u and min(u) == panel:
            a = p
            break
    assert a is not None, "the panel's corner is covered"
    b = _vp(v, g.x + g.extent_x - 4, g.y + g.extent_y - 4)
    box = QtCore.QRectF(v.mapToScene(a), v.mapToScene(b)).normalized()
    inside = []
    for r in w.doc.page.roots():
        unit = w._unit_for(r)
        rr = v._unit_rect(unit) if unit and r != panel else None
        if rr is not None and box.contains(rr):
            inside.append(r)
    assert inside and any(w.doc.page.objects[r].is_genie for r in inside)
    pumps = {_named(w, "FxManyPump%d" % k) for k in range(1, 7)}
    assert pumps <= set(inside)
    _drag(v, a, b, SHIFT)
    got = v.selection_roots()
    assert panel not in got and set(inside) <= set(got)
    g2 = w.doc.page.objects[panel].geometry
    assert (g2.x, g2.y) == at0  # the panel did not move


def _inside(win, root, box):
    g = win.doc.page.objects[root].geometry
    l, t, r, b = box
    return g.extent_x > 0 and l <= g.x and t <= g.y and g.x + g.extent_x <= r and g.y + g.extent_y <= b


def test_a_shift_click_on_an_object_still_adds_it_and_takes_it_out(about):
    w, v = about, about.view
    a = _new_rect(w, (300, 300, 340, 330))
    b = _new_rect(w, (400, 300, 440, 330))
    v.select_units([[a]], reveal=False)
    _click(v, _at(v, 420, 315), SHIFT)
    assert v.selection_roots() == [a, b]
    _click(v, _at(v, 420, 315), SHIFT)
    assert v.selection_roots() == [a]
    # Ctrl+click adds at once, so a drag right away moves both
    _press(v, _at(v, 420, 315), CTRL)
    assert v.selection_roots() == [a, b] and v._maybe_drag
    _release(v, _at(v, 420, 315), CTRL)
    _click(v, _at(v, 420, 315), CTRL)
    assert v.selection_roots() == [a]


def test_a_drag_from_a_large_object_boxes_with_the_setting(about):
    w, v = about, about.view
    _new_rect(w, (0, 0, 4, 4))  # (the work area reaches down here)
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(420, OY + 320))
    _pump(1)
    seen = v.mapToScene(v.viewport().rect()).boundingRect()
    assert seen.top() > 1000
    big = _new_rect(
        w,
        (int(seen.left()) + 5, int(seen.top()) + 5, int(seen.right()) - 5, int(seen.bottom()) - 5),
        off=False,
    )
    c = seen.center()
    small = _new_rect(w, (int(c.x()) - 20, int(c.y()) - 20, int(c.x()) + 20, int(c.y()) + 20), off=False)
    assert small > big  # (drawn later: on top)
    v.centerOn(c)
    _pump(1)
    start = _vp(v, c.x() - 60, c.y() - 60)
    end = _vp(v, c.x() + 60, c.y() + 60)

    def where():
        g = w.doc.page.objects[big].geometry
        return g.x, g.y

    at0 = where()
    # the default: the drag moves the big object
    v.BOX_FROM_BACKGROUND = False
    v.select_units([], reveal=False)
    _drag(v, start, end)
    assert where() != at0
    w.undo()
    assert where() == at0
    # with the setting: a box takes the small one, the big one stays
    v.BOX_FROM_BACKGROUND = True
    v.select_units([], reveal=False)
    _drag(v, start, end)
    assert where() == at0 and v.selection_roots() == [small]
    # a click still selects it
    _click(v, start)
    assert v.selection_roots() == [big]


def test_lock_as_background_is_offered_for_a_large_filled_rectangle(about):
    from ctgedit.ui.window import context_menu as CM

    w, _v = about, about.view
    ap = w.doc.page.appearance
    big = _new_rect(w, (0, 0, int((ap.width or 1024) * 0.8), int((ap.height or 768) * 0.8)))
    w._set_selection([big])
    menu = CM.build_canvas_menu(w, QtCore.QPoint(-50, -50), [big])
    act = next((a for a in menu.actions() if a.text() == "Lock as Background"), None)
    assert act is not None
    n = len(w.doc._undo)
    act.trigger()
    assert w._ct_locked(big) and not w._sel and len(w.doc._undo) == n + 1
    assert "padlock" in w.statusBar().currentMessage()
    small = _new_rect(w, (600, 600, 640, 640))
    w._set_selection([small])
    menu = CM.build_canvas_menu(w, QtCore.QPoint(-50, -50), [small])
    assert "Lock as Background" not in [a.text() for a in menu.actions()]


# ================================================================== the handles' look
def test_handles_are_white_with_the_selection_colour_and_take_the_size_setting(about):
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.style import theme

    w, v = about, about.view
    theme.apply_theme(_app(), "light", 1.0)
    try:
        theme.reload_canvas_colours()
        assert theme.canvas_colour("selection").name() == theme.LIGHT["accent"]
        i = _new_rect(w, (300, 300, 500, 400), colour=0x202020)
        v.select_units([[i]], reveal=False)
        v.set_zoom(1.0)
        v.centerOn(QtCore.QPointF(400, OY + 350))
        _pump(2)
        img = v.viewport().grab().toImage()
        dpr = img.devicePixelRatio() or 1.0
        c = v.mapFromScene(QtCore.QPointF(300, OY + 300))
        hs = C_look.handle_size()
        assert hs == 8.0

        def px(x, y):
            return QtGui.QColor(img.pixel(int(x * dpr), int(y * dpr)))

        assert px(c.x(), c.y()).lightness() > 235  # the white fill
        acc = QtGui.QColor(theme.LIGHT["accent"])
        edges = [px(x, c.y()) for x in range(c.x() - 7, c.x() - 1)]
        assert any(
            abs(e.red() - acc.red()) < 40
            and abs(e.green() - acc.green()) < 40
            and abs(e.blue() - acc.blue()) < 40
            for e in edges
        ), [e.name() for e in edges]
        w.view.SELECTION_BOX = True
        C_look.set_handle_size("large")
        assert C_look.handle_size() == 11.0
        specs = dict((h, s) for h, _x, _y, s in v._handle_specs(v._handle_box()[1]))
        assert specs[0] == 11.0 and specs[1] == 9.0  # edge handles 2 px smaller
        C_look.set_handle_size("small")
        assert C_look.handle_size() == 6.0
    finally:
        C_look.set_handle_size("medium")
        theme.apply_theme(_app(), "dark", 1.0)
        theme.reload_canvas_colours()


def test_the_canvas_has_no_colour_literals():
    """canvas.py draws with theme roles and theme.fixed (tests/test_theme_literals.py)."""
    import ast
    from ctgedit.ui.editor import canvas as C

    tree = ast.parse(open(C.__file__, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "QColor":
            args = node.args
            assert not (args and isinstance(args[0], ast.Constant)), ast.dump(node)[:80]


# ================================================================== the keyboard
def test_tab_selects_in_reading_order_from_the_top_left(many):
    w, v = many, many.view
    v.setFocus()
    _pump(1)
    w._set_selection([])
    entries = v._reading_order(v._selectable_units())
    assert len(entries) > 3
    tab = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Tab, NOMOD)
    assert v.event(tab)
    first = v.selection_roots()
    assert first == [entries[0][0]]
    # the first is the top-left one: no unit starts in a higher row
    top = min(e[2].top() for e in entries)
    assert entries[0][2].top() < top + max(1.0, entries[0][2].height())
    assert w._sel == first and w.statusBar().currentMessage()
    v.event(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Tab, NOMOD))
    assert v.selection_roots() == [entries[1][0]]
    v.event(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Backtab, SHIFT))
    assert v.selection_roots() == [entries[0][0]]
    # before the first: no wrap, the keyboard leaves the canvas with the first still selected
    v.event(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Backtab, SHIFT))
    assert v.selection_roots() == [entries[0][0]]
    v.setFocus()
    _pump(1)
    # past the last: no wrap either (the keyboard goes on to the next panel)
    v.select_units([entries[-1][1]])
    assert not v.select_next(True) and v.selection_roots() == [entries[-1][0]]
    # drawing order
    v.TAB_ORDER = "drawing"
    w._set_selection([])
    v.select_next(True)
    assert v.selection_roots() == [v._selectable_units()[0][0]]
    # off: Tab moves the keyboard on (the canvas does not take it)
    v.TAB_SELECTS = False
    before = v.selection_roots()
    assert not v._tab_selects()
    v.event(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Tab, NOMOD))
    assert v.selection_roots() == before
    v.TAB_SELECTS = True


def test_tab_skips_what_a_click_cannot_select(many):
    w, v = many, many.view
    roots = [e[0] for e in v._selectable_units()]
    hide = roots[0]
    w.set_roots_hidden([hide], True)
    assert hide not in [e[0] for e in v._selectable_units()]
    w.set_roots_hidden([hide], False)
    # the Filter: genies and groups only
    w.sel_filter.setCurrentIndex(6)
    kinds = {w.doc.page.objects[e[0]].kind.value for e in v._selectable_units()}
    assert kinds and kinds <= {"genie", "composite_genie", "group"}
    w.sel_filter.setCurrentIndex(0)


def test_ctrl_alt_arrows_select_the_nearest_object_that_way(about):
    w, v = about, about.view
    mid = _new_rect(w, (400, 300, 440, 330))
    right = _new_rect(w, (520, 310, 560, 340))
    far_right = _new_rect(w, (700, 300, 740, 330))
    up_right = _new_rect(w, (470, 200, 510, 230))  # nearer but well off to the side
    down = _new_rect(w, (400, 420, 440, 450))
    v.select_units([[mid]], reveal=False)
    assert v.select_direction(1, 0) and v.selection_roots() == [right]
    assert v.select_direction(1, 0) and v.selection_roots() == [far_right]
    v.select_units([[mid]], reveal=False)
    assert v.select_direction(0, 1) and v.selection_roots() == [down]
    assert v.select_direction(0, -1) and v.selection_roots() == [mid]
    assert v.select_direction(0, -1) and v.selection_roots() == [up_right]
    # the canvas keys exist
    seqs = {sc.key().toString() for sc in v.findChildren(QtGui.QShortcut)}
    for k in ("Ctrl+Alt+Left", "Ctrl+Alt+Right", "Ctrl+Alt+Up", "Ctrl+Alt+Down", "Shift+F10"):
        assert k in seqs, k


def test_enter_shift_enter_and_ctrl_enter(many, monkeypatch):
    """FxManyPump1 is a genie with three members."""
    w, v = many, many.view
    genie = _named(w, "FxManyPump1")
    assert w.doc.page.objects[genie].is_genie
    opened = []
    monkeypatch.setattr(w, "_on_canvas_activated", lambda i: opened.append(i))
    v.objectActivated.disconnect()
    v.objectActivated.connect(w._on_canvas_activated)
    v.select_units([v._resolve_unit(genie)], reveal=False)
    key = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Return, NOMOD)
    v.keyPressEvent(key)
    assert opened == [genie]
    # Ctrl+Enter: its first member; Shift+Enter: back to the genie
    v.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Return, CTRL))
    member = v.selection_roots()[0]
    assert member != genie and w.doc.page.objects[member].parent == genie
    v.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Return, SHIFT))
    assert v.selection_roots() == [genie]


def test_shift_f10_and_the_menu_key_open_the_menu_at_the_selection(many, monkeypatch):
    w, v = many, many.view
    got = []
    monkeypatch.setattr(w, "_context_menu", lambda pos, roots: got.append((pos, roots)))
    v.contextRequested.disconnect()
    v.contextRequested.connect(w._context_menu)
    root = _named(w, "FxManyPump1")
    cx, cy = _centre_of(w, root)
    _show(v, cx, cy, 1.0)
    v.select_units([v._resolve_unit(root)], reveal=False)
    sc = next(s for s in v.findChildren(QtGui.QShortcut) if s.key().toString() == "Shift+F10")
    sc.activated.emit()
    assert got and got[-1][1] == [root]
    at = v.viewport().mapFromGlobal(got[-1][0])
    box = v.mapFromScene(v.selection_box()).boundingRect()
    assert box.adjusted(-2, -2, 2, 2).contains(at)
    # the Menu key (a keyboard context-menu event) opens it once more, not twice at once
    v._kbd_menu_closed = 0.0
    p = QtCore.QPoint(5, 5)
    v.contextMenuEvent(QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Keyboard, p, p))
    assert len(got) == 2
    v.contextMenuEvent(QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Keyboard, p, p))
    assert len(got) == 2  # (Shift+F10 can come twice)


def test_a_tree_selection_far_off_the_view_is_scrolled_into_view(many):
    """FxManyPump6 (640, 630) is far off a view at 412 % on (900, 400)."""
    w, v = many, many.view
    target = _named(w, "FxManyPump6")
    o = w.doc.page.objects[target]
    _show(v, 900, 400, 4.12)
    z = v.zoom()
    seen = v.mapToScene(v.viewport().rect()).boundingRect()
    g = o.geometry
    box = QtCore.QRectF(g.x, g.y, g.extent_x, g.extent_y)
    assert not seen.contains(box)
    w._on_dock_selection([target])
    _pump(1)
    seen = v.mapToScene(v.viewport().rect()).boundingRect()
    assert seen.contains(box) and v.zoom() == pytest.approx(z)
    # off: it stays where it is
    from ctgedit.ui.prefs.settings import set_option

    set_option("reveal_selection", False)
    _show(v, 900, 400)
    w._on_dock_selection([_named(w, "FxManyPump5")])
    seen2 = v.mapToScene(v.viewport().rect()).boundingRect()
    assert abs(seen2.center().x() - 900) < 5
    set_option("reveal_selection", True)


def test_the_canvas_says_which_unit_the_pointer_is_over(many):
    w, v = many, many.view
    got = []
    v.hoverChanged.connect(got.append)
    cx, cy = _centre_of(w, _named(w, "FxStackC"))
    _show(v, cx, cy, 1.09)
    v.mouseMoveEvent(_me(v, QtCore.QEvent.MouseMove, _vp(v, cx, cy)))
    v.mouseMoveEvent(_me(v, QtCore.QEvent.MouseMove, _vp(v, cx, cy)))
    assert got and got[-1] and min(got[-1]) in [min(u) for u in v.units_under(_vp(v, cx, cy))]
    n = len(got)
    v.mouseMoveEvent(_me(v, QtCore.QEvent.MouseMove, QtCore.QPoint(2, 2)))
    if not v.units_under(QtCore.QPoint(2, 2)):
        assert len(got) == n + 1 and got[-1] is None


def test_a_locked_object_shows_a_padlock_pointer_and_can_be_unlocked_by_right_click(about):
    from ctgedit.ui.window import context_menu as CM

    w, v = about, about.view
    i = _new_rect(w, (300, 300, 400, 380))
    w._set_selection([i])
    w._lock_toggle()
    assert w._ct_locked(i) and not w._sel
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(350, 340))
    _pump(2)
    at = _at(v, 350, 340)
    msgs = []
    v.statusMessage.connect(msgs.append)
    v.mouseMoveEvent(_me(v, QtCore.QEvent.MouseMove, at))
    assert v._locked_hover == i
    assert msgs and msgs[-1].startswith("Locked: Rectangle AN ") and "Ctrl+L" in msgs[-1]
    assert v.viewport().cursor().shape() == QtCore.Qt.BitmapCursor
    menu = CM.build_canvas_menu(w, v.viewport().mapToGlobal(at), [])
    first = [a for a in menu.actions() if a.text()][0]
    assert first.text().startswith("Unlock Rectangle AN ")
    first.trigger()
    assert not w._ct_locked(i)
    # Ctrl+L with nothing selected unlocks the object under the pointer
    w._set_selection([i])
    w._lock_toggle()
    assert w._ct_locked(i)
    QtGui.QCursor.setPos(v.viewport().mapToGlobal(at))
    if v.locked_under_pointer() == i:
        w._lock_toggle()
        assert not w._ct_locked(i)


def test_a_click_on_one_of_several_selects_only_it_with_the_setting(about):
    w, v = about, about.view
    a = _new_rect(w, (300, 300, 340, 330))
    b = _new_rect(w, (400, 300, 440, 330))
    v.select_units([[a], [b]], reveal=False)
    _click(v, _at(v, 320, 315))
    assert v.selection_roots() == [a, b]  # the default keeps them
    v.CLICK_SELECTED_ISOLATES = True
    _click(v, _at(v, 320, 315))
    assert v.selection_roots() == [a]
    v.select_units([[a], [b]], reveal=False)
    _drag(v, _at(v, 320, 315), _at(v, 340, 315))  # a drag still moves both
    assert v.selection_roots() == [a, b]


def test_the_settings_window_has_one_row_per_new_setting(about):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    d = SettingsDialog(about)
    try:
        for key in (
            "canvas/box_preview",
            "canvas/box_from_background",
            "canvas/click_selected_isolates",
            "opt/tab_selects_objects",
            "sel/tab_order",
            "opt/reveal_selection",
            "canvas/modifier_scheme",
            "canvas/drag_copy_key",
            "edit/smart_duplicate",
            "opt/ctrl_arrow",
            "opt/arrow_grid",
            "opt/arrows_pan",
            "view/handle_size",
            "view/selection_box",
            "canvas/ctrl_shift_drag",
        ):
            assert key in d._row_widgets, key
        assert d.SELECTION_COLOUR_ROW
        d._set_row_value("view/handle_size", "large")
        d._set_row_value("canvas/modifier_scheme", "design")
        d._apply(accept=False)
        from ctgedit.ui.editor import canvas_look as C_look

        assert C_look.handle_size() == 11.0 and C_look.modifier_scheme() == "design"
        d.restore_defaults()
        d._apply(accept=False)
        assert C_look.handle_size() == 8.0 and C_look.modifier_scheme() == "builder"
    finally:
        d.close()


def test_the_one_pass_reading_order_is_the_rows_order_it_replaced():
    """_reading_order walks each row once: the same sequence as the two-pass rows, on random
    layouts with ties and overlaps."""
    import random

    from ctgedit.ui.editor.canvas import CanvasView

    def rows(entries):
        rest = sorted(entries, key=lambda e: (e[2].top(), e[2].left(), e[0]))
        out = []
        while rest:
            first = rest[0]
            limit = first[2].top() + max(1.0, first[2].height()) / 2.0
            row = [e for e in rest if e[2].top() < limit]
            rest = [e for e in rest if e[2].top() >= limit]
            out.extend(sorted(row, key=lambda e: (e[2].left(), e[2].top(), e[0])))
        return out

    rnd = random.Random(7)
    for _case in range(2000):
        n = rnd.randint(0, 40)
        entries = []
        for k in range(n):
            x = rnd.choice([rnd.uniform(0, 300), rnd.randint(0, 6) * 50])
            y = rnd.choice([rnd.uniform(0, 300), rnd.randint(0, 6) * 40])
            entries.append((k, [k], QtCore.QRectF(x, y, rnd.choice([0, 5, 30]), rnd.choice([0, 0.5, 8, 60]))))
        got = [e[0] for e in CanvasView._reading_order(entries)]
        assert got == [e[0] for e in rows(entries)], entries


def test_the_kept_selectable_units_follow_locks_hiding_filters_and_moves(many):
    """Tab's list of selectable units is kept between presses; every change that makes a
    unit selectable or not is seen at once."""
    w, v = many, many.view
    roots = lambda: [e[0] for e in v._selectable_units()]  # noqa: E731
    first = roots()
    assert v._selectable_units() == v._selectable_units()  # (kept)
    r = first[1]
    w._set_selection([r])
    w._lock_selection()
    assert r not in roots()
    w._break_lock = True  # Break Lock Mode
    assert r in roots()
    w._set_selection([r])
    w._unlock_selection()  # (the persisted lock off)
    w._break_lock = False
    assert r in roots()
    w.set_roots_hidden([r], True)
    assert r not in roots()
    w.set_roots_hidden([r], False)
    assert r in roots()
    w._apply_sel_filter(1)  # lines and pipes only
    assert roots() != first
    w._apply_sel_filter(0)
    assert roots() == first
    box = next(e[2] for e in v._selectable_units() if e[0] == r)
    w.doc.move_objects([r], 30, 0)
    w._refresh()
    moved = next(e[2] for e in v._selectable_units() if e[0] == r)
    assert moved.left() == box.left() + 30
    # the smart guides' kept boxes too
    sg = dict(v._sg_page_boxes(v.scene(), v.scene()._page))
    assert sg[r].left() == box.left() + 30


def test_the_indexed_smart_guide_lookup_matches_the_scan():
    """The smart-guide lookup uses a sorted index of the targets: the same delta and guide as
    the scan, ties and equal distances included."""
    import random
    import types

    from ctgedit.ui.editor import canvas_guides as CG

    def scan(targets, edges, tol):
        d = None
        g = 0.0
        for edge in edges:
            for t in targets:
                c = t - edge
                if abs(c) <= tol and (d is None or abs(c) < abs(d)):
                    d, g = c, t
        return d, g

    rnd = random.Random(11)
    holder = types.SimpleNamespace()
    for _case in range(3000):
        n = rnd.randint(0, 60)
        targets = [
            rnd.choice([rnd.randint(0, 40) * 5.0, rnd.uniform(0, 200), rnd.randint(0, 400) / 2.0])
            for _ in range(n)
        ]
        tol = rnd.choice([0.5, 3.0, 6.0, 12.5])
        left = rnd.uniform(-10, 210)
        width = rnd.choice([0.0, 4.0, 10.0, rnd.uniform(0, 80)])
        edges = (left, left + width / 2.0, left + width)
        got = CG._nearest_target(CG._target_index(holder, targets), edges, tol)
        assert got == scan(targets, edges, tol), (targets, edges, tol)


def test_culled_outlines_paint_what_the_full_pass_paints(many):
    """A large selection culls its units by kept bounds before outlining them: every dirty
    area paints the same pixels as asking each unit, also while a drag offsets them."""
    import random

    w, v = many, many.view
    roots = [e[0] for e in v._selectable_units()]
    w._set_selection(list(roots))
    _pump(2)
    sr = v.scene().sceneRect()
    rnd = random.Random(3)

    def paint(area, cull):
        v.OUTLINE_CULL_FROM = 1 if cull else 10**9
        img = QtGui.QImage(int(sr.width()) + 20, int(sr.height()) + 20, QtGui.QImage.Format_ARGB32)
        img.fill(0)
        p = QtGui.QPainter(img)
        p.translate(-sr.left() + 10, -sr.top() + 10)
        p.setClipRect(area)
        v._draw_selection_outlines(p, area, 1.0, QtGui.QColor("#4aa3ff"))
        p.end()
        return img

    try:
        for shift in (None, QtCore.QPointF(13, -7)):
            if shift is not None:
                v._moving = True
                v._move_applied = shift
            for _k in range(25):
                x, y = rnd.uniform(sr.left(), sr.right()), rnd.uniform(sr.top(), sr.bottom())
                area = QtCore.QRectF(x, y, rnd.choice([5, 20, 120]), rnd.choice([5, 20, 120]))
                assert paint(area, True) == paint(area, False), (area, shift)
    finally:
        v._moving = False
        v.OUTLINE_CULL_FROM = type(v).OUTLINE_CULL_FROM


def test_the_objects_panel_count_follows_a_lock_at_once(many):
    """The Objects panel keeps whether each row can be selected; a lock is seen at once."""
    w = many
    od = w.objects_dock
    before = od.listed()
    r = before[1]
    w._set_selection([r])
    w._lock_selection()
    assert r not in od.listed()
    w._break_lock = True
    assert r in od.listed()
    w._break_lock = False


def test_a_large_page_s_rows_come_in_batches_and_every_reader_sees_them_all(many, monkeypatch):
    """A page with many top-level objects gets its Objects rows in batches over the next
    turns; a reader meanwhile (the listed rows, a selection) has every row made first, and
    a tab switch stops the batches without keeping a part of the rows."""
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    w = many
    od = w.objects_dock
    want = od.listed()
    n = len(want)
    assert n > 6
    monkeypatch.setattr(ObjectsDock, "BATCH_FROM", 4)
    monkeypatch.setattr(ObjectsDock, "BATCH_ROWS", 3)
    # (1) a reader at once: every row
    od.set_page(w.doc.page, [])
    assert od._pending_rows is not None
    assert od._tree.topLevelItemCount() == 3
    assert od.listed() == want
    assert od._pending_rows is None
    # (2) the event loop finishes the rows on its own, then the selection is shown
    od.set_page(w.doc.page, [want[-1]])
    for _ in range(50):
        if od._pending_rows is None:
            break
        QtWidgets.QApplication.processEvents()
    assert od._pending_rows is None
    assert od._tree.topLevelItemCount() == len(want)
    assert [it.isSelected() for it in [od._items[want[-1]]]] == [True]
    # (3) a detach while batches run: nothing kept, the rows made again when shown
    od.set_page(w.doc.page, [])
    assert od.detach_rows() is None
    assert od._tree.topLevelItemCount() == 0 and not od._items
    QtWidgets.QApplication.processEvents()
    assert od._tree.topLevelItemCount() == 0  # the stale batch did nothing
    od.set_page(w.doc.page, [])
    assert od.listed() == want
