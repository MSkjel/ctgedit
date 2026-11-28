"""Find Duplicate Pictures, in a helper process."""

from __future__ import annotations

from ..infra.paths import lib_path_dirs

import base64
import os
from typing import Dict, List, Optional

#: the request / answer format (both sides check it)
FORMAT = "ctgedit-pictures/1"
#: the thumbnail's longest side (pixels)
THUMB = 64


def _free_flags(objs, parents, i: int) -> bool:
    """Record ``i`` is a bitmap of the page's own (no library-linked, genie, symbol-set or
    transformed ancestor; not transformed itself)."""
    from ..format import placement

    if placement.carries(objs[i]):
        return False
    a = parents[i]
    while a is not None:
        if objs[a].subtype_size & 0x201 or objs[a].type_code == 0x0C or placement.carries(objs[a]):
            return False
        a = parents[a]
    return True


def _template_records(path: str, objs) -> set:
    """The records the page's template put there (they are locked on the page)."""
    try:
        from ..format.symbols import SymbolResolver
        from ..io.readerparts import symbols as RS

        extra = lib_path_dirs()
        return RS.template_records(objs, SymbolResolver.for_page(path, extra))
    except Exception:  # noqa: BLE001 -- swallow-ok: no template known, every record counts
        return set()


def _thumb(eb) -> dict:
    """A small copy of the picture (nearest pixels), base64 RGBA."""
    from ..format.bitmaps import normal_rgba, resample

    k = min(1.0, THUMB / max(eb.width, eb.height))
    w, h = max(1, round(eb.width * k)), max(1, round(eb.height * k))
    rgba = resample(normal_rgba(eb.rgba), eb.width, eb.height, w, h)
    return {"w": w, "h": h, "rgba": base64.b64encode(rgba).decode("ascii")}


def scan(project_dir: str, progress=None) -> dict:
    """The pictures the project's registered pages store more than once."""
    from ..format import bitmaps, forest
    from ..format import geometry as G
    from ..format.page import parse_ctg
    from ..io.updatepages import project_pages

    groups: Dict[str, dict] = {}
    failed: List[str] = []
    pages = project_pages(project_dir)
    for n, path in enumerate(pages):
        name = os.path.splitext(os.path.basename(path))[0]
        if progress is not None and progress(n, len(pages), name) is False:
            break
        try:
            objs = parse_ctg(path).page.objects
        except Exception as e:  # noqa: BLE001 -- swallow-ok: the page is reported, the scan goes on
            failed.append("%s: %s" % (name, e))
            continue
        parents = forest.parents(objs)
        tpl = None
        for i, po in enumerate(objs):
            if po.type_code != bitmaps.BITMAP_TC or bitmaps.dib_section(po) is None:
                continue
            if not _free_flags(objs, parents, i):
                continue
            if tpl is None:
                tpl = _template_records(path, objs)
            if i in tpl:
                continue
            try:
                d = bitmaps.picture_digest(po)
            except Exception:  # noqa: BLE001 -- swallow-ok: a damaged picture is not a copy
                d = None
            if d is None:
                continue
            g = groups.get(d)
            if g is None:
                primary, flash = bitmaps.read_pictures(po)
                g = groups[d] = {
                    "digest": d,
                    "width": primary.width,
                    "height": primary.height,
                    "flash": flash is not None,
                    "bytes": bitmaps.stored_size(po),
                    "uses": [],
                    "thumb": _thumb(primary),
                }
            l, t, r, b = G.parse_geom_header(po.body_raw).rect1
            g["uses"].append(
                {"page": path, "name": name, "index": i, "rect": [int(l), int(t), int(r), int(b)]}
            )
    dup = [g for g in groups.values() if len(g["uses"]) > 1]
    for g in dup:
        g["pages"] = len({u["page"] for u in g["uses"]})
        g["wasted"] = g["bytes"] * (len(g["uses"]) - 1)
    dup.sort(key=lambda g: (-g["wasted"], g["digest"]))
    return {"format": FORMAT, "project": project_dir, "pages": len(pages), "groups": dup, "failed": failed}


def _picture_of(path: str, index: int, digest: str):
    """(primary, flash) of the picture ``digest`` at record ``index`` of ``path``."""
    from ..format import bitmaps
    from ..format.page import parse_ctg

    objs = parse_ctg(path).page.objects
    if 0 <= index < len(objs) and bitmaps.picture_digest(objs[index]) == digest:
        return bitmaps.read_pictures(objs[index])
    for po in objs:
        if po.type_code == bitmaps.BITMAP_TC and bitmaps.picture_digest(po) == digest:
            return bitmaps.read_pictures(po)
    return None, None


