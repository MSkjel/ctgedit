"""Page Properties."""

import os
import shutil

import pytest

import fxinstall

#: the SA page (FxSa): the Page Properties dialog tests' page
SA_PAGE, SA_PROJECT, SA_TITLE = "FxSaMaster", "FxSa", "Fx workspace"
#: the fake install's copy of this test (set by the autouse fixture)
FX = None


@pytest.fixture(autouse=True)
def fx(fx_install):
    """The fake install's copy for every test of this file (FX)."""
    global FX
    FX = fx_install
    yield fx_install
    FX = None


def _page(name, project="FxMain"):
    return FX.page(name, project)


def _project(name="FxMain"):
    return FX.project(name)


# ----------------------------------------------------------- implicit includes
def test_implicit_include_project_on_search_path():
    """Every project implicitly includes the system-wide Include project (FxMain's
    include.DBF names only FxLib and FxStyle): the install's Include joins the path and
    its libraries resolve (the two-install lookup itself is pinned on a synthetic tree in
    test_symbol_resolution.test_include_chain_with_sibling_install_fallback)."""
    from ctgedit.format.symbols import SymbolReference, SymbolResolver

    r = SymbolResolver.for_page(_page("FxSymbols"))
    include_dirs = [p for p in r.search_paths if os.path.basename(p).lower() == "include"]
    installed = [_project("Include")]
    assert {os.path.normcase(p) for p in installed} <= {os.path.normcase(p) for p in include_dirs}, (
        r.search_paths
    )
    for lib, elem in (("lights", "light_1_red"), ("icons", "help1"), ("fxinc", "fx_banner")):
        rs = r.resolve(SymbolReference(lib, elem, "ctl", "symbol"))
        assert rs is not None, (lib, elem)


def test_symbols_render_children():
    """Linked symbols resolve into drawable art."""
    from collections import Counter
    from ctgedit.io.reader import read_page
    from ctgedit.model.document import ObjectKind

    page = read_page(_page("FxSymbols"))

    def ref(o):
        return str(o.raw.get("_resolved_ref") or o.raw.get("_ref") or "").lower()

    def drawn(o):
        if o.kind is ObjectKind.SYMBOL:
            return bool(o.children)
        return bool(o.members) and (
            bool(o.children) or any(page.objects[m].kind is not ObjectKind.UNKNOWN for m in o.members)
        )

    libs = Counter(ref(o).split(".")[0] for o in page.objects if ref(o) and drawn(o))
    assert libs == {"fxsym": 13, "fxinc": 1, "lights": 2}, libs


def test_every_placed_symbol_draws():
    """Every placed symbol draws."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.readerparts.symbols import _member_has_art

    doc = EditableDocument.open(_page("FxSymbols"))
    objs = doc.page.objects
    placed = [(i, kind) for i, key, kind in doc.symbol_placements() if key[0].lower() in ("fxsym", "lights")]

    def draws(i, kind):
        o = objs[i]
        if o.children or o.raw.get("_embedded_bmp") is not None:
            return True
        return kind == "group" and any(
            _member_has_art(objs[k]) or objs[k].children for k in doc.page.subtree(i) if k != i
        )

    assert len(placed) >= 12, "too few placed symbols (%d)" % len(placed)
    assert {kind for _i, kind in placed} == {"leaf", "group"}
    bad = [(i, kind) for i, kind in placed if not draws(i, kind)]
    assert not bad, "symbols that draw nothing: %r" % bad


def test_stripped_symbol_slots_repaired():
    """FxGenies' FxLamp (fxgenie.fx_lamp: a symbol set on %Tag%) stores its state slots
    as 99-byte art-stripped tc=7 records inside the ref-carrying genie; the repair pass
    grafts the matched library record's embedded DIB / resolved children onto them."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg
    from ctgedit.io.reader import read_page

    path = _page("FxGenies")
    page = read_page(path)
    pobjs = parse_ctg(path).page.objects
    (lamp,) = EditableDocument.open(path).find_objects_by_name("FxLamp")
    grafted = 0
    for i in page.subtree(lamp):
        o = page.objects[i]
        if o.raw.get("_type_code") != 0x07:
            continue
        assert len(pobjs[i].body_raw) == 99  # (stored art-stripped)
        if o.raw.get("_embedded_bmp") is not None or o.children:
            grafted += 1
    assert grafted >= 2, "stripped symbol slots not repaired (%d)" % grafted
    # a one-char caption 'M' survives as a literal label, in magenta (FxTextKinds)
    page = read_page(_page("FxTextKinds"))
    motors = [o for o in page.objects if o.style.text == "M" and o.raw.get("_type_code") == 0x05]
    assert motors, "the 'M' caption is missing"
    assert any(o.style.text_colour == 0xFF00FF for o in motors), (
        "the 'M' caption lost its magenta trailing-pair colour"
    )


def test_template_chrome_graft():
    """The template's chrome is grafted onto its art-stripped copy."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg
    from ctgedit.io.reader import _template_alignment, read_page
    from ctgedit.format.symbols import SymbolResolver
    from ctgedit.model.document import ObjectKind

    path = _page("FxSaContent", "FxSa")
    pobjs = parse_ctg(path).page.objects
    resolver = SymbolResolver.for_page(path)
    tpl_map = _template_alignment(pobjs, resolver)
    assert len(tpl_map) >= 5, "template alignment collapsed (%d)" % len(tpl_map)

    page = read_page(path)
    (nav,) = EditableDocument.open(path).find_objects_by_name("FxTplNav")
    assert nav in tpl_map
    o = page.objects[nav]
    art = [
        page.objects[m] for m in o.members if m in tpl_map and page.objects[m].kind is ObjectKind.RECTANGLE
    ]
    assert len(art) == 1 and art[0].raw.get("_twin_style"), "nav slot not grafted"


def test_colour_model():
    """CtDraw's editor colour model."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg
    from ctgedit.io.reader import read_page

    maxon_rest = red_block = mask = 0
    for name in ("FxColours", "FxTextKinds"):
        path = _page(name)
        page = read_page(path)
        pobjs = parse_ctg(path).page.objects
        for i, po in enumerate(pobjs):
            o = page.objects[i]
            rp = po.raw_properties()
            # COL_MAXON + NO literal COL_A condition -> not folded -> rests at the
            # block/state-0 default, as the live editor shows
            if (
                po.type_code in (0x0F, 0x11)
                and rp.get("COL_MAXON")
                and "COL_A" not in rp
                and "VISIBLE" not in rp
            ):
                on = int(rp["COL_MAXON"].split(",")[0], 16) & 0xFFFFFF
                if on != (o.style.fill_colour or 0) & 0xFFFFFF:
                    maxon_rest += 1
            if (
                po.type_code == 0x0F
                and "COL_STA_0" in rp
                and o.style.filled
                and o.style.fill_colour == 0xFF0000
            ):
                red_block += 1
            if (
                str(rp.get("TXT_OFF", "")).startswith("####")
                and o.style.text
                and set(o.style.text) <= set("#0.,-+ !")
            ):
                mask += 1
    doc = EditableDocument.open(_page("FxColours"))
    (arr,) = doc.find_objects_by_name("FxCm2")
    seq = doc.page.objects[arr].style
    assert seq.filled and (seq.fill_colour or 0) & 0xFFFFFF == 0xFF0000, (
        "the state-0 red colour-array box lost its fill"
    )
    (el,) = doc.find_objects_by_name("FxEllipseMaxon")
    assert (doc.page.objects[el].style.fill_colour or 0) & 0xFFFFFF == 0x808080
    assert maxon_rest >= 1, (
        "COL_MAXON-only boxes must rest at the block default, not the on-colour (%d)" % maxon_rest
    )
    assert red_block >= 1, "block face colour lost on the COL_STA_0 box"
    assert mask >= 5, "format-mask placeholders missing (%d)" % mask


@pytest.mark.golden
def test_colour_model_is_what_ctdraw_draws():
    """CtDraw's own canvas of FxColours (the oracle's canvas.png: the canvas grabbed at
    100 % with the page's top left at the canvas's top left): the pixel at the middle of
    each box is the rest colour our model gives it (FxEllipseMaxon grey, not its COL_MAXON
    on-colour; FxCm1 and FxCm2 their state-0 red)."""
    from PySide6 import QtGui
    from ctgedit.editing import EditableDocument

    img = QtGui.QImage()
    assert img.loadFromData(fxinstall.oracle_bytes("pages", "FxMain/FxColours", "canvas.png"))
    doc = EditableDocument.open(_page("FxColours"))
    for name in ("FxEllipseMaxon", "FxCm1", "FxCm2", "FxFlatPair"):
        (i,) = doc.find_objects_by_name(name)
        o = doc.page.objects[i]
        g = o.geometry
        c = img.pixelColor(int(g.x + g.extent_x / 2), int(g.y + g.extent_y / 2))
        want = (o.style.fill_colour or 0) & 0xFFFFFF
        assert (c.red(), c.green(), c.blue()) == ((want >> 16) & 0xFF, (want >> 8) & 0xFF, want & 0xFF), name


# ----------------------------------------------------------------- pages.DBF
def test_page_record_read_and_update(tmp_path):
    from ctgedit.io.project import Project

    shutil.copy(os.path.join(_project(), "pages.DBF"), tmp_path)
    p = Project(str(tmp_path))
    rec = p.page_record("FxMaster")
    assert rec is not None
    assert rec.get("TITLE") == "@(Fx Start)"
    assert "FxMaster" in p.page_names()

    assert p.update_page_record("FxMaster", {"TITLE": "Edited Title", "SCANTIME": "250"}) is True
    rec2 = p.page_record("FxMaster")
    assert rec2.get("TITLE") == "Edited Title"
    assert rec2.get("SCANTIME") == "250"
    # unknown page -> False, file untouched
    assert p.update_page_record("NoSuchPage", {"TITLE": "x"}) is False


