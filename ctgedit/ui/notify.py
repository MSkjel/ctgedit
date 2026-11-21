"""The message centre of an editor window."""

from __future__ import annotations

import time
from typing import Callable, Iterable, List, NamedTuple, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .prefs.settings import app_settings

LEVELS = ("info", "success", "warning", "error")
#: the theme role each level is drawn in
LEVEL_ROLE = {"info": "accent", "success": "tip", "warning": "warn", "error": "danger"}
#: words for screen readers and tool tips
LEVEL_NAME = {"info": "Information", "success": "Done", "warning": "Warning", "error": "Error"}
#: Settings > Appearance 'Messages stay:' (ui/message_time) and its factor of the reading time
MESSAGE_TIMES = (("short", "Short"), ("normal", "Normal"), ("long", "Long"))
TIME_FACTOR = {"short": 0.7, "normal": 1.0, "long": 1.6}
#: the messages a window keeps
HISTORY_MAX = 200
#: a plain status message (QStatusBar.showMessage) is kept in the list when it stays at
#: least this long (shorter ones are hints while dragging or typing)
RECORD_MIN_MS = 4000


class Action(NamedTuple):
    """A button of a message: ``fn()`` runs it; ``enabled()`` (None = always) says whether
    it can still run."""

    label: str
    fn: Callable
    enabled: Optional[Callable] = None

    def can_run(self) -> bool:
        if self.enabled is None:
            return True
        try:
            return bool(self.enabled())
        except Exception:  # noqa: BLE001  # swallow-ok: a stale button is shown disabled
            return False

    def run(self) -> None:
        if self.can_run():
            self.fn()


def as_action(a) -> Action:
    if isinstance(a, Action):
        return a
    a = tuple(a)
    return Action(str(a[0]), a[1], a[2] if len(a) > 2 else None)


def sentence(text: str) -> str:
    """``text`` with a final full stop when it has none ('Saved' -> 'Saved.'); a text that
    ends with '.', '?', '!' or '…' is kept."""
    t = str(text or "").strip()
    if t and not t.endswith((".", "?", "!", "…")):
        t += "."
    return t


def message_time() -> str:
    v = str(app_settings().value("ui/message_time", "normal") or "normal")
    return v if v in TIME_FACTOR else "normal"


def time_factor() -> float:
    """Settings > Appearance > Messages stay as a factor of a message's time (0.7 / 1 /
    1.6)."""
    return TIME_FACTOR[message_time()]


def scaled_ms(ms: int) -> int:
    """A status message's own time ``ms`` times Settings > Appearance > Messages stay (0 =
    it stays until the next message)."""
    ms = int(ms or 0)
    return int(ms * time_factor()) if ms > 0 else ms


class ScaledStatusBar(QtWidgets.QStatusBar):
    """The status bar of a window other than the editor's (the Simulate window, the manual):
    a message stays for its caller's time times Settings > Appearance > Messages stay, as
    in the editor window (status_items.StatusBar)."""

    def showMessage(self, text: str, timeout: int = 0) -> None:  # noqa: N802 -- Qt naming
        super().showMessage(str(text or ""), scaled_ms(timeout))


def reading_ms(text: str, level: str = "info", factor: Optional[float] = None) -> int:
    """How long a message stays in the status bar: 2.5 s and 280 ms a word, between 4 and
    15 s; a warning or an error at least 10 s; all of it times Settings > Appearance >
    Messages stay (0.7 / 1 / 1.6)."""
    words = len(str(text or "").split())
    ms = min(max(2500 + 280 * words, 4000), 15000)
    if level in ("warning", "error"):
        ms = max(ms, 10000)
    ms *= time_factor() if factor is None else factor
    return int(ms)


def message_ms(text: str, level: str = "info", at_least_ms: int = 0, scaled: bool = True) -> int:
    """How long a message stays."""
    ms = max(reading_ms(text, level, factor=1.0), int(at_least_ms or 0))
    return scaled_ms(ms) if scaled else ms


