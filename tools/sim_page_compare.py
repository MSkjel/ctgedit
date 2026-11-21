"""Compare the simulator's pages with the live runtime captures.

python tools/sim_page_compare.py [--cases DIR] [--only page[/case[/frame]]] [--ctsim exe]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("CTG_NO_CTF_PRECHECK", "1")
os.environ.setdefault("CTG_NO_DISPLAY_CHECK", "1")
# the native platform on Windows: its font engine synthesises bold like GDI (Lucida Console
# has no bold face; the offscreen platform's engine draws it regular)
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

#: the conformance cases (the engine submodule)
CASES = os.environ.get("CTG_SIM_CONFORMANCE") or os.path.join(
    ROOT, "external", "cicode-sim", "conformance", "pages"
)
#: the projects of the cases
R2_USER = os.path.join("C:\\", "ProgramData", "AVEVA Plant SCADA 2023 R2", "User")
#: simulated seconds after the snapshot is set (two page scans and more: the page settles)
SETTLE_S = 1.0
TREND_TC = 0x1003
#: an ActiveX control (the OLE control draws itself; the simulator draws none)
ACTIVEX_TC = 0x0D
#: the Cicode calls a frame's steps replay: one call with literal arguments (ctsim/call)
CALL_TIMEOUT = 10.0
#: the startup code's tasks (CaseRun._settle_startup): Include's _CitectBegin and what it starts
#: (_SA_Library_Startup, Tab_Style_Startup, _Workspace_Startup ...), the roles' StartupCode
_STARTUP_TASK = re.compile(r"(^_?CitectBegin$|Startup$|^DPS_Init$)", re.I)
STARTUP_STEP_S = 0.5
STARTUP_MIN_S = 1.0
STARTUP_MAX_S = 30.0
#: a quality step's call (sim_page_capture.set_quality)
_QUALITY_CALL = re.compile(r"\s*TagSetOverride(Good|Bad|Uncertain)\s*\(", re.I)


def _tools_path() -> None:
    t = os.path.join(ROOT, "tools")
    if t not in sys.path:
        sys.path.insert(0, t)


def masks_for(run) -> List[Tuple[int, int, int, int]]:
    """Every area a comparison leaves out: the page's trends and ActiveX controls
    (trend_masks) and the client area that was off the recording's screen."""
    _tools_path()
    from sim_page_capture import offscreen_masks

    return trend_masks(run.page) + offscreen_masks(run.case)


def live_captures(run, frame: dict):
    """The frame's live captures as PIL images (the first one first) and whether the runtime
    was steady over them (sim_page_capture.frame_captures)."""
    from PIL import Image

    _tools_path()
    from sim_page_capture import frame_captures

    names, steady = frame_captures(frame)
    return [Image.open(os.path.join(run.dir, n)) for n in names], steady


def call_steps(case: dict, frame: dict) -> List[Tuple[str, Optional[int]]]:
    """(call text, window) of each Cicode call a frame's live steps ran: ``window`` the window
    the call ran in live (0 when the step does not say). The TagSetOverride* calls of a frame's quality steps are left out:
    the snapshot has what they did (the quality word with the override flag)."""
    out = []
    for st in frame.get("steps") or ():
        if isinstance(st, dict) and st.get("call"):
            if _QUALITY_CALL.match(str(st["call"])):
                continue
            w = st.get("window", 0)
            out.append((str(st["call"]), int(w) if w is not None else None))
    return out


def case_dirs(root: str, only: Optional[str] = None) -> List[str]:
    """Every case folder under ``root`` (<project>/<page>/<case>), sorted; ``only`` =
    'page' or 'page/case' (the frame part is the caller's)."""
    out = []
    for project in sorted(os.listdir(root)):
        pdir = os.path.join(root, project)
        if not os.path.isdir(pdir):
            continue
        for page in sorted(os.listdir(pdir)):
            for case in sorted(
                os.listdir(os.path.join(pdir, page)) if os.path.isdir(os.path.join(pdir, page)) else ()
            ):
                d = os.path.join(pdir, page, case)
                if not os.path.isfile(os.path.join(d, "case.json")):
                    continue
                if only:
                    parts = only.replace("\\", "/").split("/")
                    if parts[0].lower() != page.lower():
                        continue
                    if len(parts) > 1 and parts[1] and parts[1].lower() != case.lower():
                        continue
                out.append(d)
    return out


def _app():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def snapshot_writes(snapshot: Dict[str, dict]) -> List[Tuple[str, object, Optional[str]]]:
    """(name, value, quality) per readable snapshot entry (a CtAPI error entry is left out;
    the quality word wins over the name, the override flag included)."""
    out = []
    for name, e in snapshot.items():
        if not isinstance(e, dict) or "error" in e:
            continue
        q = e.get("qualityWord") or e.get("quality") or "good"
        out.append((name, e.get("value"), str(q)))
    return out


