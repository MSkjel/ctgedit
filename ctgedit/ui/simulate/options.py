"""Simulate options: setting keys and defaults."""

from __future__ import annotations

from typing import Any, Dict, Tuple

#: name -> (setting key, default)
OPTIONS: Dict[str, Tuple[str, Any]] = {
    # a page change in a window (PageDisplay, PageGoto, a menu ...) opens the new page in a
    # tab of its own; the tab of the page it left keeps its last picture
    "new_tab_per_page": ("sim/new_tab_per_page", False),
    # every window the page's Cicode opens (a pinned pane, a popup, WinNew) gets a tab of its
    # own, instead of being drawn inside its parent window (a pane) or over the Simulate
    # window (a popup), as the runtime shows them
    "tab_per_window": ("sim/tab_per_window", False),
    # the clock runs as soon as a page opens in the Simulate window
    "autorun": ("sim/autorun", True),
    # closing the Simulate window (or its docked panel, whose X sits next to the float
    # button) asks first when a scenario is being recorded or tags were set by hand
    "confirm_close": ("sim/confirm_close", True),
    # who is logged in when a simulation starts: 'full', 'none' or 'user:<name>'
    "user": ("sim/user", "full"),
    "simulate_undefined": ("sim/simulate_undefined", False),
    # a simulation ctgedit starts runs what the runtime runs at a client's start: the startup
    # page in window 0, Include's _CitectBegin and the project's StartupCode, then the page
    "run_startup": ("sim/run_startup", True),
    # trends show a pen's first value before the simulation started, so a long-span trend
    # shows its whole span at once (a simulator option: the runtime shows its trend archive)
    "seed_trend_history": ("sim/seed_trend_history", True),
    # the debugger (VS Code) stops page code too: a breakpoint in an expression or a function
    # it calls holds the whole simulation until the debugger continues (a simulator option:
    # the runtime never stops foreground Cicode, 'Foreground Cicode cannot break')
    "foreground_break": ("sim/foreground_break", True),
    # with foreground_break: a hardware error in page code stops it too (ctsim/options
    # foregroundErrorBreak; off: reported, the page runs on, as the runtime)
    "foreground_error_break": ("sim/foreground_error_break", False),
    # the startup code runs on a clock 20 times faster until it settles (load fastStartup);
    # off: at the clock's own speed, as the runtime
    "fast_startup": ("sim/fast_startup", True),
    # what a text whose value comes from code shows until the simulator sends the value:
    # 'abc' (ctgedit's placeholder) or '' (nothing)
    "value_placeholder": ("sim/value_placeholder", "abc"),
    # an SA content page runs inside its workspace, as in the runtime: the startup page in
    # window 0 (the tab), the page shown in its pane through the workspace (needs run_startup)
    "sa_workspace": ("sim/sa_workspace", True),
    # a .ci file of the simulated project changed on disk: the simulator compiles it again
    # (ctsim/reload; a simulator another client started is left to that client)
    "reload_cicode": ("sim/reload_cicode", True),
    # the page (or a library it uses) changed in the editor or on disk: it opens again in the
    # simulator about a second after the last change (Reload Page by itself)
    "reload_page": ("sim/reload_page", True),
    # with reload_page: the editor's own update of the page (its genies brought up to date
    # from changed libraries after the simulation started) reloads it too; off, the page runs
    # on as the user saw it and the note offers Reload Page (window._auto_only)
    "reload_auto_updates": ("sim/reload_auto_updates", False),
    # Page Keys: the keys typed while the page has the focus go to the simulation (its
    # keyboard commands); off, they stay ctgedit's (its shortcuts)
    "keys_to_page": ("sim/keys_to_page", True),
    # whether the simulation may load DLLs (DLLOpen, DllClassCreate) and reach SQL databases
    # (SQLConnect ...): 'off' (neither is reached: the calls fail as for a missing file or a
    # database that is down), 'ask' (a question first, see the allowed lists) or 'on'. Off by
    # default: a simulated page must not run foreign code or change a real database by itself
    "dll_access": ("sim/dll_access", "off"),
    "sql_access": ("sim/sql_access", "off"),
    # what Ask does not ask about (JSON lists): DLLs by their full path, databases by the
    # name the question shows (the connection string's source without user and password)
    "dll_allowed": ("sim/dll_allowed", "[]"),
    "sql_allowed": ("sim/sql_allowed", "[]"),
    # seconds a DLL call may take before the simulator ends the library and fails the call;
    # 0 = as long as it takes, as the runtime (a message box a DLL shows waits for its click)
    "dll_timeout": ("sim/dll_timeout", 0),
    # the Watch tab's watches (one a line: a tag, or one function call)
    "watch_list": ("sim/watch_list", ""),
    "show_errors": ("sim/show_errors", True),
    "show_numbers": ("sim/show_numbers", False),
    # a developer's aid: made-up sine values while no simulator runs the page (off: the page
    # shows as it is saved)
    "demo_values": ("sim/demo_values", False),
    # text without smoothing, as the runtime draws it
    "aliased_text": ("sim/aliased_text", True),
    # the Outputs log lines each tab keeps
    "log_lines": ("sim/log_lines", 5000),
    # the Outputs log shows the same line from the same place once, with a count
    "log_group": ("sim/log_group", True),
    # the Outputs log's filters: the severities shown, the types turned off, the messages
    # hidden from a row's menu (JSON rules)
    "log_severities": ("sim/log_severities", "error,warning,info"),
    # Task lines (every task started / ended: thousands a minute) are off until asked for
    "log_types_off": ("sim/log_types_off", "task"),
    "log_hidden": ("sim/log_hidden", "[]"),
    # how a new tab shows its page: 'fit' (fitted to the tab, and fitted again as the tab's
    # size changes until you zoom or pan) or 'actual' (at 100 %, as the runtime shows it)
    "open_zoom": ("sim/open_zoom", "fit"),
    # Space held + a drag pans the page; off: Space goes to the page's keyboard commands, as
    # in the runtime (the middle button still pans)
    "space_pans": ("sim/space_pans", True),
    # the speed a new simulation's clock starts at (times real time: one of SPEEDS)
    "start_speed": ("sim/start_speed", 1.0),
    # what Step runs: 'scan' (the simulator's own step, one page scan), 'ten' (ten scans) or
    # 'second' (one second of simulated time)
    "step": ("sim/step", "scan"),
    # the pauses and counts. With reload_page: the page reloads this long
    # after the last edit (s); with
    # reload_cicode: a changed .ci goes to the simulator this long after its last change
    # (ms); the watched folders are compared this often as well (s); the Inspector and the
    # Watch tab refresh this often (ms); the Inputs panel lists this many tags at most
    "reload_delay_s": ("sim/reload_delay_s", 1.0),
    "cicode_delay_ms": ("sim/cicode_delay_ms", 500),
    "folder_check_s": ("sim/folder_check_s", 2),
    "inspector_refresh_ms": ("sim/inspector_refresh_ms", 500),
    "watch_refresh_ms": ("sim/watch_refresh_ms", 1000),
    "tag_rows": ("sim/tag_rows", 2000),
}

