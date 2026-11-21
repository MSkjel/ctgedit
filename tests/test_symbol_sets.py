"""Symbol sets."""

import os
import shutil
import struct
import types

import pytest

from ctgedit.editing import EditableDocument, SymbolSetError
from ctgedit.format import geometry as G, placement as P, symbolset as S, writer
from ctgedit.format.page import parse_ctg
from ctgedit.model.document import ObjectKind

import fxinstall
from qtkit import app as _app

#: the page of the set under edit and the pages holding the other kinds of set
PAGE = "FxSymbols2"
SET_PAGES = ("FxSymbols2", "FxSymbols", "FxArrange")


def _find(d, name):
    found = d.find_objects_by_name(name)
    assert len(found) == 1, (name, found)
    return found[0]


@pytest.fixture()
def fx(fx_install):
    """The page and the indices of FxSquareSet and its two states (OFF, ON) in the copy."""
    d = EditableDocument.open(fx_install.page(PAGE))
    i = _find(d, "FxSquareSet")
    off, on = d.page.objects[i].members
    return types.SimpleNamespace(
        path=fx_install.page(PAGE), set=i, off=off, on=on, other=_find(d, "FxBmp1"), install=fx_install
    )


@pytest.fixture()
def doc(fx):
    # the copy (in FxMain) resolves its library symbols through FxMain's include chain
    return EditableDocument.open(fx.path)


def _payloads(d):
    return [writer.object_payload(po) for po in d.parsed.page.objects]


# --------------------------------------------------------------------------- classification
def test_alarmsim_set_is_a_symbol_set_not_a_group(fx):
    from ctgedit.io.reader import read_page

    page = read_page(fx.path)
    o = page.objects[fx.set]
    assert o.kind is ObjectKind.SYMBOL_SET
    assert not o.is_genie
    # each state is lights_square_small's instance forest (its root, then its rectangle:
    # CtDraw's save of FxSymbols2 has them so)
    assert o.members == [fx.set + 1, fx.set + 3] == [fx.off, fx.on]
    anim = o.animations.symbol_set
    assert anim is not None and anim.type == S.ON_OFF and anim.expressions == ["FX_PUMP2"]
    assert o.animations.display_value is None  # TXTMODE is the set type here
    # every tc 0x0C container on the pages (a multi-state and an array set too) is one
    for name in SET_PAGES:
        path = fx.install.page(name)
        pg = read_page(path)
        pobjs = parse_ctg(path).page.objects
        sets = [i for i, po in enumerate(pobjs) if po.type_code == S.SET_TC and po.class_flag]
        assert sets
        assert all(pg.objects[i].kind is ObjectKind.SYMBOL_SET for i in sets)


def test_class_section_follows_ctdraw_rule(fx):
    """u16 declared (2 / 2**n / 256) + u16 state count + the i16 hotspot."""
    seen = 0
    for name in SET_PAGES:
        objs = parse_ctg(fx.install.page(name)).page.objects
        for i, po in enumerate(objs):
            if po.type_code != S.SET_TC or not po.class_flag:
                continue
            cs = S.class_section(po)
            assert cs["declared"] == S.declared_count(po.raw_properties())
            assert cs["states"] == len(S.state_children(objs, i))
            if po.subtype_size & 2:
                assert cs["hotspot"] is not None
            seen += 1
    assert seen == 6  # FxSquareSet, FxHalfSet; FxOnOffSet, FxMultiSet, FxDefaultSet;
    #                            FxTripleSet


@pytest.mark.parametrize("source", ["fixture", pytest.param("ctdraw", marks=pytest.mark.golden)])
def test_state_records_reproduce_the_engine_bytes(fx, source):
    """Rebuilt states reproduce CtDraw's bytes."""
    from ctgedit.format.symbols import SymbolResolver

    path = fx.path
    if source == "ctdraw":
        src = fxinstall.oracle("pages", "FxMain/FxSymbols2", "page.ctg")
        path = os.path.join(fx.install.project("FxMain"), "FxOracleSymbols2.ctg")
        shutil.copyfile(src, path)
    d = EditableDocument.open(path)
    SET = _find(d, "FxSquareSet")
    states = d.page.objects[SET].members
    assert len(states) == 2
    objs = parse_ctg(path).page.objects
    res = SymbolResolver.for_page(path)
    setpo = objs[SET]
    chain = S.set_chain(setpo)
    for k in states:
        sym = S.state_symbol(objs[k])
        assert sym is not None and S.is_group_state(objs[k]), k
        info = P.element_info(res, sym[0], sym[1], "ctl")
        end = S.subtree_end(objs, k)
        mine = {G.get_handle(objs[j]) for j in range(k + 1, end)}
        used = {G.get_handle(o) for o in objs} - mine - {None}
        recs = S.state_records(
            G.get_handle(objs[k]),
            sym,
            info,
            S.hotspot(setpo),
            chain,
            used,
            lib_path=d.library_path(sym[0], sym[1], "ctl"),
            resolver=res,
        )
        assert [writer.object_payload(r) for r in recs] == [writer.object_payload(o) for o in objs[k:end]], k
    # the set's own fields through the record builder, finished from its states
    from ctgedit.format import genie_place as GP, records as R

    r = GP.decode(setpo)
    copy = R.new_record(
        "symbol_set",
        handle=r.handle,
        hotspot=S.hotspot(setpo),
        local=r.slots[0][1][4:],
        props=tuple(r.entries[0][1].pairs),
        style=R.Style.unpack(r.style),
    )
    S.finish_set(copy, [objs[k] for k in states], 2)
    # (the builder makes an unnamed set: the fixture's set is named, so the copy takes
    # the stored header name before the bytes are compared)
    rc = GP.decode(copy)
    rc.name = r.name
    assert GP.to_po(GP.encode(rc)).body_raw == setpo.body_raw


