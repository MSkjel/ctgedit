"""Parity batch tests that read the real install."""


def test_shipped_flashing_bitmap_decodes():
    """The R2 Include ICONS.CTL 'handflash_w' is a shipped two-DIB flashing
    bitmap."""
    import glob
    from ctgedit.format.symbols import SymbolLibrary, embedded_bitmaps_from_body
    from ctgedit.format import geometry as G

    hits = glob.glob(r"C:\ProgramData\AVEVA Plant SCADA 2023*\User\Include\ICONS.CTL")
    if not hits:
        import pytest as _pytest

        _pytest.skip("ICONS.CTL not installed")
    lib = SymbolLibrary.open(hits[0])
    names = [n for n in lib.element_names() if n.lower() == "handflash_w"]
    assert names, lib.element_names()[:20]
    pb = lib.element(names[0])
    found = False
    for po in pb.objects:
        if po.type_code == 0x07:
            gh = G.parse_geom_header(po.body_raw)
            primary, flash = embedded_bitmaps_from_body(po.body_raw, gh.end if gh else 0)
            if primary is not None and flash is not None:
                assert (flash.width, flash.height) == (primary.width, primary.height)
                assert flash.rgba != primary.rgba
                found = True
    assert found, "handflash_w did not decode as a two-DIB flashing bitmap"
