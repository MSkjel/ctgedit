"""Rename Pages: the pages' files, pages.DBF rows, rows in the per-page tables and the pages
and tables that name them (io/project.rename_pages), the dialog (ui/dialog/rename_pages) and
the window's tabs."""

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


def rename_page(pdir, old, new, **kw):
    from ctgedit.io.project import rename_pages

    return rename_pages(pdir, [(old, new)], **kw)


def test_a_page_and_its_rows_take_the_new_name(fx_install):
    from ctgedit.io.project import Project

    path = fx_install.page("FxGenies")
    pdir = os.path.dirname(path)
    proj = Project(pdir)
    before = _rows(pdir, "FxGenies")
    record = dict(proj.page_record("FxGenies"))
    assert before
    rep = rename_page(pdir, "FxGenies", "FxPumps")
    assert not os.path.exists(path) and os.path.isfile(os.path.join(pdir, "FxPumps.ctg"))
    assert (
        rep["renamed"] == [("FxGenies", "FxPumps")] and rep["records"] and rep["tables"] and not rep["errors"]
    )
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
    from ctgedit.io.project import Project

    pdir = os.path.dirname(fx_install.page("!fx_popup"))
    rename_page(pdir, "!fx_popup", "!fx_window")
    names = set(os.listdir(pdir))
    assert {"!fx_window.ctg", "!fx_window.ctF"} <= names and not {"!fx_popup.ctg", "!fx_popup.ctF"} & names
    rename_page(pdir, "!FX_WINDOW", "!Fx_Window")  # (the file's own spelling is the old name)
    assert "!Fx_Window.ctg" in os.listdir(pdir)
    assert Project(pdir).page_record("!Fx_Window")["NAME"].strip() == "!Fx_Window"


def test_the_pages_that_link_to_it_follow(fx_install):
    from ctgedit.io.project import Project

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


def _table_rows(pdir, table, rows):
    from ctgedit.format.dbf import Dbf

    with Dbf(os.path.join(pdir, table)) as d:
        d.append([d.encode(r) for r in rows])


def _param_table(pdir, rows):
    import datetime
    import struct

    fields = [("SECTION", 64), ("NAME", 64), ("DATA", 254)]
    d = datetime.date.today()
    head = struct.pack(
        "<BBBBIHH20x", 3, d.year - 1900, d.month, d.day, len(rows), 32 + 32 * len(fields) + 1, 383
    )
    desc = b"".join(struct.pack("<11sc4xBB14x", n.encode("ascii"), b"C", ln, 0) for n, ln in fields)
    body = b"".join(
        b" " + b"".join(r[i].encode("ascii").ljust(ln) for i, (_n, ln) in enumerate(fields)) for r in rows
    )
    with open(os.path.join(pdir, "param.DBF"), "wb") as fh:
        fh.write(head + desc + b"\r" + body + b"\x1a")


def _values(pdir, table, col):
    from ctgedit.format.projects import read_dbf_rows

    return [r.get(col, "").strip() for r in read_dbf_rows(os.path.join(pdir, table))]


def test_the_tables_that_name_a_page_follow_its_new_name(fx_install):
    pdir = os.path.dirname(fx_install.page("FxParts"))
    _table_rows(
        pdir,
        "equip.DBF",
        [{"NAME": "FxEqA", "PAGE": "FxParts", "HELP": "fxparts"}, {"NAME": "FxEqB", "PAGE": "FxPartsMore"}],
    )
    _table_rows(pdir, "pagemenu.DBF", [{"PAGE": "FxParts", "LEVEL1": "FxParts", "TARGETPAGE": "FxParts"}])
    _table_rows(pdir, "digalm.DBF", [{"TAG": "FxParts", "HELP": "FxParts"}])
    _param_table(
        pdir,
        [
            ("Page", "Startup", "FxParts"),
            ("Page", "SplashWinName", "FxParts"),
            ("Alarm", "Startup", "FxParts"),
        ],
    )
    rep = rename_page(pdir, "FxParts", "FxPieces", names=False)
    assert rep["names"] == [] and "FxParts" in _values(pdir, "equip.DBF", "PAGE")

    rep = rename_page(pdir, "FxPieces", "FxBits")
    assert rep["names"] == []  # (the tables name FxParts, not FxPieces)
    rename_page(pdir, "FxBits", "FxParts")
    rep = rename_page(pdir, "FxParts", "FxPieces")
    assert sorted(rep["names"]) == [
        ("digalm.dbf", "HELP"),
        ("equip.dbf", "HELP"),
        ("equip.dbf", "PAGE"),
        ("pagemenu.dbf", "PAGE"),
        ("pagemenu.dbf", "TARGETPAGE"),
        ("param.dbf", "DATA"),
    ]
    assert "FxPieces" in _values(pdir, "equip.DBF", "PAGE") and "FxPartsMore" in _values(
        pdir, "equip.DBF", "PAGE"
    )
    # a menu's text, an alarm's tag and parameters that are not a page keep the old name
    assert "FxParts" in _values(pdir, "pagemenu.DBF", "LEVEL1") and "FxParts" in _values(
        pdir, "digalm.DBF", "TAG"
    )
    assert _values(pdir, "param.DBF", "DATA") == ["FxPieces", "FxParts", "FxParts"]


