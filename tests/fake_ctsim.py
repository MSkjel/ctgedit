"""A fake ctsim server for the client tests."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from ctgedit.infra import paths  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402

PIPE_ACCESS_DUPLEX = 0x3
FILE_FLAG_OVERLAPPED = 0x40000000
PIPE_UNLIMITED_INSTANCES = 255

#: the page's own tags (``tags {page}``)
PAGE_TAGS = [
    {
        "name": "Tank1.Level",
        "type": "REAL",
        "value": 12.5,
        "quality": "Good",
        "engZero": 0,
        "engFull": 200,
        "units": "%",
        "equipment": "Tank1",
        "item": "Level",
        "pageReads": True,
    },
    {
        "name": "Pump1.Run",
        "type": "DIGITAL",
        "value": 0,
        "quality": "Good",
        "equipment": "Pump1",
        "item": "Run",
        "pageReads": True,
    },
    {"name": "Message", "type": "STRING", "value": "hello", "quality": 192, "pageReads": True},
]


def make_tags(n: int):
    tags = [dict(t) for t in PAGE_TAGS]
    for k in range(n):
        tags.append(
            {
                "name": "Tag%04d" % k,
                "type": "INT",
                "value": k,
                "quality": "Good",
                "comment": "Generated tag %d" % k,
                "engZero": 0,
                "engFull": 1000,
            }
        )
    return tags


class _Conn:
    def __init__(self, server: "FakeServer", stream, name: str):
        self.server = server
        self.stream = stream
        self.name = name
        self.seq = 0
        self.lock = threading.Lock()
        self.subscribed = set()
        self.hello = None
        self.closed = False

    def send(self, msg: dict) -> None:
        with self.lock:
            if self.closed:
                return
            self.seq += 1
            msg = dict(msg)
            msg["seq"] = self.seq
            try:
                self.stream.write(C.encode(msg))
            except Exception:  # noqa: BLE001 -- the client left
                self.closed = True

    def respond(self, req: dict, body=None, ok: bool = True, message: str = "") -> None:
        msg = {
            "type": "response",
            "request_seq": req.get("seq", 0),
            "success": ok,
            "command": req.get("command", ""),
        }
        if body is not None:
            msg["body"] = body
        if message:
            msg["message"] = message
        self.send(msg)

    def event(self, name: str, body: dict) -> None:
        self.send({"type": "event", "event": name, "body": body})

    def run(self) -> None:
        parser = C.FrameParser()
        try:
            while not self.server.stopping and not self.closed:
                data = self.stream.read(timeout=0.2)
                if data is None:
                    continue
                if not data:
                    break
                for raw in parser.feed(data):
                    msg = json.loads(raw.decode("utf-8"))
                    self.server.handle(self, msg)
        except Exception:  # noqa: BLE001 -- a broken client ends its thread only
            import traceback

            traceback.print_exc()
        self.closed = True
        try:
            self.stream.close()
        except Exception:  # noqa: BLE001
            pass
        self.server.dropped(self)


class FakeServer:
    """One fake simulation (one project): tags, a clock, windows, clients."""

    def __init__(
        self,
        project: str,
        folder: str,
        pages: bool = False,
        list_requests: bool = False,
        tags: int = 1234,
        sid: str = "",
        pid: int = 0,
        started=None,
        page_objects=None,
        scan_s: float = 0.25,
        exit_on_crash: bool = False,
        transport: str = "pipe",
        capabilities: bool = True,
        undefined_tags=None,
        window_refs=None,
        associations=None,
        window_tags: bool = True,
        root: str = "",
        roots=None,
        root_reason: str = "",
        navigate: bool = False,
        start_page: str = "Master",
    ):
        self.project = os.path.abspath(project)
        # the compiled project it runs as (engine/PROTOCOL.md 'The ROOT'): its name
        self.root = root or os.path.basename(self.project)
        self.roots = list(roots) if roots is not None else [self.root]
        self.root_reason = root_reason or (
            "self" if self.root.lower() == os.path.basename(self.project).lower() else "only"
        )
        self.folder = folder
        self.pages = pages
        self.list_requests = list_requests
        self.id = sid or "fake" + uuid.uuid4().hex[:12]
        self.pipe = "\\\\.\\pipe\\ctsim." + self.id
        self.pid = pid or os.getpid()
        self.started = started if started is not None else paths.process_started(self.pid)
        self.tags = make_tags(tags)
        self.by_name = {t["name"].lower(): t for t in self.tags}
        self.conns = []
        # request records, made up front: a test waiting on one never meets a missing attribute
        self.navigated: list = []
        self.option_requests: list = []
        self.seed_sets: list = []
        self.reloads: list = []
        self.opened: list = []
        self.models: list = []
        self.answers: list = []
        self.images: list = []
        self.alarm_queries: list = []
        self.alarm_sets: list = []
        self.object_sets: list = []
        self.equipment_sets: list = []
        self.lock = threading.RLock()
        self.stopping = False
        self.running = False
        self.speed = 1.0
        self.scan = 0
        self.time = 0.0
        self.scan_s = scan_s
        self.windows = {}
        self.parents = {}  # window -> its parent window (windows)
        self.paths = {}  # page name (lower) -> its drawing file (windows)
        # load {startPage} and ctsim/navigate (engine/PROTOCOL.md): the
        # startup page load shows in window 0, a navigated page's pane window (its parent 0)
        self.navigate_on = navigate
        self.start_page = start_page
        self.inputs = []
        self.requests = []
        self.scenarios = []
        self.loaded = []
        self.exit_on_crash = exit_on_crash
        self.shutdowns = 0
        self.capabilities = capabilities
        self.window_tags = window_tags
        self.undefined = [(str(n), str(t)) for n, t in (undefined_tags or ())]
        self.simulated = {}  # lower name -> the simulated tag
        self.simulate_undefined = False
        self.window_refs = {int(k): list(v) for k, v in (window_refs or {}).items()}
        self.associations = {int(k): dict(v) for k, v in (associations or {}).items()}
        self.transport = transport
        self.address = self.pipe
        self._sock = None
        # page variables per window: name -> (type 'int' | 'string', value)
        self.page_vars: dict = {}
        self.page_objects = (
            page_objects
            if page_objects is not None
            else {
                11: {"move": [0, 0], "fillColour": 0x00FF00},
                12: {"text": "12.5"},
            }
        )
        self.discovery_path = os.path.join(folder, self.id + ".json")
        self._threads = []
        self._ticker = None

    # -- life -------------------------------------------------------------------------
    def start(self) -> "FakeServer":
        loop = self._accept_loop if self.transport == "pipe" else self._accept_tcp
        t = threading.Thread(target=loop, daemon=True, name="fake-ctsim-accept")
        self._threads.append(t)
        self._ready = threading.Event()
        t.start()
        self._ready.wait(5)
        self.write_discovery()
        self._ticker = threading.Thread(target=self._tick_loop, daemon=True, name="fake-ctsim-clock")
        self._ticker.start()
        return self

    def write_discovery(self, **extra) -> str:
        os.makedirs(self.folder, exist_ok=True)
        # the engine's own shape (Host/Discovery.cs ServerEntry.ToJson, 'ctsim-server/0')
        from datetime import datetime, timezone

        iso = ""
        if isinstance(self.started, int) and self.started > 0:
            unix = (self.started - 116444736000000000) / 1e7
            iso = datetime.fromtimestamp(unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        name = os.path.basename(self.project)
        data = {
            "format": "ctsim-server/0",
            "id": self.id,
            "transport": self.transport,
            "pipe": self.pipe if self.transport == "pipe" else "",
            "address": self.pipe if self.transport == "pipe" else self.address,
            "pid": self.pid,
            "processStart": self.started,
            "processStartIso": iso,
            "mode": "serve",
            "project": self.project,
            "projectName": name,
            "root": self.root,
            "rootReason": self.root_reason,
            "roots": list(self.roots),
            "user": os.path.dirname(self.project),
            "install": "",
            "version": "0.0-fake",
            "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        }
        data.update(extra)
        with open(self.discovery_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return self.discovery_path

    def stop(self) -> None:
        self.stopping = True
        with self.lock:
            conns = list(self.conns)
        for c in conns:
            c.closed = True
            try:
                c.stream.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            os.remove(self.discovery_path)
        except OSError:
            pass
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(2)

    def crash(self) -> None:
        """The server dies: every connection drops without an answer."""
        if self.exit_on_crash:
            os._exit(3)
        self.stop()

    def _accept_loop(self) -> None:
        import _winapi

        first = True
        while not self.stopping:
            try:
                h = _winapi.CreateNamedPipe(
                    self.pipe,
                    PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
                    0,
                    PIPE_UNLIMITED_INSTANCES,
                    65536,
                    65536,
                    0,
                    _winapi.NULL,
                )
            except OSError:
                time.sleep(0.05)
                continue
            if first:
                first = False
                self._ready.set()
            ov = _winapi.ConnectNamedPipe(h, overlapped=True)
            connected = False
            while not self.stopping:
                res = _winapi.WaitForMultipleObjects([ov.event], False, 200)
                if res == 0:
                    connected = True
                    break
            if not connected:
                try:
                    ov.cancel()
                    ov.GetOverlappedResult(True)
                except OSError:
                    pass
                _winapi.CloseHandle(h)
                break
            try:
                ov.GetOverlappedResult(True)
            except OSError:
                _winapi.CloseHandle(h)
                continue
            conn = _Conn(self, C.PipeStream(h, self.pipe), "client%d" % len(self.conns))
            with self.lock:
                self.conns.append(conn)
            t = threading.Thread(target=conn.run, daemon=True, name="fake-ctsim-" + conn.name)
            self._threads.append(t)
            t.start()

    def _accept_tcp(self) -> None:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(8)
        s.settimeout(0.2)
        self._sock = s
        self.address = "127.0.0.1:%d" % s.getsockname()[1]
        self._ready.set()
        while not self.stopping:
            try:
                c, _addr = s.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn = _Conn(self, C.SocketStream(c, self.address), "client%d" % len(self.conns))
            with self.lock:
                self.conns.append(conn)
            t = threading.Thread(target=conn.run, daemon=True, name="fake-ctsim-" + conn.name)
            self._threads.append(t)
            t.start()

    def dropped(self, conn: _Conn) -> None:
        with self.lock:
            if conn in self.conns:
                self.conns.remove(conn)

    def broadcast(self, name: str, body: dict) -> None:
        with self.lock:
            conns = list(self.conns)
        for c in conns:
            c.event(name, body)

    # -- the clock ---------------------------------------------------------------------
    def _tick_loop(self) -> None:
        last = time.monotonic()
        while not self.stopping:
            time.sleep(0.02)
            now = time.monotonic()
            if self.running and (now - last) * self.speed >= self.scan_s:
                last = now
                self.step()
            elif not self.running:
                last = now

    def step(self, seconds: float = 0.0) -> None:
        with self.lock:
            self.scan += 1
            self.time = round(self.time + (seconds or self.scan_s), 6)
            level = self.by_name["tank1.level"]
            level["value"] = round((level["value"] + 1.5) % 200, 3)
            windows = list(self.windows.items())
        self._tag_changed(level)
        for w, page in windows:
            self.broadcast(
                "ctsim/pageChanged",
                dict(
                    {
                        "window": w,
                        "page": page,
                        "t": self.time,
                        "flashOn": self.scan % 2 == 0,
                        "objects": [{"an": 11, "state": {"move": [self.scan, 0]}}],
                    },
                    **self._drawn_part(w),
                ),
            )

    def clock_body(self) -> dict:
        """The engine's ClockState (Sim/Simulation.cs)."""
        return {
            "mode": "run" if self.running else "paused",
            "speed": self.speed,
            "ms": int(round(self.time * 1000)),
            "t": self.time,
            "time": "2026-10-01T12:00:00.000Z",
            "start": "2026-10-01T12:00:00.000Z",
            "stepSize": self.scan_s,
        }

    @staticmethod
    def _update(tag: dict) -> dict:
        """The engine's TagUpdate."""
        return {
            "name": tag["name"],
            "value": tag["value"],
            "quality": str(tag["quality"]).lower() if isinstance(tag["quality"], str) else "good",
        }

    def _tag_changed(self, tag: dict) -> None:
        body = {"t": self.time, "time": "", "tags": [self._update(tag)]}
        with self.lock:
            conns = [c for c in self.conns if tag["name"].lower() in c.subscribed]
        for c in conns:
            c.event("ctsim/tagsChanged", body)

    # -- requests (Host/SimServer.cs) ----------------------------------------------------------
    KNOWN = (
        "hello",
        "load",
        "tags",
        "subscribe",
        "unsubscribe",
        "setTag",
        "call",
        "clock",
        "scenario",
        "tasks",
        "shutdown",
        "openPage",
        "pageState",
        "input",
        "windows",
        "closePage",
        "options",
        "pageVars",
        "setPageVar",
        "user",
        "users",
        "lockTag",
        "setTagProperty",
        "profile",
        "params",
        "setParam",
        "usedTags",
    ) + (
        # the viewer's part (sim/client): below
        "pageModel",
        "answer",
        "windowImage",
        "alarms",
        "equipment",
        "setAlarm",
        "setEquipment",
        # ActiveX controls, drawn buttons, alarm list layout, trend history
        "setObjectProperty",
        "navigate",
        "reload",
    )
    NOT_IMPLEMENTED_PAGES = "not implemented yet: the page animator is a later package"

    def requests_known(self):
        return [
            "ctsim/" + n
            for n in self.KNOWN
            if self.pages or n not in ("openPage", "pageState", "input", "windows", "closePage")
        ]

    def handle(self, conn: _Conn, msg: dict) -> None:
        if msg.get("type") != "request":
            return
        cmd = msg.get("command", "")
        args = msg.get("arguments") or {}
        with self.lock:
            self.requests.append((conn.name, cmd, args))
            record = getattr(self, "record_path", "")
            if record:
                # (--record: a fake run as the engine process writes the requests it got, in
                # order, with what window 0 showed and whether the startup code had run)
                with open(record, "a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "cmd": cmd,
                                "args": {k: v for k, v in args.items() if k != "model"},
                                "window0": self.windows.get(0, ""),
                                "startupRan": bool(getattr(self, "startup_ran", False)),
                            }
                        )
                        + "\n"
                    )
        short = cmd[6:] if cmd.startswith("ctsim/") else cmd
        if short in ("openPage", "pageState", "input", "windows", "closePage") and not self.pages:
            conn.respond(msg, {"notImplemented": True}, ok=False, message=self.NOT_IMPLEMENTED_PAGES)
            return
        if not cmd.startswith("ctsim/") or short not in self.KNOWN:
            conn.respond(msg, ok=False, message="unknown request '%s'" % cmd)
            return
        getattr(self, "_r_" + short)(conn, msg, args)

    def _root_refused(self, conn, msg, args) -> bool:
        """A {root} that is not this server's fails, like the engine's RootMatches."""
        want = str(args.get("root") or "").strip()
        if want and want.lower() != self.root.lower():
            conn.respond(
                msg,
                None,
                ok=False,
                message="this server runs %s as part of %s: "
                "start a server with --root %s" % (os.path.basename(self.project), self.root, want),
            )
            return True
        return False

    def _r_hello(self, conn, msg, args):
        if self._root_refused(conn, msg, args):
            return
        conn.hello = args
        body = {
            "project": self.project,
            "projectName": os.path.basename(self.project),
            "root": self.root,
            "rootReason": self.root_reason,
            "roots": list(self.roots),
            "server": "ctsim",
            "version": "0.0-fake",
            "protocol": "ctsim/0",
            "id": self.id,
            "pid": self.pid,
            "mode": "serve",
            "clock": self.clock_body(),
            "debugger": False,
            "clients": len(self.conns),
            # every project the simulation reads (engine/PROTOCOL.md ctsim/hello)
            "unit": [{"name": os.path.basename(self.project), "dir": self.project}],
        }
        if self.capabilities:
            body["capabilities"] = {
                "pages": self.pages,
                "compile": False,
                "scenarios": True,
                "debug": False,
                "simulateUndefined": True,
                "windowTags": self.window_tags,
                "alarms": getattr(self, "alarms_on", True),
                "controls": getattr(self, "controls_on", True),
                "drawnButtons": True,
                "alarmListLayout": True,
                "trendHistory": getattr(self, "trend_history_on", True),
                "navigate": self.navigate_on,
                # PAGE serials grow per window, across page displays
                "windowSerials": True,
                # tags / alarms / equipment {regex}
                "regexFilter": getattr(self, "regex_filter", True),
                # options dll / sql, the permission question
                "outside": getattr(self, "outside_on", True),
            }
            body["options"] = {"simulateUndefined": self.simulate_undefined}
            # the load notes (compile errors, functions not compiled from source ...)
            body["notes"] = list(getattr(self, "load_notes", []) or [])
            body["options"].update(getattr(self, "outside", None) or {})
        # the build identity (engine/PROTOCOL.md 'Versions')
        body.update(
            {
                "protocolRevision": getattr(self, "revision", C.PROTOCOL_REVISION),
                "commit": "fakecommit",
                "buildTime": "2026-10-02T12:00:00Z",
            }
        )
        if self.list_requests:
            body["requests"] = self.requests_known()
        conn.respond(msg, body)

    def _r_load(self, conn, msg, args):
        if self._root_refused(conn, msg, args):
            return
        self.loaded.append(args)
        body = {"project": self.project, "root": self.root}
        if args.get("startPage") and self.navigate_on:
            with self.lock:
                if 0 in self.windows:
                    sp = {"window": 0, "page": self.windows[0], "error": 0, "already": True}
                elif not self.start_page:
                    sp = {
                        "window": 0,
                        "page": "",
                        "error": 261,
                        "errorText": "the project has no startup page",
                        "notes": [],
                    }
                else:
                    self.windows[0] = self.start_page
                    self.parents[0] = -1
                    self._note_path(self.start_page)
                    sp = {
                        "window": 0,
                        "page": self.start_page,
                        "error": 0,
                        "held": False,
                        "source": "[Page] Startup (ParameterGet)",
                        "notes": [],
                    }
            body["startPage"] = sp
        if str(args.get("startup") or "").lower() == "auto":
            body.update(self.startup_body())
        conn.respond(msg, body)
        if args.get("startPage") and self.navigate_on:
            self.broadcast("ctsim/windowsChanged", self.windows_body())

    def _note_path(self, page: str) -> None:
        """A page of the project folder names its drawing file in the windows (as the
        engine's does)."""
        p = os.path.join(self.project, page + ".ctg")
        if os.path.isfile(p):
            self.paths[page.lower()] = p

    def _r_navigate(self, conn, msg, args):
        """The page in a pane window of window 0 (an SA workspace's Workspace_ShowContent),
        else in window 0 (the fake's 'no workspace': ``navigate_pane`` False)."""
        page = str(args.get("page") or "")
        with self.lock:
            self._note_path(page)
            if args.get("how") == "pageDisplay":
                # PageDisplay in the window, once the startup code has settled
                w = int(args.get("window") or 0)
                self.windows[w] = page
                self.parents.setdefault(w, -1)
                method = "pageDisplay"
            elif getattr(self, "navigate_pane", True):
                w = next((k for k, p in self.windows.items() if self.parents.get(k) == 0), None)
                if w is None:
                    w = min(set(range(1, len(self.windows) + 2)) - set(self.windows))
                self.windows[w] = page
                self.parents[w] = 0
                method = "workspace"
            else:
                w = 0
                self.windows[0] = page
                method = "pageDisplay"
            self.navigated.append(dict(args))
            # what the engine says of the startup code it waited for
            startup = (
                {"state": "settled", "waitedMs": 0, "simulatedMs": 1000}
                if getattr(self, "startup_ran", False)
                else {"state": "notAsked"}
            )
        conn.respond(
            msg,
            {
                "page": page,
                "method": method,
                "window": w,
                "shownIn": [w],
                "source": "rdb" if args.get("model") else "",
                "notes": [],
                "startup": startup,
            },
        )
        self.broadcast("ctsim/windowsChanged", self.windows_body())

    def _page_names(self, page):
        if page == "NotCompiled":
            return None
        return [t["name"] for t in self.tags if t.get("pageReads")]

    def _undefined_entry(self, name, guess):
        sim = self.simulated.get(name.lower()) if self.simulate_undefined else None
        if sim is not None:
            return dict(sim)
        return {
            "name": name,
            "defined": False,
            "kind": "undefined",
            "type": guess,
            "typeFrom": "code",
            "guess": guess,
            "value": None,
            "quality": "bad",
            "equipment": "",
            "item": "",
        }

    def _r_tags(self, conn, msg, args):
        names = list(args.get("names") or ())
        page = args.get("page") or ""
        window = args.get("window")
        if names or page or window is not None:
            extra = []
            refs = []
            if window is not None:
                w = int(window)
                if not self.window_tags:
                    conn.respond(msg, ok=False, message="unknown argument window")
                    return
                if w not in self.windows:
                    conn.respond(msg, ok=False, message="window %d shows no page" % w)
                    return
                page = self.windows[w]
                refs = self.window_refs.get(w, [])
            if page:
                pn = self._page_names(page)
                if pn is None:
                    conn.respond(
                        msg, ok=False, message="no compiled page %s (no %s.RDB in the project)" % (page, page)
                    )
                    return
                names += pn
                extra = [self._undefined_entry(n, t) for n, t in self.undefined]
            rows = [self.by_name[n.lower()] for n in names if n.lower() in self.by_name]
            for r in refs:
                t = self.by_name.get(str(r["target"]).lower())
                if t is not None:
                    rows.append(dict(t, via=r["via"], ref=r["ref"]))
            unknown = [n for n in names if n.lower() not in self.by_name]
            unknown += [e["name"] for e in extra if not e.get("simulated")]
            body = {
                "total": len(rows) + len(extra),
                "start": 0,
                "count": len(rows) + len(extra),
                "tags": rows + extra,
                "unknown": unknown,
            }
            if page:
                body.update(
                    {
                        "undefined": len(extra),
                        "page": page,
                        "literals": [],
                        "unresolved": [],
                        "simulateUndefined": self.simulate_undefined,
                    }
                )
                if window is not None:
                    body["window"] = int(window)
            conn.respond(msg, body)
            return
        rows = self.tags
        f = str(args.get("filter") or "").lower()
        if f and args.get("regex"):
            # the filter as a regular expression, without case
            import re as _re

            try:
                rx = _re.compile(str(args.get("filter")), _re.I)
            except _re.error as e:
                conn.respond(msg, None, ok=False, message="not a regular expression: %s" % e)
                return
            rows = [
                t
                for t in rows
                if rx.search(t["name"])
                or (t.get("equipment") and rx.search("%s.%s" % (t.get("equipment", ""), t.get("item", ""))))
            ]
            f = ""
        if f:
            rows = [
                t
                for t in rows
                if f in t["name"].lower()
                or ("%s.%s" % (t.get("equipment", ""), t.get("item", ""))).lower().find(f) >= 0
                and t.get("equipment")
            ]
        start = int(args.get("start", 0))
        count = int(args.get("count", 500))
        conn.respond(
            msg,
            {
                "total": len(rows),
                "start": start,
                "count": len(rows[start : start + count]),
                "tags": rows[start : start + count],
                "all": len(self.tags),
            },
        )

    def _r_subscribe(self, conn, msg, args):
        names = [str(n) for n in args.get("names") or ()]
        if args.get("page"):
            names += self._page_names(args["page"]) or []
        known = {n.lower() for n in names if n.lower() in self.by_name}
        conn.subscribed = known if args.get("replace") else conn.subscribed | known
        conn.respond(
            msg,
            {
                "subscribed": len(conn.subscribed),
                "tags": [self._update(self.by_name[n]) for n in sorted(known)],
                "unknown": [n for n in names if n.lower() not in self.by_name],
            },
        )

    def _r_unsubscribe(self, conn, msg, args):
        conn.subscribed = set()
        conn.respond(msg, {"subscribed": 0})

    def _r_setTag(self, conn, msg, args):
        name = str(args.get("name", ""))
        t = self.by_name.get(name.lower())
        if t is None and self.simulate_undefined and name and "." not in name:
            t = self.simulated.get(name.lower())
            if t is None:
                guess = next((g for n, g in self.undefined if n.lower() == name.lower()), "REAL")
                t = {
                    "name": name,
                    "type": str(args.get("type") or guess),
                    "value": None,
                    "quality": "Good",
                    "defined": False,
                    "kind": "simulated",
                    "simulated": True,
                    "equipment": "",
                    "item": "",
                }
                self.simulated[name.lower()] = t
        if t is None:
            conn.respond(
                msg,
                {"error": 51, "errorText": "Tag not found"},
                ok=False,
                message="setTag %s: error 51 Tag not found" % args.get("name"),
            )
            return
        t["value"] = args.get("value")
        if args.get("quality"):
            t["quality"] = args["quality"]
        conn.respond(msg, {"error": 0, "tag": self._update(t)})
        self._tag_changed(t)

    def _r_call(self, conn, msg, args):
        expr = str(args.get("expr", ""))
        name = expr.split("(", 1)[0].strip()
        if name == "Hang":
            return  # never answers
        if name == "Crash":
            self.crash()
            return
        # the engine's TaskSummary / CallOutcome shapes (Sim/Simulation.cs)
        task = {"task": 7, "name": "call", "function": name, "state": "running", "foreground": False}
        self.broadcast("ctsim/taskStarted", task)
        if name in ("Fail", "OutOfProcess"):
            # a hardware error that halts the task (error 420's title has 'not available')
            code, title = (
                (274, "invalid argument") if name == "Fail" else (420, "Not available outside process")
            )
            outcome = {
                "task": 7,
                "state": "halt",
                "result": "",
                "value": None,
                "type": "",
                "error": code,
                "errorText": title,
                "haltReason": "error %d" % code,
            }
            conn.respond(msg, outcome, ok=False, message="the call halted: error %d %s" % (code, title))
            self.broadcast("ctsim/taskExited", dict(task, state="halt", haltCode=code, haltReason=title))
            return
        if name == "Add":
            a, b = expr[expr.index("(") + 1 : expr.rindex(")")].split(",")
            value = float(a) + float(b)
        elif name == "Beep":
            self.broadcast("ctsim/notSimulated", {"function": "Beep", "count": 1})
            value = 0
        else:
            value = expr
        self.broadcast(
            "ctsim/log",
            {
                "level": "info",
                "text": "%s returned %s" % (name, value),
                "function": name,
                "file": "fake.ci",
                "line": 3,
            },
        )
        conn.respond(
            msg,
            {"task": 7, "state": "stop", "result": str(value), "value": value, "type": "REAL", "error": 0},
        )
        self.broadcast("ctsim/taskExited", dict(task, state="stop"))

    def _r_clock(self, conn, msg, args):
        mode = args.get("mode", "")
        if mode == "run":
            self.running = True
        elif mode == "pause":
            self.running = False
        elif mode == "step":
            self.running = False
            self.step(float(args["value"]) if args.get("value") is not None else 0.0)
        elif mode == "speed":
            self.speed = float(args.get("value") or 1.0)
        elif mode:
            conn.respond(msg, ok=False, message="clock mode must be run, pause, step or speed")
            return
        conn.respond(msg, self.clock_body())
        if mode:
            self.broadcast("ctsim/clockChanged", self.clock_body())

    def _r_scenario(self, conn, msg, args):
        self.scenarios.append(args)
        if args.get("action") == "save":
            # (the state as a scenario)
            tags = {
                n: {"value": t.get("value"), "quality": "good"}
                for n, t in getattr(self, "by_name", {}).items()
                if t.get("value") is not None
            }
            conn.respond(msg, {"scenario": {"format": "ctsim-scenario/0", "tags": tags}, "tags": len(tags)})
            return
        if args.get("action") not in ("load", "run", "stop"):
            conn.respond(msg, ok=False, message="scenario action must be load, run or stop")
            return
        conn.respond(msg, {"loaded": True, "steps": len((args.get("scenario") or {}).get("steps") or ())})

    def _r_profile(self, conn, msg, args):
        """Revision 16: where the time goes (a fixed report)."""
        self.profiling = args.get("action", "get") in ("start", "get") and (
            args.get("action") == "start" or getattr(self, "profiling", False)
        )
        conn.respond(
            msg,
            {
                "running": self.profiling,
                "seconds": 1.0,
                "scans": [
                    {"window": 0, "page": "sim_ui", "count": 4, "ms": 2.0, "avgUs": 500.0, "maxUs": 700.0}
                ],
                "expressions": [
                    {
                        "window": 0,
                        "page": "sim_ui",
                        "an": 11,
                        "class": "fill_level",
                        "count": 4,
                        "ms": 1.5,
                        "avgUs": 375.0,
                        "maxUs": 400.0,
                    }
                ],
                "expressionsTotal": 1,
                "tasks": [{"name": "Background", "count": 10, "ms": 0.5, "avgUs": 50.0, "maxUs": 80.0}],
                "tasksTotal": 1,
            },
        )

    def _r_tasks(self, conn, msg, args):
        conn.respond(msg, {"tasks": []})

    def _r_shutdown(self, conn, msg, args):
        self.shutdowns += 1
        conn.respond(msg, {})
        if self.exit_on_crash:  # the 'engine' process: end like ctsim does
            try:
                os.remove(self.discovery_path)
            except OSError:
                pass
            threading.Timer(0.1, os._exit, (0,)).start()

    def _r_options(self, conn, msg, args):
        # (every option request, as sent: the debugger options among them)
        self.option_requests.append(dict(args))
        changed = False
        if "simulateUndefined" in args:
            on = bool(args["simulateUndefined"])
            changed = on != self.simulate_undefined
            self.simulate_undefined = on
        if "seedTrendHistory" in args:
            on = bool(args["seedTrendHistory"])
            changed = changed or on != getattr(self, "seed_trend_history", False)
            self.seed_trend_history = on
            self.seed_sets.append(on)
        outside = getattr(self, "outside", None) or {
            "dll": "off",
            "sql": "off",
            "dllAllowed": [],
            "sqlAllowed": [],
        }
        for k in ("dll", "sql", "dllAllowed", "sqlAllowed"):
            if k in args and outside.get(k) != args[k]:
                outside = dict(outside, **{k: args[k]})
                changed = True
        self.outside = outside
        body = {
            "simulateUndefined": self.simulate_undefined,
            "seedTrendHistory": getattr(self, "seed_trend_history", False),
        }
        body.update(outside)
        conn.respond(msg, body)
        if changed:
            self.broadcast("ctsim/optionsChanged", body)

    def windows_body(self) -> dict:
        # ``window_info`` = {window: {parent, mode, x, y, w, h, title}}: what WinNewAt /
        # WinNewPinAt gave a window (a pinned pane, a popup); else a top-level window
        info = getattr(self, "window_info", {})
        return {
            "windows": [
                dict(
                    {
                        "window": w,
                        "page": p,
                        "parent": getattr(self, "parents", {}).get(w, -1),
                        "path": self.paths.get(p.lower(), ""),
                        "hasModel": True,
                        "associations": dict(self.associations.get(w, {})),
                    },
                    **info.get(w, {}),
                )
                for w, p in sorted(self.windows.items())
            ]
        }

    def open_window(self, window: int, page: str, path: str = "", **info) -> None:
        """The page's Cicode opened ``window`` (WinNewAt, WinNewPinAt, AssPopUp) showing
        ``page``: windowsChanged goes to every client. ``info`` = parent, mode, x, y, w, h."""
        with self.lock:
            self.windows[int(window)] = page
            if path:
                self.paths[page.lower()] = path
            if not hasattr(self, "window_info"):
                self.window_info = {}
            self.window_info[int(window)] = dict(info)
        self.broadcast("ctsim/windowsChanged", self.windows_body())

    def free_window(self, window: int) -> None:
        """The page's Cicode freed ``window`` (WinFree, its parent's page changed)."""
        with self.lock:
            self.windows.pop(int(window), None)
            getattr(self, "window_info", {}).pop(int(window), None)
        self.broadcast("ctsim/windowsChanged", self.windows_body())

    def set_associations(self, window: int, associations: dict, refs=None) -> None:
        """A window's associations change (a popup displayed in it, AssWinReplace)."""
        with self.lock:
            self.associations[int(window)] = dict(associations)
            if refs is not None:
                self.window_refs[int(window)] = list(refs)
        self.broadcast("ctsim/windowsChanged", self.windows_body())

    def _r_reload(self, conn, msg, args):
        """ctsim/reload {file, text?}: compiled, or not with the errors ``reload_errors``
        names for the file's base name (engine/PROTOCOL.md)."""
        f = str(args.get("file") or "")
        with self.lock:
            self.reloads.append(f)
        errs = (getattr(self, "reload_errors", None) or {}).get(os.path.basename(f).lower())
        body = {
            "accepted": True,
            "file": f,
            "compiled": not errs,
            "incremental": True,
            "ms": 1.5,
            "errors": list(errs or ()),
        }
        if errs:
            body["reason"] = "%d compile error(s)" % len(errs)
        conn.respond(msg, body)
        self.broadcast(
            "ctsim/log",
            {
                "level": "error" if errs else "info",
                "text": "reload %s: %s" % (f, "not compiled" if errs else "compiled"),
            },
        )

    def _r_closePage(self, conn, msg, args):
        w = int(args.get("window", 0))
        with self.lock:
            page = self.windows.pop(w, None)
            getattr(self, "window_info", {}).pop(w, None)
        conn.respond(msg, {"window": w, "closed": page is not None, "page": page or ""})
        if page is not None and w != 0:
            # (like the engine: a freed window changes the windows)
            self.broadcast("ctsim/windowsChanged", self.windows_body())

    def _r_openPage(self, conn, msg, args):
        w = args.get("window")
        if w is None or int(w) not in self.windows:
            w = min(set(range(len(self.windows) + 1)) - set(self.windows))
        w = int(w)
        with self.lock:
            self.opened.append(dict(args))
        self.windows[w] = args.get("name", "")
        # before the answer: pageChanged events of the window's earlier display ([(serial,
        # objects)]: an engine whose events go out on another thread than its answers)
        for serial, objs in getattr(self, "open_events_first", None) or ():
            conn.event(
                "ctsim/pageChanged",
                {"window": w, "page": args.get("name", ""), "serial": serial, "objects": objs},
            )
        early = getattr(self, "open_then_change", None)
        if early is None:
            conn.respond(msg, {"window": w, "page": args.get("name", "")})
            self.run_pending_startup(w)
            return
        # the open's answer with its state, then at once a change of the page's first scans
        # (the runtime's entry code), before the client has taken the answer
        objs = [{"an": an, "state": st} for an, st in self.page_objects.items()]
        serial = getattr(self, "open_serial", None)
        conn.respond(
            msg,
            {
                "window": w,
                "page": args.get("name", ""),
                "state": {
                    "window": w,
                    "page": args.get("name", ""),
                    "objects": objs,
                    "serial": self.scan if serial is None else serial,
                },
            },
        )
        self.scan += 1
        conn.event(
            "ctsim/pageChanged",
            {"window": w, "page": args.get("name", ""), "serial": self.scan, "objects": early},
        )
        self.run_pending_startup(w)

    def _r_pageState(self, conn, msg, args):
        w = int(args.get("window", 0))
        objs = [{"an": an, "state": st} for an, st in self.page_objects.items()]
        newer = getattr(self, "change_before_state", None)
        if newer is not None:
            # the engine's event thread writes a pageChanged made after the reply's state
            # before the request thread writes the reply (once)
            self.change_before_state = None
            serial = self.scan
            self.scan += 1
            body = {
                "window": w,
                "page": self.windows.get(w, ""),
                "serial": self.scan,
                "objects": newer.get("objects") or [],
            }
            if "pageVars" in newer:
                body["pageVars"] = newer["pageVars"]
            conn.event("ctsim/pageChanged", body)
            conn.respond(
                msg,
                {
                    "window": w,
                    "page": self.windows.get(w, ""),
                    "objects": objs,
                    "flashOn": True,
                    "serial": serial,
                    "pageVars": self.vars_json(w),
                },
            )
            return
        conn.respond(
            msg,
            dict(
                {
                    "window": w,
                    "page": self.windows.get(w, ""),
                    "objects": objs,
                    "flashOn": True,
                    "serial": self.scan,
                    "pageVars": self.vars_json(w),
                },
                **self._drawn_part(w),
            ),
        )

    def vars_json(self, window: int) -> list:
        locks = getattr(self, "var_locks", {})
        return [
            dict(
                {"name": n, "type": t, "value": v},
                **({"locked": True} if locks.get((int(window), n)) else {}),
            )
            for n, (t, v) in self.page_vars.get(int(window), {}).items()
        ]

    # -- what the page's Cicode drew (PAGE.drawn) ----------------------
    def _drawn_part(self, window: int) -> dict:
        """The page state's 'drawn' key: the whole list, absent when nothing is drawn (the
        engine's PageState.ToJson)."""
        entries = getattr(self, "drawn", {}).get(int(window))
        return {"drawn": [dict(e) for e in entries]} if entries else {}

    def set_drawn(self, window: int, entries) -> None:
        """The page's Cicode output in ``window`` is now ``entries`` (a list of drawn entries;
        [] = nothing drawn): a pageChanged carries the whole list, as the engine's does."""
        with self.lock:
            if not hasattr(self, "drawn"):
                self.drawn = {}
            self.drawn[int(window)] = [dict(e) for e in entries or ()]
            self.scan += 1
        self.broadcast(
            "ctsim/pageChanged",
            dict(
                {
                    "window": int(window),
                    "page": self.windows.get(int(window), ""),
                    "serial": self.scan,
                    "objects": [],
                },
                **self._drawn_part(window),
            ),
        )

    def set_page_vars(self, window: int, values: dict) -> None:
        """The page's code used page variables: a pageChanged carries the list."""
        with self.lock:
            self.page_vars.setdefault(int(window), {}).update(values)
            self.scan += 1
        self.broadcast(
            "ctsim/pageChanged",
            {
                "window": int(window),
                "page": self.windows.get(int(window), ""),
                "serial": self.scan,
                "objects": [],
                "pageVars": self.vars_json(window),
            },
        )

    # -- alarm lists in the page state (sim/alarmlists) ---------------
    def push_alarm_list(self, window: int, an: int, alarm_list: dict, rows_on_ans: bool = False) -> None:
        """A pageChanged with an alarm list's state on its first AN (alarm_list {type,
        count, offset, total, array, no_draw, columns, rows, fontInfo?,
        headerFontInfo?}); ``rows_on_ans`` = an AlarmDsp list the runtime draws itself: each
        row also as alarm_row on AN + row."""
        states = {int(an): {"alarm_list": alarm_list}}
        if rows_on_ans:
            for r in alarm_list.get("rows") or ():
                states.setdefault(int(an) + int(r.get("row", 0)), {})["alarm_row"] = r
        objs = [{"an": a, "state": st} for a, st in states.items()]
        with self.lock:
            self.scan += 1
        self.broadcast(
            "ctsim/pageChanged",
            {
                "window": int(window),
                "page": self.windows.get(int(window), ""),
                "serial": self.scan,
                "objects": objs,
            },
        )

    # -- the simulated user and tag locks ----------------------------
    USERS = [{"name": "Operator", "fullName": "Plant operator", "roles": ["Operators"]}]

    def _user_body(self) -> dict:
        u = getattr(self, "user", None) or {"mode": "none", "name": "", "fullName": "", "roles": []}
        return dict(u)

    def _r_user(self, conn, msg, args):
        mode = args.get("mode")
        if mode == "full":
            self.user = {
                "mode": "full",
                "name": "(full access)",
                "fullName": "Full access (simulator)",
                "roles": [],
            }
        elif mode == "none":
            self.user = {"mode": "none", "name": "", "fullName": "", "roles": []}
        elif mode == "user" and any(u["name"] == args.get("name") for u in self.USERS):
            u = next(u for u in self.USERS if u["name"] == args.get("name"))
            self.user = {"mode": "user", "name": u["name"], "fullName": u["fullName"], "roles": u["roles"]}
        else:
            conn.respond(msg, ok=False, message="no user '%s'" % args.get("name"))
            return
        conn.respond(msg, self._user_body())
        self.broadcast("ctsim/userChanged", self._user_body())

    def _r_users(self, conn, msg, args):
        conn.respond(msg, {"user": self._user_body(), "users": [dict(u) for u in self.USERS]})

    def _r_lockTag(self, conn, msg, args):
        name = args.get("name") or ""
        self.locked = getattr(self, "locked", set())
        if args.get("locked", True):
            self.locked.add(name.lower())
        else:
            self.locked.discard(name.lower())
        conn.respond(msg, {"name": name, "locked": bool(args.get("locked", True)), "value": 0})

    def _r_setTagProperty(self, conn, msg, args):
        """Protocol revision 15: a definition field overridden ({name, field?, value?}); the
        answer is the tag's entry with overrides / configured."""
        name = args.get("name") or ""
        self.tag_props = getattr(self, "tag_props", {})
        ov = self.tag_props.setdefault(name.lower(), {})
        field = (args.get("field") or "").upper()
        if not field:
            ov.clear()
        elif args.get("value") is None:
            ov.pop(field, None)
        else:
            ov[field] = str(args["value"])
        body = {"name": name, "overrides": dict(ov)} if ov else {"name": name}
        conn.respond(msg, body)

    # -- parameters -------------------------------------------------
    def params_json(self):
        ps = getattr(self, "params", [])
        return {"serial": getattr(self, "params_serial", 0), "params": [dict(p) for p in ps]}

    def set_params(self, params):
        """The parameters the 'Cicode' read: [{section, name, value, source, ...}]."""
        self.params = [dict(p) for p in params]
        self.params_serial = getattr(self, "params_serial", 0) + 1

    def _r_usedTags(self, conn, msg, args):
        # the names a window's code used (--used w=name;name), from {from} on
        w = int(args.get("window", 0))
        names = list(getattr(self, "used", {}).get(w, ()))
        frm = max(0, int(args.get("from", 0)))
        conn.respond(
            msg, {"window": w, "page": self.windows.get(w, ""), "count": len(names), "names": names[frm:]}
        )

    def _r_params(self, conn, msg, args):
        conn.respond(msg, self.params_json())

    def _r_setParam(self, conn, msg, args):
        ps = getattr(self, "params", [])
        for p in ps:
            if (
                p["section"].lower() == str(args.get("section")).lower()
                and p["name"].lower() == str(args.get("name")).lower()
            ):
                p["value"], p["source"] = args.get("value") or "", "simulator"
                break
        else:
            ps.append(
                {
                    "section": args.get("section"),
                    "name": args.get("name"),
                    "value": args.get("value") or "",
                    "source": "simulator",
                    "users": [],
                }
            )
        self.params = ps
        self.params_serial = getattr(self, "params_serial", 0) + 1
        conn.respond(msg, self.params_json())

    def _r_pageVars(self, conn, msg, args):
        w = int(args.get("window", 0))
        conn.respond(msg, {"window": w, "page": self.windows.get(w, ""), "vars": self.vars_json(w)})

    def _r_setPageVar(self, conn, msg, args):
        w = int(args.get("window", 0))
        name = args.get("name") or ""
        vs = self.page_vars.setdefault(w, {})
        typ = args.get("type") or (vs[name][0] if name in vs else "int")
        value = args.get("value")
        if typ == "int":
            try:
                value = int(value)
            except TypeError, ValueError:
                conn.respond(msg, ok=False, message="'%s' is not a whole number" % value)
                return
        vs[name] = (typ, value)
        if "locked" in args:
            self.var_locks = getattr(self, "var_locks", {})
            self.var_locks[(w, name)] = bool(args["locked"])
        conn.respond(msg, {"window": w, "page": self.windows.get(w, ""), "vars": self.vars_json(w)})

    def _r_windows(self, conn, msg, args):
        conn.respond(msg, self.windows_body())

    def _r_input(self, conn, msg, args):
        self.inputs.append(args)
        conn.respond(msg, {})
        self.broadcast(
            "ctsim/log", {"level": "info", "text": "input %s AN %s" % (args.get("kind"), args.get("an"))}
        )
        if args.get("kind") == "down" and args.get("an") == 11:
            self.broadcast("ctsim/notSimulated", {"function": "Beep", "count": 2})

    # -- protocol revision 10, the viewer's part (sim/client) -------------------------------
    # load {startup: 'auto'} (the runtime's startup tasks), modelWanted -> pageModel, dialogs
    # (ctsim/dialog -> ctsim/answer, ctsim/dialogClosed), ctsim/alarms + ctsim/equipment with
    # setAlarm / setEquipment and alarmsChanged. Recorded: ``models`` [(window, model)],
    # ``answers`` [args], ``alarm_sets`` / ``equipment_sets`` [args].
    STARTUP_TASKS = [{"function": "_CitectBegin", "task": 1}, {"function": "Startup", "task": 2}]

    def startup_body(self) -> dict:
        if 0 not in self.windows:
            self.startup_pending = True
            return {
                "startupTasks": [],
                "pending": True,
                "notes": ["the startup code runs once window 0 shows a page"],
            }
        if getattr(self, "startup_ran", False):
            return {"startupTasks": list(self.STARTUP_TASKS), "notes": ["the startup code ran already"]}
        self.startup_ran = True
        return {"startupTasks": list(self.STARTUP_TASKS), "notes": []}

    def run_pending_startup(self, window: int) -> None:
        """A page shows in ``window``: a startup code that waits for window 0 runs now (the
        engine's RuntimeStartup.PageShown), logged as the engine logs it; ``startup_after``
        records the page it ran after."""
        if (
            int(window) != 0
            or not getattr(self, "startup_pending", False)
            or getattr(self, "startup_ran", False)
        ):
            return
        self.startup_pending = False
        self.startup_ran = True
        self.startup_after = self.windows.get(0, "")
        for t in self.STARTUP_TASKS:
            self.broadcast(
                "ctsim/log",
                {
                    "level": "info",
                    "kind": "startup",
                    "text": "startup: %s() runs as task %d" % (t["function"], t["task"]),
                },
            )

    def want_model(self, window: int, page: str, path: str = "") -> None:
        """Cicode displayed ``page`` in ``window``: the viewers are asked for its drawing."""
        with self.lock:
            self.windows[int(window)] = page
            if path:
                self.paths[page.lower()] = path
        self.broadcast("ctsim/modelWanted", {"window": int(window), "page": page, "path": path})

    def _r_pageModel(self, conn, msg, args):
        self.models.append((int(args.get("window", 0)), args.get("model")))
        conn.respond(msg, {"window": int(args.get("window", 0)), "ok": True})

    def show_dialog(self, body: dict) -> None:
        """A task waits on a dialog (popupMenu / message / input / form)."""
        self.broadcast("ctsim/dialog", dict(body))

    def close_dialog(self, dialog_id) -> None:
        self.broadcast("ctsim/dialogClosed", {"id": dialog_id})

    def _r_answer(self, conn, msg, args):
        self.answers.append(dict(args))
        conn.respond(msg, {"id": args.get("id"), "ok": True})

    def want_image(self, body: dict) -> None:
        """WinFile / WinPrint / WinCopy: the viewers that draw windows are asked for one
        (``{id, window, page, function, xScale, yScale, clipboard}``)."""
        self.images_wanted = getattr(self, "images_wanted", {})
        self.images_wanted[body.get("id")] = dict(body)
        self.broadcast("ctsim/imageWanted", dict(body))

    def _r_windowImage(self, conn, msg, args):
        self.images.append(dict(args))
        want = getattr(self, "images_wanted", {}).pop(args.get("id"), None)
        if want is None:
            conn.respond(msg, None, ok=False, message="no window picture %s is waited for" % args.get("id"))
            return
        body = {"id": args.get("id"), "accepted": True}
        if want.get("clipboard") and args.get("bmp"):
            # (the engine sends the recoloured picture back; the fake sends it as it came)
            body["bmp"] = args["bmp"]
        conn.respond(msg, body)

    ALARMS = [
        {
            "tag": "Pump1_Fault",
            "name": "Pump 1 fault",
            "desc": "",
            "equipment": "Pump1",
            "item": "Fault",
            "type": "digital",
            "category": 1,
            "priority": 1,
        },
        {
            "tag": "Tank1_High",
            "name": "Tank 1 high",
            "desc": "Level high",
            "equipment": "Tank1",
            "item": "High",
            "type": "analog",
            "category": 2,
            "priority": 2,
        },
        {
            "tag": "Other_Alarm",
            "name": "Elsewhere",
            "desc": "",
            "equipment": "Area9.Fan",
            "item": "Trip",
            "type": "digital",
            "category": 1,
            "priority": 3,
        },
    ]
    EQUIPMENT = [
        {
            "name": "Pump1",
            "type": "Pump",
            "MODE": "Unknown",
            "STATE": "Unknown",
            "DRMODE": "Unknown",
            "scheduled": False,
        },
        {"name": "Tank1", "type": "Tank", "MODE": "0", "STATE": "Off", "DRMODE": "0", "scheduled": True},
        {
            "name": "Area9.Fan",
            "type": "Fan",
            "MODE": "Unknown",
            "STATE": "Unknown",
            "DRMODE": "Unknown",
            "scheduled": False,
        },
    ]
    _ALARM_WORDS = {
        "on": (True, False, False),
        "on_ack": (True, True, False),
        "off_unack": (False, False, False),
        "off": (False, True, False),
        "disabled": (False, True, True),
    }

    def _alarm_rows(self):
        st = getattr(self, "alarm_states", None)
        if st is None:
            st = self.alarm_states = {a["tag"].lower(): "off" for a in self.ALARMS}
        out = []
        for a in self.ALARMS:
            word = st[a["tag"].lower()]
            on, ack, dis = self._ALARM_WORDS[word]
            state = {
                "on": "unack_on",
                "on_ack": "ack_on",
                "off_unack": "unack_off",
                "off": "off",
                "disabled": "disabled",
            }[word]
            out.append(
                dict(
                    a,
                    state=state,
                    on=on,
                    ack=ack,
                    disabled=dis,
                    onTime="2026-10-02T08:00:00.000Z" if word in ("on", "on_ack", "off_unack") else "",
                )
            )
        return out

    def _r_alarms(self, conn, msg, args):
        rows = self._alarm_rows()
        f = str(args.get("filter") or "").lower()
        if f:
            rows = [
                a
                for a in rows
                if f in a["tag"].lower()
                or f in a["name"].lower()
                or f in ("%s.%s" % (a["equipment"], a["item"])).lower()
            ]
        if args.get("page") or args.get("window") is not None:
            rows = [a for a in rows if a["equipment"] in ("Pump1", "Tank1")]
        start = int(args.get("start") or 0)
        count = int(args.get("count") if args.get("count") is not None else 5000)
        self.alarm_queries.append(dict(args))
        conn.respond(
            msg,
            {
                "total": len(rows),
                "start": start,
                "serial": getattr(self, "alarm_serial", 0),
                "alarms": rows[start : start + count],
            },
        )

    def _r_setAlarm(self, conn, msg, args):
        name = str(args.get("name") or "").lower()
        rows = {a["tag"].lower() for a in self.ALARMS}
        state = args.get("state")
        if name not in rows:
            conn.respond(
                msg,
                {"error": 424, "errorText": "Tag not found"},
                ok=False,
                message="setAlarm %s: error 424 Tag not found" % args.get("name"),
            )
            return
        if state not in self._ALARM_WORDS:
            conn.respond(msg, ok=False, message="setAlarm: '%s' is no alarm state" % state)
            return
        self._alarm_rows()
        self.alarm_states[name] = state
        self.alarm_sets.append(dict(args))
        self.alarm_serial = getattr(self, "alarm_serial", 0) + 1
        a = next(x for x in self._alarm_rows() if x["tag"].lower() == name)
        conn.respond(msg, {"alarm": {k: a[k] for k in ("tag", "on", "ack", "disabled", "state", "onTime")}})
        self.broadcast("ctsim/alarmsChanged", {"serial": self.alarm_serial})

    # -- ActiveX controls (ctsim/setObjectProperty, PAGE controls) -------------------------
    def _r_setObjectProperty(self, conn, msg, args):
        w = int(args.get("window", 0))
        an = int(args.get("an", 0))
        prop, value = str(args.get("property") or ""), str(args.get("value", ""))
        known = getattr(self, "control_ans", {6})
        if w not in self.windows or an not in known or not prop:
            conn.respond(
                msg, {"window": w, "an": an}, ok=False, message="AN %d is no ActiveX control of the page" % an
            )
            return
        self.object_sets.append(dict(args))
        ctl = getattr(self, "controls", {}).setdefault(w, {})
        entry = ctl.setdefault(an, {"an": an, "name": "AN%d" % an, "properties": {}, "serial": 0})
        changed = entry["properties"].get(prop) != value
        entry["properties"][prop] = value
        entry["serial"] += 1
        conn.respond(msg, {"window": w, "an": an, "property": prop, "value": value, "changed": changed})

    def set_control(self, window: int, an: int, prop: str, value: str) -> None:
        """The page's code wrote a control property: a pageChanged with the controls."""
        with self.lock:
            if not hasattr(self, "controls"):
                self.controls = {}
            ctl = self.controls.setdefault(int(window), {})
            entry = ctl.setdefault(an, {"an": an, "name": "AN%d" % an, "properties": {}, "serial": 0})
            entry["properties"][prop] = value
            entry["serial"] += 1
            self.scan += 1
            body = {
                "window": int(window),
                "page": self.windows.get(int(window), ""),
                "serial": self.scan,
                "objects": [],
                "controls": [dict(e, properties=dict(e["properties"])) for e in ctl.values()],
            }
        self.broadcast("ctsim/pageChanged", body)

    def _r_equipment(self, conn, msg, args):
        rows = [dict(e) for e in getattr(self, "equipment_rows", self.EQUIPMENT)]
        f = str(args.get("filter") or "").lower()
        if f:
            rows = [e for e in rows if f in e["name"].lower()]
        conn.respond(msg, {"total": len(rows), "start": 0, "equipment": rows})

    def _r_setEquipment(self, conn, msg, args):
        name = str(args.get("name") or "")
        if not hasattr(self, "equipment_rows"):
            self.equipment_rows = [dict(e) for e in self.EQUIPMENT]
        e = next((x for x in self.equipment_rows if x["name"].lower() == name.lower()), None)
        if e is None:
            conn.respond(
                msg,
                {"error": 289, "errorText": "Equipment not found"},
                ok=False,
                message="setEquipment %s: error 289" % name,
            )
            return
        for k, v in (args.get("fields") or {}).items():
            if k.upper() not in ("MODE", "STATE", "DRMODE"):
                conn.respond(
                    msg,
                    {"error": 274, "errorText": "Invalid argument"},
                    ok=False,
                    message="setEquipment: %s is no runtime field" % k,
                )
                return
            e[k.upper()] = str(v)
        self.equipment_sets.append(dict(args))
        conn.respond(msg, {"name": e["name"], "fields": {k: e[k] for k in ("MODE", "STATE", "DRMODE")}})


