"""Shared rows for the search popups: one scorer, model and view."""

from __future__ import annotations

import functools
from typing import Iterable, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from ..format.cnum import is_digits
from ..infra import caches as _caches

#: the role holding the :class:`Row` of an index
ROW_ROLE = QtCore.Qt.UserRole + 2
#: Qt's roles, read once (an attribute of QtCore.Qt costs about 1 us)
_DISPLAY_ROLE = QtCore.Qt.DisplayRole
_ACCESSIBLE_ROLE = QtCore.Qt.AccessibleTextRole
_TOOLTIP_ROLE = QtCore.Qt.ToolTipRole
_USER_ROLE = QtCore.Qt.UserRole
#: the role holding a row's command id
_CID_ROLE = QtCore.Qt.UserRole + 1
#: the words the letters-only rule does not start at (typed whole they are found as words)
JOINING_WORDS = frozenset(
    ("a", "an", "and", "as", "at", "by", "for", "in", "into", "of", "on", "or", "the", "to", "with")
)
#: characters that end a word
_SEPARATORS = set(" \t_.-/\\()[]{}:,;>…'\"+|&@#*!?=<")


# --------------------------------------------------------------------------- the scorer
def word_starts(text: str) -> List[int]:
    """The positions where a word of ``text`` starts (see the module docstring)."""
    out = []
    prev = ""
    for i, ch in enumerate(text):
        if ch in _SEPARATORS or ch.isspace():
            prev = ch
            continue
        if not prev or prev in _SEPARATORS or prev.isspace():
            out.append(i)
        elif prev.islower() and ch.isupper():
            out.append(i)
        elif prev.isalpha() and is_digits(ch):
            out.append(i)
        elif is_digits(prev) and ch.isalpha():
            out.append(i)
        prev = ch
    return out


@functools.lru_cache(maxsize=8192)
def _starts_of(text: str) -> Tuple[int, ...]:
    """:func:`word_starts` of ``text``, kept (a popup matches the same texts on every key
    press: the Settings rows, the commands, the help sections)."""
    return tuple(word_starts(text))


_caches.register("search_rows.word_starts", _starts_of, on=())


def _joining_starts(t: str, starts: Sequence[int]) -> set:
    """The starts in ``t`` (lower case) of its joining words (:data:`JOINING_WORDS`)."""
    out = set()
    for s in starts:
        e = s
        while e < len(t) and t[e].isalpha():
            e += 1
        if t[s:e] in JOINING_WORDS and (e == len(t) or t[e] in _SEPARATORS or t[e].isspace()):
            out.add(s)
    return out


def _subsequence(
    q: str, text: str, starts: Sequence[int], within_word: bool
) -> Optional[Tuple[int, List[int]]]:
    """(cost, positions) of the cheapest word-rule subsequence of ``q`` in ``text``."""
    t = text.lower()
    n = len(t)
    start_set = set(starts)
    # (the words a letter may start at: the joining words are noise)
    joining = _joining_starts(t, starts)
    starts = [s for s in starts if s not in joining]
    # the end of the word each position is in (a skip inside a word stays before it)
    word_end = [n] * n
    end = n
    for i in range(n - 1, -1, -1):
        word_end[i] = end
        if i in start_set:
            end = i

    @functools.lru_cache(maxsize=None)
    def go(qi: int, prev: int):
        if qi == len(q):
            return 0, ()
        ch = q[qi]
        cands = []
        if qi == 0:
            cands = [(s, s * 0.01) for s in starts if t[s] == ch]
        else:
            nxt = prev + 1
            if nxt < n and t[nxt] == ch:
                cands.append((nxt, 0.0))
            if within_word:
                for p in range(prev + 2, word_end[prev] if prev < n else n):
                    if t[p] == ch:
                        cands.append((p, 1.0 + (p - prev) * 0.1))
            for s in starts:
                if s > prev + 1 and t[s] == ch:
                    cands.append((s, 2.0 + (s - prev) * 0.05))
        best = None
        for pos, cost in cands:
            rest = go(qi + 1, pos)
            if rest is None:
                continue
            total = cost + rest[0]
            if best is None or total < best[0]:
                best = (total, (pos,) + rest[1])
        return best

    hit = go(0, -1)
    go.cache_clear()
    if hit is None:
        return None
    return int(round(hit[0] * 4)), list(hit[1])


