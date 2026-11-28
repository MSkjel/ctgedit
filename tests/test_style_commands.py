"""Style commands in the window."""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app

#: the host page of FxMain
HOST = "FxShapes"


def _clean():
    from ctgedit.ui.editor import draw_styles as DS
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    for k in (
        DS.STYLE_KEY,
        DS.LAST_KEY,
        DS.MODE_KEY,
        DS.FILL_KEY,
        DS.LINE_KEY,
        DS.TEXT_KEY,
        DS.COLOUR_MODE_KEY,
        DS.NO_FILL_KEY,
        "styles/list",
        "styles/shared_file",
    ):
        st.remove(k)
    from ctgedit.ui.panels import styles_gallery as SG

    SG.store().reload()


@pytest.fixture
def win(fx_page):
    _app()
    _clean()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_page(HOST))
    w.resize(1500, 900)
    w.show()
    _app().processEvents()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()
    _clean()


def _select(w, ix):
    w._on_canvas_selection(list(ix))
    w.view.set_selection(list(ix))
    for _ in range(3):
        _app().processEvents()


def _rect(w, colour=0x808080, box=(300, 100, 380, 160)):
    i = w.doc.create_object("rectangle", box, None, colour=colour)
    w._refresh()
    return i


# ------------------------------------------------------------------ the swatch pair
def test_the_swatch_pair_replaces_the_drawing_colour(win):
    from ctgedit.ui.editor import draw_styles as DS
    from ctgedit.ui.swatch_pair import SwatchPair

    pair = win.ribbon_colours
    assert isinstance(pair, SwatchPair) and not hasattr(win, "ribbon_colour_btn")
    create = next(g for g in win.ribbon.groups_of("Design") if g.caption == "Create")
    assert pair in create.findChildren(SwatchPair)
    st = pair.state()
    assert (st["fill"], st["line"], st["text"]) == (0xC0C0C0, 0x202020, 0x202020)
    assert st["auto"] and not st["no_fill"]
    pair.resize(66, 64)
    reg = pair._layout()
    assert pair.part_at(reg["swap"].center()) == "swap"
    assert pair.part_at(reg["no_fill"].center()) == "no_fill"
    assert pair.part_at(reg["fill"].center()) == "fill"
    assert pair.part_at(reg["text"].center()) == "text"
    # the parts
    win._on_swatch_part("swap")
    assert DS.colours(win.doc.page)["fill"] == 0x202020 and DS.colour_mode() == "fixed"
    win._on_swatch_part("no_fill")
    assert DS.no_fill() and win.act_new_no_fill.isChecked()
    win._set_new_colours_auto(True)
    assert DS.colour_mode() == "auto" and win.act_new_colours_auto.isChecked()


def test_the_colour_popover_says_what_it_sets(win, monkeypatch):
    from ctgedit.ui.editor import draw_styles as DS
    from ctgedit.ui import swatch_pair as SP

    shown = []
    real = SP.NewColourPopover.open_under

    def spy(self, widget, focus_entry=False):
        shown.append(self)
        real(self, widget, focus_entry)

    monkeypatch.setattr(SP.NewColourPopover, "open_under", spy)
    win._on_swatch_part("line")
    pop = shown[-1]
    assert pop.target() == "line" and pop.head.text() == SP.TITLES["line"]
    pop.set_target("text")
    assert pop.head.text() == SP.TITLES["text"] and pop.no_fill.isHidden()
    pop.pick_pair(0x3A7BD5)
    assert DS.colours(win.doc.page)["text"] == 0x3A7BD5
    assert "New text is #3A7BD5" in win.statusBar().currentMessage()


def test_colours_of_new_objects_are_menu_commands(win):
    from ctgedit.ui.main_window_base import DESCRIPTIONS

    acts = [
        win.act_new_fill_colour,
        win.act_new_line_colour,
        win.act_new_text_colour,
        win.act_new_colours_swap,
        win.act_new_no_fill,
        win.act_new_colours_auto,
    ]
    tools = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Tools")
    colours = next(a.menu() for a in tools.actions() if a.text() == "Colours")
    sub = next(a.menu() for a in colours.actions() if a.text() == "Colours of New Objects")
    assert [a for a in sub.actions() if not a.isSeparator()] == acts
    for a in acts:
        assert a.objectName().startswith("act_new_") and not a.icon().isNull()
        assert DESCRIPTIONS.get(a.objectName())
    swap = win.act_new_colours_swap
    assert swap.shortcut().toString() == "Shift+X"
    assert swap in win.view.actions() and swap not in win.actions()  # (the page only)


def test_the_armed_tool_reaches_the_panel_and_the_tool_colour(win):
    from ctgedit.ui.editor import draw_styles as DS

    _select(win, [])
    win._set_tool("text")
    _app().processEvents()
    assert win.props.section("New Text") is not None
    assert win._draw_colour == DS.colours(win.doc.page)["text"] == 0x202020
    win._set_tool("rectangle")
    _app().processEvents()
    assert win.props.section("New Rectangle") is not None
    assert win._draw_colour == 0xC0C0C0
    win._set_tool("select")
    _app().processEvents()
    assert win.props.section("New Rectangle") is None


