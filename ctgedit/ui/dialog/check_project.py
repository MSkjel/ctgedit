"""Tools > Check Project: Check Page on every page of a project (and of the projects it
includes), and after Rename Pages the page references it finds given their new names."""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtWidgets


_LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}


class CheckProjectDialog(QtWidgets.QDialog):
    """The findings of every page of a project, from a helper process (workers/check_worker):
    Check Page's checks and the engine's check of each expression and command. A row goes to
    its object. Given ``renames`` (Rename Pages: {old name, lower case: new name}), the
    references to the old pages it finds can be given the new names (Replace References)."""

    def __init__(
        self,
        parent=None,
        project_dir: str = "",
        *,
        renames: Optional[Dict[str, str]] = None,
        includes: bool = False,
        documents: Optional[Callable] = None,
        open_page: Optional[Callable] = None,
        goto: Optional[Callable] = None,
        replaced: Optional[Callable] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Check Project")
        #: Rename Pages' names: {old, lower case: new}
        self.renames = {k.lower(): v for k, v in (renames or {}).items()}
        #: the open documents (their pages are replaced in their tabs)
        self._documents = documents or (lambda: [])
        #: open_page(path) -> its document (None: not opened); goto(doc, index)
        self._open_page, self._goto = open_page, goto
        #: replaced(docs): open pages the replace changed (the window redraws them)
        self._replaced = replaced
        self._rows: List[dict] = []
        self._job = None
        #: what Replace References did in the open pages: (replaced, read only, problems)
        self._open_result = (0, 0, [])
        #: what the last replace said, put before the next check's line
        self._after_check = ""
        from ..pick_fields import ProjectScopeField
        from ..style.theme import SearchField, note_label

        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QFormLayout()
        self._scope = ProjectScopeField(
            os.path.join(project_dir, "_") if project_dir else "",
            what="The project whose pages are checked",
            horizontal=True,
        )
        self._scope.includes.setChecked(bool(includes))
        top.addRow("Project:", self._scope)
        self._filter = SearchField("Filter findings", regex_key="Check Project filter")
        self._filter.setToolTip("Show only the findings whose page, object or text contains this")
        self._only_refs = QtWidgets.QCheckBox("Only references to renamed pages")
        self._only_refs.setVisible(bool(self.renames))
        self._only_refs.setChecked(bool(self.renames))
        show = QtWidgets.QHBoxLayout()
        show.addWidget(self._filter, 1)
        show.addWidget(self._only_refs)
        top.addRow("Show:", show)
        lay.addLayout(top)

        self._list = QtWidgets.QTreeWidget()
        self._list.setColumnCount(3)
        self._list.setHeaderLabels(["Page", "Object", "Problem"])
        self._list.setRootIsDecorated(False)
        self._list.setUniformRowHeights(True)
        self._list.setAlternatingRowColors(True)
        self._list.setMinimumHeight(self._list.fontMetrics().height() * 12)
        from ..tables import enable_row_copy, make_resizable

        make_resizable(self._list, "CheckProjectDialog/findings")
        enable_row_copy(self._list)
        self._list.itemDoubleClicked.connect(lambda *_a: self.go_to())
        self._list.currentItemChanged.connect(lambda *_a: self._sync_buttons())
        lay.addWidget(self._list, 1)

        self._status = note_label("", "text_dim", self)
        self._status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        srow = QtWidgets.QHBoxLayout()
        srow.addWidget(self._status, 1)
        self._b_stop = QtWidgets.QToolButton()
        self._b_stop.setText("Stop")
        self._b_stop.setVisible(False)
        self._b_stop.clicked.connect(self.stop)
        srow.addWidget(self._b_stop)
        lay.addLayout(srow)

        btns = QtWidgets.QHBoxLayout()
        self._b_check = QtWidgets.QPushButton("Check")
        self._b_check.setDefault(True)
        self._b_check.setToolTip("Check every page of the project, as saved")
        self._b_check.clicked.connect(self.check)
        self._b_goto = QtWidgets.QPushButton("Go To")
        self._b_goto.setToolTip("Open the page of the selected row and select its object")
        self._b_goto.clicked.connect(self.go_to)
        self._b_replace = QtWidgets.QPushButton("Replace References")
        self._b_replace.setToolTip(
            "Give every reference listed to a renamed page the page's new name. The closed "
            "pages are saved, each backed up first (Tools > Restore Backup)"
        )
        self._b_replace.clicked.connect(self.replace_references)
        self._b_replace.setVisible(bool(self.renames))
        for b in (self._b_check, self._b_goto, self._b_replace):
            btns.addWidget(b)
        btns.addStretch(1)
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        box.button(QtWidgets.QDialogButtonBox.Close).setAutoDefault(False)
        box.rejected.connect(self.reject)
        btns.addWidget(box)
        lay.addLayout(btns)

        self._filter.searchChanged.connect(lambda _t: self._fill())
        self._filter.regex_toggle.toggled.connect(lambda _on: self._fill())
        self._only_refs.toggled.connect(lambda _on: self._fill())
        self._scope.changed.connect(self._scope_changed)
        self.setMinimumWidth(640)
        self.resize(900, 560)
        self._sync_buttons()

    # -- the rows ------------------------------------------------------------------------
    def rows(self) -> List[dict]:
        """The findings (check_worker's rows), as the list holds them."""
        return list(self._rows)

    def references(self) -> List[dict]:
        """The rows that are references to a renamed page (a page the project does not have,
        whose name Rename Pages gave away)."""
        return [
            r for r in self._rows if r.get("code") == "page" and r.get("subject", "").lower() in self.renames
        ]

    def _shown(self) -> List[dict]:
        rows = self.references() if self._only_refs.isChecked() else self._rows
        m = self._filter.matcher()
        return [r for r in rows if m(r["name"], r["object"], r["message"], r.get("project", ""))]

    def _fill(self) -> None:
        from ..notify import level_icon

        self._list.clear()
        items = []
        for r in self._shown():
            it = QtWidgets.QTreeWidgetItem([r["name"], r["object"], r["message"]])
            it.setIcon(2, level_icon(r["level"]))
            it.setToolTip(0, r["page"])
            it.setToolTip(2, r["message"])
            it.setData(0, QtCore.Qt.UserRole, r)
            items.append(it)
        self._list.addTopLevelItems(items)
        from ..tables import refit

        refit(self._list)
        self._sync_buttons()

    def current_row(self) -> Optional[dict]:
        it = self._list.currentItem()
        return it.data(0, QtCore.Qt.UserRole) if it is not None else None

    def _sync_buttons(self) -> None:
        busy = self._job is not None
        self._b_check.setEnabled(not busy and bool(self._scope.project_dirs()))
        self._b_goto.setEnabled(self.current_row() is not None)
        self._b_replace.setEnabled(not busy and bool(self.references()))
        self._b_stop.setVisible(busy)

    def _scope_changed(self) -> None:
        self._rows = []
        self._fill()
        self._status.setText("")

    # -- checking ----------------------------------------------------------------------------
    def check(self) -> bool:
        """Check the project's pages in a helper process (False: one runs already)."""
        from ...infra.paths import data_dir
        from ...workers import check_worker as CW
        from ..jobs.helper_job import HelperJob
        from ..simulate.window import engine_setting

        if self._job is not None:
            return False
        req = {"format": CW.FORMAT, "projects": self._scope.project_dirs(), "engine": engine_setting() or ""}
        job = HelperJob(self, CW.command, req, data_dir("check"), label="Check Project")
        job.progress.connect(
            lambda i, n, name: self._status.setText("Checking page %d of %d (%s)…" % (i + 1, n, name))
        )
        job.finished.connect(lambda res: self._checked(job, res))
        if not job.start():
            self._status.setText("The pages could not be checked: the helper process did not start.")
            return False
        self._job = job
        self._rows = []
        self._fill()
        return True

    def stop(self) -> None:
        if self._job is not None:
            self._job.stop()
            self._status.setText("Stopping…")

    def searching(self) -> bool:
        return self._job is not None

    def _checked(self, job, res: dict) -> None:
        if job is not self._job:
            return
        self._job = None
        self.take(res)

    def take(self, res: dict) -> None:
        """Show a check's answer (check_worker.check)."""
        if res.get("error"):
            self._rows = []
            self._fill()
            self._status.setText("The pages could not be checked: %s" % res["error"])
            return
        rows = list(res.get("rows") or ())
        rows.sort(
            key=lambda r: (
                _LEVEL_ORDER.get(r["level"], 3),
                r["project"].lower(),
                r["name"].lower(),
                r["index"],
            )
        )
        self._rows = rows
        self._fill()
        parts = []
        n = int(res.get("checked") or 0)
        parts.append(
            "%d finding%s on %d page%s checked."
            % (len(rows), "" if len(rows) == 1 else "s", n, "" if n == 1 else "s")
        )
        if self.renames:
            k = len(self.references())
            parts.append("%d reference%s to renamed pages." % (k, "" if k == 1 else "s"))
        if res.get("more"):
            parts.append("Only the first %d are listed." % len(rows))
        for proj, why in (res.get("engine") or {}).items():
            parts.append("The Cicode of %s was not compiled: %s." % (proj, str(why).rstrip(".")))
        if res.get("failed"):
            parts.append("Not read: %s." % "; ".join(res["failed"][:3]))
        if res.get("stopped"):
            parts.append("Stopped before every page was checked.")
        before, self._after_check = self._after_check, ""
        self._status.setText(" ".join(([before] if before else []) + parts))

    # -- going to a finding --------------------------------------------------------------------
    def go_to(self) -> None:
        r = self.current_row()
        if r is None or self._open_page is None:
            return
        doc = self._open_page(r["page"])
        if doc is None:
            self._status.setText("%s could not be opened. The window's status bar says why." % r["name"])
            return
        if self._goto is not None:
            self._goto(doc, int(r["index"]))

    # -- replacing the references ------------------------------------------------------------
    def _open_doc(self, path: str):
        key = os.path.normcase(os.path.abspath(path))
        for d in self._documents() or ():
            if getattr(d, "path", "") and os.path.normcase(os.path.abspath(d.path)) == key:
                return d
        return None

    def replace_references(self) -> bool:
        """Give each listed reference to a renamed page the page's new name: the open pages in
        their tabs (one undo step each), the closed ones in a helper process, each backed up
        and saved. False: nothing to do, or one runs already."""
        from ...workers import check_worker as CW

        refs = self.references()
        if not refs or self._job is not None:
            return False
        edits: Dict[str, list] = {}
        for r in refs:
            if r["an"] < 1 or not r["field"] or int(r.get("column") or 0) < 1:
                continue
            edits.setdefault(r["page"], []).append(
                {
                    "page": r["page"],
                    "name": r["name"],
                    "an": r["an"],
                    "field": r["field"],
                    "old": r["subject"],
                    "new": self.renames[r["subject"].lower()],
                    "column": r.get("column", 0),
                }
            )
        changed, replaced, read_only, problems = [], 0, 0, []
        closed = []
        for path, es in edits.items():
            doc = self._open_doc(path)
            if doc is None:
                closed.extend(es)
                continue
            rep = CW.replace_in(doc, es)
            replaced += rep.replaced
            read_only += rep.read_only
            problems += rep.problems
            if rep.replaced:
                changed.append(doc)
        if changed and self._replaced is not None:
            self._replaced(changed)
        self._open_result = (replaced, read_only, problems)
        if not closed:
            self._replace_done({})
            return True
        from ...infra import backups
        from ...infra.paths import data_dir
        from ..jobs.helper_job import HelperJob, builder_open_pages

        req = {
            "format": CW.FORMAT,
            "mode": "replace",
            "edits": closed,
            "backup": backups.new_folder("references"),
            "builder_open": builder_open_pages(),
        }
        job = HelperJob(self, CW.command, req, data_dir("check"), label="Replace References")
        job.progress.connect(
            lambda i, n, name: self._status.setText("Replacing in page %d of %d (%s)…" % (i + 1, n, name))
        )
        job.finished.connect(lambda res: self._replaced_closed(job, res))
        if not job.start():
            self._status.setText("The closed pages could not be changed: the helper process did not start.")
            return False
        self._job = job
        self._sync_buttons()
        return True

    def _replaced_closed(self, job, res: dict) -> None:
        if job is not self._job:
            return
        self._job = None
        self._replace_done(res)

    def _replace_done(self, res: dict) -> None:
        """Say what Replace References did, then check again (the list shows what is left)."""
        replaced, read_only, problems = self._open_result
        replaced += int(res.get("replaced") or 0)
        read_only += int(res.get("read_only") or 0)
        problems = problems + list(res.get("problems") or ())
        parts = [
            "%d reference%s given the new name%s."
            % (replaced, "" if replaced == 1 else "s", "" if replaced == 1 else "s")
        ]
        if read_only:
            parts.append(
                "%d cannot be changed here (a genie's definition or parameter holds the name: "
                "change it there)." % read_only
            )
        if res.get("updated"):
            parts.append(
                "Saved %d closed page%s, each backed up first."
                % (len(res["updated"]), "" if len(res["updated"]) == 1 else "s")
            )
        if res.get("failed"):
            parts.append("Could not change: %s." % "; ".join(res["failed"][:3]))
        if res.get("open_in_builder"):
            parts.append("Left (open in the Graphics Builder): %s." % ", ".join(res["open_in_builder"]))
        parts.extend(problems[:3])
        if res.get("error"):
            parts.append("The closed pages could not be changed: %s" % res["error"])
        said = " ".join(parts)
        self._sync_buttons()
        if self.check():
            self._after_check = said
        else:
            self._status.setText(said)

    def done(self, r) -> None:
        if self._job is not None:
            self._job.stop()
            self._job = None
        super().done(r)
