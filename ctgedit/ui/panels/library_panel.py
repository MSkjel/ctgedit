"""The Library panel: search and place symbols and genies."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import collections
import html
import json
import math
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ...io.catalog import (
    INCLUDE_CYCLE,
    INCLUDE_IMPLICIT,
    INCLUDE_MISSING,
    Entry,
    catalog_for,
    is_super_genie,
    natural_key,
)
from ..browser import search as _search
from ..browser.project_tree import FLAT, TREE, LabelRole, NoteRole, StyleRole, _RowDelegate
from ..browser.thumbs import ThumbnailService
from . import panel_options as _panels
from ..style import theme as _theme
from ..prefs.settings import app_settings
from ..style.theme import COLORS, SearchField, TextMatch, fixed, icon, tracked, type_qss

__all__ = [
    "LIBELEM_MIME",
    "KIND_EXT",
    "LibraryPanel",
    "element_mime",
    "parse_libelem_mime",
    "score_tokens",
    "key_background",
    "fit_pixmap",
    "paste_linked",
    "set_paste_linked",
]

#: whether a placed symbol stays linked to its library (CtDraw's Paste Symbol
#: 'Linked' check box -- PasteLibrary_Linked / PasteLibrary_Unlinked)
PASTE_LINKED_KEY = "library/paste_linked"


def paste_linked() -> bool:
    """Place symbols LINKED to their library (the default) or as unlinked copies."""
    try:
        return bool(app_settings().value(PASTE_LINKED_KEY, True, type=bool))
    except Exception:  # noqa: BLE001
        return True


def set_paste_linked(on: bool) -> None:
    s = app_settings()
    s.setValue(PASTE_LINKED_KEY, bool(on))
    s.sync()


LIBELEM_MIME = "application/x-ctgedit-libelem"
KIND_EXT = {"symbol": "ctl", "genie": "ctm"}
EXT_KIND = {v: k for k, v in KIND_EXT.items()}
_KIND_NOUN = {"symbol": ("symbol", "symbols"), "genie": ("genie", "genies")}

_S = "librarypanel/"
#: the recent elements kept: the default of Settings > Pages and libraries > Library panel >
#: Recent elements kept (ui/prefs/timing_options.LIBRARY_RECENT_KEPT)
MAX_RECENT = 40
HOME_RECENT = 12
CARD_MIN, CARD_MAX, CARD_DEFAULT = 56, 184, 100

EntryRole = QtCore.Qt.UserRole + 40
HeaderRole = QtCore.Qt.UserRole + 41
ScopeRole = QtCore.Qt.UserRole + 42
_LibKeysRole = QtCore.Qt.UserRole + 43  # a project row: its libraries' keys
_CycleRole = QtCore.Qt.UserRole + 44  # a project row already on the path above

_WELL = fixed("well")  # light well: white page bg keyed out
_WELL_EDGE = fixed("well_edge")
_CARD = tracked("#262a30")  # follow the theme (updated in place)
_HOVER = tracked("#2d3238")
_GOLD = fixed("star_gold")

# card geometry (grid mode)
_PAD = 6  # card inner padding
# the text lines, a section header, a list row and a chip (px at 100 %: times the text size,
# Settings > Appearance > Text size, as the page browser's cards)
_NAME_H = 17
_SUB_H = 14
_HEADER_H = 28
_LIST_H = 38
_CHIP_H = 13
_LIST_WELL = QtCore.QSize(46, 32)
_SPACING = 3

Scope = Tuple[str, ...]  # ("all",) ("favourites",) ("recent",)
#                                  ("project", name) ("library", normcased path)
_ALL: Scope = ("all",)
_FAV: Scope = ("favourites",)
_RECENT: Scope = ("recent",)


# --------------------------------------------------------------------------- mime
def element_mime(library: str, element: str, ext: str) -> QtCore.QMimeData:
    """Drag payload of one library element: :data:`LIBELEM_MIME` =
    ``b'library|element|ext'`` plus ``text/plain`` ``library.element`` (drops
    into text fields insert the reference)."""
    md = QtCore.QMimeData()
    md.setData(LIBELEM_MIME, ("%s|%s|%s" % (library, element, ext)).encode("utf-8"))
    md.setText("%s.%s" % (library, element))
    return md


def parse_libelem_mime(md: Optional[QtCore.QMimeData]) -> Optional[Tuple[str, str, str]]:
    """``(library, element, ext)`` from a drag payload, or None."""
    if md is None or not md.hasFormat(LIBELEM_MIME):
        return None
    try:
        raw = bytes(md.data(LIBELEM_MIME)).decode("utf-8")
    except UnicodeDecodeError, TypeError:
        return None
    parts = raw.split("|")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1], parts[2].lower().lstrip(".")


# --------------------------------------------------------------------------- search
# the ranking is shared with the page browser's element lists (browser.search)
_words = _search.words
_subsequence = _search.subsequence
_prep = _search.prep
_token_score = _search.token_score
score_tokens = _search.score_tokens


def _file_stamp(path: str) -> tuple:
    """(path, mtime_ns, size) of a file, (path, None, None) when it is not there."""
    try:
        st = os.stat(path)
    except OSError:
        return (path, None, None)
    return (path, st.st_mtime_ns, st.st_size)


# --------------------------------------------------------------------------- glyphs
_GLYPHS: Dict[tuple, QtGui.QIcon] = {}


def _star_path(cx: float, cy: float, r: float) -> QtGui.QPainterPath:
    path = QtGui.QPainterPath()
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.45
        pt = QtCore.QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a))
        if k == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    return path


def _paint_star(p: QtGui.QPainter, rect: QtCore.QRectF, filled: bool, colour: QtGui.QColor) -> None:
    path = _star_path(rect.center().x(), rect.center().y() + rect.height() * 0.04, rect.width() / 2)
    p.save()
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    if filled:
        p.setPen(QtGui.QPen(fixed("star_edge"), 0.9))
        p.setBrush(colour)
    else:
        p.setPen(QtGui.QPen(colour, 1.3))
        p.setBrush(QtCore.Qt.NoBrush)
    p.drawPath(path)
    p.restore()


def glyph(name: str, size: int = 16, colour: Optional[str] = None) -> QtGui.QIcon:
    """Small panel glyphs painted like :func:`theme.icon` (20-unit box)."""
    key = (name, size, colour)
    hit = _GLYPHS.get(key)
    if hit is not None:
        return hit
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.scale(size / 20.0, size / 20.0)
    fg = QtGui.QColor(colour or COLORS["text"])
    pen = QtGui.QPen(fg, 1.6)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    R = QtCore.QRectF
    if name == "star":
        _paint_star(p, R(2, 2, 16, 16), True, _GOLD)
    elif name == "star_outline":
        _paint_star(p, R(2.5, 2.5, 15, 15), False, fg)
    elif name == "clock":
        p.drawEllipse(R(3, 3, 14, 14))
        p.drawLine(QtCore.QPointF(10, 10), QtCore.QPointF(10, 5.8))
        p.drawLine(QtCore.QPointF(10, 10), QtCore.QPointF(13.2, 11.8))
    elif name in ("all", "grid_view"):
        for x, y in ((3.5, 3.5), (11, 3.5), (3.5, 11), (11, 11)):
            p.drawRoundedRect(R(x, y, 5.5, 5.5), 1.2, 1.2)
    elif name == "list_view":
        for y in (5.0, 10.0, 15.0):
            p.drawRoundedRect(R(3, y - 1.6, 3.2, 3.2), 0.8, 0.8)
            p.drawLine(QtCore.QPointF(8.5, y), QtCore.QPointF(17, y))
    elif name == "project":
        path = QtGui.QPainterPath()
        path.moveTo(2.5, 5.5)
        path.lineTo(7.5, 5.5)
        path.lineTo(9, 7)
        path.lineTo(17.5, 7)
        path.lineTo(17.5, 15.5)
        path.lineTo(2.5, 15.5)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "library":
        p.drawRoundedRect(R(3, 4, 3.6, 12), 0.8, 0.8)
        p.drawRoundedRect(R(8, 4, 3.6, 12), 0.8, 0.8)
        p.save()
        p.translate(15.3, 4.4)
        p.rotate(14)
        p.drawRoundedRect(R(0, 0, 3.4, 11.6), 0.8, 0.8)
        p.restore()
    elif name == "sidebar":
        p.drawRoundedRect(R(2.5, 3.5, 15, 13), 1.6, 1.6)
        p.drawLine(QtCore.QPointF(7.5, 3.8), QtCore.QPointF(7.5, 16.2))
    elif name == "more":
        p.setBrush(fg)
        p.setPen(QtCore.Qt.NoPen)
        for x in (4.5, 10, 15.5):
            p.drawEllipse(QtCore.QPointF(x, 10), 1.6, 1.6)
    elif name == "close":
        p.drawLine(QtCore.QPointF(6, 6), QtCore.QPointF(14, 14))
        p.drawLine(QtCore.QPointF(14, 6), QtCore.QPointF(6, 14))
    elif name == "chevron":
        p.drawPolyline([QtCore.QPointF(8, 5.5), QtCore.QPointF(12.5, 10), QtCore.QPointF(8, 14.5)])
    elif name == "link":
        # two chain links (the Linked paste toggle)
        for dx in (0.0, 6.0):
            p.save()
            p.translate(3.2 + dx, 10)
            p.rotate(-35)
            p.drawRoundedRect(R(-1.0, -2.6, 8.0, 5.2), 2.6, 2.6)
            p.restore()
    p.end()
    out = QtGui.QIcon(pm)
    _GLYPHS[key] = out
    return out


# --------------------------------------------------------------------------- thumbnails
def key_background(pm: QtGui.QPixmap) -> QtGui.QPixmap:
    """Make a thumbnail's white page background transparent."""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover -- numpy is a dependency
        return pm
    img = pm.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    if img.isNull() or w < 3 or h < 3 or (img.pixel(0, 0) & 0xFFFFFF) != 0xFFFFFF:
        return pm
    a = (
        np.frombuffer(img.constBits(), dtype=np.uint32, count=img.bytesPerLine() // 4 * h)
        .reshape(h, -1)[:, :w]
        .copy()
    )
    white = (a & 0xFFFFFF) == 0xFFFFFF
    m = np.zeros_like(white)
    m[0, :], m[-1, :], m[:, 0], m[:, -1] = white[0, :], white[-1, :], white[:, 0], white[:, -1]
    for _ in range(w + h):  # 4-connected flood: whole white RUNS per sweep
        g = _spread_runs(_spread_runs(m, white).T, white.T).T
        if np.array_equal(g, m):
            break
        m = g
    a[m] = 0
    out = QtGui.QImage(a.tobytes(), w, h, w * 4, QtGui.QImage.Format_ARGB32).copy()
    return QtGui.QPixmap.fromImage(out)


def _spread_runs(m, white):
    """Mark every horizontal white run that contains a marked pixel (one
    flood-fill sweep along rows; transpose for columns)."""
    import numpy as np

    h, w = white.shape
    ids = np.cumsum(~white, axis=1) + (np.arange(h)[:, None] * (w + 1))
    hit = np.zeros(h * (w + 1) + 1, dtype=bool)
    hit[ids[m]] = True
    return hit[ids] & white


def fit_pixmap(pm: QtGui.QPixmap, w: int, h: int, max_up: int = 6) -> QtGui.QPixmap:
    """Fit ``pm`` into w x h: whole-multiple nearest upscaling for small
    thumbnails (crisp icons), smooth downscaling for big ones."""
    iw, ih = pm.width(), pm.height()
    if iw <= 0 or ih <= 0 or w <= 0 or h <= 0:
        return pm
    f = min(w / iw, h / ih)
    if f >= 2:
        k = min(int(f), max_up)
        return pm.scaled(iw * k, ih * k, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.FastTransformation)
    if f >= 1:
        return pm
    return pm.scaled(
        max(1, int(iw * f)), max(1, int(ih * f)), QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation
    )


class _PixLru:
    """Pixmap LRU capped by BYTES (w*h*4): a scroll through 5k cards must not
    pin hundreds of MB on a small machine."""

    def __init__(self, limit_bytes: int):
        self.limit = int(limit_bytes)
        self.bytes = 0
        self._d: "collections.OrderedDict" = collections.OrderedDict()

    @staticmethod
    def _size(pm) -> int:
        return max(1, pm.width() * pm.height() * 4)

    def get(self, k):
        v = self._d.get(k)
        if v is not None:
            self._d.move_to_end(k)
        return v

    def put(self, k, v) -> None:
        old = self._d.pop(k, None)
        if old is not None:
            self.bytes -= self._size(old)
        self._d[k] = v
        self.bytes += self._size(v)
        while self.bytes > self.limit and len(self._d) > 1:
            _k, ev = self._d.popitem(last=False)
            self.bytes -= self._size(ev)

    def __len__(self) -> int:
        return len(self._d)


# --------------------------------------------------------------------------- model
class _Header:
    __slots__ = ("title", "subtitle", "count", "scope", "glyph")

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        count: int = 0,
        scope: Optional[Scope] = None,
        glyph_name: str = "library",
    ):
        self.title, self.subtitle, self.count = title, subtitle, count
        self.scope, self.glyph = scope, glyph_name


# Qt's values the grid model reads for every row and role, looked up once (a view laid out
# asks every row)
_DISPLAY_ROLE = QtCore.Qt.DisplayRole
_ACCESSIBLE_ROLE = QtCore.Qt.AccessibleTextRole
_NO_FLAGS = QtCore.Qt.NoItemFlags
_ENTRY_FLAGS = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDragEnabled


class _GridModel(QtCore.QAbstractListModel):
    """Flat rows: section headers (disabled -> keyboard skips them) + entries."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._by_key: Dict[str, List[int]] = {}

    def set_rows(self, rows: list) -> None:
        self.beginResetModel()
        self._rows = rows
        self._by_key = {}
        for r, x in enumerate(rows):
            if isinstance(x, Entry):
                self._by_key.setdefault(x.key, []).append(r)
        self.endResetModel()

    def rows_for_key(self, key: str) -> List[int]:
        return self._by_key.get(key, [])

    def entry(self, row: int) -> Optional[Entry]:
        x = self._rows[row] if 0 <= row < len(self._rows) else None
        return x if isinstance(x, Entry) else None

    def header(self, row: int) -> Optional[_Header]:
        x = self._rows[row] if 0 <= row < len(self._rows) else None
        return x if isinstance(x, _Header) else None

    def entries(self) -> List[Entry]:
        return [x for x in self._rows if isinstance(x, Entry)]

    def headers(self) -> List[_Header]:
        return [x for x in self._rows if isinstance(x, _Header)]

    def first_entry_row(self, start: int = 0, step: int = 1) -> int:
        r = start
        while 0 <= r < len(self._rows):
            if isinstance(self._rows[r], Entry):
                return r
            r += step
        return -1

    # -- Qt API ------------------------------------------------------------------
    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def flags(self, index):
        if not index.isValid() or not isinstance(self._rows[index.row()], Entry):
            return _NO_FLAGS
        return _ENTRY_FLAGS

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        x = self._rows[index.row()]
        if isinstance(x, _Header):
            if role == _DISPLAY_ROLE:
                return x.title
            if role == HeaderRole:
                return x
            return None
        if role == _DISPLAY_ROLE:
            return x.name
        if role == EntryRole:
            return x
        if role == _ACCESSIBLE_ROLE:
            return x.reference
        return None

    def mimeTypes(self):
        return [LIBELEM_MIME, "text/plain"]

    def mimeData(self, indexes):
        for i in indexes:
            e = self.entry(i.row())
            if e is not None:
                return element_mime(e.library, e.name, KIND_EXT.get(e.kind, "ctl"))
        return QtCore.QMimeData()

    def supportedDragActions(self):
        return QtCore.Qt.CopyAction


# --------------------------------------------------------------------------- delegate
class _CardDelegate(QtWidgets.QStyledItemDelegate):
    """Cards (grid), rows (list) and section headers."""

    def __init__(self, view: "_GridView", panel: "LibraryPanel"):
        super().__init__(view)
        self._view = view
        self._panel = panel
        self._fonts_for = None  # the text size the fonts were made at
        self._fonts()

    def _fonts(self) -> None:
        """The fonts from the type scale (theme.TYPE) at the text size, made again when it
        changed: the name (body), the library line (caption), the headers (body, bold) and
        the chips (9 px at 100 %, bold)."""
        scale = _theme.ui_scale()
        if self._fonts_for == scale:
            return
        self._fonts_for = scale
        self.f_name = _theme.font("body")
        self.f_sub = _theme.font("caption")
        self.f_head = _theme.font("body")
        self.f_head.setBold(True)
        self.f_chip = QtGui.QFont("Segoe UI")
        self.f_chip.setPixelSize(max(6, _theme.px(9)))
        self.f_chip.setBold(True)

    # -- geometry (shared with the view's hit tests) -------------------------------
    def well_rect(self, r: QtCore.QRect) -> QtCore.QRect:
        if self._panel.view_mode() == "list":
            return QtCore.QRect(
                r.left() + 6,
                r.top() + (r.height() - _LIST_WELL.height()) // 2,
                _LIST_WELL.width(),
                _LIST_WELL.height(),
            )
        ww = r.width() - 2 * _PAD
        return QtCore.QRect(r.left() + _PAD, r.top() + _PAD, ww, self.well_height(ww))

    @staticmethod
    def well_height(ww: int) -> int:
        return max(24, int(round(ww * 0.72)))

    @staticmethod
    def shows_library(ww: int) -> bool:
        return ww >= 76

    def card_height(self, cell_w: int) -> int:
        ww = cell_w - 2 * _PAD
        return (
            _PAD
            + self.well_height(ww)
            + 4
            + _theme.px(_NAME_H)
            + (_theme.px(_SUB_H) if self.shows_library(ww) else 0)
            + _PAD
            - 2
        )

    def star_rect(self, r: QtCore.QRect) -> QtCore.QRect:
        if self._panel.view_mode() == "list":
            return QtCore.QRect(r.right() - 26, r.center().y() - 9, 18, 18)
        w = self.well_rect(r)
        return QtCore.QRect(w.right() - 19, w.top() + 2, 18, 18)

    def sizeHint(self, option, index) -> QtCore.QSize:  # noqa: N802 (Qt API)
        full = max(40, self._view.usable_width() - 2 * _SPACING)
        if index.data(HeaderRole) is not None:
            return QtCore.QSize(full, _theme.px(_HEADER_H))
        if self._panel.view_mode() == "list":
            return QtCore.QSize(full, _theme.px(_LIST_H))
        cw = self._view.cell_width()
        return QtCore.QSize(cw, self.card_height(cw))

    # -- paint -------------------------------------------------------------------------
    def paint(self, p: QtGui.QPainter, opt, index) -> None:
        hdr = index.data(HeaderRole)
        self._fonts()
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        try:
            if hdr is not None:
                self._paint_header(p, opt, index, hdr)
                return
            e = index.data(EntryRole)
            if e is None:
                return
            if self._panel.view_mode() == "list":
                self._paint_row(p, opt, index, e)
            else:
                self._paint_card(p, opt, index, e)
        finally:
            p.restore()

    def _paint_header(self, p, opt, index, hdr: _Header) -> None:
        r = opt.rect
        hot = hdr.scope is not None and self._view.hover_row() == index.row()
        base = QtCore.QRect(r.left() + 2, r.top() + 4, r.width() - 4, r.height() - 6)
        if hot:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(COLORS["hover"]))
            p.drawRoundedRect(QtCore.QRectF(base), 4, 4)
        x = base.left() + 4
        gl = glyph(hdr.glyph, 14, COLORS["accent"] if hot else COLORS["text_dim"])
        gl.paint(p, QtCore.QRect(x, base.center().y() - 7, 14, 14))
        x += 20
        p.setFont(self.f_sub)
        fm_s = QtGui.QFontMetrics(self.f_sub)
        cnt = str(hdr.count) if hdr.count else ""
        cw = fm_s.horizontalAdvance(cnt) + 6 if cnt else 0
        right = base.right() - 6 - cw
        p.setFont(self.f_head)
        fm = QtGui.QFontMetrics(self.f_head)
        title_w = min(fm.horizontalAdvance(hdr.title), max(20, right - x))
        p.setPen(QtGui.QColor(COLORS["accent"]) if hot else QtGui.QColor(COLORS["text"]))
        p.drawText(
            QtCore.QRect(x, base.top(), title_w, base.height()),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            fm.elidedText(hdr.title, QtCore.Qt.ElideRight, title_w),
        )
        x += title_w + 8
        if hdr.subtitle and right - x > 24:
            p.setFont(self.f_sub)
            p.setPen(QtGui.QColor(COLORS["text_dim"]))
            p.drawText(
                QtCore.QRect(x, base.top(), right - x, base.height()),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                fm_s.elidedText(hdr.subtitle, QtCore.Qt.ElideRight, right - x),
            )
        if cnt:
            p.setFont(self.f_sub)
            p.setPen(QtGui.QColor(COLORS["text_dim"]))
            p.drawText(
                QtCore.QRect(base.right() - 6 - cw, base.top(), cw, base.height()),
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                cnt,
            )
        p.setPen(QtGui.QPen(QtGui.QColor(COLORS["border"]), 1))
        p.drawLine(
            QtCore.QPointF(base.left() + 2, base.bottom() + 0.5),
            QtCore.QPointF(base.right() - 2, base.bottom() + 0.5),
        )

    def _frame(self, p, opt, r: QtCore.QRect) -> Tuple[bool, bool]:
        sel = bool(opt.state & QtWidgets.QStyle.State_Selected)
        hov = bool(opt.state & QtWidgets.QStyle.State_MouseOver)
        p.setPen(
            QtGui.QPen(QtGui.QColor(COLORS["accent"]), 1.6)
            if sel
            else QtGui.QPen(QtGui.QColor(COLORS["border"]) if hov else _CARD, 1.0)
        )
        p.setBrush(QtGui.QColor(COLORS["selection"]) if sel else (_HOVER if hov else _CARD))
        p.drawRoundedRect(QtCore.QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        return sel, hov

    def _paint_well(self, p, well: QtCore.QRect, e: Entry) -> None:
        p.setPen(QtGui.QPen(_WELL_EDGE, 1))
        p.setBrush(_WELL)
        p.drawRoundedRect(QtCore.QRectF(well).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        inner = well.adjusted(4, 4, -4, -4)
        pm = self._panel.card_pixmap(e, inner.width(), inner.height())
        if pm is not None and not pm.isNull():
            p.drawPixmap(
                inner.left() + (inner.width() - pm.width()) // 2,
                inner.top() + (inner.height() - pm.height()) // 2,
                pm,
            )
        else:
            p.setFont(self.f_sub)
            # (on the light well whatever the theme: a grey that reads there)
            p.setPen(fixed("well_text"))
            p.drawText(well, QtCore.Qt.AlignCenter, "No preview" if self._panel.has_no_thumbnail(e) else "…")

    def _paint_star(self, p, rect: QtCore.QRect, fav: bool, hov: bool) -> None:
        if fav:
            _paint_star(p, QtCore.QRectF(rect).adjusted(2, 2, -2, -2), True, _GOLD)
        elif hov:
            _paint_star(
                p, QtCore.QRectF(rect).adjusted(2.5, 2.5, -2.5, -2.5), False, QtGui.QColor(COLORS["text_dim"])
            )

    @staticmethod
    def _diamond(p, rect: QtCore.QRect) -> None:
        """CtDraw's picker mark of a FLASHING symbol: a diamond split into
        its two colour halves (the two pictures it alternates)."""
        c = QtCore.QPointF(rect.center())
        hw, hh = rect.width() / 2.0, rect.height() / 2.0
        top, right = QtCore.QPointF(c.x(), c.y() - hh), QtCore.QPointF(c.x() + hw, c.y())
        bottom, left = QtCore.QPointF(c.x(), c.y() + hh), QtCore.QPointF(c.x() - hw, c.y())
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(fixed("flash_a"))
        p.drawPolygon(QtGui.QPolygonF([top, right, bottom]))
        p.setBrush(fixed("flash_b"))
        p.drawPolygon(QtGui.QPolygonF([top, bottom, left]))
        p.setPen(QtGui.QPen(fixed("mark_edge"), 1))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawPolygon(QtGui.QPolygonF([top, right, bottom, left]))

    def _chip(self, p, x: int, y: int, text: str, col: QtGui.QColor) -> None:
        p.setFont(self.f_chip)
        fm = QtGui.QFontMetrics(self.f_chip)
        br = QtCore.QRect(x, y, fm.horizontalAdvance(text) + 8, _theme.px(_CHIP_H))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QtCore.QRectF(br), 3, 3)
        p.setPen(fixed("badge_text"))
        p.drawText(br, QtCore.Qt.AlignCenter, text)

    def _paint_card(self, p, opt, index, e: Entry) -> None:
        r = opt.rect
        sel, hov = self._frame(p, opt, r)
        well = self.well_rect(r)
        self._paint_well(p, well, e)
        self._paint_star(p, self.star_rect(r), self._panel.is_favourite(e), hov)
        if e.shadowed_by:
            self._chip(
                p, well.left() + 3, well.bottom() - 2 - _theme.px(_CHIP_H), "Hidden", fixed("badge_grey")
            )
        if self._panel.is_flashing(e):
            self._diamond(p, QtCore.QRect(well.left() + 3, well.top() + 3, 12, 12))
        y = well.bottom() + 4
        p.setFont(self.f_name)
        fm = QtGui.QFontMetrics(self.f_name)
        nr = QtCore.QRect(r.left() + _PAD + 1, y, r.width() - 2 * _PAD - 2, _theme.px(_NAME_H))
        p.setPen(QtGui.QColor(COLORS["sel_text"] if sel else COLORS["text"]))
        p.drawText(
            nr,
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            fm.elidedText(e.name, QtCore.Qt.ElideMiddle, nr.width()),
        )
        if self.shows_library(well.width()):
            p.setFont(self.f_sub)
            fm2 = QtGui.QFontMetrics(self.f_sub)
            sr = QtCore.QRect(nr.left(), nr.bottom() + 1, nr.width(), _theme.px(_SUB_H))
            p.setPen(QtGui.QColor(COLORS["sel_text_dim" if sel else "text_dim"]))
            p.drawText(
                sr,
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                fm2.elidedText(self._panel.card_subtitle(e), QtCore.Qt.ElideRight, sr.width()),
            )

    def _paint_row(self, p, opt, index, e: Entry) -> None:
        r = opt.rect
        sel, hov = self._frame(p, opt, r)
        well = self.well_rect(r)
        self._paint_well(p, well, e)
        star = self.star_rect(r)
        self._paint_star(p, star, self._panel.is_favourite(e), hov)
        if self._panel.is_flashing(e):
            self._diamond(p, QtCore.QRect(well.left() + 1, well.top() + 1, 10, 10))
        x = well.right() + 10
        w = star.left() - 6 - x
        p.setFont(self.f_name)
        fm = QtGui.QFontMetrics(self.f_name)
        p.setPen(QtGui.QColor(COLORS["sel_text"] if sel else COLORS["text"]))
        px = _theme.px
        p.drawText(
            QtCore.QRect(x, r.top() + px(3), w, px(17)),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            fm.elidedText(e.name, QtCore.Qt.ElideMiddle, w),
        )
        p.setFont(self.f_sub)
        fm2 = QtGui.QFontMetrics(self.f_sub)
        sub = "%s  ·  %s" % (e.library, e.project)
        if e.shadowed_by:
            sub += "  ·  hidden by %s" % e.shadowed_by
        p.setPen(QtGui.QColor(COLORS["sel_text_dim" if sel else "text_dim"]))
        p.drawText(
            QtCore.QRect(x, r.top() + px(19), w, px(15)),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            fm2.elidedText(sub, QtCore.Qt.ElideRight, w),
        )


# --------------------------------------------------------------------------- view
class _GridView(QtWidgets.QListView):
    """Wrapping flow of cards (or a list) with headers that span the row."""

    def __init__(self, panel: "LibraryPanel"):
        super().__init__(panel)
        self._panel = panel
        self._hover = -1
        self._cell_cache: Tuple = ()
        self.setObjectName("libGrid")
        self.setViewMode(QtWidgets.QListView.ListMode)
        self.setMovement(QtWidgets.QListView.Static)
        self.setResizeMode(QtWidgets.QListView.Adjust)
        self.setUniformItemSizes(False)
        self.setSpacing(_SPACING)
        self.setMouseTracking(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.verticalScrollBar().setSingleStep(24)
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragOnly)
        self.setDefaultDropAction(QtCore.Qt.CopyAction)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setAttribute(QtCore.Qt.WA_Hover, True)
        # a rebuild lays the rows out in batches between events (the cards
        # have no uniform size: Qt asks every row's size)
        self.setLayoutMode(QtWidgets.QListView.Batched)
        self.setBatchSize(self.BATCH)
        self.apply_mode("grid")

    #: rows laid out per batch (the first batch at once)
    BATCH = 400

    def full_layout(self) -> None:
        """Lay out EVERY row now (to scroll to a row past the first batch)."""
        self.setLayoutMode(QtWidgets.QListView.SinglePass)
        try:
            self.doItemsLayout()
        finally:
            self.setLayoutMode(QtWidgets.QListView.Batched)

    def scrollTo(self, index, hint=QtWidgets.QAbstractItemView.EnsureVisible):  # noqa: N802
        # a row the batched layout has not reached yet has no rectangle, and
        # QListView would not scroll to it (Show in Library, a view-mode
        # switch, keyboard navigation right after a rebuild)
        if index.isValid() and not self.visualRect(index).isValid():
            self.full_layout()
        super().scrollTo(index, hint)

    def apply_mode(self, mode: str) -> None:
        if mode == "list":
            self.setFlow(QtWidgets.QListView.TopToBottom)
            self.setWrapping(False)
        else:
            self.setFlow(QtWidgets.QListView.LeftToRight)
            self.setWrapping(True)
        self.relayout()

    # -- geometry -----------------------------------------------------------------------
    def usable_width(self) -> int:
        """Viewport width assuming the vertical scrollbar is ALWAYS shown, so
        the column count never oscillates as the scrollbar comes and goes."""
        sb = max(10, self.verticalScrollBar().sizeHint().width())
        return max(60, self.width() - 2 * self.frameWidth() - sb)

    def cell_width(self) -> int:
        """Card width: the target size stretched so whole cards fill the row."""
        uw, target = self.usable_width(), self._panel.card_size() + 2 * _PAD
        key = (uw, target)
        if self._cell_cache and self._cell_cache[0] == key:
            return self._cell_cache[1]
        avail = uw - 2 * _SPACING
        # ROUND the column count: cards flex 0.75x..1.25x around the target
        # (a 250 px dock gets two cards, not one giant one)
        n = max(1, int(avail / (target + 2 * _SPACING) + 0.5))
        cw = avail // n - 2 * _SPACING
        # a lone column never balloons past 1.35x the chosen size (the slider
        # keeps a visible effect in a narrow dock; the rest of the row stays empty)
        cw = max(CARD_MIN + 2 * _PAD - 8, min(cw, int(target * 1.35)))
        self._cell_cache = (key, cw)
        return cw

    def columns(self) -> int:
        if self._panel.view_mode() == "list":
            return 1
        avail = self.usable_width() - 2 * _SPACING
        return max(1, avail // (self.cell_width() + 2 * _SPACING))

    def relayout(self) -> None:
        self._cell_cache = ()
        self.doItemsLayout()
        self.viewport().update()

    def hover_row(self) -> int:
        return self._hover

    # -- navigation ----------------------------------------------------------------------
    def moveCursor(self, action, modifiers):  # noqa: N802 (Qt API)
        m = self.model()
        cur = self.currentIndex()
        CA = QtWidgets.QAbstractItemView.CursorAction
        if m is None or m.rowCount() == 0:
            return super().moveCursor(action, modifiers)
        if action == CA.MoveHome:
            r = m.first_entry_row(0, 1)
            return m.index(r, 0) if r >= 0 else cur
        if action == CA.MoveEnd:
            r = m.first_entry_row(m.rowCount() - 1, -1)
            return m.index(r, 0) if r >= 0 else cur
        if not cur.isValid():
            r = m.first_entry_row(0, 1)
            return m.index(r, 0) if r >= 0 else cur
        idx = super().moveCursor(action, modifiers)
        stuck = not idx.isValid() or idx == cur or m.entry(idx.row()) is None
        if stuck and action in (CA.MoveRight, CA.MoveNext):
            r = m.first_entry_row(cur.row() + 1, 1)
            return m.index(r, 0) if r >= 0 else cur
        if stuck and action in (CA.MoveLeft, CA.MovePrevious):
            r = m.first_entry_row(cur.row() - 1, -1)
            return m.index(r, 0) if r >= 0 else cur
        if stuck and action in (CA.MoveDown, CA.MovePageDown):
            r = m.first_entry_row(cur.row() + self.columns(), 1)
            return m.index(r, 0) if r >= 0 else cur
        if stuck and action in (CA.MoveUp, CA.MovePageUp):
            r = m.first_entry_row(cur.row() - self.columns(), -1)
            return m.index(r, 0) if r >= 0 else cur
        return idx

    # -- mouse -----------------------------------------------------------------------------
    def _pos(self, ev) -> QtCore.QPoint:
        return ev.position().toPoint() if hasattr(ev, "position") else ev.pos()

    def mousePressEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        self._panel.hide_preview()
        pos = self._pos(ev)
        idx = self.indexAt(pos)
        if idx.isValid() and ev.button() == QtCore.Qt.LeftButton:
            hdr = idx.data(HeaderRole)
            if hdr is not None:
                if hdr.scope is not None:
                    self._panel.set_scope(hdr.scope)
                ev.accept()
                return
            e = idx.data(EntryRole)
            dlg = self.itemDelegate()
            if (
                e is not None
                and isinstance(dlg, _CardDelegate)
                and dlg.star_rect(self.visualRect(idx)).contains(pos)
            ):
                self._panel.toggle_favourite(e)
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        idx = self.indexAt(self._pos(ev))
        row = idx.row() if idx.isValid() else -1
        if row != self._hover:
            self._hover = row
            self._panel.hide_preview()
            self.viewport().update()
        hdr = idx.data(HeaderRole) if idx.isValid() else None
        self.viewport().setCursor(
            QtCore.Qt.PointingHandCursor
            if hdr is not None and hdr.scope is not None
            else QtCore.Qt.ArrowCursor
        )
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        self._hover = -1
        self._panel.hide_preview()
        self.viewport().update()
        super().leaveEvent(ev)

    def wheelEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        self._panel.hide_preview()
        from ..browser.thumbs import card_wheel_delta

        d = card_wheel_delta(ev)
        if d is not None:
            if d:
                self._panel.set_card_size(self._panel.card_size() + d)
            ev.accept()
            return
        super().wheelEvent(ev)

    def viewportEvent(self, ev) -> bool:  # noqa: N802 (Qt API)
        if ev.type() == QtCore.QEvent.ToolTip:
            idx = self.indexAt(ev.pos())
            e = idx.data(EntryRole) if idx.isValid() else None
            if e is not None and not self._panel.preview_on():
                # (Settings turned the large preview off: a plain tool tip with the name)
                QtWidgets.QToolTip.showText(
                    ev.globalPos(), e.reference, self.viewport(), self.visualRect(idx)
                )
            elif e is not None:
                r = self.visualRect(idx)
                self._panel.show_preview(e, QtCore.QRect(self.viewport().mapToGlobal(r.topLeft()), r.size()))
            else:
                QtWidgets.QToolTip.hideText()
                self._panel.hide_preview()
            return True
        return super().viewportEvent(ev)

    def startDrag(self, actions) -> None:  # noqa: N802 (Qt API)
        idx = self.currentIndex()
        e = idx.data(EntryRole) if idx.isValid() else None
        if e is None:
            return
        self._panel.hide_preview()
        drag = QtGui.QDrag(self)
        drag.setMimeData(self._panel.mime_for(e))
        pm = self._panel.drag_pixmap(e)
        drag.setPixmap(pm)
        drag.setHotSpot(QtCore.QPoint(pm.width() // 2, pm.height() // 2))
        if drag.exec(QtCore.Qt.CopyAction, QtCore.Qt.CopyAction) != QtCore.Qt.IgnoreAction:
            self._panel.note_placed(e.library, e.name, KIND_EXT.get(e.kind, "ctl"))


# --------------------------------------------------------------------------- hover preview
class _HoverPreview(QtWidgets.QFrame):
    """Large thumbnail + facts, shown next to the hovered card (Settings > Pages and
    libraries > Library panel > Preview of a card you point at: Off, Normal = :attr:`BOX`,
    Large; panel_options.LIBRARY_PREVIEW)."""

    BOX = QtCore.QSize(248, 180)

    @classmethod
    def box(cls) -> QtCore.QSize:
        """The picture's box at the size Settings chose."""
        w, h = _panels.LIBRARY_PREVIEW_BOX.get(_panels.LIBRARY_PREVIEW, (cls.BOX.width(), cls.BOX.height()))
        return QtCore.QSize(w, h)

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent, QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setObjectName("libPreview")
        # (the light well of the library cards: theme.FIXED 'well', as the page browser's)
        self.setStyleSheet(
            "QFrame#libPreview { background:%(panel)s; border:1px solid %(border)s; }"
            "QLabel { background:transparent; color:%(text)s; }"
            % COLORS
            + "QLabel#pvImage { background:%s; border:1px solid %s; border-radius:4px; }"
            % (fixed("well").name(), fixed("well_edge").name())
            + "QLabel#pvTitle { %s }" % type_qss("subtitle")
            + "QLabel#pvInfo { color:%s; %s }" % (COLORS["text_dim"], type_qss("caption"))
        )
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(2), _sp(2), _sp(2), _sp(2))
        lay.setSpacing(_sp(1))
        self.image = QtWidgets.QLabel(self)
        self.image.setObjectName("pvImage")
        self.image.setAlignment(QtCore.Qt.AlignCenter)
        self.title = QtWidgets.QLabel(self)
        self.title.setObjectName("pvTitle")
        self.info = QtWidgets.QLabel(self)
        self.info.setObjectName("pvInfo")
        self.info.setTextFormat(QtCore.Qt.RichText)
        self.info.setWordWrap(True)
        lay.addWidget(self.image)
        lay.addWidget(self.title)
        lay.addWidget(self.info)
        self.entry_key = ""
        self._sized = None
        self._fit_box()

    def _fit_box(self) -> QtCore.QSize:
        """The picture and the facts at the box Settings chose (again when it changed)."""
        b = self.box()
        if self._sized != b:
            self._sized = b
            self.image.setFixedSize(b.width() + 12, b.height() + 12)
            self.info.setFixedWidth(b.width() + 12)
        return b

    def show_for(self, e: Entry, pm: Optional[QtGui.QPixmap], info_html: str, anchor: QtCore.QRect) -> None:
        box = self._fit_box()
        self.entry_key = e.key
        if pm is not None and not pm.isNull():
            self.image.setPixmap(pm)
        else:
            self.image.setPixmap(QtGui.QPixmap())
            self.image.setText("Loading…")
        fm = QtGui.QFontMetrics(self.title.font())
        self.title.setText(fm.elidedText(e.reference, QtCore.Qt.ElideMiddle, box.width() + 12))
        self.info.setText(info_html)
        self.adjustSize()
        scr = QtGui.QGuiApplication.screenAt(anchor.center()) or QtGui.QGuiApplication.primaryScreen()
        area = scr.availableGeometry() if scr is not None else QtCore.QRect(0, 0, 4000, 4000)
        x = anchor.right() + 10
        if x + self.width() > area.right():
            x = anchor.left() - 10 - self.width()
        y = min(max(area.top(), anchor.top()), area.bottom() - self.height())
        self.move(max(area.left(), x), y)
        self.show()
        self.raise_()


