"""Library reads that CtDraw's own writes can trip."""

import glob
import os
import shutil
import struct

import pytest

import fxinstall
from ctgedit.format import element as E
from ctgedit.format import factory, pagesummary as PS, symbols as S, thumbnail, writer
from ctgedit.format.container import Container
from ctgedit.format.textcodec import CODEPAGE

#: the code page 1252 element of fxmore.ctl, its bytes, and the element its set state 1 links
CP_ELEMENT = "fx_blå_sø"
CP_BYTES = CP_ELEMENT.encode("cp1252")
OTHER_STATE = b"fx_on"


def _head(data: bytes, off: int):
    objdata, count, thumb = struct.unpack_from("<III", data, off)
    links = struct.unpack_from("<I", data, off + 0x1C)[0]
    return objdata, count, thumb, links


def _direct_children(objs, i):
    """The indices of record ``i``'s direct children in a depth-first forest."""
    j = i + 1
    for _ in range(objs[i].class_flag):
        yield j
        j = E.root_tree_end(objs, j)


def _content(data: bytes, off: int) -> int:
    """A block's real extent from its head: 0x34 + links x 0x60 + records + thumbnail."""
    objdata, _n, thumb, links = _head(data, off)
    return 0x34 + links * 0x60 + objdata + thumb


# --------------------------------------------------------------------------- grown libraries
def _check_read_whole(lib_path, name, count=None, content=None):
    """Block ``name`` of the library at ``lib_path`` is grown (its directory size is short
    of its content) and every reader takes it whole."""
    with open(lib_path, "rb") as fh:
        data = fh.read()
    lib = S.SymbolLibrary.from_bytes(lib_path, data)
    blk = lib._find_block(name)
    if content is None:
        content = _content(data, blk.offset)
    assert blk is not None and blk.size < content
    assert len(blk.data) == content
    objdata, n, thumb, _links = _head(data, blk.offset)
    pb = lib.element(name)
    assert len(pb.objects) == n and pb.objdata_consumed == objdata
    if count is not None:
        assert n == count
    assert len(pb.thumbnail) == thumb
    _check_slot_rule(data)
    # the library panel / browser read the thumbnail from the directory + head
    d = PS.read_directory(lib_path)
    e = d.block(name)
    assert e.size == content
    es = PS.read_element_summary(lib_path, name, offset=e.offset, size=e.size, word2=d.word2)
    assert es is not None and es.has_thumbnail and es.object_count == n
    assert PS.read_element_summaries(lib_path, [name])[0].has_thumbnail
    return n


def _check_slot_rule(data):
    """The readers' rule (symbols.grown_block_end) is the writer's
    (element.LibraryFile._extent_end), slot for slot."""
    lf = E.LibraryFile(data)
    for s in lf.slots:
        if s.offset:
            end = S.grown_block_end(
                s.tag, s.offset, s.size, data[s.offset : s.offset + 0x20], len(data), lf.word2
            )
            assert end == lf._extent_end(s), s.name


def _grow_in_place(path, name, records):
    """Rewrite element ``name`` (the last block) of the library at ``path`` in place with
    ``records`` rectangles, past its directory size, as CtDraw does. Returns the content."""
    big = _element(records)
    lf = E.LibraryFile.open(path)
    old = lf.find(name)
    assert old.tag == 0x0101 and old.size < len(big), "not the last block, or not short"
    s = lf.write_element(name, big)
    assert s.tag == 0x0101 and s.size == old.size and s.offset == old.offset
    with open(path, "wb") as fh:
        fh.write(lf.to_bytes())
    return big


