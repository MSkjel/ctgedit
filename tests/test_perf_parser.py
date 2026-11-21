"""Parser speed-ups change no output."""

import os
import random
import shutil
import struct
import time

import pytest

from ctgedit.format import colourstate as CS
from ctgedit.format import geometry as G
from ctgedit.format import page as PG
from ctgedit.format import placement as PL
from ctgedit.format import writer as W
from ctgedit.format.container import Container
from ctgedit.format.textcodec import CODEC

#: the fake install's pages the comparisons walk (FxPerf's FxLarge: about 1,200 records of
#: genies, pipes, groups, styled shapes, texts and numbers; FxGenies; FxAnim: every
#: animation key; FxSymbols: symbols and sets; FxTextKinds: text shapes and fonts;
#: FxShapes: one of each primitive, bitmaps; FxMotion: transforms) and libraries
PAGES = (
    ("FxLarge", "FxPerf"),
    ("FxGenies", "FxMain"),
    ("FxAnim", "FxMain"),
    ("FxSymbols", "FxMain"),
    ("FxTextKinds", "FxMain"),
    ("FxShapes", "FxMain"),
    ("FxMotion", "FxMain"),
)
#: the fake install's copy (set by the autouse ``_fx`` fixture)
FX = {}


@pytest.fixture(autouse=True)
def _fx(fx_install):
    FX.clear()
    FX.update(
        install=fx_install,
        pages=[fx_install.page(n, proj) for n, proj in PAGES],
        lib=fx_install.file("FxLib", "fxgenie.ctm"),
        symbols=[fx_install.file("FxLib", n) for n in ("fxsym.ctl", "fxmore.ctl")],
    )
    yield


# --------------------------------------------------------------- the replaced code
def _ref_candidates(p, start):
    """Every offset the lookahead prefilter gives, in order (each search from c + 1)."""
    out = []
    m = PG._BAG_CANDIDATE.search(p, start)
    while m is not None:
        out.append(m.start())
        m = PG._BAG_CANDIDATE.search(p, m.start() + 1)
    return out


def _anchor_candidates(p, start):
    out = []
    back = PG._ANCHOR_BACK
    m = PG._BAG_ANCHOR.search(p, start + back)
    while m is not None:
        c = m.start() - back
        out.append(c)
        m = PG._BAG_ANCHOR.search(p, c + 1 + back)
    return out


def _ref_scan(p, start):
    """The prefiltered bag scan as it was (lookahead search, a parse at every candidate)."""
    bags = []
    search = PG._BAG_CANDIDATE.search
    m = search(p, start)
    while m is not None:
        c = m.start()
        entry = PG._parse_entry(p, c)
        if entry is not None:
            pairs, trailer_len, end = entry
            bags.append((c, pairs, trailer_len, any(PG._key_is_known_uncached(k) for k, _ in pairs), end - c))
            m = search(p, end)
        else:
            m = search(p, c + 1)
    return bags


def _ref_text_extras(payload, prefix_end):
    """The prefiltered text scan as it was (read_lpstr and the checks at every candidate)."""
    exprs, font = [], None
    n = len(payload)
    search = PG._TEXT_CANDIDATE.search
    m = search(payload, prefix_end)
    while m is not None:
        c = m.start()
        if c >= n - 2:
            break
        s, no = PG.read_lpstr(payload, c)
        if s is not None and s.startswith("@(") and s.endswith(")") and no > c + 2:
            if s not in exprs:
                exprs.append(s)
            m = search(payload, no)
            continue
        if (
            s is not None
            and 1 <= len(s) <= 32
            and all(ch.isalnum() or ch in " -_" for ch in s)
            and any(ch.isalpha() for ch in s)
            and not s.startswith("@")
        ):
            if not PG._key_is_known_uncached(s):
                font = s
        m = search(payload, c + 1)
    return exprs, font


