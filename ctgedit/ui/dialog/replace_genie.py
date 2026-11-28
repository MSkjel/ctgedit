"""Edit > Library > Replace Genie."""

from __future__ import annotations

from ...infra.paths import path_key

from ..names import name_list, plural

import contextlib
import os
from collections import OrderedDict
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtWidgets

from ...doc import genieswap as GS
from ..jobs.helper_job import builder_open_pages, open_documents

Link = Tuple[str, str]


def _name(link: Link) -> str:
    return "%s.%s" % link


def _names_of(links: Sequence[Link]) -> str:
    return name_list([_name(l) for l in links], 8)


def _low(link: Link) -> Link:
    return link[0].lower(), link[1].lower()


class ReplaceGenieDialog(QtWidgets.QDialog):
    """Replace Genie. ``doc`` = the page's (or library genie's) document, ``selection`` = the
    selected record indexes. After OK: :meth:`old_genies`, :meth:`targets`,
    :meth:`new_genie`, :meth:`mapping`, :meth:`old_params`, :meth:`keep`,
    :meth:`project_wide`, :meth:`project_dirs`."""

    def __init__(self, doc, selection: Sequence[int] = (), parent=None):
        super().__init__(parent)
        from ..style import theme
        from ..pick_fields import LibraryElementField, ProjectScopeField

        self.doc = doc
        page = doc.page.name if doc.page is not None else ""
        self.setWindowTitle("Replace Genie - %s" % page if page else "Replace Genie")
        self._all = doc.replaceable_genies()
        self._picked = doc.replaceable_genies(list(selection)) if selection else []
        kinds: "OrderedDict[Link, List[int]]" = OrderedDict()
        for link in [l for _i, l in self._picked] + [l for _i, l in self._all]:
            kinds.setdefault(link, [])
        for i, link in self._all:
            kinds[link].append(i)
        self._kinds = kinds
        self._kinds_low = {_low(l): r for l, r in kinds.items()}
        self._params_of: Dict[Link, List[str]] = {}
        self._old_params: List[str] = []
        self._new_decl: "OrderedDict[str, str]" = OrderedDict()

        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        # the genies to replace: tick one or several (all become the one new genie), placed
        # here or not (those not placed here are replaced on the project's pages)
        from ..pick_fields import GenieChecklist

        picked = [l for _i, l in self._picked]
        first = picked or (list(kinds) if len(kinds) == 1 else [])
        self.checklist = GenieChecklist(doc.path or "", {l: len(r) for l, r in kinds.items()}, ticked=first)
        self.checklist.setToolTip("Tick the genies to replace. They all become the new genie")
        form.addRow("Replace:", self.checklist)
        which = QtWidgets.QWidget()
        wl = QtWidgets.QVBoxLayout(which)
        wl.setContentsMargins(0, 0, 0, 0)
        self.rb_selected = QtWidgets.QRadioButton()
        self.rb_every = QtWidgets.QRadioButton()
        self.rb_project = QtWidgets.QRadioButton("All pages of the project:")
        self.rb_project.setToolTip(
            "Every page and library genie of the project, open or not. "
            "What is not open is changed in the background and saved, "
            "backed up first (Tools > Restore Backup puts it back)"
        )
        self.scope = ProjectScopeField(
            doc.path or "",
            what="The project whose pages and library genies are changed",
            remember="replace_genie",
        )
        self.cb_project, self.cb_includes = self.scope.combo, self.scope.includes
        self.rb_project.setEnabled(self.scope.has_projects())
        for rb in (self.rb_selected, self.rb_every, self.rb_project):
            wl.addWidget(rb)
        wl.addWidget(self.scope)
        form.addRow("Which:", which)
        start = os.path.dirname(os.path.abspath(doc.path)) if doc.path else None
        self.new_field = LibraryElementField(start, "genie", self, title="Select Genie")
        self.new_field.btn_browse.setToolTip("Choose the genie to put in their place")
        form.addRow("With:", self.new_field)
        lay.addLayout(form)

        lay.addWidget(QtWidgets.QLabel("Parameters of the new genie:"))
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Parameter", "Value", "Comes from"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.table, 1)
        from ..tables import make_resizable

        make_resizable(self.table, "ReplaceGenieDialog/parameters", stretch=1)
        self.hint = theme.note_label(
            "Type %Name% to use the value the old genie had for Name. A blank value takes the default."
        )
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        self.dropped = theme.note_label("", "warn")
        self.dropped.setWordWrap(True)
        lay.addWidget(self.dropped)
        # where the new genie goes: its hot spot (the cross of the genie's window) where the
        # old one's was, or its top-left corner where the old one's was
        line = QtWidgets.QHBoxLayout()
        line.addWidget(QtWidgets.QLabel("Line up by:"))
        self.rb_hotspot = QtWidgets.QRadioButton("Hot spot")
        self.rb_hotspot.setToolTip(
            "The new genie's hot spot (the cross in the genie's "
            "window) goes where the old genie's hot spot was"
        )
        self.rb_topleft = QtWidgets.QRadioButton("Top-left corner")
        self.rb_topleft.setToolTip(
            "The new genie's top-left corner goes where the old genie's top-left corner was"
        )
        from ..prefs.settings import app_settings

        (
            self.rb_topleft
            if app_settings().value("edit/replace_genie_topleft", False, type=bool)
            else self.rb_hotspot
        ).setChecked(True)
        self.rb_topleft.toggled.connect(
            lambda on: app_settings().setValue("edit/replace_genie_topleft", bool(on))
        )
        self._line_group = QtWidgets.QButtonGroup(self)  # (apart from Which)
        self._line_group.addButton(self.rb_hotspot)
        self._line_group.addButton(self.rb_topleft)
        line.addWidget(self.rb_hotspot)
        line.addWidget(self.rb_topleft)
        line.addStretch(1)
        lay.addLayout(line)
        self.status = theme.note_label("", "danger")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.ok = self.buttons.button(QtWidgets.QDialogButtonBox.Ok)
        self.ok.setText("Replace")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        self.checklist.changed.connect(self._old_changed)
        for rb in (self.rb_selected, self.rb_every, self.rb_project):
            rb.toggled.connect(self._validate)
        self.rb_project.toggled.connect(self._sync_project)
        self.scope.changed.connect(self._validate)
        self._sync_project()
        self.new_field.changed.connect(self._new_changed)
        self._old_changed()
        self._new_changed()

    # -- the parts ------------------------------------------------------------------
    def old_genies(self) -> List[Link]:
        """The genies ticked (placed here or not), in the tree's order."""
        return self.checklist.ticked()

    def set_old_genies(self, links) -> None:
        """Tick exactly ``links`` (tests, callers)."""
        self.checklist.set_ticked(links)

    def _placed(self, link: Link) -> List[int]:
        """The roots of genie ``link``'s instances here (its spelling not minding case)."""
        return self._kinds_low.get(_low(link), [])

    def targets(self) -> List[int]:
        """The roots of the instances to replace here."""
        olds = {_low(l) for l in self.old_genies()}
        if self.rb_selected.isChecked():
            return sorted(i for i, l in self._picked if _low(l) in olds)
        return sorted(i for l in olds for i in self._kinds_low.get(l, []))

    def project_wide(self) -> bool:
        """Every page and library genie of :meth:`project_dirs` (what is open, in its
        tab)."""
        return self.rb_project.isChecked()

    def project_dirs(self) -> List[str]:
        return self.scope.project_dirs()

    def new_genie(self) -> Optional[Link]:
        return self.new_field.value()

    def keep(self) -> str:
        return "topleft" if self.rb_topleft.isChecked() else "origin"

    def old_params(self) -> List[str]:
        """The parameters of every genie ticked (replace_genies' ``old_params``)."""
        return list(self._old_params)

    def mapping(self) -> Dict[str, str]:
        """{new parameter: value text} of the rows with a value (blank rows: the default)."""
        out: Dict[str, str] = {}
        for r in range(self.table.rowCount()):
            name = self.table.item(r, 0).text()
            text = self._value_box(r).currentText().strip()
            if text:
                out[name] = text
        return out

    def _value_box(self, row: int) -> QtWidgets.QComboBox:
        return self.table.cellWidget(row, 1)

    def _sync_project(self, *_a) -> None:
        self.scope.setEnabled(self.rb_project.isChecked())

    # -- reactions ------------------------------------------------------------------
    def _params(self, link: Link) -> List[str]:
        """Genie ``link``'s parameters: what its definition declares (when it can be read),
        then what its instances here bind."""
        got = self._params_of.get(link)
        if got is None:
            try:
                decl = list(self.doc.genie_parameters(link[0], link[1], "ctm"))
            except Exception:  # noqa: BLE001 -- swallow-ok: a missing library: the bindings alone
                decl = []
            got = decl + [k for i in self._placed(link) for k in self.doc.genie_instance_bindings(i)]
            self._params_of[link] = got
        return got

    def _old_changed(self) -> None:
        olds = {_low(l) for l in self.old_genies()}
        n_sel = len([1 for _i, l in self._picked if _low(l) in olds])
        n_all = sum(len(self._kinds_low.get(l, [])) for l in olds)
        self.rb_selected.setText("Selected (%d)" % n_sel)
        self.rb_every.setText("Every one here (%d)" % n_all)
        self.rb_selected.setEnabled(n_sel > 0)
        self.rb_every.setEnabled(n_all > 0)
        if olds and not n_all and self.rb_project.isEnabled():
            # (none of the genies ticked is placed here: they are replaced where they are)
            self.rb_project.setChecked(True)
        elif not self.rb_project.isChecked() or not n_all:
            if n_sel or n_all:
                (self.rb_selected if n_sel else self.rb_every).setChecked(True)
        names: List[str] = []
        seen = set()
        for link in self.old_genies():
            for k in self._params(link):
                if GS.is_param(k) and k.lower() not in seen:
                    seen.add(k.lower())
                    names.append(k)
        self._old_params = names
        self._fill_rows()

    def _new_changed(self) -> None:
        link = self.new_genie()
        self._new_decl = OrderedDict()
        if link is not None:
            try:
                self._new_decl = self.doc.genie_parameters(link[0], link[1], "ctm")
            except Exception:  # noqa: BLE001 -- swallow-ok: a genie that cannot be read lists no parameters
                self._new_decl = OrderedDict()
        self._fill_rows()

    def _fill_rows(self) -> None:
        keep = {}
        for r in range(self.table.rowCount()):
            keep[self.table.item(r, 0).text().lower()] = self._value_box(r).currentText()
        names = [n for n in self._new_decl if GS.is_param(n)]
        suggested = GS.suggested_mapping(self._old_params, names)
        self.table.setRowCount(len(names))
        tokens = [""] + ["%" + p + "%" for p in self._old_params]
        for r, name in enumerate(names):
            self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(name))
            box = QtWidgets.QComboBox()
            box.setEditable(True)
            box.addItems(tokens)
            dflt = self._new_decl.get(name) or ""
            box.lineEdit().setPlaceholderText("Default: %s" % dflt if dflt else "Blank")
            box.setCurrentText(keep.get(name.lower(), suggested.get(name, "")))
            box.currentTextChanged.connect(self._validate)
            self.table.setCellWidget(r, 1, box)
            self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(""))
        self._validate()

    def _validate(self, *_a) -> None:
        low_old = {p.lower() for p in self._old_params}
        used = set()
        for r in range(self.table.rowCount()):
            text = self._value_box(r).currentText()
            toks = GS.mapping_tokens({"": text})
            used.update(t.lower() for t in toks)
            if not text.strip():
                dflt = self._new_decl.get(self.table.item(r, 0).text()) or ""
                src = "Default" if dflt else "Not set"
            elif toks and all(t.lower() in low_old for t in toks):
                src = "Old " + ", ".join(toks)
            elif toks:
                src = "Unknown: " + ", ".join(t for t in toks if t.lower() not in low_old)
            else:
                src = "Fixed value"
            self.table.item(r, 2).setText(src)
        left = [p for p in self._old_params if p.lower() not in used]
        self.dropped.setText("Not carried over: %s." % ", ".join(left) if left else "")
        why, conflict = "", False
        olds, new = self.old_genies(), self.new_genie()
        if not olds:
            why = "Tick the genies to replace."
        elif new is None:
            why = "Choose the genie to put in their place."
        elif _low(new) in {_low(l) for l in olds}:
            why, conflict = "The new genie is one of those ticked: untick it, or choose another.", True
        elif self.rb_project.isChecked() and not self.project_dirs():
            why = "Choose a project."
        elif not self.rb_project.isChecked() and not self.targets():
            why = "None of the genies ticked is placed here. Choose All pages of the project."
        from ..style.theme import set_note_role

        # (what is still to choose is a hint; only a conflict is an error)
        set_note_role(self.status, "danger" if conflict else "text_dim")
        self.status.setText(why)
        self.ok.setEnabled(not why)


