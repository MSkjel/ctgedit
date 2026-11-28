"""The History panel: undo steps as a list."""

from __future__ import annotations

from ..names import plural, step_title
from ..style.theme import sp as _sp  # (the spacing scale)

import time
import weakref
from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..style import theme
from ..style.theme import set_note_role

HELP_TOPIC = "the-window#history-panel"
#: the setting of the reveal after Undo / Redo
REVEAL_KEY = "ui/undo_reveal"
#: how long the objects an Undo / Redo brought back flash (ms): the default of Settings >
#: Canvas > Undo and Redo > Flash the changed objects for (ui/prefs/timing_options.UNDO_FLASH_MS)
FLASH_MS = 400

ROLE_TIME = QtCore.Qt.UserRole + 1
ROLE_STATE = QtCore.Qt.UserRole + 2


def _clock(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t)) if t else ""


class _StepDelegate(QtWidgets.QStyledItemDelegate):
    """A row: the step's name at the left, the 'Saved' badge and the time at the right; the
    current state bold, an undone step italic and dimmed, a line over the first undone one."""

    def sizeHint(self, option, index):  # noqa: N802 -- Qt naming
        fm = QtGui.QFontMetrics(theme.font("body"))
        return QtCore.QSize(120, fm.height() + 2 * theme.sp(2))

    def paint(self, p, option, index) -> None:
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        state = index.data(ROLE_STATE) or {}
        title, opt.text = opt.text, ""
        opt.icon = QtGui.QIcon()
        widget = opt.widget
        style = widget.style() if widget is not None else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, p, widget)
        p.save()
        r = opt.rect.adjusted(theme.sp(2), 0, -theme.sp(2), 0)
        selected = bool(opt.state & QtWidgets.QStyle.State_Selected)
        dim = theme.color("sel_text_dim" if selected else "text_dim")
        if state.get("first_undone"):
            p.setPen(QtGui.QPen(theme.color("control_edge"), 1))
            p.drawLine(opt.rect.left(), opt.rect.top(), opt.rect.right(), opt.rect.top())
        # the right side: the time, then the badge before it
        small = theme.font("caption")
        p.setFont(small)
        fm_small = QtGui.QFontMetrics(small)
        right = r.right()
        when = index.data(ROLE_TIME) or ""
        if when:
            w = fm_small.horizontalAdvance(when)
            p.setPen(dim)
            p.drawText(
                QtCore.QRect(right - w, r.top(), w, r.height()),
                QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight,
                when,
            )
            right -= w + theme.sp(3)
        if state.get("saved"):
            label = "Saved"
            w = fm_small.horizontalAdvance(label)
            p.setPen(dim)
            p.drawText(
                QtCore.QRect(right - w, r.top(), w, r.height()),
                QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight,
                label,
            )
            right -= w + theme.sp(1)
            s = min(14, r.height() - 4)
            ic = theme.icon("save").pixmap(
                QtCore.QSize(s, s), widget.devicePixelRatioF() if widget is not None else 1.0
            )
            p.drawPixmap(QtCore.QRect(right - s, r.center().y() - s // 2 + 1, s, s), ic)
            right -= s + theme.sp(3)
        font = theme.font("body")
        font.setBold(bool(state.get("current")))
        font.setItalic(bool(state.get("undone")))
        p.setFont(font)
        fm = QtGui.QFontMetrics(font)
        if state.get("undone"):
            p.setPen(dim)
        else:
            p.setPen(theme.color("sel_text" if selected else "text"))
        text_r = QtCore.QRect(r.left(), r.top(), max(10, right - r.left()), r.height())
        p.drawText(
            text_r,
            QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
            fm.elidedText(title, QtCore.Qt.ElideRight, text_r.width()),
        )
        p.restore()


class _StepList(QtWidgets.QListWidget):
    """The steps: Up / Down move the highlight only, Enter (or a click) goes to the step."""

    stepChosen = QtCore.Signal(int)

    def keyPressEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and self.currentRow() >= 0:
            self.stepChosen.emit(self.currentRow())
            e.accept()
            return
        super().keyPressEvent(e)


class HistoryDock(QtWidgets.QWidget):
    """The list of a document's undo steps (``show_document``); ``goToRequested(k)`` asks
    the window to take the document to the state after ``k`` steps, ``selectRequested(k)``
    to select the objects of step ``k``."""

    goToRequested = QtCore.Signal(int)
    selectRequested = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))
        self.note = QtWidgets.QLabel("")
        self.note.setWordWrap(True)
        set_note_role(self.note, "text_dim")
        lay.addWidget(self.note)
        self.list = _StepList(self)
        self.list.setAccessibleName("History")
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.list.setUniformItemSizes(True)
        self.list.setItemDelegate(_StepDelegate(self.list))
        self.list.setToolTip(
            "Click a step to take the page to the state after it (the later "
            "steps are undone; Redo brings them back)"
        )
        self.list.itemClicked.connect(self._clicked)
        self.list.stepChosen.connect(self.goToRequested.emit)
        self.list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list, 1)
        foot = QtWidgets.QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(_sp(0))
        self.undo_btn = QtWidgets.QToolButton(self)
        self.redo_btn = QtWidgets.QToolButton(self)
        for b in (self.undo_btn, self.redo_btn):
            b.setAutoRaise(True)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
            foot.addWidget(b)
        foot.addStretch(1)
        lay.addLayout(foot)
        self._key = None
        self._doc = None
        self._steps: list = []  # the step of each row (None: the opened state)
        try:
            from ..help import set_help

            set_help(self, HELP_TOPIC)
        except Exception:  # noqa: BLE001  # swallow-ok: the help link is an aid
            pass

    def set_actions(self, undo: QtGui.QAction, redo: QtGui.QAction) -> None:
        """The footer's buttons are the Edit menu's Undo and Redo (no copies)."""
        self.undo_btn.setDefaultAction(undo)
        self.redo_btn.setDefaultAction(redo)

    # -- the rows ----------------------------------------------------------------------
    def show_document(self, doc) -> None:
        """Rebuild the list for ``doc`` (None: no page) when its history changed."""
        undo = list(getattr(doc, "_undo", ()) or ()) if doc is not None else []
        redo = list(getattr(doc, "_redo", ()) or ()) if doc is not None else []
        trimmed = int(getattr(doc, "undo_trimmed", 0) or 0) if doc is not None else 0
        saved = getattr(doc, "saved_depth", None) if doc is not None else None
        other = bool(getattr(doc, "has_unstepped_changes", False)) if doc is not None else False
        key = (
            id(doc),
            tuple(id(c) for c in undo),
            tuple(id(c) for c in redo),
            trimmed,
            saved,
            other,
            tuple(c.label for c in undo[-1:]),
        )
        if key == self._key:
            return
        self._key = key
        self._doc = doc
        self.list.clear()
        self._steps = []
        if doc is None:
            self.note.setText("No page open.")
            return
        saved_row = saved - trimmed if saved is not None else None
        first = (
            "Opened (%d earlier step%s dropped)" % (trimmed, "" if trimmed == 1 else "s")
            if trimmed
            else "Opened"
        )
        opened = float(getattr(getattr(doc, "ui", None), "opened_at", 0.0) or 0.0)
        rows = [(first, None, opened if not trimmed else 0.0)]
        rows += [
            (step_title(getattr(c, "label", "")), c, float(getattr(c, "when", 0.0) or 0.0)) for c in undo
        ]
        current = len(rows) - 1
        rows += [
            (step_title(getattr(c, "label", "")), c, float(getattr(c, "when", 0.0) or 0.0))
            for c in reversed(redo)
        ]
        for k, (title, cmd, when) in enumerate(rows):
            it = QtWidgets.QListWidgetItem(title)
            state = {
                "current": k == current,
                "undone": k > current,
                "first_undone": k == current + 1,
                "saved": k == saved_row,
            }
            it.setData(ROLE_STATE, state)
            it.setData(ROLE_TIME, _clock(when))
            tips = []
            if when:
                tips.append(("Opened at %s" if cmd is None else "Made at %s") % _clock(when))
            if state["undone"]:
                tips.append("Undone: click to do it again")
            if state["saved"]:
                tips.append("The page was saved in this state")
            it.setToolTip(". ".join(tips))
            it.setData(
                QtCore.Qt.AccessibleTextRole,
                ", ".join(
                    [title, _clock(when)]
                    + (["saved"] if state["saved"] else [])
                    + (["current"] if state["current"] else [])
                    + (["undone"] if state["undone"] else [])
                ).strip(", "),
            )
            self.list.addItem(it)
            self._steps.append(cmd)
        cur = self.list.item(current)
        self.list.setCurrentRow(current)
        self.list.scrollToItem(cur)
        self.note.setText(self._note_text(len(undo), len(redo), saved_row, other))

    @staticmethod
    def _note_text(n: int, can_redo: int, saved_row: Optional[int], other: bool) -> str:
        if not n and not can_redo:
            text = "No edits yet."
        else:
            text = "%s." % (plural(n, "step"))
            if can_redo:
                text += " %d can be redone." % can_redo
            if saved_row is None:
                text += " The saved state is no longer in the history."
            elif saved_row:
                text += " Saved at step %d." % saved_row
            else:
                text += " Saved before the first step."
        if other:
            text += " Some unsaved changes are not undo steps (undoing keeps them)."
        return text

    def step_of(self, row: int):
        """The undo step of ``row`` (None: the opened state, or no such row)."""
        return self._steps[row] if 0 <= row < len(self._steps) else None

    def rows_text(self) -> str:
        """Every row as tab-separated text: step number, name, time, state."""
        out = []
        for k in range(self.list.count()):
            it = self.list.item(k)
            st = it.data(ROLE_STATE) or {}
            notes = [
                w
                for w, on in (
                    ("current", st.get("current")),
                    ("saved", st.get("saved")),
                    ("undone", st.get("undone")),
                )
                if on
            ]
            out.append("\t".join((str(k), it.text(), it.data(ROLE_TIME) or "", ", ".join(notes))))
        return "\n".join(out)

    # -- input -------------------------------------------------------------------------
    def _clicked(self, item) -> None:
        self.goToRequested.emit(self.list.row(item))

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        row = self.list.row(item) if item is not None else -1
        m = QtWidgets.QMenu(self)
        go = m.addAction("Go to This Step")
        go.setObjectName("history.go_to_step")
        go.setEnabled(row >= 0)
        go.triggered.connect(lambda: self.goToRequested.emit(row))
        sel = m.addAction("Select Objects of This Step")
        sel.setObjectName("history.select_step")
        step = self.step_of(row)
        sel.setEnabled(bool(step is not None and _step_records(step)))
        sel.triggered.connect(lambda: self.selectRequested.emit(row))
        m.addSeparator()
        cp = m.addAction("Copy Steps")
        cp.setObjectName("history.copy_steps")
        cp.setEnabled(self.list.count() > 0)
        cp.triggered.connect(self.copy_steps)
        m.exec(self.list.viewport().mapToGlobal(pos))

    def copy_steps(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.rows_text())

    def current_row(self) -> Optional[int]:
        return self.list.currentRow() if self.list.count() else None


