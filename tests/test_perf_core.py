"""Core speed-ups change no output."""

import copy
import os
import random
import shutil
import struct
import zlib

import pytest

from ctgedit.format import geometry as G
from ctgedit.format import page as PG
from ctgedit.format import placement as P
from ctgedit.format import symbols as S
from ctgedit.format import writer as W
from ctgedit.format.container import Container
from ctgedit.format.textcodec import CODEC


@pytest.fixture()
def big(fx_install):
    """FxPerf's FxLarge: the big page (1,191 records)."""
    return fx_install.page("FxLarge", "FxPerf")


@pytest.fixture()
def genies(fx_install):
    """FxMain's FxGenies: genie units with members."""
    return fx_install.page("FxGenies")


@pytest.fixture()
def lib_path(fx_install):
    """FxLib's fxparts.ctm: a genie library of many elements (217 records)."""
    return fx_install.file("FxLib", "fxparts.ctm")


# --------------------------------------------------------------- the replaced algorithms
def _ref_parse_bag(p, off):
    if off + 2 > len(p):
        return None
    (count,) = struct.unpack_from("<H", p, off)
    if count == 0 or count > 256:
        return None
    oo = off + 2
    pairs = []
    for _ in range(count):
        if oo + 2 > len(p):
            return None
        (klen,) = struct.unpack_from("<H", p, oo)
        if oo + 2 + klen > len(p):
            return None
        kraw = p[oo + 2 : oo + 2 + klen]
        if not PG._is_keyish(kraw):
            return None
        oo += 2 + klen
        if oo + 2 > len(p):
            return None
        (vlen,) = struct.unpack_from("<H", p, oo)
        if oo + 2 + vlen > len(p):
            return None
        vraw = p[oo + 2 : oo + 2 + vlen]
        oo += 2 + vlen
        pairs.append((kraw.decode(CODEC), vraw.decode(CODEC)))
    return pairs, oo


def _ref_parse_entry(p, off):
    if off + 2 > len(p):
        return None
    (entry_len,) = struct.unpack_from("<H", p, off)
    if entry_len < 4 or off + 2 + entry_len > len(p):
        return None
    bag = _ref_parse_bag(p, off + 2)
    if bag is None:
        return None
    pairs, bag_end = bag
    trailer_len = off + 2 + entry_len - bag_end
    if trailer_len < 0:
        return None
    return pairs, trailer_len, off + 2 + entry_len


def _ref_key_is_known(key):
    return PG._key_is_known_uncached(key)


def _ref_scan(p, start):
    """The first pass of _scan_payload_bags as it was: an attempt at EVERY offset."""
    n = len(p)
    out = []
    o = start
    while o < n - 2:
        e = _ref_parse_entry(p, o)
        if e is not None:
            pairs, trailer_len, end = e
            out.append((o, pairs, trailer_len, any(_ref_key_is_known(k) for k, _ in pairs), end - o))
            o = end
        else:
            o += 1
    return out


def _ref_read_lpstr(data, off):
    if off + 2 > len(data):
        return None, off
    (n,) = struct.unpack_from("<H", data, off)
    if off + 2 + n > len(data):
        return None, off
    return data[off + 2 : off + 2 + n].decode(CODEC, "replace"), off + 2 + n


def _ref_text_extras(payload, prefix_end):
    exprs, font = [], None
    o, n = prefix_end, len(payload)
    while o < n - 2:
        s, no = _ref_read_lpstr(payload, o)
        if s is not None and s.startswith("@(") and s.endswith(")") and no > o + 2:
            if s not in exprs:
                exprs.append(s)
            o = no
            continue
        if (
            s is not None
            and 1 <= len(s) <= 32
            and all(c.isalnum() or c in " -_" for c in s)
            and any(c.isalpha() for c in s)
            and not s.startswith("@")
        ):
            if not _ref_key_is_known(s):
                font = s
        o += 1
    return exprs, font


def _bags(po):
    return [(b.offset, b.pairs, b.trailer_len, b.is_property, b.consumed) for b in po.bags]


