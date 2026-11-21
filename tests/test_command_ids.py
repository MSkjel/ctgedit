"""Stable command ids."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the menus that are rebuilt as they open (their rows get 'menu:' ids)
DYNAMIC = ("File > Recent Pages", "File > Import Recent", "Edit > Styles", "View > Labels")


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.deleteLater()


def test_every_menu_command_has_a_unique_id(win):
    from ctgedit.ui.dialog.palette import harvest_actions

    owner = {}
    missing = []
    for a, crumb, label in harvest_actions(win.menuBar()):
        if crumb.startswith(DYNAMIC):
            continue
        name = a.objectName()
        if not name:
            missing.append("%s > %s" % (crumb, label))
            continue
        assert owner.setdefault(name, a) is a, "two commands named %r" % name
    assert not missing, missing
    # the ids the spec names
    assert win.act_undo.objectName() == "act_undo"
    assert win.tool_actions["line"].objectName() == "tool.line"
    assert [a.objectName() for a in win.align_actions][:3] == ["align.left", "align.hcentre", "align.right"]
    assert win.justify_actions[0].objectName() == "justify.left"
    assert win.text_flag_actions[0].objectName() == "textflag.bold"
    assert win.effect_actions[2].objectName() == "effect.raised"
    assert win.dock_props.toggleViewAction().objectName() == "panel.props"
    # the inline menu commands have attributes (and so ids) of their own
    for name in (
        "act_exit",
        "act_new_window",
        "act_help",
        "act_shortcuts_window",
        "act_about",
        "act_cicode_ref",
    ):
        assert getattr(win, name).objectName() == name
    # an alias keeps the first name
    assert win.act_rotate is win.act_rot_cw and win.act_rot_cw.objectName() == "act_rot_cw"


def test_a_renamed_command_keeps_its_id(win):
    from ctgedit.ui.prefs import keymap

    win.act_undo.setText("Undo Move")
    assert keymap.command_id(win.act_undo) == "act_undo"
    assert keymap.label_of(win.act_undo) == "Undo"
    assert keymap.find_action(win, "act_undo") is win.act_undo
    # a row of a menu rebuilt as it opens
    a = QtGui.QAction("FxShapes", win)
    assert keymap.command_id(a, "File > Recent Pages") == "menu:File > Recent Pages/FxShapes"


def test_the_palette_history_stores_ids(win):
    from ctgedit.ui.prefs import keymap

    win.palette_popup.open_at()
    win.palette_popup._edit.setText("zoom in")
    win.palette_popup._run_current()
    QtWidgets.QApplication.processEvents()
    assert keymap.palette_mru(win)[0] == "act_zoom_in"
    win.palette_popup.hide()


def test_my_commands_are_stored_by_id(win):
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    # 'Export Picture…' sits in File and in Tools > Pictures: one command, one id
    st.setValue("ui/ribbon_custom", ["act_export_picture", "act_undo", "act_check_page"])
    win.apply_ribbon_layout()
    g = win._custom_group
    assert g is not None
    hosted = {b.defaultAction() for b in g.findChildren(QtWidgets.QToolButton)}
    assert {win.act_export_picture, win.act_undo, win.act_check_page} <= hosted
    # an Undo with a named step stays in the group
    win.act_undo.setText("Undo Colour")
    win.apply_ribbon_layout()
    hosted = {b.defaultAction() for b in win._custom_group.findChildren(QtWidgets.QToolButton)}
    assert win.act_undo in hosted


def test_settings_is_found_by_its_other_names(win):
    win.palette_popup.open_at()
    for word in ("configure", "preferences", "options"):
        win.palette_popup._edit.setText(word)
        texts = [win.palette_popup._list.item(i).text() for i in range(win.palette_popup._list.count())]
        assert texts and texts[0].startswith("Settings"), (word, texts[:3])
    win.palette_popup.hide()
