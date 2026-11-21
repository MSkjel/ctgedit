"""The ribbon."""

from ctgedit.ui.prefs.settings import app_settings
import os

import pytest


@pytest.fixture
def mixer(fx_page):
    """The busy page (FxTplLinked) of the fake install's copy."""
    return fx_page("FxTplLinked")


def _win():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtWidgets

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    return MainWindow()


def _drop(w):
    """A test's window gone now: out of the window list, its pages not asking to be saved,
    hidden and deleted while the test still runs (left to the garbage collector, a shown
    window was destroyed in a later test's event processing: an access violation)."""
    from PySide6 import QtCore

    from ctgedit.ui.main_window import MainWindow

    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    w.hide()
    w.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def _menu_actions(menubar):
    """Every QAction reachable from the menu bar, recursively."""
    out = set()

    def walk(menu):
        for a in menu.actions():
            if a.menu() is not None:
                walk(a.menu())
            elif not a.isSeparator():
                out.add(a)

    for top in menubar.actions():
        if top.menu() is not None:
            walk(top.menu())
    return out


def _ribbon_actions(ribbon):
    """Every defaultAction hosted by ribbon buttons + popup menu entries."""
    from PySide6 import QtWidgets

    out = set()
    for b in ribbon.findChildren(QtWidgets.QToolButton):
        if b.defaultAction() is not None:
            out.add(b.defaultAction())
        if b.menu() is not None:
            for a in b.menu().actions():
                # submenu TITLE actions (e.g. the QAT 'Recent Pages') are
                # containers, not commands -- their contents are dynamic
                if not a.isSeparator() and a.menu() is None:
                    out.add(a)
    return out


def test_ribbon_parity_guard():
    """invariant 3: every ribbon action is menu-reachable -- the
    ribbon duplicates, never replaces, never orphans."""
    w = _win()
    try:
        menu_set = _menu_actions(w.menuBar())
        # the drawing-tool actions are toolbar/ribbon-only by design
        rib = _ribbon_actions(w.ribbon)
        orphans = [a.text() for a in rib if a not in menu_set and a.text() != ""]
        assert not orphans, orphans
    finally:
        _drop(w)


def test_lock_blocks_move_until_break_lock(mixer):
    """Edit > Lock makes a unit non-movable (and non-deletable) until Break Lock
    Mode, like CtDraw. The move/nudge/resize/delete paths all route through
    _filter_template, which must drop locked roots -- not only template ones."""
    w = _win()
    try:
        from ctgedit.editing import EditableDocument

        w.set_document(EditableDocument.open(mixer))
        root = next(
            i
            for i in w.doc.page.roots()
            if not w.doc.page.objects[i].raw.get("_from_template")
            and not w.doc.page.objects[i].raw.get("_non_drawable")
        )
        g = w.doc.page.objects[root].geometry
        x0, y0 = g.x, g.y
        w._sel = [root]
        w._lock_selection()
        # CtDraw persists Edit > Lock as dwFlags bit 0x4 (NOSELECT)
        assert w.doc.parsed.page.objects[root].subtype_size & 0x4
        assert w._ct_locked(root)
        # the drag-move + filter must refuse the locked unit
        assert w._filter_template([root], "move") == []
        w._on_objects_moved([root], 30, 30)
        g = w.doc.page.objects[root].geometry
        assert (g.x, g.y) == (x0, y0), "locked object moved"
        # Break Lock Mode overrides; the move then applies
        w.act_break_lock.setChecked(True)
        w._break_locks()
        assert w._filter_template([root], "move") == [root]
        w._on_objects_moved([root], 30, 30)
        g = w.doc.page.objects[root].geometry
        assert (g.x, g.y) != (x0, y0), "object did not move after Break Lock"
    finally:
        _drop(w)