def match(
    query: str, text: str, substring_min: int = 1, within_word: bool = False, loose: bool = True
) -> Optional[Tuple[int, List[int]]]:
    """(rank, matched positions) of ``query`` in ``text``, or None (see the module).
    ``loose`` False: None instead of a match of the letters in order (rank 100 and up)."""
    q = " ".join((query or "").lower().split())
    if not q:
        return 0, []
    t = text.lower()
    if t.startswith(q):
        return 0, list(range(len(q)))
    starts = _starts_of(text)
    for k, s in enumerate(starts):
        if t.startswith(q, s):
            return 10 + k, list(range(s, s + len(q)))
    words = q.split()
    if len(words) > 1:
        pos: List[int] = []
        cursor, skipped, last_k = 0, 0, -1
        ok = True
        for w in words:
            found = None
            for k, s in enumerate(starts):
                if s >= cursor and t.startswith(w, s):
                    found = (k, s)
                    break
            if found is None:
                ok = False
                break
            k, s = found
            skipped += max(0, k - last_k - 1)
            last_k = k
            pos.extend(range(s, s + len(w)))
            cursor = s + len(w)
        if ok:
            return 20 + skipped, pos
    if len(q) >= max(1, substring_min) and q in t:
        i = t.index(q)
        return 30 + i, list(range(i, i + len(q)))
    if not loose:
        return None
    sub = _subsequence(q.replace(" ", ""), text, starts, within_word)
    if sub is not None:
        return 100 + sub[0], sub[1]
    return None


def rank(query: str, text: str, substring_min: int = 1, within_word: bool = False) -> int:
    """:func:`match`'s rank, -1 = no match."""
    m = match(query, text, substring_min, within_word)
    return -1 if m is None else m[0]


# --------------------------------------------------------------------------- key caps
def key_parts(key: str) -> List[str]:
    """'Ctrl+Shift+A' -> ['Ctrl', 'Shift', 'A'] ('Ctrl++' keeps its '+')."""
    key = (key or "").strip()
    if not key:
        return []
    if key == "+":
        return ["+"]
    parts = []
    cur = ""
    for k, ch in enumerate(key):
        if ch == "+" and cur and k + 1 < len(key):
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


