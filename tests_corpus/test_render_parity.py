"""Render parity on the real corpus."""

import os

import pytest
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
from ctgedit.format.cnum import c_atoi


EX = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example"
have_ex = pytest.mark.skipif(not os.path.exists(EX), reason="Example not installed")


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@have_ex
def test_button_caption_paints():
    """A captioned button renders a SimpleTextItem child (the GC-safe item) in a
    dark, readable colour -- not a garbage-collected, near-white QGraphicsTextItem."""
    _app()
    from ctgedit.io.reader import read_page
    from ctgedit.render.scene import PageScene
    from ctgedit.model.document import ObjectKind

    pg = read_page(os.path.join(EX, "!AlarmComments.ctg"))
    sc = PageScene()
    sc.set_page(pg, thumbnail_opacity=0.0)
    # find a non-target button with a caption and assert it has a simple-text child
    found = False
    for i, o in enumerate(pg.objects):
        if o.kind is not ObjectKind.BUTTON:
            continue
        if str((o.raw.get("_props") or {}).get("VIS_MODE", "")).upper() == "TARGET":
            continue
        item = sc._items.get(i)
        if item is None:
            continue
        caps = [c for c in item.childItems() if isinstance(c, QtWidgets.QGraphicsSimpleTextItem)]
        if caps:
            found = True
            lum = caps[0].brush().color().lightness()
            assert lum < 160, "caption must be dark on the light button face"
    assert found, "no captioned button rendered a text child"


@have_ex
def test_splash_art_not_suppressed_by_literal_visible_gate():
    """VISIBLE is HIDDEN-WHEN: a constant VISIBLE='0' ("never hidden") splash
    banner symbol is shown, like CtDraw draws it."""
    _app()
    from ctgedit.io.reader import read_page
    from ctgedit.render.scene import PageScene

    pg = read_page(os.path.join(EX, "!Splash.ctg"))
    art = [
        i
        for i, o in enumerate(pg.objects)
        if o.animations.visibility is not None
        and o.animations.visibility.expression.strip() == "0"
        and (o.children or o.raw.get("_embedded_bmp"))
    ]
    assert art, "expected a VISIBLE=0 object that owns art"
    assert all(pg.objects[i].raw.get("_rest_visible") is True for i in art)
    sc = PageScene()
    sc.set_page(pg, thumbnail_opacity=0.0)
    assert all(i in sc._items for i in art)  # drawn, not skipped


@have_ex
def test_dynamic_string_display_echoes_expression_in_literal_mode():
    """In literal/design-time mode a runtime string display shows '=<cicode>'
    (CtDraw's editor echo), not the generic 'abc' stub."""
    _app()
    from ctgedit.io import reader

    try:
        reader.set_label_mode("literal")
        pg = reader.read_page(os.path.join(EX, "!ServerProperties.ctg"))
        echoed = [o.style.text for o in pg.objects if o.style.text and o.style.text.startswith("=")]
        assert echoed, "expected at least one '=<expr>' echo in literal mode"
        assert not any(o.style.text == "abc" for o in pg.objects)
    finally:
        reader.set_label_mode("native")


@have_ex
def test_processanalyst_control_gets_light_fill_scheduler_does_not():
    """A ProcessAnalyst control gets a light fill; others don't."""
    _app()
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    pa = reader.read_page(os.path.join(EX, "ProcessAnalyst.ctg"))
    sc = PageScene()
    sc.set_page(pa, thumbnail_opacity=0.0)
    pac = next(
        i
        for i, o in enumerate(pa.objects)
        if o.raw.get("_type_code") in (0x0D, 0x13)
        and "PA" in str((o.raw.get("_props") or {}).get("ACTIVEXEXT_*NAME", "")).upper()
    )
    assert sc._items[pac].brush().color().lightness() >= 230  # light fill

    ts = reader.read_page(os.path.join(EX, "!TimeScheduler.ctg"))
    sc2 = PageScene()
    sc2.set_page(ts, thumbnail_opacity=0.0)
    tsc = next(
        i
        for i, o in enumerate(ts.objects)
        if o.raw.get("_type_code") in (0x0D, 0x13)
        and not str((o.raw.get("_props") or {}).get("ACTIVEXEXT_*NAME", ""))
    )
    b = sc2._items[tsc].brush()
    assert b.style() == QtCore.Qt.SolidPattern and b.color().lightness() == 255


