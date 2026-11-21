"""Arrow key nudges: step size and key repeat bursts."""

import os
import shutil
import time

import pytest
from qtkit import app as _app


@pytest.fixture
def MIXER(fx_page, monkeypatch):
    """The page (in the fake install copy), its project on CTG_LIB_PATH."""
    path = fx_page("FxLarge", "FxPerf")
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(path))
    return path


def _win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    return MainWindow()


def _drop(w):
    from ctgedit.ui.main_window import MainWindow

    w.commit_nudge()
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)


@pytest.fixture()
def mixer(MIXER, tmp_path):
    """A window on a copy of the page (the fixture copy is never written)."""
    dst = tmp_path / "Mixer.ctg"
    shutil.copyfile(MIXER, dst)
    w = _win()
    w.resize(1400, 900)
    w.show()
    w.open_document(str(dst))
    _pump(0.05)
    yield w
    _drop(w)
    w.hide()


def _pump(seconds=0.0):
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance()
    end = time.perf_counter() + seconds
    while True:
        app.processEvents()
        if time.perf_counter() >= end:
            return
        time.sleep(0.005)


def _genie(w, skip=()):
    """A placed genie root of the page (not the template) whose subtree moves whole."""
    page = w.doc.page
    for r in page.roots():
        o = page.objects[r]
        if r in skip or not o.is_genie or o.raw.get("_from_template") or w._unit_locked(r):
            continue
        sub = w.doc.subtree(r)
        if len(sub) > 1 and w._filter_template(sub, "move", quiet=True) == sub:
            return r
    raise AssertionError("no movable genie on the page")


def _select(w, root):
    w._on_canvas_selection([root])
    w.view.set_selection([root])


def _press(w, key, n=1):
    """Arrow-key presses as the canvas shortcuts deliver them (MainWindow._MOVE_KEYS)."""
    dx, dy = {"Right": (1, 0), "Left": (-1, 0), "Down": (0, 1), "Up": (0, -1)}[key]
    for _ in range(n):
        w._move_key(dx, dy, key)


def _item_pos(w, i):
    it = w.scene._items[i]
    return (it.pos().x(), it.pos().y())


def test_nudge_uses_configured_step(MIXER):
    w = _win()
    try:
        w.open_document(MIXER)
        # first selection that survives the move filter
        sel = None
        for i in range(len(w.doc.page.objects)):
            if w._filter_template([i], "move"):
                sel = i
                break
        assert sel is not None
        w._sel = [sel]
        w.view.set_selection([sel])

        calls = []
        w.doc.move_objects = lambda objs, dx, dy: calls.append((dx, dy))

        w._nudge_step = 5
        w.act_slow_keys.setChecked(False)
        w.nudge(1, 0)
        assert calls == []  # the burst waits
        assert w.commit_nudge() and calls[-1] == (5, 0)

        w.nudge(0, -1)
        w.commit_nudge()
        assert calls[-1] == (0, -5)

        # Slow Speed forces 1px regardless of the configured step
        w.act_slow_keys.setChecked(True)
        w.nudge(1, 0)
        w.commit_nudge()
        assert calls[-1] == (1, 0)

        # the presses of one burst: one move of their sum
        w.act_slow_keys.setChecked(False)
        n = len(calls)
        for dx, dy in ((1, 0), (1, 0), (0, 1), (-1, 0), (1, 1)):
            w.nudge(dx, dy)
        assert len(calls) == n
        w.commit_nudge()
        assert calls[n:] == [(10, 10)]
        assert not w.commit_nudge()  # nothing left
    finally:
        w.doc.dirty = False
        _drop(w)


