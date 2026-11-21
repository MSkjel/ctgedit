"""The ribbon."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest  # noqa: E402
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from qtkit import app as _app

#: the page the need-a-page tests open: FxSa's FxSaContent in the fake install (a content
#: page on the fx_sa template: genies, an alarm genie, a symbol)
PAGE = ("FxSaContent", "FxSa")


def _pump(n=6):
    app = _app()
    for _ in range(n):
        app.processEvents()


@pytest.fixture()
def win(fx_install, tmp_path):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1600, 1000)
    w.show()
    w.ribbon._tabs.setCurrentIndex(0)
    _pump()
    w._test_fx = fx_install
    w._test_tmp = tmp_path
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()


def _open(w, monkeypatch):
    w.open_document(w._test_fx.page(*PAGE))  # (the fake install's copy)
    _pump()


def _button(w, action):
    """The ribbon button hosting ``action`` (a shown one first)."""
    found = [b for b in w.ribbon.findChildren(QtWidgets.QToolButton) if b.defaultAction() is action]
    shown = [b for b in found if b.isVisible()]
    return (shown or found or [None])[0]


# --------------------------------------------------------------------------- rich tool tips
def test_the_rectangle_tip_names_its_key_and_says_what_it_does(win):
    from ctgedit.ui.style import tips

    a = win.tool_actions["rectangle"]
    text = tips.rich_tip(a, _button(win, a))
    assert ">Rectangle<" in text and ">R<" in text
    assert "Drag on the page to draw a filled rectangle." in text
    assert "F1 for help" in text
    # the plain tip with the setting off
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue(tips.SETTING, False)
    assert tips.tip_for(_button(win, a)) == "Rectangle (R)"
    app_settings().setValue(tips.SETTING, True)


def test_a_greyed_command_says_what_it_needs(win):
    from ctgedit.ui.style import tips

    a = win.align_actions[0]
    assert not a.isEnabled()
    assert "Select two or more objects." in tips.rich_tip(a)
    assert "Open a page first." in tips.rich_tip(win.act_goto)
    assert "Select an object first." in tips.rich_tip(win.act_dup)
    assert "nothing to undo" in tips.rich_tip(win.act_undo)


def test_the_tip_follows_a_key_bound_since(win):
    from ctgedit.ui.style import tips

    b = _button(win, win.act_dup)
    assert "Ctrl+D" in tips.tip_for(b)
    win.act_dup.setShortcut(QtGui.QKeySequence("Ctrl+Alt+D"))
    assert "Ctrl+Alt+D" in tips.tip_for(b)
    assert "Shortcut: Ctrl+Alt+D" in b.accessibleDescription()


def test_hovering_a_ribbon_button_shows_the_rich_tip(win):
    b = _button(win, win.act_open)  # (a Quick Access button: always shown)
    pos = b.rect().center()
    ev = QtGui.QHelpEvent(QtCore.QEvent.ToolTip, pos, b.mapToGlobal(pos))
    QtWidgets.QApplication.sendEvent(b, ev)
    _pump()
    shown = QtWidgets.QToolTip.text()
    QtWidgets.QToolTip.hideText()
    assert ">Open<" in shown and ">Ctrl+O<" in shown
    assert "Open a page or a library element of a project." in shown


def test_every_ribbon_quick_access_and_utility_button_has_the_rich_tip(win):
    from ctgedit.ui.style import tips

    rb = win.ribbon
    for b in rb.findChildren(QtWidgets.QToolButton):
        if b.defaultAction() is not None and not isinstance(b.parentWidget(), QtWidgets.QTabBar):
            assert b.property("ctgRichTip"), b.defaultAction().text()
    # the sentences pass the UI text rules (no dashes)
    from ctgedit.ui.main_window_base import DESCRIPTIONS, NEEDS_CUSTOM

    for text in list(DESCRIPTIONS.values()) + list(NEEDS_CUSTOM.values()):
        assert "\u2014" not in text and "\u2013" not in text and " -- " not in text
        assert text.endswith("."), text
    assert tips.NEEDS["multi"] == "Select two or more objects."


def test_the_accessible_description_names_the_key(win):
    b = _button(win, win.tool_actions["rectangle"])
    assert "Shortcut: R" in b.accessibleDescription()


# --------------------------------------------------------------------------- contextual tabs
def _accent_pixels(img, rect, accent):
    n = 0
    for y in range(rect.top(), rect.bottom()):
        for x in range(rect.left(), rect.right()):
            c = img.pixelColor(x, y)
            if (
                c.saturationF() > 0.35
                and abs(c.hslHueF() - accent.hslHueF()) < 0.05
                and c.lightnessF() > 0.35
            ):
                n += 1
    return n


def test_a_contextual_tab_is_painted_in_the_accent(win):
    from ctgedit.ui.style.theme import color

    rb = win.ribbon
    rb.set_context_tabs(["Text"])
    _pump()
    bar = rb._tabs
    i = next(k for k in range(bar.count()) if bar.tabText(k) == "Text")
    img = bar.grab().toImage()
    accent = color("accent")
    text_area = bar.tabRect(i).adjusted(4, 7, -4, -2)  # below the accent bar
    assert _accent_pixels(img, text_area, accent) > 15
    other = bar.tabRect(1).adjusted(4, 7, -4, -2)  # Utilities: not tinted
    assert _accent_pixels(img, other, accent) == 0
    assert bar.context_indexes() == {i}
    # two contextual tabs: the 'Selection' caption over the run
    rb.set_context_tabs(["Text", "Genie"])
    _pump()
    assert len(bar.context_indexes()) == 2


# --------------------------------------------------------------------------- the flyout
def test_collapsed_a_tab_click_shows_its_commands_over_the_page(win, monkeypatch):
    _open(win, monkeypatch)
    rb = win.ribbon
    win.act_collapse_ribbon.setChecked(True)
    _pump()
    assert rb.collapsed() and rb.height() == rb.collapsed_height()
    canvas = win.view.geometry()
    bar = rb._tabs
    QTest.mouseClick(bar, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, bar.tabRect(0).center())
    _pump(10)
    assert rb.flyout_open()
    assert win.view.geometry() == canvas  # the page does not move
    assert rb.height() == rb.collapsed_height()
    rect_btn = next(
        b
        for b in rb._flyout.findChildren(QtWidgets.QToolButton)
        if b.defaultAction() is win.tool_actions["rectangle"] and b.isVisible()
    )
    QTest.mouseClick(
        rect_btn, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(8, rect_btn.height() // 2)
    )
    _pump(10)
    assert not rb.flyout_open()
    assert win.tool_actions["rectangle"].isChecked()
    assert win.view.geometry() == canvas
    # Esc closes it too
    QTest.mouseClick(bar, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, bar.tabRect(0).center())
    _pump(10)
    assert rb.flyout_open()
    QTest.keyClick(rb._flyout, QtCore.Qt.Key_Escape)
    _pump()
    assert not rb.flyout_open()
    # a contextual tab that appears while collapsed is tinted, never opened
    rb.set_context_tabs(["Text"], activate=True)
    _pump()
    assert not rb.flyout_open() and bar.tabText(bar.currentIndex()) != "Text"
    win.act_collapse_ribbon.setChecked(False)
    _pump()
    assert not rb.collapsed() and rb.height() == rb.expanded_height()


def test_collapse_the_ribbon_is_a_view_command_on_ctrl_f1(win):
    a = win.act_collapse_ribbon
    assert a.isCheckable() and a.shortcut().toString() == "Ctrl+F1"
    from tests.test_ribbon import _menu_actions

    assert a in _menu_actions(win.menuBar())
    assert win.ribbon._chevron.defaultAction() is a
    a.trigger()
    assert win.ribbon.collapsed() and a.isChecked()
    win.ribbon.toggle_collapsed()  # the double-click path keeps it in step
    assert not a.isChecked() and not win.ribbon.collapsed()


def test_esc_in_a_dialog_a_flyout_command_opened_closes_the_dialog(win):
    """While the flyout is open, Esc in a modal dialog (one a flyout command opened) is the
    dialog's: it cancels the dialog, and the flyout stays until the command returns."""
    rb = win.ribbon
    win.act_collapse_ribbon.setChecked(True)
    _pump()
    rb.open_flyout()
    _pump(10)
    assert rb.flyout_open()
    dlg = QtWidgets.QDialog(win)
    dlg.setWindowTitle("Test")
    seen = {}

    def poke():
        QTest.keyClick(dlg, QtCore.Qt.Key_Escape)
        seen["dialog"] = dlg.isVisible()
        seen["flyout"] = rb.flyout_open()
        if dlg.isVisible():
            dlg.reject()

    QtCore.QTimer.singleShot(100, poke)
    result = dlg.exec()
    assert seen == {"dialog": False, "flyout": True}
    assert result == QtWidgets.QDialog.Rejected
    QTest.keyClick(rb._flyout, QtCore.Qt.Key_Escape)  # back in the window: Esc is the flyout's
    _pump()
    assert not rb.flyout_open()
    dlg.deleteLater()
    win.act_collapse_ribbon.setChecked(False)
    _pump()


