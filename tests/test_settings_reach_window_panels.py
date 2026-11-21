"""Window and panel settings reach every place they should."""

import os
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app


def _pump(n=3):
    for _ in range(n):
        _app().processEvents()


@pytest.fixture()
def theme():
    """The theme module; the application is put back as it was afterwards (the accent and
    the density back at their defaults first)."""
    app = _app()
    from ctgedit.render import scene as SC
    from ctgedit.ui.style import theme as T
    from ctgedit.ui.prefs.settings import app_settings

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
    st = app_settings()
    for key in ("ui/accent", "ui/density", "ui/overlay_opacity"):
        st.remove(key)
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


def _drop(*widgets):
    for w in widgets:
        w.hide()
        w.deleteLater()
    _pump()


# ------------------------------------------------------------------ ui/message_time#status
def test_messages_stay_reaches_the_plain_status_messages_and_hints():
    _app()
    from ctgedit.ui import notify as N
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.panels.status_items import StatusBar

    w = QtWidgets.QMainWindow()
    sb = StatusBar(w)
    w.setStatusBar(sb)
    try:
        for choice, factor in (("short", 0.7), ("normal", 1.0), ("long", 1.6)):
            app_settings().setValue("ui/message_time", choice)
            sb.showMessage("Copied 2 objects.", 4000)  # (Copy, Undo, Save ...)
            assert sb._timer.interval() == int(4000 * factor), choice
            sb.show_hint("Ctrl+K finds any command.", 6000)
            assert sb._timer.interval() == int(6000 * factor), choice
            sb.showMessage("Stays until the next one")
            assert not sb._timer.isActive()
        # the bell keeps a plain message by the caller's own time, whatever the setting
        c = N.centre(w)
        c.clear()
        sb.showMessage("Moved the guideline", 3000)  # (a hint: 4.8 s at Long)
        assert c.entries == []
        # a warning's 10 s floor is the Normal time: Short and Long scale it too
        app_settings().setValue("ui/message_time", "short")
        assert N.reading_ms("Not moved.", "warning") == 7000
        app_settings().setValue("ui/message_time", "long")
        assert N.reading_ms("Not moved.", "warning") == 16000
        app_settings().setValue("ui/message_time", "normal")
        assert N.reading_ms("Not moved.", "warning") == 10000
        assert N.scaled_ms(0) == 0
    finally:
        _drop(w)


def test_messages_stay_reaches_the_simulate_window_and_the_manual():
    _app()
    from ctgedit.ui import notify as N
    from ctgedit.ui.prefs.settings import app_settings

    w = QtWidgets.QMainWindow()
    sb = N.ScaledStatusBar(w)
    w.setStatusBar(sb)

    def running():
        """The intervals of the status bar's running timers (Qt's message timer)."""
        return [t.interval() for t in sb.findChildren(QtCore.QTimer) if t.isActive()]

    try:
        for choice, factor in (("short", 0.7), ("normal", 1.0), ("long", 1.6)):
            app_settings().setValue("ui/message_time", choice)
            w.statusBar().showMessage("Copied a picture of the page.", 4000)
            assert sb.currentMessage() == "Copied a picture of the page."
            assert running() == [int(4000 * factor)], choice
        w.statusBar().showMessage("Stays until the next one")
        assert sb.currentMessage() == "Stays until the next one" and running() == []
    finally:
        _drop(w)
    # the two windows that are not the editor's have one
    from ctgedit.ui.dialog.help_window import HelpWindow
    from ctgedit.ui.simulate.window import SimulateWindow

    for cls in (HelpWindow, SimulateWindow):
        other = cls()
        try:
            assert isinstance(other.statusBar(), N.ScaledStatusBar), cls
        finally:
            other.close()
            other.deleteLater()
            _pump()