def test_dummy_marker_is_a_placeholder_at_the_hotspot():
    """The DummyMarker of a cleared middle state: Local = Mods = T(hotspot),
    Total = the set's frame after it, the class part = the hotspot rect +
    point, the header = that rect under Total, the plain black style."""
    from ctgedit.format import genie_place as GP, records as R

    d = S.make_dummy_record(9, (40, 50), [((1.0, 0.0, 0.0, 1.0, 100.0, 20.5), 1, (0, 0))])
    r = GP.decode(d)
    assert (d.type_code, d.subtype_size, r.handle) == (S.DUMMY_TC, 0x1A, 9)
    assert r.slots[0] == r.slots[1] == (1, (1.0, 0.0, 0.0, 1.0, 40.0, 50.0))
    assert r.slots[2] == (1, (1.0, 0.0, 0.0, 1.0, 140.0, 70.5))
    assert r.cls == struct.pack("<6h", 40, 50, 41, 51, 40, 50)
    # (the class rect again under Total, like the engine-authored ones)
    assert (r.anchor, r.rect1, r.rect2) == ((180, 120), (180, 120, 181, 121), (180, 120, 181, 121))
    assert r.style == R.DEFAULT_STYLE.pack() and r.cs == R.colour_state()


def test_multi_state_labels_put_condition_a_in_the_low_bit():
    assert S.combination_text(1, 2) == "A true, B false"
    assert S.combination_text(2, 2) == "A false, B true"
    assert S.combination_label(0, 3) == "A\u0305 B\u0305 C\u0305"
    assert S.state_names(S.ON_OFF, 2) == ["OFF symbol", "ON symbol"]
    assert S.state_names(S.ANIMATED, 3) == ["Off frame", "Frame 1", "Frame 2"]
    assert S.capacity(S.MULTI_STATE, 3) == 8 and S.capacity(S.ARRAY) == 256


# --------------------------------------------------------------------------- ungroup
def test_ungroup_is_refused_for_a_symbol_set(doc, fx):
    SET = fx.set
    before = _payloads(doc)
    for i in (SET, fx.off):  # the set, or one of its states
        with pytest.raises(SymbolSetError, match="cannot be ungrouped"):
            doc.ungroup_object(i)
    assert _payloads(doc) == before
    assert not doc.can_undo()


# --------------------------------------------------------------------------- editing
def test_symbol_set_info_shows_off_and_on_symbols(doc, fx):
    SET = fx.set
    info = doc.symbol_set_info(SET)
    assert info["type"] == S.ON_OFF and info["expressions"] == ["FX_PUMP2"]
    assert [s["symbol"] for s in info["states"]] == [
        ("lights_square_small", "grey"),
        ("lights_square_small", "red"),
    ]
    assert info["editable"] and info["capacity"] == 2
    assert doc.symbol_set_info(fx.other) is None  # a free bitmap


def test_unchanged_apply_is_byte_identical(doc, fx):
    SET = fx.set
    before = _payloads(doc)
    info = doc.symbol_set_info(SET)
    doc.apply_symbol_set(SET, info["type"], info["expressions"], doc.symbol_set_specs(SET))
    assert _payloads(doc) == before


def test_swapping_the_on_symbol_rederives_its_placement(doc, fx):
    SET = fx.set
    before = _payloads(doc)
    doc.set_state_symbol(SET, 1, ("lights", "light_1_green"))
    objs = doc.parsed.page.objects
    st = objs[fx.on]
    assert P.walk(st)["link"][1] == "lights.light_1_green.<ctl"
    assert st.subtype_size == 0x19  # LINK + bags + dynamic
    # light_1_green is a group-root symbol: the state is its instance forest
    assert S.is_group_state(st) and st.class_flag == 1
    # Local = T(hotspot); slot 2 = Local o T(-e); slot 3 = the set's Total o slot 2
    info = P.element_info(doc._resolver(), "lights", "light_1_green", "ctl")
    (s1, s2, s3), _e = P.read_slots(st.body_raw)
    assert (s1[1][4], s1[1][5]) == (25.0, 105.0)  # FxSquareSet's hot spot
    t2, m2 = P.slot2_for_linked(s1[1], s1[0], info["e"])
    assert s2[1] == t2
    t3, m3 = P.apply_parent(t2, m2, S.set_chain(objs[SET]))
    assert s3[1] == t3
    r1, anchor, _R = P.derive_geom(t3, m3, info["N"])
    gh = G.parse_geom_header(st.body_raw)
    assert tuple(gh.rect1) == r1 and tuple(gh.anchor) == anchor
    # (a group root's Bounds are its element root's, under Total: one more right and down)
    assert tuple(gh.rect2) == (r1[0], r1[1], r1[2] + 1, r1[3] + 1)
    # the OFF state is untouched; the set keeps the first state's extent
    assert writer.object_payload(objs[fx.off]) == before[fx.off]
    assert tuple(G.parse_geom_header(objs[SET].body_raw).rect1) == (20, 100, 30, 110)
    # the page import table lists the new element
    from ctgedit.format import links

    names = [n.lower() for _g, n in links.parse_link_table(doc.parsed.page.link_table_raw)[1]]
    assert any(n.startswith("lights.light_1_green.<ctl") for n in names)
    # one undo step
    doc.undo()
    assert _payloads(doc) == before
    doc.redo()
    assert doc.symbol_set_info(SET)["states"][1]["symbol"] == ("lights", "light_1_green")


