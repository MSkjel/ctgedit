"""Locks between ctgedit processes."""

import contextlib
import glob
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_HOLD = r"""
import os, sys, time
from ctgedit.format import projectlock as PL
kind, path, ready = sys.argv[1], sys.argv[2], sys.argv[3]
with getattr(PL, kind)(path, timeout=5):
    open(ready, "w").close()
    time.sleep(120)
"""

_PROBE = r"""
import sys
from ctgedit.format import projectlock as PL
kind, path, t = sys.argv[1], sys.argv[2], float(sys.argv[3])
try:
    with getattr(PL, kind)(path, timeout=t):
        print("got")
except (PL.ProjectBusyError, PL.PageBusyError, PL.FileBusyError):
    print("busy")
"""


def _env():
    return dict(os.environ, PYTHONPATH=ROOT)


def _run(code, *args, timeout=120):
    return subprocess.run(
        [sys.executable, "-c", code] + [str(a) for a in args],
        env=_env(),
        timeout=timeout,
        capture_output=True,
        text=True,
    )


def _probe(kind, path, t=0.2) -> str:
    r = _run(_PROBE, kind, path, t)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _holder(kind, path, tmp_path):
    ready = tmp_path / ("ready-%d" % time.monotonic_ns())
    p = subprocess.Popen([sys.executable, "-c", _HOLD, kind, str(path), str(ready)], env=_env())
    end = time.monotonic() + 60
    while not ready.exists():
        assert p.poll() is None, "the holder ended"
        assert time.monotonic() < end, "the holder did not take the lock"
        time.sleep(0.05)
    return p


def _journals():
    from ctgedit.format import journal

    return sorted(glob.glob(os.path.join(journal.journal_dir(), "*.jnl")))


_SEQ = [0]


def _dead_journal(path, as_pid=None, started=1) -> str:
    """The journal a killed writer left behind."""
    import base64
    from ctgedit.format import journal

    d = journal.journal_dir()
    os.makedirs(d, exist_ok=True)
    data = open(path, "rb").read()
    n = len(data)
    pid = os.getppid() if as_pid is None else as_pid
    _SEQ[0] += 1
    jp = os.path.join(d, "%d-%d-%d.jnl" % (pid, 1000 + _SEQ[0], int(time.time() * 1000)))
    lines = [
        {
            "op": "begin",
            "pid": pid,
            "started": started,
            "label": "child update",
            "time": time.time(),
            "lock": 1,
        },
        {"op": "file", "id": 0, "path": os.path.abspath(path), "size": n},
        {"op": "w", "id": 0, "off": 5, "old": base64.b64encode(data[5:11]).decode(), "end": n},
        {"op": "w", "id": 0, "off": n, "old": "", "end": n + 8},
    ]
    with open(jp, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(x) + "\n" for x in lines))
    with open(path, "r+b") as fh:
        fh.seek(5)
        fh.write(b"child!")
        fh.seek(0, 2)
        fh.write(b"appended")
    return jp


def _share_none(path):
    """A handle on ``path`` no one else may open (a program holding a table): its close
    function."""
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
    h = k32.CreateFileW(path, 0x80000000, 0, None, 3, 0x80, None)  # read, share none
    assert h and h != ctypes.c_void_p(-1).value
    return lambda: k32.CloseHandle(h)


