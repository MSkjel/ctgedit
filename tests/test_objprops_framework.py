"""The object sheet framework."""

import json
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
pytestmark = pytest.mark.usefixtures("collect_between_tests")

from PySide6 import QtWidgets  # noqa: E402

from ctgedit.ui.objprops import registry as R  # noqa: E402
from ctgedit.ui.objprops.range_field import (
    default_range,
    join_format,  # noqa: E402
    join_range,
    split_format,
    split_range,
)
from qtkit import app as _app

#: the fake install's pages (set by the autouse ``_fx`` fixture)
FX = {}
#: AlarmAck as the stand-in function table and help give it (:func:`_builtins`)
ACK = "AlarmAck(Mode, Value [,ClusterName])"
ACK_SIGNATURE = "INT AlarmAck(INT Mode, INT Value [, STRING ClusterName])"


@pytest.fixture(autouse=True)
def _fx(fx_install):
    FX.clear()
    FX.update(
        install=fx_install,
        shapes=fx_install.page("FxShapes"),
        genies=fx_install.page("FxGenies"),
        main=fx_install.project("FxMain"),
    )
    yield


def _named(doc, name):
    found = doc.find_objects_by_name(name)
    assert len(found) == 1, name
    return found[0]


@pytest.fixture
def builtins(fx_install):
    """The built-in function list of the fake install: a stand-in Bin/FUNC0.DBF (the
    argument names) and a stand-in help (Docs/toc.json and one topic: AlarmAck's
    signature and category), as the install's help and function table give them."""
    from ctgedit.io import cicode_catalog as C

    fx_install.write_func0(list(fx_install_func0_rows()))
    docs = os.path.join(fx_install.root, "Docs")
    os.makedirs(docs, exist_ok=True)
    toc = {
        "items": [
            {
                "name": "Cicode Function Categories",
                "items": [{"name": "Alarm Functions", "items": [{"name": "AlarmAck", "id": "1001"}]}],
            }
        ]
    }
    with open(os.path.join(docs, "toc.json"), "w", encoding="utf-8") as fh:
        json.dump(toc, fh)
    with open(os.path.join(docs, "1001.html"), "w", encoding="utf-8") as fh:
        fh.write(
            '<h5>AlarmAck</h5>\n<p class="paragraph">Acknowledges alarms.</p>\n'
            '<p class="subheading">Syntax</p>\n<p class="paragraph">%s</p>\n' % ACK_SIGNATURE
        )
    C._builtin_cached.cache_clear()
    yield
    C._builtin_cached.cache_clear()


def fx_install_func0_rows():
    import fxinstall

    return tuple(fxinstall.FUNC0_ROWS) + (("AlarmAck", "(Mode, Value [,ClusterName])"),)


def _copy(tmp_path, src=None):
    """A copy of ``src`` (default FxShapes) in tmp_path."""
    src = src or FX["shapes"]
    dst = tmp_path / os.path.basename(src)
    shutil.copy2(src, dst)
    return str(dst)


def _sheet(doc, index, **kw):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    return ObjectPropertiesDialog(doc, index, **kw)


def _ctx(tc, dwflags=0x8, ext=""):
    return R.SheetContext(None, 0, tc, dwflags, ext, {}, "")


def _ids(tc, dwflags=0x8, ext=""):
    return [p.id for p in R.pages_for(_ctx(tc, dwflags, ext))]


def _tabs(dlg):
    return [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())]


# ------------------------------------------------------------------ the tab table
COMMON = [
    "visibility",
    "move_h",
    "move_v",
    "move_rot",
    "scale_h",
    "scale_v",
    "fill_color",
    "fill_level",
    "touch",
    "keyboard",
    "slider_h",
    "slider_v",
    "slider_rot",
    "access",
    "disable",
    "metadata",
]


def test_tab_table_per_class():
    """CtDraw's page masks and own pages, per type code."""
    shape = ["general", "effects"] + COMMON
    for tc in (0x01, 0x02, 0x03, 0x0E, 0x0F, 0x04, 0x11):
        assert _ids(tc) == shape, hex(tc)
    for tc in (0x12, 0x0B):  # rail, pipe: no 3D Effects
        assert _ids(tc) == ["general"] + COMMON, hex(tc)
    assert _ids(0x05) == ["general", "effects", "display_value"] + COMMON  # text
    for tc in (0x0A, 0x10):  # polylines + Vertices
        assert _ids(tc) == ["general", "effects", "visibility", "vertices"] + COMMON[1:]
    assert _ids(0x07) == COMMON  # an unlinked bitmap: no own page
    assert _ids(0x07, 0x9) == ["linkage"] + COMMON  # a placed (linked) symbol
    assert _ids(0x08) == COMMON + ["alarm_indicator"]  # group
    assert _ids(0x08, 0x209, "ctm") == ["access", "metadata", "alarm_indicator"]  # genie
    assert _ids(0x08, 0x9, "ctl") == ["linkage"] + COMMON  # a symbol group
    assert _ids(0x08, 0xD, "ctt") == ["linkage"]  # the page template
    assert _ids(0x0C) == [
        "set_object",
        "visibility",
        "move_h",
        "move_v",
        "move_rot",
        "scale_h",
        "scale_v",
        "fill_level",
        "touch",
        "keyboard",
        "slider_h",
        "slider_v",
        "slider_rot",
        "access",
        "disable",
        "metadata",
    ]  # symbol set
    assert _ids(0x13) == [
        "general",
        "visibility",
        "move_h",
        "move_v",
        "scale_h",
        "scale_v",
        "access",
        "metadata",
    ]  # web content
    assert _ids(0x0D) == [
        "general",
        "tag_association",
        "visibility",
        "move_h",
        "move_v",
        "scale_h",
        "scale_v",
        "access",
        "identification",
        "metadata",
    ]
    assert _ids(0x1001) == [
        "general",
        "effects",
        "visibility",
        "move_h",
        "move_v",
        "touch",
        "keyboard",
        "access",
        "disable",
        "metadata",
    ]  # button
    for tc in (0x1003, 0x1006):  # trend, digital text
        assert _ids(tc) == ["general", "visibility", "keyboard", "access", "metadata"]
    assert _ids(0x1009) == ["general", "keyboard", "access"]  # Cicode object
    cic = R.pages_for(_ctx(0x1009))[0]
    assert (cic.tab, cic.sub) == ("Cicode", "General")
    for tc in (0x1000, 0x1002, 0x1004, 0x1005, 0x1007, 0x1008, 0x100B, 0x100D, 0x100F, 0x1010, 0x09):
        assert _ids(tc) == [] and not _ctx(tc).has_sheet(), hex(tc)


