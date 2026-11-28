"""Every table and tree lets the user size its columns."""

import os
import re
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from qtkit import app as _app

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: views whose header the user cannot size, and why. Matched by object name or
#: by the view's class name.
EXCEPTIONS = {
    # header hidden: a one-column list with a dim count / note column (the count column
    # is as wide as its text, the names take the rest: nothing to size)
    "libTree": "Library panel tree: header hidden, name + count",
    # the Ctrl+Tab list of open pages: shown while Ctrl is held, nothing to drag
    "TabSwitcherList": "the Ctrl+Tab list: header hidden, page + project",
}


def _pump(n=4):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _excepted(view) -> bool:
    return view.objectName() in EXCEPTIONS or type(view).__name__ in EXCEPTIONS


CHECKED: list = []


def _problems(root, where: str) -> list:
    """The views under ``root`` whose header the user cannot size."""
    from ctgedit.ui import tables

    out = []
    views = root.findChildren(QtWidgets.QAbstractItemView)
    if isinstance(root, QtWidgets.QAbstractItemView):
        views.append(root)
    for v in views:
        hdr = tables.header_of(v)
        if hdr is None or hdr.isHidden() or _excepted(v):
            continue
        # a completer popup is a list, never a table the user works in
        if v.windowFlags() & QtCore.Qt.Popup:
            continue
        name = "%s %s %r" % (where, type(v).__name__, v.objectName() or v.accessibleName())
        CHECKED.append(name)
        if getattr(v, tables.ATTR, None) is None:
            out.append(name + ": not set up by tables.make_resizable")
            continue
        modes = {hdr.sectionResizeMode(c) for c in range(hdr.count())}
        if modes - {QtWidgets.QHeaderView.Interactive}:
            out.append(name + ": sections not Interactive %s" % modes)
        if not hdr.stretchLastSection():
            out.append(name + ": last section does not stretch")
        # Fit Columns to Contents: on the header's own right-click menu, or (a header menu of
        # the view's own) added there: test_every_module_with_a_header_menu_of_its_own...
        state = getattr(v, tables.ATTR)
        if state.fit_action not in hdr.actions():
            out.append(name + ": no Fit Columns to Contents")
        if hdr.contextMenuPolicy() not in (QtCore.Qt.ActionsContextMenu, QtCore.Qt.CustomContextMenu):
            out.append(name + ": the header has no right-click menu")
    return out


def _hidden_headers(root, where: str) -> list:
    """The views under ``root`` with several visible columns and no header to size them
    by."""
    from ctgedit.ui import tables

    out = []
    for v in root.findChildren(QtWidgets.QAbstractItemView):
        hdr = tables.header_of(v)
        if hdr is None or not hdr.isHidden() or _excepted(v) or v.windowFlags() & QtCore.Qt.Popup:
            continue
        shown = [c for c in range(hdr.count()) if not hdr.isSectionHidden(c)]
        if len(shown) > 1:
            out.append(
                "%s %s %r: header hidden over %d columns"
                % (where, type(v).__name__, v.objectName() or v.accessibleName(), len(shown))
            )
    return out


def _all_tabs(widget) -> None:
    """Visit every tab (pages that build on first show)."""
    for tw in widget.findChildren(QtWidgets.QTabWidget):
        for k in range(tw.count()):
            tw.setCurrentIndex(k)
            _pump(1)


# -- the static check: no table is built without the helper --------------------
_VIEW = re.compile(r"QtWidgets\.(QTreeWidget|QTableWidget|QTableView|QTreeView)\(")
_FILE_EXCEPTIONS = {
    "panels/objects_dock.py": "Objects tree: header hidden",
    "dialog/help_window.py": "help contents tree: header hidden",
    "panels/library_panel.py": "Library tree: header hidden (EXCEPTIONS libTree)",
    "browser/project_tree.py": "project tree: header hidden",
    "panels/doc_tabs.py": "the Ctrl+Tab list of open pages: header hidden",
    "objprops/cicode_assist.py": "the completion popup: header hidden, sized to its two columns",
}


def test_every_ui_module_that_builds_a_table_uses_the_helper():
    """Each module under ctgedit/ui that builds a table or tree view calls
    tables.make_resizable (directly or through a module helper), or hides
    the header; else it is listed above with the reason."""
    ui = os.path.join(REPO, "ctgedit", "ui")
    bad = []
    for root, _d, files in os.walk(ui):
        for f in files:
            if not f.endswith(".py") or f == "tables.py":
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, ui).replace("\\", "/")
            src = open(path, encoding="utf-8").read()
            if not _VIEW.search(src):
                continue
            if "make_resizable(" in src or rel in _FILE_EXCEPTIONS:
                continue
            bad.append(rel)
    assert not bad, "tables built without ui/tables.make_resizable: %s" % bad
    # and no module sets its own Fixed / Stretch / ResizeToContents columns
    # on a horizontal header (the helper owns them)
    own = []
    for root, _d, files in os.walk(ui):
        for f in files:
            if not f.endswith(".py") or f == "tables.py":
                continue
            rel = os.path.relpath(os.path.join(root, f), ui).replace("\\", "/")
            if rel in _FILE_EXCEPTIONS:
                continue
            for n, line in enumerate(open(os.path.join(root, f), encoding="utf-8"), 1):
                if (
                    "setSectionResizeMode" in line
                    and "verticalHeader" not in line
                    and not line.lstrip().startswith(("vh.", "#"))
                ):
                    own.append("%s:%d" % (rel, n))
    assert not own, "header modes set outside ui/tables.py: %s" % own


