"""Record builders and the compound-file writer."""

import struct

import pytest

from ctgedit.format import cfb, records as R
from ctgedit.format import genie_place as GP
from ctgedit.format.records import Style

#: each builder's default record (our own neutral values)
EXPECTED = {
    "animated_symbol": (
        "00000d1000001a00000002002d3100000000000000000000001100000011000100120000000000140012"
        "000000020000003100030004004558505200000700434f4d4d454e54000004004d4f44450900414e4d5f"
        "41525241590200000001000000ff000000ff0000000000000001ffff000000ff000000ff000000ff0000"
        "00000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f0000001100"
    ),
    "bar_graph": (
        "0000021000001a00000002002d3100000000000000000000009800000000001300990000000000130099"
        "000000020000001f00020004004558505200000700434f4d4d454e5400000200000001000000ff000000"
        "ff0000000000000001ffff000000ff000000ff000000ff000000000000ff000000ffffffff00ffffff00"
        "7f7f7f007f7f7f007f7f7f007f7f7f00000000001300990000000000010002000000ff7fff7f00ff0000"
        "00ff000000000000000000000000000000000000"
    ),
    "button": (
        "000001100000da00000002002d3100000000000000000000001400000000003c001400000000003d0015"
        "00000002000000570005000400544558540600427574746f6e0400464f4e540a00427574746f6e466f6e"
        "74050057494454480200363006004845494748540200323008005649535f4d4f4445080058505f535459"
        "4c45020000000500417269616c0012010000000a0000000000000000000000000000ff000000ff060042"
        "7574746f6e0100000095ca600095ca60000000000001ff95ca600095ca600095ca600095ca6000000000"
        "ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f00000000003c001400"
    ),
    "cicode_object": (
        "0000091000001a00000002002d31000000000000000000000010000000100001001100000000001f0011"
        "000000020000000d0000000200000001000000ff000000ff0000000000000001ffff000000ff000000ff"
        "000000ff000000000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f00000010"
        "00"
    ),
    "color_flood": (
        "0000081000001a00000002002d3100000000000000000000000e0000000e0001000f000000000010000f"
        "00000002000000140001000300434d4400000200000001000000ff000000ff0000000000000001ffff00"
        "0000ff000000ff000000ff000000000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f00"
        "7f7f7f0000000e00"
    ),
    "digital_text": (
        "0000061000001a00000002002d3100000000000000000000000c0000000c0001000d0000000000400011"
        "000000020000004000040004004558505200000700434f4d4d454e540000060053545230303000000700"
        "464f4e543030300b0044656661756c74466f6e740200000001000000ff000000ff0000000000000001ff"
        "ff000000ff000000ff000000ff000000000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f"
        "7f007f7f7f0000000c00"
    ),
    "ellipse": (
        "0000110000000a00000002002d3100000000000000000013000000000000002800280000000000290029"
        "000000020000000c0000000200000001000000aaaa0000aaaa00000000010001ffaaaa0000aaaa0000aa"
        "aa0000aaaa0000000000ff000000ffffffd500ffffd5002b2b00002b2b00000000000000000000000000"
        "002800280000000000000000000000000000"
    ),
    "ellipse_block": (
        "0000110000000a00000002002d3100000000000000000013000000000000002800280000000000290029"
        "000000020000000c000000020000000100000000d5d50000d5d5000000000001ff00d5d50000d5d50000"
        "d5d50000d5d500000000ff000000ff00ffff0000ffff0000d5d50000d5d5007f7f7f007f7f7f00000000"
        "002800280000000000000000000000000000"
    ),
    "ellipse_outline": (
        "0000110000000a00000002002d3100000000000000000013000000000000002800280000000000290029"
        "000000020000000c000000020000000100000000d5d50000d5d5000000000001ff00d5d50000d5d50000"
        "d5d50000d5d500000000ff000000ff00ffff0000ffff0000d5d50000d5d5007f7f7f007f7f7f00000000"
        "002800280000000000000000000000000000"
    ),
    "group": (
        "0000080000000a00000002002d3100000000000000000000000000000000000100010000000000010001"
        "000000020000000c000000020000000100000000000000000000000000000001ff000000000000000000"
        "00000000000000000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f00"
    ),
    "keyboard": (
        "00000b1000001a00000002002d3100000000000000000000000000000000000100010000000000210017"
        "000000020000001f00020007004b45595f53455100000300434d44000002000000010000000000000000"
        "0000000000000001ff00000000000000000000000000000000000000ff000000ffffffff00ffffff007f"
        "7f7f007f7f7f007f7f7f007f7f7f0000000000"
    ),
    "line": (
        "0000010000000a00000002002d3100000000000000000000000000000000000a000a00000000000b000b"
        "000000020000000c000000020000000100000000000000000000000000000001ff000000000000000000"
        "00000000000000000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f00000000"
        "000a000a00"
    ),
    "number": (
        "0000050000001a00000002002d3100000000000000000000000d00000000001600100000000000160010"
        "000000020000001d00010007005458545f4f46460600232323232e230200000001000000000000000000"
        "00000000000001ff00000000000000000000000000000000000000ff000000ffffffff00ffffff007f7f"
        "7f007f7f7f007f7f7f007f7f7f00030000000600232323232e23000000000d0001000b0600232323232e"
        "230500417269616c"
    ),
    "pipe": (
        "00000b0000000a00000002002d31000000000000000000000000000000000000001300ffffffff020015"
        "000000020000000c0000000200000001000000ffffff00ffffff000000000003ffffffff00ffffff00ff"
        "ffff00ffffff00000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f00030000"
        "0000000000130000001300"
    ),
    "polygon": (
        "0000100000000a00000002002d310000000000000000000f000000000000000f000a000000000010000b"
        "000000020000000c000000020000000100000080000000800000000000010001ff800000008000000080"
        "00000080000000000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f0004000f"
        "0000000f000a00000005000f000000000000000000000000"
    ),
    "rectangle": (
        "00000f0000000a00000002002d3100000000000000000000001400000000002800140000000000290015"
        "000000020000000c0000000200000001000000e5e5e500e5e5e5000000010001ffe5e5e500e5e5e500e5"
        "e5e500e5e5e500000000ff000000ffffffff00ffffff00bfbfbf00bfbfbf007f7f7f007f7f7f00000000"
        "002800140000000000000000000000"
    ),
    "rectangle_outline": (
        "00000f0000000a00000002002d3100000000000000000000000500000000009400050000000000950006"
        "000000020000000c000000020000000100000000000000000000000000000001ff000000000000000000"
        "00000000000000000000ff000000ffffffff00ffffff0099999900999999007f7f7f007f7f7f00000000"
        "009400050000000000000000000000"
    ),
    "slider": (
        "00000f1000001a00000002002d310000000000000000000b0099000b0000000c009a000000000017009a"
        "000000020000004b0005000300544147000005005448554d4200000600595f5350414e04002d31353304"
        "004d4f44450d0057524954455f4f4e5f445241470700434f4d4d454e5400000200000001000000ff0000"
        "00ff0000000000000001ffff000000ff000000ff000000ff000000000000ff000000ffffffff00ffffff"
        "007f7f7f007f7f7f007f7f7f007f7f7f000b0099000b000000"
    ),
    "string": (
        "0000051000001a00000002002d3100000000000000000000000e0000000e0001000f0000000200400013"
        "000000020000006300060008004f46465f464f4e540b0044656661756c74466f6e7407004f4e5f464f4e"
        "540b0044656661756c74466f6e74040045585052000008004f46465f54455854000007004f4e5f544558"
        "5400000700434f4d4d454e5400000200000001000000ff000000ff0000000000000001ffff000000ff00"
        "0000ff000000ff000000000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f00"
        "00000e00"
    ),
    "symbol": (
        "0000070000001900000002002d3100000000000001000000000000000000000000000000000100000000"
        "000000000000000000000000010000000000000000000000000000000000000f00000000001000100000"
        "00000010001000130066786c69622e667873796d626f6c2e3c63746c0000020000000c00000002000000"
        "010000000000000000000000"
    ),
    "symbol_set": (
        "00000c0000001a00000002002d3100000000000001000000000000000000000000000000000100000000"
        "000000000000000000000000010000000000000000000000000000000000000000000000000100010000"
        "000000160016000000020000002100020005005458545f41000007005458544d4f444501003002000000"
        "01000000ff808000ff8080000000000001ffff808000ff808000ff808000ff808000000000ff000000ff"
        "ffffff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f000000000000000000"
    ),
    "symbol_set_dummy": (
        "0000ffff00001a00000002002d3100000000000001000000000000000000000000000000000100000000"
        "000000000000000000000000010000000000000000000000000000000000000000000000000100010000"
        "000000010001000000020000000c000000020000000100000000000000000000000000000001ff000000"
        "00000000000000000000000000000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f007f"
        "7f7f00000000000100010000000000"
    ),
    "text": (
        "0000050000001a00000002002d3100000000000000000000000d00000000001600100000000000160010"
        "000000020000001b00010007005458545f4f464604005465787402000000010000000000000000000000"
        "0000000001ff00000000000000000000000000000000000000ff000000ffffffff00ffffff007f7f7f00"
        "7f7f7f007f7f7f007f7f7f0003000000040054657874000000000d0001000b0400546578740500417269"
        "616c"
    ),
    "touch": (
        "0000101000001a00000002002d3100000000000000000000000000000000000100010000000000210017"
        "00000002000000310002000600414354494f4e0b004b45595f4c425554544f4e08005250545f52415445"
        "0300353030020000000100000000000000000000000000000001ff000000000000000000000000000000"
        "00000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f0000000000"
    ),
    "trend": (
        "0000031000001a00000002002d310000000000000000000000bc0000000000c900bc0000000000ca00bd"
        "000000020000000c0000000200000001000000ff000000ff0000000000000001ffff000000ff000000ff"
        "000000ff000000000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f00c800bb"
        "00bc000100000000000400ca00080000ff000000ff000000ffff0000ffff000000ff000000ff00ffff00"
        "00ffff0000ff00ff00ff00ff00ffffff00ffffff00ff000000ff000000bfbfbf00bfbfbf00"
    ),
    "web_content": (
        "0000130000001a00000002002d31000000000c00576562436f6e74656e745f3100000000005a00000000"
        "00a0005a0000000000a0005a00000002000000270001000b005f5f414e5f4e414d455f5f0c0057656243"
        "6f6e74656e745f31020000000100000000000000000000000000000001ff000000000000000000000000"
        "00000000000000ff000000ffffffff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f00000000000000"
        "00a0005a00"
    ),
}


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_builder_default_is_pinned(kind):
    assert R.build(kind).hex() == "".join(EXPECTED[kind])


