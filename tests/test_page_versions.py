"""Page history."""

import os
import time

import pytest
from PySide6 import QtWidgets
from qtkit import app as _app

#: the page the tests edit (its name shows in the titles)
PAGE = "FxThumb"


@pytest.fixture
def hist(tmp_path, monkeypatch):
    from ctgedit.infra import history as H

    monkeypatch.setenv("CTG_DATA_DIR", str(tmp_path / "data"))
    H.configure(10, 200)
    yield H
    H.configure(0, 200)


def test_keep_list_dedupe_and_prune(tmp_path, hist):
    H = hist
    page = str(tmp_path / "p.ctg")
    other = str(tmp_path / "q.ctg")
    for p in (page, other):
        with open(p, "wb") as fh:
            fh.write(b"x")
    t = 1_800_000_000.0
    vs = [H.keep_version(page, b"v%d" % k * 100, now=t + k) for k in range(4)]
    assert all(v is not None for v in vs)
    assert H.keep_version(page, b"v3" * 100, now=t + 9) is None  # (the same bytes)
    got = H.list_versions(page)
    assert [H.read_bytes(v) for v in got] == [b"v%d" % k * 100 for k in (3, 2, 1, 0)]
    assert got[0].size == 200 and got[0].replaced_at == t + 3
    # each page has its own folder; an element of a library has its own too
    H.keep_version(other, b"other", now=t)
    H.keep_version(other, b"element", element="E1", now=t)
    assert len(H.list_versions(other)) == 1 and len(H.list_versions(other, "E1")) == 1
    # the newest N of each page stay
    H.prune(versions=2)
    assert [H.read_bytes(v) for v in H.list_versions(page)] == [b"v3" * 100, b"v2" * 100]
    total, count = H.disk_use()
    assert count == 4 and total > 0
    # a size cap: the oldest go first, the copy just made stays
    newest = H.list_versions(page)[0]
    H.prune(versions=10, mb=0, keep_file=newest.file)
    assert [v.file for v in H.list_versions(page)] == [newest.file]
    assert H.list_versions(other) == []
    H.remove_version(newest)
    assert H.list_versions(page) == []
    H.keep_version(page, b"again", now=t)
    assert H.clear() == 1 and H.disk_use() == (0, 0)


def test_off_keeps_nothing(tmp_path, hist):
    H = hist
    H.configure(0, 200)
    page = str(tmp_path / "p.ctg")
    assert H.keep_version(page, b"data") is None and not H.enabled()
    assert H.disk_use() == (0, 0)


def test_two_saves_keep_two_versions_of_the_old_bytes(fx_install, hist):
    from ctgedit.editing import EditableDocument

    H = hist
    dst = fx_install.page(PAGE)
    with open(dst, "rb") as fh:
        original = fh.read()
    d = EditableDocument.open(str(dst))
    count = len(d.objects)
    r = d.page.roots()[0]
    d.move_objects([r], 3, 0)
    d.save()
    with open(dst, "rb") as fh:
        first_save = fh.read()
    d.move_objects([r], 3, 0)
    d.save()
    got = H.list_versions(str(dst))
    assert len(got) == 2
    assert H.read_bytes(got[-1]) == original and H.read_bytes(got[0]) == first_save
    assert got[-1].objects == count


@pytest.fixture
def win(fx_install, hist):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_install.page(PAGE))
    w.resize(1300, 900)
    w.show()
    QtWidgets.QApplication.processEvents()
    if w.doc.dirty:
        w.save()
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()


def _payloads(doc):
    from ctgedit.format import writer

    return [writer.object_payload(po) for po in doc.parsed.page.objects]


def test_restore_puts_the_first_version_back_as_unsaved_changes(win, hist):
    H = hist
    doc = win.doc
    path = doc.path
    with open(path, "rb") as fh:
        before = fh.read()
    from ctgedit.editing import EditableDocument

    first = _payloads(EditableDocument.open(path))
    r = doc.page.roots()[0]
    doc.move_objects([r], 11, 0)
    win.save()
    doc.move_objects([r], 11, 0)
    win.save()
    vs = H.list_versions(path)
    assert len(vs) >= 2
    oldest = vs[-1]
    assert H.read_bytes(oldest) == before
    with open(path, "rb") as fh:
        on_disk = fh.read()
    assert win.restore_version(oldest, ask=False)
    new = win.doc
    assert new is not doc and new.dirty and new.has_unstepped_changes
    assert new.path == path and not new._undo
    assert _payloads(new) == first
    with open(path, "rb") as fh:
        assert fh.read() == on_disk  # nothing written yet
    assert win.windowTitle().startswith(PAGE + " •")
    # the save writes it (through the normal save) and keeps the page before it
    n = len(H.list_versions(path))
    win.save()
    assert not win.doc.dirty and len(H.list_versions(path)) == n + 1
    assert _payloads(EditableDocument.open(path)) == first


