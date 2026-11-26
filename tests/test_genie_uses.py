"""Where a genie's parameters go, and the genie form's fields made for it."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from qtkit import app as _app

from ctgedit.features import genie_uses as GU


def _role(template: str, key: str = "CMD", name: str = "P"):
    p = GU.ParamUses(name)
    GU._classify(p, GU.Use(key, key, GU._kind_of(key), template))
    return p


# ------------------------------------------------------------------ the roles
def test_a_value_is_classified_by_where_its_token_stands():
    assert _role("%P%").role == GU.ROLE_COMMAND
    assert _role("%P% AND Run", "VISIBLE").role == GU.ROLE_EXPRESSION
    assert _role('AssWin("!Face",%P=0%,0)').role == GU.ROLE_EXPRESSION  # a number
    p = _role('AssPopUp("!Pop","%P%_CMD","%P%_M")')
    assert p.role == GU.ROLE_NAME and p.patterns == [("", "_CMD"), ("", "_M")]
    assert p.names("Pump1") == ["Pump1_CMD", "Pump1_M"]
    assert _role("Area%P%.Level", "TXT_STR").patterns == [("Area", ".Level")]
    assert _role('AssResolve("%P%")').role == GU.ROLE_STRING
    assert _role('Message("Pump %P% failed")').role == GU.ROLE_TEXT
    assert _role("%P% @(Pump Control)", "COMMENT").role == GU.ROLE_TEXT
    assert _role("%P%", "PRIV").role == GU.ROLE_OTHER
    # two tokens in one name: not a name this parameter makes alone
    assert _role("%P%%Q%_PV", "TXT_STR").role == GU.ROLE_EXPRESSION


def test_the_strongest_role_wins():
    p = GU.ParamUses("Tag")
    for key, t in (("TEXT", "%Tag%"), ("CMD", 'AssWin("!F","%Tag%_PV")'), ("COL_A", "%Tag%")):
        GU._classify(p, GU.Use(key, key, GU._kind_of(key), t))
    assert p.roles == {GU.ROLE_TEXT, GU.ROLE_NAME, GU.ROLE_EXPRESSION}
    assert p.role == GU.ROLE_EXPRESSION and p.patterns == [("", "_PV")]


def test_substitute_fills_values_defaults_and_keeps_missing_tokens():
    t = 'AssWin("!F",%X=0%,"%Tag%_PV")'
    assert GU.substitute(t, {"tag": "P1"}) == 'AssWin("!F",0,"P1_PV")'
    assert GU.substitute(t, {}, keep_missing=True) == 'AssWin("!F",0,"%Tag%_PV")'


def test_the_fixture_genies_uses(fx_install):
    lib = os.path.join(fx_install.project("FxLib"), "fxgenie.ctm")
    pump = GU.element_uses(lib, "fx_pump", ["Tag", "Label"])
    assert pump["tag"].role == GU.ROLE_EXPRESSION
    assert {u.key.upper() for u in pump["tag"].uses} >= {"COL_A", "TXT_STR", "CMD"}
    assert pump["label"].role == GU.ROLE_TEXT
    valve = GU.element_uses(lib, "fx_valve", ["TagPrefix"])["tagprefix"]
    assert valve.role == GU.ROLE_NAME and valve.patterns == [("", "_ON")]
    assert GU.element_uses(lib, "fx_valve", ["TagPrefix", "Spare"])["spare"].uses == []


# ------------------------------------------------------------------ the form
def _paste(fx_install, element, params):
    from ctgedit.ui.dialog.genie_dialogs import GeniePasteDialog
    from ctgedit.ui.objprops.expression import completion_model_for

    _app()
    lib = os.path.join(fx_install.project("FxLib"), "fxgenie.ctm")
    uses = GU.element_uses(lib, element, list(params))
    model = completion_model_for(fx_install.project("FxMain"))
    return GeniePasteDialog("fxgenie." + element, params, model, uses=uses)


class _FakeChecker:
    """ctsim/checkExpression as the form sees it: the Cicode it is given, and a name of
    ``undefined`` in it reported as the engine reports an undefined tag."""

    def __init__(self, undefined=()):
        self.undefined = set(undefined)
        self.seen = []

    def check(self, text, kind, callback):
        self.seen.append((kind, text))
        warn = [
            {
                "kind": "tag",
                "message": "the project does not define the tag %s (the runtime raises error 424 for it)" % n,
                "column": text.find(n) + 1,
            }
            for n in sorted(self.undefined)
            if n in text
        ]
        callback({"ok": True, "errors": [], "warnings": warn})


def test_a_tag_prefix_completes_the_values_that_make_tags(fx_install):
    from ctgedit.ui.objprops.expression import ExpressionField, NameCompletionModel

    dlg = _paste(fx_install, "fx_valve", {"TagPrefix": ""})
    ed = dlg.params_form.editor("TagPrefix")
    assert isinstance(ed, ExpressionField) and isinstance(ed.name_completer().model(), NameCompletionModel)
    names = ed.completion_names("fx_v")
    assert names[:2] == ["FX_V1", "FX_V2"] and len(names) == 8
    m = ed.name_completer().model()
    assert m.data(m.index(0, 1)) == "1 of 1 tags"
    dlg.reject()


def test_a_field_checks_the_genies_cicode_with_the_values(fx_install, monkeypatch):
    """The compiler says whether the genie's Cicode works with the value typed: each
    command and expression the parameter goes into, the form's values in it."""
    from ctgedit.ui.dialog import genie_dialogs as GD

    fake = _FakeChecker({"FX_V9_ON"})
    monkeypatch.setattr(GD, "checker_for", lambda _d: fake)
    dlg = _paste(fx_install, "fx_valve", {"TagPrefix": ""})
    ed = dlg.params_form.editor("TagPrefix")
    ed.setText("FX_V9")
    assert ed.check_now()
    assert ("expression", "FX_V9_ON") in fake.seen
    assert "the project does not define the tag FX_V9_ON" in ed.advice_text()
    labels = {u.label for u in dlg.params_form.param_uses("TagPrefix").uses}
    assert any(ed.advice_text().startswith(lab + ": ") for lab in labels)
    ed.setText("FX_V1")
    assert ed.check_now() and ed.advice_text() == ""
    dlg.reject()
    # the other parameters' values go in; one not filled in stays a token for the template rule
    dlg = _paste(fx_install, "fx_pump", {"Tag": "", "Label": ""})
    monkeypatch.setattr(GD, "checker_for", lambda _d: fake)
    check = GD.GenieCicodeCheck(dlg.params_form, "Tag", fake)
    texts = [t for _l, _k, t in check.texts("FX_PUMP1")]
    assert texts and all("%Tag%" not in t for t in texts) and any("FX_PUMP1" in t for t in texts)
    dlg.reject()


