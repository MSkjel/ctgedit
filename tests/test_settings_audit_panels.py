"""Panel settings."""

import gc
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from qtkit import app as _app

#: the pages the tests open (FxMain's)
GENIES, MANY, SECOND = "FxGenies", "FxMany", "FxShapes"

LEFT = QtCore.Qt.LeftButton
NOMOD = QtCore.Qt.NoModifier
EDITING = QtWidgets.QAbstractItemView.EditingState

#: the keys of this package: (Settings page, the key)
KEYS = (
    ("Editing", "opt/scrub_speed"),
    ("Editing", "opt/tag_suggest"),
    ("Editing", "objprops/open_tab"),
    ("Editing", "draw/style_preview"),
    ("Appearance", "objects/rename_click"),
    ("Appearance", "objects/double_click"),
    ("Appearance", "objects/follow_selection"),
    ("Appearance", "objects/keep_filter"),
    ("Pages and libraries", "library/hover_preview"),
    ("Pages and libraries", "browser/fuzzy"),
    ("Pages and libraries", "browser/short_names"),
    ("Pages and libraries", "browser/preview_pane"),
    ("Pages and libraries", "browser/preview_live"),
)


def _pump(n=6):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture(autouse=True, scope="module")
def _collect_at_the_end():
    """The windows these tests made are deleted and collected before the next module (a
    late collection on the GUI thread holds up the next module's timers)."""
    yield
    for _ in range(2):
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        _pump(3)
        gc.collect()


@pytest.fixture(autouse=True)
def _defaults():
    """The panels' module values back at their defaults around each test (a window reads
    them from the test's own settings when it is made)."""
    from ctgedit.ui.panels import panel_options

    panel_options.reset()
    yield
    panel_options.reset()


def _close(w):
    from ctgedit.ui.main_window import MainWindow

    for d in list(getattr(w, "_dialogs", {}).values()):
        try:
            d.close()
        except RuntimeError:
            pass
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()


def _window():
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
    return w


@pytest.fixture()
def win():
    w = _window()
    yield w
    _close(w)


def _page(win, fx_page, page=GENIES):
    """FxMain's ``page`` (of the test's fx_install copy) open in ``win``."""
    win.open_document(fx_page(page))
    win.show()
    _pump()
    return win.doc


def _settings(win, values: dict) -> None:
    """Change rows of the Settings window and apply them, as its Apply does."""
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    d = SettingsDialog(win)
    try:
        for key, value in values.items():
            assert key in d._row_widgets, key
            d._set_row_value(key, value)
            assert d._row_value(key) == value, key
        d._apply(accept=False)
    finally:
        d.close()
        d.deleteLater()
    _pump(2)


def _idx(doc, name):
    hits = doc.find_objects_by_name(name)
    assert len(hits) == 1, (name, hits)
    return hits[0]


def _select(win, ix):
    """The canvas's selection (as a click on the page makes it)."""
    win._on_canvas_selection(list(ix))
    win.view.set_selection(list(ix))
    _pump(3)


def _rect(win, colour=0x808080, box=(300, 100, 380, 160)):
    i = win.doc.create_object("rectangle", box, None, colour=colour)
    win._refresh()
    _pump(2)
    return i


def _mouse(widget, kind, pos, mods=NOMOD):
    t = {
        "press": QtCore.QEvent.MouseButtonPress,
        "move": QtCore.QEvent.MouseMove,
        "release": QtCore.QEvent.MouseButtonRelease,
        "double": QtCore.QEvent.MouseButtonDblClick,
    }[kind]
    buttons = QtCore.Qt.NoButton if kind == "release" else LEFT
    p = QtCore.QPointF(pos)
    ev = QtGui.QMouseEvent(
        t,
        p,
        QtCore.QPointF(widget.mapToGlobal(pos)),
        QtCore.Qt.NoButton if kind == "move" else LEFT,
        buttons,
        mods,
    )
    QtWidgets.QApplication.sendEvent(widget, ev)


