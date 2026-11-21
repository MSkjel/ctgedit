"""Dialog sizing."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the workspace pane genie of the fake install (FxSa's fxws.ctm, its form in fxws.frm)
PANE_REF = "fxws.pane"


@pytest.fixture(scope="module")
def themed():
    """The dark theme for the module; afterwards the application is put back
    as it was (style, palette, font, stylesheet, colours)."""
    app = _app()
    from ctgedit.render import scene as SC
    from ctgedit.ui.style import theme as T

    snap = dict(
        style=app.style().name(),
        palette=QtGui.QPalette(app.palette()),
        font=QtGui.QFont(app.font()),
        sheet=app.styleSheet(),
        state=dict(T._state),
        colors=dict(T.COLORS),
        hooked=T._orig_set_style_sheet is not None,
        margin=QtGui.QColor(SC.PageScene._MARGIN_COLOUR),
    )
    T.apply_theme(app, "dark", 1.0)
    yield T
    T.COLORS.clear()
    T.COLORS.update(snap["colors"])
    T._state.clear()
    T._state.update(snap["state"])
    T._update_tracked()
    if not snap["hooked"]:
        T._uninstall_stylesheet_hook()
    app.setStyleSheet(snap["sheet"])
    app.setPalette(snap["palette"])
    app.setFont(snap["font"])
    if snap["style"] and app.style().name() != snap["style"]:
        app.setStyle(snap["style"])
    SC.PageScene._MARGIN_COLOUR = snap["margin"]


@pytest.fixture(scope="module")
def win(themed, tmp_path_factory):
    """An editor window with the dialog decoration hook installed."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    home = tmp_path_factory.mktemp("dialog_sizing")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CTG_SETTINGS_INI", str(home / "ctgedit_settings.ini"))
        mp.setenv("CTG_DATA_DIR", str(home / "ctgedit_data"))
        mp.setenv("CTG_SCADA_ROOT", "none")
        for k in (
            "CTG_NO_RUNTIME",
            "CTG_NO_SIM_ENGINE",
            "CTG_NO_CTF_PRECHECK",
            "CTG_NO_DISPLAY_CHECK",
            "CTG_NO_ANIMATION",
            "CTG_NO_DIALOG_REAP",
        ):
            mp.setenv(k, "1")
        w = MainWindow()
        w.resize(760, 700)
        w.show()
        QtWidgets.QApplication.processEvents()
        yield w
    for d in list(getattr(w, "_dialogs", {}).values()):
        d.close()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()
    QtWidgets.QApplication.processEvents()


def _buttons(lay, dlg):
    bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)


class ListProbeDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Pick one:"))
        self.list = QtWidgets.QListWidget()
        self.list.addItems(["a", "b", "c"])
        lay.addWidget(self.list, 1)
        _buttons(lay, self)


class EditProbeDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.spin = QtWidgets.QSpinBox()
        self.edit = QtWidgets.QLineEdit("a text field")
        self.combo = QtWidgets.QComboBox()
        self.combo.addItems(["TRUE", "FALSE"])
        form.addRow("Number:", self.spin)
        form.addRow("Name:", self.edit)
        form.addRow("Flag:", self.combo)
        self.form = form
        lay.addLayout(form)
        _buttons(lay, self)


class SpinProbeDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        form.addRow("Width:", QtWidgets.QSpinBox())
        form.addRow("Height:", QtWidgets.QDoubleSpinBox())
        form.addRow("Choice:", QtWidgets.QComboBox())
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QCheckBox("Snap"))
        _buttons(lay, self)


class RuleProbeDialog(ListProbeDialog):
    RESIZE = "width"


class NoLayoutProbeDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        QtWidgets.QLineEdit(self)


