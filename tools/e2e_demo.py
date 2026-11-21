"""Open, edit, save, then check the Graphics Builder opens the result."""

import os
import shutil
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctgedit.editing import EditableDocument
from ctgedit.backend.graphics_builder import GraphicsBuilder
from ctgedit.format.page import parse_ctg
from ctgedit.format.writer import get_object_rect1

PROJECT_DIR = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example"
ELEMENT = "!PUMP_A_ACC"
CTG = os.path.join(PROJECT_DIR, ELEMENT + ".ctg")
CTF = os.path.join(PROJECT_DIR, ELEMENT + ".ctF")


def main():
    bak = CTG + ".e2e_bak"
    bakf = CTF + ".e2e_bak"
    shutil.copy2(CTG, bak)
    had_ctf = os.path.exists(CTF)
    if had_ctf:
        shutil.copy2(CTF, bakf)
    opened = None
    moved = None
    try:
        # 1) open + edit through the real editing API
        doc = EditableDocument.open(CTG)
        before = get_object_rect1(doc.objects[2])
        doc.move_object(2, 30, 15)
        moved = (before, get_object_rect1(doc.objects[2]))
        # 2) save in-place (drops .ctF so the engine must re-read our bytes)
        doc.save()
        # 3) confirm on disk
        reparsed = parse_ctg(CTG)
        disk_rect = get_object_rect1(reparsed.page.objects[2])
        # 4) the live engine opens our edited page
        with GraphicsBuilder(visible=False) as gb:
            c = gb.com
            with contextlib.suppress(Exception):
                c.ProjectSelect("Example")
            with contextlib.suppress(Exception):
                c.PageOpen("Example", ELEMENT)
            name = None
            with contextlib.suppress(Exception):
                name = str(c.PageName())
            opened = name is not None and ELEMENT.lower() in name.lower()
            with contextlib.suppress(Exception):
                c.PageClose()
    finally:
        shutil.copy2(bak, CTG)
        os.remove(bak)
        if had_ctf:
            shutil.copy2(bakf, CTF)
            os.remove(bakf)
        GraphicsBuilder.reap()

    print("edit (move obj#2 +30,+15): rect1 %s -> %s" % moved, flush=True)
    print("on-disk after save:", disk_rect, "(matches edit:", disk_rect == moved[1], ")", flush=True)
    print("LIVE ENGINE OPENED OUR EDITED PAGE:", opened, flush=True)
    print("E2E", "PASS" if (opened and disk_rect == moved[1]) else "FAIL", flush=True)
    return 0 if opened else 1


if __name__ == "__main__":
    sys.exit(main())
