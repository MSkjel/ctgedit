"""Names are never cut off at a table's default size."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pump(n=4):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture(autouse=True)
def _defaults():
    """The helper's module values back at their defaults around each test."""
    _app()
    from ctgedit.ui import tables

    tables.reset_settings()
    yield
    tables.reset_settings()


def _tree(rows, cols=("Name", "Value", "Note")):
    t = QtWidgets.QTreeWidget()
    t.setHeaderLabels(list(cols))
    t.setRootIsDecorated(False)
    for r in rows:
        t.addTopLevelItem(QtWidgets.QTreeWidgetItem(list(r)))
    return t


def _drag(view, col, dx):
    """The user drags the divider at the right of column ``col`` by ``dx`` pixels."""
    from PySide6 import QtTest
    from ctgedit.ui import tables

    hdr = tables.header_of(view)
    vp = hdr.viewport()
    x = hdr.sectionViewportPosition(col) + hdr.sectionSize(col) - 1
    y = vp.height() // 2
    QtTest.QTest.mousePress(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, y))
    QtTest.QTest.mouseMove(vp, QtCore.QPoint(x + dx, y))
    QtTest.QTest.mouseRelease(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x + dx, y))
    _pump()


def _tip_at(view, idx, monkeypatch) -> list:
    """The tool tips shown for a ToolTip event on cell ``idx``."""
    shown = []
    monkeypatch.setattr(QtWidgets.QToolTip, "showText", lambda pos, text, *a, **k: shown.append(text))
    rect = view.visualRect(idx)
    ev = QtGui.QHelpEvent(QtCore.QEvent.ToolTip, rect.center(), view.viewport().mapToGlobal(rect.center()))
    QtWidgets.QApplication.sendEvent(view.viewport(), ev)
    return shown


def _close(w):
    w.close()
    w.deleteLater()
    _pump()


# -- 1. Insert Tag: the Project box --------------------------------------------------------
def test_insert_tag_project_box_shows_the_whole_name_and_opens_its_list_as_wide():
    _app()
    from ctgedit.ui.objprops.expression import ExpressionContext, TagPickerDialog
    from ctgedit.ui.pick_fields import WideCombo

    dlg = TagPickerDialog(None, ExpressionContext())
    dlg.resize(640, 460)
    dlg.show()
    _pump()
    long = "SxW_Style_Include (3345)"
    box = dlg.project
    assert isinstance(box, WideCombo)
    box.addItem(long, "x")
    box.addItem("FxOther (12)", "y")
    box.setVisible(True)
    box.setCurrentIndex(box.findData("x"))
    _pump()
    fm = box.fontMetrics()
    assert box.sizePolicy().horizontalPolicy() == QtWidgets.QSizePolicy.Expanding
    # the box shows the whole name
    assert box.width() >= fm.horizontalAdvance(long) + 20
    box.showPopup()
    _pump()
    try:
        assert box.view().minimumWidth() >= fm.horizontalAdvance(long)
    finally:
        box.hidePopup()
    _close(dlg)


# -- 2. the Objects panel's columns --------------------------------------------------------
def test_objects_panel_columns_fit_their_text_and_a_cut_tag_cell_is_in_the_tip(fx_install, monkeypatch):
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui import tables
    from ctgedit.ui.panels.objects_dock import ObjectsDock
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("objects/columns", ["object", "tag"])
    doc = EditableDocument.open(fx_install.page("FxShapes"))
    d = ObjectsDock()
    d.resize(700, 500)
    d.show()
    _pump()
    d.set_page(doc.page, [], hidden=set())
    _pump()
    tree = d._tree
    assert d.columns == ["object", "tag"]
    state = tables.state_of(tree)
    assert state is not None and 1 in state.text_columns and not state.fixed
    tag = "Mixing_Tank1_Agitator_Speed"
    idx = next(iter(d._items))
    d._items[idx].setText(1, tag)
    state.schedule()
    _pump()
    fm = tree.fontMetrics()
    # the column fits the tag
    assert tree.columnWidth(1) >= fm.horizontalAdvance(tag) + 8 > 100
    # a narrow panel cuts it (the columns share the panel's width): its text first in the
    # tip, then the row's own tip
    d.resize(260, 500)
    _pump()
    cell = tree.indexFromItem(d._items[idx], 1)
    assert tables.is_cut(tree, cell, tag)
    shown = _tip_at(tree, cell, monkeypatch)
    assert shown and shown[0].startswith(tag) and "stacking order" in shown[0]
    _close(d)


