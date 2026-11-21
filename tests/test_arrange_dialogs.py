"""Shortcuts and the Arrange dialogs."""

import json
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtGui, QtWidgets  # noqa: E402

from ctgedit.format import geometry as G  # noqa: E402
from qtkit import app as _app

INVENTORY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "ctdraw_inventory.json"
)

ACCEPT = QtWidgets.QDialog.Accepted
REJECT = QtWidgets.QDialog.Rejected


def _window():
    _app()
    from ctgedit.ui.main_window import MainWindow

    return MainWindow()


def _close(w):
    from ctgedit.ui.main_window import MainWindow

    for dlg in list(getattr(w, "_dialogs", {}).values()):
        dlg.reject()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()


@pytest.fixture()
def bare():
    w = _window()
    yield w
    _close(w)


@pytest.fixture()
def host(fx_page):
    """The page the new shapes go on: FxShapes in the test's fx_install copy."""
    return fx_page("FxShapes")


@pytest.fixture()
def win(host):
    w = _window()
    w.open_document(host)
    yield w
    _close(w)


def _box(doc, i):
    return G.norm_rect(G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1)


def _rects(win, *rects):
    idx = [win.doc.create_object("rectangle", rect=r, colour=0x336699) for r in rects]
    win._refresh()
    win._set_selection(list(idx))
    return idx


def _snap(doc):
    return [(bytes(po.body_raw), po.subtype_size, po.class_flag) for po in doc.parsed.page.objects]


# ------------------------------------------------------------------ keys
def _all_keys(w):
    """[(key, owner)] of every QAction / QShortcut in the window."""
    out = []
    for a in w.findChildren(QtGui.QAction):
        for s in a.shortcuts():
            if s.toString():
                out.append((s.toString(), "action %r" % a.text()))
    for sc in w.findChildren(QtGui.QShortcut):
        if sc.key().toString():
            out.append((sc.key().toString(), "shortcut on %s" % type(sc.parent()).__name__))
    return out


def _collisions(w):
    seen, bad = {}, []
    for key, owner in _all_keys(w):
        if key in seen and seen[key] != owner:
            bad.append((key, seen[key], owner))
        seen.setdefault(key, owner)
    return bad


def test_no_shortcut_is_bound_twice(bare):
    """Every key of the window (menu actions, canvas-only keys, the palette)
    does one thing: a key bound twice fires neither (Qt: ambiguous)."""
    assert _collisions(bare) == []


def test_no_shortcut_is_bound_twice_with_the_graphics_builder_keymap():
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("ui/prefs/keymap", "builder")
    w = _window()
    try:
        assert w.tool_actions["symbol"].shortcut().toString() == "S"
        assert _collisions(w) == []
    finally:
        _close(w)
        app_settings().remove("ui/prefs/keymap")


def _ctdraw_table(w):
    """CtDraw's key (from its menu captions) -> the ctgedit action."""
    flags = {a.text(): a for a in w.text_flag_actions}
    return {
        "Ctrl+N": w.act_new,
        "Ctrl+O": w.act_open,
        "Ctrl+F4": w.act_close_doc,
        "Ctrl+S": w.act_save,
        "Alt+F10": w.act_compile,
        "F5": w.act_run,
        "Ctrl+P": w.act_print,
        "Ctrl+Z": w.act_undo,
        "Ctrl+Y": w.act_redo,
        "Ctrl+X": w.act_cut,
        "Ctrl+C": w.act_copy,
        "Ctrl+V": w.act_paste,
        "Del": w.act_del,
        "Ctrl+D": w.act_dup,
        "Ctrl+A": w.act_sel_all,
        "Ctrl+F": w.act_find,
        "Ctrl+H": w.act_replace,
        "F6": w.act_paste_symbol,
        "F11": w.act_paste_genie,
        "Ctrl+F11": w.act_paste_super,
        "F8": w.act_snap,
        "F7": w.act_snap_guides,
        "F3": w.act_show_tools,
        "F10": w.act_zoom_window,
        "Ctrl+B": flags["Bold"],
        "Ctrl+I": flags["Italic"],
        "Ctrl+U": flags["Underline"],
        "Ctrl+Shift+P": w.act_adjust,
        "Ctrl+Shift+A": w.act_align_dialog,
        "Ctrl+Shift+R": w.act_rot_cw,
        "Ctrl+Shift+M": w.act_flip_h,
        "Ctrl+Shift+G": w.act_group,
        "Ctrl+Shift+U": w.act_ungroup,
        "Ctrl+G": w.act_goto,
        "F9": w.act_bitmap_editor,
        "F12": w.external_actions[0],
        "Ctrl+T": w.act_var_tags,
    }