# -- the walk --------------------------------------------------------------------
@pytest.fixture
def page(fx_page):
    """FxMain's FxShapes in the test's copy of the fake install."""
    return fx_page("FxShapes")


def test_every_header_in_the_app_is_resizable(page):
    proj = os.path.dirname(page)  # (FxMain: its pages.DBF and tables)
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.model.document import ObjectKind
    from ctgedit.ui.main_window import MainWindow

    problems = []
    w = MainWindow()
    try:
        w.resize(1200, 800)
        w.show()
        w.open_document(page)
        _pump()
        for dock in w.findChildren(QtWidgets.QDockWidget):
            dock.show()
        _pump()
        problems += _problems(w, "MainWindow") + _hidden_headers(w, "MainWindow")
        doc = w.doc

        from ctgedit.backend.compiler import CompileMessage, CompileResult
        from ctgedit.ui.dialog.activex_insert import InsertActiveXDialog
        from ctgedit.ui.dialog.dialogs import CompileResultsDialog, GotoObjectDialog, UpdatePagesDialog
        from ctgedit.ui.objprops import ObjectPropertiesDialog
        from ctgedit.ui.dialog.page_props import PagePropertiesDialog
        from ctgedit.ui.panels.diag_ui import UnresolvedReferencesDialog
        from ctgedit.ui.dialog.find_dialog import FindReplaceDialog
        from ctgedit.ui.dialog.library_dialogs import UsedByDialog
        from ctgedit.ui.objprops.expression import (
            ExpressionContext,
            FunctionPickerDialog,
            TagPickerDialog,
            TrendTagPickerDialog,
        )
        from ctgedit.ui.panels.sa_pickers import EquipmentBrowserDialog
        from ctgedit.ui.simulate.window import SimulateWindow
        from ctgedit.io.project import Project

        res = CompileResult(
            "FxMain",
            "completed",
            0,
            1.0,
            [CompileMessage("FxMain", "FxShapes", 3, "a message", "E1000", "error")],
        )
        report = types.SimpleNamespace(
            path=page,
            search_paths=[proj],
            includes=[],
            unresolved=[
                types.SimpleNamespace(
                    reference="lib.elem", library="lib", ext="ctl", reason="not found", count=1, files=[]
                )
            ],
        )
        ctx = ExpressionContext()
        dialogs = [
            ("CompileResultsDialog", lambda: CompileResultsDialog(res, "FxShapes")),
            ("GotoObjectDialog", lambda: GotoObjectDialog(doc)),
            ("FindReplaceDialog", lambda: FindReplaceDialog(doc)),
            ("UsedByDialog", lambda: UsedByDialog("tanks.ctm", "tank1", [], None)),
            ("UnresolvedReferencesDialog", lambda: UnresolvedReferencesDialog(report)),
            ("InsertActiveXDialog", lambda: InsertActiveXDialog(proj, w)),
            ("PagePropertiesDialog", lambda: PagePropertiesDialog(doc, Project(proj))),
            ("TagPickerDialog", lambda: TagPickerDialog(None, ctx)),
            ("FunctionPickerDialog", lambda: FunctionPickerDialog(None, ctx)),
            ("TrendTagPickerDialog", lambda: TrendTagPickerDialog(None, ctx)),
            ("EquipmentBrowserDialog", lambda: EquipmentBrowserDialog("", "", rows=[])),
            ("UpdatePagesDialog", lambda: UpdatePagesDialog(proj, None, projects=[("Probe", proj)])),
            ("SimulateWindow", SimulateWindow),
        ]
        # object sheets: a rectangle (fill tables, metadata), a polygon
        # (vertices), a text (display value tables), a trend (pens)
        rect = doc.create_object("rectangle", (40, 40, 120, 90))
        poly = doc.create_object("polygon", nodes=[(150, 80), (200, 20), (250, 80)])
        text = next(
            (
                i
                for i, o in enumerate(doc.page.objects)
                if o.kind is ObjectKind.TEXT and o.parent is None and not o.raw.get("_from_template")
            ),
            None,
        )
        trend = doc.create_object("trend", (300, 40, 400, 120))
        for label, idx in (("rect", rect), ("polygon", poly), ("text", text), ("trend", trend)):
            if idx is not None:
                dialogs.append(
                    ("ObjectPropertiesDialog/" + label, lambda i=idx: ObjectPropertiesDialog(doc, i))
                )
        assert isinstance(doc, EditableDocument)
        for where, make in dialogs:
            dlg = make()
            try:
                dlg.show()
                _pump()
                _all_tabs(dlg)
                for sub in dlg.findChildren(QtWidgets.QStackedWidget):
                    for k in range(sub.count()):
                        sub.setCurrentIndex(k)
                _pump()
                problems += _problems(dlg, where) + _hidden_headers(dlg, where)
                if where == "GotoObjectDialog":
                    # (its header menu is its column chooser: the item is added there)
                    from ctgedit.ui import tables

                    dlg._fill_columns_menu()
                    if tables.state_of(dlg._tree).fit_action not in dlg._cols_menu.actions():
                        problems.append(where + ": no Fit Columns to Contents in its menu")
            finally:
                dlg.close()
                dlg.deleteLater()
                _pump()
    finally:
        w.hide()  # close() would ask to save the new objects
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.deleteLater()
        _pump()
    assert not problems, "\n".join(problems)


