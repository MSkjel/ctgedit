"""Recomposing nested genie copies after a library save."""

import itertools
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from ctgedit.format import element as E  # noqa: E402
from ctgedit.format import genie_place as GP  # noqa: E402

EMPTY = [[], [], []]


@pytest.fixture
def fx(fx_install, monkeypatch):
    """The fake install, each save with a stamp of its own (two saves in one second read
    as one version)."""
    c = itertools.count(E.ctdraw_timestamp())
    monkeypatch.setattr(E, "ctdraw_timestamp", lambda: next(c))
    return fx_install


def _lib(fx):
    return fx.file("FxLib", "fxnest.ctm")


def _tree(lib, name: str):
    return E.open_element(lib, name).tree


def _levels(po):
    r = GP.decode(po)
    return [[list(b.pairs) for b in lv] for lv in (r.entries or [])]


def _txt(po):
    r = GP.decode(po)
    return dict(r.entries[0][1].pairs).get("TXT_OFF") if r.entries else None


def _tmpl(po):
    r = GP.decode(po)
    return dict(r.entries[0][2].pairs).get("TXT_OFF") if r.entries else None


def _span(recs, element: str):
    """(start, end) of the outermost copy of ``element`` in ``recs``."""
    for k, po in enumerate(recs):
        if k and po.class_flag and (po.subtype_size & 0x201) == 0x201:
            ref = GP.reference_from_object(po)
            if ref.element.lower() == element.lower():
                return k, GP._span_end(recs, k)
    raise AssertionError("no copy of %s" % element)


def _own_text(d) -> int:
    par = GP.P.forest_parents(d.parsed.page.objects)
    return next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05 and par[k] is None)


def _open(lib, name: str):
    from ctgedit.editing import ElementDocument

    return ElementDocument.open_element(lib, name)


def _save(d, **kw) -> None:
    from ctgedit.editing import libraries_changed

    d.save(**kw)
    libraries_changed()


def _gain_text(fx) -> str:
    """n0 gains a text '%Label% X' (saved, nothing after it); the library."""
    lib = _lib(fx)
    d = _open(lib, "n0")
    d.create_object("text", (80, 12, 140, 28), text="%Label% X")
    _save(d)
    return lib


def _gained(recs, tmpl="%Label% X"):
    return [po for po in recs if po.type_code == 0x05 and _tmpl(po) == tmpl]


def _stamp_entries(lib, name):
    from ctgedit.format import links

    pb = E.open_element(lib, name).page
    return [n for _g, n in links.parse_link_table(pb.link_table_raw)[1]] if pb.link_count else []


def _placed(fx, lib_name: str, element: str, params=None):
    """The records of a fresh placement of ``element`` on a page (in memory)."""
    from ctgedit.editing import EditableDocument

    pd = EditableDocument.open(fx.page("FxShapes"))
    n = len(pd.parsed.page.objects)
    pd.place_library_element(lib_name, element, "ctm", (400, 300), params=params)
    return pd.parsed.page.objects[n:]


# ------------------------------------------------- a host saved over a stale genie between
def test_a_host_saved_while_the_genie_between_is_stale_gets_the_record_unbound(fx):
    """n2 saved while n1 is out of date (CtDraw's chain5/stale_host 'stale'): its copy of
    n1's copy gains the text n0 gained, unbound, three levels, the value left-trimmed 'X'
    and n2 keeps quoting n1's stamp."""
    lib = _gain_text(fx)
    before = _stamp_entries(lib, "n2")
    _save(_open(lib, "n2"))
    n2 = _tree(lib, "n2")
    (new,) = _gained(n2)
    assert _levels(new) == [
        [[], [("TXT_OFF", "X")], [("TXT_OFF", "%Label% X")]],
        [[("Label", "\n")], [], []],
        EMPTY,
    ]
    assert _stamp_entries(lib, "n2") == before


def test_once_the_genie_between_is_saved_the_host_binds_the_record(fx):
    """Then n1 and n2 saved (chain5/stale_host 'after'): the record takes its four levels,
    bound as its kept twin ('%Name% L1 X')."""
    lib = _gain_text(fx)
    _save(_open(lib, "n2"))
    _save(_open(lib, "n1"))
    d = _open(lib, "n2")
    assert d.nested_genie_changes() == {("fxnest", "n1")}
    _save(d)
    n2 = _tree(lib, "n2")
    (new,) = _gained(n2)
    twin = next(po for po in n2 if po.type_code == 0x05 and _tmpl(po) == "%Label%")
    assert _txt(new) == "%Name% L1 X"
    assert len(_levels(new)) == 4
    assert _levels(new)[1:] == _levels(twin)[1:]
    assert _levels(new)[1] == [[("Label", "\n")], [("Label", "%Name% L1")], [("Label", "%Name% L1")]]
    assert _levels(new)[2] == [[("Name", "\n")], [], [("Name", "%Name%")]]


