"""Text geometry after uneven resizes and turns."""

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

import fxinstall  # noqa: E402

from ctgedit.format import genie_place as GP  # noqa: E402
from ctgedit.format import geometry as G  # noqa: E402
from ctgedit.format import placement as P  # noqa: E402

#: the chain6/text_resize texts: (index, x factor, y factor); 2 and 3 are mirrored first
FACTORS = ((0, 2.0, 1.0), (1, 1.0, 2.0), (2, 2.0, 2.0), (3, 2.0, 1.0))
MIRRORED = (2, 3)


def _read(path_or_bytes):
    from ctgedit.format.page import parse_ctg_bytes

    data = path_or_bytes
    if isinstance(path_or_bytes, str):
        with open(path_or_bytes, "rb") as fh:
            data = fh.read()
    return parse_ctg_bytes(data).page.objects


def _put(fx, data: bytes, name: str = "FxOracleDraw") -> str:
    path = os.path.join(fx.project("FxMain"), name + ".ctg")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _slots(po):
    """The slots as stored, the sign of every zero included."""
    return [(m, tuple(repr(v) for v in x)) for m, x, _o, _n in P.read_slots(po.body_raw)[0]]


def _text(po):
    r = GP.decode(po)
    t = G.parse_text_tail(po)
    return (r.anchor, r.rect1, r.rect2, t["size"], t["orient"], tuple(t["echo"]), _slots(po))


def _stage(stage):
    return _read(fxinstall.oracle_bytes("chain6", "text_resize", "%s/page.ctg" % stage))


def _resize_by(doc, factors):
    objs = doc.parsed.page.objects
    for k, sx, sy in factors:
        l, t, r, b = G.parse_geom_header(objs[k].body_raw).rect1
        doc.set_object_rect(k, (l, t, l + int(round(sx * (r - l))), t + int(round(sy * (b - t)))))


def _new_text(doc, text="Plain me", at=(100, 100)):
    x, y = at
    doc.create_object("text", (x, y, x + 60, y + 16), text=text)
    return len(doc.parsed.page.objects) - 1


# ------------------------------------------------------------------- Q17: chain6/text_resize
@pytest.mark.golden
def test_ctdraws_resize_session_gives_its_records_byte_for_byte(fx_install):
    """CtDraw's drawn page: 'Mirror back' and 'Mirror wide' mirrored, every text scaled about
    its top left by its factors, the mirrored ones mirrored back, each step saved, and a
    reopen and save: every stage is CtDraw's page record for record and byte for byte (the
    -0.0 of the mirrored texts' C after the scale, +0.0 after the mirror back)."""
    from ctgedit.editing import EditableDocument

    path = _put(fx_install, fxinstall.oracle_bytes("chain6", "text_resize", "drawn/page.ctg"))
    doc = EditableDocument.open(path)
    for k in MIRRORED:
        doc.mirror_objects([k], horizontal=True)
    doc.save()
    steps = [("mirrored", None)]
    _resize_by(doc, FACTORS)
    doc.save()
    steps.append(("resized", None))
    got_resized = _read(path)
    for k in MIRRORED:
        doc.mirror_objects([k], horizontal=True)
    doc.save()
    got_back = _read(path)
    EditableDocument.open(path).save()
    for stage, got in (("resized", got_resized), ("mirrored_back", got_back), ("reopened", _read(path))):
        want = _stage(stage)
        assert len(got) == len(want) == 4
        for k in range(4):
            assert _text(got[k]) == _text(want[k]), (stage, k)
            assert got[k].body_raw == want[k].body_raw, (stage, k)
    assert all(math.copysign(1.0, s[1][2]) == -1.0 for s in P.read_slots(got_resized[2].body_raw)[0])


