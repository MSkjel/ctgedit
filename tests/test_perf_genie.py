"""Genie speed-ups change no output."""

import os
import shutil
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

#: the fixture paths of the current test (the ``_fx`` fixture fills them)
P = types.SimpleNamespace()
#: the tests that build an install of their own (no fx_install)
_OWN_FOLDERS = {"test_an_element_nesting_itself_ends_its_operation"}


@pytest.fixture(autouse=True)
def _fx(request):
    """The fake install's pages and libraries as P.LARGE (FxLarge), P.GENIES (FxGenies),
    P.SYMBOLS (FxSymbols), P.FXGENIE (fxgenie.ctm), P.FXPARTS (fxparts.ctm), P.NEST
    (fxnest.ctm), P.LIB (the FxLib folder)."""
    if request.node.originalname in _OWN_FOLDERS:
        yield
        return
    fx = request.getfixturevalue("fx_install")
    P.LARGE = fx.page("FxLarge", "FxPerf")
    P.GENIES = fx.page("FxGenies")
    P.SYMBOLS = fx.page("FxSymbols")
    P.FXGENIE = fx.file("FxLib", "fxgenie.ctm")
    P.FXPARTS = fx.file("FxLib", "fxparts.ctm")
    P.NEST = fx.file("FxLib", "fxnest.ctm")
    P.LIB = fx.project("FxLib")
    yield
    P.__dict__.clear()


def _fp(el):
    """Everything a caller reads of a loaded Element."""
    from ctgedit.format import writer

    nt = getattr(el, "nested_twins", None)
    return (
        el.lib_path,
        el.library,
        el.element,
        el.ext,
        tuple(el.e),
        tuple(el.N),
        el.identity,
        [writer.object_payload(po) for po in el.records],
        None if nt is None else sorted((k, writer.object_payload(v)) for k, v in nt.items()),
    )


def _fresh(monkeypatch, fn):
    """``fn()`` with the memo off (every load computed afresh)."""
    monkeypatch.setenv("GP_MEMO", "0")
    try:
        return fn()
    finally:
        monkeypatch.delenv("GP_MEMO")


def _genie_elements(path, limit=12):
    from ctgedit.format.symbols import open_library

    return open_library(path).element_names()[:limit]


@pytest.fixture(autouse=True)
def _clean_memo():
    from ctgedit.format import genie_place as GP

    GP.clear_memo()
    yield
    GP.clear_memo()


# --------------------------------------------------------------------------- the memo
def test_memo_reuse_equals_a_fresh_load(monkeypatch):
    from ctgedit.format import genie_place as GP

    for lib in (P.FXGENIE, P.FXPARTS):
        for name in _genie_elements(lib):
            ref = _fp(_fresh(monkeypatch, lambda: GP.load_element(lib, name)))
            a = GP.load_element(lib, name)  # computed (nested loads memoised)
            b = GP.load_element(lib, name)  # reused
            assert _fp(a) == ref and _fp(b) == ref, name
            # a copy per caller: its own records list, the same record objects
            assert a.records is not b.records
            assert all(x is y for x, y in zip(a.records, b.records))


def test_memo_reuse_with_an_explicit_resolver(monkeypatch):
    """A load given the page's resolver and one building its own (the genie
    dialog and the OK) share the entry when the search paths agree -- and give
    what a fresh load with that resolver gives."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format.symbols import SymbolResolver

    name = _genie_elements(P.FXPARTS)[0]
    ref = _fresh(
        monkeypatch, lambda: GP.load_element(P.FXPARTS, name, resolver=SymbolResolver.for_page(P.FXPARTS))
    )
    a = GP.load_element(P.FXPARTS, name, resolver=SymbolResolver.for_page(P.FXPARTS))
    b = GP.load_element(P.FXPARTS, name)
    assert _fp(a) == _fp(ref) == _fp(b)
    assert all(x is y for x, y in zip(a.records, b.records))


def test_callers_changing_their_copy_do_not_change_the_memo():
    from ctgedit.format import genie_place as GP

    name = _genie_elements(P.FXGENIE)[0]
    a = GP.load_element(P.FXGENIE, name)
    before = _fp(a)
    a.library, a.element, a.lib_path = "x", "y", "z"
    a.records = a.records[:1]
    if hasattr(a, "nested_twins"):
        a.nested_twins.clear()
    assert _fp(GP.load_element(P.FXGENIE, name)) == before


def test_memo_follows_the_library_file(tmp_path):
    """A library rewritten on disk (another version: a new open_library object)
    is loaded again, not served from the memo."""
    from ctgedit.format import genie_place as GP

    lib = tmp_path / "fxgenie.ctm"
    shutil.copyfile(P.FXGENIE, lib)
    name = _genie_elements(P.FXGENIE)[0]
    a = GP.load_element(str(lib), name)
    st = os.stat(lib)
    os.utime(lib, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    b = GP.load_element(str(lib), name)
    assert _fp(a) == _fp(b)
    assert b.__dict__["_gp_canon"] is not a.__dict__["_gp_canon"]
    assert not any(x is y for x, y in zip(a.records, b.records))


def _new_version(path) -> None:
    """The library file written again (a new modification time: a new version)."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


