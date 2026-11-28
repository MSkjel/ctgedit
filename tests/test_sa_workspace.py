"""Situational Awareness authoring aids."""

import os
from qtkit import app as _app


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

#: the workspace master page and the nested-pane pages of FxSa
MASTER, NESTED, LOOPING = "FxSaMaster", "FxSaNested1", "FxSaNested2"
MASTER_PANES = ["Content", "HeaderBar", "FacePlate", "FacePlateHeader", "Navigation", "Info", "InfoHeader"]


def _pane(i, name="", **values):
    from ctgedit.features import sa_workspace as SA

    vals = {"name": name}
    vals.update({k.lower(): v for k, v in values.items()})
    return SA.Pane(i, i, (10 * i, 0, 10 * i + 100, 50), vals, i + 1)


def _ctx(
    record=None,
    env=None,
    pages=("BlankPage_HD1080", "Header_HD1080"),
    types=("l1", "l2", "fp", "general", "master"),
    names=None,
    resolution="HD1080",
):
    from ctgedit.features import sa_workspace as SA

    low = {p.lower() for p in pages}
    return SA.PageContext(
        "Master_Test_HD1080",
        {"READY_FUNC": "Workspace_Init()", "CON_TYPE": "Master"} if record is None else record,
        env or {},
        resolution,
        lambda n: n.lower() in low,
        set(types),
        lambda page: (names or {}).get(page.lower()),
    )


def _codes(rep, level=None):
    return [f.code for f in rep.findings if level is None or f.level == level]


def _sa(fx_install):
    """The FxSa project folder of the fx_install copy."""
    return fx_install.project("FxSa")


def _master(fx_install):
    """FxSaMaster of the fx_install copy (writable)."""
    return fx_install.page(MASTER, "FxSa")


#: the menu configuration _write_menus writes: (menu, levels, target page, command).
#: 'Navigation' is the menu the navigation genies read (io.project.NAVIGATION_MENU).
MENUS = (
    ("Navigation", ("FxPlant", "Fx & Area", "FxContent (Fx3)"), "FxSaContent", 'FxShow("FxSaContent")'),
    ("Navigation", ("FxPlant", "Fx & Area", "FxSupply (Fx3)"), "FxSaBlank", 'FxShow("FxSaBlank")'),
    ("FxHeaderbar", ("Home",), "FxSaContent", 'FxShow("FxSaContent")'),
    ("FxHelp", ("@(FxValves)",), "FxSaBlank", 'FxShow("FxSaBlank")'),
    ("FxInfoZone", ("Info",), "FxSaBlank", 'FxShow("FxSaBlank")'),
)
CONTENT_PATH = "FxPlant.Fx & Area.FxContent (Fx3)"


def _write_menus(fx_install):
    """FxSa's menu configuration (pagemenu.DBF: PAGE = the menu, LEVEL1 .. LEVEL6,
    COMMAND, TARGETPAGE, COMMENT, ORDER), written into the copy in place of the fake
    install's own (the Menu Configuration form's layout, format/dbfschema)."""
    from ctgedit.format import dbfschema

    rows = []
    for k, (menu, levels, target, cmd) in enumerate(MENUS):
        row = {"PAGE": menu, "COMMAND": cmd, "TARGETPAGE": target, "ORDER": str(k + 1)}
        row.update({"LEVEL%d" % (n + 1): v for n, v in enumerate(levels)})
        rows.append(row)
    dbfschema.create_table(os.path.join(_sa(fx_install), "pagemenu.DBF"), "pagemenu", rows)


# ------------------------------------------------------------ the rules
def test_two_default_panes_give_a_warning():
    from ctgedit.features import sa_workspace as SA

    panes = [
        _pane(0, "Content", IsDefault="TRUE"),
        _pane(1, "Header", IsDefault="1"),
        _pane(2, "Info", IsDefault="FALSE"),
    ]
    rep = SA.check_panes(panes, _ctx())
    assert _codes(rep, SA.WARNING) == ["two-defaults"]
    f = next(f for f in rep.findings if f.code == "two-defaults")
    assert f.pane.name == "Header" and f.index == 1 and "Content" in f.message
    assert rep.master and rep.summary() == "1 warning"
    # one default = nothing to report
    panes[1].values["isdefault"] = "FALSE"
    assert SA.check_panes(panes, _ctx()).findings == []