def make_symbol(project_dir: str, library: str, element: str, page: str, index: int, digest: str) -> str:
    """The picture ``digest`` (found at ``page`` record ``index``) stored as the symbol
    ``library.element`` in the project's library ``library``.ctl (created when missing).
    Returns the library's path."""
    from ..editing import EditableDocument

    primary, flash = _picture_of(page, index, digest)
    if primary is None:
        raise ValueError("the picture is no longer on %s" % os.path.basename(page))
    lib_path = os.path.join(project_dir, library + ".ctl")
    doc = EditableDocument.open(page)  # (a scratch copy: never saved)
    w, h = primary.width, primary.height
    i = doc.add_bitmap_object(
        (0, 0, w, h), primary.rgba, w, h, flash_rgba=flash.rgba if flash is not None else None
    )
    doc.copy_to_library([i], lib_path, element)
    return lib_path


def apply(req: dict, progress=None, skip_now=None) -> dict:
    """``req`` = {"mode": 'symbol' | 'replace', "digest", "pages", "skip", "builder_open",
    "backup", and for 'symbol' "project", "library", "element", "make" (store the symbol
    first), "first": {"page", "index"}; for 'replace' "pixels": {"WxH": {"file", "w", "h"}}
    (raw RGBA files the editor wrote: the new picture at each size on the page)}."""
    from . import batchjob
    from .. import editing as editing_mod
    from ..editing import EditableDocument
    from ..format import bitmaps
    from ..format import geometry as G

    mode = req["mode"]
    digest = req["digest"]
    out = batchjob.batch_answer(FORMAT, req.get("backup"), changed=0)
    library, element = req.get("library", ""), req.get("element", "")
    if mode == "symbol" and req.get("make"):
        first = req["first"]
        make_symbol(req["project"], library, element, first["page"], int(first["index"]), digest)
        out["library"] = os.path.join(req["project"], library + ".ctl")
        editing_mod.libraries_changed()
    pixels = {}
    for key, spec in (req.get("pixels") or {}).items():
        with open(spec["file"], "rb") as fh:
            pixels[key] = (fh.read(), int(spec["w"]), int(spec["h"]))

    def edit(path):
        doc = EditableDocument.open(path)
        objs = doc.parsed.page.objects
        hits = [
            i
            for i, po in enumerate(objs)
            if po.type_code == bitmaps.BITMAP_TC
            and bitmaps.dib_section(po) is not None
            and doc.free_picture(i)
            and bitmaps.picture_digest(po) == digest
        ]
        done = 0
        for i in reversed(hits):  # (later indices first: they stay put)
            if mode == "symbol":
                doc.link_picture_to_symbol(i, library, element)
                done += 1
                continue
            l, t, r, b = G.norm_rect(G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1)
            px = pixels.get("%dx%d" % (r - l, b - t)) or pixels.get("native")
            if px is None:
                continue
            rgba, w, h = px
            if doc.replace_bitmap(i, (l, t, r, b), rgba, w, h, flash_rgba=None):
                done += 1
        return doc, done

    for _name, done in batchjob.edit_pages(
        req.get("pages") or (), edit, out, req=req, kind="pictures", progress=progress, skip_now=skip_now
    ):
        out["changed"] += done
    return out


def command(request: str, answer: str) -> List[str]:
    from ..infra import workers

    return workers.command("--pictures", "ctgedit.workers.picture_worker", "main", [request, answer])


def main(argv=None) -> int:
    """``ctgedit --pictures <request.json> <answer.json>``: a scan or an apply."""
    from .batchjob import helper_main

    def dispatch(req, progress, skip_now):
        if req.get("mode") == "scan":
            return scan(req["project"], progress)
        return apply(req, progress, skip_now)

    return helper_main(argv, "--pictures", FORMAT, dispatch)


def page_digests(path: str) -> Optional[set]:
    """The digests of a page's own bitmaps (tests)."""
    from ..format import bitmaps
    from ..format.page import parse_ctg

    return {
        bitmaps.picture_digest(po)
        for po in parse_ctg(path).page.objects
        if po.type_code == bitmaps.BITMAP_TC and bitmaps.dib_section(po) is not None
    }
