"""Settings > Ribbon."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402

from ctgedit.ui.prefs.settings import app_settings  # noqa: E402


@pytest.fixture
def win():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()


def _group(win, title, caption):
    return next((g for g in win.ribbon.groups_of(title) if g.caption == caption), None)


def test_group_names_list_every_tab_and_group(win):
    names = win.ribbon.group_names()
    assert ("Design", "Select") in names and ("Design", "Data") in names
    assert ("Utilities", "Libraries") in names and ("Text", "Font") in names
    assert win.ribbon.tab_titles()[:2] == ["Design", "Utilities"]


def test_hidden_groups_and_the_custom_group_follow_the_settings(win):
    st = app_settings()
    st.setValue("ui/ribbon_hidden_groups", ["Design/Data", "Utilities/External"])
    st.setValue("ui/ribbon_custom", ["act_check_page", "act_quick_open", "no_such_command"])
    win.apply_ribbon_layout()
    data = _group(win, "Design", "Data")
    assert data is not None and data.hidden_by_user and data.isHidden()
    ext = _group(win, "Utilities", "External")
    assert ext is not None and ext.hidden_by_user
    assert not _group(win, "Design", "Select").hidden_by_user
    mine = _group(win, "Design", win.CUSTOM_GROUP)
    assert mine is not None and mine is win._custom_group
    assert [a.text().replace("&", "") for a in mine._all_actions] == ["Check Page", "Quick Open…"]
    assert win.ribbon.groups_of("Design")[-1] is mine
    # the hidden group is left out of the reflow
    win.resize(700, 600)
    win.show()
    QtWidgets.QApplication.processEvents()
    win.ribbon._reflow()
    assert not data.collapsed and data.isHidden()
    # back to the defaults: the groups show, the custom group goes
    st.setValue("ui/ribbon_hidden_groups", [])
    st.setValue("ui/ribbon_custom", [])
    win.apply_ribbon_layout()
    assert not data.hidden_by_user and win._custom_group is None
    assert _group(win, "Design", win.CUSTOM_GROUP) is None
    assert all(
        not g.hidden_by_user
        for _t, g in [(t, g) for t in win.ribbon.tab_titles() for g in win.ribbon.groups_of(t)]
    )


def test_settings_page_edits_the_ribbon_choices(win, monkeypatch):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win)
    assert dlg._tabs.indexOf("Ribbon") >= 0
    assert "Design/Data" in dlg._ribbon_boxes and dlg._ribbon_boxes["Design/Data"].isChecked()
    dlg._ribbon_boxes["Design/Data"].setChecked(False)
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Check Page", True)))
    dlg._ribbon_field.add_command()
    monkeypatch.setattr(
        QtWidgets.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Quick Open…", True))
    )
    dlg._ribbon_field.add_command()
    dlg._ribbon_list.setCurrentRow(1)
    dlg._ribbon_field.move(-1)
    c = dlg.ribbon_choices()
    assert c["ui/ribbon_hidden_groups"] == ["Design/Data"]
    # (stored by command id: ui/prefs/keymap; the list shows the names)
    assert c["ui/ribbon_custom"] == ["act_quick_open", "act_check_page"]
    assert [dlg._ribbon_list.item(k).text() for k in range(2)] == ["Quick Open…", "Check Page"]
    assert "Design/Data" not in [x for x in dlg._choices().get("ui/ribbon_custom", [])]
    assert dlg._choices()["ui/ribbon_hidden_groups"] == ["Design/Data"]
    labels = type(win).ribbon_command_labels(win)
    assert "Check Page" in labels and labels == sorted(labels, key=str.lower)
    dlg._ribbon_field.remove_command()
    assert dlg.ribbon_choices()["ui/ribbon_custom"] == ["act_check_page"]
    dlg.restore_defaults()
    assert dlg.ribbon_choices() == {"ui/ribbon_hidden_groups": [], "ui/ribbon_custom": []}
    dlg.reject()
