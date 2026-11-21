"""The History panel's version tabs and settings."""

import os
import shutil
import time
import weakref

import pytest
from PySide6 import QtWidgets
from qtkit import app as _app


def _pump(n=4):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture
def hist(tmp_path, monkeypatch):
    from ctgedit.infra import history as H

    monkeypatch.setenv("CTG_DATA_DIR", str(tmp_path / "data"))
    H._TOTAL.clear()
    H.configure(10, 200)
    yield H
    H.configure(0, 200)
    H._TOTAL.clear()


@pytest.fixture
def win(fx_install, hist, monkeypatch):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_install.page("FxShapes"))
    w.resize(1300, 900)
    w.show()
    _pump()
    if w.doc.dirty:
        w.save()
    yield w
    for x in list(MainWindow._instances):
        for e in getattr(x, "_docs", []):
            e["doc"].dirty = False
        MainWindow._instances.remove(x)
        x.hide()


def _bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _a_version(win, hist):
    doc = win.doc
    r = doc.page.roots()[0]
    doc.move_objects([r], 9, 0)
    win.save()
    return doc, hist.list_versions(doc.path)[0]


# ------------------------------------------------------------------ [0] the version tab
def test_ctrl_s_and_save_all_on_a_version_tab_never_write_the_page(win, hist, monkeypatch):
    doc, v = _a_version(win, hist)
    page = _bytes(doc.path)
    win.open_version_read_only(v)
    vdoc = win.doc
    vdoc.move_objects([vdoc.page.roots()[0]], 30, 0)
    vdoc.dirty = True
    asked = []
    monkeypatch.setattr(type(win), "save_as", lambda self: (asked.append(1), False)[1])
    assert win.save() is False and asked == [1]
    assert vdoc.read_only and vdoc.ui.version_view is v
    assert _bytes(doc.path) == page
    # Save All leaves it (says so in its summary)
    boxes = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: (boxes.append(a), 0)[1])
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: (boxes.append(a), 0)[1])
    )
    from ctgedit.ui import messages

    monkeypatch.setattr(messages, "list_box", lambda *a, **k: boxes.append(a))
    win.save_all()
    assert _bytes(doc.path) == page
    assert vdoc.dirty
    # the lower writers refuse it too (the close list, any other in-place save)
    errs = []
    win.set_document(vdoc)
    assert win._save_to(errors=errs) is False and "earlier version" in errs[0]
    assert _bytes(doc.path) == page


def test_autosave_leaves_a_version_tab(win, hist):
    from ctgedit.ui.recovery import AutosaveController

    doc, v = _a_version(win, hist)
    win.open_version_read_only(v)
    vdoc = win.doc
    vdoc.move_objects([vdoc.page.roots()[0]], 30, 0)
    vdoc.dirty = True
    ctl = AutosaveController(QtWidgets.QApplication.instance())
    try:
        assert vdoc in ctl.documents()
        ctl.tick(force=True)
        assert vdoc.ui.autosave_key is None
    finally:
        ctl.deleteLater()


def test_save_as_makes_a_version_a_page_of_its_own(win, hist, fx_project, monkeypatch):
    doc, v = _a_version(win, hist)
    win.open_version_read_only(v)
    vdoc = win.doc
    target = os.path.join(fx_project("FxMain"), "FxShapesOld.ctg")
    from ctgedit.ui.browser import PageBrowserDialog

    monkeypatch.setattr(PageBrowserDialog, "get_save_path", staticmethod(lambda *a, **k: target))
    assert win.save() is True
    assert os.path.isfile(target) and vdoc.ui.version_view is None
    assert not vdoc.read_only and win._tab_title(vdoc) == "FxShapesOld"


def test_an_element_version_tab_is_named_and_not_the_elements_own(win, hist, fx_install, tmp_path):
    """A copy of FxLib's fxsym.ctl in a folder of its own (no page uses it there): its
    first element, fx_box."""
    from ctgedit.format.element import LibraryFile

    (tmp_path / "FxCopy").mkdir()
    lib = str(tmp_path / "FxCopy" / "fxsym.ctl")
    shutil.copyfile(fx_install.file("FxLib", "fxsym.ctl"), lib)
    name = LibraryFile(_bytes(lib)).element_names()[0]
    assert name == "fx_box"
    win._open_element(lib, name)
    _pump()
    edoc = win.doc
    assert getattr(edoc, "element_name", "").lower() == name.lower()
    hx, hy = edoc.hotspot
    if edoc.can_set_hotspot:
        edoc.set_hotspot(hx + 7, hy + 3)
    else:
        edoc.move_objects([edoc.page.roots()[0]], 5, 0)
    win.save()
    v = hist.list_versions(lib, edoc.element_name)[0]
    stored = _bytes(lib)
    win.open_version_read_only(v)
    vdoc = win.doc
    assert vdoc is not edoc and "(" in win._tab_title(vdoc)
    assert not any(win._is_element_tab(e, lib, name) for e in win._docs if e["doc"] is vdoc)
    errs = []
    assert win._save_to(errors=errs) is False and _bytes(lib) == stored
    # [3] the version keeps its own hot spot (Restore does not keep today's)
    if edoc.can_set_hotspot:
        assert tuple(vdoc.hotspot) == (hx, hy)