def test_a_taken_name_or_a_failure_changes_nothing(fx_install, monkeypatch):
    from ctgedit.io import pgregister
    from ctgedit.io.project import Project

    path = fx_install.page("FxShapes")
    pdir = os.path.dirname(path)
    with pytest.raises(FileExistsError):
        rename_page(pdir, "FxShapes", "fxparts")
    with pytest.raises(FileNotFoundError):
        rename_page(pdir, "FxNoSuchPage", "FxOther")
    before = _rows(pdir, "FxShapes")

    def boom(*_a, **_k):
        raise OSError("disk full")

    def unchanged():
        names = set(os.listdir(pdir))
        assert "FxShapes.ctg" in names and "FxForms.ctg" not in names
        assert not any(n.startswith("~ctgedit-rename") for n in names)
        assert (
            Project(pdir).page_record("FxShapes") is not None and Project(pdir).page_record("FxForms") is None
        )
        assert _rows(pdir, "FxShapes") == before

    monkeypatch.setattr(pgregister, "rename_page_rows", boom)
    with pytest.raises(OSError, match="disk full"):
        rename_page(pdir, "FxShapes", "FxForms")
    unchanged()
    monkeypatch.undo()
    # Stop half way: nothing changes either
    from ctgedit.io.project import RenameStopped

    with pytest.raises(RenameStopped):
        rename_page(pdir, "FxShapes", "FxForms", progress=lambda i, n, what: i < 2)
    unchanged()


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
    rows = {n: _rows(pdir, n) for n in ("FxGenies", "FxShapes", "FxParts")}
    from ctgedit.io.project import Project, rename_pages

    title = Project(pdir).page_record("FxGenies")["TITLE"]
    rep = rename_pages(pdir, [(o, n) for _d, o, n in swap])
    assert sorted(rep["renamed"]) == sorted((o, n) for _d, o, n in swap)
    assert not any(f.startswith("~ctgedit-rename") for f in os.listdir(pdir))
    assert _rows(pdir, "FxShapes") == rows["FxGenies"] and _rows(pdir, "FxGenies") == rows["FxShapes"]
    assert Project(pdir).page_record("FxShapes")["TITLE"].strip() == (
        "FxShapes" if title.strip().lower() == "fxgenies" else title.strip()
    )
    # three names round
    rename_pages(pdir, [("FxGenies", "FxParts"), ("FxParts", "FxShapes"), ("FxShapes", "FxGenies")])
    assert _rows(pdir, "FxGenies") == rows["FxGenies"] and _rows(pdir, "FxParts") == rows["FxShapes"]
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
        assert dlg.rename() and dlg.renaming()
        dlg.finish()
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
    held, moved, refs = [], [], []
    dlg = RenamePagesDialog(
        None,
        pdir,
        ["FxGenies", "FxParts"],
        hold=held.append,
        moved=lambda moves, h: moved.append((sorted(moves), sorted(h))),
        check_references=lambda *a: refs.append(a),
    )
    try:
        dlg._mode.setCurrentIndex(1)
        dlg._find.setText("^Fx")
        dlg.naming.find_regex.set_on(True)
        dlg._repl.setText("Plant")
        dlg._names.setChecked(False)
        dlg.flush()
        assert dlg._pairs == [(pdir, "FxGenies", "PlantGenies"), (pdir, "FxParts", "PlantParts")]
        assert dlg.rename()
        assert not dlg._ok.isEnabled() and not dlg._b_stop.isHidden() and not dlg.undo_rename()
        dlg.finish()
        olds = sorted(os.path.join(pdir, n + ".ctg") for n in ("FxGenies", "FxParts"))
        news = sorted(os.path.join(pdir, n + ".ctg") for n in ("PlantGenies", "PlantParts"))
        assert [sorted(h) for h in held] == [olds] and moved == [(sorted(zip(olds, news)), olds)]
        names = set(os.listdir(pdir))
        assert "PlantGenies.ctg" in names and "FxGenies.ctg" not in names
        assert dlg.renamed == [(pdir, "FxGenies", "PlantGenies"), (pdir, "FxParts", "PlantParts")]
        assert "Renamed 2 pages" in dlg._status.text() and not dlg._b_undo.isHidden()
        dlg.check_references()
        assert refs == [(pdir, False, {"fxgenies": "PlantGenies", "fxparts": "PlantParts"})]
        assert dlg.undo_rename()
        dlg.finish()
        assert os.path.isfile(fx_install.page("FxGenies")) and dlg.renamed == []
        assert "Put back 2 pages" in dlg._status.text()
        # a name that cannot be used: Rename stays off, the reason is shown
        dlg._repl.setText("Fx|")
        dlg.flush()
        assert not dlg._ok.isEnabled() and "cannot be used" in dlg._status.text()
        dlg._repl.setText("Plant")
    finally:
        dlg.close()
    # the choices come back with the next window, the '.*' toggle too
    again = RenamePagesDialog(None, pdir, [])
    try:
        assert (
            again._mode.currentIndex() == 1 and again._find.text() == "^Fx" and again._repl.text() == "Plant"
        )
        assert again.naming.find_regex.on and again._links.isChecked() and not again._names.isChecked()
    finally:
        again.close()


