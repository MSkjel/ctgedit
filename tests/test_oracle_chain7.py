"""What CtDraw writes along the save chain (oracle step 'chain7')."""

import struct

import fxinstall
import pytest

pytestmark = pytest.mark.golden

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
EMPTY = [[], [], []]
OUT, INNER = "fxouter@XGA", "normal@XGA"
SELF_LINK = "Attempt to save page with a link to itself.  Press OK to abort [OK]"
UPDATE_PAGES = (
    "Please use Update Pages under Tools menu\nTo resave any page(s) associated with this template. [OK]"
)


def _path(unit, *parts):
    return fxinstall.oracle("chain7", unit, "/".join(parts))


def _json(unit, name):
    return fxinstall.oracle_json("chain7", unit, name)


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

    pb = parse_ctg_bytes(fxinstall.oracle_bytes("chain7", unit, "/".join(stage + ("page.ctg",)))).page
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


def _block_stamp(path):
    with open(path, "rb") as fh:
        return "#%08x" % struct.unpack_from("<I", fh.read(), 0x20)[0]


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


def _text(recs, template):
    """The one text record whose template bag (e0.b2) is ``template``."""
    hits = [
        r
        for r in recs
        if r.tc == 0x05 and r.entries and dict(r.entries[0][2].pairs).get("TXT_OFF") == template
    ]
    assert len(hits) == 1, template
    return hits[0]


def _named(recs, name):
    hits = [r for r in recs if r.name == name]
    assert len(hits) == 1, name
    return hits[0]


def _markers(recs):
    return [r for r in recs if r.tc == 0xFFFF and r.entries and dict(r.entries[0][1].pairs).get("_Library")]


def _rect(recs, box):
    hits = [r for r in recs if r.rect1 == box]
    assert len(hits) == 1, box
    return hits[0]


# ---------------------------------------------------------------- Q24 (b): pages from a placeholder template
def test_new_pages_from_a_template_holding_a_placeholder_place_the_genie():
    """New pages from a template holding a placeholder place the genie."""
    held = fxinstall.oracle("chain6", "template_placeholder", "template_missing_fresh/library.ctt")
    assert _markers(_recs(held, INNER))
    pb, recs = _page("holder_pages", "linked")
    assert len(recs) == 11 and not _markers(recs)
    root = recs[0]
    assert root.dw == 0x0D and root.link == "fx_style.normal@XGA.<ctt" and root.handle == 7
    menu = _named(recs, "FxTplMenu")
    assert menu.dw == 0x218 and menu.link is None and menu.handle == 6
    assert _levels(menu)[1][1] == [("Description", "Fx Menu")]
    text = _text(recs, "@(%Description%)")
    assert dict(text.entries[0][1].pairs)["TXT_OFF"] == "@(Fx Menu)"
    assert text.handle == 8 and text.rect1 == (824, 698, 902, 714)
    assert _rect(recs, (824, 716, 944, 734)).handle == 10
    assert _page_table(pb)[0][1] == "fx_style.normal@XGA.<ctt%s" % _stamp(held, INNER)
    assert _names(_page_table(pb))[1:] == ["\\\\Fx Menu"]
    pb, recs = _page("holder_pages", "unlinked")
    assert len(recs) == 10 and not _markers(recs)
    assert not [r for r in recs if r.link and r.link.endswith(".<ctt")]
    menu = _named(recs, "FxTplMenu")
    assert menu.dw == 0x219 and menu.link == "fxgenie.fx_value.<ctm"
    assert _levels(menu)[1][1] == [("Description", "Fx Menu")]
    assert dict(_text(recs, "@(%Description%)").entries[0][1].pairs)["TXT_OFF"] == "@(Fx Menu)"
    (g, n), (g2, n2) = _page_table(pb)
    assert g != bytes(16) and n == "fxgenie.fx_value.<ctm#ed02da80"
    assert g2 == bytes(16) and n2 == "\\\\Fx Menu"