def test_the_chevron_tip_says_what_a_click_does_now(win):
    from ctgedit.ui.style import tips
    from ctgedit.ui.prefs.settings import app_settings

    chev = win.ribbon._chevron
    assert chev.property("ctgRichTip")
    text = tips.tip_for(chev)
    assert ">Collapse the Ribbon<" in text and "Show only the ribbon" in text
    win.act_collapse_ribbon.setChecked(True)
    _pump()
    try:
        text = tips.tip_for(chev)
        assert ">Expand the Ribbon<" in text and ">Ctrl+F1<" in text
        assert "Show the ribbon commands again." in text
        assert "Collapse" not in text and "Show only" not in text
        app_settings().setValue(tips.SETTING, False)
        assert tips.tip_for(chev) == "Expand the Ribbon (Ctrl+F1)"
    finally:
        app_settings().setValue(tips.SETTING, True)
        win.act_collapse_ribbon.setChecked(False)
        _pump()
    assert ">Collapse the Ribbon<" in tips.tip_for(chev)


def test_the_outline_sentences_name_no_key(win):
    """The tip's key cap follows a rebound key; the sentence must not name a fixed one."""
    from ctgedit.ui.style import tips
    from ctgedit.ui.main_window_base import DESCRIPTIONS

    for k in ("tool.rectangle", "tool.ellipse"):
        assert "Shift+" not in DESCRIPTIONS[k], k
    a = win.tool_actions["rectangle_outline"]
    a.setShortcut(QtGui.QKeySequence("Ctrl+Alt+R"))
    text = tips.rich_tip(win.tool_actions["rectangle"])
    assert "Shift+R" not in text and "The arrow offers the outline." in text


