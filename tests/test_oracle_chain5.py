"""What CtDraw writes along the save chain (oracle step 'chain5')."""

import struct

import fxinstall
import pytest

pytestmark = pytest.mark.golden

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
EMPTY = [[], [], []]
INNER_RECT = (300, 300, 400, 360)
OUTER, INNER = "fxouter@XGA", "normal@XGA"


def _path(unit, *parts):
    return fxinstall.oracle("chain5", unit, "/".join(parts))


def _json(unit, name):
    return fxinstall.oracle_json("chain5", unit, name)


def _calls(unit, stage=None):
    calls = _json(unit, "actions.json")["calls"]
    return [c for c in calls if stage is None or c.get("stage") == stage]


def _recs(path, el):
    from ctgedit.format import genie_place as GP
    from ctgedit.format.element import open_element

    return [GP.decode(po) for po in open_element(path, el).tree]


def _page(unit, *stage):
    from ctgedit.format import genie_place as GP
    from ctgedit.format.page import parse_ctg_bytes

    pb = parse_ctg_bytes(fxinstall.oracle_bytes("chain5", unit, "/".join(stage + ("page.ctg",)))).page
    return pb, [GP.decode(po) for po in pb.objects]


def _entries(raw, count):
    from ctgedit.format import links

    if not count:
        return []
    return list(links.parse_link_table(raw)[1])


def _page_table(pb):
    return _entries(pb.link_table_raw, pb.link_count)


def _table(path, el):
    from ctgedit.format.element import open_element

    pb = open_element(path, el).page
    return _entries(pb.link_table_raw, pb.link_count)


def _names(entries):
    return [n for _g, n in entries]


def _bare(names):
    from ctgedit.format import links

    return [links.strip_stamp(n) for n in names]


def _stamp(path, el):
    """The element's save stamp (its block header at 0x20) as a link entry quotes it."""
    from ctgedit.format.element import LibraryFile

    lf = LibraryFile.open(path)
    return "#%08x" % struct.unpack_from("<I", lf.block_data(lf.find(el)), 0x20)[0]


def _levels(r):
    return [[list(b.pairs) for b in lvl] for lvl in (r.entries or [])]


def _key(r):
    return (r.cf, r.tc, r.dw, r.handle, r.an, r.name, r.link, r.slots, r.anchor, r.rect1, r.rect2, _levels(r))


def _declared(recs):
    out = []
    for r in recs:
        if r.entries and len(r.entries) >= 2:
            for k, _v in r.entries[-1][0].pairs:
                if k not in out:
                    out.append(k)
    return out


def _text_tail(unit, stage, index):
    from ctgedit.format import geometry as G
    from ctgedit.format.page import parse_ctg_bytes

    po = parse_ctg_bytes(fxinstall.oracle_bytes("chain5", unit, "%s/page.ctg" % stage)).page.objects[index]
    return G.parse_text_tail(po)


# ---------------------------------------------------------------- Q6: a host saved over a stale one
@pytest.mark.parametrize("where", ["same", "fresh"])
def test_a_host_saved_while_the_genie_it_nests_is_stale_gets_the_gained_record_unbound(where):
    """n0 gained '%Label% X' and was saved, n1 was not: n2's load + save composes its copy of
    n1's copy of n0 with the gained text (n1's copy root one child more) but unbound: its
    value trimmed 'X', the Label binding level empty, three levels where its twin has four.
    n2 keeps quoting n1's old stamp (the fixture's)."""
    path = _path("stale_host", where, "stale", "n2", "library.ctm")
    recs = _recs(path, "n2")
    assert len(recs) == 7
    copy, twin, new = recs[4], recs[5], recs[6]
    assert copy.dw == 0x208 and copy.cf == 2
    assert new.tc == 0x05 and (new.handle, new.an) == (6, "4")
    assert _levels(new) == [
        [[], [("TXT_OFF", "X")], [("TXT_OFF", "%Label% X")]],
        [[("Label", "\n")], [], []],
        EMPTY,
    ]
    assert new.rect1 == (10, 105, 22, 121)
    assert len(twin.entries) == 4
    assert _levels(twin)[1] == [[("Label", "\n")], [("Label", "%Name% L1")], [("Label", "%Name% L1")]]
    assert _names(_table(path, "n2")) == ["fxnest.n1.<ctm#ed02da80"]
    other = _recs(
        _path("stale_host", "fresh" if where == "same" else "same", "stale", "n2", "library.ctm"), "n2"
    )
    assert [_key(r) for r in recs] == [_key(r) for r in other]


