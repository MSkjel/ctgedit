"""Page lifecycle: Save As, New, delete and rename."""

import os
import shutil
import struct

import pytest

from ctgedit.format.dbf import Dbf, Ndx, create_ndx
from ctgedit.format.page import parse_ctg
from ctgedit.io import pgregister as PR
from qtkit import app as _app

MASTER = "FxMaster"
HW = "FxAnim"  # has page-wide (blank-AN) keyboard rows
#: a page with a linked template and a genie (FxTplPump: fxgenie.fx_pump)
TPL_PAGE = "FxTplLinked"
#: a genie placed once on FxGenies (FxValue)
AGITATOR = "fxgenie.fx_value.<ctm"


@pytest.fixture
def SA(fx_project):
    """FxMain of the fake install copy (the project the trimmed copies come from)."""
    return fx_project("FxMain")


@pytest.fixture
def EX(fx_project):
    """FxMain of the fake install copy (the project pages are copied from)."""
    return fx_project("FxMain")


def _mini_project(
    project, dst, pages, tables=("pgkey", "pgdynobj", "pgtouch", "pgbutton", "pgalmind", "activex")
):
    """A copy of ``project`` holding ``pages`` (their .ctg) and their rows in
    ``tables`` (+ fresh PAGE indexes); pages.DBF / changes.DBF / PROJECT.CIT
    copied whole."""
    os.makedirs(dst, exist_ok=True)
    for f in ("pages.DBF", "changes.DBF", "PROJECT.CIT"):
        src = PR._find_file(project, f)
        if src:
            shutil.copy(src, os.path.join(dst, f))
    for t in tables:
        src = PR._find_file(project, t + ".DBF")
        if src is None:
            continue
        d = Dbf(src, writable=False)
        n = PR._open_ndx(project, t)
        recs = [r for p in pages for _i, r in sorted(PR._page_records(d, n, "PAGE", p))]
        if n is not None:
            n.close()
        with open(src, "rb") as fh:
            head = bytearray(fh.read(d.hlen))
        struct.pack_into("<I", head, 4, len(recs))
        out = os.path.join(dst, t + ".DBF")
        with open(out, "wb") as fh:
            fh.write(bytes(head) + b"".join(recs) + b"\x1a")
        create_ndx(os.path.join(dst, t + ".NDX"), "PAGE", d.fmap["PAGE"][1])
        d.close()
        dd = Dbf(out)
        with Ndx(os.path.join(dst, t + ".NDX")) as nx:
            for i in range(dd.nrec):
                nx.insert(dd.field(dd.read(i), "PAGE"), i + 1)
        dd.close()
    for p in pages:
        shutil.copy(os.path.join(project, p + ".ctg"), os.path.join(dst, p + ".ctg"))
    return dst


def _add_page_keys(proj, page, from_page):
    """Give ``page`` of the trimmed copy ``proj`` the page-wide (blank-AN) pgkey rows of
    ``from_page`` (the fixture has no page with both page-wide and object keyboard rows:
    FxAnim has two page-wide ones, FxButtons twelve object ones). Returns the rows added."""
    with Dbf(os.path.join(proj, "pgkey.DBF")) as d:
        rows = [d.decode(d.read(i)) for i in range(d.nrec)]
        add = [
            dict(r, PAGE=page)
            for r in rows
            if r["PAGE"].strip().lower() == from_page.lower() and not r["AN"].strip()
        ]
        first = d.nrec
        d.append(d.encode_many(add))
    with Ndx(os.path.join(proj, "pgkey.NDX")) as nx:
        for k in range(len(add)):
            nx.insert(page, first + k + 1)
    return add


def _live_rows(project_dir, table, page):
    """Live rows of ``page`` read with dbfread (an independent DBF reader)."""
    from dbfread import DBF

    path = PR._find_file(project_dir, table + ".DBF")
    if path is None:
        return []
    return [
        dict(r)
        for r in DBF(path, encoding="cp1252", char_decode_errors="replace")
        if str(r.get("PAGE", "")).strip().lower() == page.lower()
    ]


def _identity(path):
    pc = parse_ctg(path)
    return pc.page.page_identity, pc.attributes.page_identity, pc.container


