"""Symbol and genie resolution."""

import os
import struct

import pytest


def _walk(children):
    for c in children:
        yield c
        if c.children:
            yield from _walk(c.children)


# ------------------------------------------------------------- projects.py
def _write_dbf(path, fields, rows):
    """Write a minimal dBASE III table (char fields only) for tests."""
    fdescs = b""
    rec_size = 1
    for name, length in fields:
        fdescs += name.ljust(11, "\x00").encode("latin1")[:11]
        fdescs += b"C" + b"\x00" * 4 + bytes([length]) + b"\x00" * 15
        rec_size += length
    hdr_size = 32 + len(fdescs) + 1
    head = struct.pack("<BBBBIHH20x", 3, 26, 6, 10, len(rows), hdr_size, rec_size)
    body = b""
    for row in rows:
        rec = b" "
        for (name, length), val in zip(fields, row):
            rec += val.encode("latin1").ljust(length)[:length]
        body += rec
    with open(path, "wb") as fh:
        fh.write(head + fdescs + b"\x0d" + body + b"\x1a")


def test_read_dbf_rows_roundtrip(tmp_path):
    from ctgedit.format.projects import read_dbf_rows

    p = str(tmp_path / "t.dbf")
    _write_dbf(p, [("NAME", 16), ("COMMENT", 8)], [("Alpha", "first"), ("Beta", "")])
    rows = read_dbf_rows(p)
    assert rows == [{"NAME": "Alpha", "COMMENT": "first"}, {"NAME": "Beta", "COMMENT": ""}]


def test_read_dbf_rows_never_raises(tmp_path):
    from ctgedit.format.projects import read_dbf_rows

    assert read_dbf_rows(str(tmp_path / "missing.dbf")) == []
    bad = str(tmp_path / "bad.dbf")
    open(bad, "wb").write(b"\x03garbage")
    assert read_dbf_rows(bad) == []


def test_include_chain_with_sibling_install_fallback(tmp_path):
    """A registered-but-missing project resolves through a sibling install's
    User tree, recursively and dedup-guarded."""
    from ctgedit.format.projects import include_search_dirs

    r1 = tmp_path / "Install One" / "User"
    r2 = tmp_path / "Install Two R2" / "User"
    for proj in ("Proj",):
        (r1 / proj).mkdir(parents=True)
    for proj in ("LibA", "LibB"):
        (r2 / proj).mkdir(parents=True)
    # registries: R1 registers LibA at an R1 path that does NOT exist. The field widths
    # are a real MASTER.DBF's (NAME 64, PATH 240).
    _write_dbf(
        str(r1 / "MASTER.DBF"),
        [("NAME", 64), ("PATH", 240)],
        [("Proj", str(r1 / "Proj")), ("LibA", str(r1 / "LibA"))],
    )
    _write_dbf(
        str(r2 / "MASTER.DBF"),
        [("NAME", 64), ("PATH", 240)],
        [("LibA", str(r2 / "LibA")), ("LibB", str(r2 / "LibB"))],
    )
    # include chains: Proj -> LibA -> LibB (and a cycle back to LibA)
    _write_dbf(str(r1 / "Proj" / "include.dbf"), [("NAME", 16), ("COMMENT", 4)], [("LibA", "")])
    _write_dbf(str(r2 / "LibA" / "include.dbf"), [("NAME", 16), ("COMMENT", 4)], [("LibB", "")])
    _write_dbf(str(r2 / "LibB" / "include.dbf"), [("NAME", 16), ("COMMENT", 4)], [("LibA", "")])  # cycle

    dirs = include_search_dirs(str(r1 / "Proj"))
    assert dirs == [str(r2 / "LibA"), str(r2 / "LibB")]


