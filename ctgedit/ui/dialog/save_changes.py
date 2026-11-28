"""One Save Changes question for several unsaved pages."""

from __future__ import annotations

from ..names import plural

from PySide6 import QtCore, QtWidgets

#: the columns of the list
COLUMNS = ("Page", "Project", "Changes", "Folder")


class SaveChangesDialog(QtWidgets.QDialog):
    """``rows`` = [(key, page, project, changes, folder, ticked)]: ``key`` is handed back by
    :meth:`selected` for the ticked rows. :meth:`exec_choice` returns SAVE, DISCARD or
    CANCEL."""

    SAVE, DISCARD, CANCEL = 1, 2, 0
    RESIZE = "both"

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unsaved Changes - ctgedit")
        self._keys = [r[0] for r in rows]
        self.choice = self.CANCEL
        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel("Save the changes to these pages?")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.table = QtWidgets.QTableWidget(len(rows), len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        # one line a row: a long folder loses its middle, not all but 'C:' (the tool tip
        # has it all)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(QtCore.Qt.ElideMiddle)
        self.table.setAccessibleName("Pages with unsaved changes")
        for r, (_key, page, project, changes, folder, ticked) in enumerate(rows):
            for c, text in enumerate((page, project, changes, folder)):
                it = QtWidgets.QTableWidgetItem(text)
                it.setToolTip(text)
                if c == 0:
                    it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                    it.setCheckState(QtCore.Qt.Checked if ticked else QtCore.Qt.Unchecked)
                self.table.setItem(r, c, it)
        from .. import tables

        tables.make_resizable(self.table, "SaveChangesDialog/pages", stretch=3)
        lay.addWidget(self.table, 1)
        # what the save button does now, in words (the unticked pages lose their changes)
        self.outcome = QtWidgets.QLabel()
        self.outcome.setWordWrap(True)
        self.outcome.setAccessibleName("What saving does")
        lay.addWidget(self.outcome)
        bb = QtWidgets.QDialogButtonBox(self)
        self.btn_save = bb.addButton("Save All", QtWidgets.QDialogButtonBox.AcceptRole)
        self.btn_discard = bb.addButton("Don't Save", QtWidgets.QDialogButtonBox.DestructiveRole)
        self.btn_cancel = bb.addButton(QtWidgets.QDialogButtonBox.Cancel)
        self.btn_save.setDefault(True)
        self.btn_save.setToolTip("Save the ticked pages. Unticked pages are closed without saving")
        self.btn_discard.setToolTip("Close every page in the list without saving")
        self.btn_save.clicked.connect(lambda: self._done(self.SAVE))
        self.btn_discard.clicked.connect(lambda: self._done(self.DISCARD))
        self.btn_cancel.clicked.connect(lambda: self._done(self.CANCEL))
        lay.addWidget(bb)
        self.table.itemChanged.connect(lambda _it: self._sync())
        self._sync()
        self.resize(640, min(440, 160 + 26 * len(rows)))

    def _sync(self) -> None:
        """Save only with a ticked page (with none it would close them all unsaved:
        Don't Save says that), and the outcome in words beside the buttons."""
        n = len(self.selected())
        total = len(self._keys)

        def pages(k: int) -> str:
            return plural(k, "page")

        if n == 0:
            text = "No page is ticked. Tick the pages to save, or choose Don't Save."
            label = "Save"
        elif n == total:
            text = "Save %s." % pages(n)
            label = "Save All" if n > 1 else "Save"
        else:
            text = "Save %s and discard the changes to %s." % (pages(n), pages(total - n))
            # (the button says it discards too: 'Save Selected' read as 'the others stay')
            label = "Save %d, Discard %d" % (n, total - n)
        self.outcome.setText(text)
        self.btn_save.setText(label)
        self.btn_save.setEnabled(n > 0)
        # (Enter never discards: with nothing ticked, or some unticked, it is Cancel)
        self.btn_save.setDefault(0 < n == total)
        self.btn_cancel.setDefault(not 0 < n == total)

    def set_ticked(self, row: int, on: bool) -> None:
        it = self.table.item(row, 0)
        if it is not None:
            it.setCheckState(QtCore.Qt.Checked if on else QtCore.Qt.Unchecked)

    def selected(self) -> list:
        """The keys of the ticked rows, in list order."""
        return [
            k
            for r, k in enumerate(self._keys)
            if self.table.item(r, 0) is not None and self.table.item(r, 0).checkState() == QtCore.Qt.Checked
        ]

    def _done(self, choice: int) -> None:
        self.choice = choice
        (self.reject if choice == self.CANCEL else self.accept)()

    def exec_choice(self) -> int:
        self.exec()
        return self.choice
