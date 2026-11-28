"""Find's index of the closed pages (features/find_index): a search from it answers as a
read of the pages does, and a page is read again when it or what its read read changed."""

import os
import shutil

import pytest

ALL = ["expr", "name", "page", "param", "text"]


def _req(opts, projects):
    from ctgedit.workers import find_worker as FW

    return {"format": FW.FORMAT, "projects": list(projects), "options": FW.options_to_json(opts)}


@pytest.fixture
def indexed(fx_project, monkeypatch):
    """(FxMain, its first 16 pages), each read once by the helper's search (every page: no
    page left out by the prefilter) so the index holds it."""
    from ctgedit.features import find_replace as FR
    from ctgedit.workers import find_worker as FW

    main = fx_project("FxMain")
    pages = FW.pages_of([main])[:16]
    assert len(pages) == 16
    monkeypatch.setattr(FW, "pages_of", lambda projects: pages)
    with monkeypatch.context() as m:
        m.setattr(FR.PagePrefilter, "may_match", lambda self, path, data: True)
        FW.find(_req(FR.FindOptions("x"), [main]))
    return main, pages


def _kept(opts, pages):
    from ctgedit.features import find_index as FI

    ks = FI.KeptSearch(opts)
    return [ks.rows(p) for p in pages]


CASES = [
    ("pump", {}),
    ("e", {}),
    ("FX_", {"match": "word"}),
    (r"Pu.p", {"regex": True}),
    (r"^\d+$", {"regex": True}),
    ("e", {"scopes": frozenset({"param"})}),
    ("e", {"scopes": frozenset({"page"})}),
    ("e", {"inside_genies": False, "inside_templates": True}),
    ("e", {"fields": ("cmd|command", "")}),
    ("e", {"objects": ("", "AN1")}),
    ("e", {"genies": (".", "")}),
]


def _opts(text, kw):
    from ctgedit.features import find_replace as FR

    kw = dict(kw)
    for k in ("fields", "objects", "genies"):
        if k in kw:
            kw[k] = FR.NameFilter(*kw[k])
    return FR.FindOptions(text, **kw)


def test_a_search_of_the_index_answers_as_reading_the_pages(indexed):
    from ctgedit.workers import find_worker as FW

    main, pages = indexed
    hits = 0
    for text, kw in CASES:
        opts = _opts(text, kw)
        kept = _kept(opts, pages)
        assert all(r is not None for r in kept), text
        want = FW.find(_req(opts, [main]))["rows"]
        assert [row for rows in kept for row in rows] == want, (text, kw)
        hits += bool(want)
    assert hits >= 6  # (the equality is not an empty one)