def test_configured_and_disabled_pages():
    ctx = _ctx(0x0F)
    props = {"MVX": "Tag", "MVXSL": "", "VISIBLE": " ", "COLMODE": "1", "COL_C": "X > 1", "AI_ON": "0"}
    got = R.configured_pages(ctx, props)
    assert got == {"move_h", "fill_color"}
    assert R.disabled_pages(got) == {"slider_h": "move_h"}
    # a slider owns the axis: movement is not 'configured', the slider is
    props = {"MVX": "Tag", "MVXSL": "Tag", "MVXSLMODE": "4097"}
    got = R.configured_pages(ctx, props)
    assert got == {"slider_h"}
    assert R.disabled_pages(got) == {"move_h": "slider_h", "slider_rot": "slider_h"}
    assert R.configured_pages(_ctx(0x10), {"VERTEX_VERTEXP0": "Tag"}) == {"vertices"}
    assert "alarm_indicator" in R.configured_pages(_ctx(0x08), {"AI_ON": "1"})


# ------------------------------------------------------------------ the sheet
def test_sheet_tabs_follow_the_class(tmp_path):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path))
    rect = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    text = doc.create_object("text", rect=(5, 70, 80, 90), text="Hi")
    line = doc.create_object("line", rect=(100, 5, 180, 60))
    trend = doc.create_object("trend", rect=(200, 5, 400, 160))
    dlg = _sheet(doc, rect)
    assert _tabs(dlg) == [
        "Appearance",
        "Movement",
        "Scaling",
        "Fill",
        "Input",
        "Slider",
        "Access",
        "Metadata",
    ]
    appearance = dlg._tabs.widget(0)
    # 3D Effects: page bit 0x1 of the shape classes
    assert [appearance.tabText(i) for i in range(appearance.count())] == [
        "General",
        "3D Effects",
        "Visibility",
    ]
    assert "display_value" not in dlg.page_ids()  # CtDraw: text objects only
    dlg = _sheet(doc, text)
    appearance = dlg._tabs.widget(0)
    assert [appearance.tabText(i) for i in range(appearance.count())] == [
        "General",
        "3D Effects",
        "Display Value",
        "Visibility",
    ]
    assert dlg.display_value_editor is not None  # the Type selector page
    dlg = _sheet(doc, line)
    assert "display_value" not in dlg.page_ids() and "TXTMODE" not in dlg._widgets
    # a trend: Visibility, Keyboard Commands (no Touch), Access > General only
    dlg = _sheet(doc, trend)
    assert _tabs(dlg) == ["Appearance", "Input", "Access", "Metadata"]
    inp = dlg._input
    assert not inp.isTabVisible(0) and inp.isTabVisible(1)
    access = dlg._tabs.widget(2)
    assert [access.tabText(i) for i in range(access.count())] == ["General"]
    assert "MVX" not in dlg._widgets and "DISABLED" not in dlg._widgets


def test_web_content_and_cicode_object_sheets(tmp_path):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path))
    web = doc.create_object("web_content", rect=(5, 5, 300, 200))
    assert doc.parsed.page.objects[web].type_code == 0x13
    dlg = _sheet(doc, web)
    assert _tabs(dlg) == ["Appearance", "Movement", "Scaling", "Access", "Metadata"]
    mv = dlg._tabs.widget(1)
    assert [mv.tabText(i) for i in range(mv.count())] == ["Horizontal", "Vertical"]
    # help 1172149: Tooltip and Log Device are disabled for Web Content
    assert not dlg._editors["COMMENT"].isEnabled() and not dlg._editors["DEVICE"].isEnabled()
    assert dlg._editors["DESC"].isEnabled()
    cic = doc.create_object("cicode_object", rect=(320, 5, 340, 25))
    assert doc.parsed.page.objects[cic].type_code == 0x1009
    dlg = _sheet(doc, cic)
    # the class's own Cicode > General page (ui/objprops/cicode.py)
    assert _tabs(dlg) == ["Cicode", "Input", "Access"]
    assert not dlg._input.isTabVisible(0)  # no Touch on a Cicode object


