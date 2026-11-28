r"""Check that placed genies still draw after recomposing, across the corpus.

python tools/genie_recompose_sweep.py [--pages p1.ctg,Project\p2.ctg,...]
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import enum
import gc
import glob
import hashlib
import json
import os
import random
import sqlite3
import sys
import tempfile
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests_corpus"))
import corpus  # noqa: E402  (the local roles: tests_corpus/local.json)

R2 = corpus.R2_USER
#: pages always swept (when present) besides the per-project sample: the shipped pages and the
#: local pages of the role 'sweep_pages'
EXTRAS = [
    p
    for p in [
        os.path.join(R2, "Example", "Mixer.ctg"),
        os.path.join(R2, "Example", "Genies.ctg"),
        os.path.join(R2, "Example", "Groups.ctg"),
        os.path.join(R2, "Example", "!PID.ctg"),
        os.path.join(R2, "ExampleSA", "Mixing_L3.ctg"),
        os.path.join(R2, "ExampleSA", "Overview_L2.ctg"),
    ]
    + [corpus.install_path(rel) for rel in corpus.role_value("sweep_pages", [])]
    if p
]
EXTRAS = [
    p for k, p in enumerate(EXTRAS) if os.path.normcase(p) not in {os.path.normcase(x) for x in EXTRAS[:k]}
]


# ------------------------------------------------------------------------------ the model digest
def has_art(o) -> bool:
    """A member that draws symbol ART: an embedded / grafted picture or resolved children."""
    return o.raw.get("_embedded_bmp") is not None or bool(o.children)


def canon(v, depth: int = 0):
    """A deterministic, comparable form of a model value: dataclasses by field, enums by value,
    dicts sorted, floats rounded, byte strings as (length, crc32); anything else by type name
    (never a repr with a memory address)."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, enum.Enum):
        return v.value
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        return ("#bytes", len(b), zlib.crc32(b))
    if depth > 12:
        return "#deep"
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return tuple(
            (f.name, canon(getattr(v, f.name), depth + 1))
            for f in dataclasses.fields(v)
            if f.name != "children"
        )
    if isinstance(v, dict):
        return tuple(sorted(((str(k), canon(x, depth + 1)) for k, x in v.items()), key=lambda t: t[0]))
    if isinstance(v, (list, tuple)):
        return tuple(canon(x, depth + 1) for x in v)
    if isinstance(v, (set, frozenset)):
        return tuple(sorted((canon(x, depth + 1) for x in v), key=repr))
    return "#" + type(v).__name__


#: the fields of :func:`obj_digest` (what an object DRAWS), for naming what changed
DRAW_FIELDS = (
    "kind",
    "x",
    "y",
    "extent_x",
    "extent_y",
    "nodes",
    "fill",
    "line",
    "filled",
    "line_width",
    "line_style",
    "gradient",
    "corner_radius",
    "three_d",
    "text",
    "font",
    "font_size",
    "text_colour",
    "bold",
    "justify",
    "bitmap",
    "resolved_ref",
    "symbol_xform",
    "local_xform",
    "dup_suppressed",
    "state_hidden",
    "non_drawable",
    "no_outline",
    "children",
)


def obj_digest(o, depth: int = 0) -> tuple:
    """What an object DRAWS, as a comparable tuple (:data:`DRAW_FIELDS`): kind, geometry,
    colours / line, text and font, its picture, its resolved reference and placement, the render
    flags -- and the same of its resolved children (recursively)."""
    g, st = o.geometry, o.style
    raw = o.raw
    kids = tuple(obj_digest(c, depth + 1) for c in (o.children or ())) if depth < 12 else ()
    return (
        o.kind.value,
        canon(g.x),
        canon(g.y),
        canon(g.extent_x),
        canon(g.extent_y),
        canon(g.nodes or ()),
        st.fill_colour,
        st.line_colour,
        st.filled,
        st.line_width,
        st.line_style,
        (st.gradient_mode, st.gradient_colour),
        st.corner_radius,
        st.three_d,
        st.text,
        st.font,
        st.font_size,
        st.text_colour,
        st.bold,
        canon(st.justify),
        canon(raw.get("_embedded_bmp")),
        raw.get("_resolved_ref"),
        canon(raw.get("_symbol_xform")),
        canon(raw.get("_local_xform")),
        bool(raw.get("_dup_suppressed")),
        bool(raw.get("_state_hidden")),
        bool(raw.get("_non_drawable")),
        bool(raw.get("_no_outline")),
        kids,
    )