def _project(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    f = d / "t.DBF"
    f.write_bytes(bytes(range(64)))
    return d, f


# ---------------------------------------------------------------------------- (a) - (d)
def test_the_lock_is_re_entrant_in_a_thread(tmp_path):
    from ctgedit.format import projectlock as PL

    d = str(tmp_path)
    with PL.project(d):
        with PL.project(d + os.sep):
            assert PL.held("project", d) and PL.any_held(("project",))
        assert PL.held("project", d)
        with PL.page(os.path.join(d, "A.ctg")):
            assert PL.held("page", os.path.join(d, "a.CTG"))
    assert not PL.held("project", d) and not PL.any_held(("project", "page"))


def test_another_process_waits_and_gets_it_after_the_release(tmp_path):
    from ctgedit.format import projectlock as PL

    d = str(tmp_path)
    with PL.project(d):
        assert _probe("project", d) == "busy"
        assert _probe("page", os.path.join(d, "A.ctg")) == "got"  # (another lock)
    assert _probe("project", d) == "got"


def test_a_killed_holder_frees_the_lock(tmp_path):
    from ctgedit.format import projectlock as PL

    d = str(tmp_path)
    p = _holder("project", d, tmp_path)
    try:
        with pytest.raises(PL.ProjectBusyError):
            with PL.project(d, timeout=0.2):
                pass
    finally:
        p.kill()
        p.wait(30)
    with PL.project(d, timeout=5):  # (abandoned: taken)
        assert PL.held("project", d)


def test_open_patch_holds_the_project_lock_to_the_end_of_its_transaction(tmp_path):
    from ctgedit.format import journal

    d, f = _project(tmp_path, "A")
    fh = journal.open_patch(str(f))
    try:
        assert _probe("project", d) == "busy"
    finally:
        fh.close()
    assert _probe("project", d) == "got"
    with journal.transaction("test"):
        fh = journal.open_patch(str(f))
        fh.seek(3)
        fh.write(b"xy")
        fh.close()
        assert _probe("project", d) == "busy"  # (the transaction goes on)
    assert _probe("project", d) == "got"
    assert f.read_bytes()[3:5] == b"xy"


# ---------------------------------------------------------------------------- (e) - (g)
def test_the_first_holder_rolls_back_the_projects_dead_journals(tmp_path):
    from ctgedit.format import journal
    from ctgedit.format import projectlock as PL
    from ctgedit.infra.paths import process_started

    journal.take_notes()
    a, fa = _project(tmp_path, "A")
    b, fb = _project(tmp_path, "B")
    ja = _dead_journal(str(fa))
    jb = _dead_journal(str(fb))
    # a journal of this (live) process on another file of A: no transaction of it can
    # run while the lock is held, so it is stale and rolled back too
    other = a / "u.DBF"
    other.write_bytes(bytes(32))
    jo = _dead_journal(str(other), as_pid=os.getpid(), started=process_started())
    assert fa.read_bytes() != bytes(range(64))
    with PL.project(str(a)):
        assert fa.read_bytes() == bytes(range(64))  # before the holder writes
        assert other.read_bytes() == bytes(32)
        assert not os.path.exists(ja) and not os.path.exists(jo)
    assert os.path.exists(jb)
    assert fb.read_bytes() != bytes(range(64))
    notes = journal.take_notes()
    assert [n["label"] for n in notes] == ["child update"] * 2
    assert all(n["restored"] for n in notes)


def test_a_live_writers_journal_is_rolled_back_under_the_lock(tmp_path):
    """A journal is stale under the project's lock whatever its writer's pid says: a
    helper killed while its process still ends, or one whose rollback failed and that
    went on."""
    from ctgedit.format import journal
    from ctgedit.format import projectlock as PL
    from ctgedit.infra.paths import process_started

    a, fa = _project(tmp_path, "A")
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        started = process_started(p.pid)
        ja = _dead_journal(str(fa), as_pid=p.pid, started=started)
        with PL.project(str(a)):
            assert fa.read_bytes() == bytes(range(64)) and not os.path.exists(ja)
        assert p.poll() is None  # (its writer still runs)
    finally:
        p.kill()
        p.wait(30)
    journal.take_notes()


def test_an_empty_journal_is_told_by_its_file_name(tmp_path):
    """A journal its writer has created but not yet flushed has no begin line: the pid in
    its name says whether the writer lives."""
    from ctgedit.format import journal

    d = journal.journal_dir()
    os.makedirs(d, exist_ok=True)
    live = os.path.join(d, "%d-7-7.jnl" % os.getpid())
    gone = os.path.join(d, "%d-8-8.jnl" % 4194300)  # (no such process)
    for p in (live, gone):
        open(p, "w").close()
    try:
        journal.recover_stale()
        assert os.path.exists(live) and not os.path.exists(gone)
    finally:
        for p in (live, gone):
            with contextlib.suppress(OSError):
                os.remove(p)


def test_the_journal_of_this_threads_transaction_is_left(tmp_path):
    from ctgedit.format import journal

    a, fa = _project(tmp_path, "A")
    with journal.transaction("test") as txn:
        fh = journal.open_patch(str(fa))
        fh.seek(3)
        fh.write(b"xy")
        fh.close()
        assert journal.recover_project(str(a)) == []
        assert fa.read_bytes()[3:5] == b"xy" and os.path.exists(txn.path)
    assert fa.read_bytes()[3:5] == b"xy" and not _journals()


def test_a_journal_without_files_is_kept_while_its_writer_lives(tmp_path):
    """(the first lines of a journal another project's writer is starting)"""
    from ctgedit.format import journal
    from ctgedit.format import projectlock as PL
    from ctgedit.infra.paths import process_started

    a, _fa = _project(tmp_path, "A")
    d = journal.journal_dir()
    os.makedirs(d, exist_ok=True)
    live = os.path.join(d, "%d-1-1.jnl" % os.getpid())
    dead = os.path.join(d, "%d-2-2.jnl" % os.getppid())
    for path, pid, started in ((live, os.getpid(), process_started()), (dead, os.getppid(), 1)):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                json.dumps({"op": "begin", "pid": pid, "started": started, "label": "x", "time": time.time()})
                + "\n"
            )
    with PL.project(str(a)):
        pass
    assert os.path.exists(live) and not os.path.exists(dead)
    os.remove(live)


