"""What CtDraw writes along the save chain (oracle step 'chain6')."""

import math
import struct

import fxinstall
import pytest

pytestmark = pytest.mark.golden

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
EMPTY = [[], [], []]
OUT, INNER = "fxouter@XGA", "normal@XGA"


def _path(unit, *parts):
    return fxinstall.oracle("chain6", unit, "/".join(parts))


def _json(unit, name):
    return fxinstall.oracle_json("chain6", unit, name)


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

    pb = parse_ctg_bytes(fxinstall.oracle_bytes("chain6", unit, "/".join(stage + ("page.ctg",)))).page
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


def _tail(unit, stage, index):
    from ctgedit.format import geometry as G
    from ctgedit.format.page import parse_ctg_bytes

    po = parse_ctg_bytes(fxinstall.oracle_bytes("chain6", unit, "%s/page.ctg" % stage)).page.objects[index]
    return G.parse_text_tail(po)


def _text(recs, template):
    """The one text record whose template bag (e0.b2) is ``template``."""
    hits = [
        r
        for r in recs
        if r.tc == 0x05 and r.entries and dict(r.entries[0][2].pairs).get("TXT_OFF") == template
    ]
    assert len(hits) == 1, template
    return hits[0]


# ---------------------------------------------------------------- Q17: text geometry
def test_an_uneven_scale_of_a_plain_text_stays_a_matrix():
    """'Plain wide' scaled 2 x 1 and 'Plain tall' 1 x 2 about their top left: the scale is
    not baked into the font (size 11, the echo kept) but kept in all three slots (mode
    0x0F); the anchor is the echo mapped and the box the old box mapped."""
    _pb, drawn = _page("text_resize", "drawn")
    _pb, got = _page("text_resize", "resized")
    assert drawn[0].rect1 == (100, 100, 163, 116) and drawn[1].rect1 == (400, 100, 453, 116)
    for k, m, anchor, box, echo in (
        (0, (2.0, 0.0, 0.0, 1.0, -100.0, 0.0), (100, 113), (100, 100, 226, 116), (100, 113)),
        (1, (1.0, 0.0, 0.0, 2.0, 0.0, -100.0), (400, 126), (400, 100, 453, 132), (400, 113)),
    ):
        assert got[k].slots == [(0x0F, m)] * 3
        tail = _tail("text_resize", "resized", k)
        assert tail["size"] == 11 and tuple(tail["echo"]) == echo
        assert got[k].anchor == anchor and got[k].rect1 == got[k].rect2 == box


def test_a_mirrored_text_scaled_keeps_the_scale_and_a_negative_zero_in_its_matrix():
    """'Mirror back' (2 x 2) and 'Mirror wide' (2 x 1), mirrored first: the composed matrix
    in all three slots (mode 0x0F), its C entry stored as -0.0, the font 11."""
    _pb, mirrored = _page("text_resize", "mirrored")
    _pb, got = _page("text_resize", "resized")
    assert mirrored[2].slots == [(3, (-1.0, 0.0, 0.0, 1.0, 271.0, 0.0))] * 3
    assert mirrored[3].slots == [(3, (-1.0, 0.0, 0.0, 1.0, 869.0, 0.0))] * 3
    for k, m, box in (
        (2, (-2.0, 0.0, 0.0, 2.0, 442.0, -300.0), (100, 300, 242, 332)),
        (3, (-2.0, 0.0, 0.0, 1.0, 1338.0, 0.0), (400, 300, 538, 316)),
    ):
        assert got[k].slots == [(0x0F, m)] * 3
        assert all(math.copysign(1.0, s[1][2]) == -1.0 for s in got[k].slots)
        assert _tail("text_resize", "resized", k)["size"] == 11
        assert got[k].rect1 == box