def _double_click(widget, pos):
    """A double-click as Qt delivers one: press, release, press, double-click, release."""
    for kind in ("press", "release", "press", "double", "release"):
        _mouse(widget, kind, pos)
    _pump(3)


# ------------------------------------------------------------------ the Settings window
def test_one_row_each_on_its_page_at_the_default(win):
    """Each setting has one row, on its page, showing the declared default; Label drag speed
    is greyed while labels cannot be dragged."""
    from ctgedit.ui.panels import panel_options as PO
    from ctgedit.ui.prefs.settings import SETTINGS_KEYS
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    d = SettingsDialog(win)
    try:
        rows = [r.key for r in d._tabs.rows()]
        for page, key in KEYS:
            assert rows.count(key) == 1, key
            on_page = {r.key for r in d._tabs.widget(d._tabs.indexOf(page)).rows()}
            assert key in on_page, (page, key)
            assert d._row_value(key) == SETTINGS_KEYS[key], key
        speed = d._row_widgets["opt/scrub_speed"]
        label = next(r for r in d._tabs.rows() if r.key == "opt/scrub_speed").label
        assert speed.isEnabled() and label.isEnabled()
        d._flags["props_scrub"].setChecked(False)
        assert not speed.isEnabled() and not label.isEnabled()  # (the label greys too)
        d._flags["props_scrub"].setChecked(True)
        assert speed.isEnabled() and label.isEnabled()
        # the tip of Double-click a row names the choices it is about
        tip = d._row_widgets["objects/double_click"].toolTip()
        assert "Opens its properties:" in tip and "Scrolls to it:" in tip
    finally:
        d.close()
        d.deleteLater()
    # (what a window made with fresh settings reads)
    assert (
        PO.SCRUB_RATE,
        PO.TAG_SUGGEST_AFTER,
        PO.OPEN_TAB,
        PO.STYLE_PREVIEW,
        PO.LIBRARY_PREVIEW,
        PO.SHORT_NAMES,
        PO.PREVIEW_PANE,
        PO.PREVIEW_LIVE,
    ) == (0.5, 2, "first", True, "normal", True, "auto", "thumbnail")


def test_an_import_applies_the_choices_at_once(win, tmp_path):
    """Settings > Import: the panels follow the imported choices before a new window."""
    from ctgedit.ui.panels import panel_options as PO
    from ctgedit.ui.browser import search
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.prefs.settings_transfer import export_groups

    path = str(tmp_path / "panels.ini")
    export_groups(
        path,
        overrides={
            "opt/scrub_speed": "fast",
            "opt/tag_suggest": 3,
            "browser/fuzzy": False,
            "objects/double_click": "props",
        },
    )
    assert (PO.SCRUB_RATE, PO.TAG_SUGGEST_AFTER, search.FUZZY) == (0.5, 2, True)
    assert win.objects_dock.double_click == "zoom"
    d = SettingsDialog(win)
    try:
        assert d.import_from(path)
    finally:
        d.close()
        d.deleteLater()
    _pump(2)
    assert str(app_settings().value("opt/scrub_speed")) == "fast"
    assert (PO.SCRUB_RATE, PO.TAG_SUGGEST_AFTER, search.FUZZY) == (1.0, 3, False)
    assert win.objects_dock.double_click == "props"  # (a window: method too)


# ------------------------------------------------------------------ Label drag speed
def _x_label(win):
    from ctgedit.ui.number_field import ScrubLabel

    labs = [
        lab
        for lab in win.props.findChildren(ScrubLabel)
        if lab.field.property("propKey") == "x" and lab.isVisible()
    ]
    assert labs, "no X label in the Properties panel"
    return labs[0]


def _drag_label(win, dx, mods=NOMOD) -> int:
    """Drag the Properties panel's X label ``dx`` px sideways; how much X changed."""
    lab = _x_label(win)
    before = lab.field.value()
    y = lab.height() // 2
    _mouse(lab, "press", QtCore.QPoint(2, y), mods)
    _mouse(lab, "move", QtCore.QPoint(2 + dx, y), mods)
    moved = lab.field.value() - before
    _mouse(lab, "release", QtCore.QPoint(2 + dx, y), mods)
    _pump(3)
    return moved


