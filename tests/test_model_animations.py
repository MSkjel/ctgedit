"""The model's animations match what the object sheet shows."""

import shutil


def test_a_text_display_value_by_its_mode():
    from ctgedit.io.readerparts.common import _text_display_anim

    dv = _text_display_anim({"TXTMODE": "0", "TXT_A": "Pump1.Run", "TXT_OFF": "Off"})
    assert (dv.expression, dv.is_text) == ("Pump1.Run", True)
    dv = _text_display_anim({"TXTMODE": "3", "TXT_STR": "Tank_Level:###.#", "TXT_OFF": "x"})
    assert (dv.expression, dv.format, dv.is_text) == ("Tank_Level", "###.#", False)
    dv = _text_display_anim({"TXTMODE": "4", "TXT_STR": "Recipe_Name"})
    assert (dv.expression, dv.is_text) == ("Recipe_Name", True)


def test_a_fill_colour_by_its_mode():
    from ctgedit.io.readerparts.common import _fill_colour_anim

    fc = _fill_colour_anim({"COLMODE": "2", "COL_ARR": "Mode"})
    assert not fc.range_on and fc.thresholds[0].expression == "Mode"
    fc = _fill_colour_anim(
        {
            "COLMODE": "3",
            "COL_EXP": "Level^(0,100)",
            "COL_THR_0": "<,5,0x000000FF,0x000000FF",
            "COL_THR_1": ">=,90,0x0000FF00,0x0000FF00",
        }
    )
    assert fc.range_on and [(t.limit, t.operator, t.colour) for t in fc.thresholds] == [
        (5.0, 0, 0xFF),
        (90.0, 3, 0xFF00),
    ]
    assert fc.thresholds[0].expression == "Level^(0,100)"


def _with_v3_numbers(fx_page, tmp_path):
    """A copy of FxMain's FxLegacy (the V3 / V4 objects; FxString is a V3 String with EXPR
    FX_PUMP1) with two V3 Numbers (0x1004) the test adds: EXPR 'Time()' and EXPR
    'FX_LEVEL1'. The fixture has no 0x1004 record; CtDraw's Number is a point record like
    the String (format/legacy.POINT_TCS), so records.point_record builds it."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import records as R

    path = str(tmp_path / "FxLegacy.ctg")
    shutil.copy2(fx_page("FxLegacy"), path)
    doc = EditableDocument.open(path)
    for n, expr in enumerate(("Time()", "FX_LEVEL1")):
        po = R.to_po(
            R.point_record(
                0x1004,
                handle=doc.next_handle(),
                at=(600, 200 + 30 * n),
                deltas=(0, -12, 64, 5),
                props=(("EXPR", expr), ("COMMENT", "")),
            )
        )
        doc.add_object(po)
    doc.save()
    return path


def test_a_v3_number_shows_its_expr_and_the_check_reads_it(fx_page, tmp_path):
    from ctgedit.io import reader
    from ctgedit.editing import EditableDocument
    from ctgedit.features import page_checks

    path = _with_v3_numbers(fx_page, tmp_path)
    pg = reader.read_page(path)
    nums = [o for o in pg.objects if o.raw.get("_type_code") in (0x1004, 0x1005)]
    assert sorted(o.raw.get("_type_code") for o in nums) == [0x1004, 0x1004, 0x1005]
    assert all(o.animations.display_value.expression for o in nums)
    assert "Time()" in [o.animations.display_value.expression for o in nums]
    by_tc = {o.raw.get("_type_code"): o.animations.display_value for o in nums}
    assert by_tc[0x1005].is_text and not by_tc[0x1004].is_text
    rep = page_checks.check_document(EditableDocument.open(path))
    assert not [
        f
        for f in rep.findings
        if f.code == "no_expression" and pg.objects[f.index].raw.get("_type_code") in (0x1004, 0x1005)
    ]


def test_only_touch_records_and_buttons_have_a_touch_command(fx_page):
    """FxLegacy holds a touch record (under FxTouchRect), a button with a command, a
    keyboard record with a command (under FxKeyText), a Cicode object and a colour flood;
    FxInputs holds touch records on every kind of owner, keyboard records and buttons.
    FxAnim holds FxMove (MVX), FxSize (SZX) and FxRotate (ROT)."""
    from ctgedit.io import reader

    touch = set()
    for name in ("FxLegacy", "FxInputs"):
        pg = reader.read_page(fx_page(name))
        touch |= {o.raw.get("_type_code") for o in pg.objects if o.animations.touch}
    assert touch == {0x1010, 0x1001}
    pg = reader.read_page(fx_page("FxAnim"))
    moved = [o.animations.movement for o in pg.objects if o.animations.movement]
    assert moved and any(
        m.move_h_expr or m.move_v_expr or m.scale_h_expr or m.scale_v_expr or m.rotation_expr for m in moved
    )
    assert {"FX_LEVEL1", "FX_SPEED"} <= {
        e for m in moved for e in (m.move_h_expr, m.scale_h_expr, m.rotation_expr)
    }
