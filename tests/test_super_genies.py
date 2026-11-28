"""Super genies: storage, pages and Attach."""

from ctgedit.format.fileio import find_ci
import hashlib
import os
import shutil
import struct

import pytest

import fxinstall

#: the attaching genie (library, element), its super genie, the second super genie
GENIE = ("fxgenie", "fx_popper")
SUPER = "!fx_popup@SUPER"
SUPER2 = "!fx_detail@SUPER"
#: the genie attaching both (fx_popper2 of fxparts)
GENIE2 = ("fxparts", "fx_popper2")


def _records_sha(objects):
    from ctgedit.format import writer

    return hashlib.sha256(b"".join(writer.object_payload(o) for o in objects)).hexdigest()


def _lib_dir():
    """FxLib of the test's copy (the ``fx_install`` fixture made it the install)."""
    return os.path.join(os.environ["CTG_SCADA_ROOT"], "User", "FxLib")


@pytest.fixture
def fxlib(fx_install, monkeypatch):
    """FxLib of the test's copy, on the include path (CTG_LIB_PATH) of the pages and
    libraries the tests make outside the install."""
    d = fx_install.project("FxLib")
    monkeypatch.setenv("CTG_LIB_PATH", d)
    return d


def _project(fxlib, tmp_path, name="FxHost", lib=False):
    """A project folder ``tmp_path/User/<name>`` outside the install, with FxMain's
    PROJECT.CIT and EMPTY pages.DBF, changes.DBF and page tables (+ fresh indexes).
    ``lib``: also copies of fxgenie.ctm and fxsuper.ctm (found before FxLib's on the
    include path)."""
    from ctgedit.format.dbf import Dbf, create_ndx
    from ctgedit.io import pgregister as PR

    src_dir = os.path.join(os.path.dirname(fxlib), "FxMain")
    proj = tmp_path / "User" / name
    proj.mkdir(parents=True)
    shutil.copyfile(find_ci(src_dir, "PROJECT.CIT"), str(proj / "PROJECT.CIT"))
    for t in ["pages", "changes"] + list(PR.PAGE_TABLES) + ["activex", "pglang"]:
        src = find_ci(src_dir, t + ".DBF")
        if src is None:
            continue
        d = Dbf(src, writable=False)
        with open(src, "rb") as fh:
            head = bytearray(fh.read(d.hlen))
        struct.pack_into("<I", head, 4, 0)
        (proj / (t + ".DBF")).write_bytes(bytes(head) + b"\x1a")
        if t not in ("pages", "changes"):
            key = "NATIVE" if t == "pglang" else "PAGE"
            create_ndx(str(proj / (t + ".NDX")), key, d.fmap[key][1])
        d.close()
    if lib:
        for f in ("fxgenie.ctm", "fxsuper.ctm"):
            shutil.copyfile(os.path.join(fxlib, f), str(proj / f))
    return str(proj)


def _host(proj, name="Host"):
    from ctgedit.editing import EditableDocument
    from ctgedit.format import template_place as TP

    return EditableDocument.from_bytes(TP.new_page_bytes(800, 600), os.path.join(proj, name + ".ctg"))


def _rows(proj, table, page):
    from ctgedit.format.dbf import Dbf

    d = Dbf(os.path.join(proj, table + ".DBF"), writable=False)
    try:
        return [d.decode(r) for _i, r in d.find("PAGE", page) if r[:1] != b"*"]
    finally:
        d.close()


def _stamp(lib, element):
    """The save stamp of a library element (its identity's first u32), as a bind shows it."""
    from ctgedit.format import element as E

    return "%08x" % struct.unpack_from("<I", E.open_element(lib, element).page.page_identity, 0)[0]


def _buttons(objs):
    """(AN, CMD) of the button records of a page (AN = handle + 1)."""
    from ctgedit.format import geometry as G

    return sorted(
        (str(G.get_handle(o) + 1), str(o.raw_properties().get("CMD")).strip())
        for o in objs
        if o.type_code == 0x1001 and o.raw_properties().get("CMD")
    )


def _our_page(fxlib, element=SUPER):
    """The page format/supergenie makes from FxLib's super genie (no stamp given)."""
    from ctgedit.format import supergenie as SG
    from ctgedit.format.page import parse_ctg_bytes

    sg = SG.load(os.path.join(fxlib, "fxsuper.ctm"), element)
    return parse_ctg_bytes(SG.new_page_bytes(sg, SG.link_text("fxsuper", SG.page_name(element))))


# =========================================================================== link tables
def test_library_attachments_parse(fx_install):
    """FxLib's attaching genies store @SUPER bind entries."""
    from ctgedit.format import links
    from ctgedit.format.symbols import SymbolLibrary

    found = {}
    for lib_name in ("fxgenie.ctm", "fxparts.ctm"):
        lib = SymbolLibrary.open(fx_install.file("FxLib", lib_name))
        for name in lib.element_names():
            pb = lib.element(name)
            if pb is None or not pb.link_table_raw:
                continue
            parsed = links.parse_link_table(pb.link_table_raw)
            assert parsed is not None, name
            att = links.super_genie_attachments(parsed[1])
            if att:
                found[name] = att
    assert found == {
        "fx_popper": ["fxsuper.!fx_popup"],
        "fx_popper2": ["fxsuper.!fx_detail", "fxsuper.!fx_popup"],
    }
    all_names = [a for v in found.values() for a in v]
    assert any("@" not in a and "." in a for a in all_names)
    assert any("!fx" in a.lower() for a in all_names)


def _round_trip(lib_path):
    from ctgedit.format import links
    from ctgedit.format.symbols import SymbolLibrary

    lib = SymbolLibrary.open(lib_path)
    checked = 0
    for name in lib.element_names():
        pb = lib.element(name)
        if pb is None or not pb.link_table_raw:
            continue
        parsed = links.parse_link_table(pb.link_table_raw)
        if parsed is None or not parsed[1]:
            continue
        raw, link_count = links.serialize_link_table(parsed[0], parsed[1])
        assert raw == pb.link_table_raw, name
        assert link_count == pb.link_count, name
        checked += 1
    return checked


def test_link_table_round_trips_byte_exact(fx_install):
    """parse + serialize reproduces every link table of FxLib's genie libraries
    byte-exactly (up to the zero pad, which we also reproduce)."""
    checked = sum(
        _round_trip(fx_install.file("FxLib", f))
        for f in ("fxgenie.ctm", "fxparts.ctm", "fxsuper.ctm", "fxnest.ctm")
    )
    assert checked >= 5


@pytest.mark.golden
@pytest.mark.parametrize(
    "unit", ["FxLib/fxgenie.ctm", "FxLib/fxparts.ctm", "FxLib/fxsuper.ctm", "FxLib/fxnest.ctm"]
)
def test_ctdraws_link_tables_round_trip_byte_exact(unit):
    """The same over the libraries as CtDraw saved them (the oracle's open + save of each
    element)."""
    path = fxinstall.oracle("elements", unit, "library.ctm")
    assert _round_trip(path) >= 1