def test_an_uneven_resize_of_a_plain_text_keeps_a_matrix(fx_install):
    """An uneven resize of a plain text keeps a matrix."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    k = _new_text(doc)
    before = GP.decode(doc.parsed.page.objects[k])
    tail0 = G.parse_text_tail(doc.parsed.page.objects[k])
    l, t, r, b = before.rect1
    want = (l, t, l + 2 * (r - l), b)
    doc.set_object_rect(k, want)
    po = doc.parsed.page.objects[k]
    got = GP.decode(po)
    tail = G.parse_text_tail(po)
    assert tail["size"] == tail0["size"] and tail["echo"] == tail0["echo"]
    m = (2.0, 0.0, 0.0, 1.0, float(l - 2 * l), 0.0)
    assert got.slots == [(0x0F, m)] * 3
    assert all(math.copysign(1.0, v) == 1.0 for s in got.slots for v in s[1][1:3])
    assert got.rect1 == want and got.anchor == tuple(tail0["echo"])
    assert doc.page.objects[k].raw.get("_text_scale") == (2.0, 1.0)
    doc.set_object_rect(k, (l, t, l + (r - l), t + 2 * (b - t)))
    po = doc.parsed.page.objects[k]
    assert [s[0] for s in GP.decode(po).slots] == [0x0F] * 3  # 1 x 2 now: still uneven
    assert G.parse_text_tail(po)["size"] == tail0["size"]


def test_an_uneven_scale_brought_back_to_the_text_bakes_it_again(fx_install):
    """A plain text stretched 2 x 1 and resized back to its own box: the matrix becomes
    uniform with no flip and is baked (the slots the identity, the echo its anchor)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    k = _new_text(doc)
    box = GP.decode(doc.parsed.page.objects[k]).rect1
    l, t, r, b = box
    doc.set_object_rect(k, (l, t, l + 2 * (r - l), b))
    assert G.text_keeps_matrix(doc.parsed.page.objects[k])
    doc.set_object_rect(k, box)
    po = doc.parsed.page.objects[k]
    got = GP.decode(po)
    assert [s[0] for s in got.slots] == [0, 0, 0] and not G.text_keeps_matrix(po)
    assert got.rect1 == box and tuple(G.parse_text_tail(po)["echo"]) == got.anchor


def test_a_wide_text_takes_a_small_stretch_as_uniform():
    """CtDraw's rule: uniform while the box's aspect changes by less than 0.05. A text
    box 63 x 16 stretched 1.1 x 1 is uniform (0.254 to 0.231), 2 x 1 is not; a tall box 16 x
    63 stretched 1.02 x 1 is not (3.94 to 3.86); a mirror never is."""
    wide, tall = (100, 100, 163, 116), (100, 100, 116, 163)
    assert G.text_transform_bakes((1.1, 0.0, 0.0, 1.0, 0.0, 0.0), wide)
    assert not G.text_transform_bakes((2.0, 0.0, 0.0, 1.0, 0.0, 0.0), wide)
    assert not G.text_transform_bakes((1.02, 0.0, 0.0, 1.0, 0.0, 0.0), tall)
    assert G.text_transform_bakes((2.0, 0.0, 0.0, 2.0, 0.0, 0.0), tall)
    assert not G.text_transform_bakes((-2.0, 0.0, 0.0, 2.0, 0.0, 0.0), wide)


