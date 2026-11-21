"""Timings and counts as settings."""

from __future__ import annotations

from ...infra import caches as _caches
from . import settings as S

#: the keys :func:`apply` reads (each has an apply hook); their spans are in
#: settings.WHOLE_SPANS
KEYS = (
    "opt/expression_check_ms",
    "opt/props_step_pause_ms",
    "objects/rename_click_ms",
    "opt/autosave_idle_s",
    "view/undo_flash_ms",
    "ui/hud_delay_ms",
    "ui/navbar_fade_px",
    "ui/navbar_faded_pct",
    "ui/quick_open_rows",
    "ui/palette_rows",
    "view/last_views_kept",
    "opt/underlays_kept",
    "view/saved_views_max",
    "library/recent_kept",
    "ui/settings_live_delay_ms",
)

#: Quick Open: the tags and the objects a search looks at for each row it shows
TAGS_PER_ROW = 5
OBJECTS_PER_ROW = 1.5

# the values (the defaults are declared once in settings.SETTINGS_KEYS)
_D = S.SETTINGS_KEYS
EXPRESSION_CHECK_MS = _D["opt/expression_check_ms"]
STEP_PAUSE_MS = _D["opt/props_step_pause_ms"]
RENAME_CLICK_MS = _D["objects/rename_click_ms"]
AUTOSAVE_IDLE_S = _D["opt/autosave_idle_s"]
UNDO_FLASH_MS = _D["view/undo_flash_ms"]
HUD_DELAY_MS = _D["ui/hud_delay_ms"]
NAVBAR_FADE_PX = _D["ui/navbar_fade_px"]
NAVBAR_FADED_PCT = _D["ui/navbar_faded_pct"]
QUICK_OPEN_ROWS = _D["ui/quick_open_rows"]
PALETTE_ROWS = _D["ui/palette_rows"]
LAST_VIEWS_KEPT = _D["view/last_views_kept"]
UNDERLAYS_KEPT = _D["opt/underlays_kept"]
SAVED_VIEWS_MAX = _D["view/saved_views_max"]
LIBRARY_RECENT_KEPT = _D["library/recent_kept"]
SETTINGS_LIVE_DELAY_MS = _D["ui/settings_live_delay_ms"]


def _read(key: str) -> int:
    return S.whole(key, S.WHOLE_SPANS[key])


def apply(_window=None) -> None:
    """Read the values from the settings (cheap: a window start and each change in the
    Settings window)."""
    global EXPRESSION_CHECK_MS, STEP_PAUSE_MS, RENAME_CLICK_MS, AUTOSAVE_IDLE_S
    global UNDO_FLASH_MS, HUD_DELAY_MS, NAVBAR_FADE_PX, NAVBAR_FADED_PCT, QUICK_OPEN_ROWS
    global PALETTE_ROWS
    global LAST_VIEWS_KEPT, UNDERLAYS_KEPT, SAVED_VIEWS_MAX, LIBRARY_RECENT_KEPT
    global SETTINGS_LIVE_DELAY_MS
    EXPRESSION_CHECK_MS = _read("opt/expression_check_ms")
    STEP_PAUSE_MS = _read("opt/props_step_pause_ms")
    RENAME_CLICK_MS = _read("objects/rename_click_ms")
    AUTOSAVE_IDLE_S = _read("opt/autosave_idle_s")
    UNDO_FLASH_MS = _read("view/undo_flash_ms")
    HUD_DELAY_MS = _read("ui/hud_delay_ms")
    NAVBAR_FADE_PX = _read("ui/navbar_fade_px")
    NAVBAR_FADED_PCT = _read("ui/navbar_faded_pct")
    QUICK_OPEN_ROWS = _read("ui/quick_open_rows")
    PALETTE_ROWS = _read("ui/palette_rows")
    LAST_VIEWS_KEPT = _read("view/last_views_kept")
    UNDERLAYS_KEPT = _read("opt/underlays_kept")
    SAVED_VIEWS_MAX = _read("view/saved_views_max")
    LIBRARY_RECENT_KEPT = _read("library/recent_kept")
    SETTINGS_LIVE_DELAY_MS = _read("ui/settings_live_delay_ms")
    _register_hooks()


