"""Genie parameter parsing and multi-state value display."""


def _doc(fx_page, name):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(fx_page(name))


def _one(doc, name):
    found = doc.find_objects_by_name(name)
    assert len(found) == 1, (name, found)
    return found[0]


def test_genie_all_params_includes_template_declarations(fx_page):
    """genie_all_params returns EVERY parameter the .ctm template declares (not just the
    instance's overrides), with the instance values overlaid."""
    doc = _doc(fx_page, "FxParts")
    root = _one(doc, "FxManyParams")
    inst = doc.genie_bindings(root)
    allp = doc.genie_all_params(root)
    assert len(allp) > len(inst)  # the template adds the unset parameters
    assert len(allp) == 42  # the full declared list of fx_many
    # the instance's overridden values survive and lead the list
    assert allp.get("Tag") == inst.get("Tag") == "FX_PUMP1"
    assert list(allp)[0] == "Tag"
    assert allp.get("P01") == "first" and allp.get("P41") == "last"
    # an un-overridden parameter is present (blank / default), not missing
    assert "P02" in allp and not allp["P02"]
    assert list(allp)[1:] == ["P%02d" % n for n in range(1, 42)]


def test_template_params_empty_when_library_absent(fx_page, monkeypatch):
    """No template on the path: genie_all_params falls back to the instance bindings (never
    raises)."""
    monkeypatch.setenv("CTG_NO_SYMBOLS", "1")
    doc = _doc(fx_page, "FxParts")
    root = _one(doc, "FxManyParams")
    assert dict(doc.genie_all_params(root)) == doc.genie_bindings(root)


def test_value_state_stack_draws_every_state(fx_page):
    """A multi-state value display (7 gated overlapping captions in one slot: FxSlot0 ..
    FxSlot6, each at the same origin) draws every state, as CtDraw's editor overprints them."""
    doc = _doc(fx_page, "FxTextKinds")
    objs = doc.page.objects
    slots = [objs[_one(doc, "FxSlot%d" % n)] for n in range(7)]
    assert len({(o.geometry.x, o.geometry.y) for o in slots}) == 1  # one slot
    shown = [
        o
        for o in slots
        if o.kind.value == "text"
        and not o.raw.get("_dup_suppressed")
        and not o.raw.get("_non_drawable")
        and not o.children
    ]
    assert len(shown) == 7


def test_link_driven_text_renders_dark_not_green(fx_page):
    """A link-driven genie text renders dark, not green."""
    from ctgedit.io.reader import read_page

    doc = _doc(fx_page, "FxParts")
    root = _one(doc, "FxLinkText1")
    pg = read_page(doc.path)
    label = next(
        i
        for i in pg.subtree(root)
        if pg.objects[i].kind.value == "text" and pg.objects[i].style.text == "Fx step:"
    )
    props = dict(doc.parsed.page.objects[label].raw_properties())
    assert props["COL_MAXON"] == "0x0000ff00,0x0000ff00" and "!*#1410" in props
    assert not doc.parsed.page.objects[label].subtype_size & 0x2  # art-stripped
    assert pg.objects[label].style.text_colour in (None, 0x000000)  # not 0x00FF00