def test_a_grown_last_block_is_read_whole(fx_install):
    """fxmany.ctl's last block fx_last_big, rewritten in place with 120 rectangles past its
    directory size (as CtDraw grows it): the readers take it whole, the other 13 elements
    stay as they were, the element readers and the process cache agree."""
    lib = fx_install.file("FxLib", "fxmany.ctl")
    before = {
        n: len(S.SymbolLibrary.open(lib).element(n).objects)
        for n in S.SymbolLibrary.open(lib).element_names()
        if n != "fx_last_big"
    }
    assert len(before) == 13
    big = _grow_in_place(lib, "fx_last_big", 120)
    n = _check_read_whole(lib, "fx_last_big", content=len(big))
    pb = S.SymbolLibrary.open(lib).element("fx_last_big")
    root_end = E.root_tree_end(pb.objects, 0)
    assert root_end == n - 3  # one root tree + the 3-record trailer
    root = pb.objects[0]  # every direct child of the root is present
    assert root.class_flag == sum(1 for _ in _direct_children(pb.objects, 0)) == 120
    assert E.ElementTrailer.parse([writer.object_payload(o) for o in pb.objects[root_end:]]) is not None
    # the element the Graphics Builder's own reader (element.LibraryFile) sees
    lf = E.LibraryFile.open(lib)
    assert lf.block_data(lf.find("fx_last_big")) == big
    after = S.SymbolLibrary.open(lib)
    assert {k: len(after.element(k).objects) for k in before} == before
    assert len(E.open_element(lib, "fx_last_big").page.objects) == n
    assert len(S.open_library(lib).element("fx_last_big").objects) == n


@pytest.mark.golden
@pytest.mark.parametrize("unit", ["FxLib/fxmany.ctl", "append"])
def test_ctdraws_saved_library_is_read_whole(unit):
    """fxmany.ctl as CtDraw left it (its open + save of every element in order, and the
    PageSaveAsEx of fx_last_big as a new element): every element parses to its head's
    record count, the readers' extent rule is the writer's for every slot, and each block
    the directory makes short is read to its real extent."""
    path = fxinstall.oracle("elements", unit, "library.ctl")
    with open(path, "rb") as fh:
        data = fh.read()
    _check_slot_rule(data)
    lib = S.SymbolLibrary.from_bytes(path, data)
    names = lib.element_names()
    assert len(names) >= 14
    for name in names:
        blk = lib._find_block(name)
        objdata, n, thumb, _links = _head(data, blk.offset)
        assert len(blk.data) >= _content(data, blk.offset), name
        pb = lib.element(name)
        assert len(pb.objects) == n and pb.objdata_consumed == objdata, name
        if blk.size < _content(data, blk.offset):
            _check_read_whole(path, name)


@pytest.mark.golden
def test_ctf_of_a_page_placing_a_grown_element_is_ctdraws(fx_install):
    """The .ctF of a page placing a grown element is CtDraw's."""
    from ctgedit.format import ctf as CTF
    from ctgedit.format.page import parse_page

    a = fxinstall.oracle_bytes("place", "grown_element", "page.ctg")
    b = fxinstall.oracle_bytes("place", "grown_element", "page.ctF")
    g = os.path.join(fx_install.project("FxMain"), "FxOraclePlace.ctg")
    with open(g, "wb") as fh:
        fh.write(a)
    # (without the classic zlib: both with their DIBs deflated by ctf._compress)
    import ctfcompare

    out, ref = (Container.parse(x) for x in ctfcompare.comparable(CTF.build_ctf(a, g), b))
    ours = parse_page(out.block("page"), out.word2).objects
    theirs = parse_page(ref.block("page"), ref.word2).objects
    assert len(ours) == len(theirs)
    diff = [
        i
        for i, (x, y) in enumerate(zip(ours, theirs))
        if (x.type_code, x.subtype_size, x.body_raw) != (y.type_code, y.subtype_size, y.body_raw)
    ]
    assert diff == []


