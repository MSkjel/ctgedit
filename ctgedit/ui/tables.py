"""Resizable column headers for tables and trees."""

from __future__ import annotations

import html
from typing import Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..infra import caches as _caches
from .prefs.settings import app_settings, whole

# Settings > Appearance > Tables (apply_settings reads them, the Settings window's apply
# hooks call it)
#: a fitted column is never wider than this (the user can drag it wider)
MAX_FIT_WIDTH = 360
#: the main-text column never shrinks below this when the window gets narrower
MIN_STRETCH_WIDTH = 60
#: in a fit_view table, a column that gives way never gets narrower than this
MIN_SQUEEZED_WIDTH = 36
#: in a fit_view table, a fitted column gets at most this share of the view's width
FIT_SHARE = 0.35
#: in a fit_view table, the main column keeps at least this share of the view's width
MAIN_SHARE = 0.33
#: a fit of a text column measures at most this many rows
FIT_SAMPLE = 1000
#: the settings: key -> (its span, the module value it sets, True = a share in percent)
SETTINGS = {
    "tables/max_fit_width": ((100, 2000), "MAX_FIT_WIDTH", False),
    "tables/fit_share": ((10, 90), "FIT_SHARE", True),
    "tables/main_share": ((10, 80), "MAIN_SHARE", True),
    "tables/min_main_width": ((20, 400), "MIN_STRETCH_WIDTH", False),
    "tables/min_squeezed_width": ((16, 200), "MIN_SQUEEZED_WIDTH", False),
    "tables/fit_sample": ((100, 100000), "FIT_SAMPLE", False),
}
#: the settings were read into the module values (once per process; the tests reset it)
_SETTINGS_READ = False
SETTINGS_PREFIX = "columns/"
#: the attribute that marks a view set up here (the UI test looks for it)
ATTR = "_ctg_columns"
#: the header menu's item (and its tool tip)
FIT_TEXT = "Fit Columns to Contents"
FIT_TIP = "Fit every column to its text again and forget the widths you set"


def header_of(view) -> Optional[QtWidgets.QHeaderView]:
    if isinstance(view, QtWidgets.QTableView):
        return view.horizontalHeader()
    if isinstance(view, QtWidgets.QTreeView):
        return view.header()
    return None


def settings_key(key: str) -> str:
    return SETTINGS_PREFIX + key.strip("/")


def _safe(fn) -> None:
    """Run a deferred step; a view deleted meanwhile is not an error."""
    try:
        fn()
    except RuntimeError:  # swallow-ok: the view was deleted before the step ran
        pass


def parse_widths(raw) -> Optional[Tuple[List[int], int]]:
    """A ``columns/<key>`` value: (the width of each logical section, 0 = hidden then;
    the view's width then), or None. A value without the view's width is None (it may hold
    Qt's stretch of the last section), so the table fits afresh and the next width the user
    sets is saved."""
    if not raw:
        return None
    if isinstance(raw, (list, tuple)):  # (QSettings reads 'a,b' as a list)
        raw = ",".join(str(x) for x in raw)
    widths: List[int] = []
    view_w = None
    try:
        for tok in str(raw).split(","):
            tok = tok.strip()
            if not tok:
                continue
            if tok[0] in "vV":
                view_w = int(tok[1:])
            else:
                widths.append(int(tok))
    except ValueError:
        return None
    return (widths, view_w) if widths and view_w else None


def format_widths(widths, view_w: int) -> str:
    return ",".join(str(int(w)) for w in widths) + ",v%d" % int(view_w)


# -- the settings ------------------------------------------------------------------------
def read_settings() -> None:
    """Settings > Appearance > Tables into the module values (a number outside its span
    reads as its default), and the Settings window's apply hooks registered."""
    global _SETTINGS_READ
    g = globals()
    for key, (span, name, percent) in SETTINGS.items():
        v = whole(key, span)
        g[name] = v / 100.0 if percent else int(v)
    _SETTINGS_READ = True
    _register_hooks()


def apply_settings(_window=None) -> None:
    """The Settings window changed a table setting: the module values again, and every
    table shown fits its columns again (one whose widths the user set keeps them)."""
    read_settings()
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        state = getattr(w, ATTR, None)
        if state is None:
            continue
        try:
            if not state.user_set():
                state.schedule()
        except RuntimeError:  # swallow-ok: a view being deleted
            continue


def reset_settings() -> None:
    """The module values back to their defaults (tests: they outlive a test's settings)."""
    global _SETTINGS_READ
    global MAX_FIT_WIDTH, MIN_STRETCH_WIDTH, MIN_SQUEEZED_WIDTH, FIT_SHARE, MAIN_SHARE
    global FIT_SAMPLE
    MAX_FIT_WIDTH, MIN_STRETCH_WIDTH, MIN_SQUEEZED_WIDTH = 360, 60, 36
    FIT_SHARE, MAIN_SHARE, FIT_SAMPLE = 0.35, 0.33, 1000
    _SETTINGS_READ = False


def _register_hooks() -> None:
    from .prefs import settings_pages as SP

    for key in SETTINGS:
        if apply_settings not in SP.APPLY_HOOKS.get(key, []):
            SP.register_apply(key, apply_settings)