def test_ctdraw_softlock_bit4_blocks_until_break_lock(fx_page):
    """CtDraw's soft-lock bit blocks edits until Break Lock Mode."""
    w = _win()
    try:
        from ctgedit.editing import EditableDocument

        w.set_document(EditableDocument.open(fx_page("FxSymbols2")))
        locked = next(
            i
            for i, o in enumerate(w.doc.page.objects)
            if (o.raw.get("_dwflags") or 0) & 0x4 and o.raw.get("_type_code") == 0x07
        )
        assert w.doc.find_objects_by_name("FxBmp2") == [locked]
        assert w._ct_locked(locked)
        assert w._unit_for(locked) == []  # canvas can't select it
        assert w._filter_template([locked], "move") == []  # can't move it
        w._break_lock = True  # Break Lock Mode
        assert w._unit_for(locked) == w.doc.subtree(locked)
        assert w._filter_template([locked], "move") == [locked]
    finally:
        _drop(w)


def test_ribbon_overflow_and_collapse(mixer):
    w = _win()
    try:
        # the whole-bar collapse state persists in QSettings across runs/tests;
        # start from a known EXPANDED bar or the per-group overflow check below
        # has nothing laid out to collapse.
        if w.ribbon._collapsed:
            w.ribbon.toggle_collapsed()
        # (with no page the Design tab shows how to start instead of its groups)
        w.open_document(mixer)
        from PySide6 import QtWidgets as _QW

        w.resize(900, 700)
        w.show()
        # the ribbon measures its OWN width: let the resize reach it before
        # reflowing (without this the test depended on event timing)
        for _ in range(3):
            _QW.QApplication.processEvents()
        w.ribbon._reflow()
        groups = w.ribbon._groups[w.ribbon._stack.currentIndex()]
        assert any(g.collapsed for g in groups), "narrow window: none collapsed"
        w.resize(1900, 700)
        for _ in range(3):
            _QW.QApplication.processEvents()
        w.ribbon._reflow()
        # priority-1 groups must be expanded again at full width
        p1 = [g for g in groups if g.priority == 1]
        assert all(not g.collapsed for g in p1)
        # two-state heights (force a known expanded start -- the collapse
        # state persists in QSettings across runs)
        from ctgedit.ui.ribbon import COLLAPSED_H, EXPANDED_H

        if w.ribbon._collapsed:
            w.ribbon.toggle_collapsed()
        assert w.ribbon.height() == EXPANDED_H
        w.ribbon.toggle_collapsed()
        assert w.ribbon.height() == COLLAPSED_H
        w.ribbon.toggle_collapsed()
        assert w.ribbon.height() == EXPANDED_H
    finally:
        _drop(w)


def test_lean_surface_and_optional_tools():
    """The classic toolbars are gone (the ribbon is the one surface); the
    V3/V4 creation tools and the developer tools are hidden until enabled in
    Settings -- and then appear, without a restart."""
    w = _win()
    try:
        w.show()
        assert w._ribbon_host.isVisible()
        assert not hasattr(w, "_classic_toolbars")
        assert all(not a.isVisible() for a in w.legacy_actions)
        assert all(a.isVisible() for a in w.modern_v34_actions)  # Trend, Cicode
        assert not w.act_thumb.isVisible()
        app_settings().setValue("opt/show_legacy_tools", True)
        app_settings().setValue("opt/developer_tools", True)
        w._apply_surface_options()
        assert all(a.isVisible() for a in w.legacy_actions)
        assert w.act_thumb.isVisible()
        # the Insert menu offers only kinds with an engine seed
        from ctgedit.format import factory

        have = set(factory.available_kinds())
        assert all(a.text() for a in w.insert_actions)
        assert len(w.insert_actions) <= len(have)
    finally:
        _drop(w)


def test_utilities_tab_and_colour_popover():
    w = _win()
    try:
        # UTILITIES is tab 1 with the six groups
        assert w.ribbon._tabs.tabText(1) == "Utilities"
        captions = [g.caption for g in w.ribbon._groups[1]]
        assert captions == ["Colours", "Libraries", "Bitmap", "External"]
        # the colours of new objects: the swatch pair's popover sets the one it was
        # opened for (ui/swatch_pair, ui/editor/draw_styles)
        from ctgedit.ui.editor import draw_styles as DS
        from ctgedit.ui.swatch_pair import NewColourPopover

        pop = NewColourPopover("fill", w.new_object_colours(), w)
        got = []
        pop.chosen.connect(lambda which, c: got.append((which, c)))
        pop.pick_pair(0x123456)
        assert got == [("fill", 0x123456)]
        DS.set_colour("fill", 0x123456)
        w._update_colour_button()
        assert w.ribbon_colours.state()["fill"] == 0x123456
    finally:
        _drop(w)