# --------------------------------------------------------------------------- panel
_QSS = """
#LibraryPanel { background:%(bg)s; }
QToolButton#segL, QToolButton#segR { background:%(panel)s; color:%(text_dim)s;
    border:1px solid %(border)s; padding:3px 12px; font-weight:600; }
QToolButton#segL { border-top-left-radius:5px; border-bottom-left-radius:5px; }
QToolButton#segR { border-top-right-radius:5px; border-bottom-right-radius:5px;
    border-left:none; }
QToolButton#segL:hover, QToolButton#segR:hover { color:%(text)s; }
QToolButton#segL:checked, QToolButton#segR:checked { background:%(selection)s;
    color:%(sel_text)s; border-color:%(accent)s; }
QToolButton#segL[kbdFocus="true"], QToolButton#segR[kbdFocus="true"] {
    border:2px solid %(accent)s; padding:2px 11px; }
QToolButton#libTool { background:transparent; border:1px solid transparent;
    border-radius:4px; padding:3px; }
QToolButton#libTool:hover { background:#2c3036; border-color:%(border)s; }
QToolButton#libTool:checked { background:rgba(77,163,255,50); border-color:%(accent)s; }
QToolButton#libTool[kbdFocus="true"] { border:2px solid %(accent)s; padding:2px; }
QToolButton#libTool::menu-indicator { image:none; width:0px; }
QTreeWidget#libTree { background:%(bg)s; border:1px solid %(bg)s; outline:none; }
QTreeWidget#libTree[kbdFocus="true"], QListView#libGrid[kbdFocus="true"] {
    border:1px solid %(accent)s; }
QTreeWidget#libTree::item { padding:2px 2px; border:none; }
QTreeWidget#libTree::item:hover { background:#2c3036; }
QTreeWidget#libTree::item:selected { background:%(selection)s; color:%(sel_text)s; }
QListView#libGrid { background:%(bg)s; border:1px solid %(bg)s; outline:none; }
QFrame#libScopeBar { background:%(panel)s; border:1px solid %(border)s; border-radius:5px; }
QLabel#libScopeLabel { font-weight:600; background:transparent; }
QLabel#libScopeCount { color:%(text_dim)s; background:transparent; }
QLabel#libStatus, QLabel#libEmpty { color:%(text_dim)s; background:transparent; }
QPushButton#libLink { background:transparent; border:none; color:%(accent)s;
    text-decoration:underline; padding:2px; }
QPushButton#libLink[kbdFocus="true"] { border:1px solid %(accent)s; padding:1px; }
QSlider::groove:horizontal { height:4px; background:%(border)s; border-radius:2px; }
QSlider::handle:horizontal { width:12px; margin:-5px 0; border-radius:6px;
    background:%(text_dim)s; }
QSlider::handle:horizontal:hover { background:%(accent)s; }
QSlider[kbdFocus="true"]::handle:horizontal { border:2px solid %(accent)s; width:8px; }
QSplitter::handle { background:%(border)s; }
"""