def test_pane_names_missing_and_twice():
    from ctgedit.features import sa_workspace as SA

    panes = [_pane(0, "Content", IsDefault="TRUE"), _pane(1, ""), _pane(2, "content")]
    rep = SA.check_panes(panes, _ctx())
    assert _codes(rep, SA.ERROR) == ["no-name", "duplicate-name"]
    assert [f.pane.index for f in rep.findings if f.level == SA.ERROR] == [1, 2]
    # the canvas marks the same problems
    assert SA.local_problems(panes) == {1: "no name", 2: "name used twice"}


def test_master_page_and_haspanes_rules():
    from ctgedit.features import sa_workspace as SA

    panes = [_pane(0, "Content", IsDefault="TRUE")]
    # a workspace master page: Workspace_Init() in On page shown + Master
    assert SA.check_panes(panes, _ctx()).findings == []
    # content type Master without Workspace_Init -> the panes never exist
    rep = SA.check_panes(panes, _ctx({"CON_TYPE": "Master", "READY_FUNC": ""}))
    assert _codes(rep) == ["init-missing"] and rep.findings[0].field == "On page shown"
    # Workspace_Init() in the wrong event
    rep = SA.check_panes(panes, _ctx({"CON_TYPE": "Master", "ENTRY_FUNC": "Workspace_Init();"}))
    assert _codes(rep) == ["init-wrong-event"]
    # Workspace_Init() but content type General
    rep = SA.check_panes(panes, _ctx({"CON_TYPE": "General", "READY_FUNC": "Workspace_Init()"}))
    assert _codes(rep) == ["contype-not-master"]
    # a page with nested panes: HasPanes = 1 (the string "1")
    nested = {"CON_TYPE": "", "READY_FUNC": ""}
    assert _codes(SA.check_panes(panes, _ctx(nested))) == ["haspanes-missing"]
    assert _codes(SA.check_panes(panes, _ctx(nested, {"HasPanes": "TRUE"}))) == ["haspanes-value"]
    assert SA.check_panes(panes, _ctx(nested, {"hasPanes": "1"})).findings == []
    # a workspace needs a default pane; a nested page does not
    none = [_pane(0, "Content")]
    assert _codes(SA.check_panes(none, _ctx())) == ["no-default"]
    assert SA.check_panes(none, _ctx(nested, {"HasPanes": "1"})).findings == []
    # an unregistered page: its events cannot be checked
    rep = SA.check_panes(panes, SA.PageContext("New", None))
    assert _codes(rep) == ["page-unregistered"] and rep.findings[0].level == SA.INFO


def test_pane_values_and_references():
    from ctgedit.features import sa_workspace as SA

    panes = [
        _pane(
            0,
            "Content",
            IsDefault="TRUE",
            FillMode="Autofil",
            DisplayMode="Stretch",
            DefaultPage="BlankPage_UHD4K",
            ContentTypes="L1,XX",
        ),
        _pane(
            1, "Header", DefaultPage="Header_HD1080", DisplayMode="PANE_DISPLAYMODE_x", HasScrollbars="yes"
        ),
        _pane(
            2,
            "Body",
            FillMode="PANE_FILLMODE_AutofillContextMustMatch",
            TabPane="Header",
            TabControlName="Tabs",
            DefaultPage="Master_Test_HD1080",
        ),
        _pane(3, "Side", DisplayMode="2", HasScrollbars="TRUE", TabPane="Nowhere", DefaultPage="Gone"),
        _pane(4, "Info", TabControlName="Tabs", FillMode="4", DisplayMode="MaintainSize", HasScrollbars="1"),
    ]
    rep = SA.check_panes(panes, _ctx(names={"header_hd1080": {"tabbar", "tabbar.tab_1"}}))
    by = {}
    for f in rep.findings:
        by.setdefault(f.pane.name, []).append(f.code)
    # a pane's findings: errors first, then warnings, then notes
    assert by["Content"] == ["default-page-missing", "fill-mode", "default-page-resolution", "content-type"]
    # (HasScrollbars 'yes' reads as FALSE: no scroll-bar note)
    assert by["Header"] == ["bool-value", "display-mode"]
    # an Autofill pane with no content types that is not the default pane
    assert by["Body"] == ["default-page-loop", "tab-control-missing", "autofill-no-types"]
    assert by["Side"] == ["default-page-missing", "tab-pane-missing", "scrollbars"]
    assert by["Info"] == ["tab-control-unused"]  # Maintain size: scrollbars fine
    missing = next(f for f in rep.findings if f.code == "tab-control-missing")
    assert "'Tabs'" in missing.message and "Header_HD1080" in missing.message
    # more panes than the runtime creates (it counts the ones it created:
    # one per distinct name)
    many = [_pane(0, "Content", IsDefault="TRUE")] + [_pane(k, "P%d" % k) for k in range(1, 51)]
    assert "too-many" in _codes(SA.check_panes(many, _ctx()))
    many[-1].values["name"] = "P1"
    assert "too-many" not in _codes(SA.check_panes(many, _ctx()))
    # the runtime's parsers
    assert SA.str_to_bool("TRUE") == (True, True) and SA.str_to_bool("") == (False, True)
    assert SA.str_to_bool("0") == (False, True) and SA.str_to_bool("yes") == (False, False)
    assert panes[2].fill_mode == 3 and panes[4].fill_mode == 4 and panes[0].fill_mode is None
    assert panes[3].display_mode == 2 and panes[4].display_mode == 3
    assert SA.page_resolution("X", "pagecontent@-4K") == "UHD4K"
    assert SA.page_resolution("Page_HD1080") == "HD1080"
    assert SA.page_resolution("Page", "", 3840) == "UHD4K"


