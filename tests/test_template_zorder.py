"""Page template stacking order."""

import os


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from ctgedit.model.document import (  # noqa: E402
    Geometry,
    GraphicObject,
    ObjectKind as K,
    Page,
    PageAppearance,
    Style,
)
from qtkit import app as _app


# -------------------------------------------------------------- synthetic page
GENIE, BUTTON, PIPE_BARE, PIPE_NODES, SLOT, ARROW, TOUCH, LOOSE, SYM, TPL, PANEL = range(11)


def _o(kind, rect, tc, dw, parent=None, members=(), raw=None, style=None, nodes=None):
    x, y, w, h = rect
    r = {"_type_code": tc, "_dwflags": dw}
    r.update(raw or {})
    return GraphicObject(
        kind=kind,
        geometry=Geometry(x=x, y=y, extent_x=w, extent_y=h, nodes=list(nodes or [])),
        style=style or Style(),
        parent=parent,
        members=list(members),
        raw=r,
    )


def _page():
    """A genie (root 0) under a page template stored LAST (root TPL)."""
    bmp = (2, 2, bytes([255, 0, 0, 255] * 4), True)
    grey = Style(filled=True, fill_colour=0xE5E5E5, line_colour=0xE5E5E5)
    red = Style(filled=True, fill_colour=0xFF0000, line_colour=0xFF0000)
    objs = [
        _o(
            K.GENIE,
            (0, 0, 600, 400),
            0x08,
            0x209,
            members=(BUTTON, PIPE_BARE, PIPE_NODES, SLOT, ARROW, LOOSE),
        ),
        _o(K.RECTANGLE, (10, 10, 80, 30), 0x0F, 0x02, parent=GENIE, style=red),
        _o(K.PIPE, (300, 10, 10, 190), 0x0B, 0x18, parent=GENIE),
        _o(K.PIPE, (330, 10, 10, 190), 0x0B, 0x18, parent=GENIE, nodes=[(335, 10), (335, 200)]),
        _o(K.SYMBOL, (150, 150, 40, 40), 0x07, 0x18, parent=GENIE),
        _o(
            K.GROUP,
            (200, 200, 32, 32),
            0x07,
            0x18,
            parent=GENIE,
            members=(TOUCH,),
            raw={"_embedded_bmp": bmp},
        ),
        _o(K.UNKNOWN, (200, 200, 0, 0), 0x1010, 0x08, parent=ARROW, raw={"_non_drawable": True}),
        _o(K.RECTANGLE, (400, 250, 60, 40), 0x0F, 0x02, parent=GENIE, style=red),
        _o(K.SYMBOL, (700, 500, 40, 40), 0x07, 0x01),  # top level, library missing
        _o(
            K.GENIE,
            (0, 0, 1920, 1080),
            0x08,
            0x20D,
            members=(PANEL,),
            raw={"_from_template": True, "_ref": "fx_sa.normal@-HD1080.<ctt"},
        ),
        _o(K.RECTANGLE, (0, 0, 120, 300), 0x0F, 0x02, parent=TPL, style=grey, raw={"_from_template": True}),
    ]
    objs[TOUCH].parent = ARROW
    return Page(name="synthetic", appearance=PageAppearance(width=1920, height=1080), objects=objs)


def _scene(page):
    _app()
    from ctgedit.render.scene import PageScene

    sc = PageScene()
    sc.set_page(page)
    return sc


def _positions(sc):
    from PySide6 import QtCore

    return {id(it): k for k, it in enumerate(sc.items(QtCore.Qt.AscendingOrder))}


def test_template_stored_last_stacks_above_the_page():
    """CtDraw paints in stream order: the template stored after the genie
    covers it (its panel over the genie's button), every item at z 0."""
    from PySide6 import QtCore

    page = _page()
    sc = _scene(page)
    assert {it.zValue() for it in sc._items.values()} == {0.0}
    pos = _positions(sc)
    assert pos[id(sc._items[PANEL])] > pos[id(sc._items[BUTTON])]
    assert pos[id(sc._items[TPL])] > max(pos[id(sc._items[i])] for i in sc._items if i < TPL)
    top = sc.items(QtCore.QPointF(20, 20))[0]  # over the covered button
    assert top is sc._items[PANEL]


def test_template_stored_first_stays_at_the_bottom():
    """The normal layout (template = record 0) stacks exactly as before."""
    page = _page()
    order = [TPL, PANEL] + [i for i in range(len(page.objects)) if i not in (TPL, PANEL)]
    remap = {old: new for new, old in enumerate(order)}
    objs = []
    for old in order:
        o = page.objects[old]
        o.parent = remap[o.parent] if o.parent is not None else None
        o.members = [remap[m] for m in o.members]
        objs.append(o)
    page.objects = objs
    sc = _scene(page)
    pos = _positions(sc)
    assert max(pos[id(sc._items[i])] for i in (0, 1)) < min(pos[id(sc._items[i])] for i in sc._items if i > 1)


