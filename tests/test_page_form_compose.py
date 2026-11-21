"""A nested genie copy in page form keeps its bound values when recomposed."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from ctgedit.format import genie_place as GP  # noqa: E402
from ctgedit.format import template_place as TP  # noqa: E402


def _texts(objs):
    """{template TXT_OFF: (value TXT_OFF, [each genie level's values])} of the texts."""
    out = {}
    for po in objs:
        if po.type_code != 0x05:
            continue
        r = GP.decode(po)
        if not r.entries:
            continue
        out[dict(r.entries[0][2].pairs).get("TXT_OFF")] = (
            dict(r.entries[0][1].pairs).get("TXT_OFF"),
            [dict(e[1].pairs) for e in r.entries[1:]],
        )
    return out


def _host(fx, kind: str, params):
    """A new element of ``kind`` holding a copy of fxnest.n2 placed with ``params``, saved:
    (library path, element name)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    if kind == "template":
        path, name, kw = fx.file("FxStyle", "fx_alt.ctt"), "nest@-XGA", {"page_size": (1024, 768)}
    elif kind == "supergenie":
        path, name, kw = fx.file("FxLib", "fxsuper.ctm"), "!fx_nest", {"page_size": (400, 300)}
    else:
        path, name, kw = fx.file("FxLib", "fxnest.ctm"), "nplain", {"canvas": (300, 200)}
    d = ElementDocument.new_element(path, name, kind, **kw)
    assert d.place_library_element("fxnest", "n2", "ctm", (100, 100), params=params) is not None
    d.save()
    libraries_changed()
    return path, d.element_ref[1]


@pytest.mark.parametrize("kind", ["template", "supergenie", "genie"])
def test_compose_again_keeps_a_page_form_copys_bound_values(fx_install, kind):
    from ctgedit.editing import ElementDocument

    path, name = _host(fx_install, kind, {"Name": "Bound"})
    doc = ElementDocument.open_element(path, name)
    stored = _texts(doc.parsed.page.objects)
    assert stored["%Name%"] == ("Bound", [{"Name": "Bound"}, {"Name": "Bound"}])
    assert stored["%Label%"] == ("Bound L1", [{"Label": "Bound L1"}, {"Name": "Bound"}, {"Name": "Bound"}])
    # Edit > Library > Compose Nested Genies Again: nothing to change
    doc.update_nested_genies(label="compose nested genies again", every=True)
    assert _texts(doc.parsed.page.objects) == stored


def test_a_template_saved_after_its_nested_genie_changed_keeps_the_bound_values(fx_install):
    from ctgedit.editing import ElementDocument, libraries_changed

    path, name = _host(fx_install, "template", {"Name": "Bound"})
    n0 = ElementDocument.open_element(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    k = next(j for j, o in enumerate(n0.parsed.page.objects) if o.type_code == 0x05)
    assert n0.set_text(k, "%Label% X")
    n0.save()
    libraries_changed()
    doc = ElementDocument.open_element(path, name)
    assert ("fxnest", "n0") in doc.nested_genie_changes()
    assert doc.update_nested_genies() > 0
    doc.save()
    libraries_changed()
    got = _texts(ElementDocument.open_element(path, name).parsed.page.objects)
    assert got["%Label% X"][0] == "Bound L1 X"
    assert got["%Name%"][0] == "Bound"
    # the template holds what a page built from it derives
    pre = TP.compose_prefix(TP.load_template(path, name), link_text="fx_alt.%s.<ctt" % name, fresh=True)
    assert _texts(pre)["%Label% X"][0] == "Bound L1 X"


@pytest.mark.parametrize("kind", ["template", "supergenie", "genie"])
def test_a_copy_placed_unbound_keeps_the_pages_left_trim(fx_install, kind):
    from ctgedit.editing import ElementDocument

    # Name left unbound, and n2 declares no default for it: the placed level holds no value.
    # The paste left-trims the derived ' L1' (as on a page); the save and Compose Again keep it.
    path, name = _host(fx_install, kind, {})
    doc = ElementDocument.open_element(path, name)
    stored = _texts(doc.parsed.page.objects)
    assert stored["%Label%"] == ("L1", [{"Label": " L1"}, {}, {}])
    assert stored["%Name%"] == (None, [{}, {}])
    assert doc.update_nested_genies(label="compose nested genies again", every=True) == 0
    assert _texts(doc.parsed.page.objects) == stored


def test_a_page_built_from_the_template_keeps_the_left_trim(fx_install):
    path, name = _host(fx_install, "template", {})
    pre = TP.compose_prefix(TP.load_template(path, name), link_text="fx_alt.%s.<ctt" % name, fresh=True)
    assert _texts(pre)["%Label%"] == ("L1", [{"Label": " L1"}, {}, {}])


def test_a_genie_templates_copy_placed_unbound_is_derived_as_on_a_page(fx_install):
    """A template whose own records carry genie levels (an element level after e0, as in a
    template library) holds a copy placed with nothing bound: its placed level is
    empty, as an element level is."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import genie_norm as GN

    path = fx_install.file("FxStyle", "fx_alt.ctt")
    d = ElementDocument.new_element(path, "own@-XGA", "template", page_size=(1024, 768))
    d.create_object("text", (0, 0, 60, 16), text="x")
    assert d.place_library_element("fxnest", "n2", "ctm", (100, 100), params={}) is not None
    d.save()
    libraries_changed()
    doc = ElementDocument.open_element(path, d.element_ref[1])
    tree = list(doc.session.pe.tree)
    k = next(
        j
        for j, o in enumerate(tree)
        if j and o.type_code == 0x05 and not GN.nested_roots(tree, GN.parents_of(tree))[j]
    )
    r = GP.decode(tree[k])
    r.entries = [
        [GP.Bag([]), GP.Bag([("TXT_OFF", "x")]), GP.Bag([("TXT_OFF", "%Own%")])],
        [GP.Bag([("Own", "\n")]), GP.Bag([]), GP.Bag([])],
    ]
    tree[k] = GP.to_po(GP.encode(r))
    assert GN.is_genie_element(tree)
    out = GN.normalise_records(
        tree,
        ext="ctt",
        resolver=doc._resolver(),
        lib_path=path,
        e=tuple(doc.session.pe.origin),
        page_like=True,
    )
    got = _texts(out)
    assert got["%Label%"] == ("L1", [{"Label": " L1"}, {}, {}])
    assert got["%Name%"] == (None, [{}, {}])


@pytest.mark.parametrize("kind", ["template", "genie"])
def test_a_page_form_copy_takes_the_new_binding_of_a_genie_nested_in_it(fx_install, kind):
    """n1 binds its n0 with '%Name% L2' now: the page-form copy of n2 takes it, as a page and
    a genie element's copy do."""
    from ctgedit.editing import ElementDocument, libraries_changed

    path, name = _host(fx_install, kind, {"Name": "Bound"})
    lib = fx_install.file("FxLib", "fxnest.ctm")
    n1 = ElementDocument.open_element(lib, "n1")
    root = next(j for j, o in enumerate(n1.parsed.page.objects) if o.class_flag and o.subtype_size & 0x200)
    assert n1.set_genie_bindings(root, {"Label": "%Name% L2"})
    n1.save()
    libraries_changed()
    n2 = ElementDocument.open_element(lib, "n2")
    assert n2.update_nested_genies() > 0
    n2.save()
    libraries_changed()
    doc = ElementDocument.open_element(path, name)
    assert ("fxnest", "n2") in doc.nested_genie_changes()
    assert doc.update_nested_genies() > 0
    doc.save()
    libraries_changed()
    got = _texts(ElementDocument.open_element(path, name).parsed.page.objects)
    assert got["%Label%"] == ("Bound L2", [{"Label": "Bound L2"}, {"Name": "Bound"}, {"Name": "Bound"}])
    n0_level = next(
        GP.decode(o).entries[1]
        for o in ElementDocument.open_element(path, name).parsed.page.objects
        if o.type_code == 0x05
        and GP.decode(o).entries
        and dict(GP.decode(o).entries[0][2].pairs).get("TXT_OFF") == "%Label%"
    )
    assert dict(n0_level[2].pairs) == {"Label": "%Name% L2"}


def test_the_element_form_keeps_the_member_rule(fx_install):
    from ctgedit.editing import ElementDocument

    # a binding that names a parameter: the host is a genie and its copy is in the element
    # form, whose members read the binding through their templates (LIVE n0 .. n4)
    path, name = _host(fx_install, "genie", {"Name": "%Outer%"})
    doc = ElementDocument.open_element(path, name)
    stored = _texts(doc.parsed.page.objects)
    assert stored["%Label%"][0] == "%Outer% L1"
    doc.update_nested_genies(label="compose nested genies again", every=True)
    assert _texts(doc.parsed.page.objects) == stored