@pytest.mark.parametrize("stage", ["cut_fixture", "cut_linked"])
def test_cut_link_from_a_placeholder_template_places_the_genie_without_its_binding(stage):
    """Cut Link of FxTplLinked and of the linked New Page, the template holding the
    placeholder: the genie is placed (a linked 0x219 copy, no marker left) but its binding
    is lost ('@()' measured, 24 wide), as a Cut Link of the plain template loses it; the
    table lists the genie's stamped entry, no bare entry and no label."""
    pb, recs = _page("holder_pages", stage)
    assert not _markers(recs)
    assert not [r for r in recs if r.link and r.link.endswith(".<ctt")]
    menu = _named(recs, "FxTplMenu")
    assert menu.dw == 0x219 and menu.link == "fxgenie.fx_value.<ctm"
    assert _levels(menu)[1] == EMPTY
    text = _text(recs, "@(%Description%)")
    assert dict(text.entries[0][1].pairs)["TXT_OFF"] == "@()"
    assert text.rect1 == (824, 698, 848, 714) and (text.handle, text.an) == (8, "8")
    table = _page_table(pb)
    assert table[0][0] != bytes(16) and table[0][1] == "fxgenie.fx_value.<ctm#ed02da80"
    assert all(g != bytes(16) for g, _n in table)
    assert len(recs) == (17 if stage == "cut_fixture" else 11)


# ---------------------------------------------------------------- Q25 (a): a page copy pasted into a genie
def test_a_page_copy_of_a_genie_without_parameters_pasted_into_n0_takes_its_level():
    """fx_plain placed on a page (one level each), copied and pasted into n0: the copy keeps
    the page form (linked, its handles kept but where n0 uses one) and every record of it
    takes n0's empty level; n0 still declares only Label, its root moves to h 4. n0's load +
    save in a fresh Graphics Builder is a fixed point."""
    _pb, placed = _page("paste_paramless", "placed")
    assert [len(r.entries) for r in placed] == [1, 1, 1]
    assert [r.handle for r in placed] == [2, 0, 1]
    path = _path("paste_paramless", "pasted", "library.ctm")
    recs = _recs(path, "n0")
    assert len(recs) == 5 and _declared(recs) == ["Label"]
    assert (recs[0].cf, recs[0].handle) == (2, 4) and _levels(recs[0]) == [EMPTY, EMPTY]
    assert _levels(recs[1]) == [[[], [], [("TXT_OFF", "%Label%")]], [[("Label", "\n")], [], []]]
    copy = recs[2]
    assert copy.dw == 0x209 and copy.link == "fxgenie.fx_plain.<ctm"
    assert (copy.handle, copy.an) == (2, "2") and copy.rect1 == placed[0].rect1
    assert [(r.handle, r.an) for r in recs[3:]] == [(3, "0"), (1, "1")]
    assert all(_levels(r) == [EMPTY, EMPTY] for r in recs[2:])
    ((g, n),) = _table(path, "n0")
    assert g != bytes(16) and n == "fxgenie.fx_plain.<ctm#ed02da80"
    again = _recs(_path("paste_paramless", "fresh", "n0", "library.ctm"), "n0")
    assert [_key(r) for r in again] == [_key(r) for r in recs]


def test_the_host_of_n0_nests_the_pasted_copy_one_level_deeper():
    """n1's load + save after the paste: its copy of n0 holds the fx_plain copy as an
    unlinked genie copy (0x208), every record of it with three levels."""
    recs = _recs(_path("paste_paramless", "same", "n1", "library.ctm"), "n1")
    assert len(recs) == 7 and _declared(recs) == ["Name"]
    inner = recs[4]
    assert inner.dw == 0x208 and inner.link is None
    assert all(len(r.entries) == 3 for r in recs[2:])