def trend_masks(page) -> List[Tuple[int, int, int, int]]:
    """(x, y, w, h) of the page's trends (they scroll with time alone: the README's mask) and
    its ActiveX controls (they draw themselves, whenever they are ready)."""
    out = []
    for o in page.objects:
        if o.raw.get("_type_code") not in (TREND_TC, ACTIVEX_TC):
            continue
        g = o.geometry
        x, y = int(g.x), int(g.y)
        out.append((x, y, int(g.extent_x) + 1, int(g.extent_y) + 1))
    return out


def render_states(page, states, flash_on: bool, size: Tuple[int, int], aa: bool = False, drawn=None):
    """The page with the engine's states, as the Simulate window draws it, at ``size``
    (window client pixels = page pixels from (0, 0)); a QImage. ``drawn`` = the page's
    Cicode output (PageState.drawn), drawn over it as the window does."""
    from PySide6 import QtCore, QtGui
    from ctgedit.render.scene import PageScene

    w, h = size
    scene = PageScene()
    scene.set_page(page)
    scene.set_runtime_look(True)
    # aliased text needs the fonts' NoAntialias strategy on Windows (the painter hint alone
    # leaves text smoothed)
    scene.set_aliased_text(not aa)
    scene.apply_states(states, replace=True)
    if drawn:
        scene.sim_layer().apply_drawn(drawn)
    scene.set_flash_phase(flash_on)
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor(255, 255, 255))
    p = QtGui.QPainter(img)
    hints = QtGui.QPainter.SmoothPixmapTransform
    if aa:
        hints |= QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing
    p.setRenderHints(hints, True)
    if not aa:
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, False)
    scene.render(p, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(0, 0, w, h))
    p.end()
    scene.clear_states()
    return img, scene


def qimage_to_pil(img):
    from PIL import Image
    from PySide6 import QtCore

    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG")
    import io

    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")