# keys the window handles without a QAction of its own: Alt+F4 = the system
# menu's Close, F1 = the help event filter (ctgedit.ui.help)
_HANDLED_ELSEWHERE = {"Alt+F4", "F1"}


def test_every_ctdraw_menu_key_reaches_its_command(bare):
    table = _ctdraw_table(bare)
    for key, action in table.items():
        keys = [s.toString() for s in action.shortcuts()]
        assert key in keys, (key, action.text(), keys)
    assert "Plant SCADA Studio" in table["F12"].text()
    # the menu keys of CtDraw's own menus (docs/ctdraw_inventory.json, read
    # from its resources): each one is in the table
    with open(INVENTORY, encoding="utf-8") as f:
        inv = json.load(f)
    pat = re.compile(r"\((Ctrl\+[^)]+|Alt\+[^)]+|F\d+|Del)\)")
    found = set()
    for m in inv["menus"]["menus"]:
        name = m.get("menu", "")
        if name.startswith(("Bitmap Editor", "Zoom window")):
            continue  # keys of their own windows
        for item in m.get("items", []):
            hit = pat.search(item if isinstance(item, str) else str(item))
            if hit:
                found.add(hit.group(1))
    assert found, "no key read from the inventory"
    assert found - set(table) - _HANDLED_ELSEWHERE == set()


def test_rotate_counter_clockwise_and_flip_vertical_have_keys(bare):
    assert bare.act_rot_ccw.shortcut().toString() == "Ctrl+Shift+L"
    assert bare.act_flip_v.shortcut().toString() == "Ctrl+Shift+V"
    # CtDraw's own keys stay on the direct commands
    assert bare.act_rot_cw.shortcut().toString() == "Ctrl+Shift+R"
    assert bare.act_flip_h.shortcut().toString() == "Ctrl+Shift+M"
    # Ctrl+W still closes the page next to CtDraw's Ctrl+F4
    assert [s.toString() for s in bare.act_close_doc.shortcuts()] == ["Ctrl+W", "Ctrl+F4"]


def test_canvas_keypad_plus_minus_are_node_insert_and_delete(bare):
    """CtDraw's canvas: VK_ADD / VK_INSERT = insert a node, VK_SUBTRACT =
    delete; ctgedit binds keypad minus on the
    canvas to the node-aware delete (keypad plus is read by the canvas) once Settings >
    Keyboard gives Delete the minus keys (not by default)."""
    keys = {sc.key().toString() for sc in bare.view.findChildren(QtGui.QShortcut) if sc.isEnabled()}
    assert "-" not in keys
    from ctgedit.ui.prefs import keymap

    # (Delete's minus keys are not given by default: Settings > Keyboard gives them)
    keymap.store_user_keys({"act_del": ["Del", "-", "Num+-"]})
    keymap.apply_keymap(bare)
    keys = {sc.key().toString() for sc in bare.view.findChildren(QtGui.QShortcut) if sc.isEnabled()}
    assert "Num+-" in keys and "-" in keys


def test_align_dialog_is_in_the_arrange_menu_next_to_the_direct_commands(bare):
    # (QAction.menu() hands the menu to Python in PySide6: find it by title)
    arrange = next(m for m in bare.menuBar().findChildren(QtWidgets.QMenu) if m.title() == "&Arrange")
    acts = arrange.actions()
    assert bare.act_align_dialog in acts
    for a in bare.align_actions:
        assert a in acts  # the six direct commands stay
    assert acts.index(bare.act_align_dialog) == acts.index(bare.act_adjust) + 1


# ------------------------------------------------------------------ Align dialog
def _align_window(win):
    win.act_align_dialog.trigger()
    dlg = win._dialogs.get("align")
    assert dlg is not None and not dlg.isModal() and dlg.isVisible()
    return dlg


def _apply(dlg):
    dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).click()


