"""Turns and mirrors in a row, against CtDraw."""

import os
import shutil

import pytest

import fxinstall

#: the 'turns' units of fixture_oracle.py this file checks: (unit, page, the selected
#: names, the operations). 'R' rotate, 'H' mirror horizontal, 'S' save, 'O' save and open
#: again, ('at', x, y) PositionAt of the selection, ('select', names) a new selection.
TURNS = (
    ("T01_point_turn", "FxArrange", ["FxPointGenie"], ["R"]),
    ("T02_point_two_turns", "FxArrange", ["FxPointGenie"], ["R", "R"]),
    ("T03_point_text_turn", "FxArrange", ["FxPointGenie", "FxLoneText"], ["R"]),
    ("T04_point_text_four_turns", "FxArrange", ["FxPointGenie", "FxLoneText"], ["R", "R", "R", "R"]),
    ("T05_point_text_turn_save_turn", "FxArrange", ["FxPointGenie", "FxLoneText"], ["R", "S", "R"]),
    (
        "T06_point_text_turn_move_turn",
        "FxArrange",
        ["FxPointGenie", "FxLoneText"],
        ["R", ("select", ["FxLoneText"]), ("at", 100, 100), ("select", ["FxPointGenie", "FxLoneText"]), "R"],
    ),
    ("T07_three_turns_reopen_turn", "FxArrange", ["FxPointGenie", "FxLoneText"], ["R", "R", "R", "O", "R"]),
    ("T08_mirror_two_turns", "FxArrange", ["FxPointGenie", "FxLoneText"], ["H", "R", "R"]),
    ("T09_text_mirror_move", "FxArrange", ["FxLoneText"], ["H", ("at", 200, 200)]),
    ("T10_text_with_key_turn", "FxArrange", ["FxKeyText"], ["R"]),
    # (a text in a plain group: its extent measured again, its Bounds the old box turned)
    ("T11_big_group_turn", "FxArrange", ["FxBigGroup"], ["R"]),
    ("T12_carrier_turn", "FxArrange", ["FxCarrier", "FxSameSize"], ["R"]),
    ("T13_symbol_set_turn", "FxArrange", ["FxTripleSet"], ["R"]),
    ("T14_symbol_turn", "FxMotion", ["FxSymRotate"], ["R"]),
    ("T15_symbol_line_turn", "FxMotion", ["FxSymRotate", "FxNeedle1"], ["R"]),
    ("T16_stretched_symbol_mirror", "FxMotion", ["FxSymStretched"], ["H"]),
    ("T17_full_ellipse_turn", "FxShapes", ["FxEllipse"], ["R"]),
    ("T18_pie_turn", "FxShapes", ["FxPie"], ["R"]),
    ("T19_line_turn_mirror", "FxShapes", ["FxLine"], ["R", "H"]),
    ("T20_gradient_mirror", "FxShapes", ["FxGradient"], ["H"]),
    ("T21_composite_turn", "FxComposites", ["FxComp1"], ["R"]),
    ("T22_composite_mirror", "FxComposites", ["FxComp1"], ["H"]),
    ("T23_text_turn", "FxTextKinds", ["FxTk1"], ["R"]),
    ("T24_cicode_point_turn", "FxCicode", ["FxCicodePoint"], ["R", "R"]),
    ("T25_group_symbol_turn", "FxSymbols", ["FxGroupSymbol"], ["R"]),
    ("T26_point_text_two_turns", "FxArrange", ["FxPointGenie", "FxLoneText"], ["R", "R"]),
    # (the genie alone turns about a whole-pixel centre: CtDraw saves what T02 saves)
    ("T27_point_turn_reopen_turn", "FxArrange", ["FxPointGenie"], ["R", "O", "R"]),
    # (a reopen keeps a genie member's half pixel, as the save does in T05: the members'
    # Totals store it)
    ("T28_point_text_turn_reopen_turn", "FxArrange", ["FxPointGenie", "FxLoneText"], ["R", "O", "R"]),
    # (a save keeps every Local in memory with its zero signs: T29 equals T08; a genie in a
    # plain group takes the group's Local, which the save composes onto its own: T30)
    (
        "T29_point_text_mirror_save_two_turns",
        "FxArrange",
        ["FxPointGenie", "FxLoneText"],
        ["H", "S", "R", "R"],
    ),
    ("T30_genie_in_group_mirror_save_two_turns", "FxMembers", ["FxGenieInGroup"], ["H", "S", "R", "R"]),
    # (the group rule across a save, and for a mirrored text in a plain group too: T32's
    # text stores (1, +0.0, +0.0, -1) mode 0x27, its own Local turned gives B -0.0)
    ("T31_genie_in_group_turn_save_turn", "FxMembers", ["FxGenieInGroup"], ["R", "S", "R"]),
    ("T32_pair_mirror_save_two_turns", "FxMembers", ["FxPair"], ["H", "S", "R", "R"]),
    # (the group's Local holds a mirror and two turns with no save between; a save drops a
    # grouped text's box in memory)
    ("T33_genie_in_group_mirror_two_turns", "FxMembers", ["FxGenieInGroup"], ["H", "R", "R"]),
    ("T34_big_group_turn_save_turn", "FxArrange", ["FxBigGroup"], ["R", "S", "R"]),
    # (the mode bytes ORed across a save, for a genie and a text in a plain group; a
    # grouped text's turn held in the group's Local, which its mirror composes onto, and
    # the group's box in memory taking the text's: T37)
    ("T35_genie_in_group_mirror_turn_save_turn", "FxMembers", ["FxGenieInGroup"], ["H", "R", "S", "R"]),
    ("T36_pair_mirror_turn_save_turn", "FxMembers", ["FxPair"], ["H", "R", "S", "R"]),
    ("T37_pair_turn_mirror_save_turn", "FxMembers", ["FxPair"], ["R", "H", "S", "R"]),
    # (a save drops the half pixels of the plain shapes in a plain group)
    ("T38_big_group_text_turn_save_turn", "FxArrange", ["FxBigGroup", "FxLoneText"], ["R", "S", "R"]),
    # (a top-level text's turn held in its own Local, which its mirror composes onto; and
    # whether a save makes the turn the orient for good: waiting for their capture)
    ("T39_text_turn_mirror", "FxArrange", ["FxLoneText"], ["R", "H"]),
    ("T40_text_turn_save_mirror", "FxArrange", ["FxLoneText"], ["R", "S", "H"]),
    # (a move of a plain group with a turned text: whether the text keeps its Bounds, the
    # box in memory, and the mirror centres on the group's box in memory moved along:
    # waiting for its capture)
    ("T41_pair_turn_move_mirror", "FxMembers", ["FxPair"], ["R", ("at", 100, 100), "H"]),
)
BY_ID = {t[0]: t for t in TURNS}