def test_a_mirrored_text_stretched_a_little_and_mirrored_back_is_baked(fx_install):
    """A mirrored text stretched 1.1 x 1, then mirrored back: the matrix left (1.1, 0, 0, 1)
    is uniform for its wide box, so CtDraw bakes it (its height's scale, 1: the font stays)
    and drops the matrix."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    k = _new_text(doc, "Mirror me")
    size = G.parse_text_tail(doc.parsed.page.objects[k])["size"]
    doc.mirror_objects([k])
    l, t, r, b = GP.decode(doc.parsed.page.objects[k]).rect1
    doc.set_object_rect(k, (l, t, l + int(round(1.1 * (r - l))), b))
    assert G.text_keeps_matrix(doc.parsed.page.objects[k])
    doc.mirror_objects([k])
    po = doc.parsed.page.objects[k]
    assert G.text_total(po) is None and [s[0] for s in GP.decode(po).slots] == [0, 0, 0]
    assert G.parse_text_tail(po)["size"] == size


def test_a_group_scaled_unevenly_keeps_its_plain_texts_scale_in_a_matrix(fx_install):
    """A plain group of a text and a rectangle scaled 2 x 1: the text keeps the group's
    scale as a matrix, its font kept, its box the old one mapped. Scaled 2 x 2 it is baked,
    as CtDraw does (chain5/scaled_groups)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    t = _new_text(doc, "Group me", (100, 100))
    box = GP.decode(doc.parsed.page.objects[t]).rect1
    size = G.parse_text_tail(doc.parsed.page.objects[t])["size"]
    r = doc.create_object("rectangle", rect=(100, 140, 200, 180))
    g = doc.group_objects([t, r])
    objs = doc.parsed.page.objects
    if not isinstance(g, int):
        g = next(k for k, o in enumerate(objs) if o.type_code == 0x08 and o.class_flag == 2)
    t = next(k for k in range(g, len(objs)) if objs[k].type_code == 0x05)
    l, tt, rr, b = GP.decode(objs[g]).rect1
    doc.set_object_rect(g, (l, tt, l + 2 * (rr - l), b))
    po = doc.parsed.page.objects[t]
    got = GP.decode(po)
    assert [s[0] for s in got.slots] == [0x0F] * 3
    assert got.slots[2][1][:4] == (2.0, 0.0, 0.0, 1.0)
    assert G.parse_text_tail(po)["size"] == size
    assert got.rect1 == (2 * box[0] - l, box[1], 2 * box[2] - l, box[3])


def test_a_text_turned_and_resized_unevenly_lands_in_the_box_asked_for(fx_install):
    """A text turned (its turn held in memory, the page storing it as the orient) and resized
    to a wide, low box: the turn goes into the matrix the resize keeps (the orient back to
    1, the echo its old one, the font kept) and the text fills the box asked for."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    k = _new_text(doc, "Turn me", (10, 10))
    tail0 = G.parse_text_tail(doc.parsed.page.objects[k])
    doc.rotate_objects([k])
    assert G.parse_text_tail(doc.parsed.page.objects[k])["orient"] == 3
    doc.set_object_rect(k, (10, 10, 140, 40))
    po = doc.parsed.page.objects[k]
    got = GP.decode(po)
    tail = G.parse_text_tail(po)
    assert got.rect1 == (10, 10, 140, 40)
    assert tail["orient"] == 1 and tail["size"] == tail0["size"]
    assert tuple(tail["echo"]) == tuple(tail0["echo"])
    a, b, c, d = got.slots[2][1][:4]
    assert abs(a) < 1e-9 and abs(d) < 1e-9 and b > 0 > c  # the turn, scaled
    o = doc.page.objects[k]
    assert (o.geometry.x, o.geometry.y, o.geometry.extent_x, o.geometry.extent_y) == (10, 10, 130, 30)


def test_a_turned_texts_resize_kept_as_a_matrix_is_not_baked_by_a_second_decision(fx_install):
    """A turned text's resize kept as a matrix isn't baked by a second decision."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    k = _new_text(doc, "Turn me", (10, 10))
    size = G.parse_text_tail(doc.parsed.page.objects[k])["size"]
    doc.rotate_objects([k])
    assert GP.decode(doc.parsed.page.objects[k]).rect1 == (27, -8, 44, 46)
    for rect in ((27, -8, 39, 39), (27, -8, 42, 50)):
        doc.set_object_rect(k, rect)
        po = doc.parsed.page.objects[k]
        got = GP.decode(po)
        tail = G.parse_text_tail(po)
        a, b, c, d = got.slots[2][1][:4]
        assert got.rect1 == rect and tail["size"] == size and tail["orient"] == 1
        assert abs(a) < 1e-9 and abs(d) < 1e-9 and b > 0 > c  # the turn, scaled
        assert doc.page.objects[k].raw.get("_local_xform") == (0, 1, -1, 0)
        doc.undo()