def _payloads(path):
    data = open(path, "rb").read()
    pc = PG.parse_ctg_bytes(data)
    return [(po, W.object_payload(po)) for po in pc.page.objects]


# --------------------------------------------------------------- 1. the parse prefilter
@pytest.mark.parametrize("project, page", [("FxPerf", "FxLarge"), ("FxPerf", "FxHuge"), ("FxMain", "*")])
def test_prefiltered_scans_equal_the_byte_loops_on_pages(fx_install, project, page):
    """A page of the fake install (``*``: every page of the project in turn)."""
    import glob

    paths = (
        sorted(glob.glob(os.path.join(fx_install.project(project), "*.ctg")))
        if page == "*"
        else [fx_install.page(page, project)]
    )
    n = 0
    for path in paths:
        for po, payload in _payloads(path):
            assert [
                (b.offset, b.pairs, b.trailer_len, b.is_property, b.consumed) for b in po.bags
            ] == _ref_scan(payload, po.body_offset)
            if po.type_code == PG.TEXT_TYPE_CODE:
                assert (po.text_exprs, po.font_face) == _ref_text_extras(payload, po.body_offset)
            n += 1
    assert n > 500


def test_prefiltered_scans_equal_the_byte_loops_on_a_library(lib_path):
    lib = S.SymbolLibrary.open(lib_path)
    n = 0
    for b in lib.container.blocks:
        if not b.name or b.is_deleted or not b.size:
            continue
        pb = PG.parse_page(b, lib.container.word2)
        for po in pb.objects:
            payload = W.object_payload(po)
            assert _bags(po) == _ref_scan(payload, po.body_offset)
            n += 1
    assert n > 50


def test_prefiltered_scans_on_adversarial_bodies():
    """Random bodies seeded with real-looking entries, lpstrs, '@(' tokens and near misses."""
    rnd = random.Random(20260929)
    pieces = [
        b"\x0e\x00\x01\x00\x03\x00KEY\x03\x00val",
        b"\x04\x00\x01\x00\x01\x00A",
        b"\x10\x00\x00\x01",
        b"\x00\x01\x02\x00AB",
        b"\x07\x00@(Hi)",
        b"\x05\x00Arial",
        b"\x03\x00-_ ",
        b"\x02\x00\xe5\xe6",
        b"\x01\x00\xb2",
        b"\x20\x00" + b"x" * 32,
        b"\xff\xff\x01\x00\x60\x00",
        bytes(range(256)),
        b"\x00" * 12,
    ]
    for _ in range(400):
        body = bytearray()
        for _k in range(rnd.randint(1, 40)):
            if rnd.random() < 0.5:
                body += rnd.choice(pieces)
            else:
                body += bytes(rnd.getrandbits(8) for _b in range(rnd.randint(0, 24)))
        payload = bytes(body)
        start = rnd.randint(0, 3)
        got = PG._scan_payload_bags(payload, start)[0]
        assert [(b.offset, b.pairs, b.trailer_len, b.is_property, b.consumed) for b in got] == _ref_scan(
            payload, start
        )
        assert PG._parse_text_extras(payload, start) == _ref_text_extras(payload, start)
        for off in range(0, len(payload), 7):
            assert PG.read_lpstr(payload, off) == _ref_read_lpstr(payload, off)