# ---------------------------------------------------------------- Q25 (b): a template update under '%P%'
@pytest.mark.parametrize("stage", ["same", "fresh", "fresh/after_outer", "update_pages"])
def test_a_template_update_blanks_the_unbound_parameter_the_new_page_typed(stage):
    """The New Page from fxouter (its own '%P%', no default) types the token ('%P%', 33
    wide); after normal@XGA changes, the page's load + save (same and fresh Graphics
    Builder, after the outer's load + save) and Update Pages blank it: no TXT_OFF value,
    4 wide, the P declaration kept. The page takes the new rectangle."""
    _pb, made = _page("outer_param_page", "page_made")
    p = _text(made, "%P%")
    assert dict(p.entries[0][1].pairs)["TXT_OFF"] == "%P%" and p.rect1 == (500, 500, 533, 516)
    pb, recs = _page("outer_param_page", *stage.split("/"))
    assert len(recs) == len(made) + 1
    _rect(recs, (300, 300, 400, 360))
    p = _text(recs, "%P%")
    assert _levels(p)[0] == [[], [], [("TXT_OFF", "%P%")]]
    assert _levels(p)[1][0] == [("P", "\n")]
    assert p.rect1 == (500, 500, 504, 516)


def test_update_pages_saves_the_outer_first_and_the_page_quotes_it():
    """Tools > Update Pages over COM saves fxouter (normal@XGA changed under it) and the page
    quotes the outer's new stamp; the plain load + save quotes the outer as it was."""
    outer = _block_stamp(_path("outer_param_page", "update_pages", OUT + ".block"))
    before = _block_stamp(_path("outer_param_page", "inner_changed", OUT + ".block"))
    assert outer != before
    pb, _recs_ = _page("outer_param_page", "update_pages")
    assert _names(_page_table(pb))[0] == "fx_style.fxouter@XGA.<ctt%s" % outer
    pb, _recs_ = _page("outer_param_page", "fresh")
    assert _names(_page_table(pb))[0] == "fx_style.fxouter@XGA.<ctt%s" % before


# ---------------------------------------------------------------- Q25 (c): a genie in a template with a parameter
def test_a_genie_placed_into_a_template_with_a_parameter_is_in_the_element_form():
    """fxouter@XGA with '%P%': n0 placed with Label 'TL' carries the template's level (three
    levels: its placed level with the value in both bags, then an empty one); fx_plain two;
    the template declares only P. Its load + save in a fresh Graphics Builder is a fixed
    point and a New Page from it holds all of it."""
    recs = _recs(_path("genie_in_outer", "placed", "library.ctt"), OUT)
    assert len(recs) == 18 and _declared(recs) == ["P"]
    n0 = [r for r in recs if r.link == "fxnest.n0.<ctm"]
    assert len(n0) == 1 and n0[0].dw == 0x209
    assert _levels(n0[0]) == [EMPTY, [[], [("Label", "TL")], [("Label", "TL")]], EMPTY]
    text = _text(recs, "%Label%")
    assert _levels(text) == [
        [[], [("TXT_OFF", "TL")], [("TXT_OFF", "%Label%")]],
        [[("Label", "\n")], [("Label", "TL")], [("Label", "TL")]],
        EMPTY,
    ]
    k = recs.index([r for r in recs if r.link == "fxgenie.fx_plain.<ctm"][0])
    assert all(_levels(r) == [EMPTY, EMPTY] for r in recs[k : k + 3])
    again = _recs(_path("genie_in_outer", "fresh", OUT, "library.ctt"), OUT)
    assert [_key(r) for r in again] == [_key(r) for r in recs]
    _pb, page = _page("genie_in_outer", "fresh", "new_page")
    assert len(page) == 18 and page[0].dw == 0x0D and page[0].link == "fx_style.fxouter@XGA.<ctt"


# ---------------------------------------------------------------- Q25 (d): leading blanks
def test_a_leading_blank_stays_in_the_elements_own_value():
    """n0's text set to ' %Label%': its value keeps the blank (' '), the box is the caption
    as typed (63 wide)."""
    recs = _recs(_path("label_blank", "n0_blank", "library.ctm"), "n0")
    text = recs[1]
    assert _levels(text)[0] == [[], [("TXT_OFF", " ")], [("TXT_OFF", " %Label%")]]
    assert text.rect1 == (10, 10, 73, 26)