# -- the helper ------------------------------------------------------------------
def _tree(rows, cols=("Name", "Value", "Note")):
    t = QtWidgets.QTreeWidget()
    t.setHeaderLabels(list(cols))
    t.setRootIsDecorated(False)
    for r in rows:
        t.addTopLevelItem(QtWidgets.QTreeWidgetItem(list(r)))
    return t


def _drag(view, col, dx):
    """The user drags the divider at the right of column ``col`` by ``dx`` pixels (real
    mouse events on the header)."""
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


def _sizes(view):
    from ctgedit.ui import tables

    hdr = tables.header_of(view)
    return [hdr.sectionSize(c) for c in range(hdr.count())]


def test_columns_fit_on_first_show_and_user_widths_come_back():
    _app()
    from ctgedit.ui import tables
    from ctgedit.ui.prefs.settings import app_settings

    t = _tree([("a fairly long name here", "1", "x")])
    tables.make_resizable(t, "Probe/list")
    t.resize(600, 200)
    t.show()
    _pump()
    hdr = t.header()
    assert hdr.sectionResizeMode(0) == QtWidgets.QHeaderView.Interactive
    assert hdr.stretchLastSection()
    fm = t.fontMetrics()
    assert hdr.sectionSize(0) >= fm.horizontalAdvance("a fairly long name here")
    assert not tables.has_user_widths(t)
    # the user drags column 1 wider: remembered (with the view's width)
    before = hdr.sectionSize(1)
    _drag(t, 1, 60)
    assert hdr.sectionSize(1) == before + 60
    assert tables.has_user_widths(t)
    raw = app_settings().value("columns/Probe/list")
    widths, view_w = tables.parse_widths(raw)
    assert widths[1] == before + 60 and view_w == t.viewport().width()
    t.close()
    t2 = _tree([("x", "y", "z")])
    tables.make_resizable(t2, "Probe/list")
    t2.resize(600, 200)
    t2.show()
    _pump()
    assert t2.header().sectionSize(1) == before + 60  # (not fitted to 'y')
    assert tables.has_user_widths(t2)
    t2.close()


def test_qt_and_code_resizes_are_never_the_users():
    """Only a drag (or a double-click) on the header counts: a resize in code, the last
    section stretching and the window's own resizes are never remembered."""
    _app()
    from ctgedit.ui import tables
    from ctgedit.ui.prefs.settings import app_settings

    for fit_view in (False, True):
        key = "Probe/qt%d" % fit_view
        t = _tree([("a name", "1", "x")])
        tables.make_resizable(t, key, stretch=0, fit_view=fit_view)
        t.resize(500, 200)
        t.show()
        _pump()
        t.header().resizeSection(1, 150)  # code
        t.resize(300, 200)  # the window
        _pump()
        t.resize(700, 200)
        _pump()
        t.model().layoutChanged.emit()  # a model change
        _pump()
        assert not tables.has_user_widths(t), fit_view
        assert not app_settings().value(tables.settings_key(key)), fit_view
        t.close()


def test_a_stretching_last_section_is_not_a_user_change():
    """A 2-column tree in a dialog, the names in column 0 (stretch=0, fit_view): Qt stretching
    the last section on the first resize is not a drag, so the names are not cut."""
    _app()
    from ctgedit.ui import tables

    dlg = QtWidgets.QDialog()
    lay = QtWidgets.QVBoxLayout(dlg)
    t = QtWidgets.QTreeWidget()
    t.setHeaderLabels(["Project", "Pages"])
    t.setColumnCount(2)
    tables.make_resizable(t, "Probe/projects", stretch=0, fit_view=True)
    lay.addWidget(t)
    root = QtWidgets.QTreeWidgetItem(["Plant_Root_Project", "No pages"])
    t.addTopLevelItem(root)
    for name, n in (
        ("Area_North_Mixing_And_Dosing", "12 pages"),
        ("Library_Equipment_Pumps_And_Valves", "No pages"),
        ("Area_South_Packaging_Line_Two", "140 pages"),
    ):
        root.addChild(QtWidgets.QTreeWidgetItem([name, n]))
    t.expandAll()
    dlg.resize(480, 400)
    dlg.show()
    _pump(10)
    try:
        hdr = t.header()
        assert not tables.has_user_widths(t)
        width = t.viewport().width()
        if width >= t.sizeHintForColumn(0) + t.sizeHintForColumn(1):
            assert hdr.sectionSize(0) >= t.sizeHintForColumn(0)  # no name is cut
        assert hdr.sectionSize(0) + hdr.sectionSize(1) >= width - 2  # (the rest: the names)
        assert hdr.sectionSize(1) < width // 3  # (Pages as wide as its text)
        assert hdr.sectionSize(1) >= t.fontMetrics().horizontalAdvance("140 pages")
        assert sum(_sizes(t)) <= t.viewport().width() + 2  # no sideways scroll
    finally:
        dlg.close()
        dlg.deleteLater()
        _pump()


