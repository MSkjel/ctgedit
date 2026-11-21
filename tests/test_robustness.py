"""Robustness infrastructure."""

import glob
import json
import contextlib
import os
import shutil
import stat
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from qtkit import app as _app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: the registered page with touch rows (FxMain's), the library's element deleted before a
#: pack, its first element and its last block (2,192 bytes)
REG_PAGE = "FxButtons"
DEAD = "fx_flash2"
FIRST = "fx_bitmap2"
LAST = "fx_dspsym"


@pytest.fixture()
def sample(fx_install):
    """FxMain's FxShapes (the sample page; copy it before an edit)."""
    return fx_install.page("FxShapes")


@pytest.fixture()
def smoke(fx_install):
    """FxLib's fxmore.ctl (the symbol library; copy it before a write)."""
    return fx_install.file("FxLib", "fxmore.ctl")


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)


@pytest.fixture()
def pages(tmp_path, sample):
    made = []

    def make(name, folder="Proj"):
        d = tmp_path / folder
        d.mkdir(exist_ok=True)
        p = str(d / name)
        shutil.copy2(sample, p)
        os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
        made.append(p)
        return p

    yield make


def _edit(doc):
    doc.move_objects([doc.page.roots()[-1]], 2, 2)
    assert doc.dirty


def _find(folder, name):
    for f in os.listdir(folder):
        if f.lower() == name.lower():
            return os.path.join(folder, f)
    return None


@pytest.fixture()
def reg_project(tmp_path, fx_install):
    """A scratch project: FxMain's pages.DBF, pgtouch (+ index), the change
    log and PROJECT.CIT, and a copy of FxButtons (which has Touch rows)."""
    main = fx_install.project("FxMain")
    sub = tmp_path / "Reg"
    sub.mkdir()
    for n in ("pages.DBF", "pgtouch.DBF", "pgtouch.NDX", "changes.DBF", "CHANGES.NDX", "PROJECT.CIT"):
        src = _find(main, n)
        if src is not None:
            shutil.copy2(src, sub / os.path.basename(src))
    assert _find(str(sub), "pgtouch.DBF") is not None and _find(str(sub), "pages.DBF")
    shutil.copy2(fx_install.page(REG_PAGE), sub / (REG_PAGE + ".ctg"))
    return str(sub)


def _tables(folder):
    """{file name: bytes} of the project's database files (not the pages)."""
    return {
        f: open(os.path.join(folder, f), "rb").read()
        for f in sorted(os.listdir(folder))
        if not f.lower().endswith((".ctg", ".ctf"))
    }


def _run_child(code, *args, timeout=240):
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run(
        [sys.executable, "-c", code] + list(args), env=env, timeout=timeout, capture_output=True, text=True
    )


def _journals():
    from ctgedit.format import journal

    return glob.glob(os.path.join(journal.journal_dir(), "*.jnl"))


# ======================================================================= journal
def test_journal_rolls_back_every_file_of_a_failed_transaction(tmp_path):
    from ctgedit.format import journal

    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"A" * 100)
    b.write_bytes(b"B" * 50)
    with pytest.raises(RuntimeError):
        with journal.transaction("test"):
            with journal.open_patch(str(a)) as fh:
                fh.seek(10)
                fh.write(b"xx")
                fh.seek(0, 2)
                fh.write(b"tail")  # past the old end: truncated back
            fh2 = journal.open_patch(str(b))
            fh2.seek(48)
            fh2.write(b"ZZZZ")
            fh2.close()
            raise RuntimeError("boom")
    assert a.read_bytes() == b"A" * 100 and b.read_bytes() == b"B" * 50
    assert not _journals()


def test_journal_commit_keeps_the_writes_and_removes_the_journal(tmp_path):
    from ctgedit.format import journal

    a = tmp_path / "a.bin"
    a.write_bytes(b"A" * 20)
    with journal.transaction("test") as txn:
        with journal.open_patch(str(a)) as fh:
            fh.seek(2)
            fh.write(b"ok")
    assert a.read_bytes() == b"AAokAAAAAAAAAAAAAAAA"
    assert txn.written == [str(a)] and not _journals()
    # a lone file: a block that raises rolls just that file back
    with pytest.raises(ValueError):
        with journal.open_patch(str(a)) as fh:
            fh.write(b"--")
            raise ValueError("stop")
    assert a.read_bytes() == b"AAokAAAAAAAAAAAAAAAA"


_CHILD_JOURNAL = r"""
import os, sys
from ctgedit.infra.paths import hard_exit
from ctgedit.format import journal
p = sys.argv[1]
with journal.transaction("child update"):
    fh = journal.open_patch(p)
    fh.seek(5)
    fh.write(b"child!")
    fh.seek(0, 2)
    fh.write(b"appended")
    hard_exit(7)                    # killed half-way through
"""


def test_a_killed_update_is_rolled_back_on_the_next_start(tmp_path):
    from ctgedit.format import journal

    p = tmp_path / "t.DBF"
    p.write_bytes(bytes(range(64)))
    r = _run_child(_CHILD_JOURNAL, str(p))
    assert r.returncode == 7, r.stderr
    assert p.read_bytes() != bytes(range(64))  # the half-done update is there
    assert len(_journals()) == 1
    notes = journal.recover_stale()
    assert p.read_bytes() == bytes(range(64))
    assert notes and notes[0]["label"] == "child update" and notes[0]["restored"]
    assert not _journals()


def test_a_file_changed_after_the_crash_is_left_alone(tmp_path):
    from ctgedit.format import journal

    p = tmp_path / "t.DBF"
    p.write_bytes(bytes(range(64)))
    assert _run_child(_CHILD_JOURNAL, str(p)).returncode == 7
    with open(p, "ab") as fh:
        fh.write(b"studio wrote this")  # another program, after the crash
    after = p.read_bytes()
    notes = journal.recover_stale()
    assert p.read_bytes() == after
    assert notes[0]["skipped"] == [str(p)] and not notes[0]["restored"]
    assert not _journals()


def test_start_up_reports_rolled_back_updates(win, tmp_path):
    from ctgedit import app

    p = tmp_path / "t.DBF"
    p.write_bytes(bytes(range(64)))
    assert _run_child(_CHILD_JOURNAL, str(p)).returncode == 7
    notes = app.recover_journals()
    assert p.read_bytes() == bytes(range(64)) and notes
    win.report_journal_recovery(notes)
    box = win._journal_box
    assert box.isVisible() and not box.isModal()
    assert "child update" in box.text() and "t.DBF" in box.text()
    box.close()


