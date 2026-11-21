"""The frozen build ships the simulator."""

import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(REPO, "packaging", "ctgedit.spec")


def _spec():
    with open(SPEC, encoding="utf-8") as fh:
        src = fh.read()
    return src, ast.parse(src)


def _assigned(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node.value
    raise AssertionError("%s is not assigned in the spec" % name)


def test_the_spec_ships_only_the_engines_three_files():
    src, tree = _spec()
    files = ast.literal_eval(_assigned(tree, "ENGINE_FILES"))
    assert files == ("ctsim.exe", "CicodeSim.Engine.dll", "ctsim.exe.config")
    # the build folder = the engine's net48 Release output (or CTG_CTSIM_BUILD)
    build = ast.unparse(_assigned(tree, "ENGINE_BUILD"))
    for part in (
        "external",
        "cicode-sim",
        "engine",
        "src",
        "ctsim",
        "bin",
        "Release",
        "net48",
        "CTG_CTSIM_BUILD",
    ):
        assert repr(part) in build
    # they go next to ctgedit.exe (the dist folder), not into the Python archive
    assert 'os.path.join(DISTPATH, "ctgedit")' in src
    # external/ stays excluded from the Python analysis, and no data comes from it
    excludes = ast.literal_eval(
        next(
            kw.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Analysis"
            for kw in node.keywords
            if kw.arg == "excludes"
        )
    )
    assert "external" in excludes
    datas = ast.unparse(_assigned(tree, "datas"))
    assert "external" not in datas and "cicode" not in datas
    # the engine checkout is named only in the ENGINE_BUILD assignment (and the docs)
    code_lines = [ln for ln in src.splitlines() if "external" in ln and not ln.lstrip().startswith("#")]
    assert len([ln for ln in code_lines if "cicode-sim" in ln and '"' in ln]) == 1


def test_the_client_finds_the_bundled_files(tmp_path, monkeypatch):
    from ctgedit.sim import client as C

    app = tmp_path / "ctgedit"
    app.mkdir()
    for f in ("ctsim.exe", "CicodeSim.Engine.dll", "ctsim.exe.config"):
        (app / f).write_bytes(b"")
    monkeypatch.delenv(C.PATH_ENV, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "ctgedit.exe"))
    loc = C.find_engine()
    assert loc.origin == "bundled" and loc.exe == str(app / "ctsim.exe")
    assert os.path.isfile(loc.dll)