def test_genie_and_template_roots(tmp_path):
    """FxTplLinked: a page on fx_style's normal@XGA (its template root) with the genie
    FxTplPump."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path, FX["install"].page("FxTplLinked")))
    objs = doc.parsed.page.objects
    genie = _named(doc, "FxTplPump")
    assert objs[genie].subtype_size & 0x200
    tpl, _ref = doc.template_reference()
    dlg = _sheet(doc, genie)
    assert _tabs(dlg) == ["Access", "Metadata", "Alarm Indicator"] and not dlg.is_read_only()
    assert dlg.page_ids() == ["access", "metadata", "alarm_indicator"]
    # the page template's root: only CtDraw's Linkage page (ui/objprops/linkage.py: the
    # template's ref, library file and Set)
    dlg = _sheet(doc, tpl, read_only=False)
    assert R.pages_for(dlg.context)[0].id == "linkage"
    assert _tabs(dlg) == ["Appearance"] and dlg.page_ids() == ["linkage"]
    assert "template" in dlg.linkage_editor.kind.text()


def test_check_marks_and_disabled_pages(tmp_path):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path, FX["install"].page("FxMotion")))
    k = _named(doc, "FxSliderHV")  # horizontal + vertical sliders
    # (the fixture stores no MVYSLMODE: the vertical slider gets the mode its horizontal
    # one has, as CtDraw stores it for an object with both)
    assert doc.ensure_property(k, "MVYSLMODE", "4097")
    dlg = _sheet(doc, k)
    assert dlg.tab_marked("Slider") and dlg.tab_marked("Slider", "Horizontal")
    assert dlg.tab_marked("Access")  # its tool tip
    assert not dlg.tab_marked("Movement") and not dlg.tab_marked("Scaling")
    # the tab titles stay plain (the mark is the tab's icon)
    assert "Slider" in _tabs(dlg)
    move_h = dlg.page_widget("move_h")
    assert not move_h.isEnabled()  # the slider owns the axis
    mv = dlg._tabs.widget(_tabs(dlg).index("Movement"))
    assert "Slider > Horizontal" in mv.tabToolTip(0)
    assert dlg.page_widget("slider_h").isEnabled()
    assert not dlg.page_widget("slider_rot").isEnabled()
    # marks follow Apply
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    assert not dlg.tab_marked("Appearance")
    dlg._editors["VISIBLE"].setText("NOT Pump_Running")
    assert dlg.apply()
    assert dlg.tab_marked("Appearance") and dlg.tab_marked("Appearance", "Visibility")
    assert not dlg.tab_marked("Appearance", "General")
    dlg._editors["MVX"].setText("Level_1")
    assert dlg.apply()
    assert dlg.tab_marked("Movement", "Horizontal")
    assert not dlg.page_widget("slider_h").isEnabled()


# ------------------------------------------------------------------ Specify range
def test_range_split_and_join():
    assert split_range("(Tag)^(0,100)").expr == "Tag"
    r = split_range("(Level_1 * 2)^(-4000, 4000)")
    assert (r.expr, r.lo, r.hi) == ("Level_1 * 2", "-4000", " 4000")
    assert join_range(r.expr, r.lo, r.hi, r.trail) == "(Level_1 * 2)^(-4000, 4000)"
    # the LAST '^' decides (CtDraw's strrchr); a nested range stays inside
    r = split_range("((A)^(0,1))^(0.5,2.25)")
    assert (r.expr, r.lo, r.hi) == ("(A)^(0,1)", "0.5", "2.25")
    for plain in ("Tag", "(Tag)", "(A)^B", "(A)^(x,1)", "(A)^(0,1) + 1", "@(Hi ^(there^))", "(A) ^(0,1)", ""):
        assert split_range(plain) is None, plain
    assert split_format("(tag.PV):#.#") == ("tag.PV", "#.#")
    assert split_format("tag.PV:#.#") is None
    assert join_format("T", "##.#") == "(T):##.#"
    assert join_format("T", "##.#", "0", "10") == "(T)^(0,10):##.#"


def test_default_range_hint():
    tags = {"T1": ("0", "250"), "T2": ("", "")}
    look = tags.get
    # the Graphics Builder's pre-fill (live): the tag's text, else '0.0' /
    # '32000.0', without a tag '0.0' / '100.0'
    assert default_range("T1 * 2", look)[:2] == ("0", "250")
    assert default_range("T1 + T2", look)[:2] == ("0.0", "32000.0")  # the LAST tag decides
    assert default_range('TagInfo("T1", 0) + 5', look)[:2] == ("0.0", "100.0")
    assert default_range("", look)[:2] == ("0.0", "100.0")
    # a LOCAL variable is a tag without a range (live: TT001 0..4095 -> 0.0 / 32000.0)
    from ctgedit.io.project import TagEntry, TagIndex
    from ctgedit.ui.objprops.expression import ExpressionContext

    ctx = ExpressionContext()
    ctx.tag_index = lambda: TagIndex(
        [
            TagEntry("TT001", "local", "P", "INT", "", "0", "4095"),
            TagEntry("LOOP_2_PV", "variable", "P", "INT", "", "0", "200.0"),
        ]
    )
    assert default_range("TT001", ctx.tag_range)[:2] == ("0.0", "32000.0")
    assert default_range("LOOP_2_PV + TT001", ctx.tag_range)[:2] == ("0.0", "32000.0")
    assert default_range("TT001 + LOOP_2_PV", ctx.tag_range)[:2] == ("0", "200.0")


def test_expression_field_specify_range():
    _app()
    from ctgedit.ui.objprops.expression import ExpressionContext, ExpressionField
    from ctgedit.ui.objprops.range_field import RangeField

    f = ExpressionField(ExpressionContext())
    f.attach_range(RangeField())
    rf = f.range_field
    f.setText("(Level_1)^( 0 , 100 )")
    assert f.inner_text() == "Level_1" and rf.specified()
    assert (rf.minimum.text(), rf.maximum.text()) == ("0", "100")
    assert f.text() == "(Level_1)^( 0 , 100 )"  # untouched: identical text
    got = []
    f.valueChanged.connect(got.append)
    rf.maximum.setText("250")
    assert f.text() == "(Level_1)^( 0 ,250)" and got[-1] == f.text()
    rf.check.setChecked(False)
    assert f.text() == "Level_1" and not rf.minimum.isEnabled()
    rf.check.setChecked(True)  # ticks keep / prefill numbers
    assert f.text().startswith("(Level_1)^(")
    rf.minimum.setText("")
    assert "Minimum" in (f.range_problem() or "")
    # a plain expression shows no range; ticking fills in the default range
    f.setText("Level_2")
    assert not rf.specified() and rf.minimum.placeholderText() == "0.0"
    rf.check.setChecked(True)
    assert f.text() == "(Level_2)^(0.0,100.0)"
    # a range typed in the expression moves into the fields when editing ends
    f.setText("")
    f.set_value("X")
    f.selectAll()
    f.insert("(X)^(1,9)")
    assert not rf.specified()
    f.editingFinished.emit()
    assert rf.specified() and f.inner_text() == "X" and f.text() == "(X)^(1,9)"


def test_sheet_range_fields_store_the_suffix(tmp_path):
    """A rotation stored with a range (FxRotExpress of FxMotion, its ROT given the form
    a page stores, '(FxExpanded())^(0,1)') shows in the range fields."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path, FX["install"].page("FxMotion")))
    k = _named(doc, "FxRotExpress")
    assert doc.set_property(k, "ROT", "(FxExpanded())^(0,1)")
    dlg = _sheet(doc, k, read_only=False)
    rot = dlg._editors["ROT"]
    assert rot.text() == "(FxExpanded())^(0,1)"
    assert rot.inner_text() == "FxExpanded()" and rot.range_field.specified()
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    for key in ("MVX", "MVY", "ROT", "SZX", "SZY", "FIL"):
        assert dlg._editors[key].range_field is not None, key
    mvx = dlg._editors["MVX"]
    mvx.setText("Level_1")
    mvx.range_field.check.setChecked(True)
    mvx.range_field.minimum.setText("-50")
    mvx.range_field.maximum.setText("50")
    n = len(doc._undo)
    assert dlg.apply() and len(doc._undo) == n + 1
    assert doc.object_properties(i)["MVX"] == "(Level_1)^(-50,50)"
    mvx.range_field.maximum.setText("")
    assert dlg.apply() is False and "Maximum" in dlg._status.text()
    assert doc.object_properties(i)["MVX"] == "(Level_1)^(-50,50)"
    mvx.range_field.check.setChecked(False)
    assert dlg.apply() and doc.object_properties(i)["MVX"] == "Level_1"


