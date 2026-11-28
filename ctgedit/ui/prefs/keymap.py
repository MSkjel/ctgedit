"""Command ids and the keyboard map."""

from __future__ import annotations

import json
import os
from typing import Dict, Iterable, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from .settings import app_settings

#: the preset setting and its default
KEYMAP_SETTING = "ui/prefs/keymap"
DEFAULT_PRESET = "ctgedit"
#: the user's own keys: KEYS_PREFIX + command id
KEYS_PREFIX = "keys/"
#: the preset files
#: the preset keymaps: ctgedit/ui/keymaps/
PRESET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "keymaps")
#: the action property holding the keys a command had before any preset or user key
DEFAULT_PROP = "ctg_default_keys"
#: the action property holding the shortcut context a command had before any preset or user
#: key (an int: Qt.ShortcutContext); its built-in keys keep it
DEFAULT_CTX_PROP = "ctg_default_context"
#: an action property: a drawing tool (its bare letter works on the canvas)
TOOL_PROP = "ctg_tool"
#: the action property holding the command's own name (Undo stays 'Undo' while the menu
#: says 'Undo Move')
LABEL_PROP = "ctg_label"
#: an action property: more words a search finds the command by (list of str)
KEYWORDS_PROP = "ctg_keywords"
#: an action property: those of its built-in keys that work on the canvas only (list of str),
#: while its other keys keep the command's scope (Edit > Delete: '-' and the keypad minus)
CANVAS_KEYS_PROP = "ctg_canvas_keys"
#: a QShortcut property: the id of the command whose canvas-only key it carries
CANVAS_KEY_OF_PROP = "ctg_canvas_key_of"
#: an action property: the canvas-only keys it has now (apply_keymap)
CANVAS_KEYS_NOW_PROP = "ctg_canvas_keys_now"
#: a QShortcut property: a keypad key of the canvas that runs nothing (_place_canvas_keys)
CANVAS_KEY_BLOCK_PROP = "ctg_canvas_key_block"
#: the keys of a command that work on the canvas only when it has them (main_window sets
#: :data:`CANVAS_KEYS_PROP` from it): Delete's minus keys (CtDraw's), not given by default
BUILT_IN_CANVAS_KEYS: Dict[str, Tuple[str, ...]] = {"act_del": ("-", "Num+-")}

#: the Simulate window's own commands (ui/simulate/window.py, SimulateWindow.sim_actions): id,
#: name, built-in keys. Their keys work in the Simulate window only (docked too: while the
#: focus is in it), so they take no key from an editor command and give none: Settings >
#: Keyboard lists them with the Menu 'Simulate window', the user's own keys are stored as
#: the editor's (keys/sim.run ...) and no keymap preset changes them (:func:`sim_resolve`)
SIM_COMMANDS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("sim.run", "Run", ("F5",)),
    ("sim.pause", "Pause", ("Shift+F5",)),
    ("sim.step", "Step", ("F10",)),
    ("sim.open_page", "Open Page…", ("Ctrl+O",)),
    ("sim.copy_image", "Copy Image", ("Ctrl+Shift+C",)),
    ("sim.inspector", "Inspector", ("Ctrl+I",)),
    # the editor's zoom commands on the editor's keys; the panels on Alt+1 / Alt+2, the
    # editor's View > Panels keys
    ("sim.zoom_fit", "Fit Page", ("Ctrl+0",)),
    ("sim.zoom_100", "Zoom 100%", ("Ctrl+1",)),
    ("sim.zoom_in", "Zoom In", ("Ctrl+=", "Ctrl++")),
    ("sim.zoom_out", "Zoom Out", ("Ctrl+-",)),
    ("sim.inputs", "Inputs panel", ("Alt+1",)),
    ("sim.outputs", "Outputs panel", ("Alt+2",)),
)
SIM_PREFIX = "sim."
#: the Menu column of the Simulate window's commands
SIM_CRUMB = "Simulate window"
#: the Simulate window's commands whose keys stay the window's while the simulated page has
#: the keyboard (SimCanvas.RESERVED_KEYS); with Page Keys on the page gets the other keys
SIM_RESERVED = (
    "sim.open_page",
    "sim.copy_image",
    "sim.inspector",
    "sim.zoom_fit",
    "sim.zoom_100",
    "sim.zoom_in",
    "sim.zoom_out",
)
#: the keys the simulated page keeps (Space held pans it): no command of it can take them
SIM_FIXED_KEYS = ("Space",)
#: the format of an exported keyboard file
EXPORT_FORMAT = 1

#: where a command's keys work (SIMULATE: in the Simulate window, its own commands)
WINDOW, CANVAS, SIMULATE = "window", "canvas", "simulate"
SCOPE_NAMES = {WINDOW: "Window", CANVAS: "Canvas", SIMULATE: "Simulate"}

#: the commands F4 (Edit > Repeat) repeats: they act on the selection or the page, open no
#: window and are no tool (id prefixes and ids)
REPEAT_PREFIXES = ("align.", "act_dist_", "textflag.", "justify.", "effect.")
REPEAT_IDS = (
    "act_rot_cw",
    "act_rot_ccw",
    "act_flip_h",
    "act_flip_v",
    "act_paste_appearance",
    "act_same_size",
    "act_front",
    "act_back",
    "act_fwd",
    "act_bwd",
    "act_group",
    "act_ungroup",
    "act_lock",
    "act_unlock",
)