def test_fit_view_remembers_user_widths_and_they_give_way_when_they_do_not_fit():
    _app()
    from ctgedit.ui import tables

    rows = [("Plant.Mixer.Tank%d.Level" % k, "12.5", "Good") for k in range(5)]
    t = _tree(rows, ("Tag", "Value", "Quality"))
    tables.make_resizable(t, "Probe/fitview", stretch=0, fit_view=True)
    t.resize(500, 200)
    t.show()
    _pump()
    _drag(t, 1, 80)
    hdr = t.header()
    mine = hdr.sectionSize(1)
    assert tables.has_user_widths(t) and mine >= 100
    t.close()
    t.deleteLater()
    # the next time: the user's widths come back
    t2 = _tree(rows, ("Tag", "Value", "Quality"))
    tables.make_resizable(t2, "Probe/fitview", stretch=0, fit_view=True)
    t2.resize(500, 200)
    t2.show()
    _pump()
    hdr = t2.header()
    assert hdr.sectionSize(1) == mine
    quality = hdr.sectionSize(2)
    # a narrow view: the main column gives way first, never a sideways scroll
    t2.resize(320, 200)
    _pump()
    assert sum(_sizes(t2)) <= t2.viewport().width() + 2
    assert hdr.sectionSize(0) >= tables.MIN_STRETCH_WIDTH
    # narrower still: the others give way too, in proportion
    t2.resize(230, 200)
    _pump()
    assert sum(_sizes(t2)) <= t2.viewport().width() + 2
    assert hdr.sectionSize(1) < mine
    # room again: back up to the remembered widths
    t2.resize(600, 200)
    _pump()
    assert hdr.sectionSize(1) == mine and hdr.sectionSize(2) >= quality
    assert sum(_sizes(t2)) <= t2.viewport().width() + 2
    t2.close()


def test_the_main_column_takes_the_change_of_the_width_since_the_user_set_them():
    _app()
    from ctgedit.ui import tables

    rows = [("a message of a fair length", "where.ci:12", "n")]
    t = _tree(rows, ("Message", "Where", "Note"))
    tables.make_resizable(t, "Probe/main", stretch=0)
    t.resize(600, 200)
    t.show()
    _pump()
    _drag(t, 1, 50)
    widths = _sizes(t)
    t.close()
    t2 = _tree(rows, ("Message", "Where", "Note"))
    tables.make_resizable(t2, "Probe/main", stretch=0)
    t2.resize(700, 200)
    t2.show()
    _pump()
    got = _sizes(t2)
    assert got[1] == widths[1]
    assert got[0] == widths[0] + 100  # (the window is 100 px wider)
    t2.close()


def test_fit_columns_to_contents_forgets_and_fits_again():
    _app()
    from ctgedit.ui import tables
    from ctgedit.ui.prefs.settings import app_settings

    for fit_view in (False, True):
        key = "Probe/refit%d" % fit_view
        t = _tree([("a fairly long name here", "1", "x")])
        state = tables.make_resizable(t, key, fit_view=fit_view)
        t.resize(600, 200)
        t.show()
        _pump()
        fitted = _sizes(t)[0]
        _drag(t, 0, -60)
        assert tables.has_user_widths(t) and _sizes(t)[0] < fitted
        hdr = t.header()
        # the header's own right-click menu has the item
        assert hdr.contextMenuPolicy() == QtCore.Qt.ActionsContextMenu
        acts = [a for a in hdr.actions() if a.text() == tables.FIT_TEXT]
        assert acts == [state.fit_action]
        acts[0].trigger()
        _pump()
        assert not tables.has_user_widths(t), fit_view
        assert not app_settings().value(tables.settings_key(key)), fit_view
        assert abs(_sizes(t)[0] - fitted) <= 8, fit_view
        t.close()


def test_a_header_menu_of_the_views_own_gets_the_item():
    _app()
    from ctgedit.ui import tables

    t = _tree([("a", "b", "c")])
    hdr = t.header()
    hdr.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
    state = tables.make_resizable(t, "Probe/own")
    assert hdr.contextMenuPolicy() == QtCore.Qt.CustomContextMenu  # (kept)
    menu = QtWidgets.QMenu()
    menu.addAction("Columns")
    assert tables.add_fit_item(menu, t) is state.fit_action
    assert state.fit_action in menu.actions()
    menu.deleteLater()


def test_every_module_with_a_header_menu_of_its_own_adds_the_fit_item():
    ui = os.path.join(REPO, "ctgedit", "ui")
    bad = []
    for root, _d, files in os.walk(ui):
        for f in files:
            if not f.endswith(".py") or f == "tables.py":
                continue
            src = open(os.path.join(root, f), encoding="utf-8").read()
            if re.search(r"(hdr|header\(\))\.customContextMenuRequested", src) and "add_fit_item(" not in src:
                bad.append(f)
    assert not bad, bad


def test_every_table_has_a_key_of_its_own():
    """No two make_resizable calls share a literal key (the widths of one table would
    land in another)."""
    ui = os.path.join(REPO, "ctgedit", "ui")
    seen = {}
    dup = []
    pat = re.compile(r"make_resizable\(\s*[\w.]+\s*,\s*\"([^\"%]+)\"")
    for root, _d, files in os.walk(ui):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, ui)
            for key in pat.findall(open(path, encoding="utf-8").read()):
                if key.endswith("/"):
                    continue  # ('<window>/' + a name per table)
                if key in seen:
                    dup.append("%s: %s and %s" % (key, seen[key], rel))
                seen.setdefault(key, rel)
    assert len(seen) > 30
    assert not dup, dup


