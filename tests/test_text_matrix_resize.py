"""Resizing mirrored texts and placed text widths."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

import fxinstall  # noqa: E402

from ctgedit.format import geometry as G  # noqa: E402
from ctgedit.format import genie_place as GP  # noqa: E402
from ctgedit.format import placement as P  # noqa: E402

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _levels(r):
    return [[list(b.pairs) for b in lvl] for lvl in (r.entries or [])]


def _key(r):
    return (r.cf, r.tc, r.dw, r.handle, r.an, r.name, r.link, r.slots, r.anchor, r.rect1, r.rect2, _levels(r))


def _read(path):
    from ctgedit.format.page import parse_ctg_bytes

    with open(path, "rb") as fh:
        return parse_ctg_bytes(fh.read()).page.objects


def _put(fx, data: bytes, name: str = "FxOracleDraw") -> str:
    """CtDraw's page ``data`` as page ``name`` of FxMain in the test's install."""
    path = os.path.join(fx.project("FxMain"), name + ".ctg")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _total_of(po):
    return P.read_slots(po.body_raw)[0][2][1]


def _laid_out(po) -> bool:
    """The anchor is the echo through the Total, truncated."""
    a, b, c, d, h, k = _total_of(po)
    ex, ey = G.parse_text_tail(po)["echo"]
    return GP.decode(po).anchor == (int(a * ex + c * ey + h), int(b * ex + d * ey + k))


def _first_text(objs):
    return next(k for k, o in enumerate(objs) if o.type_code == 0x05)


def _lib(fx):
    return fx.file("FxLib", "fxnest.ctm")


# ------------------------------------------------------------- a mirrored text resized alone
@pytest.mark.golden
def test_a_resized_mirrored_text_is_the_record_ctdraw_writes(fx_install):
    """CtDraw's page with 'Mirror me' mirrored, resized to twice its box: the record is
    CtDraw's (mode 0x0F in all three slots, size 11, echo (100, 113), anchor (220, 126),
    box (100, 100, 220, 132)). 'Plain me' resized the same way bakes the scale into its
    font, as CtDraw does."""
    from ctgedit.editing import EditableDocument

    path = _put(fx_install, fxinstall.oracle_bytes("chain4", "mirror_resize", "mirrored/page.ctg"))
    want = [GP.decode(p) for p in _read(fxinstall.oracle("chain4", "mirror_resize", "resized/page.ctg"))]
    doc = EditableDocument.open(path)
    doc.set_object_rect(0, (100, 100, 220, 132))
    doc.set_object_rect(1, (100, 200, 208, 232))
    doc.set_object_rect(3, (100, 300, 300, 420))
    objs = doc.parsed.page.objects
    tail = G.parse_text_tail(objs[0])
    assert tail["size"] == 11 and tuple(tail["echo"]) == (100, 113)
    assert G.parse_text_tail(objs[1])["size"] == 21
    for k in (0, 1, 3):
        assert _key(GP.decode(objs[k])) == _key(want[k]), k
    doc.save()
    saved = [GP.decode(p) for p in _read(path)]
    again = [GP.decode(p) for p in _read(fxinstall.oracle("chain4", "mirror_resize", "reopened/page.ctg"))]
    for k in (0, 1, 3):
        assert _key(saved[k]) == _key(want[k]) == _key(again[k]), k


@pytest.mark.golden
def test_a_resized_mirrored_text_in_a_genie_is_ctdraws_and_reaches_its_host(fx_install):
    """A resized mirrored text in a genie matches CtDraw and reaches its host."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx_install)
    d = ElementDocument.open_element(lib, "n0")
    t = _first_text(d.parsed.page.objects)
    d.mirror_objects([t])
    mirrored = [GP.decode(p) for p in d.parsed.page.objects]
    from ctgedit.format.element import open_element

    want0 = [
        GP.decode(p)
        for p in open_element(
            fxinstall.oracle("chain4", "mirror_resize", "n0_mirrored/library.ctm"), "n0"
        ).tree
    ]
    # (the element window holds the records under the element's root)
    assert [_key(r) for r in mirrored] == [_key(r) for r in want0[1:]]
    d.set_object_rect(t, (10, 10, 128, 42))
    d.save()
    libraries_changed()
    got = [GP.decode(p) for p in open_element(lib, "n0").tree]
    want = [
        GP.decode(p)
        for p in open_element(
            fxinstall.oracle("chain4", "mirror_resize", "n0_resized/library.ctm"), "n0"
        ).tree
    ]
    assert [_key(r) for r in got] == [_key(r) for r in want]
    assert G.parse_text_tail(open_element(lib, "n0").tree[t + 1])["size"] == 11
    n1 = ElementDocument.open_element(lib, "n1")
    assert n1.nested_genie_changes()
    n1.update_nested_genies()
    n1.save()
    libraries_changed()
    got1 = [GP.decode(p) for p in open_element(lib, "n1").tree]
    want1 = [
        GP.decode(p)
        for p in open_element(
            fxinstall.oracle("chain4", "mirror_resize", "after_resize/n1/library.ctm"), "n1"
        ).tree
    ]
    assert [_key(r) for r in got1] == [_key(r) for r in want1]
    assert got1[3].rect1 == (-32, 40, 128, 72) and got1[3].anchor == (128, 66)