@pytest.mark.parametrize("where", ["same", "fresh"])
def test_once_the_stale_genie_is_saved_the_host_is_the_in_order_form(where):
    """Then n1 and n2 saved: n2 equals the in-order chain's n2 (chain/gained_member), the
    gained record bound (four levels, '%Name% L1 X', 92 wide) and n1's new stamp quoted."""
    path = _path("stale_host", where, "after", "n2", "library.ctm")
    recs = _recs(path, "n2")
    want = _recs(fxinstall.oracle("chain", "gained_member", "same/n2/library.ctm"), "n2")
    assert [_key(r) for r in recs] == [_key(r) for r in want]
    new = recs[6]
    assert len(new.entries) == 4 and new.rect1 == (10, 105, 102, 121)
    assert _levels(new)[0][1] == [("TXT_OFF", "%Name% L1 X")]
    n1 = _path("stale_host", where, "after", "n1", "library.ctm")
    assert _names(_table(path, "n2")) == ["fxnest.n1.<ctm%s" % _stamp(n1, "n1")]


# ---------------------------------------------------------------- Q7: a parameter default that changes
@pytest.mark.parametrize("q", ["5", "7"])
def test_a_default_in_a_text_is_declared_on_the_text_and_the_root(q):
    recs = _recs(_path("param_default", "q%s" % q, "n0", "library.ctm"), "n0")
    root, text = recs
    assert _levels(text)[0] == [[], [("TXT_OFF", " %s" % q)], [("TXT_OFF", "%%Label%% %%Q=%s%%" % q)]]
    assert _levels(text)[1][0] == [("Label", "\n"), ("Q", q)]
    assert _levels(root)[1][0] == [("Q", q)]
    assert text.rect1 == (10, 10, 124, 26)


def test_the_first_default_becomes_a_binding_of_the_copy():
    """n1's load + save after n0's text took '%Q=5%': the copy root's placed level binds
    Q = 5 in its values and its templates, the copy's text shows '%Name% L1 5'."""
    recs = _recs(_path("param_default", "q5", "n1", "library.ctm"), "n1")
    root, text = recs[2], recs[3]
    assert _levels(root)[1] == [
        [("Q", "5")],
        [("Label", " L1"), ("Q", "5")],
        [("Label", "%Name% L1"), ("Q", "5")],
    ]
    assert _levels(text)[0][1:] == [[("TXT_OFF", "%Name% L1 5")], [("TXT_OFF", "%Label% %Q=5%")]]
    assert _declared(recs) == ["Name"]


@pytest.mark.parametrize("stage", ["q7", "q7_fresh"])
def test_a_changed_default_leaves_the_copys_binding_and_value(stage):
    """n0's default becomes 7: n1's load + save (the same Graphics Builder or a fresh one)
    takes the new template and declaration (Q = 7) but keeps the binding Q = 5 and the
    value '%Name% L1 5'."""
    recs = _recs(_path("param_default", stage, "n1", "library.ctm"), "n1")
    root, text = recs[2], recs[3]
    assert _levels(root)[1] == [
        [("Q", "7")],
        [("Label", " L1"), ("Q", "5")],
        [("Label", "%Name% L1"), ("Q", "5")],
    ]
    assert _levels(text)[0][1:] == [[("TXT_OFF", "%Name% L1 5")], [("TXT_OFF", "%Label% %Q=7%")]]
    assert _levels(text)[1] == [
        [("Label", "\n"), ("Q", "7")],
        [("Label", " L1"), ("Q", "5")],
        [("Label", "%Name% L1"), ("Q", "5")],
    ]
    assert _declared(recs) == ["Name"]