def test_problems_of_several_fields_name_their_field():
    from ctgedit.ui.dialog.genie_dialogs import _merged

    got = _merged(
        [
            (
                "Command",
                {
                    "ok": False,
                    "errors": [{"name": "E2064", "message": "Function 'X' not found", "column": 4}],
                },
            ),
            (
                "Fill colour condition",
                {
                    "ok": True,
                    "errors": [],
                    "warnings": [{"kind": "tag", "message": "no tag A_ON", "column": 3}],
                },
            ),
        ]
    )
    assert not got["ok"] and got["errors"][0]["message"] == "Command: Function 'X' not found"
    assert got["errors"][0]["column"] == 0
    assert got["warnings"][0]["message"] == "Fill colour condition: no tag A_ON"
    assert got["warnings"][0]["column"] == 0 and got["warnings"][0]["kind"] == "tag"
    assert _merged([("Command", {"error": "no simulator"})]) == {"error": "no simulator"}


def test_fields_follow_the_parameters_roles(fx_install):
    from PySide6 import QtWidgets

    from ctgedit.ui.objprops.expression import ExpressionField

    dlg = _paste(fx_install, "fx_pump", {"Tag": "", "Label": ""})
    tag = dlg.params_form.editor("Tag")
    assert isinstance(tag, ExpressionField) and tag._kind == "expr"
    assert [a.text() for a in tag._menu.actions()] == ["Insert Tag…", "Insert Function…"]
    label = dlg.params_form.editor("Label")
    assert type(label) is QtWidgets.QLineEdit and label.completer() is None
    dlg.reject()


def test_the_uses_panel_follows_the_field_and_its_value(fx_install):
    dlg = _paste(fx_install, "fx_valve", {"TagPrefix": ""})
    assert "Where the genie uses TagPrefix:" in dlg.uses_text()
    assert "%TagPrefix%_ON" in dlg.uses_text()
    dlg.params_form.editor("TagPrefix").setText("FX_V2")
    assert "FX_V2_ON" in dlg.uses_text()
    dlg.reject()


def test_a_placed_genie_and_a_paste_find_their_uses(fx_install):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxBig", "FxMain"))
    assert doc.genie_uses_for("fxgenie", "fx_valve", ["TagPrefix"])["tagprefix"].patterns == [("", "_ON")]
    i = doc.place_library_element("fxgenie", "fx_valve", "ctm", (300, 200), params={"TagPrefix": "FX_V1"})
    uses = doc.genie_param_uses(i)
    assert uses["tagprefix"].role == GU.ROLE_NAME
