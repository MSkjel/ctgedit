"""Compare our parser with the Graphics Builder COM engine (read only)."""

import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctgedit.backend.graphics_builder import GraphicsBuilder
from ctgedit.format.page import parse_ctg

PROJECT = "Example"
PAGE = "!PUMP_A_ACC"
CTG = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example\!PUMP_A_ACC.ctg"


def via_com():
    out = {"objects": [], "page_name": None, "error": None}
    with GraphicsBuilder(visible=False) as gb:
        c = gb.com
        with contextlib.suppress(Exception):
            c.ProjectSelect(PROJECT)
        ok = c.PageOpen(PROJECT, PAGE)
        out["page_open"] = ok
        with contextlib.suppress(Exception):
            out["page_name"] = str(c.PageName())
        try:
            sel = c.PageSelectFirstObject()
            while sel:
                rec = {}
                for attr in ("AttributeX", "AttributeY", "AttributeExtentX", "AttributeExtentY"):
                    with contextlib.suppress(Exception):
                        rec[attr] = int(getattr(c, attr)())
                out["objects"].append(rec)
                sel = c.PageSelectNextObject()
        except Exception as e:  # noqa: BLE001
            out["error"] = "enum: %r" % e
        with contextlib.suppress(Exception):
            c.PageClose()
    return out


def main():
    print("=== native parser ===", flush=True)
    p = parse_ctg(CTG)
    print("native objects:", len(p.page.objects), "header count:", p.page.object_count, flush=True)

    print("=== COM engine ===", flush=True)
    try:
        com = via_com()
    except Exception as e:  # noqa: BLE001
        print("COM ERROR:", type(e).__name__, e, flush=True)
        GraphicsBuilder.reap()
        return 2
    print("page_open:", com.get("page_open"), "page_name:", com.get("page_name"), flush=True)
    print("COM object count:", len(com["objects"]), flush=True)
    for i, o in enumerate(com["objects"][:6]):
        print("  com[%d] %s" % (i, o), flush=True)
    if com.get("error"):
        print("enum note:", com["error"], flush=True)
    print("MATCH object count:", len(com["objects"]) == len(p.page.objects), flush=True)
    print("VALIDATE DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
