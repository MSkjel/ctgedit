"""Text positions in element windows after a move or paste."""

import itertools
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ctgedit.format import element as E  # noqa: E402
from ctgedit.format import placement as P  # noqa: E402


@pytest.fixture
def fx(fx_install, monkeypatch):
    """The fake install, each save with a stamp of its own."""
    c = itertools.count(E.ctdraw_timestamp())
    monkeypatch.setattr(E, "ctdraw_timestamp", lambda: next(c))
    return fx_install


def _lib(fx):
    return fx.file("FxLib", "fxnest.ctm")


def _open(fx, name):
    from ctgedit.editing import ElementDocument

    d = ElementDocument.open_element(_lib(fx), name)
    d.derive_check = True  # every derive compared with a full one (CTG_DERIVE_CHECK)
    return d


def _texts(d, among=None):
    return [
        o.style.text
        for k, o in enumerate(d.page.objects)
        if o.kind.name == "TEXT" and (among is None or k in among)
    ]


def _full_texts(d, among=None):
    """The texts a full derive of the window's records as saved shows."""
    from ctgedit.format.page import parse_ctg_bytes
    from ctgedit.io.reader import derive_page, model_difference
    from ctgedit.io.readerparts.common import derive_options

    with derive_options(element_window=True):
        full, _ = derive_page(parse_ctg_bytes(d.serialize()), d.path, records_fresh=True)
    assert model_difference(d.page, full) == ""
    return [
        o.style.text
        for k, o in enumerate(full.objects)
        if o.kind.name == "TEXT" and (among is None or k in among)
    ]


def _own_text(d):
    objs = d.parsed.page.objects
    par = P.forest_parents(objs)
    return next(k for k, o in enumerate(objs) if o.type_code == 0x05 and par[k] is None)


def _copy_root(d, k0=0):
    objs = d.parsed.page.objects
    return next(
        k for k, o in enumerate(objs) if k >= k0 and o.class_flag and (o.subtype_size & 0x201) == 0x201
    )


def _payload_of_n2_placed(fx):
    """A copy of n2 placed on FxShapes with Name 'P' (the oracle's chain6/pasted_copy)."""
    from ctgedit.editing import EditableDocument

    pd = EditableDocument.open(fx.page("FxShapes"))
    i = pd.place_library_element("fxnest", "n2", "ctm", (400, 300), params={"Name": "P"})
    return pd.copy_payload([i])


def test_a_pasted_genie_copy_shows_its_bound_values_at_once(fx):
    """The pasted members show 'P' and 'P L1' as soon as the paste lands, after an undo and
    a redo too; n3's own copy keeps its binding chain."""
    from ctgedit.editing import libraries_changed

    payload = _payload_of_n2_placed(fx)
    d = _open(fx, "n3")
    own = _texts(d)
    assert own == ["%Extra%", "N2", "%Name% 3", "%Name% 3 L1"]
    new = d.paste_payload(payload)
    assert new
    assert _texts(d, new) == ["N2", "P", "P L1"] == _full_texts(d, new)
    assert _texts(d)[:4] == own
    d.undo()
    assert _texts(d) == own
    d.redo()
    assert _texts(d, new) == ["N2", "P", "P L1"]
    d.save()
    libraries_changed()
    again = _open(fx, "n3")
    assert _texts(again) == own + ["N2", "P", "P L1"]


def test_a_copy_pasted_from_another_element_window_shows_its_binding_chain(fx):
    """n3's own copy of n2 pasted into fx_pump: '%Name% 3' and '%Name% 3 L1', as the
    element saved and opened again shows them."""
    from ctgedit.editing import ElementDocument, libraries_changed

    src = _open(fx, "n3")
    dst = ElementDocument.open_element(fx.file("FxLib", "fxgenie.ctm"), "fx_pump")
    dst.derive_check = True
    n = len(dst.parsed.page.objects)
    new = dst.paste_payload(src.copy_payload([_copy_root(src)]))
    assert new and new[0] == n
    assert _texts(dst, new) == ["N2", "%Name% 3", "%Name% 3 L1"] == _full_texts(dst, new)
    dst.save()
    libraries_changed()
    again = ElementDocument.open_element(fx.file("FxLib", "fxgenie.ctm"), "fx_pump")
    assert _texts(again, set(range(n, len(again.page.objects)))) == ["N2", "%Name% 3", "%Name% 3 L1"]


