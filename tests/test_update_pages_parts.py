"""Update Pages in parts."""

import datetime
import glob
import json
import os
import shutil
import struct


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


def tmp_install(tmp_path):
    """User\\{Root, A, B, Include} (+ an Elsewhere project in another install): Root (no
    .ctg) includes A, B, Include and Elsewhere; A includes B. A has 3 registered pages, B
    one page (no pages.DBF: every .ctg counts)."""
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


# ------------------------------------------------------------------------- the merge
def _page(project, name):
    return os.path.join(r"C:\Run", project, name + ".ctg")


def _rec(i, path, outcome, **kw):
    from ctgedit.io import updatepages as UP

    return UP.page_record(i, path, outcome, **kw)


def test_the_merge_folds_the_records_in_list_order():
    from ctgedit.workers import genie_worker as GW

    pages = [_page("A", "p%d" % i) for i in range(4)] + [_page("B", "q%d" % i) for i in range(3)]
    a = {
        "format": GW.FORMAT,
        "recovered": [{"label": "x", "restored": [], "skipped": []}],
        "records": [
            _rec(0, pages[0], "updated", checked=1, stale=1, instances=2),
            _rec(2, pages[2], "current", checked=1),
            _rec(4, pages[4], "deferred", rows_written=1),
            _rec(6, pages[6], "db_in_use", checked=1, stale=1, detail="busy"),
        ],
    }
    b = {
        "format": GW.FORMAT,
        "stopped": True,
        "records": [
            _rec(1, pages[1], "failed", checked=1, detail="bad"),
            _rec(3, pages[3], "updated", checked=1, stale=1, symbols=1, rows_pending=1, warning="w"),
        ],
    }
    p2 = {"format": GW.FORMAT, "records": [_rec(4, pages[4], "updated", checked=1, stale=1, composites=3)]}
    m = GW.merge_update_answers([a, b], pages, ["u1"], claimed={0, 1, 2, 3, 4, 6}, backup="BK", phase2=p2)
    assert m["updated"] == ["p0 (A)", "p3 (A)", "q0 (B)"]
    assert (m["instances"], m["symbols"], m["composites"]) == (2, 1, 3)
    assert m["pages"] == 6 and m["stale"] == 4
    assert m["failed"] == ["p1 (A): bad"] and m["db_in_use"] == ["q2 (B)"]
    assert m["rows_pending"] == ["p3 (A)"] and m["rows_written"] == ["q0 (B)"]
    assert m["warnings"] == ["p3 (A): w"]
    assert m["not_checked"] == 1  # q1: nobody took it
    assert m["unregistered"] == ["u1"] and m["backup"] == "BK"
    assert m["projects"] == ["A", "B"] and m["stopped"] and not m["errors"]
    assert m["recovered"] and m["recovered"][0]["label"] == "x"


def test_a_part_that_died_keeps_what_the_others_saved():
    from ctgedit.workers import genie_worker as GW

    pages = [_page("A", "p%d" % i) for i in range(4)]
    a = {
        "format": GW.FORMAT,
        "records": [
            _rec(0, pages[0], "updated", checked=1, stale=1),
            _rec(2, pages[2], "updated", checked=1, stale=1),
        ],
    }
    # (the other part died after its first page: its streamed record is what is known)
    b = {"error": "the helper gave no answer", "streamed": [_rec(1, pages[1], "updated", checked=1, stale=1)]}
    m = GW.merge_update_answers([a, b], pages, [], claimed={0, 1, 2, 3}, backup="BK")
    assert m["updated"] == ["p0", "p1", "p2"]  # (one project: plain names)
    assert m["failed"] == ["p3: the helper process ended while it was updating it"]
    assert m["errors"] == ["the helper gave no answer"] and m["backup"] == "BK"
    assert m["not_checked"] == 0 and m["projects"] == ["A"]
    # a page still deferred (phase 2 never ran: Cancel) was not checked
    a2 = dict(a, records=a["records"] + [_rec(3, pages[3], "deferred")])
    m = GW.merge_update_answers([a2, dict(b, error="")], pages, [], claimed={0, 1, 2, 3}, backup=None)
    assert m["not_checked"] == 1 and not m["failed"]


