"""The ribbon."""

from __future__ import annotations
from .style.theme import sp as _sp

from typing import Dict, List, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from .style import tips
from .style.accessibility import spoken
from .prefs.settings import app_settings
from .style.theme import COLORS, arrow_image, color, px, type_qss

EXPANDED_H = 104  # at 100 % text size (see RibbonBar.expanded_height)
COLLAPSED_H = 30
_OVERFLOW_HYSTERESIS = 24
#: the gap between two groups on a tab page (the page layout's spacing)
GROUP_GAP = 4
#: the states of a group, from the widest to the narrowest
STATES = ("full", "compact", "collapsed")

#: the design sizes (px at 100 %) the density can change (theme.density)
_DENSITY_DEFAULTS = {
    "ribbon_h": EXPANDED_H,
    "ribbon_big_h": 64,
    "ribbon_small_h": 20,
    "qat_icon": 16,
    "captions": True,
}


def density(key: str):
    """A ribbon size of the density chosen in Settings > Appearance (theme.density), or the
    comfortable design value while there is no density setting."""
    from .style import theme

    fn = getattr(theme, "density", None)
    if callable(fn):
        try:
            v = fn(key)
            if v is not None:
                return v
        except KeyError, TypeError, ValueError:  # swallow-ok: a key the theme lacks
            pass
    return _DENSITY_DEFAULTS[key]


def big_height() -> int:
    return px(density("ribbon_big_h"))


def small_height() -> int:
    return px(density("ribbon_small_h"))


def ribbon_qss() -> str:
    """The ribbon's own rules, in the active scheme: the header, the body and the group
    dividers, flat split and menu arrows that never overlap their text, the Quick Access
    hover, captions and the greyed look of a command that cannot run."""
    c = dict(COLORS)
    down = arrow_image("text", 8, 4) or ""
    dim = arrow_image("text_dim", 6, 3) or ""
    dis = color("text_dim")
    dis_text = "rgba(%d, %d, %d, 150)" % (dis.red(), dis.green(), dis.blue())
    c.update(down=down, dim=dim, dis=dis_text)
    rules = (
        """
QWidget#RibbonBar, QWidget#RibbonBody, QWidget#RibbonPage, QFrame#RibbonFlyout {
    background: %(panel)s; }
QWidget#RibbonHeader { background: %(bg)s; }
QWidget#RibbonBar { border-bottom: 1px solid %(border)s; }
QFrame#RibbonFlyout { border: 1px solid %(border)s; border-top: none; }
QTabBar#RibbonTabs::tab { background: transparent; color: %(text_dim)s; padding: 5px 12px;
    margin-top: 2px; border: 1px solid transparent; border-bottom: none;
    border-top-left-radius: 4px; border-top-right-radius: 4px; }
QTabBar#RibbonTabs::tab:selected { background: %(panel)s; color: %(text)s;
    border-color: %(border)s; }
QTabBar#RibbonTabs::tab:hover:!selected { color: %(text)s; background: %(hover)s; }
QToolButton[ctgMenu="true"] { padding-right: 14px; }
QToolButton[popupMode="2"] { padding-right: 14px; }
QToolButton[ctgMenu="true"]::menu-indicator, QToolButton[popupMode="2"]::menu-indicator {
    subcontrol-origin: padding; subcontrol-position: center right; right: 3px;
    width: 8px; height: 4px; }
QToolButton[popupMode="1"]::menu-button { width: 12px; border: none;
    background: transparent; }
QToolButton[popupMode="1"]::menu-button:hover { border-left: 1px solid %(border)s;
    background: %(hover)s; }
QToolButton#RibbonCaption { color: %(text_dim)s; font-size: 11px; border: none;
    background: transparent; padding: 0px 10px 0px 2px; }
QToolButton#RibbonCaption:hover { color: %(text)s; }
QToolButton#RibbonCaption[kbdFocus="true"] { border: 2px solid %(accent)s;
    padding: 0px 8px 0px 0px; }
QToolButton#RibbonCaption::menu-indicator { subcontrol-origin: padding;
    subcontrol-position: center right; right: 1px; width: 6px; height: 3px; }
QWidget#RibbonQat QToolButton { border: 1px solid transparent; padding: 2px; }
QWidget#RibbonQat QToolButton:hover { background: %(hover)s; border-color: %(border)s; }
QWidget#RibbonQat QToolButton[kbdFocus="true"] { border: 2px solid %(accent)s;
    padding: 1px; }
QToolButton#QatArrow { padding: 0px; }
QToolButton#QatArrow::menu-indicator { image: none; width: 0px; }
QToolButton#RibbonChevron:checked { background: transparent; border-color: transparent; }
QToolButton#RibbonChevron:checked:hover { background: %(hover)s; border-color: %(border)s; }
QToolButton:disabled { color: %(dis)s; }
QLabel#RibbonEmptyText { color: %(text_dim)s; }
"""
        % c
    )
    if down:
        rules += (
            'QToolButton[popupMode="1"]::menu-arrow { image: url("%(down)s"); '
            "width: 8px; height: 4px; }\n"
            'QToolButton[ctgMenu="true"]::menu-indicator, '
            'QToolButton[popupMode="2"]::menu-indicator { image: url("%(down)s"); }\n' % c
        )
    if dim:
        rules += 'QToolButton#RibbonCaption::menu-indicator { image: url("%(dim)s"); }\n' % c
    return rules


class _ChevronEngine(QtGui.QIconEngine):
    """The collapse / expand chevron, painted in the theme's text colour at
    the size Qt asks for."""

    def __init__(self, up: bool):
        super().__init__()
        self._up = up

    def _paint(self, p: QtGui.QPainter, rect: QtCore.QRect, mode) -> None:
        role = "disabled" if mode == QtGui.QIcon.Mode.Disabled else "text"
        pen = QtGui.QPen(color(role), max(1.2, rect.height() / 9.0))
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(pen)
        w, h = rect.width(), rect.height()
        cx, cy = rect.x() + w / 2.0, rect.y() + h / 2.0
        dx, dy = w * 0.28, h * 0.16
        s = -1 if self._up else 1
        p.drawPolyline(
            [
                QtCore.QPointF(cx - dx, cy - s * dy),
                QtCore.QPointF(cx, cy + s * dy),
                QtCore.QPointF(cx + dx, cy - s * dy),
            ]
        )
        p.restore()

    def paint(self, painter, rect, mode, state):
        self._paint(painter, rect, mode)

    def pixmap(self, size, mode, state):
        pm = QtGui.QPixmap(size)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        self._paint(p, QtCore.QRect(QtCore.QPoint(0, 0), size), mode)
        p.end()
        return pm

    def clone(self):
        return _ChevronEngine(self._up)


def chevron_icon(up: bool) -> QtGui.QIcon:
    return QtGui.QIcon(_ChevronEngine(up))