def test_label_drag_speed(win, fx_page):
    _page(win, fx_page)
    i = _rect(win)
    _select(win, [i])
    n = len(win.doc._undo)
    assert _drag_label(win, 40) == 20  # Normal: 1 per 2 px
    assert len(win.doc._undo) == n + 1  # (the release applies it once)
    assert _drag_label(win, 40, QtCore.Qt.ShiftModifier) == 200
    assert _drag_label(win, 40, QtCore.Qt.AltModifier) == 5
    _settings(win, {"opt/scrub_speed": "fast"})
    assert _drag_label(win, 40) == 40  # 1 per px
    assert _drag_label(win, 40, QtCore.Qt.ShiftModifier) == 400
    assert _drag_label(win, 40, QtCore.Qt.AltModifier) == 10
    _settings(win, {"opt/scrub_speed": "slow"})
    assert _drag_label(win, 40) == 10  # 1 per 4 px


# ------------------------------------------------------------------ the Objects panel
def _row_rect(dock, idx):
    tree = dock._tree
    item = dock._items[idx]
    tree.scrollToItem(item)
    _pump(2)
    return tree.visualRect(tree.indexFromItem(item, 0))


def _text_pos(dock, idx):
    look = dock._looks[idx]
    return dock._delegate.zones(_row_rect(dock, idx), idx, look.flags)["text"].center()


def _end_edit(tree):
    """Esc in the name editor that is open (the window may not be the active one)."""
    if tree.state() == EDITING:
        eds = [e for e in tree.viewport().findChildren(QtWidgets.QLineEdit) if e.isVisible()]
        assert eds, "no name editor"
        QTest.keyClick(eds[0], QtCore.Qt.Key_Escape)
        _pump(2)
        assert tree.state() != EDITING


def test_a_second_click_on_a_selected_row_renames_it_unless_settings_say_no(win, fx_page):
    from ctgedit.ui.panels.objects_dock import SLOW_CLICK_MS

    doc = _page(win, fx_page, MANY)
    dock, tree = win.objects_dock, win.objects_dock._tree
    r = _idx(doc, "FxPlainRect")  # a plain rectangle
    _select(win, [r])
    QTest.mouseClick(tree.viewport(), LEFT, NOMOD, _text_pos(dock, r))
    # (the rename starts SLOW_CLICK_MS after the click: waited for, not slept, so a busy
    # machine that fires the timer late does not fail it)
    deadline = time.monotonic() + 5
    while tree.state() != EDITING and time.monotonic() < deadline:
        QTest.qWait(50)
    assert tree.state() == EDITING  # (the default: it renames)
    _end_edit(tree)
    _settings(win, {"objects/rename_click": False})
    _select(win, [r])
    QTest.mouseClick(tree.viewport(), LEFT, NOMOD, _text_pos(dock, r))
    QTest.qWait(SLOW_CLICK_MS + 200)
    assert tree.state() != EDITING
    tree.setFocus()
    QTest.keyClick(tree, QtCore.Qt.Key_F2)  # F2 always renames
    assert tree.state() == EDITING
    _end_edit(tree)


def _shown(win, idx) -> bool:
    """Object ``idx`` lies in the canvas's viewport."""
    l, t, r, b = win.doc.selection_extent([idx], bounds=True)
    box = win.view.mapFromScene(QtCore.QRectF(l, t, r - l, b - t)).boundingRect()
    return win.view.viewport().rect().intersects(box)