class Message:
    """One message of the list."""

    __slots__ = ("text", "level", "actions", "key", "detail", "when", "read", "count")

    def __init__(
        self,
        text: str,
        level: str = "info",
        actions: Iterable = (),
        key: Optional[str] = None,
        detail: str = "",
        when: Optional[float] = None,
    ):
        self.text = text
        self.level = level if level in LEVELS else "info"
        self.actions = tuple(as_action(a) for a in actions or ())
        self.key = key
        self.detail = detail or ""
        self.when = time.time() if when is None else when
        self.read = False
        self.count = 1

    def time_text(self) -> str:
        return time.strftime("%H:%M", time.localtime(self.when))

    def __repr__(self) -> str:
        return "Message(%r, %r)" % (self.level, self.text)


class MessageCentre(QtCore.QObject):
    """The messages of one editor window, oldest first (at most :data:`HISTORY_MAX`)."""

    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries: List[Message] = []

    def add(self, msg: Message) -> Message:
        if msg.key:
            old = [m for m in self.entries if m.key == msg.key]
            if old:
                # (counted only when it says the same again: another text starts at 1)
                if old[-1].text == msg.text:
                    msg.count = old[-1].count + 1
                self.entries = [m for m in self.entries if m.key != msg.key]
        elif (
            self.entries
            and self.entries[-1].key is None
            and self.entries[-1].text == msg.text
            and self.entries[-1].level == msg.level
        ):
            last = self.entries[-1]  # (the same message again: counted once)
            last.count += 1
            last.when, last.read, last.actions = msg.when, False, msg.actions or last.actions
            self.changed.emit()
            return last
        self.entries.append(msg)
        del self.entries[:-HISTORY_MAX]
        self.changed.emit()
        return msg

    def newest_first(self) -> List[Message]:
        return list(reversed(self.entries))

    def unread(self) -> int:
        """The unread warnings and errors (the bell's badge)."""
        return sum(1 for m in self.entries if not m.read and m.level in ("warning", "error"))

    def worst_unread(self) -> str:
        levels = {m.level for m in self.entries if not m.read}
        return "error" if "error" in levels else "warning" if "warning" in levels else ""

    def mark_read(self) -> None:
        if any(not m.read for m in self.entries):
            for m in self.entries:
                m.read = True
            self.changed.emit()

    def clear(self) -> None:
        self.entries = []
        self.changed.emit()


def editor_window(obj=None):
    """The editor window ``obj`` belongs to (a window, a dialog or a widget in one), else the
    active editor window, else the first; None when there is none."""
    o = obj
    seen = 0
    while o is not None and seen < 50:
        if hasattr(o, "doc_tabs") and hasattr(o, "statusBar"):
            return o
        try:
            o = o.parent() if not isinstance(o, QtWidgets.QWidget) else o.parentWidget()
        except RuntimeError:  # swallow-ok: a widget being destroyed
            o = None
        seen += 1
    try:
        from .main_window import MainWindow

        wins = MainWindow.editor_windows()
    except Exception:  # noqa: BLE001  # swallow-ok: no editor window module (a tool)
        return None
    act = QtWidgets.QApplication.activeWindow()
    for w in wins:
        if w is act:
            return w
    return wins[0] if wins else None


def centre(window) -> MessageCentre:
    """``window``'s message list (made on first use)."""
    c = window.__dict__.get("_ctg_messages")
    if c is None:
        c = window.__dict__["_ctg_messages"] = MessageCentre(window)
    return c


def toasts_on() -> bool:
    return bool(app_settings().value("ui/toasts", True, type=bool))


