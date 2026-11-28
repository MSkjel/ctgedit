"""Copy, paste and duplicate of placed genies."""

import json
import os
import re
import shutil


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

#: the AN argument of fx_anlist's Cicode calls
ARRAY_CALL = re.compile(r"FxList(?:Show|Pos|Size)\((\d+)[,)]")
#: the page the list genie is placed on, and the page it is pasted onto
SOURCE, TARGET = "FxBig", "FxInputs"


def _open(path):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(path)


def _place_list(doc):
    """Place fxparts.fx_anlist at (100, 300): the root's index."""
    i = doc.place_library_element("fxparts", "fx_anlist", "ctm", (100, 300))
    assert i is not None
    return i


def _an(doc, i):
    from ctgedit.format import geometry as G

    return G.get_handle(doc.objects[i]) + 1


def _values(doc, root):
    """Every level value (b1) of the instance subtree: key -> set of values."""
    from ctgedit.format import genie_place as GP

    out = {}
    for j in doc.subtree(root):
        r = GP.decode(doc.objects[j])
        for e in r.entries or ():
            for k, v in e[1].pairs:
                out.setdefault(k, set()).add(v)
    return out


def _an_args(vals):
    """The AN arguments of the list genie's Cicode calls."""
    return {int(a) for vs in vals.values() for v in vs for a in ARRAY_CALL.findall(v)}


def _names(doc):
    from ctgedit.format import links

    t = links.parse_link_table(doc.parsed.page.link_table_raw) if doc.parsed.page.link_count else None
    return [n for _g, n in (t[1] if t else [])]


def _payloads(doc):
    from ctgedit.format import writer

    return [writer.object_payload(o) for o in doc.objects]


def _cmd_member(doc, root):
    """The member of the instance whose own bag holds the touch command (fx_anlist's
    button: CtDraw keeps a button's CMD in its own record)."""
    from ctgedit.format import genie_place as GP

    return next(
        j
        for j in doc.subtree(root)
        if any(k == "CMD" for k, _v in (GP.decode(doc.objects[j]).entries or [[None, ()]])[0][1].pairs)
    )


def _value_genie(doc):
    """FxGenies' FxValue: an fxgenie.fx_value instance (no %AN% in it)."""
    hits = doc.find_objects_by_name("FxValue")
    assert len(hits) == 1
    i = hits[0]
    assert any(k == i and el.lower() == "fx_value" for k, (_lib, el) in doc.genie_instances())
    return i


# --------------------------------------------------------------------------- %AN%
def test_duplicate_alarm_list_drives_its_own_an(fx_page, monkeypatch):
    """fxparts.fx_anlist: ArrayAN / CMD / MVY / SZY of the duplicate take the
    duplicate's AN; the copy is exactly what composing the genie with that AN
    gives (CtDraw's paste / page load), and the pg rows follow."""
    from ctgedit.io import pgregister

    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")
    d = _open(fx_page(SOURCE))
    i = _place_list(d)
    an0 = _an(d, i)
    v0 = _values(d, i)
    assert "FxListShow(%d);" % an0 in v0["CMD"]
    assert _an_args(v0) == {an0} and v0["ArrayAN"] == {str(an0)}
    before, links0 = _payloads(d), d.parsed.page.link_table_raw
    j = d.duplicate_objects([i])[0]
    an1 = _an(d, j)
    assert an1 != an0
    v1 = _values(d, j)
    assert "FxListShow(%d);" % an1 in v1["CMD"]
    assert _an_args(v1) == {an1} and v1["ArrayAN"] == {str(an1)}
    # (the nested genie's constant AN: test_a_nested_constant_an_stays_in_the_duplicate)
    assert _payloads(d)[: len(before)] == before  # the original is untouched
    # (composing the copy again changes nothing: test_a_duplicate_is_what_composing_gives)
    rows = pgregister.page_rows(d.serialize(), SOURCE)
    # (fx_anlist's command is a button's: its rows are pgbutton's; the bar's are pgdynobj's)
    cmds = {r.get("CMD") for r in rows["pgbutton"]}
    assert {"FxListShow(%d);" % an0, "FxListShow(%d);" % an1} <= cmds
    mvys = {r.get("MVY") for r in rows["pgdynobj"]}
    assert {"FxListPos(%d)" % an0, "FxListPos(%d)" % an1} <= mvys
    d.undo()
    assert _payloads(d) == before and d.parsed.page.link_table_raw == links0