def test_nudge_moves_whole_genie_subtree(MIXER):
    """Nudging a selected genie moves EVERY member (bbox rigid), not just the root container."""
    w = _win()
    try:
        w.open_document(MIXER)
        page = w.doc.page
        # a real (non-template) inline genie whose members are all movable
        root = None
        for i, o in enumerate(page.objects):
            if not (o.members and not o.children and o.kind.name in ("GENIE", "GROUP")):
                continue
            if o.raw.get("_from_template"):
                continue  # page template genie: locked
            sub = w.doc.subtree(i)
            if len(sub) > 1 and len(w._filter_template(sub, "move")) == len(sub):
                root = i
                break
        assert root is not None
        sub = w.doc.subtree(root)

        # canvas selection: view holds the SUBTREE, main window holds the ROOTS
        w.view.set_selection([root])
        w._sel = [root]
        before = {i: (page.objects[i].geometry.x, page.objects[i].geometry.y) for i in sub}

        w._nudge_step = 4
        w.act_slow_keys.setChecked(False)
        w.nudge(1, 0)
        w.commit_nudge()

        page = w.doc.page
        for i in sub:
            ox, oy = before[i]
            assert page.objects[i].geometry.x == ox + 4, i
            assert page.objects[i].geometry.y == oy, i
    finally:
        w.doc.dirty = False
        _drop(w)


def test_nudge_flashes_alignment_guides(MIXER):
    """After a nudge that lands a selection edge on a target (here the page-left
    at x=0), the smart-guide lines show."""
    w = _win()
    try:
        w.open_document(MIXER)
        sc = w.view.scene()
        # a simple top-level drawable object with its own scene item
        idx = next(
            i
            for i in range(len(w.doc.page.objects))
            if w.doc.root_of(i) == i and i in sc._items and w._filter_template([i], "move") == [i]
        )
        w.view.set_selection([idx])
        w._sel = [idx]
        w.view._smart_guides = True

        # shove its Extent's left edge onto the page-left (x=0), an exact guide target
        left = w.doc.page.objects[idx].geometry.x
        if left:
            w.doc.move_objects([idx], -left, 0)
            w._refresh()
        w.view.set_selection([idx])
        w._sel = [idx]
        w.view.flash_alignment_guides()
        assert any(axis == "v" and abs(coord) <= 1 for axis, coord in w.view._sg_active)
    finally:
        w.doc.dirty = False
        _drop(w)


def test_settings_round_trips_nudge_step():
    w = _win()
    try:
        from ctgedit.ui.prefs.settings_dialog import SettingsDialog
        from ctgedit.ui.prefs.settings import nudge_burst_ms

        d = SettingsDialog(w)
        d._nudge.setValue(12)
        d._nudge_burst.setValue(350)
        d._apply()
        assert w._nudge_step == 12
        assert nudge_burst_ms() == 350
        d = SettingsDialog(w)
        assert d._nudge_burst.value() == 350
        d.restore_defaults()
        d._apply()
        assert nudge_burst_ms() == 200
    finally:
        _drop(w)


# ------------------------------------------------------------------ bursts
def test_a_burst_is_one_move_of_the_sum_and_one_undo_step(mixer):
    w, doc = mixer, mixer.doc
    w._nudge_step = 1
    w.act_slow_keys.setChecked(False)
    root = _genie(w)
    sub = doc.subtree(root)
    _select(w, root)
    _pump()
    before, steps = doc.serialize(), len(doc._undo)
    pos0 = _item_pos(w, root)
    x0 = doc.page.objects[root].geometry.x
    _press(w, "Right", 5)
    _press(w, "Down", 3)
    _press(w, "Left", 1)
    # the canvas shows the moves at once; the page has none of them yet
    assert w.nudge_pending() and w.view.nudge_offset() == (4, 3)
    assert _item_pos(w, root) == (pos0[0] + 4, pos0[1] + 3)
    assert doc.serialize() == before and doc.page.objects[root].geometry.x == x0
    assert w.view.busy()  # (a background genie update waits)
    assert w.commit_nudge()
    assert not w.nudge_pending() and not w.view.busy()
    after = doc.serialize()
    assert len(doc._undo) == steps + 1  # one undo step for the burst
    assert doc.page.objects[root].geometry.x == x0 + 4
    assert _item_pos(w, root) == (pos0[0] + 4, pos0[1] + 3)
    # the same bytes as one move of the summed vector
    w.undo()
    assert doc.serialize() == before
    doc.move_objects(sub, 4, 3)
    assert doc.serialize() == after