# --------------------------------------------------------------------------- Save As
def test_save_as_copies_the_page_record_and_gets_a_new_identity(SA, tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project

    proj = _mini_project(SA, str(tmp_path / "SA"), [MASTER])
    src = Project(proj).page_record(MASTER)
    assert src["READY_FUNC"] == "FxSet(FX_MODE, 1)" and src["CON_TYPE"] == "FxMaster"
    doc = EditableDocument.open(os.path.join(proj, MASTER + ".ctg"))
    out = os.path.join(proj, "MyMaster.ctg")
    doc.save(out)
    row = Project(proj).page_record("MyMaster")
    assert row is not None
    for k, v in src.items():
        if k != "NAME":
            assert row[k] == v, k
    assert row["TITLE"] == "@(Fx Start)"
    assert row["IGNOREVQ"] == "1" and row["PARENT"] == "FxShapes"
    assert Project(proj).page_record(MASTER) == src  # the source row is untouched
    # a NEW page: its own identity (the .ctF staleness key), the stamp in the head
    a_page, a_attr, _c = _identity(os.path.join(proj, MASTER + ".ctg"))
    b_page, b_attr, b_cont = _identity(out)
    assert b_page != a_page and b_page[4:] != a_page[4:]
    assert b_attr == b_page
    assert struct.unpack_from("<I", b_page, 0)[0] == b_cont.ts_modified
    # the register ran for the new name: changes.DBF logs its pages.DBF record
    ch = Dbf(os.path.join(proj, "changes.DBF"), writable=False)
    logged = [ch.decode(ch.read(i)) for i in range(ch.nrec)]
    ch.close()
    with Dbf(os.path.join(proj, "pages.DBF"), writable=False) as pg:
        ((recno, _r),) = pg.find("NAME", "MyMaster")
    assert {"NAME": "pages", "KEY": "MyMaster", "RECORD_NO": str(recno + 1)} in logged
    # the copy's link table lists everything the page uses (nothing pruned here)
    assert (
        parse_ctg(out).page.link_table_raw
        == parse_ctg(os.path.join(proj, MASTER + ".ctg")).page.link_table_raw
    )


def test_save_as_copies_the_page_wide_keyboard_rows(SA, tmp_path):
    """FxButtons (twelve object keyboard rows) with FxAnim's two page-wide rows added."""
    from ctgedit.editing import EditableDocument

    keys = "FxButtons"
    proj = _mini_project(SA, str(tmp_path / "SA"), [keys, HW])
    assert len(_add_page_keys(proj, keys, HW)) == 2
    blank = [r for r in _live_rows(proj, "pgkey", keys) if not r["AN"].strip()]
    assert len(blank) == 2
    assert len([r for r in _live_rows(proj, "pgkey", keys) if r["AN"].strip()]) == 12
    doc = EditableDocument.open(os.path.join(proj, keys + ".ctg"))
    doc.save(os.path.join(proj, "MyHardware.ctg"))
    rows = _live_rows(proj, "pgkey", "MyHardware")
    copied = [r for r in rows if not r["AN"].strip()]
    assert [(r["KEY_SEQ"], r["CMD"]) for r in copied] == [(r["KEY_SEQ"], r["CMD"]) for r in blank]
    # the object keyboard rows come from the register, as for any save
    gen = PR.page_rows(os.path.join(proj, "MyHardware.ctg"), "MyHardware")["pgkey"]
    assert gen and len([r for r in rows if r["AN"].strip()]) == len(gen)
    # Save As over the (now registered) copy again: the rows are not doubled
    doc2 = EditableDocument.open(os.path.join(proj, keys + ".ctg"))
    doc2.save(os.path.join(proj, "MyHardware.ctg"))
    again = [r for r in _live_rows(proj, "pgkey", "MyHardware") if not r["AN"].strip()]
    assert len(again) == 2
    from ctgedit.io.project import Project

    live = [r for r in Project(proj).page_rows() if r["NAME"].lower() == "myhardware"]
    assert len(live) == 1
    _index_consistent(proj, "pgkey")


def _index_consistent(dst, table):
    d = Dbf(os.path.join(dst, table + ".DBF"), writable=False)
    with Ndx(os.path.join(dst, table + ".NDX")) as n:
        ents = n.entries()
        assert sorted(r for _k, r in ents) == list(range(1, d.nrec + 1))
    assert os.path.getsize(os.path.join(dst, table + ".DBF")) == d.hlen + d.nrec * d.rlen + 1
    d.close()


def test_save_as_over_a_registered_page_rewrites_its_row(SA, tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project

    proj = _mini_project(SA, str(tmp_path / "SA"), [MASTER, HW])
    master_keys = [(r["KEY_SEQ"], r["CMD"]) for r in _live_rows(proj, "pgkey", MASTER) if not r["AN"].strip()]
    assert master_keys == [("LBUTTON_UP", "FxSet(FX_MODE, 0);\r\nFxSet(FX_MODE, 1);")]
    doc = EditableDocument.open(os.path.join(proj, MASTER + ".ctg"))
    doc.save(os.path.join(proj, HW + ".ctg"))  # Replace
    rows = [r for r in Project(proj).page_rows() if r["NAME"].lower() == HW.lower()]
    assert len(rows) == 1 and rows[0]["CON_TYPE"] == "FxMaster"
    assert rows[0]["READY_FUNC"] == "FxSet(FX_MODE, 1)"
    # the replaced page's page-wide keys are the source's now (its one, not HW's two)
    assert [
        (r["KEY_SEQ"], r["CMD"]) for r in _live_rows(proj, "pgkey", HW) if not r["AN"].strip()
    ] == master_keys


def test_save_as_outside_a_project_writes_no_record(EX, tmp_path):
    """Save As of a project's page into a scratch folder: nothing is registered
    and the source project is never written."""
    from ctgedit.editing import EditableDocument

    before = os.stat(os.path.join(EX, "pages.DBF")).st_mtime_ns
    doc = EditableDocument.open(os.path.join(EX, "FxShapes.ctg"))
    doc.save(str(tmp_path / "Copy.ctg"))
    assert not getattr(doc, "save_warning", "")
    assert os.stat(os.path.join(EX, "pages.DBF")).st_mtime_ns == before


# --------------------------------------------------------------------------- New page
def test_new_page_is_registered_with_its_own_identity_and_links(EX, tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP
    from ctgedit.format import links
    from ctgedit.io.project import Project

    proj = str(tmp_path / "EX")
    os.makedirs(proj)
    for f in ("pages.DBF", "changes.DBF", "PROJECT.CIT"):
        shutil.copy(os.path.join(EX, f), os.path.join(proj, f))
    shutil.copy(os.path.join(EX, TPL_PAGE + ".ctg"), os.path.join(proj, "Mixer.ctg"))
    doc = EditableDocument.new_blank(os.path.join(proj, "Mixer.ctg"), os.path.join(proj, "Blank1.ctg"))
    assert not doc.dirty
    row = Project(proj).page_record("Blank1")
    assert row is not None and row["TITLE"] == "Blank1"
    src_id, _a, _c = _identity(os.path.join(proj, "Mixer.ctg"))
    new_id, new_attr, cont = _identity(os.path.join(proj, "Blank1.ctg"))
    assert new_id != src_id and new_attr == new_id
    assert cont.ts_created == cont.ts_modified == struct.unpack_from("<I", new_id, 0)[0]
    # the link table keeps only what the kept template uses
    src_pb = parse_ctg(os.path.join(proj, "Mixer.ctg")).page
    new_pb = parse_ctg(os.path.join(proj, "Blank1.ctg")).page
    src_names = [n for _g, n in links.parse_link_table(src_pb.link_table_raw)[1]]
    new_ents = links.parse_link_table(new_pb.link_table_raw)[1]
    new_names = [n for _g, n in new_ents]
    assert new_names and len(new_names) < len(src_names)
    assert all(n in src_names for n in new_names)
    assert any(n.lower().startswith("fx_style.normal@xga.") for n in new_names)  # template
    assert any(n.lower().startswith("fxgenie.") for n in src_names)
    assert not any(n.lower().startswith("fxgenie.") for n in new_names)  # the page's genie
    assert GP.used_link_entries(new_pb.objects, new_ents) == new_ents
    ch = Dbf(os.path.join(proj, "changes.DBF"), writable=False)
    last = ch.decode(ch.read(ch.nrec - 1))
    ch.close()
    assert last["KEY"] == "Blank1"


def test_link_table_prune_keeps_every_used_entry(EX):
    """used_link_entries is the identity on unchanged pages (genies, templates, symbols,
    super genie hosts, composites of the fake install)."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format import links

    for fn in (
        "FxGenies.ctg",
        "FxTplLinked.ctg",
        "FxSymbols.ctg",
        "FxAssoc.ctg",
        "FxComposite.ctg",
        "FxParts.ctg",
        "FxMissing.ctg",
    ):
        p = os.path.join(EX, fn)
        pb = parse_ctg(p).page
        t = links.parse_link_table(pb.link_table_raw)
        assert GP.used_link_entries(pb.objects, t[1]) == t[1], fn


def test_link_table_prune_keeps_library_bag_references(EX):
    """A tc 0xFFFF record names its element in a '_Library' bag value (the
    entry has no stamp and a zero GUID): that entry is in use too. FxPlaceholder holds
    the placeholders of fxgone.fx_value and fxgone.fx_plain (a library that is gone)."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format import links

    pb = parse_ctg(os.path.join(EX, "FxPlaceholder.ctg")).page
    assert any(po.type_code == 0xFFFF for po in pb.objects)
    t = links.parse_link_table(pb.link_table_raw)
    assert any(n == "fxgone.fx_value.<ctm" and g == bytes(16) for g, n in t[1])
    assert GP.used_link_entries(pb.objects, t[1]) == t[1]


def _links_of(path):
    from ctgedit.format import links

    t = links.parse_link_table(parse_ctg(path).page.link_table_raw)
    return [links.strip_stamp(n).lower() for _g, n in (t[1] if t else [])]


def _root_placing(doc, ref):
    from ctgedit.format import genie_place as GP
    from ctgedit.format import links

    for i, po in enumerate(doc.parsed.page.objects):
        if po.subtype_size & 0x1:
            r = GP.decode(po)
            if r.link and links.strip_stamp(r.link).lower() == ref:
                return doc.page.root_of(i)
    raise AssertionError("no record places " + ref)


def test_undo_after_save_as_gets_the_pruned_link_entry_back(EX, tmp_path):
    """The Save As prune is not an undo step: a genie deleted before the Save
    As and brought back by Undo gets its link-table entry again on the next
    save (Update Pages reads the element stamp from it)."""
    from ctgedit.editing import EditableDocument

    src = str(tmp_path / "Mixer.ctg")
    shutil.copy(os.path.join(EX, "FxGenies.ctg"), src)
    doc = EditableDocument.open(src)
    doc.delete_objects([_root_placing(doc, AGITATOR)])
    out = str(tmp_path / "Copy.ctg")
    doc.save(out)
    assert AGITATOR not in _links_of(out)
    doc.undo()
    doc.save()
    assert AGITATOR in _links_of(out)
    assert _root_placing(doc, AGITATOR) is not None


def test_failed_save_as_leaves_the_document_as_it_was(EX, tmp_path, monkeypatch):
    """A Save As whose write fails keeps the document's own identity, stamps
    and link table (the copy's are applied only once it is on disk)."""
    from ctgedit.editing import EditableDocument

    src = str(tmp_path / "Mixer.ctg")
    shutil.copy(os.path.join(EX, "FxGenies.ctg"), src)
    doc = EditableDocument.open(src)
    doc.delete_objects([_root_placing(doc, AGITATOR)])  # the prune has work to do
    pb, at, ct = doc.parsed.page, doc.parsed.attributes, doc.parsed.container
    before = (
        pb.page_identity,
        pb.link_table_raw,
        pb.link_count,
        at.page_identity,
        at.ts_modified,
        ct.ts_created,
        ct.ts_modified,
    )

    def refuse(_a, b):
        raise PermissionError(13, "Access is denied", b)

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(PermissionError):
        doc.save(str(tmp_path / "Copy.ctg"))
    monkeypatch.undo()
    assert (
        pb.page_identity,
        pb.link_table_raw,
        pb.link_count,
        at.page_identity,
        at.ts_modified,
        ct.ts_created,
        ct.ts_modified,
    ) == before
    assert doc.path == src
    doc.save()
    # in place: a new identity like every save (CtDraw's CoCreateGuid), the
    # document's own the one on disk, and the link table rebuilt from the records (the
    # deleted genie's entry goes)
    on_disk = parse_ctg(src).page.page_identity
    assert on_disk != before[0] and on_disk == pb.page_identity
    assert AGITATOR not in _links_of(src)


# --------------------------------------------------------------------------- Delete page
@pytest.mark.parametrize("files", [True, False])
def test_delete_page_removes_its_records(SA, tmp_path, files):
    from ctgedit.io.project import Project, delete_page

    proj = _mini_project(SA, str(tmp_path / "SA"), [HW, MASTER])
    for ext in (".ctF", ".rdb"):
        with open(os.path.join(proj, HW + ext), "wb") as fh:
            fh.write(b"sidecar")
    PR.register_page(proj, HW, os.path.join(proj, HW + ".ctg"))
    assert _live_rows(proj, "pgkey", HW) and _live_rows(proj, "pgdynobj", HW)
    master_before = {t: _live_rows(proj, t, MASTER) for t in ("pgkey", "pgdynobj", "pgtouch")}
    rep = delete_page(proj, HW, files=files)
    assert not rep["errors"]
    assert Project(proj).page_record(HW) is None
    for t, _p in PR.page_tables(proj):
        assert _live_rows(proj, t, HW) == [], t  # incl. the blank-AN key rows
    assert {t: _live_rows(proj, t, MASTER) for t in master_before} == master_before
    assert not os.path.exists(os.path.join(proj, HW + ".rdb"))
    assert os.path.exists(os.path.join(proj, HW + ".ctg")) is (not files)
    assert os.path.exists(os.path.join(proj, HW + ".ctF")) is (not files)
    ch = Dbf(os.path.join(proj, "changes.DBF"), writable=False)
    last = ch.decode(ch.read(ch.nrec - 1))
    ch.close()
    assert last["NAME"] == "pages" and last["KEY"] == HW
    assert rep["records"] and int(last["RECORD_NO"]) == rep["records"][-1]
    assert rep["compstate"] is not None
    for t in ("pgkey", "pgdynobj"):
        _index_consistent(proj, t)


def test_browser_delete_button_deletes_the_page(SA, tmp_path):
    _app()
    from ctgedit.io.catalog import ProjectCatalog
    from ctgedit.ui.browser import PageBrowserDialog

    proj = _mini_project(SA, str(tmp_path / "Loose"), [HW, MASTER])
    cat = ProjectCatalog(str(tmp_path / "no_install"))
    dlg = PageBrowserDialog(
        PageBrowserDialog.OPEN, catalog=cat, start_path=os.path.join(proj, HW + ".ctg"), kinds=("page",)
    )
    calls = []
    try:
        assert dlg.current_entry().name == HW
        assert dlg.btn_delete is not None and dlg.btn_delete.isEnabled()
        dlg.ask_delete_page = lambda _parent, _path: None  # Cancel
        assert not dlg.delete_current()
        assert os.path.exists(os.path.join(proj, HW + ".ctg"))
        dlg.ask_delete_page = lambda _parent, _path: "files"
        dlg.before_delete = lambda *a: calls.append(a) or True
        assert dlg.delete_current()
        assert calls == [("page", os.path.join(proj, HW + ".ctg"), "")]
        assert not os.path.exists(os.path.join(proj, HW + ".ctg"))
        assert [e.name for e in dlg.model.all_entries()] == [MASTER]
        from ctgedit.io.project import Project

        assert Project(proj).page_record(HW) is None
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------------- elements
#: the symbols of the test's SMOKE.ctl and the fxsym.ctl elements they copy
SMOKE = (("S1", "fx_box"), ("S2", "fx_round"), ("S3", "fx_on"), ("S4", "fx_off"))


def _smoke(tmp_path):
    """A symbol library SMOKE.ctl in tmp_path of four elements S1 .. S4 in slots 0 .. 3
    (copies of FxLib's fxsym fx_box, fx_round, fx_on, fx_off, written in order: the last
    slot, S4's, has the tag 0x0101). Made from the committed fixtures."""
    import fxinstall
    from ctgedit.format import element as E

    src = E.LibraryFile.open(os.path.join(fxinstall.FX_INSTALL, "User", "FxLib", "fxsym.ctl"))
    lib = E.LibraryFile.new()
    for name, el in SMOKE:
        lib.write_element(name, src.block_data(src.find(el)))
    dst = str(tmp_path / "SMOKE.ctl")
    E._write_library(dst, lib)
    return dst


def test_delete_element_marks_it_deleted_until_pack(tmp_path):
    from ctgedit.format import element as E
    from ctgedit.format.pagesummary import list_element_names, read_directory
    from ctgedit.format.projects import pack_libraries

    lib = _smoke(tmp_path)
    before = E.LibraryFile.open(lib)
    blocks = {n: before.block_data(before.find(n)) for n in before.element_names()}
    assert sorted(blocks) == ["S1", "S2", "S3", "S4"] and len(set(blocks.values())) == 4
    s4 = before.find("S4")
    assert s4.tag == 0x0101
    assert E.delete_library_element(lib, "S4") and E.delete_library_element(lib, "S2")
    assert not E.delete_library_element(lib, "S2")  # already gone
    after = E.LibraryFile.open(lib)
    assert after.find("S4") is None and after.find("S2") is None
    assert list_element_names(lib) == ["S1", "S3"]
    ents = {e.slot: e for e in read_directory(lib).entries}
    # CtDraw's delete: tag low byte cleared (high byte kept), name 'deleted', extent kept
    assert (ents[3].tag, ents[3].name, ents[3].offset, ents[3].size) == (
        0x0100,
        "deleted",
        s4.offset,
        s4.size,
    )
    assert ents[1].tag == 0x0000 and ents[1].name == "deleted"
    assert after.slots[1].raw[2:34] == b"deleted".ljust(32, b"\0")
    for n in ("S1", "S3"):
        assert after.block_data(after.find(n)) == blocks[n]
    assert os.path.getsize(lib) == len(before.to_bytes())  # nothing moved
    changed, saved = pack_libraries(str(tmp_path))
    assert changed == 1 and saved > 0
    assert list_element_names(lib) == ["S1", "S3"]
    assert not any(e.is_deleted for e in read_directory(lib).entries)


def test_rename_element(tmp_path):
    from ctgedit.format import element as E

    lib = _smoke(tmp_path)
    data = E.LibraryFile.open(lib).block_data(E.LibraryFile.open(lib).find("S2"))
    assert E.rename_library_element(lib, "S2", "Smoke2") == "Smoke2"
    L = E.LibraryFile.open(lib)
    assert L.find("S2") is None and L.block_data(L.find("Smoke2")) == data
    with pytest.raises(ValueError):
        E.rename_library_element(lib, "Smoke2", "S1")  # taken
    with pytest.raises(ValueError):
        E.rename_library_element(lib, "Smoke2", "a.b")  # a dot splits references
    with pytest.raises(ValueError):
        E.rename_library_element(lib, "Smoke2", "x" * 32)  # CtDraw keeps 31 bytes
    with pytest.raises(KeyError):
        E.rename_library_element(lib, "nope", "x")
    # a super genie keeps its @SUPER suffix
    E.store_element(
        lib, "pop@SUPER", E.LibraryFile.open(lib).block_data(E.LibraryFile.open(lib).find("S1"))[: len(data)]
    )
    assert E.rename_library_element(lib, "pop@SUPER", "popup") == "popup@SUPER"
    assert E.LibraryFile.open(lib).find("popup@SUPER") is not None


def test_delete_and_rename_stamp_the_library(tmp_path):
    """CtDraw writes a changed directory back with a new container
    ts_modified, so a delete / rename stamps the library."""
    from ctgedit.format import element as E

    lib = _smoke(tmp_path)
    L = E.LibraryFile.open(lib)
    L.ts_modified = 1
    E._write_library(lib, L)
    now = E.ctdraw_timestamp()
    assert E.delete_library_element(lib, "S4")
    assert E.LibraryFile.open(lib).ts_modified >= now
    L = E.LibraryFile.open(lib)
    L.ts_modified = 1
    E._write_library(lib, L)
    E.rename_library_element(lib, "S2", "Smoke2")
    assert E.LibraryFile.open(lib).ts_modified >= now


def test_super_genie_usage_note():
    from ctgedit.ui import lifecycle

    assert "opened at runtime" in lifecycle.usage_text([], "faceplat.ctm", "!sg1_itune@SUPER")
    assert "opened at runtime" not in lifecycle.usage_text([], "smoke.ctl", "S1")


def test_browser_renames_and_deletes_a_library_element(tmp_path):
    _app()
    from ctgedit.io.catalog import ProjectCatalog
    from ctgedit.ui.browser import PageBrowserDialog

    lib = _smoke(tmp_path)
    cat = ProjectCatalog(str(tmp_path / "no_install"))
    dlg = PageBrowserDialog(PageBrowserDialog.OPEN, catalog=cat, start_path=lib, kinds=("symbol",))
    try:
        assert dlg.select_name("S3", "SMOKE")
        dlg.ask_element_name = lambda *_a: "Smoke3"
        assert dlg.rename_current() == "Smoke3"
        assert dlg.current_entry().name == "Smoke3"
        dlg.ask_delete_element = lambda _p, _lib, element, usage: element == "Smoke3" and usage == []
        assert dlg.delete_current()
        assert sorted(e.name for e in dlg.model.all_entries()) == ["S1", "S2", "S4"]
        assert dlg.deleted == [lib + "|S3", lib + "|Smoke3"]
    finally:
        dlg.deleteLater()


def test_element_usage_comes_from_the_pages_link_tables(fx_install):
    """fxgenie.fx_value is placed on FxGenies (FxValue) and on FxTplUnlinked."""
    from ctgedit.io.catalog import catalog_for

    cat = catalog_for(fx_install.user)
    use = cat.element_usage(fx_install.file("FxLib", "fxgenie.ctm"), "fx_value")
    assert "FxGenies" in [e.name for e in use]
    assert all(e.project == "FxMain" for e in use)
    assert cat.element_usage(fx_install.file("FxLib", "fxsym.ctl"), "no_such_element") == []


def test_library_panel_offers_delete_and_rename(EX, tmp_path, monkeypatch):
    _app()
    from ctgedit.ui import lifecycle
    from ctgedit.ui.panels.library_panel import LibraryPanel

    p = LibraryPanel()
    p.set_project_dir(EX)
    p.ensure_loaded()
    p.set_kind("symbol")
    p.set_query("fx_box")
    e = p.current_entry()
    assert e is not None
    menu = p.build_context_menu(e)
    texts = [a.text() for a in menu.actions()]
    assert "Delete Element…" in texts and "Rename Element…" in texts
    got = []
    p.deleteRequested.connect(lambda *a: got.append(("delete",) + a))
    p.renameRequested.connect(lambda *a: got.append(("rename",) + a))
    next(a for a in menu.actions() if a.text() == "Delete Element…").trigger()
    next(a for a in menu.actions() if a.text() == "Rename Element…").trigger()
    assert got == [("delete", e.path, e.name), ("rename", e.path, e.name)]
    p.close()
    # the host's confirmation flow on a COPY of the library
    lib = _smoke(tmp_path)
    asked = []
    assert not lifecycle.delete_element(None, lib, "S1", ask=lambda *a: asked.append(a) or False)
    assert asked and asked[0][2] == "S1"
    assert lifecycle.delete_element(None, lib, "S1", ask=lambda *a: True)
    assert lifecycle.rename_element(None, lib, "S3", ask=lambda *a: "Smoke3") == "Smoke3"
    from ctgedit.format.pagesummary import list_element_names

    assert list_element_names(lib) == ["S2", "Smoke3", "S4"]


def _answer_later(text=None, typed=None):
    """Answer the next modal box: click the button labelled ``text`` (or type
    ``typed`` into an input dialog and accept)."""
    from PySide6 import QtCore, QtWidgets

    def go():
        box = QtWidgets.QApplication.activeModalWidget()
        if box is None:
            QtCore.QTimer.singleShot(10, go)
            return
        if typed is not None:
            box.findChild(QtWidgets.QLineEdit).setText(typed)
            box.accept()
            return
        for b in box.findChildren(QtWidgets.QAbstractButton):
            if b.text().replace("&", "") == text:
                b.click()
                return
        box.reject()

    QtCore.QTimer.singleShot(0, go)


def test_the_confirmations_offer_ctdraws_choices(tmp_path):
    _app()
    from ctgedit.ui import lifecycle

    page = str(tmp_path / "!Popup.ctg")
    _answer_later("Remove from Project Only")
    assert lifecycle.ask_delete_page(None, page) == lifecycle.PAGE_PROJECT
    _answer_later("Delete Page and Files")
    assert lifecycle.ask_delete_page(None, page) == lifecycle.PAGE_FILES
    _answer_later("Cancel")
    assert lifecycle.ask_delete_page(None, page) is None
    lib = _smoke(tmp_path)
    _answer_later("Delete")
    assert lifecycle.ask_delete_element(None, lib, "S1", []) is True
    _answer_later("Cancel")
    assert lifecycle.ask_delete_element(None, lib, "S1", []) is False
    _answer_later(typed="Smoke9")
    assert lifecycle.ask_element_name(None, lib, "S1", []) == "Smoke9"
    assert "It is placed on 2 pages" in lifecycle.usage_text(
        [type("E", (), {"name": "A", "project": "P"})()] * 2
    )


# --------------------------------------------------------------------------- main window
@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    for e in list(w._docs):
        e["doc"].dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)


def test_deleting_an_open_page_closes_its_tab(win, EX, tmp_path, monkeypatch):
    from PySide6 import QtWidgets

    page = str(tmp_path / "Mixer.ctg")
    shutil.copy(os.path.join(EX, "FxShapes.ctg"), page)
    win.open_document(page)
    assert win._tabs_of("page", page) == [0]
    # unsaved edits: the user is asked; Cancel keeps the tab (and the page)
    win.doc.dirty = True
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Cancel)
    )
    assert not win._before_delete_or_rename("page", page)
    assert win._tabs_of("page", page) == [0]
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Discard)
    )
    close = win._before_delete_or_rename("page", page)
    assert callable(close)
    # (the tab closes only once the delete has succeeded: the step it hands back)
    assert win._tabs_of("page", page) == [0]
    close()
    assert win._tabs_of("page", page) == [] and win.doc is None
    assert os.path.exists(page)  # the hook only closes; deleting is next


