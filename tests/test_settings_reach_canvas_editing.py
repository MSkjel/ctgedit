"""Canvas and editing settings reach every place they should."""

import contextlib
import gc
import os
import shutil
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

MB = 1024 * 1024


def _pump(n=3):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture
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


class _Pens:
    """A painter stand-in that keeps the width of every pen set (the canvas's paint methods
    take any painter)."""

    def __init__(self):
        self.widths = []

    def setPen(self, pen):  # noqa: N802 -- the painter's name
        if isinstance(pen, QtGui.QPen):
            self.widths.append(round(pen.widthF(), 3))

    def testRenderHint(self, *_a):  # noqa: N802
        return False

    def __getattr__(self, name):
        return lambda *a, **k: None


def _doc(fx_install, name="FxCopy.ctg"):
    from ctgedit.editing import EditableDocument

    dst = os.path.join(fx_install.project("FxMain"), name)
    shutil.copyfile(fx_install.page("FxShapes"), dst)
    return EditableDocument.open(str(dst))


# ------------------------------------------------------------------ Selection outlines
def _mark_widths(v):
    """The pen widths of the marks that follow Selection outlines, at zoom 1."""
    from ctgedit.ui.panels import changes_dock, history_dock

    accent = QtGui.QColor(0, 120, 215)
    rect = QtCore.QRectF(-500, -500, 1000, 1000)
    p = _Pens()
    # the move held on one axis, the smart guides
    v._constrain, v._moving = ("x", QtCore.QPointF(5, 5)), True
    v._sg_active = [("v", 10.0), ("h", 20.0)]
    v.SMART_GUIDE_OUTLINE = False
    v._paint_smart_guides(p, rect, 1.0, accent)
    v._constrain, v._moving, v._sg_active = None, False, []
    # the selection box
    v._marquee, v._marquee_touch = QtCore.QRectF(0, 0, 50, 40), False
    v._paint_selection(p, rect, 1.0, accent)
    v._marquee = None
    # the rotate ghost, the zoom box, the drawing preview
    v._rotating = {"box": QtCore.QRectF(0, 0, 40, 20), "centre": QtCore.QPointF(20, 10), "steps": 0}
    v._paint_rotate_ghost(p, 1.0, accent)
    v._rotating = None
    v._zbox = {"origin": QtCore.QPoint(10, 10), "cur": QtCore.QPoint(80, 60)}
    v._paint_zoom_box(p)
    v._zbox = None
    v._tool, v._nodes = "rectangle", []
    v._draw_start, v._draw_cur = QtCore.QPointF(0, 0), QtCore.QPointF(40, 30)
    v._draw_mods = QtCore.Qt.NoModifier
    v._paint_draw_preview(p, rect, 1.0, accent)
    v._draw_start = v._draw_cur = None
    # the Undo flash and the change marks
    history_dock._FLASH[v] = ([(0, 0, 10, 10)], time.monotonic() + 60)
    changes_dock._MARKS[v] = ([("new", (0, 0, 10, 10))], (0, 0, 10, 10))
    try:
        history_dock._paint_flash(v, p, rect)
        changes_dock._paint_marks(v, p, rect)
    finally:
        history_dock._FLASH.pop(v, None)
        changes_dock._MARKS.pop(v, None)
    return p.widths


def test_selection_outlines_widen_the_other_canvas_marks(view):
    """Medium and thick add 1 and 2 px to the smart guides, the selection and zoom boxes,
    the drawing preview, the ghosts, the Undo flash and the change marks; thin adds nothing."""
    from ctgedit.ui.editor import canvas_look as C_look

    from ctgedit.ui.editor import canvas_look as C_look

    try:
        C_look.set_canvas_lines(outline="thin")
        thin = _mark_widths(view)
        C_look.set_canvas_lines(outline="thick")
        thick = _mark_widths(view)
        C_look.set_canvas_lines(outline="medium")
        medium = _mark_widths(view)
    finally:
        C_look.set_canvas_lines()
    assert len(thin) >= 8 and len(thin) == len(thick) == len(medium)
    assert 1.0 in thin and 1.5 in thin and 2.0 in thin
    assert thick == [w + 2.0 for w in thin], (thin, thick)
    assert medium == [w + 1.0 for w in thin], (thin, medium)