def test_register_failure_leaves_every_table_as_it_was(reg_project, monkeypatch):
    from ctgedit.format import dbf
    from ctgedit.io import pgregister

    page = os.path.join(reg_project, REG_PAGE + ".ctg")
    pgregister.delete_page_rows(reg_project, REG_PAGE)  # so the register appends rows
    before = _tables(reg_project)
    calls = {"n": 0}
    real = dbf.Dbf.append

    def append(self, recs):
        calls["n"] += 1
        if calls["n"] == 2:  # the change log: disk full
            raise OSError(28, "No space left on device")
        return real(self, recs)

    monkeypatch.setattr(dbf.Dbf, "append", append)
    with pytest.raises(OSError):
        pgregister.register_page(reg_project, REG_PAGE, page)
    assert calls["n"] == 2
    assert _tables(reg_project) == before  # pgtouch rows + index undone
    assert not _journals()
    monkeypatch.setattr(dbf.Dbf, "append", real)
    rep = pgregister.register_page(reg_project, REG_PAGE, page)
    names = {os.path.basename(p).lower() for p in rep["files"]}
    assert "pgtouch.dbf" in names and "changes.dbf" in names


_CHILD_REGISTER = r"""
import os, sys
from ctgedit.infra.paths import hard_exit
from ctgedit.format import dbf
from ctgedit.io import pgregister
real = dbf.Dbf.append
def append(self, recs):
    r = real(self, recs)
    hard_exit(9)                    # killed right after the first table grew
dbf.Dbf.append = append
d = sys.argv[1]
pgregister.register_page(d, "FxButtons", os.path.join(d, "FxButtons.ctg"))
"""


def test_a_register_killed_half_way_is_undone_on_the_next_start(reg_project):
    from ctgedit.format import journal
    from ctgedit.io import pgregister

    pgregister.delete_page_rows(reg_project, REG_PAGE)
    before = _tables(reg_project)
    r = _run_child(_CHILD_REGISTER, reg_project)
    assert r.returncode == 9, r.stderr
    assert _tables(reg_project) != before
    notes = journal.recover_stale()
    assert notes and REG_PAGE in notes[0]["label"]
    assert _tables(reg_project) == before


# ======================================================================= autosave
_CHILD_AUTOSAVE = r"""
import os, sys
from ctgedit.infra.paths import hard_exit
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtWidgets
app = QtWidgets.QApplication([])
from ctgedit.ui.main_window import MainWindow
from ctgedit.ui.recovery import controller
w = MainWindow()
w.open_document(sys.argv[1])
doc = w.doc
doc.move_objects([doc.page.roots()[-1]], 3, 4)
doc.set_page_background(0x00123456)
written = controller().tick(force=True)
assert written, "no recovery copy written"
open(sys.argv[2], "wb").write(doc.serialize())
hard_exit(3)                        # the editor is killed with unsaved changes
"""


def test_killed_editor_offers_its_unsaved_changes_with_identical_bytes(win, pages, tmp_path, monkeypatch):
    from ctgedit.infra import autosave, paths
    from ctgedit.ui import recovery

    page = pages("Crash.ctg")
    on_disk = open(page, "rb").read()
    expect_path = str(tmp_path / "expected.bin")
    r = _run_child(_CHILD_AUTOSAVE, page, expect_path)
    assert r.returncode == 3, r.stderr
    expected = open(expect_path, "rb").read()
    assert open(page, "rb").read() == on_disk  # nothing reached the page
    assert os.listdir(os.path.dirname(page)) == ["Crash.ctg"]  # nothing next to it either
    snaps = autosave.orphans()
    assert len(snaps) == 1 and os.path.normcase(snaps[0].path) == os.path.normcase(page)
    assert snaps[0].data_path.startswith(paths.data_dir("autosave"))
    assert not snaps[0].data_path.lower().endswith(".ctg")
    # the next start: the session reopens the page (its saved version), then
    # the Recover dialog -- Restore takes over that tab
    win.open_document(page)
    monkeypatch.setattr(
        recovery.RecoveryDialog, "exec", lambda self: setattr(self, "choice", self.RESTORE) or 1
    )
    assert win.offer_recovery() == 1
    assert len(win._docs) == 1
    assert win.doc.dirty and os.path.normcase(win.doc.path) == os.path.normcase(page)
    assert win.doc.serialize() == expected  # byte for byte
    # (the unsaved mark is the tab's dot, not text: w1-tabs)
    assert win.doc_tabs.tab_state(0)["dirty"] and "•" in win.windowTitle()
    assert autosave.orphans() == []
    assert open(page, "rb").read() == on_disk
    # saving it writes the recovered version into the project (with a fresh
    # thumbnail of it: every other byte of the records is the recovered one)
    assert win.save()
    from ctgedit.format import writer
    from ctgedit.format.page import parse_ctg_bytes

    def records(data):
        return [writer.object_payload(o) for o in parse_ctg_bytes(data).page.objects]

    saved = open(page, "rb").read()
    assert saved == win.doc.serialize() and records(saved) == records(expected)
    assert records(saved) != records(on_disk)


def test_recovery_copy_written_on_idle_and_removed_on_save(win, pages, monkeypatch):
    from ctgedit.infra import autosave
    from ctgedit.ui import recovery

    page = pages("A.ctg")
    win.open_document(page)
    _edit(win.doc)
    clock = {"t": 1000.0}
    monkeypatch.setattr(recovery, "_now", lambda: clock["t"])
    ctl = recovery.controller()

    def mine():
        return [s for s in autosave.snapshots() if os.path.normcase(s.path) == os.path.normcase(page)]

    ctl.tick()
    assert not mine()  # still editing: not yet
    clock["t"] += recovery.IDLE_SECONDS + 1
    ctl.tick()
    assert len(mine()) == 1  # the pause after the edit wrote it
    assert autosave.read_bytes(mine()[0]) == win.doc.serialize()
    assert os.listdir(os.path.dirname(page)) == ["A.ctg"]
    assert win.save()
    assert not mine()  # saved: the copy is gone


