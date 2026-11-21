"""The shared picker widgets."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app

#: the style with no title bar forms only, the one with title bar forms only (with stored
#: thumbnails) and the one with both forms of normal@XGA
NO_TITLE, TITLE_ONLY, BOTH = "fx_sa", "fx_hd", "fx_style"


# ------------------------------------------------------------------ TemplatePicker
def test_template_picker_lists_the_include_chains_styles_and_preselects(fx_install):
    _app()
    from ctgedit.ui.pick_fields import TemplatePicker

    p = TemplatePicker(fx_install.project("FxMain"))
    styles = [p.cb_style.itemText(i) for i in range(p.cb_style.count())]
    assert NO_TITLE in styles  # FxStyle
    assert not p.cb_style.isEditable() and not p.cb_res.isEditable()
    p.set_current(NO_TITLE, "pagecontent@-HD1080")
    assert p.style() == NO_TITLE
    res = [p.cb_res.itemData(i) for i in range(p.cb_res.count())]
    assert res[0] == "" and p.cb_res.itemText(0) == "All resolutions"
    assert {"HD1080", "UHD4K"} <= set(res)
    assert p.resolution() == "HD1080" and p.current_name() == "pagecontent"
    path, element = p.value()
    assert element == "pagecontent@-HD1080"
    assert os.path.basename(os.path.dirname(path)) == "FxStyle"
    assert p.template_ref() == NO_TITLE + ".pagecontent@-HD1080.<ctt"
    # the fx_sa templates exist without a title bar only: the box follows them
    assert not p.chk_titlebar.isEnabled() and not p.chk_titlebar.isChecked()
    assert "no title bar" in p.info.text() and "FxStyle" in p.info.text()
    # the grid shows thumbnails at the shared card size
    from ctgedit.ui.browser.thumbs import CARD_SIZE

    assert p.list.iconSize() == CARD_SIZE
    # another template in the grid: value and changed follow
    seen = []
    p.changed.connect(lambda: seen.append(p.value()))
    row = next(i for i in range(p.list.count()) if p.list.item(i).text().startswith("blank"))
    p.list.setCurrentRow(row)
    assert seen and seen[-1][1] == "blank@-HD1080"


def test_template_catalog_is_kept_and_reads_thumbnails_when_drawn(tmp_path, monkeypatch, fx_install):
    """The template catalog of a project is kept while its template libraries are the same files,
    and a thumbnail is read when a card or the preview first draws it (the same picture)."""
    _app()
    import shutil
    from PySide6 import QtCore
    from ctgedit.format import pagesummary
    from ctgedit.ui import pick_fields as PF
    from ctgedit.infra import caches

    caches.clear("refresh")  # (a picker of an earlier test drew some)
    main = fx_install.project("FxMain")
    a = PF.TemplateCatalog.for_project(main)
    assert PF.TemplateCatalog.for_project(main) is a
    path, s = a.find(TITLE_ONLY, "normal@HD1080")  # (it stores a thumbnail)
    assert s.thumb_bmp == b"" and s.thumb_pending
    full = pagesummary.read_element_summary(path, s.name)
    assert PF.template_thumbnail(s) == full.thumb_bmp and full.thumb_bmp
    assert not s.thumb_pending
    pm = PF.thumbnail_pixmap(s, QtCore.QSize(120, 80))
    assert not pm.isNull()
    # the same listing as a catalog read with the thumbnails
    fresh = PF.TemplateCatalog(main)
    assert fresh.styles() == a.styles()
    for style in a.styles():
        assert [(p, x.name) for p, x in a.elements(style)] == [(p, x.name) for p, x in fresh.elements(style)]
    # another template library file (a copy of the project with its own .ctt): read again
    proj = tmp_path / "Proj"
    proj.mkdir()
    lib = os.path.join(os.path.dirname(path), os.path.basename(path))
    shutil.copyfile(lib, proj / "mystyle.ctt")
    monkeypatch.setenv("CTG_LIB_PATH", "")
    b = PF.TemplateCatalog.for_project(str(proj))
    assert b.has_style("mystyle") and PF.TemplateCatalog.for_project(str(proj)) is b
    os.utime(proj / "mystyle.ctt", ns=(1, 1))
    assert PF.TemplateCatalog.for_project(str(proj)) is not b
    assert "pick_fields.template_catalogs" in caches.registered()


def test_template_picker_keeps_a_template_that_is_not_found(fx_install):
    _app()
    from ctgedit.ui.pick_fields import TemplatePicker

    p = TemplatePicker(fx_install.project("FxMain"))
    p.set_current(NO_TITLE, "nothere@-HD1080")
    assert p.is_missing() and p.value() is None and p.template_ref() == ""
    assert p.list.currentItem().text() == NO_TITLE + ".nothere@-HD1080 (not found)"
    # a style that is not on the include path at all
    p.set_current("nostyle", "normal@HD1080")
    assert p.cb_style.currentText() == "nostyle (not found)"
    assert p.list.count() == 1 and p.value() is None
    # back to a real one: the missing entries go
    p.set_current(NO_TITLE, "blank@-HD1080")
    assert p.cb_style.findText("nostyle (not found)") < 0
    assert not p.is_missing() and p.value()[1] == "blank@-HD1080"


def test_template_picker_title_bar_follows_the_variants(fx_install):
    _app()
    from ctgedit.ui.pick_fields import TemplatePicker

    p = TemplatePicker(fx_install.project("FxMain"))
    # fx_hd templates exist only with a title bar: checked and fixed
    p.set_current(TITLE_ONLY, "normal@HD1080")
    assert p.value()[1] == "normal@HD1080"
    assert p.chk_titlebar.isChecked() and not p.chk_titlebar.isEnabled()
    # fx_style has both forms of 'normal' at XGA: the box is free
    both = next(
        (
            s
            for s in (p.cb_style.itemText(i) for i in range(p.cb_style.count()))
            if {"normal@XGA", "normal@-XGA"} <= {x[1].name for x in p.catalog.elements(s)}
        ),
        None,
    )
    assert both == BOTH
    p.set_current(both, "normal@-XGA")
    assert p.chk_titlebar.isEnabled() and not p.chk_titlebar.isChecked()
    p.chk_titlebar.setChecked(True)
    assert p.value()[1] == "normal@XGA"


# ------------------------------------------------------------------ PageField
def test_page_field_lists_the_chain_pages_and_keeps_an_unknown_value(fx_install):
    _app()
    from ctgedit.io.project import chain_page_names
    from ctgedit.ui.pick_fields import PageField

    main = fx_install.project("FxMain")
    names = chain_page_names(main)
    names = [n if isinstance(n, str) else n[0] for n in names]
    assert "!FxSystem" in names
    f = PageField(main, value="FxShapes", exclude="FxGenies", system_pages=False)
    # (the names are listed when the list opens or the field takes the focus)
    assert not f.is_filled() and f.count() == 1 and f.value() == "FxShapes"
    f.fill()
    assert f.is_filled() and f.value() == "FxShapes"
    items = [f.itemText(i) for i in range(f.count())]
    assert items[0] == "<None>" and f.isEditable()
    assert "FxShapes" in items and "FxGenies" not in items
    assert not any(t.startswith("!") for t in items)
    assert f.value() == "FxShapes"
    f.set_value("")
    assert f.currentText() == "<None>" and f.value() == ""
    f.set_value("NoSuchPage")
    assert f.value() == "NoSuchPage"  # kept as typed
    with_sys = PageField(main, system_pages=True)
    with_sys.fill()
    assert any(with_sys.itemText(i).startswith("!") for i in range(with_sys.count()))
    from PySide6 import QtCore

    comp = f.completer()
    assert comp is not None and comp.caseSensitivity() == QtCore.Qt.CaseInsensitive


def test_page_field_lists_its_names_when_the_list_opens_or_it_takes_the_focus():
    """A page field's names are listed when first needed, and what the field shows stays."""
    _app()
    from PySide6 import QtCore, QtGui
    from ctgedit.ui.pick_fields import PageField

    names = ["Mixer", "Genies", "Pump", "!Popup"]
    f = PageField(pages=names, value="Typed", exclude="Genies", system_pages=False)
    assert not f.is_filled() and f.count() == 1 and f.currentText() == "Typed"
    f.showPopup()
    assert f.is_filled() and f.currentText() == "Typed" and f.value() == "Typed"
    assert [f.itemText(i) for i in range(f.count())] == ["<None>", "Mixer", "Pump"]
    f.hidePopup()
    f.fill()  # once only
    assert f.count() == 3
    g = PageField(pages=names, value="", system_pages=True)
    changed = []
    g.editTextChanged.connect(changed.append)
    g.currentIndexChanged.connect(changed.append)
    g.focusInEvent(QtGui.QFocusEvent(QtCore.QEvent.FocusIn, QtCore.Qt.TabFocusReason))
    assert g.is_filled() and g.value() == "" and g.currentText() == "<None>" and not changed
    assert g.pages() == names
    h = PageField(pages=names, system_pages=False)
    assert not h.is_filled()
    assert h.pages() == ["Mixer", "Genies", "Pump"] and h.is_filled()


