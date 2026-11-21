"""Vectors shared with the Cicode engine, on the real corpus."""

import json
import os
import sys

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


def test_tag_scope_order_is_the_vectors_one():
    v = _load("tagscope.json")
    if not os.path.isdir(v["user"]):
        pytest.skip("the R2 corpus is not installed")
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import cross_vectors

    if cross_vectors.project_inputs() != v["inputs"]:
        pytest.skip("the R2 project tree changed since the vectors (tools/cross_vectors.py)")
    from ctgedit.io import tagscope as T

    bad = []
    for c in v["cases"]:
        got = [
            [p.name, p.relation] for p in T.scope(os.path.join(v["user"], c["project"]), c["root"]).projects
        ]
        if [[a.lower(), b] for a, b in got] != [[a.lower(), b] for a, b in c["projects"]]:
            bad.append("%s as %s" % (c["project"], c["root"]))
    assert not bad, bad
