"""Rename Pages: a page's files, its pages.DBF row, its rows in the per-page tables and the
pages that link to it (io/project.rename_page), and the dialog (ui/dialog/rename_pages)."""

import os

import pytest

from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _rows(pdir, name):
    from ctgedit.io import pgregister

    return {
        t: sorted((r.get("AN", ""), r.get("TYPE", "")) for r in rs)
        for t, rs in pgregister.export_page_rows(pdir, name).items()
    }


def test_a_page_and_its_rows_take_the_new_name(fx_install):
    from ctgedit.io.project import Project, rename_page

    path = fx_install.page("FxGenies")
    pdir = os.path.dirname(path)
    proj = Project(pdir)
    before = _rows(pdir, "FxGenies")
    record = dict(proj.page_record("FxGenies"))
    assert before
    rep = rename_page(pdir, "FxGenies", "FxPumps")
    assert not os.path.exists(path) and os.path.isfile(os.path.join(pdir, "FxPumps.ctg"))
    assert rep["records"] and rep["tables"] and not rep["errors"]
    assert proj.page_record("FxGenies") is None
    after = dict(Project(pdir).page_record("FxPumps"))
    title = record.get("TITLE", "").strip()
    assert after["TITLE"].strip() == ("FxPumps" if title.lower() == "fxgenies" else title)
    assert {k: v for k, v in after.items() if k not in ("NAME", "TITLE")} == {
        k: v for k, v in record.items() if k not in ("NAME", "TITLE")
    }
    assert _rows(pdir, "FxPumps") == before and _rows(pdir, "FxGenies") == {}
    from ctgedit.editing import EditableDocument

    assert EditableDocument.open(os.path.join(pdir, "FxPumps.ctg")).find_text("FX_PUMP1")


def test_the_ctf_moves_with_the_page_and_a_change_of_case_is_a_rename(fx_install):
    from ctgedit.io.project import Project, rename_page

    pdir = os.path.dirname(fx_install.page("!fx_popup"))
    rename_page(pdir, "!fx_popup", "!fx_window")
    names = set(os.listdir(pdir))
    assert {"!fx_window.ctg", "!fx_window.ctF"} <= names and not {"!fx_popup.ctg", "!fx_popup.ctF"} & names
    rename_page(pdir, "!FX_WINDOW", "!Fx_Window")  # (the file's own spelling is the old name)
    assert "!Fx_Window.ctg" in os.listdir(pdir)
    assert Project(pdir).page_record("!Fx_Window")["NAME"].strip() == "!Fx_Window"


def test_the_pages_that_link_to_it_follow(fx_install):
    from ctgedit.io.project import Project, rename_page

    pdir = os.path.dirname(fx_install.page("FxParts"))
    proj = Project(pdir)
    assert proj.update_page_record("FxAnim", {"NEXT_PAGE": "FxParts", "PARENT": "fxparts"})
    assert proj.update_page_record("FxShapes", {"PREV_PAGE": "FxParts"})
    rep = rename_page(pdir, "FxParts", "FxPieces")
    assert sorted(rep["links"]) == [("FxAnim", "NEXT_PAGE"), ("FxAnim", "PARENT"), ("FxShapes", "PREV_PAGE")]
    anim = Project(pdir).page_record("FxAnim")
    assert anim["NEXT_PAGE"].strip() == anim["PARENT"].strip() == "FxPieces"
    # without links the other pages keep the old name
    rep = rename_page(pdir, "FxPieces", "FxBits", links=False)
    assert rep["links"] == [] and Project(pdir).page_record("FxShapes")["PREV_PAGE"].strip() == "FxPieces"


def test_a_taken_name_or_a_failure_changes_nothing(fx_install, monkeypatch):
    from ctgedit.io import pgregister
    from ctgedit.io.project import Project, rename_page

    path = fx_install.page("FxShapes")
    pdir = os.path.dirname(path)
    with pytest.raises(FileExistsError):
        rename_page(pdir, "FxShapes", "fxparts")
    with pytest.raises(FileNotFoundError):
        rename_page(pdir, "FxNoSuchPage", "FxOther")
    before = _rows(pdir, "FxShapes")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(pgregister, "restore_page_rows", boom)
    with pytest.raises(OSError, match="disk full"):
        rename_page(pdir, "FxShapes", "FxForms")
    assert os.path.isfile(path) and not os.path.exists(os.path.join(pdir, "FxForms.ctg"))
    assert Project(pdir).page_record("FxShapes") is not None and Project(pdir).page_record("FxForms") is None
    assert _rows(pdir, "FxShapes") == before