@have_ex
def test_3d_framed_rect_gets_bevel_edges():
    """A 3D framed rect gets bevel edges."""
    _app()
    from ctgedit.io import reader
    from ctgedit.format import geometry as G
    from ctgedit.render.scene import PageScene
    from ctgedit.model.document import ObjectKind

    for name in ("Genies.ctg", "DatabaseDisplay.ctg", "!PID.ctg"):
        pg = reader.read_page(os.path.join(EX, name))
        sc = PageScene()
        sc.set_page(pg, thumbnail_opacity=0.0)
        for i, o in enumerate(pg.objects):
            fl = o.style.style_flags
            if (
                o.kind is ObjectKind.RECTANGLE
                and fl
                and not (fl[2] & 0x0F)
                and (fl[0] & 0x0F) in (1, 2, 3, 4)
                and i in sc._items
            ):
                eff = fl[0] & 0x0F
                g = o.geometry
                d = G.effect_depth(
                    o.raw["_type_code"],
                    (
                        int(round(g.x)),
                        int(round(g.y)),
                        int(round(g.x + g.extent_x)),
                        int(round(g.y + g.extent_y)),
                    ),
                    fl,
                )
                behind = QtWidgets.QGraphicsItem.ItemStacksBehindParent
                copies = sorted(
                    (c.pos().x(), c.pos().y(), bool(c.flags() & behind))
                    for c in sc._items[i].childItems()
                    if (c.pos().x(), c.pos().y()) != (0.0, 0.0)
                )
                if eff == 3:
                    assert copies == [(d, d, True)]
                elif eff == 4 and fl[4] == 1:
                    assert copies == [(-d, -d, False)]
                else:
                    assert copies == [(-d, -d, True), (d, d, True)]
                return
    pytest.skip("no unfilled 3D rect found in the sampled pages")


def _wmf(records, window=(0, 0, 1000, 1000)):
    """A memory metafile: MM_ANISOTROPIC + SetWindowOrg / SetWindowExt (x, y, w, h), then
    ``records`` (function, params as i16), black brush + pen selected first."""
    import struct

    ox, oy, ew, eh = window
    recs = [(0x0103, (8,)), (0x020B, (oy, ox)), (0x020C, (eh, ew))]
    body = b""
    for fn, prm in recs:
        body += struct.pack("<IH%dh" % len(prm), 3 + len(prm), fn, *prm)
    body += struct.pack("<IHHIH", 7, 0x02FC, 0, 0x00000000, 0)  # CreateBrushIndirect solid black
    body += struct.pack("<IHH", 4, 0x012D, 0)  # SelectObject(0)
    for fn, prm in records:
        body += struct.pack("<IH%dh" % len(prm), 3 + len(prm), fn, *prm)
    body += struct.pack("<IH", 3, 0)  # EOF
    words = (18 + len(body)) // 2
    return struct.pack("<HHHIHIH", 1, 9, 0x300, words, 1, 7, 0) + body


def _ink_box(eb):
    import numpy as np

    a = np.frombuffer(eb.rgba, dtype=np.uint8).reshape(eb.height, eb.width, 4)
    ys, xs = np.nonzero(a[:, :, 3])
    return xs.min() / eb.width, ys.min() / eb.height, (xs.max() + 1) / eb.width, (ys.max() + 1) / eb.height


