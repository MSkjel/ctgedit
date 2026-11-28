"""The object sheet writes CtDraw's keys and applies as one undo step."""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app

#: the pages (FxMain of the fake install)
MOTION = "FxMotion"
GENIES = "FxGenies"
CICODE = "FxCicode"
#: a page the tests only draw on
VEHICLE = "FxShapes"


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
#: the key names CtDraw itself uses (its sheet / automation writers). Keys of four
#: characters or fewer (MVX, ROT, CMD, AREA, PRIV, DESC, FIL ...) come from the pg*.DBF
#: column lists (io/pgregister.TABLE_FIELDS).
CTDRAW_KEYS = (
    "ROTSLMODE",
    "ROTXORG",
    "ROTYORG",
    "SZXORG",
    "SZYORG",
    "VISIBLE",
    "TXTMODE",
    "TXT_B",
    "TXT_C",
    "TXT_D",
    "TXT_E",
    "MVXMAXPT",
    "MVXMINPT",
    "MVYMAXPT",
    "MVYMINPT",
    "ROTMAXPC",
    "ROTMINPC",
    "ROTMODE",
    "SZXMAXPC",
    "SZXMINPC",
    "SZXMODE",
    "SZYMAXPC",
    "SZYMINPC",
    "SZYMODE",
    "MVXSLMODE",
    "MVYSLMODE",
    "COLMODE",
    "COL_B",
    "COL_C",
    "COL_D",
    "COL_E",
    "!COL_STATIC",
    "FILMAXPC",
    "FILMINPC",
    "FILMODE",
    "FILCOL",
    "ACTION",
    "RPT_RATE",
    "MSG_LOG",
    "DOWN_CMD",
    "MSG_LOGDN",
    "REPEAT_CMD",
    "MSG_LOGRPT",
    "KEY_SEQ",
    "__AN_NAME__",
    "DEVICE",
    "DISABLED",
    "DISMODE",
    "GREY_STYLE",
    "COMMENT",
)
#: the Alarm Indicator page's keys and the ActiveX Identification page's
CTDRAW_PAGE_KEYS = (
    "AI_ON",
    "AI_FLAG_LOC",
    "AI_EQHIER",
    "AI_INSIDE",
    "AI_WIDTH",
    "AI_PADDING",
    "AI_COLOUR",
    "AI_FLAG_ON",
    "AI_EXPR",
    "AI_INCLUDE",
    "ACTIVEXEXT_*NAME",
    "ACTIVEXEXT_*EVENTCLASS",
    "ACTIVEXEXT_*PERSIST",
)


def _engine_keys() -> set:
    from ctgedit.io import pgregister

    with open(os.path.join(REPO, "ctgedit", "format", "catalog.json"), encoding="utf-8") as f:
        cat = json.load(f)
    keys = {p["key"].replace("<n>", "*") for p in cat["properties"] if p.get("key")}
    for fields in pgregister.TABLE_FIELDS.values():
        keys.update(fields)
    keys.update(CTDRAW_KEYS)
    keys.update(CTDRAW_PAGE_KEYS)
    return keys


@pytest.fixture
def page(fx_page):
    """``page(name)``: the path of a FxMain page in the fake install's copy (the copy is
    the test's own: edits and saves stay in its tmp_path)."""
    return fx_page


def _named(doc, name):
    (i,) = doc.find_objects_by_name(name)
    return i


def _sheet(doc, index):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    return ObjectPropertiesDialog(doc, index)


def _w(dlg, key, n=0):
    return dlg._widgets[key][n][1]


# ------------------------------------------------------------------ the key contract
def test_sheet_writes_only_engine_keys():
    from ctgedit.ui.objprops import sheet_keys

    keys = sheet_keys()
    engine = _engine_keys()
    invented = sorted(k for k in keys if k not in engine)
    assert invented == [], (
        "sheet keys neither in catalog.json, the pg*.DBF columns nor CtDraw's key table: %s" % invented
    )
    # never the genie parameter 'Name'
    assert "Name" not in keys