class PictureProbeDialog(QtWidgets.QDialog):
    """The magnifier's shape: a QLabel made to take the extra room."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lab = QtWidgets.QLabel()
        lab.setMinimumSize(120, 90)
        lab.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        lay.addWidget(lab)
        lay.addWidget(QtWidgets.QToolButton())


def _show(dlg, modal=False):
    """Show ``dlg`` (``modal``: as exec / open show it, without their loop)."""
    from ctgedit.ui import help as UH

    if modal:
        dlg.setModal(True)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    UH._decorate_dialog(dlg)  # (a no-op when the Show hook already ran)
    QtWidgets.QApplication.processEvents()
    return dlg


def _forget(*names):
    from ctgedit.ui import help as UH
    from ctgedit.ui.prefs.settings import app_settings

    for n in names:
        app_settings().remove(UH.GEOMETRY_PREFIX + n)


# ------------------------------------------------------------------ the rule
def test_rule_comes_from_the_content(win):
    from ctgedit.ui import help as UH

    assert UH.resize_rule(ListProbeDialog(win)) == "both"
    assert UH.resize_rule(EditProbeDialog(win)) == "width"
    assert UH.resize_rule(SpinProbeDialog(win)) == "none"  # a spin box's own line edit
    assert UH.resize_rule(RuleProbeDialog(win)) == "width"  # RESIZE wins
    over = ListProbeDialog(win)
    over.setProperty("_ctgResize", "none")
    assert UH.resize_rule(over) == "none"
    assert UH.resize_rule(PictureProbeDialog(win)) == "both"  # the magnifier resizes
    editable = SpinProbeDialog(win)
    combo = editable.findChild(QtWidgets.QComboBox)
    combo.setEditable(True)
    assert UH.resize_rule(editable) == "width"
    # a dialog without a layout keeps sizing itself (no rule, resizable)
    _forget("NoLayoutProbeDialog")
    bare = _show(NoLayoutProbeDialog(win))
    assert bare.property("_ctgRule") is None
    assert bare.minimumSize() != bare.maximumSize()
    bare.close()


def test_none_dialog_has_a_fixed_size_and_is_not_remembered(win):
    from ctgedit.ui import help as UH
    from ctgedit.ui.prefs.settings import app_settings

    _forget("SpinProbeDialog")
    dlg = _show(SpinProbeDialog(win))
    assert dlg.property("_ctgRule") == "none"
    assert dlg.minimumSize() == dlg.maximumSize() == dlg.size()
    before = dlg.size()
    dlg.resize(before.width() + 300, before.height() + 200)
    QtWidgets.QApplication.processEvents()
    assert dlg.size() == before
    assert not UH.remembers_size(dlg)
    dlg.done(0)
    assert app_settings().value(UH.GEOMETRY_PREFIX + "SpinProbeDialog") is None


def test_none_dialog_keeps_its_own_minimum_width(win):
    """About sets a 460 px minimum width: a fixed size never goes below it."""

    class WideProbeDialog(SpinProbeDialog):
        pass

    probe = WideProbeDialog(win)
    probe.setMinimumWidth(700)
    _show(probe)
    assert probe.width() == probe.minimumWidth() == probe.maximumWidth() == 700
    probe.close()


def test_width_dialog_keeps_its_content_height_and_saves_only_the_width(win):
    from ctgedit.ui import help as UH
    from ctgedit.ui.prefs.settings import app_settings

    _forget("EditProbeDialog")
    dlg = _show(EditProbeDialog(win), modal=True)
    assert dlg.property("_ctgRule") == "width"
    h0, w0 = dlg.height(), dlg.width()
    dlg.resize(w0 + 400, h0 + 300)
    QtWidgets.QApplication.processEvents()
    assert dlg.width() == w0 + 400 and dlg.height() == h0
    dlg.done(0)
    assert app_settings().value(UH.GEOMETRY_PREFIX + "EditProbeDialog") == str(w0 + 400)
    again = _show(EditProbeDialog(win), modal=True)
    assert again.width() == w0 + 400 and again.height() == h0
    again.close()


def test_a_minimum_below_the_content_is_raised(win):
    class TightProbeDialog(ListProbeDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            wide = QtWidgets.QLineEdit()
            wide.setMinimumWidth(420)
            self.layout().insertWidget(0, wide)
            self.setMinimumSize(60, 40)

    _forget("TightProbeDialog")
    dlg = _show(TightProbeDialog(win))
    least = dlg.layout().totalMinimumSize()
    assert dlg.minimumWidth() >= least.width() > 420
    assert dlg.minimumHeight() >= least.height()
    dlg.resize(100, 50)
    QtWidgets.QApplication.processEvents()
    assert dlg.width() >= least.width()
    dlg.close()


def test_form_fields_other_than_text_keep_their_size(win):
    """After apply_theme a QFormLayout grows only its text fields."""
    assert (
        QtWidgets.QFormLayout(QtWidgets.QWidget()).fieldGrowthPolicy()
        == QtWidgets.QFormLayout.ExpandingFieldsGrow
    )
    _forget("EditProbeDialog")
    dlg = _show(EditProbeDialog(win))
    edit_w = dlg.edit.width()
    dlg.resize(dlg.width() + 400, dlg.height())
    QtWidgets.QApplication.processEvents()
    assert dlg.spin.width() == dlg.spin.sizeHint().width()
    assert dlg.combo.width() == dlg.combo.sizeHint().width()
    assert dlg.edit.width() >= edit_w + 380
    dlg.close()
    _forget("EditProbeDialog")


def test_short_sheet_choices_keep_their_size(win, fx_page):
    """The object sheet's line Style, Gradient and font Style combos hold
    short choices: they keep their size in a wide window; the Font combo
    (long names) grows."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    doc = EditableDocument.open(fx_page("FxThumb"))
    r = doc.create_object("rectangle", rect=(10, 10, 80, 60), colour=0x336699)
    t = doc.create_object("text", rect=(100, 10, 180, 40), colour=0x336699)
    _forget("ObjectPropertiesDialog")
    for i, short, long_ in ((r, ("_cb_lstyle", "_cb_grad"), ()), (t, ("_cb_fstyle",), ("_cb_font",))):
        dlg = _show(ObjectPropertiesDialog(doc, i))
        dlg.resize(dlg.width() + 400, dlg.height())
        QtWidgets.QApplication.processEvents()
        for name in short:
            combo = getattr(dlg, name)
            assert combo.width() == combo.sizeHint().width(), name
        for name in long_:
            combo = getattr(dlg, name)
            assert combo.width() > combo.sizeHint().width() + 200, name
        dlg.reject()
    _forget("ObjectPropertiesDialog")