# ----------------------------------------------------------------------------- key text
def norm(key) -> str:
    """One key (text or QKeySequence) in the portable form ('Ctrl+Shift+Z'); '' = none."""
    try:
        seq = key if isinstance(key, QtGui.QKeySequence) else QtGui.QKeySequence(str(key or ""))
        return seq.toString(QtGui.QKeySequence.PortableText)
    except TypeError, ValueError:  # swallow-ok: text that is no key is no key
        return ""


def native(key: str) -> str:
    """A key as the menus show it."""
    return QtGui.QKeySequence(key).toString(QtGui.QKeySequence.NativeText) if key else ""


def keys_of(action) -> List[str]:
    try:
        return [k for k in (norm(s) for s in action.shortcuts()) if k]
    except RuntimeError:  # swallow-ok: a deleted action has no keys
        return []


def _clean(keys: Iterable) -> List[str]:
    out = []
    for k in keys or ():
        k = norm(k)
        if k and k not in out:
            out.append(k)
    return out


def canvas_keys(action) -> List[str]:
    """The keys of ``action`` that work on the canvas only (:data:`CANVAS_KEYS_PROP`)."""
    try:
        v = action.property(CANVAS_KEYS_PROP) if action is not None else None
    except RuntimeError:  # swallow-ok: a deleted action has no keys
        return []
    return _clean([v] if isinstance(v, str) else v) if v else []


def shown_keys(action) -> List[str]:
    """Every key ``action`` has now: its own (the menus show the first) and those the canvas
    runs it with (:func:`canvas_keys`)."""
    out = keys_of(action)
    try:
        now = action.property(CANVAS_KEYS_NOW_PROP) or ()
    except RuntimeError:  # swallow-ok: a deleted action has no keys
        return out
    return out + [k for k in _clean([now] if isinstance(now, str) else now) if k not in out]


def is_bare(key: str) -> bool:
    """A key without Ctrl, Alt or Meta (a letter, Shift + a letter, F-keys stay window
    keys): it would type into the lists, so it works on the canvas only."""
    seq = QtGui.QKeySequence(key)
    if seq.count() != 1:
        return False
    combo = seq[0]
    mods = combo.keyboardModifiers()
    if mods & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier | QtCore.Qt.MetaModifier):
        return False
    k = combo.key()
    return not (QtCore.Qt.Key_F1 <= k <= QtCore.Qt.Key_F35)


# ----------------------------------------------------------------------------- command ids
def label_of(action) -> str:
    """The command's own name (without '&', the tab text and a step name)."""
    try:
        own = action.property(LABEL_PROP)
        if own:
            return str(own)
        return action.text().replace("&&", "\x00").replace("&", "").replace("\x00", "&").split("\t")[0]
    except RuntimeError:  # swallow-ok: a deleted action has no name
        return ""


def name_actions(actions, prefix: str, ids) -> None:
    """Give each of ``actions`` the id ``prefix + id`` (the list-built commands)."""
    for a, i in zip(actions, ids):
        if not a.objectName():
            a.setObjectName("%s%s" % (prefix, i))


def _slug(text: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "_" for c in text)
    return "_".join(p for p in out.split("_") if p)


def assign_command_ids(win) -> None:
    """Name every command of ``win`` (see the module docstring); a name already set stays.
    Also keeps each command's own label (``ctg_label``)."""

    def name(a, n):
        if isinstance(a, QtGui.QAction) and not a.objectName():
            a.setObjectName(n)

    for tool, a in list(getattr(win, "tool_actions", {}).items()):
        name(a, "tool." + tool)
        if isinstance(a, QtGui.QAction) and not a.property(TOOL_PROP):
            a.setProperty(TOOL_PROP, True)
    for key, value in list(vars(win).items()):
        if key.startswith("act_"):
            name(value, key)
    for attr in (
        "dock_objects",
        "dock_library",
        "dock_tags",
        "dock_props",
        "dock_history",
        "dock_changes",
        "dock_sa_checks",
        "dock_page_checks",
        "dock_preview_states",
        "dock_navigator",
    ):
        dock = getattr(win, attr, None)
        if dock is not None:
            name(dock.toggleViewAction(), "panel." + attr[len("dock_") :])
    for size, a in list(getattr(win, "_grid_actions", {}).items()):
        name(a, "grid.size.%d" % size)
    # Window > Go to Page > Page 1 to 9 (Alt+Shift+1 ... the n-th tab)
    for k, a in enumerate(list(getattr(win, "goto_tab_actions", ()) or ())):
        name(a, "tab.goto.%d" % (k + 1))
    # any list of commands a later change adds without ids: an id from its label
    for attr in (
        "align_actions",
        "text_flag_actions",
        "justify_actions",
        "effect_actions",
        "external_actions",
        "v34_actions",
        "insert_actions",
        "legacy_actions",
    ):
        for a in list(getattr(win, attr, ()) or ()):
            name(a, "%s.%s" % (attr[: -len("_actions")], _slug(label_of(a))))
    for a in win.findChildren(QtGui.QAction):
        try:
            if a.objectName() and a.property(LABEL_PROP) is None and a.text():
                a.setProperty(LABEL_PROP, label_of(a))
        except RuntimeError:  # swallow-ok: deleted while we looked
            continue


