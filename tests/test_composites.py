"""Reading composite genies."""

import os
import shutil

import pytest

import fxinstall
from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

#: the drive template (fx_drive_c.xml: Equipment 1 to 160 characters, MEO 0 to 5, a Label
#: shown when DisplayLabel is Above) and its instance FxComp3 on FxComposites (MEO 1,
#: DisplayLabel Above, Label Spray, DriveType Sprayer, DisplayStatus Inside Right,
#: Orientation Right): its options file is Instances/<DRIVE_HASH>.xml of the drive's cache
DRIVE_GUID = "8d3f5e4c-af60-4b7c-9d8e-9fa0b1c2d3e4"
DRIVE_HASH = "058f947be5bae7e88fa9df69c1716cc"
DRIVE_REF = "_CompositionCaches\\{%s}\\cache.%s.<ctm" % (DRIVE_GUID, DRIVE_HASH)
#: the switch template's GUID, stored in upper case in fx_switch_c.xml
SWITCH_GUID = "9E4A6F5D-B071-4C8D-AE9F-A0B1C2D3E4F5"
#: the composites FxComposites places (FxComp1 .. FxComp8)
COMPOSITES_ON_PAGE = 9


@pytest.fixture()
def tpl_file(fx_install):
    """``tpl_file(name)``: a template of FxLib's 'Composite Genies' in the fx_install copy."""
    return lambda name: fx_install.file("FxLib", os.path.join("Composite Genies", name))


@pytest.fixture()
def tpl_dirs(fx_install):
    """The fake install's template folders (FxLib's 'Composite Genies')."""
    return [fx_install.file("FxLib", "Composite Genies")]


@pytest.fixture()
def comp_page(fx_page):
    """FxComposites in the fx_install copy: eight composite instances and their caches."""
    return fx_page("FxComposites")


def _named(doc, name):
    """The index of the one object named ``name`` (the catalogue's names)."""
    (i,) = doc.find_objects_by_name(name)
    return i


def _template(xml: str):
    from ctgedit.format import composites as C

    return C.parse_template(xml.encode("utf-8"), "test.xml")


# -- template schemas ---------------------------------------------------------------
def test_template_schemas_load(tpl_dirs):
    """The six templates of FxLib's 'Composite Genies' (tools/make_fixtures.py writes
    them: COMPOSITE_GUIDS and FX_TANK_GUID)."""
    import glob
    from ctgedit.format import composites

    out = {}
    for d in tpl_dirs:
        for f in glob.glob(os.path.join(d, "*.xml")):
            tpl = composites.load_template(f)
            if tpl:
                out[tpl.guid] = tpl
    assert len(out) == 6
    valve = next((t for t in out.values() if t.name == "FxValveC"), None)
    assert valve is not None
    names = {p.name: p for p in valve.params}
    assert "Equipment" in names
    assert names["ValveType"].values == ("Block", "Control", "Damper", "Hand")
    assert names["DisplayAlarm"].type == "Bool"
    assert names["ValveType"].default == "Block"
    assert names["DisplayAlarm"].default_value is False
    drive = out[DRIVE_GUID]
    m = {p.name: p for p in drive.params}
    # the limits, naming rule, Int type and dependencies are read
    assert (m["Equipment"].min_length, m["Equipment"].max_length) == (1, 160)
    assert m["Equipment"].naming_rule == "Equipment"
    assert m["MEO"].type == "Int" and m["MEO"].standard_values == (0, 1, 2, 3, 4, 5)
    assert m["Label"].dependency is not None and m["Equipment"].dependency is None
    assert drive.template_id == "{%s}" % DRIVE_GUID


def test_shipped_templates_pass_the_schema_and_ctdraws_checks(tpl_dirs, fx_project):
    """Shipped templates pass the schema and CtDraw's checks."""
    import glob
    from ctgedit.format import composites as C

    files = [f for d in tpl_dirs for f in glob.glob(os.path.join(d, "*.xml"))]
    files += glob.glob(os.path.join(fx_project("FxMain"), "_CompositionCaches", "*", "*.xml"))
    files = [f for f in files if not f.lower().endswith("metadata.xml")]
    assert len(files) == 12
    for f in files:
        tpl = C.load_template(f)
        assert tpl is not None, f
        assert tpl.params, f
        assert C.check_template(f) == [], (f, [str(p) for p in C.check_template(f)][:3])
    assert C.schema_path() is not None  # (the schema part ran too)


def test_schema_problems_are_listed_with_line_and_position(tmp_path, tpl_file):
    from ctgedit.format import composites as C

    src = open(tpl_file("fx_switch_c.xml"), encoding="utf-8-sig").read()
    src = src.replace("\r\n", "\n")
    bad = (
        src.replace('Name="Size" Label="Size"', 'Name="Size"')  # required Label
        .replace(
            'Value="Small" Comparison="IsEqualTo"', 'Value="Small" Comparison="IsBigger"'
        )  # not an operator
        .replace('Guid="{%s}"' % SWITCH_GUID, 'Guid="{%s}"' % SWITCH_GUID.lower())  # lower case
        .replace(
            '<Content ItemId="1" ZIndex="1" VisibleWhen="IsSmall"/>',
            '<Content ItemId="1" ZIndex="1" VisibleWhen="IsSmall" '
            'AnimationName="SelectionAdorner"/>\n        '
            '<Content ItemId="2" ZIndex="2" '
            'AnimationName="selectionadorner"/>',
        )  # case-insensitive dup
        .replace("</Parameters>\n  <ContentItems>", "<Foo/>\n  </Parameters>\n  <ContentItems>")
    )  # unknown element
    assert bad.count("selectionadorner") == 1 and "<Foo/>" in bad and "IsBigger" in bad
    p = tmp_path / "fx_switch_bad.xml"
    p.write_text(bad, encoding="utf-8")
    probs = C.check_template(str(p))
    text = [str(x) for x in probs]
    lines = bad.splitlines()

    def line_of(needle):
        return next(i + 1 for i, s in enumerate(lines) if needle in s)

    assert any(
        x.message == "The required attribute 'Label' is missing."
        and x.line == line_of('<Parameter Name="Size"')
        for x in probs
    ), text
    assert any(
        "'Comparison' attribute is invalid" in x.message
        and "Enumeration" in x.message
        and x.line == line_of("IsBigger")
        for x in probs
    ), text
    assert any("'Guid' attribute is invalid" in x.message and "Pattern" in x.message for x in probs), text
    assert any(
        "duplicate key sequence 'SELECTIONADORNER'" in x.message
        and x.line == line_of('AnimationName="selectionadorner"')
        for x in probs
    ), text
    assert any("invalid child element 'Foo'" in x.message and x.line == line_of("<Foo/>") for x in probs), (
        text
    )
    assert all(x.line > 0 and x.column > 0 for x in probs)
    # not well-formed XML: one problem with its position
    p2 = tmp_path / "broken.xml"
    p2.write_text(src.replace("</Parameters>", "</Parameter>"), encoding="utf-8")
    (only,) = C.check_template(str(p2))
    assert "not valid XML" in only.message and only.line > 0