def test_settings_show_the_new_rows_once(win):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win, "Editing")
    try:
        keys = [r.key for r in dlg._tabs.rows() if r.key]
        for k in (
            "draw/colour_mode",
            "opt/new_object_style",
            "props/multi_apply",
            "props/search_keep",
            "styles/shared_file",
        ):
            assert keys.count(k) == 1, k
        ch = dlg._choices()
        assert ch["draw/colour_mode"] == "auto" and ch["styles/shared_file"] == ""
        dlg._new_colours.buttons["fill"].set_pair((0x123456, 0x123456))
        dlg._new_colours._picked()
        assert dlg._choices()["draw/colour_mode"] == "fixed"
        assert dlg._choices()["draw/fill_colour"] == 0x123456
    finally:
        dlg.reject()


# ------------------------------------------------------------------ the styles
def test_a_tile_previews_on_hover_and_applies_on_click(win):
    a = _rect(win, 0x808080)
    b = _rect(win, 0x404040, (420, 100, 500, 160))
    win.doc.set_line_width(a, 4)
    _select(win, [a])
    win.save_style("Thick")
    gallery = win.ribbon_styles
    assert [t.style_.name for t in gallery.tiles] == ["Thick"]
    group = win._styles_group
    groups = [g.caption for g in win.ribbon.groups_of("Design")]
    assert groups.index("Styles") == groups.index("Create") + 1
    tile = gallery.tiles[0]
    assert tile.toolTip().startswith("Thick") and not tile.icon().isNull()
    assert tile.defaultAction() is win.style_actions()["Thick"]
    _select(win, [b])
    n = len(win.doc._undo)
    before = bytes(win.doc.parsed.page.objects[b].body_raw)
    tile.hoverChanged.emit("Thick")
    assert win._style_preview is not None and win._style_preview[1] == [b]
    assert len(win.doc._undo) == n and bytes(win.doc.parsed.page.objects[b].body_raw) == before
    # the overlay draws it (no error noted)
    win.view.viewport().repaint()
    tile.hoverChanged.emit("")
    assert win._style_preview is None
    tile.click()
    assert len(win.doc._undo) == n + 1 and win.doc._undo[-1].label == "style Thick"
    assert win.doc.appearance_of(b)["line_width"] == 4
    assert win.act_save_style in group.extras


def test_edit_styles_update_rename_select_and_defaults(win):
    from ctgedit.ui.editor import draw_styles as DS

    a = _rect(win, 0x808080)
    b = _rect(win, 0x808080, (420, 100, 500, 160))
    c = _rect(win, 0x00FF00, (520, 100, 600, 160))
    win.doc.set_line_width(a, 3)
    win.doc.set_line_width(b, 3)
    _select(win, [a])
    win.save_style("Grey 3")
    win._rebuild_styles_menu()
    texts = [x.text() for x in win.styles_menu.actions() if not x.isSeparator()]
    for t in (
        "Update Style from Selection",
        "Rename Style",
        "Select Objects with This Style",
        "Set as Default for New Objects",
        "Use Selection as Default for New Rectangles",
        "Grey 3",
        "Manage Styles…",
    ):
        assert t in texts, t
    # Select Objects with This Style: a and b, not c
    assert win.select_by_style("Grey 3") == 2 and sorted(win._sel) == sorted([a, b])
    # Update Style from Selection
    _select(win, [c])
    win.update_style("Grey 3")
    from ctgedit.ui.panels.styles_gallery import store

    assert store().find("Grey 3").appearance["fill_colour"] == 0x00FF00
    # Rename
    assert win.rename_style("Grey 3", "Green")
    assert store().find("Green") is not None and store().find("Grey 3") is None
    assert [t.style_.name for t in win.ribbon_styles.tiles] == ["Green"]
    # Set as Default for New Objects / Use Selection as Default
    win.set_style_as_default("Green", ["rectangle"])
    assert DS.tool_style("rectangle")["fill_colour"] == 0x00FF00
    assert DS.tool_style("rectangle_outline").get("fill_colour") is None
    _select(win, [a])
    win.style_selection_as_default()
    assert DS.tool_style("rectangle")["line_width"] == 3