def command_id(action, crumb: str = "") -> str:
    """The id of ``action``: its object name, else 'menu:<breadcrumb>/<label>' (a row of a
    menu that is rebuilt as it opens)."""
    try:
        name = action.objectName()
    except RuntimeError:  # swallow-ok: a deleted action has no id
        return ""
    if name:
        return name
    return "menu:%s/%s" % (crumb, label_of(action))


def find_action(win, ref: str):
    """The live command ``ref`` names: an id or a 'menu:' id. None when no such command is
    in the window now."""
    import shiboken6

    if not ref:
        return None
    a = win.findChild(QtGui.QAction, ref)
    if a is not None and shiboken6.isValid(a):
        return a
    from ..dialog.palette import harvest_actions

    for act, crumb, label in harvest_actions(win.menuBar()):
        try:
            if ref == command_id(act, crumb):
                return act
        except RuntimeError:  # swallow-ok: a dynamic menu deleted it
            continue
    return None


#: the command palette's recently run commands (ids, newest first)
PALETTE_MRU = "ui/palette_mru"


def palette_mru(win) -> List[str]:
    """The palette's recent commands as ids, newest first."""
    return [str(x) for x in (app_settings().value(PALETTE_MRU, [], type=list) or [])]


def push_palette_mru(win, cid: str, keep: int = 20) -> None:
    mru = palette_mru(win)
    app_settings().setValue(PALETTE_MRU, ([cid] + [m for m in mru if m != cid])[:keep])


# ----------------------------------------------------------------------------- commands
class Command:
    """One row of the keyboard map."""

    __slots__ = ("id", "action", "label", "crumb", "scope", "fixed", "what")

    def __init__(self, cid, action, label, crumb, scope, fixed=False, what=""):
        self.id = cid
        self.action = action
        self.label = label
        self.crumb = crumb
        self.scope = scope
        self.fixed = fixed  # a key of the canvas without a command (not changed here)
        self.what = what  # fixed rows: the keys as text

    def top(self) -> str:
        return self.crumb.split(" > ")[0] if self.crumb else ""


def scope_of(action) -> str:
    try:
        return CANVAS if action.shortcutContext() == QtCore.Qt.WidgetWithChildrenShortcut else WINDOW
    except RuntimeError:  # swallow-ok: a deleted action
        return WINDOW


def commands(win, fixed: bool = False) -> List[Command]:
    """Every command of ``win`` that has an id (the menus, then the tools and the canvas
    commands no menu lists); ``fixed`` adds the canvas keys of help_window.CANVAS_KEYS."""
    from ..dialog.palette import harvest_actions

    out: List[Command] = []
    seen = set()
    for a, crumb, _label in harvest_actions(win.menuBar()):
        try:
            cid = a.objectName()
        except RuntimeError:  # swallow-ok: a dynamic menu deleted it
            continue
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(Command(cid, a, label_of(a), crumb, scope_of(a)))
    extra = list(getattr(win, "tool_actions", {}).values()) + list(getattr(win, "canvas_commands", ()) or ())
    for a in extra:
        cid = a.objectName()
        if cid and cid not in seen:
            seen.add(cid)
            out.append(Command(cid, a, label_of(a), "Canvas", scope_of(a)))
    if fixed:
        from ..dialog.help_window import CANVAS_KEYS

        for k, (keys, what) in enumerate(CANVAS_KEYS):
            out.append(Command("canvas.%d" % k, None, what, "Canvas", CANVAS, True, keys))
    return out


def capture_defaults(win) -> None:
    """Remember each command's own keys and shortcut context (once, before a preset or the
    user changes them)."""
    for c in commands(win):
        a = c.action
        if a.property(DEFAULT_PROP) is None:
            a.setProperty(DEFAULT_PROP, keys_of(a))
        if a.property(DEFAULT_CTX_PROP) is None:
            a.setProperty(DEFAULT_CTX_PROP, _ctx_int(a.shortcutContext()))


def _ctx_int(ctx) -> int:
    return int(getattr(ctx, "value", ctx))


def default_context(action):
    """The shortcut context ``action`` had before the keymap placed it (None: not known)."""
    try:
        v = action.property(DEFAULT_CTX_PROP)
    except RuntimeError:  # swallow-ok: a deleted action
        return None
    if v is None:
        return None
    try:
        return QtCore.Qt.ShortcutContext(int(v))
    except TypeError, ValueError:  # swallow-ok: a garbled property is no context
        return None


def default_keys(action) -> List[str]:
    v = action.property(DEFAULT_PROP)
    return list(v) if v is not None else keys_of(action)


# ----------------------------------------------------------------------------- presets
def _read_presets() -> Dict[str, dict]:
    out = {}
    try:
        names = sorted(os.listdir(PRESET_DIR))
    except OSError:  # swallow-ok: no preset folder: only the built-in keys
        names = []
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(PRESET_DIR, fn), encoding="utf-8") as f:
                data = json.load(f)
        except OSError, ValueError:  # swallow-ok: a broken preset file is left out
            continue
        name = str(data.get("name") or fn[:-5])
        out[name] = data
    order = {n: k for k, n in enumerate(("ctgedit", "builder", "design"))}
    return dict(sorted(out.items(), key=lambda kv: (order.get(kv[0], 99), kv[0])))


#: the presets (read once: they ship with ctgedit; View > Refresh reads them again)
_PRESETS: Dict[str, dict] = {}
from ...infra import caches as _caches  # noqa: E402

_caches.register("keymap.presets", _PRESETS, on=("refresh",))