def test_double_click_a_row(win, fx_page):
    from ctgedit.ui.panels.objects_dock import SLOW_CLICK_MS

    doc = _page(win, fx_page, MANY)
    dock, tree = win.objects_dock, win.objects_dock._tree
    r = _idx(doc, "FxPlainRect")  # a plain rectangle
    view = win.view

    def away():
        # (far from the object, zoomed out: framing it zooms in)
        view.frame_rect(QtCore.QRectF(6000, 6000, 3000, 3000))
        _pump(2)
        assert not _shown(win, r)
        return view.transform().m11()

    # Zooms to the object (the default)
    zoom = away()
    _double_click(tree.viewport(), _text_pos(dock, r))
    assert _shown(win, r) and view.transform().m11() > zoom
    QTest.qWait(SLOW_CLICK_MS + 200)
    assert tree.state() != EDITING  # (a double-click is no slow click)
    # Scrolls to it: at the zoom it had
    _settings(win, {"objects/double_click": "scroll"})
    zoom = away()
    _double_click(tree.viewport(), _text_pos(dock, r))
    assert _shown(win, r) and view.transform().m11() == pytest.approx(zoom)
    assert win._modeless_for("objprops", r) is None and tree.state() != EDITING
    # Opens its properties, as Enter does
    _settings(win, {"objects/double_click": "props"})
    _double_click(tree.viewport(), _text_pos(dock, r))
    sheet = win._modeless_for("objprops", r)
    assert sheet is not None and sheet.isVisible()
    sheet.close()
    _pump(2)
    # Renames it
    _settings(win, {"objects/double_click": "rename"})
    _double_click(tree.viewport(), _text_pos(dock, r))
    assert tree.state() == EDITING
    _end_edit(tree)


def test_the_panel_shows_a_selection_made_elsewhere_unless_settings_say_no(win, fx_page):
    """Tools > Goto Object picks a genie's member (the tree opens the genie and scrolls to
    it); a click on the page selects an object far down the list (the tree scrolls). Off:
    the rows are selected, the tree stays as it was. On FxMany (more than 50 roots)."""
    doc = _page(win, fx_page, MANY)
    dock, tree = win.objects_dock, win.objects_dock._tree
    g = _idx(doc, "FxEquipGenie2")  # the last genie of the page
    m = doc.page.objects[g].members[0]
    bar = tree.verticalScrollBar()

    def reset():
        _select(win, [])
        dock._items[g].setExpanded(False)
        tree.scrollToTop()
        _pump(2)
        assert bar.maximum() > 0 and bar.value() == 0

    def row_visible(i):
        item = dock._items[i]
        rect = tree.visualItemRect(item)
        return not rect.isEmpty() and tree.viewport().rect().intersects(rect)

    reset()
    win._goto_preview(m, centre=False)
    _pump(3)
    assert dock._items[g].isExpanded() and row_visible(m) and bar.value() > 0
    # a click on the page (the genie's own centre)
    reset()
    l, t, r, b = doc.selection_extent([g], bounds=True)
    win.view.pan_to(QtCore.QPointF((l + r) / 2, (t + b) / 2))
    _pump(2)
    pos = win.view.mapFromScene(QtCore.QPointF((l + r) / 2, (t + b) / 2))
    QTest.mouseClick(win.view.viewport(), LEFT, NOMOD, pos)
    _pump(3)
    assert win._sel == [g] and row_visible(g) and bar.value() > 0
    _settings(win, {"objects/follow_selection": False})
    reset()
    win._goto_preview(m, centre=False)
    _pump(3)
    assert win._sel == [m] and dock._items[m].isSelected()
    assert not dock._items[g].isExpanded() and bar.value() == 0
    reset()
    QTest.mouseClick(win.view.viewport(), LEFT, NOMOD, pos)
    _pump(3)
    assert win._sel == [g] and dock._items[g].isSelected() and bar.value() == 0
    # an edit on the page keeps the tree where it is too
    win.doc.move_objects([g], 4, 0)
    win._after_edit()
    _pump(3)
    assert bar.value() == 0
    # and a page tab switch and back: the genie of the member selected stays closed
    reset()
    win._goto_preview(m, centre=False)
    _pump(3)
    assert win._sel == [m] and not dock._items[g].isExpanded()
    _page(win, fx_page, SECOND)
    assert win.doc is not doc
    win.show_tab(next(k for k, e in enumerate(win._docs) if e["doc"] is doc))
    _pump(3)
    assert win.doc is doc and win._sel == [m] and dock._items[m].isSelected()
    assert not dock._items[g].isExpanded()