@pytest.mark.parametrize("stage,q", [("q5", "5"), ("q7", "7"), ("q7_fresh", "7")])
def test_a_placement_of_the_host_shows_the_bound_default(stage, q):
    _pb, recs = _page("param_default", stage, "page")
    text = recs[3]
    assert _levels(text)[0][1:] == [[("TXT_OFF", "P1 L1 5")], [("TXT_OFF", "%%Label%% %%Q=%s%%" % q)]]
    assert _levels(text)[1][0] == [("Label", "\n"), ("Q", q)]
    assert _levels(text)[1][1] == [("Label", "P1 L1"), ("Q", "5")]


# ---------------------------------------------------------------- Q9: labels
def test_an_element_lists_its_template_label_and_its_substituted_one():
    path = _path("label_edit", "genie_edited", "library.ctm")
    assert _names(_table(path, "fx_value")) == ["\\\\%Description% EDITED", "\\\\ EDITED"]
    assert all(g == bytes(16) for g, _n in _table(path, "fx_value"))
    text = _recs(path, "fx_value")[1]
    assert _levels(text)[0] == [[], [("TXT_OFF", "@( EDITED)")], [("TXT_OFF", "@(%Description% EDITED)")]]


@pytest.mark.parametrize("stage", ["same", "fresh"])
def test_a_pages_load_and_save_lists_the_labels_its_records_hold_now(stage):
    """The placed page listed '\\Fx Start'; after fx_value's text changed, the page's load +
    save lists '\\Fx Start EDITED' instead (the old label gone) and quotes the new stamp."""
    pb0, _r = _page("label_edit", "placed")
    assert _bare(_names(_page_table(pb0))) == ["fxgenie.fx_value.<ctm", "\\\\Fx Start"]
    pb, recs = _page("label_edit", stage)
    edited = _path("label_edit", "genie_edited", "library.ctm")
    assert _names(_page_table(pb)) == [
        "fxgenie.fx_value.<ctm%s" % _stamp(edited, "fx_value"),
        "\\\\Fx Start EDITED",
    ]
    assert _levels(recs[1])[0][1] == [("TXT_OFF", "@(Fx Start EDITED)")]


STATE_LABELS = [
    "\\\\Running",
    "\\\\Stopped",
    "\\\\Fault",
    "\\\\Manual",
    "\\\\Auto",
    "\\\\Hand",
    "\\\\Off Mode",
    "\\\\Fx Off",
    "\\\\Fx On",
]


def test_an_element_lists_the_labels_of_states_arrays_and_on_texts_in_key_order():
    """A multi-state, an array and an On / Off text: the element's table lists each '@()'
    label once, record by record in the bag's key order (TXT_OFF, which equals STATE_0 /
    ARRAY_0, then the other rows; TXT_OFF then TXT_ON), all with a zero GUID."""
    path = _path("label_states", "saved", "library.ctm")
    recs = _recs(path, "fx_plain")
    assert [p[0] for p in _levels(recs[3])[0][1]] == [
        "TXT_OFF",
        "TXTMODE",
        "TXT_A",
        "TXT_B",
        "STATE_0",
        "STATE_1",
        "STATE_2",
        "STATE_3",
    ]
    assert dict(_levels(recs[3])[0][1])["STATE_0"] == "@(Running)"
    assert [p[0] for p in _levels(recs[4])[0][1]] == [
        "TXT_OFF",
        "TXTMODE",
        "TXT_A",
        "ARRAY_0",
        "ARRAY_1",
        "ARRAY_2",
    ]
    assert [p[0] for p in _levels(recs[5])[0][1]] == ["TXT_OFF", "TXTMODE", "TXT_A", "TXT_ON"]
    assert _names(_table(path, "fx_plain")) == STATE_LABELS
    assert all(g == bytes(16) for g, _n in _table(path, "fx_plain"))
    again = _path("label_states", "reopened", "fx_plain", "library.ctm")
    assert _names(_table(again, "fx_plain")) == STATE_LABELS


