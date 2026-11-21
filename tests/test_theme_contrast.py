"""Theme colours pass WCAG contrast."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from ctgedit.ui.style import theme as T  # noqa: E402
from qtkit import app as _app

TEXT_ROLES = ("text", "head", "label", "text_dim", "danger", "warn", "accent")
SURFACES = ("bg", "panel", "hover", "card")


def _schemes():
    _app()
    out = []
    for scheme in ("dark", "light"):
        for accent in list(T.ACCENTS) + ["#ffcc00", "#202020", "#00ff00"]:
            out.append((scheme, accent, T._scheme_colours(scheme, accent)))
    return out


def _fails(c, fgs, bgs, ratio):
    bad = []
    for fg in fgs:
        for bg in bgs:
            r = T.contrast(c[fg], c[bg])
            if r < ratio:
                bad.append("%s on %s: %.2f" % (fg, bg, r))
    return bad


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_text_roles_pass_on_every_surface(scheme):
    c = T._scheme_colours(scheme)
    assert not _fails(c, TEXT_ROLES, SURFACES, 4.5)


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_selection_text_and_its_second_line(scheme):
    c = T._scheme_colours(scheme)
    assert not _fails(c, ("sel_text", "sel_text_dim"), ("selection",), 4.5)
    assert not _fails(c, ("sel_text",), ("selection_hover",), 4.5)


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_control_edges_and_scroll_handles_are_visible(scheme):
    c = T._scheme_colours(scheme)
    assert not _fails(c, ("control_edge", "scroll_handle"), ("bg", "panel"), 3.0)
    assert T.contrast(c["control_edge"], c["bg"]) > T.contrast(c["border"], c["bg"]) + 1


def test_the_retuned_values():
    assert T.DARK["text_dim"] == "#969ca5"
    assert T.LIGHT["caution"] == "#9a4c00"
    assert T.contrast(T.LIGHT["caution"], T.LIGHT["caution_bg"]) >= 4.5
    assert T.LIGHT["bg"] == "#eef0f3" and T.LIGHT["desk"] == "#d4d7dc"
    assert T.LIGHT["selection"] == "#cfe0f7" and T.LIGHT["on_selection"] == "#0b1f3a"
    assert T._scheme_colours("light")["sel_text"] == "#0b1f3a"


def test_every_accent_and_scheme_passes():
    for scheme, accent, c in _schemes():
        tag = (scheme, accent)
        assert not _fails(c, ("accent",), SURFACES, 4.5), tag
        assert not _fails(c, ("sel_text", "sel_text_dim"), ("selection",), 4.5), tag
        assert T.contrast(c["on_accent"], c["accent"]) >= 3.0, tag


def test_the_blue_preset_is_the_authored_table():
    for scheme, table in (("dark", T.DARK), ("light", T.LIGHT)):
        c = T._scheme_colours(scheme, "blue")
        for k, v in table.items():
            assert c[k] == v, (scheme, k)


def test_light_values_never_collide_with_dark_design_colours():
    bad = []
    for role, value in T.LIGHT.items():
        if role in T._UNMAPPED:
            continue
        other = T._DESIGN_ROLE.get(value.lower())
        if other is not None and other != role:
            bad.append("%s %s is dark %s" % (role, value, other))
        assert value.lower() not in ("#ffffff", "#000000"), role
    assert not bad, bad
    # each mapped light value names one role
    seen = {}
    for role, value in T.LIGHT.items():
        if role in T._UNMAPPED:
            continue
        assert seen.setdefault(value.lower(), role) == role, (role, value)


def test_fit_contrast_and_helpers():
    assert T.contrast("#ffffff", "#000000") == pytest.approx(21.0)
    assert T.mix("#ffffff", "#000000", 0.5) in ("#808080", "#7f7f7f")
    dark_surfaces = [T.DARK[k] for k in SURFACES]
    fitted = T.fit_contrast("#203040", dark_surfaces, 4.5, lighter=True)
    assert all(T.contrast(fitted, s) >= 4.5 for s in dark_surfaces)
    assert T.fit_contrast("#4da3ff", dark_surfaces) == "#4da3ff"
    assert T.readable_on("#4da3ff", ("#ffffff", "#17191c"), 3.0) == "#17191c"