@pytest.mark.skipif(os.name != "nt", reason="Windows share modes")
def test_a_failed_rollback_is_undone_by_the_next_holder_of_the_same_process(tmp_path):
    """A rollback that cannot put one table back keeps its journal and lets the lock go;
    the next holder (this live process) rolls it back before it writes."""
    from ctgedit.format import journal
    from ctgedit.format import projectlock as PL

    a, fa = _project(tmp_path, "A")
    fb = a / "u.DBF"
    fb.write_bytes(bytes(32))
    close = [None]
    with pytest.raises(RuntimeError):
        with journal.transaction("test"):
            for f in (fa, fb):
                fh = journal.open_patch(str(f))
                fh.seek(2)
                fh.write(b"zz")
                fh.close()
            close[0] = _share_none(str(fb))  # (Studio grabs it)
            raise RuntimeError("the register failed")
    try:
        assert fa.read_bytes() == bytes(range(64))  # (put back)
        assert len(_journals()) == 1  # (kept: u.DBF was not)
    finally:
        close[0]()
    assert fb.read_bytes()[2:4] == b"zz"
    with PL.project(str(a)):
        assert fb.read_bytes() == bytes(32)
    assert not _journals()
    journal.take_notes()


@pytest.mark.skipif(os.name != "nt", reason="Windows share modes")
def test_a_replayed_journal_that_cannot_be_removed_is_never_replayed_again(tmp_path):
    from ctgedit.format import journal
    from ctgedit.format import projectlock as PL

    a, fa = _project(tmp_path, "A")
    ja = _dead_journal(str(fa))
    held = open(ja, "rb")  # (a scanner: no delete, read / write)
    try:
        with PL.project(str(a)):
            assert fa.read_bytes() == bytes(range(64))
        assert os.path.exists(ja)
    finally:
        held.close()
    # (a later writer, over the bytes the journal put back)
    fa.write_bytes(bytes(range(5)) + b"newer!" + bytes(range(64))[11:])
    with PL.project(str(a)):
        pass
    assert fa.read_bytes()[5:11] == b"newer!" and not os.path.exists(ja)
    journal.take_notes()


def test_recover_stale_leaves_a_journal_whose_lock_is_busy(tmp_path):
    from ctgedit.format import journal

    b, fb = _project(tmp_path, "B")
    p = _holder("project", str(b), tmp_path)
    # (made after the holder took the lock: taking it rolls back what is there)
    jb = _dead_journal(str(fb))
    try:
        t0 = time.monotonic()
        assert journal.recover_stale() == []
        assert time.monotonic() - t0 < 30
        assert os.path.exists(jb) and fb.read_bytes() != bytes(range(64))
    finally:
        p.kill()
        p.wait(30)
    notes = journal.recover_stale()
    assert notes and fb.read_bytes() == bytes(range(64)) and not os.path.exists(jb)


_RECOVER_AT_BARRIER = r"""
import json, os, sys, time
from ctgedit.format import journal
go = sys.argv[1]
while not os.path.exists(go):
    time.sleep(0.005)
print(json.dumps(journal.recover_stale()))
"""


def test_two_recoveries_at_once_replay_a_journal_once(tmp_path):
    a, fa = _project(tmp_path, "A")
    _dead_journal(str(fa))
    go = tmp_path / "go"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _RECOVER_AT_BARRIER, str(go)],
            env=_env(),
            stdout=subprocess.PIPE,
            text=True,
        )
        for _k in range(2)
    ]
    time.sleep(1.5)  # (both started, waiting)
    go.write_bytes(b"")
    outs = [json.loads(p.communicate(timeout=120)[0]) for p in procs]
    restored = [n for out in outs for n in out if n.get("restored")]
    assert len(restored) == 1, outs
    assert fa.read_bytes() == bytes(range(64)) and not _journals()