def full_digest(o) -> tuple:
    """EVERYTHING the derived object holds (every dataclass field and every raw key -- the
    property bag, the graft flags ...) and its resolved children, canonicalised."""
    return (canon(o), tuple(full_digest(c) for c in (o.children or ())))


def page_digest(page) -> dict:
    """{'model': sha1 of every object's :func:`full_digest`, 'drawn': sha1 of every
    :func:`obj_digest`, 'roots': {root index: short sha1 of its unit}, 'objects': n, 'art':
    art-bearing objects} of a derived page -- how the soak compares two derivations."""
    hm, hd = hashlib.sha1(), hashlib.sha1()
    roots = {}
    cur_root, cur = None, None
    art = 0
    for i, o in enumerate(page.objects):
        f = repr(full_digest(o)).encode("utf-8", "replace")
        hm.update(f)
        hd.update(repr(obj_digest(o)).encode("utf-8", "replace"))
        art += has_art(o)
        if o.parent is None:
            if cur is not None:
                roots[str(cur_root)] = cur.hexdigest()[:10]
            cur_root, cur = i, hashlib.sha1()
        if cur is not None:
            cur.update(f)
    if cur is not None:
        roots[str(cur_root)] = cur.hexdigest()[:10]
    return {
        "model": hm.hexdigest()[:16],
        "drawn": hd.hexdigest()[:16],
        "roots": roots,
        "objects": len(page.objects),
        "art": art,
    }


def art_label(page, i: int) -> str:
    """A short name for an art-bearing member (for 'what was lost'): its own reference, else the
    nearest linked ancestor's element + what it draws."""
    o = page.objects[i]
    ref = o.raw.get("_resolved_ref") or o.raw.get("_ref") or o.genie_template
    if ref:
        return str(ref)
    where = ""
    for a in page.ancestors(i):
        r = page.objects[a].raw
        ar = r.get("_resolved_ref") or r.get("_ref")
        if ar:
            where = str(ar).split(".<")[0] + " > "
            break
    if o.raw.get("_embedded_bmp") is not None:
        b = o.raw["_embedded_bmp"]
        try:
            return "%s%s bitmap %dx%d" % (where, o.kind.value, b[0], b[1])
        except Exception:  # noqa: BLE001
            return "%s%s bitmap" % (where, o.kind.value)
    return "%s%s (%d children)" % (where, o.kind.value, len(o.children or ()))


def subtree_stats(page, root: int, digests: bool = True) -> dict:
    """Record count, art-bearing members, resolved refs, the art labels (Counter) and -- with
    ``digests`` -- the per-member drawn and full digests of ``root``'s subtree in a derived
    page."""
    sub = page.subtree(root)
    objs = page.objects
    art = [i for i in sub if has_art(objs[i])]
    return {
        "records": len(sub),
        "art": len(art),
        "refs": sum(1 for i in sub if objs[i].raw.get("_resolved_ref")),
        "art_labels": collections.Counter(art_label(page, i) for i in art),
        "art_flags": [has_art(objs[i]) for i in sub],
        "kinds": [objs[i].kind.value for i in sub],
        "drawable": [objs[i].kind.value != "unknown" and not objs[i].raw.get("_non_drawable") for i in sub],
        "digests": [obj_digest(objs[i]) for i in sub] if digests else None,
        "full": [full_digest(objs[i]) for i in sub] if digests else None,
    }


def _what_differs(a: tuple, b: tuple) -> list:
    return [n for n, x, y in zip(DRAW_FIELDS, a, b) if x != y]


