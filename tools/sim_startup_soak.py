"""Start-up and race soak of the Simulate window on the real simulator.

python tools/sim_startup_soak.py [--cases mixer,local,...] [--runs 10] [--seed 1]
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import os
import random
import shutil
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# the ctgedit to soak: CTG_SOAK_CODE (a folder holding another ctgedit to compare
# with), else this checkout's
CODE = os.environ.get("CTG_SOAK_CODE") or ROOT
if CODE not in sys.path:
    sys.path.insert(0, CODE)

R2_USER = os.path.join("C:\\", "ProgramData", "AVEVA Plant SCADA 2023 R2", "User")
MAIN_BUILD = os.path.join(
    ROOT,
    "external",
    "cicode-sim",
    "engine",
    "src",
    "ctsim",
    "bin",
    "Release",
    "net48",
)
DEFAULT_WORK = os.path.join(os.environ.get("TEMP", "."), "ctgedit_sim_soak")
#: the wire recorder of this soak (main installs it)
WIRE = None

#: the local cases: case name -> the local role of its page (tests_corpus/local.json)
USER_CASES = {
    "local": "soak_page",
    "local_away": "soak_away_page",
}
#: (page role, the role of the page its 'away' action shows): the away page of its project
USER_AWAY = (("soak_page", "soak_away_page"),)


def _corpus():
    """tests_corpus/corpus.py (the local roles) of this checkout."""
    d = os.path.join(ROOT, "tests_corpus")
    if d not in sys.path:
        sys.path.append(d)
    import corpus

    return corpus


def _role_page(name: str) -> Optional[Tuple[str, str]]:
    """(project, page stem) of a page role, or None when it is not set or not installed."""
    corpus = _corpus()
    if corpus.missing(name):
        return None
    parts = [p for p in corpus.role_value(name).replace("\\", "/").split("/") if p]
    return parts[0], os.path.splitext(parts[-1])[0]


def _user_case(case: str) -> Dict[str, Tuple[str, str, Dict[str, Any]]]:
    hit = _role_page(USER_CASES[case])
    return {case: (hit[0], hit[1], {})} if hit else {}


#: name -> (project, page, options)
CASES: Dict[str, Tuple[str, str, Dict[str, Any]]] = {
    "mixing": ("ExampleSA", "Mixing_L3", {"sa_workspace": False}),
    "overview": ("ExampleSA", "Overview_L2", {"sa_workspace": False}),
    "alarm": ("ExampleSA", "DefaultAlarm_HD1080", {"sa_workspace": False}),
    "alarm_ws": ("ExampleSA", "DefaultAlarm_HD1080", {"sa_workspace": True}),
    "mixer": ("Example", "Mixer", {}),
    **_user_case("local"),
    **_user_case("local_away"),
}
#: the page the 'away' action shows meanwhile (same project)
AWAY = {"ExampleSA": "Overview_L2", "Example": "Genies"}
for _page_role, _away_role in USER_AWAY:
    _hit, _away = _role_page(_page_role), _role_page(_away_role)
    if _hit and _away:
        AWAY[_hit[0]] = _away[1]
#: projects whose drawing files are all copied (the SA workspace shows its own pages)
ALL_PAGES = ("ExampleSA", "SA_Include")
IMPLICIT = ("Include", "System")
COPY_EXT = (".dbf", ".ci", ".rdb", ".ini", ".frm", ".pal", ".ndx", ".ctg")


# -- the install: copies of the include chains ---------------------------------------------
def chain_of(project: str) -> List[str]:
    from ctgedit.format import projects as P

    seen: Dict[str, str] = {}

    def walk(n):
        if n.lower() in seen or not os.path.isdir(os.path.join(R2_USER, n)):
            return
        seen[n.lower()] = n
        for i in P.included_project_names(os.path.join(R2_USER, n)):
            walk(i)

    walk(project)
    for n in IMPLICIT:
        walk(n)
    return list(seen.values())


def _write_master(path: str, rows: List[Dict[str, str]]) -> None:
    import datetime
    import struct
    from ctgedit.format.dbf import Dbf

    fields = [("NAME", 64), ("PATH", 200)]
    desc = b"".join(
        n.encode("ascii").ljust(11, b"\0") + b"C" + b"\0" * 4 + bytes([ln]) + b"\0" * 15 for n, ln in fields
    )
    hlen = 32 + len(desc) + 1
    rlen = 1 + sum(ln for _n, ln in fields)
    d = datetime.date.today()
    head = struct.pack("<BBBBIHH20x", 3, d.year - 1900, d.month, d.day, 0, hlen, rlen)
    with open(path, "wb") as fh:
        fh.write(head + desc + b"\x0d" + b"\x1a")
    with Dbf(path) as t:
        t.append([t.encode(r) for r in rows])


def prepare_install(work: str, cases: List[str]) -> str:
    """``work``/inst/User with copies of every project the cases need (made once: a project
    folder that exists is kept). The User folder."""
    user = os.path.join(work, "inst", "User")
    os.makedirs(user, exist_ok=True)
    pages: Dict[str, set] = collections.defaultdict(set)
    projects: List[str] = []
    for c in cases:
        proj, page, _o = CASES[c]
        pages[proj].add(page.lower())
        if proj in AWAY:
            pages[proj].add(AWAY[proj].lower())
        for p in chain_of(proj):
            if p not in projects:
                projects.append(p)
    rows = []
    for name in projects:
        src = os.path.join(R2_USER, name)
        dst = os.path.join(user, name)
        mark = os.path.join(dst, ".soak_copied")
        want = sorted(pages.get(name, ()))
        have = []
        if os.path.isfile(mark):
            with open(mark, encoding="utf-8") as fh:
                have = json.load(fh)
        if have != want or not os.path.isdir(dst):
            os.makedirs(dst, exist_ok=True)
            for f in os.listdir(src):
                low = f.lower()
                fp = os.path.join(src, f)
                if not os.path.isfile(fp) or not low.endswith(COPY_EXT):
                    continue
                if low.startswith("pg") and low.endswith((".dbf", ".ndx")):
                    continue  # the page tables: the models are exported from the live pages
                if low == "errlog.dbf":
                    continue
                if (
                    low.endswith(".ctg")
                    and name not in ALL_PAGES
                    and os.path.splitext(low)[0] not in pages.get(name, ())
                ):
                    continue
                out = os.path.join(dst, f)
                if not os.path.isfile(out) or os.path.getsize(out) != os.path.getsize(fp):
                    shutil.copyfile(fp, out)
            with open(mark, "w", encoding="utf-8") as fh:
                json.dump(want, fh)
        rows.append({"NAME": name, "PATH": dst})
    _write_master(os.path.join(user, "MASTER.DBF"), rows)
    return user


def live_path(copy_path: str, user: str) -> str:
    """The live drawing file of a copied one (the page models are exported from it)."""
    rel = os.path.relpath(os.path.abspath(copy_path), user)
    return os.path.join(R2_USER, rel)


# -- jitter: monkeypatches of the client (this process only) -------------------------------
class Jitter:
    """The client's timing disturbed at random (``scale`` 0 = untouched)."""

    #: responses whose callback may be held behind later messages (the engine writes events
    #: from its event thread, replies from the request thread: a pageChanged made after the
    #: reply's state can be written first)
    DEFER = (
        "ctsim/pageState",
        "ctsim/windows",
        "ctsim/pageVars",
        "ctsim/params",
        "ctsim/usedTags",
        "ctsim/tags",
        "ctsim/subscribe",
        "ctsim/load",
    )

    def __init__(self, scale: float, seed: int):
        self.scale = scale
        self.rng = random.Random(seed)
        self.lock = threading.Lock()
        self.stats = collections.Counter()
        self._undo: List[Tuple[Any, str, Any]] = []

    def chance(self, p: float) -> bool:
        with self.lock:
            return self.scale > 0 and self.rng.random() < p * min(1.0, self.scale)

    def ms(self, hi: float) -> float:
        with self.lock:
            return self.rng.uniform(0, hi * self.scale) / 1000.0

    def _patch(self, obj, name, new):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, new)

    def install(self) -> None:
        if self.scale <= 0:
            return
        from ctgedit.sim import client as C
        from ctgedit.ui.simulate import ctsim_source as CS
        from ctgedit.ui.simulate import window as W

        jit = self
        deferred: List[list] = []  # [messages left, deadline, fn]
        dlock = threading.Lock()

        def run_due(force_time: bool) -> None:
            now = time.monotonic()
            due = []
            with dlock:
                for d in list(deferred):
                    if d[0] <= 0 or (force_time and now >= d[1]):
                        deferred.remove(d)
                        due.append(d[2])
            for fn in due:
                fn()

        orig_handle = C.Connection._handle

        def _handle(self, raw):
            orig_handle(self, raw)
            with dlock:
                for d in deferred:
                    d[0] -= 1
            run_due(False)

        self._patch(C.Connection, "_handle", _handle)

        orig_expire = C.Connection._expire

        def _expire(self):
            orig_expire(self)
            run_due(True)

        self._patch(C.Connection, "_expire", _expire)

        orig_fire = C.Connection._fire

        def _fire(self, p):
            if p.command in jit.DEFER and self.on_reader_thread() and jit.chance(0.5):
                with jit.lock:
                    n = jit.rng.randint(1, 6)
                jit.stats["deferred_reply"] += 1
                with dlock:
                    deferred.append([n, time.monotonic() + 0.25, lambda: orig_fire(self, p)])
                return
            orig_fire(self, p)

        self._patch(C.Connection, "_fire", _fire)

        orig_listener = CS.EngineSimulation._event_listener

        def _event_listener(self, gen):
            fn = orig_listener(self, gen)

            def slow(name, body):
                if jit.chance(0.15):
                    jit.stats["slow_event"] += 1
                    time.sleep(jit.ms(25))
                fn(name, body)

            return slow

        self._patch(CS.EngineSimulation, "_event_listener", _event_listener)

        orig_open = C.Session.open_page_full

        def open_page_full(self, *a, **kw):
            if jit.chance(0.5):
                jit.stats["late_open_send"] += 1
                time.sleep(jit.ms(80))
            body = orig_open(self, *a, **kw)
            if jit.chance(0.7):
                jit.stats["late_open_wake"] += 1
                time.sleep(jit.ms(150))
            return body

        self._patch(C.Session, "open_page_full", open_page_full)
        if hasattr(CS.EngineSimulation, "_open_request"):
            # (the client's own openPage wait: the same delays)
            orig_req = CS.EngineSimulation._open_request

            def _open_request(self, *a, **kw):
                if jit.chance(0.5):
                    jit.stats["late_open_send"] += 1
                    time.sleep(jit.ms(80))
                out = orig_req(self, *a, **kw)
                if jit.chance(0.7):
                    jit.stats["late_open_wake"] += 1
                    time.sleep(jit.ms(150))
                return out

            self._patch(CS.EngineSimulation, "_open_request", _open_request)

        orig_nav = C.Session.navigate

        def navigate(self, *a, **kw):
            body = orig_nav(self, *a, **kw)
            if jit.chance(0.6):
                jit.stats["late_navigate_wake"] += 1
                time.sleep(jit.ms(150))
            return body

        self._patch(C.Session, "navigate", navigate)

        orig_tick = W.SimulatedPage.tick

        def tick(self):
            if jit.chance(0.1):
                jit.stats["slow_tick"] += 1
                time.sleep(jit.ms(120))
            return orig_tick(self)

        self._patch(W.SimulatedPage, "tick", tick)

    def uninstall(self) -> None:
        while self._undo:
            obj, name, old = self._undo.pop()
            setattr(obj, name, old)