def notify(
    window,
    text: str,
    level: str = "info",
    actions=(),
    key: Optional[str] = None,
    toast: bool = False,
    detail: str = "",
) -> Message:
    """Tell the user something (see the module notes); ``detail`` = the message's tool tip
    and its second line in the list (a path, the parts of a summary). Returns the message."""
    level = level if level in LEVELS else "info"
    msg = Message(sentence(text), level, actions, key, detail)
    win = editor_window(window)
    if level in ("warning", "error"):
        from ..infra import logsetup

        logsetup.get("messages").info("%s: %s", level, msg.text)
    if win is None:
        return msg
    msg = centre(win).add(msg)
    ms = reading_ms(msg.text, level)
    try:
        sb = win.statusBar()
        show = getattr(sb, "show_message", None)
        if callable(show):
            show(msg.text, ms, level=level, actions=msg.actions, detail=msg.detail)
        else:
            sb.showMessage(msg.text, ms)
    except RuntimeError:  # swallow-ok: the window is being destroyed
        return msg
    if toast and toasts_on():
        from .toast import show_toast

        show_toast(win, msg.text, level, msg.actions, ms)
    return msg


def undo_action(window, doc=None, label: str = "Undo") -> Action:
    """An Undo button for the step just made on ``doc`` (default: the window's page): it runs
    Edit > Undo only while that step is still the newest of the page shown."""
    doc = doc if doc is not None else getattr(window, "doc", None)
    stack = getattr(doc, "_undo", None) or []
    step = stack[-1] if stack else None

    def ok() -> bool:
        cur = getattr(doc, "_undo", None) or []
        return step is not None and getattr(window, "doc", None) is doc and bool(cur) and cur[-1] is step

    def run() -> None:
        if ok():
            window.act_undo.trigger()

    return Action(label, run, ok)


def open_log() -> None:
    """Open the activity log (Help > Open Log Folder has the folder). A seam: tests replace
    it."""
    import os
    from ..infra import logsetup

    p = logsetup.log_path()
    target = p if os.path.isfile(p) else os.path.dirname(p)
    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))


def open_log_action() -> Action:
    return Action("Open Log", lambda: open_log())


# --------------------------------------------------------------------------- the level icons
#: (level, size, the level's colour, the mark's colour) -> its icon (a theme change makes new
#: keys; emptied at 64 and for the tests)
_ICONS: dict = {}
from ..infra import caches as _caches  # noqa: E402

_caches.register("notify.level_icons", _ICONS, on=("tests",))


