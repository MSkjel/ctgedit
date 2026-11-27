"""Check Project and Replace References in a helper process."""

from __future__ import annotations

import os
from typing import List

#: the request / answer format (both sides check it)
FORMAT = "ctgedit-check/1"
#: at most this many rows come back (the answer says when there were more)
MAX_ROWS = 20000
#: the Cicode of the project tables checked with the pages: {table: columns}
TABLE_CICODE = {
    "pages.dbf": ("ENTRY_FUNC", "EXIT_FUNC", "BGND_FUNC", "READY_FUNC", "ACT_FUNC", "DEACT_FUNC"),
    "pagemenu.dbf": ("COMMAND",),
    "keyboard.dbf": ("CMD",),
}


def pages_of(projects) -> List[str]:
    """The registered pages of ``projects`` (folders), in project then name order."""
    from .batchjob import project_pages

    return project_pages(projects)


def _row(path: str, doc, f) -> dict:
    from ..format import geometry as G

    an = -1
    if f.index is not None:
        objs = doc.parsed.page.objects
        if 0 <= f.index < len(objs):
            an = G.live_an(objs[f.index])
    return {
        "page": path,
        "name": os.path.splitext(os.path.basename(path))[0],
        "project": os.path.basename(os.path.dirname(path)),
        "index": -1 if f.index is None else f.index,
        "an": an,
        "object": f.label or ("Page" if f.index is None else ""),
        "level": f.level,
        "code": f.code,
        "message": f.message,
        "field": f.field,
        "subject": f.subject,
        "column": f.column,
    }


def _table_labels(table: str, row: dict, col: str):
    """(name, object, field label) a table value is listed under."""
    from ..features.find_replace import PAGE_COLUMN_NAMES
    from ..io.project import MENU_LEVELS

    if table == "pages.dbf":
        return row.get("NAME", "").strip(), "Page Properties", PAGE_COLUMN_NAMES.get(col, col)
    if table == "pagemenu.dbf":
        path = ".".join(v for v in (row.get(c, "").strip() for c in MENU_LEVELS) if v)
        return "Menu %s" % row.get("PAGE", "").strip(), path, "Command"
    return "Keyboard commands", "Key %s" % row.get("KEY_SEQ", "").strip(), "Command"


def table_fields(project_dir: str) -> List[dict]:
    """The Cicode in ``project_dir``'s tables (TABLE_CICODE), one dict per value: table, record,
    field, text, name, object, label."""
    from ..format.dbf import Dbf
    from ..io.pgregister import find_file

    out = []
    for table, cols in TABLE_CICODE.items():
        path = find_file(project_dir, table)
        if path is None:
            continue
        with Dbf(path, writable=False) as d:
            use = [c for c in cols if c in d.fmap]
            for i in range(d.nrec):
                r = d.read(i)
                if r[:1] == b"*":
                    continue
                for c in use:
                    text = d.field(r, c)
                    if text.strip():
                        name, obj, label = _table_labels(table, d.decode(r), c)
                        out.append(
                            {
                                "table": path,
                                "record": i,
                                "field": c,
                                "text": text,
                                "name": name,
                                "object": obj,
                                "label": label,
                            }
                        )
    return out


def table_rows(project_dir: str, f: dict, result: dict) -> List[dict]:
    """check_worker rows of one engine check of table value ``f`` (table_fields)."""
    base = {
        "page": f["table"],
        "name": f["name"],
        "project": os.path.basename(project_dir),
        "index": -1,
        "an": -1,
        "object": f["object"],
        "field": f["field"],
        "table": f["table"],
        "record": f["record"],
    }
    rows = []
    for w in result.get("warnings") or ():
        rows.append(
            dict(
                base,
                level="warning",
                code=str(w.get("kind") or "compile"),
                message="%s: %s" % (f["label"], w.get("message") or ""),
                subject=str(w.get("name") or ""),
                line=int(w.get("line") or 0),
                column=int(w.get("column") or 0),
            )
        )
    for e in result.get("errors") or ():
        line, col = int(e.get("line") or 0), int(e.get("column") or 0)
        where = " at line %d, column %d" % (line, col) if line and col else ""
        rows.append(
            dict(
                base,
                level="error",
                code="compile",
                message="%s does not compile%s: %s"
                % (f["label"], where, e.get("message") or e.get("name") or ""),
                subject="",
                line=0,
                column=0,
            )
        )
    return rows