def test_set_attachments_preserves_imports():
    from ctgedit.format import links

    entries = [
        (b"\x01" * 16, "fxsym.fx_box.<ctl#dd67fb91"),
        (b"\x00" * 16, "fxsuper.!fx_old@SUPER#dd67f948"),
        (b"\x02" * 16, "\\\\Derivative"),
    ]
    out = links.set_super_genie_attachments(entries, ["fxsuper.!fx_new"])
    names = [n for _g, n in out]
    assert "fxsym.fx_box.<ctl#dd67fb91" in names  # import kept
    assert "\\\\Derivative" in names  # marker kept
    assert not any("fx_old" in n for n in names)  # old bind replaced
    assert names[-1] == "fxsuper.!fx_new@SUPER#00000000"
    # round-trip through the serializer
    raw, _lc = links.serialize_link_table("    ", out)
    area, back = links.parse_link_table(raw)
    assert [n for _g, n in back] == names


def test_attachments_rewrite_on_library_copy(tmp_path, fx_install):
    """End-to-end: rewrite a genie template's bind list on a COPY of the
    library; every element still parses and the binds read back."""
    from ctgedit.format import links
    from ctgedit.format.container import Container
    from ctgedit.format.page import parse_page
    from ctgedit.format.symbols import SymbolLibrary
    from ctgedit.format.writer import serialize_page_block

    lib_copy = str(tmp_path / "fxgenie.ctm")
    shutil.copyfile(fx_install.file("FxLib", "fxgenie.ctm"), lib_copy)
    lib = SymbolLibrary.open(lib_copy)
    target = None
    for name in lib.element_names():
        pb = lib.element(name)
        if pb is None or not pb.link_table_raw:
            continue
        parsed = links.parse_link_table(pb.link_table_raw)
        if parsed and links.super_genie_attachments(parsed[1]):
            target = name
            break
    assert target == "fx_popper"
    with open(lib_copy, "rb") as fh:
        cont = Container.parse(fh.read())
    blk = next(b for b in cont.blocks if b.name == target)
    pb = parse_page(blk, cont.word2)
    n_objs = len(pb.objects)
    area, entries = links.parse_link_table(pb.link_table_raw)
    new_entries = links.set_super_genie_attachments(entries, ["fxsuper.!fx_detail", "fxother.!fx_more"])
    raw, lc = links.serialize_link_table(area, new_entries)
    pb.link_table_raw = raw
    pb.link_count = lc
    blk.data = serialize_page_block(pb, blk.data)
    cont.relayout()
    with open(lib_copy, "wb") as fh:
        fh.write(cont.to_bytes())
    # read back: binds updated, records intact
    lib2 = SymbolLibrary.open(lib_copy)
    pb2 = lib2.element(target)
    assert pb2 is not None and len(pb2.objects) == n_objs
    att = links.super_genie_attachments(links.parse_link_table(pb2.link_table_raw)[1])
    assert att == ["fxsuper.!fx_detail", "fxother.!fx_more"]
    for name in lib2.element_names():
        assert lib2.element(name) is not None, name


# ===============================================================================
# Super genie PAGES (format/supergenie.py), Attach Super Genies through the
# element save, Update Pages of super genie pages, associations in use.
def test_association_tokens_follow_ctdraws_parse():
    """?name? / ?TYPE name? (DIGITAL LONG INT OBJECT REAL
    STRING); tokens do not overlap; NOT inside a string literal (LIVE:
    CtDraw's In Use column says 'No' for StrToReal("?A1?"))."""
    from ctgedit.format import supergenie as SG

    assert SG.association_tokens("?DIGITAL 1?=1") == ["1"]
    assert SG.association_tokens("NOT ?digital 2?") == ["2"]
    assert SG.association_tokens("?INT AlmDesc? + ?Speed?") == ["AlmDesc", "Speed"]
    assert SG.association_tokens('_F("?__EquipmentRef?", "x")') == []
    assert SG.association_tokens('StrToReal("?A1?") + ?A2?') == ["A2"]
    assert SG.association_tokens('Prompt("say ^"?A1?^" ok") + ?A3?') == ["A3"]
    assert SG.association_tokens("??Equipment?.Setpoint?") == ["Equipment"]
    assert SG.association_tokens("?a?b?c?") == ["a", "c"]
    assert SG.association_tokens("Are you sure? Yes") == []
    # a number token is listed as the number
    assert SG.association_tokens("?INT 02? + ?7?") == ["2", "7"]
    assert SG.page_name("!fx_popup@SUPER") == "!fx_popup"
    assert SG.link_text("fxsuper", "!fx_popup") == "fxsuper.!fx_popup@SUPER.<ctm"


def test_sync_lists_is_ctdraws_page_load_rule():
    """a super genie page's non-'!' environment and its
    associations become the element's when they differ (same count, every
    key with the same value); the page's '!' keys stay, first."""
    from ctgedit.format import element as E
    from ctgedit.format import supergenie as SG
    from ctgedit.format import template_place as TP

    tr = E.ElementTrailer([("SgVar", "2"), (E.KEY_ASSOCIATIONS, "1")], [("Tag", "x\n\n")], None, [], [])
    sg = TP.Template(None, 10, 10, tr)
    assert SG.element_lists(sg) == ([("SgVar", "2")], [("Tag", "x\n\n")])
    assert SG.sync_lists([("!TITLE", "t"), ("sgvar", "2")], [("Tag", "x\n\n")], sg) == (None, None)
    assert SG.sync_lists([("PageVar", "1"), ("!TITLE", "t")], [("Tag", "y\n\n")], sg) == (
        [("!TITLE", "t"), ("SgVar", "2")],
        [("Tag", "x\n\n")],
    )
    assert SG.sync_lists([("SgVar", "2"), ("More", "1")], [], sg) == ([("SgVar", "2")], [("Tag", "x\n\n")])


def test_existing_bind_keeps_its_guid():
    """CtDraw's save keeps a bind's stored guid (zero on an attaching genie) and takes
    the target's current stamp; a NEW bind is stored with a zero guid and the target's
    stamp (LIVE, CtDraw's Attach Super Genies on library copies)."""
    from ctgedit.format import links

    entries = [(b"\x00" * 16, "fxsuper.!fx_popup@SUPER#dd67f948")]
    out = links.set_super_genie_attachments(
        entries, ["fxsuper.!fx_popup", "fxsuper.!fx_detail"], lambda n: (b"\x07" * 16, 0xDD67FB98)
    )
    assert out == [
        (b"\x00" * 16, "fxsuper.!fx_popup@SUPER#dd67fb98"),
        (b"\x00" * 16, "fxsuper.!fx_detail@SUPER#dd67fb98"),
    ]
    # unresolvable: the stored stamp stays
    out = links.set_super_genie_attachments(entries, ["fxsuper.!fx_popup"], None)
    assert out == entries


def test_super_genie_page_is_the_element_as_a_genie_instance(fxlib):
    """format/supergenie: the element as a template placement + the root's genie-instance
    bit; its link names the super genie. (That it is CtDraw's page record for record is
    the golden test below.)"""
    from ctgedit.format import element as E
    from ctgedit.format import supergenie as SG

    pc = _our_page(fxlib)
    objs = pc.page.objects
    el = E.open_element(os.path.join(fxlib, "fxsuper.ctm"), SUPER)
    assert len(objs) == len(el.tree)
    assert [o.type_code for o in objs] == [o.type_code for o in el.tree]
    assert objs[0].subtype_size & 0x200 and not el.tree[0].subtype_size & 0x200
    assert SG.page_link(objs) == ("fxsuper", SUPER)
    assert _buttons(objs) and _buttons(objs)[0][1] == "WinFree();"