def test_clear_middle_state_leaves_a_placeholder_last_state_goes(doc, fx):
    SET = fx.set
    doc.clear_state(SET, 0)
    objs = doc.parsed.page.objects
    info = doc.symbol_set_info(SET)
    assert [s["kind"] for s in info["states"]] == ["empty", "linked"]
    assert objs[fx.off].type_code == S.DUMMY_TC
    assert S.class_section(objs[SET])["states"] == 2
    # clearing the last one removes it and the cleared slot before it
    doc.clear_state(SET, 1)
    assert doc.symbol_set_info(SET)["states"] == []
    assert doc.parsed.page.objects[SET].class_flag == 0
    assert S.class_section(doc.parsed.page.objects[SET])["states"] == 0


def test_type_change_rebuilds_the_state_count(doc, fx):
    SET = fx.set
    grey, red = ("lights_square_small", "grey"), ("lights_square_small", "red")
    green = ("lights", "light_1_green")
    doc.apply_symbol_set(SET, S.MULTI_STATE, ["FX_PUMP2", "FX_PUMP3"], [grey, red, green, red])
    po = doc.parsed.page.objects[SET]
    props = po.raw_properties()
    assert props["TXTMODE"] == "1" and props["TXT_B"] == "FX_PUMP3"
    assert S.class_section(po)["declared"] == 4
    assert len(doc.symbol_set_info(SET)["states"]) == 4
    # back to On / Off: only the first two states and TXT_A survive
    doc.set_symbol_set_type(SET, S.ON_OFF)
    po = doc.parsed.page.objects[SET]
    assert "TXT_B" not in po.raw_properties()
    assert [s["symbol"] for s in doc.symbol_set_info(SET)["states"]] == [grey, red]
    assert S.class_section(po)["declared"] == 2
    # Array: SET_ARR carries the expression, TXT_A goes, 256 declared
    doc.apply_symbol_set(SET, S.ARRAY, ["FX_MODE"], [grey, red, green])
    po = doc.parsed.page.objects[SET]
    props = po.raw_properties()
    assert props["SET_ARR"] == "FX_MODE" and "TXT_A" not in props
    assert props["TXTMODE"] == "2" and S.class_section(po)["declared"] == 256
    assert len(doc.symbol_set_info(SET)["states"]) == 3
    doc.remove_state(SET, 0)
    assert [s["symbol"] for s in doc.symbol_set_info(SET)["states"]] == [red, green]


def test_trailing_blank_condition_is_dropped_with_its_states(doc, fx):
    """CtDraw's saver counts Multi-state conditions up to the LAST non-blank
    one, so a blank condition at the end does not survive a
    reload: OK drops it and the states that need it TRUE, keeping the class
    section's declared count and state count consistent."""
    SET = fx.set
    grey, red = ("lights_square_small", "grey"), ("lights_square_small", "red")
    green = ("lights", "light_1_green")
    doc.apply_symbol_set(SET, S.MULTI_STATE, ["FX_PUMP2", ""], [grey, red, green, red])
    po = doc.parsed.page.objects[SET]
    cs = S.class_section(po)
    assert (cs["declared"], cs["states"]) == (2, 2)
    info = doc.symbol_set_info(SET)
    assert info["expressions"] == ["FX_PUMP2"] and len(info["states"]) == 2
    # a blank condition in the MIDDLE is kept: it evaluates FALSE at runtime
    doc.apply_symbol_set(SET, S.MULTI_STATE, ["", "FX_PUMP3"], [grey, red, green, red])
    po = doc.parsed.page.objects[SET]
    assert S.class_section(po)["declared"] == 4
    assert len(doc.symbol_set_info(SET)["states"]) == 4


def test_unchanged_apply_records_no_undo_step(doc, fx):
    SET = fx.set
    info = doc.symbol_set_info(SET)
    doc.apply_symbol_set(SET, info["type"], info["expressions"], doc.symbol_set_specs(SET))
    doc.set_symbol_set_type(SET, S.ON_OFF)
    assert not doc.can_undo() and not doc.dirty


def test_bad_input_is_refused_with_a_reason(doc, fx):
    SET = fx.set
    with pytest.raises(SymbolSetError, match="not in any library"):
        doc.set_state_symbol(SET, 1, ("no_such_library", "nothing"))
    with pytest.raises(SymbolSetError, match="at most 128"):
        doc.apply_symbol_set(SET, S.ON_OFF, ["X" * 129], doc.symbol_set_specs(SET))
    with pytest.raises(SymbolSetError, match="no state 2"):
        doc.set_state_symbol(SET, 2, ("lights", "light_1_green"))


