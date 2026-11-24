"""The Cicode tokens of the expression and command fields (ctgedit/cicode_lex.py)."""

import pytest

from ctgedit import cicode_lex as L


def _kinds(text):
    return [(t.kind, text[t.start : t.end]) for t in L.tokenize(text)]


def test_an_expression_and_a_command():
    assert _kinds("IF Level > 50 AND NOT Pump.On THEN Run = TRUE END") == [
        (L.KEYWORD, "IF"),
        (L.NAME, "Level"),
        (L.OPERATOR, ">"),
        (L.NUMBER, "50"),
        (L.OPERATOR_WORD, "AND"),
        (L.OPERATOR_WORD, "NOT"),
        (L.NAME, "Pump.On"),
        (L.KEYWORD, "THEN"),
        (L.NAME, "Run"),
        (L.OPERATOR, "="),
        (L.LABEL, "TRUE"),
        (L.KEYWORD, "END"),
    ]
    assert _kinds('AssWin("!Pop", 0); PageNext()')[:3] == [
        (L.FUNCTION, "AssWin"),
        (L.PUNCT, "("),
        (L.STRING, '"!Pop"'),
    ]
    # a dotted name is a reference, not a call ('True.Value' is a name too)
    assert _kinds("FxPlant.Pump1.Speed")[0] == (L.NAME, "FxPlant.Pump1.Speed")
    assert _kinds("True.Value")[0] == (L.NAME, "True.Value")
    assert _kinds("INT x")[0] == (L.TYPE, "INT")


def test_strings_escapes_and_genie_parameters_inside_them():
    assert _kinds('Message("a ^"b^" c")')[2] == (L.STRING, '"a ^"b^" c"')
    assert _kinds('"%Tag%_PV"') == [(L.STRING, '"'), (L.PARAM, "%Tag%"), (L.STRING, '_PV"')]
    toks = L.tokenize('Foo("open')
    assert toks[-1].kind == L.STRING and "not closed" in toks[-1].problem


def test_genie_parameters_and_super_genie_associations():
    assert _kinds("%Tag%.Value + ?INT 2? + Area%N%.Level") == [
        (L.PARAM, "%Tag%"),
        (L.FIELD, ".Value"),
        (L.OPERATOR, "+"),
        (L.ASSOCIATION, "?INT 2?"),
        (L.OPERATOR, "+"),
        (L.NAME, "Area"),
        (L.PARAM, "%N%"),
        (L.FIELD, ".Level"),
    ]
    assert _kinds("%X=0% + %a^%b%")[0] == (L.PARAM, "%X=0%")
    assert _kinds("%a^%b%")[0] == (L.PARAM, "%a^%b%")
    # a lone '%' is no parameter
    assert L.tokenize("5 % 2")[1].kind == L.INVALID


def test_numbers_and_what_the_compiler_refuses():
    assert [k for k, _ in _kinds("0x1F 0b101 0o17 1.5e3 42")] == [L.NUMBER] * 5
    for bad in ("99999999999", "0x123456789", "a == b", "a >< b"):
        assert any(t.kind == L.INVALID and t.problem for t in L.tokenize(bad)), bad
    assert L.tokenize("CiCode")[0].kind == L.INVALID  # reserved
    assert "character" in L.tokenize("a $ b")[1].problem


def test_display_formats_and_the_stored_range():
    assert _kinds("Level:###.# EU") == [(L.NAME, "Level"), (L.PUNCT, ":"), (L.FORMAT, "###.# EU")]
    assert _kinds("Level : ##")[-1] == (L.FORMAT, "##")
    assert L.tokenize("Level ###")[1].kind == L.INVALID  # a picture needs its ':'
    assert [k for k, _ in _kinds("(Foo(1))^(-4000,4000)")][6:9] == [L.RANGE, L.PUNCT, L.OPERATOR]


def test_comments_and_vba():
    assert _kinds("A = 1 ! the rest")[-1] == (L.COMMENT, "! the rest")
    assert _kinds("A = 1 // the rest")[-1] == (L.COMMENT, "// the rest")
    assert _kinds("A /* x */ + 1")[1] == (L.COMMENT, "/* x */")
    assert "not closed" in L.tokenize("A /* x")[1].problem
    assert _kinds("CiVBA\r\nX.Caption = Y") == [(L.KEYWORD, "CiVBA"), (L.VBA, "\r\nX.Caption = Y")]


