"""A text's automatic 3D depth."""

from ctgedit.format import geometry as G

TEXT = 0x05


def _flags(effect: int, bold: bool = False, depth: int = -1) -> bytes:
    f = bytearray(6)
    f[G.FLAG_EFFECTS] = effect
    f[G.FLAG_ATTRIBS] = 0x10 if bold else 0
    f[G.FLAG_EFFECT_DEPTH] = depth & 0xFF
    return bytes(f)


def test_text_auto_depth_rule():
    assert G.text_auto_depth(48, False) == 2
    assert G.text_auto_depth(48, True) == 3
    assert G.text_auto_depth(24, False) == 1
    assert G.text_auto_depth(24, True) == 1  # 1 + (1 >> 1)
    assert G.text_auto_depth(10, False) == 1  # at least 1
    assert G.text_auto_depth(80, True) == 6  # 4 + 2


def test_effect_depth_and_bounds_of_a_text():
    r = (100, 100, 300, 160)
    assert G.effect_depth(TEXT, r, _flags(1), 48) == 2
    assert G.effect_depth(TEXT, r, _flags(1, bold=True), 48) == 3
    assert G.effect_depth(TEXT, r, _flags(3), 24) == 1
    assert G.effect_depth(TEXT, r, _flags(0), 48) == 0  # no effect
    assert G.effect_depth(TEXT, r, _flags(1, depth=4), 48) == 4  # a stored depth wins
    assert G.effect_depth(TEXT, r, _flags(1)) == 1  # no size known: 1
    assert G.predict_bounds(TEXT, r, _flags(1), 48) == (98, 98, 302, 162)
    assert G.predict_bounds(TEXT, r, _flags(1, bold=True), 48) == (97, 97, 303, 163)
    assert G.predict_bounds(TEXT, r, _flags(3), 24) == (100, 100, 301, 161)
    # a shape keeps the base rule
    assert G.effect_depth(0x0F, (0, 0, 600, 100), _flags(1)[:2] + bytes([1]) + _flags(1)[3:]) >= 1
