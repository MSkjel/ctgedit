"""The container and writer over every Example page."""

import glob
import os

import pytest

from ctgedit.format.container import Container

R2_USER = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"
EXAMPLE = os.path.join(R2_USER, "Example")
SAMPLES = sorted(glob.glob(os.path.join(EXAMPLE, "*.ctg")))
have_samples = pytest.mark.skipif(not SAMPLES, reason="example project not installed")


# ----------------------------------------------------------------- container
@have_samples
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: os.path.basename(p))
def test_container_tiles_to_eof(path):
    data = open(path, "rb").read()
    c = Container.parse(data)
    assert c.version == 9 and c.dword_c == 8
    blocks = sorted(c.blocks, key=lambda b: b.offset)
    assert blocks, "no blocks parsed"
    # contiguous from first block to EOF
    cur = blocks[0].offset
    for b in blocks:
        assert b.offset == cur, f"gap/overlap at {b.name}"
        cur = b.end
    assert cur == len(data), "blocks do not tile to EOF"
    # every page file has page + attributes
    names = {b.name for b in blocks}
    assert "page" in names and "attributes" in names


@have_samples
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: os.path.basename(p))
def test_container_byte_exact_roundtrip(path):
    data = open(path, "rb").read()
    assert Container.parse(data).to_bytes() == data


@have_samples
def test_all_samples_have_two_known_blocks():
    for path in SAMPLES:
        c = Container.parse(open(path, "rb").read())
        tags = sorted(b.tag for b in c.blocks)
        assert tags == [0x0001, 0x0101], f"{os.path.basename(path)}: {tags}"


# ----------------------------------------------------------------- writer
@have_samples
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: os.path.basename(p))
def test_full_reserialize_byte_exact(path):
    """Re-serializing parsed page+attributes structures reproduces the file exactly."""
    from ctgedit.format.page import parse_ctg
    from ctgedit.format.writer import serialize_ctg

    data = open(path, "rb").read()
    p = parse_ctg(path)
    assert serialize_ctg(p.container, p.page, p.attributes) == data