def test_recovery_copy_removed_when_the_page_is_discarded(win, pages, monkeypatch):
    from ctgedit.infra import autosave
    from ctgedit.ui import recovery

    page = pages("B.ctg")
    win.open_document(page)
    _edit(win.doc)
    recovery.controller().tick(force=True)
    assert any(os.path.normcase(s.path) == os.path.normcase(page) for s in autosave.snapshots())
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Discard)
    assert win._close_doc_tab(0)
    assert not any(os.path.normcase(s.path) == os.path.normcase(page) for s in autosave.snapshots())


def test_autosave_can_be_turned_off_in_settings(win, pages, monkeypatch):
    from ctgedit.infra import autosave
    from ctgedit.ui import recovery
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.prefs.settings import app_settings

    dlg = SettingsDialog(win)
    assert dlg._autosave.value() == recovery.DEFAULT_MINUTES
    dlg._autosave.setValue(0)
    dlg._apply()
    assert app_settings().value("opt/autosave_minutes", type=int) == 0
    page = pages("Off.ctg")
    win.open_document(page)
    _edit(win.doc)
    clock = {"t": 50.0}
    monkeypatch.setattr(recovery, "_now", lambda: clock["t"])
    recovery.controller().tick()
    clock["t"] += 3600
    recovery.controller().tick()
    assert not [s for s in autosave.snapshots() if os.path.normcase(s.path) == os.path.normcase(page)]


def test_recovery_dialog_lists_the_copies_and_confirms_a_discard(tmp_path, monkeypatch):
    _app()
    from ctgedit.infra import autosave
    from ctgedit.ui.recovery import RecoveryDialog

    snaps = []
    for n in ("One", "Two"):
        p = str(tmp_path / "Proj" / (n + ".ctg"))
        s = autosave.Snapshot(autosave.key_for(p, pid=999999), 999999, p, title=n)
        snaps.append(autosave.write(s, b"x"))
    dlg = RecoveryDialog(snaps)
    assert dlg.list.count() == 2 and "One" in dlg.list.item(0).text()
    assert "page on disk has changed" not in dlg.list.item(0).text()
    dlg.list.item(1).setCheckState(QtCore.Qt.Unchecked)
    assert [s.title for s in dlg.selected()] == ["One"]
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Cancel)
    dlg.btn_discard.click()
    assert dlg.choice == RecoveryDialog.LATER  # cancelled: nothing chosen
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Discard)
    dlg.btn_discard.click()
    assert dlg.choice == RecoveryDialog.DISCARD


def test_restore_backup_command_puts_packed_libraries_back(win, tmp_path, monkeypatch, smoke, sample):
    import ctgedit.backend.graphics_builder as gb
    from ctgedit.ui import recovery

    proj, libs = _two_libraries(tmp_path, smoke)
    page = os.path.join(proj, "P.ctg")
    shutil.copy2(sample, page)
    win.open_document(page)
    before = {p: open(p, "rb").read() for p in libs}
    monkeypatch.setattr(gb, "_ctdraw_pids", lambda: [])
    told = []
    monkeypatch.setattr(
        type(win),
        "_show_run_results",
        lambda self, title, lines, **k: told.append((title, "\n".join(lines))) or False,
    )
    win._pack_libraries(False, ask=False)
    assert {p: open(p, "rb").read() for p in libs} != before
    assert told[-1][0] == "Pack Libraries" and "2 library files packed" in told[-1][1]

    def pick(self):
        assert self.list.count() == 1 and self.contents.count() == 2
        self.chosen = self.backups[0]
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(recovery.RestoreBackupDialog, "exec", pick)
    win._restore_backup_dialog()
    assert told[-1][0] == "Restore Backup" and "2 items restored" in told[-1][1]
    assert {p: open(p, "rb").read() for p in libs} == before


def test_restore_backup_confirmation_counts_in_the_singular(monkeypatch):
    # live: 'Put the 1 pages of this backup back as they were ...'
    from ctgedit.infra.backups import BackupInfo
    from ctgedit.ui.recovery import RestoreBackupDialog

    asked = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning", lambda *a, **k: asked.append(a[2]) or QtWidgets.QMessageBox.Cancel
    )
    one = BackupInfo("x", "update-pages", time.time(), [{"page": "P"}])
    five = BackupInfo("y", "pack", time.time(), [{"original": str(i)} for i in range(5)])
    for b in (one, five):
        dlg = RestoreBackupDialog([b])
        dlg._restore()
        assert dlg.chosen is None  # cancelled
    assert asked[0].startswith(
        "Put the 1 page of this backup back as it was before "
        "Update Pages ran? Changes made to it since are lost."
    )
    assert asked[1].startswith(
        "Put the 5 libraries of this backup back as they were "
        "before Pack Libraries ran? Changes made to them since"
    )


def test_a_clean_exit_removes_the_processes_copies(tmp_path):
    from ctgedit.infra import autosave

    s = autosave.Snapshot(autosave.key_for(str(tmp_path / "x.ctg")), os.getpid(), str(tmp_path / "x.ctg"))
    autosave.write(s, b"data")
    other = autosave.Snapshot(
        autosave.key_for(str(tmp_path / "y.ctg"), pid=999999), 999999, str(tmp_path / "y.ctg")
    )
    autosave.write(other, b"other")
    assert autosave.remove_process() == 1
    left = autosave.snapshots()
    assert [x.pid for x in left] == [999999]
    assert [x.path for x in autosave.orphans()] == [str(tmp_path / "y.ctg")]


# ======================================================================= logging
def test_activity_log_lists_every_file_a_save_wrote(reg_project):
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import logsetup

    path = logsetup.install()
    try:
        doc = EditableDocument.open(os.path.join(reg_project, REG_PAGE + ".ctg"))
        _edit(doc)
        doc.save()
    finally:
        logsetup.uninstall()
    assert path and os.path.normcase(os.path.dirname(path)) == os.path.normcase(logsetup.log_dir())
    text = open(logsetup.log_path(), encoding="utf-8").read()
    assert "saved page" in text
    assert len(doc.save_files) >= 2  # the page + database files
    for f in doc.save_files:
        assert f in text, f


def test_help_menu_copies_diagnostics(win, pages):
    page = pages("Diag.ctg")
    win.open_document(page)
    labels = [a.text() for m in win.menuBar().findChildren(QtWidgets.QMenu) for a in m.actions()]
    assert "Open Log Folder" in labels and "Copy Diagnostics" in labels
    text = win._copy_diagnostics()
    assert QtWidgets.QApplication.clipboard().text() == text
    assert "Version" in text and "Open documents (1)" in text and page in text
    assert "  %s: %s" % (os.path.basename(page), page) in text  # name, then path
    assert "Activity log" in text