# --------------------------------------------------------------------------- create
def test_create_on_off_set_one_undo_step_and_registers(doc):
    from ctgedit.format import links
    from ctgedit.io import pgregister as R

    before = _payloads(doc)
    links_before = doc.parsed.page.link_table_raw
    i = doc.create_symbol_set((300, 200), exprs=["Tag1"])
    objs = doc.parsed.page.objects
    assert doc.page.objects[i].kind is ObjectKind.SYMBOL_SET
    info = doc.symbol_set_info(i)
    assert [s["symbol"] for s in info["states"]] == [("lights", "light_1_black"), ("lights", "light_1_red")]
    setpo = objs[i]
    assert setpo.subtype_size == 0x1A and setpo.class_flag == 2
    assert setpo.raw_properties() == {"TXT_A": "Tag1", "TXTMODE": "0"}
    # the first state's top-left lands on the click point
    first = G.parse_geom_header(objs[i + 1].body_raw)
    assert tuple(first.rect1[:2]) == (300, 200)
    assert tuple(G.parse_geom_header(setpo.body_raw).rect1) == tuple(first.rect1)
    names = [n.lower() for _g, n in links.parse_link_table(doc.parsed.page.link_table_raw)[1]]
    assert any(n.startswith("lights.light_1_black.<ctl") for n in names)
    # pgdynobj rows: the set (TXT_A / TXTMODE) + both states (GROUP = its AN)
    doc.save()
    an = str(G.live_an(setpo))
    rows = [r for r in R.page_rows(doc.path, PAGE)["pgdynobj"] if r["AN"] == an or r.get("GROUP") == an]
    assert rows[0]["TYPE"] == "0x000C" and rows[0]["TXT_A"] == "Tag1" and rows[0]["TXTMODE"] == "0"
    # the states are group-root symbols' forests: a 0x0008 row each, their ellipses' rows
    # GROUP = the state's AN (LIVE, the fixture oracle: CtDraw's rows of FxSymbols' sets)
    assert [r["TYPE"] for r in rows[1:]] == ["0x0008", "0x0008"]
    for st in rows[1:]:
        kids = [r for r in R.page_rows(doc.path, PAGE)["pgdynobj"] if r.get("GROUP") == st["AN"]]
        assert [r["TYPE"] for r in kids] == ["0x0011"]
    # reopened, it is still a symbol set with both states
    again = EditableDocument.open(doc.path)
    assert again.page.objects[i].kind is ObjectKind.SYMBOL_SET
    assert len(again.symbol_set_info(i)["states"]) == 2
    # one undo step removes the set, its states and the import entries
    doc.undo()
    assert _payloads(doc) == before and doc.parsed.page.link_table_raw == links_before


def test_move_keeps_the_set_hotspot_with_the_states(doc):
    """The new set's hot spot is the click point plus the first state's element origin
    (Include's lights.light_1_black stand-in: a 20 x 20 element with its origin at
    (10, 10), so (310, 210)); a move leaves it in the set's local frame."""
    i = doc.create_symbol_set((300, 200))
    assert S.hotspot(doc.parsed.page.objects[i]) == (310, 210)
    objs = doc.parsed.page.objects
    doc.move_objects(doc.subtree(i), 10, 5)
    setpo = objs[i]
    gh = G.parse_geom_header(setpo.body_raw)
    st = G.parse_geom_header(objs[i + 1].body_raw)
    assert tuple(st.rect1[:2]) == (310, 205)
    assert tuple(gh.rect1) == tuple(st.rect1)
    # the hotspot stays in the set's local frame; the move is on its Local
    assert S.hotspot(setpo) == (310, 210)
    (s1, _s2, _s3), _e = P.read_slots(setpo.body_raw)
    assert (s1[1][4], s1[1][5]) == (10.0, 5.0)
    # the editor draws ONE state (the first with art), like CtDraw's canvas; each state is
    # light_1_black's / light_1_red's instance forest (a root, then its ellipse)
    st0, st1 = doc.page.objects[i].members
    assert (st0, st1) == (i + 1, i + 3)
    assert not any(doc.page.objects[k].raw.get("_state_hidden") for k in (i + 1, i + 2))
    assert all(doc.page.objects[k].raw.get("_state_hidden") for k in (i + 3, i + 4))


def test_resize_keeps_the_first_state_extent_with_a_placeholder(doc):
    """A cleared middle slot (DummyMarker) sits off at 2 x the hotspot; the
    set's extent stays its first real state's (not the union)."""
    i = doc.create_symbol_set((300, 200), states=[None, ("lights", "light_1_red")])
    objs = doc.parsed.page.objects
    assert objs[i + 1].type_code == S.DUMMY_TC
    real = G.parse_geom_header(objs[i + 2].body_raw)
    assert tuple(G.parse_geom_header(objs[i].body_raw).rect1) == tuple(real.rect1)
    l, t, r, b = real.rect1
    doc.set_object_rect(i, (l, t, l + 2 * (r - l), t + 2 * (b - t)))
    objs = doc.parsed.page.objects
    real = G.parse_geom_header(objs[i + 2].body_raw)
    assert real.rect1[2] - real.rect1[0] == 2 * (r - l)
    assert tuple(G.parse_geom_header(objs[i].body_raw).rect1) == tuple(real.rect1)


