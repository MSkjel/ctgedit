"""Find the User folder the active install's citect.ini points at."""

import os

import pytest

from ctgedit.infra import install
from ctgedit.io import citect_config as cc


@pytest.fixture(autouse=True)
def _real_lookup(monkeypatch):
    """These tests are of the real lookup (CTG_SCADA_ROOT unset), its registry and its
    glob stubbed to tmp_path."""
    monkeypatch.delenv("CTG_SCADA_ROOT", raising=False)


def _make_install(root, user_dir, mtime, with_page=True):
    cfg_dir = os.path.join(root, "Config")
    os.makedirs(cfg_dir, exist_ok=True)
    os.makedirs(os.path.join(user_dir, "FxMain"), exist_ok=True)
    ini = os.path.join(cfg_dir, "citect.ini")
    with open(ini, "w", encoding="latin1") as fh:
        fh.write("[General]\nproject=\n\n[CtEdit]\nUSER=%s\nBackup=\n" % user_dir)
    os.utime(ini, (mtime, mtime))
    if with_page:
        with open(os.path.join(user_dir, "FxMain", "FxShapes.ctg"), "wb") as fh:
            fh.write(b"page")
    return ini, os.path.join(user_dir, "FxMain", "FxShapes.ctg")


def test_live_user_dir_picks_newest_config(tmp_path, monkeypatch):
    r1 = tmp_path / "PS 2023"
    r2 = tmp_path / "PS 2023 R2"
    u1 = str(r1 / "User")
    u2 = str(r2 / "User")
    _make_install(str(r1), u1, mtime=1000)  # older config
    ini2, _p = _make_install(str(r2), u2, mtime=9999)  # newest = active
    monkeypatch.setattr(install, "_registry_ini", lambda: None)  # heuristic
    monkeypatch.setattr(install, "_INI_GLOB", os.path.join(str(tmp_path), "*", "Config", "citect.ini"))
    assert os.path.normcase(cc.live_user_dir()) == os.path.normcase(u2)
    assert os.path.normcase(cc.live_project_dir("FxMain")) == os.path.normcase(os.path.join(u2, "FxMain"))


def test_registry_ini_overrides_mtime_heuristic(tmp_path, monkeypatch):
    """The registry DefaultINIPath is AUTHORITATIVE: even if a sibling
    install has a newer citect.ini, the registry-named install wins."""
    r1 = tmp_path / "PS 2023"
    r2 = tmp_path / "PS 2023 R2"
    u1 = str(r1 / "User")
    u2 = str(r2 / "User")
    ini1, _p1 = _make_install(str(r1), u1, mtime=9999)  # NEWER ini (heuristic would pick)
    _ini2, _p2 = _make_install(str(r2), u2, mtime=1000)  # older, but registry-named
    reg_ini = os.path.join(str(r2), "Config", "citect.ini")
    monkeypatch.setattr(install, "_registry_ini", lambda: reg_ini)
    monkeypatch.setattr(install, "_INI_GLOB", os.path.join(str(tmp_path), "*", "Config", "citect.ini"))
    # registry points at R2 -> R2 wins despite R1's newer mtime
    assert os.path.normcase(cc.live_user_dir()) == os.path.normcase(u2)


def test_no_config_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "_registry_ini", lambda: None)
    monkeypatch.setattr(install, "_INI_GLOB", os.path.join(str(tmp_path), "none", "citect.ini"))
    assert cc.live_user_dir() is None
    assert cc.live_project_dir() is None