def test_double_click_on_a_divider_fits_and_is_remembered():
    _app()
    from ctgedit.ui import tables
    from ctgedit.ui.prefs.settings import app_settings

    t = _tree([("short", "a value that is much longer than the column", "n")])
    tables.make_resizable(t, "Probe/dbl", fit_on_first_fill=False)
    t.resize(700, 200)
    t.show()
    _pump()
    hdr = t.header()
    hdr.resizeSection(1, 30)
    hdr.sectionHandleDoubleClicked.emit(1)  # what the header sends on a divider
    _pump()
    assert hdr.sectionSize(1) > 30
    assert app_settings().value("columns/Probe/dbl")
    t.close()


def _press_release_on_title(view, during):
    """A plain click on the first column's title (to sort, not on a divider) with
    ``during()`` run while the button is down."""
    from PySide6 import QtTest
    from ctgedit.ui import tables

    hdr = tables.header_of(view)
    vp = hdr.viewport()
    x = hdr.sectionViewportPosition(0) + hdr.sectionSize(0) // 2
    QtTest.QTest.mousePress(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, 5))
    during()
    _pump(6)
    QtTest.QTest.mouseRelease(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, 5))
    _pump(6)


def _reopen(key, make, width, **kw):
    from ctgedit.ui import tables

    t = make()
    tables.make_resizable(t, key, **kw)
    t.resize(width, 200)
    t.show()
    _pump()
    return t


def test_fit_view_a_narrowed_main_column_widens_again():
    """The user narrows the main column by mistake and drags it back: it comes back, now and
    the next time."""
    _app()
    from ctgedit.ui import tables

    for cols in (("Project", "Pages"), ("Tag", "Value", "Quality")):
        rows = [("ProjectName_%d_SomeLongerName" % k, "12", "Good")[: len(cols)] for k in range(5)]
        key = "Probe/narrowed%d" % len(cols)

        def make():
            return _tree(rows, cols)

        t = _reopen(key, make, 420, stretch=0, fit_view=True)
        fitted = _sizes(t)
        _drag(t, 0, -100)
        assert _sizes(t)[0] == fitted[0] - 100 and tables.has_user_widths(t)
        _drag(t, 0, 100)
        assert abs(_sizes(t)[0] - fitted[0]) <= 2, (cols, fitted, _sizes(t))
        # a save that changes nothing takes nothing off the main column
        main = _sizes(t)[0]
        t.header().sectionHandleDoubleClicked.emit(len(cols) - 1)
        _pump()
        assert _sizes(t)[0] == main
        t.close()
        t.deleteLater()
        t2 = _reopen(key, make, 420, stretch=0, fit_view=True)
        assert abs(_sizes(t2)[0] - fitted[0]) <= 2, (cols, fitted, _sizes(t2))
        t2.close()
        t2.deleteLater()


def test_the_last_column_is_remembered_at_its_own_width_not_its_stretch():
    """Default layout, no main column: the user widens column 0 in a wide view. Reopened in a
    narrow view it does not scroll. A double-click on the last divider saves its text width,
    not the fill."""
    _app()
    from ctgedit.ui import tables
    from ctgedit.ui.prefs.settings import app_settings

    rows = [("item %d" % k, "v%d" % k, "a note that is fairly long here %d" % k) for k in range(5)]

    def make():
        return _tree(rows, ("Name", "Value", "Note"))

    t = _reopen("Probe/lastown", make, 900)
    _drag(t, 0, 10)
    widths, view_w = tables.parse_widths(app_settings().value("columns/Probe/lastown"))
    assert widths[2] < view_w - widths[0] - widths[1] - 100  # (not the stretched 814)
    t.close()
    t.deleteLater()
    t2 = _reopen("Probe/lastown", make, 450)
    assert tables.has_user_widths(t2)
    assert _sizes(t2)[0] == widths[0]
    assert t2.horizontalScrollBar().maximum() == 0
    assert sum(_sizes(t2)) <= t2.viewport().width() + 1
    t2.resize(900, 200)
    _pump()
    t2.resize(450, 200)
    _pump()
    assert t2.horizontalScrollBar().maximum() == 0
    t2.header().sectionHandleDoubleClicked.emit(2)
    _pump()
    widths, view_w = tables.parse_widths(app_settings().value("columns/Probe/lastown"))
    assert widths[2] <= max(t2.sizeHintForColumn(2), t2.header().sectionSizeHint(2)) + 1
    t2.close()
    t2.deleteLater()


def test_fit_view_a_drag_in_a_narrow_view_keeps_the_other_columns_widths():
    """The user's widths, squeezed in a narrow panel: a nudge of one divider saves that column
    only; the others come back to the user's widths when the panel is wide again."""
    _app()
    from ctgedit.ui import tables

    rows = [("Plant.Mixer.Tank%d.Level" % k, "12.5", "Good") for k in range(5)]
    t = _reopen(
        "Probe/squeezed", lambda: _tree(rows, ("Tag", "Value", "Quality")), 600, stretch=0, fit_view=True
    )
    try:
        _drag(t, 1, 80)
        wide = _sizes(t)
        assert tables.has_user_widths(t)
        t.resize(220, 200)
        _pump()
        narrow = _sizes(t)
        assert narrow[1] < wide[1] or narrow[2] < wide[2]  # (squeezed)
        _drag(t, 1, 3)
        t.resize(600, 200)
        _pump()
        got = _sizes(t)
        assert got[1] == narrow[1] + 3  # the column the user nudged
        assert got[2] == wide[2]  # the one they did not: theirs again
        assert sum(got) <= t.viewport().width() + 2
    finally:
        t.close()
        t.deleteLater()


