"""The Library panel."""

import json
import os

import pytest
from qtkit import app as _app
from timing import check_speed


def _thumb(w, h, colour):
    """A small stored thumbnail of our own: a filled box with a dark border (as the fixture
    generator draws one)."""
    from ctgedit.format import thumbnail

    px = bytearray()
    for y in range(h):
        for x in range(w):
            c = 0x202020 if x in (0, w - 1) or y in (0, h - 1) else colour
            px += bytes((c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF, 0))
    return thumbnail.encode(bytes(px), w, h)


@pytest.fixture
def proj(fx_install):
    """FxMain of the test's copy, with the libraries of the module docstring."""
    from ctgedit.editing import ElementDocument, libraries_changed

    main = fx_install.project("FxMain")
    for lib_name, names in (
        ("fxmixer.ctm", ("fx_agitator_mixer", "fx_agitator_tank")),
        ("fxagitation.ctm", ("fx_agitation_tank",)),
    ):
        for n in names:
            e = ElementDocument.new_element(os.path.join(main, lib_name), n, "genie", canvas=(120, 80))
            e.create_object("rectangle", (10, 10, 90, 60), colour=0x4060A0)
            e.save(thumbnail=_thumb(40, 30, 0x4060A0))
    fx_install.big_library("FxMain", "fxagitator.ctl", 2, prefix="fx_agitator_")
    fx_install.big_library("FxMain", "fxstir.ctl", 1, prefix="fx_stir_agitator_")
    libraries_changed()
    return main


def _panel(proj, kind="symbol", w=320, h=800, show=False):
    app = _app()
    from ctgedit.ui.panels.library_panel import LibraryPanel

    p = LibraryPanel()
    p.resize(w, h)
    p.set_project_dir(proj)
    p.ensure_loaded()
    p.set_kind(kind)
    if show:
        p.show()
        app.processEvents()
    return p


def _row_of(p, e):
    rows = p._model.rows_for_key(e.key)
    assert rows, e
    return rows[0]


# --------------------------------------------------------------------------- pure helpers
def test_score_ranking_and_reference_tokens():
    from ctgedit.io.catalog import Entry
    from ctgedit.ui.panels.library_panel import _prep, score_tokens

    def rank(name, lib, q, proj="FxMain"):
        return score_tokens(_prep(Entry("symbol", proj, name, "x.ctl", library=lib)), q.split())

    exact = rank("agitator", "misc", "agitator")
    prefix = rank("agitator_grey", "misc", "agitator")
    word = rank("green_agitator", "misc", "agitator")
    lib_only = rank("mixer", "agitator", "agitator")
    fuzzy = rank("agi_ta_tor", "misc", "agitator")
    assert exact < prefix < word < lib_only < fuzzy
    assert rank("pump", "misc", "agitator") is None
    # every token must match; library + element in one pasted reference
    assert rank("agitator_grey", "FxStir", "agitator grey") is not None
    assert rank("agitator_grey", "FxStir", "agitator blue") is None
    assert rank("agitator_mixer", "fxmixer", "fxmixer.agitator_mixer") == (0, 0)
    assert rank("agitator_mixer", "fxmixer", "fxmixer.mixer")[0] <= 3
    assert rank("agitator_mixer", "fxother", "fxmixer.agitator") is None
    assert rank("anything", "fxmixer", "fxmixer.") is not None


def test_mime_round_trip_and_parse():
    _app()
    from PySide6 import QtCore
    from ctgedit.ui.panels.library_panel import LIBELEM_MIME, element_mime, parse_libelem_mime

    md = element_mime("fxmixer", "fx_agitator_mixer", "ctm")
    assert bytes(md.data(LIBELEM_MIME)) == b"fxmixer|fx_agitator_mixer|ctm"
    assert md.text() == "fxmixer.fx_agitator_mixer"
    assert parse_libelem_mime(md) == ("fxmixer", "fx_agitator_mixer", "ctm")
    bad = QtCore.QMimeData()
    bad.setData(LIBELEM_MIME, b"only|two")
    assert parse_libelem_mime(bad) is None
    assert parse_libelem_mime(QtCore.QMimeData()) is None
    assert parse_libelem_mime(None) is None


