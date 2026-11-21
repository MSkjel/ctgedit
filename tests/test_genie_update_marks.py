"""Genies waiting for a background update are marked on the canvas."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtWidgets  # noqa: E402


@pytest.fixture()
def win():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    w.doc = None
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.close()


def test_genies_being_updated_are_marked_until_the_update_lands(fx_page, win, monkeypatch):
    """FxMain's FxGenies places fx_pump three times (FxPump1 .. FxPump3) among other
    genies: the marks are on every instance of the first instance's element, not on the
    others."""
    win.open_document(fx_page("FxGenies"))
    doc = win.doc
    inst = doc.genie_instances()
    assert inst
    key = (inst[0][1][0].lower(), inst[0][1][1].lower())
    want = [i for i, (a, b) in inst if (a.lower(), b.lower()) == key]
    assert len(want) >= 2 and len(want) < len(inst)
    monkeypatch.setattr(doc, "genie_changes", lambda: {key: None})
    monkeypatch.setattr(doc, "genie_sources", lambda keys: {})
    submitted = []
    runner = win._genie_job_runner()
    monkeypatch.setattr(runner, "submit", lambda *a, **k: submitted.append(a))
    names = win._update_stale_genies(doc)
    assert names and submitted
    assert win.view._pending == want
    win._refresh()  # (a refresh while it runs keeps the marks)
    assert win.view._pending == want
    win._genie_job_finished(doc, names, 0)
    assert win.view._pending == []
    assert doc.ui.genies_updating is None