# --------------------------------------------------------------------------- arrows
def _ink_mask(img, bg):
    out = set()
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if (
                abs(c.red() - bg.red())
                + abs(c.green() - bg.green())
                + abs(c.blue() - bg.blue())
                + abs(c.alpha() - bg.alpha())
                > 60
            ):
                out.add((x, y))
    return out


def _is_magenta(c):
    return c.alpha() > 30 and c.red() > 120 and c.blue() > 120 and c.green() < 0.6 * c.red()


def test_menu_arrows_never_sit_on_their_icon_or_text(win, monkeypatch):
    from ctgedit.ui.style import theme

    theme.apply_theme(_app(), "dark", 1.0)  # (the flat look the editor runs with)
    _open(win, monkeypatch)
    rb = win.ribbon
    arrow_png = QtGui.QImage(16, 8, QtGui.QImage.Format_ARGB32)
    arrow_png.fill(QtGui.QColor(255, 0, 255))
    magenta = str(win._test_tmp / "magenta.png").replace("\\", "/")
    arrow_png.save(magenta)
    checked = with_arrow = 0
    for b in rb.findChildren(QtWidgets.QToolButton):
        if b.menu() is None or not b.isVisible() or b.objectName() == "RibbonCaption":
            continue
        if b.popupMode() == QtWidgets.QToolButton.MenuButtonPopup:
            opt = QtWidgets.QStyleOptionToolButton()
            b.initStyleOption(opt)
            st = b.style()
            menu = st.subControlRect(
                QtWidgets.QStyle.CC_ToolButton, opt, QtWidgets.QStyle.SC_ToolButtonMenu, b
            )
            # the arrow's own 12 px part at the right edge, beside the icon and the text
            assert menu.width() <= 14 and menu.right() >= b.width() - 2, (b.defaultAction().text(), menu)
            label = QtCore.QRect(0, 0, b.width() - menu.width(), b.height())
            icon_w = b.iconSize().width()
            if b.toolButtonStyle() != QtCore.Qt.ToolButtonTextBesideIcon:
                assert (label.width() - icon_w) // 2 + icon_w <= menu.left(), b.defaultAction().text()
        # in pixels: the arrow drawn in magenta; its pixels keep apart from the label's
        sheet = b.styleSheet()
        b.setStyleSheet(
            sheet + '\nQToolButton::menu-indicator { image: url("%s"); }'
            '\nQToolButton::menu-arrow { image: url("%s"); }' % (magenta, magenta)
        )
        img = b.grab().toImage()
        b.setStyleSheet(sheet)
        from collections import Counter

        bg = QtGui.QColor.fromRgba(
            Counter(img.pixel(x, y) for y in range(img.height()) for x in range(img.width())).most_common(1)[
                0
            ][0]
        )
        ink = _ink_mask(img, bg)
        arrow = {(x, y) for x, y in ink if _is_magenta(img.pixelColor(x, y))}
        label = ink - arrow
        name = b.text() or (b.defaultAction().text() if b.defaultAction() else b.objectName())
        if arrow and label:
            ax = [x for x, _y in arrow]
            ay = [y for _x, y in arrow]
            box = QtCore.QRect(min(ax), min(ay), max(ax) - min(ax) + 1, max(ay) - min(ay) + 1)
            lx = [x for x, _y in label]
            ly = [y for _x, y in label]
            lab = QtCore.QRect(min(lx), min(ly), max(lx) - min(lx) + 1, max(ly) - min(ly) + 1)
            assert not box.intersects(lab), (name, box, lab)
            with_arrow += 1
        checked += 1
    assert checked >= 6 and with_arrow >= checked // 2, (checked, with_arrow)