# ----------------------------------------------------- group / ungroup
def test_group_and_ungroup_round_trip(tmp_path):
    """Arrange > Group wraps root subtrees in a synthesized tc=8 container
    (depth-first forest stays valid, save round-trips); Ungroup dissolves it."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_page(SA_PAGE, SA_PROJECT))
    a = doc.create_object("rectangle", rect=(10, 10, 60, 60), colour=0x102030)
    b = doc.create_object("ellipse", rect=(80, 10, 130, 60), colour=0x405060)

    gi = doc.group_objects([a, b])
    assert gi is not None
    go = doc.page.objects[gi]
    assert go.members and len(go.members) == 2
    assert doc.parsed.page.objects[gi].type_code == 0x08
    assert doc.parsed.page.objects[gi].class_flag == 2
    # union bbox covers both members
    g = go.geometry
    assert (g.x, g.y) == (10, 10)
    assert (g.x + g.extent_x, g.y + g.extent_y) == (130, 60)
    # the whole unit moves together
    doc.move_objects(doc.expand_to_subtrees([gi]), 5, 5)
    assert doc.page.objects[gi].geometry.x == 15

    # save + reopen: forest intact
    out = str(tmp_path / "grouped.ctg")
    doc.save(out)
    re = EditableDocument.open(out)
    gi2 = next(
        i
        for i, o in enumerate(re.page.objects)
        if o.members and len(o.members) == 2 and re.page.objects[o.members[0]].geometry.x == 15
    )
    assert re.parsed.page.objects[gi2].class_flag == 2

    # ungroup: members become roots again
    members = doc.ungroup_object(gi)
    assert len(members) == 2
    for m in members:
        assert doc.page.objects[m].parent is None
    doc.undo()  # restores the group structurally
    assert doc.page.objects[gi].members


def test_save_registers_new_page(tmp_path):
    """Saving a page into a project dir registers it in pages.DBF (CtDraw
    parity: unregistered pages don't compile)."""
    import shutil as _sh
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project

    _sh.copy(os.path.join(_project(), "pages.DBF"), tmp_path)
    _sh.copy(_page("FxShapes"), tmp_path / "BrandNewPage.ctg")
    doc = EditableDocument.open(str(tmp_path / "BrandNewPage.ctg"))
    doc.save()
    proj = Project(str(tmp_path))
    rec = proj.page_record("BrandNewPage")
    assert rec is not None
    # what CtDraw's own registration writes (its COM-created pages; the golden test
    # below reads it from the capture): TITLE = NAME, IGNOREVQ left blank
    assert rec.get("TITLE", "").strip() == "BrandNewPage"
    assert rec.get("IGNOREVQ", "").strip() == ""
    # existing rows intact, count grew by exactly one
    assert proj.page_record("FxMaster") is not None
    # second save does not duplicate
    doc.save()
    assert sum(1 for r in proj.page_rows() if r.get("NAME", "").strip() == "BrandNewPage") == 1


@pytest.mark.golden
@pytest.mark.parametrize("unit,name", [("linked", "FxOracleNewLinked"), ("unlinked", "FxOracleNewUnlinked")])
def test_ctdraws_registration_writes_title_and_no_ignorevq(unit, name):
    """CtDraw's New Page + Save As (the newpage capture): the pages.DBF row it adds has
    TITLE = NAME and no IGNOREVQ (the rows.json of a unit lists an added row's fields that
    are not blank), as our registration writes it."""
    rows = fxinstall.oracle_json("newpage", unit, "rows.json")
    pages = next(v for k, v in rows["tables"].items() if k.lower().endswith("/pages.dbf"))
    (row,) = [r for r in pages["added"] if r.get("NAME", "").strip() == name]
    assert row.get("TITLE", "").strip() == name
    assert not row.get("IGNOREVQ", "").strip()


# ------------------------------------------------------------- page edits
def test_set_page_size_round_trip(tmp_path):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_page(SA_PAGE, SA_PROJECT))
    doc.set_page_size(1904, 750)
    assert doc.page.appearance.width == 1904
    assert doc.parsed.attributes.width == 1904
    assert doc.parsed.page.page_width == 1904
    out = str(tmp_path / "resized.ctg")
    doc.save(out)
    re = EditableDocument.open(out)
    assert (re.page.appearance.width, re.page.appearance.height) == (1904, 750)
    doc.undo()
    assert doc.page.appearance.width == 1920


def test_template_reference_edit_and_undo():
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_page(SA_PAGE, SA_PROJECT))
    idx, ref = doc.template_reference()
    assert idx is not None
    assert ref.library == "fx_sa" and ref.ext == "ctt"
    assert ref.element == "normal@-HD1080"

    new = ref.raw.replace("HD1080", "UHD4K")
    assert doc.set_template_reference(idx, new) is True
    _i, ref2 = doc.template_reference()
    assert ref2.element == "normal@-UHD4K"
    doc.undo()
    _i, ref3 = doc.template_reference()
    assert ref3.raw == ref.raw
    # a nonsense splice target reports failure and leaves the body intact
    assert doc.set_template_reference(idx, "") is False
    _i, ref4 = doc.template_reference()
    assert ref4.raw == ref.raw


# --------------------------------------------------- embedded bitmap create
def test_bitmap_record_round_trip(tmp_path):
    """File>Import / Convert to Bitmap: a synthesized embedded-bitmap record
    decodes back pixel-exact and survives save."""
    import shutil as _sh
    from ctgedit.editing import EditableDocument
    from ctgedit.format.symbols import embedded_bitmap_from_body
    from ctgedit.io.reader import _read_geometry

    _sh.copy(_page(SA_PAGE, SA_PROJECT), tmp_path / "m.ctg")
    doc = EditableDocument.open(str(tmp_path / "m.ctg"))
    w, h = 5, 3
    rgba = bytearray()
    for i in range(w * h):
        rgba += bytes((i * 10 % 256, 50, 200, 255 if i % 2 == 0 else 0))
    idx = doc.add_bitmap_object((30, 40, 30 + w, 40 + h), bytes(rgba), w, h)

    o = doc.page.objects[idx]
    assert o.raw.get("_embedded_bmp") is not None
    bw, bh, brgba, balpha = o.raw["_embedded_bmp"]
    assert (bw, bh) == (w, h) and balpha is True
    # opaque pixels round-trip exactly; transparent ones have alpha 0
    for i in range(w * h):
        r0, g0, b0, a0 = rgba[i * 4 : i * 4 + 4]
        r1, g1, b1, a1 = brgba[i * 4 : i * 4 + 4]
        if a0 >= 128:
            assert (r1, g1, b1, a1) == (r0, g0, b0, 255)
        else:
            assert a1 == 0

    out = str(tmp_path / "bmp.ctg")
    doc.save(out)
    re = EditableDocument.open(out)
    po = re.parsed.page.objects[idx]
    geo = _read_geometry(po.body_raw, 0)
    eb = embedded_bitmap_from_body(po.body_raw, geo["end"])
    assert eb is not None and (eb.width, eb.height) == (w, h)


# --------------------------------------------------- shape variants
def _shape_tails():
    """(rounded, gradients, ellipses) over the shape tails of every FxMain page; every
    rectangle tail is valid (a radius of at most 32)."""
    import glob as _glob
    from ctgedit.format.page import parse_ctg
    from ctgedit.format import geometry as G

    rounded = grads = pies = 0
    for f in sorted(_glob.glob(os.path.join(_project(), "*.ctg"))):
        for po in parse_ctg(f).page.objects:
            if not (po.subtype_size & 0x2):
                continue
            t = G.parse_shape_tail(po)
            if t is None:
                continue
            if t["kind"] == "rect":
                assert t["radius"] <= 32
                rounded += t["radius"] > 0
                grads += t["grad_mode"] > 0
            elif t["kind"] == "ellipse":
                pies += 1
    return rounded, grads, pies


def test_shape_variant_decode_sweep():
    """The byte-pinned shape tails decode on every FxMain page: every bit2 rect yields a
    valid tail; the gradients (FxShapes' FxGradient, FxColours' FxGradientTB) and the
    ellipses and pies decode."""
    _rounded, grads, pies = _shape_tails()
    assert grads >= 2 and pies >= 1


def test_rounded_rectangles_decode():
    """A rounded rectangle's corner radius decodes (FxColours' FxRounded: corner radius 8)."""
    rounded, _grads, _pies = _shape_tails()
    assert rounded > 0


def test_shape_variant_edit(tmp_path):
    """The editing APIs of the shape tails round-trip through save."""
    import shutil as _sh
    from ctgedit.editing import EditableDocument

    _sh.copy(_page(SA_PAGE, SA_PROJECT), tmp_path / "m.ctg")
    doc = EditableDocument.open(str(tmp_path / "m.ctg"))
    r = doc.create_object("rectangle", rect=(10, 10, 110, 70), colour=0x224466)
    assert doc.set_corner_radius(r, 7) is True
    assert doc.set_gradient(r, 3, 0x00CCEE) is True
    assert doc.set_effect(r, 1, depth=2) is True
    st = doc.page.objects[r].style
    assert st.corner_radius == 7
    assert st.gradient_mode == 3 and st.gradient_colour == 0x00CCEE
    assert st.three_d == 1 and st.three_d_depth == 2

    e = doc.create_object("ellipse", rect=(150, 10, 250, 90), colour=0x884422)
    assert doc.set_ellipse(e, style=1, start=-90, end=91) is True
    ste = doc.page.objects[e].style
    assert ste.ellipse_style == 1
    assert (ste.start_angle, ste.end_angle) == (-90.0, 91.0)

    out = str(tmp_path / "variants.ctg")
    doc.save(out)
    re = EditableDocument.open(out)
    st2 = re.page.objects[r].style
    assert st2.corner_radius == 7 and st2.gradient_mode == 3
    st3 = re.page.objects[e].style
    assert st3.ellipse_style == 1 and st3.start_angle == -90.0

    doc.undo()  # ellipse
    assert doc.page.objects[e].style.ellipse_style == 0


# --------------------------------------------------- classic genie re-subst
def test_classic_genie_resubstitution(tmp_path):
    """Editing a classic genie's parameter re-substitutes the member records'
    current-value bags from their stored %templates% (CtDraw dialog-apply
    semantics): an fx_valve member's text follows %TagPrefix% (FxGenies' FxValve1 ..)."""
    import shutil as _sh
    from ctgedit.editing import EditableDocument
    from ctgedit.format.writer import genie_bindings as wb

    _sh.copy(_page("FxGenies"), tmp_path / "m.ctg")
    doc = EditableDocument.open(str(tmp_path / "m.ctg"))
    # find a classic genie root whose bindings carry TagPrefix
    root = next(
        i
        for i, po in enumerate(doc.parsed.page.objects)
        if po.type_code == 0x08 and (po.subtype_size & 0x200) and "TagPrefix" in wb(po)
    )
    old_prefix = wb(doc.parsed.page.objects[root])["TagPrefix"]
    # a member whose substituted properties embed the prefix
    member = next(
        j
        for j in doc.subtree(root)
        if j != root
        and any(old_prefix in str(v) for v in doc.parsed.page.objects[j].raw_properties().values())
    )
    old_txt = {
        k: v for k, v in doc.parsed.page.objects[member].raw_properties().items() if old_prefix in str(v)
    }
    assert old_txt

    assert doc.set_genie_binding(root, "TagPrefix", "NewPumpTag") is True
    assert wb(doc.parsed.page.objects[root])["TagPrefix"] == "NewPumpTag"
    rp = doc.parsed.page.objects[member].raw_properties()
    for k, v in old_txt.items():
        assert rp.get(k) == str(v).replace(old_prefix, "NewPumpTag"), "member %r not re-substituted: %r" % (
            k,
            rp.get(k),
        )

    # one undo restores root AND members
    doc.undo()
    assert wb(doc.parsed.page.objects[root])["TagPrefix"] == old_prefix
    rp2 = doc.parsed.page.objects[member].raw_properties()
    for k, v in old_txt.items():
        assert rp2.get(k) == v

    # save round-trips the edited state
    doc.redo()
    out = str(tmp_path / "resub.ctg")
    doc.save(out)
    re_doc = EditableDocument.open(out)
    assert wb(re_doc.parsed.page.objects[root])["TagPrefix"] == "NewPumpTag"