def _element_key(path: str, element: str) -> Tuple[str, str]:
    return path_key(path), element.lower()


def _doc_label(d) -> str:
    stem = os.path.splitext(os.path.basename(d.path))[0]
    el = getattr(d, "element_name", "")
    return "%s.%s" % (stem, el) if el else stem


def open_page_docs(dirs) -> list:
    """(window, document) of the pages of these project folders open in an editor."""
    return open_documents(dirs, elements=False)


def open_element_docs(dirs) -> list:
    """(window, document) of the library elements of these project folders open in an
    editor."""
    return open_documents(dirs, elements=True)


class ProjectReplaceRun(QtCore.QObject):
    """Replace Genie across projects."""

    #: the run going on (one at a time)
    active: Optional["ProjectReplaceRun"] = None

    def __init__(
        self,
        win,
        dirs: List[str],
        old: Sequence[Link],
        new: Link,
        mapping: Dict[str, str],
        keep: str,
        old_params: Sequence[str] = (),
        confirm=None,
    ):
        super().__init__(win)
        self.win, self.dirs, self.new = win, list(dirs), tuple(new)
        self.old = [tuple(l) for l in old]
        self.mapping, self.keep = dict(mapping), keep
        self.old_params = list(old_params)
        #: ``confirm(text, details) -> bool`` (tests answer it); default: a question box
        self._confirm = confirm or self._ask
        self.job = None
        self.changed_open: List[str] = []
        #: those of :attr:`changed_open` that are library genies
        self.changed_open_elements: List[str] = []
        self.failed_open: List[str] = []
        self.found: dict = {}
        self._editor_file = ""
        #: the result text and the helper's answer (tests read them)
        self.result_text = ""
        self.answer: dict = {}

    def _base(self) -> dict:
        """What both requests carry: the genies to replace and the projects."""
        return {"old": [list(l) for l in self.old], "projects": self.dirs}

    # -- the helper ------------------------------------------------------------------
    def _run(self, req: dict, label: str, done) -> bool:
        from ...workers import genieswap_worker as GW
        from ...infra.paths import data_dir
        from ..jobs.helper_job import HelperJob

        job = HelperJob(
            self,
            GW.command,
            dict(req, format=GW.FORMAT),
            data_dir("replacegenie"),
            label=label,
            tell=False,
            skip_paths=lambda: [d.path for _w, d in open_page_docs(self.dirs)],
        )

        def finished(res):
            self.job = None
            if res.get("error"):
                self._end()
                self.result_text = "%s failed: %s" % (label, res["error"])
                self._tell(self.result_text, error=True)
                return
            done(res)

        job.finished.connect(finished)
        if not job.start():
            self.result_text = "The pages could not be read."
            self._tell(self.result_text, error=True)
            return False
        self.job = job
        self.win.statusBar().showMessage(label + "…", 4000)
        return True

    def start(self) -> bool:
        if ProjectReplaceRun.active is not None:
            self.win.statusBar().showMessage("Replace Genie is already running.", 5000)
            return False
        # (one run that writes libraries or pages at a time: Update Pages, its chain, the
        # chain after a genie save and their open tabs' step)
        from ..jobs.update_run import UpdatePagesRun

        why = UpdatePagesRun.busy_text("Run Replace Genie")
        if why:
            self.win.statusBar().showMessage(why, 8000)
            return False
        ProjectReplaceRun.active = self
        ok = self._run(
            dict(self._base(), mode="find"), "Finding what uses %s" % _names_of(self.old), self._found
        )
        if not ok:
            self._end()
        return ok

    def busy(self) -> bool:
        return ProjectReplaceRun.active is self

    def _end(self) -> None:
        if ProjectReplaceRun.active is self:
            ProjectReplaceRun.active = None
        if self._editor_file:
            with contextlib.suppress(OSError):
                os.remove(self._editor_file)
            self._editor_file = ""

    def _ask(self, text: str, details: str) -> bool:
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Question,
            "Replace Genie",
            text,
            QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
            self.win,
        )
        box.button(QtWidgets.QMessageBox.Ok).setText("Replace")
        box.setDefaultButton(QtWidgets.QMessageBox.Ok)
        box.setDetailedText(details)
        return box.exec() == QtWidgets.QMessageBox.Ok

    def _tell(self, text: str, error: bool = False, actions=()) -> None:
        if getattr(self.win, "_replace_genie_quiet", False):
            return  # (tests)
        from .. import messages

        (messages.error_box if error else messages.info_box)(self.win, "Replace Genie", text, actions=actions)

    # -- the steps -------------------------------------------------------------------
    def _found(self, res: dict) -> None:
        self.found = res
        # what is open in an editor is changed in its tab
        open_pages = open_page_docs(self.dirs)
        open_page_keys = {path_key(d.path) for _w, d in open_pages}
        open_els = open_element_docs(self.dirs)
        open_el_keys = {_element_key(d.path, getattr(d, "element_name", "")) for _w, d in open_els}
        closed = [p for p in res.get("pages") or () if path_key(p["page"]) not in open_page_keys]
        closed_els = [
            e
            for e in res.get("elements") or ()
            if _element_key(e["library"], e["element"]) not in open_el_keys
        ]
        open_hits = []
        for w, d in open_pages + open_els:
            roots = self._roots(d)
            if roots:
                open_hits.append((w, d, roots))
        n_genies = (
            sum(p["count"] for p in closed)
            + sum(e["count"] for e in closed_els)
            + sum(len(r) for _w, _d, r in open_hits)
        )
        n_open_els = len([1 for _w, d, _r in open_hits if getattr(d, "element_name", "")])
        n_pages = len(closed) + len(open_hits) - n_open_els
        n_els = len(closed_els) + n_open_els
        if res.get("stopped") or not (n_pages or n_els):
            self._end()
            self.result_text = (
                "Stopped while finding what uses the genies."
                if res.get("stopped")
                else "Nothing in %s places %s."
                % (", ".join(os.path.basename(d) for d in self.dirs), _names_of(self.old))
            )
            if res.get("shipped"):
                self.result_text += (
                    " What is supplied with Plant SCADA places it, and is "
                    "never changed: %s." % name_list(res["shipped"], 8)
                )
            self.win.statusBar().showMessage(self.result_text, 8000)
            return
        where = []
        if n_pages:
            where.append("on " + plural(n_pages, "page"))
        if n_els:
            where.append("in " + plural(n_els, "library genie"))
        text = "Replace %s %s with %s?" % (plural(n_genies, "genie"), " and ".join(where), _name(self.new))
        follow_p, follow_e = res.get("follow_pages") or [], res.get("follow_elements") or []
        if follow_p or follow_e:
            parts = ([plural(len(follow_e), "library genie")] if follow_e else []) + (
                [plural(len(follow_p), "page")] if follow_p else []
            )
            text += " Then %s that use the changed library genies are brought up to date." % " and ".join(
                parts
            )
        if open_hits:
            one = len(open_hits) == 1
            text += " %s open in the editor %s changed in %s tab%s and not saved." % (
                plural(len(open_hits), "window"),
                "is" if one else "are",
                "its" if one else "their",
                "" if one else "s",
            )
        text += " Everything else is backed up first, then saved (Tools > Restore Backup puts it back)."
        if res.get("shipped"):
            text += " Left (supplied with Plant SCADA): %s." % name_list(res["shipped"], 8)
        if res.get("failed"):
            text += " %s could not be read." % plural(len(res["failed"]), "file")
        details = "\n".join(
            ["Replaced: %s" % ", ".join(_name(l) for l in self.old)]
            + ["Library genie %s: %d" % (e["label"], e["count"]) for e in closed_els]
            + ["Page %s\\%s: %d" % (p["project"], p["name"], p["count"]) for p in closed]
            + ["%s (open in the editor): %d" % (_doc_label(d), len(r)) for _w, d, r in open_hits]
            + ["Brought up to date: library genie %s" % x for x in follow_e]
            + ["Brought up to date: page %s" % x for x in follow_p]
            + ["Could not be read: %s" % x for x in res.get("failed") or ()]
        )
        if not self._confirm(text, details):
            self._end()
            return
        from ..jobs.update_run import UpdatePagesRun

        why = UpdatePagesRun.busy_text("Run Replace Genie again")
        if why:
            # (a run started while the question was open)
            self._end()
            self._tell(why, error=True)
            return
        # the open tabs take the same parameter list as the closed pages and genies (the
        # dialog's and the ones the finding read from the old genies' libraries), so a %Tok%
        # the new genie does not bind comes out the same in both
        self.old_params = sorted(set(self.old_params) | set(res.get("params") or ()))
        self._replace_open(open_hits)
        if not closed and not closed_els:
            self._applied({"updated": [], "failed": [], "changed": 0})
            return
        from ...infra import backups

        req = dict(
            self._base(),
            mode="apply",
            pages=[p["page"] for p in closed],
            elements=[{"library": e["library"], "element": e["element"]} for e in closed_els],
            new=list(self.new),
            mapping=self.mapping,
            old_params=list(self.old_params),
            keep=self.keep,
            skip=sorted(open_page_keys),
            skip_elements=[[d.path, getattr(d, "element_name", "")] for _w, d in open_els],
            builder_open=builder_open_pages(),
            editor_file=self._write_editor_file(),
            backup=backups.new_folder("replace-genie"),
        )
        if not self._run(req, "Replacing %s" % _names_of(self.old), self._applied):
            self._end()

    def _write_editor_file(self) -> str:
        """The library genies open in an editor ('clean' / 'dirty' rows: the chain never
        saves over one with unsaved changes, genie_worker._editor_state); '' without."""
        states = getattr(self.win, "_element_tab_states", None)
        if states is None:
            return ""
        from ...infra.paths import data_dir

        try:
            folder = data_dir("replacegenie")
            os.makedirs(folder, exist_ok=True)
            self._editor_file = os.path.join(folder, "%d-%d.editors" % (os.getpid(), id(self)))
            with open(self._editor_file, "w", encoding="utf-8") as fh:
                fh.write(states())
        except Exception:  # noqa: BLE001
            from ...infra import logsetup

            logsetup.note_exception("replace_genie.editor_file")
            self._editor_file = ""
        return self._editor_file

    def _roots(self, doc) -> List[int]:
        olds = {_low(l) for l in self.old}
        return [i for i, link in doc.replaceable_genies() if _low(link) in olds]

    def _replace_open(self, hits) -> None:
        for w, d, roots in hits:
            name = _doc_label(d)
            try:
                d.replace_genies(
                    roots, self.new[0], self.new[1], self.mapping, keep=self.keep, old_params=self.old_params
                )
                self.changed_open.append(name)
                if getattr(d, "element_name", ""):
                    self.changed_open_elements.append(name)
            except Exception as e:  # noqa: BLE001 -- swallow-ok: reported in the result
                self.failed_open.append("%s: %s" % (name, e))
            if getattr(w, "doc", None) is d:
                w._sel = list(getattr(d, "replaced_roots", []) or [])
                w._refresh()

    def _applied(self, res: dict) -> None:
        self._end()
        self.answer = res
        upd = list(res.get("updated") or [])
        els = list(res.get("elements_updated") or [])
        chain_els = list(res.get("chain_elements_saved") or [])
        chain_pages = [p for p in res.get("chain_pages_updated") or () if p not in upd]
        saved = els + [x for x in chain_els if x not in els]
        if saved:
            # (the helper wrote the libraries: this process reads them again, and the open
            # tabs that use them compose them again, as after the chain after a genie save)
            from ...editing import libraries_changed

            libraries_changed()
            follow = getattr(self.win, "_follow_run_saves", None)
            if follow is not None:
                try:
                    follow(saved)
                except Exception:  # noqa: BLE001
                    from ...infra import logsetup

                    logsetup.note_exception("replace_genie.follow")
        chain = res.get("chain_answer") or {}
        after = getattr(self.win, "_after_chain_saves", None)
        if after is not None and any(chain.values()):
            # (as the chain after a genie save ends: the element tabs it saved from their
            # clean state read the stored element again, the libraries it wrote follow; else
            # such a tab keeps the version before the run and its next save writes over the
            # chain's)
            try:
                after(chain)
            except Exception:  # noqa: BLE001
                from ...infra import logsetup

                logsetup.note_exception("replace_genie.after_chain")
        n_open_els = len(self.changed_open_elements)
        n_open_pages = len(self.changed_open) - n_open_els
        done = []
        if upd or n_open_pages:
            done.append("on %s" % plural(len(upd) + n_open_pages, "page"))
        if els or n_open_els:
            done.append("in %s" % plural(len(els) + n_open_els, "library genie"))
        text = "Replaced %s with %s %s." % (
            _names_of(self.old),
            _name(self.new),
            " and ".join(done) if done else "nowhere",
        )
        if els:
            text += " Library genies saved: %s." % name_list(els, 8)
        if chain_els or chain_pages:
            parts = ([plural(len(chain_els), "library genie")] if chain_els else []) + (
                [plural(len(chain_pages), "page")] if chain_pages else []
            )
            text += " Brought up to date after them: %s." % " and ".join(parts)
        if self.changed_open:
            text += " Open in the editor, not saved yet: %s." % name_list(self.changed_open, 8)
            if n_open_els:
                text += " Saving a library genie brings what uses it up to date."
        failed = list(res.get("failed") or []) + self.failed_open + list(res.get("chain_failed") or [])
        if failed:
            text += " Could not change: %s." % "; ".join(failed)
        left = list(res.get("elements_skipped") or []) + list(res.get("chain_skipped") or [])
        if left:
            text += " Left: %s." % "; ".join(left)
        opened = (
            list(res.get("skipped_open") or [])
            + list(res.get("elements_open") or [])
            + [p for p in res.get("chain_open") or () if p not in self.changed_open]
        )
        if opened:
            text += " Left (open in the editor): %s." % name_list(opened, 8)
        if res.get("chain_unregistered"):
            text += " Left (not registered in their project, as Update Pages leaves them): %s." % name_list(
                res["chain_unregistered"], 8
            )
        if res.get("open_in_builder"):
            text += " Left (open in the Graphics Builder): %s." % name_list(res["open_in_builder"], 8)
        if res.get("stopped"):
            text = "Stopped. " + text
        self.result_text = text
        from ...infra import logsetup

        logsetup.get("pages").info(
            "replace genie %s -> %s: pages %s, library genies %s, chain %s / %s, open %s, "
            "failed %s, backup %s",
            _names_of(self.old),
            _name(self.new),
            upd,
            els,
            chain_els,
            chain_pages,
            self.changed_open,
            failed,
            res.get("backup"),
        )
        from .. import messages

        actions = []
        if (upd or saved) and res.get("backup"):
            actions.append(messages.open_folder_action("Open Backup Folder", res["backup"]))
        self._tell(text, actions=actions)


