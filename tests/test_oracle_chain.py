"""What CtDraw writes along the save chain (oracle step 'chain')."""

import struct

import fxinstall
import pytest

pytestmark = pytest.mark.golden

#: the text the gained_member unit drew in n0 (tools/live/fixture_oracle.CHAIN_GAINED)
GAINED = "%Label% X"
#: the rectangle the outer_template unit drew on normal@XGA (fixture_oracle.INNER_RECT)
INNER_RECT = (300, 300, 400, 360)
OUTER, INNER = "fxouter@XGA", "normal@XGA"


def _lib(unit, *parts):
    return fxinstall.oracle("chain", unit, "/".join(parts))


def _recs(path, el):
    from ctgedit.format import genie_place as GP
    from ctgedit.format.element import open_element

    return [GP.decode(po) for po in open_element(path, el).tree]


def _levels(r):
    return [[list(b.pairs) for b in lvl] for lvl in (r.entries or [])]


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


def _text(r):
    return dict(r.entries[0][2].pairs).get("TXT_OFF") if r.entries else None


def _fixture(el):
    return _recs(fxinstall.FxInstall(fxinstall.FX_INSTALL).file("FxLib", "fxnest.ctm"), el)


# ---------------------------------------------------------------- a member gained (Q1)
def test_the_text_gained_in_n0_has_the_two_levels_of_n0s_own():
    recs = _recs(_lib("gained_member", "n0_gained", "library.ctm"), "n0")
    assert len(recs) == 3
    own, gained = recs[1], recs[2]
    assert _text(own) == "%Label%" and _text(gained) == GAINED
    assert _levels(gained) == [[[], [("TXT_OFF", " X")], [("TXT_OFF", GAINED)]], [[("Label", "\n")], [], []]]
    assert _declared(recs) == ["Label"]


@pytest.mark.parametrize("stage", ["same", "fresh"])
@pytest.mark.parametrize("k", [1, 2, 3, 4])
def test_each_copy_of_the_gained_text_carries_its_kept_siblings_levels(stage, k):
    """In n1 .. n4 the copy of the gained text has as many levels as the copy of n0's own
    text (k + 2: the element's, then one per genie it is nested in), the same bags on every
    level but the first, and its first level's value is the kept one's with ' X'."""
    el = "n%d" % k
    recs = _recs(_lib("gained_member", stage, el, "library.ctm"), el)
    kept = [r for r in recs if _text(r) == "%Label%"]
    gained = [r for r in recs if _text(r) == GAINED]
    assert len(kept) == len(gained) == 1, el
    kept, gained = kept[0], gained[0]
    assert len(gained.entries) == len(kept.entries) == k + 2
    assert _levels(gained)[1:] == _levels(kept)[1:]
    kv = dict(kept.entries[0][1].pairs)["TXT_OFF"]
    assert dict(gained.entries[0][1].pairs)["TXT_OFF"] == kv + " X"


@pytest.mark.parametrize("stage", ["same", "fresh"])
def test_no_element_of_the_chain_declares_the_nested_genies_parameter(stage):
    """The gained member adds no declaration: n1 still lists Name only (not Label), and each
    element of the chain lists what the fixture's lists. Each import entry quotes the stamp
    of the element it nests as saved just before."""
    for k in (1, 2, 3, 4):
        el, inner = "n%d" % k, "n%d" % (k - 1)
        path = _lib("gained_member", stage, el, "library.ctm")
        assert _declared(_recs(path, el)) == _declared(_fixture(el)), el
        assert _table(path, el) == ["fxnest.%s.<ctm%s" % (inner, _stamp(path, inner))], el
    assert _declared(_fixture("n1")) == ["Name"]


def test_a_later_session_writes_the_same_records():
    for k in (1, 2, 3, 4):
        el = "n%d" % k
        same = _recs(_lib("gained_member", "same", el, "library.ctm"), el)
        fresh = _recs(_lib("gained_member", "fresh", el, "library.ctm"), el)
        assert [(r.cf, r.tc, r.dw, r.handle, r.an, r.slots, r.anchor, r.rect1, _levels(r)) for r in same] == [
            (r.cf, r.tc, r.dw, r.handle, r.an, r.slots, r.anchor, r.rect1, _levels(r)) for r in fresh
        ], el


