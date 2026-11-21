"""The Objects panel on large pages."""

import os
import random
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore  # noqa: E402

from ctgedit.model.document import GraphicObject, ObjectKind, Page  # noqa: E402
from qtkit import app as _app


def _page(members=3000, loose=400) -> Page:
    """A group of ``members`` rectangles, a nested group (a group in a group, a hidden
    record), then ``loose`` rectangles at the top level."""
    page = Page(name="Synthetic")
    objs = page.objects
    objs.append(GraphicObject(kind=ObjectKind.GROUP, an=1))
    for k in range(members):
        objs.append(GraphicObject(kind=ObjectKind.RECTANGLE, an=k + 2, parent=0))
        objs[0].members.append(len(objs) - 1)
    g = len(objs)
    objs.append(GraphicObject(kind=ObjectKind.GROUP, an=g + 1))
    for k in range(3):
        objs.append(GraphicObject(kind=ObjectKind.RECTANGLE, an=len(objs) + 1, parent=g))
        objs[g].members.append(len(objs) - 1)
    inner = len(objs)
    objs.append(GraphicObject(kind=ObjectKind.GROUP, an=inner + 1, parent=g))
    objs[g].members.append(inner)
    for k in range(4):
        objs.append(GraphicObject(kind=ObjectKind.RECTANGLE, an=len(objs) + 1, parent=inner))
        objs[inner].members.append(len(objs) - 1)
    hidden = GraphicObject(kind=ObjectKind.RECTANGLE, parent=g, raw={"_non_drawable": True})
    objs.append(hidden)
    objs[g].members.append(len(objs) - 1)
    objs.append(GraphicObject(kind=ObjectKind.RECTANGLE, an=len(objs) + 1, parent=g))
    objs[g].members.append(len(objs) - 1)
    for k in range(loose):
        objs.append(GraphicObject(kind=ObjectKind.RECTANGLE, an=len(objs) + 1))
    return page


def _dock(page):
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    dock = ObjectsDock()
    dock.set_page(page, [])
    return dock


def _selected(dock) -> list:
    return sorted(it.data(0, QtCore.Qt.UserRole) for it in dock._tree.selectedItems())


def _check(dock, want) -> None:
    want = set(want)
    assert _selected(dock) == sorted(want)
    # the items' own flag follows the model too (the context menu reads it)
    assert [i for i, it in dock._items.items() if it.isSelected() != (i in want)] == []
    if want:
        first = min(want, key=lambda i: dock._rank[i])
        assert dock._tree.currentItem() is dock._items[first]


@pytest.mark.parametrize("bulk", [0, 32])
def test_selection_of_many_rows_is_exact(bulk):
    """Random selections, large and small, switching between them, clearing, with rows
    the filter hides: the rows selected are exactly those asked for, by the one-by-one
    path and by the one-selection path alike."""
    _app()
    page = _page(members=600, loose=200)
    dock = _dock(page)
    dock.BULK_SELECT = bulk
    rows = list(dock._items)
    rnd = random.Random(11)
    for _ in range(30):
        want = rnd.sample(rows, rnd.choice((0, 1, 5, 40, 300, len(rows))))
        dock.set_selection(want)
        _check(dock, want)
    # a hidden row (the filter) is cleared too
    dock.set_selection(rows[:100])
    dock._search.setText("zzz-no-such-object")
    dock._apply_filter(dock._search.text())
    dock.set_selection(rows[200:400])
    dock._search.setText("")
    dock._apply_filter("")
    _check(dock, rows[200:400])
    dock.set_selection([])
    _check(dock, [])


def test_selecting_and_clearing_thousands_of_rows_is_quick():
    """3,400 rows: selected and cleared in well under a second."""
    _app()
    page = _page(members=3000, loose=400)
    dock = _dock(page)
    rows = list(dock._items)
    assert len(rows) > 3400
    t = time.perf_counter()
    dock.set_selection(rows)
    picked = time.perf_counter() - t
    _check(dock, rows)
    t = time.perf_counter()
    dock.set_selection(rows[::2])
    switched = time.perf_counter() - t
    _check(dock, rows[::2])
    t = time.perf_counter()
    dock.set_selection([])
    cleared = time.perf_counter() - t
    _check(dock, [])
    assert picked < 0.5 and switched < 0.5 and cleared < 0.5, (picked, switched, cleared)


def _snapshot(dock):
    return sorted(
        (
            i,
            it.text(0),
            it.toolTip(0),
            it.foreground(0).color().name() if it.data(0, QtCore.Qt.ForegroundRole) is not None else "",
            it.data(0, QtCore.Qt.UserRole),
            it.isExpanded(),
        )
        for i, it in dock._items.items()
    )


