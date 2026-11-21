"""The command palette (Ctrl+K): one search for everything."""

from __future__ import annotations
from ..style.theme import sp as _sp

import re
from typing import Dict, List, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from ...infra import caches as _caches
from ...infra import logsetup
from .. import search_rows as SR


def harvest_actions(menubar) -> list:
    """``[(action, breadcrumb, label)]`` for every leaf menu-bar action.
    Labels captured at harvest time: dynamic menus (MRU, window list) delete
    their actions at any moment, so consumers must RuntimeError-guard every
    call on a harvested action."""
    out = []
    # map each submenu's menuAction -> its QMenu WITHOUT QAction.menu():
    # under PySide6 that call re-wraps the menu and invalidates the Python
    # wrappers the window cached (the MRU / Labels / Window menus would then raise
    # RuntimeError on their next aboutToShow)
    import shiboken6

    menus = {}
    for m in menubar.findChildren(QtWidgets.QMenu):
        try:
            menus[shiboken6.getCppPointer(m.menuAction())[0]] = m
        except RuntimeError:
            continue

    def sub(a):
        return menus.get(shiboken6.getCppPointer(a)[0])

    def walk(menu, crumb):
        for a in menu.actions():
            try:
                if a.isSeparator():
                    continue
                m = sub(a)
                if m is not None:
                    walk(m, crumb + [a.text().replace("&", "")])
                elif a.text() and a.isVisible():
                    out.append((a, " > ".join(crumb), a.text().replace("&", "")))
            except RuntimeError:
                continue  # dynamic menus delete actions under us

    for top in menubar.actions():
        try:
            m = sub(top)
            if m is not None:
                walk(m, [top.text().replace("&", "")])
        except RuntimeError:
            continue
    return out


def menu_chain(menubar, action) -> list:
    """``[(menu, action in it), ...]`` from the menu bar down to ``action``: the top menu's
    action in the bar first (menu None), then each submenu's action in its menu, then
    ``action`` in its own menu (empty when no menu holds it)."""
    import shiboken6

    ptr = shiboken6.getCppPointer
    menus = {}
    for m in menubar.findChildren(QtWidgets.QMenu):
        try:
            menus[ptr(m.menuAction())[0]] = m
        except RuntimeError:
            continue
    want = ptr(action)[0]

    def walk(menu, path):
        for a in menu.actions():
            try:
                if a.isSeparator():
                    continue
                if ptr(a)[0] == want:
                    return path + [(menu, a)]
                m = menus.get(ptr(a)[0])
                if m is not None:
                    got = walk(m, path + [(menu, a)])
                    if got:
                        return got
            except RuntimeError:
                continue
        return []

    for top in menubar.actions():
        try:
            m = menus.get(ptr(top)[0])
            if m is not None:
                got = walk(m, [(None, top)])
                if got:
                    return got
        except RuntimeError:
            continue
    return []


#: the Graphics Builder's words for ctgedit's commands (a palette search for one finds these)
ALIASES = {
    "options": ("Settings",),
    "preferences": ("Settings",),
    "setup": ("Settings",),
    "properties": ("Object Properties", "Page Properties"),
    "attributes": ("Object Properties",),
    "goto": ("Goto Object",),
    "go to": ("Goto Object",),
    "definition": ("Edit Definition",),
    "edit genie": ("Edit Definition", "Genie Parameters"),
    "genie": ("Genie Parameters", "Edit Definition", "Paste Genie"),
    "parameters": ("Genie Parameters",),
    "substitution": ("Genie Parameters",),
    "simulate": ("Simulate Page",),
    "runtime": ("Run",),
    "compile": ("Compile",),
    "turn": ("Rotate",),
    "mirror": ("Flip",),
    "zoom": ("Magnifier", "Fit"),
    "show zoom": ("Magnifier",),
    "zoom window": ("Magnifier",),
    "library": ("Copy to Library", "Paste Symbol", "Paste Genie"),
    "symbol": ("Paste Symbol",),
    "super genie": ("Paste Super Genie", "Attach Super Genies"),
}

#: more words the palette finds a command by (its id -> words), besides an action's own
#: ``ctg_keywords`` (ui/prefs/keymap.KEYWORDS_PROP)
KEYWORDS = {
    "act_paste_genie": ("insert genie", "add genie", "tank", "pump", "valve", "motor", "level bar"),
    "act_paste_symbol": ("insert symbol", "add symbol", "picture", "image"),
    "act_obj_props": ("animation", "fill level", "level", "dynamics", "colour"),
    "act_page_props": ("page settings", "title", "template"),
    "act_simulate": ("test", "preview", "simulator"),
    "act_run": ("start runtime",),
    "act_compile": ("build",),
    "act_goto": ("an", "animation number", "jump"),
    "act_snap": ("magnet",),
    "tool.text": ("label", "caption", "write"),
    "tool.number": ("value", "display value"),
    "tool.ellipse": ("circle", "oval"),
    "tool.rectangle": ("box", "square"),
    "tool.button": ("push button", "command button"),
    "tool.symbol": ("insert symbol", "place symbol"),
}

#: the drawing tools (the tool box's tiles by default; Suggested)
DRAWING_TOOLS = (
    "tool.line",
    "tool.freehand",
    "tool.rectangle",
    "tool.rectangle_outline",
    "tool.ellipse",
    "tool.ellipse_outline",
    "tool.polygon",
    "tool.pipe",
    "tool.text",
    "tool.number",
    "tool.button",
    "tool.symbol",
    "tool.symbol_set",
)
#: the commands 'Suggested' lists after the drawing tools
SUGGESTED = ("act_obj_props", "act_page_props", "act_compile", "act_run", "act_simulate")
#: the tool box's pins (command ids; unset = the drawing tools)
PINS_SETTING = "ui/palette_pins"
#: the recent commands the empty palette shows
MRU_SHOWN = 8
#: the tiles of a tool box row
TILES_PER_ROW = 6
#: what a command needs when it cannot run (its ViewState.ENABLE_WHEN rule)
ENABLE_REASONS = {
    "doc": "Open a page first.",
    "sel": "Select objects first.",
    "multi": "Select two or more objects first.",
    "three": "Select three or more objects first.",
    "page": "Open a page first. A library element cannot do this.",
    "project": "Open a page of a project first.",
    "tab": "Open a page first.",
}
#: the commands with a rule of their own (ViewState.CUSTOM_ENABLE)
CUSTOM_REASONS = {
    "act_undo": "There is nothing to undo.",
    "act_redo": "There is nothing to redo.",
    "act_genie_params": "Select a genie first.",
    "act_attach_super": "Select one genie first.",
    "act_genie_form": "Select one genie first.",
    "act_paste_appearance": "Copy an appearance first (Edit > Copy Appearance).",
    "act_ungroup": "Select a group or a genie first.",
    "act_cut_link": "Select one object, or open a page with a template.",
    "act_cut_template_link": "The page has no template.",
    "act_edit_template": "The page has no template.",
    "act_update_page_template": "The page has no template.",
    "act_prev_view": "There is no view to go back to.",
    "act_next_view": "There is no view to go forward to.",
    "act_revert": "The page has no saved copy to go back to.",
    "act_reopen_closed": "No page was closed.",
    "act_next_page": "Open two or more pages first.",
    "act_prev_page": "Open two or more pages first.",
    "act_new_from_last": "Create a page with File > New first.",
    "act_repeat": "Run an align, order, rotate, flip, group, lock or text command first.",
    "act_hotspot": "Open a symbol or genie for editing first.",
    "act_used_by": "Select one placed symbol or genie, or open one for editing.",
    "act_font_dialog": "Select a text, number or button first.",
    "act_edit_text": "Select a text, number or button first.",
    "act_bitmap_editor": "Select a picture first.",
    "act_replace_picture": "Select a picture first.",
    "act_export_picture": "Select a picture first.",
    "act_resample_picture": "Select a stretched picture first.",
    "act_paste_composite": "A composite genie goes only into a page, a template or a super genie.",
}
#: what Align and Align... need (needs_text): one object with
#: Settings > Editing 'Align to: The page', else two
ALIGN_REASONS = {"page": "Select an object first.", "": "Select two or more objects first."}
#: the prefixes listed under the empty field: (what to type, what it finds)
PREFIX_HINTS = (
    (">", "commands"),
    ("@", "objects"),
    (":", "AN"),
    ("?", "help"),
    ("p:", "pages"),
    ("s:", "symbols"),
    ("g:", "genies"),
    ("t:", "tags"),
    ("f:", "functions"),
    ("set:", "settings"),
)
#: the commands the palette lists at most for a query: the default of Settings > Ribbon >
#: Search > The command palette lists at most (ui/prefs/timing_options.PALETTE_ROWS)
MAX_COMMANDS = 40
#: the settings, help sections and Quick Open rows of each kind a query without a prefix adds
MIXED_SETTINGS = 3
MIXED_HELP = 3
MIXED_FIND = 3