def test_library_panel_delete_goes_through_the_window(win, tmp_path, monkeypatch):
    from ctgedit.ui import lifecycle

    lib = _smoke(tmp_path)
    monkeypatch.setattr(lifecycle, "ask_delete_element", lambda *a: True)
    monkeypatch.setattr(lifecycle, "ask_element_name", lambda *a: "Smoke1")
    assert win._rename_library_element(lib, "S1") == "Smoke1"
    assert win._delete_library_element(lib, "S2")
    from ctgedit.format.pagesummary import list_element_names

    assert list_element_names(lib) == ["Smoke1", "S3", "S4"]


def _fake_use_template(monkeypatch, win, new_path, ex, ref="fx_style.normal@XGA.<ctt"):
    """File > New > Page without its window: the New window answers a page at ``new_path``
    from template ``ref`` (linked); templates resolve through the include chain of ``ex``
    (FxMain of the fake install copy: FxStyle has fx_style.ctt)."""
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.browser.new_mode import NewTarget

    monkeypatch.setattr(
        PageBrowserDialog, "get_new", staticmethod(lambda *a, **k: NewTarget("page", new_path, "", ref, True))
    )
    monkeypatch.setenv("CTG_LIB_PATH", ex)


def test_new_page_is_written_and_registered_by_its_first_save(win, EX, tmp_path, monkeypatch):
    """Nothing is written until the first Save, as in CtDraw; that save writes the page
    with its thumbnail and registers it."""
    from PySide6 import QtWidgets
    from ctgedit.io.project import Project

    proj = str(tmp_path / "EX")
    os.makedirs(proj)
    for f in ("pages.DBF", "changes.DBF", "PROJECT.CIT"):
        shutil.copy(os.path.join(EX, f), os.path.join(proj, f))
    src = os.path.join(proj, "Mixer.ctg")
    shutil.copy(os.path.join(EX, "FxShapes.ctg"), src)
    new = os.path.join(proj, "Fresh.ctg")
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: 0))
    _fake_use_template(monkeypatch, win, new, EX)
    win._new_page()
    assert win.doc is not None and os.path.normcase(win.doc.path) == os.path.normcase(new)
    assert win.doc.dirty and not os.path.exists(new)
    assert Project(proj).page_record("Fresh") is None
    assert "Save it" in win.statusBar().currentMessage()
    assert win.save()
    assert not win.doc.dirty
    assert Project(proj).page_record("Fresh")["TITLE"] == "Fresh"
    assert parse_ctg(new).page.thumbnail[:2] == b"TH"  # rendered by the first save


