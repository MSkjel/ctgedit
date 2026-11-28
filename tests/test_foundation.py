"""Container, model and project reader basics."""

import glob
import os

import pytest

import fxinstall
from ctgedit.format.container import Container
from ctgedit.model.document import (
    GraphicObject,
    ObjectKind,
    Page,
    Visibility,
)

#: (project, page) of every page of the committed fake install (the byte checks read the
#: committed files, read only: a copy of the install per page would cost seconds each)
FX_PAGES = sorted(
    (os.path.basename(os.path.dirname(p)), os.path.splitext(os.path.basename(p))[0])
    for p in glob.glob(os.path.join(fxinstall.FX_INSTALL, "User", "*", "*.ctg"))
)
#: the pages in an old form (word2 9, 10, 11: format/upgrade reads them as the current one)
OLD_FORMS = {("FxMain", "FxOld9"), ("FxMain", "FxOld10"), ("FxMain", "FxOld11")}
CURRENT = [p for p in FX_PAGES if p not in OLD_FORMS]
#: the pages the oracle's 'pages' step saved through CtDraw (every page but FxPerf's)
ORACLE_PAGES = [p for p in FX_PAGES if p[0] in ("FxMain", "FxOther", "FxSim", "FxSa")]


def _ids(p):
    return "%s/%s" % p


def _committed(page):
    """The committed fixture file of (project, page), read only."""
    return os.path.join(fxinstall.FX_INSTALL, "User", page[0], page[1] + ".ctg")


def _tiles(data):
    """The container checks: blocks contiguous to EOF, the page and attributes blocks."""
    c = Container.parse(data)
    blocks = sorted(c.blocks, key=lambda b: b.offset)
    assert blocks, "no blocks parsed"
    # contiguous from first block to EOF
    cur = blocks[0].offset
    for b in blocks:
        assert b.offset == cur, f"gap/overlap at {b.name}"
        cur = b.end
    assert cur == len(data), "blocks do not tile to EOF"
    # every page file has page + attributes
    names = {b.name for b in blocks}
    assert "page" in names and "attributes" in names
    assert sorted(b.tag for b in c.blocks) == [0x0001, 0x0101]
    return c


def _text_kinds(fx_page):
    """FxTextKinds and the index of FxTxtOffLabel (the generator stores it as record 2)."""
    path = fx_page("FxTextKinds")
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(path)
    found = doc.find_objects_by_name("FxTxtOffLabel")
    assert len(found) == 1
    return path, doc, found[0]


# ----------------------------------------------------------------- container
@pytest.mark.parametrize("page", FX_PAGES, ids=_ids)
def test_container_tiles_to_eof(page):
    """Every fixture page tiles; the current ones are container version 9 (dword_c 8), the
    old forms the generator's downgrade wrote are version 8."""
    data = open(_committed(page), "rb").read()
    c = _tiles(data)
    assert c.version == (8 if page in OLD_FORMS else 9) and c.dword_c == 8


@pytest.mark.parametrize("page", FX_PAGES, ids=_ids)
def test_container_byte_exact_roundtrip(page):
    data = open(_committed(page), "rb").read()
    assert Container.parse(data).to_bytes() == data


def test_fixture_pages_are_there():
    """(The parametrised lists are not empty: the generator wrote the install.)"""
    assert len(FX_PAGES) >= 60 and OLD_FORMS <= set(FX_PAGES)
    assert len(ORACLE_PAGES) == 74


# ----------------------------------------------------------------- writer
@pytest.mark.parametrize("page", CURRENT, ids=_ids)
def test_full_reserialize_byte_exact(page):
    """Re-serializing parsed page+attributes structures reproduces the file exactly."""
    from ctgedit.format.page import parse_ctg
    from ctgedit.format.writer import serialize_ctg

    path = _committed(page)
    data = open(path, "rb").read()
    p = parse_ctg(path)
    assert serialize_ctg(p.container, p.page, p.attributes) == data


@pytest.mark.golden
@pytest.mark.parametrize("page", ORACLE_PAGES, ids=_ids)
def test_ctdraw_saved_page_tiles_and_reserializes(page):
    """CtDraw's PageOpen + PageSave of each fixture page: the container tiles (version 9,
    dword_c 8, the page and attributes blocks), parses back to its bytes, and the parsed
    page + attributes serialise to the bytes CtDraw wrote."""
    from ctgedit.format.page import parse_ctg
    from ctgedit.format.writer import serialize_ctg

    path = fxinstall.oracle("pages", "%s/%s" % page, "page.ctg")
    data = open(path, "rb").read()
    c = _tiles(data)
    assert c.version == 9 and c.dword_c == 8
    assert Container.parse(data).to_bytes() == data
    p = parse_ctg(path)
    assert serialize_ctg(p.container, p.page, p.attributes) == data


