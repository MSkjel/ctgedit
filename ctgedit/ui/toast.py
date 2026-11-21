"""Toasts and busy notes over the page."""

from __future__ import annotations

import time
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets


def _toast_qss() -> str:
    from .style.theme import DARK, type_qss

    # (rgba(34, 38, 44, …) is the overlay role: the theme maps it, keeping the alpha)
    return (
        "QFrame#PageToast { background: rgba(34, 38, 44, 0.96); border: 1px solid %(e)s; "
        "border-radius: 8px; }"
        "QFrame#PageToast QLabel { background: transparent; %(body)s }"
        "QFrame#PageToast QToolButton { color: %(a)s; border: 1px solid transparent; "
        "border-radius: 3px; padding: 2px 8px; background: transparent; }"
        "QFrame#PageToast QToolButton:hover { border-color: %(e)s; background: %(h)s; }"
        "QFrame#PageToast QToolButton:disabled { color: %(d)s; }"
        'QFrame#PageToast QToolButton[kbdFocus="true"] { border: 2px solid %(a)s; '
        "padding: 1px 7px; }"
        % {
            "e": DARK["border"],
            "a": DARK["accent"],
            "h": DARK["hover"],
            "d": DARK["text_dim"],
            "body": type_qss("body"),
        }
    )