def function_rows(project_dir: str, result: dict) -> List[dict]:
    """check_worker rows of the engine's check of the project's Cicode files
    (ctsim/checkFunctions)."""
    rows = []
    for level, problems in (("warning", result.get("warnings") or ()), ("error", result.get("errors") or ())):
        for w in problems:
            path, line = str(w.get("file") or ""), int(w.get("line") or 0)
            where = "%s, line %d" % (w["function"], line) if w.get("function") else "Line %d" % line
            rows.append(
                {
                    "page": path,
                    "name": os.path.basename(path),
                    "project": str(w.get("project") or os.path.basename(project_dir)),
                    "index": -1,
                    "an": -1,
                    "object": where if line else "",
                    "level": level,
                    "code": str(w.get("kind") or "compile") if level == "warning" else "compile",
                    "message": str(w.get("message") or w.get("name") or ""),
                    "field": "",
                    "subject": str(w.get("name") or "") if level == "warning" else "",
                    "line": line,
                    "column": int(w.get("column") or 0),
                    "file": path,
                }
            )
    return rows


def check(req: dict, progress=None, skip_now=None) -> dict:
    """The findings of every page of ``req['projects']``: Check Page's checks, then the engine's
    check of each expression and command, of the projects' table Cicode and of their Cicode
    files (``req['engine']``: the simulator, '' = bundled)."""
    from ..editing import EditableDocument
    from ..features import page_checks as PC
    from ..sim import client as C
    from ..sim.checks import CHECK_TIMEOUT_S, check_field, check_text

    pages = pages_of(req["projects"])
    ans: dict = {"format": FORMAT, "rows": [], "pages": len(pages), "checked": 0, "failed": [], "engine": {}}
    sessions: dict = {}

    def session(d: str):
        """The engine of project folder ``d`` (None: it could not start; said once)."""
        if d in sessions:
            return sessions[d]
        try:
            s = C.open_session(d, engine_path=req.get("engine") or None, viewer=False)
        except Exception as e:  # noqa: BLE001  # swallow-ok: the answer says it, the other checks go on
            ans["engine"][os.path.basename(d)] = str(e).strip() or type(e).__name__
            s = None
        sessions[d] = s
        return s

    try:
        for j, path in enumerate(pages):
            name = os.path.splitext(os.path.basename(path))[0]
            if progress is not None and progress(j, len(pages), name) is False:
                break
            try:
                doc = EditableDocument.open(path)
                rep = PC.check_document(doc)
            except Exception as e:  # noqa: BLE001  # swallow-ok: the page is reported, the check goes on
                ans["failed"].append("%s: %s" % (name, e))
                continue
            findings = list(rep.findings)
            d = os.path.dirname(os.path.abspath(path))
            s = session(d)
            for f in PC.expression_fields(doc) if s is not None else ():
                try:
                    result = check_field(s, f.text, f.kind)
                except Exception as e:  # noqa: BLE001  # swallow-ok: the answer says it
                    ans["engine"][os.path.basename(d)] = str(e).strip() or type(e).__name__
                    sessions[d] = None
                    break
                findings.extend(PC.engine_findings(doc, f, result))
            ans["checked"] += 1
            for f in findings:
                if len(ans["rows"]) >= MAX_ROWS:
                    ans["more"] = True
                    return ans
                ans["rows"].append(_row(path, doc, f))
        for d in req["projects"]:
            s = session(d)
            if s is None or (progress is not None and progress(len(pages), len(pages), "tables") is False):
                continue
            for f in table_fields(d):
                try:
                    result = check_text(s, f["text"], "command")
                except Exception as e:  # noqa: BLE001  # swallow-ok: the answer says it
                    ans["engine"][os.path.basename(d)] = str(e).strip() or type(e).__name__
                    break
                ans["rows"].extend(table_rows(d, f, result))
                if len(ans["rows"]) >= MAX_ROWS:
                    ans["more"] = True
                    return ans
            if sessions.get(d) is None:
                continue
            try:
                result = s.request(
                    "checkFunctions",
                    {"project": os.path.basename(os.path.normpath(d))},
                    timeout=CHECK_TIMEOUT_S,
                )
            except Exception as e:  # noqa: BLE001  # swallow-ok: the answer says it
                ans["engine"][os.path.basename(d)] = str(e).strip() or type(e).__name__
                continue
            ans["rows"].extend(function_rows(d, result or {}))
    finally:
        for s in sessions.values():
            if s is not None:
                try:
                    s.close()
                except Exception:  # noqa: BLE001  # swallow-ok: a session that is gone already
                    pass
    return ans


