"""Genie forms, list lookups and parameter defaults."""

import os

import pytest
from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

#: fxws.frm's pane form (FxSa, the fake install tests/fixtures/FxInstall): the prompt
#: before each field in reading order and the parameter behind each field
PANE_PROMPTS = [
    "Name",
    "Default page",
    "Is default",
    "Scroll bars",
    "Display mode",
    "Fill mode",
    "Content types",
    "Tab pane",
    "Tab control",
    "Excluded panes",
    "Categories",
]
PANE_FIELDS = [
    "Name",
    "DefaultPage",
    "IsDefault",
    "HasScrollbars",
    "DisplayMode",
    "FillMode",
    "ContentTypes",
    "TabPaneName",
    "TabControlName",
    "ExcludedAutofillPanes",
    "EquipRefAssocs",
]
PANE_TITLE = "Genie - Fx pane"


def _list_tables(fx):
    """Write the list tables the fixture forms name."""
    from ctgedit.format import dbf

    dbf.create_table(
        os.path.join(fx.project("FxLib"), "FxBoolean.DBF"),
        [("NAME", 16)],
        [{"NAME": "FALSE"}, {"NAME": "TRUE"}],
    )
    with open(os.path.join(fx.bin, "citect.frm"), "wb") as fh:
        fh.write(b'FORM "Variable Tags", "Variable Tags", "variable", INDEX\r\n')
    dbf.create_table(
        os.path.join(fx.bin, "SCADAHELP.DBF"),
        [("TYPE", 16), ("DATA", 32), ("FILTER", 16), ("LOCALIZE", 1)],
        [{"TYPE": "BOOLEAN", "DATA": "FALSE"}, {"TYPE": "BOOLEAN", "DATA": "TRUE"}],
    )


def _pane(d, name="FxPaneContent"):
    """The pane genie ``name`` of FxSaMaster (fxws.pane)."""
    (i,) = d.find_objects_by_name(name)
    assert (d.genie_link(i) or ("", ""))[:2] == ("fxws", "pane")
    return i


# ------------------------------------------------------------------ grammar
SAMPLE = r"""
! a comment line, "quotes" ignored
FORM "Pump_East", "Genie - Pump";

1: "Tag", 32, readwrite, "Variable Tags", "NAME", "TYPE=DIGITAL";
2: "Label", 16;

NORMAL
"Tag  {1         }
{LINE, 20}
Label{2    }  MyFunc(^"#Name^")"

FORM "spaced";
1: "X", 6, readonly;
NORMAL "{ 1 }"

FORM "numbered";
2: "Y", 6;
NORMAL "{2  }"

FORM "twice", "first";
1: "Z", 6;
NORMAL "{1 }"
FORM "twice", "broken";
1: "Z", 6:
NORMAL "{1 }"
FORM "twice", "never reached";
1: "Z", 6;
NORMAL "{1 }"
FORM "undefined";
1: "A", 6;
NORMAL "{1 } {3 }"
"""


def test_frm_grammar_and_ctdraw_checks():
    from ctgedit.format import genieform as GF

    # (.frm files are CR LF: a CR ends a layout line, a lone LF is whitespace)
    forms = GF.parse_forms(SAMPLE.replace("\n", "\r\n"), "x.frm")
    by = {}
    for f in forms:
        by.setdefault(f.name, []).append(f)
    pump = by["Pump_East"][0]
    assert pump.valid and pump.title == "Genie - Pump"
    tag, lab = pump.fields
    assert (tag.number, tag.name, tag.size, tag.access) == (1, "Tag", 32, "readwrite")
    assert (tag.list_form, tag.list_field, tag.list_filter) == ("Variable Tags", "NAME", "TYPE=DIGITAL")
    assert (lab.name, lab.size, lab.list_form) == ("Label", 16, "")
    kinds = [(it.kind, it.line, it.col, it.width, it.text, it.field) for it in pump.layout]
    assert kinds == [
        ("text", 1, 1, 6, "Tag  ", -1),
        ("field", 1, 6, 12, "", 1),
        ("line", 2, 0, 20, "", -1),
        ("text", 3, 1, 6, "Label", -1),
        ("field", 3, 6, 7, "", 2),
        ("text", 3, 15, 16, 'MyFunc("#Name")', -1),
    ]  # ^" = a quote
    # CtDraw's dialog units: a field 4 per column / 15 per line
    assert pump.layout[1].dlu_rect() == (24, 3, 48, 12)
    assert pump.layout[0].dlu_rect() == (4, 5, 20, 10)
    assert pump.layout[2].dlu_rect() == (0, 25, 76, 1)
    assert pump.label_for(1) == "Tag" and pump.label_for(2) == "Label"
    assert [f.name for f in pump.placed_fields()] == ["Tag", "Label"]
    # '{ 1 }': a blank after the brace is an error in CtDraw (the help's own
    # "Tag{ 1 }" example would not load)
    assert "no space" in by["spaced"][0].error
    # the numbers must run 1..n
    assert "numbered 1 to 1" in by["numbered"][0].error
    assert "not a defined field" in by["undefined"][0].error
    # the last matching block that parses -- up to the first that fails
    assert GF.lookup_form(forms, "TWICE").title == "first"
    assert GF.lookup_form(forms, "pump_east") is pump
    assert GF.lookup_form(forms, "numbered") is None
    assert GF.lookup_form(forms, "nothing") is None