def test_for_page_search_path_covers_include_closure(fx_page, fx_project):
    """A page's resolver search path is its project, then the recursive include chain in
    chain order (FxMain -> FxLib, FxStyle, then the system projects Include and System),
    and resolves the libraries on it."""
    from ctgedit.format.symbols import SymbolResolver

    r = SymbolResolver.for_page(fx_page("FxShapes"))
    lower = [os.path.normcase(os.path.abspath(p)) for p in r.search_paths]
    want = [
        os.path.normcase(os.path.abspath(fx_project(n)))
        for n in ("FxMain", "FxLib", "FxStyle", "Include", "System")
    ]
    assert lower == want
    for lib, ext in (
        ("fxgenie", "ctm"),
        ("fxsym", "ctl"),
        ("fx_style", "ctt"),
        ("lights", "ctl"),
        ("internal", "ctm"),
    ):
        assert r.library(lib, ext) is not None, (lib, ext)


def test_same_named_library_in_multiple_projects_resolves_by_element(fx_page, fx_project):
    """fxsym.ctl is in FxOther (fx_other_only) AND in FxLib (fx_box ...), disjoint element
    sets: resolution must try every same-named file on the path."""
    from ctgedit.format.symbols import SymbolReference, SymbolResolver

    r = SymbolResolver.for_page(fx_page("FxShapes", "FxOther"))
    libs = r.libraries("fxsym", "ctl")
    assert len(libs) == 2
    assert os.path.normcase(os.path.dirname(libs[0].path)) == os.path.normcase(fx_project("FxOther"))
    names0 = set(libs[0].element_names())
    only_later = [n for n in libs[1].element_names() if n not in names0]
    assert "fx_box" in only_later
    rs = r.resolve(SymbolReference("fxsym", "fx_box", "ctl", "symbol"))
    assert rs is not None and rs.library_path == libs[1].path
    rs = r.resolve(SymbolReference("fxsym", "fx_other_only", "ctl", "symbol"))
    assert rs is not None and rs.library_path == libs[0].path


# ------------------------------------------------------------ cicode_rest
def test_rest_eval_hidden_when_semantics():
    """VISIBLE holds a hidden-when expression."""
    from ctgedit.io.cicode_rest import visible_at_rest

    assert visible_at_rest("TRUE") is False  # always hidden
    assert visible_at_rest("1") is False
    assert visible_at_rest("-1") is False
    assert visible_at_rest("0") is True  # never hidden
    assert visible_at_rest("FALSE") is True
    assert visible_at_rest("NOT TRUE") is True
    assert visible_at_rest('"Motor" <> "Automatic"') is False
    assert visible_at_rest('"Motor" <> "Motor"') is True
    assert visible_at_rest('NOT TagGetValueIfExists("A.B.Opening", 0)') is None
    assert visible_at_rest("NOT GetPrivEx(0, 0, Foo(__E))") is None
    assert visible_at_rest("NOT IsRunStatusOutOfService(.RunStatus)") is None


def test_rest_eval_unknowables_yield_none():
    """Metadata readers, custom functions and bare variables have no rest
    default -- the verdict must be None so the renderer stays conservative."""
    from ctgedit.io.cicode_rest import visible_at_rest

    assert visible_at_rest('DspGetMetadataFromName("x","Mode") <> "4"') is None
    assert visible_at_rest("SA_DisableLabel = 1") is None
    assert visible_at_rest("Custom_Function(1, 2)") is None
    assert visible_at_rest("AlarmDsp(0, 1)") is None
    assert visible_at_rest("%%%garbage((") is None
    assert visible_at_rest("") is None
    # unknown poisons through operators
    assert visible_at_rest("NOT Custom_Function()") is None
    # ... but a definite FALSE short-circuits an AND: Hidden-when FALSE = shown
    assert visible_at_rest("Custom_Function() AND FALSE") is True


# ------------------------------------------------------- reader expansion
@pytest.mark.usefixtures("composite_cache")
def test_composite_cache_glyphs_expand_with_real_art(fx_page):
    """FxComposites' nine composite genies resolve their composition caches: each is
    drawn from the cache element with children."""
    from ctgedit.io.reader import read_page
    from ctgedit.model.document import ObjectKind

    page = read_page(fx_page("FxComposites"))
    roots = [o for o in page.objects if o.kind is ObjectKind.COMPOSITE_GENIE and o.parent is None]
    composites = [o for o in roots if o.children]
    assert len(roots) == 9 and len(composites) == 9, "a composite genie lost its cache"
    assert all(list(_walk(o.children)) for o in composites)