LOG_LINES_RANGE = (500, 100000)
# the spans of the numbers above (get() keeps a stored number within its span)
RELOAD_DELAY_RANGE = (0.5, 10.0)
CICODE_DELAY_RANGE = (100, 5000)
FOLDER_CHECK_RANGE = (1, 60)
REFRESH_RANGE = (100, 5000)
TAG_ROWS_RANGE = (500, 20000)
SPANS = {
    "log_lines": LOG_LINES_RANGE,
    "reload_delay_s": RELOAD_DELAY_RANGE,
    "cicode_delay_ms": CICODE_DELAY_RANGE,
    "folder_check_s": FOLDER_CHECK_RANGE,
    "inspector_refresh_ms": REFRESH_RANGE,
    "watch_refresh_ms": REFRESH_RANGE,
    "tag_rows": TAG_ROWS_RANGE,
}

#: the clock bar's speeds (times real time)
SPEEDS = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0)
#: Simulate Settings > Open a page at (the words of Settings > Canvas > Open a page at)
OPEN_ZOOMS = (("fit", "Fit the page in the window"), ("actual", "100 %"))
#: Simulate Settings > Step runs: id, label, page scans (0: one second of simulated time)
STEPS = (("scan", "One scan", 1), ("ten", "Ten scans", 10), ("second", "One second", 0))


def speed_label(v: float) -> str:
    """A speed as the clock bar names it ('0.25x', '1x', '10x')."""
    return ("%g" % v) + "x"


def open_zoom(value: Any = None) -> str:
    """``open_zoom`` (or ``value``, a stored text) as 'fit' or 'actual' (anything else is
    'fit'). The Settings window shows what this gives (SimSettingsControls._fill)."""
    v = str(get("open_zoom") if value is None else value).strip().lower()
    return v if v in dict(OPEN_ZOOMS) else "fit"


def start_speed(value: Any = None) -> float:
    """``start_speed`` (or ``value``): one of :data:`SPEEDS` (anything else is 1x, real
    time)."""
    try:
        v = float(get("start_speed") if value is None else value)
    except TypeError, ValueError:  # swallow-ok: a garbled value is real time
        return 1.0
    return v if v in SPEEDS else 1.0