def _open(path):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(path)


def _roots(doc, names):
    out = []
    for n in names:
        found = doc.find_objects_by_name(n)
        assert len(found) == 1, (n, found)
        out.append(found[0])
    return out


def _position_at(doc, idx, x, y):
    """CtDraw's PositionAt(x, y): the selection moved so its Bounds' top-left is (x, y)
    (LIVE: a turned text whose Bounds start one pixel inside its extent's top
    moved by (-3, 113) to PositionAt(100, 100)): the Bounds CtDraw holds in memory, where a
    turned text has its old box turned (T06)."""
    box = doc.selection_extent(idx, bounds=True, held=True)
    doc.move_objects(idx, x - box[0], y - box[1])


def _run(doc, names, ops):
    """``ops`` on ``doc`` with ctgedit's edits; returns the document (a new one after 'O')."""
    sel = list(names)
    for op in ops:
        if op == "R":
            doc.rotate_objects(_roots(doc, sel))
        elif op == "H":
            doc.mirror_objects(_roots(doc, sel), horizontal=True)
        elif op == "V":
            doc.mirror_objects(_roots(doc, sel), horizontal=False)
        elif op == "S":
            doc.save()
        elif op == "O":
            doc.save()
            doc = _open(doc.path)
        elif op[0] == "at":
            _position_at(doc, _roots(doc, sel), op[1], op[2])
        elif op[0] == "select":
            sel = list(op[1])
        else:
            raise AssertionError("unknown operation %r" % (op,))
    return doc


