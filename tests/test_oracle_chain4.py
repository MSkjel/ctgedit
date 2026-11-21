"""What CtDraw writes along the save chain (oracle step 'chain4')."""

import json
import struct

import fxinstall
import pytest

pytestmark = pytest.mark.golden

#: tools/live/fixture_oracle: OUTER_PARAM, INNER_RECT, RESIZE_*, PLACEHOLDER_*, PARAMLESS_*
PARAM_TEXT, PARAM_AT = "%P%", (500, 500)
INNER_RECT = (300, 300, 400, 360)
OUTER, INNER = "fxouter@XGA", "normal@XGA"
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _path(unit, *parts):
    return fxinstall.oracle("chain4", unit, "/".join(parts))


def _json(unit, name):
    return fxinstall.oracle_json("chain4", unit, name)


def _recs(path, el):
    from ctgedit.format import genie_place as GP
    from ctgedit.format.element import open_element

    return [GP.decode(po) for po in open_element(path, el).tree]


def _page(unit, stage):
    from ctgedit.format import genie_place as GP
    from ctgedit.format.page import parse_ctg_bytes

    pb = parse_ctg_bytes(fxinstall.oracle_bytes("chain4", unit, "%s/page.ctg" % stage)).page
    return pb, [GP.decode(po) for po in pb.objects]


def _page_table(pb):
    from ctgedit.format import links

    if not pb.link_count:
        return []
    return [(g, n) for g, n in links.parse_link_table(pb.link_table_raw)[1]]


def _levels(r):
    return [[list(b.pairs) for b in lvl] for lvl in (r.entries or [])]


def _key(r):
    return (r.cf, r.tc, r.dw, r.handle, r.an, r.name, r.link, r.slots, r.anchor, r.rect1, r.rect2, _levels(r))


def _table(path, el):
    from ctgedit.format import links
    from ctgedit.format.element import open_element

    pb = open_element(path, el).page
    if not pb.link_count:
        return []
    return [n for _g, n in links.parse_link_table(pb.link_table_raw)[1]]


def _stamp(path, el):
    """The element's save stamp (its block header at 0x20) as a link entry quotes it."""
    from ctgedit.format.element import LibraryFile

    lf = LibraryFile.open(path)
    return "#%08x" % struct.unpack_from("<I", lf.block_data(lf.find(el)), 0x20)[0]


def _declared(recs):
    """The names the records' outermost levels declare (a record with two levels or more),
    in order: the genie dialog's rows."""
    out = []
    for r in recs:
        if r.entries and len(r.entries) >= 2:
            for k, _v in r.entries[-1][0].pairs:
                if k not in out:
                    out.append(k)
    return out


def _value(r, key, level=0, bag=1):
    return dict(r.entries[level][bag].pairs).get(key) if r.entries else None


def _end(recs, i):
    """The end of record ``i``'s subtree (the records are depth first, cf counts the
    direct children)."""
    k, left = i, [recs[i].cf]
    while left:
        if left[-1] == 0:
            left.pop()
            continue
        left[-1] -= 1
        k += 1
        left.append(recs[k].cf)
    return k + 1


def _span(recs, link):
    """(root, end) of the subtree whose root links ``link``."""
    hits = [i for i, r in enumerate(recs) if (r.link or "").lower() == link.lower()]
    assert len(hits) == 1, hits
    return hits[0], _end(recs, hits[0])


def _text_tail(unit, stage, index):
    from ctgedit.format import geometry as G
    from ctgedit.format.page import parse_ctg_bytes

    po = parse_ctg_bytes(fxinstall.oracle_bytes("chain4", unit, "%s/page.ctg" % stage)).page.objects[index]
    return G.parse_text_tail(po)


EMPTY = [[], [], []]