def compare(before: dict, after: dict) -> dict:
    """The difference of two :func:`subtree_stats`: counts, art lost (members that had art and
    lost it, when the subtrees align; else the art-label multiset difference) and, for aligned
    subtrees with digests, the members whose drawn / full digest changed (with a few samples
    naming the drawn fields that differ)."""
    out = {
        "records": [before["records"], after["records"]],
        "art": [before["art"], after["art"]],
        "refs": [before["refs"], after["refs"]],
    }
    lost = before["art_labels"] - after["art_labels"]
    gained = after["art_labels"] - before["art_labels"]
    out["art_lost_labels"] = dict(lost.most_common(12))
    out["art_gained_labels"] = dict(gained.most_common(6))
    aligned = before["records"] == after["records"] and before["kinds"] == after["kinds"]
    out["aligned"] = aligned
    if aligned:
        out["art_lost_members"] = sum(
            1 for a, b in zip(before["art_flags"], after["art_flags"]) if a and not b
        )
        if before["digests"] is not None and after["digests"] is not None:
            diff = [k for k, (a, b) in enumerate(zip(before["digests"], after["digests"])) if a != b]
            drawn = [k for k in diff if before["drawable"][k] or after["drawable"][k]]
            # members that DRAW and changed; the non-drawable ones (DummyMarkers, animation /
            # input records) apart
            out["changed_members"] = len(drawn)
            out["changed_nondrawable"] = len(diff) - len(drawn)
            out["changed_members_any"] = sum(1 for a, b in zip(before["full"], after["full"]) if a != b)
            out["changed_samples"] = [
                {
                    "offset": k,
                    "kind": before["kinds"][k],
                    "drawable": before["drawable"][k] or after["drawable"][k],
                    "fields": _what_differs(before["digests"][k], after["digests"][k]),
                }
                for k in (drawn[:5] + [k for k in diff if k not in drawn][:3])
            ]
    else:
        out["art_lost_members"] = max(0, sum(lost.values()))
    return out


# ------------------------------------------------------------------------------ one page (worker)
_WORKER_READY = []


def init_worker(small_caches: bool = False) -> None:
    """Per process: private data / settings folders (nothing of the user's is read or written),
    no .ctF pre-check process, no runtime, CtDraw's literal labels (the app's default)."""
    if _WORKER_READY:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    os.environ["CTG_NO_CTF_PRECHECK"] = "1"
    os.environ["CTG_NO_RUNTIME"] = "1"
    tmp = tempfile.mkdtemp(prefix="ctgsweep_")
    os.environ["CTG_DATA_DIR"] = os.path.join(tmp, "data")
    os.environ["CTG_SETTINGS_INI"] = os.path.join(tmp, "settings.ini")
    if small_caches:
        apply_small_caches()
    from ctgedit.io import reader

    reader.set_label_mode("literal")
    _WORKER_READY.append(tmp)


def apply_small_caches() -> None:
    """Tiny process caches, so eviction happens all the time (the memo paths get exercised)."""
    os.environ["GP_MEMO_RECORDS"] = "3000"
    from ctgedit.format import symbols
    from ctgedit.io import reader

    symbols._BMP_MEMO_LIMIT = 1 << 20
    reader._FRESH_MEMO_BYTES = 2 << 20


def _changed_value(v: str) -> str:
    s = (v or "").strip()
    try:
        return str(int(s) + 1)
    except ValueError:
        return (v or "") + "X"


def pick_parameter(doc, root: int, overrides: dict):
    """(name, old value, new value) of the parameter the sweep changes, or None: an override
    (--change) when the genie declares it, else the first BOUND parameter with a value, else the
    first declared one with a value."""
    params = doc.genie_all_params(root)
    if not params:
        return None
    low = {k.lower(): k for k in params}
    for k, v in overrides.items():
        if k.lower() in low:
            name = low[k.lower()]
            return name, params[name], v
    own = doc._instance_bindings(root) or {}
    own_low = {k.lower() for k, v in own.items() if v}
    for name, v in params.items():
        if name.lower() in own_low and v:
            return name, v, _changed_value(v)
    for name, v in params.items():
        if v:
            return name, v, _changed_value(v)
    name = next(iter(params))
    return name, params[name], "X"


def _full_model(doc):
    """What a reload of the page as it would be saved now draws (an in-memory full derive)."""
    from ctgedit import editing
    from ctgedit.format.page import parse_ctg_bytes
    from ctgedit.io.reader import derive_page

    page, _der = derive_page(
        parse_ctg_bytes(doc.serialize()),
        doc.path,
        records_fresh=True,
        generation=editing.LIBRARY_GENERATION[0],
    )
    return page