def test_a_nested_constant_an_stays_in_the_duplicate(fx_page):
    """A nested genie placed with a constant AN parameter (fx_anconst, AN 10) keeps it
    in the instance and in its duplicate: only the outer genie's %AN% values move."""
    d = _open(fx_page(SOURCE))
    i = _place_list(d)
    v0 = _values(d, i)
    j = d.duplicate_objects([i])[0]
    v1 = _values(d, j)
    assert v1["AN"] == v0["AN"] == {"10"}  # a nested genie's constant parameter


def test_a_duplicate_is_what_composing_gives(fx_page):
    """The duplicate of fx_anlist is exactly what composing the genie from its element
    with the copy's own AN gives (CtDraw's paste / page load): composing it again
    changes nothing."""
    d = _open(fx_page(SOURCE))
    i = _place_list(d)
    j = d.duplicate_objects([i])[0]
    assert d._recompose_genie(j, {}) is False


#: the values of the bound instance a duplicate changes: the root's and three members'
#: bound 'AN' (the frame, the button, the bar), ArrayAN, the button's command, MVY and SZY
#: (the nested genie's text shows its own constant AN 10, which a duplicate keeps)
BOUND_CHANGED = 8


def test_duplicate_of_an_instance_with_a_bound_an(fx_page):
    """Duplicating an instance with a bound AN."""
    from ctgedit.format import genie_place as GP

    d = _open(fx_page(SOURCE))
    root = _place_list(d)
    d.set_object_name(root, "FxSoeList")
    an0 = _an(d, root)
    assert d._recompose_genie(root, {"AN": str(an0)}) is True
    r0 = GP.decode(d.objects[root])
    assert r0.entries[-1][1].get("AN") == str(an0)
    before = _payloads(d)
    j = d.duplicate_objects([root])[0]
    an1 = _an(d, j)
    assert GP.decode(d.objects[j]).entries[-1][1].get("AN") == str(an1)
    old = re.compile(r"(?<!\d)%d(?!\d)" % an0)
    changed = 0
    renamed = False
    for a, b in zip(d.subtree(root), d.subtree(j)):
        ra, rb = GP.decode(d.objects[a]), GP.decode(d.objects[b])
        assert [[list(dict(bg.pairs)) for bg in e] for e in ra.entries] == [
            [list(dict(bg.pairs)) for bg in e] for e in rb.entries
        ]  # same keys, order
        for ea, eb in zip(ra.entries, rb.entries):
            for ba, bb in zip(ea, eb):
                for (k, va), (_k, vb) in zip(ba.pairs, bb.pairs):
                    if va != vb and a == root and k == "__AN_NAME__":
                        assert (va, vb) == ("FxSoeList", "FxSoeList_1")
                        renamed = True
                    elif va != vb:
                        assert old.sub(str(an1), va) == vb, (k, va, vb)
                        changed += 1
    assert renamed
    assert changed == BOUND_CHANGED
    assert _an_args(_values(d, j)) == {an1}
    assert _payloads(d)[: len(before)] == before


def test_a_genie_without_an_copies_verbatim(fx_page):
    """No %AN% in the instance (FxGenies' FxValue): the copy's bags are the original's,
    but for the instance's name, which the duplicate makes unique (FxValue ->
    FxValue_1) like CtDraw's paste."""
    from ctgedit.format import genie_place as GP

    d = _open(fx_page("FxGenies"))
    root = _value_genie(d)
    j = d.duplicate_objects([root])[0]

    def renamed(entries, old, new):
        return [
            [[(k, new if (k == "__AN_NAME__" and v == old) else v) for k, v in bg.pairs] for bg in e]
            for e in entries
        ]

    for a, b in zip(d.subtree(root), d.subtree(j)):
        ea, eb = GP.decode(d.objects[a]).entries, GP.decode(d.objects[b]).entries
        if a == root:
            assert renamed(ea, "FxValue", "FxValue_1") == renamed(eb, "", "")
            assert d._stored_name(b) == "FxValue_1"
        else:
            assert ea == eb


