"""Rich tool tips for command buttons."""

from __future__ import annotations

import html
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

#: the setting that turns the rich tips off (plain 'Name (key)' then)
SETTING = "ui/rich_tooltips"

#: dynamic properties a window sets on its actions (main_window._describe_actions)
DESCRIPTION_PROP = "ctgDescription"
NEEDS_PROP = "ctgNeeds"

#: dynamic properties a button sets when its tip differs from its command's (the ribbon's
#: chevron: 'Expand the Ribbon' while collapsed)
TIP_NAME_PROP = "ctgTipName"
TIP_TEXT_PROP = "ctgTipText"
#: the key a button's tip names when its own action has none (the header Search field's
#: magnifier names the command palette's key)
TIP_KEY_PROP = "ctgTipKey"

#: the property install() sets on a button that shows the rich tip
RICH_TIP_PROP = "ctgRichTip"
#: the need a greyed command's status tip ends with now ('' while it is enabled):
#: show_need_in_status_tip keeps it in step, description() leaves it out
SHOWN_NEED_PROP = "ctgShownNeed"

#: what a command of each ViewState.ENABLE_WHEN rule needs before it can run
NEEDS = {
    "doc": "Open a page first.",
    "sel": "Select an object first.",
    "multi": "Select two or more objects.",
    "three": "Select three or more objects.",
    "page": "Open a page first (not a library element).",
    "project": "Open a page of a project first.",
}


def enabled_setting() -> bool:
    from ..prefs.settings import app_settings, SETTINGS_KEYS

    try:
        return bool(app_settings().value(SETTING, SETTINGS_KEYS[SETTING], type=bool))
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled value reads as the default
        return True


def command_name(action: QtGui.QAction) -> str:
    """The command's name as a tip shows it: no '&' access key, no trailing '…'."""
    text = (action.text() or action.iconText() or "").replace("&&", "\0").replace("&", "")
    text = text.replace("\0", "&").strip()
    for tail in ("…", "..."):
        if text.endswith(tail):
            text = text[: -len(tail)].rstrip()
    return text


def first_key(action: QtGui.QAction) -> str:
    """The command's first key in the system's words ('Ctrl+D'), '' when it has none."""
    for k in action.shortcuts():
        if not k.isEmpty():
            return k.toString(QtGui.QKeySequence.NativeText)
    return ""


def description(action: QtGui.QAction) -> str:
    """One sentence of what the command does: its status tip (without the need a greyed
    command's status tip ends with), a tool tip of its own (one that is not just the name)
    or the description its window gave it."""
    name = command_name(action)
    for text in (base_status_tip(action), action.toolTip(), action.property(DESCRIPTION_PROP)):
        text = (text or "").strip()
        if text and text.replace("&", "").rstrip("…").strip() != name and not text.startswith("<"):
            return text if text.endswith((".", "?", "!", ")")) else text + "."
    return ""


def own_tip(action: QtGui.QAction) -> str:
    """The sentence a command's own tool tip gives ('' when it is just the name, or HTML):
    the status bar line of a command without a DESCRIPTIONS entry."""
    name = command_name(action)
    text = (action.toolTip() or "").strip()
    if not text or text.startswith("<") or text.replace("&", "").rstrip("…").strip() == name:
        return ""
    return text


def needs(action: QtGui.QAction) -> str:
    """What a greyed command needs ('' while it is enabled or nothing is known)."""
    if action.isEnabled():
        return ""
    v = action.property(NEEDS_PROP) or ""
    return NEEDS.get(v, v)


def base_status_tip(action: QtGui.QAction) -> str:
    """The command's status tip without the need it ends with while greyed."""
    text = action.statusTip() or ""
    shown = action.property(SHOWN_NEED_PROP) or ""
    if shown and text.endswith(shown):
        return text[: -len(shown)].rstrip()
    return text


def _sync_need_tip(action: QtGui.QAction) -> None:
    try:
        base = base_status_tip(action)
        need = needs(action)
    except RuntimeError:  # the action is gone
        return
    new = ("%s %s" % (base, need)).strip() if need else base
    action.setProperty(SHOWN_NEED_PROP, need)
    if new != action.statusTip():
        action.setStatusTip(new)


#: the need of ``action`` changed (NEEDS_PROP set anew): its status tip follows
refresh_need_tip = _sync_need_tip


def show_need_in_status_tip(action: QtGui.QAction) -> None:
    """A greyed menu item says why in the status bar: while the command is
    disabled its status tip ends with what it needs ('Edit the page template. Open a page
    with a template.'); enabled again, the need goes. The rich tips show the need on a line
    of their own (description() leaves it out of the sentence). Once per action."""
    if action.property(SHOWN_NEED_PROP) is not None:
        return
    action.setProperty(SHOWN_NEED_PROP, "")
    action.enabledChanged.connect(lambda _on=False, a=action: _sync_need_tip(a))
    _sync_need_tip(action)