# --------------------------------------------------------------------------- matching
def command_rank(query: str, label: str, crumb: str) -> int:
    """Lower = better, -1 = no match: the label starting with the words (0), a word of it
    starting with them (10 +), each word starting a word of it (20 +), the label holding
    them (30 +, from four letters on), its letters in order from word starts (100 +), a word
    of the menu path starting with them (300 +)."""
    q = " ".join(query.lower().split())
    if not q:
        return 0
    m = SR.match(q, label, substring_min=4)
    if m is not None:
        return m[0]
    c = SR.match(q, crumb, substring_min=99) if crumb else None
    if c is not None and c[0] < 100:
        return 300 + c[0]
    return -1


def _alias_rank(query: str, label: str) -> int:
    q = " ".join(query.lower().split())
    best = -1
    for word, targets in ALIASES.items():
        if not word.startswith(q) and not q.startswith(word):
            continue
        for k, target in enumerate(targets):
            if label.lower().startswith(target.lower()):
                r = 5 + k
                best = r if best < 0 else min(best, r)
    return best


def _alias_word(query: str, label: str) -> str:
    """The Graphics Builder word of ALIASES the query matched for ``label`` ('' = none)."""
    q = " ".join(query.lower().split())
    for word, targets in ALIASES.items():
        if (word.startswith(q) or q.startswith(word)) and any(
            label.lower().startswith(t.lower()) for t in targets
        ):
            return word
    return ""


def _fuzzy(query: str, text: str) -> int:
    """The letters of ``query`` in order in ``text`` by the word rule (ui/search_rows): -1 =
    no match; lower = better."""
    q = (query or "").lower().replace(" ", "")
    if not q:
        return 0
    m = SR.match(q, text, substring_min=1)
    return -1 if m is None else m[0]


def keywords_of(action, cid: str) -> tuple:
    try:
        own = action.property("ctg_keywords") or ()
    except RuntimeError:  # swallow-ok: a deleted action has no words
        own = ()
    return tuple(str(w) for w in own) + tuple(KEYWORDS.get(cid, ()))


def _keyword_hit(action, cid: str, query: str) -> str:
    """The search word of the command ``query`` starts ('' = none)."""
    q = " ".join(query.lower().split())
    if not q:
        return ""
    for w in keywords_of(action, cid):
        if str(w).lower().startswith(q):
            return str(w)
    return ""


# --------------------------------------------------------------------------- commands
class Command:
    """A menu command as the palette shows it: ``name`` (its label, or 'Menu: label' for a
    row of a menu built as it opens and a number such as '1 px'), ``bare`` (its own label),
    ``crumb`` (its menu path) and ``cid`` (its id)."""

    __slots__ = ("action", "crumb", "label", "name", "bare", "extra", "cid", "order")

    def __init__(self, action, crumb: str, label: str, order: int = 0, ids: bool = True):
        from ..prefs import keymap

        self.action = action
        self.crumb = crumb
        self.label = label
        self.order = order
        self.cid = keymap.command_id(action, crumb)
        self.name, self.bare, self.extra = display_name(action, crumb, label, ids)


def display_name(action, crumb: str, label: str, ids: bool = True) -> tuple:
    """(name, label, detail) of a menu row."""
    try:
        raw = action.text()
        own_id = action.objectName()
    except RuntimeError:  # swallow-ok: a dynamic menu deleted it
        raw, own_id = label, ""
    text, _tab, extra = label.partition("\t")
    if re.match(r"^&\d\s", raw or ""):
        text = re.sub(r"^\d\s+", "", text)
    text = " ".join(text.split())
    last = crumb.split(" > ")[-1] if crumb else ""
    if last and ((ids and not own_id) or text[:1].isdigit()):
        return "%s: %s" % (last, text), text, extra.strip()
    return text, text, extra.strip()


def needs_text(window, action, cid: str) -> str:
    """What a command that cannot run now needs (its ENABLE_WHEN rule), '' when it can."""
    try:
        if action.isEnabled():
            return ""
    except RuntimeError:  # swallow-ok: a deleted action
        return ""
    if cid == "act_align_dialog" or cid.startswith("align."):
        # (one object is enough with Align to: The page)
        page = getattr(window, "_align_to", "") == "page"
        return ALIGN_REASONS["page" if page else ""]
    if cid in CUSTOM_REASONS:
        return CUSTOM_REASONS[cid]
    for rule, names in (getattr(window, "ENABLE_WHEN", None) or {}).items():
        if cid in names:
            return ENABLE_REASONS.get(rule, "")
    if cid.startswith(("tool.", "insert.", "v34.")):
        return ENABLE_REASONS["doc"]
    if cid.startswith(("textflag.", "justify.", "effect.")):
        return "Select a text, number or button first."
    if getattr(window, "doc", "") is None:
        return ENABLE_REASONS["doc"]
    return "Not available now."


def _keys(action) -> list:
    try:
        return [s.toString(QtGui.QKeySequence.NativeText) for s in action.shortcuts() if not s.isEmpty()]
    except RuntimeError:  # swallow-ok: a deleted action
        return []


def command_row(window, c: Command, hits=(), alias: str = "") -> SR.Row:
    a = c.action
    try:
        enabled = a.isEnabled()
        check = a.isChecked() if a.isCheckable() else None
        group = a.actionGroup()
        if check is not None and group is not None and group.isExclusive():
            check = True if check else None  # (one of a set: only the chosen one)
        ic = a.icon()
        tip = a.statusTip() or ""
    except RuntimeError:  # swallow-ok: deleted by a dynamic menu
        enabled, check, ic, tip = False, None, None, ""
    reason = "" if enabled else needs_text(window, a, c.cid)
    detail = c.crumb + ("  %s" % c.extra if c.extra else "")
    return SR.Row(
        "command",
        c.name,
        detail,
        (a, c.label),
        hits=hits,
        alias=alias,
        keys=_keys(a)[:1],
        check=check,
        enabled=enabled,
        reason=reason,
        cid=c.cid,
        icon=ic,
        tip=reason or tip,
    )


# --------------------------------------------------------------------------- settings
class SettingEntry:
    """One row of the Settings window: its page, section and label, the setting it shows
    (``key``) and how its value reads (``kind``: 'bool', 'choice', 'number' or '';
    ``choices``: (stored value, text) pairs; ``suffix`` of a number)."""

    __slots__ = ("page", "section", "label", "key", "kind", "choices", "suffix", "words")

    def __init__(self, page, section, label, key, kind="", choices=(), suffix="", words=""):
        self.page, self.section, self.label, self.key = page, section, label, key
        self.kind, self.choices, self.suffix, self.words = kind, tuple(choices), suffix, words

    @property
    def path(self) -> str:
        return " > ".join(x for x in ("Settings", self.page, self.section) if x)


_SETTINGS: Dict[str, list] = {}
_caches.register("palette.settings", _SETTINGS, on=("tests",))


def _clean(text: str) -> str:
    t = (text or "").replace("&&", "\x00").replace("&", "").replace("\x00", "&")
    return " ".join(t.split()).rstrip(":").strip()


def settings_index(window) -> List[SettingEntry]:
    """Every row of the Settings window (built once, hidden, and read: its pages are made in
    code, not declared), as :class:`SettingEntry`."""
    hit = _SETTINGS.get("rows")
    if hit is not None:
        return hit
    out: List[SettingEntry] = []
    dlg = None
    try:
        from ..prefs.settings_dialog import SettingsDialog

        dlg = SettingsDialog(window)
        for p in dlg._tabs.pages:
            for s in p.sections:
                for r in s.rows:
                    out.append(_entry_of(p.title, s.title, r))
    except Exception:  # noqa: BLE001 -- no settings rows then; the commands still show
        logsetup.note_exception("palette.settings_index")
    finally:
        if dlg is not None:
            try:
                import shiboken6

                dlg.hide()
                dlg.setParent(None)
                shiboken6.delete(dlg)
            except RuntimeError:  # swallow-ok: already gone
                pass
    out = [e for e in out if e.label and e.label.lower() != "none"]
    _SETTINGS["rows"] = out
    return out


