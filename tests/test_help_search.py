"""Manual search and the help window."""

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit import help as H  # noqa: E402
from qtkit import app as _app


def _pump(n=4):
    for _ in range(n):
        _app().processEvents()


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui import help as UH
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1600, 1000)
    w.show()
    _pump()
    UH._last_main[:] = []
    yield w
    from ctgedit.ui.dialog.help_window import HelpWindow

    hw = HelpWindow._inst
    if hw is not None:
        hw.hide()
    for m in w.menuBar().findChildren(QtWidgets.QMenu):
        m.hide()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()
    _pump()


def _close_popups():
    for _ in range(4):
        pop = QtWidgets.QApplication.activePopupWidget()
        if pop is None:
            break
        pop.close()
        _pump()


# --------------------------------------------------------------------------- the search
def test_a_hit_per_section():
    hits = H.search_sections("smart guides")
    assert hits[0] == H.SectionHit("drawing", "smart-guides", "Smart guides", hits[0].snippet)
    topics = [h.topic for h in hits]
    # the same page with two matching sections lists both
    keys = [(h.topic, h.anchor) for h in hits]
    assert len(keys) == len(set(keys)) and len(set(topics)) < len(topics) or len(hits) > 3
    assert H.search_sections("zzqqxx") == []
    assert H.search_sections("") == []
    # every word must be there; the heading that is the phrase first
    g = H.search_sections("grid and snap")
    assert (g[0].topic, g[0].anchor) == ("drawing", "grid-and-snap")
    # the page search answers per page
    assert H.search("genie")[0][0] == "genies"


def test_snippets_are_clean_text():
    for q in ("smart guides", "alt", "template", "keyboard", "genie parameters", "tags panel"):
        for h in H.search_sections(q):
            s = h.snippet
            assert "[[" not in s and "]]" not in s and "**" not in s and "](" not in s, s
            assert "`" not in s and not re.search(r"(^|\s)- ", s), s
            assert not re.search(r"\s:\s", s.replace(" · ", " ")), s  # ('Placing : smart')
            # cut at word boundaries, with an ellipsis where it was cut
            body = s.strip("…")
            assert body == body.strip()
            if s.startswith("…"):
                assert not s[1:2].isspace()
    # (the snippet is the part of the section around the words searched for)
    s = next(h.snippet for h in H.search_sections("without snapping") if h.anchor == "move")
    assert "Alt+drag moves without snapping" in s


def test_plain_text_drops_the_markup():
    assert H.plain_text("- **Bold** [[Alt]] [a link](x.md) `code` *it*") == "Bold Alt a link code it"
    assert H.plain_text("> **Tip:** hold [[Shift]]") == "Tip: hold Shift"
    assert H.plain_text("| a | b |\n|---|---|\n| c | d |") == "a · b c · d"
    secs = H.sections("drawing")
    s = next(x for x in secs if x.anchor == "smart-guides")
    assert s.heading == "Smart guides" and s.level == 2 and "Alt" in s.keys


def test_the_window_lists_commands_then_sections(win):
    from ctgedit.ui import help as UH

    hw = UH.show_help("index", main=win)
    hw.search_for("smart guides")
    rows = hw.result_rows()
    assert rows[0].kind == "command" and rows[0].text == "Command: View > Smart Guides"
    assert rows[0].check is win.act_smart_guides.isChecked()
    assert len([r for r in rows if r.kind == "command"]) <= 3
    sec = next(r for r in rows if r.kind == "help")
    assert sec.text == "Drawing objects > Smart guides"
    # the snippet's words in bold, its keys as key caps
    dl = hw.results.itemDelegate()
    html = dl.snippet_html(sec)
    assert "<b>Smart Guides</b>" in html or "<b>Smart</b>" in html
    hw.search_for("without snapping")
    mv = next(r for r in hw.result_rows() if r.kind == "help" and r.data.anchor == "move")
    assert "background-color" in dl.snippet_html(mv) and "Alt" in dl.snippet_html(mv)


def test_a_section_opens_on_the_first_word(win):
    from ctgedit.ui import help as UH

    hw = UH.show_help("index", main=win)
    hw.search_for("snap radius")
    r = next(r for r in hw.result_rows() if r.kind == "help")
    hw.open_result(r)
    assert hw.current_topic() == r.data.topic and hw.current_anchor() == r.data.anchor
    sel = hw.view.textCursor().selectedText().lower()
    assert sel in ("snap radius", "snap")
    # the selection is in the section, not above it
    assert hw.view.textCursor().position() >= hw.anchor_position(r.data.anchor)


