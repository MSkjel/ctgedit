"""Update Pages guards next to other writers."""

from ctgedit.ui.main_window import MainWindow
import datetime
import os
import shutil
import struct
import subprocess
import sys
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtWidgets  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: the page the saves write, and the page Delete Page removes (both registered in FxMain)
PAGE, OTHER = "FxGenies", "FxSymbols"

_HOLD = r"""
import os, sys, time
from ctgedit.format import projectlock as PL
kind, path, ready = sys.argv[1], sys.argv[2], sys.argv[3]
with getattr(PL, kind)(path, timeout=5):
    open(ready, "w").close()
    time.sleep(120)
"""


def _holder(kind, path, tmp_path):
    """A process holding the ``kind`` lock (format/projectlock) of ``path`` until killed."""
    ready = tmp_path / ("ready-%d" % time.monotonic_ns())
    p = subprocess.Popen(
        [sys.executable, "-c", _HOLD, kind, str(path), str(ready)], env=dict(os.environ, PYTHONPATH=ROOT)
    )
    end = time.monotonic() + 60
    while not ready.exists():
        assert p.poll() is None, "the holder ended"
        assert time.monotonic() < end, "the holder did not take the lock"
        time.sleep(0.05)
    return p


def _unchanged(proj, page) -> bool:
    """Registering the page again changes no row of its project."""
    from ctgedit.io import pgregister as PR

    rep = PR.register_page(
        proj, page, os.path.join(proj, page + ".ctg"), log_change=False, mark_uncompiled=False
    )
    return (
        all(v.get("action") in ("unchanged", "absent") for v in rep["tables"].values())
        and not rep.get("pglang")
        and not rep.get("activex")
    )


def write_dbf(path, fields, rows):
    """A dBASE III table of character ``fields`` [(name, length)] holding ``rows``."""
    d = datetime.date.today()
    hlen = 32 + 32 * len(fields) + 1
    rlen = 1 + sum(ln for _n, ln in fields)
    head = struct.pack("<BBBBIHH20x", 3, d.year - 1900, d.month, d.day, len(rows), hlen, rlen)
    desc = b"".join(struct.pack("<11sc4xBB14x", n.encode("ascii"), b"C", ln, 0) for n, ln in fields)
    body = b"".join(
        b" " + b"".join(str(r.get(n, "")).encode("cp1252")[:ln].ljust(ln) for n, ln in fields) for r in rows
    )
    with open(path, "wb") as fh:
        fh.write(head + desc + b"\r" + body + b"\x1a")


def tmp_install(tmp_path):
    """User\\{Root, A, B, Include} (+ an Elsewhere project in another install): Root (no
    .ctg) includes A, B, Include and Elsewhere; A includes B. A has 3 registered pages, B
    one page (no pages.DBF: every .ctg counts)."""

    def pages_dbf(folder, names):
        write_dbf(
            os.path.join(folder, "pages.DBF"),
            [("NAME", 64), ("TITLE", 64)],
            [{"NAME": n, "TITLE": n} for n in names],
        )

    def include_dbf(folder, names):
        write_dbf(
            os.path.join(folder, "include.dbf"), [("NAME", 64), ("COMMENT", 48)], [{"NAME": n} for n in names]
        )

    user = tmp_path / "Install" / "User"
    for n in ("Root", "A", "B", "Include"):
        (user / n).mkdir(parents=True)
    write_dbf(
        str(user / "MASTER.DBF"),
        [("NAME", 64), ("PATH", 240)],
        [{"NAME": n, "PATH": str(user / n)} for n in ("Root", "A", "B", "Include")],
    )
    include_dbf(str(user / "Root"), ["A", "B", "Include", "Elsewhere"])
    include_dbf(str(user / "A"), ["B"])
    pages_dbf(str(user / "A"), ["a1", "a2", "a3"])
    for n in ("a1", "a2", "a3"):
        (user / "A" / (n + ".ctg")).write_bytes(b"page")
    (user / "B" / "b1.ctg").write_bytes(b"page")
    other = tmp_path / "Other" / "User" / "Elsewhere"
    other.mkdir(parents=True)
    (other / "e1.ctg").write_bytes(b"page")
    pages_dbf(str(user / "Root"), [])
    return str(user)


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def proj(fx_install):
    """FxMain of the fake install's copy."""
    return fx_install.project("FxMain")