def test_align_dialog_top_and_centre_in_one_undo_step(win):
    a, b, c = _rects(win, (100, 60, 140, 90), (220, 120, 300, 200), (160, 300, 180, 310))
    doc = win.doc
    s0 = _snap(doc)
    n0 = len(doc._undo)
    boxes = [_box(doc, i) for i in (a, b, c)]
    top = min(bx[1] for bx in boxes)
    cx = (min(bx[0] for bx in boxes) + max(bx[2] for bx in boxes)) // 2
    dlg = _align_window(win)
    assert dlg.vertical() == "" and dlg.horizontal() == ""  # None / None
    assert not dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).isEnabled()
    # a choice changes nothing on the page (modeless: no preview edit)
    dlg.set_choice(vertical="bottom")
    assert _snap(doc) == s0
    dlg.set_choice(vertical="top", horizontal="hcentre")
    _apply(dlg)
    for i in (a, b, c):
        l, t, r, bb = _box(doc, i)
        assert t == top
        assert (l + r) // 2 == cx
    assert len(doc._undo) == n0 + 1  # ONE step
    assert doc._undo[-1].label == "align"
    assert dlg.isVisible()  # Apply keeps it open
    win.undo()
    assert _snap(doc) == s0


def test_align_dialog_cancel_leaves_no_trace(win):
    a, b, c = _rects(win, (100, 60, 140, 90), (220, 120, 300, 200), (160, 300, 180, 310))
    doc = win.doc
    doc.move_objects([c], 5, 0)
    doc.undo()  # a Redo entry to keep
    redo = list(doc._redo)
    dirty = doc.dirty
    s0 = _snap(doc)
    n0 = len(doc._undo)
    dlg = _align_window(win)
    dlg.set_choice(vertical="vcentre", horizontal="right")
    dlg.reject()
    assert "align" not in win._dialogs
    assert _snap(doc) == s0
    assert len(doc._undo) == n0 and doc._redo == redo and doc.dirty == dirty


def test_align_dialog_ok_applies_and_closes(win):
    a, b, c = _rects(win, (100, 60, 140, 90), (220, 120, 300, 200), (160, 300, 180, 310))
    doc = win.doc
    n0 = len(doc._undo)
    dlg = _align_window(win)
    dlg.set_choice(horizontal="left")
    dlg._buttons.button(QtWidgets.QDialogButtonBox.Ok).click()
    assert "align" not in win._dialogs
    assert len(doc._undo) == n0 + 1
    assert len({_box(doc, i)[0] for i in (a, b, c)}) == 1


def test_align_dialog_even_spaces_the_midpoints(win):
    a, b, c = _rects(win, (0, 0, 10, 10), (30, 40, 130, 50), (200, 80, 210, 90))
    doc = win.doc
    dlg = _align_window(win)
    dlg.set_choice(horizontal="even")
    _apply(dlg)
    mids = sorted((_box(doc, i)[0] + _box(doc, i)[2]) / 2 for i in (a, b, c))
    assert mids[1] - mids[0] == pytest.approx(mids[2] - mids[1], abs=1)
    # the outer two stay; the wide one moved by its CENTRE, not its left edge
    assert _box(doc, a)[0] == 0 and _box(doc, c)[0] == 200
    assert _box(doc, b)[0] == 105 - 50


def test_align_dialog_follows_the_selection(win):
    """Apply aligns the objects selected WHEN it is clicked; below two
    objects Apply is off with a note, Even needs three."""
    a, b, c = _rects(win, (100, 60, 140, 90), (220, 120, 300, 200), (160, 300, 180, 310))
    doc = win.doc
    dlg = _align_window(win)
    assert dlg.units() == 3 and dlg._evens[0].isEnabled()
    win._set_selection([a, b])
    assert dlg.units() == 2 and not dlg._evens[0].isEnabled()
    dlg.set_choice(vertical="top")
    _apply(dlg)
    assert _box(doc, a)[1] == _box(doc, b)[1] != _box(doc, c)[1]
    win._set_selection([c])
    assert not dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).isEnabled()
    assert "two or more" in dlg._note.text()


