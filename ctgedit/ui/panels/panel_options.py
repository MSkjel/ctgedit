"""Panel settings, as module values the panels read."""

from __future__ import annotations

from ...infra import caches as _caches
from ..prefs.settings import app_settings, choice

#: Label drag speed: the units a plain drag moves per screen pixel (Shift 10 times, Alt a
#: quarter of it)
SCRUB_SPEEDS = (("slow", "Slow (1 per 4 px)"), ("normal", "Normal (1 per 2 px)"), ("fast", "Fast (1 per px)"))
SCRUB_RATES = {"slow": 0.25, "normal": 0.5, "fast": 1.0}
#: the Objects panel's double-click on a row
OBJECTS_DOUBLE_CLICK = (
    ("zoom", "Zooms to the object"),
    ("scroll", "Scrolls to it"),
    ("props", "Opens its properties"),
    ("rename", "Renames it"),
)
#: the Library panel's preview of the card under the pointer: its picture's box (px)
LIBRARY_PREVIEWS = (("off", "Off"), ("normal", "Normal"), ("large", "Large"))
LIBRARY_PREVIEW_BOX = {"normal": (248, 180), "large": (372, 270)}
#: the page browser's preview pane (File > Open, Save As and the pickers)
PREVIEW_PANES = (("auto", "When the window is wide"), ("always", "Always"), ("never", "Never"))
PREVIEW_SHOWS = (("thumbnail", "The saved picture"), ("live", "A fresh drawing of the page"))
#: the letters an expression field waits for before it lists the names (0 = never)
TAG_SUGGEST = ((1, "1 letter"), (2, "2 letters"), (3, "3 letters"), (0, "Never"))
#: the tab Object Properties opens on
OPEN_TABS = (("first", "The first tab"), ("last", "The tab used last for that kind"))

#: the keys :func:`apply` reads (each has an apply hook)
KEYS = (
    "opt/scrub_speed",
    "opt/tag_suggest",
    "objprops/open_tab",
    "draw/style_preview",
    "library/hover_preview",
    "browser/fuzzy",
    "browser/short_names",
    "browser/preview_pane",
    "browser/preview_live",
)

# the values the panels read (the defaults)
SCRUB_RATE = 0.5
TAG_SUGGEST_AFTER = 2
OPEN_TAB = "first"
STYLE_PREVIEW = True
LIBRARY_PREVIEW = "normal"
SHORT_NAMES = True
PREVIEW_PANE = "auto"
PREVIEW_LIVE = "thumbnail"


def _flag(key: str, default: bool) -> bool:
    try:
        return bool(app_settings().value(key, default, type=bool))
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return default


def tag_suggest_after() -> int:
    """opt/tag_suggest: 1, 2 or 3 letters, 0 = never (2 when unset or garbled)."""
    try:
        # (as text: Qt reads a garbled number as 0, which here means never)
        v = int(str(app_settings().value("opt/tag_suggest", 2)).strip())
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return 2
    return v if v in dict(TAG_SUGGEST) else 2


def apply(_window=None) -> None:
    """Read the panels' choices into the module values (cheap: a window start and each
    change in the Settings window)."""
    global SCRUB_RATE, TAG_SUGGEST_AFTER, OPEN_TAB, STYLE_PREVIEW, LIBRARY_PREVIEW
    global SHORT_NAMES, PREVIEW_PANE, PREVIEW_LIVE
    SCRUB_RATE = SCRUB_RATES[choice("opt/scrub_speed", SCRUB_SPEEDS, "normal")]
    TAG_SUGGEST_AFTER = tag_suggest_after()
    OPEN_TAB = choice("objprops/open_tab", OPEN_TABS, "first")
    STYLE_PREVIEW = _flag("draw/style_preview", True)
    LIBRARY_PREVIEW = choice("library/hover_preview", LIBRARY_PREVIEWS, "normal")
    SHORT_NAMES = _flag("browser/short_names", True)
    PREVIEW_PANE = choice("browser/preview_pane", PREVIEW_PANES, "auto")
    PREVIEW_LIVE = choice("browser/preview_live", PREVIEW_SHOWS, "thumbnail")
    # (the search ranking is pure Python: it keeps its own switch)
    from ..browser import search

    search.FUZZY = _flag("browser/fuzzy", True)
    _register_hooks()


def _refresh_library(window) -> None:
    """'Also find names with the letters in order' changed: the Library panel's results
    again for the search typed (with those matches or without)."""
    apply()
    panel = getattr(window, "library_dock", None)
    if panel is not None:
        panel.refresh_search()


def _library_preview(window) -> None:
    """'Preview of a card you point at' changed: the large preview shown is put away (the
    next one has the new size, or a tool tip with Off); the results and the card selected
    stay as they are."""
    apply()
    panel = getattr(window, "library_dock", None)
    if panel is not None:
        panel.hide_preview()


def _style_tips(window) -> None:
    """'Preview a style on the page when you point at it' changed: the Properties panel's
    style tiles say what pointing at one does now."""
    apply()
    from PySide6 import QtWidgets
    from .properties_widgets import styles_strip_tip

    for g in window.findChildren(QtWidgets.QWidget, "panelStyles"):
        g.setToolTip(styles_strip_tip())


def reset() -> None:
    """Every value back to its default (tests: a module value outlives a test's settings)."""
    global SCRUB_RATE, TAG_SUGGEST_AFTER, OPEN_TAB, STYLE_PREVIEW, LIBRARY_PREVIEW
    global SHORT_NAMES, PREVIEW_PANE, PREVIEW_LIVE
    SCRUB_RATE, TAG_SUGGEST_AFTER, OPEN_TAB, STYLE_PREVIEW = 0.5, 2, "first", True
    LIBRARY_PREVIEW, SHORT_NAMES = "normal", True
    PREVIEW_PANE, PREVIEW_LIVE = "auto", "thumbnail"
    from ..browser import search

    search.FUZZY = True


def _register_hooks() -> None:
    from ..prefs import settings_pages as SP

    for key in KEYS:
        if apply not in SP.APPLY_HOOKS.get(key, []):
            SP.register_apply(key, apply)
    for key, fn in (
        ("browser/fuzzy", _refresh_library),
        ("library/hover_preview", _library_preview),
        ("draw/style_preview", _style_tips),
    ):
        if fn not in SP.APPLY_HOOKS.get(key, []):
            SP.register_apply(key, fn)


# (the values are state the Settings window sets, not a cache: a test module starts with the
# defaults, as a window made with fresh settings reads them)
_caches.register("ui.panel_options", KEYS, clear=reset, on=("tests",))