@pytest.fixture
def short_waits(monkeypatch):
    from ctgedit.format import projectlock as PL

    monkeypatch.setitem(PL._WAITS, "project", 0.3)
    monkeypatch.setitem(PL._WAITS, "file", 0.3)


def _plain_project(tmp_path):
    d = tmp_path / "P"
    d.mkdir()
    page = d / "a.ctg"
    page.write_bytes(b"page")
    return str(d), str(page)


# ------------------------------------------------------------------------------- backups
def test_a_busy_project_stops_the_backup_with_nothing_backed_up(tmp_path, short_waits):
    from ctgedit.format import projectlock as PL
    from ctgedit.io import updatepages as UP

    pdir, page = _plain_project(tmp_path)
    dest = tmp_path / "backup"
    p = _holder("project", pdir, tmp_path)
    try:
        with pytest.raises(PL.ProjectBusyError):
            UP._backup_page(page, str(dest))
    finally:
        p.kill()
        p.wait(30)
    assert not dest.exists() or not any(dest.rglob("*"))


def test_a_cancelled_wait_stops_the_backup_with_nothing_backed_up(tmp_path, monkeypatch):
    from ctgedit.format import projectlock as PL
    from ctgedit.io import updatepages as UP

    pdir, page = _plain_project(tmp_path)
    dest = tmp_path / "backup"
    monkeypatch.setattr(PL, "_CANCEL", [lambda: True])  # (Cancel of Update Pages)
    p = _holder("project", pdir, tmp_path)
    try:
        with pytest.raises(PL.LockCancelled):
            UP._backup_page(page, str(dest))
    finally:
        p.kill()
        p.wait(30)
    assert not dest.exists() or not any(dest.rglob("*"))


def test_other_row_errors_keep_the_page_files_as_the_backup(tmp_path, monkeypatch):
    import json
    from ctgedit.infra import backups
    from ctgedit.io import pgregister
    from ctgedit.io import updatepages as UP

    pdir, page = _plain_project(tmp_path)
    dest = tmp_path / "backup"

    def broken(*_a, **_k):
        raise ValueError("a broken table")

    monkeypatch.setattr(pgregister, "export_page_rows", broken)
    UP._backup_page(page, str(dest))
    rows = json.loads((dest / "P" / "a.pgrows.json").read_text())
    assert "_error" in rows["tables"] and (dest / "P" / "a.ctg").exists()
    assert [e["page"] for e in backups.read_manifest(str(dest))["entries"]] == ["a"]


def test_a_restore_of_a_backup_without_rows_writes_them_from_the_page(proj, tmp_path, monkeypatch):
    from ctgedit.io import pendingrows
    from ctgedit.io import pgregister as PR
    from ctgedit.io import updatepages as UP

    page = os.path.join(proj, PAGE + ".ctg")
    real = PR.export_page_rows

    def broken(*_a, **_k):
        raise ValueError("a broken table")

    monkeypatch.setattr(PR, "export_page_rows", broken)
    folder = str(tmp_path / "backup")
    UP._backup_page(page, folder)
    monkeypatch.setattr(PR, "export_page_rows", real)
    PR.delete_page_rows(proj, PAGE)  # (the update's rows, say)
    assert not _unchanged(proj, PAGE)
    rep = UP.restore_backup(folder)
    assert rep.restored == [PAGE] and not rep.failed
    assert _unchanged(proj, PAGE) and pendingrows.marked(page) is None
    # when the rows cannot be written either, the page is left pending (and said so at
    # the next start)
    PR.delete_page_rows(proj, PAGE)

    def refused(*_a, **_k):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(PR, "register_page", refused)
    rep = UP.restore_backup(folder)
    assert rep.restored == [PAGE] and "_error" in rep.rows[PAGE]
    assert pendingrows.marked(page) is not None
    pendingrows.clear(page)


# ------------------------------------------------------------------------- page saves
def test_a_failed_page_write_keeps_an_earlier_saves_marker(proj, monkeypatch):
    from ctgedit.doc import history
    from ctgedit.editing import EditableDocument
    from ctgedit.io import pendingrows

    page = os.path.join(proj, PAGE + ".ctg")
    doc = EditableDocument.open(page)

    def full(*_a, **_k):
        raise OSError(28, "No space left on the disk")

    monkeypatch.setattr(history, "write_atomic", full)
    with pytest.raises(OSError):
        doc.save()
    assert pendingrows.marked(page) is None  # (this save's own marker goes)
    pendingrows.mark(page)  # (an earlier save's rows behind)
    with pytest.raises(OSError):
        doc.save()
    assert pendingrows.marked(page) is not None  # (still behind the page file)
    pendingrows.clear(page)