def test_fixture_workspace_pages_are_checked(fx_install):
    """FxSa's workspace and nested-pane pages: their panes, default pages and tab bars
    resolve. The one problem the fixture holds: the Info pane's Display Mode
    'PANE_DISPLAYMODE_MAINTAIN' is not a mode the runtime knows (a warning); FxSaNested2's
    pane shows its own page (an error)."""
    from ctgedit.features import sa_workspace as SA
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(_master(fx_install))
    rep = SA.check_document(d)
    assert [p.name for p in rep.panes] == MASTER_PANES
    assert rep.master
    assert [p.name for p in rep.panes if p.is_default] == ["Content"]
    assert [p.fill_mode_text() for p in rep.panes][:3] == ["Autofill", "Static", "Autofill"]
    assert [(f.code, f.pane.name) for f in rep.findings if f.level != SA.INFO] == [("display-mode", "Info")]
    face = next(p for p in rep.panes if p.name == "FacePlate")
    assert face.tab_pane == "FacePlateHeader" and face.tab_control == "FxTabBar"
    # the pane record is the one the runtime reads; the unit is its genie
    assert all(d.page.objects[p.index].raw.get("_name") == "Pane" for p in rep.panes)
    assert all(d.genie_link(p.root)[:2] == ("fxws", "pane") for p in rep.panes)
    rep = SA.check_document(EditableDocument.open(fx_install.page(NESTED, "FxSa")))
    assert rep.panes and not rep.master
    assert not [f for f in rep.findings if f.level != SA.INFO], rep.findings
    rep = SA.check_document(EditableDocument.open(fx_install.page(LOOPING, "FxSa")))
    assert rep.panes and not rep.master
    assert [f.code for f in rep.findings if f.level != SA.INFO] == ["default-page-loop"]
    # the header pane's page holds the tab bar the FacePlate pane names
    p = SA.find_page_file(_sa(fx_install), "FxSaHeader")
    assert p and "fxtabbar" in SA.page_object_names(p)
    assert "FxTabBar" in SA.page_top_level_names(p)


# ------------------------------------------------------------ the overlay
def _render(scene, w, h, export=False):
    from PySide6 import QtCore, QtGui

    img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
    img.fill(QtCore.Qt.white)
    p = QtGui.QPainter(img)
    try:
        if export:
            with scene.export_only(None):
                scene.render(p, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(0, 0, 1920, 1080))
        else:
            scene.render(p, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(0, 0, 1920, 1080))
    finally:
        p.end()
    return img


