"""Composite genie dialogs: Presentation Options and Paste Composite Genie."""

from __future__ import annotations

import html as _html
import os
from typing import Callable, Dict, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from ...format import composites as C
from ..style.theme import TextMatch, set_note_role

_SPIN_MIN, _SPIN_MAX = C.INT_MIN, C.INT_MAX


class TypedParamsForm(QtWidgets.QWidget):
    """The typed parameter rows of a visual template."""

    changed = QtCore.Signal()
    currentChanged = QtCore.Signal(str)  # the parameter the description follows

    def __init__(
        self,
        params,
        current: Optional[dict] = None,
        parent=None,
        read_only: bool = False,
        *,
        completions: Optional[dict] = None,
        advice: Optional[Callable] = None,
        project_dir: str = "",
    ):
        super().__init__(parent)
        self._project_dir = project_dir or ""
        self._params = list(params)
        self._tpl = C.CompositeTemplate(guid="", name="", path="", params=tuple(self._params))
        self._read_only = read_only
        self._advice = advice
        self._typed: Dict[str, object] = C.typed_values(self._tpl, current)
        #: the values as loaded (what has_pending_edits / reload compare with)
        self._typed0: Dict[str, object] = dict(self._typed)
        self._search = TextMatch()
        self._only_invalid = False
        self._past_invalid: set = set()
        self._current = ""
        self._rows: Dict[str, dict] = {}
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self._form = form
        completions = completions or {}
        for p in self._params:
            # a buddy label reads '&' as a mnemonic: show a template's '&' as is
            label = QtWidgets.QLabel(p.label.replace("&", "&&") + ":")
            w = self._make_widget(p, completions)
            holder = getattr(w, "_ctg_holder", None) or w
            err = QtWidgets.QLabel()
            err.setWordWrap(True)
            set_note_role(err, "danger")
            err.hide()
            note = QtWidgets.QLabel()
            note.setWordWrap(True)
            set_note_role(note, "text_dim")
            note.hide()
            box = QtWidgets.QWidget()
            bl = QtWidgets.QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(1)
            bl.addWidget(holder)
            bl.addWidget(err)
            bl.addWidget(note)
            if p.description:
                w.setToolTip(p.description)
                label.setToolTip(p.description)
            label.setBuddy(w)
            form.addRow(label, box)
            self._rows[p.name.lower()] = {
                "param": p,
                "label": label,
                "widget": w,
                "error": err,
                "note": note,
                "box": box,
                "row": form.rowCount() - 1,
            }
            for obj in (label, box, w):
                obj.installEventFilter(self)
                obj.setProperty("_ctg_param", p.name)
        self._refresh()

    # -- widgets --------------------------------------------------------------
    def _make_widget(self, p, completions) -> QtWidgets.QWidget:
        v = self._typed.get(p.name.lower(), p.default_value)
        ro = self._read_only
        if p.values:
            w = QtWidgets.QComboBox()
            w.addItems(list(p.values))
            idx = next((k for k, s in enumerate(p.standard_values) if C.same_value(s, v)), -1)
            if idx < 0:
                # a value outside the standard values stays visible (flagged)
                w.addItem(C.to_text(v))
                idx = w.count() - 1
            w.setCurrentIndex(idx)
            w.setEnabled(not ro)
            w.currentIndexChanged.connect(lambda _i, n=p.name, cw=w: self._combo_changed(n, cw))
            return w
        if p.type == "Bool":
            w = QtWidgets.QCheckBox()
            w.setChecked(bool(v))
            w.setEnabled(not ro)
            w.toggled.connect(lambda on, n=p.name: self._set(n, bool(on)))
            return w
        if p.type == "Int":
            w = QtWidgets.QSpinBox()
            # the whole Int32 range: a value outside the template's limits is
            # shown and flagged, never clamped away
            w.setRange(_SPIN_MIN, _SPIN_MAX)
            w.setValue(int(v))
            w.setReadOnly(ro)
            if ro:
                w.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            w.valueChanged.connect(lambda val, n=p.name: self._set(n, int(val)))
            return w
        names = completions.get(p.naming_rule) if p.naming_rule else None
        holder = None
        if p.naming_rule == "Equipment":
            # the equipment field: completion + Browse… (the scope's equipment tree)
            from ..panels.sa_pickers import EquipmentField

            holder = EquipmentField(C.to_text(v), self._project_dir)
            w = holder.lineEdit()
            if names and not isinstance(names, QtCore.QAbstractItemModel):
                comp = QtWidgets.QCompleter(QtCore.QStringListModel(list(names), w), w)
                comp.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
                comp.setFilterMode(QtCore.Qt.MatchContains)
                w.setCompleter(comp)
            holder.button.setEnabled(not ro)
        elif (
            p.naming_rule == "Tag"
            and names is not None
            and hasattr(names, "update")
            and isinstance(names, QtCore.QAbstractItemModel)
        ):
            # the scope's shared tag completion model (ui/objprops/expression.py)
            from ..objprops.expression import ExpressionContext, ExpressionField

            w = ExpressionField(
                ExpressionContext(self._project_dir or None), wizard=("tag",), field_kind="tag"
            )
            w.setText(C.to_text(v))
            w.set_names(names)
        else:
            w = QtWidgets.QLineEdit(C.to_text(v))
            if names:
                model = QtCore.QStringListModel(list(names), w)
                comp = QtWidgets.QCompleter(model, w)
                comp.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
                comp.setFilterMode(QtCore.Qt.MatchContains)
                w.setCompleter(comp)
        if p.max_length < 32767:
            # never cut a stored value that is already longer (it is flagged)
            w.setMaxLength(max(p.max_length, len(C.to_text(v))))
        w.setReadOnly(ro)
        w.textChanged.connect(lambda t, n=p.name: self._set(n, str(t)))
        if holder is not None:
            w._ctg_holder = holder
        return w

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt API
        """Wide enough to show the longest text value (the window opens so
        that every value can be read; a larger window widens the fields)."""
        s = super().sizeHint()
        extra = 0
        for r in self._rows.values():
            w = r["widget"]
            if isinstance(w, QtWidgets.QLineEdit):
                need = w.fontMetrics().horizontalAdvance(w.text()) + 24
                extra = max(extra, need - w.sizeHint().width())
        return QtCore.QSize(s.width() + extra, s.height())

    def _combo_changed(self, name: str, w: QtWidgets.QComboBox) -> None:
        p = self._rows[name.lower()]["param"]
        ok, v = C.parse_typed(p.type, w.currentText())
        self._set(name, v if ok else p.default_value)

    def _set(self, name: str, value) -> None:
        if self._read_only:
            return
        self._typed[name.lower()] = value
        self.__dict__.setdefault("_touched", set()).add(name.lower())
        self._refresh()
        self.changed.emit()

    # -- visibility / validation ------------------------------------------------
    def _matches(self, p) -> bool:
        return self._search(p.label, p.name)

    def _refresh(self) -> None:
        for low, r in self._rows.items():
            p = r["param"]
            dep = C.shown(p, self._typed)
            err = C.validate_value(p, self._typed.get(low, p.default_value)) if dep else ""
            if err:
                self._past_invalid.add(low)
            visible = (
                dep
                and self._matches(p)
                and (not self._only_invalid or bool(err) or low in self._past_invalid)
            )
            self._form.setRowVisible(r["row"], visible)
            # a requirement reads dim until the field was typed in or OK was pressed
            late = low in self.__dict__.get("_touched", ()) or getattr(self, "_submitted", False)
            r["error"].setText(err)
            r["error"].setVisible(bool(err))
            set_note_role(r["error"], "danger" if late else "text_dim")
            set_note_role(r["label"], "danger" if err and late else "")
            note = ""
            if self._advice is not None and dep and not err:
                try:
                    note = self._advice(p, self._typed.get(low)) or ""
                except Exception:  # noqa: BLE001 -- advice never breaks the form
                    note = ""
            r["note"].setText(note)
            r["note"].setVisible(bool(note))

    def set_search(self, text) -> None:
        """Show only the parameters whose label or name matches ``text`` (a part, without
        case; a TextMatch: the search box's rule, a regular expression with its '.*')."""
        self._search = text if isinstance(text, TextMatch) else TextMatch(text or "")
        self._refresh()

    def set_show_only_invalid(self, on: bool) -> None:
        """CtDraw's 'Show only parameters with invalid input': the rows that
        are invalid now (and those that become invalid) stay shown, even
        once corrected, until the option is switched off."""
        self._only_invalid = bool(on)
        if on:
            self._past_invalid = {n.lower() for n in self.problems()}
        self._refresh()

    # -- results ------------------------------------------------------------------
    @property
    def params(self) -> list:
        return list(self._params)

    def typed_values(self) -> Dict[str, object]:
        return {p.name: self._typed.get(p.name.lower(), p.default_value) for p in self._params}

    def values(self) -> Dict[str, str]:
        """The values OK would apply, as text: a hidden parameter (its
        dependency is false) takes its default (GetFinalParameterValues)."""
        return {k: C.to_text(v) for k, v in C.final_values(self._tpl, self._typed).items()}

    def changed_values(self) -> Dict[str, str]:
        """Only the values that differ from the template defaults."""
        out = {}
        final = C.final_values(self._tpl, self._typed)
        for p in self._params:
            v = final[p.name]
            if not C.same_value(v, p.default_value):
                out[p.name] = C.to_text(v)
        return out

    def problems(self) -> Dict[str, str]:
        """{name: message} of the shown parameters with invalid values."""
        return C.invalid_parameters(self._tpl, self._typed)

    def is_valid(self) -> bool:
        return not self.problems()

    def row_shown(self, name: str) -> bool:
        r = self._rows.get((name or "").lower())
        return bool(r) and self._form.isRowVisible(r["row"])

    def error_text(self, name: str) -> str:
        r = self._rows.get((name or "").lower())
        return r["error"].text() if r else ""

    def widget(self, name: str) -> Optional[QtWidgets.QWidget]:
        r = self._rows.get((name or "").lower())
        return r["widget"] if r else None

    def is_read_only(self) -> bool:
        return self._read_only

    # -- the page changes underneath -----------------
    def _row_typed(self, low: str) -> bool:
        return not C.same_value(self._typed.get(low), self._typed0.get(low))

    def has_pending_edits(self) -> bool:
        """An option was changed and not applied yet."""
        return not self._read_only and any(self._row_typed(low) for low in self._rows)

    def reload(self, current: Optional[dict]) -> None:
        """Show ``current`` (the options as the genie has them now) in every
        row the user has not changed; typed rows keep their values."""
        new = C.typed_values(self._tpl, current)
        for low, r in self._rows.items():
            if self._row_typed(low):
                continue
            p = r["param"]
            v = new.get(low, p.default_value)
            self._show_value(p, r["widget"], v)
            self._typed[low] = v
            self._typed0[low] = v
        self._refresh()
        self.changed.emit()

    @staticmethod
    def _show_value(p, w, v) -> None:
        """Put value ``v`` of parameter ``p`` into its widget (no change signal)."""
        w.blockSignals(True)
        try:
            if isinstance(w, QtWidgets.QComboBox):
                idx = next((k for k, s in enumerate(p.standard_values) if C.same_value(s, v)), -1)
                if idx < 0:
                    text = C.to_text(v)
                    idx = w.findText(text)
                    if idx < 0:
                        w.addItem(text)
                        idx = w.count() - 1
                w.setCurrentIndex(idx)
            elif isinstance(w, QtWidgets.QCheckBox):
                w.setChecked(bool(v))
            elif isinstance(w, QtWidgets.QSpinBox):
                w.setValue(int(v))
            elif isinstance(w, QtWidgets.QLineEdit):
                text = C.to_text(v)
                if p.max_length < 32767:
                    w.setMaxLength(max(p.max_length, len(text)))
                w.setText(text)
        except TypeError, ValueError:
            pass
        finally:
            w.blockSignals(False)

    def current(self) -> str:
        return self._current

    def set_current(self, name: str) -> None:
        """Make ``name`` the parameter the description pane shows."""
        if name and name != self._current:
            self._current = name
            self.currentChanged.emit(name)

    def eventFilter(self, obj, ev):  # noqa: N802 (Qt API)
        if ev.type() in (QtCore.QEvent.FocusIn, QtCore.QEvent.Enter, QtCore.QEvent.MouseButtonPress):
            name = obj.property("_ctg_param")
            if name:
                self.set_current(str(name))
        return super().eventFilter(obj, ev)