def test_a_hosts_copy_trims_the_leading_blank_from_its_value_but_measures_it():
    """n1's load + save: its copy's text stores '%Name% L1' (the blank trimmed) and is
    measured with it (84 wide, 80 before); n0 placed into n1 with Label ' %Name% L1': the
    binding keeps its blank in the level ('  L1'), the text stores '%Name% L1', 88 wide."""
    recs = _recs(_path("label_blank", "n1_after", "n1", "library.ctm"), "n1")
    text = _text(recs, " %Label%")
    assert dict(text.entries[0][1].pairs)["TXT_OFF"] == "%Name% L1"
    assert _levels(text)[1] == [[("Label", "\n")], [("Label", " L1")], [("Label", "%Name% L1")]]
    assert text.rect1 == (10, 40, 94, 56)
    recs = _recs(_path("label_blank", "n1_bound", "library.ctm"), "n1")
    new = [r for r in recs if r.tc == 0x05 and r.rect1[1] == 70]
    assert len(new) == 1
    assert dict(new[0].entries[0][1].pairs)["TXT_OFF"] == "%Name% L1"
    assert _levels(new[0])[1][1:] == [[("Label", "  L1")], [("Label", " %Name% L1")]]
    assert new[0].rect1 == (10, 70, 98, 86)
    again = _recs(_path("label_blank", "fresh", "n1", "library.ctm"), "n1")
    assert [_key(r) for r in again] == [_key(r) for r in recs]


def test_a_page_placement_trims_the_value_and_keeps_the_blank_in_the_level():
    """n1 placed with Name 'P1': both copies' texts store 'P1 L1'; the copy bound with
    ' %Name% L1' keeps ' P1 L1' in its level and is measured with the blank."""
    _pb, recs = _page("label_blank", "page")
    texts = [r for r in recs if r.tc == 0x05]
    assert [dict(r.entries[0][1].pairs)["TXT_OFF"] for r in texts] == ["P1", "P1 L1", "P1 L1"]
    assert [r.rect1 for r in texts] == [(200, 200, 217, 216), (200, 230, 241, 246), (200, 260, 245, 276)]
    assert _levels(texts[2])[1][1] == [("Label", " P1 L1")]
    assert _levels(texts[1])[1][1] == [("Label", "P1 L1")]


# ---------------------------------------------------------------- Q26: pages open while their template is saved
@pytest.mark.parametrize("how", ["menu", "com"])
def test_pages_open_in_ctdraw_take_a_template_saved_in_another_window(how):
    """Pages open in CtDraw take a template saved in another window."""
    (rec,) = [c for c in _calls("open_pages") if c.get("how") == how]
    assert rec["template saved"]
    assert not any(w["file changed by the template's save"] for w in rec["windows"].values())
    boxes = rec["watch"].get("message boxes", [])
    assert boxes == ([UPDATE_PAGES] if how == "menu" else [])
    stamp = _stamp(_path("open_pages", how, "template", "library.ctt"), INNER)
    pb, recs = _page("open_pages", how, "FxTplLast")
    assert len(recs) == 21
    root = [r for r in recs if r.link == "fx_style.normal@XGA.<ctt"]
    assert len(root) == 1 and root[0].cf == 9
    new = _rect(recs, (300, 300, 400, 360))
    assert (new.tc, new.dw, new.handle, new.an) == (0x0F, 0x8, 20, "10") and new is recs[-1]
    assert _names(_page_table(pb)) == [
        "fxparts.fx_many.<ctm#ed02da80",
        "fx_style.normal@XGA.<ctt%s" % stamp,
        "\\\\Fx Menu",
    ]
    made_pb, made = _page("open_pages", "page_made")
    pb, recs = _page("open_pages", how, "FxOracleOuterLinked")
    assert len(recs) == 13 and _key(recs[0]) == _key(made[0])
    assert [(r.handle, r.an) for r in recs[1:]] == [(h, str(h)) for h in range(12, 24)]
    assert [_key(r)[5:] for r in recs[1:12]] == [_key(r)[5:] for r in made[1:12]]
    assert recs[-1].rect1 == (300, 300, 400, 360)
    (g, n), (g2, n2) = _page_table(pb)
    assert g == bytes(16) and n == _page_table(made_pb)[0][1]
    assert _page_table(made_pb)[0][0] != bytes(16)
    assert (g2, n2) == (bytes(16), "\\\\Fx Menu")