def test_the_filter_comes_back_at_a_start_unless_settings_say_no(win, fx_page):
    _page(win, fx_page)
    dock = win.objects_dock
    dock.open_filter()
    _pump(2)
    dock._popover.boxes["kind:genie"].click()  # the funnel's Genies
    dock._popover.close()
    _pump(2)
    assert dock.filters() == ["kind:genie"]
    again = _window()  # ctgedit starts again
    try:
        assert again.objects_dock.filters() == ["kind:genie"]
    finally:
        _close(again)
    _settings(win, {"objects/keep_filter": False})
    assert dock.filters() == ["kind:genie"]  # (this session keeps it)
    again = _window()
    try:
        assert again.objects_dock.filters() == []
        assert not again.objects_dock._active()
    finally:
        _close(again)


# ------------------------------------------------------------------ the Library panel
def _library(win, fx_project):
    p = win.library_dock
    p.set_project_dir(fx_project("FxMain"))
    p.ensure_loaded()
    p.set_kind("symbol")
    win.show()
    p.show()
    _pump(3)
    return p


def _point_at_card(p, e):
    grid = p._grid
    grid.doItemsLayout()
    row = p._model.rows_for_key(e.key)[0]
    rect = grid.visualRect(p._model.index(row, 0))
    pos = rect.center()
    ev = QtGui.QHelpEvent(QtCore.QEvent.ToolTip, pos, grid.viewport().mapToGlobal(pos))
    QtWidgets.QApplication.sendEvent(grid.viewport(), ev)
    _pump(2)


def test_the_preview_of_a_card_you_point_at(win, fx_project):
    p = _library(win, fx_project)
    e = p.visible_entries()[0]
    pv = p.preview_widget()
    _point_at_card(p, e)
    assert pv.isVisible() and pv.image.width() == 248 + 12  # Normal
    p.hide_preview()
    _settings(win, {"library/hover_preview": "large"})
    _point_at_card(p, e)
    assert pv.isVisible() and pv.image.width() == 372 + 12
    _settings(win, {"library/hover_preview": "off"})
    assert not pv.isVisible()  # (the change put it away)
    _point_at_card(p, e)
    assert not pv.isVisible()
    assert QtWidgets.QToolTip.text() == e.reference  # a tool tip with its name
    QtWidgets.QToolTip.hideText()
    # a change keeps the search and the card selected
    p._search.setFocus()
    QTest.keyClicks(p._search, "pump")
    p._search.flush()
    _pump(2)
    found = [x.key for x in p.visible_entries()]
    assert len(found) >= 2
    pick = p.visible_entries()[min(4, len(found) - 1)]
    assert p.select_entry(pick) and p.current_entry() is pick
    _settings(win, {"library/hover_preview": "large"})
    assert p.query() == "pump" and [x.key for x in p.visible_entries()] == found
    assert p.current_entry() is not None and p.current_entry().key == pick.key


def test_names_with_the_letters_in_order_in_the_library_panel(win, fx_project):
    """'pmpl': the letters of fx_pump_small, fx_pump_large, fx_pump_tall in order, a part
    of no name (fx_pmp_mark has 'pmp' but no l after it)."""
    from ctgedit.ui.browser import search

    p = _library(win, fx_project)
    p._search.setFocus()
    QTest.keyClicks(p._search, "pmpl")
    p._search.flush()
    _pump(2)
    found = [e.name.lower() for e in p.visible_entries()]
    assert {"fx_pump_small", "fx_pump_large", "fx_pump_tall"} <= set(found)
    assert all("pmpl" not in n for n in found)  # only letters in order
    assert all(search.subsequence("pmpl", n) for n in found)
    _settings(win, {"browser/fuzzy": False})  # the results follow at once
    assert p.query() == "pmpl" and p.visible_entries() == []
    _settings(win, {"browser/fuzzy": True})
    assert [e.name.lower() for e in p.visible_entries()] == found


