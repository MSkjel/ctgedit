"""A genie inside a library genie shows its own bound values."""

import pytest

LIB = "fxnest.ctm"
ELEMENT = "n4"


def _open(fx_install):
    from ctgedit.editing import ElementDocument

    lib = fx_install.file("FxLib", LIB)
    ed = ElementDocument.open_element(lib, ELEMENT)
    for i in range(len(ed.parsed.page.objects)):
        if _is_nested(ed, i):
            return lib, ed, i
    pytest.fail("fxnest.n4 has no nested genie bound to %Name%")


def _level1(ed, i):
    from ctgedit.format import genie_place as GP

    r = GP.decode(ed.parsed.page.objects[i])
    return [dict(b.pairs) for b in r.entries[1]]


def test_the_dialog_shows_the_authored_values(fx_install):
    _lib, ed, i = _open(fx_install)
    rows = dict(ed.genie_all_params(i))
    assert rows.get("Name") == "%Name%"
    assert rows.get("Extra") == _level1(ed, i)[2]["Extra"] == "E%Extra%"


def test_ok_keeps_the_other_values_and_save_rederives_them(fx_install):
    from ctgedit.editing import ElementDocument

    lib, ed, i = _open(fx_install)
    before = _level1(ed, i)
    assert before[1].get("Extra") == "E" and "Name" not in before[1]
    assert ed.set_genie_bindings(i, {"Extra": "Q"})
    after = _level1(ed, i)
    # the authored bag: the new value, every other value kept
    assert after[2]["Extra"] == "Q" and after[2]["Name"] == "%Name%"
    assert {k: v for k, v in after[2].items() if k != "Extra"} == {
        k: v for k, v in before[2].items() if k != "Extra"
    }
    # the values bag: the new value, the others as they were ('%Name%' with its empty
    # default is no key there: CtDraw removes a key whose value comes out empty)
    assert after[1]["Extra"] == "Q"
    assert {k: v for k, v in after[1].items() if k != "Extra"} == {
        k: v for k, v in before[1].items() if k != "Extra"
    }
    ed.undo()
    assert _level1(ed, i) == before
    ed.redo()
    ed.save()
    again = ElementDocument.open_element(lib, ELEMENT)
    j = next(k for k in range(len(again.parsed.page.objects)) if _is_nested(again, k))
    saved = _level1(again, j)
    assert saved[2]["Extra"] == "Q" and saved[2]["Name"] == "%Name%"
    assert dict(again.genie_all_params(j)).get("Extra") == "Q"


def _is_nested(ed, k):
    from ctgedit.format import genie_place as GP

    po = ed.parsed.page.objects[k]
    if not (po.class_flag and (po.subtype_size & 0x201) == 0x201):
        return False
    r = GP.decode(po)
    return bool(r.entries) and len(r.entries) > 2 and ("Name", "%Name%") in r.entries[1][2].pairs


def test_a_caption_ending_in_a_parenthetical_and_a_colon_is_a_caption():
    import re
    from ctgedit.io.readerparts import style

    src = open(style.__file__, encoding="utf-8").read()
    assert '"):" in s' not in src  # (not the rule that any '):' is a value)
    rule = re.compile(r"\):[^():]{1,16}$")
    assert not rule.search("Flow since last flush (calculated):")
    assert rule.search("(Tank1.PV):#.#") and rule.search("(x):####")