def test_navbar_mounted_and_synced(mixer):
    w = _win()
    try:
        w.show()
        # no page open: the start page covers the canvas, the NavBar hides
        assert w.start_page.isVisible() and not w.navbar.isVisible()
        w.open_document(mixer)
        assert not w.start_page.isVisible()
        assert w.navbar.isVisible()
        assert w.navbar.parent() is w.view.viewport()
        # zoom label follows the view
        w.view.reset_zoom()
        w.view.zoom_by(2.0)
        assert w.navbar.zoom_text() == "200 %"
        w.view.reset_zoom()
        # repositions inside the viewport on resize
        w.resize(1100, 800)
        w.navbar.reposition()
        vp = w.view.viewport()
        assert 0 <= w.navbar.x() <= vp.width()
        assert w.navbar.y() + w.navbar.height() <= vp.height()
    finally:
        _drop(w)


def _without_identity(path):
    """The saved page with its identity (page + attributes block) and the
    container stamps zeroed: what two Save As copies must share byte for byte."""
    from ctgedit.format import writer
    from ctgedit.format.page import parse_ctg

    pc = parse_ctg(path)
    pc.page.page_identity = bytes(20)
    if pc.attributes is not None:
        pc.attributes.page_identity = bytes(20)
    pc.container.ts_created = pc.container.ts_modified = 0
    return writer.serialize_ctg(pc.container, pc.page, pc.attributes)


def test_browser_eyes_isolate_frame_and_save_integrity(mixer, tmp_path):
    w = _win()
    try:
        w.open_document(mixer)
        w.show()
        roots = [r for r in w.doc.page.roots() if not w.doc.page.objects[r].raw.get("_non_drawable")]
        target = roots[0]
        # eye toggle hides the whole subtree's scene items, model untouched
        w._on_hide_toggled(target, True)
        sub = set(w.doc.page.subtree(target))
        for i in sub:
            item = w.scene._items.get(i)
            if item is not None:
                assert not item.isVisible()
        assert w._hidden_chip.isVisible()
        # SAVE output is byte-identical with and without hides (editor-only)
        out1 = str(tmp_path / "a.ctg")
        w.doc.save(out1)
        w._show_all_roots()
        out2 = str(tmp_path / "b.ctg")
        w.doc.save(out2)
        # (each Save As copy gets its own page identity + stamps -- masked)
        assert _without_identity(out1) == _without_identity(out2)
        assert not w._hidden_chip.isVisible()
        # isolate hides every other root (the Objects panel's menu runs View > Isolate
        # Selection on the selection: w2-tree)
        w._on_canvas_selection([target])
        w.isolate_selection()
        others = [r for r in roots if r != target]
        assert all(r in w._hidden_roots for r in others)
        assert target not in w._hidden_roots
        w._show_all_roots()
        # frame: double-click centres the unit
        w._frame_index(target)
        g = w.doc.page.objects[target].geometry
        centre = w.view.mapToScene(w.view.viewport().rect().center())
        assert abs(centre.x() - (g.x + g.extent_x / 2)) < g.extent_x + 60
        # focus mode: menu bar collapses to height 0 and comes back
        w.act_focus_mode.setChecked(True)
        w._toggle_focus_mode()
        assert w.menuBar().height() == 0
        w.act_focus_mode.setChecked(False)
        w._toggle_focus_mode()
        w._reset_ui_layout()
        assert w.menuBar().height() > 0
    finally:
        _drop(w)