def test_open_tabs_follow_their_renamed_page(fx_install):
    """A renamed page's tab stays, unsaved changes and all, and shows the new file; while it
    is being renamed it is not saved."""
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.window import files

    pdir = os.path.dirname(fx_install.page("FxGenies"))
    old, new = fx_install.page("FxGenies"), os.path.join(pdir, "FxPumps.ctg")
    w = MainWindow()
    try:
        w.open_document(old)
        doc = w.doc
        doc.dirty = True
        w.hold_pages([old])
        assert not w._save_to() and "being renamed" in w.statusBar().currentMessage()
        assert old not in w._watcher.files()
        rename_page(pdir, "FxGenies", "FxPumps")
        w.pages_moved([(old, new)], [old])
        assert w.doc is doc and doc.path == new and doc.dirty and len(w._docs) == 1
        assert not files.RENAMING and new in w._watcher.files()
        assert "FxPumps" in w._tab_title(doc)
        assert w._save_to() and os.path.isfile(new) and not os.path.exists(old)
    finally:
        for e in list(w._docs):
            e["doc"].dirty = False
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.close()


def test_a_rename_leaves_the_page_tables_their_size_and_their_index_whole(fx_install):
    from ctgedit.format.dbf import Dbf, Ndx

    pdir = os.path.dirname(fx_install.page("FxGenies"))

    def state():
        with Dbf(os.path.join(pdir, "pgdynobj.DBF"), writable=False) as d:
            deleted = sum(1 for i in range(d.nrec) if d.read(i)[:1] == b"*")
            pages = sorted((d.field(d.read(i), "PAGE").lower(), i + 1) for i in range(d.nrec))
            nrec = d.nrec
        n = Ndx(os.path.join(pdir, "pgdynobj.NDX"), writable=False)
        try:
            keys = sorted((k.decode("cp1252").strip().lower(), r) for k, r in n.entries())
        finally:
            n.fh.close()
        return nrec, deleted, pages, keys

    nrec, deleted, _pages, _keys = state()
    rename_page(pdir, "FxGenies", "FxPumps")
    after = state()
    assert after[:2] == (nrec, deleted)  # (renamed in place: no deleted copies)
    assert after[3] == after[2] and any(
        p == "fxpumps" for p, _r in after[2]
    )  # (one key per row, the new name's)
