"""Property writes go to the record's value bag, for every record class."""

import copy
import struct

import pytest

import fxinstall

from ctgedit.format import colourstate as CS
from ctgedit.format import factory, genie_place as GP, placement as PL, writer as W
from ctgedit.format.genie_norm import class_ov
from ctgedit.format.page import parse_ctg, parse_ctg_bytes
from ctgedit.io import pgregister as PR
from qtkit import app as _app

#: FxMain's pages the record-class tests read (records of every class, genies, members)
PAGES = ("FxGenies", "FxMembers", "FxSymbols", "FxParts")
#: the libraries with plain and flashing bitmaps (project, library)
BITMAP_LIBS = (("FxLib", "fxsym.ctl"), ("FxLib", "fxmore.ctl"), ("Include", "fxinc.ctl"))
#: the genie root the editing tests use (FxGenies), the symbol they place
GENIE_ROOT = "FxPump1"
SYMBOL = ("fxsym", "fx_box", "ctl")


def _objects(fx_install, name):
    return parse_ctg(fx_install.page(name)).page.objects


def _oracle_objects(name):
    """The records of FxMain's page ``name`` as CtDraw's PageOpen + PageSave wrote it."""
    return parse_ctg_bytes(fxinstall.oracle_bytes("pages", "FxMain/" + name, "page.ctg")).page.objects


def _strip_section(po):
    """Remove a record's dwFlags-0x8 section (what a record without one
    would look like)."""
    r = GP.decode(po)
    r.dw &= ~0x8
    r.entries = None
    W._store(po, r)


# --------------------------------------------------------------------------- the section
@pytest.mark.parametrize("page", PAGES)
def test_ensure_bag_section_is_a_byte_noop_on_records_with_a_section(fx_install, page):
    objs = _objects(fx_install, page)
    assert objs
    for po in objs:
        before = W.object_payload(po)
        assert W.ensure_bag_section(po)
        assert W.object_payload(po) == before


def _resynthesise_empty_groups(objs):
    """Strip the standard empty group off every record of ``objs`` that carries it and
    synthesise it again: each must come back byte-identical. The count checked."""
    n = 0
    for po in objs:
        r = GP.decode(po)
        if len(r.entries) != 1 or any(b.pairs for b in r.entries[0]):
            continue
        if [b.ov for b in r.entries[0]] != [0, class_ov(po.type_code), 0]:
            continue
        before = W.object_payload(po)
        s = copy.deepcopy(po)
        _strip_section(s)
        assert not s.subtype_size & 0x8 and W.object_payload(s) != before
        assert W.ensure_bag_section(s)
        assert W.object_payload(s) == before
        n += 1
    return n


def test_ensure_bag_section_writes_the_engine_empty_group(fx_install):
    """ensure_bag_section writes CtDraw's empty group."""
    assert _resynthesise_empty_groups([o for p in PAGES for o in _objects(fx_install, p)]) >= 20


@pytest.mark.golden
def test_ensure_bag_section_writes_ctdraws_empty_group():
    """The same on the pages as CtDraw saved them: the group it writes is the one
    ensure_bag_section synthesises."""
    assert _resynthesise_empty_groups([o for p in PAGES for o in _oracle_objects(p)]) >= 20


def test_record_without_a_section_takes_a_property():
    po = factory.create_object("line", nodes=[(0, 0), (40, 20)])
    _strip_section(po)
    assert W.ensure_object_property(po, "VISIBLE", "Tag1 = 1")
    assert po.subtype_size & 0x8
    assert W.value_bag(po) == [("VISIBLE", "Tag1 = 1")]
    L = CS.layout(po)  # the forward walk still lands
    assert L is not None and "style_at" in L


def test_every_factory_kind_and_group_has_a_section():
    """The seeds, the group record and the bitmap record already carry the
    (empty) section CtDraw writes."""
    kinds = [k for k in factory.available_kinds() if k != "symbol"]
    recs = [
        factory.create_object(
            k,
            rect=(10, 10, 60, 40),
            nodes=[(10, 10), (60, 40), (30, 60)] if k in ("line", "polygon", "polyline", "pipe") else None,
        )
        for k in kinds
    ]
    recs.append(factory.make_group_record(3, (0, 0, 10, 10), 2))
    recs.append(factory.make_bitmap_record(4, (0, 0, 2, 2), bytes(16), 2, 2))
    for po in recs:
        assert po.subtype_size & 0x8
        assert GP.decode(po).entries is not None