# --------------------------------------------------------------------------- V3/V4 Animated Symbol
def test_animated_symbol_state_wells(fx_install):
    """A V3 / V4 Animated Symbol on a copy of FxShapes (in FxMain: Include's icons.help1
    is on its chain)."""
    p = os.path.join(fx_install.project("FxMain"), "FxAnimSym.ctg")
    shutil.copy2(fx_install.page("FxShapes"), p)
    d = EditableDocument.open(p)
    i = d.create_object("animated_symbol", (100, 100, 120, 118))
    assert d.is_animated_symbol(i)
    states = [("icons", "help1"), None, ("icons", "help1")]
    assert d.apply_animated_symbol(i, "FX_PUMP1", "SOFT", states)
    props = d.parsed.page.objects[i].raw_properties()
    assert props["OFF_SYMBOL"] == "icons.help1" and props["SYMBOL_2"] == "icons.help1"
    assert "SYMBOL_1" not in props and props["EXPR"] == "FX_PUMP1"
    info = d.animated_symbol_info(i)
    assert info["states"][:3] == states and info["mode"] == "SOFT"
    # its natural extent = the first state's bitmap, bottom-left at the anchor
    gh = G.parse_geom_header(d.parsed.page.objects[i].body_raw)
    n = P.element_info(d._resolver(), "icons", "help1", "ctl")["N"]
    assert gh.rect2[2] - gh.rect2[0] == n[2] - n[0]
    # the same sheet edits it: Off symbol + Symbols 1..8, no Type choice
    _app()
    from ctgedit.ui.dialog.symbol_set_dialog import SymbolSetDialog

    dlg = SymbolSetDialog(d, i, picker=lambda _p: ("icons", "help1"))
    assert dlg.windowTitle().startswith("Animated Symbol Properties")
    assert not dlg._type_box.isVisibleTo(dlg)
    assert dlg._list.rowCount() == 9
    assert dlg._list.item(0, 0).text() == "Off symbol"
    assert dlg._list.item(2, 1).text() == "icons.help1"
    assert dlg._ed_expr.text() == "FX_PUMP1" and dlg._lbl_expr.text() == "Animate when:"
    dlg._list.setCurrentCell(1, 1)
    dlg._set_state(1)
    assert dlg.apply() is True
    assert d.parsed.page.objects[i].raw_properties()["SYMBOL_1"] == "icons.help1"
    d.undo()
    d.undo()
    assert "OFF_SYMBOL" not in d.parsed.page.objects[i].raw_properties()


# --------------------------------------------------------------------------- the sheet
def test_sheet_shows_on_off_symbols_and_sets_one(doc, fx):
    SET = fx.set
    _app()
    from ctgedit.ui.dialog.symbol_set_dialog import SymbolSetDialog
    from ctgedit.ui.help import dialog_topic

    dlg = SymbolSetDialog(doc, SET, picker=lambda _parent: ("lights", "light_1_green"))
    assert dialog_topic(dlg) == "symbol-sets#choose-the-type-and-the-symbols"
    assert dlg._type_buttons[S.ON_OFF].isChecked()
    assert dlg._ed_expr.text() == "FX_PUMP2" and dlg._lbl_expr.text() == "ON symbol when:"
    assert dlg._ed_expr.maxLength() == 128
    rows = [(dlg._list.item(r, 0).text(), dlg._list.item(r, 1).text()) for r in range(dlg._list.rowCount())]
    assert rows == [("OFF symbol", "lights_square_small.grey"), ("ON symbol", "lights_square_small.red")]
    # Set the ON symbol, then OK -> one undo step
    dlg._list.setCurrentCell(1, 1)
    dlg._set_state(1)
    assert dlg._list.item(1, 1).text() == "lights.light_1_green"
    assert dlg.apply() is True and dlg.changed
    assert doc.symbol_set_info(SET)["states"][1]["symbol"] == ("lights", "light_1_green")
    assert doc._undo[-1].label == "symbol set"
    # Clear the OFF symbol: an empty middle slot (the ON symbol keeps its index)
    dlg._clear_state(0)
    assert dlg._list.item(0, 1).text() == ""  # an empty value is blank
    dlg.apply()
    assert [s["kind"] for s in doc.symbol_set_info(SET)["states"]] == ["empty", "linked"]


