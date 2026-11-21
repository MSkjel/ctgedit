"""Page templates: recompose, re-apply and switch."""

import os

import fxinstall
import pytest

from ctgedit.format import template_place as TP
from ctgedit.format import writer
from ctgedit.format.page import parse_ctg
from ctgedit.format.symbols import SymbolResolver

#: (project, page) of the fixture pages whose template is current
CURRENT = [
    ("FxMain", n)
    for n in (
        "FxTplLinked",
        "FxTplNoTitle",
        "FxTplBlank",
        "FxTplMarker",
        "FxTplSets",
        "FxTplFlash",
        "FxHd1080",
    )
]
CURRENT += [("FxSa", "FxSaContent")]
#: the page saved on a template that changed afterwards
STALE = "FxTplStale"


def _payloads(objs):
    return [writer.object_payload(po) for po in objs]


def _recomposes(path):
    """The page at ``path`` re-composes byte-exact from its template, reads 'current' and
    Update Template does nothing."""
    from ctgedit.editing import EditableDocument

    parsed = parse_ctg(path)
    r = TP.recompose(
        parsed.page.objects,
        SymbolResolver.for_page(path),
        page_name=os.path.splitext(os.path.basename(path))[0],
    )
    assert r["objects"] is not None
    assert _payloads(r["objects"]) == _payloads(parsed.page.objects)
    doc = EditableDocument.open(path)
    assert doc.template_status()[0] == "current"
    assert doc.update_template() == 0  # nothing to do, no undo step
    assert not doc.can_undo()


@pytest.mark.parametrize("project,page", CURRENT)
def test_up_to_date_pages_recompose_byte_exact(fx_page, project, page):
    _recomposes(fx_page(page, project))


@pytest.mark.golden
@pytest.mark.parametrize("project,page", CURRENT)
def test_ctdraws_saves_of_up_to_date_pages_recompose_byte_exact(fx_page, project, page):
    """CtDraw's PageOpen + PageSave of each page (oracle step 'pages'), put in place of the
    page in the test's copy (so its include chain resolves), re-composes byte-exact."""
    data = fxinstall.oracle_bytes("pages", "%s/%s" % (project, page), "page.ctg")
    path = fx_page(page, project)
    with open(path, "wb") as fh:
        fh.write(data)
    _recomposes(path)


def test_update_template_reapplies_a_changed_template(fx_page):
    """A template changed (a group removed, a caption changed) after this page was
    saved (FxTplStale on fx_alt stale@-XGA); Update Pages re-applies it and leaves the
    page's own objects (FxOwn) byte-identical."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page(STALE))
    before = _payloads(doc.parsed.page.objects)
    roots_before = TP.template_roots(doc.parsed.page.objects)
    a, b, _ref = roots_before[0]
    own_before = before[:a] + before[b:]
    assert doc.template_status()[0] == "stale"
    assert doc.update_template() > 0
    assert doc.template_status()[0] == "current"
    after = _payloads(doc.parsed.page.objects)
    a2, b2, _r = TP.template_roots(doc.parsed.page.objects)[0]
    assert after[:a2] + after[b2:] == own_before  # user objects untouched
    texts = [o.style.text for o in doc.page.objects[a2:b2] if o.style.text]
    assert "FxAfter" in texts and "FxBefore" not in texts
    assert b2 - a2 < b - a  # (the template's group went)
    assert len(doc.find_objects_by_name("FxOwn")) == 1
    # the result is stable: re-composing again changes nothing
    r = TP.recompose(doc.parsed.page.objects, doc._resolver(), page_name=doc.page.name)
    assert _payloads(r["objects"]) == after
    doc.undo()
    assert _payloads(doc.parsed.page.objects) == before
    assert doc.template_status()[0] == "stale"


def test_the_worker_applies_the_template_as_the_inline_step_does(fx_page):
    """The open's template step runs in the genie worker (genie_worker.compute with
    ``template``): its answer, applied as record ops, gives the records update_template
    gives, as one undo step with the step's label."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.jobs.genie_jobs import apply_answer

    STALE = fx_page("FxTplStale")
    inline = EditableDocument.open(STALE)
    assert inline.update_template() > 0
    want = _payloads(inline.parsed.page.objects)
    doc = EditableDocument.open(STALE)
    with open(STALE, "rb") as fh:
        data = fh.read()
    ans = GW.compute(STALE, data, {}, None, template=True)
    assert not ans.get("error") and ans["ops"]
    n = apply_answer(doc, ans, "template chrome re-applied")
    assert n and n > 0
    assert _payloads(doc.parsed.page.objects) == want
    assert doc.template_status()[0] == "current"
    assert doc._undo[-1].label == "template chrome re-applied"
    doc.undo()
    assert doc.template_status()[0] == "stale"


def test_change_template_relinks_the_page(fx_page, tmp_path):
    """FxTplLinked (fx_style normal@XGA) switched to another template of fx_style.ctt."""
    from ctgedit.editing import EditableDocument

    src = fx_page("FxTplLinked")
    doc = EditableDocument.open(src)
    before = _payloads(doc.parsed.page.objects)
    ((a, b, ref),) = TP.template_roots(doc.parsed.page.objects)
    own = before[:a] + before[b:]
    # another template of the same library, resolvable from this project
    res = doc._resolver()
    rs = res.resolve(ref)
    from ctgedit.format.symbols import SymbolLibrary

    names = [
        n for n in SymbolLibrary.open(rs.library_path).element_names() if n.lower() != ref.element.lower()
    ]
    assert names
    target = "%s.%s.<ctt" % (ref.library, names[0])
    tpl = TP.resolve_template(res, TP.reference_for(target))
    assert doc.change_template(target)
    objs = doc.parsed.page.objects
    ((a2, b2, ref2),) = TP.template_roots(objs)
    assert ref2.element.lower() == names[0].lower()
    assert b2 - a2 == len(tpl.records)
    after = _payloads(objs)
    assert after[:a2] + after[b2:] == own  # the user's objects, same order
    if tpl.width and tpl.height:
        assert (doc.page.appearance.width, doc.page.appearance.height) == (tpl.width, tpl.height)
    assert doc.template_status() == ("current", target)
    # handles stay page-unique
    from ctgedit.format import geometry as G

    hs = [G.get_handle(po) for po in objs if po.type_code not in (0x1010, 0x100B)]
    assert len(hs) == len(set(hs))
    # it saves and re-opens (a copy outside the project: no runtime files)
    out = tmp_path / "switched.ctg"
    doc.save(str(out))
    again = EditableDocument.open(str(out))
    assert again.template_status()[0] in ("current", "unresolved")
    doc.undo()
    assert _payloads(doc.parsed.page.objects) == before


def test_update_pages_batch_updates_templates_and_backs_up(fx_project, tmp_path):
    """Update Pages over the copy's FxMain re-applies the stale template of FxTplStale
    (the one registered page of FxMain whose template changed), saves the page and keeps
    a backup of the original."""
    from ctgedit.io import updatepages

    proj = fx_project("FxMain")
    page = os.path.join(proj, STALE + ".ctg")
    with open(page, "rb") as fh:
        orig = fh.read()
    bk = str(tmp_path / "backup")
    rep = updatepages.update_pages(proj, templates=True, genies=False, backup=bk)
    assert rep.templates == 1 and len(rep.updated) == 1, rep
    with open(page, "rb") as fh:
        saved = fh.read()
    assert saved != orig
    with open(os.path.join(bk, "FxMain", STALE + ".ctg"), "rb") as fh:
        assert fh.read() == orig