def test_quick_access_save_has_its_own_arrow(win):
    rb = win.ribbon
    save = _button(win, win.act_save)
    arrow = next(b for b in rb.findChildren(QtWidgets.QToolButton) if b.objectName() == "QatArrow")
    assert save.menu() is None and save.popupMode() != QtWidgets.QToolButton.MenuButtonPopup
    acts = arrow.menu().actions()
    assert win.act_save_as in acts and win.act_save_all in acts
    assert arrow.popupMode() == QtWidgets.QToolButton.InstantPopup
    assert arrow.geometry().left() >= save.geometry().right()
    assert "QWidget#RibbonQat QToolButton:hover" in rb.styleSheet()


# --------------------------------------------------------------------------- greyed, no page
def test_greyed_commands_look_greyed(win):
    assert "QToolButton:disabled" in win.ribbon.styleSheet()
    from ctgedit.ui.style import theme

    assert theme.DISABLED_ICON_OPACITY <= 0.4


def test_with_no_page_the_design_tab_offers_new_and_open(win, monkeypatch):
    rb = win.ribbon
    design = rb.page_of("Design")
    assert rb.empty_shown(design)
    empty = design.findChild(QtWidgets.QWidget, "RibbonEmpty")
    assert empty.isVisible()
    # (no instruction text: the start page already says how to begin)
    assert empty.findChild(QtWidgets.QLabel) is None
    hosted = [b.defaultAction() for b in empty.findChildren(QtWidgets.QToolButton)]
    assert hosted == [win.act_new, win.act_open]
    assert not any(g.isVisible() for g in rb.groups_of("Design"))
    _open(win, monkeypatch)
    assert not rb.empty_shown(design) and not empty.isVisible()
    assert all(g.isVisible() for g in rb.groups_of("Design") if not g.hidden_by_user)


# --------------------------------------------------------------------------- text size
def test_a_text_size_change_resizes_the_ribbon_at_once(win, monkeypatch):
    from ctgedit.ui.style import theme

    app = _app()
    # (as the editor runs: its theme applied at start, then the text size changed)
    theme.apply_theme(app, "dark", 1.0)
    _open(win, monkeypatch)
    try:
        _pump()
        theme.apply_theme(app, "dark", 1.5)
        _pump(10)
        rb = win.ribbon
        assert rb.height() == theme.px(104)
        for g in rb.groups_of("Design"):
            for b in g.findChildren(QtWidgets.QToolButton):
                if not b.isVisible() or b.objectName() == "RibbonCaption":
                    continue
                # nothing cut: each button is as big as its contents ask
                hint = b.sizeHint()
                assert b.height() >= hint.height() - 1, (g.caption, b.text(), b.height(), hint)
                assert b.width() >= b.minimumSizeHint().width() - 1, (g.caption, b.text())
        big = _button(win, win.act_dup)
        assert big.height() == theme.px(64) and big.iconSize().width() == theme.px(28)
        # the ribbon's buttons fit inside the ribbon
        for b in rb.findChildren(QtWidgets.QToolButton):
            if b.isVisible() and rb.isAncestorOf(b):
                bottom = b.mapTo(rb, QtCore.QPoint(0, b.height())).y()
                assert bottom <= rb.height() + 1, b.text()
    finally:
        theme.apply_theme(app, "dark", 1.0)
        _pump()