def test_manage_styles_rename_remove_move_import_export(win, tmp_path):
    from ctgedit.ui.panels.styles_gallery import ManageStylesDialog, read_style_file, store

    a = _rect(win)
    _select(win, [a])
    for name in ("One", "Two", "Three"):
        win.save_style(name)
    dlg = ManageStylesDialog(win)
    assert [s.name for s in dlg.styles()] == ["One", "Three", "Two"]
    assert dlg.list.count() == 3 and not dlg.list.item(0).icon().isNull()
    dlg.list.setCurrentRow(2)
    assert dlg.move_selected(-1) and [s.name for s in dlg.styles()] == ["One", "Two", "Three"]
    dlg.list.setCurrentRow(0)
    assert dlg.rename_selected("First")
    assert not dlg.rename_selected("Two")  # (taken)
    dlg.list.setCurrentRow(2)
    assert dlg.remove_selected()
    path = str(tmp_path / "team.ctgstyles")
    assert dlg.export_file(path)
    assert [s.name for s in read_style_file(path)] == ["First", "Two"]
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["format"] == 1
    other = tmp_path / "more.ctgstyles"
    other.write_text(
        json.dumps({"format": 1, "styles": [{"name": "Pipe 3 px", "appearance": {"line_width": 3}}]}),
        encoding="utf-8",
    )
    assert dlg.import_file(str(other)) == 1
    dlg.accept()
    store().put_user_styles(dlg.styles())
    assert [s.name for s in store().user_styles()] == ["First", "Two", "Pipe 3 px"]
    assert not hasattr(dlg, "_hand_size")


def test_a_shared_styles_file_is_listed_read_only_and_read_again(win, tmp_path):
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.panels.styles_gallery import store

    path = tmp_path / "shared.ctgstyles"
    path.write_text(
        json.dumps(
            {
                "format": 1,
                "styles": [
                    {
                        "name": "Alarm red",
                        "kind": "rectangle",
                        "appearance": {"fill_colour": 0xFF0000, "filled": True},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    app_settings().setValue("styles/shared_file", str(path))
    store().reload()
    shared = store().shared_styles()
    assert [s.name for s in shared] == ["Alarm red"] and shared[0].shared
    win._rebuild_styles_menu()
    assert any(a.isSeparator() and "Shared" in a.text() for a in win.styles_menu.actions())
    assert "shared:Alarm red" in win.style_actions()
    # read-only: no Update / Rename for it
    m = QtWidgets.QMenu()
    win.style_menu_for(shared[0], m)
    texts = [a.text() for a in m.actions()]
    assert "Rename…" not in texts and "Select Objects with This Style" in texts
    # the file changed: read again
    path.write_text(
        json.dumps(
            {
                "format": 1,
                "styles": [
                    {"name": "Alarm red", "appearance": {"fill_colour": 0xFF0000}},
                    {"name": "Normal green", "appearance": {"fill_colour": 0x00AA00}},
                ],
            }
        ),
        encoding="utf-8",
    )
    store().reload()
    assert [s.name for s in store().shared_styles()] == ["Alarm red", "Normal green"]
    # apply a shared style
    a = _rect(win)
    _select(win, [a])
    win.apply_style("Normal green", True)
    assert win.doc.appearance_of(a)["fill_colour"] == 0x00AA00
    # a file that is not there says so
    app_settings().setValue("styles/shared_file", str(tmp_path / "gone.ctgstyles"))
    store().reload()
    assert store().shared_styles() == [] and "not there" in store().shared_error


def test_the_panel_strip_heads_the_appearance_section(win):
    a = _rect(win)
    _select(win, [a])
    win.save_style("Box")
    for _ in range(3):
        _app().processEvents()
    _select(win, [a])
    sec = win.props.section("Appearance")
    strips = [w for w in sec.findChildren(QtWidgets.QWidget) if w.objectName() == "panelStyles"]
    assert strips and [t.style_.name for t in strips[0].tiles] == ["Box"]


def test_a_drawn_object_takes_its_tools_look_in_one_step(win):
    """Drawing with a tool: the tool's colour and its style (draw_styles.style_for) as one
    undo step; a text placed in place takes the text colour (not the fill grey)."""
    from ctgedit.format import colourstate
    from ctgedit.ui.editor import draw_styles as DS
    from ctgedit.ui.prefs.settings import set_option

    set_option("props_on_new", False)
    DS.set_kind_style("rectangle", {"line_width": 2, "corner_radius": 4})
    doc = win.doc
    n = len(doc._undo)
    count = len(doc.page.objects)
    win._set_tool("rectangle")
    win._on_draw_committed("rectangle", {"rect": (300, 300, 380, 360)})
    i = win._sel[0]
    assert len(doc._undo) == n + 1 and doc._undo[-1].label == "add object"
    app = doc.appearance_of(i)
    assert app["line_width"] == 2 and app["corner_radius"] == 4
    ch = colourstate.colour_channels(doc.parsed.page.objects[i])
    assert ch["fill"] == (0xC0C0C0, 0xC0C0C0) and ch["line"] == (0x202020, 0x202020)
    doc.undo()
    assert len(doc.page.objects) == count  # (one step took it all back)
    win._set_tool("select")
    idx = win._apply_text_edit({"kind": "place", "pos": (500, 300), "doc": doc}, "Tank")
    assert doc.page.objects[idx].style.text_colour == 0x202020
