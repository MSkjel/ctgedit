"""Multi-language labels."""

import pytest

LANDIC = "Fxlandic(Fxland)"
ISH = "Fxish(Fxland)"


@pytest.fixture
def text_kinds(fx_page):
    return fx_page("FxTextKinds")


@pytest.fixture
def anim(fx_page):
    return fx_page("FxAnim")


def test_language_db_translates(text_kinds):
    from ctgedit.io.lang import LanguageDB

    db = LanguageDB.for_page(text_kinds)
    assert db is not None
    langs = db.languages()
    assert LANDIC in langs and ISH in langs
    # the translations of fxlandic.DBF / fxish.DBF
    assert db.translate("Fx Start", LANDIC) == "Fx Anfang"
    assert db.translate("Fx Stop", LANDIC) == "Fx Halt"
    assert db.translate("Fx Start", ISH) == "Fx Begin"
    # untranslated native string falls back to itself
    assert db.translate("zzz no such string", LANDIC) == "zzz no such string"


def test_reader_label_modes(text_kinds):
    from ctgedit.io import reader

    def caption_for(target, mode):
        reader.set_label_mode(mode)
        pg = reader.read_page(text_kinds)
        for o in pg.objects:
            t = getattr(o.style, "text", "") or ""
            if target in t:
                return t
        return None

    try:
        # literal keeps the marker verbatim (matches CtDraw's editor canvas)
        assert caption_for("Fx Start", "literal") == "@(Fx Start)"
        # native strips to the source string
        assert caption_for("Fx Start", "native") == "Fx Start"
        # a language resolves to its translation
        assert caption_for("Fx Anfang", LANDIC) == "Fx Anfang"
        assert caption_for("Fx Begin", ISH) == "Fx Begin"
    finally:
        reader.set_label_mode("native")


def test_multi_marker_caption(anim):
    """FxAnim's FxLabelText '@(Fx Start) @(Fx Stop) @(Fx Mode)' resolves every marker
    independently in each mode."""
    from ctgedit.io import reader

    def captions(mode):
        reader.set_label_mode(mode)
        return {o.style.text for o in reader.read_page(anim).objects if "Fx Mode" in (o.style.text or "")}

    try:
        lit = captions("literal")
        assert any(s.startswith("@(") and s.count("@(") == 3 for s in lit)
        assert "Fx Start Fx Stop Fx Mode" in captions("native")
        # each marker on its own: two translated, the third falls back to its source
        assert "Fx Anfang Fx Halt Fx Mode" in captions(LANDIC)
    finally:
        reader.set_label_mode("native")