# -- 3. Go To Object: a column ticked later ------------------------------------------------
def test_a_remembered_width_of_a_column_hidden_then_is_fitted_when_it_is_shown():
    _app()
    from ctgedit.ui import tables

    long = "a value that is a good deal wider than Qt's default column"
    t = _tree([("x", long, "n")])
    t.setColumnHidden(1, True)
    tables.make_resizable(t, "Probe/unsized")
    t.resize(600, 200)
    t.show()
    _pump()
    _drag(t, 0, 20)  # the user's widths: column 1 remembered as 0
    widths, _v = tables.parse_widths(tables.app_settings().value("columns/Probe/unsized"))
    assert widths[1] == 0
    t.close()
    t2 = _tree([("x", long, "n")])
    tables.make_resizable(t2, "Probe/unsized")
    t2.resize(600, 200)
    t2.show()
    _pump()
    fm = t2.fontMetrics()
    # (its text, capped)
    assert t2.columnWidth(1) >= min(fm.horizontalAdvance(long), tables.MAX_FIT_WIDTH)
    t2.close()


def test_goto_object_a_column_ticked_later_comes_back_fitted(fx_install):
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui import tables
    from ctgedit.ui.dialog.dialogs import GOTO_COLUMNS, GotoObjectDialog
    from ctgedit.ui.prefs.settings import app_settings

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    keys = [k for k, _h, _d in GOTO_COLUMNS]
    c = 1 + keys.index("text")
    dlg = GotoObjectDialog(doc)
    dlg.resize(900, 500)
    dlg.show()
    _pump()
    _drag(dlg._tree, 0, 30)  # the user's widths are remembered
    assert tables.has_user_widths(dlg._tree)
    # (Locked last: Qt stretches the last column, Text is sized to its text)
    dlg.show_column("locked", True)
    dlg.show_column("text", True)
    _pump()
    hdr = dlg._tree.header()
    want = max(
        min(max(dlg._tree.sizeHintForColumn(c), hdr.sectionSizeHint(c)), tables.MAX_FIT_WIDTH),
        hdr.minimumSectionSize(),
    )
    assert want != 100 and dlg._tree.columnWidth(c) == want
    _close(dlg)
    again = GotoObjectDialog(doc)
    again.resize(900, 500)
    again.show()
    _pump()
    try:
        assert not again._tree.isColumnHidden(c)
        assert again._tree.columnWidth(c) == want  # (not Qt's default 100 px)
    finally:
        _close(again)
        app_settings().remove("goto/columns")


# -- 4. Select Equipment ---------------------------------------------------------------------
def test_select_equipment_keeps_the_whole_dotted_name_at_every_level():
    _app()
    from ctgedit.ui import tables
    from ctgedit.ui.panels.sa_pickers import EquipmentBrowserDialog

    deep = "FxPlant.FxArea01.FxLine02.FxMixer03.FxMotor"
    comment = "A very long comment of the kind a site writes in its equipment table " * 2
    page = "FxVeryLongPageNameOfTheEquipmentOverviewDisplay"
    rows = [
        {"NAME": deep, "PROJECT": "FxMain", "TYPE": "Motor", "COMMENT": comment, "PAGE": page},
        {
            "NAME": "FxPlant.FxArea01.FxPump",
            "PROJECT": "FxMain",
            "TYPE": "Pump",
            "COMMENT": comment,
            "PAGE": page,
        },
    ]
    dlg = EquipmentBrowserDialog("", "", rows=rows)
    # (within the offscreen screen, 800 px: a wider request is clamped once a window was
    # shown in the process)
    dlg.resize(780, 560)
    dlg.show()
    _pump()
    view, m = dlg.view, dlg.model
    idx = m.index(0, 0)
    for _level in range(deep.count(".")):
        if m.canFetchMore(idx):
            m.fetchMore(idx)
        view.expand(idx)
        _pump()
        idx = m.index(0, 0, idx)
        assert idx.isValid()
    _pump()
    assert idx.data() == deep
    try:
        assert not tables.is_cut(view, idx, deep)
    finally:
        _close(dlg)


