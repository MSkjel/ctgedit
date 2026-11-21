"""Old page forms are upgraded on load."""

import os
import struct

import pytest

from ctgedit.format import links, upgrade, writer
from ctgedit.format.container import Container
from ctgedit.format.integrity import DamagedFileError, PAGE_EXTENT_MAX, check_openable, problems
from ctgedit.format.page import parse_ctg_bytes
from ctgedit.format.pagesummary import read_link_names, read_page_summary

import fxinstall

#: the current page that is downgraded: FxMain's FxTplBlank of the fake install (read only;
#: version 9 / word2 12, a link table of one template entry with its GUID, three records
#: without object names: the template genie, its member and a text)
FXMAIN = os.path.join(fxinstall.FX_INSTALL, "User", "FxMain")
PAGE = os.path.join(FXMAIN, "FxTplBlank.ctg")
#: the committed old forms of one page (tools/make_fixtures.py downgrade(): a rectangle, an
#: ellipse, a button, a text, a symbol and a genie, with their links)
OLD_FORMS = (("FxOld9", 9), ("FxOld10", 10), ("FxOld11", 11))


def _downgrade(data: bytes, word2: int = 9) -> bytes:
    """The old form of a current page: no GUIDs, bare link entries, no name lpstr (word2
    < 10), container version 8 / ``word2``."""
    c = Container.parse(data, keep_raw=False)
    pg = c.block("page")
    d = pg.data
    link_count = struct.unpack_from("<I", d, 0x1C)[0]
    data_start = 0x34 + link_count * 0x60
    area, entries = links.parse_link_table(d[0x34:data_start]) if link_count else ("    ", [])
    body = b""
    n_links = 0
    if entries:
        names = [e[1].encode("latin-1") + b"\x00" for e in entries]
        offs, pos = [], 0
        for nb in names:
            offs.append(pos)
            pos += len(nb)
        body = (
            area.encode("latin-1")
            + struct.pack("<II", len(names), pos)
            + struct.pack("<%dI" % len(names), *offs)
            + b"".join(names)
        )
        n_links = (len(body) + 0x5F) // 0x60
        body = body.ljust(n_links * 0x60, b"\x00")
    objdata_size, count = struct.unpack_from("<II", d, 0)
    recs = bytearray()
    off = data_start
    sizes = []
    payloads = []
    for _ in range(count):
        size = struct.unpack_from("<I", d, off)[0]
        pl = bytearray(d[off + 8 : off + 8 + size])
        an_end = 0x0C + struct.unpack_from("<H", pl, 0x0A)[0]
        if word2 < 0x0B:
            pl = bytearray(_grow_rects(bytes(pl)))
        if word2 < 10:
            assert struct.unpack_from("<H", pl, an_end + 4)[0] == 0, "a named record"
            del pl[an_end + 4 : an_end + 6]
        payloads.append(bytes(pl))
        sizes.append(len(pl))
        off += 8 + size
    running = 0
    for i, pl in enumerate(payloads):
        running += 8 + len(pl)
        recs += struct.pack("<II", len(pl), running if i + 1 < len(payloads) else 0) + pl
    head = bytearray(d[:0x24])
    struct.pack_into("<I", head, 0, len(recs))
    struct.pack_into("<I", head, 0x1C, n_links)
    pg.data = bytes(head) + body + bytes(recs) + d[data_start + objdata_size :]
    at = c.block("attributes")
    at.data = at.data[:0x24] + at.data[0x34:]
    c.version, c.word2 = 8, word2
    c.relayout()
    return c.to_bytes()


def _grow_rects(payload: bytes) -> bytes:
    """The inverse of the upgrade's class-rect rule (below word2 11 a rectangle, ellipse or
    button with its class part stores its class rect one more on the right and bottom, a
    button in XP_STYLE two; its header the same amount more) on a current-form payload. A
    record without its class part is left alone (CtDraw's load derives it from its twin)."""
    from ctgedit.format import geometry as G, placement as P
    from ctgedit.format.page import parse_payload

    po = parse_payload(payload)
    if po.type_code not in upgrade._RECT_CLASSES or not po.subtype_size & 0x2:
        return payload
    o = P.class_section_offset(po)
    n = 2 if po.type_code == 0x1001 and upgrade._xp_style(po) else 1
    body = bytearray(po.body_raw)
    l, t, r, b = struct.unpack_from("<4h", body, o)
    dr, db = (n if r > l else 0), (n if b > t else 0)
    struct.pack_into("<4h", body, o, l, t, r + dr, b + db)
    gh = G.parse_geom_header(bytes(body))
    for at in (gh.rect1_off, gh.rect2_off):
        rl, rt, rr, rb = struct.unpack_from("<4h", body, at)
        struct.pack_into("<4h", body, at, rl, rt, rr + dr, rb + db)
    if po.type_code != 0x11:
        struct.pack_into("<2h", body, gh.anchor_off, gh.anchor[0], gh.anchor[1] + db)
    po.body_raw = bytes(body)
    if po.type_code == 0x1001:
        props = po.raw_properties()
        for key, d in (("WIDTH", dr), ("HEIGHT", db)):
            if d and key in props:
                writer.set_object_property(po, key, str(int(props[key]) + d))
    return writer.object_payload(po)