def _ensure_settings() -> None:
    if not _SETTINGS_READ:
        read_settings()


# (the values are state the Settings window sets, not a cache: a test module starts with the
# defaults, as a table made with fresh settings reads them)
_caches.register("ui.tables.settings", SETTINGS, clear=reset_settings, on=("tests",))


def text_width(view, c: int, sample: Optional[int] = None) -> int:
    """Column ``c``'s widest text over a sample of rows."""
    model = view.model()
    if model is None:
        return 0
    if sample is None:
        sample = FIT_SAMPLE
    fm = view.fontMetrics()
    widest = icon = 0
    tree = isinstance(view, QtWidgets.QTreeView)
    display, deco, font = QtCore.Qt.DisplayRole, QtCore.Qt.DecorationRole, QtCore.Qt.FontRole

    def measure(idx, first: bool) -> None:
        nonlocal widest, icon
        v = idx.data(display)
        if v is None or v == "":
            return
        f = idx.data(font)
        w = (QtGui.QFontMetrics(f) if f else fm).horizontalAdvance(str(v))
        if tree and idx.column() == 0:
            depth = 0
            p = idx.parent()
            while p.isValid():
                depth += 1
                p = p.parent()
            w += view.indentation() * (depth + (1 if view.rootIsDecorated() else 0))
        if w > widest:
            widest = w
        if first and idx.data(deco) is not None:
            icon = view.iconSize().width() + 4 if view.iconSize().isValid() else 20

    n = 0
    if tree:
        idx = model.index(0, c)
        while idx.isValid() and n < sample:
            measure(idx, n == 0)
            idx = view.indexBelow(idx)
            n += 1
    else:
        for r in range(model.rowCount()):
            if n >= sample:
                break
            if isinstance(view, QtWidgets.QTableView) and view.isRowHidden(r):
                continue
            measure(model.index(r, c), n == 0)
            n += 1
    return widest + icon + 8 if widest else 0


def show_cut_tip(view, ev) -> bool:
    """The ToolTip event ``ev`` on ``view``'s viewport: the whole text of a cut cell as the
    tool tip (unless the model gives one of its own). True when it was handled."""
    idx = view.indexAt(ev.pos())
    if not idx.isValid():
        return False
    own = idx.data(QtCore.Qt.ToolTipRole)
    if own:
        return False  # the model's own tool tip
    text = idx.data(QtCore.Qt.DisplayRole)
    text = "" if text is None else str(text)
    if text and is_cut(view, idx, text):
        QtWidgets.QToolTip.showText(
            ev.globalPos(), tool_tip_text(text), view.viewport(), view.visualRect(idx)
        )
    else:
        QtWidgets.QToolTip.hideText()
    return True