def test_the_chain_after_a_genie_left_unsaved_reaches_every_host(fx, tmp_path):
    """n1 open with unsaved changes: the chain from n0 leaves it and saves n2 .. n4, fx_row
    and FxGenies over it. Once n1 is saved, its chain gives every host and the page the
    bound text ('Pump 3 L1 X' on FxGenies, where 'X' stayed), and a placement of n2 shows
    'Host L1 X'."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import libraries_changed
    from ctgedit.format.page import parse_ctg_bytes

    lib = _gain_text(fx)
    ed = tmp_path / "editor.txt"
    ed.write_text("dirty\t%s\tn1\n" % lib, encoding="utf-8")
    r = GW.update_closed_pages(lib, "n0", editor_file=str(ed))
    libraries_changed()
    assert any(s.startswith("fxnest.n1:") for s in r["elements_skipped"])
    assert "fxnest.n2" in r["elements_saved"]
    _save(_open(lib, "n1"))
    GW.update_closed_pages(lib, "n1")
    libraries_changed()
    for host, want in (("n2", "%Name% L1 X"), ("n3", "%Name% 3 L1 X"), ("n4", "%Name% 3 L1 X")):
        assert [_txt(p) for p in _gained(_tree(lib, host))] == [want], host
    parts = fx.file("FxLib", "fxparts.ctm")
    assert [_txt(p) for p in _gained(_tree(parts, "fx_row"))] == ["Row %RowNum% L1 X"]
    with open(fx.page("FxGenies"), "rb") as fh:
        objs = parse_ctg_bytes(fh.read()).page.objects
    assert "Pump 3 L1 X" in [_txt(p) for p in objs if p.type_code == 0x05]
    assert "Host L1 X" in [_txt(p) for p in _placed(fx, "fxnest", "n2", {"Name": "Host"})]


def test_compose_again_repairs_a_host_written_with_the_record_unbound(fx, monkeypatch):
    """A host whose record is unbound while its stamps are current: Edit > Library >
    Compose Nested Genies Again sees the copy differ from a fresh composition and binds it."""
    lib = _gain_text(fx)
    _save(_open(lib, "n2"))
    _save(_open(lib, "n1"))
    with monkeypatch.context() as m:
        # (a merge where a member with fewer levels keeps them)
        m.setattr(GP, "_pad_levels", lambda *a, **k: False, raising=False)
        GP.clear_memo()
        _save(_open(lib, "n2"))
    GP.clear_memo()
    (new,) = _gained(_tree(lib, "n2"))
    assert len(_levels(new)) == 3  # (the state to repair)
    d = _open(lib, "n2")
    assert not d.nested_genie_changes()
    ops, _link = d.nested_update_ops(every=True)
    assert ops
    assert d.update_nested_genies(every=True)
    _save(d)
    (new,) = _gained(_tree(lib, "n2"))
    assert _txt(new) == "%Name% L1 X" and len(_levels(new)) == 4


@pytest.mark.golden
def test_a_bound_copy_without_the_host_level_takes_it_last(fx):
    """A bound copy without the host level takes it last."""
    import fxinstall

    parts = fx.file("FxLib", "fxparts.ctm")
    d = _open(parts, "fx_anlist")
    ops, _link = d.nested_update_ops(every=True)
    assert ops
    assert d.update_nested_genies(every=True)
    _save(d)
    ours = _tree(parts, "fx_anlist")
    k, end = _span(ours, "fx_anconst")
    eng = _tree(fxinstall.oracle("elements", "FxLib/fxparts.ctm", "library.ctm"), "fx_anlist")
    ke, ende = _span(eng, "fx_anconst")
    assert end - k == ende - ke
    for a, b in zip(ours[k:end], eng[ke:ende]):
        la, lb = _levels(a), _levels(b)
        assert [lv[0] for lv in la] == [lv[0] for lv in lb]  # (the declarations, level by level)
        assert la[-1] == lb[-1] == EMPTY  # (the host's level, last)
    assert _levels(ours[k + 1])[1][0] == [("AN", "\n")]
    assert not _open(parts, "fx_anlist").nested_update_ops(every=True)[0]


# ----------------------------------------------------------- Q7: a default that changes
def _set_n0_text(lib, text):
    d = _open(lib, "n0")
    assert d.set_text(_own_text(d), text)
    _save(d)


def test_a_default_a_copy_takes_is_a_binding_in_its_templates_too(fx):
    """n0's '%Label% %Q=5%' (chain5/param_default q5): n1's copy root and text bind Q = 5 in
    their values and templates (the templates lacked it). After the default became 7 (q7) the
    declaration says 7, the binding and the value stay 5, n1 declares Name only, and a
    placement of n1 shows 'P1 L1 5'."""
    lib = _lib(fx)
    for q in ("5", "7"):
        _set_n0_text(lib, "%%Label%% %%Q=%s%%" % q)
        _save(_open(lib, "n1"))
        n1 = _tree(lib, "n1")
        k, _end = _span(n1, "n0")
        root, text = n1[k], n1[k + 1]
        assert _levels(root)[1] == [
            [("Q", q)],
            [("Label", " L1"), ("Q", "5")],
            [("Label", "%Name% L1"), ("Q", "5")],
        ], q
        assert _levels(text)[0][1:] == [
            [("TXT_OFF", "%Name% L1 5")],
            [("TXT_OFF", "%%Label%% %%Q=%s%%" % q)],
        ], q
        assert _levels(text)[1] == [
            [("Label", "\n"), ("Q", q)],
            [("Label", " L1"), ("Q", "5")],
            [("Label", "%Name% L1"), ("Q", "5")],
        ], q
        assert list(GP.declared_params(GP.load_element(lib, "n1"))) == ["Name"]
        placed = _placed(fx, "fxnest", "n1", {"Name": "P1"})
        assert "P1 L1 5" in [_txt(p) for p in placed], q


# ------------------------------------------------- Q4: a genie without parameters in a genie
def _paste_plain(fx):
    lib = _lib(fx)
    d = _open(lib, "n0")
    d.place_library_element("fxgenie", "fx_plain", "ctm", (100, 10))
    _save(d)
    return lib


def test_a_genie_without_parameters_pasted_into_a_genie_takes_the_hosts_level(fx):
    """chain4/param_gained n0_pasted: the copy's root and members carry two levels, the
    second empty; n1's copy of it three; nothing new declared."""
    lib = _paste_plain(fx)
    n0 = _tree(lib, "n0")
    k, end = _span(n0, "fx_plain")
    assert [_levels(po) for po in n0[k:end]] == [[EMPTY, EMPTY]] * 3
    assert list(GP.declared_params(GP.load_element(lib, "n0"))) == ["Label"]
    _save(_open(lib, "n1"))
    n1 = _tree(lib, "n1")
    inner = [po for po in n1 if po.subtype_size == 0x208 and po.class_flag]
    (root,) = inner
    i = n1.index(root)
    assert [_levels(po) for po in n1[i : GP._span_end(n1, i)]] == [[EMPTY] * 3] * 3