def test_unclosed_string_keeps_the_forms_before_it():
    from ctgedit.format import genieform as GF

    text = 'FORM "ok";\n1: "A", 6;\nNORMAL "{1 }"\nFORM "bad";\n1: "B, 6;\n'
    forms = GF.parse_forms(text)
    assert GF.lookup_form(forms, "ok") is not None
    assert any("not closed" in f.error for f in forms)


def test_fixture_forms_parse_like_ctdraw(fx_install):
    """Every genie form of the fake install parses (fxgenie.frm 2, fxparts.frm 5, fxws.frm
    1, fxsys.frm 1, fxdemo.frm 3), except the one CtDraw cannot use either: fxparts.frm's fx_broken
    numbers its 8 fields 4..11 (CtDraw shows its genie's DEFAULT form).
    tests_corpus/test_genie_forms.py checks every shipped form."""
    import glob
    from ctgedit.format import genieform as GF

    bad = {}
    n = 0
    for p in sorted(set(glob.glob(os.path.join(fx_install.user, "*", "*.frm")))):
        for f in GF.load_form_file(p):
            n += 1
            if not f.valid:
                bad.setdefault(os.path.basename(p).lower(), set()).add(f.name)
    assert n == 12
    assert bad == {"fxparts.frm": {"fx_broken"}}, bad


def test_pane_form_and_the_an_form(fx_install):
    """fxws.frm's pane form: its prompts, its fields in reading order. fxparts.frm:
    fx_named defines 'Spare' but its layout does not show it; fx_insert_object shows its
    read-only AN (field 5)."""
    from ctgedit.format import genieform as GF

    pane = GF.lookup_form(GF.load_form_file(fx_install.file("FxSa", "fxws.frm")), "pane")
    assert pane.title == PANE_TITLE
    assert [pane.label_for(f.number) for f in pane.placed_fields()] == PANE_PROMPTS
    assert [f.name for f in pane.placed_fields()] == PANE_FIELDS
    assert [it.text.strip() for it in pane.layout if it.kind == "text"] == PANE_PROMPTS
    forms = GF.load_form_file(fx_install.file("FxLib", "fxparts.frm"))
    named = GF.lookup_form(forms, "fx_named")
    names = [f.name for f in named.placed_fields()]
    assert "Spare" in [f.name for f in named.fields] and "Spare" not in names
    ins = GF.lookup_form(forms, "fx_insert_object")
    an = ins.field_by_number(5)
    assert an.name == "AN" and an.access == "readonly"
    assert "AN" in [f.name for f in ins.placed_fields()]


def test_form_search_order_is_ctdrawss(fx_install):
    """System and Include first, the included projects (their
    own includes before them), the page's project LAST. FxOther includes FxLib and
    FxMain; FxMain includes FxLib and FxStyle; FxStyle includes FxLib."""
    from ctgedit.format import genieform as GF

    dirs = [os.path.basename(d).lower() for d in GF.form_search_dirs(fx_install.project("FxOther"))]
    assert dirs[0] == "system" and dirs[1] == "include"
    assert dirs[-1] == "fxother"
    assert dirs.index("fxlib") < dirs.index("fxstyle") < dirs.index("fxmain")
    sa = fx_install.project("FxSa")
    form = GF.find_form(sa, "fxws", "pane")
    assert form is not None and os.path.normcase(form.source) == os.path.normcase(
        fx_install.file("FxSa", "fxws.frm")
    )
    pump = GF.find_form(fx_install.project("FxMain"), "fxgenie", "fx_pump")
    assert pump is not None and os.path.normcase(pump.source) == os.path.normcase(
        fx_install.file("FxLib", "fxgenie.frm")
    )
    assert GF.find_form(sa, "fxws", "no_such_genie") is None


