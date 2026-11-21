"""Appearance > Display Value."""

import os
import shutil
import struct

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from ctgedit.format import dspval as D  # noqa: E402
from ctgedit.format import geometry as G  # noqa: E402
from ctgedit.format import writer  # noqa: E402
from ctgedit.format.page import parse_ctg  # noqa: E402
from qtkit import app as _app

#: FxMain of the fake install copy (set for each test by _fx_main)
EX = None
#: the host page of the new objects
MIXER = "FxShapes.ctg"
#: the pages CtDraw's saves of are read by the golden checks: every display type (0 .. 4 on
#: FxAnim; 0 / 3 / 4 on FxTextKinds; 0 / 3 on FxParts), string sections, genie members
CTDRAW_PAGES = (
    "FxAnim",
    "FxTextKinds",
    "FxParts",
    "FxShapes",
    "FxGenies",
    "FxBig",
    "FxMany",
    "FxAlarms",
    "FxButtons",
)


@pytest.fixture(autouse=True)
def _fx_main(fx_project):
    global EX
    EX = fx_project("FxMain")
    yield
    EX = None


def _pages():
    """Every page of FxMain (the fake install copy)."""
    return sorted(os.path.join(EX, f) for f in os.listdir(EX) if f.lower().endswith(".ctg"))


def _ctdraw_pages():
    """CtDraw's PageOpen + PageSave of CTDRAW_PAGES (the oracle's page.ctg)."""
    import fxinstall

    return [fxinstall.oracle("pages", "FxMain/" + n, "page.ctg") for n in CTDRAW_PAGES]


@pytest.fixture(autouse=True)
def _derive_check(monkeypatch):
    # every re-derive after an edit is compared with a full one
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")


def _copy(tmp_path, name=MIXER):
    dst = tmp_path / name
    shutil.copy2(os.path.join(EX, name), dst)
    return str(dst)


def _texts(path):
    for i, po in enumerate(parse_ctg(path).page.objects):
        if po.type_code != 0x05:
            continue
        tail = G.parse_text_tail(po) if po.subtype_size & 0x2 else None
        yield i, po, po.raw_properties(), (tail["text"] if tail else None)


def _doc(tmp_path, name=MIXER):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(_copy(tmp_path, name))


def _sheet(doc, index):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    dlg = ObjectPropertiesDialog(doc, index)
    return dlg, getattr(dlg, "display_value_editor", None)


def _section(po):
    """(flag, [state strings]) of a text's string section."""
    t = G.parse_text_tail(po)
    b = po.body_raw
    o = t["k_off"]
    (flag,) = struct.unpack_from("<I", b, o)
    o += 4
    out = []

    def lp():
        nonlocal o
        (n,) = struct.unpack_from("<H", b, o)
        s = b[o + 2 : o + 2 + n].decode("cp1252")
        o += 2 + n
        return s

    lp()  # TXT_OFF / the caption
    if flag & 2:
        lp()
    if flag & 4:
        (n,) = struct.unpack_from("<H", b, o)
        o += 2
        out = [lp() for _ in range(n)]
    return flag, out


def _set_condition(ed, row, text):
    ed._conds.setCurrentCell(row, 1)
    ed._cond_edit.setText(text)


# ------------------------------------------------------------------ the model vs the pages
def _untouched_writes_nothing(paths):
    n = 0
    for path in paths:
        for _i, _po, props, cap in _texts(path):
            td = D.read(props, cap)
            assert D.apply_edits(props, cap, td, td.copy()) == ([], None), (path, _i)
            n += 1
    return n


def test_untouched_page_writes_nothing_on_the_fixtures():
    assert _untouched_writes_nothing(_pages()) >= 300


@pytest.mark.golden
def test_untouched_page_writes_nothing_on_ctdraws_saves():
    assert _untouched_writes_nothing(_ctdraw_pages()) >= 200


def _stored_values_are_ours(paths):
    seen = set()
    for path in paths:
        for i, _po, props, cap in _texts(path):
            if "TXTMODE" not in props:
                continue
            td = D.read(props, cap)
            get = D._getter(props)
            diffs = {k: (get(k), v) for k, v in D.target_values(td).items() if get(k) != v}
            assert diffs == {}, (path, i)
            seen.add(td.mode)
    assert seen == set(D.TYPES)