def _entry_of(page: str, section: str, r) -> SettingEntry:
    field = r.field
    label = r.label_text or (field.property("ctgText") if isinstance(field, QtWidgets.QWidget) else "") or ""
    if not label and isinstance(field, QtWidgets.QAbstractButton):
        label = field.text()
    # (a row of its own kind without a name, the Keyboard page's table, is not an option)
    kind, choices, suffix = "", [], ""
    w = field
    if isinstance(w, QtWidgets.QWidget) and not isinstance(
        w, (QtWidgets.QAbstractButton, QtWidgets.QComboBox, QtWidgets.QAbstractSpinBox)
    ):
        inner = [
            c
            for c in w.findChildren(QtWidgets.QWidget)
            if isinstance(c, (QtWidgets.QComboBox, QtWidgets.QAbstractSpinBox))
        ]
        w = inner[0] if len(inner) == 1 else w
    if isinstance(w, QtWidgets.QAbstractButton) and w.isCheckable():
        kind = "bool"
    elif isinstance(w, QtWidgets.QComboBox):
        kind = "choice"
        choices = [(w.itemData(k), w.itemText(k)) for k in range(w.count())]
    elif isinstance(w, QtWidgets.QAbstractSpinBox):
        kind = "number"
        suffix = w.suffix() if hasattr(w, "suffix") else ""
    elif getattr(w, "CHOICES", None):
        kind = "choice"  # (the theme tiles answer as a combo box)
        choices = [tuple(c[:2]) for c in w.CHOICES]
    return SettingEntry(page, section, _clean(label), r.key or "", kind, choices, suffix, r.search_text or "")


def setting_state(e: SettingEntry) -> str:
    """The setting's value now, as its row shows it ('' = not known)."""
    if not e.key:
        return ""
    from ..prefs.settings import app_settings
    from ..prefs.settings_dialog import declared_default

    default = declared_default(e.key)
    st = app_settings()
    try:
        if e.kind == "bool":
            if not isinstance(default, bool):
                return ""
            return "On" if st.value(e.key, default, type=bool) else "Off"
        v = st.value(e.key, default)
        if v is None:
            return ""
        if e.kind == "choice":
            for data, text in e.choices:
                if data is not None and str(data).lower() == str(v).lower():
                    return text
            return ""
        if e.kind == "number":
            return "%s%s" % (v, e.suffix)
    except TypeError, ValueError:  # swallow-ok: a garbled setting shows no state
        return ""
    return ""


def _row_label(r) -> str:
    f = r.field
    own = f.property("ctgText") if isinstance(f, QtWidgets.QWidget) else ""
    return _clean(r.label_text or str(own or ""))


def show_setting(window, page: str, search: str = "", key: str = "", exec_: bool = True):
    """Open Settings on ``page`` with ``search`` in its search box and the row of ``key``
    (or the first row found) focused. ``exec_`` False: shown, not run (the tests); returns
    the dialog then."""
    from ..prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(window, page=page)
    tabs = dlg._tabs
    if search:
        tabs.search.setText(search)
        tabs.search.flush()
    rows = tabs.rows()
    want = (search or "").lower()
    target = None
    if key:
        cands = [r for r in rows if r.key == key]
        same = [r for r in cands if _row_label(r).lower() == want]
        target = (same or cands or [None])[0]
    if target is None and want:
        same = [r for r in rows if _row_label(r).lower() == want]
        shown = [r for r in rows if not r.isHidden()]
        target = (same or shown or [None])[0]

    def focus():
        if target is None:
            return
        try:
            f = target.field
            w = f if isinstance(f, QtWidgets.QWidget) else target
            if isinstance(w, QtWidgets.QWidget) and w.focusPolicy() == QtCore.Qt.NoFocus:
                kids = [k for k in w.findChildren(QtWidgets.QWidget) if k.focusPolicy() != QtCore.Qt.NoFocus]
                w = kids[0] if kids else w
            tabs.scroll.ensureWidgetVisible(target, 0, 40)
            w.setFocus(QtCore.Qt.OtherFocusReason)
        except RuntimeError:  # swallow-ok: the window closed at once
            pass

    dlg.focus_target = target
    QtCore.QTimer.singleShot(0, focus)
    if not exec_:
        dlg.show()
        focus()
        return dlg
    try:
        dlg.exec()
    finally:
        dlg.deleteLater()
    return None


# --------------------------------------------------------------------------- pins
def pins() -> List[str]:
    """The tool box's commands (ids): the user's pins, else the drawing tools."""
    from ..prefs.settings import app_settings

    raw = app_settings().value(PINS_SETTING, None)
    if raw is None:
        return list(DRAWING_TOOLS)
    if isinstance(raw, str):
        raw = [raw] if raw else []
    return [str(x) for x in raw if str(x)]


def set_pins(ids: Sequence[str]) -> None:
    from ..prefs.settings import app_settings

    app_settings().setValue(PINS_SETTING, list(dict.fromkeys(str(i) for i in ids if i)))


def pin(cid: str) -> None:
    if cid and cid not in pins():
        set_pins(pins() + [cid])


def unpin(cid: str) -> None:
    set_pins([p for p in pins() if p != cid])