def presets() -> Dict[str, dict]:
    if not _PRESETS:
        _PRESETS.update(_read_presets())
        _PRESETS.setdefault(DEFAULT_PRESET, {"name": DEFAULT_PRESET, "title": "ctgedit", "keys": {}})
    return _PRESETS


def preset_title(name: str) -> str:
    return str(presets().get(name, {}).get("title") or name)


def preset_keys(name: str) -> Dict[str, List[str]]:
    """The keys a preset sets (with the ones of the preset it extends)."""
    out: Dict[str, List[str]] = {}
    chain, seen = [], set()
    while name and name not in seen and name in presets():
        seen.add(name)
        chain.append(presets()[name])
        name = presets()[name].get("extends") or ""
    for p in reversed(chain):
        for cid, keys in (p.get("keys") or {}).items():
            out[cid] = _clean(keys if isinstance(keys, list) else [keys])
    return out


def preset_settings(name: str) -> dict:
    """The settings a preset sets when it is chosen ('design': Shift + arrow moves)."""
    out = {}
    chain, seen = [], set()
    while name and name not in seen and name in presets():
        seen.add(name)
        chain.append(presets()[name])
        name = presets()[name].get("extends") or ""
    for p in reversed(chain):
        out.update(p.get("settings") or {})
    return out


def preset_switch_settings(old: str, new: str) -> dict:
    """The settings to store when the keymap goes from preset ``old`` to ``new``: the new
    one's own (:func:`preset_settings`), and the default of each setting only ``old`` set
    (leaving Design tools puts Shift + arrow back to resizing; between two presets that set
    nothing, your own choice stays)."""
    out = dict(preset_settings(new))
    from .settings import OPTION_DEFAULTS, SETTINGS_KEYS

    for k in preset_settings(old):
        if k in out:
            continue
        if k.startswith("opt/") and k[4:] in OPTION_DEFAULTS:
            out[k] = OPTION_DEFAULTS[k[4:]]
        elif SETTINGS_KEYS.get(k) is not None:
            out[k] = SETTINGS_KEYS[k]
    return out


def preset_name() -> str:
    """The chosen preset (``ui/prefs/keymap``)."""
    v = app_settings().value(KEYMAP_SETTING, "", type=str) or ""
    return v if v in presets() else DEFAULT_PRESET


def set_preset(name: str) -> None:
    """Choose keymap preset ``name``, with the settings it sets (and back to their defaults
    the ones only the preset before set: :func:`preset_switch_settings`)."""
    old = preset_name()
    st = app_settings()
    st.setValue(KEYMAP_SETTING, name if name in presets() else DEFAULT_PRESET)
    for k, v in preset_switch_settings(old, name).items():
        st.setValue(k, v)


# ----------------------------------------------------------------------------- user keys
def read_stored(cid: str, value) -> List[str]:
    """The keys of a stored list of command ``cid`` (``keys/<cid>``, an exported file)."""
    if value is None or value == "":
        raw = []
    elif isinstance(value, str):
        raw = [value]
    else:
        try:
            raw = [str(v) for v in value]
        except TypeError:  # swallow-ok: a garbled value reads as no key
            raw = []
    return _clean(raw)


def stored_form(cid: str, keys: Iterable[str]):
    """``keys`` of command ``cid`` as they are stored (:func:`read_stored` reads them back);
    '' = no key."""
    ks = _clean(keys)
    return ks if ks else ""


def user_keys() -> Dict[str, List[str]]:
    """The user's own keys: command id -> keys ([] = none)."""
    st = app_settings()
    out = {}
    for k in st.allKeys():
        if k.startswith(KEYS_PREFIX):
            cid = k[len(KEYS_PREFIX) :]
            out[cid] = read_stored(cid, st.value(k))
    return out


def store_user_keys(keys: Dict[str, List[str]]) -> None:
    """Replace the user's own keys with ``keys``."""
    st = app_settings()
    for k in st.allKeys():
        if k.startswith(KEYS_PREFIX) and k[len(KEYS_PREFIX) :] not in keys:
            st.remove(k)
    for cid, ks in keys.items():
        st.setValue(KEYS_PREFIX + cid, stored_form(cid, ks))


# ----------------------------------------------------------------------------- resolving
def layered(
    win, preset: Optional[str] = None, user: Optional[Dict[str, List[str]]] = None
) -> Dict[str, Tuple[List[str], str]]:
    """Command id -> (keys, source) before conflicts are settled; source 'default' (the
    built-in key or the preset's) or 'user'."""
    preset = preset_name() if preset is None else preset
    user = user_keys() if user is None else user
    pk = preset_keys(preset)
    out = {}
    for c in commands(win):
        cid = c.id
        if cid in user:
            out[cid] = (list(user[cid]), "user")
        elif cid in pk:
            out[cid] = (list(pk[cid]), "default")
        else:
            out[cid] = (default_keys(c.action), "default")
    return out


#: the scope of a key no command has: on the canvas, the whole window, or one panel's list
PANEL = "panel"
#: conflicts(): the key is a panel's own (not the canvas')
FIXED_PANEL = ":panel"


def _top_window(obj):
    """The top-level window ``obj`` works in: its nearest widget's window(); a floating
    panel counts as the window it docks into (its window keys work there). None: no widget."""
    while obj is not None and not isinstance(obj, QtWidgets.QWidget):
        obj = obj.parent()
    if obj is None:
        return None
    top = obj.window()
    while isinstance(top, QtWidgets.QDockWidget) and top.parentWidget() is not None:
        top = top.parentWidget().window()
    return top