_CODE_PAGE_CHECK = r"""
import random, struct, sys
sys.path.insert(0, sys.argv[1])
from ctgedit.format import page as PG
from ctgedit.format.textcodec import CODEC, CODEPAGE
assert str(CODEPAGE) == sys.argv[2], CODEPAGE

def lp(b):
    return struct.pack("<H", len(b)) + b

def ref_text_extras(payload, prefix_end):
    exprs, font = [], None
    o, n = prefix_end, len(payload)
    while o < n - 2:
        s, no = None, o
        if o + 2 <= n:
            (ln,) = struct.unpack_from("<H", payload, o)
            if o + 2 + ln <= n:
                s, no = payload[o + 2:o + 2 + ln].decode(CODEC, "replace"), o + 2 + ln
        if s is not None and s.startswith("@(") and s.endswith(")") and no > o + 2:
            if s not in exprs:
                exprs.append(s)
            o = no
            continue
        if (s is not None and 1 <= len(s) <= 32
                and all(c.isalnum() or c in " -_" for c in s)
                and any(c.isalpha() for c in s) and not s.startswith("@")):
            if not PG._key_is_known_uncached(s):
                font = s
        o += 1
    return exprs, font

pool = []
for ch in "漢字明朝ゴシック한글굴림宋体黑体Кирилица ĄąŁł-_ 09AbéüÀ":
    try:
        pool.append(ch.encode(CODEC))
    except UnicodeError:
        pass
pool += [bytes([b]) for b in range(0x80, 0x100)]
rnd = random.Random(int(sys.argv[2]))
bad = 0
for k in range(1500):
    if k < 64:       # a font-like token of every length: 1..64 BYTES of 1-2 byte characters
        body = b"\x00\x00\x00" + lp(b"".join(rnd.choice(pool) for _ in range(k % 40 + 1))) + b"\x01"
    else:
        body = b"".join(rnd.choice([
            lp(b"".join(rnd.choice(pool) for _ in range(rnd.randint(1, 40)))),
            lp(b"@(" + b"".join(rnd.choice(pool) for _ in range(rnd.randint(0, 4))) + b")"),
            bytes(rnd.getrandbits(8) for _ in range(rnd.randint(0, 12)))])
            for _ in range(rnd.randint(1, 8)))
    for start in (0, 1):
        if PG._parse_text_extras(body, start) != ref_text_extras(body, start):
            bad += 1
print("BAD", bad)
"""


@pytest.mark.parametrize("cp", ["932", "936", "949", "950", "1251"])
def test_text_prefilter_on_other_code_pages(tmp_path, cp):
    """The Text prefilter's length class is a CHARACTER count 1..32: on a double-byte ANSI code
    page (textcodec MULTIBYTE) a font token of up to 32 characters can be up to 64 bytes.
    The code page is chosen at import (CTG_CODEPAGE),
    so each runs in its own process."""
    import subprocess
    import sys

    script = tmp_path / "cp_check.py"
    script.write_text(_CODE_PAGE_CHECK, encoding="utf-8")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, CTG_CODEPAGE=cp)
    r = subprocess.run(
        [sys.executable, str(script), root, cp], env=env, capture_output=True, text=True, timeout=300
    )
    assert r.returncode == 0, r.stderr[-2000:]
    assert "BAD 0" in r.stdout, r.stdout


def test_word_class_is_isalnum_for_every_code_point():
    """The font-token regex [\\w \\-] is exactly ch.isalnum() or ch in ' -_' (CPython's sre
    word class is Py_UNICODE_ISALNUM || '_')."""
    for i in range(0x110000):
        ch = chr(i)
        assert (PG._FONT_CHARS.fullmatch(ch) is not None) == (ch.isalnum() or ch in " -_"), hex(i)


def test_coverage_mode_keeps_the_byte_loop(monkeypatch):
    monkeypatch.setattr(PG, "WANT_COVERAGE", True)
    payload = b"\x00" * 30 + b"\x0e\x00\x01\x00\x03\x00KEY\x03\x00val" + b"\x00" * 8
    bags, covered = PG._scan_payload_bags(payload, 0)
    assert [(b.offset, b.pairs) for b in bags] == [(30, [("KEY", "val")])]
    assert covered > 0


def test_key_is_known_memo():
    for k in ("TXT_OFF", "COL_STA_3", "!*#1403", "!*#14x", "Arial", "METADATA_7", "zz"):
        assert PG._key_is_known(k) == PG._key_is_known_uncached(k)
        assert PG._key_is_known(k) == PG._key_is_known_uncached(k)  # the memoised answer