def test_ctdraws_semantic_checks():
    """Define before use, duplicate names (case-insensitive), typed values
    and defaults, comparison / type mismatches, references into the content
    items and global conditions -- CtDraw's own template checks."""
    tpl = _template("""<?xml version="1.0"?>
<VisualTemplate Guid="{11111111-2222-3333-4444-555555555555}" Name="T" Thumbnail="x">
  <GlobalConditions>
    <GlobalCondition Id="UsesLater"><GlobalConditionRef Id="Later"/></GlobalCondition>
    <GlobalCondition Id="Later"><ParameterCondition Name="Mode" Value="1"/></GlobalCondition>
    <GlobalCondition Id="later"><ParameterCondition Name="Mode" Value="2"/></GlobalCondition>
    <GlobalCondition Id="BadCmp">
      <ParameterCondition Name="Text" Comparison="IsGreaterThan" Value="a"/></GlobalCondition>
    <GlobalCondition Id="BadVal"><ParameterCondition Name="Mode" Value="x"/></GlobalCondition>
    <GlobalCondition Id="NoParam"><ParameterCondition Name="Nope" Value="1"/></GlobalCondition>
  </GlobalConditions>
  <Parameters>
    <Parameter Name="Mode" Label="Mode" Type="Int" DefaultValue="abc"/>
    <Parameter Name="mode" Label="Mode again" Type="String"/>
    <Parameter Name="Text" Label="Text" Type="String">
      <Values><Value Id="1" Label="a"/></Values>
      <Dependency><GlobalConditionRef Id="Missing"/></Dependency>
    </Parameter>
  </Parameters>
  <ContentItems>
    <Content Id="1" Type="Genie" Project="P" Library="l" Item="i"/>
    <Content Id="1" Type="Genie" Project="P" Library="l" Item="j"/>
  </ContentItems>
  <Compositions>
    <Composition Id="{11111111-2222-3333-4444-555555555556}">
      <Layout>
        <Content ItemId="7" VisibleWhen="Nowhere" AnimationName="9bad">
          <Parameters><Parameter Name="Equipment" TemplateParameter="Ghost"/></Parameters>
        </Content>
      </Layout>
    </Composition>
  </Compositions>
</VisualTemplate>""")
    msgs = [p.message for p in tpl.problems]
    need = [
        "The referenced global condition 'Later' does not exist.",
        "Duplicate global condition Id - 'later'.",
        "Type mismatch between the type of the parameter 'Text' and the comparison operator 'IsGreaterThan'.",
        "Invalid value type, expecting type 'Int'.",
        "The referenced parameter 'Nope' does not exist.",
        "The default value is of the wrong type for the parameter 'Mode'.",
        "Duplicate parameter name - 'mode'.",
        "The referenced global condition 'Missing' does not exist.",
        "Duplicate content item Id - '1'.",
        "The referenced content item Id '7' does not exist.",
        "The referenced global condition 'Nowhere' does not exist.",
        "The referenced template parameter 'Ghost' does not exist.",
    ]
    for n in need:
        assert n in msgs, (n, msgs)
    assert any(m.startswith("The animation name '9BAD' is invalid") for m in msgs)
    assert all(p.line > 0 for p in tpl.problems)
    # a bad parameter is still read (the form can show what is there)
    assert [p.name for p in tpl.params] == ["Mode", "Text"]


def test_condition_evaluator_follows_ctdraw():
    from ctgedit.format import composites as C

    tpl = _template("""<?xml version="1.0"?>
<VisualTemplate Guid="{11111111-2222-3333-4444-555555555555}" Name="T" Thumbnail="x">
  <GlobalConditions>
    <GlobalCondition Id="Big"><ParameterCondition Name="N" Comparison="IsGreaterThanOrEqualTo"
        Value="2"/></GlobalCondition>
    <GlobalCondition Id="NotBig"><GlobalConditionRef Id="Big" Negation="true"/></GlobalCondition>
    <GlobalCondition Id="OnAndBig">
      <CompositeCondition Operator="And">
        <ParameterCondition Name="On" Value="true"/>
        <GlobalConditionRef Id="Big"/>
      </CompositeCondition></GlobalCondition>
    <GlobalCondition Id="Named">
      <CompositeCondition Operator="Or" Negation="true">
        <ParameterCondition Name="S" Comparison="StartsWith" Value="Ab"/>
        <ParameterCondition Name="S" Comparison="EndsWith" Value="yz"/>
        <ParameterCondition Name="S" Comparison="Contains" Value="mid"/>
      </CompositeCondition></GlobalCondition>
  </GlobalConditions>
  <Parameters>
    <Parameter Name="N" Label="N" Type="Int" DefaultValue="0"/>
    <Parameter Name="On" Label="On" Type="Bool" DefaultValue="false"/>
    <Parameter Name="S" Label="S" Type="String"/>
    <Parameter Name="OnlyBig" Label="Only big" Type="String">
      <Dependency><GlobalConditionRef Id="Big"/></Dependency></Parameter>
    <Parameter Name="Both" Label="Both" Type="String">
      <Dependency><GlobalConditionRef Id="OnAndBig"/></Dependency></Parameter>
    <Parameter Name="Unnamed" Label="Unnamed" Type="String">
      <Dependency><GlobalConditionRef Id="Named"/></Dependency></Parameter>
    <Parameter Name="Small" Label="Small" Type="String">
      <Dependency><GlobalConditionRef Id="NotBig"/></Dependency></Parameter>
    <Parameter Name="Exact" Label="Exact" Type="String">
      <Dependency><ParameterCondition Name="S" Comparison="IsNotEqualTo" Value="abc"/>
      </Dependency></Parameter>
  </Parameters>
  <ContentItems><Content Id="1" Type="Genie" Project="P" Library="l" Item="i"/></ContentItems>
  <Compositions><Composition Id="{11111111-2222-3333-4444-555555555556}">
    <Layout><Content ItemId="1"/></Layout></Composition></Compositions>
</VisualTemplate>""")
    assert not tpl.problems, [str(p) for p in tpl.problems]

    def shown(**vals):
        v = C.typed_values(tpl, vals)
        return {p.name for p in tpl.params if C.shown(p, v)}

    base = {"N", "On", "S"}
    # N = 0: Small; S '' matches none of Ab* / *yz / mid -> Named (negated Or) holds
    assert shown() == base | {"Small", "Unnamed", "Exact"}
    # instance texts are adapted to the types ('True' -> a Bool, '3' -> an Int)
    assert shown(N="3", On="True", S="Abc") == base | {"OnlyBig", "Both", "Exact"}
    assert shown(N=2, On=False, S="xyz") == base | {"OnlyBig", "Exact"}
    assert "Unnamed" not in shown(S="a mid b")
    # string comparisons are case-sensitive, equality type-strict
    assert "Exact" in shown(S="ABC") and "Exact" not in shown(S="abc")
    assert "Unnamed" in shown(S="abc")  # 'abc' does not start with 'Ab'