def test_a_genie_without_parameters_that_gains_one_gives_its_copies_one_level_more(fx):
    """fx_plain gains '%P%' (chain4/param_gained same): n0's copy root gains an empty placed
    level (three), the old members three, the gained text its own two and an empty one; n0
    declares Label only and n1's copy has four levels on each record,
    n1 declaring Name only."""
    lib = _paste_plain(fx)
    _save(_open(lib, "n1"))
    gen = fx.file("FxLib", "fxgenie.ctm")
    d = _open(gen, "fx_plain")
    d.create_object("text", (0, 25, 40, 41), text="%P%")
    _save(d)
    _save(_open(lib, "n0"))
    n0 = _tree(lib, "n0")
    k, end = _span(n0, "fx_plain")
    copy = n0[k:end]
    assert len(copy) == 4
    assert [_levels(po) for po in copy[:3]] == [[EMPTY] * 3] * 3
    assert _levels(copy[3]) == [[[], [], [("TXT_OFF", "%P%")]], [[("P", "\n")], [], []], EMPTY]
    assert list(GP.declared_params(GP.load_element(lib, "n0"))) == ["Label"]
    _save(_open(lib, "n1"))
    n1 = _tree(lib, "n1")
    (root,) = [po for po in n1 if po.subtype_size == 0x208 and po.class_flag]
    i = n1.index(root)
    inner = n1[i : GP._span_end(n1, i)]
    assert len(inner) == 4 and all(len(_levels(po)) == 4 for po in inner)
    assert _levels(inner[-1])[:2] == [[[], [], [("TXT_OFF", "%P%")]], [[("P", "\n")], [], []]]
    assert list(GP.declared_params(GP.load_element(lib, "n1"))) == ["Name"]