# ------------------------------------------------------------- fonts
def test_font_tail_decode():
    """The byte-pinned text font: every dwFlags-bit2 text record on FxTextKinds
    yields a face + point size; the anchor echo matches the geometry header."""
    from ctgedit.format.page import parse_ctg
    from ctgedit.format import geometry as G
    from ctgedit.io.reader import read_page

    pobjs = parse_ctg(_page("FxTextKinds")).page.objects
    page = read_page(_page("FxTextKinds"))
    decoded = 0
    for i, po in enumerate(pobjs):
        if po.type_code != 0x05 or not (po.subtype_size & 0x2):
            continue
        tail = G.parse_text_tail(po)
        assert tail is not None, "text tail failed on #%d" % i
        gh = G.parse_geom_header(po.body_raw)
        assert tail["echo"] == gh.anchor, "anchor echo mismatch on #%d" % i
        assert 1 <= tail["size"] <= 64
        st = page.objects[i].style
        assert st.font_size == tail["size"]
        decoded += 1
    assert decoded >= 20, "too few text tails decoded (%d)" % decoded


def test_font_editing_round_trip(tmp_path):
    """set_font edits face/size/B-I-U-S/justify in place; the echo survives a
    move; everything round-trips through save."""
    import shutil as _sh
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G

    _sh.copy(_page("FxTextKinds"), tmp_path / "m.ctg")
    doc = EditableDocument.open(str(tmp_path / "m.ctg"))
    # a text with its OWN font tail (an art-stripped template copy only shows
    # a grafted template size and has no font to edit)
    idx = next(
        i
        for i, o in enumerate(doc.page.objects)
        if o.raw.get("_type_code") == 0x05
        and o.style.font_size
        and G.parse_text_tail(doc.parsed.page.objects[i]) is not None
    )

    assert doc.set_font(idx, size=17, bold=True, justify="centre", face="Tahoma") is True
    st = doc.page.objects[idx].style
    assert st.font_size == 17 and st.bold and st.font == "Tahoma"
    assert st.justify.value == "centre"
    assert doc.set_text_effect(idx, 3) is True
    assert doc.page.objects[idx].style.three_d == 3

    # the anchor echo follows a move (parse checksum stays valid)
    doc.move_objects(doc.expand_to_subtrees([idx]), 9, 4)
    po = doc.parsed.page.objects[idx]
    tail = G.parse_text_tail(po)
    gh = G.parse_geom_header(po.body_raw)
    assert tail is not None and tail["echo"] == gh.anchor

    out = str(tmp_path / "fonts.ctg")
    doc.save(out)
    re = EditableDocument.open(out)
    st2 = re.page.objects[idx].style
    assert st2.font_size == 17 and st2.bold and st2.font == "Tahoma"
    assert st2.three_d == 3

    doc.undo()  # move
    doc.undo()  # effect
    doc.undo()  # font
    st3 = doc.page.objects[idx].style
    assert st3.font_size != 17 or not st3.bold


# ----------------------------------------------------- rotate / mirror
def test_rotate_and_mirror(tmp_path):
    """Arrange > Rotate/Mirror: node shapes transform exactly about the
    selection centre; 4x rotate / 2x mirror are identity; undo restores."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_page(SA_PAGE, SA_PROJECT))
    tri = doc.create_object("polygon", nodes=[(100, 100), (140, 100), (120, 140)], colour=0x112233)
    box = doc.create_object("rectangle", rect=(200, 100, 260, 140), colour=0x445566)

    g0_tri = list(doc.page.objects[tri].geometry.nodes)
    g0_box = (
        doc.page.objects[box].geometry.x,
        doc.page.objects[box].geometry.y,
        doc.page.objects[box].geometry.extent_x,
        doc.page.objects[box].geometry.extent_y,
    )

    doc.rotate_objects([tri, box], clockwise=True)
    g1 = doc.page.objects[box].geometry
    # the box (200..260 x 100..140) rotates CW about the union centre
    assert (g1.extent_x, g1.extent_y) == (40, 60), (g1.extent_x, g1.extent_y)
    # rotating four times returns home (within integer rounding)
    for _ in range(3):
        doc.rotate_objects([tri, box], clockwise=True)
    g4 = doc.page.objects[box].geometry
    assert abs(g4.x - g0_box[0]) <= 2 and abs(g4.y - g0_box[1]) <= 2
    assert (g4.extent_x, g4.extent_y) == (g0_box[2], g0_box[3])

    doc.mirror_objects([tri], horizontal=True)
    doc.mirror_objects([tri], horizontal=True)
    gtri = doc.page.objects[tri].geometry.nodes
    assert all(abs(a[0] - b[0]) <= 1 and abs(a[1] - b[1]) <= 1 for a, b in zip(gtri, g0_tri))

    # one undo step per operation
    n_ops = 6
    for _ in range(n_ops):
        doc.undo()
    assert list(doc.page.objects[tri].geometry.nodes) == g0_tri


def test_arrange_tools_dialogs():
    """The Rotate/Mirror/Align/Adjust/Goto/Find dialogs construct and carry
    CtDraw's options (on FxMany: 97 objects; FxManyPump4 is bound Label 'Pump 4')."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.dialog.dialogs import GotoObjectDialog
    from ctgedit.ui.dialog.arrange_dialogs import AdjustPositionDialog
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    doc = EditableDocument.open(_page("FxMany"))

    adj = AdjustPositionDialog((10, 20, 30, 40))
    assert adj.rect() == (10, 20, 30, 40)
    g = GotoObjectDialog(doc)
    assert g.object_count() > 90
    f = FindReplaceDialog(doc, replace_mode=True)
    f._ed_find.setText("Pump 4")
    f.find_next()
    assert f._cursor >= 0
    # (Find searches every field: the first match is the FxManyPump4
    # genie's Label parameter, on the genie itself)
    assert "Pump 4" in f.current_hit().value
    assert f.current_hit().index in doc.find_objects_by_name("FxManyPump4")


# --------------------------------------------------- object property sheet
def test_object_properties_sheet(tmp_path):
    """The CtDraw-style per-object sheet: tab tree present, style-block edits
    (width/style/fill) and animation-key edits round-trip with undo."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    shutil.copy(_page(SA_PAGE, SA_PROJECT), tmp_path)
    doc = EditableDocument.open(str(tmp_path / (SA_PAGE + ".ctg")))
    idx = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)

    dlg = ObjectPropertiesDialog(doc, idx)
    tabs = [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())]
    assert tabs == ["Appearance", "Movement", "Scaling", "Fill", "Input", "Slider", "Access", "Metadata"]

    dlg._sp_width.setValue(4)
    dlg._cb_lstyle.setCurrentIndex(2)  # Dot
    dlg._editors["VISIBLE"].setText("MyTag = 1")
    dlg._editors["MVX"].setText("MoveExpr")
    assert dlg.apply() is True
    o = doc.page.objects[idx]
    assert o.style.line_width == 4
    assert o.style.line_style == 2
    rp = doc.object_properties(idx)
    assert rp.get("VISIBLE") == "MyTag = 1"
    assert rp.get("MVX") == "MoveExpr"
    # clearing a field removes the key
    dlg._editors["MVX"].setText("")
    assert dlg.apply() is True
    assert "MVX" not in doc.object_properties(idx)
    # save round-trips the edits
    out = str(tmp_path / "sheet.ctg")
    doc.save(out)
    re = EditableDocument.open(out)
    o2 = re.page.objects[idx]
    assert o2.style.line_width == 4 and o2.style.line_style == 2
    assert re.object_properties(idx).get("VISIBLE") == "MyTag = 1"


# ------------------------------------------------------------- the dialog
def test_page_properties_dialog_matches_ctdraw(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog
    from ctgedit.ui.pick_fields import PageField, TemplatePicker

    monkeypatch.setenv("CTG_LIB_PATH", _project(SA_PROJECT))  # the copy's include chain
    shutil.copy(_page(SA_PAGE, SA_PROJECT), tmp_path)
    shutil.copy(os.path.join(_project(SA_PROJECT), "pages.DBF"), tmp_path)
    # (the page's parent, written here: the fixture's row has none)
    assert Project(str(tmp_path)).update_page_record(SA_PAGE, {"PARENT": "FxSaContent"})
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    doc = EditableDocument.open(str(tmp_path / (SA_PAGE + ".ctg")))
    dlg = PagePropertiesDialog(doc, Project(str(tmp_path)))

    assert dlg.windowTitle() == SA_PAGE + " Properties"
    assert [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())] == [
        "General",
        "Appearance",
        "Keyboard Commands",
        "Events",
        "Environment",
        "Associations",
    ]
    # the Appearance tab mirrors CtDraw's screenshot values: the template
    # picker (read-only Style / Resolution drop-downs) has the page's template
    p = dlg._tpl_picker
    assert isinstance(p, TemplatePicker)
    assert p.cb_style.currentText() == "fx_sa" and not p.cb_style.isEditable()
    assert p.cb_res.currentText() == "HD1080 (1920x1080, 16:9)" and not p.cb_res.isEditable()
    assert p.current_name() == "normal"
    assert p.title_bar() is False
    assert p.value()[1] == "normal@-HD1080" and not p.is_missing()
    assert (dlg._sp_w.value(), dlg._sp_h.value()) == (1920, 1080)
    for col in ("PREV_PAGE", "NEXT_PAGE", "PARENT"):
        assert isinstance(dlg._dbf_edits[col], PageField)
        assert dlg._dbf_edits[col].itemText(0) == "<None>"
    assert dlg._cb_quality.currentText() == "Default Indication"
    assert dlg._dbf_edits["TITLE"].text() == SA_TITLE
    assert dlg._dbf_edits["PARENT"].currentText() == "FxSaContent"
    # SA association entries surface under their stored names (the AVEVA help
    # calls them "__EquipmentName" and "__EquipmentRef")
    names = {dlg._assoc_table.item(r, 0).text() for r in range(dlg._assoc_table.rowCount())}
    assert "__EquipmentRef" in names and "__EquipmentName" in names

    # apply: a DBF field + the view area -- the pages.DBF edit is STAGED in the
    # document (CtDraw writes the row when the page is saved) and lands on save
    dlg._dbf_edits["TITLE"].setText("Edited")
    dlg._sp_w.setValue(1900)
    assert dlg.apply() is True and dlg.changed is True
    assert doc.page_record()["TITLE"] == "Edited"
    assert Project(str(tmp_path)).page_record(SA_PAGE)["TITLE"] == SA_TITLE
    assert doc.page.appearance.width == 1900
    doc.save()
    assert Project(str(tmp_path)).page_record(SA_PAGE)["TITLE"] == "Edited"


# ------------------------------------------------ Events / Ignore Quality / Associations
#: a second SA page (FxSa), its associations as stored ("''" defaults, no descriptions)
CAP_LABEL = "FxSaContent"


def _qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets, QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _sa_page_dialog(tmp_path, page=SA_PAGE, project=SA_PROJECT):
    """A PagePropertiesDialog on a COPY of a page of the fake install (FxSa's FxSaMaster
    by default) + its project's pages.DBF, in tmp_path (no include chain)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    _qt()
    shutil.copy(_page(page, project), tmp_path)
    shutil.copy(os.path.join(_project(project), "pages.DBF"), tmp_path)
    doc = EditableDocument.open(str(tmp_path / (page + ".ctg")))
    return doc, PagePropertiesDialog(doc, Project(str(tmp_path)))