@pytest.fixture(scope="module")
def current():
    return open(PAGE, "rb").read()


def test_the_old_form_parses_as_the_current_one(current):
    old = _downgrade(current)
    assert struct.unpack_from("<HH", old, 0) == (8, 9)
    check_openable(PAGE, old)
    now = parse_ctg_bytes(current)
    up = parse_ctg_bytes(old)
    assert (up.container.version, up.container.word2) == (9, 0x0C)
    assert up.container.upgraded_from == 9
    assert up.page.head_size == 0x34
    assert len(up.page.objects) == len(now.page.objects) == up.page.object_count
    assert up.page.objdata_consumed == up.page.objdata_size
    assert (up.page.page_width, up.page.page_height) == (now.page.page_width, now.page.page_height)
    assert [writer.object_payload(o) for o in up.page.objects] == [
        writer.object_payload(o) for o in now.page.objects
    ]
    # the identity: the stamp is kept, the GUID is fresh and shared with the attributes
    assert up.page.page_identity[:4] == now.page.page_identity[:4]
    assert up.page.page_identity[4:] != b"\x00" * 16
    assert up.attributes.page_identity == up.page.page_identity
    assert (up.attributes.width, up.attributes.height) == (now.attributes.width, now.attributes.height)
    assert up.attributes.bg_color0 == now.attributes.bg_color0
    assert up.attributes.list1.pairs == now.attributes.list1.pairs
    assert up.attributes.list2.pairs == now.attributes.list2.pairs
    t_now = links.parse_link_table(now.page.link_table_raw) if now.page.link_count else None
    t_up = links.parse_link_table(up.page.link_table_raw) if up.page.link_count else None
    if t_now:
        assert [n for _g, n in t_up[1]] == [n for _g, n in t_now[1]]
        assert all(g == b"\x00" * 16 for g, _n in t_up[1])
    assert problems(up, len(old)) == []


def test_a_save_of_the_old_form_writes_the_current_one(current, tmp_path):
    up = parse_ctg_bytes(_downgrade(current))
    out = writer.serialize_ctg(up.container, up.page, up.attributes)
    assert struct.unpack_from("<HH", out, 0) == (9, 0x0C)
    again = parse_ctg_bytes(out)
    assert again.container.upgraded_from == 0
    assert problems(again, len(out)) == []
    assert [writer.object_payload(o) for o in again.page.objects] == [
        writer.object_payload(o) for o in up.page.objects
    ]
    assert again.page.page_identity == up.page.page_identity
    # and a second save is byte-exact
    assert writer.serialize_ctg(again.container, again.page, again.attributes) == out


def test_the_summary_reads_the_old_head(current, tmp_path):
    p = str(tmp_path / "old.ctg")
    open(p, "wb").write(_downgrade(current))
    s = read_page_summary(p, want_thumbnail=False)
    now = parse_ctg_bytes(current)
    assert s.ok and s.word2 == 9
    assert (s.width, s.height) == (now.page.page_width, now.page.page_height)
    assert s.object_count == now.page.object_count
    assert read_link_names(p) == read_link_names(PAGE)


def test_the_loader_gate(current):
    for version, word2 in ((8, 4), (10, 0x0C), (6, 0x0C)):
        bad = struct.pack("<HH", version, word2) + current[4:]
        with pytest.raises(DamagedFileError):
            check_openable(PAGE, bad)
    check_openable(PAGE, current)


def _geom(payload: bytes):
    from ctgedit.format import geometry as G, placement as P
    from ctgedit.format.page import parse_payload

    po = parse_payload(payload)
    gh = G.parse_geom_header(po.body_raw)
    o = P.class_section_offset(po)
    return (
        tuple(gh.anchor),
        tuple(gh.rect1),
        tuple(gh.rect2),
        struct.unpack_from("<4h", po.body_raw, o),
        po.raw_properties(),
    )