def test_every_builder_is_pinned():
    assert set(EXPECTED) == set(R.BUILDERS) - {"activex"}


@pytest.mark.parametrize("kind", sorted(R.BUILDERS))
def test_record_walks_like_a_loaded_one(kind):
    """The strict decoder walks every section to the end, and encodes the
    record back to the same bytes."""
    payload = R.build(kind)
    po = R.to_po(payload)
    r = GP.decode(po)
    assert GP.encode(r) == payload
    assert (r.style is not None) == bool(po.subtype_size & R.STYLE)
    assert r.style is None or len(r.style) == 54


def test_style_round_trip_and_state0():
    s = Style(
        effects=2,
        line_style=1,
        fill_style=1,
        attribs=0x10,
        line_width=3,
        effect_depth=-1,
        line=(1, 2),
        fill=(3, 4),
        background=(5, 6),
        hilight=(7, 8),
        lolight=(9, 10),
        shadow=(11, 12),
    )
    b = s.pack()
    assert len(b) == 54 and Style.unpack(b) == s
    assert s.state0() == (3, 4) and Style(line=(1, 2)).state0() == (1, 2)


def test_colour_state_flags_and_layout():
    from ctgedit.format import colourstate as CS

    cs = R.colour_state(
        (1, 1), maxon=(2, 2), states=[(3, 3)], array=[(4, 4)] * 256, thresholds=[(1, 50, 5, 5)], filcol=(6, 6)
    )
    assert struct.unpack_from("<I", cs)[0] == 0x7F
    payload = R.record(
        0x0F,
        anchor=(0, 20),
        rect1=(0, 0, 40, 20),
        rect2=(0, 0, 41, 21),
        props=(),
        cs=cs,
        style=R.RECT_STYLE,
        cls=R.rect_part((0, 0, 40, 20)),
    )
    L = CS.layout(R.to_po(payload))
    assert L is not None and L["end"] - L["at"] == len(cs)


