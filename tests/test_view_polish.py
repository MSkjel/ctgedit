"""Grid, guidelines and zoom."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit.format import geometry as G  # noqa: E402
from qtkit import app as _app

#: the page the window tests edit: a copy of FxMain's FxMaster (tests/fixtures/FxInstall: one
#: text at the top left) as FxPolish, 600 x 443 (free room at 100 %; its bottom edge in view)
PAGE_W, PAGE_H = 600, 443

CTRL = QtCore.Qt.ControlModifier
NOMOD = QtCore.Qt.NoModifier
LEFT = QtCore.Qt.LeftButton


def _me(view, kind, sp, buttons=QtCore.Qt.NoButton, mods=NOMOD):
    vp = view.mapFromScene(QtCore.QPointF(*sp))
    g = view.viewport().mapToGlobal(vp)
    return QtGui.QMouseEvent(kind, QtCore.QPointF(vp), QtCore.QPointF(g), LEFT, buttons, mods)


def _press(view, sp, mods=NOMOD):
    view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, sp, LEFT, mods))


def _move(view, sp, mods=NOMOD, buttons=LEFT):
    view.mouseMoveEvent(_me(view, QtCore.QEvent.MouseMove, sp, buttons, mods))


def _release(view, sp, mods=NOMOD):
    view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, sp, QtCore.Qt.NoButton, mods))


def _drag(view, a, b, mods=NOMOD, steps=3):
    _press(view, a, mods)
    for k in range(1, steps + 1):
        t = k / steps
        _move(view, (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), mods)
    _release(view, b, mods)
    QtWidgets.QApplication.processEvents()


def _open_window(path):
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(str(path))
    w.resize(1300, 900)
    w.show()
    w.view.set_smart_guides(False)  # only grid / guideline snapping here
    w.view.reset_zoom()
    w.view.centerOn(300, 220)
    QtWidgets.QApplication.processEvents()
    return w


def _close_window(w):
    from ctgedit.ui.main_window import MainWindow

    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()  # (close() would ask to save the page)


def _small_page(src, dst):
    """``src`` copied to ``dst`` and made PAGE_W x PAGE_H."""
    from ctgedit.editing import EditableDocument

    shutil.copy2(src, dst)
    doc = EditableDocument.open(str(dst))
    doc.set_page_size(PAGE_W, PAGE_H)
    doc.save(str(dst))
    return dst


@pytest.fixture()
def page(tmp_path, fx_page):
    _app()
    return _small_page(fx_page("FxMaster"), tmp_path / "FxPolish.ctg")


@pytest.fixture()
def win(page):
    w = _open_window(page)
    yield w
    _close_window(w)


def _box(po):
    return G.norm_rect(G.parse_geom_header(po.body_raw).rect1)


def _new_rect(w, rect):
    i = w.doc.create_object("rectangle", rect, None)
    w._refresh()
    return i


# ------------------------------------------------------------------ the grid
def test_grid_snap_is_ctdraw_nearest_line_per_axis():
    """Separate pitches from the origin; halfway between two lines takes the
    lower one (the upper line only when strictly nearer)."""
    _app()
    from ctgedit.ui.editor.canvas import CanvasView

    v = CanvasView()
    v.set_grid(10, 4)
    p = v._snap_pt(QtCore.QPointF(23.0, 23.0))
    assert (p.x(), p.y()) == (20.0, 24.0)
    assert v._snap_pt(QtCore.QPointF(15.0, 2.0)).x() == 10.0  # tie -> lower
    v.set_grid(10, 4, origin=(3, 1))
    p = v._snap_pt(QtCore.QPointF(23.0, 23.0))
    assert (p.x(), p.y()) == (23.0, 21.0)
    assert v.grid() == (10, 4, 3, 1)
    assert v.grid_point(31, 30) == (33, 29)
    v.set_grid_size(8)  # a preset: one pitch, origin kept
    assert v.grid() == (8, 8, 3, 1)
    v.set_snap(False)
    assert v._snap_pt(QtCore.QPointF(23.4, 22.6)) == QtCore.QPointF(23.0, 23.0)


def test_moving_an_off_grid_rect_with_snap_lands_on_the_grid(win):
    """Acceptance: an off-grid rect moved with Snap to Grid ends ON the grid
    (its top-left on an intersection), not just moved by a rounded displacement."""
    view = win.view
    view.set_snap(True)
    win._apply_grid(8)
    i = _new_rect(win, (301, 203, 351, 233))
    _drag(view, (326, 218), (326 + 37, 218 + 21))
    l, t, r, b = _box(win.doc.parsed.page.objects[i])
    assert (l % 8, t % 8) == (0, 0)
    assert (r - l, b - t) == (50, 30)  # moved, not resized
    assert (l, t) == (336, 224)  # the nearest intersection
    # separate pitches and an origin: the top-left lands on origin + k * pitch
    win._apply_grid(10, 6, 3, 2)
    _drag(view, (l + 25, t + 15), (l + 25 + 24, t + 15 + 10))  # TL -> (360, 234)
    l2, t2, _r, _b = _box(win.doc.parsed.page.objects[i])
    assert ((l2 - 3) % 10, (t2 - 2) % 6) == (0, 0)
    assert (l2, t2) == (363, 236)
    # Snap to Grid off: the object moves by the displacement as dragged
    view.set_snap(False)
    _drag(view, (l2 + 25, t2 + 15), (l2 + 25 + 5, t2 + 15 + 3))
    assert _box(win.doc.parsed.page.objects[i])[:2] == (l2 + 5, t2 + 3)


def test_drag_preview_shows_the_snapped_position(win):
    view = win.view
    view.set_snap(True)
    win._apply_grid(8)
    i = _new_rect(win, (301, 203, 351, 233))
    _press(view, (326, 218))
    _move(view, (330, 222))
    _move(view, (340, 229))  # +14, +11 dragged
    d = view._move_applied
    assert (301 + d.x(), 203 + d.y()) == (312.0, 216.0)
    _release(view, (340, 229))
    assert _box(win.doc.parsed.page.objects[i])[:2] == (312, 216)


def test_grid_setup_dialog_pitch_x_y_and_origin(win):
    from ctgedit.ui.dialog.dialogs import GridSetupDialog

    dlg = GridSetupDialog(8, True, True, win, pitch_y=4, origin=(2, 3))
    assert dlg.grid() == (8, 4, 2, 3)
    assert dlg.values() == (8, True, True)
    dlg._sp_pitch.setValue(12)
    dlg._sp_pitch_y.setValue(6)
    dlg._sp_origin_x.setValue(5)
    assert dlg.grid() == (12, 6, 5, 3)
    dlg.close()
    # applied: the canvas snaps with it, the scene draws it, no preset checked
    win._apply_grid(12, 6, 5, 3)
    win._refresh()
    assert win.view.grid() == (12, 6, 5, 3)
    sc = win.scene
    assert (sc._grid_size, sc._grid_y, sc._grid_origin) == (12, 6, (5, 3))
    assert not any(a.isChecked() for a in win._grid_actions.values())
    win._set_grid_size(8)
    assert win.view.grid() == (8, 8, 5, 3)
    assert win._grid_actions[8].isChecked()
    # the grid view paints (the grid lines counted from the origin)
    img = win.view.grab().toImage()
    assert not img.isNull()


# ---------------------------------------------------------------- guidelines
def test_moved_object_edge_snaps_to_a_guideline(win):
    """A moved unit's right edge (and centre) snaps to a guideline within
    CtDraw's 8 px catch distance; grid snapping is off here."""
    view = win.view
    view.set_snap(False)
    view.set_guidelines([200], [])
    i = _new_rect(win, (100, 100, 150, 130))
    _drag(view, (125, 115), (125 + 47, 115))  # right edge 150 -> 197
    l, t, r, b = _box(win.doc.parsed.page.objects[i])
    assert r == 200 and l == 150  # snapped 3 px onto the guide
    # the centre snaps too: centre 175 -> 218 (+43) is 18 px from 200: no
    # snap; +27 puts it at 202 (2 px): it lands on 200
    _drag(view, (175, 115), (175 + 27, 115))
    l2, _t, r2, _b = _box(win.doc.parsed.page.objects[i])
    assert (l2 + r2) / 2 == 200