def help_topic(widget: Optional[QtWidgets.QWidget], action: QtGui.QAction) -> str:
    from .. import help as _help

    t = _help.help_of(action)
    if not t and widget is not None:
        try:
            t = _help.topic_for(widget)
        except RuntimeError:
            t = ""
    return t or ""


def _widget_text(widget: Optional[QtWidgets.QWidget], prop: str) -> str:
    if widget is None:
        return ""
    try:
        return str(widget.property(prop) or "")
    except RuntimeError:  # the button is gone
        return ""


def _key_of(action: QtGui.QAction, widget: Optional[QtWidgets.QWidget]) -> str:
    return first_key(action) or _widget_text(widget, TIP_KEY_PROP)


def plain_tip(action: QtGui.QAction, widget: Optional[QtWidgets.QWidget] = None) -> str:
    """'Name (key)', or the name alone."""
    key = _key_of(action, widget)
    name = _widget_text(widget, TIP_NAME_PROP) or command_name(action)
    return "%s (%s)" % (name, key) if key else name


def rich_tip(action: QtGui.QAction, widget: Optional[QtWidgets.QWidget] = None) -> str:
    """The rich tip (HTML) of ``action`` shown on ``widget``."""
    from .theme import COLORS

    name = html.escape(_widget_text(widget, TIP_NAME_PROP) or command_name(action))
    key = _key_of(action, widget)
    head = '<td style="font-weight:600; white-space:pre;">%s</td>' % name
    if key:
        head += (
            '<td width="10"></td><td style="background-color:%s; color:%s; '
            'padding:0px 4px; white-space:pre;">%s</td>'
            % (COLORS.get("raised", "#343941"), COLORS.get("text_dim", "#969ca5"), html.escape(key))
        )
    parts = ['<table cellspacing="0" cellpadding="0"><tr>%s</tr></table>' % head]
    desc = _widget_text(widget, TIP_TEXT_PROP) or description(action)
    if desc:
        parts.append('<p style="margin-top:4px; margin-bottom:0px;">%s</p>' % html.escape(desc))
    need = needs(action)
    if need:
        parts.append(
            '<p style="margin-top:4px; margin-bottom:0px; color:%s;">%s</p>'
            % (COLORS.get("warn", "#d9a441"), html.escape(need))
        )
    if help_topic(widget, action):
        parts.append(
            '<p style="margin-top:4px; margin-bottom:0px; color:%s;">F1 for help</p>'
            % COLORS.get("text_dim", "#969ca5")
        )
    return "<qt>%s</qt>" % "".join(parts)


def tip_for(widget: QtWidgets.QWidget) -> str:
    """The tip ``widget`` shows now: rich or plain by the setting, '' when it hosts no
    command (Qt's own tip then)."""
    action = action_of(widget)
    if action is None:
        return ""
    return rich_tip(action, widget) if enabled_setting() else plain_tip(action, widget)


def action_of(widget: QtWidgets.QWidget) -> Optional[QtGui.QAction]:
    try:
        if isinstance(widget, QtWidgets.QToolButton) and widget.defaultAction() is not None:
            return widget.defaultAction()
    except RuntimeError:  # the button is gone
        return None
    return None


def _owner(win: Optional[QtWidgets.QWidget]) -> Optional[QtWidgets.QWidget]:
    """The window ``win`` answers for: a tool window (the drawing toolbox) for the editor
    window it belongs to."""
    while win is not None and win.windowType() == QtCore.Qt.Tool and win.parentWidget() is not None:
        win = win.parentWidget().window()
    return win


def pointer_topic() -> str:
    """The help topic of the command button under the mouse, or ''."""
    try:
        w = QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos())
        while w is not None and not w.property(RICH_TIP_PROP):
            if w.isWindow():
                return ""
            w = w.parentWidget()
        if w is None:
            return ""
        active = QtWidgets.QApplication.activeWindow()
        if active is not None and _owner(active) is not _owner(w.window()):
            return ""
        action = action_of(w)
        return help_topic(w, action) if action is not None else ""
    except RuntimeError:  # the button went away under the mouse
        return ""


def accessible_shortcut(action: QtGui.QAction) -> str:
    """'Shortcut: R' for an accessible description, '' without a key."""
    key = first_key(action)
    return "Shortcut: %s" % key if key else ""


class _TipFilter(QtCore.QObject):
    """Answers a ToolTip event on a command button with the tip of that moment."""

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        if ev.type() != QtCore.QEvent.Type.ToolTip:
            return False
        try:
            text = tip_for(obj)
        except RuntimeError:  # the button is being deleted
            return False
        if not text:
            return False
        QtWidgets.QToolTip.showText(ev.globalPos(), text, obj, obj.rect())
        return True


def install(widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
    """Give ``widget`` (a button hosting a command) the rich tip. Once per widget."""
    if widget.property(RICH_TIP_PROP):
        return widget
    app = QtWidgets.QApplication.instance()
    f = getattr(app, "_ctg_tip_filter", None)
    if f is None:
        f = app._ctg_tip_filter = _TipFilter(app)  # (one per application)
    widget.installEventFilter(f)
    widget.setProperty(RICH_TIP_PROP, True)
    return widget