def test_align_dialog_greys_even_for_two_objects():
    _app()
    from ctgedit.ui.dialog.arrange_dialogs import AlignDialog

    dlg = AlignDialog(units=2)
    evens = [b for g in dlg._groups.values() for b in g.buttons() if b.property("mode") == "even"]
    assert len(evens) == 2 and not any(b.isEnabled() for b in evens)
    got = []
    dlg.changed.connect(lambda v, h: got.append((v, h)))
    dlg.set_choice("bottom", "left")
    assert got[-1] == ("bottom", "left")
    three = AlignDialog(units=3)
    assert three._groups["v"].buttons()[3].isEnabled()


def test_align_dialog_has_a_help_topic():
    from ctgedit.ui.help import DIALOG_TOPICS

    assert DIALOG_TOPICS["AlignDialog"].startswith("selecting-and-arranging#")


def test_distribute_uses_midpoints(host):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(host)
    a = doc.create_object("rectangle", rect=(10, 0, 20, 100))
    b = doc.create_object("rectangle", rect=(0, 150, 10, 250))  # tall
    c = doc.create_object("rectangle", rect=(30, 300, 40, 310))
    doc.distribute([a, b, c], "v")
    mids = [(_box(doc, i)[1] + _box(doc, i)[3]) / 2 for i in (a, b, c)]
    assert mids[1] - mids[0] == pytest.approx(mids[2] - mids[1], abs=1)
    assert _box(doc, a)[1] == 0 and _box(doc, c)[1] == 300