def test_memo_is_cleared_by_a_library_write(tmp_path):
    """ctgedit's own library writes (libraries_changed) empty the memo; a write by anything
    else is seen by the entry's check of its library file's version."""
    from ctgedit import editing as E
    from ctgedit.format import genie_place as GP

    lib = str(tmp_path / os.path.basename(P.FXGENIE))
    shutil.copy2(P.FXGENIE, lib)
    name = _genie_elements(P.FXGENIE)[0]
    a = GP.load_element(lib, name)
    assert GP.load_element(lib, name).__dict__["_gp_canon"] is a.__dict__["_gp_canon"]
    E.libraries_changed()
    b = GP.load_element(lib, name)  # loaded again, not reused
    assert b.__dict__["_gp_canon"] is not a.__dict__["_gp_canon"]
    assert _fp(a) == _fp(b)
    assert GP.load_element(lib, name).__dict__["_gp_canon"] is b.__dict__["_gp_canon"]
    _new_version(lib)
    c = GP.load_element(lib, name)
    assert c.__dict__["_gp_canon"] is not b.__dict__["_gp_canon"]
    assert _fp(c) == _fp(a)


def test_memo_follows_the_tag_formats():
    """What a load's text measures asked the installed tag-format lookup is
    recorded; an entry is reused only while the lookup answers the same (an
    entry made with no lookup installed only while none is)."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format import textmetrics as TM

    def recorded(lookup, ask=()):
        TM.use_tag_formats(lookup)
        op = GP._op_enter()
        try:
            f = GP._frame_push(op, None, None)
            for name in ask:
                assert TM._tag_format(name) == lookup(name)  # a text measure's lookup
            GP._frame_pop(op, f)
        finally:
            GP._op_leave(op)
        assert TM._tag_format is lookup  # restored after the operation
        return f.deps

    def valid(deps, lookup):
        TM.use_tag_formats(lookup)
        op = GP._op_enter()
        try:
            return GP._check(op, deps, None)
        finally:
            GP._op_leave(op)

    old = TM._tag_format
    try:
        a = recorded(lambda n: "#.#" if n == "TAG_A" else None, ["TAG_A", "TAG_B"])
        assert a.calls == {"TAG_A": "#.#", "TAG_B": None} and not a.none_ctx
        assert valid(a, lambda n: {"TAG_A": "#.#"}.get(n)) is True
        assert valid(a, lambda n: "##.##") is False
        assert valid(a, None) is False
        quiet = recorded(lambda n: "x")  # asked nothing
        assert valid(quiet, None) is True and valid(quiet, lambda n: "y") is True
        TM.use_tag_formats(None)
        op = GP._op_enter()
        f = GP._frame_push(op, None, None)
        GP._frame_pop(op, f)
        GP._op_leave(op)
        assert f.deps.none_ctx
        assert valid(f.deps, None) is True and valid(f.deps, lambda n: None) is False
    finally:
        TM.use_tag_formats(old)


def test_memoised_element_peeks_without_loading(tmp_path):
    from ctgedit import editing as E
    from ctgedit.format import genie_place as GP

    lib = str(tmp_path / os.path.basename(P.FXPARTS))
    shutil.copy2(P.FXPARTS, lib)
    name = _genie_elements(P.FXPARTS)[0]
    assert GP.memoised_element(lib, name) is None and not GP._MEMO
    a = GP.load_element(lib, name)
    b = GP.memoised_element(lib, name)
    assert b is not None and _fp(b) == _fp(a)
    _new_version(lib)
    assert GP.memoised_element(lib, name) is None
    GP.load_element(lib, name)
    assert GP.memoised_element(lib, name) is not None
    E.libraries_changed()
    assert GP.memoised_element(lib, name) is None


def test_declared_params_view_equals_the_load(monkeypatch):
    """The genie dialog's rows without the re-composition (declared_params_of)
    = declared_params(load_element), also with the page's resolver."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format.symbols import SymbolResolver

    for lib in (P.FXGENIE, P.FXPARTS, P.NEST):
        for name in _genie_elements(lib, 20):
            for res in (None, SymbolResolver.for_page(P.LARGE)):
                want = _fresh(
                    monkeypatch,
                    lambda: list(GP.declared_params(GP.load_element(lib, name, resolver=res)).items()),
                )
                assert list(GP.declared_params_of(lib, name, res).items()) == want, name
    with pytest.raises(KeyError):
        GP.declared_params_of(P.FXGENIE, "no such element")


