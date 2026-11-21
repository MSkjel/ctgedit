"""The .ctF writer, byte-exact against CtDraw."""

import os
import time

import pytest

import ctfcompare
import fxinstall
from ctgedit.format import ctf as CTF
from ctgedit.format.container import Container
from timing import check_speed

#: oracle units ('pages' step) whose .ctF the writer reproduces byte for byte
EXACT = [
    ("FxMain", "FxShapes"),
    ("FxMain", "FxSymbols"),
    ("FxMain", "FxGenies"),
    ("FxMain", "FxColours"),
    ("FxMain", "FxStyles"),
    ("FxMain", "FxTextKinds"),
    ("FxMain", "FxCicode"),
    ("FxMain", "FxAnim"),
]


def _pair(fx_install, pj, page):
    """CtDraw's saved .ctg + .ctF of ``pj``/``page`` (the oracle); the .ctg is written over
    the fixture page of the fx_install copy, so the writer resolves its libraries there."""
    unit = "%s/%s" % (pj, page)
    a = fxinstall.oracle_bytes("pages", unit, "page.ctg")
    b = fxinstall.oracle_bytes("pages", unit, "page.ctF")
    g = fx_install.page(page, pj)
    with open(g, "wb") as fh:
        fh.write(a)
    ca, cb = Container.parse(a), Container.parse(b)
    # CtDraw saves the pair together (the .ctg, then the .ctF): the same page
    # version, the .ctF's save stamps the same second or a later one (FxShapes and FxCicode
    # were written a second apart in the capture)
    assert ctfcompare.page_identity(a)[4:] == ctfcompare.page_identity(b)[4:]
    assert 0 <= cb.ts_modified - ca.ts_modified <= 2
    # CtDraw's load re-composed every genie, so its save is current with the fixture's
    # libraries (an art-stripped member's look comes from the CURRENT element)
    from ctgedit.editing import EditableDocument

    assert not EditableDocument.open(g).genie_changes()
    return g, a, b


@pytest.mark.golden
@pytest.mark.parametrize("pj,page", EXACT)
def test_ctf_byte_exact(fx_install, pj, page):
    """Byte for byte but the save stamps of the .ctF (the same as the .ctg's when CtDraw
    wrote both in one second) and, without the classic zlib, the deflate of each DIB
    (tests/ctfcompare.py)."""
    g, a, b = _pair(fx_install, pj, page)
    ctfcompare.assert_same_ctf(CTF.build_ctf(a, g), b, stamps=True)


def test_bake_nothing_keeps_every_record(fx_page):
    """bake=False: a page-colour bitmap + every record (CtDraw's own shape for
    an all-dynamic page). On FxShapes (one of each primitive)."""
    g = fx_page("FxShapes")
    with open(g, "rb") as fh:
        a = fh.read()
    from ctgedit.format.page import parse_page

    c = Container.parse(a)
    n = len(parse_page(c.block("page"), c.word2).objects)
    assert n > 20
    out = Container.parse(CTF.build_ctf(a, g, bake=False))
    objs = parse_page(out.block("page"), out.word2).objects
    assert len(objs) == n + 1 and objs[0].type_code == 0


@pytest.mark.parametrize("page", ["FxLarge", "FxHuge"])
def test_ctf_speed_large_page(fx_page, page):
    """FxPerf's FxLarge (about 1,200 records: pump and pipe genies, groups, styled shapes,
    texts) and FxHuge (5,000 rectangles and texts) bake within the bound."""
    g = fx_page(page, "FxPerf")
    with open(g, "rb") as fh:
        a = fh.read()
    CTF.build_ctf(a, g)  # warm the library element caches
    t = time.perf_counter()
    CTF.build_ctf(a, g)
    check_speed(time.perf_counter() - t, 1.5, "the bake")


