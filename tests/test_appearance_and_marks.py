"""Copy / Paste Appearance, named styles and Mark Changes Since Save."""

import os
import sys

import pytest
from PySide6 import QtWidgets
from qtkit import app as _app

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


@pytest.fixture
def win(fx_page):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_page("FxMembers"))
    w.resize(1300, 900)
    w.show()
    QtWidgets.QApplication.processEvents()
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()


def _rects(doc):
    """FxFree1 and FxFree2: free root rectangles (in no group or genie)."""
    from ctgedit.model.document import ObjectKind

    out = []
    for name in ("FxFree1", "FxFree2"):
        (i,) = doc.find_objects_by_name(name)
        o = doc.page.objects[i]
        assert o.kind is ObjectKind.RECTANGLE and o.parent is None
        out.append(i)
    return out


def test_copy_and_paste_appearance(win):
    doc = win.doc
    rects = _rects(doc)
    assert len(rects) >= 2
    a, b = rects[0], rects[1]
    # give the source a look of its own
    assert doc.set_line_width(a, 3)
    assert doc.set_colour(a, 0x00FF8800)
    app = doc.appearance_of(a)
    assert app["line_width"] == 3 and app["fill_colour"] == 0x00FF8800
    win._on_canvas_selection([a])
    assert win.act_copy_appearance.isEnabled() and not win.act_paste_appearance.isEnabled()
    win.copy_appearance()
    assert win._appearance == app
    win._on_canvas_selection([b])
    assert win.act_paste_appearance.isEnabled()
    steps = len(doc._undo)
    win.paste_appearance()
    got = doc.appearance_of(b)
    assert got["line_width"] == 3 and got["fill_colour"] == 0x00FF8800
    assert len(doc._undo) == steps + 1 and doc._undo[-1].label == "paste appearance"
    doc.undo()
    assert doc.appearance_of(b)["line_width"] != 3 or doc.appearance_of(b)["fill_colour"] != 0x00FF8800


def test_named_styles_are_kept_in_the_settings(win, monkeypatch):
    doc = win.doc
    rects = _rects(doc)
    a, b = rects[0], rects[1]
    doc.set_line_width(a, 4)
    win._on_canvas_selection([a])
    win.save_style("Thick")
    assert [s["name"] for s in win._styles()] == ["Thick"]
    win._on_canvas_selection([b])
    win.apply_style("Thick")
    assert doc.appearance_of(b)["line_width"] == 4
    assert doc._undo[-1].label == "style Thick"
    # the menu lists it between Save and Manage
    win._rebuild_styles_menu()
    texts = [x.text() for x in win.styles_menu.actions() if not x.isSeparator()]
    assert texts[0] == "Save Appearance as Style…" and texts[-1] == "Manage Styles…"
    assert "Thick" in texts and "Update Style from Selection" in texts
    win._put_styles([])
    assert win._styles() == []


def test_mark_changes_since_save_outlines_the_edited_objects(win):
    from ctgedit.ui.panels import changes_dock as CD

    doc = win.doc
    win.act_mark_changes.setChecked(True)
    win._mark_changes()
    assert CD.marks_of(win.view) is None
    assert "Nothing changed since the save" in win.statusBar().currentMessage()
    rects = _rects(doc)
    doc.move_objects([rects[0]], 10, 0)
    win._refresh()
    changed, gone = win.changes_since_save()
    assert rects[0] in changed and gone == 0
    marks = CD.marks_of(win.view)[0]
    assert [k for k, _b in marks] == ["changed"]
    # a deleted object makes the page shorter than the saved one, and leaves a ghost
    n = len(doc.page.objects)
    doc.delete_objects([rects[1]])
    win._refresh()
    assert len(doc.page.objects) < n
    assert win.changes_since_save()[1] >= 1
    assert sorted(k for k, _b in CD.marks_of(win.view)[0]) == ["changed", "removed"]
    # off: the marks go
    win.act_mark_changes.setChecked(False)
    win._mark_changes()
    assert CD.marks_of(win.view) is None
    # a page saved is clean again
    win.save()
    win.act_mark_changes.setChecked(True)
    win._mark_changes()
    assert win.changes_since_save() == (set(), 0)