def test_new_page_stays_dirty_when_its_first_save_fails(win, EX, tmp_path, monkeypatch):
    """Closing then asks to save it; nothing was registered."""
    from PySide6 import QtWidgets
    from ctgedit.io.project import Project

    proj = str(tmp_path / "EX")
    os.makedirs(proj)
    shutil.copy(os.path.join(EX, "pages.DBF"), os.path.join(proj, "pages.DBF"))
    src = os.path.join(proj, "Mixer.ctg")
    shutil.copy(os.path.join(EX, "FxShapes.ctg"), src)
    new = os.path.join(proj, "Fresh.ctg")
    warned = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2]) or 0)
    )
    _fake_use_template(monkeypatch, win, new, EX)

    win._new_page()

    def full_disk(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(win, "_save_to", full_disk)
    with pytest.raises(OSError):
        win._save_to()
    assert win.doc is not None and win.doc.dirty  # closing asks to save it
    assert not os.path.exists(new)
    assert Project(proj).page_record("Fresh") is None


def test_new_page_outside_a_project_does_not_claim_registration(win, EX, tmp_path, monkeypatch):
    from PySide6 import QtWidgets

    src = str(tmp_path / "Mixer.ctg")
    shutil.copy(os.path.join(EX, "FxShapes.ctg"), src)
    new = str(tmp_path / "Loose.ctg")
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: 0))
    _fake_use_template(monkeypatch, win, new, EX)
    win._new_page()
    assert win.doc is not None and win.doc.dirty and not os.path.exists(new)
    assert "New page Loose" in win.statusBar().currentMessage()
    assert "registered" not in win.statusBar().currentMessage()