def test_open_read_only_and_compare(win, hist):
    from ctgedit.ui.panels import changes_dock as CD

    H = hist
    doc = win.doc
    r = doc.page.roots()[0]
    doc.move_objects([r], 9, 0)
    win.save()
    v = H.list_versions(doc.path)[0]
    tabs = len(win._docs)
    win.open_version_read_only(v)
    vdoc = win.doc
    assert len(win._docs) == tabs + 1 and vdoc is not doc
    assert vdoc.read_only and vdoc.ui.version_view is v and not vdoc.dirty
    assert "(version of %s)" % v.when in win.windowTitle()
    assert win._tab_title(vdoc).startswith(PAGE + " (")
    # opening the page goes to its own tab, not the version's
    win.open_document(doc.path)
    assert win.doc is doc
    # Compare: the page against that version (the move is the difference)
    win.compare_with_version(v)
    cs = CD.changes_since_save(doc, base=v)
    assert [c.kind for c in cs.changes] == ["changed"] and cs.base.startswith("the version")
    assert win.changes_dock.back_btn.isVisible()
    assert "(version)" in win._changes_chip.text()
    win.compare_with_save()
    assert doc.ui.changes_base is None
    assert CD.changes_since_save(doc).changes == []


def test_a_version_tab_is_display_checked_against_its_own_bytes(win, hist):
    """Check Page Display on an earlier version's tab checks against the version's own bytes,
    not the page's file on disk; the check after an open leaves it out."""
    H = hist
    doc = win.doc
    r = doc.page.roots()[0]
    doc.move_objects([r], 9, 0)
    win.save()
    win.open_version_read_only(H.list_versions(doc.path)[0])
    vdoc = win.doc
    assert vdoc.ui.version_view is not None
    out = win._check_page_display(in_process=True, show=False)
    assert out is not None and out.identical, out.differences
    assert not win._display_check_wanted(vdoc)


def test_the_page_history_window(win, hist):
    H = hist
    doc = win.doc
    r = doc.page.roots()[0]
    doc.move_objects([r], 2, 0)
    win.save()
    time.sleep(0.01)
    doc.move_objects([r], 2, 0)
    win.save()
    win.act_page_history.trigger()
    QtWidgets.QApplication.processEvents()
    dlg = win._modeless_for("pagehistory")
    assert dlg is not None and dlg.isVisible() and not dlg.isModal()
    assert dlg.windowTitle() == "Page History - " + PAGE
    assert dlg.tree.topLevelItemCount() == 2
    assert dlg.tree.topLevelItem(0).text(0) == H.list_versions(doc.path)[0].when
    assert not dlg.tree.topLevelItem(0).icon(0).isNull()  # the page's thumbnail
    assert dlg.btn_restore.isEnabled() and dlg.btn_compare.isEnabled()
    from ctgedit.ui.help import DIALOG_TOPICS

    assert "PageHistoryDialog" in DIALOG_TOPICS
    # asking again brings the same window
    win.page_history()
    assert win._modeless_for("pagehistory") is dlg
    # Clear here removes this page's versions only (every page's: Settings > Editing > Safety)
    other = os.path.join(os.path.dirname(doc.path), "OtherPage.ctg")
    H.keep_version(other, b"other page", now=time.time())
    from ctgedit.ui import messages

    asked = []
    orig = messages.confirm
    messages.confirm = lambda parent, title, text, action, **k: asked.append(text) or True
    try:
        assert dlg.btn_clear.isEnabled() and "This Page" in dlg.btn_clear.text()
        dlg.btn_clear.click()
    finally:
        messages.confirm = orig
    assert asked and PAGE in asked[0] and "Other pages keep theirs" in asked[0]
    assert H.list_versions(doc.path) == [] and len(H.list_versions(other)) == 1
    assert dlg.tree.topLevelItemCount() == 0 and not dlg.btn_clear.isEnabled()
    assert dlg.use_label.text().startswith("All pages: ")
    dlg.close()


def test_the_settings_switch_versions_off(win, hist, tmp_path):
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.dialog import history_versions as HV

    H = hist
    app_settings().setValue("opt/history_versions", 0)
    HV.configure_from_settings()
    try:
        doc = win.doc
        doc.move_objects([doc.page.roots()[0]], 1, 0)
        win.save()
        assert H.list_versions(doc.path) == []
    finally:
        app_settings().remove("opt/history_versions")
        HV.configure_from_settings()
    assert H.enabled()