def test_a_busy_database_tells_a_new_pages_save_to_save_again(proj, tmp_path, short_waits):
    from ctgedit.doc.history import BUSY_NEW_PAGE_WARNING, BUSY_WARNING
    from ctgedit.editing import EditableDocument

    fresh = os.path.join(proj, "Fresh.ctg")  # (no pages.DBF row)
    shutil.copy(os.path.join(proj, PAGE + ".ctg"), fresh)
    p = _holder("project", proj, tmp_path)
    try:
        doc = EditableDocument.open(fresh)
        doc.save()
        assert BUSY_NEW_PAGE_WARNING in doc.save_warning
        assert BUSY_WARNING not in doc.save_warning
        doc = EditableDocument.open(os.path.join(proj, PAGE + ".ctg"))
        doc.save()
        assert BUSY_WARNING in doc.save_warning
        assert BUSY_NEW_PAGE_WARNING not in doc.save_warning
    finally:
        p.kill()
        p.wait(30)


def test_keyboard_commands_that_cannot_be_read_cannot_be_changed(proj, app, monkeypatch):
    from ctgedit.editing import EditableDocument
    from ctgedit.format import projectlock as PL
    from ctgedit.io import pgregister
    from ctgedit.ui.dialog.page_props import PageKeyboardTab

    doc = EditableDocument.open(os.path.join(proj, PAGE + ".ctg"))
    assert doc.page_keyboard_read()[1] == ""

    def busy(*_a, **_k):
        raise PL.ProjectBusyError(proj)

    monkeypatch.setattr(pgregister, "page_keyboard_rows", busy)
    rows, why = doc.page_keyboard_read()
    assert rows == [] and "busy" in why and doc.page_keyboard_commands() == []
    with pytest.raises(ValueError, match="cannot be read now"):
        doc.set_page_keyboard_commands([{"KEY_SEQ": "F2", "CMD": "Beep()"}])
    assert doc.staged_page_state() == {}
    tab = PageKeyboardTab(doc, enabled=True)
    try:
        assert not tab._enabled and not tab.btn_add.isEnabled()
        assert not tab.read_note.isHidden() and "busy" in tab.read_note.text()
        assert not tab.rows_changed()
    finally:
        tab.deleteLater()


# ----------------------------------------------------------------- rows pending markers
def test_delete_page_clears_the_pages_marker(proj):
    from ctgedit.io import pendingrows
    from ctgedit.io.project import delete_page

    page = os.path.join(proj, OTHER + ".ctg")
    pendingrows.mark(page)
    delete_page(proj, OTHER, files=False)  # (remove from the project only)
    assert pendingrows.marked(page) is None


def test_the_start_up_list_drops_gone_and_unregistered_pages(proj, tmp_path):
    from ctgedit.io import pendingrows

    gone = str(tmp_path / "Nowhere" / "Gone.ctg")
    fresh = os.path.join(proj, "Fresh.ctg")
    shutil.copy(os.path.join(proj, PAGE + ".ctg"), fresh)
    page = os.path.join(proj, PAGE + ".ctg")
    for p in (gone, fresh, page):
        pendingrows.mark(p)
    assert [m["page"] for m in pendingrows.listing()] == [os.path.abspath(page)]
    assert pendingrows.marked(gone) is None and pendingrows.marked(fresh) is None
    pendingrows.clear(page)


# ---------------------------------------------------------------------- helper processes
class _Proc:
    def poll(self):
        return None


def _fake_start(calls, fail_at, tmp_path):
    def start(self):
        calls.append(self)
        if len(calls) == fail_at:
            return False
        self._proc = _Proc()
        self._files = {"cancel_file": str(tmp_path / ("cancel-%d" % len(calls)))}
        return True

    return start