# ------------------------------------------------------------------ the page browser
@pytest.fixture()
def catalog(fx_install):
    """The catalog of the fx_install copy's User folder. FxThumb2 = a copy of FxThumb (its
    saved picture) beside it, so two pages hold one."""
    import shutil
    from ctgedit.io.catalog import catalog_for

    thumb = fx_install.page("FxThumb")
    shutil.copyfile(thumb, os.path.join(os.path.dirname(thumb), "FxThumb2.ctg"))
    cat = catalog_for(fx_install.user)
    cat.start = thumb
    return cat


def _browser(catalog, width=1200, **kw):
    _app()
    from ctgedit.ui.browser import PageBrowserDialog

    dlg = PageBrowserDialog(
        PageBrowserDialog.OPEN, catalog=catalog, start_path=catalog.start, kinds=("page",), **kw
    )
    dlg.resize(width, 720)
    dlg.show()
    # (its first show fits it to the screen, 800 px wide on the test platform: the width
    # asked for again, as on a monitor that has the room)
    dlg.resize(width, 720)
    _pump(3)
    return dlg


def test_names_with_the_letters_in_order_in_the_page_browser(win, catalog):
    dlg = _browser(catalog)
    try:
        QTest.keyClicks(dlg.grid, "smbl")  # typed in the grid: the search
        dlg.search.flush()
        names = [e.name for e in dlg.model.entries()]
        assert "FxSymbols" in names and dlg.model.fuzzy_used
    finally:
        dlg.reject()
        dlg.deleteLater()
    _settings(win, {"browser/fuzzy": False})
    dlg = _browser(catalog)
    try:
        QTest.keyClicks(dlg.grid, "smbl")
        dlg.search.flush()
        assert dlg.model.entries() == [] and not dlg.model.fuzzy_used
    finally:
        dlg.reject()
        dlg.deleteLater()


def test_a_name_start_most_pages_share_is_shortened_unless_settings_say_no(win):
    from ctgedit.io.catalog import Entry
    from ctgedit.ui.browser.model import CatalogModel

    pages = [
        Entry("page", "P", name, "C:\\x\\%s.ctg" % name)
        for name in ["Plant_Area_Pump_%02d" % k for k in range(6)] + ["Overview"]
    ]
    m = CatalogModel(None)
    m.set_entries(pages, "page")
    assert m.common_prefix == "Plant_Area_Pump_"  # the cards show '…01'
    _settings(win, {"browser/short_names": False})
    m.set_entries(pages, "page")
    assert m.common_prefix == ""  # the whole names


def test_the_preview_pane_when_the_window_is_wide_always_or_never(win, catalog):
    for choice, narrow, wide in (("auto", False, True), ("always", True, True), ("never", False, False)):
        _settings(win, {"browser/preview_pane": choice})
        dlg = _browser(catalog, width=1000)
        try:
            assert dlg.preview.isVisible() is narrow, choice
            dlg.resize(1200, 720)
            _pump(3)
            assert dlg.preview.isVisible() is wide, choice
        finally:
            dlg.reject()
            dlg.deleteLater()


class _Renderer:
    """Stands in for the render process (no process starts): the pages asked for."""

    def __init__(self):
        self.asked = []

    def request(self, token, path, w, h, owner):
        self.asked.append(os.path.basename(path))

    def attach(self, owner):
        pass

    def cancel_for(self, owner):
        pass


