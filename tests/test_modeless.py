"""Modeless windows follow their object, page or selection."""

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the two pages the tests open (FxShapes holds the framed Go To's 900, 600 box)
MIXER = "FxShapes"
GENIES = "FxGenies"
MAIN_WINDOW = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit", "ui", "main_window.py"
)


def _pump():
    QtWidgets.QApplication.processEvents()


@pytest.fixture()
def win(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    for dlg in list(w._dialogs.values()):
        dlg._ctg_dropped = True
        dlg.reject()
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()
    w.deleteLater()
    _pump()


def _copy(fx_install, name):
    """The page ``name`` of FxMain in the fx_install copy (a copy already: a test may
    write it)."""
    return fx_install.page(name)


def _rects(win, *boxes):
    """New rectangles on the page (one undo step each)."""
    out = []
    for b in boxes:
        out.append(win.doc.create_object("rectangle", rect=b, colour=0x336699))
    win._refresh()
    return out


class _StubSheet(QtWidgets.QDialog):
    """An object window with the whole protocol (retarget / reload /
    has_pending_edits): what the object sheet offers once it follows."""

    applied = QtCore.Signal()

    def __init__(self, doc, index, parent):
        super().__init__(parent)
        self.doc, self.index = doc, index
        self.reloads = 0
        self.retargets = []
        self.pending = False
        self.edit = QtWidgets.QLineEdit(self)
        QtWidgets.QVBoxLayout(self).addWidget(self.edit)

    def retarget(self, index):
        self.retargets.append(index)
        self.index = index

    def reload(self):
        self.reloads += 1

    def has_pending_edits(self):
        return self.pending

    def apply(self):
        ok = self.doc.ensure_property(self.index, "COMMENT", self.edit.text())
        self.applied.emit()
        return ok


def _stub(win, index):
    dlg = _StubSheet(win.doc, index, win)
    assert win._show_modeless(dlg, ("objprops", index), scope="object") is dlg
    return dlg


# ------------------------------------------------------------------ object windows
def test_object_window_follows_its_record_when_an_earlier_object_is_deleted(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    a, b = _rects(win, (20, 20, 60, 50), (80, 20, 120, 50))
    target = win.doc.parsed.page.objects[b]
    dlg = _stub(win, b)
    assert win._dialogs.get(("objprops", b)) is dlg
    win._delete_indices([a])
    assert not getattr(dlg, "_ctg_dropped", False)
    assert win._dialogs.get(("objprops", b - 1)) is dlg  # the short key: its index now
    assert dlg._ctg_index == b - 1 and dlg.retargets == [b - 1]
    assert win.doc.parsed.page.objects[dlg._ctg_index] is target
    dlg.edit.setText("follows")
    assert dlg.apply()
    props = win.doc.object_properties(b - 1)
    assert props.get("COMMENT") == "follows"
    assert dlg.isVisible()


def test_real_sheet_follows_or_closes_with_the_moved_message(win, fx_install):
    """The object sheet: it follows its record when it can re-target itself,
    else it closes with a message -- never edits another
    object."""
    win.open_document(_copy(fx_install, MIXER))
    a, b = _rects(win, (20, 20, 60, 50), (80, 20, 120, 50))
    win.object_properties(b)
    dlg = win._dialogs.get(("objprops", b))
    assert dlg is not None and not dlg.isModal()
    win._delete_indices([a])
    if hasattr(dlg, "retarget"):
        assert win._dialogs.get(("objprops", b - 1)) is dlg and dlg._ctg_index == b - 1
    else:
        assert dlg not in win._dialogs.values()
        assert "moved" in win.statusBar().currentMessage()


def test_unrelated_undo_keeps_the_sheet_and_what_was_typed(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    a, b = _rects(win, (20, 20, 60, 50), (80, 20, 120, 50))
    win.object_properties(a)
    dlg = win._dialogs.get(("objprops", a))
    dlg._editors["VISIBLE"].setText("Tag1 = 1")
    win.doc.move_objects([b], 5, 5)  # another object
    win._refresh()
    win.undo()
    assert win._dialogs.get(("objprops", a)) is dlg and dlg.isVisible()
    assert dlg._editors["VISIBLE"].text() == "Tag1 = 1"


def test_undo_of_its_own_object_reloads_it(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    (a,) = _rects(win, (20, 20, 60, 50))
    dlg = _stub(win, a)
    win.doc.move_objects([a], 5, 5)
    win._refresh()
    before = dlg.reloads
    assert before >= 1  # a move of its object re-reads it
    win.undo()
    assert dlg.reloads == before + 1 and dlg.isVisible()


def test_own_apply_does_not_reload(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    (a,) = _rects(win, (20, 20, 60, 50))
    dlg = _stub(win, a)
    dlg.edit.setText("mine")
    assert dlg.apply()
    assert dlg.reloads == 0 and dlg.isVisible()


def test_deleting_its_object_closes_it_with_the_removed_message(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    (a,) = _rects(win, (20, 20, 60, 50))
    win.object_properties(a)
    dlg = win._dialogs.get(("objprops", a))
    assert dlg is not None
    win._delete_indices([a])
    assert dlg not in win._dialogs.values()
    assert win.statusBar().currentMessage() == "The object was removed, so its Properties closed."
    win.undo()  # the object is back: no window
    assert not any(isinstance(d, type(dlg)) for d in win._dialogs.values())


def test_page_tabs_hide_and_show_their_windows(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    mixer = win.doc
    (a,) = _rects(win, (20, 20, 60, 50))
    win.object_properties(a)
    sheet = win._dialogs.get(("objprops", a))
    win._adjust_dialog()
    adjust = win._dialogs.get("adjust")
    assert sheet.isVisible() and adjust.isVisible()
    win.open_document(_copy(fx_install, GENIES))
    assert win.doc is not mixer
    assert sheet.isHidden() and sheet in win._dialogs.values()
    assert adjust.isVisible() and win._dialogs.get("adjust") is adjust
    win.set_document(mixer)
    assert sheet.isVisible() and win._dialogs.get(("objprops", a)) is sheet
    assert adjust.isVisible()


def test_page_edits_that_change_no_record_reload_page_properties(win, fx_install):
    """Page edits that change no record reload Page Properties."""
    win.open_document(_copy(fx_install, MIXER))
    doc = win.doc
    win.page_properties()
    dlg = win._dialogs[("pageprops", "%x" % id(doc))]
    old = dlg._btn_colour.pair()[0] & 0xFFFFFF
    new = 0x123456 if old != 0x123456 else 0x654321
    serial = doc.derive_serial
    doc.set_page_background(new)
    assert doc.derive_serial == serial  # no record changed, no derive
    win._after_edit()  # the Properties panel's change signal
    assert getattr(dlg, "_ctg_reload_pending", False)
    win._run_page_reload(dlg)
    assert dlg._btn_colour.pair()[0] & 0xFFFFFF == new
    assert not dlg.has_pending_edits()
    dlg.apply()
    assert doc.parsed.attributes.bg_color0 & 0xFFFFFF == new
    # an Undo of it (no derive either) is followed too
    doc.undo()
    win._sync_modeless("refresh")
    assert getattr(dlg, "_ctg_reload_pending", False)
    win._run_page_reload(dlg)
    assert dlg._btn_colour.pair()[0] & 0xFFFFFF == old
    # the window's own Apply is not reloaded
    w0 = dlg._sp_w.value()
    dlg._sp_w.setValue(w0 + 8)
    assert dlg.apply() is True
    assert not getattr(dlg, "_ctg_reload_pending", False)
    assert dlg._sp_w.value() == w0 + 8


class _NoReloadSheet(QtWidgets.QDialog):
    """An object window that cannot re-read itself."""


def test_a_hidden_page_changed_by_replace_is_followed_when_shown(win, fx_install):
    """Replace / Update Pages over a page not shown: its windows re-read it
    when its tab is shown; one that cannot re-read itself closes then."""
    win.open_document(_copy(fx_install, MIXER))
    mixer = win.doc
    a, b = _rects(win, (20, 20, 60, 50), (80, 20, 120, 50))
    follows = _stub(win, a)
    old = _NoReloadSheet(win)
    win._show_modeless(old, ("symset", b), scope="object")
    win.open_document(_copy(fx_install, GENIES))
    assert follows.isHidden() and old.isHidden()
    # a replacement changes both objects of the page not shown
    assert mixer.ensure_property(a, "COMMENT", "replaced")
    assert mixer.ensure_property(b, "COMMENT", "replaced")
    win._mark_modeless_reason([mixer], "replace")
    n = follows.reloads
    win.set_document(mixer)
    assert follows.isVisible() and follows.reloads == n + 1
    assert old not in win._dialogs.values()


def test_closing_a_page_with_typed_values_asks(win, fx_install, monkeypatch):
    win.open_document(_copy(fx_install, MIXER))
    (a,) = _rects(win, (20, 20, 60, 50))
    win.doc.dirty = False
    dlg = _stub(win, a)
    dlg.pending = True
    asked = []

    def question(_parent, _title, text, *_a, **_k):
        asked.append(text)
        return QtWidgets.QMessageBox.Discard

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", question)
    assert win._close_doc_tab(0) is not False
    assert asked and "Not applied yet" in asked[0]
    assert dlg not in win._dialogs.values()


class _OkApplies(QtWidgets.QDialog):
    """An object window whose OK is its apply (Genie Parameters, Presentation
    Options): typed values, no apply() of its own; ``refuse`` = its OK checks
    keep it open."""

    def __init__(self, parent, refuse=False):
        super().__init__(parent)
        self.refuse = refuse

    def has_pending_edits(self):
        return True

    def accept(self):
        if not self.refuse:
            super().accept()


def test_save_question_applies_a_window_whose_ok_is_its_apply(win, fx_install, monkeypatch):
    """Closing a page with typed values in Genie Parameters (no apply(): its
    OK applies): Save runs the window's OK, so the values are not lost; a
    window whose OK refuses keeps the page open."""
    win.open_document(_copy(fx_install, MIXER))
    a, b = _rects(win, (20, 20, 60, 50), (80, 20, 120, 50))
    win.doc.dirty = False
    applied = []
    ok = _OkApplies(win)
    win._show_modeless(ok, ("genie", a), on_accept=applied.append, scope="object")
    stuck = _OkApplies(win, refuse=True)
    win._show_modeless(stuck, ("symset", b), on_accept=applied.append, scope="object")
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question", staticmethod(lambda *_a, **_k: QtWidgets.QMessageBox.Save)
    )
    assert win._close_doc_tab(0) is False  # the refusing window keeps it
    assert applied == [ok] and stuck.isVisible()
    stuck.refuse = False
    assert win._close_doc_tab(0) is not False
    assert applied == [ok, stuck]
    assert not win._dialogs


def test_element_member_sheet_stays_one_window_after_its_apply(win, fx_install):
    """An element member's sheet stays one window after Apply."""
    lib = fx_install.file("FxLib", "fxparts.ctm")
    win._open_element(lib, "fx_agitator_mixer")
    doc = win.doc
    i = next(k for k, o in enumerate(doc.page.objects) if o.raw.get("_type_code") == 0x05)
    win.object_properties(i)
    dlg = win._dialogs.get(("objprops", i))
    assert dlg is not None
    field = (dlg._widgets.get("COMMENT") or dlg._widgets.get("DESC"))[0][1]
    field.setText("tip")
    old = doc.parsed.page.objects[i]
    assert dlg.apply()
    assert doc.parsed.page.objects[i] is not old and dlg.isVisible()
    win.object_properties(i)  # the same window, raised
    sheets = [d for d in win._dialogs.values() if type(d) is type(dlg)]
    assert sheets == [dlg] and win._dialogs.get(("objprops", i)) is dlg
    win.undo()
    assert win._dialogs.get(("objprops", i)) is dlg and dlg.isVisible()
    assert dlg._ctg_target is doc.parsed.page.objects[i] is old
    assert field.text() != "tip" and not dlg.has_pending_edits(), "it shows the undone value"
    win.object_properties(i)  # still the one window
    assert [d for d in win._dialogs.values() if type(d) is type(dlg)] == [dlg]
    dlg.reject()
    _pump()
    assert not win._dialogs


def test_two_windows_closing_at_once_say_so_in_one_line(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    a, b = _rects(win, (20, 20, 60, 50), (80, 20, 120, 50))
    win.object_properties(a)
    win.object_properties(b)
    win._delete_indices([a, b])
    assert win.statusBar().currentMessage() == (
        "The object was removed, so its Properties closed. 1 other window closed too."
    )


def test_closing_the_editor_window_closes_its_windows(win, fx_install):
    """Another editor window can keep the program running: Grid Setup, Goto
    Object, Align or a sheet of a closed window must not float on."""
    win.open_document(_copy(fx_install, MIXER))
    a, b = _rects(win, (20, 20, 60, 50), (80, 20, 120, 50))
    win.doc.dirty = False
    win.object_properties(a)
    win._set_selection([a, b])
    win._align_dialog()
    win._grid_setup_dialog()
    win._goto_dialog()
    opened = list(win._dialogs.values())
    assert len(opened) == 4 and all(d.isVisible() for d in opened)
    win.show()
    win.close()
    _pump()
    assert not win._dialogs
    assert not any(_alive_and_visible(d) for d in opened)


def _alive_and_visible(dlg) -> bool:
    import shiboken6

    return shiboken6.isValid(dlg) and dlg.isVisible()


# ------------------------------------------------------------------ selection windows
def test_align_is_modeless_changes_nothing_before_apply_and_is_one_step(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    a, b, c = _rects(win, (100, 60, 140, 90), (220, 120, 300, 200), (160, 300, 180, 310))
    doc = win.doc
    win._set_selection([a, b])
    win._align_dialog()
    dlg = win._dialogs.get("align")
    assert dlg is not None and not dlg.isModal() and dlg.isVisible()
    snap = [bytes(po.body_raw) for po in doc.parsed.page.objects]
    n0 = len(doc._undo)
    dlg.set_choice(vertical="top", horizontal="left")
    assert [bytes(po.body_raw) for po in doc.parsed.page.objects] == snap
    assert len(doc._undo) == n0
    # the selection changes while it is open: Apply aligns the new one
    win._set_selection([a, b, c])
    assert dlg.units() == 3
    dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).click()
    assert len(doc._undo) == n0 + 1 and doc._undo[-1].label == "align"
    from ctgedit.format import geometry as G

    tops = {G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1[1] for i in (a, b, c)}
    assert len(tops) == 1
    assert dlg.isVisible()
    # fewer than two objects: Apply off, with a note
    win._set_selection([a])
    assert not dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).isEnabled()
    assert "two or more" in dlg._note.text()


def test_swap_colours_reads_its_targets_at_apply(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    a, b = _rects(win, (20, 20, 60, 50), (80, 20, 120, 50))
    doc = win.doc
    win._set_selection([a])
    win._swap_colors()
    dlg = win._dialogs.get("swapcol")
    assert dlg is not None and not dlg.isModal()
    assert dlg.scope() == "selection" and "1 object" in dlg.apply_to.selection.text()
    dlg.set_from((0x336699, 0x336699))
    dlg.set_to((0x00FF00, 0x00FF00))
    win._set_selection([b])  # the selection moves on
    dlg._apply()
    from ctgedit.format import colourstate as CS

    fill_a = CS.colour_channels(doc.parsed.page.objects[a])
    fill_b = CS.colour_channels(doc.parsed.page.objects[b])
    assert fill_b != fill_a  # only b took the new colour
    assert dlg.isVisible()
    win._set_selection([])
    assert dlg.scope() == "page" and not dlg.apply_to.selection.isEnabled()


def test_font_window_follows_the_selection(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    doc = win.doc
    t = doc.create_object("text", rect=(20, 20, 80, 40), text="Hello")
    win._refresh()
    win._set_selection([t])
    win._font_dialog()
    dlg = win._dialogs.get("font")
    assert dlg is not None and not dlg.isModal() and dlg.count() == 1
    (a,) = _rects(win, (100, 100, 140, 130))
    win._set_selection([a])
    assert dlg.count() == 0
    assert not dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).isEnabled()
    win._set_selection([t])
    dlg._sp_size.setValue(21)
    n0 = len(doc._undo)
    dlg._apply()
    assert len(doc._undo) == n0 + 1
    assert doc.page.objects[t].style.font_size == 21


# ------------------------------------------------------------------ window windows
def test_goto_object_is_modeless_and_rebuilds_after_a_delete(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    a, b = _rects(win, (20, 20, 60, 50), (80, 20, 120, 50))
    win._goto_dialog()
    dlg = win._dialogs.get("goto")
    assert dlg is not None and not dlg.isModal()
    n0 = dlg.object_count()
    rec = win.doc.parsed.page.objects[b]
    dlg._tree.setCurrentItem(dlg._items[b])
    win._delete_indices([a])
    win._run_window_sync()
    assert dlg.object_count() == n0 - 1
    cur = dlg.current_index()
    assert win.doc.parsed.page.objects[cur] is rec  # the row stays on its object
    got = []
    dlg.propertiesRequested.connect(got.append)
    dlg._ok()  # Properties…: the window stays
    assert got == [cur] and dlg.isVisible()
    assert win._dialogs.get(("objprops", cur)) is not None


def test_goto_rows_never_act_on_another_object_before_the_rebuild(win, fx_install):
    """The tree is rebuilt a moment after an edit or a tab switch; a row
    picked before that follows its record (Go To selects and frames the
    object where it is now), and a row of the page left selects nothing."""
    win.resize(1200, 800)
    win.show()
    win.open_document(_copy(fx_install, MIXER))
    _pump()
    mixer = win.doc
    a, b = _rects(win, (20, 20, 60, 50), (900, 600, 940, 630))
    win._goto_dialog()
    dlg = win._dialogs.get("goto")
    rec = mixer.parsed.page.objects[b]
    dlg._tree.setCurrentItem(dlg._items[b])
    win._delete_indices([a])  # no rebuild yet (debounced)
    assert dlg._items.get(b) is not None  # the row still says index b
    win.view.fit()
    zoom = win.view.zoom()
    dlg._go_to()
    assert [mixer.parsed.page.objects[i] for i in win._sel] == [rec]
    assert win.view.zoom() > zoom  # framed: zoomed in on it
    centre = win.view.mapToScene(win.view.viewport().rect().center())
    assert abs(centre.x() - 920) < 60 and abs(centre.y() - 615) < 60
    _pump()  # the stale tree was rebuilt
    assert mixer.parsed.page.objects[dlg.current_index()] is rec
    # a tab switch: the old page's rows select nothing on the new page
    win.open_document(_copy(fx_install, GENIES))
    win._set_selection([])
    row = next(i for i in dlg._items if i > 0)
    if dlg._doc is mixer:  # (not rebuilt yet: the usual case)
        win._goto_highlight(row)
        assert win._sel == []
    _pump()
    win._run_window_sync()
    assert dlg._doc is win.doc


def test_goto_object_follows_a_click_on_another_object(win, fx_install):
    """A click on another object on the page moves Goto Object's current row there
    (and shows its AN); it selects nothing back."""
    win.open_document(_copy(fx_install, MIXER))
    a, b = _rects(win, (20, 20, 60, 50), (80, 20, 120, 50))
    win._set_selection([a])
    win._goto_dialog()
    dlg = win._dialogs.get("goto")
    assert dlg.current_index() == a
    picked = []
    dlg.highlighted.connect(picked.append)
    win._on_canvas_selection([b])
    assert dlg.current_index() == b and picked == []
    an = win.doc.page.objects[b].an
    assert dlg._filter.text() == str(an)
    # after an edit the tree is rebuilt first, then follows
    win.doc.move_object(b, 5, 0)
    win._refresh()
    win._on_canvas_selection([a])
    assert win.doc.parsed.page.objects[dlg.current_index()] is win.doc.parsed.page.objects[a]
    win._on_canvas_selection([a, b])  # several: the row stays
    assert dlg.current_index() == a


def test_goto_object_columns_are_chosen_and_remembered(win, fx_install):
    from ctgedit.ui.dialog.dialogs import GOTO_COLUMNS, goto_columns

    win.open_document(_copy(fx_install, MIXER))
    (a,) = _rects(win, (20, 20, 60, 50))
    doc = win.doc
    doc.ensure_property(a, "COMMENT", "the tank")
    doc.ensure_property(a, "FIL", "Tank_Level")
    doc.ensure_property(a, "FILMAXPC", "100")
    doc.set_metadata(a, [("Equipment", "T1")])
    doc.set_input_commands(a, touch={"CMD": "Beep(0)"})
    win._refresh()
    assert goto_columns() == ["where"]  # by default as before
    win._goto_dialog()
    dlg = win._dialogs.get("goto")
    keys = [k for k, _h, _d in GOTO_COLUMNS]
    hidden = [dlg._tree.isColumnHidden(1 + k) for k in range(len(keys))]
    assert hidden == [k != "where" for k in keys]
    for key in ("fill", "tooltip", "animations", "commands", "metadata", "size"):
        dlg.show_column(key)
    assert dlg.column_text(a, "tooltip") == "the tank"
    assert "FIL = Tank_Level" in dlg.column_text(a, "animations")
    assert dlg.column_text(a, "commands") == "up: Beep(0)"
    assert dlg.column_text(a, "metadata") == "Equipment = T1"
    assert dlg.column_text(a, "size") == "40 x 30"
    assert dlg.column_text(a, "fill").startswith("#")
    assert goto_columns() == ["where", "size", "fill", "animations", "commands", "tooltip", "metadata"]
    # the typed text also finds what the shown columns say
    dlg._filter.setText("Tank_Level")
    assert a in dlg.matches()
    dlg.show_column("where", False)
    dlg.reject()
    win._dialogs.pop("goto", None)
    win._goto_dialog()  # a new window keeps the choice
    dlg2 = win._dialogs.get("goto")
    assert dlg2.shown_columns() == ["size", "fill", "animations", "commands", "tooltip", "metadata"]
    dlg2.default_columns()
    assert goto_columns() == ["where"]


def test_grid_setup_apply_shows_the_grid(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    win.act_grid.setChecked(False)
    win._toggle_grid()
    win._grid_setup_dialog()
    dlg = win._dialogs.get("grid")
    assert dlg is not None and not dlg.isModal()
    assert not dlg._chk_display.isChecked()
    dlg._sp_pitch.setValue(12)
    dlg._chk_display.setChecked(True)
    dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).click()
    assert win._show_grid and win.act_grid.isChecked() and win._grid_size == 12
    assert dlg.isVisible() and not dlg.has_pending_edits()
    # F8 / View > Show Grid follow into the window
    win.act_snap.setChecked(False)
    win._toggle_snap()
    assert not dlg._chk_snap.isChecked()


def test_guidelines_follow_the_page_shown(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    mixer = win.doc
    win._guidelines_dialog()
    dlg = win._dialogs.get("guides")
    assert dlg is not None and not dlg.isModal() and "FxShapes" in dlg.windowTitle()
    dlg._rb_v.setChecked(True)
    dlg._sp_pos.setValue(120)
    dlg._set_pos()
    dlg.applyRequested.emit()
    assert win.view.guidelines()[0] == [120]
    win.open_document(_copy(fx_install, GENIES))
    assert "FxGenies" in dlg.windowTitle() and dlg.values()[0] == []
    win.set_document(mixer)
    assert dlg.values()[0] == [120]


def test_compile_results_are_replaced_by_the_next_compile(win, fx_install, monkeypatch):
    from types import SimpleNamespace
    from ctgedit.backend import compiler

    win.open_document(_copy(fx_install, MIXER))
    win.doc.dirty = False
    msg = SimpleNamespace(
        severity="error",
        code="E1",
        project="FxMain",
        file="FxShapes",
        line=1,
        message="Something",
        context="",
    )
    res = SimpleNamespace(
        project="FxMain", outcome="completed", errors=[msg], warnings=[], seconds=1.0, note="", messages=[msg]
    )
    monkeypatch.setattr(compiler, "root_project_for", lambda _d: "FxMain")
    monkeypatch.setattr(compiler, "compile_project", lambda *_a, **_k: res)
    win.compile_project()
    first = win._dialogs.get("compile")
    assert first is not None and not first.isModal() and first.isVisible()
    win.compile_project()
    second = win._dialogs.get("compile")
    assert second is not None and second is not first
    assert first not in win._dialogs.values()


def test_used_by_stays_open_after_open_page(win, fx_install, monkeypatch):
    from ctgedit.io import genieusage as GU

    page = _copy(fx_install, MIXER)
    lib = fx_install.file("FxLib", "fxgenie.ctm")
    target = GU.Ref("element", lib, "fx_pump", "FxLib")
    ref = GU.Ref("page", page, "", "FxMain")
    usage = GU.Usage(target, [GU.User(ref, [GU.Link(ref, target, "fxgenie.fx_pump.<ctm#1", 1, 1)], (), True)])
    monkeypatch.setattr(GU, "element_users", lambda *_a, **_k: usage)
    opened = []
    monkeypatch.setattr(type(win), "open_page", lambda self, p: opened.append(p) or True)
    win._show_used_by(lib, "fx_pump")
    dlg = next(d for d in win._dialogs.values() if type(d).__name__ == "ElementUsersDialog")
    assert not dlg.isModal()
    g = dlg._tree.topLevelItem(0)
    dlg._tree.setCurrentItem(g.child(0))
    dlg._btn_open.click()
    assert opened == [page] and dlg.isVisible()
    assert not dlg._btn_update.isEnabled()  # everything up to date
    win._show_used_by(lib, "fx_pump")  # one window per element
    assert [type(d).__name__ for d in win._dialogs.values()].count("ElementUsersDialog") == 1


def test_used_by_from_the_page_browser_still_closes_on_open():
    _app()
    from types import SimpleNamespace
    from ctgedit.ui.dialog.library_dialogs import UsedByDialog

    usage = [SimpleNamespace(name="FxShapes", project="FxMain", title="", path="x.ctg")]
    got = []
    dlg = UsedByDialog("fxgenie.ctm", "fx_pump", usage, None, on_open=got.append)
    dlg.show()
    dlg._btn_open.click()
    assert got == ["x.ctg"] and dlg.result() == QtWidgets.QDialog.Accepted


# ------------------------------------------------------------------ bitmap editor
def test_bitmap_editor_save_refuses_after_its_picture_changed(win, fx_install):
    win.open_document(_copy(fx_install, MIXER))
    doc = win.doc
    red = bytes([255, 0, 0, 255]) * (8 * 6)
    i = doc.add_bitmap_object((30, 30, 38, 36), red, 8, 6)
    win._refresh()
    win._set_selection([i])
    win._bitmap_editor()
    dlg = win._dialogs.get(("bitmap", i))
    assert dlg is not None and not dlg.isModal()
    assert dlg.canvas.put(1, 1, (0xFF00FF00, 0xFF00FF00))
    assert dlg.has_pending_edits()
    blue = bytes([0, 0, 255, 255]) * (8 * 6)
    assert doc.replace_bitmap(i, (30, 30, 38, 36), blue, 8, 6)  # changed on the page
    win._refresh()
    assert dlg.isVisible()  # never closed by the page
    n0 = len(doc._undo)
    dlg._save()
    assert "changed on the page" in dlg._problem.text()
    assert dlg.isVisible() and dlg.has_pending_edits() and len(doc._undo) == n0
    win.undo()  # its picture is back
    dlg._save()
    assert len(doc._undo) == n0 and not dlg.isVisible()


# ------------------------------------------------------------------ source scan
def test_converted_dialogs_are_never_exec_d_in_the_main_window():
    src = open(MAIN_WINDOW, encoding="utf-8").read()
    names = (
        "AlignDialog",
        "GotoObjectDialog",
        "GridSetupDialog",
        "GuidelinesDialog",
        "FontDialog",
        "SwapColorsDialog",
        "AdjustColorsDialog",
        "BitmapEditorDialog",
        "CompileResultsDialog",
        "UsedByDialog",
    )
    for name in names:
        assert not re.search(r"%s\([^)]*\)\.exec\(" % name, src), name
    # every dlg = <converted>(...) is followed by _show_modeless, not dlg.exec()
    for m in re.finditer(r"dlg = (%s)\(" % "|".join(names), src):
        rest = src[m.end() : m.end() + 1500]
        nxt = min((k for k in (rest.find("dlg.exec()"), rest.find("_show_modeless(")) if k >= 0), default=-1)
        assert nxt >= 0 and rest[nxt:].startswith("_show_modeless("), m.group(1)
