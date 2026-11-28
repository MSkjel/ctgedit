"""Every dialog of the editor, built on a copy of the fake install with a page open, and the
layout check its rows go through (tests/test_dialog_layout.py, tools/dialog_gallery.py)."""

from __future__ import annotations

import os
from typing import Callable, Dict, List

from PySide6 import QtCore, QtGui, QtWidgets


class Context:
    """What the builders need: the window (FxShapes open), the install's root."""

    def __init__(self, window, root: str):
        self.w = window
        self.root = root

    @property
    def doc(self):
        return self.w.doc

    def project(self, name: str = "FxMain") -> str:
        return os.path.join(self.root, "User", name)

    def page(self, name: str, project: str = "FxMain") -> str:
        return os.path.join(self.project(project), name + ".ctg")

    def file(self, project: str, name: str) -> str:
        return os.path.join(self.project(project), name)


def _index_of_kind(doc, kind: str) -> int:
    return next(i for i, o in enumerate(doc.page.objects) if o.kind.value == kind)


def _png(path: str) -> str:
    img = QtGui.QImage(16, 16, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor("#3080c0"))
    img.save(path)
    return path


# -- the builders: name -> (context) -> the dialog ------------------------------------------
def _builders() -> Dict[str, Callable]:
    B: Dict[str, Callable] = {}

    def add(name):
        def deco(fn):
            B[name] = fn
            return fn

        return deco

    @add("About")
    def _(c):
        from ctgedit.ui.dialog.about import AboutDialog

        return AboutDialog(c.w)

    @add("Insert ActiveX")
    def _(c):
        from ctgedit.ui.dialog.activex_insert import InsertActiveXDialog

        return InsertActiveXDialog(c.project(), c.w)

    @add("Align")
    def _(c):
        from ctgedit.ui.dialog.arrange_dialogs import AlignDialog

        return AlignDialog(c.w)

    @add("Adjust Position")
    def _(c):
        from ctgedit.ui.dialog.arrange_dialogs import AdjustPositionDialog

        return AdjustPositionDialog(parent=c.w)

    @add("Space Equally")
    def _(c):
        from ctgedit.ui.dialog.arrange_dialogs import SpaceEquallyDialog

        return SpaceEquallyDialog(c.w)

    @add("Arrange in Grid")
    def _(c):
        from ctgedit.ui.dialog.arrange_dialogs import ArrangeGridDialog

        return ArrangeGridDialog(c.w)

    @add("Bitmap Size")
    def _(c):
        from ctgedit.ui.dialog.bitmap_editor import BitmapSizeDialog

        return BitmapSizeDialog(32, 24, c.w)

    @add("Bitmap Editor")
    def _(c):
        from ctgedit.ui.dialog.bitmap_editor import BitmapEditorDialog

        return BitmapEditorDialog(bytes([0, 128, 255, 255]) * (16 * 16), 16, 16, c.w)

    @add("Transparent Paste")
    def _(c):
        from ctgedit.ui.dialog.bitmap_import import TransparentPasteDialog

        return TransparentPasteDialog(c.w)

    @add("Check Project")
    def _(c):
        from ctgedit.ui.dialog.check_project import CheckProjectDialog

        return CheckProjectDialog(c.w, c.project())

    @add("Swap Colours")
    def _(c):
        from ctgedit.ui.dialog.colour_dialogs import SwapColorsDialog

        return SwapColorsDialog(c.w)

    @add("Adjust Colours")
    def _(c):
        from ctgedit.ui.dialog.colour_dialogs import AdjustColorsDialog

        return AdjustColorsDialog(c.w)

    @add("Go To Object")
    def _(c):
        from ctgedit.ui.dialog.dialogs import GotoObjectDialog

        return GotoObjectDialog(c.doc, c.w)

    @add("Number Format")
    def _(c):
        from ctgedit.ui.dialog.dialogs import NumberExpressionDialog

        return NumberExpressionDialog(parent=c.w, doc=c.doc)

    @add("Grid Setup")
    def _(c):
        from ctgedit.ui.dialog.dialogs import GridSetupDialog

        return GridSetupDialog(10, True, True, c.w)

    @add("Guidelines")
    def _(c):
        from ctgedit.ui.dialog.dialogs import GuidelinesDialog

        return GuidelinesDialog([100, 300], [200], True, True, c.w)

    @add("Update Pages")
    def _(c):
        from ctgedit.ui.dialog.dialogs import UpdatePagesDialog

        return UpdatePagesDialog(c.project(), c.w)

    @add("Use Template")
    def _(c):
        from ctgedit.ui.dialog.dialogs import UseTemplateDialog

        return UseTemplateDialog(c.project(), parent=c.w)

    @add("Page Defaults")
    def _(c):
        from ctgedit.ui.dialog.dialogs import PageDefaultsDialog

        return PageDefaultsDialog(c.project(), c.w)

    @add("Find and Replace")
    def _(c):
        from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

        return FindReplaceDialog(c.doc, c.w, replace_mode=True)

    @add("Page History")
    def _(c):
        from ctgedit.ui.dialog.history_versions import PageHistoryDialog

        return PageHistoryDialog(c.doc, c.w)

    @add("Copy to Library")
    def _(c):
        from ctgedit.ui.dialog.library_dialogs import CopyToLibraryDialog

        return CopyToLibraryDialog(c.project(), c.w)

    @add("Hot Spot")
    def _(c):
        from ctgedit.ui.dialog.library_dialogs import HotSpotDialog

        return HotSpotDialog(0, 0, c.w)

    @add("Pack Libraries")
    def _(c):
        from ctgedit.ui.dialog.pack_dialog import PackLibrariesDialog

        return PackLibrariesDialog(c.w, c.doc.path)

    @add("Pack Database")
    def _(c):
        from ctgedit.ui.dialog.pack_dialog import PackDatabaseDialog

        return PackDatabaseDialog(c.w, c.doc.path)

    @add("Page Properties")
    def _(c):
        from ctgedit.ui.dialog.page_props import PagePropertiesDialog
        from ctgedit.io.project import Project

        return PagePropertiesDialog(c.doc, Project(c.project()), c.w)

    @add("Picture Import")
    def _(c):
        from ctgedit.ui.dialog.bitmap_import import open_source
        from ctgedit.ui.dialog.picture_import import PictureImportDialog

        return PictureImportDialog([open_source(_png(os.path.join(c.root, "gallery.png")))], c.w)

    @add("Rename Objects")
    def _(c):
        from ctgedit.ui.dialog.rename_dialog import RenameObjectsDialog

        return RenameObjectsDialog(c.w, c.doc, [0, 1])

    @add("Rename Pages")
    def _(c):
        from ctgedit.ui.dialog.rename_pages import RenamePagesDialog

        return RenamePagesDialog(c.w, c.project(), ["FxShapes"])

    @add("Replace Genie")
    def _(c):
        from ctgedit.ui.dialog.replace_genie import ReplaceGenieDialog

        return ReplaceGenieDialog(c.doc, parent=c.w)

    @add("Select by Tag")
    def _(c):
        from ctgedit.ui.dialog.select_dialogs import SelectByTagDialog

        return SelectByTagDialog(c.w)

    @add("Keyboard Shortcuts")
    def _(c):
        from ctgedit.ui.dialog.shortcuts_window import ShortcutsWindow

        return ShortcutsWindow(c.w)

    @add("Run Results")
    def _(c):
        from ctgedit.ui.jobs.run_results import RunResultsDialog

        return RunResultsDialog(
            "Update Pages", ["12 pages updated, 3 left as they were.", "", "FxShapes: open in a tab."], c.w
        )

    @add("Cicode Box")
    def _(c):
        from ctgedit.ui.objprops.cicode_box import CicodeBoxDialog
        from ctgedit.ui.objprops.expression import ExpressionContext

        return CicodeBoxDialog('PageDisplay("FxShapes")', ExpressionContext(), parent=c.w)

    @add("Insert Tag")
    def _(c):
        from ctgedit.ui.objprops.expression import ExpressionContext, TagPickerDialog

        return TagPickerDialog(c.w, ExpressionContext(project_dir=c.project()))

    @add("Insert Function")
    def _(c):
        from ctgedit.ui.objprops.expression import ExpressionContext, FunctionPickerDialog

        return FunctionPickerDialog(c.w, ExpressionContext(project_dir=c.project()))

    @add("Key Sequence")
    def _(c):
        from ctgedit.ui.objprops.input import KeySequenceDialog

        return KeySequenceDialog(c.w)

    @add("Object Properties")
    def _(c):
        from ctgedit.ui.objprops.sheet import ObjectPropertiesDialog

        return ObjectPropertiesDialog(c.doc, _index_of_kind(c.doc, "rectangle"), c.w)

    @add("Equipment Picker")
    def _(c):
        from ctgedit.ui.objprops.expression import ExpressionContext
        from ctgedit.ui.panels.sa_pickers import EquipmentPickerDialog

        return EquipmentPickerDialog(c.w, ExpressionContext(project_dir=c.project()))

    @add("Menu Item Picker")
    def _(c):
        from ctgedit.ui.panels.sa_pickers import MenuItemPickerDialog

        return MenuItemPickerDialog(c.project(), parent=c.w)

    @add("Manage Styles")
    def _(c):
        from ctgedit.ui.panels.styles_gallery import ManageStylesDialog

        return ManageStylesDialog(c.w)

    @add("Settings")
    def _(c):
        from ctgedit.ui.prefs.settings_dialog import SettingsDialog

        return SettingsDialog(c.w)

    @add("Export Settings")
    def _(c):
        from ctgedit.ui.prefs.settings_transfer import SettingsTransferDialog

        return SettingsTransferDialog(c.w)

    @add("Simulate Settings")
    def _(c):
        from ctgedit.ui.simulate.settings_dialog import SimSettingsDialog

        return SimSettingsDialog(c.w)

    @add("Navigation Buttons")
    def _(c):
        from ctgedit.ui.editor.navbar import NavBarCustomiseDialog

        return NavBarCustomiseDialog(c.w.navbar, c.w)

    @add("Saved Views")
    def _(c):
        from ctgedit.ui.editor.saved_views import ManageSavedViewsDialog

        return ManageSavedViewsDialog(c.w)

    @add("Underlay")
    def _(c):
        from ctgedit.ui.editor.underlay_panel import UnderlayDialog

        return UnderlayDialog(c.w)

    @add("Page Browser")
    def _(c):
        from ctgedit.ui.browser.dialog import PageBrowserDialog

        return PageBrowserDialog(parent=c.w, start_path=c.doc.path)

    @add("Restore Backup")
    def _(c):
        import time

        from ctgedit.infra.backups import BackupInfo
        from ctgedit.ui.recovery import RestoreBackupDialog

        return RestoreBackupDialog(
            [BackupInfo("x", "update-pages", time.time(), [{"page": "FxShapes"}])], c.w
        )

    @add("Save Changes")
    def _(c):
        from ctgedit.ui.dialog.save_changes import SaveChangesDialog

        return SaveChangesDialog([(c.doc.path, "FxShapes", "FxMain", "2 changes", c.project(), True)], c.w)

    @add("Symbol Set")
    def _(c):
        c.w.open_document(c.page("FxSymbols2"))
        return c.w.symbol_set_properties(_index_of_kind(c.doc, "symbol_set"))

    @add("Genie Parameters")
    def _(c):
        c.w.open_document(c.page("FxGenies"))
        root = _index_of_kind(c.doc, "genie")
        c.w._edit_genie(root, c.doc.page.objects[root])
        return c.w._modeless_for("genie", root)

    @add("Genie Form")
    def _(c):
        lib = c.file("FxLib", "fxgenie.ctm")
        c.w._open_genie_form(lib, "fx_pump", c.project())
        return next(
            w
            for w in QtWidgets.QApplication.topLevelWidgets()
            if type(w).__name__ == "GenieFormEditor" and w.isVisible()
        )

    @add("Element Users")
    def _(c):
        from ctgedit.io import genieusage as GU
        from ctgedit.ui.dialog.library_dialogs import ElementUsersDialog

        lib = c.file("FxLib", "fxgenie.ctm")
        return ElementUsersDialog(lib, "fx_pump", GU.element_users(lib, "fx_pump"), c.w)

    @add("Duplicate Pictures")
    def _(c):
        from ctgedit.ui.dialog.duplicate_pictures import DuplicatePicturesDialog

        return DuplicatePicturesDialog(c.w, c.project())

    @add("Recover Changes")
    def _(c):
        from ctgedit.ui.recovery import RecoveryDialog

        return RecoveryDialog([], c.w)

    @add("Paste Special")
    def _(c):
        from ctgedit.ui.dialog.dialogs import PasteSpecialDialog

        return PasteSpecialDialog(["Picture (PNG)", "Picture (metafile)", "Text"], c.w)

    return B