@pytest.mark.usefixtures("composite_cache")
def test_composite_cache_nested_members_expand_and_repair(fx_page):
    """The nested art-stripped members inside the cache expand from their library refs: a
    valve bowtie polygon gets repaired vertex nodes (3) and a static black fill, and
    symbol-set slots reduce to one drawn state (_state_hidden on the others). FxComposites'
    FxComp9 (FxStatusC: fxparts.fx_sets, fx_alarm and fx_bowtie, two black triangles)."""
    from ctgedit.io.reader import read_page
    from ctgedit.model.document import ObjectKind

    page = read_page(fx_page("FxComposites"))
    root = next(
        o for o in page.objects if o.kind is ObjectKind.COMPOSITE_GENIE and o.raw.get("_name") == "FxComp9"
    )
    assert root.children, "FxComp9 did not resolve its cache"
    repaired = [
        c
        for c in _walk(root.children)
        if c.kind is ObjectKind.POLYGON
        and len(c.geometry.nodes or []) == 3
        and c.style.fill_colour == 0x000000
    ]
    nested = [c for c in _walk(root.children) if c.members and c.children]
    hidden = sum(1 for c in _walk(root.children) if c.raw.get("_state_hidden"))
    assert nested, "no nested cache member expanded from its library"
    assert hidden > 0, "symbol-set state selection did not run"
    assert len(repaired) == 2, "the bowtie triangles lost their vertices or black fill"


def test_plain_genie_with_artless_members_expands_from_library(fx_page):
    """A GENIE container whose ref is a plain .ctm and whose inline members are
    art-stripped gets the library element as children (FxParts' FxSets1, fxparts.fx_sets);
    genies whose members carry the substituted art stay inline."""
    from ctgedit.editing import EditableDocument
    from ctgedit.model.document import ObjectKind

    doc = EditableDocument.open(fx_page("FxParts"))
    p = doc.page
    expanded = [i for i, o in enumerate(p.objects) if o.kind is ObjectKind.GENIE and o.members and o.children]
    assert expanded, "no plain genie expanded from its library"
    (sets,) = doc.find_objects_by_name("FxSets1")
    assert sets in expanded
    # the expansion suppressed the stripped inline subtree
    for i in expanded:
        ref = p.objects[i].raw.get("_resolved_ref", "")
        assert ref, "expanded genie lost its reference label"
    assert p.objects[sets].raw["_resolved_ref"].startswith("fxparts.fx_sets")
    # (the other genies of the page stay inline)
    (pump,) = doc.find_objects_by_name("FxValveLeft1")
    assert pump not in expanded