def _ref_prefix(payload):
    class_flag = struct.unpack_from("<H", payload, 0x00)[0] if len(payload) >= 2 else 0
    type_code = struct.unpack_from("<I", payload, 0x02)[0] if len(payload) >= 6 else 0
    subtype_size = struct.unpack_from("<I", payload, 0x06)[0] if len(payload) >= 10 else 0
    an_len = struct.unpack_from("<H", payload, 0x0A)[0] if len(payload) >= 12 else 0
    an_end = 0x0C + an_len
    an_str = payload[0x0C:an_end].decode(CODEC, "replace") if an_end <= len(payload) else "-1"
    raw_payload = None
    if an_end > len(payload) or len(payload) < 0x0C:
        an_end = min(0x0C, len(payload))
        raw_payload = bytes(payload)
    return class_flag, type_code, subtype_size, an_str, an_end, raw_payload


def _ref_lp_end(b, o):
    (n,) = struct.unpack_from("<H", b, o)
    return o + 2 + n


def _ref_bag_end(b, o):
    (cnt,) = struct.unpack_from("<H", b, o)
    o += 2
    for _ in range(cnt):
        o = _ref_lp_end(b, _ref_lp_end(b, o))
    return o


def _ref_layout(po):
    b = po.body_raw
    fl = po.subtype_size
    try:
        gh = _ref_geom_header(b)
        if gh is None:
            return None
        o = gh[3]
        if fl & 1:
            o = _ref_lp_end(b, o)
        if fl & 8:
            (n_ent,) = struct.unpack_from("<H", b, o)
            o += 2
            for _ in range((n_ent + 1) * 3):
                o = _ref_bag_end(b, o + 2)
        if fl & 0x40:
            o = _ref_lp_end(b, o) + 2 + 24
        sec80 = None
        if fl & 0x80:
            sec80 = o
            o = _ref_lp_end(b, o)
        L = {"at": o}
        if sec80 is not None:
            L["sec80_at"] = sec80
        (flag,) = struct.unpack_from("<I", b, o)
        L["flag"] = flag
        o += 4
        L["state0_at"] = o
        o += 8
        if flag & 2:
            L["maxon_at"] = o
            o += 8
        for bit, name, size in ((4, "sta", 8), (8, "arr", 8), (0x10, "thr", 11)):
            if flag & bit:
                (n,) = struct.unpack_from("<H", b, o)
                L[name + "_at"], L[name + "_n"] = o + 2, n
                o += 2 + size * n
        if flag & 0x20:
            L["x20_at"] = o
            o += 8
        if flag & 0x40:
            L["x40_at"] = o
            o += 8
        if o > len(b):
            return None
        L["end"] = o
        if fl & 2:
            if o + 54 > len(b):
                return None
            L["style_at"] = o
        return L
    except struct.error:
        return None


def _ref_name_len(p, start):
    n = len(p)
    o = start
    if o + 6 > n:
        return start
    (name_len,) = struct.unpack_from("<H", p, o + 4)
    o += 6
    if (
        name_len
        and name_len <= 96
        and o + name_len <= n
        and all(PG._name_byte(b) for b in p[o : o + name_len])
    ):
        o += name_len
    return o


def _ref_geom_header(body):
    n = len(body)
    o = _ref_name_len(body, 0)
    if o == 0 or o > n:
        return None
    name = body[6:o].decode(CODEC, "replace") if o > 6 else ""
    for _ in range(3):
        if o >= n:
            return None
        mode = body[o]
        o += 1
        o += PG._XFORM_DOUBLES.get(mode, 6 if mode > 3 else 0) * 8
        if o > n:
            return None
    if o + 4 + 8 + 8 > n:
        return None
    return (
        o,
        o + 4,
        o + 12,
        o + 20,
        struct.unpack_from("<2h", body, o),
        struct.unpack_from("<4h", body, o + 4),
        struct.unpack_from("<4h", body, o + 12),
        name,
    )