def test_page_directories_are_left_as_stored(fx_page):
    """A page's 0x0101 block is 'attributes' (no page head): never 'grown'."""
    for name in ("FxShapes", "FxGenies", "FxBig"):
        page = fx_page(name)
        with open(page, "rb") as fh:
            raw = fh.read(0x10 + 8 * 42)
        stored = {}
        for i in range(8):
            o = 0x10 + i * 42
            (tag,) = struct.unpack_from("<H", raw, o)
            off, size = struct.unpack_from("<II", raw, o + 34)
            if tag or off or size:
                stored[raw[o + 2 : o + 34].split(b"\0", 1)[0].decode("ascii")] = size
        assert stored, name
        got = {e.name: e.size for e in PS.read_directory(page).entries}
        assert got == stored, name


# --------------------------------------------------------------------------- synthetic
def _element(n: int) -> bytes:
    """An element of ``n`` rectangles with a real TH thumbnail."""
    recs = [
        factory.create_object("rectangle", (10 + 6 * i, 10, 14 + 6 * i, 40), colour=0x00FF0000, handle=i + 1)
        for i in range(n)
    ]
    tw, th = 16, 8
    return E.new_element_content(recs, thumbnail=thumbnail.encode(b"\x80" * (tw * th * 4), tw, th))


def _grown_library(tmp_path, ext: str = ".ctl", appendix: bytes = b"") -> tuple:
    small, big = _element(2), _element(40)
    assert len(big) > len(small) + E.PAD + 0x100
    lf = E.LibraryFile.new()
    lf.write_element("first", small)
    lf.write_element("grow", small)  # the last block: tag 0x0101, size small + pad
    s = lf.write_element("grow", big)  # CtDraw rewrites it in place past that size
    assert s.tag == 0x0101 and s.size == len(small) + E.PAD
    data = lf.to_bytes() + appendix
    assert len(data) == s.offset + len(big) + len(appendix)
    path = tmp_path / ("grown" + ext)
    path.write_bytes(data)
    return str(path), data, s, big


@pytest.mark.parametrize(
    "appendix", [b"", b"FingerPrint Ext Prop" + b"\0" * 44], ids=["file-ends-at-content", "with-appendix"]
)
def test_synthetic_grown_block_parses_whole(tmp_path, appendix):
    path, data, s, big = _grown_library(tmp_path, appendix=appendix)
    old = Container.parse(data).block("grow")
    assert len(old.data) == s.size < len(big)  # what a directory-size slice gave
    lib = S.SymbolLibrary.open(path)
    blk = lib._find_block("grow")
    assert blk.data == big  # the content, not the appendix
    pb = lib.element("grow")
    objdata, n, thumb, _links = _head(big, 0)
    assert len(pb.objects) == n and pb.objdata_consumed == objdata
    assert len(pb.thumbnail) == thumb
    assert E.root_tree_end(pb.objects, 0) == n - 3  # root + 40 rects, then the trailer
    assert (
        lib.element("first") is not None
        and len(lib._find_block("first").data) == lib._find_block("first").size
    )
    # the process cache and the element readers agree
    assert len(S.open_library(path).element("grow").objects) == n
    pe = E.open_element(path, "grow")
    assert len(pe.page.objects) == n
    # the directory / thumbnail readers
    e = PS.read_directory(path).block("grow")
    assert e.size == len(big)
    es = PS.read_element_summary(path, "grow")
    assert es.ok and es.has_thumbnail and es.object_count == n
    assert es.thumbnail_size == (16, 8)


def test_synthetic_grown_block_in_a_page_file_is_not_grown(tmp_path):
    path, data, s, big = _grown_library(tmp_path, ext=".ctg")
    lib = S.SymbolLibrary.open(path)
    assert len(lib._find_block("grow").data) == s.size
    assert PS.read_directory(path).block("grow").size == s.size


