"""The document tab bar."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the fake install's files the tests copy (set by the ``win`` fixture): 'small', 'second',
#: 'lib' (fxparts.ctm)
FX = {}
#: the genie element of fxparts.ctm the element tabs open
ELEMENT = "fx_agitator_mixer"


def _pump(n=6):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture()
def win(fx_install, monkeypatch):
    _app()
    FX.update(
        small=fx_install.page("FxShapes"),
        second=fx_install.page("FxGenies"),
        lib=fx_install.file("FxLib", "fxparts.ctm"),
    )
    monkeypatch.setenv("CTG_LIB_PATH", fx_install.project("FxMain"))
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    for k in (
        "auto_placeholder_missing",
        "auto_update_genies",
        "auto_update_symbols",
        "auto_update_template",
    ):
        st.setValue("opt/" + k, False)
    w = MainWindow()
    w.resize(1280, 720)
    yield w
    for x in list(MainWindow._instances):
        for e in getattr(x, "_docs", []):
            e["doc"].dirty = False
        if x in MainWindow._instances:
            MainWindow._instances.remove(x)
        x.hide()


def _copies(tmp_path, names, src=None, project="FxMain"):
    d = tmp_path / project
    d.mkdir(exist_ok=True)
    out = []
    for n in names:
        p = d / (n + ".ctg")
        shutil.copyfile(src or FX["small"], p)
        out.append(str(p))
    return out


def _open(win, paths):
    for p in paths:
        assert win.open_page(p), p
    _pump()


def _edit(doc):
    r = doc.page.roots()[-1]
    doc.move_objects(doc.subtree(r), 2, 0)


# ------------------------------------------------------------------- 12 characters
def test_fourteen_tabs_at_1280_px_keep_twelve_characters(win, tmp_path):
    names = [
        "%s_Overview_Page_%02d" % (w, k)
        for k, w in enumerate(
            (
                "FxMix",
                "Tanks",
                "Pumps",
                "Boiler",
                "Alarms",
                "Trends",
                "Conveyor",
                "Filler",
                "Drives",
                "Mining",
                "Sprayer",
                "Valves",
                "Meters",
                "Labels",
            )
        )
    ]
    win.show()
    _open(win, _copies(tmp_path, names))
    bar = win.doc_tabs
    assert bar.count() == 14
    for i in range(bar.count()):
        shown = bar.shown_text(i)
        full = bar.tabText(i)
        assert shown == full or len(shown.rstrip("…")) >= 12, (full, shown)
        assert bar.tabRect(i).width() <= 240 + 40
    # the bar scrolls; the current tab is in view
    assert bar.rect().contains(bar.tabRect(bar.currentIndex()).center())
    # a tab's tool tip names it and its path
    assert names[0] in bar.tabToolTip(0) and os.path.sep in bar.tabToolTip(0)


# ------------------------------------------------------------------- switching
def test_ctrl_tab_goes_to_the_page_used_before_and_ctrl_pgdown_along_the_tabs(win, tmp_path):
    a, b, c = _copies(tmp_path, ["A", "B", "C"])
    _open(win, [a, b, c])
    win.show_tab(0)  # A (C was shown before it)
    win.switch_page(1, via_tab=True)  # Ctrl+Tab: the previously used one
    assert os.path.basename(win.doc.path) == "C.ctg"
    win.switch_page(1, via_tab=True)
    assert os.path.basename(win.doc.path) == "A.ctg"
    win.switch_page(1, via_tab=False)  # Ctrl+PgDown: the next tab
    assert os.path.basename(win.doc.path) == "B.ctg"
    win.switch_page(-1, via_tab=False)
    win.switch_page(-1, via_tab=False)  # wraps round to the last tab
    assert os.path.basename(win.doc.path) == "C.ctg"
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("ui/ctrl_tab_order", "position")
    win.switch_page(1, via_tab=True)  # Tab order: along the tabs
    assert os.path.basename(win.doc.path) == "A.ctg"
    # the keys are the Window menu's
    keys = [s.toString() for s in win.act_next_page.shortcuts()]
    assert keys == ["Ctrl+Tab", "Ctrl+PgDown"]
    assert [s.toString() for s in win.act_prev_page.shortcuts()] == ["Ctrl+Shift+Tab", "Ctrl+PgUp"]


def test_the_action_knows_its_tab_key(win, tmp_path):
    a, b, c = _copies(tmp_path, ["A", "B", "C"])
    _open(win, [a, b, c])
    win.show_tab(0)
    ev = QtGui.QShortcutEvent(QtGui.QKeySequence("Ctrl+Tab"), 0, False)
    QtCore.QCoreApplication.sendEvent(win.act_next_page, ev)
    assert os.path.basename(win.doc.path) == "C.ctg"  # (the MRU order, not the next tab)
    ev = QtGui.QShortcutEvent(QtGui.QKeySequence("Ctrl+PgDown"), 0, False)
    QtCore.QCoreApplication.sendEvent(win.act_next_page, ev)
    assert os.path.basename(win.doc.path) == "A.ctg"  # (C is the last: wraps round)


def test_the_switcher_lists_the_pages_last_used_first(win, tmp_path):
    a, b, c = _copies(tmp_path, ["A", "B", "C"])
    win.show()
    _open(win, [a, b, c])
    win.show_tab(1)  # B, then C, A
    win._show_switcher(win._mru_order(), 1)
    sw = win._switcher
    assert sw.isVisible()
    assert [r[0] for r in sw.rows_text()] == ["B", "C", "A"]
    assert all(r[1] == "FxMain" for r in sw.rows_text())
    sw.step(1)
    # releasing Ctrl shows the chosen page
    QtCore.QCoreApplication.sendEvent(
        sw, QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Control, QtCore.Qt.NoModifier)
    )
    assert not sw.isVisible() and os.path.basename(win.doc.path) == "A.ctg"
    win._show_switcher(win._mru_order(), 1)
    QtCore.QCoreApplication.sendEvent(
        sw, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier)
    )
    assert not sw.isVisible() and os.path.basename(win.doc.path) == "A.ctg"


def test_alt_shift_number_goes_to_that_tab_and_text_fields_keep_the_keys(win, tmp_path, monkeypatch):
    _open(win, _copies(tmp_path, ["A", "B", "C"]))
    assert win.goto_tab_actions[1].shortcut().toString() == "Alt+Shift+2"
    win.goto_tab_actions[1].trigger()
    assert os.path.basename(win.doc.path) == "B.ctg"
    assert not win.goto_tab_actions[5].isEnabled()  # (three tabs)
    win._fill_goto_tab_menu()
    assert win.goto_tab_actions[0].text() == "&1 A"
    edit = QtWidgets.QLineEdit(win)
    monkeypatch.setattr(QtWidgets.QApplication, "focusWidget", staticmethod(lambda: edit))
    win.goto_tab_actions[0].trigger()
    win.switch_page(1, via_tab=False)
    assert os.path.basename(win.doc.path) == "B.ctg"  # (typing in a field: nothing moved)


# ------------------------------------------------------------------- reopen
def test_reopen_closed_page_comes_back_at_its_zoom_and_place(win, tmp_path):
    a, b = _copies(tmp_path, ["A", "B"])
    win.show()
    _open(win, [a, b])
    win.show_tab(0)
    _pump()
    win.view.set_zoom(3.0)
    win.view.centerOn(QtCore.QPointF(100, 80))
    _pump()
    vp = win.view.viewport()
    before = win.view.mapToScene(QtCore.QPoint(vp.width() // 2, vp.height() // 2))
    win.close_current_document()
    assert win.doc_tabs.count() == 1 and win.act_reopen_closed.isEnabled()
    assert win.reopen_closed_tab()
    _pump(10)
    assert os.path.basename(win.doc.path) == "A.ctg"
    assert win.doc_tabs.currentIndex() == 0  # (where its tab was)
    assert abs(win.view.zoom() - 3.0) < 1e-6
    c = win.view.mapToScene(QtCore.QPoint(vp.width() // 2, vp.height() // 2))
    assert abs(c.x() - before.x()) < 2 and abs(c.y() - before.y()) < 2
    assert not win.act_reopen_closed.isEnabled()


# ------------------------------------------------------------------- the tab menu
def test_the_tab_menu_holds_the_menu_bar_commands(win, tmp_path):
    _open(win, _copies(tmp_path, ["A", "B"]))
    menu = win.build_tab_menu(0)
    acts = menu.actions()
    for a in (
        win.act_close_doc,
        win.act_close_others,
        win.act_close_right,
        win.act_close_saved,
        win.act_close_all,
        win.act_pin_tab,
        win.act_reopen_closed,
        win.act_copy_page_path,
        win.act_copy_page_name,
        win.act_reveal_page,
        win.act_show_in_browser,
        win.act_page_props,
        win.act_revert,
        win.act_move_to_window,
    ):
        assert a in acts, a.text()
    # the commands act on the tab the menu was opened on
    win.act_copy_page_name.trigger()
    assert QtWidgets.QApplication.clipboard().text() == "A"
    win.act_copy_page_path.trigger()
    assert os.path.normcase(QtWidgets.QApplication.clipboard().text()) == os.path.normcase(
        os.path.abspath(win._docs[0]["doc"].path)
    )
    shown = []
    monkeypatch_reveal = win._reveal_in_explorer
    try:
        win._reveal_in_explorer = shown.append
        win.act_reveal_page.trigger()
    finally:
        win._reveal_in_explorer = monkeypatch_reveal
    assert shown and shown[0].endswith("A.ctg")
    win._tab_target = None
    # every command is in the File or Window menu too (the palette finds them)
    from ctgedit.ui.dialog.palette import harvest_actions

    in_menus = {a for a, _c, _l in harvest_actions(win.menuBar())}
    for a in (
        win.act_close_others,
        win.act_close_right,
        win.act_close_saved,
        win.act_close_all,
        win.act_reopen_closed,
        win.act_copy_page_path,
        win.act_copy_page_name,
        win.act_reveal_page,
        win.act_show_in_browser,
        win.act_pin_tab,
        win.act_move_to_window,
        win.act_next_page,
        win.act_prev_page,
        win.act_new_from_last,
    ):
        assert a in in_menus, a.text()


def test_close_others_with_five_tabs_asks_once_and_keeps_the_tab(win, tmp_path, monkeypatch):
    _open(win, _copies(tmp_path, ["A", "B", "C", "D", "E"]))
    _edit(win._docs[3]["doc"])  # D is unsaved
    asked = []
    monkeypatch.setattr(
        type(win),
        "_ask_save_on_close",
        lambda self, doc, pending, word="": asked.append(doc) or QtWidgets.QMessageBox.Discard,
    )
    win._tab_target = win._docs[1]  # the menu of B
    assert win.close_other_tabs()
    win._tab_target = None
    assert len(asked) == 1 and os.path.basename(asked[0].path) == "D.ctg"
    assert [os.path.basename(e["doc"].path) for e in win._docs] == ["B.ctg"]
    assert os.path.basename(win.doc.path) == "B.ctg"


def test_close_to_the_right_saved_and_all(win, tmp_path, monkeypatch):
    _open(win, _copies(tmp_path, ["A", "B", "C", "D"]))
    win._tab_target = win._docs[1]
    assert win.close_tabs_to_the_right()
    win._tab_target = None
    assert [os.path.basename(e["doc"].path) for e in win._docs] == ["A.ctg", "B.ctg"]
    _edit(win._docs[0]["doc"])
    assert win.close_saved_tabs()
    assert [os.path.basename(e["doc"].path) for e in win._docs] == ["A.ctg"]
    monkeypatch.setattr(
        type(win), "_ask_save_on_close", lambda self, doc, pending, word="": QtWidgets.QMessageBox.Cancel
    )
    assert not win.close_all_tabs()  # cancelled: it stays
    assert len(win._docs) == 1
    assert win.act_close_all.shortcut().toString() == "Ctrl+Shift+W"


def test_one_unsaved_page_cancelled_closes_nothing(win, tmp_path, monkeypatch):
    _open(win, _copies(tmp_path, ["A", "B", "C"]))
    _edit(win._docs[0]["doc"])
    monkeypatch.setattr(
        type(win), "_ask_save_on_close", lambda self, doc, pending, word="": QtWidgets.QMessageBox.Cancel
    )
    assert not win.close_all_tabs()
    assert len(win._docs) == 3


def test_a_tab_dragged_back_onto_its_own_bar_moves_there(win, tmp_path):
    from ctgedit.ui.main_window import MainWindow

    _open(win, _copies(tmp_path, ["A", "B", "C"]))
    before = set(MainWindow._instances)
    win._tab_dropped(win.doc_tabs, 0, 2)  # (its own bar: no new window)
    assert set(MainWindow._instances) == before
    assert [os.path.basename(e["doc"].path) for e in win._docs] == ["B.ctg", "C.ctg", "A.ctg"]


def test_a_click_outside_the_switcher_closes_it(win, tmp_path):
    win.show()
    _open(win, _copies(tmp_path, ["A", "B"]))
    win._show_switcher(win._mru_order(), 1)
    sw = win._switcher
    gp = QtCore.QPointF(win.mapToGlobal(QtCore.QPoint(5, 5)))
    ev = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress,
        QtCore.QPointF(-500, -500),
        gp,
        QtCore.Qt.LeftButton,
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
    )
    QtCore.QCoreApplication.sendEvent(sw, ev)
    assert not sw.isVisible() and os.path.basename(win.doc.path) == "B.ctg"


def test_middle_click_closes_a_tab(win, tmp_path):
    win.show()
    _open(win, _copies(tmp_path, ["A", "B"]))
    bar = win.doc_tabs
    pos = bar.tabRect(0).center()
    from PySide6.QtTest import QTest

    QTest.mouseClick(bar, QtCore.Qt.MiddleButton, QtCore.Qt.NoModifier, pos)
    _pump()
    assert [os.path.basename(e["doc"].path) for e in win._docs] == ["B.ctg"]


# ------------------------------------------------------------------- pins
def test_pinned_tabs_stay_on_close_all_and_come_back_pinned(win, tmp_path, monkeypatch):
    a, b, c = _copies(tmp_path, ["FxMix_Overview", "B", "C"])
    _open(win, [a, b, c])
    win._tab_target = win._docs[2]  # pin C: it moves to the left
    win.toggle_pin_tab()
    win._tab_target = None
    assert [os.path.basename(e["doc"].path) for e in win._docs][0] == "C.ctg"
    assert win.doc_tabs.tab_state(0)["pinned"]
    assert win.doc_tabs.close_button(0).mode() == "pin"
    win.show_tab(1)
    win._tab_target = win._docs[1]  # and FxMix_Overview: compact
    win.toggle_pin_tab()
    win._tab_target = None
    assert win.doc_tabs.tabText(1) == "FxMix_"  # (6 characters)
    assert win.doc_tabs.full_name(1) == "FxMix_Overview"
    assert win.close_all_tabs()
    assert [os.path.basename(e["doc"].path) for e in win._docs] == ["C.ctg", "FxMix_Overview.ctg"]
    # Ctrl+W on a pinned tab asks first
    asked = []
    monkeypatch.setattr(type(win), "_ask_unpin", lambda self, name: asked.append(name) or False)
    win.show_tab(0)
    win.close_current_document()
    assert asked == ["C"] and len(win._docs) == 2
    # a drag across the pinned edge goes back
    win._tab_target = win._docs[0]
    win.toggle_pin_tab()  # C unpinned: after the pinned one
    win._tab_target = None
    assert [os.path.basename(e["doc"].path) for e in win._docs] == ["FxMix_Overview.ctg", "C.ctg"]
    win.doc_tabs.moveTab(1, 0)
    win._enforce_pin_order()
    assert [os.path.basename(e["doc"].path) for e in win._docs] == ["FxMix_Overview.ctg", "C.ctg"]
    # restart: the session brings the pin back
    win.save_prefs()
    from ctgedit.ui.main_window import MainWindow

    MainWindow._session_seen.clear()
    w2 = MainWindow()
    assert w2.restore_session(reopen=True)
    assert [os.path.basename(e["doc"].path) for e in w2._docs] == ["FxMix_Overview.ctg", "C.ctg"]
    assert w2._docs[0].get("pinned") and not w2._docs[1].get("pinned")
    assert w2.doc_tabs.tab_state(0)["pinned"]


def test_clicking_the_pin_unpins(win, tmp_path):
    _open(win, _copies(tmp_path, ["A", "B"]))
    win._tab_target = win._docs[1]
    win.toggle_pin_tab()
    win._tab_target = None
    b = win.doc_tabs.close_button(0)
    assert b.mode() == "pin"
    b.click()
    assert not win._docs[0].get("pinned")


# ------------------------------------------------------------------- the look
def test_close_buttons_follow_the_setting_and_unsaved_tabs_show_a_dot(win, tmp_path):
    from ctgedit.ui.prefs.settings import app_settings

    _open(win, _copies(tmp_path, ["A", "B", "C"]))
    bar = win.doc_tabs
    win.show_tab(0)
    modes = lambda: [bar.close_button(i).mode() for i in range(bar.count())]  # noqa: E731
    assert modes() == ["cross", "none", "none"]  # the current tab only
    bar._hover = 2
    bar._sync_buttons()
    assert modes() == ["cross", "none", "cross"]  # and the one under the pointer
    bar._hover = -1
    _edit(win._docs[1]["doc"])
    win._update_title()
    assert modes()[1] == "dot"  # unsaved: a dot, the cross on hover
    assert bar.tabText(1) == "B" and "unsaved" in bar.accessibleTabName(1)
    app_settings().setValue("ui/tab_close_button", "always")
    bar._sync_buttons()
    assert modes() == ["cross", "dot", "cross"]
    app_settings().setValue("ui/tab_close_button", "never")
    bar._sync_buttons()
    assert modes() == ["none", "mark", "none"]


def test_kind_icons_and_the_project_of_tabs_that_share_a_name(win, tmp_path):
    (a,) = _copies(tmp_path, ["FxBlend"], src=FX["second"], project="FxMain")
    (b,) = _copies(tmp_path, ["FxBlend"], src=FX["second"], project="FxOther")
    (c,) = _copies(tmp_path, ["Alone"], project="FxMain")
    _open(win, [a, b, c])
    bar = win.doc_tabs
    st = [bar.tab_state(i) for i in range(3)]
    assert [s["show_project"] for s in st] == [True, True, False]
    assert [s["project"] for s in st[:2]] == ["FxMain", "FxOther"]
    tail = bar.tabButton(0, QtWidgets.QTabBar.RightSide)
    assert tail.project.text() == "· FxMain"
    assert all(s["kind"] == "page" for s in st) and not bar.tabIcon(0).isNull()
    # a library element: its name, its kind icon, the full name in the tool tip
    lib = tmp_path / "FxMain" / "fxparts.ctm"
    shutil.copyfile(FX["lib"], lib)
    win._open_element(str(lib), ELEMENT)
    _pump()
    k = win.doc_tabs.currentIndex()
    assert bar.tabText(k) == ELEMENT
    assert bar.tab_state(k)["kind"] == "genie"
    assert "fxparts." + ELEMENT in bar.tabToolTip(k)


def test_a_read_only_page_has_a_lock_icon(win, tmp_path):
    (p,) = _copies(tmp_path, ["Locked"])
    os.chmod(p, 0o444)
    try:
        _open(win, [p])
        st = win.doc_tabs.tab_state(0)
        assert st["read_only"] and win.doc_tabs.tabText(0) == "Locked"
    finally:
        os.chmod(p, 0o666)


# ------------------------------------------------------------------- list and '+'
def test_the_open_pages_list_filters_and_shows(win, tmp_path):
    names = ["FxBlend"] + ["Page%02d" % k for k in range(14)]
    _open(win, _copies(tmp_path, names))
    menu = win.tab_list_menu
    menu.fill()
    assert len(menu.visible_rows()) == 15
    menu.filter("blend")
    rows = menu.visible_rows()
    assert len(rows) == 1 and rows[0].text().startswith("FxBlend")
    assert rows[0].text().endswith("\tFxMain")
    menu._enter()
    assert os.path.basename(win.doc.path) == "FxBlend.ctg"
    menu.fill()
    assert [a.isChecked() for a in menu.visible_rows()][0]  # (the current one ticked)
    # the tick shows: a drawn tick in place of the kind icon, and the name in bold (a
    # checked row with an icon shows no check mark in this style)
    from ctgedit.ui.panels.doc_tabs import kind_icon, tick_icon

    cur, other = menu.visible_rows()[0], menu.visible_rows()[1]
    assert cur.font().bold() and not other.font().bold()
    img = cur.icon().pixmap(16, 16).toImage()
    assert img == tick_icon().pixmap(16, 16).toImage()
    assert img != kind_icon("page").pixmap(16, 16).toImage()


def test_the_plus_button_and_a_new_page_from_the_last_template(win, tmp_path, monkeypatch):
    from ctgedit.ui.prefs.settings import app_settings

    (a,) = _copies(tmp_path, ["NewPage"])
    _open(win, [a])
    assert win.tab_new_button.menu().actions()[0] is win.act_new_from_last
    assert not win.act_new_from_last.isEnabled()  # (nothing remembered yet)
    app_settings().setValue("pagebrowser/new/template", "fx_style|normal@XGA|1024x768|0")
    win._update_actions()
    assert win.act_new_from_last.isEnabled()
    made = []
    monkeypatch.setattr(
        type(win), "_create_page", lambda self, ref, linked, path: made.append((ref, linked, path))
    )
    win.new_page_from_last_template()
    assert made and made[0][0] == "fx_style.normal@XGA.<ctt" and made[0][1] is True
    assert os.path.basename(made[0][2]) == "NewPage_2.ctg"  # (NewPage is taken)
    assert os.path.dirname(made[0][2]) == os.path.dirname(a)
    opened = []
    monkeypatch.setattr(type(win), "new_blank_dialog", lambda self, kind="": opened.append(1))
    win.act_new.triggered.disconnect()
    win.act_new.triggered.connect(lambda: win.new_blank_dialog())
    win.tab_new_button.click()
    assert opened == [1]


# ------------------------------------------------------------------- other windows
def test_move_to_a_new_window_keeps_the_document_and_its_undo(win, tmp_path):
    a, b = _copies(tmp_path, ["A", "B"])
    _open(win, [a, b])
    doc = win.doc
    _edit(doc)
    steps = list(doc._undo)
    win2 = win.move_tab_to_new_window()
    _pump()
    assert win2 is not None and win2.doc is doc
    assert doc._undo == steps and doc.can_undo()
    assert all(e["doc"] is not doc for e in win._docs) and len(win._docs) == 1
    assert win2.doc_tabs.tab_state(0)["dirty"]
    win2.undo()  # its history goes on there
    assert doc.can_redo() and len(doc._undo) == len(steps) - 1
    # and back by a drop of its tab on the first window's tab bar
    win._tab_dropped(win2.doc_tabs, 0, 0)
    assert win._docs[0]["doc"] is doc and not win2._docs


def test_dragging_a_tab_off_the_bar_starts_a_tab_drag(win, tmp_path, monkeypatch):
    """A press on a tab and a move more than 40 px below the bar: the bar ends its own move
    and drags the tab; dropped where no window takes it, the tab opens in a new window."""
    from ctgedit.ui.panels.doc_tabs import DocTabBar
    from ctgedit.ui.main_window import MainWindow

    win.show()
    _open(win, _copies(tmp_path, ["A", "B"]))
    bar = win.doc_tabs
    seen = []

    def fake_exec(drag):
        seen.append(bytes(drag.mimeData().data("application/x-ctgedit-tab")))
        return QtCore.Qt.IgnoreAction

    monkeypatch.setattr(DocTabBar, "exec_drag", staticmethod(fake_exec))
    uid0 = bar.tab_uid(0)  # (the drag names the tab by its number)
    p = QtCore.QPointF(bar.tabRect(0).center())
    gp = QtCore.QPointF(bar.mapToGlobal(p.toPoint()))
    press = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress,
        p,
        gp,
        QtCore.Qt.LeftButton,
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
    )
    QtCore.QCoreApplication.sendEvent(bar, press)
    q = QtCore.QPointF(p.x(), bar.height() + 60)
    move = QtGui.QMouseEvent(
        QtCore.QEvent.MouseMove,
        q,
        QtCore.QPointF(bar.mapToGlobal(q.toPoint())),
        QtCore.Qt.NoButton,
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
    )
    before = set(MainWindow._instances)
    QtCore.QCoreApplication.sendEvent(bar, move)
    _pump()
    assert seen == [str(uid0).encode("ascii")]
    new = [w for w in MainWindow._instances if w not in before]
    assert len(new) == 1 and os.path.basename(new[0].doc.path) == "A.ctg"
    assert [os.path.basename(e["doc"].path) for e in win._docs] == ["B.ctg"]


def test_a_page_dropped_on_the_start_page_opens(win, tmp_path):
    win.show()
    _pump()
    win._update_start_page()
    sp = win.start_page
    assert not sp.isHidden()
    (p,) = _copies(tmp_path, ["Dropped"])
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(p)])
    pos = QtCore.QPoint(50, 50)
    enter = QtGui.QDragEnterEvent(pos, QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    QtCore.QCoreApplication.sendEvent(sp, enter)
    assert enter.isAccepted()
    drop = QtGui.QDropEvent(
        QtCore.QPointF(pos), QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier
    )
    QtCore.QCoreApplication.sendEvent(sp, drop)
    _pump()
    assert win.doc is not None and os.path.basename(win.doc.path) == "Dropped.ctg"


def test_a_tab_dragged_out_opens_in_a_new_window(win, tmp_path):
    from ctgedit.ui.main_window import MainWindow

    win.show()
    a, b = _copies(tmp_path, ["A", "B"])
    _open(win, [a, b])
    doc = win._docs[0]["doc"]
    before = set(MainWindow._instances)
    win._tab_dragged_out(0, QtCore.QPoint(600, 400))
    _pump()
    new = [w for w in MainWindow._instances if w not in before]
    assert len(new) == 1 and new[0].doc is doc
    assert [os.path.basename(e["doc"].path) for e in win._docs] == ["B.ctg"]


def _send(bar, typ, pos, buttons, button=QtCore.Qt.NoButton):
    ev = QtGui.QMouseEvent(
        typ, QtCore.QPointF(pos), QtCore.QPointF(bar.mapToGlobal(pos)), button, buttons, QtCore.Qt.NoModifier
    )
    QtCore.QCoreApplication.sendEvent(bar, ev)
    _pump(3)


def test_a_tab_dragged_along_the_bar_and_then_out_moves_that_page(win, tmp_path, monkeypatch):
    """Qt moves a dragged tab along the bar as it goes: the tab that leaves the bar is the
    pressed one, not the one now at its old index."""
    from ctgedit.ui.panels.doc_tabs import DocTabBar
    from ctgedit.ui.main_window import MainWindow

    win.show()
    _open(win, _copies(tmp_path, ["AAAA", "BBBB", "CCCC"]))
    bar = win.doc_tabs
    monkeypatch.setattr(DocTabBar, "exec_drag", staticmethod(lambda drag: QtCore.Qt.IgnoreAction))
    moves = []
    bar.tabMoved.connect(lambda a, b: moves.append((a, b)))
    r0, r1 = bar.tabRect(0), bar.tabRect(1)
    p = r0.center()
    _send(bar, QtCore.QEvent.MouseButtonPress, p, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton)
    for x in range(p.x(), r1.center().x() + r1.width() // 2, 8):
        _send(bar, QtCore.QEvent.MouseMove, QtCore.QPoint(x, p.y()), QtCore.Qt.LeftButton)
    assert moves and [bar.tabText(i) for i in range(3)] == ["BBBB", "AAAA", "CCCC"]
    before = set(MainWindow._instances)
    x = r1.center().x() + r1.width() // 2
    for y in range(p.y(), p.y() + 90, 10):
        _send(bar, QtCore.QEvent.MouseMove, QtCore.QPoint(x, y), QtCore.Qt.LeftButton)
    new = [w for w in MainWindow._instances if w not in before]
    assert len(new) == 1
    assert [os.path.basename(e["doc"].path) for e in new[0]._docs] == ["AAAA.ctg"]
    assert [os.path.basename(e["doc"].path) for e in win._docs] == ["BBBB.ctg", "CCCC.ctg"]


def test_a_dragged_tab_is_named_by_its_number_not_its_index(win, tmp_path):
    from ctgedit.ui.panels.doc_tabs import TAB_MIME, dragged_tab_index

    _open(win, _copies(tmp_path, ["A", "B", "C"]))
    bar = win.doc_tabs
    uid = bar.tab_uid(0)
    mime = QtCore.QMimeData()
    mime.setData(TAB_MIME, QtCore.QByteArray(str(uid).encode("ascii")))
    bar.moveTab(0, 2)  # (moved while it is dragged)
    _pump()
    assert dragged_tab_index(mime, bar) == 2
    assert os.path.basename(win._docs[2]["doc"].path) == "A.ctg"
    mime.setData(TAB_MIME, QtCore.QByteArray(b"x"))
    assert dragged_tab_index(mime, bar) == -1


def _last_template(project_dir):
    from ctgedit.ui.pick_fields import TemplateCatalog
    from ctgedit.ui.prefs.settings import app_settings

    cat = TemplateCatalog.for_project(project_dir)
    style = cat.styles()[0]
    el = cat.elements(style)[0][1].name
    app_settings().setValue("pagebrowser/new/template", "|".join((style, el, "", "1")))
    return "%s.%s.<ctt" % (style, el)


def test_new_page_from_the_last_template_twice_gives_two_names(win, tmp_path, monkeypatch):
    """A new page is on disk only after its first Save: the second new page takes the next
    name, File > New refuses the name of an unsaved tab, and the first save of a new page
    whose file was saved since asks before it replaces it."""
    (a,) = _copies(tmp_path, ["FxBlend"], src=FX["second"])
    _open(win, [a])
    ref = _last_template(os.path.dirname(a))
    assert win.new_page_from_last_template() and win.new_page_from_last_template()
    _pump()
    d1, d2 = win._docs[1]["doc"], win._docs[2]["doc"]
    assert [os.path.basename(d.path) for d in (d1, d2)] == ["NewPage.ctg", "NewPage_2.ctg"]
    assert not os.path.exists(d1.path) and not os.path.exists(d2.path)
    assert win.open_page_names(os.path.dirname(a)) == ["FxBlend", "NewPage", "NewPage_2"]
    warned = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    win._create_page(ref, True, d1.path)
    assert len(win._docs) == 3 and "already open in a tab" in warned[0]
    # another program saved a NewPage meanwhile: Save asks (Cancel: the default)
    shutil.copyfile(FX["small"], d1.path)
    with open(d1.path, "rb") as fh:
        before = fh.read()
    win.show_tab(1)
    asked = []

    def cancel(box):
        asked.append(box.text())
        return 0

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", cancel)
    assert not win.save()
    with open(d1.path, "rb") as fh:
        assert fh.read() == before
    assert asked and "already exists" in asked[0] and d1.dirty
    # Replace: it is saved over that file
    monkeypatch.setattr(type(win), "_confirm_replace_new", lambda self, errors=None: "replace")
    assert win.save() and not d1.dirty
    with open(d1.path, "rb") as fh:
        assert fh.read() != before
    # (once saved here, the next save is an ordinary one)
    monkeypatch.setattr(
        type(win), "_confirm_replace_new", lambda self, errors=None: pytest.fail("asked again")
    )
    _edit(d1)
    assert win.save()


def _restore(win, paths, current, monkeypatch):
    import json
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    st.setValue("session/tabs", json.dumps([{"path": p, "element": "", "pinned": False} for p in paths]))
    st.setValue("session/open_files", paths)
    st.setValue("session/current", current)
    MainWindow._session_seen.clear()
    assert win.restore_session(reopen=True)
    _pump()
    opened = []
    orig = win._materialise

    def counting(entry, failures=None):
        opened.append(os.path.basename(entry["doc"].path))
        return orig(entry, failures)

    monkeypatch.setattr(win, "_materialise", counting)
    return opened


def test_close_all_opens_no_restored_tab_on_the_way(win, tmp_path, monkeypatch):
    from ctgedit.ui.main_window_base import _PendingPage

    paths = _copies(tmp_path, ["P0", "P1", "P2", "P3", "P4", "P5"])
    opened = _restore(win, paths, 5, monkeypatch)
    assert sum(isinstance(e["doc"], _PendingPage) for e in win._docs) == 5
    assert win.close_all_tabs()
    assert opened == [] and win._docs == [] and win.doc is None


def test_close_others_opens_only_the_tab_it_keeps(win, tmp_path, monkeypatch):
    paths = _copies(tmp_path, ["Q0", "Q1", "Q2", "Q3"])
    opened = _restore(win, paths, 3, monkeypatch)
    win._tab_target = win._docs[0]
    try:
        assert win.close_other_tabs()
    finally:
        win._tab_target = None
    assert opened == ["Q0.ctg"]
    assert len(win._docs) == 1 and os.path.basename(win.doc.path) == "Q0.ctg"


def test_close_to_the_right_of_a_restored_tab_shows_it_once(win, tmp_path, monkeypatch):
    paths = _copies(tmp_path, ["R0", "R1", "R2", "R3"])
    opened = _restore(win, paths, 3, monkeypatch)
    win._tab_target = win._docs[1]
    try:
        assert win.close_tabs_to_the_right()
    finally:
        win._tab_target = None
    assert opened == ["R1.ctg"]  # (its neighbour, once)
    assert [os.path.basename(e["doc"].path) for e in win._docs] == ["R0.ctg", "R1.ctg"]
    assert os.path.basename(win.doc.path) == "R1.ctg"


def test_a_pinned_tab_whose_close_is_cancelled_stays_pinned(win, tmp_path, monkeypatch):
    a, b = _copies(tmp_path, ["A", "B"])
    _open(win, [a, b])
    win._tab_target = win._docs[1]
    win.toggle_pin_tab()  # B pinned: first
    win._tab_target = None
    win.show_tab(0)
    _edit(win.doc)
    monkeypatch.setattr(win, "_ask_unpin", lambda name: True)
    monkeypatch.setattr(win, "_ask_save_on_close", lambda doc, pending, word: QtWidgets.QMessageBox.Cancel)
    win.close_current_document()
    assert [(os.path.basename(e["doc"].path), bool(e.get("pinned"))) for e in win._docs] == [
        ("B.ctg", True),
        ("A.ctg", False),
    ]
    assert win.doc_tabs.tab_state(0)["pinned"]


def test_the_bitmap_editor_saves_into_the_window_its_page_moved_to(win, tmp_path):
    (pid,) = _copies(tmp_path, ["PID"])  # (FxShapes: FxBitmap, FxFlashBitmap)
    (b,) = _copies(tmp_path, ["B"])
    _open(win, [b, pid])
    root = None
    for r in win.doc.page.roots():
        win._sel = [r]
        if win._bitmap_target(quiet=True)[0] is not None:
            root = r
            break
    assert root is not None, "FxShapes has no bitmap"
    win._bitmap_editor()
    _pump()
    dlg = next(d for d in dict.values(win._dialogs) if type(d).__name__ == "BitmapEditorDialog")
    doc = win.doc
    steps = len(doc._undo)
    win2 = win.move_tab_to_new_window()
    _pump()
    assert win2.doc is doc and dlg._ctg_host is win2
    rgba, w, h = dlg.result_rgba()
    changed = bytearray(rgba)
    blue = bytes((0, 0, 255, 255))
    changed[0:4] = blue if bytes(changed[0:4]) != blue else bytes((255, 0, 0, 255))
    dlg.result_rgba = lambda: (bytes(changed), w, h)
    dlg.modified = lambda: True
    problems = []
    dlg.show_problem = problems.append
    dlg.saveRequested.emit()
    _pump()
    assert problems == [] and len(doc._undo) == steps + 1 and doc.dirty
    assert win2.doc is doc


def _element_session(tmp_path, current=0):
    import json
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    (a,) = _copies(tmp_path, ["A"])
    lib = os.path.join(os.path.dirname(a), "fxparts.ctm")
    shutil.copyfile(FX["lib"], lib)
    st = app_settings()
    st.setValue(
        "session/tabs",
        json.dumps(
            [{"path": a, "element": "", "pinned": False}, {"path": lib, "element": ELEMENT, "pinned": False}]
        ),
    )
    st.setValue("session/open_files", [a, lib])
    st.setValue("session/current", current)
    MainWindow._session_seen.clear()
    return a, lib


def test_a_restored_element_tab_is_known_before_it_is_shown(win, tmp_path):
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.main_window_base import _PendingPage

    _a, lib = _element_session(tmp_path)
    assert win.restore_session(reopen=True)
    _pump()
    assert isinstance(win._docs[1]["doc"], _PendingPage)
    assert win._tabs_of("element", lib, ELEMENT) == [1]
    assert win._tabs_of("page", lib) == []
    win._open_element(lib, ELEMENT.upper())  # (the Library panel, Edit Definition)
    _pump()
    assert len(win._docs) == 2 and win.doc_tabs.currentIndex() == 1
    assert getattr(win.doc, "element_name", "") == ELEMENT
    # and from another window: that window's tab comes to the front, no second tab
    win2 = MainWindow()
    win2._open_element(lib, ELEMENT)
    assert win2._docs == [] and len(win._docs) == 2


def test_a_restored_element_tab_is_framed_like_an_opened_one(win, tmp_path):
    from ctgedit.ui.prefs.settings import app_settings

    _element_session(tmp_path)
    app_settings().setValue("opt/open_zoom", "fit")
    win.show()
    assert win.restore_session(reopen=True)
    _pump()
    win.show_tab(1)
    _pump(20)
    restored = win.view.zoom()
    win._frame_content()  # (what an open from the library does)
    _pump()
    assert abs(win.view.zoom() - restored) < 0.01 * restored
    win.view.fit()
    assert win.view.zoom() < restored * 0.8  # (framed on its objects, not fitted)


def test_a_bitmap_editor_save_of_painted_pixels_keeps_a_later_resize(win, tmp_path):
    """Paint in the Bitmap Editor, scale the bitmap on the page to 200 %, then Save: the
    pixels go in and the page keeps its newer size."""
    from ctgedit.format import geometry as G

    (pid,) = _copies(tmp_path, ["PID"])
    _open(win, [pid])
    root = None
    for r in win.doc.page.roots():
        win._sel = [r]
        if win._bitmap_target(quiet=True)[0] is not None:
            root = r
            break
    assert root is not None
    win._bitmap_editor()
    _pump()
    dlg = next(d for d in dict.values(win._dialogs) if type(d).__name__ == "BitmapEditorDialog")
    doc = win.doc
    i = win._bitmap_target(quiet=True)[0]
    l, t, r, b = G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1
    w, h = r - l, b - t
    doc.set_object_rect(i, (l, t, l + 2 * w, t + 2 * h))
    rgba, pw, ph = dlg.result_rgba()
    changed = bytearray(rgba)
    changed[0:4] = (
        bytes((0, 0, 255, 255)) if bytes(changed[0:4]) != bytes((0, 0, 255, 255)) else bytes((255, 0, 0, 255))
    )
    dlg.result_rgba = lambda: (bytes(changed), pw, ph)
    dlg.modified = lambda: True
    problems = []
    dlg.show_problem = problems.append
    dlg.saveRequested.emit()
    _pump()
    assert problems == []
    l2, t2, r2, b2 = G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1
    assert (l2, t2, r2 - l2, b2 - t2) == (l, t, 2 * w, 2 * h)


def test_save_as_onto_a_page_open_in_another_tab_is_refused(win, tmp_path, monkeypatch):
    """Save As over the file of another open tab: refused (two tabs on one file would each
    overwrite the other's edits), nothing written."""
    from ctgedit.ui.browser import PageBrowserDialog

    a, b = _copies(tmp_path, ["A", "B"])
    _open(win, [a, b])
    before = open(a, "rb").read()
    warned = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *x, **k: warned.append(x[2])))
    monkeypatch.setattr(PageBrowserDialog, "get_save_path", staticmethod(lambda *x, **k: a))
    assert os.path.normcase(win.doc.path) == os.path.normcase(b)
    assert win.save_as() is False
    assert warned and "open in another tab" in warned[0]
    assert open(a, "rb").read() == before and os.path.normcase(win.doc.path) == os.path.normcase(b)
