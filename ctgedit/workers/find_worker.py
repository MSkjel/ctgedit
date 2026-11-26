"""Find and Replace across a project's closed pages, in a helper process."""

from __future__ import annotations

import os
from typing import List

#: the request / answer format (both sides check it)
FORMAT = "ctgedit-find/1"
#: at most this many rows come back (the list says when there were more)
MAX_ROWS = 20000


def options_to_json(opts) -> dict:
    return {
        "text": opts.text,
        "scopes": sorted(opts.scopes),
        "match": opts.match,
        "inside_genies": opts.inside_genies,
        "inside_templates": opts.inside_templates,
        "template_editable": opts.template_editable,
        "break_lock": opts.break_lock,
        "regex": opts.regex,
        "filters": {k: [f.include, f.exclude] for k, f in _filters(opts).items()},
    }


def _filters(opts) -> dict:
    return {"pages": opts.pages, "fields": opts.fields, "genies": opts.genies, "objects": opts.objects}


def options_from_json(d: dict):
    from ..features.find_replace import FindOptions, NameFilter

    filters = {
        k: NameFilter(str(v[0] or ""), str(v[1] or ""))
        for k, v in (d.get("filters") or {}).items()
        if k in ("pages", "fields", "genies", "objects")
    }
    return FindOptions(
        str(d["text"]),
        scopes=frozenset(d.get("scopes") or ()),
        match=str(d.get("match") or "part"),
        inside_genies=bool(d.get("inside_genies", True)),
        inside_templates=bool(d.get("inside_templates", False)),
        template_editable=bool(d.get("template_editable", False)),
        break_lock=bool(d.get("break_lock", False)),
        regex=bool(d.get("regex", False)),
        **filters,
    )


def pages_of(projects) -> List[str]:
    """The registered pages of ``projects`` (folders), in project then name order
    (batchjob.project_pages)."""
    from .batchjob import project_pages

    return project_pages(projects)


def _open(path: str, prefilter=None):
    """The page as a document, or None when ``prefilter`` (find_replace.PagePrefilter) says
    it cannot hold a hit (it is then not parsed)."""
    from ..editing import EditableDocument, read_with_identity

    # (with the identity of the file read: Replace All's batch save checks it)
    data, identity = read_with_identity(path)
    if prefilter is not None and not prefilter.may_match(path, data):
        return None
    return EditableDocument.from_bytes(data, path, identity=identity)


def find(req: dict, progress=None, skip_now=None) -> dict:
    """The rows of every closed page's hits. A request with ``part`` = [k, n] searches the
    pages k, k + n, k + 2n, ... (one of n helper processes searching at once: the Find
    dialog, Settings > Pages and libraries) and answers with each page's record;
    :func:`merge` makes of the n answers the one of a search of every page."""
    part = req.get("part")
    k, n = (int(part[0]), int(part[1])) if part else (0, 1)
    ans = _find_part(req, k, n, progress, skip_now)
    return ans if part else merge([ans])


def _find_part(req: dict, k: int, n: int, progress=None, skip_now=None) -> dict:
    """The records of pages k, k + n, ...: [page index, 'skip'] (open in an editor, or left
    out by the Pages filter),
    [index, 'failed', why] or [index, 'hits', rows]. Stops after a page that brings its
    rows past MAX_ROWS: the merged list is full by that page, its later pages are never
    read."""
    from ..features import find_replace as FR
    from ..features.object_labels import object_label

    opts = options_from_json(req["options"])
    skip = {os.path.normcase(os.path.abspath(p)) for p in req.get("skip") or ()}
    pages = pages_of(req["projects"])
    mine = range(k, len(pages), n)
    prefilter = FR.PagePrefilter(opts)
    records: list = []
    found = 0
    for j, i in enumerate(mine):
        path = pages[i]
        name = os.path.splitext(os.path.basename(path))[0]
        if progress is not None and progress(j, len(mine), name) is False:
            break
        if (
            os.path.normcase(os.path.abspath(path)) in skip
            or (skip_now is not None and skip_now(path))
            or not FR.page_allowed(opts, path)  # (left out by the Pages filter: not read)
        ):
            records.append([i, "skip"])
            continue
        try:
            doc = _open(path, prefilter)
            hits = FR.find_hits(doc, opts) if doc is not None else []
        except Exception as e:  # noqa: BLE001 -- swallow-ok: the page is reported, the search goes on
            records.append([i, "failed", "%s: %s" % (name, e)])
            continue
        rows = []
        labels: dict = {}
        for h in hits:
            if h.index >= 0 and h.index not in labels:
                labels[h.index] = object_label(doc, h.index)
            rows.append(
                {
                    "page": path,
                    "name": name,
                    "project": os.path.basename(os.path.dirname(path)),
                    "index": h.index,
                    "object": labels.get(h.index, "Page"),
                    "scope": h.scope,
                    "kind": h.kind,
                    "key": h.key,
                    "value": h.value,
                    "label": h.label,
                    "reason": h.reason,
                    "params": list(h.params),
                    "sub": h.sub,
                }
            )
        records.append([i, "hits", rows])
        found += len(rows)
        if found > MAX_ROWS:
            break
    return {"format": FORMAT, "part": [k, n], "pages": len(pages), "records": records}