def test_value_placeholders_from_txt_str_tail(fx_page, tmp_path):
    """Value displays carrying '(cicode):<format>' in TXT_STR: CtDraw's editor shows the
    format tail ('#.#', '#!') as the placeholder text, never the 'abc' fallback. FxTextKinds'
    FxNumParen stores '(FX_LEVEL1):#.#'; the test gives FxNumBang (stored as 'FX_LEVEL2:#!')
    the '(FX_LEVEL2):#!' form in a copy."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.reader import read_page

    doc = EditableDocument.open(fx_page("FxTextKinds"))
    (bang,) = doc.find_objects_by_name("FxNumBang")
    assert doc.ensure_property(bang, "TXT_STR", "(FX_LEVEL2):#!")
    path = str(tmp_path / "FxTextKindsBang.ctg")
    doc.save(path)
    doc = EditableDocument.open(path)
    page = read_page(path)
    texts = {}
    for name in ("FxNumParen", "FxNumBang", "FxNumUndefined", "FxNumField", "FxNumOverride", "FxNumEquip"):
        (i,) = doc.find_objects_by_name(name)
        texts[name] = page.objects[i].style.text
    assert texts["FxNumParen"] == "#.#", texts
    assert texts["FxNumBang"] == "#!", texts
    assert "abc" not in texts.values(), "TXT_STR-driven displays still fall back to 'abc'"


def test_advanced_animation_symbol_display_is_drawable(fx_page):
    """A tc=0x1009 AdvancedAnimation with a SYMBOL bag (FxCicode's FxCicodePage
    'fxicons16.page', FxCicodePriority 'fxprio.fx_p1') is a drawable symbol-set whose ref
    resolves to children; without a SYMBOL value (FxCicodeIcon ...) it stays non-drawable.
    (FxCicodeAngle1 / 2 name no library element: '<default>', '<deAlarm>'; not this rule.)"""
    from ctgedit.format.page import parse_ctg
    from ctgedit.io.reader import read_page
    from ctgedit.model.document import ObjectKind

    path = fx_page("FxCicode")
    page = read_page(path)
    pobjs = parse_ctg(path).page.objects
    drawn = []
    bare = 0
    for i, o in enumerate(page.objects):
        if pobjs[i].type_code != 0x1009:
            continue
        sym = str(pobjs[i].raw_properties().get("SYMBOL") or "")
        if sym.startswith("<"):
            continue  # ('<default>', '<deAlarm>': the Cicode object's own icon)
        if sym:
            assert not o.raw.get("_non_drawable")
            assert o.kind is ObjectKind.SYMBOL_SET
            drawn.append(o)
        else:
            assert o.raw.get("_non_drawable")
            bare += 1
    assert drawn, "no SYMBOL-carrying 0x1009 records found"
    assert bare >= 2
    assert sum(1 for o in drawn if o.children) >= 2, "no 0x1009 symbol display resolved its element"


def test_flat_pair_colour_animation_unfilled(fx_page):
    """A colour-animated shape's COL_MAXON is never the design-time fill."""
    from ctgedit.format.page import parse_ctg
    from ctgedit.io.reader import read_page

    path = fx_page("FxColours")
    page = read_page(path)
    pobjs = parse_ctg(path).page.objects
    checked = 0
    for i, o in enumerate(page.objects):
        if pobjs[i].type_code != 0x0F:
            continue
        rp = pobjs[i].raw_properties()
        pair = str(rp.get("COL_MAXON", ""))
        col_a = str(rp.get("COL_A", "")).strip().upper()
        if "COL_A" in rp and "," in pair and col_a not in ("TRUE", "FALSE"):
            on, off = pair.split(",", 1)
            if on.strip() == off.strip():
                maxon = int(on.strip(), 16) & 0xFFFFFF
                assert not o.style.filled or (o.style.fill_colour or 0) & 0xFFFFFF != maxon, (
                    "#%d rests at its runtime COL_MAXON colour" % i
                )
                checked += 1
    assert checked, "no flat-pair animated rectangles found"