# -- 5. a fit measures every row of a text column ------------------------------------------
def test_a_text_column_is_fitted_over_every_row_not_only_the_rows_on_screen():
    _app()
    from ctgedit.ui import tables

    long = "a page name far down the list, well past the rows on screen"
    rows = [("page %03d" % r, "t", "n") for r in range(300)]
    rows[250] = (long, "t", "n")
    t = _tree(rows)
    t.header().setResizeContentsPrecision(0)
    tables.make_resizable(t, "Probe/textfit", text_columns=(0,), grow=True)
    t.resize(600, 120)
    t.show()
    _pump()
    fm = t.fontMetrics()
    assert t.columnWidth(0) >= fm.horizontalAdvance(long)
    # text that comes later into a row (the page browser's summaries): the column widens
    later = "a title that comes from the page summary read in the background"
    t.topLevelItem(5).setText(1, later)
    _pump()
    assert t.columnWidth(1) >= fm.horizontalAdvance(later)
    t.close()


def test_the_page_browsers_list_fits_from_the_models_text(fx_install):
    _app()
    from ctgedit.ui import tables
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.browser.model import COL_NAME

    dlg = PageBrowserDialog(PageBrowserDialog.OPEN)
    try:
        state = tables.state_of(dlg.table)
        assert state.fit and state.grow and state.stretch == COL_NAME
        assert state.text_columns == set(range(dlg.model.columnCount()))
        assert not state.designed  # (no first widths in code)
    finally:
        _close(dlg)


# -- 6. the Tags panel's first widths --------------------------------------------------------
def test_tags_panel_first_widths_follow_their_settings_live():
    _app()
    from ctgedit.ui.prefs import settings_pages as SP
    from ctgedit.ui.panels import tags_dock as TD
    from ctgedit.ui.prefs.settings import app_settings

    d = TD.TagsDock()
    d.resize(400, 300)
    d.show()
    _pump()
    hdr = d._table.horizontalHeader()
    col = {c: TD.DOCK_COLUMNS.index(c) for c in TD.DOCK_COLUMNS}
    d._fit_columns()
    rest = d._table.viewport().width() - hdr.sectionSize(col["type"])
    assert hdr.sectionSize(col["name"]) == max(90, int(rest * 0.42))
    assert hdr.sectionSize(col["equip"]) == max(60, int(rest * 0.29))
    for key in TD.COLUMN_SPANS:
        assert TD._apply_column_settings in SP.APPLY_HOOKS.get(key, []), key
    app_settings().setValue("tags/name_share", 60)
    app_settings().setValue("tags/min_column_width", 70)

    class Win:
        tags_dock = d

    TD._apply_column_settings(Win())  # (the Settings window's apply)
    assert hdr.sectionSize(col["name"]) == max(90, int(rest * 0.6))
    assert hdr.sectionSize(col["equip"]) >= 70
    d.close()


# -- 7. the manual's contents tree -----------------------------------------------------------
def test_help_contents_tree_tips_a_cut_title(monkeypatch):
    _app()
    from ctgedit.ui import tables
    from ctgedit.ui.dialog.help_window import HelpWindow

    hw = HelpWindow()
    hw.show()
    _pump()
    hw.resize(800, 500)
    hw.split.setSizes([90, 700])
    _pump()
    tree = hw.tree
    fm = tree.fontMetrics()
    best, widest = None, 0
    for k in range(tree.topLevelItemCount()):
        it = tree.topLevelItem(k)
        w = fm.horizontalAdvance(it.text(0))
        if w > widest:
            best, widest = it, w
    idx = tree.indexFromItem(best, 0)
    try:
        assert tables.is_cut(tree, idx, best.text(0))
        shown = _tip_at(tree, idx, monkeypatch)
        assert shown == [tables.tool_tip_text(best.text(0))]
    finally:
        _close(hw)