def test_structural_edit_roundtrips(tmp_path, fx_page):
    """Duplicating an object (the text FxTxtOffLabel) yields a valid, re-parseable .ctg with
    recomputed sizes."""
    import copy
    from ctgedit.format.page import parse_ctg
    from ctgedit.format.writer import serialize_ctg

    src, _doc, i = _text_kinds(fx_page)
    p = parse_ctg(src)
    n0 = len(p.page.objects)
    p.page.objects.insert(i + 1, copy.deepcopy(p.page.objects[i]))
    out = str(tmp_path / "_ctgedit_dup.ctg")
    with open(out, "wb") as f:
        f.write(serialize_ctg(p.container, p.page, p.attributes))
    q = parse_ctg(out)
    assert len(q.page.objects) == n0 + 1
    assert q.page.object_count == n0 + 1
    assert q.page.objdata_consumed == q.page.objdata_size  # tiles
    assert q.page.objects[-1].next_link == 0  # chain valid


def test_editing_move_undo_save_roundtrip(tmp_path, fx_page):
    from ctgedit.format.page import parse_ctg
    from ctgedit.format.writer import get_object_rect1

    _src, doc, i = _text_kinds(fx_page)
    before = get_object_rect1(doc.objects[i])
    doc.move_object(i, 25, -7)
    assert doc.dirty
    doc.undo()
    assert get_object_rect1(doc.objects[i]) == before
    doc.move_object(i, 25, -7)
    # Save As into a scratch folder (not a project): the fixture page is only read
    out = str(tmp_path / "_ctgedit_sess.ctg")
    doc.save(out)
    q = parse_ctg(out)
    l, t, r, b = get_object_rect1(q.page.objects[i])
    assert (l, t) == (before[0] + 25, before[1] - 7)
    assert q.page.objdata_consumed == q.page.objdata_size


def test_rerender_writes_no_temp_file(tmp_path, monkeypatch, fx_page):
    """The re-render derives the page in memory: no render file in %TEMP% (two ctgedit
    processes must never read each other's), and the page keeps the document's name."""
    import shutil
    import tempfile
    from ctgedit.editing import EditableDocument

    src = tmp_path / "Scratch.ctg"
    shutil.copy(fx_page("FxShapes"), src)
    doc = EditableDocument.open(str(src))
    tmp = tmp_path / "temp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp))
    doc._rerender_page()
    doc.set_colour(next(i for i, o in enumerate(doc.page.objects) if o.kind.value == "rectangle"), 0x123456)
    assert not list(tmp.iterdir())
    assert doc.page.name == "Scratch"


def test_property_edit_and_delete_roundtrip(tmp_path, fx_page):
    """On FxTextKinds: FxTxtOffLabel's TXT_OFF is edited, and FxLabelOne (a leaf after it)
    is deleted."""
    from ctgedit.format.page import parse_ctg

    _src, doc, i = _text_kinds(fx_page)
    assert doc.set_property(i, "TXT_OFF", "@(NEW)")
    doc.undo()
    assert doc.object_properties(i).get("TXT_OFF") == "@(Fx Start)"
    n = len(doc.objects)
    gone = doc.find_objects_by_name("FxLabelOne")
    assert len(gone) == 1 and gone[0] > i and not doc.page.objects[gone[0]].children
    doc.delete_object(gone[0])
    doc.set_property(i, "TXT_OFF", "@(SAVED)")
    # Save As into a scratch folder (not a project): the fixture page is only read
    out = str(tmp_path / "_ctgedit_prop.ctg")
    doc.save(out)
    q = parse_ctg(out)
    assert len(q.page.objects) == n - 1
    assert q.page.objdata_consumed == q.page.objdata_size
    assert q.page.objects[i].raw_properties().get("TXT_OFF") == "@(SAVED)"


# --------------------------------------------------------------------- model
def test_model_build_and_animations():
    p = Page(name="T")
    o = GraphicObject(kind=ObjectKind.ELLIPSE, an=7)
    o.animations.visibility = Visibility("Tag1")
    p.add(o)
    assert p.objects[0].an == 7
    assert p.objects[0].animations.visibility.expression == "Tag1"
    assert p.objects[0].geometry.transform.is_identity()


# ------------------------------------------------------------------- project
def test_project_tags_load(fx_project):
    """FxMain's variable.DBF holds 122 tags (the generator's FX_* and FX_T2xx blocks)."""
    from ctgedit.io.project import Project

    tags = Project(fx_project("FxMain")).variable_tags()
    assert len(tags) == 122
    assert all(t.name for t in tags)