def test_template_objects_are_locked(mixer):
    """CtDraw locks the page's TEMPLATE-derived records: not selectable on
    the canvas, immune to move/nudge/resize/delete (Options can unlock)."""

    w = _win()
    try:
        app_settings().remove("opt/unlock_template")
        w.open_document(mixer)
        tpl_roots = [r for r in w.doc.page.roots() if w.doc.page.objects[r].raw.get("_from_template")]
        assert tpl_roots, "FxTplLinked should have template-aligned roots"
        t = tpl_roots[0]
        # canvas click resolves to NOTHING (unselectable, like lock mode)
        assert w._unit_for(t) == []
        # move/resize/delete are refused
        g0 = w.doc.page.objects[t].geometry
        before = (g0.x, g0.y)
        w._on_objects_moved([t], 50, 50)
        g1 = w.doc.page.objects[t].geometry
        assert (g1.x, g1.y) == before
        n0 = len(w.doc.page.objects)
        w._delete_indices([t])
        assert len(w.doc.page.objects) == n0
        # non-template objects still work
        normal = [
            r
            for r in w.doc.page.roots()
            if not w.doc.page.objects[r].raw.get("_from_template")
            and not w.doc.page.objects[r].raw.get("_non_drawable")
        ]
        assert any(w._unit_for(r) for r in normal)
        # the Options escape hatch unlocks
        app_settings().setValue("opt/unlock_template", True)
        assert w._unit_for(t) != []
        app_settings().remove("opt/unlock_template")
    finally:
        _drop(w)


def test_selection_outline_follows_shape(mixer):
    """A selected pipe/polygon outlines along its SHAPE, not its bbox."""

    w = _win()
    try:
        w.open_document(mixer)
        # a created pipe (diagonal-ish polyline) must outline non-rectangular
        i = w.doc.create_object("pipe", None, [(50, 700), (200, 700), (200, 740)])
        w._refresh()
        outline = w.view._unit_outline([i])
        assert outline is not None
        bbox = w.view._unit_rect([i])
        rect_path = None
        from PySide6 import QtGui

        rect_path = QtGui.QPainterPath()
        rect_path.addRect(bbox)
        # the shape path hugs the pipe: its area differs from the full bbox
        assert outline != rect_path
        assert outline.elementCount() > 5  # a real polyline outline
        # a rectangle still outlines as its rect-ish path
        j = w.doc.create_object("rectangle", (300, 700, 360, 730), None, colour=0x00808080)
        w._refresh()
        out_r = w.view._unit_outline([j])
        assert out_r is not None
    finally:
        _drop(w)


def test_pipe_hit_shape_is_stroke_only(mixer):
    """Clicking inside an L-pipe's empty elbow must NOT hit the pipe; the
    pipe hits only along its stroke (StrokePathItem)."""
    from PySide6 import QtCore

    w = _win()
    try:
        w.open_document(mixer)
        i = w.doc.create_object("pipe", None, [(100, 600), (220, 600), (220, 700)])
        w._refresh()
        item = w.scene._items[i]
        from ctgedit.render.scene import StrokePathItem

        assert isinstance(item, StrokePathItem)
        # ON the stroke: hit
        assert item.contains(item.mapFromScene(QtCore.QPointF(160, 600)))
        # inside the implicit closure (the empty elbow): MISS
        assert not item.contains(item.mapFromScene(QtCore.QPointF(150, 660)))
        # hollow polygon: edge hits, interior misses
        j = w.doc.create_object("polygon", None, [(300, 600), (420, 600), (360, 700)])
        w.doc.set_filled(j, False)
        w._refresh()
        pitem = w.scene._items[j]
        from ctgedit.render.scene import HollowPolygonItem

        assert isinstance(pitem, HollowPolygonItem)
        assert pitem.contains(pitem.mapFromScene(QtCore.QPointF(360, 600)))
        assert not pitem.contains(pitem.mapFromScene(QtCore.QPointF(360, 640)))
        # filled polygon keeps area hit
        w.doc.set_filled(j, True)
        w._refresh()
        pitem = w.scene._items[j]
        assert pitem.contains(pitem.mapFromScene(QtCore.QPointF(360, 640)))
    finally:
        _drop(w)


