"""Vectors shared with the Cicode engine."""

import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CANDIDATES = (os.path.join(REPO, "external", "cicode-sim", "conformance", "shared"),)


def _load(name):
    for d in CANDIDATES:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return json.load(open(p, encoding="utf-8"))
    pytest.skip("the engine's conformance/shared vectors are not checked out")


def test_cicode_names_are_the_vectors_ones():
    from ctgedit import cicode_names as cn
    from ctgedit.format.textcodec import CODEC

    bad = []
    for row in _load("cicode_names.json")["bytes"]:
        ch = bytes([row["byte"]]).decode(CODEC)
        if cn.is_start(ch) != row["start"] or cn.is_part(ch) != row["part"]:
            bad.append("0x%02X" % row["byte"])
    assert not bad


def test_the_tokenizers_take_code_page_names():
    """The field tokens, the rest-state evaluator and the association scan cut a name
    by the same rule (a Norwegian tag is one name, not 'R' + 'r_Temp')."""
    from ctgedit import cicode_lex as L
    from ctgedit.io import cicode_rest as C
    from ctgedit.format import supergenie as S

    name = "R\u00f8r_Temp"
    assert [r.text for r in L.references(name + ".PV + 1")] == [name + ".PV"]
    assert [(r.text, r.kind) for r in L.references("M\u00e5ling(1)")] == [("M\u00e5ling", L.FUNCTION)]
    assert C._tokenize(name + " <> 1")[0] == ("id", name)
    assert S._TOKEN.findall("?" + name + "?") == [name]