def test_mirrored_back_a_uniform_scale_is_baked_and_an_uneven_one_stays_a_matrix():
    """The mirrored texts mirrored back: the uniform 2 x 2 becomes the font (11 -> 21), the
    slots identity, the echo its anchor; the 2 x 1 keeps the matrix (2, 0, +0, 1, -400, 0),
    mode 0x0F, the font 11. The plain texts are not touched; a fresh load + save keeps it."""
    _pb, resized = _page("text_resize", "resized")
    _pb, back = _page("text_resize", "mirrored_back")
    assert [_key(r) for r in back[:2]] == [_key(r) for r in resized[:2]]
    tail = _tail("text_resize", "mirrored_back", 2)
    assert tail["size"] == 21 and tuple(tail["echo"]) == (100, 326)
    assert back[2].slots == [(0, IDENTITY)] * 3
    assert back[2].anchor == (100, 326) and back[2].rect1 == (100, 300, 238, 332)
    assert back[3].slots == [(0x0F, (2.0, 0.0, 0.0, 1.0, -400.0, 0.0))] * 3
    assert all(math.copysign(1.0, s[1][2]) == 1.0 for s in back[3].slots)
    tail = _tail("text_resize", "mirrored_back", 3)
    assert tail["size"] == 11 and tuple(tail["echo"]) == (400, 313)
    assert back[3].rect1 == (400, 300, 538, 316)
    _pb, again = _page("text_resize", "reopened")
    assert [_key(r) for r in again] == [_key(r) for r in back]


# ---------------------------------------------------------------- Q18: an outer template, two parameters
def test_an_outer_template_with_a_default_declares_only_the_default_on_its_root():
    recs = _recs(_path("outer_mixed", "params_added", "library.ctt"), OUT)
    assert len(recs) == 14
    assert _levels(recs[0]) == [EMPTY, [[("Q", "5")], [], []]]
    p, q = _text(recs, "%P%"), _text(recs, "%Q=5%")
    assert _levels(p) == [[[], [], [("TXT_OFF", "%P%")]], [[("P", "\n")], [], []]]
    assert _levels(q) == [[[], [("TXT_OFF", "5")], [("TXT_OFF", "%Q=5%")]], [[("Q", "5")], [], []]]
    assert p.rect1 == (500, 500, 533, 516) and q.rect1 == (500, 540, 551, 556)
    for stage in ("resaved", "fresh"):
        again = _recs(_path("outer_mixed", stage, OUT, "library.ctt"), OUT)
        assert [_key(r) for r in again] == [_key(r) for r in recs]


@pytest.mark.parametrize("stage", ["first", "resaved", "fresh", "fresh_resaved"])
def test_a_new_page_stores_the_template_strings_as_the_values(stage):
    """New Page from the outer (as saved, after its load + save, in a fresh builder): the
    template root keeps Q = 5 declared and binds nothing; each text's value is its template
    string ('%P%', '%Q=5%', not '5'), measured as such (33 and 51 wide)."""
    pb, recs = _page("outer_mixed", stage, "new_page")
    root = recs[0]
    assert root.link == "fx_style.fxouter@XGA.<ctt" and root.dw == 0x0D
    assert _levels(root) == [EMPTY, [[("Q", "5")], [], []]]
    p, q = _text(recs, "%P%"), _text(recs, "%Q=5%")
    assert _levels(p) == [[[], [("TXT_OFF", "%P%")], [("TXT_OFF", "%P%")]], [[("P", "\n")], [], []]]
    assert _levels(q) == [[[], [("TXT_OFF", "%Q=5%")], [("TXT_OFF", "%Q=5%")]], [[("Q", "5")], [], []]]
    assert p.rect1 == (500, 500, 533, 516) and q.rect1 == (500, 540, 551, 556)
    lib = (
        "params_added/library.ctt"
        if stage in ("first", "fresh")
        else "%s/%s/library.ctt" % ("resaved" if stage == "resaved" else "fresh", OUT)
    )
    assert _names(_page_table(pb))[0] == "fx_style.fxouter@XGA.<ctt%s" % _stamp(
        _path("outer_mixed", lib), OUT
    )
    _pb, first = _page("outer_mixed", "first", "new_page")
    assert [_key(r) for r in recs] == [_key(r) for r in first]