def reference_options(name: str, an: int, field: str, old: str, column: int):
    """FindOptions matching only the quoted ``old`` at ``column`` (the engine's, 1-based) of
    field ``field`` of object AN ``an`` on page ``name``."""
    import re

    from ..features import find_replace as FR

    return FR.FindOptions(
        '^(.{%d})"%s"' % (column - 1, re.escape(old)),
        regex=True,
        scopes=frozenset({FR.SCOPE_EXPR}),
        pages=FR.NameFilter("^%s$" % re.escape(name)),
        objects=FR.NameFilter("^AN%d$" % an),
        fields=FR.NameFilter("^%s$" % re.escape(field)),
    )


def replace_in(doc, edits):
    """Give ``doc``'s references ``edits`` their new names, the last in a field first; one
    ReplaceReport."""
    from ..features import find_replace as FR

    total = FR.ReplaceReport()
    for e in sorted(edits, key=lambda e: (int(e["an"]), e["field"], -int(e["column"]))):
        if int(e["column"]) < 1:
            continue  # (where it is not known: not changed)
        opts = reference_options(e["name"], int(e["an"]), e["field"], e["old"], int(e["column"]))
        hits = FR.find_hits(doc, opts)
        total.add(FR.replace_hits(doc, hits, opts, r'\g<1>"%s"' % e["new"], label="replace references"))
    return total


def _offset(text: str, line: int, column: int):
    """The index of (line, column), both 1-based, in ``text``; None when it is not in it."""
    if line < 1 or column < 1:
        return None
    starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
    if line > len(starts):
        return None
    at = starts[line - 1] + column - 1
    return at if at < len(text) else None


def replace_in_tables(edits) -> tuple:
    """Give the table references ``edits`` (table, record, field, line, column, old, new) their
    new names, the last in a value first. Returns (replaced, problems)."""
    from ..format import journal, projectlock
    from ..format.dbf import Dbf
    from ..io import pgregister

    replaced, problems = 0, []
    by_table: dict = {}
    for e in edits:
        by_table.setdefault(e["table"], {}).setdefault(int(e["record"]), []).append(e)
    for path, records in by_table.items():
        d_dir = os.path.dirname(path)
        changed = 0
        with projectlock.project(d_dir), journal.transaction("replace references"), Dbf(path) as d:
            for rec, es in records.items():
                row = d.decode(d.read(rec))
                n = 0
                for e in sorted(es, key=lambda e: (e["field"], -int(e["line"]), -int(e["column"]))):
                    text = row.get(e["field"], "")
                    old, new = '"%s"' % e["old"], '"%s"' % e["new"]
                    at = _offset(text, int(e["line"]), int(e["column"]))
                    if at is None or text[at : at + len(old)] != old:
                        problems.append(
                            "%s, %s: it no longer holds %s there." % (e["name"], e["object"], old)
                        )
                        continue
                    row[e["field"]] = text[:at] + new + text[at + len(old) :]
                    n += 1
                if not n:
                    continue
                encoded = d.encode(row)
                if any(d.field(encoded, f) != row[f].rstrip(" ") for f in {e["field"] for e in es}):
                    problems.append("%s, %s: the new name would not fit." % (es[0]["name"], es[0]["object"]))
                    continue
                d.write(rec, encoded)
                replaced += n
                changed += n
            if changed:
                d.touch()
        if changed:
            pgregister.mark_uncompiled(d_dir)
    return replaced, problems