def test_the_record_body_rules():
    """The record body rules for old page forms."""
    current = parse_ctg_bytes(_old("FxOld11")).page.objects  # (word2 11: no rule)
    rect, ellipse, button = (writer.object_payload(current[k]) for k in range(3))
    assert _geom(upgrade._upgrade_payload(rect, 10))[:4] == (
        (20, 79),
        (20, 20, 119, 79),
        (20, 20, 120, 80),
        (20, 20, 119, 79),
    )
    assert _geom(upgrade._upgrade_payload(ellipse, 10))[:4] == (
        (179, 20),
        (140, 20, 219, 79),
        (140, 20, 220, 80),
        (140, 20, 219, 79),
    )
    a, r1, r2, cls, props = _geom(upgrade._upgrade_payload(button, 10))
    assert props["VIS_MODE"].upper() == "XP_STYLE"
    assert (a, r1, cls, props["WIDTH"], props["HEIGHT"]) == (
        (20, 128),
        (20, 100, 118, 128),
        (20, 100, 118, 128),
        "98",
        "28",
    )
    # word2 11 has no rule; the old form the fixtures store reads back as the current one
    for pl in (rect, ellipse, button):
        assert upgrade._upgrade_payload(pl, 0x0B) == pl
        assert upgrade._upgrade_payload(_grow_rects(pl), 10) == pl
    # a record without its class part (a genie member: dwFlags 0x2 clear) is left alone
    from ctgedit.format.page import parse_payload

    member = parse_payload(rect)
    member.subtype_size &= ~0x2
    plain = writer.object_payload(member)
    assert upgrade._upgrade_payload(plain, 10) == plain
    # another class: the name only (word2 9: the u16 0 name goes in after 4 body bytes)
    text = writer.object_payload(current[3])
    assert current[3].type_code == 0x05
    an_end = 0x0C + struct.unpack_from("<H", text, 0x0A)[0]
    old9 = text[: an_end + 4] + text[an_end + 6 :]
    assert upgrade._upgrade_payload(old9, 9) == text == upgrade._upgrade_payload(text, 10)


def test_problems_checks_the_chain_and_the_limits(current):
    pc = parse_ctg_bytes(current)
    assert problems(pc, len(current)) == []
    pc.page.objects[0].next_link += 4
    assert any("links to byte" in p for p in problems(pc, len(current)))
    pc = parse_ctg_bytes(current)
    pc.page.page_width = PAGE_EXTENT_MAX + 1
    assert any("beyond what the Graphics Builder opens" in p for p in problems(pc, len(current)))


def _old(name):
    return open(os.path.join(FXMAIN, name + ".ctg"), "rb").read()


def test_the_committed_old_forms_read_as_one_page():
    """FxOld9 / FxOld10 / FxOld11 are one page in three old forms: each reads as the current
    form with the same records and links, and passes the checks."""
    payloads, names = [], []
    for name, word2 in OLD_FORMS:
        data = _old(name)
        assert struct.unpack_from("<HH", data, 0) == (8, word2)
        check_openable(name + ".ctg", data)
        up = parse_ctg_bytes(data)
        assert (up.container.version, up.container.word2) == (9, 0x0C)
        assert up.container.upgraded_from == word2
        assert problems(up, len(data)) == []
        # (the symbol fxsym.fx_box is a group-root symbol: its instance forest 0x08 > 0x0F,
        # as CtDraw's save of FxOld9 has it)
        assert [hex(o.type_code) for o in up.page.objects] == [
            "0xf",
            "0x11",
            "0x1001",
            "0x5",
            "0x8",
            "0xf",
            "0x8",
            "0xf",
            "0x11",
        ]
        payloads.append([writer.object_payload(o) for o in up.page.objects])
        names.append([n for _g, n in links.parse_link_table(up.page.link_table_raw)[1]])
        out = writer.serialize_ctg(up.container, up.page, up.attributes)
        assert struct.unpack_from("<HH", out, 0) == (9, 0x0C)
    assert payloads[0] == payloads[1] == payloads[2]
    assert names[0] == names[1] == names[2] and len(names[0]) == 2


@pytest.mark.golden
def test_ctdraws_save_of_the_old_form_is_our_upgrade():
    """LIVE: CtDraw's PageOpen + PageSave of a word2 9 page writes version 9 /
    word2 12 and every record with a u16 0 name after its first four body bytes (the rect
    rule of tc 0x0F / 0x11 / 0x1001 applied): the records our upgrade reads. The oracle's
    FxOld9 as CtDraw saved it."""
    ct = fxinstall.oracle_bytes("pages", "FxMain/FxOld9", "page.ctg")
    assert struct.unpack_from("<HH", ct, 0) == (9, 0x0C)
    want = parse_ctg_bytes(ct)
    up = parse_ctg_bytes(_old("FxOld9"))
    assert want.container.upgraded_from == 0
    assert [writer.object_payload(o) for o in want.page.objects] == [
        writer.object_payload(o) for o in up.page.objects
    ]
    assert [n for _g, n in links.parse_link_table(want.page.link_table_raw)[1]] == [
        n for _g, n in links.parse_link_table(up.page.link_table_raw)[1]
    ]
    assert want.page.page_identity[4:] != b"\x00" * 16  # a fresh GUID
    assert want.attributes.page_identity == want.page.page_identity