def announce(button: QtWidgets.QAbstractButton, action: QtGui.QAction, suffix: str = "") -> None:
    """Name ``button`` after ``action`` for screen readers, now and whenever
    the action changes (its text, tip, key or checked state), and give it the rich tool
    tip (ui/style/tips.py)."""

    def sync(b=button, a=action):
        try:
            face = b.defaultAction() if isinstance(b, QtWidgets.QToolButton) else None
            if face is not None and face is not a:
                return  # a split button showing another variant
            # set only what changed: every set is a NameChanged event for a
            # screen reader, and 'changed' also fires on each check / enable
            name = spoken(a.text() or a.iconText()) + suffix
            if b.accessibleName() != name:
                b.setAccessibleName(name)
            tip = spoken(a.toolTip()) if a.toolTip() else ""
            desc = (a.toolTip() if tip and tip != spoken(a.text()) else a.statusTip()) or ""
            key = tips.accessible_shortcut(a)
            if key:
                desc = ("%s. %s" % (desc.rstrip(". "), key)) if desc else key
            if b.accessibleDescription() != desc:
                b.setAccessibleDescription(desc)
            # a new label changes the group's width ('Undo mirror' in My Commands): the
            # group is measured again and the row laid out anew
            last, now = getattr(b, "_ctg_text", None), (id(a), a.text())
            b._ctg_text = now
            if last is not None and last[0] == now[0] and last[1] != now[1]:
                group = b.parentWidget()
                if isinstance(group, RibbonGroup):
                    group.contents_changed()
        except RuntimeError:  # the button is gone
            pass

    sync()
    tips.install(button)
    done = getattr(button, "_ctg_announced", None)
    if done is None:
        done = button._ctg_announced = set()
    if id(action) not in done:
        done.add(id(action))
        action.changed.connect(sync)


def _label_width(button: QtWidgets.QToolButton, texts) -> int:
    fm = button.fontMetrics()
    return max((fm.horizontalAdvance(spoken(t)) for t in texts if t), default=0)


class VariantButton(QtWidgets.QToolButton):
    """Split button whose face is the last-used variant (persisted). Its width fits the
    longest variant, so the ribbon does not move when the face changes."""

    def __init__(self, actions, settings_key: str, parent=None):
        super().__init__(parent)
        self._actions = list(actions)
        self._key = "ui/variant/" + settings_key
        #: a small split button (RibbonGroup.add_small_widget): its natural width
        self.small = False
        self.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        menu = QtWidgets.QMenu(self)
        for a in self._actions:
            menu.addAction(a)
        self.setMenu(menu)
        st = app_settings()
        last = st.value(self._key, "", type=str)
        face = next((a for a in self._actions if a.text() == last), self._actions[0])
        self.setDefaultAction(face)
        announce(self, face, ", more choices")
        menu.triggered.connect(self._remember)

    def _remember(self, action) -> None:
        self.setDefaultAction(action)
        announce(self, action, ", more choices")
        app_settings().setValue(self._key, action.text())
        self.fit_width()
        # (a face with a longer name is wider: the ribbon measures the group again)
        group = self.parentWidget()
        if isinstance(group, RibbonGroup):
            group.contents_changed()

    def fit_width(self) -> None:
        """The minimum width of the labelled face: its label + 14 px for the arrow. A small
        one (Rotate, Flip) keeps its natural width."""
        style = self.toolButtonStyle()
        if self.small:
            self.setMinimumWidth(0)
        elif style == QtCore.Qt.ToolButtonIconOnly:
            self.setMinimumWidth(px(36) + 12)
        elif style == QtCore.Qt.ToolButtonTextUnderIcon:
            face = self.defaultAction()
            label = _label_width(self, [face.text() if face is not None else ""])
            self.setMinimumWidth(max(px(56), label + 12 + 14))
        else:
            self.setMinimumWidth(0)


def _layout_widgets(layout: QtWidgets.QLayout) -> list:
    """Every widget of ``layout`` and its nested layouts, in order."""
    out = []
    for k in range(layout.count()):
        item = layout.itemAt(k)
        if item.widget() is not None:
            out.append(item.widget())
        elif item.layout() is not None:
            out.extend(_layout_widgets(item.layout()))
    return out