#: the keys the canvas handles itself (CanvasView.keyPressEvent, listed for the user in
#: help_window.CANVAS_KEYS): Esc cancels and goes back to Select, Space held pans, Enter
#: finishes a shape, Backspace takes its last point back, Insert and '+' add a node
CANVAS_OWN_KEYS = ("Esc", "Space", "Return", "Enter", "Num+Enter", "Backspace", "Ins", "+", "Num++")


def fixed_keys(win) -> Dict[str, str]:
    """Key -> where it works, for keys the window handles itself."""
    out = {}
    view = getattr(win, "view", None)
    if view is not None:
        for k in CANVAS_OWN_KEYS:
            out[k] = CANVAS
    for sc in win.findChildren(QtGui.QShortcut):
        try:
            k = norm(sc.key())
            parent = sc.parent()
            ctx = sc.context()
            if (
                not k
                or _top_window(parent) is not win
                or sc.property(CANVAS_KEY_OF_PROP)
                or sc.property(CANVAS_KEY_BLOCK_PROP)
            ):
                # (a command's canvas-only key, or a keypad key no command has: free)
                continue
        except RuntimeError:  # swallow-ok: deleted while we looked
            continue
        if ctx in (QtCore.Qt.WindowShortcut, QtCore.Qt.ApplicationShortcut) or parent is win:
            scope = WINDOW
        elif view is not None and (
            parent is view or (isinstance(parent, QtWidgets.QWidget) and view.isAncestorOf(parent))
        ):
            scope = CANVAS
        else:
            scope = PANEL
        if out.get(k) != WINDOW:
            out[k] = scope if out.get(k) in (None, scope) else WINDOW
    return out


def _overlaps(command_scope: str, key_scope: str) -> bool:
    """A key that works in ``key_scope`` would fire with a command of ``command_scope``."""
    return not (command_scope == CANVAS and key_scope == PANEL)


def scope_for(action, keys) -> str:
    """Where ``action`` works with ``keys``: a tool's letter (no Ctrl or Alt) on the canvas;
    a command that keeps one of its built-in keys keeps the scope it had (Edit > Delete works
    from the Objects panel too, View > Previous View on the canvas only); any other bare key
    a preset or the user gives a command on the canvas, any other key in the window."""
    keys = list(keys)
    try:
        if action is not None and action.property("ctg_canvas_only"):
            return CANVAS
        bare = bool(keys) and all(is_bare(k) for k in keys)
        if bare and (action is None or action.property(TOOL_PROP)):
            return CANVAS
        if action is not None and set(_clean(keys)) & set(_clean(default_keys(action))):
            # (a built-in key among them: the command keeps working where it did, Delete
            # with Del from the Objects panel too, Previous View with Alt+Left on the canvas
            # only, also with a key of your own added)
            ctx = default_context(action)
            if ctx is not None:
                return CANVAS if ctx == QtCore.Qt.WidgetWithChildrenShortcut else WINDOW
        if not bare:
            return WINDOW
    except RuntimeError:  # swallow-ok: a deleted action
        return WINDOW
    return CANVAS


#: :func:`scope_note`'s words for where a key works
_WHERE_WORDS = {WINDOW: "in the whole window", CANVAS: "on the canvas only"}


def key_scopes(action, keys) -> Dict[str, str]:
    """Where each key of ``keys`` works for ``action``: a canvas-only key (:func:`canvas_keys`)
    on the canvas, the others where the command works with them (:func:`scope_for`)."""
    on_canvas = canvas_keys(action)
    scope = scope_for(action, [k for k in keys if k not in on_canvas])
    return {k: CANVAS if k in on_canvas else scope for k in keys}


def scope_note(action, keys) -> str:
    """'Del works in the whole window, - and Num+- on the canvas only.' when the keys of
    ``action`` do not all work in one place (Edit > Delete); '' when they do."""
    scopes = key_scopes(action, keys)
    parts = []
    for where in (WINDOW, CANVAS):
        ks = [native(k) for k, s in scopes.items() if s == where]
        if ks:
            text = ks[0] if len(ks) == 1 else "%s and %s" % (", ".join(ks[:-1]), ks[-1])
            parts.append((text, len(ks), _WHERE_WORDS[where]))
    if len(parts) < 2:
        return ""
    (a, n, wa), (b, _m, wb) = parts
    return "%s %s %s, %s %s." % (a, "works" if n == 1 else "work", wa, b, wb)


def where_name(action, keys, scope: str) -> str:
    """The Where column of a command (Settings > Keyboard): ``scope``, or Canvas when every
    key it has works on the canvas only (Delete with its minus keys alone)."""
    if canvas_keys(action) and keys and set(key_scopes(action, keys).values()) == {CANVAS}:
        scope = CANVAS
    return SCOPE_NAMES.get(scope, scope)


def keys_tip(action, keys, what: str = "") -> str:
    """The tool tip of a command's keys: the keys, and where each works when not all of them
    work in one place (:func:`scope_note`); ``what`` when it has none."""
    text = ", ".join(native(k) for k in keys) or what
    note = scope_note(action, keys) if keys and action is not None else ""
    return "%s\n%s" % (text, note) if note else text