def test_a_command_row_runs_the_command_only_when_asked(win):
    from ctgedit.ui import help as UH

    hw = UH.show_help("index", main=win)
    hw.search_for("smart guides")
    was = win.act_smart_guides.isChecked()
    # a plain click looks it up: shown in the menu, not run
    hw.open_result(hw.result_rows()[0])
    _pump()
    assert win.act_smart_guides.isChecked() is was
    for m in win.menuBar().findChildren(QtWidgets.QMenu):
        m.hide()
    # Shift+click runs it
    hw.open_result(hw.result_rows()[0], run=True)
    try:
        assert win.act_smart_guides.isChecked() is not was
    finally:
        if win.act_smart_guides.isChecked() is not was:
            win.act_smart_guides.trigger()


# --------------------------------------------------------------------------- show: links
def _menu_target(hw, path):
    for k, t in enumerate(hw._show_targets):
        if t[0] == "menu" and t[2] == path:
            return k, t
    raise AssertionError("no link for %s: %s" % (path, [t[2] for t in hw._show_targets if t[0] == "menu"]))


def test_menu_paths_become_show_links(win):
    from ctgedit.ui import help as UH

    hw = UH.show_help("drawing", main=win)
    k, t = _menu_target(hw, "View > Smart Guides")
    assert t[1] is win.act_smart_guides
    assert 'href="show:%d"' % k in hw._page_body("drawing")
    assert hw._describe("show:%d" % k).startswith("Shows View > Smart Guides")
    # a path with a submenu, a command with '…'
    _menu_target(hw, "View > Guidelines > Guidelines Setup…")
    # Settings links name the page and the option after it
    s = [t for t in hw._show_targets if t[0] == "settings"]
    assert ("settings", "Editing", "Arrow-key move step") in s
    # links, code and headings are left alone
    body, targets = UH.link_menu_paths(
        '<h2>View &gt; Rulers</h2><p><a href="x.md">View &gt; Rulers</a> <code>View &gt; '
        "Rulers</code> View &gt; Rulers, View &gt; No Such Thing</p>",
        win,
    )
    assert body.count("show:") == 1 and len(targets) == 1 and targets[0][1] is win.act_rulers


def test_a_click_on_view_smart_guides_opens_the_view_menu_on_it(win):
    from ctgedit.ui import help as UH

    hw = UH.show_help("drawing", main=win)
    _k, t = _menu_target(hw, "View > Smart Guides")
    try:
        assert UH.follow_show(t) == "menu"
        _pump()
        mb = win.menuBar()
        assert mb.activeAction() is not None and mb.activeAction().text() == "&View"
        pop = QtWidgets.QApplication.activePopupWidget()
        assert isinstance(pop, QtWidgets.QMenu) and pop.activeAction() is win.act_smart_guides
    finally:
        _close_popups()


def test_a_submenu_path_opens_each_menu_on_the_way(win):
    from ctgedit.ui import help as UH

    try:
        assert UH.open_menu_at(win, win.act_snap)
        _pump()
        pop = QtWidgets.QApplication.activePopupWidget()
        assert isinstance(pop, QtWidgets.QMenu) and pop.activeAction() is win.act_snap
    finally:
        _close_popups()


def test_shift_click_runs_the_command(win):
    from ctgedit.ui import help as UH

    hw = UH.show_help("drawing", main=win)
    _k, t = _menu_target(hw, "View > Smart Guides")
    was = win.act_smart_guides.isChecked()
    try:
        assert UH.follow_show(t, shift=True) == "ran"
        assert win.act_smart_guides.isChecked() is not was
    finally:
        if win.act_smart_guides.isChecked() is not was:
            win.act_smart_guides.trigger()


def test_the_ribbon_button_lights_up(win):
    from ctgedit.ui import help as UH

    assert UH.FLASH_MS == 1500
    assert UH.flash_action(win, win.act_compile)
    flashes = win.findChildren(QtWidgets.QWidget, "HelpFlash")
    assert flashes and flashes[0].isVisible()
    b = next(
        b
        for b in win.findChildren(QtWidgets.QToolButton)
        if b.defaultAction() is win.act_compile and b.isVisible()
    )
    g = flashes[0].geometry()
    assert g.contains(QtCore.QRect(b.mapTo(win, QtCore.QPoint(0, 0)), b.size()))


def test_a_settings_link_opens_settings_there(win, monkeypatch):
    from ctgedit.ui import help as UH
    from ctgedit.ui.dialog import palette

    opened = []
    monkeypatch.setattr(
        palette, "show_setting", lambda w, page, search="", key="", exec_=True: opened.append((page, search))
    )
    assert UH.follow_show(("settings", "Editing", "Arrow-key move step")) == "settings"
    _pump()
    assert opened == [("Editing", "Arrow-key move step")]
    # the words after the option's name are left out; an unknown one searches nothing
    assert UH.settings_label_for(win, "Arrow-key move step sets") == "Arrow-key move step"
    assert UH.settings_label_for(win, "zzqq yyww") == ""