def _ref_read_slots(body):
    off = 4
    ln = struct.unpack_from("<H", body, off)[0]
    off += 2 + ln
    out = []
    for _ in range(3):
        mode = body[off]
        n = PL._n_doubles(mode)
        vals = struct.unpack_from("<%dd" % n, body, off + 1) if n else ()
        a, b, c, d, h, k = PL.IDENT
        if n == 6:
            a, b, c, d, h, k = vals
        elif n == 4:
            a, d, h, k = vals
        elif n == 2:
            if mode & 0x0F == 2:
                a, d = vals
            else:
                h, k = vals
        out.append((mode, (a, b, c, d, h, k), off, 1 + 8 * n))
        off += 1 + 8 * n
    return out, off


def _ref_object_payload(po):
    raw = getattr(po, "raw_payload", None)
    if raw is not None:
        if len(raw) >= 2 and struct.unpack_from("<H", raw, 0)[0] != po.class_flag:
            raw = struct.pack("<H", po.class_flag) + raw[2:]
        return raw
    an = po.an_str.encode(CODEC, "replace")
    prefix = (
        struct.pack("<H", po.class_flag)
        + struct.pack("<I", po.type_code)
        + struct.pack("<I", po.subtype_size)
        + struct.pack("<H", len(an))
        + an
    )
    return prefix + po.body_raw


def _ref_serialize_objects(objects):
    payloads = [_ref_object_payload(o) for o in objects]
    cum = []
    running = 0
    for pl in payloads:
        running += 8 + len(pl)
        cum.append(running)
    out = bytearray()
    for i, pl in enumerate(payloads):
        out += struct.pack("<II", len(pl), cum[i] if i + 1 < len(payloads) else 0)
        out += pl
    return bytes(out)


def _ref_to_bytes(c):
    capacity = c.dword_c if 0 < c.dword_c <= 1024 else max(8, len(c.blocks))
    out = bytearray(struct.pack("<HHIII", c.version, c.word2, c.ts_created, c.ts_modified, c.dword_c))
    for i in range(capacity):
        if i < len(c.blocks):
            b = c.blocks[i]
            name = b.name.encode(CODEC, "replace")[:32].ljust(32, b"\x00")
            out += struct.pack("<H", b.tag) + name + struct.pack("<II", b.offset, b.size)
        else:
            out += b"\x00" * 42
    for b in sorted(c.blocks, key=lambda x: x.offset):
        if len(out) < b.offset:
            out += b"\x00" * (b.offset - len(out))
        out += b.data
    return bytes(out)


# --------------------------------------------------------------- inputs
_PIECES = [
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
    b"\x0d\x00\x01\x00\x07\x00TXT_OFF\x00\x00",
    b"\x08\x00\x02\x00\x01\x00Q\x00\x00",
    b"\x0c\x00\x01\x00\x03\x00KEY\x03\x00val",
    b"\x0b\x00\x01\x00\x07\x00TXT_OFF\x00\x00",
    b"\x12\x00\x01\x00\x07\x00TXTMODE\x01\x000\x00\x00\x00\x00\x00",
]


def _random_bodies(seed, count):
    rnd = random.Random(seed)
    for _ in range(count):
        body = bytearray()
        for _k in range(rnd.randint(1, 40)):
            if rnd.random() < 0.6:
                body += rnd.choice(_PIECES)
            else:
                body += bytes(rnd.getrandbits(8) for _b in range(rnd.randint(0, 24)))
        yield bytes(body), rnd.randint(0, 6)


def _corpus_payloads(paths):
    out = []
    for path in paths:
        pc = PG.parse_ctg_bytes(open(path, "rb").read())
        out += [(po, W.object_payload(po)) for po in pc.page.objects]
    return out


def _bags(bags):
    return [(b.offset, b.pairs, b.trailer_len, b.is_property, b.consumed) for b in bags]