def replace_in_files(edits, backup: str) -> tuple:
    """Give the Cicode file references ``edits`` (file, line, column, old, new) their new names,
    the last in a file first, each file backed up into ``backup`` first. Returns (replaced,
    problems)."""
    from ..format import projectlock
    from ..format.fileio import write_atomic
    from ..infra import backups

    replaced, problems = 0, []
    by_file: dict = {}
    for e in edits:
        by_file.setdefault(e["file"], []).append(e)
    for path, es in by_file.items():
        name = os.path.basename(path)
        try:
            with projectlock.file(path):
                with open(path, "rb") as fh:
                    data = fh.read()
                text = data.decode("cp1252")
                n = 0
                for e in sorted(es, key=lambda e: (-int(e["line"]), -int(e["column"]))):
                    old, new = '"%s"' % e["old"], '"%s"' % e["new"]
                    at = _offset(text, int(e["line"]), int(e["column"]))
                    if at is None or text[at : at + len(old)] != old:
                        problems.append(
                            "%s line %d: it no longer holds %s there." % (name, int(e["line"]), old)
                        )
                        continue
                    text = text[:at] + new + text[at + len(old) :]
                    n += 1
                if not n:
                    continue
                out = text.encode("cp1252")
                project = os.path.basename(os.path.dirname(os.path.abspath(path)))
                os.makedirs(os.path.join(backup, project), exist_ok=True)
                write_atomic(os.path.join(backup, project, name), data)
                backups.add_entry(
                    backup, "references", {"library": os.path.abspath(path), "folder": project, "file": name}
                )
                write_atomic(path, out)
                replaced += n
        except (OSError, UnicodeError) as e:
            problems.append("%s: %s" % (name, getattr(e, "strerror", None) or e))
    return replaced, problems


def replace_references(req: dict, progress=None, skip_now=None) -> dict:
    """Replace References on closed pages: ``req['edits']``, each page backed up, then saved."""
    from . import batchjob
    from .find_worker import open_page

    by_page: dict = {}
    for e in req.get("edits") or ():
        by_page.setdefault(e["page"], []).append(e)
    out = batchjob.batch_answer(FORMAT, req.get("backup"), replaced=0, read_only=0, problems=[])

    def edit(path):
        doc = open_page(path)
        rep = replace_in(doc, by_page[path])
        out["read_only"] += rep.read_only
        out["problems"].extend("%s: %s" % (batchjob.page_name(path), p) for p in rep.problems)
        return (doc, rep) if rep.replaced else None

    for _name, rep in batchjob.edit_pages(
        list(by_page), edit, out, req=req, kind="references", progress=progress, skip_now=skip_now
    ):
        out["replaced"] += rep.replaced
    return out


def command(request: str, answer: str) -> List[str]:
    from ..infra import workers

    return workers.command("--check", "ctgedit.workers.check_worker", "main", [request, answer])


def main(argv=None) -> int:
    """``ctgedit --check <request.json> <answer.json>``."""
    from .batchjob import helper_main

    def dispatch(req, progress, skip_now):
        if req.get("mode") == "replace":
            return replace_references(req, progress, skip_now)
        return check(req, progress, skip_now)

    return helper_main(argv, "--check", FORMAT, dispatch)
