"""Mark Changes Since Save and the Changes panel."""

import pytest
from PySide6 import QtWidgets
from qtkit import app as _app


@pytest.fixture
def win(fx_page):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_page("FxComposites"))
    w.resize(1400, 900)
    w.show()
    QtWidgets.QApplication.processEvents()
    # (open checks may update the page: start from it as saved)
    if w.doc.dirty:
        w.save()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()


def _genies(doc, n=5):
    return [r for r in doc.page.roots() if doc.page.objects[r].is_genie][:n]


def test_five_moved_genies_are_five_changed_rows_moved(win):
    from ctgedit.ui.panels import changes_dock as CD

    doc = win.doc
    g = _genies(doc)
    assert len(g) == 5
    doc.move_objects(g, 5, 0)
    win._refresh()
    cs = CD.changes_since_save(doc, detail=True)
    assert [c.kind for c in cs.changes] == ["changed"] * 5
    assert all(c.summary.startswith("moved 5, 0") for c in cs.changes)
    assert {c.index for c in cs.changes} == set(g)
    # counted by unit, with the records in brackets
    assert cs.message() == "5 objects changed since the save."
    win.dock_changes.show()
    QtWidgets.QApplication.processEvents()
    win._changes_refresh()
    tree = win.changes_dock.tree
    assert tree.topLevelItemCount() == 5
    assert all(tree.topLevelItem(k).text(3).startswith("moved 5, 0") for k in range(5))
    assert all(tree.topLevelItem(k).text(0) == "Changed" for k in range(5))
    # the labels name the element, never a cache path
    assert not any("_CompositionCaches" in tree.topLevelItem(k).text(1) for k in range(5))


def test_next_change_frames_each_change_in_turn(win):
    doc = win.doc
    g = _genies(doc)
    doc.move_objects(g, 5, 0)
    win._refresh()
    seen = []
    for _k in range(5):
        win.act_next_change.trigger()
        assert len(win._sel) == 1
        seen.append(win._sel[0])
        assert win.statusBar().currentMessage().startswith("Change %d of 5" % len(seen))
        # brought into view
        box = doc.selection_extent(win._sel, bounds=True)
        from PySide6 import QtCore

        r = QtCore.QRectF(box[0], box[1], box[2] - box[0], box[3] - box[1])
        assert win.view.mapToScene(win.view.viewport().rect()).boundingRect().intersects(r)
    assert sorted(seen) == sorted(g)
    win.act_next_change.trigger()  # wraps round
    assert win._sel[0] == seen[0]
    win.act_prev_change.trigger()
    assert win._sel[0] == seen[-1]
    # the marks went on by themselves; the chip counts
    assert win.act_mark_changes.isChecked()
    assert win._changes_chip.isVisible() and win._changes_chip.text() == "5 changed"
    assert win.act_next_change.shortcut().toString() == "Alt+F5"


def test_new_and_removed_units_and_their_marks(win):
    from ctgedit.ui.panels import changes_dock as CD

    doc = win.doc
    g = _genies(doc, 2)
    doc.delete_objects([g[0]])
    sel = doc.duplicate_objects([g[1]], offset=20)
    win._sel = list(sel)
    win.act_mark_changes.setChecked(True)
    win._mark_changes()
    cs = CD.changes_since_save(doc)
    kinds = sorted(c.kind for c in cs.changes)
    assert kinds == ["new", "removed"]
    removed = next(c for c in cs.changes if c.kind == "removed")
    assert removed.index is None and removed.box is not None
    marks = CD.marks_of(win.view)[0]
    assert sorted(k for k, _b in marks) == ["new", "removed"]
    assert cs.chip_text() == "2 changes" and cs.chip_tip() == "1 new, 1 removed"
    # Select All Changed selects the new one (a removed one is not on the page)
    win.act_select_changed.trigger()
    assert win._sel == [c.index for c in cs.changes if c.kind == "new"]
    # a removed change is gone to: no selection, its saved place in view
    win.go_to_change(removed)
    assert "removed" in win.statusBar().currentMessage()


def test_a_page_change_is_one_page_row(win):
    from ctgedit.ui.panels import changes_dock as CD

    doc = win.doc
    pb = doc.parsed.page
    w0 = pb.page_width
    doc.set_page_size(w0 + 16, pb.page_height)
    assert pb.page_width == w0 + 16
    cs = CD.changes_since_save(doc)
    assert [c.kind for c in cs.changes] == ["page"] and "size" in cs.changes[0].summary
    assert cs.ordered()[0].kind == "page"


def test_double_click_selects_and_marks_off_when_both_are_off(win):
    from ctgedit.ui.panels import changes_dock as CD

    doc = win.doc
    g = _genies(doc, 1)
    doc.move_objects(g, 0, 7)
    win.dock_changes.show()
    QtWidgets.QApplication.processEvents()
    win._refresh()
    cd = win.changes_dock
    assert cd.tree.topLevelItemCount() == 1
    cd.tree.itemActivated.emit(cd.tree.topLevelItem(0), 0)
    assert win._sel == g
    # the panel alone shows no canvas marks; closing it forgets the saved page read
    assert CD.marks_of(win.view) is None
    win.dock_changes.hide()
    QtWidgets.QApplication.processEvents()
    assert not CD._SAVED_MEMO


def test_view_menu_has_the_change_commands(win):
    from ctgedit.ui.dialog.palette import harvest_actions

    crumbs = {a.objectName(): crumb for a, crumb, _l in harvest_actions(win.menuBar())}
    for name in ("act_mark_changes", "act_next_change", "act_prev_change", "act_select_changed"):
        assert crumbs[name].startswith("View"), name
    assert crumbs["panel.changes"].startswith("View > Panels")
    # the panel's buttons are the menu's commands
    cd = win.changes_dock
    assert cd.next_btn.defaultAction() is win.act_next_change
    assert cd.prev_btn.defaultAction() is win.act_prev_change
    assert cd.select_btn.defaultAction() is win.act_select_changed