def test_resolver_walk_is_what_resolve_walks():
    from ctgedit.format import genie_place as GP
    from ctgedit.format.symbols import SymbolReference, SymbolResolver, reference_from_object

    res = SymbolResolver.for_page(P.LARGE)
    el = GP.load_element(P.FXGENIE, _genie_elements(P.FXGENIE)[0])
    refs = [reference_from_object(po) for po in el.records if po.subtype_size & 0x1]
    refs = [r for r in refs if r is not None] + [SymbolReference("nolib", "x", "ctm", "genie")]
    for ref in refs:
        w = GP._walk(res, ref.library, ref.element, ref.ext)
        got = res.resolve(ref)
        hold = [L for _x, libs, _f in w for L in libs if L.element(ref.element) is not None]
        assert (got.library_path if got else None) == (hold[0].path if hold else None)


# --------------------------------------------------------------------------- internals
def test_shell_records_decode_like_full_ones():
    """A _shell_po (no bag scan) decodes like to_po of the same payload."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format import writer
    from ctgedit.format.page import parse_ctg

    n = 0
    for po in parse_ctg(P.LARGE).page.objects:
        if po.raw_payload is not None:
            continue
        try:
            blob = GP.encode(GP.decode(po))
        except Exception:  # noqa: BLE001
            continue
        shell = GP._shell_po(blob)
        assert writer.object_payload(shell) == blob
        assert GP.encode(GP.decode(shell)) == GP.encode(GP.decode(GP.to_po(blob))) == blob
        n += 1
    assert n > 1000


def test_bag_size_words_round_trip():
    """decode's overshoot (from the raw lengths) and encode's size word (each
    string encoded once) agree with Bag.size(), also for code-page letters."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format.page import parse_ctg

    n = 0
    for po in parse_ctg(P.LARGE).page.objects:
        if not (po.subtype_size & 8) or po.raw_payload is not None:
            continue
        try:
            r = GP.decode(po)
        except Exception:  # noqa: BLE001
            continue
        if n < 50:  # letters that are not ASCII
            r.entries[0][1].pairs.append(("Rør", "€ %d æøå" % n))
        r2 = GP.decode(GP.to_po(GP.encode(r)))
        for e1, e2 in zip(r.entries, r2.entries):
            for b1, b2 in zip(e1, e2):
                assert b1.pairs == b2.pairs and b1.ov == b2.ov and b1.size() == b2.size()
        n += 1
    assert n > 1000


def test_shell_build_gives_the_same_payloads():
    from ctgedit.format import genie_place as GP
    from ctgedit.format import writer
    from ctgedit.format.symbols import SymbolResolver

    res = SymbolResolver.for_page(P.LARGE)
    for name in _genie_elements(P.FXGENIE, 6):
        el = GP.load_element(P.FXGENIE, name)
        hs = list(range(100, 100 + GP.handle_count(el)))
        full = GP.build_instance_records(el, (10.0, 20.0), {}, handles=hs, resolver=res)
        shell = GP.build_instance_records(el, (10.0, 20.0), {}, handles=hs, resolver=res, shells=True)
        assert [writer.object_payload(p) for p in full] == [writer.object_payload(p) for p in shell]