def test_background_keying_keeps_enclosed_white_and_icons_upscale_crisp():
    _app()
    from PySide6 import QtCore, QtGui
    from ctgedit.ui.panels.library_panel import fit_pixmap, key_background

    img = QtGui.QImage(20, 20, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor("#ffffff"))
    p = QtGui.QPainter(img)
    p.setPen(QtGui.QColor("#000000"))
    p.drawRect(5, 5, 9, 9)  # a black frame enclosing white
    p.end()
    out = key_background(QtGui.QPixmap.fromImage(img)).toImage()
    assert QtGui.QColor.fromRgba(out.pixel(0, 0)).alpha() == 0  # page bg keyed
    assert QtGui.QColor.fromRgba(out.pixel(10, 10)).alpha() == 255  # inside kept
    assert out.pixel(10, 10) & 0xFFFFFF == 0xFFFFFF
    assert QtGui.QColor.fromRgba(out.pixel(5, 5)).alpha() == 255  # the frame
    # a non-white background is left alone
    grey = QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32)
    grey.fill(QtGui.QColor("#808080"))
    g = key_background(QtGui.QPixmap.fromImage(grey)).toImage()
    assert QtGui.QColor.fromRgba(g.pixel(0, 0)).alpha() == 255
    # 16 px icon in a 100x72 box -> whole-multiple nearest upscale (4x)
    icon16 = QtGui.QPixmap(16, 16)
    icon16.fill(QtCore.Qt.red)
    assert fit_pixmap(icon16, 100, 72).size() == QtCore.QSize(64, 64)
    big = QtGui.QPixmap(200, 140)
    big.fill(QtCore.Qt.red)
    s = fit_pixmap(big, 100, 72).size()
    assert s.width() <= 100 and s.height() <= 72 and s.width() >= 98
    mid = QtGui.QPixmap(60, 40)
    assert fit_pixmap(mid, 100, 72).size() == QtCore.QSize(60, 40)  # no blurry 1.x zoom


# --------------------------------------------------------------------------- the panel
def test_search_finds_elements_across_every_library_of_the_chain(proj, fx_install):
    """(FxLib gets a generated library of 1,000 symbols: the chain is long.)"""
    fx_install.big_library("FxLib", "fxbulk.ctl", 1000)
    from ctgedit.editing import libraries_changed

    libraries_changed()
    p = _panel(proj, "symbol")
    assert len(p.all_entries()) > 1000  # the include chain, not FxMain
    projects = {pr.name for pr, _li in p.libraries()}
    assert {"FxMain", "FxLib", "Include"} <= projects
    p.set_query("agitator")
    hits = p.visible_entries()
    libs = {e.library.lower() for e in hits}
    assert len(libs) >= 3, libs  # fxmany, fxagitator, fxstir
    assert all("agitator" in (e.name + e.library).lower() for e in hits)
    assert p.section_titles()  # grouped by library
    assert p.scope_count_text() == "%d matches" % len(hits)
    assert p.status_text().startswith(p.current_entry().reference)  # top hit selected
    # the tree maps where the results are: libraries without any are hidden
    tree = p.tree_widget()
    shown = hidden = 0
    for sc, it in p._tree_items.items():
        if sc[0] == "library":
            if it.isHidden():
                hidden += 1
                assert it.text(1) == "0"
            else:
                shown += 1
                assert int(it.text(1)) > 0
    assert shown == len({os.path.normcase(e.path) for e in hits}) and hidden > 0
    assert tree.topLevelItem(2).text(1) == str(len(hits))  # All libraries: n matches
    # genies: FxMain's fxmixer agitator + FxLib's fx_agitator genie, exact name first
    p.set_kind("genie")
    p.set_query("fx_agitator")
    refs = [e.reference for e in p.visible_entries()]
    assert "fxmixer.fx_agitator_mixer" in refs and "fxparts.fx_agitator" in refs
    assert refs[0] == "fxparts.fx_agitator"
    assert p.current_entry() is not None and p.current_entry().reference == refs[0]
    # clearing restores everything
    p.set_query("")
    assert len(set(e.key for e in p.visible_entries())) == sum(
        1 for e in p.all_entries() if not e.shadowed_by
    )
    assert all(not it.isHidden() for it in p._tree_items.values())