def _step(doc, root, before, label, fn, fresh: bool, original: bytes) -> dict:
    """Run one re-composition ``fn`` (returns None / False / True like _recompose_genie), measure
    the re-derived subtree against ``before``, check a full derive of the result, then Undo and
    check the page is back to its ``original`` bytes and model."""
    res = {"step": label}
    n_undo = len(doc._undo)
    recs = doc.parsed.page.objects
    sub = doc.page.subtree(root)
    old_payloads = [_payload(recs[i]) for i in sub]
    t = time.perf_counter()
    try:
        done = fn()
    except Exception as e:  # noqa: BLE001 -- a crash IS a finding
        res.update(outcome="error", error="%s: %s" % (type(e).__name__, e))
        _undo_to(doc, n_undo)
        return res
    res["ms"] = round((time.perf_counter() - t) * 1000)
    res["outcome"] = {None: "not-applicable", False: "byte-identical", True: "changed"}.get(done, str(done))
    if done:
        after = subtree_stats(doc.page, root)
        res.update(compare(before, after))
        recs = doc.parsed.page.objects
        new_payloads = [_payload(recs[i]) for i in doc.page.subtree(root)]
        if len(new_payloads) == len(old_payloads):
            res["changed_records"] = sum(1 for a, b in zip(old_payloads, new_payloads) if a != b)
        if fresh:
            t2 = time.perf_counter()
            try:
                full = _full_model(doc)
                fs = subtree_stats(full, root)
                res["fresh"] = {"records": fs["records"], "art": fs["art"], "refs": fs["refs"]}
                # the in-session (incremental) derive against a reload of the result
                res["fresh_matches_session"] = fs["full"] == after["full"]
                if not res["fresh_matches_session"] and fs["records"] == after["records"]:
                    res["fresh_changed_members"] = sum(1 for a, b in zip(fs["full"], after["full"]) if a != b)
                    res["fresh_changed_drawn"] = sum(
                        1 for a, b in zip(fs["digests"], after["digests"]) if a != b
                    )
                del full
            except Exception as e:  # noqa: BLE001
                res["fresh_error"] = "%s: %s" % (type(e).__name__, e)
            res["fresh_ms"] = round((time.perf_counter() - t2) * 1000)
        # Undo back to the page as opened
        _undo_to(doc, n_undo)
        back = subtree_stats(doc.page, root)
        res["undo_restores_model"] = back["full"] == before["full"]
        res["undo_restores_bytes"] = doc.serialize() == original
    return res


def _payload(po) -> bytes:
    from ctgedit.format import writer

    return bytes(writer.object_payload(po))


def _undo_to(doc, n: int) -> None:
    while len(doc._undo) > n:
        doc.undo()


def sweep_page(path: str, opts: dict, skip_elements=()) -> dict:
    """Every chosen top-level classic genie instance of ``path``: identity + parameter change."""
    init_worker(opts.get("small_caches", False))
    from ctgedit.editing import EditableDocument

    out = {"page": path, "genies": [], "t_open_ms": None}
    deadline = opts.get("deadline")
    t0 = time.perf_counter()
    try:
        doc = EditableDocument.open(path)
    except Exception as e:  # noqa: BLE001
        out["error"] = "open: %s: %s" % (type(e).__name__, e)
        return out
    out["t_open_ms"] = round((time.perf_counter() - t0) * 1000)
    out["objects"] = len(doc.page.objects)
    try:
        insts = doc.genie_instances()
    except Exception as e:  # noqa: BLE001
        out["error"] = "genie_instances: %s: %s" % (type(e).__name__, e)
        return out
    out["instances"] = len(insts)
    skip = {tuple(x) for x in skip_elements}
    # one instance per element (the first on the page), elements the sweep has tested often
    # enough left out, at most max_genies; the biggest instances first (they hold the most
    # members to lose), the page order among equals
    first = collections.OrderedDict()
    for i, (lib, el) in insts:
        key = (lib.lower(), el.lower())
        if key not in first and key not in skip:
            first[key] = i
    sizes = {i: len(doc.page.subtree(i)) for i in first.values()}
    chosen = sorted(((i, k) for k, i in first.items()), key=lambda t: (-sizes[t[0]], t[0]))
    chosen = chosen[: opts.get("max_genies", 6)]
    out["elements_on_page"] = len({(lib.lower(), el.lower()) for _i, (lib, el) in insts})
    original = doc.serialize()
    overrides = opts.get("change") or {}
    fresh = opts.get("fresh", True)
    for root, key in chosen:
        if deadline and time.time() > deadline:
            out["cut_by_budget"] = True
            break
        g = {"root": root, "element": "%s.%s" % key}
        try:
            before = subtree_stats(doc.page, root)
            g.update(records=before["records"], art=before["art"], refs=before["refs"])
            own = dict(doc._instance_bindings(root) or {})
            g["identity"] = _step(
                doc,
                root,
                before,
                "identity",
                lambda: doc._recompose_genie(root, {}, "identity"),
                fresh,
                original,
            )
            pick = pick_parameter(doc, root, overrides)
            if pick is None:
                g["param"] = {"step": "param", "outcome": "no-parameters"}
            else:
                name, old, new = pick
                g["param"] = _step(
                    doc,
                    root,
                    before,
                    "param",
                    lambda: doc.set_genie_bindings(root, {name: new}),
                    fresh,
                    original,
                )
                g["param"].update(name=name, old=old, new=new, bound=name in own)
        except Exception as e:  # noqa: BLE001
            g["error"] = "%s: %s" % (type(e).__name__, e)
        out["genies"].append(g)
        if doc.serialize() != original:
            # a step left the page changed: start the next genie from the file again
            out.setdefault("reopened", 0)
            out["reopened"] += 1
            doc = EditableDocument.open(path)
            original = doc.serialize()
    doc = None
    gc.collect()
    return out