def _ctf_background(ctg_path):
    """CtDraw's baked static background of a page (the .ctF 'Dynamic CTF Bitmap', record 0) as
    an RGB array, or None when there is no current .ctF."""
    import struct
    import zlib
    import numpy as np
    from ctgedit.format import ctf as CTF
    from ctgedit.format.container import Container
    from ctgedit.format.page import parse_page

    f = CTF.ctf_path_for(ctg_path)
    if f is None or CTF.ctf_is_stale(ctg_path) is not False:
        return None
    c = Container.parse(open(f, "rb").read())
    b = parse_page(c.block("page"), c.word2).objects[0].body_raw
    for sig in (b"\x78\x5e", b"\x78\x9c", b"\x78\x01", b"\x78\xda"):
        k = b.find(sig)
        if k >= 0:
            try:
                raw = zlib.decompress(b[k:])
                break
            except zlib.error:
                continue
    else:
        return None
    w, h = struct.unpack_from("<ii", raw, 4)
    h = -h
    return np.frombuffer(raw[40 : 40 + w * h * 4], np.uint8).reshape(h, w, 4)[:, :, [2, 1, 0]]


@pytest.mark.skipif(not os.path.exists(os.path.join(EX, "!SOE_PopUp.ctg")), reason="Example not installed")
def test_runtime_animated_rows_not_filled_by_state_zero():
    """A bit-0x10-SET colour-animated shape (the !SOE_PopUp alarm rows: trailing
    0x23 array but runtime-driven) must NOT be filled with its array state-0 --
    the editor leaves it unfilled."""
    from ctgedit.io import reader

    pg = reader.read_page(os.path.join(EX, "!SOE_PopUp.ctg"))
    rows = [
        o
        for o in pg.objects
        if o.kind.value == "rectangle"
        and (o.raw.get("_dwflags", 0) & 0x10)
        and o.style.filled
        and (o.style.fill_colour or 0) & 0xFFFFFF in (0xFFAA55, 0x00FF00)
    ]
    assert not rows, "%d runtime-animated rows wrongly filled with state-0" % len(rows)


@have_ex
def test_reader_set_hash_mask_preserved():
    """A reader-set '##' value mask (the Mixer alarm-count, COL_STA_0 red) is NOT affected by the
    empty-text blank rule, which is only the scene's empty-text fallback."""
    _app()
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    reader.set_label_mode("literal")
    try:
        pg = reader.read_page(os.path.join(EX, "Mixer.ctg"))
        sc = PageScene()
        sc.set_page(pg, thumbnail_opacity=0.0)
        n = sum(
            1 for it in sc.items() if isinstance(it, QtWidgets.QGraphicsSimpleTextItem) and it.text() == "##"
        )
        assert n >= 1, "reader-set '##' masks lost"
    finally:
        reader.set_label_mode("native")


def test_native_mode_keeps_abc_stub():
    """The headless/native default: a runtime string display
    shows the generic 'abc' stub (no design-time echo)."""
    if not os.path.exists(EX):
        pytest.skip("Example not installed")
    _app()
    from ctgedit.io import reader

    reader.set_label_mode("native")
    pg = reader.read_page(os.path.join(EX, "!ServerProperties.ctg"))
    assert not any((o.style.text or "").startswith("=PageGetStr") for o in pg.objects)


_SA2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\ExampleSA"
# The WTP pages are a DEMO another tool rebuilds: a rebuild renumbers every record, so
# these tests find the status lights and pumps by CONTENT, never by index or AN.
_WTP = os.path.join(_SA2, "WTP_INTAKE_HD1080.ctg")
needs_wtp = pytest.mark.skipif(not os.path.exists(_WTP), reason="R2 ExampleSA WTP demo page absent")


def _ai_groups(path):
    """{index: own value bag} of the tc 0x08 records whose own AI_ON is non-zero -- the records
    CtDraw draws an alarm indicator for."""
    from ctgedit.format.page import parse_ctg
    from ctgedit.format import writer as W

    out = {}
    for i, po in enumerate(parse_ctg(path).page.objects):
        bag = {k.upper(): v for k, v in (W.value_bag(po) or [])}
        if po.type_code == 0x08 and c_atoi(bag.get("AI_ON")):
            out[i] = bag
    return out


