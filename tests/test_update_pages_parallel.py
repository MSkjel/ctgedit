"""Update Pages in parallel helper processes."""

import collections
import glob
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtWidgets  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fxinstall  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: the committed FxMain (only read here, to choose the pages)
FX_MAIN = os.path.join(fxinstall.FX_INSTALL, "User", "FxMain")
#: the page tables the register writes
TABLES = (
    "pgkey",
    "pgdynobj",
    "pgtouch",
    "pgbutton",
    "pgalmind",
    "pgadv",
    "pgansym",
    "pgnum",
    "pgstr",
    "pgsym",
    "activex",
)

pytestmark = [pytest.mark.skipif(os.environ.get("CTG_SKIP_HEAVY") == "1", reason="heavy")]


def _pages_with_genies(n=16):
    """``n`` FxMain pages that place a genie of a library (their stamp can be bumped); not
    FxMissing (its genies' library is gone)."""
    from ctgedit.format.pagesummary import read_link_names

    out = []
    for p in sorted(glob.glob(os.path.join(FX_MAIN, "*.ctg")), key=str.lower):
        name = os.path.splitext(os.path.basename(p))[0]
        if name.startswith("!") or name == "FxMissing":
            continue
        try:
            names = read_link_names(p)
        except Exception:  # noqa: BLE001 -- swallow-ok: a page the test does not use
            continue
        if any(".<ctm#" in x and "_compositioncaches" not in x.lower() for x in names):
            out.append(name)
        if len(out) == n:
            break
    return out


PAGES = _pages_with_genies()
BUMPED = PAGES[:10]
_STALE: list = []


def _bumped(src, dst):
    """A copy of page ``src`` whose first genie link-table stamp is changed (the genie
    then reads as changed since the page was saved)."""
    from ctgedit.format import links
    from ctgedit.format.page import parse_ctg

    pb = parse_ctg(src).page
    _area, entries = links.parse_link_table(pb.link_table_raw)
    target = next(n for _g, n in entries if ".<ctm#" in n and "_compositioncaches" not in n.lower())
    stamp = target.rsplit("#", 1)[1]
    bumped = target[: -len(stamp)] + ("%08x" % (int(stamp, 16) ^ 1))
    with open(src, "rb") as fh:
        raw = fh.read()
    with open(dst, "wb") as fh:
        fh.write(raw.replace(target.encode("cp1252"), bumped.encode("cp1252"), 1))


