"""Context help: F1 opens the manual on whatever has focus."""

from __future__ import annotations

import re
import weakref
from typing import Callable, Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

HELP_PROP = "ctgHelpTopic"


def _page_topic(w) -> str:
    """The help topic a tab's page widget names itself (the object sheet's
    pages do: properties#fill-colour ...), looking into an inner tab widget's
    current page first."""
    try:
        if isinstance(w, QtWidgets.QTabWidget) and w.count():
            t = _page_topic(w.currentWidget())
            if t:
                return t
        if isinstance(w, QtWidgets.QScrollArea) and w.widget() is not None:
            t = help_of(w.widget())
            if t:
                return t
        return help_of(w) if w is not None else ""
    except RuntimeError:
        return ""


def _tab_anchor(page: str, attr: str = "_tabs"):
    """Topic of a tabbed dialog = the current page's own topic when it names
    one, else its page + the current tab's heading."""

    def topic(dlg) -> str:
        tabs = getattr(dlg, attr, None)
        if tabs is None:
            tabs = dlg.findChild(QtWidgets.QTabWidget)
        # (a tab widget, or the settings window's page list: settings_pages.SettingsPages
        # answers the same)
        if tabs is not None and hasattr(tabs, "tabText") and tabs.count():
            own = _page_topic(tabs.currentWidget())
            if own:
                return own
            from .. import help as H

            return "%s#%s" % (page, H.slug(tabs.tabText(tabs.currentIndex()).replace("&", "")))
        return page

    return topic


def _genie_topic(default: str):
    """A genie dialog showing its library's FORM is documented under genie
    forms, the default form under ``default``."""

    def topic(dlg) -> str:
        return "genies#genie-forms" if getattr(dlg, "form", None) is not None else default

    return topic


#: every dialog's help topic (class name -> 'page#anchor' or a callable
#: giving it from the dialog's state); applied when the dialog is shown, so
#: dialogs need no help code of their own. tests/test_help.py checks that
#: each topic exists and that every dialog class is listed.
DIALOG_TOPICS = {
    "GenieSubstitutionDialog": _genie_topic("genies#genie-parameters"),
    "ReplaceGenieDialog": "genies#replace-a-genie",
    "SimSettingsDialog": "simulate#simulate-settings",
    "SimFormDialog": "simulate#dialogs-the-page-shows",
    "PermissionDialog": "simulate#dlls-and-databases",
    "AllowedListDialog": "simulate#dlls-and-databases",
    "CompileResultsDialog": "compile-and-run#compile-results",
    "GotoObjectDialog": "selecting-and-arranging#go-to-an-object",
    "RenameObjectsDialog": "selecting-and-arranging#rename-objects",
    "FindReplaceDialog": "selecting-and-arranging#find-and-replace",
    "AdjustPositionDialog": "selecting-and-arranging#exact-position-and-size",
    "SelectByTagDialog": "selecting-and-arranging#select-same-invert-selection-select-previous",
    "AlignDialog": "selecting-and-arranging#align-objects",
    "_SelectionDialog": "selecting-and-arranging#align-objects",
    "SpaceEquallyDialog": "selecting-and-arranging#align-objects",
    "ArrangeGridDialog": "selecting-and-arranging#align-objects",
    "FontDialog": "text-and-numbers#fonts",
    "PasteSpecialDialog": "selecting-and-arranging#paste-special",
    "SettingsDialog": _tab_anchor("settings"),
    "ShortcutsWindow": "the-window#keyboard-shortcuts",
    "ChangeShortcutDialog": "settings#keyboard",
    "SettingsTransferDialog": "settings#reset-export-and-import",
    "GridSetupDialog": "drawing#grid-and-snap",
    "GuidelinesDialog": "drawing#guidelines",
    "SwapColorsDialog": "colours#swap-colours",
    "AdjustColorsDialog": "colours#adjust-colours",
    "FavoriteColorsDialog": "colours#favourite-colours",
    "ManageStylesDialog": "colours#named-styles",
    "SaveStyleDialog": "colours#named-styles",
    "BitmapEditorDialog": "bitmaps#the-bitmap-editor",
    "BitmapSizeDialog": "bitmaps#bitmap-size",
    "TransparentPasteDialog": "bitmaps#transparent-paste-colour",
    "PictureImportDialog": "bitmaps#picture-options",
    "DuplicatePicturesDialog": "bitmaps#duplicate-pictures",
    "_SymbolNameDialog": "bitmaps#duplicate-pictures",
    "PresentationOptionsDialog": "composite-genies#presentation-options",
    "PasteCompositeGenieDialog": "composite-genies#insert-a-composite-genie",
    "AttachSuperGeniesDialog": "super-genies#attach-super-genies",
    "GenieFormEditor": "genies#design-a-genie-form",
    "SuperGeniePageDialog": "super-genies#pages-created-from-a-super-genie",
    "CopyToLibraryDialog": "library-elements#copy-to-library",
    "HotSpotDialog": "library-elements#hot-spot",
    "UsedByDialog": "library-elements#used-by",
    "ElementUsersDialog": "library-elements#used-by",
    "GeniePasteDialog": _genie_topic("genies#place-a-genie"),
    "_GenieDialogBase": "genies#genie-forms",
    "NumberExpressionDialog": "drawing#numbers",
    "ZoomWindow": "the-window#the-magnifier",
    "PackLibrariesDialog": "library-elements#pack-libraries",
    "ManageSavedViewsDialog": "the-window#saved-views",
    "NavBarCustomiseDialog": "the-window#the-navigation-bar",
    "ObjectPropertiesDialog": _tab_anchor("properties"),
    "KeySequenceDialog": "properties#keyboard-commands",
    "ListPickerDialog": "properties#the-wizard",
    "CicodeBoxDialog": "properties#edit-in-a-larger-box",
    "TagPickerDialog": "properties#insert-a-tag",
    "FunctionPickerDialog": "properties#insert-a-function",
    "TrendTagPickerDialog": "properties#insert-a-trend-tag",
    "EquipmentPickerDialog": "properties#alarm-indicator",
    "EquipmentBrowserDialog": "situational-awareness#choose-equipment",
    "MenuItemPickerDialog": "situational-awareness#choose-a-menu-item",
    "PagePropertiesDialog": _tab_anchor("page-properties"),
    "PageBrowserDialog": lambda dlg: (
        "opening-and-saving#create-a-page-or-library-element"
        if getattr(dlg, "mode", "") == "new"
        else "opening-and-saving#the-page-browser"
    ),
    "UpdatePagesDialog": "update-pages",
    "RunResultsDialog": "update-pages#the-summary",
    "UseTemplateDialog": "templates#create-a-page-from-a-template",
    "PageDefaultsDialog": "templates#page-defaults",
    "SymbolSetDialog": "symbol-sets#choose-the-type-and-the-symbols",
    "InsertActiveXDialog": "special-objects#insert-an-activex-control",
    "AboutDialog": "troubleshooting#report-a-problem",
    "UpdateDialog": "getting-started#updates",
    "RecoveryDialog": "troubleshooting#recover-unsaved-changes",
    "SaveChangesDialog": "opening-and-saving#unsaved-changes",
    "RestoreBackupDialog": "troubleshooting#restore-a-backup",
    "PageHistoryDialog": "opening-and-saving#earlier-versions-of-a-page",
    "UnderlayDialog": "underlay#the-underlay-window",
    "VersionPickerDialog": "underlay#underlay-an-earlier-version",
    "UnresolvedReferencesDialog": "troubleshooting#symbols-not-found",
}


