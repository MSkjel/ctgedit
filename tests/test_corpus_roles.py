"""The corpus roles (tests_corpus/local.example.json) are documented and used, and the local
files that set them never get committed."""

import json
import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_DIR = os.path.join(REPO, "tests_corpus")
if CORPUS_DIR not in sys.path:
    sys.path.append(CORPUS_DIR)
import corpus  # noqa: E402

#: the folders whose code uses the roles
CODE_DIRS = ("tests_corpus", "tests_live", "tools")


def _example_roles():
    with open(corpus.EXAMPLE_FILE, encoding="utf-8") as fh:
        return json.load(fh)["roles"]


def test_every_documented_role_has_a_meaning_and_a_shape():
    roles = _example_roles()
    assert roles
    bad = [
        k
        for k, v in roles.items()
        if not (isinstance(v, dict) and (v.get("meaning") or "").strip() and "shape" in v)
    ]
    assert not bad, bad


#: a call of a role helper with its quoted role names
_ROLE_CALL = re.compile(
    r"""\b(?:role|role_value|role_path|role_paths|role_path_or_none|role_params|need_roles|"""
    r"""missing|files_of_role)\(\s*((?:["'][A-Za-z]\w*["']\s*,?\s*)+)"""
)


def _code_sources():
    """(relative path, text) of every Python file of the folders that use the roles."""
    for d in CODE_DIRS:
        for dp, _dn, fns in os.walk(os.path.join(REPO, d)):
            if "__pycache__" in dp or "fixtures" in dp:
                continue
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dp, fn)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    yield os.path.relpath(path, REPO), fh.read()


def test_every_role_the_code_uses_is_documented():
    documented = set(_example_roles())
    used = {}
    for rel, text in _code_sources():
        for m in _ROLE_CALL.finditer(text):
            for name in re.findall(r"""["']([A-Za-z]\w*)["']""", m.group(1)):
                used.setdefault(name, rel)
    unknown = {k: v for k, v in used.items() if k not in documented}
    assert not unknown, "roles missing from tests_corpus/local.example.json: %s" % unknown


def test_every_documented_role_is_used():
    """A documented role that no code reads is set for nothing (a machine that sets it
    loses what it was for): each one is named, quoted, somewhere in the code."""
    quoted = set()
    for _rel, text in _code_sources():
        quoted.update(re.findall(r"""["']([A-Za-z]\w*)["']""", text))
    unused = sorted(set(_example_roles()) - quoted)
    assert not unused, "roles of local.example.json no code reads: %s" % unused


def test_the_local_roles_are_documented():
    if not os.path.isfile(corpus.LOCAL_FILE):
        pytest.skip("no tests_corpus/local.json here")
    unknown = sorted(set(corpus.roles()) - set(_example_roles()))
    assert not unknown, "roles of local.json missing from local.example.json: %s" % unknown


def test_the_local_files_are_not_committed():
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, timeout=60)
    except OSError, subprocess.SubprocessError:
        pytest.skip("git is not available")
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    files = set(out.stdout.decode("utf-8", "replace").split("\0"))
    assert "tests_corpus/local.json" not in files
    assert "tests_corpus/corpus_pins.json" not in files