def level_icon(level: str, size: int = 16) -> QtGui.QIcon:
    """The level's mark: an 'i' (information), a tick (done), a '!' in a triangle (warning),
    a cross (error), in the level's theme colour."""
    from .style import theme

    role = LEVEL_ROLE.get(level, "accent")
    key = (level, int(size), theme.COLORS.get(role), theme.COLORS.get("deep"))
    ic = _ICONS.get(key)
    if ic is not None:
        return ic
    if len(_ICONS) > 64:
        _ICONS.clear()
    dpr = 2.0
    n = max(8, int(size))
    pm = QtGui.QPixmap(int(n * dpr), int(n * dpr))
    pm.fill(QtCore.Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    fill = theme.color(role)
    mark = QtGui.QColor(theme.readable_on(fill.name(), [theme.COLORS["deep"], theme.COLORS["head"]], 3.0))
    s = n / 16.0
    p.scale(s, s)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(fill)
    if level == "warning":
        path = QtGui.QPainterPath()
        path.moveTo(8, 1.2)
        path.lineTo(15.2, 14.4)
        path.lineTo(0.8, 14.4)
        path.closeSubpath()
        p.drawPath(path)
    else:
        p.drawEllipse(QtCore.QRectF(1, 1, 14, 14))
    pen = QtGui.QPen(mark, 1.9)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    if level == "warning":
        p.drawLine(QtCore.QPointF(8, 5.6), QtCore.QPointF(8, 9.6))
        p.drawPoint(QtCore.QPointF(8, 12.2))
    elif level == "error":
        p.drawLine(QtCore.QPointF(5.4, 5.4), QtCore.QPointF(10.6, 10.6))
        p.drawLine(QtCore.QPointF(10.6, 5.4), QtCore.QPointF(5.4, 10.6))
    elif level == "success":
        p.drawPolyline(
            QtGui.QPolygonF([QtCore.QPointF(4.6, 8.3), QtCore.QPointF(7.1, 10.7), QtCore.QPointF(11.4, 5.6)])
        )
    else:
        p.drawPoint(QtCore.QPointF(8, 4.9))
        p.drawLine(QtCore.QPointF(8, 7.6), QtCore.QPointF(8, 11.3))
    p.end()
    ic = QtGui.QIcon(pm)
    _ICONS[key] = ic
    return ic


# --------------------------------------------------------------------------- popovers
def popover_qss(name: str) -> str:
    """The frame of a status bar popover (the messages, the background tasks), in the theme
    (written in the dark design colours: the stylesheet hook maps them)."""
    from .style.theme import DARK, type_qss

    return (
        "QFrame#%(n)s { background: %(bg)s; border: 1px solid %(edge)s; "
        "border-radius: 6px; }"
        "QFrame#%(n)s QLabel#PopoverTitle { %(title)s }"
        "QFrame#%(n)s QToolButton { border: 1px solid transparent; border-radius: 3px; "
        "padding: 2px 6px; color: %(accent)s; }"
        "QFrame#%(n)s QToolButton:hover { background: %(hover)s; border-color: %(edge)s; }"
        "QFrame#%(n)s QToolButton:disabled { color: %(dim)s; }"
        'QFrame#%(n)s QToolButton[kbdFocus="true"] { border: 2px solid %(accent)s; '
        "padding: 1px 5px; }"
        "QFrame#%(n)s QScrollArea { background: transparent; border: none; }"
        "QFrame#%(n)s QWidget#PopoverRows { background: transparent; }"
        % {
            "n": name,
            "bg": DARK["popup"],
            "edge": DARK["border"],
            "accent": DARK["accent"],
            "hover": DARK["hover"],
            "dim": DARK["text_dim"],
            "title": type_qss("body_strong"),
        }
    )


class StatusPopover(QtWidgets.QFrame):
    """A popup list over the status bar, anchored to its button (Esc or a click elsewhere
    closes it)."""

    WIDTH = 440
    MAX_HEIGHT = 420

    def __init__(self, parent, name: str, title: str):
        super().__init__(parent, QtCore.Qt.Popup)
        from .style.theme import px, sp

        self.setObjectName(name)
        self.setStyleSheet(popover_qss(name))
        self.setAccessibleName(title)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(sp(3), sp(2), sp(3), sp(3))
        lay.setSpacing(sp(2))
        head = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(title)
        self.title.setObjectName("PopoverTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.head = head
        lay.addLayout(head)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.rows = QtWidgets.QWidget()
        self.rows.setObjectName("PopoverRows")
        self.rows_lay = QtWidgets.QVBoxLayout(self.rows)
        self.rows_lay.setContentsMargins(0, 0, 0, 0)
        self.rows_lay.setSpacing(sp(1))
        self.scroll.setWidget(self.rows)
        lay.addWidget(self.scroll, 1)
        self.setFixedWidth(px(self.WIDTH))
        self._anchor = None

    def add_row(self, w: QtWidgets.QWidget) -> None:
        """A row of the list, shown now (a child added to a shown popup is shown only on the
        next turn of the event loop: its height would be missing from :meth:`fit`)."""
        self.rows_lay.addWidget(w)
        w.show()

    def clear_rows(self) -> None:
        while self.rows_lay.count():
            it = self.rows_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def fit(self) -> None:
        """As tall as the rows, at most MAX_HEIGHT (and the screen)."""
        from .style.theme import px

        self.rows_lay.invalidate()
        self.rows_lay.activate()
        rows_h = (
            self.rows_lay.heightForWidth(self.width() - px(36))
            if self.rows_lay.hasHeightForWidth()
            else self.rows_lay.sizeHint().height()
        )
        want = rows_h + self.title.sizeHint().height() + px(44)
        scr = self.screen().availableGeometry() if self.screen() is not None else None
        top = px(self.MAX_HEIGHT)
        if scr is not None:
            top = min(top, scr.height() - 60)
        self.setFixedHeight(max(px(90), min(want, top)))

    def open_at(self, anchor: QtWidgets.QWidget) -> None:
        """Show above ``anchor`` (a status bar button), right edges lined up, on the screen."""
        self._anchor = anchor
        self.fit()
        w, h = self.width(), self.height()
        if anchor is not None and anchor.isVisible():
            g = anchor.mapToGlobal(QtCore.QPoint(anchor.width(), 0))
            x, y = g.x() - w, g.y() - h - 4
        else:
            win = self.parentWidget()
            g = win.mapToGlobal(QtCore.QPoint(0, win.height()))
            x, y = g.x() + 12, g.y() - h - 30
        scr = QtGui.QGuiApplication.screenAt(QtCore.QPoint(x + w // 2, max(0, y)))
        if scr is not None:
            a = scr.availableGeometry()
            x = max(a.left() + 4, min(x, a.right() - w - 4))
            y = max(a.top() + 4, min(y, a.bottom() - h - 4))
        self.move(x, y)
        self.show()
        self.raise_()
        self.setFocus(QtCore.Qt.PopupFocusReason)

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:  # noqa: N802 -- Qt naming
        if e.key() == QtCore.Qt.Key_Escape:
            self.close()
            e.accept()
            return
        super().keyPressEvent(e)


def _action_button(parent, a: Action, after=None) -> QtWidgets.QToolButton:
    b = QtWidgets.QToolButton(parent)
    b.setText(a.label)
    b.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
    b.setEnabled(a.can_run())

    def run():
        a.run()
        if after is not None:
            after()

    b.clicked.connect(run)
    return b


class MessagesPopover(StatusPopover):
    """The bell's list: every message of the window, newest first, with its time, its level
    icon and its buttons; Clear All."""

    def __init__(self, window):
        super().__init__(window, "MessagesPopover", "Messages")
        self._win = window
        self.clear_btn = QtWidgets.QToolButton()
        self.clear_btn.setText("Clear All")
        self.clear_btn.setToolTip("Remove every message from the list")
        self.clear_btn.clicked.connect(self._clear)
        self.head.addWidget(self.clear_btn)
        centre(window).changed.connect(self._centre_changed)

    def _clear(self) -> None:
        centre(self._win).clear()

    def _centre_changed(self) -> None:
        if self.isVisible():
            self.rebuild()
            self.fit()

    def rebuild(self) -> None:
        from .style.theme import empty_state, set_note_role, sp

        self.clear_rows()
        msgs = centre(self._win).newest_first()
        self.clear_btn.setEnabled(bool(msgs))
        if not msgs:
            self.add_row(empty_state("bell", "No messages."))
            return
        for m in msgs:
            row = QtWidgets.QWidget(self.rows)
            row.setAccessibleName("%s: %s" % (LEVEL_NAME[m.level], m.text))
            hl = QtWidgets.QHBoxLayout(row)
            hl.setContentsMargins(sp(1), sp(1), sp(1), sp(1))
            hl.setSpacing(sp(2))
            ic = QtWidgets.QLabel(row)
            ic.setPixmap(level_icon(m.level, 16).pixmap(16, 16))
            ic.setToolTip(LEVEL_NAME[m.level])
            ic.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
            hl.addWidget(ic, 0, QtCore.Qt.AlignTop)
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(0)
            text = QtWidgets.QLabel(m.text, row)
            text.setWordWrap(True)
            text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            if m.level in ("warning", "error"):
                set_note_role(text, "warn" if m.level == "warning" else "danger")
            col.addWidget(text)
            meta = m.time_text() + ("  ·  %d times" % m.count if m.count > 1 else "")
            if m.detail:
                meta += "  ·  " + m.detail
            from .names import ElidedLabel

            sub = ElidedLabel(meta, row)  # (a path does not wrap: cut in the middle)
            sub.setToolTip(meta)
            set_note_role(sub, "text_dim")
            col.addWidget(sub)
            if m.actions:
                bl = QtWidgets.QHBoxLayout()
                bl.setContentsMargins(0, sp(1), 0, 0)
                for a in m.actions:
                    bl.addWidget(_action_button(row, a, after=self.close))
                bl.addStretch(1)
                col.addLayout(bl)
            hl.addLayout(col, 1)
            self.add_row(row)
        self.rows_lay.addStretch(1)

    def open_at(self, anchor) -> None:
        self.rebuild()
        super().open_at(anchor)
        centre(self._win).mark_read()


class BellButton(QtWidgets.QToolButton):
    """The status bar's bell: a badge with the unread warnings and errors; a click lists the
    messages (:class:`MessagesPopover`)."""

    def __init__(self, window):
        super().__init__(window)
        self._win = window
        self.setObjectName("StatusBell")
        self.setAutoRaise(True)
        self.setIcon(_theme_icon("bell"))
        self.setAccessibleName("Messages")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.clicked.connect(self.toggle_list)
        self.popover: Optional[MessagesPopover] = None
        centre(window).changed.connect(self._changed)
        self._changed()

    def _changed(self) -> None:
        n = centre(self._win).unread()
        total = len(centre(self._win).entries)
        if n:
            self.setToolTip(
                "Messages: %d new %s. Click to see them."
                % (n, "warning or error" if n == 1 else "warnings and errors")
            )
        else:
            self.setToolTip(
                "Messages (%d). Click to see them again." % total if total else "Messages. None yet."
            )
        self.setAccessibleDescription(self.toolTip())
        self.update()

    def toggle_list(self) -> None:
        if self.popover is not None and self.popover.isVisible():
            self.popover.close()
            return
        if self.popover is None:
            self.popover = MessagesPopover(self._win)
        self.popover.open_at(self)

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt naming
        s = super().sizeHint()
        return QtCore.QSize(s.width() + 6, s.height())

    def paintEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().paintEvent(e)
        c = centre(self._win)
        n = c.unread()
        if not n:
            return
        from .style import theme

        role = "danger" if c.worst_unread() == "error" else "warn"
        fill = theme.color(role)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        f = theme.font("caption")
        f.setBold(True)
        p.setFont(f)
        label = str(n) if n < 100 else "99+"
        fm = QtGui.QFontMetrics(f)
        h = max(fm.height(), theme.px(13))
        w = max(h, fm.horizontalAdvance(label) + theme.px(6))
        r = QtCore.QRectF(self.width() - w - 1, 1, w, h)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(r, h / 2.0, h / 2.0)
        p.setPen(
            QtGui.QColor(theme.readable_on(fill.name(), [theme.COLORS["deep"], theme.COLORS["head"]], 3.0))
        )
        p.drawText(r, QtCore.Qt.AlignCenter, label)
        p.end()


def _theme_icon(name: str) -> QtGui.QIcon:
    from .style.theme import icon

    return icon(name)


def show_messages(window) -> None:
    """View > Messages: the bell's list (at the bell, or over the window's corner when the
    status bar is hidden)."""
    bell = getattr(window, "status_bell", None)
    if bell is not None:
        bell.toggle_list()
        return
    pop = window.__dict__.get("_ctg_messages_popover")
    if pop is None:
        pop = window.__dict__["_ctg_messages_popover"] = MessagesPopover(window)
    pop.open_at(None)