def test_grown_block_end_rule():
    head = struct.pack("<III", 1000, 5, 200) + b"\0" * 16 + struct.pack("<I", 2)
    need = 0x34 + 2 * 0x60 + 1000 + 200
    # grown in place: the head's content runs past the directory size and fits the file
    assert S.grown_block_end(0x0101, 400, 600, head, 400 + need, 0x0C) == 400 + need
    assert S.grown_block_end(0x0101, 400, 600, head, 400 + need + 64, 0x0C) == 400 + need
    # an unflagged in-use block too (the flag goes at the next append, the size stays;
    # CtDraw reads by the head: LIVE); not a deleted slot / content past the
    # file / legacy header / file ends there
    assert S.grown_block_end(0x0001, 400, 600, head, 400 + need, 0x0C) == 400 + need
    assert S.grown_block_end(0x0100, 400, 600, head, 400 + need, 0x0C) == 1000
    assert S.grown_block_end(0x0101, 400, 600, head, 400 + need - 1, 0x0C) == 1000
    assert S.grown_block_end(0x0101, 400, 600, head, 400 + need, 0x09) == 1000
    assert S.grown_block_end(0x0101, 400, 600, head, 1000, 0x0C) == 1000
    # a block whose directory size already covers the content keeps it (stale slack)
    assert S.grown_block_end(0x0101, 400, need + 50, head, 400 + need + 50, 0x0C) == 400 + need + 50


# --------------------------------------------------------------------------- cp1252 references
def _lpstr(text: bytes) -> bytes:
    return struct.pack("<H", len(text)) + text


def test_reference_with_ansi_letters_parses_framed():
    ref_text = b"fxmore.32_fx_gr\xe5.<ctl"
    body = b"\x00\x00\x07\x00" + _lpstr(ref_text) + b"\x01\x00\x00\x00"
    ref = S.reference_from_body(body)
    assert ref is not None
    assert (ref.library, ref.element, ref.ext) == ("fxmore", "32_fx_gr\u00e5", "ctl")
    start, end = S.body_reference_span(body)
    assert (start, end) == (4, 6 + len(ref_text))
    assert body[start + 2 : end] == ref_text
    ref = S.reference_from_body(b"\xff" + _lpstr(b"_fxparts.Fx_l\xe5s_value.<ctm") + b"\0")
    assert (ref.library, ref.element, ref.ext) == ("_fxparts", "Fx_l\u00e5s_value", "ctm")
    # ae / oe / aa in upper and lower case
    ref = S.reference_from_body(_lpstr(b"lib.\xc6\xd8\xc5_\xe6\xf8\xe5.<ctl"))
    assert ref.element == "\u00c6\u00d8\u00c5_\u00e6\u00f8\u00e5"


def test_reference_with_ansi_letters_parses_in_the_fallback_walk():
    # no length frame (a broken prefix): the walk back over the text bytes
    body = b"\x00\x00\x99\x99fxmore.32_fx_gr\xe5.<ctl\x00"
    ref = S.reference_from_body(body)
    assert ref is not None and ref.element == "32_fx_gr\u00e5"
    assert S.body_reference_span(body) is None  # editing never guesses


@pytest.mark.skipif(CODEPAGE != 1252, reason="Windows-1252 byte table")
def test_undefined_cp1252_bytes_are_not_reference_text():
    # 0x81 is not a character of Windows-1252: no frame across it, the walk stops there
    body = _lpstr(b"lib.a\x81b.<ctl")
    assert S.body_reference_span(body) is None
    assert S.reference_from_body(body) is None  # 'b.<ctl' names no element
    assert not S._all_printable(b"a\x81b", 0, 3)
    assert S._all_printable(b"gr\xe5 \x80\x9f", 0, 6)


def test_resolver_finds_elements_named_with_ansi_letters(fx_install):
    lib_dir = fx_install.project("FxLib")
    r = S.SymbolResolver([lib_dir])
    for text in (b"fxmore." + CP_BYTES + b".<ctl", b"fxmore.FX_BL\xc5_S\xd8.<ctl"):
        res = r.resolve(S.reference_from_body(_lpstr(text)))
        assert res is not None, text
        assert os.path.normcase(res.library_path) == os.path.normcase(fx_install.file("FxLib", "fxmore.ctl"))
        assert res.objects  # the element's art