def _deflated_streams(blob: bytes) -> dict:
    """{inflated bytes: the zlib stream as ``blob`` holds it} of every zlib stream in
    ``blob`` (a .ctF: the background DIB, the bitmaps)."""
    import zlib

    out, o = {}, 0
    while True:
        o = blob.find(b"\x78", o)
        if o < 0:
            return out
        d = zlib.decompressobj()
        try:
            raw = d.decompress(blob[o:])
        except zlib.error:
            o += 1
            continue
        if d.eof and raw:
            end = len(blob) - len(d.unused_data)
            out[raw] = blob[o:end]
            o = end
        else:
            o += 1


@pytest.mark.golden
def test_super_genie_page_is_the_page_ctdraw_creates(fxlib, monkeypatch):
    """A super genie page is the page CtDraw creates."""
    from ctgedit.format import ctf as CTF
    from ctgedit.format import geometry as G
    from ctgedit.format import supergenie as SG
    from ctgedit.format import template_place as TP
    from ctgedit.format import writer
    from ctgedit.format.page import parse_ctg_bytes
    from ctgedit.format.symbols import SymbolResolver
    from ctgedit.io import projectcit

    want = parse_ctg_bytes(fxinstall.oracle_bytes("place", "attaching_genie", "files/FxMain/!fx_popup.ctg"))
    want_ctf = fxinstall.oracle_bytes("place", "attaching_genie", "files/FxMain/!fx_popup.ctF")
    sg = SG.load(os.path.join(fxlib, "fxsuper.ctm"), SUPER)
    assert TP.template_page_properties(sg)[0] is None  # (the element has none)
    stamp = struct.unpack_from("<I", want.page.page_identity, 0)[0]
    bg = projectcit.page_defaults(os.path.join(os.path.dirname(fxlib), "FxMain"))["background"]
    data = SG.new_page_bytes(sg, SG.link_text("fxsuper", "!fx_popup"), stamp=stamp, background=(bg, bg))
    pc = parse_ctg_bytes(data)
    objs = pc.page.objects
    assert _records_sha(objs) == _records_sha(want.page.objects)
    assert [(o.type_code, o.subtype_size, G.get_handle(o), o.an_str) for o in objs] == [
        (o.type_code, o.subtype_size, G.get_handle(o), o.an_str) for o in want.page.objects
    ]
    assert pc.page.link_table_raw.rstrip(b"\0") == want.page.link_table_raw.rstrip(b"\0")
    assert (pc.page.page_width, pc.page.page_height) == (want.page.page_width, want.page.page_height)
    assert (pc.attributes.bg_color0, pc.attributes.bg_color1) == (
        want.attributes.bg_color0,
        want.attributes.bg_color1,
    )
    assert pc.attributes.list1.pairs == want.attributes.list1.pairs
    assert pc.attributes.list2.pairs == want.attributes.list2.pairs
    assert SG.page_link(objs) == ("fxsuper", SUPER)
    # with CtDraw's identity the native fast display is CtDraw's .ctF byte for byte
    pc.page.page_identity = pc.attributes.page_identity = want.page.page_identity
    data2 = writer.serialize_ctg(pc.container, pc.page, pc.attributes)
    target = os.path.join(os.path.dirname(fxlib), "FxMain", "!fx_popup.ctg")
    ctdraws = _deflated_streams(want_ctf)
    assert ctdraws  # (the background DIB at least)
    ours = CTF.compress
    monkeypatch.setattr(CTF, "compress", lambda raw: ctdraws.get(raw) or ours(raw))
    blob = CTF.build_ctf(data2, target, resolver=SymbolResolver.for_page(target, [fxlib]))
    import ctfcompare  # (without the classic zlib: the DIBs deflated again first)

    ctfcompare.assert_same_ctf(blob, want_ctf)


def test_placing_a_genie_creates_its_super_genie_page(fxlib, tmp_path):
    """Placing fxgenie.fx_popper (it attaches fxsuper.!fx_popup) writes the super genie
    page into the PLACING page's project at once, as CtDraw does: .ctg (the page
    format/supergenie makes), .ctF, pages.DBF row NAME = TITLE, its pg rows (the button's
    WinFree(), at its AN) and the changes.DBF entry. A second placement leaves it."""
    from ctgedit.format.page import parse_ctg
    from ctgedit.io.project import Project

    proj = _project(fxlib, tmp_path)
    doc = _host(proj)
    assert doc.attached_super_genies(*GENIE) == [("fxsuper", SUPER)]
    i = doc.place_library_element(*GENIE, "ctm", (100, 100))
    assert i is not None
    (rep,) = doc.super_pages
    assert rep["status"] == "created", rep
    page = os.path.join(proj, "!fx_popup.ctg")
    assert os.path.normcase(rep["path"]) == os.path.normcase(page)
    pb = parse_ctg(page).page
    ours = _our_page(fxlib)
    assert _records_sha(pb.objects) == _records_sha(ours.page.objects)
    stamp = _stamp(os.path.join(fxlib, "fxsuper.ctm"), SUPER)
    from ctgedit.format import links

    assert [n for _g, n in links.parse_link_table(pb.link_table_raw)[1]] == [
        "fxsuper.!fx_popup@SUPER.<ctm#" + stamp
    ]
    assert os.path.exists(os.path.join(proj, "!fx_popup.ctF"))
    row = Project(proj).page_record("!fx_popup")
    assert {k: v.strip() for k, v in row.items() if v.strip()} == {"NAME": "!fx_popup", "TITLE": "!fx_popup"}
    have = sorted(
        (r["AN"].strip(), (r.get("CMD") or "").strip()) for r in _rows(proj, "pgbutton", "!fx_popup")
    )
    assert have == _buttons(pb.objects) and [c for _a, c in have] == ["WinFree();"]
    from ctgedit.format.dbf import Dbf

    ch = Dbf(os.path.join(proj, "changes.DBF"), writable=False)
    last = ch.decode(ch.read(ch.nrec - 1))
    ch.close()
    assert last["NAME"] == "pages" and last["KEY"] == "!fx_popup"
    # the placing page itself is not saved by it
    assert not os.path.exists(os.path.join(proj, "Host.ctg"))
    # a second placement: the page exists -> left alone
    doc.place_library_element(*GENIE, "ctm", (200, 100))
    assert [r["status"] for r in doc.super_pages] == ["exists"]
    # the page is a current super genie page
    from ctgedit.editing import EditableDocument

    sp = EditableDocument.open(page)
    assert sp.supergenie_link() == ("fxsuper", SUPER)
    assert sp.supergenie_status()[0] == "current"
    assert sp.update_supergenie_page() == 0