def test_a_value_the_old_an_does_not_reproduce_is_left_alone(fx_page):
    """resubstitute_an replaces a value only when the old AN reproduces it: a
    member value edited away from its template keeps its text, the others
    follow the new AN; no library is needed."""
    from ctgedit.format import genie_place as GP

    d = _open(fx_page(SOURCE))
    i = _place_list(d)
    an0 = _an(d, i)
    recs = [d.objects[j] for j in d.subtree(i)]
    k = d.subtree(i).index(_cmd_member(d, i))
    r = GP.decode(recs[k])
    r.entries[0][1].set("CMD", "Custom(%d)" % an0)
    recs[k] = GP.to_po(GP.encode(r))
    out = GP.resubstitute_an(recs, an0, 99)
    assert out is not None and len(out) == len(recs)
    assert GP.decode(out[k]).entries[0][1].get("CMD") == "Custom(%d)" % an0
    args = {
        int(a)
        for po in out
        for e in (GP.decode(po).entries or ())
        for _k, v in e[1].pairs
        for a in ARRAY_CALL.findall(v)
    }
    assert args == {99}
    assert GP.resubstitute_an(recs, an0, an0) is None


# --------------------------------------------------------------------------- link table
def test_paste_onto_another_page_lists_the_element_and_its_an(fx_page, monkeypatch):
    """The clipboard carries the source page's entries; each paste gets its own
    AN; the element is listed once; undo takes the entry out again."""
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")
    src = _open(fx_page(SOURCE))
    i = _place_list(src)
    entry = next(n for n in _names(src) if "fx_anlist" in n.lower())
    payload = src.copy_payload([i])
    assert [e[1] for e in json.loads(payload)["links"]] == [entry]
    dst = _open(fx_page(TARGET))
    n0, lt0 = len(dst.objects), dst.parsed.page.link_table_raw
    assert not any("fx_anlist" in n.lower() for n in _names(dst))
    first = dst.paste_payload(payload)
    second = dst.paste_payload(payload, offset=30)
    for new in (first, second):
        r = new[0]
        assert "FxListShow(%d);" % _an(dst, r) in _values(dst, r)["CMD"]
        assert _an_args(_values(dst, r)) == {_an(dst, r)}
    assert _an(dst, first[0]) != _an(dst, second[0])
    assert [n for n in _names(dst) if "fx_anlist" in n.lower()] == [entry]
    dst.undo()
    dst.undo()
    assert len(dst.objects) == n0 and dst.parsed.page.link_table_raw == lt0


#: FxGenies' FxValue: its AN and its handles (the generator places it after 38 records)
VALUE_AN, VALUE_HANDLES = 39, [38, 39, 40]


def test_paste_onto_another_page_keeps_free_handles_like_ctdraw(fx_page):
    """A paste onto another page keeps free handles, like CtDraw."""
    from ctgedit.format import genie_place as GP

    src = _open(fx_page("FxGenies"))
    gi = _value_genie(src)
    assert (src.objects[gi].subtype_size & 0x201) == 0x201
    assert "fx_value" in (GP.decode(src.objects[gi]).link or "").lower()
    handles = [_an(src, j) - 1 for j in src.subtree(gi)]
    assert _an(src, gi) == VALUE_AN and handles == VALUE_HANDLES
    payload = src.copy_payload([gi])
    dst = _open(fx_page("FxShapes"))
    first = dst.paste_payload(payload)
    assert [_an(dst, j) - 1 for j in first] == handles
    second = dst.paste_payload(payload)
    a2 = _an(dst, second[0])
    assert a2 != VALUE_AN and all(_an(dst, j) - 1 not in handles for j in second)


def test_paste_keeps_a_bound_an_on_free_handles(fx_page):
    """A paste keeps a bound AN where its handles are free."""
    from ctgedit.format import genie_place as GP

    src = _open(fx_page(SOURCE))
    root = _place_list(src)
    an0 = _an(src, root)
    assert src._recompose_genie(root, {"AN": str(an0)}) is True
    handles = [_an(src, j) - 1 for j in src.subtree(root)]
    payload = src.copy_payload([root])
    dst = _open(fx_page(TARGET))
    first = dst.paste_payload(payload)
    assert [_an(dst, j) - 1 for j in first] == handles
    assert GP.decode(dst.objects[first[0]]).entries[-1][1].get("AN") == str(an0)
    assert _an_args(_values(dst, first[0])) == {an0}
    second = dst.paste_payload(payload)
    a2 = _an(dst, second[0])
    assert a2 != an0
    assert GP.decode(dst.objects[second[0]]).entries[-1][1].get("AN") == str(a2)
    assert _an_args(_values(dst, second[0])) == {a2}