def test_symbol_references_with_ansi_letters_resolve_on_a_page(fx_install):
    """FxSymbols2's FxCp1252Symbol places fxmore.fx_blå_sø: the reference parses, so the
    symbol resolves and draws its art. Found by content, not by index (the case-folded
    spelling is the resolver test's)."""
    from ctgedit.format.page import parse_ctg

    p = fx_install.page("FxSymbols2")
    objs = parse_ctg(p).page.objects
    hits = [o for o in objs if o.subtype_size & 0x1 and CP_BYTES + b".<ctl" in o.body_raw]
    assert len(hits) == 1
    r = S.SymbolResolver.for_page(p)
    for o in hits:
        ref = S.reference_from_object(o)
        assert ref is not None and ref.ext == "ctl" and ref.library == "fxmore"
        assert ref.element == CP_ELEMENT
        assert S.body_reference_span(o.body_raw) is not None
        res = r.resolve(ref)
        assert res is not None and res.objects


def test_template_set_states_resolve(fx_install):
    """A template's symbol set (fx_alt sets@-XGA, FxTplSet) whose state 0 links a symbol
    with code page 1252 letters in its name (fxmore.fx_blå_sø) and state 1 fxsym.fx_on.
    State 0 parses, so the set shows its own symbol."""
    r = S.SymbolResolver.for_page(fx_install.page("FxTplSets"))
    tpl = r.libraries("fx_alt", "ctt")[0].element("sets@-XGA")
    found = {}
    for o in tpl.objects:
        for key in (CP_BYTES, OTHER_STATE):
            if key + b".<ctl" in o.body_raw:
                ref = S.reference_from_object(o)
                assert ref is not None and ref.element.encode("cp1252") == key
                assert S.body_reference_span(o.body_raw) is not None
                res = r.resolve(ref)
                assert res is not None and res.objects
                found[key] = o
    assert set(found) == {CP_BYTES, OTHER_STATE}


def test_page_set_shows_the_state_0_symbol(fx_install):
    """A page set shows the state 0 symbol."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from ctgedit.format.page import parse_ctg
    from ctgedit.io import reader
    from ctgedit.model.document import ObjectKind
    from ctgedit.render.scene import PageScene

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = fx_install.page("FxTplSets")
    pg, der = reader.derive_page(parse_ctg(page), page, records_fresh=True)
    first = [i for i, tw in der.tpl_map.items() if CP_BYTES + b".<ctl" in tw.body_raw]
    other = [i for i, tw in der.tpl_map.items() if OTHER_STATE + b".<ctl" in tw.body_raw]
    assert len(first) == 1 and len(other) == 1
    assert [(der.tpl_map[i].type_code, der.tpl_map[i].subtype_size) for i in first + other] == [
        (0x08, 0x19),
        (0x08, 0x19),
    ]
    g, b = pg.objects[first[0]], pg.objects[other[0]]
    assert g.parent == b.parent  # the two states of one symbol set
    (art,) = g.members
    (hidden,) = b.members
    a = pg.objects[art]
    # state 0 draws its art: its member, styled from its template twin
    assert a.kind is ObjectKind.RECTANGLE and a.style.filled and a.style.fill_colour == 0x2080A0
    assert not g.raw.get("_state_hidden") and not a.raw.get("_state_hidden")
    assert b.raw.get("_state_hidden") and pg.objects[hidden].raw.get("_state_hidden")
    sc = PageScene()
    sc.set_page(pg)
    assert art in sc._items and hidden not in sc._items
    assert sc._items[art].isVisible()


def test_an_append_fixes_the_grown_blocks_size(tmp_path):
    """An append fixes the grown block's size."""
    path, data, s, big = _grown_library(tmp_path)
    lf = E.LibraryFile.open(path)
    t = lf.write_element("third", _element(3))
    assert t.tag == 0x0101
    g = lf.find("grow")
    assert g.tag == 0x0001 and g.size == len(big)
    assert lf.block_data(g) == big
    out = tmp_path / "appended.ctl"
    out.write_bytes(lf.to_bytes())
    for name in ("first", "grow", "third"):
        assert S.SymbolLibrary.open(str(out)).element(name) is not None, name
    # a library whose append (CtDraw's own: it keeps the stale size) left the grown
    # block unflagged: every reader takes the head's extent, as CtDraw does
    stale = bytearray(lf.to_bytes())
    for i in range(8):
        off = E.HEADER_FIXED + i * E.ENTRY_SIZE
        if bytes(stale[off + 2 : off + 2 + E.NAME_LEN]).split(b"\0", 1)[0] == b"grow":
            struct.pack_into("<I", stale, off + 6 + E.NAME_LEN, s.size)
    out2 = tmp_path / "stale.ctl"
    out2.write_bytes(bytes(stale))
    lf2 = E.LibraryFile.open(str(out2))
    g2 = lf2.find("grow")
    assert g2.tag == 0x0001 and g2.size == s.size < len(big)
    assert lf2.block_data(g2) == big
    assert S.SymbolLibrary.open(str(out2))._find_block("grow").data == big
    assert next(e for e in PS.read_directory(str(out2)).entries if e.name == "grow").size == len(big)
    nest = os.path.join(os.path.dirname(__file__), "data", "nested_chain", "_nest8.ctm")
    if os.path.exists(nest):
        from ctgedit.editing import ElementDocument
        from ctgedit.format.element import open_element
        from ctgedit.format.pagesummary import list_element_names, read_directory

        lib = str(tmp_path / "_nest8.ctm")
        shutil.copyfile(nest, lib)
        el = ElementDocument.open_element(lib, "n4")
        el.save(name="n4copy")
        counts = {n: len(open_element(lib, n).page.objects) for n in list_element_names(lib)}
        assert counts["n4"] == counts["n4copy"] == 13, counts
        n4 = next(e for e in read_directory(lib).entries if e.name == "n4")
        assert (hex(n4.tag), n4.size) == ("0x1", 52508)


