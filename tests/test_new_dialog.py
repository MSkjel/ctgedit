"""File > New."""

import os

import pytest
from qtkit import app as _app

FIRST, SECOND = "FxMain", "FxOther"


@pytest.fixture
def catalog(fx_install):
    from ctgedit.io.catalog import catalog_for

    cat = catalog_for(fx_install.user)
    cat.fx_start = fx_install.page("FxShapes")
    cat.fx_first = fx_install.project(FIRST)
    cat.fx_second = fx_install.project(SECOND)
    return cat


def _new(catalog, start=None):
    _app()
    from ctgedit.ui.browser import PageBrowserDialog

    return PageBrowserDialog(PageBrowserDialog.NEW, catalog=catalog, start_path=start or catalog.fx_start)


def _target(dlg):
    from ctgedit.ui.browser.new_mode import target_info

    return target_info(dlg)


def _close(dlg):
    dlg.reject()
    dlg.deleteLater()
    _app().processEvents()


def test_new_is_the_page_browser_with_a_create_bar(catalog):
    dlg = _new(catalog)
    try:
        # the browser's own parts
        assert dlg.tree is not None and dlg.preview is not None and dlg.grid is not None
        assert dlg.windowTitle() == "New"
        dlg.set_kind("page")
        assert dlg.stack.currentWidget() is dlg.tpl_box and dlg.tpl_picker is not None
        assert dlg.ok_button.text() == "Create Page" and not dlg.cb_library.isVisible()
        assert _target(dlg).name == FIRST
        assert dlg.ed_name.text().lower().startswith("newpage")
        dlg.set_kind("genie")
        assert dlg.stack.currentWidget() is dlg.grid
        assert dlg.model.rowCount() > 0  # the project's genies, as cards
        assert dlg.ok_button.text() == "Create Genie"
        assert not dlg.ok_button.isEnabled()  # no name yet
        assert "Type the name" in dlg.msg.text()
    finally:
        _close(dlg)


def test_new_remembers_the_kind_the_project_and_the_library(catalog):
    dlg = _new(catalog)
    try:
        dlg.set_kind("symbol")
        assert dlg.set_target_project(SECOND)
        libs = [dlg.cb_library.itemText(i) for i in range(dlg.cb_library.count())]
        assert libs, "FxOther has a symbol library"
        lib_name = libs[-1]
        dlg.cb_library.setCurrentIndex(dlg.cb_library.count() - 1)
        dlg.ed_name.setText("brand_new_symbol")
        assert dlg.ok_button.isEnabled()
        assert dlg.accept_current()
        t = dlg.new_target
        assert t.kind == "symbol" and t.element == "brand_new_symbol"
        assert os.path.normcase(os.path.dirname(t.path)) == os.path.normcase(catalog.fx_second)
        assert os.path.splitext(os.path.basename(t.path))[0] == lib_name
        # (nothing written)
        assert not os.path.exists(os.path.join(catalog.fx_second, "brand_new_symbol"))
    finally:
        dlg.deleteLater()
        _app().processEvents()
    # opened again from a page of another project: where the last one was made
    dlg = _new(catalog)
    try:
        assert dlg.current_kind() == "symbol"
        assert _target(dlg).name == SECOND
        assert dlg.cb_library.currentText() == lib_name
    finally:
        _close(dlg)


def test_a_card_gives_its_library_and_a_free_name(catalog):
    dlg = _new(catalog)
    try:
        dlg.set_kind("genie")
        e = next(x for x in dlg.model.all_entries() if x.library)
        dlg._new_on_pick(e)
        assert os.path.normcase(dlg._new_library_path()) == os.path.normcase(e.path)
        name = dlg.ed_name.text()
        assert name.lower() != e.name.lower() and name.lower().startswith(e.name.lower()[:20])
        assert dlg.ok_button.isEnabled()
    finally:
        _close(dlg)


def test_a_new_library_name_is_checked_and_created_in_the_project(catalog):
    dlg = _new(catalog)
    try:
        dlg.set_kind("genie")
        dlg._new_library()
        dlg.cb_library.setEditText("my new lib")  # a space: CtDraw refuses it
        dlg.ed_name.setText("pump")
        assert not dlg.ok_button.isEnabled()
        dlg.cb_library.setEditText("mynewlib")
        assert dlg.ok_button.isEnabled()
        assert "A new library mynewlib.ctm will be created." in dlg.msg.text()
        assert os.path.normcase(dlg._new_library_path()) == os.path.normcase(
            os.path.join(catalog.fx_first, "mynewlib.ctm")
        )
    finally:
        _close(dlg)


def test_a_super_genie_starts_with_an_exclamation_mark(catalog):
    dlg = _new(catalog)
    try:
        dlg.set_kind("supergenie")
        assert dlg.ed_name.text() == "!" and not dlg.ok_button.isEnabled()
        dlg.ed_name.setText("!MyPopup")
        assert dlg.ok_button.isEnabled()
        assert dlg.accept_current()
        assert dlg.new_target.kind == "supergenie" and dlg.new_target.element == "!MyPopup"
        assert dlg.new_target.path.lower().endswith(".ctm")
    finally:
        dlg.deleteLater()
        _app().processEvents()