def test_bag_sizes_carry_the_overshoot():
    assert R.bag([("AB", "c")], 10) == struct.pack("<HH", 2 + 7 + 10, 1) + b"\x02\x00AB\x01\x00c"
    assert R.value_overshoot(0x1010) == 11 and R.value_overshoot(0x0F) == 10
    r = GP.decode(R.new_record("touch"))
    assert r.entries[0][1].ov == 11 and r.entries[0][1].pairs == list(R.TOUCH_PROPS)


def test_derived_bounds_and_anchor():
    po = R.new_record("rectangle", rect1=(10, 10, 50, 30), style=Style(effects=2, effect_depth=1))
    r = GP.decode(po)
    assert r.anchor == (10, 30) and r.rect2 == (9, 9, 52, 32)
    e = GP.decode(R.new_record("ellipse", rect1=(0, 0, 51, 40)))
    assert e.anchor == (25, 0) and e.rect2 == (0, 0, 52, 41)
    p = GP.decode(R.new_record("pipe", nodes=((5, 5), (5, 40))))
    assert p.anchor == (5, 5) and p.rect1 == (5, 5, 5, 40) and p.rect2 == (4, 4, 7, 42)


def test_the_readers_find_the_class_parts():
    from ctgedit.format import geometry as G, legacy as L, symbolset as S, webcontent as W

    t = G.parse_shape_tail(R.new_record("rectangle", radius=4, gradient=(2, 7, 8)))
    assert t["kind"] == "rect" and t["radius"] == 4 and t["grad_mode"] == 2
    t = G.parse_shape_tail(R.new_record("ellipse", start=-90, end=45))
    assert t["kind"] == "ellipse" and (t["start"], t["end"]) == (-90, 45)
    nodes = [(1, 2), (30, 4), (10, 25), (1, 2)]
    assert G.get_object_nodes(R.new_record("polygon", nodes=nodes)) == nodes
    tx = G.parse_text_tail(R.new_record("text", caption="Fx text", size=14))
    assert (tx["text"], tx["size"], tx["face"]) == ("Fx text", 14, "Arial")
    for kind in (
        "cicode_object",
        "keyboard",
        "touch",
        "string",
        "digital_text",
        "color_flood",
        "animated_symbol",
    ):
        assert L.class_tail(R.new_record(kind, at=(70, 80)))[0] == "point", kind
    info = L.trend_info(R.new_record("trend", rect1=(0, 0, 100, 50), spacing=2))
    assert info["spacing"] == 2 and info["samples"] == 51 and info["height"] == 50
    assert [p[0] for p in info["pens"]] == list(L.DEFAULT_PEN_COLOURS)
    st = R.new_record("symbol_set", hotspot=(12, 34), declared=2, states=2)
    assert S.hotspot(st) == (12, 34) and S.class_section(st)["states"] == 2
    web = R.new_record("web_content", handle=4, url="http://fx.invalid/", enable_print=1)
    assert W.read_string_section(web) == ("http://fx.invalid/", 1)
    assert web.raw_properties()["__AN_NAME__"] == "WebContent_4"


