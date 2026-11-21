"""SA workspace checks over the shipped pages."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"
EXSA = os.path.join(R2, "ExampleSA")
MASTER = os.path.join(EXSA, "Master_PageMenu1_HD1080.ctg")
HELP = os.path.join(EXSA, "Help_HD1080.ctg")
DPS = os.path.join(EXSA, "DPS_Demo_HD1080.ctg")
need_sa = pytest.mark.skipif(
    not (os.path.exists(MASTER) and os.path.exists(HELP)), reason="ExampleSA / SA_Include not installed"
)
MASTER_PANES = ["Content", "HeaderBar", "FacePlate", "FacePlateHeader", "Navigation", "Info", "InfoHeader"]


def _app():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@need_sa
def test_shipped_workspace_pages_are_clean():
    """The shipped ExampleSA workspace and nested-pane pages pass every check
    (their panes, default pages and tab bars all resolve) -- read only."""
    from ctgedit.features import sa_workspace as SA
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(MASTER)
    rep = SA.check_document(d)
    assert [p.name for p in rep.panes] == MASTER_PANES
    assert rep.master
    assert [p.name for p in rep.panes if p.is_default] == ["Content"]
    assert [p.fill_mode_text() for p in rep.panes][:3] == [
        "Autofill",
        "Static",
        "Autofill, context must match",
    ]
    assert not [f for f in rep.findings if f.level != SA.INFO]
    info = next(p for p in rep.panes if p.name == "Info")
    assert info.tab_pane == "InfoHeader" and info.tab_control == "TabBar"
    # the pane record is the one the runtime reads; the unit is its genie
    assert all(d.page.objects[p.index].raw.get("_name") == "Pane" for p in rep.panes)
    assert all(d.genie_link(p.root)[:2] == ("sa_workspace", "pane") for p in rep.panes)
    for path in (HELP, DPS):
        if os.path.exists(path):
            rep = SA.check_document(EditableDocument.open(path))
            assert rep.panes and not rep.master
            assert not [f for f in rep.findings if f.level != SA.INFO], rep.findings
    # the header pane's page holds the tab bar the Info pane names
    p = SA.find_page_file(EXSA, "InfoHeader_HD1080")
    assert p and "tabbar" in SA.page_object_names(p)
    assert "TabBar" in SA.page_top_level_names(p)


@need_sa
def test_library_elements_link_to_their_aveva_help():
    from ctgedit import help as H

    if not H.aveva_available():
        pytest.skip("the AVEVA help is not installed")
    _app()
    from ctgedit.io.catalog import Entry
    from ctgedit.ui.panels.library_panel import LibraryPanel

    lib = os.path.join(R2, "SA_Include", "sa_workspace.ctm")
    e = Entry("genie", "SA_Include", "pane", lib, library="sa_workspace")
    assert LibraryPanel.element_help(e) == ("1206806", "element")
    # an element without its own page: its library's page, else its project's
    faceplate = Entry(
        "genie",
        "SA_Library",
        "meo_selector_22",
        os.path.join(R2, "SA_Library", "sa_faceplates.ctm"),
        library="sa_faceplates",
    )
    assert LibraryPanel.element_help(faceplate)[1] == "library"
    valve = Entry(
        "genie",
        "SA_Library",
        "block_21_h",
        os.path.join(R2, "SA_Library", "sa_valve.ctm"),
        library="sa_valve",
    )
    assert LibraryPanel.element_help(valve)[1] == "project"
    own = Entry("genie", "Example", "agitator_mixer", "x.ctm", library="energydemo")
    assert LibraryPanel.element_help(own) == (None, "")
    panel = LibraryPanel()
    texts = [a.text() for a in panel.build_context_menu(e).actions()]
    assert any(t.startswith("Help for 'pane'") for t in texts)
    texts = [a.text() for a in panel.build_context_menu(own).actions()]
    assert not any(t.startswith("Help for") for t in texts)