def test_paste_onto_a_page_without_a_link_table_keeps_the_derive(fx_page, monkeypatch):
    """fxparts.fx_anlist placed on FxBig, copied and pasted onto FxShapes (no link
    table): the incremental re-derive must equal a full one."""
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")
    src = _open(fx_page(SOURCE))
    payload = src.copy_payload([_place_list(src)])
    dst = _open(fx_page("FxShapes"))
    assert not dst.parsed.page.link_count
    assert dst.paste_payload(payload)


def test_a_payload_without_links_still_lists_the_element(fx_page):
    """A clipboard written before the payload carried 'links' / 'page' (or by
    another tool) still pastes: the copy gets its own AN and the page lists
    the element with its CURRENT entry, found through the include chain."""
    src = _open(fx_page(SOURCE))
    i = _place_list(src)
    entry = next(n for n in _names(src) if "fx_anlist" in n.lower())
    data = json.loads(src.copy_payload([i]))
    legacy = json.dumps({"ctgedit_objects": data["ctgedit_objects"]})
    dst = _open(fx_page(TARGET))
    r = dst.paste_payload(legacy)[0]
    assert _an_args(_values(dst, r)) == {_an(dst, r)}
    assert [n for n in _names(dst) if "fx_anlist" in n.lower()] == [entry]


def test_paste_keeps_page_values_and_moves_the_an(fx_page):
    """A template that uses %page% AND %AN%: the value keeps the page it was
    composed on (the clipboard carries it) and takes the paste's own AN."""
    from ctgedit.format import genie_place as GP

    src = _open(fx_page(SOURCE))
    i = _place_list(src)
    an0 = _an(src, i)
    k = _cmd_member(src, i)
    r = GP.decode(src.objects[k])
    r.entries[0][2].set("CMD", 'Show("%page%", %AN%)')
    r.entries[0][1].set("CMD", 'Show("%s", %d)' % (src.page.name, an0))
    src.parsed.page.objects[k] = GP.to_po(GP.encode(r))
    payload = src.copy_payload([i])
    assert json.loads(payload)["page"] == src.page.name
    dst = _open(fx_page(TARGET))
    dst.paste_payload(payload)  # takes the source handles (free on this page)
    new = dst.paste_payload(payload)  # ... so this one gets new ones
    an1 = _an(dst, new[0])
    assert an1 != an0
    j = _cmd_member(dst, new[0])
    cmds = {GP.decode(dst.objects[j]).entries[0][1].get("CMD")}
    assert cmds == {'Show("%s", %d)' % (src.page.name, an1)}


def test_update_pages_sees_a_pasted_copy_of_an_out_of_date_instance(fx_page):
    """The copied instance was composed from the version its SOURCE page lists:
    a stale stamp travels with it, so Update Pages finds the pasted genie."""
    from ctgedit.format.symbols import SymbolResolver
    from ctgedit.io import updatepages

    src = _open(fx_page(SOURCE))
    i = _place_list(src)
    data = json.loads(src.copy_payload([i]))
    g, n = data["links"][0]
    base, stamp = n.rsplit("#", 1)
    data["links"][0] = [g, "%s#%08x" % (base, int(stamp, 16) ^ 1)]  # an older version
    dst = _open(fx_page(TARGET))
    cur = updatepages.Current(SymbolResolver.for_page(dst.path))
    dst.paste_payload(json.dumps(data))
    dst.save()
    assert updatepages.stale_elements(dst.path, cur) == {("fxparts", "fx_anlist")}


def _pasting_project(fx_install, page="FxShapes"):
    """A project of its own (FxPaste, no page tables: every page in it counts) that
    includes FxLib and FxStyle, holding a copy of FxMain's ``page``."""
    proj = fx_install.new_project("FxPaste", includes=("FxLib", "FxStyle"), tables=False)
    dst = os.path.join(proj, page + ".ctg")
    shutil.copyfile(fx_install.page(page), dst)
    return proj, dst


