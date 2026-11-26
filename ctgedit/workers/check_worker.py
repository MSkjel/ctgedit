"""Tools > Check Project: Check Page's checks on every page of some projects, in a helper
process (the pages as saved; each project's Cicode compiled by its own engine session); and
Replace References, the page references it found given new names after Rename Pages."""

from __future__ import annotations

import os
from typing import List

#: the request / answer format (both sides check it)
FORMAT = "ctgedit-check/1"
#: at most this many rows come back (the answer says when there were more)
MAX_ROWS = 20000


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


def check(req: dict, progress=None, skip_now=None) -> dict:
    """The findings of every page of ``req['projects']``: Check Page's own checks, then
    each expression and command field compiled by the engine (``req['engine']``: the
    simulator to start; '' = the bundled one)."""
    from ..editing import EditableDocument
    from ..features import page_checks as PC
    from ..sim import client as C
    from ..sim.checks import check_field

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
    finally:
        for s in sessions.values():
            if s is not None:
                try:
                    s.close()
                except Exception:  # noqa: BLE001  # swallow-ok: a session that is gone already
                    pass
    return ans


def reference_options(name: str, an: int, field: str, old: str, column: int):
    """find_replace.FindOptions of one page reference the check found: the quoted ``old``
    page name at ``column`` (the engine's: its opening quote, 1-based) of field ``field`` of
    the object with AN ``an`` on page ``name``, and no other text (a regular expression:
    the characters before it kept as group 1)."""
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
    """The references ``edits`` [{name, an, field, old, new, column}] of ``doc`` given their
    new names (one find_replace.ReplaceReport for all); in a field, the last first (a new
    name of another length moves what follows it)."""
    from ..features import find_replace as FR

    total = FR.ReplaceReport()
    for e in sorted(edits, key=lambda e: (int(e["an"]), e["field"], -int(e["column"]))):
        if int(e["column"]) < 1:
            continue  # (where it is not known: not changed)
        opts = reference_options(e["name"], int(e["an"]), e["field"], e["old"], int(e["column"]))
        hits = FR.find_hits(doc, opts)
        total.add(FR.replace_hits(doc, hits, opts, r'\g<1>"%s"' % e["new"], label="replace references"))
    return total


def replace_references(req: dict, progress=None, skip_now=None) -> dict:
    """Rename Pages' Replace References on closed pages: ``req['edits']`` [{page, name, an,
    field, old, new, column}]; each changed page backed up (``req['backup']``) and saved."""
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
