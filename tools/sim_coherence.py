"""How closely the simulator's pages match the live runtime captures.

python tools/sim_coherence.py [--cases DIR] [--only page[/case[/frame]]] [--ctsim exe]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import sim_page_compare as SPC  # noqa: E402  (sets the Qt platform and the env first)

#: the animation keys reported per object (the page's pg* tables are made from them)
ANIM_KEYS = (
    "VISIBLE",
    "DISABLED",
    "COL_A",
    "COL_EXP",
    "COL_ARR",
    "COLMODE",
    "COL_MAXON",
    "TXT_A",
    "TXT_STR",
    "TXTMODE",
    "TXT_ON",
    "TXT_OFF",
    "MVX",
    "MVY",
    "ROT",
    "SZX",
    "SZY",
    "FIL",
    "SYMBOL",
    "AI_ON",
)


def _np(img):
    import numpy as np

    return np.asarray(img.convert("RGB"), dtype=np.int16)


def _differ(a, b, tol: int):
    import numpy as np

    return np.abs(a - b).max(axis=2) > tol


def _mask(shape, masks):
    import numpy as np

    use = np.ones(shape[:2], dtype=bool)
    for x, y, w, h in masks:
        use[max(0, y) : max(0, y + h), max(0, x) : max(0, x + w)] = False
    return use


def owner_map(page, size: Tuple[int, int]):
    """Per pixel the index of the topmost record whose extent (rect1) holds it, -1 = none: the
    records in drawing order (record order), behaviour records and anchors left out."""
    import numpy as np

    w, h = size
    m = np.full((h, w), -1, dtype=np.int32)
    for i, o in enumerate(page.objects):
        g = o.geometry
        if g is None or o.raw.get("_non_drawable"):
            continue
        if o.members and o.kind.name in ("GROUP", "SYMBOL_SET"):
            continue  # a container: its members are drawn, not its box
        x0, y0 = int(g.x), int(g.y)
        x1, y1 = int(g.x + g.extent_x) + 1, int(g.y + g.extent_y) + 1
        if x1 <= 0 or y1 <= 0 or x0 >= w or y0 >= h or x1 - x0 > w * 2 or y1 - y0 > h * 2:
            continue
        m[max(0, y0) : min(h, y1), max(0, x0) : min(w, x1)] = i
    return m


#: ID-buffer codes above the record indexes: an alarm indicator frame of record i
AI_BASE = 1 << 22


def id_buffer(page, states, flash_on: bool, size: Tuple[int, int], drawn=None):
    """Per pixel, the page record that painted it (-1 for none)."""
    import numpy as np
    from PySide6 import QtCore, QtGui, QtWidgets
    from ctgedit.render.scene import PAGE_INDEX_ROLE, PageScene

    w, h = size
    scene = PageScene()
    scene.set_page(page)
    scene.set_runtime_look(True)
    scene.apply_states(states, replace=True)
    if drawn:
        scene.sim_layer().apply_drawn(drawn)
    scene.set_flash_phase(flash_on)
    layer = scene.sim_layer()
    ai_items = {id(it): i for i, it in getattr(scene, "_ai_items", {}).items()}

    def code(i: int) -> QtGui.QColor:
        v = i + 1
        return QtGui.QColor((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)

    for it in scene.items():
        i = ai_items.get(id(it))
        if i is not None:
            i = AI_BASE + i
        else:
            i = it.data(PAGE_INDEX_ROLE)
            if i is None:
                continue
            try:
                i = layer.record_under(int(i), [it])
            except Exception:  # noqa: BLE001 -- the root record
                i = int(i)
        c = code(int(i))
        it.setGraphicsEffect(None)
        if isinstance(it, QtWidgets.QAbstractGraphicsShapeItem):
            if it.pen().style() != QtCore.Qt.NoPen:
                p = QtGui.QPen(it.pen())
                p.setColor(c)
                it.setPen(p)
            if it.brush().style() != QtCore.Qt.NoBrush:
                it.setBrush(QtGui.QBrush(c))
        elif isinstance(it, QtWidgets.QGraphicsPixmapItem):
            pm = it.pixmap()
            if not pm.isNull():
                img = pm.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
                mask = QtGui.QImage(img.size(), QtGui.QImage.Format_ARGB32)
                mask.fill(QtCore.Qt.transparent)
                pp = QtGui.QPainter(mask)
                pp.drawImage(0, 0, img)
                pp.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
                pp.fillRect(mask.rect(), c)
                pp.end()
                it.setPixmap(QtGui.QPixmap.fromImage(mask))
                it.setTransformationMode(QtCore.Qt.FastTransformation)
        elif isinstance(it, QtWidgets.QGraphicsTextItem):
            it.setDefaultTextColor(c)
    img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(0, 0, 0))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, False)
    p.setRenderHint(QtGui.QPainter.TextAntialiasing, False)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, False)
    scene.render(p, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(0, 0, w, h))
    p.end()
    ptr = img.constBits()
    a = np.frombuffer(ptr, dtype=np.uint8, count=img.sizeInBytes()).reshape(h, img.bytesPerLine() // 4, 4)[
        :, :w
    ]
    v = (a[:, :, 2].astype(np.int32) << 16) | (a[:, :, 1].astype(np.int32) << 8) | a[:, :, 0]
    return v - 1


def _anim(o) -> Dict[str, str]:
    props = o.raw.get("_props") or {}
    return {k: str(props[k])[:120] for k in ANIM_KEYS if props.get(k) not in (None, "")}


def _chain_anim(page, i: int) -> Dict[str, str]:
    """The object's own animation keys and its groups' (a group's Hidden when hides it)."""
    out = {}
    j, guard = i, 0
    while j is not None and guard < 16:
        for k, v in _anim(page.objects[j]).items():
            out.setdefault(
                k if j == i else "%s(group AN %s)" % (k, page.objects[j].raw.get("_handle", -2) + 1), v
            )
        j = page.objects[j].parent
        guard += 1
    return out


def _state_of(states, an: int) -> Optional[dict]:
    st = states.get(an) if states else None
    if st is None:
        return None
    out = {}
    for f in (
        "shown",
        "disabled",
        "fill_colour",
        "text",
        "error",
        "symbol",
        "symbol_state",
        "alarm",
        "indication",
    ):
        v = getattr(st, f, None)
        if v not in (None, "", False) or f == "shown":
            out[f] = v
    return out


def compare_frame(run, frame: dict, out_dir: str, tol: int, top: int) -> dict:
    import numpy as np
    from PIL import Image

    st, failed, live_failed = run.frame_state(frame)
    # the engine resolves some colours per flash phase (a flashing colour pair of an
    # expression, an alarm indicator's FG / BG): the state of the OTHER phase too, the paused
    # clock stepped until the phase changes
    st2 = None
    for _k in range(8):
        run.session.clock_request("step", 0.25)
        s2 = run.session.page_state(run.window)
        if bool(s2.flash_on) != bool(st.flash_on):
            st2 = s2
            break
    st_on, st_off = (st, st2 or st) if st.flash_on else (st2 or st, st)
    # the page's Cicode output (PageState.drawn) drawn as sim_page_compare and the Simulate
    # window draw it, workspace or not (finding 9)
    drawn = getattr(st, "drawn", None)
    on_img, _s = SPC.render_states(
        run.page, st_on.objects, True, run.size, drawn=getattr(st_on, "drawn", None)
    )
    off_img, _s = SPC.render_states(
        run.page, st_off.objects, False, run.size, drawn=getattr(st_off, "drawn", None)
    )
    on, off = SPC.qimage_to_pil(on_img), SPC.qimage_to_pil(off_img)
    lives, steady = SPC.live_captures(run, frame)
    l1 = lives[0]
    a_on, a_off, a1 = _np(on), _np(off), _np(l1)
    # the other flash phase: of the later captures (4 captures 250 ms apart, 'captures'; else
    # 'capture2'), the one that differs most from the first; a frame whose captures were all
    # the same (capturesIdentical) was steady: its 'other phase' is the first capture again
    a2 = l2 = None
    best = -1
    for lk in lives[1:]:
        ak = _np(lk)
        d = int(_differ(ak, a1, tol).sum())
        if d > best:
            best, a2, l2 = d, ak, lk
    if a2 is None and steady:
        a2, l2 = a1, l1
    use = _mask(a1.shape, SPC.masks_for(run))
    total = int(use.sum()) or 1
    # 1. phase-correct match
    if a2 is not None:
        pa = (_differ(a_on, a1, tol) | _differ(a_off, a2, tol)) & use
        pb = (_differ(a_off, a1, tol) | _differ(a_on, a2, tol)) & use
        bad, pairing = (pa, "on/off") if pa.sum() <= pb.sum() else (pb, "off/on")
    else:
        pa = _differ(a_on, a1, tol) & use
        pb = _differ(a_off, a1, tol) & use
        bad, pairing = (pa, "on") if pa.sum() <= pb.sum() else (pb, "off")
    # the lenient score (any capture, the engine's phase): for comparison
    phase_img = on if st.flash_on else off
    from sim_page_capture import compare_images

    n_old, tot_old, _bb, _b = compare_images(
        [l1] + ([l2] if l2 is not None else []), phase_img, tol, SPC.masks_for(run)
    )
    # 2. blinking
    live_blink = (_differ(a1, a2, tol) & use) if a2 is not None else np.zeros(use.shape, bool)
    sim_blink = _differ(a_on, a_off, tol) & use
    runtime_only_blink = live_blink & ~sim_blink
    sim_only_blink = sim_blink & ~live_blink if a2 is not None else np.zeros(use.shape, bool)
    # 3. objects
    om = id_buffer(run.page, st_on.objects, True, run.size, drawn=drawn)
    om_off = id_buffer(run.page, st_off.objects, False, run.size, drawn=drawn)
    om = np.where(om >= 0, om, om_off)

    def per_object(mask) -> List[Tuple[int, int]]:
        idx = om[mask]
        nobj = len(run.page.objects)
        idx = idx[((idx >= 0) & (idx < nobj)) | ((idx >= AI_BASE) & (idx < AI_BASE + nobj))]
        if not idx.size:
            return []
        vals, counts = np.unique(idx, return_counts=True)
        order = np.argsort(-counts)
        return [(int(vals[k]), int(counts[k])) for k in order[:top]]

    def describe(i: int, n: int, kind_mask=None) -> dict:
        if i >= AI_BASE:
            g = run.page.objects[i - AI_BASE]
            an = int(g.raw.get("_handle", -2)) + 1
            d = OrderedDict([("index", i - AI_BASE), ("an", an), ("kind", "alarm indicator"), ("pixels", n)])
            s = _state_of(st.objects, an)
            if s:
                d["state"] = s
            a = getattr(st.objects.get(an), "alarm", None) if st.objects else None
            if a:
                d["alarm"] = a
            return d
        o = run.page.objects[i]
        an = int(o.raw.get("_handle", -2)) + 1
        d = OrderedDict([("index", i), ("an", an), ("kind", o.kind.name.lower()), ("pixels", n)])
        name = (o.raw.get("_props") or {}).get("__AN_NAME__")
        if name:
            d["name"] = name
        if o.style is not None and o.style.text:
            d["text"] = o.style.text[:60]
        anim = _chain_anim(run.page, i)
        if anim:
            d["animation"] = anim
        s = _state_of(st.objects, an)
        if s:
            d["state"] = s
        if o.raw.get("_flash_slots"):
            d["flashingColours"] = True
        return d

    objects = [describe(i, n) for i, n in per_object(bad)]
    blink_objects = [describe(i, n) for i, n in per_object(runtime_only_blink)]
    sim_blink_objects = [describe(i, n) for i, n in per_object(sim_only_blink)]
    # outputs: both phases + a diff (live grey, mismatch red, runtime-only blink blue)
    case = os.path.basename(run.dir)
    stem = "%s_%s_%s" % (run.page_name, case, frame["name"])
    os.makedirs(out_dir, exist_ok=True)
    on.save(os.path.join(out_dir, stem + "_sim_on.png"), "PNG", optimize=True)
    off.save(os.path.join(out_dir, stem + "_sim_off.png"), "PNG", optimize=True)
    g = np.asarray(l1.convert("L"), dtype=np.uint8)
    rgb = np.stack([g // 2 + 96] * 3, axis=2)
    rgb[bad] = (255, 0, 0)
    rgb[runtime_only_blink] = (0, 90, 255)
    Image.fromarray(rgb.astype(np.uint8), "RGB").save(
        os.path.join(out_dir, stem + "_diff.png"), "PNG", optimize=True
    )
    return OrderedDict(
        [
            ("page", run.page_name),
            ("case", case),
            ("frame", frame["name"]),
            ("pairing", pairing),
            ("differing", round(100.0 * int(bad.sum()) / total, 2)),
            ("differingLenient", round(100.0 * n_old / float(tot_old or 1), 2)),
            ("runtimeBlinksSimSteady", int(runtime_only_blink.sum())),
            ("simBlinksRuntimeSteady", int(sim_only_blink.sum())),
            ("liveBlinkPixels", int(live_blink.sum())),
            (
                "changedByCapture2",
                sorted((frame.get("changedByCapture2") or {}).keys())
                if isinstance(frame.get("changedByCapture2"), dict)
                else frame.get("changedByCapture2"),
            ),
            ("objects", objects),
            ("blinkObjects", blink_objects),
            ("simBlinkObjects", sim_blink_objects),
            ("tagsFailed", failed[:5]),
            ("liveStateFailed", live_failed[:5]),
            ("states", len(st.objects)),
            ("source", run.source),
            ("bothPhases", st2 is not None),
            ("liveSteady", bool(steady)),
            ("drifted", run.last_drift[:20]),
            ("calls", run.call_notes),
        ]
    )


def _line(o: dict) -> str:
    bits = ["AN %s %s" % (o["an"], o["kind"]), "%d px" % o["pixels"]]
    if o.get("name"):
        bits.append(o["name"])
    if o.get("text"):
        bits.append(repr(o["text"]))
    if o.get("animation"):
        bits.append(", ".join("%s=%s" % (k, v[:50]) for k, v in list(o["animation"].items())[:3]))
    if o.get("state"):
        bits.append("state %s" % o["state"])
    if o.get("flashingColours"):
        bits.append("flashing colours")
    return "; ".join(bits)


# --------------------------------------------------------------------------- the sweep plan
#: tag types set as digital / analog by the sweep (variable.dbf TYPE)
DIGITAL_TYPES = ("DIGITAL",)
ANALOG_TYPES = ("INT", "UINT", "LONG", "ULONG", "REAL", "LONGBCD", "BCD", "BYTE")
_PAGE_VAR = __import__("re").compile(r"PageGet(Int|Str)\s*\(\s*\"([^\"]+)\"", __import__("re").I)


def _num(text: str, default: float) -> float:
    try:
        return float(str(text).strip())
    except TypeError, ValueError:
        return default


def sweep_case(model, page: str, ctsim: str, assoc: Optional[Dict[str, str]], max_tags: int = 400) -> dict:
    """A capture case that drives every feature of the page."""
    import sim_page_capture as CAP

    refs, _eq = CAP.page_refs(model, page, ctsim, assoc)
    digital, analog = [], []
    for name, m in refs.items():
        if m["kind"] not in ("tag", "item"):
            continue
        t = (m.get("type") or "").upper()
        row = model.tags.get((m.get("tag") or "").lower(), {})
        if t in DIGITAL_TYPES:
            digital.append(m["name"])
        elif t in ANALOG_TYPES:
            lo = _num(row.get("ENG_ZERO"), 0.0)
            hi = _num(row.get("ENG_FULL"), 100.0)
            if hi <= lo:
                lo, hi = 0.0, 100.0
            analog.append((m["name"], lo, hi, t))
    digital, analog = digital[:max_tags], analog[:max_tags]
    # page variables the page's code reads
    pvars = set()
    for s in CAP.ctg_strings(os.path.join(model.dir, page + ".ctg")):
        for kind, name in _PAGE_VAR.findall(s or ""):
            pvars.add((kind.lower(), name))
    frames = [{"name": "s0_baseline", "description": "as found"}]

    def val(t, v):
        return (
            int(round(v)) if t in ("INT", "UINT", "LONG", "ULONG", "BYTE", "BCD", "LONGBCD") else round(v, 3)
        )

    if digital:
        frames.append(
            {
                "name": "d1_all_on",
                "description": "every digital the page reads = 1",
                "set": {n: 1 for n in digital},
            }
        )
        frames.append(
            {
                "name": "d2_all_off",
                "description": "every digital the page reads = 0",
                "set": {n: 0 for n in digital},
            }
        )
        frames.append(
            {
                "name": "d3_even_on",
                "description": "even digitals 1, odd 0",
                "set": {n: (1 if k % 2 == 0 else 0) for k, n in enumerate(digital)},
            }
        )
        frames.append(
            {
                "name": "d4_odd_on",
                "description": "odd digitals 1, even 0",
                "set": {n: (0 if k % 2 == 0 else 1) for k, n in enumerate(digital)},
            }
        )
    if analog:
        for fname, frac, what in (
            ("a1_low", 0.0, "engineering zero"),
            ("a2_mid", 0.5, "the middle of the range"),
            ("a3_high", 1.0, "engineering full scale"),
        ):
            frames.append(
                {
                    "name": fname,
                    "description": "every analog the page reads at %s" % what,
                    "set": {n: val(t, lo + (hi - lo) * frac) for n, lo, hi, t in analog},
                }
            )
    if pvars:

        def calls(on):
            out = []
            for kind, name in sorted(pvars):
                if kind == "int":
                    out.append('PageSetInt("%s", %d)' % (name, 1 if on else 0))
                else:
                    out.append('PageSetStr("%s", "%s")' % (name, "1" if on else ""))
            return out

        frames.append(
            {
                "name": "p1_vars_on",
                "description": "the page variables its code reads set (int 1, string '1')",
                "call": calls(True),
            }
        )
        frames.append(
            {"name": "p2_vars_off", "description": "the page variables set to 0 / ''", "call": calls(False)}
        )
    return {
        "name": "sweep",
        "description": "Feature sweep (tools/sim_coherence.py plan): %d "
        "digital, %d analog tags / items, %d page variables." % (len(digital), len(analog), len(pvars)),
        "frames": frames,
    }


def cmd_plan(args) -> int:
    import sim_page_capture as CAP

    base = CAP.load_plan(args.base)
    model = CAP.ProjectModel(CAP.project_dir(base.get("project", "ExampleSA")))
    ctsim = CAP.ctsim_exe(args.ctsim)
    pages = []
    for pg in base["pages"]:
        if args.pages and pg["page"].lower() not in {p.lower() for p in args.pages}:
            continue
        case = sweep_case(model, pg["page"], ctsim, pg.get("associations"), args.max_tags)
        q = {k: v for k, v in pg.items() if k != "cases"}
        q["cases"] = [case]
        pages.append(q)
        print("%-16s %s" % (pg["page"], case["description"]), flush=True)
    out = {
        "project": base.get("project", "ExampleSA"),
        "comment": "Generated by tools/sim_coherence.py plan from %s" % os.path.basename(args.base),
        "pages": pages,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print("plan: %s (%d pages)" % (args.out, len(pages)))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["plan"]:
        import sim_page_capture as CAP

        pp = argparse.ArgumentParser(description="Generate the feature-sweep capture plan")
        pp.add_argument("--base", default=CAP.PLAN)
        pp.add_argument("--out", required=True)
        pp.add_argument("--ctsim")
        pp.add_argument("--max-tags", type=int, default=400)
        pp.add_argument("pages", nargs="*")
        return cmd_plan(pp.parse_args(argv[1:]))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", default=SPC.CASES)
    ap.add_argument("--only")
    ap.add_argument("--ctsim")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "ctgedit_sim_coherence"))
    ap.add_argument("--json")
    ap.add_argument("--tolerance", type=int, default=8)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--workspace", action="store_true")
    ap.add_argument("--no-live-state", action="store_true")
    ap.add_argument(
        "--no-hold",
        action="store_true",
        help="leave the snapshot tags unlocked (the project's code may move them)",
    )
    ap.add_argument(
        "--no-seed-trends",
        action="store_true",
        help="no trend history before the start (ctsim/options seedTrendHistory)",
    )
    args = ap.parse_args(argv)
    SPC._app()
    from ctgedit.io import reader
    from ctgedit.sim import client as C

    reader.set_label_mode("native")
    loc = C.find_engine(args.ctsim or None)
    os.environ[C.SERVERS_ENV] = tempfile.mkdtemp(prefix="ctsim_coherence_servers_")
    only_frame = args.only.split("/")[2] if args.only and args.only.count("/") >= 2 else None
    rows = []
    for d in SPC.case_dirs(args.cases, args.only):
        run = SPC.CaseRun(
            d,
            loc,
            live_state=not args.no_live_state,
            workspace=args.workspace,
            hold=not args.no_hold,
            seed_trends=not args.no_seed_trends,
        )
        t0 = time.monotonic()
        try:
            run.open()
            for f in run.case["frames"]:
                if only_frame and f["name"] != only_frame:
                    continue
                r = compare_frame(run, f, args.out, args.tolerance, args.top)
                rows.append(r)
                print(
                    "%-16s %-14s %-22s %6.2f %% (lenient %5.2f %%)  runtime blinks / sim "
                    "steady %6d px  sim blinks / runtime steady %5d px"
                    % (
                        r["page"],
                        r["case"],
                        r["frame"],
                        r["differing"],
                        r["differingLenient"],
                        r["runtimeBlinksSimSteady"],
                        r["simBlinksRuntimeSteady"],
                    ),
                    flush=True,
                )
                for o in r["objects"][:4]:
                    print("      differs: " + _line(o), flush=True)
                for o in r["blinkObjects"][:3]:
                    print("      blinks live only: " + _line(o), flush=True)
        except Exception as e:  # noqa: BLE001 -- the next case
            print("  %s: %r" % (os.path.relpath(d, args.cases), e), flush=True)
        finally:
            run.close()
        print("  %s: %.1f s" % (os.path.relpath(d, args.cases), time.monotonic() - t0), flush=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