def test_guideline_drag_moves_copies_deletes_and_persists(win, page):
    """Acceptance: a guideline dragged to a new position persists across a
    restart (per page, editor settings only -- the page file is untouched)."""
    from ctgedit.ui.prefs.settings import app_settings

    view = win.view
    view.set_snap(False)
    before = open(page, "rb").read()
    win.view.set_guidelines([200], [150])
    win._save_guidelines()
    msgs = []
    view.guidelinesChanged.connect(msgs.append)
    # the pointer over the guideline offers a drag
    _move(view, (200, 300), buttons=QtCore.Qt.NoButton)
    assert view.viewport().cursor().shape() == QtCore.Qt.SplitHCursor
    _drag(view, (200, 300), (260, 300))
    assert view.guidelines() == ([260], [150])
    assert msgs and "x = 260" in msgs[-1]
    # Ctrl+drag makes a new one, the original stays
    _drag(view, (260, 300), (300, 300), mods=CTRL)
    assert view.guidelines() == ([260, 300], [150])
    # dropped at the page edge (within 8 px): deleted
    _drag(view, (300, 300), (4, 300))
    assert view.guidelines() == ([260], [150])
    _drag(view, (400, 150), (400, PAGE_H - 3))  # 3 px from the bottom edge
    assert view.guidelines() == ([260], [])
    assert "Deleted" in msgs[-1]
    assert open(page, "rb").read() == before  # nothing in the .ctg
    key = win._guidelines_key(win.doc)
    assert "\\" not in key and ":" not in key  # a hashed settings key
    stored = app_settings().value(key, "", type=str)
    assert "260" in stored
    # on disk, where the next run reads it (not only in this process' cache)
    app_settings().sync()
    ini = open(os.environ["CTG_SETTINGS_INI"], encoding="utf-8", errors="replace").read()
    assert key.split("/")[-1] in ini and "260" in ini
    # a restart: the page opens with its guidelines
    _close_window(win)
    w2 = _open_window(page)
    try:
        assert w2.view.guidelines() == ([260], [])
    finally:
        _close_window(w2)