# --------------------------------------------------------------------------- the tool box
class ToolTile(QtWidgets.QToolButton):
    """A tile of the tool box: the command's icon (32 px), its key under it and its number
    (1 to 9) in the corner. It hosts the command's QAction."""

    def __init__(self, action, number: int, parent=None):
        super().__init__(parent)
        self.setDefaultAction(action)
        self.number = number
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setAutoRaise(True)
        self.setMouseTracking(True)
        from ..style.theme import px

        self.setFixedSize(px(56), px(54))
        keys = _keys(action)
        name = _command_label(action)
        self.setToolTip("%s (%s)" % (name, keys[0]) if keys else name)
        self.setAccessibleName(name)

    def paintEvent(self, _e) -> None:  # noqa: N802 -- Qt naming
        from ..style.theme import COLORS, font, px

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        a = self.defaultAction()
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        try:
            enabled = a.isEnabled()
            checked = a.isChecked()
            ic = a.icon()
            keys = _keys(a)
        except RuntimeError:  # swallow-ok: the action went
            return
        if self.underMouse() or checked:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(COLORS["selection" if checked else "hover"]))
            p.drawRoundedRect(r, px(4), px(4))
        s = px(32)
        mode = QtGui.QIcon.Normal if enabled else QtGui.QIcon.Disabled
        ic.paint(p, QtCore.QRect((self.width() - s) // 2, px(4), s, s), QtCore.Qt.AlignCenter, mode)
        f = font("caption")
        p.setFont(f)
        p.setPen(QtGui.QColor(COLORS["text_dim"] if not checked else COLORS["sel_text_dim"]))
        if keys:
            p.drawText(QtCore.QRect(0, px(36), self.width(), px(16)), QtCore.Qt.AlignCenter, keys[0])
        if 1 <= self.number <= 9:
            p.drawText(
                QtCore.QRect(px(4), px(2), px(12), px(14)),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop,
                str(self.number),
            )
        p.end()


def _command_label(action) -> str:
    from ..prefs import keymap

    return keymap.label_of(action)


class ToolBox(QtWidgets.QWidget):
    """The band of tool tiles the palette shows when it opens from the page."""

    picked = QtCore.Signal(object)
    unpinRequested = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PaletteToolBox")
        self._grid = QtWidgets.QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(_sp(0))
        self.tiles: List[ToolTile] = []

    def set_actions(self, actions) -> None:
        for t in self.tiles:
            self._grid.removeWidget(t)
            t.hide()
            t.deleteLater()
        self.tiles = []
        for k, a in enumerate(actions):
            t = ToolTile(a, k + 1, self)
            t.triggered.connect(self._triggered)
            t.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            t.customContextMenuRequested.connect(lambda _p, tile=t: self._tile_menu(tile))
            self._grid.addWidget(t, k // TILES_PER_ROW, k % TILES_PER_ROW)
            self.tiles.append(t)
        self._grid.setColumnStretch(TILES_PER_ROW, 1)

    def _triggered(self, action) -> None:
        self.picked.emit(action)

    def action_at(self, number: int):
        """The command of tile ``number`` (1 to 9), None when there is no such tile."""
        if 1 <= number <= len(self.tiles):
            return self.tiles[number - 1].defaultAction()
        return None

    def pick(self, number: int) -> bool:
        """Keys 1 to 9: run the tile of that number. False (nothing picked) when there is
        no such tile or its command is greyed."""
        a = self.action_at(number)
        if a is None:
            return False
        try:
            if not a.isEnabled():
                return False
        except RuntimeError:  # swallow-ok: the action went
            return False
        self.picked.emit(a)
        try:
            a.trigger()
        except RuntimeError:  # swallow-ok: the action went
            return False
        return True

    def _tile_menu(self, tile) -> None:
        m = self.tile_menu(tile)
        m.exec(QtGui.QCursor.pos())

    def tile_menu(self, tile) -> QtWidgets.QMenu:
        from ..prefs import keymap

        m = QtWidgets.QMenu(self)
        cid = keymap.command_id(tile.defaultAction())
        a = m.addAction("Unpin from the Tool Box")
        a.setObjectName("palette.unpin")
        a.triggered.connect(lambda _=False, c=cid: self.unpinRequested.emit(c))
        return m


# --------------------------------------------------------------------------- the popup
def parse(text: str):
    """(mode, query, kinds) of what was typed: mode 'all' (no prefix), 'commands' ('>'),
    'objects' ('@'), 'goto' (':'), 'help' ('?'), 'settings' ('set:') or 'find' (a Quick
    Open prefix: ``kinds``)."""
    from . import quick_open as QO

    t = (text or "").lstrip()
    low = t.lower()
    if t.startswith(">"):
        return "commands", t[1:].strip(), ()
    if t.startswith("@"):
        return "objects", t[1:].strip(), ()
    if t.startswith(":"):
        return "goto", t[1:].strip(), ()
    if t.startswith("?"):
        return "help", t[1:].strip(), ()
    if low.startswith("set:"):
        return "settings", t[4:].strip(), ()
    kinds, q = QO.split_query(t)
    if kinds != tuple(QO.KIND_ORDER):
        return "find", q, kinds
    return "all", t.strip(), ()


#: the footer under the list, by the kind of the current row
FOOTERS = {
    "command": "Enter runs · Shift+Enter runs and stays open · Alt+Enter assigns a key",
    "setting": "Enter opens it in Settings",
    "help": "Enter opens the help",
}


def fit_footer(text: str, fm: QtGui.QFontMetrics, width: int) -> str:
    """``text`` in ``width`` px: its last ' · ' parts left out first ('Enter runs · Shift+Enter
    runs and stays open'), then the one part cut with '…'."""
    if width <= 0 or fm.horizontalAdvance(text) <= width:
        return text
    parts = text.split(" · ")
    while len(parts) > 1:
        parts.pop()
        t = " · ".join(parts)
        if fm.horizontalAdvance(t) <= width:
            return t
    return fm.elidedText(parts[0], QtCore.Qt.ElideRight, width)


class CommandPalette(QtWidgets.QFrame):
    """The palette popup (see the module docstring)."""

    def __init__(self, window):
        super().__init__(window, QtCore.Qt.Popup)
        self.setObjectName("CommandPalette")
        self._window = window
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))
        self._edit = QtWidgets.QLineEdit()
        self._edit.setPlaceholderText("Search commands, pages, objects, settings and help")
        self._edit.setAccessibleName("Command palette")
        try:
            from ..style.theme import icon

            self._edit.addAction(icon("search"), QtWidgets.QLineEdit.LeadingPosition)
        except Exception:  # noqa: BLE001  # swallow-ok: the search icon is decoration
            pass
        # the '.*' toggle every search box has (theme.RegexToggle)
        from ..style.theme import RegexToggle, note_label

        self._regex = RegexToggle(self._edit, "Search commands")
        self._regex.toggled.connect(lambda _on: self._refill())
        lay.addWidget(self._edit)
        self._hints = QtWidgets.QLabel()
        self._hints.setObjectName("PaletteHints")
        self._hints.setTextFormat(QtCore.Qt.RichText)
        self._hints.setWordWrap(True)
        self._hints.linkActivated.connect(self._hint_clicked)
        lay.addWidget(self._hints)
        self.toolbox = ToolBox(self)
        self.toolbox.picked.connect(self._tile_picked)
        self.toolbox.unpinRequested.connect(self._unpin)
        lay.addWidget(self.toolbox)
        self._list = SR.RowList(self)
        self._list.setFocusPolicy(QtCore.Qt.NoFocus)
        self._list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._context_menu)
        lay.addWidget(self._list, 1)
        self._footer = note_label("")
        self._footer.setWordWrap(False)
        self._footer.setObjectName("PaletteFooter")
        # (its text never widens the popup: it is fitted to the width, fit_footer)
        self._footer.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self._footer.installEventFilter(self)
        self._footer_text = ""
        lay.addWidget(self._footer)
        self._edit.textChanged.connect(self._refill)
        self._edit.installEventFilter(self)
        self._list.clicked.connect(self._clicked)
        self._list.selectionModel().currentChanged.connect(self._current_changed)
        self._actions: list = []
        self._commands: List[Command] = []
        self._built_from = None
        self._toolbox_mode = False
        self._last_search = ""
        self._reason_shown = False
        from . import quick_open as QO

        self._sources = QO.Sources()
        self._objects = QO.ObjectSource()
        self._sources_dir = None
        self._apply_qss()
        self.toolbox.hide()
        from ..help import set_help

        set_help(self, "the-window#search")

    # -- the look -----------------------------------------------------------------------
    @staticmethod
    def _qss() -> str:
        from ..style.theme import COLORS

        return (
            "#CommandPalette { background: %(popup)s; border: 1px solid %(border)s; "
            "border-radius: 6px; }" % COLORS
        )

    def _apply_qss(self) -> None:
        self.setStyleSheet(self._qss())
        self._hints.setText(self._hints_html())

    def _theme_changed(self) -> None:
        """Settings > Appearance changed: the frame and the hints in the new colours, the
        footer fitted to the new text size."""
        self._apply_qss()
        self._fit_footer()
        self._list.viewport().update()

    @staticmethod
    def _hints_html() -> str:
        from ..style.theme import COLORS

        parts = []
        for prefix, what in PREFIX_HINTS:
            parts.append(
                "<a href='%s' style='color:%s; text-decoration:none;'><b>%s</b></a>"
                "&nbsp;%s" % (prefix, COLORS["accent"], prefix.replace(">", "&gt;"), what)
            )
        return "<span style='color:%s;'>%s</span>" % (COLORS["text_dim"], "&nbsp;&nbsp; ".join(parts))

    def _hint_clicked(self, prefix: str) -> None:
        self._edit.setText(prefix)
        self._edit.setFocus()

    # -- keys -----------------------------------------------------------------------------
    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt naming
        if obj is self._edit and e.type() == QtCore.QEvent.KeyPress:
            return self._key(e)
        if obj is self._footer and e.type() == QtCore.QEvent.Resize:
            self._fit_footer()
        return False

    def keyPressEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if not self._key(e):
            super().keyPressEvent(e)

    def _key(self, e) -> bool:
        k, mods = e.key(), e.modifiers()
        ctrl = bool(mods & QtCore.Qt.ControlModifier)
        alt = bool(mods & QtCore.Qt.AltModifier)
        shift = bool(mods & QtCore.Qt.ShiftModifier)
        if k in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Up):
            self._list.move(1 if k == QtCore.Qt.Key_Down else -1)
            return True
        if k in (QtCore.Qt.Key_PageDown, QtCore.Qt.Key_PageUp):
            self._list.move_page(1 if k == QtCore.Qt.Key_PageDown else -1)
            return True
        if k in (QtCore.Qt.Key_Home, QtCore.Qt.Key_End) and not shift:
            self._list.move_end(k == QtCore.Qt.Key_End)
            return True
        if k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if ctrl or alt:
                self._ctrl_enter()
            elif shift:
                self._run_current(stay=True)
            else:
                self._run_current()
            return True
        if (
            self._toolbox_mode
            and not self._edit.text()
            and not (ctrl or alt)
            and QtCore.Qt.Key_1 <= k <= QtCore.Qt.Key_9
        ):
            self._pick_tile(k - QtCore.Qt.Key_0)
            return True
        if ctrl and not alt and k == QtCore.Qt.Key_C and not self._edit.hasSelectedText():
            return self._copy_current()
        return False

    def _pick_tile(self, number: int) -> None:
        """Keys 1 to 9 in the tool box: run that tile and close; a greyed tile keeps the
        palette open and says what its command needs (as Enter on a greyed row does)."""
        a = self.toolbox.action_at(number)
        if a is None:
            return
        if self.toolbox.pick(number):
            self.hide()
            return
        from ..prefs import keymap

        try:
            cid = keymap.command_id(a)
            label = _command_label(a)
        except RuntimeError:  # swallow-ok: the action went
            return
        self._show_footer(
            "%s: %s"
            % (label.rstrip("…").rstrip("."), needs_text(self._window, a, cid) or "Not available now."),
            "warn",
        )
        self._reason_shown = True

    def _copy_current(self) -> bool:
        """Ctrl+C on a page, symbol, tag ... row: its name on the clipboard (as in Quick
        Open). False on a command, setting or help row (the field copies its text)."""
        from . import quick_open as QO

        r = self.current_row()
        if r is None or r.kind not in QO.ENTER_HINT:
            return False
        name = getattr(r.data, "name", "") or r.text
        QtWidgets.QApplication.clipboard().setText(name)
        self._show_footer("%s is on the clipboard" % name)
        return True

    # -- showing ----------------------------------------------------------------------
    def open_at(self, global_pos=None, prefill: str = "", toolbox: bool = False) -> None:
        """Show the palette (at ``global_pos``, else centred at the top of the window) with
        ``prefill`` typed; ``toolbox``: with the tool box (opened from the page)."""
        self._harvest()
        self._toolbox_mode = bool(toolbox)
        self._sources_dir = None
        if self._toolbox_mode:
            self.toolbox.set_actions(self._pinned_actions())
        self.toolbox.setVisible(self._toolbox_mode)
        # Settings > Ribbon > Search: Ctrl+K opens with the last search, selected (typing
        # replaces it); the tool box and a prefilled text never take it
        last = ""
        if not prefill and not self._toolbox_mode:
            from ..prefs.window_options import palette_keep_search

            if palette_keep_search():
                last = self._last_search
        self._edit.blockSignals(True)
        self._edit.setText(prefill or last)
        self._edit.blockSignals(False)
        self._refill()
        self._fit_width()
        self.adjustSize()
        if global_pos is None:
            g = self._window.geometry()
            global_pos = QtCore.QPoint(g.center().x() - self.width() // 2, g.top() + 120)
        self.move(self._on_screen(global_pos))
        self.show()
        self._edit.setFocus()
        if last:
            self._edit.selectAll()
        else:
            self._edit.end(False)

    def hideEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        # (the search a later Ctrl+K opens with: Settings > Ribbon > Search)
        if not self._toolbox_mode:
            self._last_search = self._edit.text()
        super().hideEvent(e)

    #: the window's menus that are filled as they open (their rows are listed too: 'Recent
    #: Pages: Mixer', 'Window: Mixing_L3')
    FILL_FIRST = ("_fill_mru", "_fill_window_list", "_fill_import_recent")

    def _harvest(self) -> None:
        for name in self.FILL_FIRST:
            fill = getattr(self._window, name, None)
            if callable(fill):
                try:
                    fill()
                except Exception:  # noqa: BLE001 -- the menu's rows are left out then
                    logsetup.note_exception("palette.fill_menu")
        self._actions = harvest_actions(self._window.menuBar())
        # the commands the selection's context menu offers that no menu has (Edit
        # Definition, Edit Composite Genie ...): the window lists them
        extra = getattr(self._window, "palette_context_actions", None)
        if extra is not None:
            try:
                self._actions = list(extra()) + self._actions
            except Exception:  # noqa: BLE001 -- the menu commands still show
                logsetup.note_exception("palette.context_actions")
        self._build_commands()

    def _build_commands(self) -> None:
        """The :class:`Command` of every harvested row (each action once)."""
        cmds = []
        seen = set()
        self._built_from = self._actions
        ids = False
        for a, _c, _l in self._actions:
            try:
                if a.objectName():
                    ids = True
                    break
            except RuntimeError:
                continue
        for k, (a, crumb, label) in enumerate(self._actions):
            try:
                key = id(a)
                if key in seen:
                    continue
                seen.add(key)
                if ids and not a.objectName() and not a.isEnabled():
                    continue  # (a placeholder: 'No recent pages')
                cmds.append(Command(a, crumb, label, k, ids))
            except RuntimeError:
                continue  # (a dynamic menu deleted it)
        self._commands = cmds

    def _fit_width(self) -> None:
        """40 % of the window, at least 460 px and about 72 characters of the text size now
        (a larger text keeps the words that tell the rows apart), within the window."""
        ww = self._window.width() if self._window is not None else 1000
        w = max(460, int(ww * 0.4), self.fontMetrics().averageCharWidth() * 72)
        w = min(w, max(320, ww - 32))
        self.setFixedWidth(w)

    def _on_screen(self, pos: QtCore.QPoint) -> QtCore.QPoint:
        screen = QtGui.QGuiApplication.screenAt(pos) or QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return pos
        r = screen.availableGeometry()
        if self._window is not None:
            # (a window larger than its screen keeps the popup over itself)
            r = r.united(self._window.frameGeometry())
        x = max(r.left(), min(pos.x(), r.right() + 1 - self.width()))
        y = max(r.top(), min(pos.y(), r.bottom() + 1 - self.height()))
        return QtCore.QPoint(x, y)

    def _fit_list(self) -> None:
        n = self._list.count()
        dl = self._list.itemDelegate()
        h = 0
        for k in range(min(n, 14)):
            r = self._list.rows_model.row_at(k)
            h += dl.header_height() if r is not None and r.kind == "header" else dl.row_height()
        self._list.setFixedHeight(max(SR.RowDelegate.row_height(), min(420, h + 4)))
        if self.isVisible():
            self.adjustSize()

    # -- the rows -----------------------------------------------------------------------
    def _refill(self) -> None:
        if self._built_from is not self._actions:
            self._build_commands()  # (the rows were given, not harvested)
        text = self._edit.text()
        mode, q, kinds = parse(text)
        rx = self._regex.matcher(q) if self._regex.on and q else None
        rows: List[SR.Row] = []
        if mode in ("all", "commands"):
            if not q and mode == "all":
                rows = self._empty_rows()
            else:
                rows = self._command_rows(q, rx)
                if mode == "all" and len(q) >= 2 and rx is None and self._is_editor():
                    rows += self._mixed_rows(q)
        elif mode == "settings":
            rows = self._setting_rows(q, rx, limit=200)
        elif mode == "help":
            rows = self._help_rows(q, limit=40)
        elif mode == "objects":
            rows = self._object_rows(q)
        elif mode == "goto":
            rows = self._goto_rows(q)
        elif mode == "find":
            rows = self._find_rows(q, kinds, limit=40)
        if not any(r.selectable for r in rows):
            rows = [SR.note(self._nothing_text(mode, q, rx))]
        self._hints.setVisible(not text.strip() and not self._toolbox_mode)
        self._list.set_rows(rows)
        self._fit_list()
        self._reason_shown = False
        self._current_changed()

    def _is_editor(self) -> bool:
        return hasattr(self._window, "run_help_action")

    def _nothing_text(self, mode, q, rx) -> str:
        if rx is not None and rx.error:
            return "Not a regular expression: %s" % rx.error
        if mode == "objects" and getattr(self._window, "doc", None) is None:
            return "Open a page first: @ searches its objects"
        if mode == "goto":
            from . import quick_open as QO

            return QO.goto_note(self._window, q)
        if mode == "help":
            return 'No help section matches "%s"' % q if q else "Type what to look up"
        if mode == "settings":
            return 'No setting matches "%s"' % q
        if mode == "find":
            from . import quick_open as QO

            kinds = parse(self._edit.text())[2]
            what = QO.KIND_LABEL.get(kinds[0], "name") if len(kinds) == 1 else "name"
            if q:
                return 'No %s matches "%s"' % (what, q)
            return "No %ss in this project" % what
        if mode == "objects":
            return 'No object matches "%s"' % q
        return 'No command matches "%s"' % q if q else "No commands"

    def _command_rows(self, q: str, rx=None) -> List[SR.Row]:
        from ..prefs import keymap

        mru = keymap.palette_mru(self._window)
        pos_of = {c: k for k, c in enumerate(mru)}
        scored = []
        for c in self._commands:
            a = c.action
            try:
                enabled = a.isEnabled()
            except RuntimeError:
                continue  # deleted by a dynamic menu
            if not q:
                if enabled:
                    scored.append(((0, 0, 0, c.order), c, (), ""))
                continue
            alias = ""
            hits: Sequence[int] = ()
            if rx is not None:
                s = 0 if rx(c.name, c.bare, c.crumb) else -1
            else:
                m = SR.match(q, c.bare, substring_min=4)
                if m is not None:
                    off = len(c.name) - len(c.bare)
                    s, hits = m[0], [p + off for p in m[1]]
                else:
                    m = SR.match(q, c.name, substring_min=4)
                    s, hits = (m[0] + 40, m[1]) if m is not None else (-1, ())
                if s < 0 and c.crumb:
                    cm = SR.match(q, c.crumb, substring_min=99)
                    if cm is not None and cm[0] < 100:
                        s = 300 + cm[0]
                a_r = _alias_rank(q, c.bare)
                if a_r >= 0 and (s < 0 or a_r < s):
                    s, hits = a_r, ()
                    word = _alias_word(q, c.bare)
                    alias = "Graphics Builder: %s" % word.title() if word else ""
                if s < 0 or s > 5:
                    kw = _keyword_hit(a, c.cid, q)
                    if kw and (s < 0 or 5 < s):
                        s, hits, alias = 5, (), "Also: %s" % kw
            if s < 0:
                continue
            boost = pos_of.get(c.cid, len(mru) + 1)
            scored.append(((s, 0 if enabled else 1, boost, c.order), c, hits, alias))
        scored.sort(key=lambda t: t[0])
        from ..prefs import timing_options

        return [
            command_row(self._window, c, hits, alias)
            for _k, c, hits, alias in scored[: timing_options.PALETTE_ROWS]
        ]

    def _empty_rows(self) -> List[SR.Row]:
        """Nothing typed: Recently used, For the selection, Suggested, then every other
        command (each once, in the first section it belongs to)."""
        from ..prefs import keymap

        by_id: Dict[str, Command] = {}
        by_action: Dict[int, Command] = {}
        for c in self._commands:
            by_id.setdefault(c.cid, c)
            by_action.setdefault(id(c.action), c)
        used = set()
        rows: List[SR.Row] = []

        def section(title: str, cmds, enabled_only: bool = False) -> None:
            out = []
            for c in cmds:
                if c is None or id(c) in used:
                    continue
                try:
                    if enabled_only and not c.action.isEnabled():
                        continue
                except RuntimeError:
                    continue
                used.add(id(c))
                out.append(command_row(self._window, c))
            if out:
                rows.append(SR.header(title))
                rows.extend(out)

        mru = keymap.palette_mru(self._window)
        section("Recently used", [by_id.get(i) for i in mru[: MRU_SHOWN * 2]][:MRU_SHOWN])
        section(
            "For the selection",
            [by_action.get(id(a)) or self._extra_command(a) for a in self._selection_actions()],
        )
        tiles = set(pins()) if self._toolbox_mode else set()
        section("Suggested", [by_id.get(i) for i in DRAWING_TOOLS + SUGGESTED if i not in tiles])
        section("All commands", self._commands, enabled_only=True)
        return rows

    def _extra_command(self, a):
        return next((c for c in self._commands if c.action is a), None)

    def _selection_actions(self) -> list:
        """The commands of the contextual ribbon tabs the selection shows (Text, Shape,
        Genie), then the selection's context-menu commands."""
        w = self._window
        out = []
        try:
            if getattr(w, "doc", None) is None or not getattr(w, "_sel", None):
                return []
            traits = w._classify_selection()
            ribbon = getattr(w, "ribbon", None)
            for t in ("TEXT", "SHAPE", "GENIE"):
                if t in traits and ribbon is not None:
                    for g in ribbon.groups_of(t.title()):
                        out += [a for a in g.menu_actions() if a is not None]
        except Exception:  # noqa: BLE001 -- the other sections still show
            logsetup.note_exception("palette.selection_actions")
        out += [a for a, crumb, _l in self._actions if crumb == "Selection"]
        return list(dict.fromkeys(out))

    def _mixed_rows(self, q: str) -> List[SR.Row]:
        rows: List[SR.Row] = []
        s = self._setting_rows(q, None, limit=MIXED_SETTINGS)
        if s:
            rows.append(SR.header("Settings"))
            rows += s
        h = self._help_rows(q, limit=MIXED_HELP)
        if h:
            rows.append(SR.header("Help"))
            rows += h
        f = self._find_rows(q, None, limit=MIXED_FIND * 4, per_kind=MIXED_FIND, tags_built_only=True)
        if f:
            rows.append(SR.header("Pages, symbols, genies and tags"))
            rows += f
        return rows

    def _setting_rows(self, q: str, rx, limit: int) -> List[SR.Row]:
        if not self._is_editor():
            return []
        from ..style.theme import icon

        scored = []
        for k, e in enumerate(settings_index(self._window)):
            if rx is not None:
                if not rx(e.label, e.words):
                    continue
                s, hits = 0, ()
            elif q:
                # (no loose letters: 'rot' is not 'Ask before closing ... recording or tags')
                m = SR.match(q, e.label, substring_min=3)
                if m is None or m[0] >= 100:
                    w = SR.match(q, e.words.replace("\n", " "), substring_min=99)
                    if w is None or w[0] >= 100:
                        continue
                    s, hits = 200 + w[0], ()
                else:
                    s, hits = m
            else:
                s, hits = 0, ()
            scored.append((s, k, e, hits))
        scored.sort(key=lambda t: (t[0], t[1]))
        out = []
        for _s, _k, e, hits in scored[:limit]:
            state = setting_state(e)
            out.append(
                SR.Row(
                    "setting",
                    e.label,
                    e.path,
                    e,
                    hits=hits,
                    chip=state,
                    icon=icon("settings"),
                    spoken="Setting: %s%s    %s" % (e.label, (", %s" % state) if state else "", e.path),
                )
            )
        return out

    def _help_rows(self, q: str, limit: int) -> List[SR.Row]:
        if not q:
            return []
        from ... import help as H
        from ..style.theme import icon

        out = []
        try:
            hits = H.search_sections(q, limit=limit, regex=self._regex.on)
        except Exception:  # noqa: BLE001 -- no help rows then
            logsetup.note_exception("palette.help_rows")
            hits = []
        for h in hits:
            title = H.page_title(h.topic)
            text = title if not h.anchor else "%s > %s" % (title, h.heading)
            m = SR.match(q, text, substring_min=3)
            out.append(
                SR.Row("help", text, h.snippet, h, hits=m[1] if m else (), icon=icon("help"), tip=h.snippet)
            )
        return out

    def _ensure_sources(self) -> None:
        w = self._window
        pdir = ""
        doc = getattr(w, "doc", None)
        import os

        if doc is not None and getattr(doc, "path", ""):
            pdir = os.path.dirname(os.path.abspath(doc.path))
        else:
            try:
                recent = w.recent_pages()
            except Exception:  # noqa: BLE001  # swallow-ok: no recent pages
                recent = []
            if recent:
                pdir = os.path.dirname(recent[0])
        if pdir != self._sources_dir:
            self._sources.set_project_dir(pdir)
            self._sources_dir = pdir

    def _find_rows(
        self, q: str, kinds, limit: int, per_kind: int = 0, tags_built_only: bool = False
    ) -> List[SR.Row]:
        if not self._is_editor():
            return []
        from . import quick_open as QO

        self._ensure_sources()
        if not self._sources.project_dir:
            return []
        kinds = kinds or ("page", "symbol", "genie", "tag")
        if not q and kinds:
            ranked = self._sources.hits("", kinds, limit=limit)
        else:
            ranked = self._sources.hits(q, kinds, limit=limit * 3, tags_built_only=tags_built_only)
        out = []
        count: Dict[str, int] = {}
        for _s, h, pos in ranked:
            if per_kind:
                if count.get(h.kind, 0) >= per_kind:
                    continue
                if _s >= 30:
                    continue  # (a part of a word among the commands: noise)
            count[h.kind] = count.get(h.kind, 0) + 1
            out.append(QO.hit_row(self._window, h, pos, q))
            if len(out) >= limit:
                break
        return out

    def _object_rows(self, q: str) -> List[SR.Row]:
        from . import quick_open as QO

        doc = getattr(self._window, "doc", None)
        if doc is None:
            return []
        return [QO.hit_row(self._window, h, pos, q) for _s, h, pos in self._objects.hits(doc, q)]

    def _goto_rows(self, q: str) -> List[SR.Row]:
        from . import quick_open as QO

        return [
            QO.hit_row(self._window, h, pos, q)
            for h, pos in QO.goto_hits(self._window, self._objects, ":" + q)
        ]

    # -- the current row --------------------------------------------------------------
    def current_row(self) -> Optional[SR.Row]:
        r = self._list.current_row_obj()
        return r if r is not None and r.selectable else None

    def _current_changed(self, *_a) -> None:
        if self._reason_shown:
            self._reason_shown = False
        self._show_footer()

    def _show_footer(self, text: str = "", role: str = "text_dim") -> None:
        from . import quick_open as QO
        from ..style.theme import set_note_role

        r = self.current_row()
        if not text:
            if r is None:
                text = ""
            elif r.kind == "command":
                text = FOOTERS["command"]
            else:
                text = FOOTERS.get(r.kind) or QO.ENTER_HINT.get(r.kind, "")
        set_note_role(self._footer, role)
        self._footer_text = text
        self._fit_footer()

    def _fit_footer(self) -> None:
        """The footer's text in the footer's width (fit_footer); the whole text in its tip."""
        lab = self._footer
        text = self._footer_text
        shown = fit_footer(text, lab.fontMetrics(), lab.contentsRect().width())
        lab.setText(shown)
        lab.setToolTip(text if shown != text else "")

    def footer_text(self) -> str:
        """The footer's whole text (it shows less when the popup is narrow)."""
        return self._footer_text

    def _clicked(self, index) -> None:
        r = index.data(SR.ROW_ROLE)
        if r is None or not r.selectable:
            return
        self._list.setCurrentRow(index.row())
        self._run_current()

    # -- Enter ------------------------------------------------------------------------
    def _run_current(self, stay: bool = False) -> None:
        r = self.current_row()
        if r is None:
            return
        if r.kind == "command":
            self._run_command(r, stay)
            return
        if stay and r.kind in ("symbol", "genie", "supergenie", "template"):
            self._run_hit(r, "edit")
            return
        self._run_other(r)

    def _run_command(self, r: SR.Row, stay: bool) -> None:
        action, label = r.data
        try:
            enabled = action.isEnabled()
        except RuntimeError:
            enabled = False
        if not enabled:
            # (the palette stays open and says what the command needs)
            self._show_footer(
                "%s: %s" % (r.text.rstrip("…").rstrip("."), r.reason or "Not available now."), "warn"
            )
            self._reason_shown = True
            return
        from ..prefs import keymap

        keymap.push_palette_mru(self._window, r.cid or label)
        if stay:
            try:
                action.trigger()
            except RuntimeError:  # swallow-ok: the action went
                pass
            k = self._list.currentRow()
            self._harvest()
            self._refill()
            self._list.setCurrentRow(min(k, self._list.count() - 1))
            self._edit.setFocus()
            return
        self.hide()

        def fire():
            try:
                action.trigger()
            except RuntimeError:
                pass

        QtCore.QTimer.singleShot(0, fire)

    def _run_other(self, r: SR.Row) -> None:
        if r.kind == "setting":
            e = r.data
            self.hide()
            w = self._window
            QtCore.QTimer.singleShot(0, self, lambda: show_setting(w, e.page, e.label, e.key))
            return
        if r.kind == "help":
            h = r.data
            self.hide()
            from .. import help as UH

            topic = "%s#%s" % (h.topic, h.anchor) if h.anchor else h.topic
            QtCore.QTimer.singleShot(0, self, lambda: UH.show_help(topic, main=self._window))
            return
        self._run_hit(r, "default")

    def _run_hit(self, r: SR.Row, mode: str) -> None:
        from . import quick_open as QO

        listed = [x.data for x in self._list.rows() if x.kind == "object"]
        self.hide()
        w = self._window
        QtCore.QTimer.singleShot(0, self, lambda: QO.run_hit(w, r.data, mode, listed))

    def _ctrl_enter(self) -> None:
        """Ctrl+Enter / Alt+Enter: Change Shortcut for a command, a page in a new window,
        every object listed selected."""
        r = self.current_row()
        if r is None:
            return
        if r.kind == "command":
            cid = r.cid
            if not cid or cid.startswith("menu:"):
                self._show_footer("This command cannot have a key of its own.", "warn")
                return
            self.hide()
            from ..prefs.keymap_page import open_change_shortcut

            w = self._window
            QtCore.QTimer.singleShot(0, self, lambda: open_change_shortcut(w, cid, window=w))
            return
        if r.kind == "page":
            self._run_hit(r, "new_window")
        elif r.kind in ("object", "goto"):
            self._run_hit(r, "all")
        else:
            self._run_current()

    # -- pins and the tool box ------------------------------------------------------------
    def _pinned_actions(self) -> list:
        from ..prefs import keymap

        out = []
        for cid in pins():
            a = keymap.find_action(self._window, cid)
            if a is not None:
                out.append(a)
        return out

    def _tile_picked(self, _action) -> None:
        self.hide()

    def _unpin(self, cid: str) -> None:
        unpin(cid)
        self.toolbox.set_actions(self._pinned_actions())
        self.adjustSize()

    def row_menu(self, r: SR.Row) -> Optional[QtWidgets.QMenu]:
        """The right-click menu of a command row: Pin to the Tool Box / Unpin, Assign a
        Key…."""
        if r is None or r.kind != "command" or not r.cid or r.cid.startswith("menu:"):
            return None
        m = QtWidgets.QMenu(self)
        if r.cid in pins():
            a = m.addAction("Unpin from the Tool Box")
            a.setObjectName("palette.unpin")
            a.triggered.connect(lambda _=False, c=r.cid: self._unpin(c))
        else:
            a = m.addAction("Pin to the Tool Box")
            a.setObjectName("palette.pin")
            a.triggered.connect(lambda _=False, c=r.cid: self._pin(c))
        k = m.addAction("Assign a Key…")
        k.setObjectName("palette.assign_key")
        k.triggered.connect(lambda _=False: self._ctrl_enter())
        return m

    def _pin(self, cid: str) -> None:
        pin(cid)
        if self._toolbox_mode:
            self.toolbox.set_actions(self._pinned_actions())
            self.adjustSize()
        self._show_footer("Pinned to the tool box (S on the page)")

    def _context_menu(self, pos) -> None:
        idx = self._list.indexAt(pos)
        if not idx.isValid():
            return
        self._list.setCurrentRow(idx.row())
        m = self.row_menu(idx.data(SR.ROW_ROLE))
        if m is not None:
            m.exec(self._list.viewport().mapToGlobal(pos))


# --------------------------------------------------------------------------- the header field
class HeaderSearch(QtWidgets.QWidget):
    """The ribbon header's Search field."""

    WIDE, NARROW = 220, 160
    NARROW_BELOW, BUTTON_BELOW = 1366, 1100

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.setObjectName("HeaderSearch")
        self._window = window
        self._companions: list = []
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, _sp(1), 0)
        lay.setSpacing(0)
        from ..style import tips
        from ..style.theme import icon, search_field

        self.field = search_field("Search")
        self.field.setObjectName("HeaderSearchField")
        self.field.setAccessibleName("Search")
        self.field.setClearButtonEnabled(False)
        # (a launcher, not a filter: no '.*' of its own; the palette has one)
        self.field.removeAction(self.field.regex_toggle.action)
        # the search icon's button: a screen reader says what it is, it has the rich tip of
        # the header's other buttons (the palette's key in its key cap) and a click on it
        # opens the palette as a click on the field does
        self._icon_buttons: List[QtWidgets.QToolButton] = []
        for b in self.field.findChildren(QtWidgets.QToolButton):
            a = b.defaultAction()
            if a is None:
                continue
            b.setAccessibleName("Search")
            b.setProperty(tips.TIP_NAME_PROP, "Search")
            b.setProperty(tips.TIP_TEXT_PROP, self.TIP + ".")
            tips.install(b)
            a.triggered.connect(self._icon_clicked)
            self._icon_buttons.append(b)
        self.field.installEventFilter(self)
        from ..help import set_help

        set_help(self, "the-window#search")
        self._chip = QtWidgets.QLabel(self.field)
        self._chip.setObjectName("HeaderSearchChip")
        self._chip.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.field)
        self.button = QtWidgets.QToolButton(self)
        self.button.setObjectName("HeaderSearchButton")
        self.button.setIcon(icon("search"))
        self.button.setAutoRaise(True)
        self.button.setAccessibleName("Search")
        self.button.clicked.connect(lambda: self.open_palette(""))
        self.button.hide()
        lay.addWidget(self.button)
        self._state = None
        self._ignore_focus = False
        self._apply_look()
        from ..prefs import keymap

        keymap.notifier().applied.connect(self._keys_applied)

    # -- the look -------------------------------------------------------------------------
    #: what the field, its magnifier and the button say they do (the key follows)
    # (each search says what it does next to the others: the Objects panel selects on this
    # page, Find and Replace lists every field across pages)
    TIP = (
        "Find a command, page, symbol, object, setting or help topic and go to it. To select "
        "this page's objects by tag or text, use the Objects panel's search; to list or change "
        "every matching field across pages, Edit > Find and Replace"
    )

    def _key_text(self) -> str:
        """The command palette's first key ('' when Settings > Keyboard left it none)."""
        from ..prefs import keymap

        act = getattr(self._window, "act_palette", None)
        keys = keymap.keys_of(act) if act is not None else []
        return keymap.native(keys[0]) if keys else ""

    def _apply_look(self) -> None:
        from ..style import tips
        from ..style.theme import COLORS, font, px, type_qss

        key = self._key_text()
        tip = "%s (%s)" % (self.TIP, key) if key else self.TIP
        self.field.setToolTip(tip)
        self.button.setToolTip(tip)
        for b in self._icon_buttons:
            try:
                b.setProperty(tips.TIP_KEY_PROP, key)
            except RuntimeError:  # swallow-ok: a button the field dropped
                continue
        # (no chip when the palette has no key: it would name a key that does nothing)
        self._chip.setText(key)
        self._chip.setVisible(bool(key))
        self._chip.setFont(font("caption"))
        self._chip.setStyleSheet(
            "QLabel#HeaderSearchChip { color: %s; border: 1px solid %s; border-radius: 3px; "
            "padding: 0px 4px; background: transparent; %s }"
            % (COLORS["text_dim"], COLORS["border"], type_qss("caption"))
        )
        self._chip.adjustSize()
        self.field.setTextMargins(0, 0, self._chip.width() + px(6) if key else 0, 0)
        self.field.setStyleSheet(
            "QLineEdit#HeaderSearchField { background: %(field)s; border: 1px solid "
            "%(control_edge)s; border-radius: 4px; padding: 1px 2px; }"
            'QLineEdit#HeaderSearchField[kbdFocus="true"] { border: 1px solid %(accent)s; }' % COLORS
        )
        self.button.setIconSize(QtCore.QSize(px(16), px(16)))
        self._place_chip()

    def _theme_changed(self) -> None:
        self._apply_look()
        self.fit()

    def _place_chip(self) -> None:
        from ..style.theme import px

        c = self._chip
        c.move(self.field.width() - c.width() - px(5), (self.field.height() - c.height()) // 2)

    def resizeEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().resizeEvent(e)
        self._place_chip()

    def refresh_keys(self) -> None:
        """The palette's key changed (Settings > Keyboard): the chip says the new one."""
        self._apply_look()

    def _keys_applied(self, window) -> None:
        if window is self._window:
            self.refresh_keys()

    def _icon_clicked(self, _checked: bool = False) -> None:
        """A click on the field's magnifier: the palette, as a click on the field."""
        self.open_palette(self.field.text())

    # -- the companions and the width -----------------------------------------------------
    def set_companions(self, buttons) -> None:
        """The utility buttons beside the field: they drop their names before it narrows."""
        self._companions = list(buttons)
        host = self.parentWidget()
        if host is not None:
            host.installEventFilter(self)
        self.fit()

    def _other_width(self) -> int:
        """What the header's other parts take (not the field, not the companions)."""
        host = self.parentWidget()
        lay = host.layout() if host is not None else None
        if lay is None:
            return 0
        skip = {id(self)} | {id(b) for b in self._companions}
        total = lay.contentsMargins().left() + lay.contentsMargins().right()
        for k in range(lay.count()):
            item = lay.itemAt(k)
            w = item.widget()
            if w is not None:
                if id(w) in skip or w.isHidden():
                    continue
                total += w.sizeHint().width() + lay.spacing()
            elif item.layout() is not None:
                sub = item.layout()
                for j in range(sub.count()):
                    ww = sub.itemAt(j).widget()
                    if ww is not None and id(ww) not in skip and not ww.isHidden():
                        total += ww.sizeHint().width() + sub.spacing()
        return total

    def _companion_width(self, style) -> int:
        total = 0
        for b in self._companions:
            try:
                old = b.toolButtonStyle()
                b.setToolButtonStyle(style)
                total += b.sizeHint().width() + 2
                b.setToolButtonStyle(old)
            except RuntimeError:  # swallow-ok: a button that went
                continue
        return total

    def fit(self) -> str:
        """Choose the field's state for the header's width: 'wide', 'narrow' or 'button';
        the companions show their names only while all of it fits."""
        from ..style.theme import px

        host = self.parentWidget()
        room = host.width() if host is not None else 4000
        win = self._window.width() if self._window is not None else 4000
        other = self._other_width()
        text_w = self._companion_width(QtCore.Qt.ToolButtonTextBesideIcon)
        icon_w = self._companion_width(QtCore.Qt.ToolButtonIconOnly)
        wide, narrow, btn = px(self.WIDE), px(self.NARROW), px(28)
        want = "wide" if win >= self.NARROW_BELOW else ("narrow" if win >= self.BUTTON_BELOW else "button")
        names = True
        order = ["wide", "narrow", "button"]
        state = want
        for s in order[order.index(want) :]:
            size = {"wide": wide, "narrow": narrow, "button": btn}[s]
            if other + size + text_w <= room:
                state, names = s, True
                break
            if other + size + icon_w <= room:
                state, names = s, False
                break
            state, names = s, False
        for b in self._companions:
            try:
                b.setToolButtonStyle(
                    QtCore.Qt.ToolButtonTextBesideIcon if names else QtCore.Qt.ToolButtonIconOnly
                )
            except RuntimeError:  # swallow-ok: a button that went
                continue
        # (the width also when only the text size changed: px() of the new scale)
        width = wide if state == "wide" else narrow
        if state != self._state or (
            state != "button" and (self.field.minimumWidth() != width or self.field.maximumWidth() != width)
        ):
            self._state = state
            self.field.setVisible(state != "button")
            self.button.setVisible(state == "button")
            if state != "button":
                self.field.setFixedWidth(width)
            self.updateGeometry()
        self._place_chip()
        return state

    @property
    def state(self) -> str:
        return self._state or ""

    def companions_named(self) -> bool:
        return bool(self._companions) and all(
            b.toolButtonStyle() == QtCore.Qt.ToolButtonTextBesideIcon for b in self._companions
        )

    # -- opening the palette ------------------------------------------------------------
    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt naming
        t = e.type()
        if obj is self.parentWidget() and t == QtCore.QEvent.Resize:
            QtCore.QTimer.singleShot(0, self, self.fit)  # (none once the bar is gone)
            return False
        if obj is self.field:
            if (
                t == QtCore.QEvent.FocusIn
                and not self._ignore_focus
                and e.reason()
                in (
                    QtCore.Qt.MouseFocusReason,
                    QtCore.Qt.TabFocusReason,
                    QtCore.Qt.BacktabFocusReason,
                    QtCore.Qt.ShortcutFocusReason,
                )
            ):
                QtCore.QTimer.singleShot(0, self, lambda: self.open_palette(self.field.text()))
            elif t == QtCore.QEvent.MouseButtonPress and self.field.hasFocus():
                QtCore.QTimer.singleShot(0, self, lambda: self.open_palette(self.field.text()))
            elif t == QtCore.QEvent.KeyPress:
                txt = e.text()
                if (
                    txt
                    and txt.isprintable()
                    and not (e.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier))
                ):
                    self.open_palette(self.field.text() + txt)
                    return True
                if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Down):
                    self.open_palette(self.field.text())
                    return True
        return False

    def open_palette(self, text: str = "") -> None:
        """The palette under the field (or the button), with ``text`` typed."""
        pal = getattr(self._window, "palette_popup", None)
        if pal is None:
            return
        anchor = self.field if self.field.isVisible() else self.button
        pos = anchor.mapToGlobal(QtCore.QPoint(0, anchor.height() + 2))
        pal.open_at(pos, prefill=text)
        # (the palette's frame ends under the field's right end when it fits there)
        right = anchor.mapToGlobal(QtCore.QPoint(anchor.width(), 0)).x()
        if pos.x() + pal.width() > right + 4:
            x = max(right - pal.width(), self._window.mapToGlobal(QtCore.QPoint(8, 0)).x())
            pal.move(pal._on_screen(QtCore.QPoint(x, pos.y())))
        self.field.clear()
        pal.installEventFilter(self._closer())

    def _closer(self):
        c = getattr(self, "_close_watch", None)
        if c is None:
            c = self._close_watch = _PaletteCloseWatch(self)
        return c

    def palette_closed(self) -> None:
        """The palette closed: the keyboard goes back to the page (not into the field,
        which would open the palette again)."""
        if self.field.hasFocus():
            self._ignore_focus = True
            view = getattr(self._window, "view", None)
            if view is not None:
                view.setFocus(QtCore.Qt.OtherFocusReason)
            else:
                self.field.clearFocus()
            QtCore.QTimer.singleShot(0, self, self._focus_ok)

    def _focus_ok(self) -> None:
        self._ignore_focus = False


class _PaletteCloseWatch(QtCore.QObject):
    def __init__(self, owner: HeaderSearch):
        super().__init__(owner)
        self._owner = owner

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt naming
        if e.type() == QtCore.QEvent.Hide:
            obj.removeEventFilter(self)
            self._owner.palette_closed()
        return False
