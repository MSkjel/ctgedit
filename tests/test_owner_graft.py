"""Art-stripped genie members take their look from their owner record."""

import dataclasses


def _parse(fx_install, page):
    from ctgedit.format.page import parse_ctg

    path = fx_install.page(page)
    return path, parse_ctg(path)


def _named(parsed, name):
    """The record index of the object named ``name`` (its header / __AN_NAME__)."""
    found = [
        i
        for i, po in enumerate(parsed.page.objects)
        if po.raw_payload is None and po.raw_properties().get("__AN_NAME__") == name
    ]
    assert len(found) == 1, (name, found)
    return found[0]


def _bitmap_genie(parsed):
    """The linked genie FxBitmapGenie (dwFlags 0x201): its element's bitmap slots are
    art-stripped on the page."""
    g = _named(parsed, "FxBitmapGenie")
    po = parsed.page.objects[g]
    assert po.type_code == 0x08 and po.subtype_size & 0x201 == 0x201
    return g


# --------------------------------------------------------------------------- the graft
def test_unaligned_instance_still_grafts_from_its_owners(fx_install):
    """A genie instance with a record its element lacks (here: a duplicated bitmap slot)
    no longer aligns index-for-index; its art-stripped members still take their owners'
    pictures."""
    from ctgedit.format.ctf import subtree_end
    from ctgedit.format.symbols import reference_from_object
    from ctgedit.io import reader

    path, parsed = _parse(fx_install, "FxGenies")
    recs = parsed.page.objects
    g = _bitmap_genie(parsed)
    end = subtree_end(recs, g)
    slots = [
        j
        for j in range(g + 1, end)
        if recs[j].type_code == 0x07 and not recs[j].subtype_size & 0x2 and recs[j].class_flag == 0
    ]
    assert slots
    page0, _d0 = reader.derive_page(parsed, path, records_fresh=True)
    before = [j for j in slots if page0.objects[j].raw.get("_bmp_grafted")]
    assert before  # the aligned instance grafts them
    # duplicate the first slot in place (its parent gains a child)
    k = slots[0]
    parent = next(j for j in range(k - 1, g - 1, -1) if subtree_end(recs, j) > k)
    recs[parent] = dataclasses.replace(recs[parent], class_flag=recs[parent].class_flag + 1)
    recs.insert(k + 1, dataclasses.replace(recs[k]))
    page1, der1 = reader.derive_page(parsed, path, records_fresh=True)
    # the instance no longer aligns with its element
    element = der1.resolver.resolve(reference_from_object(recs[g])).objects
    sub = page1.subtree(g)
    assert len(sub) > len(element) or any(
        recs[j].type_code != element[n].type_code for n, j in enumerate(sub)
    )
    moved = [j + 1 if j > k else j for j in before]
    assert all(page1.objects[j].raw.get("_bmp_grafted") for j in moved)
    assert all(
        page1.objects[j].raw.get("_embedded_bmp") == page0.objects[b].raw.get("_embedded_bmp")
        for j, b in zip(moved, before)
    )


def test_only_a_styled_owner_marks_a_member(fx_install):
    """Only a styled owner marks a member as grafted."""
    from ctgedit.format.ctf import subtree_end
    from ctgedit.io import reader

    path, parsed = _parse(fx_install, "FxGenies")
    recs = parsed.page.objects
    page, der = reader.derive_page(parsed, path, records_fresh=True)
    ctx = reader._GraftCtx(page, recs, der.resolver)
    g = _named(parsed, "FxValue")
    members = list(range(g + 1, subtree_end(recs, g)))
    frame = next(j for j in members if recs[j].type_code == 0x0F and not recs[j].subtype_size & 0x2)
    owner = ctx.twin(frame)
    assert owner is not None and owner.subtype_size & 0x2
    stripped = next(
        po for po in recs if po.type_code == 0x0F and not po.subtype_size & 0x2 and po is not recs[frame]
    )
    o = reader._object_from(recs[frame])
    before = (o.style.line_colour, o.style.line_style, list(o.geometry.nodes or []))
    reader._graft_twin_extras(o, recs[frame], stripped, ctx)
    assert not o.raw.get("_twin_grafted") and not o.raw.get("_twin_style")
    assert (o.style.line_colour, o.style.line_style, list(o.geometry.nodes or [])) == before
    # ...the real (styled) owner still grafts afterwards
    reader._graft_twin_extras(o, recs[frame], owner, ctx)
    assert o.raw.get("_twin_grafted") and o.raw.get("_twin_style")