def test_ctrl_at_release_decides_copy_or_move(win):
    """CtDraw's button-up tests MK_CONTROL: Ctrl held when the button is
    RELEASED adds a new guideline, a drag that ends without Ctrl moves it
    (also when Ctrl was held at the press). A copy leaves the original drawn
    during the drag."""
    view = win.view
    view.set_snap(False)
    view.set_guidelines([200], [])
    _press(view, (200, 300), CTRL)
    _move(view, (230, 300), CTRL)
    assert view._guide_drag["copy"] and view._guide_drag["pos"] == 230
    _move(view, (240, 300))  # Ctrl let go
    _release(view, (240, 300))
    assert view.guidelines() == ([240], [])  # moved, not copied
    _press(view, (240, 300))
    _move(view, (280, 300))
    # Ctrl pressed without moving: the drag shows as a copy at once (a Ctrl
    # key event carries the state BEFORE the key; Qt's modifiers() adds it)
    view.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Control, NOMOD))
    assert view._guide_drag["copy"] and "new guideline" in view._pill
    _release(view, (280, 300), CTRL)  # Ctrl pressed at the end
    assert view.guidelines() == ([240, 280], [])
    # a copy dropped on the page edge makes nothing (the original stays)
    _drag(view, (280, 300), (3, 300), mods=CTRL)
    assert view.guidelines() == ([240, 280], [])


def test_click_on_a_guideline_without_drag_selects_the_object_below(win):
    """A guideline is grabbed before the objects under it, but a click that
    does not drag it is a click on the object there; Alt+click reaches the
    object too (depth cycling)."""
    view = win.view
    view.set_snap(False)
    i = _new_rect(win, (300, 200, 400, 260))
    view.set_guidelines([350], [])
    view._set_units([])
    _press(view, (350, 230))
    assert view._guide_drag is not None  # the guideline was grabbed
    _release(view, (350, 230))
    assert view.guidelines() == ([350], [])  # unchanged
    assert i in view.selection_roots()  # ... and the rect selected
    view._set_units([])
    ALT = QtCore.Qt.AltModifier
    _press(view, (350, 230), ALT)
    assert view._guide_drag is None
    _release(view, (350, 230), ALT)
    # (an Alt+click selects on the release: a drag does not change the selection under it)
    assert i in view.selection_roots()