def _status_lights(groups):
    """The WTP status lights: solid indicators (AI_INSIDE 1, AI_WIDTH 15, no flag)."""
    return [
        i
        for i, bag in groups.items()
        if bag.get("AI_INSIDE") == "1" and bag.get("AI_WIDTH") == "15" and bag.get("AI_FLAG_ON") == "0"
    ]


@needs_wtp
def test_alarm_indicator_rule():
    """CtDraw draws alarm indicators only on groups with AI_ON."""
    from ctgedit.io.reader import read_page
    from ctgedit.format.page import parse_ctg
    from ctgedit.format import geometry as G

    wtp = read_page(_WTP)
    ai = {i: o.raw["_alarm_indicator"] for i, o in enumerate(wtp.objects) if o.raw.get("_alarm_indicator")}
    groups = _ai_groups(_WTP)
    assert set(ai) == set(groups)
    lights = _status_lights(groups)
    assert len(lights) >= 2
    for i in lights:
        a = ai[i]
        o = a["outer"]
        assert (o[2] - o[0], o[3] - o[1]) == (31, 31) and a["strip"] == 16, (i, o)
        assert a["fill"] == (255, 0, 0) and a["pen"] == (0xBF, 0xBF, 0xBF) and a["flag"] is None
    wpos = parse_ctg(_WTP).page.objects
    for i in lights:  # no flag: Bounds == the frame
        assert ai[i]["outer"] == tuple(G.parse_geom_header(wpos[i].body_raw).rect2), i
    for n, want in (("Meters", {48}), ("Mixing_L3", None)):
        path = os.path.join(_SA2, n + ".ctg")
        pg = read_page(path)
        pos = parse_ctg(path).page.objects
        got = {i for i, o in enumerate(pg.objects) if o.raw.get("_alarm_indicator")}
        if want is not None:
            assert got == want
        assert got
        for i in got:
            a = pg.objects[i].raw["_alarm_indicator"]
            f = a["flag"]
            fx, fy = f.geometry.x, f.geometry.y
            fr = (fx, fy, fx + f.geometry.extent_x, fy + f.geometry.extent_y)
            o = a["outer"]
            bb = (min(o[0], fr[0]), min(o[1], fr[1]), max(o[2], fr[2]), max(o[3], fr[3]))
            assert bb == tuple(G.parse_geom_header(pos[i].body_raw).rect2), (n, i, bb)


@needs_wtp
def test_alarm_indicator_never_on_text():
    """AI_ON on a TEXT record draws nothing (CtDraw draws it for a group only). No shipped
    page has one, so a text of the WTP page gets AI_ON 1 in memory."""
    from ctgedit.editing import EditableDocument
    from ctgedit.model.document import ObjectKind

    doc = EditableDocument.open(_WTP)
    i = next(
        i
        for i, o in enumerate(doc.page.objects)
        if o.kind is ObjectKind.TEXT and not o.members and o.parent is None
    )
    assert doc.ensure_property(i, "AI_ON", "1")
    assert doc.ensure_property(i, "AI_WIDTH", "3")
    assert (doc.page.objects[i].raw.get("_props") or {}).get("AI_ON") == "1"  # re-derived
    got = {k for k, o in enumerate(doc.page.objects) if o.raw.get("_alarm_indicator")}
    assert i not in got and got == set(_ai_groups(_WTP))