@pytest.mark.golden
def test_placing_a_genie_writes_ctdraws_page_and_rows(fxlib, tmp_path):
    """The page and the pg rows the placement writes are CtDraw's (the oracle's
    place/attaching_genie: LibraryObjectPlaceEx of fx_popper, Tag FX_PUMP1, in FxMain with
    its own !fx_popup deleted first, so the page and its rows are new there as they are in
    the fresh project here)."""
    from ctgedit.format.page import parse_ctg, parse_ctg_bytes

    want = parse_ctg_bytes(fxinstall.oracle_bytes("place", "attaching_genie", "files/FxMain/!fx_popup.ctg"))
    rows = fxinstall.oracle_json("place", "attaching_genie", "rows.json")["tables"]
    proj = _project(fxlib, tmp_path)
    doc = _host(proj)
    doc.place_library_element(*GENIE, "ctm", (200, 200), {"Tag": "FX_PUMP1"})
    pb = parse_ctg(os.path.join(proj, "!fx_popup.ctg")).page
    assert _records_sha(pb.objects) == _records_sha(want.page.objects)
    assert pb.link_table_raw.rstrip(b"\0") == want.page.link_table_raw.rstrip(b"\0")
    for key, table in rows.items():
        t = os.path.splitext(key.split("/")[-1])[0].lower()
        if not t.startswith("pg"):
            continue
        added = [r for r in table.get("added", ()) if r.get("PAGE", "").strip() == "!fx_popup"]
        have = [{k: v.strip() for k, v in r.items() if v.strip()} for r in _rows(proj, t, "!fx_popup")]
        assert sorted(sorted(r.items()) for r in have) == sorted(
            sorted((k, v.strip()) for k, v in r.items() if k != "_flag" and v.strip()) for r in added
        ), t


def test_super_genie_page_rules_include_unregistered_and_opt_out(fxlib, tmp_path):
    """No page in a project named Include, none with
    instantiate=False, and a stray unregistered .ctg of that name is left
    alone (reported) instead of overwritten."""
    proj = _project(fxlib, tmp_path, name="Include")
    doc = _host(proj)
    doc.place_library_element(*GENIE, "ctm", (100, 100))
    assert [r["status"] for r in doc.super_pages] == ["skipped"]
    assert not os.path.exists(os.path.join(proj, "!fx_popup.ctg"))
    proj2 = _project(fxlib, tmp_path / "b")
    doc2 = _host(proj2)
    doc2.place_library_element(*GENIE, "ctm", (100, 100), instantiate=False)
    assert doc2.super_pages == []
    assert not os.path.exists(os.path.join(proj2, "!fx_popup.ctg"))
    stray = os.path.join(proj2, "!fx_popup.ctg")
    with open(stray, "wb") as fh:
        fh.write(b"not mine")
    doc2.place_library_element(*GENIE, "ctm", (200, 100))
    (rep,) = doc2.super_pages
    assert rep["status"] == "exists" and "not registered" in rep["detail"]
    assert open(stray, "rb").read() == b"not mine"
    # a super genie that is not on the include path: reported, nothing written
    from ctgedit.editing import create_super_genie_pages

    (rep,) = create_super_genie_pages(proj2, [("nolib", "!nothing@SUPER")], doc2._resolver())
    assert rep["status"] == "missing" and "include path" in rep["detail"]
    assert not os.path.exists(os.path.join(proj2, "!nothing.ctg"))


def test_attach_in_the_genie_window_is_saved_with_the_genie(fxlib, tmp_path):
    """Attach in the genie window is saved with the genie."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import element as E
    from ctgedit.format import links

    lib = str(tmp_path / "fxgenie.ctm")
    shutil.copyfile(os.path.join(fxlib, "fxgenie.ctm"), lib)
    before = E.open_element(lib, "fx_popper")
    others = {
        n: before.library.block_data(before.library.find(n))
        for n in before.library.element_names()
        if n != "fx_popper"
    }
    doc = ElementDocument.open_element(lib, "fx_popper")
    assert doc.can_attach_super_genies
    assert doc.attachments() == ["fxsuper.!fx_popup"]
    with pytest.raises(ValueError) as exc:
        doc.set_attachments(["fxsuper.!fx_popup", "fxsuper.nosuch"])
    assert "nosuch" in str(exc.value)
    # a NEW super genie is stored as the element spells it ('!fx_detail@SUPER',
    # what CtDraw's Add picker writes); the one already attached keeps its spelling
    assert doc.set_attachments(["fxsuper.!fx_popup", "fxsuper.!FX_Detail@SUPER"])
    assert doc.attachments() == ["fxsuper.!fx_popup", "fxsuper.!fx_detail"]
    assert doc.dirty
    doc.undo()
    assert doc.attachments() == ["fxsuper.!fx_popup"]
    doc.redo()
    doc.save()
    after = E.open_element(lib, "fx_popper")
    sup = os.path.join(fxlib, "fxsuper.ctm")
    assert links.parse_link_table(after.page.link_table_raw)[1] == [
        (b"\x00" * 16, "fxsuper.!fx_popup@SUPER#" + _stamp(sup, SUPER)),
        (b"\x00" * 16, "fxsuper.!fx_detail@SUPER#" + _stamp(sup, SUPER2)),
    ]
    # no pages.DBF next to the library: not a project, no page is created
    assert [r["status"] for r in doc.super_pages] == ["skipped", "skipped"]
    assert after.page.object_count == before.page.object_count
    assert after.page.page_identity != before.page.page_identity
    L = E.LibraryFile.open(lib)
    assert {n: L.block_data(L.find(n)) for n in L.element_names() if n != "fx_popper"} == others
    # a symbol / super genie window has no Attach Super Genies (CtDraw: genie only)
    sgdoc = ElementDocument.open_element(sup, SUPER)
    assert not sgdoc.can_attach_super_genies
    with pytest.raises(ValueError):
        sgdoc.set_attachments(["fxsuper.!fx_detail"])


@pytest.mark.golden
def test_attach_saves_the_records_ctdraws_attach_writes():
    """The records are saved engine-normal, like CtDraw's save of an attach (LIVE, on a
    library of the install: the button caption '%TAG%' and its auto width 21 -> 64)."""
    pytest.skip(
        "not captured, on purpose: CtDraw's Attach Super Genies + save (the Attach "
        "Super Genies dialog has no COM route; filling it needs UI automation)"
    )


def test_attach_from_a_placed_genie_saves_its_element(fxlib, tmp_path):
    """With a placed genie selected on a page, the list goes through the same
    element save (identity refreshed, libraries re-read)."""
    from ctgedit.format import element as E
    from ctgedit.format import links

    proj = _project(fxlib, tmp_path, lib=True)
    doc = _host(proj)
    i = doc.place_library_element(*GENIE, "ctm", (100, 100), instantiate=False)
    lib = os.path.join(proj, "fxgenie.ctm")
    target = doc._genie_template_target(i)
    assert os.path.normcase(target[0]) == os.path.normcase(lib)
    ident = E.open_element(lib, "fx_popper").page.page_identity
    assert doc.genie_attachments(i) == ["fxsuper.!fx_popup"]
    assert doc.set_genie_attachments(i, ["fxsuper.!fx_popup", "fxsuper.!fx_detail"])
    pe = E.open_element(lib, "fx_popper")
    assert pe.page.page_identity != ident
    want = ["fxsuper.!fx_popup", "fxsuper.!fx_detail"]
    assert links.super_genie_attachments(links.parse_link_table(pe.page.link_table_raw)[1]) == want
    assert doc.genie_attachments(i) == want
    assert doc.super_genie_problem("fxsuper.nosuch")
    assert doc.super_genie_problem("nodot")
    assert doc.super_genie_problem("fxsuper.!fx_detail") is None
    # the genie save created the super genie pages the library's project
    # lacked (CtDraw's genie save), named as the binds spell them
    assert [(r["name"], r["status"]) for r in doc.super_pages] == [
        ("!fx_popup", "created"),
        ("!fx_detail", "created"),
    ]
    from ctgedit.io.project import Project

    for name in ("!fx_popup", "!fx_detail"):
        assert os.path.exists(os.path.join(proj, name + ".ctg"))
        assert Project(proj).page_record(name) is not None


def test_saving_a_genie_creates_its_super_genie_pages_even_in_include(fxlib, tmp_path):
    """CtDraw's genie save creates the missing super genie pages
    in the genie's own project -- also in a project named Include, which a
    placement never writes into; an existing page is left."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format.page import parse_ctg
    from ctgedit.io.project import Project

    proj = _project(fxlib, tmp_path, name="Include", lib=True)
    lib = os.path.join(proj, "fxgenie.ctm")
    sup = os.path.join(proj, "fxsuper.ctm")
    el = ElementDocument.open_element(lib, "fx_popper")
    el.save()
    assert [(r["name"], r["status"]) for r in el.super_pages] == [("!fx_popup", "created")]
    page = os.path.join(proj, "!fx_popup.ctg")
    assert _records_sha(parse_ctg(page).page.objects) == _records_sha(_our_page(proj).page.objects)
    assert Project(proj).page_record("!fx_popup")["TITLE"].strip() == "!fx_popup"
    # LIVE: the bind a genie save created a page from is written
    # with its target's identity
    from ctgedit.format import element as E
    from ctgedit.format import links

    target = E.open_element(sup, SUPER).page.page_identity[4:20]
    saved = E.open_element(lib, "fx_popper")
    assert [g for g, _n in links.parse_link_table(saved.page.link_table_raw)[1]] == [target]
    stamp = os.stat(page).st_mtime_ns

    def guids():
        saved = E.open_element(lib, "fx_popper")
        return [g for g, _n in links.parse_link_table(saved.page.link_table_raw)[1]]

    # ... and every other save writes a zero guid (LIVE: CtDraw's plain load +
    # save zeroed it; the save creating the second page wrote its identity on that bind
    # only)
    assert el.set_attachments(el.attachments() + ["fxsuper.!fx_detail"])
    el.save()
    assert [r["status"] for r in el.super_pages] == ["exists", "created"]
    assert os.stat(page).st_mtime_ns == stamp
    detail = E.open_element(sup, SUPER2).page.page_identity[4:20]
    assert guids() == [bytes(16), detail]
    el.save()  # nothing changed and no page made: not written
    assert [r["status"] for r in el.super_pages] == ["exists", "exists"]
    assert guids() == [bytes(16), detail]
    el.set_attachments(["fxsuper.!fx_detail"])
    el.save()  # a real save: the stored guid goes
    assert guids() == [bytes(16)]
    # a symbol / super genie save creates nothing
    sg = ElementDocument.open_element(sup, SUPER2)
    sg.save()
    assert sg.super_pages == []


