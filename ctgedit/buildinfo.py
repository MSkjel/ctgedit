"""Version, commit and build date of the running ctgedit.

The version is the newest vX.Y.Z tag of the checkout, the next patch level on a later commit
(so a build of it is told apart from the release), 0.0.0 without a tag; a built program has
it stamped (write_build_info)."""

from __future__ import annotations

import datetime
import functools
import json
import os
import re
import subprocess
import sys
from typing import Dict, Optional

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(PACKAGE_DIR)
BUILD_INFO_NAME = "_build_info.json"
BUILD_INFO_FILE = os.path.join(PACKAGE_DIR, BUILD_INFO_NAME)
#: the release tags only (vX.Y.Z, not v1.0.0-rc1)
DESCRIBE_RELEASE = ("describe", "--tags", "--match", "v[0-9]*.[0-9]*.[0-9]*", "--exclude", "v*[!0-9.]*")
NO_VERSION = "0.0.0"


def _git(*args: str, cwd: str = REPO_ROOT) -> str:
    """One git query ('' when git or the repository is not available)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash
    try:
        out = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True, timeout=5, creationflags=flags
        )
    except OSError, subprocess.SubprocessError:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def version_from_describe(described: str) -> str:
    """The version of a ``git describe`` of the release tags: 'v0.1.11' -> '0.1.11',
    'v0.1.11-3-g6d9d2bb' (three commits later) -> '0.1.12', '' (no tag) -> '0.0.0'."""
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)(?:-(\d+)-g[0-9a-f]+)?$", described.strip())
    if not m:
        return NO_VERSION
    major, minor, patch, ahead = m.groups()
    return "%d.%d.%d" % (int(major), int(minor), int(patch) + (1 if ahead and int(ahead) else 0))


def git_identity(cwd: str = REPO_ROOT) -> Dict[str, str]:
    """``{'version': '0.0.2', 'commit': 'v0.0.1-3-g6d9d2bb-dirty', 'date': '2026-09-25'}``
    of the checkout at ``cwd`` (commit = ``git describe``, date = the commit's)."""
    if not os.path.exists(os.path.join(cwd, ".git")):  # a worktree has a .git FILE
        return {"version": NO_VERSION, "commit": "", "date": ""}
    return {
        "version": version_from_describe(_git(*DESCRIBE_RELEASE, cwd=cwd)),
        "commit": _git("describe", "--always", "--dirty", "--tags", cwd=cwd),
        "date": _git("log", "-1", "--format=%cs", cwd=cwd),
    }


@functools.lru_cache(maxsize=1)
def build_info() -> Dict[str, str]:
    """version / commit / date / built / source ('build', 'git' or 'unknown')."""
    info = {"version": NO_VERSION, "commit": "", "date": "", "built": "", "source": "unknown"}
    try:
        with open(BUILD_INFO_FILE, encoding="utf-8") as fh:
            stamped = json.load(fh)
    except OSError, ValueError:
        stamped = None
    if isinstance(stamped, dict):
        info.update({k: str(v) for k, v in stamped.items() if k in info})
        info["source"] = "build"
        return info
    if not getattr(sys, "frozen", False):
        ident = git_identity()
        info["version"] = ident["version"]
        if ident["commit"]:
            info.update(ident)
            info["source"] = "git"
    return info


def version() -> str:
    """'0.1.11' (``ctgedit.__version__``)."""
    return build_info()["version"]


def version_string(info: Optional[Dict[str, str]] = None) -> str:
    """'0.0.1 (6d9d2bb, 2026-09-25)' -- the one line a bug report needs."""
    info = info or build_info()
    extra = [x for x in (info.get("commit") or "unknown commit", info.get("date")) if x]
    return "%s (%s)" % (info.get("version") or NO_VERSION, ", ".join(extra))


def write_build_info(path: str, cwd: str = REPO_ROOT) -> Dict[str, str]:
    """Stamp the build identity into ``path`` (called by the PyInstaller spec;
    the file is bundled as ``ctgedit/_build_info.json``)."""
    ident = git_identity(cwd)
    info = {
        "version": ident["version"],
        "commit": ident["commit"] or "unknown",
        "date": ident["date"],
        "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)
    return info


# the process caches of this module (infra/caches.py: when each is emptied)
from .infra import caches as _caches  # noqa: E402

_caches.register("buildinfo", build_info, on=())
