"""Rename Pages in a helper process."""

from __future__ import annotations

import os
from typing import List

#: the request / answer format (both sides check it)
FORMAT = "ctgedit-rename/1"


def rename(req: dict, progress=None, skip_now=None) -> dict:
    """Rename ``req['pairs']`` [(folder, old, new)], each project in one go
    (io.project.rename_pages). Answers the renames done, the projects that failed and the
    links and table entries changed."""
    from ..io.project import RenameStopped, rename_pages

    by_dir: dict = {}
    for d, old, new in req.get("pairs") or ():
        by_dir.setdefault(d, []).append((old, new))
    ans: dict = {"format": FORMAT, "done": [], "failed": [], "changed": [], "errors": []}
    for j, (d, pairs) in enumerate(by_dir.items()):
        project = os.path.basename(os.path.normpath(d))

        def step(i, n, what, j=j, project=project):
            if progress is None:
                return True
            return progress(j, len(by_dir), "%s: %s" % (project, what))

        try:
            rep = rename_pages(
                d,
                pairs,
                links=bool(req.get("links", True)),
                names=bool(req.get("names", True)),
                link_dirs=req.get("link_dirs") or (),
                progress=step,
            )
        except RenameStopped:
            break
        except Exception as e:  # noqa: BLE001  # swallow-ok: said as a failure, the other projects go on
            ans["failed"].append("%s: %s" % (project, e))
            continue
        ans["done"] += [[d, o, n] for o, n in rep["renamed"]]
        ans["changed"] += [list(x) for x in rep["links"] + rep["names"]]
        ans["errors"] += rep["errors"]
    return ans


def command(request: str, answer: str) -> List[str]:
    from ..infra import workers

    return workers.command("--rename", "ctgedit.workers.rename_worker", "main", [request, answer])


def main(argv=None) -> int:
    """``ctgedit --rename <request.json> <answer.json>``."""
    from .batchjob import helper_main

    return helper_main(argv, "--rename", FORMAT, rename)