# --------------------------------------------------------------------------- writer
def test_add_property_goes_to_the_value_bag_and_keeps_empty_values():
    po = factory.create_object("rectangle_outline", rect=(10, 10, 60, 40))
    assert W.value_bag(po) == []
    assert W.add_object_property(po, "MVX", "Tag2")
    assert W.set_object_property(po, "MVX", "")  # cleared, not removed
    assert W.value_bag(po) == [("MVX", "")]
    assert W.remove_object_property(po, "MVX")
    assert W.value_bag(po) == []
    # the size-word overshoot (CtDraw's internal pair) survives the rewrites
    assert GP.decode(po).entries[0][1].ov == class_ov(0x0F)


def test_genie_root_property_lands_in_its_value_bag_not_its_bindings(fx_install):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxGenies"))
    (root,) = doc.find_objects_by_name(GENIE_ROOT)
    po = parse_ctg(fx_install.page("FxGenies")).page.objects[root]
    assert po.class_flag and (po.subtype_size & 0x201) == 0x201  # a genie root
    bindings = W.genie_bindings(po)
    assert W.ensure_object_property(po, "COMMENT", "Straw feed")
    r = GP.decode(po)
    assert r.entries[0][1].get("COMMENT") == "Straw feed"  # e0.b1
    assert r.entries[0][2].get("COMMENT") == "Straw feed"  # its template
    assert r.entries[-1][1].get("COMMENT") is None  # not a binding
    assert W.genie_bindings(po) == bindings
    assert "COMMENT" not in W.genie_bindings(po)


def test_nested_genie_containers_take_new_keys_in_e0_b1(fx_install):
    """Nested genie containers take new keys in e0.b1."""
    n = 0
    for page in ("FxGenies", "FxParts"):
        n += _nested_take_keys(_objects(fx_install, page))
    assert n >= 20


def _nested_take_keys(objs):
    par = PL.forest_parents(objs)
    n = 0
    for i, po in enumerate(objs):
        if not po.class_flag or par[i] is None:
            continue
        before = GP.decode(po).entries
        s = copy.deepcopy(po)
        assert W.ensure_object_property(s, "ROT", "WP02_Angle")
        after = GP.decode(s).entries
        assert after[0][1].get("ROT") == "WP02_Angle"
        assert [b.pairs for e in after[1:] for b in e] == [b.pairs for e in before[1:] for b in e]
        assert after[0][0].pairs == before[0][0].pairs
        n += 1
    return n


# --------------------------------------------------------------------------- dwFlags 0x10
def _check_dynamic_rule(page, objs):
    """is_dynamic of every record of ``objs`` equals its stored dwFlags 0x10."""
    assert objs
    par = PL.forest_parents(objs)
    owners = {par[k] for k, o in enumerate(objs) if o.type_code in CS.INPUT_TCS and par[k] is not None}
    for i, po in enumerate(objs):
        p = par[i]
        pdyn = p is not None and objs[p].type_code in CS.INHERIT_TCS and bool(objs[p].subtype_size & 0x10)
        assert CS.is_dynamic(po, parent_dynamic=pdyn, input_child=i in owners) == bool(
            po.subtype_size & 0x10
        ), (page, i)


@pytest.mark.parametrize("page", PAGES)
def test_dynamic_rule_matches_the_bit_our_save_stores(fx_install, page):
    """is_dynamic (own keys / flashing / input child / a dynamic group or symbol-set
    parent / always-dynamic classes) against every record of the fixture pages, which
    our own save wrote: the save and the rule agree."""
    _check_dynamic_rule(page, _objects(fx_install, page))


@pytest.mark.golden
@pytest.mark.parametrize("page", PAGES)
def test_dynamic_rule_reproduces_the_stored_bit(page):
    """The rule against every record of the pages as CtDraw's PageOpen + PageSave stored
    them."""
    _check_dynamic_rule(page, _oracle_objects(page))


def test_dynamic_rule_config_keys():
    po = factory.create_object("rectangle", rect=(0, 0, 10, 10))
    assert not CS.is_dynamic(
        po,
        {
            "COL_MAXON": "0x0,0x0",
            "AI_ON": "0",
            "AI_WIDTH": "3",
            "OrphanedFrom": "lib.el.<ctm",
            "SZXMINPC": "0",
        },
    )
    assert CS.is_dynamic(po, {"AI_ON": "1"})
    assert CS.is_dynamic(po, {"COMMENT": "tool tip"})
    assert CS.is_dynamic(po, {}, parent_dynamic=True)
    assert CS.is_dynamic(po, {}, input_child=True)
    grp = factory.make_group_record(1, (0, 0, 10, 10), 0)
    assert not CS.is_dynamic(grp, {})