def test_member_without_an_owner_keeps_the_default_pen(fx_install):
    """In an instance that no longer aligns with its element, a member whose AN names no
    element record has no owner (the .ctF writer gives it CtDraw's default appearance): the
    graft must not read 'no visible line' off the member itself and hide its frame."""
    from ctgedit.format.ctf import subtree_end
    from ctgedit.io import reader

    path, parsed = _parse(fx_install, "FxGenies")
    recs = parsed.page.objects
    g = _bitmap_genie(parsed)
    members = list(range(g + 1, subtree_end(recs, g)))
    page0, _d0 = reader.derive_page(parsed, path, records_fresh=True)
    frame = next(
        j
        for j in members
        if recs[j].type_code == 0x0F
        and not recs[j].subtype_size & 0x2
        and not page0.objects[j].style.filled
        and page0.objects[j].style.line_style == 5
    )
    # the frame names no element record any more, and a duplicated bitmap slot AFTER it breaks
    # the index alignment (as on a page's large row genie)
    recs[frame] = dataclasses.replace(recs[frame], an_str="9999")
    slot = next(
        j
        for j in range(frame + 1, subtree_end(recs, g))
        if recs[j].type_code == 0x07 and not recs[j].subtype_size & 0x2 and recs[j].class_flag == 0
    )
    parent = next(j for j in range(slot - 1, g - 1, -1) if subtree_end(recs, j) > slot)
    recs[parent] = dataclasses.replace(recs[parent], class_flag=recs[parent].class_flag + 1)
    recs.insert(slot + 1, dataclasses.replace(recs[slot]))
    page1, der1 = reader.derive_page(parsed, path, records_fresh=True)
    assert reader._GraftCtx(page1, recs, der1.resolver).twin(frame) is None
    o = page1.objects[frame]
    assert o.style.line_style != 5 and not o.raw.get("_no_outline")


# --------------------------------------------------------------------------- the derive
def test_default_cicode_symbols_show_the_fx_icon(fx_install):
    """FxCicode: the Cicode objects without a symbol, with '<default>' (FxCicodeAngle1) and
    with the mangled '<deAlarm>' (FxCicodeAngle2) draw the class icon; the two with a real
    symbol (fxicons16.page, fxprio.fx_p1) stay drawn."""
    from ctgedit.io import reader
    from ctgedit.model.document import ObjectKind

    path, parsed = _parse(fx_install, "FxCicode")
    page, der = reader.derive_page(parsed, path, records_fresh=True)
    o, recs = page.objects, der.shadow
    seen = real = 0
    for i, po in enumerate(recs):
        if po.type_code != 0x1009:
            continue
        sym = str(po.raw_properties().get("SYMBOL") or "")
        if sym.startswith("<") and sym.endswith(">"):  # '<default>', mangled '<deAlarm>'
            seen += 1
            assert o[i].raw.get("_non_drawable") and o[i].raw.get("_class_icon") == "ADVSHOW"
        elif sym:
            real += 1
            assert o[i].kind is ObjectKind.SYMBOL_SET  # a real symbol stays drawn
    assert (seen, real) == (2, 2)


def test_incremental_derive_matches_a_full_derive(fx_install):
    """CTG_DERIVE_CHECK: a unit re-derived next to reused ones equals a full derive (a unit's
    owner graft reads only its own records and the libraries). FxParts: one root per
    instance of the fxparts genies and its free symbols, 37 in all."""
    from ctgedit.io import reader

    path, parsed = _parse(fx_install, "FxParts")
    reader.set_label_mode("literal")
    try:
        page, der = reader.derive_page(parsed, path, records_fresh=True)
        recs = list(parsed.page.objects)
        roots = page.roots()
        assert len(roots) == 37
        for r in roots:
            p2, d2 = reader.derive_page(parsed, path, previous=der, records_fresh=True, touched={id(recs[r])})
            assert d2.full is False and d2.changed_roots == [r]  # only that unit re-derived
            assert reader.model_difference(p2, page) == ""
    finally:
        reader.set_label_mode("native")


def test_blank_caption_is_not_an_empty_caption(fx_install):
    """case 0 falls back to TXT_ON only when the caption's first byte is 0: an
    empty caption shows the On text, a blank ' ' caption draws as itself (FxAnim's FxOnOff:
    TXTMODE 0, TXT_ON 'Running')."""
    from ctgedit.io import reader

    _path, parsed = _parse(fx_install, "FxAnim")
    tag = parsed.page.objects[_named(parsed, "FxOnOff")]
    on = tag.raw_properties()["TXT_ON"]
    assert on == "Running"
    reader.set_label_mode("literal")  # the editor's label mode
    assert reader._editor_display(tag, "") == on
    assert reader._editor_display(tag, " ") != on


def test_a_duplicated_member_keeps_the_others_owned(fx_install):
    """An instance that no longer aligns (FxValue's text duplicated in place, its root
    gaining a child) still finds an owner for each of its original members (ctf._Owners maps
    by AN, not by index)."""
    from ctgedit.io import reader

    path, parsed = _parse(fx_install, "FxGenies")
    recs = parsed.page.objects
    g = _named(parsed, "FxValue")
    page0, der0 = reader.derive_page(parsed, path, records_fresh=True)
    ctx0 = reader._GraftCtx(page0, recs, der0.resolver)
    kids = page0.subtree(g)[1:]
    owned = [ctx0.twin(j) is not None for j in kids]
    assert all(owned)
    k = kids[0]
    recs[g] = dataclasses.replace(recs[g], class_flag=recs[g].class_flag + 1)
    recs.insert(k + 1, dataclasses.replace(recs[k]))
    page1, der1 = reader.derive_page(parsed, path, records_fresh=True)
    ctx1 = reader._GraftCtx(page1, recs, der1.resolver)
    moved = [j + 1 if j > k else j for j in kids]
    assert all(ctx1.twin(j) is not None for j in moved)