def test_art_twins_memo_equals_fresh(monkeypatch):
    from ctgedit.format import genie_place as GP
    from ctgedit.format.symbols import SymbolResolver

    res = SymbolResolver.for_page(P.LARGE)
    for lib in (P.FXGENIE, P.FXPARTS):
        for name in _genie_elements(lib, 6):
            el = GP.load_element(lib, name)
            ref = _fresh(monkeypatch, lambda: GP.art_twins(el, res))
            assert GP.art_twins(el, res) == ref and GP.art_twins(el, res) == ref


# --------------------------------------------------------------------------- editing
def _genies_of(doc):
    return [i for i, _key in doc.genie_instances()]


def test_genie_ok_is_byte_identical_to_the_unmemoised_recompose(monkeypatch):
    """Three parameter OKs + undo / redo on FxLarge's genies: the page payloads and
    link table after every step equal the memo-off run, and the model equals a
    full derive (the derive check) throughout."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import writer

    def run():
        doc = EditableDocument.open(P.LARGE)
        doc.derive_check = True
        out = []
        for i in _genies_of(doc)[:3]:
            params = doc.genie_all_params(i)
            if not params:
                continue
            k = next(iter(params))
            doc.set_genie_bindings(i, {k: (params[k] or "") + "X"})
            out.append([writer.object_payload(p) for p in doc.parsed.page.objects])
            out.append(doc.parsed.page.link_table_raw)
        doc.undo()
        doc.redo()
        out.append([writer.object_payload(p) for p in doc.parsed.page.objects])
        return out

    ref = _fresh(monkeypatch, run)
    got = run()
    assert len(got) > 3 and got == ref


def test_merge_keeps_the_members_it_leaves_alone():
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP
    from ctgedit.format import writer

    doc = EditableDocument.open(P.LARGE)
    i = _genies_of(doc)[0]
    idx = doc.subtree(i)
    inst = [doc.parsed.page.objects[k] for k in idx]
    new = GP.refresh_instance(inst, doc._resolver(), doc.page.name, set(doc._used_handles()), shells=True)
    full = GP.refresh_instance(inst, doc._resolver(), doc.page.name, set(doc._used_handles()))
    merged = EditableDocument._merge_instance(inst, new, shells=True, keep_stored=True)
    ref = EditableDocument._merge_instance(inst, full)
    assert [writer.object_payload(p) for p in merged] == [writer.object_payload(p) for p in ref]
    assert merged[0] is not inst[0]  # the root is always a new record
    kept = [k for k in range(1, len(inst)) if merged[k] is inst[k]]
    same = [
        k for k in range(1, len(inst)) if writer.object_payload(merged[k]) == writer.object_payload(inst[k])
    ]
    assert kept == same and kept
    # without keep_stored (callers that patch the returned records in place)
    # every record is a new one
    plain = EditableDocument._merge_instance(inst, new, shells=True)
    assert [writer.object_payload(p) for p in plain] == [writer.object_payload(p) for p in ref]
    assert not any(a is b for a, b in zip(plain, inst))


def test_genie_dialog_installs_the_page_tag_formats():
    from ctgedit.editing import EditableDocument
    from ctgedit.format import textmetrics as TM

    doc = EditableDocument.open(P.LARGE)
    TM.use_tag_formats(None)
    doc.genie_declared_params(_genies_of(doc)[0])
    assert TM._tag_format == doc.tag_format


def test_element_window_open_equals_the_reparsed_derive():
    from ctgedit.editing import ElementDocument
    from ctgedit.format import element_session as ES
    from ctgedit.format import writer
    from ctgedit.format.page import _parse_object
    from ctgedit.io.reader import derive_options, derive_page, model_difference

    for name in _genie_elements(P.FXGENIE, 4):
        doc = ElementDocument.open_element(P.FXGENIE, name)
        # (in the element window's derive context, as ElementDocument derives: a string
        # display shows its expression there, fxgenie.fx_value's '@(%Description%)')
        with derive_options(element_window=True):
            full, _d = derive_page(doc.parsed, doc.path)
        full.name = doc.page.name
        full.source_path = doc.page.source_path
        assert not model_difference(doc.page, full), name
        # the session wrapper = the one built from clones of the element records
        pe = ES.open_element(P.FXGENIE, name)
        clones = [_parse_object(0, 0, 0, 0, writer.object_payload(o)) for o in pe.tree]
        assert [writer.object_payload(o) for o in clones] == [writer.object_payload(o) for o in pe.tree]


def test_element_snapshot_without_a_temp_copy(tmp_path, monkeypatch):
    from ctgedit.editing import ElementDocument

    lib = tmp_path / "fxgenie.ctm"
    shutil.copyfile(P.FXGENIE, lib)
    monkeypatch.setenv("CTG_LIB_PATH", P.LIB)
    name = _genie_elements(P.FXGENIE)[0]
    doc = ElementDocument.open_element(str(lib), name)
    fast = doc._snapshot_element()
    real_open = open

    def not_same(path, mode="r", *a, **k):  # force the temp-copy path
        if os.path.abspath(str(path)) == os.path.abspath(str(lib)) and "b" in mode:
            raise OSError("pretend it changed")
        return real_open(path, mode, *a, **k)

    import builtins

    monkeypatch.setattr(builtins, "open", not_same)
    slow = doc._snapshot_element()
    monkeypatch.setattr(builtins, "open", real_open)
    assert fast is not None and slow is not None
    assert _fp(fast) == _fp(slow)


# --------------------------------------------------------------------------- stamps / register
def test_update_pages_stamp_check_matches_the_load():
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP
    from ctgedit.format import links
    from ctgedit.format.symbols import SymbolReference
    from ctgedit.io import updatepages as UP

    doc = EditableDocument.open(P.LARGE)
    res = doc._resolver()
    t = links.parse_link_table(doc.parsed.page.link_table_raw)
    names = [n for _g, n in t[1] if not n.startswith("\\")]
    assert names
    cur = UP._Current(res)
    for n in names:
        base = links.strip_stamp(n)
        lib, _, rest = base.partition(".")
        elem, _, ext = rest.rpartition(".")
        ext = ext.lstrip("<").lower()
        rs = res.resolve(SymbolReference(lib, elem, ext, "genie" if ext == "ctm" else "symbol"))
        want = None
        if rs is not None:
            el = GP.load_element(rs.library_path, elem)
            el.library, el.element = lib, elem
            want = GP.link_table_entry(el)[1]
        got = cur.name(n)
        stale_old = want is not None and want.lower() != n.lower()
        stale_new = got is not None and got.lower() != n.lower()
        assert stale_old == stale_new, n
        assert got == want or (not stale_old and got.lower() == n.lower()), n
        # a stale entry answers the load's own value
        assert cur.name(n[:-1] + ("0" if n[-1] != "0" else "1")) == want


def test_update_pages_checks_share_one_parse(tmp_path):
    from ctgedit.format.symbols import SymbolResolver
    from ctgedit.io import updatepages as UP

    page = str(tmp_path / "FxLarge.ctg")
    shutil.copyfile(P.LARGE, page)
    cur = UP._Current(SymbolResolver.for_page(P.LARGE))
    want = (UP.stale_elements(page, cur), UP.stale_template(page, cur), UP.stale_supergenie(page, cur))
    UP._SHARE_PARSE[0] = True
    try:
        a = UP._parse_page(page)
        assert UP._parse_page(page) is a  # the same bytes: one parse
        got = (UP.stale_elements(page, cur), UP.stale_template(page, cur), UP.stale_supergenie(page, cur))
        with open(page, "ab") as fh:  # another file: parsed again
            fh.write(b"\0")
        assert UP._parse_page(page) is not a
    finally:
        UP._SHARE_PARSE[0] = False
        UP._PARSE_SLOT[0] = None
    assert got == want
    cur.clear()
    assert not cur.cache and not cur._fast


def test_value_bag_walk_equals_decode():
    from ctgedit.format import genie_place as GP
    from ctgedit.format.page import parse_ctg
    from ctgedit.io import pgregister as PR

    for page in (P.LARGE, P.GENIES):
        for po in parse_ctg(page).page.objects:
            if not (po.subtype_size & 0x8):
                continue
            try:
                r = GP.decode(po)
                want = list(r.entries[0][1].pairs) if r.entries else []
            except Exception:  # noqa: BLE001
                want = []
            assert PR._value_bag(po) == want


def test_page_rows_twice_are_the_same_rows():
    from ctgedit.format.page import parse_ctg
    from ctgedit.io import pgregister as PR

    pc = parse_ctg(P.LARGE)
    a = PR.page_rows(pc, "FxLarge")
    b = PR.page_rows(pc, "FxLarge")  # served from the page's value bags
    PR._VALUE_BAGS.clear()
    c = PR.page_rows(pc, "FxLarge")
    assert a == b == c and sum(len(v) for v in a.values()) > 50


def test_an_element_nesting_itself_ends_its_operation(tmp_path, monkeypatch):
    """An element nesting itself ends its operation."""
    import fxinstall
    from ctgedit.format import genie_place as GP
    from ctgedit.format import textmetrics as TM

    fxlib = os.path.join(fxinstall.FX_INSTALL, "User", "FxLib")
    user = tmp_path / "User"
    p1, p2, p3 = (user / n for n in ("P1", "P2", "P3"))
    for p in (p1, p2, p3):
        p.mkdir(parents=True)
    with open(os.path.join(fxlib, "fxparts.ctm"), "rb") as fh:
        data = bytearray(fh.read())
    i = data.find(b"fx_row\x00")
    assert i > 0
    data[i : i + 7] = b"n1\x00\x00\x00\x00\x00"  # a genie that places itself
    (p3 / "fxnest.ctm").write_bytes(bytes(data))
    shutil.copyfile(os.path.join(fxlib, "fxparts.ctm"), p1 / "fxparts.ctm")
    shutil.copyfile(os.path.join(fxlib, "fxnest.ctm"), p2 / "fxnest.ctm")
    lib = str(p1 / "fxparts.ctm")
    base = _fp(GP.load_element(lib, "fx_row"))
    tf = TM._tag_format

    def nested(extra):  # where the limit falls varies
        if extra:
            return nested(extra - 1)
        return GP.load_element(str(p3 / "fxnest.ctm"), "n1")

    for extra in range(12):
        nested(extra)
        assert GP._OP[0] is None and TM._tag_format is tf, extra
    src = bytearray((p2 / "fxnest.ctm").read_bytes())
    j = src.find(b"%Name% L1")
    assert j > 0
    src[j : j + 9] = b"%Name% L2"
    (p1 / "fxnest.ctm").write_bytes(bytes(src))  # a same-named library earlier on the path
    after = _fp(GP.load_element(lib, "fx_row"))
    assert after != base
    assert after == _fp(_fresh(monkeypatch, lambda: GP.load_element(lib, "fx_row")))


def test_tag_lookups_of_other_threads_are_not_recorded():
    """The operation's recording lookup is global: another thread's text
    measure (a page-browser preview) goes straight to the real lookup."""
    import threading
    from ctgedit.format import genie_place as GP
    from ctgedit.format import textmetrics as TM

    old = TM._tag_format
    try:
        TM.use_tag_formats(lambda n: "#.#")
        op = GP._op_enter()
        try:
            f = GP._frame_push(op, None, None)
            got = []
            t = threading.Thread(target=lambda: got.append(TM._tag_format("OTHER")))
            t.start()
            t.join()
            assert got == ["#.#"]
            TM._tag_format("MINE")
            GP._frame_pop(op, f)
        finally:
            GP._op_leave(op)
        assert f.deps.calls == {"MINE": "#.#"}
    finally:
        TM.use_tag_formats(old)


def test_symbol_update_leaves_the_stored_records_alone(tmp_path, monkeypatch):
    """A symbol update leaves the stored records alone."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import writer

    dst = tmp_path / os.path.basename(P.SYMBOLS)
    shutil.copyfile(P.SYMBOLS, dst)
    monkeypatch.setenv("CTG_LIB_PATH", os.path.dirname(P.SYMBOLS))
    doc = EditableDocument.open(str(dst))
    doc.update_symbol_placements()
    doc.save()
    doc = EditableDocument.open(str(dst))
    assert doc.update_symbol_placements() == 0  # (now every placement is current)
    g = next(
        i
        for i, _k, kind in doc.symbol_placements()
        if kind == "group" and len(doc.subtree(i)) > 2 and "VISIBLE" not in doc.object_properties(i)
    )
    assert doc.ensure_property(g, "VISIBLE", "FX_PUMP1")
    idx = doc.subtree(g)
    objs = doc.parsed.page.objects
    for k in idx[1:]:
        objs[k].subtype_size &= ~0x10  # a stored instance out of step
    doc._touch_model(idx)
    doc._rerender_page()
    before = [writer.object_payload(o) for o in doc.parsed.page.objects]
    assert doc.update_symbol_placements() == 1
    after = [writer.object_payload(o) for o in doc.parsed.page.objects]
    assert after != before
    doc.undo()
    assert [writer.object_payload(o) for o in doc.parsed.page.objects] == before