class CaseRun:
    """One case on one engine server: the page opened once, then the frames in order (the
    live recording kept its window open across the frames too)."""

    def __init__(
        self,
        case_dir: str,
        loc,
        user: str = R2_USER,
        live_state: bool = True,
        workspace: bool = False,
        startup: bool = True,
        hold: bool = True,
        seed_trends: bool = True,
    ):
        self.dir = case_dir
        self.live_state = live_state
        #: lock the snapshot tags once set (ctsim/lockTag), so the project's simulation code
        #: leaves the recorded state as it is (finding 4)
        self.hold = hold
        self._locked: set = set()
        #: seed the trends' history as the Simulate window does (ctsim/options)
        self.seed_trends = seed_trends
        #: the last frame's snapshot tags that moved after the settle, its replayed calls' notes
        self.last_drift: List[str] = []
        self.call_notes: List[str] = []
        self.open_notes: List[str] = []
        self.workspace = workspace
        #: run the project's startup code before the page opens, as the Simulate window does
        #: (load {startup: 'auto'}: the theme, the IO simulation)
        self.startup = startup
        #: the engine has no ctsim/setAlarm / setEquipment
        self.live_unavailable = False
        with open(os.path.join(case_dir, "case.json"), encoding="utf-8") as fh:
            self.case = json.load(fh)
        self.project = os.path.join(user, self.case["runtime"]["project"])
        self.page_name = self.case["page"]["name"]
        self.page_path = os.path.join(self.project, self.case["page"]["ctg"]["file"])
        self.size = tuple(self.case["window"]["client"][2:])
        self.loc = loc
        self.session = None
        self.window = None
        self.page = None
        self.model = None
        self.source = ""

    def open(self) -> None:
        from ctgedit.editing import EditableDocument
        from ctgedit.sim import client as C
        from ctgedit.ui.simulate.window import runtime_page

        doc = EditableDocument.open(self.page_path)
        self.model = doc.export_page_model()
        self.page = runtime_page(self.page_path)
        if self.workspace:
            self._open_in_workspace()
            return
        # (a viewer: the engine asks for the drawings its windows need, ctsim/modelWanted, and
        # the tool answers them, as the Simulate window: the startup page's metadata ...)
        self.session = C.open_session(self.project, location=self.loc, start_timeout=180, viewer=self.startup)
        self.session.clock_request("pause")
        self.open_notes = []
        self._options()
        self._sent_models: set = set()
        window = None
        if self.startup:
            # as the live recording: the runtime started (its startup page in window 0, the
            # startup code run to its end: _CitectBegin's splash wait, then _SA_Library_Startup
            # reads [SA_Library.Meter] UseDefaultPLCLimits ...), then the page in a window of
            # its own (WinNewAt), so its code runs after the startup code has set what it
            # reads (finding 11)
            try:
                body = self.session.request(
                    "load", self.session.load_args(startup="auto", start_page=True), timeout=C.LOAD_TIMEOUT
                )
                sp = (body or {}).get("startPage") or {}
                # (window 0 shows the startup page: the page opens in the lowest free window)
                self.open_notes.append(
                    "startPage %s, startup settled after %.1f s" % (sp.get("page"), self._settle_startup())
                )
            except C.CtsimError as e:
                self.open_notes.append("startup: %s" % e)
        self._preset()
        body = self.session.open_page_full(
            self.page_name,
            window=window,
            model=self.model,
            associations=self.case.get("associations") or None,
        )
        self.window = int(body["window"])
        self.source = str(body.get("source") or "")
        self.open_notes += [str(n) for n in body.get("notes") or ()]
        self.session.clock_request("step", 0.5)

    def _preset(self) -> None:
        """Set the first frame's snapshot before the page opens."""
        from ctgedit.sim import client as C

        frames = self.case.get("frames") or []
        if not frames:
            return
        writes = snapshot_writes(frames[0].get("snapshot") or {})
        for name, value, quality in writes:
            try:
                self.session.request("setTag", C.Session.set_tag_args(name, value, quality))
            except C.CtsimError:  # swallow-ok: frame_state reports the writes that fail
                pass
        if self.hold:
            self._lock(n for n, _v, _q in writes)

    def _settle_startup(self) -> float:
        """Run the paused clock until the startup code has ended: no startup task (one the
        load started, _CitectBegin, a '<role>Startup' / '_<Project>_Startup' it starts) left
        running, at least STARTUP_MIN_S simulated; at most STARTUP_MAX_S. The simulated
        seconds it took."""
        from ctgedit.sim import client as C

        t = 0.0
        self._feed_models(self._sent_models)
        while t < STARTUP_MAX_S:
            self.session.clock_request("step", STARTUP_STEP_S)
            self._feed_models(self._sent_models)
            t += STARTUP_STEP_S
            if t < STARTUP_MIN_S:
                continue
            try:
                tasks = (self.session.request("tasks", {}) or {}).get("tasks") or []
            except C.CtsimError:
                break
            if not any(
                _STARTUP_TASK.search(str(k.get("function") or k.get("name") or ""))
                and str(k.get("state") or "") not in ("Stop", "Halt", "Dead")
                for k in tasks
            ):
                break
        return t

    def _options(self) -> None:
        """The simulator options the Simulate window sets for the tab that drives the
        simulation (window.py, ctsim_source._send_seed): the trends' history seeded."""
        from ctgedit.sim import client as C

        if not self.seed_trends:
            return
        try:
            self.session.request("options", {"seedTrendHistory": True})
        except C.CtsimError as e:
            self.open_notes.append("options: %s" % e)

    def _replay_calls(self, frame: dict) -> List[str]:
        """Replay a frame's recorded Cicode calls."""
        from ctgedit.sim import client as C

        notes = []
        live_win = int((self.case.get("window") or {}).get("number", -1))
        for expr, w in call_steps(self.case, frame):
            if w == live_win:
                target = self.window
            elif w == 0 and self.window != 0:
                target = 0
            else:
                notes.append("%s: ran in live window %s, no simulator window matches" % (expr, w))
                continue
            try:
                body = self.session.request("call", {"expr": expr, "window": target}, timeout=CALL_TIMEOUT)
                if isinstance(body, dict) and str(body.get("state") or "") == "halt":
                    notes.append("%s: halted %s" % (expr, body.get("haltReason") or ""))
            except C.CtsimError as e:
                notes.append("%s: %s" % (expr, e))
        return notes

    def _lock(self, names) -> None:
        """Lock the snapshot tags not locked yet (an unknown name or an alarm property is
        refused: the frame runs on without it)."""
        from ctgedit.sim import client as C

        for name in names:
            key = name.lower()
            if key in self._locked:
                continue
            self._locked.add(key)
            try:
                self.session.request("lockTag", {"name": name, "locked": True})
            except C.CtsimError:  # swallow-ok: an alarm property or a name the engine lacks
                pass

    def drifted(self, writes) -> List[str]:
        """The snapshot tags whose engine value is not the recorded one now (name: recorded ->
        now), after the settle."""
        from ctgedit.sim import client as C

        want = {}
        for name, value, _q in writes:
            if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                want[name] = value
        if not want:
            return []
        try:
            body = self.session.request("tags", {"names": list(want)})
        except C.CtsimError:
            return []
        out = []
        low = {k.lower(): k for k in want}
        for t in (body or {}).get("tags") or ():
            # (a tag entry is named by the defined tag: an Equip.Item asked for is matched by
            # its equipment and item)
            name = None
            for cand in (t.get("name"), "%s.%s" % (t.get("equipment"), t.get("item"))):
                if cand and str(cand).lower() in low:
                    name = low[str(cand).lower()]
                    break
            if name is None:
                continue
            a, b = want[name], t.get("value")
            try:
                # (a REAL tag of an integer raw range reads back quantised: 342 -> 341.993)
                same = abs(float(a) - float(b)) <= 0.02 + 1e-4 * abs(float(a))
            except TypeError, ValueError:
                same = str(a) == str(b)
            if not same:
                out.append("%s: %r -> %r" % (name, a, b))
        return out

    def _feed_models(self, sent: set) -> None:
        """Send the drawing of every window's page the engine has none of (what the Simulate
        window does for ctsim/modelWanted)."""
        from ctgedit.editing import EditableDocument

        for w in self.session.windows():
            key = (w.get("window"), w.get("page"))
            if not w.get("page") or w.get("hasModel") or not w.get("path") or key in sent:
                continue
            sent.add(key)
            try:
                m = EditableDocument.open(w["path"]).export_page_model()
                self.session.page_model(int(w["window"]), m)
            except Exception as e:  # noqa: BLE001 -- the page runs without its drawing
                self.open_notes.append("pageModel %s: %s" % (w.get("page"), e))

    def _open_in_workspace(self) -> None:
        from ctgedit.sim import client as C

        self.open_notes = []
        self.session = C.open_session(self.project, location=self.loc, start_timeout=180, viewer=True)
        self._options()
        self.session.clock_request("run")
        body = self.session.request(
            "load", self.session.load_args(startup="auto", start_page=True), timeout=C.LOAD_TIMEOUT
        )
        sp = (body or {}).get("startPage") or {}
        self.open_notes.append("startPage %s (%s)" % (sp.get("page"), sp.get("source")))
        self._preset()
        done: Dict[str, object] = {}
        self.session.request_async(
            "navigate",
            {"page": self.page_name, "model": self.model},
            callback=lambda b, e: done.update(body=b, err=e),
            timeout=C.LOAD_TIMEOUT,
        )
        sent: set = set()
        end = time.monotonic() + 120
        while "body" not in done and time.monotonic() < end:
            self._feed_models(sent)
            time.sleep(0.3)
        if done.get("err") is not None or "body" not in done:
            raise RuntimeError("navigate %s: %s" % (self.page_name, done.get("err") or "no answer"))
        nav = done["body"] or {}
        self.window = int(nav.get("window", -1))
        if self.window < 0:
            raise RuntimeError("navigate %s: shown in no window (%s)" % (self.page_name, nav))
        self.source = "workspace:" + str(nav.get("source") or "")
        # the page's first scans and the panes' drawings, then the frames' own steps
        for _k in range(10):
            self._feed_models(sent)
            time.sleep(0.3)
        self.session.clock_request("pause")
        self.session.clock_request("step", 0.5)

    def frame_state(self, frame: dict):
        """Set the frame's snapshot (and, ctsim-pagecase/1, its alarm server and equipment
        state after the tags: a tag write marks its alarms for the next scan, a set alarm
        holds), let the page scan, read its states. (a PageState, the failed tag writes, the
        failed live-state requests)."""
        from ctgedit.sim import client as C

        _tools_path()
        from sim_page_capture import live_state_requests

        writes = snapshot_writes(frame.get("snapshot") or {})
        live = live_state_requests(self.case, frame) if self.live_state else []
        failed, live_failed = [], []
        self.call_notes = self._replay_calls(frame)
        for k in range(2):
            for name, value, quality in writes:
                try:
                    self.session.request("setTag", C.Session.set_tag_args(name, value, quality))
                except C.CtsimError as e:
                    if k == 0:
                        failed.append("%s: %s" % (name, e))
            for cmd, args in live:
                try:
                    self.session.request(cmd, args)
                except C.NotAvailable as e:
                    # an engine without the requests: said once, the frame runs on its tags
                    live_failed.append("%s: %s" % (cmd, e))
                    self.live_unavailable = True
                    live = []
                    break
                except C.CtsimError as e:
                    if k == 0:
                        live_failed.append("%s %s: %s" % (cmd, args.get("name"), e))
            if k == 0 and self.hold:
                self._lock(n for n, _v, _q in writes)
            self.session.clock_request("step", 0.1 if k == 0 else SETTLE_S)
        st = self.session.page_state(self.window)
        self.last_drift = self.drifted(writes)
        return st, failed, live_failed

    def close(self) -> None:
        if self.session is not None:
            try:
                self.session.close_page(self.window)
            except Exception:  # noqa: BLE001 -- the server stops anyway
                pass
            self.session.close()
            self.session = None


