"""Edit > Rename Objects: rename several objects at once."""

from __future__ import annotations
from ..style.theme import sp as _sp

import re
import string

from PySide6 import QtCore, QtWidgets

from ..names import element_label, kind_name, object_name_of, what_label

#: the orders the numbers are given in
ORDERS = (("reading", "Reading order"), ("drawing", "Drawing order"), ("selection", "Selection order"))
#: the fields a pattern can use
PATTERN_FIELDS = ("n", "name", "element", "kind", "an")


def reading_order(page, indices) -> list:
    """``indices`` as a page is read: rows from the top, each from the left (objects whose
    middles are within half an object's height of a row's first one share its row)."""
    boxes = []
    for i in indices:
        g = page.objects[i].geometry
        if g is None:
            boxes.append((i, 0.0, 0.0, 1.0))
            continue
        boxes.append((i, g.x + g.extent_x / 2.0, g.y + g.extent_y / 2.0, max(1.0, float(g.extent_y))))
    boxes.sort(key=lambda b: (b[2], b[1]))
    rows, out = [], []
    for b in boxes:
        if rows and abs(b[2] - rows[-1][0][2]) <= max(4.0, min(b[3], rows[-1][0][3]) / 2.0):
            rows[-1].append(b)
        else:
            rows.append([b])
    for row in rows:
        out.extend(i for i, _x, _y, _h in sorted(row, key=lambda b: b[1]))
    return out


def ordered(page, indices, order: str) -> list:
    """``indices`` in ``order``: 'reading', 'drawing' (the stacking order, back first) or
    'selection' (as given)."""
    if order == "reading":
        return reading_order(page, indices)
    if order == "drawing":
        return sorted(indices)
    return list(indices)


class _Fields(dict):
    def __missing__(self, key):
        raise KeyError(key)


def codepage_problem(name: str) -> str:
    """Why the page's code page cannot store ``name`` ('' = it can, and always while such
    text is stored as '?'): what doc.set_object_name would refuse with."""
    from ...editing_base import UnencodableTextError, guard_text

    try:
        guard_text(name)
    except UnencodableTextError as e:
        return "%s." % str(e).rstrip(".")
    return ""


def check_pattern(pattern: str) -> None:
    """Raise ValueError unless every field of ``pattern`` is one of the plain
    :data:`PATTERN_FIELDS` (with a number format at most: no '.', '[' or '!')."""
    try:
        parts = list(string.Formatter().parse(pattern))
    except ValueError as e:
        raise ValueError("The pattern cannot be filled in: %s." % e) from None
    for _text, field, spec, conv in parts:
        if field is None:
            continue
        if conv is not None or field not in PATTERN_FIELDS or "{" in (spec or ""):
            shown = field + ("!" + conv if conv else "")
            raise ValueError(
                "The pattern has an unknown field {%s}. Use {n}, {name}, {element}, {kind} or {an}." % shown
            )


def pattern_name(page, index: int, pattern: str, n: int) -> str:
    """The name ``pattern`` gives object ``index`` as number ``n``. Raises ValueError for a
    pattern that cannot be filled in (an unknown field, a bad number format)."""
    check_pattern(pattern)
    obj = page.objects[index]
    kind = kind_name(obj).replace(" ", "_")
    fields = _Fields(
        n=n,
        name=object_name_of(obj),
        kind=kind,
        element=(element_label(obj, page) or kind).replace(" ", "_"),
        an=obj.an if obj.an > 0 else "",
    )
    try:
        return string.Formatter().vformat(pattern, (), fields).strip()
    except KeyError as e:
        raise ValueError(
            "The pattern has an unknown field {%s}. Use {n}, {name}, {element}, {kind} or {an}." % e.args[0]
        ) from None
    except (ValueError, IndexError, AttributeError, TypeError) as e:
        raise ValueError("The pattern cannot be filled in: %s." % e) from None


def replaced_name(name: str, find: str, repl: str, regex: bool, case: bool) -> str:
    """``name`` with ``find`` replaced by ``repl`` (a regular expression with ``regex``; its
    groups as \\1). Raises ValueError for an expression that does not compile."""
    if not find:
        return name
    flags = 0 if case else re.IGNORECASE
    if regex:
        try:
            return re.sub(find, repl, name, flags=flags).strip()
        except re.error as e:
            raise ValueError("Not a regular expression: %s." % e) from None
    return re.sub(re.escape(find), lambda _m: repl, name, flags=flags).strip()