BUILDERS = _builders()


# -- the layout check ---------------------------------------------------------------------
_TEXT_TYPES = (QtWidgets.QLabel, QtWidgets.QAbstractButton)
_LEAF_TYPES = (
    QtWidgets.QLabel,
    QtWidgets.QAbstractButton,
    QtWidgets.QLineEdit,
    QtWidgets.QComboBox,
    QtWidgets.QAbstractSpinBox,
)


def _visible_in(w: QtWidgets.QWidget, top: QtWidgets.QWidget) -> bool:
    return w.isVisibleTo(top) and w.width() > 0 and w.height() > 0


def _shown_text(text: str) -> str:
    """``text`` as drawn: '&' marks the shortcut letter ('&&' is one '&')."""
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


def _text_clipped(w: QtWidgets.QWidget) -> bool:
    if isinstance(w, QtWidgets.QLabel):
        if w.wordWrap() or not w.text() or w.textFormat() == QtCore.Qt.RichText or "<" in w.text():
            return False
        if w.pixmap() is not None and not w.pixmap().isNull():
            return False
        if "Elided" in type(w).__name__:
            return False  # (made to elide)
        need = max(w.fontMetrics().horizontalAdvance(line) for line in _shown_text(w.text()).split("\n"))
        return need > w.contentsRect().width() + 1
    if isinstance(w, QtWidgets.QAbstractButton) and w.text():
        if isinstance(w.parentWidget(), QtWidgets.QLineEdit):
            return False  # (a line edit's action: its icon is drawn, not its text)
        return w.sizeHint().width() > w.width() + 1
    return False