def test_properties_panel_keeps_full_width_fields(win):
    from ctgedit.ui.panels.properties import PropertyPanel

    box = QtWidgets.QWidget()
    form = PropertyPanel._form(None, box)
    assert form.fieldGrowthPolicy() == QtWidgets.QFormLayout.AllNonFixedFieldsGrow


def test_page_field_grows(win):
    from ctgedit.ui.pick_fields import PageField

    f = PageField(pages=["FxShapes", "FxGenies"])
    assert f.sizePolicy().horizontalPolicy() == QtWidgets.QSizePolicy.Expanding


def test_settings_shows_every_page_name_and_resizes(win):
    # the page list is as wide as its longest name; the window grows (its pages scroll)
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = _show(SettingsDialog(win))
    pages = dlg._tabs
    fm = pages.list.fontMetrics()
    for k in range(pages.count()):
        assert fm.horizontalAdvance(pages.tabText(k)) < pages.list.width(), pages.tabText(k)
    assert dlg.property("_ctgRule") == "both"
    assert dlg.minimumSize() != dlg.maximumSize()
    dlg.reject()


# ------------------------------------------------------------------ modeless windows
def test_modeless_window_opens_at_the_canvas_right_and_remembers_where(win):
    from ctgedit.ui import help as UH
    from ctgedit.ui.prefs.settings import app_settings

    class ModelessProbeDialog(ListProbeDialog):
        pass

    _forget("ModelessProbeDialog")
    first = _show(ModelessProbeDialog(win))
    second = _show(ModelessProbeDialog(win))
    assert not first.isModal()
    view = win.view
    right = view.mapToGlobal(QtCore.QPoint(0, 0)).x() + view.width()
    avail = (win.screen() or QtWidgets.QApplication.primaryScreen()).availableGeometry()
    fw = first.frameGeometry().width()
    want = max(avail.left(), min(right - UH.PLACE_MARGIN - fw, avail.right() + 1 - fw))
    assert first.frameGeometry().x() == want
    assert second.frameGeometry().y() == first.frameGeometry().y() + UH.PLACE_STEP
    second.close()
    first.move(first.x() - 30, first.y() + 17)
    moved = QtCore.QPoint(first.pos())
    first.close()  # (a QDialog close = reject = finished)
    value = app_settings().value(UH.GEOMETRY_PREFIX + "ModelessProbeDialog")
    assert str(value).startswith("%d,%d," % (moved.x(), moved.y()))
    again = _show(ModelessProbeDialog(win))
    assert again.pos() == moved
    again.close()
    _forget("ModelessProbeDialog")