@pytest.mark.golden
def test_a_genie_save_creates_ctdraws_super_genie_page(fxlib, tmp_path):
    """The page a genie save creates is the page CtDraw's genie save makes (the oracle's
    open + save of each element of FxLib's fxgenie.ctm keeps any page CtDraw makes)."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format.page import parse_ctg, parse_ctg_bytes

    want = parse_ctg_bytes(
        fxinstall.oracle_bytes("elements", "FxLib/fxgenie.ctm", "files/FxLib/!fx_popup.ctg")
    )
    proj = _project(fxlib, tmp_path, lib=True)
    el = ElementDocument.open_element(os.path.join(proj, "fxgenie.ctm"), "fx_popper")
    el.save()
    pb = parse_ctg(os.path.join(proj, "!fx_popup.ctg")).page
    assert _records_sha(pb.objects) == _records_sha(want.page.objects)


def test_a_super_genie_made_in_ctgedit_gets_its_page(fxlib, tmp_path):
    """A super genie made in ctgedit gets its page."""
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.format import element as E
    from ctgedit.io.project import Project

    proj = _project(fxlib, tmp_path)
    lib = os.path.join(proj, "mylib.ctm")
    sg = ElementDocument.new_element(lib, "!mysg", "supergenie", page_size=(300, 200))
    r = sg.create_object("rectangle", rect=(10, 10, 100, 60), colour=0x336699)
    assert sg.ensure_property(r, "COL_A", "?DIGITAL 1?")
    assert sg.set_associations([("1", "\n\nThe state")])
    sg.save()
    root = E.open_element(lib, "!mysg@SUPER").tree[0]
    assert root.type_code == 0x08 and root.class_flag == 1
    g = ElementDocument.new_element(lib, "mygenie", "genie")
    b = g.create_object("rectangle", rect=(0, 0, 40, 20), colour=0x00FF00)
    assert g.ensure_property(b, "COL_A", "%Tag%")
    g.save()
    assert E.open_element(lib, "mygenie").tree[0].type_code == 0x08
    assert g.set_attachments(["mylib.!mysg"])
    g.save()
    assert [(p["name"], p["status"]) for p in g.super_pages] == [("!mysg", "created")]
    page = os.path.join(proj, "!mysg.ctg")
    d = EditableDocument.open(page)
    assert d.supergenie_link() == ("mylib", "!mysg@SUPER")
    assert d.supergenie_status()[0] == "current"
    assert d.associations() == [("1", "", "", "The state")]
    assert Project(proj).page_record("!mysg")["TITLE"].strip() == "!mysg"
    assert [(x["AN"].strip(), x["COL_A"].strip()) for x in _rows(proj, "pgdynobj", "!mysg")] == [
        (
            str(
                __import__("ctgedit.format.geometry", fromlist=["x"]).get_handle(d.parsed.page.objects[1]) + 1
            ),
            "?DIGITAL 1?",
        )
    ]
    assert os.path.exists(os.path.join(proj, "!mysg.ctF"))
    host = _host(proj)
    host.place_library_element("mylib", "mygenie", "ctm", (50, 50), {"Tag": "Pump1"})
    assert [(p["name"], p["status"]) for p in host.super_pages] == [("!mysg", "exists")]


def test_update_pages_leaves_current_super_genie_pages_alone(fx_install):
    """FxMain's !fx_popup (fxsuper.!fx_popup@SUPER) and !fx_detail are current: Update
    Pages does nothing -- and even a forced re-compose reproduces them byte for byte.
    Their root is not a genie instance of its own."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.symbols import SymbolResolver
    from ctgedit.io import updatepages as U

    for path in (fx_install.page("!fx_popup"), fx_install.page("!fx_detail")):
        doc = EditableDocument.open(path)
        assert doc.supergenie_link() is not None
        assert doc.supergenie_status()[0] == "current", path
        assert doc.update_supergenie_page() == 0
        assert doc.update_supergenie_page(force=True) == 0
        assert not doc.can_undo()
        assert doc.genie_instances() == []
        cur = U.Current(SymbolResolver.for_page(path))
        assert not U.stale_supergenie(path, cur)
        assert not U.stale_elements(path, cur)