class _CutCellTips(QtCore.QObject):
    """tip_cut_cells: the cut-cell tool tip of a view that has no header to set up."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        view.viewport().installEventFilter(self)

    def eventFilter(self, obj, ev):  # noqa: N802 -- Qt API
        try:
            if ev.type() == QtCore.QEvent.ToolTip:
                return show_cut_tip(self.view, ev)
        except RuntimeError:  # swallow-ok: the view is being deleted
            return False
        return False


def tip_cut_cells(view) -> QtCore.QObject:
    """A view without a column header (a one-column tree with its header hidden) shows the
    whole text of a cut cell as its tool tip, as every table does."""
    return _CutCellTips(view)


class _Header(QtCore.QObject):
    """What both layouts share: the user's widths (told apart from Qt's own resizes),
    the settings, the header menu and the tool tip of a cut cell."""

    def __init__(self, view, key: str, text_columns=()):
        super().__init__(view)
        self.view = view
        self.key = key
        self.header = hdr = header_of(view)
        #: the columns a fit measures from the model's text (text_width), not the delegate
        self.text_columns: set = set(text_columns or ())
        #: the left button is down on the header (a drag of a divider is the user's)
        self.dragging = False
        #: the section whose divider that press is on (None: a click on a title, to sort)
        self.pressed: Optional[int] = None
        #: that section was resized during the press
        self.changed = False
        #: the sections the user resized since the widths were last remembered
        self.touched: set = set()
        #: the section the user fitted with a double-click on its divider
        self.clicked: Optional[int] = None
        #: a layout skipped during a press (rows came, the view resized): run on the release
        self.pending = False
        #: our own resizes
        self.adjusting = False
        #: the remembered widths: ([width per logical section], the view's width) or None
        self.user = parse_widths(app_settings().value(settings_key(key)))
        hdr.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        hdr.setTextElideMode(QtCore.Qt.ElideRight)
        self._header_vp = hdr.viewport()
        self._view_vp = view.viewport()
        self._header_vp.installEventFilter(self)
        hdr.sectionResized.connect(self._on_section_resized)
        hdr.sectionHandleDoubleClicked.connect(self._after_double_click)
        self._view_vp.installEventFilter(self)
        view.installEventFilter(self)
        # the header menu: Fit Columns to Contents (a view with its own header menu adds
        # the same action to it: add_fit_item)
        self.fit_action = act = QtGui.QAction(FIT_TEXT, hdr)
        act.setObjectName("columns.fit")
        act.setToolTip(FIT_TIP)
        act.triggered.connect(lambda: _safe(self.fit_to_contents))
        hdr.addAction(act)
        if hdr.contextMenuPolicy() != QtCore.Qt.CustomContextMenu:
            hdr.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)

    # -- the remembered widths ----------------------------------------------------------------
    def user_set(self) -> bool:
        """The user's widths are remembered (for this many columns)."""
        return self.user is not None and len(self.user[0]) == self.header.count()

    def user_width(self, c: int) -> int:
        w = self.user[0][c] if self.user is not None and c < len(self.user[0]) else 0
        return w if w > 0 else self.header.sectionSize(c)

    def user_view(self) -> int:
        """The view's width when the user set the widths."""
        widths, view_w = self.user
        return view_w if view_w else self.view.viewport().width()

    def main_from_user(self, main: int, width: int) -> int:
        """The main column's width in a view ``width`` wide, from the user's widths: theirs
        plus the change of the view's width since. When the view got narrower, the room the
        last section only stretched over then goes first (it is not the user's)."""
        widths, _view_w = self.user
        then = self.user_view()
        delta = width - then
        if delta < 0:
            spare = max(0, then - sum(w for w in widths if w > 0))
            delta = min(0, delta + spare)
        return self.user_width(main) + delta

    def own_width(self, c: int) -> int:
        """Column ``c``'s width before Qt's stretch (the layouts know it)."""
        return self.header.sectionSize(c)

    def widths_to_save(self) -> List[int]:
        """The widths to remember: the sections' sizes, but the last section at its own
        width when it only fills the view. Qt's stretch of it is never the user's width:
        remembered, it would be the least that section gets (Qt never stretches it below
        the width it was given) and a narrower view would scroll."""
        hdr = self.header
        widths = [hdr.sectionSize(c) for c in range(hdr.count())]
        last = self.last()
        if last is not None:
            others = sum(widths[c] for c in self.visible_columns() if c != last)
            room = self.view.viewport().width() - others
            if widths[last] <= room + 1:
                if last == self.clicked:
                    # (a double-click on its divider: Qt's resizeColumnToContents width)
                    own = max(self.measure(last), hdr.sectionSizeHint(last))
                else:
                    own = self.own_width(last)
                widths[last] = max(hdr.minimumSectionSize(), min(widths[last], own))
        return widths

    def measure(self, c: int) -> int:
        """Column ``c``'s content width: the model's text for a text column, else the
        delegate's size hints (Qt's sizeHintForColumn)."""
        if c in self.text_columns:
            return text_width(self.view, c)
        return self.view.sizeHintForColumn(c)

    def remember(self) -> None:
        """The widths now are the user's: remembered with the view's width."""
        widths = self.widths_to_save()
        view_w = self.view.viewport().width()
        self.touched = set()
        self.clicked = None
        self.user = (widths, view_w)
        try:
            app_settings().setValue(settings_key(self.key), format_widths(widths, view_w))
        except Exception:  # noqa: BLE001 -- swallow-ok: not remembered then, the view works
            pass
        self.after_user()

    def forget(self) -> None:
        self.user = None
        try:
            app_settings().remove(settings_key(self.key))
        except Exception:  # noqa: BLE001 -- swallow-ok: the next open fits them anyway
            pass

    # -- the layout (the two kinds below) --------------------------------------------------
    def after_user(self) -> None:
        """The user set the widths."""

    def fit_to_contents(self) -> None:
        """Fit Columns to Contents: forget the user's widths, fit the columns again."""

    def schedule(self) -> None:
        """Lay the columns out again soon (a model replaced, a column set changed)."""

    def viewport_resized(self) -> None:
        pass

    def shown(self) -> None:
        pass

    # -- helpers ------------------------------------------------------------------------------
    def visible_columns(self) -> List[int]:
        hdr = self.header
        cols = [hdr.logicalIndex(v) for v in range(hdr.count())]
        return [c for c in cols if not hdr.isSectionHidden(c)]

    def last(self) -> Optional[int]:
        cols = self.visible_columns()
        return cols[-1] if cols else None

    def set_sizes(self, widths: Dict[int, int]) -> None:
        """Exactly these widths (the last section too: its stretch is off meanwhile)."""
        hdr = self.header
        self.adjusting = True
        try:
            hdr.setStretchLastSection(False)
            for c, w in widths.items():
                if 0 <= c < hdr.count():
                    hdr.resizeSection(c, max(int(w), hdr.minimumSectionSize()))
            hdr.setStretchLastSection(True)
        finally:
            self.adjusting = False

    def resize(self, c: int, w: int) -> None:
        hdr = self.header
        if hdr.sectionSize(c) == w:
            return
        self.adjusting = True
        try:
            hdr.resizeSection(c, w)
        finally:
            self.adjusting = False

    # -- signals / events -------------------------------------------------------------------
    def _columns_changed(self) -> None:
        """Columns came (a model filled later): Interactive too."""
        try:
            self.header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        except RuntimeError:  # swallow-ok: the view is being deleted
            pass

    def _on_section_resized(self, logical=-1, *_a) -> None:
        # (only the section whose divider the press is on: Qt's stretch of the last section
        # and a fit while the button is down resize others)
        if self.dragging and not self.adjusting and logical == self.pressed:
            self.changed = True
            self.touched.add(logical)

    def _after_double_click(self, logical=-1, *_a) -> None:
        # Qt fits the column in the same signal (connected first); remember it after that
        if logical in self.text_columns:
            # (Qt measured the rows on screen through the delegate: the model's text, every
            # row, instead)
            self.resize(logical, max(text_width(self.view, logical), self.header.sectionSizeHint(logical)))
        self.touched = {logical}
        self.clicked = logical
        QtCore.QTimer.singleShot(0, self, lambda: _safe(self.remember))

    def _handle_at(self, x: int) -> Optional[int]:
        """The section whose divider is at ``x`` on the header's viewport, or None (Qt's
        own rule, QHeaderViewPrivate::sectionHandleAt)."""
        hdr = self.header
        visual = hdr.visualIndexAt(x)
        if visual < 0:
            return None
        log = hdr.logicalIndex(visual)
        pos = hdr.sectionViewportPosition(log)
        grip = hdr.style().pixelMetric(QtWidgets.QStyle.PM_HeaderGripMargin, None, hdr)
        at_left, at_right = x < pos + grip, x > pos + hdr.sectionSize(log) - grip
        if hdr.isRightToLeft():
            at_left, at_right = at_right, at_left
        if at_left:
            for v in range(visual - 1, -1, -1):
                c = hdr.logicalIndex(v)
                if not hdr.isSectionHidden(c):
                    return c
            return None
        return log if at_right else None

    def eventFilter(self, obj, ev):  # noqa: N802 -- Qt API
        try:
            return self._event(obj, ev)
        except RuntimeError:  # swallow-ok: the view is being deleted
            return False

    def _event(self, obj, ev) -> bool:
        t = ev.type()
        if obj is self._header_vp:
            if t == QtCore.QEvent.MouseButtonPress and ev.button() == QtCore.Qt.LeftButton:
                self.dragging = True
                self.changed = False
                self.pressed = self._handle_at(ev.position().toPoint().x())
            elif t == QtCore.QEvent.MouseButtonRelease and self.dragging:
                self.dragging = False
                self.pressed = None
                pending, self.pending = self.pending, False
                if self.changed:
                    self.changed = False
                    # (after the header's own release; the layout runs after it)
                    QtCore.QTimer.singleShot(0, self, lambda: _safe(self.remember))
                elif pending:
                    # rows came or the view resized during a click: lay out now
                    QtCore.QTimer.singleShot(0, self, lambda: _safe(self.viewport_resized))
        elif obj is self._view_vp:
            if t == QtCore.QEvent.Resize:
                self.viewport_resized()
            elif t == QtCore.QEvent.ToolTip:
                return self._tool_tip(ev)
        elif obj is self.view:
            if t == QtCore.QEvent.Show:
                self.shown()
        return False

    def _tool_tip(self, ev) -> bool:
        return show_cut_tip(self.view, ev)

    def fit_one(self, c: int) -> int:
        """Column ``c`` fitted to its content now (capped as a fit is); its width."""
        hdr = self.header
        want = max(self.measure(c), hdr.sectionSizeHint(c), hdr.minimumSectionSize())
        if c == self.last():
            self.set_sizes({c: want})
        else:
            self.resize(c, want)
        return want