def test_magnifier_keeps_its_size_and_position_when_hidden(win):
    """The magnifier (View > Show Zoom) is hidden, never closed: hiding it
    saves its size and position, and the next editor window's magnifier
    opens there."""
    from ctgedit.ui import help as UH
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    _forget("ZoomWindow")
    win.act_zoom_window.setChecked(True)
    win._toggle_zoom_window()
    QtWidgets.QApplication.processEvents()
    zoom = win._zoom_win
    assert zoom.isVisible() and zoom.property("_ctgRule") == "both"
    zoom.resize(zoom.width() + 60, zoom.height() + 40)
    zoom.move(zoom.x() - 25, zoom.y() + 15)
    QtWidgets.QApplication.processEvents()
    size, pos = QtCore.QSize(zoom.size()), QtCore.QPoint(zoom.pos())
    win.act_zoom_window.setChecked(False)
    win._toggle_zoom_window()
    assert zoom.isHidden()
    value = str(app_settings().value(UH.GEOMETRY_PREFIX + "ZoomWindow"))
    assert value == "%d,%d,%d,%d" % (pos.x(), pos.y(), size.width(), size.height())
    other = MainWindow()
    try:
        other.act_zoom_window.setChecked(True)
        other._toggle_zoom_window()
        QtWidgets.QApplication.processEvents()
        assert other._zoom_win.size() == size and other._zoom_win.pos() == pos
        other.act_zoom_window.setChecked(False)
        other._toggle_zoom_window()
    finally:
        if other in MainWindow._instances:
            MainWindow._instances.remove(other)
        other.hide()
        other.deleteLater()
        QtWidgets.QApplication.processEvents()
    _forget("ZoomWindow")


# ------------------------------------------------------------------ the genie form
def _boolean_table(project_dir):
    """The table fxws.frm's IsDefault and HasScrollbars list ('FxBoolean', 'NAME'): the
    fixture has FxBoolean as a label only, so the test writes the table (TRUE, FALSE) into
    its copy of FxSa, which makes the two fields TRUE / FALSE drop-downs."""
    from ctgedit.format import dbf

    path = os.path.join(project_dir, "FxBoolean.DBF")
    if not os.path.exists(path):
        dbf.create_table(path, [("NAME", 16)], [{"NAME": "TRUE"}, {"NAME": "FALSE"}])