def test_a_page_placing_it_lists_the_same_labels_after_the_genie():
    pb, _recs_ = _page("label_states", "page")
    assert _bare(_names(_page_table(pb))) == ["fxgenie.fx_plain.<ctm"] + STATE_LABELS


# ---------------------------------------------------------------- Q10: an element holding a placeholder
MARKER_KEYS = [
    ("_Library", "fxnest.n1.<ctm"),
    ("_Transform_A", "1.000000"),
    ("_Transform_B", "0.000000"),
    ("_Transform_C", "0.000000"),
    ("_Transform_D", "1.000000"),
    ("_Transform_H", "90.000000"),
    ("_Transform_K", "23.000000"),
    ("_Transform_Byte", "1"),
]


def test_an_element_saved_while_a_nested_library_is_missing_holds_a_placeholder():
    """fx_row's save with fxnest.ctm missing: its copy of n1 becomes one DummyMarker (the
    copy's box, its Local as _Transform_*, the copy root's binding level after) and the
    entry the bare name with a zero GUID; the root gets the lowest free handle."""
    path = _path("element_placeholder", "element_missing", "library.ctm")
    recs = _recs(path, "fx_row")
    assert len(recs) == 4
    ph = recs[2]
    assert (ph.tc, ph.dw, ph.handle, ph.an) == (0xFFFF, 0xA, 1, "-1")
    assert ph.rect1 == ph.rect2 == (50, 0, 180, 46) and ph.anchor == (50, 45)
    assert _levels(ph)[0] == [[], MARKER_KEYS, MARKER_KEYS]
    assert _levels(ph)[1] == [[], [("Name", "Row ")], [("Name", "Row %RowNum%")]]
    assert recs[0].handle == 2
    assert _table(path, "fx_row") == [(bytes(16), "fxnest.n1.<ctm")]


def test_a_page_placing_the_element_carries_the_bare_entry():
    pb, recs = _page("element_placeholder", "page_placed")
    assert [(g == bytes(16), n) for g, n in _page_table(pb)][1:] == [(True, "fxnest.n1.<ctm")]
    ph = recs[2]
    assert (ph.tc, ph.dw, ph.an) == (0xFFFF, 0x8, "1") and ph.rect1 == (150, 100, 280, 146)
    pb, recs = _page("element_placeholder", "page_missing")
    assert recs[2].rect1 == recs[2].rect2 == (100, 100, 100, 100)
    assert recs[2].anchor == (100, 100) and recs[0].rect1 == (100, 100, 160, 160)
    assert _bare(_names(_page_table(pb))) == ["fxparts.fx_row.<ctm", "fxnest.n1.<ctm"]


@pytest.mark.parametrize("stage", ["page_back", "page_placed_back"])
def test_with_the_library_back_a_page_gets_the_nested_genie_as_an_unlinked_copy(stage):
    """The page's load + save with fxnest back (and a new placement of the element that still
    holds the placeholder): the marker becomes n1's copy inside the fx_row instance, without
    a link of its own (dwFlags 0x208), and the bare entry goes."""
    pb, recs = _page("element_placeholder", stage)
    assert len(recs) == 7
    copy = recs[2]
    assert copy.tc == 0x08 and copy.dw == 0x208 and copy.link is None and copy.cf == 2
    assert _levels(copy)[1] == [[], [("Name", "Row ")], [("Name", "Row %RowNum%")]]
    assert recs[4].dw == 0x208
    assert not any(r.tc == 0xFFFF for r in recs)
    assert _bare(_names(_page_table(pb))) == ["fxparts.fx_row.<ctm"]
    other = _page("element_placeholder", "page_placed_back" if stage == "page_back" else "page_back")[1]
    assert [_key(r) for r in recs] == [_key(r) for r in other]