def replace_genie(win) -> None:
    """Edit > Library > Replace Genie… in the window's page or library genie (or every page
    and library genie of a project)."""
    doc = getattr(win, "doc", None)
    window = getattr(doc, "_swap_window", None)
    if doc is None or window is None or not window():
        win.statusBar().showMessage("Open a page or a library genie to replace its genies.", 4000)
        return
    # (a page with no genie of its own still opens it: the genies of its project's libraries
    # are listed, and replaced on the project's pages)
    dlg = ReplaceGenieDialog(doc, list(getattr(win, "_sel", []) or []), win)
    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return
    new = dlg.new_genie()
    if dlg.project_wide():
        run = ProjectReplaceRun(
            win,
            dlg.project_dirs(),
            dlg.old_genies(),
            new,
            dlg.mapping(),
            dlg.keep(),
            old_params=dlg.old_params(),
        )
        win._replace_genie_run = run  # (kept while it runs)
        run.start()
        return
    try:
        n = doc.replace_genies(
            dlg.targets(), new[0], new[1], dlg.mapping(), keep=dlg.keep(), old_params=dlg.old_params()
        )
    except (ValueError, KeyError) as exc:
        from .. import messages

        messages.error_box(
            win, "Replace Genie", "The genies could not be replaced: %s" % (exc.args[0] if exc.args else exc)
        )
        return
    win._sel = list(getattr(doc, "replaced_roots", []) or [])
    win._refresh()
    text = "Replaced %d %s with %s. Undo puts them back." % (n, "genie" if n == 1 else "genies", _name(new))
    if getattr(doc, "element_name", ""):
        text += " Save to bring what uses this genie up to date."
    win.statusBar().showMessage(text, 6000)