def _check_bitmap_bits(lib, path):
    """Every bitmap record of the library at ``path``: is_dynamic == its stored 0x10.
    The count of bitmaps checked."""
    from ctgedit.format.symbols import SymbolLibrary

    lib_ = SymbolLibrary.open(path)
    n = 0
    for name in lib_.element_names():
        pb = lib_.element(name)
        objs = pb.objects if pb else []
        par = PL.forest_parents(objs)
        for k, po in enumerate(objs):
            if po.type_code != 7 or CS.bitmap_flashing(po) is None:
                continue
            p = par[k]
            pdyn = p is not None and objs[p].type_code in CS.INHERIT_TCS and bool(objs[p].subtype_size & 0x10)
            assert CS.is_dynamic(po, parent_dynamic=pdyn) == bool(po.subtype_size & 0x10), (lib, name, k)
            n += 1
    return n


def test_bitmap_dynamic_bit_follows_the_flash_dib(fx_install):
    """A bitmap's dynamic bit follows its flash DIB."""
    n = sum(_check_bitmap_bits(lib, fx_install.file(proj, lib)) for proj, lib in BITMAP_LIBS)
    assert n >= 5


@pytest.mark.golden
@pytest.mark.parametrize(
    "proj,lib",
    [("FxLib", "fxsym.ctl"), ("FxLib", "fxmore.ctl"), ("FxInclude", "fxinc.ctl"), ("FxLib", "fxbmpgrp.ctl")],
)
def test_bitmap_dynamic_bit_is_the_one_ctdraw_stores(proj, lib):
    """The same rule against the libraries as CtDraw's element open + save left them."""
    path = fxinstall.oracle("elements", "%s/%s" % (proj, lib), "library.ctl")
    assert _check_bitmap_bits(lib, path) >= 1


def _bitmap_parents(path):
    """(dynamic parent, flashing) -> the count of the library's bitmaps of that kind."""
    from ctgedit.format.symbols import SymbolLibrary

    lib_ = SymbolLibrary.open(path)
    kinds = {}
    for name in lib_.element_names():
        objs = lib_.element(name).objects
        par = PL.forest_parents(objs)
        for k, po in enumerate(objs):
            if po.type_code != 7 or par[k] is None:
                continue
            key = (bool(objs[par[k]].subtype_size & 0x10), bool(CS.bitmap_flashing(po)))
            kinds[key] = kinds.get(key, 0) + 1
    return kinds


def test_bitmap_dynamic_bit_inside_dynamic_and_static_groups(fx_install):
    """A bitmap member takes 0x10 from a dynamic group parent and from its own flash DIB,
    never from a static parent. FxLib's fxbmpgrp.ctl: six group elements, each a plain
    and a flashing 8 x 8 bitmap (and a frame); three groups carry VISIBLE (dynamic), three
    none."""
    path = fx_install.file("FxLib", "fxbmpgrp.ctl")
    assert _check_bitmap_bits("fxbmpgrp.ctl", path) >= 10
    kinds = _bitmap_parents(path)
    assert all(kinds.get((dyn, flash), 0) >= 2 for dyn in (True, False) for flash in (True, False)), kinds


def test_factory_flashing_bitmap_is_dynamic():
    red, green = bytes([255, 0, 0, 255]) * 16, bytes([0, 255, 0, 255]) * 16
    assert factory.make_bitmap_record(1, (0, 0, 4, 4), red, 4, 4, flash_rgba=green).subtype_size == 0x1A
    assert factory.make_bitmap_record(1, (0, 0, 4, 4), red, 4, 4).subtype_size == 0x0A
    # a flash half equal to the primary does not flash
    same = factory.make_bitmap_record(1, (0, 0, 4, 4), red, 4, 4, flash_rgba=red)
    assert CS.bitmap_flashing(same) is False and same.subtype_size == 0x0A


def test_factory_bitmap_stream_inflates_to_its_dib():
    """The 8x8 red factory bitmap's DIB stream is one zlib stream of the whole DIB
    (without the install's classic zlib.dll, Python's deflate writes it). That its bytes
    are exactly CtDraw's needs the AVEVA zlib.dll at test time:
    tests_live/test_bag_synthesis.py checks that."""
    import zlib
    from ctgedit.format.symbols import _find_stream

    po = factory.make_bitmap_record(925, (95, 95, 103, 103), bytes([255, 0, 0, 255]) * 64, 8, 8)
    off, raw = _find_stream(po.body_raw, 0)
    comp = struct.unpack_from("<I", po.body_raw, off - 4)[0]
    assert zlib.decompress(po.body_raw[off : off + comp]) == raw
    assert len(raw) >= 8 * 8 * 3


