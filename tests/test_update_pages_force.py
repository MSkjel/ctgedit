"""Update Pages with 'Update every page'."""

import os
import shutil

from ctgedit.format import writer


def _project(fx_install):
    proj = fx_install.new_project("FxForce", includes=("FxLib", "FxStyle"), tables=False)
    for page in ("FxShapes", "FxGenies"):
        shutil.copyfile(fx_install.page(page), os.path.join(proj, page + ".ctg"))
    return proj


def _payloads(doc, a, b):
    return [writer.object_payload(o) for o in doc.objects[a:b]]


def test_force_updates_every_page_also_the_current_ones(fx_install):
    from ctgedit.io import updatepages

    proj = _project(fx_install)
    updatepages.update_pages(proj)  # (anything out of date in the copies)
    rep = updatepages.update_pages(proj)
    assert rep.updated == [], rep  # the stamp test: nothing to do
    rep = updatepages.update_pages(proj, force=True)
    assert sorted(rep.updated) == ["FxGenies", "FxShapes"], rep
    assert rep.instances >= 1
    # (and a normal run after it finds every page current still)
    assert updatepages.update_pages(proj).updated == []


def test_force_composes_again_a_genie_whose_stamps_say_current(fx_install):
    """A member of a genie instance moved on the page (the link table still quotes the
    element's current stamp): a normal run leaves it, the forced run composes the instance
    again from its element."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages

    proj = _project(fx_install)
    path = os.path.join(proj, "FxGenies.ctg")
    updatepages.update_pages(proj)
    doc = EditableDocument.open(path)
    (root,) = doc.find_objects_by_name("FxValue")
    sub = doc.subtree(root)
    end = root + len(sub)
    composed = _payloads(doc, root, end)
    from ctgedit.format import geometry as geom

    first = geom.parse_geom_header(doc.objects[root].body_raw)
    member = sub[-1]
    assert member != root
    doc.move_objects([member], 7, 5)
    doc.save()
    moved = _payloads(EditableDocument.open(path), root, end)
    assert moved != composed

    rep = updatepages.update_pages(proj)
    assert "FxGenies" not in rep.updated
    assert _payloads(EditableDocument.open(path), root, end) == moved

    rep = updatepages.update_pages(proj, force=True)
    assert "FxGenies" in rep.updated, rep
    again = EditableDocument.open(path)
    now = _payloads(again, root, end)
    # the members as composed; the root's box fits them again (its header name is what
    # any full composition of the instance writes, as a normal run after an element edit)
    assert now[1:] == composed[1:]
    box = geom.parse_geom_header(again.objects[root].body_raw)
    assert (box.anchor, box.rect1, box.rect2) == (first.anchor, first.rect1, first.rect2)