def test_invalid_form_falls_back_and_is_explained(fx_install):
    """FxParts' FxBroken1 places fxparts.fx_broken, whose form numbers its 8 fields
    4..11."""
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(fx_install.page("FxParts"))
    (g,) = d.find_objects_by_name("FxBroken1")
    assert d.genie_link(g)[:2] == ("fxparts", "fx_broken")
    assert d.genie_form(g) is None  # CtDraw: the default form
    probs = d.genie_form_problems("fxparts", "fx_broken")
    assert probs and "numbered 1 to 8" in probs[0][1]
    _app()
    from ctgedit.ui.dialog.genie_dialogs import GenieSubstitutionDialog

    dlg = GenieSubstitutionDialog(
        d.page.objects[g], [], None, bindings=d.genie_all_params(g), problems=probs, ref="fxparts.fx_broken"
    )
    from PySide6 import QtWidgets

    notes = [w.text() for w in dlg.findChildren(QtWidgets.QLabel)]
    assert any("fxparts.frm" in t and "has an error" in t for t in notes)
    assert list(dlg.values()) == list(d.genie_all_params(g))
    assert dlg.windowTitle() == "Genie Parameters - fxparts.fx_broken"


# ------------------------------------------------------------------ lists
def test_list_filter_terms():
    from ctgedit.io.project import list_filter_value as V, parse_list_filter as P

    t = P("NAME", "*_CMD,TYPE=DIGITAL")
    assert [(x.field, x.kind) for x in t] == [("NAME", "suffix"), ("TYPE", "exact")]
    assert V(t, {"NAME": "PUMP1_CMD", "TYPE": "DIGITAL"}) == "PUMP1"
    assert V(t, {"NAME": "PUMP1_CMD", "TYPE": "INT"}) is None
    assert V(t, {"NAME": "PUMP1_PV", "TYPE": "DIGITAL"}) is None
    t = P("Name", "PANE_DISPLAYMODE_*")
    assert V(t, {"NAME": "PANE_DISPLAYMODE_Stretch"}) == "Stretch"
    assert V(t, {"NAME": "PANE_FILLMODE_Static"}) is None
    t = P("NAME", "TYPE=Digital")  # field names/values: no case
    assert V(t, {"NAME": "X", "TYPE": "DIGITAL"}) == "X"
    t = P("NAME", "A*B*C")
    assert t[0].kind == "glob" and V(t, {"NAME": "AxxByyC"}) == "xxyy"
    assert P("NAME", "")[0].kind == "any"
    with pytest.raises(ValueError):
        P("NAME", "A=1,B=2,C=3,D=4,E=5")


def test_list_values_of_the_pane_form(fx_install):
    """fxws.frm's lists on FxSa (FxLib's labels PANE_DISPLAYMODE_MAINTAIN / STRETCH and
    PANE_FILLMODE_AUTOFILL / STATIC, the content types of System and FxSa, FxSa's pages)
    and FxMain's 122 variable tags (11 of them DIGITAL); the BOOLEAN help rows and the
    FxBoolean table are _list_tables' stand-ins."""
    from ctgedit.io import project as PR

    _list_tables(fx_install)
    sa = fx_install.project("FxSa")
    assert PR.list_values(sa, "SCADAHELP", "BOOLEAN") == ["FALSE", "TRUE"]
    assert PR.list_values(sa, "FxBoolean", "NAME") == ["FALSE", "TRUE"]
    assert PR.list_values(sa, "Labels", "Name", "PANE_DISPLAYMODE_*") == ["MAINTAIN", "STRETCH"]
    assert PR.list_values(sa, "Labels", "Name", "PANE_FILLMODE_*") == ["AUTOFILL", "STATIC"]
    ct = PR.list_values(sa, "CONTYPE", "Name")
    assert {"FxL1", "FxFP", "FxFace", "FxMaster", "Master"} <= set(ct)
    pages = PR.list_values(sa, "Pages", "Name")
    assert "FxSaBlank" in pages and pages == sorted(pages, key=str.lower)
    main = fx_install.project("FxMain")
    dig = PR.list_values(main, "Variable Tags", "NAME", "TYPE=DIGITAL")
    alltags = PR.list_values(main, "Variable Tags", "NAME")
    assert dig and set(dig) < set(alltags)
    assert len(alltags) == 122 and "FX_PUMP1" in dig
    assert PR.list_values(sa, "fxNoSuchTable", "VALUETYPE") is None  # no such table