# ------------------------------------------------------------------ stored values shown
def test_sheet_shows_stored_engine_values(page):
    from ctgedit.editing import EditableDocument

    # a template-like rectangle: movement offsets + scaling percentages
    doc = EditableDocument.open(page(MOTION))
    i = _named(doc, "FxTplLikeRect")
    rp = doc.object_properties(i)
    assert (rp["MVXMINPT"], rp["MVXMAXPT"]) == ("0", "500")
    dlg = _sheet(doc, i)
    assert (_w(dlg, "MVXMINPT").value(), _w(dlg, "MVXMAXPT").value()) == (0, 500)
    assert _w(dlg, "MVXMINPT", 1).value() == 0  # the H slider shares them
    assert (_w(dlg, "SZXMINPC").value(), _w(dlg, "SZXMAXPC").value()) == (0, 100)
    assert _w(dlg, "MVX").text() == "FX_LEVEL1"
    assert _w(dlg, "MVYMINPT").text() == "(not set)"  # absent key, not a fake 0
    # a navigation button: Access > Disable + tool tip
    dlg = _sheet(doc, _named(doc, "FxNavButton"))
    assert _w(dlg, "DISABLED").text() == 'PageInfo(6)=""'
    assert _w(dlg, "DISMODE").isChecked()
    assert _w(dlg, "GREY_STYLE").currentData() == "GRAY_HIDE"
    assert _w(dlg, "COMMENT").text() == "@(Fx Back)"

    # rotation in DEGREES (a symbol: ROT + 0 / 180)
    dlg = _sheet(doc, _named(doc, "FxSymRotate"))
    assert _w(dlg, "ROT").text() == "FX_SPEED"
    assert (_w(dlg, "ROTMINPC").value(), _w(dlg, "ROTMAXPC").value()) == (0, 180)
    assert _w(dlg, "ROTMINPC").suffix() == "\u00b0"

    # sliders
    dlg = _sheet(doc, _named(doc, "FxSliderHV"))
    assert _w(dlg, "MVXSL").text() == "FX_SPEED"
    assert _w(dlg, "MVXSLMODE").isChecked()  # 4097 = continuous
    assert (_w(dlg, "MVXMINPT").value(), _w(dlg, "MVXMAXPT").value()) == (0, 300)
    assert _w(dlg, "MVYSL").text() == "FX_LEVEL1"
    assert _w(dlg, "MVYMAXPT").value() == 200
    assert not _w(dlg, "ROTSLMODE").isEnabled()  # no rotational tag
    # CtDraw's '<User>' tooltip marker is not shown but kept
    assert _w(dlg, "COMMENT").text() == "@(Fx Start)"
    dlg = _sheet(doc, _named(doc, "FxRotSlider"))
    assert _w(dlg, "ROTSL").text() == "FX_SPEED"
    assert (_w(dlg, "ROTMINPC").value(), _w(dlg, "ROTMAXPC").value()) == (135, 135)


# ------------------------------------------------------------------ apply = one undo step
def test_multi_field_apply_is_one_undo_step_and_reaches_the_register(page):
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G
    from ctgedit.io import pgregister

    doc = EditableDocument.open(page(MOTION))
    i = _named(doc, "FxSliderHV")
    # (FxSliderHV stores no MVYSLMODE: the test gives it one, a key the sheet must leave)
    assert "MVYSLMODE" not in doc.object_properties(i)
    assert doc.ensure_property(i, "MVYSLMODE", "4097")
    before = dict(doc.object_properties(i))
    assert before["MVYSLMODE"] == "4097"
    width0 = doc.page.objects[i].style.line_width
    dlg = _sheet(doc, i)
    n = len(doc._undo)
    _w(dlg, "MVXMAXPT").setValue(250)
    _w(dlg, "MVXSLMODE").setChecked(False)
    _w(dlg, "DESC").setText("Horizontal and vertical slider")
    _w(dlg, "DISABLED").setText("NOT Operator_OK")
    _w(dlg, "GREY_STYLE").setCurrentIndex(2)  # Greyed
    _w(dlg, "DISMODE").setChecked(True)
    _w(dlg, "COMMENT").setText("Drag me")
    dlg._sp_width.setValue(3)
    assert dlg.apply() is True and dlg._status.isHidden()
    assert len(doc._undo) == n + 1 and doc._undo[-1].label == "object properties"
    rp = doc.object_properties(i)
    assert rp["MVXMAXPT"] == "250" and rp["MVXSLMODE"] == "1"
    assert rp["DESC"] == "Horizontal and vertical slider"
    assert (rp["DISABLED"], rp["GREY_STYLE"], rp["DISMODE"]) == ("NOT Operator_OK", "GRAY_PART", "TRUE")
    # (FxSliderHV's tool tip carries CtDraw's <User> marker: the edit keeps it)
    assert rp["COMMENT"] == "Drag me<User>"
    # untouched keys are not rewritten (MVYSLMODE, MVXMINPT)
    assert rp["MVYSLMODE"] == before["MVYSLMODE"] and rp["MVXMINPT"] == before["MVXMINPT"]
    # the register (pgdynobj) rows carry the values the compiler reads
    an = G.live_an(doc.parsed.page.objects[i])
    rows = [r for r in pgregister.page_rows(doc.serialize(), MOTION)["pgdynobj"] if r["AN"] == str(an)]
    assert len(rows) == 1
    row = rows[0]
    assert (row["MVXSL"], row["MVXMAXPT"], row["MVXSLMODE"]) == ("FX_SPEED", "250", "1")
    assert (row["DESC"], row["DISABLED"], row["GREY_STYLE"], row["DISMODE"]) == (
        "Horizontal and vertical slider",
        "NOT Operator_OK",
        "GRAY_PART",
        "TRUE",
    )
    assert row["COMMENT"] == "Drag me"
    # ONE Ctrl+Z restores every field
    doc.undo()
    assert doc.object_properties(i) == before
    assert doc.page.objects[i].style.line_width == width0
    doc.redo()
    assert doc.object_properties(i)["MVXMAXPT"] == "250"