def test_a_turned_or_mirrored_bitmap_bakes_in_its_orientation():
    """A turned or mirrored bitmap bakes in its orientation."""
    from array import array

    w, h = 3, 2
    px = array("I", [1, 2, 3, 4, 5, 6]).tobytes()  # rows (1 2 3) / (4 5 6)

    def grid(W, H, b):
        a = array("I", b)
        return [list(a[y * W : (y + 1) * W]) for y in range(H)]

    assert grid(3, 2, CTF._orient_pixels(w, h, px, -1, 0, 0, 1)[2]) == [[3, 2, 1], [6, 5, 4]]
    assert grid(3, 2, CTF._orient_pixels(w, h, px, 1, 0, 0, -1)[2]) == [[4, 5, 6], [1, 2, 3]]
    assert grid(3, 2, CTF._orient_pixels(w, h, px, -1, 0, 0, -1)[2]) == [[6, 5, 4], [3, 2, 1]]
    W, H, cw = CTF._orient_pixels(w, h, px, 0, 1, -1, 0)  # x' = -y, y' = x: clockwise
    assert (W, H) == (2, 3) and grid(W, H, cw) == [[4, 1], [5, 2], [6, 3]]
    W, H, ccw = CTF._orient_pixels(w, h, px, 0, -1, 1, 0)
    assert grid(W, H, ccw) == [[3, 6], [2, 5], [1, 4]]
    assert CTF._orient_pixels(w, h, px, 0.7, 0.7, -0.7, 0.7) is None


@pytest.mark.golden
def test_the_trust_check_ignores_session_state(fx_install):
    """The .ctF trust check ignores session state."""
    from ctgedit.editing_base import ctf_matches

    g, a, disk = _pair(fx_install, "FxMain", "FxActiveX")
    # (without the classic zlib: both with their DIBs deflated by ctf._compress)
    ours, disk = ctfcompare.comparable(CTF.build_ctf(a, g), disk)
    assert ctf_matches(ours, disk)
    c = Container.parse(disk)
    ranges = CTF.session_ranges(disk, c)
    assert len(ranges) > 1  # the background + a storage
    for a0, _b0 in ranges[:2]:
        t = bytearray(ours)
        t[a0] ^= 0xFF
        assert ctf_matches(bytes(t), disk)
    blk = c.block("page")
    t = bytearray(ours)
    t[blk.offset + blk.size - 1] ^= 0xFF  # the page's last byte
    assert not ctf_matches(bytes(t), disk)


def test_an_out_of_date_ctf_is_written_again(tmp_path, fx_page):
    """A .ctF older than a library the page is composed from shows the page as it was:
    CtDraw's save writes a new one, and so does ctgedit's. Not when nothing changed.
    On FxGenies (composed from fxgenie.ctm and fxnest.ctm)."""
    from ctgedit.doc.history import ctf_out_of_date
    from ctgedit.format.symbols import SymbolResolver

    g = fx_page("FxGenies")
    with open(g, "rb") as fh:
        prev = fh.read()
    old = tmp_path / "old.ctF"
    old.write_bytes(b"x")
    os.utime(old, (1_000_000, 1_000_000))  # 1970: older than every library
    res = SymbolResolver.for_page(g)
    assert ctf_out_of_date(prev, str(old), res) == "a library changed since"
    os.utime(old, (time.time() + 86400, time.time() + 86400))
    assert ctf_out_of_date(prev, str(old), res) == ""


def test_a_translatable_texts_sweep_is_centred_on_its_grown_bounds():
    """grows the region by the sweep before it restores the 1.3x transform of a translatable
    text, so a size animation's centre (and a rotation's origin) is taken from the GROWN
    bounds (inside): scaled about its right edge, an '@(' label's
    region keeps the grown right edge, not the ungrown one."""
    from ctgedit.format import factory, writer

    po = factory.create_object("text", (100, 100, 180, 120), text="@(Pump running)", handle=1)
    for k, v in (
        ("TXTMODE", "0"),
        ("TXT_OFF", "@(Pump running)"),
        ("SZX", "Tag1"),
        ("SZXORG", "1"),
        ("SZXMODE", "0"),
        ("SZXMINPC", "0"),
        ("SZXMAXPC", "200"),
    ):
        writer.ensure_object_property(po, k, v)
    r0 = GP_decode(po)
    o = CTF._Obj(po, r0, CTF.ctf_rec(r0, None))
    flags = CTF._anim_flags(o)
    grown = CTF._inflate3d(o, CTF._map_box(CTF._grow_map(o), o.rect1))
    assert grown[2] > o.bounds[2]  # the label grows to the right
    region = CTF._text_region(o, flags)
    assert region[2] == grown[2]  # the centre: the grown right edge
    pre = CTF._union(
        CTF._text_ext(o, "@(Pump running)", CTF._grow_map(o)), CTF._text_ext(o, "", CTF._grow_map(o))
    )
    pre = CTF._union(pre, CTF._text_ext(o, "Wg", CTF._grow_map(o)))
    assert CTF._expand(o, pre, flags)[1][2] != region[2]  # the ungrown centre differs