def test_function_help_list_includes_project_and_builtin_functions(fx_install, monkeypatch):
    from ctgedit.io.project import list_values
    from ctgedit.io import cicode_catalog as catalog

    monkeypatch.setattr(catalog, "builtin_functions", lambda: (catalog.CicodeFunction("PageDisplay"),))
    names = list_values(fx_install.project("FxMain"), "scadahelp", "Function")
    assert {"FxToggle", "FxLibShow", "PageDisplay"} <= set(names)
    assert names == sorted(names, key=str.lower)
    assert len(names) == len({name.lower() for name in names})


# ------------------------------------------------------------------ dialogs
def _prompts(dlg):
    from PySide6 import QtWidgets

    labs = [w for w in dlg.params_form.canvas.findChildren(QtWidgets.QLabel)]
    labs.sort(key=lambda w: (w.y(), w.x()))
    return [w.text().strip() for w in labs]


def test_localized_help_values_resolve_all_tokens_and_skip_missing_resources(fx_install, monkeypatch):
    from pathlib import Path
    from ctgedit.format import dbf
    from ctgedit.io import help_strings, project

    (Path(fx_install.bin) / "SE.SCADASystem.dll").touch()
    monkeypatch.setattr(help_strings, "_assembly_strings", lambda *_: {"IDS_DAY": "day", "IDS_MONTH": "May"})
    dbf.create_table(
        os.path.join(fx_install.bin, "SCADAHELP.DBF"),
        [("TYPE", 16), ("DATA", 48), ("FILTER", 16), ("LOCALIZE", 1)],
        [
            {"TYPE": "PERIOD", "DATA": "$(IDS_DAY) 1 $(IDS_MONTH)", "LOCALIZE": "1"},
            {"TYPE": "PERIOD", "DATA": "$(IDS_MISSING)", "LOCALIZE": "1"},
            {"TYPE": "PERIOD", "DATA": "weekly"},
        ],
    )
    assert project.list_values(fx_install.project("FxMain"), "HELP", "period") == ["day 1 May", "weekly"]


def test_font_list_keeps_a_stored_family_until_the_user_selects_another():
    _app()
    from PySide6 import QtGui
    from ctgedit.format.genieform import parse_forms
    from ctgedit.ui.dialog.genie_dialogs import GenieParamsForm

    source = 'FORM "Example";\r\n1: "Face", 64, readwrite, "HELP", "FONTTYPE";\r\nNORMAL "{1     }"'
    form = GenieParamsForm({"Face": "Unavailable family"}, form=parse_forms(source, "example.frm")[0])
    combo = form.combos["Face"]
    assert combo.currentText() == "Unavailable family"
    family = QtGui.QFontDatabase.families()[0]
    combo.setCurrentIndex(combo.findText(family))
    assert dict(form.values())["Face"] == family