class LibraryPanel(QtWidgets.QWidget):
    """Search-first browser of every symbol / genie on the page's include chain."""

    placeRequested = QtCore.Signal(str, str, str)  # (library_stem, element, ext)
    editRequested = QtCore.Signal(str, str)  # (library_path, element)
    deleteRequested = QtCore.Signal(str, str)  # (library_path, element)
    renameRequested = QtCore.Signal(str, str)  # (library_path, element)
    usedByRequested = QtCore.Signal(str, str)  # (library_path, element)
    formRequested = QtCore.Signal(str, str)  # (library_path, genie): its genie form
    linkedToggled = QtCore.Signal(bool)  # the Linked toggle (paste_linked)
    kindChanged = QtCore.Signal(str)

    WIDE = 560  # side-by-side tree above this panel width

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, thumbs: Optional[ThumbnailService] = None):
        super().__init__(parent)
        self.setObjectName("LibraryPanel")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self._thumbs = thumbs or ThumbnailService.shared()
        self._thumbs.thumbnailReady.connect(self._on_thumb)
        self._thumbs.summaryReady.connect(self._on_summary)
        self._keyed = _PixLru(24 * 1024 * 1024)  # keyed full-size thumbnails
        self._scaled = _PixLru(32 * 1024 * 1024)  # fitted card / row / preview pixmaps

        s = app_settings()
        kind = str(s.value(_S + "kind", "symbol") or "symbol")
        self._kind = kind if kind in KIND_EXT else "symbol"
        self._card = self._clamp_card(s.value(_S + "card_size", CARD_DEFAULT))
        vm = str(s.value(_S + "view_mode", "grid") or "grid")
        self._view_mode = vm if vm in ("grid", "list") else "grid"
        self._grouped = self._bool(s.value(_S + "grouped", True), True)
        self._tree_visible = self._bool(s.value(_S + "tree_visible", True), True)
        self._favs = self._load_refs(s.value(_S + "favourites", ""))
        self._recent = self._load_refs(s.value(_S + "recent", ""))
        self._recent_ver = 0  # bumped on every Recent change
        self._built_recent = -1  # the version the grid was built with
        self._tree_kind = ""

        self._project_dir = ""
        self._chain: list = []
        self._dirty = False
        self._loaded_stamp = None  # _chain_stamp() of the lists shown
        self._loaded_dir = ""  # the project the lists shown are of
        self._data: Dict[str, dict] = {}  # kind -> loaded data
        self._scope: Scope = _ALL
        self._query = ""
        self._tokens: List[str] = []
        self._regex = False  # the search box's '.*': the query is one expression
        self._scores: tuple = ()  # (kind, query, [rank|None])
        self._tree_items: Dict[Scope, QtWidgets.QTreeWidgetItem] = {}  # first row of a scope
        self._tree_all: List[QtWidgets.QTreeWidgetItem] = []  # every row
        self._expanded_before_query: Optional[set] = None
        self._wide: Optional[bool] = None
        self._split_sizes = {
            "h": self._load_sizes(s.value(_S + "split_h", "")),
            "v": self._load_sizes(s.value(_S + "split_v", "")),
        }

        self._build()
        self.setStyleSheet(_QSS % COLORS)
        self._relayout_timer = QtCore.QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(0)
        self._relayout_timer.timeout.connect(self._grid.relayout)
        self._load_timer = QtCore.QTimer(self)  # deferred build on first show
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(0)
        self._load_timer.timeout.connect(self._timed_load)
        # a project's library directories read on a thread before its lists are built:
        # ((project dir, kind), thread, start time); the wait polls with _warm_timer
        self._warm = None
        self._warm_timer = QtCore.QTimer(self)
        self._warm_timer.setSingleShot(True)
        self._warm_timer.setInterval(25)
        self._warm_timer.timeout.connect(self._timed_load)
        self._sync_kind_buttons()
        self._show_empty("Open a page to browse its symbol and genie libraries.")
        # the Tree / Flat choice is shared with the page browser
        from ..browser.dialog import view_notifier

        view_notifier().changed.connect(self._on_project_view)

    def _theme_changed(self) -> None:
        """apply_theme: the sheet again from the new scheme's colours (the text on the
        selection, 'sel_text', is no design colour a recolour could map); the cards laid
        out again at the text size (their fonts and line heights)."""
        self.setStyleSheet(_QSS % COLORS)
        grid = getattr(self, "_grid", None)
        if grid is not None:
            grid.relayout()

    # ------------------------------------------------------------------ settings helpers
    @staticmethod
    def _bool(v, default: bool) -> bool:
        if isinstance(v, bool):
            return v
        if v is None or v == "":
            return default
        return str(v).lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _clamp_card(v) -> int:
        try:
            n = int(v)
        except TypeError, ValueError:
            n = CARD_DEFAULT
        return max(CARD_MIN, min(CARD_MAX, n))

    @staticmethod
    def _load_refs(raw) -> List[Tuple[str, str, str]]:
        """JSON ``[[ext, library, element], ...]`` (QSettings INI stores a
        one-element list as a plain string, so lists go through JSON)."""
        try:
            data = json.loads(raw) if isinstance(raw, str) and raw else []
        except ValueError:
            data = []
        out = []
        seen = set()
        for it in data if isinstance(data, list) else []:
            if isinstance(it, (list, tuple)) and len(it) == 3 and all(isinstance(x, str) for x in it):
                k = tuple(x.lower() for x in it)
                if k not in seen and it[1] and it[2]:
                    seen.add(k)
                    out.append((it[0].lower(), it[1], it[2]))
        return out

    @staticmethod
    def _load_sizes(raw) -> Optional[List[int]]:
        try:
            v = json.loads(raw) if isinstance(raw, str) and raw else None
        except ValueError:
            return None
        if isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) and x >= 0 for x in v):
            return v
        return None

    def _save(self, key: str, value) -> None:
        s = app_settings()
        s.setValue(_S + key, value)
        s.sync()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))

        # the one search box of the UI guide (theme.SearchField: its pause, its clear
        # button and its '.*' toggle); the panel's own keys move the grid from it
        self._search = SearchField("", self, delay_ms=120, regex_key="Search the libraries")
        self._search.setObjectName("libSearch")
        self._search.setAccessibleName("Search the libraries")
        self._search.searchChanged.connect(lambda _t: self._apply_search_text())
        self._search_regex = self._search.regex_toggle
        lay.addWidget(self._search)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(0)
        self._seg = QtWidgets.QButtonGroup(self)
        self._seg.setExclusive(True)
        self._btn_kind: Dict[str, QtWidgets.QToolButton] = {}
        for kind, name, obj in (("symbol", "Symbols", "segL"), ("genie", "Genies", "segR")):
            b = QtWidgets.QToolButton(self)
            b.setObjectName(obj)
            b.setText(name)
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=kind: self.set_kind(k))
            self._seg.addButton(b)
            self._btn_kind[kind] = b
            row.addWidget(b)
        row.addSpacing(6)
        # CtDraw's Paste Symbol 'Linked' check box: a placed symbol follows its
        # library (linked) or is an unlinked copy of it -- an icon toggle, so
        # the panel stays narrow
        self._btn_linked = self._tool(
            "link", "Paste symbols linked to their library. Genies are always linked."
        )
        self._btn_linked.setText("Linked")
        self._btn_linked.setAccessibleName("Paste symbols linked")
        self._btn_linked.setCheckable(True)
        self._btn_linked.setChecked(paste_linked())
        self._btn_linked.toggled.connect(self._linked_toggled)
        row.addWidget(self._btn_linked)
        row.addStretch(1)
        self._btn_tree = self._tool("sidebar", "Show / hide the library tree")
        self._btn_tree.setCheckable(True)
        self._btn_tree.setChecked(self._tree_visible)
        self._btn_tree.toggled.connect(self.set_tree_visible)
        row.addWidget(self._btn_tree)
        row.addSpacing(2)
        self._btn_more = self._tool("more", "View options")
        self._btn_more.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self._btn_more.setMenu(self._build_menu())
        row.addWidget(self._btn_more)
        lay.addLayout(row)

        self._split = QtWidgets.QSplitter(QtCore.Qt.Vertical, self)
        self._split.setChildrenCollapsible(False)
        self._split.setHandleWidth(3)
        self._split.splitterMoved.connect(self._on_split_moved)

        self._tree = QtWidgets.QTreeWidget(self._split)
        self._tree.setObjectName("libTree")
        self._tree.setColumnCount(2)
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(14)
        self._tree.setUniformRowHeights(True)
        self._tree.setIconSize(QtCore.QSize(16, 16))
        self._tree.setMinimumHeight(90)
        self._tree.setMinimumWidth(150)
        self._tree.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self._tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        hdr = self._tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        # names with a dim note ('always included', '(included above)')
        self._tree.setItemDelegateForColumn(0, _RowDelegate(self._tree))
        self._tree.setAccessibleName("Libraries")
        self._tree.currentItemChanged.connect(self._on_tree_current)

        right = QtWidgets.QWidget(self._split)
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(_sp(1))
        bar = QtWidgets.QFrame(right)
        bar.setObjectName("libScopeBar")
        bl = QtWidgets.QHBoxLayout(bar)
        bl.setContentsMargins(_sp(2), _sp(0), _sp(0), _sp(0))
        bl.setSpacing(_sp(1))
        self._scope_icon = QtWidgets.QLabel(bar)
        self._scope_icon.setFixedSize(16, 16)
        self._scope_label = QtWidgets.QLabel(bar)
        self._scope_label.setObjectName("libScopeLabel")
        self._scope_label.setMinimumWidth(40)
        self._scope_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self._scope_count = QtWidgets.QLabel(bar)
        self._scope_count.setObjectName("libScopeCount")
        self._btn_scope_clear = self._tool("close", "Show all libraries")
        self._btn_scope_clear.setIconSize(QtCore.QSize(12, 12))
        self._btn_scope_clear.clicked.connect(lambda: self.set_scope(_ALL))
        bl.addWidget(self._scope_icon)
        bl.addWidget(self._scope_label, 1)
        bl.addWidget(self._scope_count)
        bl.addWidget(self._btn_scope_clear)
        rl.addWidget(bar)
        self._scope_bar = bar

        self._stack = QtWidgets.QStackedWidget(right)
        self._model = _GridModel(self)
        self._grid = _GridView(self)
        self._delegate = _CardDelegate(self._grid, self)
        self._grid.setItemDelegate(self._delegate)
        self._grid.setModel(self._model)
        self._grid.doubleClicked.connect(self._on_double_clicked)
        self._grid.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._grid.customContextMenuRequested.connect(self._context_menu)
        self._grid.selectionModel().currentChanged.connect(lambda *_a: self._update_status())
        self._grid.apply_mode(self._view_mode)
        self._stack.addWidget(self._grid)
        empty = QtWidgets.QWidget(right)
        el = QtWidgets.QVBoxLayout(empty)
        el.addStretch(1)
        self._empty_label = QtWidgets.QLabel(empty)
        self._empty_label.setObjectName("libEmpty")
        self._empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self._empty_label.setWordWrap(True)
        el.addWidget(self._empty_label)
        self._empty_link = QtWidgets.QPushButton(empty)
        self._empty_link.setObjectName("libLink")
        self._empty_link.setCursor(QtCore.Qt.PointingHandCursor)
        self._empty_link.clicked.connect(lambda: self.set_scope(_ALL))
        el.addWidget(self._empty_link, 0, QtCore.Qt.AlignHCenter)
        el.addStretch(2)
        self._stack.addWidget(empty)
        rl.addWidget(self._stack, 1)
        self._split.addWidget(self._tree)
        self._split.addWidget(right)
        self._split.setStretchFactor(0, 0)
        self._split.setStretchFactor(1, 1)
        lay.addWidget(self._split, 1)
        self._tree.setVisible(self._tree_visible)

        foot = QtWidgets.QHBoxLayout()
        foot.setSpacing(_sp(0))
        self._status = QtWidgets.QLabel(self)
        self._status.setObjectName("libStatus")
        self._status.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        foot.addWidget(self._status, 1)
        self._btn_grid = self._tool("grid_view", "Thumbnail grid")
        self._btn_list = self._tool("list_view", "List")
        for b, mode in ((self._btn_grid, "grid"), (self._btn_list, "list")):
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, m=mode: self.set_view_mode(m))
            foot.addWidget(b)
        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, self)
        self._slider.setRange(CARD_MIN, CARD_MAX)
        self._slider.setSingleStep(8)
        self._slider.setPageStep(24)
        self._slider.setFixedWidth(84)
        self._slider.setToolTip("Card size (Ctrl+wheel over the grid)")
        self._slider.setValue(self._card)
        self._slider.valueChanged.connect(self.set_card_size)
        foot.addSpacing(4)
        foot.addWidget(self._slider)
        lay.addLayout(foot)
        self._sync_view_buttons()

        self._preview = _HoverPreview(self)
        self._preview.hide()
        for w in (
            self,
            self._search,
            self._grid,
            self._grid.viewport(),
            self._tree,
            self._slider,
            *self._btn_kind.values(),
        ):
            w.installEventFilter(self)

    def _tool(self, glyph_name: str, tip: str) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton(self)
        b.setObjectName("libTool")
        b.setIcon(glyph(glyph_name, 16))
        b.setIconSize(QtCore.QSize(16, 16))
        b.setToolTip(tip)
        b.setAutoRaise(True)
        return b

    def _build_menu(self) -> QtWidgets.QMenu:
        m = QtWidgets.QMenu(self)
        grp = QtGui.QActionGroup(m)
        # the keys work while the panel has the focus (eventFilter): shown as
        # text only, so they never compete with the window's own shortcuts
        self._act_grid = m.addAction(
            glyph("grid_view"), "Thumbnail Grid\tCtrl+1", lambda: self.set_view_mode("grid")
        )
        self._act_list = m.addAction(glyph("list_view"), "List\tCtrl+2", lambda: self.set_view_mode("list"))
        for a in (self._act_grid, self._act_list):
            a.setCheckable(True)
            grp.addAction(a)
        m.addSeparator()
        sizes = m.addMenu("Card Size")
        sgrp = QtGui.QActionGroup(sizes)
        self._size_acts = {}
        for label, px in (("Small", 64), ("Medium", CARD_DEFAULT), ("Large", 148)):
            a = sizes.addAction(label, lambda _p=px: self.set_card_size(_p))
            a.setCheckable(True)
            sgrp.addAction(a)
            self._size_acts[px] = a
        projects = m.addMenu("Projects")
        pgrp = QtGui.QActionGroup(projects)
        self._act_proj_tree = projects.addAction("Tree", lambda: self.set_project_view(TREE))
        self._act_proj_tree.setToolTip("Projects under the projects that include them")
        self._act_proj_flat = projects.addAction("Flat", lambda: self.set_project_view(FLAT))
        self._act_proj_flat.setToolTip("Every project once, in search order")
        for a in (self._act_proj_tree, self._act_proj_flat):
            a.setCheckable(True)
            pgrp.addAction(a)
        m.addSeparator()
        self._act_group = m.addAction("Group by Library")
        self._act_group.setCheckable(True)
        self._act_group.setChecked(self._grouped)
        self._act_group.toggled.connect(self.set_grouped)
        self._act_tree = m.addAction("Show Library Tree")
        self._act_tree.setCheckable(True)
        self._act_tree.setChecked(self._tree_visible)
        self._act_tree.toggled.connect(self.set_tree_visible)
        m.addSeparator()
        m.addAction("Clear Recent", self.clear_recent)
        self._act_rescan = m.addAction("Rescan Libraries\tF5", self.refresh)
        m.aboutToShow.connect(self._sync_project_view_actions)
        return m

    # ------------------------------------------------------------------ projects view
    def project_view(self) -> str:
        from ..browser.dialog import project_view

        return project_view()

    def set_project_view(self, view: str) -> None:
        """Tree or Flat projects (the shared page browser setting)."""
        from ..browser.dialog import set_project_view

        if view not in (TREE, FLAT):
            return
        set_project_view(view)  # _on_project_view follows
        self._on_project_view(view)

    def _on_project_view(self, view: str) -> None:
        """The shared Tree / Flat choice changed (here or in the page browser)."""
        try:
            self._sync_project_view_actions()
            if self._chain and getattr(self, "_tree_view", None) != view:
                self._build_tree()
        except RuntimeError:  # the panel is being destroyed
            pass

    def _sync_project_view_actions(self) -> None:
        v = self.project_view()
        self._act_proj_tree.setChecked(v == TREE)
        self._act_proj_flat.setChecked(v == FLAT)

    # ------------------------------------------------------------------ linked / flashing
    def _linked_toggled(self, on: bool) -> None:
        set_paste_linked(bool(on))
        self.linkedToggled.emit(bool(on))

    def linked(self) -> bool:
        return self._btn_linked.isChecked()

    def set_linked(self, on: bool) -> None:
        self._btn_linked.setChecked(bool(on))

    def sync_linked(self) -> None:
        """Follow the setting (changed from Edit > Library)."""
        on = paste_linked()
        if self._btn_linked.isChecked() != on:
            self._btn_linked.blockSignals(True)
            self._btn_linked.setChecked(on)
            self._btn_linked.blockSignals(False)

    def card_subtitle(self, e: Entry) -> str:
        """A card's second line: the library, with the project in front where
        the cards come from several projects without a library header over
        them (Favourites, Recent, all libraries not grouped)."""
        sc = self._scope
        mixed = sc in (_FAV, _RECENT) or (sc == _ALL and not self._grouped)
        return "%s · %s" % (e.project, e.library) if mixed else e.library

    def is_flashing(self, e: Entry) -> bool:
        """The element's bitmap flashes (known once its summary is read)."""
        s = self._thumbs.summary(e)
        return bool(getattr(s, "flashing", False)) if s is not None else False

    def request_used_by(self, e: Optional[Entry] = None) -> bool:
        e = e or self.current_entry()
        if e is None:
            return False
        self.usedByRequested.emit(e.path, e.name)
        return True

    # ------------------------------------------------------------------ public API
    def set_project_dir(self, path: str) -> None:
        """List every library on the page's include chain."""
        path = path or ""
        if (
            path == self._project_dir
            and not self._dirty
            and self._loaded_stamp is not None
            and self._loaded_stamp == self._chain_stamp()
        ):
            return
        self._project_dir = path
        self._dirty = True
        if self.isVisible():
            self._load_timer.start()

    def project_dir(self) -> str:
        return self._project_dir

    #: the longest a deferred build waits for the thread reading its library directories (s)
    WARM_WAIT_S = 3.0

    def _timed_load(self) -> None:
        """The deferred build of the library lists."""
        if (
            self._dirty
            and self._project_dir
            and self._project_dir not in self.__dict__.get("_project_lists", {})
            and QtCore.QThread.currentThread().loopLevel() > 0
        ):
            key = (self._project_dir, self._kind)
            warm = self._warm
            if warm is None or warm[0] != key:
                warm = self._warm = (key, self._start_warm(*key), time.monotonic())
            if (
                warm[1] is not None
                and warm[1][0].is_alive()
                and time.monotonic() - warm[2] < self.WARM_WAIT_S
            ):
                self._warm_timer.start()
                return
        self.ensure_loaded()

    @classmethod
    def _start_warm(cls, project_dir: str, kind: str):
        """A thread that reads the ``kind`` library directories of ``project_dir``'s include
        chain into the catalog and makes the panel's lists of them: (thread, {'lists':
        (chain, lists, their stamp)}) (None: none started)."""
        out: dict = {}

        def run() -> None:
            try:
                cat = catalog_for()
                proj = cat.project_for_dir(project_dir) or cat.folder(project_dir)
                chain = cat.closure(proj)
                for pr in chain:
                    cat.libraries(pr, kind)
                if chain:
                    d = cls._compute_kind_data(chain, kind)
                    out["lists"] = (chain, d, cls._stamp_of(project_dir, chain, {kind: d}))
            except Exception:  # noqa: BLE001  # swallow-ok: the build reads them itself then
                pass

        try:
            from ...infra import gcguard

            gcguard.before_worker()
            t = threading.Thread(target=run, name="library-panel-warm", daemon=True)
            t.start()
            return t, out
        except Exception:  # noqa: BLE001  # swallow-ok: the build reads them itself then
            return None

    def _warmed_lists(self):
        """The lists the warm-up thread made for the project and kind shown, when it is done,
        made from the chain the panel lists and its libraries are as they were then:
        (lists, their stamp), else None."""
        warm = self._warm
        if (
            warm is None
            or warm[0] != (self._project_dir, self._kind)
            or warm[1] is None
            or warm[1][0].is_alive()
        ):
            return None
        got = warm[1][1].pop("lists", None)
        if got is None:
            return None
        chain, d, stamp = got
        if len(chain) != len(self._chain) or any(a is not b for a, b in zip(chain, self._chain)):
            return None
        if self._stamp_of(self._project_dir, self._chain, {self._kind: d}) != stamp:
            return None
        return d, stamp

    def ensure_loaded(self) -> None:
        """Build the element lists for the current kind now (no-op when current)."""
        self._load_timer.stop()
        self._warm_timer.stop()
        if self._dirty:
            self._dirty = False
            # the lists of the last projects shown are kept: a tab switch between two
            # projects alternates between them (the catalog reads are cached, the scoring
            # and the per-kind lists are not)
            kept = self.__dict__.setdefault("_project_lists", collections.OrderedDict())
            if self._loaded_dir and self._loaded_stamp is not None:
                kept[self._loaded_dir] = (self._loaded_stamp, self._chain, self._data, self._scores)
                kept.move_to_end(self._loaded_dir)
                while len(kept) > 3:
                    kept.popitem(last=False)
            hit = kept.get(self._project_dir)
            if hit is not None:
                # (the stamp of the KEPT lists, not of self._data: that is still the
                # previous project's)
                if self._chain_stamp(hit[1], hit[2]) == hit[0]:
                    self._chain = hit[1]
                    self._data, self._scores = hit[2], hit[3]
                else:
                    hit = None
            stamp = hit[0] if hit is not None else None
            if hit is None:
                self._load_chain()
                self._data = {}
                self._scores = ()
                # the lists a warm-up thread made, when they are of this chain as it is now
                warmed = self._warmed_lists()
                if warmed is not None:
                    self._data = {self._kind: warmed[0]}
                    stamp = warmed[1]
            kinds = set(self._data)
            self._reset_scope_if_gone()
            self._build_tree()
            self._rebuild(keep=self.current_entry())
            # the kept (or warmed) lists' stamp, just checked, when no kind was listed since
            # (a stamp is a stat of every listed library)
            self._loaded_stamp = (
                stamp if stamp is not None and set(self._data) == kinds else self._chain_stamp()
            )
            self._loaded_dir = self._project_dir
        elif self._kind not in self._data and self._chain:
            self._build_tree()
            self._rebuild()

    def _chain_stamp(self, chain=None, data=None) -> tuple:
        """What the library lists were built from."""
        return self._stamp_of(
            self._project_dir, self._chain if chain is None else chain, self._data if data is None else data
        )

    @staticmethod
    def _stamp_of(project_dir: str, chain, data) -> tuple:
        """_chain_stamp of ``project_dir``'s ``chain`` and lists ``data`` (no Qt: the warm-up
        thread stamps the lists it makes)."""
        try:
            from ... import editing

            gen = editing._LIBRARY_GENERATION[0]
        except Exception:  # noqa: BLE001
            gen = None
        # (an include added in Studio: each install's MASTER.DBF and the include.dbf of each
        # project of the chain; the chain is read again in _load_chain, io/catalog. Stamped
        # from the chain itself, not from what the catalog has read so far: a catalog
        # reloaded by a thread meanwhile must not move the stamp of unchanged lists)
        topo = []
        try:
            for inst in catalog_for().installs:
                topo.append(_file_stamp(os.path.join(inst.user_dir, "MASTER.DBF")))
        except Exception:  # noqa: BLE001 -- swallow-ok: no catalog: the stamp does without it
            pass
        dirs = []
        for pr in chain:
            d = getattr(pr, "dir", "") or ""
            try:
                dirs.append((d, os.stat(d).st_mtime_ns))
            except OSError:
                dirs.append((d, None))
            topo.append(_file_stamp(os.path.join(d, "include.dbf")))
        topo = tuple(topo)
        libs = []
        for kind in sorted(data):
            for _pr, li in data[kind].get("libs", ()):
                try:
                    st = os.stat(li.path)
                    libs.append((li.path, st.st_mtime_ns, st.st_size))
                except OSError:
                    libs.append((li.path, None, None))
        return (project_dir, gen, topo, tuple(dirs), tuple(libs))

    def refresh(self) -> None:
        """Re-read the catalog (topology + library directories)."""
        try:
            catalog_for().reload()
        except Exception:  # noqa: BLE001
            pass
        self._dirty = True
        self.ensure_loaded()

    def kind(self) -> str:
        return self._kind

    def set_kind(self, kind: str) -> None:
        """Symbols (``'symbol'``) or genies (``'genie'``); remembered."""
        if kind not in KIND_EXT:
            return
        self._sync_kind_buttons(kind)
        if kind == self._kind and kind in self._data:
            return
        self._kind = kind
        self._save("kind", kind)
        self._scores = ()
        if self._chain:
            self._kind_data()  # load the kind
            self._reset_scope_if_gone()
            self._build_tree()
            self._rebuild(select_first=bool(self._tokens))
        self._update_placeholder()
        self.kindChanged.emit(kind)

    def query(self) -> str:
        return self._query

    def set_query(self, text: str) -> None:
        """Filter immediately (the search box debounces by 120 ms)."""
        if self._search.text() != text:
            self._search.blockSignals(True)
            self._search.setText(text)
            self._search.blockSignals(False)
        self._search.cancel()
        self._apply_query(text, regex=self._search_regex.on)

    def scope(self) -> Scope:
        return self._scope

    def set_scope(self, scope: Scope) -> None:
        """``('all',)`` / ``('favourites',)`` / ``('recent',)`` /
        ``('project', name)`` / ``('library', path)`` (any path spelling)."""
        scope = self._norm_scope(scope)
        self.hide_preview()
        self._scope = scope
        if self._tokens:
            self._update_tree_counts()  # the scoped library stays listed
        self._select_tree_scope(scope)
        self._rebuild(keep=None, select_first=bool(self._tokens))
        self._grid.scrollToTop()

    def library_scope(self, library: str, project: str = "") -> Optional[Scope]:
        """The scope of library stem ``library`` (first on the chain, or in
        ``project``), or None."""
        d = self._kind_data()
        for pr, li in d.get("libs", []):
            if li.stem.lower() == library.lower() and (not project or pr.name.lower() == project.lower()):
                return ("library", os.path.normcase(li.path))
        return None

    def libraries(self) -> list:
        """``[(ProjectInfo, LibraryInfo)]`` of the current kind, chain order."""
        return list(self._kind_data().get("libs", []))

    def all_entries(self) -> List[Entry]:
        return list(self._kind_data().get("entries", []))

    def visible_entries(self) -> List[Entry]:
        """Entries shown in the grid, in display order (sections included,
        so an entry can appear twice in the home view)."""
        return self._model.entries()

    def section_titles(self) -> List[str]:
        return [h.title for h in self._model.headers()]

    def current_entry(self) -> Optional[Entry]:
        idx = self._grid.currentIndex()
        return self._model.entry(idx.row()) if idx.isValid() else None

    def select_entry(self, e: Entry) -> bool:
        rows = self._model.rows_for_key(e.key)
        if not rows:
            return False
        # prefer the card in its library group over a Favourites/Recent copy
        outside = [r for r in rows if not self._in_sections(r)]
        idx = self._model.index(outside[0] if outside else rows[0], 0)
        self._grid.setCurrentIndex(idx)
        self._grid.scrollTo(idx)
        return True

    def find_entry(self, library: str, element: str, kind: Optional[str] = None) -> Optional[Entry]:
        d = self._kind_data(kind)
        i = d.get("ref", {}).get((library.lower(), element.lower()))
        return d["entries"][i] if i is not None else None

    def place(self, e: Optional[Entry] = None) -> bool:
        """Emit ``placeRequested`` for ``e`` (default: the current card)."""
        e = e or self.current_entry()
        if e is None:
            return False
        ext = KIND_EXT.get(e.kind, "ctl")
        self.note_placed(e.library, e.name, ext)
        self.placeRequested.emit(e.library, e.name, ext)
        return True

    def activate_current(self) -> bool:
        return self.place()

    def edit(self, e: Optional[Entry] = None) -> bool:
        e = e or self.current_entry()
        if e is None:
            return False
        self.editRequested.emit(e.path, e.name)
        return True

    def request_delete(self, e: Optional[Entry] = None) -> bool:
        """Ask the host to delete ``e`` from its library (it confirms)."""
        e = e or self.current_entry()
        if e is None:
            return False
        self.hide_preview()
        self.deleteRequested.emit(e.path, e.name)
        return True

    def request_rename(self, e: Optional[Entry] = None) -> bool:
        """Ask the host to rename ``e`` in its library (it asks the name)."""
        e = e or self.current_entry()
        if e is None:
            return False
        self.hide_preview()
        self.renameRequested.emit(e.path, e.name)
        return True

    def mime_for(self, e: Entry) -> QtCore.QMimeData:
        return element_mime(e.library, e.name, KIND_EXT.get(e.kind, "ctl"))

    # -- favourites / recent -----------------------------------------------------------
    @staticmethod
    def _ref_of(e: Entry) -> Tuple[str, str, str]:
        return (KIND_EXT.get(e.kind, "ctl"), e.library, e.name)

    def is_favourite(self, e: Entry) -> bool:
        k = (KIND_EXT.get(e.kind, "ctl"), e.library.lower(), e.name.lower())
        return k in self._fav_keys()

    def _fav_keys(self) -> set:
        cache = getattr(self, "_fav_cache", None)
        if cache is None or cache[0] is not self._favs or cache[1] != len(self._favs):
            cache = (
                self._favs,
                len(self._favs),
                {(x.lower(), l.lower(), n.lower()) for x, l, n in self._favs},
            )
            self._fav_cache = cache
        return cache[2]

    def favourites(self) -> List[Tuple[str, str, str]]:
        """``[(ext, library, element)]`` in the order they were starred."""
        return list(self._favs)

    def recent(self) -> List[Tuple[str, str, str]]:
        """``[(ext, library, element)]``, most recently placed first."""
        return list(self._recent)

    def set_favourite(self, e: Entry, on: bool) -> None:
        ref = self._ref_of(e)
        low = tuple(x.lower() for x in ref)
        cur = [r for r in self._favs if tuple(x.lower() for x in r) != low]
        if on:
            cur.append(ref)
        self._favs = cur
        self._fav_cache = None
        self._save("favourites", json.dumps([list(r) for r in self._favs]))
        top = self.scope() in (_ALL, _FAV) and not self._tokens
        if top:
            self._rebuild(keep=e, keep_scroll=True)
        else:
            for r in self._model.rows_for_key(e.key):
                self._model.dataChanged.emit(self._model.index(r, 0), self._model.index(r, 0))
        self._update_fixed_counts()

    def toggle_favourite(self, e: Optional[Entry] = None) -> None:
        e = e or self.current_entry()
        if e is not None:
            self.set_favourite(e, not self.is_favourite(e))

    def note_placed(self, library: str, element: str, ext: str) -> None:
        """Record a placement in Recent (the panel calls this itself on
        activation / a completed drag; the window may call it too)."""
        ext = (ext or "ctl").lower().lstrip(".")
        low = (ext, library.lower(), element.lower())
        self._recent = [(ext, library, element)] + [
            r for r in self._recent if (r[0], r[1].lower(), r[2].lower()) != low
        ]
        from ..prefs import timing_options

        del self._recent[timing_options.LIBRARY_RECENT_KEPT :]
        self._recent_ver += 1
        self._save("recent", json.dumps([list(r) for r in self._recent]))
        self._update_fixed_counts()
        # the grid's Recent section refreshes on the next rebuild (no jump
        # under the cursor right after a double-click)

    def trim_recent(self) -> None:
        """Settings > Pages and libraries > Recent elements kept lowered: the oldest beyond
        it go now (the Settings window's hook), not after the next placement."""
        from ..prefs import timing_options

        keep = timing_options.LIBRARY_RECENT_KEPT
        if len(self._recent) <= keep:
            return
        del self._recent[keep:]
        self._recent_ver += 1
        self._save("recent", json.dumps([list(r) for r in self._recent]))
        self._update_fixed_counts()
        self._rebuild(keep=self.current_entry(), keep_scroll=True)

    def remove_recent(self, e: Entry) -> None:
        low = (KIND_EXT.get(e.kind, "ctl"), e.library.lower(), e.name.lower())
        self._recent = [r for r in self._recent if (r[0], r[1].lower(), r[2].lower()) != low]
        self._recent_ver += 1
        self._save("recent", json.dumps([list(r) for r in self._recent]))
        self._update_fixed_counts()
        self._rebuild(keep=None, keep_scroll=True)

    def clear_recent(self) -> None:
        self._recent = []
        self._recent_ver += 1
        self._save("recent", "[]")
        self._update_fixed_counts()
        self._rebuild(keep=self.current_entry(), keep_scroll=True)

    # -- view options ------------------------------------------------------------------
    def card_size(self) -> int:
        return self._card

    def set_card_size(self, px: int) -> None:
        px = self._clamp_card(px)
        if px == self._card:
            return
        self._card = px
        if self._slider.value() != px:
            self._slider.blockSignals(True)
            self._slider.setValue(px)
            self._slider.blockSignals(False)
        self._save("card_size", px)
        self._sync_view_buttons()
        self._relayout_timer.start()

    def view_mode(self) -> str:
        return self._view_mode

    def set_view_mode(self, mode: str) -> None:
        if mode not in ("grid", "list"):
            return
        self._view_mode = mode
        self._save("view_mode", mode)
        self._grid.apply_mode(mode)
        self._sync_view_buttons()
        cur = self._grid.currentIndex()
        if cur.isValid():
            self._grid.scrollTo(cur)

    def grouped(self) -> bool:
        return self._grouped

    def set_grouped(self, on: bool) -> None:
        on = bool(on)
        if self._act_group.isChecked() != on:
            self._act_group.setChecked(on)
            return  # toggled -> re-enters
        if on == self._grouped:
            return
        self._grouped = on
        self._save("grouped", on)
        self._rebuild(keep=self.current_entry())

    def set_tree_visible(self, on: bool) -> None:
        on = bool(on)
        self._tree_visible = on
        for w in (self._btn_tree, self._act_tree):
            if w.isChecked() != on:
                w.blockSignals(True)
                w.setChecked(on)
                w.blockSignals(False)
        self._tree.setVisible(on)
        self._save("tree_visible", on)

    def focus_search(self) -> None:
        self._search.setFocus(QtCore.Qt.ShortcutFocusReason)
        self._search.selectAll()

    # widgets (tests / integration)
    def search_field(self) -> QtWidgets.QLineEdit:
        return self._search

    def grid_view(self) -> QtWidgets.QListView:
        return self._grid

    def tree_widget(self) -> QtWidgets.QTreeWidget:
        return self._tree

    def status_text(self) -> str:
        """The full (unelided) status line."""
        return self._status.toolTip()

    def scope_text(self) -> str:
        return self._scope_label.text()

    def scope_count_text(self) -> str:
        """The scope bar's count ('193 matches' / '2868 symbols')."""
        return self._scope_count.text()

    # ------------------------------------------------------------------ loading
    def _load_chain(self) -> None:
        self._chain = []
        if not self._project_dir:
            return
        try:
            cat = catalog_for()
            cat.refresh_topology()  # (MASTER.DBF or an include.dbf changed: read again)
            proj = cat.project_for_dir(self._project_dir) or cat.folder(self._project_dir)
            self._chain = cat.closure(proj)
        except Exception:  # noqa: BLE001 -- the catalog is best-effort
            self._chain = []

    def _kind_data(self, kind: Optional[str] = None) -> dict:
        kind = kind or self._kind
        d = self._data.get(kind)
        if d is not None or not self._chain:
            return d or {}
        d = self._compute_kind_data(self._chain, kind)
        self._data[kind] = d
        if not self._dirty:
            # what the lists now show (set_project_dir's no-op test)
            self._loaded_stamp = self._chain_stamp()
        return d

    @staticmethod
    def _compute_kind_data(chain: list, kind: str) -> dict:
        """The ``kind`` lists of the include ``chain``: its libraries, their entries and what
        the search and the sort read of them (no Qt: the warm-up thread makes them too)."""
        libs, entries = [], []
        try:
            cat = catalog_for()
            for pr in chain:
                for li in cat.libraries(pr, kind):
                    libs.append((pr, li))
            entries = cat.entries(chain, kind)
        except Exception:  # noqa: BLE001
            libs, entries = [], []
        if kind == "genie":
            # super genies ('<name>@SUPER') are pages attached to a genie, not
            # something to place: CtDraw's Paste Genie list leaves them out
            # (they are placed with Edit > Paste Super Genie)
            supers = {os.path.normcase(e.path) for e in entries if is_super_genie(e.name)}
            entries = [e for e in entries if not is_super_genie(e.name)]
            keep = {os.path.normcase(e.path) for e in entries}
            # a library holding only super genies has nothing to place here
            # (an EMPTY library stays listed)
            libs = [
                (pr, li)
                for pr, li in libs
                if os.path.normcase(li.path) in keep or os.path.normcase(li.path) not in supers
            ]
        libpos = {os.path.normcase(li.path): i for i, (_pr, li) in enumerate(libs)}
        preps = [_prep(e) for e in entries]
        libkey = [os.path.normcase(e.path) for e in entries]
        ref: Dict[Tuple[str, str], int] = {}
        for i, e in enumerate(entries):
            ref.setdefault((e.library.lower(), e.name.lower()), i)
        d = {
            "libs": libs,
            "entries": entries,
            "preps": preps,
            "libkey": libkey,
            "libpos": [libpos.get(k, 1 << 20) for k in libkey],
            "nat": [natural_key(e.name) for e in entries],
            "ref": ref,
            "lib_count": {k: 0 for k in libpos},
        }
        for k in libkey:
            if k in d["lib_count"]:
                d["lib_count"][k] += 1
        return d

    def _norm_scope(self, scope) -> Scope:
        scope = tuple(scope or _ALL)
        if scope and scope[0] == "library" and len(scope) > 1:
            return (
                "library",
                os.path.normcase(os.path.abspath(scope[1]))
                if (os.sep in scope[1] or "/" in scope[1])
                else scope[1],
            )
        if scope and scope[0] in ("all", "favourites", "recent", "project"):
            return scope
        return _ALL

    def _reset_scope_if_gone(self) -> None:
        sc = self._scope
        if sc[0] == "library":
            if sc[1] not in self._kind_data().get("lib_count", {}):
                self._scope = _ALL
        elif sc[0] == "project":
            if not any(pr.name == sc[1] for pr, _li in self._kind_data().get("libs", [])):
                self._scope = _ALL

    # ------------------------------------------------------------------ tree
    def _tree_item(
        self,
        text: str,
        count: int,
        scope: Optional[Scope],
        gl: str,
        parent=None,
        bold: bool = False,
        tip: str = "",
        note: str = "",
        style: str = "",
    ) -> QtWidgets.QTreeWidgetItem:
        it = QtWidgets.QTreeWidgetItem([text + ("  " + note if note else ""), str(count)])
        it.setData(0, LabelRole, text)
        it.setData(0, NoteRole, note)
        it.setData(0, StyleRole, style)
        if scope is not None:
            it.setData(0, ScopeRole, scope)
        else:
            it.setFlags(QtCore.Qt.ItemIsEnabled)
        it.setIcon(
            0,
            glyph(
                gl, 16, COLORS["text_dim"] if gl in ("library", "clock", "all") or style == "dim" else None
            ),
        )
        it.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        it.setForeground(1, QtGui.QBrush(QtGui.QColor(COLORS["text_dim"])))
        if bold:
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
        if tip:
            it.setToolTip(0, tip)
        if parent is None:
            self._tree.addTopLevelItem(it)
        else:
            parent.addChild(it)
        if scope is not None:
            self._tree_items.setdefault(scope, it)
        self._tree_all.append(it)
        return it

    @staticmethod
    def _item_key(it: QtWidgets.QTreeWidgetItem) -> tuple:
        """A row's place in the tree: the scopes (else labels) from the top."""
        out = []
        while it is not None:
            sc = it.data(0, ScopeRole)
            out.append(tuple(sc) if sc else ("row", it.data(0, LabelRole) or it.text(0)))
            it = it.parent()
        return tuple(reversed(out))

    def _project_tip(self, pr, n_libs: int, state: str = "ok") -> str:
        one = _KIND_NOUN[self._kind][0]
        if state == INCLUDE_MISSING:
            return "%s\n%s" % (
                pr.name,
                "Registered, folder missing" if pr.registered else "In include.dbf, project not found",
            )
        lines = [pr.name, pr.dir]
        if pr.is_implicit:
            lines.append("Always included")
        elif pr.included_by:
            lines.append("Included by " + ", ".join(pr.included_by))
        lines.append("%d %s librar%s" % (n_libs, one, "y" if n_libs == 1 else "ies"))
        return "\n".join(lines)

    def _add_project_row(
        self, parent, pr, libs: list, bold: bool = True, note: str = "", style: str = "", leaf: bool = False
    ) -> QtWidgets.QTreeWidgetItem:
        noun = _KIND_NOUN[self._kind][1]
        total = sum(len(li.elements) for _p, li in libs)
        pit = self._tree_item(
            pr.name,
            total,
            ("project", pr.name),
            "project",
            parent=parent,
            bold=bold,
            tip=self._project_tip(pr, len(libs)),
            note=note,
            style=style,
        )
        pit.setData(0, _LibKeysRole, [os.path.normcase(li.path) for _p, li in libs])
        if not leaf:
            for _p, li in libs:
                self._tree_item(
                    li.stem,
                    len(li.elements),
                    ("library", os.path.normcase(li.path)),
                    "library",
                    parent=pit,
                    tip="%s\n%d %s" % (li.path, len(li.elements), noun),
                )
        return pit

    def _build_project_tree(self, by_proj) -> None:
        """Tree view: the page's project at the top, each project under the
        project that includes it (include.dbf order), Include / System last;
        a project whose rows would hold no library of the kind is left out."""
        cat = catalog_for()
        anchor = self._chain[0]
        by_key = {pr.key: libs for _n, libs in by_proj.items() for pr in [libs[0][0]]}
        has_memo: Dict[str, bool] = {}

        def has_libs(pr) -> bool:
            k = pr.key
            hit = has_memo.get(k)
            if hit is not None:
                return hit
            has_memo[k] = False  # a cycle counts as nothing
            todo, seen, found = [pr], set(), False
            while todo and not found:
                p = todo.pop()
                if p.key in seen:
                    continue
                seen.add(p.key)
                if p.key in by_key:
                    found = True
                    break
                for n in p.includes:
                    t = cat.project(n, p.install) if p.install is not None else None
                    if t is not None and t.exists:
                        todo.append(t)
            has_memo[k] = found
            return found

        def add(parent, pr, state, path):
            libs = by_key.get(pr.key, [])
            if state == INCLUDE_MISSING:
                self._tree_item(
                    pr.name,
                    0,
                    None,
                    "project",
                    parent=parent,
                    tip=self._project_tip(pr, 0, INCLUDE_MISSING),
                    note="(not found)",
                    style="dim",
                )
                return
            if state == INCLUDE_CYCLE:
                if libs:
                    it = self._add_project_row(
                        parent, pr, libs, bold=False, note="(included above)", style="dim", leaf=True
                    )
                    it.setData(0, _CycleRole, True)
                    it.setText(1, "")
                return
            note = "always included" if state == INCLUDE_IMPLICIT else ""
            it = self._add_project_row(parent, pr, libs, note=note)
            for child, st in cat.include_children(pr, path, top=not path):
                if st in ("ok", INCLUDE_IMPLICIT) and not has_libs(child):
                    continue
                add(it, child, st, path + (pr,))

        add(None, anchor, "ok", ())

    def _build_tree(self) -> None:
        d = self._kind_data()
        noun = _KIND_NOUN[self._kind][1]
        prev = None
        # (a Tree / Flat switch starts from the default expansion: the rows of
        # one view do not name the rows of the other)
        if (
            self._tree_all
            and self._tree_kind == self._kind
            and getattr(self, "_tree_view", None) == self.project_view()
        ):
            prev = (
                self._expanded_before_query
                if self._expanded_before_query is not None
                else self._expanded_keys()
            )
        self._tree_kind = self._kind
        self._tree.blockSignals(True)
        by_proj: "collections.OrderedDict[str, list]" = collections.OrderedDict()
        for pr, li in d.get("libs", []):
            by_proj.setdefault(pr.name, []).append((pr, li))

        def fill(tree_mode: bool) -> None:
            self._tree.clear()
            self._tree_items = {}
            self._tree_all = []
            self._tree_item("Favourites", 0, _FAV, "star", tip="Starred %s" % noun)
            self._tree_item("Recent", 0, _RECENT, "clock", tip="Recently placed %s" % noun)
            self._tree_item(
                "All libraries",
                0,
                _ALL,
                "all",
                tip="Every %s on the page's include chain" % _KIND_NOUN[self._kind][0],
            )
            if tree_mode:
                self._build_project_tree(by_proj)
            else:
                for _name, libs in by_proj.items():
                    self._add_project_row(None, libs[0][0], libs)

        self._tree_view = self.project_view()
        try:
            fill(self._tree_view == TREE and bool(self._chain))
        except Exception:  # noqa: BLE001 -- the catalog is best-effort: a flat list
            fill(False)
        tops = [self._tree.topLevelItem(k) for k in range(3, self._tree.topLevelItemCount())]
        for it in self._tree_all:
            if it.data(0, _LibKeysRole) is None or it.childCount() == 0:
                continue
            key = self._item_key(it)
            if prev is not None:
                it.setExpanded(key in prev)
            else:
                # the page's project open (all of them when there are few)
                it.setExpanded(it in tops[:1] or len(by_proj) <= 2)
        self._expanded_before_query = prev if self._tokens else None
        self._update_fixed_counts()
        self._update_tree_counts()
        self._select_tree_scope(self._scope)
        self._tree.blockSignals(False)
        self._update_placeholder()

    def _expanded_keys(self) -> set:
        return {
            self._item_key(it)
            for it in self._tree_all
            if it.data(0, _LibKeysRole) is not None and it.isExpanded()
        }

    def _select_tree_scope(self, scope: Scope) -> None:
        cur = self._tree.currentItem()
        csc = cur.data(0, ScopeRole) if cur is not None else None
        if csc and tuple(csc) == tuple(scope):
            # a library listed under several projects keeps the row picked
            it = cur
        else:
            it = self._tree_items.get(scope)
        self._tree.blockSignals(True)
        if it is not None:
            p = it.parent()
            while p is not None:
                if not p.isExpanded():
                    p.setExpanded(True)
                p = p.parent()
            self._tree.setCurrentItem(it)
            self._tree.scrollToItem(it)
        else:
            self._tree.setCurrentItem(None)
        self._tree.blockSignals(False)

    def _on_tree_current(self, cur, _prev) -> None:
        if cur is None:
            return
        sc = cur.data(0, ScopeRole)
        if sc:
            self.set_scope(tuple(sc))

    def _update_fixed_counts(self) -> None:
        d = self._kind_data()
        ext = KIND_EXT[self._kind]
        ents = d.get("entries", [])
        ranks = self._ranks() if self._tokens else None
        fav = self._resolve_refs(self._favs, ext)
        rec = self._resolve_refs(self._recent, ext)
        if ranks is not None:
            fav = [i for i in fav if ranks[i] is not None]
            rec = [i for i in rec if ranks[i] is not None]
            n_all = sum(1 for i, e in enumerate(ents) if not e.shadowed_by and ranks[i] is not None)
        else:
            n_all = sum(1 for e in ents if not e.shadowed_by)
        for sc, n in ((_FAV, len(fav)), (_RECENT, len(rec)), (_ALL, n_all)):
            it = self._tree_items.get(sc)
            if it is not None:
                it.setText(1, str(n) if n or sc == _ALL else "")

    def _update_tree_counts(self) -> None:
        d = self._kind_data()
        if not d:
            return
        querying = bool(self._tokens)
        if querying and self._expanded_before_query is None:
            self._expanded_before_query = self._expanded_keys()
        counts = dict(d["lib_count"])
        if querying:
            ranks = self._ranks()
            counts = {k: 0 for k in counts}
            for i, k in enumerate(d["libkey"]):
                if ranks[i] is not None and k in counts:
                    counts[k] += 1
        tree = getattr(self, "_tree_view", TREE) == TREE

        def visit(it: QtWidgets.QTreeWidgetItem) -> int:
            """Set the counts under ``it`` (a project row); its subtree's total."""
            sc = it.data(0, ScopeRole)
            keys = it.data(0, _LibKeysRole)
            if keys is None:  # a project that is not found
                it.setHidden(querying)
                return 0
            own = sum(counts.get(k, 0) for k in keys)
            sub = 0
            for c in range(it.childCount()):
                ch = it.child(c)
                csc = ch.data(0, ScopeRole)
                if csc is not None and csc[0] == "library":
                    n = counts.get(csc[1], 0)
                    ch.setText(1, str(n))
                    ch.setHidden(querying and n == 0 and self._scope != tuple(csc))
                else:
                    sub += visit(ch)
            if not it.data(0, _CycleRole):
                it.setText(1, str(own) if own or not tree else "")
            total = own + sub
            it.setHidden(querying and total == 0 and self._scope != tuple(sc or ()))
            if querying and it.childCount():
                it.setExpanded(total > 0)
            return total

        self._tree.setUpdatesEnabled(False)
        for k in range(self._tree.topLevelItemCount()):
            it = self._tree.topLevelItem(k)
            if it.data(0, _LibKeysRole) is not None or it.data(0, ScopeRole) is None:
                visit(it)
        if not querying and self._expanded_before_query is not None:
            for it in self._tree_all:
                if it.data(0, _LibKeysRole) is not None and it.childCount():
                    it.setExpanded(self._item_key(it) in self._expanded_before_query)
            self._expanded_before_query = None
        self._tree.setUpdatesEnabled(True)

    # ------------------------------------------------------------------ filtering
    def _resolve_refs(self, refs, ext: str) -> List[int]:
        ref = self._kind_data().get("ref", {})
        out, seen = [], set()
        for x, lib, name in refs:
            if x != ext:
                continue
            i = ref.get((lib.lower(), name.lower()))
            if i is not None and i not in seen:
                seen.add(i)
                out.append(i)
        return out

    def _ranks(self) -> list:
        """Per-entry rank tuple (None = no match) for the current query, cached (with the
        letters in order matches or without: Settings > Pages and libraries > Searching)."""
        key = (self._kind, " ".join(self._tokens), self._regex, _search.FUZZY)
        if self._scores and self._scores[0] == key:
            return self._scores[1]
        preps = self._kind_data().get("preps", [])
        toks = self._tokens
        if self._regex:
            # one regular expression, found in the name, the library, the project or
            # 'library.name' (without case); the matches keep their order
            m = TextMatch(self._query, True)
            ranks = [(0, 0) if m(p[0], p[1], p[2], p[1] + "." + p[0]) else None for p in preps]
        else:
            ranks = [score_tokens(p, toks) for p in preps]
        self._scores = (key, ranks)
        return ranks

    def _scope_indices(self, scope: Scope) -> List[int]:
        d = self._kind_data()
        ents = d.get("entries", [])
        ext = KIND_EXT[self._kind]
        if scope == _FAV:
            return self._resolve_refs(self._favs, ext)
        if scope == _RECENT:
            return self._resolve_refs(self._recent, ext)
        if scope[0] == "library":
            k = scope[1]
            return [i for i, lk in enumerate(d.get("libkey", [])) if lk == k]
        if scope[0] == "project":
            return [i for i, e in enumerate(ents) if e.project == scope[1]]
        return [i for i, e in enumerate(ents) if not e.shadowed_by]

    def _apply_search_text(self) -> None:
        self._apply_query(self._search.text(), regex=self._search_regex.on)

    def _apply_query(self, text: str, regex: bool = False) -> None:
        text = (text or "").strip()
        if (text, bool(regex)) == (self._query, self._regex):
            return
        self._query, self._regex = text, bool(regex)
        # (a regular expression is one term: the words split it)
        self._tokens = ([text] if text else []) if regex else [t for t in text.lower().split() if t]
        self.hide_preview()
        if not self._chain:
            return
        self._update_fixed_counts()
        self._update_tree_counts()
        self._rebuild(keep=None, select_first=bool(self._tokens))
        self._grid.scrollToTop()

    def refresh_search(self) -> None:
        """The results again for the search typed (Settings changed how a search matches:
        'Also find names with the letters in order')."""
        self.hide_preview()
        if not (self._tokens and self._chain):
            return
        self._update_fixed_counts()
        self._update_tree_counts()
        self._rebuild(keep=None, select_first=True)

    def _in_sections(self, row: int) -> bool:
        """True when ``row`` lies in the home view's Favourites / Recent sections."""
        for r in range(row, -1, -1):
            h = self._model.header(r)
            if h is not None:
                return h.scope in (_FAV, _RECENT)
        return False

    def _rebuild(
        self, keep: Optional[Entry] = None, select_first: bool = False, keep_scroll: bool = False
    ) -> None:
        d = self._kind_data()
        ents = d.get("entries", [])
        if not self._chain:
            self._model.set_rows([])
            self._show_empty(
                "Open a page to browse its symbol and genie libraries."
                if not self._project_dir
                else "No libraries found for this project."
            )
            self._update_scope_bar(0)
            self._update_status()
            return
        noun1, noun = _KIND_NOUN[self._kind]
        scroll = self._grid.verticalScrollBar().value() if keep_scroll else 0
        sc = self._scope
        idx = self._scope_indices(sc)
        ranks = self._ranks() if self._tokens else None
        if ranks is not None:
            idx = [i for i in idx if ranks[i] is not None]
        rows: list = []
        libs = d["libs"]
        nat, libpos = d["nat"], d["libpos"]

        def lib_header(li_i: int, n: int) -> _Header:
            pr, li = libs[li_i] if 0 <= li_i < len(libs) else (None, None)
            if li is None:
                return _Header("(other)", "", n, None)
            return _Header(li.stem, pr.name, n, ("library", os.path.normcase(li.path)))

        def grouped_rows(ix: List[int]) -> list:
            groups: Dict[int, List[int]] = {}
            for i in ix:
                groups.setdefault(libpos[i], []).append(i)
            if ranks is not None:
                for g in groups.values():
                    g.sort(key=lambda i: (ranks[i], nat[i]))
                order = sorted(groups, key=lambda lp: (ranks[groups[lp][0]], lp))
            else:
                for g in groups.values():
                    g.sort(key=lambda i: nat[i])
                order = sorted(groups)
            out = []
            for lp in order:
                out.append(lib_header(lp, len(groups[lp])))
                out.extend(ents[i] for i in groups[lp])
            return out

        def flat(ix: List[int]) -> list:
            if ranks is not None:
                ix = sorted(ix, key=lambda i: (ranks[i], nat[i], libpos[i]))
            else:
                ix = sorted(ix, key=lambda i: (nat[i], libpos[i]))
            return [ents[i] for i in ix]

        if sc in (_FAV, _RECENT):
            if ranks is not None:
                idx = sorted(idx, key=lambda i: ranks[i])
            rows = [ents[i] for i in idx]
        elif sc[0] == "library":
            rows = flat(idx)
        else:
            home = sc == _ALL and ranks is None
            if home:
                ext = KIND_EXT[self._kind]
                fav = self._resolve_refs(self._favs, ext)
                rec = self._resolve_refs(self._recent, ext)[:HOME_RECENT]
                if fav:
                    rows.append(_Header("Favourites", "", len(fav), _FAV, "star"))
                    rows.extend(ents[i] for i in fav)
                if rec:
                    rows.append(_Header("Recently placed", "", len(rec), _RECENT, "clock"))
                    rows.extend(ents[i] for i in rec)
            if self._grouped:
                rows.extend(grouped_rows(idx))
            else:
                if rows:
                    rows.append(_Header("All %s" % noun, "", len(idx), None, "all"))
                rows.extend(flat(idx))
        self._model.set_rows(rows)
        n = len(idx)
        self._last_counts = (n, len({libpos[i] for i in idx}))
        self._built_recent = self._recent_ver
        self._update_scope_bar(n)
        if not rows:
            self._show_no_results(noun)
        else:
            self._stack.setCurrentIndex(0)
        # selection
        target = -1
        if keep is not None:
            rs = self._model.rows_for_key(keep.key)
            if rs:
                target = rs[0]
        if target < 0 and select_first:
            target = self._model.first_entry_row(0, 1)
        if target >= 0:
            ix = self._model.index(target, 0)
            self._grid.selectionModel().setCurrentIndex(ix, QtCore.QItemSelectionModel.ClearAndSelect)
            if not keep_scroll:
                if target >= self._grid.BATCH:
                    self._grid.full_layout()  # its row is past the first batch
                self._grid.scrollTo(ix)
        if keep_scroll:
            self._grid.full_layout()
            self._grid.verticalScrollBar().setValue(scroll)
        self._update_status()

    # ------------------------------------------------------------------ chrome
    def _show_empty(self, text: str, link: str = "") -> None:
        self._empty_label.setText(text)
        self._empty_link.setText(link)
        self._empty_link.setVisible(bool(link))
        self._stack.setCurrentIndex(1)

    def _show_no_results(self, noun: str) -> None:
        sc = self._scope
        if not self._kind_data().get("entries"):
            self._show_empty("No %s libraries on this project's include chain." % _KIND_NOUN[self._kind][0])
            return
        if sc == _FAV and not self._tokens:
            self._show_empty("No favourite %s yet.\nStar a card (or press Ctrl+D) to keep it here." % noun)
            return
        if sc == _RECENT and not self._tokens:
            self._show_empty("Nothing placed yet.\nDouble-click or drag a card onto the page.")
            return
        link = ""
        if sc != _ALL and self._tokens:
            ranks = self._ranks()
            n_all = sum(1 for i in self._scope_indices(_ALL) if ranks[i] is not None)
            if n_all:
                link = "Search all libraries (%d)" % n_all
        self._show_empty("No %s match '%s'%s." % (noun, self._query, "" if sc == _ALL else " here"), link)

    def _scope_title(self, sc: Scope) -> Tuple[str, str]:
        if sc == _FAV:
            return "Favourites", "star"
        if sc == _RECENT:
            return "Recently placed", "clock"
        if sc[0] == "project":
            return sc[1], "project"
        if sc[0] == "library":
            for pr, li in self._kind_data().get("libs", []):
                if os.path.normcase(li.path) == sc[1]:
                    return "%s  ›  %s" % (pr.name, li.stem), "library"
            return os.path.basename(sc[1]), "library"
        return "All libraries", "all"

    def _update_scope_bar(self, n: int) -> None:
        title, gl = self._scope_title(self._scope)
        self._scope_icon.setPixmap(glyph(gl, 16, None if gl == "star" else COLORS["text_dim"]).pixmap(16, 16))
        self._scope_label.setText(title)
        self._scope_label.setToolTip(title)
        noun = _KIND_NOUN[self._kind][1 if n != 1 else 0]
        self._scope_count.setText(
            ("%d match%s" % (n, "" if n == 1 else "es")) if self._tokens else "%d %s" % (n, noun)
        )
        self._btn_scope_clear.setVisible(self._scope != _ALL)

    def _update_placeholder(self) -> None:
        n = sum(1 for e in self._kind_data().get("entries", []) if not e.shadowed_by)
        noun = _KIND_NOUN[self._kind][1]
        one = _KIND_NOUN[self._kind][0]
        self._search.setPlaceholderText(
            ("Search %d %s   (Ctrl+F)" % (n, one if n == 1 else noun)) if n else "Search %s   (Ctrl+F)" % noun
        )

    def _update_status(self) -> None:
        e = self.current_entry()
        if e is not None:
            s = self._thumbs.summary(e) or self._thumbs.request_summary(e)
            size = ""
            if s is not None and getattr(s, "width", 0) and getattr(s, "height", 0):
                size = "  ·  %d × %d" % (s.width, s.height)
            text = "%s%s  ·  %s" % (e.reference, size, e.project)
        else:
            n, libs = getattr(self, "_last_counts", (0, 0))
            noun = _KIND_NOUN[self._kind]
            if not self._chain:
                text = ""
            elif self._tokens:
                text = "%d match%s in %d librar%s" % (
                    n,
                    "" if n == 1 else "es",
                    libs,
                    "y" if libs == 1 else "ies",
                )
            else:
                text = "%d %s  ·  %d librar%s" % (
                    n,
                    noun[1 if n != 1 else 0],
                    libs,
                    "y" if libs == 1 else "ies",
                )
        fm = QtGui.QFontMetrics(self._status.font())
        self._status.setToolTip(text)
        self._status.setText(fm.elidedText(text, QtCore.Qt.ElideRight, max(60, self._status.width())))

    def _sync_kind_buttons(self, kind: Optional[str] = None) -> None:
        kind = kind or self._kind
        for k, b in self._btn_kind.items():
            b.blockSignals(True)
            b.setChecked(k == kind)
            b.blockSignals(False)

    def _sync_view_buttons(self) -> None:
        self._btn_grid.setChecked(self._view_mode == "grid")
        self._btn_list.setChecked(self._view_mode == "list")
        self._act_grid.setChecked(self._view_mode == "grid")
        self._act_list.setChecked(self._view_mode == "list")
        self._slider.setEnabled(self._view_mode == "grid")
        for px, a in self._size_acts.items():
            a.setChecked(abs(px - self._card) <= 8)

    # ------------------------------------------------------------------ thumbnails
    def card_pixmap(self, e: Entry, w: int, h: int) -> Optional[QtGui.QPixmap]:
        """The element thumbnail fitted into w x h (None while loading)."""
        k = (e.key, w, h)
        pm = self._scaled.get(k)
        if pm is not None:
            return pm
        base = self._keyed.get(e.key)
        if base is None:
            full = self._thumbs.pixmap(e, "full")
            if full is None:
                if not self._thumbs.has_no_thumbnail(e):
                    self._thumbs.request_full(e)
                return None
            base = key_background(full)
            self._keyed.put(e.key, base)
        pm = fit_pixmap(base, w, h)
        self._scaled.put(k, pm)
        return pm

    def has_no_thumbnail(self, e: Entry) -> bool:
        return self._thumbs.has_no_thumbnail(e)

    def wait_thumbnails(self, timeout_ms: int = 5000) -> bool:
        """Pump events until queued thumbnails landed (tests / screenshots)."""
        return self._thumbs.wait_idle(timeout_ms)

    def drag_pixmap(self, e: Entry) -> QtGui.QPixmap:
        pm = self.card_pixmap(e, 72, 56)
        out = QtGui.QPixmap(84, 68)
        out.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(out)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setOpacity(0.9)
        p.setPen(QtGui.QPen(QtGui.QColor(COLORS["accent"]), 1.5))
        p.setBrush(_WELL)
        p.drawRoundedRect(QtCore.QRectF(1, 1, 82, 66), 6, 6)
        if pm is not None and not pm.isNull():
            p.drawPixmap((84 - pm.width()) // 2, (68 - pm.height()) // 2, pm)
        else:
            glyph("library", 32, "#555").paint(p, QtCore.QRect(26, 18, 32, 32))
        p.end()
        return out

    def _on_thumb(self, key: str) -> None:
        rows = self._model.rows_for_key(key)
        for r in rows:
            ix = self._model.index(r, 0)
            self._model.dataChanged.emit(ix, ix)
        if self._preview.isVisible() and self._preview.entry_key == key:
            e = self._model.entry(rows[0]) if rows else None
            if e is not None:
                self._refresh_preview(e)

    def _on_summary(self, key: str, _summary) -> None:
        e = self.current_entry()
        if e is not None and e.key == key:
            self._update_status()

    # ------------------------------------------------------------------ hover preview
    def _preview_info(self, e: Entry) -> str:
        s = self._thumbs.summary(e)
        noun = _KIND_NOUN.get(e.kind, ("element",))[0].capitalize()
        lines = ["%s  ·  %s project" % (noun, e.project)]
        facts = []
        if s is not None and getattr(s, "width", 0) and getattr(s, "height", 0):
            facts.append("%d × %d px" % (s.width, s.height))
        n = getattr(s, "object_count", None) if s is not None else None
        if n:
            facts.append("%d object%s" % (n, "" if n == 1 else "s"))
        if facts:
            lines.append("  ·  ".join(facts))
        lines.append(os.path.basename(e.path))
        if self.is_flashing(e):
            lines.append("◆ Flashing: shows two pictures in turn at runtime")
        if e.shadowed_by:
            lines.append(
                "<span style='color:%s'>Hidden by the copy in %s, which is placed "
                "instead</span>" % (COLORS["warn"], html.escape(e.shadowed_by))
            )
        if self.is_favourite(e):
            lines.append("<span style='color:%s'>★ Favourite</span>" % _GOLD.name())
        lines.append(
            "<span style='color:%s'>Double-click to place · drag onto the page</span>" % COLORS["text_dim"]
        )
        return "<br>".join(ln if ln.startswith("<span") else html.escape(ln) for ln in lines)

    def _preview_pixmap(self, e: Entry) -> Optional[QtGui.QPixmap]:
        b = _HoverPreview.box()
        return self.card_pixmap(e, b.width(), b.height())

    @staticmethod
    def preview_on() -> bool:
        """Settings > Pages and libraries > Library panel > Preview of a card you point at
        is not Off."""
        return _panels.LIBRARY_PREVIEW != "off"

    def show_preview(self, e: Entry, anchor_global: QtCore.QRect) -> None:
        if not self.preview_on():
            self.hide_preview()
            return
        self._preview_anchor = anchor_global
        self._preview.show_for(e, self._preview_pixmap(e), self._preview_info(e), anchor_global)

    def _refresh_preview(self, e: Entry) -> None:
        anchor = getattr(self, "_preview_anchor", None)
        if anchor is not None and self.preview_on():
            self._preview.show_for(e, self._preview_pixmap(e), self._preview_info(e), anchor)

    def hide_preview(self) -> None:
        if self._preview.isVisible():
            self._preview.hide()

    def preview_widget(self) -> QtWidgets.QFrame:
        return self._preview

    # ------------------------------------------------------------------ actions
    def _on_double_clicked(self, idx: QtCore.QModelIndex) -> None:
        e = self._model.entry(idx.row()) if idx.isValid() else None
        if e is not None:
            self.place(e)

    def _context_menu(self, pos: QtCore.QPoint) -> None:
        idx = self._grid.indexAt(pos)
        e = self._model.entry(idx.row()) if idx.isValid() else None
        if e is None:
            return
        self.hide_preview()
        self._grid.setCurrentIndex(idx)
        m = self.build_context_menu(e, idx.row())
        m.exec(self._grid.viewport().mapToGlobal(pos))

    def build_context_menu(self, e: Entry, row: int = -1) -> QtWidgets.QMenu:
        m = QtWidgets.QMenu(self)
        a = m.addAction("Place on Page", lambda: self.place(e))
        f = a.font()
        f.setBold(True)
        a.setFont(f)
        m.addAction("Edit Element", lambda: self.edit(e))
        if e.kind == "genie":
            m.addAction("Edit Genie Form…", lambda: self.formRequested.emit(e.path, e.name))
        m.addAction("Used By…", lambda: self.request_used_by(e))
        tid, what = self.element_help(e)
        if tid:
            label = {"element": "Help for '%s'" % e.name, "library": "Help for Library '%s'" % e.library}.get(
                what, "Help for Project '%s'" % e.project
            )
            a = m.addAction(
                icon("help"),
                label,
                lambda t=tid: self.open_element_help(t),
            )
            a.setToolTip("Open the AVEVA help for this %s" % ("element" if what == "element" else what))
        m.addSeparator()
        fav = self.is_favourite(e)
        m.addAction(
            glyph("star" if not fav else "star_outline", 16),
            "Remove from Favourites" if fav else "Add to Favourites",
            lambda: self.set_favourite(e, not fav),
        )
        in_recent = self._scope == _RECENT or (
            row >= 0 and self._in_sections(row) and self._section_of(row) == _RECENT
        )
        if in_recent:
            m.addAction("Remove from Recent", lambda: self.remove_recent(e))
        lib_scope = ("library", os.path.normcase(e.path))
        if self._scope != lib_scope:
            m.addAction(
                glyph("library", 16), "Show Library '%s'" % e.library, lambda: self._show_library_of(e)
            )
        m.addSeparator()
        clip = QtWidgets.QApplication.clipboard()
        m.addAction("Copy Reference (%s)" % e.reference, lambda: clip.setText(e.reference))
        m.addAction("Show in Explorer", lambda: self._reveal(e.path))
        m.addSeparator()
        m.addAction("Rename Element…", lambda: self.request_rename(e))
        m.addAction("Delete Element…", lambda: self.request_delete(e))
        return m

    @staticmethod
    def element_help(e: Entry):
        """(AVEVA help topic, 'element' | 'library' | 'project') of a library
        element -- the reference page of a system genie (SA_Include,
        SA_Controls, SA_Library ...), else its library's or project's page
        (io.genie_help.element_help); (None, '') when the help has none."""
        if not e.is_element:
            return None, ""
        try:
            from ...io import genie_help

            return genie_help.element_help(e.library, e.name, e.project)
        except Exception:  # noqa: BLE001 -- the reference is optional
            return None, ""

    @staticmethod
    def open_element_help(topic_id: str) -> None:
        """Open AVEVA help topic ``topic_id`` (the installed page, else the
        help server)."""
        from ... import help as H

        QtGui.QDesktopServices.openUrl(QtCore.QUrl(H.aveva_target(topic_id)))

    def _section_of(self, row: int) -> Optional[Scope]:
        for r in range(row, -1, -1):
            h = self._model.header(r)
            if h is not None:
                return h.scope
        return None

    def _show_library_of(self, e: Entry) -> None:
        self.set_query("")
        self.set_scope(("library", os.path.normcase(e.path)))
        self.select_entry(e)

    @staticmethod
    def _reveal(path: str) -> None:
        try:
            from ..browser.dialog import reveal_in_explorer

            reveal_in_explorer(path)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ events
    def showEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().showEvent(ev)
        if (
            not self._dirty
            and self._loaded_stamp is not None
            and self._project_dir
            and self._loaded_stamp != self._chain_stamp()
        ):
            self._dirty = True  # a library changed while the panel was hidden
        if self._dirty or (self._chain and self._kind not in self._data):
            self._load_timer.start()
        else:
            # Recent changed since the grid was built (a placement)
            if (
                self._chain
                and self._built_recent != self._recent_ver
                and self._scope in (_ALL, _RECENT)
                and not self._tokens
            ):
                self._rebuild(keep=self.current_entry(), keep_scroll=True)

    def hideEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        self.hide_preview()
        super().hideEvent(ev)

    def resizeEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(ev)
        wide = self.width() >= self.WIDE
        if wide != self._wide:
            if self._wide is not None:
                self._split_sizes["h" if self._wide else "v"] = self._split.sizes()
            self._wide = wide
            self._split.setOrientation(QtCore.Qt.Horizontal if wide else QtCore.Qt.Vertical)
            sizes = self._split_sizes["h" if wide else "v"]
            if not sizes or sum(sizes) <= 0:
                total = max(200, self.width() if wide else self.height() - 120)
                first = min(250, int(total * 0.28)) if wide else min(230, int(total * 0.3))
                sizes = [first, max(100, total - first)]
            self._split.setSizes(sizes)
        self._update_status()

    def _on_split_moved(self, *_a) -> None:
        if self._wide is None:
            return
        key = "h" if self._wide else "v"
        self._split_sizes[key] = self._split.sizes()
        self._save("split_" + key, json.dumps(self._split_sizes[key]))

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 (Qt API)
        t = ev.type()
        if t == QtCore.QEvent.ShortcutOverride:
            key, mods = ev.key(), ev.modifiers() & ~QtCore.Qt.KeypadModifier
            if key == QtCore.Qt.Key_F and mods == QtCore.Qt.ControlModifier:
                ev.accept()  # beat the window's Edit > Find
                return True
            if self._panel_key(key, mods) is not None:
                ev.accept()  # the page browser's keys, while here
                return True
            if (
                key == QtCore.Qt.Key_D
                and mods == QtCore.Qt.ControlModifier
                and obj in (self._grid, self._grid.viewport())
            ):
                ev.accept()
                return True
            if key == QtCore.Qt.Key_Escape and (self._search.text() or self._scope != _ALL):
                ev.accept()
                return True
            return False
        if t != QtCore.QEvent.KeyPress:
            return super().eventFilter(obj, ev)
        key = ev.key()
        mods = ev.modifiers() & ~QtCore.Qt.KeypadModifier
        if key == QtCore.Qt.Key_F and mods == QtCore.Qt.ControlModifier:
            self.focus_search()
            return True
        act = self._panel_key(key, mods)
        if act is not None:
            act()
            return True
        if obj is self._search:
            return self._search_key(ev, key, mods)
        if obj in (self._grid, self._grid.viewport()):
            return self._grid_key(ev, key, mods)
        if key == QtCore.Qt.Key_Escape and (self._search.text() or self._scope != _ALL):
            self._escape()
            return True
        return super().eventFilter(obj, ev)

    def _panel_key(self, key, mods):
        """The panel's own keys (as in the page browser): Ctrl+1 grid, Ctrl+2
        list, F5 rescan; None for any other key."""
        if mods == QtCore.Qt.ControlModifier and key == QtCore.Qt.Key_1:
            return lambda: self.set_view_mode("grid")
        if mods == QtCore.Qt.ControlModifier and key == QtCore.Qt.Key_2:
            return lambda: self.set_view_mode("list")
        if not mods and key == QtCore.Qt.Key_F5:
            return self.refresh
        return None

    def _escape(self) -> None:
        if self._search.text():
            self.set_query("")
        elif self._scope != _ALL:
            self.set_scope(_ALL)

    def _search_key(self, ev, key, mods) -> bool:
        if key in (QtCore.Qt.Key_Down, QtCore.Qt.Key_PageDown, QtCore.Qt.Key_Up, QtCore.Qt.Key_PageUp):
            # move the grid's selection; the focus stays in the search
            self._search.flush()
            if not self._grid.currentIndex().isValid():
                r = self._model.first_entry_row(0, 1)
                if r >= 0:
                    self._grid.setCurrentIndex(self._model.index(r, 0))
            else:
                cur = self._grid.currentIndex()
                act = {
                    QtCore.Qt.Key_Down: QtWidgets.QAbstractItemView.MoveDown,
                    QtCore.Qt.Key_Up: QtWidgets.QAbstractItemView.MoveUp,
                    QtCore.Qt.Key_PageDown: QtWidgets.QAbstractItemView.MovePageDown,
                    QtCore.Qt.Key_PageUp: QtWidgets.QAbstractItemView.MovePageUp,
                }[key]
                nxt = self._grid.moveCursor(act, QtCore.Qt.NoModifier)
                if nxt.isValid() and nxt != cur:
                    self._grid.setCurrentIndex(nxt)
                    self._grid.scrollTo(nxt)
            return True
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self._search.flush()
            if self.current_entry() is None:
                r = self._model.first_entry_row(0, 1)
                if r >= 0:
                    self._grid.setCurrentIndex(self._model.index(r, 0))
            self.place()
            return True
        if key == QtCore.Qt.Key_Escape and (self._search.text() or self._scope != _ALL):
            self._escape()
            return True
        return False

    def _grid_key(self, ev, key, mods) -> bool:
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.place()
            return True
        if key == QtCore.Qt.Key_D and mods == QtCore.Qt.ControlModifier:
            self.toggle_favourite()
            return True
        if key == QtCore.Qt.Key_Escape and (self._search.text() or self._scope != _ALL):
            self._escape()
            return True
        if key == QtCore.Qt.Key_Backspace and not mods:
            self._search.setFocus()
            self._search.deselect()
            self._search.end(False)
            self._search.backspace()
            return True
        text = ev.text()
        if (
            text
            and text.isprintable()
            and not text.isspace()
            and not (mods & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier | QtCore.Qt.MetaModifier))
        ):
            self._search.setFocus()  # type-to-search REFINES the query
            self._search.deselect()
            self._search.end(False)
            self._search.insert(text)
            return True
        return False


from ...infra import caches as _caches  # noqa: E402

_caches.register("library_panel.glyphs", _GLYPHS, on=("tests",))
