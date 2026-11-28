"""Two threads deriving pages that share a library element."""

import os
import threading

import pytest


def reader_part(name):
    """The reader part that owns a name (ctgedit/io/readerparts): a patch
    goes there, where the reader's code looks it up."""
    import importlib

    return importlib.import_module("ctgedit.io.readerparts." + name)


#: the page with the art-twin genie, the page on a nesting template, the tag-bound page
TWIN_PAGE, TWIN_ELEMENT = "FxParts", "fx_agitator_mixer"
TEMPLATE_PAGE = "FxTplLinked"
TAG_PAGE = "FxGenies"


def _genie_element(page_path, element):
    """(library path, element) of the first top-level instance of ``element`` on the page, and
    the page's resolver."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.symbols import SymbolResolver, reference_from_object

    doc = EditableDocument.open(page_path)
    res = SymbolResolver.for_page(page_path)
    for i, (_lib, el) in doc.genie_instances():
        if el.lower() == element:
            ref = reference_from_object(doc.parsed.page.objects[i])
            return res.resolve(ref).library_path, ref.element, res
    pytest.fail("no %s on %s" % (element, page_path))


def test_art_twins_while_another_thread_computes_them(monkeypatch, fx_install):
    from ctgedit.io import reader

    path, element, res = _genie_element(fx_install.page(TWIN_PAGE), TWIN_ELEMENT)
    reader.ART_TWINS_CACHE.clear()
    alone = reader.art_twins_light(path, element, res)
    assert alone, "the element has art twins"
    reader.ART_TWINS_CACHE.clear()

    inside, go = threading.Event(), threading.Event()
    real = reader.element_root_records

    def held(p, e):
        # the worker thread stops inside the computation (after the guard is stored)
        if threading.current_thread().name == "preview-parse":
            inside.set()
            go.wait(10)
        return real(p, e)

    monkeypatch.setattr(reader_part("symbols"), "element_root_records", held)
    out = {}
    t = threading.Thread(
        target=lambda: out.update(worker=reader.art_twins_light(path, element, res)), name="preview-parse"
    )
    t.start()
    try:
        assert inside.wait(10)
        # the worker is released soon after, so a call that waits for its result passes too
        threading.Timer(0.5, go.set).start()
        gui = reader.art_twins_light(path, element, res)  # the GUI thread, meanwhile
    finally:
        go.set()
        t.join(10)
    assert out.get("worker") == alone
    assert gui == alone, "the second thread got %r" % (gui if gui is None else len(gui))


def test_art_twins_alone_are_stable(fx_install):
    """Control: sequential derivations agree (the cache itself is right)."""
    from ctgedit.io import reader

    path, element, res = _genie_element(fx_install.page(TWIN_PAGE), TWIN_ELEMENT)
    reader.ART_TWINS_CACHE.clear()
    a = reader.art_twins_light(path, element, res)
    reader.ART_TWINS_CACHE.clear()
    b = reader.art_twins_light(path, element, res)
    assert a and a == b


def _template_and_nested(page_path):
    """(library path, template element, nested element, resolver): the page's template and
    the first linked element nested in it (FxTplLinked: fx_style.normal@XGA around
    fxgenie.fx_value)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.symbols import SymbolResolver, reference_from_object
    from ctgedit.io import reader

    doc = EditableDocument.open(page_path)
    res = SymbolResolver.for_page(page_path)
    for i, o in enumerate(doc.page.objects):
        if o.parent is not None:
            continue
        ref = reference_from_object(doc.parsed.page.objects[i])
        if ref is None or ref.ext != "ctt":
            continue
        path = res.resolve(ref).library_path
        for po in reader.element_root_records(path, ref.element)[1:]:
            if po.class_flag and po.subtype_size & 0x1:
                sub = reference_from_object(po)
                sp = res.resolve(sub).library_path
                return path, ref.element, sp, sub.element, res
    pytest.fail("no template with a nested linked element")


def test_nested_art_twins_computed_meanwhile_are_not_cached_incomplete(monkeypatch, fx_install):
    """Nested art twins computed meanwhile aren't cached incomplete."""
    from ctgedit.io import reader

    path, outer, npath, nested, res = _template_and_nested(fx_install.page(TEMPLATE_PAGE))
    reader.ART_TWINS_CACHE.clear()
    alone = reader.art_twins_light(path, outer, res)
    assert alone
    reader.ART_TWINS_CACHE.clear()

    inside, go = threading.Event(), threading.Event()
    real = reader.element_root_records

    def held(p, e):
        if threading.current_thread().name == "preview-parse" and e == nested:
            inside.set()
            go.wait(10)
        return real(p, e)

    monkeypatch.setattr(reader_part("symbols"), "element_root_records", held)
    t = threading.Thread(target=lambda: reader.art_twins_light(npath, nested, res), name="preview-parse")
    t.start()
    try:
        assert inside.wait(10)
        threading.Timer(0.5, go.set).start()
        reader.art_twins_light(path, outer, res)  # the GUI thread, meanwhile
    finally:
        go.set()
        t.join(10)
    # later, with both threads done: a lone derive of a page using the outer element
    later = reader.art_twins_light(path, outer, res)
    assert later == alone, "the outer element's twins stayed incomplete in the cache"


# -- what the art-twin cache stores: only results that do not depend on how they were reached --
class _Rec:
    """A stand-in element record: ``ref`` = the element a linked container places."""

    def __init__(self, children=0, ref=None):
        self.class_flag, self.subtype_size, self.ref = children, 0x1 if ref else 0, ref


