"""One read of each library per composition."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ctgedit.format import element as E  # noqa: E402
from ctgedit.format import fileio  # noqa: E402
from ctgedit.format import writer  # noqa: E402


def _nest(fx):
    return fx.file("FxLib", "fxnest.ctm")


def _parts(fx):
    return fx.file("FxLib", "fxparts.ctm")


def _payload(records) -> bytes:
    return b"".join(writer.object_payload(o) for o in records)


def _read(path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _save_text(lib: str, name: str, text: str) -> bytes:
    """Element ``name``'s own first text becomes ``text``, saved; the library's bytes then."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(lib, name)
    i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    assert d.set_text(i, text)
    d.save()
    libraries_changed()
    return _read(lib)


def _two_versions(lib: str, name: str, base: str):
    """Two saved versions of ``name`` ('<base> FIRST', then '<base> SECOND'): their library
    bytes; the library is left at the first."""
    from ctgedit.editing import libraries_changed

    first = _save_text(lib, name, base + " FIRST")
    second = _save_text(lib, name, base + " SECOND")
    fileio.write_atomic(lib, first)
    libraries_changed()
    return first, second


def _stored(lib: str, name: str) -> bytes:
    return _payload(E.open_element(lib, name).tree)


class _Rewrite:
    """Wraps ``obj.attr``: its first call writes ``data`` to ``path`` first (another process
    saving the library while the composition runs)."""

    def __init__(self, monkeypatch, obj, attr, path, data, static=False):
        self.calls = 0
        real = getattr(obj, attr)

        def hook(*a, **k):
            self.calls += 1
            if self.calls == 1:
                fileio.write_atomic(path, data)
            return real(*a, **k)

        monkeypatch.setattr(obj, attr, staticmethod(hook) if static else hook)


# --------------------------------------------------------------------------- the hold
def test_a_hold_reads_each_library_once(fx_install):
    from ctgedit.format import symbols as S

    lib = _nest(fx_install)
    first, second = _two_versions(lib, "n0", "%Label%")
    res = S.SymbolResolver.for_page(lib)
    cached = res.libraries("fxnest", "ctm")[0]  # (the resolver's own, before)
    with S.held_libraries() as hold:
        a = S.open_library(lib)
        ver = S.library_version(lib)
        fileio.write_atomic(lib, second)  # another process saves it
        assert S.open_library(lib) is a  # the first read, also now
        assert S.library_version(lib) == ver == a.version
        assert res.libraries("fxnest", "ctm")[0] is a
        assert S.SymbolResolver.for_page(lib).libraries("fxnest", "ctm")[0] is a
        with S.held_libraries() as inner:  # (a hold inside one is it)
            assert inner is hold and S.open_library(lib) is a
        assert S.current_hold() is hold
        assert hold.changed() == [a.path]
    assert S.current_hold() is None
    after = S.open_library(lib)
    assert after is not a and after.version != a.version
    assert b"SECOND" in _payload(after.element("n0").objects)
    # outside a hold the resolver keeps what it cached, as before
    assert res.libraries("fxnest", "ctm")[0] is cached


def test_a_hold_is_the_threads_own(fx_install):
    import threading
    from ctgedit.format import symbols as S

    lib = _nest(fx_install)
    first, second = _two_versions(lib, "n0", "%Label%")
    got = {}
    with S.held_libraries():
        a = S.open_library(lib)
        fileio.write_atomic(lib, second)
        t = threading.Thread(target=lambda: got.setdefault("lib", S.open_library(lib)))
        t.start()
        t.join()
    assert got["lib"] is not a and got["lib"].version != a.version