class _TooMany(Exception):
    pass


@pytest.fixture
def bounded(monkeypatch):
    """Element loads counted, and refused past 3000 (a cyclic library's composition stops; the
    test then fails on what it got)."""
    calls = [0]
    real = GP._load_element

    def counting(*a, **k):
        calls[0] += 1
        if calls[0] > 3000:
            raise _TooMany()
        return real(*a, **k)

    monkeypatch.setattr(GP, "_load_element", counting)
    return calls


# --------------------------------------------------------------------- genie cycles
def test_a_genie_cannot_be_placed_into_itself(fx, bounded):
    lib = _lib(fx)
    d = _open(lib, "n0")
    n = len(d.parsed.page.objects)
    with pytest.raises(ValueError, match="cannot be placed in itself"):
        d.place_library_element("fxnest", "n0", "ctm", (150, 40), params={"Label": "S"})
    assert len(d.parsed.page.objects) == n and not d.dirty
    why = d.placement_refusal("fxnest", "n0")
    assert why == "A genie cannot be placed in itself: fxnest.n0 is the genie you are editing."


def test_a_genie_that_holds_the_host_cannot_be_placed_into_it(fx, bounded):
    """n2 holds n1, which holds n0: placed into n0 it would hold itself (CtDraw places it and
    refuses the save). The other way round, and a genie nesting none of them, is fine."""
    lib = _lib(fx)
    d = _open(lib, "n0")
    with pytest.raises(ValueError, match="fxnest.n2 > fxnest.n1 > fxnest.n0"):
        d.place_library_element("fxnest", "n2", "ctm", (150, 40))
    why = d.placement_refusal("fxnest", "n2")
    assert why == (
        "fxnest.n2 cannot be placed in fxnest.n0, because it holds a copy of "
        "fxnest.n0: fxnest.n2 > fxnest.n1 > fxnest.n0."
    )
    d = _open(lib, "n2")
    assert d.placement_refusal("fxnest", "n0") == ""
    assert d.place_library_element("fxnest", "n0", "ctm", (150, 40), params={"Label": "%Name% D"}) is not None
    from ctgedit.editing import EditableDocument

    assert EditableDocument.open(fx.page("FxShapes")).placement_refusal("fxnest", "n0") == ""


def test_the_window_says_why_before_it_asks_for_the_parameters(fx, bounded, monkeypatch):
    """Edit > Paste Genie of n2 in n0's window: the message names the chain, and the
    parameters are not asked for first (nothing is placed)."""
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.dialog import genie_dialogs
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/auto_update_genies", False)
    shown, asked = [], []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(
        genie_dialogs.GeniePasteDialog, "exec", lambda self: asked.append(1) or QtWidgets.QDialog.Rejected
    )
    w = MainWindow()
    try:
        w._open_element(_lib(fx), "n0")
        n = len(w.doc.parsed.page.objects)
        assert w._place_library("fxnest", "n2", "ctm", (150, 40)) is None
        assert shown == [
            "fxnest.n2 cannot be placed in fxnest.n0, because it holds a copy of "
            "fxnest.n0: fxnest.n2 > fxnest.n1 > fxnest.n0."
        ]
        assert not asked and len(w.doc.parsed.page.objects) == n
    finally:
        for e in list(w._docs):
            e["doc"].dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)


def test_a_paste_that_would_make_a_genie_hold_itself_is_refused(fx, bounded):
    """A copy of n2 (placed on a page) pasted into n0's window: refused; into n3 (which holds
    n2 already) it is pasted."""
    from ctgedit.editing import EditableDocument

    pd = EditableDocument.open(fx.page("FxShapes"))
    i = pd.place_library_element("fxnest", "n2", "ctm", (400, 300), params={"Name": "P"})
    payload = pd.copy_payload([i])
    lib = _lib(fx)
    d = _open(lib, "n0")
    n = len(d.parsed.page.objects)
    with pytest.raises(ValueError, match="fxnest.n2 > fxnest.n1 > fxnest.n0"):
        d.paste_payload(payload)
    assert len(d.parsed.page.objects) == n
    assert _open(lib, "n3").paste_payload(payload)