# ------------------------------------------------------------------------------ page selection
def find_index() -> str:
    """The corpus index (tools/corpus_index.py) of this checkout or of the repository a
    worktree lives in; '' when there is none."""
    env = os.environ.get("CTG_CORPUS_DB")
    if env and os.path.isfile(env):
        return env
    d = HERE
    for _ in range(8):
        cand = (
            os.path.join(d, "tools", "_corpus", "corpus.sqlite")
            if os.path.basename(d) != "tools"
            else os.path.join(d, "_corpus", "corpus.sqlite")
        )
        if os.path.isfile(cand):
            return cand
        d = os.path.dirname(d)
    return ""


def genie_pages(root: str, projects, per_project: int, seed: int) -> list:
    """Per project, ``per_project`` pages that place a top-level genie (dwFlags 0x201 group
    root): from the corpus index when there is one, else any pages of the folder."""
    rng = random.Random(seed)
    by_proj = collections.defaultdict(list)
    db = find_index()
    if db:
        con = sqlite3.connect(db)
        for path, proj, n in con.execute(
            "SELECT f.path, f.project, COUNT(*) FROM records r JOIN files f ON f.id=r.file_id "
            "WHERE f.ext='.ctg' AND r.depth=0 AND r.tc=8 AND (r.dwflags & 513)=513 "
            "AND r.child_count>0 GROUP BY f.id"
        ):
            if os.path.isfile(path):
                by_proj[proj].append(path)
        con.close()
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        proj = os.path.basename(d)
        if os.path.isdir(d) and proj not in by_proj:
            by_proj[proj] = sorted(glob.glob(os.path.join(d, "*.ctg")))
    out = []
    for proj in sorted(by_proj):
        if projects and proj.lower() not in projects:
            continue
        pages = sorted(by_proj[proj])
        rng.shuffle(pages)
        out.extend(pages[:per_project])
    return out


# ------------------------------------------------------------------------------ the run
def _task(args):
    path, opts, skip = args
    try:
        return sweep_page(path, opts, skip)
    except Exception as e:  # noqa: BLE001
        return {"page": path, "genies": [], "error": "%s: %s" % (type(e).__name__, e)}