def dialog_topic(dlg) -> str:
    for cls in type(dlg).__mro__:
        spec = DIALOG_TOPICS.get(cls.__name__)
        if spec is not None:
            try:
                return spec(dlg) if callable(spec) else spec
            except Exception:  # noqa: BLE001
                return ""
    return ""


GEOMETRY_PREFIX = "geometry/"

# ----------------------------------------------------------------- resizing
# every dialog has one resize rule, set here when
# it is first shown. 'both' = the list, table or text box takes the extra room;
# 'width' = fields grow wider, the height follows the content; 'none' = fixed
# size. A class sets RESIZE (or an instance the '_ctgResize' property) when the
# rule its content gives is not the right one.

RESIZE_RULES = ("both", "width", "none")
#: Qt's own dialogs: they size themselves
_QT_DIALOGS = (
    QtWidgets.QMessageBox,
    QtWidgets.QProgressDialog,
    QtWidgets.QInputDialog,
    QtWidgets.QFileDialog,
    QtWidgets.QColorDialog,
    QtWidgets.QFontDialog,
    QtWidgets.QErrorMessage,
    QtWidgets.QWizard,
)
#: widgets whose line edit is a part of them, not a text field of its own
_COMPOUND = (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox, QtWidgets.QKeySequenceEdit)
#: Qt widgets that only take extra height when the dialog asked for it: their
#: default vertical policy is Preferred (a QTabWidget's is Expanding)
_PLAIN = (
    QtWidgets.QWidget,
    QtWidgets.QFrame,
    QtWidgets.QLabel,
    QtWidgets.QStackedWidget,
    QtWidgets.QGroupBox,
)
_QMAX = 16777215  # QWIDGETSIZE_MAX
#: a modeless window's first position: this far inside the canvas' top right
#: corner, the next one of the same kind this much lower
PLACE_MARGIN = 12
PLACE_STEP = 24


def _geometry_key(dlg: QtWidgets.QDialog) -> str:
    key = dlg.property("_ctgGeometryKey")
    return str(key) if key else GEOMETRY_PREFIX + type(dlg).__name__


def _is_qt_dialog(dlg) -> bool:
    return isinstance(dlg, _QT_DIALOGS)


def _plain_qdialog(dlg) -> bool:
    """A QDialog built ad hoc (no class of its own to remember it by)."""
    return type(dlg).__module__.split(".", 1)[0] == "PySide6" and not dlg.property("_ctgGeometryKey")


def _own_class(w) -> bool:
    return type(w).__module__.split(".", 1)[0] == "ctgedit"


def _in_compound(w, top) -> bool:
    p = w.parentWidget()
    while p is not None and p is not top:
        if isinstance(p, _COMPOUND):
            return True
        p = p.parentWidget()
    return False


def infer_resize_rule(dlg: QtWidgets.QDialog) -> str:
    """The rule the content gives: 'both' with a list, table, tree, text box,
    canvas, splitter or scroll area (or a widget made to take extra height),
    'width' with a text field, an editable combo box or a slider, else
    'none'."""
    wide = False
    for w in dlg.findChildren(QtWidgets.QWidget):
        try:
            if w.window() is not dlg:  # a popup, a completer, a child window
                continue
            grows = w.sizePolicy().expandingDirections()
        except RuntimeError:
            continue
        if isinstance(w, (QtWidgets.QAbstractScrollArea, QtWidgets.QSplitter)):
            return "both"
        own = _own_class(w)
        if (grows & QtCore.Qt.Vertical) and (own or type(w) in _PLAIN):
            return "both"
        if wide:
            continue
        if isinstance(w, QtWidgets.QLineEdit):
            wide = not _in_compound(w, dlg)
        elif isinstance(w, QtWidgets.QComboBox):
            wide = w.isEditable()
        elif isinstance(w, QtWidgets.QSlider):
            wide = True
        elif own and grows & QtCore.Qt.Horizontal:
            wide = True
    return "width" if wide else "none"