class _Columns(_Header):
    """The default layout: the columns fit their content once; the main column takes the
    room the others leave and the view's width changes, never narrower than its own
    (capped) text: a narrow window scrolls sideways instead."""

    def __init__(
        self,
        view,
        key: str,
        stretch: Optional[int],
        fit: bool,
        max_width: Optional[int],
        caps: Optional[dict] = None,
        grow: bool = False,
        text_columns=(),
    ):
        hdr = header_of(view)
        default = hdr.defaultSectionSize()
        before = {c: hdr.sectionSize(c) for c in range(hdr.count())}
        stretched = hdr.stretchLastSection()
        super().__init__(view, key, text_columns)
        #: widths the code set before the call: a fit never goes below them (not the last
        #: section's when it stretched: that is Qt's fill of the view then)
        self.designed = {c: w for c, w in before.items() if w != default}
        if stretched:
            self.designed.pop(self.last(), None)
        #: the widths before the call (the last section's own width when it is not fitted)
        self.base = before
        #: a list that keeps getting rows (a log): columns widen to new rows' text
        self.grow = grow
        self.grow_rows: list = []
        #: the fitted content width per column
        self.content: dict = {}
        #: per column: the most a fit gives it (a float = a share of the view's width)
        self.caps = dict(caps or {})
        self.stretch = stretch
        self.fit = fit
        self.max_width = max_width
        self.fitted = False
        #: the remembered widths were put on the header
        self.applied = False
        if self.user_set():
            self.apply_user()
        self.hook_model()

    # -- model ------------------------------------------------------------------
    def hook_model(self) -> None:
        model = self.view.model()
        if model is None:
            return
        for sig in (model.rowsInserted, model.modelReset, model.layoutChanged):
            sig.connect(self._maybe_fit)
        if self.grow:
            model.rowsInserted.connect(self._rows_inserted)
            # (text that comes later into rows already there: the page browser's summaries)
            model.dataChanged.connect(self._data_changed)
        model.columnsInserted.connect(lambda *_a: self._columns_changed())
        model.headerDataChanged.connect(lambda *_a: self._columns_changed())
        self._maybe_fit()

    def _columns_changed(self) -> None:
        super()._columns_changed()
        try:
            if self.user_set() and not self.applied:
                self.apply_user()
        except RuntimeError:  # swallow-ok: the view is being deleted
            pass

    def _maybe_fit(self, *_a) -> None:
        try:
            self._maybe_fit_now()
        except RuntimeError:  # swallow-ok: the view is being deleted
            pass

    def _maybe_fit_now(self) -> None:
        model = self.view.model()
        if model is None or model.rowCount() == 0 or not self.view.isVisible():
            return  # (fitted on the first show)
        if self.user_set():
            if self.unsized():
                QtCore.QTimer.singleShot(0, self, lambda: _safe(self.fit_unsized))
            return
        if self.fitted or not self.fit:
            return
        QtCore.QTimer.singleShot(0, self, lambda: _safe(self.fit_now))

    def unsized(self) -> List[int]:
        """The columns shown whose remembered width is 0: hidden when the user set the
        widths, shown now (Go To Object's columns ticked later)."""
        if not self.user_set():
            return []
        widths = self.user[0]
        return [c for c in self.visible_columns() if c < len(widths) and widths[c] <= 0]

    def fit_unsized(self) -> None:
        """The columns shown with no remembered width fit their text (capped), once: they
        came back at Qt's default width, cut."""
        model = self.view.model()
        if model is None or model.rowCount() == 0:
            return
        for c in self.unsized():
            # (the width this column has now; the next drag remembers it with the others)
            self.user[0][c] = self.fit_one(c)

    def _rows_inserted(self, parent, first, last) -> None:
        try:
            self._queue_grow(parent, first, last)
        except RuntimeError:  # swallow-ok: the view is being deleted
            pass

    def _data_changed(self, top, bottom, *_roles) -> None:
        try:
            if top.isValid():
                self._queue_grow(top.parent(), top.row(), bottom.row())
        except RuntimeError:  # swallow-ok: the view is being deleted
            pass

    def _queue_grow(self, parent, first, last) -> None:
        if not self.fitted or self.user_set():
            return  # the first fit measures them all
        if not self.grow_rows:
            QtCore.QTimer.singleShot(0, self, lambda: _safe(self._grow_now))
        self.grow_rows.append((QtCore.QPersistentModelIndex(parent), first, last))

    def _grow_now(self) -> None:
        pending, self.grow_rows = self.grow_rows, []
        if self.user_set():
            return
        model = self.view.model()
        if model is None:
            return
        view = self.view
        fm = view.fontMetrics()
        hdr = self.header
        want = {}
        tree = isinstance(view, QtWidgets.QTreeView)
        for parent, first, last in pending:
            par = QtCore.QModelIndex(parent)
            indent = 0
            if tree:
                # (the first column of a tree: the rows' indent, as Qt's own measure)
                depth, p = 0, par
                while p.isValid():
                    depth += 1
                    p = p.parent()
                indent = view.indentation() * (depth + (1 if view.rootIsDecorated() else 0))
            for r in range(first, min(last, first + 500) + 1):
                for c in self.visible_columns():
                    v = model.index(r, c, par).data(QtCore.Qt.DisplayRole)
                    if v:
                        w = fm.horizontalAdvance(str(v)) + 16 + (indent if c == 0 else 0)
                        want[c] = max(want.get(c, 0), w)
        main = self.main()
        for c, w in want.items():
            w = min(w, self.cap(c))
            if w > self.content.get(c, 0):
                self.content[c] = w
            if c != main and w > hdr.sectionSize(c):
                self.resize(c, w)
        self.fill_main()

    # -- widths -----------------------------------------------------------------
    def main(self) -> Optional[int]:
        if (
            self.stretch is not None
            and 0 <= self.stretch < self.header.count()
            and not self.header.isSectionHidden(self.stretch)
        ):
            return self.stretch
        return None

    def fit_now(self, force: bool = False) -> None:
        """Fit every column to its content (capped), once; ``force`` = again (Fit
        Columns to Contents), also in a view that sets its own first widths."""
        if not force and (self.fitted or not self.fit or self.user_set()):
            return
        model = self.view.model()
        if model is None or (model.rowCount() == 0 and not force):
            return
        self.fitted = True
        hdr = self.header
        widths = {}
        for c in self.visible_columns():
            want = max(self.measure(c), hdr.sectionSizeHint(c))
            want = min(want, self.cap(c))
            # (a width the code set is the least a fit gives; in a view that sets its own
            # widths they are its design, not a floor)
            floor = self.designed.get(c, 0) if self.fit else 0
            widths[c] = max(want, floor, hdr.minimumSectionSize())
        last = self.last()
        if self.main() is None and last in widths:
            # (no main column: the last one gets at most the room the others leave, Qt never
            # stretches it below its width and a wider one scrolls the view sideways)
            room = self.view.viewport().width() - sum(w for c, w in widths.items() if c != last)
            widths[last] = max(hdr.sectionSizeHint(last), hdr.minimumSectionSize(), min(widths[last], room))
        self.content = dict(widths)
        self.set_sizes(widths)
        self.fill_main()

    def cap(self, c: int) -> int:
        cap = self.caps.get(c, self.max_width if self.max_width is not None else MAX_FIT_WIDTH)
        if isinstance(cap, float):
            cap = int(max(self.view.viewport().width(), 200) * cap)
        return max(int(cap), self.header.minimumSectionSize())

    def fit_one(self, c: int) -> int:
        """Column ``c`` fitted to its content now, capped as a fit caps it; the main column
        takes what it leaves."""
        hdr = self.header
        want = max(self.measure(c), hdr.sectionSizeHint(c))
        want = max(min(want, self.cap(c)), hdr.minimumSectionSize())
        self.content[c] = want
        if c == self.last():
            self.set_sizes({c: want})
        else:
            self.resize(c, want)
        self.fill_main()
        return want

    def own_width(self, c: int) -> int:
        return self.natural(c)

    def natural(self, c: int) -> int:
        """Column ``c``'s own width: for the last section (Qt stretches it) the one it
        was given, else its size."""
        if c != self.last():
            return self.header.sectionSize(c)
        if self.user_set():
            return self.user_width(c)
        if c in self.content:
            return self.content[c]
        return self.base.get(c, self.header.sectionSize(c))

    def fill_main(self) -> None:
        """The main-text column takes the room the others leave (with the user's widths:
        their main width plus the change of the view's width since, main_from_user), never
        less than its own (capped) content: then the view scrolls instead of cutting it."""
        main, last = self.main(), self.last()
        if main is None or last is None or main == last:
            return
        if self.dragging:
            self.pending = True  # (laid out on the release)
            return
        width = self.view.viewport().width()
        if self.user_set() and self.user[0][main] > 0:
            mine = self.user[0][main]
            size = self.main_from_user(main, width)
            floor = min(mine, MIN_STRETCH_WIDTH)
        else:
            others = sum(self.natural(c) for c in self.visible_columns() if c != main)
            size = width - others
            own = min(self.content.get(main, 0), self.cap(main))
            floor = max(MIN_STRETCH_WIDTH, own)
        self.resize(main, max(floor, size))

    def apply_user(self) -> None:
        """The remembered widths on the header."""
        if not self.user_set():
            return
        self.applied = True
        self.set_sizes({c: w for c, w in enumerate(self.user[0]) if w > 0})
        self.fill_main()
        self.fit_unsized()

    # -- the layout hooks -------------------------------------------------------
    def after_user(self) -> None:
        self.applied = True
        self.fill_main()

    def fit_to_contents(self) -> None:
        self.forget()
        self.applied = False
        self.content = {}
        self.fitted = False
        self.fit_now(force=True)

    def schedule(self) -> None:
        self.content = {}
        self.fitted = False
        self._maybe_fit()

    def viewport_resized(self) -> None:
        # (now, before the header's own deferred stretch: it then fills what is left)
        self.fill_main()

    def shown(self) -> None:
        if self.user_set():
            if not self.applied:
                QtCore.QTimer.singleShot(0, self, lambda: _safe(self.apply_user))
            elif self.unsized():
                QtCore.QTimer.singleShot(0, self, lambda: _safe(self.fit_unsized))
        elif self.fit and not self.fitted:
            QtCore.QTimer.singleShot(0, self, lambda: _safe(self.fit_now))