# ------------------------------------------------------------------ [1] the Settings count
def test_stepping_the_count_down_and_up_removes_nothing(tmp_path, hist):
    from ctgedit.ui.dialog import history_versions as HV
    from ctgedit.ui.prefs.settings import app_settings

    H = hist
    pages = [str(tmp_path / ("p%d.ctg" % k)) for k in range(2)]
    t = 1_800_000_000.0
    for p in pages:
        with open(p, "wb") as fh:
            fh.write(b"x")
        for k in range(10):
            H.keep_version(p, b"%s v%d" % (p.encode(), k) * 50, now=t + k)
    for n in (2, 6):
        app_settings().setValue(HV.VERSIONS_KEY, n)
        HV._apply_settings(None)
    assert [len(H.list_versions(p)) for p in pages] == [10, 10]
    # the next save of a page keeps its newest 6
    H.keep_version(pages[0], b"newest" * 50, now=t + 20)
    assert [len(H.list_versions(p)) for p in pages] == [6, 10]


# ------------------------------------------------------------------ [2] whose history
def test_the_smoke_test_keeps_its_files_out_of_the_users_folder():
    import inspect
    from ctgedit import selftest

    assert 'os.environ.setdefault("CTG_DATA_DIR"' in inspect.getsource(selftest.run)


def test_a_window_turns_versions_on_for_its_test_only(win, hist):
    assert hist.enabled()


def test_the_next_test_starts_with_versions_off():
    # (tests/conftest.py's _history_versions_off: the window of the test before turned
    # them on for the whole process)
    from ctgedit.infra import history as H

    assert not H.enabled()


# ------------------------------------------------------------------ [4] the saved pages
def test_two_pages_keep_their_saved_pages_read(win, hist, fx_page):
    from ctgedit.ui.panels import changes_dock as CD

    a = win.doc
    win.open_document(fx_page("FxGenies"))
    b = win.doc
    sa = CD._read_saved(a, None)
    sb = CD._read_saved(b, None)
    assert CD._read_saved(a, None) is sa and CD._read_saved(b, None) is sb
    assert set(sa.sigs) == set(sa.roots)
    # a newer version of a page's file takes its old entry's place
    a.move_objects([a.page.roots()[0]], 4, 0)
    win.set_document(a)
    win.save()
    sa2 = CD._read_saved(a, None)
    assert sa2 is not sa and sum(1 for k in CD._SAVED_MEMO if k[0] == sa.key[0]) == 1


# ------------------------------------------------------------------ [5] the prune of a save
def test_a_save_prunes_its_own_folder_and_scans_only_over_the_cap(tmp_path, hist, monkeypatch):
    H = hist
    H.configure(3, 200)
    calls = []
    real = H.prune
    monkeypatch.setattr(H, "prune", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    page = str(tmp_path / "p.ctg")
    with open(page, "wb") as fh:
        fh.write(b"x")
    t = 1_800_000_000.0
    for k in range(6):
        H.keep_version(page, b"v%d" % k * 100, now=t + k)
    assert len(H.list_versions(page)) == 3 and calls == []
    assert H._total() == sum(v.size for v in H.list_versions(page))
    H.configure(3, 1)  # (1 MB)
    H.keep_version(page, b"z" * (2 * 1024 * 1024), now=t + 10)
    assert calls == [1]
    assert [v.size for v in H.list_versions(page)] == [2 * 1024 * 1024]


# ------------------------------------------------------------------ [6] the change gone to
def test_the_change_gone_to_holds_its_page_weakly(win, hist):
    doc = win.doc
    doc.move_objects([doc.page.roots()[0]], 6, 0)
    win.next_change()
    focus = win.__dict__.get("_change_focus")
    assert isinstance(focus[0], weakref.ref) and focus[0]() is doc
    assert win._focus() is not None


# ------------------------------------------------------------------ [7] an undone page edit
def test_an_undone_page_properties_edit_is_no_change(win, hist):
    """FxShapes is registered in FxMain's pages.DBF (with pgkey.DBF beside it)."""
    from ctgedit.ui.panels import changes_dock as CD

    doc = win.doc
    rec = doc.page_record() or {}
    assert rec
    old = rec.get("TITLE", "")
    assert doc.set_page_record({"TITLE": (old or "FxShapes") + "X"})
    assert CD._staged_differs(doc)
    win.undo()
    assert not CD._staged_differs(doc)
    assert all(c.kind != "page" for c in CD.changes_since_save(doc).changes)


# ------------------------------------------------------------------ [8] a removed version
def test_a_compared_version_that_was_removed_goes_back_to_the_save(win, hist):
    from ctgedit.ui.panels import changes_dock as CD

    doc, v = _a_version(win, hist)
    win.compare_with_version(v)
    assert doc.ui.changes_base is v
    hist.remove_version(v)
    assert CD.changes_since_save(doc, base=v).error == "That version is no longer kept."
    cs = win._current_changes()
    assert doc.ui.changes_base is None and cs.base == "the save"
    assert "no longer kept" in win.statusBar().currentMessage()


# ------------------------------------------------------------------ [9] the chip
def test_the_chip_is_short_and_its_tip_has_each_kind():
    from ctgedit.ui.panels.changes_dock import Change, ChangeSet

    cs = ChangeSet(
        changes=[
            Change("new", 1, None, "a"),
            Change("changed", 2, None, "b"),
            Change("removed", None, None, "c"),
            Change("page", None, None, "Page"),
        ]
    )
    assert cs.chip_text() == "4 changes"
    assert cs.chip_tip() == "1 new, 1 changed, 1 removed, page changed"
    assert ChangeSet(changes=[Change("changed", 2, None, "b")]).chip_text() == "1 changed"
    assert ChangeSet().chip_text() == "No changes"
    time.sleep(0)