def _assoc_rows(dlg):
    """The editable cells (Name, Default, Value on Error, Description); the
    read-only In Use column is tested on its own (test_super_genies)."""
    t = dlg._assoc_table
    return [[t.item(r, c).text() for c in range(4)] for r in range(t.rowCount())]


def _tab(dlg, text):
    return [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())].index(text)


def test_events_tab_labels_follow_ctdraw_and_write_their_columns(tmp_path):
    """CtDraw's Events list and the
    pages.DBF column behind each entry: 'While page
    shown' is BGND_FUNC."""
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import _EVENT_FIELDS

    QtWidgets, _app = _qt()
    from PySide6 import QtCore

    assert _EVENT_FIELDS == [
        ("On page entry:", "ENTRY_FUNC"),
        ("On page exit:", "EXIT_FUNC"),
        ("While page shown:", "BGND_FUNC"),
        ("On page shown:", "READY_FUNC"),
        ("On activate:", "ACT_FUNC"),
        ("On deactivate:", "DEACT_FUNC"),
    ]
    doc, dlg = _sa_page_dialog(tmp_path)
    # the tab lists exactly these events, in this order, on these columns
    ev = dlg._events
    shown = [
        (ev.events.item(r).text(), ev.events.item(r).data(QtCore.Qt.UserRole))
        for r in range(ev.events.count())
    ]
    assert shown == [(lbl.rstrip(":"), col) for lbl, col in _EVENT_FIELDS]
    assert ev.cmd._limit == 254

    ev.set_command("BGND_FUNC", "Background_Calc()")
    ev.set_command("READY_FUNC", "Page_Shown()")
    # a command ticks its event (CtDraw's check marks)
    checked = {
        ev.events.item(r).data(QtCore.Qt.UserRole)
        for r in range(ev.events.count())
        if ev.events.item(r).checkState() == QtCore.Qt.Checked
    }
    assert {"BGND_FUNC", "READY_FUNC"} <= checked
    assert dlg.apply() is True and not dlg.apply_error
    doc.save()
    rec = Project(str(tmp_path)).page_record(SA_PAGE)
    assert rec["BGND_FUNC"] == "Background_Calc()"
    assert rec["READY_FUNC"] == "Page_Shown()"
    assert rec["ACT_FUNC"] == "" and rec["DEACT_FUNC"] == ""


def test_ignore_quality_offers_the_four_indications_and_round_trips(tmp_path):
    """IGNOREVQ: Default -1 / #COM 0 / No 1 / Background 2 (CtDraw's
    labels)."""
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    doc, dlg = _sa_page_dialog(tmp_path)
    cb = dlg._cb_quality
    assert [(cb.itemText(i), cb.itemData(i)) for i in range(cb.count())] == [
        ("Default Indication", "-1"),
        ("#COM Indication", "0"),
        ("No Indication", "1"),
        ("Background Indication", "2"),
    ]
    cb.setCurrentIndex(cb.findText("Background Indication"))
    assert dlg.apply() is True
    proj = Project(str(tmp_path))
    assert doc.page_record()["IGNOREVQ"] == "2"
    # a fresh dialog reads it back (the staged value); an untouched combo
    # writes nothing
    dlg2 = PagePropertiesDialog(doc, proj)
    assert dlg2._cb_quality.currentText() == "Background Indication"
    assert dlg2.apply() is False
    dlg2._cb_quality.setCurrentIndex(dlg2._cb_quality.findText("#COM Indication"))
    assert dlg2.apply() is True
    doc.save()
    assert proj.page_record(SA_PAGE)["IGNOREVQ"] == "0"
    # a value CtDraw does not list survives an untouched Apply
    assert proj.update_page_record(SA_PAGE, {"IGNOREVQ": "7"})
    dlg3 = PagePropertiesDialog(doc, proj)
    assert dlg3._cb_quality.currentData() == "7"
    dlg3._dbf_edits["TITLE"].setText("Other")
    assert dlg3.apply() is True
    doc.save()
    assert proj.page_record(SA_PAGE)["IGNOREVQ"] == "7"
    assert proj.page_record(SA_PAGE)["TITLE"] == "Other"


def test_association_edit_writes_list2_and_survives_save(tmp_path):
    """Page Properties > Associations edits attributes LIST 2. FxSaContent's associations:
    __EquipmentRef and __EquipmentName with the default "''" and no description."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg

    doc, dlg = _sa_page_dialog(tmp_path, CAP_LABEL)
    at = doc.parsed.attributes
    list1_before = list(at.list1.pairs)
    stored = list(at.list2.pairs)  # (CtDraw's three-field form, "''\n\n")
    assert _assoc_rows(dlg) == [["__EquipmentRef", "''", "", ""], ["__EquipmentName", "''", "", ""]]
    assert [dlg._assoc_table.horizontalHeaderItem(c).text() for c in range(4)] == [
        "Name",
        "Default",
        "Value on Error",
        "Description",
    ]

    t = dlg._assoc_table
    t.item(0, 1).setText("'FxPlant.Tank1'")
    t.item(0, 2).setText("no equipment")
    assert dlg.apply() is True and not dlg.apply_error
    assert list(at.list1.pairs) == list1_before  # no list-1 key added
    assert "__EquipmentRef" not in doc.page_properties()
    assert at.list2.pairs == [
        ("__EquipmentRef", "'FxPlant.Tank1'\nno equipment\n"),
        stored[1],
    ]  # untouched: as stored
    assert dlg.apply() is False  # nothing left to write

    out = str(tmp_path / "saved.ctg")
    doc.save(out)
    re_at = parse_ctg(out).attributes
    assert re_at.list1.pairs == list1_before
    assert re_at.list2.pairs == at.list2.pairs
    re_doc = EditableDocument.open(out)
    assert re_doc.associations()[0] == ("__EquipmentRef", "'FxPlant.Tank1'", "no equipment", "")

    # one undo step restores the stored list
    doc.undo()
    assert at.list2.pairs == stored


def test_associations_tab_lists_non_underscore_super_genie_associations():
    """Every list-2 entry is an association, not only the '__' ones: FxHd1080's
    FxEquipment and FxTitle (its list 1 holds PageCicodeOn), the super genie page
    !fx_popup's 'Tag' and !fx_detail's numbered '1', '2', '3' (default "''"); the
    Environment tab is list 1 only."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    _qt()
    dlg = PagePropertiesDialog(EditableDocument.open(_page("FxHd1080")), None)
    assert _assoc_rows(dlg) == [["FxEquipment", "''", "", ""], ["FxTitle", "'Fixture'", "", ""]]
    assert "FxEquipment" not in dlg._env_original and "PageCicodeOn" in dlg._env_original
    dlg = PagePropertiesDialog(EditableDocument.open(_page("!fx_popup")), None)
    assert _assoc_rows(dlg) == [["Tag", "''", "", ""]]
    dlg = PagePropertiesDialog(EditableDocument.open(_page("!fx_detail")), None)
    assert _assoc_rows(dlg) == [["1", "''", "", ""], ["2", "''", "", ""], ["3", "''", "", ""]]
    assert not dlg._check_associations()  # '1' is a valid name


def test_association_name_rules():
    from ctgedit.format.attributes import association_name_error, join_association, split_association

    for ok in ("_Inactive", "1", "Tag", "__EquipmentRef", "My\\Tag_2", "x" * 253):
        assert association_name_error(ok) is None, ok
    for bad, why in (
        ("", "Enter a name"),
        ("a b", "spaces"),
        ("Equip.Item", "period"),
        ("-Tag", "must start"),
        ("?Tag?", "must start"),
        ("Tag#1", "'#' is not allowed"),
        ("x" * 254, "at most 253"),
        ("Tag\u00e9", "is not allowed"),
    ):
        err = association_name_error(bad)
        assert err is not None and why in err, (bad, err)
    assert split_association("\n\nEquipment Reference") == ("", "", "Equipment Reference")
    assert split_association("''\n\n") == ("''", "", "")
    assert split_association("") == ("", "", "")
    assert join_association("'1.23'", "none", "d") == "'1.23'\nnone\nd"


def test_association_add_delete_and_validation_in_the_dialog(tmp_path, monkeypatch):
    """Add / Remove buttons; names are checked as you type; an invalid or
    duplicate name shows in the status line above the buttons, disables OK /
    Apply and refuses the whole Apply (nothing is written, OK keeps the
    dialog open, no message box)."""
    from ctgedit.io.project import Project

    QtWidgets, _app = _qt()
    doc, dlg = _sa_page_dialog(tmp_path)
    at = doc.parsed.attributes
    before = list(at.list2.pairs)
    t = dlg._assoc_table
    warned = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a)))
    assert [dlg._btn_assoc_add.text(), dlg._btn_assoc_edit.text(), dlg._btn_assoc_del.text()] == [
        "Add",
        "Edit",
        "Remove",
    ]
    assert not t.verticalHeader().isVisible()  # no row numbers

    dlg._btn_assoc_add.click()
    r = t.rowCount() - 1
    assert t.currentRow() == r and _assoc_rows(dlg)[r] == ["", "", "", ""]
    assert dlg._check_associations() == ""  # a blank row is ignored
    t.item(r, 0).setText("Equip.Item")  # itemChanged -> checked
    assert "period" in t.item(r, 0).toolTip()
    assert "period" in dlg._revalidate()
    assert "period" in dlg._status.text() and not dlg._status.isHidden()
    assert not dlg._btn_ok.isEnabled() and not dlg._btn_apply.isEnabled()
    dlg._dbf_edits["TITLE"].setText("Must not be written")
    dlg._tabs.setCurrentIndex(_tab(dlg, "General"))
    assert dlg.apply() is False and "period" in dlg.apply_error and not warned
    assert list(at.list2.pairs) == before
    assert Project(str(tmp_path)).page_record(SA_PAGE)["TITLE"] == SA_TITLE
    assert doc.page_record()["TITLE"] == SA_TITLE and not doc.can_undo()
    assert dlg._tabs.currentIndex() == _tab(dlg, "Associations")  # the tab of the field
    dlg._ok()
    assert dlg.result() != QtWidgets.QDialog.Accepted  # still open
    assert not warned

    t.item(r, 0).setText("__equipmentref")  # duplicate, ignoring case
    assert "already an association" in dlg._check_associations()
    t.item(r, 0).setText("Speed")
    t.item(r, 1).setText("'50'")
    t.item(r, 3).setText("Motor speed")
    assert dlg._check_associations() == "" and dlg._revalidate() == ""
    assert dlg._status.isHidden() and dlg._btn_ok.isEnabled()
    dlg._dbf_edits["TITLE"].setText(SA_TITLE)
    assert dlg.apply() is True
    assert at.list2.pairs == before + [("Speed", "'50'\n\nMotor speed")]

    # Delete the first row
    t.selectRow(0)
    dlg._btn_assoc_del.click()
    assert [row[0] for row in _assoc_rows(dlg)] == ["__EquipmentName", "Speed"]
    assert dlg.apply() is True
    assert [k for k, _v in at.list2.pairs] == ["__EquipmentName", "Speed"]
    assert "__EquipmentRef" not in doc.page_properties()


