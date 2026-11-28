"""Window basics: settings, shortcuts, narrow windows, unsaved edits."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app


@pytest.fixture
def EX(fx_project):
    """FxMain of the fake install copy."""
    return fx_project("FxMain")


@pytest.fixture
def MIXER(fx_page):
    """The big page with a template (FxPerf's FxLarge in the fake install copy)."""
    return fx_page("FxLarge", "FxPerf")


@pytest.fixture()
def win(tmp_path):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)


def _copy_mixer(MIXER, tmp_path):
    import shutil

    dst = tmp_path / "Mixer.ctg"
    shutil.copy2(MIXER, dst)
    return str(dst)


def test_settings_go_to_the_test_ini():
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    assert st.format() == QtCore.QSettings.Format.IniFormat
    assert st.fileName().endswith("ctgedit_settings.ini")


def test_no_ambiguous_window_shortcuts(win):
    seen = {}
    for a in win.findChildren(QtGui.QAction):
        if a.shortcutContext() != QtCore.Qt.WindowShortcut:
            continue
        for seq in a.shortcuts():
            k = seq.toString()
            if k:
                assert k not in seen or seen[k] is a, (k, seen.get(k) and seen[k].text(), a.text())
                seen[k] = a
    assert win.act_save_as.shortcut().toString() == "Ctrl+Shift+S"


def test_arrows_and_tool_letters_are_canvas_scoped(win):
    for a in win.tool_actions.values():
        if a.shortcut().toString() in ("R", "E", "L", "T", "V"):
            assert a.shortcutContext() == QtCore.Qt.WidgetWithChildrenShortcut
    # no window-wide bare arrow action remains
    for a in win.findChildren(QtGui.QAction):
        if a.shortcut().toString() in ("Left", "Right", "Up", "Down"):
            assert a.shortcutContext() != QtCore.Qt.WindowShortcut


def test_tree_has_no_second_delete_shortcut(win):
    tree = win.objects_dock._tree
    for sc in tree.findChildren(QtGui.QShortcut):
        assert sc.key() != QtGui.QKeySequence(QtGui.QKeySequence.Delete)


def test_palette_does_not_break_dynamic_menus(win):
    win.palette_popup.open_at()
    win.palette_popup.hide()
    win._fill_mru()  # must not raise RuntimeError
    win._rebuild_labels_menu()
    win._fill_window_list()


def test_window_can_be_narrow(win):
    win.show()
    _app().processEvents()
    assert win.minimumSizeHint().width() < 900


def test_open_asks_before_discarding_edits(win, MIXER, tmp_path, monkeypatch):
    p = _copy_mixer(MIXER, tmp_path)
    win.open_document(p)
    win.doc.move_objects([win.doc.page.roots()[-1]], 3, 3)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Cancel)
    doc = win.doc
    win.open_document(p)
    assert win.doc is doc and doc.dirty  # cancelled: nothing replaced


def test_page_keeps_its_name_after_an_edit(win, MIXER, tmp_path):
    win.open_document(_copy_mixer(MIXER, tmp_path))
    i = next(
        k
        for k, o in enumerate(win.doc.page.objects)
        if o.kind.value == "rectangle" and o.parent is None and not o.raw.get("_from_template")
    )
    win.doc.set_line_width(i, 3)
    assert win.doc.page.name == "Mixer"


def test_select_all_skips_locked_template(win, MIXER, tmp_path):
    win.open_document(_copy_mixer(MIXER, tmp_path))
    win.select_all()
    assert all(not win._unit_locked(r) for r in win._sel)


def test_convert_to_bitmap_is_one_undo_step(win, MIXER, tmp_path):
    win.open_document(_copy_mixer(MIXER, tmp_path))
    n0 = len(win.doc.parsed.page.objects)
    r = next(
        k
        for k in win.doc.page.roots()
        if win.doc.page.objects[k].kind.value == "rectangle" and not win._unit_locked(k)
    )
    win._sel = [r]
    win._convert_to_bitmap()
    po = win.doc.parsed.page.objects[win._sel[0]]
    assert po.type_code == 0x07
    win.undo()
    assert len(win.doc.parsed.page.objects) == n0
    assert win.doc.parsed.page.objects[r].type_code == 0x0F