def test_pane_labels_on_the_canvas_never_in_an_export(fx_install):
    """The master page shows its panes' names; a print / thumbnail / bitmap
    render (export_only) has no overlay pixels."""
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.render.scene import PageScene

    d = EditableDocument.open(_master(fx_install))
    sc = PageScene()
    sc.set_page(d.page)
    labels = sc.pane_labels()
    assert [t for _i, _r, _b, t, _d, _p in labels] == MASTER_PANES
    content = labels[0]
    assert "Autofill" in content[4] and "default pane" in content[4]
    assert "FxSaContent" in content[4] and not content[5]
    on = _render(sc, 960, 540)
    ex_on = _render(sc, 960, 540, export=True)
    sc.set_pane_labels_visible(False)
    assert sc.pane_labels() == []
    off = _render(sc, 960, 540)
    ex_off = _render(sc, 960, 540, export=True)
    assert on != off  # the editor shows the labels
    assert ex_on == ex_off  # an export never does
    # ... through the canvas's own paint path too (QGraphicsView.paintEvent)
    from ctgedit.ui.editor.canvas import CanvasView

    view = CanvasView()
    view.setScene(sc)
    view.resize(800, 500)
    view.centerOn(400, 250)
    shot_off = view.grab().toImage()
    sc.set_pane_labels_visible(True)
    shot_on = view.grab().toImage()
    assert shot_on != shot_off
    sc.set_pane_labels_visible(False)
    assert view.grab().toImage() == shot_off
    view.setScene(None)
    view.deleteLater()
    # a library element window has no page panes
    sc.set_pane_labels_visible(True)
    sc.set_element_mode(True)
    assert sc.pane_labels() == []
    sc.set_element_mode(False)
    assert len(sc.pane_labels()) == 7


def test_page_browser_preview_has_no_pane_labels(fx_install, monkeypatch):
    """The page browser's rendered preview is a picture of the page: the
    editor's pane badges are not drawn there."""
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.render.scene import PageScene
    from ctgedit.ui.browser.preview import render_page_image

    d = EditableDocument.open(_master(fx_install))
    drawn = []
    real = PageScene._draw_pane_labels
    monkeypatch.setattr(
        PageScene, "_draw_pane_labels", lambda self, p, r: (drawn.append(1), real(self, p, r))
    )
    img = render_page_image(d.page, 480, 270)
    assert not img.isNull() and drawn == []
    sc = PageScene()  # the editor's scene does draw them
    sc.set_page(d.page)
    _render(sc, 480, 270)
    assert drawn


def test_pane_labels_follow_an_edit(fx_install, monkeypatch):
    """Renaming a pane (its genie re-composed) or marking a second default
    pane updates the label in the patched scene (update_units)."""
    _app()
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")
    from ctgedit.editing import EditableDocument
    from ctgedit.render.scene import PageScene

    d = EditableDocument.open(_master(fx_install))
    sc = PageScene()
    sc.set_page(d.page)
    serial = d.derive_serial if hasattr(d, "derive_serial") else None
    header = next(r for _i, r, _b, t, _d, _p in sc.pane_labels() if t == "HeaderBar")
    assert d.set_genie_bindings(header, {"Name": "Top", "IsDefault": "TRUE"})
    roots = d.model_changes_since(serial) if serial is not None else None
    if roots is None or not sc.update_units(d.page, roots):
        sc.set_page(d.page)
    labels = {t: (det, prob) for _i, _r, _b, t, det, prob in sc.pane_labels()}
    assert "HeaderBar" not in labels and "Top" in labels
    assert "default pane" in labels["Top"][0]
    assert labels["Top"][1] == "second default pane"