# ---------------------------------------------------------------- Q1: the element form
def test_the_first_own_parameter_puts_the_copy_in_the_element_form():
    """'%P%' drawn on the template CtDraw made: every record (its root, the inner copy and
    the nested genie's records) gains one level, the new outermost level empty; on each old
    level an empty value bag (b2) takes the substituted one's (b1) pairs. The template then
    declares P only (the nested genie's Description is a level below the outermost)."""
    made = _recs(_path("outer_param", "made", "library.ctt"), OUTER)
    path = _path("outer_param", "param_added", "library.ctt")
    got = _recs(path, OUTER)
    assert len(got) == len(made) + 1
    for k, (a, b) in enumerate(zip(made, got)):
        old, new = _levels(a), _levels(b)
        assert len(new) == len(old) + 1, k
        assert new[-1] == EMPTY, k
        for i, (lo, ln) in enumerate(zip(old, new)):
            assert ln[0] == lo[0] and ln[1] == lo[1], (k, i)
            assert ln[2] == (lo[2] or lo[1]), (k, i)
    text = got[-1]
    assert text.tc == 0x05 and text.rect1[:2] == PARAM_AT
    assert _levels(text) == [[[], [], [("TXT_OFF", PARAM_TEXT)]], [[("P", "\n")], [], []]]
    assert _declared(made) == ["Description"] and _declared(got) == ["P"]
    assert _table(path, OUTER) == _table(_path("outer_param", "made", "library.ctt"), OUTER)


def test_the_template_with_its_parameter_is_a_fixed_point_of_a_load_and_save():
    a = _recs(_path("outer_param", "param_added", "library.ctt"), OUTER)
    b = _recs(_path("outer_param", "plain", OUTER, "library.ctt"), OUTER)
    assert [_key(r) for r in a] == [_key(r) for r in b]


@pytest.mark.parametrize("stage", ["same", "fresh"])
def test_the_element_form_copy_is_composed_after_the_inner_template_changes(stage):
    """normal@XGA gains a rectangle: the outer's load + save inserts it as the copy's last
    member with a fresh handle, its an = its handle in normal@XGA and the element form's two
    levels (both empty); the copy root counts one more child; no other record changes; the
    import entry quotes the inner's new stamp."""
    plain = _recs(_path("outer_param", "plain", OUTER, "library.ctt"), OUTER)
    path = _path("outer_param", stage, OUTER, "library.ctt")
    got = _recs(path, OUTER)
    i, end = _span(plain, "fx_style.normal@XGA.<ctt")
    assert len(got) == len(plain) + 1
    new = got[end]
    assert new.tc == 0x0F and new.rect1 == INNER_RECT and new.cf == 0
    assert _levels(new) == [EMPTY, EMPTY]
    assert new.handle not in {r.handle for r in plain}
    changed = _path("outer_param", "inner_changed", "library.ctt")
    rect = [r for r in _recs(changed, INNER) if r.rect1 == INNER_RECT]
    assert len(rect) == 1 and new.an == str(rect[0].handle)
    assert got[i].cf == plain[i].cf + 1
    others = [_key(r) for k, r in enumerate(got) if k not in (i, end)]
    assert others == [_key(r) for k, r in enumerate(plain) if k != i]
    assert _table(path, OUTER)[0] == "fx_style.normal@XGA.<ctt%s" % _stamp(changed, INNER)
    assert _stamp(changed, INNER) != _stamp(_path("outer_param", "plain", OUTER, "library.ctt"), INNER)


@pytest.mark.parametrize("stage", ["same", "fresh"])
def test_a_new_page_from_it_keeps_the_unbound_token_as_its_text(stage):
    """A new page from it keeps the unbound token as its text."""
    pb, recs = _page("outer_param", "%s/new_page" % stage)
    root = recs[0]
    assert root.link == "fx_style.fxouter@XGA.<ctt" and _levels(root) == [EMPTY, EMPTY]
    rect = [r for r in recs if r.rect1 == INNER_RECT]
    assert len(rect) == 1
    assert len(rect[0].entries) == (2 if stage == "same" else 1)
    text = [r for r in recs if _value(r, "TXT_OFF", bag=2) == PARAM_TEXT]
    assert len(text) == 1
    assert _value(text[0], "TXT_OFF", bag=1) == PARAM_TEXT
    assert text[0].rect1 == (500, 500, 533, 516)
    assert [n.split("#")[0] for _g, n in _page_table(pb)] == ["fx_style.fxouter@XGA.<ctt", "\\\\Fx Menu"]


# ---------------------------------------------------------------- Q2: a mirrored text resized
def _calls(unit, stage):
    return [c for c in _json(unit, "actions.json")["calls"] if c.get("stage") == stage]


def test_the_matrix_put_sets_the_objects_matrix():
    calls = _calls("mirror_resize", "resized") + _calls("mirror_resize", "n0_resized")
    assert len(calls) == 5
    for c in calls:
        assert (
            c["getters after"]["AttributeTransformationMatrixGet"] == c["AttributeTransformationMatrixPut"]
        ), c["scaled"]