# -- the wire: the order pageChanged events and page-state replies arrive in -----------------
class Wire:
    """Records, on the reader thread, the page-state replies (openPage / pageState: window,
    page, serial) and the pageChanged events (window, page, serial), and the requests sent; a
    pageChanged NEWER than a reply (its serial larger) that arrived after the request went
    and BEFORE the reply is the engine's reorder (its event thread wrote it first)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.log: List[tuple] = []
        self.traced: List[tuple] = []
        self._undo = []

    def install(self) -> None:
        from ctgedit.sim import client as C

        wire = self
        orig_handle = C.Connection._handle

        def _handle(conn, raw):
            try:
                msg = json.loads(raw.decode("utf-8", "replace") or "null")
            except ValueError:
                msg = None
            if isinstance(msg, dict):
                b = msg.get("body") if isinstance(msg.get("body"), dict) else {}
                if msg.get("type") == "event" and msg.get("event") == "ctsim/pageChanged":
                    wire.note(("event", b.get("window"), str(b.get("page") or ""), b.get("serial")))
                    wire.trace_objects("event", b)
                elif msg.get("type") == "response" and msg.get("command") in (
                    "ctsim/pageState",
                    "ctsim/openPage",
                ):
                    st = b.get("state") if msg.get("command") == "ctsim/openPage" else b
                    st = st if isinstance(st, dict) else {}
                    wire.note(
                        (
                            "reply",
                            st.get("window", b.get("window")),
                            str(st.get("page") or b.get("page") or ""),
                            st.get("serial"),
                            msg.get("command"),
                            msg.get("request_seq"),
                        )
                    )
                    wire.trace_objects("reply " + str(msg.get("command")), st)
                elif msg.get("type") == "event" and msg.get("event") == "ctsim/windowsChanged":
                    if wire.trace:
                        wire.traced.append(
                            (
                                time.monotonic(),
                                "windows",
                                [
                                    (w.get("window"), w.get("page"))
                                    for w in b.get("windows") or ()
                                    if isinstance(w, dict)
                                ],
                            )
                        )
            orig_handle(conn, raw)

        self._undo.append((C.Connection, "_handle", orig_handle))
        C.Connection._handle = _handle
        orig_send = C.Connection._send

        def _send(conn, cmd, arguments, timeout):
            p = orig_send(conn, cmd, arguments, timeout)
            if p.command in ("ctsim/pageState", "ctsim/openPage"):
                a = arguments if isinstance(arguments, dict) else {}
                wire.note(("request", a.get("window"), str(a.get("name") or ""), None, p.command, p.seq))
            return p

        self._undo.append((C.Connection, "_send", orig_send))
        C.Connection._send = _send

    #: ANs whose states are logged (--trace): [(time, what, window, serial, an, state)]
    trace: set = set()

    def trace_objects(self, what: str, body: Dict[str, Any]) -> None:
        if not self.trace or not isinstance(body, dict):
            return
        for it in body.get("objects") or ():
            if isinstance(it, dict) and it.get("an") in self.trace:
                with self.lock:
                    self.traced.append(
                        (
                            time.monotonic(),
                            what,
                            body.get("window"),
                            body.get("serial"),
                            it.get("an"),
                            it.get("state"),
                        )
                    )

    def note(self, item) -> None:
        with self.lock:
            self.log.append(item)

    def uninstall(self) -> None:
        while self._undo:
            obj, name, old = self._undo.pop()
            setattr(obj, name, old)

    def take(self) -> List[tuple]:
        with self.lock:
            out, self.log = self.log, []
        return out

    @staticmethod
    def reorders(log: List[tuple]) -> List[Dict[str, Any]]:
        """Replies that came after a newer pageChanged of their window and page (sent after
        the request)."""
        out = []
        sent = {}  # request_seq -> index
        for i, it in enumerate(log):
            if it[0] == "request":
                sent[it[5]] = i
            elif it[0] == "reply" and it[3] is not None:
                start = sent.get(it[5])
                if start is None:
                    continue
                w, page, serial = it[1], it[2].lower(), it[3]
                newer = [
                    e[3]
                    for e in log[start:i]
                    if e[0] == "event"
                    and e[1] == w
                    and e[2].lower() == page
                    and e[3] is not None
                    and e[3] > serial
                ]
                if newer and it[4] == "ctsim/pageState":
                    out.append(
                        {"window": w, "page": it[2], "reply_serial": serial, "newer_events_before": newer[:8]}
                    )
        return out


# -- comparing states ------------------------------------------------------------------------
_CLOCKISH = None


def _strip_clock(text: str) -> str:
    """A wall-clock time / date in a display string (alarm rows: the alarm's ON time, which
    is the real time the simulation started) as '<t>': runs differ by it."""
    global _CLOCKISH
    if _CLOCKISH is None:
        import re

        _CLOCKISH = re.compile(r"\d{1,2}:\d\d:\d\d( ?[AP]M)?|\d\d[-/.]\d\d[-/.]\d{2,4}")
    return _CLOCKISH.sub("<t>", text)


def _norm(v, clock: bool = False):
    if clock and isinstance(v, str):
        return _strip_clock(v)
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (list, tuple)):
        return [_norm(x, clock) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x, clock) for k, x in sorted(v.items())}
    return v


def state_dict(st, clock: bool = False) -> Dict[str, Any]:
    """An ObjectState as {field: value} without the defaults (None = static); ``clock``:
    wall-clock times in strings left out."""
    from ctgedit.sim.state import ObjectState

    if st is None:
        return {}
    base = ObjectState(st.an)
    out = {}
    for f in dataclasses.fields(st):
        if f.name == "an":
            continue
        v = getattr(st, f.name)
        if v != getattr(base, f.name):
            out[f.name] = _norm(v, clock)
    return out


def diff_states(drawn: Dict[int, Any], fresh: Dict[int, Any]) -> Dict[int, Dict[str, Any]]:
    """{AN: {field: [drawn, fresh]}} where they differ."""
    out = {}
    for an in sorted(set(drawn) | set(fresh)):
        a, b = state_dict(drawn.get(an)), state_dict(fresh.get(an))
        if a != b:
            d = {}
            for k in sorted(set(a) | set(b)):
                if a.get(k) != b.get(k):
                    d[k] = [a.get(k), b.get(k)]
            out[an] = d
    return out


# -- one run ---------------------------------------------------------------------------------
class Run:
    def __init__(
        self, args, user: str, loc, case: str, k: int, startup: bool, actions: List[str], rng: random.Random
    ):
        self.args, self.user, self.loc = args, user, loc
        self.case, self.k, self.startup, self.actions, self.rng = case, k, startup, actions, rng
        proj, page, opts = CASES[case]
        self.project, self.page_name, self.opts = proj, page, opts
        self.path = os.path.join(user, proj, page + ".ctg")
        self.result: Dict[str, Any] = {
            "case": case,
            "run": k,
            "startup": startup,
            "actions": actions,
            "errors": [],
        }
        self.events = collections.Counter()
        self.last_event = time.monotonic()
        self.windows: List[Any] = []

    # the Qt loop
    def pump(self, seconds: float, until=None) -> bool:
        from PySide6 import QtWidgets

        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QtWidgets.QApplication.processEvents()
            for w in self.windows:
                try:
                    w.tick()
                except Exception as e:  # noqa: BLE001
                    self.result["errors"].append("tick: %r" % e)
            if until is not None and until():
                return True
            time.sleep(0.01)
        return until() if until is not None else True

    def _listen(self, s) -> None:
        def on(name, body):
            if name.endswith("pageChanged"):
                self.events["pageChanged"] += 1
                self.last_event = time.monotonic()
            elif name.endswith("windowsChanged"):
                self.events["windowsChanged"] += 1
                self.last_event = time.monotonic()

        s.add_listener(on)

    def views(self):
        out = []
        for w in self.windows:
            for v in w.pages():
                if getattr(v, "left", False):
                    continue
                out.append(v)
        return out

    def live(self) -> bool:
        vs = self.views()
        if not vs:
            return False
        for v in vs:
            sim = v.sim
            st = sim.status()
            if not sim.page_active() or getattr(sim, "_held", False) or st.busy:
                return False
        return True

    def settle(self, quiet: float = 1.0, limit: float = 30.0) -> bool:
        """Clock paused: no page / windows event for ``quiet`` s and every view drew what it
        got."""

        def calm():
            if time.monotonic() - self.last_event < quiet:
                return False
            for v in self.views():
                sim = v.sim
                with sim._lock:
                    if sim._changes or sim._full is not None or getattr(sim, "_nav", None):
                        return False
            return self.live()

        return self.pump(limit, calm)

    def snapshot(self, s) -> Dict[str, Any]:
        """Every view's drawn states and the engine's states of its window now."""
        from ctgedit.sim.state import object_states_from_json

        out = {}
        for v in self.views():
            w = v.engine_window()
            if w is None:
                continue
            try:
                body = s.request("pageState", {"window": int(w)}, timeout=60)
            except Exception as e:  # noqa: BLE001
                self.result["errors"].append("pageState %s: %r" % (w, e))
                continue
            fresh = object_states_from_json(body.get("objects") or ())
            drawn = v.scene.sim_states()
            key = "%s@%d" % ((body.get("page") or v.title()), w)
            out[key] = {
                "view": v,
                "window": w,
                "page": body.get("page"),
                "fresh": fresh,
                "drawn": drawn,
                "serial": body.get("serial"),
                "pageVars": body.get("pageVars"),
            }
        return out

    def do_action(self, act: str, s) -> None:
        from ctgedit.ui.simulate.ctsim_source import EngineSimulation
        from ctgedit.ui.simulate.window import SimulateWindow

        tab = self.windows[0].current()
        if act == "tab2":
            win2 = SimulateWindow()
            self.windows.append(win2)
            sim2 = EngineSimulation(hub=self.hub, location=self.loc)
            win2.open_page(self.path, sim=sim2)
        elif act == "reload" and tab is not None:
            tab.reload_page()
        elif act == "away" and tab is not None:
            w = tab.engine_window()
            other = AWAY.get(self.project)
            if w is None or not other:
                return
            try:
                s.navigate(other, window=w, how="pageDisplay", timeout=120)
                self.pump(self.rng.uniform(0.3, 2.0))
                s.navigate(self.page_name, window=w, how="pageDisplay", timeout=120)
            except Exception as e:  # noqa: BLE001
                self.result["errors"].append("away: %r" % e)

    def go(self) -> Dict[str, Any]:
        from ctgedit.sim import client as C
        from ctgedit.ui.simulate import options
        from ctgedit.ui.simulate.ctsim_source import EngineSimulation
        from ctgedit.ui.simulate.window import SimulateWindow

        r = self.result
        options.put("run_startup", self.startup)
        options.put("sa_workspace", bool(self.opts.get("sa_workspace", True)))
        options.put("autorun", True)
        options.put("reload_page", False)
        options.put("reload_cicode", False)
        self.hub = C.Hub(linger=0)
        t0 = time.monotonic()
        try:
            win = SimulateWindow()
            self.windows.append(win)
            sim = EngineSimulation(hub=self.hub, location=self.loc)
            win.open_page(self.path, sim=sim)
            if not self.pump(180, lambda: sim.session is not None or not sim.status().busy):
                r["errors"].append("no session: %s" % sim.status().text)
                return r
            s = sim.session
            if s is None:
                r["errors"].append("no session: %s" % sim.status().text)
                return r
            self._listen(s)
            ok = self.pump(180, self.live)
            r["open_s"] = round(time.monotonic() - t0, 2)
            if not ok:
                r["errors"].append("not live: %s" % "; ".join(v.sim.status().text for v in self.views()))
                return r
            # the clock runs a while, the actions happen at random times meanwhile
            acts = list(self.actions)
            self.rng.shuffle(acts)
            for act in acts:
                self.pump(self.rng.uniform(0.0, 1.5 * self.args.run_s))
                self.do_action(act, s)
                if not self.pump(120, self.live):
                    r["errors"].append("not live after %s" % act)
            self.pump(self.rng.uniform(0.5, 1.0) * self.args.run_s)
            # 1: drawn vs fresh, clock paused
            first = self._pause_and_compare(s, "a")
            # dynamic: what changes while the clock runs on (left out of 2)
            sim.clock("run")
            self.pump(1.0)
            second = self._pause_and_compare(s, "b")
            dyn = set()
            for key, a in first.items():
                b = second.get(key)
                if b is None:
                    continue
                for an in set(a["fresh"]) | set(b["fresh"]):
                    if state_dict(a["fresh"].get(an)) != state_dict(b["fresh"].get(an)):
                        dyn.add((key, an))
            r["dynamic"] = sorted("%s/%d" % k for k in dyn)
            r["settled"] = {
                key: {str(an): state_dict(st, clock=True) for an, st in a["fresh"].items()}
                for key, a in second.items()
            }
            # page variables by name (the order of first use is reported apart)
            r["pageVars"] = {
                key: sorted("%s=%s" % (d.get("name"), d.get("value")) for d in a.get("pageVars") or ())
                for key, a in second.items()
            }
            r["pageVarOrder"] = {
                key: [d.get("name") for d in a.get("pageVars") or ()] for key, a in second.items()
            }
            try:
                body = s.request("ctsim/params", {}, timeout=30)
                r["params"] = sorted(
                    "[%s]%s=%s" % (d.get("section"), d.get("name"), d.get("value"))
                    for d in body.get("params") or ()
                    if isinstance(d, dict)
                )
            except Exception as e:  # noqa: BLE001
                r["errors"].append("params: %r" % e)
            tags = {}
            for key, a in second.items():
                try:
                    body = s.request("tags", {"window": int(a["window"])}, timeout=60)
                    tags[key] = sorted(
                        "%s:%s" % (t.get("name"), t.get("defined", True)) for t in body.get("tags") or ()
                    )
                except Exception as e:  # noqa: BLE001
                    r["errors"].append("tags: %r" % e)
            r["tags"] = tags
        except Exception:  # noqa: BLE001
            import traceback

            r["errors"].append("run: %s" % traceback.format_exc())
        finally:
            r["events"] = dict(self.events)
            if WIRE is not None:
                r["reorders"] = Wire.reorders(WIRE.take())
                if WIRE.trace:
                    with WIRE.lock:
                        r["trace"], WIRE.traced = WIRE.traced, []
            r["total_s"] = round(time.monotonic() - t0, 2)
            for w in self.windows:
                try:
                    w.shut_down()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    w.deleteLater()
                except Exception:  # noqa: BLE001
                    pass
            self.pump(0.3)
            try:
                self.hub.shutdown()
            except Exception:  # noqa: BLE001
                pass
            # the windows go now (deleteLater waits for an event loop this tool never runs)
            from PySide6 import QtCore, QtWidgets

            self.windows = []
            QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            try:
                from ctgedit.ui.simulate import window as W

                W._MODEL_CACHE.clear()
            except Exception:  # noqa: BLE001
                pass
            import gc

            gc.collect()
            r["rss_mb"] = rss_mb()
        return r

    def _pause_and_compare(self, s, tag: str) -> Dict[str, Any]:
        for w in self.windows:
            for p in w.tab_pages():
                if getattr(p.sim, "_own", False):
                    p.sim.clock("pause")
        if not self.settle():
            self.result["errors"].append("did not settle (%s)" % tag)
        snap = self.snapshot(s)
        diffs = {}
        for key, a in snap.items():
            d = diff_states(a["drawn"], a["fresh"])
            if d:
                v = a["view"]
                diffs[key] = {str(an): {"diff": fd, "expr": v.expressions_of(an)[:4]} for an, fd in d.items()}
        if diffs:
            # one page scan (the clock steps once): a difference the scan's pageChanged
            # mends was a state the engine had not sent yet ('unsent'); one that stays is a
            # state the client lost ('lost': the engine sends an object only when it changes)
            try:
                s.request("clock", {"mode": "step"}, timeout=30)
            except Exception as e:  # noqa: BLE001
                self.result["errors"].append("step: %r" % e)
            self.settle()
            again = self.snapshot(s)
            for key, objs in diffs.items():
                b = again.get(key)
                for an, d in objs.items():
                    still = b is not None and state_dict(b["drawn"].get(int(an))) != state_dict(
                        b["fresh"].get(int(an))
                    )
                    d["kind"] = "lost" if still else "unsent"
        self.result.setdefault("drawn_vs_fresh", {})[tag] = diffs
        self.result.setdefault("objects", {})[tag] = {k: len(a["fresh"]) for k, a in snap.items()}
        return snap


# -- the soak --------------------------------------------------------------------------------
def rss_mb() -> float:
    """This process's working set (MB), 0 when it cannot be read."""
    import ctypes

    class PMC(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.c_void_p(k32.GetCurrentProcess()), ctypes.byref(pmc), pmc.cb
        )
        return round(pmc.WorkingSetSize / 2**20, 1) if ok else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def write_report(path: str, a, jit, runs, t0) -> Dict[str, Any]:
    rep = {
        "args": vars(a),
        "jitter": dict(jit.stats),
        "seconds": round(time.monotonic() - t0),
        "summary": summarise(runs),
        "runs": runs,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=str)
    return rep


