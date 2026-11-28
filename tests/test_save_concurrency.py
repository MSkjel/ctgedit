"""A page save next to other writers."""

from ctgedit.format.fileio import find_ci
import json
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = ["FxGenies", "FxSymbols", "FxButtons", "FxLegacy"]
#: the page the single-page tests save
PAGE = "FxGenies"


def _env(**extra):
    return dict(os.environ, PYTHONPATH=ROOT, **extra)


@pytest.fixture
def proj(fx_install):
    """FxMain of the fake install's copy."""
    return fx_install.project("FxMain")


def _tables(proj):
    """Every database file of the project, as bytes."""
    out = {}
    for fn in sorted(os.listdir(proj)):
        if fn.lower().endswith((".dbf", ".ndx", ".cit")):
            with open(os.path.join(proj, fn), "rb") as fh:
                out[fn] = fh.read()
    return out


def _unchanged(proj, page) -> bool:
    from ctgedit.io import pgregister as PR

    rep = PR.register_page(
        proj, page, os.path.join(proj, page + ".ctg"), log_change=False, mark_uncompiled=False
    )
    return (
        all(v.get("action") in ("unchanged", "absent") for v in rep["tables"].values())
        and not rep.get("pglang")
        and not rep.get("activex")
    )


def _index_matches_scan(proj) -> list:
    """The project's .NDX files (pg*, activex, pages, changes, ...) whose entries are not
    their table's records (recnos 1..n, each key its record's field)."""
    from ctgedit.format.dbf import Dbf, Ndx

    bad = []
    for fn in os.listdir(proj):
        low = fn.lower()
        if not low.endswith(".ndx"):
            continue
        dp = find_ci(proj, fn[:-4] + ".DBF")
        if dp is None:
            continue
        with Dbf(dp, writable=False) as d, Ndx(os.path.join(proj, fn), writable=False) as nx:
            ents = nx.entries()
            ok = sorted(r for _k, r in ents) == list(range(1, d.nrec + 1)) and all(
                nx.key_bytes(d.field(d.read(r - 1), nx.expr)) == k for k, r in ents
            )
        if not ok:
            bad.append(fn)
    return bad


def _journals():
    import glob
    from ctgedit.format import journal

    return glob.glob(os.path.join(journal.journal_dir(), "*.jnl"))


