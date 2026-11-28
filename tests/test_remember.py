"""Choices a dialog keeps between openings (ui/remember.py)."""

import os

from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_each_kind_of_control_comes_back_as_it_was_left():
    _app()
    from PySide6 import QtWidgets

    from ctgedit.ui import remember as R

    def controls():
        combo = QtWidgets.QComboBox()
        combo.addItem("All", None)
        combo.addItem("FxMain (12)", "FxMain")
        combo.addItem("FxLib (3)", "FxLib")
        check, line, spin = QtWidgets.QCheckBox("x"), QtWidgets.QLineEdit(), QtWidgets.QSpinBox()
        radios = [QtWidgets.QRadioButton(t) for t in ("Text", "Picture")]
        radios[0].setChecked(True)
        R.keep(combo, "t/combo")
        R.keep(check, "t/check")
        R.keep(line, "t/line")
        R.keep(spin, "t/spin")
        R.keep_one_of(radios, "t/radio")
        return combo, check, line, spin, radios

    combo, check, line, spin, radios = controls()
    assert combo.currentIndex() == 0 and not check.isChecked() and line.text() == "" and spin.value() == 0
    combo.setCurrentIndex(2)
    check.setChecked(True)
    line.setText("FX_PUMP1")
    spin.setValue(7)
    radios[1].setChecked(True)
    # the next opening, its project counts changed: found again by the item's data
    combo, check, line, spin, radios = controls()
    combo.setItemText(2, "FxLib (4)")
    assert combo.currentData() == "FxLib" and check.isChecked() and line.text() == "FX_PUMP1"
    assert spin.value() == 7 and radios[1].isChecked()
    # a choice made outside the dialog is kept too
    R.store("t/line", "FX_PUMP2")
    assert controls()[2].text() == "FX_PUMP2"


def test_a_file_dialog_starts_in_the_folder_chosen_last(tmp_path, monkeypatch):
    _app()
    from PySide6 import QtWidgets

    from ctgedit.ui import remember as R

    started = []

    def pick(parent, title, start, filt):
        started.append(start)
        return str(tmp_path / "keys.json"), filt

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", pick)
    assert R.file_path(None, "Export", "keys", "JSON (*.json)", save=True, file_name="keys.json") == str(
        tmp_path / "keys.json"
    )
    R.file_path(None, "Export", "keys", "JSON (*.json)", save=True, file_name="keys.json")
    assert started == [
        os.path.join(os.path.expanduser("~"), "keys.json"),
        os.path.join(str(tmp_path), "keys.json"),
    ]