def test_stored_association_name_is_not_judged_until_retyped(tmp_path):
    """Like CtDraw (CtDraw checks the name being entered), a stored
    name that breaks the rules does not block the dialog; retyping it does."""
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    doc, _dlg = _sa_page_dialog(tmp_path)
    at = doc.parsed.attributes
    at.list2.pairs.append(("Old Name", "\n\n"))
    at.list2.count = len(at.list2.pairs)
    dlg = PagePropertiesDialog(doc, None)
    t = dlg._assoc_table
    assert dlg._check_associations() == ""
    t.item(0, 3).setText("Edited description")
    assert dlg.apply() is True
    assert at.list2.pairs[-1] == ("Old Name", "\n\n")
    last = t.rowCount() - 1
    t.item(last, 0).setText("Old Name2")
    assert "spaces" in dlg._check_associations()


def test_page_properties_help_button_opens_the_current_tab(tmp_path, monkeypatch):
    from ctgedit.ui import help as help_mod

    QtWidgets, _app = _qt()
    shown = []
    monkeypatch.setattr(help_mod, "show_help", lambda topic="index", *a, **k: shown.append(topic))
    doc, dlg = _sa_page_dialog(tmp_path)
    box = dlg.findChild(QtWidgets.QDialogButtonBox)
    btn = box.button(QtWidgets.QDialogButtonBox.Help)
    assert btn is not None and btn.isEnabled()
    for tab, anchor in (("Events", "events"), ("Associations", "associations"), ("General", "general")):
        dlg._tabs.setCurrentIndex(_tab(dlg, tab))
        btn.click()
        assert shown[-1] == "page-properties#" + anchor
    # the automatic dialog decoration must not add a second Help button
    help_mod._decorate_dialog(dlg)
    assert sum(1 for b in box.buttons() if box.buttonRole(b) == QtWidgets.QDialogButtonBox.HelpRole) == 1


def test_template_element_associations_are_trailer_record_b(tmp_path):
    """On a library element (here FxLib's super genie !fx_popup@SUPER, association Tag)
    the associations are trailer record B; an edit lands there, not in record A."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format.element import open_element

    lib = str(tmp_path / "fxsuper.ctm")
    shutil.copy(FX.file("FxLib", "fxsuper.ctm"), lib)
    d = ElementDocument.open_element(lib, "!fx_popup@SUPER")
    assert d.associations()[0] == ("Tag", "''", "", "")
    assert d.set_association("Tag", "'Area1'", "", "The tag") is True
    assert d.set_association("Tag", "'Area1'", "", "The tag") is False
    d.save()
    tr = open_element(lib, "!fx_popup@SUPER").trailer
    assert tr.associations == [("Tag", "'Area1'\n\nThe tag")]
    assert not any(k == "Tag" for k, _v in tr.props)


def test_association_document_api_validates_and_undoes(tmp_path):
    """EditableDocument.set_association / remove_association / set_associations:
    list 2 only, CtDraw's name rules for a new name, case-insensitive
    uniqueness, one undo step each."""
    from ctgedit.editing import EditableDocument

    shutil.copy(_page(SA_PAGE, SA_PROJECT), tmp_path)
    doc = EditableDocument.open(str(tmp_path / (SA_PAGE + ".ctg")))
    at = doc.parsed.attributes
    before1, before2 = list(at.list1.pairs), list(at.list2.pairs)
    with pytest.raises(ValueError, match="spaces"):
        doc.set_association("Bad Name", "'1'")
    with pytest.raises(ValueError, match="unique"):
        doc.set_associations(before2 + [("__EQUIPMENTREF", "\n\n")])
    assert at.list2.pairs == before2 and not doc.can_undo()

    assert doc.set_association("Speed", "'50'", "", "Motor speed") is True
    assert at.list2.pairs[-1] == ("Speed", "'50'\n\nMotor speed")
    assert doc.associations()[-1] == ("Speed", "'50'", "", "Motor speed")
    # same name ignoring case = the same row, kept in place
    assert doc.set_association("speed", "'60'") is True
    assert [k for k, _v in at.list2.pairs] == ["__EquipmentRef", "__EquipmentName", "speed"]
    assert doc.remove_association("SPEED") is True
    assert doc.remove_association("SPEED") is False
    assert at.list2.pairs == before2 and at.list1.pairs == before1
    doc.undo()
    assert at.list2.pairs[-1] == ("speed", "'60'\n\n")
    doc.undo()
    doc.undo()
    assert at.list2.pairs == before2


def test_association_fields_length_line_breaks_and_code_page(tmp_path, monkeypatch):
    """Association field length, line breaks and code page."""
    from ctgedit.io.project import Project

    QtWidgets, _app = _qt()
    doc, dlg = _sa_page_dialog(tmp_path)
    at = doc.parsed.attributes
    before = list(at.list2.pairs)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    t = dlg._assoc_table
    t.item(0, 3).setText("d" * 256)
    msg = dlg._check_associations()
    assert "description can be at most 255" in msg and t.item(0, 3).toolTip()
    assert dlg.apply() is False and at.list2.pairs == before
    t.item(0, 3).setText("d" * 255)
    assert dlg._check_associations() == "" and not t.item(0, 3).toolTip()

    t.item(0, 1).setText("'a\nb'")
    t.item(0, 3).setText("Equipment Reference")
    assert dlg.apply() is True
    assert at.list2.pairs[0] == ("__EquipmentRef", "'a b'\n\nEquipment Reference")

    t.item(1, 3).setText("arrow \u2192")
    dlg._dbf_edits["TITLE"].setText("Must not be written")
    assert dlg.apply() is False and "U+2192" in dlg.apply_error
    assert at.list2.pairs[1] == before[1]
    assert Project(str(tmp_path)).page_record(SA_PAGE)["TITLE"] == SA_TITLE
    assert doc.page_record()["TITLE"] == SA_TITLE


# --------------------------------------------- staged pages.DBF row + page keyboard commands
#: FxMain's page with a page-wide keyboard command and that command;
#: the page the staging tests edit (registered, no page keys, all
#: areas, the default scan time)
SUMMARY_PAGE = "FxMaster"
SUMMARY_CMD = "FxSet(FX_MODE, 0);\r\nFxSet(FX_MODE, 1);"
PUMP = "FxShapes"


def _summary():
    return _page(SUMMARY_PAGE)


def _mini_project(project, dst, pages, tables=("pgkey",)):
    """A new project ``dst`` (a folder name) in the fake install's User folder, holding
    ``pages`` of ``project`` (FxMain: their .ctg) and their rows in ``tables`` (+ fresh
    PAGE indexes); pages.DBF / changes.DBF / PROJECT.CIT / include.DBF and the small
    lookup tables copied whole."""
    dst = os.path.join(FX.user, dst)
    project = _project(project)
    import struct
    from ctgedit.format.dbf import Dbf, Ndx, create_ndx
    from ctgedit.io import pgregister as PR

    os.makedirs(dst, exist_ok=True)
    for f in (
        "pages.DBF",
        "changes.DBF",
        "PROJECT.CIT",
        "include.DBF",
        "cluster.DBF",
        "contype.DBF",
        "KEYS.DBF",
        "devices.DBF",
    ):
        src = PR._find_file(project, f)
        if src:
            shutil.copy(src, os.path.join(dst, os.path.basename(src)))
    for t in tables:
        src = PR._find_file(project, t + ".DBF")
        if src is None:
            continue
        d = Dbf(src, writable=False)
        n = PR._open_ndx(project, t)
        recs = [r for p in pages for _i, r in sorted(PR._page_records(d, n, "PAGE", p))]
        if n is not None:
            n.close()
        with open(src, "rb") as fh:
            head = bytearray(fh.read(d.hlen))
        struct.pack_into("<I", head, 4, len(recs))
        out = os.path.join(dst, t + ".DBF")
        with open(out, "wb") as fh:
            fh.write(bytes(head) + b"".join(recs) + b"\x1a")
        create_ndx(os.path.join(dst, t + ".NDX"), "PAGE", d.fmap["PAGE"][1])
        d.close()
        dd = Dbf(out)
        with Ndx(os.path.join(dst, t + ".NDX")) as nx:
            for i in range(dd.nrec):
                nx.insert(dd.field(dd.read(i), "PAGE"), i + 1)
        dd.close()
    for p in pages:
        shutil.copy(os.path.join(project, p + ".ctg"), os.path.join(dst, p + ".ctg"))
    return dst


def _live_rows(project_dir, table, page):
    """Live rows of ``page`` read with dbfread (an independent DBF reader)."""
    from dbfread import DBF
    from ctgedit.io import pgregister as PR

    path = PR._find_file(project_dir, table + ".DBF")
    return [
        dict(r)
        for r in DBF(path, encoding="cp1252", char_decode_errors="replace")
        if str(r.get("PAGE", "")).strip().lower() == page.lower()
    ]


def _page_keys(project_dir, page):
    return [
        (r["KEY_SEQ"], r["CMD"], r["AREA"], r["PRIV"], r["MSG_LOG"])
        for r in _live_rows(project_dir, "pgkey", page)
        if not str(r["AN"]).strip()
    ]


def _bytes(*paths):
    out = {}
    for p in paths:
        with open(p, "rb") as fh:
            out[p] = fh.read()
    return out


def _pump_dialog(tmp_path, tables=("pgkey",)):
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    _qt()
    proj = _mini_project("FxMain", "FxMini", [PUMP], tables)
    doc = EditableDocument.open(os.path.join(proj, PUMP + ".ctg"))
    return proj, doc, PagePropertiesDialog(doc, Project(proj))


def test_summary_keyboard_tab_shows_the_page_rows():
    """The page's keyboard commands are its pgkey rows with a BLANK AN (FxMain's
    FxMaster: LBUTTON_UP -> FxSet(FX_MODE, 0); ...), not page records -- read here from the
    fake install's copy (nothing is written)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    _qt()
    doc = EditableDocument.open(_summary())
    rows = doc.page_keyboard_commands()
    assert [(r["KEY_SEQ"], r["CMD"]) for r in rows] == [("LBUTTON_UP", SUMMARY_CMD)]
    dlg = PagePropertiesDialog(doc, Project(_project()))
    k = dlg._keys
    assert [k.key_list.item(i).text() for i in range(k.key_list.count())] == ["LBUTTON_UP"]
    assert k.cmd.value() == SUMMARY_CMD
    assert k.cmd.toPlainText() == SUMMARY_CMD.replace("\r\n", "\n")
    # no command area / privilege: the boxes are ticked, CtDraw's texts shown (the
    # disabled area menu shows the page's area: FxMaster's AREA is 2)
    assert k.same_area.isChecked() and k.area.currentText() == "2"
    assert not k.area.isEnabled()
    assert k.no_priv.isChecked() and k.priv.currentText() == "<None>"
    assert k.cmd_group.title() == "'LBUTTON_UP' command"
    assert dlg.apply() is False and not doc.can_undo() and not doc.dirty
    # a page without an area: '<All areas>'
    assert doc.set_page_record({"AREA": ""}) is True
    k = PagePropertiesDialog(doc, Project(_project()))._keys
    assert k.same_area.isChecked() and k.area.currentText() == "<All areas>"