# --------------------------------------------------------------------- the page list
def test_the_run_page_list(tmp_path):
    from ctgedit.io import updatepages as UP

    x, y = tmp_path / "X", tmp_path / "Y"
    x.mkdir()
    y.mkdir()
    pages_dbf(str(x), ["b", "A"])
    for n in ("a", "B", "c"):
        (x / (n + ".ctg")).write_bytes(b"page")
    (y / "z.ctg").write_bytes(b"page")
    pages, unreg = UP.run_page_list([str(x), str(y), str(x)])
    assert [os.path.basename(p) for p in pages] == ["a.ctg", "B.ctg", "z.ctg"]
    assert unreg == ["c"]
    pages, unreg = UP.run_page_list([str(x), str(y)], skip=[str(x / "B.ctg")])
    assert [os.path.basename(p) for p in pages] == ["a.ctg", "z.ctg"] and unreg == ["c"]


def test_the_include_tree_of_a_root_without_pages(tmp_path):
    from ctgedit.io import updatepages as UP

    user = tmp_install(tmp_path)
    tree = UP.include_tree(os.path.join(user, "Root"), user)

    def shape(n):
        return [n.name, n.pages, [shape(c) for c in n.children]]

    assert shape(tree) == ["Root", 0, [["A", 3, [["B", 1, []]]]]]
    assert [n.name for n in tree.walk()] == ["Root", "A", "B"]
    # (the probe fallback: no MASTER.DBF, <user>\<name> folders)
    os.remove(os.path.join(user, "MASTER.DBF"))
    assert shape(UP.include_tree(os.path.join(user, "Root"))) == shape(tree)


# ------------------------------------------------------------------ the helper count
def test_the_helper_count(monkeypatch):
    from ctgedit.infra import workers
    from ctgedit.ui.prefs import settings as S

    st = S.app_settings()
    free = [10000]
    monkeypatch.setattr(workers, "free_memory_mb", lambda: free[0])
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    st.setValue("opt/update_processes", 0)
    assert S.update_parts(1000) == 4  # half the processors, at most 4
    free[0] = 1000
    assert S.update_parts(1000) == 2  # what the free memory holds
    free[0] = None
    monkeypatch.setattr(os, "cpu_count", lambda: 6)
    assert S.update_parts(1000) == 3
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    assert S.update_parts(1000) == 1
    assert S.update_parts(7) == 1 and S.update_parts(0) == 1
    free[0] = 400
    st.setValue("opt/update_processes", 16)
    assert S.update_parts(1000) == 16  # (an explicit count: not capped)
    assert S.update_parts(20) == 2  # (8 pages a helper at least)
    # a count chosen in the window (not the setting) is capped the same way
    st.setValue("opt/update_processes", 0)
    assert S.update_parts(20, 16) == 2 and S.update_parts(5, 4) == 1
    assert S.update_parts(1000, 16) == 16 and S.update_parts(1000, 99) == 16
    assert S.update_parts(1000, 0) == 1  # (Automatic: the free memory)
    st.setValue("opt/update_processes", 99)
    assert S.update_processes() == 16
    st.setValue("opt/update_processes", "garbled")
    assert S.update_processes() == 0
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    free[0] = 100000
    assert S.update_parts(1000) == 4


# ----------------------------------------------------------------------------- claims
def test_claims_give_each_page_to_one_part(fx_install, tmp_path, monkeypatch):
    """Four copies of FxGenies (genies of FxLib on FxMain's include chain)."""
    from ctgedit.io import updatepages as UP

    monkeypatch.setenv("CTG_LIB_PATH", fx_install.project("FxMain"))
    d = tmp_path / "scratch"
    d.mkdir()
    pages = []
    for n in ("a", "b", "c", "d"):
        shutil.copy(fx_install.page("FxGenies"), d / (n + ".ctg"))
        pages.append(str(d / (n + ".ctg")))
    claims = tmp_path / "claims"
    claims.mkdir()
    for i in (1, 3):  # another part has these
        (claims / str(i)).write_text("1")
    recs = tmp_path / "part.records"
    rep = UP.update_pages(None, pages=pages, claims=str(claims), records=str(recs))
    assert [r["i"] for r in rep.records] == [0, 2]
    assert [json.loads(x)["i"] for x in recs.read_text().splitlines()] == [0, 2]
    assert UP.claimed(str(claims)) == {0, 1, 2, 3}
    assert rep.pages == 2 and not rep.failed


