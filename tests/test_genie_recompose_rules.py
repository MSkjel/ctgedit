"""What CtDraw's load and save do to placed genies."""

import itertools

import pytest


@pytest.fixture
def base_page(fx_page):
    """The page new_blank takes its form from: FxMain's FxShapes of the fake install (the
    libraries the tests place are their own, in tmp_path)."""
    return fx_page("FxShapes")


def _counting_clock(monkeypatch):
    """Save stamps are whole seconds: a counter keeps each save's stamp its own."""
    from ctgedit.format import element as E

    c = itertools.count(E.ctdraw_timestamp())
    monkeypatch.setattr(E, "ctdraw_timestamp", lambda: next(c))


def _texts(objs):
    from ctgedit.format import genie_place as GP

    return [
        (dict(GP.decode(po).entries[0][1].pairs).get("TXT_OFF"), GP.decode(po).rect1)
        for po in objs
        if po.raw_payload is None and po.type_code == 0x05
    ]


def _nested_page(base_page, tmp_path, monkeypatch):
    """p = a text 'P' and n0 (a text '%Label%') bound to X, placed on a page; then the page's
    'P' member is spoiled (out of step with its library) and only n0 changes (its text
    moves 3 px). Returns the page path."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import genie_place as GP

    _counting_clock(monkeypatch)
    monkeypatch.setenv("CTG_LIB_PATH", str(tmp_path))
    lib = str(tmp_path / "_nf.ctm")
    d = ElementDocument.new_element(lib, "n0", "genie", canvas=(300, 200))
    d.create_object("text", rect=(10, 10, 120, 26), text="%Label%")
    d.save()
    d = ElementDocument.new_element(lib, "p", "genie", canvas=(300, 200))
    d.create_object("text", rect=(10, 10, 120, 26), text="P")
    d.place_library_element("_nf", "n0", "ctm", (10, 40), params={"Label": "X"})
    d.save()
    page = str(tmp_path / "!NF.ctg")
    doc = EditableDocument.new_blank(base_page, page, register=False)
    doc.place_library_element("_nf", "p", "ctm", (100, 100), params={})
    doc.save()
    doc = EditableDocument.open(page)
    objs = doc.parsed.page.objects
    k = [i for i, po in enumerate(objs) if po.raw_payload is None and po.type_code == 0x05][0]
    r = GP.decode(objs[k])
    b = r.entries[0][1]
    b.pairs = [(key, "Z" if key == "TXT_OFF" else v) for key, v in b.pairs]
    objs[k] = GP.to_po(GP.encode(r))
    with open(page, "wb") as fh:
        fh.write(doc.serialize())
    e = ElementDocument.open_element(lib, "n0")
    t = [i for i, po in enumerate(e.parsed.page.objects) if po.raw_payload is None and po.type_code == 0x05][
        0
    ]
    e.move_objects([t], 3, 0)
    e.save()
    libraries_changed()
    return page


def test_a_nested_change_composes_the_genie_again_in_full(base_page, tmp_path, monkeypatch):
    from ctgedit.editing import EditableDocument

    monkeypatch.delenv("CTG_GENIE_NESTED_COPIES", raising=False)
    doc = EditableDocument.open(_nested_page(base_page, tmp_path, monkeypatch))
    assert doc.genie_changes() == {("_nf", "p"): frozenset({("_nf", "n0")})}
    assert doc.apply_open_updates() == ["1 genies"]
    (p, _), (x, x_rect) = _texts(doc.parsed.page.objects)
    # the spoiled member is its library's again; the nested copy moved with n0
    assert (p, x) == ("P", "X")
    assert x_rect[0] == 103


def test_the_nested_copies_setting_leaves_the_other_members(base_page, tmp_path, monkeypatch):
    from ctgedit.editing import EditableDocument

    monkeypatch.setenv("CTG_GENIE_NESTED_COPIES", "1")
    doc = EditableDocument.open(_nested_page(base_page, tmp_path, monkeypatch))
    assert doc.apply_open_updates() == ["1 genies"]
    (p, _), (x, x_rect) = _texts(doc.parsed.page.objects)
    assert (p, x) == ("Z", "X")
    assert x_rect[0] == 103


def test_the_no_change_memo_tells_the_two_ways_apart():
    from ctgedit.infra import noop_memo

    args = (b"page", {("lib", "g"): frozenset({("lib", "n")})}, {"lib.g": [["f", 1, 2]]})
    assert noop_memo.key(*args, mode="full") != noop_memo.key(*args, mode="copies")


def test_a_stale_stripped_text_is_measured_again_on_save(base_page, tmp_path, monkeypatch):
    """The page's member texts carry no art (dwFlags 0x2 clear): a stored extent out of step
    with the twin's font is measured again by the save, an unbound '%Label%' as blank (' ',
    4 px here), and the genie's group follows its texts."""
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.format import genie_place as GP
    from ctgedit.format.page import parse_ctg

    monkeypatch.setenv("CTG_LIB_PATH", str(tmp_path))
    lib = str(tmp_path / "_sf.ctm")
    d = ElementDocument.new_element(lib, "n0", "genie", canvas=(300, 200))
    d.create_object("text", rect=(10, 10, 120, 26), text="%Label%")
    d.create_object("rectangle", rect=(10, 40, 60, 70))
    d.save()
    page = str(tmp_path / "!SF.ctg")
    doc = EditableDocument.new_blank(base_page, page, register=False)
    doc.place_library_element("_sf", "n0", "ctm", (100, 100), params={})
    doc.place_library_element("_sf", "n0", "ctm", (100, 200), params={"Label": "Pump 12345"})
    doc.save()
    want = [GP.decode(po) for po in parse_ctg(page).page.objects if po.raw_payload is None]
    kinds = [
        (po.type_code, po.subtype_size & 0x2) for po in parse_ctg(page).page.objects if po.raw_payload is None
    ]
    assert kinds == [(0x08, 0), (0x05, 0), (0x0F, 0)] * 2
    assert want[1].rect1 == (100, 100, 104, 116)  # ' ' for the unbound '%Label%'
    # spoil both texts' extents and their groups'
    doc = EditableDocument.open(page)
    objs = doc.parsed.page.objects
    for k, r1 in (
        (1, (100, 100, 160, 116)),
        (0, (100, 100, 160, 160)),
        (4, (100, 200, 140, 216)),
        (3, (100, 200, 150, 260)),
    ):
        r = GP.decode(objs[k])
        r.rect1 = r.rect2 = r1
        objs[k] = GP.to_po(GP.encode(r))
    with open(page, "wb") as fh:
        fh.write(doc.serialize())
    EditableDocument.open(page).save()
    got = [GP.decode(po) for po in parse_ctg(page).page.objects if po.raw_payload is None]
    assert [(g.rect1, g.rect2, g.anchor) for g in got] == [(w.rect1, w.rect2, w.anchor) for w in want]