def test_press_on_a_guideline_over_the_selected_object_moves_the_object(win):
    """After a centre snap the guideline runs through the middle of the
    object: a plain press there on the SELECTED object drags the object
    (the guideline stays); with the object not selected the press grabs the
    guideline, and Ctrl still copies the guideline over a selected object."""
    view = win.view
    view.set_snap(False)
    view.set_snap_guides(False)  # only the grab rule here
    i = _new_rect(win, (300, 200, 400, 260))
    view.set_guidelines([350], [])
    view._set_units([[i]])
    _move(view, (350, 230), buttons=QtCore.Qt.NoButton)
    assert view.viewport().cursor().shape() != QtCore.Qt.SplitHCursor
    _drag(view, (350, 230), (370, 240))
    assert _box(win.doc.parsed.page.objects[i])[:2] == (320, 210)
    assert view.guidelines() == ([350], [])
    # Ctrl over the selected object: a new guideline, the object stays put
    _drag(view, (350, 235), (380, 235), mods=CTRL)
    assert view.guidelines() == ([350, 380], [])
    assert _box(win.doc.parsed.page.objects[i])[:2] == (320, 210)
    # not selected: the press grabs the guideline
    view._set_units([])
    _drag(view, (380, 235), (390, 235))
    assert view.guidelines() == ([350, 390], [])
    assert _box(win.doc.parsed.page.objects[i])[:2] == (320, 210)


def test_guideline_nearer_than_a_coarse_grid_point_wins():
    """CtDraw's catch distance with Snap to Grid on is max(8, pitch):
    a guideline nearer than the grid point wins
    even beyond 8 px; with the grid off only within 8 px."""
    _app()
    from ctgedit.ui.editor.canvas import CanvasView

    v = CanvasView()
    v.set_grid(32)
    v.set_snap(True)
    v.set_guidelines([120], [])
    assert v._snap_pt(QtCore.QPointF(110, 0)).x() == 120.0  # grid 96 is 14 away
    assert v._snap_pt(QtCore.QPointF(100, 0)).x() == 96.0  # grid 4 away: grid
    v.set_snap(False)
    assert v._snap_pt(QtCore.QPointF(110, 0)).x() == 110.0  # 10 px: no catch
    assert v._snap_pt(QtCore.QPointF(114, 0)).x() == 120.0  # 6 px: caught


def test_double_click_on_a_guideline_opens_guidelines_setup(win, monkeypatch):
    """CtDraw's canvas opens its 'GuideLines' dialog on a double-click on a
    guideline where no object is (WM_LBUTTONDBLCLK tests the objects first,
    then -> dialog); over an object the double-click opens the
    object, like its click selects it."""
    view = win.view
    i = _new_rect(win, (300, 150, 400, 260))
    view.set_guidelines([], [180])
    activated = []
    view.objectActivated.disconnect()  # (no object sheet here)
    view.objectActivated.connect(activated.append)
    # a spot on the guideline with no selectable object under it
    empty = next(
        x for x in range(420, 1400, 7) if not view._topmost_unit(view.mapFromScene(QtCore.QPointF(x, 180)))
    )
    view.mouseDoubleClickEvent(_me(view, QtCore.QEvent.MouseButtonDblClick, (empty, 180), LEFT))
    dlg = win._dialogs.get("guides")  # modeless Guidelines Setup
    assert dlg is not None and not dlg.isModal()
    assert dlg.values()[1] == [180] and not activated
    dlg.reject()
    # on the guideline over the rect, and off the guideline: the object's
    for sp in ((350, 180), (350, 230)):
        view.mouseDoubleClickEvent(_me(view, QtCore.QEvent.MouseButtonDblClick, sp, LEFT))
    assert win._dialogs.get("guides") is None and activated == [i, i]