def _step_records(cmd, after: Optional[bool] = None) -> tuple:
    """The records a step was about: before it (``after`` False), after it (True), or both
    (None)."""
    t = getattr(cmd, "targets", None)
    if not t:
        return ()
    before, later = t[0] or (), t[1] or ()
    if after is None:
        return tuple(before) + tuple(r for r in later if all(r is not b for b in before))
    return tuple(later if after else before)


def undo_reveal_on() -> bool:
    from ..prefs.settings import app_settings

    try:
        return bool(app_settings().value(REVEAL_KEY, True, type=bool))
    except Exception:  # noqa: BLE001  # swallow-ok: a garbled setting reads as its default
        return True


# --------------------------------------------------------------------------- the canvas flash
#: canvas -> (boxes in page coordinates, until when) of the objects flashing
_FLASH: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _paint_flash(view, painter, exposed) -> None:
    hit = _FLASH.get(view)
    if not hit:
        return
    boxes, until = hit
    if time.monotonic() > until:
        return
    from ..editor.canvas_look import mark_width  # (Settings > Selection outlines widens it)

    pen = QtGui.QPen(theme.color("accent"), mark_width(2.0))
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    for b in boxes:
        r = QtCore.QRectF(b[0], b[1], max(1.0, b[2] - b[0]), max(1.0, b[3] - b[1]))
        if exposed.intersects(r.adjusted(-4, -4, 4, 4)):
            painter.drawRect(r.adjusted(-2, -2, 2, 2))