@pytest.mark.parametrize(
    "text",
    [
        'IF A THEN AssWin("!P", 0, 0, 9, "%Tag%_PV") ELSE Beep() END',
        "Tag:##.# + 0x1F - ?INT 2? * %N=3% / 2 ! done",
        'Foo("^"x", [1], A.B.C) ; Bar(1,,2)',
        "  ",
        "",
    ],
)
def test_tokens_are_in_order_and_cover_every_visible_character(text):
    toks = L.tokenize(text)
    covered = set()
    last = 0
    for t in toks:
        assert last <= t.start < t.end <= len(text)
        last = t.end
        covered.update(range(t.start, t.end))
    assert all(i in covered for i, c in enumerate(text) if not c.isspace())


def test_the_call_the_cursor_is_in():
    text = 'AssWin("!Pop", (1 + 2), Foo(3, '
    c = L.call_at(text, len(text))
    assert (c.name, c.argument, c.open_paren) == ("Foo", 1, 27)
    # brackets that group pass to the call around them
    c = L.call_at('AssWin("!Pop", (1 + 2', 20)
    assert (c.name, c.argument) == ("AssWin", 1)
    assert L.call_at("Foo(1) + 2", 9) is None
    assert L.call_at('Foo("a, b', 7) is None  # (in a string: no hint)
    assert L.call_at("Foo(", 0) is None


def test_signatures_split_into_their_arguments():
    head, args, tail = L.split_signature("INT AlarmAck(INT Mode, INT Value [, STRING ClusterName])")
    assert head == "INT AlarmAck(" and tail == ")"
    assert args == [
        L.Argument("INT Mode", False),
        L.Argument("INT Value", False),
        L.Argument("STRING ClusterName", True),
    ]
    assert [a.optional for a in L.split_signature("(Mode, Value [,Cluster [, Extra]])")[1]] == [
        False,
        False,
        True,
        True,
    ]
    assert L.split_signature("INT F(STRING s, INT x = 0)")[1][1] == L.Argument("INT x = 0", True)
    assert L.split_signature("F()")[1] == []
    assert L.split_signature("no brackets") is None


def test_references_leave_out_names_a_genie_makes_and_key_arguments():
    text = 'Tank_1 > 1 AND Pump("a", %Tag%_PV, Area%N%.Level, ?INT 2?, Arg1) ! Ghost'
    got = [(r.text, r.kind) for r in L.references(text)]
    assert got == [("Tank_1", L.NAME), ("Pump", L.FUNCTION), ("Arg1", L.NAME)]
    assert [r.text for r in L.references(text, command=True)] == ["Tank_1", "Pump"]
    r = L.references("1 + Motor.Speed")[0]
    assert (r.start, r.end, r.text) == (4, 15, "Motor.Speed")
    assert L.alarm_field("Alarm1.Ack") and not L.alarm_field("Pump.Speed") and not L.alarm_field("On")


def test_fill_template_keeps_the_columns():
    text = '%Tag%_PV + ?INT 2? + Area%N%.Level + Message("%Desc%")'
    filled, spans = L.fill_template(text)
    assert filled == 'Gp____PV + Gp_____ + AreaGp_.Level + Message("Gp____")'
    assert len(filled) == len(text)
    assert [filled[s:e] for s, e in spans] == ["Gp____PV", "Gp_____", "AreaGp_", "Gp____"]
    assert L.fill_template("Tank_1 > 1") == ("Tank_1 > 1", [])


def test_a_parameters_default_may_hold_quotes():
    text = 'OpenURL(%WebIP="127.0.0.1"%) + "50%" + "%Desc%"'
    got = [(text[t.start : t.end], t.kind) for t in L.tokenize(text)]
    assert ('%WebIP="127.0.0.1"%', L.PARAM) in got and ('"50%"', L.STRING) in got
    assert ("%Desc%", L.PARAM) in got
    assert L.param_parts('%Line3=" Hello"%') == ("Line3", '" Hello"')
    assert L.param_parts("%Tag%") == ("Tag", None) and L.param_parts("%a=%") == ("a", None)
