"""The page capture tool's cache cleanup on the real install."""

from __future__ import annotations

import importlib.util
import os
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def spc():
    spec = importlib.util.spec_from_file_location(
        "sim_page_capture", os.path.join(ROOT, "tools", "sim_page_capture.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_override_cleanup_on_a_copy_of_a_real_cache(spc, tmp_path):
    data = spc.runtime_data_dir()
    if not data:
        pytest.skip("no Plant SCADA Data folder")
    inv = spc.override_inventory(data)
    if not inv:
        pytest.skip("no cache holds an Override element")
    name, tags = sorted(inv.items(), key=lambda kv: -len(kv[1]))[0]
    src = tmp_path / name
    shutil.copyfile(os.path.join(data, name), src)  # never the live file
    raw = src.read_bytes()
    gone = spc.remove_override_elements(str(src), sorted(tags), str(tmp_path / "bak"))
    assert sorted(gone) == sorted(tags)
    assert spc.override_inventory(str(tmp_path)).get(name) is None
    new = src.read_bytes()
    assert len(new) < len(raw)
    # nothing but the removed elements changed: every removed block is gone, the rest kept
    assert new.count(b'<element name="Override">') == raw.count(b'<element name="Override">') - len(tags)
    assert new.count(b"<tag name=") == raw.count(b"<tag name=")