def test_an_interrupted_burst_gives_the_bytes_of_the_moves_in_order(mixer):
    """A command during a burst (Bring to Front, which renumbers the records) commits the
    presses before it first; the presses after it are a burst of their own."""
    w, doc = mixer, mixer.doc
    w._nudge_step = 2
    w.act_slow_keys.setChecked(False)
    root = _genie(w)
    _select(w, root)
    before, steps = doc.serialize(), len(doc._undo)
    _press(w, "Right", 3)
    w._reorder(list(w._sel), "front")  # the command: the burst lands first
    assert not w.nudge_pending()
    front = w._sel[0]
    assert front != root
    _press(w, "Up", 2)
    _press(w, "Right", 1)
    w.commit_nudge()
    got = doc.serialize()
    assert len(doc._undo) == steps + 3
    for _ in range(3):
        w.undo()
    assert doc.serialize() == before
    # the same edits made one by one
    doc.move_objects(doc.subtree(root), 6, 0)
    (new,) = doc.reorder([root], "front")
    assert new == front
    doc.move_objects(doc.subtree(new), 2, -4)
    assert doc.serialize() == got


def test_a_burst_ends_on_release_other_input_and_the_pause(mixer, monkeypatch):
    from PySide6 import QtCore, QtGui, QtWidgets

    w, doc = mixer, mixer.doc
    w._nudge_step = 1
    root = _genie(w)
    _select(w, root)
    steps = len(doc._undo)

    def send(ev, to=None):
        QtWidgets.QApplication.sendEvent(to or w.view, ev)

    Key = QtCore.Qt.Key
    # the arrow key let go: committed (an auto-repeat release is not the end)
    _press(w, "Right", 2)
    send(QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, Key.Key_Right, QtCore.Qt.NoModifier, "", True))
    assert w.nudge_pending()
    send(QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, Key.Key_Right, QtCore.Qt.NoModifier))
    assert not w.nudge_pending() and len(doc._undo) == steps + 1
    # another key (a shortcut's override event) or a modifier alone
    _press(w, "Down", 1)
    send(QtGui.QKeyEvent(QtCore.QEvent.ShortcutOverride, Key.Key_Shift, QtCore.Qt.ShiftModifier))
    assert w.nudge_pending()
    send(QtGui.QKeyEvent(QtCore.QEvent.ShortcutOverride, Key.Key_Z, QtCore.Qt.ControlModifier))
    assert not w.nudge_pending() and len(doc._undo) == steps + 2
    # a click anywhere (here on the Objects tree)
    _press(w, "Up", 1)
    target = w.objects_dock
    send(
        QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonPress,
            QtCore.QPointF(5, 5),
            QtCore.QPointF(target.mapToGlobal(QtCore.QPoint(5, 5))),
            QtCore.Qt.LeftButton,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoModifier,
        ),
        target,
    )
    assert not w.nudge_pending() and len(doc._undo) == steps + 3
    _select(w, root)
    # a save (and everything else that stores an open text edit first)
    _press(w, "Left", 1)
    w._flush_text_edit()
    assert not w.nudge_pending() and len(doc._undo) == steps + 4
    # the pause: Settings > Join arrow-key presses within
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/nudge_burst_ms", 40)
    _press(w, "Right", 2)
    assert w.nudge_pending()
    _pump(0.3)
    assert not w.nudge_pending() and len(doc._undo) == steps + 5
    # 0: every press is a move of its own
    app_settings().setValue("opt/nudge_burst_ms", 0)
    _press(w, "Right", 3)
    assert not w.nudge_pending() and len(doc._undo) == steps + 8
    app_settings().remove("opt/nudge_burst_ms")
    # the filter is gone with the burst
    assert not w._nudge_filter.installed