def test_our_apply_reads_back_what_it_writes(tmp_path):
    """Every TXTMODE text of FxAnim and FxTextKinds (copies) given our Apply's values
    (D.target_values of what the page shows) through set_text_display: read back, each
    stores exactly those values, over all five display types."""
    from ctgedit.editing import EditableDocument

    paths = []
    for name in ("FxAnim.ctg", "FxTextKinds.ctg"):
        path = _copy(tmp_path, name)
        doc = EditableDocument.open(path)
        n = 0
        for i, _po, props, cap in list(_texts(path)):
            if "TXTMODE" not in props:
                continue
            td = D.read(props, cap)
            if doc.set_text_display(i, list(D.target_values(td).items())):
                n += 1
        assert n, name
        doc.save()
        paths.append(path)
    _stored_values_are_ours(paths)


@pytest.mark.golden
def test_ctdraws_stored_display_values_are_what_our_apply_writes():
    """CtDraw's stored display values are what our Apply writes."""
    pytest.skip(
        "needs fixture: an oracle unit of texts of all five display types whose "
        "values CtDraw's own Display Value dialog applied (one-off COM authoring); "
        "PageOpen + PageSave keeps the generator's keys"
    )


def _string_sections_rebuild(paths):
    n = 0
    for path in paths:
        for _i, po, _props, _cap in _texts(path):
            if not (po.subtype_size & 0x2):
                continue
            before = po.body_raw
            writer.rebuild_text_strings(po)
            assert po.body_raw == before
            n += 1
    return n


def test_rebuilt_string_section_is_the_identity_on_the_fixtures():
    assert _string_sections_rebuild(_pages()) >= 150


@pytest.mark.golden
def test_rebuilt_string_section_is_the_identity_on_ctdraws_saves():
    """Rebuilding the string section of every text CtDraw saved gives its stored bytes."""
    assert _string_sections_rebuild(_ctdraw_pages()) >= 100


def _numeric_split_join(paths):
    """(the displays with a format in CtDraw's '(expr):fmt' form, those in the bare
    'expr:fmt' form): the first split and join back to their stored text, the bare ones
    split the same way and join to the parenthesised form."""
    n = bare = 0
    for path in paths:
        for _i, _po, props, _cap in _texts(path):
            if (props.get("TXTMODE") or "").strip() != "3" or not props.get("TXT_STR"):
                continue
            s = props["TXT_STR"]
            expr, fmt = D.split_numeric(s)
            if fmt and not s.startswith("("):
                assert D.is_format(fmt), fmt
                assert D.join_numeric(expr, fmt) == "(%s):%s" % (expr, fmt)
                bare += 1
                continue
            assert D.join_numeric(expr, fmt) == s
            if fmt:
                assert D.is_format(fmt), fmt
                n += 1
    return n, bare


@pytest.mark.golden
def test_numeric_expression_and_format_split_and_join_on_ctdraws_saves():
    """CtDraw stores a format as '(expr):fmt'; every numeric display it
    saved splits and joins back to its stored text."""
    assert sum(_numeric_split_join(_ctdraw_pages())) >= 7


def test_numeric_expression_and_format_split_and_join_like_ctdraw():
    """Every fixture numeric display splits and joins back to its stored text: FxTextKinds'
    FxNumParen has a format in the '(expr):fmt' form, five more of FxTextKinds and FxAnim's
    FxNumeric the bare 'expr:fmt' form."""
    assert _numeric_split_join(_pages()) == (1, 6)
    assert D.split_numeric("LOOP_1_PV:###.#") == ("LOOP_1_PV", "###.#")  # bare form
    assert D.split_numeric('StrToLocal("a:b")') == ('StrToLocal("a:b")', "")
    assert D.join_numeric("Tag", "#.##") == "(Tag):#.##" and D.join_numeric("", "#") == ""
    assert all(D.is_format(f) for f in D.SCADAHELP_FORMATS + ("#!", "###.#EU", "## EU"))
    # the other specifiers of help topic 1064774: 's' exponential, shortform (+ EU)
    assert all(D.is_format(f) for f in ("#s###", "3.2", "6.0EU", "4.1", "#0-###.#EU"))
    assert not D.is_format("abc") and not D.is_format("#" * 11)
    assert not D.is_format("3.2.1") and not D.is_format("EU")