class RibbonGroup(QtWidgets.QFrame):
    """A group: big buttons, stacked small rows, fields and a caption that opens the
    group's full command list."""

    def __init__(self, caption: str, priority: int = 2, parent=None, extras=()):
        super().__init__(parent)
        self.setObjectName("RibbonGroup")
        self.setAccessibleName("%s group" % caption)
        self.caption = caption
        #: the name of the group's button when it is collapsed (default: its caption; another
        #: when a pinned button beside it already shows the caption)
        self.fold_caption = caption
        self.priority = priority
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(_sp(1), _sp(0), _sp(1), 0)
        outer.setSpacing(0)
        self._row = QtWidgets.QHBoxLayout()
        self._row.setSpacing(_sp(0))
        outer.addLayout(self._row, 1)
        # the caption: a flat button whose menu lists every command of the group
        self._caption_btn = QtWidgets.QToolButton(self)
        self._caption_btn.setObjectName("RibbonCaption")
        self._caption_btn.setText(caption)
        self._caption_btn.setAccessibleName("%s commands" % caption)
        self._caption_btn.setToolTip("Every command of the %s group" % caption)
        self._caption_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self._caption_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self._caption_menu = QtWidgets.QMenu(self._caption_btn)
        self._caption_menu.aboutToShow.connect(lambda: self._fill_menu(self._caption_menu, with_pinned=True))
        self._caption_btn.setMenu(self._caption_menu)
        outer.addWidget(self._caption_btn, 0, QtCore.Qt.AlignHCenter)
        self._all_actions: list[QtGui.QAction] = []
        #: more commands for the caption's menu (existing QActions of the menus)
        self.extras: list[QtGui.QAction] = list(extras or ())
        self._small_col: QtWidgets.QVBoxLayout | None = None
        self._small_count = 0
        self._field_col: QtWidgets.QVBoxLayout | None = None
        self._field_count = 0
        #: icon-only buttons (the step before collapsing to one pop-up)
        self.compact = False
        #: hidden in Settings > Ribbon (left out of the reflow)
        self.hidden_by_user = False
        #: a 1 px divider at the right edge (every visible group but the last)
        self.divider = False
        self._big: list = []  # big buttons, the split buttons among them
        self._smalls: list = []  # small buttons and small menu buttons
        self._toggles: list = []  # the buttons of the toggle rows
        self._fields: list = []  # (label or None, field widget)
        self._pinned: list = []  # stay labelled and visible when collapsed
        self._state = "full"
        self._folded: list = []  # the widgets the collapsed state hid
        self._watched: list = []  # the actions the pop-up button follows
        self._swatches: list = []  # colour buttons (add_swatch)
        self._galleries: list = []  # tile galleries (add_gallery)
        self._widths: Dict[str, int] = {}  # state -> width (RibbonBar measures)
        self._fold_btn: QtWidgets.QToolButton | None = None

    #: the group's widths changed after it was laid out (a split button's new face)
    widthsChanged = QtCore.Signal()

    # ---- building ------------------------------------------------------
    def _changed(self) -> None:
        self._widths.clear()

    def contents_changed(self) -> None:
        """Measure the group again and let the ribbon lay the row out anew."""
        self._widths.clear()
        self.layout().invalidate()
        self.widthsChanged.emit()

    def _new_small_slot(self) -> QtWidgets.QVBoxLayout:
        if self._small_col is None or self._small_count >= 3:
            self._small_col = QtWidgets.QVBoxLayout()
            self._small_col.setSpacing(0)
            self._small_col.setContentsMargins(0, 0, 0, 0)
            self._row.addLayout(self._small_col)
            self._small_count = 0
        self._small_count += 1
        return self._small_col

    def _style_big(self, b: QtWidgets.QToolButton) -> None:
        pinned = b in self._pinned
        icon_only = self.compact and not pinned
        b.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly if icon_only else QtCore.Qt.ToolButtonTextUnderIcon)
        b.setIconSize(QtCore.QSize(px(28), px(28)))
        b.setFixedHeight(big_height())
        if isinstance(b, VariantButton):
            b.fit_width()
        else:
            b.setMinimumWidth(px(36) if icon_only else px(56))

    def _style_small(self, b: QtWidgets.QToolButton) -> None:
        has_icon = not b.icon().isNull()
        b.setToolButtonStyle(
            QtCore.Qt.ToolButtonIconOnly
            if (self.compact and has_icon)
            else QtCore.Qt.ToolButtonTextBesideIcon
        )
        b.setIconSize(QtCore.QSize(px(14), px(14)))
        b.setFixedHeight(small_height())
        if isinstance(b, VariantButton):
            b.fit_width()

    def _style_toggle(self, b: QtWidgets.QToolButton) -> None:
        h = small_height()
        b.setIconSize(QtCore.QSize(px(14), px(14)))
        b.setFixedSize(h + px(4), h)

    def add_big(self, action: QtGui.QAction, pinned: bool = False) -> QtWidgets.QToolButton:
        """A big button. ``pinned``: it stays labelled and visible when the group folds
        into one pop-up (the Select and Symbol tools)."""
        b = QtWidgets.QToolButton(self)
        b.setDefaultAction(action)
        announce(b, action)
        if pinned:
            self._pinned.append(b)
        self._big.append(b)
        self._style_big(b)
        self._row.addWidget(b)
        self._small_col = None
        self._all_actions.append(action)
        self._changed()
        return b

    def add_big_widget(self, w: QtWidgets.QWidget, pinned: bool = False) -> None:
        """A big split button (a VariantButton): it compacts like the big buttons."""
        if pinned:
            self._pinned.append(w)
        self._big.append(w)
        self._style_big(w)
        self._row.addWidget(w)
        self._small_col = None
        if hasattr(w, "_actions"):
            self._all_actions.extend(w._actions)
        self._changed()

    def add_small(self, action: QtGui.QAction) -> QtWidgets.QToolButton:
        col = self._new_small_slot()
        b = QtWidgets.QToolButton(self)
        b.setDefaultAction(action)
        announce(b, action)
        self._smalls.append(b)
        self._style_small(b)
        col.addWidget(b)
        self._all_actions.append(action)
        self._changed()
        return b

    def add_small_widget(self, w: QtWidgets.QToolButton) -> None:
        """A small split button (a VariantButton) in a row of the small column."""
        col = self._new_small_slot()
        if isinstance(w, VariantButton):
            w.small = True
        self._smalls.append(w)
        self._style_small(w)
        col.addWidget(w)
        if hasattr(w, "_actions"):
            self._all_actions.extend(w._actions)
        self._changed()

    def add_toggle_row(self, actions) -> list:
        """One row of icon-only buttons in the small column (Bold, Italic, Underline,
        Strikeout; the three justifications), the height of a small button."""
        col = self._new_small_slot()
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(1)
        out = []
        for a in actions:
            b = QtWidgets.QToolButton(self)
            b.setDefaultAction(a)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
            announce(b, a)
            self._toggles.append(b)
            self._style_toggle(b)
            row.addWidget(b)
            out.append(b)
            self._all_actions.append(a)
        row.addStretch(1)
        col.addLayout(row)
        self._changed()
        return out

    def add_field(self, label: str, widget: QtWidgets.QWidget) -> None:
        """Labelled inline control (combo/spinbox/checkbox) for the contextual
        SHAPE/TEXT/Select editors. Up to 3 stack in one column: a dim label
        beside a 20px control, matching the small-button rows."""
        if self._field_col is None or self._field_count >= 3:
            self._field_col = QtWidgets.QVBoxLayout()
            self._field_col.setSpacing(_sp(0))
            self._field_col.setContentsMargins(_sp(0), _sp(0), _sp(0), 0)
            self._row.addLayout(self._field_col)
            self._field_count = 0
        widget.setFixedHeight(small_height())
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(_sp(1))
        lb = None
        if label:
            from .style.theme import set_note_role

            lb = QtWidgets.QLabel(label)
            set_note_role(lb, "label", type_qss("caption"))
            lb.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            lb.setMinimumWidth(px(40))
            lb.setBuddy(widget)  # a screen reader names the field by it
            row.addWidget(lb)
        row.addWidget(widget, 1)
        self._field_col.addLayout(row)
        self._field_count += 1
        self._fields.append((lb, widget))
        self._small_col = None
        self._changed()

    def add_gallery(self, w: QtWidgets.QWidget) -> None:
        """A gallery of tiles (the named styles), centred in the group's height. Its tiles
        host their own commands; the commands a collapsed group lists are its ``extras``.
        A gallery with ``set_compact(on)`` shows fewer tiles while the group is compact."""
        self._galleries.append(w)
        self._row.addWidget(w, 0, QtCore.Qt.AlignVCenter)
        self._small_col = None
        self._changed()

    def add_swatch(self, button: QtWidgets.QToolButton) -> None:
        """A colour button (the colours of new objects), sized with the text size: square,
        or the size its ``ribbon_size()`` gives (the swatch pair)."""
        self._swatches.append(button)
        self._size_swatch(button)
        self._row.addWidget(button)
        self._small_col = None
        self._changed()

    @staticmethod
    def _size_swatch(button: QtWidgets.QToolButton) -> None:
        fn = getattr(button, "ribbon_size", None)
        if callable(fn):
            button.setFixedSize(*fn())
        else:
            button.setFixedSize(px(28), px(28))

    def add_small_menu(self, title: str, actions, icon_name: str = "") -> QtWidgets.QToolButton:
        """A small pop-up button. With ``icon_name`` it narrows to its icon when the group
        is compact (like the small buttons); without one it keeps its text."""
        b = QtWidgets.QToolButton(self)
        b.setText(title)
        b.setAccessibleName("%s menu" % spoken(title))
        b.setProperty("ctgMenu", True)
        b.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        if icon_name:
            from .style.theme import icon

            b.setIcon(icon(icon_name))
            b.setToolTip(title)
        menu = QtWidgets.QMenu(b)
        for a in actions:
            menu.addAction(a)
        b.setMenu(menu)
        self._smalls.append(b)
        self._style_small(b)
        self._new_small_slot().addWidget(b)
        self._all_actions.extend(actions)
        self._changed()
        return b

    # ---- the caption's menu ------------------------------------------
    def menu_actions(self, with_pinned: bool = True) -> list:
        """The group's commands for a menu: every hosted action once, in order, then the
        extras (None = a separator)."""
        pinned = {
            id(b.defaultAction())
            for b in self._pinned
            if isinstance(b, QtWidgets.QToolButton) and b.defaultAction() is not None
        }
        seen, out = set(), []
        for a in self._all_actions:
            if id(a) in seen or (not with_pinned and id(a) in pinned):
                continue
            seen.add(id(a))
            out.append(a)
        more = [a for a in self.extras if id(a) not in seen]
        if more:
            out.append(None)
            out.extend(more)
        return out

    def _fill_menu(self, menu: QtWidgets.QMenu, with_pinned: bool) -> None:
        menu.clear()
        for a in self.menu_actions(with_pinned):
            if a is None:
                menu.addSeparator()
            else:
                menu.addAction(a)

    # ---- states: full / compact / collapsed --------------------------
    def set_compact(self, on: bool) -> None:
        """Icon-only buttons (the names in their tool tips): a group narrows this way before
        it collapses into one pop-up button. A small button without an icon and a pinned
        button keep their text."""
        self.apply_state("compact" if on else ("collapsed" if self.collapsed else "full"))

    def set_collapsed(self, on: bool) -> None:
        self.apply_state("collapsed" if on else ("compact" if self.compact else "full"))

    @property
    def state(self) -> str:
        return self._state

    def apply_state(self, state: str) -> None:
        if state not in STATES:
            raise ValueError(state)
        compact = state != "full"
        if compact != self.compact:
            self.compact = compact
            self._restyle()
        prev, self._state = self._state, state
        if state == "collapsed" and prev != "collapsed":
            self._fold()
        elif state != "collapsed" and prev == "collapsed":
            self._unfold()
        self._caption_btn.setVisible(state != "collapsed" and bool(density("captions")))
        lay = self.layout()
        lay.invalidate()
        lay.activate()
        self.updateGeometry()

    def _restyle(self) -> None:
        for b in self._big:
            self._style_big(b)
        for b in self._smalls:
            self._style_small(b)
        for g in self._galleries:
            fn = getattr(g, "set_compact", None)
            if callable(fn):
                fn(self.compact)
        for b in self._big + self._smalls:
            b.updateGeometry()

    def _make_fold_button(self) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton(self)
        b.setObjectName("RibbonFold")
        b.setText(self.fold_caption)
        b.setProperty("ctgMenu", True)
        b.setAccessibleName(self.fold_caption)
        b.setAccessibleDescription("The %s commands" % self.caption)
        b.setToolTip("The %s commands" % self.caption)
        b.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        b.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        # the face: the group's first big command's icon above the caption
        first = next((x for x in self._big if x not in self._pinned), None)
        act = first.defaultAction() if isinstance(first, QtWidgets.QToolButton) else None
        if act is None:
            # (never a pinned tool's icon: it stays shown beside the fold button)
            pinned = {
                id(p.defaultAction())
                for p in self._pinned
                if isinstance(p, QtWidgets.QToolButton) and p.defaultAction() is not None
            }
            act = next((a for a in self._all_actions if id(a) not in pinned and not a.icon().isNull()), None)
        if act is not None and not act.icon().isNull():
            b.setIcon(act.icon())
        menu = QtWidgets.QMenu(b)
        menu.aboutToShow.connect(lambda m=menu: self._fill_menu(m, with_pinned=False))
        self._fill_menu(menu, with_pinned=False)
        b.setMenu(menu)
        b.hide()
        return b

    def _style_fold(self) -> None:
        b = self._fold_btn
        if b is None:
            return
        b.setIconSize(QtCore.QSize(px(28), px(28)))
        b.setFixedHeight(big_height())
        b.setMinimumWidth(px(48))

    def _fold(self) -> None:
        if self._fold_btn is None:
            self._fold_btn = self._make_fold_button()
            self._row.addWidget(self._fold_btn)
        self._style_fold()
        self._folded = []
        for w in _layout_widgets(self._row):
            if w is self._fold_btn or w in self._pinned:
                continue
            if w.isVisibleTo(self):
                w.hide()
                self._folded.append(w)
        self._fold_btn.show()
        # (the button is greyed while none of its commands can run)
        self._watched = list({id(a): a for a in self._all_actions}.values())
        for a in self._watched:
            a.changed.connect(self._sync_collapsed)
        self._sync_collapsed()

    def _unfold(self) -> None:
        for a in getattr(self, "_watched", ()):
            try:
                a.changed.disconnect(self._sync_collapsed)
            except RuntimeError, TypeError:  # swallow-ok: an action deleted since
                pass
        self._watched = []
        if self._fold_btn is not None:
            self._fold_btn.hide()
        for w in self._folded:
            try:
                w.show()
            except RuntimeError:  # swallow-ok: a widget deleted since
                pass
        self._folded = []

    def _sync_collapsed(self) -> None:
        b = self._fold_btn
        if b is None or self._state != "collapsed":
            return
        on = False
        for a in self._all_actions:
            try:
                if a.isEnabled() and a.isVisible() and not a.isSeparator():
                    on = True
                    break
            except RuntimeError:
                continue
        try:
            b.setEnabled(on)
        except RuntimeError:
            pass

    @property
    def collapsed(self) -> bool:
        return self._state == "collapsed"

    @property
    def _collapsed_btn(self) -> Optional[QtWidgets.QToolButton]:
        """The pop-up button of the collapsed group (None while it is not collapsed)."""
        return self._fold_btn if self._state == "collapsed" else None

    # ---- widths ---------------------------------------------------------
    def widths(self) -> Dict[str, int]:
        """The group's width in each state (measured once, cached until the theme, the
        text size, the density or the group's contents change)."""
        if len(self._widths) != len(STATES):
            keep = self._state
            for s in STATES:
                self.apply_state(s)
                self._widths[s] = self.sizeHint().width()
            self.apply_state(keep)
        return dict(self._widths)

    def apply_metrics(self) -> None:
        """Sizes again at the current text size and density (Settings > Appearance)."""
        self._restyle()
        for b in self._toggles:
            self._style_toggle(b)
        for _lb, w in self._fields:
            w.setFixedHeight(small_height())
        for b in self._swatches:
            self._size_swatch(b)
        self._style_fold()
        self._caption_btn.setVisible(self._state != "collapsed" and bool(density("captions")))
        self._widths.clear()
        self.layout().invalidate()
        self.updateGeometry()

    def paintEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().paintEvent(e)
        if self.divider:
            p = QtGui.QPainter(self)
            p.setPen(QtGui.QPen(color("border"), 1))
            x = self.width() - 1
            p.drawLine(x, 6, x, self.height() - 6)
            p.end()