def GP_decode(po):
    from ctgedit.format import genie_place as GP

    return GP.decode(po)


#: one fixture page per record class the byte-exact pins above do not cover: the .ctF
#: the writer builds equals CtDraw's up to the save stamps and the session state
CLASS_PINS = [
    ("FxMain", "FxLegacy"),  # V3 / V4 records, web content, ActiveX, gauges
    ("FxMain", "FxActiveX"),  # ActiveX (its storage's save times)
    ("FxMain", "FxButtons"),  # buttons, touch and keyboard rows
    ("FxMain", "FxInputs"),  # input records on every kind of owner
    ("FxSim", "FxSimTrend"),  # a trend with eight pens
    ("FxMain", "FxMotion"),
]  # rotation, scaling, movement and slider keys


@pytest.mark.golden
@pytest.mark.parametrize("pj,page", CLASS_PINS)
def test_ctf_reproduced_per_class(fx_install, pj, page):
    g, a, b = _pair(fx_install, pj, page)
    ctfcompare.assert_same_ctf(CTF.build_ctf(a, g), b, session=True)


def test_the_comparison_sees_the_pixels_not_the_deflate():
    """tests/ctfcompare.py: without the classic zlib the two files compare with every DIB
    deflated again by ctf._compress, so another deflate of the same pixels is equal and
    one changed pixel is not. On CtDraw's .ctF of FxShapes (its background and its
    bitmaps)."""
    import struct
    import zlib

    disk = fxinstall.oracle_bytes("pages", "FxMain/FxShapes", "page.ctF")
    c, blk, start, payloads, end = ctfcompare.records(disk)
    secs = ctfcompare.dib_sections(payloads[0])
    assert len(secs) == 1 and len([s for p in payloads for s in ctfcompare.dib_sections(p)]) >= 2

    def background(raw):
        a, b, flags, _raws = secs[0]
        p = payloads[0]
        z = zlib.compress(raw, 9)
        new = p[:a] + struct.pack("<III", flags, len(raw), len(z)) + z + p[b:]
        out = bytearray()
        for k, q in enumerate([new] + payloads[1:]):
            nxt = sum(8 + len(x) for x in ([new] + payloads[1:])[: k + 1])
            out += struct.pack("<II", len(q), nxt if k + 1 < len(payloads) else 0) + q
        head = bytearray(blk.data[:start])
        struct.pack_into("<I", head, 0, len(out))
        cc = Container.parse(disk)
        cc.block("page").data = bytes(head) + bytes(out) + blk.data[end:]
        cc.relayout()
        return cc.to_bytes()

    raw = secs[0][3][0]
    same = background(raw)
    assert same != disk  # another deflate: other bytes
    changed = bytearray(raw)
    changed[-4] ^= 0xFF  # one pixel
    other = background(bytes(changed))
    if ctfcompare.has_classic_zlib():
        assert not ctfcompare.same_ctf(same, disk)
    else:
        assert ctfcompare.same_ctf(same, disk)
    assert not ctfcompare.same_ctf(other, disk)
    assert ctfcompare.same_ctf(disk, disk)


def test_a_bitmaps_dibs_are_deflated_again_as_ctdraw_saves_them():
    """A bitmap's DIBs are deflated again, as CtDraw saves them."""
    import struct
    import zlib

    raw1 = bytes(range(256)) * 40
    raw2 = bytes(reversed(range(256))) * 40
    z1, z2 = zlib.compress(raw1, 1), zlib.compress(raw2, 9)
    rect = struct.pack("<4h", 0, 0, 16, 16)
    part = rect + struct.pack("<III", 0x101, len(raw1), len(z1)) + z1 + struct.pack("<I", len(z2)) + z2
    out = CTF.bitmap_redeflated(part, 0)
    y1, y2 = CTF._compress(raw1), CTF._compress(raw2)
    assert out == rect + struct.pack("<III", 0x101, len(raw1), len(y1)) + y1 + struct.pack("<I", len(y2)) + y2
    # not deflated, or not a stream: as it is
    plain = rect + struct.pack("<III", 0x100, 4, 4) + b"abcd"
    assert CTF.bitmap_redeflated(plain, 0) == plain
    broken = rect + struct.pack("<III", 0x1, len(raw1), 3) + b"xyz"
    assert CTF.bitmap_redeflated(broken, 0) == broken