# ------------------------------------------------------------------- symbols
def test_embedded_bitmap_decode_synthetic():
    """The inline DIB decoder on synthetic data."""
    import struct
    import zlib

    from ctgedit.format.symbols import embedded_bitmap_from_body

    # 2x2, top-down (negative height), 32bpp. Per pixel B,G,R,mask.
    hdr = struct.pack("<IiiHHIIiiII", 40, 2, -2, 1, 32, 0, 0, 0, 0, 0, 0)
    # px0 opaque blue, px1 transparent, px2 opaque red, px3 opaque green
    px = bytes(
        [
            200,
            0,
            0,
            0x00,  # B=200 -> opaque
            0,
            0,
            0,
            0xFF,  # mask 0xFF -> transparent
            0,
            0,
            200,
            0x00,  # R=200 -> opaque
            0,
            200,
            0,
            0x00,  # G=200 -> opaque
        ]
    )
    body = b"\x00" * 8 + zlib.compress(hdr + px)
    eb = embedded_bitmap_from_body(body, 0)
    assert eb is not None
    assert (eb.width, eb.height) == (2, 2)
    assert eb.has_alpha is True
    # pixel 0: B=200 in source -> R,G,B,A = 0,0,200,255 (opaque blue)
    assert tuple(eb.rgba[0:4]) == (0, 0, 200, 255)
    # pixel 1: mask 0xFF -> fully transparent
    assert eb.rgba[7] == 0
    # pixel 2: source R=200 -> R=200, opaque
    assert tuple(eb.rgba[8:12]) == (200, 0, 0, 255)
    # pixel 3: source G=200 -> G=200, opaque
    assert tuple(eb.rgba[12:16]) == (0, 200, 0, 255)


def test_embedded_bitmap_rejects_garbage():
    """The decoder never raises and returns None on non-DIB / truncated bodies."""
    from ctgedit.format.symbols import embedded_bitmap_from_body

    assert embedded_bitmap_from_body(b"", 0) is None
    assert embedded_bitmap_from_body(b"not a bitmap at all", 0) is None
    # a valid zlib stream that does not contain a BITMAPINFOHEADER
    import zlib

    assert embedded_bitmap_from_body(zlib.compress(b"hello world"), 0) is None


def test_symbol_library_resolves_and_has_embedded_art(fx_project):
    """A library element parses, and FxLib's fxsym.ctl fx_bitmap (14 x 23) carries real
    inline art."""
    from ctgedit.format.symbols import (
        SymbolLibrary,
        SymbolReference,
        SymbolResolver,
    )

    lib_dir = fx_project("FxLib")
    lib = SymbolLibrary.open(os.path.join(lib_dir, "fxsym.ctl"))
    assert "fx_bitmap" in lib.element_names()
    pb = lib.element("fx_bitmap")
    assert pb is not None and pb.objects
    # resolver finds it by reference
    r = SymbolResolver([lib_dir])
    rs = r.resolve(SymbolReference("fxsym", "fx_bitmap", "ctl", "symbol"))
    assert rs is not None
    # at least one element object decodes to a real embedded picture
    from ctgedit.format.symbols import embedded_bitmap_from_body
    from ctgedit.io.reader import read_geometry

    decoded = []
    for o in rs.objects:
        geo = read_geometry(o.body_raw, 0)
        ge = geo["end"] if geo else 0
        eb = embedded_bitmap_from_body(o.body_raw, ge)
        if eb is not None:
            decoded.append(eb)
    assert decoded, "no embedded bitmap decoded from fxsym.fx_bitmap"
    eb = decoded[0]
    assert (eb.width, eb.height) == (14, 23)
    assert len(eb.rgba) == eb.width * eb.height * 4


def test_reader_attaches_embedded_bitmaps_and_resolves_symbols(fx_page):
    """read_page wires the record forest (containers with inlined members),
    resolves genuine leaf symbol placements, and attaches inline art (FxMain's FxSymbols:
    group symbols, and FxBitmapSymbol, a linked fxsym.fx_bitmap)."""
    from ctgedit.io.reader import read_page

    page = read_page(fx_page("FxSymbols"))

    # container records (groups/genies) expose their inlined member subtrees
    containers = [o for o in page.objects if o.members]
    assert containers, "no group/genie containers wired on FxSymbols"
    # the forest is consistent: every member points back at its parent
    for ci, c in enumerate(page.objects):
        for m in c.members:
            assert page.objects[m].parent == ci

    # an inline bitmap was decoded somewhere (object or a resolved leaf child)
    def any_embedded(o):
        if o.raw.get("_embedded_bmp"):
            w, h, rgba, _ = o.raw["_embedded_bmp"]
            return w > 0 and h > 0 and len(rgba) == w * h * 4
        return any(any_embedded(c) for c in o.children)

    assert any(any_embedded(o) for o in page.objects), "no embedded bitmap reached on FxSymbols"
    assert any(o.children for o in page.objects), "no symbol resolved on FxSymbols"


def test_no_symbols_env_disables_resolution(monkeypatch, fx_page):
    """CTG_NO_SYMBOLS=1 and CTG_NO_EMBEDDED=1 fall back cleanly (placeholders)."""
    from ctgedit.io.reader import read_page

    monkeypatch.setenv("CTG_NO_SYMBOLS", "1")
    monkeypatch.setenv("CTG_NO_EMBEDDED", "1")
    page = read_page(fx_page("FxSymbols"))
    assert not any(o.children for o in page.objects)
    assert not any(o.raw.get("_embedded_bmp") for o in page.objects)