def test_a_new_page_takes_the_template_and_remembers_it(catalog):
    dlg = _new(catalog)
    try:
        dlg.set_kind("page")
        p = dlg.tpl_picker
        assert p.value() is not None
        # (FxMain's PROJECT.CIT says XGA; fx_style's blank is an SVGA template)
        p.cb_res.setCurrentIndex(p.cb_res.findData(""))  # All resolutions
        names = [p.list.item(i).data(0x0100) for i in range(p.list.count())]
        pick = next(i for i, k in enumerate(names) if isinstance(k, tuple) and str(k[0]).lower() == "blank")
        p.list.setCurrentRow(pick)
        ref = p.template_ref()
        assert "blank" in ref.lower()
        dlg.chk_new_linked.setChecked(False)
        dlg.ed_name.setText("FxShapes")  # already in FxMain
        assert not dlg.ok_button.isEnabled() and "already exists" in dlg.msg.text()
        dlg.ed_name.setText("BrandNewPage")
        assert dlg.accept_current()
        t = dlg.new_target
        assert (t.kind, t.template_ref, t.linked) == ("page", ref, False)
        assert os.path.normcase(t.path) == os.path.normcase(
            os.path.join(catalog.fx_first, "BrandNewPage.ctg")
        )
        assert not os.path.exists(t.path)
    finally:
        dlg.deleteLater()
        _app().processEvents()
    dlg = _new(catalog)
    try:
        dlg.set_kind("page")
        assert dlg.tpl_picker.template_ref() == ref
        assert not dlg.chk_new_linked.isChecked()
    finally:
        _close(dlg)


def test_the_tree_and_the_bar_choose_the_same_project(catalog):
    dlg = _new(catalog)
    try:
        dlg.set_kind("genie")
        info = catalog.project(SECOND)
        assert dlg.tree.select_project(info)
        assert _target(dlg).name == SECOND
        assert all(e.project == SECOND for e in dlg.model.all_entries())
        dlg.set_target_project(FIRST)
        base = dlg.tree.current_base_scope()
        assert base is not None and base.anchor.name == FIRST
    finally:
        _close(dlg)


def test_unique_element_name_keeps_the_31_character_limit():
    from ctgedit.ui.browser.new_mode import unique_element_name

    assert unique_element_name("pump", ["pump", "pump_2"]) == "pump_3"
    assert unique_element_name("pump_7", ["pump_7"]) == "pump_8"
    long = "x" * 31
    out = unique_element_name(long, [long])
    assert len(out) == 31 and out.endswith("_2")
    assert unique_element_name("", []) == "New"


def test_file_new_creates_what_the_window_answers(monkeypatch, tmp_path):
    """The window's answer opens the new element (nothing written until its first Save)."""
    _app()
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.browser.new_mode import NewTarget
    from ctgedit.ui.main_window import MainWindow

    lib = str(tmp_path / "fresh.ctm")
    monkeypatch.setattr(
        PageBrowserDialog, "get_new", staticmethod(lambda *a, **k: NewTarget("genie", lib, "my_genie"))
    )
    w = MainWindow()
    try:
        w.new_blank_dialog()
        assert w.doc is not None and w.doc.element_ref == (lib, "my_genie", "genie")
        assert not os.path.exists(lib)
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.deleteLater()


def test_file_new_page_end_to_end(fx_install, monkeypatch):
    """Ctrl+N with the window driven as a user would: the Page tab, a name, Create Page. The
    page opens from its template; nothing is written (the first Save writes it)."""
    _app()
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.main_window import MainWindow

    first = fx_install.project(FIRST)

    def drive(dlg):
        dlg.set_kind("page")
        dlg.set_target_project(FIRST)
        dlg.ed_name.setText("ZzNewFromTest")
        assert dlg.accept_current()
        return dlg.result()

    monkeypatch.setattr(PageBrowserDialog, "_run", staticmethod(drive))
    w = MainWindow()
    try:
        w.new_blank_dialog()
        path = os.path.join(first, "ZzNewFromTest.ctg")
        assert w.doc is not None and os.path.normcase(w.doc.path) == os.path.normcase(path)
        assert w.doc.dirty and not os.path.exists(path)
        assert w.doc.template_status()[0] == "current"
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.deleteLater()


def test_edit_template_opens_the_selected_template(catalog):
    dlg = _new(catalog)
    try:
        dlg.set_kind("page")
        v = dlg.tpl_picker.value()
        assert v is not None and dlg.btn_edit_template.isEnabled()
        dlg.btn_edit_template.click()
        assert dlg.new_target.kind == "edit_template"
        assert (dlg.new_target.path, dlg.new_target.element) == v
    finally:
        dlg.deleteLater()
        _app().processEvents()


def test_the_pages_of_the_projects_compiled_together_are_read_once(catalog, monkeypatch):
    """The page names of every project compiled with the target are kept between New / Save As
    runs while those projects' folders and pages.DBF tables are the same."""
    from ctgedit.ui.browser import dialog as D

    calls = []
    real = catalog.pages_elsewhere
    monkeypatch.setattr(catalog, "pages_elsewhere", lambda info: calls.append(info.key) or real(info))
    D._ELSEWHERE.clear()
    info = catalog.project_for_dir(catalog.fx_first)
    a = _new(catalog)
    first = a._pages_elsewhere(info)
    _close(a)
    b = _new(catalog)
    assert b._pages_elsewhere(info) == first
    _close(b)
    assert calls == [info.key]
    # a folder or pages.DBF of the group changed (its stamp): read again
    stamps, names = D._ELSEWHERE[catalog][info.key]
    D._ELSEWHERE[catalog][info.key] = (stamps + (("changed",),), names)
    c = _new(catalog)
    assert c._pages_elsewhere(info) == first
    _close(c)
    assert calls == [info.key, info.key]
