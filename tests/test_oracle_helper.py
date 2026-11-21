"""fxinstall.oracle: the path to a captured file, or a skip."""

import hashlib
import importlib.util
import json
import os
import sys

import pytest

import fxinstall as FX

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "live", "fixture_oracle.py")


@pytest.fixture
def oracle_dir(tmp_path, monkeypatch):
    """An empty oracle folder and a fixture User folder with one page in it."""
    root = tmp_path / "oracle"
    root.mkdir()
    fx = tmp_path / "FxInstall"
    (fx / "User" / "FxMain").mkdir(parents=True)
    (fx / "User" / "FxMain" / "FxShapes.ctg").write_bytes(b"fx shapes page")
    monkeypatch.setattr(FX, "ORACLE", str(root))
    monkeypatch.setattr(FX, "ORACLE_MANIFEST", str(root / "manifest.json"))
    monkeypatch.setattr(FX, "FX_INSTALL", str(fx))
    monkeypatch.setattr(FX, "_MANIFEST", [])
    return root, fx


def _sha(data):
    return hashlib.sha1(data).hexdigest()


def _capture(root, fx, status="captured", data=b"ctdraw page bytes", **entry):
    """One captured unit pages:FxMain/FxShapes with page.ctg, like the capture writes it."""
    src = (fx / "User" / "FxMain" / "FxShapes.ctg").read_bytes()
    unit = root / "pages" / "FxMain" / "FxShapes"
    unit.mkdir(parents=True, exist_ok=True)
    (unit / "page.ctg").write_bytes(data)
    e = {
        "file": "pages/FxMain/FxShapes/page.ctg",
        "step": "pages",
        "unit": "FxMain/FxShapes",
        "sha1": _sha(data),
        "size": len(data),
        "source": "FxMain/FxShapes.ctg",
        "source_sha1": _sha(src),
    }
    e.update(entry)
    man = {"files": [e], "units": {"pages:FxMain/FxShapes": {"status": status, "note": "fx note"}}}
    (root / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    FX._MANIFEST[:] = []
    return unit / "page.ctg"


def _reason(fn):
    with pytest.raises(pytest.skip.Exception) as e:
        fn()
    return str(e.value)


def test_waiting_until_the_capture_is_there(oracle_dir):
    def call():
        return FX.oracle("pages", "FxMain/FxShapes", "page.ctg")

    assert _reason(call) == "waiting for the oracle capture: pages/FxMain/FxShapes"
    root, fx = oracle_dir
    _capture(root, fx, status="failed")
    why = _reason(call)
    assert why.startswith("waiting for the oracle capture: pages/FxMain/FxShapes")
    assert "failed: fx note" in why
    _capture(root, fx)
    assert _reason(lambda: FX.oracle("pages", "FxMain/FxShapes", "canvas.png")) == (
        "waiting for the oracle capture: pages/FxMain/FxShapes (the capture made no canvas.png)"
    )
    assert _reason(lambda: FX.oracle("pages", "FxMain/FxColours", "page.ctg")) == (
        "waiting for the oracle capture: pages/FxMain/FxColours"
    )


def test_a_captured_file_is_returned(oracle_dir):
    root, fx = oracle_dir
    path = _capture(root, fx)
    assert FX.oracle("pages", "FxMain/FxShapes", "page.ctg") == str(path)
    assert FX.oracle_bytes("pages", "FxMain/FxShapes", "page.ctg") == b"ctdraw page bytes"


def test_a_json_file(oracle_dir):
    root, fx = oracle_dir
    unit = root / "turns" / "T01"
    unit.mkdir(parents=True)
    data = json.dumps({"page": "<FX>/FxMain/FxShapes"}).encode("utf-8")
    (unit / "sequence.json").write_bytes(data)
    man = {
        "files": [{"file": "turns/T01/sequence.json", "step": "turns", "unit": "T01", "sha1": _sha(data)}],
        "units": {"turns:T01": {"status": "captured"}},
    }
    (root / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    assert FX.oracle_json("turns", "T01", "sequence.json") == {"page": "<FX>/FxMain/FxShapes"}


def test_a_changed_fixture_makes_the_capture_stale(oracle_dir):
    root, fx = oracle_dir
    _capture(root, fx)
    (fx / "User" / "FxMain" / "FxShapes.ctg").write_bytes(b"fx shapes page, regenerated")
    why = _reason(lambda: FX.oracle("pages", "FxMain/FxShapes", "page.ctg"))
    assert why.startswith("oracle stale: pages/FxMain/FxShapes/page.ctg")
    assert "--steps pages:FxMain/FxShapes" in why


def test_a_withheld_file_is_a_skip(oracle_dir):
    root, fx = oracle_dir
    _capture(root, fx, withheld="the file holds a path of the install")
    assert _reason(lambda: FX.oracle("pages", "FxMain/FxShapes", "page.ctg")) == (
        "oracle withheld: pages/FxMain/FxShapes/page.ctg (the file holds a path of the install)"
    )


def test_an_edited_oracle_file_fails(oracle_dir):
    root, fx = oracle_dir
    path = _capture(root, fx)
    path.write_bytes(b"edited by hand")
    with pytest.raises(pytest.fail.Exception, match="differs from its manifest sha1"):
        FX.oracle("pages", "FxMain/FxShapes", "page.ctg")


def test_names_are_the_captures():
    """The unit folders are the capture tool's own (its safe_name), the layout
    <step>/<unit parts>/<file>."""
    spec = importlib.util.spec_from_file_location("fixture_oracle", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fixture_oracle"] = mod  # (its dataclasses look their module up)
    try:
        spec.loader.exec_module(mod)
        for name in ("FxMain/!fx_detail", "fxgenie.fx_value", "fx café #2", "a/b c"):
            assert FX.oracle_safe_name(name) == mod.safe_name(name)
        assert os.path.normpath(FX.ORACLE) == os.path.normpath(mod.ORACLE)
    finally:
        sys.modules.pop("fixture_oracle", None)
    assert FX.oracle_rel("pages", "FxMain/!fx detail", "page.ctg") == "pages/FxMain/!fx%20detail/page.ctg"
    assert (
        FX.oracle_rel("pages", "FxMain/FxShapes", "files/FxMain/pgdynobj.DBF")
        == "pages/FxMain/FxShapes/files/FxMain/pgdynobj.DBF"
    )