def describe(p, error: str = "") -> str:
    """The description pane's HTML for parameter ``p``."""
    esc = _html.escape
    parts = [
        "<b>%s</b> <span style='color:#9aa3ad'>(%s, %s)</span>"
        % (esc(p.label), esc(p.name), esc({"Int": "Integer", "Bool": "Boolean"}.get(p.type, p.type)))
    ]
    if p.description:
        parts.append(esc(p.description))
    limits = []
    if p.type == "String" and (p.min_length > 0 or p.max_length < C.INT_MAX):
        limits.append("%d to %d characters" % (p.min_length, p.max_length))
    if p.type == "Int" and (p.min_value > C.INT_MIN or p.max_value < C.INT_MAX):
        limits.append("%d to %d" % (p.min_value, p.max_value))
    if p.naming_rule:
        limits.append("%s naming rules" % p.naming_rule.lower())
    if p.has_default:
        limits.append("default: %s" % (p.default or "(empty)"))
    if limits:
        parts.append("<span style='color:#9aa3ad'>%s</span>" % esc("; ".join(limits)))
    if error:
        parts.append("<span style='color:#e05252'>%s</span>" % esc(error))
    return "<br>".join(parts)


class CompositeOptionsPanel(QtWidgets.QWidget):
    """The body of both dialogs: notes, template problems, the template ID
    and instance name (selectable), Search, 'Show only parameters with
    invalid input', the parameter form and the description pane."""

    changed = QtCore.Signal()

    def __init__(
        self,
        parent=None,
        *,
        completions: Optional[dict] = None,
        advice: Optional[Callable] = None,
        project_dir: str = "",
    ):
        super().__init__(parent)
        self._completions = completions or {}
        self._advice = advice
        self._project_dir = project_dir or ""
        self.form: Optional[TypedParamsForm] = None
        self._cache_dir = ""
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._notes = QtWidgets.QLabel()
        self._notes.setWordWrap(True)
        self._notes.setTextFormat(QtCore.Qt.PlainText)
        set_note_role(self._notes, "warn")
        self._notes.hide()
        lay.addWidget(self._notes)
        self._problems = QtWidgets.QPlainTextEdit()
        self._problems.setReadOnly(True)
        self._problems.setMaximumHeight(96)
        self._problems.hide()
        lay.addWidget(self._problems)
        ids = QtWidgets.QFormLayout()
        self._tpl_id = QtWidgets.QLineEdit()
        self._tpl_id.setReadOnly(True)
        self._tpl_id.setFocusPolicy(QtCore.Qt.ClickFocus)  # (the first option field gets the focus)
        self._tpl_id.setToolTip("The template ID. Select and copy it to find the template files.")
        ids.addRow("Template ID:", self._tpl_id)
        inst_row = QtWidgets.QHBoxLayout()
        self._inst = QtWidgets.QLineEdit()
        self._inst.setReadOnly(True)
        self._inst.setFocusPolicy(QtCore.Qt.ClickFocus)
        self._inst.setToolTip("The instance name")
        inst_row.addWidget(self._inst, 1)
        self._open = QtWidgets.QPushButton("Show in Explorer")
        self._open.setToolTip("Show the composition cache folder in Explorer. Do not change its files.")
        self._open.clicked.connect(self._open_folder)
        inst_row.addWidget(self._open)
        self._inst_label = QtWidgets.QLabel("Instance:")
        ids.addRow(self._inst_label, inst_row)
        lay.addLayout(ids)
        self._summary = QtWidgets.QLabel("Options of this composite genie:")
        lay.addWidget(self._summary)
        tools = QtWidgets.QHBoxLayout()
        from ..style.theme import SearchField

        self.search = SearchField("Search parameters")
        # (a few rows: filtered as the text changes)
        self.search.textChanged.connect(self._on_search)
        tools.addWidget(self.search, 1)
        self.only_invalid = QtWidgets.QCheckBox("Show only parameters with invalid input")
        self.only_invalid.toggled.connect(self._on_only_invalid)
        tools.addWidget(self.only_invalid)
        lay.addLayout(tools)
        # the options scroll in a smaller window; the window opens big enough to
        # show them all (within the screen)
        from ..objprops.scroll import PageScroll

        self._scroll = PageScroll(QtWidgets.QWidget())
        self._scroll.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self._scroll.setMinimumSize(420, 260)
        lay.addWidget(self._scroll, 1)
        self._empty = QtWidgets.QLabel()
        set_note_role(self._empty, "text_dim")
        self._empty.hide()
        lay.addWidget(self._empty)
        self.description = QtWidgets.QLabel()
        self.description.setWordWrap(True)
        self.description.setTextFormat(QtCore.Qt.RichText)
        self.description.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.description.setMinimumHeight(64)
        self.description.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.description.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.description)

    def set_options(
        self,
        template,
        current: Optional[dict],
        *,
        read_only: bool = True,
        instance: str = "",
        cache_dir: str = "",
        notes: Sequence[str] = (),
    ) -> None:
        """Show ``template``'s parameters with ``current``'s values."""
        self._cache_dir = cache_dir or ""
        self._tpl_id.setText(template.template_id if template is not None else "")
        self._inst.setText(instance or "")
        for w in (self._inst_label, self._inst):
            w.setVisible(bool(instance))
        self._open.setVisible(bool(self._cache_dir) and os.path.isdir(self._cache_dir))
        notes = [n for n in notes if n]
        if template is not None and template.duplicates:
            notes.append(C.duplicate_warning(template))
        self._notes.setText("\n\n".join(notes))
        self._notes.setVisible(bool(notes))
        probs = list(template.problems) if template is not None else []
        if probs:
            self._problems.setPlainText(
                "The template file %s has %d problem%s. Fix them in the XML file:\n%s"
                % (
                    template.path,
                    len(probs),
                    "" if len(probs) == 1 else "s",
                    "\n".join(str(p) for p in probs),
                )
            )
        self._problems.setVisible(bool(probs))
        self.form = TypedParamsForm(
            template.params if template is not None else (),
            current,
            read_only=read_only,
            completions=self._completions,
            advice=self._advice,
            project_dir=self._project_dir,
        )
        self.form.currentChanged.connect(self._show_description)
        self.form.changed.connect(self._on_changed)
        self._scroll.setWidget(self.form)
        self.form.set_search(self.search.matcher())
        self.form.set_show_only_invalid(self.only_invalid.isChecked())
        first = next((p.name for p in self.form.params if self.form.row_shown(p.name)), "")
        self.description.setText("")
        if first:
            self._show_description(first)
        self._update_empty()

    def _show_description(self, name: str) -> None:
        if self.form is None:
            return
        p = next((q for q in self.form.params if q.name == name), None)
        if p is not None:
            self.description.setText(describe(p, self.form.error_text(name)))

    def _on_changed(self) -> None:
        if self.form is not None and self.form.current():
            self._show_description(self.form.current())
        self._update_empty()
        self.changed.emit()

    def _on_search(self, text: str) -> None:
        if self.form is not None:
            self.form.set_search(self.search.matcher(text))
            self._update_empty()

    def _on_only_invalid(self, on: bool) -> None:
        if self.form is not None:
            self.form.set_show_only_invalid(on)
            self._update_empty()

    def _update_empty(self) -> None:
        if self.form is None:
            return
        any_shown = any(self.form.row_shown(p.name) for p in self.form.params)
        if any_shown or not self.form.params:
            self._empty.hide()
            return
        if self.only_invalid.isChecked():
            self._empty.setText("No parameter has an invalid value.")
        elif self.search.text().strip():
            self._empty.setText("No parameter matches '%s'." % self.search.text().strip())
        else:
            self._empty.setText("No parameter applies with these options.")
        self._empty.show()

    def _open_folder(self) -> None:
        if self._cache_dir and os.path.isdir(self._cache_dir):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self._cache_dir))