def test_update_pages_refreshes_a_genie_pasted_from_another_page(fx_install):
    """FxGenies' FxValue (fxgenie.fx_value) pasted onto a copy of FxShapes in a
    project of its own (the page placed no fxgenie genie): the page lists the
    element, and after the element is edited + saved, Update Pages re-composes the
    pasted instance."""
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.format import writer
    from ctgedit.io import updatepages

    proj, page = _pasting_project(fx_install)
    src = _open(fx_install.page("FxGenies"))
    root = _value_genie(src)
    entry = next(n for n in _names(src) if "fx_value" in n.lower())
    dst = _open(page)
    assert not any("fxgenie" in n.lower() for n in _names(dst))
    k = dst.paste_payload(src.copy_payload([root]))[0]
    assert entry in _names(dst)
    dst.save()
    rep = updatepages.update_pages(proj)
    assert "FxShapes" not in rep.updated  # the element did not change
    pasted = [writer.object_payload(o) for o in dst.objects[k : k + len(src.subtree(root))]]
    el = ElementDocument.open_element(fx_install.file("FxLib", "fxgenie.ctm"), "fx_value")
    roots = [i for i, o in enumerate(el.page.objects) if o.parent is None]
    el.move_objects(el.subtree(roots[-1]), 3, 2)
    el.save()
    rep = updatepages.update_pages(proj)
    assert rep.updated == ["FxShapes"] and rep.instances == 1, rep
    again = EditableDocument.open(dst.path)
    now = [writer.object_payload(o) for o in again.objects[k : k + len(pasted)]]
    assert now != pasted
    assert entry not in _names(again)  # the entry now carries the new stamp
    assert any(n.lower().startswith(entry.lower().split("#")[0]) for n in _names(again))


def test_pasted_linked_symbols_are_listed_like_ctdraw_lists_them(fx_page):
    """Pasted linked symbols are listed the way CtDraw lists them."""
    from ctgedit.format import genie_place as GP, links

    src = _open(fx_page("FxSymbols"))
    roots = [
        i
        for i, po in enumerate(src.objects)
        if src.page.objects[i].parent is None
        and po.subtype_size & 0x1
        and po.type_code in (0x07, 0x08)
        and links.strip_stamp(GP.decode(po).link or "").lower().endswith(".<ctl")
    ]
    forms = [src.objects[i].type_code for i in roots]
    assert len(roots) == 8 and forms.count(0x07) == 2 and forms.count(0x08) == 6, forms
    carried = {links.strip_stamp(n).lower(): n for n in _names(src)}
    dst = _open(fx_page("FxShapes"))
    for _ in range(2):
        new = dst.paste_payload(src.copy_payload(roots))
    names = _names(dst)
    refs = {
        links.strip_stamp(GP.decode(dst.objects[j]).link).lower()
        for j in new
        if dst.objects[j].subtype_size & 0x1
    }
    assert refs and all(carried[r] in names for r in refs)
    base = [links.strip_stamp(n).lower() for n in names if not n.startswith("\\")]
    assert len(base) == len(set(base))


def test_a_symbol_drawn_to_a_box_is_listed(fx_page, monkeypatch):
    """The Symbol tool's dragged box (CtDraw's Paste Symbol + resize) creates
    a linked record: the page lists its element at once, and undo takes the entry out with
    the record. FxLib's
    fxmany.fx_tank_tall drawn on FxBig."""
    from ctgedit.format import genie_place as GP, links

    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")
    d = _open(fx_page("FxBig"))
    n0, lt0 = len(d.objects), d.parsed.page.link_table_raw
    assert not any("fx_tank_tall" in n.lower() for n in _names(d))
    i = d.create_object("symbol", (10, 10, 60, 90), symbol=("fxmany", "fx_tank_tall", "ctl"))
    assert d.objects[i].subtype_size & 0x1
    ref = links.strip_stamp(GP.decode(d.objects[i]).link).lower()
    listed = [n for n in _names(d) if links.strip_stamp(n).lower() == ref]
    assert len(listed) == 1
    d.duplicate_objects([i])
    assert [n for n in _names(d) if links.strip_stamp(n).lower() == ref] == listed
    d.undo()
    d.undo()
    assert len(d.objects) == n0 and d.parsed.page.link_table_raw == lt0


def test_element_documents_keep_their_copies_as_copied(fx_install):
    """Element documents keep their copies as copied."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import links

    src = _open(fx_install.page("FxGenies"))
    root = _value_genie(src)
    el = ElementDocument.open_element(fx_install.file("FxLib", "fxgenie.ctm"), "fx_pump")
    lt0 = el.parsed.page.link_table_raw
    assert el.paste_payload(src.copy_payload([root]))
    want = [n for n in _names(src) if links.strip_stamp(n).lower() == "fxgenie.fx_value.<ctm"]
    assert len(want) == 1
    assert [n for n in _names(el) if not n.startswith("\\")] == want
    assert not any(n.startswith("\\") for n in _names(el))
    el.undo()
    assert el.parsed.page.link_table_raw == lt0