# --------------------------------------------------------------- 1. the page parse
def test_anchor_gives_the_lookahead_candidates():
    for body, start in _random_bodies(20261009, 600):
        assert _anchor_candidates(body, start) == _ref_candidates(body, start)
    # a candidate right at the start, at the end, and none in a short body
    p = b"\x04\x00\x01\x00\x01\x00A"
    assert _anchor_candidates(p, 0) == _ref_candidates(p, 0) == [0]
    assert _anchor_candidates(b"\x00" * 6, 0) == _ref_candidates(b"\x00" * 6, 0) == []


@pytest.mark.parametrize("memo", ["cold", "warm"])
def test_memoised_scans_equal_the_old_scans_on_random_bodies(memo):
    for body, start in _random_bodies(7, 400):
        if memo == "cold":
            PG._ENTRY_MEMO.clear()
            PG._LPSTR_MEMO.clear()
        got = PG._scan_payload_bags(body, start)[0]
        assert _bags(got) == _ref_scan(body, start)
        assert PG._parse_text_extras(body, start) == _ref_text_extras(body, start)


def test_memoised_scans_equal_the_old_scans_on_pages():
    recs = _corpus_payloads(FX["pages"])
    assert len(recs) > 1000
    for memo in ("cold", "warm"):
        if memo == "cold":
            PG._ENTRY_MEMO.clear()
            PG._LPSTR_MEMO.clear()
        for po, payload in recs:
            assert _bags(PG._scan_payload_bags(payload, po.body_offset)[0]) == _ref_scan(
                payload, po.body_offset
            )
            if po.type_code == PG.TEXT_TYPE_CODE:
                assert PG._parse_text_extras(payload, po.body_offset) == _ref_text_extras(
                    payload, po.body_offset
                )


def test_a_full_memo_empties_and_the_parse_stays_the_same(monkeypatch):
    monkeypatch.setattr(PG, "_ENTRY_MEMO_MAX", 3)
    monkeypatch.setattr(PG, "_LPSTR_MEMO_MAX", 3)
    for body, start in _random_bodies(11, 150):
        assert _bags(PG._scan_payload_bags(body, start)[0]) == _ref_scan(body, start)
        assert PG._parse_text_extras(body, start) == _ref_text_extras(body, start)
        assert len(PG._ENTRY_MEMO) <= 3 and len(PG._LPSTR_MEMO) <= 3


def test_each_bag_gets_its_own_pair_list():
    # two entries back to back: the second one is the memo's (no search)
    body = b"\x00" * 4 + b"\x0c\x00\x01\x00\x03\x00KEY\x03\x00val" * 2
    a = PG._parse_object(0, 0, 0, 0, bytes(12) + body)
    b = PG._parse_object(0, 0, 0, 0, bytes(12) + body)
    assert [x.pairs for x in a.bags] == [[("KEY", "val")]] * 2
    assert _bags(a.bags) == _ref_scan(bytes(12) + body, 12)
    assert a.bags[0].pairs is not a.bags[1].pairs and a.bags[0].pairs is not b.bags[0].pairs


def test_record_prefix_equals_the_old_reads():
    rnd = random.Random(5)
    for n in list(range(0, 20)) + [rnd.randint(20, 80) for _ in range(200)]:
        payload = bytes(rnd.getrandbits(8) for _ in range(n))
        if n >= 12 and rnd.random() < 0.7:
            al = rnd.randint(0, 6)
            payload = payload[:10] + struct.pack("<H", al) + payload[12:]
        po = PG._parse_object(0, 0, n, 0, payload)
        cf, tc, ss, an, body_offset, raw = _ref_prefix(payload)
        assert (
            po.class_flag,
            po.type_code,
            po.subtype_size,
            po.an_str,
            po.body_offset,
            po.raw_payload,
            po.body_raw,
        ) == (cf, tc, ss, an, body_offset, raw, payload[body_offset:])
    # a bytearray payload parses as its bytes
    p = bytes(12) + b"\x00" * 4 + b"\x0e\x00\x01\x00\x03\x00KEY\x03\x00val"
    assert _bags(PG._parse_object(0, 0, 0, 0, bytearray(p)).bags) == _bags(
        PG._parse_object(0, 0, 0, 0, p).bags
    )