def test_distribute_orders_by_the_bounds_midpoints(host):
    """CtDraw orders the units by their BOUNDS
    before it spaces the extent midpoints: a 2-px line and a rectangle with
    the same extent midpoint take their slots by bounds (line 44..56 before
    rectangle 45..56), not by the selection order."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(host)
    a = doc.create_object("rectangle", rect=(0, 0, 10, 20))
    rect = doc.create_object("rectangle", rect=(45, 40, 55, 60))
    line = doc.create_object("line", rect=(45, 80, 55, 80))
    assert doc.set_line_width(line, 2)
    d = doc.create_object("rectangle", rect=(200, 0, 210, 20))
    bnd = {
        i: G.norm_rect(G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect2) for i in (rect, line)
    }
    assert bnd[line][0] + bnd[line][2] < bnd[rect][0] + bnd[rect][2]
    doc.distribute([a, rect, line, d], "h")  # rect selected before line
    # step 200 / 3: the second slot's midpoint 71.67 (+22), the third 138.33 (+88)
    assert _box(doc, line)[0] == 45 + 22
    assert _box(doc, rect)[0] == 45 + 88
    assert _box(doc, a)[0] == 0 and _box(doc, d)[0] == 200


# ------------------------------------------------------------------ Adjust Position and Size
def test_adjust_dialog_values_follow_ctdraw():
    _app()
    from ctgedit.ui.dialog.arrange_dialogs import AdjustPositionDialog

    dlg = AdjustPositionDialog((10, 20, 30, 40))
    assert dlg.rect() == (10, 20, 30, 40)
    assert dlg.by_percentage()  # CtDraw opens By percentage
    assert dlg._sp["pct"].value() == 100
    ok = dlg._buttons.button(QtWidgets.QDialogButtonBox.Ok)
    apply_ = dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply)
    assert ok.isEnabled() and not apply_.isEnabled()  # nothing changed yet
    dlg._sp["pct"].setValue(50)
    assert apply_.isEnabled() and dlg.scale() == (0.5, 0.5)
    assert dlg.rect() == (10, 20, 15, 20)
    dlg.set_by_percentage(False)
    assert not apply_.isEnabled()  # Width / Height unchanged
    dlg._sp["w"].setValue(60)
    sx, sy = dlg.scale()
    assert int(30 * sx) == 60 and sy == 1.0
    # the page limit: X + Width <= 32000
    dlg._sp["x"].setValue(31990)
    assert not ok.isEnabled() and "32000" in dlg._note.text()
    # a selection without a height: Height greyed, sy stays 1
    dlg.set_box((0, 0, 50, 0))
    assert not dlg._sp["h"].isEnabled()
    dlg.set_by_percentage(True)
    dlg._sp["pct"].setValue(200)
    assert dlg.scale() == (2.0, 1.0)
    dlg.set_box(None)
    assert not ok.isEnabled() and not dlg._sp["x"].isEnabled()


def test_adjust_dialog_shows_a_box_past_the_limits_as_it_is():
    """A selection outside CtDraw's -32000..32000 is shown with its real
    values and OK is greyed -- a clamped field would move
    the selection on the next Apply."""
    _app()
    from ctgedit.ui.dialog.arrange_dialogs import AdjustPositionDialog

    dlg = AdjustPositionDialog((-32500, 10, 40000, 20))
    assert dlg.position() == (-32500, 10)
    assert dlg.scale() == (1.0, 1.0)
    assert not dlg._buttons.button(QtWidgets.QDialogButtonBox.Ok).isEnabled()
    assert "-32000" in dlg._note.text()
    dlg.set_box((100, 10, 40000, 20))
    dlg.set_by_percentage(False)
    assert dlg._sp["w"].value() == 40000
    assert "between 1 and 32000" in dlg.problem()


def test_adjust_dialog_is_modeless_and_follows_the_selection(win):
    a, b, c = _rects(win, (100, 100, 140, 120), (200, 100, 260, 140), (100, 200, 120, 260))
    doc = win.doc
    win.act_adjust.trigger()
    dlg = win._dialogs["adjust"]
    assert not dlg.isModal() and dlg.isVisible()
    ext = doc.adjust_extent([a, b, c])
    assert dlg.box() == (ext[0], ext[1], ext[2] - ext[0], ext[3] - ext[1])
    win._set_selection([b])
    bb = _box(doc, b)
    assert dlg.box() == (bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1])
    win._set_selection([])
    assert dlg.box() is None
    win._set_selection([a])
    win._adjust_dialog()  # a second call raises the same window
    assert win._dialogs["adjust"] is dlg
    ba = _box(doc, a)
    assert dlg.box() == (ba[0], ba[1], ba[2] - ba[0], ba[3] - ba[1])


def test_adjust_by_percentage_scales_the_selection_about_its_top_left(win):
    """50 % of three objects: CtDraw scales the SELECTION about the top-left
    of its extent (every object's size and its offset from that corner halve),
    one undo step per Apply; the window then shows the new box."""
    a, b, c = _rects(win, (100, 100, 140, 120), (200, 100, 260, 140), (100, 200, 120, 260))
    doc = win.doc
    s0 = _snap(doc)
    n0 = len(doc._undo)
    before = {i: _box(doc, i) for i in (a, b, c)}
    ox, oy = doc.adjust_extent([a, b, c])[:2]
    win.act_adjust.trigger()
    dlg = win._dialogs["adjust"]
    dlg._sp["pct"].setValue(50)
    dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).click()
    for i, (l, t, r, bb) in before.items():
        want = (
            int(ox + (l - ox) * 0.5),
            int(oy + (t - oy) * 0.5),
            int(ox + (r - ox) * 0.5),
            int(oy + (bb - oy) * 0.5),
        )
        assert _box(doc, i) == want, i
    assert len(doc._undo) == n0 + 1
    ext = doc.adjust_extent([a, b, c])
    assert dlg.box() == (ext[0], ext[1], ext[2] - ext[0], ext[3] - ext[1])
    assert dlg._sp["pct"].value() == 100  # a fresh start after Apply
    assert dlg.isVisible()
    win.undo()
    assert _snap(doc) == s0


def test_adjust_moves_and_resizes_a_multi_selection(win):
    a, b = _rects(win, (100, 100, 140, 120), (200, 150, 260, 190))
    doc = win.doc
    l0, t0, r0, b0 = doc.adjust_extent([a, b])
    win.act_adjust.trigger()
    dlg = win._dialogs["adjust"]
    dlg.set_by_percentage(False)
    dlg._sp["x"].setValue(l0 + 30)
    dlg._sp["y"].setValue(t0 - 20)
    dlg._sp["w"].setValue(2 * (r0 - l0))
    dlg._buttons.button(QtWidgets.QDialogButtonBox.Ok).click()
    assert "adjust" not in win._dialogs  # OK applied and closed
    assert doc.adjust_extent([a, b]) == (l0 + 30, t0 - 20, l0 + 30 + 2 * (r0 - l0), t0 - 20 + (b0 - t0))
    # every object moved with the selection, widths doubled, heights kept
    la, ta, ra, ba = _box(doc, a)
    assert (ra - la, ba - ta) == (80, 20)


def test_adjust_leaves_locked_objects_out(win):
    a, b = _rects(win, (100, 100, 140, 120), (200, 150, 260, 190))
    doc = win.doc
    win._locked.add(a)
    locked_box = _box(doc, a)
    win.act_adjust.trigger()
    dlg = win._dialogs["adjust"]
    bb = _box(doc, b)
    assert dlg.box() == (bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1])
    assert "1 selected object" in dlg._note.text()
    dlg._sp["x"].setValue(bb[0] + 10)
    dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).click()
    assert _box(doc, a) == locked_box
    assert _box(doc, b)[0] == bb[0] + 10


def test_adjust_scales_a_genie_with_its_members(fx_page):
    """FxArrange's FxCarrier (a placed fxgenie.fx_plain, root and members) and the
    rectangle FxSameSize beside it, scaled together to 50 %."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxArrange"))
    s0 = _snap(doc)
    (genie,) = doc.find_objects_by_name("FxCarrier")
    (rect,) = doc.find_objects_by_name("FxSameSize")
    assert doc.page.objects[genie].is_genie and len(doc.subtree(genie)) > 1
    ox, oy, _r, _b = doc.adjust_extent([genie, rect])
    sub = doc.subtree(genie)
    before = {i: _box(doc, i) for i in sub}
    assert doc.adjust_position_and_size([genie, rect], ox, oy, 0.5, 0.5)
    # CtDraw's map about the selection's corner (H = ox - ox *
    # sx), each point baked as (int)(x * sx + H): the genie AND every member
    # land exactly there
    hx, hy = ox - ox * 0.5, oy - oy * 0.5
    for i, (l, t, r, b) in before.items():
        want = (int(l * 0.5 + hx), int(t * 0.5 + hy), int(r * 0.5 + hx), int(b * 0.5 + hy))
        assert _box(doc, i) == want, i
    assert doc._container_fits(genie)  # the members scaled with it
    doc.undo()
    assert _snap(doc) == s0
    # nothing to do: no step
    n = len(doc._undo)
    assert not doc.adjust_position_and_size([genie], *_box(doc, genie)[:2])
    assert len(doc._undo) == n