def test_an_edit_of_most_of_the_page_sets_the_rows_in_place():
    """The rows of a unit that keeps its rows are kept (the same items, still expanded);
    what they show is set again where it changed, and equals a fresh build."""
    _app()
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    page = _page(members=1500, loose=10)
    dock = _dock(page)
    dock._items[0].setExpanded(True)
    before = dict(dock._items)
    # the unit changed: a member got a caption, another a lock, a third a template mark
    edited = Page(
        name="Synthetic",
        objects=[
            GraphicObject(kind=o.kind, an=o.an, parent=o.parent, members=list(o.members), raw=dict(o.raw))
            for o in page.objects
        ],
    )
    edited.objects[5].style.text = "Pump 5"
    edited.objects[6].raw["_dwflags"] = 0x4
    edited.objects[7].raw["_from_template"] = True
    assert dock.update_units(edited, {0}, [5, 6])
    assert all(dock._items[i] is it for i, it in before.items())
    assert dock._items[0].isExpanded()
    assert "Pump 5" in dock._items[5].text(0)
    # (the lock is the row's padlock, no mark in its text)
    from ctgedit.ui.panels.objects_dock import F_LOCKED

    assert dock._looks[6].flags & F_LOCKED
    assert "locked" in dock._items[6].data(0, QtCore.Qt.AccessibleDescriptionRole)
    assert _selected(dock) == [5, 6]
    mine = _snapshot(dock)
    fresh = ObjectsDock()
    fresh.set_page(edited, [5, 6])
    fresh._items[0].setExpanded(True)
    assert mine == _snapshot(fresh)
    # and back: the lock and the template colour go again
    assert dock.update_units(page, {0}, [])
    fresh.set_page(page, [])
    fresh._items[0].setExpanded(True)
    assert _snapshot(dock) == _snapshot(fresh)


def test_a_structure_change_of_most_of_the_page_builds_the_tree_again():
    """A unit whose rows changed (a member removed, a member moved to another group) is
    not patched in place: update_units declines and set_page builds it."""
    _app()
    page = _page(members=1500, loose=10)
    dock = _dock(page)
    fewer = Page(
        name="Synthetic",
        objects=[
            GraphicObject(kind=o.kind, an=o.an, parent=o.parent, members=list(o.members), raw=dict(o.raw))
            for o in page.objects
        ],
    )
    fewer.objects[9].raw["_non_drawable"] = True  # one row less in the unit
    assert not dock.update_units(fewer, {0}, [])
    shorter = Page(name="Synthetic", objects=page.objects[:-1])  # the last row is gone
    big = dock.update_units(shorter, set(range(len(shorter.objects))), [])
    assert not big


def test_turning_every_object_patches_the_tree_and_equals_a_rebuild(fx_page, monkeypatch):
    """FxPerf's FxLarge (about 1,200 records on the normal@XGA template: pump and pipe
    genies, groups, styled shapes, texts), every unit turned: the Objects tree is patched
    in place (no set_page), and equals a full build after the turn, its undo and its
    redo."""
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    w = MainWindow()
    try:
        w.open_document(fx_page("FxLarge", "FxPerf"))
        doc = w.doc
        roots = [
            r
            for r in doc.page.roots()
            if not doc.page.objects[r].raw.get("_non_drawable") and not w._unit_locked(r)
        ]
        w._on_canvas_selection(roots)
        w.view.set_selection(roots)
        # (every unit that turns counts as most of the page)
        monkeypatch.setattr(w.objects_dock, "PATCH_SHARE", 0)
        calls, patched = [], []
        real = w.objects_dock.set_page
        monkeypatch.setattr(w.objects_dock, "set_page", lambda *a, **k: calls.append(1) or real(*a, **k))
        real_patch = w.objects_dock._patch_rows
        monkeypatch.setattr(
            w.objects_dock, "_patch_rows", lambda *a, **k: patched.append(real_patch(*a, **k)) or patched[-1]
        )
        for step in (lambda: w._rotate(True), w.undo, w.redo):
            rows = dict(w.objects_dock._items)
            patched.clear()
            step()
            assert calls == [] and patched == [True]
            assert all(w.objects_dock._items.get(i) is it for i, it in rows.items())
            fresh = ObjectsDock()
            fresh.set_page(doc.page, list(w._sel), hidden=w._hidden_roots)
            for i, it in w.objects_dock._items.items():
                if it.isExpanded():
                    fresh._items[i].setExpanded(True)
            assert _snapshot(w.objects_dock) == _snapshot(fresh)
            assert _selected(w.objects_dock) == _selected(fresh)
    finally:
        w.doc.dirty = False
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