def resize_rule(dlg: QtWidgets.QDialog) -> str:
    """The dialog's resize rule: its '_ctgResize' property, its class' RESIZE,
    else what its content gives (:func:`infer_resize_rule`)."""
    for rule in (dlg.property("_ctgResize"), getattr(dlg, "RESIZE", None)):
        if rule in RESIZE_RULES:
            return rule
    return infer_resize_rule(dlg)


def _frame_extra(dlg: QtWidgets.QWidget) -> QtCore.QSize:
    """What the window frame adds to the client size."""
    frame = dlg.frameGeometry().size() - dlg.size()
    if frame.isEmpty() and QtGui.QGuiApplication.platformName() == "windows":
        frame = QtCore.QSize(16, 39)  # before the frame is known: a caption and borders
    return QtCore.QSize(max(0, frame.width()), max(0, frame.height()))


def _room(dlg: QtWidgets.QWidget) -> QtCore.QSize:
    """The largest client size that fits the dialog's screen (frame included)."""
    screen = dlg.screen() or QtWidgets.QApplication.primaryScreen()
    if screen is None:
        return QtCore.QSize(_QMAX, _QMAX)
    avail = screen.availableGeometry().size()
    frame = _frame_extra(dlg)
    return QtCore.QSize(
        max(200, avail.width() - max(0, frame.width())), max(120, avail.height() - max(0, frame.height()))
    )


def _content_height(lay: QtWidgets.QLayout, width: int) -> int:
    if lay.hasHeightForWidth():
        h = lay.totalHeightForWidth(width)
    else:
        h = lay.totalSizeHint().height()
    return max(h, lay.totalMinimumSize().height())


class _SizeRule(QtCore.QObject):
    """Keep a dialog to its resize rule as its content changes."""

    TYPES = (QtCore.QEvent.Type.LayoutRequest, QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Show)

    def __init__(self, dlg: QtWidgets.QDialog, rule: str):
        super().__init__(dlg)
        self.rule = rule
        #: the minimum the dialog set itself
        self._floor = QtCore.QSize(dlg.minimumSize())
        self._busy = False
        dlg.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        if not self._busy and ev.type() in self.TYPES:
            if not (self.rule == "both" and ev.type() == QtCore.QEvent.Type.Resize):
                try:
                    self.apply(obj)
                except RuntimeError:
                    pass
        return False

    def apply(self, dlg: QtWidgets.QDialog) -> None:
        lay = dlg.layout()
        if lay is None or self._busy:
            return
        self._busy = True
        try:
            room = _room(dlg)
            least = lay.totalMinimumSize()
            if self.rule == "none":
                w = max(lay.totalSizeHint().width(), self._floor.width(), least.width())
                w = min(w, room.width())
                h = min(max(_content_height(lay, w), self._floor.height()), room.height())
                if dlg.minimumSize() != QtCore.QSize(w, h) or dlg.maximumSize() != QtCore.QSize(w, h):
                    dlg.setFixedSize(w, h)
            elif self.rule == "width":
                lo = min(max(self._floor.width(), least.width()), room.width())
                h = min(_content_height(lay, max(dlg.width(), lo)), room.height())
                if dlg.minimumSize() != QtCore.QSize(lo, h):
                    dlg.setMinimumSize(lo, h)
                if dlg.maximumHeight() != h:
                    dlg.setMaximumSize(dlg.maximumWidth(), h)
            else:
                cur = dlg.minimumSize()
                want = cur.expandedTo(least).boundedTo(room)
                if want != cur:
                    dlg.setMinimumSize(want)
        finally:
            self._busy = False


def remembers_size(dlg: QtWidgets.QDialog) -> bool:
    """A dialog of ctgedit's own that the user can resize (Qt's message
    boxes, file and input dialogs keep their own sizes; a 'none' dialog has
    a fixed size)."""
    if _is_qt_dialog(dlg) or _plain_qdialog(dlg) or dlg.property("_ctgNoGeometry"):
        return False
    if dlg.property("_ctgRule") == "none":
        return False
    if dlg.minimumSize() == dlg.maximumSize():
        return False
    if dlg.windowFlags() & QtCore.Qt.MSWindowsFixedSizeDialogHint:
        return False
    lay = dlg.layout()
    return not (lay is not None and lay.sizeConstraint() == QtWidgets.QLayout.SetFixedSize)


def remembers_position(dlg: QtWidgets.QDialog) -> bool:
    """A modeless window of an editor window remembers where it was."""
    if _is_qt_dialog(dlg) or dlg.property("_ctgNoGeometry") or _plain_qdialog(dlg):
        return False
    return not dlg.isModal() and owning_main(dlg) is not None


def _saved_geometry(dlg: QtWidgets.QDialog):
    """(position or None, width, height) the dialog was closed at; 0 = not
    remembered. Stored 'w,h' ('both'), 'w' ('width'), 'x,y,w,h' (a modeless
    window)."""
    from .prefs.settings import app_settings

    raw = app_settings().value(_geometry_key(dlg), "")
    try:
        parts = [int(v) for v in str(raw).split(",")] if raw not in (None, "") else []
    except TypeError, ValueError:
        parts = []
    if len(parts) == 4:
        return QtCore.QPoint(parts[0], parts[1]), max(0, parts[2]), max(0, parts[3])
    if len(parts) == 2:
        return None, max(0, parts[0]), max(0, parts[1])
    if len(parts) == 1:
        return None, max(0, parts[0]), 0
    return None, 0, 0