# ======================================================================= pack
def _two_libraries(tmp_path, smoke):
    from ctgedit.format import element as E

    proj = tmp_path / "Libs"
    proj.mkdir()
    out = []
    for n in ("A.ctl", "B.ctl"):
        p = str(proj / n)
        shutil.copy(smoke, p)
        assert E.delete_library_element(p, DEAD)  # something to pack
        out.append(p)
    return str(proj), out


def test_pack_keeps_every_element_and_backs_up_first(tmp_path, smoke):
    from ctgedit.format import element as E
    from ctgedit.format.projects import _element_content, pack_libraries, restore_pack_backup
    from ctgedit.infra import backups

    proj, libs = _two_libraries(tmp_path, smoke)
    before = {p: open(p, "rb").read() for p in libs}
    contents = {}
    for p in libs:
        L = E.LibraryFile.open(p)
        contents[p] = {n: _element_content(L, L.find(n)) for n in L.element_names()}
        assert DEAD not in contents[p]
    folder = backups.new_folder("pack")
    res = pack_libraries(proj, backup=folder)
    files, saved = res  # still unpacks as a pair
    assert files == 2 and saved > 0 and not res.failures
    for p in libs:
        L = E.LibraryFile.open(p)
        assert L.element_names() == list(contents[p])
        assert not any((s.tag & 0xFF) == 0 and s.size for s in L.slots)  # no deleted blocks
        for n, content in contents[p].items():
            s = L.find(n)
            assert L.block_data(s) == content + b"\0" * 0x200  # CtDraw's new-page extent
    m = backups.read_manifest(folder)
    assert m["kind"] == "pack" and len(m["entries"]) == 2
    assert pack_libraries(proj) == (0, 0)  # idempotent
    # Restore Backup puts the originals back
    r = restore_pack_backup(folder)
    assert sorted(r.packed) == sorted(libs) and not r.failures
    assert {p: open(p, "rb").read() for p in libs} == before


def test_a_failed_pack_write_puts_every_library_back(tmp_path, monkeypatch, smoke):
    from ctgedit.format import fileio
    from ctgedit.format.projects import PackError, pack_libraries
    from ctgedit.infra import backups

    proj, libs = _two_libraries(tmp_path, smoke)
    before = {p: open(p, "rb").read() for p in libs}
    real = fileio.write_atomic

    def write(path, data):
        if os.path.normcase(path) == os.path.normcase(libs[1]):
            raise OSError(28, "No space left on device")
        return real(path, data)

    monkeypatch.setattr(fileio, "write_atomic", write)
    folder = backups.new_folder("pack")
    with pytest.raises(PackError) as ei:
        pack_libraries(proj, backup=folder)
    assert "B.ctl" in str(ei.value) and "put back" in str(ei.value)
    assert {p: open(p, "rb").read() for p in libs} == before  # A was packed, then undone
    # the backup still holds both originals
    for p in libs:
        assert open(os.path.join(folder, "Libs", os.path.basename(p)), "rb").read() == before[p]


def test_directory_growth_moves_the_overlapped_block_to_the_end(tmp_path, fx_install):
    """The 9th element of an 8-slot library: it is appended, and at close the block the
    13-slot directory overlaps moves to the end, with its extent + 0x200 holding the bytes
    that followed it (as CtDraw moves it). On fxmany.ctl's first nine elements; the bytes
    CtDraw writes for a shipped library are pinned in tests_corpus/test_robustness.py."""
    from ctgedit.format import element as E
    from ctgedit.format.projects import _element_content

    src = E.LibraryFile.open(fx_install.file("FxLib", "fxmany.ctl"))
    names = src.element_names()[:9]
    eight = E.LibraryFile.new(src.version, src.word2, 1)
    for n in names[:8]:
        eight.write_element(n, _element_content(src, src.find(n)))
    assert eight.dword_c == 8
    old = eight.find(names[0]).size  # its extent before the move
    lib = str(tmp_path / "eight.ctl")
    open(lib, "wb").write(eight.to_bytes())
    E.store_element(lib, names[8], _element_content(src, src.find(names[8])))
    data = open(lib, "rb").read()
    L = E.LibraryFile(data)
    assert L.element_names() == names
    a, d = L.find(names[0]), L.find(names[1])
    content = _element_content(src, src.find(names[0]))
    assert (L.dword_c, a.tag) == (13, 0x0101)
    assert a.offset == max(L.find(n).offset for n in names)  # moved to the end
    assert old == len(content) + 0x200 and a.size == old + 0x200
    assert data[a.offset : a.offset + len(content)] == content
    assert data[a.offset + old : a.offset + a.size] == data[d.offset : d.offset + 0x200]
    for n in names:
        assert _element_content(L, L.find(n)) == _element_content(src, src.find(n))


def test_new_container_and_directory_chunks_follow_ctdraw():
    from ctgedit.format import element as E
    from ctgedit.format.projects import packed_library

    new = E.LibraryFile.new()
    assert (new.slots[0].tag, new.slots[0].size, new.slots[0].offset) == (0x0100, 0, 0)
    assert packed_library(new.to_bytes()) is None  # an empty library: nothing to do
    one = E.LibraryFile.new()
    one.write_element("a", b"x" * 0x40)
    assert one.slots[0].tag == 0x0101 and one.slots[0].offset == 352
    caps = []
    lib = E.LibraryFile.new()
    for i in range(103):
        lib.write_element("e%d" % i, bytes([i]) * 0x40)
        if lib.dword_c not in caps:
            caps.append(lib.dword_c)
    assert caps[-4:] == [93, 97, 102, 107]  # CtDraw's 97-entry chunks
    back = E.LibraryFile(lib.to_bytes())
    assert back.element_names() == ["e%d" % i for i in range(103)]


