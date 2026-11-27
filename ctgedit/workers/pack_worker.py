"""Pack Database in a helper process."""

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


def command(request: str, answer: str) -> List[str]:
    from ..infra import workers

    return workers.command("--pack", "ctgedit.workers.pack_worker", "main", [request, answer])


def main(argv=None) -> int:
    """``ctgedit --pack <request.json> <answer.json>``."""
    from .batchjob import helper_main

    return helper_main(argv, "--pack", FORMAT, pack)
