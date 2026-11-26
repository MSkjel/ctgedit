"""Tools > Check Project (workers/check_worker, ui/dialog/check_project) and Rename Pages'
Replace References."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtkit import app as _app


class _Engine:
    """ctsim/checkExpression as a session answers it: a page named in quotes that is not one
    of ``pages`` is a page the project does not have (kind "page", its name)."""

    def __init__(self, pages=("FxShapes", "FxGenies")):
        self.pages = {p.lower() for p in pages}
        self.closed = False

    def request(self, name, args, timeout=None):
        import re

        assert name == "checkExpression"
        warnings = []
        for m in re.finditer(r'PageDisplay\("([^"]*)"\)', args["text"]):
            if m.group(1).lower() not in self.pages:
                warnings.append(
                    {
                        "kind": "page",
                        "name": m.group(1),
                        "message": "the project has no page %s (the runtime cannot display it)" % m.group(1),
                        "column": m.start(1),
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