def end_flash(view) -> bool:
    """The objects an Undo / Redo brought back stop flashing on ``view`` now (editor chrome:
    tools/session_soak grabs the page without it). True when one was flashing."""
    if _FLASH.pop(view, None) is None:
        return False
    view.viewport().update()
    return True


def _install_overlay() -> None:
    from ..editor import canvas_hooks

    if "history_flash" not in {o.name for o in canvas_hooks.overlays("foreground")}:
        canvas_hooks.register_overlay("history_flash", _paint_flash, "foreground", order=50)


class HistoryCommands:
    """The editor window's side of the undo history (a MainWindow mixin): the Edit menu's
    step names, each step's objects, and what Undo / Redo show."""

    def _install_history_ui(self) -> None:
        """After the docks and the actions: the panel's buttons, its signals, the flash."""
        hd = self.history_dock
        hd.set_actions(self.act_undo, self.act_redo)
        hd.selectRequested.connect(self.select_history_step)
        _install_overlay()
        try:
            from .. import help as _help

            _help.set_help(self.dock_history, HELP_TOPIC)
        except Exception:  # noqa: BLE001  # swallow-ok: the help link is an aid
            pass

    # -- the Edit menu and the steps' objects ----------------------------------------------
    def _history_update_actions(self) -> None:
        """_update_actions: Undo / Redo name their step (Title Case: 'Undo Move'), and a step
        just made learns its objects (the selection before it and after it)."""
        doc = self.doc
        has = doc is not None
        u = doc._undo[-1].label if has and doc.can_undo() else ""
        r = doc._redo[-1].label if has and doc.can_redo() else ""
        self.act_undo.setText("Undo %s" % step_title(u) if u else "Undo")
        self.act_redo.setText("Redo %s" % step_title(r) if r else "Redo")
        if not has:
            self._hist_sel = None
            return
        recs = self._selection_records()
        objs = doc.parsed.page.objects
        top = doc._undo[-1] if doc._undo else None
        memo = self.__dict__.get("_hist_sel")
        if memo is not None and memo[0] is not doc:
            memo = None
        if top is not None and getattr(top, "targets", "") is None:
            before = memo[1] if memo is not None else ()
            after = recs
            if memo is not None and memo[4] == len(objs):
                # no object added or removed: the units the step changed, when the page
                # says which (a window of an object not selected, a move); a long run of
                # them is a change of the stacking order: the selection says it better
                try:
                    ch = doc.model_changes_since(memo[2], memo[3])
                except Exception:  # noqa: BLE001  # swallow-ok: the selection says then
                    ch = None
                units = {doc.root_of(i) for i in self._sel if 0 <= i < len(objs)}
                if ch and len(ch) <= max(8, 4 * len(units)):
                    changed = tuple(objs[r] for r in sorted(ch) if r < len(objs))
                    # (a selected record the step replaced by an edited copy is gone now:
                    # Undo brings it back)
                    alive = {id(po) for po in objs}
                    before = changed + tuple(r for r in before if id(r) not in alive)
                    after = changed
                    top.targets = (before, after, tuple(sorted(r for r in ch if r < len(objs))))
            if top.targets is None:
                top.targets = (before, after)
        self._hist_sel = (
            doc,
            recs,
            getattr(doc, "derive_serial", 0),
            getattr(doc, "touch_serial", 0),
            len(objs),
        )

    def _selection_records(self) -> tuple:
        objs = self.doc.parsed.page.objects if self.doc is not None else []
        return tuple(objs[i] for i in self._sel if 0 <= i < len(objs))

    def _history_message(self, verb: str, label: str) -> str:
        """'Undid Align.' (the status bar after Undo / Redo)."""
        return (
            "%s %s." % (verb, step_title(label))
            if label
            else "%s." % ("Undone" if verb == "Undid" else "Redone")
        )

    def _record_indices(self, records) -> List[int]:
        """The indices of ``records`` on the page now (the ones gone are left out)."""
        if not records or self.doc is None:
            return []
        want = {id(r) for r in records}
        return [i for i, po in enumerate(self.doc.parsed.page.objects) if id(po) in want]

    # -- after Undo / Redo / a History click -------------------------------------------
    def _reveal_history_targets(self) -> None:
        """Select what the steps just undone or redone changed, bring it into view when
        none of it shows, and flash it (Settings > Canvas: ui/undo_reveal)."""
        doc = self.doc
        if doc is None:
            return
        take = getattr(doc, "take_history_moves", None)
        moves = take() if take is not None else []
        if not moves or not undo_reveal_on():
            return
        records: list = []
        roots: list = []
        for redone, cmd in moves:
            got = _step_records(cmd, after=redone)
            if not self._record_indices(got):
                got = _step_records(cmd, after=not redone)
            if not self._record_indices(got):
                # (records the step replaces by edited copies: the units at their places)
                t = getattr(cmd, "targets", None) or ()
                roots.extend(t[2] if len(t) > 2 else ())
            records.extend(got)
        n = len(doc.parsed.page.objects)
        extra = [doc.parsed.page.objects[r] for r in roots if 0 <= r < n]
        self._show_records(records + extra, quiet=True)

    def select_history_step(self, row: int) -> None:
        """The History panel's Select Objects of This Step: the step's objects that are on
        the page now, selected and brought into view."""
        step = self.history_dock.step_of(row)
        if step is None or self.doc is None:
            return
        if not self._show_records(_step_records(step)):
            self.statusBar().showMessage(
                "The objects of %s are not on the page now." % step_title(step.label), 5000
            )

    def _show_records(self, records, quiet: bool = False) -> bool:
        idx = self._record_indices(records)
        if not idx:
            return False
        objs = self.doc.page.objects
        # a member selected with its unit root counts once (the canvas selects units)
        self.view.set_selection(idx)
        self._on_canvas_selection(idx)
        boxes = []
        roots = sorted({self.doc.root_of(i) for i in idx if 0 <= i < len(objs)})
        for r in roots[:500]:
            b = self.doc.selection_extent([r], bounds=True)
            if b is not None:
                boxes.append(b)
        if not boxes:
            return True
        view = self.view
        seen = view.mapToScene(view.viewport().rect()).boundingRect()
        if not any(
            seen.intersects(QtCore.QRectF(b[0], b[1], max(1, b[2] - b[0]), max(1, b[3] - b[1])))
            for b in boxes
        ):
            u = (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
            view.ensureVisible(QtCore.QRectF(u[0], u[1], max(1, u[2] - u[0]), max(1, u[3] - u[1])), 40, 40)
        from ..prefs import timing_options

        flash_ms = timing_options.UNDO_FLASH_MS
        _FLASH[view] = (boxes, time.monotonic() + flash_ms / 1000.0)
        view.viewport().update()
        QtCore.QTimer.singleShot(flash_ms + 20, self, self._end_history_flash)
        return True

    def _end_history_flash(self) -> None:
        view = getattr(self, "view", None)
        if view is not None:
            end_flash(view)