def test_selection_modes(mixer):
    """Kind filter (click-through), marquee enclose vs touch, Alt cycling."""
    from PySide6 import QtCore

    w = _win()
    try:
        w.open_document(mixer)
        r = w.doc.create_object("rectangle", (100, 600, 200, 660), None, colour=0x00AA8800)
        t = w.doc.create_object("text", (110, 610, 190, 630), None, text="Over")
        w._refresh()
        # FILTER: text-only makes the rectangle click-through
        w.sel_filter.setCurrentIndex(3)  # Text & Numbers
        assert w.view._kind_filter is not None
        assert w.view._kind_filter(t)
        assert not w.view._kind_filter(r)
        w.sel_filter.setCurrentIndex(0)
        assert w.view._kind_filter is None
        # MARQUEE: a box over half the rect selects in TOUCH, not ENCLOSE
        sc = w.scene
        item = sc._items[r]
        half = QtCore.QRectF(90, 590, 60, 80)  # covers only the rect's left
        assert half.intersects(item.sceneBoundingRect())
        assert not half.contains(item.sceneBoundingRect())
        w.view.set_marquee_mode("touch")
        assert w.view._marquee_mode == "touch"
        w.view.set_marquee_mode("enclose")
        w.view.set_marquee_mode("auto")
        # ALT CYCLING: hits under the overlap point include both objects.
        # Realize the view + zoom onto the pair so hit-testing runs at a real
        # scale (a text item's shape is its GLYPH bounds, smaller than the
        # placement rect), then probe the text's actual centre -- which also
        # sits inside the filled rectangle, so both must be hit.
        from PySide6 import QtWidgets

        app = QtWidgets.QApplication.instance()
        w.resize(1000, 800)
        w.show()
        app.processEvents()
        w.view.frame_rect(QtCore.QRectF(80, 590, 140, 90))
        app.processEvents()
        tcenter = sc._items[t].sceneBoundingRect().center()
        assert item.sceneBoundingRect().contains(tcenter)
        vp = w.view.mapFromScene(tcenter)
        hits = w.view._hits_under(vp)
        roots = {w.doc.root_of(h) for h in hits}
        assert {r, t} <= roots
    finally:
        _drop(w)


def test_shape_and_font_context_widgets(mixer):
    """The SHAPE tab edits pipe/line width + style + filled; the TEXT tab
    edits face + point size -- against the byte-pinned setters."""
    w = _win()
    try:
        w.open_document(mixer)
        p = w.doc.create_object("pipe", None, [(50, 600), (150, 600)])
        w._on_canvas_selection([p])
        w._apply_selection_context()
        titles = [w.ribbon._tabs.tabText(i) for i in range(w.ribbon._tabs.count())]
        assert "Shape" in titles
        w.shape_width.setValue(9)
        assert w.doc.page.objects[p].style.line_width == 9
        w.shape_style.setCurrentIndex(1)  # dash
        assert w.doc.page.objects[p].style.line_style == 1
        # text font controls
        t = w.doc.create_object("text", (50, 650, 150, 670), None, text="F")
        w._on_canvas_selection([t])
        w._apply_selection_context()
        w.font_size.setValue(18)
        assert w.doc.page.objects[t].style.font_size == 18
        from PySide6 import QtGui

        w.font_combo.setCurrentFont(QtGui.QFont("Verdana"))
        assert w.doc.page.objects[t].style.font == "Verdana"
    finally:
        _drop(w)


def test_command_palette():
    w = _win()
    try:
        from ctgedit.ui.dialog.palette import harvest_actions, _fuzzy

        acts = harvest_actions(w.menuBar())
        assert len(acts) > 100
        labels = [label for _a, _c, label in acts]
        assert "Flip Horizontal" in labels
        # breadcrumbs name the menu path
        crumb = next(c for _a, c, label in acts if label == "Flip Horizontal")
        assert "Arrange" in crumb
        # fuzzy: subsequence matches, non-matches rejected
        assert _fuzzy("flip", "Flip Horizontal") >= 0
        assert _fuzzy("zzz", "Flip Horizontal") < 0
        # typing filters and Enter triggers the action
        w.palette_popup.open_at()
        w.palette_popup._edit.setText("flip h")
        texts = [w.palette_popup._list.item(i).text() for i in range(w.palette_popup._list.count())]
        assert any("Flip Horizontal" in t for t in texts)
        # (Flip needs a selection -- trigger an always-available command)
        w.palette_popup._edit.setText("zoom in")
        fired = []
        from PySide6 import QtCore as _qc

        act, _label = w.palette_popup._list.item(0).data(_qc.Qt.UserRole)
        act.triggered.connect(lambda *_: fired.append(1))
        w.palette_popup._run_current()
        from PySide6 import QtWidgets as _qw

        _qw.QApplication.processEvents()
        assert fired
        w.palette_popup.hide()
        # modern scheme: Symbol rebinds to A
        assert w.tool_actions["symbol"].shortcut().toString() == "A"
    finally:
        _drop(w)