def _share_read_handle(path):
    """A handle on ``path`` that lets others read it only (Plant SCADA Studio holding a
    table): (close function)."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.CreateFileW(path, 0x80000000 | 0x40000000, 0x1, None, 3, 0x80, None)
    assert h and h != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    return lambda: k32.CloseHandle(h)


# ------------------------------------------------------------------------------------- (a)
def test_a_batch_save_refuses_a_page_changed_since_it_was_read(proj, tmp_path):
    from ctgedit.editing import EditableDocument, PageChangedOnDisk
    from ctgedit.io import pendingrows

    page = os.path.join(proj, PAGE + ".ctg")
    a = EditableDocument.open(page)
    b = EditableDocument.open(page)
    b.save()
    theirs = open(page, "rb").read()
    folder = str(tmp_path / "backup")
    with pytest.raises(PageChangedOnDisk):
        a.save(batch=True, backup=(folder, "update-pages"))
    assert open(page, "rb").read() == theirs
    assert pendingrows.marked(page) is None
    assert not os.path.exists(os.path.join(folder, "manifest.json"))


# ------------------------------------------------------------------------------------- (b)
@pytest.mark.skipif(os.name != "nt", reason="Windows share modes")
def test_a_held_table_refuses_a_batch_save_and_delays_an_editor_saves_rows(proj):
    from ctgedit.editing import EditableDocument, ProjectDatabaseInUse
    from ctgedit.io import pendingrows
    from ctgedit.io import pgregister as PR

    page = os.path.join(proj, PAGE + ".ctg")
    PR.delete_page_rows(proj, PAGE)  # (so the register has rows to write)
    before = _tables(proj)
    ctg = open(page, "rb").read()
    close = _share_read_handle(find_ci(proj, "pgdynobj.DBF"))
    try:
        doc = EditableDocument.open(page)
        with pytest.raises(ProjectDatabaseInUse) as e:
            doc.save(batch=True)
        assert "pgdynobj" in str(e.value)
        assert open(page, "rb").read() == ctg and _tables(proj) == before
        assert not os.path.exists(os.path.join(proj, PAGE + ".ctF"))
        assert pendingrows.marked(page) is None
        # the editor's save writes the page, warns, and leaves the marker
        doc = EditableDocument.open(page)
        doc.save()
        assert open(page, "rb").read() != ctg
        assert "locked" in doc.save_warning and doc.rows_pending
        assert pendingrows.marked(page) is not None
    finally:
        close()
    assert pendingrows.finish(page) == "written"
    assert pendingrows.marked(page) is None
    assert _unchanged(proj, PAGE)
    assert not _index_matches_scan(proj)


# ------------------------------------------------------------------------------------- (c)
_SAVE = r"""
import sys
from ctgedit.editing import EditableDocument
doc = EditableDocument.open(sys.argv[1])
doc.save()
print("saved")
"""


def test_a_save_killed_after_its_page_file_is_finished_later(proj):
    from ctgedit.io import pendingrows
    from ctgedit.io import pgregister as PR

    page = os.path.join(proj, PAGE + ".ctg")
    PR.delete_page_rows(proj, PAGE)
    ctf = os.path.join(proj, PAGE + ".ctF")
    with open(ctf, "wb") as fh:
        fh.write(b"an old fast display")
    os.utime(ctf, (time.time() - 3600, time.time() - 3600))
    r = subprocess.run(
        [sys.executable, "-c", _SAVE, page],
        env=_env(CTG_TEST_EXIT_AT="after_ctg"),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 3, r.stderr
    assert pendingrows.marked(page) is not None
    assert [m["page"] for m in pendingrows.listing([proj])] == [os.path.abspath(page)]
    assert pendingrows.finish(page) == "written"
    assert not os.path.exists(ctf)  # (older than the page file)
    assert pendingrows.marked(page) is None and _unchanged(proj, PAGE)


# ------------------------------------------------------------------------------------- (d)
def test_a_save_killed_in_its_register_is_rolled_back_then_finished(proj):
    from ctgedit.io import pendingrows
    from ctgedit.io import pgregister as PR

    page = os.path.join(proj, PAGE + ".ctg")
    PR.delete_page_rows(proj, PAGE)
    r = subprocess.run(
        [sys.executable, "-c", _SAVE, page],
        env=_env(CTG_TEST_EXIT_AT="in_register:3"),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 3, r.stderr
    assert _journals() and pendingrows.marked(page) is not None
    assert pendingrows.finish(page) == "written"
    assert not _journals()
    assert _unchanged(proj, PAGE) and not _index_matches_scan(proj)


# ------------------------------------------------------------------------------------- (e)
def test_the_rows_a_save_writes_are_the_rows_of_its_page_file(proj):
    from ctgedit.editing import EditableDocument
    from ctgedit.io import pgregister as PR

    for p in PAGES:
        PR.delete_page_rows(proj, p)
        doc = EditableDocument.open(os.path.join(proj, p + ".ctg"))
        doc.save()
        assert not doc.rows_pending, doc.save_warning
        assert _unchanged(proj, p), p
    assert not _index_matches_scan(proj)


# ------------------------------------------------------------------------------------- (f)
_HOLD_PAGE = r"""
import sys, time
from ctgedit.format import projectlock as PL
with PL.page(sys.argv[1], timeout=5):
    open(sys.argv[2], "w").close()
    time.sleep(float(sys.argv[3]))