def test_value_checks_follow_ctdraw():
    from ctgedit.format import composites as C

    P = C.TemplateParam
    eq = P("Equipment", type="String", min_length=1, max_length=160, naming_rule="Equipment")
    assert C.validate_value(eq, "FxPlant.FxArea.FX_SPRAY02") == ""
    assert C.validate_value(eq, "?__EquipmentRef?") == ""  # an association
    assert C.validate_value(eq, "") == C.MSG_LENGTH.format(1, 160)
    assert C.validate_value(eq, "Bad Name!") == C.MSG_EQUIP_CHARS
    assert C.validate_value(eq, ".".join(["a1"] * 15)) == C.MSG_EQUIP_LEVELS
    assert C.validate_value(eq, "Plant." + "x" * 64) == C.MSG_EQUIP_LONG
    assert C.validate_value(eq, "9Pump") == C.MSG_EQUIP_CHARS  # no digit first
    tag = P("Tag", type="String", naming_rule="Tag")
    assert C.validate_value(tag, "Cluster1.PUMP_101") == ""
    assert C.validate_value(tag, "9TAG") == ""  # a tag may start with a digit
    assert C.validate_value(tag, "a.b.c") == C.MSG_TAG_CLUSTER
    num = P("N", type="Int", min_value=0, max_value=10)
    assert C.validate_value(num, 10) == "" and C.validate_value(num, 11) == C.MSG_RANGE.format(0, 10)
    std = P("Size", type="String", values=("Small", "Large"))
    assert C.validate_value(std, "Large") == ""
    assert C.validate_value(std, "large") == C.MSG_STANDARD.format("large")
    # AdaptValue: text parsed like .NET TryParse, else the default
    b = P("B", type="Bool", default="true", has_default=True)
    assert C.adapt_value(b, " TRUE ") is True and C.adapt_value(b, "yes") is True
    assert C.adapt_value(P("I", type="Int"), "-12") == -12
    assert C.adapt_value(P("I", type="Int"), "1.5") == 0
    assert C.to_text(True) == "True"


# -- placed instances: the CURRENT options --------------------------------------------
def test_drive_shows_its_current_options(comp_page):
    """FxComposites' FxComp3 (an FxDriveC) opens with the options of its
    Instances\\<hash>.xml -- Sprayer, Inside Right, turned Right, the label Above --
    not the template defaults (Pump, None, Left, Below)."""
    import xml.etree.ElementTree as ET
    from PySide6 import QtWidgets
    from ctgedit.format import composites as C
    from ctgedit.ui.dialog.composite_dialogs import PresentationOptionsDialog

    _app()
    info = C.composite_info(comp_page, DRIVE_REF)
    assert info.template.name == "FxDriveC" and info.template_source == "project"
    assert info.instance is not None and not info.notes, info.notes
    stored = {
        p.get("Name"): p.get("Value") for p in ET.parse(info.instance.path).getroot().find("Parameters")
    }
    vals = info.values()
    for name, text in stored.items():
        assert C.to_text(vals[name.lower()]) == text, name
    assert stored["Orientation"] == "Right" and stored["DisplayLabel"] == "Above"
    assert stored["DriveType"] == "Sprayer" and stored["DisplayStatus"] == "Inside Right"
    dlg = PresentationOptionsDialog(
        info.template, vals, read_only=True, note="locked", instance=info.hash, cache_dir=info.cache_dir
    )
    assert dlg.windowTitle() == "Presentation Options - FxDriveC"
    f = dlg.panel.form
    shown_vals = f.values()
    assert shown_vals["Orientation"] == "Right" and shown_vals["DisplayLabel"] == "Above"
    assert shown_vals["DriveType"] == "Sprayer" and shown_vals["DisplayStatus"] == "Inside Right"
    assert f.widget("DriveType").currentText() == "Sprayer"  # not 'Pump'
    assert f.widget("Orientation").currentText() == "Right"  # default Left
    assert shown_vals["MEO"] == "1"  # default 0
    assert dlg.panel._tpl_id.text() == "{%s}" % DRIVE_GUID
    assert dlg.panel._inst.text() == DRIVE_HASH
    # DisplayLabel = Above: its dependant (Label) follows the instance's value
    assert f.row_shown("Label") and shown_vals["Label"] == "Spray"
    box = dlg.findChild(QtWidgets.QDialogButtonBox)
    assert box.button(QtWidgets.QDialogButtonBox.Close) is not None
    assert box.button(QtWidgets.QDialogButtonBox.Ok) is None


def test_every_page_composite_reads_its_instance(comp_page):
    """Each of FxComposites' eight composites (FxComp1 .. FxComp8) reads its options file
    with no problem and no invalid value."""
    from ctgedit.format import composites as C
    from ctgedit.format.page import parse_ctg
    from ctgedit.format.symbols import reference_from_object

    templates = C.load_templates(comp_page)
    n = 0
    for po in parse_ctg(comp_page).page.objects:
        if po.type_code != 0x08 or not po.class_flag:
            continue
        ref = reference_from_object(po)
        if ref is None or C.instance_ref(ref.raw) is None:
            continue
        info = C.composite_info(comp_page, ref.raw, templates)
        assert info.instance is not None and not info.instance.problems
        assert C.invalid_parameters(info.template, info.values()) == {}
        n += 1
    assert n == COMPOSITES_ON_PAGE