def test_a_text_resized_to_no_width_or_height_keeps_no_singular_matrix(fx_install):
    """A text resized to no width or height keeps no singular matrix."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    k = _new_text(doc)
    box = GP.decode(doc.parsed.page.objects[k]).rect1
    size = G.parse_text_tail(doc.parsed.page.objects[k])["size"]
    l, t, r, b = box

    def regular(po):
        return all(abs(x[0] * x[3] - x[1] * x[2]) > 1e-9 for _m, x in GP.decode(po).slots)

    for rect in ((l, t, l, b), (l, t, r, t)):
        doc.set_object_rect(k, rect)
        po = doc.parsed.page.objects[k]
        assert regular(po) and G.parse_text_tail(po)["size"] == size
        doc.set_object_rect(k, (l, t, l + 2 * (r - l), b))
        assert GP.decode(doc.parsed.page.objects[k]).rect1 == (l, t, l + 2 * (r - l), b)
        doc.set_object_rect(k, box)
        po = doc.parsed.page.objects[k]
        assert GP.decode(po).rect1 == box and not G.text_keeps_matrix(po)
    t2 = _new_text(doc, "Group me", (300, 300))
    rk = doc.create_object("rectangle", rect=(300, 340, 400, 380))
    g = doc.group_objects([t2, rk])
    objs = doc.parsed.page.objects
    t2 = next(i for i in (g + 1, g + 2) if objs[i].type_code == 0x05)
    gl, gt, _gr, gb = GP.decode(objs[g]).rect1
    doc.set_object_rect(g, (gl, gt, gl, gb))
    po = doc.parsed.page.objects[t2]
    assert regular(po) and G.parse_text_tail(po)["size"] == size


def test_a_saved_turned_text_resized_unevenly_keeps_its_orient_and_lands_in_the_box(fx_install):
    """The same text turned, saved and reopened (the turn is its orient now) and resized
    unevenly: the scale is kept as a matrix over the orient, the box the one asked for."""
    from ctgedit.editing import EditableDocument

    page = fx_install.page("FxShapes")
    doc = EditableDocument.open(page)
    k = _new_text(doc, "Turn me", (10, 10))
    size = G.parse_text_tail(doc.parsed.page.objects[k])["size"]
    doc.rotate_objects([k])
    doc.save()
    doc = EditableDocument.open(page)
    l, t, r, b = GP.decode(doc.parsed.page.objects[k]).rect1
    want = (l, t, l + 3 * (r - l), t + (b - t) // 2)
    doc.set_object_rect(k, want)
    po = doc.parsed.page.objects[k]
    got = GP.decode(po)
    tail = G.parse_text_tail(po)
    assert tail["orient"] == 3 and tail["size"] == size
    assert got.slots[2][0] == 0x0F and got.slots[2][1][1:3] == (0.0, 0.0)
    assert got.rect1 == want


def test_a_turned_texts_matrix_is_drawn_after_its_turn():
    """The scene draws a text whose page stores its turn as the orient through its matrix
    in page axes, after the turn: a vertical text mirrored is drawn mirrored, and one
    scaled 2 x 1 is twice as wide across the page."""
    from PySide6 import QtCore, QtGui, QtWidgets
    from ctgedit.render.scene import PageScene

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class _Obj:
        def __init__(self, raw):
            self.raw = raw

    def drawn(raw):
        item = QtWidgets.QGraphicsSimpleTextItem("Turn me")
        item.setFont(QtGui.QFont("Arial", 11))
        assert PageScene._layout_text(item, _Obj(raw), 0, 0, 100, 100)
        return item

    mirrored = drawn({"_text_turns": 1, "_local_xform": (-1.0, 0.0, 0.0, 1.0)})
    o = mirrored.mapToScene(QtCore.QPointF(0, 0))
    down = mirrored.mapToScene(QtCore.QPointF(0, 1)) - o
    assert abs(down.x() - 1.0) < 1e-9 and abs(down.y()) < 1e-9  # turned, then mirrored
    plain = drawn({"_text_turns": 1})
    down = plain.mapToScene(QtCore.QPointF(0, 1)) - plain.mapToScene(QtCore.QPointF(0, 0))
    assert abs(down.x() + 1.0) < 1e-9
    wide = drawn({"_text_turns": 1, "_text_scale": (2.0, 1.0)})
    br = wide.boundingRect()
    box = wide.mapRectToScene(br)
    assert abs(box.width() - 2 * br.height()) < 1e-6 and abs(box.height() - br.width()) < 1e-6


def test_with_stretching_off_an_uneven_resize_goes_into_the_point_size(fx_install, monkeypatch):
    """With stretching off, an uneven resize goes into the point size."""
    from ctgedit.doc.geometry import GeometryEdits
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.prefs import settings as S

    assert S.OPTION_DEFAULTS["stretch_texts"] is True and GeometryEdits.STRETCH_TEXTS is True
    S.app_settings().setValue("opt/stretch_texts", False)
    try:
        S.apply_drawing_options()
        assert GeometryEdits.STRETCH_TEXTS is False
        doc = EditableDocument.open(fx_install.page("FxShapes"))
        k = _new_text(doc)
        l, t, r, b = GP.decode(doc.parsed.page.objects[k]).rect1
        doc.set_object_rect(k, (l, t, l + 3 * (r - l), t + 2 * (b - t)))
        po = doc.parsed.page.objects[k]
        assert not G.text_keeps_matrix(po) and G.parse_text_tail(po)["size"] == 21
        m = _new_text(doc, "Mirror me", (100, 300))
        doc.mirror_objects([m])
        mirrored = GP.decode(doc.parsed.page.objects[m])
        l, t, r, b = mirrored.rect1
        doc.set_object_rect(m, (l, t, l + 3 * (r - l), b))
        po = doc.parsed.page.objects[m]
        got = GP.decode(po)
        assert G.text_keeps_matrix(po) and G.text_scale(po) is None
        assert got.slots[2][1][:4] == mirrored.slots[2][1][:4]
        assert G.parse_text_tail(po)["size"] == 11 and got.rect1[2] - got.rect1[0] == r - l
        doc.undo()
        doc.set_object_rect(m, (l, t, l + 3 * (r - l), t + 2 * (b - t)))
        po = doc.parsed.page.objects[m]
        assert G.text_scale(po) is None and G.parse_text_tail(po)["size"] == 21
        g = _new_text(doc, "Group me", (300, 500))
        rk = doc.create_object("rectangle", rect=(300, 540, 400, 580))
        doc.mirror_objects([g])
        gi = doc.group_objects([g, rk])
        objs = doc.parsed.page.objects
        ti = next(i for i in (gi + 1, gi + 2) if objs[i].type_code == 0x05)
        assert G.text_keeps_matrix(objs[ti])
        gl, gt, gr, gb = GP.decode(objs[gi]).rect1
        doc.set_object_rect(gi, (gl, gt, gl + 3 * (gr - gl), gb))
        po = doc.parsed.page.objects[ti]
        assert G.text_keeps_matrix(po) and G.text_scale(po) is None
        assert G.parse_text_tail(po)["size"] == 11
    finally:
        S.app_settings().remove("opt/stretch_texts")
        S.apply_drawing_options()
    assert GeometryEdits.STRETCH_TEXTS is True


def test_a_new_text_in_a_box_takes_its_height_never_a_matrix(fx_install):
    """A new text drawn in a box wider than its caption: its point size follows the box's
    height and it keeps no matrix (the creation is no resize)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    doc.create_object("text", (100, 100, 400, 116), text="Short")
    po = doc.parsed.page.objects[-1]
    assert not G.text_keeps_matrix(po) and [s[0] for s in GP.decode(po).slots] == [0, 0, 0]


