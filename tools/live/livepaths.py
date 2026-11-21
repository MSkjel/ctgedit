"""Paths for the live tools."""

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS = os.path.join(REPO, "tools")
ROOT = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"


def setup() -> None:
    """Put the repo, tools/ and tools/live on the import path."""
    for p in (os.path.join(TOOLS, "live"), TOOLS, REPO):
        if p not in sys.path:
            sys.path.insert(0, p)


def out_dir(tool: str, argv=None) -> str:
    """The folder ``tool`` writes into (made if needed): ``--out <dir>`` from ``argv`` (taken
    out of it), else %TEMP%/ctgedit_live/<tool>."""
    argv = sys.argv if argv is None else argv
    path = ""
    if "--out" in argv:
        k = argv.index("--out")
        if k + 1 < len(argv):
            path = argv[k + 1]
            del argv[k : k + 2]
    if not path:
        path = os.path.join(tempfile.gettempdir(), "ctgedit_live", tool)
    os.makedirs(path, exist_ok=True)
    return path
