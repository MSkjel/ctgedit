"""Theme tokens, controls and focus visuals."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app


def _pump(n=4):
    for _ in range(n):
        _app().processEvents()


@pytest.fixture(autouse=True)
def _own_top_levels():
    """The top-level widgets a test made (a slider, scroll bars, a menu, a tree) are deleted
    after it, before the theme fixture restyles the application."""
    app = _app()
    before = {id(w) for w in app.topLevelWidgets()}
    yield
    for w in list(app.topLevelWidgets()):
        if id(w) in before:
            continue
        try:
            w.hide()
            w.deleteLater()
        except RuntimeError:  # (deleted already)
            continue
    _delete_now()


@pytest.fixture()
def theme():
    """The theme module; the application is put back as it was afterwards."""
    app = _app()
    from ctgedit.ui.style import theme as T
    from ctgedit.render import scene as SC

    snap = dict(
        style=app.style().name(),
        palette=QtGui.QPalette(app.palette()),
        font=QtGui.QFont(app.font()),
        sheet=app.styleSheet(),
        state=dict(T._state),
        colors=dict(T.COLORS),
        hooked=T._orig_set_style_sheet is not None,
        margin=QtGui.QColor(SC.PageScene._MARGIN_COLOUR),
    )
    yield T
    T.apply_theme(app, "dark", 1.0)
    T.COLORS.clear()
    T.COLORS.update(snap["colors"])
    T._state.clear()
    T._state.update(snap["state"])
    T._update_tracked()
    T.reload_canvas_colours()
    if not snap["hooked"]:
        T._uninstall_stylesheet_hook()
    app.setStyleSheet(snap["sheet"])
    app.setPalette(snap["palette"])
    app.setFont(snap["font"])
    if app.style().name() != snap["style"]:
        app.setStyle(snap["style"])
    SC.PageScene._MARGIN_COLOUR = snap["margin"]


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    for d in list(getattr(w, "_dialogs", {}).values()):
        d.close()
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()
    w.deleteLater()
    _pump()
    _delete_now()


def _delete_now():
    """Delete what was given to deleteLater (outside a running event loop processEvents never
    does)."""
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def _set(key, value):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue(key, value)


# --------------------------------------------------------------------- check boxes
def _indicator(widget):
    """The colours in the indicator box of a check box / radio button."""
    widget.resize(widget.sizeHint())
    widget.show()
    _pump()
    img = widget.grab().toImage()
    st = widget.style()
    opt = QtWidgets.QStyleOptionButton()
    widget.initStyleOption(opt)
    se = (
        QtWidgets.QStyle.SE_CheckBoxIndicator
        if isinstance(widget, QtWidgets.QCheckBox)
        else QtWidgets.QStyle.SE_RadioButtonIndicator
    )
    r = st.subElementRect(se, opt, widget)
    cols = [
        QtGui.QColor(img.pixel(x, y))
        for y in range(r.top(), r.bottom() + 1)
        for x in range(r.left(), r.right() + 1)
    ]
    return r, cols


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_check_box_and_radio_states_are_drawn_in_the_theme(theme, scheme):
    T = theme
    T.apply_theme(_app(), scheme, 1.0)
    edge = QtGui.QColor(T.COLORS["control_edge"])
    accent = QtGui.QColor(T.COLORS["accent"])

    def near(c, ref, tol=40):
        return abs(c.red() - ref.red()) + abs(c.green() - ref.green()) + abs(c.blue() - ref.blue()) < tol

    holder = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(holder)
    boxes = {}
    for name, state, enabled in (
        ("off", 0, True),
        ("on", 2, True),
        ("part", 1, True),
        ("disabled", 0, False),
    ):
        cb = QtWidgets.QCheckBox(name)
        cb.setTristate(True)
        cb.setCheckState(QtCore.Qt.CheckState(state))
        cb.setEnabled(enabled)
        lay.addWidget(cb)
        boxes[name] = cb
    rb_off, rb_on = QtWidgets.QRadioButton("r0"), QtWidgets.QRadioButton("r1")
    for rb in (rb_off, rb_on):
        rb.setAutoExclusive(False)
        lay.addWidget(rb)
    rb_on.setChecked(True)
    holder.show()
    _pump()
    r, cols = _indicator(boxes["off"])
    assert r.width() == T.px(T.AppStyle.INDICATOR_PX)
    assert any(near(c, edge) for c in cols), "no control_edge rim on an unchecked box"
    assert not any(near(c, accent, 30) for c in cols)
    for name in ("on", "part"):
        _r, cols = _indicator(boxes[name])
        n_acc = sum(near(c, accent) for c in cols)
        assert n_acc > len(cols) // 3, name  # an accent fill
        mark = QtGui.QColor(T.COLORS["on_accent"])
        assert any(near(c, mark, 60) for c in cols), name  # a tick / bar on it
    _r, cols = _indicator(boxes["disabled"])
    assert not any(near(c, accent, 30) for c in cols)
    _r, cols = _indicator(rb_off)
    assert any(near(c, edge) for c in cols)
    _r, cols = _indicator(rb_on)
    assert sum(near(c, accent) for c in cols) > len(cols) // 4
    # the rim reads on the window: 3:1 (WCAG 1.4.11)
    assert T.contrast(T.COLORS["control_edge"], T.COLORS["bg"]) >= 3.0
    holder.deleteLater()


def test_objects_tree_eye_indicators_stay_images(theme):
    T = theme
    T.apply_theme(_app(), "dark", 1.0)
    qss = T.eye_indicator_qss("QTreeWidget")
    assert "::indicator:checked { image: url(" in qss


def test_fields_and_buttons_use_the_control_edge(theme):
    T = theme
    T.apply_theme(_app(), "dark", 1.0)
    sheet = _app().styleSheet()
    import re

    for sel in (
        "QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {",
        "QPushButton {",
    ):
        block = sheet[sheet.index(sel) :]
        block = block[: block.index("}")]
        assert "border: 1px solid %s" % T.COLORS["control_edge"] in block, sel
    assert re.search(r"QMainWindow::separator \{ background: %s" % T.COLORS["border"], sheet)


# --------------------------------------------------------------------- focus ring
@pytest.fixture()
def ring():
    from ctgedit.ui.style import accessibility as A

    return A.install_focus_ring(_app())


def test_tab_shows_the_ring_and_a_click_does_not(theme, ring):
    T = theme
    T.apply_theme(_app(), "dark", 1.0)
    from ctgedit.ui.dialog.dialogs import GridSetupDialog

    dlg = GridSetupDialog(8, True, True)
    dlg.show()
    dlg.activateWindow()
    _pump()
    cb = dlg._chk_display
    cb.setFocus(QtCore.Qt.TabFocusReason)
    _pump()
    assert cb.hasFocus() and T.has_keyboard_focus(cb)
    ringed = cb.grab().toImage()
    # a mouse click takes it away (and gives none)
    QtTest.QTest.mouseClick(dlg._chk_snap, QtCore.Qt.LeftButton)
    QtTest.QTest.mouseClick(dlg._chk_snap, QtCore.Qt.LeftButton)
    _pump()
    assert not T.has_keyboard_focus(cb)
    assert not dlg._chk_snap.property(T.KBD_FOCUS_PROP)
    cb.setFocus(QtCore.Qt.MouseFocusReason)
    _pump()
    assert not T.has_keyboard_focus(cb)
    plain = cb.grab().toImage()
    assert ringed != plain  # the ring was drawn
    accent = QtGui.QColor(T.COLORS["accent"]).rgb() & 0xFFFFFF
    label_px = [ringed.pixel(x, ringed.height() // 2) & 0xFFFFFF for x in range(ringed.width())]
    assert accent in label_px or any((ringed.pixel(x, 1) & 0xFFFFFF) == accent for x in range(ringed.width()))
    # a navigation key on the focused widget shows it again
    QtTest.QTest.keyClick(cb, QtCore.Qt.Key_Space)
    _pump()
    assert T.has_keyboard_focus(cb)
    dlg.close()


def test_focus_ring_always(theme, ring, monkeypatch):
    T = theme
    _set("ui/focus_ring", "always")
    T.apply_theme(_app(), "dark", 1.0)
    assert T.focus_ring() == "always"
    b = QtWidgets.QPushButton("OK")
    b.show()
    _pump()
    b.setFocus(QtCore.Qt.MouseFocusReason)
    _pump()
    assert T.has_keyboard_focus(b)
    _set("ui/focus_ring", "keyboard")
    T.apply_theme(_app(), "dark", 1.0)
    b.deleteLater()


def test_ring_rules_for_buttons_tabs_and_lists(theme):
    T = theme
    T.apply_theme(_app(), "light", 1.0)
    sheet = _app().styleSheet()
    for sel in (
        'QToolButton[kbdFocus="true"]',
        'QPushButton[kbdFocus="true"]',
        'QTabBar[kbdFocus="true"]::tab:selected',
        'QTreeView[kbdFocus="true"]',
    ):
        assert sel in sheet, sel
    assert "border: 2px solid %s" % T.COLORS["accent"] in sheet


def test_slider_handle_ring(theme, ring):
    T = theme
    T.apply_theme(_app(), "dark", 1.0)
    s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    s.resize(160, 30)
    s.show()
    _pump()
    s.setFocus(QtCore.Qt.MouseFocusReason)
    _pump()
    plain = s.grab().toImage()
    s.setFocus(QtCore.Qt.TabFocusReason)
    s.clearFocus()
    s.setFocus(QtCore.Qt.TabFocusReason)
    _pump()
    assert T.has_keyboard_focus(s)
    assert s.grab().toImage() != plain
    s.deleteLater()


def test_hot_scroll_bars(theme, ring):
    T = theme
    T.apply_theme(_app(), "dark", 1.0)
    sb = QtWidgets.QScrollBar(QtCore.Qt.Vertical)
    sb.setRange(0, 100)
    sb.resize(10, 200)
    sb.show()
    _pump()
    _app().sendEvent(sb, QtGui.QEnterEvent(QtCore.QPointF(5, 5), QtCore.QPointF(5, 5), QtCore.QPointF(5, 5)))
    assert sb.property("hot") is True
    _app().sendEvent(sb, QtCore.QEvent(QtCore.QEvent.Leave))
    assert sb.property("hot") is False
    sheet = _app().styleSheet()
    assert 'QScrollBar[hot="true"]::handle:vertical' in sheet
    assert "background: %s" % T.COLORS["scroll_handle"] in sheet
    sb.deleteLater()


# --------------------------------------------------------------------- density
def test_density_changes_rows_and_menus_live(theme):
    T = theme
    app = _app()
    tree = QtWidgets.QTreeWidget()
    for k in range(5):
        QtWidgets.QTreeWidgetItem(tree, ["row %d" % k])
    tree.resize(200, 200)
    tree.show()
    menu = QtWidgets.QMenu()
    for k in range(4):
        menu.addAction("Item %d" % k)
    heights = {}
    for name in ("compact", "comfortable", "touch"):
        _set("ui/density", name)
        T.apply_theme(app, "dark", 1.0)
        assert T.current_density() == name
        tree.style().unpolish(tree)
        tree.style().polish(tree)
        tree.doItemsLayout()
        _pump()
        heights[name] = (tree.visualItemRect(tree.topLevelItem(1)).height(), menu.sizeHint().height())
    assert heights["compact"][0] < heights["comfortable"][0] < heights["touch"][0]
    assert heights["compact"][1] < heights["comfortable"][1] < heights["touch"][1]
    assert T.density("ribbon_h", "compact") == 84 and T.density("captions", "compact") is False
    assert T.density("big_h", "touch") == 76 and T.density("qat_icon", "comfortable") == 16
    _set("ui/density", "comfortable")
    T.apply_theme(app, "dark", 1.0)
    tree.deleteLater()
    menu.deleteLater()


def test_objects_tree_rows_follow_the_density(theme, win):
    T = theme
    tree = win.objects_dock._tree
    for k in range(3):
        QtWidgets.QTreeWidgetItem(tree, ["row %d" % k])
    win.resize(1280, 720)
    win.show()
    h = {}
    for name in ("compact", "touch"):
        _set("ui/density", name)
        T.apply_theme(_app(), "dark", 1.0)
        tree.doItemsLayout()
        _pump()
        h[name] = tree.visualItemRect(tree.topLevelItem(1)).height()
    assert h["compact"] < h["touch"]
    _set("ui/density", "comfortable")


def test_comfortable_keeps_todays_paddings(theme):
    T = theme
    T.apply_theme(_app(), "dark", 1.0)
    sheet = _app().styleSheet()
    for frag in (
        "QMenu::item { padding: 4px 24px; }",
        "QListView::item, QTreeView::item { padding: 2px 4px; }",
        "padding: 5px 12px;",
        "padding: 4px 12px; }",
    ):
        assert frag in sheet, frag


# --------------------------------------------------------------------- accent
def test_accent_green_repaints_live(theme):
    T = theme
    app = _app()
    lab = QtWidgets.QLabel("x")
    lab.setStyleSheet("QLabel { border: 1px solid #4da3ff; }")
    tracked = T.tracked("#4da3ff")
    _set("ui/accent", "green")
    T.apply_theme(app, "dark", 1.0)
    green = T.COLORS["accent"]
    assert green == T.accent_for("green", "dark") and green != T.DARK["accent"]
    assert green in app.styleSheet()  # menus, focus rings
    acc = QtGui.QColor(green)
    assert "rgba(%d, %d, %d, 56)" % (acc.red(), acc.green(), acc.blue()) in app.styleSheet()
    assert green in lab.styleSheet()  # a widget's own sheet
    assert tracked.name() == green  # a painted constant
    assert T.COLORS["selection"] == T.mix(green, T.DARK["bg"], T._SELECTION_MIX["dark"])
    assert T.canvas_colour("selection").name() == green
    _set("ui/accent", "#123456")
    T.apply_theme(app, "light", 1.0)
    assert T.contrast(T.COLORS["accent"], T.COLORS["bg"]) >= 4.5
    _set("ui/accent", "blue")
    T.apply_theme(app, "dark", 1.0)
    assert lab.styleSheet() == "QLabel { border: 1px solid #4da3ff; }"  # as written again
    assert T.COLORS["accent"] == T.DARK["accent"]
    lab.deleteLater()


def test_windows_accent_is_read_and_fitted(theme, monkeypatch):
    T = theme
    monkeypatch.setattr(T, "windows_accent", lambda: "#0078d4")
    c = T._scheme_colours("dark", "windows")
    assert T.contrast(c["accent"], T.DARK["bg"]) >= 4.5
    c = T._scheme_colours("light", "windows")
    assert c["accent"] == "#0078d4" or T.contrast(c["accent"], T.LIGHT["hover"]) >= 4.5
    monkeypatch.undo()
    # DWM AccentColor is 0xAABBGGRR
    monkeypatch.setattr(T, "_hkcu_dword", lambda key, name: 0xFFD47800 if name == "AccentColor" else None)
    assert T.windows_accent() == "#0078d4"
    monkeypatch.setattr(
        T, "_hkcu_dword", lambda key, name: 0xC40078D4 if name == "ColorizationColor" else None
    )
    assert T.windows_accent() == "#0078d4"


def test_canvas_colour_follows_the_selection_setting(theme):
    T = theme
    T.apply_theme(_app(), "dark", 1.0)
    assert T.canvas_colour("selection").name() == T.COLORS["accent"]
    assert T.canvas_colour("marquee").alpha() == 40
    assert T.canvas_colour("hover", 99).alpha() == 99
    _set("view/selection_colour", "#ff00ff")
    T.reload_canvas_colours()
    assert T.canvas_colour("handle_edge").name() == "#ff00ff"
    _set("view/selection_colour", str(0x00FF00))
    T.reload_canvas_colours()
    assert T.canvas_colour("selection").name() == "#00ff00"
    _set("view/selection_colour", "accent")
    T.reload_canvas_colours()


def test_contrast_scheme_has_the_new_roles(theme):
    c = theme.contrast_colours()
    for role in ("control_edge", "sel_text_dim", "scroll_handle", "on_accent"):
        assert c.get(role), role


# --------------------------------------------------------------------- empty state
def test_empty_state(theme):
    T = theme
    act = QtGui.QAction("Open Page…")
    hit = []
    act.triggered.connect(lambda: hit.append(1))
    w = T.empty_state("page", "No page is open.", act)
    w.resize(240, 200)
    w.show()
    _pump()
    assert w.label.text() == "No page is open."
    assert w.label.property("noteRole") == "text_dim"
    assert not w.icon_label.pixmap().isNull()
    w.button.click()
    assert hit == [1]
    w2 = T.empty_state("", "Nothing found.", ("Clear", lambda: hit.append(2)))
    w2.button.click()
    assert hit == [1, 2] and w2.icon_label.isHidden()
    T.apply_theme(_app(), "light", 1.0)  # repaints its icon
    w.deleteLater()
    w2.deleteLater()


# --------------------------------------------------------------------- the window
def test_settings_dialog_appearance_and_canvas_round_trip(theme, win):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.prefs.settings import app_settings, canvas_look

    d = SettingsDialog(win)
    assert d._accent.currentData() == "blue" and d._density.currentData() == "comfortable"
    assert d._focus_ring.currentData() == "keyboard" and d._accent_custom.isHidden()
    d._accent.setCurrentIndex(d._accent.findData("orange"))
    d._density.setCurrentIndex(d._density.findData("compact"))
    d._grid_style.setCurrentIndex(d._grid_style.findData("dots"))
    d._grid_major.setValue(5)
    d._grid_outside.setChecked(True)
    d._pixel_grid.setChecked(False)
    d._page_edge.setCurrentIndex(d._page_edge.findData("line"))
    d._page_shadow.setChecked(False)
    # (Settings > Appearance > Canvas > Selection colour: the canvas reads it)
    assert d.SELECTION_COLOUR_ROW and not d._sel_colour_row.isHidden()
    d._sel_colour.setCurrentIndex(d._sel_colour.findData("custom"))
    assert not d._sel_custom.isHidden()
    d._sel_custom.set_pair((0xFF00FF, 0xFF00FF))
    d._apply(accept=False)
    st = app_settings()
    assert st.value("ui/accent") == "orange" and st.value("ui/density") == "compact"
    assert theme.current_density() == "compact"
    assert theme.COLORS["accent"] == theme.accent_for("orange", theme.current_scheme())
    assert canvas_look() == {
        "grid_major": 5,
        "grid_style": "dots",
        "grid_outside": True,
        "pixel_grid": False,
        "page_edge": "line",
        "page_shadow": False,
    }
    assert win.scene.look() == canvas_look()
    assert not win.act_pixel_grid.isChecked()
    assert theme.canvas_colour("selection").name() == "#ff00ff"
    assert app_settings().value("view/selection_colour") == "#ff00ff"
    _set("view/selection_colour", "accent")
    d.restore_defaults()
    assert d._accent.currentData() == "blue" and d._grid_major.value() == 8
    assert d._surround.currentData() == "desk" and d._page_shadow.isChecked()
    d._apply(accept=False)
    assert win.scene.look() == canvas_look() and canvas_look()["grid_major"] == 8
    d.close()


def test_grid_setup_sets_the_grid_look(win):
    win._grid_setup_dialog()
    dlg = win._dialogs.get("grid")
    assert dlg.look() == (8, "lines")
    dlg._sp_major.setValue(4)
    dlg._cb_style.setCurrentIndex(dlg._cb_style.findData("dots"))
    assert dlg.has_pending_edits()
    dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).click()
    assert not dlg.has_pending_edits()
    assert win.scene.look("grid_major") == 4 and win.scene.look("grid_style") == "dots"
    win.act_grid.trigger()  # Show Grid follows, the look stays
    assert dlg.look() == (4, "dots") and not dlg.has_pending_edits()
    dlg.close()


def test_pixel_grid_action_in_view_grid(win):
    view_menu = next(m for m in win.menuBar().findChildren(QtWidgets.QMenu) if m.title() == "&View")
    grid = next(m for m in view_menu.findChildren(QtWidgets.QMenu) if m.title() == "Grid")
    assert win.act_pixel_grid in grid.actions()
    assert win.act_pixel_grid.isChecked() and win.scene.look("pixel_grid")
    win.act_pixel_grid.trigger()
    from ctgedit.ui.prefs.settings import app_settings

    assert app_settings().value("view/pixel_grid", True, type=bool) is False
    assert win.scene.look("pixel_grid") is False


def test_new_tab_scenes_get_the_look_and_surround(win):
    from ctgedit.render.scene import PageScene

    win.set_surround_mode("page")
    _set("view/grid_major", 3)
    sc = PageScene(win)
    win.apply_canvas_look(sc)
    assert sc.surround() == "page" and sc.look("grid_major") == 3


def test_settings_ok_updates_an_open_grid_setup(win):
    """Settings > Canvas changes the grid's look while Grid Setup is open: Grid Setup shows
    it, and its own Apply (another pitch typed) keeps it."""
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.prefs.settings import canvas_look

    win._grid_setup_dialog()
    dlg = win._dialogs.get("grid")
    assert dlg.look() == (8, "lines")
    d = SettingsDialog(win)
    d._grid_style.setCurrentIndex(d._grid_style.findData("dots"))
    d._grid_major.setValue(5)
    d._apply(accept=False)
    assert dlg.look() == (5, "dots") and not dlg.has_pending_edits()
    # with a pitch typed (pending) the look still follows, the pitch stays typed
    dlg._sp_pitch.setValue(12)
    d._grid_style.setCurrentIndex(d._grid_style.findData("lines"))
    d._apply(accept=False)
    assert dlg.look() == (5, "lines") and dlg._sp_pitch.value() == 12
    assert dlg.has_pending_edits()
    dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).click()
    assert canvas_look()["grid_style"] == "lines" and canvas_look()["grid_major"] == 5
    # a look changed in Grid Setup and not applied is kept
    dlg._cb_style.setCurrentIndex(dlg._cb_style.findData("dots"))
    d._grid_major.setValue(6)
    d._apply(accept=False)
    assert dlg.look() == (5, "dots")
    d.restore_defaults()
    d._apply(accept=False)
    d.close()
    dlg.close()


# --------------------------------------------------------------------- the Library panel
def _darkest(img):
    best = None
    for y in range(img.height()):
        for x in range(img.width()):
            c = QtGui.QColor(img.pixel(x, y))
            lum = c.red() * 299 + c.green() * 587 + c.blue() * 114
            if best is None or lum < best[0]:
                best = (lum, c)
    return best[1]


@pytest.fixture()
def library(theme):
    from ctgedit.ui.panels.library_panel import LibraryPanel

    p = LibraryPanel()
    p.resize(700, 500)
    yield p
    p.hide()
    p.deleteLater()
    _pump()
    _delete_now()


def test_library_text_on_the_selection_reads_in_light(theme, library):
    T = theme
    T.apply_theme(_app(), "light", 1.0)
    p = library
    sheet = p.styleSheet()
    assert "#ffffff" not in sheet.lower()
    assert "color:%s" % T.COLORS["sel_text"] in sheet
    p.show()
    b = p._btn_kind["symbol"]
    b.setChecked(True)
    _pump()
    img = b.grab().toImage()
    text = _darkest(img)
    assert T.contrast(text, T.COLORS["selection"]) >= 4.5
    # and back in dark: the sheet is made again from the dark colours
    T.apply_theme(_app(), "dark", 1.0)
    _pump()
    assert "color:%s" % T.COLORS["sel_text"] in p.styleSheet()
    assert T.COLORS["selection"] in p.styleSheet()


def _edge(w):
    img = w.grab().toImage()
    return QtGui.QColor(img.pixel(0, img.height() // 2)).name()


def test_focus_ring_on_views_and_sliders_with_their_own_sheets(theme, ring, library):
    T = theme
    T.apply_theme(_app(), "dark", 1.0)
    p = library
    p.show()
    _pump()
    accent = QtGui.QColor(T.COLORS["accent"]).name()
    for w in (p._tree, p._grid):
        assert _edge(w) != accent, w.objectName()
        ring.mark(w)
        _pump()
        assert _edge(w) == accent, w.objectName()
        ring.unmark(w)
        _pump()
        assert _edge(w) != accent, w.objectName()
    s = p._slider
    plain = s.grab().toImage()
    ring.mark(s)
    _pump()
    ringed = s.grab().toImage()
    assert ringed != plain
    acc = QtGui.QColor(accent).rgb() & 0xFFFFFF
    assert any(
        (ringed.pixel(x, y) & 0xFFFFFF) == acc for y in range(ringed.height()) for x in range(ringed.width())
    )
    ring.unmark(s)


def test_focus_ring_on_the_page_browser_grid(theme, ring):
    T = theme
    T.apply_theme(_app(), "dark", 1.0)
    from ctgedit.ui.browser import PageBrowserDialog

    dlg = PageBrowserDialog(PageBrowserDialog.OPEN)
    try:
        g = dlg.grid
        g.resize(300, 200)
        g.show()
        _pump()
        accent = QtGui.QColor(T.COLORS["accent"]).name()
        assert _edge(g) != accent
        ring.mark(g)
        _pump()
        assert _edge(g) == accent
        ring.unmark(g)
    finally:
        dlg.reject()
        dlg.deleteLater()
        _pump()


# --------------------------------------------------------------------- ring: own sheets
def _has_accent(w, accent):
    img = w.grab().toImage()
    acc = QtGui.QColor(accent).rgb() & 0xFFFFFF
    return any((img.pixel(x, y) & 0xFFFFFF) == acc for y in range(img.height()) for x in range(img.width()))


def _ringed_in(sheet, make, ring, accent):
    """(no accent without the ring, the accent with it) for the widget ``make`` gives,
    inside a host whose own stylesheet is ``sheet``."""
    host = QtWidgets.QWidget()
    host.setStyleSheet(sheet)
    lay = QtWidgets.QHBoxLayout(host)
    w = make()
    lay.addWidget(w)
    host.show()
    _pump()
    try:
        before = _has_accent(w, accent)
        ring.mark(w)
        _pump()
        return before, _has_accent(w, accent)
    finally:
        ring.unmark(w)
        host.hide()
        host.deleteLater()
        _pump()


def test_ring_on_buttons_in_panels_whose_sheet_gives_them_a_border(theme, ring):
    """A widget's own sheet beats the application's, whatever the specificity: the
    Properties panel's tool buttons and the start page's buttons and cards ring
    themselves."""
    T = theme
    T.apply_theme(_app(), "dark", 1.0)
    accent = T.COLORS["accent"]
    from ctgedit.ui.panels import properties_parts as P_parts
    from ctgedit.ui.panels import start_page as S

    def tool(name=""):
        def make():
            b = QtWidgets.QToolButton()
            b.setText("Page Colour")
            if name:
                b.setObjectName(name)
            return b

        return make

    def big():
        b = QtWidgets.QPushButton("Open Page")
        b.setObjectName("big")
        return b

    assert _ringed_in(P_parts.QSS, tool(), ring, accent) == (False, True)
    assert _ringed_in(S._qss(), tool("card"), ring, accent) == (False, True)
    assert _ringed_in(S._qss(), big, ring, accent) == (False, True)
    # the ring takes the place of the edge and padding: the button keeps its size
    for sheet, make in ((P_parts.QSS, tool()), (S._qss(), tool("card")), (S._qss(), big)):
        host = QtWidgets.QWidget()
        host.setStyleSheet(sheet)
        w = make()
        w.setParent(host)
        w.ensurePolished()
        size = w.sizeHint()
        ring.mark(w)
        assert w.sizeHint() == size, sheet[:40]
        ring.unmark(w)
        host.deleteLater()


def test_panel_fields_use_the_control_edge(theme):
    """The Properties panel, Objects and Library search fields are edged in control_edge
    (3:1), as the application sheet's fields are."""
    T = theme
    from ctgedit.ui.panels import library_panel as L
    from ctgedit.ui.panels import objects_dock as O
    from ctgedit.ui.panels import properties_parts as P_parts

    edge = T.DARK["control_edge"]
    for sheet, sel in (
        (P_parts.QSS, "QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {"),
        (O._QSS, "QLineEdit {"),
    ):
        block = sheet[sheet.index(sel) :]
        block = block[: block.index("}")]
        assert "border: 1px solid %s" % edge in block, sel
    # (the Library search field has no rule of its own: the application sheet's field, edged
    # in control_edge)
    assert "libSearch" not in L._QSS
    for scheme in ("dark", "light"):
        T.apply_theme(_app(), scheme, 1.0)
        w = QtWidgets.QWidget()
        w.setStyleSheet(P_parts.QSS)
        assert "border: 1px solid %s" % T.COLORS["control_edge"] in w.styleSheet()
        w.deleteLater()