def test_pane_dialog_is_ctdraws_form(fx_install):
    """Double-click on a pane (FxSaMaster's FxPaneContent) = its form's dialog with the
    form's prompts, order and lookups; the values are the instance's."""
    _app()
    from PySide6 import QtWidgets
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.dialog.genie_dialogs import GenieSubstitutionDialog

    _list_tables(fx_install)
    d = EditableDocument.open(fx_install.page("FxSaMaster", "FxSa"))
    pane = _pane(d)
    form = d.genie_form(pane)
    assert form is not None
    params = d.genie_all_params(pane)
    dlg = GenieSubstitutionDialog(
        d.page.objects[pane],
        [],
        None,
        bindings=params,
        form=form,
        list_values=d.form_list_values,
        an=d.genie_an(pane),
        ref="fxws.pane",
    )
    assert dlg.windowTitle() == PANE_TITLE
    assert _prompts(dlg) == PANE_PROMPTS
    pf = dlg.params_form
    assert list(pf.editors) == PANE_FIELDS
    assert [pf.combos["IsDefault"].itemText(i) for i in range(2)] == ["FALSE", "TRUE"]
    dm = pf.combos["DisplayMode"]
    assert [dm.itemText(i) for i in range(dm.count())] == ["MAINTAIN", "STRETCH"]
    # the fields sit where the layout puts them: Is default / Scroll bars on one line,
    # Default page left-aligned with Name
    ed = {k: w for k, (_g, w) in pf.editors.items()}
    assert ed["IsDefault"].y() == ed["HasScrollbars"].y() < ed["FillMode"].y()
    assert ed["IsDefault"].x() < ed["HasScrollbars"].x()
    assert ed["Name"].x() == ed["DefaultPage"].x() == ed["DisplayMode"].x()
    # the values round-trip: nothing changed = nothing to apply
    assert dict(dlg.values()) == {k: v for k, v in params.items()}
    assert not dlg.changed_values(params)
    dm.setEditText("MAINTAIN")
    assert dict(dlg.changed_values(params)) == {"DisplayMode": "MAINTAIN"}
    # the field lengths come from the form (IsDefault 6)
    assert pf.combos["IsDefault"].lineEdit().maxLength() == 6
    assert dlg.findChild(QtWidgets.QGroupBox) is None  # every parameter is on it
    # the dialog opens at least as wide as its form: no field is cut off at
    # the right-hand edge and there is no horizontal scroll bar
    dlg.show()
    QtWidgets.QApplication.processEvents()
    assert isinstance(pf.scroll, QtWidgets.QScrollArea)
    assert pf.canvas.width() >= pf.canvas.natural_size().width()
    assert pf.scroll.viewport().width() >= pf.canvas.natural_size().width()
    assert not pf.scroll.horizontalScrollBar().isVisible()
    dlg.reject()


def test_pane_dialog_content_types_are_a_multi_list(fx_install):
    """A form field whose list is CONTYPE is a multi-choice list (_MultiListField): two
    content types ticked = 'FxL1,FxFP'. fxws.frm's field 7 ContentTypes lists "Contype",
    "NAME"; FxSa's contype.DBF holds FxL1, FxFP, Master and __Internal."""
    _list_tables(fx_install)
    frm = os.path.join(fx_install.project("FxSa"), "fxws.frm")
    with open(frm, "rb") as fh:
        assert b'7: "ContentTypes", 64, readwrite, "Contype", "NAME";' in fh.read()
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.dialog.genie_dialogs import GenieSubstitutionDialog, _MultiListField

    d = EditableDocument.open(fx_install.page("FxSaMaster", "FxSa"))
    pane = _pane(d, "FxPaneHeaderBar")
    params = d.genie_all_params(pane)
    dlg = GenieSubstitutionDialog(
        d.page.objects[pane],
        [],
        None,
        bindings=params,
        form=d.genie_form(pane),
        list_values=d.form_list_values,
        an=d.genie_an(pane),
        ref="fxws.pane",
    )
    pf = dlg.params_form
    assert isinstance(pf.editor("ContentTypes"), _MultiListField)
    pf.editor("ContentTypes")._toggle("FxL1", True)
    pf.editor("ContentTypes")._toggle("FxFP", True)
    assert dict(dlg.changed_values(params))["ContentTypes"] == "FxL1,FxFP"
    dlg.reject()