def test_new_page_key_is_staged_then_saved_as_a_blank_an_row(tmp_path):
    """Adding F2 -> PageDisplay("FxGenies") changes nothing on disk until the
    page is saved; the save writes ONE pgkey row with a blank AN (indexed,
    logged in changes.DBF). An edit rewrites that row in place, a delete
    flags it deleted."""
    from ctgedit.format.dbf import Dbf
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    proj, doc, dlg = _pump_dialog(tmp_path)
    pgkey = os.path.join(proj, "pgkey.DBF")
    before = _bytes(pgkey, os.path.join(proj, "pgkey.NDX"), os.path.join(proj, "pages.DBF"))
    assert _page_keys(proj, PUMP) == []
    k = dlg._keys
    k.add_key_sequence("F2")
    k.cmd.setPlainText('PageDisplay("FxGenies")')
    k.log.setText("FxGenies shown")
    k.same_area.setChecked(False)
    assert k.area.isEnabled() and k.area.currentText() == "1"  # CtDraw: the first entry
    k.area.setEditText("5")
    k.no_priv.setChecked(False)
    k.priv.setEditText("2")
    assert dlg.apply() is True and doc.dirty and doc.can_undo()
    assert _bytes(*before) == before  # staged, nothing written yet
    assert [
        (r["KEY_SEQ"], r["CMD"], r["AREA"], r["PRIV"], r["MSG_LOG"]) for r in doc.page_keyboard_commands()
    ] == [("F2", 'PageDisplay("FxGenies")', "5", "2", "FxGenies shown")]

    doc.save()
    assert not doc.dirty
    assert _page_keys(proj, PUMP) == [("F2", 'PageDisplay("FxGenies")', "5", "2", "FxGenies shown")]
    _index_consistent(proj, "pgkey")
    with Dbf(os.path.join(proj, "pages.DBF"), writable=False) as pg:
        ((recno, _r),) = [x for x in pg.find("NAME", PUMP) if x[1][:1] != b"*"]
    ch = Dbf(os.path.join(proj, "changes.DBF"), writable=False)
    logged = [ch.decode(ch.read(i)) for i in range(ch.nrec)]
    ch.close()
    assert {"NAME": "pages", "KEY": PUMP, "RECORD_NO": str(recno + 1)} in logged

    # an edit rewrites the SAME record (no second row)
    with Dbf(pgkey, writable=False) as t:
        n0 = t.nrec
    dlg2 = PagePropertiesDialog(doc, Project(proj))
    dlg2._keys.key_list.setCurrentRow(0)
    dlg2._keys.cmd.setPlainText('PageDisplay("FxMaster")')
    assert dlg2.apply() is True
    doc.save()
    with Dbf(pgkey, writable=False) as t:
        assert t.nrec == n0
    assert _page_keys(proj, PUMP) == [("F2", 'PageDisplay("FxMaster")', "5", "2", "FxGenies shown")]
    # Delete + Apply + Save: the row is flagged deleted
    dlg3 = PagePropertiesDialog(doc, Project(proj))
    dlg3._keys.key_list.setCurrentRow(0)
    dlg3._keys.btn_del.click()
    assert dlg3._keys.key_list.count() == 0
    assert dlg3.apply() is True
    doc.save()
    assert _page_keys(proj, PUMP) == []
    _index_consistent(proj, "pgkey")


def _index_consistent(dst, table):
    from ctgedit.format.dbf import Dbf, Ndx

    d = Dbf(os.path.join(dst, table + ".DBF"), writable=False)
    with Ndx(os.path.join(dst, table + ".NDX")) as n:
        assert sorted(r for _k, r in n.entries()) == list(range(1, d.nrec + 1))
    d.close()


def test_page_record_edits_are_staged_undoable_and_discarded(tmp_path):
    """pages.DBF edits live in the document until Save: one undo step each,
    an Undo reverts an area change, and a page closed with 'Discard changes'
    leaves pages.DBF as it was. A value the column cannot hold is refused
    with the reason."""
    from ctgedit.editing import EditableDocument

    proj, doc, dlg = _pump_dialog(tmp_path, tables=())
    pages = os.path.join(proj, "pages.DBF")
    before = _bytes(pages)
    title0 = doc.page_record()["TITLE"]
    assert doc.set_page_record({"TITLE": "FxShapes (edited)"}) is True
    assert doc.set_page_record({"TITLE": "FxShapes (edited)"}) is False  # no change
    assert doc.page_record()["TITLE"] == "FxShapes (edited)" and doc.dirty
    assert doc.page_record_on_disk()["TITLE"] == title0
    doc.undo()
    assert doc.page_record()["TITLE"] == title0
    doc.redo()
    assert doc.page_record()["TITLE"] == "FxShapes (edited)"

    # an area change made in the dialog, undone
    assert dlg._chk_all_areas.isChecked() and dlg._cb_area.currentText() == "<All areas>"
    dlg._chk_all_areas.setChecked(False)
    dlg._cb_area.setEditText("7")
    assert dlg.apply() is True
    assert doc.page_record()["AREA"] == "7"
    doc.undo()
    assert doc.page_record()["AREA"] == ""
    assert _bytes(pages) == before
    # 'Discard changes': the document goes, pages.DBF was never written
    del dlg, doc
    assert _bytes(pages) == before
    doc2 = EditableDocument.open(os.path.join(proj, PUMP + ".ctg"))
    assert doc2.page_record()["TITLE"] == title0
    with pytest.raises(ValueError, match="at most 64 characters"):
        doc2.set_page_record({"TITLE": "x" * 65})
    with pytest.raises(ValueError, match="60000"):
        doc2.set_page_record({"SCANTIME": "70000"})
    with pytest.raises(ValueError, match="U\\+2192"):
        doc2.set_page_record({"COMMENT": "arrow \u2192"})
    assert not doc2.can_undo()


def test_general_tab_writes_ctdraws_encodings(tmp_path):
    """All areas = no AREA; Inherit from caller = CINHERIT 'TRUE' and no
    CLUSTER; Default scan time = no SCANTIME; the description
    keeps its line breaks (CRLF); a fresh dialog reads it all back."""
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    proj, doc, dlg = _pump_dialog(tmp_path, tables=())
    assert dlg._chk_scan_default.isChecked() and not dlg._sp_scan.isEnabled()
    assert not dlg._chk_inherit.isChecked() and dlg._cb_cluster.isEnabled()
    prev = dlg._dbf_edits["PREV_PAGE"]
    prev.fill()  # (the names are listed when it opens)
    assert prev.findText("FxGenies") >= 0  # the project's pages
    assert dlg._cb_logdevice.findText("<None>") == 0
    dlg._chk_all_areas.setChecked(False)
    dlg._cb_area.setEditText("12")
    dlg._chk_inherit.setChecked(True)
    assert not dlg._cb_cluster.isEnabled()
    dlg._chk_scan_default.setChecked(False)
    assert dlg._sp_scan.isEnabled()
    dlg._sp_scan.setValue(500)
    dlg._desc.setPlainText("line one\nline two")
    dlg._title.setText("Fx shapes")
    assert dlg.apply() is True
    rec = doc.page_record()
    assert (rec["AREA"], rec["CINHERIT"], rec["CLUSTER"], rec["SCANTIME"]) == ("12", "TRUE", "", "500")
    assert rec["COMMENT"] == "line one\r\nline two" and rec["TITLE"] == "Fx shapes"
    doc.save()
    disk = Project(proj).page_record(PUMP)
    for k in ("AREA", "CINHERIT", "CLUSTER", "SCANTIME", "COMMENT", "TITLE"):
        assert disk[k] == rec[k], k
    dlg2 = PagePropertiesDialog(doc, Project(proj))
    assert not dlg2._chk_all_areas.isChecked() and dlg2._cb_area.currentText() == "12"
    assert dlg2._chk_inherit.isChecked() and not dlg2._cb_cluster.isEnabled()
    assert not dlg2._chk_scan_default.isChecked() and dlg2._sp_scan.value() == 500
    assert dlg2._desc.value() == "line one\r\nline two"
    # back to the defaults: the keys are emptied again
    dlg2._chk_all_areas.setChecked(True)
    dlg2._chk_inherit.setChecked(False)
    dlg2._chk_scan_default.setChecked(True)
    assert dlg2.apply() is True
    rec = doc.page_record()
    assert (rec["AREA"], rec["CINHERIT"], rec["SCANTIME"]) == ("", "", "")


def test_save_as_takes_the_staged_page_properties(tmp_path):
    """Save As copies the source page's row and keyboard commands to the new
    name WITH the staged edits; the source page keeps its own."""
    from ctgedit.io.project import Project

    proj, doc, _dlg = _pump_dialog(tmp_path)
    src_row = Project(proj).page_record(PUMP)
    assert doc.set_page_record({"TITLE": "Copy of pump A"}) is True
    assert doc.set_page_keyboard_commands([{"KEY_SEQ": "F3", "CMD": "Beep()"}]) is True
    doc.save(os.path.join(proj, "PumpCopy.ctg"))
    row = Project(proj).page_record("PumpCopy")
    assert row["TITLE"] == "Copy of pump A"
    for k, v in src_row.items():
        if k not in ("NAME", "TITLE"):
            assert row[k] == v, k
    assert Project(proj).page_record(PUMP) == src_row
    assert _page_keys(proj, "PumpCopy") == [("F3", "Beep()", "", "", "")]
    assert _page_keys(proj, PUMP) == []
    assert not doc.dirty and doc.staged_page_state() == {}