def plan_layout(avail: int, entries: Sequence[tuple], gap: int = GROUP_GAP) -> List[str]:
    """The state of each ribbon group for a given width."""
    n = len(entries)
    states = ["full"] * n
    if not n:
        return states
    widths = [dict(w) for _p, w in entries]

    def total() -> int:
        return sum(widths[k][states[k]] for k in range(n)) + gap * (n - 1)

    order = sorted(range(n), key=lambda k: (-entries[k][0], -k))
    for step in ("compact", "collapsed"):
        for k in order:
            if total() <= avail:
                return states
            if widths[k][step] < widths[k][states[k]]:
                states[k] = step
    return states


class RibbonTabBar(QtWidgets.QTabBar):
    """The ribbon's tab row. The contextual tabs (set_context_tabs) are painted over the
    base paint: a 2 px accent bar on top and the text in the accent colour (a stylesheet
    colour wins over setTabTextColor), the selected one on an accent tint, and a 'Selection'
    caption over the run when two or more show."""

    CAPTION = "Selection"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RibbonTabs")
        self._context: set = set()

    def set_context_indexes(self, indexes) -> None:
        new = {int(i) for i in indexes}
        if new != self._context:
            self._context = new
            self.update()

    def context_indexes(self) -> set:
        return set(self._context)

    def paintEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().paintEvent(e)
        idx = sorted(i for i in self._context if 0 <= i < self.count())
        if not idx:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        accent = color("accent")
        top = 3  # (the tab's margin and border)
        for i in idx:
            r = self.tabRect(i)
            body = r.adjusted(1, top, -1, 0)
            sel = i == self.currentIndex()
            p.fillRect(body, color("panel") if sel else color("bg"))
            if sel:
                tint = QtGui.QColor(accent)
                tint.setAlpha(40)
                p.fillRect(body, tint)
            p.fillRect(QtCore.QRect(body.left(), body.top(), body.width(), 2), accent)
            p.setPen(accent)
            p.setFont(self.font())
            p.drawText(body.adjusted(0, 2, 0, 0), QtCore.Qt.AlignCenter, self.tabText(i).replace("&", ""))
        if len(idx) >= 2:
            # one bar over the run and its caption on it (a legend: no room above the tabs)
            run = self.tabRect(idx[0]).united(self.tabRect(idx[-1])).adjusted(1, top, -1, 0)
            p.fillRect(QtCore.QRect(run.left(), run.top(), run.width(), 2), accent)
            f = QtGui.QFont(self.font())
            f.setPixelSize(max(7, px(9)))
            p.setFont(f)
            fm = QtGui.QFontMetrics(f)
            w = fm.horizontalAdvance(self.CAPTION) + 8
            box = QtCore.QRect(run.center().x() - w // 2, 0, w, fm.height())
            # the caption cuts the bar; each part of its box keeps the background under it
            # (the selected tab's tint stays whole)
            p.fillRect(box, color("bg"))
            for i in idx:
                body = self.tabRect(i).adjusted(1, top, -1, 0)
                part = box.intersected(body)
                if part.isEmpty():
                    continue
                if i == self.currentIndex():
                    p.fillRect(part, color("panel"))
                    tint = QtGui.QColor(accent)
                    tint.setAlpha(40)
                    p.fillRect(part, tint)
            p.setPen(color("text_dim"))
            p.drawText(box, QtCore.Qt.AlignCenter, self.CAPTION)
        p.end()


class _Flyout(QtWidgets.QFrame):
    """The collapsed ribbon's commands shown over the page (a frameless child of the main
    window, under the tab row, the ribbon's width, with a shadow)."""

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setObjectName("RibbonFlyout")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        dark = color("shadow")
        dark.setAlpha(110)
        shadow.setColor(dark)
        self.setGraphicsEffect(shadow)
        self.hide()


class RibbonBar(QtWidgets.QWidget):
    """Full ribbon: header (QAT + tabs + utility) over stacked pages."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RibbonBar")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self._pages: list[QtWidgets.QWidget] = []
        self._groups: dict[int, list[RibbonGroup]] = {}
        self._tab_map: list[int] = []  # static tabbar idx -> stack idx
        self._context_pages: dict[str, int] = {}  # title -> stack idx
        self._prev_static: int | None = None
        self._last_static = 0  # the static tab shown last (the fallback)
        self._syncing = False
        self._last_layout = None  # (stack index, width, generation) of the last layout
        self._generation = 0  # bumped when a group's widths or visibility change
        self._empty: dict = {}  # page -> (placeholder widget, shown?)
        self._qat_buttons: list = []
        self._utility_buttons: list = []
        self._flyout: _Flyout | None = None
        self._flyout_hooks: list = []
        self._collapse_action: QtGui.QAction | None = None

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 1)
        outer.setSpacing(0)
        self._header = QtWidgets.QWidget(self)
        self._header.setObjectName("RibbonHeader")
        self._header.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        header = QtWidgets.QHBoxLayout(self._header)
        header.setContentsMargins(_sp(1), 0, _sp(1), 0)
        header.setSpacing(_sp(0))
        qat = QtWidgets.QWidget(self._header)
        qat.setObjectName("RibbonQat")
        self._qat = QtWidgets.QHBoxLayout(qat)
        self._qat.setContentsMargins(0, 0, _sp(1), 0)
        self._qat.setSpacing(0)
        header.addWidget(qat)
        self._tabs = RibbonTabBar(self._header)
        self._tabs.setAccessibleName("Ribbon tabs")
        self._tabs.setDocumentMode(True)
        self._tabs.setExpanding(False)
        self._tabs.setDrawBase(False)
        header.addWidget(self._tabs, 0, QtCore.Qt.AlignBottom)
        header.addStretch(1)
        self._utility = QtWidgets.QHBoxLayout()
        self._utility.setSpacing(_sp(0))
        header.addLayout(self._utility)
        self._chevron = QtWidgets.QToolButton(self._header)
        self._chevron.setObjectName("RibbonChevron")
        self._chevron.setIcon(chevron_icon(True))
        self._chevron.setAutoRaise(True)
        self._chevron.setToolTip("Show or hide the ribbon commands")
        self._chevron.clicked.connect(self._chevron_clicked)
        header.addWidget(self._chevron)
        outer.addWidget(self._header)
        self._stack = QtWidgets.QStackedWidget()
        self._stack.setObjectName("RibbonBody")
        # a QStackedWidget's minimum is the widest of ALL its pages at full
        # expansion (it would set the WINDOW minimum). Ignored lets it shrink;
        # the priority overflow (_reflow) collapses groups instead.
        self._stack.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        outer.addWidget(self._stack, 1)
        self._tabs.currentChanged.connect(self._on_tab)
        self._tabs.tabBarClicked.connect(self._on_tab_clicked)
        self._tabs.tabBarDoubleClicked.connect(self._on_tab_double_clicked)

        st = app_settings()
        self._collapsed = st.value("ui/ribbon_collapsed", False, type=bool)
        self.setStyleSheet(ribbon_qss())
        self._apply_height()

    # ---- structure -----------------------------------------------------
    def add_qat(self, action: QtGui.QAction) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton(self)
        b.setDefaultAction(action)
        announce(b, action)
        b.setAutoRaise(True)
        self._style_qat(b)
        self._qat.addWidget(b)
        self._qat_buttons.append(b)
        return b

    def add_qat_split(self, action: QtGui.QAction, menu: QtWidgets.QMenu) -> tuple:
        """A Quick Access command with a menu of related ones: the command's button and a
        narrow arrow button beside it that opens ``menu`` (the arrow never sits on the
        icon)."""
        b = self.add_qat(action)
        arrow = QtWidgets.QToolButton(self)
        arrow.setObjectName("QatArrow")
        arrow.setAutoRaise(True)
        arrow.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        arrow.setIcon(chevron_icon(False))
        arrow.setAccessibleName("More %s commands" % spoken(action.text()).lower())
        arrow.setToolTip("More %s commands" % spoken(action.text()).lower())
        menu.setParent(arrow, menu.windowFlags())
        arrow.setMenu(menu)
        self._style_qat(arrow)
        self._qat.addWidget(arrow)
        self._qat_buttons.append(arrow)
        return b, arrow

    def clear_qat(self) -> None:
        """Take every Quick Access button away (the row is built again from Settings >
        Ribbon > Quick Access: ui/prefs/window_options.build_quick_access)."""
        while self._qat.count():
            item = self._qat.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._qat_buttons = []

    def qat_actions(self) -> list:
        """The commands of the Quick Access row, in order (an arrow's menu is not one)."""
        out = []
        for b in self._qat_buttons:
            a = b.defaultAction() if isinstance(b, QtWidgets.QToolButton) else None
            if a is not None:
                out.append(a)
        return out

    def _style_qat(self, b: QtWidgets.QToolButton) -> None:
        n = px(density("qat_icon"))
        if b.objectName() == "QatArrow":
            b.setIconSize(QtCore.QSize(px(8), px(8)))
            b.setFixedWidth(px(12))
        else:
            b.setIconSize(QtCore.QSize(n, n))

    def add_utility(self, w: QtWidgets.QWidget) -> None:
        self._utility.addWidget(w)
        if isinstance(w, QtWidgets.QToolButton):
            if w.defaultAction() is not None:
                announce(w, w.defaultAction())
            self._utility_buttons.append(w)

    def _new_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        page.setObjectName("RibbonPage")
        page.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        lay = QtWidgets.QHBoxLayout(page)
        lay.setContentsMargins(_sp(1), 0, _sp(1), 0)
        lay.setSpacing(GROUP_GAP)
        lay.addStretch(1)
        return page

    def add_tab(self, title: str) -> QtWidgets.QWidget:
        page = self._new_page()
        idx = self._stack.addWidget(page)
        self._tabs.addTab(title)
        self._tab_map.append(idx)
        self._pages.append(page)
        self._groups[idx] = []
        return page

    # ---- contextual tabs -------------------------------
    def add_context_tab(self, title: str) -> QtWidgets.QWidget:
        """Page that lives in the stack but appears in the tab bar only while
        its trait is active (set_context_tabs)."""
        page = self._new_page()
        idx = self._stack.addWidget(page)
        self._pages.append(page)
        self._groups[idx] = []
        self._context_pages[title] = idx
        return page

    def set_context_tabs(self, titles, activate: bool = False) -> None:
        """Show the contextual tabs in ``titles``."""
        if self._collapsed:
            activate = False
        want = [t for t in self._context_pages if t in set(titles)]
        have = {self._tabs.tabText(i): i for i in range(len(self._tab_map), self._tabs.count())}
        if list(have) == want and not activate:
            return
        n_static = len(self._tab_map)
        cur_i = self._tabs.currentIndex()
        cur_text = self._tabs.tabText(cur_i)
        on_context = cur_i >= n_static
        self._syncing = True
        try:
            # drop stale context tabs (right-to-left)
            for title, ti in sorted(have.items(), key=lambda kv: -kv[1]):
                if title not in want:
                    self._tabs.removeTab(ti)
            # append missing
            present = {self._tabs.tabText(i) for i in range(n_static, self._tabs.count())}
            for title in want:
                if title not in present:
                    self._tabs.addTab(title)
            self._tabs.set_context_indexes(range(n_static, self._tabs.count()))

            def index_of(text):
                for i in range(self._tabs.count()):
                    if self._tabs.tabText(i) == text:
                        return i
                return -1

            if activate and want:
                if self._prev_static is None:
                    self._prev_static = cur_i if not on_context else self._last_static
                self._tabs.setCurrentIndex(index_of(want[0]))
            elif on_context:
                if cur_text in want:
                    self._tabs.setCurrentIndex(index_of(cur_text))
                elif want:
                    # one contextual tab replaced by another (a text, then a genie)
                    self._tabs.setCurrentIndex(index_of(want[0]))
                else:
                    back = self._prev_static if self._prev_static is not None else self._last_static
                    self._tabs.setCurrentIndex(back if 0 <= back < n_static else 0)
                    self._prev_static = None
            elif 0 <= cur_i < n_static:
                self._tabs.setCurrentIndex(cur_i)
        finally:
            self._syncing = False
        self._on_tab(self._tabs.currentIndex(), persist=False)

    def add_group(self, page: QtWidgets.QWidget, caption: str, priority: int = 2, extras=()) -> RibbonGroup:
        """A group at the end of ``page``. ``extras``: more existing commands for the
        caption's menu (the group's own commands are always in it)."""
        g = RibbonGroup(caption, priority, page, extras=extras)
        lay = page.layout()
        lay.insertWidget(lay.count() - 1, g)  # before the stretch
        idx = self._pages.index(page)
        self._groups[idx].append(g)
        self._generation += 1
        g.widthsChanged.connect(self._group_changed)
        state = self._empty.get(page)
        if state is not None and state[1]:
            g.hide()
        return g

    def _group_changed(self) -> None:
        self._generation += 1
        self._reflow()

    def remove_group(self, g: RibbonGroup) -> None:
        """Take a group off its tab (Settings > Ribbon's own group, built again)."""
        for idx, groups in self._groups.items():
            if g in groups:
                groups.remove(g)
                if g.collapsed:
                    g.set_collapsed(False)
                page = self._pages[idx]
                page.layout().removeWidget(g)
                g.hide()
                g.deleteLater()
                self._generation += 1
                break

    # ---- the empty state (no page open) --------------------------------
    def set_empty_state(self, page: QtWidgets.QWidget, text: str, actions) -> QtWidgets.QWidget:
        """What ``page`` shows instead of its groups while there is nothing to work on: a
        line of text and buttons for ``actions`` (show_empty switches)."""
        w = QtWidgets.QWidget(page)
        w.setObjectName("RibbonEmpty")
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(px(12), 0, 0, 0)
        lay.setSpacing(px(8))
        if text:
            lab = QtWidgets.QLabel(text, w)
            lab.setObjectName("RibbonEmptyText")
            lay.addWidget(lab)
        for a in actions:
            b = QtWidgets.QToolButton(w)
            b.setDefaultAction(a)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            b.setIconSize(QtCore.QSize(px(20), px(20)))
            b.setAutoRaise(False)
            announce(b, a)
            lay.addWidget(b)
        lay.addStretch(1)
        page.layout().insertWidget(0, w)
        w.hide()
        self._empty[page] = (w, False)
        return w

    def show_empty(self, page: QtWidgets.QWidget, on: bool) -> None:
        state = self._empty.get(page)
        if state is None or state[1] == bool(on):
            return
        w = state[0]
        self._empty[page] = (w, bool(on))
        idx = self._pages.index(page)
        for g in self._groups.get(idx, []):
            g.setVisible(not on and not g.hidden_by_user)
        w.setVisible(bool(on))
        self._generation += 1
        self._reflow()

    def empty_shown(self, page: QtWidgets.QWidget) -> bool:
        state = self._empty.get(page)
        return bool(state and state[1])

    # ---- Settings > Ribbon ------------------------------------------------
    def tab_titles(self) -> list:
        """The titles of the tabs, static ones first, then the ones that come and go."""
        out = [self._tabs.tabText(k) for k in range(len(self._tab_map))]
        out += [t for t in self._context_pages if t not in out]
        return out

    def page_of(self, title: str):
        """The page widget of tab ``title`` (None when there is none)."""
        for k in range(len(self._tab_map)):
            if self._tabs.tabText(k) == title:
                return self._pages[self._tab_map[k]]
        idx = self._context_pages.get(title)
        return self._pages[idx] if idx is not None else None

    def groups_of(self, title: str) -> list:
        """The groups of tab ``title`` in order."""
        page = self.page_of(title)
        if page is None:
            return []
        return list(self._groups.get(self._pages.index(page), []))

    def group_names(self) -> list:
        """Every (tab title, group caption) the ribbon has, in order."""
        return [(t, g.caption) for t in self.tab_titles() for g in self.groups_of(t)]

    def set_group_hidden(self, title: str, caption: str, hidden: bool) -> bool:
        """Hide or show a group by the user's choice (Settings > Ribbon). True when the
        group exists."""
        page = self.page_of(title)
        for g in self.groups_of(title):
            if g.caption != caption:
                continue
            hidden = bool(hidden)
            if g.hidden_by_user == hidden:
                return True
            if hidden and g.collapsed:
                g.set_collapsed(False)
            g.hidden_by_user = hidden
            g.setVisible(not hidden and not self.empty_shown(page))
            self._generation += 1
            self._reflow()
            return True
        return False

    # ---- behaviour ------------------------------------------------------
    def _on_tab(self, idx: int, persist: bool = True) -> None:
        if idx < 0:
            return
        if idx < len(self._tab_map):
            stack_idx = self._tab_map[idx]
            self._last_static = idx
            if persist and not getattr(self, "_syncing", False):
                app_settings().setValue("ui/ribbon_tab", idx)
        else:
            stack_idx = self._context_pages.get(self._tabs.tabText(idx), 0)
        if 0 <= stack_idx < self._stack.count():
            self._stack.setCurrentIndex(stack_idx)
            self._reflow()

    # ---- collapse and the flyout ---------------------------------------
    def collapsed(self) -> bool:
        return bool(self._collapsed)

    def set_collapse_action(self, action: QtGui.QAction) -> None:
        """The View menu's 'Collapse the Ribbon' (checkable): the chevron hosts it."""
        self._collapse_action = action
        action.setCheckable(True)
        action.setChecked(self._collapsed)
        action.toggled.connect(self.set_collapsed)
        self._chevron.setDefaultAction(action)
        tips.install(self._chevron)
        self._apply_height()

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, on: bool) -> None:
        on = bool(on)
        self.close_flyout()
        if on == self._collapsed:
            self._sync_collapse_action()
            return
        self._collapsed = on
        app_settings().setValue("ui/ribbon_collapsed", self._collapsed)
        self._apply_height()
        self._reflow()

    def _sync_collapse_action(self) -> None:
        a = self._collapse_action
        if a is not None and a.isChecked() != self._collapsed:
            a.blockSignals(True)
            a.setChecked(self._collapsed)
            a.blockSignals(False)

    def _chevron_clicked(self) -> None:
        if self._collapse_action is None:  # (the action triggers it otherwise)
            self.toggle_collapsed()

    def _on_tab_clicked(self, idx: int) -> None:
        if not self._collapsed or idx < 0:
            return
        if self.flyout_open() and idx == self._tabs.currentIndex():
            self.close_flyout()
            return
        # (after the tab bar made it current)
        QtCore.QTimer.singleShot(0, self, self.open_flyout)

    def _on_tab_double_clicked(self, _idx: int) -> None:
        self.toggle_collapsed()

    def flyout_open(self) -> bool:
        return self._flyout is not None and self._flyout.isVisible()

    def open_flyout(self) -> None:
        """Show the current tab's commands over the page (collapsed ribbon only)."""
        if not self._collapsed:
            return
        win = self.window()
        if self._flyout is None or self._flyout.parentWidget() is not win:
            self._flyout = _Flyout(win)
        fly = self._flyout
        if self._stack.parentWidget() is not fly:
            fly.layout().addWidget(self._stack)
        fly.setStyleSheet(ribbon_qss())
        self._stack.show()
        top_left = self.mapTo(win, QtCore.QPoint(0, self.height()))
        fly.setGeometry(
            top_left.x(), top_left.y(), self.width(), self.expanded_height() - self.collapsed_height()
        )
        fly.show()
        fly.raise_()
        self._last_layout = None
        self._reflow()
        if not self._flyout_hooks:
            app = QtWidgets.QApplication.instance()
            app.installEventFilter(self)
            self._flyout_hooks.append(("filter", app))
        self._hook_flyout_buttons()

    def _hook_flyout_buttons(self) -> None:
        """While the flyout is open, a command run from any of its buttons closes it: the
        buttons there now, and those made since it opened (the fold buttons of a tab first
        shown or of a group folded inside it)."""
        if not self._flyout_hooks:
            return
        hooked = {id(obj) for kind, obj in self._flyout_hooks if kind == "button"}
        for b in self._stack.findChildren(QtWidgets.QToolButton):
            if id(b) not in hooked:
                b.triggered.connect(self._command_ran)
                self._flyout_hooks.append(("button", b))

    def close_flyout(self) -> None:
        fly = self._flyout
        for kind, obj in self._flyout_hooks:
            try:
                if kind == "filter":
                    obj.removeEventFilter(self)
                else:
                    obj.triggered.disconnect(self._command_ran)
            except RuntimeError, TypeError:  # swallow-ok: a button deleted since
                pass
        self._flyout_hooks = []
        if fly is None or self._stack.parentWidget() is self:
            return
        fly.hide()
        self.layout().addWidget(self._stack, 1)
        self._stack.setVisible(not self._collapsed)
        self._last_layout = None
        self._reflow()

    def _command_ran(self, *_a) -> None:
        # (after the command: the flyout's buttons must finish their click first)
        QtCore.QTimer.singleShot(0, self, self.close_flyout)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        """While the flyout is open: Esc closes it; a click outside it (and outside the
        tab row and the menus it opened) closes it and goes on to where it was aimed."""
        t = ev.type()
        if t == QtCore.QEvent.Type.KeyPress and ev.key() == QtCore.Qt.Key_Escape:
            # (only in the editor's own window: a dialog a flyout command opened, or a
            # menu, gets its own Esc)
            if (
                QtWidgets.QApplication.activePopupWidget() is not None
                or QtWidgets.QApplication.activeModalWidget() is not None
                or not isinstance(obj, QtWidgets.QWidget)
                or obj.window() is not self.window()
            ):
                return False
            self.close_flyout()
            return True
        if t == QtCore.QEvent.Type.MouseButtonPress and isinstance(obj, QtWidgets.QWidget):
            fly = self._flyout
            if fly is None or isinstance(obj, QtWidgets.QMenu) or obj.window() is not self.window():
                return False
            if obj is fly or fly.isAncestorOf(obj) or obj is self._tabs or self._tabs.isAncestorOf(obj):
                return False
            self.close_flyout()
        return False

    # ---- heights, metrics, theme ---------------------------------------
    @staticmethod
    def expanded_height() -> int:
        """The ribbon's height with its commands shown, at the UI text size."""
        return px(density("ribbon_h"))

    @staticmethod
    def collapsed_height() -> int:
        return px(COLLAPSED_H)

    def _apply_height(self) -> None:
        if self._stack.parentWidget() is self:
            self._stack.setVisible(not self._collapsed)
        self.setFixedHeight(self.collapsed_height() if self._collapsed else self.expanded_height())
        self._header.setFixedHeight(self.collapsed_height())
        self._chevron.setIconSize(QtCore.QSize(px(14), px(14)))
        ic = chevron_icon(self._collapsed is False)
        a = self._collapse_action
        if a is not None:
            a.setIcon(ic)
            self._sync_collapse_action()
        self._chevron.setIcon(ic)
        self._chevron.setAccessibleName("Expand the ribbon" if self._collapsed else "Collapse the ribbon")
        self._chevron.setToolTip(
            "Show the ribbon commands (Ctrl+F1)" if self._collapsed else "Show only the ribbon tabs (Ctrl+F1)"
        )
        # (the rich tip says what a click does now, not the menu command's name)
        self._chevron.setProperty(
            tips.TIP_NAME_PROP, "Expand the Ribbon" if self._collapsed else "Collapse the Ribbon"
        )
        self._chevron.setProperty(
            tips.TIP_TEXT_PROP,
            "Show the ribbon commands again."
            if self._collapsed
            else "Show only the ribbon's tabs. A click on a tab shows its commands over the page.",
        )

    def apply_metrics(self) -> None:
        """Every size again at the current text size and density, the widths measured anew
        and the row laid out (Settings > Appearance applies live)."""
        for groups in self._groups.values():
            for g in groups:
                g.apply_metrics()
        for b in self._qat_buttons:
            self._style_qat(b)
        for b in self._utility_buttons:
            b.setIconSize(QtCore.QSize(px(16), px(16)))
        self._apply_height()
        self._generation += 1
        self._last_layout = None
        self._reflow()

    def _theme_changed(self) -> None:
        self.setStyleSheet(ribbon_qss())
        if self._flyout is not None:
            self._flyout.setStyleSheet(ribbon_qss())
        self.apply_metrics()

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(320, super().minimumSizeHint().height())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.flyout_open():
            fly = self._flyout
            fly.resize(self.width(), fly.height())
        self._reflow()

    def layout_states(self, idx: Optional[int] = None) -> list:
        """(caption, state) of every visible group of the current (or ``idx``) page."""
        idx = self._stack.currentIndex() if idx is None else idx
        return [(g.caption, g.state) for g in self._groups.get(idx, []) if not g.hidden_by_user]

    def _reflow(self) -> None:
        """Lay the current page out for the ribbon's width (plan_layout): the same width
        always gives the same groups; the layout of the last width is kept while the
        ribbon grows by less than 24 px."""
        idx = self._stack.currentIndex()
        page = self._pages[idx] if 0 <= idx < len(self._pages) else None
        groups = [g for g in self._groups.get(idx, []) if not g.hidden_by_user]
        if not groups or (page is not None and self.empty_shown(page)):
            self._last_layout = None
            return
        width = self.width()
        key = (idx, self._generation)
        last = self._last_layout
        if last is not None and last[0] == key and 0 <= width - last[1] < _OVERFLOW_HYSTERESIS:
            return
        lay = page.layout() if page is not None else None
        margins = lay.contentsMargins() if lay is not None else QtCore.QMargins(4, 0, 4, 0)
        # (the stretch after the last group is one more gap)
        avail = width - margins.left() - margins.right() - GROUP_GAP
        states = plan_layout(avail, [(g.priority, g.widths()) for g in groups])
        for g, s in zip(groups, states):
            if g.state != s:
                g.apply_state(s)
        # (measuring and folding make fold buttons: an open flyout hooks them too)
        self._hook_flyout_buttons()
        for k, g in enumerate(groups):
            want = k < len(groups) - 1
            if g.divider != want:
                g.divider = want
                g.update()
        self._last_layout = (key, width)

    def restore_tab(self) -> None:
        st = app_settings()
        idx = st.value("ui/ribbon_tab", 0, type=int)
        if 0 <= idx < self._tabs.count():
            self._tabs.setCurrentIndex(idx)