# --------------------------------------------------------------- 2. / 3. the per-record walks
def test_layout_geometry_and_slots_equal_the_old_walks():
    recs = [po for po, _p in _corpus_payloads(FX["pages"])]
    CS._LAYOUT_MEMO.clear()
    for po in recs:
        ref = _ref_layout(po)
        assert CS.layout(po) == ref
        L = CS.layout(po)  # the memo's answer: equal, a copy of its own
        assert L == ref
        if L is not None:
            L["at"] = -1
            assert CS.layout(po) == ref
        gh = G.parse_geom_header(po.body_raw)
        rg = _ref_geom_header(po.body_raw)
        assert (
            None
            if gh is None
            else (gh.anchor_off, gh.rect1_off, gh.rect2_off, gh.end, gh.anchor, gh.rect1, gh.rect2, gh.name)
        ) == rg
        if po.raw_payload is None:
            try:
                ref_slots = _ref_read_slots(po.body_raw)
            except (struct.error, IndexError) as e:
                with pytest.raises(type(e)):
                    PL.read_slots(po.body_raw)
            else:
                assert PL.read_slots(po.body_raw) == ref_slots


def _outcome(fn, body):
    try:
        return "ok", fn(body)
    except (struct.error, IndexError) as e:
        return "error", type(e)


def test_walks_on_random_bodies():
    rnd = random.Random(3)
    for _ in range(3000):
        n = rnd.randint(0, 90)
        body = bytearray(rnd.getrandbits(8) for _ in range(n))
        if n > 8 and rnd.random() < 0.5:
            body[4:6] = struct.pack("<H", rnd.randint(0, 12))
        if n > 20 and rnd.random() < 0.5:
            body[6] = rnd.choice([0, 1, 2, 3, 8, 0x12, 0xFF])
        body = bytes(body)
        assert PG._geom_name_len(body, 0) == _ref_name_len(body, 0)
        gh = G.parse_geom_header(body)
        assert (
            None
            if gh is None
            else (gh.anchor_off, gh.rect1_off, gh.rect2_off, gh.end, gh.anchor, gh.rect1, gh.rect2, gh.name)
        ) == _ref_geom_header(body)
        po = PG._parse_object(0, 0, 0, 0, struct.pack("<HIIH", 0, 1, rnd.getrandbits(9), 0) + body)
        assert CS.layout(po) == _ref_layout(po)
        assert _outcome(PL.read_slots, body) == _outcome(_ref_read_slots, body)
    # every byte of an object name: the delete table is _name_byte
    for b in range(256):
        assert (b in PG._NOT_NAME_BYTES) == (not PG._name_byte(b))


def _ref_entries(po):
    """genie_place.decode's value-bag walk as it was (_lp / _sdec per string)."""
    from ctgedit.format import genie_place as GP

    b = po.body_raw
    o = G.parse_geom_header(b).end
    dw = po.subtype_size
    if dw & 1:
        _s, o = GP._lp(b, o)
    if not dw & 8:
        return None
    n = struct.unpack_from("<H", b, o)[0]
    o += 2
    entries = []
    for _e in range(n + 1):
        bags = []
        for _b in range(3):
            size, cnt = struct.unpack_from("<HH", b, o)
            o += 4
            pairs = []
            tot = 2
            for _p in range(cnt):
                k, o = GP._lp(b, o)
                v, o = GP._lp(b, o)
                tot += len(k) + len(v) + 4
                pairs.append((GP._sdec(k), GP._sdec(v)))
            bag = GP.Bag(pairs)
            bag.ov = size - tot
            bags.append(bag)
        entries.append(bags)
    return entries


