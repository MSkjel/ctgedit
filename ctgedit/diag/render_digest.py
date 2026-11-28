"""A digest of what a page draws, and the ``--display-digest`` command."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import zlib
from typing import Dict, List, Optional

DIGEST_VERSION = 1

#: the raw flags the scene reads to leave an object (or part of it) out
FLAGS = ("_dup_suppressed", "_state_hidden", "_non_drawable", "_in_composite", "_no_outline")


def _r(v, nd: int = 2):
    """A number rounded (a float that is an int prints as the int)."""
    if isinstance(v, float):
        v = round(v, nd)
        return int(v) if v == int(v) else v
    return v


def art_key(bmp) -> Optional[tuple]:
    """(width, height, crc32 of the pixels) of a raw['_embedded_bmp'] (a
    tuple (w, h, rgba, alpha) or an EmbeddedBitmap), None without one."""
    if not bmp:
        return None
    try:
        if isinstance(bmp, (tuple, list)):
            w, h, rgba = bmp[0], bmp[1], bmp[2]
        else:
            w, h, rgba = bmp.width, bmp.height, bmp.rgba
        return (int(w), int(h), zlib.crc32(rgba) & 0xFFFFFFFF if rgba else 0)
    except Exception:  # noqa: BLE001 -- swallow-ok: an odd value still counts as art
        return (0, 0, 0)


def _xform(v):
    if isinstance(v, (tuple, list)):
        return tuple(_r(x, 3) if isinstance(x, float) else x for x in v)
    return None


def object_row(o, depth: int) -> tuple:
    g, st, raw = o.geometry, o.style, o.raw
    just = getattr(st, "justify", None)
    return (
        depth,
        getattr(o.kind, "value", str(o.kind)),
        _r(g.x),
        _r(g.y),
        _r(g.extent_x),
        _r(g.extent_y),
        len(g.nodes or ()),
        st.fill_colour,
        st.line_colour,
        bool(st.filled),
        st.line_style,
        st.line_width,
        st.gradient_mode,
        st.corner_radius,
        st.text,
        st.font,
        st.font_size,
        bool(st.bold),
        getattr(just, "value", just),
        art_key(raw.get("_embedded_bmp")),
        raw.get("_resolved_ref"),
        tuple(bool(raw.get(f)) for f in FLAGS),
        raw.get("_rest_visible"),
        _xform(raw.get("_symbol_xform")),
        bool(raw.get("_alarm_indicator")),
        len(o.children or ()),
    )


# row fields the summary reads
_ART, _REF, _NCHILD = 19, 20, 25


def object_rows(o, depth: int = 0, out: Optional[list] = None) -> list:
    """``o``'s row and its resolved children's, depth first."""
    if out is None:
        out = []
    out.append(object_row(o, depth))
    for c in o.children or ():
        object_rows(c, depth + 1, out)
    return out


def object_digest(o) -> str:
    return hashlib.sha1(repr(object_rows(o)).encode("utf-8", "replace")).hexdigest()[:16]


def summary(o) -> dict:
    """What a difference is described with (JSON-compatible)."""
    rows = object_rows(o)
    st = o.style
    flags = [f.strip("_").replace("_", " ") for f in FLAGS if o.raw.get(f)]
    return {
        "kind": getattr(o.kind, "value", str(o.kind)),
        "box": [_r(o.geometry.x), _r(o.geometry.y), _r(o.geometry.extent_x), _r(o.geometry.extent_y)],
        "nodes": len(o.geometry.nodes or ()),
        "fill": st.fill_colour,
        "line": st.line_colour,
        "filled": bool(st.filled),
        "text": (st.text or "")[:60],
        "picture": list(art_key(o.raw.get("_embedded_bmp")) or ()) or None,
        "art": sum(1 for r in rows if r[_ART] is not None),
        "children": len(rows) - 1,
        "ref": o.raw.get("_resolved_ref"),
        "flags": flags,
    }


def page_digest(page, detail: bool = False) -> dict:
    """The digest of a derived page."""
    objs = list(page.objects or ())
    per = [object_digest(o) for o in objs]
    out = {
        "version": DIGEST_VERSION,
        "count": len(objs),
        "total": hashlib.sha1("".join(per).encode("ascii")).hexdigest()[:16],
        "objects": per,
    }
    if detail:
        out["detail"] = [summary(o) for o in objs]
    return out


def _colour(v) -> str:
    return "none" if v is None else "#%06x" % (v & 0xFFFFFF) if (v >> 24) == 0 else "0x%08x" % v


def describe(here: dict, fresh: dict) -> str:
    """What differs between this session's summary of an object and a fresh
    read's, in words ('lost art ...')."""
    parts: List[str] = []
    if here.get("art", 0) < fresh.get("art", 0) or here.get("children", 0) < fresh.get("children", 0):
        parts.append(
            "lost art (%d picture(s) / %d resolved child object(s) here, %d / %d in "
            "a fresh read)"
            % (here.get("art", 0), here.get("children", 0), fresh.get("art", 0), fresh.get("children", 0))
        )
    elif here.get("art", 0) > fresh.get("art", 0) or here.get("children", 0) > fresh.get("children", 0):
        parts.append(
            "more art here than in a fresh read (%d / %d vs %d / %d)"
            % (here.get("art", 0), here.get("children", 0), fresh.get("art", 0), fresh.get("children", 0))
        )
    if here.get("ref") != fresh.get("ref"):
        parts.append(
            "resolved to %s here, %s in a fresh read"
            % (here.get("ref") or "nothing", fresh.get("ref") or "nothing")
        )
    if here.get("kind") != fresh.get("kind"):
        parts.append("a %s here, a %s in a fresh read" % (here.get("kind"), fresh.get("kind")))
    for key, label in (
        ("box", "box"),
        ("nodes", "vertices"),
        ("text", "text"),
        ("flags", "flags"),
        ("picture", "picture"),
    ):
        if here.get(key) != fresh.get(key):
            parts.append("%s %s here, %s in a fresh read" % (label, here.get(key), fresh.get(key)))
    for key, label in (("fill", "fill"), ("line", "line colour")):
        if here.get(key) != fresh.get(key):
            parts.append(
                "%s %s here, %s in a fresh read" % (label, _colour(here.get(key)), _colour(fresh.get(key)))
            )
    if here.get("filled") != fresh.get("filled"):
        parts.append("filled here" if here.get("filled") else "not filled here")
    return "; ".join(parts) or "its resolved children differ"