def test_canvas_skips_the_template_root_anchor():
    """With templates unlocked (or Break Lock on) a template stored last must
    not make every click select it: the root's page-size anchor is skipped,
    its members still resolve to the template unit, and a locked template
    lets the click through to the object under it."""
    from PySide6 import QtCore

    page = _page()
    sc = _scene(page)
    from ctgedit.ui.editor.canvas import CanvasView

    view = CanvasView()
    view.setScene(sc)
    view.resize(800, 600)

    def vp(x, y):
        view.centerOn(QtCore.QPointF(x, y))
        return view.mapFromScene(QtCore.QPointF(x, y))

    unit = lambda i: page.subtree(page.root_of(i))  # noqa: E731  (unlocked)
    view.set_unit_resolver(unit)
    hits = view._hits_under(vp(430, 270))  # the loose genie rect
    assert TPL not in hits and hits[0] == LOOSE
    assert view._topmost_unit(vp(430, 270)) == page.subtree(GENIE)
    assert view._hits_under(vp(1500, 900)) == []  # empty page: no template
    assert view._topmost_unit(vp(60, 150)) == page.subtree(TPL)  # its panel
    assert view._hit_index(vp(60, 150))[0] == PANEL
    # locked template: its panel resolves to nothing, the button under it is reached
    view.set_unit_resolver(lambda i: [] if page.root_of(i) == TPL else unit(i))
    assert view._hits_under(vp(20, 20))[:2] == [PANEL, BUTTON]
    assert view._topmost_unit(vp(20, 20)) == page.subtree(GENIE)
    # ... by the hover outline, a right click and a tag drop too (_hit_index):
    # the same object a left click selects, not the locked panel on top
    assert view._hit_index(vp(20, 20))[0] == BUTTON
    assert view._hit_index(vp(60, 150))[0] == GENIE  # the genie box under the panel
    assert view._topmost_unit(vp(60, 150)) == page.subtree(GENIE)
    assert view._hit_index(vp(1500, 900))[0] is None  # empty page: nothing
    from PySide6 import QtGui

    menus = []
    view.contextRequested.connect(lambda _pos, roots: menus.append(list(roots)))
    p = vp(20, 20)
    view.mousePressEvent(
        QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonPress,
            QtCore.QPointF(p),
            QtCore.QPointF(view.viewport().mapToGlobal(p)),
            QtCore.Qt.RightButton,
            QtCore.Qt.RightButton,
            QtCore.Qt.NoModifier,
        )
    )
    assert menus == [[GENIE]]  # the covered genie's menu
    # (the view goes now: left to the collector, this bare CanvasView and its scene in a
    # reference cycle can be freed in the middle of a later test's page read and crash the process)
    import shiboken6

    view.setScene(None)
    shiboken6.delete(view)


def test_bitmap_with_touch_child_paints_its_picture():
    """A tc 7 record with its (own or grafted) DIB that owns a Touch record is
    classified a GROUP by the reader; the scene still paints its picture."""
    from PySide6 import QtWidgets

    sc = _scene(_page())
    assert isinstance(sc._items[ARROW], QtWidgets.QGraphicsPixmapItem)
    assert TOUCH not in sc._items


def test_bitmap_with_touch_child_paints_inside_a_resolved_element():
    """The same rule for a resolved element child (_add_symbol_child)."""
    from PySide6 import QtWidgets

    page = _page()
    arrow = page.objects[ARROW]
    touch = page.objects[TOUCH]
    arrow.members = [1]
    touch.parent = 0
    host = _o(K.SYMBOL, (800, 100, 32, 32), 0x07, 0x01)
    host.children = [arrow, touch]
    sc = _scene(Page(appearance=PageAppearance(width=1920, height=1080), objects=[host]))
    grp = sc._items[0]
    assert any(isinstance(c, QtWidgets.QGraphicsPixmapItem) for c in grp.childItems())


def test_vertexless_artstripped_pipe_draws_nothing():
    """No style block and no vertex array: no rect-diagonal fallback. A pipe
    that has its vertices (its own or grafted) still draws."""
    sc = _scene(_page())
    assert PIPE_BARE not in sc._items
    assert PIPE_NODES in sc._items


def test_artless_genie_member_symbol_draws_no_placeholder():
    """An art-less symbol slot inside a placed genie draws nothing; a top-level
    symbol whose library is missing keeps its placeholder box."""
    sc = _scene(_page())
    assert SLOT not in sc._items
    assert SYM in sc._items