def step(value: Any = None) -> str:
    """``step`` (or ``value``) as 'scan', 'ten' or 'second' (anything else is 'scan')."""
    v = str(get("step") if value is None else value).strip().lower()
    return v if v in {s[0] for s in STEPS} else "scan"


def step_seconds() -> Any:
    """The simulated seconds a Step runs (sent with ctsim/clock 'step'): None for one scan,
    the simulator's own step (0.25 s)."""
    mode = step()
    scans = next(n for k, _l, n in STEPS if k == mode)
    if mode == "scan":
        return None
    if not scans:
        return 1.0
    from ...sim.client import SCAN_SECONDS

    return scans * SCAN_SECONDS


#: the stored texts of a check box option (anything else is its default: _coerce)
TRUE_TEXTS = ("1", "true", "yes", "on")
FALSE_TEXTS = ("0", "false", "no", "off")


def _coerce(value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        # (a garbled value is the default, not off)
        v = str(value).strip().lower()
        return True if v in TRUE_TEXTS else False if v in FALSE_TEXTS else default
    if isinstance(default, float):
        try:
            return float(value)
        except TypeError, ValueError:
            return default
    if isinstance(default, int):
        try:
            return int(float(value))
        except TypeError, ValueError:
            return default
    return "" if value is None else str(value)


#: options where an empty stored value is a choice, not 'not set' (no log type hidden)
EMPTY_IS_A_VALUE = frozenset({"log_types_off", "value_placeholder"})


def get(name: str) -> Any:
    key, default = OPTIONS[name]
    try:
        from ..prefs.settings import app_settings

        v = app_settings().value(key, None)
    except Exception:  # noqa: BLE001 -- a setting is a convenience: the default
        return default
    if v == "" and name in EMPTY_IS_A_VALUE:
        return ""
    if v is None or v == "":
        return default
    v = _coerce(v, default)
    span = SPANS.get(name)
    if span is not None:
        v = max(span[0], min(span[1], v))
    return v


def put(name: str, value: Any) -> None:
    key, default = OPTIONS[name]
    if isinstance(default, bool):
        value = "true" if value else "false"
    try:
        from ..prefs.settings import app_settings

        app_settings().setValue(key, value)
    except Exception:  # noqa: BLE001
        pass


def log_lines() -> int:
    """The Outputs log lines kept (any value of LOG_LINES_RANGE: 500 keeps 500)."""
    return int(get("log_lines"))


def reload_delay_s() -> float:
    """With Reload the page: the pause after the last edit before the page reloads (s)."""
    return float(get("reload_delay_s"))


def cicode_delay_ms() -> int:
    """With Reload Cicode: the pause after a .ci file's last change before it is sent."""
    return int(get("cicode_delay_ms"))


def folder_check_ms() -> int:
    """How often the watched folders are compared as well (ms)."""
    return int(get("folder_check_s")) * 1000


def inspector_refresh_ms() -> int:
    return int(get("inspector_refresh_ms"))


def watch_refresh_ms() -> int:
    return int(get("watch_refresh_ms"))


def tag_rows() -> int:
    """The tags the Inputs panel lists at most (and one search fetches)."""
    return int(get("tag_rows"))


#: the choices of dll_access / sql_access
ACCESS_MODES = ("off", "ask", "on")


def access(name: str) -> str:
    """``dll_access`` / ``sql_access`` as 'off', 'ask' or 'on' (anything else is off)."""
    v = str(get(name)).strip().lower()
    return v if v in ACCESS_MODES else "off"


DLL_TIMEOUT_MAX = 3600


def dll_timeout() -> int:
    """``dll_timeout`` in seconds, 0 (no limit) to DLL_TIMEOUT_MAX."""
    return max(0, min(DLL_TIMEOUT_MAX, int(get("dll_timeout"))))


def parse_list(text: Any) -> list:
    """A stored list (JSON) as its texts, without empty ones or repeats (case ignored)."""
    import json

    try:
        items = json.loads(text) if isinstance(text, str) else list(text or ())
    except TypeError, ValueError:
        items = []
    out, seen = [], set()
    for x in items if isinstance(items, list) else []:
        x = str(x).strip()
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def get_list(name: str) -> list:
    """``dll_allowed`` / ``sql_allowed`` as a list of texts."""
    return parse_list(get(name))


def put_list(name: str, items) -> None:
    import json

    put(name, json.dumps(parse_list(list(items))))


def add_to_list(name: str, item: str) -> bool:
    """Add one entry; False when it was there already."""
    items = get_list(name)
    if not str(item).strip() or str(item).strip().lower() in {x.lower() for x in items}:
        return False
    put_list(name, items + [str(item).strip()])
    return True