# -- 8. the helper's numbers are settings ----------------------------------------------------
def test_the_fit_cap_is_a_setting_applied_live():
    _app()
    from ctgedit.ui.prefs import settings_pages as SP
    from ctgedit.ui import tables
    from ctgedit.ui.prefs.settings import app_settings

    long = (
        "a message so long that the fit's cap of 360 px cuts it well before its end, "
        "as Find and Replace's Text column showed"
    )
    t = _tree([(long, "1", "x")])
    tables.make_resizable(t, "Probe/cap")
    t.resize(1200, 200)
    t.show()
    _pump()
    fm = t.fontMetrics()
    assert fm.horizontalAdvance(long) > 400
    assert t.columnWidth(0) == 360
    for key in tables.SETTINGS:
        assert tables.apply_settings in SP.APPLY_HOOKS.get(key, []), key
    app_settings().setValue("tables/max_fit_width", 900)
    tables.apply_settings()  # (the Settings window's apply)
    _pump()
    assert tables.MAX_FIT_WIDTH == 900
    assert t.columnWidth(0) >= fm.horizontalAdvance(long)
    # a user's width is kept
    _drag(t, 0, -200)
    w = t.columnWidth(0)
    app_settings().setValue("tables/max_fit_width", 300)
    tables.apply_settings()
    _pump()
    assert t.columnWidth(0) == w
    t.close()


def test_fit_view_shares_and_least_widths_are_settings():
    _app()
    from ctgedit.ui import tables
    from ctgedit.ui.prefs.settings import app_settings

    long = "a column text that wants far more than a third of the view " * 2
    t = _tree([("main", long, "x")])
    tables.make_resizable(t, "Probe/shares", fit_view=True, stretch=0)
    t.resize(600, 200)
    t.show()
    _pump()
    width = t.viewport().width()
    assert t.columnWidth(1) == int(width * 0.35)
    app_settings().setValue("tables/fit_share", 50)
    app_settings().setValue("tables/main_share", 20)
    tables.apply_settings()
    _pump()
    assert tables.FIT_SHARE == 0.5 and tables.MAIN_SHARE == 0.2
    assert t.columnWidth(1) == int(width * 0.5)
    t.close()


def test_every_table_setting_has_a_row_a_declaration_and_a_span():
    from ctgedit.ui import tables
    from ctgedit.ui.panels import tags_dock as TD
    from ctgedit.ui.prefs.settings import SETTINGS_KEYS, WHOLE_SPANS
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    rows = {r.key: r for r in SettingsDialog.ROWS}
    for key, (span, _name, _pc) in list(tables.SETTINGS.items()) + [
        (k, (s, "", False)) for k, s in TD.COLUMN_SPANS.items()
    ]:
        assert key in SETTINGS_KEYS, key
        assert WHOLE_SPANS[key] == span, key
        row = rows[key]
        assert row.page == "Appearance" and row.kind == "int" and row.span == span, key
        assert span[0] <= SETTINGS_KEYS[key] <= span[1], key


def test_the_panels_first_widths_are_no_longer_fixed_in_code():
    """The panels set no fixed first widths in code: the fit sizes the columns (a width set in
    code is the least a fit gives)."""
    import re

    for rel in (
        "panels/changes_dock.py",
        "panels/page_checks.py",
        "panels/sa_checks.py",
        "dialog/history_versions.py",
        "editor/underlay_panel.py",
        os.path.join("objprops", "metadata.py"),
        "panels/sa_pickers.py",
    ):
        path = os.path.join(REPO, "ctgedit", "ui", rel)
        src = open(path, encoding="utf-8").read()
        assert not re.search(r"\.(setColumnWidth|resizeSection)\(\s*\d+\s*,\s*\d+\s*\)", src), rel
