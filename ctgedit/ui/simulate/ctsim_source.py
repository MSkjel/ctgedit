"""The Simulate window's sources, backed by the Cicode simulator."""

from __future__ import annotations

import copy
import re
import dataclasses
import os
import threading
import time
import weakref
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...infra import gcguard, logsetup
from ...infra.textmatch import TextMatch
from ...sim import client as C
from ...sim.state import (
    ObjectState,
    PageState,
    controls_from_json,
    drawn_from_json,
    object_states_from_json,
    page_state_from_json,
)
from . import sources as S
from . import viewer as V

log = logsetup.get("sim")


def used_where(by) -> str:
    """Where a name was first used (an entry of ctsim/usedTags' ``by``): 'TankLevel
    (tanks.ci line 32)', 'the page' for the page's own expression or command, '' unknown."""
    if not isinstance(by, dict):
        return ""
    fn = str(by.get("function") or "")
    if not fn:
        return "the page"
    f = os.path.basename(str(by.get("file") or ""))
    try:
        line = int(by.get("line") or 0)
    except TypeError, ValueError:
        line = 0
    if f and line:
        return "%s (%s line %d)" % (fn, f, line)
    return "%s (%s)" % (fn, f) if f else fn


def _sentence(e) -> str:
    """An error's text to follow 'Demo values: ' (its first letter in lower case)."""
    t = str(e)
    return t[:1].lower() + t[1:] if t[1:2].islower() else t


def prestart(page_path: str, engine_path: Optional[str] = None, hub=None) -> None:
    """Start (or attach to) the simulator of the page's project now, on a worker: the
    Simulate window then derives the runtime page and exports the model on the GUI thread
    while the server boots."""
    if C.disabled() or not page_path:
        return
    project = os.path.dirname(os.path.abspath(page_path))
    try:
        (hub or C.HUB).prestart(project, root=simulation_root(project), engine_path=engine_path, viewer=True)
    except Exception:  # noqa: BLE001 -- the tab's own connect reports it
        logsetup.note_exception("sim.ctsim_source.prestart")


def simulation_root(project: str) -> Optional[str]:
    """The compiled project the Simulate tab runs ``project`` as: io/tagscope's root (the Tags
    panel's From: choice when one was made, else [CtEdit] LastDatabase among several, else the
    first by name; the project itself when it is compiled on its own). None for a folder no
    registered project compiles (the server then decides)."""
    if not project:
        return None
    try:
        from ...io import tagscope as TS

        return TS.scope(project).root or None
    except Exception:  # noqa: BLE001 -- the server picks the same root itself
        logsetup.note_exception("sim.ctsim_source.simulation_root")
        return None


#: the live EngineSimulations (root_choice_changed reconnects those of a project)
_LIVE: "weakref.WeakSet[EngineSimulation]" = weakref.WeakSet()


def root_choice_changed(project: str) -> int:
    """The Tags panel's From: choice for ``project`` changed: every live tab of that project
    whose server runs it as another root reconnects (to a server of the new root, started
    when none runs). GUI thread. The number of tabs reconnected."""
    key = C.project_key(project)
    n = 0
    for sim in list(_LIVE):
        if sim._stopped or not sim.project or C.project_key(sim.project) != key:
            continue
        want = simulation_root(sim.project)
        s = sim.session
        if s is None or C.root_key(want) == C.root_key(s.root):
            continue
        log.info(
            "ctsim: the compiled project of %s is now %s (was %s): reconnecting", sim.project, want, s.root
        )
        sim.reconnect()
        n += 1
    return n


#: the page's own tags fetched at most
PAGE_TAG_LIMIT = 20000
#: tags one search fetches (the Inputs panel shows this many): the default of Settings >
#: Simulate > Inputs > Tags listed at most (options.tag_rows)
SEARCH_LIMIT = 2000
#: log entries kept between two drains (the default of Log lines kept)
LOG_KEEP = 5000


def _tag_rows() -> int:
    try:
        from .options import tag_rows

        return tag_rows()
    except Exception:  # noqa: BLE001 -- swallow-ok: a setting is a convenience: the default
        return SEARCH_LIMIT


#: the alarm states (ctsim/setAlarm's words) as the Alarms panel names them
ALARM_STATE_TEXT = {
    "unack_on": "On",
    "on": "On",
    "ack_on": "Acknowledged",
    "on_ack": "Acknowledged",
    "unack_off": "Off unacknowledged",
    "off_unack": "Off unacknowledged",
    "off": "Off",
    "disabled": "Disabled",
}
#: a ctsim/setAlarm word -> the state ctsim/alarms lists for it
LISTED_STATE = {
    "on": "unack_on",
    "on_ack": "ack_on",
    "off_unack": "unack_off",
    "off": "off",
    "disabled": "disabled",
}


#: the log line limit, read again at most every few seconds (not a settings read per event
#: on the reader thread)
_LOG_LINES = [LOG_KEEP, -1.0]
#: task events (started / ended without a halt) one second may add to the log one by one;
#: more are counted and listed in one line a second
TASK_BURST = 40
#: the most log lines one drain hands over (the oldest of a burst are left out, counted)
DRAIN_MAX = 2000


def _log_lines() -> int:
    now = time.monotonic()
    if now - _LOG_LINES[1] < 3.0:
        return _LOG_LINES[0]
    try:
        from .options import log_lines

        _LOG_LINES[0] = log_lines()
    except Exception:  # noqa: BLE001  # swallow-ok: a setting, the default
        _LOG_LINES[0] = LOG_KEEP
    _LOG_LINES[1] = now
    return _LOG_LINES[0]


def _tasks_shown() -> bool:
    """Whether the Outputs panel shows Task lines (its type filter)."""
    try:
        from . import options

        off = {t.strip() for t in str(options.get("log_types_off")).split(",")}
    except Exception:  # noqa: BLE001  # swallow-ok: a setting, the default
        return True
    return "task" not in off


def _tag_info(d: Dict[str, Any], page_reads: Optional[bool] = None) -> S.TagInfo:
    return S.TagInfo(
        name=d["name"],
        type=d.get("type") or "REAL",
        value=d.get("value"),
        quality=d.get("quality") or "Good",
        eng_zero=d.get("eng_zero"),
        eng_full=d.get("eng_full"),
        units=d.get("units") or "",
        equipment=d.get("equipment") or "",
        item=d.get("item") or "",
        comment=d.get("comment") or "",
        page_reads=bool(d.get("page_reads")) if page_reads is None else page_reads,
        project=d.get("project") or "",
        defined=d.get("defined") is not False,
        simulated=bool(d.get("simulated")),
        via=d.get("via") or "",
        ref=d.get("ref") or "",
        locked=bool(d.get("locked")),
        format=d.get("format") or "",
        raw_zero=d.get("raw_zero"),
        raw_full=d.get("raw_full"),
        deadband=d.get("deadband"),
        io_device=d.get("io_device") or "",
        address=d.get("address") or "",
        custom=tuple(d.get("custom") or ("",) * 8),
        overrides=dict(d.get("overrides") or {}),
        configured=dict(d.get("configured") or {}),
    )


def _write_error(err) -> str:
    """A refused setTag as 'error 424 Tag not found' (the engine's message repeats 'setTag
    <name>:' in front)."""
    body = getattr(err, "body", None)
    if isinstance(body, dict) and body.get("error") and body.get("errorText"):
        return "error %s %s" % (body["error"], body["errorText"])
    return str(err)


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except TypeError, ValueError:
        return default


_HW_ERROR_RE = re.compile(
    r"^(?P<sup>SUPPRESSED )?Hardware error \((?P<code>\d+)\) '(?P<name>[^']*)'"
    r"(?: ErrPage: '(?P<page>[^']*)')?(?: ErrDesc: '(?P<desc>[^']*)')?"
    r"(?: in task (?P<task>-?\d+)(?P<fn> \S+)?)?(?P<tail>.*)$"
)


def plain_log_text(text: str) -> str:
    """A hardware error line as a SCADA engineer reads it: 'Tag not found (424) in TagWrite:
    X', not the runtime's 'Hardware error (424) 'Tag not found' ErrPage: 'TagWrite' ErrDesc:
    'X' in task 7 Fn' (the details box keeps the raw text; the Where column has the task's
    function). A line of another shape is returned as it is."""
    m = _HW_ERROR_RE.match(text or "")
    if m is None:
        return text
    out = "%s (%s)" % (m.group("name") or "Hardware error", m.group("code"))
    if m.group("page"):
        out += " in " + m.group("page")
    if m.group("desc"):
        out += ": " + m.group("desc")
    tail = (m.group("tail") or "").strip()
    if tail.startswith(":"):
        out += "; " + tail[1:].strip()  # (': the task halts (why)')
    if m.group("sup"):
        out = "Suppressed: " + out
    return out


def log_entry(t: float, body: dict) -> "S.LogEntry":
    """A ctsim/log event as an Outputs line: the simulator's text, the failing call with its
    values, the tag, the call stack with each frame's arguments and the page object whose code
    ran."""
    level = str(body.get("level") or "info").lower()
    kind = "error" if level in ("error", "fatal") else "log"
    stack = []
    for f in body.get("stack") or ():
        if isinstance(f, dict):
            args = f.get("args") if isinstance(f.get("args"), dict) else {}
            stack.append(
                (
                    str(f.get("function") or ""),
                    str(f.get("file") or ""),
                    _int(f.get("line")),
                    tuple((str(k), str(v)) for k, v in args.items()),
                )
            )
    raw = str(body.get("text") or "")
    text = plain_log_text(raw)
    return S.LogEntry(
        t,
        kind,
        text,
        raw=raw if text != raw else "",
        category=S.engine_category(str(body.get("kind") or ""), level, raw, _int(body.get("code"))),
        function=str(body.get("function") or ""),
        file=str(body.get("file") or ""),
        line=_int(body.get("line")),
        level=level,
        code=_int(body.get("code")),
        call=str(body.get("call") or ""),
        tag=str(body.get("tag") or ""),
        stack=tuple(stack),
        window=_int(body.get("window"), -1),
        page=str(body.get("page") or ""),
        an=_int(body.get("an"), -1),
        source=str(body.get("source") or ""),
    )


def debug_pause_where(body: Dict[str, Any]) -> str:
    """'TankLevel, tanks.ci:18 (AN 44 on Mixer)': where ctsim/debugPaused says the debugger
    holds the simulation."""
    fn = str(body.get("function") or "") or "page code"
    f = str(body.get("file") or "").replace("/", "\\").rsplit("\\", 1)[-1]
    line = _int(body.get("line"), 0)
    text = fn + (", %s%s" % (f, ":%d" % line if line else "") if f else "")
    an = _int(body.get("an"), 0)
    page = str(body.get("page") or "")
    if an > 0:
        text += " (AN %d%s)" % (an, " on %s" % page if page else "")
    elif page:
        text += " (on %s)" % page
    return text


#: the simulator's load notes about code that did not compile (engine LiveProgram /
#: Simulation.Open): 'compile error: <E.... text>', 'not compiled from source: <function>:
#: <why>', '<file>.ci does not compile ...'
_NOT_COMPILED = "not compiled from source: "
#: how many compile errors, and how many functions of a reason, a summary names
MAX_COMPILE_ERRORS = 10
MAX_NAMED_FUNCTIONS = 5


def is_compile_note(note: str) -> bool:
    """A load note about code that did not compile (compile_note_lines sums them up)."""
    return note.startswith("compile error: ") or note.startswith(_NOT_COMPILED) or " does not compile" in note


def compile_note_lines(notes) -> List[Tuple[str, str]]:
    """The simulator's compile notes as Outputs lines."""
    errors: List[str] = []
    files: List[str] = []
    reasons: List[str] = []
    by_reason: Dict[str, List[str]] = {}
    for n in notes:
        n = str(n or "")
        if n.startswith("compile error: "):
            errors.append(n[len("compile error: ") :].strip())
        elif n.startswith(_NOT_COMPILED):
            rest = n[len(_NOT_COMPILED) :]
            fn, _sep, why = rest.partition(": ")
            why = why.strip()
            if why not in by_reason:
                by_reason[why] = []
                reasons.append(why)
            by_reason[why].append(fn.strip())
        elif " does not compile" in n:
            files.append(n.strip())
    out: List[Tuple[str, str]] = []
    for e in errors[:MAX_COMPILE_ERRORS]:
        out.append(("error", "Compile error: " + e))
    if len(errors) > MAX_COMPILE_ERRORS:
        out.append(("error", "%d more compile errors." % (len(errors) - MAX_COMPILE_ERRORS)))
    for why in reasons:
        fns = by_reason[why]
        if len(fns) == 1:
            out.append(("error", "%s did not compile from source: %s" % (fns[0], why or "no reason given")))
        else:
            named = ", ".join(fns[:MAX_NAMED_FUNCTIONS]) + (", ..." if len(fns) > MAX_NAMED_FUNCTIONS else "")
            out.append(
                (
                    "error",
                    "%d functions did not compile from source (%s): %s"
                    % (len(fns), named, why or "no reason given"),
                )
            )
    for f in files:
        out.append(("warning", f[0].upper() + f[1:] + ("" if f.endswith(".") else ".")))
    return out


class _Buffered:
    """pageChanged events buffered while a tab waits for a window's whole state."""

    __slots__ = ("page", "objs", "drawn", "controls", "vars", "flash")

    def __init__(self, page: str):
        self.page = page
        self.objs: Dict[int, Tuple[int, Optional[int], ObjectState]] = {}
        self.drawn = None
        self.controls = None
        self.vars = None
        self.flash = None

    def add(self, n: int, serial: Optional[int], body: Dict[str, Any]) -> None:
        for an, st in object_states_from_json(body.get("objects") or ()).items():
            self.objs[an] = (n, serial, st)
        self.drawn = (n, serial, drawn_from_json(body.get("drawn")))
        c = controls_from_json(body.get("controls"))
        if c is not None:
            self.controls = (n, serial, c)
        if isinstance(body.get("pageVars"), list):
            self.vars = (n, serial, body)
        if "flashOn" in body or "flash_on" in body:
            self.flash = (n, serial, bool(body.get("flashOn", body.get("flash_on"))))

    def newer(self, keep: Callable[[int, Optional[int]], bool]):
        """(objects, drawn | None, controls | None, page-vars body | None, flash | None) of
        the entries ``keep(arrival, serial)`` takes (None: none newer)."""
        objs = {an: st for an, (n, ser, st) in self.objs.items() if keep(n, ser)}

        def one(e):
            return e[2] if e is not None and keep(e[0], e[1]) else None

        return objs, one(self.drawn), one(self.controls), one(self.vars), one(self.flash)


def _serial(body: Any) -> Optional[int]:
    """A PAGE body's serial (None: none, or not a number)."""
    try:
        v = body.get("serial") if isinstance(body, dict) else None
        return None if v is None or isinstance(v, bool) else int(v)
    except TypeError, ValueError:
        return None


#: windows whose events are kept while a tab opens its page (any window: the tab does not
#: know its window before the open's answer)
BUFFER_WINDOWS = 64