def test_adjust_bakes_polygon_vertices_like_ctdraw(host):
    """A polygon's vertices take CtDraw's (int)(x * sx + H), not a rounded
    proportional fit into the new box (103 -> 101 at 50 %, not 102)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(host)
    pts = [(100, 300), (103, 300), (110, 310), (100, 310)]
    k = doc.create_object("polygon", nodes=pts)
    r = doc.create_object("rectangle", rect=(100, 300, 141, 321))
    s0 = _snap(doc)
    ox, oy = doc.adjust_extent([k, r])[:2]
    assert doc.adjust_position_and_size([k, r], ox, oy, 0.5, 0.5)
    hx, hy = ox - ox * 0.5, oy - oy * 0.5
    assert G.get_object_nodes(doc.parsed.page.objects[k]) == [
        (int(x * 0.5 + hx), int(y * 0.5 + hy)) for x, y in pts
    ]
    assert _box(doc, r) == (100, 300, 120, 310)
    doc.undo()
    assert _snap(doc) == s0


def test_the_align_window_shows_what_it_aligns_to_and_changes_it(bare):
    """Align to is in the window: it shows the setting, and a choice there is the setting."""
    from ctgedit.ui.prefs.settings import app_settings

    bare._align_to = "page"
    from ctgedit.ui.dialog.arrange_dialogs import AlignDialog

    dlg = AlignDialog(bare, units=3)
    dlg.set_reference("page")
    dlg.referenceChosen.connect(bare._align_to_chosen)
    try:
        assert dlg.align_to.currentData() == "page" and dlg.reference() == "page"
        dlg.align_to.setCurrentIndex(dlg.align_to.findData("last"))
        assert dlg.reference() == "last" and bare._align_to == "last"
        assert app_settings().value("opt/align_to") == "last"
        left = next(b for b in dlg._groups["h"].buttons() if b.property("mode") == "left")
        assert "the object selected last" in left.toolTip()
        even = next(b for b in dlg._groups["h"].buttons() if b.property("mode") == "even")
        assert even.text() == "Even (centres)" and "Space Equally" in even.toolTip()
    finally:
        dlg.deleteLater()