def test_a_members_total_takes_its_twins_local_and_offset():
    from ctgedit.format import factory, genie_place as GP, placement as P

    A, D = 0.9722222222222225, 1.0211748633879785
    L = (A, 0.0, 0.0, D, 633.5, 97.05614754098363)
    s2, m2 = P.slot2_for_linked(L, 3, (100, 100))
    twin = factory.create_object("rectangle", (10, 10, 50, 40), handle=1)
    P.write_slots(twin, [(3, L), (m2, s2), (m2, s2)])
    link = GP._member_link(twin, (m2, s2))
    assert link == (L, 3, (100, 100))
    child = (1.0, 0.0, 0.0, 1.0, 97.0, 129.0)
    assert P.apply_parent(child, 1, [link])[0][4] == 630.5833333333334  # CtDraw's
    assert P.apply_parent(child, 1, [(s2, m2, (0, 0))])[0][4] == 630.5833333333333
    # a member whose page slot 2 is not what its twin gives (a Local kept on the page):
    # its slot 2 as a whole
    kept = (A, 0.0, 0.0, D, s2[4] + 1.0, s2[5])
    assert GP._member_link(twin, (m2, kept)) == (kept, m2, (0, 0))


def test_a_symbol_sets_declared_states_come_from_the_instance_values():
    """CtDraw's saver counts a symbol set's declared states from the values in memory: an
    instance that binds a condition its element leaves unbound declares more (LIVE,
    Example Genies' xp_controls.Button: Disabled = FALSE gives TXT_C, 8 declared
    where the element's Disabled unbound gives 4; CtDraw's load + save kept 8)."""
    import struct
    import types
    from ctgedit.format import genie_place as GP, symbolset as SS

    vals = GP.Bag(
        [
            ("TXTMODE", "1"),
            ("TXT_A", "DspGetMouseOver()"),
            ("TXT_B", "DspGetMouseDown()"),
            ("TXT_C", "FALSE"),
        ],
        0,
    )
    elem = struct.pack("<HH", 4, 8) + b"\xf7\x01\x45\x01"
    for dw in (0x2, 0x0):  # the element record's own appearance, an art-stripped copy
        r = types.SimpleNamespace(tc=GP.SYMSET_TC, cls=elem)
        out = GP.strip_class_section(r, vals, None, dw)
        assert struct.unpack("<HH", out[:4]) == (SS.declared_count(dict(vals.pairs)), 8)
        assert out[:2] == struct.pack("<H", 8)