def test_sheet_multi_state_conditions_and_array(doc, fx):
    SET = fx.set
    _app()
    from ctgedit.ui.dialog.symbol_set_dialog import SymbolSetDialog

    dlg = SymbolSetDialog(doc, SET, picker=lambda _p: ("lights", "light_1_green"))
    dlg._type_buttons[S.MULTI_STATE].setChecked(True)
    assert dlg._cond_box.isVisibleTo(dlg) and not dlg._expr_row.isVisibleTo(dlg)
    assert dlg._conds.rowCount() == 1 and dlg._list.rowCount() == 2
    dlg._add_condition()
    dlg._conds.item(1, 1).setText("FX_PUMP3")
    assert dlg._list.rowCount() == 4
    assert dlg._list.item(2, 0).text() == S.combination_label(2, 2)
    for _ in range(3):
        dlg._add_condition()
    assert dlg._conds.rowCount() == 5 and not dlg._btn_cond_add.isEnabled()
    for _ in range(3):
        dlg._conds.setCurrentCell(dlg._conds.rowCount() - 1, 1)
        dlg._delete_condition()
    assert dlg.values()["expressions"] == ["FX_PUMP2", "FX_PUMP3"]
    dlg.apply()
    assert doc.parsed.page.objects[SET].raw_properties()["TXTMODE"] == "1"
    # Array: add a symbol; the status warns when symbols would be dropped
    dlg._type_buttons[S.ARRAY].setChecked(True)
    assert dlg._lbl_expr.text() == "Array expression:"
    dlg._add_state()
    assert dlg.values()["states"][-1] == ("lights", "light_1_green")
    dlg._type_buttons[S.ON_OFF].setChecked(True)
    assert "will be removed" in dlg._status.text()


def test_sheet_warns_about_a_blank_last_condition(doc, fx):
    SET = fx.set
    _app()
    from ctgedit.ui.dialog.symbol_set_dialog import SymbolSetDialog

    dlg = SymbolSetDialog(doc, SET, picker=lambda _p: ("lights", "light_1_green"))
    dlg._type_buttons[S.MULTI_STATE].setChecked(True)
    dlg._add_condition()
    dlg._list.setCurrentCell(3, 1)
    dlg._set_state(3)  # a symbol for 'A and B true'
    assert "Condition B is blank" in dlg._status.text()
    assert "1 symbol(s)" in dlg._status.text()
    dlg._conds.item(1, 1).setText("FX_PUMP3")  # typing the condition clears it
    assert "blank" not in dlg._status.text()


def test_genie_member_set_sheet_is_read_only(fx_install):
    """A set inside a genie takes its symbols from the genie's library
    definition: the sheet says so and edits nothing (FxGenies' FxLamp, fxgenie.fx_lamp)."""
    d = EditableDocument.open(fx_install.page("FxGenies"))
    objs = d.parsed.page.objects
    member = next(
        (
            i
            for i, po in enumerate(objs)
            if po.type_code == S.SET_TC
            and po.class_flag
            and d.page.objects[i].parent is not None
            and any(d.page.objects[a].is_genie for a in d.page.ancestors(i))
        ),
        None,
    )
    assert member is not None
    assert any(a == _find(d, "FxLamp") for a in d.page.ancestors(member))
    ok, why = d.symbol_set_editable(member)
    assert not ok and "genie" in why
    with pytest.raises(SymbolSetError):
        d.apply_symbol_set(member, S.ON_OFF, [""], [])
    # the sheet shows the reason, names where the states come from, and is read-only
    _app()
    from ctgedit.ui.dialog.symbol_set_dialog import SymbolSetDialog

    dlg = SymbolSetDialog(d, member)
    assert "genie" in dlg._banner.text() and not dlg._states_box.isEnabled()
    texts = {dlg._list.item(r, 1).text() for r in range(dlg._list.rowCount())}
    assert "(from the library definition)" in texts
    assert dlg.apply() is None and not d.can_undo()


def test_properties_panel_shows_a_symbol_set_box(doc, fx):
    SET = fx.set
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.panels.properties import PropertyPanel

    panel = PropertyPanel()
    panel.set_target(doc, [SET])
    boxes = {b.title(): b for b in panel.widget().findChildren(QtWidgets.QGroupBox)}
    assert "Symbol set" in boxes and "Group" not in boxes
    labels = [w.text() for w in boxes["Symbol set"].findChildren(QtWidgets.QLabel)]
    assert "On / Off" in labels and "2" in labels


# --------------------------------------------------------------------------- the window
def _win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    return MainWindow()


def test_window_double_click_ungroup_and_tool(doc, fx):
    SET = fx.set
    from PySide6 import QtWidgets
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.dialog.symbol_set_dialog import SymbolSetDialog

    w = _win()
    try:
        w.set_document(doc)
        w._keep_tool = w.view.KEEP_TOOL = False  # (off: back to Select after each)
        # double-click on a state opens the SET's sheet (not the group hint)
        w._on_object_activated(fx.off)
        dlg = w._dialogs.get(("symset", SET))
        assert isinstance(dlg, SymbolSetDialog)
        dlg.reject()
        QtWidgets.QApplication.processEvents()
        # Ungroup refuses and explains
        before = _payloads(w.doc)
        w._on_dock_selection([SET])
        w._ungroup_selection()
        assert "cannot be ungrouped" in w.statusBar().currentMessage()
        assert _payloads(w.doc) == before
        # the Symbol Set tool: a click creates a set and opens its sheet
        assert "symbol_set" in w.tool_actions
        n = len(w.doc.parsed.page.objects)
        w._set_tool("symbol_set")
        w._on_draw_committed("symbol_set", {"rect": (400, 300, 416, 316)})
        QtWidgets.QApplication.processEvents()
        # the set and its two default states, each the instance forest of a lights symbol
        assert len(w.doc.parsed.page.objects) == n + 5
        assert w.doc.is_symbol_set(n)
        assert isinstance(w._dialogs.get(("symset", n)), SymbolSetDialog)
        assert w.view.tool() == "select"
        # Object Properties on a set opens its sheet; 'Other Properties…' the generic one
        from ctgedit.ui.objprops import ObjectPropertiesDialog

        w.object_properties(SET)
        sheet = w._dialogs.get(("symset", SET))
        assert isinstance(sheet, SymbolSetDialog)
        sheet._other_properties()
        assert isinstance(w._dialogs.get(("objprops", SET)), ObjectPropertiesDialog)
        # a new V3/V4 Animated Symbol opens its state sheet right away
        w._create_legacy("animated_symbol")
        QtWidgets.QApplication.processEvents()
        k = w._sel[0]
        assert w.doc.is_animated_symbol(k)
        assert w._dialogs.get(("symset", k))._legacy
        for dlg in list(w._dialogs.values()):
            dlg.reject()
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)


