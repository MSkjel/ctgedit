"""Page templates end to end."""

import glob
import json
import os
import shutil

import fxinstall
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from ctgedit.format import genie_place as GP  # noqa: E402
from ctgedit.format import geometry as G  # noqa: E402
from ctgedit.format import links  # noqa: E402
from ctgedit.format import placement as P  # noqa: E402
from ctgedit.format import template_place as TP  # noqa: E402
from ctgedit.format import writer  # noqa: E402
from ctgedit.format.page import parse_ctg  # noqa: E402
from ctgedit.format.symbols import SymbolResolver  # noqa: E402
from qtkit import app as _app

NORMAL = "fx_style.normal@XGA.<ctt"
NOTITLE = "fx_style.normal@-XGA.<ctt"
BLANK = "fx_style.blank@SVGA.<ctt"
#: the pages of FxMain placed on a template (linked)
TEMPLATE_PAGES = ("FxTplLinked", "FxTplNoTitle", "FxTplBlank")


def _payloads(objs):
    return [writer.object_payload(po) for po in objs]


def _light_doc(path):
    """An EditableDocument without the semantic page (template_status /
    update_template only need the parsed records): the corpus checks stay fast."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument(path=path)
    doc.parsed = parse_ctg(path)
    return doc


def _handles_by_an(prefix):
    """(an_str, type) -> handle of the prefix members (not the root, not the
    input records that share their owner's handle)."""
    par = P.forest_parents(prefix)
    out = {}
    for j, po in enumerate(prefix):
        if j == 0 or (po.type_code in GP.INPUT_TCS and par[j] is not None):
            continue
        out.setdefault((po.an_str, po.type_code), G.get_handle(po))
    return out


# ------------------------------------------------------------------ recompose
def _corpus_check(pages):
    exact = current = 0
    resolvers = {}
    for p in pages:
        pc = parse_ctg(p)
        if pc.page is None:
            continue
        d = os.path.dirname(p)
        res = resolvers.get(d) or resolvers.setdefault(d, SymbolResolver.for_page(p))
        roots = TP.template_roots(pc.page.objects)
        if not roots:
            continue
        tpl = TP.resolve_template(res, roots[0][2])
        if tpl is None:
            continue
        link = GP.decode(pc.page.objects[roots[0][0]]).link or roots[0][2].raw
        if TP.is_stale(pc.page, tpl, link):
            continue
        current += 1
        r = TP.recompose(pc.page.objects, res, page_name=os.path.splitext(os.path.basename(p))[0])
        exact += _payloads(r["objects"]) == _payloads(pc.page.objects)
    return exact, current


def test_recompose_reproduces_every_template_page(fx_install):
    """Every page of the fake install that is placed on a template re-composes byte for
    byte (ctgedit wrote them: its own composition is a fixed point)."""
    pages = sorted(glob.glob(os.path.join(fx_install.user, "*", "*.ctg")))
    exact, current = _corpus_check(pages)
    # (FxTplStale is stale on purpose: it is not counted)
    assert current >= len(TEMPLATE_PAGES)
    assert exact == current


def test_update_template_is_a_noop_on_every_current_page(fx_install):
    """Every template page of FxMain: the template is current, so Update Template
    changes nothing and adds no undo step."""
    checked = 0
    for name in TEMPLATE_PAGES + ("FxShapes", "FxGenies"):
        doc = _light_doc(fx_install.page(name))
        state = doc.template_status()[0]
        if state == "none":
            continue
        assert state == "current", name
        before = _payloads(doc.parsed.page.objects)
        assert doc.update_template() == 0, name
        assert not doc.can_undo()
        assert _payloads(doc.parsed.page.objects) == before
        checked += 1
    assert checked == len(TEMPLATE_PAGES)


# ---------------------------------------------------------- stale template
@pytest.fixture()
def stale_project(fx_install):
    """A project FxStale (including FxLib) holding a copy of fx_style.ctt whose
    normal@XGA was edited through ElementDocument (its caption 'tpl' changed, the small
    rectangle moved, the corner line deleted, a rectangle added) + the handles of those
    records. Pages copied into it resolve the edited template first."""
    from ctgedit.editing import ElementDocument

    proj = fx_install.new_project("FxStale", includes=("FxLib",), tables=False)
    shutil.copy2(fx_install.file("FxStyle", "fx_style.ctt"), os.path.join(proj, "fx_style.ctt"))
    el = ElementDocument.open_element(os.path.join(proj, "fx_style.ctt"), "normal@XGA")
    objs = el.page.objects

    def root(pred):
        return next(i for i, o in enumerate(objs) if o.parent is None and pred(o))

    ti = root(lambda o: o.style.text == "tpl")
    ri = root(lambda o: o.raw.get("_type_code") == 0x0F and (o.geometry.x, o.geometry.y) == (964, 40))
    li = root(lambda o: o.raw.get("_type_code") == 0x01 and o.geometry.extent_y == 27)
    handles = {
        k: G.get_handle(el.parsed.page.objects[i]) for k, i in (("text", ti), ("rect", ri), ("line", li))
    }
    assert el.set_text(ti, "Changed caption")
    el.move_objects([ri], 10, 5)
    el.delete_objects([li])
    el.create_object("rectangle", (20, 700, 120, 720))
    el.save()
    return proj, handles


def _stale_page(fx_install, proj, name):
    dst = os.path.join(proj, name + ".ctg")
    shutil.copy2(fx_install.page("FxTplLinked"), dst)
    return dst


def test_edited_template_updates_only_the_prefix(fx_install, stale_project):
    from ctgedit.editing import EditableDocument
    from ctgedit.io import pgregister

    proj, handles = stale_project
    path = _stale_page(fx_install, proj, "FxUpd")
    doc = EditableDocument.open(path)
    assert doc.template_status() == ("stale", NORMAL)
    before = _payloads(doc.parsed.page.objects)
    ((a, b, _ref),) = TP.template_roots(doc.parsed.page.objects)
    old_prefix = list(doc.parsed.page.objects[a:b])
    rows_before = pgregister.page_rows(doc.parsed, "FxUpd")
    size_before = (doc.parsed.page.page_width, doc.parsed.page.page_height)

    assert doc.update_template(page_properties=False) > 0
    assert doc.template_status()[0] == "current"
    objs = doc.parsed.page.objects
    after = _payloads(objs)
    ((a2, b2, _r),) = TP.template_roots(objs)
    # the page's own objects: byte-identical, same order
    assert after[:a2] + after[b2:] == before[:a] + before[b:]
    # the template edits arrived: one line gone, one rectangle added
    old_h, new_h = _handles_by_an(old_prefix), _handles_by_an(objs[a2:b2])
    assert (str(handles["line"]), 0x01) in old_h and (str(handles["line"]), 0x01) not in new_h
    assert len(set(new_h) - set(old_h)) == 1 and len(set(old_h) - set(new_h)) == 1
    texts = [o.style.text for o in doc.page.objects[a2:b2]]
    assert "Changed caption" in texts and "tpl" not in texts
    moved = next(po for po in objs[a2:b2] if po.an_str == str(handles["rect"]) and po.type_code == 0x0F)
    assert tuple(G.parse_geom_header(moved.body_raw).rect1)[:2] == (974, 45)
    # matched members keep their page handles -> their pg rows keep their keys
    common = set(old_h) & set(new_h)
    assert len(common) >= 5 and all(old_h[k] == new_h[k] for k in common)
    rows_after = pgregister.page_rows(doc.parsed, "FxUpd")
    keys = lambda rows: {(t, r["AN"]) for t, rs in rows.items() for r in rs}  # noqa: E731
    assert keys(rows_before) <= keys(rows_after)
    # page size / properties untouched without TemplateUpdate
    assert (doc.parsed.page.page_width, doc.parsed.page.page_height) == size_before
    # the template lock re-aligns to the new template
    locked = sum(1 for i in range(a2, b2) if doc.page.objects[i].raw.get("_from_template"))
    assert locked >= 0.9 * (b2 - a2)
    # a fixed point, and one undo step back
    r = TP.recompose(objs, doc._resolver(), page_name=doc.page.name)
    assert _payloads(r["objects"]) == after
    doc.undo()
    assert _payloads(doc.parsed.page.objects) == before


def test_template_update_ini_applies_the_template_page_properties(fx_install, stale_project, monkeypatch):
    """[Animator] TemplateUpdate=1: the updated page also takes the
    template's size (and background / page properties)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import citect_config

    proj, _h = stale_project
    doc = EditableDocument.open(_stale_page(fx_install, proj, "FxProps"))
    doc.set_page_size(800, 600)
    monkeypatch.setattr(citect_config, "template_update_enabled", lambda: False)
    assert doc.update_template() > 0
    assert (doc.parsed.page.page_width, doc.parsed.page.page_height) == (800, 600)
    doc.undo()
    monkeypatch.setattr(citect_config, "template_update_enabled", lambda: True)
    assert doc.update_template() > 0
    tpl = TP.resolve_template(doc._resolver(), TP.reference_for(NORMAL))
    assert (doc.parsed.page.page_width, doc.parsed.page.page_height) == (tpl.width, tpl.height)
    _bg, props = TP.template_page_properties(tpl)
    have_props = dict(doc.parsed.attributes.list1.pairs)
    assert props and all(have_props.get(k) == v for k, v in props)


def test_update_pages_registered_only_skips_builder_pages_and_backs_up_rows(
    fx_install, stale_project, tmp_path
):
    from ctgedit.io import pgregister, updatepages

    proj, _h = stale_project
    # FxMain's pages.DBF registers FxTplLinked, not Loose; FxMain's pgtouch.DBF holds the
    # page's rows; the edited template comes through FxStale
    sub = fx_install.new_project("FxReg", includes=("FxStale", "FxLib"), tables=False)
    shutil.copy2(fx_install.file("FxMain", "pages.DBF"), sub)
    shutil.copy2(fx_install.file("FxMain", "pgtouch.DBF"), sub)
    page = fx_install.page("FxTplLinked")
    shutil.copy2(page, os.path.join(sub, "FxTplLinked.ctg"))
    shutil.copy2(page, os.path.join(sub, "Loose.ctg"))
    orig = open(page, "rb").read()
    # open in the Graphics Builder: skipped
    rep = updatepages.update_pages(sub, builder_open=[("FxReg", "FxTplLinked")], backup=None)
    assert "Loose" in rep.unregistered and "FxTplLinked" in rep.open_in_builder
    assert not rep.updated
    assert open(os.path.join(sub, "FxTplLinked.ctg"), "rb").read() == orig
    # now updated, with a backup of the page and its project database rows
    rows_now = pgregister.export_page_rows(sub, "FxTplLinked")
    assert rows_now.get("pgtouch")  # the pump genie's touch command
    bk = str(tmp_path / "backup")
    rep = updatepages.update_pages(sub, backup=bk)
    assert rep.updated == ["FxTplLinked"] and rep.templates == 1, rep
    assert open(os.path.join(sub, "Loose.ctg"), "rb").read() == orig  # unregistered
    assert open(os.path.join(bk, "FxReg", "FxTplLinked.ctg"), "rb").read() == orig
    saved = json.load(open(os.path.join(bk, "FxReg", "FxTplLinked.pgrows.json"), encoding="utf-8"))
    assert saved["page"] == "FxTplLinked"
    assert saved["tables"] == rows_now
    # the page's own touch rows were rewritten IN PLACE (same records), none deleted +
    # appended
    after = pgregister.export_page_rows(sub, "FxTplLinked")
    rec = lambda rows: sorted(int(r["_record"]) for r in rows)  # noqa: E731
    assert rec(after["pgtouch"]) == rec(rows_now["pgtouch"])


# ------------------------------------------------------------ switching
@pytest.fixture()
def new_project(fx_install):
    """A new project FxNew (including FxLib and FxStyle) with empty page tables."""
    return fx_install.new_project("FxNew")


def test_change_template_round_trips(new_project):
    """A -> B -> A: the page's records and link table come back byte for
    byte, and its size follows the template each time."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.new_from_template(
        NORMAL, os.path.join(new_project, "RoundTrip.ctg"), register=False
    )
    doc.create_object("rectangle", (100, 200, 300, 260))  # an own object
    recs0 = _payloads(doc.parsed.page.objects)
    table0 = (doc.parsed.page.link_table_raw, doc.parsed.page.link_count)
    size0 = (doc.parsed.page.page_width, doc.parsed.page.page_height)
    assert doc.change_template(NOTITLE)
    assert doc.template_status() == ("current", NOTITLE)
    assert _payloads(doc.parsed.page.objects)[-1] == recs0[-1]  # own object kept
    assert doc.change_template(NORMAL)
    assert doc.template_status() == ("current", NORMAL)
    assert _payloads(doc.parsed.page.objects) == recs0
    assert (doc.parsed.page.link_table_raw, doc.parsed.page.link_count) == table0
    assert (doc.parsed.page.page_width, doc.parsed.page.page_height) == size0
    doc.undo()
    doc.undo()
    assert _payloads(doc.parsed.page.objects) == recs0