def test_kind_toggle_is_segmented_and_remembered(proj):
    from ctgedit.ui.prefs.settings import app_settings

    p = _panel(proj, "symbol")
    assert p._btn_kind["symbol"].isChecked() and not p._btn_kind["genie"].isChecked()
    assert all(e.kind == "symbol" and e.path.lower().endswith(".ctl") for e in p.all_entries())
    symbols = {e.key for e in p.all_entries()}
    p._btn_kind["genie"].click()
    assert p.kind() == "genie" and p._btn_kind["genie"].isChecked()
    assert all(e.kind == "genie" and e.path.lower().endswith(".ctm") for e in p.all_entries())
    assert p.all_entries() and not symbols & {e.key for e in p.all_entries()}
    assert str(app_settings().value("librarypanel/kind")) == "genie"
    assert "genies" in p.search_field().placeholderText()
    from ctgedit.ui.panels.library_panel import LibraryPanel

    q = LibraryPanel()
    q.set_project_dir(proj)
    q.ensure_loaded()
    assert q.kind() == "genie" and q._btn_kind["genie"].isChecked()
    assert all(e.kind == "genie" for e in q.visible_entries())


def test_library_scope_from_tree_header_and_api(proj):
    from PySide6 import QtCore
    from PySide6.QtTest import QTest

    p = _panel(proj, "genie", show=True)
    sc = p.library_scope("fxmixer")
    assert sc is not None
    # the tree item scopes the grid
    item = p._tree_items[sc]
    p.tree_widget().setCurrentItem(item)
    assert p.scope() == sc
    vis = p.visible_entries()
    assert vis and all(e.library == "fxmixer" for e in vis)
    assert "fxmixer" in p.scope_text() and p._btn_scope_clear.isVisibleTo(p)
    assert not p.section_titles()  # one library: no headers
    # search applies inside the scope; zero matches offers the global search
    p.set_query("agitator")
    assert {e.name for e in p.visible_entries()} == {"fx_agitator_mixer", "fx_agitator_tank"}

    p.set_query("Agitation")  # only in fxagitation, not fxmixer
    assert not p.visible_entries()
    assert p._empty_link.isVisibleTo(p) and "all libraries" in p._empty_link.text().lower()
    p._empty_link.click()
    assert p.scope() == ("all",) and [e.name for e in p.visible_entries()] == ["fx_agitation_tank"]
    # a project node scopes to its libraries (FxMain: fxback, fxmixer, fxagitation)
    p.set_query("")
    p.set_scope(("project", "FxMain"))
    assert {e.project for e in p.visible_entries()} == {"FxMain"}
    assert len({e.library for e in p.visible_entries()}) >= 3
    # clicking a group header scopes to that library
    p.set_scope(("all",))

    grid = p.grid_view()
    grid.doItemsLayout()
    hdr_row = next(
        r
        for r in range(p._model.rowCount())
        if p._model.header(r) is not None
        and p._model.header(r).scope
        and p._model.header(r).scope[0] == "library"
    )
    target = p._model.header(hdr_row).scope
    rect = grid.visualRect(p._model.index(hdr_row, 0))
    QTest.mouseClick(grid.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, rect.center())
    assert p.scope() == target
    # Esc widens back to all libraries
    QTest.keyClick(grid, QtCore.Qt.Key_Escape)
    assert p.scope() == ("all",)
    p.close()


