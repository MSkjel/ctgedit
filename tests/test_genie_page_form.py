"""A genie copied from a page into a genie element keeps its page form."""

import os

import pytest

from ctgedit.format import genie_place as GP, genie_norm as GN, writer
from ctgedit.format.element import open_element
from ctgedit.format.page import parse_ctg
from ctgedit.format.pagesummary import list_element_names

HERE = os.path.dirname(__file__)
PAGE = os.path.join(HERE, "data", "nested_chain", "!NEST_T8.ctg")
LIB = os.path.join(HERE, "data", "nested_chain", "_nest8.ctm")
pytestmark = pytest.mark.skipif(not os.path.exists(PAGE), reason="nested chain fixtures")


def _typed(po, pairs):
    """``po`` with its placed level's typed bindings replaced by ``pairs``."""
    r = GP.decode(po)
    r.entries[-1][1] = GP.Bag(list(pairs), r.entries[-1][1].ov)
    return GP.to_po(GP.encode(r))


def _element(token: bool = True):
    """A genie element root above the page's placed genie, with its root's bindings typed
    with an outer token (what Replace gives a page copy inside a genie element);
    ``token`` False keeps the page's own bindings (no parameter anywhere)."""
    page = parse_ctg(PAGE).page.objects
    inst = list(page)
    root = GP.decode(inst[0])
    typed = [(k, v) for k, v in root.entries[-1][1].pairs]
    assert typed, "the fixture's placed root carries typed bindings"
    name = typed[0][0]
    if token:
        typed[0] = (name, "%Line% " + typed[0][1])
    inst[0] = _typed(inst[0], typed)
    # the element root: a group that owns the instance
    group = GP.decode(inst[0])
    group.entries = None
    group.link = None
    group.cf = 1
    group.dw &= ~0x209  # no bags, not a nested genie, not linked
    recs = [GP.to_po(GP.encode(group))] + inst
    return recs, name


def _levels(po):
    e = GP.decode(po).entries
    return None if e is None else e


def test_page_form_is_detected_and_converted_in_a_genie_element():
    recs, name = _element()
    before = _levels(recs[1])
    assert len(before) == 2 and before[1][1].pairs and not before[1][2].pairs  # the page form
    out = GP.convert_page_form(recs, genie=True)
    root = _levels(out[1])
    assert len(root) == 3
    assert root[1][2].pairs == before[1][1].pairs  # the typed bindings are templates
    assert dict(root[1][1].pairs)[name] == " " + dict(before[1][1].pairs)[name][7:]  # '%Line%' unbound
    assert root[2][0].pairs == [] and root[2][1].pairs == [] and root[2][2].pairs == []
    # a member that binds the tokenised parameter declares the outer token on its element level
    declared = GP.declared_params(GP.Element(LIB, "_nest8", "n4", "ctm", (0, 0), out, (0, 0, 0, 0)))
    assert list(declared) == ["Line"]
    # every record of the instance gained exactly one level
    for a, b in zip(recs[1:], out[1:]):
        la, lb = _levels(a), _levels(b)
        if la is None:
            assert lb is None
        else:
            assert len(lb) == len(la) + 1, (len(la), len(lb))
    # the conversion is final: a second pass changes nothing
    again = GP.convert_page_form(out, genie=True)
    assert [writer.object_payload(o) for o in again] == [writer.object_payload(o) for o in out]
    # the normal form (what a save writes) keeps the element form and the declaration
    normed = GN.normalise_records(list(out), ext="ctm", convert=True)
    assert len(_levels(normed[1])) == 3
    assert list(GP.declared_params(GP.Element(LIB, "_nest8", "n4", "ctm", (0, 0), normed, (0, 0, 0, 0)))) == [
        "Line"
    ]


def test_a_non_genie_element_keeps_the_page_form():
    recs, _name = _element(token=False)
    # nothing names a parameter: not a genie element, the page form stays (servermon's
    # list_cluster rows through CtDraw's save)
    out = GP.convert_page_form(recs)
    assert [writer.object_payload(o) for o in out] == [writer.object_payload(o) for o in recs]
    out = GP.convert_page_form(recs, genie=False)
    assert [writer.object_payload(o) for o in out] == [writer.object_payload(o) for o in recs]
    # a token typed into a nested instance's binding alone makes it a genie (CtDraw's
    # dialog listed the element's parameters that way)
    recs, _name = _element()
    out = GP.convert_page_form(recs)
    assert [writer.object_payload(o) for o in out] != [writer.object_payload(o) for o in recs]


def test_the_element_form_passes_unchanged():
    for name in list_element_names(LIB):
        objs = open_element(LIB, name).page.objects
        out = GP.convert_page_form(objs, genie=True)
        assert [writer.object_payload(o) for o in out] == [writer.object_payload(o) for o in objs], name