# ------------------------------------------------------------------ the element save
def test_an_element_save_stamps_the_version_its_copies_came_from(fx_install, monkeypatch):
    """fx_row's save composes n1's copy from the first version; n1 is saved again before the
    import table is made. The stored fx_row holds the first version and quotes ITS stamp, so
    it reads out of date against the second."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import genie_norm as GN

    first, second = _two_versions(_nest(fx_install), "n1", "%Name%")
    hook = _Rewrite(monkeypatch, GN, "rebuild_link_table", _nest(fx_install), second)
    row = ElementDocument.open_element(_parts(fx_install), "fx_row")
    assert ("fxnest", "n1") in row.nested_genie_changes()
    row.save()
    monkeypatch.undo()
    assert hook.calls >= 1
    got = _stored(_parts(fx_install), "fx_row")
    assert b"FIRST" in got and b"SECOND" not in got
    again = ElementDocument.open_element(_parts(fx_install), "fx_row")
    assert ("fxnest", "n1") in again.nested_genie_changes()
    # its next save composes the second version, now current
    again.save()
    got = _stored(_parts(fx_install), "fx_row")
    assert b"SECOND" in got and b"FIRST" not in got
    assert not ElementDocument.open_element(_parts(fx_install), "fx_row").nested_genie_changes()


def test_the_stamp_read_reads_no_genie_again(fx_install, monkeypatch):
    """The import table's stamps are the element identities alone (genie_place.
    current_link_entry): rebuild_link_table loads no nested genie again."""
    from ctgedit.format import genie_norm as GN
    from ctgedit.format import genie_place as GP
    from ctgedit.format.symbols import SymbolResolver

    lib = _parts(fx_install)
    pe = E.open_element(lib, "fx_row")
    from ctgedit.format import links

    t = links.parse_link_table(pe.page.link_table_raw)
    loads = []
    real = GP.load_element
    monkeypatch.setattr(GP, "load_element", lambda *a, **k: loads.append(a) or real(*a, **k))
    out = GN.rebuild_link_table(pe.tree, t[1], SymbolResolver.for_page(lib))
    assert not loads
    names = [n for _g, n in out]
    want = GP.current_link_entry(_nest(fx_install), "fxnest", "n1")
    assert any(n.lower() == want[1].lower() for n in names), names


def test_a_save_holds_no_library_while_the_window_runs(fx_install):
    """A save holds no library while the window runs."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import genie_place as GP
    from ctgedit.format import symbols as S

    nest = _nest(fx_install)
    first, second = _two_versions(nest, "n1", "%Name%")
    old = GP.current_link_entry(nest, "fxnest", "n1")
    seen = {}

    def picture(doc):
        fileio.write_atomic(nest, second)  # saved again meanwhile
        seen["hold"] = S.current_hold()
        seen["entry"] = GP.current_link_entry(nest, "fxnest", "n1")
        return None  # (the stored picture stays)

    row = ElementDocument.open_element(_parts(fx_install), "fx_row")
    assert ("fxnest", "n1") in row.nested_genie_changes()
    row.save(thumbnail_for=picture)
    assert "hold" in seen and seen["hold"] is None
    now = GP.current_link_entry(nest, "fxnest", "n1")
    assert now != old and seen["entry"] == now
    got = _stored(_parts(fx_install), "fx_row")
    assert b"FIRST" in got and b"SECOND" not in got
    assert ("fxnest", "n1") in ElementDocument.open_element(
        _parts(fx_install), "fx_row"
    ).nested_genie_changes()


# ------------------------------------------------------------------- a page's update
def test_a_page_update_stamps_the_version_its_instances_came_from(fx_install, fx_page, monkeypatch):
    """A page update stamps the version its instances came from."""
    from ctgedit.editing import EditableDocument

    first, second = _two_versions(_parts(fx_install), "fx_row", "%RowNum%")
    doc = EditableDocument.open(fx_page("FxParts"))
    assert ("fxparts", "fx_row") in doc.genie_changes()
    hook = _Rewrite(
        monkeypatch, EditableDocument, "_instance_in_page_context", _parts(fx_install), second, static=True
    )
    assert doc.update_genie_instances({("fxparts", "fx_row")}) == 3
    monkeypatch.undo()
    assert hook.calls == 3
    got = _payload(doc.parsed.page.objects)
    assert got.count(b"FIRST") >= 3 and b"SECOND" not in got
    assert ("fxparts", "fx_row") in doc.genie_changes()
    # the next update composes the second version
    assert doc.apply_genie_changes(doc.genie_changes()) >= 3
    got = _payload(doc.parsed.page.objects)
    assert got.count(b"SECOND") >= 3 and b"FIRST" not in got
    assert not doc.genie_changes()