# ------------------------------------------------------------------ Wizard / completion
def test_completer_offers_the_include_chain(tmp_path):
    """On an FxMain page the expression fields offer the included projects'
    names: FxLib's local variable fx_lib_local, the Equip.Item references
    (FxPlant.Pump1.Run), the project's own tags; completion replaces only the name at
    the cursor."""
    from ctgedit.editing import EditableDocument
    from PySide6 import QtTest

    # the page in its project (the include chain is the project's); never saved
    doc = EditableDocument.open(FX["shapes"])
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    ctx = dlg.expression_context
    names = ctx.tag_names()
    assert "fx_lib_local" in names  # FxLib locvar.DBF
    assert ctx.tag_index().get("fx_lib_local").project == "FxLib"
    assert "FxPlant.Pump1.Run" in names  # Equip.Item
    assert "FX_SPEED" in names  # the project's own tag
    vis = dlg._editors["VISIBLE"]
    assert "fx_lib_local" in vis.completion_names("fx_lib_lo")
    vis.setText("NOT ")
    vis.setCursorPosition(4)
    dlg.show()
    QtTest.QTest.keyClicks(vis, "fx_lib_loc")
    comp = vis.name_completer()
    assert comp.popup().isVisible() and comp.completionPrefix() == "fx_lib_loc"
    comp.activated[str].emit("fx_lib_local")
    assert vis.text() == "NOT fx_lib_local"  # only the name was replaced
    dlg.close()


def test_insert_function_at_the_cursor(builtins, tmp_path, monkeypatch):
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops.expression import FunctionPickerDialog

    doc = EditableDocument.open(FX["shapes"])  # in its project; never saved
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    ctx = dlg.expression_context
    pick = FunctionPickerDialog(dlg, ctx)
    # the Graphics Builder's text (live): 'Insert arguments' ticked = the name
    # and Bin\FUNC0.DBF's argument names, cleared = the empty call
    ack = ACK
    assert pick.arguments.isChecked()
    assert pick.select_text("AlarmAck") and pick.chosen() == ack
    pick.arguments.setChecked(False)
    assert pick.chosen() == "AlarmAck()"
    assert "INT AlarmAck(INT Mode" in pick.detail.text()
    pick.kind.setCurrentIndex(2)  # the include chain's functions
    assert pick.select_text("FxLibShow")
    assert "FxLib" in pick.detail.text()
    # the Wizard menu inserts it where the cursor was
    f = dlg._editors["DISABLED"]
    f.setText("NOT () AND X")
    f.setCursorPosition(5)
    monkeypatch.setattr(
        FunctionPickerDialog,
        "exec",
        lambda self: (self.select_text("AlarmAck"), self._accept(), QtWidgets.QDialog.Accepted)[-1],
    )
    menu = f._menu
    menu.aboutToShow.emit()
    act = next(a for a in menu.actions() if a.text() == "Insert Function…")
    act.trigger()
    assert f.text() == "NOT (%s) AND X" % ack
    assert f.cursorPosition() == len("NOT (" + ack)