def _pane_dialog(fx_page, extra=None, parent=None):
    """The genie dialog of the first pane placed on FxSa's FxSaMaster (fxws.pane)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.dialog.genie_dialogs import GenieSubstitutionDialog

    page = fx_page("FxSaMaster", "FxSa")
    _boolean_table(os.path.dirname(page))
    d = EditableDocument.open(page)
    pane = next(
        i
        for i, o in enumerate(d.page.objects)
        if o.parent is None and (d.genie_link(i) or ("",) * 2)[1].lower() == "pane"
    )
    params = dict(d.genie_all_params(pane))
    params.update(extra or {})
    return GenieSubstitutionDialog(
        d.page.objects[pane],
        [],
        parent,
        bindings=params,
        form=d.genie_form(pane),
        list_values=d.form_list_values,
        an=d.genie_an(pane),
        ref=PANE_REF,
    )


def test_genie_form_widens_its_long_fields_and_scrolls_when_smaller(win, fx_page):
    from ctgedit.ui import help as UH
    from ctgedit.ui.prefs.settings import app_settings

    key = "geometry/GenieForm/" + PANE_REF
    app_settings().remove(key)
    dlg = _pane_dialog(fx_page)
    pf = dlg.params_form
    canvas = pf.canvas
    natural = canvas.natural_size()
    # the form is as wide as its fields and prompts (a longer rule is cut there)
    items = [w for w in canvas.children() if isinstance(w, QtWidgets.QWidget)]
    content = [
        canvas.base_rect(w)
        for w in items
        if not (isinstance(w, QtWidgets.QFrame) and w.frameShape() == QtWidgets.QFrame.HLine)
    ]
    right = max(r.right() + 1 for r in content if r is not None)
    assert natural.width() == right + 8
    rules = [w for w in items if isinstance(w, QtWidgets.QFrame) and w.frameShape() == QtWidgets.QFrame.HLine]
    assert rules and all(canvas.base_rect(r).right() + 1 <= right for r in rules)
    _show(dlg)
    assert dlg.property("_ctgRule") == "both"
    # it opens at least as wide as its form: no field cut off, no scroll bar
    assert canvas.width() >= natural.width()
    assert not pf.scroll.horizontalScrollBar().isVisible()
    ed = {k: w for k, (_g, w) in pf.editors.items()}
    labels = [w for w in canvas.findChildren(QtWidgets.QLabel)]
    before = {k: QtCore.QRect(w.geometry()) for k, w in ed.items()}
    lab_before = [QtCore.QRect(w.geometry()) for w in labels]
    dlg.resize(dlg.width() + 400, dlg.height() + 300)
    QtWidgets.QApplication.processEvents()
    name = ed["Name"].geometry()
    assert name.x() == before["Name"].x()
    assert name.right() == before["Name"].right() + 400
    for k in ("IsDefault", "HasScrollbars"):  # short BOOLEAN fields
        assert ed[k].geometry() == before[k]
    assert [QtCore.QRect(w.geometry()) for w in labels] == lab_before
    assert any(canvas.base_rect(r).width() < r.width() for r in rules)  # a rule follows
    # a smaller window scrolls
    dlg.resize(300, 200)
    QtWidgets.QApplication.processEvents()
    assert pf.scroll.horizontalScrollBar().isVisible() or pf.scroll.verticalScrollBar().isVisible()
    assert canvas.width() >= natural.width()
    dlg.resize(natural.width() + 200, 420)
    QtWidgets.QApplication.processEvents()
    size = QtCore.QSize(dlg.size())
    dlg.reject()
    value = str(app_settings().value(key))
    assert value.endswith("%d,%d" % (size.width(), size.height())), value
    assert UH._geometry_key(dlg) == key
    again = _pane_dialog(fx_page)
    _show(again)
    assert again.size() == size
    again.reject()
    app_settings().remove(key)


def test_genie_form_other_parameters_scroll_with_the_form(win, fx_page):
    dlg = _pane_dialog(fx_page, extra={"NotOnTheForm": "x"})
    pf = dlg.params_form
    assert pf.other_box is not None
    content = pf.scroll.widget()
    w = pf.other_box
    while w is not None and w is not content:
        w = w.parentWidget()
    assert w is content  # inside the scroll area, under the form
    assert pf.other_box.y() >= pf.canvas.geometry().bottom()


def test_genie_form_set_values_keeps_typed_rows(win, fx_page):
    dlg = _pane_dialog(fx_page)
    pf = dlg.params_form
    assert not dlg.has_pending_edits()
    name = pf.editor("Name")
    name.setText("Typed")
    assert dlg.has_pending_edits()
    dlg.set_values({"Name": "FromThePage", "DefaultPage": "FxSaBlank", "IsDefault": "TRUE"})
    assert pf.values()["Name"] == "Typed"  # typed: kept
    assert pf.values()["DefaultPage"] == "FxSaBlank"  # untouched: follows the page
    assert pf.combos["IsDefault"].currentText() == "TRUE"
    assert dict(dlg.changed_values()) == {"Name": "Typed"}
    dlg.reject()


# a two-column form (the Lib_Controls alarm table's shape): a left-column field
# alone on its line, TRUE / FALSE drop-downs in the right column
_TWO_COLUMNS = """FORM "Probe", "Probe";
1: "Name",  32,  readwrite;
2: "Delay", 32,  readwrite;
3: "Flag",  128, readwrite, "SCADAHELP", "BOOLEAN";
4: "Font",  32,  readwrite, "FONTS", "NAME";
5: "Path",  200, readwrite;
NORMAL
"Name   {1          }      Path {5                     }
Delay  {2          }
                          Flag {3                     }
                          Font {4                     }"