def test_meter_dependencies_hide_the_dependent_rows(tpl_file):
    from ctgedit.format import composites as C
    from ctgedit.ui.dialog.composite_dialogs import TypedParamsForm

    _app()
    meter = C.load_template(tpl_file("fx_meter_c.xml"))
    form = TypedParamsForm(meter.params, {})
    # the defaults: no output, no trend
    for hidden in ("DisplayReadback", "TrendType", "Span"):
        assert not form.row_shown(hidden), hidden
    assert form.row_shown("DisplayOutput") and form.row_shown("DisplayTrend")
    form.widget("DisplayOutput").setChecked(True)
    assert form.row_shown("DisplayReadback")
    form.widget("DisplayReadback").setChecked(True)
    form.widget("DisplayTrend").setChecked(True)
    assert form.row_shown("TrendType") and form.row_shown("Span")
    form.widget("DisplayOutput").setChecked(False)
    assert not form.row_shown("DisplayReadback")
    # a hidden row takes its default on OK (GetFinalParameterValues): fx_meter_c's
    # DisplayReadback defaults to false, so the ticked value is not kept
    form.widget("DisplayReadback")  # still there, only hidden
    assert form.values()["DisplayReadback"] == "False"


def test_meter_second_equipment_rows_hide_with_a_single_meter(tpl_file):
    """The original Meter template also hid Equipment2 and ReferenceEquipment for a single
    meter (the defaults); fx_meter_c.xml gives both a dependency (DisplaySecond /
    DisplayOutput)."""
    from ctgedit.format import composites as C

    meter = C.load_template(tpl_file("fx_meter_c.xml"))
    deps = {p.name: p.dependency for p in meter.params}
    assert deps.get("Equipment2") is not None, deps
    assert deps.get("ReferenceEquipment") is not None, deps
    from ctgedit.ui.dialog.composite_dialogs import TypedParamsForm

    _app()
    form = TypedParamsForm(meter.params, {})
    for hidden in ("Equipment2", "ReferenceEquipment"):
        assert not form.row_shown(hidden), hidden


def test_invalid_equipment_name_is_flagged_and_blocks_ok(tpl_file):
    from PySide6 import QtWidgets
    from ctgedit.format import composites as C
    from ctgedit.ui.dialog.composite_dialogs import PresentationOptionsDialog

    _app()
    drive = C.load_template(tpl_file("fx_drive_c.xml"))
    dlg = PresentationOptionsDialog(drive, {"Equipment": "Bad Name!"})  # editable form
    f = dlg.panel.form
    ok = dlg.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok)
    assert f.error_text("Equipment") == C.MSG_EQUIP_CHARS
    assert f.problems() == {"Equipment": C.MSG_EQUIP_CHARS}
    assert not ok.isEnabled()
    # 'Show only parameters with invalid input' lists it alone
    dlg.panel.only_invalid.setChecked(True)
    shown = [p.name for p in f.params if f.row_shown(p.name)]
    assert shown == ["Equipment"]
    f.widget("Equipment").setText("Plant.Area1.Pump01")
    assert f.is_valid() and ok.isEnabled() and f.error_text("Equipment") == ""
    # a corrected row stays in the list until the option is switched off
    assert f.row_shown("Equipment")
    dlg.panel.only_invalid.setChecked(False)
    assert f.row_shown("DriveType")
    # a Label hidden by its dependency does not block OK even when empty
    f.widget("DisplayLabel").setCurrentText("Below")
    f.widget("Label").setText("")
    assert not f.row_shown("Label") and ok.isEnabled()
    dlg.panel.search.setText("orient")
    assert [p.name for p in f.params if f.row_shown(p.name)] == ["Orientation"]
    assert ok.isEnabled()  # CtDraw asks to clear Search first; ctgedit not


def test_typed_widgets_search_and_description():
    from PySide6 import QtWidgets
    from ctgedit.ui.dialog.composite_dialogs import CompositeOptionsPanel

    _app()
    tpl = _template("""<?xml version="1.0"?>
<VisualTemplate Guid="{11111111-2222-3333-4444-555555555555}" Name="T" Thumbnail="x">
  <Parameters>
    <Parameter Name="Count" Label="Number of pumps" Type="Int" MinValue="1" MaxValue="4"
        DefaultValue="2" Description="How many pumps are drawn."/>
    <Parameter Name="Equip" Label="Equipment Name" Type="String" NamingRules="Equipment"
        MaxLength="20" Description="The equipment."/>
    <Parameter Name="Show" Label="Show label" Type="Bool"/>
  </Parameters>
  <ContentItems><Content Id="1" Type="Genie" Project="P" Library="l" Item="i"/></ContentItems>
  <Compositions><Composition Id="{11111111-2222-3333-4444-555555555556}">
    <Layout><Content ItemId="1"/></Layout></Composition></Compositions>
</VisualTemplate>""")
    panel = CompositeOptionsPanel(completions={"Equipment": ["Plant.PumpA", "Plant.PumpB"]})
    panel.set_options(tpl, {"Count": "9", "Equip": "Plant.PumpA"}, read_only=False)
    f = panel.form
    spin = f.widget("Count")
    assert isinstance(spin, QtWidgets.QSpinBox) and spin.value() == 9  # not clamped to 4
    assert f.error_text("Count") == "The value must be between 1 and 4."
    spin.setValue(3)
    assert f.error_text("Count") == "" and f.typed_values()["Count"] == 3
    eq = f.widget("Equip")
    assert eq.completer() is not None and eq.maxLength() == 20
    assert isinstance(f.widget("Show"), QtWidgets.QCheckBox)
    # the description follows the current parameter
    f.set_current("Count")
    assert "How many pumps are drawn." in panel.description.text()
    assert "1 to 4" in panel.description.text()
    panel.search.setText("EQUIPMENT")
    assert [p.name for p in f.params if f.row_shown(p.name)] == ["Equip"]
    assert f.changed_values() == {"Count": "3", "Equip": "Plant.PumpA"}
    # read-only: typed, selectable, not editable
    panel.set_options(tpl, {"Count": "3"}, read_only=True)
    f = panel.form
    assert f.widget("Count").isReadOnly() and not f.widget("Show").isEnabled()
    assert f.widget("Equip").isReadOnly() and f.widget("Equip").isEnabled()