def _state(doc, names):
    """Every record of the named objects as the page stores it: its type, its anchor and
    rects, its three placement slots (mode and matrix, repr: the zero signs count) and a
    text's orientation and echo."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format import geometry as G
    from ctgedit.format import placement as P

    objs = doc.parsed.page.objects
    out = []
    for n, r in zip(names, _roots(doc, names)):
        for k in doc.subtree(r):
            po = objs[k]
            if po.raw_payload is not None:
                out.append((n, k - r, po.type_code, "raw", bytes(po.raw_payload)))
                continue
            rec = GP.decode(po)
            try:
                slots = repr([s[:2] for s in P.read_slots(po.body_raw)[0]])
            except Exception:  # noqa: BLE001  (a record without slots)
                slots = None
            tail = G.parse_text_tail(po) if po.type_code == 0x05 else None
            out.append(
                (
                    n,
                    k - r,
                    po.type_code,
                    rec.anchor,
                    rec.rect1,
                    rec.rect2,
                    slots,
                    (tail["orient"], tail["echo"]) if tail else None,
                )
            )
    return out


def _ctdraw_state(fx_install, unit, page, names):
    """The named objects' records in the page CtDraw saved after ``unit`` (copied next to
    the fixture page, so it opens with the same libraries)."""
    src = fxinstall.oracle("turns", unit, "page.ctg")
    dst = os.path.join(fx_install.project("FxMain"), "FxOracle_%s.ctg" % page)
    shutil.copyfile(src, dst)
    return _state(_open(dst), names)


@pytest.mark.golden
@pytest.mark.parametrize("unit", [t[0] for t in TURNS])
def test_a_sequence_saves_what_ctdraws_saves(fx_install, unit):
    """A sequence saves what CtDraw saves."""
    _unit, page, names, ops = BY_ID[unit]
    want = _ctdraw_state(fx_install, unit, page, names)
    doc = _run(_open(fx_install.page(page)), names, ops)
    doc.save()
    assert _state(_open(doc.path), names) == want


def test_undo_and_redo_keep_the_half_pixel_like_two_turns(fx_install):
    """Two turns, undo, redo, undo, a turn: the same saved records as two turns in a row
    (FxArrange's FxPointGenie and FxLoneText; ctgedit against itself, CtDraw's records are
    the golden test below). A save between the turns gives other records: the fixture's
    turns land on a half pixel, so a lost in-memory half pixel would show."""
    names = ["FxPointGenie", "FxLoneText"]
    src = fx_install.page("FxArrange")
    folder = os.path.dirname(src)

    def copy(name):
        dst = os.path.join(folder, name + ".ctg")
        shutil.copyfile(src, dst)
        return dst

    a = _run(_open(copy("FxTwoTurns")), names, ["R", "R"])
    a.save()
    want = _state(_open(a.path), names)
    b = _run(_open(copy("FxTurnSaveTurn")), names, ["R", "S", "R"])
    b.save()
    assert _state(_open(b.path), names) != want  # (the half pixel matters here)
    doc = _open(copy("FxUndoRedo"))
    for _ in range(2):
        doc.rotate_objects(_roots(doc, names))
    doc.undo()
    doc.redo()
    doc.undo()
    doc.rotate_objects(_roots(doc, names))
    doc.save()
    assert _state(_open(doc.path), names) == want


@pytest.mark.golden
def test_undo_and_redo_keep_the_half_pixel(fx_install):
    """Two turns, undo, redo, undo, a turn: the same records as two turns in a row
    (T26_point_text_two_turns)."""
    _unit, page, names, _ops = BY_ID["T26_point_text_two_turns"]
    want = _ctdraw_state(fx_install, "T26_point_text_two_turns", page, names)
    doc = _open(fx_install.page(page))
    for _ in range(2):
        doc.rotate_objects(_roots(doc, names))
    doc.undo()
    doc.redo()
    doc.undo()
    doc.rotate_objects(_roots(doc, names))
    doc.save()
    assert _state(_open(doc.path), names) == want


def test_a_save_keeps_the_half_pixels_of_a_genies_members_only(fx_install):
    """After a turn of FxArrange's FxPointGenie with FxLoneText, a save keeps the true
    positions of the genie's two points (T05: CtDraw's next turn puts them at y 196, the
    truncated points would give 195) and forgets the text's turned box (the save measures it
    again: T05's second turn centres on the measured box)."""
    names = ["FxPointGenie", "FxLoneText"]
    doc = _open(fx_install.page("FxArrange"))
    genie, text = _roots(doc, names)
    doc.rotate_objects([genie, text])
    members = [k for k in doc.subtree(genie) if k != genie]
    assert set(members) <= set(doc._turned_points(members))
    assert doc._turned_points([text])
    doc.save()
    assert set(doc._turned_points(members)) == set(members)
    assert doc._turned_points([text]) == {}