@needs_wtp
def test_alarm_indicator_follows_move():
    """move_objects translates the model in place (no re-parse): the indicator must follow the
    group, not stay at its parse-time position (a status-light genie owns its AI group)."""
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.editing import EditableDocument
    from ctgedit.render.scene import PageScene

    doc = EditableDocument.open(_WTP)
    g = _status_lights(_ai_groups(_WTP))[0]
    root = doc.page.root_of(g)
    assert root != g  # the genie owns the AI group
    sc = PageScene()
    sc.set_page(doc.page)
    r0 = sc._ai_items[g].sceneBoundingRect()
    doc.move_objects([root], 100, 50)
    sc.set_page(doc.page)
    r1 = sc._ai_items[g].sceneBoundingRect()
    assert (r1.x() - r0.x(), r1.y() - r0.y()) == (100, 50)
    doc.undo()
    sc.set_page(doc.page)
    assert sc._ai_items[g].sceneBoundingRect() == r0


@needs_wtp
def test_hover_never_deletes_scene_items():
    """The canvas hit-test must not walk up with QGraphicsItem.parentItem(); PySide6 6.11 hands
    a TOP-LEVEL item reached that way back to Python ownership and deletes it once nothing
    references it. Hover over every drawn root and every alarm indicator, collect garbage:
    nothing may disappear."""
    import gc
    from PySide6 import QtCore, QtGui, QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    groups = set(_ai_groups(_WTP))
    light = _status_lights(_ai_groups(_WTP))[0]
    w = MainWindow()
    try:
        w.open_document(_WTP)
        w.resize(1600, 900)
        w.show()
        QtWidgets.QApplication.processEvents()
        sc = w.view.scene()
        before = len(sc.items())
        assert set(sc._ai_items) == groups
        targets = [it.sceneBoundingRect().center() for it in sc._ai_items.values()]
        targets += [it.sceneBoundingRect().center() for it in sc._items.values()]
        vpw = w.view.viewport()
        for c in targets:
            vp = w.view.mapFromScene(c)
            ev = QtGui.QMouseEvent(
                QtCore.QEvent.MouseMove,
                QtCore.QPointF(vp),
                QtCore.QPointF(vpw.mapToGlobal(vp)),
                QtCore.Qt.NoButton,
                QtCore.Qt.NoButton,
                QtCore.Qt.NoModifier,
            )
            QtWidgets.QApplication.sendEvent(vpw, ev)
        del targets
        gc.collect()
        QtWidgets.QApplication.processEvents()
        assert len(sc.items()) == before
        assert set(sc._ai_items) == groups
        # the hit-test still resolves the status light (its indicator) to the genie root
        o = w.doc.page.objects[light].raw["_alarm_indicator"]["outer"]
        vp = w.view.mapFromScene(QtCore.QPointF(o[0] + 4, o[1] + 4))
        assert w.doc.page.root_of(w.view._hit_index(vp)[0]) == w.doc.page.root_of(light)
    finally:
        w.doc = None
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.close()


EXSA_R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\ExampleSA"


def test_art_stripped_genie_polygons_take_twin_outline():
    """Art-stripped genie polygons take their twin's outline."""
    page = os.path.join(EXSA_R2, "WTP_INTAKE_HD1080.ctg")
    if not os.path.exists(page):
        pytest.skip("ExampleSA (R2) not installed")
    _app()
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    pg = reader.read_page(page)
    # the pumps by content (the WTP page is a demo that gets rebuilt): their Base / Discharge /
    # Body members
    parts = [
        i
        for p, o in enumerate(pg.objects)
        if "sa_drive.pump_32_r" in str(o.raw.get("_ref") or o.raw.get("_resolved_ref") or "")
        for i in pg.subtree(p)
        if pg.objects[i].raw.get("_name") in ("Base", "Discharge", "Body")
    ]
    assert len(parts) >= 6  # two pumps
    for i in parts:
        st = pg.objects[i].style
        assert st.filled and (st.fill_colour or 0) & 0xFFFFFF == 0xFFFFFF, i
        assert st.line_colour == 0x969696 and st.line_width == 1, (i, st.line_colour)
    mx = reader.read_page(os.path.join(EXSA_R2, "Mixing_L3.ctg"))
    bg, arrow = mx.objects[1001], mx.objects[1000]
    assert bg.raw.get("_name") == "Background" and bg.style.polygon_open
    assert not arrow.style.polygon_open
    sc = PageScene()
    sc.set_page(mx)
    item = sc._items[1001]
    assert item.brush().style() != QtCore.Qt.NoBrush, "open filled polygon lost its fill"