def test_a_click_on_a_title_while_the_view_resizes_is_not_a_user_change():
    """A click on a title (to sort) while rows come in and the scroll bar appears: Qt
    stretches the last column during the press. Not the user's: nothing is remembered and
    the log keeps widening its columns."""
    _app()
    from ctgedit.ui import tables
    from ctgedit.ui.prefs.settings import app_settings

    for fit_view in (False, True):
        key = "Probe/sortclick%d" % fit_view
        t = _tree([("0.%d s" % k, "w", "Tank.Level = %d" % k) for k in range(3)], ("Time", "Kind", "Message"))
        t.setSortingEnabled(True)
        tables.make_resizable(t, key, stretch=2, grow=not fit_view, fit_view=fit_view)
        t.resize(500, 200)
        t.show()
        _pump()

        def rows():
            for k in range(40):
                t.addTopLevelItem(QtWidgets.QTreeWidgetItem(["1%d.0 s" % k, "write", "Tank.Level = %d" % k]))

        _press_release_on_title(t, rows)
        assert t.verticalScrollBar().maximum() > 0
        assert not tables.has_user_widths(t), fit_view
        assert not app_settings().value(tables.settings_key(key)), fit_view
        # a real drag of a divider still counts
        _drag(t, 0, 30)
        assert tables.has_user_widths(t), fit_view
        t.close()
        t.deleteLater()


def test_widths_saved_in_the_older_form_are_not_used():
    """Stored widths with no view width (sums wider than the screen) are not used: the table
    fits its text."""
    _app()
    from ctgedit.ui import tables
    from ctgedit.ui.prefs.settings import app_settings

    assert tables.parse_widths("67,50,102,506,255,1555") is None
    assert tables.parse_widths("67,50,v400") == ([67, 50], 400)
    key = "Probe/olderform"
    app_settings().setValue(tables.settings_key(key), "67,50,102,506,255,1555")
    msg = "Tank1.Level = 3 and a longer message text here"
    t = _tree(
        [("0.25 s", "", "Tag write", msg, "PageDisplay, citect.ci:2594", "")],
        ("Time", "Count", "Kind", "Message", "Where", "Call"),
    )
    tables.make_resizable(t, key, stretch=3, caps={0: 96, 3: 0.6, 4: 0.4, 5: 0.3}, grow=True)
    t.resize(1000, 300)
    t.show()
    _pump()
    try:
        assert not tables.has_user_widths(t)
        assert t.header().sectionSize(3) >= t.fontMetrics().horizontalAdvance(msg)
        assert t.horizontalScrollBar().maximum() == 0
    finally:
        t.close()
        t.deleteLater()


def test_fit_view_the_last_columns_stretch_gives_way_before_the_main_column():
    """Simulate Inputs: in a wide floating window the user drags the Tag divider far left
    (Quality stretched). Docked narrow, the tag names keep their width while Quality is back
    to its own."""
    _app()
    from ctgedit.ui import tables

    key = "Probe/inputs"

    def make():
        t = QtWidgets.QTreeWidget()
        t.setHeaderLabels(["Tag", "Value", "Quality", "Hidden"])
        for k in range(6):
            t.addTopLevelItem(
                QtWidgets.QTreeWidgetItem(["Plant.Mixer.Tank%d.Level_PV" % k, "12.5", "Good", ""])
            )
        t.setColumnHidden(3, True)
        t.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        return t

    kw = dict(stretch=0, caps={1: 0.22, 3: 0.3}, fixed={2: "Uncertain"}, fit_view=True)
    t = _reopen(key, make, 1200, **kw)
    quality = _sizes(t)[2]
    _drag(t, 0, -700)
    wide = _sizes(t)
    assert wide[2] > 500 and tables.has_user_widths(t)  # (Qt's stretch)
    t.close()
    t.deleteLater()
    t2 = _reopen(key, make, 300, **kw)
    try:
        got = _sizes(t2)
        need = t2.sizeHintForColumn(0)
        if t2.viewport().width() - got[1] - quality >= need:
            assert got[0] >= need, got
        assert got[2] <= quality + 2, got
        assert sum(got) <= t2.viewport().width() + 2
        t2.resize(1200, 200)
        _pump()
        assert _sizes(t2)[0] == wide[0]  # the user's width, wide again
    finally:
        t2.close()
        t2.deleteLater()


def test_a_row_that_comes_during_a_click_on_a_title_is_laid_out_after_it():
    """The log widens a column for a new row while the button is down on a title: the main
    column shrinks to match after the release."""
    _app()
    from ctgedit.ui import tables

    t = _tree([("0.25 s", "a message", "here", "1")], ("Time", "Message", "Where", "N"))
    t.setSortingEnabled(True)
    t.sortByColumn(0, QtCore.Qt.AscendingOrder)
    tables.make_resizable(t, "Probe/growclick", stretch=1, caps={0: 96, 1: 0.45, 2: 0.4, 3: 50}, grow=True)
    t.resize(600, 300)
    t.show()
    _pump()
    try:
        where = t.header().sectionSize(2)
        _press_release_on_title(
            t,
            lambda: t.addTopLevelItem(
                QtWidgets.QTreeWidgetItem(["1.0 s", "b", "a_long_function_name, a_file_name.ci:1234", "2"])
            ),
        )
        assert t.header().sectionSize(2) > where  # (it widened)
        assert not tables.has_user_widths(t)
        assert sum(_sizes(t)) <= t.viewport().width() + 1
        assert t.horizontalScrollBar().maximum() == 0
    finally:
        t.close()
        t.deleteLater()