# ------------------------------------------------------------- new pages
def test_new_page_from_template_registers_and_opens_locked(new_project):
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project

    path = os.path.join(new_project, "FromTemplate.ctg")
    doc = EditableDocument.new_from_template(NORMAL, path, background=(0xE5E5E5,) * 2)
    assert Project(new_project).page_record("FromTemplate") is not None  # registered
    again = EditableDocument.open(path)
    assert again.template_status() == ("current", NORMAL)
    objs = again.parsed.page.objects
    tpl = TP.resolve_template(again._resolver(), TP.reference_for(NORMAL))
    ((a, b, _r),) = TP.template_roots(objs)
    assert (a, b) == (0, len(objs)) and b == len(tpl.records)
    assert objs[0].subtype_size & 0x5 == 0x5  # LINK + NOSELECT
    # the template lock applies (aligned records are template records)
    locked = sum(1 for o in again.page.objects if o.raw.get("_from_template"))
    assert locked >= 0.9 * len(objs)
    # CtDraw's new-page form: size, page properties, associations, background
    at = again.parsed.attributes
    assert (at.width, at.height) == (tpl.width, tpl.height) == (1024, 768)
    _bg, props = TP.template_page_properties(tpl)
    assert list(at.list1.pairs) == props and props
    assert list(at.list2.pairs) == TP.template_associations(tpl)
    assert at.list2.pairs
    # the template's own background wins over the Page Defaults colour
    assert tpl.trailer.background() == (0xD8D8D8, 0xD8D8D8)
    assert (at.bg_color0, at.bg_color1) == (0xD8D8D8, 0xD8D8D8)
    assert again.parsed.page.page_identity == at.page_identity
    # link table: the template entry, then the @() labels of its texts
    ents = links.parse_link_table(again.parsed.page.link_table_raw)[1]
    assert ents[0] == TP.link_entry(tpl, NORMAL)
    assert [n for _g, n in ents[1:]] == ["\\\\Fx Menu"]
    # the update path re-composes it as CtDraw's load does: the same records but the
    # DummyMarker, which has no point in slot 2 and keeps its element extent on the new
    # page (CtDraw's PageNew), and is the empty rect at the origin once loaded; handles
    # unique
    r = TP.recompose(objs, again._resolver(), page_name="FromTemplate")
    dm = [j for j, po in enumerate(objs) if po.type_code == TP.DUMMY_TC]
    assert len(dm) == 1 and _geo(objs[dm[0]]) == MARKER_GEO
    assert _geo(r["objects"][dm[0]]) == ((0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
    assert [p for j, p in enumerate(_payloads(r["objects"])) if j not in dm] == [
        p for j, p in enumerate(_payloads(objs)) if j not in dm
    ]
    hs = [G.get_handle(po) for po in objs if po.type_code not in GP.INPUT_TCS]
    assert len(hs) == len(set(hs))
    assert doc.save_warning == ""
    with pytest.raises(FileExistsError):
        EditableDocument.new_from_template(NORMAL, path)
    # a template without a background: the page takes the colour it is given
    blank = EditableDocument.new_from_template(
        BLANK, os.path.join(new_project, "Blank.ctg"), background=(0xE5E5E5,) * 2
    )
    at = blank.parsed.attributes
    assert (at.width, at.height, at.bg_color0, at.bg_color1) == (800, 600, 0xE5E5E5, 0xE5E5E5)


def test_new_page_refuses_unknown_template(new_project):
    from ctgedit.editing import EditableDocument

    with pytest.raises(KeyError):
        EditableDocument.new_from_template(
            "no_such_style.normal@XGA.<ctt", os.path.join(new_project, "Nope.ctg")
        )
    assert not os.path.exists(os.path.join(new_project, "Nope.ctg"))


def test_unlinked_new_page_and_cut_link(new_project):
    """Linked=off copies the template's members as ordinary top-level objects with
    their own appearance (CtDraw's PageNew writes no root group: oracle newpage/unlinked);
    Cut Link on a linked page keeps the template's root group, now a plain group, over the
    same records (handles kept; oracle newpage/cut_link) and removes the template link."""
    from ctgedit.editing import EditableDocument

    un = EditableDocument.new_from_template(NORMAL, os.path.join(new_project, "Unlinked.ctg"), linked=False)
    assert un.template_status()[0] == "none"
    tpl = TP.resolve_template(un._resolver(), TP.reference_for(NORMAL))
    objs = un.parsed.page.objects
    assert len(objs) == len(tpl.records) - 1
    # no lock (the nested genie FxTplMenu keeps its own library link)
    assert not any(po.subtype_size & 0x4 for po in objs)
    for po, e in zip(objs, tpl.records[1:]):  # appearance is back
        assert po.type_code == e.type_code
        assert bool(po.subtype_size & 0x2) == bool(e.subtype_size & 0x2)
    names = [
        links.strip_stamp(n).lower()
        for _g, n in (links.parse_link_table(un.parsed.page.link_table_raw) or ("", []))[1]
    ]
    assert NORMAL.lower() not in names

    doc = EditableDocument.new_from_template(NORMAL, os.path.join(new_project, "Linked.ctg"), register=False)
    before = _payloads(doc.parsed.page.objects)
    handles = [G.get_handle(po) for po in doc.parsed.page.objects]
    assert doc.cut_template_link() == len(before)
    assert doc.template_status()[0] == "none"
    assert [G.get_handle(po) for po in doc.parsed.page.objects] == handles
    cut = doc.parsed.page.objects
    assert cut[0].type_code == TP.GROUP_TC and not cut[0].subtype_size & 0x5
    # the same records as the unlinked page, but for the genie the template placed: CtDraw's
    # Cut Link empties its values (oracle newpage/cut_link) where its unlinked New Page keeps
    # them (newpage/unlinked)
    menu = next(k for k, po in enumerate(objs) if po.subtype_size & 0x201 == 0x201)
    span = range(menu, GP.subtree_end(objs, menu))
    assert [p for k, p in enumerate(_payloads(cut[1:])) if k not in span] == [
        p for k, p in enumerate(_payloads(objs)) if k not in span
    ]
    texts = [
        GP.decode(cut[1 + k]).entries[0][1].get("TXT_OFF") for k in span if cut[1 + k].type_code == TP.TEXT_TC
    ]
    assert texts == ["@()"]
    assert [
        GP.decode(objs[k]).entries[0][1].get("TXT_OFF") for k in span if objs[k].type_code == TP.TEXT_TC
    ] == ["@(Fx Menu)"]
    assert not any(o.raw.get("_from_template") for o in doc.page.objects[:1])
    doc.undo()
    assert _payloads(doc.parsed.page.objects) == before
    assert doc.template_status() == ("current", NORMAL)
    # the unlinked page saves and re-opens
    doc.cut_template_link()
    doc.save()
    assert EditableDocument.open(doc.path).template_status()[0] == "none"


def test_cut_link_on_a_current_page_whose_template_copy_was_edited(new_project):
    """A CURRENT page whose template copy no longer lines up with the
    template record for record (a member deleted by hand, Break Lock): Cut
    Link converts the template as it is now."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.new_from_template(NORMAL, os.path.join(new_project, "Edited.ctg"), register=False)
    tpl = TP.resolve_template(doc._resolver(), TP.reference_for(NORMAL))
    objs = doc.parsed.page.objects
    leaf = next(
        j for j in range(len(objs) - 1, 0, -1) if not objs[j].class_flag and objs[j].type_code == 0x0F
    )
    doc.delete_objects([leaf])
    assert len(doc.parsed.page.objects) == len(tpl.records) - 1
    assert doc.template_status()[0] == "current"
    assert doc.cut_template_link() == len(tpl.records)
    assert doc.template_status()[0] == "none"
    assert len(doc.parsed.page.objects) == len(tpl.records)


def test_engine_roundtrip_template_mutators_offline(fx_install, tmp_path):
    """tools/engine_roundtrip.py 'template' (the live A/B check): its
    mutators apply ctgedit's template operations to a parsed page the way
    the harness writes it for the engine."""
    import importlib.util
    from ctgedit.format.page import parse_ctg_bytes

    spec = importlib.util.spec_from_file_location(
        "engine_roundtrip", os.path.join(os.path.dirname(__file__), "..", "tools", "engine_roundtrip.py")
    )
    ER = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ER)
    page = fx_install.page("FxTplLinked")
    for kind, target in (("switch", NOTITLE), ("new", NORMAL), ("new_unlinked", NORMAL), ("unlink", None)):
        mutate, labels, edited = ER.template_mutator(kind, page, target)
        p = parse_ctg(page)
        hs = mutate(p)
        data = writer.serialize_ctg(p.container, p.page, p.attributes)
        back = parse_ctg_bytes(data)
        assert _payloads(back.page.objects) == _payloads(edited.page.objects), kind
        assert back.page.link_table_raw == edited.page.link_table_raw
        assert len(hs) == len(edited.page.objects) and set(labels) >= set(hs)
    with pytest.raises(ValueError):
        ER.template_mutator("bogus", page)


def _engine_roundtrip():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "engine_roundtrip", os.path.join(os.path.dirname(__file__), "..", "tools", "engine_roundtrip.py")
    )
    ER = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ER)
    return ER


def _geo(po):
    gh = G.parse_geom_header(po.body_raw)
    return tuple(gh.anchor), tuple(gh.rect1), tuple(gh.rect2)


#: the free DummyMarker of normal@XGA (tools/make_fixtures.TPL_MARKER_RECT): its
#: element extent
MARKER_GEO = ((700, 118), (700, 100, 803, 119), (700, 100, 803, 119))


def test_template_operations_pin_ctgedits_output(fx_install):
    """Pins of ctgedit's own template operations."""
    ER = _engine_roundtrip()
    page = fx_install.page("FxTplLinked")
    empty = ((0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
    assert [_geo(po) for po in parse_ctg(page).page.objects if po.type_code == TP.DUMMY_TC] == [empty]
    _m, _l, linked = ER.template_mutator("new", page, NORMAL)
    assert [_geo(po) for po in linked.page.objects if po.type_code == TP.DUMMY_TC] == [MARKER_GEO]
    _m, _l, unl = ER.template_mutator("new_unlinked", page, NORMAL)
    assert [_geo(po) for po in unl.page.objects if po.type_code == TP.DUMMY_TC] == [MARKER_GEO]
    _m, _l, cut = ER.template_mutator("unlink", page)
    names = [n for _g, n in links.parse_link_table(cut.page.link_table_raw)[1]]
    assert not any(n.lower().startswith("fx_style.") for n in names)
    assert [n.split("#")[0] for n in names] == ["fxgenie.fx_value.<ctm", "fxgenie.fx_pump.<ctm"]
    dm = [po for po in cut.page.objects if po.type_code == TP.DUMMY_TC]
    assert [_geo(po) for po in dm] == [empty]
    assert GP.decode(dm[0]).cls[:12] == bytes(12)
    objs = cut.page.objects
    menu = next(
        k for k, po in enumerate(objs) if ("__AN_NAME__", "FxTplMenu") in GP.decode(po).entries[0][1].pairs
    )
    r = GP.decode(objs[menu])
    assert (r.an, r.name) == ("6", "FxTplMenu")
    assert r.entries[0][2].pairs == [] and r.entries[1][1].pairs == []
    text = GP.decode(objs[menu + 1])
    assert text.entries[0][1].pairs == [("TXT_OFF", "@()")]
    assert [bg.pairs for bg in text.entries[1]] == [[("Description", "\n")], [], []]
    assert [po.an_str for po in objs[menu + 1 : GP.subtree_end(objs, menu)]] == ["0", "1"]


def test_cut_link_drops_a_template_parameter_level(fx_install):
    """Cut Link drops a template parameter level."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxTplLinked"))
    res = doc._resolver()
    objs = doc.parsed.page.objects
    ((a, b, ref),) = TP.template_roots(objs)
    prefix = objs[a:b]
    copy_ = TP.unlinked_copy(prefix, TP.resolve_template(res, ref), res)
    menu = next(k for k, po in enumerate(copy_) if GP.decode(po).name == "FxTplMenu")
    members = range(menu + 1, GP.subtree_end(copy_, menu))

    def level():
        return [GP.Bag([("FxArea", "\n")]), GP.Bag([("FxArea", "North")]), GP.Bag([])]

    given, before = [], []
    for k, po in enumerate(copy_):
        r = GP.decode(po)
        if not po.subtype_size & 0x8 or not r.entries:
            given.append(po)
            before.append(None)
            continue
        if k == menu:
            # its value held by a typed binding too (Example Genies' template genies)
            r.entries[1][2] = GP.Bag([("Description", "Fx Menu")], r.entries[1][2].ov)
        elif k not in members and k:
            r.entries[0][2] = GP.Bag([("TXT_OFF", "%FxArea%")], r.entries[0][2].ov)
        before.append([[bg.pairs for bg in lv] for lv in r.entries])
        r.entries = r.entries + [level()]
        given.append(GP.to_po(GP.encode(r)))
    out = TP._cut_levels(given, prefix, res, set(), "FxTplLinked")
    assert len(out) == len(given)
    for k, po in enumerate(out):
        if before[k] is None:
            continue
        r = GP.decode(po)
        got = [[bg.pairs for bg in lv] for lv in r.entries]
        if k == 0:
            assert got == before[k] + [[bg.pairs for bg in level()]], k
        elif k == menu:
            assert got[0][2] == [] and got[1][1] == [("Description", "Fx Menu")], k
            assert got[1][2] == [] and len(got) == 2, k
            assert r.an == prefix[k].an_str
        elif k in members:
            assert got == before[k], k
        else:
            assert got == [before[k][0][:2] + [[]]], k


def test_cut_link_keeps_the_element_key_order():
    """A member of a genie the copy still links takes the element record's key order
    (keys the element lacks follow in the page's order), with the page bag's size word."""
    page = GP.Bag([("TXT_OFF", "0"), ("FxExtra", "1"), ("VISIBLE", "FX_PUMP1")], 7)
    el = GP.Bag([("visible", "%Tag%"), ("TXT_OFF", "0")], 3)
    got = TP._in_order(page, el)
    assert got.pairs == [("VISIBLE", "FX_PUMP1"), ("TXT_OFF", "0"), ("FxExtra", "1")]
    assert got.ov == 7


def _same_records(ours, eng, what):
    """Record for record the same, up to the bag size words: type code, child count,
    dwFlags, geometry and every bag's pairs."""
    assert len(ours) == len(eng), what
    for k, (a, b) in enumerate(zip(ours, eng)):
        assert (a.type_code, a.class_flag, a.subtype_size) == (b.type_code, b.class_flag, b.subtype_size), (
            what,
            k,
        )
        if G.parse_geom_header(a.body_raw) is not None:
            assert _geo(a) == _geo(b), (what, k)
        assert [bg.pairs for bg in a.bags] == [bg.pairs for bg in b.bags], (what, k)


def _link_names(page):
    return [links.strip_stamp(n) for _g, n in (links.parse_link_table(page.link_table_raw) or ("", []))[1]]


@pytest.mark.golden
def test_template_operations_match_the_live_engine(fx_install, new_project):
    """Template operations match CtDraw."""
    from ctgedit.editing import EditableDocument

    for unit, linked in (("linked", True), ("unlinked", False)):
        want = parse_ctg(fxinstall.oracle("newpage", unit, "page.ctg"))
        eng = want.page
        doc = EditableDocument.new_from_template(
            NORMAL,
            os.path.join(new_project, "FxOracleNew%s.ctg" % unit.title()),
            linked=linked,
            register=False,
        )
        _same_records(doc.parsed.page.objects, eng.objects, unit)
        assert _link_names(doc.parsed.page) == _link_names(eng), unit
        at, wat = doc.parsed.attributes, want.attributes
        assert (at.bg_color0, at.bg_color1) == (wat.bg_color0, wat.bg_color1), unit
        assert at.list1.pairs == wat.list1.pairs, unit
        assert at.list2.pairs == wat.list2.pairs, unit
        assert [_geo(po) for po in eng.objects if po.type_code == TP.DUMMY_TC] == [MARKER_GEO], unit


def _cut_page(fx_install):
    ER = _engine_roundtrip()
    return ER.template_mutator("unlink", fx_install.page("FxTplLinked"))[2]


@pytest.mark.golden
def test_cut_link_keeps_the_structure_ctdraws_cut_keeps(fx_install):
    """Cut Link keeps the structure CtDraw's cut keeps."""
    cut = _cut_page(fx_install)
    eng = parse_ctg(fxinstall.oracle("newpage", "cut_link", "page.ctg")).page

    def shape(objs):
        return [(po.type_code, po.class_flag, po.subtype_size, G.get_handle(po)) for po in objs]

    assert shape(cut.page.objects) == shape(eng.objects)
    menu = next(
        k
        for k, po in enumerate(eng.objects)
        if ("__AN_NAME__", "FxTplMenu") in [kv for bg in po.bags for kv in bg.pairs]
    )
    assert GP.subtree_end(eng.objects, menu) - menu == 3
    for k, (a, b) in enumerate(zip(cut.page.objects, eng.objects)):
        if G.parse_geom_header(a.body_raw) is not None:
            assert _geo(a) == _geo(b), k
        assert [bg.pairs for bg in a.bags] == [bg.pairs for bg in b.bags], k
        ra, rb = GP.decode(a), GP.decode(b)
        assert (ra.name, ra.cls) == (rb.name, rb.cls), k
        if a.class_flag and a.subtype_size & 0x201 == 0x201:
            assert a.an_str == b.an_str, k
    assert eng.objects[menu].an_str == "6"
    assert [_geo(po) for po in eng.objects if po.type_code == TP.DUMMY_TC] == [
        ((0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
    ]
    assert _link_names(cut.page) == _link_names(eng) == ["fxgenie.fx_value.<ctm", "fxgenie.fx_pump.<ctm"]


@pytest.mark.golden
def test_cut_link_is_ctdraws_cut_after_its_own_resave(fx_install):
    """Our Cut Link equals CtDraw's once CtDraw has resaved the page."""
    from ctgedit.editing import EditableDocument

    eng = parse_ctg(fxinstall.oracle("newpage", "cut_link", "reopened.ctg")).page
    doc = EditableDocument.open(fx_install.page("FxTplLinked"))
    doc.cut_template_link()
    for what, objs in (("cut_link", doc.parsed.page.objects), ("cut_link saved", doc.saved_form())):
        _same_records(objs, eng.objects, what)
        assert [po.an_str for po in objs] == [po.an_str for po in eng.objects], what
        for k, (a, b) in enumerate(zip(objs, eng.objects)):
            ra, rb = GP.decode(a), GP.decode(b)
            assert (ra.name, ra.cls) == (rb.name, rb.cls), (what, k)
    assert _link_names(doc.parsed.page) == _link_names(eng)


def test_shipped_projects_and_element_names():
    assert TP.shipped_project(r"C:\x\User\Include\fx_style.ctt") == "Include"
    assert TP.shipped_project(r"C:\x\User\FxMain\mine.ctt") is None
    assert TP.split_element("pagecontent@-XGA") == ("pagecontent", "XGA", False)
    assert TP.split_element("normal@XGA") == ("normal", "XGA", True)
    assert TP.split_element("plain") == ("plain", "", True)
    assert TP.element_for("normal", "XGA", True) == "normal@XGA"
    assert TP.element_for("blank", "4K", False) == "blank@-4K"


# ----------------------------------------------------------------- dialogs
def test_use_template_dialog_lists_the_include_chain_templates(fx_project):
    _app()
    from ctgedit.ui.dialog.dialogs import UseTemplateDialog

    main = fx_project("FxMain")
    dlg = UseTemplateDialog(
        main, {"style": "fx_style", "resolution": "XGA", "title_bar": True, "background": 0xD8D8D8}
    )
    assert "fx_style" in [dlg.cb_style.itemText(i) for i in range(dlg.cb_style.count())]
    assert dlg.cb_style.currentText() == "fx_style"
    labels = [dlg.list.item(i).text() for i in range(dlg.list.count())]
    assert "normal\nXGA" in labels
    assert dlg.template_ref() == NORMAL
    # fx_style has normal@XGA with and without a title bar: the choice is open
    assert dlg.chk_titlebar.isEnabled() and dlg.chk_titlebar.isChecked()
    dlg.chk_titlebar.setChecked(False)
    assert dlg.template_ref() == NOTITLE
    dlg.chk_titlebar.setChecked(True)
    assert dlg.linked()
    lib, element = dlg.template_element()
    assert element == "normal@XGA" and lib.lower().endswith("fx_style.ctt")
    # 'All resolutions' lists the style's other templates too
    dlg.cb_res.setCurrentIndex(dlg.cb_res.findData(""))
    assert {"normal\nXGA", "blank\nSVGA"} <= {dlg.list.item(i).text() for i in range(dlg.list.count())}
    dlg._edit()
    assert dlg.edit_requested


def test_page_defaults_are_the_projects_properties(fx_project):
    """CtDraw's Page Defaults are PROJECT properties in PROJECT.CIT (Style /
    Res index / ShowTitle / BackgroundColor): FxMain = fx_style, XGA, title bar,
    d8d8d8; FxSim = fx_style, SVGA, title bar, e8e8e8."""
    from ctgedit.io import projectcit
    from ctgedit.ui.dialog import dialogs as D

    main, sim = fx_project("FxMain"), fx_project("FxSim")
    assert projectcit.page_defaults(main) == {
        "style": "fx_style",
        "resolution": "XGA",
        "title_bar": True,
        "background": 0xD8D8D8,
    }
    assert projectcit.page_defaults(sim) == {
        "style": "fx_style",
        "resolution": "SVGA",
        "title_bar": True,
        "background": 0xE8E8E8,
    }
    d = D.page_defaults(sim)
    assert d["source"] == "project" and d["style"] == "fx_style"
    # New Page in FxSim starts in the project's style + resolution
    _app()
    dlg = D.UseTemplateDialog(sim, d)
    assert dlg.cb_style.currentText().lower() == "fx_style"
    assert dlg.cb_res.currentData() == "SVGA"
    assert dlg.template_ref() == BLANK


def test_page_defaults_dialog_writes_the_project_file(fx_project, tmp_path):
    """OK writes the four Page Defaults streams of the project's PROJECT.CIT
    in place (CtDraw's project-info set) and nothing else of the file."""
    _app()
    from ctgedit.io import projectcit
    from ctgedit.ui.dialog import dialogs as D

    main = fx_project("FxMain")
    proj = tmp_path / "Proj"
    proj.mkdir()
    shutil.copy2(projectcit.find(main), proj / "PROJECT.CIT")
    before = projectcit.read_streams(str(proj / "PROJECT.CIT"))
    size = os.path.getsize(proj / "PROJECT.CIT")
    dlg = D.PageDefaultsDialog(str(proj), catalog=D.TemplateCatalog(main))
    assert dlg.cb_style.currentText() == "fx_style" and dlg.resolution() == "XGA"
    assert dlg.chk_titlebar.isChecked()
    assert not dlg.preview.pixmap().isNull()
    assert "fx_alt" in [dlg.cb_style.itemText(i) for i in range(dlg.cb_style.count())]
    dlg.cb_style.setCurrentText("fx_alt")
    dlg.set_resolution("4K")  # the SA spelling of UHD4K
    dlg.chk_titlebar.setChecked(False)
    dlg._background = 0x123456
    dlg._ok()
    assert dlg.result() == QtAccepted() and dlg.stored_in == "project"
    assert projectcit.page_defaults(str(proj)) == {
        "style": "fx_alt",
        "resolution": "UHD4K",
        "title_bar": False,
        "background": 0x123456,
    }
    after = projectcit.read_streams(str(proj / "PROJECT.CIT"))
    assert set(after) == set(before)
    assert {k for k in after if after[k] != before[k]} == {"Style", "Res", "ShowTitle", "BackgroundColor"}
    assert os.path.getsize(proj / "PROJECT.CIT") == size
    d = D.page_defaults(str(proj))
    assert (d["source"], d["style"], d["resolution"]) == ("project", "fx_alt", "UHD4K")


def test_page_defaults_without_a_project_file(tmp_path, monkeypatch):
    """A folder without PROJECT.CIT: [CtExplore] DefaultTemplate* (what a new
    project gets) until the user saves defaults, then ctgedit's settings."""
    _app()
    from ctgedit.io import citect_config
    from ctgedit.ui.dialog import dialogs as D

    monkeypatch.setattr(
        citect_config,
        "template_defaults",
        lambda: {"style": "Fx_Style_A", "resolution": "XGA", "title_bar": False},
    )
    folder = tmp_path / "NotAProject"
    folder.mkdir()
    d = D.page_defaults(str(folder))
    assert (d["style"], d["resolution"], d["title_bar"], d["source"]) == (
        "Fx_Style_A",
        "XGA",
        False,
        "settings",
    )
    assert d["background"] == D.DEFAULT_PAGE_BACKGROUND
    assert (
        D.set_page_defaults(
            {"style": "fx_style", "resolution": "HD1080", "title_bar": True, "background": 0x123456},
            str(folder),
        )
        == "settings"
    )
    d = D.page_defaults(str(folder))
    assert (d["style"], d["resolution"], d["title_bar"], d["background"]) == (
        "fx_style",
        "HD1080",
        True,
        0x123456,
    )
    assert not os.listdir(folder)  # no project file invented


def QtAccepted():
    from PySide6 import QtWidgets

    return QtWidgets.QDialog.Accepted


def test_page_properties_appearance_previews_the_template(fx_page):
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    doc = EditableDocument.open(fx_page("FxTplLinked"))
    dlg = PagePropertiesDialog(doc, None)
    picker = dlg._tpl_picker  # the shared TemplatePicker
    pm = picker.preview.pixmap()
    assert pm is not None and not pm.isNull()
    assert "normal@XGA" in picker.preview.toolTip()
    picker.set_current("fx_style", "no_such_template@XGA")
    assert picker.is_missing() and picker.value() is None
    assert "(not found)" in picker.list.currentItem().text()
    assert picker.preview.toolTip() == ""


# ------------------------------------------------------------- main window
@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)


def test_new_page_command_creates_registered_page(fx_page, new_project, monkeypatch, win):
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog import dialogs as D
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.browser.new_mode import NewTarget

    target = os.path.join(new_project, "Created.ctg")
    # the New window's Page tab answers: the project's Page Defaults template, linked
    monkeypatch.setattr(
        PageBrowserDialog,
        "get_new",
        staticmethod(lambda *a, **k: NewTarget("page", target, "", NORMAL, True)),
    )
    monkeypatch.setattr(
        D,
        "page_defaults",
        lambda *_a, **_k: {
            "style": "fx_style",
            "resolution": "XGA",
            "title_bar": True,
            "background": 0xE5E5E5,
            "source": "project",
        },
    )
    # an open page in another tab keeps its editor state (hidden roots)
    win.set_document(EditableDocument.open(fx_page("FxShapes")))
    win._hidden_roots = {5}
    first = win.doc
    win._new_page()
    assert win.doc is not None and os.path.normcase(win.doc.path) == os.path.normcase(target)
    assert win.doc.template_status() == ("current", NORMAL)
    # written and registered by its first Save (not before)
    assert not os.path.exists(target) and win.doc.dirty
    assert win.save()
    assert Project(new_project).page_record("Created") is not None
    assert win.doc.parsed.page.thumbnail  # rendered on the registering save
    # (normal@XGA has a background of its own: it wins over the Page Defaults colour)
    assert (win.doc.parsed.attributes.bg_color0, win.doc.parsed.attributes.bg_color1) == (0xD8D8D8, 0xD8D8D8)
    assert len(win._docs) == 2 and win._docs[0]["doc"] is first
    assert win._docs[0].get("hidden") == {5}
    # the template commands apply to this page
    win._update_actions()
    assert win.act_cut_template_link.isEnabled() and win.act_edit_template.isEnabled()
    assert win._definition_of(0)[1] == "normal@XGA"
    opened = []
    monkeypatch.setattr(win, "_open_element", lambda *a: opened.append(a))
    win._edit_page_template()
    assert opened and opened[0][1] == "normal@XGA"
    # Cut Link (confirmed) unlinks the page
    from ctgedit.ui import messages

    monkeypatch.setattr(messages, "choose", lambda *a, **k: a[3][0])  # (Cut Link)
    win._sel = []
    win._cut_link()
    assert win.doc.template_status()[0] == "none"
    win._update_actions()
    assert not win.act_cut_template_link.isEnabled()


def test_saving_a_shipped_template_warns_first(fx_install, monkeypatch, win):
    from PySide6 import QtWidgets
    from ctgedit.ui.prefs.settings import app_settings

    class Doc:
        pass

    doc = Doc()
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QtWidgets.QMessageBox, "clickedButton", lambda self: None)
    shipped = os.path.join(fx_install.user, "Include", "fx_style.ctt")  # Include is shipped
    mine = fx_install.project("FxMain")
    assert not win._confirm_shipped_template(doc, shipped, True)  # cancelled
    assert win._confirm_shipped_template(doc, os.path.join(mine, "mine.ctt"), True)
    # a supplied SYMBOL / GENIE library warns too (not only templates)
    assert not win._confirm_shipped_template(doc, fx_install.file("Include", "internal.ctm"), True)
    assert win._confirm_shipped_template(doc, os.path.join(mine, "mine.ctm"), True)
    app_settings().setValue("opt/warn_shipped_template", False)
    assert win._confirm_shipped_template(doc, shipped, True)
