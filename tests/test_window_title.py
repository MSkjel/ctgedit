"""The window title and the Window menu."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from qtkit import app as _app


@pytest.fixture()
def win(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    for k in (
        "auto_placeholder_missing",
        "auto_update_genies",
        "auto_update_symbols",
        "auto_update_template",
    ):
        app_settings().setValue("opt/" + k, False)
    w = MainWindow()
    yield w
    for x in list(MainWindow.instances):
        for e in getattr(x, "_docs", []):
            e["doc"].dirty = False
        if x in MainWindow.instances:
            MainWindow.instances.remove(x)


def test_no_page_is_just_the_program():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        assert w.windowTitle() == "ctgedit"
    finally:
        MainWindow.instances.remove(w)


def test_a_page_of_its_project(win, fx_install):
    assert win.open_page(fx_install.page("FxShapes"))
    assert win.windowTitle() == "FxShapes - FxMain - ctgedit"
    d = win.doc
    d.move_objects(d.subtree(d.page.roots()[-1]), 1, 0)
    win._update_title()
    assert win.windowTitle() == "FxShapes \u2022 - FxMain - ctgedit"


def test_a_page_of_another_install_says_so(win, fx_install, monkeypatch):
    from ctgedit.io import citect_config

    monkeypatch.setattr(citect_config, "live_user_dir", lambda: r"C:\Some\Other\User")
    assert win.open_page(fx_install.page("FxShapes"))
    assert win.windowTitle() == "FxShapes - FxMain (other install) - ctgedit"


def test_a_library_element_names_its_library(win, fx_install):
    win._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_agitator_mixer")
    assert win.windowTitle() == "fxparts.fx_agitator_mixer - FxLib - ctgedit"


def test_the_window_menu_lists_a_page_once(win, fx_install):
    assert win.open_page(fx_install.page("FxShapes"))
    win._fill_window_list()
    texts = [a.text() for a in win._window_menu.actions()]
    rows = [t for t in texts if t.startswith("FxShapes")]
    assert rows == ["FxShapes\tFxMain"]  # once, with its project
    assert "Pages" in texts and "Windows" not in texts
    # Next / Previous Page at the top
    assert texts[:2] == ["Next Page", "Previous Page"]
    # two editor windows: a Windows section lists them
    from ctgedit.ui.main_window import MainWindow

    win.show()
    other = MainWindow()
    other.show()
    try:
        win._fill_window_list()
        texts = [a.text() for a in win._window_menu.actions()]
        assert "Windows" in texts
        assert any(t.startswith("&1 ") for t in texts) and any(t.startswith("&2 ") for t in texts)
    finally:
        other.hide()
        MainWindow.instances.remove(other)