# ------------------------------------------------------------------ Messages stay
def test_messages_stay_times_the_canvas_notes(view):
    """The arrow keys' readout, an Alt+click's note and flash, and the alignment lines after
    an arrow key stay 0.7, 1 or 1.6 times as long."""
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()

    def times():
        view.show_key_pill("10, 20  (+1, 0)")
        view.flash_pill("2 of 5: Text", QtCore.QPoint(20, 20))
        view._flash_outline([0])
        # (an arrow-key burst whose box lines up with a target)
        view.scene()._items = {}
        view._smart_guides, view._moving = True, False
        view._nudge = {"guides": True, "off": QtCore.QPointF(0, 0)}
        view._sg_bbox = QtCore.QRectF(10, 10, 20, 20)
        view._sg_targets_x, view._sg_targets_y = [10.0], []
        view.flash_alignment_guides()
        view._nudge = None
        out = (
            view._key_pill_timer.interval(),
            view._flash_timer.interval(),
            view._flash_unit_timer.interval(),
            view._sg_flash.interval(),
        )
        view.hide_key_pill()
        for t in (view._flash_timer, view._flash_unit_timer, view._sg_flash):
            t.stop()
        return out

    assert times() == (800, 1500, 600, 900)
    st.setValue("ui/message_time", "long")
    assert times() == (1280, 2400, 960, 1440)
    st.setValue("ui/message_time", "short")
    assert times() == (560, 1050, 420, 630)


# ------------------------------------------------------------------ Text size
class _FarScene(QtWidgets.QGraphicsScene):
    """A scene whose page is out of view (the Page chip shows)."""

    def page_rect(self):
        return QtCore.QRectF(3000, 3000, 200, 100)


def test_text_size_sizes_the_canvas_readouts_and_the_page_chip(view, monkeypatch):
    from ctgedit.ui.style import theme

    view.setScene(_FarScene(-500, -500, 4000, 4000, view))
    view.centerOn(0, 0)
    _pump()

    def sizes():
        pill = view._pill_box(QtCore.QPoint(50, 50), "100, 200")
        chip = view.page_chip_rect()
        assert chip is not None
        return pill.height(), pill.width(), chip.height()

    monkeypatch.setitem(theme._state, "scale", 1.0)
    small = sizes()
    monkeypatch.setitem(theme._state, "scale", 1.5)
    big = sizes()
    assert all(b > s for b, s in zip(big, small)), (small, big)


# ------------------------------------------------------------------ the focus frame
def test_show_the_keyboard_focus_always_shows_the_canvas_frame(view, monkeypatch):
    from ctgedit.ui.style import theme

    view.activateWindow()
    view.setFocus(QtCore.Qt.MouseFocusReason)
    _pump()
    if not view.hasFocus():
        pytest.skip("the offscreen window does not take the focus")
    view.focusInEvent(QtGui.QFocusEvent(QtCore.QEvent.FocusIn, QtCore.Qt.MouseFocusReason))
    monkeypatch.setitem(theme._state, "focus_ring", "keyboard")
    assert not view.keyboard_focus_shown()  # (a click: no frame)
    monkeypatch.setitem(theme._state, "focus_ring", "always")
    assert view.keyboard_focus_shown()


# ------------------------------------------------------------------ the wheel
def test_a_notch_scrolls_the_lines_windows_says(view):
    """Shift + the wheel, a sideways wheel and a touchpad's notch scroll as many lines as
    the plain wheel does in Qt's own scroll (Windows' mouse setting)."""
    from ctgedit.ui.editor import viewnav

    hints = QtWidgets.QApplication.instance().styleHints()
    before = hints.wheelScrollLines()
    bar = view.horizontalScrollBar()
    try:
        hints.setWheelScrollLines(7)
        assert viewnav.wheel_lines() == 7
        assert viewnav.notch_px(120, bar) == 7 * max(1, bar.singleStep())
        dx, _dy = viewnav.wheel_scroll_px(view, 240, 0, "notch")
        assert dx == -14 * max(1, bar.singleStep())
        hints.setWheelScrollLines(1)
        assert viewnav.notch_px(120, bar) == 1 * max(1, bar.singleStep())
    finally:
        hints.setWheelScrollLines(before)