def test_cached_option_migrations_are_validated_before_use(tmp_path):
    from ctgedit.format import composites as C

    template = C.parse_template(b"""<VisualTemplate Guid="{11111111-2222-3333-4444-555555555555}"
        Name="Pump" Thumbnail="Pump"><Parameters>
        <Parameter Name="Label" Label="Label" Type="String"/>
        </Parameters></VisualTemplate>""")
    path = tmp_path / "instance.xml"

    def instance(migrations):
        path.write_text(
            "<CompositeInstance><Parameters>"
            '<Parameter Name="Label" Type="String" Value="Before"/></Parameters>'
            "<ParameterMigrations>" + migrations + "</ParameterMigrations></CompositeInstance>",
            encoding="utf-8",
        )
        return C.load_instance(str(path))

    record = '<Parameter Name="Label" Type="String" Value="After"/>'
    saved = instance(record)
    assert saved.values() == {"Label": "Before"}
    assert saved.update_values(template) == {"Label": "After"}
    broken = instance(record + '<Parameter Name="n" Type="Int" Value="invalid"/>')
    assert broken.problems
    assert broken.update_values(template) == {"Label": "Before"}


def test_instance_problems_and_cached_template_fallback(tmp_path, monkeypatch):
    """No template in the project: the copy the cache keeps is used (and
    said); an unreadable parameter in the options file is reported with
    CtDraw's message."""
    from ctgedit.format import composites as C

    monkeypatch.delenv("CTG_LIB_PATH", raising=False)
    guid = "11111111-2222-3333-4444-555555555555"
    proj = tmp_path / "User" / "Proj"
    cache = proj / "_CompositionCaches" / ("{%s}" % guid)
    (cache / "Instances").mkdir(parents=True)
    (cache / (guid.replace("-", "") + ".xml")).write_text(
        """<?xml version="1.0"?>
<VisualTemplate Guid="{11111111-2222-3333-4444-555555555555}" Name="Cached" Thumbnail="x">
  <Parameters><Parameter Name="On" Label="On" Type="Bool"/>
    <Parameter Name="N" Label="N" Type="Int"/></Parameters>
  <ContentItems><Content Id="1" Type="Genie" Project="P" Library="l" Item="i"/></ContentItems>
  <Compositions><Composition Id="{11111111-2222-3333-4444-555555555556}">
    <Layout><Content ItemId="1"/></Layout></Composition></Compositions>
</VisualTemplate>""",
        encoding="utf-8",
    )
    (cache / "Instances" / "abc123.xml").write_text(
        """<?xml version="1.0"?>
<CompositeInstance TemplateId="%s"><Parameters>
  <Parameter Name="On" Type="Bool" Value="True"/>
  <Parameter Name="N" Type="Int" Value="lots"/>
</Parameters></CompositeInstance>"""
        % guid,
        encoding="utf-8",
    )
    page = str(proj / "Page.ctg")
    ref = "_CompositionCaches\\{%s}\\cache.abc123.<ctm" % guid
    info = C.composite_info(page, ref)
    assert info.template.name == "Cached" and info.template_source == "cache"
    assert info.values() == {"on": True, "n": 0}
    assert any("copy of the template kept in the composition cache" in n for n in info.notes)
    assert any("instance hash 'abc123'" in n for n in info.notes)
    # no options file: said, and the caller falls back to the stored values
    info2 = C.composite_info(page, ref.replace("abc123", "fff999"))
    assert info2.instance is None
    assert any("Instances\\fff999.xml" in n for n in info2.notes)


# -- the template list --------------------------------------------------------------
def test_template_list_is_the_include_closure(tmp_path, monkeypatch):
    from ctgedit.format import composites as C

    monkeypatch.delenv("CTG_LIB_PATH", raising=False)
    user = tmp_path / "User"
    for proj in ("ProjA", "ProjB"):
        (user / proj / "Composite Genies").mkdir(parents=True)
    dirs = C._template_dirs(str(user / "ProjA" / "Page.ctg"))
    assert [os.path.normcase(d) for d in dirs] == [os.path.normcase(str(user / "ProjA" / "Composite Genies"))]


def test_page_templates_come_from_its_include_chain(comp_page):
    """FxMain has no 'Composite Genies' folder of its own: FxComposites' templates come
    from FxLib, which FxMain includes."""
    from ctgedit.format import composites as C

    dirs = C._template_dirs(comp_page)
    assert dirs and all(os.path.basename(os.path.dirname(d)).lower() == "fxlib" for d in dirs), dirs


def test_duplicate_template_guid_is_warned(tmp_path, monkeypatch, tpl_file):
    from ctgedit.format import composites as C
    from ctgedit.ui.dialog.composite_dialogs import PasteCompositeGenieDialog

    monkeypatch.delenv("CTG_LIB_PATH", raising=False)
    _app()
    folder = tmp_path / "User" / "Proj" / "Composite Genies"
    folder.mkdir(parents=True)
    src = tpl_file("fx_switch_c.xml")
    shutil.copy(src, folder / "fx_switch_c.xml")
    shutil.copy(src, folder / "fx_switch_c copy.xml")
    templates = C.load_templates(str(folder.parent / "Page.ctg"))
    (tpl,) = templates.values()
    assert len(tpl.duplicates) == 1 and tpl.duplicates[0].endswith("fx_switch_c.xml")
    warning = C.duplicate_warning(tpl)
    assert "{%s}" % SWITCH_GUID.lower() in warning and "copy" in warning
    dlg = PasteCompositeGenieDialog(templates, {tpl.guid: ("x.ctg", 0)}, donor_info=lambda g: None)
    dlg._list.setCurrentRow(0)
    assert "same template ID" in dlg._list.item(0).toolTip()
    assert "has the template ID" in dlg.panel._notes.text()