def test_with_no_main_column_a_fit_gives_the_last_column_the_room_left():
    """Insert Tag: a table without a main column, the Comment column last with long text.
    The first fit gives Comment the room the others leave; a wider view still stretches it."""
    _app()
    from PySide6 import QtGui
    from ctgedit.ui import tables

    for long in (True, False):
        dlg = QtWidgets.QDialog()
        dlg.resize(640, 460)
        lay = QtWidgets.QVBoxLayout(dlg)
        m = QtGui.QStandardItemModel(0, 5)
        m.setHorizontalHeaderLabels(["Name", "Kind", "Type", "Project", "Comment"])
        note = "The level of the mixing tank as measured by the radar transmitter" if long else "Level"
        for k in range(40):
            m.appendRow(
                [
                    QtGui.QStandardItem(x)
                    for x in ("FxTank%d_Level_PV" % k, "Variable tag", "REAL", "FxMain", note)
                ]
            )
        v = QtWidgets.QTableView()
        v.setModel(m)
        v.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(v)
        for c in (1, 2, 3):
            v.resizeColumnToContents(c)
        v.setColumnWidth(0, 240)
        tables.make_resizable(v, "Probe/tagpicker%d" % long)
        dlg.show()
        _pump()
        try:
            assert v.horizontalScrollBar().maximum() == 0, (long, _sizes(v))
            assert sum(_sizes(v)) <= v.viewport().width() + 1
            assert _sizes(v)[0] >= 240
            dlg.resize(1000, 460)
            _pump()
            assert sum(_sizes(v)) >= v.viewport().width() - 1
        finally:
            dlg.close()
            dlg.deleteLater()
            _pump()


def test_a_cut_cell_shows_its_whole_text_as_the_tool_tip(monkeypatch):
    _app()
    from PySide6 import QtGui
    from ctgedit.ui import tables

    long = "a message that does not fit in the narrow column at all " * 2
    t = _tree([(long, "1", "x")])
    tables.make_resizable(t, "Probe/tip", fit_on_first_fill=False)
    t.resize(400, 200)
    t.show()
    _pump()
    t.header().resizeSection(0, 60)
    shown = []
    monkeypatch.setattr(QtWidgets.QToolTip, "showText", lambda pos, text, *a, **k: shown.append(text))
    rect = t.visualRect(t.model().index(0, 0))
    ev = QtGui.QHelpEvent(QtCore.QEvent.ToolTip, rect.center(), t.viewport().mapToGlobal(rect.center()))
    QtWidgets.QApplication.sendEvent(t.viewport(), ev)
    assert shown and "does not fit" in shown[0]
    t.close()


# -- the Simulate Outputs log ----------------------------------------------------
#: a Cicode file a message names (only shown, never read)
CITECT_CI = r"C:\FxInstall\User\Include\citect.ci"


def _log_panel():
    _app()
    from ctgedit.ui.simulate.panels import OutputsPanel
    from ctgedit.ui.simulate.sources import LogEntry

    p = OutputsPanel()
    p.resize(900, 400)
    p.show()
    _pump()
    p.add_entries(
        [
            LogEntry(0.25, "write", "Tank1.Level = 3"),
            LogEntry(
                1.5,
                "error",
                "prompt: Cannot Display Page\nPage FxGone2 not found",
                function="PageDisplay",
                file=CITECT_CI,
                line=2594,
            ),
        ]
    )
    _pump()
    return p


def test_log_where_is_short_and_fits_without_dragging():
    p = _log_panel()
    try:
        from ctgedit.ui.simulate.panels import COL_MSG, COL_WHERE

        rows = p.entries()
        assert rows[1].text(COL_WHERE) == "PageDisplay, citect.ci:2594"
        assert CITECT_CI in rows[1].toolTip(COL_WHERE)
        assert "Page FxGone2 not found" in rows[1].toolTip(COL_MSG)
        hdr = p.log.header()
        fm = p.log.fontMetrics()
        # Where and Message show their whole text
        assert hdr.sectionSize(COL_WHERE) >= fm.horizontalAdvance(rows[1].text(COL_WHERE))
        assert hdr.sectionSize(COL_MSG) >= fm.horizontalAdvance(rows[1].text(COL_MSG))
        assert all(hdr.sectionResizeMode(c) == QtWidgets.QHeaderView.Interactive for c in range(5))
    finally:
        p.deleteLater()
        _pump()


def test_log_details_box_shows_the_whole_selected_line():
    p = _log_panel()
    try:
        p.log.setCurrentItem(p.entries()[1])
        _pump()
        text = p.details.toPlainText()
        assert "1.50 s" in text and "Error" in text
        assert "Where: PageDisplay (%s:2594)" % CITECT_CI in text
        assert "prompt: Cannot Display Page\nPage FxGone2 not found" in text
        assert p.details.isReadOnly()
        p.clear()
        assert p.details.toPlainText() == ""
    finally:
        p.deleteLater()
        _pump()