def test_the_preview_shows_the_saved_picture_or_a_fresh_drawing(win, catalog, monkeypatch):
    from ctgedit.ui.browser import preview as PV

    fake = _Renderer()
    monkeypatch.setattr(PV, "renderer", lambda name="preview": fake)

    wait = PV.PreviewPane.LIVE_WAIT_MS + 150

    def pick(dlg, name):
        fake.asked.clear()
        assert dlg.select_name(name)
        QTest.qWait(wait)
        return list(fake.asked)

    dlg = _browser(catalog)
    try:
        assert not dlg.preview.thumbs.has_no_thumbnail(dlg.current_entry())
        assert pick(dlg, "FxThumb2") == []  # the saved picture
        QTest.keyClick(dlg.grid, QtCore.Qt.Key_Space)  # Space draws it
        assert fake.asked == ["FxThumb2.ctg"]
    finally:
        dlg.reject()
        dlg.deleteLater()
    _settings(win, {"browser/preview_live": "live"})
    fake.asked.clear()
    dlg = _browser(catalog)
    try:
        QTest.qWait(wait)
        assert fake.asked == ["FxThumb.ctg"]  # the page it opens on
        assert pick(dlg, "FxThumb2") == ["FxThumb2.ctg"]  # each page selected is drawn
        assert pick(dlg, "FxThumb") == ["FxThumb.ctg"]
        # arrowing through the cards: only the page it stops on is drawn (the pages
        # without a saved picture aside: those are drawn at once, as before)
        dlg.preview.auto_live = False
        start = dlg.current_entry().key
        fake.asked.clear()
        for _ in range(10):
            QTest.keyClick(dlg.grid, QtCore.Qt.Key_Right)
        assert fake.asked == []  # (nothing before the pause)
        QTest.qWait(wait)
        now = dlg.current_entry()
        assert now.key != start and fake.asked == [os.path.basename(now.path)]
    finally:
        dlg.reject()
        dlg.deleteLater()
    # a pane that does not show draws nothing: Never, or a narrow window (until it is wide)
    for pane, width in (("never", 1200), ("auto", 1000)):
        _settings(win, {"browser/preview_pane": pane})
        fake.asked.clear()
        dlg = _browser(catalog, width=width)
        try:
            QTest.qWait(wait)
            assert not dlg.preview.isVisible() and fake.asked == [], pane
            assert pick(dlg, "FxThumb2") == [], pane
            if pane == "auto":
                dlg.resize(1200, 720)  # the pane shows: it draws now
                QTest.qWait(wait)
                assert dlg.preview.isVisible() and fake.asked == ["FxThumb2.ctg"]
        finally:
            dlg.reject()
            dlg.deleteLater()


# ------------------------------------------------------------------ styles
def _clean_styles():
    from ctgedit.ui.panels import styles_gallery as SG
    from ctgedit.ui.prefs.settings import app_settings

    for k in ("styles/list", "styles/shared_file"):
        app_settings().remove(k)
    SG.store().reload()


def test_pointing_at_a_style_previews_it_unless_settings_say_no(win, fx_page):
    _clean_styles()
    try:
        _page(win, fx_page)
        a = _rect(win)
        b = _rect(win, 0x404040, (420, 100, 500, 160))
        win.doc.set_line_width(a, 4)
        _select(win, [a])
        win.save_style("Thick")
        _pump(2)
        tile = win.ribbon_styles.tiles[0]
        _select(win, [b])

        def point(on):
            ev = (
                QtGui.QEnterEvent(
                    QtCore.QPointF(4, 4),
                    QtCore.QPointF(4, 4),
                    QtCore.QPointF(tile.mapToGlobal(QtCore.QPoint(4, 4))),
                )
                if on
                else QtCore.QEvent(QtCore.QEvent.Leave)
            )
            QtWidgets.QApplication.sendEvent(tile, ev)
            _pump(2)

        point(True)
        assert win._style_preview is not None and win._style_preview[1] == [b]
        point(False)
        assert win._style_preview is None

        def strip_tips():
            strips = win.props.findChildren(QtWidgets.QWidget, "panelStyles")
            assert strips, "no style tiles in the Properties panel"
            return {g.toolTip() for g in strips}

        assert strip_tips() == {"Click a style to apply it. Point at one to see it on the page"}
        _settings(win, {"draw/style_preview": False})
        assert strip_tips() == {"Click a style to apply it"}  # (no preview promised)
        _select(win, [a])
        _select(win, [b])
        assert strip_tips() == {"Click a style to apply it"}
        point(True)
        assert win._style_preview is None  # nothing until a click
        win.style_actions()["Thick"].hover()  # the Styles menu's item
        assert win._style_preview is None
        n = len(win.doc._undo)
        tile.click()
        assert len(win.doc._undo) == n + 1 and win.doc.appearance_of(b)["line_width"] == 4
    finally:
        _clean_styles()