"""


def _two_column_form():
    from ctgedit.format import genieform
    from ctgedit.ui.dialog.genie_dialogs import GenieParamsForm

    # (a .frm file breaks its lines with CR LF; a lone LF is a blank)
    form = genieform.lookup_form(genieform.parse_forms(_TWO_COLUMNS.replace("\n", "\r\n")), "Probe")
    assert form is not None and form.valid

    def lists(fld):
        if fld.list_field.upper() == "BOOLEAN":
            return ["TRUE", "FALSE"]
        return ["Arial Unicode MS", "Segoe UI Semibold", "Courier New"]

    return GenieParamsForm(
        {"Name": "n", "Delay": "1", "Flag": "TRUE", "Font": "Arial Unicode MS", "Path": "p"},
        form=form,
        list_values=lists,
    )


def test_genie_form_grows_only_long_right_hand_fields(themed):
    """A left-column field never grows under the right-hand column, and a
    TRUE / FALSE drop-down keeps its size (UI guide section 9)."""
    pf = _two_column_form()
    grows = set(pf.canvas.growing())
    ed = {k: w for k, (_g, w) in pf.editors.items()}
    assert ed["Path"] in grows  # a long text field at the right edge
    assert ed["Font"] in grows  # long values
    assert ed["Delay"] not in grows  # left column, alone on its line
    assert ed["Name"] not in grows  # a prompt follows it
    assert ed["Flag"] not in grows  # TRUE / FALSE
    assert ed["Flag"].sizePolicy().horizontalPolicy() != QtWidgets.QSizePolicy.Expanding
    pf.scroll.resize(pf.canvas.natural_size() + QtCore.QSize(300, 40))
    pf.show()
    QtWidgets.QApplication.processEvents()
    pf.canvas.resize(pf.canvas.natural_size().width() + 300, pf.canvas.height())
    QtWidgets.QApplication.processEvents()
    assert ed["Delay"].geometry() == pf.canvas.base_rect(ed["Delay"])
    assert ed["Path"].width() == pf.canvas.base_rect(ed["Path"]).width() + 300
    pf.close()


def test_small_genie_form_leaves_no_empty_band(themed):
    """A form smaller than the scroll area's minimum asks for its own size."""
    from ctgedit.format import genieform
    from ctgedit.ui.dialog.genie_dialogs import GenieParamsForm

    text = 'FORM "Tiny";\n1: "Tag", 16, readwrite;\nNORMAL\n"Tag {1          }"\n'
    form = genieform.lookup_form(genieform.parse_forms(text), "Tiny")
    assert form is not None and form.valid
    pf = GenieParamsForm({"Tag": "T1"}, form=form)
    hint = pf.scroll.sizeHint()
    assert hint.height() < 120  # a one-line form
    assert pf.scroll.minimumSizeHint().height() == hint.height()