def test_page_field_width_does_not_follow_the_longest_page_name():
    """The field is not sized to its longest name (50-character page names), so it does not
    widen the Properties panel or move the canvas zoom."""
    _app()
    from ctgedit.ui.pick_fields import PageField

    short = PageField(pages=["Mixer", "Genies"])
    long_ = PageField(pages=["Mixer", "FxArea21_Room1_A_Very_Long_Page_Name_For_Width_01"])
    assert long_.minimumSizeHint().width() == short.minimumSizeHint().width()
    assert long_.sizeHint().width() == short.sizeHint().width()


# ------------------------------------------------------------------ LibraryElementField
def test_library_element_field_browse_clear_and_edit(monkeypatch):
    _app()
    from ctgedit.ui import pick_fields as PF
    from ctgedit.ui.browser import PageBrowserDialog

    asked = []

    def fake(parent, project_dir=None, kind="symbol", **kw):
        asked.append((project_dir, kind, kw.get("title")))
        return (r"C:\x\thumbs.ctl", "pointer_s_red")

    monkeypatch.setattr(PageBrowserDialog, "get_library_element", staticmethod(fake))
    f = PF.LibraryElementField(
        r"C:\proj", "symbol", title="Select Symbol", clear_text="Clear", edit_text="Edit Symbol"
    )
    assert f.btn_browse.text() == "Browse…" and f.value() is None and f.text() == ""
    changes = []
    f.changed.connect(lambda: changes.append(f.value()))
    assert f.browse() is True
    assert asked == [(r"C:\proj", "symbol", "Select Symbol")]
    assert f.value() == ("thumbs", "pointer_s_red") and f.text() == "thumbs.pointer_s_red"
    assert f.path() == r"C:\x\thumbs.ctl" and changes == [("thumbs", "pointer_s_red")]
    assert f.name.text() == "thumbs.pointer_s_red"
    assert f.btn_clear.isEnabled() and f.btn_edit.isEnabled()
    edits = []
    f.editRequested.connect(lambda: edits.append(1))
    f.btn_edit.click()
    assert edits == [1]
    f.btn_clear.click()
    assert f.value() is None and changes[-1] is None and not f.btn_clear.isEnabled()
    f.setText("icons.note")
    assert f.value() == ("icons", "note")
    f.set_read_only(True)
    assert not f.btn_browse.isEnabled() and f.browse() is False