def test_a_notch_scrolls_a_screen_or_nothing_when_windows_says_so(view, monkeypatch):
    """Windows' 0 lines is no scroll (Qt's own plain-wheel scroll stays put too), and only
    'One screen at a time' (WHEEL_PAGESCROLL: below 0 as Qt reports it, or more than 100
    lines) scrolls a screen a notch."""
    from ctgedit.ui.editor import viewnav

    assert viewnav.wheel_lines(0) == 0
    assert viewnav.wheel_lines(5) == 5 and viewnav.wheel_lines(100) == 100
    assert viewnav.wheel_lines(-1) == viewnav.WHEEL_SCREEN
    assert viewnav.wheel_lines(0xFFFF) == viewnav.WHEEL_SCREEN
    bar = view.horizontalScrollBar()
    monkeypatch.setattr(viewnav, "wheel_lines", lambda: 0)
    assert viewnav.notch_px(120, bar) == 0
    assert viewnav.wheel_scroll_px(view, 240, 120, "notch") == (0, 0)
    monkeypatch.setattr(viewnav, "wheel_lines", lambda: viewnav.WHEEL_SCREEN)
    assert viewnav.notch_px(240, bar) == 2 * max(1, bar.pageStep())


# ------------------------------------------------------------------ the size of a click
def _click(view, tool):
    got = []
    view.drawCommitted.connect(lambda t, payload: got.append((t, payload)))
    view.set_tool(tool)
    view._draw_start = QtCore.QPointF(100, 100)
    view._commit_box_or_line(QtCore.QPointF(100, 100))
    return got[-1][1]["rect"] if got else None


def test_the_button_tools_style_holds_the_size_of_a_click(view):
    from ctgedit.ui.editor import draw_styles as DS

    assert _click(view, "button") == (100, 100, 210, 136)  # (the factory size)
    DS.set_click_size("button", 120, 40)
    assert DS.click_size("button") == (120, 40)
    assert _click(view, "button") == (100, 100, 220, 140)
    # the look is not the size: a style change keeps it, style_for never gives it
    DS.set_tool_field("button", "font_size", 12)
    assert DS.click_size("button") == (120, 40)
    assert "click_w" not in DS.style_for("button", "button") and DS.tool_style("button") == {"font_size": 12}
    DS.set_click_size("button", 5000, 0)
    assert DS.click_size("button") == (DS.CLICK_SIZE_RANGE[1], DS.CLICK_SIZE_RANGE[0])
    # Reset to the factory look: the factory size again
    DS.reset_tool("button")
    assert DS.click_size("button") == (110, 36) and DS.tool_style("button") == {}


def test_new_button_shows_the_size_of_a_click(fx_install):
    _app()
    from ctgedit.ui.editor import draw_styles as DS
    from ctgedit.ui.panels.properties import PropertyPanel

    doc = _doc(fx_install)
    panel = PropertyPanel()
    panel.resize(300, 900)
    panel.show()
    try:
        panel.set_target(doc, [])
        _pump()
        panel.set_tool("button")
        _pump()
        assert panel.section("New Button") is not None

        def field(key):
            return next(
                w for w in panel.widget().findChildren(QtWidgets.QWidget) if w.property("propKey") == key
            )

        w, h = field("click_w"), field("click_h")
        assert (w.value(), h.value()) == (110, 36)
        w.setValue(120)
        w.editingFinished.emit()
        _pump()
        field("click_h").setValue(40)
        field("click_h").editingFinished.emit()
        _pump()
        assert DS.click_size("button") == (120, 40)
        panel._reset_tool("button")
        _pump()
        assert DS.click_size("button") == (110, 36)
        # a text and a number are fitted to their text: no size of a click
        panel.set_tool("text")
        _pump()
        assert not any(
            x.property("propKey") == "click_w" for x in panel.widget().findChildren(QtWidgets.QWidget)
        )
    finally:
        panel.close()
        panel.deleteLater()