@pytest.mark.parametrize("stage", ["element_back", "element_nobare"])
def test_the_elements_load_and_save_places_the_genie_again_with_or_without_the_bare_entry(stage):
    """fx_row's load + save with fxnest back places n1 again, as the linked copy it held
    before (records as the fixture's but two handles), and the entry gets its GUID and
    stamp back; the same when the bare entry was removed from its table first."""
    from ctgedit.format import links

    path = _path("element_placeholder", stage, "fx_row", "library.ctm")
    recs = _recs(path, "fx_row")
    held = _recs(fxinstall.oracle("chain5", "element_placeholder", "element_missing/library.ctm"), "fx_row")
    assert len(held) == 4
    assert len(recs) == 7 and recs[2].dw == 0x209 and recs[2].link == "fxnest.n1.<ctm"
    assert [r.handle for r in recs] == [2, 0, 3, 4, 1, 6, 5]
    assert not any(r.tc == 0xFFFF for r in recs)
    ((g, n),) = _table(path, "fx_row")
    assert g != bytes(16) and links.strip_stamp(n) == "fxnest.n1.<ctm" and n.endswith("#ed02da80")
    nobare = _path("element_placeholder", "nobare_input", "library.ctm")
    assert _table(nobare, "fx_row") == []
    other = _recs(
        _path(
            "element_placeholder",
            "element_nobare" if stage == "element_back" else "element_back",
            "fx_row",
            "library.ctm",
        ),
        "fx_row",
    )
    assert [_key(r) for r in recs] == [_key(r) for r in other]


# ---------------------------------------------------------------- Q11: the order of the new handles
@pytest.mark.parametrize("stage", ["same", "fresh"])
def test_the_templates_new_members_get_their_handles_before_the_genies(stage):
    """normal@XGA (its nested fx_value copy) and the page's own fx_value both gained two
    rectangles: the page's load + save gives the template's the lowest free handles (14, 15,
    an = their handles in normal@XGA) and the genie's the next (16, 17)."""
    _pb0, made = _page("order", "page_made")
    assert len(made) == 14 and max(r.handle for r in made) == 13
    _pb, recs = _page("order", stage)
    assert len(recs) == 18
    got = {r.rect1: (r.handle, r.an) for r in recs if r.tc == 0x0F}
    assert got[(824, 738, 874, 758)] == (14, "11")
    assert got[(894, 738, 944, 758)] == (15, "12")
    assert got[(300, 340, 350, 360)] == (16, "2")
    assert got[(370, 340, 420, 360)] == (17, "3")
    other = _page("order", "fresh" if stage == "same" else "same")[1]
    assert [_key(r) for r in recs] == [_key(r) for r in other]


# ---------------------------------------------------------------- Q13: a binding the inner template lacks
def test_a_touch_command_on_a_copys_member_is_a_child_record():
    made = _recs(_path("extra_binding", "made", "library.ctt"), OUTER)
    recs = _recs(_path("extra_binding", "bound", "library.ctt"), OUTER)
    assert len(recs) == len(made) + 1
    rect, touch = recs[7], recs[8]
    assert rect.rect1 == (964, 40, 1004, 60) and (rect.cf, rect.dw) == (1, 0x18)
    assert made[7].cf == 0 and made[7].dw == 0x8
    assert touch.tc == 0x1010 and touch.handle == rect.handle
    assert _levels(touch)[0][1] == [("ACTION", "KEY_LBUTTON"), ("DOWN_CMD", "Beep()")]