def test_library_element_field_validate_refuses_with_the_picker_title(monkeypatch):
    _app()
    from ctgedit.ui import pick_fields as PF

    warned = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        staticmethod(lambda parent, title, text: warned.append((title, text))),
    )
    f = PF.LibraryElementField(
        None,
        "symbol",
        title="Select Symbol",
        picker=lambda parent: (r"C:\x\tab_base.ctl", "page_footer"),
        validate=lambda lib, el, path: "The symbol must be a bitmap.",
    )
    assert f.browse() is False and f.value() is None
    assert warned == [("Select Symbol", "The symbol must be a bitmap.")]


def test_template_catalog_without_a_folder_reads_no_current_directory(tmp_path, monkeypatch):
    """A page with no folder yet: the catalog lists only CTG_LIB_PATH's
    templates, never the .ctt files of the current directory."""
    import shutil
    import fxinstall
    from ctgedit.ui.pick_fields import TemplateCatalog

    src = os.path.join(fxinstall.FX_INSTALL, "User", "FxStyle", TITLE_ONLY + ".ctt")
    here = tmp_path / "cwd"
    here.mkdir()
    shutil.copy(src, here)
    monkeypatch.chdir(here)
    monkeypatch.delenv("CTG_LIB_PATH", raising=False)
    assert TemplateCatalog("").styles() == []
    monkeypatch.setenv("CTG_LIB_PATH", str(here))
    assert TITLE_ONLY in TemplateCatalog("").styles()


def test_template_picker_shows_the_current_template_when_it_is_shown(fx_install):
    """The grid lays out when it is shown (a Page Properties tab switched to):
    the chosen template is scrolled into view then."""
    app = _app()
    from ctgedit.ui.pick_fields import TemplatePicker

    tabs = QtWidgets.QTabWidget()
    tabs.addTab(QtWidgets.QWidget(), "First")
    p = TemplatePicker(fx_install.project("FxMain"))
    tabs.addTab(p, "Template")
    p.set_current(TITLE_ONLY, "normal@HD768")
    last = p.list.item(p.list.count() - 1)
    p.list.setCurrentItem(last)
    tabs.resize(700, 420)
    tabs.show()
    app.processEvents()
    tabs.setCurrentIndex(1)
    for _ in range(5):
        app.processEvents()
    rect = p.list.visualItemRect(last)
    assert p.list.viewport().rect().intersects(rect)
    tabs.close()