def test_three_parameter_edit_is_one_undo_step_and_one_recompose(fx_install, monkeypatch):
    """FxSaMaster's FxPaneContent (Name Content, DefaultPage FxSaContent, IsDefault TRUE):
    three parameters in one edit."""
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")  # every re-derive == a full one
    page = fx_install.page("FxSaMaster", "FxSa")
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP

    d = EditableDocument.open(page)
    pane = _pane(d)
    before = dict(d.genie_all_params(pane))
    calls = []
    real = GP.refresh_instance

    def counted(*a, **k):
        calls.append(k.get("bindings"))
        return real(*a, **k)

    monkeypatch.setattr(GP, "refresh_instance", counted)
    n_undo = len(d._undo)
    new = {"Name": "Probe", "DefaultPage": "FxSaBlank", "IsDefault": "FALSE"}
    assert all(before[k] != v for k, v in new.items())
    assert d.set_genie_bindings(pane, new)
    assert len(calls) == 1 and calls[0] == new
    assert len(d._undo) == n_undo + 1
    after = d.genie_all_params(pane)
    assert {k: after[k] for k in new} == new
    d.undo()
    assert dict(d.genie_all_params(pane)) == before
    # redone and saved, all three values are on disk
    d.redo()
    d.save()
    d2 = EditableDocument.open(page)
    got = d2.genie_all_params(pane)
    assert {k: got[k] for k in new} == new
    assert {k: v for k, v in got.items() if k not in new} == {k: v for k, v in before.items() if k not in new}
    # nothing changed = nothing applied (no recompose, no undo step)
    n_undo = len(d2._undo)
    assert not d2.set_genie_bindings(pane, {})
    assert len(d2._undo) == n_undo


def test_an_is_shown_read_only_with_the_real_an(fx_install):
    """help 1180305: the list / tree view genies' AN = the genie's own AN
    (handle + 1), read-only -- the value AlarmListCreate(<AN>) needs. FxParts:
    FxInsertObject1 (fx_insert_object, a form with a read-only AN field; the element
    declares no %AN%) and FxAnList1 (fx_anlist: an AN parameter, no form)."""
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.dialog.genie_dialogs import GenieSubstitutionDialog

    d = EditableDocument.open(fx_install.page("FxParts"))
    for name, form_mode in (("FxInsertObject1", True), ("FxAnList1", False)):
        (root,) = d.find_objects_by_name(name)
        form = d.genie_form(root)
        assert (form is not None) == form_mode
        params = d.genie_all_params(root)
        assert ("AN" in params) == (not form_mode)
        an = d.genie_an(root)
        dlg = GenieSubstitutionDialog(
            d.page.objects[root], [], None, bindings=params, form=form, list_values=d.form_list_values, an=an
        )
        ed = dlg.params_form.an_editor
        assert ed is not None and ed.isReadOnly() and ed.text() == str(an)
        assert "AN" not in dlg.values()  # never written back
        from ctgedit.format import geometry as G

        assert an == G.get_handle(d.parsed.page.objects[root]) + 1


def test_default_form_rows_and_lengths():
    _app()
    from ctgedit.ui.dialog.genie_dialogs import GeniePasteDialog

    dlg = GeniePasteDialog("lib.genie", {"Tag": "T1", "AN": "", "Long": "x" * 300}, [], an=17)
    pf = dlg.params_form
    assert list(pf.editors) == ["Tag", "Long"]  # AN is not a value row
    assert pf.an_editor.text() == "17" and pf.an_editor.isReadOnly()
    assert pf.editor("Tag").maxLength() == 254  # 0xfe
    assert pf.editor("Long").maxLength() == 300  # a stored value is never cut
    assert dlg.windowTitle() == "Paste Genie - lib.genie"
    assert dict(dlg.values()) == {"Tag": "T1", "Long": "x" * 300}
    # the header names the genie and its AN, whether or not it declares an AN parameter
    from PySide6 import QtWidgets

    heads = [lab.text() for lab in dlg.findChildren(QtWidgets.QLabel)]
    assert any("lib.genie" in t and "AN 17" in t for t in heads)
    other = GeniePasteDialog("lib.other", {"Tag": "T1"}, [], an=23)
    assert any(
        "lib.other" in lab.text() and "AN 23" in lab.text() for lab in other.findChildren(QtWidgets.QLabel)
    )


def test_genie_fields_complete_from_the_scope_model(fx_install):
    """With the scope's shared completion model the text fields are expression
    fields completing the name at the cursor (the project shown beside it: FxLib's local
    variable fx_lib_local on FxMain's scope); a 'Variable Tags' list field is one too, not a
    drop-down of every tag."""
    _app()
    from ctgedit.ui.dialog.genie_dialogs import GeniePasteDialog
    from ctgedit.ui.objprops.expression import ExpressionField, completion_model_for

    model = completion_model_for(fx_install.project("FxMain"))
    dlg = GeniePasteDialog("lib.genie", {"Tag": "", "Label": "x"}, model)
    ed = dlg.params_form.editor("Tag")
    assert isinstance(ed, ExpressionField)
    names = ed.completion_names("fx_lib")
    assert "fx_lib_local" in names
    assert model.data(model.index(names.index("fx_lib_local"), 1)) == "FxLib"
    dlg.reject()