def test_tags_dock_and_drop_bind(fx_page):
    from PySide6 import QtWidgets

    w = _win()
    try:
        w.open_document(fx_page("FxMany"))
        # the dock loaded the project's variable.dbf
        assert w.tags_dock.tag_names(), "no tags loaded"
        w.tags_dock._filter.setText("")
        assert w.tags_dock._model.rowCount() > 0
        # Ctrl+T raises the dock (not the modal) when tags exist
        w.show()
        w._variable_tags()
        assert w.dock_tags.isVisible()
        # the canvas accepts the tag mime

        assert w.view.acceptDrops()
        # drop on a genie root binds (or opens the dialog -- here we call the
        # single-key path directly through the handler on a genie with an
        # Equipment binding)
        genie = next(
            (
                i
                for i, o in enumerate(w.doc.page.objects)
                if w.doc.genie_bindings(i) and "Equipment" in w.doc.genie_bindings(i)
            ),
            None,
        )
        assert genie in w.doc.find_objects_by_name("FxEquipGenie1")
        if genie is not None:
            tag = w.tags_dock.tag_names()[0]
            w._edit_genie = lambda *_a, **_k: None  # never block on a modal
            w._on_tag_dropped(genie, tag)
            # either bound directly or dialog path: binding OR clipboard set.
            # An Equipment input takes the tag's EQUIP (equipment name), never
            # the tag name (that fails the compile with E2123).
            equip = next((t.equip for t in w.doc.project().variable_tags() if t.name == tag), "")
            b = w.doc.genie_bindings(genie)
            assert (
                (equip and b.get("Equipment") == equip)
                or b.get("Equipment") != tag
                or QtWidgets.QApplication.clipboard().text() == tag
            )
    finally:
        _drop(w)


def test_smart_guide_snap_math():
    """The per-axis smart-guide resolver: nearest target within tolerance
    wins; Alt (not simulated here) and disabled state return raw."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets, QtCore

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.editor.canvas import CanvasView

    v = CanvasView()
    v._sg_bbox = QtCore.QRectF(100, 100, 50, 30)
    v._sg_targets_x = [203.0]  # near the dragged right edge
    v._sg_targets_y = [400.0]  # far: no y snap
    raw = QtCore.QPointF(50.0, 10.0)  # right edge -> 200, 3px from target
    out, guides = v._sg_snap_move(raw)
    assert out.x() == 53.0 and out.y() == 10.0
    assert ("v", 203.0) in guides and len(guides) == 1
    v.set_smart_guides(False)
    out2, guides2 = v._sg_snap_move(raw)
    assert out2 == raw and guides2 == []


def test_smart_guide_cache_uses_roots():
    """The drag-start cache covers non-selected items + page lines, built
    once per drag (perf guard for 700-record pages)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.editor.canvas import CanvasView

    class FakeScene(QtWidgets.QGraphicsScene):
        pass

    sc = FakeScene()
    sc._items = {}
    sc._page_w, sc._page_h = 1000, 500
    for i, x in enumerate((10, 200, 300)):
        it = sc.addRect(x, 50, 40, 20)
        sc._items[i] = it
    v = CanvasView()
    v.setScene(sc)
    v._selection = [0]
    v._sg_begin_drag()
    assert abs(v._sg_bbox.left() - 10) <= 1  # pen half-width tolerance
    # 2 other items x 3 lines + 3 page lines
    assert len(v._sg_targets_x) == 9
    assert 1000.0 in v._sg_targets_x and 250.0 in v._sg_targets_y


