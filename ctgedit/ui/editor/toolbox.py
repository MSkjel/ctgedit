"""View > Show Tools: the floating toolbox."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

from PySide6 import QtCore, QtGui, QtWidgets

from ..style import tips
from ..prefs.settings import SETTINGS_KEYS, app_settings
from ..style.theme import set_note_role, type_qss

#: the setting of Right-click > Show Labels
LABELS_KEY = "ui/toolbox_labels"


class ToolboxWindow(QtWidgets.QWidget):
    COLUMNS = 2

    def __init__(self, actions, parent=None):
        """``actions`` = the tool actions in order; None = a break between two groups; a
        string = the heading of the group that follows (shown with the labels)."""
        super().__init__(parent, QtCore.Qt.Tool)
        self.setWindowTitle("Tools")
        self._items = list(actions)
        self._columns: dict = {}
        self.buttons: list[QtWidgets.QToolButton] = []
        self.separators: list[QtWidgets.QFrame] = []
        self.headings: list[QtWidgets.QLabel] = []
        self._grid = QtWidgets.QGridLayout(self)
        self._grid.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        self._grid.setSpacing(_sp(0))
        # buttons of a fixed size: the window keeps its size (UI guide section 9)
        self._grid.setSizeConstraint(QtWidgets.QLayout.SetFixedSize)
        self.act_labels = QtGui.QAction("Show Labels", self)
        self.act_labels.setCheckable(True)
        self.act_labels.setChecked(self.labels_shown())
        self.act_labels.toggled.connect(self.set_labels)
        self.setContextMenuPolicy(QtCore.Qt.DefaultContextMenu)
        self._build()

    @staticmethod
    def labels_shown() -> bool:
        return bool(app_settings().value(LABELS_KEY, SETTINGS_KEYS[LABELS_KEY], type=bool))

    def set_labels(self, on: bool) -> None:
        """One column of icon and name (on) or the two-column grid of icons (off); kept."""
        on = bool(on)
        app_settings().setValue(LABELS_KEY, on)
        if self.act_labels.isChecked() != on:
            self.act_labels.setChecked(on)
        self._build()

    def _clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self.buttons, self.separators, self.headings = [], [], []

    def groups(self) -> list:
        """(heading, [actions]) per group, in order."""
        out = [["", []]]
        for a in self._items:
            if a is None or isinstance(a, str):
                if out[-1][1] or out[-1][0]:
                    out.append(["", []])
                if isinstance(a, str):
                    out[-1][0] = a
                continue
            out[-1][1].append(a)
        return [(h, acts) for h, acts in out if acts]

    def _room(self) -> int:
        """The height the toolbox may take: its screen's free height, less its title bar."""
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return 10_000
        return max(200, screen.availableGeometry().height() - 48)

    def column_of(self, button) -> int:
        """The column ``button`` is in (0 = the first)."""
        for w, col in self._columns.items():
            if w.isAncestorOf(button):
                return col
        k = self._grid.indexOf(button)
        return self._grid.getItemPosition(k)[1] if k >= 0 else -1

    def _build_labels(self) -> None:
        """Icon and name, a group under its heading; the groups go into a further column
        when the next one would not fit in the screen's height (whole groups: a tall list
        never runs off the screen)."""
        self._columns = {}
        blocks = []
        for heading, acts in self.groups():
            block = QtWidgets.QWidget(self)
            v = QtWidgets.QVBoxLayout(block)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(_sp(0))
            if heading:
                lab = QtWidgets.QLabel(heading, block)
                set_note_role(lab, "text_dim", "%s padding: 4px 2px 0px 2px;" % type_qss("caption"))
                v.addWidget(lab)
                self.headings.append(lab)
            for a in acts:
                b = QtWidgets.QToolButton(block)
                b.setDefaultAction(a)
                b.setAutoRaise(True)
                b.setIconSize(QtCore.QSize(20, 20))
                b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
                b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                tips.install(b)
                v.addWidget(b)
                self.buttons.append(b)
            blocks.append(block)
        room = self._room()
        # (each column stacks its own groups: rows shared across columns left gaps)
        columns: list = []
        used = 0
        for block in blocks:
            h = block.sizeHint().height()
            if not columns or (used and used + h > room):
                holder = QtWidgets.QWidget(self)
                v = QtWidgets.QVBoxLayout(holder)
                v.setContentsMargins(0, 0, 0, 0)
                v.setSpacing(_sp(0))
                self._grid.addWidget(holder, 0, len(columns), QtCore.Qt.AlignTop)
                columns.append(v)
                used = 0
            columns[-1].addWidget(block)
            self._columns[block] = len(columns) - 1
            used += h + columns[-1].spacing()
        for v in columns:
            v.addStretch(1)
        self.adjustSize()

    def _build(self) -> None:
        self._clear()
        self._columns = {}
        labels = self.act_labels.isChecked()
        if labels:
            self._build_labels()
            return
        cols = self.COLUMNS
        row = 0
        for gi, (heading, acts) in enumerate(self.groups()):
            if labels and heading:
                lab = QtWidgets.QLabel(heading, self)
                set_note_role(lab, "text_dim", "%s padding: 4px 2px 0px 2px;" % type_qss("caption"))
                self._grid.addWidget(lab, row, 0, 1, cols)
                self.headings.append(lab)
                row += 1
            elif gi:
                line = QtWidgets.QFrame(self)
                line.setFrameShape(QtWidgets.QFrame.HLine)
                line.setFrameShadow(QtWidgets.QFrame.Plain)
                line.setStyleSheet("color: #3a3f46;")  # (the border colour)
                self._grid.addWidget(line, row, 0, 1, cols)
                self.separators.append(line)
                row += 1
            for k, a in enumerate(acts):
                b = QtWidgets.QToolButton(self)
                b.setDefaultAction(a)
                b.setAutoRaise(True)
                b.setIconSize(QtCore.QSize(20, 20))
                if labels:
                    b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
                    b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                tips.install(b)
                self._grid.addWidget(b, row + k // cols, k % cols)
                self.buttons.append(b)
            row += (len(acts) + cols - 1) // cols
        self.adjustSize()

    def contextMenuEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        menu = QtWidgets.QMenu(self)
        menu.addAction(self.act_labels)
        menu.exec(e.globalPos())