def test_favourites_and_recent_persist_through_app_settings(proj):
    from ctgedit.ui.panels.library_panel import LibraryPanel
    from ctgedit.ui.prefs.settings import app_settings

    p = _panel(proj, "genie")
    mixer = p.find_entry("fxmixer", "fx_agitator_mixer")
    tank = p.find_entry("fxmixer", "fx_agitator_tank")
    assert mixer is not None and tank is not None
    p.set_favourite(mixer, True)
    assert p.is_favourite(mixer) and not p.is_favourite(tank)
    assert json.loads(app_settings().value("librarypanel/favourites")) == [
        ["ctm", "fxmixer", "fx_agitator_mixer"]
    ]
    spy = []
    p.placeRequested.connect(lambda *a: spy.append(a))
    p.place(tank)
    assert spy == [("fxmixer", "fx_agitator_tank", "ctm")]
    p.note_placed("fxparts", "fx_agitator", "ctm")
    assert p.recent()[:2] == [("ctm", "fxparts", "fx_agitator"), ("ctm", "fxmixer", "fx_agitator_tank")]
    p.place(tank)  # re-placing moves it to the front, no dup
    assert [r[2] for r in p.recent()] == ["fx_agitator_tank", "fx_agitator"]

    q = LibraryPanel()
    q.set_project_dir(proj)
    q.ensure_loaded()
    assert q.kind() == "genie"
    assert q.favourites() == [("ctm", "fxmixer", "fx_agitator_mixer")]
    assert [r[2] for r in q.recent()] == ["fx_agitator_tank", "fx_agitator"]
    titles = q.section_titles()
    assert titles[:2] == ["Favourites", "Recently placed"]
    vis = q.visible_entries()
    assert vis[0].reference == "fxmixer.fx_agitator_mixer"
    assert [e.reference for e in vis[1:3]] == ["fxmixer.fx_agitator_tank", "fxparts.fx_agitator"]
    assert q._tree_items[("favourites",)].text(1) == "1"
    assert q._tree_items[("recent",)].text(1) == "2"
    q.set_scope(("favourites",))
    assert [e.reference for e in q.visible_entries()] == ["fxmixer.fx_agitator_mixer"]
    q.set_scope(("recent",))
    assert [e.name for e in q.visible_entries()] == ["fx_agitator_tank", "fx_agitator"]
    # symbols keep their own lists (filtered by ext)
    q.set_kind("symbol")
    assert not q.section_titles() or q.section_titles()[0] not in ("Favourites", "Recently placed")
    q.set_kind("genie")
    q.toggle_favourite(q.find_entry("fxmixer", "fx_agitator_mixer"))
    assert q.favourites() == [] and json.loads(app_settings().value("librarypanel/favourites")) == []


def test_star_click_toggles_favourite(proj):
    from PySide6 import QtCore
    from PySide6.QtTest import QTest

    p = _panel(proj, "genie", show=True)
    p.set_query("fxmixer.fx_agitator_mixer")
    e = p.current_entry()
    assert e is not None and e.reference == "fxmixer.fx_agitator_mixer"
    grid = p.grid_view()
    grid.doItemsLayout()
    idx = p._model.index(_row_of(p, e), 0)
    star = p._delegate.star_rect(grid.visualRect(idx))
    QTest.mouseClick(grid.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, star.center())
    assert p.is_favourite(e)
    QTest.keyClick(grid, QtCore.Qt.Key_D, QtCore.Qt.ControlModifier)
    assert not p.is_favourite(e)
    p.close()


def test_place_requested_on_double_click_enter_and_search_enter(proj):
    from PySide6 import QtCore
    from PySide6.QtTest import QTest

    p = _panel(proj, "genie", show=True)
    spy = []
    p.placeRequested.connect(lambda *a: spy.append(a))
    p.set_query("fxmixer.fx_agitator_mixer")
    e = p.current_entry()
    idx = p._model.index(_row_of(p, e), 0)
    p.grid_view().doubleClicked.emit(idx)
    assert spy[-1] == ("fxmixer", "fx_agitator_mixer", "ctm")
    # Enter in the grid places the current card
    QTest.keyClick(p.grid_view(), QtCore.Qt.Key_Return)
    assert len(spy) == 2 and spy[-1] == ("fxmixer", "fx_agitator_mixer", "ctm")
    # Enter in the search box places the top result
    p.set_query("Agitation")
    QTest.keyClick(p.search_field(), QtCore.Qt.Key_Return)
    assert spy[-1] == ("fxagitation", "fx_agitation_tank", "ctm")
    # symbols place with ext 'ctl'
    p.set_kind("symbol")
    p.set_query("fx_agitator_grey")
    assert p.place()
    assert spy[-1][2] == "ctl" and spy[-1][1] == "fx_agitator_grey"
    # Edit element from the context menu -> editRequested(path, element)
    edits = []
    p.editRequested.connect(lambda *a: edits.append(a))
    cur = p.current_entry()
    menu = p.build_context_menu(cur)
    texts = [a.text() for a in menu.actions()]
    assert "Place on Page" in texts and "Edit Element" in texts and "Show in Explorer" in texts
    assert any(t.startswith("Copy Reference") for t in texts)
    assert "Add to Favourites" in texts
    assert "Rename Element…" in texts and "Delete Element…" in texts
    assert not any("“" in t or "”" in t for t in texts)  # straight quotes
    next(a for a in menu.actions() if a.text() == "Edit Element").trigger()
    assert edits == [(cur.path, cur.name)] and cur.path.lower().endswith(".ctl")
    p.close()


