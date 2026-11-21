"""The genie form designer."""

import glob
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from ctgedit.format import genieform as GF  # noqa: E402
from ctgedit.format import textcodec  # noqa: E402

import fxinstall  # noqa: E402
from qtkit import app as _app

#: the committed forms (read only: the editor tests copy theirs into tmp_path)
FRM_FILES = sorted(
    {os.path.normcase(p) for p in glob.glob(os.path.join(fxinstall.FX_INSTALL, "User", "*", "*.frm"))}
)
#: the form of fxparts.fx_insert_object: GUID, EVENTCLASS, W, H and a read-only AN on
#: lines 1 to 5, after two comment lines and fx_named's block
FXPARTS_FRM = os.path.join(fxinstall.FX_INSTALL, "User", "FxLib", "fxparts.frm")


def _shape(form):
    """What a form shows: its title, each layout item (a field by its definition) and the
    definitions it does not place."""
    lay = []
    for it in form.layout:
        if it.kind == "field":
            f = form.field_by_number(it.field)
            lay.append(
                (
                    "field",
                    it.line,
                    it.col,
                    it.width,
                    f.name,
                    f.size,
                    f.access,
                    f.list_form,
                    f.list_field,
                    f.list_filter,
                )
            )
        else:
            lay.append((it.kind, it.line, it.col, it.width, it.text))
    placed = {i.field for i in form.layout if i.kind == "field"}
    extra = sorted((f.name, f.size) for f in form.fields if f.number not in placed)
    return form.title, lay, extra


# --------------------------------------------------------------------------- the writer
def test_every_fixture_form_writes_and_reads_back_alike():
    """Every FORM of every .frm of the fake install: draft -> form -> text -> parse shows
    the same, and replace_form changes that block only. Eleven valid named forms (fx_pump,
    fx_pane; fx_named, fx_insert_object, the two fx_listy blocks; pane; fxsys_none; FxDemo's
    tank, pump and valve):
    fx_broken (fields numbered 4 to 11) is not valid and is left out, as CtDraw does."""
    assert len(FRM_FILES) == 5
    n = 0
    for p in FRM_FILES:
        text = textcodec.dec(open(p, "rb").read())
        forms = GF.parse_forms(text, p)
        spans = GF.form_spans(text)
        assert [s[0] for s in spans] == [f.name for f in forms if f.name], p
        for f in forms:
            if not f.valid or not f.name:
                continue
            n += 1
            items, extra = GF.draft_from_form(f)
            g = GF.form_from_draft(f.name, f.title, items, extra)
            assert GF.check_form(g) == "", (p, f.name)
            back = GF.lookup_form(GF.parse_forms(GF.format_form(g)), f.name)
            assert back is not None and _shape(back) == _shape(f), (p, f.name)
            if sum(1 for x in forms if x.name.lower() == f.name.lower()) != 1:
                continue
            new = GF.replace_form(text, g)
            a, b = next((s[1], s[2]) for s in spans if s[0] == f.name)
            assert new.startswith(text[:a]) and new.endswith(text[b:]), (p, f.name)
            # every other block as it was, in its order (two fx_listy blocks: a list,
            # not a dictionary by name)
            others = [(x.name, _shape(x)) for x in forms if x.valid and x.name != f.name]
            assert [
                (x.name, _shape(x)) for x in GF.parse_forms(new, p) if x.valid and x.name != f.name
            ] == others, (p, f.name)
    assert n == 11


def test_fields_are_numbered_in_reading_order():
    a = GF.FormField(0, "Second", 10)
    b = GF.FormField(0, "First", 8)
    items = [
        GF.DraftItem("field", 2, 10, 12, field=a),
        GF.DraftItem("text", 1, 1, 5, text="First"),
        GF.DraftItem("field", 1, 10, 10, field=b),
        GF.DraftItem("text", 2, 1, 6, text="Second"),
    ]
    form = GF.form_from_draft("G", "", items)
    assert [(f.number, f.name) for f in form.fields] == [(1, "First"), (2, "Second")]
    text = GF.format_form(form)
    back = GF.lookup_form(GF.parse_forms(text), "G")
    assert back is not None and back.valid
    assert [(it.line, it.col, it.field) for it in back.layout if it.kind == "field"] == [
        (1, 10, 1),
        (2, 10, 2),
    ]