# --------------------------------------------------------------------------- Update Pages
def test_update_pages_only_touches_registered_pages(EX, tmp_path):
    from ctgedit.io import updatepages

    proj = str(tmp_path / "EX")
    os.makedirs(proj)
    shutil.copy(os.path.join(EX, "pages.DBF"), os.path.join(proj, "pages.DBF"))
    shutil.copy(os.path.join(EX, "FxShapes.ctg"), os.path.join(proj, "FxShapes.ctg"))
    shutil.copy(os.path.join(EX, "FxShapes.ctg"), os.path.join(proj, "FxShapes_old_copy.ctg"))
    names = [os.path.basename(p) for p in updatepages.project_pages(proj)]
    assert names == ["FxShapes.ctg"]
    assert len(updatepages.project_pages(proj, registered_only=False)) == 2
    rep = updatepages.update_pages(proj)
    assert rep.pages == 1 and "FxShapes_old_copy" not in rep.updated
    # a folder without pages.DBF is not a project: every page counts
    os.remove(os.path.join(proj, "pages.DBF"))
    assert len(updatepages.project_pages(proj)) == 2


# --------------------------------------------------------------------------- naming
def test_page_name_clash_with_a_compiled_together_project_warns():
    from ctgedit.ui.browser import naming

    v = naming.validate_page_name(
        "DefaultAlarm_HD1080",
        None,
        existing={},
        registered=(),
        has_pages_dbf=True,
        elsewhere={"defaultalarm_hd1080": "SA_Include"},
    )
    assert v.level == naming.WARN and "SA_Include" in v.message and v.ok
    ok = naming.validate_page_name(
        "MyAlarm",
        None,
        existing={},
        registered=(),
        has_pages_dbf=True,
        elsewhere={"defaultalarm_hd1080": "SA_Include"},
    )
    assert ok.level == naming.OK


