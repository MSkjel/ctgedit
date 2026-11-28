"""The open tabs a run updates or holds, the Used By windows that follow it, and the run state."""

from __future__ import annotations

from ...editing import ui_state

from .run_text import unique_names


from ..names import name_list

from ..main_window_base import editor_windows

from ..main_window_base import (
    ElementDocument,
    QtCore,
    QtWidgets,
    os,
)
from ..main_window_base import (
    PendingPage,
)
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def run_state():
    """The process's run state (ui/jobs/update_run.UpdatePagesRun)."""
    from ..jobs.update_run import UpdatePagesRun

    return UpdatePagesRun


def all_tabs(first=None):
    """(window, tab entry) of every editor window's tabs, ``first``'s first."""
    wins = editor_windows()
    if first is not None:
        wins = [first] + [w for w in wins if w is not first]
    for w in wins:
        for e in list(getattr(w, "_docs", ())):
            yield w, e


def page_tab_showing(path: str, first=None):
    """(window, tab entry) of the tab that shows the page ``path`` itself (not a library
    element, not an earlier version of it), ``first``'s tabs looked at first; None."""
    from ...infra.paths import path_key
    from ..main_window_base import ElementDocument

    want = path_key(path)
    for w, e in all_tabs(first):
        d = e["doc"]
        if isinstance(d, ElementDocument) or getattr(d, "element", ""):
            continue
        if getattr(ui_state(d), "version_view", None) is not None:
            continue
        if path_key(getattr(d, "path", "") or "") == want:
            return w, e
    return None


def used_by_windows() -> list:
    """The open Used By windows (ElementUsersDialog) of every editor window."""
    from ..dialog.library_dialogs import ElementUsersDialog

    out = []
    for w in editor_windows():
        try:
            dlgs = list(getattr(w, "_dialogs", {}).values())
        except RuntimeError:  # swallow-ok: a window being destroyed
            continue
        out += [d for d in dlgs if isinstance(d, ElementUsersDialog)]
    return out


def page_tab(d) -> bool:
    """Is ``d`` a page tab a run may update in place?"""
    from ..dialog.history_versions import is_version

    return (
        not isinstance(d, (ElementDocument, PendingPage))
        and not is_version(d)
        and not getattr(d, "read_only", False)
    )


def element_tab(d) -> bool:
    """``d`` is a library element's own tab: an element of its library (not a new one), not
    an earlier version of it shown read-only."""
    from ..dialog.history_versions import is_version

    return isinstance(d, ElementDocument) and not getattr(d, "is_new", False) and not is_version(d)


def refresh_stale_used_by() -> None:
    """Refresh stale Used By windows once no run is writing."""
    from ..jobs.update_run import UpdatePagesRun as R

    if (
        R.users_busy
        or R.active is not None
        or R.chain_busy
        or R.tabs_following
        or run_state().users_reads
        or QtWidgets.QApplication.activeModalWidget() is not None
    ):
        QtCore.QTimer.singleShot(500, refresh_stale_used_by)
        return
    run_state().used_by_due = False
    for d in used_by_windows():
        try:
            if getattr(d, "stale", False) and d.isVisible():
                d.refresh()
        except RuntimeError:  # swallow-ok: a window closed meanwhile
            continue


def page_mtimes(paths) -> Dict[str, Optional[int]]:
    """{path: its file's modification time (ns), None when it cannot be read} (the pages
    a run left because they were open: one whose time is the same at the end was not
    saved since)."""
    out: Dict[str, Optional[int]] = {}
    for p in paths:
        if not p:
            continue
        try:
            out[p] = os.stat(p).st_mtime_ns
        except OSError:  # swallow-ok: a page with no file (deleted): no time to compare
            out[p] = None
    return out


def history_depth(d) -> int:
    """How many steps the page ``d`` is from its open state: its undo steps with those the
    undo cap dropped (EditableDocument.history_depth). A step pushed while the cap drops an
    old one leaves the undo stack as long as it was, but this count still grows."""
    return len(getattr(d, "_undo", None) or ()) + int(getattr(d, "undo_trimmed", 0) or 0)


class TabsHold:
    """A run's hold on the others until its open tabs have followed it."""

    def __init__(self, run: str = ""):
        self.run = run
        self.held = False

    def take(self) -> None:
        from ..jobs.update_run import UpdatePagesRun

        UpdatePagesRun.tabs_following = True
        UpdatePagesRun.tabs_run = self.run
        self.held = True

    def release(self, win) -> None:
        from ..jobs.update_run import UpdatePagesRun

        if not self.held:
            return
        self.held = False
        UpdatePagesRun.tabs_following = False
        UpdatePagesRun.tabs_run = ""
        if UpdatePagesRun.users_queue:
            QtCore.QTimer.singleShot(0, win, win._next_users_run)