def is_cut(view, idx, text: str) -> bool:
    """True when ``text`` does not fit the cell ``idx``."""
    if "\n" in text:
        return True
    rect = view.visualRect(idx)
    fm = QtGui.QFontMetrics(idx.data(QtCore.Qt.FontRole) or view.font())
    need = fm.horizontalAdvance(text) + 10
    if idx.data(QtCore.Qt.DecorationRole) is not None:
        need += view.iconSize().width() + 4 if view.iconSize().isValid() else 20
    return need > rect.width()


def tool_tip_text(text: str) -> str:
    """A long text as a tool tip that wraps (rich text wraps, plain text does not)."""
    if len(text) <= 80 and "\n" not in text:
        return text
    return "<qt>%s</qt>" % html.escape(text).replace("\n", "<br>")


def make_resizable(
    view,
    key: str,
    *,
    stretch: Optional[int] = None,
    fit_on_first_fill: bool = True,
    max_width: Optional[int] = None,
    caps: Optional[dict] = None,
    grow: bool = False,
    fit_view: bool = False,
    fixed: Optional[dict] = None,
    squeeze=(),
    main_min: Optional[int] = None,
    text_columns=(),
):
    """Set up ``view``'s column header."""
    hdr = header_of(view)
    if hdr is None:
        return None
    old = getattr(view, ATTR, None)
    if old is not None:
        return old
    _ensure_settings()
    if not view.objectName():
        view.setObjectName(key.rsplit("/", 1)[-1])
    if fit_view:
        state = _FitToView(view, key, stretch or 0, caps or {}, fixed or {}, squeeze, main_min, text_columns)
    else:
        state = _Columns(view, key, stretch, fit_on_first_fill, max_width, caps, grow, text_columns)
    setattr(view, ATTR, state)
    return state