def test_copy_to_library_refuses_to_store_a_genie_in_itself(fx, bounded):
    from ctgedit.editing import EditableDocument

    pd = EditableDocument.open(fx.page("FxShapes"))
    i = pd.place_library_element("fxnest", "n1", "ctm", (400, 300), params={"Name": "P"})
    lib = _lib(fx)
    with open(lib, "rb") as fh:
        before = fh.read()
    with pytest.raises(ValueError, match="cannot be stored as fxnest.n0"):
        pd.copy_to_library([i], lib, "n0")
    with open(lib, "rb") as fh:
        assert fh.read() == before


def _make_cycle(fx, monkeypatch, which: str) -> str:
    """A cyclic library: ``which`` placed into n0 with the refusals
    off (n0 itself, or n2 that holds n0 through n1); the library."""
    from ctgedit.doc.library import LibraryEdits
    from ctgedit.editing import ElementDocument

    lib = _lib(fx)
    with monkeypatch.context() as m:
        m.setattr(LibraryEdits, "placement_refusal", lambda *a, **k: "", raising=False)
        m.setattr(ElementDocument, "_refuse_self_link", lambda *a, **k: None, raising=False)
        d = _open(lib, "n0")
        d.place_library_element(
            "fxnest", which, "ctm", (150, 40), params={"Label": "S"} if which == "n0" else {}
        )
        _save(d)
    GP.clear_memo()
    return lib


@pytest.mark.parametrize(
    "which,chain", [("n0", "fxnest.n0 > fxnest.n0"), ("n2", "fxnest.n0 > fxnest.n2 > fxnest.n1 > fxnest.n0")]
)
def test_a_genie_that_holds_a_copy_of_itself_is_not_saved(fx, monkeypatch, bounded, which, chain):
    """CtDraw refuses that save ('Attempt to save page with a link to itself', the library
    unchanged): so does ctgedit, naming the cycle."""
    lib = _make_cycle(fx, monkeypatch, which)
    with open(lib, "rb") as fh:
        before = fh.read()
    d = _open(lib, "n0")
    with pytest.raises(ValueError) as got:
        d.save(refresh=True)
    assert str(got.value) == (
        "fxnest.n0 holds a copy of itself (%s), so it cannot be saved. "
        "Delete one of the copies in that chain first." % chain
    )
    with open(lib, "rb") as fh:
        assert fh.read() == before
    assert bounded[0] < 3000


def test_a_cyclic_library_composes_in_bounded_time(fx, monkeypatch, bounded):
    """A cyclic library composes in bounded time."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument, libraries_changed

    lib = _make_cycle(fx, monkeypatch, "n2")
    bounded[0] = 0
    el = GP.load_element(lib, "n0", compose=True)
    assert ("fxnest.n0", "fxnest.n2", "fxnest.n1", "fxnest.n0") in el.cycles
    assert len(el.records) == len(_tree(lib, "n0"))  # (the cycle is not unrolled)
    GP.load_element(lib, "n4", compose=True)
    assert list(GP.declared_params_of(lib, "n1")) == ["Name"]
    pd = EditableDocument.open(fx.page("FxShapes"))
    assert pd.place_library_element("fxnest", "n1", "ctm", (400, 300), params={"Name": "Q"}) is not None
    pd = EditableDocument.open(fx.page("FxGenies"))
    ch = pd.genie_changes()
    assert ch and pd.apply_genie_changes(ch)
    r = GW.update_closed_pages(lib, "n1")
    libraries_changed()
    failed = r.get("elements_failed") or []
    assert any(
        s.startswith(
            "fxnest.n2: fxnest.n2 holds a copy of itself (fxnest.n2 > fxnest.n1 > fxnest.n0 > fxnest.n2)"
        )
        for s in failed
    ), failed
    assert bounded[0] < 3000


def test_a_recursion_error_is_never_taken_for_a_failed_load(fx, monkeypatch):
    """A nested load that hits Python's recursion limit is not swallowed as 'could not load
    this copy'."""
    lib = _lib(fx)
    real = GP.load_element

    def deep(lib_path, element, *a, **k):
        if element.lower() == "n0":
            raise RecursionError("maximum recursion depth exceeded")
        return real(lib_path, element, *a, **k)

    GP.clear_memo()
    monkeypatch.setattr(GP, "load_element", deep)
    with pytest.raises(RecursionError):
        GP._load_element(lib, "n1", compose=True)