def test_rest_hidden_objects_marked(fx_page):
    """Objects whose VISIBLE gate rest-evaluates carry the marker the renderer uses (the
    gate is the HIDDEN-WHEN expression): FxSymbols2's FxHiddenEmbedded (VISIBLE '0': never
    hidden) is True; FxVisibleGroupSym (VISIBLE FX_PUMP1, a tag) carries no verdict."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxSymbols2"))
    p = doc.page
    verdicts = [o.raw.get("_rest_visible") for o in p.objects if o.animations.visibility is not None]
    assert True in verdicts or False in verdicts, "no gate evaluated at all"
    (i,) = doc.find_objects_by_name("FxHiddenEmbedded")
    assert p.objects[i].raw.get("_rest_visible") is True
    (i,) = doc.find_objects_by_name("FxVisibleGroupSym")
    assert p.objects[i].animations.visibility is not None
    assert p.objects[i].raw.get("_rest_visible") is None


# ------------------------------------------------------------ editing
def test_composite_genie_options_are_read_only(fx_page):
    """Composite genie options are read-only."""
    from ctgedit.editing import EditableDocument
    from ctgedit.model.document import ObjectKind

    doc = EditableDocument.open(fx_page("FxComposites"))
    roots = [i for i in doc.page.roots() if doc.page.objects[i].kind is ObjectKind.COMPOSITE_GENIE]
    assert len(roots) == 9
    (idx,) = doc.find_objects_by_name("FxComp1")
    assert idx in roots
    before = doc.serialize()
    eq = doc.genie_bindings(idx)["Tag"]
    assert eq == "FxPlant.Valve1"
    assert doc.is_composite_genie(idx)
    assert all(doc.is_composite_genie(j) for j in doc.subtree(idx))
    assert doc.set_genie_binding(idx, "Tag", eq + "_X") is False
    assert doc.set_genie_bindings(idx, {"Tag": eq + "_X"}) is False
    assert doc.serialize() == before and not doc._undo


@pytest.mark.usefixtures("composite_cache")
def test_composite_genie_hit_test_resolves_to_root(fx_page):
    """Hits on a composite genie resolve to its root."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from ctgedit.io.reader import read_page
    from ctgedit.model.document import ObjectKind
    from ctgedit.render.scene import PageScene

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    page = read_page(fx_page("FxComposites"))
    scene = PageScene()
    scene.set_page(page)

    def own(item, above):
        d = item.data(0)
        return d if isinstance(d, int) and d in scene._items else above

    checked = 0
    groups = 0
    for idx, o in enumerate(page.objects):
        if o.kind is not ObjectKind.COMPOSITE_GENIE or not o.children:
            continue
        group = scene._items.get(idx)
        if group is None:
            continue
        groups += 1
        top = own(group, None)
        assert top == idx
        stack = [(it, top) for it in group.childItems()]
        while stack:
            it, above = stack.pop()
            got = own(it, above)
            assert got == idx, "item inside composite #%d hit-tests to %r" % (idx, got)
            stack.extend((c, got) for c in it.childItems())
            checked += 1
    assert groups == 9
    assert checked > 0, "no composite group items checked"


@pytest.mark.usefixtures("composite_cache")
def test_composite_genie_subtree_moves_as_unit(fx_page):
    """Moving a composite's subtree shifts the record geometry AND the cached
    placement transform that positions the resolved art -- without the latter
    the art snaps back to the old spot on the next scene rebuild (FxComposites' FxComp1)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.model.document import ObjectKind

    doc = EditableDocument.open(fx_page("FxComposites"))
    (idx,) = doc.find_objects_by_name("FxComp1")
    assert doc.page.objects[idx].kind is ObjectKind.COMPOSITE_GENIE
    assert doc.page.objects[idx].children
    o = doc.page.objects[idx]
    before = (o.geometry.x, o.geometry.y)
    xf_before = o.raw["_symbol_xform"]
    cxf_before = o.children[0].raw["_symbol_xform"]

    doc.move_objects(doc.expand_to_subtrees([idx]), 7, 11)
    o2 = doc.page.objects[idx]
    assert (o2.geometry.x, o2.geometry.y) == (before[0] + 7, before[1] + 11)
    assert o2.raw["_symbol_xform"] == (xf_before[0] + 7, xf_before[1] + 11, xf_before[2], xf_before[3])
    assert o2.children[0].raw["_symbol_xform"] == (
        cxf_before[0] + 7,
        cxf_before[1] + 11,
        cxf_before[2],
        cxf_before[3],
    )

    doc.undo()
    o3 = doc.page.objects[idx]
    assert (o3.geometry.x, o3.geometry.y) == before
    assert o3.raw["_symbol_xform"] == xf_before


@pytest.mark.usefixtures("composite_cache")
def test_rerender_after_edit_keeps_composite_art(fx_page):
    """A body edit re-derives the page; the resolver must still walk the real
    project's include chain or the composite loses its glyph art after any
    edit (FxComposites' FxComp1)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxComposites"))
    (idx,) = doc.find_objects_by_name("FxComp1")
    n_before = len(doc.page.objects[idx].children)
    assert n_before
    doc.move_objects([idx], 8, 0)
    assert len(doc.page.objects[idx].children) == n_before, "composite art lost across the re-derive"
    doc.undo()