def test_genie_form_long_value_shows_its_start(themed):
    """A value longer than its field shows its start (setText leaves the cursor at
    the end, which scrolled the field to the end of the value)."""
    pf = _two_column_form()
    long = "_TabAlmTable_InitDsp(" + "x" * 80 + ")"
    pf.set_values({"Delay": long})
    for name in ("Delay", "Path"):
        assert pf.editor(name).cursorPosition() == 0
    pf.close()


def test_opening_other_parameters_makes_the_window_taller(win, fx_page):
    dlg = _pane_dialog(fx_page, extra={"NotOnTheForm%d" % i: "x" for i in range(6)}, parent=win)
    _show(dlg)
    h0 = dlg.height()
    dlg.params_form.other_box.setChecked(True)
    for _ in range(3):
        QtWidgets.QApplication.processEvents()
    assert dlg.height() > h0
    avail = (dlg.screen() or QtWidgets.QApplication.primaryScreen()).availableGeometry()
    assert dlg.frameGeometry().bottom() <= avail.bottom()
    dlg.reject()


def test_a_stepped_down_window_does_not_move_the_remembered_position(win):
    """The second window of a kind opens 24 px lower; closed without being moved
    it keeps the stored position (else each session drifted down)."""
    from ctgedit.ui import help as UH
    from ctgedit.ui.prefs.settings import app_settings

    class DriftProbeDialog(ListProbeDialog):
        pass

    _forget("DriftProbeDialog")
    first = _show(DriftProbeDialog(win))
    first.move(first.x() - 40, first.y() + 30)
    at = QtCore.QPoint(first.pos())
    first.close()
    a = _show(DriftProbeDialog(win))
    b = _show(DriftProbeDialog(win))
    assert a.pos() == at and b.y() == at.y() + UH.PLACE_STEP
    a.close()
    b.close()  # b last: it was not moved
    value = str(app_settings().value(UH.GEOMETRY_PREFIX + "DriftProbeDialog"))
    assert value.startswith("%d,%d," % (at.x(), at.y())), value
    again = _show(DriftProbeDialog(win))
    assert again.pos() == at
    again.close()
    # the same kind shown modal and resized keeps the modeless window's position
    modal = _show(DriftProbeDialog(win), modal=True)
    modal.resize(modal.width() + 50, modal.height())
    QtWidgets.QApplication.processEvents()
    modal.done(0)
    value = str(app_settings().value(UH.GEOMETRY_PREFIX + "DriftProbeDialog"))
    assert value.startswith("%d,%d," % (at.x(), at.y())), value
    _forget("DriftProbeDialog")


def test_genie_form_sections_grow_on_their_own(themed):
    """keyentry.Number_Pad's shape: long fields above a line of underscores, a
    wider row of TRUE / FALSE lists below it. The fields above still grow."""
    from ctgedit.format import genieform
    from ctgedit.ui.dialog.genie_dialogs import GenieParamsForm

    text = (
        'FORM "Pad";\n1: "Tag", 254, readwrite;\n2: "Comment", 48, readwrite;\n'
        '3: "PM", 6, readwrite, "HELP", "BOOLEAN";\n'
        '4: "SL", 6, readwrite, "HELP", "BOOLEAN";\nNORMAL\n'
        '"Tag      {1                    }\n'
        "Comment  {2                    }\n"
        "__________________________________________________\n"
        'Numbers  {3       }                [.] Key {4       }"\n'
    )
    form = genieform.lookup_form(genieform.parse_forms(text.replace("\n", "\r\n")), "Pad")
    assert form is not None and form.valid
    pf = GenieParamsForm(
        {"Tag": "T", "Comment": "c", "PM": "TRUE", "SL": "FALSE"},
        form=form,
        list_values=lambda fld: ["TRUE", "FALSE"],
    )
    ed = {k: w for k, (_g, w) in pf.editors.items()}
    grows = set(pf.canvas.growing())
    assert pf.canvas.base_rect(ed["SL"]).left() > pf.canvas.base_rect(ed["Tag"]).right()
    assert ed["Tag"] in grows and ed["Comment"] in grows
    assert ed["PM"] not in grows and ed["SL"] not in grows
