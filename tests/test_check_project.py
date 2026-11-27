"""Tools > Check Project (workers/check_worker, ui/dialog/check_project) and Rename Pages'
Replace References."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtkit import app as _app


class _Engine:
    """ctsim/checkExpression as a session answers it: a page named in quotes that is not one
    of ``pages`` is a page the project does not have (kind "page", its name, the line and
    the column in it of the opening quote)."""

    def __init__(self, pages=("FxShapes", "FxGenies"), functions=()):
        self.pages = {p.lower() for p in pages}
        #: ctsim/checkFunctions' warnings
        self.functions = list(functions)
        self.closed = False

    def request(self, name, args, timeout=None):
        import re

        if name == "checkFunctions":
            return {"warnings": self.functions, "errors": []}
        assert name == "checkExpression"
        warnings = []
        text = args["text"]
        for m in re.finditer(r'PageDisplay\("([^"]*)"\)', text):
            if m.group(1).lower() not in self.pages:
                start = text.rfind("\n", 0, m.start(1)) + 1
                warnings.append(
                    {
                        "kind": "page",
                        "name": m.group(1),
                        "message": "the project has no page %s (the runtime cannot display it)" % m.group(1),
                        "line": text.count("\n", 0, m.start(1)) + 1,
                        "column": m.start(1) - start,
                    }
                )
        return {"ok": True, "errors": [], "warnings": warnings}

    def close(self):
        self.closed = True


def _plant(fx_install, page, command):
    """A button on ``page`` whose command is ``command``; its AN."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G

    doc = EditableDocument.open(fx_install.page(page))
    b = doc.create_object("button", rect=(40, 40, 140, 80), text="Go")
    assert doc.set_input_commands(b, touch={"CMD": command})
    doc.save()
    return G.live_an(doc.parsed.page.objects[b])


def test_the_project_check_lists_a_page_the_project_does_not_have(fx_install, monkeypatch):
    from ctgedit.sim import client as C
    from ctgedit.workers import check_worker as CW

    an = _plant(fx_install, "FxGenies", 'PageDisplay("FxOld")')
    engines = []
    monkeypatch.setattr(C, "open_session", lambda d, **_k: engines.append(_Engine()) or engines[-1])
    ans = CW.check({"projects": [fx_install.project("FxMain")], "engine": ""})
    assert ans["checked"] == ans["pages"] and not ans["failed"] and not ans["engine"]
    (row,) = [r for r in ans["rows"] if r["code"] == "page"]
    assert (row["name"], row["an"], row["field"], row["subject"]) == ("FxGenies", an, "CMD", "FxOld")
    assert row["column"] == 'PageDisplay("FxOld")'.index('"') + 1
    assert row["level"] == "warning" and "the project has no page FxOld" in row["message"]
    assert len(engines) == 1 and engines[0].closed  # (one engine per project, closed at the end)


def _menu_command(pdir, command):
    from ctgedit.format.dbf import Dbf

    with Dbf(os.path.join(pdir, "pagemenu.DBF")) as d:
        d.append([d.encode({"PAGE": "Navigation", "LEVEL1": "Plant", "LEVEL2": "Old", "COMMAND": command})])


ENTRY = 'Sleep(0);\r\nPageDisplay("FxOld"); Message("FxOld", "FxOld", 0)'


def test_the_project_check_lists_the_tables_cicode(fx_install, monkeypatch):
    from ctgedit.io.project import Project
    from ctgedit.sim import client as C
    from ctgedit.workers import check_worker as CW

    pdir = fx_install.project("FxMain")
    assert Project(pdir).update_page_record("FxShapes", {"ENTRY_FUNC": ENTRY})
    _menu_command(pdir, 'PageDisplay("FxOld")')
    monkeypatch.setattr(C, "open_session", lambda d, **_k: _Engine())
    ans = CW.check({"projects": [pdir], "engine": ""})
    rows = sorted((r for r in ans["rows"] if r.get("table")), key=lambda r: r["name"])
    assert [(r["name"], r["object"], r["field"], r["subject"], r["line"], r["column"]) for r in rows] == [
        ("FxShapes", "Page Properties", "ENTRY_FUNC", "FxOld", 2, 13),
        ("Menu Navigation", "Plant.Old", "COMMAND", "FxOld", 1, 13),
    ]
    assert rows[0]["message"].startswith("On page entry: the project has no page FxOld")