# ---------------------------------------------------------------- Q28 (b): a symbol of one DummyMarker
def test_a_symbol_of_one_dummy_marker_is_saved_under_a_root_group():
    """normal@XGA's free marker as the only record of fxsym.fx_marker: CtDraw's save adds a
    root group (h 1, the marker's box, slot 2 the origin's translation) and keeps the marker
    as it was; a fresh load + save is a fixed point."""
    before = _recs(_path("marker_symbol", "input", "library.ctl"), "fx_marker")
    assert len(before) == 1 and before[0].tc == 0xFFFF
    recs = _recs(_path("marker_symbol", "saved", "fx_marker", "library.ctl"), "fx_marker")
    assert len(recs) == 2
    root = recs[0]
    assert (root.cf, root.tc, root.dw, root.handle) == (1, 0x08, 0x0A, 1)
    assert root.anchor == (700, 118) and root.rect1 == root.rect2 == (700, 100, 803, 119)
    assert root.slots == [(0, IDENTITY), (1, (1.0, 0.0, 0.0, 1.0, -20.0, -15.0)), (0, IDENTITY)]
    assert _levels(root) == [EMPTY]
    assert _key(recs[1]) == _key(before[0])
    again = _recs(_path("marker_symbol", "fresh", "fx_marker", "library.ctl"), "fx_marker")
    assert [_key(r) for r in again] == [_key(r) for r in recs]


def test_a_scaled_marker_has_its_transform_baked_into_its_class_part():
    """A point marker with a zero class part under a 1.2 scale (the form a library
    holds): under the root group its slots become identity, its class rect the mapped
    point (631, 361, 631, 361), its anchor and class point (0, 0)."""
    el = "fx_marker_scaled"
    before = _recs(_path("marker_symbol", "input", "library.ctl"), el)
    assert before[0].slots[0][0] == 3 and struct.unpack("<4h2h", before[0].cls) == (0,) * 6
    recs = _recs(_path("marker_symbol", "saved", el, "library.ctl"), el)
    assert len(recs) == 2
    root, mark = recs
    assert root.tc == 0x08 and root.anchor == (631, 360)
    assert root.rect1 == root.rect2 == (631, 361, 631, 361)
    assert mark.slots == [(0, IDENTITY)] * 3 and mark.anchor == (0, 0)
    assert mark.rect1 == mark.rect2 == (631, 361, 631, 361)
    assert struct.unpack("<4h2h", mark.cls) == (631, 361, 631, 361, 0, 0)
    again = _recs(_path("marker_symbol", "fresh", el, "library.ctl"), el)
    assert [_key(r) for r in again] == [_key(r) for r in recs]


# ---------------------------------------------------------------- Q28 (d): an array text with row 0 unbound
@pytest.mark.parametrize("stage", ["placed", "reopened", "fresh"])
def test_an_array_text_with_its_first_row_unbound_is_measured_with_the_first_row_set(stage):
    """fx_plain's text '%Array0%' with the array rows '%Array0%' .. '%Array2%' (66 wide in
    the genie), placed with Array1 = 'A1': the value has no TXT_OFF and no ARRAY_0, and the
    box is 'A1' measured (17 wide), not the token (66) nor a blank (4); the page's load +
    save keeps it."""
    genie = _recs(_path("array_token", "genie", "library.ctm"), "fx_plain")
    tg = _text(genie, "%Array0%")
    assert tg.rect1 == (0, 100, 66, 116)
    assert dict(tg.entries[0][2].pairs)["ARRAY_1"] == "%Array1%"
    _pb, recs = _page("array_token", stage)
    text = _text(recs, "%Array0%")
    assert _levels(text)[0][1] == [("TXTMODE", "2"), ("TXT_A", "FX_IDX"), ("ARRAY_1", "A1")]
    assert text.rect1 == (100, 200, 117, 216)