def test_template_set_sheet_shows_close_and_edit_template(fx_install):
    """A symbol set of the page template is read-only: the sheet shows only
    Close plus Edit Template (the window opens the template). FxTplSets' template
    fx_alt sets@-XGA holds the set FxTplSet."""
    d = EditableDocument.open(fx_install.page("FxTplSets"))
    idx = next(
        (i for i, o in enumerate(d.page.objects) if d.is_symbol_set(i) and o.raw.get("_from_template")), None
    )
    assert idx is not None and idx == _find(d, "FxTplSet")
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.dialog.symbol_set_dialog import SymbolSetDialog

    opened = []

    class Win(QtWidgets.QWidget):
        def _owner_definition(self, index):
            return (r"C:\x\fx_alt.ctt", "sets@-XGA")

        def _open_element(self, *a):
            opened.append(a)

    win = Win()
    dlg = SymbolSetDialog(d, idx, win)
    box = dlg._buttons
    texts = sorted(b.text().replace("&", "") for b in box.buttons() if b.isVisibleTo(dlg))
    assert texts == ["Close", "Edit Template"]
    assert not dlg._states_box.isEnabled() and "template" in dlg._banner.text()
    dlg._btn_definition.click()
    assert opened == [(r"C:\x\fx_alt.ctt", "sets@-XGA")]
    assert dlg.result() == QtWidgets.QDialog.Rejected
    # without a window that can open it: Close only
    dlg2 = SymbolSetDialog(d, idx)
    assert [b.text().replace("&", "") for b in dlg2._buttons.buttons() if b.isVisibleTo(dlg2)] == ["Close"]
    assert "(AN " in dlg2.windowTitle() and "—" not in dlg2.windowTitle()


def test_a_genies_symbol_set_shows_and_keeps_its_parameter_expression(fx_install):
    """In a genie window an On / Off set member's expression is its %parameter% value
    (fxgenie.fx_lamp: '%Tag%'), not the record's own value bag (the genie keeps no expression
    there and its load derives that bag again)."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import genie_place as GP

    lib = fx_install.file("FxLib", "fxgenie.ctm")
    doc = ElementDocument.open_element(str(lib), "fx_lamp")
    i = next(k for k in range(len(doc.parsed.page.objects)) if doc.is_symbol_set(k))
    info = doc.symbol_set_info(i)
    assert info["expressions"] == ["%Tag%"]
    steps = len(doc._undo)
    doc.apply_symbol_set(i, info["type"], ["%Tag% AND NOT %Fault%"], doc.symbol_set_specs(i))
    assert len(doc._undo) == steps + 1
    assert doc.symbol_set_info(i)["expressions"] == ["%Tag% AND NOT %Fault%"]
    doc.save()
    again = ElementDocument.open_element(str(lib), "fx_lamp")
    j = next(k for k in range(len(again.parsed.page.objects)) if again.is_symbol_set(k))
    assert again.symbol_set_info(j)["expressions"] == ["%Tag% AND NOT %Fault%"]
    tmpl = dict(GP.decode(again.parsed.page.objects[j]).entries[0][2].pairs)
    assert tmpl["TXT_A"] == "%Tag% AND NOT %Fault%"
    # the new parameter is declared: a paste asks for it
    assert list(GP.declared_params_of(str(lib), "fx_lamp")) == ["Tag", "Fault"]
    doc.undo()
    assert doc.symbol_set_info(i)["expressions"] == ["%Tag%"]


def test_a_placed_genies_set_of_group_root_symbols(fx_install):
    """A placed genie's set of group-root symbols."""
    from ctgedit.editing import ElementDocument

    lib = os.path.join(fx_install.project("FxMain"), "fxgsets.ctm")
    d = ElementDocument.new_element(lib, "fx_gsets", "genie")
    d.create_symbol_set(
        (0, 0), kind=S.ON_OFF, exprs=["%Tag%"], states=[("fxsym", "fx_off"), ("fxsym", "fx_on")]
    )
    d.create_object("text", (20, 0, 100, 16), text="%Tag%")
    d.save()
    page = fx_install.page("FxGenies")
    pd = EditableDocument.open(page)
    g = pd.place_library_element("fxgsets", "fx_gsets", "ctm", (300, 300), {"Tag": "FX_PUMP1"})
    pd.save()
    pd = EditableDocument.open(page)
    objs, sem = pd.parsed.page.objects, pd.page.objects
    s = next(k for k in pd.subtree(g) if objs[k].type_code == S.SET_TC)
    assert [
        (objs[k].type_code, objs[k].class_flag, objs[k].subtype_size)
        for k in range(s + 1, S.subtree_end(objs, s))
    ] == [(0x08, 1, 0x18), (0x11, 0, 0x18), (0x08, 1, 0x18), (0x11, 0, 0x18)]
    assert sem[s].members == [s + 1, s + 3]
    info = pd.symbol_set_info(s)
    assert [st["kind"] for st in info["states"]] == ["stripped", "stripped"]
    assert not info["editable"]
    # the editor draws the first state; the other hides with its ellipse
    assert not any(sem[k].raw.get("_state_hidden") for k in (s + 1, s + 2))
    assert all(sem[k].raw.get("_state_hidden") for k in (s + 3, s + 4))
    m = pd.export_page_model(owners=True)
    ss = next(o["symbolSet"] for o in m["objects"] if o["record"] == s)
    assert ss["names"] == ["fxsym.fx_off", "fxsym.fx_on"]
    assert ss["kinds"] == ["stripped", "stripped"]