class Toast(QtWidgets.QFrame):
    """The result of a command over the page (see the module notes)."""

    MAX_W = 560
    GAP = 10

    def __init__(self, window):
        super().__init__(window.view.viewport())
        from .style.theme import sp

        self._win = window
        self.setObjectName("PageToast")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet(_toast_qss())
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(sp(3), sp(2), sp(1), sp(2))
        lay.setSpacing(sp(2))
        self.icon = QtWidgets.QLabel(self)
        self.icon.setFixedSize(16, 16)
        lay.addWidget(self.icon, 0, QtCore.Qt.AlignVCenter)
        self.label = QtWidgets.QLabel(self)
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self._btn_lay = QtWidgets.QHBoxLayout()
        self._btn_lay.setSpacing(sp(1))
        lay.addLayout(self._btn_lay)
        self.close_btn = QtWidgets.QToolButton(self)
        self.close_btn.setIcon(_icon("close"))
        self.close_btn.setAccessibleName("Close")
        self.close_btn.setToolTip("Close (Esc)")
        self.close_btn.clicked.connect(self.dismiss)
        lay.addWidget(self.close_btn, 0, QtCore.Qt.AlignVCenter)
        self.buttons = []
        self.actions_ = ()
        self.level = "info"
        #: the page the toast tells about (another tab shown: it goes)
        self.doc = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._until = 0.0
        self._held = False
        self._filtering = False
        window.view.viewport().installEventFilter(self)
        self.hide()

    # -- showing ---------------------------------------------------------------------------
    def show_message(self, text: str, level: str = "info", actions=(), ms: int = 6000) -> None:
        from .notify import LEVEL_NAME, level_icon

        self.level = level
        self.doc = getattr(self._win, "doc", None)
        self.label.setText(text)
        self.icon.setPixmap(level_icon(level, 16).pixmap(16, 16))
        self.setAccessibleName("%s: %s" % (LEVEL_NAME.get(level, ""), text))
        for b in self.buttons:
            b.hide()
            b.deleteLater()
        self.buttons = []
        self.actions_ = tuple(actions or ())
        for a in self.actions_:
            b = QtWidgets.QToolButton(self)
            b.setText(a.label)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            b.setEnabled(a.can_run())
            b.clicked.connect(lambda _=False, a=a: self._run(a))
            self._btn_lay.addWidget(b)
            self.buttons.append(b)
        self.place()
        self.show()
        self.raise_()
        self._until = time.monotonic() + ms / 1000.0
        self._timer.start(int(ms))
        self._watch(True)

    def _run(self, a) -> None:
        a.run()
        self.dismiss()

    def sync_actions(self) -> None:
        """An Undo whose step is no longer the newest is greyed out; the toast of a page
        whose tab is no longer shown goes."""
        if self.isHidden():
            return
        if getattr(self._win, "doc", None) is not self.doc:
            self.dismiss()
            return
        for b, a in zip(self.buttons, self.actions_):
            b.setEnabled(a.can_run())

    def dismiss(self) -> None:
        self._timer.stop()
        self._watch(False)
        self.hide()

    def place(self) -> None:
        """Centred over the canvas, above the navigation bar."""
        from .style.theme import px

        vp = self.parentWidget()
        if vp is None:
            return
        w = min(px(self.MAX_W), max(px(240), vp.width() - 2 * px(24)))
        # (as narrow as the text needs, at most MAX_W; as tall as the wrapped text)
        want = self.sizeHint().width()
        w = max(min(w, want), min(w, px(280)))
        self.setFixedWidth(w)
        lay = self.layout()
        h = lay.heightForWidth(w) if lay is not None and lay.hasHeightForWidth() else self.sizeHint().height()
        h = max(h, self.minimumSizeHint().height())
        self.setFixedHeight(h)
        bottom = vp.height() - px(16)
        nb = getattr(self._win, "navbar", None)
        try:
            if nb is not None and nb.isVisible() and nb.parentWidget() is vp:
                bottom = nb.geometry().top() - px(self.GAP)
        except RuntimeError:  # swallow-ok: the navigation bar is being destroyed
            pass
        self.move(max(0, (vp.width() - w) // 2), max(0, bottom - h))

    # -- hover, Esc, a click elsewhere ------------------------------------------------------
    def enterEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().enterEvent(e)
        self._held = True
        self._timer.stop()

    def leaveEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().leaveEvent(e)
        self._held = False
        if not self.isHidden():
            left = max(1500, int((self._until - time.monotonic()) * 1000))
            self._until = time.monotonic() + left / 1000.0
            self._timer.start(left)

    def _watch(self, on: bool) -> None:
        _app_filter(self, on)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        t = ev.type()
        try:
            vp = self.parentWidget()
        except RuntimeError:  # swallow-ok: being destroyed
            return False
        if obj is vp and t == QtCore.QEvent.Resize and not self.isHidden():
            self.place()
            return False
        if not self._filtering or self.isHidden():
            return False
        if t == QtCore.QEvent.KeyPress and ev.key() == QtCore.Qt.Key_Escape:
            self.dismiss()
            return False
        if t == QtCore.QEvent.MouseButtonPress and isinstance(obj, QtWidgets.QWidget):
            try:
                inside = obj is self or self.isAncestorOf(obj)
            except RuntimeError:  # swallow-ok: a widget being destroyed
                inside = True
            if not inside:
                self.dismiss()
        return False


def _icon(name: str) -> QtGui.QIcon:
    from .style.theme import icon

    return icon(name)


def toast_for(window) -> Toast:
    t = window.__dict__.get("_ctg_toast")
    if t is None:
        t = window.__dict__["_ctg_toast"] = Toast(window)
    return t


def show_toast(window, text: str, level: str = "info", actions=(), ms: int = 6000):
    """Show ``text`` over ``window``'s page (no page shown: nothing). Returns the toast."""
    view = getattr(window, "view", None)
    if view is None or getattr(window, "doc", None) is None:
        return None
    try:
        t = toast_for(window)
        t.show_message(text, level, actions, ms)
    except RuntimeError:  # swallow-ok: the window is being destroyed
        return None
    return t


# --------------------------------------------------------------------------- the busy note
class BusyNote(QtWidgets.QWidget):
    """'Opening the genie's parameters…' over the canvas while a step blocks the window (see
    the module notes). :meth:`show_over` paints it at once (``repaint``); the step's end hides
    it, and so does a dialog or a menu the step opens (the step waits for the user then, the
    window is not busy)."""

    def __init__(self, window):
        super().__init__(
            window, QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setObjectName("BusyNote")
        self._win = window
        self.text = ""
        self.shown_count = 0
        #: the step's watch (told when a dialog hides the note: it looks again later)
        self.watch = None
        self._filtering = False

    def show_over(self, text: str) -> None:
        from .style.theme import font, px

        self.text = str(text or "Working…")
        fm = QtGui.QFontMetrics(font("body"))
        w = fm.horizontalAdvance(self.text) + px(16 + 16 + 12 + 18)
        h = max(fm.height(), px(18)) + px(20)
        self.resize(w, h)
        view = getattr(self._win, "view", None)
        try:
            host = view.viewport() if view is not None and view.isVisible() else self._win
            c = host.mapToGlobal(host.rect().center())
        except RuntimeError:  # swallow-ok: the window is being destroyed
            return
        self.move(c.x() - w // 2, c.y() - h // 2)
        self.setAccessibleName(self.text)
        self.show()
        self.raise_()
        self.repaint()
        self.shown_count += 1
        self._watch_dialogs(True)

    def hideEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        self._watch_dialogs(False)
        super().hideEvent(e)

    def _watch_dialogs(self, on: bool) -> None:
        _app_filter(self, on)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        if (
            ev.type() == QtCore.QEvent.Show
            and obj is not self
            and isinstance(obj, QtWidgets.QWidget)
            and _waits_for_user(obj)
        ):
            self.hide()  # (a box of the step: it waits for the user)
            w = self.watch
            if w is not None:
                w.look_again()
        return False

    def paintEvent(self, _e) -> None:  # noqa: N802 -- Qt naming
        from .style import theme

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QtGui.QPen(theme.color("border"), 1))
        p.setBrush(theme.color("popup"))
        p.drawRoundedRect(r, theme.px(8), theme.px(8))
        n = theme.px(16)
        x = theme.px(16)
        y = (self.height() - n) / 2.0
        ic = theme.icon("sync").pixmap(QtCore.QSize(n, n), self.devicePixelRatioF() or 1.0)
        p.drawPixmap(QtCore.QRectF(x, y, n, n), ic, QtCore.QRectF(ic.rect()))
        p.setPen(theme.color("text"))
        p.setFont(theme.font("body"))
        p.drawText(
            QtCore.QRectF(x + n + theme.px(10), 0, self.width(), self.height()),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            self.text,
        )
        p.end()


def _waits_for_user(w: QtWidgets.QWidget) -> bool:
    """``w`` is a window that waits for the user: a modal dialog or a menu."""
    try:
        return w.isWindow() and (w.isModal() or w.windowType() == QtCore.Qt.Popup)
    except RuntimeError:  # swallow-ok: a widget being destroyed
        return False


def busy_note(window) -> BusyNote:
    n = window.__dict__.get("_ctg_busy_note")
    if n is None:
        n = window.__dict__["_ctg_busy_note"] = BusyNote(window)
    return n


class BusyWatch:
    """Show the busy note once a GUI-thread step runs longer than ``after_ms``."""

    TOOL = 4
    after_ms = 400
    #: ms between looks while a dialog of the step is open
    RECHECK_MS = 250

    def __init__(self, window, text: str, after_ms: Optional[int] = None):
        import threading

        self._win = window
        self._text = text
        self._main = threading.get_ident()
        self._done = threading.Event()
        self._wake = threading.Event()
        self._armed = False
        self.shown = False
        # (a dialog or a menu open when the step began is where the step runs, not a wait)
        self._modal0 = QtWidgets.QApplication.activeModalWidget()
        self._popup0 = QtWidgets.QApplication.activePopupWidget()
        if after_ms is not None:
            self.after_ms = after_ms
        self._mon = getattr(__import__("sys"), "monitoring", None)
        self._thread = None
        if self._mon is None:
            return
        try:
            self._mon.use_tool_id(self.TOOL, "ctgedit busy note")
        except ValueError:  # swallow-ok: the slot is taken (another watch, a tool)
            self._mon = None
            return
        self._thread = threading.Thread(target=self._wait, name="ctgedit-busy", daemon=True)
        self._thread.start()

    def _wait(self) -> None:
        from ..infra import gcguard

        gcguard.before_worker()
        delay = self.after_ms / 1000.0
        while not self._done.wait(delay):
            self._wake.clear()
            if not self._arm():
                return
            self._wake.wait()  # (a dialog of the step was open: look again)
            delay = self.RECHECK_MS / 1000.0

    def _arm(self) -> bool:
        mon = self._mon
        try:
            ev = mon.events
            mon.register_callback(self.TOOL, ev.PY_START, self._fire)
            mon.register_callback(self.TOOL, ev.CALL, self._fire)
            mon.set_events(self.TOOL, ev.PY_START | ev.CALL)
            self._armed = True
        except Exception:  # noqa: BLE001  # swallow-ok: no note then
            return False
        return True

    def look_again(self) -> None:
        """A dialog or a menu of the step opened: look again in a moment."""
        self._wake.set()

    def _waiting_for_user(self) -> bool:
        m = QtWidgets.QApplication.activeModalWidget()
        p = QtWidgets.QApplication.activePopupWidget()
        return (m is not None and m is not self._modal0) or (p is not None and p is not self._popup0)

    def _fire(self, *_args):
        import threading

        if threading.get_ident() != self._main or self._done.is_set():
            return None
        self._disarm()
        try:
            if self._waiting_for_user():
                self.look_again()  # (the step waits for the user in a box)
                return None
            note = busy_note(self._win)
            note.watch = self
            note.show_over(self._text)
            self.shown = True
        except Exception:  # noqa: BLE001 -- a note only
            from ..infra.logsetup import note_exception

            note_exception("toast.busy_note")
        return None

    def _disarm(self) -> None:
        mon = self._mon
        if mon is None or not self._armed:
            return
        self._armed = False
        try:
            mon.set_events(self.TOOL, 0)
            ev = mon.events
            mon.register_callback(self.TOOL, ev.PY_START, None)
            mon.register_callback(self.TOOL, ev.CALL, None)
        except Exception:  # noqa: BLE001  # swallow-ok: released below
            pass

    def finish(self) -> bool:
        """The step ended: the note goes. True when it was shown."""
        self._done.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(1.0)
        self._disarm()
        if self._mon is not None:
            try:
                self._mon.free_tool_id(self.TOOL)
            except Exception:  # noqa: BLE001  # swallow-ok: already free
                pass
        n = self._win.__dict__.get("_ctg_busy_note")
        if n is not None and n.watch is self:
            n.watch = None
        if self.shown and n is not None:
            n.hide()
        return self.shown


def _app_filter(w, on: bool) -> None:
    """Install / remove ``w`` as the application's event filter (once: ``w._filtering``)."""
    app = QtWidgets.QApplication.instance()
    if app is None or on == w._filtering:
        return
    w._filtering = on
    if on:
        app.installEventFilter(w)
    else:
        app.removeEventFilter(w)