def test_the_restart_note_names_only_the_selection_toolbar():
    import inspect
    from ctgedit.ui.prefs import settings_dialog

    src = inspect.getsource(settings_dialog)
    assert "Some buttons keep their size" not in src
    md = open(
        os.path.join(os.path.dirname(settings_dialog.__file__), "..", "..", "help", "pages", "settings.md"),
        encoding="utf-8",
    ).read()
    assert "some buttons keep their size" not in md


# --------------------------------------------------------------------------- the toolbox
def test_the_toolbox_has_groups_distinct_icons_and_a_labels_mode(win):
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.editor.toolbox import LABELS_KEY

    win.act_show_tools.setChecked(True)
    win._toggle_toolbox()
    _pump()
    tb = win._toolbox
    assert len(tb.separators) >= 3
    special = [
        win.special_tool_actions[k]
        for k in ("process_analyst", "scheduler", "db_exchange", "pelco")
        if k in win.special_tool_actions
    ]
    keys = {a.icon().cacheKey() for a in special}
    assert len(keys) == len(special)
    assert win.special_tool_actions["activex"].icon().cacheKey() not in keys
    assert all(b.property("ctgRichTip") for b in tb.buttons)
    cols = {tb._grid.getItemPosition(tb._grid.indexOf(b))[1] for b in tb.buttons}
    assert cols == {0, 1}
    tb.set_labels(True)
    _pump()
    assert app_settings().value(LABELS_KEY, type=bool) is True
    # (one column while it fits the screen; the groups go on in a further column, whole,
    # rather than below the screen)
    cols = [tb.column_of(b) for b in tb.buttons]
    assert cols[0] == 0 and cols == sorted(cols)
    room = tb._room()
    for c in set(cols):
        assert sum(w.sizeHint().height() for w, k in tb._columns.items() if k == c) <= room or (
            sum(1 for k in tb._columns.values() if k == c) == 1
        )
    assert all(b.toolButtonStyle() == QtCore.Qt.ToolButtonTextBesideIcon for b in tb.buttons)
    assert [h.text() for h in tb.headings][:3] == ["Draw", "Text and data", "Place"]
    tb.set_labels(False)
    win.act_show_tools.setChecked(False)
    win._toggle_toolbox()


# --------------------------------------------------------------------------- F1 while pointing
@pytest.fixture()
def cursor():
    """The mouse goes back where it was (a later test's F1 must not find a button under it)."""
    pos = QtGui.QCursor.pos()
    yield
    QtGui.QCursor.setPos(pos)
    _pump()


def _f1_over(win, button, monkeypatch):
    """F1 as a real key (QTest goes through the shortcut map) with the mouse over ``button``
    and the keyboard focus on the canvas: the topics the manual is asked to open."""
    from ctgedit.ui import help as UH

    shown = []
    monkeypatch.setattr(UH, "show_help", lambda topic="index", *a, **k: shown.append(topic))
    win.activateWindow()
    win.view.setFocus()
    _pump()
    QtGui.QCursor.setPos(button.mapToGlobal(button.rect().center()))
    _pump()
    assert QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos()) is button
    QTest.keyClick(win.view, QtCore.Qt.Key_F1)
    _pump()
    return shown