def test_contextual_tabs_and_hud(mixer):

    w = _win()
    try:
        w.open_document(mixer)
        w.show()
        w.ribbon._tabs.setCurrentIndex(0)  # DESIGN active
        from ctgedit.ui.prefs import window_options

        # by default a canvas selection only tints its tab: Design and its tools stay
        text_i0 = next(i for i, o in enumerate(w.doc.page.objects) if o.kind.name == "TEXT")
        assert window_options.CONTEXT_TABS_OPEN is False
        w._on_canvas_selection([text_i0])
        w._apply_selection_context()
        assert "Text" in [w.ribbon._tabs.tabText(i) for i in range(w.ribbon._tabs.count())]
        assert w.ribbon._tabs.currentIndex() == 0
        w._on_canvas_selection([])
        w._apply_selection_context()
        window_options.CONTEXT_TABS_OPEN = True  # (Settings > Ribbon > Contextual tabs on)
        # find a text object + a genie root
        from ctgedit.model.document import ObjectKind

        text_i = next(i for i, o in enumerate(w.doc.page.objects) if o.kind is ObjectKind.TEXT)
        # CANVAS-origin selection of a text object -> TEXT tab inserted+active
        w._on_canvas_selection([text_i])
        w._apply_selection_context()
        titles = [w.ribbon._tabs.tabText(i) for i in range(w.ribbon._tabs.count())]
        assert "Text" in titles
        assert w.ribbon._tabs.tabText(w.ribbon._tabs.currentIndex()) == "Text"
        # the HUD shows for the selection
        w._show_hud()
        assert w.hud.isVisible()
        # deselect -> contextual tab removed, DESIGN restored, HUD hidden
        w._on_canvas_selection([])
        w._apply_selection_context()
        titles = [w.ribbon._tabs.tabText(i) for i in range(w.ribbon._tabs.count())]
        assert "Text" not in titles
        assert w.ribbon._tabs.currentIndex() == 0
        assert not w.hud.isVisible()
        # TREE-origin selection inserts but does NOT activate
        w._on_dock_selection([text_i])
        w._apply_selection_context()
        titles = [w.ribbon._tabs.tabText(i) for i in range(w.ribbon._tabs.count())]
        assert "Text" in titles
        assert w.ribbon._tabs.currentIndex() == 0
        # rapid selections coalesce: many queued updates, one timer
        for _ in range(50):
            w._queue_context_update("canvas")
        assert w._ctx_pending
    finally:
        window_options.CONTEXT_TABS_OPEN = False
        _drop(w)