# -------------------------------------------------------------------- a template's text boxes
def _outer_lib(fx):
    from ctgedit.editing import libraries_changed

    lib = os.path.join(fx.project("FxStyle"), "fx_style.ctt")
    with open(lib, "wb") as fh:
        fh.write(fxinstall.oracle_bytes("chain4", "outer_param", "param_added/library.ctt"))
    libraries_changed()
    return lib


def _find(objs, word):
    for j, po in enumerate(objs):
        r = GP.decode(po)
        if (
            po.type_code == 0x05
            and r.entries
            and word in ((r.entries[0][2].get("TXT_OFF") or "") + (r.entries[0][1].get("TXT_OFF") or ""))
        ):
            return j, r
    raise AssertionError(word)


def test_an_outer_templates_copy_of_a_mirrored_title_keeps_its_box(fx_install):
    """normal@XGA's title mirrored and saved: the chain saves fxouter@XGA, whose copy of the
    title has the template's box (8, 7, 50, 23), not one measured at its anchor."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import element as E

    lib = _outer_lib(fx_install)
    d = ElementDocument.open_element(lib, "normal@XGA")
    j, _r = _find(d.parsed.page.objects, "FxTitle")
    d.mirror_objects([j])
    d.save()
    libraries_changed()
    inner = _find(list(E.open_element(lib, "normal@XGA").tree), "FxTitle")[1]
    res = GW.update_closed_pages(lib, "normal@XGA")
    libraries_changed()
    assert "fx_style.fxouter@XGA" in (res.get("elements_saved") or [])
    outer = _find(list(E.open_element(lib, "fxouter@XGA").tree), "FxTitle")[1]
    assert inner.rect1 == (8, 7, 50, 23) and inner.anchor == (50, 20)
    assert outer.rect1 == inner.rect1 and outer.anchor == inner.anchor
    w = ElementDocument.open_element(lib, "fxouter@XGA")
    k = next(
        i
        for i, o in enumerate(w.page.objects)
        if getattr(o, "style", None) is not None and o.style.text == "FxTitle"
    )
    assert w.page.objects[k].geometry.x == 8


def test_a_stored_copy_with_a_mirrored_text_beside_its_box_is_composed_again(fx_install):
    """A copy whose mirrored text was saved beside its box differs in
    content from a fresh one: no load measures a mirrored text again, so Compose Nested
    Genies Again and Used By call it out of date. A
    plain text's box is no reason (it is measured again at every load)."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import copy_compare as CC
    from ctgedit.format import element as E

    lib = _outer_lib(fx_install)
    d = ElementDocument.open_element(lib, "normal@XGA")
    j, _r = _find(d.parsed.page.objects, "FxTitle")
    d.mirror_objects([j])
    d.save()
    libraries_changed()
    GW.update_closed_pages(lib, "normal@XGA")
    libraries_changed()
    tree = list(E.open_element(lib, "fxouter@XGA").tree)
    k, r = _find(tree, "FxTitle")
    po = tree[k]
    r.rect1 = (r.rect1[0] + 42, r.rect1[1], r.rect1[2] + 42, r.rect1[3])
    r.rect2 = (r.rect2[0] + 42, r.rect2[1], r.rect2[2] + 42, r.rect2[3])
    moved = GP.to_po(GP.encode(r))
    assert not CC.same_content(po, moved) and CC.same_content(po, po)
    plain = next(p for p in tree if p.type_code == 0x05 and not CC._own_matrix(GP.decode(p)))
    rp = GP.decode(plain)
    rp.rect1 = (rp.rect1[0], rp.rect1[1], rp.rect1[2] + 9, rp.rect1[3])
    assert CC.same_content(plain, GP.to_po(GP.encode(rp)))