def _move_symbol(path: str, element: str) -> bytes:
    from ctgedit.editing import ElementDocument, libraries_changed

    e = ElementDocument.open_element(path, element)
    e.move_objects([list(e.page.roots())[0]], 4, 0)
    e.save()
    libraries_changed()
    return _read(path)


def test_a_symbol_update_stamps_the_version_its_placements_came_from(fx_install, monkeypatch):
    """FxMotion places fxsym.fx_box twice (group roots), composed from the first version;
    fx_box is saved again after the first placement was composed. Both placements hold the
    first version and the page quotes ITS stamp."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import genie_place as GP

    lib = fx_install.file("FxLib", "fxsym.ctl")
    page = fx_install.page("FxMotion")
    first = _move_symbol(lib, "fx_box")
    second = _move_symbol(lib, "fx_box")
    fileio.write_atomic(lib, first)
    libraries_changed()
    ref = EditableDocument.open(page)  # (the first version alone)
    assert ref.update_symbol_placements() >= 2
    want = [writer.object_payload(o) for o in ref.parsed.page.objects]
    pd = EditableDocument.open(page)
    real, calls = GP.refresh_instance, []

    def hook(inst, *a, **k):
        out = real(inst, *a, **k)
        calls.append(1)
        if len(calls) == 1:
            fileio.write_atomic(lib, second)  # saved again meanwhile
        return out

    monkeypatch.setattr(GP, "refresh_instance", hook)
    pd.update_symbol_placements()
    monkeypatch.undo()
    assert len(calls) == 2
    assert [writer.object_payload(o) for o in pd.parsed.page.objects] == want
    assert ("fxsym", "fx_box") in pd.stale_symbol_elements()
    pd.update_symbol_placements()
    assert ("fxsym", "fx_box") not in pd.stale_symbol_elements()


# --------------------------------------------------------------- the worker's answers
def test_the_workers_element_answer_stamps_the_version_its_copies_came_from(fx_install, monkeypatch):
    """An element tab's nested update in the warm worker (genie_worker.compute_element), n1
    saved again while it composes: the answer, applied as the window applies it, leaves the
    tab with the first version under its stamp, out of date against the second."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument
    from ctgedit.format import genie_norm as GN
    from ctgedit.ui.jobs.genie_jobs import apply_answer

    first, second = _two_versions(_nest(fx_install), "n1", "%Name%")
    tab = ElementDocument.open_element(_parts(fx_install), "fx_row")
    hook = _Rewrite(monkeypatch, GN, "rebuild_link_table", _nest(fx_install), second)
    ans = GW.compute_element(tab.nested_update_request(), tab.serialize())
    monkeypatch.undo()
    assert hook.calls >= 1 and ans["ops"] and "link_table" in ans
    assert apply_answer(tab, ans) > 0
    got = _payload(tab.parsed.page.objects)
    assert b"FIRST" in got and b"SECOND" not in got
    assert ("fxnest", "n1") in tab.nested_genie_changes()