def test_twin_style_counts_as_an_own_style_block():
    """An art-stripped button face draws as its data says: no ctgedit XP edge / r=2 rounding,
    with or without the reader's raw['_twin_style'] (the look came from a styled element twin)."""
    from PySide6 import QtGui, QtWidgets

    page = _page()
    face = page.objects[BUTTON]
    face.raw["_dwflags"] = 0x18  # art-stripped face
    face.style = Style(filled=True, fill_colour=0xBFBFBF, line_colour=0xBFBFBF)
    face.members = [TOUCH]
    page.objects[TOUCH].parent = BUTTON
    page.objects[ARROW].members = []
    sc = _scene(page)
    assert isinstance(sc._items[BUTTON], QtWidgets.QGraphicsRectItem)
    assert sc._items[BUTTON].pen().color() != QtGui.QColor("#808080")
    face.raw["_twin_style"] = True
    sc = _scene(page)
    assert isinstance(sc._items[BUTTON], QtWidgets.QGraphicsRectItem)
    assert sc._items[BUTTON].pen().color() != QtGui.QColor("#808080")


# ------------------------------------------------- a template-first fixture page
def _template_last_doc(fx_page, monkeypatch):
    """FxTplLinked (template record 0, then FxOwnRect and FxTplPump) with its template
    moved to the end of the list."""
    from ctgedit.editing import EditableDocument

    monkeypatch.setenv("CTG_NO_CTF_PRECHECK", "1")
    doc = EditableDocument.open(fx_page("FxTplLinked"))
    tpl_i, _ref = doc.template_reference()
    assert tpl_i == 0
    doc.reorder([tpl_i], "front")  # the template stored LAST
    tpl_i, _ref = doc.template_reference()
    assert tpl_i > 0 and max(doc.subtree(tpl_i)) == len(doc.parsed.page.objects) - 1
    return doc


def test_send_to_back_with_records_before_the_template(fx_page, monkeypatch):
    """CtDraw's Send to Back puts the object at the head of the list; the
    template (stored last) is not moved."""
    doc = _template_last_doc(fx_page, monkeypatch)
    objs = doc.parsed.page.objects
    tpl_i, _ref = doc.template_reference()
    tpl_recs = [objs[i] for i in doc.subtree(tpl_i)]
    r = doc.page.roots()[1]
    head = objs[r]
    n = len(doc.subtree(r))
    doc.reorder([r], "back")
    objs = doc.parsed.page.objects
    assert objs[0] is head
    assert objs[len(objs) - len(tpl_recs) :] == tpl_recs  # the template did not move
    assert doc.template_reference()[0] == len(objs) - len(tpl_recs)
    assert len(doc.subtree(0)) == n


def test_z_steps_keep_the_template_as_a_barrier(fx_page, monkeypatch):
    """Bring Forward / Send Backward never step past the template, wherever it
    is stored (ctgedit's deliberate barrier, test_arrange_correctness pins it
    on Groups); Bring to Front crosses it, and so does Send to Back once
    records precede the template (CtDraw's head-of-list rule)."""
    doc = _template_last_doc(fx_page, monkeypatch)
    objs = doc.parsed.page.objects
    roots = doc.page.roots()
    before = objs[roots[-2]]
    depth = len(doc._undo)
    assert doc.reorder([roots[-2]], "forward") == [roots[-2]]  # held below it
    assert doc.parsed.page.objects[doc.page.roots()[-2]] is before
    assert len(doc._undo) == depth
    doc.reorder([doc.page.roots()[-2]], "front")  # crosses it
    objs = doc.parsed.page.objects
    assert objs[doc.page.roots()[-1]] is before
    tpl_now = doc.template_reference()[0]
    tpl_head = objs[tpl_now]
    doc.reorder([tpl_now], "back")  # template back to record 0
    assert doc.template_reference()[0] == 0
    objs = doc.parsed.page.objects
    assert objs[0] is tpl_head
    second = objs[doc.page.roots()[1]]
    assert doc.reorder([doc.page.roots()[1]], "backward") == [doc.page.roots()[1]]
    assert doc.parsed.page.objects[doc.page.roots()[1]] is second  # held above it
    last = doc.parsed.page.objects[doc.page.roots()[-1]]
    doc.reorder([doc.page.roots()[-1]], "back")  # a FIRST template: above it
    assert doc.parsed.page.objects[len(doc.subtree(0))] is last
    assert doc.template_reference()[0] == 0


