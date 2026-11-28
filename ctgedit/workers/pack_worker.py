"""Pack Database and Pack Libraries in a helper process."""

from __future__ import annotations

import os
from typing import List

#: the request / answer format (both sides check it)
FORMAT = "ctgedit-pack/1"


def pack(req: dict, progress=None, skip_now=None) -> dict:
    """Pack the tables of every project folder of ``req['dirs']``
    (pgregister.pack_project_tables)."""
    from ..io.pgregister import pack_project_tables

    dirs = list(req.get("dirs") or ())
    ans: dict = {"format": FORMAT, "projects": []}
    for d in dirs:
        name = os.path.basename(os.path.normpath(d))

        def step(i, n, table, name=name):
            return True if progress is None else progress(i, n, "%s: %s" % (name, table))

        rep = pack_project_tables(d, step)
        ans["projects"].append(dict(rep, project=name, failed=[list(f) for f in rep["failed"]]))
    return ans


def pack_libraries(req: dict, progress=None, skip_now=None) -> dict:
    """Pack the libraries of every project folder of ``req['dirs']`` into the backup
    ``req['backup']``, and of the folders of ``req['prune']`` the composition-cache instances
    no page links (``req['open_links']``: those the editor's open pages link). A PackError
    stops the pack: ``stopped_by`` says why."""
    from ..format.projects import PackError, pack_composition_caches
    from ..format.projects import pack_libraries as pack_folder

    dirs = list(req.get("dirs") or ())
    prune = set(req.get("prune") or ())
    links = {tuple(x) for x in req.get("open_links") or ()}
    ans: dict = {"format": FORMAT, "files": 0, "saved": 0, "packed": [], "failures": [], "pruned": 0}
    for j, d in enumerate(dirs):
        if progress is not None and progress(j, len(dirs), os.path.basename(os.path.normpath(d))) is False:
            break
        try:
            res = pack_folder(d, backup=req["backup"])
        except PackError as e:
            ans["stopped_by"] = str(e)
            break
        ans["files"] += res.changed
        ans["saved"] += res.saved
        ans["packed"] += list(res.packed)
        ans["failures"] += [list(f) for f in res.failures]
        if d in prune:
            try:
                cres = pack_composition_caches(d, backup=req["backup"], extra_links=links)
                ans["pruned"] += cres.deleted
                ans["packed"] += list(cres.files)
            except Exception as e:  # noqa: BLE001  # swallow-ok: the caches never stop a pack; said
                ans["failures"].append([os.path.join(d, "_CompositionCaches"), str(e)])
    return ans


def command(request: str, answer: str) -> List[str]:
    from ..infra import workers

    return workers.command("--pack", "ctgedit.workers.pack_worker", "main", [request, answer])


def main(argv=None) -> int:
    """``ctgedit --pack <request.json> <answer.json>``."""
    from .batchjob import helper_main

    def dispatch(req, progress, skip_now):
        if req.get("mode") == "libraries":
            return pack_libraries(req, progress, skip_now)
        return pack(req, progress, skip_now)

    return helper_main(argv, "--pack", FORMAT, dispatch)