def test_window_apply_refreshes_and_is_one_ctrl_z(page):
    _app()
    from ctgedit.ui.main_window import MainWindow

    win = MainWindow()
    try:
        win.open_document(page(MOTION))
        i = _named(win.doc, "FxSliderHV")
        before = dict(win.doc.object_properties(i))
        win.object_properties(i)
        dlg = win._dialogs[("objprops", i)]
        calls = []
        dlg.applied.connect(lambda: calls.append(1))
        _w(dlg, "MVXMINPT").setValue(-20)
        _w(dlg, "DEVICE").setText("FxLog")
        box = dlg.findChild(QtWidgets.QDialogButtonBox)
        box.button(QtWidgets.QDialogButtonBox.Apply).click()
        assert calls == [1]
        # the window refreshed: its page is the one the edit re-rendered
        assert win._page is win.doc.page
        assert win.act_undo.text() == "Undo Object Properties"
        assert win.doc.object_properties(i)["DEVICE"] == "FxLog"
        n = len(win.doc._undo)
        # OK applies (and refreshes) too, then closes the sheet
        _w(dlg, "DESC").setText("Two axis slider")
        box.button(QtWidgets.QDialogButtonBox.Ok).click()
        assert calls == [1, 1] and win._page is win.doc.page
        assert ("objprops", i) not in win._dialogs
        assert len(win.doc._undo) == n + 1
        win.undo()
        win.undo()
        assert win.doc.object_properties(i) == before
    finally:
        if win in MainWindow.instances:
            MainWindow.instances.remove(win)


# ------------------------------------------------------------------ sliders / clear
def test_slider_mode_is_4097_or_1_and_goes_with_the_tag(page):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page(VEHICLE))
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    assert not _w(dlg, "MVXSLMODE").isEnabled()
    _w(dlg, "MVXSL").setText("Loop_1_SP")
    # CtDraw (live): a new object's box is unticked; the typed tag saves '1'
    assert _w(dlg, "MVXSLMODE").isEnabled() and not _w(dlg, "MVXSLMODE").isChecked()
    assert dlg.apply()
    assert doc.object_properties(i)["MVXSLMODE"] == "1"
    _w(dlg, "MVXSLMODE").setChecked(True)
    assert dlg.apply()
    assert doc.object_properties(i)["MVXSLMODE"] == "4097"
    _w(dlg, "MVXSL").setText("")
    assert dlg.apply()
    rp = doc.object_properties(i)
    assert "MVXSL" not in rp and "MVXSLMODE" not in rp