def test_insert_tag_and_trend_pickers(tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops.expression import ExpressionContext, TagPickerDialog, TrendTagPickerDialog

    _app()
    doc = EditableDocument.open(FX["shapes"])  # in its project; never saved
    ctx = ExpressionContext.for_document(doc)
    dlg = TagPickerDialog(None, ctx)
    assert dlg.select_text("fx_lib_local") and dlg.chosen() == "fx_lib_local"
    assert "Local variable" in dlg.detail.text() and "FxLib" in dlg.detail.text()
    dlg.kind.setCurrentIndex(3)  # Equipment items
    assert dlg.select_text("Pump1.Run")
    assert dlg.chosen() == "FxPlant.Pump1.Run"
    dlg.kind.setCurrentIndex(2)  # local variables only
    assert not dlg.select_text("FX_SPEED")
    # a tag with an engineering range is described, not a crash in the slot
    from ctgedit.io.project import TagEntry

    assert dlg.describe(TagEntry("T1", "variable", "P", "REAL", "", "0", "100", "%")) == (
        "Variable tag T1, type REAL, range 0 to 100 %, project P."
    )
    assert "range 0 to 5, " in dlg.describe(TagEntry("T2", "variable", "P", "", "", "0", "5"))
    # the Graphics Builder's list (live): with [CtEdit]
    # DisplayEquipmentItem=1 (its default) only the Equip.Item references,
    # with 0 the variable tags + local variables -- ctgedit offers both
    kinds = {e.kind for e in ctx.tag_index().entries}
    assert {"variable", "local", "equipment"} <= kinds
    tr = TrendTagPickerDialog(None, ctx)
    assert tr.model.rowCount() == 108  # (FxMain's trends)
    assert tr.select_text("FX_TR_FX_LEVEL1")
    assert tr.chosen() == "FX_TR_FX_LEVEL1"
    from ctgedit.ui.help import DIALOG_TOPICS

    for cls in ("ListPickerDialog", "TagPickerDialog", "FunctionPickerDialog", "TrendTagPickerDialog"):
        assert cls in DIALOG_TOPICS


def test_command_fields_and_advice(tmp_path, monkeypatch):
    """The Input tab's command fields get the Wizard too (insert at the
    cursor of the multi-line command); an expression field shows advice
    after an edit and never refuses the value for it."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops.expression import TagPickerDialog

    doc = EditableDocument.open(_copy(tmp_path))
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    cmd = dlg._input.command_edits["CMD"]
    assert cmd.wizard is not None
    cmd.setPlainText("Toggle()")
    cur = cmd.textCursor()
    cur.setPosition(len("Toggle("))
    cmd.setTextCursor(cur)
    monkeypatch.setattr(TagPickerDialog, "pick", classmethod(lambda cls, p, c=None: "Pump_1"))
    menu = cmd.wizard.menu()
    menu.aboutToShow.emit()
    next(a for a in menu.actions() if a.text() == "Insert Tag…").trigger()
    assert cmd.toPlainText() == "Toggle(Pump_1)"
    vis = dlg._editors["VISIBLE"]
    vis.setText("NOT (Pump_1")
    assert vis.advice_text() == ""  # nothing until the edit ends
    vis.editingFinished.emit()
    assert "not closed" in vis.advice_text()
    assert dlg.apply() and doc.object_properties(i)["VISIBLE"] == "NOT (Pump_1"


def test_advice_never_blocks():
    from ctgedit.ui.objprops import validators as V

    class Ctx:
        def is_function(self, name):
            return name.lower() in ("alarmack", "taginfo")

        def is_name(self, name):
            return name.lower() in ("pump_1",)

    # unknown tags and functions are the engine check's, not this advice's
    msgs = [p.message for p in V.advice('AlarmAck(0, 0) + Foo(Pump_1) + Nope + ("x"', "expr", Ctx())]
    assert any("'(' is not closed" in m for m in msgs)
    assert not any("Foo" in m or "Nope" in m or "AlarmAck" in m or "Pump_1" in m for m in msgs)
    assert all(p.level == V.WARNING for p in V.advice("Foo(", "expr", Ctx()))
    assert V.advice("%Tag%_PV AND Pump_1", "expr", Ctx()) == []
    assert V.advice("1 == 2", "expr", None)[0].message == "Cicode has no operator ==."
    assert V.advice('"a ^" quote" + ")"', "expr", None) == []  # strings skipped
    assert "too long" in V.blocking("x" * 300, 253, "Movement expression")
    assert V.blocking("NOT Tag", 253) is None
    seen = []
    hook = V.register(lambda text, kind, ctx: seen.append(text) or [V.Problem(V.WARNING, "hi")])
    try:
        assert any(p.message == "hi" for p in V.advice("X", "expr"))
    finally:
        V.unregister(hook)
    assert seen == ["X"]


def test_cicode_catalog_parse_and_builtins(builtins, tmp_path):
    from ctgedit.io import cicode_catalog as C

    src = (
        "// header\nINT FUNCTION Pump_Start(STRING sTag, INT n = 1)\n"
        "PRIVATE FUNCTION _Helper()\n! FUNCTION Commented()\n"
        "/* FUNCTION Blocked() */\nPUBLIC REAL\nFUNCTION Two_Line()\n"
        'FUNCTION Say()  ! "FUNCTION inline()"\n'
        # the Include project's style: type, FUNCTION and name on own lines
        "INT\r\nFUNCTION\r\nAssChainPage(STRING sPage,\r\n    INT n = 0)\r\n"
        "PRIVATE\nFUNCTION\n_Hidden()\n"
        # a '/*' inside a '//' banner opens no block comment (Include\AFilter.ci)
        "//*****************\nINT\nFUNCTION\nAfter_Banner(INT nIndex = -1)\n"
        "// see AlarmFilterForm */\n"
    )
    got = {f.name: f.signature for f in C.parse_ci(src, "P", "a.ci")}
    # (a return type on the line before FUNCTION is still the function's)
    assert got == {
        "Pump_Start": "INT Pump_Start(STRING sTag, INT n = 1)",
        "Two_Line": "REAL Two_Line()",
        "Say": "Say()",
        "AssChainPage": "INT AssChainPage(STRING sPage, INT n = 0)",
        "After_Banner": "INT After_Banner(INT nIndex = -1)",
    }
    # what the Graphics Builder inserts for a project function (live: the
    # declared argument NAMES, 'PlayWav(sFile, FromMs, ToMs, Mode)')
    ins = {f.name: f.insert_text() for f in C.parse_ci(src, "P", "a.ci")}
    assert ins == {
        "Pump_Start": "Pump_Start(sTag, n)",
        "Two_Line": "Two_Line()",
        "Say": "Say()",
        "AssChainPage": "AssChainPage(sPage, n)",
        "After_Banner": "After_Banner(nIndex)",
    }
    assert C._declared_argnames('String sFile, String FromMs="", Int Mode=0') == ("(sFile, FromMs, Mode)")
    assert C._declared_argnames('Object This, STRING s = "a,b", REAL X') == "(This, s, X)"
    # an entry without an argument list is inserted bare (live: 'AccControl')
    assert C.CicodeFunction("AccControl").insert_text() == "AccControl"
    assert C.CicodeFunction("AccControl").insert_text(False) == "AccControl"
    # (the fake install's stand-in help and function table: the ``builtins`` fixture)
    b = C.builtin_functions()
    ack = next(f for f in b if f.name == "AlarmAck")
    assert ack.insert_text() == ACK
    assert ack.insert_text(False) == "AlarmAck()"
    assert ack.signature == ACK_SIGNATURE and ack.category == "Alarm"
    cat = C.FunctionCatalog(FX["main"])
    assert cat.is_function("alarmack") is True and cat.is_function("NoSuchFunction") is False
    fx = {f.name: f for f in cat.project()}
    assert fx["FxLibShow"].source == "FxLib"


# ------------------------------------------------------------------ read-only sheets
def test_genie_member_sheet_is_read_only(tmp_path):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path, FX["genies"]))
    objs = doc.parsed.page.objects
    genie = next(r for r in doc.page.roots() if objs[r].subtype_size & 0x200)
    member = doc.page.objects[genie].members[0]
    body = bytes(objs[member].body_raw)
    defn = (os.path.join(FX["install"].project("FxLib"), "x.ctm"), "Elem")
    dlg = _sheet(doc, member, definition=defn)
    assert dlg.is_read_only() and "part of the genie" in dlg.read_only_reason
    # CtDraw's member sheet (live 8.50) greys every page but Access > General:
    # OK / Apply stay for those five fields (tests/test_access_metadata_ai.py)
    assert dlg.is_partly_editable()
    box = dlg._buttons
    assert box.button(QtWidgets.QDialogButtonBox.Apply) is not None
    assert dlg._ro_box.isVisibleTo(dlg) and dlg._btn_definition.isVisibleTo(dlg)
    fired = []
    dlg.editDefinitionRequested.connect(lambda: fired.append(1))
    dlg._btn_definition.click()
    assert fired == [1]
    vis = dlg._editors["VISIBLE"]
    assert vis.isReadOnly()
    clears = [b for b in dlg.findChildren(QtWidgets.QPushButton) if b.text() == "Clear Property"]
    assert clears and not any(b.isEnabled() for b in clears)
    n = len(doc._undo)
    vis.set_value("X = 1")  # even a programmatic change
    assert dlg.apply() is False and len(doc._undo) == n
    assert bytes(doc.parsed.page.objects[member].body_raw) == body
    # a template record is read-only too -- unless the caller says otherwise
    # (the page in its project: the template resolves through the include
    # chain; opened only, never saved) -- and wholly: Close, no Apply
    doc = EditableDocument.open(FX["install"].page("FxTplLinked"))
    t, _ref = doc.template_reference()
    assert t is not None
    assert "page template" in R.read_only_reason(doc, t)
    assert R.read_only_reason(doc, t, template_locked=False) == ""
    tpl = _sheet(doc, t)
    assert tpl.is_read_only() and not tpl.is_partly_editable()
    assert tpl._buttons.button(QtWidgets.QDialogButtonBox.Close) is not None
    assert tpl._buttons.button(QtWidgets.QDialogButtonBox.Apply) is None
    assert not _sheet(doc, t, read_only=False).is_read_only()


def test_window_opens_member_sheets_read_only(tmp_path):
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.editing import EditableDocument

    # FxGenies holds only genies: a copy with a free shape of the test's own
    path = _copy(tmp_path, FX["genies"])
    d = EditableDocument.open(path)
    d.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    d.save()
    win = MainWindow()
    try:
        win.open_document(path)
        objs = win.doc.parsed.page.objects
        genie = next(r for r in win.doc.page.roots() if objs[r].subtype_size & 0x200)
        member = win.doc.page.objects[genie].members[0]
        win.object_properties(member)
        dlg = win._dialogs[("objprops", member)]
        assert dlg.is_read_only()
        defn = win._definition_of(member)
        assert dlg._btn_definition.isVisibleTo(dlg) == (defn is not None)
        dlg.reject()
        # a genie member SYMBOL SET gets the read-only sheet too, not the
        # (editable) Symbol Set sheet -- live: CtDraw greys every page of a
        # member set but Access > General (FxGenies: FxLamp's set)
        sem = win.doc.page.objects
        sset = next(
            i
            for i in range(len(objs))
            if objs[i].type_code == 0x0C
            and sem[i].parent is not None
            and objs[sem[i].parent].subtype_size & 0x200
            and "part of the genie" in R.read_only_reason(win.doc, i)
        )
        win.object_properties(sset)
        assert ("symset", sset) not in win._dialogs
        assert win._dialogs[("objprops", sset)].is_read_only()
        win._dialogs[("objprops", sset)].reject()
        free = next(
            r
            for r in win.doc.page.roots()
            if not win._unit_locked(r) and not objs[r].class_flag and not R.read_only_reason(win.doc, r)
        )
        win.object_properties(free)
        assert not win._dialogs[("objprops", free)].is_read_only()
        win._dialogs[("objprops", free)].reject()
    finally:
        if win in MainWindow._instances:
            MainWindow._instances.remove(win)
        win.close()


# ------------------------------------------------------------------ multi-selection
def test_multi_selection_visibility_is_one_undo_step(tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.panels.properties import PropertyPanel

    _app()
    doc = EditableDocument.open(_copy(tmp_path))
    rects = [
        doc.create_object("rectangle", rect=(5 + 60 * k, 5, 50 + 60 * k, 40), colour=0x336699)
        for k in range(5)
    ]
    assert doc.ensure_property(rects[0], "VISIBLE", "Old = 1")
    panel = PropertyPanel()
    panel.set_target(doc, rects)
    box = next(
        b for b in panel.widget().findChildren(QtWidgets.QGroupBox) if b.title().startswith("Animation")
    )
    edits = {
        box.layout().labelForField(w).text(): w
        for w in box.findChildren(QtWidgets.QLineEdit)
        if box.layout().labelForField(w) is not None
    }
    vis = edits["Hidden when"]  # (all five have it: no count)
    assert vis.text() == "" and vis.placeholderText() == "(different values)"
    assert "Disable when" in edits and "Tool tip" in edits
    n = len(doc._undo)
    changed = []
    panel.changed.connect(lambda: changed.append(1))
    vis.setText("NOT Pump_Running")
    vis.editingFinished.emit()
    assert len(doc._undo) == n + 1 and changed
    assert all(doc.object_properties(r)["VISIBLE"] == "NOT Pump_Running" for r in rects)
    doc.undo()
    assert doc.object_properties(rects[0])["VISIBLE"] == "Old = 1"
    assert all("VISIBLE" not in doc.object_properties(r) for r in rects[1:])


def test_configured_page_keys_follow_the_mode():
    """The keys a page checks follow its mode."""
    rect, text = _ctx(0x0F), _ctx(0x05)
    fill = R.PAGE_BY_ID["fill_color"]
    dv = R.PAGE_BY_ID["display_value"]
    assert R.is_configured(fill, rect, {"COLMODE": "2", "COL_ARR": "Level / 10"})
    assert R.is_configured(fill, rect, {"COLMODE": "3", "COL_EXP": "Temperature"})
    assert R.is_configured(fill, rect, {"COLMODE": "4", "COL_EXP": "(T)^(0,1)"})
    assert not R.is_configured(fill, rect, {"COLMODE": "2", "COL_A": "X"})
    assert not R.is_configured(fill, rect, {"COL_ARR": "X"})  # mode 0 = COL_A
    assert R.is_configured(fill, rect, {"COL_A": "X"})
    assert not R.is_configured(fill, rect, {"COLMODE": "-1", "COL_A": "X"})
    assert not R.is_configured(dv, text, {"TXT_OFF": "Pump 1"})  # a plain caption
    assert not R.is_configured(dv, text, {"TXTMODE": "0", "TXT_OFF": "Off"})
    assert R.is_configured(dv, text, {"TXTMODE": "0", "TXT_A": "Run", "TXT_OFF": "Off"})
    assert R.is_configured(dv, text, {"TXTMODE": "2", "TXT_A": "Idx"})
    assert R.is_configured(dv, text, {"TXTMODE": "1", "TXT_C": "X > 1"})
    assert R.is_configured(dv, text, {"TXTMODE": "3", "TXT_STR": "Level:#.#"})
    assert not R.is_configured(dv, text, {"TXTMODE": "3", "TXT_A": "X"})
    poly = _ctx(0x10)
    assert R.configured_pages(poly, {"VERTEX_VERTEXP0": ""}) == {"vertices"}
    assert "disable" in R.configured_pages(rect, {"DISMODE": "TRUE"})
    assert "access" in R.configured_pages(_ctx(0x0D), {"ACTIVEXEXT_*NAME": "CiMeter"})
    assert R.disabled_pages(set(), 0x07)["fill_color"].startswith("!")
    assert "fill_color" not in R.disabled_pages(set(), 0x0F)


def test_live_ctdraw_check_marks(tmp_path):
    """Check marks match CtDraw's."""
    sset = _ctx(0x0C)
    so = R.PAGE_BY_ID["set_object"]
    assert R.is_configured(so, sset, {"TXT_A": "BIT_1", "TXTMODE": "0"})
    assert R.is_configured(so, sset, {"TXT_A": "BIT_1"})  # no TXTMODE = 0
    assert R.is_configured(so, sset, {"TXTMODE": "1", "TXT_C": "X"})
    assert R.is_configured(so, sset, {"TXTMODE": "2", "SET_ARR": "Idx"})
    assert not R.is_configured(so, sset, {"TXTMODE": "2", "TXT_A": "X"})
    assert R.is_configured(so, sset, {"TXTMODE": "3", "TXT_A": "X"})
    assert not R.is_configured(so, sset, {"TXTMODE": "4", "TXT_A": "X"})
    assert "set_object" in R.configured_pages(sset, {"TXT_A": "BIT_1"})
    acc = R.PAGE_BY_ID["access"]
    assert R.is_configured(acc, _ctx(0x0F), {"NAME": "FilterBar"})
    assert not R.is_configured(acc, _ctx(0x0F), {"NAME": " "})
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path, FX["genies"]))
    objs = doc.parsed.page.objects
    genie = _named(doc, "FxPump1")
    assert objs[genie].subtype_size & 0x200 and objs[genie].class_flag
    ctx = R.SheetContext.for_object(doc, genie)
    # (FxPump1 is named, which marks Access by itself: its name is left out here)
    props = {k: v for k, v in ctx.props.items() if k != "__AN_NAME__"}
    props["Comment"] = "a genie root's comment"
    assert "access" not in R.configured_pages(ctx, props)
    assert "access" in R.configured_pages(ctx, dict(props, __AN_NAME__="Knob"))


