"""The open's fast paths give the same result as the slow ones."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit.model.document import GraphicObject, ObjectKind, Page  # noqa: E402
from qtkit import app as _app


def _page() -> Page:
    """Groups in groups, a hidden record, a locked and a template record, loose objects."""
    page = Page(name="Synthetic")
    objs = page.objects

    def add(kind, parent=None, **raw):
        objs.append(GraphicObject(kind=kind, an=len(objs) + 1, parent=parent, raw=dict(raw)))
        if parent is not None:
            objs[parent].members.append(len(objs) - 1)
        return len(objs) - 1

    g = add(ObjectKind.GROUP)
    for _k in range(5):
        add(ObjectKind.RECTANGLE, g)
    inner = add(ObjectKind.GROUP, g)
    add(ObjectKind.TEXT, inner)
    add(ObjectKind.LINE, inner, _non_drawable=True)
    add(ObjectKind.ELLIPSE, inner, _dwflags=0x4)
    add(ObjectKind.RECTANGLE, g, _from_template=True)
    for _k in range(6):
        add(ObjectKind.POLYGON)
    add(ObjectKind.TEXT, _non_drawable=True)
    return page


def _rows(tree):
    out = []

    def visit(it, depth):
        out.append(
            (
                depth,
                it.text(0),
                it.toolTip(0),
                it.icon(0).cacheKey() != 0,
                it.data(0, QtCore.Qt.UserRole),
                it.checkState(0) if it.data(0, QtCore.Qt.CheckStateRole) is not None else None,
                it.isHidden(),
                it.isExpanded(),
            )
        )
        for k in range(it.childCount()):
            visit(it.child(k), depth + 1)

    root = tree.invisibleRootItem()
    for k in range(root.childCount()):
        visit(root.child(k), 0)
    return out


def _rows_one_by_one(dock, page):
    """The rows as the tree made them before: each row made in the tree, under its parent."""
    from ctgedit.ui.panels.objects_dock import icon

    tree = QtWidgets.QTreeWidget()
    tree.setHeaderHidden(True)

    def add(index, parent):
        obj = page.objects[index]
        item = QtWidgets.QTreeWidgetItem(parent)
        # (the row's look is drawn by the panel's delegate; its tool tip is made when
        # one is asked for, its eye is the delegate's: no check box)
        look = dock._row_look(page, index, obj)
        item.setData(0, QtCore.Qt.UserRole, index)
        item.setText(0, look.text)
        item.setIcon(0, icon(look.icon))
        for m in obj.members:
            if not page.objects[m].raw.get("_non_drawable"):
                add(m, item)
        return item

    for r in page.roots():
        if page.objects[r].raw.get("_non_drawable"):
            continue
        add(r, tree.invisibleRootItem())
    return tree


def test_rows_made_outside_the_tree_equal_rows_made_in_it():
    _app()
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    page = _page()
    dock = ObjectsDock()
    dock.set_page(page, [], hidden={0})
    ref = _rows_one_by_one(dock, page)
    assert _rows(dock._tree) == _rows(ref)
    # the made order (the current row of a selection is the first made)
    order = sorted(dock._rank, key=dock._rank.get)
    assert order == [r[4] for r in _rows(ref)]


def test_a_tab_switch_shows_rows_a_search_hid_again():
    _app()
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    page = _page()
    dock = ObjectsDock()
    dock.set_page(page, [])
    full = _rows(dock._tree)
    dock._search.setText("ellipse")
    dock._search.flush()
    assert any(r[6] for r in _rows(dock._tree))  # rows hidden by the search
    state = dock.detach_rows()
    dock._search.setText("")
    dock._search.flush()
    dock.attach_rows(state, [])
    assert [r[:7] for r in _rows(dock._tree)] == [r[:7] for r in full]
    # rows taken out without a search come back as they were
    state = dock.detach_rows()
    dock.attach_rows(state, [])
    assert [r[:7] for r in _rows(dock._tree)] == [r[:7] for r in full]


def _font_fresh(scene, obj):
    """The font as _object_font made it for every text before."""
    from ctgedit.render import scene as SC

    st = obj.style
    f = QtGui.QFont(st.font or "Arial")
    if obj.raw.get("_font_px"):
        f.setPixelSize(int(obj.raw["_font_px"]))
    elif st.font_size:
        f.setPixelSize(SC._em(st.font_size))
    else:
        f.setPixelSize(SC._em(8))
    f.setBold(st.bold)
    f.setItalic(st.italic)
    f.setUnderline(st.underline)
    f.setStrikeOut(st.strikeout)
    return scene.text_font(f)


def test_text_fonts_are_copies_of_one_font_per_face_and_size():
    _app()
    from ctgedit.render.scene import PageScene

    sc = PageScene()
    cases = []
    for face, size, bold, italic, px in (
        ("Arial", 10, False, False, None),
        ("Arial", 10, True, False, None),
        ("Tahoma", 12, False, True, None),
        ("Arial", 0, False, False, 13),
    ):
        o = GraphicObject(kind=ObjectKind.TEXT, raw={"_font_px": px} if px else {})
        o.style.font, o.style.font_size, o.style.bold, o.style.italic = face, size, bold, italic
        o.geometry.extent_y = 0
        cases.append(o)
    for aliased in (False, True):
        sc.set_aliased_text(aliased)
        for o in cases + cases:
            got, want = sc._object_font(o), _font_fresh(sc, o)
            assert got.toString() == want.toString()
            assert got.resolveMask() == want.resolveMask()
            got.setBold(not got.bold())  # a copy: the next is as made
            assert sc._object_font(o).toString() == want.toString()


def test_pictures_of_one_buffer_share_one_pixmap_in_a_build():
    _app()
    from ctgedit.render.scene import PageScene

    w, h = 4, 3
    rgba = bytes(range(w * h * 4))
    page = Page(name="Pictures")
    for k in range(3):
        o = GraphicObject(
            kind=ObjectKind.SYMBOL, an=k + 1, raw={"_embedded_bmp": (w, h, rgba, True), "_type_code": 0x07}
        )
        o.geometry.x, o.geometry.y, o.geometry.extent_x, o.geometry.extent_y = 10 * k, 0, w, h
        page.objects.append(o)
    sc = PageScene()
    sc.set_page(page)
    items = [sc._items[i] for i in range(3)]
    keys = {it.pixmap().cacheKey() for it in items}
    assert len(keys) == 1  # one pixmap for the buffer
    want = QtGui.QPixmap.fromImage(
        QtGui.QImage(rgba, w, h, w * 4, QtGui.QImage.Format_RGBA8888).copy()
    ).toImage()
    for it in items:
        assert it.pixmap().toImage() == want
    # outside a build each call makes its own
    assert sc._picture_pixmap(rgba, w, h).toImage() == want


# (the window tests open FxGenies of the fake install's copy: any page does, the template
# job and the genie step are stand-ins; the Library panel lists FxMain's include chain)


def _window():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1400, 900)
    return w


def _close(w):
    from ctgedit.ui.main_window import MainWindow

    for e in w._docs:
        e["doc"].dirty = False
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()


def test_a_template_landing_and_a_remembered_genie_answer_show_with_one_refresh(fx_page, monkeypatch):
    w = _window()
    try:
        w.open_document(fx_page("FxGenies"))
        builds = []
        real = w.props.set_target
        monkeypatch.setattr(w.props, "set_target", lambda *a, **k: (builds.append(1), real(*a, **k)))

        def genie_step(doc, first=False):
            w._refresh()  # (a remembered genie answer landing at once)
            return ["lib.genie"]

        monkeypatch.setattr(w, "_update_stale_genies", genie_step)

        class TemplateJob:
            template = True
            bindings = None

        w._genie_job_finished(w.doc, ["tpl"], 1, TemplateJob())
        assert len(builds) == 1
        assert w.__dict__.get("_refresh_hold") is None
    finally:
        _close(w)


def test_a_page_shown_first_in_an_event_loop_has_its_chips_after_its_paint(fx_page, monkeypatch):
    w = _window()
    try:
        monkeypatch.setattr(type(w), "DEFER_POST_OPEN", True)
        calls = []
        real = w._update_diag_chips
        monkeypatch.setattr(w, "_update_diag_chips", lambda: (calls.append(1), real()))
        w.open_document(fx_page("FxGenies"))
        assert not calls  # (the page shows first)
        app = _app()
        for _k in range(5):
            app.processEvents()
        assert calls
        from ctgedit.diag import open_report as OR

        assert w._diag_report_current(w.doc) and OR.report_for(w.doc) is w.doc.ui.diag_report
    finally:
        _close(w)


def _lists(p):
    """What the Library panel lists: its kind's lists and its grid rows."""
    d = p._data[p._kind]
    return (
        [(pr.key, li.path) for pr, li in d["libs"]],
        [(e.key, e.shadowed_by) for e in d["entries"]],
        d["preps"],
        d["libkey"],
        d["libpos"],
        d["nat"],
        sorted(d["ref"].items()),
        sorted(d["lib_count"].items()),
        [p._model.data(p._model.index(r, 0)) for r in range(p._model.rowCount())],
    )