@pytest.mark.parametrize("stage", ["same", "fresh"])
def test_the_copys_composition_drops_the_binding_the_inner_template_lacks(stage):
    """normal@XGA gains a rectangle: the outer's load + save composes its copy again and the
    touch command on the copy's member is gone (the member as normal@XGA's), the rectangle
    the copy's last member."""
    recs = _recs(_path("extra_binding", stage, OUTER, "library.ctt"), OUTER)
    assert not any(r.tc == 0x1010 for r in recs)
    assert (recs[7].cf, recs[7].dw) == (0, 0x8) and recs[7].rect1 == (964, 40, 1004, 60)
    assert recs[1].cf == 9 and recs[-1].rect1 == INNER_RECT and recs[-1].an == "10"
    other = _recs(_path("extra_binding", "fresh" if stage == "same" else "same", OUTER, "library.ctt"), OUTER)
    assert [_key(r) for r in recs] == [_key(r) for r in other]


# ---------------------------------------------------------------- Q14: an element-form copy's marker box
def test_the_load_and_save_derives_an_element_form_copys_marker_box_again():
    """The copy (in the element form: the outer has its parameter P) holds its free
    DummyMarker with normal@XGA's stored box: the outer's load + save gives it the empty rect
    at its origin and changes nothing else."""
    patched = _recs(_path("marker_box", "patched", "library.ctt"), OUTER)
    recs = _recs(_path("marker_box", "plain", OUTER, "library.ctt"), OUTER)
    assert _declared(recs) == ["P"] and len(recs[1].entries) == 2
    k = next(i for i, r in enumerate(recs) if r.tc == 0xFFFF)
    assert (patched[k].anchor, patched[k].rect1) == ((700, 118), (700, 100, 803, 119))
    assert (recs[k].anchor, recs[k].rect1, recs[k].rect2) == ((0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
    assert [_key(r) for i, r in enumerate(recs) if i != k] == [
        _key(r) for i, r in enumerate(patched) if i != k
    ]


# ---------------------------------------------------------------- Q15: the Save Template warning
WARNING = (
    "Please use Update Pages under Tools menu\nTo resave any page(s) associated with this template. [OK]"
)


def test_the_save_template_warning_only_points_to_update_pages():
    """File > Save of normal@XGA: an OK-only message box; the template is saved, no page and
    not the outer template."""
    (call,) = _calls("template_warning", "menu_save")
    assert call["watch"]["message boxes"] == [WARNING]
    assert call["after"] == {
        "inner saved": True,
        "outer saved": False,
        "pages changed": {"FxOracleOuterLinked": False, "FxTplLinked": False},
    }


def test_update_pages_composes_and_saves_the_outer_template_and_updates_its_page():
    """Tools > Update Pages (ProjectUpdatePages on FxMain): the outer template is composed
    (the rectangle its copy's last member, an '10') and saved with normal@XGA's new stamp,
    and the page linked to it gets the rectangle (its an the page handle) and the outer's new
    stamp."""
    (call,) = _calls("template_warning", "update_pages")
    assert call["after"]["outer saved"] and all(call["after"]["pages changed"].values())
    lib = _path("template_warning", "update_pages", "library.ctt")
    recs = _recs(lib, OUTER)
    assert len(recs) == 13 and recs[1].cf == 9
    assert recs[-1].rect1 == INNER_RECT and recs[-1].an == "10"
    assert _names(_table(lib, OUTER))[0] == "fx_style.normal@XGA.<ctt%s" % _stamp(
        _path("template_warning", "menu_save", "library.ctt"), INNER
    )
    pb, page = _page("template_warning", "update_pages", "FxOracleOuterLinked")
    assert len(page) == 13 and page[1].cf == 9
    assert page[-1].rect1 == INNER_RECT and page[-1].an == str(page[-1].handle) == "12"
    assert _names(_page_table(pb))[0] == "fx_style.fxouter@XGA.<ctt%s" % _stamp(lib, OUTER)
    assert "FxStyle/fx_style.ctt" in _json("template_warning", "actions.json")["files changed"]


# ---------------------------------------------------------------- Q16: scaled groups
def test_a_text_mirrored_before_its_first_save_keeps_its_top_left_as_its_echo():
    _pb, recs = _page("scaled_groups", "grouped")
    assert recs[1].slots == [(3, (-1.0, 0.0, 0.0, 1.0, 262.0, 13.0))] * 3
    assert tuple(_text_tail("scaled_groups", "grouped", 1)["echo"]) == (100, 100)
    assert recs[1].anchor == (162, 113) and recs[1].rect1 == (100, 100, 162, 116)


def test_a_scaled_group_keeps_a_mirrored_members_scale_in_its_matrix():
    """The group of the mirrored text and a rectangle, scaled twice about its top left: the
    text's slots all hold the composed matrix (mode 0x0F), its font and echo stay, its anchor
    and box are the local ones mapped; the rectangle is scaled into its rects."""
    _pb, recs = _page("scaled_groups", "scaled")
    assert recs[0].rect1 == (100, 100, 300, 260)
    text = recs[1]
    assert text.slots == [(0x0F, (-2.0, 0.0, 0.0, 2.0, 424.0, -74.0))] * 3
    tail = _text_tail("scaled_groups", "scaled", 1)
    assert tail["size"] == 11 and tuple(tail["echo"]) == (100, 100)
    assert text.anchor == (224, 126) and text.rect1 == (100, 100, 224, 132)
    assert recs[2].rect1 == (100, 180, 300, 260) and recs[2].slots == [(0, IDENTITY)] * 3


def test_a_scaled_group_bakes_a_plain_members_scale_into_its_font():
    _pb, recs = _page("scaled_groups", "scaled")
    assert recs[3].rect1 == (400, 100, 600, 260)
    text = recs[4]
    assert _text_tail("scaled_groups", "scaled", 4)["size"] == 21
    assert text.anchor == (400, 126) and text.rect1 == (400, 100, 511, 132)
    assert text.slots == [(0, IDENTITY)] * 3
    assert recs[5].rect1 == (400, 180, 600, 260)


def test_a_font_size_put_grows_a_plain_text_right_and_a_mirrored_one_left():
    """AttributeTextFontSize 22 (from 11): both keep their anchor (the baseline point) and
    their slots; the plain text grows to the right, the mirrored one to the left."""
    _pb, before = _page("scaled_groups", "grouped")
    _pb, recs = _page("scaled_groups", "scaled")
    for k, box in ((6, (100, 386, 228, 419)), (7, (326, 386, 470, 419))):
        assert _text_tail("scaled_groups", "scaled", k)["size"] == 22
        assert recs[k].anchor == before[k].anchor and recs[k].slots == before[k].slots
        assert recs[k].rect1 == box
    assert recs[7].slots == [(3, (-1.0, 0.0, 0.0, 1.0, 870.0, 13.0))] * 3


def test_the_scaled_page_is_kept_by_a_load_and_save_but_one_text_width():
    _pb, a = _page("scaled_groups", "scaled")
    _pb, b = _page("scaled_groups", "reopened")
    diff = [k for k, (x, y) in enumerate(zip(a, b)) if _key(x) != _key(y)]
    assert diff == [4] and (a[4].rect2, b[4].rect2) == ((400, 100, 512, 132), (400, 100, 511, 132))


# ---------------------------------------------------------------- Q8: a genie placed into itself
def test_ctdraw_places_a_genie_into_itself_but_refuses_the_save():
    """n0 placed into n0, and n2 (which nests n0) into n0: the placement is made in the
    window, the save stops with 'Attempt to save page with a link to itself' and fails
    (E_FAIL), the library is not written; nothing hangs."""
    calls = _calls("cycles")
    assert [c["stage"] for c in calls] == ["n0_into_n0", "n2_into_n0"]
    for c in calls:
        assert c["result"] == "returned None" and not c["builder ended"]
        assert any(cls == "Genie" for _an, cls in c["objects after"])
        assert "-2147467259" in c["save"]
        assert c["message boxes"] == ["Attempt to save page with a link to itself.  Press OK to abort [OK]"]
        assert c["library changed"] is False