def test_view_menu_wording_and_panel_keys(proj):
    """Title Case menu items; Ctrl+1 / Ctrl+2 / F5 like the page browser."""
    from PySide6 import QtCore
    from PySide6.QtTest import QTest

    p = _panel(proj, "symbol", show=True)
    menu = p._btn_more.menu()
    top = [a.text().split("\t")[0] for a in menu.actions() if a.text()]
    for want in (
        "Thumbnail Grid",
        "List",
        "Card Size",
        "Projects",
        "Group by Library",
        "Show Library Tree",
        "Clear Recent",
        "Rescan Libraries",
    ):
        assert want in top, (want, top)
    grid = p.grid_view()
    grid.setFocus()
    QTest.keyClick(grid, QtCore.Qt.Key_2, QtCore.Qt.ControlModifier)
    assert p.view_mode() == "list"
    QTest.keyClick(grid, QtCore.Qt.Key_1, QtCore.Qt.ControlModifier)
    assert p.view_mode() == "grid"
    rescans = []
    p.refresh = lambda: rescans.append(1)
    QTest.keyClick(grid, QtCore.Qt.Key_F5)
    assert rescans == [1]
    assert "(Ctrl+F)" in p.search_field().placeholderText()
    assert "in all libraries" not in p.search_field().placeholderText()
    tip = p._btn_linked.toolTip()
    assert tip == "Paste symbols linked to their library. Genies are always linked."
    p.close()


def _rows(p):
    """(depth, label, note) of every tree row, in order."""
    out = []

    def walk(it, depth):
        from ctgedit.ui.browser.project_tree import LabelRole, NoteRole

        out.append((depth, it.data(0, LabelRole) or it.text(0), it.data(0, NoteRole) or ""))
        for k in range(it.childCount()):
            walk(it.child(k), depth + 1)

    t = p.tree_widget()
    for k in range(t.topLevelItemCount()):
        walk(t.topLevelItem(k), 0)
    return out


def test_projects_tree_and_flat(proj):
    """Tree: the page's project on top, its includes under it, Include /
    System last ('always included'); Flat: the chain's projects side by side.
    The choice is the page browser's shared setting."""
    from ctgedit.io.catalog import catalog_for
    from ctgedit.ui.prefs.settings import app_settings

    p = _panel(proj, "symbol")
    assert p.project_view() == "tree"
    rows = _rows(p)
    assert [r[1] for r in rows[:3]] == ["Favourites", "Recent", "All libraries"]
    top = [r for r in rows if r[0] == 0][3:]
    assert [r[1] for r in top] == ["FxMain"]  # the page's project
    under = [r for r in rows if r[0] == 1 and r[1] in ("Include", "System")]
    assert under and all(r[2] == "always included" for r in under)
    # the included projects sit under the project that includes them
    cat = catalog_for()
    ex = cat.project("FxMain")
    kids = [r[1] for r in rows if r[0] == 1]
    nested_libs = {pr.name for pr, _li in p.libraries()}
    for inc in ex.includes:
        if inc in nested_libs:
            assert inc in kids, (inc, kids)
    # every library of the chain is still reachable, each at least once
    libs = {("library", os.path.normcase(li.path)) for _pr, li in p.libraries()}
    assert libs <= set(p._tree_items)
    # project rows name who includes them (FxStyle: included by FxMain)
    sx = p._tree_items.get(("project", "FxStyle"))
    assert sx is not None and "Included by" in sx.toolTip(0)
    # a search keeps the map: libraries without matches hidden, at any depth
    p.set_query("agitator")
    for sc, it in p._tree_items.items():
        if sc[0] == "library":
            assert it.isHidden() == (it.text(1) == "0")
    p.set_query("")
    # Flat: one row per project, in chain order; remembered in the shared key
    p.set_project_view("flat")
    assert str(app_settings().value("pagebrowser/project_view")) == "flat"
    top = [r[1] for r in _rows(p) if r[0] == 0][3:]
    order = [pr.name for pr in cat.closure(ex)]
    assert top == [n for n in order if n in top] and len(top) == len(set(top))
    assert not [r for r in _rows(p) if r[0] >= 2]  # nothing nested deeper
    p.set_project_view("tree")
    assert [r[1] for r in _rows(p) if r[0] == 0][3:] == ["FxMain"]
    # a change made in the page browser reaches the panel
    from ctgedit.ui.browser.dialog import set_project_view

    set_project_view("flat")
    assert len([r for r in _rows(p) if r[0] == 0][3:]) > 1
    assert p._act_proj_flat.isChecked()
    set_project_view("tree")
    assert [r[1] for r in _rows(p) if r[0] == 0][3:] == ["FxMain"]