def resolve(win, preset: Optional[str] = None, user: Optional[Dict[str, List[str]]] = None):
    """(command id -> (keys, source), notes): every key on one command. A user's key wins
    over a preset's, a preset's over a built-in one, and within a layer the first command
    keeps it; ``notes`` = (key, kept on, taken from) for each key taken from a command."""
    lay = layered(win, preset, user)
    fixed = fixed_keys(win)
    actions = {c.id: c.action for c in commands(win)}
    used: Dict[str, str] = {}
    order = [cid for cid, (_k, s) in lay.items() if s == "user"] + [
        cid for cid, (_k, s) in lay.items() if s != "user"
    ]
    out = {}
    notes = []
    for cid in order:
        keys, source = lay[cid]
        scope = scope_for(actions.get(cid), keys)
        on_canvas = set(canvas_keys(actions.get(cid)))
        keep = []
        for k in keys:
            if k in fixed and _overlaps(CANVAS if k in on_canvas else scope, fixed[k]):
                continue
            if k in used:
                notes.append((k, used[k], cid))
                continue
            used[k] = cid
            keep.append(k)
        out[cid] = (keep, source)
    return out, notes


def apply_keymap(win, preset: Optional[str] = None, user: Optional[Dict[str, List[str]]] = None) -> list:
    """Give every command of ``win`` its keys (the module docstring) and put it in its
    scope. Returns the notes of :func:`resolve`; the status bar names a palette key that
    another command took."""
    assign_command_ids(win)
    capture_defaults(win)
    eff, notes = resolve(win, preset, user)
    view = getattr(win, "view", None)
    by_id = {c.id: c.action for c in commands(win)}
    taken = {k for ks, _s in eff.values() for k in ks}
    for cid, (keys, _source) in eff.items():
        a = by_id.get(cid)
        if a is None:
            continue
        # (a canvas-only key goes on a shortcut of the canvas; the others are the action's)
        on_canvas = canvas_keys(a) if view is not None else []
        own = [k for k in keys if k not in on_canvas]
        seqs = [QtGui.QKeySequence(k) for k in own]
        if [norm(s) for s in a.shortcuts()] != own:
            a.setShortcuts(seqs)
        _place(win, view, a, own)
        if on_canvas:
            here = [k for k in keys if k in on_canvas]
            # (a keypad key it lost that no command has, while it keeps the key's twin:
            # without a shortcut of its own Qt runs the twin's, so Num - would still delete)
            blocked = [k for k in on_canvas if k not in taken and keypad_twin(k) in here]
            _place_canvas_keys(view, a, here, blocked)
    palette = getattr(win, "act_palette_here", None)
    pname = palette.objectName() if palette is not None else ""
    for key, kept, taken in notes:
        if taken == pname or kept == pname:
            other = by_id.get(kept if taken == pname else taken)
            if other is not None and taken == pname:
                _status(
                    win, "%s now runs %s instead of the command palette." % (native(key), label_of(other))
                )
    notifier().applied.emit(win)
    return notes


class KeymapNotifier(QtCore.QObject):
    """``applied(window)``: an editor window was given its keys again (Settings, a preset,
    an import, Change Shortcut): an open Keyboard Shortcuts window lists them again.
    ``simKeysChanged``: the keys may have changed (:func:`apply_everywhere`): the Simulate
    window gives its commands theirs (SimulateWindow.apply_keys)."""

    applied = QtCore.Signal(object)
    simKeysChanged = QtCore.Signal()


_notifier_obj: Optional[KeymapNotifier] = None


def notifier() -> KeymapNotifier:
    """The one :class:`KeymapNotifier` of the process (made on first use)."""
    global _notifier_obj
    if _notifier_obj is None:
        _notifier_obj = KeymapNotifier()
    return _notifier_obj


def _place(win, view, a, keys) -> None:
    """A command of the canvas scope (scope_for) sits on the canvas; any other on the
    window."""
    ctx = a.shortcutContext()
    if ctx not in (QtCore.Qt.WindowShortcut, QtCore.Qt.WidgetWithChildrenShortcut):
        return
    if view is not None and scope_for(a, keys) == CANVAS:
        if ctx != QtCore.Qt.WidgetWithChildrenShortcut:
            a.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
        if a in win.actions():
            win.removeAction(a)
        if a not in view.actions():
            view.addAction(a)
    else:
        if ctx != QtCore.Qt.WindowShortcut:
            a.setShortcutContext(QtCore.Qt.WindowShortcut)
        if view is not None and a in view.actions():
            view.removeAction(a)
        if keys and a not in win.actions():
            win.addAction(a)


def keypad_twin(key: str) -> str:
    """The key ``key`` is on the keypad ('Num+-' -> '-'); '' when it is no keypad key."""
    seq = QtGui.QKeySequence(key)
    if seq.count() != 1:
        return ""
    combo = seq[0]
    mods = combo.keyboardModifiers()
    if not mods & QtCore.Qt.KeypadModifier:
        return ""
    return norm(QtGui.QKeySequence(QtCore.QKeyCombination(mods & ~QtCore.Qt.KeypadModifier, combo.key())))


def _place_canvas_keys(view, a, keys, blocked=()) -> None:
    """Bind canvas-only keys as canvas shortcuts."""
    cid = a.objectName()

    def put(prop, wanted, run):
        have = [sc for sc in view.findChildren(QtGui.QShortcut) if sc.property(prop) == cid]
        for k, key in enumerate(wanted):
            if k < len(have):
                sc = have[k]
            else:
                sc = QtGui.QShortcut(view)
                sc.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
                sc.setProperty(prop, cid)
                if run is not None:
                    sc.activated.connect(run)
            if norm(sc.key()) != key:
                sc.setKey(QtGui.QKeySequence(key))
            sc.setEnabled(True)
        for sc in have[len(wanted) :]:
            sc.setKey(QtGui.QKeySequence())
            sc.setEnabled(False)

    put(CANVAS_KEY_OF_PROP, keys, a.trigger)
    put(CANVAS_KEY_BLOCK_PROP, list(blocked), None)
    a.setProperty(CANVAS_KEYS_NOW_PROP, list(keys))