# --------------------------------------------------------------------------- CtDraw's load + append
def _dir(data: bytes, name: bytes):
    """(tag, offset, size) of the directory entry named ``name``."""
    n = struct.unpack_from("<I", data, 12)[0]
    for i in range(n):
        o = E.HEADER_FIXED + i * E.ENTRY_SIZE
        if data[o + 2 : o + 2 + E.NAME_LEN].split(b"\0", 1)[0] == name:
            (tag,) = struct.unpack_from("<H", data, o)
            off, size = struct.unpack_from("<II", data, o + 2 + E.NAME_LEN)
            return tag, off, size
    return None


def test_an_append_goes_to_the_physical_end_past_a_stale_tail():
    """places a new block at the physical end of the file: the stale tail a shorter
    rewrite left stays in front of it."""
    lf = E.LibraryFile.new()
    lf.write_element("a", _element(2))
    stale = b"\xa5" * 777
    data = lf.to_bytes() + stale
    lf = E.LibraryFile(data)
    t = lf.write_element("b", _element(3))
    out = lf.to_bytes()
    assert t.offset == len(data) and t.size == len(_element(3)) + E.PAD
    assert out[len(data) - len(stale) : len(data)] == stale
    assert _dir(out, b"a")[0] == 0x0001 and _dir(out, b"b")[0] == 0x0101
    lib = S.SymbolLibrary.from_bytes("x.ctl", out)
    assert lib.element("a") is not None and lib.element("b") is not None


def test_a_shorter_rewrite_of_the_grown_block_keeps_the_file_length(tmp_path):
    """CtDraw writes the content in place and never shortens the file: the grown block's
    old bytes past the new content stay as a stale tail; the size is kept."""
    path, data, s, big = _grown_library(tmp_path)
    mid = _element(20)
    assert s.size < len(mid) < len(big)
    lf = E.LibraryFile(data)
    g = lf.write_element("grow", mid)
    out = lf.to_bytes()
    assert len(out) == len(data) and g.size == s.size and g.tag == 0x0101
    assert out[s.offset : s.offset + len(mid)] == mid
    assert out[s.offset + len(mid) :] == data[s.offset + len(mid) :]
    assert lf.block_data(lf.find("grow")) == mid
    # the next append lands past that tail, and the grown block's size becomes the gap
    t = lf.write_element("next", _element(1))
    assert t.offset == len(data)
    assert lf.find("grow").size == len(data) - s.offset