def test_replace_references_changes_the_tables_at_the_engines_place(fx_install):
    from ctgedit.io.pgregister import find_file
    from ctgedit.io.project import Project
    from ctgedit.workers import check_worker as CW

    pdir = fx_install.project("FxMain")
    assert Project(pdir).update_page_record("FxShapes", {"ENTRY_FUNC": ENTRY})
    (f,) = [f for f in CW.table_fields(pdir) if f["field"] == "ENTRY_FUNC" and f["name"] == "FxShapes"]
    edit = dict(f, line=2, column=13, old="FxOld", new="FxNewName")
    assert CW.replace_in_tables([edit]) == (1, [])
    # only the literal the engine found: Message's text keeps the name
    got = Project(pdir).page_record("FxShapes")["ENTRY_FUNC"]
    assert got == 'Sleep(0);\r\nPageDisplay("FxNewName"); Message("FxOld", "FxOld", 0)'
    # the value changed since the check: nothing is replaced, and it says so
    replaced, problems = CW.replace_in_tables([edit])
    assert replaced == 0 and "no longer holds" in problems[0]
    # a new value too long for its column is not written
    long = dict(edit, old="FxNewName", new="X" * 300)
    replaced, problems = CW.replace_in_tables([long])
    assert replaced == 0 and "would not fit" in problems[0]
    assert Project(pdir).page_record("FxShapes")["ENTRY_FUNC"] == got
    assert find_file(pdir, "pages.dbf") == f["table"]


def test_replace_references_gives_a_closed_page_the_new_name(fx_install):
    from ctgedit.editing import EditableDocument
    from ctgedit.features import find_replace as FR
    from ctgedit.workers import check_worker as CW

    an = _plant(fx_install, "FxGenies", 'PageDisplay("FxOld"); Message("FxOld", "FxOld", 0)')
    path = fx_install.page("FxGenies")
    cmd = 'PageDisplay("FxOld"); Message("FxOld", "FxOld", 0)'
    col = cmd.index('"FxOld"') + 1  # (the engine's column: the opening quote, 1-based)
    edit = {
        "page": path,
        "name": "FxGenies",
        "an": an,
        "field": "CMD",
        "old": "FxOld",
        "new": "FxNewName",
        "column": col,
    }
    from ctgedit.infra import backups

    out = CW.replace_references({"edits": [edit], "backup": backups.new_folder("references")})
    assert out["replaced"] == 1 and out["updated"] == ["FxGenies"], out
    doc = EditableDocument.open(path)
    (hit,) = doc.find_text("FxNewName", scopes={FR.SCOPE_EXPR})
    # only the literal the engine found: Message's text keeps the name
    assert hit.value == 'PageDisplay("FxNewName"); Message("FxOld", "FxOld", 0)'


def test_the_window_replaces_the_references_of_an_open_page(fx_install):
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.features import find_replace as FR
    from ctgedit.ui.dialog.check_project import CheckProjectDialog

    an = _plant(fx_install, "FxGenies", 'PageDisplay("FxOld")')
    path = fx_install.page("FxGenies")
    doc = EditableDocument.open(path)
    changed = []
    dlg = CheckProjectDialog(
        None,
        fx_install.project("FxMain"),
        renames={"FxOld": "FxNew"},
        documents=lambda: [doc],
        replaced=changed.extend,
    )
    try:
        dlg.check = lambda: False  # (no helper process here)
        rows = [
            {
                "page": path,
                "name": "FxGenies",
                "project": "FxMain",
                "index": 3,
                "an": an,
                "object": "Button AN %d" % an,
                "level": "warning",
                "code": "page",
                "message": "Command (CMD): the project has no page FxOld (the runtime cannot display it)",
                "field": "CMD",
                "subject": "FxOld",
                "column": 13,
            },
            {
                "page": path,
                "name": "FxGenies",
                "project": "FxMain",
                "index": 4,
                "an": 9,
                "object": "Text AN 9",
                "level": "error",
                "code": "compile",
                "message": "does not compile",
                "field": "TXT_A",
                "subject": "",
                "column": 0,
            },
        ]
        dlg.take({"rows": rows, "checked": 1, "pages": 1})
        assert [r["code"] for r in dlg.references()] == ["page"]
        assert dlg._list.topLevelItemCount() == 1  # (only the references, as it opens)
        dlg._only_refs.setChecked(False)
        assert dlg._list.topLevelItemCount() == 2
        assert dlg._b_replace.isEnabled() and dlg.replace_references()
        assert changed == [doc] and doc.dirty
        (hit,) = doc.find_text('"FxNew"', scopes={FR.SCOPE_EXPR})
        assert hit.value == 'PageDisplay("FxNew")'
        assert "1 reference given the new name." in dlg._status.text()
    finally:
        dlg.close()