# ------------------------------------------------------------------ Object Properties
def _sheet(win, idx):
    _select(win, [idx])
    win.act_obj_props.trigger()
    _pump(3)
    dlg = win._modeless_for("objprops", idx)
    assert dlg is not None and dlg.isVisible()
    return dlg


def _click_tab(dlg, k):
    bar = dlg._tabs.tabBar()
    QTest.mouseClick(bar, LEFT, NOMOD, bar.tabRect(k).center())
    _pump(2)


def test_object_properties_opens_on_the_first_tab_or_the_one_used_last(win, fx_page):
    _page(win, fx_page)
    a = _rect(win)
    b = _rect(win, 0x404040, (420, 100, 500, 160))
    dlg = _sheet(win, a)
    assert dlg._tabs.count() > 2 and dlg._tabs.currentIndex() == 0
    _click_tab(dlg, 2)
    used = dlg.current_page_id()
    assert used and used != dlg.page_ids()[0]
    dlg.close()
    _pump(2)
    dlg = _sheet(win, b)
    assert dlg._tabs.currentIndex() == 0  # the first tab (the default)
    dlg.close()
    _pump(2)
    _settings(win, {"objprops/open_tab": "last"})
    dlg = _sheet(win, a)
    assert dlg.current_page_id() == used  # the tab used last for a rectangle
    # an inner tab of the Input tab (a tab its module builds whole): Keyboard Commands
    k = next(i for i in range(dlg._tabs.count()) if dlg._tabs.tabText(i) == "Input")
    _click_tab(dlg, k)
    inner = dlg._tabs.currentWidget()
    assert isinstance(inner, QtWidgets.QTabWidget) and dlg.current_page_id() == "touch"
    j = next(i for i in range(inner.count()) if inner.tabText(i) == "Keyboard Commands")
    bar = inner.tabBar()
    QTest.mouseClick(bar, LEFT, NOMOD, bar.tabRect(j).center())
    _pump(2)
    assert dlg.current_page_id() == "keyboard"
    dlg.close()
    _pump(2)
    dlg = _sheet(win, b)
    assert dlg.current_page_id() == "keyboard"  # (not Input > Touch)
    dlg.close()
    _pump(2)


# ------------------------------------------------------------------ Suggest names after
def _typed(win, idx, letters) -> bool:
    """Type the first ``letters`` letters of a name the field offers into the Visibility
    expression of ``idx``'s sheet: the list of names opened."""
    dlg = _sheet(win, idx)
    try:
        ed = dlg._editors["VISIBLE"]
        dlg.show_page(next(p for p in dlg.page_ids() if "visib" in p.lower()))
        _pump(2)
        # (the names it offers: the page's tags are a copy's, read again after the Settings
        # window applied; a list of names takes the same rule)
        name = "Pump_Speed"
        ed.set_names([name, "Pump_Flow", "Fx_Level"])
        ed.setFocus()
        ed.setText("")
        QTest.keyClicks(ed, name[:letters])
        _pump(2)
        assert ed.text() == name[:letters]
        comp = ed.name_completer()
        return comp is not None and comp.popup().isVisible()
    finally:
        dlg.close()
        _pump(2)


def test_suggest_names_after(win, fx_page):
    _page(win, fx_page)
    a = _rect(win)
    assert not _typed(win, a, 1) and _typed(win, a, 2)  # 2 letters (the default)
    _settings(win, {"opt/tag_suggest": 3})
    assert not _typed(win, a, 2) and _typed(win, a, 3)
    _settings(win, {"opt/tag_suggest": 1})
    assert _typed(win, a, 1)
    _settings(win, {"opt/tag_suggest": 0})
    assert not _typed(win, a, 5)  # never
