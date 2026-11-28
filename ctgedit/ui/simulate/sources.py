"""Interfaces the Simulate tab talks to, plus demo fakes."""

from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from ...format.cnum import is_digits
from ...sim.state import ObjectState, PageState
from ...format import codes as _codes

#: the default page scan time, ms ([Page]ScanTime, doc: 1118065)
SCAN_MS = 250
#: tag qualities the Inputs panel offers
QUALITIES = ("Good", "Uncertain", "Bad")
#: input event kinds
INPUT_KINDS = ("down", "up", "double", "drag", "key")


@dataclass
class InputEvent:
    """One user input on the page (``input {window, an, kind, key?, x?, y?}``)."""

    kind: str  # INPUT_KINDS
    an: Optional[int] = None  # the AN under the pointer (innermost record with one)
    ans: Tuple[int, ...] = ()  # that record's AN and its groups' ANs, innermost first
    x: float = 0.0  # page coordinates of the pointer
    y: float = 0.0
    dx: float = 0.0  # 'drag': the pointer's offset from the press, page px
    dy: float = 0.0
    key: str = ""  # 'key': the key name ('F2', 'Enter', 'A')
    text: str = ""  # 'key': the character typed, if any
    modifiers: int = 0  # 1 Shift, 2 Ctrl, 4 Alt
    button: str = "left"  # 'left' / 'right' / 'middle' for pointer events
    window: int = 0


@dataclass
class ClockState:
    running: bool = False
    speed: float = 1.0
    scan: int = 0  # scans done
    time: float = 0.0  # simulated seconds


@dataclass
class TagInfo:
    """One tag as the Inputs panel shows it."""

    name: str
    type: str = "REAL"  # DIGITAL, INT, LONG, REAL, STRING ... (variable.dbf TYPE)
    value: Any = None
    quality: str = "Good"  # QUALITIES
    eng_zero: Optional[float] = None
    eng_full: Optional[float] = None
    units: str = ""
    equipment: str = ""
    item: str = ""
    comment: str = ""
    page_reads: bool = False  # the page's expressions read it
    project: str = ""
    defined: bool = True  # False: the project does not define this name
    simulated: bool = False  # an undefined name the simulator made a tag for
    via: str = ""  # the association a '?name?' reference came through
    ref: str = ""  # that reference as the page's code has it
    page_var: bool = False  # a page variable of the tab's window (PageSetInt ...)
    param: bool = False  # a parameter the page's Cicode read ([Section]Name)
    source: str = ""  # a parameter's: citect.ini, _PARAM.RDB, param.dbf, default
    cicode_used: bool = False  # not the page's own: its Cicode used it while it ran
    used_by: str = ""  # where first: 'TankLevel (tanks.ci line 32)'
    users: tuple = ()  # a parameter's: the functions / objects that used it
    locked: bool = False  # locked in the simulator: Cicode cannot change it
    # the definition fields a simulation can override (ctsim/setTagProperty): as the project
    # has them, else the override
    format: str = ""
    raw_zero: Optional[float] = None
    raw_full: Optional[float] = None
    deadband: Optional[float] = None
    io_device: str = ""
    address: str = ""
    custom: tuple = ("",) * 8
    overrides: Dict[str, str] = field(default_factory=dict)  # FIELD -> override
    configured: Dict[str, str] = field(default_factory=dict)  # FIELD -> project's

    def is_digital(self) -> bool:
        return self.type.upper() in ("DIGITAL", "BOOL", "BOOLEAN")

    def is_string(self) -> bool:
        return self.type.upper() == "STRING"


@dataclass
class SimStatus:
    """What the Simulate window says about a tab's simulation (a status line)."""

    text: str = ""
    demo: bool = True  # the page moves on demo values
    level: str = "text_dim"  # theme note role: 'text_dim', 'warn', 'danger'
    connected: bool = False  # a simulator answers (tags, log, clock are its own)
    busy: bool = False  # connecting
    can_retry: bool = False  # Reconnect makes sense
    engine_missing: bool = False  # no ctsim.exe found (choose one)
    engine: str = ""  # 'ctsim 0.3.0'
    project: str = ""
    detail: str = ""  # the tool tip: where it looked, the server's output


