"""Tools > Rename Pages."""

from __future__ import annotations

from ...infra.paths import path_key

from ..names import plural

import os
import string
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..style.theme import sp as _sp
from .rename_dialog import NamingPanel

#: the fields a page pattern can use
PAGE_FIELDS = ("n", "name", "title")

#: a page of the list: (its project folder, its name, its title)
Page = Tuple[str, str, str]
#: a rename: (project folder, old name, new name)
Pair = Tuple[str, str, str]


def project_pages(project_dirs: Sequence[str]) -> List[Page]:
    """(folder, name, title) of every page file of the projects, by project then name."""
    from ...io.project import Project

    out: List[Page] = []
    for d in project_dirs:
        try:
            names = sorted(
                (os.path.splitext(f)[0] for f in os.listdir(d) if f.lower().endswith(".ctg")), key=str.lower
            )
        except OSError:
            continue
        titles: Dict[str, str] = {}
        try:
            proj = Project(d)
            if proj.has_page_table():
                for r in proj.page_rows():
                    titles.setdefault(r.get("NAME", "").strip().lower(), (r.get("TITLE") or "").strip())
        except Exception:  # noqa: BLE001  # swallow-ok: the titles are a help only
            titles = {}
        out.extend((d, n, titles.get(n.lower(), "")) for n in names)
    return out


def page_pattern_name(pattern: str, name: str, title: str, n: int) -> str:
    """The name ``pattern`` gives page ``name`` as number ``n``; ValueError when it can't be
    filled in."""
    try:
        parts = list(string.Formatter().parse(pattern))
    except ValueError as e:
        raise ValueError("The pattern cannot be filled in: %s." % e) from None
    for _text, fld, spec, conv in parts:
        if fld is not None and (conv is not None or fld not in PAGE_FIELDS or "{" in (spec or "")):
            raise ValueError("The pattern has an unknown field {%s}. Use {n}, {name} or {title}." % fld)
    try:
        return pattern.format(n=n, name=name, title=title).strip()
    except (ValueError, IndexError, KeyError, TypeError) as e:
        raise ValueError("The pattern cannot be filled in: %s." % e) from None


def _in_builder(path: str) -> bool:
    """The page is open in the Graphics Builder (its next save would write the old name)."""
    try:
        from ...backend.graphics_builder import page_open_in_builder

        return page_open_in_builder(path)
    except Exception:  # noqa: BLE001 -- swallow-ok: a hint only, as lifecycle's
        return False


def _project(d: str) -> str:
    return os.path.basename(os.path.normpath(d))


def plan_problems(pairs: Sequence[Pair], pages: Sequence[Page]) -> Dict[Tuple[str, str], str]:
    """{(folder, old): why the new name can't be used}: the page name rule, two pages given one
    name, or a page of the projects compiled together that keeps it."""
    from ..browser.naming import ERROR, validate_page_name

    out: Dict[Tuple[str, str], str] = {}
    renamed = {(os.path.normcase(d), o.lower()) for d, o, n in pairs if n != o}
    keep: Dict[str, Tuple[str, str]] = {}
    for d, n, _t in pages:
        if (os.path.normcase(d), n.lower()) not in renamed:
            keep.setdefault(n.lower(), (d, n))
    given: Dict[str, Tuple[str, str]] = {}
    for d, old, new in pairs:
        if new == old:
            continue
        v = validate_page_name(new, d, existing={}, mode="new")
        if v.level == ERROR:
            out[(d, old)] = v.message
            continue
        low = new.lower()
        if low in given:
            out[(d, old)] = "'%s' is given to two pages: each needs its own name." % new
            out.setdefault(given[low], out[(d, old)])
            continue
        given[low] = (d, old)
        if low in keep:
            kd, kn = keep[low]
            same = os.path.normcase(kd) == os.path.normcase(d)
            out[(d, old)] = (
                "The project already has a page '%s'." % kn
                if same
                else "Project %s, compiled with this one, has a page '%s'." % (_project(kd), kn)
            )
    return out