def test_f1_while_pointing_at_a_command_button_opens_its_topic(win, monkeypatch, cursor):
    _open(win, monkeypatch)
    win.tool_actions["select"].trigger()
    assert _f1_over(win, _button(win, win.tool_actions["rectangle"]), monkeypatch) == ["drawing#rectangles"]
    assert _f1_over(win, _button(win, win.tool_actions["text"]), monkeypatch) == ["drawing#text"]
    assert _f1_over(win, _button(win, win.act_save), monkeypatch) == ["the-window#the-ribbon"]
    # the toolbox's buttons too, while the editor window is the active one
    win.act_show_tools.setChecked(True)
    win._toggle_toolbox()
    _pump()
    try:
        pipe = next(b for b in win._toolbox.buttons if b.defaultAction() is win.tool_actions["pipe"])
        assert _f1_over(win, pipe, monkeypatch) == ["drawing#pipes"]
    finally:
        win.act_show_tools.setChecked(False)
        win._toggle_toolbox()
        _pump()
    # away from the buttons F1 is the editor's context (the Select tool, nothing selected)
    from ctgedit.ui import help as UH

    shown = []
    monkeypatch.setattr(UH, "show_help", lambda topic="index", *a, **k: shown.append(topic))
    win.activateWindow()
    win.view.setFocus()
    QtGui.QCursor.setPos(win.view.mapToGlobal(win.view.rect().center()))
    _pump()
    QTest.keyClick(win.view, QtCore.Qt.Key_F1)
    _pump()
    assert shown == [win.context_help_topic()] and shown != ["drawing#rectangles"]
    # a click on Help > ctgedit Help (the mouse where the menu was) opens the context too
    rect = _button(win, win.tool_actions["rectangle"])
    QtGui.QCursor.setPos(rect.mapToGlobal(rect.rect().center()))
    _pump()
    shown.clear()
    win.act_help.trigger()
    assert shown == [win.context_help_topic()]


def test_f1_in_another_window_puts_the_button_under_the_mouse_before_the_focus(win, monkeypatch, cursor):
    """The application's F1 rule (a dialog or the toolbox window active): a command button
    under the mouse wins over the focused widget (the Objects panel here)."""
    _open(win, monkeypatch)
    f1 = QtWidgets.QApplication.instance()._ctg_f1
    b = _button(win, win.tool_actions["rectangle"])
    win.dock_objects.show()
    win.activateWindow()
    field = next(
        w
        for w in win.dock_objects.findChildren(QtWidgets.QWidget)
        if w.focusPolicy() != QtCore.Qt.NoFocus and w.isVisible()
    )
    field.setFocus()
    _pump()
    assert QtWidgets.QApplication.focusWidget() is field
    QtGui.QCursor.setPos(b.mapToGlobal(b.rect().center()))
    _pump()
    assert f1.resolve() == "drawing#rectangles"
    QtGui.QCursor.setPos(win.view.mapToGlobal(win.view.rect().center()))
    _pump()
    assert f1.resolve() == "objects-panel"
    # a dialog over the editor: a ribbon button behind it is not what F1 is about
    from ctgedit.ui import help as UH

    dlg = QtWidgets.QDialog(win)
    UH.set_help(dlg, "page-properties")
    dlg.setGeometry(win.geometry().center().x(), win.geometry().center().y(), 200, 100)
    dlg.show()
    dlg.activateWindow()
    edit = QtWidgets.QLineEdit(dlg)
    edit.show()
    edit.setFocus()
    _pump()
    try:
        assert QtWidgets.QApplication.activeWindow() is dlg
        QtGui.QCursor.setPos(b.mapToGlobal(b.rect().center()))
        _pump()
        assert QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos()) is b
        assert f1.resolve() == "page-properties"
    finally:
        dlg.close()
        dlg.deleteLater()
        _pump()


# --------------------------------------------------------------------------- widths
def test_a_hosted_command_whose_name_grows_measures_its_group_again(win):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("ui/ribbon_custom", ["act_undo", "act_redo"])
    try:
        win.apply_ribbon_layout()
        _pump()
        rb, g = win.ribbon, win._custom_group
        before = g.widths()["full"]
        gen = rb._generation
        win.act_undo.setText("Undo a change with a much longer name")
        _pump()
        assert rb._generation > gen  # the row is planned again
        assert g.widths()["full"] > before
        gen = rb._generation
        win.act_undo.setEnabled(not win.act_undo.isEnabled())  # (no new name: no new plan)
        _pump()
        assert rb._generation == gen
    finally:
        app_settings().setValue("ui/ribbon_custom", [])
        win.apply_ribbon_layout()
        _pump()