def test_a_burst_keeps_its_records_when_the_page_changed_meanwhile(mixer):
    """Should the page change under a burst without the input that commits it (a
    background change), the burst moves its records where they are now."""
    w, doc = mixer, mixer.doc
    w._nudge_step = 1
    root = _genie(w)
    other = _genie(w, skip=(root,))
    _select(w, root)
    _press(w, "Right", 2)
    rec = doc.parsed.page.objects[root]
    # (the page changes behind the burst's back: another unit to the front)
    burst = w._nudge_burst
    w._nudge_burst = None
    doc.reorder([other], "front" if other < root else "back")
    w._nudge_burst = burst

    def where():
        return next(k for k, o in enumerate(doc.parsed.page.objects) if o is rec)

    x0 = doc.page.objects[where()].geometry.x
    assert w.commit_nudge()
    now = where()
    assert now != root
    assert doc.page.objects[now].geometry.x == x0 + 2


def test_shift_resize_and_node_keys_commit_the_burst_first(mixer):
    w, doc = mixer, mixer.doc
    w._nudge_step = 1
    root = _genie(w)
    _select(w, root)
    steps = len(doc._undo)
    _press(w, "Right", 2)
    w.resize_by_keys(1, 0)
    assert not w.nudge_pending()
    assert len(doc._undo) >= steps + 1 and doc._undo[steps].label == "move"


def test_a_drag_moves_every_item_and_commits_once(mixer):
    """The drag moves the items it took when it started (looked up once, not per frame)
    and the release commits one move."""
    from PySide6 import QtCore, QtGui

    w, doc = mixer, mixer.doc
    view = w.view
    view._snap = False
    root = _genie(w)
    _select(w, root)
    _pump()
    sub = doc.subtree(root)
    items = view._items_of(sub)
    assert items
    start = [(it.pos().x(), it.pos().y()) for it in items]
    g = doc.page.objects[root].geometry
    c = view.mapFromScene(QtCore.QPointF(g.x + g.extent_x / 2.0, g.y + g.extent_y / 2.0))

    def ev(kind, vp, buttons):
        return QtGui.QMouseEvent(
            kind,
            QtCore.QPointF(vp),
            QtCore.QPointF(view.viewport().mapToGlobal(vp)),
            QtCore.Qt.LeftButton,
            buttons,
            QtCore.Qt.NoModifier,
        )

    view.mousePressEvent(ev(QtCore.QEvent.MouseButtonPress, c, QtCore.Qt.LeftButton))
    assert view._maybe_drag, "the press did not hit the genie"
    steps = len(doc._undo)
    z = view.zoom()
    for k in range(1, 6):
        view.mouseMoveEvent(
            ev(QtCore.QEvent.MouseMove, c + QtCore.QPoint(round(4 * k * z), 0), QtCore.Qt.LeftButton)
        )
    assert view._moving
    off = view._move_applied
    for it, (x, y) in zip(items, start):
        assert (it.pos().x(), it.pos().y()) == (x + off.x(), y + off.y())
    x0 = g.x
    view.mouseReleaseEvent(
        ev(QtCore.QEvent.MouseButtonRelease, c + QtCore.QPoint(round(20 * z), 0), QtCore.Qt.NoButton)
    )
    _pump()
    assert len(doc._undo) == steps + 1
    assert doc.page.objects[root].geometry.x == x0 + round(off.x())
    assert view._drag_items == []


def test_the_flash_uses_the_extents_a_drag_snaps_to(mixer):
    """The alignment lines after a nudge come from the objects' Extents (the boxes a drag
    snaps to), not from the item bounds, which include the pen: a rectangle with a 6 px
    pen whose Extent starts at x 0 lines up with the page's left edge."""
    w, doc = mixer, mixer.doc
    i = doc.create_object("rectangle", rect=(0, 300, 80, 340), colour=0x3366CC)
    assert doc.set_line_width(i, 6)
    w._refresh()
    w.view._smart_guides = True
    _select(w, i)
    item_left = w.view._unit_rect([i]).left()
    assert item_left < -1  # the pen reaches past the Extent
    w.view.flash_alignment_guides()
    assert any(axis == "v" and abs(c) <= 1 for axis, c in w.view._sg_active), w.view._sg_active
    # the drag's targets are the same boxes
    w.view._sg_begin_drag()
    assert w.view._sg_bbox.left() == 0