def test_a_stored_copy_with_a_stretched_text_at_another_box_is_composed_again(fx_install):
    """A stored copy with a stretched text at another box is composed again."""
    from ctgedit.format import copy_compare as CC
    from ctgedit.format import element as E

    lib = _outer_lib(fx_install)
    tree = list(E.open_element(lib, "fxouter@XGA").tree)
    po = next(p for p in tree if p.type_code == 0x05 and not CC._own_matrix(GP.decode(p)))
    r = GP.decode(po)
    l = r.rect1[0]
    r.slots = [r.slots[0]] + [(0x0F, (2.0, 0.0, 0.0, 1.0, -float(l), 0.0))] * 2
    stretched = GP.to_po(GP.encode(r))
    assert CC._own_matrix(GP.decode(stretched))
    r.rect1 = (r.rect1[0], r.rect1[1], r.rect1[2] + (r.rect1[2] - r.rect1[0]), r.rect1[3])
    r.rect2 = (r.rect2[0], r.rect2[1], r.rect2[2] + (r.rect2[2] - r.rect2[0]), r.rect2[3])
    wide = GP.to_po(GP.encode(r))
    assert not CC.same_content(stretched, wide) and CC.same_content(stretched, stretched)


def test_a_new_pages_mirrored_parameter_text_reaches_left_of_its_anchor(fx_install):
    """fxouter's own '%P%' text mirrored (box (500, 500, 533, 516), anchor (533, 513)): a New
    Page from it stores that box, and the template input P = 'Hello world' grows it to the
    left of the anchor, (465, 500, 533, 516), as the text is drawn."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import element as E

    lib = _outer_lib(fx_install)
    d = ElementDocument.open_element(lib, "fxouter@XGA")
    j, _r = _find(d.parsed.page.objects, "%P%")
    d.mirror_objects([j])
    d.save()
    libraries_changed()
    stored = _find(list(E.open_element(lib, "fxouter@XGA").tree), "%P%")[1]
    assert stored.rect1 == (500, 500, 533, 516) and stored.anchor == (533, 513)
    p = os.path.join(fx_install.project("FxMain"), "FromOuter.ctg")
    doc = EditableDocument.new_from_template("fx_style.fxouter@XGA.<ctt", p, linked=True)
    assert _find(doc.parsed.page.objects, "%P%")[1].rect1 == (500, 500, 533, 516)
    doc.set_template_inputs({"P": "Hello world"})
    r = _find(doc.parsed.page.objects, "Hello")[1]
    assert r.anchor == (533, 513) and r.rect1 == (465, 500, 533, 516)


@pytest.mark.parametrize("value, width", [(" x", 10), ("  ", 8), ("x", 6)])
def test_a_template_input_is_measured_as_the_save_measures_it(fx_install, value, width):
    """New Page from fxouter@XGA, template input P set: the text's box in the tab is the one
    the save writes, measured with a leading blank (' x': 10 px, LIVE Q5-a,
    chain4/label_space), with the blanks of a value of blanks alone ('  ': 8 px)."""
    from ctgedit.editing import EditableDocument

    p = os.path.join(fx_install.project("FxMain"), "FromOuter.ctg")
    _outer_lib(fx_install)
    doc = EditableDocument.new_from_template("fx_style.fxouter@XGA.<ctt", p, linked=True)
    doc.set_template_inputs({"P": value})
    k, r = next(
        (j, GP.decode(po))
        for j, po in enumerate(doc.parsed.page.objects)
        if po.type_code == 0x05
        and GP.decode(po).entries
        and GP.decode(po).entries[0][2].get("TXT_OFF") == "%P%"
    )
    assert r.rect1 == (500, 500, 500 + width, 516)
    doc.save()
    assert GP.decode(_read(p)[k]).rect1 == r.rect1


def test_a_template_update_measures_a_removed_value_as_the_save_does(fx_install):
    """A New Page from fxouter@XGA keeps '%P%' as typed (33 px); after fxouter changes,
    Update Template removes the value, and the tab's box is the 4 px the save writes (the
    caption with its parameter blanked: one blank)."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed

    lib = _outer_lib(fx_install)
    p = os.path.join(fx_install.project("FxMain"), "FromOuter.ctg")
    doc = EditableDocument.new_from_template("fx_style.fxouter@XGA.<ctt", p, linked=True)

    def ptext(objs):
        return next(
            (j, GP.decode(po))
            for j, po in enumerate(objs)
            if po.type_code == 0x05
            and GP.decode(po).entries
            and GP.decode(po).entries[0][2].get("TXT_OFF") == "%P%"
        )

    assert ptext(doc.parsed.page.objects)[1].rect1 == (500, 500, 533, 516)
    doc.save()
    libraries_changed()
    o = ElementDocument.open_element(lib, "fxouter@XGA")
    o.create_object("rectangle", (600, 600, 650, 650))
    o.save()
    libraries_changed()
    d = EditableDocument.open(p)
    assert d.update_template()
    k, r = ptext(d.parsed.page.objects)
    assert r.entries[0][1].get("TXT_OFF") is None and r.rect1 == (500, 500, 504, 516)
    d.save()
    assert GP.decode(_read(p)[k]).rect1 == r.rect1