# ------------------------------------------------------ checks on save
def test_save_runs_the_checks_and_reports(fx_install):
    """Saving a page with panes runs the workspace checks: two default panes
    give a warning in the Workspace Checks panel and the status message (besides the
    fixture's own warning: the Info pane's unknown Display Mode); the save itself is
    unchanged; the thumbnail never has pane labels."""
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.features import sa_workspace as SA

    path = _master(fx_install)
    win = MainWindow()
    try:
        win.open_page(path)
        doc = win.doc
        header = next(p.root for p in SA.model_panes(doc.page) if p.name == "HeaderBar")
        assert doc.set_genie_bindings(header, {"IsDefault": "TRUE"})
        win._refresh()
        assert win.act_pane_labels.isChecked()
        # a thumbnail render is the same with or without the labels
        win._render_thumbnail()
        thumb_on = bytes(doc.parsed.page.thumbnail or b"")
        win.scene.set_pane_labels_visible(False)
        win._render_thumbnail()
        assert bytes(doc.parsed.page.thumbnail or b"") == thumb_on
        win.scene.set_pane_labels_visible(True)
        assert win.save()
        assert not win.dock_sa_checks.isHidden()
        rep = win.sa_checks.report
        assert rep is not None and rep.master
        assert sorted(f.code for f in rep.findings if f.level == SA.WARNING) == [
            "display-mode",
            "two-defaults",
        ]
        assert "Workspace checks: 2 warnings" in win.statusBar().currentMessage()
        row = next(
            k
            for k in range(win.sa_checks.tree.topLevelItemCount())
            if win.sa_checks.finding(k).code == "two-defaults"
        )
        win.sa_checks.activate_row(row)  # selects the pane's genie
        assert win.doc.root_of(win._sel[0]) == header
        # View > Pane Labels turns them off (and is remembered)
        win.act_pane_labels.trigger()
        assert not win.act_pane_labels.isChecked() and win.scene.pane_labels() == []
        win.act_pane_labels.trigger()
        assert len(win.scene.pane_labels()) == 7
        # a row of an earlier check still finds its pane after an edit moved
        # the records (the pane genie brought to the front)
        new_root = doc.reorder([header], "front")[0]
        win._refresh()
        assert new_root != header
        win._sel = []
        win.sa_checks.activate_row(row)
        assert win._sel and win.doc.root_of(win._sel[0]) == new_root
        # ... and says so when the pane is gone (renamed since the check)
        assert doc.set_genie_bindings(new_root, {"Name": "Top"})
        win._refresh()
        win._sel = []
        win.sa_checks.activate_row(row)
        assert not win._sel and "no longer on the page" in win.statusBar().currentMessage()
        doc.undo()
        doc.undo()
        win._refresh()
        # a page finding opens Page Properties on the tab that fixes it
        win._page_properties_tab("Events")
        dlg = win._dialogs.get(("pageprops", "%x" % id(win.doc)))
        assert dlg is not None and dlg._tabs.tabText(dlg._tabs.currentIndex()) == "Events"
        dlg.reject()
        # the page on disk is what was saved (the checks change nothing)
        from ctgedit.editing import EditableDocument

        d2 = EditableDocument.open(path)
        assert d2.genie_all_params(header)["IsDefault"] == "TRUE"
        # 'Check when saving' off: a save does not check again
        win.sa_checks.on_save.setChecked(False)
        win.sa_checks.clear()
        win.dock_sa_checks.hide()
        doc.dirty = True
        assert win.save()
        assert win.sa_checks.report is None and win.dock_sa_checks.isHidden()
        # Tools > Check Workspace still does
        win.act_check_workspace.trigger()
        assert win.sa_checks.report is not None and not win.dock_sa_checks.isHidden()
    finally:
        win.doc.dirty = False
        win.close()
        QtWidgets.QApplication.processEvents()


def test_pages_without_panes_are_not_checked(tmp_path):
    from ctgedit.features import sa_workspace as SA
    from ctgedit.model.document import Page

    assert SA.model_panes(Page()) == [] and SA.model_panes(None) == []

    class _Doc:
        page = Page()
        path = str(tmp_path / "X.ctg")

    rep = SA.check_document(_Doc())
    assert rep.panes == [] and rep.findings == [] and not SA.has_panes(_Doc())


# ------------------------------------------------------------ pickers
def test_menu_configuration_readers(fx_install):
    from ctgedit.io import project as PR

    _write_menus(fx_install)
    sa = _sa(fx_install)
    items = PR.menu_items(sa, "Navigation")
    paths = [it.path for it in items]
    assert CONTENT_PATH in paths
    content = next(it for it in items if it.path == CONTENT_PATH)
    assert content.target_page == "FxSaContent" and content.command == 'FxShow("FxSaContent")'
    all_paths = PR.menu_paths(sa)
    assert all_paths.index("FxPlant") < all_paths.index("FxPlant.Fx & Area") < all_paths.index(CONTENT_PATH)
    pages = PR.menu_pages(sa)
    for pg in ("Navigation", "FxHeaderbar", "FxHelp", "FxInfoZone"):
        assert pg in pages
    assert PR.menu_items(sa, "navigation")  # case-insensitive, like the runtime