def state_of(view):
    """The column state make_resizable gave ``view`` (None when it has none)."""
    return getattr(view, ATTR, None)


def fit_column(view, c: int) -> None:
    """Fit column ``c`` to its content now (a column shown later: Go To Object's Columns).
    Not the user's width: the next drag remembers it with the others."""
    state = state_of(view)
    if state is None:
        return
    try:
        state.fit_one(c)
    except RuntimeError:  # swallow-ok: the view is being deleted
        pass


def refit(view) -> None:
    """Fit the columns again (a view whose model was replaced), unless the
    user set the widths."""
    state = state_of(view)
    if state is not None:
        state.schedule()


def has_user_widths(view) -> bool:
    """True when the user set this table's column widths (they are remembered)."""
    state = state_of(view)
    return state is not None and state.user_set()


def forget_widths(view, key: Optional[str] = None) -> None:
    """Forget the widths the user set in ``view`` (or, with no state yet, under ``key``)
    and fit the columns again: Fit Columns to Contents, or a column set that changed."""
    state = state_of(view)
    if state is not None:
        state.fit_to_contents()
    elif key:
        try:
            app_settings().remove(settings_key(key))
        except Exception:  # noqa: BLE001 -- swallow-ok: the next open fits them anyway
            pass


def add_fit_item(menu: QtWidgets.QMenu, view) -> Optional[QtGui.QAction]:
    """Fit Columns to Contents in a header menu of the view's own (the helper's own menu
    has it already)."""
    state = state_of(view)
    if state is None:
        return None
    if not menu.isEmpty():
        menu.addSeparator()
    menu.addAction(state.fit_action)
    return state.fit_action