def quick_open_limits() -> tuple:
    """(rows, tags, objects): the rows Quick Open shows at most, and the tags and the
    objects one search looks at (so a longer list is not cut by the search's own limit)."""
    rows = QUICK_OPEN_ROWS
    return rows, rows * TAGS_PER_ROW, int(rows * OBJECTS_PER_ROW)


def _autosave(_window) -> None:
    """'Also after a pause in editing of' changed: the controller looks as often as the pause
    needs."""
    apply()
    from .. import recovery

    if recovery.has_controller():
        recovery.controller().apply_settings()


def reset() -> None:
    """Every value back to its default (tests: a module value outlives a test's settings)."""
    global EXPRESSION_CHECK_MS, STEP_PAUSE_MS, RENAME_CLICK_MS, AUTOSAVE_IDLE_S
    global UNDO_FLASH_MS, HUD_DELAY_MS, NAVBAR_FADE_PX, NAVBAR_FADED_PCT, QUICK_OPEN_ROWS
    global PALETTE_ROWS
    global LAST_VIEWS_KEPT, UNDERLAYS_KEPT, SAVED_VIEWS_MAX, LIBRARY_RECENT_KEPT
    global SETTINGS_LIVE_DELAY_MS
    EXPRESSION_CHECK_MS = _D["opt/expression_check_ms"]
    STEP_PAUSE_MS = _D["opt/props_step_pause_ms"]
    RENAME_CLICK_MS = _D["objects/rename_click_ms"]
    AUTOSAVE_IDLE_S = _D["opt/autosave_idle_s"]
    UNDO_FLASH_MS = _D["view/undo_flash_ms"]
    HUD_DELAY_MS = _D["ui/hud_delay_ms"]
    NAVBAR_FADE_PX = _D["ui/navbar_fade_px"]
    NAVBAR_FADED_PCT = _D["ui/navbar_faded_pct"]
    QUICK_OPEN_ROWS = _D["ui/quick_open_rows"]
    PALETTE_ROWS = _D["ui/palette_rows"]
    LAST_VIEWS_KEPT = _D["view/last_views_kept"]
    UNDERLAYS_KEPT = _D["opt/underlays_kept"]
    SAVED_VIEWS_MAX = _D["view/saved_views_max"]
    LIBRARY_RECENT_KEPT = _D["library/recent_kept"]
    SETTINGS_LIVE_DELAY_MS = _D["ui/settings_live_delay_ms"]


def _last_views(window) -> None:
    """'Remember the view of at most' changed: the views beyond it go now, not at the next
    page close."""
    apply()
    window._prune_views(S.app_settings())


def _underlays(_window) -> None:
    """'Remember the underlay of at most' changed: the underlays beyond it go now."""
    apply()
    from ..editor import underlay

    underlay.prune_remembered(S.app_settings())


def _library_recent(window) -> None:
    """'Recent elements kept' changed: the Library panel's Recent section shows that many
    now, not after the next placement."""
    apply()
    panel = getattr(window, "library_dock", None)
    if panel is not None:
        panel.trim_recent()


def _navbar_faded(window) -> None:
    """'Faded to' changed: a navigation bar faded now takes the opacity at once."""
    apply()
    bar = getattr(window, "navbar", None)
    if bar is not None and bar.faded():
        bar._set_effect()


#: the keys whose hook does more than read the values
_HOOKS = {
    "opt/autosave_idle_s": _autosave,
    "view/last_views_kept": _last_views,
    "opt/underlays_kept": _underlays,
    "library/recent_kept": _library_recent,
    "ui/navbar_faded_pct": _navbar_faded,
}


def _register_hooks() -> None:
    from . import settings_pages as SP

    for key in KEYS:
        fn = _HOOKS.get(key, apply)
        if fn not in SP.APPLY_HOOKS.get(key, []):
            SP.register_apply(key, fn)


_register_hooks()

# (the values are state the Settings window sets, not a cache: a test module starts with the
# defaults, as a window made with fresh settings reads them)
_caches.register("ui.timing_options", KEYS, clear=reset, on=("tests",))
