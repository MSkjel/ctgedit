"""Accessibility, theme, DPI and localisation."""

import os
import random
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


@pytest.fixture()
def theme():
    """The theme module; afterwards the application is put back exactly as
    it was (style, palette, font, stylesheet, widget stylesheets, colours)."""
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
    T.apply_theme(app, "dark", 1.0)  # every widget back to its sheet as written
    T.COLORS.clear()
    T.COLORS.update(snap["colors"])
    T._state.clear()
    T._state.update(snap["state"])
    T._update_tracked()
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
    QtWidgets.QApplication.processEvents()


# --------------------------------------------------------------------------- themes
def test_dark_theme_keeps_the_authored_look(theme):
    app = _app()
    lbl = QtWidgets.QLabel("note")
    lbl.setStyleSheet("color: #8a8f98; font-size: 10px;")
    assert theme.apply_theme(app, "dark", 1.0) == ("dark", 1.0)
    later = QtWidgets.QLabel("later")
    later.setStyleSheet("color: #8a8f98;")
    # the design colours are not 'normalised': dark stays exactly as written
    assert lbl.styleSheet() == "color: #8a8f98; font-size: 10px;"
    assert later.styleSheet() == "color: #8a8f98;"
    assert "font-size: 12px" in app.styleSheet()
    assert app.font().pixelSize() == 12
    for k, v in theme.DARK.items():
        assert theme.COLORS[k] == v


def test_light_theme_reaches_every_stylesheet_and_switches_back(theme):
    app = _app()
    note = QtWidgets.QLabel("note")
    note.setStyleSheet("color: #969ca5;")
    panel = QtWidgets.QFrame()
    panel.setStyleSheet("QFrame { background: #26292e; border: 1px solid #3a3f46; }")
    swatch = QtWidgets.QPushButton()
    swatch.setStyleSheet(theme.DATA_MARK + "background:#1f2226;")  # a page colour
    overlay = QtWidgets.QFrame()
    overlay.setStyleSheet("#o { background: rgba(34, 38, 44, 215); }")
    theme.apply_theme(app, "light", 1.0)
    L = theme.LIGHT
    assert note.styleSheet() == "color: %s;" % L["text_dim"]
    assert L["panel"] in panel.styleSheet() and L["border"] in panel.styleSheet()
    assert swatch.styleSheet().endswith("background:#1f2226;")  # data untouched
    ov = QtGui.QColor(L["overlay"])
    assert "rgba(%d, %d, %d, 215)" % (ov.red(), ov.green(), ov.blue()) in overlay.styleSheet()
    # a sheet formatted from COLORS while light is on turns dark again
    made = QtWidgets.QLabel()
    made.setStyleSheet("color:%s;" % theme.COLORS["text_dim"])
    assert app.palette().color(QtGui.QPalette.Window).name() == L["bg"]
    theme.apply_theme(app, "dark", 1.0)
    assert note.styleSheet() == "color: #969ca5;"
    assert made.styleSheet() == "color:%s;" % theme.DARK["text_dim"]
    assert "#26292e" in panel.styleSheet()


def test_widgets_styled_before_the_first_theme_are_adopted(theme):
    """A widget styled while no theme was applied (the hook not installed)
    still switches: its stylesheet as it is becomes its template."""
    app = _app()
    theme._uninstall_stylesheet_hook()
    theme._state.update(themed=False, recolour=False, scheme="dark", scale=1.0)
    lbl = QtWidgets.QLabel("x")
    lbl.setStyleSheet("color: #d6d9de;")
    theme.apply_theme(app, "light", 1.0)
    assert lbl.styleSheet() == "color: %s;" % theme.LIGHT["text"]


def test_text_size_scales_fonts_and_the_ribbon(theme):
    app = _app()
    lbl = QtWidgets.QLabel("x")
    lbl.setStyleSheet("QLabel { font-size: 10px; } QLabel#t { font: 600 20px 'Segoe UI'; }")
    theme.apply_theme(app, "dark", 1.5)
    assert "font-size: 15px" in lbl.styleSheet() and "600 30px" in lbl.styleSheet()
    assert app.font().pixelSize() == 18 and "font-size: 18px" in app.styleSheet()
    assert theme.px(64) == 96
    from ctgedit.ui.ribbon import RibbonBar

    assert RibbonBar.expanded_height() == 156 and RibbonBar.collapsed_height() == 45


