"""Check that objects from the factory load in the Graphics Builder."""

import contextlib
import os
import shutil
import sys

# the checkout this script lives in (a worktree validates its own code)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from ctgedit.backend.graphics_builder import GraphicsBuilder
from ctgedit.format.page import parse_ctg, parse_ctg_bytes
from ctgedit.format.writer import serialize_ctg
from ctgedit.format import factory, geometry as G

# The engine opens projects from the ACTIVE install (registry DefaultINIPath ->
# citect.ini [CTEDIT] USER=): another install's copy would be a different file.
from ctgedit.io.citect_config import live_project_dir  # noqa: E402

PROJECT_DIR = live_project_dir("Example")
ELEMENT = "!PUMP_A_ACC"
CTG = os.path.join(PROJECT_DIR, ELEMENT + ".ctg")
CTF = os.path.join(PROJECT_DIR, ELEMENT + ".ctF")
THUMB_OUT = os.path.join(os.path.dirname(__file__), "_factory_thumb.bin")

# !PUMP_A_ACC is a small popup page: keep everything inside it so the engine's
# thumbnail shows the objects.
NEW_OBJECTS = [
    ("line", dict(nodes=[(10, 10), (90, 50)], colour=0xFF0000, handle=900)),
    ("rectangle", dict(rect=(100, 10, 160, 50), colour=0x00FF00, handle=901)),
    ("rectangle_outline", dict(rect=(170, 10, 230, 50), colour=0x4080FF, handle=902)),
    ("ellipse", dict(rect=(10, 60, 70, 110), colour=0xFFA000, handle=903)),
    ("ellipse_outline", dict(rect=(80, 60, 140, 110), colour=0x00FFFF, handle=904)),
    ("polygon", dict(nodes=[(150, 60), (230, 70), (210, 110), (160, 105)], colour=0xA040FF, handle=905)),
    ("pipe", dict(nodes=[(10, 130), (120, 130), (120, 180)], colour=0x808080, handle=906)),
    ("text", dict(rect=(140, 125, 300, 145), text="Hello ctgedit", colour=0x000000, handle=907)),
    ("button", dict(rect=(150, 160, 250, 195), text="Press me", handle=908)),
    # (global.go is not on Example's include path: a placed symbol needs a
    # resolvable element, the same one tools/engine_roundtrip.py places)
    ("symbol", dict(rect=(260, 60, 324, 124), symbol=("servermon", "no", "ctl"), handle=909)),
    ("polyline", dict(nodes=[(20, 140), (60, 120), (90, 150)], colour=0x202020, handle=924)),
    ("number", dict(rect=(140, 150, 220, 170), text="PUMP_A_SPEED", handle=925)),
    # legacy V3/V4 kinds: bindings cleared on create
    ("string", dict(rect=(10, 200, 110, 220), text="Two state", handle=910)),
    ("bar_graph", dict(rect=(260, 130, 290, 220), handle=911)),
    ("slider", dict(rect=(295, 130, 325, 220), handle=912)),
    # (a resolvable element: CtDraw drops an OFF_SYMBOL it cannot resolve)
    ("animated_symbol", dict(rect=(240, 10, 300, 50), symbol=("icons", "help1", "ctl"), handle=913)),
    ("cicode_object", dict(rect=(10, 190, 50, 225), handle=914)),
    ("animation_keyboard", dict(rect=(60, 190, 90, 220), handle=915)),
    ("trend", dict(rect=(120, 150, 300, 225), handle=916)),
    # ActiveX family (cloned persisted control state)
    ("activex", dict(rect=(10, 10, 90, 90), handle=917)),
    ("process_analyst", dict(rect=(100, 10, 320, 120), handle=918)),
    ("scheduler", dict(rect=(10, 100, 150, 200), handle=919)),
    ("web_content", dict(rect=(160, 130, 320, 220), handle=920)),
    ("flashing_bitmap", dict(rect=(95, 95, 103, 103), handle=921)),
    ("digital_text", dict(rect=(10, 230, 110, 250), handle=922)),
    ("color_flood", dict(rect=(120, 230, 180, 290), handle=923)),
]


def build_edited(src: str) -> tuple[bytes, int]:
    only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    if only == ["none"]:
        only = []
    p = parse_ctg(src)
    # the editor measures text against the project's tag formats (EditableDocument
    # installs them per edit); without them a numeric display is not re-measured
    from ctgedit.format import textmetrics
    from ctgedit.io.project import Project

    fmts = {t.name.lower(): t.format for t in Project(PROJECT_DIR).variable_tags()}
    textmetrics.use_tag_formats(lambda n: fmts.get(n.strip().lower()))
    for kind, kw in NEW_OBJECTS:
        if only is not None and kind not in only:
            continue
        if kind == "flashing_bitmap":
            po = factory.make_bitmap_record(
                kw["handle"],
                kw["rect"],
                bytes([255, 0, 0, 255]) * 64,
                8,
                8,
                flash_rgba=bytes([0, 255, 0, 255]) * 64,
            )
            p.page.objects.append(po)
        else:
            if kind == "symbol":
                # a placed symbol derives its Local from the resolved element
                from ctgedit.format import placement
                from ctgedit.format.symbols import SymbolResolver

                kw = dict(kw, link_info=placement.element_info(SymbolResolver.for_page(src), *kw["symbol"]))
            p.page.objects.append(factory.create_object(kind, **kw))
        print("  +", kind, flush=True)
    p.page.object_count = len(p.page.objects)
    return serialize_ctg(p.container, p.page, p.attributes), p.page.object_count