def _set_of(*states):
    """A set (record 0) of ``states``: (kind, type code, has art, member kinds) each; a
    state with member kinds is a group-root symbol's instance forest (its 0x08 root over
    them). The records' GraphicObjects and stand-in parsed records (type codes only)."""
    from ctgedit.model.document import GraphicObject

    objs = [GraphicObject(kind=ObjectKind.SYMBOL_SET, an=1)]
    tcs = [S.SET_TC]
    for kind, tc, art, members in states:
        k = len(objs)
        o = GraphicObject(kind=kind, an=k + 1, parent=0)
        if art:
            o.raw["_embedded_bmp"] = (1, 1, b"\0\0\0\0", False)
        objs.append(o)
        tcs.append(tc)
        objs[0].members.append(k)
        for mk, mtc in members:
            objs.append(GraphicObject(kind=mk, an=len(objs) + 1, parent=k))
            tcs.append(mtc)
            o.members.append(len(objs) - 1)
    return objs, [types.SimpleNamespace(type_code=tc) for tc in tcs]


@pytest.mark.parametrize("second", ["bitmap", "forest"])
def test_a_repaired_state_0_is_drawn_again(second):
    """A repaired state 0 is drawn again."""
    from ctgedit.io.readerparts import symbols as RS

    if second == "bitmap":
        objs, pobjs = _set_of((ObjectKind.SYMBOL, 0x07, False, ()), (ObjectKind.SYMBOL, 0x07, True, ()))
    else:
        objs, pobjs = _set_of(
            (ObjectKind.SYMBOL, 0x07, False, ()),
            (ObjectKind.GENIE, 0x08, False, [(ObjectKind.ELLIPSE, 0x11)]),
        )
    RS._select_set_state([objs[m] for m in objs[0].members], objs)
    assert objs[1].raw.get("_state_hidden") and not objs[2].raw.get("_state_hidden")
    objs[1].raw["_embedded_bmp"] = (1, 1, b"\0\0\0\0", False)  # the repair's graft
    RS._reselect_set_state(objs[0], objs, pobjs)
    assert not objs[1].raw.get("_state_hidden")
    assert all(o.raw.get("_state_hidden") for o in objs[2:])


def test_a_set_in_the_state_drawn_again_is_chosen_again():
    """A set in the state drawn again is chosen again."""
    from ctgedit.model.document import GraphicObject
    from ctgedit.io.readerparts import symbols as RS

    bmp = (1, 1, b"\0\0\0\0", False)
    # 0 outer set > 1 forest root > 2 nested set > 3, 4 slots; 5 the bitmap state
    kinds = [
        (ObjectKind.SYMBOL_SET, S.SET_TC, None),
        (ObjectKind.GENIE, 0x08, 0),
        (ObjectKind.SYMBOL_SET, S.SET_TC, 1),
        (ObjectKind.SYMBOL, 0x07, 2),
        (ObjectKind.SYMBOL, 0x07, 2),
        (ObjectKind.SYMBOL, 0x07, 0),
    ]
    objs = [GraphicObject(kind=k, an=i + 1, parent=p) for i, (k, _tc, p) in enumerate(kinds)]
    for i, (_k, _tc, p) in enumerate(kinds):
        if p is not None:
            objs[p].members.append(i)
    pobjs = [types.SimpleNamespace(type_code=tc) for _k, tc, _p in kinds]
    objs[5].raw["_embedded_bmp"] = bmp
    RS._select_set_state([objs[1], objs[5]], objs)
    assert all(objs[i].raw.get("_state_hidden") for i in (1, 2, 3, 4))
    assert not objs[5].raw.get("_state_hidden")
    before = {i: dict(o.raw) for i, o in enumerate(objs)}
    RS._reselect_set_state(objs[2], objs, pobjs)  # inside the hidden state: left alone
    assert {i: dict(o.raw) for i, o in enumerate(objs)} == before
    objs[3].raw["_embedded_bmp"] = objs[4].raw["_embedded_bmp"] = bmp  # the repair
    for j in (0, 2):  # (the callers' record order)
        RS._reselect_set_state(objs[j], objs, pobjs)
    assert [bool(o.raw.get("_state_hidden")) for o in objs] == [False, False, False, False, True, True]
