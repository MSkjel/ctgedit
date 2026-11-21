"""Hidden objects stay hidden across edits."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app

#: the hidden rectangle, another rectangle (before it), an earlier object, a genie
HIDDEN, OTHER, EARLIER, GENIE = "FxPlainRect", "FxSame12", "FxSame05", "FxManyPump1"


def _pump(n=6):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture()
def win(fx_install):
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
    for x in list(MainWindow._instances):
        for e in getattr(x, "_docs", []):
            e["doc"].dirty = False
        if x in MainWindow._instances:
            MainWindow._instances.remove(x)
        x.hide()


@pytest.fixture()
def page(fx_page):
    return fx_page("FxMany")


def _an(doc, an):
    return next(i for i, o in enumerate(doc.page.objects) if o.an == an)


def _ans(path):
    """{name: AN} of the four objects the tests use, as the page stores them."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(path)
    out = {}
    for name in (HIDDEN, OTHER, EARLIER, GENIE):
        (i,) = doc.find_objects_by_name(name)
        out[name] = doc.page.objects[i].an
    assert doc.page.objects[doc.find_objects_by_name(HIDDEN)[0]].kind.name == "RECTANGLE"
    assert doc.find_objects_by_name(EARLIER)[0] < doc.find_objects_by_name(HIDDEN)[0]
    assert doc.page.objects[doc.find_objects_by_name(GENIE)[0]].members
    return out


def _hidden_ans(win):
    return sorted(win.doc.page.objects[i].an for i in win._hidden_roots)


def test_a_delete_before_a_hidden_object_keeps_it_hidden(win, page):
    """Hide the rectangle FxPlainRect, delete FxSame05 (an earlier top-level object): the
    same object stays hidden and the chip's count is the same; its undo and a reorder too."""
    a = _ans(page)
    win.open_document(page)
    doc = win.doc
    i_hid = _an(doc, a[HIDDEN])
    rec = doc.parsed.page.objects[i_hid]
    win._on_hide_toggled(i_hid, True)
    assert win._hidden_chip.text().startswith("1 hidden")
    doc.delete_objects([_an(doc, a[EARLIER])])
    win._refresh()
    assert _hidden_ans(win) == [a[HIDDEN]]
    (now,) = win._hidden_roots
    assert doc.parsed.page.objects[now] is rec  # the same record, by identity
    assert win._hidden_chip.text().startswith("1 hidden")
    # the canvas hides that object, and shows the one that took its old index
    assert not win.scene._items[now].isVisible()
    assert win.scene._items[i_hid].isVisible() if i_hid in win.scene._items else True
    assert win.objects_dock.hidden() == {now}
    win.undo()
    assert _hidden_ans(win) == [a[HIDDEN]] and win._hidden_roots == {i_hid}
    # a reorder (send it to the front) and a paste before it
    doc.reorder([i_hid], "front")
    win._refresh()
    assert _hidden_ans(win) == [a[HIDDEN]]
    assert doc.parsed.page.objects[next(iter(win._hidden_roots))] is rec
    win._on_canvas_selection([_an(doc, a[OTHER])])
    win.view.set_selection(win._sel)
    doc.duplicate_objects(win._sel)
    win._refresh()
    assert _hidden_ans(win) == [a[HIDDEN]]


def test_hidden_objects_are_remembered_by_an_and_ignored_when_gone(win, page):
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    a = _ans(page)
    two = sorted((a[HIDDEN], a[OTHER]))
    win.open_document(page)
    doc = win.doc
    win._on_hide_toggled(_an(doc, a[HIDDEN]), True)
    win._on_hide_toggled(_an(doc, a[OTHER]), True)
    key = win._hidden_key()
    assert key == "browser/hidden_an/%s" % os.path.abspath(page)
    assert sorted(int(x) for x in app_settings().value(key, type=list)) == two
    # an AN that is not on the page any more (and a garbled value) is left out
    app_settings().setValue(key, two + [99999, "x"])
    win.act_close_doc.trigger()
    assert win.doc is None
    other = MainWindow()
    try:
        other.open_document(page)
        assert _hidden_ans(other) == two
        assert not other.scene._items[_an(other.doc, a[HIDDEN])].isVisible()
        assert other._hidden_chip.text().startswith("2 hidden")
        assert other.objects_dock.hidden() == other._hidden_roots
        # Show All forgets them
        other._show_all_roots()
        assert app_settings().value(key) is None
    finally:
        if other.doc is not None:
            other.doc.dirty = False
        MainWindow._instances.remove(other)
        other.hide()