def draw_key_caps(
    p: QtGui.QPainter, right: int, mid_y: float, keys: Sequence[str], font: QtGui.QFont, dim: bool = False
) -> int:
    """Key caps for ``keys`` ending at x ``right``, centred on ``mid_y``; returns the left
    edge they took."""
    from .style.theme import COLORS, px

    fm = QtGui.QFontMetrics(font)
    pad, gap = px(5), px(3)
    h = fm.height() + px(2)
    caps = []
    for k in keys:
        caps.extend(key_parts(k))
    widths = [max(fm.horizontalAdvance(c) + 2 * pad, h) for c in caps]
    x = right - sum(widths) - gap * max(0, len(caps) - 1)
    left = x
    p.save()
    p.setFont(font)
    edge = QtGui.QColor(COLORS["control_edge"])
    fill = QtGui.QColor(COLORS["raised"])
    text = QtGui.QColor(COLORS["text_dim"] if dim else COLORS["label"])
    for c, w in zip(caps, widths):
        r = QtCore.QRectF(x + 0.5, mid_y - h / 2.0 + 0.5, w - 1, h - 1)
        p.setPen(QtGui.QPen(edge, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, px(3), px(3))
        p.setPen(text)
        p.drawText(r, QtCore.Qt.AlignCenter, c)
        x += w + gap
    p.restore()
    return int(left)


# --------------------------------------------------------------------------- rows
class Row:
    """One line of a search list."""

    __slots__ = (
        "kind",
        "text",
        "hits",
        "detail",
        "alias",
        "keys",
        "check",
        "chip",
        "enabled",
        "reason",
        "data",
        "cid",
        "tip",
        "icon",
        "spoken",
        "meta",
    )

    def __init__(
        self,
        kind: str,
        text: str,
        detail: str = "",
        data=None,
        *,
        hits=(),
        alias: str = "",
        keys: Sequence[str] = (),
        check: Optional[bool] = None,
        chip: str = "",
        enabled: bool = True,
        reason: str = "",
        cid: str = "",
        tip: str = "",
        icon: Optional[QtGui.QIcon] = None,
        spoken: str = "",
        meta=None,
    ):
        self.kind = kind
        self.text = text
        self.hits = tuple(hits or ())
        self.detail = detail
        self.alias = alias
        self.keys = tuple(keys or ())
        self.check = check
        self.chip = chip
        self.enabled = enabled
        self.reason = reason
        self.data = data
        self.cid = cid
        self.tip = tip
        self.icon = icon
        self.spoken = spoken
        self.meta = meta

    @property
    def selectable(self) -> bool:
        return self.kind not in ("header", "note")

    def accessible_text(self) -> str:
        """The plain line a screen reader says (and the tests read): the name, then its
        keys and its path, each after four spaces."""
        if self.spoken:
            return self.spoken
        out = self.text
        if self.keys:
            out += "    %s" % ", ".join(self.keys)
        if self.chip:
            out += "    %s" % self.chip
        if self.detail:
            out += "    %s" % self.detail
        if not self.enabled and self.reason:
            out += "    %s" % self.reason
        return out

    def __repr__(self) -> str:
        return "Row(%s %r)" % (self.kind, self.text)


def header(text: str) -> Row:
    return Row("header", text)


def note(text: str) -> Row:
    return Row("note", text)


class RowsModel(QtCore.QAbstractListModel):
    """The rows of a list. DisplayRole: the plain line; UserRole: ``Row.data``; UserRole + 1:
    the command id; :data:`ROW_ROLE`: the Row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: List[Row] = []

    def set_rows(self, rows: Iterable[Row]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rows(self) -> List[Row]:
        return list(self._rows)

    def row_at(self, k: int) -> Optional[Row]:
        return self._rows[k] if 0 <= k < len(self._rows) else None

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802 -- Qt naming
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=_DISPLAY_ROLE):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        r = self._rows[index.row()]
        # (the delegate asks for the row: first)
        if role == ROW_ROLE:
            return r
        if role == _DISPLAY_ROLE or role == _ACCESSIBLE_ROLE:
            return r.accessible_text()
        if role == _TOOLTIP_ROLE:
            return r.tip or (r.reason if not r.enabled else "") or None
        if role == _USER_ROLE:
            return r.data
        if role == _CID_ROLE:
            return r.cid
        return None

    def flags(self, index):
        r = self.row_at(index.row()) if index.isValid() else None
        if r is None or not r.selectable:
            return QtCore.Qt.NoItemFlags
        return QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable


class RowItem:
    """A row as QListWidget handed it out (``text()``, ``data(role)``): the tests and older
    callers read the list this way."""

    def __init__(self, model: RowsModel, k: int):
        self._model, self._k = model, k

    def text(self) -> str:
        return self._model.data(self._model.index(self._k)) or ""

    def data(self, role):
        return self._model.data(self._model.index(self._k), role)

    def row(self) -> Optional[Row]:
        return self._model.row_at(self._k)

    def toolTip(self) -> str:  # noqa: N802 -- QListWidgetItem naming
        return self._model.data(self._model.index(self._k), QtCore.Qt.ToolTipRole) or ""


class RowList(QtWidgets.QListView):
    """A list of :class:`Row` lines: the model and the delegate set up, the current row moved
    over headers, and QListWidget's ``count`` / ``item`` / ``currentRow`` / ``setCurrentRow``
    / ``currentItem`` for older callers."""

    def __init__(self, parent=None, delegate=None):
        super().__init__(parent)
        self.rows_model = RowsModel(self)
        self.setModel(self.rows_model)
        self.setItemDelegate(delegate or RowDelegate(self))
        self.setUniformItemSizes(False)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setTextElideMode(QtCore.Qt.ElideRight)
        self.setStyleSheet(
            "QListView { background: transparent; border: none; }"
            'QListView[kbdFocus="true"] { border: 1px solid #4da3ff; }'
        )

    # -- rows -------------------------------------------------------------------------------
    def set_rows(self, rows: Iterable[Row], current: int = -2) -> None:
        """Show ``rows``; the current row is ``current`` (-2 = the first selectable one)."""
        self.rows_model.set_rows(rows)
        first = self.first_selectable()
        if current == -2:
            current = first
        self.setCurrentRow(current)
        if current == first:
            self.scrollToTop()  # (the header over the first row shows too)

    def rows(self) -> List[Row]:
        return self.rows_model.rows()

    def first_selectable(self, start: int = 0, step: int = 1) -> int:
        n = self.rows_model.rowCount()
        k = start
        while 0 <= k < n:
            r = self.rows_model.row_at(k)
            if r is not None and r.selectable:
                return k
            k += step
        return -1

    def current_row_obj(self) -> Optional[Row]:
        return self.rows_model.row_at(self.currentRow())

    # -- QListWidget's answers ------------------------------------------------------------
    def count(self) -> int:
        return self.rows_model.rowCount()

    def item(self, k: int) -> Optional[RowItem]:
        return RowItem(self.rows_model, k) if 0 <= k < self.count() else None

    def currentRow(self) -> int:  # noqa: N802 -- Qt naming
        idx = self.currentIndex()
        return idx.row() if idx.isValid() else -1

    def setCurrentRow(self, k: int) -> None:  # noqa: N802 -- Qt naming
        if 0 <= k < self.count():
            idx = self.rows_model.index(k)
            self.setCurrentIndex(idx)
            self.scrollTo(idx)
        else:
            self.setCurrentIndex(QtCore.QModelIndex())

    def currentItem(self) -> Optional[RowItem]:  # noqa: N802 -- Qt naming
        k = self.currentRow()
        if k < 0:
            return None
        r = self.rows_model.row_at(k)
        return RowItem(self.rows_model, k) if r is not None and r.selectable else None

    # -- moving -----------------------------------------------------------------------------
    def move(self, step: int) -> None:
        """One selectable row up (-1) or down (+1), over the headers."""
        n = self.count()
        if not n:
            return
        cur = self.currentRow()
        if cur < 0:
            k = self.first_selectable(0 if step > 0 else n - 1, 1 if step > 0 else -1)
        else:
            k = self.first_selectable(cur + step, step)
        if k >= 0:
            self.setCurrentRow(k)

    def move_page(self, pages: int) -> None:
        """A page of rows up or down (Page Up / Page Down)."""
        n = self.count()
        if not n:
            return
        first = self.indexAt(QtCore.QPoint(4, 4))
        last = self.indexAt(QtCore.QPoint(4, self.viewport().height() - 4))
        span = max(1, (last.row() if last.isValid() else n - 1) - (first.row() if first.isValid() else 0))
        cur = max(0, self.currentRow())
        target = max(0, min(n - 1, cur + pages * span))
        step = 1 if pages > 0 else -1
        k = self.first_selectable(target, step)
        if k < 0:
            k = self.first_selectable(target, -step)
        if k >= 0:
            self.setCurrentRow(k)

    def move_end(self, last: bool) -> None:
        """The first (Home) or the last (End) row."""
        n = self.count()
        k = self.first_selectable(n - 1, -1) if last else self.first_selectable(0, 1)
        if k >= 0:
            self.setCurrentRow(k)


class RowDelegate(QtWidgets.QStyledItemDelegate):
    """Paints a :class:`Row` (see the module)."""

    def __init__(self, parent=None, two_lines: bool = False):
        super().__init__(parent)
        self.two_lines = two_lines

    # -- sizes ------------------------------------------------------------------------------
    @staticmethod
    def row_height() -> int:
        from .style.theme import density, font, sp

        fm = QtGui.QFontMetrics(font("body"))
        return max(16, fm.height()) + 2 * (sp(1) + int(density("row_pad")))

    @staticmethod
    def header_height() -> int:
        from .style.theme import font, sp

        return QtGui.QFontMetrics(font("caption")).height() + sp(2) + sp(1)

    def _heights(self) -> Tuple[int, int, int]:
        """(header, row, caption line) heights, kept while the theme is the same (Qt asks
        every row's size on each key press of a search)."""
        from .style.theme import font, generation

        gen = generation()
        kept = self.__dict__.get("_kept_heights")
        if kept is None or kept[0] != gen:
            caption = QtGui.QFontMetrics(font("caption")).height()
            kept = self._kept_heights = (gen, (self.header_height(), self.row_height(), caption))
        return kept[1]

    def sizeHint(self, option, index) -> QtCore.QSize:  # noqa: N802 -- Qt naming
        r = index.data(ROW_ROLE)
        w = option.rect.width() if option.rect.width() > 0 else 400
        head, h, caption = self._heights()
        if r is not None and r.kind == "header":
            return QtCore.QSize(w, head)
        if self.two_lines and r is not None and r.detail and r.kind not in ("note",):
            h += caption
        return QtCore.QSize(w, h)

    # -- painting ---------------------------------------------------------------------------
    def paint(self, p: QtGui.QPainter, option, index) -> None:
        from .style.theme import COLORS, font, px, sp

        r = index.data(ROW_ROLE)
        if r is None:
            return super().paint(p, option, index)
        rect = QtCore.QRect(option.rect)
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        if r.kind == "header":
            self._paint_header(p, rect, r, index.row() > 0)
            p.restore()
            return
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        hover = bool(option.state & QtWidgets.QStyle.State_MouseOver) and r.selectable
        if selected or hover:
            bg = QtGui.QColor(COLORS["selection" if selected else "hover"])
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QtCore.QRectF(rect.adjusted(px(2), 1, -px(2), -1)), px(4), px(4))
        dim = not r.enabled or r.kind == "note"
        text_col = QtGui.QColor(COLORS["sel_text" if selected else "text"])
        dim_col = QtGui.QColor(COLORS["sel_text_dim" if selected else "text_dim"])
        if dim:
            text_col = QtGui.QColor(dim_col)
        x = rect.left() + sp(2)
        line_h = self.row_height()
        top = rect.top()
        mid = top + line_h / 2.0
        icon_sz = px(16)
        if r.icon is not None and not r.icon.isNull():
            mode = QtGui.QIcon.Disabled if dim else (QtGui.QIcon.Selected if selected else QtGui.QIcon.Normal)
            pm = r.icon.pixmap(
                QtCore.QSize(icon_sz, icon_sz),
                p.device().devicePixelRatioF() if hasattr(p.device(), "devicePixelRatioF") else 1.0,
                mode,
            )
            p.drawPixmap(QtCore.QRect(x, int(mid - icon_sz / 2.0), icon_sz, icon_sz), pm)
        if r.icon is not None or r.kind not in ("note",):
            x += icon_sz + sp(2)
        right = rect.right() - sp(2)
        # the right end: key caps, a state chip or the kind, an on / off box
        cap_font = font("caption")
        if r.keys:
            right = draw_key_caps(p, right, mid, r.keys[:1], cap_font, dim) - sp(2)
        if r.chip:
            right = self._paint_chip(p, right, mid, r.chip, cap_font, dim_col, selected) - sp(2)
        if r.check is not None:
            right = self._paint_check(p, right, mid, bool(r.check), selected, dim) - sp(2)
        # the name with the matched letters marked, then the alias and the detail
        body = font("body")
        strong = font("body_strong")
        avail = max(0, right - x)
        used = self._paint_marked(p, x, top, line_h, avail, r.text, r.hits, body, strong, text_col, selected)
        x2 = x + used + sp(2)
        tail = []
        if r.alias:
            tail.append(r.alias)
        if r.detail and not self.two_lines:
            tail.append(r.detail)
        if tail and right - x2 > px(24):
            p.setFont(cap_font)
            p.setPen(dim_col)
            fm = QtGui.QFontMetrics(cap_font)
            txt = fm.elidedText("   ".join(tail), QtCore.Qt.ElideRight, right - x2)
            p.drawText(
                QtCore.QRect(x2, top, right - x2, line_h), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, txt
            )
        if self.two_lines and r.detail:
            p.setFont(cap_font)
            p.setPen(dim_col)
            fm = QtGui.QFontMetrics(cap_font)
            w = rect.right() - sp(2) - x
            txt = fm.elidedText(r.detail, QtCore.Qt.ElideRight, max(0, w))
            p.drawText(
                QtCore.QRect(x, top + line_h - sp(1) - px(2), w, fm.height()),
                QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft,
                txt,
            )
        p.restore()

    @staticmethod
    def _paint_header(p, rect, r, rule: bool) -> None:
        from .style.theme import COLORS, font, sp

        if rule:
            p.setPen(QtGui.QPen(QtGui.QColor(COLORS["border"]), 1))
            y = rect.top() + 1
            p.drawLine(rect.left() + sp(2), y, rect.right() - sp(2), y)
        f = font("caption")
        f.setWeight(QtGui.QFont.DemiBold)
        p.setFont(f)
        p.setPen(QtGui.QColor(COLORS["text_dim"]))
        p.drawText(
            rect.adjusted(sp(2), sp(1), -sp(2), 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, r.text
        )

    @staticmethod
    def _paint_chip(p, right, mid, text, f, colour, selected) -> int:
        from .style.theme import COLORS, px

        fm = QtGui.QFontMetrics(f)
        w = fm.horizontalAdvance(text) + px(10)
        h = fm.height() + px(2)
        rr = QtCore.QRectF(right - w + 0.5, mid - h / 2.0 + 0.5, w - 1, h - 1)
        p.save()
        p.setPen(QtGui.QPen(QtGui.QColor(COLORS["border"]), 1))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRoundedRect(rr, h / 2.0, h / 2.0)
        p.setFont(f)
        p.setPen(colour)
        p.drawText(rr, QtCore.Qt.AlignCenter, text)
        p.restore()
        return int(right - w)

    @staticmethod
    def _paint_check(p, right, mid, on, selected, dim) -> int:
        from .style.theme import COLORS, px

        s = px(12)
        box = QtCore.QRectF(right - s + 0.5, mid - s / 2.0 + 0.5, s - 1, s - 1)
        p.save()
        if on:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(COLORS["text_dim" if dim else "accent"]))
            p.drawRoundedRect(box, px(2), px(2))
            pen = QtGui.QPen(QtGui.QColor(COLORS["on_accent"]), max(1.2, px(1.6)))
            pen.setCapStyle(QtCore.Qt.RoundCap)
            pen.setJoinStyle(QtCore.Qt.RoundJoin)
            p.setPen(pen)
            x0, y0 = box.left(), box.top()
            p.drawPolyline(
                QtGui.QPolygonF(
                    [
                        QtCore.QPointF(x0 + s * 0.22, y0 + s * 0.52),
                        QtCore.QPointF(x0 + s * 0.42, y0 + s * 0.72),
                        QtCore.QPointF(x0 + s * 0.78, y0 + s * 0.30),
                    ]
                )
            )
        else:
            p.setPen(QtGui.QPen(QtGui.QColor(COLORS["control_edge"]), 1))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRoundedRect(box, px(2), px(2))
        p.restore()
        return int(right - s)

    @staticmethod
    def _paint_marked(p, x, top, h, avail, text, hits, body, strong, colour, selected) -> int:
        """``text`` at ``x`` with the letters at ``hits`` in the strong font (and the
        accent colour on an unselected row); the width it took (cut with '…' to ``avail``)."""
        from .style.theme import COLORS

        fm_b = QtGui.QFontMetrics(body)
        fm_s = QtGui.QFontMetrics(strong)
        hit = set(hits)
        mark = QtGui.QColor(COLORS["sel_text"] if selected else COLORS["accent"])
        # runs of marked / plain letters
        runs = []
        for i, ch in enumerate(text):
            m = i in hit
            if runs and runs[-1][0] == m:
                runs[-1][1] += ch
            else:
                runs.append([m, ch])
        ell = fm_b.horizontalAdvance("…")
        used = 0
        cut = False
        for m, chunk in runs:
            fm = fm_s if m else fm_b
            w = fm.horizontalAdvance(chunk)
            if used + w > avail:
                room = max(0, avail - used - ell)
                keep = ""
                for ch in chunk:
                    if fm.horizontalAdvance(keep + ch) > room:
                        break
                    keep += ch
                chunk, w, cut = keep, fm.horizontalAdvance(keep), True
            p.setFont(strong if m else body)
            p.setPen(mark if m else colour)
            p.drawText(
                QtCore.QRect(int(x + used), top, int(w) + 2, h),
                QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                chunk,
            )
            used += w
            if cut:
                p.setFont(body)
                p.setPen(colour)
                p.drawText(
                    QtCore.QRect(int(x + used), top, ell + 2, h),
                    QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                    "…",
                )
                used += ell
                break
        return int(used)