def _namespace(doc, index: int):
    """The namespace of object ``index`` (doc.name_siblings' rule): its group (a group
    record) or None, the page's top level."""
    parent = doc.page.objects[index].parent
    if parent is not None and doc.parsed.page.objects[parent].type_code == 0x08:
        return parent
    return None


def plan_problems(doc, pairs) -> dict:
    """{index: why its new name cannot be used} for ``pairs`` [(index, new name)]: the name
    rule, the page's code page, two of them taking one name in one namespace, or another
    object (not renamed here) using it. Each object's name is read once and the top level's
    names are looked up in one map (a preview of many objects on a big page stays quick)."""
    from ...editing_base import object_name_rule_problem

    page = doc.page
    out = {}
    renamed = {i for i, _n in pairs}
    by_space: dict = {}
    names: dict = {}
    top: dict = {}

    def low(j: int) -> str:
        v = names.get(j)
        if v is None:
            v = names[j] = object_name_of(page.objects[j]).lower()
        return v

    def top_names() -> dict:
        if None not in top:
            top[None] = {}
            for j in page.roots():
                if j not in renamed and low(j):
                    top[None].setdefault(low(j), j)
        return top[None]

    for i, new in pairs:
        msg = codepage_problem(new) or object_name_rule_problem(new)
        if msg:
            out[i] = msg
            continue
        if not new:
            continue
        ns = _namespace(doc, i)
        want = new.lower()
        key = (ns, want)
        if key in by_space:
            out[i] = "'%s' is given to two objects here: each needs its own name." % new
            out.setdefault(by_space[key], out[i])
            continue
        by_space[key] = i
        if ns is None:
            hit = top_names().get(want)
        else:
            hit = next((j for j in doc.name_siblings(i) if j not in renamed and low(j) == want), None)
        if hit is not None:
            o = page.objects[hit]
            out[i] = (
                "The name '%s' is already used by the %s%s. Object names must be "
                "unique in their group or on the page."
                % (new, kind_name(o).lower(), (" (AN %d)" % o.an) if o.an > 0 else "")
            )
    return out


def apply_renames(doc, pairs) -> int:
    """Rename several objects as one undo step."""
    problems = plan_problems(doc, pairs)
    if problems:
        raise ValueError(next(iter(problems.values())))
    pending = [(i, n or "") for i, n in pairs if (n or "") != doc.object_name(i)]
    if not pending:
        return 0
    count = len(pending)
    # (plan_problems found no other object using a new name, so only an object of the
    # list can hold one: the list's names are followed in one map, not a scan per name)
    space = {i: _namespace(doc, i) for i, _n in pairs}
    held: dict = {}
    for i, _n in pairs:
        name = doc.object_name(i).lower()
        if name:
            held.setdefault((space[i], name), set()).add(i)

    def taken(i: int, new: str) -> bool:
        return bool(held.get((space[i], new.lower()), set()) - {i})

    def give(i: int, new: str) -> None:
        old = doc.object_name(i).lower()
        doc.set_object_name(i, new)
        if old:
            held.get((space[i], old), set()).discard(i)
        if new:
            held.setdefault((space[i], new.lower()), set()).add(i)

    with doc.batch("rename"):
        spare = 0
        while pending:
            progress = False
            for item in list(pending):
                i, new = item
                if not new or not taken(i, new):
                    give(i, new)
                    pending.remove(item)
                    progress = True
            if pending and not progress:
                # (a round: the first of them takes a name nobody has, then the rest go)
                i, new = pending[0]
                while True:
                    spare += 1
                    tmp = "Rename%d" % spare
                    if not taken(i, tmp) and doc.name_conflict(i, tmp) is None:
                        break
                give(i, tmp)
    return count