class PresentationOptionsDialog(QtWidgets.QDialog):
    """A composite genie's Presentation Options."""

    # the option list takes the extra room
    RESIZE = "both"

    def __init__(
        self,
        template,
        current: Optional[dict],
        parent=None,
        read_only: bool = False,
        note: str = "",
        *,
        instance: str = "",
        cache_dir: str = "",
        notes: Sequence[str] = (),
        completions: Optional[dict] = None,
        advice: Optional[Callable] = None,
        project_dir: str = "",
        index: Optional[int] = None,
        options_now: Optional[Callable[[], Optional[dict]]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Presentation Options - %s" % template.name)
        self.template = template
        self.index = index
        self._options_now = options_now
        lay = QtWidgets.QVBoxLayout(self)
        if note:
            lab = QtWidgets.QLabel(note)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        self.panel = CompositeOptionsPanel(
            self, completions=completions, advice=advice, project_dir=project_dir
        )
        self.panel.set_options(
            template, current, read_only=read_only, instance=instance, cache_dir=cache_dir, notes=notes
        )
        lay.addWidget(self.panel, 1)
        self._buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Close
            if read_only
            else QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        lay.addWidget(self._buttons)
        self.panel.changed.connect(self._update_ok)
        self._update_ok()

    @property
    def _form(self) -> TypedParamsForm:
        return self.panel.form

    # -- modeless -------------------------------------
    def has_pending_edits(self) -> bool:
        return self.panel.form is not None and self.panel.form.has_pending_edits()

    def retarget(self, index: int) -> None:
        """The genie's record now sits at ``index``."""
        self.index = index

    def reload(self, current: Optional[dict] = None) -> None:
        """Show the genie's options as they are now in the untouched rows."""
        if current is None and self._options_now is not None:
            try:
                current = self._options_now()
            except Exception:  # noqa: BLE001 -- keep what is shown
                return
        if current is None or self.panel.form is None:
            return
        self.panel.form.reload(current)
        self._update_ok()

    def _update_ok(self) -> None:
        ok = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok is not None:
            valid = self.panel.form.is_valid()
            ok.setEnabled(valid)
            ok.setToolTip("" if valid else "Correct the options marked in red first.")

    def accept(self) -> None:
        form = getattr(getattr(self, "panel", None), "form", None)
        if form is not None:
            form._submitted = True  # (every requirement shows in full now)
            form._refresh()
        if not self.panel.form.is_read_only() and not self.panel.form.is_valid():
            return
        super().accept()

    def values(self) -> dict:
        return self.panel.form.values()


class PasteCompositeGenieDialog(QtWidgets.QDialog):
    """Edit > Paste Composite Genie."""

    # the lists take the extra room
    RESIZE = "both"

    def __init__(
        self,
        templates: dict,
        donors: Optional[dict] = None,
        parent=None,
        *,
        donor_info: Optional[Callable] = None,
        completions: Optional[dict] = None,
        advice: Optional[Callable] = None,
        project_dir: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Paste Composite Genie")
        self._templates = templates
        self._donors = donors or {}
        self._values: Dict[str, Dict[str, object]] = {}
        # (UI guide section 9: a QVBoxLayout with the buttons last, under
        # the list and the options)
        page = QtWidgets.QVBoxLayout(self)
        lay = QtWidgets.QHBoxLayout()
        page.addLayout(lay, 1)
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("Template:"))
        self._list = QtWidgets.QListWidget()
        from ..notify import level_icon

        warn = level_icon("warning")
        for guid, tpl in sorted(templates.items(), key=lambda kv: kv[1].name.lower()):
            item = QtWidgets.QListWidgetItem(tpl.name)
            item.setData(QtCore.Qt.UserRole, guid)
            tips = [tpl.path]
            if tpl.problems:
                item.setIcon(warn)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)
                item.setText(tpl.name + " (has problems)")
                tips.append("This template has problems. Select it to see them.")
            if tpl.duplicates:
                item.setIcon(warn)
                tips.append("Another template file has the same template ID.")
            if guid in self._donors:
                tips.append(
                    "Already placed on %s." % os.path.splitext(os.path.basename(self._donors[guid][0]))[0]
                )
            item.setToolTip("\n".join(tips))
            self._list.addItem(item)
        self._list.currentItemChanged.connect(self._rebuild_form)
        left.addWidget(self._list)
        lay.addLayout(left, 1)
        right = QtWidgets.QVBoxLayout()
        self._note = QtWidgets.QLabel(
            "Choose the options, click OK, then click on the page where the genie goes."
        )
        self._note.setWordWrap(True)
        right.addWidget(self._note)
        self.panel = CompositeOptionsPanel(
            self, completions=completions, advice=advice, project_dir=project_dir
        )
        self.panel.changed.connect(self._update_ok)
        right.addWidget(self.panel, 1)
        self._buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        lay.addLayout(right, 2)
        page.addWidget(self._buttons)
        self._form = None
        self._guid = None
        self._buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(False)
        for i in range(self._list.count()):
            if self._list.item(i).flags() & QtCore.Qt.ItemIsEnabled:
                self._list.setCurrentRow(i)
                break

    def _rebuild_form(self, item, _prev=None) -> None:
        if self._form is not None and self._guid is not None:
            # switching back to a template keeps what was typed for it
            self._values[self._guid] = self._form.typed_values()
        if item is None:
            return
        guid = item.data(QtCore.Qt.UserRole)
        tpl = self._templates.get(guid)
        if tpl is None:
            return
        enabled = bool(item.flags() & QtCore.Qt.ItemIsEnabled)
        self.panel.set_options(tpl, self._values.get(guid, {}), read_only=not enabled)
        self._form = self.panel.form
        self._guid = guid
        self._update_ok()

    def _update_ok(self) -> None:
        ok = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
        item = self._list.currentItem()
        enabled = (
            item is not None
            and bool(item.flags() & QtCore.Qt.ItemIsEnabled)
            and self._form is not None
            and self._form.is_valid()
        )
        ok.setEnabled(enabled)
        ok.setToolTip(
            ""
            if enabled or self._form is None or self._form.is_valid()
            else "Correct the options marked in red first."
        )

    def accept(self) -> None:
        if self.selection()[0] is None:
            return
        super().accept()

    def selection(self):
        """(guid, {option: value as text}) of the chosen template -- the values
        OK would compose with (a hidden option takes its default) -- or
        (None, {}) when none can be placed."""
        item = self._list.currentItem()
        if (
            item is None
            or self._form is None
            or not (item.flags() & QtCore.Qt.ItemIsEnabled)
            or not self._form.is_valid()
        ):
            return None, {}
        return item.data(QtCore.Qt.UserRole), self._form.values()
