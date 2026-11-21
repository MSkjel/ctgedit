"""Check that our writer's output loads in the Graphics Builder, with backup and restore."""

import copy
import os
import shutil
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
ELEMENT = "!PUMP_A_ACC"
CTG = os.path.join(PROJECT_DIR, ELEMENT + ".ctg")
CTF = os.path.join(PROJECT_DIR, ELEMENT + ".ctF")


def main():
    bak_ctg = CTG + ".ctgedit_bak"
    bak_ctf = CTF + ".ctgedit_bak"
    shutil.copy2(CTG, bak_ctg)
    had_ctf = os.path.exists(CTF)
    if had_ctf:
        shutil.copy2(CTF, bak_ctf)

    result = {"opened": False, "name": None}
    try:
        # build edited output (duplicate an object -> 18) and overwrite the registered page
        p = parse_ctg(bak_ctg)
        p.page.objects.insert(3, copy.deepcopy(p.page.objects[2]))
        data = serialize_ctg(p.container, p.page, p.attributes)
        with open(CTG, "wb") as f:
            f.write(data)
        if had_ctf:
            os.remove(CTF)  # force the engine to re-read our .ctg
        print(
            "overwrote %s with edited output (%d bytes, 18 objects); removed .ctF" % (ELEMENT, len(data)),
            flush=True,
        )

        with GraphicsBuilder(visible=False) as gb:
            c = gb.com
            with contextlib.suppress(Exception):
                c.ProjectSelect("Example")
            with contextlib.suppress(Exception):
                c.PageOpen("Example", ELEMENT)
            with contextlib.suppress(Exception):
                result["name"] = str(c.PageName())
            result["opened"] = result["name"] is not None and ELEMENT.lower() in str(result["name"]).lower()
            with contextlib.suppress(Exception):
                c.PageClose()
    finally:
        shutil.copy2(bak_ctg, CTG)
        os.remove(bak_ctg)
        if had_ctf:
            shutil.copy2(bak_ctf, CTF)
            os.remove(bak_ctf)
        GraphicsBuilder.reap()
        print("restored original %s (+.ctF=%s)" % (ELEMENT, had_ctf), flush=True)

    print("ENGINE PARSED OUR EDITED BYTES:", result["opened"], "| PageName:", result["name"], flush=True)
    print("INPLACE DONE", flush=True)
    return 0 if result["opened"] else 1


if __name__ == "__main__":
    sys.exit(main())