def test_the_first_change_retiles_the_directory_as_ctdraws_load():
    """in offset order every entry takes the gap to the next as its size,
    only the last in-use entry keeps the 'last block' byte, an entry with a size but no
    offset is zeroed. CtDraw writes that directory at close."""
    lf = E.LibraryFile.new()
    for n in ("a", "b", "c"):
        lf.write_element(n, _element(2))
    good = lf.to_bytes()
    bad = bytearray(good)
    _ta, oa, sa = _dir(good, b"a")
    o = next(
        E.HEADER_FIXED + i * E.ENTRY_SIZE
        for i in range(8)
        if good[E.HEADER_FIXED + i * E.ENTRY_SIZE + 2 :][:2] == b"a\0"
    )
    struct.pack_into("<H", bad, o, 0x0101)  # a non-last block flagged
    struct.pack_into("<I", bad, o + 6 + E.NAME_LEN, sa - 64)  # and short of its gap
    j = E.HEADER_FIXED + 7 * E.ENTRY_SIZE  # a sized entry without an offset
    struct.pack_into("<H", bad, j, 0x0000)
    struct.pack_into("<I", bad, j + 6 + E.NAME_LEN, 99)
    lf = E.LibraryFile(bytes(bad))
    assert lf.to_bytes() == bytes(bad)  # a read changes nothing
    lf.write_element("b", _element(2))  # an in-place rewrite
    out = lf.to_bytes()
    assert _dir(out, b"a") == (0x0001, oa, sa)
    assert _dir(out, b"c")[0] == 0x0101
    assert out[j : j + E.ENTRY_SIZE] == b"\0" * E.ENTRY_SIZE
    # libraries our writer made are already tiled: the rule changes none of them (the
    # shipped Include libraries: tests_corpus/test_library_growth.py)
    inc = fxinstall.FX_INSTALL
    for f in sorted(glob.glob(os.path.join(inc, "User", "*", "*.ct[lmt]"))):
        raw = open(f, "rb").read()
        lf = E.LibraryFile(raw)
        lf._retile()
        assert lf.to_bytes() == raw and not lf._dirty, f


def _appendix() -> bytes:
    rec = b"Library.Comment".ljust(32, b"\0") + struct.pack("<I", 5) + b"hello"
    return E.EXT_FINGERPRINT.ljust(32, b"\0") + rec


def test_the_appendix_is_written_again_after_a_changed_directory():
    """ctgClose with a changed directory cuts the file at ctgInternalFileSize (the content
    end of the 'last block') and writes the external properties there again; the copy an
    append went past stays behind. An in-place rewrite leaves the file as it is."""
    lf = E.LibraryFile.new()
    ca = _element(2)
    lf.write_element("a", ca)
    ext = _appendix()
    # CtDraw writes it at the content end of the 'last block' (over its pad)
    data = lf.to_bytes()[: lf._content_end(lf.find("a"))] + ext
    lf = E.LibraryFile(data)
    assert lf.appendix == ext
    lf.write_element("a", ca)
    assert lf.to_bytes() == data  # not dirty: as it was
    content = _element(3)
    t = lf.write_element("b", content)
    out = lf.to_bytes()
    assert t.offset == len(data)
    assert out[len(data) - len(ext) : len(data)] == ext  # the old copy, stale now
    assert out[len(data) :] == content + ext  # cut at the content end
    back = E.LibraryFile(out)
    assert back.appendix == ext
    assert back.block_data(back.find("b"))[: len(content)] == content
    # Pack Libraries keeps the 'Library.' properties
    from ctgedit.format import projects

    assert projects._library_properties(back) == ext