def test_tree_rows_of_a_repeated_project_and_a_view_switch(fx_install):
    """A library listed under two projects keeps the row the user picked;
    Flat -> Tree opens the page's project again. (The test makes FxSaRoot, which includes
    FxSaInclude, which includes Include: Include is then listed twice, under FxSaInclude
    and always included.)"""
    from ctgedit.editing import libraries_changed
    from ctgedit.ui.browser.dialog import set_project_view
    from ctgedit.ui.browser.project_tree import LabelRole
    from ctgedit.ui.panels.library_panel import ScopeRole
    from ctgedit.format import dbf

    fx_install.new_project("FxSaInclude", includes=("Include",), tables=False)
    sa = fx_install.new_project("FxSaRoot", includes=("FxSaInclude",))
    with dbf.Dbf(os.path.join(fx_install.user, "MASTER.DBF")) as master:  # (registered)
        master.append(
            [
                master.encode({"NAME": n, "PATH": os.path.join(fx_install.user, n)})
                for n in ("FxSaInclude", "FxSaRoot")
            ]
        )
    libraries_changed()
    _app()
    from ctgedit.ui.panels.library_panel import LibraryPanel

    p = LibraryPanel()
    p.set_project_dir(sa)
    p.ensure_loaded()
    p.set_kind("symbol")
    # Include is included by FxSaInclude and always included under FxSaRoot
    inc = [it for it in p._tree_all if it.data(0, LabelRole) == "Include"]
    assert len(inc) >= 2 and inc[-1].childCount()
    row = inc[-1].child(0)
    p.tree_widget().setCurrentItem(row)
    assert p.scope() == tuple(row.data(0, ScopeRole))
    assert p.tree_widget().currentItem() is row
    p.set_query("a")
    p.set_query("")
    assert p.tree_widget().currentItem() is row
    set_project_view("flat")
    set_project_view("tree")
    top = [it for it in p._tree_all if it.data(0, LabelRole) == "FxSaRoot"]
    assert top and top[0].isExpanded()
    p.close()


def test_cards_name_the_project_where_projects_mix(proj):
    p = _panel(proj, "genie")
    e = p.find_entry("fxmixer", "fx_agitator_mixer")
    assert p.card_subtitle(e) == "fxmixer"  # grouped by library
    p.set_grouped(False)
    assert p.card_subtitle(e) == "FxMain · fxmixer"
    p.set_scope(p.library_scope("fxmixer"))
    assert p.card_subtitle(e) == "fxmixer"
    p.set_scope(("favourites",))
    assert p.card_subtitle(e) == "FxMain · fxmixer"


def test_drag_payload_of_a_card(proj):
    from ctgedit.ui.panels.library_panel import LIBELEM_MIME, parse_libelem_mime

    p = _panel(proj, "genie")
    p.set_query("fxmixer.fx_agitator_mixer")
    e = p.current_entry()
    m = p._model
    from PySide6 import QtCore

    assert m.flags(m.index(_row_of(p, e), 0)) & QtCore.Qt.ItemIsDragEnabled
    assert LIBELEM_MIME in m.mimeTypes()
    md = m.mimeData([m.index(_row_of(p, e), 0)])
    assert bytes(md.data(LIBELEM_MIME)) == b"fxmixer|fx_agitator_mixer|ctm"
    assert md.text() == "fxmixer.fx_agitator_mixer"
    assert parse_libelem_mime(p.mime_for(e)) == ("fxmixer", "fx_agitator_mixer", "ctm")
    pm = p.drag_pixmap(e)
    assert not pm.isNull() and pm.width() > 32
    # headers are neither selectable nor draggable
    p.set_query("agitator")
    hdr = next(r for r in range(m.rowCount()) if m.header(r) is not None)
    assert m.flags(m.index(hdr, 0)) == QtCore.Qt.NoItemFlags


