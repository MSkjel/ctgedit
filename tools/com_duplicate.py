"""Check that PageSaveAs can create a page from a registered one."""

import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctgedit.backend.graphics_builder import GraphicsBuilder
from ctgedit.format.page import parse_ctg

PROJECT = "Example"
SRC = "!PUMP_A_ACC"
NEW = "!CTGEDIT_DUP"
# The engine opens projects from the ACTIVE install (registry DefaultINIPath ->
# citect.ini [CTEDIT] USER=): another install's copy would be a different file.
from ctgedit.io.citect_config import live_project_dir  # noqa: E402

PROJECT_DIR = live_project_dir("Example")
NEW_CTG = os.path.join(PROJECT_DIR, NEW + ".ctg")


def main():
    dup = read_ok = False
    objs = None
    try:
        with GraphicsBuilder(visible=False) as gb:
            dup = gb.duplicate_page(PROJECT, SRC, NEW)
        if os.path.exists(NEW_CTG):
            p = parse_ctg(NEW_CTG)
            read_ok = p.page is not None
            objs = len(p.page.objects) if p.page else 0
    finally:
        with contextlib.suppress(Exception):
            with GraphicsBuilder(visible=False) as gb2:
                gb2.delete_page(PROJECT, NEW)
        for ext in (".ctg", ".ctF", ".ctf"):
            with contextlib.suppress(Exception):
                pth = os.path.join(PROJECT_DIR, NEW + ext)
                if os.path.exists(pth):
                    os.remove(pth)
        GraphicsBuilder.reap()
    print(
        "duplicate_page:%s | new .ctg parses with our reader:%s | objects:%s" % (dup, read_ok, objs),
        flush=True,
    )
    print("DUPLICATE", "PASS" if (dup and read_ok) else "PARTIAL", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
