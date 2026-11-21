"""Pack Libraries against AVEVA's shipped libraries."""

import os

import pytest

try:
    from ctgedit.io.citect_config import live_project_dir

    EX = live_project_dir("Example")
except Exception:  # noqa: BLE001
    EX = None
EX = EX or r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example"


# CtDraw's own pack output (Tools > Pack Libraries and COM ProjectPackLibraries);
# sha256 of the file with the two container stamps (bytes 4..11) zeroed.
_CTDRAW_PACKED = {
    # source (sha256 of the shipped Example file), element deleted first
    ("SMOKE.ctl", "1a3f4655bb0861dfa0ea535aaccab21709d69f7b8f012473c26dbd90652bbde2", "S2"): (
        15685,
        "4168af56d421d49dc117dbb00df3284aafb6d46d780c04d39a6b8c8f96bc1430",
    ),
    ("utilities.ctl", "18f980acdc82088e71451240913d8a11060e62e715ad83046842349963d8e2f6", "paint_01_sml"): (
        32661,
        "5e349f45a26a9c1c087dd162d84685ccc1f06d4f72a144da7d1393601aea65db",
    ),
    ("Schedule.ctm", "aa1383da160029183e6e7d0a37c7a31b43f3796153ffd129838a4249b0b01d59", "Light2"): (
        6844,
        "21bc8ab6e148c82fe68bb13d7d26a42433bca2d0b17b24c46185c6840d57c2e7",
    ),
}


def _masked_sha(data):
    import hashlib

    return hashlib.sha256(data[:4] + b"\0" * 8 + data[12:]).hexdigest()


def _shipped(name, sha):
    import hashlib

    p = os.path.join(EX, name)
    if not os.path.exists(p):
        pytest.skip("%s missing" % name)
    data = open(p, "rb").read()
    if hashlib.sha256(data).hexdigest() != sha:
        pytest.skip("%s is not the shipped version" % name)
    return data


@pytest.mark.parametrize("key", list(_CTDRAW_PACKED), ids=[k[0] for k in _CTDRAW_PACKED])
def test_pack_is_ctdraws_pack_byte_for_byte(tmp_path, key):
    from ctgedit.format import element as E
    from ctgedit.format.projects import packed_library

    name, sha, dead = key
    p = str(tmp_path / name)
    open(p, "wb").write(_shipped(name, sha))
    assert E.delete_library_element(p, dead)
    packed = packed_library(open(p, "rb").read())
    size, want = _CTDRAW_PACKED[key]
    assert (len(packed), _masked_sha(packed)) == (size, want)


def test_directory_growth_moves_blocks_at_close_like_ctdraw(tmp_path):
    """The 9th element of an 8-slot library: CtDraw appends it, and only at
    close moves the block the 13-slot directory overlaps to the end, with
    its extent + 0x200 holding the bytes that followed it."""
    from ctgedit.format import element as E
    from ctgedit.format.projects import _element_content

    src = E.LibraryFile(
        _shipped("utilities.ctl", "18f980acdc82088e71451240913d8a11060e62e715ad83046842349963d8e2f6")
    )
    names = [
        "alarm_01_sml",
        "drwatson_01_sml",
        "logfile_01_sml",
        "kernel_01_sml",
        "paint_01_sml",
        "trend_01_sml",
        "wordpad_01_sml",
        "info_02_sml",
    ]
    eight = E.LibraryFile.new(src.version, src.word2, 1)
    for n in names:
        eight.write_element(n, _element_content(src, src.find(n)))
    lib = str(tmp_path / "eight.ctl")
    open(lib, "wb").write(eight.to_bytes())
    E.store_element(lib, "calc_01_sml", _element_content(src, src.find("calc_01_sml")))
    data = open(lib, "rb").read()
    # CtDraw's own result on the same file:
    assert (len(data), _masked_sha(data)) == (
        22830,
        "2cc9156c448d604c92ad6a37c1ccc4b91b72b8e592445327f83895a8cf6558d5",
    )
    L = E.LibraryFile(data)
    a, d = L.find("alarm_01_sml"), L.find("drwatson_01_sml")
    assert (L.dword_c, a.tag, a.offset, a.size) == (13, 0x0101, 20308, 2522)
    assert data[a.offset + 2010 : a.offset + 2522] == data[d.offset : d.offset + 0x200]