def test_compound_file_streams_read_back():
    from ctgedit.io.pgregister import ole_clsid
    from ctgedit.io.projectcit import _layout

    small, big = b"fx" * 50, bytes(range(256)) * 20
    blob = cfb.build(R.FX_CONTROL_CLSID, [("Contents", small), ("FxBig", big), ("A", b"")])
    assert len(blob) % 512 == 0 and ole_clsid(b"pad" + blob) == R.FX_CONTROL_CLSID
    got = {
        n: b"".join(blob[o : o + unit] for o in spans)[:size]
        for n, (_e, spans, unit, size) in _layout(blob).items()
    }
    assert got == {"Contents": small, "FxBig": big, "A": b""}
    assert cfb.build(R.FX_CONTROL_CLSID, [("Contents", small)]) == cfb.build(
        R.FX_CONTROL_CLSID, [("Contents", small)]
    )


def test_synthetic_activex_record():
    from ctgedit.format import olecontrol
    from ctgedit.io.pgregister import ole_clsid
    from ctgedit.io.projectcit import _layout

    po = R.new_record(
        "activex",
        handle=3,
        rect1=(10, 20, 110, 70),
        props=(("ACTIVEX_Value", "FxTag|"),),
        properties=(("Value", "FxTag|"),),
    )
    assert po.type_code == 0x0D and po.subtype_size == 0x1A
    assert ole_clsid(po.body_raw) == R.FX_CONTROL_CLSID
    start, prog, _at, pairs = olecontrol.find_list(po.body_raw)
    assert prog == R.FX_CONTROL_PROGID and pairs == [("Value", "FxTag|")]
    assert not olecontrol.rebuild_list(po)  # the list mirrors the keys
    r = GP.decode(po)
    assert r.rect1 == (10, 20, 110, 70)
    # the Contents stream holds the extent in HIMETRIC
    storage = r.cls[12 : len(r.cls) - (len(po.body_raw) - start)]
    _e, spans, unit, size = _layout(storage)["Contents"]
    contents = b"".join(storage[o : o + unit] for o in spans)[:size]
    assert contents[:4] == cfb.CONTENTS_TAG
    assert struct.unpack_from("<ii", contents, 8) == (cfb.himetric(100), cfb.himetric(50))