def test_clear_property_removes_every_key_of_the_property(page):
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops import _SHEET_TABS

    subs = {(t, s.title): s for t, ss in _SHEET_TABS for s in (ss or [])}
    doc = EditableDocument.open(page(MOTION))
    # FxRotSlider: a rotational slider about a CUSTOM centre (ROTMODE 1); the test gives it
    # a horizontal movement too, the property that must stay
    i = _named(doc, "FxRotSlider")
    rot = ("ROTSL", "ROTSLMODE", "ROTMINPC", "ROTMAXPC", "ROTMODE", "ROTXORG", "ROTYORG")
    assert all(k in doc.object_properties(i) for k in rot)
    for k, v in (("MVX", "FX_LEVEL1"), ("MVXMINPT", "0"), ("MVXMAXPT", "500")):
        assert doc.ensure_property(i, k, v)
    dlg = _sheet(doc, i)
    dlg._clear(subs[("Slider", "Rotational")])
    assert dlg.apply()
    rp = doc.object_properties(i)
    assert [k for k in rot if k in rp] == []
    assert rp["MVXMINPT"] == "0" and rp["MVXMAXPT"] == "500"  # other properties stay

    # a slider's offsets are the movement's: clearing the slider keeps them
    # while horizontal movement is on; clearing the movement removes them
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    _w(dlg, "MVX").setText("Level_1")
    _w(dlg, "MVXMINPT").setValue(10)
    _w(dlg, "MVXMAXPT").setValue(20)
    assert dlg.apply()
    dlg._clear(subs[("Slider", "Horizontal")])
    assert dlg.apply() is False  # nothing to remove
    rp = doc.object_properties(i)
    assert (rp["MVX"], rp["MVXMINPT"], rp["MVXMAXPT"]) == ("Level_1", "10", "20")
    dlg._clear(subs[("Movement", "Horizontal")])
    assert dlg.apply()
    rp = doc.object_properties(i)
    assert not {"MVX", "MVXMINPT", "MVXMAXPT"} & set(rp)

    # Appearance > Visibility has its own Clear Property button
    from ctgedit.ui.objprops import _VISIBILITY_SUB

    _w(dlg, "VISIBLE").setText("NOT Pump_Running")
    assert dlg.apply() and doc.object_properties(i)["VISIBLE"] == "NOT Pump_Running"
    page = _w(dlg, "VISIBLE").parentWidget()
    clear = [b for b in page.findChildren(QtWidgets.QPushButton) if b.text() == "Clear Property"]
    assert len(clear) == 1
    clear[0].click()
    assert dlg.apply() and "VISIBLE" not in doc.object_properties(i)
    assert _VISIBILITY_SUB.family == ("VISIBLE",)


# ------------------------------------------------------------------ validation / widgets
def test_spin_boxes_and_length_limits(page):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page(VEHICLE))
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    long_tip = "x" * 60
    assert doc.ensure_property(i, "COMMENT", long_tip)
    dlg = _sheet(doc, i)
    # offsets / percentages / degrees are spin boxes with negative and >100 values
    # (the Input tab -- CMD / RPT_RATE / MSG_LOG -- is ui/objprops/input: tests/test_input_records.py)
    for key in ("MVXMINPT", "MVYMAXPT", "SZXMAXPC", "ROTMINPC"):
        assert isinstance(_w(dlg, key), QtWidgets.QSpinBox), key
    sp = _w(dlg, "SZXMAXPC")
    sp.stepBy(1)  # (not set) -> CtDraw default
    assert sp.value() == 100
    sp.setValue(25600)
    assert sp.value() == 25600
    _w(dlg, "MVXMINPT").setValue(-4000)
    assert _w(dlg, "MVXMINPT").value() == -4000
    # documented maximum lengths
    for key, limit in (("VISIBLE", 254), ("DISABLED", 254), ("MVX", 253), ("MVXSL", 79), ("__AN_NAME__", 32)):
        assert _w(dlg, key).maxLength() == limit, key
    # (Fill > Color is its own page: a multi-state condition takes 128)
    assert dlg.fill_color_editor._cond_edit.maxLength() == 128
    # a stored value longer than the limit is shown whole, not truncated ...
    assert _w(dlg, "COMMENT").text() == long_tip
    n = len(doc._undo)
    _w(dlg, "COMMENT").setText("z" * 60)
    _w(dlg, "MVX").setText("Tag_1")
    # ... but a change that is still too long is refused, nothing written
    assert dlg.apply() is False
    assert "too long" in dlg._status.text() and "48" in dlg._status.text()
    assert len(doc._undo) == n and "MVX" not in doc.object_properties(i)
    # the tab holding the field is brought to the front
    inner = dlg._tabs.currentWidget()
    assert dlg._tabs.tabText(dlg._tabs.currentIndex()) == "Access"
    assert inner.tabText(inner.currentIndex()) == "General"
    _w(dlg, "COMMENT").setText("short")
    assert dlg.apply() is True and dlg._status.isHidden()
    rp = doc.object_properties(i)
    assert rp["COMMENT"] == "short" and rp["MVX"] == "Tag_1"
    assert rp["SZXMAXPC"] == "25600" and rp["MVXMINPT"] == "-4000"