def test_send_to_back_stays_above_the_template(MIXER, tmp_path):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy_mixer(MIXER, tmp_path))
    tpl_i, _ = doc.template_reference()
    tpl_n = len(doc.subtree(tpl_i))
    r = doc.page.roots()[-1]
    po = doc.parsed.page.objects[r]
    doc.reorder([r], "back")
    assert doc.parsed.page.objects.index(po) == tpl_n


def test_new_page_keeps_the_template(MIXER, tmp_path):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.new_blank(MIXER, str(tmp_path / "NewPage.ctg"))
    tpl_i, ref = doc.template_reference()
    assert ref is not None and ref.ext == "ctt"
    assert len(doc.page.objects) == len(doc.subtree(tpl_i))


def test_zoom_is_clamped(win):
    for _ in range(80):
        win.view.zoom_by(1.5)
    assert win.view.zoom() <= win.view.ZOOM_MAX + 1e-6
    for _ in range(200):
        win.view.zoom_by(0.5)
    assert win.view.zoom() >= win.view.ZOOM_MIN - 1e-6


def test_property_dialog_never_edits_another_object_after_structural_edit(win, MIXER, tmp_path):
    """A structural edit shifts the sheet's object: the sheet follows it
    (re-targeted) or, when it cannot, closes -- it never stays on the old
    index, where another object is now."""
    win.open_document(_copy_mixer(MIXER, tmp_path))
    roots = [r for r in win.doc.page.roots() if not win._unit_locked(r)]
    target = roots[-1]
    rec = win.doc.parsed.page.objects[target]
    win.object_properties(target)
    dlg = win._dialogs.get(("objprops", target))
    assert dlg is not None
    win._delete_indices([roots[-2]])  # shifts the target's index
    objs = win.doc.parsed.page.objects
    if dlg in win._dialogs.values():
        assert objs[dlg._ctg_index] is rec
        assert win._dialogs.get(("objprops", dlg._ctg_index)) is dlg
    else:
        assert "closed" in win.statusBar().currentMessage()
    assert ("objprops", target) not in win._dialogs or objs[target] is rec


def test_multi_selection_edit_is_one_undo_step(MIXER, tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G

    doc = EditableDocument.open(_copy_mixer(MIXER, tmp_path))
    rects = [
        i for i, p in enumerate(doc.parsed.page.objects) if p.type_code == 0x0F and (p.subtype_size & 2)
    ][:3]
    assert len(rects) == 3
    before = [G.style_flags(doc.parsed.page.objects[i])[G.FLAG_LINE_WIDTH] for i in rects]
    n_undo = len(doc._undo)
    with doc.batch("line width"):
        for i in rects:
            doc.set_line_width(i, 5)
    assert len(doc._undo) == n_undo + 1
    assert all(G.style_flags(doc.parsed.page.objects[i])[G.FLAG_LINE_WIDTH] == 5 for i in rects)
    doc.undo()
    assert [G.style_flags(doc.parsed.page.objects[i])[G.FLAG_LINE_WIDTH] for i in rects] == before


def test_property_panel_multi_selection_has_editors(win, MIXER, tmp_path):
    win.open_document(_copy_mixer(MIXER, tmp_path))
    roots = [
        r
        for r in win.doc.page.roots()
        if not win._unit_locked(r) and win.doc.page.objects[r].kind.value in ("rectangle", "text")
    ][:4]
    win.props.set_target(win.doc, roots)
    boxes = [b.title() for b in win.props.widget().findChildren(QtWidgets.QGroupBox)]
    assert "Selection" in boxes


def test_document_tabs_keep_each_page(win, MIXER, EX, tmp_path, monkeypatch):
    import shutil

    a = _copy_mixer(MIXER, tmp_path)
    b = str(tmp_path / "Genies.ctg")
    shutil.copy2(os.path.join(EX, "FxGenies.ctg"), b)
    win.open_document(a)
    doc_a = win.doc
    doc_a.move_objects([doc_a.page.roots()[-1]], 4, 4)  # unsaved edit in A
    win.open_document(b)  # opens a 2nd tab
    assert win.doc is not doc_a and len(win.open_documents()) == 2
    assert doc_a.dirty  # nothing discarded
    win.open_document(a)  # back to A's tab
    assert win.doc is doc_a and win.doc_tabs.currentIndex() == 0
    assert win.doc.can_undo()  # its own history
    # closing a dirty tab asks; Cancel keeps it
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Cancel)
    assert win._close_doc_tab(0) is False and len(win.open_documents()) == 2
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Discard)
    assert win._close_doc_tab(0) is True
    assert len(win.open_documents()) == 1 and win.doc is not doc_a
    win._close_doc_tab(0)
    assert win.doc is None and not win.doc_tabs.isVisible()