def test_strict_decode_equals_the_old_walk():
    from ctgedit.format import genie_place as GP

    n = 0
    for po, _p in _corpus_payloads(FX["pages"]):
        if po.raw_payload is not None:
            continue
        try:
            ref = _ref_entries(po)
        except (struct.error, AttributeError, TypeError) as e:
            with pytest.raises(type(e)):
                GP.decode(po)
            continue
        r = GP.decode(po)
        assert r.entries == ref
        assert GP.encode(r) == W.object_payload(po)
        n += 1
    assert n > 1000


# --------------------------------------------------------------- 4. the writer
def test_serialize_equals_the_old_writer():
    for path in FX["pages"]:
        data = open(path, "rb").read()
        pc = PG.parse_ctg_bytes(data)
        objs = pc.page.objects
        assert W.serialize_objects(objs) == _ref_serialize_objects(objs)
        for po in objs:
            assert W.object_payload(po) == _ref_object_payload(po)
        ref = _ref_to_bytes(pc.container)
        assert pc.container.to_bytes() == ref
        assert W.serialize_ctg(pc.container, pc.page, pc.attributes) == data
    # an an_str the code page encodes with the registered codec, a bytearray body
    po = PG._parse_object(0, 0, 0, 0, bytes(12) + b"\x00" * 8)
    po.an_str = "\u00e5\u20ac12"
    po.body_raw = bytearray(b"\x01\x02")
    assert W.object_payload(po) == _ref_object_payload(po)
    assert W.serialize_objects([po, po]) == _ref_serialize_objects([po, po])
    assert W.serialize_objects([]) == b""


def test_container_with_gaps_equals_the_old_writer():
    c = Container.parse(_tiny_container(), keep_raw=False)
    c.blocks[1].offset += 7  # a gap before the second block
    assert c.to_bytes() == _ref_to_bytes(c)


def _tiny_container():
    out = bytearray(struct.pack("<HHIII", 9, 0x0C, 1, 2, 8))
    blocks = [(1, b"page", b"P" * 10), (0x101, b"attributes", b"A" * 5)]
    off = 0x10 + 8 * 42
    for tag, name, data in blocks:
        out += struct.pack("<H", tag) + name.ljust(32, b"\x00") + struct.pack("<II", off, len(data))
        off += len(data)
    out += b"\x00" * ((8 - len(blocks)) * 42)
    for _t, _n, data in blocks:
        out += data
    return bytes(out)


# --------------------------------------------------------------- 5. the catalogue
def test_library_listing_is_shared_and_follows_the_folder(tmp_path):
    from ctgedit.io.catalog import ProjectCatalog

    d = tmp_path / "proj"
    d.mkdir()
    lib = os.path.join(str(d), "one.ctl")
    shutil.copyfile(FX["lib"], lib)
    os.mkdir(os.path.join(str(d), "folder.ctm"))  # not a file: never listed
    cat = ProjectCatalog(str(tmp_path), other_installs=False)
    info = cat.folder(str(d))

    def fresh(ext):
        return sorted(
            (e.path for e in os.scandir(str(d)) if e.name.lower().endswith(ext) and e.is_file()),
            key=lambda s: os.path.basename(s).lower(),
        )

    # a folder changed just now is listed again next time (the same clock tick)
    assert cat._library_paths(str(d))[".ctl"] == fresh(".ctl")
    assert str(d) not in cat._lib_paths
    old = time.time_ns() - 60_000_000_000
    os.utime(str(d), ns=(old, old))
    first = cat._library_paths(str(d))
    assert cat._lib_paths[str(d)][1] is first and first[".ctm"] == []
    assert cat._library_paths(str(d)) is first
    for ext in (".ctl", ".ctm", ".ctt"):
        assert first[ext] == fresh(ext)
    open(os.path.join(str(d), "Two.CTT"), "wb").close()  # the folder's time changes
    now = cat._library_paths(str(d))
    assert now is not first and now[".ctt"] == fresh(".ctt") == [os.path.join(str(d), "Two.CTT")]
    libs = cat.libraries(info, "symbol", include_empty=True)
    assert [li.path for li in libs] == fresh(".ctl")
    cat.refresh()
    assert str(d) not in cat._lib_paths