def test_a_helper_that_cannot_start_leaves_the_started_ones_to_run(app, tmp_path, monkeypatch):
    from ctgedit.ui.jobs import helper_job as HJ
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    calls = []
    monkeypatch.setattr(HJ.HelperJob, "start", _fake_start(calls, 3, tmp_path))
    d = tmp_path / "P"
    d.mkdir()
    for k in range(24):
        (d / ("p%02d.ctg" % k)).write_bytes(b"page")
    run = UpdatePagesRun(None, [str(d)], {"backup": None}, parts=3)
    try:
        assert run.start()
        assert run.parts == 2 and UpdatePagesRun.active is run
        assert os.path.isdir(os.path.join(run._folder, "claims"))  # (kept for them)
        run.stop()
        assert (tmp_path / "cancel-1").exists() and (tmp_path / "cancel-2").exists()
    finally:
        UpdatePagesRun.active = None
        shutil.rmtree(run._folder, ignore_errors=True)


def test_the_first_helper_failing_ends_the_run_at_once(app, tmp_path, monkeypatch):
    from ctgedit.ui.jobs import helper_job as HJ
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    calls = []
    monkeypatch.setattr(HJ.HelperJob, "start", _fake_start(calls, 1, tmp_path))
    d = tmp_path / "P"
    d.mkdir()
    for k in range(24):
        (d / ("p%02d.ctg" % k)).write_bytes(b"page")
    run = UpdatePagesRun(None, [str(d)], {"backup": None}, parts=3)
    assert not run.start()
    assert UpdatePagesRun.active is None and not os.path.exists(run._folder)


def test_find_still_stops_its_parts_when_one_cannot_start(app, tmp_path, monkeypatch):
    from ctgedit.ui.jobs import helper_job as HJ

    calls = []
    monkeypatch.setattr(HJ.HelperJob, "start", _fake_start(calls, 3, tmp_path))
    jobs = HJ.HelperJobs(None, lambda r, a: [], {}, str(tmp_path), 3, 30, lambda a: {})
    assert not jobs.start()
    assert (tmp_path / "cancel-1").exists() and (tmp_path / "cancel-2").exists()


def test_a_chosen_count_is_capped_by_the_pages(app, tmp_path, monkeypatch):
    from ctgedit.ui.jobs import helper_job as HJ
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    calls = []
    monkeypatch.setattr(HJ.HelperJob, "start", _fake_start(calls, 0, tmp_path))
    d = tmp_path / "P"
    d.mkdir()
    for k in range(20):
        (d / ("p%02d.ctg" % k)).write_bytes(b"page")
    run = UpdatePagesRun(None, [str(d)], {"backup": None}, parts=16)
    try:
        assert run.start()
        assert run.parts == 2 and len(calls) == 2  # (8 pages a helper at least)
    finally:
        UpdatePagesRun.active = None
        shutil.rmtree(run._folder, ignore_errors=True)


# ------------------------------------------------------------------------------ the window
def _dlg(user, start=None):
    from ctgedit.ui.dialog.dialogs import UpdatePagesDialog

    return UpdatePagesDialog(start, None, projects=[(n, os.path.join(user, n)) for n in ("Root", "A", "B")])


def test_an_include_start_folder_is_never_a_root(app, tmp_path, monkeypatch):
    from ctgedit.io import catalog
    from ctgedit.ui.dialog.dialogs import UpdatePagesDialog

    user = tmp_install(tmp_path)
    (tmp_path / "Install" / "User" / "Include" / "x.ctg").write_bytes(b"page")
    dlg = _dlg(user, os.path.join(user, "Include"))
    try:
        names = [dlg._root.itemText(k) for k in range(dlg._root.count())]
        assert names == ["A", "B", "Root"] and dlg.root_name() != "Include"
        assert os.path.join(user, "Include") not in dlg.projects()
    finally:
        dlg.deleteLater()
    cat = catalog.ProjectCatalog(user, other_installs=False)
    monkeypatch.setattr(catalog, "catalog_for", lambda *a, **k: cat)
    dlg = UpdatePagesDialog(os.path.join(user, "Include"), None)
    try:
        names = [dlg._root.itemText(k) for k in range(dlg._root.count())]
        assert "Include" not in names and dlg.root_name() != "Include"
    finally:
        dlg.deleteLater()
    loose = tmp_path / "Loose"
    loose.mkdir()
    dlg = UpdatePagesDialog(str(loose), None)  # (a folder of no install: a root)
    try:
        assert dlg.root_name() == "Loose"
    finally:
        dlg.deleteLater()