class RenamePagesDialog(QtWidgets.QDialog):
    """Rename a project's pages (and its included projects') by a pattern or a find and
    replace, with a preview."""

    TYPING_MS = 150
    #: where the choices are remembered (NamingPanel)
    SETTINGS = "rename/pages"

    def __init__(
        self,
        parent=None,
        project_dir: str = "",
        pages: Sequence[str] = (),
        *,
        hold: Optional[Callable] = None,
        moved: Optional[Callable] = None,
        check_references: Optional[Callable] = None,
        fixed: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle("Rename Pages")
        #: hold(paths): the window saves and watches none of these pages until moved()
        self._hold = hold
        #: moved([(old path, new path)], held paths): the window's tabs follow the renames
        self._moved = moved
        #: check_references(project folder, includes, {old: new}): opens Check Project
        self._check_references = check_references
        self._pairs: List[Pair] = []
        self._pages: List[Page] = []
        #: (folder, original name, current name) of each page renamed here
        self.renamed: List[Pair] = []
        self._undo: List[Pair] = []
        #: the last rename, {old lower case: new}
        self._last_names: Dict[str, str] = {}

        lay = QtWidgets.QVBoxLayout(self)
        self._build_scope_rows(lay, project_dir, fixed)
        self._build_preview(lay)

        self.naming = NamingPanel(
            self.SETTINGS,
            "{name} the page's name, {title} its title, {n} the number in the list ({n:02}: two digits)",
            defaults={"pattern": "{name}", "mode": 1, "links": True, "names": True},
        )
        lay.addWidget(self.naming)
        nm = self.naming
        self._mode, self._pattern, self._start, self._step = nm.mode, nm.pattern, nm.start, nm.step
        self._find, self._repl, self._case = nm.find, nm.repl, nm.case

        self._build_options(lay, nm)

        B = QtWidgets.QDialogButtonBox
        self._buttons = B(self)
        self._ok = self._buttons.addButton("Rename", B.AcceptRole)
        self._ok.setDefault(True)
        self._buttons.addButton(B.Close)
        self._buttons.accepted.connect(self.rename)
        self._buttons.rejected.connect(self.reject)
        lay.addWidget(self._buttons)

        self._connect_signals(nm)
        self.resize(700, 600)
        d = path_key(project_dir) if project_dir else ""
        self._load(ticked={(d, p.lower()) for p in pages})

    def _build_scope_rows(self, lay: QtWidgets.QVBoxLayout, project_dir: str, fixed: bool) -> None:
        """The project and the filter of the pages shown."""
        from ..pick_fields import ProjectScopeField
        from ..style.theme import SearchField

        top = QtWidgets.QFormLayout()
        self._scope = ProjectScopeField(
            os.path.join(project_dir, "_") if project_dir else "",
            what="The project whose pages are renamed",
            horizontal=True,
            remember="rename_pages",
            fixed=fixed,
        )
        top.addRow("Project:", self._scope)
        self._filter = SearchField("Filter pages", regex_key="Rename Pages filter", enter_filters=True)
        self._filter.setToolTip("Show only the pages whose name, title or project contains this")
        self._only_ticked = QtWidgets.QCheckBox("Only ticked")
        self._only_ticked.setToolTip("Show only the pages ticked for renaming")
        show = QtWidgets.QHBoxLayout()
        show.addWidget(self._filter, 1)
        show.addWidget(self._only_ticked)
        top.addRow("Show:", show)
        lay.addLayout(top)

    def _build_preview(self, lay: QtWidgets.QVBoxLayout) -> None:
        """The preview of the pages and their new names, Tick Shown, Untick All and the
        count."""
        self._table = QtWidgets.QTableWidget(0, 4, self)
        self._table.setObjectName("renamePagesPreview")
        self._table.setHorizontalHeaderLabels(["Page", "Project", "Title", "New name"])
        self._table.verticalHeader().hide()
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        from .. import tables

        tables.make_resizable(self._table, "RenamePagesDialog/preview", stretch=3)
        # one text line per row
        self._table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + _sp(2))
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        lay.addWidget(self._table, 1)
        row = QtWidgets.QHBoxLayout()
        self._b_check = QtWidgets.QPushButton("Tick Shown")
        self._b_check.setToolTip("Tick every page the list shows now")
        self._b_clear = QtWidgets.QPushButton("Untick All")
        self._count = QtWidgets.QLabel()
        for w in (self._b_check, self._b_clear):
            w.setAutoDefault(False)
            row.addWidget(w)
        row.addWidget(self._count, 1)
        lay.addLayout(row)

    def _build_options(self, lay: QtWidgets.QVBoxLayout, nm) -> None:
        """What else takes the new names (the links, the project's tables), the status, and
        Check References, Undo Rename and the helper's buttons."""
        from ..style.theme import note_label

        self._links = QtWidgets.QCheckBox("Update the pages that link to them")
        self._links.setToolTip(
            "The Previous, Next and Parent page (Page Properties) of the other pages of the project, "
            "and of the projects it includes, take the new names"
        )
        self._links.setChecked(bool(nm.remembered.get("links", True)))
        lay.addWidget(self._links)
        self._names = QtWidgets.QCheckBox("Update the project's tables that name them")
        self._names.setToolTip(
            "Equipment pages, menu items, alarm help pages and the startup, home and splash page "
            "(Parameters) of the project, and of the projects it includes, take the new names"
        )
        self._names.setChecked(bool(nm.remembered.get("names", True)))
        lay.addWidget(self._names)
        self._status = note_label("", "danger", self)
        self._status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self._status)
        after = QtWidgets.QHBoxLayout()
        self._b_refs = QtWidgets.QToolButton()
        self._b_refs.setText("Check References")
        self._b_refs.setAutoRaise(True)
        self._b_refs.setToolTip(
            "Check the project for Cicode that still opens a page by its old name, such as "
            'PageDisplay("Name"), and give it the new name'
        )
        self._b_undo = QtWidgets.QToolButton()
        self._b_undo.setText("Undo Rename")
        self._b_undo.setAutoRaise(True)
        self._b_undo.setToolTip("Give the pages renamed last their old names again")

        for b in (self._b_refs, self._b_undo):
            b.setVisible(False)
            after.addWidget(b)
        from ..jobs.helper_job import DialogJob

        self._helper = DialogJob(self._status, "Stop: the project being renamed keeps its old names")
        after.addWidget(self._helper.stop_button)
        after.addWidget(self._helper.log_button)
        after.addStretch(1)
        lay.addLayout(after)

    def _connect_signals(self, nm) -> None:
        """The preview follows the naming after a pause in typing; the filter, the ticks,
        the buttons and the project wired."""
        self._typing = QtCore.QTimer(self)
        self._typing.setSingleShot(True)
        self._typing.setInterval(self.TYPING_MS)
        self._typing.timeout.connect(self._preview)
        nm.typed.connect(self._typed)
        nm.changed.connect(self._preview)
        self._filter.searchChanged.connect(lambda _t: self._show_rows())
        self._filter.regex_toggle.toggled.connect(lambda _on: self._show_rows())
        self._only_ticked.toggled.connect(lambda _on: self._show_rows())
        self._table.itemChanged.connect(self._ticked)
        self._b_check.clicked.connect(lambda: self._tick_all(True))
        self._b_clear.clicked.connect(lambda: self._tick_all(False))
        self._b_refs.clicked.connect(self.check_references)
        self._b_undo.clicked.connect(self.undo_rename)
        self._scope.changed.connect(lambda: self._load(ticked=self._ticked_keys()))

    # -- the pages -----------------------------------------------------------------------------
    def project_dirs(self) -> List[str]:
        """The project's folder, and with 'And the projects it includes' theirs."""
        return self._scope.project_dirs()

    @staticmethod
    def _key(d: str, name: str) -> Tuple[str, str]:
        return path_key(d), name.lower()

    def _ticked_keys(self) -> set:
        return {self._key(self._pages[r][0], self._pages[r][1]) for r in self._ticked_rows()}

    def _load(self, ticked=frozenset()) -> None:
        """List the pages (``ticked``: (normcase folder, lower-case name) of those ticked)."""
        dirs = self.project_dirs()
        self._pages = project_pages(dirs)
        t = self._table
        t.blockSignals(True)
        try:
            t.setRowCount(len(self._pages))
            for r, (d, name, title) in enumerate(self._pages):
                for c, text in enumerate((name, _project(d), title, "")):
                    it = QtWidgets.QTableWidgetItem(text)
                    if c == 0:
                        it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                        on = self._key(d, name) in ticked
                        it.setCheckState(QtCore.Qt.Checked if on else QtCore.Qt.Unchecked)
                    t.setItem(r, c, it)
        finally:
            t.blockSignals(False)
        # Project only with more than one project
        t.setColumnHidden(1, len(dirs) < 2)
        self._show_rows()
        self._preview()
        self._scroll_to_ticked()

    def _scroll_to_ticked(self) -> None:
        first = next(iter(self._ticked_rows()), None)
        if first is not None:
            self._table.scrollToItem(self._table.item(first, 0), QtWidgets.QAbstractItemView.PositionAtCenter)

    def showEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().showEvent(ev)
        QtCore.QTimer.singleShot(0, self, self._scroll_to_ticked)

    def _show_rows(self) -> None:
        m = self._filter.matcher()
        only = self._only_ticked.isChecked()
        for r, (d, name, title) in enumerate(self._pages):
            item = self._table.item(r, 0)
            hide = not m(name, title, _project(d)) or (
                only and item is not None and item.checkState() != QtCore.Qt.Checked
            )
            self._table.setRowHidden(r, hide)

    def _ticked_rows(self) -> List[int]:
        return [
            r
            for r in range(self._table.rowCount())
            if self._table.item(r, 0) is not None and self._table.item(r, 0).checkState() == QtCore.Qt.Checked
        ]

    def tick(self, names: Sequence[str], on: bool = True) -> None:
        """Tick (untick) the pages ``names`` (tests, scripting)."""
        want = {n.lower() for n in names}
        for r, (_d, name, _t) in enumerate(self._pages):
            if name.lower() in want:
                self._table.item(r, 0).setCheckState(QtCore.Qt.Checked if on else QtCore.Qt.Unchecked)

    def _tick_all(self, on: bool) -> None:
        self._table.blockSignals(True)
        try:
            for r in range(self._table.rowCount()):
                if on and self._table.isRowHidden(r):
                    continue
                self._table.item(r, 0).setCheckState(QtCore.Qt.Checked if on else QtCore.Qt.Unchecked)
        finally:
            self._table.blockSignals(False)
        self._preview()

    def _ticked(self, item) -> None:
        if item.column() == 0:
            self._preview()

    # -- the names -----------------------------------------------------------------------------
    def new_names(self) -> List[Pair]:
        """[(folder, old, new)] of the ticked pages; ValueError for a bad pattern or expression."""
        out = []
        n, step = self._start.value(), self._step.value()
        for r in self._ticked_rows():
            d, name, title = self._pages[r]
            if self.naming.pattern_mode:
                new = page_pattern_name(self._pattern.text(), name, title, n)
                n += step
            else:
                new = self.naming.replaced(name)
            out.append((d, name, new))
        return out

    def _typed(self, *_a) -> None:
        self._ok.setEnabled(True)
        self._typing.start()

    def flush(self) -> None:
        """Show the preview of what was typed now (not after the pause)."""
        if self._typing.isActive():
            self._preview()

    def _preview(self, *_a) -> None:
        self._typing.stop()
        from ..style.theme import COLORS

        try:
            pairs, error = self.new_names(), ""
        except ValueError as e:
            pairs, error = [], str(e)
        problems = plan_problems(pairs, self._pages) if not error else {}
        new_of = {(d, o): n for d, o, n in pairs}
        t = self._table
        t.blockSignals(True)
        try:
            for r, (d, name, _title) in enumerate(self._pages):
                cell = t.item(r, 3)
                new = new_of.get((d, name))
                cell.setText("" if new is None or new == name else new)
                why = problems.get((d, name), "")
                cell.setToolTip(why)
                colour = COLORS["danger"] if why else None
                cell.setData(QtCore.Qt.ForegroundRole, QtGui.QBrush(QtGui.QColor(colour)) if colour else None)
        finally:
            t.blockSignals(False)
        self._pairs = [p for p in pairs if p[2] != p[1]]
        changes = len(self._pairs)
        ticked = len(pairs)
        self._count.setText("%d of %s ticked" % (ticked, plural(len(self._pages), "page")))
        if error:
            msg = error
        elif problems:
            k = len(problems)
            msg = "%s cannot be used. %s" % (plural(k, "name"), next(iter(problems.values())))
        elif not self._pages:
            msg = "This project has no pages."
        elif not ticked:
            msg = "Tick the pages to rename."
        elif not changes:
            msg = "No name changes."
        else:
            msg = ""
        if msg or not self._undo:
            self._helper.say(msg, failed=bool(error or problems), log=False)
        self._ok.setEnabled(bool(changes) and not error and not problems and not self._helper.running())

    # -- renaming ------------------------------------------------------------------------------
    def rename(self) -> bool:
        """Rename the ticked pages (the Rename button), in a helper process. True when it
        started."""
        self.flush()
        if not self._ok.isEnabled():
            return False
        ok = self._run(list(self._pairs))
        if ok:
            self.naming.remember(links=self._links.isChecked(), names=self._names.isChecked())
        return ok

    def undo_rename(self) -> bool:
        """Give the pages renamed last their old names again (Undo Rename)."""
        pairs = [(d, new, old) for d, old, new in self._undo]
        if not pairs or self._helper.running():
            return False
        return self._run(pairs, undo=True)

    def renaming(self) -> bool:
        return self._helper.running()

    def stop(self) -> None:
        self._helper.stop()

    def finish(self) -> None:
        """Wait for the rename running and take its answer now."""
        if self._helper.job is not None:
            self._helper.job.finish_now()

    def _sync_busy(self) -> None:
        busy = self._helper.running()
        self._b_undo.setEnabled(not busy)
        if busy:
            self._ok.setEnabled(False)

    def _run(self, pairs: List[Pair], undo: bool = False) -> bool:
        from ...infra.paths import data_dir
        from ...workers import rename_worker as RW
        from ..jobs.helper_job import HelperJob

        if self._helper.running():
            return False
        busy = [o for d, o, _n in pairs if _in_builder(os.path.join(d, o + ".ctg"))]
        if busy:
            self._helper.say(
                "Open in the Graphics Builder: %s. Close %s there first, or its next save writes the "
                "old name back." % (", ".join(busy[:5]), "it" if len(busy) == 1 else "them"),
                failed=True,
                log=False,
            )
            return False
        held = [os.path.join(d, o + ".ctg") for d, o, _n in pairs]
        req = {
            "format": RW.FORMAT,
            "pairs": [list(p) for p in pairs],
            "links": self._links.isChecked(),
            "names": self._names.isChecked(),
            "link_dirs": self.project_dirs(),
        }
        job = HelperJob(self, RW.command, req, data_dir("rename"), label="Rename Pages")
        if self._hold is not None:
            self._hold(held)
        if not self._helper.run(
            job,
            finished=lambda res: self._renamed(res, pairs, held, undo),
            failed="The pages could not be renamed.",
            progress=lambda _i, _n, what: "Renaming pages (%s)…" % what,
        ):
            if self._moved is not None:
                self._moved([], held)
            return False
        self._helper.say("Renaming pages…")
        self._sync_busy()
        return True

    def _renamed(self, res: dict, pairs: List[Pair], held: List[str], undo: bool) -> None:
        from .. import lifecycle

        done: List[Pair] = [(d, o, n) for d, o, n in res.get("done") or ()]
        failed = list(res.get("failed") or ()) + list(res.get("errors") or ())
        if res.get("error"):
            failed.append(str(res["error"]))
        if self._moved is not None:
            self._moved(
                [(os.path.join(d, o + ".ctg"), os.path.join(d, n + ".ctg")) for d, o, n in done], held
            )
        for d in {d for d, _o, _n in done}:
            lifecycle.touch_catalog(None, d)
        for d, o, n in done:
            k = next(
                (i for i, (rd, _a, b) in enumerate(self.renamed) if self._key(rd, b) == self._key(d, o)),
                None,
            )
            if k is None:
                self.renamed.append((d, o, n))
            else:
                self.renamed[k] = (d, self.renamed[k][1], n)
        self.renamed = [r for r in self.renamed if r[1] != r[2]]
        renamed = {self._key(d, n) for d, _o, n in done}
        self._undo = [] if undo else done
        self._last_names = {} if undo else {o.lower(): n for _d, o, n in done}
        kept = {self._key(d, o) for d, o, _n in pairs if self._key(d, o) not in renamed}
        self._load(ticked=renamed | kept)
        parts = []
        k = len(done)
        if k:
            parts.append(
                "%s %d page%s%s."
                % (
                    "Put back" if undo else "Renamed",
                    k,
                    "" if k == 1 else "s",
                    " to their old names" if undo else "",
                )
            )
            changed = res.get("changed") or ()
            if changed:
                parts.append(
                    "%d reference%s in the project's tables followed."
                    % (len(changed), "" if len(changed) == 1 else "s")
                )
            parts.append("Compile the project to use the new names.")
            if not undo:
                parts.append(
                    "Cicode that opens the old pages keeps the old names: Check References finds it."
                )
        if res.get("stopped"):
            parts.append("Stopped: the pages not renamed keep their names.")
        if failed:
            parts.append("Not renamed: %s." % "; ".join(failed[:3]))
        self._helper.say(" ".join(parts) or "Nothing was renamed.", failed=bool(failed))
        self._b_undo.setVisible(bool(self._undo))
        self._b_refs.setVisible(bool(self._last_names) and self._check_references is not None)
        self._sync_busy()

    def done(self, r) -> None:
        self._helper.close(wait=True)  # (the pages renamed so far: the window's tabs follow them)
        super().done(r)

    def check_references(self) -> None:
        """Check Project over the pages renamed, with their new names to give the references."""
        if self._check_references is not None and self._last_names:
            self._check_references(
                self._scope.project_dir(), self._scope.includes.isChecked(), dict(self._last_names)
            )
