"""The 'input changed' hook looks at what the failing test reads."""

import importlib.util
import os
import sys
import textwrap
import types

import pytest

CORPUS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests_corpus")
if CORPUS_DIR not in sys.path:
    sys.path.append(CORPUS_DIR)
import corpus  # noqa: E402

#: a test module like the corpus tests: a module name for a page its test reads through a
#: helper, and another page only a different test reads
MODULE = """
import os

EX = r"C:\\FxUser\\FxProject"
PAGE = os.path.join(EX, "FxPageOne.ctg")
OTHER = os.path.join(EX, "FxPageTwo.ctg")


def _copy(tmp_path):
    return os.path.join(str(tmp_path), os.path.basename(PAGE))


def test_reads_one(tmp_path, monkeypatch):
    assert _copy(tmp_path)


def test_reads_other():
    assert OTHER
"""


@pytest.fixture
def fxmod(tmp_path, monkeypatch):
    path = tmp_path / "fx_corpus_module.py"
    path.write_text(textwrap.dedent(MODULE), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("fx_corpus_module", str(path))
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "fx_corpus_module", mod)
    spec.loader.exec_module(mod)
    return mod


def _item(fn, module, fixtures=(), params=None):
    it = types.SimpleNamespace(function=fn, module=module, fixturenames=list(fixtures))
    if params is not None:
        it.callspec = types.SimpleNamespace(params=params)
    return it


def test_the_text_of_a_test_follows_its_names_and_fixtures(fxmod):
    text = corpus.text_read_by(_item(fxmod.test_reads_one, fxmod, ("tmp_path", "monkeypatch")))
    names = corpus.names_in(text)
    # through _copy (a module function it calls) and PAGE / EX (module names)
    assert "fxpageone.ctg" in names
    # the module also names FxPageTwo.ctg (OTHER), which this test never reads
    assert "fxpagetwo.ctg" in corpus.names_in(open(fxmod.__file__, encoding="utf-8").read())
    assert "fxpagetwo.ctg" not in names


def test_parameters_count(fxmod):
    def fake(rel):
        return rel

    text = corpus.text_read_by(_item(fake, fxmod, params={"rel": "FxProject\\FxPage3.ctg"}))
    assert "fxpage3.ctg" in corpus.names_in(text)


def test_the_hook_turns_a_failure_on_a_changed_input_into_a_skip(fxmod, tmp_path, monkeypatch):
    user = tmp_path / "User"
    (user / "FxProject").mkdir(parents=True)
    (user / "FxProject" / "FxPageOne.ctg").write_bytes(b"fx page one")
    monkeypatch.setattr(corpus, "R2_USER", str(user))
    monkeypatch.setattr(corpus, "_sha", {})
    monkeypatch.delenv("CTG_HERMETIC", raising=False)
    item = _item(fxmod.test_reads_one, fxmod, ("tmp_path",))
    item.fspath, item.location = fxmod.__file__, (fxmod.__file__, 12, "test_reads_one")

    def rep():
        return types.SimpleNamespace(when="call", failed=True, outcome="failed", longrepr="x")

    monkeypatch.setattr(
        corpus, "_pins", {"FxProject/FxPageOne.ctg": corpus.sha1(os.path.join("FxProject", "FxPageOne.ctg"))}
    )
    r = rep()
    corpus.report(item, r)
    assert r.outcome == "failed"  # unchanged since its pin: the failure stands
    monkeypatch.setattr(corpus, "_pins", {"FxProject/FxPageOne.ctg": "0" * 40})
    r = rep()
    corpus.report(item, r)
    assert r.outcome == "skipped" and "input changed: FxProject/FxPageOne.ctg" in r.longrepr[2]
    # a hermetic run reads no corpus: the failure stands
    monkeypatch.setenv("CTG_HERMETIC", "1")
    r = rep()
    corpus.report(item, r)
    assert r.outcome == "failed"