def test_pages_elsewhere_covers_the_include_chain(fx_install):
    """FxOther includes FxMain (and FxLib); its one page FxShapes is named like FxMain's."""
    from ctgedit.io.catalog import catalog_for

    cat = catalog_for(fx_install.user)
    other_p = cat.project("FxOther")
    assert any(p.name.lower() == "fxmain" for p in cat.closure(other_p))
    other = cat.pages_elsewhere(other_p)
    assert other.get(MASTER.lower(), "").lower() == "fxmain"
    assert other.get("fxgenies", "").lower() == "fxmain"
    # and the FxMain side sees FxOther (it compiles FxMain in), never its own pages
    inc = cat.project("FxMain")
    back = cat.pages_elsewhere(inc)
    assert back.get("fxshapes", "").lower() == "fxother"
    assert MASTER.lower() not in back and "fxgenies" not in back


def test_a_deleted_page_comes_back_from_its_backup(SA, tmp_path, monkeypatch):
    """Delete Page backs the page up first; Restore Backup puts back its files, its pages.DBF row
    and its project database rows."""
    from ctgedit.infra import backups
    from ctgedit.io import updatepages
    from ctgedit.io.project import Project
    from ctgedit.ui import lifecycle

    monkeypatch.setenv("CTG_DATA_DIR", str(tmp_path / "data"))
    proj = _mini_project(SA, str(tmp_path / "SA"), [HW, MASTER])
    page = os.path.join(proj, HW + ".ctg")
    PR.register_page(proj, HW, page)
    keys = _live_rows(proj, "pgkey", HW)
    rec = Project(proj).page_record(HW)
    with open(page, "rb") as fh:
        data = fh.read()
    rep = lifecycle.delete_page(None, page, ask=lambda *_a: lifecycle.PAGE_FILES)
    assert rep and rep["backup"] and not os.path.exists(page)
    assert Project(proj).page_record(HW) is None
    b = backups.list_backups()[0]
    assert b.kind == "delete-page" and b.entries[0]["page"] == HW
    res = updatepages.restore_backup(b.path)
    assert res.restored == [HW] and not res.failed
    with open(page, "rb") as fh:
        assert fh.read() == data
    back = Project(proj).page_record(HW)
    assert back is not None and back.get("TITLE") == rec.get("TITLE")
    assert len(_live_rows(proj, "pgkey", HW)) == len(keys)