# --------------------------------------------------------------------------- the legend
def test_the_selection_legend_keeps_the_selected_tabs_tint(win):
    """Two contextual tabs: the 'Selection' caption cuts their bar, but over the selected
    tab its box keeps that tab's tinted colour."""
    from ctgedit.ui.style.theme import color, px

    rb = win.ribbon
    rb.set_context_tabs(["Text", "Shape"])
    _pump()
    bar = rb._tabs
    idx = sorted(bar.context_indexes())
    assert len(idx) == 2
    f = QtGui.QFont(bar.font())
    f.setPixelSize(max(7, px(9)))
    fm = QtGui.QFontMetrics(f)
    w = fm.horizontalAdvance(bar.CAPTION) + 8
    try:
        for sel in idx:
            bar.setCurrentIndex(sel)
            _pump()
            img = bar.grab().toImage()
            ratio = img.width() / bar.width()
            body = bar.tabRect(sel).adjusted(1, 3, -1, 0)
            run = bar.tabRect(idx[0]).united(bar.tabRect(idx[-1]))
            box = QtCore.QRect(run.center().x() - w // 2, 0, w, fm.height())
            part = box.intersected(body)
            assert not part.isEmpty()
            # the legend's padding (no glyphs) over the tab, below the bar
            x = box.left() + 2 if sel == idx[0] else box.right() - 2
            y = min(part.bottom(), body.top() + 4)
            assert part.contains(x, y)
            under = img.pixelColor(int(x * ratio), int(y * ratio))
            # (the tab's colour at its far end, away from the legend)
            far = body.left() + 3 if sel == idx[0] else body.right() - 3
            assert not box.contains(far, y)
            tab = img.pixelColor(int(far * ratio), int(y * ratio))
            assert under.name() != color("bg").name()
            assert (
                abs(under.red() - tab.red()) <= 3
                and abs(under.green() - tab.green()) <= 3
                and abs(under.blue() - tab.blue()) <= 3
            ), (bar.tabText(sel), under.name(), tab.name())
    finally:
        rb.set_context_tabs([])
        _pump()


# --------------------------------------------------------------------------- the flyout hooks
def test_a_fold_menu_command_of_a_tab_first_shown_in_the_flyout_closes_it(fx_install, monkeypatch):
    """Collapsed, Utilities the last tab, 900 px: Design is first laid out inside the open
    flyout, so its fold buttons are made after the flyout hooked its buttons."""
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    _app()
    app_settings().setValue("ui/ribbon_tab", 1)
    app_settings().setValue("ui/ribbon_collapsed", True)
    w = MainWindow()
    w.resize(900, 900)
    w.show()
    w._test_fx = fx_install
    try:
        _open(w, monkeypatch)
        rb = w.ribbon
        assert rb.collapsed() and rb._tabs.currentIndex() == 1
        design = rb._groups[rb._tab_map[0]]
        assert not any(g._fold_btn for g in design)  # (never laid out yet)
        rb.open_flyout()
        _pump(10)
        assert rb.flyout_open()
        rb._tabs.setCurrentIndex(0)  # Design, laid out inside the flyout
        _pump(10)
        folds = [g._fold_btn for g in design if g.state == "collapsed"]
        assert folds, rb.layout_states()
        hooked = {id(o) for kind, o in rb._flyout_hooks if kind == "button"}
        assert all(id(b) in hooked for b in folds)
        # a command picked from a fold menu (the button's triggered signal) closes it
        folds[0].triggered.emit(w.act_break_lock)
        _pump(10)
        assert not rb.flyout_open()
    finally:
        w.ribbon.close_flyout()
        w.act_collapse_ribbon.setChecked(False)
        _pump()
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.hide()
        w.deleteLater()
        _pump()


# --------------------------------------------------------------------------- insert and V3/V4
def test_insert_activex_and_the_old_kinds_say_what_they_do_and_need(win):
    from ctgedit.ui.style import tips

    assert win.doc is None
    a = next(a for kind, a in win.insert_kinds if kind == "activex")
    text = tips.rich_tip(a)
    assert "Choose an ActiveX control, then drag on the page to place it." in text
    assert "Open a page first." in text
    tools = {id(t) for t in win.tool_actions.values()}
    legacy = [a for a in win.v34_actions if id(a) not in tools]
    assert legacy
    for a in legacy:
        assert a.statusTip().endswith("."), a.text()
        assert "Open a page first." in tips.rich_tip(a), a.text()