def test_a_save_keeps_every_local_in_memory(fx_install):
    """A save keeps every Local in memory."""
    from ctgedit.format import placement

    names = ["FxPointGenie", "FxLoneText"]
    doc = _open(fx_install.page("FxArrange"))
    genie, text = _roots(doc, names)
    doc.mirror_objects([genie, text], horizontal=True)
    doc.save()
    held = doc._turned_points([genie, text])
    assert set(held) == {genie, text}
    for k in (genie, text):
        xmap, ymap, (mode, L), box, grp = held[k]
        assert (xmap, ymap, box, grp) == ({}, {}, None, None)
        assert mode == 0x03 and repr(L[:4]) == "(-1.0, 0.0, -0.0, 1.0)"
    doc = _open(fx_install.page("FxMembers"))
    group = _roots(doc, ["FxGenieInGroup"])[0]
    inner = _roots(doc, ["FxInnerGenie"])[0]
    doc.mirror_objects([group], horizontal=True)
    m0, L0, mg, G = doc._turned_points([inner])[inner][4]
    assert (m0, mg) == (0x01, 0x03) and repr(G[:4]) == "(-1.0, 0.0, -0.0, 1.0)"
    doc.save()
    _x, _y, (mode, L), _box, grp = doc._turned_points([inner])[inner]
    assert grp is None and mode == 0x03 and repr(L[:4]) == "(-1.0, 0.0, -0.0, 1.0)"
    # a mirrored text in a plain group takes the group rule too (FxMembers' FxPairB under
    # FxPair; T32)
    pair = _roots(doc, ["FxPair"])[0]
    text = _roots(doc, ["FxPairB"])[0]
    doc.mirror_objects([pair], horizontal=True)
    m0, L0, mg, G = doc._turned_points([text])[text][4]
    assert (m0, L0, mg) == (0x00, placement.IDENT, 0x03)
    assert repr(G[:4]) == "(-1.0, 0.0, -0.0, 1.0)"
    doc.save()
    _x, _y, (mode, L), _box, grp = doc._turned_points([text])[text]
    assert grp is None and mode == 0x03 and repr(L[:4]) == "(-1.0, 0.0, -0.0, 1.0)"
    for _ in range(2):
        doc.rotate_objects([pair])
    m0, L0, mg, G = doc._turned_points([text])[text][4]
    assert (m0, mg) == (0x03, 0x25) and repr(G[:4]) == "(-1.0, -0.0, -0.0, -1.0)"
    mode, L = doc._turned_points([text])[text][2]
    assert mode == 0x27 and repr(L[:4]) == "(1.0, 0.0, 0.0, -1.0)"