def mini(fx, name, pages=PAGES):
    """A copy of the fx_install copy's FxMain named ``name`` in its User folder, holding
    ``pages`` (no other page) with every table, library and file of the project. The pages
    the fixture keeps unregistered are registered here by a first save (a pages.DBF row
    and their pg rows: Update Pages takes a project's registered pages only)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages as UP

    src = fx.project("FxMain")
    proj = os.path.join(fx.user, name)
    keep = {p.lower() + ext for p in pages for ext in (".ctg", ".ctf")}

    def ignore(folder, names):
        if os.path.normcase(folder) != os.path.normcase(src):
            return []
        return [n for n in names if n.lower().endswith((".ctg", ".ctf")) and n.lower() not in keep]

    shutil.copytree(src, proj, ignore=ignore)
    reg = {n.lower() for n in UP.registered_names(proj) or ()}
    for p in pages:
        if p.lower() not in reg:
            doc = EditableDocument.open(os.path.join(proj, p + ".ctg"))
            doc._creating = True  # (the save of a new page registers it)
            doc.save()
    assert {p.lower() for p in pages} <= {n.lower() for n in UP.registered_names(proj)}
    stale = _bad_indexes(proj)
    assert {f.lower() for f in stale} <= {"pages.ndx"}, stale  # the register's are kept
    _STALE_AFTER_MINI[os.path.normcase(proj)] = stale
    return proj


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


#: {project folder: the .NDX files mini() leaves stale}: see _index_matches_scan
_STALE_AFTER_MINI: dict = {}


def _bad_indexes(proj) -> list:
    """The project's .NDX files whose entries are not their table's records (recnos 1..n,
    each key its record's field)."""
    from ctgedit.format.dbf import Dbf, Ndx
    from ctgedit.io import pgregister as PR

    bad = []
    for fn in os.listdir(proj):
        low = fn.lower()
        if not low.endswith(".ndx"):
            continue
        dp = PR._find_file(proj, fn[:-4] + ".DBF")
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


def _index_matches_scan(proj) -> list:
    """The .NDX files a run left stale: every .NDX of the project is checked, apart from
    the ones mini() itself left stale. (mini() registers the fixture's unregistered pages
    through ProjectTables.add_page_record, which appends pages.DBF rows without updating
    pages.NDX; whether CtDraw keeps that index has not been checked.)"""
    skip = {f.lower() for f in _STALE_AFTER_MINI.get(os.path.normcase(proj), ())}
    return [f for f in _bad_indexes(proj) if f.lower() not in skip]


def stale_copy(fx, sub):
    """A mini FxMain project (``FxRun<sub>``) of PAGES, the first 10 with a genie stamp
    bumped. stale() = the pages Update Pages then updates (a run in this process on the
    first copy made: the same in every copy)."""
    from ctgedit.io import updatepages as UP

    proj = mini(fx, "FxRun" + sub)
    src = fx.project("FxMain")
    for p in BUMPED:
        path = os.path.join(proj, p + ".ctg")
        _bumped(os.path.join(src, p + ".ctg"), path + ".new")
        os.replace(path + ".new", path)
    if not _STALE:
        ref = mini(fx, "FxReference")
        for p in BUMPED:
            shutil.copy(os.path.join(proj, p + ".ctg"), os.path.join(ref, p + ".ctg"))
        _STALE.extend(UP.update_pages(ref).updated)
        assert len(set(_STALE) & set(BUMPED)) >= 8, _STALE
    return proj


@pytest.fixture(autouse=True)
def _fresh_stale():
    """stale() is found again for each test (each test has its own fx_install copy)."""
    _STALE.clear()
    yield
    _STALE.clear()


def stale() -> list:
    return list(_STALE)


def run_update(dirs, parts, backup=None, on_poll=None, timeout=900):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    job = {
        "templates": True,
        "genies": True,
        "symbols": True,
        "page_properties": False,
        "backup": backup,
        "builder_open": [],
    }
    run = UpdatePagesRun(None, list(dirs), job, parts=parts)
    got = []
    run.finished.connect(got.append)
    assert run.start()
    end = time.monotonic() + timeout
    while not got and time.monotonic() < end:
        app.processEvents()
        if on_poll is not None:
            on_poll(run)
        time.sleep(0.05)
    assert got, "no answer"
    assert UpdatePagesRun.active is None
    return got[0], run


def table_rows(proj):
    """{(table, page): [the page's live rows in record order]}."""
    from ctgedit.format.dbf import Dbf
    from ctgedit.io import pgregister as PR

    out = {}
    for t in TABLES:
        p = PR._find_file(proj, t + ".DBF")
        if not p:
            continue
        with Dbf(p, writable=False) as d:
            if "PAGE" not in d.fmap:
                continue
            for i in range(d.nrec):
                r = d.read(i)
                if r[:1] != b"*":
                    row = d.decode(r)
                    out.setdefault((t, row.get("PAGE", "").lower()), []).append(row)
    return out


def natives(proj):
    from ctgedit.format.dbf import Dbf
    from ctgedit.io import pgregister as PR

    p = PR._find_file(proj, "pglang.DBF")
    if not p:
        return set()
    with Dbf(p, writable=False) as d:
        return {d.field(d.read(i), "NATIVE") for i in range(d.nrec) if d.read(i)[:1] != b"*"}


def changes(proj, src):
    """The change log's (NAME, KEY) multiset of the entries the run added (the copy holds
    the whole log of ``src``, FxMain, first); each 'pages' entry's RECORD_NO names a live
    pages.DBF row of that page."""
    from ctgedit.format.dbf import Dbf
    from ctgedit.io import pgregister as PR

    with Dbf(PR._find_file(src, "changes.DBF"), writable=False) as d:
        since = d.nrec
    with Dbf(PR._find_file(proj, "pages.DBF"), writable=False) as pg:
        names = {
            i + 1: pg.field(pg.read(i), "NAME").lower() for i in range(pg.nrec) if pg.read(i)[:1] != b"*"
        }
    out = collections.Counter()
    with Dbf(PR._find_file(proj, "changes.DBF"), writable=False) as d:
        for i in range(since, d.nrec):
            r = d.read(i)
            if r[:1] == b"*":
                continue
            name, key = d.field(r, "NAME").lower(), d.field(r, "KEY").lower()
            if name == "pages":
                assert names.get(int(d.field(r, "RECORD_NO") or 0)) == key, (name, key)
            out[(name, key)] += 1
    return out


def payloads(path):
    from ctgedit.format import links, writer
    from ctgedit.format.page import parse_ctg

    pb = parse_ctg(path).page
    t = links.parse_link_table(pb.link_table_raw) if pb.link_table_raw else None
    return (
        [writer.object_payload(o) for o in pb.objects],
        sorted(links.strip_stamp(n) for _g, n in (t[1] if t else [])),
    )


def leftovers(proj):
    from ctgedit.format import journal
    from ctgedit.infra.paths import data_dir
    from ctgedit.io import pendingrows

    return (
        glob.glob(os.path.join(journal.journal_dir(), "*.jnl"))
        + glob.glob(os.path.join(pendingrows.folder(), "*.json"))
        + glob.glob(os.path.join(data_dir("updatepages"), "*"))
        + glob.glob(os.path.join(proj, "*.ctgedit-tmp"))
    )


# ---------------------------------------------------------------------------------- (a)
def test_two_helpers_give_what_one_gives(tmp_path, fx_install):
    from ctgedit.infra import backups

    assert len(PAGES) == 16
    one, two = stale_copy(fx_install, "One"), stale_copy(fx_install, "Two")
    b1, b2 = str(tmp_path / "bk1"), str(tmp_path / "bk2")
    r1, run1 = run_update([one], 1, backup=b1)
    r2, run2 = run_update([two], 2, backup=b2)
    assert run1.parts == 1 and run2.parts == 2
    assert sorted(r1["updated"]) == sorted(stale()) and r2["updated"] == r1["updated"]
    for k in ("pages", "stale", "instances", "symbols", "templates", "not_checked"):
        assert r1[k] == r2[k], k
    assert not r1["failed"] and not r2["failed"] and not r2["errors"]
    assert r1["projects"] == ["FxRunOne"] and r2["projects"] == ["FxRunTwo"]
    assert table_rows(one) == table_rows(two)
    assert natives(one) == natives(two)
    src = fx_install.project("FxMain")
    assert changes(one, src) == changes(two, src)
    with (
        open(os.path.join(one, "PROJECT.CIT"), "rb") as f1,
        open(os.path.join(two, "PROJECT.CIT"), "rb") as f2,
    ):
        assert f1.read() == f2.read()  # (the same CompState)
    assert not _index_matches_scan(one) and not _index_matches_scan(two)
    for p in PAGES:
        assert payloads(os.path.join(one, p + ".ctg")) == payloads(os.path.join(two, p + ".ctg")), p
    for bk in (b1, b2):
        entries = [e["page"] for e in backups.read_manifest(bk)["entries"]]
        assert sorted(entries) == sorted(stale())
    assert not leftovers(one) and not leftovers(two)


# ---------------------------------------------------------------------------------- (b)
def test_cancel_stops_every_helper_and_leaves_rows_with_their_pages(fx_install):
    from ctgedit.format import journal

    proj = stale_copy(fx_install, "Cancel")
    asked = []

    def poll(run):
        if asked or not run._folder:
            return
        for f in glob.glob(os.path.join(run._folder, "*.records")):
            with open(f, encoding="utf-8") as fh:
                if fh.read().count("\n"):
                    asked.append(True)
                    run.stop()
                    return

    res, _run = run_update([proj], 2, on_poll=poll)
    assert asked and res["stopped"]
    assert len(res["updated"]) < len(stale()) and res["not_checked"] > 0
    for p in res["updated"]:
        assert _unchanged(proj, p), p
    assert not glob.glob(os.path.join(journal.journal_dir(), "*.jnl"))


# ---------------------------------------------------------------------------------- (c)
def test_a_killed_helper_leaves_its_page_failed_and_the_other_does_the_rest(fx_install, monkeypatch):
    from ctgedit.io import pendingrows

    proj = stale_copy(fx_install, "Killed")
    monkeypatch.setenv("CTG_TEST_EXIT_AT", "after_records:1:0")
    res, _run = run_update([proj], 2)
    monkeypatch.delenv("CTG_TEST_EXIT_AT")
    ended = [f for f in res["failed"] if f.endswith(": the helper process ended while it was updating it")]
    assert len(ended) == 1 and res["errors"]
    lost = ended[0].split(":")[0]
    assert sorted(res["updated"] + [lost]) == sorted(stale()) or sorted(res["updated"]) == sorted(
        s for s in stale() if s != lost
    )
    assert res["not_checked"] == 0
    for m in pendingrows.listing([proj]):
        pendingrows.finish(m["page"])
    for p in res["updated"]:
        assert _unchanged(proj, p), p
    assert not _index_matches_scan(proj)


# ---------------------------------------------------------------------------------- (d)
def _store_snapshot(fx):
    """The composition caches of FxMain (the fixture's own store), file by file."""
    out = {}
    for p in glob.glob(os.path.join(fx.project("FxMain"), "_CompositionCaches", "**", "*"), recursive=True):
        if os.path.isfile(p):
            st = os.stat(p)
            out[p] = (st.st_size, st.st_mtime_ns)
    return out


def stale_templates(project):
    """Every composite genie template of ``project`` changed (its digest differs): the
    composites placed from it must be composed again."""
    for p in glob.glob(os.path.join(project, "Composite Genies", "*.xml")):
        with open(p, "rb") as fh:
            data = fh.read()
        with open(p, "wb") as fh:
            fh.write(data.replace(b'Thumbnail="', b'Thumbnail="x', 1))


def composite_project(fx, name, pages):
    """A project folder in the fx_install copy."""
    proj = fx.new_project(name, includes=("FxMain", "FxLib", "FxStyle"), tables=False)
    shutil.copytree(
        os.path.join(fx.project("FxLib"), "Composite Genies"), os.path.join(proj, "Composite Genies")
    )
    src = fx.page("FxComposites")
    out = []
    for n, bump in pages.items():
        dst = os.path.join(proj, n + ".ctg")
        if bump:
            _bumped(src, dst)
        else:
            shutil.copy(src, dst)
        out.append(dst)
    return proj, out


def test_composites_are_composed_by_the_one_helper_at_the_end(tmp_path, fx_install, monkeypatch):
    from ctgedit.ui.prefs import settings as S
    from ctgedit.ui.jobs.helper_job import HelperJob, HelperJobs

    # (3 pages: two helpers only with a minimum of one page a helper)
    S.app_settings().setValue("opt/update_pages_per_helper", 1)
    before = _store_snapshot(fx_install)
    results = {}
    for parts in (1, 2):
        proj, pages = composite_project(
            fx_install, "FxComp%d" % parts, {"P1": False, "P2": False, "P3": False}
        )
        stale_templates(proj)
        log = tmp_path / ("compose-%d.log" % parts)
        monkeypatch.setenv("CTG_TEST_COMPOSE_LOG", str(log))
        seen = {"phase1": False, "at_phase2": None}

        def composes(log=log):
            return len(log.read_text().split()) if log.exists() else 0

        def poll(run, seen=seen, composes=composes):
            h = run._helper
            if isinstance(h, HelperJobs):
                seen["phase1"] = True
            elif isinstance(h, HelperJob) and seen["phase1"] and seen["at_phase2"] is None:
                # (the composite pass just started: every phase 1 helper has ended, and
                # the pass's own helper is still starting)
                seen["at_phase2"] = composes()

        res, run = run_update([proj], parts, on_poll=poll)
        results[parts] = (res, {p: payloads(p) for p in pages}, composes(), seen)
    one, two = results[1], results[2]
    assert one[0]["updated"] == two[0]["updated"] == ["P1", "P2", "P3"]
    assert one[0]["composites"] == two[0]["composites"] > 0
    assert not two[0]["failed"] and not two[0]["errors"]
    assert one[2] and two[2]
    assert two[3]["phase1"] and two[3]["at_phase2"] == 0  # (no compose in phase 1)
    assert [v for _k, v in sorted(one[1].items())] == [v for _k, v in sorted(two[1].items())]
    assert _store_snapshot(fx_install) == before


# ---------------------------------------------------------------------------------- (e)
_CLOSED = r"""
import json, os, sys, time, types
from ctgedit.workers import genie_worker as GW
from ctgedit.io import genieusage as GU
pages, go = json.loads(sys.argv[1]), sys.argv[2]
GU.element_users = lambda lib, elem: types.SimpleNamespace(users=[
    types.SimpleNamespace(ref=types.SimpleNamespace(path=p, kind="page"), needs_update=True)
    for p in pages], problems=[], unreadable=[])
GW.helper_waits()
while not os.path.exists(go):
    time.sleep(0.005)
print(json.dumps(GW.update_closed_pages("x.ctm", "x")))
"""


def test_the_closed_pages_update_beside_a_run_updates_each_page_once(tmp_path, fx_install):
    proj = stale_copy(fx_install, "Both")
    paths = [os.path.join(proj, p + ".ctg") for p in stale()]
    go = tmp_path / "go"
    closed = subprocess.Popen(
        [sys.executable, "-c", _CLOSED, json.dumps(paths), str(go)],
        env=dict(os.environ, PYTHONPATH=ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    started = []

    def poll(run):
        if not started:
            started.append(True)
            go.write_bytes(b"")

    res, _run = run_update([proj], 2, on_poll=poll)
    out, err = closed.communicate(timeout=900)
    assert closed.returncode == 0, err
    other = json.loads(out)
    a, b = set(res["updated"]), set(other["updated"])
    assert not (a & b), (a, b)  # (each page updated once)
    left = set(res["changed_meanwhile"]) | set(other.get("changed_meanwhile") or ())
    assert a | b | left == set(stale()), (a, b, left)
    assert not res["failed"] and not other["failed"], (res["failed"], other["failed"])
    for p in PAGES:
        assert _unchanged(proj, p), p
    assert not _index_matches_scan(proj)