def merge(answers: List[dict]) -> dict:
    """The answer of a search of every page from the answers of its parts (find with
    ``part``): the pages in their order, the rows up to MAX_ROWS (more = there were more),
    the pages searched until then and the ones that could not be read. A part's error is
    the answer; a part that stopped (Stop) ends the list where its pages end."""
    for a in answers:
        if a.get("error") or a.get("format") != FORMAT:
            return a if a.get("error") else {"format": FORMAT, "error": "format %r" % a.get("format")}
    total = int(answers[0].get("pages", 0)) if answers else 0
    by_page = {}
    for a in answers:
        for rec in a.get("records") or ():
            by_page[rec[0]] = rec
    rows, failed, searched = [], [], 0
    more = False
    for i in range(total):
        rec = by_page.get(i)
        if rec is None:
            break  # (its part stopped before it)
        if rec[1] == "skip":
            continue
        if rec[1] == "failed":
            failed.append(rec[2])
            continue
        searched += 1
        for row in rec[2]:
            if len(rows) >= MAX_ROWS:
                more = True
                break
            rows.append(row)
        if more:
            break
    out = {
        "format": FORMAT,
        "rows": rows,
        "pages": total,
        "searched": searched,
        "failed": failed,
        "more": more,
    }
    if any(a.get("stopped") for a in answers):
        out["stopped"] = True
    return out


def replace(req: dict, progress=None, skip_now=None) -> dict:
    """Replace All on the closed pages of ``req['projects']``; each changed page backed up
    and saved (a batch save: a page another save changed meanwhile, or whose project
    database is in use, is left and reported)."""
    from . import batchjob
    from ..features import find_replace as FR

    opts = options_from_json(req["options"])
    replacement = str(req.get("replacement") or "")
    out = batchjob.batch_answer(FORMAT, req.get("backup"), replaced=0, parameters=0, read_only=0, problems=[])
    pages = list(req.get("pages") or pages_of(req["projects"]))
    prefilter = FR.PagePrefilter(opts)

    def edit(path):
        if not FR.page_allowed(opts, path):
            return None  # (left out by the Pages filter)
        doc = _open(path, prefilter)
        if doc is None:
            return None  # (it cannot hold the text: nothing to replace)
        hits = FR.find_hits(doc, opts)
        if not any(h.replaceable for h in hits):
            out["read_only"] += len(hits)
            return None
        rep = FR.replace_hits(doc, hits, opts, replacement, "replace all")
        out["read_only"] += rep.read_only
        out["problems"].extend("%s: %s" % (batchjob.page_name(path), p) for p in rep.problems)
        return (doc, rep) if rep.replaced else None

    for _name, rep in batchjob.edit_pages(
        pages, edit, out, req=req, kind="replace", progress=progress, skip_now=skip_now
    ):
        out["replaced"] += rep.replaced
        out["parameters"] += rep.parameters
    return out


def command(request: str, answer: str) -> List[str]:
    from ..infra import workers

    return workers.command("--find", "ctgedit.workers.find_worker", "main", [request, answer])


def main(argv=None) -> int:
    """``ctgedit --find <request.json> <answer.json>``: a find or a replace (the run's
    include chains and genie declarations are read once: infra/runmemo)."""
    from .batchjob import helper_main

    def dispatch(req, progress, skip_now):
        return (
            replace(req, progress, skip_now)
            if req.get("mode") == "replace"
            else find(req, progress, skip_now)
        )

    return helper_main(argv, "--find", FORMAT, dispatch, memo=True)