@pytest.mark.parametrize(
    "ops, mode",
    [
        (["R", "R"], 0x25),  # the group's Local holds both turns
        (["R", "M", "R"], 0x25),  # a move goes onto the group's Local
        (["R", "U", "Re", "R"], 0x25),  # undo and redo come back to it
        (["R", "R", "U", "U", "R", "R"], 0x25),
        (["R", "S", "R"], 0x15),
    ],
)  # a save composes it in (T31 waits)
def test_a_genie_in_a_group_keeps_the_groups_local_across_edits(fx_install, ops, mode):
    """A genie in a group keeps the group's Local across edits."""
    from ctgedit.format import placement as P

    doc = _open(fx_install.page("FxMembers"))
    group = _roots(doc, ["FxGenieInGroup"])
    for op in ops:
        if op == "R":
            doc.rotate_objects(group)
        elif op == "M":
            doc.move_objects(doc.subtree(group[0]), 7, 3)
        elif op == "S":
            doc.save()
        elif op == "U":
            doc.undo()
        else:
            doc.redo()
    inner = _roots(doc, ["FxInnerGenie"])[0]
    m, L = P.read_slots(doc.parsed.page.objects[inner].body_raw)[0][0][:2]
    shift = (7, 3) if "M" in ops else (0, 0)
    assert m == mode and tuple(L[:4]) == (-1.0, 0.0, 0.0, -1.0)
    assert (L[4], L[5]) == (98.0 + shift[0], 130.0 + shift[1])
    assert repr(L[1:3]) == ("(0.0, 0.0)" if mode == 0x15 else "(-0.0, -0.0)")


@pytest.mark.parametrize(
    "ops, mode, bc, shift",
    [
        (["H", "R", "R"], 0x27, "(-0.0, 0.0)", (0, 0)),  # the group's Local holds all three
        (["H", "M", "R", "R"], 0x27, "(-0.0, 0.0)", (7, 3)),  # a move goes onto the group's Local
        (["H", "R", "U", "Re", "R"], 0x27, "(-0.0, 0.0)", (0, 0)),  # undo and redo come back
        (["H", "R", "R", "U", "U", "R", "R"], 0x27, "(-0.0, 0.0)", (0, 0)),
        (["H", "S", "R", "R"], 0x27, "(0.0, 0.0)", (0, 0)),  # a save composes it in (T32)
        (["H", "S", "M", "R", "R"], 0x27, "(0.0, 0.0)", (7, 3)),
    ],
)
def test_a_mirrored_text_in_a_group_keeps_the_groups_local_across_edits(fx_install, ops, mode, bc, shift):
    """A mirrored text in a group keeps the group's Local across edits."""
    from ctgedit.format import geometry as G
    from ctgedit.format import placement as P

    doc = _open(fx_install.page("FxMembers"))
    group = _roots(doc, ["FxPair"])
    for op in ops:
        if op == "R":
            doc.rotate_objects(group)
        elif op == "H":
            doc.mirror_objects(group, horizontal=True)
        elif op == "M":
            doc.move_objects(doc.subtree(group[0]), 7, 3)
        elif op == "S":
            doc.save()
        elif op == "U":
            doc.undo()
        else:
            doc.redo()
    text = _roots(doc, ["FxPairB"])[0]
    po = doc.parsed.page.objects[text]
    slots = P.read_slots(po.body_raw)[0]
    for m, L in (s[:2] for s in slots):
        assert m == mode and tuple(L[:4]) == (1.0, 0.0, 0.0, -1.0)
        assert (L[4], L[5]) == (0.0 + shift[0], 90.0 + shift[1])
        assert repr(L[1:3]) == bc
    tail = G.parse_text_tail(po)
    assert (tail["orient"], tail["echo"]) == (1, (20, 67))  # (a Local echo: a move
    # goes onto the Local)
    assert tuple(G.parse_geom_header(po.body_raw).anchor) == (20 + shift[0], 23 + shift[1])