def test_unencodable_text_is_refused_before_anything_is_written(page):
    """A caption the page code page cannot store stops the whole Apply (the
    font size changed on the same sheet is not written either)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page(VEHICLE))
    i = doc.create_object("text", rect=(5, 5, 80, 30), text="Hi")
    body = bytes(doc.parsed.page.objects[i].body_raw)
    dlg = _sheet(doc, i)
    n = len(doc._undo)
    assert dlg._sp_width is None
    dlg._sp_fsize.setValue(dlg._sp_fsize.value() + 3)
    dlg._ed_caption.setText("漢字")
    assert dlg.apply() is False
    assert "cannot be stored" in dlg._status.text()
    assert len(doc._undo) == n and bytes(doc.parsed.page.objects[i].body_raw) == body
    _w(dlg, "DESC").setText("café ☃")  # a field value too
    dlg._ed_caption.setText("Hi")
    assert dlg.apply() is False and len(doc._undo) == n
    inner = dlg._tabs.currentWidget()
    assert inner.tabText(inner.currentIndex()) == "General"  # Access > General


def test_disable_when_stores_ctdraws_default_style(page):
    """CtDraw's Disable page always stores the checked style radio, and an
    absent GREY_STYLE checks Embossed (GRAY_SUNK)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page(VEHICLE))
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    assert _w(dlg, "GREY_STYLE").currentData() is None
    _w(dlg, "DISABLED").setText("NOT Operator_OK")
    assert dlg.apply()
    rp = doc.object_properties(i)
    assert (rp["DISABLED"], rp["GREY_STYLE"]) == ("NOT Operator_OK", "GRAY_SUNK")
    assert _w(dlg, "GREY_STYLE").currentText() == "Embossed"  # the sheet shows it
    assert dlg.apply() is False  # and nothing is pending
    _w(dlg, "GREY_STYLE").setCurrentIndex(3)  # Hidden
    assert dlg.apply() and doc.object_properties(i)["GREY_STYLE"] == "GRAY_HIDE"
    # a chosen style without 'Disable when' is stored as chosen, nothing added
    j = doc.create_object("rectangle", rect=(90, 5, 160, 60), colour=0x336699)
    dlg = _sheet(doc, j)
    _w(dlg, "DISMODE").setChecked(True)
    assert dlg.apply()
    rp = doc.object_properties(j)
    assert rp["DISMODE"] == "TRUE" and "GREY_STYLE" not in rp and "DISABLED" not in rp