def test_smart_guide_in_reach_beats_the_grid():
    """A smart-guide alignment within its reach wins over Snap to Grid (with
    an 8 px grid the grid point is never more than 4 px away, so a 'nearer
    wins' rule meant the guides almost never snapped); a GUIDELINE still
    wins only when it is at least as near as the grid point (CtDraw)."""
    _app()
    from ctgedit.ui.editor.canvas import CanvasView

    v = CanvasView()
    v.set_grid(8)
    v.set_snap(True)
    v.set_smart_guides(True)
    v._sg_bbox = QtCore.QRectF(301, 203, 50, 30)
    v._mv_box = QtCore.QRectF(301, 203, 50, 30)
    v._sg_targets_x, v._sg_targets_y = [310.0], []
    out, lines = v._move_snap(QtCore.QPointF(5, 0))  # left 306: grid 304 (-2)
    assert 301 + out.x() == 310.0 and lines == [("v", 310.0)]  # the alignment (+4) wins
    assert 203 + out.y() == 200.0  # y: no alignment, the grid
    v._sg_targets_x = [400.0]  # out of reach: the grid
    out, lines = v._move_snap(QtCore.QPointF(5, 0))
    assert (301 + out.x(), lines) == (304.0, [])
    v.set_smart_guides(False)  # off: the grid
    v._sg_targets_x = [310.0]
    out, lines = v._move_snap(QtCore.QPointF(5, 0))
    assert (301 + out.x(), lines) == (304.0, [])


def test_smart_guides_align_page_objects_on_their_extents(fx_page):
    """On a page the targets are the top-level objects' Extents, so a drag
    that snaps puts the moved object's edge exactly on the other's edge. On FxShapes
    (one of each primitive)."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.open_document(fx_page("FxShapes"))
        page = w.doc.page
        roots = [
            i
            for i in page.roots()
            if page.objects[i].geometry is not None
            and page.objects[i].geometry.extent_x > 4
            and not w.view._is_template_anchor(i, w.scene._items[i])
            and i in w.scene._items
        ]
        a = roots[-1]
        v = w.view
        v._selection = [a]
        v._units = [[a]]
        v._sg_begin_drag()
        v._mv_box = v._model_box([a])
        g = page.objects[a].geometry
        assert (v._sg_bbox.left(), v._sg_bbox.top()) == (g.x, g.y)
        target = next(t for t in v._sg_targets_x if 5 < abs(t - g.x) < 40)
        raw = QtCore.QPointF(target - g.x + 2.6, 0)  # 2.6 px short of it
        out, lines = v._move_snap(raw)
        vx = [c for a_, c in lines if a_ == "v"]
        assert len(vx) == 1 and vx[0] in v._sg_targets_x
        moved = v._mv_box.translated(out.x(), 0)
        assert vx[0] in (moved.left(), moved.center().x(), moved.right())
        assert abs(out.x() - raw.x()) <= v.SMART_GUIDE_TOL / max(v.zoom(), 0.01)
        # the objects it lines up with are outlined (Settings can turn that off)
        page_lines = (
            {0.0, page.width / 2.0, float(page.width)}
            if hasattr(page, "width")
            else {0.0, w.scene._page_w / 2.0, float(w.scene._page_w)}
        )
        partners = v.smart_guide_partners([("v", vx[0])])
        assert vx[0] in page_lines or partners
        # an object's own edge: outlined
        edge = next(r.left() for r in v._sg_rects if r.left() not in page_lines)
        assert v.smart_guide_partners([("v", edge)])
        assert all(
            any(abs(x - vx[0]) < 0.01 for x in (r.left(), r.center().x(), r.right())) for r in partners
        )
        v._sg_active = [("v", edge)]
        w.resize(900, 600)
        assert not v.grab().isNull()  # the paint path with outlines
        v.SMART_GUIDE_OUTLINE = False
        assert not v.grab().isNull()
        v._sg_active = []
    finally:
        w.close()
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)


def test_guideline_drop_outside_an_element_window_deletes_it():
    """A library element's window has no page edge: a guideline dropped
    outside the visible view is deleted; on a page, 8 px from its edges."""
    _app()
    from ctgedit.render.scene import PageScene
    from ctgedit.ui.editor.canvas import CanvasView

    v = CanvasView()
    sc = PageScene()
    v.setScene(sc)
    v.resize(400, 300)
    sc._page_w, sc._page_h = 800, 600
    assert v._guide_deletes("v", 8) and v._guide_deletes("v", 792)
    assert not v._guide_deletes("v", 9) and not v._guide_deletes("h", 591)
    sc._element_mode = True
    seen = v.mapToScene(v.viewport().rect()).boundingRect()
    assert not v._guide_deletes("v", seen.center().x())
    assert v._guide_deletes("v", seen.right() + 20)
    assert v._guide_deletes("h", seen.top() - 20)


def test_guidelines_are_per_page(tmp_path, page, fx_page):
    other = _small_page(fx_page("FxMaster"), tmp_path / "Other.ctg")
    w = _open_window(page)
    try:
        w.view.set_guidelines([111], [])
        w._save_guidelines()
        w.open_document(str(other))
        assert w.view.guidelines() == ([], [])  # its own (none yet)
        w.view.set_guidelines([], [77])
        w._save_guidelines()
        w.set_document(w._docs[0]["doc"])  # back to the first tab
        assert w.view.guidelines() == ([111], [])
        w.set_document(w._docs[1]["doc"])
        assert w.view.guidelines() == ([], [77])
    finally:
        _close_window(w)


def test_element_save_as_keeps_its_guidelines(fx_install, monkeypatch):
    """A library element's guidelines are kept under library + element; Save
    As under another name takes them along, like a page's Save As. FxLib's
    fxparts.fx_agitator_mixer (in the test's fx_install copy)."""
    _app()
    from ctgedit.ui.dialog import library_dialogs
    from ctgedit.ui.main_window import MainWindow

    lib = fx_install.file("FxLib", "fxparts.ctm")

    class _PickCopy:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return QtWidgets.QDialog.Accepted

        def values(self):
            return lib, "fx_agitator_copy"

    w = MainWindow()
    try:
        w._open_element(lib, "fx_agitator_mixer")
        w.view.set_guidelines([40], [30])
        w._save_guidelines()
        old_key = w._guidelines_key(w.doc)
        monkeypatch.setattr(library_dialogs, "CopyToLibraryDialog", _PickCopy)
        assert w._save_element_as()
        assert w.doc.element_name == "fx_agitator_copy"
        assert w._guidelines_key(w.doc) != old_key
        assert w._load_guidelines(w.doc) == ([40], [30])
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)