def test_a_journal_that_cannot_be_replayed_keeps_the_project_locked(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows share modes")
    import ctypes
    from ctypes import wintypes
    from ctgedit.format import journal
    from ctgedit.format import projectlock as PL

    a, fa = _project(tmp_path, "A")
    ja = _dead_journal(str(fa))
    dirty = fa.read_bytes()
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
    h = k32.CreateFileW(str(fa), 0x80000000, 0, None, 3, 0x80, None)  # read, share none
    assert h and h != ctypes.c_void_p(-1).value
    try:
        with pytest.raises(journal.JournalRecoveryError):
            with PL.project(str(a)):
                pass
        assert not PL.held("project", str(a))
    finally:
        k32.CloseHandle(h)
    assert fa.read_bytes() == dirty and os.path.exists(ja)
    with PL.project(str(a)):
        pass
    assert fa.read_bytes() == bytes(range(64)) and not os.path.exists(ja)


def test_the_cache_lock_is_never_waited_for_under_a_page_lock(tmp_path):
    from ctgedit.format import composer_store as CS
    from ctgedit.format import projectlock as PL

    with PL.page(str(tmp_path / "A.ctg")):
        with pytest.raises(RuntimeError, match="lock order"):
            with CS.cache_lock(str(tmp_path)):
                pass
    with CS.cache_lock(str(tmp_path)):  # (no lock held: fine)
        pass


def test_a_held_cache_lock_is_waited_for(tmp_path):
    """Another holder of _000.cache.lock: the wait ends with the lock once it is let go,
    or with the composer's message at its deadline."""
    import threading
    from ctgedit.format import composer_store as CS

    root = tmp_path / "_CompositionCaches"
    root.mkdir()
    fh = open(root / CS.LOCK_FILE, "wb")
    threading.Timer(0.6, fh.close).start()
    t0 = time.monotonic()
    with CS.cache_lock(str(tmp_path), wait=10):
        assert time.monotonic() - t0 >= 0.5
    fh = open(root / CS.LOCK_FILE, "wb")
    try:
        with pytest.raises(CS.CacheLockError):
            with CS.cache_lock(str(tmp_path), wait=0.3):
                pass
    finally:
        fh.close()


# ---------------------------------------------------------------------------- (i) heavy
_REGISTER_ROUNDS = r"""
import os, sys, time
from ctgedit.io import pgregister as PR
proj, page, go, rounds = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
while not os.path.exists(go):
    time.sleep(0.005)
ctg = os.path.join(proj, page + ".ctg")
for _k in range(rounds):
    PR.delete_page_rows(proj, page)
    PR.register_page(proj, page, ctg)
print("done")
"""


def _index_matches_scan(dbf_path, ndx_path) -> bool:
    from ctgedit.format.dbf import Dbf, Ndx

    with Dbf(dbf_path, writable=False) as d, Ndx(ndx_path, writable=False) as nx:
        ents = nx.entries()
        if sorted(r for _k, r in ents) != list(range(1, d.nrec + 1)):
            return False
        return all(nx.key_bytes(d.field(d.read(r - 1), nx.expr)) == k for k, r in ents)


@pytest.mark.skipif(os.environ.get("CTG_SKIP_HEAVY") == "1", reason="heavy")
def test_two_processes_register_into_one_project_at_once(fx_project, tmp_path):
    """Two pages of FxMain with dynamic objects, keyboard rows and language strings."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_page_lifecycle import _mini_project
    from ctgedit.format.dbf import Dbf
    from ctgedit.io import pgregister as PR

    EX = fx_project("FxMain")
    pages = ["FxAnim", "FxGenies"]
    proj = str(tmp_path / "FxMain")
    _mini_project(EX, proj, pages)
    for t in ("pglang",):
        for ext in (".DBF", ".NDX"):
            src = PR._find_file(EX, t + ext)
            if src:
                shutil.copy(src, os.path.join(proj, t + ext))

    def page_entries():
        with Dbf(PR._find_file(proj, "changes.DBF"), writable=False) as d:
            return sum(
                1
                for i in range(d.nrec)
                if d.read(i)[:1] != b"*"
                and d.field(d.read(i), "NAME").lower() == "pages"
                and d.field(d.read(i), "KEY").lower() in ("fxanim", "fxgenies")
            )

    before = page_entries()
    go = tmp_path / "go"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _REGISTER_ROUNDS, proj, p, str(go), "20"],
            env=_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for p in pages
    ]
    time.sleep(1.5)
    go.write_bytes(b"")
    for p in procs:
        out, err = p.communicate(timeout=600)
        assert p.returncode == 0 and out.strip() == "done", err
    for p in pages:
        rep = PR.register_page(proj, p, os.path.join(proj, p + ".ctg"), log_change=False)
        assert all(
            v.get("action") in ("unchanged", "absent", "missing_table") for v in rep["tables"].values()
        ), rep["tables"]
    for fn in os.listdir(proj):
        if fn.lower().endswith(".ndx"):
            dbf = PR._find_file(proj, fn[:-4] + ".DBF")
            assert dbf and _index_matches_scan(dbf, os.path.join(proj, fn)), fn
    lp = PR._find_file(proj, "pglang.DBF")
    if lp:
        with Dbf(lp, writable=False) as d:
            natives = [d.field(d.read(i), "NATIVE") for i in range(d.nrec) if d.read(i)[:1] != b"*"]
        assert len(natives) == len(set(natives))
    assert page_entries() - before == 40
    assert not _journals()