def test_keyboard_navigation_search_focus_and_escape(proj):
    from PySide6 import QtCore
    from PySide6.QtTest import QTest

    p = _panel(proj, "symbol", show=True)
    p.activateWindow()
    grid, search = p.grid_view(), p.search_field()
    p.set_query("agitator")
    first = p.current_entry()
    assert first is not None
    rows = [r for r in range(p._model.rowCount()) if p._model.entry(r) is not None]
    # Right walks entries in order, skipping the library headers
    grid.setFocus()
    grid.setCurrentIndex(p._model.index(rows[0], 0))
    seen = [p.current_entry().key]
    for _ in range(min(20, len(rows) - 1)):
        QTest.keyClick(grid, QtCore.Qt.Key_Right)
        seen.append(p.current_entry().key)
    assert seen == [p._model.entry(r).key for r in rows[: len(seen)]]
    QTest.keyClick(grid, QtCore.Qt.Key_Home)
    assert p.current_entry().key == p._model.entry(rows[0]).key
    QTest.keyClick(grid, QtCore.Qt.Key_End)
    assert p.current_entry().key == p._model.entry(rows[-1]).key
    # Ctrl+F focuses the search; typing in the grid goes to the search
    QTest.keyClick(grid, QtCore.Qt.Key_F, QtCore.Qt.ControlModifier)
    assert (
        search.hasFocus()
        or QtCore.QCoreApplication.instance().focusWidget() is search
        or search.selectedText() == "agitator"
    )
    QTest.keyClicks(grid, "x")
    assert search.text() == "agitatorx"
    # Esc clears the search
    p.set_query("agitator")
    QTest.keyClick(search, QtCore.Qt.Key_Escape)
    assert p.query() == "" and search.text() == ""
    # Down from the search enters the grid on the first card
    p.set_query("pump")
    grid.setCurrentIndex(QtCore.QModelIndex())
    QTest.keyClick(search, QtCore.Qt.Key_Down)
    assert (
        p.current_entry() is not None
        and "pump" in (p.current_entry().name + p.current_entry().library).lower()
    )
    p.close()


def test_view_options_card_size_list_mode_and_paint(proj):
    from ctgedit.ui.prefs.settings import app_settings

    p = _panel(proj, "genie", show=True)
    p.set_query("agitator")
    grid = p.grid_view()
    grid.doItemsLayout()
    idx = p._model.index(_row_of(p, p.current_entry()), 0)
    w_med = grid.visualRect(idx).width()
    cols_med = grid.columns()
    p.set_card_size(56)
    grid.relayout()
    w_small, cols_small = grid.visualRect(idx).width(), grid.columns()
    p.set_card_size(184)
    grid.relayout()
    w_big, cols_big = grid.visualRect(idx).width(), grid.columns()
    assert w_small < w_med < w_big and cols_small > cols_med >= cols_big >= 1
    # cards stretch to fill the row: the row is used up to one card's slack
    assert cols_med * (w_med + 6) > grid.usable_width() - w_med
    p.set_card_size(170)
    assert int(app_settings().value("librarypanel/card_size")) == 170
    assert p.card_size() == 170 and p._slider.value() == 170
    p.set_view_mode("list")
    assert str(app_settings().value("librarypanel/view_mode")) == "list"
    grid.doItemsLayout()
    r = grid.visualRect(idx)
    assert r.height() < 60 and r.width() > 250  # one full-width row
    p.set_grouped(False)
    assert not p.section_titles()
    assert str(app_settings().value("librarypanel/grouped")).lower() in ("false", "0")
    # thumbnails land in the background and the grid paints both modes
    p.wait_thumbnails(8000)
    img = p.grab()
    assert not img.isNull()
    p.set_view_mode("grid")
    p.wait_thumbnails(8000)
    assert not p.grab().isNull()
    assert p.card_pixmap(p.current_entry(), 80, 60) is not None
    # the hover preview shows the reference + facts
    from PySide6 import QtCore

    p.show_preview(p.current_entry(), QtCore.QRect(10, 10, 100, 100))
    pv = p.preview_widget()
    assert pv.isVisible() and p.current_entry().name in pv.title.text()
    assert "Double-click to place" in pv.info.text()
    p.hide_preview()
    assert not pv.isVisible()
    p.close()


