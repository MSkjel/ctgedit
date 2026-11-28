"""Wheel settings reach every canvas."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the page the windows open (of FxMain)
BIG = "FxBig"

M = QtCore.Qt
STEP = 1.15  # (the wheel zoom step's default: 15 %)


def _pump(n=4):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _wheel(view, dy, dx=0, mods=M.NoModifier):
    """A real wheel event (no pixel delta, no phase: how Windows reports a mouse, a precision
    touchpad and a Remote Desktop client alike) sent to the canvas's viewport."""
    vp = view.viewport()
    pos = QtCore.QPointF(vp.rect().center())
    ev = QtGui.QWheelEvent(
        pos,
        QtCore.QPointF(vp.mapToGlobal(pos.toPoint())),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(dx, dy),
        M.NoButton,
        mods,
        M.NoScrollPhase,
        False,
    )
    QtWidgets.QApplication.sendEvent(vp, ev)


def _ready(view, zoom=2.0):
    """The view at ``zoom``, scrolled to the middle, with no touchpad burst going on."""
    view.set_zoom(zoom)
    fin = getattr(view, "finish_view_animation", None) or getattr(view, "finish_glide", None)
    if fin is not None:
        fin()
    for bar in (view.horizontalScrollBar(), view.verticalScrollBar()):
        bar.setValue((bar.minimum() + bar.maximum()) // 2)
    view._pad_at = -1.0
    _pump()


def _effect(view, fn):
    """(zoom ratio, horizontal scroll, vertical scroll) of ``fn``."""
    z0 = view.zoom()
    h0, v0 = view.horizontalScrollBar().value(), view.verticalScrollBar().value()
    fn()
    _pump()
    return (view.zoom() / z0, view.horizontalScrollBar().value() - h0, view.verticalScrollBar().value() - v0)


def _burst(view, dy, n=4):
    def go():
        for _ in range(n):
            _wheel(view, dy)

    return go


_WINS = []


@pytest.fixture()
def pages(tmp_path, fx_page, monkeypatch):
    """Two copies of FxBig (one for each window: a page opens in one window only)."""
    _app()
    monkeypatch.setenv("CTG_NO_ANIMATION", "1")
    import ctgedit.ui.editor.viewnav as VN

    monkeypatch.setattr(VN, "PAD_BY_DELTA", True)  # (Windows' rule, on any platform)
    out = []
    for sub in ("a", "b"):
        (tmp_path / sub).mkdir()
        dst = tmp_path / sub / (BIG + ".ctg")
        shutil.copy2(fx_page(BIG), dst)
        out.append(str(dst))
    yield out
    _drop_windows()


@pytest.fixture()
def bare(monkeypatch):
    """Editor windows with no page (the View toggles, the navigation bar)."""
    _app()
    monkeypatch.setenv("CTG_NO_ANIMATION", "1")
    yield
    _drop_windows()


def _drop_windows():
    from ctgedit.ui.main_window import MainWindow

    while _WINS:
        w = _WINS.pop()
        for e in getattr(w, "_docs", []):
            e["doc"].dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.hide()


def _window(path=None):
    """An editor window as a second window or a moved tab makes one: no restore_session."""
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    _WINS.append(w)
    if path:
        w.open_document(path)
    w.resize(1100, 800)
    w.show()
    _pump()
    return w


def _settings():
    from ctgedit.ui.prefs.settings import app_settings

    return app_settings()


# ------------------------------------------------------------------ the reach
def test_a_window_made_without_restore_session_follows_the_mouse_wheel(pages):
    st = _settings()
    st.setValue("opt/wheel_scrolls", True)
    w = _window(pages[0])
    v = w.view
    assert v.WHEEL_SCROLLS is True
    _ready(v)
    ratio, _dh, dv = _effect(v, lambda: _wheel(v, -120))
    assert ratio == pytest.approx(1.0) and dv > 0  # (Scroll: the page scrolls)
    st.setValue("opt/wheel_scrolls", False)
    w2 = _window(pages[1])
    v2 = w2.view
    assert v2.WHEEL_SCROLLS is False
    _ready(v2)
    ratio, _dh, _dv = _effect(v2, lambda: _wheel(v2, 120))
    assert ratio == pytest.approx(STEP, rel=1e-6)  # (Zoom: the page zooms)


def test_a_window_made_without_restore_session_reads_the_window_choices(pages):
    """The other choices the Settings window put on its own window only, and restore_session
    read: the smart guides, the arrow-key step, the paste offset, the opening zoom."""
    st = _settings()
    st.setValue("view/smart_guide_tol", 20.0)
    st.setValue("view/smart_guide_outline", False)
    st.setValue("view/nudge_step", 7)
    st.setValue("opt/paste_offset", 25)
    st.setValue("opt/open_zoom", "fit")
    w = _window()
    assert w.view.SMART_GUIDE_TOL == 20.0 and w.view.SMART_GUIDE_OUTLINE is False
    assert w._nudge_step == 7 and w._paste_offset == 25 and w._open_zoom == "fit"


def test_a_settings_change_reaches_every_open_window(pages):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.prefs.settings import option

    w1, w2 = _window(pages[0]), _window(pages[1])
    assert not w1.view.WHEEL_SCROLLS and not w2.view.WHEEL_SCROLLS
    dlg = SettingsDialog(w1)
    try:
        dlg._wheel.setCurrentIndex(dlg._wheel.findData(True))
        dlg._apply(accept=False)
        assert option("wheel_scrolls")
        assert w1.view.WHEEL_SCROLLS and w2.view.WHEEL_SCROLLS
        v2 = w2.view
        _ready(v2)
        ratio, _dh, dv = _effect(v2, lambda: _wheel(v2, -120))
        assert ratio == pytest.approx(1.0) and dv > 0  # (the other window scrolls)
        # the canvas options (Settings > Canvas's navigation choices) too
        dlg._nav.touchpad.setChecked(True)
        dlg._nav.wheel_step.setValue(30)
        # and the choices each window keeps on itself
        dlg._nudge.setValue(9)
        dlg._tol.setValue(20)
        dlg._sg_outline.setChecked(False)
        dlg._paste_offset.setValue(30)
        other = next(
            dlg._open_zoom.itemData(k)
            for k in range(dlg._open_zoom.count())
            if dlg._open_zoom.itemData(k) != w2._open_zoom
        )
        dlg._open_zoom.setCurrentIndex(dlg._open_zoom.findData(other))
        dlg._apply(accept=False)
        for w in (w1, w2):
            assert w.view.TOUCHPAD_PANS and w.view.WHEEL_ZOOM_STEP == 30
            assert w._nudge_step == 9 and w._paste_offset == 30 and w._open_zoom == other
            assert w.view.SMART_GUIDE_TOL == 20.0 and w.view.SMART_GUIDE_OUTLINE is False
        dlg._wheel.setCurrentIndex(dlg._wheel.findData(False))
        dlg._apply(accept=False)
        assert not w1.view.WHEEL_SCROLLS and not w2.view.WHEEL_SCROLLS
    finally:
        dlg.close()


def test_the_canvas_options_apply_to_every_open_window(pages):
    from ctgedit.ui.editor.canvas_options import CanvasNavControls

    w1, w2 = _window(pages[0]), _window(pages[1])
    st = _settings()
    st.setValue("opt/wheel_scrolls", True)
    st.setValue("opt/touchpad_pans", True)
    CanvasNavControls.apply()
    for w in (w1, w2):
        assert w.view.WHEEL_SCROLLS and w.view.TOUCHPAD_PANS
    st.setValue("opt/wheel_scrolls", False)
    st.setValue("opt/touchpad_pans", False)
    CanvasNavControls.apply(w1)
    for w in (w1, w2):
        assert not w.view.WHEEL_SCROLLS and not w.view.TOUCHPAD_PANS


# ------------------------------------------------------------------ small steps
def test_small_steps_zoom_with_the_default(pages):
    """Nothing stored: the touchpad choice is off, so the 30-unit steps of a smooth-scrolling
    mouse or a Remote Desktop client zoom as the wheel does (step ** (delta / 120))."""
    from ctgedit.ui.prefs.settings import option

    w = _window(pages[0])
    v = w.view
    assert not option("touchpad_pans") and not v.TOUCHPAD_PANS and not v.WHEEL_SCROLLS
    _ready(v)
    ratio, _dh, _dv = _effect(v, _burst(v, 30))
    assert ratio == pytest.approx(STEP ** (4 * 30 / 120.0), rel=1e-6)
    ratio, _dh, _dv = _effect(v, _burst(v, -30))
    assert ratio == pytest.approx(STEP ** (-4 * 30 / 120.0), rel=1e-6)


def test_small_steps_pan_with_the_touchpad_choice_stored_on(pages):
    _settings().setValue("opt/touchpad_pans", True)
    w = _window(pages[0])
    v = w.view
    assert v.TOUCHPAD_PANS
    _ready(v)
    ratio, _dh, dv = _effect(v, _burst(v, -30))
    assert ratio == pytest.approx(1.0) and dv > 0


@pytest.mark.parametrize("touchpad", [False, True])
@pytest.mark.parametrize("scrolls", [False, True])
def test_whole_notches_follow_the_mouse_wheel_in_either_touchpad_setting(pages, touchpad, scrolls):
    st = _settings()
    st.setValue("opt/touchpad_pans", touchpad)
    st.setValue("opt/wheel_scrolls", scrolls)
    w = _window(pages[0])
    v = w.view
    assert v.TOUCHPAD_PANS is touchpad and v.WHEEL_SCROLLS is scrolls
    _ready(v)
    ratio, _dh, dv = _effect(v, lambda: (_wheel(v, -120), _wheel(v, -120)))
    if scrolls:
        assert ratio == pytest.approx(1.0) and dv > 0
    else:
        assert ratio == pytest.approx(STEP**-2, rel=1e-6)
    # Ctrl + the wheel zooms either way
    _ready(v)
    ratio, _dh, _dv = _effect(v, lambda: _wheel(v, 120, mods=M.ControlModifier))
    assert ratio == pytest.approx(STEP, rel=1e-6)


# ------------------------------------------------------------------ the default and the row
def test_restore_defaults_gives_the_touchpad_choice_off(pages):
    from ctgedit.ui.editor import viewnav as VN
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.prefs.settings import OPTION_DEFAULTS, option

    assert OPTION_DEFAULTS["touchpad_pans"] is False
    assert VN.WheelPrefs().touchpad_pans is False
    assert VN.CanvasNavigation.TOUCHPAD_PANS is False
    assert VN.WheelPrefs.from_settings().touchpad_pans is False  # (nothing stored)
    _settings().setValue("opt/touchpad_pans", True)  # (a stored value stays)
    w = _window()
    assert w.view.TOUCHPAD_PANS and VN.WheelPrefs.from_settings().touchpad_pans
    dlg = SettingsDialog(w)
    try:
        assert dlg._nav.touchpad.isChecked()
        dlg.restore_defaults()
        assert not dlg._nav.touchpad.isChecked()
        dlg._apply(accept=False)
        assert not option("touchpad_pans") and not w.view.TOUCHPAD_PANS
    finally:
        dlg.close()


def test_the_touchpad_choice_sits_under_mouse_wheel(pages):
    from ctgedit.ui.editor.canvas_options import TOUCHPAD_TEXT
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    w = _window()
    dlg = SettingsDialog(w)
    try:
        page = next(p for p in dlg._tabs.pages if p.title == "Canvas")
        mouse = next(s for s in page.sections if s.title == "Mouse")
        assert mouse.rows[0].field is dlg._wheel
        row = mouse.rows[1]
        assert row.field is dlg._nav.touchpad and row.key == "opt/touchpad_pans"
        assert row.field.property("ctgText") == TOUCHPAD_TEXT
        for words in (
            "Touchpad",
            "two fingers pan",
            "pinch zooms",
            "smooth-scrolling mouse",
            "Remote Desktop",
        ):
            assert words in TOUCHPAD_TEXT
        assert "Default: Off" in row.field.toolTip()
        zoom = next(s for s in page.sections if s.title == "Zoom and scroll")
        assert all(r.field is not dlg._nav.touchpad for r in zoom.rows)
    finally:
        dlg.close()


# ------------------------------------------------------------------ the Simulate window
def test_the_simulate_window_page_follows_the_default_and_the_stored_choice(pages):
    """The Simulate window's canvas reads the choices at each wheel event
    (WheelPrefs.from_settings): small steps zoom by default, pan with the choice on."""
    from ctgedit.model.document import Page
    from ctgedit.render.scene import PageScene
    from ctgedit.ui.simulate.canvas import SimCanvas

    p = Page(name="wheel")
    p.appearance.width, p.appearance.height = 6000, 6000
    sc = PageScene()
    sc.set_page(p)
    sc.setSceneRect(0, 0, 6000, 6000)
    sim = SimCanvas(sc)
    sim.resize(500, 400)
    sim.show()
    try:
        _ready(sim, 0.5)
        ratio, _dh, _dv = _effect(sim, _burst(sim, 30))
        assert ratio == pytest.approx(STEP ** (4 * 30 / 120.0), rel=1e-6)
        _settings().setValue("opt/touchpad_pans", True)
        _ready(sim, 0.5)
        ratio, _dh, dv = _effect(sim, _burst(sim, -30))
        assert ratio == pytest.approx(1.0) and dv > 0
    finally:
        sim.close()


# ------------------------------------------------------------------ the View toggles
#: Settings > Canvas > "Grid and guidelines"'s rows that are View menu toggles: (the key, the
#: action, what the window or its canvas has)
TOGGLES = (
    ("view/grid", "act_grid", lambda w: w._show_grid),
    ("view/snap", "act_snap", lambda w: w.view._snap),
    ("view/snap_guides", "act_snap_guides", lambda w: w.view.snap_guides()),
    ("view/guides_visible", "act_show_guides", lambda w: w.view.guides_visible()),
    ("view/smart_guides", "act_smart_guides", lambda w: w.view._smart_guides),
)


def _toggles(w) -> list:
    return [(getattr(w, act).isChecked(), bool(has(w))) for _k, act, has in TOGGLES]


def test_a_window_made_without_restore_session_reads_the_view_toggles(bare):
    """Window > New Window and a moved tab make a window without restore_session: it takes
    the grid, the snaps and the smart guides as stored."""
    st = _settings()
    for key, _act, _has in TOGGLES:
        st.setValue(key, False)
    w = _window()
    assert _toggles(w) == [(False, False)] * len(TOGGLES)
    for key, _act, _has in TOGGLES:
        st.setValue(key, True)
    w2 = _window()
    assert _toggles(w2) == [(True, True)] * len(TOGGLES)
    assert _toggles(w) == [(False, False)] * len(TOGGLES)  # (each window keeps its own)


def test_settings_from_a_new_window_keeps_the_stored_view_toggles(bare):
    """Settings opened from a window Window > New Window made shows the stored view toggles,
    and an Apply keeps the user's choices."""
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    st = _settings()
    for key in ("view/snap", "view/grid", "view/smart_guides"):
        st.setValue(key, False)
    w1 = _window()
    w1.restore_session(reopen=False)
    w2 = w1._new_window()
    _WINS.append(w2)
    for w in (w1, w2):
        assert not w.act_snap.isChecked() and not w._show_grid
        assert not w.act_smart_guides.isChecked()
    dlg = SettingsDialog(w2)
    try:
        for key in ("view/snap", "view/grid", "view/smart_guides"):
            assert not dlg._row_widgets[key].isChecked()
        dlg._wheel.setCurrentIndex(dlg._wheel.findData(True))  # (another change)
        dlg._apply(accept=False)
    finally:
        dlg.close()
    w2.save_prefs()  # (the last window to close)
    for key in ("view/snap", "view/grid", "view/smart_guides"):
        assert st.value(key, True, type=bool) is False


@pytest.mark.parametrize("key,act,has", TOGGLES, ids=[k for k, _a, _h in TOGGLES])
def test_a_view_toggle_changed_in_settings_reaches_every_window(bare, key, act, has):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    w1, w2 = _window(), _window()
    getattr(w2, act).trigger()  # (w2 has it off already: F8 there)
    assert getattr(w1, act).isChecked() and not getattr(w2, act).isChecked()
    dlg = SettingsDialog(w1)
    try:
        dlg._row_widgets[key].setChecked(False)
        dlg._apply(accept=False)
        for w in (w1, w2):
            assert not getattr(w, act).isChecked() and not has(w)
        assert _settings().value(key, True, type=bool) is False
        dlg._row_widgets[key].setChecked(True)
        dlg._apply(accept=False)
        for w in (w1, w2):
            assert getattr(w, act).isChecked() and has(w)
    finally:
        dlg.close()


def test_an_import_keeps_the_view_toggles_it_brings(bare, tmp_path):
    """An apply after an import keeps the imported view toggles, not the window's own state."""
    from ctgedit.ui.prefs import settings_transfer as T
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    st = _settings()
    st.setValue("view/snap", False)
    st.setValue("view/smart_guides", False)
    path = str(tmp_path / "exported.ini")
    T.export_groups(path)
    st.setValue("view/snap", True)
    st.setValue("view/smart_guides", True)
    w1, w2 = _window(), _window()
    w1.act_grid.trigger()  # (the import has view/grid on: this one stays)
    assert w1.act_snap.isChecked() and not w1.act_grid.isChecked()
    dlg = SettingsDialog(w1)
    try:
        assert dlg.import_from(path)
        assert st.value("view/snap", True, type=bool) is False
        assert st.value("view/smart_guides", True, type=bool) is False
        for w in (w1, w2):
            assert not w.act_snap.isChecked() and not w.view._snap
            assert not w.act_smart_guides.isChecked() and not w.view._smart_guides
        assert not w1.act_grid.isChecked() and w2.act_grid.isChecked()
    finally:
        dlg.close()


# ------------------------------------------------------------------ the navigation bar
def test_the_navigation_bar_position_reaches_every_window(bare):
    from ctgedit.ui.editor.canvas_options import CanvasNavControls

    w1, w2 = _window(), _window()
    assert w1.navbar.position() == w2.navbar.position() == "bottom_centre"
    w1.navbar._store_position("top_centre")
    assert _settings().value("view/navbar_pos") == "top_centre"
    assert w1.navbar.position() == w2.navbar.position() == "top_centre"
    assert CanvasNavControls().navbar_pos.currentData() == "top_centre"
    w2.navbar._store_position("hidden")
    assert w1.navbar.position() == w2.navbar.position() == "hidden"


def test_the_navigation_bar_buttons_reach_every_window(bare):
    w1, w2 = _window(), _window()
    every = w1.navbar.chosen()
    assert "smart_guides" in every and w2.navbar.chosen() == every
    w2.navbar.set_chosen(["zoom", "grid"])
    for w in (w1, w2):
        assert w.navbar.chosen() == {"zoom", "grid"}
        assert not w.navbar._buttons["smart_guides"].isVisibleTo(w.navbar)
    w1.navbar.set_chosen(every)
    for w in (w1, w2):
        assert w.navbar.chosen() == every


# ------------------------------------------------------------------ the manual
def test_the_manual_canvas_keys_say_what_counts_as_a_touchpad():
    """The keys table (Help > Keyboard Shortcuts, On the canvas) says the default and, as the
    Settings, the window and the Simulate pages do, what counts as a touchpad."""
    from ctgedit.ui.dialog.help_window import CANVAS_KEYS

    text = dict(CANVAS_KEYS)["Touchpad: two fingers, pinch"]
    for words in (
        "off by default",
        "Remote Desktop",
        "smooth-scrolling mouse",
        "small scroll steps count as a touchpad",
        "A pinch zooms",
    ):
        assert words in text