# --------------------------------------------------------------------------- editing
@pytest.fixture
def mixer(fx_install):
    """FxGenies of the fake install's copy; ``.fx_root`` = FxPump1's index."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxGenies"))
    (doc.fx_root,) = doc.find_objects_by_name(GENIE_ROOT)
    return doc


def _new(doc, kind):
    if kind == "group":
        a = doc.create_object("rectangle", rect=(10, 610, 40, 640), colour=0x123456)
        b = doc.create_object("line", nodes=[(50, 610), (80, 640)], colour=0x654321)
        return doc.group_objects([a, b])
    if kind == "bitmap":
        return doc.add_bitmap_object((5, 605, 9, 609), bytes([255, 0, 0, 255]) * 16, 4, 4)
    if kind == "symbol":
        return doc.place_library_element(*SYMBOL, (500, 300))
    nodes = {
        "line": [(10, 610), (90, 650)],
        "polygon": [(10, 610), (60, 610), (35, 650)],
        "polyline": [(10, 610), (60, 620), (35, 650)],
        "pipe": [(10, 610), (60, 610), (60, 650)],
    }.get(kind)
    return doc.create_object(kind, rect=None if nodes else (10, 610, 90, 650), nodes=nodes, colour=0x2080C0)


def _an(po):
    return PR._an(po)


@pytest.mark.parametrize(
    "kind", ["line", "polygon", "polyline", "pipe", "rectangle_outline", "group", "bitmap", "symbol"]
)
def test_new_object_takes_visible_and_movement(mixer, kind):
    doc = mixer
    i = _new(doc, kind)
    assert i is not None
    objs = doc.parsed.page.objects
    members = doc._subtree_span(i)[1:]
    flags0 = [objs[j].subtype_size for j in [i] + members]
    assert doc.ensure_property(i, "VISIBLE", "Tag1 = 1")
    assert doc.ensure_property(i, "MVX", "Tag2")
    po = doc.parsed.page.objects[i]
    vb = dict(W.value_bag(po))
    assert vb["VISIBLE"] == "Tag1 = 1" and vb["MVX"] == "Tag2"
    assert po.subtype_size & 0x18 == 0x18
    for j in members:  # members inherit the group's 0x10
        assert doc.parsed.page.objects[j].subtype_size & 0x10
    assert doc.object_properties(i)["MVX"] == "Tag2"

    # the register writes the row
    rows = PR.page_rows(doc.serialize(), "FxGenies")["pgdynobj"]
    row = [r for r in rows if r["AN"] == str(_an(po))]
    assert row and row[0]["VISIBLE"] == "Tag1 = 1" and row[0]["MVX"] == "Tag2"
    if kind == "group":
        assert {r["AN"] for r in rows if r["GROUP"] == str(_an(po))} == {
            str(_an(doc.parsed.page.objects[j])) for j in members
        }

    # save -> reopen: stable
    doc.save()
    data = open(doc.path, "rb").read()
    pc = parse_ctg_bytes(data)
    po2 = pc.page.objects[i]
    assert dict(W.value_bag(po2)) == vb and po2.subtype_size == po.subtype_size
    assert W.serialize_ctg(pc.container, pc.page, pc.attributes) == data

    # undo takes the keys AND the derived flags back
    doc.undo()
    doc.undo()
    objs = doc.parsed.page.objects
    assert [objs[j].subtype_size for j in [i] + members] == flags0
    assert "MVX" not in dict(W.value_bag(objs[i]))


def test_removing_the_last_dynamic_key_clears_the_flag(mixer):
    doc = mixer
    i = _new(doc, "line")
    assert not doc.parsed.page.objects[i].subtype_size & 0x10
    assert doc.ensure_property(i, "VISIBLE", "Tag1 = 1")
    assert doc.parsed.page.objects[i].subtype_size & 0x10
    assert doc.remove_property(i, "VISIBLE")
    assert not doc.parsed.page.objects[i].subtype_size & 0x10


def test_member_property_sets_its_own_flag(mixer):
    """A member of a static group gets 0x10 from its own key."""
    doc = mixer
    g = _new(doc, "group")
    m = doc._subtree_span(g)[1]
    assert not doc.parsed.page.objects[m].subtype_size & 0x10
    assert doc.ensure_property(m, "ROT", "Angle")
    assert doc.parsed.page.objects[m].subtype_size & 0x10
    assert not doc.parsed.page.objects[g].subtype_size & 0x10  # never upward


def test_genie_root_refuses_the_keys_ctdraw_drops(mixer):
    """A genie root refuses the keys CtDraw drops."""
    doc = mixer
    root = doc.fx_root
    before = W.object_payload(doc.parsed.page.objects[root])
    for key, value in (("COMMENT", "Straw feed"), ("PRIV", "1"), ("VISIBLE", "BIT_1")):
        assert not doc.ensure_property(root, key, value)
        assert not doc.set_property(root, key, value)
    assert W.object_payload(doc.parsed.page.objects[root]) == before
    params = dict(doc.genie_all_params(root))
    assert doc.set_object_name(root, "StrawFeed")
    po = doc.parsed.page.objects[root]
    r = GP.decode(po)
    assert r.entries[0][1].get("__AN_NAME__") == "StrawFeed"
    assert "__AN_NAME__" not in W.genie_bindings(po)
    assert dict(doc.genie_all_params(root)) == params
    rows = PR.page_rows(doc.serialize(), "FxGenies")["pgdynobj"]
    assert [r for r in rows if r["AN"] == str(_an(po))]
    # members inherit the now-dynamic genie root
    members = doc._subtree_span(root)[1:]
    assert members
    for j in members:
        assert doc.parsed.page.objects[j].subtype_size & 0x10


def test_genie_recompose_keeps_the_name_and_drops_what_ctdraw_drops(mixer):
    """A genie parameter edit re-composes the instance from its element the
    way CtDraw's page load does: the root keeps its object name; an Access
    comment stored there goes, as it does in CtDraw."""
    doc = mixer
    first = doc.fx_root
    assert doc.set_object_name(first, "StrawFeed")
    assert W.ensure_object_property(doc.parsed.page.objects[first], "COMMENT", "a comment")
    before = doc.parsed.page.objects[first]
    key = next(iter(doc.genie_all_params(first)))
    assert key == "Tag"
    assert doc.set_genie_binding(first, key, "FX_OTHER")
    root = next(i for i, _e in doc.genie_instances() if doc.genie_all_params(i).get(key) == "FX_OTHER")
    po = doc.parsed.page.objects[root]
    assert po is not before  # re-composed (not the bag fallback)
    r = GP.decode(po)
    assert r.entries[0][1].get("__AN_NAME__") == "StrawFeed"
    assert r.entries[0][2].get("__AN_NAME__") == "StrawFeed"
    assert r.entries[0][1].get("COMMENT") is None
    assert r.entries[0][2].get("COMMENT") is None
    assert po.subtype_size & 0x10


def test_root_key_kept_is_the_live_set():
    for k in ("__AN_NAME__", "NAME", "AI_ON", "AI_WIDTH", "!*PropSheet.Focus"):
        assert GP.root_key_kept(k)
    for k in ("COMMENT", "DESC", "PRIV", "AREA", "DEVICE", "DISABLED", "VISIBLE", "MVX"):
        assert not GP.root_key_kept(k)


# --------------------------------------------------------------------------- UI
def test_property_sheet_stores_animation_on_a_new_line(mixer, monkeypatch):
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    warned = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning", lambda *a, **k: warned.append(a) or QtWidgets.QMessageBox.Ok
    )
    doc = mixer
    i = _new(doc, "line")
    dlg = ObjectPropertiesDialog(doc, i)
    dlg._editors["VISIBLE"].setText("Tag1 = 1")
    dlg._editors["MVX"].setText("Tag2")
    assert dlg.apply() is True
    assert dlg.failures == [] and not warned
    assert dict(W.value_bag(doc.parsed.page.objects[i]))["VISIBLE"] == "Tag1 = 1"


def test_property_sheet_reports_what_it_could_not_store(mixer, monkeypatch):
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    warned = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning", lambda *a, **k: warned.append(a) or QtWidgets.QMessageBox.Ok
    )
    doc = mixer
    i = _new(doc, "line")
    monkeypatch.setattr(doc, "ensure_property", lambda *a, **k: False)
    dlg = ObjectPropertiesDialog(doc, i)
    accepted = []
    dlg.accepted.connect(lambda: accepted.append(1))
    dlg._editors["VISIBLE"].setText("Tag1 = 1")
    dlg._ok()
    assert dlg.failures == ["VISIBLE"]
    assert not accepted  # the sheet stays open
    # reported IN the sheet (its status line), not as a separate message box
    msg = dlg._status.text()
    assert "Could not store VISIBLE" in msg and "no property list" in msg


def test_property_panel_reports_a_failed_add(mixer, monkeypatch):
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.panels.properties import PropertyPanel

    doc = mixer
    i = _new(doc, "pipe")
    panel = PropertyPanel()
    panel.set_target(doc, [i])
    msgs = []
    panel.statusMessage.connect(msgs.append)
    combo = QtWidgets.QComboBox()
    combo.addItem("Add animation…")
    combo.addItem("Hidden when", ("VISIBLE", "0"))
    panel._on_add_animation(i, combo, 1)  # works on a new pipe now
    assert dict(W.value_bag(doc.parsed.page.objects[i]))["VISIBLE"] == "0"
    assert not msgs
    monkeypatch.setattr(doc, "ensure_property", lambda *a, **k: False)
    panel._on_add_animation(i, combo, 1)
    # the message names the setting ('Hidden when'), not its key
    assert msgs and msgs[-1].startswith("Could not store 'Hidden when' on this pipe")


def test_object_properties_shows_a_value_bag_the_scanner_cannot_frame(mixer):
    """A value bag whose size-word overshoot runs past the body end (members
    of linked symbols / templates on pages carry 26..37) is invisible
    to the heuristic scanner: the stored key must still show in the sheet."""
    doc = mixer
    i = _new(doc, "line")
    po = doc.parsed.page.objects[i]
    r = GP.decode(po)
    r.entries[0][1].ov = 400  # past the end of the body
    W._store(po, r)
    assert doc.ensure_property(i, "VISIBLE", "Tag1 = 1")
    po = doc.parsed.page.objects[i]
    assert dict(W.value_bag(po))["VISIBLE"] == "Tag1 = 1"
    assert "VISIBLE" not in po.raw_properties()  # the scanner misses it
    assert doc.object_properties(i)["VISIBLE"] == "Tag1 = 1"


def test_element_member_inherits_the_unwrapped_root(fx_install):
    """In an element document the root group is unwrapped (not in the parsed
    list): a member edit must still see it as the parent it inherits 0x10
    from, or removing the member's own key clears an inherited flag (fxsym.fx_group:
    an element whose root is a group)."""
    from ctgedit.editing import ElementDocument

    d = ElementDocument.open_element(fx_install.file("FxLib", "fxsym.ctl"), "fx_group")
    root = d.session.root
    assert root is not None and root.type_code == 0x08
    assert W.ensure_object_property(root, "VISIBLE", "Tag1 = 1")
    root.subtype_size |= 0x10  # a dynamic element root
    m = next(
        k
        for k, o in enumerate(d.parsed.page.objects)
        if o.type_code < 0x1000 and o.type_code not in CS.ALWAYS_DYNAMIC_TCS
    )
    assert d.ensure_property(m, "COMMENT", "note")
    assert d.parsed.page.objects[m].subtype_size & 0x10
    assert d.remove_property(m, "COMMENT")
    assert d.parsed.page.objects[m].subtype_size & 0x10  # still inherited


def test_property_sheet_cancel_after_apply_refreshes_the_window(fx_install, monkeypatch):
    """Apply (or an OK that stayed open over a failed field) has changed the
    page; closing the sheet with Cancel must still refresh the canvas."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.open_document(fx_install.page("FxGenies"))
        i = _new(w.doc, "line")
        w._refresh()
        w.object_properties(i)
        dlg = w._dialogs[("objprops", i)]
        refreshed = []
        monkeypatch.setattr(w, "_refresh", lambda *a, **k: refreshed.append(1))
        dlg._editors["VISIBLE"].setText("Tag1 = 1")
        assert dlg.apply() is True
        dlg.reject()
        assert refreshed
        assert dict(W.value_bag(w.doc.parsed.page.objects[i]))["VISIBLE"] == "Tag1 = 1"
        # a sheet closed as stale (inside a refresh / undo) does not refresh again
        w.object_properties(i)
        dlg = w._dialogs[("objprops", i)]
        dlg._editors["MVX"].setText("Tag2")
        assert dlg.apply() is True
        refreshed.clear()
        w._drop_stale_dialogs(all_objects=True)
        assert not refreshed and ("objprops", i) not in w._dialogs
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