def test_the_workers_page_answer_is_not_kept_when_a_library_changed_meanwhile(
    fx_install, fx_page, monkeypatch
):
    """A page's update in the warm worker (genie_worker.compute) while fx_row is saved
    again: the answer quotes the version it composed, and it is not kept under the files'
    key (infra/answer_memo): the key was made from other files than the ones composed."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import answer_memo

    first, second = _two_versions(_parts(fx_install), "fx_row", "%RowNum%")
    path = fx_page("FxParts")
    kept = []
    monkeypatch.setattr(answer_memo, "put", lambda k, v: kept.append(k))
    hook = _Rewrite(
        monkeypatch, EditableDocument, "_instance_in_page_context", _parts(fx_install), second, static=True
    )
    ans = GW.compute(path, _read(path), None)
    assert hook.calls >= 3 and ans["ops"] and "link_table" in ans
    assert not kept
    monkeypatch.undo()
    from ctgedit.ui.jobs.genie_jobs import apply_answer

    doc = EditableDocument.open(path)
    assert apply_answer(doc, ans) > 0
    assert ("fxparts", "fx_row") in doc.genie_changes()
    # (with nothing written meanwhile the answer is kept, as before)
    kept.clear()
    monkeypatch.setattr(answer_memo, "put", lambda k, v: kept.append(k))
    GW.compute(path, _read(path), None)
    assert kept


def test_a_no_change_is_not_remembered_when_a_library_changed_meanwhile(fx_install, fx_page, monkeypatch):
    """apply_genie_changes remembers 'composing these changes changes nothing' under the
    files' key (infra/noop_memo); a library written while it composed makes the key another
    version than the one composed: nothing is remembered."""
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import noop_memo

    doc = EditableDocument.open(fx_page("FxParts"))
    first, second = _two_versions(_parts(fx_install), "fx_row", "%RowNum%")
    # (FxParts composed from the first version: composing it again changes nothing)
    assert doc.apply_genie_changes(doc.genie_changes()) >= 3
    assert not doc.genie_changes()
    remembered = []
    monkeypatch.setattr(noop_memo, "remember", lambda k: remembered.append(k))
    monkeypatch.setattr(noop_memo, "known", lambda k: False)
    changes = {("fxparts", "fx_row"): None}
    hook = _Rewrite(
        monkeypatch, EditableDocument, "_instance_in_page_context", _parts(fx_install), second, static=True
    )
    assert doc.apply_genie_changes(changes) == 0
    assert hook.calls == 3
    assert not remembered
    monkeypatch.undo()
    fileio.write_atomic(_parts(fx_install), first)
    remembered.clear()
    monkeypatch.setattr(noop_memo, "remember", lambda k: remembered.append(k))
    monkeypatch.setattr(noop_memo, "known", lambda k: False)
    assert doc.apply_genie_changes(changes) == 0
    assert remembered  # (nothing written: kept, as before)


# --------------------------------------------------------- two saves and the chain
def test_a_save_during_the_chain_is_composed_by_the_next_run(fx_install, fx_page, monkeypatch):
    """A save during the chain is composed by the next run."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.format import genie_norm as GN

    lib = _nest(fx_install)
    first, second = _two_versions(lib, "n0", "%Label%")
    hook = _Rewrite(monkeypatch, GN, "rebuild_link_table", lib, second)
    res = GW.update_closed_pages(lib, "n0")
    monkeypatch.undo()
    assert hook.calls >= 1
    assert res["elements_saved"][0] == "fxnest.n1"
    assert b"FIRST" in _stored(lib, "n1")
    res2 = GW.update_closed_pages(lib, "n0")
    assert "fxnest.n1" in res2["elements_saved"], res2
    for name in ("n1", "n2", "n3", "n4"):
        got = _stored(lib, name)
        assert b"SECOND" in got and b"FIRST" not in got, name
    got = _stored(_parts(fx_install), "fx_row")
    assert b"SECOND" in got and b"FIRST" not in got
    from ctgedit.format.page import parse_ctg_bytes

    page = _payload(parse_ctg_bytes(_read(fx_page("FxGenies"))).page.objects)
    assert b"SECOND" in page and b"FIRST" not in page
    # everything is current now
    res3 = GW.update_closed_pages(lib, "n0")
    assert not res3["elements_saved"] and not res3["updated"]