@pytest.mark.parametrize(
    "page, group, text, ops, want",
    [
        # a turn and a mirror of a text: the full matrix, the old orient and echo
        # (a test page record 21 has that form: orient 1, (0, 1, 1, 0) mode 0x17)
        (
            "FxArrange",
            "FxLoneText",
            "FxLoneText",
            ["R", "H"],
            (1, (720, 193), 0x17, (0.0, 1.0, 1.0, 0.0, 548.5, -549.5)),
        ),
        (
            "FxArrange",
            "FxLoneText",
            "FxLoneText",
            ["R", "M", "H"],
            (1, (720, 193), 0x17, (0.0, 1.0, 1.0, 0.0, 555.5, -546.5)),
        ),
        (
            "FxArrange",
            "FxLoneText",
            "FxLoneText",
            ["R", "U", "Re", "H"],
            (1, (720, 193), 0x17, (0.0, 1.0, 1.0, 0.0, 548.5, -549.5)),
        ),
        # a move after the mirror goes onto the Local and keeps the echo (T09), though the
        # half-pixel offset puts Total^-1(anchor) on (719.5, 192.5)
        (
            "FxArrange",
            "FxLoneText",
            "FxLoneText",
            ["R", "H", "M"],
            (1, (720, 193), 0x17, (0.0, 1.0, 1.0, 0.0, 555.5, -546.5)),
        ),
        # in a plain group the group's Local holds them (T37's mirror about x 40, the text's
        # box in memory)
        ("FxMembers", "FxPair", "FxPairB", ["R", "H"], (1, (20, 67), 0x17, (0.0, 1.0, 1.0, 0.0, -5.0, 5.0))),
        (
            "FxMembers",
            "FxPair",
            "FxPairB",
            ["R", "R", "H"],
            (1, (20, 67), 0x27, (1.0, 0.0, 0.0, -1.0, 0.0, 90.0)),
        ),
        # a move takes the group's box in memory along, the text's Bounds kept (a mirror
        # about x 47, as the mirror and then the move give)
        (
            "FxMembers",
            "FxPair",
            "FxPairB",
            ["R", "M", "H"],
            (1, (20, 67), 0x17, (0.0, 1.0, 1.0, 0.0, 2.0, 8.0)),
        ),
        (
            "FxMembers",
            "FxPair",
            "FxPairB",
            ["R", "H", "M"],
            (1, (20, 67), 0x17, (0.0, 1.0, 1.0, 0.0, 2.0, 8.0)),
        ),
        # a second mirror leaves turns alone again: the page stores them as the orient
        ("FxMembers", "FxPair", "FxPairB", ["R", "H", "H"], (3, (18, 25), 0, None)),
        # a save makes the turn the orient for good: the mirror is the text's own (ctgedit's
        # reading: capture unit T40)
        (
            "FxArrange",
            "FxLoneText",
            "FxLoneText",
            ["R", "S", "H"],
            (3, (732, 170), 0x03, (-1.0, 0.0, 0.0, 1.0, 1473.0, 0.0)),
        ),
    ],
)
def test_a_mirror_composes_onto_a_texts_turns(fx_install, page, group, text, ops, want):
    """A mirror composes onto a text's turns."""
    from ctgedit.format import geometry as G
    from ctgedit.format import placement as P

    doc = _open(fx_install.page(page))
    sel = _roots(doc, [group])
    for op in ops:
        if op == "R":
            doc.rotate_objects(sel)
        elif op == "H":
            doc.mirror_objects(sel, horizontal=True)
        elif op == "M":
            doc.move_objects(doc.subtree(sel[0]), 7, 3)
        elif op == "S":
            doc.save()
        elif op == "U":
            doc.undo()
        else:
            doc.redo()
    po = doc.parsed.page.objects[_roots(doc, [text])[0]]
    tail = G.parse_text_tail(po)
    orient, echo, mode, L = want
    assert (tail["orient"], tail["echo"]) == (orient, echo)
    for m, got in (s[:2] for s in P.read_slots(po.body_raw)[0]):
        assert m == mode
        if L is not None:
            assert tuple(got) == L


def test_an_element_save_drops_what_a_page_save_drops(fx_install):
    """An element's save leaves the turn memory as a page save does: the half pixels of a
    record under no carrier and a text's turned box go (fxsym's fx_group: a rectangle and a
    text, both turned)."""
    from ctgedit.editing import ElementDocument

    doc = ElementDocument.open_element(fx_install.file("FxLib", "fxsym.ctl"), "fx_group")
    roots = [k for k, o in enumerate(doc.page.objects) if o.parent is None]
    doc.rotate_objects(roots)
    held = doc._turned_points(roots)
    assert set(held) == set(roots) and any(m[3] is not None for m in held.values())
    doc.save()
    assert doc._turned_points(roots) == {}