def test_ring_rules_follow_a_later_accent_change(theme):
    """The Help view's and the page browser grid's ring rules are written in the design
    colours, so a later accent change recolours them (an accent filled in when the widget
    was made would stay)."""
    T = theme
    app = _app()
    _set("ui/accent", "orange")
    try:
        T.apply_theme(app, "dark", 1.0)
        orange = T.COLORS["accent"]
        from ctgedit.ui.browser import PageBrowserDialog
        from ctgedit.ui.dialog.help_window import HelpWindow

        hw = HelpWindow()
        dlg = PageBrowserDialog(PageBrowserDialog.OPEN)
        try:
            assert orange in hw.view.styleSheet() and orange in dlg.grid.styleSheet()
            _set("ui/accent", "green")
            T.apply_theme(app, "dark", 1.0)
            green = T.COLORS["accent"]
            for w in (hw.view, dlg.grid):
                assert orange not in w.styleSheet() and green in w.styleSheet()
            T.apply_theme(app, "light", 1.0)
            for w in (hw.view, dlg.grid):
                assert T.COLORS["bg"] in w.styleSheet()
                assert T.COLORS["accent"] in w.styleSheet()
        finally:
            dlg.reject()
            dlg.deleteLater()
            hw.close()
            hw.deleteLater()
            _pump()
    finally:
        _set("ui/accent", "blue")
        T.apply_theme(app, "dark", 1.0)