# --------------------------------------------------------------------------- deferral
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
    raw = open(src, "rb").read()
    with open(dst, "wb") as fh:
        fh.write(raw.replace(target.encode("cp1252"), bumped.encode("cp1252"), 1))


def _store_snapshot(project):
    out = {}
    for p in glob.glob(os.path.join(project, "_CompositionCaches", "**", "*"), recursive=True):
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


def composite_source(fx_install, tmp_path):
    """FxComposites (eight composite genies, their caches in FxMain) with an FxLib genie
    placed beside them and saved: a page of composites and of a library genie (the test
    bumps that genie's stamp). Made in tmp_path, outside any project."""
    from ctgedit.editing import EditableDocument

    src = tmp_path / "src"
    src.mkdir()
    dst = str(src / "FxCompSource.ctg")
    shutil.copy(fx_install.page("FxComposites"), dst)
    doc = EditableDocument.open(dst)
    assert doc.place_library_element("fxgenie", "fx_plain", "ctm", (40, 700)) is not None
    doc.save()
    return dst


def composite_project(fx_install, tmp_path, name, pages, source):
    """A project folder holding copies of ``source`` (``pages`` = {name: bumped}) and FxLib's
    composite genie templates (so its composites are composed from the project)."""
    proj = tmp_path / name
    proj.mkdir()
    shutil.copytree(
        os.path.join(fx_install.project("FxLib"), "Composite Genies"), str(proj / "Composite Genies")
    )
    out = []
    for n, bump in pages.items():
        dst = str(proj / (n + ".ctg"))
        if bump:
            _bumped(source, dst)
        else:
            shutil.copy(source, dst)
        out.append(dst)
    return str(proj), out


def test_defer_mode_leaves_pages_that_must_compose_unopened(fx_install, tmp_path, monkeypatch):
    """P1: composites whose templates changed (deferred, never opened); P2: current
    templates and a genie changed since the save (updated in phase 1). FxMain's composition
    caches (the store) stay as they are."""
    from ctgedit import editing
    from ctgedit.format import composer_store as CS
    from ctgedit.io import updatepages as UP

    main = fx_install.project("FxMain")
    monkeypatch.setenv("CTG_LIB_PATH", main)
    source = composite_source(fx_install, tmp_path)
    before = _store_snapshot(main)
    assert before
    stale_proj, (p1,) = composite_project(fx_install, tmp_path, "Stale", {"P1": False}, source)
    stale_templates(stale_proj)
    _cur_proj, (p2,) = composite_project(fx_install, tmp_path, "Current", {"P2": True}, source)
    opened = []
    real_open = editing.EditableDocument.open.__func__

    def spy(cls, path, *a, **k):
        opened.append(os.path.basename(path))
        return real_open(cls, path, *a, **k)

    monkeypatch.setattr(editing.EditableDocument, "open", classmethod(spy))

    def no_compose(*a, **k):
        raise AssertionError("phase 1 composed")

    monkeypatch.setattr(CS, "compose", no_compose)
    p2_before = open(p2, "rb").read()
    rep = UP.update_pages(None, pages=[p1, p2], defer_composites=True)
    outcomes = {r["name"]: r["outcome"] for r in rep.records}
    assert outcomes == {"P1": "deferred", "P2": "updated"}, rep.records
    assert opened == ["P2.ctg"]
    assert open(p2, "rb").read() != p2_before
    assert rep.not_checked == 1 and rep.updated == ["P2"]
    assert _store_snapshot(main) == before