# --------------------------------------------------------------- 2. one re-scan per record
def _count_parses(monkeypatch):
    n = [0]
    real = W.parse_object

    def counting(*a, **k):
        n[0] += 1
        return real(*a, **k)

    monkeypatch.setattr(W, "parse_object", counting)
    return n


def test_unit_move_rescans_each_record_once(monkeypatch, genies):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(genies)
    roots = [r for r in doc.page.roots() if doc.page.objects[r].members]
    unit = max(roots, key=lambda r: len(doc.page.subtree(r)))
    idx = doc.page.subtree(unit)
    assert len(idx) > 5
    n = _count_parses(monkeypatch)
    doc._translate_records(idx, 3, 2)
    assert n[0] == len(idx)


def test_rescan_leaves_its_parse_on_the_record_and_an_unchanged_one_is_a_noop(monkeypatch, big):
    from ctgedit.io import reader as RD

    po = next(
        o for o in PG.parse_ctg_bytes(open(big, "rb").read()).page.objects if o.bags and o.raw_payload is None
    )
    G.translate_object(po, 5, 5)
    payload, fresh = getattr(po, W.FRESH_PARSE_ATTR)
    assert payload == W.object_payload(po)
    assert po.bags is fresh.bags and po.text_exprs is fresh.text_exprs
    assert fresh.body_raw is po.body_raw
    ref = RD._fresh_record(po, po.index)  # what the reader parses from scratch
    assert _bags(fresh) == _bags(ref) and fresh.text_exprs == ref.text_exprs
    assert (
        fresh.font_face,
        fresh.bytes_covered,
        fresh.class_flag,
        fresh.type_code,
        fresh.subtype_size,
        fresh.an_str,
        fresh.body_offset,
        fresh.raw_payload,
    ) == (
        ref.font_face,
        ref.bytes_covered,
        ref.class_flag,
        ref.type_code,
        ref.subtype_size,
        ref.an_str,
        ref.body_offset,
        ref.raw_payload,
    )
    n = _count_parses(monkeypatch)
    W.refresh_object_bags(po)  # nothing changed: no parse
    assert n[0] == 0 and po.bags is fresh.bags
    po.bags = list(po.bags)  # a bag view that is not that parse
    W.refresh_object_bags(po)
    assert n[0] == 1
    # a move that leaves every bag alone keeps the parse (writer._same_parse_after
    # proves a re-parse would give the same)
    G.translate_object(po, 1, 0)
    assert n[0] == 1 and getattr(po, W.FRESH_PARSE_ATTR)[0] == W.object_payload(po)
    ref = RD._fresh_record(po, po.index)
    assert _bags(po) == _bags(ref) and po.text_exprs == ref.text_exprs
    monkeypatch.setattr(W, "SAME_PARSE_FAST", False)
    G.translate_object(po, 1, 0)  # without the proof: a new parse
    assert n[0] == 2 and getattr(po, W.FRESH_PARSE_ATTR)[0] == W.object_payload(po)


def test_no_fresh_parse_kept_in_coverage_mode(monkeypatch):
    po = PG.parse_object(0, 0, 0, 0, bytes(12) + b"\x00" * 60)
    monkeypatch.setattr(PG, "WANT_COVERAGE", True)
    W.refresh_object_bags(po)
    assert not hasattr(po, W.FRESH_PARSE_ATTR)


def _edit_session(doc):
    roots = [r for r in doc.page.roots() if doc.page.objects[r].members]
    for r in sorted(roots, key=lambda r: -len(doc.page.subtree(r)))[:4]:
        o = doc.page.objects[r].geometry
        doc.set_object_rect(r, (o.x, o.y, o.x + o.extent_x + 7, o.y + o.extent_y + 3))
        doc.rotate_objects([r], True)
        doc.move_objects(doc.page.subtree(r), 2, -1)
    return [(W.object_payload(po), _bags(po), po.text_exprs, po.font_face) for po in doc.parsed.page.objects]


