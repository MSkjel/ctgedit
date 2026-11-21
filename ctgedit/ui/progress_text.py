"""Progress text that doesn't resize its window as it changes."""

from __future__ import annotations

from typing import Iterable, List, Optional

from PySide6 import QtCore, QtWidgets


def _widest(fm, texts: Iterable[str]) -> int:
    return max((fm.horizontalAdvance(t) for t in texts), default=0)


class SteadyProgressLabel:
    """The label of a QProgressDialog sized once for the widest figures of ``stats``
    (RunStats.widest_lines) and their lines: a longer line is shortened in the middle,
    a shorter text gets blank lines at the end, so the window keeps its size."""

    def __init__(self, prog: QtWidgets.QProgressDialog, stats):
        self._prog = prog
        prog.ensurePolished()  # (the font the window will draw with)
        self._label: Optional[QtWidgets.QLabel] = prog.findChild(QtWidgets.QLabel)
        if self._label is not None:
            self._label.ensurePolished()
        fm = (self._label or prog).fontMetrics()
        samples = stats.widest_lines()
        self.width = _widest(fm, samples)
        #: the label's lines: the step's own line, then the figures (none when off)
        self.rows = 1 + (stats.LINES if stats.show else 0)
        widest = max(samples, key=fm.horizontalAdvance)
        prog.setLabelText("\n".join([widest] * self.rows))
        # (a hidden QProgressDialog takes its size hint at each new text, a shown one only
        # grows: the minimum keeps it at the widest text's size either way)
        prog.setMinimumSize(prog.sizeHint().expandedTo(prog.minimumSize()))
        prog.resize(prog.minimumSize().expandedTo(prog.size()))

    def lines(self, lines: List[str]) -> List[str]:
        """``lines`` as the label shows them (each fits, as many as :attr:`rows`)."""
        fm = (self._label or self._prog).fontMetrics()
        out = [fm.elidedText(str(x), QtCore.Qt.ElideMiddle, self.width) for x in lines]
        # (a blank line still takes a line's height: a space)
        return out + [" "] * max(0, self.rows - len(out))

    def set(self, lines: List[str]) -> None:
        self._prog.setLabelText("\n".join(self.lines(lines)))


def hold_width(label: QtWidgets.QLabel, texts: Iterable[str] = ()) -> None:
    """A status bar note that grows but never shrinks while its run goes on: at least as
    wide as its text now and each of ``texts`` (the same note with the widest figures).
    ``label.setMinimumWidth(0)`` when the run has ended lets it fit its result."""
    label.ensurePolished()
    fm = label.fontMetrics()
    m = label.contentsMargins()
    pad = m.left() + m.right() + 2 * label.margin() + max(0, label.indent())
    need = _widest(fm, [label.text()] + list(texts)) + pad
    if need > label.minimumWidth():
        label.setMinimumWidth(need)