class EngineSimulation:
    """The Simulate tab's simulation over ctsim (see the module docstring)."""

    def __init__(
        self,
        engine_path: Optional[str] = None,
        hub: Optional[C.Hub] = None,
        fake: Optional[S.FakeSimulation] = None,
        location: Optional[C.EngineLocation] = None,
        folder: Optional[str] = None,
        page_name: Optional[str] = None,
        model: Optional[dict] = None,
        attach_window: Optional[int] = None,
        project: Optional[str] = None,
    ):
        self.engine_path = engine_path
        self.location = location  # a test's fake engine
        self.folder = folder  # the discovery folder (tests)
        self._hub = hub if hub is not None else C.HUB
        self.fake = fake if fake is not None else S.FakeSimulation()
        #: made-up values while no simulator runs the page (Simulate option demo_values, a
        #: developer's aid; set by the window): off, the page shows as it is saved
        self.demo_values = False
        self._lock = threading.RLock()
        self._gen = 0
        self._session: Optional[C.Session] = None
        self._listener = None
        self._closer = None
        self._stopped = False
        self._status = S.SimStatus("Starting the simulation.", busy=True)
        self.page_path = ""
        self.page = None
        self.project = ""
        self.page_name = page_name or ""
        self._init_page_state()
        self._init_tag_state()
        self._init_user_state()
        self._init_window_state(model, attach_window, project)
        self._init_options()
        self._init_alarm_state()
        _LIVE.add(self)

    def _init_page_state(self) -> None:
        """The page states from the server: the window it opened, whether its whole state
        arrived, the changes, drawn list and controls not handed on yet, and the flash."""
        # page states from the server: _window = the window it opened, _live = its whole
        # state arrived (from then on its states are drawn)
        self._window: Optional[int] = None
        self._live = False
        self._full: Optional[PageState] = None
        self._changes: Dict[int, ObjectState] = {}
        # the page's drawn list (PAGE.drawn, the whole list) not handed to the scene yet
        self._drawn_next: Optional[Dict[int, Dict[str, Any]]] = None
        # the page's ActiveX controls (PAGE.controls) not handed to the window yet
        self._controls_next: Optional[Dict[int, Dict[str, Any]]] = None
        self._flash = True
        self._flash_sent = True  # the flash phase the last PageState carried
        self._page_serial = 0
        self._replace_next = False

    def _init_tag_state(self) -> None:
        """The tags from the server, the page variables, the page changes held while a
        window's whole state is awaited, the parameters, the tags used at run time and the
        reloads."""
        # tags from the server
        self._tag_mode = 0  # bumped when the tags switch source
        self._tag_serial = 0
        self._page_tags: Optional[List[S.TagInfo]] = None
        # the tab's window's page variables (PAGE 'pageVars'), first use first
        self._page_vars: List[S.TagInfo] = []
        # pageChanged events that came while the tab waited for a window's whole state (the
        # page opening: every window, the tab does not know its own yet; a reload, a
        # navigation back: its window), by window; _arrived numbers every pageChanged the
        # session delivered (the reader thread's order); _base = the serial of the whole state
        # drawn now (an event no newer than it is in it already)
        self._opening = False
        self._buffers: Dict[int, _Buffered] = {}
        self._arrived = 0
        self._base: Optional[int] = None
        # the parameters the simulation's Cicode read (ctsim/params), polled while it runs
        self._params: List[S.TagInfo] = []
        self._params_serial = -1
        self._params_asked = 0.0
        self._params_busy = False
        self._params_off = False
        # the tags the window's code used at run time (ctsim/usedTags) that the page's own
        # list does not have: (window, page) they are for, how many names were taken, the rows
        self._used_for: Optional[Tuple[int, str]] = None
        self._used_from = 0
        self._used_rows: List[S.TagInfo] = []
        self._used_busy = False
        # the page tag lists asked for (_load_page_tags runs on worker threads: a connect, a
        # reload, a tag definition change): only the newest one asked is taken
        self._tags_seq = 0
        # reloads (the page edited): one at a time, the newest of those waiting runs
        self._reload_gate = threading.Lock()
        self._reload_seq = 0

    def _init_user_state(self) -> None:
        """The simulated user, the tag search, the associations and undefined tags asked
        for, the log and the clock."""
        # the simulated user (engine/PROTOCOL.md ctsim/user): what this tab wants on a
        # simulation it drives ('full' / 'none' / 'user:<name>'), what the server said, and
        # the project's users
        self.want_user = "full"
        self._user: Dict[str, Any] = {}
        self._users: List[Dict[str, Any]] = []
        self._user_serial = 0
        self._search_rows: Optional[List[S.TagInfo]] = None
        self._search_total = 0
        self._search_done: Optional[str] = None
        self._search_want: Optional[str] = None
        self._search_busy = False
        self._index: Dict[str, List[S.TagInfo]] = {}
        self._subscribed: Tuple[str, ...] = ()
        # the window's associations when its tags were asked (windowsChanged: ask again)
        self._assoc_seen: Optional[str] = None
        # simulate undefined tags (the server option, engine/PROTOCOL.md ctsim/options):
        # what this tab wants (None = no say), what the server said last
        self._want_undefined: Optional[bool] = None
        self._server_undefined: Optional[bool] = None
        # log and clock
        self._log: List[S.LogEntry] = []
        self._clock: Optional[C.ClockInfo] = None
        self._clock_at = 0.0
        self._want_running = False
        self._want_speed = 1.0
        self._reported: set = set()
        self.inputs: List[S.InputEvent] = []

    def _init_window_state(
        self, model: Optional[dict], attach_window: Optional[int], project: Optional[str]
    ) -> None:
        """The page model the engine reads, the window this tab shows and the windows of the
        simulation, the debugger's pause, the tools, the tag writes not logged yet and
        whether this tab drives the simulation."""
        # the page as the engine reads it (sim/pagemodel: ctsim-page/0), sent with openPage
        self.model = model
        # a window the engine opened (a popup): this tab shows it, it opens no page itself
        self.attach_window = attach_window
        # the simulated project when it is not the page's folder (a popup of an included
        # project's page runs in the project that opened it)
        self._project = project or ""
        self.open_source = ""  # 'rdb' | 'rows' | 'projectRows' (openPage's answer)
        self._windows: List[Dict[str, Any]] = []
        self._windows_serial = 0
        self._windows_seen = False
        # the windows of their own that showed a page before this page opened (another
        # viewer's, on a shared simulator): None = not known
        self._windows_before: Optional[frozenset] = None
        self._nav: Optional[Dict[str, Any]] = None
        self._other_page = ""
        self._window_gone = False
        # the window shows another page for now (a navigation the tab is about to follow, or
        # a state of another page the windows are asked about): the states drawn stay, nothing
        # new is drawn, never the demo values
        self._held = False
        #: ctsim/debugPaused's body while the debugger holds the simulation in page code
        #: (its requests wait until it goes on), None = running
        self._debug_pause: Optional[Dict[str, Any]] = None
        #: the Simulate window's tools on this simulation (sim_tools.SimTools), made on first use
        self._tools = None
        #: the tag writes not logged yet: lower name -> [name, first value, last value,
        #: quality, writes, when the last came, the simulation time of the first]. A slider
        #: drag writes a tag tens of times a second: one line once it settles (WRITE_SETTLE_S)
        self._writes_pending: Dict[str, list] = {}
        # this tab drives the simulation (the first tab on a server ctgedit started): it sets
        # the clock and asks for the project's startup code (load {startup: 'auto'}) once
        self._own = False
        self._viewer_of: Optional[C.Session] = None

    def _init_options(self) -> None:
        """The Simulate settings the window sets, and the SA start."""
        #: Simulate Settings > Run the project's startup code (set by the window)
        self.run_startup = True
        #: Simulate Settings > Show trend history from the start (set by the window): sent
        #: as ctsim/options {seedTrendHistory} by the tab that drives the simulation
        self.seed_trend_history = True
        #: Simulate Settings > Stop at breakpoints in page code (set by the window): sent as
        #: ctsim/options {foregroundBreak} by the tab that drives the simulation (the engine's
        #: default is on: sent on connect only to turn it off)
        self.foreground_break = True
        #: Simulate Settings: a hardware error in page code stops it in the debugger too
        #: (ctsim/options foregroundErrorBreak; sent on connect when on)
        self.foreground_error_break = False
        #: Simulate Settings: the startup code on a fast clock (load fastStartup)
        self.fast_startup = True
        #: Simulate Settings > DLLs and databases (set by the window): 'off', 'ask' or 'on' for
        #: DLLs and for SQL databases, and the allowed lists of Ask; sent as ctsim/options
        #: {dll, sql, dllAllowed, sqlAllowed} by the tab that drives the simulation, before
        #: the project's startup code runs
        self.dll_access = "off"
        self.sql_access = "off"
        self.dll_allowed: List[str] = []
        self.sql_allowed: List[str] = []
        self.dll_timeout = 0  # seconds a DLL call may take; 0 = no limit
        # the simulation was run ahead once for the page's trends (_pre_roll)
        self._pre_rolled = False
        #: Simulate Settings > Run SA pages inside their workspace (set by the window)
        self.sa_workspace = True
        # the SA start (sa_start.py): the startup page this tab shows in window 0 and the
        # window the page itself went to (its pane), None until then
        self.workspace_page = ""
        self.focus_window: Optional[int] = None
        # this view shows an SA page's own workspace pane (set by the window): a reload shows
        # the page again through the workspace (ctsim/navigate)
        self.sa_pane = False

    def _init_alarm_state(self) -> None:
        """Alarms and equipment: what the panels asked last, what came back, the serials of
        the changes."""
        # alarms and equipment (ctsim/alarms, ctsim/equipment): what the panels asked last,
        # what came back, the serial of the last ctsim/alarmsChanged
        self._alarm_serial = 0
        self._alarm_want: Optional[tuple] = None
        self._alarm_done: Optional[tuple] = None
        self._alarm_busy = False
        self._alarm_rows: Optional[List[Dict[str, Any]]] = None
        self._alarm_total = 0
        self._equip_serial = 0
        self._equip_want: Optional[tuple] = None
        self._equip_done: Optional[tuple] = None
        self._equip_busy = False
        self._equip_rows: Optional[List[Dict[str, Any]]] = None
        self._equip_total = 0
        self._equip_asked = 0.0

    # -- what the window shows about it -------------------------------------------------
    def status(self) -> S.SimStatus:
        with self._lock:
            return dataclasses.replace(self._status)

    def _set_status(self, gen: int, **kw) -> None:
        with self._lock:
            if gen == self._gen:
                self._status = S.SimStatus(**kw)

    @property
    def session(self) -> Optional[C.Session]:
        return self._session

    def page_active(self) -> bool:
        """The server runs the page (its states are drawn)."""
        with self._lock:
            return self._live

    def tags_active(self) -> bool:
        with self._lock:
            return self._page_tags is not None

    # -- StateSource ------------------------------------------------------------------
    def start(self, page_path: str, page) -> None:
        self.fake.start(page_path, page)
        self.page_path = page_path or ""
        self.page = page
        self.project = self._project or (os.path.dirname(os.path.abspath(page_path)) if page_path else "")
        if not self.page_name:
            self.page_name = (
                os.path.splitext(os.path.basename(page_path))[0]
                if page_path
                else (getattr(page, "name", "") or "")
            )
        self._connect_async()

    def _connect_async(self) -> None:
        with self._lock:
            self._gen += 1
            gen = self._gen
        if C.disabled():
            self._set_status(
                gen,
                text=self._no_sim_prefix() + "the Cicode simulator is turned off on this computer.",
                level="warn",
            )
            return
        if not self.page_path:
            self._set_status(
                gen,
                text=self._no_sim_prefix() + "the page is not saved in a "
                "project, so the Cicode simulator cannot run it.",
                level="warn",
            )
            return
        self._set_status(gen, text="Connecting to the Cicode simulator.", busy=True)
        gcguard.before_worker()
        threading.Thread(target=self._connect, args=(gen,), daemon=True, name="ctsim-connect").start()

    def reconnect(self, engine_path: Optional[str] = None) -> None:
        """Let the current server go and connect again (``engine_path`` = a new setting: a
        server ctgedit started for the project with the previous ctsim.exe is let go too, so
        the choice takes effect; other tabs of the project fall back and reconnect)."""
        # the page this tab opened closes first (a server that goes on, one another program
        # runs or ctgedit's own kept by another tab, would keep it running in a window of its
        # own)
        self._close_window()
        if engine_path is not None:
            new = engine_path or None
            changed = new != self.engine_path
            self.engine_path = new
            if changed and self.project:
                self._hub.close_started(self.project)
        self._drop_session()
        if not self._stopped:
            self._connect_async()

    def _no_sim_prefix(self) -> str:
        return "Demo values: " if self.demo_values else "The page shows as it is saved: "

    def _connect(self, gen: int) -> None:
        # (the hello asks for task events only while Outputs shows them)
        C.viewer_events = None if _tasks_shown() else tuple(k for k in C.EVENT_KINDS if k != "task")
        kw: Dict[str, Any] = {
            "engine_path": self.engine_path,
            "root": simulation_root(self.project),
            # it answers modelWanted and shows the dialogs (viewer.py)
            "viewer": True,
        }
        if self.location is not None:
            kw["location"] = self.location
        if self.folder is not None:
            kw["folder"] = self.folder
        try:
            s = self._hub.acquire(self.project, **kw)
        except C.EngineNotFound as e:
            self._set_status(
                gen,
                text="%s%s Choose it with Choose Simulator." % (self._no_sim_prefix(), _sentence(e)),
                level="warn",
                engine_missing=True,
                can_retry=True,
                detail="Looked in:\n" + "\n".join(e.searched),
            )
            return
        except C.ServerUnavailable as e:
            # says itself what happened ('The Cicode simulator did not start: no compiled
            # code: ...', 'No Cicode simulator is listening ...')
            first = (str(e).strip().splitlines() or [""])[0]
            self._set_status(
                gen,
                text="%s%s" % (self._no_sim_prefix(), _sentence(first)),
                level="warn",
                can_retry=True,
                detail=str(e),
            )
            return
        except C.CtsimError as e:
            first = (str(e).strip().splitlines() or [""])[0]
            self._set_status(
                gen,
                text="%sthe Cicode simulator could not be reached. %s" % (self._no_sim_prefix(), first),
                level="warn",
                can_retry=True,
                detail=str(e),
            )
            return
        except Exception as e:  # noqa: BLE001 -- a client bug must not end the thread silently
            logsetup.note_exception("sim.ctsim_source.connect")
            self._set_status(
                gen,
                text="Demo values: the Cicode simulator could not be reached (%s)." % e,
                level="danger",
                can_retry=True,
            )
            return
        with self._lock:
            stale = gen != self._gen or self._stopped
            if not stale:
                self._session = s
                listener = self._listener = self._event_listener(gen)
                # under the lock: a stop / reconnect right after this removes it again (a
                # listener added after _drop_session ran would stay on a shared session)
                s.add_listener(listener)
                closer = self._closer = self._close_listener(gen, s)
                # the simulation's modelWanted / dialog events (one listener per session,
                # before any page opens: a page's entry code can open a window at once)
                V.attach(s)
                self._viewer_of = s
        if stale:
            self._hub.release(s)
            return
        # outside the lock (a connection that already ended calls it at once); a stop in
        # between found nothing registered yet: take it off again
        s.on_close(closer)
        if not self._current(gen):
            s.remove_close_listener(closer)
            return
        log.info(
            "ctsim: %s %s for %s (%s)",
            "attached to" if s.attached else "started",
            s.describe(),
            self.project,
            self.page_name,
        )
        # a server of our own follows the clock bar from the start, set by the first tab that
        # uses it; a later tab and a window the page opened (a popup) join the simulation as
        # it runs (the clock is the simulation's: they neither pause it nor reset its speed)
        own = self.attach_window is None and s.claim_clock()
        with self._lock:
            self._own = own
        if s.hello.get("clock") is not None:
            self._take_clock(s.clock)
        if not own:
            self._send_clock(s, gen, "")  # only ask (a shared clock moved on)
        else:
            self._send_clock(s, gen, "speed", self._want_speed)
            self._send_clock(s, gen, "run" if self._want_running else "pause")
        self._status_connected(gen, s, page=None)
        self._log_compile_notes(s, gen)
        # the choice before the server's user is taken (that would overwrite it with the
        # server's own: a started server has nobody logged in)
        with self._lock:
            want_user = self.want_user
        self._take_user(s.hello.get("user"), gen)
        self._ask_users(s, gen)
        if own:
            # the tab that drives the simulation logs in as the window's user choice
            self._send_user(s, gen, want_user)
        opts = s.hello.get("options")
        if isinstance(opts, dict) and isinstance(opts.get("simulateUndefined"), bool):
            with self._lock:
                self._server_undefined = opts["simulateUndefined"]
        with self._lock:
            want = self._want_undefined
        if want:
            # (only to turn it on: a setting that is off leaves a server another client
            # turned on as it is)
            self._send_options(s, gen, want)
        if own and self.seed_trend_history:
            self._send_seed(s, gen, True)
        if own and not self.foreground_break:
            self._send_foreground_break(s, gen, False)
        if own and self.foreground_error_break:
            self._send_foreground_error_break(s, gen, True)
        if own:
            self._send_outside(s, gen)
        if self._current(gen):
            # (a tab stopped meanwhile opens no window on the server); the page first: the
            # tags it reads are those of the code the server runs (an unsaved page's rows)
            if not self._open_after_startup(s, gen):
                self._open_page(s, gen)
        self._load_page_tags(s, gen)

    def _current(self, gen: int) -> bool:
        with self._lock:
            return gen == self._gen and not self._stopped

    def _who(self, s: C.Session) -> str:
        how = (
            "started by ctgedit"
            if not s.attached
            else ("already running, process %d" % s.pid if s.pid else "already running")
        )
        return "%s, %s" % (s.describe() or "ctsim", how)

    @staticmethod
    def _notes(s: C.Session) -> str:
        """The tool tip: the server's project and what its load did not do (hello notes; the
        ones about code that did not compile summed up: compile_note_lines)."""
        bits = [str(s.hello.get("project") or s.project)]
        loc = getattr(s, "location", None)
        exe = (loc.exe if loc is not None else "") or C.process_exe(getattr(s, "pid", 0) or 0)
        build = C.build_text(s.hello) or (
            "file dated %s" % C.file_date(exe) if exe and C.file_date(exe) else ""
        )
        rev = C.protocol_revision(s.hello)
        if exe or build:
            bits.append(
                "Simulator: %s%s%s"
                % (
                    exe or "ctsim",
                    " (protocol revision %d%s)" % (rev, ", " + build if build else ""),
                    "" if loc is not None else ", already running",
                )
            )
        if s.root and C.root_key(s.root) != C.root_key(os.path.basename(str(s.project).rstrip("\\/"))):
            bits.append(
                "Compiled as part of %s%s."
                % (s.root, " (of %s)" % ", ".join(s.roots) if len(s.roots) > 1 else "")
            )
        notes = s.hello.get("notes") or s.hello.get("loadNotes") or ()
        if isinstance(notes, list):
            bits += [text for _level, text in compile_note_lines(notes)]
            bits += [str(n) for n in notes if n and not is_compile_note(str(n))]
        return "\n".join(bits)

    def _log_compile_notes(self, s: C.Session, gen: int) -> None:
        """The simulator's load notes about code that did not compile, as Outputs lines
        (errors): a compile error as it is, the functions that did not compile from source
        grouped by reason. Once per connection; the load answer's copies are left out
        (_startup_done)."""
        if self.attach_window is not None:
            return  # (a window the page's code opened: its tab logged them)
        notes = s.hello.get("notes") or s.hello.get("loadNotes") or ()
        if not isinstance(notes, list) or not self._current(gen):
            return
        for level, text in compile_note_lines(notes):
            self._add_log(S.LogEntry(self._now(), "log", "Startup: " + text, level=level))

    def _project_label(self, s: C.Session) -> str:
        """ "'Example'", or "'MyInclude', compiled as part of MyProject" when the server runs
        the project as part of another compiled project."""
        proj = os.path.basename(self.project.rstrip("\\/")) or self.project
        root = s.root
        if root and C.root_key(root) != C.root_key(proj):
            return "'%s', compiled as part of %s" % (proj, root)
        return "'%s'" % proj

    def _status_connected(
        self, gen: int, s: C.Session, page: Optional[bool], why: str = "", retry: bool = True
    ) -> None:
        label = self._project_label(s)
        notes = self._notes(s)
        if page:
            text = "Cicode simulator for %s (%s)." % (label, self._who(s))
            self._set_status(
                gen,
                text=text,
                demo=False,
                connected=True,
                engine=s.describe(),
                project=self.project,
                detail=notes,
            )
        elif page is None:
            text = "Cicode simulator for %s (%s). Opening the page." % (label, self._who(s))
            self._set_status(
                gen,
                text=text,
                demo=True,
                connected=True,
                busy=True,
                engine=s.describe(),
                project=self.project,
                detail=notes,
            )
        else:
            text = "Demo values on the page: %s Tags, log and clock are the Cicode simulator's (%s, %s)." % (
                why,
                label,
                self._who(s),
            )
            self._set_status(
                gen,
                text=text,
                demo=True,
                connected=True,
                level="warn",
                engine=s.describe(),
                project=self.project,
                can_retry=retry,
                detail=notes,
            )

    def _open_page(self, s: C.Session, gen: int, window: Optional[int] = None) -> None:
        """openPage with the model (or, attached, the window's pageModel), then the states:
        the openPage answer's own ``state`` when it has one, else a pageState."""
        if s.supports("openPage") is False:
            self._status_connected(
                gen, s, page=False, retry=False, why="this simulator cannot run pages yet."
            )
            return
        state = None
        cut = None
        # pageChanged events that come while the page opens (its first scans run while the
        # open is answered; before the answer the tab does not know its window: every
        # window's are kept) go on top of the open's state when they are newer than it
        with self._lock:
            self._opening = True
            self._buffers = {}
        try:
            if self.attach_window is not None:
                window = int(self.attach_window)
                model = self._model_value()
                if (
                    model is not None
                    and s.supports("pageModel") is not False
                    and self._model_needed(s, window, self.page_name)
                ):
                    try:
                        s.page_model(window, model)
                    except C.NotAvailable:
                        pass
            else:
                before = None
                try:
                    before = frozenset(
                        w["window"]
                        for w in s.windows(timeout=3.0)
                        if w.get("parent", -1) == -1 and w.get("page")
                    )
                except C.CtsimError:
                    pass
                with self._lock:
                    self._windows_before = before
                body, cut = self._open_request(
                    s, s.open_page_args(self.page_name, window, self._model_value())
                )
                try:
                    window = int(body.get("window", window if window is not None else 0))
                except TypeError, ValueError:
                    window = int(window or 0)
                state = body.get("state")
                self._note_open(body)
                self._run_startup(s, gen)
        except C.NotAvailable:
            with self._lock:
                self._opening = False
                self._buffers = {}
            self._unhold(gen)
            self._status_connected(
                gen, s, page=False, retry=False, why="this simulator cannot run pages yet."
            )
            return
        except C.CtsimError as e:
            with self._lock:
                self._opening = False
                self._buffers = {}
            self._unhold(gen)
            self._status_connected(gen, s, page=False, why="the simulator did not open the page (%s)." % e)
            return
        with self._lock:
            self._opening = False
            if gen != self._gen:
                return
            # this window's events stay kept until its whole state is taken
            self._buffers = {w: b for w, b in self._buffers.items() if w == window}
            self._window = window
            self._live = False
            self._changes = {}
            self._other_page = ""
            self._window_gone = False
        if self.attach_window is not None:
            # a window Cicode opened (a pane, a popup): the windows its page opened while
            # this connection was coming up went out before it (an AssPopUp from it at once),
            # so they are asked, as a tab asks when it shows a window (_show_window)
            self._ask_windows(s, gen)
        if isinstance(state, dict) and "objects" in state:
            self._page_state_done(gen, s, window, cut=cut)(state, None)
            return
        try:
            s.request_async(
                "pageState", {"window": int(window)}, callback=self._page_state_done(gen, s, window)
            )
        except C.CtsimError as e:
            self._fall_back(gen, "Demo values on the page: %s" % e)

    def _open_request(self, s: C.Session, args: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """Send openPage and wait for it on this worker thread."""
        done = threading.Event()
        out: Dict[str, Any] = {}

        def got(body, err):
            with self._lock:
                out["cut"] = self._arrived
            out["body"], out["err"] = body, err
            done.set()

        s.request_async("openPage", args, callback=got, timeout=C.LOAD_TIMEOUT)
        if not done.wait(C.LOAD_TIMEOUT + 5.0):
            raise C.RequestTimeout("ctsim/openPage", C.LOAD_TIMEOUT)
        if out.get("err") is not None:
            raise out["err"]
        body = out.get("body")
        return (body if isinstance(body, dict) else {}), int(out.get("cut") or 0)

    def _open_after_startup(self, s: C.Session, gen: int) -> bool:
        """Open the page the way a runtime user reaches it: startup page first."""
        with self._lock:
            own = self._own
        if (
            not own
            or self.attach_window is not None
            or not self.run_startup
            or s.supports("navigate") is False
        ):
            return False
        ws = None
        if self.sa_workspace:
            try:
                from .sa_start import workspace_start

                ws = workspace_start(self.project, self.page_name)
            except Exception:  # noqa: BLE001 -- open the page as before
                logsetup.note_exception("sim.ctsim_source.workspace_start")
                ws = None
        if not s.take_startup():
            return False
        # the startup code runs on a running clock, as in the runtime (paused, it would settle
        # at once and the page's entry code and first scans would run before the startup code
        # set the values they read); paused again afterwards
        ran = self._startup_clock(s, gen)
        try:
            if ws is None:
                return self._open_plain_after_startup(s, gen)
            return self._open_workspace_after_startup(s, gen)
        finally:
            self._startup_clock_done(s, gen, ran)

    def _startup_clock(self, s: C.Session, gen: int) -> bool:
        """Run the clock for the startup code when the tab wants it paused (True: it was
        started, :meth:`_startup_clock_done` pauses it again)."""
        with self._lock:
            if self._want_running or not self._own:
                return False
        try:
            self._take_clock(
                C.clock_from(s.request("clock", C.Session.clock_args("run"), timeout=10.0), s.clock), gen
            )
        except C.CtsimError:
            return False
        return True

    def _startup_clock_done(self, s: C.Session, gen: int, ran: bool) -> None:
        if not ran:
            return
        with self._lock:
            if self._want_running or gen != self._gen:
                return  # (Run pressed meanwhile: it keeps running)
        self._send_clock(s, gen, "pause")
        self._add_log(
            S.LogEntry(self._now(), "log", "Startup: the clock ran for the startup code and is paused again.")
        )

    def _open_workspace_after_startup(self, s: C.Session, gen: int) -> bool:
        """The SA start (:meth:`_open_after_startup`): the startup page in window 0 with the
        startup code, then the page in its workspace pane."""
        try:
            body = s.request(
                "load",
                s.load_args(startup="auto", start_page=True, fast_startup=self.fast_startup),
                timeout=C.LOAD_TIMEOUT,
            )
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "The startup page did not show: %s" % e))
            # nothing was started: the page's own open asks for the startup code (_run_startup)
            s.return_startup()
            return False
        body = body if isinstance(body, dict) else {}
        self._startup_done(body)
        sp = body.get("startPage") if isinstance(body.get("startPage"), dict) else {}
        master = str(sp.get("page") or "")
        if not master or _int(sp.get("error")) != 0:
            self._add_log(
                S.LogEntry(
                    self._now(),
                    "log",
                    "The project's startup page did not "
                    "show (%s): the page opens on its own." % (sp.get("errorText") or "no startup page"),
                )
            )
            return False
        self._add_log(
            S.LogEntry(
                self._now(),
                "log",
                "Startup page %s in window 0 (%s); %s "
                "opens in its workspace." % (master, sp.get("source") or "", self.page_name),
            )
        )
        with self._lock:
            self.workspace_page = master
        try:
            nav = s.navigate(self.page_name, model=self._model_value(), timeout=C.LOAD_TIMEOUT)
        except C.CtsimError as e:
            self._add_log(
                S.LogEntry(self._now(), "error", "%s did not open in the workspace: %s" % (self.page_name, e))
            )
            nav = {}
        for n in nav.get("notes") or ():
            if n:
                self._add_log(S.LogEntry(self._now(), "log", "Navigate: %s" % n))
        self._startup_settled(nav.get("startup"))
        pane = _int(nav.get("window"), -1)
        method = str(nav.get("method") or "")
        if method == "pageDisplay" and pane == 0:
            # no workspace after all: the page shows in window 0 (this tab's), as openPage would
            self._note_open(nav)
            self._show_window(s, gen, 0)
            return True
        if method != "workspace" or pane <= 0:
            self._add_log(
                S.LogEntry(
                    self._now(),
                    "log",
                    "%s was not shown in a workspace pane: it opens on its own." % self.page_name,
                )
            )
            return False
        self._note_open(nav)
        with self._lock:
            self.focus_window = pane
        # this tab shows window 0 (the startup page): the windows say so, the tab follows
        # (take_navigation), and the page's pane comes as a window of window 0
        self._show_window(s, gen, 0)
        return True

    def _open_plain_after_startup(self, s: C.Session, gen: int) -> bool:
        """Open a non-SA page after the startup page and code."""
        try:
            body = s.request(
                "load",
                s.load_args(startup="auto", start_page=True, fast_startup=self.fast_startup),
                timeout=C.LOAD_TIMEOUT,
            )
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "The startup page did not show: %s" % e))
            s.return_startup()
            return False
        body = body if isinstance(body, dict) else {}
        self._startup_done(body)
        sp = body.get("startPage") if isinstance(body.get("startPage"), dict) else {}
        start = str(sp.get("page") or "")
        if not start or _int(sp.get("error")) != 0:
            self._add_log(
                S.LogEntry(
                    self._now(),
                    "log",
                    "The project's startup page did not "
                    "show (%s): the page opens first, the startup code after "
                    "it." % (sp.get("errorText") or "no startup page"),
                )
            )
            return False
        if start.strip().lower() == (self.page_name or "").strip().lower():
            # the page is the startup page: it shows in window 0 already, its startup code
            # runs once it has started
            self._add_log(
                S.LogEntry(
                    self._now(),
                    "log",
                    "%s is the project's startup page: it "
                    "shows in window 0 (%s)." % (start, sp.get("source") or ""),
                )
            )
            self._show_window(s, gen, 0)
            return True
        self._add_log(
            S.LogEntry(
                self._now(),
                "log",
                "Startup page %s in window 0 (%s); %s shows "
                "once the startup code has run." % (start, sp.get("source") or "", self.page_name),
            )
        )
        try:
            nav = s.navigate(
                self.page_name, window=0, how="pageDisplay", model=self._model_value(), timeout=C.LOAD_TIMEOUT
            )
        except C.CtsimError as e:
            self._add_log(
                S.LogEntry(
                    self._now(), "error", "%s did not show after the startup page: %s" % (self.page_name, e)
                )
            )
            return False
        for n in nav.get("notes") or ():
            if n:
                self._add_log(S.LogEntry(self._now(), "log", "Navigate: %s" % n))
        self._startup_settled(nav.get("startup"))
        if str(nav.get("method") or "") != "pageDisplay" or 0 not in (
            nav.get("shownIn") or [_int(nav.get("window"), -1)]
        ):
            self._add_log(
                S.LogEntry(
                    self._now(), "log", "%s was not shown in window 0: it opens on its own." % self.page_name
                )
            )
            return False
        self._note_open(nav)
        self._show_window(s, gen, 0)
        return True

    def _startup_settled(self, st: Any) -> None:
        """What ctsim/navigate's ``startup`` says (Outputs): how far the startup code was when
        the page showed."""
        if not isinstance(st, dict):
            return
        state = str(st.get("state") or "")
        secs = _int(st.get("simulatedMs")) / 1000.0
        running = ", ".join(
            str(t.get("function") or "?") for t in st.get("running") or () if isinstance(t, dict)
        )
        text = {
            "settled": "Startup: the startup code ran (%.1f s simulated) before the page showed." % secs,
            "capped": "Startup: the startup code still runs after %.1f s simulated (%s): the "
            "page shows now." % (secs, running or "?"),
            "paused": "Startup: the clock is paused, the startup code waits (%s): the page shows "
            "now." % (running or "sleeping tasks"),
            "timedOut": "Startup: the startup code had not run in time: the page shows now.",
        }.get(state)
        if text:
            self._add_log(S.LogEntry(self._now(), "log", text))

    def _show_window(self, s: C.Session, gen: int, window: int) -> None:
        """This tab shows engine ``window`` from now on: the windows are asked, and a page there
        other than the tab's is a navigation the tab follows (its states are asked then)."""
        with self._lock:
            if gen != self._gen:
                return
            self._window = window
            self._live = False
            self._changes = {}
            self._window_gone = False
            self._windows_seen = True
            self._held = True
            self._other_page = "?"
        # (the reply names the window's page: another page than the tab's = a navigation; the
        # tab's own page = 'back', whose whole state is asked then)
        self._ask_windows(s, gen)

    def _unhold(self, gen: int) -> None:
        """The page's states will not come: the demo values again (the status says why)."""
        with self._lock:
            if gen == self._gen:
                self._held = False

    def _note_open(self, body: Dict[str, Any]) -> None:
        """What the openPage answer says: where the page's code came from, the fields the
        engine could not compile (Outputs)."""
        src = str(body.get("source") or "")
        self.open_source = src
        if src == "rows":
            self._add_log(
                S.LogEntry(
                    self._now(),
                    "log",
                    "The simulator runs the page as it is in "
                    "the editor (its own compile of the page's expressions).",
                )
            )
        elif src == "projectRows":
            self._add_log(
                S.LogEntry(
                    self._now(),
                    "log",
                    "The page is not compiled: the simulator compiled its expressions itself.",
                )
            )
        notes = [str(n) for n in (body.get("notes") or ()) if n]
        for n in notes:
            # a restored or copied project's .RDB of another version of
            # the page: its rows run instead -- said, since a compile puts it right
            if "another version of the page" in n:
                self._add_log(
                    S.LogEntry(
                        self._now(),
                        "log",
                        "%s. Compile the project to run its compiled page again." % (n[0].upper() + n[1:]),
                        level="warning",
                    )
                )
        bad = [n for n in notes if ": E" in n or "not compiled" in n or "does not write" in n]
        # (every one: the Not Simulated tab lists them with Show on Page)
        for n in bad[:200]:
            self._add_log(S.LogEntry(self._now(), "error", "Not simulated: %s" % n, category="not_simulated"))
        if len(bad) > 200:
            self._add_log(
                S.LogEntry(
                    self._now(),
                    "error",
                    "%d more fields are not simulated." % (len(bad) - 200),
                    category="not_simulated",
                )
            )

    # -- the viewer's part ----------------------------------------------------------
    def viewer(self) -> Optional[V.ViewerServices]:
        """The modelWanted / dialog services of this tab's session (None: not connected)."""
        s = self._session
        return V.services_for(s) if s is not None and s.alive else None

    def _model_value(self) -> Optional[dict]:
        """The page model to send now: a helper process's job (modeljob.ModelJob, made while
        the server booted) is waited for here, on the connection's thread, and kept as its
        model; a job that gave none says why in the log (the page runs from its .RDB)."""
        m = self.model
        if m is None or isinstance(m, dict) or not hasattr(m, "result"):
            return m
        got = m.result()
        if self.model is m:
            self.model = got
        if got is None:
            self._add_log(
                S.LogEntry(
                    self._now(),
                    "log",
                    "The page's drawing data was not "
                    "made (%s): the page runs from its compiled code."
                    % (getattr(m, "error", "") or "no answer"),
                    level="warning",
                )
            )
        return got

    def _model_needed(self, s: C.Session, window: int, page: str) -> bool:
        """False when the engine asked for this window's drawing itself (modelWanted: the
        window answers it) or got it already; True = this tab sends it (and says so)."""
        svc = V.services_for(s)
        if svc is None:
            return True
        if svc.model_wanted_or_sent(window, page):
            return False
        svc.note_model_sent(window, page)
        return True

    def _run_startup(self, s: C.Session, gen: int) -> None:
        """After the first openPage on a server ctgedit started: the runtime's startup code
        (load {startup: 'auto'}: Include's _CitectBegin, then the roles' StartupCode), once
        per simulation, when Simulate Settings > Run the project's startup code is on."""
        with self._lock:
            own = self._own
        if not own or not self.run_startup:
            return
        if not s.take_startup():
            return

        def done(body, err):
            if err is not None:
                if not isinstance(err, C.ConnectionLost):
                    self._add_log(
                        S.LogEntry(self._now(), "error", "The project's startup code did not run: %s" % err)
                    )
                return
            self._startup_done(body if isinstance(body, dict) else {})

        try:
            s.request_async(
                "load",
                s.load_args(startup="auto", fast_startup=self.fast_startup),
                callback=done,
                timeout=C.LOAD_TIMEOUT,
            )
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "The project's startup code did not run: %s" % e))

    def _startup_done(self, body: Dict[str, Any]) -> None:
        """The load answer: each startup task (Outputs), the notes, or that it waits."""
        tasks = [t for t in body.get("startupTasks") or () if isinstance(t, dict)]
        for t in tasks:
            fn = str(t.get("function") or "?")
            task = t.get("task")
            self._add_log(
                S.LogEntry(
                    self._now(),
                    "log",
                    "Startup: %s runs as task %s" % (fn, task if task not in (None, "") else "?"),
                    function=fn,
                )
            )
        if not tasks and body.get("pending"):
            self._add_log(
                S.LogEntry(
                    self._now(), "log", "Startup: the project's startup code runs once window 0 shows a page."
                )
            )
        elif not tasks:
            self._add_log(S.LogEntry(self._now(), "log", "Startup: the project has no startup code."))
        for n in body.get("notes") or ():
            # (the notes about code that did not compile were logged at connect)
            if n and not is_compile_note(str(n)):
                self._add_log(S.LogEntry(self._now(), "log", "Startup: %s" % n))

    # -- what the window asks about the engine's windows (GUI thread) ------------------------
    @property
    def window(self) -> Optional[int]:
        """The engine window this tab shows (None until the page is open)."""
        with self._lock:
            return self._window

    def engine_windows(self) -> Tuple[int, List[Dict[str, Any]]]:
        """(serial, the engine's open windows): the last windowsChanged."""
        with self._lock:
            return self._windows_serial, [dict(w) for w in self._windows]

    def windows_before(self) -> Optional[frozenset]:
        """The windows of their own (no parent) that showed a page before this tab's page
        opened: another viewer's windows, never shown as this tab's popups (None: not known)."""
        with self._lock:
            return self._windows_before

    def window_closed(self) -> bool:
        """The engine freed this tab's window (WinFree, its parent's page changed)."""
        with self._lock:
            return self._window_gone

    def take_navigation(self) -> Optional[Dict[str, Any]]:
        """The page this tab's window shows now instead of the tab's page (a navigation), once:
        ``{window, page, path, hasModel}``; the window then shows it (``navigated``)."""
        with self._lock:
            nav, self._nav = self._nav, None
            # (a second windows list given while the tab followed the first: the page is
            # the tab's own now)
            if nav and self._same_page(str(nav.get("page") or "")):
                return None
            return nav

    def navigated(self, page_name: str, page_path: str, page, model) -> None:
        """The tab shows the page its window navigated to: states of that page are drawn
        again (the whole set first); its drawing summary goes to the engine. ``model`` = the
        model, or the helper's job that makes it (window.page_model_file_job: waited for on a
        thread of its own, then sent)."""
        self.fake.start(page_path, page)
        with self._lock:
            self.page_name = page_name
            self.page_path = page_path or self.page_path
            self.page = page
            self.model = model
            s, gen, window = self._session, self._gen, self._window
            self._other_page = ""
        if s is None or window is None:
            return

        def send():
            m = model
            if hasattr(m, "result"):
                m = m.result()
                with self._lock:
                    if self.model is model:
                        self.model = m
            if (
                m is not None
                and s.supports("pageModel") is not False
                and self._model_needed(s, window, page_name)
            ):
                try:
                    s.request_async(
                        "pageModel",
                        {"window": int(window), "model": m},
                        callback=self._quiet(
                            "pageModel", "The page's drawing data cannot go to this simulator."
                        ),
                    )
                except C.CtsimError:
                    pass
            try:
                s.request_async(
                    "pageState", {"window": int(window)}, callback=self._page_state_done(gen, s, window)
                )
            except C.CtsimError:
                pass

        if hasattr(model, "result") and not model.done():
            gcguard.before_worker()
            threading.Thread(target=send, daemon=True, name="ctsim-model").start()
        else:
            send()

    def reload(self, model: Optional[dict], page=None) -> None:
        """The page changed in the editor: open it again in its window with the new model
        (its rows run from now on; the page's open events run again)."""
        if page is not None:
            self.fake.start(self.page_path, page)
            self.page = page
        self.model = model
        with self._lock:
            s, gen, window = self._session, self._gen, self._window
            alive = s is not None and s.alive
            attached = self.attach_window is not None
            # an SA page started in its workspace: this tab shows the startup page (window
            # 0), the page itself is in its pane; the pane's own view (sa_pane) re-shows it
            # through the workspace too
            workspace = bool(self.workspace_page) and not attached
            pane = bool(getattr(self, "sa_pane", False)) and attached
            if s is not None and not workspace:
                self._live = False
                self._changes = {}
                self._replace_next = True
                # the states drawn stay until the page's new states come (not the demo's)
                self._held = alive and (window is not None or not attached)
        if not alive:
            return
        if workspace or pane:
            target = lambda: self._renavigate(s, gen, window if pane else None)  # noqa: E731
        elif attached:
            if window is None:
                return
            target = lambda: self._reopen_attached(s, gen, window)  # noqa: E731
        else:

            def target():
                self._open_page(s, gen, window)
                self._load_page_tags(s, gen)

        with self._lock:
            self._reload_seq += 1
            seq = self._reload_seq

        def run():
            # one reload at a time (two overlapping opens of the window would mix their states
            # and could end with the older page); a reload a newer one follows is left out: the
            # newer one sends the newest model (self.model, read when it runs)
            with self._reload_gate:
                with self._lock:
                    if seq != self._reload_seq:
                        return
                target()

        gcguard.before_worker()
        threading.Thread(target=run, daemon=True, name="ctsim-reload").start()

    def _reopen_attached(self, s: C.Session, gen: int, window: int) -> None:
        """Reload of a window the page's Cicode opened (a popup, a pinned window): its page
        opens again in that window with the new model and the window's associations (its
        open events run again)."""
        with self._lock:
            w = next((x for x in self._windows if x.get("window") == window), None)
        if w is None:
            try:
                w = next((x for x in s.windows(timeout=3.0) if x.get("window") == window), None)
            except C.CtsimError:
                w = None
        ass = w.get("associations") if isinstance(w, dict) else None
        state = None
        cut = None
        with self._lock:
            self._buffers = {}
        try:
            body, cut = self._open_request(
                s,
                s.open_page_args(
                    self.page_name, window, self._model_value(), ass if isinstance(ass, dict) else None
                ),
            )
            self._note_open(body)
            state = body.get("state")
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "%s did not open again: %s" % (self.page_name, e)))
        self._states_of(s, gen, window, state, cut=cut)
        self._load_page_tags(s, gen)

    def _renavigate(self, s: C.Session, gen: int, window: Optional[int]) -> None:
        """Reload of an SA page shown in its workspace pane: ctsim/navigate with the new model
        (the workspace shows it again, its rows compiled when the model is not current); the
        pane's view asks its states again. ``window`` = the pane this view shows (None: the
        tab of the startup page, which keeps showing window 0)."""
        try:
            nav = s.navigate(self.page_name, model=self._model_value(), timeout=C.LOAD_TIMEOUT)
        except C.CtsimError as e:
            self._add_log(
                S.LogEntry(
                    self._now(), "error", "%s did not show again in its workspace: %s" % (self.page_name, e)
                )
            )
            nav = {}
        for n in nav.get("notes") or ():
            if n:
                self._add_log(S.LogEntry(self._now(), "log", "Navigate: %s" % n))
        if window is None:
            return
        self._note_open(nav)
        pane = _int(nav.get("window"), -1)
        if pane > 0 and pane != window:
            # (the workspace showed it in another pane: this view follows that window)
            with self._lock:
                if gen != self._gen:
                    return
                self.attach_window = pane
                self._window = pane
            window = pane
        self._states_of(s, gen, window, None)
        self._load_page_tags(s, gen)

    def _states_of(self, s: C.Session, gen: int, window: int, state, cut: Optional[int] = None) -> None:
        """The window's whole state after a reload: ``state`` (an openPage answer's, ``cut``
        = the events delivered when it came) or a pageState."""
        with self._lock:
            if gen != self._gen:
                return
            if window != self._window:
                self._buffers = {w: b for w, b in self._buffers.items() if w == window}
            self._window = window
            self._window_gone = False
        if isinstance(state, dict) and "objects" in state:
            self._page_state_done(gen, s, window, cut=cut)(state, None)
            return
        try:
            s.request_async(
                "pageState",
                {"window": int(window)},
                callback=self._page_state_done(gen, s, window),
            )
        except C.CtsimError as e:
            self._fall_back(gen, "Demo values on the page: %s" % e)

    def _close_window(self) -> None:
        """closePage for this tab's window (its close events run in the engine)."""
        with self._lock:
            s, w = self._session, self._window
            gone = self._window_gone
            if w is None and self.attach_window is not None and s is not None:
                # (a window the page's Cicode opened, closed before its tab had shown it:
                # the tab still waited for the drawing, made in a helper process)
                w = self.attach_window
        if s is None or w is None or gone or not s.alive or s.supports("closePage") is False:
            return
        try:
            s.request_async("closePage", {"window": int(w)}, callback=lambda body, err: None)
        except C.CtsimError:
            pass

    def _page_state_done(self, gen: int, s: C.Session, window: int, cut: Optional[int] = None):
        """Take a window's whole state, then replay the events kept while waiting."""

        def done(body, err):
            if err is not None:
                retry = not isinstance(err, C.NotAvailable)
                if not retry:
                    why = "this simulator cannot run pages yet."
                else:
                    why = "the simulator did not send the page (%s)." % err
                with self._lock:
                    if gen != self._gen:
                        return
                    self._window = None
                    self._live = False
                    self._held = False
                self._status_connected(gen, s, page=False, why=why, retry=retry)
                return
            got = str(body.get("page") or "") if isinstance(body, dict) else ""
            if got and not self._same_page(got):
                self._page_taken(gen, window, got)
                return
            st = page_state_from_json(body, page=self.page_name, replace=True)
            st.window = window
            serial = _serial(body)
            # (a whole state lists every control the page has used: none = none)
            drawn = st.drawn
            controls = st.controls if st.controls is not None else {}
            flash = st.flash_on
            # one step under the lock: an event that comes meanwhile is either kept (taken
            # here) or drawn on top (the tab is live from here on), never lost in between
            with self._lock:
                if gen != self._gen or self._window != window:
                    return
                n = self._arrived if cut is None else cut
                buf = self._buffers.pop(window, None)
                if buf is not None and not self._same_page(buf.page):
                    buf = None

                def keep(arrival: int, ser: Optional[int]) -> bool:
                    if arrival > n:
                        return True  # came after the answer: newer
                    if ser is None or serial is None:
                        return False
                    return ser > serial

                later: Dict[int, ObjectState] = {}
                vars_body = None
                if buf is not None:
                    later, d, c, vars_body, f = buf.newer(keep)
                    drawn = d if d is not None else drawn
                    controls = c if c is not None else controls
                    flash = f if f is not None else flash
                self._take_page_vars(body, gen, window)
                if vars_body is not None:
                    self._take_page_vars(vars_body, gen, window)
                # the reply is the whole set; the events kept while it was asked that are
                # newer go on top of it
                self._full = st
                self._changes = later
                self._drawn_next = drawn
                self._controls_next = controls
                self._flash = flash
                self._live = True
                self._held = False
                self._base = serial
            self._status_connected(gen, s, page=True)
            self._pre_roll(s, gen, st)

        return done

    #: the longest a page's trends are filled by running the simulation ahead (seconds;
    #: about 70 simulated seconds per second of time: 300 s takes some 4 s)
    PRE_ROLL_MAX_S = 300.0

    def _pre_roll(self, s: C.Session, gen: int, st) -> None:
        """Fill trend history from the start, as the runtime's archive would."""
        with self._lock:
            if not self._own or self._pre_rolled or gen != self._gen or not self.seed_trend_history:
                return
        span = 0.0
        for o in (st.objects or {}).values():
            tr = getattr(o, "trend", None)
            if not isinstance(tr, dict):
                continue
            try:
                period = float(tr.get("period") or 0)
            except TypeError, ValueError:
                period = 0.0
            n = 0
            for p in tr.get("pens") or ():
                vals = p.get("values") if isinstance(p, dict) else p
                if isinstance(vals, list):
                    n = max(n, len(vals))
            span = max(span, period * n)
        secs = min(span, self.PRE_ROLL_MAX_S)
        if secs < 1.0:
            return  # (a page without trends: a later one may have some)
        with self._lock:
            if self._pre_rolled:
                return
            self._pre_rolled = True
        self._add_log(
            S.LogEntry(
                self._now(),
                "log",
                "Running the simulation %d s ahead so the trends have history." % int(secs),
            )
        )

        def done(_body, err):
            if err is not None and not isinstance(err, C.NotAvailable):
                self._add_log(S.LogEntry(self._now(), "log", "The trends have no history: %s" % err))

        try:
            s.request_async("clock", {"mode": "step", "value": float(secs)}, callback=done, timeout=120)
        except C.CtsimError:
            pass

    def reopen_in_new_window(self) -> bool:
        """The window this tab showed went on to another page (that page has a tab of its own
        now): open this tab's page again in a free window, so it keeps running (its entry
        code runs again). What is drawn stays until the new states come."""
        with self._lock:
            s, gen = self._session, self._gen
            if s is None or self._stopped:
                return False
            self._window = None
            self._live = False
            self._held = True
            self._changes = {}
            self._full = None
            self._other_page = ""
            self.attach_window = None
            # the navigation this tab handled was given again meanwhile (a second windows
            # list while the new tab opened): it is the new tab's now
            self._nav = None
        gcguard.before_worker()
        threading.Thread(
            target=self._open_page, args=(s, gen, None), daemon=True, name="ctsim-reopen"
        ).start()
        return True

    def stop(self) -> None:
        self._stopped = True
        self.fake.stop()
        self._close_window()
        self._drop_session()

    def _drop_session(self) -> None:
        with self._lock:
            self._gen += 1
            s, self._session = self._session, None
            self._debug_pause = None  # (its 'paused: false' will not come)
            fn, self._listener = self._listener, None
            closer, self._closer = self._closer, None
            viewer_of, self._viewer_of = self._viewer_of, None
            was_page = self._live
            self._window = None
            self._live = False
            self._full = None
            self._changes = {}
            self._buffers = {}
            self._opening = False
            self._base = None
            self._drawn_next = None
            self._controls_next = None
            self._page_tags = None
            self._page_vars = []
            self._search_rows = None
            self._search_done = self._search_want = None
            self._search_busy = False
            self._index = {}
            self._subscribed = ()
            self._clock = None
            self._tag_mode += 1
            self._windows = []
            self._windows_seen = False
            self._windows_before = None
            self._assoc_seen = None
            self._server_undefined = None
            self._nav = None
            self._other_page = ""
            self._window_gone = False
            self._held = False
            self._own = False
            self._alarm_want = self._alarm_done = None
            self._alarm_busy = False
            self._alarm_rows = None
            self._alarm_total = 0
            self._equip_want = self._equip_done = None
            self._equip_busy = False
            self._equip_rows = None
            self._equip_total = 0
            # what came from that server (a new one starts its lists over: its used tags from
            # 0, its parameters' serial again)
            self._used_for, self._used_from, self._used_rows = None, 0, []
            self._used_busy = False
            self._params, self._params_serial = [], -1
            self._params_busy = self._params_off = False
            if was_page:
                self._replace_next = True
        if was_page:
            self.fake.resend()
        if s is not None:
            if fn is not None:
                s.remove_listener(fn)
            if closer is not None:
                # a session another tab keeps would otherwise keep this tab (its page model)
                # alive through the closure
                s.remove_close_listener(closer)
        if viewer_of is not None:
            V.detach(viewer_of)
        if s is not None:
            self._hub.release(s)

    def _fall_back(self, gen: int, text: str, detail: str = "") -> None:
        """The connection ended: the page goes back to the demo values."""
        with self._lock:
            if gen != self._gen:
                return
        self._drop_session()
        with self._lock:
            self._status = S.SimStatus(text=text, level="warn", can_retry=True, detail=detail)
        # (Outputs keeps the event too: the banner goes with the next page)
        self._add_log(
            S.LogEntry(
                self._now(),
                "log",
                text.split(": ", 1)[-1][:1].upper() + text.split(": ", 1)[-1][1:],
                level="warning",
                category="simulator",
            )
        )

    def poll(self) -> Optional[PageState]:
        self._poll_params()
        with self._lock:
            if self._live:
                if self._full is not None:
                    st = self._full
                    st.objects.update(self._changes)
                    self._full = None
                elif (
                    self._changes
                    or self._drawn_next is not None
                    or self._controls_next is not None
                    or self._flash != self._flash_sent
                ):
                    self._page_serial += 1
                    st = PageState(
                        window=self._window,
                        page=self.page_name,
                        objects=self._changes,
                        flash_on=self._flash,
                        serial=self._page_serial,
                    )
                else:
                    return None
                self._changes = {}
                if self._drawn_next is not None:
                    st.drawn, self._drawn_next = self._drawn_next, None
                if self._controls_next is not None:
                    st.controls, self._controls_next = self._controls_next, None
                if self._replace_next:
                    st.replace = True
                    self._replace_next = False
                # (a phase change alone is news: colours and pictures that flash of their own
                # alternate on it, render/sim_states.py)
                self._flash_sent = st.flash_on
                return st
            if self._held:
                # the window shows another page for now: what is drawn stays (the demo values
                # are for a page the simulator does not run)
                return None
            replace = self._replace_next
        if not self.demo_values:
            return None  # (no simulator: the page as it is saved)
        st = self.fake.poll()
        if st is not None and replace:
            st.replace = True
            with self._lock:
                self._replace_next = False
        return st

    def set_listener(self, fn) -> None:
        """Push mode is not used: the window polls (events never reach Qt from the reader
        thread)."""
        self.fake.set_listener(None)

    # -- the clock ---------------------------------------------------------------------
    def clock(self, mode: str, value: Optional[float] = None) -> None:
        self.fake.clock(mode, value)
        if mode == "run":
            self._want_running = True
        elif mode in ("pause", "step"):
            self._want_running = False
        elif mode == "speed" and value:
            self._want_speed = float(value)
        with self._lock:
            s, gen = self._session, self._gen
        if s is not None and s.alive and not self._held_by_debugger("the clock"):
            self._send_clock(s, gen, mode, value)

    def _send_clock(self, s: C.Session, gen: int, mode: str, value=None) -> None:
        def done(body, err):
            if err is None:
                self._take_clock(C.clock_from(body, self._clock or s.clock), gen)
            elif isinstance(err, C.NotAvailable):
                self._note_once("clock", "The clock is not available in this Cicode simulator yet.")
            elif not isinstance(err, C.ConnectionLost):
                self._add_log(S.LogEntry(self._now(), "error", "Clock %s: %s" % (mode, err)))

        try:
            s.request_async("clock", C.Session.clock_args(mode, value), callback=done)
        except C.NotAvailable:
            self._note_once("clock", "The clock is not available in this Cicode simulator yet.")
        except C.CtsimError:
            pass

    def _take_clock(self, c: C.ClockInfo, gen: Optional[int] = None) -> None:
        if not c.known:
            return
        with self._lock:
            if gen is not None and gen != self._gen:
                return
            self._clock = c
            self._clock_at = time.monotonic()

    def clock_state(self) -> Optional[S.ClockState]:
        """The simulation's clock; None while there is none (no simulator and no demo values:
        the page only shows as saved)."""
        with self._lock:
            c, at = self._clock, self._clock_at
        if c is None:
            return self.fake.clock_state() if self.demo_values else None
        t = c.time
        if c.running:
            t += (time.monotonic() - at) * (c.speed or 1.0)
        # the engine reports time, not scans: the bar counts page scans of SCAN_SECONDS
        scan = max(c.scan, int(t / C.SCAN_SECONDS + 1e-9))
        return S.ClockState(running=c.running, speed=c.speed, scan=scan, time=t)

    def _now(self) -> float:
        cs = self.clock_state()
        return cs.time if cs is not None else 0.0

    # -- input -----------------------------------------------------------------------------
    def input(self, event: S.InputEvent) -> None:
        self.inputs.append(event)
        del self.inputs[:-200]
        if event.kind != "move" and self._held_by_debugger("a click or key"):
            return
        with self._lock:
            s, window = self._session, self._window
            live = self._live
        if s is None or window is None or not live or not s.alive:
            if event.kind == "move":
                return
            self.fake.input(event)
            if s is not None and event.kind in ("down", "key"):
                self._note_once(
                    "input", "Clicks and keys are not simulated: the Cicode simulator does not run this page."
                )
            return

        def done(body, err):
            if err is None or isinstance(err, C.ConnectionLost):
                return
            if isinstance(err, C.NotAvailable):
                self._note_once("input", "Clicks and keys are not available in this Cicode simulator yet.")
            else:
                self._note_once(
                    "input:" + event.kind, "Input '%s' was refused: %s" % (event.kind, err), kind="error"
                )

        if self._tools is not None:
            self._tools.note_input(window, event)
        try:
            s.request_async("input", C.Session.input_args(window, event), callback=done)
        except C.NotAvailable:
            self._note_once("input", "Clicks and keys are not available in this Cicode simulator yet.")
        except C.CtsimError:
            pass

    # -- tags --------------------------------------------------------------------------------
    def _load_page_tags(self, s: C.Session, gen: int) -> None:
        """The tags the page reads: the server takes them from the COMPILED page (its .RDB);
        a page that is not compiled has none (every tag can still be searched)."""
        with self._lock:
            # an SA page shown in its workspace pane: the pane's window, not the workspace's
            # master page
            window = self.focus_window if self.focus_window is not None else self._window
            self._tags_seq += 1
            seq = self._tags_seq
        by_window = window is not None
        try:
            if by_window:
                # the page the window shows, its '?name?' references resolved through the
                # window's associations (a popup's tags)
                with self._lock:
                    self._assoc_seen = self._assoc_of(window)
                try:
                    rows, _total = s.all_tags(window=window, limit=PAGE_TAG_LIMIT)
                except C.NotAvailable, C.ConnectionLost:
                    raise
                except C.CtsimError:
                    # (the window shows no page yet, or another one for now)
                    rows, _total = s.all_tags(page=self.page_name, limit=PAGE_TAG_LIMIT)
            else:
                rows, _total = s.all_tags(page=self.page_name, limit=PAGE_TAG_LIMIT)
        except C.NotAvailable:
            self._note_once("tags", "The tag list is not available in this Cicode simulator yet.")
            return
        except C.ConnectionLost:
            return
        except C.CtsimError as e:
            rows = []
            self._add_log(
                S.LogEntry(
                    self._now(),
                    "log",
                    "The tags this page reads are not "
                    "known: %s. Clear 'Only tags this page reads' to search "
                    "every tag." % _sentence(e).rstrip("."),
                )
            )
        tags = [_tag_info(r, page_reads=True) for r in rows]
        with self._lock:
            # (a newer list was asked meanwhile: a reload, another window; this answer may be
            # of the window or page before, and may come after the newer one)
            if gen != self._gen or seq != self._tags_seq:
                return
            have = {t.name.lower() for t in tags}
            # the tags the code used, of this window and page only (a navigation's page
            # list may come before the used tags of the page before are let go)
            if self._used_for == self._used_key():
                tags += [t for t in self._used_rows if t.name.lower() not in have]
            self._page_tags = tags
            self._index = {}
            for t in tags:
                self._index.setdefault(t.name.lower(), []).append(t)
            self._tag_mode += 1
            self._tag_serial += 1
        self._subscribe(s, gen)

    def _assoc_of(self, window: Optional[int]) -> Optional[str]:
        """The page and the associations a window shows (the last windowsChanged), as one
        text to compare; None when the windows are not known yet. Lock held by the caller."""
        if window is None or not self._windows_seen:
            return None
        w = next((x for x in self._windows if x.get("window") == window), None)
        if w is None:
            return None
        ass = w.get("associations")
        items = sorted((str(k), str(v)) for k, v in ass.items()) if isinstance(ass, dict) else []
        return "%s|%r" % (str(w.get("page") or "").lower(), items)

    def _subscribe(self, s: C.Session, gen: int) -> None:
        with self._lock:
            names = tuple(
                sorted({t.name for rows in (self._page_tags or (), self._search_rows or ()) for t in rows})
            )
            if names == self._subscribed:
                return
            self._subscribed = names
        quiet = self._quiet(
            "subscribe",
            "Tag values do not follow the simulator: "
            "subscribing is not available in this Cicode simulator yet.",
        )

        def done(body, err):
            if err is None:
                # the reply carries the values now (the events carry the changes)
                self._apply_values(C.tag_changes_from(body), gen)
            quiet(body, err)

        try:
            s.request_async("subscribe", C.Session.subscribe_args(names, replace=True), callback=done)
        except C.NotAvailable:
            pass
        except C.CtsimError:
            pass

    def _quiet(self, key: str, unavailable: str):
        def done(body, err):
            if isinstance(err, C.NotAvailable):
                self._note_once(key, unavailable)
            elif err is not None and not isinstance(err, C.ConnectionLost):
                self._add_log(S.LogEntry(self._now(), "error", "%s: %s" % (key, err)))

        return done

    def tags(self, page_only: bool = False) -> List[S.TagInfo]:
        rows, _total = self.search_tags("", page_only)
        return rows

    def search_tags(
        self, text: str, page_only: bool = False, regex: bool = False
    ) -> Tuple[List[S.TagInfo], Optional[int]]:
        """The tags for the Inputs panel, without waiting."""
        m = TextMatch(text, regex)
        # (copies, made under the lock: copy.copy, not dataclasses.replace, which runs the
        # constructor for every tag at each poll)
        with self._lock:
            page_tags = self._page_tags
            pvars = [copy.copy(t) for t in self._page_vars + self._params if m(t.name)]
            if page_tags is None:
                fake = True
            else:
                fake = False
                if page_only:
                    rows = [copy.copy(t) for t in page_tags if m.empty or S.tag_matches(t, m)]
                    return rows + pvars, len(rows)
                want = (m.text, m.regex)
                if not m.ok:
                    return pvars, 0  # (the box says why: nothing matches)
                if want != self._search_done and want != self._search_want:
                    self._search_want = want
                    kick = not self._search_busy
                else:
                    kick = False
                rows_src = self._search_rows
                total = self._search_total if rows_src is not None else None
                if rows_src is None:
                    rows_src = page_tags
                rows = [copy.copy(t) for t in rows_src]
        if fake:
            demo = self.fake.tags(page_only) if self.demo_values else []
            return [t for t in demo if S.tag_matches(t, m)] + pvars, None
        if kick:
            self._search_next()
        return rows + pvars, total

    def _search_next(self) -> None:
        with self._lock:
            s, gen = self._session, self._gen
            want = self._search_want
            if s is None or want is None or self._search_busy:
                return
            self._search_busy = True
        text, regex = want
        limit = _tag_rows()

        def done(body, err):
            with self._lock:
                if gen != self._gen:
                    return
                self._search_busy = False
                if err is None:
                    tp = C.tag_page_from(body)
                    page = {t.name.lower(): t for t in self._page_tags or ()}
                    rows = []
                    for d in tp.tags:
                        known = page.get(d["name"].lower())
                        rows.append(_tag_info(d, page_reads=True if known is not None else None))
                    total = tp.total
                    self._search_rows = rows
                    self._search_total = total
                    self._search_done = want
                    self._index = {}
                    for t in list(self._page_tags or ()) + rows:
                        self._index.setdefault(t.name.lower(), []).append(t)
                    self._tag_serial += 1
                elif not isinstance(err, C.ConnectionLost):
                    self._search_done = want
                again = self._search_want != self._search_done
                s2 = self._session
            if (
                err is not None
                and not isinstance(err, C.ConnectionLost)
                and not (regex and isinstance(err, C.RequestFailed))
            ):
                # (a regular expression the simulator refused: the box says so already)
                self._add_log(S.LogEntry(self._now(), "error", "Tag search: %s" % err))
            if again:
                self._search_next()
            elif err is None and s2 is not None:
                self._subscribe(s2, gen)

        try:
            s.request_async(
                "tags",
                C.Session.tag_args(text or None, None, 0, limit, regex=bool(regex)),
                callback=done,
                timeout=C.TAGS_TIMEOUT,
            )
        except C.CtsimError as e:
            with self._lock:
                self._search_busy = False
                self._search_done = want
            self._add_log(S.LogEntry(self._now(), "error", "Tag search: %s" % e))

    def set_tag(self, name: str, value: Any, quality: Optional[str] = None) -> None:
        if self._held_by_debugger("a tag write"):
            return
        self.user_writes = getattr(self, "user_writes", 0) + 1  # (SimulateWindow.confirm_close)
        with self._lock:
            s, gen = self._session, self._gen
            # a connected simulator takes the write also before its tag list arrived (the
            # page opens first)
            engine = s is not None and s.alive
            undefined = None
            if engine:
                for t in self._index.get(name.lower(), ()):
                    if not t.defined and not t.simulated:
                        undefined = t
                    t.value = value
                    if quality:
                        t.quality = quality
                self._tag_serial += 1
        if not engine:
            self.fake.set_tag(name, value, quality)
            return
        self._note_write(name, value, quality or "Good")
        if self._tools is not None:
            self._tools.note_write(name, value, quality)

        def done(body, err):
            if err is None:
                t = C.written_tag(body, name)
                if "value" in t and (isinstance(body, dict) and ("tag" in body or "value" in body)):
                    self._apply_values([dict(t, name=name)], gen)
                if undefined is not None:
                    # the simulator made a tag for the undefined name: list it as simulated
                    self._reload_tags(gen)
                return
            if isinstance(err, C.ConnectionLost):
                return
            self._add_log(S.LogEntry(self._now(), "error", "%s was not set: %s" % (name, _write_error(err))))

        try:
            s.request_async(
                "setTag",
                C.Session.set_tag_args(
                    name, value, quality, undefined.type if undefined is not None else None
                ),
                callback=done,
            )
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "%s was not set: %s" % (name, e)))

    # -- ActiveX controls (engine/PROTOCOL.md ctsim/setObjectProperty) ------------------------
    def set_object_property(self, an: int, prop: str, value: str) -> bool:
        """The user typed in an ActiveX control of the page (a text box): the engine's
        property store takes the value, the page's code reads it (_ObjectGetProperty). False
        when the simulator does not run this page or cannot take it."""
        with self._lock:
            s, window, live = self._session, self._window, self._live
        if s is None or window is None or not live or not s.alive:
            return False

        def done(body, err):
            if err is None or isinstance(err, C.ConnectionLost):
                return
            self._note_once(
                "setObjectProperty:%d" % an,
                "The text typed in AN %d was not taken: %s" % (an, err),
                kind="error",
            )

        try:
            s.request_async(
                "setObjectProperty",
                {"window": int(window), "an": int(an), "property": str(prop), "value": str(value)},
                callback=done,
            )
        except C.CtsimError:
            return False
        return True

    # -- trend history (engine/PROTOCOL.md ctsim/options seedTrendHistory) -------------------
    def set_seed_trend_history(self, on: bool) -> None:
        """Simulate Settings > Show trend history from the start changed: sent at once (a
        server option every client of the simulation shares; on connect only the tab that
        drives a server ctgedit started sends it, and only to turn it on)."""
        self.seed_trend_history = bool(on)
        with self._lock:
            s, gen = self._session, self._gen
        if s is not None and s.alive:
            self._send_seed(s, gen, bool(on))

    def _send_seed(self, s: C.Session, gen: int, on: bool) -> None:

        def done(body, err):
            if err is not None and not isinstance(err, C.ConnectionLost):
                self._add_log(S.LogEntry(self._now(), "error", "Trend history: %s" % err))

        try:
            s.request_async("options", {"seedTrendHistory": bool(on)}, callback=done)
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "Trend history: %s" % e))

    # -- DLLs and SQL databases (engine/PROTOCOL.md ctsim/options dll / sql) -------------
    def outside_settings(self) -> tuple:
        return (
            self.dll_access,
            self.sql_access,
            tuple(self.dll_allowed),
            tuple(self.sql_allowed),
            self.dll_timeout,
        )

    def set_outside(self, dll: str, sql: str, dll_allowed, sql_allowed, dll_timeout: int = 0) -> None:
        """Simulate Settings > DLLs and databases changed: sent at once (a server option every
        client of the simulation shares; on connect only the tab that drives a server ctgedit
        started sends it). ``dll_timeout``: seconds a DLL call may take, 0 = no limit."""
        self.dll_access = dll if dll in ("off", "ask", "on") else "off"
        self.sql_access = sql if sql in ("off", "ask", "on") else "off"
        self.dll_allowed = [str(x) for x in dll_allowed]
        self.sql_allowed = [str(x) for x in sql_allowed]
        try:
            self.dll_timeout = max(0, int(dll_timeout))
        except TypeError, ValueError:
            self.dll_timeout = 0
        with self._lock:
            s, gen = self._session, self._gen
        if s is not None and s.alive:
            self._send_outside(s, gen)

    def set_outside_allowed(self, dll_allowed, sql_allowed) -> None:
        """Always Allow put a DLL or database on an allowed list: the lists go to the
        simulation as they are, the modes stay as the simulation has them (a simulation
        another program started keeps its own modes; ctgedit's may be Off)."""
        self.dll_allowed = [str(x) for x in dll_allowed]
        self.sql_allowed = [str(x) for x in sql_allowed]
        with self._lock:
            s, gen = self._session, self._gen
        if s is not None and s.alive:
            self._send_outside(s, gen, lists_only=True)

    def _send_outside(self, s: C.Session, gen: int, lists_only: bool = False) -> None:
        args = {"dllAllowed": list(self.dll_allowed), "sqlAllowed": list(self.sql_allowed)}
        if not lists_only:
            args.update({"dll": self.dll_access, "sql": self.sql_access, "dllTimeout": int(self.dll_timeout)})

        def done(body, err):
            if err is not None and not isinstance(err, C.ConnectionLost):
                self._add_log(S.LogEntry(self._now(), "error", "DLLs and databases: %s" % err))

        try:
            s.request_async("options", args, callback=done)
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "DLLs and databases: %s" % e))

    # -- tag property overrides (engine/PROTOCOL.md ctsim/setTagProperty) ------------------
    def can_set_tag_properties(self) -> bool:
        """A simulator is connected (it overrides tag properties)."""
        with self._lock:
            s = self._session
        return bool(s is not None and s.alive)

    def set_tag_property(
        self,
        name: str,
        field: Optional[str],
        value: Optional[str] = None,
        done: Optional[Callable[[Optional[str]], None]] = None,
    ) -> bool:
        """Override a definition field of tag ``name`` for the simulation (``value`` None =
        the project's again; ``field`` None = every field back). ``done(error)`` on the reader
        thread (None = it took it). False when it cannot be sent."""
        if self._held_by_debugger("a tag property"):
            return False
        with self._lock:
            s, gen = self._session, self._gen
        if s is None or not s.alive:
            return False

        def answered(body, err):
            if err is None and isinstance(body, dict) and body.get("name"):
                n = C.normalise_tag(body)
                if n is not None:
                    with self._lock:
                        if gen == self._gen:
                            for t in self._index.get(str(body["name"]).lower(), ()):
                                fresh = _tag_info(n, page_reads=t.page_reads)
                                for f in dataclasses.fields(fresh):
                                    if f.name not in (
                                        "page_reads",
                                        "via",
                                        "ref",
                                        "page_var",
                                        "param",
                                        "source",
                                        "users",
                                    ):
                                        setattr(t, f.name, getattr(fresh, f.name))
                            self._tag_serial += 1
            elif err is not None and not isinstance(err, C.ConnectionLost):
                self._add_log(
                    S.LogEntry(self._now(), "error", "%s: the property was not set: %s" % (name, err))
                )
            if done is not None:
                try:
                    done(None if err is None else str(err))
                except Exception:  # noqa: BLE001 -- a caller's bug
                    logsetup.note_exception("sim.ctsim_source.set_tag_property")

        try:
            s.request_async(
                "setTagProperty", C.Session.tag_property_args(name, field, value), callback=answered
            )
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "%s: the property was not set: %s" % (name, e)))
            return False
        return True

    # -- page code stops in the debugger (engine/PROTOCOL.md ctsim/options foregroundBreak) ----
    def set_foreground_break(self, on: bool) -> None:
        """Simulate Settings > Stop at breakpoints in page code changed: sent at once (a server
        option every client of the simulation shares)."""
        self.foreground_break = bool(on)
        with self._lock:
            s, gen = self._session, self._gen
        if s is not None and s.alive:
            self._send_foreground_break(s, gen, bool(on))

    def set_foreground_error_break(self, on: bool) -> None:
        """Simulate Settings > Stop page code on its hardware errors changed: sent at once."""
        self.foreground_error_break = bool(on)
        with self._lock:
            s, gen = self._session, self._gen
        if s is not None and s.alive:
            self._send_foreground_error_break(s, gen, bool(on))

    def _send_foreground_error_break(self, s: C.Session, gen: int, on: bool) -> None:

        def done(body, err):
            if err is not None and not isinstance(err, C.ConnectionLost):
                self._add_log(S.LogEntry(self._now(), "error", "Stop on page code errors: %s" % err))

        try:
            s.request_async("options", {"foregroundErrorBreak": bool(on)}, callback=done)
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "Stop on page code errors: %s" % e))

    def _send_foreground_break(self, s: C.Session, gen: int, on: bool) -> None:

        def done(body, err):
            if err is not None and not isinstance(err, C.ConnectionLost):
                self._add_log(S.LogEntry(self._now(), "error", "Stop in page code: %s" % err))

        try:
            s.request_async("options", {"foregroundBreak": bool(on)}, callback=done)
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "Stop in page code: %s" % e))

    # -- simulate undefined tags (engine/PROTOCOL.md ctsim/options) ---------------------------
    def set_simulate_undefined(self, on: bool, user: bool = False) -> None:
        """Whether a value set for a tag the project does not define makes a simulated tag
        (a server option: every client of the simulation shares it). ``user`` = the user
        changed it now (sent also to turn it off); else only remembered, and sent on connect
        when it is on."""
        with self._lock:
            self._want_undefined = bool(on)
            s, gen = self._session, self._gen
            server = self._server_undefined
        if s is None or not s.alive:
            return
        if user or (on and server is not True):
            self._send_options(s, gen, bool(on))

    # -- the simulated user (engine/PROTOCOL.md ctsim/user, ctsim/users) ------------------------
    def user_state(self) -> Tuple[int, Dict[str, Any], List[Dict[str, Any]]]:
        """(serial, the user now {mode, name, fullName}, the project's users [{name,
        fullName}]); the serial changes with either."""
        with self._lock:
            return self._user_serial, dict(self._user), [dict(u) for u in self._users]

    def set_user(self, choice: str) -> None:
        """Log the simulation in: 'full' (every privilege), 'none' (nobody) or 'user:<name>'."""
        with self._lock:
            self.want_user = choice
            s, gen = self._session, self._gen
        if s is not None and s.alive:
            self._send_user(s, gen, choice)

    def _send_user(self, s: C.Session, gen: int, choice: str) -> None:
        mode, _sep, name = (choice or "full").partition(":")
        args: Dict[str, Any] = {"mode": mode}
        if mode == "user":
            args["name"] = name

        def done(body, err):
            if err is None:
                self._take_user(body, gen)
            elif not isinstance(err, (C.ConnectionLost, C.NotAvailable)):
                self._add_log(S.LogEntry(self._now(), "error", "User: %s" % err))

        try:
            s.request_async("user", args, callback=done)
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "User: %s" % e))

    def _ask_users(self, s: C.Session, gen: int) -> None:

        def done(body, err):
            if err is not None or not isinstance(body, dict):
                return
            users = [u for u in body.get("users") or () if isinstance(u, dict) and u.get("name")]
            with self._lock:
                if gen != self._gen:
                    return
                self._users = users
                self._user_serial += 1
            self._take_user(body.get("user"), gen)

        try:
            s.request_async("users", {}, callback=done)
        except C.CtsimError:
            pass

    def _take_user(self, body: Any, gen: int) -> None:
        if not isinstance(body, dict) or not body.get("mode"):
            return
        with self._lock:
            if gen != self._gen:
                return
            if body != self._user:
                self._user = dict(body)
                self._user_serial += 1
                if body.get("mode") == "user":
                    self.want_user = "user:" + str(body.get("name") or "")
                else:
                    self.want_user = str(body.get("mode"))

    # -- locks (engine/PROTOCOL.md ctsim/lockTag, setPageVar {locked}) --------------------------
    def set_locked(
        self, name: str, locked: bool, page_var: bool = False, type_: str = "", value: Any = None
    ) -> None:
        """Lock a tag (or a page variable of the tab's window): Cicode cannot change it, the
        Inputs panel still can."""
        with self._lock:
            s, gen, window = self._session, self._gen, self._window
            rows = self._page_vars if page_var else self._index.get(name.lower(), ())
            for t in rows:
                if t.name == name or not page_var:
                    t.locked = bool(locked)
                    if page_var:
                        type_ = type_ or ("string" if t.is_string() else "int")
                        value = t.value if value is None else value
            self._tag_serial += 1
        if s is None or not s.alive:
            return
        verb = "locked" if locked else "unlocked"
        self._add_log(
            S.LogEntry(self._now(), "write", "%s%s %s" % ("page variable " if page_var else "", name, verb))
        )

        def done(body, err):
            if err is None:
                if page_var:
                    self._take_page_vars({"pageVars": (body or {}).get("vars")}, gen, window)
                return
            if isinstance(err, C.NotAvailable):
                self._note_once("lock", "This simulator cannot lock values.")
            elif not isinstance(err, C.ConnectionLost):
                self._add_log(S.LogEntry(self._now(), "error", "%s was not %s: %s" % (name, verb, err)))

        try:
            if page_var:
                if window is None:
                    return
                args = {
                    "window": window,
                    "name": name,
                    "value": value if value is not None else 0,
                    "locked": bool(locked),
                }
                if type_:
                    args["type"] = type_
                s.request_async("setPageVar", args, callback=done)
            else:
                s.request_async("lockTag", {"name": name, "locked": bool(locked)}, callback=done)
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "%s was not %s: %s" % (name, verb, e)))

    def simulating_undefined(self) -> Optional[bool]:
        """What the server said last (None: not connected or it did not say)."""
        with self._lock:
            return self._server_undefined if self._session is not None else None

    def _send_options(self, s: C.Session, gen: int, on: bool) -> None:
        if s.supports("options") is False:
            self._note_once("options", "This Cicode simulator cannot simulate undefined tags yet.")
            return

        def done(body, err):
            if err is None:
                self._take_options(body, gen)
            elif isinstance(err, C.NotAvailable):
                self._note_once("options", "This Cicode simulator cannot simulate undefined tags yet.")
            elif not isinstance(err, C.ConnectionLost):
                self._add_log(S.LogEntry(self._now(), "error", "Simulate undefined tags: %s" % err))

        try:
            s.request_async("options", {"simulateUndefined": bool(on)}, callback=done)
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "Simulate undefined tags: %s" % e))

    def _take_options(self, body: Any, gen: int) -> None:
        if not isinstance(body, dict) or not isinstance(body.get("simulateUndefined"), bool):
            return
        with self._lock:
            if gen != self._gen:
                return
            changed = self._server_undefined != body["simulateUndefined"]
            self._server_undefined = body["simulateUndefined"]
            if changed:
                self._tag_serial += 1
        if changed:
            # simulated tags appear / disappear in the page's list
            self._reload_tags(gen)

    def _apply_values(self, changes, gen: int) -> None:
        with self._lock:
            if gen != self._gen:
                return
            hit = False
            for ch in changes:
                for t in self._index.get(ch["name"].lower(), ()):
                    t.value = ch.get("value")
                    if ch.get("quality"):
                        t.quality = ch["quality"]
                    hit = True
            if hit:
                self._tag_serial += 1

    def serial(self):
        with self._lock:
            if self._page_tags is None:
                return (self._tag_mode, "demo", self.fake.serial())
            return (self._tag_mode, "engine", self._tag_serial)

    # -- alarms and equipment (engine/PROTOCOL.md ctsim/alarms, ctsim/equipment) -------------
    #: alarms one list asks for at most
    ALARM_LIMIT = 2000
    #: equipment fields change without an event (EquipSetProperty): asked again this often
    EQUIP_EVERY = 2.0

    def alarms_note(self) -> str:
        """Why the Alarms / Equipment panels are empty ('' when they work or wait)."""
        with self._lock:
            s = self._session
        if s is None or not s.alive:
            return "The Cicode simulator is not connected."
        if self._no_alarms(s):
            return "This Cicode simulator cannot list alarms and equipment."
        return ""

    @staticmethod
    def _no_alarms(s: Optional[C.Session]) -> bool:
        return s is None or s.supports("alarms") is False

    def alarm_rows(
        self, text: str = "", page_only: bool = False, regex: bool = False
    ) -> Tuple[Optional[List[Dict[str, Any]]], int]:
        """The alarms for the Alarms panel, without waiting."""
        with self._lock:
            s = self._session
            if s is None or not s.alive or self._no_alarms(s):
                return None, 0
            # (an SA page in its workspace: the pane's window, as the tag list)
            window = (
                (self.focus_window if self.focus_window is not None else self._window) if page_only else None
            )
            key = (
                (text or "").strip(),
                bool(page_only),
                window,
                (self.page_name or "").lower() if page_only else "",
                self._alarm_serial,
                bool(regex),
            )
            if not TextMatch(text, regex).ok:
                return [], 0  # (the box says why: nothing matches)
            kick = False
            if key != self._alarm_done and key != self._alarm_want:
                self._alarm_want = key
                kick = not self._alarm_busy
            rows = None if self._alarm_rows is None else [dict(a) for a in self._alarm_rows]
            total = self._alarm_total
        if kick:
            self._alarm_next()
        return rows, total

    def _alarm_next(self) -> None:
        with self._lock:
            s, gen, want = self._session, self._gen, self._alarm_want
            if s is None or want is None or self._alarm_busy:
                return
            self._alarm_busy = True
        text, page_only, window, page, _serial, regex = want

        def done(body, err):
            with self._lock:
                if gen != self._gen:
                    return
                self._alarm_busy = False
                self._alarm_done = want
                if err is None:
                    al = C.alarm_list_from(body)
                    rows, total = al.alarms, al.total
                    self._alarm_rows = rows
                    self._alarm_total = total
                elif isinstance(err, C.NotAvailable):
                    self._alarm_rows = []
                    self._alarm_total = 0
                again = self._alarm_want != self._alarm_done
            if (
                err is not None
                and not isinstance(err, (C.ConnectionLost, C.NotAvailable))
                and not (regex and isinstance(err, C.RequestFailed))
            ):
                self._note_once("alarms:" + str(err)[:60], "Alarms: %s" % err, kind="error")
            if again:
                self._alarm_next()

        args = C.Session.alarms_args(
            filter=text or None,
            window=window if page_only and window is not None else None,
            page=self.page_name if page_only and window is None else None,
            count=self.ALARM_LIMIT,
            regex=bool(regex),
        )
        try:
            s.request_async("alarms", args, callback=done, timeout=C.TAGS_TIMEOUT)
        except C.CtsimError as e:
            with self._lock:
                self._alarm_busy = False
                self._alarm_done = want
            if not isinstance(e, C.NotAvailable):
                self._add_log(S.LogEntry(self._now(), "error", "Alarms: %s" % e))

    def set_alarm(self, tag: str, state: str) -> None:
        """Set an alarm's state on the simulated alarm server (``ctsim/setAlarm``: on,
        on_ack, off_unack, off, disabled); it holds until one of the alarm's inputs changes."""
        with self._lock:
            s, gen = self._session, self._gen
            # shown at once in the list's own words (ctsim/alarms 'state'), until the
            # simulator's answer
            listed = LISTED_STATE.get(state, state)
            for a in self._alarm_rows or ():
                if a["tag"] == tag:
                    a["state"] = listed
        if s is None or not s.alive:
            return
        self._add_log(
            S.LogEntry(self._now(), "write", "alarm %s = %s" % (tag, ALARM_STATE_TEXT.get(state, state)))
        )

        def done(body, err):
            with self._lock:
                if gen == self._gen:
                    self._alarm_serial += 1  # ask again (an alarmsChanged follows too)
            if err is not None and not isinstance(err, C.ConnectionLost):
                self._add_log(
                    S.LogEntry(self._now(), "error", "Alarm %s was not set: %s" % (tag, _write_error(err)))
                )

        try:
            s.request_async("setAlarm", C.Session.set_alarm_args(tag, state), callback=done)
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "Alarm %s was not set: %s" % (tag, e)))

    def equipment_rows(
        self, text: str = "", regex: bool = False
    ) -> Tuple[Optional[List[Dict[str, Any]]], int]:
        """The equipment for the Equipment panel without waiting (copies), with MODE / STATE /
        DRMODE as EquipGetProperty answers them; asked again every EQUIP_EVERY seconds while
        the panel shows them, and after a change."""
        with self._lock:
            s = self._session
            if s is None or not s.alive or self._no_alarms(s):
                return None, 0
            key = (
                (text or "").strip(),
                self._equip_serial,
                int(time.monotonic() / self.EQUIP_EVERY),
                bool(regex),
            )
            if not TextMatch(text, regex).ok:
                return [], 0
            kick = False
            if key != self._equip_done and key != self._equip_want:
                self._equip_want = key
                kick = not self._equip_busy
            rows = None if self._equip_rows is None else [dict(e) for e in self._equip_rows]
            total = self._equip_total
        if kick:
            self._equip_next()
        return rows, total

    def _equip_next(self) -> None:
        with self._lock:
            s, gen, want = self._session, self._gen, self._equip_want
            if s is None or want is None or self._equip_busy:
                return
            self._equip_busy = True
        text, regex = want[0], want[3]

        def done(body, err):
            with self._lock:
                if gen != self._gen:
                    return
                self._equip_busy = False
                self._equip_done = want
                if err is None:
                    el = C.equipment_list_from(body)
                    rows, total = el.equipment, el.total
                    self._equip_rows = rows
                    self._equip_total = total
                elif isinstance(err, C.NotAvailable):
                    self._equip_rows = []
                    self._equip_total = 0
                again = self._equip_want != self._equip_done
            if (
                err is not None
                and not isinstance(err, (C.ConnectionLost, C.NotAvailable))
                and not (regex and isinstance(err, C.RequestFailed))
            ):
                self._note_once("equipment:" + str(err)[:60], "Equipment: %s" % err, kind="error")
            if again:
                self._equip_next()

        try:
            s.request_async(
                "equipment",
                C.Session.equipment_args(
                    text or None,
                    count=self.ALARM_LIMIT,
                    regex=bool(regex),
                ),
                callback=done,
                timeout=C.TAGS_TIMEOUT,
            )
        except C.CtsimError as e:
            with self._lock:
                self._equip_busy = False
                self._equip_done = want
            if not isinstance(e, C.NotAvailable):
                self._add_log(S.LogEntry(self._now(), "error", "Equipment: %s" % e))

    def set_equipment(self, name: str, field: str, value: str) -> None:
        """Set one runtime field (MODE, STATE or DRMODE) of an equipment
        (``ctsim/setEquipment``), as the scheduler or EquipSetProperty would."""
        with self._lock:
            s, gen = self._session, self._gen
            for e in self._equip_rows or ():
                if e["name"] == name:
                    e[field] = value
        if s is None or not s.alive:
            return
        self._add_log(S.LogEntry(self._now(), "write", "equipment %s %s = %s" % (name, field, value)))

        def done(body, err):
            with self._lock:
                if gen == self._gen:
                    self._equip_serial += 1
            if err is not None and not isinstance(err, C.ConnectionLost):
                self._add_log(
                    S.LogEntry(
                        self._now(), "error", "%s %s was not set: %s" % (name, field, _write_error(err))
                    )
                )

        try:
            s.request_async("setEquipment", C.Session.set_equipment_args(name, {field: value}), callback=done)
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "%s %s was not set: %s" % (name, field, e)))

    def page_equipment(self) -> List[str]:
        """The equipment the tab's page names: its tags' equipment and the equipment of the
        page's alarms (the Equipment panel's 'Only equipment of this page')."""
        with self._lock:
            names = {t.equipment for t in self._page_tags or () if t.equipment}
            done = self._alarm_done
            if done is not None and done[1]:
                names |= {a["equipment"] for a in self._alarm_rows or () if a["equipment"]}
        return sorted(names, key=str.lower)

    # -- the log -----------------------------------------------------------------------------
    def _add_log(self, entry: S.LogEntry) -> None:
        with self._lock:
            self._log.append(entry)
            keep = _log_lines()  # (any value of its span)
            if len(self._log) > keep:
                del self._log[: len(self._log) - keep]

    def _note_once(self, key: str, text: str, kind: str = "log") -> None:
        with self._lock:
            if key in self._reported:
                return
            self._reported.add(key)
        self._add_log(S.LogEntry(self._now(), kind, text))

    #: a tag's writes are one log line once no new write came for this long (seconds)
    WRITE_SETTLE_S = 0.8

    def _note_write(self, name: str, value: Any, quality: str) -> None:
        """Remember a tag write for the log: the writes of one tag that come close together
        (a slider drag) become one line, logged once they settle (:meth:`_flush_writes`)."""
        now = time.monotonic()
        at = self._now()  # (the simulation time: before the lock, it takes it)
        with self._lock:
            # one step: a flush between two (the window's drain) found the entry half made
            p = self._writes_pending.get(name.lower())
            if p is None or p[3] != quality:
                if p is not None:
                    self._log_write(p)
                self._writes_pending[name.lower()] = [name, value, value, quality, 1, now, at]
            else:
                p[2], p[4], p[5] = value, p[4] + 1, now

    def _log_write(self, p: list) -> None:
        """The log line of a pending write (lock held)."""
        name, first, last, q, n, _at, t = p
        text = "%s = %s%s" % (name, last, "" if q == "Good" else " (%s)" % q.lower())
        if n > 1:
            text += " (%d writes, from %s)" % (n, first)
        self._log.append(S.LogEntry(t if t is not None else 0.0, "write", text))

    def _flush_writes(self, everything: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            for key in [
                k for k, p in self._writes_pending.items() if everything or now - p[5] >= self.WRITE_SETTLE_S
            ]:
                self._log_write(self._writes_pending.pop(key))

    def drain(self) -> List[S.LogEntry]:
        demo = self.fake.drain()
        self._flush_writes()
        self._flush_task_burst()
        self._sync_task_events()
        with self._lock:
            out, self._log = self._log, []
            connected = self._session is not None
        if len(out) > DRAIN_MAX:
            left = len(out) - DRAIN_MAX
            out = [
                S.LogEntry(
                    out[0].time,
                    "log",
                    "%d earlier messages were left out (too many at once)" % left,
                    category="simulator",
                )
            ] + out[-DRAIN_MAX:]
        return out if connected else demo + out

    def _sync_task_events(self) -> None:
        """Ask the server for task events only while Outputs shows tasks (a second hello,
        on a worker thread; at most every 2 s)."""
        now = time.monotonic()
        if now - getattr(self, "_events_checked", 0.0) < 2.0:
            return
        self._events_checked = now
        want = _tasks_shown()
        C.viewer_events = None if want else tuple(k for k in C.EVENT_KINDS if k != "task")
        s = self._session
        kinds = C.viewer_events
        if (
            s is None
            or getattr(s, "events_kinds", None) == kinds
            or getattr(self, "_events_sent", None) == (id(s), want)
        ):
            return  # (what its hello asked for already)
        self._events_sent = (id(s), want)

        def send():
            gcguard.before_worker()
            s.set_events(kinds)

        threading.Thread(target=send, name="ctsim-events", daemon=True).start()

    def _flush_task_burst(self) -> None:
        """The line for the task events a busy second left out."""
        with self._lock:
            b = getattr(self, "_task_burst", None)
            if not b or not b[2] or time.monotonic() - b[0] < 1.0:
                return
            self._task_burst = None
        start, _n, left, fns = b
        top = sorted(fns.items(), key=lambda kv: -kv[1])[:3]
        names = ", ".join("%s %d" % (f or "?", n) for f, n in top)
        self._add_log(
            S.LogEntry(
                self._now(),
                "log",
                "%d more task events in one second%s" % (left, " (%s)" % names if names else ""),
                category="task",
            )
        )

    def not_simulated(self) -> Dict[str, int]:
        return {}

    # -- events (the client's reader thread) --------------------------------------------------
    def _event_listener(self, gen: int) -> Callable[[str, Any], None]:
        def on_event(name: str, body: Any) -> None:
            if gen != self._gen:
                return
            ev = name[6:] if name.startswith("ctsim/") else name
            body = body if isinstance(body, dict) else {}
            if ev == "tagsChanged":
                self._apply_values(C.tag_changes_from(body), gen)
                if "t" in body and self._clock is not None:
                    self._take_clock(C.clock_from({"t": body["t"]}, self._clock), gen)
                if body.get("definitions") or body.get("reload"):
                    self._reload_tags(gen)
            elif ev == "pageChanged":
                self._page_changed(body, gen)
                if not self._pre_rolled and self._session is not None:
                    # (the first states may come before the trends have their pens)
                    try:
                        self._pre_roll(self._session, gen, page_state_from_json(body, page=self.page_name))
                    except Exception:  # noqa: BLE001 -- no history, the page runs on
                        logsetup.note_exception("sim.ctsim_source._pre_roll")
            elif ev == "windowsChanged":
                self._windows_changed(body, gen)
            elif ev == "optionsChanged":
                self._take_options(body, gen)
            elif ev == "debugPaused":
                self._take_debug_pause(body)
            elif ev == "tagDefinitionsChanged":
                # a tag's properties were overridden (any client): list them again
                self._reload_tags(gen)
            elif ev == "userChanged":
                self._take_user(body, gen)
            elif ev == "log":
                self._add_log(log_entry(self._event_time(body), body))
            elif ev == "notSimulated":
                fn = str(body.get("function") or "")
                try:
                    count = int(body.get("count") or 1)
                except TypeError, ValueError:
                    count = 1
                self._add_log(
                    S.LogEntry(
                        self._event_time(body),
                        "not_simulated",
                        "%s is not simulated" % fn,
                        function=fn,
                        count=count,
                    )
                )
            elif ev in ("taskStarted", "taskExited"):
                self._task_event(ev, body)
            elif ev in ("clockChanged", "clock"):
                self._take_clock(C.clock_from(body, self._clock), gen)
            elif ev == "scenarioFinished":
                self._scenario_finished(body)
            elif ev == "alarmsChanged":
                with self._lock:
                    self._alarm_serial += 1

        return on_event

    def _take_debug_pause(self, body: dict) -> None:
        """ctsim/debugPaused: page code the debugger (VS Code) stopped holds the whole
        simulation, or it goes on. While held, the engine answers only the debugger: this
        source sends nothing it would wait for (clicks, tag writes, the clock, the polls)."""
        paused = bool(body.get("paused"))
        with self._lock:
            was = self._debug_pause
            self._debug_pause = dict(body) if paused else None
        if paused and was is None:
            self._add_log(
                S.LogEntry(
                    self._now(),
                    "log",
                    "Paused by the debugger in %s. The "
                    "simulation waits until the debugger continues." % debug_pause_where(body),
                    category="simulator",
                )
            )

    def debug_paused(self) -> Optional[Dict[str, Any]]:
        """Where the debugger holds the simulation (ctsim/debugPaused's body), None when it
        runs."""
        with self._lock:
            s = self._session
            if self._debug_pause is not None and s is not None and not s.alive:
                # the connection ended while held (no 'paused: false' will come)
                self._debug_pause = None
            return self._debug_pause

    def _held_by_debugger(self, what: str) -> bool:
        """True (and a note once) while the debugger holds the simulation: ``what`` is not
        sent."""
        if self.debug_paused() is None:
            return False
        self._note_once(
            "debug-held:" + what,
            "The simulation is paused by the debugger: %s waits until the debugger continues." % what,
        )
        return True

    def _scenario_finished(self, body: dict) -> None:
        """ctsim/scenarioFinished {passed, failed, skipped, errors, end, failures, ...}."""
        if self._tools is not None:
            if self._tools.swallow_finished(body):
                return  # (the tag generators' own scenario)
            self._tools.scenario_running = ""
        counts = [
            "%s %s" % (body[k], k)
            for k in ("passed", "failed", "skipped", "errors")
            if isinstance(body.get(k), int)
        ]
        text = "The scenario ended" + (": " + ", ".join(counts) if counts else "")
        if body.get("stopped"):
            text += " (stopped)"
        bad = bool(body.get("failed") or body.get("errors"))
        self._add_log(S.LogEntry(self._now(), "error" if bad else "log", text))
        for f in (body.get("failures") or ())[:20]:
            if isinstance(f, dict):
                self._add_log(
                    S.LogEntry(
                        self._now(),
                        "error",
                        "Scenario: %s %s wanted %s, "
                        "got %s"
                        % (f.get("kind", "expect"), f.get("target", ""), f.get("want"), f.get("got")),
                    )
                )

    def _event_time(self, body: dict) -> float:
        for k in ("t", "simTime", "time"):
            if k in body and not isinstance(body[k], str):
                try:
                    return float(body[k])
                except TypeError, ValueError:
                    pass
        return self._now()

    def _task_event(self, ev: str, body: dict) -> None:
        task = body.get("task", body.get("id", ""))
        fn = str(body.get("function") or body.get("name") or "")
        what = "Task %s" % task if task not in ("", None) else "A task"
        halted = str(body.get("state") or "").lower() == "halt" or body.get("haltCode") or body.get("error")
        if not halted and self._task_burst_full(fn):
            return
        if ev == "taskStarted":
            text = "%s started%s" % (what, ": %s" % fn if fn else "")
            kind = "log"
        elif halted:
            why = body.get("haltReason") or body.get("error") or "halted"
            code = body.get("haltCode")
            text = "%s ended%s: %s%s" % (
                what,
                " (%s)" % fn if fn else "",
                why,
                " (hardware error %s)" % code if code else "",
            )
            kind = "error"
        else:
            text = "%s ended%s" % (what, " (%s)" % fn if fn else "")
            if "result" in body and body["result"] not in (None, ""):
                text += ", returned %s" % body["result"]
            kind = "log"
        self._add_log(S.LogEntry(self._event_time(body), kind, text, function=fn, category="task"))

    def _task_burst_full(self, fn: str) -> bool:
        """Count a plain task event; True when this second already listed TASK_BURST (the
        rest go into one line, _flush_task_burst)."""
        now = time.monotonic()
        flush = False
        with self._lock:
            b = getattr(self, "_task_burst", None)
            if b is None or now - b[0] >= 1.0:
                flush = bool(b and b[2])
                if not flush:
                    self._task_burst = b = [now, 0, 0, {}]
            if not flush:
                b[1] += 1
                if b[1] <= TASK_BURST:
                    return False
                b[2] += 1
                b[3][fn] = b[3].get(fn, 0) + 1
                return True
        self._flush_task_burst()
        return self._task_burst_full(fn)

    # -- page variables (engine/PROTOCOL.md ctsim/pageVars) ------------------------------------
    def _take_page_vars(self, body: Any, gen: int, window: int) -> None:
        """A PAGE body's 'pageVars' (the whole list of the window's page variables)."""
        pv = body.get("pageVars") if isinstance(body, dict) else None
        if not isinstance(pv, list):
            return
        rows = []
        for d in pv:
            if not isinstance(d, dict) or not d.get("name"):
                continue
            string = str(d.get("type") or "") == "string"
            rows.append(
                S.TagInfo(
                    name=str(d["name"]),
                    type="STRING" if string else "INT",
                    value=d.get("value"),
                    page_var=True,
                    page_reads=True,
                    locked=bool(d.get("locked")),
                    comment="Page variable of this window",
                )
            )
        with self._lock:
            if gen != self._gen or window != self._window:
                return
            old = [(t.name, t.type, t.value, t.locked) for t in self._page_vars]
            if old != [(t.name, t.type, t.value, t.locked) for t in rows]:
                self._page_vars = rows
                self._tag_serial += 1

    # -- parameters (engine/PROTOCOL.md ctsim/params) -------------------------------------
    PARAMS_EVERY = 1.0

    def _used_key(self) -> Optional[Tuple[int, str]]:
        """(window, page) the used tags are asked for (lock held): an SA page shown in its
        workspace pane, the pane's window (its code), as the page's own tags; None before the
        page has a window."""
        window = self.focus_window if self.focus_window is not None else self._window
        if window is None:
            return None
        return window, (self.page_name or "").lower()

    def _poll_used(self, s: C.Session, gen: int) -> None:
        """The tags the window's code used since the last ask (ctsim/usedTags): the ones the
        page's own list does not have are added to it, read from the tag listing."""
        with self._lock:
            key = self._used_key()
            if key is None or self._used_busy:
                return
            window = key[0]
            if self._used_for != key:
                self._used_for, self._used_from, self._used_rows = key, 0, []
            frm = self._used_from
            self._used_busy = True

        def got_rows(names, where, body, err):
            rows = []
            if err is None:
                rows = [_tag_info(r, page_reads=True) for r in C.tag_page_from(body).tags]
            seen = {t.name.lower() for t in rows}
            for n in names:
                if n.lower() not in seen:
                    rows.append(
                        S.TagInfo(
                            name=n,
                            type="REAL",
                            page_reads=True,
                            defined=False,
                            comment="Used by the page's Cicode",
                        )
                    )
            for t in rows:
                t.cicode_used = True
                t.used_by = where.get(t.name.lower(), "")
                if not t.comment:
                    t.comment = "Used by the page's Cicode"
            with self._lock:
                if gen != self._gen or self._used_for != key:
                    return
                have = {t.name.lower() for t in (self._page_tags or ())}
                rows = [t for t in rows if t.name.lower() not in have]
                self._used_rows += rows
                if self._page_tags is not None:
                    self._page_tags = list(self._page_tags) + rows
                    for t in rows:
                        self._index.setdefault(t.name.lower(), []).append(t)
                    self._tag_serial += 1
            if rows:
                self._subscribe(s, gen)

        def done(body, err):
            with self._lock:
                self._used_busy = False
            if err is not None or not isinstance(body, dict):
                return
            names = [str(n) for n in body.get("names") or () if n]
            # (where each name was first used, parallel to names)
            by = body.get("by") if isinstance(body.get("by"), list) else []
            where = {n.lower(): used_where(by[k]) for k, n in enumerate(names) if k < len(by)}
            page = str(body.get("page") or "")
            try:
                count = int(body.get("count", frm + len(names)))
            except TypeError, ValueError:
                count = frm + len(names)
            with self._lock:
                if gen != self._gen or self._used_for != key:
                    return
                if not self._same_page(page):
                    # the window shows another page for now (the startup page, the page an
                    # attached server showed before): none of its tags, and this page's list
                    # from its start (it starts again when the page shows)
                    self._used_from = 0
                    return
                if count < frm:
                    # the window showed the page again since (its list started again): from
                    # the start next time
                    self._used_from = 0
                    return
                self._used_from = max(self._used_from, frm + len(names))
                have = {t.name.lower() for t in (self._page_tags or ())}
            new = [n for n in names if n.lower() not in have]
            if new:
                try:
                    s.request_async("tags", {"names": new}, lambda b, e: got_rows(new, where, b, e))
                except C.CtsimError:
                    pass

        try:
            s.request_async("usedTags", {"window": int(window), "from": int(frm)}, done)
        except C.CtsimError:
            with self._lock:
                self._used_busy = False

    def _poll_params(self) -> None:
        """Ask the simulator for the parameters its Cicode read and the tags it used (at most
        once a second, one request each at a time)."""
        with self._lock:
            s, gen = self._session, self._gen
            if (
                s is None
                or self._params_off
                or self._params_busy
                or self._stopped
                or self._debug_pause is not None
            ):
                return
            now = time.monotonic()
            if now - self._params_asked < self.PARAMS_EVERY:
                return
            self._params_asked = now
            self._params_busy = True
        self._poll_used(s, gen)

        def done(body, err):
            with self._lock:
                self._params_busy = False
            if err is None:
                self._take_params(body, gen)
            elif isinstance(err, C.NotAvailable):
                with self._lock:
                    self._params_off = True

        try:
            s.request_async("ctsim/params", {}, done)
        except Exception:  # noqa: BLE001  # swallow-ok: a lost connection is noticed elsewhere
            with self._lock:
                self._params_busy = False

    def _take_params(self, body: Any, gen: int) -> None:
        if not isinstance(body, dict) or not isinstance(body.get("params"), list):
            return
        serial = body.get("serial")
        with self._lock:
            if gen != self._gen or serial == self._params_serial:
                return
        rows = []
        for d in body["params"]:
            if not isinstance(d, dict) or not d.get("name"):
                continue
            sec, name = str(d.get("section") or ""), str(d.get("name"))
            rows.append(
                S.TagInfo(
                    name="[%s]%s" % (sec, name),
                    type="STRING",
                    value=str(d.get("value") or ""),
                    param=True,
                    page_reads=True,
                    source=str(d.get("source") or ""),
                    users=tuple(str(u) for u in d.get("users") or ()),
                    comment="Default: %s" % (d.get("default") or "''"),
                )
            )
        with self._lock:
            if gen != self._gen:
                return
            self._params_serial = serial
            self._params = rows
            self._tag_serial += 1

    def params(self) -> List[S.TagInfo]:
        with self._lock:
            return [dataclasses.replace(t) for t in self._params]

    def set_param(self, key: str, value: Any) -> None:
        """Set a parameter for the simulation ('[Section]Name'; '' = back to the project's)."""
        sec, _sep, name = key[1:].partition("]") if key.startswith("[") else ("", "", key)
        value = "" if value is None else str(value)
        with self._lock:
            s, gen = self._session, self._gen
            for t in self._params:
                if t.name == key:
                    t.value = value
                    self._tag_serial += 1
        if s is None:
            return
        self._add_log(S.LogEntry(self._now(), "write", "parameter %s = %s" % (key, value)))

        def done(body, err):
            if err is None:
                self._take_params(body, gen)
            elif isinstance(err, C.NotAvailable):
                self._note_once("setParam", "This simulator cannot set parameters.")
            elif not isinstance(err, C.ConnectionLost):
                self._add_log(S.LogEntry(self._now(), "error", "Parameter %s was not set: %s" % (key, err)))

        s.request_async("ctsim/setParam", {"section": sec, "name": name, "value": value}, done)

    def page_vars(self) -> List[S.TagInfo]:
        """The tab's window's page variables (copies), [] before the page runs."""
        with self._lock:
            return [dataclasses.replace(t) for t in self._page_vars]

    def set_page_var(self, name: str, value: Any, type_: str = "") -> None:
        """Set a page variable of the tab's window, as PageSetInt / PageSetStr would."""
        with self._lock:
            s, gen, window = self._session, self._gen, self._window
            for t in self._page_vars:
                if t.name == name:
                    if t.is_string():
                        t.value = "" if value is None else str(value)
                    else:
                        try:
                            t.value = int(float(value))
                        except TypeError, ValueError:
                            pass
                    type_ = type_ or ("string" if t.is_string() else "int")
                    self._tag_serial += 1
        if s is None or window is None:
            return
        if type_ == "int":
            try:
                value = int(float(value))
            except TypeError, ValueError:
                self._add_log(
                    S.LogEntry(
                        self._now(), "error", "Page variable %s: '%s' is not a whole number" % (name, value)
                    )
                )
                return
        self._add_log(S.LogEntry(self._now(), "write", "page variable %s = %s" % (name, value)))
        args = {"window": window, "name": name, "value": value}
        if type_:
            args["type"] = type_

        def done(body, err):
            if err is None:
                self._take_page_vars({"pageVars": (body or {}).get("vars")}, gen, window)
            elif isinstance(err, C.NotAvailable):
                self._note_once("setPageVar", "This simulator cannot set page variables.")
            elif not isinstance(err, C.ConnectionLost):
                self._add_log(
                    S.LogEntry(self._now(), "error", "Page variable %s was not set: %s" % (name, err))
                )

        try:
            s.request_async("setPageVar", args, callback=done)
        except C.CtsimError as e:
            self._add_log(S.LogEntry(self._now(), "error", "Page variable %s was not set: %s" % (name, e)))

    def _page_changed(self, body: dict, gen: int) -> None:
        try:
            window = int(body.get("window", -1))
        except TypeError, ValueError:
            return
        page = str(body.get("page") or "")
        serial = _serial(body)
        with self._lock:
            if gen != self._gen:
                return
            self._arrived += 1
            n = self._arrived
            if window != self._window:
                if self._opening:
                    # the page opens and its window is not known yet: kept by window
                    self._keep(window, page, n, serial, body)
                return
            mine = not page or self._same_page(page)
            if mine and not self._live:
                # the window's whole state is asked for (a reload, a navigation back, the
                # open's answer not taken yet): kept, the newer ones go on top of it (put
                # into the changes, they would be dropped when the whole state replaced them)
                self._keep(window, page, n, serial, body)
                return
            if mine and serial is not None and self._base is not None and serial <= self._base:
                return  # older than the whole state drawn: in it already
        if page and not self._same_page(page):
            # the server shows another page in this window now: a navigation (PageDisplay from
            # the page's code) the windowsChanged that follows names with its drawing file, so
            # the tab follows it; a server that sends no windowsChanged (or a page without a
            # file): another tab took the window, back to the demo
            with self._lock:
                # (a tab on a window Cicode opened always follows: a pane or popup that
                # shows another page is a navigation, never another tab's page)
                follow = self._windows_seen or self.attach_window is not None
                s = self._session
                # once per page shown there (every scan of it sends one)
                ask = follow and self._other_page.lower() != page.lower()
                if follow:
                    self._other_page = page
                    self._live = False
                    self._held = True
                    self._changes = {}
                    # (what was kept of the tab's page there is of a display that ended)
                    self._buffers.pop(window, None)
            if not follow:
                self._page_taken(gen, window, page)
            elif s is not None and ask:
                # the windows say which: a navigation the tab follows, or a state of another
                # page sent before this page opened in the window (no windowsChanged follows
                # then: the windows are the same as the last ones sent)
                self._ask_windows(s, gen)
            return
        objs = object_states_from_json(body.get("objects") or ())
        # every pageChanged carries the page's whole drawn list (none = nothing drawn); the
        # controls only when they changed (none = no news)
        drawn = drawn_from_json(body.get("drawn"))
        controls = controls_from_json(body.get("controls"))
        self._take_page_vars(body, gen, window)
        with self._lock:
            if gen != self._gen or window != self._window:
                return
            self._changes.update(objs)
            self._drawn_next = drawn
            if controls is not None:
                self._controls_next = controls
            if "flashOn" in body or "flash_on" in body:
                self._flash = bool(body.get("flashOn", body.get("flash_on")))
        # (no clock from it: PAGE's 'scan' counts the page's scans, it carries no time)

    def _keep(self, window: int, page: str, n: int, serial: Optional[int], body: Dict[str, Any]) -> None:
        """Keep a pageChanged of ``window`` until its whole state is taken (the lock is
        held). A window that shows another page now starts over (its kept states were of
        the page that left)."""
        buf = self._buffers.get(window)
        if buf is not None and page and buf.page and page.lower() != buf.page.lower():
            buf = None
        if buf is None:
            if len(self._buffers) >= BUFFER_WINDOWS and window not in self._buffers:
                return
            buf = self._buffers[window] = _Buffered(page)
        elif page and not buf.page:
            buf.page = page
        buf.add(n, serial, body)

    def _ask_windows(self, s: C.Session, gen: int) -> None:
        """The windows now (ctsim/windows), taken like a windowsChanged event."""

        def done(body, err):
            if err is None:
                self._windows_changed(body if isinstance(body, dict) else {}, gen)

        try:
            s.request_async("windows", {}, callback=done)
        except C.CtsimError:
            pass

    def _windows_changed(self, body: Dict[str, Any], gen: int) -> None:
        """windowsChanged: the engine's windows (popups for the window to show), a navigation
        of this tab's window, this tab's window freed."""
        wins = C.windows_from(body)
        with self._lock:
            if gen != self._gen:
                return
            self._windows = wins
            self._windows_serial += 1
            self._windows_seen = True
            mine = self._window
            key = self._assoc_of(mine)
            again = (
                key is not None
                and self._page_tags is not None
                and self._assoc_seen is not None
                and key != self._assoc_seen
            )
            if self._assoc_seen is None and key is not None:
                self._assoc_seen = key
        if again:
            # the window's page or associations changed (a popup displayed in it,
            # AssWinReplace): the tags it reads through them are other ones now
            self._reload_tags(gen)
        if mine is None:
            return
        w = next((x for x in wins if x["window"] == mine), None)
        if w is None:
            with self._lock:
                if gen != self._gen:
                    return
                self._window_gone = True
                self._live = False
                self._held = True
                self._changes = {}
                s = self._session
            if s is not None and self.attach_window is None:
                # (a popup's tab closes with its window; this tab stays, showing the page as it
                # was last drawn)
                self._status_connected(
                    gen,
                    s,
                    page=False,
                    retry=True,
                    why=(
                        "the simulator closed this page's window (the page's Cicode freed it); "
                        "Reconnect opens the page again."
                    ),
                )
            return
        if w["page"] and not self._same_page(w["page"]):
            if w["path"]:
                with self._lock:
                    # (this runs on the reader thread: the tab may have handled the navigation
                    # since 'mine' was read (Open each page in a new tab: a new tab took the
                    # window and this one let go of it), and a second windows list, the event
                    # and the answer to _ask_windows, would then open the page in two tabs)
                    if gen != self._gen or self._window != mine:
                        return
                    self._nav = dict(w)
                    self._live = False
                    self._held = True
                    self._changes = {}
                    self._replace_next = True
            else:
                self._page_taken(gen, mine, w["page"])
            return
        with self._lock:
            back = bool(self._other_page) and gen == self._gen
            s = self._session
            self._other_page = ""
        if back and s is not None:
            # the window shows this tab's page again: the whole set, drawn again
            try:
                s.request_async(
                    "pageState", {"window": int(mine)}, callback=self._page_state_done(gen, s, mine)
                )
            except C.CtsimError:
                pass

    def _same_page(self, page: str) -> bool:
        """``page`` (a server's page name) is this tab's page (names compare without case,
        as the runtime's do; a '!' popup prefix counts)."""
        a = (page or "").strip().lstrip("!").lower()
        b = (self.page_name or "").strip().lstrip("!").lower()
        return not a or not b or a == b

    def _page_taken(self, gen: int, window: int, page: str) -> None:
        """The server shows ``page`` in this tab's window: back to the demo, said once."""
        with self._lock:
            if gen != self._gen or window != self._window:
                return
            s = self._session
            self._window = None
            was = self._live or self._held
            self._live = False
            self._held = False
            self._full = None
            self._changes = {}
            if was:
                self._replace_next = True
        if was:
            self.fake.resend()
        if s is not None:
            self._status_connected(
                gen,
                s,
                page=False,
                why=(
                    "the simulator now shows %s in this page's window (another tab opened it); "
                    "Reconnect opens this page again." % page
                ),
            )

    def _reload_tags(self, gen: int) -> None:
        with self._lock:
            s = self._session
            self._search_done = None
        if s is None:
            return
        threading.Thread(target=self._load_page_tags, args=(s, gen), daemon=True, name="ctsim-tags").start()

    def _close_listener(self, gen: int, s: C.Session):
        def closed(reason: Optional[str]) -> None:
            if reason is None:
                # closed by this process, but not by this tab (a tab that lets go removes
                # this listener first): a new simulator choice, the application ending
                self._fall_back(
                    gen,
                    "%sthe Cicode simulator closed the connection. Reconnect "
                    "starts it again." % self._no_sim_prefix(),
                )
                return
            text = s.stop_reason()
            log.info("ctsim: the connection to %s ended: %s", s.describe(), text)
            first = text.splitlines()[0].strip().rstrip(".") if text else ""
            # (one sentence: 'The Cicode simulator closed the connection' repeated 'stopped')
            self._fall_back(
                gen,
                "%sthe Cicode simulator stopped%s. Reconnect starts it again."
                % (
                    self._no_sim_prefix(),
                    (" (" + first + ")") if first and "closed the connection" not in first else "",
                ),
                detail=text,
            )

        return closed