def test_tool_letter_in_tree_does_not_switch_tool(win, MIXER, tmp_path):
    from PySide6.QtTest import QTest

    win.open_document(_copy_mixer(MIXER, tmp_path))
    win.show()
    _app().processEvents()
    tree = win.objects_dock._tree
    tree.setFocus()
    _app().processEvents()
    QTest.keyClick(tree, QtCore.Qt.Key_R)
    _app().processEvents()
    assert win.view._tool == "select"
    win.view.setFocus()
    _app().processEvents()
    QTest.keyClick(win.view.viewport(), QtCore.Qt.Key_R)
    _app().processEvents()
    assert win.view._tool == "rectangle"


def test_start_page_lists_recent_pages(MIXER, EX, tmp_path):
    _app()
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.main_window import MainWindow

    app_settings().setValue("mru", [MIXER, os.path.join(EX, "FxGenies.ctg")])
    w = MainWindow()
    try:
        w.show()
        _app().processEvents()
        assert w.start_page.isVisible()
        assert len(w.start_page._cards) == 2
    finally:
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)


def test_library_panel_covers_the_include_chain(EX):
    """FxMain includes FxLib and FxStyle, which include Include: the symbol libraries of
    the whole chain are listed (FxLib's four with elements, Include's eight), not only
    the project's own (it has none)."""
    _app()
    from ctgedit.ui.panels.library_panel import LibraryPanel

    d = LibraryPanel()
    d.set_project_dir(EX)
    d.ensure_loaded()
    libs = sorted((p.name, os.path.basename(lib.path).lower()) for p, lib in d.libraries())
    assert libs == [
        ("FxLib", "fxbmpgrp.ctl"),
        ("FxLib", "fxmany.ctl"),
        ("FxLib", "fxmore.ctl"),
        ("FxLib", "fxsym.ctl"),
    ] + [
        ("Include", n)
        for n in (
            "cicode.ctl",
            "fxicons16.ctl",
            "fxinc.ctl",
            "fxprio.ctl",
            "icons.ctl",
            "lights.ctl",
            "lights_square_small.ctl",
            "thumbs.ctl",
        )
    ]


def test_literal_mode_draws_ctdraws_display_strings(EX):
    """CtDraw's editor draws a string display as '=' + TXT_STR and a numeric
    one as its format mask (CtDraw takes CtDraw's string)."""
    from ctgedit.io import reader

    reader.set_label_mode("literal")
    try:
        pg = reader.read_page(os.path.join(EX, "FxTextKinds.ctg"))
        texts = {o.style.text for o in pg.objects if o.style.text}
        assert '="@(Fx Menu)"' in texts and '="@(Fx Home)"' in texts
        assert "##.#" in texts  # a numeric one: its format mask
    finally:
        reader.set_label_mode("native")


def test_save_warns_when_the_file_changed_on_disk(win, MIXER, tmp_path, monkeypatch):
    p = _copy_mixer(MIXER, tmp_path)
    win.open_document(p)
    win.doc.move_objects([win.doc.page.roots()[-1]], 2, 2)
    import time

    time.sleep(0.02)
    with open(p, "ab") as f:  # somebody else rewrote the page
        f.write(b"")
    os.utime(p, None)
    data = open(p, "rb").read()
    open(p, "wb").write(data)
    assert win.doc.changed_on_disk()
    asked = []

    def answer(text):
        def run(box):
            asked.append(box.text())
            for b in box.buttons():
                if b.text().replace("&", "") == text:
                    b.click()
            return 0

        return run

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", answer("Cancel"))
    assert win._save_to() is False and asked and "Overwrite" in asked[0]
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", answer("Overwrite"))
    assert win._save_to() is True
    assert not win.doc.changed_on_disk()
    assert not os.path.exists(p + ".ctgedit-tmp")