# ------------------------------------------------------------------ %name=default%
def test_template_declarations_take_the_token_default():
    from ctgedit.format import genie_place as GP

    bag = GP.Bag([("TXT_OFF", "%title=Pump% %Unit% %title%"), ("COMMENT", "%Unit=kW% %x=%")])
    assert list(GP.template_decls(bag).items()) == [("title", "Pump"), ("Unit", "kW"), ("x", None)]
    bag = GP.Bag([("A", "%v=1%"), ("B", "%V=2%")])  # the last default wins
    assert list(GP.template_decls(bag).items()) == [("v", "2")]


def test_percent_name_default_is_the_declared_default(fx_install, tmp_path):
    """A text '%title=Pump% units' in a genie element declares 'title' with
    the default 'Pump'; a paste lists it with that value. (A copy of
    FxLib's fxgenie.ctm: fx_value's text.)"""
    import shutil

    shutil.copy2(fx_install.file("FxLib", "fxgenie.ctm"), tmp_path / "fxgenie.ctm")
    from ctgedit.editing import ElementDocument
    from ctgedit.format.genie_place import declared_params, load_element

    lib = str(tmp_path / "fxgenie.ctm")
    assert "title" not in declared_params(load_element(lib, "fx_value"))
    d = ElementDocument.open_element(lib, "fx_value")
    i = next(k for k, o in enumerate(d.objects) if o.type_code == 0x05)
    assert d.set_text(i, "%title=Pump% units")
    d.save()
    params = declared_params(load_element(lib, "fx_value"))
    assert params.get("title") == "Pump"


# ------------------------------------------------------------------ commands
def _window():
    _app()
    from ctgedit.ui.main_window import MainWindow

    return MainWindow()


def test_paste_symbol_genie_super_genie_commands(fx_install, monkeypatch):
    from ctgedit.ui.browser import PageBrowserDialog

    w = _window()
    try:
        assert w.act_paste_symbol.shortcut().toString() == "F6"
        assert w.act_paste_genie.shortcut().toString() == "F11"
        assert w.act_paste_super.shortcut().toString() == "Ctrl+F11"
        w.open_document(fx_install.page("FxSaMaster", "FxSa"))
        seen = []

        def pick(
            parent,
            project_dir,
            kind="symbol",
            catalog=None,
            title=None,
            super_genies=None,
            linked_option=False,
        ):
            # (a symbol pick shows the Linked check box)
            assert linked_option == (kind == "symbol")
            seen.append((kind, title, super_genies))
            return (
                (fx_install.file("FxLib", "fxgenie.ctm"), "fx_pump")
                if kind == "genie"
                else (fx_install.file("FxLib", "fxsym.ctl"), "fx_box")
            )

        monkeypatch.setattr(PageBrowserDialog, "get_library_element", staticmethod(pick))
        w.act_paste_genie.trigger()
        assert seen[-1] == ("genie", "Paste Genie", False)
        assert w._pending_symbol == ("fxgenie", "fx_pump", "ctm")
        assert w.tool_actions["symbol"].isChecked()  # the next click places it
        w.act_paste_super.trigger()
        assert seen[-1] == ("genie", "Paste Super Genie", True)
        w.act_paste_symbol.trigger()
        assert seen[-1] == ("symbol", "Paste Symbol", None)
        assert w._pending_symbol == ("fxsym", "fx_box", "ctl")
        from PySide6 import QtWidgets

        edit = next(m for m in w.menuBar().findChildren(QtWidgets.QMenu) if m.title() == "&Edit")
        texts = [a.text() for a in edit.actions()]
        assert {"Paste Symbol…", "Paste Genie…", "Paste Super Genie…"} <= set(texts)
    finally:
        w.close()