def test_a_project_with_no_pages_is_drawn_disabled(app, tmp_path):
    user = tmp_install(tmp_path)
    dlg = _dlg(user, os.path.join(user, "Root"))
    try:
        tree = dlg._tree
        delegate = tree.itemDelegate()
        root = tree.topLevelItem(0)
        child = root.child(0)
        assert root.text(0) == "Root" and child.text(0) == "A"

        def state(item, col=0):
            opt = QtWidgets.QStyleOptionViewItem()
            opt.state = QtWidgets.QStyle.State_Enabled  # (as the view hands it over)
            idx = tree.indexFromItem(item, col)
            delegate.initStyleOption(opt, idx)
            return opt

        o = state(root)
        assert o.features & QtWidgets.QStyleOptionViewItem.HasCheckIndicator
        assert not o.state & QtWidgets.QStyle.State_Enabled
        assert not state(root, 1).state & QtWidgets.QStyle.State_Enabled
        assert state(child).state & QtWidgets.QStyle.State_Enabled
        # (the item stays enabled: the projects it includes are not disabled with it)
        assert root.flags() & QtCore.Qt.ItemIsEnabled and child.flags() & QtCore.Qt.ItemIsEnabled
    finally:
        dlg.deleteLater()


def test_the_result_box_counts_in_words(app):
    rep = types.SimpleNamespace(
        pages=1,
        projects=["A"],
        stale=1,
        updated=["a"],
        stopped=True,
        symbols=1,
        templates=1,
        supergenies=1,
        instances=1,
        composites=1,
        composites_skipped=1,
        open_in_builder=[],
        db_in_use=[],
        changed_meanwhile=[],
        rows_pending=[],
        rows_written=[],
        not_checked=1,
        unregistered=["x"],
        warnings=[],
        failed=[],
        errors=[],
    )
    lines = MainWindow._update_pages_summary(rep, None, (1, 1, 1, 1))
    text = "\n".join(lines)
    assert "(s)" not in text
    assert lines[0] == "1 page checked in 1 project, 1 out of date, 1 updated."
    assert "1 placed symbol updated from its library." in text
    assert "1 composite genie left as it was: its template" in text
    assert "1 page file not registered in its project was left alone." in text
    assert "a helper process ended: 1 page." in text
    rep.pages, rep.projects, rep.not_checked = 3, ["A", "B"], 2
    rep.unregistered = ["x", "y"]
    lines = MainWindow._update_pages_summary(rep, None, (2, 0, 0, 0))
    assert lines[0] == "3 pages checked in 2 projects, 1 out of date, 1 updated."
    assert "2 page files not registered in their project were left alone." in "\n".join(lines)


# ---------------------------------------------------------------------------- the markers
def test_a_marker_that_cannot_be_written_never_stops_a_save(proj, monkeypatch):
    from ctgedit.editing import EditableDocument
    from ctgedit.io import pendingrows

    page = os.path.join(proj, PAGE + ".ctg")
    doc = EditableDocument.open(page)

    def refused(*_a, **_k):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(pendingrows, "mark", refused)
    assert doc.save() == page and not doc.dirty
    assert pendingrows.marked(page) is None


def test_a_marker_that_cannot_be_removed_stays_without_an_error(tmp_path, monkeypatch):
    from ctgedit.io import pendingrows

    page = str(tmp_path / "a.ctg")
    pendingrows.mark(page)

    def held(_p):
        raise PermissionError(13, "Access is denied")

    with monkeypatch.context() as m:
        m.setattr(pendingrows.os, "remove", held)
        pendingrows.clear(page)  # (no error: logged, kept)
    assert pendingrows.marked(page) is not None
    pendingrows.clear(page)
    assert pendingrows.marked(page) is None


def test_a_stale_fast_display_left_does_not_keep_the_rows_marker(proj, monkeypatch):
    """The marker is about the rows: a .ctF that could not be removed is the save's warning."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import pendingrows

    page = os.path.join(proj, PAGE + ".ctg")
    doc = EditableDocument.open(page)
    real = type(doc)._update_runtime_files

    def ctf_left(self, *a, **k):
        out = real(self, *a, **k)
        self._ctf_ok = False
        return out

    monkeypatch.setattr(type(doc), "_update_runtime_files", ctf_left)
    doc.save()
    assert pendingrows.marked(page) is None and not doc.rows_pending