def test_pack_copies_a_last_block_that_outgrew_its_entry(tmp_path, smoke):
    """A 'last' block that grew in place can run past its directory size; CtDraw's copy takes
    the length from the page head, so the pack must not cut the element off at the recorded size."""
    import struct
    from ctgedit.format import element as E
    from ctgedit.format.projects import _element_content, packed_library

    data = bytearray(open(smoke, "rb").read())
    L = E.LibraryFile(bytes(data))
    s4 = L.find(LAST)
    full = _element_content(L, s4)
    assert s4.tag == 0x0101 and len(full) > 1000
    o = 16 + s4.index * 42 + 2 + 32 + 4  # the entry's size field
    struct.pack_into("<I", data, o, len(full) - 700)
    L2 = E.LibraryFile(bytes(data))
    assert L2.find(LAST).size == len(full) - 700
    p = str(tmp_path / "s.ctl")
    open(p, "wb").write(bytes(data))
    assert E.delete_library_element(p, DEAD)
    packed = E.LibraryFile(packed_library(open(p, "rb").read()))
    assert packed.block_data(packed.find(LAST))[: len(full)] == full


def test_unreadable_library_is_listed_not_packed(tmp_path, smoke):
    from ctgedit.format.projects import pack_libraries

    proj, libs = _two_libraries(tmp_path, smoke)
    junk = os.path.join(proj, "junk.ctm")
    open(junk, "wb").write(b"\xff" * 5)
    res = pack_libraries(proj)
    assert res.changed == 2
    assert [p for p, _why in res.failures] == [junk]
    assert open(junk, "rb").read() == b"\xff" * 5


def test_pack_is_refused_while_the_graphics_builder_runs(win, pages, tmp_path, monkeypatch, smoke, sample):
    import ctgedit.backend.graphics_builder as gb

    proj, libs = _two_libraries(tmp_path, smoke)
    page = os.path.join(proj, "P.ctg")
    shutil.copy2(sample, page)
    win.open_document(page)
    before = {p: open(p, "rb").read() for p in libs}
    seen = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning", lambda *a, **k: seen.append(a[2]) or QtWidgets.QMessageBox.Ok
    )
    monkeypatch.setattr(gb, "_ctdraw_pids", lambda: [4242])
    win._pack_libraries(False, ask=False)
    assert seen and "Graphics Builder is running" in seen[0]
    assert {p: open(p, "rb").read() for p in libs} == before
    # without it the pack runs and makes a backup
    monkeypatch.setattr(gb, "_ctdraw_pids", lambda: [])
    monkeypatch.setattr(type(win), "_show_run_results", lambda self, title, lines, **k: False)
    win._pack_libraries(False, ask=False)
    assert {p: open(p, "rb").read() for p in libs} != before
    from ctgedit.infra import backups

    assert [b.kind for b in backups.list_backups()] == ["pack"]


# ======================================================================= update pages
def test_update_pages_backup_restores_page_and_rows(reg_project):
    from ctgedit.editing import EditableDocument
    from ctgedit.io import pgregister, updatepages
    from ctgedit.infra import backups

    page = os.path.join(reg_project, REG_PAGE + ".ctg")
    orig = open(page, "rb").read()
    rows0 = pgregister.export_page_rows(reg_project, REG_PAGE)
    assert rows0.get("pgtouch")
    folder = updatepages.backup_dir()
    updatepages._backup_page(page, folder)
    assert backups.read_manifest(folder)["entries"][0]["page"] == REG_PAGE
    # the update rewrites the page and its rows (a Touch object goes)
    doc = EditableDocument.open(page)
    touch = next(i for i, o in enumerate(doc.parsed.page.objects) if o.type_code == 0x1010)
    doc.delete_objects([doc.root_of(touch)])
    doc.save()
    assert open(page, "rb").read() != orig
    assert pgregister.export_page_rows(reg_project, REG_PAGE) != rows0
    rep = updatepages.restore_backup(folder)
    assert rep.restored == [REG_PAGE] and not rep.failed
    assert open(page, "rb").read() == orig
    assert pgregister.export_page_rows(reg_project, REG_PAGE) == rows0  # same records too
    # a page open in the Graphics Builder is not restored
    rep = updatepages.restore_backup(folder, builder_open=[("Reg", REG_PAGE)])
    assert not rep.restored and "Graphics Builder" in rep.failed[0]


def test_backups_are_pruned(tmp_path):
    from ctgedit.infra import backups

    root = str(tmp_path / "bk")
    now = time.time()
    for k in range(6):
        d = os.path.join(root, "update-pages-%d" % k)
        backups.add_entry(d, "update-pages", {"page": "P%d" % k})
        m = backups.read_manifest(d)
        m["created"] = now - k * 86400.0 * 3  # 0, 3, 6, ... days old
        open(os.path.join(d, backups.MANIFEST), "w").write(json.dumps(m))
    removed = backups.prune(root, keep=4, max_age_days=5, min_keep=2)
    left = sorted(os.path.basename(b.path) for b in backups.list_backups(root))
    # the 2 newest always stay; of the 4 kept by count, the >5-day ones go
    assert left == ["update-pages-0", "update-pages-1"]
    assert len(removed) == 4


# ======================================================================= builder guard
def test_saving_a_page_open_in_the_builder_asks_first(win, pages, monkeypatch):
    import ctgedit.backend.graphics_builder as gb

    page = pages("B.ctg")
    win.open_document(page)
    _edit(win.doc)
    before = open(page, "rb").read()
    monkeypatch.setattr(gb, "open_pages", lambda: [("Proj", "B")])
    asked = []
    monkeypatch.setattr(
        win, "_confirm", lambda title, text, ok, detail="", **k: asked.append((title, text, ok)) or False
    )
    assert win.save() is False
    assert asked and asked[0][0] == "Open in the Graphics Builder"
    assert open(page, "rb").read() == before and win.doc.dirty
    monkeypatch.setattr(win, "_confirm", lambda *a, **k: True)
    assert win.save() is True
    assert open(page, "rb").read() != before and not win.doc.dirty
    # the save's own 'Saved ...' message carries the note
    msg = win.statusBar().currentMessage()
    assert msg.startswith("Saved ") and "Reopen B in the Graphics Builder" in msg


def test_save_all_reports_a_page_kept_for_the_builder(win, pages, monkeypatch):
    import ctgedit.backend.graphics_builder as gb

    page = pages("C.ctg")
    win.open_document(page)
    _edit(win.doc)
    monkeypatch.setattr(gb, "open_pages", lambda: [("Proj", "C")])
    monkeypatch.setattr(win, "_confirm", lambda *a, **k: False)
    from ctgedit.ui import messages

    seen = []
    monkeypatch.setattr(
        messages, "list_box", lambda _p, _t, text, items, **k: seen.append(" ".join([text] + list(items)))
    )
    win.save_all()
    assert seen and "open in the Graphics Builder" in seen[0]
    assert win.doc.dirty