def test_opening_a_library_file_fails_cleanly(win, fx_install, monkeypatch):
    """A .ctm / .ctl / .ctt is not a page: the Open dialog routes an element to
    element editing, and a library path given to open_document raises a clear
    error instead of leaving a page-less document that crashes the refresh."""
    import pytest as _pt

    lib = fx_install.file("FxLib", "fxgenie.ctm")
    with _pt.raises(ValueError):
        win.open_document(lib)
    assert win.doc is None and not win.open_documents()
    from ctgedit.ui import browser as _browser
    from ctgedit.ui.browser.dialog import BrowserResult

    monkeypatch.setattr(
        _browser.PageBrowserDialog,
        "get_open_target",
        staticmethod(lambda *a, **k: BrowserResult("genie", lib, "fx_pump", "open", None)),
    )
    seen = []
    monkeypatch.setattr(win, "_open_element", lambda *a: seen.append(a))
    win.open_dialog()
    assert seen and seen[0][1] == "fx_pump" and win.doc is None


def test_drawing_objects_one_after_another_keeps_one_sheet(fx_page, tmp_path):
    """'Display properties on new': one sheet for the objects drawn one after another,
    shown without taking the keyboard."""
    import shutil
    from PySide6 import QtWidgets
    from ctgedit.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    src = fx_page("FxShapes")
    p = tmp_path / "Mixer.ctg"
    shutil.copyfile(src, p)
    w = MainWindow()
    try:
        w.resize(1200, 800)
        w.show()
        w.open_files([str(p)])
        app.processEvents()
        a = w.doc.create_object("rectangle", (10, 10, 40, 40))
        w._props_after_new(a)
        app.processEvents()
        b = w.doc.create_object("rectangle", (60, 10, 90, 40))
        w._props_after_new(b)
        app.processEvents()
        sheets = [
            d
            for k, d in dict.items(w._dialogs)
            if isinstance(k, tuple) and k and k[0] == "objprops" and d.isVisible()
        ]
        assert len(sheets) == 1
        assert sheets[0].testAttribute(__import__("PySide6").QtCore.Qt.WA_ShowWithoutActivating)
    finally:
        w.doc.dirty = False
        for d in list(dict.values(w._dialogs)):
            try:
                d.reject()
            except RuntimeError:
                pass
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.close()
        app.processEvents()


def test_an_element_without_its_own_colour_shows_no_page_colour(fx_project, tmp_path, monkeypatch):
    """A genie without @<BackgroundColor> has the 0xFF000000 'no colour' sentinel: the
    Properties panel shows no colour, not black. FxLib's fxgenie.fx_pump
    has none."""
    import shutil
    from ctgedit.editing import ElementDocument
    from ctgedit.ui.colour_picker import ColourButton
    from ctgedit.ui.panels.properties import PropertyPanel

    _app()
    src = os.path.join(fx_project("FxLib"), "fxgenie.ctm")
    lib = str(tmp_path / "fxgenie.ctm")
    shutil.copyfile(src, lib)
    monkeypatch.setenv("CTG_LIB_PATH", fx_project("FxLib"))
    d = ElementDocument.open_element(lib, "fx_pump")
    bg = d.page.appearance.background_colour
    assert bg is not None and (int(bg) >> 24) & 0xFF == 0xFF
    panel = PropertyPanel()
    panel.set_target(d, [])
    btns = [b for b in panel.widget().findChildren(ColourButton)]
    assert btns
    if bg is None or (int(bg) >> 24) & 0xFF == 0xFF:
        assert btns[0].pair() is None


def test_a_collapsed_ribbon_group_is_greyed_when_none_of_its_commands_can_run():
    from ctgedit.ui.ribbon import RibbonGroup

    _app()
    host = QtWidgets.QWidget()
    QtWidgets.QHBoxLayout(host)
    a1, a2 = QtGui.QAction("One", host), QtGui.QAction("Two", host)
    g = RibbonGroup("Colours", 2, host)
    host.layout().addWidget(g)
    g.add_big(a1)
    g.add_small(a2)
    a1.setEnabled(False)
    a2.setEnabled(False)
    g.set_collapsed(True)
    assert not g._collapsed_btn.isEnabled()
    a2.setEnabled(True)
    assert g._collapsed_btn.isEnabled()
    g.set_collapsed(False)
