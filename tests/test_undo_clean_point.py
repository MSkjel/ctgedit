"""Undoing back to the saved state leaves no unsaved changes."""

import shutil
import uuid

import pytest


@pytest.fixture
def doc(fx_page):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(fx_page("FxShapes"))


def _root(doc, k=0):
    return doc.page.roots()[k]


def test_open_edit_undo_is_not_dirty(doc):
    assert not doc.dirty and doc.saved_depth == 0 and doc.at_saved_point()
    doc.move_objects([_root(doc)], 5, 0)
    assert doc.dirty and doc.saved_depth == 0 and not doc.at_saved_point()
    doc.undo()
    assert not doc.dirty and doc.at_saved_point()
    doc.redo()
    assert doc.dirty


def test_save_then_undo_is_dirty_and_redo_back_is_not(doc):
    doc.move_objects([_root(doc)], 5, 0)
    doc.move_objects([_root(doc)], 0, 3)
    doc.save()
    assert not doc.dirty and doc.saved_depth == 2
    doc.undo()
    assert doc.dirty and doc.saved_depth == 2  # (among the steps to redo)
    doc.redo()
    assert not doc.dirty
    # save, redo, undo: back at the saved state
    doc.undo()
    doc.save()
    assert not doc.dirty and doc.saved_depth == 1
    doc.redo()
    assert doc.dirty
    doc.undo()
    assert not doc.dirty


def test_an_edit_after_undoing_below_the_saved_point_stays_dirty(doc):
    doc.move_objects([_root(doc)], 5, 0)
    doc.save()
    doc.undo()
    doc.move_objects([_root(doc)], 9, 0)  # a new branch
    assert doc.dirty and doc.saved_depth is None
    doc.undo()
    assert doc.dirty  # (the saved state is gone)
    doc.redo()
    assert doc.dirty
    while doc.can_undo():
        doc.undo()
    assert doc.dirty


def test_a_batch_keeps_the_saved_point_after_its_last_step(doc):
    r = _root(doc)
    doc.save()
    with doc.batch("move twice"):
        doc.move_objects([r], 1, 0)
        doc.move_objects([r], 1, 0)
    assert len(doc._undo) == 1 and doc.dirty
    doc.undo()
    assert not doc.dirty
    doc.redo()
    doc.save()
    assert doc.saved_depth == 1
    doc.undo()
    doc.redo()
    assert not doc.dirty


def test_the_undo_cap_keeps_the_saved_point_reachable_or_drops_it(doc, monkeypatch):
    import ctgedit.editing as ED

    monkeypatch.setattr(ED, "UNDO_BYTES_TOTAL", 0)
    monkeypatch.setattr(ED, "UNDO_KEEP_MIN", 1)
    r = _root(doc)
    doc.move_objects([r], 1, 0)
    doc.save()  # saved after step 1
    doc.move_objects([r], 1, 0)  # the cap drops step 1
    assert doc.undo_trimmed >= 1
    assert doc.saved_depth == 1  # the oldest state left
    doc.undo()
    assert not doc.dirty
    doc.redo()
    doc.move_objects([r], 1, 0)  # the cap drops step 2 too
    assert doc.saved_depth is None
    while doc.can_undo():
        doc.undo()
    assert doc.dirty


def test_a_cancelled_preview_gives_the_saved_point_back(doc):
    r = _root(doc)
    doc.move_objects([r], 4, 0)
    doc.save()
    doc.undo()  # the saved state is to redo
    mark = doc.history_mark()
    doc.move_objects([r], 1, 1)  # a preview (Align)
    assert doc.saved_depth is None
    doc.revert_to_mark(mark)
    assert doc.saved_depth == 1 and doc.dirty
    doc.redo()
    assert not doc.dirty