# ---------------------------------------------------------------- Q28 (e): legacy Number / String extents
#: the extent CtDraw derives (rect2 - anchor) for each record the tool made, whatever the
#: stored one (an odd stored extent too)
LEGACY_DELTAS = {
    "FxNum00": (0, -13, 40, 4),
    "FxNum01": (0, -13, 40, 4),
    "FxNum02": (0, -13, 40, 4),
    "FxNum03": (0, -13, 40, 4),
    "FxNum04": (0, -17, 45, 4),
    "FxNum05": (0, -10, 27, 3),
    "FxNum06": (0, -11, 40, 2),
    "FxNum07": (0, -13, 8, 4),
    "FxNum08": (0, -13, 104, 4),
    "FxNum09": (0, -13, 48, 4),
    "FxNum10": (0, -13, 48, 4),
    "FxNum11": (0, -17, 125, 4),
    "FxNum12": (0, -13, 24, 4),
    "FxStr00": (0, -12, 64, 5),
    "FxStr01": (0, -12, 64, 5),
    "FxStr02": (0, -12, 64, 5),
    "FxStr03": (0, -16, 80, 5),
    "FxStr04": (0, -9, 45, 4),
    "FxStr05": (0, -9, 45, 4),
    "FxStr06": (0, -12, 64, 5),
    "FxStr07": (0, -12, 16, 5),
    "FxStr08": (0, -12, 160, 5),
    "FxStr09": (0, -12, 64, 5),
    "FxStr10": (0, -12, 64, 5),
    "FxStr11": (0, -10, 64, 3),
    "FxStr12": (0, -12, 64, 5),
}


def test_ctdraw_derives_the_legacy_number_and_string_extents():
    """CtDraw derives the legacy Number and String extents."""
    _pb, before = _page("legacy_measure", "input")
    _pb, recs = _page("legacy_measure", "loaded")
    _pb, again = _page("legacy_measure", "again")
    assert len(recs) == len(before) == len(LEGACY_DELTAS)
    for b, r, a in zip(before, recs, again):
        ax, ay = r.anchor
        got = (r.rect2[0] - ax, r.rect2[1] - ay, r.rect2[2] - ax, r.rect2[3] - ay)
        assert got == LEGACY_DELTAS[r.name], r.name
        assert (r.anchor, r.rect1, _levels(r)) == (b.anchor, b.rect1, _levels(b)), r.name
        assert _key(a) == _key(r), r.name


# ---------------------------------------------------------------- Q24 (a), (c): cycles
def test_a_template_saved_over_itself_is_refused():
    """PageNewTemplate from normal@XGA (linked) saved under normal's name, and one from fxouter
    (which holds normal's copy): CtDraw answers 'Attempt to save page with a link to itself'
    for each spelling, PageSaveAsEx fails and the library is not written."""
    for stage in ("self", "outer_self"):
        (rec,) = _calls("self_template", stage)
        assert rec["library changed"] is False
        assert rec["message boxes"] == [SELF_LINK, SELF_LINK]
        assert [t["PageSaveAsEx"][2] for t in rec["tries"]] == ["normal", "normal@XGA"]
        assert all(t["save"].startswith("error") and not t["library changed"] for t in rec["tries"])


def test_a_genie_holding_a_placeholder_of_itself_does_not_open():
    """fx_row's content (a placeholder of fxnest.n1) written into fxnest as n1, and as n0
    (n1 nests n0): CtDraw's PageOpenEx faults at once ('The server threw an exception'), no
    window opens and nothing is written."""
    for name in ("n1", "n0"):
        (rec,) = _calls("self_placeholder", "%s_holds" % name)
        assert "The server threw an exception" in rec["open"]
        assert rec["library changed"] is False and "save" not in rec
        assert _markers(_recs(_path("self_placeholder", "%s_holds" % name, "input", "library.ctm"), name))