# ------------------------------------------------------------------ the sheet
def test_noop_apply_keeps_every_text_byte_identical(tmp_path):
    """Opening the sheet of a text and pressing Apply writes nothing: the
    record (string section included) and the undo history stay as they were
    -- on texts of every display type."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops import registry as R

    modes = set()
    for name in ("FxAnim.ctg", "FxTextKinds.ctg", "FxParts.ctg", "FxShapes.ctg"):
        doc = EditableDocument.open(_copy(tmp_path, name))
        done: dict = {}
        for i, po in enumerate(doc.parsed.page.objects):
            if po.type_code != 0x05:
                continue
            stored = (po.raw_properties().get("TXTMODE") or "").strip()
            if done.get(stored, 0) >= 3 or R.read_only_reason(doc, i):
                continue
            body, flags, undo = bytes(po.body_raw), po.subtype_size, len(doc._undo)
            dlg, ed = _sheet(doc, i)
            modes.add(ed.current().mode)
            assert dlg.apply() is False
            po2 = doc.parsed.page.objects[i]
            assert bytes(po2.body_raw) == body and po2.subtype_size == flags
            assert len(doc._undo) == undo
            dlg.reject()
            done[stored] = done.get(stored, 0) + 1
    assert {D.ON_OFF, D.MULTI_STATE, D.ARRAY, D.NUMERIC, D.STRING} <= modes


def test_display_value_tab_only_for_text_and_number(tmp_path):
    doc = _doc(tmp_path)
    for kind, kw in (
        ("rectangle", {"rect": (5, 5, 60, 40)}),
        ("line", {"rect": (5, 50, 80, 90)}),
        ("button", {"rect": (5, 100, 90, 130), "text": "Go"}),
        ("polygon", {"nodes": [(100, 5), (150, 5), (120, 50)]}),
    ):
        i = doc.create_object(kind, **kw)
        dlg, ed = _sheet(doc, i)
        assert "display_value" not in dlg.page_ids() and ed is None, kind
        dlg.reject()
    for kind in ("text", "number"):
        i = doc.create_object(kind, rect=(5, 150, 90, 170), text="T1")
        dlg, ed = _sheet(doc, i)
        assert "display_value" in dlg.page_ids() and ed is not None, kind
        tabs = dlg._tabs.widget(0)
        # CtDraw's text sheet: General, 3D Effects, Display Value, Visibility
        assert [tabs.tabText(k) for k in range(tabs.count())][:3] == [
            "General",
            "3D Effects",
            "Display Value",
        ]
        dlg.reject()


def test_three_condition_multi_state_writes_eight_states(tmp_path):
    from ctgedit.format import textmetrics as TM

    doc = _doc(tmp_path)
    i = doc.create_object("text", rect=(10, 10, 90, 30), text="Idle")
    fresh = (bytes(doc.parsed.page.objects[i].body_raw), doc.parsed.page.objects[i].subtype_size)
    dlg, ed = _sheet(doc, i)
    ed._radios[D.MULTI_STATE].setChecked(True)
    assert ed._conds.rowCount() == 3 and ed._states.rowCount() == 8  # CtDraw's default 3
    assert ed._states.item(0, 1).text() == "Idle"  # state 0 = the text
    assert ed._states.item(5, 0).text() == "A B\u0305 C"  # A + 4C
    for row, cond in enumerate(("Run", "Fault", "Local")):
        _set_condition(ed, row, cond)
    ed._states.item(1, 1).setText("Running")
    ed._states.item(3, 1).setText("Running^nwith fault")  # ^n = a line break
    n = len(doc._undo)
    assert dlg.apply() is True and len(doc._undo) == n + 1  # one undo step
    p = doc.object_properties(i)
    assert p["TXTMODE"] == "1"
    assert (p["TXT_A"], p["TXT_B"], p["TXT_C"]) == ("Run", "Fault", "Local")
    states = sorted(k for k in p if k.startswith("STATE_"))
    assert len(states) == 8
    assert p["STATE_0"] == "Idle" and p["STATE_1"] == "Running"
    assert p["STATE_3"] == "Running\r\nwith fault" and p["STATE_7"] == D.UNSET
    assert p["TXT_OFF"] == "Idle"
    po = doc.parsed.page.objects[i]
    flag, sect = _section(po)
    assert flag == 5 and len(sect) == 8 and sect[1] == "Running" and sect[2] == ""
    ext = TM.text_extent(po)  # the box re-measured (None without GDI)
    assert ext is None or tuple(G.parse_geom_header(po.body_raw).rect1) == tuple(ext)
    assert dlg.tab_marked("Appearance", "Display Value")
    # the page shows the stored state again, the break as ^n
    assert ed._states.item(3, 1).text() == "Running^nwith fault"
    # a fourth condition doubles the rows; deleting two leaves 4
    ed._btn_add.click()
    assert ed._states.rowCount() == 16 and ed._states.item(3, 1).text() == "Running^nwith fault"
    ed._conds.setCurrentCell(3, 1)
    ed._btn_del.click()
    ed._conds.setCurrentCell(2, 1)
    ed._btn_del.click()
    assert ed._conds.rowCount() == 2 and ed._states.rowCount() == 4
    assert dlg.apply() is True
    p = doc.object_properties(i)
    assert "TXT_C" not in p and "STATE_4" not in p and p["STATE_3"] == "Running\r\nwith fault"
    doc.undo()
    doc.undo()
    po = doc.parsed.page.objects[i]
    assert (bytes(po.body_raw), po.subtype_size) == fresh
    dlg.reject()


def test_state_text_under_a_blank_condition_blocks_apply(tmp_path):
    """A condition added and left blank does not keep its states (the
    reload counts to the last non-blank condition): a text typed in one of
    those rows blocks Apply with a message instead of vanishing silently."""
    doc = _doc(tmp_path)
    i = doc.create_object("text", rect=(10, 10, 90, 30), text="Idle")
    dlg, ed = _sheet(doc, i)
    ed._radios[D.MULTI_STATE].setChecked(True)
    for row, cond in enumerate(("Run", "Fault", "Local")):
        _set_condition(ed, row, cond)
    ed._btn_add.click()  # condition D, left blank
    assert ed._states.rowCount() == 16
    ed._states.item(9, 1).setText("Nine")
    body = bytes(doc.parsed.page.objects[i].body_raw)
    assert dlg.apply() is False
    assert "Condition D is blank" in dlg._status.text() and "state 9" in dlg._status.text()
    assert ed._states.currentRow() == 9  # the row is shown
    assert bytes(doc.parsed.page.objects[i].body_raw) == body
    _set_condition(ed, 3, "Remote")
    assert dlg.apply() is True, dlg._status.text()
    p = doc.object_properties(i)
    assert p["TXT_D"] == "Remote" and p["STATE_9"] == "Nine" and p["STATE_15"] == D.UNSET
    dlg.reject()
    # a sheet whose only edit is such a text (nothing Apply would store has
    # changed) still refuses it rather than dropping it with the reload
    j = doc.create_object("text", rect=(10, 40, 90, 60), text="Off")
    dlg, ed = _sheet(doc, j)
    ed._radios[D.MULTI_STATE].setChecked(True)
    _set_condition(ed, 0, "Run")
    assert dlg.apply() is True
    assert ed._conds.rowCount() == 1 and ed._states.rowCount() == 2
    ed._btn_add.click()
    ed._states.item(3, 1).setText("Three")
    undo = len(doc._undo)
    assert dlg.apply() is False and "Condition B is blank" in dlg._status.text()
    assert len(doc._undo) == undo and ed._states.item(3, 1).text() == "Three"
    dlg.reject()


def test_multi_state_with_a_blank_text_keeps_its_string_section(tmp_path):
    """Multi-state with a blank text keeps its string section."""
    from ctgedit.format import colourstate
    from ctgedit.format import textmetrics as TM

    doc = _doc(tmp_path)
    i = doc.create_object("number", rect=(10, 40, 90, 60), text="LOOP_1_PV")
    dlg, ed = _sheet(doc, i)
    ed._radios[D.MULTI_STATE].setChecked(True)
    ed._states.item(0, 1).setText("")  # the object's text left blank
    assert dlg.apply() is True, dlg._status.text()
    dlg.reject()
    p = doc.object_properties(i)
    assert (p["TXTMODE"], p["TXT_A"], p["STATE_0"], p["STATE_1"]) == ("1", "LOOP_1_PV", D.UNSET, D.UNSET)
    assert "STATE_2" not in p and "TXT_STR" not in p

    def consistent():
        po = doc.parsed.page.objects[i]
        t = G.parse_text_tail(po)
        assert t["text"] == ""
        assert t["k_off"] == colourstate.layout(po)["style_at"] + 54
        assert po.body_raw[t["k_off"] : t["echo_off"]] == writer.text_string_section(po, "")
        ext = TM.text_extent(po)  # None without GDI
        assert ext is None or tuple(G.parse_geom_header(po.body_raw).rect1) == tuple(ext)

    consistent()
    dlg, ed = _sheet(doc, i)
    ed._states.item(1, 1).setText("One")
    assert dlg.apply() is True, dlg._status.text()
    dlg.reject()
    consistent()
    assert doc.object_properties(i)["STATE_1"] == "One"


def test_numeric_format_is_stored_in_the_expression_and_shown_after_reload(tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.io import reader

    doc = _doc(tmp_path)
    i = doc.create_object("number", rect=(10, 40, 90, 60), text="LOOP_1_PV")
    dlg, ed = _sheet(doc, i)
    assert ed.current().mode == D.NUMERIC and ed._num_expr.text() == "LOOP_1_PV"
    assert ed._format.currentText() == D.DEFAULT_FORMAT_LABEL
    items = [ed._format.itemText(k) for k in range(ed._format.count())]
    assert items[0] == "<default>" and "####.#" in items and "#!" in items
    ed._format.setEditText("#.##")
    assert dlg.apply() is True
    p = doc.object_properties(i)
    assert p["TXT_STR"] == "(LOOP_1_PV):#.##" and "FORMAT" not in p
    dlg.reject()
    doc.save()
    doc2 = EditableDocument.open(doc.path)
    assert doc2.page.objects[i].style.text == "#.##"
    reader.set_label_mode("literal")
    try:
        doc3 = EditableDocument.open(doc.path)
        assert doc3.page.objects[i].style.text == "#.##"
    finally:
        reader.set_label_mode("native")
    # <default> goes back to the bare expression
    dlg, ed = _sheet(doc2, i)
    assert ed._format.currentText() == "#.##"
    ed._format.setEditText(D.DEFAULT_FORMAT_LABEL)
    assert dlg.apply() is True
    assert doc2.object_properties(i)["TXT_STR"] == "LOOP_1_PV"
    dlg.reject()


def test_off_text_is_the_caption_and_survives_the_reload(tmp_path):
    """The OFF text is the caption (the tail the loader reads), so it survives the
    reload."""
    from ctgedit.editing import EditableDocument

    doc = _doc(tmp_path)
    i = doc.create_object("text", rect=(10, 10, 90, 30), text="@(Hello)")
    dlg, ed = _sheet(doc, i)
    assert ed._off_text.text() == "@(Hello)" and dlg._ed_caption.text() == "@(Hello)"
    ed._on_expr.setText("Pump_Run")
    ed._off_text.setText("Stopped")
    assert dlg._ed_caption.text() == "Stopped"  # the General page follows
    ed._on_text.setText("@(Running)")
    assert dlg.apply() is True
    dlg.reject()
    doc.save()
    doc2 = EditableDocument.open(doc.path)
    assert doc2.text_caption(i) == "Stopped"
    p = doc2.object_properties(i)
    assert (p["TXTMODE"], p["TXT_A"], p["TXT_OFF"], p["TXT_ON"]) == ("0", "Pump_Run", "Stopped", "@(Running)")
    po = doc2.parsed.page.objects[i]
    flag, _s = _section(po)
    assert flag == 3


def test_general_text_edit_goes_through_the_display_value_page(tmp_path):
    """The General page's Text is the same static text: a multi-state text's
    state 0 follows it (CtDraw's DspVal_StaticText)."""
    doc = _doc(tmp_path)
    i = doc.create_object("text", rect=(10, 10, 90, 30), text="Off")
    dlg, ed = _sheet(doc, i)
    ed._radios[D.MULTI_STATE].setChecked(True)
    _set_condition(ed, 0, "Tag1")
    assert dlg.apply() is True
    dlg._ed_caption.setText("Top\nBottom")
    assert ed._states.item(0, 1).text() == "Top^nBottom"
    assert dlg.apply() is True
    p = doc.object_properties(i)
    assert p["STATE_0"] == p["TXT_OFF"] == "Top\r\nBottom"
    assert doc.text_caption(i) == "Top\r\nBottom"
    # Numeric: the General page's Text is off (the display shows the value)
    ed._radios[D.NUMERIC].setChecked(True)
    assert not dlg._ed_caption.isEnabled()
    dlg.reject()


def test_multi_line_off_text_takes_enter(tmp_path):
    doc = _doc(tmp_path)
    i = doc.create_object("text", rect=(10, 10, 90, 30), text="A")
    dlg, ed = _sheet(doc, i)
    from PySide6 import QtGui

    dlg.show()
    ed._off_text.setFocus()
    ed._off_text.moveCursor(QtGui.QTextCursor.End)
    QTest.keyClick(ed._off_text, QtCore.Qt.Key_Return)
    QTest.keyClicks(ed._off_text, "B")
    assert ed._off_text.text() == "A\nB"
    assert dlg.apply() is True
    assert doc.text_caption(i) == "A\r\nB" and doc.object_properties(i)["TXT_OFF"] == "A\r\nB"
    dlg.reject()


def test_array_texts_show_filled_rows_plus_the_next(tmp_path):
    doc = _doc(tmp_path)
    i = doc.create_object("text", rect=(10, 10, 90, 30), text="Running")
    dlg, ed = _sheet(doc, i)
    ed._radios[D.ARRAY].setChecked(True)
    assert ed._array.rowCount() == 2  # row 0 (the text) + the next
    ed._arr_expr.setText("MOTOR_STATUS")
    ed._array.item(1, 1).setText("Starting")
    assert ed._array.rowCount() == 3  # the next row appears
    ed._array.item(2, 1).setText("Stopping")
    assert dlg.apply() is True
    p = doc.object_properties(i)
    assert (p["TXTMODE"], p["TXT_A"], p["ARRAY_0"], p["ARRAY_1"], p["ARRAY_2"]) == (
        "2",
        "MOTOR_STATUS",
        "Running",
        "Starting",
        "Stopping",
    )
    assert "ARRAY_3" not in p
    flag = _section(doc.parsed.page.objects[i])[0]
    assert flag == 9
    dlg.reject()
    dlg, ed = _sheet(doc, i)
    assert ed._array.rowCount() == 4 and ed._array.item(2, 1).text() == "Stopping"
    dlg.reject()


def test_type_switch_carries_the_expression_and_drops_the_old_type(tmp_path):
    doc = _doc(tmp_path)
    i = doc.create_object("text", rect=(10, 10, 90, 30), text="0")
    dlg, ed = _sheet(doc, i)
    ed._radios[D.ARRAY].setChecked(True)
    ed._arr_expr.setText("Level_Index")
    ed._array.item(1, 1).setText("Low")
    assert dlg.apply() is True
    # the expression is shared by every panel (CtDraw's DspVal_Expression)
    ed._radios[D.NUMERIC].setChecked(True)
    assert ed._num_expr.text() == "Level_Index"
    ed._radios[D.MULTI_STATE].setChecked(True)
    assert ed._conds.item(0, 1).text() == "Level_Index"
    ed._radios[D.NUMERIC].setChecked(True)
    ed._format.setEditText("####")
    assert dlg.apply() is True
    p = doc.object_properties(i)
    assert p["TXTMODE"] == "3" and p["TXT_STR"] == "(Level_Index):####"
    assert not any(k.startswith("ARRAY_") for k in p) and "TXT_A" not in p
    assert _section(doc.parsed.page.objects[i])[0] == 1
    ed._radios[D.STRING].setChecked(True)
    assert ed._str_expr.text() == "Level_Index"
    assert dlg.apply() is True
    assert doc.object_properties(i)["TXT_STR"] == "Level_Index"
    assert doc.object_properties(i)["TXTMODE"] == "4"
    dlg.reject()


def test_clear_property_clears_the_current_type(tmp_path):
    from ctgedit.ui.objprops import registry as R

    doc = _doc(tmp_path)
    i = doc.create_object("text", rect=(10, 10, 90, 30), text="Off")
    dlg, ed = _sheet(doc, i)
    ed._on_expr.setText("Pump_Run")
    ed._on_text.setText("On")
    assert dlg.apply() is True
    assert dlg.tab_marked("Appearance", "Display Value")
    ed._btn_clear.click()
    assert ed._on_expr.text() == "" and ed._on_text.text() == "" and ed._off_text.text() == "Off"
    assert dlg.apply() is True
    p = doc.object_properties(i)
    assert "TXT_A" not in p and "TXT_ON" not in p and p["TXT_OFF"] == "Off"
    assert p["TXTMODE"] == "0"
    assert not dlg.tab_marked("Appearance", "Display Value")
    assert not R.is_configured(R.PAGE_BY_ID["display_value"], dlg.context, p)
    dlg.reject()


def test_invalid_values_block_apply_with_a_message(tmp_path):
    doc = _doc(tmp_path)
    i = doc.create_object("number", rect=(10, 40, 90, 60), text="Tag1")
    body = bytes(doc.parsed.page.objects[i].body_raw)
    dlg, ed = _sheet(doc, i)
    ed._format.setEditText("abc")
    assert dlg.apply() is False
    assert "not a number format" in dlg._status.text()
    ed._format.setEditText("##.#")
    ed._radios[D.ON_OFF].setChecked(True)
    ed._on_text.setText("x" * 300)
    assert dlg.apply() is False and "too long" in dlg._status.text()
    assert bytes(doc.parsed.page.objects[i].body_raw) == body
    ed._on_text.setText("\u2603")
    assert dlg.apply() is False and "cannot be stored" in dlg._status.text()
    dlg.reject()


def test_stored_format_outside_the_pattern_does_not_block_an_expression_edit(tmp_path):
    """A stored format the pattern does not accept (here longer than
    CtDraw's 10-character field) is shown whole and written back unchanged
    when only the expression is edited; a format the user types is checked."""
    doc = _doc(tmp_path)
    i = doc.create_object("number", rect=(10, 40, 90, 60), text="LOOP_1_PV")
    assert doc.ensure_property(i, "TXT_STR", "(LOOP_1_PV):########.###")
    dlg, ed = _sheet(doc, i)
    assert ed._format.currentText() == "########.###" and ed.current().fmt == "########.###"
    ed._num_expr.setText("LOOP_1_SP")
    assert dlg.apply() is True, dlg._status.text()
    assert doc.object_properties(i)["TXT_STR"] == "(LOOP_1_SP):########.###"
    ed._format.setEditText("#########.#")  # typed: 11 characters
    assert dlg.apply() is False and "not a number format" in dlg._status.text()
    assert doc.object_properties(i)["TXT_STR"] == "(LOOP_1_SP):########.###"
    dlg.reject()


def test_genie_member_display_value_is_read_only(tmp_path):
    from ctgedit.ui.objprops import registry as R

    doc = _doc(tmp_path, "FxGenies.ctg")
    i = next(
        k
        for k, po in enumerate(doc.parsed.page.objects)
        if po.type_code == 0x05 and "genie" in R.read_only_reason(doc, k)
    )
    dlg, ed = _sheet(doc, i)
    assert dlg.is_read_only() and ed is not None
    assert not any(b.isEnabled() for b in ed._radios.values())
    assert ed._off_text.isReadOnly() and not ed._btn_clear.isEnabled()
    dlg.reject()


def test_genie_element_text_writes_its_template_bag(fx_project, tmp_path, monkeypatch):
    """FxLib's fxgenie.fx_value: a text whose TXT_OFF is '@(%Description%)'."""
    from ctgedit.editing import ElementDocument

    lib = fx_project("FxLib")
    shutil.copy2(os.path.join(lib, "fxgenie.ctm"), tmp_path / "fxgenie.ctm")
    monkeypatch.setenv("CTG_LIB_PATH", lib)
    d = ElementDocument.open_element(str(tmp_path / "fxgenie.ctm"), "fx_value")
    i = next(k for k, o in enumerate(d.objects) if o.type_code == 0x05)
    before = d.text_display(i)
    assert before.static == "@(%Description%)"  # the template TXT_OFF
    after = before.copy()
    after.expression, after.on_text = "%Tag%_Alarm", "@(Alarm)"
    assert d.apply_text_display(i, before, after) is True
    p = d.object_properties(i)
    assert (p["TXTMODE"], p["TXT_A"], p["TXT_ON"], p["TXT_OFF"]) == (
        "0",
        "%Tag%_Alarm",
        "@(Alarm)",
        "@(%Description%)",
    )
    d.undo()
    assert "TXT_ON" not in d.object_properties(i)