def main(argv) -> int:
    if "serve" not in argv:
        sys.stderr.write("usage: ctsim --version\n       ctsim run <rdb> <function>\n")
        return 2
    project = argv[argv.index("--project") + 1] if "--project" in argv else os.getcwd()
    if "--no-rdb" in argv:
        # the engine's refusal of a project that is not compiled (ServeCommand, exit 1)
        sys.stderr.write(
            "ctsim serve: no compiled code: %s\\_LIBRARY.RDB does not exist "
            "(compile the project; the source compiler is not built yet)\n" % project
        )
        return 1
    folder = C.discovery_dir()
    tags = int(argv[argv.index("--tags") + 1]) if "--tags" in argv else 1234
    root = argv[argv.index("--root") + 1] if "--root" in argv else ""
    srv = FakeServer(
        project,
        folder,
        pages="--pages" in argv,
        list_requests="--list" in argv,
        tags=tags,
        exit_on_crash=True,
        root=root,
        navigate="--navigate" in argv,
    )
    if "--start-page" in argv:
        srv.start_page = argv[argv.index("--start-page") + 1]
    if "--revision" in argv:
        # an engine of another protocol revision (engine/PROTOCOL.md 'Versions')
        srv.revision = int(argv[argv.index("--revision") + 1])
    srv.used = {}
    for k, a in enumerate(argv):
        if a == "--used" and k + 1 < len(argv):
            w, _, names = argv[k + 1].partition("=")
            srv.used[int(w)] = [n for n in names.split(";") if n]
    if "--record" in argv:
        srv.record_path = argv[argv.index("--record") + 1]
    if "--slow" in argv:
        time.sleep(float(argv[argv.index("--slow") + 1]))
    srv.start()
    if "--plain-ready" in argv:
        print("ctsim serve: listening on %s" % srv.pipe, flush=True)
    else:
        # the engine's ready line (ServeCommand.WriteReady)
        print(
            json.dumps(
                {
                    "event": "ready",
                    "id": srv.id,
                    "pipe": srv.pipe,
                    "transport": "pipe",
                    "address": srv.pipe,
                    "pid": os.getpid(),
                    "discovery": srv.discovery_path,
                    "project": srv.project,
                    "root": srv.root,
                    "rootReason": srv.root_reason,
                    "roots": list(srv.roots),
                    "loadMs": 1,
                }
            ),
            flush=True,
        )
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