def _ref_th_to_bmp(th):
    out = bytearray(th)
    out[0:2] = b"BM"
    struct.pack_into("<I", out, 2, len(th))
    return bytes(out)


def test_thumbnail_bmp_and_record_shell_equal_the_old_code():
    from ctgedit.format import pagesummary as PS

    rnd = random.Random(9)
    for n in (6, 7, 53, 54, 55, 300):
        th = b"TH" + bytes(rnd.getrandbits(8) for _ in range(n - 2))
        assert PG._extract_bmp(th) == _ref_th_to_bmp(th)
        assert PS._th_to_bmp(th) == (_ref_th_to_bmp(th) if n >= 54 else b"")
    assert PG._extract_bmp(b"TX" + bytes(10)) is None
    for n in list(range(0, 20)) + [rnd.randint(20, 300) for _ in range(300)]:
        payload = bytes(rnd.getrandbits(8) for _ in range(n))
        if n >= 12 and rnd.random() < 0.7:
            payload = payload[:10] + struct.pack("<H", rnd.randint(0, 6)) + payload[12:]
        full = PG._parse_object(0, 0, n, 0, payload)
        shell = PG.parse_record_shell(payload)
        for f in (
            "index",
            "record_offset",
            "payload_size",
            "next_link",
            "class_flag",
            "type_code",
            "subtype_size",
            "an_str",
            "body_offset",
            "body_raw",
            "raw_payload",
        ):
            assert getattr(shell, f) == getattr(full, f), f
        assert shell.bags == () and shell.text_exprs == [] and shell.font_face is None


def test_element_summaries_flashing_unchanged():
    """The flashing test reads the records' prefix and body only (no bag scan). FxLib's
    fxsym.ctl (fx_flash) and fxmore.ctl (fx_flash2) hold flashing bitmaps."""
    from ctgedit.format import pagesummary as PS
    from ctgedit.format.bitmaps import dib_section

    flashing = set()
    for lib in FX["symbols"]:
        flashing |= {es.name for es in _flash_check(PS, dib_section, lib)}
    assert {"fx_flash", "fx_flash2"} <= flashing


def _flash_check(PS, dib_section, lib):
    """The element summaries of ``lib`` whose flashing flag a full record walk confirms (each
    one's flag is asserted); the flashing ones are returned."""
    out = []
    for es in PS.read_element_summaries(lib, want_thumbnail=False):
        with open(lib, "rb") as fh:
            h = PS._read_head(fh, es.offset, es.size, 0x0C)
            n = min(h.objdata, max(0, es.size - h.data_start))
            ref = False
            if 0 < n <= PS._FLASH_SCAN:
                fh.seek(es.offset + h.data_start)
                data = fh.read(n)
                o, pending, seen = 0, 1, 0
                while pending and o + 8 <= len(data) and seen < PS._FLASH_RECORDS:
                    seen += 1
                    psize = struct.unpack_from("<I", data, o)[0]
                    payload = data[o + 8 : o + 8 + psize]
                    o += 8 + psize
                    po = PG._parse_object(0, 0, len(payload), 0, payload)
                    pending += po.class_flag - 1
                    if po.type_code == 0x07 and not (po.subtype_size & 0x1):
                        sec = dib_section(po)
                        if sec is not None and sec.flags & 0x100:
                            ref = True
                            break
        assert es.flashing == ref, es.name
        if ref:
            out.append(es)
    return out