def test_a_change_that_is_no_step_keeps_the_page_unsaved(doc, tmp_path):
    from ctgedit.editing import EditableDocument

    # a recovery copy restored: the page holds changes no undo step has
    with open(doc.path, "rb") as fh:
        data = fh.read()
    rec = EditableDocument.from_bytes(data, doc.path)
    rec.dirty = True
    assert rec.has_unstepped_changes
    rec.move_objects([_root(rec)], 3, 0)
    rec.undo()
    assert rec.dirty
    rec.save()
    assert not rec.dirty and not rec.has_unstepped_changes
    rec.move_objects([_root(rec)], 3, 0)
    rec.undo()
    assert not rec.dirty
    # dirty = False (a caller says the page is as saved): that state is the saved point
    doc.move_objects([_root(doc)], 2, 0)
    doc.dirty = False
    assert doc.saved_depth == 1
    doc.undo()
    assert doc.dirty


def test_the_steps_know_when_they_were_made_and_undo_logs_its_moves(doc):
    import time

    t0 = time.time()
    doc.move_objects([_root(doc)], 5, 0)
    step = doc._undo[-1]
    assert t0 - 1 <= step.when <= time.time() + 1 and step.targets is None
    doc.take_history_moves()
    doc.undo()
    doc.redo()
    assert [(redone, c is step) for redone, c in doc.take_history_moves()] == [(False, True), (True, True)]
    assert doc.take_history_moves() == []


def _fixed_identity(monkeypatch):
    """Every save writes the same identity (a GUID and a stamp each save draws)."""
    from ctgedit.format import element as E

    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=0x1234))
    monkeypatch.setattr(E, "ctdraw_timestamp", lambda: 0x40000000)


def test_the_same_edits_give_the_same_bytes_with_or_without_history(tmp_path, monkeypatch, fx_page):
    """The saved point and the earlier versions never change what a save writes."""
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import history as H

    _fixed_identity(monkeypatch)
    monkeypatch.setenv("CTG_DATA_DIR", str(tmp_path / "data"))
    out = {}
    for name, versions, wander in (("plain", 0, False), ("versions", 10, False), ("wander", 10, True)):
        H.configure(versions, 200)
        dst = tmp_path / ("%s.ctg" % name)
        shutil.copyfile(fx_page("FxShapes"), dst)
        d = EditableDocument.open(str(dst))
        r = d.page.roots()
        d.move_objects([r[0]], 7, -3)
        d.save()  # (a version of the original)
        d.set_colour(r[1], 0x00336699)
        if wander:
            d.undo()
            d.undo()
            d.redo()
            d.redo()
        d.save()
        with open(dst, "rb") as fh:
            out[name] = fh.read()
        assert len(H.list_versions(str(dst))) == (2 if versions else 0)
    H.configure(0, 200)
    assert out["plain"] == out["versions"] == out["wander"]


def test_an_element_save_is_the_same_with_versions_and_keeps_the_saved_point(
    tmp_path, monkeypatch, fx_install
):
    from ctgedit.editing import ElementDocument
    from ctgedit.infra import history as H

    _fixed_identity(monkeypatch)
    monkeypatch.setenv("CTG_DATA_DIR", str(tmp_path / "data"))
    out = {}
    for versions in (0, 10):
        H.configure(versions, 200)
        lib = tmp_path / ("v%d" % versions) / "fxsym.ctl"
        lib.parent.mkdir()
        shutil.copyfile(fx_install.file("FxLib", "fxsym.ctl"), lib)
        d = ElementDocument.open_element(str(lib), "fx_box")
        d.move_objects([d.page.roots()[0]], 2, 0)
        d.save()
        assert not d.dirty and d.saved_depth == 1
        d.undo()
        assert d.dirty
        d.redo()
        assert not d.dirty
        with open(lib, "rb") as fh:
            out[versions] = fh.read()
        got = H.list_versions(str(lib), "fx_box")
        assert len(got) == (1 if versions else 0)
        if got:
            # the copy is a library holding the element as it was stored
            from ctgedit.format import element as E

            pe = E.open_element(got[0].file, "fx_box")
            orig = E.open_element(fx_install.file("FxLib", "fxsym.ctl"), "fx_box")
            assert len(pe.page.objects) == len(orig.page.objects)
    H.configure(0, 200)
    assert out[0] == out[10]