def apply_everywhere() -> None:
    """The keyboard map to every editor window (after Settings or an import), and the
    Simulate window's keys to it (``simKeysChanged``)."""
    try:
        from ..main_window import MainWindow
    except ImportError:  # swallow-ok: no editor window in this process
        MainWindow = None
    for w in MainWindow.editor_windows() if MainWindow is not None else ():
        try:
            apply_keymap(w)
        except RuntimeError:  # swallow-ok: a window being deleted
            continue
    notifier().simKeysChanged.emit()


def _status(win, text: str) -> None:
    try:
        win.statusBar().showMessage(text, 8000)
    except AttributeError, RuntimeError:  # swallow-ok: no status bar to tell
        pass


# ----------------------------------------------------------------------------- the Simulate window
def is_sim(cid: str) -> bool:
    """``cid`` is a command of the Simulate window (:data:`SIM_COMMANDS`)."""
    return str(cid or "").startswith(SIM_PREFIX)


def sim_commands() -> List[Command]:
    """The Simulate window's commands as rows of the keyboard map (no action: the window
    may not be open; :func:`sim_resolve` gives their keys)."""
    return [Command(cid, None, label, SIM_CRUMB, SIMULATE) for cid, label, _k in SIM_COMMANDS]


def sim_label(cid: str) -> str:
    return next((label for c, label, _k in SIM_COMMANDS if c == cid), cid)


def sim_default_keys(cid: str) -> List[str]:
    return _clean(next((keys for c, _l, keys in SIM_COMMANDS if c == cid), ()))


def sim_resolve(user: Optional[Dict[str, List[str]]] = None):
    """(command id -> (keys, source), notes) of the Simulate window's commands, as
    :func:`resolve` settles the editor's: the user's own keys first, then the built-in ones;
    within them the first command keeps a key; :data:`SIM_FIXED_KEYS` are nobody's."""
    user = user_keys() if user is None else user
    lay = {
        cid: ((list(user[cid]), "user") if cid in user else (_clean(keys), "default"))
        for cid, _l, keys in SIM_COMMANDS
    }
    order = [cid for cid, (_k, s) in lay.items() if s == "user"] + [
        cid for cid, (_k, s) in lay.items() if s != "user"
    ]
    used: Dict[str, str] = {}
    out, notes = {}, []
    for cid in order:
        keys, source = lay[cid]
        keep = []
        for k in keys:
            if k in SIM_FIXED_KEYS:
                continue
            if k in used:
                notes.append((k, used[k], cid))
                continue
            used[k] = cid
            keep.append(k)
        out[cid] = (keep, source)
    return {cid: out[cid] for cid in lay}, notes


def keys_now(
    win, cid: str, preset: Optional[str] = None, user: Optional[Dict[str, List[str]]] = None
) -> List[str]:
    """The keys command ``cid`` has with ``preset`` and ``user`` (an editor command of
    ``win`` or one of the Simulate window)."""
    if is_sim(cid):
        return sim_resolve(user)[0].get(cid, ([], ""))[0]
    if win is None:
        return []
    return resolve(win, preset, user)[0].get(cid, ([], ""))[0]


def own_keys(
    win, cid: str, preset: Optional[str] = None, user: Optional[Dict[str, List[str]]] = None
) -> List[str]:
    """The keys ``cid`` has without keys of the user's own for it: the keymap's (the
    preset's, else its built-in ones); a Simulate window command's built-in keys."""
    if is_sim(cid):
        return sim_default_keys(cid)
    if win is None:
        return []
    others = {k: v for k, v in (user or {}).items() if k != cid}
    return layered(win, preset, others).get(cid, ([], ""))[0]


def _sim_conflicts(
    cid: str, keys: Iterable[str], user: Optional[Dict[str, List[str]]] = None
) -> List[Tuple[str, str]]:
    eff, _n = sim_resolve(user)
    owner = {k: other for other, (ks, _s) in eff.items() for k in ks}
    out = []
    for k in _clean(keys):
        if k in SIM_FIXED_KEYS:
            out.append((k, ""))
        elif owner.get(k) and owner[k] != cid:
            out.append((k, owner[k]))
    return out


def _sim_take_keys(cid: str, keys: List[str], user: Dict[str, List[str]]) -> Dict[str, List[str]]:
    user = dict(user)
    keys = _clean(keys)
    eff, _n = sim_resolve(user)
    for other, (ks, _s) in eff.items():
        if other == cid:
            continue
        left = [k for k in ks if k not in keys]
        if left != ks:
            user[other] = left
    user[cid] = keys
    return user