def test_an_empty_scaled_region_sweeps_nothing(fx_page):
    """An empty scaled region sweeps nothing."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxMotion"))
    # far off the page: no other object's sweep reaches here
    mover = doc.create_object("rectangle", rect=(20000, 20000, 20060, 20040), colour=0x336699)
    for k, v in (
        ("SZY", "FX_LEVEL2"),
        ("SZYORG", "-1"),
        ("MVY", "FX_LEVEL2"),
        ("MVYMINPT", "0"),
        ("MVYMAXPT", "-4000"),
    ):
        assert doc.ensure_property(mover, k, v)
    below = doc.create_object("rectangle", rect=(20000, 20100, 20060, 20130), colour=0x336699)
    baked, _keep, _par, _drawn = CTF.bake_plan(doc.parsed.page.objects)
    assert mover not in baked and below in baked
    # with a percentage the scaled box is not flat: its sweep reaches the rectangle below
    assert doc.ensure_property(mover, "SZYMAXPC", "100")
    baked, _keep, _par, _drawn = CTF.bake_plan(doc.parsed.page.objects)
    assert below not in baked
    assert CTF._or(CTF.EMPTY, None) == CTF.EMPTY and CTF._or(None, None) is None
    assert CTF._or((1, 1, 1, 9), (2, 2, 5, 5)) == (2, 2, 5, 5)


def test_an_art_stripped_metafile_is_drawn_from_its_element(fx_page):
    """The MetaFile draw plays the IN-MEMORY metafile: a placed
    fxsym.fx_wmf's tc 0x09 member is art-stripped (its page record holds no metafile), its
    appearance is the element record's. The bake draws that one, as the bitmap draw does."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.symbols import SymbolResolver

    if CTF._GdiApi.get() is None:
        pytest.skip("no GDI here: the bake draws nothing")
    page = fx_page("FxSymbols")
    doc = EditableDocument.open(page)
    (root,) = doc.find_objects_by_name("FxWmfSymbol")
    objs = doc.parsed.page.objects
    i = root + 1
    assert objs[i].type_code == 0x09 and not objs[i].subtype_size & 0x2
    from ctgedit.format.bitmaps import metafile_bits

    assert metafile_bits(objs[i]) is None  # art-stripped
    owners = CTF.appearance_owners(objs, SymbolResolver.for_page(page))
    recs = []
    for po, ow in zip(objs, owners):
        r0 = GP_decode(po)
        recs.append((r0, CTF.ctf_rec(r0, GP_decode(ow) if ow is not None and ow is not po else None)))
    assert CTF._metafile_bits(recs[i][1])
    w, h, colour = 500, 300, 0xD8D8D8
    uns = []
    px = CTF.render_background(objs, recs, [i], w, h, colour, uns)
    assert uns == []
    l, t, r, b = recs[i][0].rect1
    from array import array

    a = array("I", px)
    inside = {a[y * w + x] & 0xFFFFFF for y in range(t, min(b, h)) for x in range(l, min(r, w))}
    assert inside - {colour}  # the metafile drew there


def test_the_static_shortcut_knows_every_key_that_sets_an_animation_flag():
    """_anim_flags returns 0 at once for an object without any key in _ANIM_FLAG_KEYS: the
    function reads no key outside those and the mode keys (a new animation key must be
    added to the list, or the shortcut would skip it)."""
    from ctgedit.format import ctf as CTF

    read = set()

    class _Rec:
        tc = CTF.TEXT_TC
        _low = {"vISIBLE".lower(): "x"}

        def get(self, key):
            read.add(key.lower())
            return "1" if key else None

    for mode in ("0", "1", "2", "3"):
        o = _Rec()
        o.get = lambda key, m=mode: (read.add(key.lower()), m if key.upper().endswith("MODE") else "")[1]
        CTF._anim_flags(o)
    read.discard("")
    assert read <= CTF._ANIM_FLAG_KEYS | CTF._ANIM_MODE_KEYS, read - CTF._ANIM_FLAG_KEYS - CTF._ANIM_MODE_KEYS
    assert CTF._ANIM_FLAG_KEYS <= read  # (and none listed in vain)