def test_lazy_until_shown_and_cheap_to_repeat(proj):
    _app()
    from ctgedit.ui.panels.library_panel import LibraryPanel

    p = LibraryPanel()
    p.set_project_dir(proj)
    assert p._dirty and not p._data  # nothing built while hidden
    p.ensure_loaded()
    p.set_kind("genie")
    n = len(p.visible_entries())
    assert n > 0
    p.set_query("pump")
    sc = p.library_scope("fxmixer")
    assert sc is not None
    p.set_scope(sc)
    p.set_project_dir(proj)  # a page re-open keeps the state
    p.ensure_loaded()
    assert p.query() == "pump" and p.scope() == sc
    assert all(e.library == "fxmixer" for e in p.visible_entries())
    p.set_project_dir("")
    p.ensure_loaded()
    assert not p.visible_entries()


def test_large_chain_filters_fast(proj, fx_install):
    """A large root project's chain filters in plain Python in milliseconds. (Three
    generated libraries of 1,000 symbols each, fx_gen0001 .. fx_gen1000 and fx_valve0001 ..,
    in FxMain and FxLib: over 3,000 symbols on the chain.)"""
    import time

    _app()
    from ctgedit.editing import libraries_changed

    fx_install.big_library("FxMain", "fxbulk1.ctl", 1000)
    fx_install.big_library("FxMain", "fxbulk2.ctl", 1000, prefix="fx_valve")
    fx_install.big_library("FxLib", "fxbulk3.ctl", 1000, prefix="fx_val")
    libraries_changed()
    from ctgedit.ui.panels.library_panel import LibraryPanel

    p = LibraryPanel()
    p.resize(320, 800)
    p.set_project_dir(proj)
    p.ensure_loaded()
    p.set_kind("symbol")
    assert len(p.all_entries()) > 3000
    t = time.perf_counter()
    for q in ("v", "va", "val", "valv", "valve"):
        p.set_query(q)
    dt = (time.perf_counter() - t) / 5
    assert p.visible_entries()
    check_speed(dt, 0.5, "a search")


def test_copy_to_library_marks_a_wrong_library_name_as_an_error(proj):
    """The hint under the name: an empty field is a dim prompt, a library
    name that cannot be used is an error (also while the name is empty)."""
    _app()
    from ctgedit.ui.dialog.library_dialogs import CopyToLibraryDialog
    from ctgedit.ui.style.theme import COLORS

    dlg = CopyToLibraryDialog(proj, None, exts=("ctl",))
    try:
        from PySide6 import QtWidgets

        ok = dlg._buttons.button(QtWidgets.QDialogButtonBox.Ok)
        dlg._cmb_lib.setEditText("bad-name")
        dlg._update_hint()
        assert not ok.isEnabled() and COLORS["danger"] in dlg._hint.text()
        dlg._cmb_lib.setEditText("fresh_lib")
        dlg._update_hint()
        assert not ok.isEnabled() and COLORS["danger"] not in dlg._hint.text()
        dlg._ed_elem.setText("elem")
        dlg._update_hint()
        assert ok.isEnabled()
    finally:
        dlg.deleteLater()


def test_a_tab_switch_between_two_projects_takes_their_kept_lists(monkeypatch, proj, fx_install):
    """The lists of the last projects shown are kept: switching back to one takes them
    (their stamp is taken with each project's own libraries).
    FxMain and FxSa of the fake install."""
    from ctgedit.ui.panels.library_panel import LibraryPanel

    main = proj
    other = fx_install.project("FxSa")
    _app()
    p = LibraryPanel()
    loads = []
    real = LibraryPanel._load_chain
    monkeypatch.setattr(
        LibraryPanel, "_load_chain", lambda self: loads.append(self._project_dir) or real(self)
    )
    for d in (main, other):
        p.set_project_dir(d)
        p.ensure_loaded()
    p.set_kind("genie")  # (a kind loaded after the lists were)
    assert len(loads) == 2
    first = {}
    for k in range(6):
        d = main if k % 2 == 0 else other
        p.set_project_dir(d)
        p.ensure_loaded()
        rows = [e.key for e in p._model.entries()]
        assert rows and first.setdefault(d, rows) == rows
    assert len(loads) == 2  # no switch read a chain again
    # a library changed meanwhile: that project's lists are built again
    from ctgedit import editing as E

    E.libraries_changed()
    p.set_project_dir(main)
    p.ensure_loaded()
    assert loads[-1] == main and len(loads) == 3