def test_a_scale_of_a_plain_text_is_baked_into_its_font():
    """'Plain me' (and 'Set me', its mirror replaced by the put scale): the font size
    doubles (11 -> 21), the anchor is mapped, the box measured again, the slots identity."""
    _pb, drawn = _page("mirror_resize", "mirrored")
    _pb, got = _page("mirror_resize", "resized")
    for k, anchor, box in ((1, (100, 226), (100, 200, 209, 232)), (2, (300, 126), (300, 100, 389, 132))):
        assert _text_tail("mirror_resize", "mirrored", k)["size"] == 11
        tail = _text_tail("mirror_resize", "resized", k)
        assert tail["size"] == 21 and tuple(tail["echo"]) == anchor
        assert got[k].anchor == anchor and got[k].rect1 == box
        assert [s for s in got[k].slots] == [(0, IDENTITY)] * 3
    assert drawn[2].slots[0][0] == 3  # ('Set me' was mirrored)
    assert got[3].rect1 == (100, 300, 300, 420) and got[3].slots == [(0, IDENTITY)] * 3


def test_a_mirrored_text_keeps_the_scale_in_its_matrix():
    """'Mirror me' with the scale composed onto its mirror: all three slots hold the matrix
    (mode 0x0F), the font size and the local echo stay, the anchor is the echo mapped through
    the matrix and the box the old local box mapped through it."""
    _pb, before = _page("mirror_resize", "mirrored")
    _pb, got = _page("mirror_resize", "resized")
    m = (-2.0, 0.0, 0.0, 2.0, 420.0, -100.0)
    assert before[0].slots == [(3, (-1.0, 0.0, 0.0, 1.0, 260.0, 0.0))] * 3
    assert [s[0] for s in got[0].slots] == [0x0F] * 3
    assert all(s[1] == m for s in got[0].slots)
    tail = _text_tail("mirror_resize", "resized", 0)
    assert tail["size"] == 11 and tuple(tail["echo"]) == (100, 113)
    assert got[0].anchor == (220, 126)  # (-2 * 100 + 420, 2 * 113 - 100)
    assert got[0].rect1 == (100, 100, 220, 132)  # (100, 100, 160, 116) mapped


def test_the_resized_page_is_a_fixed_point_of_a_load_and_save():
    _pb, a = _page("mirror_resize", "resized")
    _pb, b = _page("mirror_resize", "reopened")
    assert [_key(r) for r in a] == [_key(r) for r in b]


def test_a_mirrored_text_in_a_genie_keeps_the_scale_in_its_matrix():
    """n0's text, mirrored and scaled about its box's top left: the matrix in all three slots
    (mode 0x0F), the echo (10, 23) and the size kept, the anchor and the box mapped; the root's
    box follows. n1's copy takes the definition's matrix in its Mods and Total, its longer
    value growing to the left and scaled."""
    from ctgedit.format import geometry as G
    from ctgedit.format.element import open_element

    path = _path("mirror_resize", "n0_resized", "library.ctm")
    recs = _recs(path, "n0")
    text = recs[1]
    m = (-2.0, 0.0, -0.0, 2.0, 148.0, -10.0)
    assert [s for s in text.slots] == [(0x0F, m)] * 3
    tail = G.parse_text_tail(open_element(path, "n0").tree[1])
    assert tail["size"] == 11 and tuple(tail["echo"]) == (10, 23)
    assert text.anchor == (128, 36) and text.rect1 == (10, 10, 128, 42)
    assert recs[0].rect1 == (10, 10, 128, 42)
    n1 = _recs(_path("mirror_resize", "after_resize", "n1", "library.ctm"), "n1")
    copy = n1[3]
    assert copy.slots[0] == (0, IDENTITY)
    assert [s[0] for s in copy.slots[1:]] == [0x0F, 0x0F]
    assert copy.slots[1][1] == m and copy.slots[2][1] == (-2.0, 0.0, -0.0, 2.0, 148.0, 20.0)
    assert copy.anchor == (128, 66) and copy.rect1 == (-32, 40, 128, 72)