def summarise(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per (case, startup): drawn-vs-fresh differences (AN -> count), the settled states
    that differ between runs (non-dynamic), params / page vars / tags that differ."""
    out: Dict[str, Any] = {}
    groups = collections.defaultdict(list)
    for r in runs:
        groups["%s/startup=%s" % (r["case"], "on" if r["startup"] else "off")].append(r)
    for g, rs in groups.items():
        dvf = collections.Counter()
        fields = collections.defaultdict(collections.Counter)
        kinds = collections.defaultdict(collections.Counter)
        expr = {}
        runs_with = 0
        for r in rs:
            had = False
            for tag, views in (r.get("drawn_vs_fresh") or {}).items():
                for key, objs in views.items():
                    for an, d in objs.items():
                        k = "%s/%s" % (key.split("@")[0], an)
                        dvf[k] += 1
                        had = True
                        for f in d["diff"]:
                            fields[k][f] += 1
                        kinds[k][d.get("kind", "?")] += 1
                        expr[k] = d["expr"]
            runs_with += had
        dyn = set()
        for r in rs:
            dyn.update(
                "%s/%s" % (x.split("/")[0].split("@")[0], x.rsplit("/", 1)[1]) for x in r.get("dynamic") or ()
            )
        # settled states between runs (by page name, not engine window)
        per = collections.defaultdict(lambda: collections.defaultdict(list))
        for r in rs:
            for key, objs in (r.get("settled") or {}).items():
                page = key.split("@")[0]
                for an, st in objs.items():
                    per[page][an].append(json.dumps(st, sort_keys=True))
        between = {}
        for page, objs in per.items():
            n = max(len(v) for v in objs.values()) if objs else 0
            for an, vals in objs.items():
                k = "%s/%s" % (page, an)
                if k in dyn:
                    continue
                c = collections.Counter(vals)
                if len(c) > 1 or len(vals) != n:
                    between[k] = {"values": dict(c.most_common(4)), "seen": len(vals), "of": n}

        def spread(key):
            c = collections.Counter(json.dumps(r.get(key), sort_keys=True) for r in rs)
            return {"distinct": len(c), "counts": list(c.values())}

        out[g] = {
            "runs": len(rs),
            "runs_with_drawn_diff": runs_with,
            "drawn_vs_fresh": {
                k: {"count": n, "fields": dict(fields[k]), "kinds": dict(kinds[k]), "expr": expr.get(k)}
                for k, n in dvf.most_common()
            },
            "lost": sum(c.get("lost", 0) for c in kinds.values()),
            "unsent": sum(c.get("unsent", 0) for c in kinds.values()),
            "between_runs": between,
            "dynamic": len(dyn),
            "params": spread("params"),
            "pageVars": spread("pageVars"),
            "pageVarOrder": spread("pageVarOrder"),
            "tags": spread("tags"),
            "errors": [e for r in rs for e in r.get("errors") or ()][:20],
            "reorders": sum(len(r.get("reorders") or ()) for r in rs),
            "open_s": [r.get("open_s") for r in rs],
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jitter", type=float, default=1.0)
    ap.add_argument("--no-jitter", action="store_true")
    ap.add_argument("--startup", default="both", choices=("on", "off", "both"))
    ap.add_argument("--actions", default="tab2,reload,away")
    ap.add_argument("--run-s", type=float, default=2.0, help="seconds the clock runs per run")
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--engine", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--trace", default="", help="ANs whose states are logged (comma list)")
    ap.add_argument("--max-mb", type=int, default=1800, help="stop when this process uses more (MB)")
    a = ap.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    os.makedirs(a.work, exist_ok=True)
    os.environ["CTG_SETTINGS_INI"] = os.path.join(a.work, "settings.ini")
    os.environ["CTG_DATA_DIR"] = os.path.join(a.work, "data")
    os.environ["CTG_NO_CTF_PRECHECK"] = "1"
    os.environ["CTG_NO_DISPLAY_CHECK"] = "1"
    os.environ.pop("CTG_NO_SIM_ENGINE", None)
    from ctgedit.sim import client as C

    os.environ[C.SERVERS_ENV] = os.path.join(a.work, "servers")
    cases = [c for c in a.cases.split(",") if c]
    for c in cases:
        if c in USER_CASES and c not in CASES:
            ap.error("case %s needs the role %s: %s" % (c, USER_CASES[c], _corpus().missing(USER_CASES[c])))
        if c not in CASES:
            ap.error("unknown case %s (%s)" % (c, ", ".join(CASES)))
    # the engine: a private copy (the checkout's build may be rebuilt meanwhile)
    src = a.engine or os.environ.get(C.PATH_ENV) or os.path.join(MAIN_BUILD, C.EXE_NAME)
    src_dir = os.path.dirname(src) if src.lower().endswith(".exe") else src
    eng = os.path.join(a.work, "engine")
    os.makedirs(eng, exist_ok=True)
    for f in (C.EXE_NAME, C.DLL_NAME, C.EXE_NAME + ".config"):
        if os.path.isfile(os.path.join(src_dir, f)):
            shutil.copyfile(os.path.join(src_dir, f), os.path.join(eng, f))
    loc = C.EngineLocation(os.path.join(eng, C.EXE_NAME), "soak")
    user = prepare_install(a.work, cases)
    projects = set()
    for c in cases:
        projects.update(chain_of(CASES[c][0]))
    os.environ["CTG_LIB_PATH"] = os.pathsep.join(os.path.join(R2_USER, p) for p in projects)

    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["soak"])  # noqa: F841
    import ctgedit

    print("ctgedit: %s" % os.path.dirname(os.path.abspath(ctgedit.__file__)), flush=True)
    from ctgedit.infra import gcguard

    try:
        gcguard.install()
        gcguard.install_timer()
    except Exception:  # noqa: BLE001
        pass
    from ctgedit.ui.simulate import window as W

    # the models from the live drawing files (read only), the window's paths are the copies'
    orig_pm = W.page_model

    def page_model(page_path, doc=None):
        lp = live_path(page_path, user) if page_path else page_path
        return orig_pm(lp if lp and os.path.isfile(lp) else page_path, doc)

    W.page_model = page_model
    W._MODEL_CACHE.clear()

    global WIRE
    WIRE = Wire()
    WIRE.trace = {int(x) for x in a.trace.split(",") if x}
    WIRE.install()
    jit = Jitter(0.0 if a.no_jitter else a.jitter, a.seed)
    jit.install()
    rng = random.Random(a.seed)
    startups = {"on": [True], "off": [False], "both": [True, False]}[a.startup]
    acts_all = [x for x in a.actions.split(",") if x]
    runs = []
    t0 = time.monotonic()
    out = a.out or os.path.join(a.work, "report.json")
    try:
        for c in cases:
            for k in range(a.runs):
                for st in startups:
                    acts = [x for x in acts_all if rng.random() < 0.5]
                    run = Run(a, user, loc, c, k, st, acts, random.Random(rng.random()))
                    res = run.go()
                    runs.append(res)
                    write_report(out, a, jit, runs, t0)
                    nd = sum(len(o) for t in (res.get("drawn_vs_fresh") or {}).values() for o in t.values())
                    print(
                        "%-9s run %2d startup=%-3s %-18s open %5ss drawn-vs-fresh %4d  "
                        "reorders %d  rss %s MB  %s"
                        % (
                            c,
                            k,
                            "on" if st else "off",
                            ",".join(acts) or "-",
                            res.get("open_s"),
                            nd,
                            len(res.get("reorders") or ()),
                            res.get("rss_mb"),
                            "; ".join(res.get("errors") or ())[:150],
                        ),
                        flush=True,
                    )
                    if (res.get("rss_mb") or 0) > a.max_mb:
                        print("stopped: %s MB in use (--max-mb %d)" % (res["rss_mb"], a.max_mb))
                        raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    finally:
        jit.uninstall()
        WIRE.uninstall()
    rep = write_report(out, a, jit, runs, t0)
    print("\nreport: %s (%d s)" % (out, rep["seconds"]))
    for g, s in rep["summary"].items():
        print(
            "%-28s runs %2d  with drawn diff %2d  objects drawn != fresh %4d (lost %d, "
            "unsent %d)  between runs %4d  params %s  pageVars %s  tags %s  engine reorders %d"
            % (
                g,
                s["runs"],
                s["runs_with_drawn_diff"],
                len(s["drawn_vs_fresh"]),
                s["lost"],
                s["unsent"],
                len(s["between_runs"]),
                s["params"]["distinct"],
                s["pageVars"]["distinct"],
                s["tags"]["distinct"],
                s["reorders"],
            )
        )
    bad = any(s["drawn_vs_fresh"] for s in rep["summary"].values())
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