def test_the_move_filter_skips_the_walks_for_whole_units(mixer, monkeypatch):
    """A selection of whole top-level units passes the genie-member check without walking
    each record's ancestors; a genie's member chosen alone is still kept out."""
    w, doc = mixer, mixer.doc
    calls = []
    real = type(w)._genie_member_edits
    monkeypatch.setattr(type(w), "WHOLE_UNIT_MIN", 1)
    monkeypatch.setattr(
        type(w), "_genie_member_edits", lambda self, idx: calls.append(list(idx)) or real(self, idx)
    )
    a = _genie(w)
    b = _genie(w, skip=(a,))
    sub_a, sub_b = doc.subtree(a), doc.subtree(b)
    assert not w._filter_template([sub_b[1]], "move", quiet=True), "the genie's member is not kept out"
    calls.clear()
    assert w._filter_template(sub_a + sub_b, "move", quiet=True) == sub_a + sub_b
    assert calls[-1] == []  # nothing left to walk
    member = sub_b[1]
    got = w._filter_template(sub_a + [member], "move", quiet=True)
    assert got == sub_a  # the member alone is dropped
    assert calls[-1] == [member]
    # the result is what the walk over every index gives
    monkeypatch.setattr(type(w), "WHOLE_UNIT_MIN", 10**9)
    assert w._filter_template(sub_a + [member], "move", quiet=True) == got


def test_paste_and_ungroup_select_the_top_level_units(mixer):
    """Paste selects the pasted units' roots and Ungroup the group's former direct
    children, not each of their records."""
    from PySide6 import QtWidgets

    w, doc = mixer, mixer.doc
    root = _genie(w)
    _select(w, root)
    w.copy_selected()
    try:
        n = len(doc.parsed.page.objects)
        w.paste_clipboard()
    finally:
        QtWidgets.QApplication.clipboard().clear()
        QtWidgets.QApplication.processEvents()
    assert w._sel == [n]
    assert w.view.selection() == doc.subtree(n)
    # ungroup: a group of two genies, dissolved again
    other = _genie(w, skip=(root, n))
    g = doc.group_objects([root, other])
    w._refresh()
    _select(w, g)
    kids = [m for m in doc.page.objects[g].members]
    assert len(kids) == 2
    w._ungroup_selection()
    page = doc.page
    assert len(w._sel) == 2
    assert all(page.objects[i].parent is None and page.objects[i].is_genie for i in w._sel)
    assert len(w.view._units) == 2


@pytest.mark.parametrize("turned", [False, True])
def test_moves_of_one_step_each_give_the_bytes_of_one_move_of_their_sum(MIXER, turned):
    """What makes a burst one move: N moves of 1 px write the bytes one move of N px
    writes (every unit together, and the page's biggest units alone; also after a quarter
    turn, whose half pixels the moves carry)."""
    from ctgedit.editing import EditableDocument

    d0 = EditableDocument.open(MIXER)
    page = d0.page
    roots = [
        r
        for r in page.roots()
        if not page.objects[r].raw.get("_non_drawable") and not page.objects[r].raw.get("_from_template")
    ]
    big = sorted(roots, key=lambda r: -len(page.subtree(r)))[:3]
    small = [r for r in roots if len(page.subtree(r)) == 1][:2]
    for sel in [roots] + [[r] for r in big + small]:
        outs = []
        for steps in (True, False):
            d = EditableDocument.open(MIXER)
            idx = d.expand_to_subtrees(sel)
            if turned:
                d.rotate_objects(sel, True)
                idx = d.expand_to_subtrees(sel)
            if steps:
                for _ in range(5):
                    d.move_objects(idx, 1, 0)
                for _ in range(3):
                    d.move_objects(idx, 0, -1)
            else:
                d.move_objects(idx, 5, -3)
            outs.append(d.serialize())
        assert outs[0] == outs[1], sel[:3]
