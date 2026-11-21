"""The genie form designer over the installed .frm files."""

import glob
import os

import pytest

from ctgedit.format import genieform as GF
from ctgedit.format import textcodec

R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"
FRM_FILES = sorted({os.path.normcase(p) for p in glob.glob(os.path.join(R2, "*", "*.frm"))})


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


@pytest.mark.skipif(not FRM_FILES, reason="no installed .frm files")
def test_every_installed_form_writes_and_reads_back_alike():
    """Every FORM of every installed .frm: draft -> form -> text -> parse shows the same, and
    replace_form changes that block only."""
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
            others = {x.name: _shape(x) for x in forms if x.valid and x.name != f.name}
            for x in GF.parse_forms(new, p):
                if x.name in others:
                    assert _shape(x) == others[x.name], (p, x.name)
    assert n > 300