def test_unregistered_page_properties_are_written_by_its_registration(tmp_path):
    """A page in a project folder with no pages.DBF row yet: Page Properties
    is editable, shows an empty title like CtDraw, and the
    first save registers the page WITH the edits (an untouched title = the name)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    _qt()
    proj = _mini_project("FxMain", "FxMini", [PUMP])
    page = os.path.join(proj, "BrandNew.ctg")
    shutil.copy(os.path.join(proj, PUMP + ".ctg"), page)
    doc = EditableDocument.open(page)
    assert not doc.page_record_registered()
    dlg = PagePropertiesDialog(doc, Project(proj))
    assert dlg._title.isEnabled() and dlg._title.text() == ""
    assert doc.page_record()["TITLE"] == "BrandNew"  # what the registration writes
    dlg._title.setText("A brand new page")
    dlg._keys.add_key_sequence("ESC", "WinFree()")
    assert dlg.apply() is True
    assert Project(proj).page_record("BrandNew") is None  # nothing written yet
    doc.save()
    assert Project(proj).page_record("BrandNew")["TITLE"] == "A brand new page"
    assert _page_keys(proj, "BrandNew") == [("ESC", "WinFree()", "", "", "")]


def test_unchanged_page_keys_are_not_rewritten(tmp_path):
    """set_page_keyboard_rows writes nothing when the page already has these
    rows (a save with untouched keyboard commands leaves pgkey.DBF as is)."""
    from ctgedit.io import pgregister as PR

    proj = _mini_project("FxMain", "FxMini", [SUMMARY_PAGE])
    pgkey = os.path.join(proj, "pgkey.DBF")
    rows = PR.page_keyboard_rows(proj, SUMMARY_PAGE)
    assert [r["KEY_SEQ"] for r in rows] == ["LBUTTON_UP"]
    before = _bytes(pgkey)
    assert PR.set_page_keyboard_rows(proj, SUMMARY_PAGE, rows) == 1
    assert _bytes(pgkey) == before
    rows.append({"KEY_SEQ": "F2", "CMD": 'PageDisplay("FxGenies")'})
    assert PR.set_page_keyboard_rows(proj, SUMMARY_PAGE, rows) == 2
    assert [r["KEY_SEQ"] for r in PR.page_keyboard_rows(proj, SUMMARY_PAGE)] == ["LBUTTON_UP", "F2"]
    _index_consistent(proj, "pgkey")


def test_recovery_copy_keeps_the_staged_page_properties(tmp_path):
    """A crash recovery copy holds the page bytes AND the staged pages.DBF /
    keyboard edits; the restored document writes them on its save."""
    from ctgedit.infra import autosave
    from ctgedit.io.project import Project

    proj, doc, _dlg = _pump_dialog(tmp_path)
    doc.set_page_record({"AREA": "3"})
    doc.set_page_keyboard_commands([{"KEY_SEQ": "F4", "CMD": "Beep()"}])
    snap = autosave.save_document(doc)
    re_doc = autosave.restore_document(autosave.read(snap.meta_path))
    assert re_doc.dirty and re_doc.page_record()["AREA"] == "3"
    assert [r["KEY_SEQ"] for r in re_doc.page_keyboard_commands()] == ["F4"]
    re_doc.save()
    assert Project(proj).page_record(PUMP)["AREA"] == "3"
    assert _page_keys(proj, PUMP) == [("F4", "Beep()", "", "", "")]


def test_save_keeps_the_page_unsaved_when_the_database_refuses(tmp_path, monkeypatch):
    """A locked pages.DBF: the .ctg is saved, the page properties stay staged
    and the page stays 'unsaved', with a warning that says so."""
    from ctgedit.io.project import Project

    proj, doc, _dlg = _pump_dialog(tmp_path, tables=())
    doc.set_page_record({"TITLE": "Locked out"})

    def locked(*_a, **_k):
        raise PermissionError(13, "The process cannot access the file")

    with monkeypatch.context() as m:
        m.setattr(Project, "update_page_record", locked)
        doc.save()
    assert doc.dirty and doc.page_record()["TITLE"] == "Locked out"
    assert "NOT written to the project database" in doc.save_warning
    doc.save()
    assert not doc.dirty and Project(proj).page_record(PUMP)["TITLE"] == "Locked out"


def test_events_tab_multiline_commands_advice_and_clear(tmp_path):
    """The Events tab edits one event at a time (a check mark per event with
    a command): multi-line commands are stored with CRLF, the help's rules
    are advised (no Login() on page entry, no page navigation on page exit),
    unticking an event clears its command and Clear Property clears all."""
    from PySide6 import QtCore
    from ctgedit.ui.dialog.page_props import event_warnings

    doc, dlg = _sa_page_dialog(tmp_path)
    ev = dlg._events
    ev.set_command("ENTRY_FUNC", 'Login("op", "x")')
    assert "Login" in ev.advice.text()
    ev.set_command("EXIT_FUNC", 'PageDisplay("FxGenies")')
    assert "PageDisplay" in ev.advice.text()
    assert event_warnings("READY_FUNC", 'Login("op", "x")') == []
    assert "On page shown" in event_warnings("ENTRY_FUNC", 'ObjectByName("AN12")')[0]
    ev.set_command("ENTRY_FUNC", "InitRecipe()\nShowIt()")
    assert dlg.apply() is True
    rec = doc.page_record()
    assert rec["ENTRY_FUNC"] == "InitRecipe()\r\nShowIt()"
    assert rec["EXIT_FUNC"] == 'PageDisplay("FxGenies")'
    row = [ev.events.item(r).data(QtCore.Qt.UserRole) for r in range(ev.events.count())].index("EXIT_FUNC")
    ev.events.item(row).setCheckState(QtCore.Qt.Unchecked)
    assert ev.values()["EXIT_FUNC"] == ""
    ev.clear()
    assert set(ev.values().values()) == {""}
    assert dlg.apply() is True
    rec = doc.page_record()
    assert all(rec[c] == "" for _l, c in _EVENTS)


_EVENTS = [
    ("On page entry:", "ENTRY_FUNC"),
    ("On page exit:", "EXIT_FUNC"),
    ("While page shown:", "BGND_FUNC"),
    ("On page shown:", "READY_FUNC"),
    ("On activate:", "ACT_FUNC"),
    ("On deactivate:", "DEACT_FUNC"),
]


def test_key_sequence_advice():
    """The key-sequence assist: '#' runs are arguments (at most 8), the
    command must read them (Arg1 / ArgValue1), '*' hot keys are system-only,
    and a sequence that starts another one makes the longer one unreachable."""
    from ctgedit.ui.objprops.input import (
        command_argument_warnings,
        key_sequence_arguments,
        key_sequence_warnings,
    )

    assert key_sequence_arguments("F3 ###, ### ENTER") == 2
    assert key_sequence_arguments("F2") == 0
    assert "system keyboard commands, not on a page" in key_sequence_warnings("*BACKSPACE", (), "page")[0]
    assert "never run" in key_sequence_warnings("F3", ["F3 F4"], "page")[0]
    assert "of this page is the same" in key_sequence_warnings("F2", ["f2"], "page")[0]
    assert not key_sequence_warnings("F3 ENTER", ["F3 F4 ENTER"], "page")
    assert "Arg2" in command_argument_warnings("F3 ### ENTER", "SP1 = Arg1; SP2 = Arg2;")[0]
    assert "does not use" in command_argument_warnings("F3 ### ENTER", "Beep()")[0]
    assert not command_argument_warnings("F3 ###, ### ENTER", "SP1 = Arg1; SP2 = ArgValue2;")
    assert "at most 8" in command_argument_warnings(" ".join(["#"] * 9) + " ENTER", "X = Arg1")[0]


def test_key_menu_lists_the_include_chains_keys():
    """The key menu = argument patterns + the Keyboard Keys (keys.DBF) of the
    project and its include chain (Include's keys.DBF: ENTER, ESC, F2, LBUTTON_UP ...)
    + the mouse events the corpus uses."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops.input import key_sequence_choices

    names = {n.upper() for n in key_sequence_choices(EditableDocument.open(_summary()))}
    assert {"### ENTER", "F2", "ENTER", "ESC", "LBUTTON_UP"} <= names


def test_environment_lists_every_variable_but_ctdraws_own(tmp_path):
    """Environment = every list-1 key that does not start with '!' (CtDraw's
    PageCicodeOn is listed); Add of an existing name replaces
    its value (Edit Property box); '!' names are refused."""
    from ctgedit.ui.dialog.page_props import environment_name_problem

    doc, dlg = _sa_page_dialog(tmp_path, "FxHd1080", "FxMain")
    assert "PageCicodeOn" in dlg._env_original
    assert not any(k.startswith("!") for k in dlg._env_original)
    dlg.set_environment_variable("MyVar", "42")
    assert dlg.apply() is True and doc.page_properties()["MyVar"] == "42"
    dlg.set_environment_variable("MyVar", "43")
    assert dlg._env_table.rowCount() == len(dlg._env_original)
    assert dlg.apply() is True and doc.page_properties()["MyVar"] == "43"
    assert "cannot start with '!'" in environment_name_problem("!State")
    assert environment_name_problem("Ok_Name") == ""


def test_resolution_labels_follow_ctdraw():
    from ctgedit.ui.pick_fields import _RESOLUTION_LABELS, _RESOLUTION_ORDER

    assert _RESOLUTION_LABELS["VGA"] == "VGA (640x480, 4:3)"
    assert _RESOLUTION_LABELS["SVGA"] == "SVGA (800x600, 4:3)"
    assert _RESOLUTION_LABELS["WUXGA"] == "WUXGA (1920x1200, 16:10)"
    assert _RESOLUTION_LABELS["User"] == "User Defined"
    assert list(_RESOLUTION_ORDER[:3]) == ["VGA", "SVGA", "XGA"]


def test_object_access_page_follows_the_staged_page_area(tmp_path):
    """'Same area as page' on an object's Access page shows the page's area
    as the next save writes it."""
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    proj, doc, _dlg = _pump_dialog(tmp_path, tables=())
    doc.set_page_record({"AREA": "9"})
    r = doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    sheet = ObjectPropertiesDialog(doc, r)
    assert sheet.access_editor.area.placeholderText() == "9"


def test_main_window_page_properties_apply_is_staged_until_save(tmp_path):
    """Through the window: Apply marks the page unsaved and leaves the
    project database alone; Save writes it."""
    from ctgedit.io.project import Project
    from ctgedit.ui.main_window import MainWindow

    _qt()
    proj = _mini_project("FxMain", "FxMini", [PUMP], ())
    page = os.path.join(proj, PUMP + ".ctg")
    before = _bytes(os.path.join(proj, "pages.DBF"))
    win = MainWindow()
    try:
        win.open_document(page)
        win.page_properties()
        dlg = win._dialogs[("pageprops", "%x" % id(win.doc))]
        dlg._title.setText("FxShapes from the window")
        assert dlg.apply() is True
        assert win.doc.dirty and win.doc.can_undo()
        assert _bytes(*before) == before
        assert win._save_to() is True
        assert Project(proj).page_record(PUMP)["TITLE"] == "FxShapes from the window"
        dlg.reject()
    finally:
        if win in MainWindow._instances:
            MainWindow._instances.remove(win)
        win.deleteLater()


def test_main_window_undo_closes_page_properties_and_discard_writes_nothing(tmp_path, monkeypatch):
    """Undo in the window closes Page Properties, and discard writes nothing."""
    from PySide6 import QtWidgets
    from ctgedit.ui.main_window import MainWindow

    _qt()
    proj = _mini_project("FxMain", "FxMini", [PUMP], ())
    page = os.path.join(proj, PUMP + ".ctg")
    before = _bytes(os.path.join(proj, "pages.DBF"))
    win = MainWindow()
    try:
        win.open_document(page)
        doc = win.doc
        w0 = int(doc.page.appearance.width)
        key = ("pageprops", "%x" % id(doc))
        win.page_properties()
        dlg = win._dialogs[key]
        dlg._sp_w.setValue(w0 + 40)
        dlg._title.setText("Discard me")
        assert dlg.apply() is True
        assert int(doc.page.appearance.width) == w0 + 40
        win.undo()
        assert int(doc.page.appearance.width) == w0
        assert doc.page_record()["TITLE"] != "Discard me"
        dlg = win._dialogs.get(key)
        if dlg is not None:
            # still open: an Apply before its reload puts nothing undone back
            assert not dlg.has_pending_edits()
            dlg.apply()
            assert int(doc.page.appearance.width) == w0
            assert doc.page_record()["TITLE"] != "Discard me"
            dlg.reload()
            assert dlg._sp_w.value() == w0
            assert dlg._title.text() == doc.page_record()["TITLE"].strip()
            assert not dlg.has_pending_edits()
        win.redo()
        assert doc.page_record()["TITLE"] == "Discard me"
        monkeypatch.setattr(
            QtWidgets.QMessageBox, "question", lambda *_a, **_k: QtWidgets.QMessageBox.Discard
        )
        assert win._close_doc_tab(0) is not False
        assert _bytes(*before) == before
    finally:
        if win in MainWindow._instances:
            MainWindow._instances.remove(win)
        win.deleteLater()


def test_page_keys_need_a_pgkey_table(tmp_path):
    """A project without pgkey.DBF cannot take page keyboard commands: the
    document refuses to stage them (with the reason) and the tab says so; a
    table that vanishes before the save keeps them staged -- and the page
    properties saved with them roll back -- with a warning."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    _qt()
    proj = _mini_project("FxMain", "FxMini", [PUMP], ())
    doc = EditableDocument.open(os.path.join(proj, PUMP + ".ctg"))
    assert doc.page_record_available() and not doc.page_keyboard_available()
    with pytest.raises(ValueError, match="no pgkey.DBF"):
        doc.set_page_keyboard_commands([{"KEY_SEQ": "F2", "CMD": "Beep()"}])
    assert doc.set_page_keyboard_commands([]) is False and not doc.can_undo()
    dlg = PagePropertiesDialog(doc, Project(proj))
    assert not dlg._keys.btn_add.isEnabled()
    dlg._title.setText("Still editable")
    assert dlg.apply() is True and not dlg.apply_error

    proj2 = _mini_project("FxMain", "FxMini2", [PUMP])
    title0 = Project(proj2).page_record(PUMP)["TITLE"]
    doc2 = EditableDocument.open(os.path.join(proj2, PUMP + ".ctg"))
    assert doc2.set_page_record({"TITLE": "With keys"}) is True
    assert doc2.set_page_keyboard_commands([{"KEY_SEQ": "F2", "CMD": "Beep()"}]) is True
    os.remove(os.path.join(proj2, "pgkey.DBF"))
    os.remove(os.path.join(proj2, "pgkey.NDX"))
    doc2.save()
    assert doc2.dirty and "pgkey.DBF" in doc2.save_warning
    assert "NOT written to the project database" in doc2.save_warning
    # the title rolled back with the keyboard commands (one transaction)
    assert Project(proj2).page_record(PUMP)["TITLE"] == title0 != "With keys"
    assert doc2.page_record()["TITLE"] == "With keys"
    assert [r["KEY_SEQ"] for r in doc2.page_keyboard_commands()] == ["F2"]


def test_save_as_out_of_a_project_says_the_page_properties_are_dropped(tmp_path):
    """Save As into a folder that is not a project: the staged Page
    Properties edits have nowhere to go -- the save says so."""
    proj, doc, _dlg = _pump_dialog(tmp_path, tables=())
    doc.set_page_record({"TITLE": "Nowhere"})
    out = tmp_path / "loose"
    out.mkdir()
    doc.save(str(out / "Loose.ctg"))
    assert "were not stored" in doc.save_warning and "pages.DBF" in doc.save_warning
    assert not doc.dirty and doc.staged_page_state() == {}


# ------------------------------------------------ the shared pickers in Page Properties
def test_appearance_picks_another_template_and_apply_changes_it(tmp_path, monkeypatch):
    """Page Properties > Appearance: picking another template in the picker
    and Apply relinks the page to it (change_template, one undo step)."""
    monkeypatch.setenv("CTG_LIB_PATH", _project(SA_PROJECT))
    doc, dlg = _sa_page_dialog(tmp_path)
    p = dlg._tpl_picker
    assert dlg._new_template_ref() is None  # unchanged: nothing to do
    calls = []
    real = doc.change_template
    monkeypatch.setattr(doc, "change_template", lambda ref: calls.append(ref) or real(ref))
    # (fx_sa's other HD1080 forms are 1920 x 1080 like the page: the UHD4K one gives
    # the page another size)
    p.cb_res.setCurrentIndex(p.cb_res.findData(""))  # All resolutions
    row = next(i for i in range(p.list.count()) if p.list.item(i).data(0x0100) == ("normal", "UHD4K"))
    p.list.setCurrentRow(row)
    assert dlg._new_template_ref() == "fx_sa.normal@-UHD4K.<ctt"
    n = len(doc._undo)
    assert dlg.apply() is True and not dlg.apply_error
    assert calls == ["fx_sa.normal@-UHD4K.<ctt"]
    assert doc.template_reference()[1].element == "normal@-UHD4K"
    assert len(doc._undo) == n + 1
    assert dlg._new_template_ref() is None and p.current_name() == "normal"
    # the view area shows the size the template gave the page (the page is
    # derived again only when the Apply's batch ends): a second Apply must
    # not put the old height back
    size = (doc.page.appearance.width, doc.page.appearance.height)
    assert size != (1920, 1080)
    assert (dlg._sp_w.value(), dlg._sp_h.value()) == size
    assert dlg.apply() is False and len(doc._undo) == n + 1
    assert (doc.page.appearance.width, doc.page.appearance.height) == size
    doc.undo()
    assert doc.template_reference()[1].element == "normal@-HD1080"


def test_appearance_template_switch_keeps_a_typed_view_area(tmp_path, monkeypatch):
    """A view area typed in the same Apply as a template switch wins over
    the template's size."""
    monkeypatch.setenv("CTG_LIB_PATH", _project(SA_PROJECT))
    doc, dlg = _sa_page_dialog(tmp_path)
    p = dlg._tpl_picker
    row = next(i for i in range(p.list.count()) if p.list.item(i).text().startswith("blank"))
    p.list.setCurrentRow(row)
    dlg._sp_h.setValue(900)
    assert dlg.apply() is True
    assert doc.template_reference()[1].element == "blank@-HD1080"
    assert (doc.page.appearance.width, doc.page.appearance.height) == (1920, 900)
    assert (dlg._sp_w.value(), dlg._sp_h.value()) == (1920, 900)


def test_appearance_keeps_a_template_that_is_not_found(tmp_path):
    """No include chain for the copy: the page's template is not found. It
    stays shown as '(not found)' and an Apply does not touch it."""
    doc, dlg = _sa_page_dialog(tmp_path)
    p = dlg._tpl_picker
    assert p.is_missing() and p.value() is None
    assert "(not found)" in p.list.currentItem().text()
    assert dlg._new_template_ref() is None
    dlg._title.setText("Other title")
    assert dlg.apply() is True
    assert doc.template_reference()[1].element == "normal@-HD1080"


def test_general_scan_time_and_page_colour_fields(tmp_path):
    """The scan time unit is the spin box suffix; the page colour is a
    ColourButton titled 'Page Colour'; the keyboard buttons read Add... /
    Edit... / Remove."""
    QtWidgets, _app = _qt()
    from ctgedit.ui.colour_picker import ColourButton

    doc, dlg = _sa_page_dialog(tmp_path)
    assert dlg._sp_scan.suffix() == " ms"
    labels = [w.text() for w in dlg._general_page.findChildren(QtWidgets.QLabel)]
    assert "milliseconds" not in labels
    assert isinstance(dlg._btn_colour, ColourButton) and dlg._btn_colour._title == "Page Colour"
    keys = dlg._keys
    assert [keys.btn_add.text(), keys.btn_edit.text(), keys.btn_del.text()] == ["Add…", "Edit…", "Remove"]
    # a new page colour is staged and applied
    dlg._btn_colour.set_pair((0x123456, 0x123456))
    dlg._btn_colour.changed.emit()
    assert dlg.apply() is True
    assert doc.page.appearance.background_colour & 0xFFFFFF == 0x123456


def test_environment_is_edited_in_place_with_inline_checks(tmp_path, monkeypatch):
    """Environment rows are edited in the table; a bad name shows in the
    status line and blocks Apply (no message box)."""
    QtWidgets, _app = _qt()
    warned = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a)))
    doc, dlg = _sa_page_dialog(tmp_path)
    t = dlg._env_table
    assert not t.verticalHeader().isVisible()
    assert [dlg._btn_env_add.text(), dlg._btn_env_edit.text(), dlg._btn_env_del.text()] == [
        "Add",
        "Edit",
        "Remove",
    ]
    dlg._btn_env_add.click()
    r = t.rowCount() - 1
    t.item(r, 0).setText("!State")
    t.item(r, 1).setText("1")
    assert "cannot start with '!'" in dlg._revalidate()
    assert not dlg._btn_ok.isEnabled()
    assert dlg.apply() is False and dlg._tabs.currentIndex() == _tab(dlg, "Environment")
    assert not warned and "!State" not in doc.page_properties()
    t.item(r, 0).setText("MyVar")
    assert dlg._revalidate() == ""
    assert dlg.apply() is True and doc.page_properties()["MyVar"] == "1"