# ----------------------------------------------------------------------------- conflicts
def conflicts(
    win,
    cid: str,
    keys: Iterable[str],
    preset: Optional[str] = None,
    user: Optional[Dict[str, List[str]]] = None,
) -> List[Tuple[str, str]]:
    """The keys of ``keys`` another command has now: [(key, its command id)]; a key of the
    canvas no command can take gives the id ''. A command of the Simulate window is checked
    against the window's other commands only (its keys never reach the editor's commands,
    docked or not: SimulateWindow._key_scope)."""
    if is_sim(cid):
        return _sim_conflicts(cid, keys, user)
    eff, _n = resolve(win, preset, user)
    owner = {}
    for other, (ks, _s) in eff.items():
        for k in ks:
            owner[k] = other
    fixed = fixed_keys(win)
    keys = _clean(keys)
    action = next((c.action for c in commands(win) if c.id == cid), None)
    scope = scope_for(action, keys)
    on_canvas = set(canvas_keys(action))
    out = []
    for k in keys:
        if k in fixed and _overlaps(CANVAS if k in on_canvas else scope, fixed[k]):
            out.append((k, FIXED_PANEL if fixed[k] == PANEL else ""))
        elif owner.get(k) and owner[k] != cid:
            out.append((k, owner[k]))
    return out


def take_keys(
    win, cid: str, keys: List[str], user: Dict[str, List[str]], preset: Optional[str] = None
) -> Dict[str, List[str]]:
    """``user`` with ``keys`` on ``cid`` and taken from the commands that had them (their
    other keys stay; a Simulate window command takes them from the window's other commands)."""
    if is_sim(cid):
        return _sim_take_keys(cid, keys, user)
    user = dict(user)
    keys = _clean(keys)
    eff, _n = resolve(win, preset, user)
    for other, (ks, _s) in eff.items():
        if other == cid:
            continue
        left = [k for k in ks if k not in keys]
        if left != ks:
            user[other] = left
    user[cid] = keys
    return user


def reset_command(win, cid: str, user: Dict[str, List[str]]) -> Dict[str, List[str]]:
    user = dict(user)
    user.pop(cid, None)
    return user


# ----------------------------------------------------------------------------- files
def export_keys(path: str, preset: Optional[str] = None, user: Optional[Dict[str, List[str]]] = None) -> int:
    """Write the preset and the user's keys to the JSON file ``path``; the number of
    commands written."""
    preset = preset_name() if preset is None else preset
    user = user_keys() if user is None else user
    data = {
        "format": EXPORT_FORMAT,
        "program": "ctgedit",
        "keymap": preset,
        "keys": {k: stored_form(k, v) or [] for k, v in sorted(user.items())},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    return len(user)


class KeysFileError(ValueError):
    """A file that is no exported keyboard map."""


def read_keys_file(path: str) -> Tuple[str, Dict[str, List[str]]]:
    """(preset, user keys) of a file :func:`export_keys` wrote."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise KeysFileError("%s could not be read: %s" % (os.path.basename(path), e)) from e
    if (
        not isinstance(data, dict)
        or data.get("program") != "ctgedit"
        or not isinstance(data.get("keys"), dict)
    ):
        raise KeysFileError("%s is not a keyboard file exported from ctgedit." % os.path.basename(path))
    try:
        fmt = int(data["format"])
    except (KeyError, TypeError, ValueError) as e:  # (a hand-edited or foreign file)
        raise KeysFileError(
            "%s is not a keyboard file exported from ctgedit." % os.path.basename(path)
        ) from e
    if fmt > EXPORT_FORMAT:
        raise KeysFileError("%s was exported by a newer ctgedit." % os.path.basename(path))
    preset = str(data.get("keymap") or DEFAULT_PRESET)
    keys = {str(k): read_stored(str(k), v) for k, v in data["keys"].items()}
    return (preset if preset in presets() else DEFAULT_PRESET), keys


# ----------------------------------------------------------------------------- F4 repeat
def repeatable(cid: str) -> bool:
    return cid in REPEAT_IDS or cid.startswith(REPEAT_PREFIXES)


def install_repeat(win) -> None:
    """Edit > Repeat (F4): the last command of :data:`REPEAT_IDS` / :data:`REPEAT_PREFIXES`
    the user ran, run again (the same QAction, so the same edit and undo step)."""
    act = getattr(win, "act_repeat", None)
    if act is None:
        return
    win._repeat_last = None
    for c in commands(win):
        if repeatable(c.id):
            c.action.triggered.connect(lambda _=False, a=c.action: _remember(win, a))
            c.action.changed.connect(lambda w=win: update_repeat(w))
    update_repeat(win)


def _remember(win, action) -> None:
    if getattr(win, "_repeat_last", None) is not action:
        win._repeat_last = action
    update_repeat(win)


def repeat_target(win):
    a = getattr(win, "_repeat_last", None)
    try:
        if a is not None and a.objectName():
            return a
    except RuntimeError:  # swallow-ok: the command was deleted
        win._repeat_last = None
    return None


def repeat_ok(win) -> bool:
    a = repeat_target(win)
    return bool(a is not None and a.isEnabled() and a.isVisible())


def update_repeat(win) -> None:
    act = getattr(win, "act_repeat", None)
    if act is None:
        return
    a = repeat_target(win)
    text = "Repeat %s" % label_of(a) if a is not None else "Repeat"
    if act.text() != text:
        act.setText(text)
    on = repeat_ok(win)
    if act.isEnabled() != on:
        act.setEnabled(on)


def run_repeat(win) -> None:
    a = repeat_target(win)
    if a is None:
        _status(win, "There is no command to repeat yet.")
        return
    if not a.isEnabled():
        _status(win, "%s cannot run now." % label_of(a))
        return
    a.trigger()