# ---------------------------------------------------------------- Q19: a pasted page-form copy
def test_a_placement_pasted_into_a_genie_without_parameters_keeps_its_page_form():
    """n0 placed with nothing bound (its text empty, 4 wide), copied and pasted into fx_plain:
    the copy stays linked with the placement's two levels, fx_plain's own records keep one
    level, the root moves to the highest handle; fx_plain declares nothing."""
    _pb, placed = _page("paramless_copy", "placed")
    assert placed[1].rect1 == (100, 100, 104, 116)
    assert _levels(placed[1])[0] == [[], [], [("TXT_OFF", "%Label%")]]
    path = _path("paramless_copy", "pasted", "library.ctm")
    recs = _recs(path, "fx_plain")
    assert len(recs) == 5
    root, copy, text = recs[0], recs[3], recs[4]
    assert (root.cf, root.handle) == (3, 4) and len(root.entries) == 1
    assert all(len(r.entries) == 1 for r in recs[1:3])
    assert copy.dw == 0x209 and copy.link == "fxnest.n0.<ctm" and copy.rect1 == placed[0].rect1
    assert _levels(copy) == [EMPTY, EMPTY]
    assert _levels(text) == [[[], [], [("TXT_OFF", "%Label%")]], [[("Label", "\n")], [], []]]
    ((g, n),) = _table(path, "fx_plain")
    assert g != bytes(16) and n == "fxnest.n0.<ctm#ed02da80"
    (paste,) = _calls("paramless_copy", "pasted")
    assert paste["shown before the save"]["texts"] == {"4": ""}


def test_when_the_genie_gains_a_parameter_the_copy_gains_an_empty_outer_level():
    """fx_plain gains '%P%': its own records take a second level (P declared by the text,
    nothing on the root), every record of the copy one more, empty; P is the only name on
    an outermost level."""
    recs = _recs(_path("paramless_copy", "gained", "library.ctm"), "fx_plain")
    assert len(recs) == 6
    assert _levels(recs[0]) == [EMPTY, EMPTY] and recs[0].handle == 5
    assert _levels(recs[3]) == [EMPTY, EMPTY, EMPTY]
    assert _levels(recs[4]) == [[[], [], [("TXT_OFF", "%Label%")]], [[("Label", "\n")], [], []], EMPTY]
    new = recs[5]
    assert _levels(new) == [[[], [], [("TXT_OFF", "%P%")]], [[("P", "\n")], [], []]]
    assert new.rect1 == (0, 25, 33, 41)
    assert _declared(recs) == ["P"]


@pytest.mark.parametrize("stage", ["same", "fresh"])
def test_a_host_without_parameters_does_not_pass_the_parameter_through(stage):
    """A host without parameters doesn't pass the parameter through."""
    recs = _recs(_path("paramless_copy", stage, "fx_none", "library.ctm"), "fx_none")
    held = _recs(_path("paramless_copy", "host_made", "library.ctm"), "fx_none")
    assert len(held) == 7 and held[2].link == "fxgenie.fx_plain.<ctm"
    assert [len(r.entries) for r in held] == [1, 1, 1, 1, 1, 2, 2]
    assert len(recs) == 8 and [len(r.entries) for r in recs] == [1, 1, 2, 2, 2, 3, 3, 2]
    assert _levels(recs[7]) == [[[], [], [("TXT_OFF", "%P%")]], [[("P", "\n")], [], []]]
    assert recs[7].rect1 == (40, 25, 44, 41) and recs[7].an == "4"
    _pb, page = _page("paramless_copy", stage, "page")
    assert _levels(page[0]) == [[[], [("P", "PV"), ("Label", "LV")], []]]
    assert _levels(page[7])[0] == [[], [], [("TXT_OFF", "%P%")]]
    assert _levels(page[6])[0] == [[], [], [("TXT_OFF", "%Label%")]]
    assert page[7].rect1 == (240, 225, 244, 241)


