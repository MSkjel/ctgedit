"""Field validation look outside the Properties panel."""

import os


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app


def test_mark_invalid_frames_and_clears():
    from ctgedit.ui.style import theme

    _app()
    ed = QtWidgets.QLineEdit()
    ed.setToolTip("The window title")
    ed.setStyleSheet("QLineEdit { padding: 3px; }")
    theme.mark_invalid(ed, "Too long.")
    assert theme.is_invalid(ed) and ed.toolTip() == "Too long."
    assert theme.DARK["danger"] in ed.styleSheet() and "padding: 3px" in ed.styleSheet()
    assert ed.accessibleDescription() == "Too long."
    theme.mark_invalid(ed, "Still too long.")
    theme.mark_invalid(ed, "")
    assert not theme.is_invalid(ed) and ed.toolTip() == "The window title"
    assert ed.styleSheet() == "QLineEdit { padding: 3px; }"


def test_the_invalid_frame_follows_the_theme():
    from ctgedit.ui.style import theme

    app = _app()
    ed = QtWidgets.QLineEdit()
    theme.mark_invalid(ed, "No.")
    try:
        theme.apply_theme(app, "light")
        assert theme.LIGHT["danger"] in ed.styleSheet()
        theme.apply_theme(app, "dark")
        assert theme.DARK["danger"] in ed.styleSheet()
    finally:
        theme.apply_theme(app, "dark")


def test_no_fixed_dark_colours_in_the_owned_dialogs():
    import re

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit", "ui")
    for name in ("dialog/page_props.py", "dialog/supergenie_dialogs.py"):
        src = open(os.path.join(root, name), encoding="utf-8").read()
        assert not re.search(r"DARK\[[\"']danger[\"']\]", src), name


def test_the_super_genie_message_uses_the_danger_role():
    _app()
    from ctgedit.ui.dialog.supergenie_dialogs import AttachSuperGeniesDialog

    dlg = AttachSuperGeniesDialog([], genie="lib.g")
    assert dlg._msg.property("noteRole") == "danger"


def test_a_long_title_frames_its_field(fx_page, fx_project):
    """The fake install's FxAnim (FxMain): a page with a pages.DBF row."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.style import theme
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog
    from ctgedit.ui.number_field import NumberField

    _app()
    doc = EditableDocument.open(fx_page("FxAnim"))
    dlg = PagePropertiesDialog(doc, Project(fx_project("FxMain")))
    dlg._title.setText("x" * 70)
    assert len(dlg._title.text()) == 70  # (typed past the limit: shown, framed)
    dlg._revalidate()
    assert theme.is_invalid(dlg._title) and "at most" in dlg._title.toolTip()
    assert not dlg._btn_ok.isEnabled()
    dlg._title.setText("FxAnim")
    dlg._revalidate()
    assert not theme.is_invalid(dlg._title) and dlg._btn_ok.isEnabled()
    # the view area takes sums
    assert isinstance(dlg._sp_w, NumberField) and isinstance(dlg._sp_h, NumberField)
    # the content type list is the panel's (page_props.content_type_combo)
    assert dlg._cb_contype.isEditable()


def test_the_counter_uses_the_note_roles():
    _app()
    from ctgedit.ui.dialog.page_props import _counter

    ed = QtWidgets.QLineEdit("abc")
    lab = _counter(ed, 4)
    assert lab.property("noteRole") == "text_dim" and lab.text() == "3 / 4"
    ed.setText("abcdef")
    assert lab.property("noteRole") == "danger"


def test_the_object_sheet_fits_the_fill_colour_page(fx_page):
    """FxAnim's FxColA (the fake install's FxMain): an On / Off fill colour."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops import ObjectPropertiesDialog
    from ctgedit.ui.objprops.scroll import PageScroll
    from PySide6.QtTest import QTest

    _app()
    doc = EditableDocument.open(fx_page("FxAnim"))
    [i] = doc.find_objects_by_name("FxColA")
    dlg = ObjectPropertiesDialog(doc, i)
    dlg.resize(573, 523)  # (the size it opened with at 1280 x 720)
    dlg.show()
    dlg.show_page("fill_color")
    for _ in range(5):
        QTest.qWait(10)
    shown = [p for p in dlg.findChildren(PageScroll) if p.isVisible()]
    assert shown
    for ps in shown:
        assert not ps.horizontalScrollBar().isVisible()
        assert ps.widget().minimumSizeHint().width() <= ps.viewport().width()
    dlg.close()