@dataclass
class LogEntry:
    """One line of the Outputs panel."""

    time: float
    kind: str  # 'write' | 'log' | 'not_simulated' | 'error'
    text: str
    function: str = ""
    file: str = ""
    line: int = 0
    count: int = 1  # 'not_simulated': calls so far
    level: str = ""  # the simulator's level: error / warning / info
    code: int = 0  # a hardware error's number
    call: str = ""  # the failing built-in with its values: 'TagRead("X", 0, "")'
    tag: str = ""  # the tag a failing tag read named
    #: the Cicode call stack, innermost first: (function, file, line, ((arg, value), ...))
    stack: tuple = ()
    #: the page code that ran: its window (-1 none), page, object AN (-1 none) and what it
    #: was ('visible', 'touch.down', 'page.entry' ...)
    window: int = -1
    page: str = ""
    an: int = -1
    source: str = ""
    #: what the line is (CATEGORIES); '' = derived (category_of)
    category: str = ""
    #: the simulator's text as it came, when ``text`` is a shorter reading of it (the
    #: details box shows it)
    raw: str = ""


#: the Outputs log's line types: (key, label, what it holds)
CATEGORIES = (
    ("cicode_error", "Cicode error", "Hardware errors the page's Cicode raised"),
    ("task", "Task", "Tasks started, ended and halted"),
    ("message", "Cicode message", "TraceMsg, ErrLog, DspError, prompts and other messages the Cicode writes"),
    ("write", "Tag write", "Values written to tags, alarms, equipment and parameters"),
    ("not_simulated", "Not simulated", "Calls of functions the simulator does not simulate"),
    ("reload", "Reload", "Cicode files and pages reloaded"),
    ("startup", "Startup", "The project's load notes, startup code and workspace"),
    ("scenario", "Scenario", "Scenario steps and results"),
    ("simulator", "Simulator", "The simulator's own notes and failures"),
)
CATEGORY_LABELS = {k: label for k, label, _what in CATEGORIES}
#: ctsim/log 'kind' -> category
_ENGINE_KINDS = {
    "hardware": "cicode_error",
    "task": "task",
    "message": "message",
    "reload": "reload",
    "load": "startup",
    "startup": "startup",
    "simulator": "simulator",
    "internal": "simulator",
    "handler": "simulator",
}
_STARTUP_PREFIXES = ("startup", "the startup page", "the project's startup", "navigate:")


def engine_category(kind: str, level: str, text: str, code: int = 0) -> str:
    """The category of a ctsim/log line: its 'kind', else from what it says."""
    c = _ENGINE_KINDS.get((kind or "").lower())
    if c:
        return c
    low = (text or "").lower()
    if code or low.startswith(("hardware error", "suppressed hardware error")):
        return "cicode_error"
    if low.startswith("task ") and " halted" in low:
        return "task"
    if low.startswith("reload "):
        return "reload"
    return "message"


def category_of(e) -> str:
    """What a log line is (CATEGORIES)."""
    c = getattr(e, "category", "") or ""
    if c:
        return c
    if e.kind == "write":
        return "write"
    if e.kind == "not_simulated":
        return "not_simulated"
    low = (e.text or "").lower()
    if low.startswith(_STARTUP_PREFIXES) or "workspace" in low:
        return "startup"
    if low.startswith("scenario"):
        return "scenario"
    if getattr(e, "function", "") or getattr(e, "file", ""):
        return "message"  # (a line from a place in the Cicode)
    return "simulator"


#: the severities, most severe first
SEVERITIES = ("error", "warning", "info")


def severity_of(e) -> str:
    """'error', 'warning' or 'info'."""
    lv = (getattr(e, "level", "") or "").lower()
    if lv in ("error", "fatal"):
        return "error"
    if lv in ("warning", "warn"):
        return "warning"
    if lv == "info":
        return "info"
    if e.kind == "error":
        return "error"
    if e.kind == "not_simulated":
        return "warning"
    return "info"