def test_update_pages_recreates_a_changed_super_genie_page(fxlib, tmp_path):
    """Update Pages recreates a changed super genie page."""
    from ctgedit.editing import ElementDocument, EditableDocument
    from ctgedit.format import element as E
    from ctgedit.format import links
    from ctgedit.io import updatepages as U

    proj = _project(fxlib, tmp_path, lib=True)
    host = _host(proj)
    host.place_library_element(*GENIE, "ctm", (100, 100))
    page = os.path.join(proj, "!fx_popup.ctg")
    sp = EditableDocument.open(page)
    size = (sp.parsed.page.page_width, sp.parsed.page.page_height)
    assert sp.set_associations([("Speed", "'1'\n\nSpeed of the pump")])
    sp.set_page_property("!Keep", "yes")
    sp.set_page_property("PageVar", "1")
    sp.save()
    ident = sp.parsed.page.page_identity
    # change the super genie: the Close button's command, an association and
    # an environment variable
    lib = os.path.join(proj, "fxsuper.ctm")
    el = ElementDocument.open_element(lib, SUPER)
    k = next(
        j
        for j, o in enumerate(el.parsed.page.objects)
        if o.type_code == 0x1001 and "WinFree" in str(o.raw_properties().get("CMD"))
    )
    assert el.set_property(k, "CMD", "WinFree(); Beep(0);")
    assert el.set_associations([("Tag", "Pump1\n\nThe pump tag")])
    el.set_page_property("SgVar", "2")
    el.save()
    cur = U.Current(__import__("ctgedit.format.symbols", fromlist=["x"]).SymbolResolver.for_page(page))
    assert U.stale_supergenie(page, cur)
    rep = U.update_pages(proj)
    assert rep.supergenies == 1 and rep.updated == ["!fx_popup"], rep
    after = EditableDocument.open(page)
    assert after.supergenie_status()[0] == "current"
    assert after.parsed.page.page_identity != ident
    assert (after.parsed.page.page_width, after.parsed.page.page_height) == size
    assert after.associations() == [("Tag", "Pump1", "", "The pump tag")]
    assert after.parsed.attributes.list1.pairs == [("!Keep", "yes"), ("SgVar", "2")]
    cmds = [str(o.raw_properties().get("CMD")) for o in after.parsed.page.objects if o.type_code == 0x1001]
    assert "WinFree(); Beep(0);" in cmds
    (entry,) = [n for _g, n in links.parse_link_table(after.parsed.page.link_table_raw)[1]]
    stamp = struct.unpack_from("<I", E.open_element(lib, SUPER).page.page_identity, 0)[0]
    assert entry == "fxsuper.!fx_popup@SUPER.<ctm#%08x" % stamp
    (button_an,) = [a for a, c in _buttons(after.parsed.page.objects) if c == "WinFree(); Beep(0);"]
    assert (button_an, "WinFree(); Beep(0);") in [
        (r["AN"].strip(), r["CMD"].strip()) for r in _rows(proj, "pgbutton", "!fx_popup")
    ]
    # nothing left to do
    assert U.update_pages(proj).updated == []


def test_associations_in_use_are_the_pages_tokens(fx_install):
    """Page Properties > Associations 'In Use': the names of
    the ?associations? the page's objects use (FxMain's FxAssoc: ?Tag?, ?DIGITAL 1? /
    ?DIGITAL 2? in a button, a symbol set, a tool tip and a touch command, and the
    ?__EquipmentRef? of FxEquipRef)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxAssoc"))
    used = doc.associations_in_use()
    assert set(used) == {"tag", "1", "2", "__equipmentref"}
    assert used["tag"] == "Tag" and used["__equipmentref"] == "__EquipmentRef"


def _qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_associations_tab_in_use_column_and_name_list(fxlib, tmp_path):
    """The Associations table shows CtDraw's In Use column (Yes / No, not
    editable) and the Name list offers the used associations not listed (the
    !fx_detail page placing fx_popper2 creates: it uses 1, 2 and 3)."""
    _qt()
    from PySide6 import QtCore
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    proj = _project(fxlib, tmp_path)
    host = _host(proj)
    host.place_library_element(*GENIE2, "ctm", (100, 100))
    doc = EditableDocument.open(os.path.join(proj, "!fx_detail.ctg"))
    assert doc.set_associations([("1", "\n\nStatus"), ("Unused", "\n\n")])
    dlg = PagePropertiesDialog(doc, Project(proj))
    try:
        t = dlg._assoc_table
        assert t.horizontalHeaderItem(4).text() == "In Use"
        assert [(t.item(r, 0).text(), t.item(r, 4).text()) for r in range(t.rowCount())] == [
            ("1", "Yes"),
            ("Unused", "No"),
        ]
        assert not t.item(0, 4).flags() & QtCore.Qt.ItemIsEditable
        assert dlg.unlisted_associations() == ["2", "3"]
        # the name cell's editor lists them
        ed = t.itemDelegateForColumn(0).createEditor(t, None, t.model().index(1, 0))
        assert [ed.itemText(k) for k in range(ed.count())] == ["2", "3"]
        ed.deleteLater()
        # renaming a row updates its In Use
        t.item(1, 0).setText("2")
        assert t.item(1, 4).text() == "Yes"
        assert dlg.unlisted_associations() == ["3"]
    finally:
        dlg.deleteLater()


def test_metadata_hint_names_the_attached_super_genies_associations(fxlib, tmp_path):
    """Help 1172182: metadata names must match the super genie's associations
    -- the Metadata page lists them for the object's genie (fx_popper2 opens !fx_detail,
    associations 1, 2, 3, and !fx_popup, association Tag)."""
    proj = _project(fxlib, tmp_path)
    doc = _host(proj)
    i = doc.place_library_element(*GENIE2, "ctm", (100, 100), instantiate=False)
    got = doc.super_genie_associations(i)
    assert {page: set(names) for page, names in got} == {"!fx_detail": {"1", "2", "3"}, "!fx_popup": {"Tag"}}
    member = doc.subtree(i)[-1]
    assert doc.super_genie_associations(member) == got


def test_super_genie_element_associations_are_its_record_b(fxlib, tmp_path):
    """In the super genie's own window the Associations tab offers the names
    its objects use (In Use, the Name list) and a save stores the rows in the
    element's trailer record B -- what a page created from it takes."""
    _qt()
    from ctgedit.editing import ElementDocument
    from ctgedit.format import element as E
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    lib = str(tmp_path / "fxsuper.ctm")
    shutil.copyfile(os.path.join(fxlib, "fxsuper.ctm"), lib)
    doc = ElementDocument.open_element(lib, "!FX_Detail@SUPER")
    assert set(doc.associations_in_use()) == {"1", "2", "3"}
    assert doc.set_associations([])  # (the element declares 1, 2, 3: none now)
    dlg = PagePropertiesDialog(doc, None)
    try:
        assert sorted(dlg.unlisted_associations()) == ["1", "2", "3"]
    finally:
        dlg.deleteLater()
    rows = [("1", "\n\nOpen / closed state"), ("2", "\n\nAutomatic mode")]
    assert doc.set_associations(rows)
    doc.save()
    assert E.open_element(lib, SUPER2).trailer.associations == rows