def test_the_fake_installs_own_menus_read_as_menus(fx_install):
    """The committed pagemenu.DBF tables as they are (no _write_menus): FxSa's PAGE is the
    menu and LEVEL1 .. the path in it (as Studio's Menu Configuration, ExampleSA's own);
    FxSim's and FxMain's are the menu every page shows (PAGE blank, as Example's own).
    No PAGE is a page name, no path starts with a menu name."""
    from ctgedit.io import project as PR

    sa = _sa(fx_install)
    assert PR.menu_pages(sa) == ["FxHeaderbar", "FxHelp", "FxInfoZone", "Navigation"]
    nav = PR.menu_items(sa, "Navigation")
    assert [(it.path, it.target_page) for it in nav] == [(CONTENT_PATH, "FxSaContent")]
    assert [it.path for it in PR.menu_items(sa, "FxHeaderbar")] == ["Home"]
    for project in ("FxSim", "FxMain"):
        folder = fx_install.project(project)
        assert PR.menu_pages(folder) == [], project
        items = PR.menu_items(folder)
        assert sorted(it.path for it in items) == ["FxPlant.Genies", "FxPlant.Shapes"], project
        assert {it.page for it in items} == {""}, project


def test_equipment_hierarchy(fx_install):
    from ctgedit.io import project as PR

    rows = PR.equipment(_sa(fx_install))
    roots = PR.equipment_hierarchy(rows)
    company = next(n for n in roots if n.name == "FxCo")
    top = next(n for n in company.children if n.name == "FxSite")
    line = next(n for n in top.children if n.name == "Line1")
    pmp = next(n for n in line.children if n.name == "FXPMP1")
    assert pmp.full == "FxCo.FxSite.Line1.FXPMP1" and pmp.row["TYPE"] == "FxDrive_MEO"
    # every equipment row is a node of its own
    names = set()

    def walk(ns):
        for n in ns:
            if n.row is not None:
                names.add(n.full.lower())
            walk(n.children)

    walk(roots)
    assert names == {r["NAME"].lower() for r in rows}
    # a level no row defines is still a node (its equipment has a parent)
    synth = PR.equipment_hierarchy(
        [{"NAME": "Area.Line.Pump1"}, {"NAME": "Area.Line.Pump2"}, {"NAME": "Area"}]
    )
    assert [n.full for n in synth] == ["Area"] and synth[0].row == {"NAME": "Area"}
    line = synth[0].children[0]
    assert line.full == "Area.Line" and line.row is None
    assert [n.name for n in line.children] == ["Pump1", "Pump2"]


def test_equipment_tree_is_sorted_by_name():
    """Select Equipment lists every level by name."""
    _app()
    from ctgedit.ui.panels.sa_pickers import EquipmentBrowserDialog

    rows = [
        {"NAME": n, "PROJECT": p}
        for n, p in (
            ("Plant10.Pump", "B"),
            ("plant2", "A"),
            ("Plant9.Valve", "B"),
            ("Plant9.Aux", "A"),
            ("Area", "A"),
        )
    ]
    dlg = EquipmentBrowserDialog("", "", rows=rows)
    m = dlg.model
    top = [m.index(r, 0).data() for r in range(m.rowCount())]
    assert top == ["Area", "plant2", "Plant9", "Plant10"]
    p9 = m.index(2, 0)
    if m.canFetchMore(p9):
        m.fetchMore(p9)
    assert [m.index(r, 0, p9).data() for r in range(m.rowCount(p9))] == ["Plant9.Aux", "Plant9.Valve"]
    dlg.search.setText("nothing like this")
    dlg._apply_filter()
    assert "Nothing matches" in dlg.detail.text()
    dlg.search.setText("")
    dlg._apply_filter()
    assert dlg.detail.text() == ""
    dlg.deleteLater()


def test_equipment_parameter_names():
    from ctgedit.ui.panels.sa_pickers import is_equipment_param

    for name in (
        "Equipment",
        "EquipmentFrom",
        "EquipmentTo",
        "Equip",
        "equip1",
        "Eq",
        "AreaEquipment",
        "EquipmentSecondary",
        "eqFeedCurve",
    ):
        assert is_equipment_param(name), name
    for name in (
        "EquipRefAssocs",
        "EquipItem",
        "eqItem",
        "equip9Colour",
        "equip8Text",
        "EquipTooltip9",
        "EquipHeader9",
        "Equip.item",
        "Tag",
        "Name",
        "",
    ):
        assert not is_equipment_param(name), name