# ------------------------------------------------------------------ ui/overlay_opacity#hud
def test_the_selection_toolbar_takes_overlay_opacity_the_theme_and_the_text_size(theme):
    app = _app()
    from ctgedit.ui.editor.canvas_options import apply_canvas_nav
    from ctgedit.ui.editor.hud import SelectionHUD
    from ctgedit.ui.prefs.settings import app_settings

    theme.apply_theme(app, "dark", 1.0)
    view = QtWidgets.QGraphicsView()
    # (a window with the bar's default commands: the bar finds them by their ids,
    # Settings > Ribbon > Selection toolbar)
    win = QtWidgets.QMainWindow()
    for n in ("act_obj_props", "act_group", "act_front", "act_back", "act_del"):
        a = QtGui.QAction(n, win)
        a.setObjectName(n)
        setattr(win, n, a)
    placed = []
    win._schedule_hud = lambda *a: placed.append(a)
    hud = SelectionHUD(view, win)
    assert len(hud._buttons) == 5
    try:
        # the defaults (dark, 100 %, Overlay opacity 100 %)
        assert hud.styleSheet() == (
            "#SelectionHUD { background: rgba(34, 38, 44, 225); "
            "border: 1px solid #3a3f46; border-radius: 5px; }"
        )
        assert hud._buttons[0].iconSize() == QtCore.QSize(14, 14)
        m = hud.layout().contentsMargins()
        assert (m.left(), m.top(), m.right(), m.bottom()) == (4, 2, 4, 2)
        # Settings > Canvas > Overlay opacity, applied by the dialog's OK: the alpha only
        app_settings().setValue("ui/overlay_opacity", 60)
        apply_canvas_nav(types.SimpleNamespace(hud=hud))
        assert "rgba(34, 38, 44, 135)" in hud.styleSheet()  # (225 x 60 %)
        assert "border-radius: 5px" in hud.styleSheet()
        assert placed == []  # (hidden: not placed)
        # the light theme at 150 %: its overlay and border colours, larger icons
        theme.apply_theme(app, "light", 1.5)
        _pump()
        bg = QtGui.QColor(theme.COLORS["overlay"])
        assert "rgba(%d, %d, %d, 135)" % (bg.red(), bg.green(), bg.blue()) in hud.styleSheet()
        assert theme.COLORS["border"] in hud.styleSheet()
        assert hud._buttons[0].iconSize() == QtCore.QSize(21, 21)
        # a visible bar is placed again at its new size, not grown where it was
        view.show()
        hud.show()
        _pump()
        assert hud.isVisible()
        theme.apply_theme(app, "light", 2.0)
        _pump()
        assert not hud.isVisible() and placed
        assert hud._buttons[0].iconSize() == QtCore.QSize(28, 28)
        # (no window that places it: it takes its new size where it is)
        del win._schedule_hud
        hud.show()
        hud.resize(5, 5)
        hud._theme_changed()
        assert hud.isVisible() and hud.size() == hud.sizeHint()
    finally:
        _drop(hud, view, win)


# ------------------------------------------------------------ pagedefaults/resolution#template
def test_a_new_template_with_no_page_open_takes_the_page_defaults_resolution(tmp_path, monkeypatch):
    _app()
    from ctgedit.ui.dialog.dialogs import set_page_defaults
    from ctgedit.ui.window import files as F
    from ctgedit.io import citect_config, projectcit
    from ctgedit.ui.dialog import dialogs as D

    folder = tmp_path / "Loose"  # (no PROJECT.CIT: ctgedit's own defaults)
    folder.mkdir()
    lib = str(folder / "mytemplates.ctt")
    # never set: 1920 x 1080, not the citect.ini value for new projects nor this screen's
    monkeypatch.setattr(
        citect_config,
        "template_defaults",
        lambda: {"style": "Tab_Style_1", "resolution": "HD768", "title_bar": False},
    )
    monkeypatch.setattr(D, "screen_resolution", lambda: "SXGA")
    assert F.new_template_size(lib) == F.NEW_TEMPLATE_SIZE == (1920, 1080)
    # 'Default' (the built-in one too) is never this screen's
    set_page_defaults({"resolution": "Default"}, str(folder))
    assert F.new_template_size(lib) == (1920, 1080)
    real = projectcit.page_defaults
    monkeypatch.setattr(
        projectcit,
        "page_defaults",
        lambda _d: {"style": "Tab_Style_1", "resolution": "Default", "title_bar": False, "background": 0},
    )
    assert D.page_defaults(str(folder), resolve_default=False)["source"] == "project"
    assert F.new_template_size(lib) == (1920, 1080)
    monkeypatch.setattr(
        projectcit,
        "page_defaults",
        lambda _d: {"style": "Tab_Style_1", "resolution": "HD768", "title_bar": False, "background": 0},
    )
    assert F.new_template_size(lib) == (1366, 768)  # (a project's named size)
    monkeypatch.setattr(projectcit, "page_defaults", real)
    assert set_page_defaults({"resolution": "XGA"}, str(folder)) == "settings"
    got = {}

    class FakeElement:
        @classmethod
        def new_element(cls, lib, elem, kind, page_size=None):
            got["size"] = page_size
            return types.SimpleNamespace(display_name=elem)

    monkeypatch.setattr(F, "ElementDocument", FakeElement)
    shown = []
    win = types.SimpleNamespace(
        doc=None,
        _locked=set(),
        set_document=shown.append,
        statusBar=lambda: types.SimpleNamespace(showMessage=lambda *_a: None),
    )
    F.PageFiles._create_library_element(win, "template", lib, "pagecontent")
    assert got["size"] == (1024, 768) and shown
    assert F.new_template_size(lib) == (1024, 768)
    set_page_defaults({"resolution": "UHD4K"}, str(folder))
    assert F.new_template_size(lib) == (3840, 2160)
    set_page_defaults({"resolution": "User"}, str(folder))  # (no size of its own)
    assert F.new_template_size(lib) == F.NEW_TEMPLATE_SIZE == (1920, 1080)
    # a super genie keeps its own size
    F.PageFiles._create_library_element(win, "supergenie", lib, "!pop")
    assert got["size"] == (400, 300)