def test_metadata_page_of_a_genie_member_names_the_super_genies(fxlib, tmp_path):
    """In the genie's own window the Metadata page lists the super genies the
    genie opens with their associations (the names a metadata row passes)."""
    _qt()
    from ctgedit.editing import ElementDocument
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    lib = str(tmp_path / "fxgenie.ctm")
    shutil.copyfile(os.path.join(fxlib, "fxgenie.ctm"), lib)
    doc = ElementDocument.open_element(lib, "fx_popper")
    k = next(j for j, o in enumerate(doc.parsed.page.objects) if o.type_code not in (0x1010, 0x100B))
    dlg = ObjectPropertiesDialog(doc, k)
    try:
        md = dlg.metadata_editor
        assert not md._ro
        text = md.note.text()
        assert "!fx_popup (" in text and "associations" in text
        assert "Tag" in text.split("!fx_popup (", 1)[1]
    finally:
        dlg.deleteLater()


def test_super_genie_name_advice():
    from ctgedit.ui.dialog.supergenie_dialogs import supergenie_name_advice

    assert "!" in supergenie_name_advice("sg2_test")
    assert supergenie_name_advice("!sg2_test") == ""
    assert supergenie_name_advice("") == ""
    _qt()
    from ctgedit.ui.dialog.library_dialogs import CopyToLibraryDialog

    dlg = CopyToLibraryDialog(
        os.getcwd(), None, exts=("ctm",), element="sg2", name_advice=supergenie_name_advice
    )
    try:
        assert "'!'" in dlg._hint.text()
        dlg._ed_elem.setText("!sg2")
        assert "'!'" not in dlg._hint.text()
    finally:
        dlg.deleteLater()


def test_attach_dialog_picker_checks_and_order():
    """Add = the super genie picker (a seam here), refused when not found or
    already listed; Remove, Move Up / Down; names() in list order."""
    _qt()
    from ctgedit.ui.dialog.supergenie_dialogs import AttachSuperGeniesDialog, library_token

    picks = iter(["fxsuper.!sg_b", "fxsuper.!missing", "fxsuper.!sg_a"])
    dlg = AttachSuperGeniesDialog(
        ["fxsuper.!sg_a"],
        None,
        genie="fxsuper.g",
        problem=lambda n: "not found" if "missing" in n else None,
        pick=lambda: next(picks),
    )
    try:
        dlg._add()
        assert dlg.names() == ["fxsuper.!sg_a", "fxsuper.!sg_b"]
        dlg._add()
        assert dlg.names() == ["fxsuper.!sg_a", "fxsuper.!sg_b"]
        assert "not found" in dlg._msg.text()
        dlg._add()  # already listed
        assert "already attached" in dlg._msg.text()
        dlg._list.setCurrentRow(1)
        dlg._move(-1)
        assert dlg.names() == ["fxsuper.!sg_b", "fxsuper.!sg_a"]
        dlg._remove()
        assert dlg.names() == ["fxsuper.!sg_a"]
    finally:
        dlg.deleteLater()
    assert library_token(r"C:\p\FXSUPER.CTM") == "fxsuper"
    assert library_token(r"C:\p\Fx_Mixed.ctm") == "Fx_Mixed"


def test_opening_a_super_genie_page_asks_first(fxlib, tmp_path, monkeypatch):
    """[CtDraw.RSC] AllowEditSuperGeniePage (default 0): the page is not simply
    opened for editing -- the user picks Edit Super Genie / Open Read-Only /
    Cancel; with 1, Open Page too. A restored session opens it read-only."""
    _qt()
    from ctgedit.editing import ElementDocument
    from ctgedit.io import citect_config
    from ctgedit.ui.main_window import MainWindow

    proj = _project(fxlib, tmp_path)
    host = _host(proj)
    host.place_library_element(*GENIE, "ctm", (100, 100))
    page = os.path.join(proj, "!fx_popup.ctg")
    asked = []

    def answer(key):
        def run(dlg):
            asked.append(sorted(dlg._buttons))
            return key

        return staticmethod(run)

    monkeypatch.setattr(citect_config, "allow_edit_super_genie_page", lambda: False)
    w = MainWindow()
    try:
        monkeypatch.setattr(MainWindow, "_exec_supergenie_dialog", answer("cancel"))
        w.open_document(page)
        assert asked[-1] == ["cancel", "definition", "view"]
        assert not any(os.path.normcase(e["doc"].path) == os.path.normcase(page) for e in w._docs)
        monkeypatch.setattr(MainWindow, "_exec_supergenie_dialog", answer("view"))
        w.open_document(page)
        assert w.doc.path == page and w.doc.read_only
        w._close_doc_tab(w._docs.index(next(e for e in w._docs if e["doc"] is w.doc)))
        monkeypatch.setattr(MainWindow, "_exec_supergenie_dialog", answer("definition"))
        w.open_document(page)
        assert isinstance(w.doc, ElementDocument)
        assert w.doc.element_name.lower() == "!fx_popup@super"
        monkeypatch.setattr(citect_config, "allow_edit_super_genie_page", lambda: True)
        monkeypatch.setattr(MainWindow, "_exec_supergenie_dialog", answer("edit"))
        # the page's own association: CtDraw's load replaces it with the super
        # genie's (Tag), as an undoable step of the opened page
        from ctgedit.editing import EditableDocument as _ED

        pdoc = _ED.open(page)
        assert pdoc.set_associations([("Speed", "'1'\n\n")])
        pdoc.save()
        w.open_document(page)
        assert asked[-1] == ["cancel", "definition", "edit", "view"]
        assert w.doc.path == page and not w.doc.read_only
        assert [a[0] for a in w.doc.associations()] == ["Tag"] and w.doc.dirty
        w.doc.undo()
        assert [a[0] for a in w.doc.associations()] == ["Speed"]
    finally:
        for e in list(w._docs):
            e["doc"].dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.hide()
        w.deleteLater()
    # an ordinary page is not asked about (the guard never shows its dialog)
    monkeypatch.setattr(
        MainWindow,
        "_exec_supergenie_dialog",
        staticmethod(lambda dlg: pytest.fail("asked about an ordinary page")),
    )
    assert host.supergenie_link() is None
    assert MainWindow._supergenie_guard(None, host, ask=True) == "edit"