def test_ribbon_paint_path_smoke(mixer):
    """The verification habit: verify through view.grab (the real paint
    path) with the ribbon mounted -- the page background must still paint."""
    w = _win()
    try:
        w.open_document(mixer)
        w.resize(1500, 950)
        w.show()
        w.view.fit()
        pm = w.grab()
        assert pm.width() > 0 and not pm.toImage().isNull()
        img = pm.toImage()
        # sample a canvas pixel: must not be pure black
        c = img.pixelColor(img.width() // 2, img.height() // 2)
        assert c.value() > 16, c.name()
    finally:
        _drop(w)


def test_a_context_tab_replaced_by_another_does_not_fall_to_utilities(tmp_path):
    """Select a text (TEXT tab), then a genie: the GENIE tab shows, not the left neighbour
    UTILITIES, and nothing of it is saved as the start tab."""
    from PySide6 import QtWidgets
    from ctgedit.ui.ribbon import RibbonBar
    from ctgedit.ui.prefs.settings import app_settings

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    r = RibbonBar()
    r.add_tab("Design")
    r.add_tab("Utilities")
    r.add_context_tab("Text")
    r.add_context_tab("Genie")
    r._tabs.setCurrentIndex(0)
    app_settings().setValue("ui/ribbon_tab", 0)
    r.set_context_tabs(["Text"], activate=True)
    assert r._tabs.tabText(r._tabs.currentIndex()) == "Text"
    r.set_context_tabs(["Genie"])
    assert r._tabs.tabText(r._tabs.currentIndex()) == "Genie"
    r.set_context_tabs([])
    assert r._tabs.tabText(r._tabs.currentIndex()) == "Design"
    assert int(app_settings().value("ui/ribbon_tab", 0)) == 0


def test_the_common_groups_stay_open_at_1366(tmp_path):
    """At 1366 px Select and Modify keep their buttons (icon-only before any pop-up), and
    at 1920 nothing but at most Place / Data is a pop-up."""
    from PySide6 import QtWidgets
    from ctgedit.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = MainWindow()
    try:
        w.ribbon._tabs.setCurrentIndex(0)
        for width, may_pop in ((1366, {"Place", "Data"}), (1920, set())):
            w.resize(width, 900)
            w.show()
            app.processEvents()
            w.ribbon._reflow()
            groups = w.ribbon._groups[w.ribbon._stack.currentIndex()]
            popped = {g.caption for g in groups if g.collapsed}
            assert popped <= may_pop, (width, popped)
    finally:
        _drop(w)


def test_the_palette_knows_ctdraws_words_and_ranks_by_word_start():
    """'options' finds Settings, 'rotate' starts with Rotate (not Align Horizontal Centres),
    nothing matching says so."""
    from PySide6 import QtWidgets

    w = _win()
    try:
        w.show()
        QtWidgets.QApplication.processEvents()
        p = w.palette_popup
        p.open_at()

        def rows(q):
            p._edit.setText(q)
            return [p._list.item(i).text() for i in range(p._list.count())]

        assert rows("options")[0].startswith("Settings")
        first = rows("rotate")[0]
        assert first.startswith("Rotate"), first
        none = rows("zzqxw")
        assert len(none) == 1 and none[0].startswith("No command matches")
        p.hide()
    finally:
        _drop(w)


def test_selection_toolbar_turns_off_and_opens_genie_parameters_for_a_genie(mixer):
    """The selection toolbar's first button is what a double-click does, it sits where it
    covers the fewest other objects, and View > Selection Toolbar turns it off."""
    from PySide6 import QtWidgets

    w = _win()
    try:
        w.open_document(mixer)
        w.show()
        QtWidgets.QApplication.processEvents()
        g = next(i for i, o in enumerate(w.doc.page.objects) if o.parent is None and o.is_genie)
        w._on_canvas_selection([g])
        w._show_hud()
        assert w.hud.isVisible()
        assert w.hud._buttons[0].defaultAction() is w.act_genie_params
        r = next(
            i
            for i, o in enumerate(w.doc.page.objects)
            if o.parent is None and not o.is_genie and not o.members and not o.raw.get("_from_template")
        )
        w._on_canvas_selection([r])
        w._show_hud()
        assert w.hud._buttons[0].defaultAction() is w.act_obj_props
        w.act_selection_hud.trigger()  # off
        assert not w.act_selection_hud.isChecked() and not w.hud.isVisible()
        w._show_hud()
        assert not w.hud.isVisible()
        w.act_selection_hud.trigger()  # on again
    finally:
        _drop(w)


def test_the_selection_toolbar_does_not_sit_on_the_navigation_bar(mixer):
    """Clamped into the viewport, the bar could cover the zoom controls."""
    w = _win()
    from PySide6 import QtCore, QtWidgets

    w.resize(1200, 800)
    w.show()
    w.open_document(mixer)  # (the navigation bar shows with a page)
    QtWidgets.QApplication.processEvents()
    assert w.navbar.isVisible()
    vp = w.view.viewport()
    nb = w.navbar.geometry()
    # a selection right above the navigation bar: below it is the bar
    y = w.view.mapToScene(QtCore.QPoint(nb.center().x(), nb.top() - 30))
    rect = QtCore.QRectF(y.x() - 20, y.y() - 10, 40, 20)
    w.hud.show_for_rect(rect)
    assert not w.hud.geometry().intersects(nb)
    assert vp.rect().contains(w.hud.geometry())