def test_current_stack_is_as_big_as_its_page():
    _app()
    from ctgedit.ui.objprops.scroll import CurrentStack

    st = CurrentStack()
    small, big = QtWidgets.QWidget(), QtWidgets.QWidget()
    small.setMinimumWidth(50)
    big.setMinimumWidth(500)
    st.addWidget(small)
    st.addWidget(big)
    st.setCurrentIndex(0)
    assert st.minimumSizeHint().width() < 500
    st.setCurrentIndex(1)
    assert st.minimumSizeHint().width() >= 500


def test_new_number_frames_the_field_with_the_character():
    from ctgedit.ui.style import theme
    from ctgedit.ui.dialog.dialogs import NumberExpressionDialog

    _app()
    dlg = NumberExpressionDialog(tag_names=["Loop_1"])
    dlg._expr.setText("Loop_1 \u4e2d")
    assert theme.is_invalid(dlg._expr) and not theme.is_invalid(dlg._fmt)
    dlg._expr.setText("Loop_1")
    assert not theme.is_invalid(dlg._expr)


def test_the_sheet_frames_the_field_of_its_problem(fx_page):
    """A new rectangle on the fake install's FxShapes (FxMain)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.style import theme
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    doc = EditableDocument.open(fx_page("FxShapes"))
    r = doc.create_object("rectangle", (5, 5, 80, 60), None)
    dlg = ObjectPropertiesDialog(doc, r)
    dlg.show_page("visibility")
    _f, w = dlg.field_entries("VISIBLE")[0]
    w.setText("Tag \u4e2d")  # a character a page cannot hold
    assert not dlg.apply()
    assert theme.is_invalid(w)
    w.setText("Tag1")
    dlg._check_live()
    assert not theme.is_invalid(w)


def test_settings_of_the_panel_are_declared():
    from ctgedit.ui.prefs import settings as ST

    for k in (
        "props/label_layout",
        "props/collapsed",
        "props/show_private_keys",
        "props/lock_aspect",
        "ui/geometry_reference",
    ):
        assert k in ST.SETTINGS_KEYS
    assert ST.OPTION_DEFAULTS["props_arrow_live"] is True
    assert ST.OPTION_DEFAULTS["props_scrub"] is True
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    flags = dict(SettingsDialog.FLAGS)
    assert "props_arrow_live" in flags and "props_scrub" in flags


def test_a_fixed_field_loses_its_frame_while_another_tab_has_a_problem(fx_page, fx_project):
    """The fake install's FxAnim (FxMain): a page with a pages.DBF row."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.style import theme
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    _app()
    doc = EditableDocument.open(fx_page("FxAnim"))
    dlg = PagePropertiesDialog(doc, Project(fx_project("FxMain")))
    dlg._title.setText("x" * 70)
    dlg._revalidate()
    assert theme.is_invalid(dlg._title)
    dlg._check_associations = lambda: "The name 'a b' is not valid."
    dlg._title.setText("FxAnim")
    dlg._revalidate()
    assert not theme.is_invalid(dlg._title)  # fixed: no frame left behind
    assert not dlg._btn_ok.isEnabled()  # (the association still blocks OK)
    dlg._title.setText("y" * 70)
    dlg._revalidate()
    assert theme.is_invalid(dlg._title)  # framed under the other problem too