def tag_matches(t: TagInfo, needle) -> bool:
    """Does a tag match the Inputs panel's search?"""
    if getattr(needle, "regex", False):
        equip = t.equipment or ""
        return needle(t.name, t.comment, equip, "%s.%s" % (equip, t.item or "") if equip else "")
    needle = (getattr(needle, "text", needle) or "").strip().lower()
    if not needle:
        return True
    name = t.name.lower()
    if "*" in needle or "?" in needle:
        import fnmatch

        return fnmatch.fnmatchcase(name, needle.replace("[", "[[]"))
    if needle in name or needle in (t.comment or "").lower():
        return True
    equip = (t.equipment or "").lower()
    return bool(equip) and (needle in equip or needle in "%s.%s" % (equip, (t.item or "").lower()))


class StateSource(Protocol):
    """Object states of one page, scan by scan."""

    def start(self, page_path: str, page: Any) -> None:
        """Begin simulating ``page`` (a model.document.Page; its file is ``page_path``)."""
        ...

    def stop(self) -> None: ...

    def poll(self) -> Optional[PageState]:
        """The newest PageState not handed out yet, or None. ``objects`` holds the states of
        the ANs that changed since the last one handed out (an ObjectState equal to its default
        puts that object back to its static look); ``replace`` in the tab is not used, so a
        source that sends full states works too."""
        ...

    def set_listener(self, fn: Optional[Callable[[PageState], None]]) -> None:
        """Push mode: ``fn`` gets every new PageState (it may be called from another thread;
        the tab moves it to the GUI thread). None = poll only."""
        ...

    def clock(self, mode: str, value: Optional[float] = None) -> None:
        """'run', 'pause', 'step' (one scan while paused) or 'speed' (``value`` x real time)."""
        ...

    def clock_state(self) -> ClockState: ...

    def input(self, event: InputEvent) -> None: ...


class TagSource(Protocol):
    """The tags the Inputs panel lists and changes."""

    def tags(self, page_only: bool = False) -> List[TagInfo]:
        """Every tag (``page_only``: only the tags the page reads), values current."""
        ...

    def set_tag(self, name: str, value: Any, quality: Optional[str] = None) -> None: ...

    def serial(self) -> int:
        """Bumped whenever a value or a definition changed (the panel reads them again)."""
        ...


class LogSource(Protocol):
    """Tag writes, Cicode log lines and calls the simulator does not simulate."""

    def drain(self) -> List[LogEntry]:
        """The entries since the last drain."""
        ...


# -- the demo -----------------------------------------------------------------------------
_EXPR_KEYS = (
    "MVX",
    "MVY",
    "ROT",
    "SZX",
    "SZY",
    "COL_A",
    "COL_B",
    "COL_C",
    "COL_D",
    "COL_E",
    "COL_EXP",
    "COL_ARR",
    "FIL",
    "TXT_A",
    "TXT_B",
    "TXT_C",
    "TXT_D",
    "TXT_E",
    "TXT_STR",
    "VISIBLE",
    "DISABLED",
    "SET_ARR",
    "MVXSL",
    "MVYSL",
    "ROTSL",
)
_DIGITAL_KEYS = (
    "VISIBLE",
    "DISABLED",
    "COL_A",
    "COL_B",
    "COL_C",
    "COL_D",
    "COL_E",
    "TXT_A",
    "TXT_B",
    "TXT_C",
    "TXT_D",
    "TXT_E",
)
_WORDS = frozenset(
    {
        "AND",
        "OR",
        "NOT",
        "MOD",
        "TRUE",
        "FALSE",
        "BITAND",
        "BITOR",
        "BITXOR",
        "IF",
        "THEN",
        "ELSE",
        "END",
        "DO",
        "WHILE",
        "FOR",
        "TO",
        "RETURN",
    }
)
_STRING_RE = re.compile(r'"(?:\^.|[^"^])*"')
_RANGE_RE = re.compile(r"\^\(\s*[-+0-9.eE]+\s*,\s*[-+0-9.eE]+\s*\)\s*$")
_NAME_RE = re.compile(r"(?<![\w.%?])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?![\w.%?])(?!\s*\()")
_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")


def expression_names(expr: str) -> List[str]:
    """The tag-like names an expression reads (string literals, a ``^(min, max)`` range, a
    ``:format`` tail and function names left out). A heuristic for the demo only: the
    simulator resolves names the way the compiler does."""
    s = _RANGE_RE.sub("", str(expr or ""))
    s = _STRING_RE.sub(" ", s)
    if ":" in s and "(" not in s.split(":", 1)[1]:
        s = s.split(":", 1)[0]
    out: List[str] = []
    for m in _NAME_RE.finditer(s):
        n = m.group(1)
        if n.upper() not in _WORDS and n not in out:
            out.append(n)
    return out