def test_equipment_and_menu_pickers(fx_install):
    _app()
    from ctgedit.ui.panels.sa_pickers import EquipmentBrowserDialog, MenuItemPickerDialog

    _write_menus(fx_install)
    sa = _sa(fx_install)
    dlg = EquipmentBrowserDialog(sa, "FxCo.FxSite.Line1.FXPMP1")
    assert dlg.chosen() == "FxCo.FxSite.Line1.FXPMP1"  # the current value shown
    # the row shows the WHOLE equipment name, not only its last part ('FXPMP1')
    assert dlg.view.currentIndex().data() == "FxCo.FxSite.Line1.FXPMP1"
    assert "FxDrive_MEO" in dlg.detail.text()
    dlg.search.setText("FXSPRAY1")
    dlg._apply_filter()
    assert dlg.chosen() == "FxCo.FxSite.Line1.FXSPRAY1"
    dlg._accept()
    assert dlg._result == "FxCo.FxSite.Line1.FXSPRAY1"
    # a level of the hierarchy no equipment defines can be chosen too
    lv = EquipmentBrowserDialog("", "Area.Line", rows=[{"NAME": "Area.Line.Pump1", "TYPE": "Drive"}])
    assert lv.chosen() == "Area.Line" and "a level" in lv.detail.text()
    lv.deleteLater()
    dlg.deleteLater()
    m = MenuItemPickerDialog(sa, CONTENT_PATH)
    assert m.menu.currentText() == "Navigation"
    assert m.chosen() == CONTENT_PATH
    assert "FxSaContent" in m.detail.text()
    m.menu.setCurrentIndex(m.menu.findText("FxHelp"))
    assert m.select("@(FxValves)") and m.chosen() == "@(FxValves)"


def test_genie_dialogs_get_the_pickers(fx_install, monkeypatch):
    """Genie dialogs get the SA pickers."""
    _app()
    from ctgedit.features import sa_workspace as SA
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.panels import sa_pickers
    from ctgedit.ui.dialog.genie_dialogs import (
        GenieParamsForm,
        GenieSubstitutionDialog,
        BrowseField,
        _MultiListField,
    )

    _write_menus(fx_install)
    sa = _sa(fx_install)
    d = EditableDocument.open(_master(fx_install))
    panes = SA.model_panes(d.page)
    info = next(p for p in panes if p.name == "FacePlate")
    pk = sa_pickers.ParamPickers(sa, panes, own=info.root)
    dlg = GenieSubstitutionDialog(
        d.page.objects[info.root],
        [],
        None,
        bindings=d.genie_all_params(info.root),
        form=d.genie_form(info.root),
        list_values=d.form_list_values,
        an=d.genie_an(info.root),
        ref="fxws.pane",
        pickers=pk,
    )
    pf = dlg.params_form
    tab = pf.combos["TabPaneName"]
    names = [tab.itemText(i) for i in range(tab.count())]
    assert "FacePlateHeader" in names and "FacePlate" not in names  # not itself
    assert tab.currentText() == "FacePlateHeader"
    assert isinstance(pf.editor("ExcludedAutofillPanes"), _MultiListField)
    assert isinstance(pf.editor("TabControlName"), BrowseField)
    cats = pf.editor("EquipRefAssocs")  # eqref.dbf categories
    assert isinstance(cats, _MultiListField) and "MEO" in cats._items
    page, objs = pk.tab_control_names("FacePlateHeader")
    assert page == "FxSaHeader" and "FxTabBar" in objs
    assert not dlg.changed_values(d.genie_all_params(info.root))  # nothing changed
    dlg.reject()
    # a navigation link's Menu Item, a control link's equipment
    params = {
        "IsUHD4K": "FALSE",
        "MenuItemPath": "",
        "Tooltip": "",
        "IsControlLink": "FALSE",
        "EquipmentFrom": "",
        "EquipmentTo": "",
    }
    form = d.genie_form_for("fxws", "navlink")
    lf = GenieParamsForm(params, form=form, list_values=d.form_list_values, pickers=pk)
    menu = lf.editor("MenuItemPath")
    assert isinstance(menu, BrowseField) and isinstance(lf.editor("EquipmentFrom"), BrowseField)
    monkeypatch.setattr(
        sa_pickers.ParamPickers,
        "_browse_menu_item",
        lambda self, parent, cur, values=None: "FxPlant.Fx & Area.FxSupply (Fx3)",
    )
    pk2 = sa_pickers.ParamPickers(sa, panes)
    lf2 = GenieParamsForm(params, form=form, list_values=d.form_list_values, pickers=pk2)
    lf2.editor("MenuItemPath").browse()
    assert lf2.values()["MenuItemPath"] == "FxPlant.Fx & Area.FxSupply (Fx3)"
    # a tab bar's menu, a tree view's data source
    spec = pk.for_param("Default Tabs Menu Name")
    assert spec.kind == "combo" and "FxInfoZone" in spec.items
    spec = pk.for_param("Datasource")
    assert spec.items[0] == "__EquipmentModel" and "Navigation" in spec.items
    # without pickers the dialogs are unchanged (plain text boxes)
    plain = GenieParamsForm(params, form=form, list_values=d.form_list_values)
    assert not isinstance(plain.editor("MenuItemPath"), BrowseField)


