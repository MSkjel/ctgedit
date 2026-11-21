"""Window settings."""

from __future__ import annotations

from typing import List

from PySide6 import QtCore, QtWidgets

from .settings import OPTION_DEFAULTS, SETTINGS_KEYS, app_settings, choice

#: Appearance > Document tabs
NEW_TAB_PLACE = (("end", "At the end"), ("next", "Next to the current tab"))
AFTER_CLOSE = (("next", "The tab next to it"), ("last", "The page used before it"))
TAB_WIDTHS = (
    ("narrow", "Narrow (8 characters, 160 px)"),
    ("normal", "Normal (12 characters, 240 px)"),
    ("wide", "Wide (16 characters, 360 px)"),
)
#: a tab's least characters of its name and its most width (px) for each tab width
TAB_WIDTH_SIZES = {"narrow": (8, 160), "normal": (12, 240), "wide": (16, 360)}
#: Ribbon > Search: the scope Quick Open starts with
QUICK_OPEN_SCOPES = (("project", "This project"), ("install", "All projects"), ("last", "The one used last"))
#: Pages and libraries > Run: when File > Compile opens its results window ('errors': also a
#: compile that did not finish)
COMPILE_RESULTS = (
    ("always", "Always"),
    ("problems", "With errors or warnings"),
    ("errors", "With errors only"),
)
#: Appearance > Status bar: 'Warn about pages with more animation points than:'
AN_WARN_RANGE = (500, 20000)
AN_WARN_DEFAULT = 3000

#: the module values (see the module docstring)
CONTEXT_TABS_OPEN = False
AN_WARN = AN_WARN_DEFAULT


#: the on / off settings of this module (read strictly: :func:`flag`); the Settings window
#: shows them as the code reads them
FLAG_KEYS = (
    "ui/ask_unpin",
    "ui/context_tabs_open",
    "ui/palette_keep_search",
    "opt/single_instance",
    "opt/explorer_session",
)
#: the command list settings (:func:`command_ids`)
LIST_KEYS = ("ui/qat", "ui/menu_quick_row", "ui/hud_buttons")


def _default(key: str) -> bool:
    if key.startswith("opt/") and key[4:] in OPTION_DEFAULTS:
        return bool(OPTION_DEFAULTS[key[4:]])
    return bool(SETTINGS_KEYS[key])


def as_flag(raw, default: bool) -> bool:
    """A stored on / off value: a bool as it is, 'true' / '1' on, 'false' / '0' off, anything
    else (garbage, an empty text) ``default`` (Qt's toBool reads any other text as on and an
    empty one as off)."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return raw != 0 if raw in (0, 1) else default
    text = str(raw).strip().lower() if raw is not None else ""
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    return default


def flag(key: str) -> bool:
    """The on / off setting ``key`` of :data:`FLAG_KEYS`, its declared default when it is
    unset or garbled."""
    d = _default(key)
    try:
        st = app_settings()
        if not st.contains(key):
            return d
        return as_flag(st.value(key), d)
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return d


def _ids(key: str) -> List[str]:
    """The command ids of the list setting ``key`` ([] is a choice: none)."""
    st = app_settings()
    if not st.contains(key):
        return list(SETTINGS_KEYS[key])
    try:
        raw = st.value(key, SETTINGS_KEYS[key], type=list)
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return list(SETTINGS_KEYS[key])
    return [str(x) for x in (raw or []) if str(x)]


# --------------------------------------------------------------------------- the values
def new_tab_place() -> str:
    return choice("ui/new_tab_place", NEW_TAB_PLACE, "end")


def after_close() -> str:
    return choice("ui/after_close", AFTER_CLOSE, "next")


def ask_unpin() -> bool:
    return flag("ui/ask_unpin")


def tab_width() -> str:
    return choice("ui/tab_width", TAB_WIDTHS, "normal")


def palette_keep_search() -> bool:
    return flag("ui/palette_keep_search")


def quick_open_scope() -> str:
    """The scope Quick Open starts with ('project' / 'install'): the setting, or with 'The
    one used last' the scope Tab chose last (ui/quick_open_last)."""
    v = choice("ui/quick_open_scope", QUICK_OPEN_SCOPES, "project")
    if v == "last":
        v = choice("ui/quick_open_last", QUICK_OPEN_SCOPES[:2], "project")
    return v


def remember_quick_open_scope(scope: str) -> None:
    """Tab in Quick Open chose ``scope``: kept for 'The one used last'."""
    if scope in ("project", "install"):
        app_settings().setValue("ui/quick_open_last", scope)


def compile_results() -> str:
    return choice("opt/compile_results", COMPILE_RESULTS, "errors")