def test_resolve_follows_windows_high_contrast_app_mode_and_text_size(theme, monkeypatch):
    monkeypatch.setattr(theme, "windows_high_contrast", lambda: False)
    monkeypatch.setattr(theme, "windows_light_apps", lambda: True)
    monkeypatch.setattr(theme, "windows_text_scale", lambda: 125)
    base = dict(theme.APPEARANCE_DEFAULTS)
    assert theme.resolve(base) == ("dark", 1.25)  # Windows' 'Make text bigger'
    assert theme.resolve(dict(base, **{"ui/follow_text_size": False, "ui/text_size": 150})) == ("dark", 1.5)
    assert theme.resolve(dict(base, **{"ui/style/theme": "system"}))[0] == "light"
    monkeypatch.setattr(theme, "windows_light_apps", lambda: False)
    assert theme.resolve(dict(base, **{"ui/style/theme": "system"}))[0] == "dark"
    monkeypatch.setattr(theme, "windows_high_contrast", lambda: True)
    assert theme.resolve(dict(base, **{"ui/style/theme": "light"}))[0] == "contrast"
    assert theme.resolve(dict(base, **{"ui/style/theme": "light", "ui/high_contrast": False}))[0] == "light"


def test_high_contrast_uses_the_windows_system_colours(theme):
    app = _app()
    theme.apply_theme(app, "contrast", 1.0)
    if sys.platform == "win32":
        import ctypes

        c = ctypes.windll.user32.GetSysColor(5) & 0xFFFFFF  # COLOR_WINDOW
        want = "#%02x%02x%02x" % (c & 0xFF, (c >> 8) & 0xFF, c >> 16)
        assert theme.COLORS["bg"] == want
    lbl = QtWidgets.QLabel("x")
    lbl.setStyleSheet("color: #969ca5;")
    assert lbl.styleSheet() == "color: %s;" % theme.COLORS["text_dim"]
    assert theme.COLORS["text_dim"] == theme.COLORS["text"]  # no dimmed notes in HC


def test_icons_are_painted_at_the_device_pixel_ratio_in_the_theme_colour(theme):
    app = _app()
    ic = theme.icon("save")
    pm = ic.pixmap(QtCore.QSize(20, 20), 1.5)
    assert (pm.width(), pm.height(), pm.devicePixelRatio()) == (30, 30, 1.5)
    theme.apply_theme(app, "light", 1.0)
    img = ic.pixmap(QtCore.QSize(20, 20), 1.0).toImage()
    solid = {
        img.pixelColor(x, y).name()
        for x in range(img.width())
        for y in range(img.height())
        if img.pixelColor(x, y).alpha() == 255
    }
    assert solid == {theme.LIGHT["text"]}  # the same icon, repainted
    # a disabled icon: the text colour at 35 % (a greyed command reads as unavailable)
    dis = ic.pixmap(QtCore.QSize(20, 20), QtGui.QIcon.Disabled).toImage()
    alphas = {dis.pixelColor(x, y).alpha() for x in range(dis.width()) for y in range(dis.height())}
    assert abs(max(alphas) - 255 * theme.DISABLED_ICON_OPACITY) <= 2
    want = QtGui.QColor(theme.LIGHT["text"])
    for x in range(dis.width()):
        for y in range(dis.height()):
            c = dis.pixelColor(x, y)
            if c.alpha() == max(alphas):
                assert (
                    max(abs(c.red() - want.red()), abs(c.green() - want.green()), abs(c.blue() - want.blue()))
                    <= 4
                ), c.name()


def test_tracked_colours_follow_the_theme(theme):
    app = _app()
    c = theme.tracked("#262a30")  # the library card
    assert c.name() == "#262a30"
    theme.apply_theme(app, "light", 1.0)
    assert c.name() == theme.LIGHT["card"]
    theme.apply_theme(app, "dark", 1.0)
    assert c.name() == "#262a30"


def test_settings_appearance_tab_applies_the_theme(theme, win):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.help import DIALOG_TOPICS, dialog_topic

    dlg = SettingsDialog(win)
    tabs = [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())]
    assert "Appearance" in tabs and "SettingsDialog" in DIALOG_TOPICS
    dlg._tabs.setCurrentIndex(tabs.index("Appearance"))
    assert dialog_topic(dlg) == "settings#appearance"
    assert dlg._theme.currentData() == "dark" and dlg._text_size.value() == 100
    dlg._theme.setCurrentIndex(dlg._theme.findData("light"))
    dlg._high_contrast.setChecked(False)
    dlg._follow_text.setChecked(False)
    dlg._apply(accept=False)
    assert theme.current_scheme() == "light" and theme.appearance()["ui/style/theme"] == "light"
    assert win.ribbon.height() == win.ribbon.expanded_height()
    # Restore Defaults puts the Appearance choices back too
    dlg.restore_defaults()
    assert dlg._theme.currentData() == "dark" and dlg._follow_text.isChecked()
    dlg.close()