def command_functions(cmd: str) -> List[str]:
    """The functions a command calls, in order (demo: for the 'not simulated' list)."""
    seen = []
    for m in _CALL_RE.finditer(_STRING_RE.sub(" ", str(cmd or ""))):
        f = m.group(1)
        if f.upper() not in _WORDS and f not in seen:
            seen.append(f)
    return seen


@dataclass
class _Animated:
    an: int
    index: int
    move_x: bool = False
    move_y: bool = False
    rotate: bool = False
    scale: bool = False
    colour: bool = False
    level: bool = False
    text: bool = False
    states: int = 0  # a symbol set's state count
    phase: float = 0.0


def page_animations(page) -> List[_Animated]:
    """The page's animated records (one per AN: the first record with it), from their
    animation keys."""
    from ...model.document import ObjectKind

    out, seen = [], set()
    if page is None:
        return out
    objs = page.objects
    for i, o in enumerate(objs):
        an = getattr(o, "an", -1)
        if not isinstance(an, int) or an <= 0 or an in seen:
            continue
        seen.add(an)
        props = o.raw.get("_props") or {}
        a = _Animated(an=an, index=i, phase=(len(out) * 0.37) % (2 * math.pi))
        a.move_x = bool(props.get("MVX") or props.get("MVXSL"))
        a.move_y = bool(props.get("MVY") or props.get("MVYSL"))
        a.rotate = bool(props.get("ROT") or props.get("ROTSL"))
        a.scale = bool(props.get("SZX") or props.get("SZY"))
        a.colour = any(props.get(k) for k in ("COL_A", "COL_EXP", "COL_ARR"))
        a.level = bool(props.get("FIL"))
        if o.kind in (ObjectKind.TEXT, ObjectKind.NUMBER):
            a.text = (
                bool(props.get("TXT_STR"))
                or str(props.get("TXTMODE", "")) == "3"
                or "#" in (o.style.text or "")
            )
        if o.raw.get("_type_code") == 0x0C and o.members:
            a.states = sum(
                1
                for m in o.members
                if 0 <= m < len(objs)
                and objs[m].raw.get("_type_code") not in (_codes.TC.TOUCH, _codes.TC.KEYBOARD)
            )
        if any((a.move_x, a.move_y, a.rotate, a.scale, a.colour, a.level, a.text, a.states > 1)):
            out.append(a)
    return out


def page_tags(page) -> List[TagInfo]:
    """The tags the page's expressions read (demo heuristic, see expression_names)."""
    tags: Dict[str, TagInfo] = {}
    if page is None:
        return []
    for o in page.objects:
        props = o.raw.get("_props") or {}
        for k in _EXPR_KEYS:
            v = props.get(k)
            if not v:
                continue
            for n in expression_names(v):
                if n in tags or is_digits(n.replace(".", "")):
                    continue
                equip, item = "", ""
                if "." in n:
                    equip, item = n.rsplit(".", 1)
                t = TagInfo(
                    name=n,
                    type="DIGITAL" if k in _DIGITAL_KEYS else "REAL",
                    equipment=equip,
                    item=item,
                    page_reads=True,
                    eng_zero=None if k in _DIGITAL_KEYS else 0.0,
                    eng_full=None if k in _DIGITAL_KEYS else 100.0,
                )
                t.value = 0 if t.is_digital() else 0.0
                tags[n] = t
    return sorted(tags.values(), key=lambda t: (t.equipment.lower(), t.name.lower()))