# ---------------------------------------------------------------- Q20: a page placing a stale host
@pytest.mark.parametrize("stage", [("same",), ("fresh",), ("fresh", "placed")])
def test_a_page_placing_a_stale_host_gets_the_gained_text_bound_one_level_short(stage):
    """n0 gained '%Label% X', n1 was saved, n2 was not: the page's load + save (same and fresh
    builder) and a new placement of n2 give the text substituted with the page's Name
    ('P2 L1 X', 49 wide) and three levels where its twin has four (no level of n2's copy);
    the page keeps quoting n2's stamp."""
    _pb0, placed = _page("stale_page", "placed")
    assert len(placed) == 6
    pb, recs = _page("stale_page", *stage)
    assert len(recs) == 7
    new, twin = recs[6], recs[5]
    assert (new.tc, new.dw, new.handle, new.an) == (0x05, 0x18, 6, "6")
    assert _levels(new) == [
        [[], [("TXT_OFF", "P2 L1 X")], [("TXT_OFF", "%Label% X")]],
        [[("Label", "\n")], [("Label", "P2 L1")], [("Label", "%Name% L1")]],
        [[("Name", "\n")], [("Name", "P2")], []],
    ]
    assert new.rect1 == (200, 295, 249, 311) and recs[0].rect1 == (200, 200, 249, 311)
    assert len(twin.entries) == 4 and _levels(twin)[0][1] == [("TXT_OFF", "P2 L1")]
    assert _names(_page_table(pb)) == ["fxnest.n2.<ctm#ed02da80"]
    _pb, fresh = _page("stale_page", "fresh")
    assert [_key(r) for r in recs] == [_key(r) for r in fresh]


# ---------------------------------------------------------------- Q21: a template holding a placeholder
PH_KEYS = [
    ("__AN_NAME__", "FxTplMenu"),
    ("_Library", "fxgenie.fx_value.<ctm"),
    ("_Transform_A", "1.000000"),
    ("_Transform_B", "0.000000"),
    ("_Transform_C", "0.000000"),
    ("_Transform_D", "1.000000"),
    ("_Transform_H", "884.000000"),
    ("_Transform_K", "716.000000"),
    ("_Transform_Byte", "1"),
]


def test_a_template_saved_without_its_genies_library_holds_a_placeholder_once_the_builder_started_without_it():
    """A template saved without its genie's library holds a placeholder."""
    calls = _calls("template_placeholder")
    assert [c.get("placeholder") for c in calls if "placeholder" in c] == [False, True]
    kept = _path("template_placeholder", "template_missing", "library.ctt")
    recs = _recs(kept, INNER)
    assert len(recs) == 11 and recs[7].link == "fxgenie.fx_value.<ctm" and recs[7].dw == 0x219
    assert _names(_table(kept, INNER)) == ["fxgenie.fx_value.<ctm#ed02da80", "\\\\Fx Menu"]
    held = _path("template_placeholder", "template_missing_fresh", "library.ctt")
    recs = _recs(held, INNER)
    assert len(recs) == 9 and recs[0].handle == 7
    ph = recs[7]
    assert (ph.tc, ph.dw, ph.handle, ph.an, ph.name) == (0xFFFF, 0xA, 6, "-1", "FxTplMenu")
    assert ph.anchor == (824, 733) and ph.rect1 == ph.rect2 == (824, 698, 944, 734)
    assert ph.slots == [(0, IDENTITY)] * 3
    assert _levels(ph) == [[[], PH_KEYS, PH_KEYS], [[], [("Description", "Fx Menu")], []]]
    assert _table(held, INNER) == [(bytes(16), "fxgenie.fx_value.<ctm")]


def _menu_copy(recs):
    menu, text, rect = recs[7], recs[8], recs[9]
    assert (menu.tc, menu.dw, menu.cf, menu.name) == (0x08, 0x218, 2, "FxTplMenu")
    assert _levels(text)[0][1] == [("TXT_OFF", "@(Fx Menu)")]
    return (menu.handle, menu.an), (text.handle, text.an), (rect.handle, rect.an)


@pytest.mark.parametrize("stage", ["page_back", "page_after", "page_after_fresh"])
def test_a_linked_page_gets_the_genie_at_once_with_the_library_back(stage):
    """The library back, the template still holding the placeholder: FxTplLinked's load +
    save places fx_value in its template copy at once (17 records, the members' ANs the
    handles the template's own save gives them, 8 and 10); the load + save after the
    template's own save gives the same records."""
    pb, recs = _page("template_placeholder", stage)
    assert len(recs) == 17
    assert _menu_copy(recs) == ((6, "6"), (8, "8"), (7, "10"))
    assert [r.tc for r in recs].count(0xFFFF) == 1
    lib = (
        "template_missing_fresh/library.ctt"
        if stage == "page_back"
        else "template_back/%s/library.ctt" % INNER
    )
    assert _names(_page_table(pb)) == [
        "fx_style.normal@XGA.<ctt%s" % _stamp(_path("template_placeholder", lib), INNER),
        "\\\\Fx Menu",
        "fxgenie.fx_pump.<ctm#ed02da80",
    ]
    _pb, back = _page("template_placeholder", "page_back")
    assert [_key(r) for r in recs] == [_key(r) for r in back]