def compare_page(page, fresh: dict, limit: int = 10) -> Dict[str, object]:
    """Compare the derived ``page`` of this session with a fresh read's digest
    (``fresh`` = a :func:`page_digest` with detail). Returns {"identical",
    "count" (this session), "fresh_count", "differing" (how many objects),
    "first": [{"index", "kind", "what"}]}."""
    objs = list(page.objects or ())
    fobj = list(fresh.get("objects") or ())
    fdet = list(fresh.get("detail") or ())
    out: Dict[str, object] = {"count": len(objs), "fresh_count": len(fobj)}
    first: List[dict] = []
    differing = 0
    if len(objs) != len(fobj):
        first.append(
            {
                "index": None,
                "kind": "page",
                "what": "%d objects here, %d in a fresh read" % (len(objs), len(fobj)),
            }
        )
    for i, o in enumerate(objs[: len(fobj)]):
        if object_digest(o) == fobj[i]:
            continue
        differing += 1
        if len(first) < limit:
            here = json.loads(json.dumps(summary(o)))
            there = fdet[i] if i < len(fdet) else {}
            first.append(
                {"index": i, "kind": here.get("kind"), "what": describe(here, there) if there else "differs"}
            )
    out["differing"] = differing
    out["first"] = first
    out["identical"] = not first
    return out


# ---------------------------------------------------------------------------
# The fresh read (a new interpreter, or -- the fallback -- this one)


def fresh_digest(
    data_path: str,
    as_path: Optional[str] = None,
    label_mode: Optional[str] = None,
    detail: bool = True,
    where: str = "process",
) -> dict:
    """Read ``data_path`` (a .ctg's bytes) as the page at ``as_path`` (default:
    itself), derive it the way the editor's open does, and digest it. In this
    process the reader's label mode is set for the read and put back after."""
    from ..editing import EditableDocument
    from ..infra import logsetup
    from ..io import reader

    path = as_path or data_path
    stamp = None
    if as_path is None:
        try:
            st = os.stat(data_path)
            stamp = [st.st_mtime_ns, st.st_size]
        except OSError:
            stamp = None
    with open(data_path, "rb") as fh:
        data = fh.read()
    prev = reader.get_label_mode()
    with reader.derive_options(label_mode=label_mode or None):
        t0 = time.perf_counter()
        with logsetup.error_scope() as swallowed:
            doc = EditableDocument.from_bytes(data, path)
        derive_s = time.perf_counter() - t0
        out = page_digest(doc.page, detail)
    out.update(
        {
            "page": path,
            "sha1": hashlib.sha1(data).hexdigest(),
            "stamp": stamp,
            "derive_s": round(derive_s, 3),
            "label_mode": label_mode or prev,
            "swallowed": [[w, t, n] for (w, t), n in sorted(swallowed.items())],
            "where": where,
            "pid": os.getpid(),
        }
    )
    return out


def display_digest_main(argv=None) -> int:
    """``ctgedit --display-digest <page.ctg> <out.json> [--detail]
    [--label-mode M] [--as <page>]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--display-digest"]:
        args = args[1:]
    detail = "--detail" in args
    args = [a for a in args if a != "--detail"]
    opts: Dict[str, str] = {}
    for flag in ("--label-mode", "--as"):
        if flag in args:
            k = args.index(flag)
            if k + 1 >= len(args):
                return 2
            opts[flag] = args[k + 1]
            del args[k : k + 2]
    if len(args) != 2:
        return 2
    page, out = args
    # the check itself must start nothing else (the .ctF trust check reads the .ctF)
    os.environ["CTG_NO_CTF_PRECHECK"] = "1"
    res: dict
    try:
        res = fresh_digest(page, as_path=opts.get("--as"), label_mode=opts.get("--label-mode"), detail=detail)
    except Exception as e:  # noqa: BLE001 -- swallow-ok: reported to the editor with its traceback
        import traceback

        res = {
            "version": DIGEST_VERSION,
            "page": opts.get("--as") or page,
            "error": "%s: %s" % (type(e).__name__, e),
            "traceback": traceback.format_exc()[-4000:],
            "pid": os.getpid(),
        }
    try:
        from ..format.fileio import write_atomic

        write_atomic(out, json.dumps(res).encode("utf-8"))
    except OSError:
        return 1
    return 0 if "error" not in res else 1