def main():
    bak_ctg = CTG + ".ctgedit_bak"
    bak_ctf = CTF + ".ctgedit_bak"
    shutil.copy2(CTG, bak_ctg)
    had_ctf = os.path.exists(CTF)
    if had_ctf:
        shutil.copy2(CTF, bak_ctf)

    res = {"opened": False, "saved": False, "thumb": 0, "survived": None}
    try:
        data, want_count = build_edited(bak_ctg)
        with open(CTG, "wb") as f:
            f.write(data)
        if had_ctf:
            os.remove(CTF)
        print("overwrote %s: %d bytes, %d objects" % (ELEMENT, len(data), want_count), flush=True)

        with GraphicsBuilder(visible=False) as gb:
            c = gb.com
            with contextlib.suppress(Exception):
                c.ProjectSelect("Example")
            with contextlib.suppress(Exception):
                c.PageOpen("Example", ELEMENT)
            name = None
            with contextlib.suppress(Exception):
                name = str(c.PageName())
            res["opened"] = name is not None and ELEMENT.lower() in name.lower()
            print("PageOpen -> %r" % name, flush=True)
            try:
                c.PageSave()
                res["saved"] = True
            except Exception as e:  # noqa: BLE001
                print("PageSave FAILED: %s" % e, flush=True)
            with contextlib.suppress(Exception):
                c.PageClose()
            with contextlib.suppress(Exception):
                t = c.PageGetThumbnail("Example", ELEMENT)
                if t:
                    blob = bytes(bytearray(t))
                    res["thumb"] = len(blob)
                    with open(THUMB_OUT, "wb") as f:
                        f.write(blob)

        # the engine must have rewritten OUR file (not another install's copy)
        after = open(CTG, "rb").read()
        res["engine_rewrote"] = after != data
        if not res["engine_rewrote"]:
            print("ENGINE DID NOT REWRITE %s -- wrong project dir?" % CTG, flush=True)
        if res["saved"] and res["engine_rewrote"]:
            ours = {G.get_handle(po): po for po in parse_ctg_bytes(data).page.objects}
            rp_objs = parse_ctg(CTG).page.objects
            diffs = []
            for po in rp_objs:
                h = G.get_handle(po)
                mine = ours.get(h)
                if mine is None or h < 900:
                    continue
                d = []
                if mine.type_code != po.type_code:
                    d.append("tc %#x->%#x" % (mine.type_code, po.type_code))
                if mine.subtype_size != po.subtype_size:
                    d.append("dwFlags %#x->%#x" % (mine.subtype_size, po.subtype_size))
                if mine.an_str != po.an_str:
                    d.append("an_str %r->%r" % (mine.an_str, po.an_str))
                if mine.body_raw != po.body_raw:
                    d.append("body %d->%d bytes" % (len(mine.body_raw), len(po.body_raw)))
                if mine.raw_properties() != po.raw_properties():
                    a, b = mine.raw_properties(), po.raw_properties()
                    d.append(
                        "bag +%s -%s ~%s"
                        % (
                            sorted(set(b) - set(a)),
                            sorted(set(a) - set(b)),
                            sorted(k for k in a if k in b and a[k] != b[k]),
                        )
                    )
                if d:
                    diffs.append((h, d))
            res["engine_diffs"] = diffs
            for h, d in diffs:
                print("  engine normalised handle %d: %s" % (h, "; ".join(d)), flush=True)
        if res["saved"] and res["engine_rewrote"]:
            rp = parse_ctg(CTG)  # the ENGINE-rewritten file
            kinds = {}
            node_ok = None
            for po in rp.page.objects:
                kinds[po.type_code] = kinds.get(po.type_code, 0) + 1
            # node survival: find our polygon (tc=0x10) with 4 nodes
            for po in rp.page.objects:
                if po.type_code == 0x10:
                    nodes = G.get_object_nodes(po)
                    if nodes and len(nodes) == 4:
                        node_ok = nodes
            res["survived"] = {
                "count": len(rp.page.objects),
                "want": want_count,
                "has_line": 0x01 in kinds,
                "has_rect": kinds.get(0x0F, 0) >= 2,
                "has_ellipse": kinds.get(0x11, 0) >= 2,
                "has_poly": 0x10 in kinds,
                "has_pipe": 0x0B in kinds,
                "has_text": 0x05 in kinds,
                "has_button": 0x1001 in kinds,
                "polygon_nodes": node_ok,
            }
    finally:
        shutil.copy2(bak_ctg, CTG)
        os.remove(bak_ctg)
        if had_ctf:
            shutil.copy2(bak_ctf, CTF)
            os.remove(bak_ctf)
        elif os.path.exists(CTF):
            with contextlib.suppress(OSError):
                os.remove(CTF)
        GraphicsBuilder.reap()
        print("restored original %s" % ELEMENT, flush=True)

    print("RESULT:", res, flush=True)
    ok = (
        res["opened"]
        and res["saved"]
        and res.get("engine_rewrote")
        and res["survived"]
        and res["survived"]["count"] == res["survived"]["want"]
    )
    print("FACTORY ENGINE VALIDATION:", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