def test_a_value_a_spin_box_cannot_show_is_kept_and_cleared(page):
    """After a reload (Convert) a stored value that is not a plain number
    locks its spin box: it is never rewritten, and Clear Property removes it."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops import _SHEET_TABS

    subs = {(t, s.title): s for t, ss in _SHEET_TABS for s in (ss or [])}
    doc = EditableDocument.open(page(VEHICLE))
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    dlg.show_page("move_h")  # (built before the record changes: its spin box locks)
    assert doc.ensure_property(i, "MVX", "Level_1")
    assert doc.ensure_property(i, "MVXMAXPT", "%span%")
    dlg._load_values()
    sp = _w(dlg, "MVXMAXPT")
    assert sp.locked and not sp.isEnabled() and "%span%" in sp.toolTip()
    _w(dlg, "MVX").setText("Level_2")
    assert dlg.apply()
    assert doc.object_properties(i)["MVXMAXPT"] == "%span%"
    dlg._clear(subs[("Movement", "Horizontal")])
    assert dlg.apply()
    assert not {"MVX", "MVXMAXPT"} & set(doc.object_properties(i))
    dlg._load_values()
    assert not sp.locked and sp.isEnabled()


def test_user_marker_on_a_tooltip_is_kept(page):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page(MOTION))
    i = _named(doc, "FxSliderHV")
    assert doc.object_properties(i)["COMMENT"] == "@(Fx Start)<User>"
    dlg = _sheet(doc, i)
    _w(dlg, "COMMENT").setText("@(Drag it)")
    assert dlg.apply()
    assert doc.object_properties(i)["COMMENT"] == "@(Drag it)<User>"


def test_genie_member_tooltip_gets_ctdraws_user_marker(page):
    """Live CtDraw: a tool tip set on a GENIE MEMBER is stored
    'text<User>' (both bags); a free object's and a plain group member's is
    the text alone. CtDraw's page load keeps a member COMMENT only with the
    marker -- an unmarked one was dropped by its next save."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page(GENIES))
    # FxPump1 of FxGenies: a genie (fxgenie.fx_pump) with text members
    g = _named(doc, "FxPump1")
    t = next(j for j in doc.page.objects[g].members if doc.parsed.page.objects[j].type_code == 0x05)
    assert doc.page.objects[t].parent == g and doc.page.objects[g].is_genie
    # the sheet of a genie member is read-only in ctgedit (its properties come
    # from the genie definition), so the marker rule lives in the document.
    # (CtDraw's member sheet -- Ctrl+double-click -- leaves Access > General
    # editable, and every one of those fields gets the marker, live)
    assert _sheet(doc, t).read_only_reason  # a text of the genie
    assert doc.ensure_property(t, "COMMENT", "member tip")
    assert doc.object_properties(t)["COMMENT"] == "member tip<User>"
    assert _w(_sheet(doc, t), "COMMENT").text() == "member tip"
    for key, value in (("DESC", "member desc"), ("AREA", "2"), ("PRIV", "3"), ("DEVICE", "AlarmDisk")):
        assert doc.ensure_property(t, key, value)
        assert doc.object_properties(t)[key] == value + "<User>", key
    assert doc.ensure_property(t, "VISIBLE", "X")  # not an Access field
    assert doc.object_properties(t)["VISIBLE"] == "X"
    r = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    assert doc.ensure_property(r, "COMMENT", "free tip")
    assert doc.object_properties(r)["COMMENT"] == "free tip"
    a = doc.create_object("rectangle", rect=(100, 5, 140, 40), colour=0x336699)
    b = doc.create_object("rectangle", rect=(150, 5, 190, 40), colour=0x336699)
    g = doc.group_objects([a, b])
    m = doc.page.objects[g].members[-1]
    assert doc.ensure_property(m, "COMMENT", "group member tip")
    assert doc.object_properties(m)["COMMENT"] == "group member tip"


def test_object_name_writes_an_name_and_header(page):
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G

    doc = EditableDocument.open(page(VEHICLE))
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    _w(dlg, "__AN_NAME__").setText("Pump_Frame")
    assert dlg.apply()
    po = doc.parsed.page.objects[i]
    assert doc.object_properties(i)["__AN_NAME__"] == "Pump_Frame"
    assert G.parse_geom_header(po.body_raw).name == "Pump_Frame"
    assert "Name" not in doc.object_properties(i)
    _w(dlg, "__AN_NAME__").setText("")
    assert dlg.apply()
    assert "__AN_NAME__" not in doc.object_properties(i)
    assert G.parse_geom_header(doc.parsed.page.objects[i].body_raw).name == ""


def test_properties_panel_shows_rotation_in_degrees(page):
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.panels.properties import PropertyPanel

    _app()
    doc = EditableDocument.open(page(MOTION))
    panel = PropertyPanel()
    panel.set_target(doc, [_named(doc, "FxSymRotate")])
    box = next(b for b in panel.findChildren(QtWidgets.QGroupBox) if b.title() == "Movement / Scaling")
    labels = [lab.text() for lab in box.findChildren(QtWidgets.QLabel)]
    # the rotation expression, then its angles: short labels, the Object Properties names
    # in their tool tips, the unit as each field's suffix
    assert "Rotation" in labels
    assert "Min angle" in labels and "Max angle" in labels
    spins = box.findChildren(QtWidgets.QAbstractSpinBox)
    tips = " ".join(
        [sp.toolTip() for sp in spins] + [lab.toolTip() for lab in box.findChildren(QtWidgets.QLabel)]
    )
    assert "Angle at minimum (anticlockwise)" in tips and "Angle at maximum (clockwise)" in tips
    assert [sp.suffix() for sp in spins].count("°") == 2
    assert not any("%" in t for t in labels if t.startswith(("Rotation", "Min angle", "Max angle")))