def test_scene_patch_equals_full_rebuild_with_the_template_last(fx_page, monkeypatch):
    """update_units re-stacks by record order with every item at z 0: the
    patched scene equals a full rebuild also when the template comes last."""
    _app()
    from PySide6 import QtCore
    from ctgedit.render.scene import PageScene

    doc = _template_last_doc(fx_page, monkeypatch)
    scene = PageScene()
    scene.patch_limit = 1.0
    scene.set_page(doc.page)
    serial = doc.derive_serial
    tpl_i = doc.template_reference()[0]
    tpl = next(i for i in doc.subtree(tpl_i) if i != tpl_i and doc.page.objects[i].kind is K.RECTANGLE)
    assert doc.ensure_property(tpl, "COMMENT", "zorder")
    g = next(i for i in doc.page.roots() if doc.page.objects[i].members and i != tpl_i)
    doc.move_objects([g], 5, 5)
    changes = doc.model_changes_since(serial)
    assert scene.update_units(doc.page, changes)
    fresh = PageScene()
    fresh.set_page(doc.page)

    def stack(sc):
        mine = {id(it): ("item", i) for i, it in sc._items.items()}
        mine.update({id(it): ("ai", i) for i, it in sc._ai_items.items()})
        rows = []
        for it in sc.items(QtCore.Qt.AscendingOrder):
            key = mine.get(id(it))
            if key is not None:
                r = it.sceneBoundingRect()
                rows.append(
                    (
                        key,
                        it.zValue(),
                        round(r.x(), 3),
                        round(r.y(), 3),
                        round(r.width(), 3),
                        round(r.height(), 3),
                    )
                )
        return rows

    assert stack(scene) == stack(fresh)


# ------------------------------------------------------------ a template-last page
def _page_scene(path):
    _app()
    from ctgedit.io.reader import read_page
    from ctgedit.render.scene import PageScene

    page = read_page(path)
    sc = PageScene()
    sc.set_page(page)
    return page, sc


def test_template_last_page_draws_over_the_genie(fx_page):
    """FxTplLast: the page = a genie (record 0) then the template, stored LAST. Every
    template item stacks above every genie item, as in the live Graphics Builder capture
    of the page this rule was found on."""
    page, sc = _page_scene(fx_page("FxTplLast"))
    roots = page.roots()
    tpl = next(r for r in roots if page.objects[r].raw.get("_from_template"))
    assert tpl == roots[-1] and tpl > 0
    pos = _positions(sc)
    genie = [pos[id(it)] for i, it in sc._items.items() if i < tpl]
    tmpl = [pos[id(it)] for i, it in sc._items.items() if i >= tpl]
    assert genie and tmpl and max(genie) < min(tmpl)


def test_template_last_page_bitmaps_with_touch_paint(fx_page):
    """The template's header back / forward arrows and the two red alarm
    diamonds: tc 7 records with a grafted DIB that each own a Touch. FxTplArrows: a
    genie, then fx_alt's arrows@-XGA stored last, whose header holds four 22 x 22
    bitmaps, each with a touch command."""
    from PySide6 import QtWidgets

    page, sc = _page_scene(fx_page("FxTplArrows"))
    roots = page.roots()
    assert page.objects[roots[-1]].raw.get("_from_template") and roots[-1] > 0
    icons = [
        i
        for i, o in enumerate(page.objects)
        if o.raw.get("_type_code") == 0x07 and o.members and o.raw.get("_embedded_bmp") is not None
    ]
    assert len(icons) >= 4
    assert all(page.objects[page.objects[i].members[0]].raw.get("_type_code") == 0x1010 for i in icons)
    assert all(isinstance(sc._items.get(i), QtWidgets.QGraphicsPixmapItem) for i in icons)


def test_artless_genie_symbol_slots_get_no_item(fx_page):
    """No art-less genie symbol slot gets an item. FxParts' genies hold five such slots
    (tc 0x07 members with no picture of their own and no members)."""
    page, sc = _page_scene(fx_page("FxParts"))
    slots = [
        i
        for i, o in enumerate(page.objects)
        if (
            o.raw.get("_type_code") == 0x07
            and o.parent is not None
            and not o.members
            and not o.children
            and o.raw.get("_embedded_bmp") is None
        )
    ]
    assert len(slots) >= 5
    for i in slots:
        assert i not in sc._items, i


def test_vertexless_artstripped_pipes_of_a_page_get_no_item(fx_page):
    """No art-stripped pipe without vertices gets an item. FxTplArrows' genie FxArrowsPipe
    (fxparts.fx_lost_pipe) is older than its element, which lost the pipe: the page's
    art-stripped pipe stores no vertex array and has no twin to take one from."""
    page, sc = _page_scene(fx_page("FxTplArrows"))
    pipes = [
        i
        for i, o in enumerate(page.objects)
        if o.raw.get("_type_code") == 0x0B and not o.geometry.nodes and not (o.raw.get("_dwflags", 0) & 0x2)
    ]
    assert pipes
    for i in pipes:
        assert i not in sc._items, i