def test_imported_appearance_applies_at_once(theme, win, tmp_path, monkeypatch):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.prefs.settings_transfer import export_groups

    monkeypatch.setattr(theme, "windows_high_contrast", lambda: False)
    path = str(tmp_path / "exported.ini")
    export_groups(path, overrides={"ui/style/theme": "light", "ui/follow_text_size": False})
    dlg = SettingsDialog(win)
    assert dlg.import_from(path)
    assert theme.current_scheme() == "light" and dlg._theme.currentData() == "light"
    dlg.close()


def test_the_canvas_margin_follows_the_theme(theme):
    from ctgedit.render import scene as SC

    theme.apply_theme(_app(), "light", 1.0)
    assert SC.PageScene._MARGIN_COLOUR.name() == theme.LIGHT["desk"]


def _arrow_contrast(widget) -> int:
    """How many pixels of the right-hand arrow area differ clearly from the
    field's background."""
    img = widget.grab().toImage()
    w, h = img.width(), img.height()
    bg = QtGui.QColor(img.pixel(w // 3, h // 2)).lightness()
    n = 0
    for x in range(max(0, w - 20), w - 2):
        for y in range(3, h - 3):
            if abs(QtGui.QColor(img.pixel(x, y)).lightness() - bg) > 60:
                n += 1
    return n


@pytest.mark.parametrize("scheme", ["dark", "light", "contrast"])
def test_combo_and_spin_arrows_are_visible(theme, scheme):
    """The theme draws the combo drop-down arrow and the spin up / down
    arrows in the text colour."""
    app = _app()
    theme.apply_theme(app, scheme, 1.0)
    w = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(w)
    combo = QtWidgets.QComboBox()
    combo.addItems(["alpha", "beta"])
    editable = QtWidgets.QComboBox()
    editable.setEditable(True)
    spin = QtWidgets.QSpinBox()
    spin.setRange(0, 10)
    spin.setValue(5)
    for x in (combo, editable, spin):
        lay.addWidget(x)
    w.resize(220, 120)
    w.show()
    app.processEvents()
    try:
        for x in (combo, editable, spin):
            assert _arrow_contrast(x) >= 4, (scheme, type(x).__name__)
    finally:
        w.close()
    assert "down-arrow" in theme.arrow_qss() and "up-arrow" in theme.arrow_qss()


def test_search_field_and_note_label(theme):
    """theme.search_field: the search icon, a clear button, a debounced
    searchChanged, Esc clears first; theme.note_label: a word-wrapped note in
    its role's colour that follows the theme."""
    app = _app()
    f = theme.search_field("Search messages", delay_ms=0)
    assert f.placeholderText() == "Search messages" and f.isClearButtonEnabled()
    assert f.actions()  # the leading search icon
    got = []
    f.searchChanged.connect(got.append)
    f.setText("abc")
    assert got == []  # debounced
    QtCore.QCoreApplication.processEvents()
    app.processEvents()
    f.flush()
    deadline = QtCore.QDeadlineTimer(1000)
    while not got and not deadline.hasExpired():
        app.processEvents()
    assert got and got[-1] == "abc"
    esc = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier)
    f.keyPressEvent(esc)
    assert f.text() == "" and esc.isAccepted()
    theme.apply_theme(app, "dark", 1.0)
    lab = theme.note_label("Careful", "warn")
    assert lab.wordWrap() and lab.property("noteRole") == "warn"
    assert theme.DARK["warn"] in lab.styleSheet()
    theme.apply_theme(app, "light", 1.0)
    assert theme.LIGHT["warn"] in lab.styleSheet()


def test_search_field_drives_its_list(theme):
    """The one search field (UI guide section 6) in a panel and in a dialog:
    Up / Down move the list with the focus kept, typing in the list goes to
    the search, Ctrl+F in the list focuses it (no shortcut of its own in a
    panel, where Edit > Find owns the key)."""
    from PySide6.QtTest import QTest

    app = _app()
    panel = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(panel)
    f = theme.SearchField("Search rows", tip="Row names")
    lst = QtWidgets.QListWidget()
    lst.addItems(["alpha", "beta", "gamma"])
    lst.setCurrentRow(0)
    lay.addWidget(f)
    lay.addWidget(lst)
    f.attach(lst, panel)
    assert f.accessibleName() == "Search rows" and f.toolTip() == "Row names"
    assert not panel.findChildren(QtGui.QShortcut)
    panel.show()
    app.processEvents()
    f.setFocus()
    QTest.keyClick(f, QtCore.Qt.Key_Down)
    assert lst.currentRow() == 1 and f.hasFocus()
    lst.setFocus()
    QTest.keyClick(lst, QtCore.Qt.Key_G)
    assert f.text() == "g" and f.hasFocus()
    QTest.keyClick(f, QtCore.Qt.Key_Escape)
    assert f.text() == ""
    lst.setFocus()
    QTest.keyClick(lst, QtCore.Qt.Key_F, QtCore.Qt.ControlModifier)
    assert f.hasFocus()
    panel.close()
    dlg = QtWidgets.QDialog()
    g = theme.search_field("Search rows", dlg)
    lst2 = QtWidgets.QListWidget(dlg)
    g.attach(lst2, dlg)
    assert len(dlg.findChildren(QtGui.QShortcut)) == 1


# --------------------------------------------------------------------------- accessible names
def test_spoken_names():
    from ctgedit.ui.style.accessibility import meaningful, spoken

    assert spoken("Save &As…") == "Save As"
    assert spoken("Zoom in (Ctrl++)") == "Zoom in"
    assert spoken("Snap to grid (F8)") == "Snap to grid"
    assert spoken("Arrow-key move step (px)") == "Arrow-key move step (px)"
    assert spoken("Fish && Chips") == "Fish & Chips"
    assert spoken("First line\nsecond") == "First line"
    assert spoken("More ▾") == "More"
    assert not meaningful("▴") and not meaningful("") and meaningful("Undo")


def test_ribbon_and_navbar_buttons_are_announced(win):
    from ctgedit.ui.style.accessibility import accessible_name, meaningful

    buttons = win.ribbon.findChildren(QtWidgets.QToolButton)
    assert len(buttons) > 40
    for b in buttons:
        if isinstance(b.parentWidget(), QtWidgets.QTabBar):
            continue  # Qt's own tab scroll arrows ('Scroll Left')
        assert meaningful(accessible_name(b)), (b.text(), b.toolTip())
    undo = next(b for b in buttons if b.defaultAction() is win.act_undo)
    assert accessible_name(undo) == "Undo"
    # a split button names its current variant
    from ctgedit.ui.ribbon import VariantButton

    vb = next(b for b in buttons if isinstance(b, VariantButton))
    other = vb._actions[-1]
    vb._remember(other)
    assert accessible_name(vb).startswith(other.text().replace("&", "").rstrip("…"))
    # the collapse chevron says what it does, both ways
    chev = win.ribbon._chevron
    assert accessible_name(chev) == "Collapse the ribbon"
    win.ribbon.toggle_collapsed()
    try:
        assert accessible_name(chev) == "Expand the ribbon"
    finally:
        win.ribbon.toggle_collapsed()
    assert accessible_name(win.view) == "Page canvas"
    # the keyboard route to the canvas's objects: the Objects panel's tree
    tree = win.dock_objects.findChild(QtWidgets.QTreeWidget)
    assert accessible_name(tree) == win.dock_objects.windowTitle() == "Objects"
    win.dock_objects.setWindowTitle("Objects on the page")  # follows the panel title
    try:
        assert accessible_name(tree) == "Objects on the page"
    finally:
        win.dock_objects.setWindowTitle("Objects")
    nav = win.view.findChild(QtWidgets.QFrame, "NavBar")
    assert nav is not None
    for b in nav.findChildren(QtWidgets.QToolButton):
        assert meaningful(accessible_name(b))
    assert accessible_name(nav._zoom_btn).startswith("Zoom ")


def test_icon_only_buttons_fields_and_dialogs_get_names():
    app = _app()
    from ctgedit.ui.style import accessibility as A

    A.install(app)
    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle("Grid Setup")
    b = QtWidgets.QToolButton(dlg)
    b.setIcon(QtGui.QIcon(QtGui.QPixmap(8, 8)))
    b.setToolTip("Delete the selected rows (Del)")
    sym = QtWidgets.QToolButton(dlg)
    sym.setText("×")
    sym.setToolTip("Close the panel")
    named = QtWidgets.QToolButton(dlg)
    named.setAccessibleName("Pick a colour")
    named.setToolTip("Something else")
    search = QtWidgets.QLineEdit(dlg)
    search.setPlaceholderText("Search tags")
    labelled = QtWidgets.QLineEdit(dlg)
    lab = QtWidgets.QLabel("Width", dlg)
    lab.setBuddy(labelled)
    dlg.show()
    app.processEvents()
    try:
        assert A.accessible_name(b) == "Delete the selected rows (Del)"
        assert A.accessible_name(sym) == "Close the panel"
        assert A.accessible_name(named) == "Pick a colour"
        assert A.accessible_name(search) == "Search tags"
        assert A.accessible_name(labelled) == "Width"  # Qt's buddy label
        assert A.accessible_name(dlg) == "Grid Setup"  # Qt: the title
        # a name made here follows the tool tip / title; an explicit one stays
        b.setToolTip("Remove rows")
        named.setToolTip("Other")
        dlg.setWindowTitle("Grid")
        app.processEvents()
        assert A.accessible_name(b) == "Remove rows"
        assert A.accessible_name(named) == "Pick a colour"
        assert A.accessible_name(dlg) == "Grid"
    finally:
        dlg.close()
        dlg.deleteLater()
        app.processEvents()


def test_names_are_set_once_and_explicit_names_win():
    """A name is only (re)set when it changes -- each set is a NameChanged
    event a screen reader acts on -- and a name the code sets after one
    made here is kept (a later tool-tip change does not replace it)."""
    app = _app()
    from ctgedit.ui.style import accessibility as A

    A.install(app)
    w = QtWidgets.QWidget()
    b = QtWidgets.QToolButton(w)
    b.setToolTip("Zoom to fit")
    field = QtWidgets.QLineEdit(w)
    field.setPlaceholderText("Filter")
    lab = QtWidgets.QLabel("Width", w)
    boxed = QtWidgets.QLineEdit(w)
    lab.setBuddy(boxed)
    w.show()
    app.processEvents()
    try:
        assert A.accessible_name(b) == "Zoom to fit"
        assert A.accessible_name(field) == "Filter"
        assert boxed.accessibleName() == ""  # Qt names it by its label
        assert A.accessible_name(boxed) == "Width"
        # nothing changed: no second set (no NameChanged churn on every Show)
        assert not A.name_widget(b) and not A.name_widget(field)
        w.hide()
        w.show()
        app.processEvents()
        assert A.accessible_name(b) == "Zoom to fit"
        # the code names the button itself: that name stays
        b.setAccessibleName("Fit the page")
        b.setToolTip("Fit the page in the window")
        app.processEvents()
        assert A.accessible_name(b) == "Fit the page"
        # a label attached later names the field instead of its placeholder
        lab2 = QtWidgets.QLabel("Tag filter", w)
        lab2.setBuddy(field)
        assert A.name_widget(field) and A.accessible_name(field) == "Tag filter"
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_ribbon_names_follow_action_changes_without_resets(win):
    from ctgedit.ui.style.accessibility import accessible_name
    from ctgedit.ui.ribbon import announce

    act = QtGui.QAction("Snap to grid", win)
    act.setCheckable(True)
    b = QtWidgets.QToolButton(win)
    b.setDefaultAction(act)
    announce(b, act)
    assert accessible_name(b) == "Snap to grid"
    act.setChecked(True)  # 'changed' fires; the name stays as it was
    assert b.accessibleName() == "Snap to grid"
    act.setText("Snap to &guidelines")
    assert accessible_name(b) == "Snap to guidelines"


# --------------------------------------------------------------------------- DPI
def test_text_extents_use_the_system_dpi_whatever_the_process_awareness():
    """Measured in a system-aware thread context: a DPI-UNAWARE process (a
    python.exe without Qt reads 96 from GetDeviceCaps at any scale) and a
    per-monitor-aware one measure what CtDraw measures."""
    if sys.platform != "win32":
        pytest.skip("GDI")
    code = (
        "import ctypes, json\n"
        "u = ctypes.WinDLL('user32')\n"
        "u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]\n"
        "ok = u.SetProcessDpiAwarenessContext(ctypes.c_void_p(%s))\n"
        "from ctgedit.format import textmetrics as TM\n"
        "u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p\n"
        "u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]\n"
        "aw = lambda: u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext())\n"
        "g = TM.Gdi.get()\n"
        "with g.system_aware():\n"
        "    inside = aw()\n"
        "print(json.dumps([TM.system_dpi(), TM.extent('Hello', 'Arial', 12, 0, 1, 0, (0, 20)),\n"
        "                  TM.dpi_report(), inside, aw()]))\n"
    )
    out = []
    for ctx in ("-1", "-2", "-4"):  # unaware, system aware, per monitor v2
        env = dict(os.environ, PYTHONPATH=ROOT)
        env.pop("CTG_TEXT_DPI", None)
        r = subprocess.run([PY, "-c", code % ctx], capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 0, r.stderr
        import json

        out.append(json.loads(r.stdout.strip().splitlines()[-1]))
    dpis = {o[0] for o in out}
    boxes = {tuple(o[1]) for o in out}
    assert len(dpis) == 1 and len(boxes) == 1, out
    assert out[0][2]["text_dpi"] == out[0][0]
    # GDI runs as a SYSTEM-aware thread (CtDraw's context: 1) whatever the
    # process is, and the thread goes back to the process's own afterwards
    assert [o[3] for o in out] == [1, 1, 1], out
    assert [o[4] for o in out] == [0, 1, 2], out


def test_text_dpi_sets_the_extent_and_the_canvas_font(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("GDI")
    from ctgedit.format import textmetrics as TM
    from ctgedit.render import scene as SC

    g = TM.Gdi.get()
    base = TM.extent("Hello", "Arial", 12, 0, 1, 0, (0, 40))
    monkeypatch.setattr(g, "logpixelsy", 144)  # a 150 % session
    big = TM.extent("Hello", "Arial", 12, 0, 1, 0, (0, 40))
    assert SC._em(12) == 24 and SC._em(11) == 22
    h0, h1 = base[3] - base[1], big[3] - big[1]
    assert 1.35 < h1 / h0 < 1.65
    monkeypatch.setattr(g, "logpixelsy", 96)
    assert SC._em(12) == 16 and SC._em(11) == 14  # lfHeight's integer division


def test_legacy_text_font_from_its_box_is_dpi_neutral(monkeypatch):
    """A text without a font size is sized from its box height; at 150 % the
    box is 1.5x taller, so the font is 1.5x the pixels -- not 1.5 x 1.5."""
    if sys.platform != "win32":
        pytest.skip("GDI")
    from types import SimpleNamespace
    from ctgedit.format import textmetrics as TM
    from ctgedit.render import scene as SC

    _app()
    g = TM.Gdi.get()
    sc = SC.PageScene()

    def px(ey):
        obj = SimpleNamespace(
            style=SimpleNamespace(
                font="Arial", font_size=0, bold=False, italic=False, underline=False, strikeout=False
            ),
            geometry=SimpleNamespace(extent_y=ey),
            raw={},
        )
        return sc._object_font(obj).pixelSize()

    monkeypatch.setattr(g, "logpixelsy", 96)
    at100 = px(20)
    assert at100 == 14  # 11 pt: 96 * 11 // 72
    monkeypatch.setattr(g, "logpixelsy", 144)
    assert px(30) == 22  # the same 11 pt at 144 dpi
    assert px(0) == 16  # no box: 8 pt


def test_tile_puts_window_frames_in_the_work_area(win):
    from ctgedit.ui.main_window import MainWindow

    w = QtWidgets.QWidget()
    w.show()
    try:
        area = win._screen_area()
        rect = QtCore.QRect(area.x(), area.y(), area.width() // 2, area.height())
        MainWindow._place_frame(w, rect)
        assert rect.contains(w.frameGeometry())
    finally:
        w.close()


def test_cascade_keeps_frames_in_the_work_area(win):
    area = win._screen_area()
    w = QtWidgets.QWidget()
    w.resize(area.width() + 400, area.height() + 400)  # larger than the screen
    w.show()
    try:
        from ctgedit.ui.main_window import MainWindow

        win.show()
        # (a class attribute: the method lives on a MainWindow mixin, ui/window/shell.py)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(MainWindow, "_open_windows", staticmethod(lambda: [win, w]))
            win._cascade_windows()
        for x in (win, w):
            fg = x.frameGeometry()
            assert area.contains(fg.topLeft()) and fg.right() <= area.right() + 1
    finally:
        w.close()


def test_screen_pixels_zoom(win, monkeypatch):
    nav = win.view.findChild(QtWidgets.QFrame, "NavBar")
    nav._sync_screen_px()
    assert not nav._act_screen_px.isVisible() or abs(nav.screen_ratio() - 1.0) > 1e-3
    monkeypatch.setattr(nav, "screen_ratio", lambda: 1.5)
    nav._sync_screen_px()
    assert nav._act_screen_px.isVisible() and "67%" in nav._act_screen_px.text()
    nav.zoom_screen_pixels()
    assert abs(win.view.zoom() - 1 / 1.5) < 1e-6


# --------------------------------------------------------------------------- code pages
def test_dbf_writers_refuse_characters_the_code_page_cannot_store(tmp_path, fx_install):
    """A DBF row with a character the code page cannot store is refused and nothing is
    written. The project part runs on a copy of the fake install's FxMain pages.DBF (its
    FxShapes row has TITLE 'FxShapes')."""
    from ctgedit.editing import UnencodableTextError
    from ctgedit.format import dbf as D, textcodec

    assert UnencodableTextError is textcodec.UnencodableTextError
    p = tmp_path / "t.dbf"
    # a one-field table (NAME C 10)
    hdr = bytearray(32)
    hdr[0] = 3
    import struct

    struct.pack_into("<IHH", hdr, 4, 0, 32 + 32 + 1, 1 + 10)
    field = bytearray(32)
    field[0:4] = b"NAME"
    field[11] = ord("C")
    field[16] = 10
    p.write_bytes(bytes(hdr) + bytes(field) + b"\x0d" + b"\x1a")
    with D.Dbf(str(p)) as d:
        with pytest.raises(UnencodableTextError) as e:
            d.encode({"NAME": "Pump \u03a9"})
        msg = str(e.value)
        assert "\u03a9" in msg and "U+03A9" in msg and "NAME" in msg and "t.dbf" in msg
        assert "?" not in d.encode({"NAME": "Pump \u20ac"}).decode(textcodec.CODEC)
        assert d.find("NAME", "\u03a9") == []
    before = p.read_bytes()
    from ctgedit.io.project import Project

    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    import shutil

    shutil.copyfile(fx_install.file("FxMain", "pages.DBF"), proj_dir / "pages.DBF")
    pages = (proj_dir / "pages.DBF").read_bytes()
    prj = Project(str(proj_dir))
    assert prj.page_record("FxShapes")["TITLE"] == "FxShapes"
    with pytest.raises(UnencodableTextError):
        prj.update_page_record("FxShapes", {"TITLE": "FxShapes → Tanks"})
    with pytest.raises(UnencodableTextError):
        prj.add_page_record("NewPage", {"TITLE": "☃"})
    assert (proj_dir / "pages.DBF").read_bytes() == pages  # nothing written
    assert prj.update_page_record("FxShapes", {"TITLE": "FxShapes – Tanks"})
    assert prj.page_record("FxShapes")["TITLE"] == "FxShapes – Tanks"
    assert p.read_bytes() == before


def test_page_and_element_names_the_code_page_cannot_store_are_refused(tmp_path, fx_install):
    """Page and element names the code page can't store are refused."""
    from ctgedit.editing import EditableDocument, UnencodableTextError
    import shutil

    proj = tmp_path / "proj"
    proj.mkdir()
    shutil.copyfile(fx_install.file("FxMain", "pages.DBF"), proj / "pages.DBF")
    shutil.copyfile(fx_install.page("FxShapes"), proj / "Pump.ctg")
    before = (proj / "pages.DBF").read_bytes()
    doc = EditableDocument.open(str(proj / "Pump.ctg"))
    target = proj / "Pump \u03a9.ctg"
    with pytest.raises(UnencodableTextError) as e:
        doc.save(str(target))
    assert "\u03a9" in str(e.value) and "page name" in str(e.value)
    assert not target.exists() and (proj / "pages.DBF").read_bytes() == before
    assert doc.path == str(proj / "Pump.ctg")  # still the page it was
    with pytest.raises(UnencodableTextError):
        EditableDocument.new_blank(str(proj / "Pump.ctg"), str(proj / "New \u2192.ctg"))
    assert not (proj / "New \u2192.ctg").exists()
    # no project: nothing is registered, the name is fine
    free = tmp_path / "Pump \u03a9.ctg"
    doc.save(str(free))
    assert free.exists()
    # a library element's name is its block name in the library (ANSI too)
    from ctgedit.editing import ElementDocument

    lib = str(tmp_path / "mine.ctl")
    el = ElementDocument.new_element(lib, "probe", "symbol")
    el.create_object("rectangle", (100, 100, 200, 150), colour=0x00FF00)
    with pytest.raises(UnencodableTextError):
        el.save(lib, "pr\u03a9be")
    assert not os.path.exists(lib)


_DBCS_PROBE = r"""
import codecs, json, os, random, sys
from ctgedit.format import textcodec as T
out = {"codec": T.CODEC, "multibyte": T.MULTIBYTE}
rnd = random.Random(7)
bad = 0
for _ in range(3000):
    b = bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 24)))
    s = b.decode(T.CODEC)
    if s.encode(T.CODEC) != b or s.encode(T.CODEC, "replace") != b:
        bad += 1
out["random_roundtrip_failures"] = bad
text = sys.argv[1]
base = sys.argv[2]
raw = text.encode(base)
out["decodes_as_text"] = raw.decode(T.CODEC) == text
out["encodes_back"] = text.encode(T.CODEC) == raw
out["unencodable"] = T.unencodable("x\U0001F600")
cut = T.truncate(raw, 3)
out["truncate_keeps_chars"] = len(cut) <= 3 and cut.decode(T.CODEC) == text[:len(cut.decode(T.CODEC))]
dec = codecs.getincrementaldecoder(T.CODEC)()
out["incremental"] = "".join(dec.decode(raw[i:i + 1]) for i in range(len(raw))) + dec.decode(b"", True) == text
try:
    T.encode_field("x\U0001F600", "the TITLE field of pages.DBF")
    out["refused"] = False
except T.UnencodableTextError as e:
    out["refused"] = "pages.DBF" in str(e)
print(json.dumps(out))
"""


@pytest.mark.parametrize(
    "cp,base,text",
    [
        ("932", "cp932", "\u65e5\u672c\u8a9e\u30c6\u30ad\u30b9\u30c8 \uff71"),  # kanji + katakana
        ("936", "gbk", "\u4e2d\u6587\u6807\u7b7e"),
        ("949", "cp949", "\ud55c\uad6d\uc5b4"),
        ("950", "cp950", "\u7e41\u9ad4\u4e2d\u6587"),
    ],
)
def test_double_byte_code_pages_decode_as_text_and_stay_lossless(cp, base, text):
    env = dict(os.environ, PYTHONPATH=ROOT, CTG_CODEPAGE=cp)
    r = subprocess.run(
        [PY, "-c", _DBCS_PROBE, text, base],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    import json

    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["codec"] == "ctg" + cp and got["multibyte"]
    assert got["random_roundtrip_failures"] == 0
    assert got["decodes_as_text"] and got["encodes_back"] and got["incremental"]
    assert got["unencodable"] == ["\U0001f600"] and got["refused"]
    assert got["truncate_keeps_chars"]


def test_cp936_euro_and_cp932_duplicates_stay_byte_exact():
    code = (
        "from ctgedit.format import textcodec as T\n"
        "import sys\n"
        "b = bytes.fromhex(sys.argv[1])\n"
        "s = b.decode(T.CODEC)\n"
        "print(ascii(s), s.encode(T.CODEC) == b)\n"
    )
    for cp, hexbytes, want in (
        ("936", "80", "'\\u20ac'"),  # Windows 936: 0x80 = euro
        ("932", "ed40", None),
    ):  # NEC duplicate of U+7E8A
        env = dict(os.environ, PYTHONPATH=ROOT, CTG_CODEPAGE=cp)
        r = subprocess.run([PY, "-c", code, hexbytes], capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 0, r.stderr
        shown, same = r.stdout.split()
        assert same == "True"
        if want:
            assert shown == want


def test_single_byte_code_page_unchanged():
    from ctgedit.format import textcodec as T

    if T.CODEPAGE != 1252:
        pytest.skip("not a 1252 machine")
    b = bytes(range(256))
    assert b.decode(T.CODEC).encode(T.CODEC) == b and not T.MULTIBYTE
    assert T.truncate("Pump \u20ac".encode(T.CODEC), 5) == b"Pump "
    rnd = random.Random(1)
    for _ in range(200):
        s = bytes(rnd.randrange(256) for _ in range(10))
        assert s.decode(T.CODEC).encode(T.CODEC) == s
