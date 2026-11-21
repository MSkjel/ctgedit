"""Card drawing for the browser's grid view."""

from __future__ import annotations

import collections
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..style import theme as _theme
from ..style.theme import COLORS, fixed, tracked
from .model import BadgesRole, CardPixmapRole, EntryRole, ResolutionRole, SubtitleRole, ThumbStateRole
from .thumbs import CARD_SIZE

#: the card's text area below the well (px at 100 %: the name and the second line grow
#: with the text size)
_TEXT_H = 50
CELL = QtCore.QSize(CARD_SIZE.width() + 16, CARD_SIZE.height() + _TEXT_H)
#: card (well) widths the size slider offers
CARD_MIN, CARD_MAX = 112, 272
# theme-following constants (ui/style/theme.tracked: updated in place on a theme change)
_WELL = tracked("#15171a")
#: the card well behind a library element (CtDraw's element window background)
_ELEMENT_WELL = fixed("element_well")
_HOVER = tracked("#2c3036")
#: the badges on a thumbnail (the same in every theme: they sit on page art)
_BADGE = {
    "unregistered": ("Not registered", fixed("badge_amber")),
    "popup": ("!", fixed("badge_blue")),
    "shadowed": ("Hidden", fixed("badge_grey")),
}


def card_size(width: int) -> QtCore.QSize:
    """The well of a card ``width`` pixels wide (CARD_SIZE's aspect)."""
    w = max(CARD_MIN, min(CARD_MAX, int(width)))
    return QtCore.QSize(w, int(round(w * CARD_SIZE.height() / float(CARD_SIZE.width()))))


def cell_size(width: int) -> QtCore.QSize:
    """The grid cell of a card ``width`` pixels wide (its text area grows with the text
    size)."""
    s = card_size(width)
    return QtCore.QSize(s.width() + 16, s.height() + _theme.px(_TEXT_H))


def fold_name(name: str, prefix: str) -> str:
    """``'…' + rest`` when ``name`` starts with the scope's common ``prefix``."""
    if prefix and name.startswith(prefix) and len(name) > len(prefix):
        return "…" + name[len(prefix) :]
    return name