# --------------------------------------------------------------------------- the dialog
def test_plan_problems_and_a_cycle_of_names(fx_install):
    from ctgedit.ui.dialog import rename_pages as RP

    pdir = os.path.dirname(fx_install.page("FxGenies"))
    pages = RP.project_pages([pdir])
    names = [n for _d, n, _t in pages]
    assert "FxParts" in names and "!fx_popup" in names
    asked = [("FxParts", "FxShapes"), ("FxAnim", "Bad|Name"), ("FxBig", "X"), ("FxMany", "x")]
    got = RP.plan_problems([(pdir, o, n) for o, n in asked], pages)
    assert (
        "already has a page 'FxShapes'" in got[(pdir, "FxParts")] and "Not allowed" in got[(pdir, "FxAnim")]
    )
    assert "two pages" in got[(pdir, "FxBig")] and "two pages" in got[(pdir, "FxMany")]
    # two registered pages swap names: their rows go with them
    swap = [(pdir, "FxGenies", "FxShapes"), (pdir, "FxShapes", "FxGenies")]
    assert RP.plan_problems(swap, pages) == {}
    rows = {n: _rows(pdir, n) for n in ("FxGenies", "FxShapes")}
    done, failed, _links = RP.apply_page_renames(swap)
    assert not failed and sorted(done) == sorted(swap)
    assert not any(f.lower().startswith("_renametemp") for f in os.listdir(pdir))
    assert _rows(pdir, "FxShapes") == rows["FxGenies"] and _rows(pdir, "FxGenies") == rows["FxShapes"]
    assert RP.page_pattern_name("{name}_{n:02}", "Mix", "T", 3) == "Mix_03"
    with pytest.raises(ValueError):
        RP.page_pattern_name("{an}", "Mix", "", 1)


def test_the_projects_it_includes(fx_install):
    """FxOther includes FxMain: with 'And the projects it includes' both projects' pages are
    listed (a Project column), one set of names, and links from the included project follow."""
    _app()
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.rename_pages import RenamePagesDialog

    other = fx_install.project("FxOther")
    main = fx_install.project("FxMain")
    dlg = RenamePagesDialog(None, other, ["FxShapes"])
    try:
        assert {p[1] for p in dlg._pages} == {"FxShapes"} and dlg._table.isColumnHidden(1)
        dlg._scope.includes.setChecked(True)
        dirs = {os.path.normcase(p[0]) for p in dlg._pages}
        assert os.path.normcase(main) in dirs and not dlg._table.isColumnHidden(1)
        assert [dlg._pages[r][1] for r in dlg._ticked_rows()] == ["FxShapes"]  # (kept ticked)
        dlg._mode.setCurrentIndex(1)
        dlg._find.setText("Shapes")
        dlg._repl.setText("Genies")
        dlg.flush()
        assert not dlg._ok.isEnabled() and "Project FxMain, compiled with this one" in dlg._status.text()
        dlg._repl.setText("Forms")
        dlg.flush()
        assert Project(main).update_page_record("FxAnim", {"NEXT_PAGE": "FxShapes"})
        assert dlg.rename()
        assert os.path.isfile(os.path.join(other, "FxForms.ctg"))
        assert os.path.isfile(os.path.join(main, "FxShapes.ctg"))  # (FxMain's own page is not ticked)
        assert Project(main).page_record("FxAnim")["NEXT_PAGE"].strip() == "FxForms"
        assert dlg.renamed == [(other, "FxShapes", "FxForms")]
    finally:
        dlg.close()


def test_the_dialog_renames_and_undoes_and_finds_references(fx_install):
    _app()
    from ctgedit.ui.dialog.rename_pages import RenamePagesDialog

    pdir = os.path.dirname(fx_install.page("FxGenies"))
    asked, refs = [], []

    def before(kind, path, element):
        asked.append(os.path.basename(path))
        return True

    dlg = RenamePagesDialog(
        None, pdir, ["FxGenies", "FxParts"], before=before, check_references=lambda *a: refs.append(a)
    )
    try:
        dlg._mode.setCurrentIndex(1)
        dlg._find.setText("^Fx")
        dlg.naming.find_regex.set_on(True)
        dlg._repl.setText("Plant")
        dlg.flush()
        assert dlg._pairs == [(pdir, "FxGenies", "PlantGenies"), (pdir, "FxParts", "PlantParts")]
        assert dlg.rename()
        assert sorted(asked) == ["FxGenies.ctg", "FxParts.ctg"]
        names = set(os.listdir(pdir))
        assert "PlantGenies.ctg" in names and "FxGenies.ctg" not in names
        assert dlg.renamed == [(pdir, "FxGenies", "PlantGenies"), (pdir, "FxParts", "PlantParts")]
        assert "Renamed 2 pages" in dlg._status.text() and not dlg._b_undo.isHidden()
        dlg.check_references()
        assert refs == [(pdir, False, {"fxgenies": "PlantGenies", "fxparts": "PlantParts"})]
        assert dlg.undo_rename()
        assert os.path.isfile(fx_install.page("FxGenies")) and dlg.renamed == []
        # a name that cannot be used: Rename stays off, the reason is shown
        dlg._repl.setText("Fx|")
        dlg.flush()
        assert not dlg._ok.isEnabled() and "cannot be used" in dlg._status.text()
        # a page open with unsaved changes that is not given up: nothing is renamed
        dlg._before = lambda *_a: False
        dlg._repl.setText("Plant")
        dlg.flush()
        assert not dlg.rename() and os.path.isfile(fx_install.page("FxParts"))
    finally:
        dlg.close()
    # the choices come back with the next window, the '.*' toggle too
    again = RenamePagesDialog(None, pdir, [])
    try:
        assert (
            again._mode.currentIndex() == 1 and again._find.text() == "^Fx" and again._repl.text() == "Plant"
        )
        assert again.naming.find_regex.on
    finally:
        again.close()