class RenameObjectsDialog(QtWidgets.QDialog):
    """Edit > Rename Objects…: a pattern or a find and replace, a preview, one undo step."""

    #: ms of no typing before the preview follows a pattern, find or replace field
    TYPING_MS = 150
    #: the last choices in this session (the pattern, start, step, order, find, replace)
    LAST = {
        "pattern": "{element}_{n:02}",
        "start": 1,
        "step": 1,
        "order": "reading",
        "find": "",
        "replace": "",
        "regex": False,
        "case": False,
        "mode": 0,
    }

    def __init__(self, window, doc, indices, refusal=None):
        super().__init__(window)
        self.setWindowTitle("Rename Objects")
        self._doc = doc
        self.renamed = 0
        refusal = refusal or getattr(window, "_rename_refusal_of", None)
        page = doc.page
        seen, targets, left_out = set(), [], []
        for i in indices:
            if i in seen or not 0 <= i < len(page.objects):
                continue
            seen.add(i)
            why = refusal(i) if refusal is not None else ""
            if why:
                left_out.append(why)
            else:
                targets.append(i)
        self._targets = targets
        self._pairs = []

        from ..style.theme import note_label

        lay = QtWidgets.QVBoxLayout(self)
        self._mode = QtWidgets.QTabBar(self)
        self._mode.addTab("Pattern")
        self._mode.addTab("Find and Replace")
        self._mode.setDrawBase(False)
        lay.addWidget(self._mode)
        self._pages = QtWidgets.QStackedWidget(self)
        lay.addWidget(self._pages)

        last = self.LAST
        pat = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(pat)
        form.setContentsMargins(0, _sp(1), 0, 0)
        self._pattern = QtWidgets.QLineEdit(last["pattern"])
        self._pattern.setToolTip(
            "{n} the number ({n:02}: two digits), {name} the name it "
            "has, {element} its genie or symbol, {kind}, {an}"
        )
        form.addRow("Pattern:", self._pattern)
        row = QtWidgets.QHBoxLayout()
        self._start = QtWidgets.QSpinBox()
        self._start.setRange(0, 99999)
        self._start.setValue(int(last["start"]))
        self._step = QtWidgets.QSpinBox()
        self._step.setRange(1, 1000)
        self._step.setValue(int(last["step"]))
        row.addWidget(self._start)
        row.addWidget(QtWidgets.QLabel("Step:"))
        row.addWidget(self._step)
        row.addStretch(1)
        form.addRow("Start at:", row)
        self._order = QtWidgets.QComboBox()
        for key, label in ORDERS:
            self._order.addItem(label, key)
        self._order.setCurrentIndex(max(0, [k for k, _l in ORDERS].index(last["order"])))
        form.addRow("Number in:", self._order)
        self._pages.addWidget(pat)

        rep = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(rep)
        form.setContentsMargins(0, _sp(1), 0, 0)
        self._find = QtWidgets.QLineEdit(last["find"])
        self._repl = QtWidgets.QLineEdit(last["replace"])
        form.addRow("Find:", self._find)
        form.addRow("Replace with:", self._repl)
        self._regex = QtWidgets.QCheckBox("Regular expression (groups as \\1)")
        self._regex.setChecked(bool(last["regex"]))
        self._case = QtWidgets.QCheckBox("Match case")
        self._case.setChecked(bool(last["case"]))
        form.addRow("", self._regex)
        form.addRow("", self._case)
        self._pages.addWidget(rep)

        self._table = QtWidgets.QTableWidget(0, 3, self)
        self._table.setObjectName("renamePreview")
        self._table.setHorizontalHeaderLabels(["Object", "Old name", "New name"])
        self._table.verticalHeader().hide()
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        from .. import tables

        tables.make_resizable(self._table, "RenameObjectsDialog/preview", stretch=2)
        lay.addWidget(self._table, 1)
        note = ""
        if left_out:
            note = "%d object%s left out: %s" % (
                len(left_out),
                "" if len(left_out) == 1 else "s",
                left_out[0],
            )
        self._left = note_label(note, "text_dim", self)
        self._left.setVisible(bool(note))
        lay.addWidget(self._left)
        self._status = note_label("", "danger", self)
        lay.addWidget(self._status)

        B = QtWidgets.QDialogButtonBox
        self._buttons = B(self)
        self._ok = self._buttons.addButton("Rename", B.AcceptRole)
        self._ok.setDefault(True)
        self._buttons.addButton(B.Cancel)
        self._buttons.accepted.connect(self._apply)
        self._buttons.rejected.connect(self.reject)
        lay.addWidget(self._buttons)

        self._mode.currentChanged.connect(self._pages.setCurrentIndex)
        self._mode.setCurrentIndex(int(last["mode"]))
        self._pages.setCurrentIndex(int(last["mode"]))
        # (typing: the preview waits for a pause, as a search field does; the other choices
        # show at once)
        self._typing = QtCore.QTimer(self)
        self._typing.setSingleShot(True)
        self._typing.setInterval(self.TYPING_MS)
        self._typing.timeout.connect(self._preview)
        for sig in (self._pattern.textChanged, self._find.textChanged, self._repl.textChanged):
            sig.connect(self._typed)
        for sig in (
            self._start.valueChanged,
            self._step.valueChanged,
            self._order.currentIndexChanged,
            self._mode.currentChanged,
        ):
            sig.connect(self._preview)
        self._regex.toggled.connect(self._preview)
        self._case.toggled.connect(self._preview)
        self.resize(520, 440)
        self._preview()

    # -- the names ---------------------------------------------------------------------------
    def new_names(self) -> list:
        """[(index, new name)] the choices give, in the order they are listed. Raises
        ValueError for a pattern or expression that cannot be used."""
        page = self._doc.page
        if self._mode.currentIndex() == 0:
            order = self._order.currentData()
            out = []
            n, step = self._start.value(), self._step.value()
            for i in ordered(page, self._targets, order):
                out.append((i, pattern_name(page, i, self._pattern.text(), n)))
                n += step
            return out
        find, repl = self._find.text(), self._repl.text()
        return [
            (
                i,
                replaced_name(
                    object_name_of(page.objects[i]),
                    find,
                    repl,
                    self._regex.isChecked(),
                    self._case.isChecked(),
                ),
            )
            for i in self._targets
        ]

    def _typed(self, *_args) -> None:
        # (Rename stays on while the preview waits: Enter right after typing reaches
        # _apply, which shows the preview first and renames only when it is valid)
        self._ok.setEnabled(True)
        self._typing.start()

    def flush(self) -> None:
        """Show the preview of what was typed now (not after the pause)."""
        if self._typing.isActive():
            self._preview()

    def _preview(self, *_args) -> None:
        self._typing.stop()
        from ..style.theme import COLORS
        from PySide6 import QtGui

        page = self._doc.page
        t = self._table
        try:
            pairs = self.new_names()
            error = ""
        except ValueError as e:
            pairs, error = [(i, None) for i in self._targets], str(e)
        problems = plan_problems(self._doc, [(i, n) for i, n in pairs if n is not None]) if not error else {}
        t.setRowCount(len(pairs))
        changes = 0
        for r, (i, new) in enumerate(pairs):
            o = page.objects[i]
            old = object_name_of(o)
            # (what the object is, as its Objects panel row reads: 'pump_b  D100  AN 588')
            what = (what_label(page, i, o) or kind_name(o)) + (("  AN %d" % o.an) if o.an > 0 else "")
            cells = [what, old, new if new is not None else ""]
            for c, text in enumerate(cells):
                it = t.item(r, c)
                if it is None:
                    it = QtWidgets.QTableWidgetItem()
                    t.setItem(r, c, it)
                it.setText(text)
                it.setToolTip("")
                it.setData(QtCore.Qt.ForegroundRole, None)
            cell = t.item(r, 2)
            if i in problems:
                cell.setForeground(QtGui.QBrush(QtGui.QColor(COLORS["danger"])))
                cell.setToolTip(problems[i])
            elif new is not None and new == old:
                cell.setForeground(QtGui.QBrush(QtGui.QColor(COLORS["text_dim"])))
                cell.setToolTip("Unchanged")
            elif new is not None:
                changes += 1
        self._pairs = [(i, n) for i, n in pairs if n is not None]
        if error:
            msg = error
        elif problems:
            k = len(problems)
            msg = "%d name%s cannot be used. %s" % (k, "" if k == 1 else "s", next(iter(problems.values())))
        elif not changes:
            msg = "No name changes."
        else:
            msg = ""
        self._status.setText(msg)
        self._status.setVisible(bool(msg))
        from ..style.theme import set_note_role

        set_note_role(self._status, "danger" if (error or problems) else "text_dim")
        self._ok.setEnabled(bool(changes) and not error and not problems)

    def _apply(self) -> None:
        self.flush()
        if not self._ok.isEnabled():
            return
        try:
            self.renamed = apply_renames(self._doc, self._pairs)
        except ValueError as e:
            self._status.setText(str(e))
            self._status.show()
            return
        last = type(self).LAST
        last.update(
            pattern=self._pattern.text(),
            start=self._start.value(),
            step=self._step.value(),
            order=self._order.currentData(),
            find=self._find.text(),
            replace=self._repl.text(),
            regex=self._regex.isChecked(),
            case=self._case.isChecked(),
            mode=self._mode.currentIndex(),
        )
        self.accept()