def test_scale_and_turn_records_equal_a_rescan_after_every_step(genies):
    """With every deferred re-scan forced back in, a resize, rotate and move
    of the FxGenies units give the same records -- bytes, flags and bags."""
    from ctgedit.editing import EditableDocument

    fast = _edit_session(EditableDocument.open(genies))
    with pytest.MonkeyPatch.context() as m:
        real_map = P._map_record_geometry

        def mapped(po, M6, via=None, anchor_only=False, refresh_owed=False):
            if refresh_owed:
                G.refresh_bags(po)
            return real_map(po, M6, via, anchor_only)

        m.setattr(P, "_map_record_geometry", mapped)
        real_to = G.translate_object
        m.setattr(G, "translate_object", lambda po, *a, refresh=True, **k: real_to(po, *a, **k))
        eager = _edit_session(EditableDocument.open(genies))
    assert fast == eager


# --------------------------------------------------------------- 5. the ancestor chains
def test_memoised_chains_equal_the_plain_walk(genies):
    pc = PG.parse_ctg_bytes(open(genies, "rb").read())
    pos = pc.page.objects
    parents = P.forest_parents(pos)
    plain = P._chains(pos, parents, None)
    memo = P._chains(pos, parents, lambda k: [((1.0, 0.0, 0.0, 1.0, 3.0, 4.0), 1, (0, 0))], memo=True)
    plain_outer = P._chains(pos, parents, lambda k: [((1.0, 0.0, 0.0, 1.0, 3.0, 4.0), 1, (0, 0))])
    for i in range(len(pos)):
        assert memo(i) == plain_outer(i)
        assert plain(i) == plain_outer(i)[: len(plain(i))]
    # a list whose parents do not precede their children is read the plain way
    rev = P._chains(pos, [None, 0, 3, None], None, memo=True)
    assert rev(2) == P._chains(pos, [None, 0, 3, None], None)(2)


def test_move_with_memoised_chains_equals_the_plain_walk(genies):
    from ctgedit.editing import EditableDocument

    def run():
        doc = EditableDocument.open(genies)
        for r in doc.page.roots():
            if doc.page.objects[r].members:
                doc.move_objects(doc.page.subtree(r), 3, 1)
                o = doc.page.objects[r].geometry
                doc.set_object_rect(r, (o.x, o.y, o.x + o.extent_x + 5, o.y + o.extent_y + 5))
        return doc.serialize()

    fast = run()
    with pytest.MonkeyPatch.context() as m:
        real = P._chains
        m.setattr(P, "_chains", lambda pos, parents, outer, memo=False: real(pos, parents, outer))
        plain = run()
    assert fast == plain


# --------------------------------------------------------------- 3. library memory / cache
def test_library_and_page_containers_keep_no_file_copy(lib_path, big):
    lib = S.SymbolLibrary.open(lib_path)
    assert lib.container.raw == b""
    assert any(b.data for b in lib.container.blocks)
    data = open(big, "rb").read()
    assert Container.parse(data).raw == data
    # a page parse keeps no copy either (the blocks hold their bytes)
    parsed = PG.parse_ctg_bytes(data)
    assert parsed.container.raw == b""
    assert parsed.container.block("page").data


def test_page_cli_still_prints(capsys, big):
    assert PG.main([big, "2"]) == 0
    assert "FILE  %s  (%d bytes)" % (big, os.path.getsize(big)) in capsys.readouterr().out