def summarise(pages: list) -> dict:
    """Totals and the worst genies of a sweep's page results."""
    s = collections.Counter()
    worst_id, worst_p, errors, fresh_div, undo_bad = [], [], [], [], []
    drawn_id, art_changed = [], []
    fields = collections.Counter()
    for p in pages:
        s["pages"] += 1
        if p.get("error"):
            errors.append((p["page"], p["error"]))
        for g in p.get("genies", ()):
            s["genies"] += 1
            if g.get("error"):
                errors.append((p["page"], g["element"], g["error"]))
            for kind in ("identity", "param"):
                st = g.get(kind) or {}
                oc = st.get("outcome")
                s["%s_%s" % (kind, oc)] += 1
                if oc == "error":
                    errors.append((p["page"], g["element"], kind, st.get("error")))
                if oc != "changed":
                    continue
                art = st.get("art", [0, 0])
                page = os.path.relpath(p["page"], R2) if p["page"].startswith(R2) else p["page"]
                row = {
                    "page": page,
                    "element": g["element"],
                    "root": g["root"],
                    "records": st["records"],
                    "art": st["art"],
                    "art_lost_members": st.get("art_lost_members"),
                    "lost": st.get("art_lost_labels"),
                    "gained": st.get("art_gained_labels"),
                }
                if kind == "param":
                    row["param"] = "%s: %r -> %r" % (st.get("name"), st.get("old"), st.get("new"))
                # LOST art = fewer art-bearing members, or (the subtrees aligned) a member that
                # had art and has none; a re-shaped subtree whose art count held or grew but
                # whose art labels changed is listed apart ('art changed')
                if art[1] < art[0] or (st.get("aligned") and st.get("art_lost_members")):
                    s["%s_lost_art" % kind] += 1
                    (worst_id if kind == "identity" else worst_p).append(row)
                elif st.get("art_lost_labels") or st.get("art_gained_labels"):
                    s["%s_art_changed" % kind] += 1
                    art_changed.append(dict(row, step=kind))
                if kind == "identity" and st.get("changed_members"):
                    drawn_id.append(
                        {
                            "page": page,
                            "element": g["element"],
                            "records": st["records"][0],
                            "changed": st["changed_members"],
                            "samples": st.get("changed_samples", [])[:3],
                        }
                    )
                    for smp in st.get("changed_samples", ()):
                        if smp.get("drawable"):
                            fields.update(smp.get("fields") or ())
                if st["records"][0] != st["records"][1]:
                    s["%s_record_count_changed" % kind] += 1
                if kind == "identity" and st.get("changed_members"):
                    s["identity_changed_drawn_genies"] += 1
                    s["identity_changed_drawn_members"] += st["changed_members"]
                if kind == "identity" and st.get("changed_nondrawable"):
                    s["identity_changed_nondrawable_genies"] += 1
                if kind == "identity" and st.get("changed_members_any"):
                    s["identity_changed_model_genies"] += 1
                if kind == "identity" and st.get("aligned") is False:
                    s["identity_misaligned_genies"] += 1
                if "fresh" in st:
                    if st["fresh"]["art"] < st["art"][0]:
                        s["%s_fresh_lost_art" % kind] += 1
                    if not st.get("fresh_matches_session"):
                        fresh_div.append((p["page"], g["element"], kind, st.get("fresh_changed_members")))
                if st.get("undo_restores_bytes") is False or st.get("undo_restores_model") is False:
                    undo_bad.append(
                        (
                            p["page"],
                            g["element"],
                            kind,
                            st.get("undo_restores_bytes"),
                            st.get("undo_restores_model"),
                        )
                    )

    def key(r):
        return -(r["art"][0] - r["art"][1]), -(r.get("art_lost_members") or 0)

    return {
        "totals": dict(s),
        "worst_identity": sorted(worst_id, key=key)[:15],
        "worst_param": sorted(worst_p, key=key)[:15],
        "art_changed": art_changed[:15],
        "identity_drawn_changes": sorted(drawn_id, key=lambda r: -r["changed"])[:20],
        "identity_drawn_fields": dict(fields.most_common()),
        "session_vs_fresh_differs": fresh_div[:20],
        "session_vs_fresh_differs_count": len(fresh_div),
        "undo_mismatch": undo_bad[:20],
        "undo_mismatch_count": len(undo_bad),
        "errors": errors[:30],
        "error_count": len(errors),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        epilog="The extras include the local pages of the role 'sweep_pages' "
        "in tests_corpus/local.json (see local.example.json); --no-extras drops them.",
    )
    ap.add_argument(
        "--pages",
        default="",
        help="comma-separated pages (paths, or "
        "Project\\page[.ctg] under the User folder); replaces the sample",
    )
    ap.add_argument("--projects", default="", help="comma-separated project names to sample")
    ap.add_argument("--per-project", type=int, default=4)
    ap.add_argument("--max-genies", type=int, default=6, help="instances tested per page")
    ap.add_argument(
        "--per-element", type=int, default=3, help="instances of one element across the sweep (0 = no limit)"
    )
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--budget-min", type=float, default=40.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--root", default=R2)
    ap.add_argument("--no-extras", action="store_true")
    ap.add_argument("--no-fresh", action="store_true", help="skip the full derive of the serialised result")
    ap.add_argument("--small-caches", action="store_true")
    ap.add_argument(
        "--change",
        action="append",
        default=[],
        help="NAME=VALUE: change this parameter where a genie declares it",
    )
    ap.add_argument(
        "--out",
        default=os.path.join(HERE, "_sweeps", "genie_recompose_%s.json" % time.strftime("%Y%m%d_%H%M%S")),
    )
    a = ap.parse_args(argv)
    if a.pages:
        pages = []
        for p in a.pages.split(","):
            p = p.strip()
            if not p:
                continue
            if not os.path.isabs(p):
                p = os.path.join(a.root, p)
            if not p.lower().endswith(".ctg"):
                p += ".ctg"
            pages.append(p)
    else:
        projects = {x.strip().lower() for x in a.projects.split(",") if x.strip()}
        pages = [] if a.no_extras else [p for p in EXTRAS if os.path.isfile(p)]
        for p in genie_pages(a.root, projects, a.per_project, a.seed):
            if os.path.normcase(p) not in {os.path.normcase(x) for x in pages}:
                pages.append(p)
    start = time.time()
    deadline = start + a.budget_min * 60
    change = dict(x.split("=", 1) for x in a.change if "=" in x)
    opts = {
        "max_genies": a.max_genies,
        "fresh": not a.no_fresh,
        "deadline": deadline,
        "small_caches": a.small_caches,
        "change": change,
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    results = []
    seen = collections.Counter()
    print(
        "%d pages, %d workers, budget %.0f min -> %s" % (len(pages), a.workers, a.budget_min, a.out),
        flush=True,
    )

    def skip_list():
        return [k for k, n in seen.items() if a.per_element and n >= a.per_element]

    def record(res):
        results.append(res)
        for g in res.get("genies", ()):
            lib, _, el = g["element"].partition(".")
            seen[(lib, el)] += 1
        bad = [
            g
            for g in res.get("genies", ())
            for k in ("identity", "param")
            if (g.get(k) or {}).get("outcome") == "changed"
            and (g[k]["art"][0] > g[k]["art"][1] or (g[k].get("aligned") and g[k].get("art_lost_members")))
        ]
        print(
            "[%d/%d] %.0fs %s: %s genies%s%s"
            % (
                len(results),
                len(pages),
                time.time() - start,
                os.path.relpath(res["page"], a.root) if res["page"].startswith(a.root) else res["page"],
                len(res.get("genies", ())),
                "  LOST ART x%d" % len(bad) if bad else "",
                "  ERROR " + res["error"] if res.get("error") else "",
            ),
            flush=True,
        )
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "args": vars(a),
                    "elapsed_s": round(time.time() - start),
                    "pages": results,
                    "summary": summarise(results),
                },
                fh,
                indent=1,
                default=str,
            )

    if a.workers <= 1:
        init_worker(a.small_caches)
        for p in pages:
            if time.time() > deadline:
                break
            record(_task((p, opts, skip_list())))
    else:
        from concurrent.futures import FIRST_COMPLETED, wait

        # (the submodule: after PySide6's import hook the package's lazy names stay unresolved)
        from concurrent.futures.process import ProcessPoolExecutor

        todo = list(pages)
        with ProcessPoolExecutor(
            max_workers=a.workers, max_tasks_per_child=3, initializer=init_worker, initargs=(a.small_caches,)
        ) as ex:
            running = set()
            while todo or running:
                while todo and len(running) < a.workers and time.time() < deadline:
                    running.add(ex.submit(_task, (todo.pop(0), opts, skip_list())))
                if not running:
                    break
                done, running = wait(running, return_when=FIRST_COMPLETED)
                for f in done:
                    record(f.result())
    summ = summarise(results)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(
            {"args": vars(a), "elapsed_s": round(time.time() - start), "pages": results, "summary": summ},
            fh,
            indent=1,
            default=str,
        )
    print(json.dumps(summ, indent=1, default=str))
    print("-> %s (%.0f s)" % (a.out, time.time() - start))
    return 0


if __name__ == "__main__":
    sys.exit(main())