def test_a_reopen_reads_the_members_half_pixels_from_their_totals(fx_install):
    """A reopen reads the members' half pixels from their Totals."""
    from ctgedit.doc.geometry import _TurnPoints, _with_fraction
    from ctgedit.format import geometry as G

    names = ["FxPointGenie", "FxLoneText"]
    doc = _open(fx_install.page("FxArrange"))
    doc.rotate_objects(_roots(doc, names))
    doc.save()
    doc = _open(doc.path)
    assert doc._turned_points(range(len(doc.parsed.page.objects))) == {}
    genie, text = _roots(doc, names)
    members = [k for k in doc.subtree(genie) if k != genie]
    idx = doc.expand_to_subtrees([genie, text])
    assert doc._member_fractions(idx) == {k: (0.5, 0.5) for k in members}
    objs = doc.parsed.page.objects
    points = _TurnPoints({}, doc._member_fractions(idx))
    got = []
    for k in members:
        points.at = k
        anchor = tuple(G.parse_geom_header(objs[k].body_raw).anchor)
        got.append((anchor, points.true(anchor)))
    assert got == [((725, 70), (725.5, 70.5)), ((725, 110), (725.5, 110.5))]
    points.at = text
    assert points.true((632, 190)) == (632, 190)
    assert (_with_fraction(725, 0.5), _with_fraction(-3, 0.5)) == (725.5, -3.5)
    assert _with_fraction(0, 0.5) is None and _with_fraction(725, 0.0) is None


@pytest.mark.golden
def test_a_turn_of_ctdraws_own_saved_page_keeps_the_members_half_pixels(fx_install):
    """The half pixels a reopen keeps come from the stored bytes alone: ctgedit's turn of
    the page CtDraw saved after one turn (T03) gives the records CtDraw saves after a turn,
    a reopen and a turn (T28). The points' written positions alone give y 195."""
    _unit, page, names, _ops = BY_ID["T28_point_text_turn_reopen_turn"]
    want = _ctdraw_state(fx_install, "T28_point_text_turn_reopen_turn", page, names)
    dst = os.path.join(fx_install.project("FxMain"), "FxFromT03.ctg")
    shutil.copyfile(fxinstall.oracle("turns", "T03_point_text_turn", "page.ctg"), dst)
    doc = _run(_open(dst), names, ["R"])
    doc.save()
    assert _state(_open(doc.path), names) == want


def test_a_turned_texts_held_box_is_for_positionat_only(fx_install):
    """After a turn of FxArrange's FxPointGenie with FxLoneText the text's box in memory
    (its old box turned, T26's centre) is a pixel off the measured box the page stores (T03:
    rect2 = rect1 = (628, 190, 645, 225)). Only PositionAt takes the held box; the Bounds a
    Convert to Bitmap or a zoom uses stay the stored ones, which cover the drawn text."""
    from ctgedit.format import geometry as G

    names = ["FxPointGenie", "FxLoneText"]
    doc = _open(fx_install.page("FxArrange"))
    genie, text = _roots(doc, names)
    doc.rotate_objects([genie, text])
    stored = G.norm_rect(G.parse_geom_header(doc.parsed.page.objects[text].body_raw).rect2)
    assert doc.selection_extent([text], bounds=True) == stored
    held = doc.selection_extent([text], bounds=True, held=True)
    assert held != stored and held == doc._turned_points([text])[text][3]


def test_reversible_turns_give_the_start_back(fx_install, monkeypatch):
    """Settings > Editing > exact quarter turns: a selection turned again keeps its centre,
    so four turns (and two back, two on) give every record back byte for byte, a save
    between them too (FxArrange's FxPointGenie and FxLoneText)."""
    from ctgedit.doc.geometry import GeometryEdits
    from ctgedit.format import writer

    monkeypatch.setattr(GeometryEdits, "REVERSIBLE_TURNS", True)
    names = ["FxPointGenie", "FxLoneText"]
    doc = _open(fx_install.page("FxArrange"))

    def snap():
        return [writer.object_payload(po) for po in doc.parsed.page.objects]

    s0 = snap()
    for n in range(4):
        doc.rotate_objects(_roots(doc, names))
        if n == 1:
            doc.save()
    assert snap() == s0
    for cw in (False, False, True, True):
        doc.rotate_objects(_roots(doc, names), clockwise=cw)
    assert snap() == s0