# ------------------------------------------------ free-hand shapes (tc 0x02)
_DYN1 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example\Dynamic_1.ctg"


@pytest.mark.skipif(not os.path.exists(_DYN1), reason="R2 Example missing")
def test_freehand_shape_draws_filled_and_open():
    """CtDraw's free-hand ('Draw', tc 0x02) shape: a style block, then a counted
    point array that ends the body. Live: Dynamic_1 713 is an orange
    raised filled shape, and a hollow free-hand stroke
    has no closing segment (812 of a page)."""
    from ctgedit.io import reader
    from ctgedit.model.document import ObjectKind
    from ctgedit.render.scene import PageScene

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pg = reader.read_page(_DYN1)
    o = pg.objects[713]
    assert o.kind is ObjectKind.POLYGON and len(o.geometry.nodes) == 382
    assert o.style.filled and o.style.fill_colour == 0xFF952B
    assert o.style.polygon_open and o.style.three_d == 1
    assert not o.raw.get("_non_drawable")
    sc = PageScene()
    sc.set_page(pg)
    assert sc._items[713] is not None


@pytest.mark.skipif(not os.path.exists(_DYN1), reason="R2 Example missing")
def test_freehand_move_carries_its_points(tmp_path):
    """Move / resize edit the point array too (live: both are CtDraw fixed points)."""
    import shutil
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G

    dst = tmp_path / "Dynamic_1.ctg"
    shutil.copyfile(_DYN1, dst)
    doc = EditableDocument.open(str(dst))
    before = G.get_object_nodes(doc.parsed.page.objects[713])
    doc.move_objects([713], 30, 20)
    po = doc.parsed.page.objects[713]
    after = G.get_object_nodes(po)
    assert after == [(x + 30, y + 20) for x, y in before]
    l, t, r, b = G.parse_geom_header(po.body_raw).rect1
    xs = [x for x, _ in after]
    ys = [y for _, y in after]
    assert (min(xs), min(ys), max(xs), max(ys)) == (l, t, r, b)


@pytest.mark.skipif(not os.path.exists(_DYN1), reason="R2 Example missing")
def test_freehand_without_a_style_block_has_no_points():
    """A tc 0x02 record with dwFlags bit 0x2 clear (an art-stripped genie member)
    carries no points and stays non-drawable."""
    import copy
    from ctgedit.format import geometry as G
    from ctgedit.format.page import parse_ctg

    po = copy.copy(parse_ctg(_DYN1).page.objects[713])
    assert G.find_node_array(po) is not None
    po.subtype_size &= ~0x2
    assert G.find_node_array(po) is None


@have_ex
def test_art_stripped_ellipse_takes_its_owners_pie():
    """Example Groups' dials are art-stripped ellipses whose owner is a half-dial PIE
    (-90..90); a (0, 0) pie is a full pie with a radius line."""
    from PySide6 import QtWidgets
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    _app()
    pg = reader.read_page(os.path.join(EX, "Groups.ctg"))
    dials = [
        o
        for o in pg.objects
        if o.kind.value == "ellipse" and o.raw.get("_twin_shape") and o.style.ellipse_style == 1
    ]
    assert len(dials) >= 3 and all((o.style.start_angle, o.style.end_angle) == (-90.0, 90.0) for o in dials)
    op = reader.read_page(os.path.join(EX, "Oper_Input.ctg"))
    i = next(
        i
        for i, o in enumerate(op.objects)
        if o.kind.value == "ellipse"
        and o.style.ellipse_style == 1
        and not o.style.start_angle
        and not o.style.end_angle
    )
    sc = PageScene()
    sc.set_page(op)
    assert isinstance(sc._items[i], QtWidgets.QGraphicsPathItem)