def compare_frame(run: CaseRun, frame: dict, out_dir: str, tolerance: int, aa: bool) -> dict:
    _tools_path()
    from sim_page_capture import compare_images, diff_image

    st, failed, live_failed = run.frame_state(frame)
    img, _scene = render_states(
        run.page, st.objects, st.flash_on, run.size, aa=aa, drawn=getattr(st, "drawn", None)
    )
    sim = qimage_to_pil(img)
    live, _steady = live_captures(run, frame)
    n, total, bbox, bad = compare_images(live, sim, tolerance)
    masks = masks_for(run)
    nm, totm, _bb, badm = compare_images(live, sim, tolerance, masks) if masks else (n, total, bbox, bad)
    case = os.path.basename(run.dir)
    stem = "%s_%s_%s" % (run.page_name, case, frame["name"])
    os.makedirs(out_dir, exist_ok=True)
    sim.save(os.path.join(out_dir, stem + "_sim.png"), "PNG", optimize=True)
    diff_image(live[0], badm).save(os.path.join(out_dir, stem + "_diff.png"), "PNG", optimize=True)
    return OrderedDict(
        [
            ("page", run.page_name),
            ("case", case),
            ("frame", frame["name"]),
            ("size", list(run.size)),
            ("source", run.source),
            ("differing", round(100.0 * n / float(total or 1), 2)),
            ("masked", round(100.0 * nm / float(totm or 1), 2)),
            ("bbox", bbox),
            ("objects", len(st.objects)),
            ("tagsFailed", failed[:5]),
            ("liveStateFailed", live_failed[:5]),
            ("liveStateUnavailable", run.live_unavailable),
            ("trendMasks", len(masks)),
            ("drifted", run.last_drift[:20]),
            ("calls", run.call_notes),
        ]
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", default=CASES)
    ap.add_argument("--only")
    ap.add_argument("--ctsim")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "ctgedit_sim_compare"))
    ap.add_argument("--json")
    ap.add_argument("--tolerance", type=int, default=8)
    ap.add_argument("--aa", action="store_true")
    ap.add_argument(
        "--no-live-state",
        action="store_true",
        help="set only the tag snapshots, not the frames' alarms and equipment state",
    )
    ap.add_argument(
        "--no-startup",
        action="store_true",
        help="open each page without the project's startup code",
    )
    ap.add_argument(
        "--workspace",
        action="store_true",
        help="run each page in its SA workspace (the startup page first, then ctsim/navigate)",
    )
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
    _app()
    from ctgedit.io import reader
    from ctgedit.sim import client as C

    reader.set_label_mode("native")
    loc = C.find_engine(args.ctsim or None)
    # a server of our own (never one another program runs on the project)
    os.environ[C.SERVERS_ENV] = tempfile.mkdtemp(prefix="ctsim_compare_servers_")
    only_frame = None
    if args.only and args.only.count("/") >= 2:
        only_frame = args.only.split("/")[2]
    rows = []
    for d in case_dirs(args.cases, args.only):
        run = CaseRun(
            d,
            loc,
            live_state=not args.no_live_state,
            workspace=args.workspace,
            startup=not args.no_startup,
            hold=not args.no_hold,
            seed_trends=not args.no_seed_trends,
        )
        t0 = time.monotonic()
        try:
            try:
                run.open()
            except C.NotAvailable as e:
                print("this ctsim.exe has no page animator: %s" % e, flush=True)
                return 3
            for f in run.case["frames"]:
                if only_frame and f["name"] != only_frame:
                    continue
                r = compare_frame(run, f, args.out, args.tolerance, args.aa)
                rows.append(r)
                print(
                    "%-16s %-14s %-20s %6.2f %%  masked %6.2f %%  (%s, %d states)"
                    % (
                        r["page"],
                        r["case"],
                        r["frame"],
                        r["differing"],
                        r["masked"],
                        r["source"],
                        r["objects"],
                    ),
                    flush=True,
                )
        finally:
            run.close()
        print(
            "  %s: %.1f s (%s)"
            % (os.path.relpath(d, args.cases), time.monotonic() - t0, "; ".join(run.open_notes[:1])),
            flush=True,
        )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