# -- copy ------------------------------------------------------------------------
def selected_rows_text(view) -> str:
    """The selected rows (the current one when none is selected) as tab-separated
    text, visible columns in their shown order, one line per row."""
    model = view.model()
    if model is None:
        return ""
    sel = view.selectionModel()
    rows = []
    if sel is not None:
        seen = set()
        for idx in sel.selectedIndexes():
            key = (idx.row(), idx.parent())
            if key not in seen:
                seen.add(key)
                rows.append(idx.sibling(idx.row(), 0))
    if not rows:
        cur = view.currentIndex()
        if cur.isValid():
            rows = [cur.sibling(cur.row(), 0)]
    hdr = header_of(view)
    cols = list(range(model.columnCount()))
    if hdr is not None:
        cols = [hdr.logicalIndex(v) for v in range(hdr.count())]
        cols = [c for c in cols if not hdr.isSectionHidden(c)]

    def order(idx):
        if isinstance(view, QtWidgets.QTreeView):
            return view.visualRect(idx).top(), idx.row()
        return idx.row(), 0

    rows.sort(key=order)
    lines = []
    for r in rows:
        vals = []
        for c in cols:
            v = r.sibling(r.row(), c).data(QtCore.Qt.DisplayRole)
            vals.append(
                "" if v is None else str(v).replace("\t", " ").replace("\r\n", " ").replace("\n", " ")
            )
        lines.append("\t".join(vals))
    return "\n".join(lines)


def copy_selected_rows(view) -> str:
    text = selected_rows_text(view)
    if text:
        QtWidgets.QApplication.clipboard().setText(text)
    return text


def enable_row_copy(view) -> QtGui.QAction:
    """Copy (right-click and Ctrl+C) of the selected rows as tab-separated text."""
    act = QtGui.QAction("Copy", view)
    act.setShortcut(QtGui.QKeySequence.Copy)
    act.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
    act.setToolTip("Copy the selected rows as tab-separated text")
    act.triggered.connect(lambda: copy_selected_rows(view))
    view.addAction(act)
    view.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)
    view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    if view.selectionMode() == QtWidgets.QAbstractItemView.SingleSelection:
        view.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
    return act