class _Res:
    def __init__(self, folder, paths):
        self.folder, self.search_paths = folder, list(paths)

    def resolve(self, ref):
        from types import SimpleNamespace

        return SimpleNamespace(library_path=os.path.join(self.folder, ref.element + ".ctm"))


@pytest.fixture
def fake_elements(tmp_path, monkeypatch):
    """Elements by name (each backed by a file, for the cache key's stat)."""
    from types import SimpleNamespace
    from ctgedit.io import reader

    elements: dict = {}

    def records(path, element):
        return elements.get(element)

    monkeypatch.setattr(reader_part("symbols"), "element_root_records", records)
    monkeypatch.setattr(
        reader_part("symbols"),
        "reference_from_object",
        lambda po: SimpleNamespace(element=po.ref) if po.ref else None,
    )
    reader.ART_TWINS_CACHE.clear()

    def define(name, recs):
        (tmp_path / (name + ".ctm")).write_bytes(name.encode())
        elements[name] = recs

    yield define, tmp_path
    reader.ART_TWINS_CACHE.clear()


def _twins(folder, name, res):
    from ctgedit.io import reader

    return reader.art_twins_light(os.path.join(folder, name + ".ctm"), name, res)


def test_nested_twins_are_grafted_and_stored(fake_elements):
    from ctgedit.io import reader

    define, folder = fake_elements
    inner = [_Rec(1), _Rec()]
    define("inner", inner)
    define("outer", [_Rec(1), _Rec(1, ref="inner"), _Rec()])
    res = _Res(str(folder), [str(folder)])
    out = _twins(folder, "outer", res)
    assert out[2] is inner[1]  # the nested member's owner
    assert len(reader.ART_TWINS_CACHE) == 2  # both final: stored
    # another search path is another key (a nested reference may resolve elsewhere there)
    _twins(folder, "outer", _Res(str(folder), [str(folder), "elsewhere"]))
    assert len(reader.ART_TWINS_CACHE) == 4


def test_an_element_nesting_itself_is_not_stored(fake_elements):
    """A cycle's results depend on the element it was entered at: none is stored, so the
    session never reuses one reached another way (a fresh editor computes it again)."""
    from ctgedit.io import reader

    define, folder = fake_elements
    define("a", [_Rec(1), _Rec(1, ref="b"), _Rec()])
    define("b", [_Rec(1), _Rec(1, ref="a"), _Rec()])
    res = _Res(str(folder), [str(folder)])
    first = _twins(folder, "a", res)
    assert first is not None and not reader.ART_TWINS_CACHE
    assert _twins(folder, "a", res) == first  # the same from the same entry
    assert not getattr(reader.ART_TWINS_TLS, "stack", [])


def test_nesting_deeper_than_the_limit_is_not_stored(fake_elements):
    from ctgedit.io import reader

    define, folder = fake_elements
    n = reader.ART_TWINS_MAX_DEPTH + 3
    for k in range(n):
        define("e%d" % k, [_Rec(1), _Rec(1, ref="e%d" % (k + 1)), _Rec()] if k < n - 1 else [_Rec(1), _Rec()])
    res = _Res(str(folder), [str(folder)])
    top = _twins(folder, "e0", res)
    assert top is not None
    # every computation on the way down to the cut depended on its depth: none is stored
    assert not reader.ART_TWINS_CACHE
    # asked at the top, the lower part of the chain is complete: stored ...
    _twins(folder, "e5", res)
    stored = {k[1] for k in reader.ART_TWINS_CACHE}
    assert stored == {"e%d" % k for k in range(5, n)}
    # ... but not reused where a computation would stop following it: e0 is as before
    assert _twins(folder, "e0", res) == top
    assert "e0" not in {k[1] for k in reader.ART_TWINS_CACHE}


def test_view_refresh_derives_what_a_fresh_editor_does(fx_install):
    """View > Refresh derives what a fresh editor would."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.io import reader

    prev_mode = reader.get_label_mode()
    reader.set_label_mode("literal")
    try:
        page = fx_install.page(TAG_PAGE)
        fresh = reader.read_page(page)
        doc = EditableDocument.open(page)
        assert not reader.model_difference(doc.page, fresh)
        # what a race / a failed read could leave for the session: every element's twins
        # 'lost' and every tag given a wrong format
        for k in list(reader.ART_TWINS_CACHE):
            reader.ART_TWINS_CACHE[k] = (None, 1)
        for k in list(reader._TAG_FORMAT_CACHE):
            reader._TAG_FORMAT_CACHE[k] = {n: "#X#" for n in reader._TAG_FORMAT_CACHE[k]}
        doc._derivation = None
        doc._rerender_page()
        assert reader.model_difference(doc.page, fresh)
        libraries_changed()
        doc._rerender_page()
        assert not reader.model_difference(doc.page, fresh)
    finally:
        reader.set_label_mode(prev_mode)


def test_derive_options_scope_the_label_mode_and_leave_the_process_setting():
    """reader.derive_options overrides the label mode / composite drawing inside its block
    only."""
    from ctgedit.io import reader

    before = (reader.get_label_mode(), reader.get_composite_members())
    with reader.derive_options(label_mode="literal", composite_members=False):
        assert reader.get_label_mode() == "literal"
        assert reader.get_composite_members() is False
        with reader.derive_options(label_mode="German"):
            assert reader.get_label_mode() == "German"
            assert reader.get_composite_members() is False
        assert reader.get_label_mode() == "literal"
    assert (reader.get_label_mode(), reader.get_composite_members()) == before
    seen = []
    with reader.derive_options(label_mode="literal"):
        t = threading.Thread(target=lambda: seen.append(reader.get_label_mode()))
        t.start()
        t.join()
    assert seen == [before[0]]  # another thread keeps the process setting