def test_read_directory_equals_a_buffered_read():
    from ctgedit.format import pagesummary as PS

    for path in [FX["lib"], FX["pages"][0]] + FX["symbols"]:
        with open(path, "rb") as fh:
            ref = PS._read_dir(fh, path, os.path.getsize(path))
        got = PS.read_directory(path)
        assert (got.ok, got.word2, got.entries) == (ref.ok, ref.word2, ref.entries)


def _old_library_bytes(lib) -> bytes:
    """element.LibraryFile.to_bytes before it joined the header to the rest once."""
    from ctgedit.format import element as E

    out = bytearray(lib.buf)
    struct.pack_into("<HHIII", out, 0, lib.version, lib.word2, lib.ts_created, lib.ts_modified, lib.dword_c)
    for s in lib.slots:
        o = E.HEADER_FIXED + s.index * E.ENTRY_SIZE
        out[o : o + E.ENTRY_SIZE] = s.raw
    if lib._ext is not None and lib._dirty:
        try:
            cut = lib._internal_size()
            live = [lib._content_end(s) for s in lib.slots if s.in_use and s.size and s.offset]
        except struct.error:
            cut = -1
        if cut >= 0:
            cut = max([cut, E.HEADER_FIXED + len(lib.slots) * E.ENTRY_SIZE] + live)
            del out[cut:]
            out += lib._ext
    return bytes(out)


def test_library_bytes_equal_the_old_copy_twice():
    # 7. element.LibraryFile.to_bytes: as it was, unchanged and after writes, a rename and
    # a delete (the directory grown, the appendix written again)
    import glob
    from ctgedit.format.element import EXT_FINGERPRINT, LibraryFile

    # every library of FxLib and Include (genies, symbols, the 14 elements of fxmany.ctl)
    libs = sorted(glob.glob(os.path.join(FX["install"].project("FxLib"), "*.ct[ml]"))) + sorted(
        glob.glob(os.path.join(FX["install"].project("Include"), "*.ct[ml]"))
    )
    assert len(libs) >= 12
    cases = []
    for path in libs:
        with open(path, "rb") as fh:
            data = fh.read()
        cases.append((path, data))
        # (with a properties appendix: no corpus library has one)
        isz = LibraryFile(data)._internal_size()
        cases.append((path + " +appendix", data[:isz] + EXT_FINGERPRINT + b"\x07" * 300))
    for path, data in cases:
        lib = LibraryFile(data)
        assert (lib._ext is not None) == path.endswith("+appendix")
        assert lib.to_bytes() == _old_library_bytes(lib)
        names = lib.element_names()
        if not names:
            continue
        content = lib.block_data(lib.find(names[0]))
        for k in range(12):  # (past a directory of 8 slots)
            lib.write_element("zz_perf_%d" % k, content + b"\x00" * (k * 37))
            assert lib.to_bytes() == _old_library_bytes(lib), (path, k)
        lib.rename_element("zz_perf_3", "zz_perf_renamed")
        lib.delete_element("zz_perf_5")
        lib.write_element(names[0], content + b"\x01" * 400)
        assert lib.to_bytes() == _old_library_bytes(lib), path


@pytest.mark.golden
def test_serialize_keeps_ctdraws_own_bytes():
    """The writer gives back a CtDraw-written page byte for byte (oracle pages: CtDraw's
    PageOpen + PageSave of FxGenies, FxAnim and FxShapes), the same comparison as
    test_serialize_equals_the_old_writer on ctgedit's own files."""
    import fxinstall

    for page in ("FxGenies", "FxAnim", "FxShapes"):
        data = fxinstall.oracle_bytes("pages", "FxMain/" + page, "page.ctg")
        pc = PG.parse_ctg_bytes(data)
        objs = pc.page.objects
        assert W.serialize_objects(objs) == _ref_serialize_objects(objs)
        assert pc.container.to_bytes() == _ref_to_bytes(pc.container)
        assert W.serialize_ctg(pc.container, pc.page, pc.attributes) == data, page
