"""A nested genie tree in Goto Object and the Objects panel: each genie copy named by the
element it places, and the values each level gets as the Graphics Builder composes them."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _placed(fx_install):
    """FxGenies with fxnest.n4 placed (genies nested two deep): (doc, the genie's index)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxGenies"))
    g = doc.place_library_element(
        "fxnest", "n4", "ctm", (800, 400), params={"Name": "FX_T201", "Extra": "FX_T201 tip"}
    )
    assert g is not None
    return doc, g


def _copies(doc, g):
    """The genie copies nested in placed genie ``g`` (no link of their own)."""
    recs = doc.parsed.page.objects
    return [
        i
        for i in doc.page.subtree(g)[1:]
        if recs[i].class_flag and recs[i].subtype_size & 0x200 and not recs[i].subtype_size & 0x1
    ]


def test_each_nested_genie_copy_is_named_by_its_element(fx_install):
    from ctgedit.ui import names as N

    doc, g = _placed(fx_install)
    copies = _copies(doc, g)
    assert len(copies) >= 2
    deep = [i for i in copies if len([a for a in doc.page.ancestors(i) if a in copies]) >= 1]
    assert deep  # (a copy nested in a copy: named through two elements)
    for i in copies:
        o = doc.page.objects[i]
        assert o.raw.get("_def_ref", "").lower().startswith("fxnest."), (i, o.raw.get("_def_ref"))
        assert N.element_label(o, doc.page, short=False).lower().startswith("fxnest.")
    # the tree of the deepest copy: the placed genie, then each copy down to it
    i = deep[-1]
    chain = N.genie_chain(doc.page, i)
    assert chain[0] == g and chain[-1] == i and len(chain) >= 3


def test_a_copy_gets_the_values_composed_from_the_outer_genie(fx_install):
    """The values a nested copy gets come from its templates and the outer genie, not from
    the values the copy stores."""
    from ctgedit.format import genie_place as GP

    doc, g = _placed(fx_install)
    outer = dict(doc.genie_values(g))
    assert outer["Name"] == "FX_T201"
    copies = _copies(doc, g)
    recs = doc.parsed.page.objects
    composed = {i: dict(doc.genie_values(i)) for i in copies}
    assert any("FX_T201" in v for vals in composed.values() for v in vals.values())
    # whatever a copy stores, the values shown are the composed ones
    i = next(
        i
        for i in copies
        if any("FX_T201" in v for v in composed[i].values()) and GP.decode(recs[i]).entries[1][2].pairs
    )
    r = GP.decode(recs[i])
    r.entries[1][1].pairs = [(k, "STALE") for k, _v in r.entries[1][1].pairs]
    recs[i] = GP.to_po(GP.encode(r))
    doc.edit_serial = getattr(doc, "edit_serial", 0) + 1
    assert dict(doc.genie_values(i)) == composed[i]


def test_goto_object_names_the_copies_and_shows_the_values(fx_install):
    from qtkit import app as _app

    _app()
    from ctgedit.ui.dialog.dialogs import GotoObjectDialog

    doc, g = _placed(fx_install)
    i = _copies(doc, g)[-1]
    dlg = GotoObjectDialog(doc)
    try:
        el = doc.page.objects[i].raw["_def_ref"].split(".<")[0]
        assert el.lower() in dlg._items[i].text(0).lower()
        tip = dlg._items[i].toolTip(0)
        assert "Name = FX_T201" in tip and el.lower() in tip.lower()
        dlg.show_column("element")
        dlg.show_column("parameters")
        assert dlg.column_text(i, "element").lower() == el.lower()
        assert "Name = FX_T201" in dlg.column_text(g, "parameters")
    finally:
        dlg.done(0)


def test_the_objects_panel_names_the_copies_and_shows_the_tree(fx_install):
    from qtkit import app as _app

    _app()
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    doc, g = _placed(fx_install)
    i = _copies(doc, g)[-1]
    dock = ObjectsDock()
    dock.genie_values = doc.genie_values
    dock.set_page(doc.page, [])
    dock._finish_rows()
    el = doc.page.objects[i].raw["_def_ref"].split(".<")[0].split(".", 1)[1]
    look = dock._looks[i]
    assert el.lower() in look.text.lower()
    tip = dock.tip_for(i, "row")
    assert "Placed by, the outermost first:" in tip and "Name = FX_T201" in tip
    dock.set_columns(["object", "parameters"])
    assert "Name = FX_T201" in dock._column_value(
        doc.page, g, doc.page.objects[g], "parameters", dock._genie_values
    )


def _member_texts(doc, i):
    """The texts of the objects directly in genie ``i`` (not in a genie nested in it)."""
    return [
        doc.page.objects[j].style.text
        for j in doc.page.objects[i].members
        if doc.page.objects[j].kind.value == "text"
    ]


def test_each_level_of_the_tree_gets_its_values_from_the_level_above(fx_install):
    """fxnest: n4 places n3 (Name = %Name%, Extra = E%Extra%), n3 places n2 (Name = %Name% 3),
    n2 places n1 (Name = %Name%), n1 places n0 (Label = %Name% L1). Each copy's values are
    worked out from its parent's, and its own objects show them."""
    doc, g = _placed(fx_install)
    want = {
        "n3": {"Name": "FX_T201", "Extra": "EFX_T201 tip"},
        "n2": {"Name": "FX_T201 3"},
        "n1": {"Name": "FX_T201 3"},
        "n0": {"Label": "FX_T201 3 L1"},
    }
    seen = set()
    for i in _copies(doc, g):
        el = doc.page.objects[i].raw["_def_ref"].split(".<")[0].split(".", 1)[1].lower()
        got = dict(doc.genie_values(i))
        for k, v in want[el].items():
            assert got[k] == v, (el, k, got)
        seen.add(el)
    assert seen == set(want)
    # (the texts each level draws follow: n3's '%Extra%', n1's '%Name%', n0's '%Label%')
    texts = {t for i in _copies(doc, g) for t in _member_texts(doc, i)}
    assert {"EFX_T201 tip", "FX_T201 3", "FX_T201 3 L1"} <= texts


def test_the_inputs_say_which_fields_each_value_goes_into(fx_install):
    doc, g = _placed(fx_install)
    rows = {name: (value, uses) for name, value, uses in doc.genie_inputs(g)}
    assert rows["Name"][0] == "FX_T201" and rows["Extra"][0] == "FX_T201 tip"
    # Name reaches n1's text and n0's text, through the copies between
    name_uses = rows["Name"][1]
    assert sorted(u.value for u in name_uses) == ["FX_T201 3", "FX_T201 3 L1"]
    assert all(u.label.startswith("Text: ") for u in name_uses)
    copies = set(_copies(doc, g))
    assert all(u.via in copies and u.index in doc.page.objects[u.via].members for u in name_uses)
    (extra,) = rows["Extra"][1]
    assert extra.value == "EFX_T201 tip"
    # a nested copy's inputs: in terms of its own parameters
    n0 = next(i for i in copies if doc.page.objects[i].raw["_def_ref"].lower().startswith("fxnest.n0"))
    ((name, value, uses),) = [r for r in doc.genie_inputs(n0) if r[0] == "Label"]
    assert value == "FX_T201 3 L1" and [u.via for u in uses] == [n0]


def test_the_properties_panel_lists_the_inputs_and_selects_a_use(fx_install):
    from qtkit import app as _app

    _app()
    from ctgedit.ui.panels.properties import PropertyPanel
    from ctgedit.ui.panels.properties_inputs import InputsTree

    doc, g = _placed(fx_install)
    p = PropertyPanel()
    try:
        p.set_target(doc, [g])
        (tree,) = p.findChildren(InputsTree)
        tops = {tree.topLevelItem(k).text(0): tree.topLevelItem(k) for k in range(tree.topLevelItemCount())}
        assert tops["Name"].text(1) == "FX_T201"
        top = tops["Name"]
        rows = [top.child(k) for k in range(top.childCount())]
        assert any("FX_T201 3 L1" in r.text(0) for r in rows)
        chosen = []
        p.selectObjects.connect(chosen.append)
        tree.itemActivated.emit(rows[0], 0)
        uses = dict((n, u) for n, _v, u in doc.genie_inputs(g))["Name"]
        assert chosen and set(chosen[0]) <= {u.index for u in uses}
    finally:
        p.deleteLater()
