"""Command glyphs paint at every size."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest  # noqa: E402
from PySide6 import QtCore, QtGui  # noqa: E402
from qtkit import app as _app


def _ink(pm: QtGui.QPixmap) -> int:
    img = pm.toImage()
    return sum(
        1 for y in range(img.height()) for x in range(img.width()) if img.pixelColor(x, y).alpha() > 40
    )


def test_the_command_glyphs_are_registered_in_the_theme():
    from ctgedit.ui.style import glyphs_cmd, theme

    assert len(glyphs_cmd.GLYPHS) >= 70
    for name in glyphs_cmd.GLYPHS:
        assert name in theme._GLYPHS, name
    # the theme's own glyphs win over a command glyph of the same name
    assert theme._GLYPHS["lock"] is not glyphs_cmd.GLYPHS.get("lock")


@pytest.mark.parametrize("size", (16, 20, 32))
@pytest.mark.parametrize("dpr", (1.0, 1.5, 2.0))
def test_every_glyph_paints_at_every_size_and_ratio(size, dpr):
    _app()
    from ctgedit.ui.style import theme

    blank = []
    for name in sorted(theme._GLYPHS):
        pm = theme.icon(name).pixmap(QtCore.QSize(size, size), dpr)
        assert pm.width() == round(size * dpr), name
        if _ink(pm) < max(6, size * dpr * 0.6):
            blank.append(name)
    assert not blank, blank


def test_every_icon_name_the_window_uses_exists():
    from ctgedit.ui.style import glyphs_cmd, theme
    from ctgedit.ui.main_window_base import SPECIAL_TOOLS, TOOLS

    names = set(glyphs_cmd.ACTION_ICONS.values()) | set(glyphs_cmd.V34_ICONS.values())
    for group in glyphs_cmd.LIST_ICONS.values():
        names |= set(group)
    names |= set(SPECIAL_TOOLS.values()) | {ic for _t, _l, ic, _k in TOOLS}
    missing = sorted(n for n in names if n not in theme._GLYPHS)
    assert not missing, missing


def test_the_group_glyph_is_defined_once():
    from ctgedit.ui.style import theme

    src = open(theme.__file__, encoding="utf-8").read()
    assert src.count("def _g_group(") == 1
    assert src.count('"group": _g_group') == 1


def test_the_special_tools_have_their_own_icons():
    _app()
    from ctgedit.ui.style import theme
    from ctgedit.ui.main_window_base import SPECIAL_TOOLS

    four = [SPECIAL_TOOLS[k] for k in ("process_analyst", "scheduler", "db_exchange", "pelco")]
    assert len(set(four) | {SPECIAL_TOOLS["activex"]}) == 5
    imgs = {n: theme.icon(n).pixmap(QtCore.QSize(32, 32)).toImage() for n in four + ["activex"]}
    keys = list(imgs)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            assert imgs[a] != imgs[b], (a, b)


def test_a_disabled_icon_keeps_a_third_of_the_text_colour():
    app = _app()
    from ctgedit.ui.style import theme

    theme.apply_theme(app, "dark", 1.0)
    ic = theme.icon("rotate_cw")
    on = ic.pixmap(QtCore.QSize(20, 20), QtGui.QIcon.Normal).toImage()
    off = ic.pixmap(QtCore.QSize(20, 20), QtGui.QIcon.Disabled).toImage()
    a_on = max(on.pixelColor(x, y).alpha() for x in range(20) for y in range(20))
    a_off = max(off.pixelColor(x, y).alpha() for x in range(20) for y in range(20))
    assert a_on == 255
    assert abs(a_off - 255 * theme.DISABLED_ICON_OPACITY) <= 2