def test_same_project_seen_through_two_installs_is_no_duplicate(tmp_path, monkeypatch, tpl_file):
    """A project name contributes the folder of every install that has it:
    its same file seen twice is ONE template; a copied
    file with the same Guid is still a duplicate."""
    from ctgedit.format import composites as C

    src = tpl_file("fx_switch_c.xml")
    a = tmp_path / "A" / "User" / "Proj" / "Composite Genies"
    b = tmp_path / "B" / "User" / "Proj" / "Composite Genies"
    for d in (a, b):
        d.mkdir(parents=True)
        shutil.copy(src, d / "fx_switch_c.xml")
    monkeypatch.setenv("CTG_LIB_PATH", str(b.parent))
    page = str(a.parent / "Page.ctg")
    (tpl,) = C.load_templates(page).values()
    assert tpl.duplicates == () and C.duplicate_warning(tpl) == ""
    shutil.copy(src, b / "fx_switch_c copy.xml")
    (tpl,) = C.load_templates(page).values()
    assert len(tpl.duplicates) == 1 and tpl.duplicates[0].endswith("fx_switch_c copy.xml")


def test_content_item_ids_are_integers():
    """ContentItems are keyed by their integer Id (CtDraw's Int32
    dictionary): ItemId '1' finds Id '01', and '1' / '01' are duplicates."""
    xml = """<?xml version="1.0"?>
<VisualTemplate Guid="{11111111-2222-3333-4444-555555555555}" Name="T" Thumbnail="x">
  <Parameters><Parameter Name="A" Label="A" Type="String"/></Parameters>
  <ContentItems><Content Id="01" Type="Genie" Project="P" Library="l" Item="i"/>%s
  </ContentItems>
  <Compositions><Composition Id="{11111111-2222-3333-4444-555555555556}">
    <Layout><Content ItemId="1"/></Layout></Composition></Compositions>
</VisualTemplate>"""
    msgs = [p.message for p in _template(xml % "").problems]
    assert not any("content item" in m for m in msgs), msgs
    msgs = [
        p.message
        for p in _template(xml % '<Content Id="1" Type="Genie" Project="P" Library="l" Item="j"/>').problems
    ]
    assert "Duplicate content item Id - '1'." in msgs