def test_a_changed_page_is_read_again(indexed):
    from ctgedit.features import find_replace as FR

    _main, pages = indexed
    opts = FR.FindOptions("e")
    p = pages[0]
    st = os.stat(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    kept = _kept(opts, pages)
    assert kept[0] is None and all(r is not None for r in kept[1:])


def test_a_changed_library_is_read_again(indexed):
    from ctgedit.features import find_index as FI
    from ctgedit.features import find_replace as FR

    _main, pages = indexed
    forms = [FI._read(FI.index_file(p)) for p in pages]
    lib = next(f["libs"][0][0] for f in forms if f["libs"])
    users = [p for p, f in zip(pages, forms) if any(x[0] == lib for x in f["libs"])]
    st = os.stat(lib)
    os.utime(lib, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    kept = dict(zip(pages, _kept(FR.FindOptions("e"), pages)))
    assert users and all(kept[p] is None for p in users)
    assert all(kept[p] is not None for p in pages if p not in users)


def test_a_library_of_the_same_name_put_nearer_is_read_again(indexed):
    from ctgedit.features import find_index as FI
    from ctgedit.features import find_replace as FR

    main, pages = indexed
    form = next(
        f
        for f in (FI._read(FI.index_file(p)) for p in pages)
        if any(
            files and os.path.dirname(files[0]) != os.path.normcase(main) for _k, _key, files in f["found"]
        )
    )
    far = next(
        files[0]
        for _k, _key, files in form["found"]
        if files and os.path.dirname(files[0]) != os.path.normcase(main)
    )
    near = os.path.join(main, os.path.basename(far))
    assert not os.path.exists(near)
    shutil.copyfile(far, near)
    try:
        assert FI.KeptSearch(FR.FindOptions("e")).rows(form["path"]) is None
    finally:
        os.remove(near)
    assert FI.KeptSearch(FR.FindOptions("e")).rows(form["path"]) is not None


def test_other_switches_or_another_build_read_the_pages_again(indexed, monkeypatch):
    from ctgedit.features import find_index as FI
    from ctgedit.features import find_replace as FR

    _main, pages = indexed
    opts = FR.FindOptions("e")
    assert FI.KeptSearch(opts).rows(pages[0]) is not None
    monkeypatch.setenv("CTG_TEXT_REPLACE", "a=b")
    assert FI.KeptSearch(opts).rows(pages[0]) is None
    monkeypatch.delenv("CTG_TEXT_REPLACE")
    monkeypatch.setattr(FI, "_code", "another build")
    assert FI.KeptSearch(opts).rows(pages[0]) is None


def test_the_page_rows_are_read_at_each_search(indexed):
    # the pages.DBF row is not kept: a title changed since is found without reading the page
    from ctgedit.features import find_replace as FR
    from ctgedit.io.project import Project

    main, pages = indexed
    name = FR.page_name_of(pages[2])
    assert Project(main).update_page_record(name, {"TITLE": "Zqx kept title"})
    rows = _kept(FR.FindOptions("zqx kept", scopes=frozenset({"page"})), pages)
    assert all(r is not None for r in rows)
    assert [(r["name"], r["key"]) for rr in rows for r in rr] == [(name, "TITLE")]


def test_a_page_whose_fields_lack_the_text_is_not_unpacked(indexed, monkeypatch):
    from ctgedit.features import find_index as FI
    from ctgedit.features import find_replace as FR

    _main, pages = indexed
    FI._KEPT.clear()
    made = []
    real = FI.KeptFields.__init__
    monkeypatch.setattr(FI.KeptFields, "__init__", lambda self, form: made.append(1) or real(self, form))
    assert all(r == [] for r in _kept(FR.FindOptions("zz_not_on_any_page"), pages))
    made.clear()
    assert all(r == [] for r in _kept(FR.FindOptions("zz_not_on_any_page"), pages))
    assert not made


# --------------------------------------------------------------------------- the dialog
def _dialog(fx_install):
    from qtkit import app as _app

    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

    doc = EditableDocument.open(fx_install.page("FxParts"))
    dlg = FindReplaceDialog(doc, documents=lambda: [doc], current=lambda: doc)
    dlg._rb_project.setChecked(True)
    return dlg


def _index_all(dlg, monkeypatch):
    from ctgedit.features import find_replace as FR
    from ctgedit.workers import find_worker as FW

    with monkeypatch.context() as m:
        m.setattr(FR.PagePrefilter, "may_match", lambda self, path, data: True)
        FW.find(_req(FR.FindOptions("x"), dlg.project_folders()))


def test_the_list_follows_the_options_from_the_index(fx_install, monkeypatch):
    """Once the index holds the project's closed pages, a search lists them at once, and a
    changed query or condition lists its matches again without a helper."""
    dlg = _dialog(fx_install)
    try:
        _index_all(dlg, monkeypatch)
        dlg._ed_find.setText("pump")
        dlg.find_all()
        assert not dlg.searching()
        closed = [h for h in dlg.hits() if h.doc is None]
        assert closed
        dlg._list.setCurrentItem(dlg._list.topLevelItem(0))
        dlg._ed_find.setText("pum")
        # (the rows wait for the new search: no Go To on a row of the old one)
        assert dlg._list.topLevelItemCount() and not dlg._b_goto.isEnabled()
        dlg._refresh_now()
        assert not dlg.searching() and dlg._following
        assert len([h for h in dlg.hits() if h.doc is None]) >= len(closed)
        dlg._ed_find.setText("zz_not_on_any_page")
        dlg._refresh_now()
        assert dlg.hits() == [] and "was not found" in dlg._status.text()
        dlg._ed_find.setText("pump")
        dlg._refresh_now()
        assert [(h.path, h.index, h.key) for h in dlg.hits() if h.doc is None] == [
            (h.path, h.index, h.key) for h in closed
        ]
    finally:
        dlg.done(0)


def test_a_change_that_needs_a_page_read_clears_the_list(fx_install, monkeypatch):
    from ctgedit.workers import find_worker as FW

    dlg = _dialog(fx_install)
    try:
        _index_all(dlg, monkeypatch)
        dlg._ed_find.setText("pump")
        dlg.find_all()
        assert dlg.hits() and not dlg.searching()
        p = next(p for p in FW.pages_of(dlg.project_folders()) if not p.endswith("FxParts.ctg"))
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        dlg._ed_find.setText("pumps")
        dlg._refresh_now()
        assert dlg._list.topLevelItemCount() == 0 and not dlg._following
        assert dlg._status.text() == "Search again to update the results."
    finally:
        dlg.done(0)
