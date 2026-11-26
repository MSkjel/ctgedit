"""Genie write rules pinned to CtDraw's own saves."""

import os
import shutil

from ctgedit.editing import EditableDocument, ElementDocument
from ctgedit.format import genie_place as GP
from ctgedit.format.element import open_element

HERE = os.path.dirname(__file__)
NEST = os.path.join(HERE, "data", "nested_chain")


def _levels(po):
    e = GP.decode(po).entries
    return [[list(b.pairs) for b in lvl] for lvl in e] if e else None


def _text(doc, text, x, y):
    i = doc.create_object("text", rect=(x, y, x + 120, y + 20), text=text)
    doc.set_property(i, "TXT_OFF", text)
    return i


def test_a_nested_root_takes_values_only_and_declares_its_bindings_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("CTG_LIB_PATH", str(tmp_path))
    lib = str(tmp_path / "_rules.ctm")
    inner = ElementDocument.new_element(lib, "inner", "genie", canvas=(300, 200))
    _text(inner, "%P=ABC%", 10, 10)
    inner.save()
    outer = ElementDocument.new_element(lib, "outer", "genie", canvas=(300, 200))
    _text(outer, "%Z%", 10, 60)
    assert outer.place_library_element("_rules", "inner", "ctm", (40, 100), params={"P": "%Q=5%"}) is not None
    outer.save()
    objs = open_element(lib, "outer").page.objects
    k = next(i for i, po in enumerate(objs) if po.class_flag and (po.subtype_size & 0x200))
    root, member = _levels(objs[k]), _levels(objs[k + 1])
    assert len(root) == 3
    assert root[1][1] == [] and root[1][2] == [("P", "%Q=5%")]
    assert root[2][0] == [("Q", "5")]
    assert member[1][0] == [("P", "ABC")]
    assert member[1][1] == [("P", "5")] and member[1][2] == [("P", "%Q=5%")]
    assert member[2][0] == [("Q", "5")]
    assert list(GP.declared_params(GP.load_element(lib, "outer")).items()) == [("Q", "5"), ("Z", "")]


def test_a_container_declares_its_subtrees_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("CTG_LIB_PATH", str(tmp_path))
    lib = str(tmp_path / "_rules.ctm")
    el = ElementDocument.new_element(lib, "xdecl", "genie", canvas=(300, 200))
    _text(el, "%X%", 10, 10)
    _text(el, "%X=5%", 10, 40)
    el.save()
    objs = open_element(lib, "xdecl").page.objects
    assert _levels(objs[0])[1][0] == [("X", "5")]
    assert _levels(objs[1])[1][0] == [("X", "\n")]
    assert _levels(objs[2])[1][0] == [("X", "5")]
    assert list(GP.declared_params(GP.load_element(lib, "xdecl")).items()) == [("X", "5")]
    # the member with the default removed: X stays declared (the other text), without one
    el2 = ElementDocument.open_element(lib, "xdecl")
    el2.delete_objects([1])  # (the session leaves the root out)
    el2.save()
    objs = open_element(lib, "xdecl").page.objects
    assert _levels(objs[0])[1][0] == []
    assert list(GP.declared_params(GP.load_element(lib, "xdecl")).items()) == [("X", "")]


def test_the_page_special_substitutes_to_nothing(fx_page):
    """fx_goto's command sits on its button (tc 0x1001: CMD in the record's own bag; the
    shipped genie of the live check had it on a touch record, tc 0x1010)."""
    doc = EditableDocument.open(fx_page("FxShapes"))
    k = doc.place_library_element("fxparts", "fx_goto", "ctm", (100, 100))
    assert k is not None
    cmds = []
    for j in doc.subtree(k):
        po = doc.parsed.page.objects[j]
        if po.type_code in (0x1010, 0x1001):
            r = GP.decode(po)
            cmds += [v for key, v in r.entries[0][1].pairs if key.upper() == "CMD"]
            tpls = [v for key, v in r.entries[0][2].pairs if key.upper() == "CMD"]
            assert tpls == ['FxGoto("%Page%", 1)'], tpls  # the value comes from %Page%
    assert any(c.startswith('FxGoto("",') for c in cmds), cmds
    assert not any("FxShapes" in c for c in cmds), cmds  # not the page name


def test_ungroup_of_a_placed_genie_leaves_plain_objects(tmp_path):
    page = str(tmp_path / "!NEST_T8.ctg")
    shutil.copyfile(os.path.join(NEST, "!NEST_T8.ctg"), page)
    doc = EditableDocument.open(page)
    before = _levels(doc.parsed.page.objects[2])  # the nested n3 root: 3 levels
    assert len(before) == 3
    new = doc.ungroup_object(0)
    objs = doc.parsed.page.objects
    text = next(objs[j] for j in new if objs[j].type_code == 0x05)
    assert len(_levels(text)) == 1 and _levels(text)[0][2] == []
    inner = next(objs[j] for j in new if objs[j].class_flag and (objs[j].subtype_size & 0x200))
    lv = _levels(inner)
    assert len(lv) == 2  # its own level only, as a page holds it
    want = [(k, GP.substitute(v, GP.Bag([]), GP.Bag([]), k)) for k, v in before[1][2]]
    assert lv[1][2] == [] and lv[1][1] == [(k, v) for k, v in want if v]


def test_a_copy_from_an_element_window_pasted_on_a_page_loses_the_element_level(fx_page):
    el = ElementDocument.open_element(os.path.join(NEST, "_nest8.ctm"), "n4")
    objs = el.parsed.page.objects
    t = next(i for i, po in enumerate(objs) if po.type_code == 0x05)
    assert len(_levels(objs[t])) == 2
    payload = el.copy_payload([t])
    doc = EditableDocument.open(fx_page("FxShapes"))
    new = doc.paste_payload(payload, offset=0)
    assert len(new) == 1
    lv = _levels(doc.parsed.page.objects[new[0]])
    assert len(lv) == 1 and lv[0][2] == []


def test_substitution_matches_ctdraws_placement():
    """Texts drawn into a genie and the genie placed with values, saved by the live CtDraw
    (FUN_004d6960): the result stops at 254 characters, and a token's name is read past
    leading '='s, as its declaration is."""
    vals = GP.Bag([("P", "C" * 252), ("Q", "VAL"), ("S", "SVAL"), ("T", "TVAL")])
    decls = GP.Bag([("R", "dflt"), ("P", ""), ("Q", ""), ("S", ""), ("T", "")])
    saved = {
        "AB%P%xyz": "AB" + "C" * 252,
        "%=Q%": "VAL",
        "%=R=dflt%": "dflt",
        "%==S%": "SVAL",
        "%T%": "TVAL",
    }
    for template, want in saved.items():
        assert GP.substitute(template, vals, decls, "TXT_OFF") == want, template
    # the declarations CtDraw made from those templates
    declared = {"=Q": ("Q", None), "=R=dflt": ("R", "dflt"), "==S": ("S", None), "T": ("T", None)}
    for tok, want in declared.items():
        assert GP.token_name(tok) == want, tok
    assert GP.token_name("a==b") == ("a", "=b") and GP.token_name("a=") == ("a", None)
    assert GP.token_name("=") == ("", None)
    assert GP.substitute("%=%x", vals, decls) == "x"