def test_pane_dialog_content_types_are_the_contype_list(fx_install):
    """A pane's Content Types (a form field listing the project's CONTYPE table)
    stays the multi-select when the pickers are on."""
    _app()
    from ctgedit.features import sa_workspace as SA
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.panels import sa_pickers
    from ctgedit.ui.dialog.genie_dialogs import GenieSubstitutionDialog, _MultiListField

    d = EditableDocument.open(_master(fx_install))
    panes = SA.model_panes(d.page)
    face = next(p for p in panes if p.name == "FacePlate")
    form = d.genie_form(face.root)
    fld = next((f for f in (form.fields if form is not None else ()) if f.name == "ContentTypes"), None)
    assert fld is not None and fld.list_form.strip().lower() == "contype", fld
    pk = sa_pickers.ParamPickers(_sa(fx_install), panes, own=face.root)
    dlg = GenieSubstitutionDialog(
        d.page.objects[face.root],
        [],
        None,
        bindings=d.genie_all_params(face.root),
        form=form,
        list_values=d.form_list_values,
        an=d.genie_an(face.root),
        ref="fxws.pane",
        pickers=pk,
    )
    ct = dlg.params_form.editor("ContentTypes")
    assert isinstance(ct, _MultiListField) and {"FxL1", "FxFP", "Master"} <= set(ct._items)
    dlg.reject()


def test_page_browser_shows_the_content_type(fx_install):
    """FxSaMaster is registered as Master; FxSaBlank is given the content type __Internal
    here (in the copy's pages.DBF) and FxSaNav keeps none."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.catalog import catalog_for

    blank = EditableDocument.open(fx_install.page("FxSaBlank", "FxSa"))
    assert blank.set_page_record({"CON_TYPE": "__Internal"})
    blank.save()
    pages = {e.name: e for e in catalog_for().pages(_sa(fx_install))}
    assert pages[MASTER].content_type == "Master"
    assert pages["FxSaBlank"].content_type == "__Internal"
    assert pages["FxSaNav"].content_type == ""


def test_equipment_names_are_read_on_a_worker_once_per_folder(monkeypatch, fx_install):
    """warm_equipment reads a scope's equipment on a worker thread once per folder, and the
    completion model (made on the GUI thread) waits for it and lists the same names."""
    _app()
    import threading
    from ctgedit.infra import caches
    from ctgedit.ui.panels import sa_pickers as SP

    sa = _sa(fx_install)
    caches.clear("refresh")
    names = [r.get("NAME") for r in SP.scope_equipment(sa) if r.get("NAME")]
    assert "FxCo.FxSite.Line1.FXPMP1" in names
    caches.clear("refresh")
    seen = []
    real = SP.scope_equipment
    monkeypatch.setattr(
        SP, "scope_equipment", lambda d: seen.append(threading.current_thread().name) or real(d)
    )
    SP.warm_equipment(sa)
    SP.warm_equipment(sa)  # (once per folder)
    model = SP.equipment_completion_model(sa)
    assert seen.count("equipment") == 1 and seen[0] == "equipment"
    assert model is not None and model.stringList() == names
    assert SP.equipment_completion_model(sa) is model


def test_a_move_reads_the_panes_again_only_when_a_pane_moved(fx_install, monkeypatch):
    """shift_records (a nudge, a drag) collects the pane labels again only when a pane or
    what holds one moved."""
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.render.scene import PageScene

    d = EditableDocument.open(_master(fx_install))
    sc = PageScene()
    sc.set_page(d.page)
    labels = sc.pane_labels()
    pane = labels[0][0]
    other = next(i for i in d.page.roots() if i not in sc._pane_records)
    calls = []
    orig = sc._collect_pane_labels
    monkeypatch.setattr(sc, "_collect_pane_labels", lambda p: (calls.append(p), orig(p)))
    assert sc.shift_records([([other], 3, 0)])
    assert calls == []
    assert sc.shift_records([([pane], 3, 0)])
    assert len(calls) == 1