# -- document rules ------------------------------------------------------------------
def test_paste_composite_genie_round_trip(comp_page):
    """Paste Composite Genie composes the options into the page's project
    cache (tests/test_composer.py); the donor copy (paste_composite_copy)
    still keeps the donor's options. FxComposites in the test's fx_install copy: the
    paste writes the new option set into FxMain's composition cache of the copy."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import composites

    page = comp_page
    proj = os.path.dirname(page)
    doc = EditableDocument.open(str(page))
    donors = composites.donor_index(str(page))
    templates = composites.load_templates(str(page))
    guid = next(iter(sorted(set(donors) & set(templates))))
    instances = os.path.join(proj, "_CompositionCaches", "{%s}" % guid, "Instances")
    before = set(os.listdir(instances))
    n0 = len(doc.parsed.page.objects)
    # a donor copy keeps the donor's options (CtDraw's load re-composes it
    # from the donor's composition cache), so other values are refused
    with pytest.raises(ValueError):
        doc.paste_composite_copy(guid, (200, 600), {"Equipment": "FxPlant.Test.NEW01"})
    assert len(doc.parsed.page.objects) == n0 and not doc._undo
    # (the donor's options are its content genies' bindings: fx_tank's Description)
    opts = composites.donor_options(donors[guid])
    assert opts
    key, value = sorted(opts.items())[0]
    idx = doc.paste_composite_copy(guid, (200, 600), {key: value})
    assert idx == n0
    doc.undo()
    idx = doc.paste_composite_genie(guid, (200, 600), {"Equipment": "FxPlant.Test.NEW01"})
    assert idx == n0
    root = doc.parsed.page.objects[idx]
    assert root.type_code == 0x08 and root.class_flag >= 1
    assert len(doc.parsed.page.objects) > n0
    assert doc.composite_info(idx).values()["equipment"] == "FxPlant.Test.NEW01"
    # the new option set has an options file of its own in the project's cache
    assert len(set(os.listdir(instances)) - before) == 1, sorted(os.listdir(instances))
    # placement: root rect lands at the requested position
    from ctgedit.format import geometry as G

    gh = G.parse_geom_header(root.body_raw)
    assert (gh.rect1[0], gh.rect1[1]) == (200, 600)
    # the page's link table lists the composition-cache element, like every
    # placed genie
    from ctgedit.format import genie_place as GP, links

    t = links.parse_link_table(doc.parsed.page.link_table_raw)
    listed = {links.strip_stamp(n).lower() for _g, n in t[1]}
    for po in doc.parsed.page.objects[idx:]:
        if po.subtype_size & 0x1:
            assert links.strip_stamp(GP.decode(po).link).lower() in listed
    # undo removes the whole subtree; save round-trips
    out = os.path.join(proj, "FxPasteOut.ctg")
    doc.save(out)
    doc2 = EditableDocument.open(out)
    assert len(doc2.parsed.page.objects) == len(doc.parsed.page.objects)
    doc.undo()
    assert len(doc.parsed.page.objects) == n0


def test_composites_turn_and_undo(comp_page):
    """Help 1172148_1 says a placed composite cannot be rotated or mirrored,
    but CtDraw 8.50 offers both and keeps the result through its own next
    load + save. A turn of FxComp1 changes the page, its undo gives the bytes back, and
    a mirror turns it too (CtDraw's geometry: the golden test below)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(comp_page)
    comp = _named(doc, "FxComp1")
    assert doc.page.objects[comp].kind.value == "composite_genie"
    before = doc.serialize()
    doc.rotate_objects([comp], clockwise=True)
    assert doc.serialize() != before
    doc.undo()
    assert doc.serialize() == before
    doc.mirror_objects([comp], horizontal=False)  # mirror turns too
    assert doc.serialize() != before


def _subtree_geometry(objects, root):
    """{(handle, type code, n-th of that pair): (rect1, anchor)} of the records of the
    subtree at ``root``."""
    from ctgedit.format import forest
    from ctgedit.format import geometry as G

    out, seen = {}, {}
    for po in objects[root : forest.subtree_end(objects, root)]:
        key = (G.get_handle(po), po.type_code)
        n = seen[key] = seen.get(key, -1) + 1
        gh = G.parse_geom_header(po.body_raw)
        out[key + (n,)] = (gh.rect1, gh.anchor)
    return out


@pytest.mark.golden
@pytest.mark.parametrize("unit, turn", [("T21_composite_turn", "rotate"), ("T22_composite_mirror", "mirror")])
def test_composites_turn_like_ctdraw(comp_page, unit, turn):
    """Composite genies turn and mirror like CtDraw."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg_bytes

    want_page = parse_ctg_bytes(fxinstall.oracle_bytes("turns", unit, "page.ctg")).page
    doc = EditableDocument.open(comp_page)
    comp = _named(doc, "FxComp1")
    handle = doc.parsed.page.objects[comp]
    if turn == "rotate":
        doc.rotate_objects([comp], clockwise=True)
    else:
        doc.mirror_objects([comp], horizontal=True)
    from ctgedit.format import geometry as G

    h = G.get_handle(handle)
    theirs_at = next(
        i
        for i, po in enumerate(want_page.objects)
        if G.get_handle(po) == h and po.type_code == handle.type_code
    )
    ours = _subtree_geometry(doc.parsed.page.objects, comp)
    theirs = _subtree_geometry(want_page.objects, theirs_at)
    assert ours == theirs


def test_composites_only_go_into_pages_templates_and_super_genies(tmp_path):
    from ctgedit.editing import COMPOSITE_NOT_HERE, EditableDocument, ElementDocument

    lib = str(tmp_path / "mylib.ctm")
    for kind, ok in (("genie", False), ("symbol", False), ("template", True), ("supergenie", True)):
        doc = ElementDocument.new_element(lib if kind != "symbol" else lib[:-1] + "l", "e1", kind)
        assert doc.composites_allowed is ok, kind
        if not ok:
            with pytest.raises(ValueError) as e:
                doc.paste_composite_genie("05edfae1-c999-469d-992c-e9894e84d4c9", (0, 0))
            assert str(e.value) == COMPOSITE_NOT_HERE
    assert EditableDocument(path=str(tmp_path / "Page.ctg")).composites_allowed is True


def test_a_copied_composite_cannot_be_pasted_into_a_genie(tmp_path, comp_page):
    from ctgedit.editing import COMPOSITE_NOT_HERE, EditableDocument, ElementDocument

    doc = EditableDocument.open(comp_page)
    comp = next(i for i, o in enumerate(doc.page.objects) if o.kind.value == "composite_genie")
    payload = doc.copy_payload([comp])
    genie = ElementDocument.new_element(str(tmp_path / "lib.ctm"), "g", "genie")
    with pytest.raises(ValueError) as e:
        genie.paste_payload(payload)
    assert str(e.value) == COMPOSITE_NOT_HERE
    tpl = ElementDocument.new_element(str(tmp_path / "lib.ctt"), "t", "template")
    assert tpl.paste_payload(payload)


def test_copy_to_library_keeps_composites_out_of_genies_and_symbols(tmp_path, comp_page):
    """Copy to Library of a selection holding a composite genie: refused into
    a genie or symbol library (help 1172148_1), taken by a template or a
    super genie."""
    from ctgedit.editing import COMPOSITE_NOT_HERE, EditableDocument

    doc = EditableDocument.open(comp_page)
    comp = next(i for i, o in enumerate(doc.page.objects) if o.kind.value == "composite_genie")
    for lib, elem in (("glib.ctm", "g1"), ("slib.ctl", "s1")):
        with pytest.raises(ValueError) as e:
            doc.copy_to_library([comp], str(tmp_path / lib), elem)
        assert str(e.value) == COMPOSITE_NOT_HERE
        assert not (tmp_path / lib).exists()
    assert doc.copy_to_library([comp], str(tmp_path / "sg.ctm"), "!pop@SUPER")
    assert doc.copy_to_library([comp], str(tmp_path / "tl.ctt"), "t1")


def test_the_composition_cache_is_never_written(tmp_path):
    from ctgedit.editing import ElementDocument
    from ctgedit.format import composites as C

    cache = tmp_path / "Proj" / "_CompositionCaches" / "{11111111-2222-3333-4444-555555555555}"
    cache.mkdir(parents=True)
    lib = cache / "cache.ctm"
    assert C.in_composition_cache(str(lib)) and not C.in_composition_cache(str(tmp_path))
    doc = ElementDocument.new_element(str(lib), "abc", "genie")
    with pytest.raises(ValueError):
        doc.save()
    assert not lib.exists()


# -- dialogs + window ----------------------------------------------------------------------
def test_composite_option_dialogs_are_read_only(comp_page):
    """Presentation Options can be shown read-only (a composite whose template
    is not in the project); the Paste Composite Genie form is editable and
    starts from the template's defaults."""
    from PySide6 import QtWidgets
    from ctgedit.format import composites
    from ctgedit.ui.dialog.composite_dialogs import PasteCompositeGenieDialog, PresentationOptionsDialog

    app = _app()
    donors = composites.donor_index(comp_page)
    templates = composites.load_templates(comp_page)
    guid = next(
        g
        for g in sorted(set(donors) & set(templates))
        if any(p.name == "Equipment" for p in templates[g].params)
    )
    info = composites.donor_info(donors[guid], templates)
    dlg = PresentationOptionsDialog(templates[guid], info.values(), read_only=True, note="locked")
    form = dlg.panel.form
    assert form.is_read_only()
    for p in form.params:
        w = form.widget(p.name)
        if isinstance(w, (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox)):
            assert w.isReadOnly(), p.name
        else:
            assert not w.isEnabled(), p.name
    box = dlg.findChild(QtWidgets.QDialogButtonBox)
    assert box.button(QtWidgets.QDialogButtonBox.Close) is not None
    assert box.button(QtWidgets.QDialogButtonBox.Ok) is None
    paste = PasteCompositeGenieDialog(templates, donors)
    for i in range(paste._list.count()):
        if paste._list.item(i).data(0x0100) == guid:
            paste._list.setCurrentRow(i)
    assert not paste._form.is_read_only()
    assert paste._form.values()["Equipment"] == ""  # the template's default
    assert paste.selection() == (None, {})  # '' is not a valid equipment
    want = composites.to_text(info.values()["equipment"])
    for name in list(paste._form.problems()):  # the donor's value for each required one
        paste._form.widget(name).setText(composites.to_text(info.values()[name.lower()]))
    sel_guid, vals = paste.selection()
    assert sel_guid == guid and vals["Equipment"] == want
    app.processEvents()


@pytest.fixture()
def win(comp_page):
    """A window on FxComposites of the test's fx_install copy (the templates of FxLib,
    the caches of FxMain, the content genies of FxLib, through FxMain's includes)."""
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(comp_page)
    w.show()
    QtWidgets.QApplication.processEvents()
    yield w
    for d in list(w._dialogs.values()):
        d.reject()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    for e in w._docs:
        e["doc"].dirty = False
    w.close()


def _composites(w):
    return [i for i, o in enumerate(w.doc.page.objects) if o.kind.value == "composite_genie"]


def test_window_rotates_a_composite(win):
    """Rotate / Flip stay enabled for a composite (CtDraw offers them) and
    turn it as one undo step."""
    comp = _composites(win)[0]
    win._on_canvas_selection([comp])
    for a in (win.act_rot_cw, win.act_rot_ccw, win.act_flip_h, win.act_flip_v):
        assert a.isEnabled()
    before = win.doc.serialize()
    win._rotate(True)
    assert win.doc.serialize() != before and win.doc.can_undo()
    win.doc.undo()
    assert win.doc.serialize() == before


def test_window_edit_composite_whose_cache_did_not_resolve(win):
    """A composite whose cache element did not resolve (no ``_resolved_ref``)
    still opens its Presentation Options with its Instances\\<hash>.xml (FxComp3)."""
    from ctgedit.ui.dialog.composite_dialogs import PresentationOptionsDialog

    comp = _named(win.doc, "FxComp3")
    obj = win.doc.page.objects[comp]
    obj.raw.pop("_resolved_ref", None)
    win._edit_genie(comp, obj)
    dlg = next(d for d in win._dialogs.values() if isinstance(d, PresentationOptionsDialog))
    assert dlg.panel._inst.text() == DRIVE_HASH
    assert dlg.panel.form.values()["DriveType"] == "Sprayer"


def test_window_edit_composite_shows_instance_options(win, monkeypatch):
    from PySide6 import QtWidgets
    from ctgedit.ui.dialog.composite_dialogs import PresentationOptionsDialog

    comp = _named(win.doc, "FxComp3")
    win._sel = [comp]
    # the context menu names it 'Edit Composite Genie…' and offers no
    # 'Edit Definition' of the composition cache
    seen = []

    class _Menu(QtWidgets.QMenu):
        def exec(self, *a):  # record instead of showing (no modal loop)
            seen.extend(x.text() for x in self.actions())

    monkeypatch.setattr(QtWidgets, "QMenu", _Menu)
    win._context_menu(QtCore_point(), [comp])
    assert "Edit Composite Genie…" in seen
    assert not any(t.startswith("Edit Definition") for t in seen)
    assert "Genie Parameters…" not in seen
    assert win._definition_of(comp) is None
    win._edit_genie(comp, win.doc.page.objects[comp])
    dlg = next(d for d in win._dialogs.values() if isinstance(d, PresentationOptionsDialog))
    vals = dlg.panel.form.values()
    assert (vals["DriveType"], vals["DisplayStatus"], vals["Orientation"]) == (
        "Sprayer",
        "Inside Right",
        "Right",
    )
    assert dlg.panel._inst.text() == DRIVE_HASH
    # the template is in the include chain: OK composes the options
    assert not dlg.panel.form.is_read_only()


def test_window_presentation_options_reloads_the_genies_options(win, monkeypatch):
    """The window's Presentation Options follows its genie (``index``) and
    its reload() re-reads the options the genie has now (``options_now``);
    a row the user typed in is kept."""
    from ctgedit.ui.dialog.composite_dialogs import PresentationOptionsDialog

    comp = _named(win.doc, "FxComp3")
    win._edit_genie(comp, win.doc.page.objects[comp])
    dlg = next(d for d in win._dialogs.values() if isinstance(d, PresentationOptionsDialog))
    assert dlg.index == comp
    info0 = win._composite_info_for(win.doc.page.objects[comp])
    free = [p.name for p in info0.template.params if p.type == "String" and not p.standard_values]
    assert len(free) >= 1
    name = free[0]
    real = win._composite_info_for

    class _Now:
        """The genie's options as another edit left them."""

        def __init__(self, info):
            self._info = info

        def __getattr__(self, attr):
            return getattr(self._info, attr)

        def values(self):
            out = dict(self._info.values())
            key = next((k for k in out if k.lower() == name.lower()), name)
            out[key] = "Area.FromPage"
            return out

    monkeypatch.setattr(win, "_composite_info_for", lambda obj: _Now(real(obj)))
    dlg.reload()
    assert dlg.values()[name] == "Area.FromPage"
    if len(free) > 1:
        typed = free[1]
        dlg.panel.form.widget(typed).setText("Area.Typed")
        dlg.reload()
        assert dlg.values()[typed] == "Area.Typed"
    dlg.reject()


def QtCore_point():
    from PySide6 import QtCore

    return QtCore.QPoint(10, 10)


def test_window_paste_composite_places_at_the_click(win):
    from ctgedit.format import composites as C
    from ctgedit.format import geometry as G

    donors = C.donor_index(win.doc.path)
    guid = sorted(donors)[0]
    win._arm_composite_placement(guid, "x", {"Equipment": "FxPlant.Area.X01"})
    assert win._pending_composite == guid
    n0 = len(win.doc.parsed.page.objects)
    win._on_draw_committed("symbol", {"rect": (300, 400, 364, 464), "click": True})
    assert win._pending_composite is None
    idx = win._sel[0]
    assert idx == n0
    gh = G.parse_geom_header(win.doc.parsed.page.objects[idx].body_raw)
    assert (gh.rect1[0], gh.rect1[1]) == (300, 400)
    # Esc (the Select tool) cancels an armed placement
    win._arm_composite_placement(guid, "x")
    win._set_tool("select")
    assert win._pending_composite is None


def test_window_paste_composite_greyed_in_a_genie(win, tmp_path):
    from ctgedit.editing import COMPOSITE_NOT_HERE, ElementDocument

    assert win.act_paste_composite.isEnabled()
    genie = ElementDocument.new_element(str(tmp_path / "lib.ctm"), "g", "genie")
    win.set_document(genie)
    win._update_actions()
    assert not win.act_paste_composite.isEnabled()
    assert win.act_paste_composite.statusTip() == COMPOSITE_NOT_HERE
    win._paste_composite_genie()
    assert win.statusBar().currentMessage() == COMPOSITE_NOT_HERE