# ---------------------------------------------------------------- Q3: a turned, scaled placeholder
def test_the_turned_and_scaled_genie_keeps_its_matrix_in_its_local():
    _pb, turned = _page("placeholder_turned", "turned")
    _pb, scaled = _page("placeholder_turned", "scaled")
    assert turned[0].slots[0][0] == 0x15  # (a quarter turn with translation)
    assert scaled[0].slots[0] == (0x0F, (-0.0, 1.5, -1.5, 0.0, 408.0, 309.0))
    assert scaled[0].rect1 == (349, 262, 466, 352)


def test_its_library_missing_the_load_and_save_writes_one_placeholder():
    """With its library missing, load and save write one placeholder."""
    _pb, scaled = _page("placeholder_turned", "scaled")
    pb, recs = _page("placeholder_turned", "missing")
    assert len(recs) == 1
    ph = recs[0]
    assert (ph.tc, ph.dw, ph.handle, ph.an, ph.cf) == (0xFFFF, 0xA, 0, "-1", 0)
    assert ph.rect1 == ph.rect2 == scaled[0].rect1 and ph.anchor == (349, 351)
    assert ph.slots == [(0, IDENTITY)] * 3
    want = [
        ("_Library", "fxgenie.fx_pump.<ctm"),
        ("_Transform_A", "-0.000000"),
        ("_Transform_B", "1.500000"),
        ("_Transform_C", "-1.500000"),
        ("_Transform_D", "0.000000"),
        ("_Transform_H", "408.000000"),
        ("_Transform_K", "309.000000"),
        ("_Transform_Byte", "15"),
    ]
    assert _levels(ph)[0] == [[], want, want]
    assert _levels(ph)[1] == [[], [("Tag", "FX_PUMP1"), ("Label", "Fx")], []]
    assert _page_table(pb) == [(bytes(16), "fxgenie.fx_pump.<ctm")]


def test_its_library_back_the_load_and_save_places_the_genie_as_it_was():
    """The records and the import entry are the turned and scaled genie's again, byte for
    byte (the Local of mode 15 and the members' Totals included)."""
    from ctgedit.format import writer
    from ctgedit.format.page import parse_ctg_bytes

    def page(stage):
        return parse_ctg_bytes(
            fxinstall.oracle_bytes("chain4", "placeholder_turned", "%s/page.ctg" % stage)
        ).page

    a, b = page("scaled"), page("back")
    assert [writer.object_payload(po) for po in a.objects] == [writer.object_payload(po) for po in b.objects]
    assert _page_table(a) == _page_table(b)


# ---------------------------------------------------------------- Q4: a genie without parameters
def _copy_records(recs, link=None, dw=None):
    """The records of the copy whose root links ``link`` (or, nested, has dwFlags ``dw``)."""
    if link is not None:
        i, end = _span(recs, link)
    else:
        hits = [k for k, r in enumerate(recs) if r.dw == dw]
        assert len(hits) == 1, hits
        i, end = hits[0], _end(recs, hits[0])
    return recs[i:end]


def test_a_genie_without_parameters_pasted_into_a_genie_takes_the_hosts_level_count():
    """fx_plain pasted into n0: its copy's root and members carry two levels (n0's own
    count), the second empty and the first's value bags empty; n0 still declares Label only.
    n1's copy of n0 holds it as a copy with no link (dwFlags 0x208) whose records carry three
    levels, the two outer ones empty."""
    path = _path("param_gained", "n0_pasted", "library.ctm")
    n0 = _recs(path, "n0")
    copy = _copy_records(n0, link="fxgenie.fx_plain.<ctm")
    assert len(copy) == 3
    assert all(_levels(r) == [EMPTY, EMPTY] for r in copy)
    assert _declared(n0) == ["Label"]
    n1 = _recs(_path("param_gained", "pasted", "n1", "library.ctm"), "n1")
    inner = _copy_records(n1, dw=0x208)
    assert len(inner) == 3 and all(_levels(r) == [EMPTY] * 3 for r in inner)
    assert _declared(n1) == ["Name"]


def test_the_genie_gains_its_parameter():
    recs = _recs(_path("param_gained", "inner_gained", "library.ctm"), "fx_plain")
    assert all(len(r.entries) == 2 for r in recs)
    assert _levels(recs[-1]) == [[[], [], [("TXT_OFF", "%P%")]], [[("P", "\n")], [], []]]
    assert _declared(recs) == ["P"]


