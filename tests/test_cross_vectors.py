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


def test_too_big_numbers_end_where_the_compiler_ends_them():
    """Every E2058 CtCmp32 reported in the engine's recorded compiles: the field tokens mark
    the same characters ('{0x10000000}0': a hex number stops before the digit past 32 bits,
    a decimal, binary or octal one after it)."""
    import glob
    import re

    from ctgedit import cicode_lex as L

    root = os.path.join(REPO, "external", "cicode-sim", "conformance", "compile")
    spans = []
    for p in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
        d = json.load(open(p, encoding="utf-8"))
        for e in d.get("errors") or [] if isinstance(d, dict) else []:
            m = re.search(r"\{([^{}]*)\}", e.get("context") or "") if e.get("code") == "E2058" else None
            if m:
                spans.append((p, e["context"], m))
    if not spans:
        pytest.skip("the engine's conformance/compile records are not checked out")
    bad = []
    for p, ctx, m in spans:
        text = ctx[: m.start()] + m.group(1) + ctx[m.end() :]
        a, b = m.start(), m.start() + len(m.group(1))
        if not any(t.kind == L.INVALID and (t.start, t.end) == (a, b) for t in L.tokenize(text)):
            bad.append("%s: %s" % (os.path.relpath(p, root), ctx))
    assert not bad