def an_warn() -> int:
    lo, hi = AN_WARN_RANGE
    try:
        # (as text: Qt reads a garbled number as 0, not as an error)
        v = int(str(app_settings().value("opt/an_warn", 3000)).strip())
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return AN_WARN_DEFAULT
    return max(lo, min(hi, v))


def new_page_name() -> str:
    """The name a new page starts with: the setting when it is a name a page may have, else
    'NewPage' (a free _2, _3 ... is added where it is used when it is taken)."""
    raw = str(app_settings().value("opt/new_page_name", "NewPage") or "").strip()
    if raw:
        from ..browser import naming

        name = naming.normalise_page_name(raw)
        if naming.validate_page_name(name, existing={}, has_pages_dbf=True).ok:
            return name
    return "NewPage"


def single_instance() -> bool:
    return flag("opt/single_instance")


def explorer_session() -> bool:
    return flag("opt/explorer_session")


def load() -> None:
    """The module values from the settings: a window's start and every change in
    Settings."""
    global CONTEXT_TABS_OPEN, AN_WARN
    from ..panels import doc_tabs

    doc_tabs.MIN_CHARS, doc_tabs.MAX_TAB_WIDTH = TAB_WIDTH_SIZES[tab_width()]
    CONTEXT_TABS_OPEN = flag("ui/context_tabs_open")
    AN_WARN = an_warn()


# --------------------------------------------------------------------------- the commands
def actions_of(win, ids) -> list:
    """The window's commands named by ``ids``, in order (a command it does not have is
    left out, each once)."""
    from . import keymap

    out = []
    for cid in ids:
        a = keymap.find_action(win, cid)
        if a is not None and not any(a is b for b in out):
            out.append(a)
    return out


def command_ids(win, key: str) -> List[str]:
    """The command ids of the list setting ``key`` as the window uses them: the stored list,
    or the default when the stored one names no command of ``win`` (a garbled value; an
    empty list stays: none)."""
    from . import keymap

    ids = _ids(key)
    if ids and all(keymap.find_action(win, cid) is None for cid in ids):
        return list(SETTINGS_KEYS[key])
    return ids


def quick_access_actions(win) -> list:
    return actions_of(win, command_ids(win, "ui/qat"))


def menu_quick_actions(win) -> list:
    return actions_of(win, command_ids(win, "ui/menu_quick_row"))


def hud_actions(win) -> list:
    return actions_of(win, command_ids(win, "ui/hud_buttons"))


def _save_menu(win) -> QtWidgets.QMenu:
    """The arrow of Save in Quick Access: Save As, Save All, the recent pages."""
    sm = QtWidgets.QMenu(win)
    sm.addAction(win.act_save_as)
    sm.addAction(win.act_save_all)
    mru = sm.addMenu("Recent Pages")
    mru.aboutToShow.connect(lambda m=mru: win._fill_mru_into(m))
    return sm


def build_quick_access(win) -> None:
    """The Quick Access row of ``win``'s ribbon from Settings > Ribbon > Quick Access (Save
    keeps its arrow of Save As, Save All and the recent pages)."""
    rb = getattr(win, "ribbon", None)
    if rb is None:
        return
    rb.clear_qat()
    save = getattr(win, "act_save", None)
    for a in quick_access_actions(win):
        if a is save:
            rb.add_qat_split(a, _save_menu(win))
        else:
            rb.add_qat(a)
    # (the header's Search field and utility buttons fit the row's new width: they are
    # fitted only when the header is resized; at the start the field is not made yet)
    hs = getattr(win, "header_search", None)
    if hs is not None:
        QtCore.QTimer.singleShot(0, hs, hs.fit)


# --------------------------------------------------------------------------- live apply
def _apply_tabs(win) -> None:
    load()
    bar = getattr(win, "doc_tabs", None)
    if bar is not None:
        bar.relayout()


def _apply_values(_win) -> None:
    load()


def _apply_an_warn(win) -> None:
    load()
    fn = getattr(win, "_update_an_count", None)
    if callable(fn):
        fn()


def _apply_hud(win) -> None:
    hud = getattr(win, "hud", None)
    if hud is not None:
        hud.set_actions(hud_actions(win))
        sched = getattr(win, "_schedule_hud", None)
        if callable(sched):
            sched()


def _apply_quick_open(win) -> None:
    pop = getattr(win, "quick_open_popup", None)
    if pop is not None:
        pop.set_scope(quick_open_scope())


def _register() -> None:
    from . import settings_pages as SP

    for key, fn in (
        ("ui/tab_width", _apply_tabs),
        ("ui/context_tabs_open", _apply_values),
        ("opt/an_warn", _apply_an_warn),
        ("ui/qat", build_quick_access),
        ("ui/hud_buttons", _apply_hud),
        ("ui/quick_open_scope", _apply_quick_open),
    ):
        if fn not in SP.APPLY_HOOKS.get(key, []):
            SP.register_apply(key, fn)


_register()