@pytest.mark.parametrize("stage", ["same", "fresh"])
def test_the_host_composes_the_copy_one_level_more_and_declares_nothing_new(stage):
    """The host composes the copy one level more and declares nothing new."""
    gained = _path("param_gained", "inner_gained", "library.ctm")
    path = _path("param_gained", stage, "n0", "library.ctm")
    n0 = _recs(path, "n0")
    copy = _copy_records(n0, link="fxgenie.fx_plain.<ctm")
    assert len(copy) == 4
    assert _levels(copy[0]) == [EMPTY, EMPTY, EMPTY]
    for r in copy[1:3]:
        assert _levels(r) == [EMPTY] * 3
    assert _levels(copy[3]) == [[[], [], [("TXT_OFF", "%P%")]], [[("P", "\n")], [], []], EMPTY]
    assert _declared(n0) == ["Label"]
    assert _table(path, "n0") == ["fxgenie.fx_plain.<ctm%s" % _stamp(gained, "fx_plain")]
    n1 = _recs(_path("param_gained", stage, "n1", "library.ctm"), "n1")
    inner = _copy_records(n1, dw=0x208)
    assert len(inner) == 4
    assert all(len(r.entries) == 4 for r in inner)
    assert _levels(inner[0])[1:] == [EMPTY] * 3 and _levels(inner[-1])[2:] == [EMPTY] * 2
    assert _levels(inner[-1])[:2] == [[[], [], [("TXT_OFF", "%P%")]], [[("P", "\n")], [], []]]
    assert _declared(n1) == ["Name"]


# ---------------------------------------------------------------- Q5: two smaller facts
@pytest.mark.parametrize("stage", ["placed", "saved", "reopened"])
def test_a_value_with_a_leading_blank_keeps_the_box_measured_with_it(stage):
    _pb, recs = _page("label_space", stage)
    root, text = recs
    assert _value(text, "TXT_OFF") == "L1" and _value(text, "Label", level=1) == " L1"
    assert text.rect1 == root.rect1 == (200, 200, 220, 216)


def test_a_page_linked_to_the_outer_template_shows_the_inner_change_on_its_load():
    """The outer not saved since the inner gained its rectangle: the page's load + save
    composes it into the page's copy of the inner template (one more child, the rectangle
    last, an = its handle) and keeps the outer's entry as it was."""
    pb0, made = _page("cut_link_outer", "page_made")
    pb, got = _page("cut_link_outer", "fresh/plain")
    assert made[0].link == "fx_style.fxouter@XGA.<ctt" and made[1].dw == 0xC
    assert got[1].cf == made[1].cf + 1 and len(got) == len(made) + 1
    assert got[-1].rect1 == INNER_RECT and got[-1].an == str(got[-1].handle)
    assert _page_table(pb) == _page_table(pb0)


@pytest.mark.parametrize("stage", ["same/cut", "fresh/cut"])
def test_cut_link_leaves_the_inner_copy_as_the_pages_template(stage):
    """Cut Link of the page's (outer) template: the outer root goes, the inner copy's root is
    the page's template now (linked to the inner template, linked + locked, its an and handle
    kept), with the inner's rectangle as its last member; the import table quotes the inner
    template's new stamp."""
    _pb0, made = _page("cut_link_outer", "page_made")
    pb, got = _page("cut_link_outer", stage)
    assert len(got) == len(made)  # (one root less, the rectangle more)
    root = got[0]
    assert root.link == "fx_style.normal@XGA.<ctt" and root.dw == 0xD
    assert (root.an, root.handle) == (made[1].an, made[1].handle)
    assert root.cf == made[1].cf + 1
    assert got[-1].rect1 == INNER_RECT and got[-1].tc == 0x0F
    assert not any((r.link or "").lower().startswith("fx_style.fxouter") for r in got)
    changed = _path("cut_link_outer", "inner_changed", "library.ctt")
    assert [n for _g, n in _page_table(pb)] == [
        "fx_style.normal@XGA.<ctt%s" % _stamp(changed, INNER),
        "\\\\Fx Menu",
    ]
    other = _page("cut_link_outer", "fresh/cut" if stage == "same/cut" else "same/cut")[1]
    assert [_key(r) for r in got] == [_key(r) for r in other]


def test_the_units_record_what_they_did():
    for unit in (
        "outer_param",
        "mirror_resize",
        "placeholder_turned",
        "param_gained",
        "label_space",
        "cut_link_outer",
    ):
        calls = _json(unit, "actions.json")["calls"]
        assert calls, unit
    assert json.dumps(_json("outer_param", "actions.json")).count("dialogs answered OK") == 0