def test_guidelines_dialog_first_guideline_turns_display_and_snap_on():
    """CtDraw's Set: the first guideline ticks both boxes;
    positions start at 1 (0 means 'no guideline' to CtDraw)."""
    _app()
    from ctgedit.ui.dialog.dialogs import GuidelinesDialog

    d = GuidelinesDialog([], [], False, False)
    assert d._sp_pos.minimum() == 1
    d._sp_pos.setValue(120)
    d._set_pos()
    v, h, display, snap = d.values()
    assert (v, h, display, snap) == ([120], [], True, True)
    # Clear removes the guideline at the TYPED position (CtDraw's Clear)
    d._sp_pos.setValue(300)
    d._set_pos()
    d._list.setCurrentRow(0)  # the row 120 is picked: fills the box
    assert d._sp_pos.value() == 120
    d._sp_pos.setValue(300)
    d._clear_pos()
    assert d.values()[0] == [120]


# ---------------------------------------------------------------- cross hairs
def test_crosshair_lines_follow_the_pointer_after_a_drag(win):
    """Acceptance: View > Cross Hair Cursor draws full-length lines through
    the pointer, and they (and the cross cursor) still follow it after a move
    drag."""
    view = win.view
    view.set_snap(False)
    assert any(win.act_crosshair in m.actions() for m in win.menuBar().findChildren(QtWidgets.QMenu))
    win.act_crosshair.trigger()
    assert view.crosshair()
    i = _new_rect(win, (100, 100, 150, 130))
    _drag(view, (125, 115), (165, 135))
    assert _box(win.doc.parsed.page.objects[i])[:2] == (140, 120)
    _move(view, (420, 260), buttons=QtCore.Qt.NoButton)
    assert view._cross_pos is not None
    assert (round(view._cross_pos.x()), round(view._cross_pos.y())) == (420, 260)
    assert view.viewport().cursor().shape() == QtCore.Qt.CrossCursor
    # the painted lines: full height through x = 420, full width through y = 260
    img = view.viewport().grab().toImage()
    vp = view.mapFromScene(QtCore.QPointF(420, 260))

    def reddish(x, y):
        # (the theme's accent with a dark edge since the cross hairs follow the theme)
        c = QtGui.QColor(img.pixel(x, y))
        return c.blue() > c.red() + 60

    assert any(reddish(vp.x() + dx, 3) for dx in (-1, 0, 1))  # full height
    assert any(reddish(3, vp.y() + dy) for dy in (-1, 0, 1))  # full width
    assert not reddish(vp.x() + 40, 3)
    # leaving the drawing area hides them
    view.viewportEvent(QtCore.QEvent(QtCore.QEvent.Leave))
    assert view._cross_pos is None
    win.act_crosshair.trigger()
    assert not view.crosshair()
    assert view.viewport().cursor().shape() == QtCore.Qt.ArrowCursor