"""


def _hold_page(page, tmp_path, seconds):
    ready = tmp_path / ("ready-%d" % time.monotonic_ns())
    p = subprocess.Popen([sys.executable, "-c", _HOLD_PAGE, page, str(ready), str(seconds)], env=_env())
    end = time.monotonic() + 60
    while not ready.exists():
        assert p.poll() is None and time.monotonic() < end
        time.sleep(0.05)
    return p


def test_the_page_lock_makes_a_second_writer_wait(proj, tmp_path, monkeypatch):
    from ctgedit.editing import EditableDocument, PageBusyError
    from ctgedit.format import projectlock as PL
    from ctgedit.io import pendingrows

    page = os.path.join(proj, PAGE + ".ctg")
    ctg = open(page, "rb").read()
    holder = _hold_page(page, tmp_path, 2.0)
    try:
        doc = EditableDocument.open(page)
        t0 = time.monotonic()
        doc.save()
        assert time.monotonic() - t0 > 0.5  # (it waited for the holder)
        assert open(page, "rb").read() != ctg
    finally:
        holder.wait(60)
    ctg = open(page, "rb").read()
    monkeypatch.setitem(PL._WAITS, "page", 0.3)
    holder = _hold_page(page, tmp_path, 120)
    try:
        doc = EditableDocument.open(page)
        with pytest.raises(PageBusyError):
            doc.save()
        assert open(page, "rb").read() == ctg and pendingrows.marked(page) is None
    finally:
        holder.kill()
        holder.wait(30)


# ------------------------------------------------------------------------------------- (g)
def test_every_write_of_a_save_holds_the_page_and_project_locks(proj, monkeypatch):
    from ctgedit.doc import history
    from ctgedit.editing import EditableDocument
    from ctgedit.format import fileio, journal
    from ctgedit.format import projectlock as PL
    from ctgedit.io import pgregister as PR

    page = os.path.join(proj, PAGE + ".ctg")
    PR.delete_page_rows(proj, PAGE)
    doc = EditableDocument.open(page)
    seen = []
    key = os.path.normcase(os.path.abspath(proj))

    def note(kind, path):
        if os.path.normcase(os.path.dirname(os.path.abspath(path))) == key:
            seen.append((kind, os.path.basename(path), PL.held("page", page), PL.held("project", proj)))

    real_wa, real_op, real_rm = fileio.write_atomic, journal.open_patch, os.remove

    def wa(path, data):
        note("write", path)
        return real_wa(path, data)

    def op(path):
        note("patch", path)
        return real_op(path)

    def rm(path, *a, **k):
        note("remove", path)
        return real_rm(path, *a, **k)

    monkeypatch.setattr(fileio, "write_atomic", wa)
    monkeypatch.setattr(history, "write_atomic", wa)
    monkeypatch.setattr(journal, "open_patch", op)
    monkeypatch.setattr(os, "remove", rm)
    doc.save()
    monkeypatch.undo()
    assert any(k == "write" and n == PAGE + ".ctg" for k, n, _p, _q in seen)
    assert any(k == "patch" for k, *_r in seen)
    assert all(pg for _k, _n, pg, _pr in seen), seen
    assert all(pr for k, _n, _pg, pr in seen if k == "patch"), seen


# ------------------------------------------------------------------------------------- (h)
_BACKUPS = r"""
import os, sys, time
from ctgedit.infra import backups
folder, k, go = sys.argv[1], int(sys.argv[2]), sys.argv[3]
while not os.path.exists(go):
    time.sleep(0.005)
for i in range(50):
    backups.add_entry(folder, "update-pages", {"page": "p%d_%d" % (k, i)})
"""


def test_three_processes_add_backup_entries_without_losing_one(tmp_path):
    folder = str(tmp_path / "backup")
    go = tmp_path / "go"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _BACKUPS, folder, str(k), str(go)],
            env=_env(),
            stderr=subprocess.PIPE,
            text=True,
        )
        for k in range(3)
    ]
    time.sleep(1.5)
    go.write_bytes(b"")
    for p in procs:
        _out, err = p.communicate(timeout=300)
        assert p.returncode == 0, err
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as fh:
        m = json.load(fh)
    pages = sorted(e["page"] for e in m["entries"])
    assert len(pages) == 150 and len(set(pages)) == 150
    assert not [f for f in os.listdir(folder) if f.endswith(".ctgedit-tmp")]
