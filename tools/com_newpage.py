"""Create a page through COM and check it, then clean up."""

import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctgedit.backend.graphics_builder import GraphicsBuilder
from ctgedit.format.page import parse_ctg
from ctgedit.format.writer import serialize_ctg

PROJECT = "Example"
ELEMENT = "!CTGEDIT_NEW"
# The engine opens projects from the ACTIVE install (registry DefaultINIPath ->
# citect.ini [CTEDIT] USER=): another install's copy would be a different file.
from ctgedit.io.citect_config import live_project_dir  # noqa: E402

PROJECT_DIR = live_project_dir("Example")
NEW_CTG = os.path.join(PROJECT_DIR, ELEMENT + ".ctg")


def try_page_new(c):
    """PageNew arg-combos vary by version; try a few until one returns truthy/non-throwing."""
    attempts = [
        ("Example", "", "normal", 0, 0, 0),
        ("Example", "Standard", "normal", 0, 0, 0),
        ("Example", "", "Normal", 0, 1, 0),
        ("Example", "", "", 0, 0, 0),
    ]
    for args in attempts:
        try:
            r = c.PageNew(*args)
            return True, args, r
        except Exception as e:  # noqa: BLE001
            last = (args, repr(e))
    return False, last[0], last[1]


def main():
    created = drew = saved = read_ok = roundtrip = compiled = False
    info = {}
    try:
        with GraphicsBuilder(visible=False) as gb:
            c = gb.com
            with contextlib.suppress(Exception):
                c.ProjectSelect(PROJECT)
            created, args, r = try_page_new(c)
            info["page_new_args"] = args
            info["page_new_ret"] = repr(r) if created else r
            if created:
                with contextlib.suppress(Exception):
                    c.DrawRectangle(40, 40, 240, 160)
                    drew = True
                with contextlib.suppress(Exception):
                    c.DrawText("Hello from ctgedit", 50, 70)
                with contextlib.suppress(Exception):
                    c.PageSaveAs(PROJECT, ELEMENT)
                    saved = True
            # best-effort compile (void return; success = no exception)
            try:
                c.ProjectCompile()
                compiled = True
            except Exception as e:  # noqa: BLE001
                info["compile_err"] = repr(e)[:200]
            with contextlib.suppress(Exception):
                c.PageClose()
        # now validate the engine-written page with OUR code
        if saved and os.path.exists(NEW_CTG):
            data = open(NEW_CTG, "rb").read()
            p = parse_ctg(NEW_CTG)
            read_ok = p.page is not None
            info["our_objects"] = len(p.page.objects) if p.page else 0
            roundtrip = serialize_ctg(p.container, p.page, p.attributes) == data
    finally:
        # delete the test page via COM, then ensure files gone
        with contextlib.suppress(Exception):
            with GraphicsBuilder(visible=False) as gb2:
                with contextlib.suppress(Exception):
                    gb2.com.ProjectSelect(PROJECT)
                with contextlib.suppress(Exception):
                    gb2.com.PageDelete(PROJECT, ELEMENT)
        for ext in (".ctg", ".ctF", ".ctf"):
            with contextlib.suppress(Exception):
                pth = os.path.join(PROJECT_DIR, ELEMENT + ext)
                if os.path.exists(pth):
                    os.remove(pth)
        GraphicsBuilder.reap()

    print(
        "created:%s drew:%s saved:%s | our-reader:%s objects:%s | byte-exact-roundtrip:%s | compiled(no-exc):%s"
        % (created, drew, saved, read_ok, info.get("our_objects"), roundtrip, compiled),
        flush=True,
    )
    print("info:", {k: v for k, v in info.items() if k != "our_objects"}, flush=True)
    print("NEWPAGE DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