# ---------------------------------------------------------------- zoom window
def test_zoom_window_zoom_in_out_buttons():
    _app()
    from ctgedit.ui.editor.canvas import CanvasView
    from ctgedit.ui.dialog.dialogs import ZoomWindow

    view = CanvasView()
    z = ZoomWindow(view)
    assert z.factor() == 4 and z.lbl_factor.text() == "4×"
    z.btn_in.click()
    assert z.factor() == 8
    z.zoom_in()
    assert z.factor() == 16 and not z.btn_in.isEnabled()
    z.zoom_in()
    assert z.factor() == 16  # the largest
    for _ in range(4):
        z.btn_out.click() if z.btn_out.isEnabled() else None
    assert z.factor() == 2 and not z.btn_out.isEnabled()
    ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Plus, NOMOD, "+")
    z.keyPressEvent(ev)
    assert z.factor() == 4
    z.zoom_in()
    z.close()
    # the next magnifier (another run) opens at the factor chosen last
    z2 = ZoomWindow(view)
    assert z2.factor() == 8 and z2.lbl_factor.text() == "8×"
    z2.close()


# ------------------------------------------------ View / Window menu commands
def test_status_bar_refresh_toolbox_and_window_menu(win):
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit import editing

    names = {a.text() for m in win.menuBar().findChildren(QtWidgets.QMenu) for a in m.actions()}
    for n in (
        "Show Status Bar",
        "Refresh",
        "Show Tools",
        "Cascade",
        "Tile Horizontally",
        "Tile Vertically",
        "Cross Hair Cursor",
        "Display Guidelines",
    ):
        assert n in names, n
    # Show Status Bar
    assert win.statusBar().isVisible()
    win.act_status_bar.trigger()
    assert not win.statusBar().isVisible()
    win.save_prefs()
    assert app_settings().value("view/status_bar", True, type=bool) is False
    win.act_status_bar.trigger()
    assert win.statusBar().isVisible()
    # Refresh: libraries re-read (a new generation) and the page derived again
    gen, serial = editing._LIBRARY_GENERATION[0], win.doc.derive_serial
    win.act_refresh.trigger()
    assert editing._LIBRARY_GENERATION[0] == gen + 1
    assert win.doc.derive_serial > serial and win.scene._items
    # Show Tools (F3): the floating toolbox hosts the drawing tool actions
    assert win.act_show_tools.shortcut().toString() == "F3"
    win.act_show_tools.trigger()
    tb = win._toolbox
    assert tb.isVisible()
    acts = [b.defaultAction() for b in tb.buttons]
    assert win.tool_actions["rectangle"] in acts and win.tool_actions["select"] in acts
    tb.buttons[acts.index(win.tool_actions["ellipse"])].click()
    assert win.view.tool() == "ellipse"
    win._set_tool("select")
    tb.close()  # its title bar: the action follows
    assert not win.act_show_tools.isChecked()
    # Window > Cascade / Tile
    win.act_cascade.trigger()
    win.act_tile_h.trigger()
    win.act_tile_v.trigger()


def test_view_prefs_round_trip(win):
    from ctgedit.ui.main_window import MainWindow

    win._apply_grid(10, 5, 2, 1)
    win.act_show_guides.setChecked(False)
    win.act_crosshair.setChecked(True)
    win.save_prefs()
    w2 = MainWindow()
    try:
        w2.restore_session(reopen=False)
        assert w2.view.grid() == (10, 5, 2, 1)
        assert w2._grid_spec() == (10, 5, 2, 1)
        assert not w2.view.guides_visible()
        assert w2.view.crosshair()
    finally:
        if w2 in MainWindow._instances:
            MainWindow._instances.remove(w2)