def test_library_cache_holds_one_version_per_path(tmp_path, lib_path):
    p = str(tmp_path / "copy.ctm")
    shutil.copyfile(lib_path, p)
    a = S.open_library(p)
    assert S.open_library(p) is a
    names = a.element_names()
    st = os.stat(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    b = S.open_library(p)
    assert b is not a and b.element_names() == names
    assert sum(1 for k in S._LIBRARY_CACHE if k == p) == 1
    assert S._LIBRARY_CACHE[p][1] is b
    S._open_library_version.cache_clear()
    assert p not in S._LIBRARY_CACHE


# --------------------------------------------------------------- 4. pictures / listings
def _ref_decode_dib(raw):
    """The mask inversion as it was: a generator per pixel."""
    bi_size, w, h, _planes, bpp = struct.unpack_from("<IiiHH", raw, 0)
    width, height = w, abs(h)
    src = bytearray(raw[40 : 40 + width * height * 4])
    mask = src[3::4]
    has_alpha = any(mask)
    src[0::4], src[2::4] = src[2::4], src[0::4]
    src[3::4] = bytes(0xFF - b for b in mask)
    rb = width * 4
    out = (
        bytes(src)
        if h < 0
        else b"".join(bytes(src[y * rb : (y + 1) * rb]) for y in range(height - 1, -1, -1))
    )
    return width, height, out, has_alpha


def test_decode_dib_equals_the_generator_version():
    rnd = random.Random(7)
    for w, h in ((1, 1), (3, 2), (17, -5), (8, 8), (31, -29)):
        px = bytes(rnd.getrandbits(8) for _ in range(abs(w * h) * 4))
        for mask_kind in ("random", "opaque", "clear"):
            b = bytearray(px)
            if mask_kind != "random":
                b[3::4] = bytes([0 if mask_kind == "opaque" else 0xFF]) * (abs(w * h))
            raw = struct.pack("<IiiHH", 40, w, h, 1, 32) + bytes(24) + bytes(b)
            eb = S.decode_dib(raw)
            assert (eb.width, eb.height, eb.rgba, eb.has_alpha) == _ref_decode_dib(raw)


def test_decoded_pictures_are_memoised_by_body(fx_install):
    """FxSymbols2's four free bitmaps and fxsym.ctl's fx_bitmap and fx_flash."""
    pc = PG.parse_ctg_bytes(open(fx_install.page("FxSymbols2"), "rb").read())
    recs = list(pc.page.objects)
    lib = S.SymbolLibrary.open(fx_install.file("FxLib", "fxsym.ctl"))
    for name in lib.element_names():
        recs += lib.element_objects(name) or []
    bmp = [po for po in recs if po.type_code == 0x07 and not po.subtype_size & 1 and po.raw_payload is None]
    assert len(bmp) >= 5
    for po in bmp[:40]:
        gh = G.parse_geom_header(po.body_raw)
        start = gh.end if gh else 0
        a = S.embedded_bitmaps_from_body(po.body_raw, start)
        ref = S._embedded_bitmaps(po.body_raw, start)
        assert a == ref
        assert S.embedded_bitmaps_from_body(bytes(bytearray(po.body_raw)), start) is a
        assert S.embedded_bitmap_from_body(po.body_raw, start) == S._embedded_bitmap(po.body_raw, start)


def test_directory_listings_shared_and_revalidated(tmp_path):
    d = str(tmp_path)
    open(os.path.join(d, "one.ctl"), "wb").close()
    r1 = S.SymbolResolver([d])
    assert "one.ctl" in r1._index_dir(d)
    # a directory changed just now is never trusted: a file created in the same clock
    # tick would not change its time
    assert d not in S._DIR_INDEX_CACHE
    open(os.path.join(d, "two.ctl"), "wb").close()
    assert "two.ctl" in S.SymbolResolver([d])._index_dir(d)
    # an old directory is listed once and shared; a change re-lists it
    old = os.stat(d).st_mtime_ns - 60_000_000_000
    os.utime(d, ns=(old, old))
    first = S.SymbolResolver([d])._index_dir(d)
    assert S._DIR_INDEX_CACHE[d][1] is first
    assert S.SymbolResolver([d])._index_dir(d) is first
    open(os.path.join(d, "three.ctl"), "wb").close()
    idx = S.SymbolResolver([d])._index_dir(d)
    assert idx is not first and "three.ctl" in idx
    assert S._directory_index(str(tmp_path / "missing")) == {}


def test_find_node_array_memo(big):
    pc = PG.parse_ctg_bytes(open(big, "rb").read())
    shapes = [po for po in pc.page.objects if po.type_code in G.NODE_TCS]
    assert sum(1 for po in shapes if G._find_node_array(po, po.body_raw) is not None) >= 10
    for po in shapes:
        a = G.find_node_array(po)
        ref = G._find_node_array(po, po.body_raw)
        assert a == ref
        b = G.find_node_array(po)  # memo hit: an equal, separate object
        assert b == a and (b is not a or a is None)
    po = next(p for p in shapes if G.find_node_array(p) is not None)
    memo = po.__dict__[G._NODE_MEMO]
    G.translate_object(po, 4, 4)  # a new body: the memo is dropped
    assert po.__dict__.get(G._NODE_MEMO) is not memo
    assert G.find_node_array(po) == G._find_node_array(po, po.body_raw)
    assert W.NODE_ARRAY_MEMO_ATTR == G._NODE_MEMO
    # deep copies (clipboard) carry a consistent memo
    c = copy.deepcopy(po)
    assert G.find_node_array(c) == G._find_node_array(c, c.body_raw)


def _ref_run_starts(body, start, lo_x, hi_x, lo_y, hi_y):
    """The general node scan's candidates as the byte loop finds them: a count of 2 to 512
    with room for its first point, and that point inside the bounds."""
    out = []
    for pos in range(start, len(body) - 1):
        (cnt,) = struct.unpack_from("<H", body, pos)
        if not (2 <= cnt <= 512) or pos + 2 + cnt * 4 > len(body):
            continue
        x, y = struct.unpack_from("<2h", body, pos + 2)
        if lo_x <= x <= hi_x and lo_y <= y <= hi_y:
            out.append(pos)
    return out


def test_node_scan_candidates_hold_every_byte_loop_candidate():
    """geometry._run_starts finds every position the byte loop would test past its first
    point, on random bodies and bounds (negative, wide, empty, the int16 limits)."""
    rnd = random.Random(7)
    for _ in range(3000):
        body = bytes(rnd.choice((0, 1, 2, 3, 0xFF, rnd.randrange(256))) for _ in range(rnd.randrange(0, 300)))
        span = rnd.choice((3, 300, 3000, 40000))
        lo_x, lo_y = rnd.randint(-40000, 40000), rnd.randint(-40000, 40000)
        hi_x, hi_y = lo_x + rnd.randint(-5, span), lo_y + rnd.randint(-5, span)
        start = rnd.randrange(0, 20)
        got = G._run_starts(body, start, lo_x, hi_x, lo_y, hi_y)
        ref = _ref_run_starts(body, start, lo_x, hi_x, lo_y, hi_y)
        assert set(ref) <= set(got) and got == sorted(set(got))


def test_value_bag_decodes_as_the_full_decode(big, genies):
    """writer.value_bag's decode of e0.b1 alone gives the full decode's pairs and fails
    where it fails."""
    from ctgedit.format import genie_place as GP

    for path in (big, genies):
        for po in PG.parse_ctg_bytes(open(path, "rb").read()).page.objects:
            try:
                r = GP.decode(po)
                ref = list(r.entries[0][1].pairs) if r.entries else []
            except Exception:  # noqa: BLE001 -- swallow-ok: the failure is what is compared
                ref = None
            if getattr(po, "raw_payload", None) is not None:
                ref = None
            assert W._value_bag_of(po) == ref


def test_body_reference_memo(genies):
    """reference_from_object keeps the body's reference on the record while it holds that
    body object: every FxGenies record gives the unmemoised answer, a new body reads again."""
    pc = PG.parse_ctg_bytes(open(genies, "rb").read())
    linked = [po for po in pc.page.objects if po.subtype_size & 0x1]
    assert linked
    for po in pc.page.objects:
        ref = S.reference_from_body(po.body_raw) if po.subtype_size & 0x1 else None
        a = S.reference_from_object(po)
        assert S.reference_from_object(po) == a
        if ref is not None:
            assert a == ref
    po = next(p for p in linked if S.reference_from_object(p) is not None)
    po.body_raw = bytes(len(po.body_raw))  # (a body without a reference)
    assert S._body_reference(po) is None
    from ctgedit.doc.geometry import GeometryEdits

    assert S.BODY_REF_MEMO_ATTR in GeometryEdits._CACHE_FIELDS


def test_value_bag_memo(big):
    """writer.value_bag is memoised on the record (its body object and prefix values): every
    FxLarge record gives the decode's pairs, a fresh list each time; a new body, flags or AN
    decode again."""
    pc = PG.parse_ctg_bytes(open(big, "rb").read())
    objs = pc.page.objects
    for po in objs:
        ref = W._value_bag_of(po)
        a = W.value_bag(po)
        b = W.value_bag(po)  # memo hit: equal, a separate list
        assert a == ref and b == ref
        assert a is None or (a is not b and a is not ref)
    po = next(p for p in objs if W.value_bag(p))
    memo = po.__dict__[W.VALUE_BAG_MEMO_ATTR]
    body = po.body_raw
    W.ensure_object_property(po, "ZZ_TEST", "1")  # (a new body)
    assert po.body_raw is not body
    assert W.value_bag(po) == W._value_bag_of(po)
    assert po.__dict__[W.VALUE_BAG_MEMO_ATTR] is not memo
    old = W.value_bag(po)
    po.subtype_size ^= 0x8  # the flags read differently: decoded again
    assert W.value_bag(po) == W._value_bag_of(po)
    po.subtype_size ^= 0x8
    assert W.value_bag(po) == old
    c = copy.deepcopy(po)  # (clipboard copies carry a consistent memo)
    assert W.value_bag(c) == W._value_bag_of(c)
    # an undo keeps no memo in its record states (it is rebuilt from the bytes)
    from ctgedit.doc.geometry import GeometryEdits

    assert W.VALUE_BAG_MEMO_ATTR in GeometryEdits._CACHE_FIELDS


def test_lazy_modules_resolve():
    for name in ("placement", "legacy", "writer", "colourstate", "textmetrics"):
        assert G._lazy(name).__name__ == "ctgedit.format." + name
    assert W._gp().__name__ == "ctgedit.format.genie_place"


def test_zlib_stream_helpers_unchanged():
    """The memo does not change what an unparseable body gives."""
    assert S.embedded_bitmaps_from_body(b"\x00" * 40, 0) == (None, None)
    assert S.embedded_bitmap_from_body(b"xyz", 0) is None
    raw = struct.pack("<IiiHH", 40, 2, -1, 1, 32) + bytes(24) + b"\x01\x02\x03\x00" * 2
    body = b"\x00" * 8 + struct.pack("<III", 1, len(raw), 0) + zlib.compress(raw)
    eb = S.embedded_bitmap_from_body(body, 0)
    assert eb is not None and eb.rgba == b"\x03\x02\x01\xff" * 2


def test_a_moved_text_keeps_its_parse_only_when_a_rescan_gives_it(big):
    """writer._same_parse_after proves TEXT records too, by scanning their texts again
    Every text of the
    FxLarge moved by a few pixels: a record that keeps its parse equals a fresh parse."""
    from ctgedit.io import reader as RD

    pc = PG.parse_ctg_bytes(open(big, "rb").read())
    texts = [po for po in pc.page.objects if po.type_code == PG.TEXT_TYPE_CODE]
    assert len(texts) > 50
    kept = 0
    for k, po in enumerate(texts):
        for dx, dy in ((1, 0), (0, 3), (-7, 5)):
            W.refresh_object_bags(po)
            G.translate_object(po, dx, dy)
            fresh = getattr(po, W.FRESH_PARSE_ATTR, None)
            if fresh is not None and fresh[0] == W.object_payload(po):
                kept += 1
            ref = RD._fresh_record(po, po.index)
            assert (
                _bags(po) == _bags(ref) and po.text_exprs == ref.text_exprs and po.font_face == ref.font_face
            ), (k, dx, dy)
    assert kept