@have_ex
def test_legacy_v3_v4_objects_draw_like_ctdraw():
    """Example Genies' V3/V4 records: a Symbol (0x1007) draws its
    SYMB000 state, an Animated Symbol (0x100D) its OFF_SYMBOL, a String (0x1005, owning a
    keyboard command) its OFF text and a Number its mask, in the fonts.dbf named font
    (Include's FontSP = Courier New 14 px light red)."""
    from ctgedit.io import reader

    pg = reader.read_page(os.path.join(EX, "Genies.ctg"))
    by_tc = {}
    for o in pg.objects:
        by_tc.setdefault(o.raw.get("_type_code"), []).append(o)
    sym = by_tc[0x1007][0]
    assert sym.raw.get("_ref") == "pwrdist.cb1_OPEN_R" and sym.children
    aset = by_tc[0x100D][0]
    assert aset.raw.get("_ref") == "boiler.Tubes0" and aset.children
    ok = next(o for o in by_tc[0x1005] if o.style.text == "@(OK)")
    assert ok.kind.value == "number" and not ok.raw.get("_non_drawable")
    assert ok.style.font == "Courier New" and ok.raw.get("_font_px") == 14
    assert ok.style.text_colour == 0xFF0000 and not ok.style.filled
    num = by_tc[0x1004][0]
    assert num.style.text.startswith("#") and not num.style.filled


def test_art_stripped_touch_faces_are_drawn_from_their_data():
    """No editor rounding or grey 'XP edge' on art-stripped rects that own a touch command
    (the SA list rows of DefaultAlarm are flat in CtDraw)."""
    from PySide6 import QtWidgets
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    page = os.path.join(r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\ExampleSA", "DefaultAlarm_HD1080.ctg")
    if not os.path.exists(page):
        pytest.skip("ExampleSA not installed")
    _app()
    sc = PageScene()
    sc.set_page(reader.read_page(page))
    pens = [
        it.pen().color().name()
        for it in sc.items()
        if isinstance(it, (QtWidgets.QGraphicsRectItem, QtWidgets.QGraphicsPathItem))
    ]
    assert "#808080" not in pens


def test_a_stretched_text_gets_the_editors_dotted_frame():
    """CtDraw frames a text it cannot draw as placed (non-uniform scale or mirrored)
    with a dotted rectangle, on screen only (the FP_PMP label at
    1.14 x 1.21)."""
    from PySide6 import QtGui
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene, _TextFrame

    page = os.path.join(r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\ExampleSA", "FP_PMP_HD1080.ctg")
    if not os.path.exists(page):
        pytest.skip("ExampleSA not installed")
    _app()
    pg = reader.read_page(page)
    framed = [i for i, o in enumerate(pg.objects) if o.raw.get("_text_frame")]
    assert framed
    sc = PageScene()
    sc.set_page(pg)
    fr = next(c for c in sc._items[framed[0]].childItems() if isinstance(c, _TextFrame))
    r = fr.sceneBoundingRect()
    g = pg.objects[framed[0]].geometry
    assert abs(r.left() + 1 - g.x) < 1.5 and abs(r.top() + 1 - g.y) < 1.5

    def ink():
        img = QtGui.QImage(int(g.extent_x) + 4, int(g.extent_y) + 4, QtGui.QImage.Format_RGB32)
        img.fill(0xFFFFFF)
        p = QtGui.QPainter(img)
        sc.render(p, QtCore.QRectF(img.rect()), QtCore.QRectF(g.x - 2, g.y - 2, img.width(), img.height()))
        p.end()
        return sum(
            1
            for x in range(img.width())
            for y in range(img.height())
            if QtGui.QColor(img.pixel(x, y)).lightness() < 100
        )

    assert ink() > 10
    with sc.export_only(None):
        assert ink() < 5


_ACTIVEX_2 = os.path.join(r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User", "Example", "ActiveX_2.ctg")