def test_open_updates_run_in_the_editors_order_and_survive_a_failing_step(EX, tmp_path):
    """Open updates run in the editor's order and survive a failing step."""
    import types
    from ctgedit.editing import EditableDocument

    src = os.path.join(EX, "FxGenies.ctg")
    p = tmp_path / "Genies.ctg"
    shutil.copy(src, p)
    doc = EditableDocument.open(str(p))
    calls = []

    def stub(name, ret, fail=False):
        def f(self, *a, **k):
            calls.append(name)
            if fail:
                raise RuntimeError("boom")
            return ret

        setattr(doc, name, types.MethodType(f, doc))

    stub("symbol_placements", [1])
    stub("stale_symbol_elements", {("a", "b")})
    stub("update_symbol_placements", 2)
    stub("placeholder_genies", [1])
    stub("instantiate_placeholder_genies", 0, fail=True)
    stub("genie_changes", {("l", "e"): None})
    stub("apply_genie_changes", 3)
    stub("template_status", ("stale", "x"))
    stub("update_template", 4)
    done = doc.apply_open_updates()
    assert done == ["4 template", "2 symbols", "3 genies"]
    assert [c for c in calls if c.startswith(("update_", "instantiate", "apply_"))] == [
        "update_template",
        "update_symbol_placements",
        "instantiate_placeholder_genies",
        "apply_genie_changes",
    ]
    calls.clear()
    assert doc.apply_open_updates(symbols=False, genies=False, template=False) == []
    assert "apply_genie_changes" not in calls and "update_template" not in calls


def test_a_failed_element_delete_keeps_its_tab_and_edits(monkeypatch):
    """The library cannot be written: the element is not deleted, and the tab that had
    unsaved edits is still open with them (they were asked about, not yet dropped)."""
    from ctgedit.ui import lifecycle

    closed = []
    monkeypatch.setattr(
        "ctgedit.format.element.delete_library_element",
        lambda *a: (_ for _ in ()).throw(PermissionError(13, "in use")),
    )
    monkeypatch.setattr(lifecycle, "_warn", lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, "element_usage", lambda *a, **k: [])
    ok = lifecycle.delete_element(
        None, "C:/x/lib.ctl", "el", before=lambda *a: lambda: closed.append(1), ask=lambda *a: True
    )
    assert ok is False and closed == []
    monkeypatch.setattr("ctgedit.format.element.delete_library_element", lambda *a: True)
    monkeypatch.setattr(lifecycle, "_library_written", lambda *a: "")
    ok = lifecycle.delete_element(
        None, "C:/x/lib.ctl", "el", before=lambda *a: lambda: closed.append(1), ask=lambda *a: True
    )
    assert ok is True and closed == [1]