def _restore_size(dlg: QtWidgets.QDialog, rule: str = "both") -> bool:
    """Restore the dialog's last size, kept on screen."""
    _pos, w, h = _saved_geometry(dlg)
    if w <= 0:
        return False
    room = _room(dlg)
    size = QtCore.QSize(
        min(w, room.width()), min(h, room.height()) if (rule == "both" and h > 0) else dlg.height()
    )
    if size != dlg.size():
        dlg.resize(size)
    return True


def _fit_content(dlg: QtWidgets.QDialog, rule: str) -> None:
    """First open without a remembered size: big enough for the content (every
    field, every tab title), within the screen. Qt opens a window at most two
    thirds of the screen."""
    hint = dlg.layout().totalSizeHint()
    want = dlg.size().expandedTo(hint).boundedTo(_room(dlg))
    if rule == "width":
        want.setHeight(dlg.height())
    if want != dlg.size():
        dlg.resize(want)


def _place_modeless(dlg: QtWidgets.QDialog, main, pos) -> None:
    """A modeless window at its remembered position, else at the right of the
    canvas; the n-th open window of the same kind 24 px lower. Kept on the
    screen."""
    n = 0
    for w in QtWidgets.QApplication.topLevelWidgets():
        try:
            if w is not dlg and type(w) is type(dlg) and w.isVisible() and owning_main(w) is main:
                n += 1
        except RuntimeError:
            continue
    frame = dlg.size() + _frame_extra(dlg)
    fw, fh = frame.width(), frame.height()
    if pos is not None:
        x, y = pos.x(), pos.y()
    else:
        view = getattr(main, "view", None)
        try:
            r = QtCore.QRect(view.mapToGlobal(QtCore.QPoint(0, 0)), view.size())
        except AttributeError, RuntimeError, TypeError:
            r = main.frameGeometry()
        x = r.right() + 1 - PLACE_MARGIN - fw
        y = r.top() + PLACE_MARGIN
    y += PLACE_STEP * n
    screen = (
        QtGui.QGuiApplication.screenAt(QtCore.QPoint(x + fw // 2, y + 8))
        or main.screen()
        or QtWidgets.QApplication.primaryScreen()
    )
    if screen is not None:
        avail = screen.availableGeometry()
        x = max(avail.left(), min(x, avail.right() + 1 - fw))
        y = max(avail.top(), min(y, avail.bottom() + 1 - fh))
    dlg.move(x, y)


def _save_geometry(dlg: QtWidgets.QDialog, rule: str, opened: QtCore.QSize, opened_pos, place: bool) -> None:
    """What the user changed when the dialog closes: its size once resized
    (a dialog that sizes itself to its content keeps doing so until then),
    the width only for 'width', nothing for 'none'; a modeless window's
    position once moved."""
    from .prefs.settings import app_settings

    try:
        s = dlg.size()
        pos, sw, _sh = _saved_geometry(dlg)
        if rule == "none":
            resized = False
        elif rule == "width":
            resized = s.width() != opened.width() or sw > 0
        else:
            resized = s != opened or sw > 0
        w = s.width() if resized else 0
        h = s.height() if resized and rule == "both" else 0
        p = dlg.pos() if place else None
        moved = place and p != opened_pos
        if not (resized or moved):
            return
        # only a position the user chose is kept: a window that opened a step
        # lower (another one of its kind was open) and was not moved keeps the
        # stored position (else they drift down), and so does the same form
        # shown modal (the genie paste form shares the edit form's key)
        keep = p if moved else pos
        if keep is not None:
            value = "%d,%d,%d,%d" % (keep.x(), keep.y(), w, h)
        else:
            value = "%d" % w if rule == "width" else "%d,%d" % (w, h)
        app_settings().setValue(_geometry_key(dlg), value)
    except RuntimeError:  # the dialog is being deleted
        pass


def _size_dialog(dlg: QtWidgets.QDialog) -> None:
    """The resize rule, the remembered size and, for a modeless window, its
    position (first show)."""
    if _is_qt_dialog(dlg):
        return
    own_geometry = bool(dlg.property("_ctgNoGeometry"))
    lay = dlg.layout()
    if lay is None:
        # no layout: the dialog sizes itself; only its size is remembered
        if remembers_size(dlg):
            _restore_size(dlg)
            opened = QtCore.QSize(dlg.size())
            dlg.finished.connect(lambda _r, d=dlg, o=opened: _save_geometry(d, "both", o, None, False))
        return
    rule = resize_rule(dlg)
    dlg.setProperty("_ctgRule", rule)
    keeper = _SizeRule(dlg, rule)
    dlg._ctg_size_rule = keeper  # (the dialog keeps it; it keeps no dialog)
    keeper.apply(dlg)
    if own_geometry:
        return
    size_memory = remembers_size(dlg)
    place = remembers_position(dlg)
    pos, w, _h = _saved_geometry(dlg) if (size_memory or place) else (None, 0, 0)
    if not (size_memory and w > 0 and _restore_size(dlg, rule)) and rule != "none":
        _fit_content(dlg, rule)
    if place:
        _place_modeless(dlg, owning_main(dlg), pos)
    if size_memory or place:
        opened, at = QtCore.QSize(dlg.size()), QtCore.QPoint(dlg.pos())

        def save(_r=None, d=dlg, r=rule, o=opened, p=at, m=place):
            _save_geometry(d, r, o, p, m)

        dlg.finished.connect(save)
        if place:
            # a window that is hidden, never closed (the magnifier), keeps
            # its size and position too
            dlg._ctg_hide_saver = _HideSaver(dlg, save)


def restore_window_geometry(win: QtWidgets.QWidget) -> bool:
    """A modeless window that is not a dialog (the Simulate window's Inspector): the size and
    place it had when it was last hidden (geometry/<class name>, 'x,y,w,h' as a modeless
    dialog's), kept on the screen. False when none is remembered (it keeps its own)."""
    pos, w, h = _saved_geometry(win)
    if w <= 0 or h <= 0:
        return False
    room = _room(win)
    win.resize(min(w, room.width()), min(h, room.height()))
    if pos is not None:
        frame = win.size() + _frame_extra(win)
        screen = (
            QtGui.QGuiApplication.screenAt(QtCore.QPoint(pos.x() + frame.width() // 2, pos.y() + 8))
            or win.screen()
            or QtWidgets.QApplication.primaryScreen()
        )
        x, y = pos.x(), pos.y()
        if screen is not None:
            avail = screen.availableGeometry()
            x = max(avail.left(), min(x, avail.right() + 1 - frame.width()))
            y = max(avail.top(), min(y, avail.bottom() + 1 - frame.height()))
        win.move(x, y)
    return True


def save_window_geometry(win: QtWidgets.QWidget) -> None:
    """:func:`restore_window_geometry`'s size and place, as the window is now."""
    from .prefs.settings import app_settings

    try:
        p, s = win.pos(), win.size()
        app_settings().setValue(_geometry_key(win), "%d,%d,%d,%d" % (p.x(), p.y(), s.width(), s.height()))
    except RuntimeError:  # the window is being deleted
        pass


class _HideSaver(QtCore.QObject):
    """Saves a modeless window's geometry when it is hidden by the program
    (not when the system hides it with a minimised editor)."""

    def __init__(self, dlg: QtWidgets.QDialog, save: Callable[[], None]):
        super().__init__(dlg)
        self._save = save
        dlg.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        if ev.type() == QtCore.QEvent.Type.Hide and not ev.spontaneous():
            self._save()
        return False


def _decorate_dialog(dlg: QtWidgets.QDialog) -> None:
    """First show of a dialog: its resize rule, its saved size (and
    position), its F1 topic and a Help button."""
    if dlg.property("_ctgHelpDone"):
        return
    dlg.setProperty("_ctgHelpDone", True)
    if dialog_topic(dlg):
        # (first: the dialog is then sized with its Help button)
        box = dlg.findChild(QtWidgets.QDialogButtonBox)
        if box is not None and box.button(QtWidgets.QDialogButtonBox.Help) is None:
            b = box.addButton(QtWidgets.QDialogButtonBox.Help)
            b.setToolTip("Open the help page for this dialog (F1)")
            box.helpRequested.connect(lambda d=dlg: show_help(dialog_topic(d), main=owning_main(d)))
    try:
        _size_dialog(dlg)
    except RuntimeError:
        pass


def set_help(obj, topic: str):
    """Attach a help topic ('page' or 'page#anchor') to a widget / action."""
    obj.setProperty(HELP_PROP, topic)
    return obj


def help_of(obj) -> str:
    try:
        v = obj.property(HELP_PROP)
    except RuntimeError:
        return ""
    return v or ""


def topic_for(widget: Optional[QtWidgets.QWidget]) -> str:
    w = widget
    while w is not None:
        t = help_of(w)
        if t:
            return t
        if isinstance(w, QtWidgets.QDialog):
            t = dialog_topic(w)
            if t:
                return t
        # a ribbon / toolbar button shares its QAction with the menus
        if isinstance(w, QtWidgets.QToolButton) and w.defaultAction() is not None:
            t = help_of(w.defaultAction())
            if t:
                return t
        w = w.parentWidget()
    return ""


#: the editor window the manual was last opened from (its action: links run there)
_last_main: list = []


def _alive(obj) -> bool:
    if obj is None:
        return False
    try:
        import shiboken6

        return shiboken6.isValid(obj)
    except Exception:  # noqa: BLE001 -- no shiboken: assume it is
        return True


def owning_main(widget):
    """The editor window ``widget`` belongs to (a dialog's parent chain), or
    None."""
    w = widget
    try:
        while w is not None:
            if hasattr(w, "run_help_action"):
                return w
            w = w.parentWidget()
    except RuntimeError:
        return None
    return None


def action_target():
    """The editor window an ``action:`` link runs in, looked up when the link
    is clicked: the window the manual was last opened from while it is still
    open, else any open editor window (a window that was closed since is never
    used -- its actions are gone)."""
    try:
        from .main_window import MainWindow

        open_wins = [w for w in MainWindow._instances if _alive(w)]
    except Exception:  # noqa: BLE001
        open_wins = []
    # a closed editor window leaves MainWindow._instances but can stay alive
    # (hidden): it is never a target, even when no other window is open
    for ref in _last_main:
        w = ref()
        if _alive(w) and any(w is o for o in open_wins):
            return w
    visible = [w for w in open_wins if w.isVisible()]
    return (visible or open_wins or [None])[0]


def _help_status(message: str) -> None:
    """A message in the manual's own status bar (where the reader looks)."""
    from .dialog.help_window import HelpWindow

    win = getattr(HelpWindow, "_inst", None)
    if _alive(win):
        win.statusBar().showMessage(message, 8000)


def run_action(name: str) -> None:
    """An ``action:<name>`` link of the manual (HelpWindow.actionRequested)."""
    target = action_target()
    if target is None:
        # the manual outlived every editor window: say so instead of doing nothing
        _help_status(
            "This link runs a command in the editor, but no editor window is open. "
            "Open a window, then click the link again."
        )
        return
    try:
        act = target.help_action(name) if hasattr(target, "help_action") else None
        if act is not None and not act.isEnabled():
            # the editor says so in its own status bar too, behind the manual
            _help_status(
                "'%s' is not available now. Open a page or select objects first."
                % act.text().replace("&", "").split("\t")[0]
            )
        target.run_help_action(name)
    except RuntimeError:  # the window went away while the link was followed
        pass


def show_help(topic: str = "index", parent=None, main=None) -> "QtWidgets.QMainWindow":
    """Open (or raise) the manual on ``topic``; ``main`` = the editor window
    it is opened from (its ``action:`` links run there)."""
    from .. import help as H
    from .dialog.help_window import HelpWindow

    win = HelpWindow.instance(None)
    if main is not None and _alive(main):
        try:
            _last_main[:] = [weakref.ref(main)]
        except TypeError:  # not weak-referenceable: keep it plainly
            _last_main[:] = [lambda m=main: m]
    if not getattr(win, "_hooked", False):
        win.actionRequested.connect(run_action)
        win._hooked = True
    page, _, anchor = (topic or "index").partition("#")
    if not H.exists(page):
        page, anchor = "index", ""
    win.show_topic(page, anchor)
    win.show()
    win.raise_()
    win.activateWindow()
    if anchor:
        # (where the reader landed: the heading lights up for a moment)
        win.flash_anchor(anchor)
    return win


# ----------------------------------------------------------------- 'show:' links
#: the pages of the Settings window (a help text's 'Settings > <page>' links to one;
#: tests/test_help_search.py keeps it in step with the window)
SETTINGS_PAGES = (
    "Editing",
    "Canvas",
    "Drawing",
    "Appearance",
    "Keyboard",
    "Ribbon",
    "Pages and libraries",
    "Simulate",
    "Advanced",
)
#: how long a ribbon button lights up for a 'show:' link (ms)
FLASH_MS = 1500
_TAG = re.compile(r"(<[^>]+>)")
_STOP = re.compile(r"[.,;:()\"\[\]!?]")


def _norm_label(text: str) -> str:
    t = (text or "").replace("&&", "\x00").replace("&", "").replace("\x00", "&")
    t = t.split("\t")[0].replace("…", "").replace("...", "")
    return " ".join(t.split()).lower()


def menu_index(main) -> Dict[str, QtGui.QAction]:
    """'view > smart guides' -> the command of the editor window ``main`` (its menu bar)."""
    out: Dict[str, QtGui.QAction] = {}
    if main is None:
        return out
    from .dialog.palette import harvest_actions

    try:
        for a, crumb, label in harvest_actions(main.menuBar()):
            parts = [_norm_label(p) for p in crumb.split(" > ")] + [_norm_label(label)]
            out.setdefault(" > ".join(parts), a)
    except RuntimeError:  # swallow-ok: the window went while the page was rendered
        return out
    return out


def _resolve_path(index: Dict[str, QtGui.QAction], parts: List[str]):
    """(action, words used of the last part) of the longest menu path ``parts`` starts
    with: the last part may go on with other words ('Smart Guides or the button')."""
    head = [_norm_label(p) for p in parts[:-1]]
    words = parts[-1].split()
    for n in range(len(words), 0, -1):
        key = " > ".join(head + [_norm_label(" ".join(words[:n]))])
        a = index.get(key)
        if a is not None:
            return a, n
    return None, 0


def link_menu_paths(body: str, main) -> Tuple[str, list]:
    """The page HTML with every menu path the editor's menus have ('View > Smart Guides')
    and every 'Settings > <page>' turned into a 'show:<n>' link; ``targets[n]`` is
    ('menu', action, path) or ('settings', page, words after it). Links, code and headings
    are left as they are."""
    index = menu_index(main)
    tops = sorted({k.split(" > ")[0] for k in index}, key=len, reverse=True)
    if not index and not SETTINGS_PAGES:
        return body, []
    targets: list = []
    out = []
    skip = 0
    names = [re.escape(t) for t in tops] + ["settings"]
    start_rx = re.compile(r"(?<![\w>])(%s) &gt; " % "|".join(names), re.IGNORECASE)
    for part in _TAG.split(body):
        if part.startswith("<"):
            low = part.lower()
            if re.match(r"<(a|code|pre|h[1-6])[\s>]", low):
                skip += 1
            elif re.match(r"</(a|code|pre|h[1-6])>", low):
                skip = max(0, skip - 1)
            out.append(part)
            continue
        if skip or "&gt;" not in part:
            out.append(part)
            continue
        out.append(_link_text(part, index, start_rx, targets))
    return "".join(out), targets


def _link_text(text: str, index, start_rx, targets: list) -> str:
    out = []
    pos = 0
    while True:
        m = start_rx.search(text, pos)
        if m is None:
            break
        top = m.group(1)
        rest = text[m.end() :]
        if top.lower() == "settings":
            page = next(
                (
                    p
                    for p in sorted(SETTINGS_PAGES, key=len, reverse=True)
                    if rest.lower().startswith(p.lower()) and not rest[len(p) : len(p) + 1].isalnum()
                ),
                None,
            )
            if page is None:
                out.append(text[pos : m.end()])
                pos = m.end()
                continue
            end = m.end() + len(page)
            hint = ""
            more = text[end:]
            if more.startswith(" &gt; "):
                tail = more[len(" &gt; ") :]
                if tail.startswith('"'):
                    q = tail.find('"', 1)
                    hint = tail[1:q] if q > 0 else ""
                else:
                    stop = _STOP.search(tail)
                    hint = (tail[: stop.start()] if stop else tail).strip()
                    hint = " ".join(hint.replace("&gt;", ">").split()[:8])
            n = len(targets)
            targets.append(("settings", page, hint))
            out.append(text[pos : m.start()])
            out.append('<a href="show:%d">%s</a>' % (n, text[m.start() : end]))
            pos = end
            continue
        segs = rest.split(" &gt; ")
        parts = [top]
        used = 0
        for k, seg in enumerate(segs):
            stop = _STOP.search(seg)
            chunk = seg[: stop.start()] if stop else seg
            if "&" in chunk:
                chunk = chunk[: chunk.index("&")]
            parts.append(chunk)
            if stop or k == len(segs) - 1:
                break
        action, n_words = None, 0
        # the longest path first, then shorter ones ('View > Grid' when 'Grid > x' is not)
        for cut in range(len(parts), 1, -1):
            action, n_words = _resolve_path(index, parts[:cut])
            if action is not None:
                parts = parts[:cut]
                break
        if action is None:
            out.append(text[pos : m.end()])
            pos = m.end()
            continue
        last_words = parts[-1].split()[:n_words]
        shown = " &gt; ".join(parts[:-1] + [" ".join(last_words)])
        # the visible text as it stands in the page (its spacing kept)
        end = text.find(shown, m.start())
        if end != m.start():
            out.append(text[pos : m.end()])
            pos = m.end()
            continue
        end = m.start() + len(shown)
        n = len(targets)
        targets.append(("menu", action, shown.replace("&gt;", ">")))
        out.append(text[pos : m.start()])
        out.append('<a href="show:%d">%s</a>' % (n, shown))
        pos = end
        used += 1
    out.append(text[pos:])
    return "".join(out)


def show_command(main, action, run: bool = False, front: bool = True) -> str:
    """A 'show:' link: open the command's menu with it highlighted."""
    if main is None or action is None or not _alive(action):
        return ""
    try:
        if front or not run:
            main.raise_()
            main.activateWindow()
        if run:
            if action.isEnabled():
                action.trigger()
                return "ran"
            main.statusBar().showMessage(
                "%s is not available right now." % _norm_label(action.text()).capitalize(), 5000
            )
            return ""
        flashed = flash_action(main, action)
        if open_menu_at(main, action):
            return "menu"
        return "flash" if flashed else ""
    except RuntimeError:  # swallow-ok: the window went while the link was followed
        return ""


def open_menu_at(main, action) -> bool:
    """Open the menu that holds ``action`` with it highlighted (its submenus opened on the
    way): from the menu bar, or at the top left of the window in Focus Mode."""
    from .dialog.palette import menu_chain

    mb = main.menuBar()
    chain = menu_chain(mb, action)
    if not chain:
        return False
    _parent, top_action = chain[0]
    import shiboken6

    menus = {}
    for m in mb.findChildren(QtWidgets.QMenu):
        try:
            menus[shiboken6.getCppPointer(m.menuAction())[0]] = m
        except RuntimeError:
            continue
    top_menu = menus.get(shiboken6.getCppPointer(top_action)[0])
    if top_menu is None:
        return False
    if mb.isVisible() and mb.height() > 0:
        mb.setActiveAction(top_action)
        if not top_menu.isVisible():
            top_menu.popup(mb.mapToGlobal(mb.actionGeometry(top_action).bottomLeft()))
    else:
        top_menu.popup(main.mapToGlobal(QtCore.QPoint(8, 8)))
    steps = [(menu, a) for menu, a in chain[1:]]

    def step(k: int = 0) -> None:
        if k >= len(steps):
            return
        menu, a = steps[k]
        try:
            menu.setActiveAction(a)
        except RuntimeError:  # swallow-ok: the menu closed meanwhile
            return
        if k + 1 < len(steps):
            QtCore.QTimer.singleShot(0, menu, lambda: step(k + 1))

    step(0)
    return True


def flash_action(main, action, ms: int = FLASH_MS) -> bool:
    """Light up every visible ribbon / toolbar button of ``action`` in ``main`` for ``ms``."""
    done = False
    for b in main.findChildren(QtWidgets.QToolButton):
        try:
            if b.defaultAction() is action and b.isVisible():
                flash_widget(b, ms)
                done = True
        except RuntimeError:  # swallow-ok: a button that went
            continue
    return done


class _Flash(QtWidgets.QWidget):
    """An accent ring over a widget for a moment (a 'show:' link's ribbon button)."""

    def __init__(self, target: QtWidgets.QWidget, ms: int):
        host = target.window()
        super().__init__(host)
        self.setObjectName("HelpFlash")
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        tl = target.mapTo(host, QtCore.QPoint(0, 0))
        self.setGeometry(QtCore.QRect(tl, target.size()).adjusted(-3, -3, 3, 3))
        self.show()
        self.raise_()
        QtCore.QTimer.singleShot(int(ms), self, self.deleteLater)

    def paintEvent(self, _e) -> None:  # noqa: N802 -- Qt naming
        from .style.theme import COLORS

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        c = QtGui.QColor(COLORS["accent"])
        fill = QtGui.QColor(c)
        fill.setAlpha(50)
        p.setBrush(fill)
        p.setPen(QtGui.QPen(c, 2))
        p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), 5, 5)
        p.end()


def flash_widget(widget: QtWidgets.QWidget, ms: int = FLASH_MS) -> QtWidgets.QWidget:
    return _Flash(widget, ms)


def follow_show(target: tuple, shift: bool = False) -> str:
    """A 'show:' link clicked in the manual (``target`` from :func:`link_menu_paths`)."""
    main = action_target()
    if main is None:
        _help_status("This link shows a command in the editor, but no editor window is open.")
        return ""
    kind = target[0]
    if kind == "menu":
        return show_command(main, target[1], run=shift)
    if kind == "settings":
        _page, hint = target[1], target[2]
        search = settings_label_for(main, hint) if hint else ""
        from .dialog.palette import show_setting

        main.raise_()
        main.activateWindow()
        QtCore.QTimer.singleShot(0, main, lambda: show_setting(main, _page, search))
        return "settings"
    return ""


def settings_label_for(main, hint: str) -> str:
    """The Settings row a help text names after 'Settings > <page> > ': the longest start of
    ``hint`` that is a row's label ('' = none)."""
    from .dialog.palette import settings_index

    labels = {e.label.lower(): e.label for e in settings_index(main)}
    words = (hint or "").replace("**", "").strip().strip('"').split()
    for n in range(len(words), 0, -1):
        got = labels.get(" ".join(words[:n]).lower().rstrip(":"))
        if got:
            return got
    if len(words) >= 2:
        start = " ".join(words[:2]).lower()
        for low, label in labels.items():
            if low.startswith(start):
                return label
    return ""


def add_help_button(
    dialog: QtWidgets.QDialog, buttons: Optional[QtWidgets.QDialogButtonBox], topic: str
) -> None:
    """Help button on a dialog's button box + the dialog's F1 topic."""
    set_help(dialog, topic)
    if buttons is None:
        return
    b = buttons.addButton(QtWidgets.QDialogButtonBox.Help)
    b.setToolTip("Open the help page for this dialog (F1)")
    buttons.helpRequested.connect(
        lambda: show_help(topic_for(dialog.focusWidget()) or topic, main=owning_main(dialog))
    )


class KeyTrigger(QtCore.QObject):
    """Run ``on_key`` instead of the action's trigger when its shortcut fires."""

    def __init__(self, action: QtGui.QAction, on_key: Callable[[], None]):
        super().__init__(action)
        self._on_key = on_key
        action.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        if ev.type() == QtCore.QEvent.Type.Shortcut:
            self._on_key()
            return True
        return False


#: a widget that takes every key as a key to record sets this property (the keys search box
#: while Record Keys is on): F1 is recorded there, not answered with the manual
RECORDS_KEYS_PROP = "ctgRecordsKeys"


def records_keys(w) -> bool:
    """``w`` (the focus widget) records the keys pressed: a key field (QKeySequenceEdit, or
    the line edit inside one) or a widget with :data:`RECORDS_KEYS_PROP`."""
    try:
        while w is not None:
            if isinstance(w, QtWidgets.QKeySequenceEdit) or w.property(RECORDS_KEYS_PROP):
                return True
            if w.isWindow():
                return False
            w = w.parentWidget()
    except RuntimeError:  # swallow-ok: the widget went while we looked
        pass
    return False


class _F1Filter(QtCore.QObject):
    #: the event types it handles (what the editor's combined application
    #: filter hands over, main_window._AppEventFilter)
    TYPES = frozenset({QtCore.QEvent.Type.Show, QtCore.QEvent.Type.KeyPress})

    def __init__(self, fallback: Callable[[], str], main):
        super().__init__()
        self._fallback = fallback
        self._main = main

    def eventFilter(self, obj, ev) -> bool:
        return self.handle(obj, ev, ev.type())

    def handle(self, obj, ev, t) -> bool:
        if t == QtCore.QEvent.Show and isinstance(obj, QtWidgets.QDialog):
            try:
                _decorate_dialog(obj)
            except RuntimeError:
                pass
            return False
        if (
            t == QtCore.QEvent.KeyPress
            and ev.key() == QtCore.Qt.Key_F1
            and not (ev.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier))
        ):
            if records_keys(QtWidgets.QApplication.focusWidget()):
                return False  # (a key field or Record Keys takes F1 as a key)
            if ev.isAutoRepeat():
                return True
            main = owning_main(QtWidgets.QApplication.activeWindow()) or (
                self._main if _alive(self._main) else None
            )
            show_help(self.resolve(), main=main)
            return True
        return False

    def resolve(self) -> str:
        popup = QtWidgets.QApplication.activePopupWidget()
        if isinstance(popup, QtWidgets.QMenu):
            a = popup.activeAction()
            if a is not None and help_of(a):
                return help_of(a)
            if help_of(popup):
                return help_of(popup)
        # a command button under the mouse whose tip says 'F1 for help' wins over the focus
        from .style.tips import pointer_topic

        t = pointer_topic()
        if t:
            return t
        t = topic_for(QtWidgets.QApplication.focusWidget())
        if t:
            return t
        t = topic_for(QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos()))
        if t:
            return t
        t = topic_for(QtWidgets.QApplication.activeWindow())
        if t:
            return t
        try:
            return self._fallback() or "index"
        except Exception:  # noqa: BLE001 -- help must always open
            return "index"


def install(
    app: QtWidgets.QApplication, fallback: Callable[[], str], main=None, dispatcher=None
) -> QtCore.QObject:
    """Route F1 through the whole application (menus and modal dialogs too).
    ``dispatcher`` = the editor's combined application filter (``add(handler,
    types)``): the handler joins it instead of being a filter of its own."""
    f = _F1Filter(fallback, main)
    if dispatcher is not None:
        dispatcher.add(f, f.TYPES)
    else:
        app.installEventFilter(f)
    app._ctg_f1 = f  # keep it alive
    return f