@pytest.mark.parametrize("mode", ["keyboard", "always"])
def test_a_widget_deleted_with_the_ring_leaves_nothing(theme, ring, monkeypatch, mode):
    """The FocusOut a widget's destructor sends comes with a new plain QWidget wrapper:
    the ring finds its entry by the C++ object, drops it, and never repolishes the half
    destroyed widget."""
    import shiboken6
    from ctgedit.ui.style import accessibility as A

    T = theme
    _set("ui/focus_ring", mode)
    T.apply_theme(_app(), "dark", 1.0)
    seen = []
    real = A._repolish

    def spy(w):
        seen.append(type(w).__name__)
        return real(w)

    monkeypatch.setattr(A, "_repolish", spy)
    top = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(top)
    top.show()
    _pump()
    try:
        for kind in (
            QtWidgets.QLineEdit,
            QtWidgets.QTreeWidget,
            QtWidgets.QPushButton,
            QtWidgets.QComboBox,
            QtWidgets.QCheckBox,
            QtWidgets.QSpinBox,
            QtWidgets.QTableWidget,
        ):
            w = kind()
            lay.addWidget(w)
            _pump()
            w.setFocus(QtCore.Qt.TabFocusReason)
            _pump()
            assert T.has_keyboard_focus(w), kind.__name__
            key = shiboken6.getCppPointer(w)[0]
            assert key in ring._marked
            seen.clear()
            w.deleteLater()
            _app().sendPostedEvents(None, int(QtCore.QEvent.DeferredDelete.value))
            _pump()
            assert not shiboken6.isValid(w)
            assert "QWidget" not in seen, (kind.__name__, seen)
            assert key not in ring._marked and key not in ring._watched, kind.__name__
        # marking and unmarking a live widget again and again connects it once
        b = QtWidgets.QPushButton("x")
        lay.addWidget(b)
        _pump()
        for _ in range(3):
            ring.mark(b)
            assert T.has_keyboard_focus(b) or not b.hasFocus()
            ring.unmark(b)
            assert not b.property(T.KBD_FOCUS_PROP)
        assert shiboken6.getCppPointer(b)[0] in ring._watched
    finally:
        top.hide()
        top.deleteLater()
        _pump()
        _set("ui/focus_ring", "keyboard")
        T.apply_theme(_app(), "dark", 1.0)