def layout_problems(dlg: QtWidgets.QWidget) -> List[str]:
    """What does not fit: text cut off, the window smaller than its layout needs, controls
    outside the window or over each other."""
    out = []
    hint = dlg.minimumSizeHint()
    if hint.isValid() and (hint.width() > screen().width() or hint.height() > screen().height()):
        out.append(
            "its layout needs %dx%d, more than a %dx%d screen"
            % (hint.width(), hint.height(), screen().width(), screen().height())
        )
    box = QtCore.QRect(0, 0, dlg.width(), dlg.height())
    leaves = []
    for w in dlg.findChildren(QtWidgets.QWidget):
        if not isinstance(w, _LEAF_TYPES) or not _visible_in(w, dlg) or w.window() is not dlg:
            continue
        # (inside a scroll area: what it shows scrolls)
        if any(isinstance(p, QtWidgets.QAbstractScrollArea) for p in _parents(w, dlg)):
            continue
        name = _name(w)
        if isinstance(w, _TEXT_TYPES) and _text_clipped(w):
            out.append("text cut off: %s" % name)
        r = QtCore.QRect(w.mapTo(dlg, QtCore.QPoint(0, 0)), w.size())
        if not box.contains(r, False) and r.intersected(box) != r:
            out.append("outside the window: %s" % name)
        leaves.append((w, r))
    out += button_order_problems(dlg)
    for k, (a, ra) in enumerate(leaves):
        for b, rb in leaves[k + 1 :]:
            if a.parentWidget() is not b.parentWidget():
                continue
            i = ra.intersected(rb)
            if i.width() > 2 and i.height() > 2:
                out.append("overlap: %s and %s" % (_name(a), _name(b)))
    return out