# ======================================================================= revert / watcher
def test_revert_reloads_the_page_from_disk(win, pages, monkeypatch):
    page = pages("R.ctg")
    win.open_document(page)
    disk = open(page, "rb").read()
    _edit(win.doc)
    assert win.doc.serialize() != disk
    monkeypatch.setattr(win, "_confirm", lambda *a, **k: True)
    assert win.act_revert.isEnabled()
    assert win.revert_document() is True
    assert not win.doc.dirty and not win.doc.can_undo()
    assert win.doc.serialize() == disk
    assert len(win._docs) == 1


class _Click:
    """QMessageBox.exec stand-in: records the text, presses the button of
    the wanted role."""

    def __init__(self, monkeypatch, role):
        self.seen, self.role = [], role
        monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda box: self._exec(box))

    def _exec(self, box):
        self.seen.append(box.text() + "\n" + box.informativeText())
        for b in box.buttons():
            if box.buttonRole(b) == self.role:
                b.click()
                break
        return 0


def _ask_with_a_box():
    """Settings > Pages and libraries > Tell me about a changed page: With a question box
    (the default is the page's notice bar: tests/test_page_notices.py)."""
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("ui/disk_change_prompt", "dialog")


def test_a_page_changed_by_another_program_is_offered_for_reload(win, pages, monkeypatch):
    _ask_with_a_box()
    page = pages("W.ctg")
    win.open_document(page)
    assert any(os.path.normcase(f) == os.path.normcase(page) for f in win._watcher.files())
    old = win.doc
    boxes = _Click(monkeypatch, QtWidgets.QMessageBox.AcceptRole)
    win._check_disk_changes([page])
    assert not boxes.seen  # nothing changed yet
    st = os.stat(page)
    os.utime(page, ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10**9))  # another program
    win._check_disk_changes([page])
    assert len(boxes.seen) == 1 and "changed by another program" in boxes.seen[0]
    assert win.doc is not old and not win.doc.dirty  # reloaded
    win._check_disk_changes([page])
    assert len(boxes.seen) == 1  # asked once per change
    # our own save is not "another program"
    _edit(win.doc)
    assert win.save()
    win._check_disk_changes([page])
    assert len(boxes.seen) == 1


def test_a_modified_page_changed_on_disk_keeps_the_users_version(win, pages, monkeypatch):
    _ask_with_a_box()
    page = pages("K.ctg")
    win.open_document(page)
    _edit(win.doc)
    mine = win.doc.serialize()
    boxes = _Click(monkeypatch, QtWidgets.QMessageBox.RejectRole)  # Keep Mine
    st = os.stat(page)
    os.utime(page, ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10**9))
    win._check_disk_changes([page])
    assert boxes.seen and "unsaved changes" in boxes.seen[0]
    assert win.doc.dirty and win.doc.serialize() == mine


def _touch(page, s=5):
    st = os.stat(page)
    os.utime(page, ns=(st.st_atime_ns, st.st_mtime_ns + s * 10**9))  # another program


def _quiet(win):
    """No file-change question left queued for later (the window stays alive after the test:
    a question then has no stand-in and waits for ever)."""
    with contextlib.suppress(Exception):
        win._watcher.removePaths(win._watcher.files())
    win._disk_timer.stop()
    win._disk_pending.clear()


def test_reload_automatically_reloads_an_unmodified_page_without_asking(win, pages, monkeypatch):
    """Settings > Pages and libraries: 'Reload it' reloads an unmodified page at once (also
    while the user is in the other program); a page with unsaved changes is still asked
    about; 'Do nothing' leaves both."""
    from ctgedit.ui.prefs.settings import app_settings, set_disk_reload

    _ask_with_a_box()
    page = pages("A.ctg")
    win.open_document(page)
    boxes = _Click(monkeypatch, QtWidgets.QMessageBox.RejectRole)
    try:
        set_disk_reload("always")
        old = win.doc
        _touch(page)
        win._check_disk_changes([page])
        assert not boxes.seen and win.doc is not old and not win.doc.dirty
        _edit(win.doc)
        _touch(page, 10)
        win._check_disk_changes([page])
        assert len(boxes.seen) == 1 and "unsaved changes" in boxes.seen[0]
        assert win.doc.dirty
        set_disk_reload("never")
        mine = win.doc
        _touch(page, 15)
        win._check_disk_changes([page])
        assert len(boxes.seen) == 1 and win.doc is mine
    finally:
        _quiet(win)
        app_settings().remove("opt/disk_reload")


def test_dont_ask_again_remembers_the_answer(win, pages, monkeypatch):
    """The Changed on Disk question of an unmodified page has Don't ask again: Reload stores
    'Reload it', Not Now 'Do nothing' (Settings > Pages and libraries changes it back)."""
    from ctgedit.ui.prefs.settings import app_settings, disk_reload

    _ask_with_a_box()

    def answer(role):
        def run(box):
            box.checkBox().setChecked(True)
            for b in box.buttons():
                if box.buttonRole(b) == role:
                    b.click()
                    break

        return run

    page = pages("D.ctg")
    win.open_document(page)
    try:
        assert disk_reload() == "ask"
        monkeypatch.setattr(win, "_exec_disk_prompt", answer(QtWidgets.QMessageBox.AcceptRole))
        old = win.doc
        _touch(page)
        win._check_disk_changes([page])
        assert disk_reload() == "always" and win.doc is not old
        app_settings().remove("opt/disk_reload")
        monkeypatch.setattr(win, "_exec_disk_prompt", answer(QtWidgets.QMessageBox.RejectRole))
        old = win.doc
        _touch(page, 10)
        win._check_disk_changes([page])
        assert disk_reload() == "never" and win.doc is old
    finally:
        _quiet(win)
        app_settings().remove("opt/disk_reload")