def test_the_constant_tails_are_the_builders():
    from ctgedit.format import ctf, factory, genie_norm

    assert R.GROUP_TAIL == factory._GROUP_TAIL == ctf._BG_MID
    assert R.PICTURE_TAIL == factory._BITMAP_MID
    assert R.DEFAULT_STYLE.pack() == genie_norm.DEFAULT_GROUP_STYLE
    assert R.colour_state() == genie_norm.DEFAULT_GROUP_CS
    assert ctf._BG_HEAD == bytes.fromhex("0000000000000a00000002002d310000ffff0000000000")


def test_factory_needs_no_install_for_the_built_kinds(monkeypatch):
    """Every kind but the ActiveX controls copied from the install is built by ctgedit."""
    from ctgedit.format import factory as F

    monkeypatch.setattr(F, "_control_page", lambda kind: None)
    kinds = F.available_kinds()
    assert "activex" not in kinds and {"text", "button", "trend", "web_content"} <= set(kinds)
    info = {"N": (0, 0, 20, 18), "e": (0.0, 0.0), "root_handle": 3, "state0": (0, 0)}
    for kind in kinds:
        kw = dict(symbol=("fxlib", "fxsym", "ctl"), link_info=info) if kind == "symbol" else {}
        po = F.create_object(kind, (10, 10, 60, 40), **kw)
        assert GP.decode(po) is not None
        assert F.TOOL_SEEDS[kind] in R.BUILDERS
    for kind in ("touch", "keyboard"):
        assert F.make_input_record(kind, 4, (5, 6)).type_code in (0x1010, 0x100B)


def test_a_new_control_is_copied_from_the_install(fx_install, monkeypatch):
    """A control placed in the editor is the install's own record (its saved OLE state),
    read from the page that holds one when it is placed. With no such page the tool is not
    offered."""
    from ctgedit.format import factory as F
    from ctgedit.format.page import parse_ctg
    from ctgedit.io.pgregister import ole_clsid

    # (the fake install's made-up gauge stands in for the Example project's CiMeter; the
    # factory's own lookup, not the suite's made-up pages)
    monkeypatch.setattr(F, "_control_page", F._control_page.real)
    monkeypatch.setitem(F.CONTROL_SOURCES, "activex", ("FxMain", "FxActiveX.ctg", b"FxGauge"))
    F._control_record.cache_clear()
    try:
        assert "activex" in F.available_kinds()
        po = F.create_object("activex", (0, 0, 100, 100))
        source = parse_ctg(fx_install.page("FxActiveX")).page.objects
        assert ole_clsid(po.body_raw) == R.FX_CONTROL_CLSID
        assert any(ole_clsid(o.body_raw) == R.FX_CONTROL_CLSID for o in source if o.type_code == 0x0D)
        assert po.an_str == F.NO_LINK and po.class_flag == 0
        monkeypatch.setitem(F.CONTROL_SOURCES, "activex", ("FxMain", "NoSuchPage.ctg", b"FxGauge"))
        assert "activex" not in F.available_kinds()
        with pytest.raises(ValueError):
            F.create_object("activex", (0, 0, 100, 100))
        assert not {"db_exchange", "pelco"} & set(F.available_kinds())
    finally:
        F._control_record.cache_clear()


#: the app's new slider body at (0, 0, 22, 153) (the record checked as an engine fixed point)
_APP_SLIDER = (
    "0000000000000000000b0098000b0000000c00990000000000170099000000020000005e000500030054"
    "4147000005005448554d4213007468756d62732e706f696e746572325f655f720600595f5350414e0400"
    "2d31353204004d4f44450d0057524954455f4f4e5f445241470700434f4d4d454e540000020000000100"
    "0000ff000000ff0000000000000001ffff000000ff000000ff000000ff000000000000ff000000ffffff"
    "ff00ffffff007f7f7f007f7f7f007f7f7f007f7f7f000b0098000b000000"
)


def test_a_new_slider_has_the_include_thumb():
    """Tools > V3/V4 Objects > Slider: the Include pointer as its THUMB (no field sets it),
    while the bare builder stays neutral."""
    from ctgedit.format import factory as F

    po = F.create_object("slider", (0, 0, 22, 153))
    assert po.raw_properties()["THUMB"] == F.SLIDER_THUMB == "thumbs.pointer2_e_r"
    assert po.body_raw.hex() == _APP_SLIDER
    assert R.new_record("slider").raw_properties()["THUMB"] == ""