class CardDelegate(QtWidgets.QStyledItemDelegate):
    """Paints one thumbnail card per item (grid / IconMode list view)."""

    def __init__(self, parent=None, prefix_getter=None, width_getter: Optional[Callable[[], int]] = None):
        super().__init__(parent)
        self._prefix = prefix_getter or (lambda: "")
        self._width = width_getter or (lambda: CARD_SIZE.width())
        self._fonts_for = None  # the text size the fonts were made at
        self._fonts()
        self._scaled: "collections.OrderedDict" = collections.OrderedDict()

    def _fonts(self) -> None:
        """The card's fonts from the type scale (theme.TYPE), made again when the text
        size changed: the name (body), the second line and the badges (caption)."""
        scale = _theme.ui_scale()
        if self._fonts_for == scale:
            return
        self._fonts_for = scale
        self._name_font = _theme.font("body")
        self._sub_font = _theme.font("caption")
        self._badge_font = _theme.font("caption")
        self._badge_font.setBold(True)

    def sizeHint(self, option, index) -> QtCore.QSize:  # noqa: N802 (Qt API)
        return cell_size(self._width())

    def _fit(self, pm: QtGui.QPixmap, box: QtCore.QSize) -> QtGui.QPixmap:
        """``pm`` fitted into ``box`` (down, or up at most 1.6 times), cached."""
        f = min(box.width() / float(pm.width()), box.height() / float(pm.height()), 1.6)
        if abs(f - 1.0) < 0.02:
            return pm
        k = (pm.cacheKey(), box.width(), box.height())
        hit = self._scaled.get(k)
        if hit is not None:
            self._scaled.move_to_end(k)
            return hit
        out = pm.scaled(
            max(1, int(pm.width() * f)),
            max(1, int(pm.height() * f)),
            QtCore.Qt.IgnoreAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        self._scaled[k] = out
        while len(self._scaled) > 600:
            self._scaled.popitem(last=False)
        return out

    def paint(self, p: QtGui.QPainter, opt, index) -> None:
        e = index.data(EntryRole)
        if e is None:
            return
        self._fonts()
        r = opt.rect.adjusted(4, 4, -4, -4)
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        sel = bool(opt.state & QtWidgets.QStyle.State_Selected)
        hov = bool(opt.state & QtWidgets.QStyle.State_MouseOver)
        other = "other_install" in (index.data(BadgesRole) or [])
        border = (
            QtGui.QColor(COLORS["accent"])
            if sel
            else (QtGui.QColor(COLORS["warn"]) if other else QtGui.QColor(COLORS["border"]))
        )
        p.setPen(QtGui.QPen(border, 1.6 if sel else 1.0))
        p.setBrush(
            QtGui.QColor(COLORS["selection"]) if sel else (_HOVER if hov else QtGui.QColor(COLORS["panel"]))
        )
        p.drawRoundedRect(QtCore.QRectF(r), 5, 5)

        box = card_size(self._width())
        well = QtCore.QRect(r.left() + 4, r.top() + 4, r.width() - 8, box.height())
        # a symbol / genie is drawn on CtDraw's element window white (on the dark well a
        # transparent or dark element would vanish); a page has its own colour
        kind = getattr(e, "kind", "page") if e is not None else "page"
        p.fillRect(well, _ELEMENT_WELL if kind in ("symbol", "genie", "supergenie") else _WELL)
        pm = index.data(CardPixmapRole)
        if isinstance(pm, QtGui.QPixmap) and not pm.isNull():
            s = self._fit(pm, well.size())
            p.drawPixmap(
                well.left() + (well.width() - s.width()) // 2,
                well.top() + (well.height() - s.height()) // 2,
                s,
            )
        else:
            state = index.data(ThumbStateRole)
            if state == "none":
                # (no preview: the well shows the whole name, the one thing left to compare
                # by)
                fm_s = QtGui.QFontMetrics(self._sub_font)
                note_h = fm_s.height() + 4
                text_rect = well.adjusted(8, 6, -8, -note_h - 2)
                p.setPen(QtGui.QColor(COLORS["text"]))
                p.setFont(self._name_font)
                p.drawText(
                    text_rect,
                    QtCore.Qt.AlignCenter | QtCore.Qt.TextWrapAnywhere,
                    getattr(e, "name", "") or "",
                )
                p.setPen(QtGui.QColor(COLORS["text_dim"]))
                p.setFont(self._sub_font)
                p.drawText(
                    QtCore.QRect(well.left(), well.bottom() - note_h, well.width(), note_h),
                    QtCore.Qt.AlignCenter,
                    "No preview",
                )
            else:
                p.setPen(QtGui.QColor(COLORS["text_dim"]))
                p.setFont(self._sub_font)
                p.drawText(well, QtCore.Qt.AlignCenter, "…")

        # badges: top-right stack (not registered / hidden), system page top-left
        badges = index.data(BadgesRole) or []
        p.setFont(self._badge_font)
        fm = QtGui.QFontMetrics(self._badge_font)
        x_right = well.right() - 3
        for b in ("unregistered", "shadowed"):
            if b not in badges:
                continue
            text, col = _BADGE[b]
            w = fm.horizontalAdvance(text) + 10
            br = QtCore.QRect(x_right - w, well.top() + 3, w, max(15, fm.height() + 2))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QtCore.QRectF(br), 3, 3)
            p.setPen(fixed("badge_text"))
            p.drawText(br, QtCore.Qt.AlignCenter, text)
            x_right = br.left() - 3
        if "popup" in badges:
            text, col = _BADGE["popup"]
            w = max(15, fm.horizontalAdvance(text) + 10)
            br = QtCore.QRect(well.left() + 3, well.top() + 3, w, max(15, fm.height() + 2))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QtCore.QRectF(br), 3, 3)
            p.setPen(fixed("badge_text"))
            p.drawText(br, QtCore.Qt.AlignCenter, text)

        # resolution chip (pages: from the template reference; templates: name)
        res = index.data(ResolutionRole) or ""
        if res:
            p.setFont(self._badge_font)
            w = fm.horizontalAdvance(res) + 10
            bh = max(15, fm.height() + 2)
            br = QtCore.QRect(well.right() - 3 - w, well.bottom() - bh - 3, w, bh)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(fixed("chip_dark"))
            p.drawRoundedRect(QtCore.QRectF(br), 3, 3)
            p.setPen(fixed("badge_text"))
            p.drawText(br, QtCore.Qt.AlignCenter, res)

        # text lines
        p.setFont(self._name_font)
        fm = QtGui.QFontMetrics(self._name_font)
        nr = QtCore.QRect(r.left() + 6, well.bottom() + 4, r.width() - 12, max(17, fm.height() + 2))
        p.setPen(QtGui.QColor(COLORS["sel_text"] if sel else COLORS["text"]))
        name = fold_name(e.name, self._prefix()) if e.kind == "page" else e.name
        p.drawText(
            nr,
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            fm.elidedText(name, QtCore.Qt.ElideMiddle, nr.width()),
        )
        sub = index.data(SubtitleRole) or ""
        if sub:
            p.setFont(self._sub_font)
            fm2 = QtGui.QFontMetrics(self._sub_font)
            sr = QtCore.QRect(r.left() + 6, nr.bottom() + 1, r.width() - 12, max(15, fm2.height() + 2))
            p.setPen(QtGui.QColor(COLORS["sel_text_dim" if sel else "text_dim"]))
            p.drawText(
                sr,
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                fm2.elidedText(sub, QtCore.Qt.ElideRight, sr.width()),
            )
        p.restore()