def test_a_mirrored_text_resized_unevenly_lands_in_the_box_asked_for(fx_install):
    """n0's text mirrored, resized to (10, 10, 140, 40): the box is the one asked for, the
    anchor the echo through the new Total, the font kept; the save and a reopen keep it,
    and n1's copy follows."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format.element import open_element

    lib = _lib(fx_install)
    d = ElementDocument.open_element(lib, "n0")
    t = _first_text(d.parsed.page.objects)
    d.mirror_objects([t])
    echo = tuple(G.parse_text_tail(d.parsed.page.objects[t])["echo"])
    d.set_object_rect(t, (10, 10, 140, 40))
    po = d.parsed.page.objects[t]
    r = GP.decode(po)
    assert r.rect1 == (10, 10, 140, 40) and _laid_out(po)
    assert [s[0] for s in r.slots] == [0x0F] * 3
    tail = G.parse_text_tail(po)
    assert tail["size"] == 11 and tuple(tail["echo"]) == echo
    d.save()
    libraries_changed()
    stored = open_element(lib, "n0").tree[t + 1]
    assert GP.decode(stored).rect1 == (10, 10, 140, 40) and _laid_out(stored)
    again = ElementDocument.open_element(lib, "n0")
    assert GP.decode(again.parsed.page.objects[t]).rect1 == (10, 10, 140, 40)
    n1 = ElementDocument.open_element(lib, "n1")
    n1.update_nested_genies()
    n1.save()
    libraries_changed()
    copy = [
        GP.decode(p)
        for p in open_element(lib, "n1").tree
        if p.type_code == 0x05 and P.read_slots(p.body_raw)[0][2][1][0] < 0
    ]
    assert len(copy) == 1
    assert copy[0].rect1[2] == 140 and copy[0].anchor[0] == 140  # (its right edge, as n0's)


# ------------------------------------------------------------- a group with a mirrored text
def _mirrored_group(doc, at=(100, 100)):
    """A text mirrored and a rectangle under it, grouped: (group index, text index, the
    text's box and matrix before)."""
    x, y = at
    doc.create_object("text", (x, y, x + 80, y + 16), text="Mirror grp")
    t = len(doc.parsed.page.objects) - 1
    doc.mirror_objects([t])
    before = (GP.decode(doc.parsed.page.objects[t]).rect1, _total_of(doc.parsed.page.objects[t]))
    r = doc.create_object("rectangle", rect=(x, y + 40, x + 100, y + 80))
    g = doc.group_objects([t, r])
    objs = doc.parsed.page.objects
    if not isinstance(g, int):
        g = next(k for k, o in enumerate(objs) if o.type_code == 0x08 and o.class_flag == 2)
    t = next(k for k in range(g, len(objs)) if objs[k].type_code == 0x05)
    return g, t, before


def test_a_scaled_group_keeps_its_mirrored_texts_scale_in_the_matrix(fx_install):
    """A scaled group keeps its mirrored text's scale in the matrix."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    g, t, (box0, m0) = _mirrored_group(doc)
    tail0 = G.parse_text_tail(doc.parsed.page.objects[t])
    l, tt, r, b = GP.decode(doc.parsed.page.objects[g]).rect1
    want = (l, tt, l + 2 * (r - l), tt + 2 * (b - tt))
    doc.set_object_rect(g, want)
    objs = doc.parsed.page.objects
    assert GP.decode(objs[g]).rect1 == want
    po = objs[t]
    text = GP.decode(po)
    tail = G.parse_text_tail(po)
    assert tail["size"] == tail0["size"] == 11 and tail["echo"] == tail0["echo"]
    S = (2.0, 0.0, 0.0, 2.0, l - 2.0 * l, tt - 2.0 * tt)
    m = P.compose_after(m0, 3, S, 0x0F)[0]
    assert text.slots == [(0x0F, m)] * 3
    assert _laid_out(po)
    mapped = (2 * box0[0] + S[4], 2 * box0[1] + S[5], 2 * box0[2] + S[4], 2 * box0[3] + S[5])
    assert text.rect1 == tuple(int(v) for v in mapped)
    assert want[0] <= text.rect1[0] and text.rect1[2] <= want[2]


@pytest.mark.golden
def test_ctdraws_scaled_groups_are_scaled_as_ctdraw_does(fx_install):
    """CtDraw's page of a group with a mirrored text and one with a plain text (the text of
    the first mirrored before the page's first save: its echo is its top left), each scaled
    twice about its top left, and two texts set to 22 pt: the records CtDraw writes (the
    plain text's Bounds as its load + save keeps them)."""
    from ctgedit.editing import EditableDocument

    path = _put(fx_install, fxinstall.oracle_bytes("chain5", "scaled_groups", "grouped/page.ctg"))
    scaled = [GP.decode(p) for p in _read(fxinstall.oracle("chain5", "scaled_groups", "scaled/page.ctg"))]
    reopened = [GP.decode(p) for p in _read(fxinstall.oracle("chain5", "scaled_groups", "reopened/page.ctg"))]
    doc = EditableDocument.open(path)
    doc.set_object_rect(0, (100, 100, 300, 260))
    doc.set_object_rect(3, (400, 100, 600, 260))
    doc.set_font(6, size=22)
    doc.set_font(7, size=22)
    got = [GP.decode(p) for p in doc.parsed.page.objects]
    for k in (0, 1, 2, 6, 7):
        assert _key(got[k]) == _key(scaled[k]), k
    for k in (3, 4, 5):
        assert _key(got[k]) == _key(reopened[k]), k
    assert G.parse_text_tail(doc.parsed.page.objects[1])["size"] == 11


# --------------------------------------------- a resized mirrored text drawn, mirrored back
def _resized_mirror(doc):
    """A text mirrored and resized to twice its box about its top left: (index, the box)."""
    doc.create_object("text", (100, 100, 180, 116), text="Mirror me")
    t = len(doc.parsed.page.objects) - 1
    doc.mirror_objects([t])
    l, tt, r, b = GP.decode(doc.parsed.page.objects[t]).rect1
    want = (l, tt, l + 2 * (r - l), tt + 2 * (b - tt))
    doc.set_object_rect(t, want)
    return t, want


def test_a_resized_mirrored_text_draws_its_font_scaled(fx_install):
    """The page model carries the matrix's scale for the scene: the 11 pt text resized to
    twice its box is drawn at twice its size."""
    from PySide6 import QtGui, QtWidgets
    from ctgedit.editing import EditableDocument
    from ctgedit.render.scene import PageScene

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    doc = EditableDocument.open(fx_install.page("FxShapes"))
    t, _want = _resized_mirror(doc)
    obj = doc.page.objects[t]
    assert obj.raw.get("_text_scale") == (2.0, 2.0)
    item = QtWidgets.QGraphicsSimpleTextItem("Mirror me")
    item.setFont(QtGui.QFont("Arial", 11))
    g = obj.geometry
    assert PageScene.layout_text(item, obj, g.x, g.y, g.extent_x, g.extent_y)
    drawn = item.mapRectToScene(item.boundingRect())
    assert abs(drawn.width() - 2 * item.boundingRect().width()) < 1e-6
    assert abs(drawn.center().x() - (g.x + g.extent_x / 2.0)) < 1e-6


def test_a_placement_draws_the_genies_resized_mirrored_text_scaled(fx_install):
    """n0's text mirrored and resized to twice its box, n0 placed on a page: the member is
    drawn through its Total, the point size its twin's (11) and the scale the Total's (2)."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed

    d = ElementDocument.open_element(_lib(fx_install), "n0")
    t = _first_text(d.parsed.page.objects)
    d.mirror_objects([t])
    d.set_object_rect(t, (10, 10, 128, 42))
    d.save()
    libraries_changed()
    doc = EditableDocument.open(fx_install.page("FxShapes"))
    n = len(doc.parsed.page.objects)
    doc.place_library_element("fxnest", "n0", "ctm", (400, 300), params={"Label": "Lab"})
    member = next(o for o in doc.page.objects[n:] if o.raw.get("_type_code") == 0x05)
    assert member.raw.get("_text_scale") == (2.0, 2.0)
    assert member.style.font_size == 11


def test_a_resized_mirrored_text_mirrored_back_bakes_its_scale_into_its_font(fx_install):
    """A resized mirrored text mirrored back bakes its scale into its font."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    t, box = _resized_mirror(doc)
    doc.mirror_objects([t])
    po = doc.parsed.page.objects[t]
    r = GP.decode(po)
    tail = G.parse_text_tail(po)
    assert [s[0] for s in r.slots] == [0, 0, 0]
    assert tail["size"] == 21 and tuple(tail["echo"]) == r.anchor
    assert G.text_total(po) is None
    assert r.anchor[0] == r.rect1[0] == box[0]


def test_a_resized_mirrored_text_moves_with_its_matrix(fx_install):
    """A move keeps the font and the echo; the anchor is the echo through the moved Total."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    t, want = _resized_mirror(doc)
    before = GP.decode(doc.parsed.page.objects[t])
    assert before.rect1 == want
    echo = tuple(G.parse_text_tail(doc.parsed.page.objects[t])["echo"])
    doc.move_objects([t], 7, 3)
    po = doc.parsed.page.objects[t]
    r = GP.decode(po)
    assert r.rect1 == tuple(v + d for v, d in zip(before.rect1, (7, 3, 7, 3)))
    assert tuple(G.parse_text_tail(po)["echo"]) == echo and _laid_out(po)
    assert G.parse_text_tail(po)["size"] == 11


# ------------------------------------------------------ Q5-a: the leading blank's width
def _boxes(objs, lo):
    return [(o.type_code, GP.decode(o).rect1) for o in objs[lo:] if o.type_code in (0x05, 0x08)]


def test_a_value_trimmed_of_its_blank_keeps_the_box_measured_with_it(fx_install):
    """n0 placed with Label ' L1' stores 'L1' in a box measured with the blank (CtDraw,
    chain4/label_space: (200, 200, 220, 216)); the save keeps it. So does a
    member with nothing placed on it: n1 placed with Name '' derives ' L1' for n0's text."""
    from ctgedit.editing import EditableDocument

    page = fx_install.page("FxShapes")
    doc = EditableDocument.open(page)
    n = len(doc.parsed.page.objects)
    doc.place_library_element("fxnest", "n0", "ctm", (200, 200), params={"Label": " L1"})
    m = len(doc.parsed.page.objects)
    doc.place_library_element("fxnest", "n1", "ctm", (400, 300), params={"Name": ""})
    placed = _boxes(doc.parsed.page.objects, n)
    assert placed[:2] == [(0x08, (200, 200, 220, 216)), (0x05, (200, 200, 220, 216))]
    assert GP.decode(doc.parsed.page.objects[m + 3]).rect1 == (400, 330, 420, 346)
    doc.save()
    assert _boxes(_read(page), n) == placed
    EditableDocument.open(page).save()
    assert _boxes(_read(page), n) == placed


@pytest.mark.golden
@pytest.mark.parametrize("stage", ["placed", "reopened"])
def test_ctdraws_page_with_the_trimmed_value_keeps_its_box(fx_install, stage):
    """CtDraw's own page (placed, then its load + save in a fresh Graphics Builder) opened
    and saved: the records CtDraw writes."""
    from ctgedit.editing import EditableDocument

    data = fxinstall.oracle_bytes("chain4", "label_space", "%s/page.ctg" % stage)
    path = _put(fx_install, data, "FxOraclePlace")
    want = [GP.decode(p) for p in _read(path)]
    EditableDocument.open(path).save()
    got = [GP.decode(p) for p in _read(path)]
    assert [_key(r) for r in got] == [_key(r) for r in want]
    assert got[1].rect1 == (200, 200, 220, 216)


@pytest.mark.golden
def test_ctdraws_placement_with_a_leading_blank_is_its_record(fx_install):
    from ctgedit.editing import EditableDocument

    path = _put(fx_install, fxinstall.oracle_bytes("chain4", "label_space", "input.ctg"), "FxOraclePlace")
    doc = EditableDocument.open(path)
    doc.place_library_element("fxnest", "n0", "ctm", (200, 200), params={"Label": " L1"})
    doc.save()
    want = [GP.decode(p) for p in _read(fxinstall.oracle("chain4", "label_space", "placed/page.ctg"))]
    assert [_key(r) for r in [GP.decode(p) for p in _read(path)]] == [_key(r) for r in want]


def test_a_member_without_a_placed_level_keeps_the_width_through_the_page_save(fx_install):
    """A genie without parameters holding n2 placed with nothing bound (n0's text derives
    ' L1', stored 'L1'), placed on a page: its members carry no placed level, and the page's
    save keeps the box measured with the blank."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed

    lib = _lib(fx_install)
    d = ElementDocument.new_element(lib, "nplain", "genie", canvas=(300, 200))
    d.place_library_element("fxnest", "n2", "ctm", (100, 100), params={})
    d.save()
    libraries_changed()
    page = fx_install.page("FxShapes")
    doc = EditableDocument.open(page)
    n = len(doc.parsed.page.objects)
    doc.place_library_element("fxnest", "nplain", "ctm", (300, 300), params={})

    def trimmed(objs):
        return [
            GP.decode(o).rect1
            for o in objs[n:]
            if o.type_code == 0x05 and dict(GP.decode(o).entries[0][1].pairs).get("TXT_OFF") == "L1"
        ]

    placed = trimmed(doc.parsed.page.objects)
    assert placed == [(300, 360, 320, 376)]
    doc.save()
    assert trimmed(_read(page)) == placed


def test_a_template_copy_trimmed_of_its_blank_keeps_the_width_on_its_pages(fx_install):
    """A template holding n2 placed with nothing bound (n0's text derives ' L1', stored
    'L1'): the template's save and a page built from it keep the box measured with the
    blank (a guard: the element save and the template's composition keep the box the
    placement measured)."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import template_place as TP

    path = fx_install.file("FxStyle", "fx_alt.ctt")
    d = ElementDocument.new_element(path, "nest@-XGA", "template", page_size=(1024, 768))
    d.place_library_element("fxnest", "n2", "ctm", (100, 100), params={})
    objs = d.parsed.page.objects
    k = next(
        j
        for j, o in enumerate(objs)
        if o.type_code == 0x05 and dict(GP.decode(o).entries[0][1].pairs).get("TXT_OFF") == "L1"
    )
    box = GP.decode(objs[k]).rect1
    assert box[2] - box[0] == 20
    d.save()
    libraries_changed()
    name = d.element_ref[1]
    stored = ElementDocument.open_element(path, name).parsed.page.objects
    assert GP.decode(stored[k]).rect1 == box
    pre = TP.compose_prefix(
        TP.load_template(path, name), link_text="fx_alt.%s.<ctt" % name, fresh=True, measure_texts=True
    )
    got = [
        GP.decode(p)
        for p in pre
        if p.type_code == 0x05 and dict(GP.decode(p).entries[0][1].pairs).get("TXT_OFF") == "L1"
    ]
    assert len(got) == 1 and got[0].rect1[2] - got[0].rect1[0] == 20


# --------------------------------------- Q5-a: a value of blanks alone, a string with no line
def _placed_boxes(objs, lo):
    """(type code, anchor, rect1, rect2) of the genie root and the texts from ``lo``."""
    return [
        (o.type_code, GP.decode(o).anchor, GP.decode(o).rect1, GP.decode(o).rect2)
        for o in objs[lo:]
        if o.type_code in (0x05, 0x08)
    ]


def test_a_value_of_blanks_alone_keeps_the_width_of_its_blanks(fx_install):
    """n0 placed with Label '  ' stores '' in a box measured with the two blanks, 8 px wide,
    as ' L1' keeps the blank's width (chain4/label_space)."""
    from ctgedit.editing import EditableDocument

    page = fx_install.page("FxShapes")
    doc = EditableDocument.open(page)
    n = len(doc.parsed.page.objects)
    doc.place_library_element("fxnest", "n0", "ctm", (200, 200), params={"Label": "  "})
    placed = _placed_boxes(doc.parsed.page.objects, n)
    assert [b[2] for b in placed] == [(200, 200, 208, 216)] * 2
    assert GP.decode(doc.parsed.page.objects[n + 1]).entries[0][1].get("TXT_OFF") == ""
    doc.save()
    assert _placed_boxes(_read(page), n) == placed
    EditableDocument.open(page).save()
    assert _placed_boxes(_read(page), n) == placed


def test_a_caption_with_no_line_to_measure_keeps_its_zero_box_through_the_save(fx_install):
    """A caption with no line to measure keeps its zero box through the save."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed

    lib = _lib(fx_install)
    d = ElementDocument.new_element(lib, "nhdr", "genie", canvas=(300, 200))
    d.create_object("text", (10, 10, 80, 26), text="%Header%\r\n")
    d.create_object("rectangle", rect=(10, 40, 60, 60))
    d.save()
    libraries_changed()
    page = fx_install.page("FxShapes")
    doc = EditableDocument.open(page)
    n = len(doc.parsed.page.objects)
    doc.place_library_element("fxnest", "nhdr", "ctm", (400, 300), params={"Header": ""})
    placed = _placed_boxes(doc.parsed.page.objects, n)
    total = P.read_slots(doc.parsed.page.objects[n + 1].body_raw)[0][2][1]
    h, k = int(total[4]), int(total[5])
    assert placed[1][2] == placed[1][3] == (h, k, h, k)
    assert placed[0][2][:2] == (h, k)
    doc.save()
    assert _placed_boxes(_read(page), n) == placed
    EditableDocument.open(page).save()
    assert _placed_boxes(_read(page), n) == placed


def test_a_value_of_line_breaks_alone_is_the_zero_box_at_its_total(fx_install):
    """n0 placed with Label CR LF: its text measures no line, so it is the zero box at its
    Total's origin and the genie's box is that point, as for a multi-line caption trimmed to
    ''."""
    from ctgedit.editing import EditableDocument

    page = fx_install.page("FxShapes")
    doc = EditableDocument.open(page)
    n = len(doc.parsed.page.objects)
    doc.place_library_element("fxnest", "n0", "ctm", (400, 300), params={"Label": "\r\n"})
    placed = _placed_boxes(doc.parsed.page.objects, n)
    total = P.read_slots(doc.parsed.page.objects[n + 1].body_raw)[0][2][1]
    h, k = int(total[4]), int(total[5])
    assert (h, k) != (0, 0)
    assert [b[2] for b in placed] == [(h, k, h, k)] * 2
    assert placed[0][1] == (h, k - 1)
    doc.save()
    assert _placed_boxes(_read(page), n) == placed