# ------------------------------------------------------------------ ui/density#objects
def test_the_objects_rows_follow_the_density_and_the_text_size(theme):
    app = _app()
    from ctgedit.ui.panels.objects_dock import F_ROOT, ObjectsDock
    from ctgedit.ui.prefs.settings import app_settings

    dock = ObjectsDock()
    try:
        assert dock._tree.iconSize() == QtCore.QSize(16, 16)
        z = dock._delegate.zones(QtCore.QRect(0, 0, 300, 40), 0, F_ROOT)
        assert z["eye"].width() == 18 and z["lock"].width() == 18
        app_settings().setValue("ui/density", "touch")
        theme.apply_theme(app, "dark", 1.5)
        _pump()
        assert dock._tree.iconSize() == QtCore.QSize(30, 30)  # 20 px at 150 %
        z = dock._delegate.zones(QtCore.QRect(0, 0, 300, 40), 0, F_ROOT)
        assert z["eye"].width() == 36 and z["lock"].width() == 36  # 24 px at 150 %
        # a cell is never taller than its row
        z = dock._delegate.zones(QtCore.QRect(0, 0, 300, 30), 0, F_ROOT)
        assert z["eye"].height() <= 30
        app_settings().setValue("ui/density", "comfortable")
        theme.apply_theme(app, "dark", 1.5)
        _pump()
        assert dock._tree.iconSize() == QtCore.QSize(24, 24)
        z = dock._delegate.zones(QtCore.QRect(0, 0, 300, 40), 0, F_ROOT)
        assert z["eye"].width() == 27
    finally:
        _drop(dock)


# ------------------------------------------------------------------ ui/text_size#library
def test_the_library_cards_follow_the_text_size(theme):
    app = _app()
    from ctgedit.ui.panels.library_panel import HeaderRole, LibraryPanel

    panel = LibraryPanel()
    d = panel._delegate
    model = QtGui.QStandardItemModel()
    model.appendRow(QtGui.QStandardItem("plain"))
    head = QtGui.QStandardItem("header")
    head.setData("Library", HeaderRole)
    model.appendRow(head)

    def paint_once():
        img = QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QtGui.QPainter(img)
        try:
            d.paint(p, QtWidgets.QStyleOptionViewItem(), model.index(0, 0))
        finally:
            p.end()

    try:
        paint_once()
        assert d.f_name.pixelSize() == 12 and d.f_sub.pixelSize() == 11
        tall = d.card_height(120)
        opt = QtWidgets.QStyleOptionViewItem()
        head_h = d.sizeHint(opt, model.index(1, 0)).height()
        assert head_h == 28
        theme.apply_theme(app, "dark", 1.5)
        _pump()
        paint_once()
        assert d.f_name.pixelSize() == theme.type_px("body") == 18
        assert d.f_sub.pixelSize() == theme.type_px("caption")
        assert d.f_head.pixelSize() == 18 and d.f_head.bold()
        assert d.f_chip.pixelSize() == 14  # (9 px at 100 %)
        # the name and library lines grow with it: 17 + 14 px at 100 %
        assert d.card_height(120) == tall + (theme.px(17) - 17) + (theme.px(14) - 14)
        assert d.sizeHint(opt, model.index(1, 0)).height() == 42
    finally:
        _drop(panel)