def test_a_new_page_from_the_template_holding_the_placeholder_places_the_genie():
    pb, recs = _page("template_placeholder", "new_page")
    assert len(recs) == 11 and recs[0].handle == 7
    assert _menu_copy(recs) == ((6, "6"), (8, "8"), (10, "10"))
    assert recs[10].tc == 0xFFFF and recs[10].rect1 == (700, 100, 803, 119)
    assert _names(_page_table(pb)) == [
        "fx_style.normal@XGA.<ctt%s"
        % _stamp(_path("template_placeholder", "template_missing_fresh", "library.ctt"), INNER),
        "\\\\Fx Menu",
    ]


def test_the_templates_load_and_save_places_the_genie_again_with_its_name():
    path = _path("template_placeholder", "template_back", INNER, "library.ctt")
    recs = _recs(path, INNER)
    assert len(recs) == 11 and recs[0].handle == 7
    menu = recs[7]
    assert (menu.dw, menu.handle, menu.name, menu.link) == (0x219, 6, "FxTplMenu", "fxgenie.fx_value.<ctm")
    assert _levels(menu)[0] == [[], [("__AN_NAME__", "FxTplMenu")], [("__AN_NAME__", "FxTplMenu")]]
    assert (recs[8].handle, recs[9].handle, recs[9].dw) == (8, 10, 0x18)
    assert _names(_table(path, INNER)) == ["fxgenie.fx_value.<ctm#ed02da80", "\\\\Fx Menu"]


# ---------------------------------------------------------------- Q22: a copy pasted into an element
def test_a_placed_genie_pasted_into_an_element_binds_its_value_as_a_literal():
    """n2 placed with Name 'P', copied and pasted into n3: the copy stays linked, its root's
    placed level holds Name 'P' in the value AND the template bag, with n3's empty level
    after; the members store the bound values ('P', 'P L1') over their own templates."""
    recs = _recs(_path("pasted_copy", "pasted", "library.ctm"), "n3")
    assert len(recs) == 14 and recs[0].handle == 13
    copy = recs[8]
    assert copy.dw == 0x209 and copy.link == "fxnest.n2.<ctm" and copy.handle == 7
    assert _levels(copy) == [EMPTY, [[], [("Name", "P")], [("Name", "P")]], EMPTY]
    assert _levels(recs[11]) == [
        [[], [("TXT_OFF", "P")], [("TXT_OFF", "%Name%")]],
        [[("Name", "\n")], [("Name", "P")], [("Name", "%Name%")]],
        [[("Name", "\n")], [("Name", "P")], [("Name", "P")]],
        EMPTY,
    ]
    assert _levels(recs[13]) == [
        [[], [("TXT_OFF", "P L1")], [("TXT_OFF", "%Label%")]],
        [[("Label", "\n")], [("Label", "P L1")], [("Label", "%Name% L1")]],
        [[("Name", "\n")], [("Name", "P")], [("Name", "%Name%")]],
        [[("Name", "\n")], [("Name", "P")], [("Name", "P")]],
        EMPTY,
    ]
    again = _recs(_path("pasted_copy", "reopened", "n3", "library.ctm"), "n3")
    assert [_key(r) for r in again] == [_key(r) for r in recs]


def test_the_element_window_shows_the_bound_values_of_the_pasted_copy():
    """AttributeText of the window's texts: the pasted copy shows 'P' and 'P L1', n3's own
    copy its stored values with the tokens; the same after a fresh load."""
    (pasted,) = _calls("pasted_copy", "pasted")
    (reopened,) = _calls("pasted_copy", "reopened/n3")
    want = {
        "1": "%Extra%",
        "3": "%Name% 3 L1",
        "5": "%Name% 3",
        "7": "N2",
        "9": "N2",
        "11": "P",
        "13": "P L1",
    }
    assert pasted["shown before the save"]["texts"] == want
    assert reopened["shown"]["texts"] == want