def test_browser_has_a_super_genie_tab(fx_install):
    """File > Open lists super genies on their own tab (CtDraw's Super Genie
    tab) and leaves them out of the Genies tab. (FxLib of the test's copy, with a copy
    of FxMain's !fx_popup next to its libraries to start the browser at.)"""
    _qt()
    from ctgedit.io.catalog import catalog_for, is_super_genie
    from ctgedit.ui.browser import PageBrowserDialog

    lib_dir = fx_install.project("FxLib")
    start = os.path.join(lib_dir, "!fx_popup.ctg")
    shutil.copyfile(fx_install.page("!fx_popup"), start)
    cat = catalog_for()
    inc = cat.project_for_dir(lib_dir)
    assert inc is not None
    assert cat.count(inc, "supergenie") >= 1
    sup = cat.entries([inc], "supergenie")
    assert sup and all(is_super_genie(e.name) and e.kind == "supergenie" for e in sup)
    dlg = PageBrowserDialog(PageBrowserDialog.OPEN, catalog=cat, start_path=start)
    try:
        assert "supergenie" in dlg.kinds
        scope = dlg.current_scope()
        assert scope is not None
        genies = dlg._entries_for(scope, "genie")
        supers = dlg._entries_for(scope, "supergenie")
        assert not any(is_super_genie(e.name) for e in genies)
        assert supers and all(is_super_genie(e.name) for e in supers)
    finally:
        dlg.deleteLater()


def test_window_placement_creates_the_page_with_a_thumbnail(fxlib, tmp_path):
    """The editor's placement (Library panel / Paste Genie) creates the super
    genie page with a rendered thumbnail and says so on the status bar."""
    _qt()
    from ctgedit.format.page import parse_ctg
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    proj = _project(fxlib, tmp_path)
    host = _host(proj)
    host.save()
    app_settings().setValue("opt/disable_genie_forms", True)
    w = MainWindow()
    try:
        w.open_document(host.path)
        w._place_library(*GENIE, "ctm", (100, 100))
        page = os.path.join(proj, "!fx_popup.ctg")
        assert os.path.exists(page)
        pb = parse_ctg(page).page
        assert pb.thumb_size > 0
        assert _records_sha(pb.objects) == _records_sha(_our_page(fxlib).page.objects)
        assert "!fx_popup" in w.statusBar().currentMessage()
        assert w.doc.dirty  # the placing page is not saved by it
    finally:
        for e in list(w._docs):
            e["doc"].dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.hide()
        w.deleteLater()


def test_saving_a_super_genie_recreates_open_pages_and_offers_update_pages(fxlib, tmp_path, monkeypatch):
    """Help 1172240: after a super genie is saved, the pages created from it
    are re-created -- the open ones at once (unsaved), the rest by the Update
    Pages the editor offers."""
    _qt()
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.ui.main_window import MainWindow

    proj = _project(fxlib, tmp_path, lib=True)
    host = _host(proj)
    host.place_library_element(*GENIE, "ctm", (100, 100))
    page = os.path.join(proj, "!fx_popup.ctg")
    lib = os.path.join(proj, "fxsuper.ctm")
    asked = []
    monkeypatch.setattr(MainWindow, "_exec_prompt", staticmethod(lambda box: asked.append(box.text())))
    monkeypatch.setattr(MainWindow, "_exec_supergenie_dialog", staticmethod(lambda dlg: "edit"))
    w = MainWindow()
    try:
        w.open_document(page)
        page_doc = w.doc
        assert page_doc.supergenie_status()[0] == "current"
        w._open_element(lib, SUPER)
        el = w.doc
        assert isinstance(el, ElementDocument) and el.kind == "supergenie"
        k = next(
            j
            for j, o in enumerate(el.parsed.page.objects)
            if o.type_code == 0x1001 and "WinFree" in str(o.raw_properties().get("CMD"))
        )
        assert el.set_property(k, "CMD", "WinFree(); Beep(1);")
        assert w._save_element_to()
        assert asked and "!fx_popup" in asked[0].lower()
        # the open page was re-created from the saved super genie, not saved
        assert page_doc.dirty and page_doc.supergenie_status()[0] == "current"
        assert "WinFree(); Beep(1);" in [
            str(o.raw_properties().get("CMD")) for o in page_doc.parsed.page.objects
        ]
        assert EditableDocument.open(page).supergenie_status()[0] == "stale"
    finally:
        for e in list(w._docs):
            e["doc"].dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.hide()
        w.deleteLater()


def test_placing_into_a_genie_window_creates_the_super_genie_page(fxlib, tmp_path):
    """Placing into a genie window creates the super genie page."""
    from ctgedit.editing import ElementDocument
    from ctgedit.io.project import Project

    proj = _project(fxlib, tmp_path, lib=True)
    el = ElementDocument.new_element(os.path.join(proj, "mylib.ctm"), "outer", "genie")
    i = el.place_library_element(*GENIE, "ctm", (10, 10))
    assert i is not None
    assert [(r["name"], r["status"]) for r in el.super_pages] == [("!fx_popup", "created")]
    assert os.path.exists(os.path.join(proj, "!fx_popup.ctg"))
    assert Project(proj).page_record("!fx_popup") is not None
    inc = _project(fxlib, tmp_path / "b", name="Include", lib=True)
    el2 = ElementDocument.new_element(os.path.join(inc, "mylib.ctm"), "outer", "genie")
    el2.place_library_element(*GENIE, "ctm", (10, 10))
    assert [r["status"] for r in el2.super_pages] == ["skipped"]
    assert not os.path.exists(os.path.join(inc, "!fx_popup.ctg"))


def test_attach_on_a_genie_never_saved_is_written_by_its_first_save(fxlib, tmp_path):
    """A NEW genie (File > New > Genie) whose super genies are attached
    before its first save: the first save stores the binds (new_element_content
    derives the import table from the records alone -- _with_attachments adds
    them, spelled as the element is) and creates the page."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import element as E
    from ctgedit.format import links

    proj = _project(fxlib, tmp_path, lib=True)
    lib = os.path.join(proj, "mylib.ctm")
    g = ElementDocument.new_element(lib, "fresh", "genie")
    g.create_object("rectangle", rect=(0, 0, 40, 20), colour=0x00FF00)
    assert g.set_attachments(["fxsuper.!FX_Popup"])
    g.save()
    pe = E.open_element(lib, "fresh")
    assert links.super_genie_attachments(links.parse_link_table(pe.page.link_table_raw)[1]) == [
        "fxsuper.!fx_popup"
    ]
    assert [(r["name"], r["status"]) for r in g.super_pages] == [("!fx_popup", "created")]


def test_revert_keeps_a_read_only_super_genie_page_read_only(fxlib, tmp_path, monkeypatch):
    """A page created from a super genie that was opened read-only stays
    read-only after File > Revert to Saved (and the reload prompt, which
    reverts the same way)."""
    _qt()
    from ctgedit.io import citect_config
    from ctgedit.ui.main_window import MainWindow

    proj = _project(fxlib, tmp_path)
    host = _host(proj)
    host.place_library_element(*GENIE, "ctm", (100, 100))
    page = os.path.join(proj, "!fx_popup.ctg")
    monkeypatch.setattr(citect_config, "allow_edit_super_genie_page", lambda: False)
    monkeypatch.setattr(MainWindow, "_exec_supergenie_dialog", staticmethod(lambda dlg: "view"))
    w = MainWindow()
    try:
        w.open_document(page)
        assert w.doc.path == page and w.doc.read_only
        first = w.doc
        assert w.revert_document(ask=False)
        assert w.doc is not first and w.doc.read_only
    finally:
        for e in list(w._docs):
            e["doc"].dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.hide()
        w.deleteLater()