# ------------------------------------------------------------------ ui/accent#chrome
def _outline_colour(img: QtGui.QImage, rect: QtCore.QRect) -> QtGui.QColor:
    """The colour on the left edge of the outline round ``rect`` (a cell)."""
    return img.pixelColor(rect.left() - 1, rect.center().y())


def test_the_colour_picker_cells_follow_the_accent_the_theme_and_the_text_size(theme):
    app = _app()
    from ctgedit.format import colourpalette as CP
    from ctgedit.ui.colour_picker import PaletteGrid
    from ctgedit.ui.prefs.settings import app_settings

    theme.apply_theme(app, "dark", 1.0)
    grid = PaletteGrid(CP.Palette([]))
    try:
        assert grid.width() == 16 * 15 + 1
        # the defaults: white under the pointer and the blue accent
        grid.set_selected(3)
        grid._hover = 40
        img = grid.grab().toImage()
        assert _outline_colour(img, grid.cell_rect(40)).name() == "#ffffff"
        assert _outline_colour(img, grid.cell_rect(3)).name() == "#4da3ff"
        app_settings().setValue("ui/accent", "green")
        theme.apply_theme(app, "light", 1.5)
        _pump()
        accent = theme.color("accent")
        assert accent.name() != "#4da3ff"
        assert grid.width() == 16 * (21 + 1) + 1  # 21 px cells at 150 %
        grid.set_selected(3)
        grid._hover = 40
        img = grid.grab().toImage()
        sel = _outline_colour(img, grid.cell_rect(3))
        hov = _outline_colour(img, grid.cell_rect(40))
        assert sel.name() == accent.name()
        assert hov.name() == theme.color("on_selection").name()  # (dark on the light theme)
        assert theme.contrast(hov, theme.color("bg")) >= 3.0
    finally:
        _drop(grid)


def test_the_style_preview_outline_takes_the_accent(theme):
    app = _app()
    from ctgedit.model.document import ObjectKind
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.panels.styles_gallery import Style, paint_preview

    app_settings().setValue("ui/accent", "orange")
    theme.apply_theme(app, "dark", 1.0)
    top = QtWidgets.QWidget()
    view = QtWidgets.QGraphicsView(top)
    obj = types.SimpleNamespace(
        kind=ObjectKind.TEXT,
        animations=None,
        geometry=types.SimpleNamespace(x=2.5, y=2.5, extent_x=20, extent_y=10),
    )
    top.view = view
    top.doc = types.SimpleNamespace(page=types.SimpleNamespace(objects=[obj]))
    top._style_preview = (Style("Plain", {}, "text"), [0])
    try:
        img = QtGui.QImage(30, 20, QtGui.QImage.Format_ARGB32)
        img.fill(QtGui.QColor(0, 0, 0))
        p = QtGui.QPainter(img)
        try:
            paint_preview(view, p, None)
        finally:
            p.end()
        want = theme.canvas_colour("selection")
        got = img.pixelColor(4, 2)  # (the first dash of the top edge)
        k = 220 / 255.0
        for a, b in ((got.red(), want.red()), (got.green(), want.green()), (got.blue(), want.blue())):
            assert abs(a - b * k) <= 6, (got.name(), want.name())
        # a black text colour is outlined in it too (black would not show on a dark page)
        top._style_preview = (Style("Black", {"text_colour": 0}, "text"), [0])
        img.fill(QtGui.QColor(0, 0, 0))
        p = QtGui.QPainter(img)
        try:
            paint_preview(view, p, None)
        finally:
            p.end()
        got = img.pixelColor(4, 2)
        for a, b in ((got.red(), want.red()), (got.green(), want.green()), (got.blue(), want.blue())):
            assert abs(a - b * k) <= 6, (got.name(), want.name())
    finally:
        _drop(view, top)