# ======================================================================= damaged files
def test_empty_page_file_says_it_is_empty(tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.format.integrity import DamagedFileError

    p = tmp_path / "E.ctg"
    p.write_bytes(b"")
    with pytest.raises(DamagedFileError) as ei:
        EditableDocument.open(str(p))
    assert "E.ctg" in str(ei.value) and "empty" in str(ei.value)


def _damaged_copy(tmp_path, sample):
    """A copy of the sample whose last record claims a member it lacks."""
    from ctgedit.format import writer
    from ctgedit.format.page import parse_ctg

    p = parse_ctg(sample)
    p.page.objects[-1].class_flag = 1
    d = tmp_path / "Proj"
    d.mkdir(exist_ok=True)
    out = str(d / "D.ctg")
    open(out, "wb").write(writer.serialize_ctg(p.container, p.page, p.attributes))
    return out


def test_damaged_page_opens_with_a_warning_and_asks_before_overwrite(win, tmp_path, monkeypatch, sample):
    page = _damaged_copy(tmp_path, sample)
    win.open_document(page)
    assert win.doc.damage and "expects 1 more member" in win.doc.damage[0]
    # (the page's notice bar says so, with Save As and Details: w2-status)
    assert "damaged" in win._auto_bar.keys() and not win._auto_bar.isHidden()
    _edit(win.doc)
    before = open(page, "rb").read()
    boxes = _Click(monkeypatch, QtWidgets.QMessageBox.RejectRole)  # Cancel
    assert win.save() is False
    assert "damaged when it was opened" in boxes.seen[0]
    assert open(page, "rb").read() == before
    boxes.role = QtWidgets.QMessageBox.DestructiveRole  # Overwrite
    assert win.save() is True
    assert open(page, "rb").read() != before


# ======================================================================= session
def test_session_list_is_merged_with_another_processes(win, pages):
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    other = [r"C:\elsewhere\Other1.ctg", r"C:\elsewhere\Other2.ctg"]
    st.setValue("session/open_files", other)
    a, b = pages("A.ctg"), pages("B.ctg")
    win.open_document(a)
    win.open_document(b)
    win.save_prefs()
    files = [str(f) for f in st.value("session/open_files", [], type=list)]
    assert files[:2] == other and set(map(os.path.normcase, files[2:])) == {
        os.path.normcase(a),
        os.path.normcase(b),
    }
    # a page this process closed drops out; the other editor's pages stay
    win._close_doc_tab(1)
    win.save_prefs()
    files = [str(f) for f in st.value("session/open_files", [], type=list)]
    assert files == other + [os.path.abspath(a)]


# ======================================================================= edit atomicity
def test_an_edit_failing_half_way_leaves_the_page_as_it_was(sample):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(sample)
    before = doc.serialize()
    n_undo = len(doc._undo)
    po = doc.parsed.page.objects[0]

    def do():
        po.body_raw = bytes(po.body_raw)[:10]  # half done ...
        del doc.parsed.page.objects[-1]
        raise RuntimeError("... and then it fails")

    with pytest.raises(RuntimeError):
        doc._apply("broken", do, lambda: None)
    assert doc.serialize() == before and len(doc._undo) == n_undo


def test_document_from_recovery_bytes_round_trips(tmp_path, sample):
    from ctgedit.editing import EditableDocument

    data = open(sample, "rb").read()
    target = str(tmp_path / "Target.ctg")
    doc = EditableDocument.from_bytes(data, target)
    assert doc.serialize() == data and doc.path == target
    assert doc.page.name == "Target" and not os.path.exists(target)


# ======================================================================= atomic library writes
def test_attach_super_genies_patches_only_its_element(tmp_path, monkeypatch, fx_install):
    """FxLib's fxgenie.ctm (in the fx_install copy: its include path has fxsuper.ctm), whose
    fx_popper attaches !fx_popup; the genie is FxGenies' FxPopper."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import element as E
    from ctgedit.format import links

    lib = fx_install.file("FxLib", "fxgenie.ctm")
    L = E.LibraryFile.open(lib)
    target = None
    for name in L.element_names():
        blk = L.block(name)
        from ctgedit.format.page import parse_page

        pb = parse_page(blk, L.word2)
        t = links.parse_link_table(pb.link_table_raw) if pb.link_table_raw else None
        if t and links.super_genie_attachments(t[1]):
            target = name
            break
    assert target == "fx_popper"
    others = {n: L.block_data(L.find(n)) for n in L.element_names() if n != target}
    doc = EditableDocument.open(fx_install.page("FxGenies"))
    (g,) = doc.find_objects_by_name("FxPopper")
    monkeypatch.setattr(doc, "_genie_template_target", lambda i: (lib, target))
    current = doc.genie_attachments(g)
    assert current == ["fxsuper.!fx_popup"]
    assert not doc.set_genie_attachments(g, current)  # the same list: nothing written
    more = current + ["fxsuper.!fx_detail"]
    assert doc.set_genie_attachments(g, more)
    L2 = E.LibraryFile.open(lib)
    assert {n: L2.block_data(L2.find(n)) for n in L2.element_names() if n != target} == others
    assert doc.genie_attachments(g) == more
    assert doc.set_genie_attachments(g, current)  # and back
    L3 = E.LibraryFile.open(lib)
    assert {n: L3.block_data(L3.find(n)) for n in L3.element_names() if n != target} == others
    assert doc.genie_attachments(g) == current
    assert not os.path.exists(lib + ".ctgedit-tmp")


def test_a_patch_that_only_reads_leaves_no_journal(tmp_path):
    from ctgedit.format import journal

    a = tmp_path / "a.bin"
    a.write_bytes(b"A" * 20)
    fh = journal.open_patch(str(a))  # e.g. an index opened for a lookup
    assert fh.read(4) == b"AAAA"
    assert not _journals()  # nothing written: no journal at all
    fh.close()
    with journal.transaction("read only") as txn:
        with journal.open_patch(str(a)) as fh:
            fh.read()
        assert not _journals()
    assert txn.path is None and not _journals()


def test_a_committed_journal_that_could_not_be_deleted_is_never_replayed(tmp_path, monkeypatch):
    from ctgedit.format import journal

    a = tmp_path / "a.bin"
    a.write_bytes(b"A" * 20)
    real_remove = os.remove

    def remove(p, *args, **kw):
        if str(p).endswith(journal.SUFFIX):
            raise PermissionError(13, "held by a virus scanner")
        return real_remove(p, *args, **kw)

    monkeypatch.setattr(os, "remove", remove)
    with journal.transaction("finished update"):
        with journal.open_patch(str(a)) as fh:
            fh.write(b"new!")
    monkeypatch.setattr(os, "remove", real_remove)
    left = _journals()
    assert len(left) == 1
    # as if the process that wrote it had ended
    lines = open(left[0], encoding="utf-8").read().splitlines()
    head = json.loads(lines[0])
    head.update(pid=999999, started=1)
    open(left[0], "w", encoding="utf-8").write("\n".join([json.dumps(head)] + lines[1:]) + "\n")
    assert journal.recover_stale() == []
    assert a.read_bytes() == b"new!" + b"A" * 16  # the finished update stays
    assert not _journals()


_CHILD_JOURNAL_APPEND = r"""
import os, sys
from ctgedit.infra.paths import hard_exit
from ctgedit.format import journal
p = sys.argv[1]
with journal.transaction("child append") as txn:
    fh = journal.open_patch(p)
    fh.seek(5)
    fh.write(b"child!")
    fh.seek(0, 2)
    txn.before_write(fh._f, fh._fh, 8)       # the append is journaled ...
    hard_exit(7)                              # ... and the process dies before it
"""


def test_a_crash_between_journaling_an_append_and_writing_it_is_rolled_back(tmp_path):
    from ctgedit.format import journal

    p = tmp_path / "t.DBF"
    p.write_bytes(bytes(range(64)))
    assert _run_child(_CHILD_JOURNAL_APPEND, str(p)).returncode == 7
    assert len(p.read_bytes()) == 64 and p.read_bytes() != bytes(range(64))
    notes = journal.recover_stale()
    assert notes and notes[0]["restored"] == [str(p)] and not notes[0]["skipped"]
    assert p.read_bytes() == bytes(range(64))


def test_a_crashed_run_whose_pid_was_reused_is_still_recovered(tmp_path):
    """Windows reuses pids soon: a recovery copy / journal whose pid now
    belongs to another live process (here: this one, or the parent) with
    another start time is a dead run's, offered and rolled back."""
    from ctgedit.format import journal
    from ctgedit.infra import autosave

    p = str(tmp_path / "Reused.ctg")
    stale = autosave.Snapshot(
        autosave.key_for(p, pid=os.getpid(), started=1), os.getpid(), p, title="Reused", started=1
    )
    autosave.write(stale, b"old unsaved work")
    mine = autosave.Snapshot(autosave.key_for(p), os.getpid(), p, title="Reused")
    autosave.write(mine, b"this run")
    assert stale.key != mine.key  # no overwrite of the crash copy
    assert [s.key for s in autosave.orphans()] == [stale.key]
    assert autosave.remove_process() == 1  # a clean exit keeps the crash copy
    assert [s.key for s in autosave.orphans()] == [stale.key]
    # a journal of a dead run whose pid a live process has now
    f = tmp_path / "t.DBF"
    f.write_bytes(bytes(range(32)))
    assert _run_child(_CHILD_JOURNAL, str(f)).returncode == 7
    jp = _journals()[0]
    lines = open(jp, encoding="utf-8").read().splitlines()
    head = json.loads(lines[0])
    head.update(pid=os.getppid(), started=1)
    open(jp, "w", encoding="utf-8").write("\n".join([json.dumps(head)] + lines[1:]) + "\n")
    assert journal.recover_stale() and f.read_bytes() == bytes(range(32))


def test_a_library_element_recovery_copy_restores_the_same_element(tmp_path, smoke):
    from ctgedit.editing import ElementDocument
    from ctgedit.infra import autosave

    lib = str(tmp_path / "fxmore.ctl")
    shutil.copy(smoke, lib)
    doc = ElementDocument.open_element(lib, FIRST)
    doc.move_objects([doc.page.roots()[-1]], 3, 2)
    snap = autosave.save_document(doc)
    assert snap.kind == "element" and snap.element == FIRST
    back = autosave.restore_document(snap)
    assert isinstance(back, ElementDocument) and back.dirty
    assert back.display_name == "fxmore." + FIRST
    assert back.serialize() == doc.serialize()
    assert back.serialize_element() == doc.serialize_element()  # what Save writes
    # a new, never saved element comes back as a new element
    new = ElementDocument.new_element(lib, "Fresh", "symbol")
    new.create_object("rectangle", (10, 10, 60, 40))
    back = autosave.restore_document(autosave.save_document(new))
    assert back.is_new and back.element_name == "Fresh"
    assert back.serialize() == new.serialize()


def test_a_session_entry_that_no_longer_opens_drops_out(win, pages, tmp_path, monkeypatch):
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    gone = str(tmp_path / "Gone" / "Deleted.ctg")
    lib = str(tmp_path / "Gone" / "lib.ctm")
    a = pages("Kept.ctg")
    st.setValue("session/open_files", [gone, lib, a])
    st.setValue("session/last_file", a)
    reported = []
    monkeypatch.setattr(win, "report_open_failures", lambda f: reported.extend(f))
    assert win.restore_session(reopen=True)
    _app().processEvents()  # the (deferred) failure report, here
    assert [p for p, _e in reported] == [gone]
    win.save_prefs()
    files = [str(f) for f in st.value("session/open_files", [], type=list)]
    assert [os.path.normcase(f) for f in files] == [os.path.normcase(a)]


def test_revert_forgets_the_hidden_roots_of_the_old_version(win, pages, monkeypatch):
    page = pages("H.ctg")
    win.open_document(page)
    win.open_document(pages("Other.ctg"))
    win.doc_tabs.setCurrentIndex(0)
    _edit(win.doc)
    win._hidden_roots = {0}
    win.doc_tabs.setCurrentIndex(1)  # the tab keeps {0} while away
    win.doc_tabs.setCurrentIndex(0)
    assert win._hidden_roots == {0}
    monkeypatch.setattr(win, "_confirm", lambda *a, **k: True)
    assert win.revert_document()
    assert win._hidden_roots == set()  # indices of the old version
    assert not win.doc.dirty and len(win._docs) == 2


def test_restoring_a_page_that_had_no_rows_takes_out_the_rows_added_since(reg_project):
    from ctgedit.editing import EditableDocument
    from ctgedit.io import pgregister, updatepages

    page = os.path.join(reg_project, REG_PAGE + ".ctg")
    pgregister.delete_page_rows(reg_project, REG_PAGE)
    assert pgregister.export_page_rows(reg_project, REG_PAGE) == {}
    folder = updatepages.backup_dir()
    updatepages._backup_page(page, folder)
    doc = EditableDocument.open(page)
    _edit(doc)
    doc.save()  # registers the page: rows appear
    assert pgregister.export_page_rows(reg_project, REG_PAGE)
    rep = updatepages.restore_backup(folder)
    assert rep.restored == [REG_PAGE] and not rep.failed
    assert pgregister.export_page_rows(reg_project, REG_PAGE) == {}