def test_bitmap_fill_colour_and_genie_root_fields_greyed(tmp_path):
    """FxSymbols2's free bitmap FxBmp1 (unlinked) and FxGenies' placed genies."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path, FX["install"].page("FxSymbols2")))
    objs = doc.parsed.page.objects
    bmp = _named(doc, "FxBmp1")
    assert objs[bmp].type_code == 0x07 and not objs[bmp].subtype_size & 0x1
    dlg = _sheet(doc, bmp, read_only=False)
    assert "fill_color" in dlg.page_ids()
    assert not dlg.page_widget("fill_color").isEnabled()
    fill = dlg._tabs.widget(_tabs(dlg).index("Fill"))
    assert "no fill colour" in fill.tabToolTip(0)
    assert dlg.page_widget("fill_level").isEnabled()
    # a placed genie ROOT keeps only its name: the other Access fields are
    # greyed (the Graphics Builder drops them when the page opens)
    doc = EditableDocument.open(_copy(tmp_path, FX["genies"]))
    objs = doc.parsed.page.objects
    genie = next(r for r in doc.page.roots() if objs[r].subtype_size & 0x200 and objs[r].class_flag)
    dlg = _sheet(doc, genie)
    assert dlg._editors["__AN_NAME__"].isEnabled()
    for key in ("DESC", "COMMENT", "AREA", "PRIV", "DEVICE"):
        assert not dlg._editors[key].isEnabled(), key
        assert "genie" in dlg._editors[key].toolTip()


def test_multi_selection_skips_genie_roots_and_keeps_the_user_mark(tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.panels.properties import PropertyPanel

    _app()
    doc = EditableDocument.open(_copy(tmp_path, FX["genies"]))
    objs = doc.parsed.page.objects
    genies = [r for r in doc.page.roots() if objs[r].subtype_size & 0x200 and objs[r].class_flag][:2]
    rects = [
        doc.create_object("rectangle", rect=(5 + 60 * k, 5, 50 + 60 * k, 40), colour=0x336699)
        for k in range(2)
    ]
    assert doc.ensure_property(rects[0], "COMMENT", "Hello<User>")
    assert doc.ensure_property(rects[1], "COMMENT", "Hello")
    panel = PropertyPanel()
    panel.set_target(doc, rects + genies)
    box = next(
        b for b in panel.widget().findChildren(QtWidgets.QGroupBox) if b.title().startswith("Animation")
    )
    edits = {
        box.layout().labelForField(w).text(): w
        for w in box.findChildren(QtWidgets.QLineEdit)
        if box.layout().labelForField(w) is not None
    }
    tip = edits["Tool tip"]  # the genie roots are not targets:
    assert any(
        lab.text() == "2 of 4 objects"  # it says how many it sets
        for lab in box.findChildren(QtWidgets.QLabel)
    )
    assert tip.text() == "Hello" and not tip.placeholderText()
    n = len(doc._undo)
    tip.setText("Bye")
    tip.editingFinished.emit()
    assert len(doc._undo) == n + 1
    assert doc.object_properties(rects[0])["COMMENT"] == "Bye<User>"
    assert doc.object_properties(rects[1])["COMMENT"] == "Bye"
    assert all("COMMENT" not in doc.object_properties(g) for g in genies)
    tip.editingFinished.emit()  # nothing new: no second step
    assert len(doc._undo) == n + 1


def test_ok_stays_open_when_the_input_commands_fail(tmp_path, monkeypatch):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_copy(tmp_path))
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    warned = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning", lambda *a, **k: warned.append(a) or QtWidgets.QMessageBox.Ok
    )

    def boom():
        raise ValueError("This object is scaled or rotated.")

    monkeypatch.setattr(dlg._input, "apply", boom)
    accepted = []
    dlg.accepted.connect(lambda: accepted.append(1))
    dlg._ok()
    # shown in the sheet itself, not in a message box (the UI guide's inline rule)
    assert not warned and not accepted
    assert "scaled or rotated" in dlg._status.text() and not dlg._status.isHidden()
    assert dlg._tabs.currentWidget() is dlg._input


def test_animated_symbol_has_no_other_properties(tmp_path, monkeypatch):
    """A V3/V4 Animated Symbol has no property sheet (page mask 0): its sheet
    offers no 'Other Properties...'; a symbol set's does."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.dialog.symbol_set_dialog import SymbolSetDialog

    _app()
    monkeypatch.setenv("CTG_LIB_PATH", FX["main"])
    doc = EditableDocument.open(_copy(tmp_path, FX["install"].page("FxSymbols")))
    i = doc.create_object("animated_symbol", (100, 100, 120, 118))
    dlg = SymbolSetDialog(doc, i, picker=lambda _p: ("icons", "help1"))
    assert not dlg._btn_other.isVisibleTo(dlg)
    dlg = SymbolSetDialog(doc, _named(doc, "FxOnOffSet"), picker=lambda _p: ("icons", "help1"))
    assert dlg._btn_other.isVisibleTo(dlg)


def test_default_range_follows_equipment_items_and_tag_fields():
    from ctgedit.io.project import TagEntry, TagIndex

    idx = TagIndex(
        [
            TagEntry("Pump_1", "variable", "P", "REAL", "", "0", "250"),
            TagEntry("Plant.Pump1.Speed", "equipment", "P", "REAL", "", "-5", "5"),
        ]
    )
    assert default_range("Pump_1.Value", idx.eng_range)[:2] == ("0", "250")
    assert default_range("Plant.Pump1.Speed * 2", idx.eng_range)[:2] == ("-5", "5")
