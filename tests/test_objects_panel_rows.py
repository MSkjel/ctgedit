"""The Objects panel: row menus, locks, columns and renames."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the page every test opens
PAGE = "FxMany"
#: a Cyrillic letter: not in the pages' Windows-1252 code page
ZHE = "\u0416"


def _pump(n=8):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture()
def win(monkeypatch):
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
    w.resize(1280, 800)
    yield w
    w.objects_dock.set_columns(["object"])
    for x in list(MainWindow._instances):
        for e in getattr(x, "_docs", []):
            e["doc"].dirty = False
        if x in MainWindow._instances:
            MainWindow._instances.remove(x)
        x.hide()


@pytest.fixture()
def page(fx_page):
    return fx_page(PAGE)


def _open(win, path):
    win.open_document(path)
    win.show()
    _pump()
    return win.doc


def _plain_roots(doc):
    """Top-level objects that are not from the template, not genies and not locked."""
    page = doc.page
    return [
        i
        for i in page.roots()
        if not page.objects[i].raw.get("_from_template")
        and not getattr(page.objects[i], "is_genie", False)
        and page.objects[i].kind.value in ("rectangle", "ellipse", "line", "text", "polygon", "pipe")
    ]


def _submenu(menu, text):
    a = next((a for a in menu.actions() if a.menu() is not None and a.text() == text), None)
    return a.menu() if a is not None else None


# ------------------------------------------------------------------ the Columns menu
def test_every_row_menu_ends_with_the_columns(win, page):
    doc = _open(win, page)
    dock = win.objects_dock
    i = _plain_roots(doc)[0]
    win._on_canvas_selection([i])
    menu = dock.build_menu()
    cols = _submenu(menu, "Columns")
    assert cols is not None and menu.actions()[-1].menu() is cols
    pos = next(a for a in cols.actions() if a.text() == "Position")
    assert pos.isCheckable() and not pos.isChecked() and pos.objectName()
    pos.trigger()
    assert "position" in dock.columns
    menu.deleteLater()


# ------------------------------------------------------------------ the padlock
def test_edit_lock_and_unlock_turn_the_padlock(win, page):
    from ctgedit.ui.panels.objects_dock import F_LOCKED

    doc = _open(win, page)
    dock = win.objects_dock
    i = _plain_roots(doc)[0]
    win._on_canvas_selection([i])
    win._lock_selection()
    _pump()
    assert dock._looks[i].flags & F_LOCKED
    win.act_break_lock.setChecked(True)
    win._break_locks()
    try:
        win._on_canvas_selection([i])
        assert win._sel == [i]
        win._unlock_selection()
        _pump()
        assert not dock._looks[i].flags & F_LOCKED
    finally:
        win.act_break_lock.setChecked(False)
        win._break_locks()


# ------------------------------------------------------------------ the Position column
def test_the_position_column_follows_a_move_and_its_undo(win, page):
    doc = _open(win, page)
    dock = win.objects_dock
    dock.set_columns(["object", "position"])
    i = _plain_roots(doc)[0]
    col = dock.columns.index("position")
    g = doc.page.objects[i].geometry
    x, y = g.x, g.y
    assert dock._items[i].text(col) == "%d, %d" % (x, y)
    doc.move_objects([i], 40, 20)
    win._refresh(rebuild_tree=False, units=[i])
    g = doc.page.objects[i].geometry
    assert dock._items[i].text(col) == "%d, %d" % (g.x, g.y) != "%d, %d" % (x, y)
    win.undo()
    _pump()
    assert dock._items[i].text(col) == "%d, %d" % (x, y)


# ------------------------------------------------------------------ rename a hidden row
def test_rename_of_a_row_the_search_hides_opens_the_editor(win, page):
    doc = _open(win, page)
    dock = win.objects_dock
    dock._search.setText("valve")
    dock._search.flush()
    _pump()
    i = next(k for k in _plain_roots(doc) if dock._items[k].isHidden())
    win._on_canvas_selection([i])
    win.rename_selection()
    _pump()
    assert dock._search.text() == ""
    assert dock._tree.state() == QtWidgets.QAbstractItemView.EditingState
    dock._tree.closePersistentEditor(dock._items[i], 0)
    dock._tree.setFocus()
    _pump()


# ------------------------------------------------------------------ patterns
def test_a_pattern_with_attribute_or_index_access_is_refused(win, page):
    from ctgedit.ui.dialog.rename_dialog import RenameObjectsDialog, pattern_name

    doc = _open(win, page)
    i = _plain_roots(doc)[0]
    for bad in ("{name.x}", "{n[0]}", "{n!r}", "{}", "{n:{an}}", "{nope}", "{"):
        with pytest.raises(ValueError):
            pattern_name(doc.page, i, bad, 1)
    assert pattern_name(doc.page, i, "Tank_{n:03}", 7) == "Tank_007"
    # the dialog says so, and Rename does not keep the last good pattern's names
    dlg = RenameObjectsDialog(win, doc, _plain_roots(doc)[:2])
    dlg._mode.setCurrentIndex(0)
    dlg._pattern.setText("Good_{n}")
    dlg.flush()
    assert dlg._ok.isEnabled()
    dlg._pattern.setText("{name.x}")
    dlg.flush()
    assert not dlg._ok.isEnabled() and "unknown field" in dlg._status.text()
    dlg.close()


# ------------------------------------------------------------------ the code page
def test_both_name_checks_know_the_code_page(win, page):
    from ctgedit.ui.dialog.rename_dialog import codepage_problem, plan_problems

    doc = _open(win, page)
    i = _plain_roots(doc)[0]
    assert codepage_problem("Pump") == ""
    msg = codepage_problem("Pump" + ZHE)
    assert "cannot be stored" in msg and "U+0416" in msg
    assert "cannot be stored" in win._tree_name_problem(i, "Pump" + ZHE)
    assert "cannot be stored" in plan_problems(doc, [(i, "Tank" + ZHE + "1")])[i]
    assert win._tree_name_problem(i, "Pump") == ""


# ------------------------------------------------------------------ the preview's cost
def test_the_preview_reads_each_name_once_and_waits_for_typing(win, page, monkeypatch):
    from ctgedit.ui.dialog import rename_dialog as RD

    doc = _open(win, page)
    roots = [i for i in doc.page.roots()]
    calls = []
    real = RD.object_name_of
    monkeypatch.setattr(RD, "object_name_of", lambda o: (calls.append(1), real(o))[1])
    targets = _plain_roots(doc)
    probs = RD.plan_problems(doc, [(i, "Unique_%d" % k) for k, i in enumerate(targets)])
    assert probs == {}
    assert len(calls) <= len(roots)  # (not targets x roots)
    monkeypatch.setattr(RD, "object_name_of", real)
    # a name an object not renamed has is still found (without case)
    doc.set_object_name(targets[-1], "Taken1")
    assert "already used" in RD.plan_problems(doc, [(targets[0], "TAKEN1")])[targets[0]]
    dlg = RD.RenameObjectsDialog(win, doc, targets[:3])
    dlg._mode.setCurrentIndex(0)
    dlg._pattern.setText("P_{n}")
    # (the preview waits; Rename stays on so Enter right away still checks and renames)
    assert dlg._typing.isActive() and dlg._ok.isEnabled()
    dlg._pattern.setText("Pump_{n}")
    _pump()
    dlg.flush()
    assert not dlg._typing.isActive() and dlg._ok.isEnabled()
    assert dlg._table.item(0, 2).text() == "Pump_1"
    dlg.close()


def test_apply_renames_swaps_without_a_scan_per_name(win, page, monkeypatch):
    from ctgedit.ui.dialog.rename_dialog import apply_renames

    doc = _open(win, page)
    a, b, c = _plain_roots(doc)[:3]
    doc.set_object_name(a, "One")
    doc.set_object_name(b, "Two")
    doc.set_object_name(c, "Three")
    calls = []
    real = doc.name_conflict
    monkeypatch.setattr(doc, "name_conflict", lambda i, n: (calls.append(n), real(i, n))[1])
    # a round of three: one passes through a free name
    assert apply_renames(doc, [(a, "Two"), (b, "Three"), (c, "One")]) == 3
    assert (doc.object_name(a), doc.object_name(b), doc.object_name(c)) == ("Two", "Three", "One")
    # (set_object_name checks each name itself: 3 names and the one through a free name,
    # plus the free name's own check; no scan per name and per pass on top)
    assert len(calls) <= 5, calls


# ------------------------------------------------------------------ the filter popover
def test_the_filter_popover_goes_when_it_closes(win, page):
    _open(win, page)
    dock = win.objects_dock
    for _ in range(3):
        dock.open_filter()
        _pump()
        dock._popover.close()
        _pump()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    _pump()
    assert dock.findChildren(QtWidgets.QFrame, "objectsFilter") == []


# ------------------------------------------------------------------ the selection order
def test_selection_order_is_the_order_the_rows_were_clicked(win, page):
    from ctgedit.ui.dialog.rename_dialog import ordered

    doc = _open(win, page)
    dock = win.objects_dock
    roots = _plain_roots(doc)
    first, second, third = roots[5], roots[1], roots[3]  # (not in record order)
    tree = dock._tree
    sm = tree.selectionModel()
    flags = QtCore.QItemSelectionModel.Rows
    sm.select(tree.indexFromItem(dock._items[first]), QtCore.QItemSelectionModel.ClearAndSelect | flags)
    for i in (second, third):
        sm.select(tree.indexFromItem(dock._items[i]), QtCore.QItemSelectionModel.Select | flags)
    _pump()
    assert win._sel == [first, second, third]
    assert ordered(doc.page, win._sel, "selection") == [first, second, third]
    assert ordered(doc.page, win._sel, "drawing") == sorted([first, second, third])
    # a row taken out keeps the others' order
    sm.select(tree.indexFromItem(dock._items[second]), QtCore.QItemSelectionModel.Deselect | flags)
    _pump()
    assert win._sel == [first, third]