def test_the_library_panel_built_in_an_event_loop_lists_what_a_direct_build_does(fx_project, monkeypatch):
    """FxMain of the fake install: its include chain (FxLib, FxStyle, Include) holds the
    symbol libraries the panel lists."""
    import threading

    example = fx_project("FxMain")
    _app()
    from ctgedit.ui.panels.library_panel import LibraryPanel

    want = LibraryPanel()
    want.resize(320, 800)
    want.set_project_dir(example)
    want.ensure_loaded()  # (no event loop: built at once)
    assert _lists(want)[0] and _lists(want)[1]  # (libraries and entries listed)
    made_on = []
    real = LibraryPanel._compute_kind_data

    def compute(chain, kind):
        made_on.append(threading.current_thread() is threading.main_thread())
        return real(chain, kind)

    monkeypatch.setattr(LibraryPanel, "_compute_kind_data", staticmethod(compute))
    got = LibraryPanel()
    got.resize(320, 800)
    got.show()
    loop = QtCore.QEventLoop()
    seen = {}

    def start():
        got.set_project_dir(example)  # (the deferred build: the warm-up thread)
        poll()

    def poll():
        if got._warm is not None:
            seen["warm"] = True
        if not got._dirty and seen.get("warm"):
            loop.quit()
        else:
            QtCore.QTimer.singleShot(10, poll)

    QtCore.QTimer.singleShot(0, start)
    QtCore.QTimer.singleShot(20000, loop.quit)
    loop.exec()
    assert seen.get("warm") and not got._dirty
    assert made_on == [False]  # (made on the thread, not again here)
    assert _lists(got) == _lists(want)
    assert got._loaded_stamp == want._loaded_stamp