@dataclass
class OpenTabsUpdate:
    """What Update Pages' (and Update Users') step for the pages open in tabs did
    (main_window_class()._update_open_documents): the page files by outcome, and the counts."""

    updated: List[str] = field(default_factory=list)  # updated in their tab by the step
    #: updated in their tab and saved there: nothing on them was the user's (only the
    #: automatic update on open and this run had changed them)
    saved: List[str] = field(default_factory=list)
    auto: List[str] = field(default_factory=list)  # by the automatic update, in the run
    nothing: List[str] = field(default_factory=list)  # nothing to update
    #: out of date, but nothing on them changed (the log says why: updatepages.unchanged_reason)
    unchanged: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)  # could not be updated (the log)
    #: not done: Cancel was clicked in the open-tabs step (the run leaves them)
    left: List[str] = field(default_factory=list)
    #: what the automatic update brings up to date in them right after the run ('genies and
    #: symbols', ...: the run saved library elements, main_window_class()._follow_run_saves);
    #: '' = nothing, they stay as they are
    left_follow: str = ""
    #: what follows in a page of ``left`` (its path as listed) when it is not
    #: ``left_follow``: a template or super genie the run saved reaches only the pages made
    #: from it (main_window_class()._left_follow)
    left_words: Dict[str, str] = field(default_factory=dict)
    #: the pages of ``left`` whose template is still out of date in their tab
    left_template: List[str] = field(default_factory=list)
    genies: int = 0
    templates: int = 0
    symbols: int = 0
    composites: int = 0
    supergenies: int = 0
    placeholders: int = 0  # placeholder genies placed again

    def paths(self) -> List[str]:
        return self.saved + self.updated + self.auto + self.unchanged + self.nothing + self.failed

    def counts(self) -> tuple:
        """(genies, templates, symbols, composite genies) updated in the tabs (a placeholder
        placed again is a genie)."""
        return (
            self.genies + self.supergenies + self.placeholders,
            self.templates,
            self.symbols,
            self.composites,
        )

    def projects(self) -> List[str]:
        """Their projects' names, each once (whatever the case of its folder's name)."""
        return unique_names(os.path.basename(os.path.dirname(os.path.abspath(p))) for p in self.paths())

    @property
    def stale(self) -> int:
        """The open pages that were out of date (updated here or by the automatic update
        during the run, out of date with nothing changed, or that could not be updated)."""
        return len(self.saved) + len(self.updated) + len(self.auto) + len(self.unchanged) + len(self.failed)

    def lines(self, label) -> List[str]:
        """The result box's lines: one per outcome, ``label(path)`` = a page's name."""

        def them(xs):
            return "it" if len(xs) == 1 else "them"

        out = []
        if self.saved:
            out.append(
                "Open in a tab with no changes of yours, updated and saved there: %s."
                % name_list(label(p) for p in self.saved)
            )
        if self.updated:
            out.append(
                "Open in a tab and updated there (save %s to keep the changes): %s."
                % (them(self.updated), name_list(label(p) for p in self.updated))
            )
        if self.auto:
            out.append(
                "Open in a tab and already updated there by the automatic update (save "
                "%s to keep the changes): %s." % (them(self.auto), name_list(label(p) for p in self.auto))
            )
        if self.unchanged:
            out.append(
                "Open in a tab, out of date, but nothing on %s could be updated (the "
                "log says why): %s." % (them(self.unchanged), name_list(label(p) for p in self.unchanged))
            )
        if self.nothing:
            out.append("Open in a tab, nothing to update: %s." % name_list(label(p) for p in self.nothing))
        if self.failed:
            out.append(
                "Open in a tab, could not be updated (the log says why): %s."
                % name_list(label(p) for p in self.failed)
            )
        # (by what follows in them: a page whose template stays out of date is told so)
        groups: Dict[tuple, List[str]] = {}
        for p in self.left:
            words = self.left_words.get(p, self.left_follow)
            groups.setdefault((words, bool(words) and p in self.left_template), []).append(p)
        for (words, kept), ps in groups.items():
            its = "its" if len(ps) == 1 else "their"
            if not words:
                out.append(
                    "Open in a tab, not updated because the run was stopped: %s."
                    % name_list(label(p) for p in ps)
                )
                continue
            out.append(
                "Open in a tab, left when the run was stopped. The automatic update "
                "brings %s changed %s up to date there now%s: %s."
                % (
                    its,
                    words,
                    ", not %s template (Tools > Update Template on This Page does it)" % its if kept else "",
                    name_list(label(p) for p in ps),
                )
            )
        return out


class SharedState:
    """What the editor windows of this process share (one copy; :meth:`reset` for the tests)."""

    #: CtDraw's cache notice (_library_cache_notice), the editor's: the runs of several steps
    #: going on now, the notice waiting for their end, and whether its box was shown
    cache_notice_holds: int = 0
    cache_notice_waiting: str = ""
    cache_notice_shown: bool = False
    #: the pages Rename Pages is moving (path keys): no window saves or watches them
    renaming: set = set()

    @classmethod
    def reset(cls) -> None:
        cls.cache_notice_holds = 0
        cls.cache_notice_waiting = ""
        cls.cache_notice_shown = False
        cls.renaming = set()
