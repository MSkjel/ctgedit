"""Corpus pins: a changed file is reported, an unchanged one isn't."""

import os
import sys

CORPUS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests_corpus")
if CORPUS_DIR not in sys.path:
    sys.path.append(CORPUS_DIR)
import corpus  # noqa: E402


def test_a_changed_pinned_file_is_reported(tmp_path, monkeypatch):
    user = tmp_path / "User"
    for proj in ("FxProject", "FxOtherProject"):
        (user / proj).mkdir(parents=True)
    (user / "FxProject" / "FxPage.ctg").write_bytes(b"fx page")
    (user / "FxOtherProject" / "fxpage.ctg").write_bytes(b"the other fx page")
    monkeypatch.setattr(corpus, "R2_USER", str(user))
    monkeypatch.setattr(corpus, "_sha", {})
    # a name is looked up in every project, case blind
    rels = corpus.files_named("fxpage.ctg")
    assert rels == [os.path.join("FxOtherProject", "fxpage.ctg"), os.path.join("FxProject", "FxPage.ctg")]
    keys = [r.replace("\\", "/") for r in rels]
    real = {k: corpus.sha1(r) for k, r in zip(keys, rels)}
    monkeypatch.setattr(corpus, "_pins", dict(real))
    assert corpus.changed_inputs('PAGE = "FxPage.ctg"') == []
    monkeypatch.setattr(corpus, "_pins", dict(real, **{keys[1]: "0" * 40}))
    assert corpus.changed_inputs('PAGE = "FxPage.ctg"') == [keys[1]]
    assert corpus.changed_inputs("nothing named here") == []
    # a file changed on disk after its pin (the sha1 cache follows the file's mtime / size)
    monkeypatch.setattr(corpus, "_pins", dict(real))
    (user / "FxProject" / "FxPage.ctg").write_bytes(b"fx page, edited")
    assert corpus.changed_inputs('PAGE = "FxProject\\\\FxPage.ctg"') == [keys[1]]


def test_the_pins_file_is_next_to_the_corpus_module():
    # (the pins are local: git-ignored, so a fresh checkout has none, and pins() is empty)
    assert os.path.dirname(corpus.PINS_FILE) == CORPUS_DIR
    assert os.path.dirname(corpus.LOCAL_FILE) == CORPUS_DIR
    assert os.path.isfile(corpus.EXAMPLE_FILE)


def _local(tmp_path, monkeypatch, roles):
    import json

    f = tmp_path / "local.json"
    f.write_text(json.dumps(roles), encoding="utf-8")
    monkeypatch.setattr(corpus, "LOCAL_FILE", str(f))
    monkeypatch.setattr(corpus, "_roles", {})
    monkeypatch.setattr(corpus, "_roles_key", [None])


def test_a_role_gives_its_value_or_skips(tmp_path, monkeypatch):
    import pytest

    user = tmp_path / "User"
    (user / "FxProject").mkdir(parents=True)
    (user / "FxProject" / "FxPage.ctg").write_bytes(b"fx page")
    monkeypatch.setattr(corpus, "R2_USER", str(user))
    _local(
        tmp_path,
        monkeypatch,
        {
            "_about": "a comment",
            "fx_page": "FxProject/FxPage.ctg",
            "gone_page": "FxProject/No.ctg",
            "fx_cases": [["FxProject/FxPage.ctg", "fxlib", "fxgenie"]],
            "fx_name": "fxgenie",
            "empty_name": "",
        },
    )
    assert "_about" not in corpus.roles()
    assert corpus.role("fx_name") == "fxgenie"
    assert corpus.role_path("fx_page") == os.path.join(str(user), "FxProject", "FxPage.ctg")
    assert corpus.role_path_or_none("gone_page") is None
    with pytest.raises(
        pytest.skip.Exception, match="not configured: set nothing_here in tests_corpus/local.json"
    ):
        corpus.role("nothing_here")
    with pytest.raises(pytest.skip.Exception, match="not configured: set empty_name"):
        corpus.role("empty_name")
    with pytest.raises(pytest.skip.Exception, match="not installed: gone_page"):
        corpus.role_path("gone_page")
    assert corpus.missing("fx_page", "fx_name") is None
    assert corpus.role_params("fx_cases", 3) == [("FxProject/FxPage.ctg", "fxlib", "fxgenie")]
    skipped = corpus.role_params("nothing_here", 3)
    assert len(skipped) == 1 and skipped[0].values == (None, None, None) and skipped[0].marks
    assert corpus.files_of_role("fx_cases") == [os.path.join("FxProject", "FxPage.ctg")]
    assert os.path.join("FxProject", "No.ctg") in corpus.role_files()


def test_a_failure_on_a_roles_changed_file_is_reported(tmp_path, monkeypatch):
    user = tmp_path / "User"
    (user / "FxProject").mkdir(parents=True)
    (user / "FxProject" / "FxPage.ctg").write_bytes(b"fx page")
    monkeypatch.setattr(corpus, "R2_USER", str(user))
    monkeypatch.setattr(corpus, "_sha", {})
    _local(tmp_path, monkeypatch, {"fx_page": "FxProject/FxPage.ctg"})
    key = "FxProject/FxPage.ctg"
    monkeypatch.setattr(corpus, "_pins", {key: corpus.sha1(os.path.join("FxProject", "FxPage.ctg"))})
    src = 'PAGE = corpus.role_path("fx_page")'
    assert corpus.roles_in(src) == ["fx_page"]
    assert corpus.changed_inputs(src) == []
    monkeypatch.setattr(corpus, "_pins", {key: "0" * 40})
    assert corpus.changed_inputs(src) == [key]
    assert corpus.changed_inputs('PAGE = corpus.role_path("other_page")') == []


def test_the_user_projects_are_the_ones_aveva_does_not_ship(tmp_path, monkeypatch):
    user = tmp_path / "User"
    for name in ("Example", "Include", "FxMine", "FxOther"):
        (user / name).mkdir(parents=True)
    (user / "MASTER.DBF").write_bytes(b"")
    (user / "FxMine" / "big.ctg").write_bytes(b"x" * 10)
    (user / "FxOther" / "small.ctg").write_bytes(b"x")
    (user / "Example" / "bigger.ctg").write_bytes(b"x" * 100)
    monkeypatch.setattr(corpus, "R2_USER", str(user))
    assert corpus.user_projects() == ["FxMine", "FxOther"]
    assert corpus.biggest_user_file(".ctg") == str(user / "FxMine" / "big.ctg")
