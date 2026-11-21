"""Genie and property dialogs are modeless and can coexist."""

import os

import pytest
from PySide6 import QtWidgets

PAGE = "FxGenies"


@pytest.fixture()
def win(fx_install):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_install.page(PAGE))
    yield w
    for dlg in list(w._dialogs.values()):
        dlg._ctg_dropped = True
        dlg.reject()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()
    QtWidgets.QApplication.processEvents()


def _named(w, name):
    found = w.doc.find_objects_by_name(name)
    assert len(found) == 1, (name, found)
    return found[0]


def test_genie_dialogs_modeless_and_multiple(win):
    w = win
    pg = w.doc.page
    genies = [_named(w, "FxPump1"), _named(w, "FxPump2")]
    for g in genies:
        assert pg.objects[g].is_genie and w.doc.root_of(g) == g
    for g in genies:
        w._edit_genie(g, pg.objects[g])
    QtWidgets.QApplication.processEvents()
    assert len(w._dialogs) == 2
    for d in w._dialogs.values():
        assert not d.isModal()
        assert d.isVisible()
    # re-opening the same genie raises the existing one (no duplicate)
    w._edit_genie(genies[0], pg.objects[genies[0]])
    assert len(w._dialogs) == 2


def test_double_click_targets_top_level_genie(win):
    """Double-clicking a nested subitem acts on the TOP-LEVEL genie, never the
    inner nested genie/member (canvas clicks don't drill into the subtree)."""
    w = win
    pg = w.doc.page
    nested = _named(w, "FxNested")
    target = None
    for i in range(len(pg.objects)):
        ng = pg.nearest_genie(i)
        top = w.doc.root_of(i)
        if ng is not None and ng != top and pg.objects[top].is_genie:
            target = (i, top)
            break
    assert target is not None
    member, top = target
    assert top == nested  # (the only nesting genie on the page)
    w._on_object_activated(member)
    assert w.view.selection_roots() == [top]
    assert ("genie", top) in w._dialogs  # the TOP genie's dialog, not ng


def test_modeless_dialogs_close_on_document_change(win):
    w = win
    pg = w.doc.page
    g = _named(w, "FxPump1")
    w._edit_genie(g, pg.objects[g])
    assert w._dialogs
    w._close_modeless()
    assert not w._dialogs