def test_log_details_box_is_as_high_as_the_user_drags_it_and_remembers():
    """The details start six lines high (the list gets the rest), have no upper limit, and the
    height the user drags them to is kept for the next window."""
    from ctgedit.ui.prefs.settings import app_settings

    p = _log_panel()
    try:
        d = p.details
        # (shown with a line selected: empty, it is hidden)
        assert d.isHidden()
        p.log.setCurrentItem(p.entries()[0])
        _pump()
        assert not d.isHidden()
        split = d.parentWidget()
        assert isinstance(split, QtWidgets.QSplitter)
        assert d.maximumHeight() == 16777215
        six = d.fontMetrics().lineSpacing() * 6 + 12
        assert abs(d.height() - six) <= 2 and p.log.height() > d.height()
        p.resize(900, 700)
        _pump()
        assert abs(d.height() - six) <= 2  # (the list took the new height)
        k = split.indexOf(d)
        total = sum(split.sizes())
        split.setSizes([total - 260, 260] if k == 1 else [260, total - 260])
        split.splitterMoved.emit(total - 260, 1)
        _pump()
        assert d.height() >= 250
        assert int(app_settings().value("simulate/log_details_height", 0)) == split.sizes()[k]
        # a taller window gives the height to the list, not to the details
        before = d.height()
        p.resize(900, 900)
        _pump()
        assert abs(d.height() - before) <= 2 and p.log.height() > 300
    finally:
        p.deleteLater()
        _pump()
    p2 = _log_panel()
    try:
        p2.log.setCurrentItem(p2.entries()[0])
        _pump()
        # (remembered; a window too short for it gives what it has)
        assert p2.details.height() > 2 * six
        p2.resize(900, 900)
        _pump()
        assert abs(p2.details.height() - before) <= 2
    finally:
        p2.deleteLater()
        _pump()


def test_log_details_keep_their_place_when_lines_come_in():
    """Reading the details of a long line: a new line in the list leaves them where the user
    scrolled; another row starts at the top."""
    from ctgedit.ui.simulate.sources import LogEntry

    p = _log_panel()
    try:
        long = "\n".join("line %d of a long message" % k for k in range(60))
        p.add_entries([LogEntry(2.0, "error", long, function="Fn", file=CITECT_CI, line=7)])
        _pump()
        row = p.entries()[-1]
        p.log.setCurrentItem(row)
        _pump()
        bar = p.details.verticalScrollBar()
        assert bar.maximum() > 10
        bar.setValue(bar.maximum() // 2)
        place = bar.value()
        cur = p.details.textCursor()
        cur.setPosition(40)
        cur.setPosition(70, cur.MoveMode.KeepAnchor)
        p.details.setTextCursor(cur)
        bar.setValue(place)
        picked = p.details.textCursor().selectedText()
        for k in range(5):
            p.add_entries([LogEntry(3.0 + k, "write", "Tank1.Level = %d" % k)])
            _pump()
        assert p.log.currentItem() is row
        assert bar.value() == place
        assert p.details.textCursor().selectedText() == picked
        # the same line again (its group's count moves on): the text changes, the place stays
        p.add_entries([LogEntry(9.0, "error", long, function="Fn", file=CITECT_CI, line=7)])
        _pump()
        if p.log.currentItem() is row and "Happened 2 times" in p.details.toPlainText():
            assert bar.value() == place
        # another row: from the top
        p.log.setCurrentItem(p.entries()[0])
        _pump()
        assert bar.value() == 0
    finally:
        p.deleteLater()
        _pump()


def test_log_copy_puts_the_selected_rows_on_the_clipboard_as_tsv():
    p = _log_panel()
    try:
        rows = p.entries()
        rows[0].setSelected(True)
        rows[1].setSelected(True)
        acts = [a for a in p.log.actions() if a.text() == "Copy"]
        assert (
            acts
            and acts[0]
            .shortcut()
            .matches(__import__("PySide6.QtGui", fromlist=["QKeySequence"]).QKeySequence("Ctrl+C"))
            is not None
        )
        # (the rows' own menu: hide / show only / Show on Page, and Copy)
        assert p.log.contextMenuPolicy() == QtCore.Qt.CustomContextMenu
        acts[0].trigger()
        got = QtWidgets.QApplication.clipboard().text().split("\n")
        # Time, Count, Kind, Message, Where, Call
        assert got[0] == "0.25 s\t\tTag write\tTank1.Level = 3\t\t"
        assert got[1].split("\t")[1:] == [
            "",
            "Cicode message",
            "prompt: Cannot Display Page Page FxGone2 not found",
            "PageDisplay, citect.ci:2594",
            "",
        ]
        # the Not Simulated tab copies too
        from ctgedit.ui.simulate.sources import LogEntry

        p.add_entries(
            [LogEntry(2.0, "not_simulated", "SQLConnect is not simulated", function="SQLConnect", count=3)]
        )
        it = p.not_sim.topLevelItem(0)
        p.not_sim.setCurrentItem(it)
        it.setSelected(True)
        assert "Called 3 times" in p.not_sim_details.toPlainText()
        [a for a in p.not_sim.actions() if a.text() == "Copy"][0].trigger()
        assert QtWidgets.QApplication.clipboard().text() == "SQLConnect\t3\t"  # (Function, Calls, Why)
    finally:
        QtWidgets.QApplication.clipboard().clear()
        p.deleteLater()
        _pump()