def test_super_genies_are_not_in_the_genie_lists(fx_install):
    """FxMain's chain holds FxLib's fxsuper.ctm (!fx_popup@SUPER, !fx_detail@SUPER) and
    its ordinary genie libraries."""
    _app()
    from ctgedit.io.catalog import catalog_for, is_super_genie
    from ctgedit.ui.panels.library_panel import LibraryPanel

    main = fx_install.project("FxMain")
    cat = catalog_for()
    chain = cat.closure(cat.project_for_dir(main))
    assert any(is_super_genie(e.name) for e in cat.entries(chain, "genie"))
    p = LibraryPanel()
    p.set_project_dir(main)
    p.ensure_loaded()
    p.set_kind("genie")
    ents = p._kind_data("genie")["entries"]
    assert ents and not any(is_super_genie(e.name) for e in ents)
    # the element picker's filters (Paste Genie / Paste Super Genie)
    from ctgedit.ui.browser import dialog as BD

    made = []
    real_finish = BD.PageBrowserDialog._finish

    def finish(dlg):
        made.append(dlg)
        return None

    try:
        BD.PageBrowserDialog._finish = staticmethod(finish)
        BD.PageBrowserDialog.get_library_element(None, main, "genie", super_genies=False)
        BD.PageBrowserDialog.get_library_element(None, main, "genie", super_genies=True)
    finally:
        BD.PageBrowserDialog._finish = real_finish
    no_super, only_super = made
    sg = next(e for e in cat.entries(chain, "genie") if is_super_genie(e.name))
    plain = next(e for e in cat.entries(chain, "genie") if not is_super_genie(e.name))
    assert not no_super.element_filter(sg) and no_super.element_filter(plain)
    assert only_super.element_filter(sg) and not only_super.element_filter(plain)
    for dlg in made:
        dlg.deleteLater()


def test_genie_list_drops_only_super_genie_libraries(monkeypatch):
    """A library holding only super genies leaves the Library panel's genie
    tree; a library with ordinary genies keeps them, and an EMPTY library
    (a new one, nothing copied in yet) stays listed."""
    _app()
    from ctgedit.io.catalog import Entry, LibraryInfo
    from ctgedit.ui.panels import library_panel as LP

    libs = {
        n: LibraryInfo(path=r"C:\p\%s.ctm" % n, kind="genie", project="P")
        for n in ("mixed", "supers", "empty")
    }
    ents = [
        Entry("genie", "P", "pump", libs["mixed"].path, "mixed"),
        Entry("genie", "P", "!sg1@SUPER", libs["mixed"].path, "mixed"),
        Entry("genie", "P", "!sg2@SUPER", libs["supers"].path, "supers"),
    ]

    class FakeCatalog:
        def libraries(self, pr, kind):
            return list(libs.values())

        def entries(self, chain, kind):
            return list(ents)

    monkeypatch.setattr(LP, "catalog_for", lambda: FakeCatalog())
    p = LP.LibraryPanel()
    p._chain = ["P"]
    p._data = {}
    d = p._kind_data("genie")
    assert [e.name for e in d["entries"]] == ["pump"]
    assert [li.stem for _pr, li in d["libs"]] == ["mixed", "empty"]
    p.deleteLater()


def test_options_disable_genie_forms_and_group_button(fx_install, monkeypatch):
    from ctgedit.ui.prefs.settings import app_settings

    w = _window()
    try:
        w.open_document(fx_install.page("FxSaMaster", "FxSa"))
        pane = _pane(w.doc)
        opened = []
        monkeypatch.setattr(w, "object_properties", lambda i=None, **k: opened.append(i))
        st = app_settings()
        st.setValue("opt/disable_genie_forms", True)
        w._edit_genie(pane, w.doc.page.objects[pane])
        members = opened.copy()
        assert members and pane not in members
        assert set(members).issubset(w.doc.subtree(pane))
        assert ("genie", pane) not in w._dialogs
        st.setValue("opt/disable_genie_forms", False)
        st.setValue("opt/genie_group_button", True)
        w._edit_genie(pane, w.doc.page.objects[pane])
        dlg = w._dialogs[("genie", pane)]
        assert dlg.windowTitle() == PANE_TITLE
        assert dlg.group_button is not None
        dlg.group_button.click()
        assert opened == members + members
        dlg.reject()
        st.setValue("opt/genie_group_button", False)
        w._edit_genie(pane, w.doc.page.objects[pane])
        assert w._dialogs[("genie", pane)].group_button is None
        w._dialogs[("genie", pane)].reject()
    finally:
        w.close()


def test_settings_offer_the_genie_form_options():
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    keys = [k for k, _l in SettingsDialog.FLAGS]
    assert "disable_genie_forms" in keys and "genie_group_button" in keys