def test_layout_text_refuses_overlaps_and_narrow_fields():
    f = GF.FormField(1, "A", 5)
    with pytest.raises(GF.FormError):
        GF.layout_text(
            [GF.LayoutItem("text", 1, 1, 9, text="Prompt  "), GF.LayoutItem("field", 1, 4, 5, field=1)]
        )
    with pytest.raises(GF.FormError):
        GF.layout_text([GF.LayoutItem("field", 1, 1, 2, field=1)])
    form = GF.form_from_draft("G", "", [GF.DraftItem("field", 1, 1, 2, field=f)])
    assert GF.check_form(form)  # (too narrow for '{1}')


def test_rules_and_quotes_read_back():
    f = GF.FormField(
        0, 'Ta"g^1', 20, list_form="Variable Tags", list_field="NAME", list_filter="TYPE=DIGITAL"
    )
    items = [
        GF.DraftItem("line", 1, 0, 40),
        GF.DraftItem("text", 2, 3, 6, text='Say "x"'),
        GF.DraftItem("field", 2, 12, 22, field=f),
        GF.DraftItem("line", 3, 0, 30),
    ]
    form = GF.form_from_draft("G", 'A "title"', items)
    assert GF.check_form(form) == ""
    back = GF.lookup_form(GF.parse_forms(GF.format_form(form)), "G")
    assert back.title == 'A "title"' and back.fields[0].name == 'Ta"g^1'
    assert back.fields[0].list_filter == "TYPE=DIGITAL"
    assert [(i.kind, i.line, i.col) for i in back.layout] == [
        ("line", 1, 0),
        ("text", 2, 3),
        ("field", 2, 12),
        ("line", 3, 0),
    ]


def test_replace_form_adds_and_drops_blocks():
    old = (
        '! comment\r\nFORM "A";\r\n1: "X", 4;\r\nNORMAL\r\n"X{1  }"\r\n'
        '! between\r\nFORM "B";\r\n1: "Y", 4;\r\nNORMAL\r\n"Y{1  }"\r\n'
        'FORM "a";\r\n1: "Z", 4;\r\nNORMAL\r\n"Z{1  }"\r\n'
    )
    form = GF.default_form("A", ["Tag"])
    new = GF.replace_form(old, form)
    names = [f.name for f in GF.parse_forms(new)]
    assert names == ["A", "B"]  # the second 'a' block went
    assert new.startswith("! comment\r\n") and "! between\r\n" in new
    assert _shape(GF.lookup_form(GF.parse_forms(new), "A")) == _shape(
        GF.lookup_form(GF.parse_forms(GF.format_form(form)), "A")
    )
    added = GF.replace_form(old, GF.default_form("C", ["Tag"]))
    assert added.startswith(old) and [f.name for f in GF.parse_forms(added)][-1] == "C"
    # a file with LF line ends keeps them, the layout its CR LF (a lone LF is a blank there)
    lf = old.replace("\r\n", "\n")
    two = GF.default_form("Two", ["P1", "P2"])
    out = GF.replace_form(lf, two)
    back = GF.lookup_form(GF.parse_forms(out), "Two")
    assert back is not None and [i.line for i in back.layout if i.kind == "field"] == [1, 2]
    assert "\r\n" not in out.split("NORMAL")[0]


def test_default_form_lists_the_parameters():
    form = GF.default_form("G", ["Tag", "Description"])
    assert GF.check_form(form) == ""
    assert [f.name for f in form.fields] == ["Tag", "Description"]
    assert [i.text for i in form.layout if i.kind == "text"] == ["Tag", "Description"]


# --------------------------------------------------------------------------- the window
def _editor(lib, element, params):
    _app()
    from ctgedit.ui.dialog.genie_form_editor import GenieFormEditor

    return GenieFormEditor(lib, os.path.splitext(os.path.basename(lib))[0], element, params)