# -- columns that always fit the view ---------------------------------------------------------
class _FitToView(_Header):
    """make_resizable(fit_view=True): see there."""

    def __init__(
        self,
        view,
        key: str,
        stretch: int,
        caps: dict,
        fixed: dict,
        squeeze=(),
        main_min: Optional[int] = None,
        text_columns=(),
    ):
        super().__init__(view, key, text_columns)
        self.stretch = stretch
        self.caps = dict(caps or {})
        self.fixed = dict(fixed or {})
        self.squeeze = set(squeeze or ())
        #: the least the main column gets (None: the Tables setting)
        self.main_min = None if main_min is None else max(1, int(main_min))
        #: the widths the last fit gave (the main column's apart)
        self._given: dict = {}
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.fit)
        self.watch(view.model())

    def watch(self, model) -> None:
        if model is None:
            return
        for sig in (model.modelReset, model.layoutChanged, model.rowsInserted, model.dataChanged):
            sig.connect(lambda *_a: self.schedule())
        for sig in (model.columnsInserted, model.headerDataChanged):
            sig.connect(lambda *_a: (self._columns_changed(), self.schedule()))
        self.schedule()

    def schedule(self) -> None:
        try:
            self._timer.start()
        except RuntimeError:  # swallow-ok: the view is being deleted
            pass

    # -- the layout hooks -------------------------------------------------------
    def after_user(self) -> None:
        self.fit()

    def fit_to_contents(self) -> None:
        self.forget()
        self.fit()

    def viewport_resized(self) -> None:
        self.schedule()

    def shown(self) -> None:
        self.schedule()

    def fit_one(self, c: int) -> int:
        # (the columns always share the view: the whole fit again)
        self.schedule()
        return self.header.sectionSize(c)

    def min_main(self) -> int:
        return self.main_min if self.main_min is not None else max(1, int(MIN_STRETCH_WIDTH))

    # -- the widths ---------------------------------------------------------------
    def own_width(self, c: int) -> int:
        if self.user_set() and self.user[0][c] > 0:
            return self.user[0][c]
        return self._given.get(c, self.header.sectionSize(c))

    def widths_to_save(self) -> List[int]:
        """As the base, but the columns the user did not resize keep their remembered
        widths: the sizes now are the fit's (squeezed in a narrow view), not the user's."""
        widths = super().widths_to_save()
        if self.user_set():
            old = self.user[0]
            for c in self.visible_columns():
                if c in self.touched or c == self.stretch or old[c] <= 0:
                    continue
                widths[c] = old[c]
        return widths

    def fit(self) -> None:
        view = self.view
        try:
            hdr = header_of(view)
            model = view.model()
        except RuntimeError:  # swallow-ok: the view is being deleted
            return
        if hdr is None or model is None:
            return
        if self.dragging:
            self.pending = True  # (fitted on the release)
            return
        try:
            width = max(1, view.viewport().width())
            shown = self.visible_columns()
            if not shown:
                return
            main = self.stretch if self.stretch in shown else None
            if self.user_set():
                want, main_w = self._user_widths(width, shown, main)
            else:
                want, main_w = self._content_widths(width, shown, main)
            last = shown[-1]
            # (exactly these widths, the last section's too: Qt stretches it, never below
            # the width it was given, so that one is set with its stretch off)
            if want != self._given or any(
                hdr.sectionSize(c) < w if c == last else hdr.sectionSize(c) != w for c, w in want.items()
            ):
                self.set_sizes(want)
                self._given = dict(want)
            # the main column: what the others leave (the last section stretches itself)
            if main is not None and main != last:
                self.resize(main, main_w)
        except RuntimeError:  # swallow-ok: the view is being deleted
            return

    def _content_widths(self, width: int, shown: list, main) -> Tuple[dict, int]:
        """Each column as wide as its content (capped), the main column what is left."""
        view, hdr = self.view, self.header
        fm = view.fontMetrics()
        want = {}
        for c in shown:
            if c == main:
                continue
            if c in self.fixed:
                sample = self.fixed[c]
                if isinstance(sample, int):
                    want[c] = max(hdr.minimumSectionSize(), sample)
                else:
                    want[c] = max(fm.horizontalAdvance(str(sample)), hdr.sectionSizeHint(c) - 8) + 24
                continue
            w = max(hdr.sectionSizeHint(c), self.measure(c)) + 8
            cap = self.caps.get(c, FIT_SHARE)
            most = int(width * cap) if isinstance(cap, float) else int(cap)
            least = MIN_SQUEEZED_WIDTH
            want[c] = max(least, min(w, max(least, most)))
        # the main column keeps at least its share of the width (MAIN_SHARE): the others
        # give way in proportion (never below MIN_SQUEEZED_WIDTH)
        room = width - max(self.min_main(), int(width * MAIN_SHARE)) if main is not None else width - 2
        total = sum(want.values())
        if total > room and self.squeeze:
            # the text columns give way first (a number is never cut), down to the least
            # width of a main column
            over = total - room
            soft = {c: w for c, w in want.items() if c in self.squeeze}
            keep = MIN_STRETCH_WIDTH
            give = sum(max(0, w - keep) for w in soft.values())
            if give > 0:
                k = min(1.0, over / float(give))
                for c, w in soft.items():
                    want[c] = int(w - max(0, w - keep) * k)
            total = sum(want.values())
        if total > room:
            k = max(0.0, room) / float(total)
            want = {c: max(MIN_SQUEEZED_WIDTH, int(w * k)) for c, w in want.items()}
        # (the widths just given, not the sizes now: the last section stretches over the
        # room the main column had, and counting that would keep it narrow)
        main_w = max(self.min_main(), width - sum(want.values()) - 2)
        return want, main_w

    def _user_widths(self, width: int, shown: list, main) -> Tuple[dict, int]:
        """The user's widths (the main column: main_from_user, so the room the last section
        stretched over gives way first); when they do not fit, the main column gives way
        (down to main_min), then the others in proportion. The room left is the last
        section's stretch, as when the user set them."""
        hdr = self.header
        floor = hdr.minimumSectionSize()
        want = {c: max(floor, self.user_width(c)) for c in shown if c != main}
        main_w = 0
        if main is not None:
            main_w = max(self.min_main(), self.main_from_user(main, width))
        # (the width remember() saves, so a save takes nothing off the main column)
        over = sum(want.values()) + main_w - width
        if over > 0 and main is not None:
            give = min(over, max(0, main_w - self.min_main()))
            main_w -= give
            over -= give
        if over > 0 and want:
            total = sum(want.values())
            k = max(0.0, total - over) / float(total)
            want = {c: max(MIN_SQUEEZED_WIDTH, int(w * k)) for c, w in want.items()}
        return want, main_w