def button_order_problems(dlg: QtWidgets.QWidget) -> List[str]:
    """A button box whose own action button sits between its OK and its Cancel / Close (the
    action buttons go to the left of them, the standard ones together)."""
    out = []
    B = QtWidgets.QDialogButtonBox
    for box in dlg.findChildren(B):
        if box.window() is not dlg or not box.isVisibleTo(dlg):
            continue
        shown = sorted((b for b in box.buttons() if b.isVisibleTo(dlg)), key=lambda b: b.x())
        roles = [box.buttonRole(b) for b in shown]
        if B.AcceptRole not in roles:
            continue
        first = roles.index(B.AcceptRole)
        last = max((k for k, r in enumerate(roles) if r in (B.RejectRole, B.ApplyRole)), default=-1)
        between = [shown[k].text() for k in range(first + 1, last) if roles[k] == B.ActionRole]
        if between:
            out.append("button between OK and Cancel: %s" % ", ".join(between))
    return out


def keyboard_problems(dlg: QtWidgets.QWidget) -> List[str]:
    """Enter does something (a default button where there is a button box), focus does not
    start on Help or Cancel, and no two controls share a shortcut letter (Alt+letter)."""
    out = []
    B = QtWidgets.QDialogButtonBox
    boxes = [b for b in dlg.findChildren(B) if b.window() is dlg and b.isVisibleTo(dlg)]
    pushes = [b for b in dlg.findChildren(QtWidgets.QPushButton) if b.window() is dlg and b.isVisibleTo(dlg)]
    if boxes and not any(b.isDefault() for b in pushes):
        out.append("no default button: Enter does nothing")
    f = QtWidgets.QApplication.focusWidget()
    if f is not None and f.window() is dlg and isinstance(f, QtWidgets.QAbstractButton):
        role = next((box.buttonRole(f) for box in boxes if f in box.buttons()), None)
        ok_off = any(
            not b.isEnabled() for box in boxes for b in box.buttons() if box.buttonRole(b) == B.AcceptRole
        )
        if role in (B.HelpRole, B.RejectRole) and len(pushes) > 2 and not ok_off:
            out.append("focus starts on %r" % f.text())
    letters: dict = {}
    for w in dlg.findChildren(QtWidgets.QWidget):
        if (
            w.window() is not dlg
            or not w.isVisibleTo(dlg)
            or not isinstance(w, (QtWidgets.QLabel, QtWidgets.QAbstractButton))
        ):
            continue
        if isinstance(w, QtWidgets.QLabel) and w.buddy() is None:
            continue  # (a label's '&' works through its buddy)
        text = w.text().replace("&&", "")
        k = text.find("&")
        if 0 <= k < len(text) - 1:
            letters.setdefault(text[k + 1].lower(), []).append(text.replace("&", ""))
    for letter, names in sorted(letters.items()):
        if len(names) > 1:
            out.append("Alt+%s for %s" % (letter.upper(), " and ".join(repr(n) for n in names)))
    return out