def test_the_editor_moves_a_field_and_saves_only_its_block(tmp_path):
    """fxparts.frm's fx_insert_object (GUID, EVENTCLASS, W, H, AN on lines 1 to 5): its last
    field AN a line down, undone and done again, saved: only that block changes."""
    lib = tmp_path / "fxparts.ctm"
    lib.write_bytes(b"")
    shutil.copyfile(FXPARTS_FRM, tmp_path / "fxparts.frm")
    before = textcodec.dec((tmp_path / "fxparts.frm").read_bytes())
    ed = _editor(str(lib), "fx_insert_object", {"GUID": "", "EVENTCLASS": "", "W": "", "H": "", "AN": ""})
    assert not ed.dirty() and ed.problem_text() == ""
    fields = [it for it in ed.items if it.kind == "field"]
    assert [it.field.name for it in fields] == ["GUID", "EVENTCLASS", "W", "H", "AN"]
    last = fields[-1]
    ed.move_items([last], 0, 1)  # 'AN' a line down
    assert ed.dirty() and last.line == 6
    ed.move_items([fields[0]], 0, 1)  # onto the next line's field: refused
    assert fields[0].line == 1

    def line_of(name):
        return next(it.line for it in ed.items if it.kind == "field" and it.field.name == name)

    ed.undo()  # (the draft is a copy again)
    assert line_of("AN") == 5
    ed.redo()
    assert line_of("AN") == 6
    assert ed.save()
    after = textcodec.dec((tmp_path / "fxparts.frm").read_bytes())
    spans = [s for s in GF.form_spans(before) if s[0] == "fx_insert_object"]
    assert after.startswith(before[: spans[0][1]])  # the header comments are kept
    assert after.endswith(before[spans[0][2] :])  # and the blocks after it
    form = GF.lookup_form(GF.parse_forms(after), "fx_insert_object")
    assert form.valid and form.title == "Genie - Insert Object"
    assert [
        (f.name, it.line)
        for it in form.layout
        if it.kind == "field"
        for f in [form.field_by_number(it.field)]
    ] == [("GUID", 1), ("EVENTCLASS", 2), ("W", 3), ("H", 4), ("AN", 6)]
    assert not ed.dirty()
    ed.close()


def test_the_editor_makes_a_first_form(tmp_path):
    lib = tmp_path / "mylib.ctm"
    lib.write_bytes(b"")
    ed = _editor(str(lib), "Valve", {"Tag": "", "Desc": "", "Units": ""})
    assert ed.dirty()  # (made here: not saved yet)
    assert [it.field.name for it in ed.items if it.kind == "field"] == ["Tag", "Desc", "Units"]
    ed.title.setText("Valve Properties")
    ed._commit_edit()
    ed.items = [
        it
        for it in ed.items
        if not (it.kind == "field" and it.field.name == "Units")
        and not (it.kind == "text" and it.text == "Units")
    ]
    ed.add_parameter("Units")
    ed.add_rule()
    assert ed.problem_text() == ""
    assert ed.save()
    form = GF.lookup_form(GF.load_form_file(str(tmp_path / "mylib.frm")), "Valve")
    assert form is not None and form.valid and form.title == "Valve Properties"
    assert [f.name for f in form.placed_fields()] == ["Tag", "Desc", "Units"]
    assert any(it.kind == "line" for it in form.layout)
    ed.close()


def test_the_editor_will_not_save_a_form_with_a_problem(tmp_path, monkeypatch):
    from PySide6 import QtWidgets

    lib = tmp_path / "mylib.ctm"
    lib.write_bytes(b"")
    ed = _editor(str(lib), "Valve", {"Tag": ""})
    ed.add_field("")  # a field with no parameter
    assert "no parameter" in ed.problem_text() and not ed.btn_save.isEnabled()
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    assert not ed.save() and not (tmp_path / "mylib.frm").exists()
    ed._saved_state = ed._state()  # (no question on close)
    ed.close()