def test_isolate_and_member_hiding_follow_their_objects_and_save_nothing(win, page, tmp_path):
    a = _ans(page)
    win.open_document(page)
    doc = win.doc
    keep = _an(doc, a[OTHER])
    win._on_canvas_selection([keep])
    win.isolate_selection()
    n = len(win._hidden_roots)
    assert keep not in win._hidden_roots and n > 10
    doc.delete_objects([_an(doc, a[EARLIER])])
    win._refresh()
    keep = _an(doc, a[OTHER])
    assert keep not in win._hidden_roots and len(win._hidden_roots) == n - 1
    win._show_all_roots()
    # a genie member: only that member is hidden (scene and tree), by its record
    g = _an(doc, a[GENIE])
    m = doc.page.objects[g].members[0]
    win._on_hide_toggled(m, True)
    assert win._hidden_roots == {m}
    out1 = str(tmp_path / "a.ctg")
    doc.save(out1)
    win._show_all_roots()
    out2 = str(tmp_path / "b.ctg")
    doc.save(out2)
    from ctgedit.format.page import parse_ctg

    a, b = parse_ctg(out1), parse_ctg(out2)
    assert [bytes(p.body_raw) for p in a.page.objects] == [bytes(p.body_raw) for p in b.page.objects]


def test_the_scene_hides_any_index_and_touches_only_what_changes():
    """PageScene.set_hidden: a member index hides its
    subtree only; a second set touches only the items whose state changes."""
    _app()
    from ctgedit.model.document import GraphicObject, ObjectKind, Page
    from ctgedit.render.scene import PageScene

    pg = Page(name="P")
    pg.objects.append(GraphicObject(kind=ObjectKind.GROUP, an=1))
    for k in range(3):
        o = GraphicObject(kind=ObjectKind.RECTANGLE, an=k + 2, parent=0)
        o.geometry.x, o.geometry.y, o.geometry.extent_x, o.geometry.extent_y = 10 * k, 5, 8, 8
        pg.objects.append(o)
        pg.objects[0].members.append(k + 1)
    sc = PageScene()
    sc.set_page(pg)
    sc.set_hidden({2})
    vis = {i: it.isVisible() for i, it in sc._items.items()}
    assert vis.get(2) is False and all(v for i, v in vis.items() if i != 2)
    calls = []
    real = type(sc._items[1]).setVisible
    for i, it in sc._items.items():
        it.setVisible = lambda on, it=it, i=i: (calls.append(i), real(it, on))
    sc.set_hidden({2, 3})
    assert calls == [3]
    sc.set_hidden(set())
    assert all(it.isVisible() for it in sc._items.values())


def test_hide_selection_is_remembered_like_the_eye(win, page):
    """Edit > Hide or Show Selection saves the hidden objects by AN, as the Objects panel's
    eye does; showing them again forgets them."""
    from ctgedit.ui.prefs.settings import app_settings

    a = _ans(page)
    win.open_document(page)
    doc = win.doc
    i_hid = _an(doc, a[HIDDEN])
    win.set_roots_hidden([i_hid], True)
    key = win._hidden_key()
    assert [int(x) for x in app_settings().value(key, type=list)] == [a[HIDDEN]]
    win.set_roots_hidden([i_hid], False)
    assert not app_settings().value(key, type=list)
