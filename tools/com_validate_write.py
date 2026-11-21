"""Write an edited page, open it in the Graphics Builder, then clean up."""

import copy
import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctgedit.backend.graphics_builder import GraphicsBuilder
from ctgedit.format.page import parse_ctg
from ctgedit.format.writer import serialize_ctg

# The engine opens projects from the ACTIVE install (registry DefaultINIPath ->
# citect.ini [CTEDIT] USER=): another install's copy would be a different file.
from ctgedit.io.citect_config import live_project_dir  # noqa: E402

PROJECT_DIR = live_project_dir("Example")
SRC = os.path.join(PROJECT_DIR, "!PUMP_A_ACC.ctg")
ELEMENT = "!CTGEDIT_T"
TEST_CTG = os.path.join(PROJECT_DIR, ELEMENT + ".ctg")


def make_edited():
    p = parse_ctg(SRC)
    n0 = len(p.page.objects)
    # structural edit: duplicate an object (exercises size/next_link recompute)
    p.page.objects.insert(3, copy.deepcopy(p.page.objects[2]))
    data = serialize_ctg(p.container, p.page, p.attributes)
    with open(TEST_CTG, "wb") as f:
        f.write(data)
    return n0 + 1, len(data)


def cleanup():
    for ext in (".ctg", ".ctF", ".ctf"):
        with contextlib.suppress(Exception):
            pth = os.path.join(PROJECT_DIR, ELEMENT + ext)
            if os.path.exists(pth):
                os.remove(pth)


def main():
    expected_objs, size = make_edited()
    print("wrote edited test page: %s (%d bytes, %d objects)" % (TEST_CTG, size, expected_objs), flush=True)
    result = {"opened": False, "name": None}
    try:
        with GraphicsBuilder(visible=False) as gb:
            c = gb.com
            with contextlib.suppress(Exception):
                c.ProjectSelect("Example")
            with contextlib.suppress(Exception):
                c.PageOpen("Example", ELEMENT)
            with contextlib.suppress(Exception):
                result["name"] = str(c.PageName())
            with contextlib.suppress(Exception):
                result["w"] = int(c.PageAppearanceGet()[6]) if False else None
            result["opened"] = result["name"] is not None and ELEMENT.lower() in str(result["name"]).lower()
            with contextlib.suppress(Exception):
                c.PageClose()
    finally:
        cleanup()
        GraphicsBuilder.reap()
    print("engine opened our written page:", result["opened"], "| PageName:", result["name"], flush=True)
    print("VALIDATE_WRITE DONE", flush=True)
    return 0 if result["opened"] else 1


if __name__ == "__main__":
    sys.exit(main())