def test_the_window_replaces_a_table_reference_in_place(fx_install):
    _app()
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.check_project import CheckProjectDialog
    from ctgedit.workers import check_worker as CW

    pdir = fx_install.project("FxMain")
    assert Project(pdir).update_page_record("FxShapes", {"ENTRY_FUNC": ENTRY})
    (f,) = [f for f in CW.table_fields(pdir) if f["field"] == "ENTRY_FUNC" and f["name"] == "FxShapes"]
    result = {
        "warnings": [
            {
                "kind": "page",
                "name": "FxOld",
                "message": "the project has no page FxOld",
                "line": 2,
                "column": 13,
            }
        ]
    }
    dlg = CheckProjectDialog(None, pdir, renames={"FxOld": "FxNew"})
    try:
        dlg.check = lambda: False  # (no helper process here)
        dlg.take({"rows": CW.table_rows(pdir, f, result), "checked": 0, "pages": 0})
        dlg._list.setCurrentItem(dlg._list.topLevelItem(0))
        assert not dlg._b_goto.isEnabled()
        assert dlg.replace_references()
        assert (
            Project(pdir)
            .page_record("FxShapes")["ENTRY_FUNC"]
            .startswith('Sleep(0);\r\nPageDisplay("FxNew");')
        )
        assert "1 reference given the new name." in dlg._status.text()
    finally:
        dlg.close()


CI = 'FUNCTION GoOld()\r\n\t_PageDisplay("FxOld"); Message("FxOld", "FxOld", 0);\r\nEND\r\n'


def _ci_warning(path):
    return {
        "kind": "page",
        "name": "FxOld",
        "message": "the project has no page FxOld (the runtime cannot display it)",
        "line": 2,
        "column": 15,
        "file": path,
        "function": "GoOld",
        "project": "FxMain",
    }


def test_the_project_check_lists_the_cicode_files(fx_install, monkeypatch):
    from ctgedit.sim import client as C
    from ctgedit.workers import check_worker as CW

    pdir = fx_install.project("FxMain")
    path = os.path.join(pdir, "fxold.ci")
    monkeypatch.setattr(C, "open_session", lambda d, **_k: _Engine(functions=[_ci_warning(path)]))
    ans = CW.check({"projects": [pdir], "engine": ""})
    assert not ans["engine"]
    (row,) = [r for r in ans["rows"] if r.get("file")]
    assert (row["name"], row["object"], row["subject"], row["line"], row["column"]) == (
        "fxold.ci",
        "GoOld, line 2",
        "FxOld",
        2,
        15,
    )


def test_replace_references_changes_a_cicode_file_after_a_backup(fx_install, tmp_path):
    from ctgedit.infra import backups
    from ctgedit.workers import check_worker as CW

    path = os.path.join(fx_install.project("FxMain"), "fxold.ci")
    with open(path, "wb") as fh:
        fh.write(CI.encode("cp1252"))
    edit = {"file": path, "line": 2, "column": 15, "old": "FxOld", "new": "FxNewName"}
    folder = str(tmp_path / "backup")
    assert CW.replace_in_files([edit], folder) == (1, [])
    with open(path, "rb") as fh:
        assert fh.read().decode("cp1252") == CI.replace('_PageDisplay("FxOld")', '_PageDisplay("FxNewName")')
    (entry,) = backups.read_manifest(folder)["entries"]
    with open(os.path.join(folder, entry["folder"], entry["file"]), "rb") as fh:
        assert fh.read().decode("cp1252") == CI and entry["library"] == os.path.abspath(path)
    # changed since the check: nothing is replaced, and it says so
    replaced, problems = CW.replace_in_files([edit], str(tmp_path / "again"))
    assert replaced == 0 and "no longer holds" in problems[0]