def _parents(w, top):
    p = w.parentWidget()
    while p is not None and p is not top:
        yield p
        p = p.parentWidget()


def _name(w) -> str:
    text = w.text() if hasattr(w, "text") and callable(w.text) else ""
    return "%s %r" % (type(w).__name__, (text or w.objectName() or "")[:40])


#: the smallest screen a dialog must fit at 100 % text (a small laptop's), larger in step with
#: the text size. The tests' offscreen screen is 800 x 600: a dialog it squeezed is given the
#: room this screen would give
SCREEN = QtCore.QSize(1280, 720)


def screen() -> QtCore.QSize:
    """:data:`SCREEN` at the current text size."""
    from ctgedit.ui.style.theme import ui_scale

    return SCREEN * max(1.0, ui_scale())


def show(dlg: QtWidgets.QWidget) -> None:
    """Show ``dlg`` as it opens (sized by its own rules, on a :data:`SCREEN`) and let it lay
    out."""
    dlg.show()
    QtWidgets.QApplication.processEvents()
    hint = dlg.minimumSizeHint()
    if hint.isValid() and (dlg.width() < hint.width() or dlg.height() < hint.height()):
        dlg.resize(
            max(dlg.width(), min(hint.width(), screen().width())),
            max(dlg.height(), min(hint.height(), screen().height())),
        )
    for _ in range(3):
        QtWidgets.QApplication.processEvents()


def close(dlg: QtWidgets.QWidget) -> None:
    try:
        dlg.close()
        dlg.deleteLater()
    except RuntimeError:
        pass
    QtWidgets.QApplication.processEvents()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