class FakeSimulation:
    """A demo StateSource + TagSource + LogSource: sine-driven moves, rotations, sizes,
    colours, fill levels, texts and symbol states on the page's animated objects, the page's
    tags with sine values (until the user sets one), and log lines for the user's input."""

    def __init__(
        self,
        page=None,
        scan_ms: int = SCAN_MS,
        period: float = 8.0,
        clock: Callable[[], float] = time.monotonic,
        tags: Optional[List[TagInfo]] = None,
        extra_tags: int = 3,
    ):
        self.page = page
        self.scan_ms = max(1, int(scan_ms))
        self.period = float(period)
        self._clock = clock
        self._lock = threading.Lock()
        self._running = False
        self._speed = 1.0
        self._scan = 0
        self._acc = 0.0
        self._last = clock()
        self._pending: Optional[PageState] = None
        self._serial_state = 0
        self._listener = None
        self._anim: List[_Animated] = []
        self._held: Dict[str, Tuple[Any, str]] = {}
        self._tags: List[TagInfo] = list(tags) if tags is not None else []
        self._given_tags = tags is not None
        self._extra = max(0, int(extra_tags))
        self._tag_serial = 0
        self._log: List[LogEntry] = []
        self._not_sim: Dict[str, int] = {}
        self.inputs: List[InputEvent] = []
        self.page_path = ""
        if page is not None:
            self.start("", page)

    # -- StateSource -----------------------------------------------------------
    def start(self, page_path: str, page) -> None:
        with self._lock:
            self.page_path = page_path or getattr(page, "source_path", "") or ""
            self.page = page
            self._anim = page_animations(page)
            if not self._given_tags:
                self._tags = page_tags(page)
                for k in range(self._extra):
                    name = ("Demo_Level", "Demo_Running", "Demo_Message")[k % 3] + ("" if k < 3 else str(k))
                    typ = ("REAL", "DIGITAL", "STRING")[k % 3]
                    t = TagInfo(
                        name=name,
                        type=typ,
                        equipment="Demo",
                        item=name[5:],
                        comment="Demo tag",
                        eng_zero=0.0 if typ == "REAL" else None,
                        eng_full=100.0 if typ == "REAL" else None,
                    )
                    t.value = 0.0 if typ == "REAL" else (0 if typ == "DIGITAL" else "")
                    self._tags.append(t)
            self._scan = 0
            self._acc = 0.0
            self._last = self._clock()
            self._pending = self._state_for(0)
            self._update_tags()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._listener = None

    def poll(self) -> Optional[PageState]:
        with self._lock:
            now = self._clock()
            if self._running:
                self._acc += (now - self._last) * 1000.0 * self._speed
                n = int(self._acc // self.scan_ms)
                if n > 0:
                    self._acc -= n * self.scan_ms
                    self._advance(n)
            self._last = now
            st, self._pending = self._pending, None
            return st

    def set_listener(self, fn) -> None:
        self._listener = fn

    def resend(self) -> None:
        """Hand out the current scan's whole state again at the next poll (another source
        drew the page meanwhile)."""
        with self._lock:
            self._pending = self._state_for(self._scan)

    def clock(self, mode: str, value: Optional[float] = None) -> None:
        with self._lock:
            if mode == "run":
                self._running = True
                self._last = self._clock()
            elif mode == "pause":
                self._running = False
            elif mode == "step":
                self._running = False
                # (``value``: the simulated seconds to run, Simulate Settings > Step runs;
                # none: one scan)
                self._advance(max(1, int(round(float(value) * 1000.0 / self.scan_ms))) if value else 1)
            elif mode == "speed" and value:
                self._speed = max(0.01, float(value))
        fn = self._listener
        if fn is not None and mode == "step" and self._pending is not None:
            fn(self.poll())

    def clock_state(self) -> ClockState:
        return ClockState(
            running=self._running, speed=self._speed, scan=self._scan, time=self._scan * self.scan_ms / 1000.0
        )

    def input(self, event: InputEvent) -> None:
        with self._lock:
            self.inputs.append(event)
            t = self._scan * self.scan_ms / 1000.0
            if event.kind == "key":
                self._log.append(LogEntry(t, "log", "Key %s" % (event.key or event.text)))
                return
            if event.kind == "drag":
                return
            where = "AN %d" % event.an if event.an else "the page"
            what = {"down": "Pressed", "up": "Released", "double": "Double-clicked"}.get(
                event.kind, event.kind
            )
            self._log.append(LogEntry(t, "log", "%s %s" % (what, where)))
            cmd = self._command_for(event)
            if cmd:
                for f in command_functions(cmd):
                    self._not_sim[f] = self._not_sim.get(f, 0) + 1
                    self._log.append(
                        LogEntry(
                            t, "not_simulated", "%s is not simulated" % f, function=f, count=self._not_sim[f]
                        )
                    )

    def _command_for(self, event: InputEvent) -> str:
        """The touch command a click runs (demo: the CMD / DOWN_CMD of the record or a touch
        record sharing its AN)."""
        if self.page is None or event.kind not in ("down", "up"):
            return ""
        key = "DOWN_CMD" if event.kind == "down" else "CMD"
        for an in event.ans or ((event.an,) if event.an else ()):
            for o in self.page.objects:
                if getattr(o, "an", None) == an:
                    v = (o.raw.get("_props") or {}).get(key)
                    if v:
                        return str(v)
        return ""

    def _advance(self, n: int) -> None:
        self._scan += n
        self._pending = self._state_for(self._scan)
        self._update_tags()

    def _state_for(self, scan: int) -> PageState:
        t = scan * self.scan_ms / 1000.0
        w = 2 * math.pi * t / self.period
        objs = {}
        for a in self._anim:
            s = math.sin(w + a.phase)
            st = ObjectState(a.an)
            if a.move_x or a.move_y:
                st.move = (round(20 * s) if a.move_x else 0, round(10 * s) if a.move_y else 0)
            if a.rotate:
                st.rotate = round(45 * s, 1)
            if a.scale:
                st.scale = (round(1 + 0.25 * s, 3), round(1 + 0.25 * s, 3))
            if a.colour:
                st.fill_colour = 0x00B050 if s >= 0 else 0xE03030
            if a.level:
                st.fill_level = round((s + 1) / 2, 3)
            if a.text:
                st.text = "%.1f" % (50 + 40 * s)
            if a.states > 1:
                st.symbol_state = (scan // 4 + a.index) % a.states
            objs[a.an] = st
        self._serial_state += 1
        return PageState(
            window=0,
            page=getattr(self.page, "name", ""),
            objects=objs,
            flash_on=(scan // 2) % 2 == 0,
            serial=self._serial_state,
        )

    # -- TagSource ---------------------------------------------------------------
    def _update_tags(self) -> None:
        t = self._scan * self.scan_ms / 1000.0
        w = 2 * math.pi * t / self.period
        for k, tag in enumerate(self._tags):
            held = self._held.get(tag.name)
            if held is not None:
                tag.value, tag.quality = held
                continue
            s = math.sin(w + k * 0.5)
            if tag.is_digital():
                tag.value = 1 if s >= 0 else 0
            elif tag.is_string():
                tag.value = tag.value if isinstance(tag.value, str) else ""
            else:
                lo = tag.eng_zero if tag.eng_zero is not None else 0.0
                hi = tag.eng_full if tag.eng_full is not None else 100.0
                tag.value = round(lo + (hi - lo) * (s + 1) / 2, 2)
        self._tag_serial += 1

    def tags(self, page_only: bool = False) -> List[TagInfo]:
        with self._lock:
            return [t for t in self._tags if t.page_reads or not page_only]

    def set_tag(self, name: str, value, quality: Optional[str] = None) -> None:
        with self._lock:
            tag = next((t for t in self._tags if t.name == name), None)
            q = quality or (tag.quality if tag is not None else "Good")
            self._held[name] = (value, q)
            if tag is not None:
                tag.value, tag.quality = value, q
            t = self._scan * self.scan_ms / 1000.0
            self._log.append(
                LogEntry(t, "write", "%s = %s%s" % (name, value, "" if q == "Good" else " (%s)" % q.lower()))
            )
            self._tag_serial += 1

    def serial(self) -> int:
        return self._tag_serial

    # -- LogSource ---------------------------------------------------------------
    def drain(self) -> List[LogEntry]:
        with self._lock:
            out, self._log = self._log, []
            return out

    def not_simulated(self) -> Dict[str, int]:
        return dict(self._not_sim)


def default_simulation(engine_path: Optional[str] = None):
    """The simulation a Simulate tab gets when nothing else is given (the tab starts it on
    its page): the Cicode simulator (ctsim) of the page's project, on demo values until it
    runs the page. ``engine_path`` = the setting 'sim/ctsim_path'."""
    from .ctsim_source import EngineSimulation

    return EngineSimulation(engine_path=engine_path)


#: True while ``default_simulation`` is the demo; the engine source says itself when the
#: page moves on demo values (its ``status()``)
DEMO = False