def test_the_settings_pages_are_the_windows_pages(win):
    from ctgedit.ui import help as UH
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win)
    try:
        assert tuple(dlg._tabs.tabText(k) for k in range(dlg._tabs.count())) == UH.SETTINGS_PAGES
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------------- the window
def test_the_window_keeps_its_size_place_and_contents_pane(win):
    from ctgedit.ui.dialog.help_window import HelpWindow
    from ctgedit.ui.prefs.settings import app_settings

    hw = HelpWindow()
    hw.show()
    _pump()
    hw.resize(700, 480)
    hw.split.setSizes([200, 500])
    _pump()
    left = hw.split.sizes()[0]
    hw.close()
    assert app_settings().value("geometry/HelpWindow")
    assert app_settings().value("help/splitter") is not None
    again = HelpWindow()
    again.show()
    _pump()
    try:
        assert (again.width(), again.height()) == (700, 480)
        assert again.split.sizes()[0] == left
    finally:
        again.close()
        again.deleteLater()
        hw.deleteLater()


def test_ctrl_equals_makes_the_text_larger(win):
    from ctgedit.ui.dialog.help_window import HelpWindow, zoom_factor
    from ctgedit.ui.prefs.settings import app_settings

    def body_pt(w):
        m = re.search(r"body \{[^}]*font-size: ([\d.]+)pt", w.view.document().defaultStyleSheet())
        return float(m.group(1))

    hw = HelpWindow()
    hw.show()
    hw.show_topic("drawing")
    _pump()
    try:
        base = body_pt(hw)
        assert hw.act_zoom_in.shortcuts()[0].toString() == "Ctrl+="
        hw.act_zoom_in.trigger()
        assert hw.zoom == 1 and int(app_settings().value("help/zoom")) == 1
        assert body_pt(hw) == pytest.approx(round(base * zoom_factor(1), 1), abs=0.11)
        hw.act_zoom_out.trigger()
        hw.act_zoom_out.trigger()
        assert hw.zoom == -1 and body_pt(hw) < base
        hw.act_zoom_reset.trigger()
        assert hw.zoom == 0 and body_pt(hw) == base
        assert hw.act_zoom_reset.shortcut().toString() == "Ctrl+0"
        hw.set_zoom(2)
    finally:
        hw.close()
        hw.deleteLater()
    later = HelpWindow()
    try:
        assert later.zoom == 2
    finally:
        later.set_zoom(0)
        later.deleteLater()


def test_the_toolbar_uses_the_apps_icons_and_names_the_keys(win):
    from ctgedit.ui.style import theme
    from ctgedit.ui.dialog.help_window import HelpWindow

    hw = HelpWindow()
    try:
        assert "Alt+Left" in hw.act_back.toolTip() and "Alt+Right" in hw.act_fwd.toolTip()
        assert hw.act_home.text() == "Contents" and "Alt+Home" in hw.act_home.toolTip()
        assert "Ctrl+P" in hw.act_print.toolTip()
        for name in ("nav_back", "nav_forward", "home", "print", "zoom_in", "zoom_out"):
            assert name in theme._GLYPHS, name
        assert not hw.act_print.icon().isNull()
    finally:
        hw.deleteLater()


def test_f1_lights_up_the_heading_it_opened(win):
    from ctgedit.ui import help as UH
    from ctgedit.ui.dialog.help_window import FLASH_MS

    assert FLASH_MS == 1000
    hw = UH.show_help("drawing#smart-guides", main=win)
    assert hw.flashed_anchor == "smart-guides"
    sels = hw.view.extraSelections()
    assert len(sels) == 1
    assert (
        sels[0].format.background().color().name()
        == QtGui.QColor(__import__("ctgedit.ui.style.theme", fromlist=["COLORS"]).COLORS["note_bg"]).name()
    )
    assert sels[0].cursor.block().text() == "Smart guides"
    hw._end_flash()
    assert hw.view.extraSelections() == [] and hw.flashed_anchor == ""
    # a topic without an anchor lights nothing up
    UH.show_help("drawing", main=win)
    assert hw.flashed_anchor == ""


def test_a_miss_in_find_in_page_keeps_the_place(win):
    """Find in page with no match leaves the cursor and the scroll where they were, and
    says so beside the box; a match moves to it."""
    from ctgedit.ui import help as UH

    hw = UH.show_help("drawing", main=win)
    _pump()
    cur = hw.view.textCursor()
    cur.movePosition(cur.MoveOperation.End)
    hw.view.setTextCursor(cur)
    hw.view.ensureCursorVisible()
    _pump()
    pos, scroll = hw.view.textCursor().position(), hw.view.verticalScrollBar().value()
    hw.find_edit.setText("zznomatch99")
    hw._find_next()
    assert hw.view.textCursor().position() == pos
    assert hw.view.verticalScrollBar().value() == scroll
    assert hw.find_note.text() == "No matches on this page"
    hw.find_edit.setText("grid")
    assert hw.find_note.text() == ""
    hw._find_next()
    assert hw.view.textCursor().selectedText().lower() == "grid"
