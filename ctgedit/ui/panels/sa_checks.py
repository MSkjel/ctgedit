"""The Workspace Checks panel."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

from PySide6 import QtCore, QtWidgets

from ..prefs.settings import app_settings

HELP_TOPIC = "situational-awareness#workspace-checks"
#: setting: run the checks when a page with panes is saved
CHECK_ON_SAVE = "sa/check_on_save"

_LEVEL_TEXT = {"error": "Error", "warning": "Warning", "info": "Note"}
#: page-property field -> the Page Properties tab that edits it
PAGE_TABS = {"On page shown": "Events", "HasPanes": "Environment", "Content type": "General"}


def check_on_save() -> bool:
    try:
        return bool(app_settings().value(CHECK_ON_SAVE, True, type=bool))
    except Exception:  # noqa: BLE001  # swallow-ok: a garbled setting reads as the default
        return True


class WorkspaceChecksPanel(QtWidgets.QWidget):
    """The report of one page's workspace checks."""

    #: a pane row was activated: its sa_workspace.Finding (the window finds
    #: the pane on the page as it is now -- sa_workspace.locate_pane)
    paneActivated = QtCore.Signal(object)
    #: a page row was activated: the Page Properties tab to open ('' = any)
    pagePropertiesRequested = QtCore.Signal(str)
    #: 'Check Again'
    recheckRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        top = QtWidgets.QHBoxLayout()
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        top.addWidget(self.summary, 1)
        self.on_save = QtWidgets.QCheckBox("Check when saving")
        self.on_save.setToolTip("Check the workspace when a page with panes is saved")
        self.on_save.setChecked(check_on_save())
        self.on_save.toggled.connect(lambda on: app_settings().setValue(CHECK_ON_SAVE, bool(on)))
        top.addWidget(self.on_save)
        self.again = QtWidgets.QPushButton("Check Again")
        self.again.setToolTip("Check the open page's panes now (without saving)")
        self.again.clicked.connect(self.recheckRequested.emit)
        top.addWidget(self.again)
        lay.addLayout(top)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setWordWrap(True)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["", "Pane", "Problem"])
        from ..tables import enable_row_copy, make_resizable

        make_resizable(self.tree, "WorkspaceChecksPanel/problems")
        enable_row_copy(self.tree)
        self.tree.itemActivated.connect(self._activated)
        self.tree.currentItemChanged.connect(self._show_detail)
        lay.addWidget(self.tree, 1)
        # the whole message of the selected row (the Problem column elides)
        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.detail)
        self.report = None
        self.page_name = ""
        self.clear("Save a page with Situational Awareness panes, or use Tools > Check Workspace.")
        try:
            from ..help import set_help

            set_help(self, HELP_TOPIC)
        except Exception:  # noqa: BLE001  # swallow-ok: the help link is optional
            pass

    def clear(self, text: str = "") -> None:
        self.report = None
        self.tree.clear()
        self.detail.setText("")
        self.summary.setText(text)

    def _show_detail(self, item, _prev=None) -> None:
        f = item.data(0, QtCore.Qt.UserRole) if item is not None else None
        if f is None:
            self.detail.setText("")
            return
        how = (
            "Double-click to select the pane."
            if f.index is not None
            else "Double-click to open Page Properties."
        )
        self.detail.setText("%s %s" % (f.message, how))

    def show_report(self, report) -> None:
        """Show ``report`` (sa_workspace.Report)."""
        self.report = report
        self.page_name = report.page
        self.tree.clear()
        self.detail.setText("")
        if not report.panes:
            self.summary.setText("%s has no Situational Awareness panes." % report.page)
            return
        kind = "workspace master page" if report.master else "page"
        found = report.summary()
        self.summary.setText(
            "%s (%s, %d pane%s): %s."
            % (
                report.page,
                kind,
                len(report.panes),
                "" if len(report.panes) == 1 else "s",
                found if found else "no problems found",
            )
        )
        for f in report.findings:
            it = QtWidgets.QTreeWidgetItem(
                [
                    _LEVEL_TEXT.get(f.level, f.level),
                    (f.pane.name or "(no name)") if f.pane is not None else "(page)",
                    f.message,
                ]
            )
            from ..notify import level_icon

            it.setIcon(0, level_icon(f.level if f.level in ("error", "warning") else "info"))
            it.setToolTip(2, f.message)
            it.setData(0, QtCore.Qt.UserRole, f)
            self.tree.addTopLevelItem(it)

    def finding(self, row: int):
        it = self.tree.topLevelItem(row)
        return it.data(0, QtCore.Qt.UserRole) if it is not None else None

    def _activated(self, item: QtWidgets.QTreeWidgetItem, _col: int = 0) -> None:
        f = item.data(0, QtCore.Qt.UserRole)
        if f is None:
            return
        if f.index is not None:
            self.paneActivated.emit(f)
        else:
            self.pagePropertiesRequested.emit(PAGE_TABS.get(f.field, ""))

    def activate_row(self, row: int) -> None:
        """What a double-click on row ``row`` does (tests / keyboard)."""
        it = self.tree.topLevelItem(row)
        if it is not None:
            self._activated(it)


def run_checks(doc):
    """sa_workspace.check_document, never raising (None when it failed)."""
    try:
        from ...features import sa_workspace as SA

        return SA.check_document(doc)
    except Exception:  # noqa: BLE001 -- an aid: never in the way of a save
        from ...infra import logsetup

        logsetup.get("sa").exception("workspace checks failed")
        return None