def test_a_copy_moved_up_by_a_delete_keeps_its_binding_chain_after_a_library_change(fx):
    """n1's own text deleted moves its copy of n0 to the front; another genie of the library
    saved, the window derives again (as the editor does after a library save) and the copy's
    text still shows '%Name% L1', not n0's raw '%Label%'."""
    from ctgedit.editing import libraries_changed

    d = _open(fx, "n1")
    assert _texts(d) == ["%Name%", "%Name% L1"]
    d.delete_objects([_own_text(d)])
    assert _texts(d) == ["%Name% L1"]
    other = _open(fx, "n4")
    t = next(k for k, p in enumerate(other.parsed.page.objects) if p.type_code == 0x05)
    other.move_objects([t], 3, 3)
    other.save()
    libraries_changed()
    d._rerender_page()
    assert _texts(d) == ["%Name% L1"] == _full_texts(d)


def test_a_copy_shifted_by_a_deleted_copy_keeps_its_values_through_a_move(fx):
    """With two copies in the window, the first deleted and everything moved, the other
    copy's texts keep their values ('P', not the template '%Name%')."""
    from ctgedit.editing import libraries_changed

    payload = _payload_of_n2_placed(fx)
    d = _open(fx, "n3")
    d.paste_payload(payload)
    d.save()
    libraries_changed()
    d = _open(fx, "n3")
    first = _copy_root(d)
    d.delete_objects([first])
    assert _texts(d) == ["%Extra%", "N2", "P", "P L1"]
    d.move_objects(d.page.roots(), 1, 0)
    assert _texts(d) == ["%Extra%", "N2", "P", "P L1"] == _full_texts(d)
    d.undo()
    assert _texts(d) == ["%Extra%", "N2", "P", "P L1"] == _full_texts(d)


def test_a_text_retyped_as_in_another_window_shows_its_template(fx):
    """Two windows of n1 in one process; in the first the own text is brought to the front
    and retyped '%Name% Z', in the second (the stored order) the same: the second shows
    '%Name% Z', not 'Z' (the value with the unbound level)."""
    a = _open(fx, "n1")
    a.reorder([_own_text(a)], "front")
    a.set_text(_own_text(a), "%Name% Z")
    assert sorted(_texts(a)) == ["%Name% L1", "%Name% Z"]
    b = _open(fx, "n1")
    b.set_text(_own_text(b), "%Name% Z")
    assert _texts(b) == ["%Name% Z", "%Name% L1"] == _full_texts(b)


def test_the_side_follows_the_position_not_the_parse_index(fx):
    """The rule reads the position the derive gives the record it builds: n1's copy text
    (parsed at index 2) shows its binding chain at a nested position, its template at a
    direct one, whatever its index says; another parse (a library element's record drawn
    into a placement) is drawn as on a page."""
    from ctgedit.io.readerparts import common as C
    from ctgedit.io.readerparts import style as S

    tree = E.open_element(_lib(fx), "n1").tree
    k, po = next((k, o) for k, o in enumerate(tree) if o.type_code == 0x05 and k > 1)
    props = po.properties()
    keep = (getattr(C._PAGE_CTX, "record_at", None), getattr(C._PAGE_CTX, "nested_members", None))
    try:
        with C.derive_options(element_window=True):
            C._PAGE_CTX.nested_members = frozenset({po.index})
            C._PAGE_CTX.record_at = (po, po.index + 7)  # a direct member's place
            _out, raw = S._element_window_props(po, props)
            assert raw["TXT_OFF"] == "%Label%"
            C._PAGE_CTX.nested_members = frozenset({po.index + 7})
            _out, raw = S._element_window_props(po, props)
            assert raw["TXT_OFF"] == "%Name% L1"
            C._PAGE_CTX.record_at = (tree[0], po.index + 7)  # another record is built
            assert S._element_window_props(po, props) == (props, None)
    finally:
        C._PAGE_CTX.record_at, C._PAGE_CTX.nested_members = keep


@pytest.mark.parametrize(
    "host, own",
    [("n2", ["N2", "%Name% Q", "%Name% Q L1"]), ("n3", ["%Extra%", "N2", "%Name% Q", "%Name% Q L1"])],
)
def test_a_binding_edited_in_the_window_shows_its_chain_at_once(fx, host, own):
    """The copy's Name bound '%Name% Q' in the window (the genie dialog's OK): the members two
    and three genies deep show the chain at once, as a placement with the same binding does.
    The window's records are what the save writes."""
    from ctgedit.editing import libraries_changed
    from ctgedit.format import writer as W

    d = _open(fx, host)
    root = _copy_root(d)
    assert d.set_genie_bindings(root, {"Name": "%Name% Q"})
    assert _texts(d) == own == _full_texts(d)
    assert W.genie_bindings(d.parsed.page.objects[root]) == {"Name": " Q"}
    held = [W.object_payload(po) for po in d.parsed.page.objects]
    d.undo()
    assert "%Name% Q" not in _texts(d)
    d.redo()
    assert _texts(d) == own
    d.save()
    libraries_changed()
    again = _open(fx, host)
    assert _texts(again) == own
    assert [W.object_payload(po) for po in again.parsed.page.objects] == held