# ------------------------------------------------------------------ the typing preview
def test_a_new_text_is_typed_in_its_styles_font(view):
    from ctgedit.render.scene import font_px
    from ctgedit.ui.editor import draw_styles as DS

    view.set_tool("text")
    view._begin_placement(QtCore.QPointF(10, 10))
    f = view._inplace.font()
    assert f.family() == "Arial" and f.pixelSize() == font_px(14)  # (a click's text)
    view.finish_text_edit(False)
    DS.set_tool_style("text", {"font": "Verdana", "font_size": 20, "bold": True, "italic": True})
    view._begin_placement(QtCore.QPointF(10, 10), "A")
    f = view._inplace.font()
    assert f.family() == "Verdana" and f.pixelSize() == font_px(20)
    assert f.bold() and f.italic() and not f.underline()
    view.finish_text_edit(False)
    # Last used: the last text's look
    DS.set_mode("last")
    DS.set_last_field("text", "font_size", 9)
    assert view.new_text_font().pixelSize() == font_px(9)


# ------------------------------------------------------------------ the paste offset
def test_dropped_pictures_land_the_paste_offset_apart(monkeypatch):
    from ctgedit.ui.dialog import bitmap_import as BI
    from ctgedit.ui.window.commands import Commands

    monkeypatch.setattr(BI, "open_source", lambda p: p)
    monkeypatch.setattr(BI, "add_recent", lambda paths: None)
    placed = []

    class Doc:
        @contextlib.contextmanager
        def batch(self, label):
            yield

    class Bar:
        def showMessage(self, *a):  # noqa: N802
            pass

    class W:
        doc, _paste_offset = Doc(), 25

        def _pictures_from(self, sources, trigger, failed):
            return [types.SimpleNamespace(path="") for _s in sources]

        def _place_picture(self, pic, where):
            placed.append(where)
            return len(placed)

        def _refresh(self):
            pass

        def statusBar(self):  # noqa: N802
            return Bar()

    w = W()
    Commands._import_files(w, ["a.png", "b.png", "c.png"], at=(100, 100), trigger="drop")
    assert placed == [(100, 100), (125, 125), (150, 150)]
    placed.clear()
    w._paste_offset = 0
    Commands._import_files(w, ["a.png", "b.png"], at=(100, 100), trigger="drop")
    assert placed == [(100, 100), (100, 100)]


# ------------------------------------------------------------------ Undo memory
def test_undo_memory_caps_one_open_page(fx_install, monkeypatch):
    """With one page open the page keeps two thirds of what Undo memory says: 256 MB at the
    384 MB default, more at 4096 MB and less at 64 MB."""
    from ctgedit import editing
    from ctgedit.ui.prefs import settings

    monkeypatch.setattr(editing, "UNDO_BYTES_TOTAL", editing.UNDO_BYTES_TOTAL)
    gc.collect()
    st = settings.app_settings()
    doc = _doc(fx_install)
    root = doc.page.roots()[0]

    def steps(n, size):
        for _k in range(n):
            doc.move_objects([root], 1, 0)
        for c in doc._undo:  # (each step as large as a big page's)
            c.nbytes = size
        doc.move_objects([root], 1, 0)  # (the newest step measures itself)

    st.setValue("opt/undo_mb", 4096)
    settings.apply_undo_budget()
    steps(20, 20 * MB)  # 400 MB of history: all kept
    assert len(doc._undo) == 21 and not getattr(doc, "undo_trimmed", 0)
    for c in doc._undo:
        c.nbytes = 0
    st.setValue("opt/undo_mb", 64)
    settings.apply_undo_budget()
    steps(10, 4 * MB)  # 31 steps of 4 MB: 10 fit in 42.7 MB
    assert len(doc._undo) == 11
    assert editing._undo_total(doc) <= 64 * MB * 2 // 3
    st.remove("opt/undo_mb")  # the default, 384 MB
    settings.apply_undo_budget()
    assert settings.undo_mb() == 384 and editing._undo_page_cap() == 256 * MB
    steps(20, 20 * MB)  # 31 steps of 20 MB: 12 fit in 256 MB
    assert len(doc._undo) == 13
    assert 240 * MB <= editing._undo_total(doc) <= 256 * MB
