"""The Page Checks panel: Check Page findings as a list."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

from PySide6 import QtCore, QtWidgets

HELP_TOPIC = "troubleshooting#check-page"

_LEVEL_TEXT = {"error": "Error", "warning": "Warning", "info": "Note"}


def _finding_key(f):
    """The same finding again after a refresh (its object, field and message)."""
    return (f.level, f.label, f.field, f.message, getattr(f, "record", None))


class PageChecksPanel(QtWidgets.QWidget):
    """The report of one page's checks."""

    #: a row was activated: its page_checks.Finding (the window selects the object)
    findingActivated = QtCore.Signal(object)
    #: Properties: open the sheet of the finding's object
    propertiesRequested = QtCore.Signal(object)
    #: Check Again
    recheckRequested = QtCore.Signal()
    #: Check Page Display
    displayCheckRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        top = QtWidgets.QHBoxLayout()
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        top.addWidget(self.summary, 1)
        self.again = QtWidgets.QPushButton("Check Again")
        self.again.setToolTip("Run the checks on the open page now")
        self.again.clicked.connect(self.recheckRequested.emit)
        top.addWidget(self.again)
        self.display = QtWidgets.QPushButton("Check Page Display")
        self.display.setToolTip(
            (
                "Read the page again in a separate process and compare its drawing with this window "
                "(Tools > Check Page Display)"
            )
        )
        self.display.clicked.connect(self.displayCheckRequested.emit)
        top.addWidget(self.display)
        lay.addLayout(top)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setWordWrap(True)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["", "Object", "Field", "Problem"])
        from ..tables import enable_row_copy, make_resizable

        make_resizable(self.tree, "PageChecksPanel/problems")
        enable_row_copy(self.tree)
        self.tree.itemActivated.connect(self._activated)
        self.tree.currentItemChanged.connect(self._show_detail)
        lay.addWidget(self.tree, 1)
        bottom = QtWidgets.QHBoxLayout()
        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        bottom.addWidget(self.detail, 1)
        self.show_btn = QtWidgets.QPushButton("Show on Page")
        self.show_btn.setToolTip("Select the object and bring it into view")
        self.show_btn.clicked.connect(lambda: self._activated(self.tree.currentItem()))
        bottom.addWidget(self.show_btn)
        self.props_btn = QtWidgets.QPushButton("Properties…")
        self.props_btn.setToolTip("Open the properties where the field is set")
        self.props_btn.clicked.connect(self._properties)
        bottom.addWidget(self.props_btn)
        lay.addLayout(bottom)
        self.report = None
        self.clear("Use Tools > Check Page to check the open page.")
        try:
            from ..help import set_help

            set_help(self, HELP_TOPIC)
        except Exception:  # noqa: BLE001  # swallow-ok: the help link is an aid
            pass

    def clear(self, text: str = "") -> None:
        self.report = None
        self.tree.clear()
        self.detail.setText("")
        self.summary.setText(text)
        self._update_buttons()

    def show_report(self, report) -> None:
        """Show ``report`` (page_checks.Report); called again as the engine adds rows. The
        finding being read stays selected (and the list where it was scrolled)."""
        cur = self._current()
        keep = _finding_key(cur) if cur is not None else None
        scroll = self.tree.verticalScrollBar().value()
        self.report = report
        found = report.summary()
        text = "%s: %s." % (report.page, found if found else "no problems found")
        if report.expressions:
            if report.compiled < report.expressions and not report.engine_note:
                text += " Compiling %d of %d expressions…" % (report.compiled, report.expressions)
            elif report.engine_note:
                text += " " + report.engine_note
            else:
                text += " %d expression%s compiled." % (
                    report.expressions,
                    "" if report.expressions == 1 else "s",
                )
        self.summary.setText(text)
        # the rows the list has are kept as far as they are the report's first ones; the
        # rest goes and the new ones are added
        tree = self.tree
        have = tree.topLevelItemCount()
        new = report.findings
        same = 0
        while same < have and same < len(new) and _finding_key(self.finding(same)) == _finding_key(new[same]):
            tree.topLevelItem(same).setData(0, QtCore.Qt.UserRole, new[same])
            same += 1
        tree.setUpdatesEnabled(False)
        try:
            for k in range(have - 1, same - 1, -1):
                tree.takeTopLevelItem(k)
            from ..notify import level_icon

            rows = []
            for f in new[same:]:
                it = QtWidgets.QTreeWidgetItem(
                    [_LEVEL_TEXT.get(f.level, f.level), f.label or "(page)", f.field or "", f.message]
                )
                it.setIcon(0, level_icon(f.level if f.level in ("error", "warning") else "info"))
                it.setToolTip(3, f.message)
                it.setData(0, QtCore.Qt.UserRole, f)
                rows.append(it)
            if rows:
                tree.addTopLevelItems(rows)
        finally:
            tree.setUpdatesEnabled(True)
        n = tree.topLevelItemCount()
        now = self._current()
        if now is not None and keep is not None and _finding_key(now) == keep:
            self._update_buttons()  # (the row read is still there: it and its details stay)
            return
        self.detail.setText("")
        pick = None
        if keep is not None:
            pick = next(
                (tree.topLevelItem(i) for i in range(n) if _finding_key(self.finding(i)) == keep),
                None,
            )
        if pick is None and n:
            pick = tree.topLevelItem(0)
        if pick is not None:
            tree.setCurrentItem(pick)
        if keep is not None:
            tree.verticalScrollBar().setValue(scroll)
        self._update_buttons()

    def finding(self, row: int):
        it = self.tree.topLevelItem(row)
        return it.data(0, QtCore.Qt.UserRole) if it is not None else None

    def _current(self):
        it = self.tree.currentItem()
        return it.data(0, QtCore.Qt.UserRole) if it is not None else None

    def _update_buttons(self) -> None:
        f = self._current()
        self.show_btn.setEnabled(f is not None and f.index is not None)
        self.props_btn.setEnabled(f is not None)

    def _show_detail(self, item, _prev=None) -> None:
        f = item.data(0, QtCore.Qt.UserRole) if item is not None else None
        self.detail.setText(f.message if f is not None else "")
        self._update_buttons()

    def _activated(self, item, _col: int = 0) -> None:
        f = item.data(0, QtCore.Qt.UserRole) if item is not None else None
        if f is not None and f.index is not None:
            self.findingActivated.emit(f)

    def _properties(self) -> None:
        f = self._current()
        if f is not None:
            self.propertiesRequested.emit(f)

    def activate_row(self, row: int) -> None:
        """What a double-click on row ``row`` does (tests / keyboard)."""
        it = self.tree.topLevelItem(row)
        if it is not None:
            self._activated(it)