# ---------------------------------------------------------------- mirrored back (Q4)
def test_the_copy_takes_the_definitions_mirror():
    n0 = _recs(_lib("mirror_back", "n0_mirrored", "library.ctm"), "n0")
    assert [s[0] for s in n0[1].slots] == [3, 3, 3]
    assert all(s[1][0] == -1.0 for s in n0[1].slots)
    n1 = _recs(_lib("mirror_back", "after_mirror", "n1", "library.ctm"), "n1")
    copy = n1[3]
    assert _text(copy) == "%Label%"
    assert copy.slots[0] == (0, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
    assert [s[0] for s in copy.slots[1:]] == [3, 3]
    assert [s[1][0] for s in copy.slots[1:]] == [-1.0, -1.0]


@pytest.mark.parametrize("stage", ["after_back", "after_back_fresh"])
def test_the_copy_takes_the_definitions_identity_back(stage):
    """Mirrored back, n0 is the fixture's n0 again and n1's copy keeps no mirror of its own:
    every record of n1 is the fixture's (the import entry quotes n0's new stamp)."""
    path = _lib("mirror_back", stage, "n1", "library.ctm")

    def key(recs):
        return [
            (r.cf, r.tc, r.dw, r.handle, r.an, r.link, r.slots, r.anchor, r.rect1, r.rect2, _levels(r), r.cls)
            for r in recs
        ]

    assert key(_recs(path, "n1")) == key(_fixture("n1"))
    back = _lib("mirror_back", "n0_back", "library.ctm")
    assert key(_recs(back, "n0")) == key(_fixture("n0"))
    assert _table(path, "n1") == ["fxnest.n0.<ctm%s" % _stamp(path, "n0")]


# ---------------------------------------------------------------- a template in a template
def _inner_root(recs):
    hits = [i for i, r in enumerate(recs) if (r.link or "").lower() == "fx_style.normal@xga.<ctt"]
    assert len(hits) == 1
    return hits[0]


def test_ctdraws_new_template_holds_the_base_templates_copy():
    path = _lib("outer_template", "made", "library.ctt")
    recs = _recs(path, OUTER)
    i = _inner_root(recs)
    assert i == 1 and recs[i].dw & 0x5 == 0x5  # (linked, locked)
    for r in recs[:2]:
        assert _levels(r) == [[[], [], []]]  # (one level, nothing declared)
    assert _table(path, OUTER) == ["fx_style.normal@XGA.<ctt%s" % _stamp(path, INNER), "\\\\Fx Menu"]
    assert _declared(recs) == ["Description"]  # (the nested genie's member)


def test_its_plain_load_and_save_keeps_every_level_and_declaration():
    """Q3: CtDraw's load + save of the unedited outer template (a fresh Graphics Builder)
    changes no level, bag or declaration and no import entry; only the free DummyMarker takes
    the empty rect at the origin, as on a page."""
    made = _recs(_lib("outer_template", "made", "library.ctt"), OUTER)
    path = _lib("outer_template", "plain", OUTER, "library.ctt")
    plain = _recs(path, OUTER)
    assert len(plain) == len(made)
    assert [_levels(r) for r in plain] == [_levels(r) for r in made]
    changed = [k for k, (a, b) in enumerate(zip(made, plain)) if (a.anchor, a.rect1) != (b.anchor, b.rect1)]
    assert changed == [len(plain) - 1] and plain[-1].tc == 0xFFFF
    assert plain[-1].rect1 == (0, 0, 0, 0)
    assert _table(path, OUTER) == _table(_lib("outer_template", "made", "library.ctt"), OUTER)


@pytest.mark.parametrize("stage", ["same", "fresh"])
def test_its_load_and_save_composes_the_changed_inner_template(stage):
    """Q2: after normal@XGA gained a rectangle, the outer's load + save composes its copy
    (the inner root gains the child, the rectangle is its last member) and the import entry
    quotes the inner's new stamp."""
    plain = _recs(_lib("outer_template", "plain", OUTER, "library.ctt"), OUTER)
    path = _lib("outer_template", stage, OUTER, "library.ctt")
    recs = _recs(path, OUTER)
    i = _inner_root(recs)
    assert recs[i].cf == plain[i].cf + 1
    assert len(recs) == len(plain) + 1
    assert recs[-1].rect1 == INNER_RECT and recs[-1].tc == 0x0F
    changed = _lib("outer_template", "inner_changed", "library.ctt")
    assert (
        _stamp(path, INNER)
        == _stamp(changed, INNER)
        != _stamp(_lib("outer_template", "plain", OUTER, "library.ctt"), INNER)
    )
    assert _table(path, OUTER)[0] == "fx_style.normal@XGA.<ctt%s" % _stamp(changed, INNER)


@pytest.mark.parametrize("stage", ["same", "fresh"])
def test_a_new_page_from_it_shows_the_inner_templates_new_content(stage):
    """Q2: a New Page (linked) from the outer shows the rectangle, also when the outer was
    not saved again after the inner changed ('fresh': the New Page came first); the page's
    import table names the outer only, and its copy of the inner is no link of its own."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format import links
    from ctgedit.format.page import parse_ctg_bytes

    data = fxinstall.oracle_bytes("chain", "outer_template", "%s/new_page/page.ctg" % stage)
    pb = parse_ctg_bytes(data).page
    recs = [GP.decode(po) for po in pb.objects if po.type_code < 0x2000]
    assert any(r.rect1 == INNER_RECT for r in recs)
    names = [n for _g, n in links.parse_link_table(pb.link_table_raw)[1]]
    assert [n.split("#")[0] for n in names] == ["fx_style.fxouter@XGA.<ctt", "\\\\Fx Menu"]
    assert [r.link for r in recs if r.link] == ["fx_style.fxouter@XGA.<ctt"]
