"""The built-in manual stays complete: pages, links and anchors."""

import os
import re

import pytest

# the F1 tests read the focus and the active window: on the real Windows platform those
# follow the desktop (the foreground lock, the real mouse cursor)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from ctgedit import help as H  # noqa: E402
from qtkit import app as _app

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def win(tmp_path_factory):
    # (the module's window is made before the first test's own settings INI is set:
    # give it one of its own, so it never restores the user's real session)
    _app()
    folder = tmp_path_factory.mktemp("help_win")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CTG_SETTINGS_INI", str(folder / "ctgedit_settings.ini"))
        mp.setenv("CTG_DATA_DIR", str(folder / "ctgedit_data"))
        mp.setenv("CTG_SCADA_ROOT", "none")
        mp.delenv("CTG_LIB_PATH", raising=False)
        from ctgedit.ui.main_window import MainWindow

        w = MainWindow()
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)


def test_markdown_subset():
    out = H.markdown_to_html(
        "# Title\n\nSome **bold**, *italic*, `code` and [[Ctrl+S]].\n\n"
        "## Link a page\n\n- one\n- two\n  - nested\n\n1. first\n2. second\n\n"
        "| A | B |\n|---|---|\n| x | y |\n\n> **Tip:** hello [p](templates.md#x)\n\n"
        "```\nraw <b>\n```\n"
    )
    assert '<a name="title"></a><h1>Title</h1>' in out
    assert '<a name="link-a-page"></a>' in out
    assert "<b>bold</b>" in out and "<i>italic</i>" in out and "<code>code</code>" in out
    assert "<span class='key'>Ctrl+S</span>" in out
    assert "<ul><li>one</li><li>two</li><ul><li>nested</li></ul></ul>" in out
    assert "<ol><li>first</li><li>second</li></ol>" in out
    assert "<th>A</th>" in out and "<td>y</td>" in out
    assert 'class="callout tip"' in out and 'href="templates.md#x"' in out
    assert "raw &lt;b&gt;" in out


def test_every_contents_entry_has_a_page(win):
    missing = [e.id for e in H.walk_toc() if not H.exists(e.id)]
    assert not missing, "contents entries without a page: %s" % missing


def test_no_orphan_pages():
    listed = {e.id for e in H.walk_toc()}
    orphans = [t for t in H.topics() if t not in listed]
    assert not orphans, "pages missing from toc.json: %s" % orphans


def test_every_page_has_a_title_and_content():
    for t in H.topics():
        md = H.page_markdown(t)
        assert md.startswith("# "), "%s.md must start with a '# Title'" % t
        assert len(md) > 400, "%s.md is a stub" % t


def test_no_page_holds_a_control_character():
    """No manual page holds a control character (a '\\1' written through a Python string
    becomes the byte 0x01)."""
    bad = []
    for t in H.topics():
        for k, line in enumerate(H.page_markdown(t).splitlines(), 1):
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", line):
                bad.append("%s.md:%d" % (t, k))
    assert not bad, bad


def test_every_link_resolves(win):
    bad = []
    for t in H.topics():
        for target in H.links(t):
            if target.startswith(("http://", "https://", "mailto:", "file:")):
                continue
            if target.startswith("action:"):
                name = target[len("action:") :]
                # found the way a click finds it (the live QAction by name)
                ok = win.help_action(name) is not None or (
                    not name.startswith("_") and callable(getattr(win, name, None))
                )
                if not ok:
                    bad.append("%s: %s (no act_%s / method)" % (t, target, name))
                continue
            if target.startswith("aveva:"):
                if H.aveva_available() and not os.path.isfile(
                    os.path.join(H.aveva_docs(), "%s.html" % target[6:])
                ):
                    bad.append("%s: %s (no such AVEVA topic)" % (t, target))
                continue
            page, anchor = H.split_link(target)
            page = page or t
            if not H.exists(page):
                bad.append("%s: %s (no page)" % (t, target))
            elif anchor and not H.is_generated(page) and anchor not in H.anchors(page):
                bad.append("%s: %s (no heading #%s in %s)" % (t, target, anchor, page))
    assert not bad, "broken help links:\n" + "\n".join(bad)
    # the manual uses command links (so the click-time lookup is exercised)
    assert any(target.startswith("action:") for t in H.topics() for target in H.links(t))


def _code_topics():
    """Topic strings the code passes to set_help / show_help / add_help_button
    (and the context-help map), and the HELP_* topic constants of the page
    modules (``HELP_TOPIC = "properties#fill-colour"``)."""
    found = set()
    rx = re.compile(
        r"""(?:set_help\([^,]+,|show_help\(|add_help_button\([^,]+,[^,]+,)\s*["']([a-z0-9#-]+)["']"""
    )
    rx2 = re.compile(r"""\(\s*self\.[a-z_]+\s*,\s*["']([a-z0-9#-]+)["']\s*\)""")
    rx3 = re.compile(r"""(?m)^\s*HELP_[A-Z_]*\s*=\s*["']([a-z0-9-]+(?:#[a-z0-9-]+)?)["']""")
    for root, _d, files in os.walk(os.path.join(REPO, "ctgedit")):
        for f in files:
            if f.endswith(".py"):
                src = open(os.path.join(root, f), encoding="utf-8").read()
                found.update(rx.findall(src))
                found.update(rx3.findall(src))
                # (MainWindow's help methods: ui/window/shell.py)
                if "def _install_help" in src:
                    body = src.split("def _install_help", 1)[-1].split("def show_help", 1)[0]
                    found.update(rx2.findall(body))
                if "def context_help_topic" in src:
                    ctx = src.split("def context_help_topic", 1)[-1].split("def run_help_action", 1)[0]
                    found.update(re.findall(r'return "([a-z0-9#-]+)"', ctx))
                    for anchor in re.findall(
                        r'"([a-z-]+)"(?=[,}])', ctx.split(".get(tool", 1)[0].split("{", 1)[-1]
                    ):
                        found.add("drawing#" + anchor)
                    found.add("drawing#drawing-tools")
    return found


def _dialog_classes():
    names = set()
    for root, _d, files in os.walk(os.path.join(REPO, "ctgedit")):
        for f in files:
            if f.endswith(".py"):
                src = open(os.path.join(root, f), encoding="utf-8").read()
                names.update(re.findall(r"(?m)^class (\w+)\((?:QtWidgets\.QDialog|_ChoiceDialog)\)", src))
    names.discard("_ChoiceDialog")
    return names


def test_every_dialog_has_a_help_topic():
    from ctgedit.ui.help import DIALOG_TOPICS

    missing = sorted(_dialog_classes() - set(DIALOG_TOPICS))
    assert not missing, "dialogs without a help topic (ui/help.DIALOG_TOPICS): %s" % missing


#: the Settings dialog's tabs (ui/prefs/settings_dialog.SettingsDialog)
SETTINGS_TABS = (
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
#: the Settings pages documented elsewhere (their own help topic)
SETTINGS_PAGE_TOPICS = {"Simulate": ("simulate", "simulate-settings")}
#: Page Properties' tabs (ui/dialog/page_props.PagePropertiesDialog), CtDraw's six
PAGE_PROPERTY_TABS = ("General", "Appearance", "Keyboard Commands", "Events", "Environment", "Associations")


def _dialog_topic_strings():
    """Static DIALOG_TOPICS values + one anchor per tab of the tabbed dialogs
    (_tab_anchor: 'page#<tab heading>'): Page Properties, Settings and every
    outer tab the object sheet can have (objprops.registry)."""
    from ctgedit.ui.help import DIALOG_TOPICS
    from ctgedit.ui.objprops import _SHEET_TABS
    from ctgedit.ui.objprops import registry as R

    out = {v for v in DIALOG_TOPICS.values() if isinstance(v, str)}
    for tab in PAGE_PROPERTY_TABS:
        out.add("page-properties#" + H.slug(tab))
    for tab in SETTINGS_TABS:
        out.add("settings#" + H.slug(tab))
    out.add("properties#appearance")
    for tab, _sub in _SHEET_TABS:
        out.add("properties#" + H.slug(tab))
    for p in list(R.PAGES) + list(R.OWN_PAGES.values()):
        out.add("properties#" + H.slug(p.tab))
    return out


def test_every_topic_the_code_names_exists(win):
    bad = []
    for topic in sorted(_code_topics() | _dialog_topic_strings()):
        page, _, anchor = topic.partition("#")
        if not H.exists(page):
            bad.append(topic + " (no page)")
        elif anchor and not H.is_generated(page) and anchor not in H.anchors(page):
            bad.append(topic + " (no heading)")
    assert not bad, "help topics named in code but missing:\n" + "\n".join(bad)


def test_search_finds_pages():
    hits = H.search("template")
    assert hits and any(t == "templates" for t, _ti, _s in hits)
    assert H.search("genie")[0][0] == "genies"
    hits = [t for t, _ti, _s in H.search("Update Pages")]
    assert hits and hits[0] == "update-pages"
    assert H.search("zzzqqqxx") == []
    # a limit marker is an author's note: never shown, never searched
    assert H.search("WP26") == []


def test_rendered_pages_have_no_dashes():
    """The renderer turns no ' -- ' into an em dash, and no page has one."""
    bad = []
    for t in H.topics():
        text = H.page_html(t)
        for ch, name in ((chr(0x2014), "em dash"), (chr(0x2013), "en dash")):
            if ch in text:
                bad.append("%s: %s" % (t, name))
    assert not bad, bad
    assert chr(0x2014) not in H.markdown_to_html("one -- two")


def test_resizable_dialogs_remember_their_size(win):
    """A dialog the user resized opens at that size next time (settings key
    geometry/<class name>); a fixed-size dialog keeps its own size."""
    from PySide6 import QtWidgets
    from ctgedit.ui import help as UH
    from ctgedit.ui.prefs.settings import app_settings

    class SizedProbeDialog(QtWidgets.QDialog):
        pass

    class FixedProbeDialog(QtWidgets.QDialog):
        pass

    app_settings().remove(UH.GEOMETRY_PREFIX + "SizedProbeDialog")
    dlg = SizedProbeDialog(win)
    dlg.resize(300, 200)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    dlg.resize(520, 340)
    dlg.done(0)
    assert app_settings().value(UH.GEOMETRY_PREFIX + "SizedProbeDialog") == "520,340"
    again = SizedProbeDialog(win)
    again.resize(300, 200)
    again.show()
    QtWidgets.QApplication.processEvents()
    assert (again.width(), again.height()) == (520, 340)
    again.close()
    fixed = FixedProbeDialog(win)
    fixed.setFixedSize(240, 120)
    assert not UH.remembers_size(fixed)
    assert not UH.remembers_size(QtWidgets.QMessageBox(win))

    class ProbeBox(QtWidgets.QMessageBox):  # a message box of ctgedit's own
        pass

    assert not UH.remembers_size(ProbeBox(win))


def test_dialog_size_is_kept_only_once_resized_and_never_widened(win):
    """A dialog closed at the size it opened at stores nothing (one that
    sizes itself to its content keeps doing so); a word-wrapped label's wide
    size hint never widens the size the user left it at."""
    from PySide6 import QtWidgets
    from ctgedit.ui import help as UH
    from ctgedit.ui.prefs.settings import app_settings

    class ContentProbeDialog(QtWidgets.QDialog):
        pass

    class WrapProbeDialog(QtWidgets.QDialog):
        RESIZE = "both"  # (labels alone would give a fixed size)

        def __init__(self, parent):
            super().__init__(parent)
            lay = QtWidgets.QVBoxLayout(self)
            lab = QtWidgets.QLabel("a long line of text that wraps " * 12)
            lab.setWordWrap(True)
            lay.addWidget(lab)
            self.setMinimumWidth(200)

    for name in ("ContentProbeDialog", "WrapProbeDialog"):
        app_settings().remove(UH.GEOMETRY_PREFIX + name)
    dlg = ContentProbeDialog(win)
    dlg.resize(310, 210)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    dlg.done(0)
    assert app_settings().value(UH.GEOMETRY_PREFIX + "ContentProbeDialog") is None

    dlg = WrapProbeDialog(win)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    dlg.resize(260, 400)
    QtWidgets.QApplication.processEvents()
    left = (dlg.width(), dlg.height())
    dlg.done(0)
    again = WrapProbeDialog(win)
    assert again.sizeHint().width() > left[0]  # the wrapped label's wide size hint
    again.show()
    QtWidgets.QApplication.processEvents()
    assert (again.width(), again.height()) == left
    again.close()


def test_help_window_renders_every_page(win):
    from ctgedit.ui.dialog.help_window import HelpWindow

    hw = HelpWindow.instance()
    for e in H.walk_toc():
        hw.show_topic(e.id)
        assert hw.current_topic() == e.id
        assert hw.view.toPlainText().strip()
    hw.show_topic("keyboard-shortcuts")
    text = hw.view.toPlainText()
    assert "Keyboard shortcuts" in text and "Ctrl+S" in text
    hw.back()
    assert hw.current_topic() != "keyboard-shortcuts"


def test_f1_opens_the_focused_dialogs_topic(win):
    from PySide6 import QtCore, QtGui, QtWidgets
    from ctgedit.ui import help as UH
    from ctgedit.ui.dialog.help_window import HelpWindow

    dlg = QtWidgets.QDialog(win)
    edit = QtWidgets.QLineEdit(dlg)
    UH.set_help(dlg, "page-properties")
    dlg.show()
    edit.setFocus()
    QtWidgets.QApplication.processEvents()
    ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_F1, QtCore.Qt.NoModifier)
    QtWidgets.QApplication.sendEvent(edit, ev)
    assert HelpWindow.instance().current_topic() == "page-properties"
    dlg.close()
    HelpWindow.instance().close()


def test_shown_dialogs_get_a_help_button(win):
    from PySide6 import QtWidgets
    from ctgedit.ui.dialog.dialogs import GridSetupDialog

    dlg = GridSetupDialog(8, True, True, win)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    box = dlg.findChild(QtWidgets.QDialogButtonBox)
    assert box is not None and box.button(QtWidgets.QDialogButtonBox.Help) is not None
    dlg.close()


def test_help_action_links_run_commands(win):
    ran = []
    win.act_help.triggered.connect(lambda *_: ran.append(1))
    win.run_help_action("help")
    assert ran
    # the action is found by its object name when the link is clicked
    assert win.help_action("page_props") is win.act_page_props
    assert win.findChild(type(win.act_help), "act_update_pages") is win.act_update_pages
    assert win.help_action("no_such_command") is None


def test_help_actions_run_in_a_window_that_is_still_open(win):
    """The manual outlives the window it was opened from: an action: link
    clicked after that window closed runs in an open window, never on the
    closed window's deleted QActions ('Internal C++ object already
    deleted')."""
    import shiboken6
    from PySide6 import QtWidgets
    from ctgedit.ui import help as UH
    from ctgedit.ui.dialog.help_window import HelpWindow
    from ctgedit.ui.main_window import MainWindow

    other = MainWindow()
    UH.show_help("index", main=other)
    assert UH.action_target() is other
    other.close()
    if other in MainWindow._instances:
        MainWindow._instances.remove(other)
    shiboken6.delete(other)
    QtWidgets.QApplication.processEvents()
    ran = []
    win.act_help.triggered.connect(lambda *_: ran.append(1))
    HelpWindow.instance().actionRequested.emit("help")  # a link clicked in the manual
    assert UH.action_target() is win
    assert ran
    HelpWindow.instance().close()


def test_help_actions_never_run_in_a_closed_window(win):
    """A closed editor window leaves MainWindow._instances but stays alive
    (hidden) while something holds it: an action: link never runs there --
    not even when no other editor window is open, when the manual says so
    in its status bar instead."""
    from PySide6 import QtWidgets
    from ctgedit.ui import help as UH
    from ctgedit.ui.dialog.help_window import HelpWindow
    from ctgedit.ui.main_window import MainWindow

    other = MainWindow()
    UH.show_help("index", main=other)
    other.close()
    if other in MainWindow._instances:
        MainWindow._instances.remove(other)
    ran = []
    other.act_help.triggered.connect(lambda *_: ran.append("closed"))
    assert UH.action_target() is win  # the open window, not the closed one
    saved = list(MainWindow._instances)
    MainWindow._instances[:] = []  # no editor window open at all
    try:
        assert UH.action_target() is None
        hw = HelpWindow.instance()
        hw.statusBar().clearMessage()
        hw.actionRequested.emit("help")
        assert not ran
        assert "no editor window is open" in hw.statusBar().currentMessage()
    finally:
        MainWindow._instances[:] = saved
    HelpWindow.instance().close()
    other.deleteLater()
    QtWidgets.QApplication.processEvents()


def test_unavailable_action_link_says_so_in_the_manual(win):
    """A link to a command that is disabled right now (no page open, nothing
    selected) says so where the reader is looking -- the manual's status bar
    -- and does not run the command."""
    from ctgedit.ui import help as UH
    from ctgedit.ui.dialog.help_window import HelpWindow

    UH.show_help("page-properties", main=win)
    hw = HelpWindow.instance()
    ran = []
    win.act_page_props.triggered.connect(lambda *_: ran.append(1))
    was = win.act_page_props.isEnabled()
    win.act_page_props.setEnabled(False)
    try:
        hw.statusBar().clearMessage()
        hw.actionRequested.emit("page_props")
        assert not ran
        assert "not available now" in hw.statusBar().currentMessage()
        assert "Page Properties" in hw.statusBar().currentMessage()
    finally:
        win.act_page_props.setEnabled(was)
    hw.close()


def test_help_button_records_the_dialogs_window(win):
    """The Help button of a dialog opens the manual FROM the dialog's editor
    window (its action: links run there), like F1."""
    from PySide6 import QtWidgets
    from ctgedit.ui import help as UH
    from ctgedit.ui.dialog.dialogs import GridSetupDialog
    from ctgedit.ui.dialog.help_window import HelpWindow

    UH._last_main[:] = []
    dlg = GridSetupDialog(8, True, True, win)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    box = dlg.findChild(QtWidgets.QDialogButtonBox)
    box.button(QtWidgets.QDialogButtonBox.Help).click()
    assert HelpWindow.instance().current_topic() == "drawing"
    assert UH._last_main and UH._last_main[0]() is win
    dlg.close()
    HelpWindow.instance().close()


def test_f1_with_a_drawing_tool_opens_that_tools_section(win, monkeypatch):
    """F1 over the canvas with a tool active: the section about that tool --
    for every tool of Tools > Draw and every special-object tool."""
    from ctgedit.ui import main_window as MW

    monkeypatch.setattr(win, "doc", object())
    tools = [t for t, _l, _i, _k in MW._TOOLS if t != "select"] + list(MW._SPECIAL_TOOLS)
    seen = {}
    for tool in tools:
        monkeypatch.setattr(win.view, "tool", lambda t=tool: t)
        topic = win.context_help_topic()
        page, _, anchor = topic.partition("#")
        assert H.exists(page), (tool, topic)
        assert anchor and anchor in H.anchors(page), (tool, topic)
        seen[tool] = topic
    assert seen["rectangle"] == "drawing#rectangles"
    assert seen["trend"] == "special-objects#trends"
    assert seen["scheduler"] == "special-objects#activex-controls"


def test_shortcut_reference_is_built_from_the_live_menus(win):
    """The generated page lists the menu keys (from the window's menus, as
    they are now) and the canvas keys -- and reading it leaves the dynamic
    menus working (no QAction.menu() re-wrap)."""
    from ctgedit.ui.dialog.help_window import shortcut_reference_html

    text = shortcut_reference_html(win.menuBar())
    assert "Ctrl+S" in text and "Alt+F10" in text and "F8" in text
    assert "On the canvas" in text and "Space" in text
    assert "Citect thumbnail backdrop" not in text  # hidden actions are left out
    win.act_save.setShortcut("Ctrl+Alt+Shift+9")
    try:
        assert "Ctrl+Alt+Shift+9" in shortcut_reference_html(win.menuBar())
    finally:
        win.act_save.setShortcut("Ctrl+S")
    win._mru_menu.aboutToShow.emit()  # still a live wrapper


def _menu_paths(win) -> set:
    """Every menu path of the window, 'Edit > Library > Cut Link' (submenus
    too, hidden entries too), normalised like _normal_path. Walks the menus
    without QAction.menu() (it would invalidate the dynamic menus' wrappers,
    see ui/dialog/palette.harvest_actions)."""
    import shiboken6
    from PySide6 import QtWidgets

    menus = {}
    for m in win.menuBar().findChildren(QtWidgets.QMenu):
        menus[shiboken6.getCppPointer(m.menuAction())[0]] = m

    def label(a):
        return a.text().replace("&", "").split("\t")[0].split("  ")[0]

    out = set()

    def walk(menu, crumb):
        for a in menu.actions():
            if a.isSeparator():
                continue
            path = crumb + [label(a)]
            out.add(_normal_path(" > ".join(path)))
            sub = menus.get(shiboken6.getCppPointer(a)[0])
            if sub is not None:
                walk(sub, path)

    for top in win.menuBar().actions():
        sub = menus.get(shiboken6.getCppPointer(top)[0])
        if sub is not None:
            walk(sub, [label(top)])
    return out


#: menu words a change in progress renames in the manual before the menus
#: (keep empty: the manual names the menus as they are)
PENDING_MENU_SPELLING: dict = {}


def _normal_path(text: str) -> str:
    s = re.sub(r"\s+", " ", text.replace("…", "").replace("...", "")).strip().lower()
    for old, new in PENDING_MENU_SPELLING.items():
        s = s.replace(old, new)
    return s


_TOP_MENUS = ("File", "Edit", "View", "Text", "Arrange", "Tools", "Window", "Help")


def test_every_menu_path_the_manual_names_exists(win):
    """Every menu path the manual names exists."""
    known = _menu_paths(win)
    rx = re.compile(r"\b(%s) > " % "|".join(_TOP_MENUS))
    bad = []
    for t in H.topics():
        md = H.strip_comments(H.page_markdown(t))
        texts = []
        # blocks = paragraphs / lists / call-outs (a path may wrap a line);
        # a table row stands alone
        for block in re.split(r"\n\s*\n", md):
            lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
            if not lines:
                continue
            if lines[0].startswith("|"):
                for row in lines:
                    if t == "differences":  # ctgedit's column only
                        row = "|".join(row.strip("|").split("|")[1:])
                    texts.append(row)
                continue
            if lines[0].startswith("> **Coming from the Graphics Builder:**"):
                continue
            texts.append(" ".join(ln.lstrip("> ") if ln.startswith(">") else ln for ln in lines))
        for s in texts:
            plain = re.sub(r"\*\*|\[\[|\]\]|`", "", s)
            plain = H._LINK.sub(lambda m: m.group(2), plain)
            for m in rx.finditer(plain):
                rest = _normal_path(plain[m.start() :])
                ok = any(
                    rest.startswith(p) and (len(rest) == len(p) or not rest[len(p)].isalnum()) for p in known
                )
                if not ok:
                    bad.append("%s: %s" % (t, plain[m.start() : m.start() + 60]))
    assert not bad, "menu paths that are not in the menus:\n" + "\n".join(bad)


def test_every_setting_the_manual_names_exists(win):
    """'Settings > Editing > "Confirm before deleting objects"': the tab is a
    tab of the Settings dialog and the quoted label starts one of its
    options / fields."""
    from PySide6 import QtWidgets
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win)
    labels = []
    for k in range(dlg._tabs.count()):
        page = dlg._tabs.widget(k)
        labels += [w.text() for w in page.findChildren(QtWidgets.QCheckBox)]
        labels += [w.text() for w in page.findChildren(QtWidgets.QLabel)]
    dlg.close()
    labels = [re.sub(r"\s+", " ", t.replace("&", "")).strip().lower() for t in labels]
    tabs = "|".join(re.escape(x) for x in SETTINGS_TABS)
    rx = re.compile(r'Settings > (%s)(?: > "([^"]+)")?' % tabs)
    other = re.compile(r"Settings > (?!%s)(\S+ ?\S*)" % tabs)
    bad = []
    for t in H.topics():
        text = re.sub(r"\s+", " ", H.strip_comments(H.page_markdown(t)).replace("\n>", " "))
        bad += ["%s: no Settings tab %r" % (t, m.group(1)) for m in other.finditer(text)]
        for m in rx.finditer(text):
            tab, label = m.group(1), m.group(2)
            if label and not any(x.startswith(label.lower()) for x in labels):
                bad.append("%s: no option %r on %s" % (t, label, tab))
    assert not bad, "\n".join(bad)


def test_markdown_comments_and_graphics_builder_boxes():
    out = H.markdown_to_html(
        "# T\n\nText <!-- limit:WP99 --> here.\n\n"
        "> **Coming from the Graphics Builder:** one\n>\n> two <!-- limit:WP98 -->\n"
    )
    assert "limit" not in out and "WP99" not in out
    assert 'class="callout gb"' in out
    assert "<b>Coming from the Graphics Builder:</b> one<br><br>two" in out


#: work packages that have LIFTED a limit the manual documented: a package
#: that removes a documented limit adds its id here, and this test then
#: fails until every page that says so (its <!-- limit:WPxx --> markers) is
#: updated
LIFTED: dict = {}


def test_limit_markers_are_kept_in_step():
    """Every known-limit statement carries <!-- limit:WPxx --> (the package
    whose area it is); no marker names a package that lifted its limit, and
    the Coming from the Graphics Builder page lists every limit the other
    pages mention."""
    marks = {t: H.limit_markers(t) for t in H.topics()}
    every = {m for ms in marks.values() for m in ms}
    assert every, "the manual documents no limit at all?"
    bad = sorted(m for m in every if not re.fullmatch(r"WP[0-3]\d", m))
    assert not bad, "limit markers must name a work package WP00..WP39: %s" % bad
    lifted = sorted((t, m) for t, ms in marks.items() for m in ms if m in LIFTED)
    assert not lifted, "pages still document limits their package lifted: %s" % lifted
    missing = sorted(every - set(marks.get("differences", [])))
    assert not missing, (
        "limits documented elsewhere but not listed on the Coming from "
        "the Graphics Builder page (differences.md): %s" % missing
    )


def _settle(cond, ms=2000):
    """Process events until ``cond()`` holds (at most ``ms``): window activation and focus
    arrive as queued window-system events, and F1 resolves through both."""
    from PySide6 import QtCore, QtWidgets

    timer = QtCore.QElapsedTimer()
    timer.start()
    while not cond() and timer.elapsed() < ms:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)
    return cond()


def _press_f1(widget):
    from PySide6 import QtCore, QtGui, QtWidgets

    widget.setFocus()
    QtWidgets.QApplication.processEvents()
    # the focus moves only once the widget's window is active: the manual opened by the
    # previous F1 was active, and F1 resolves through QApplication.focusWidget()
    _settle(lambda: QtWidgets.QApplication.focusWidget() == widget)
    ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_F1, QtCore.Qt.NoModifier)
    QtWidgets.QApplication.sendEvent(widget, ev)


def _opened(dlg, focus=None):
    """F1 in ``dlg`` with the keyboard focus on the first focusable field
    inside ``focus`` (default: the dialog): the (topic, anchor) the manual
    opened on."""
    from PySide6 import QtCore, QtWidgets
    from ctgedit.ui.dialog.help_window import HelpWindow

    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    QtWidgets.QApplication.processEvents()
    _settle(lambda: QtWidgets.QApplication.activeWindow() == dlg)
    within = focus if focus is not None else dlg
    target = next(
        (
            w
            for w in within.findChildren(QtWidgets.QWidget)
            if w.focusPolicy() != QtCore.Qt.NoFocus
            and w.isVisible()
            and w.isEnabled()
            and not isinstance(w, QtWidgets.QTabBar)
        ),
        within,
    )
    _press_f1(target)
    hw = HelpWindow.instance()
    return hw.current_topic(), hw.current_anchor()


def _check_anchor(topic, anchor):
    assert H.exists(topic), topic
    assert not anchor or anchor in H.anchors(topic), "%s#%s" % (topic, anchor)


def test_f1_in_each_dialog_opens_its_anchor(win):
    from types import SimpleNamespace
    from ctgedit.ui.dialog import dialogs as D
    from ctgedit.ui.dialog.arrange_dialogs import AdjustPositionDialog, AlignDialog
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.dialog.help_window import HelpWindow
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    style = SimpleNamespace(
        font="Arial", font_size=10, bold=False, italic=False, underline=False, strikeout=False, justify=None
    )
    cases = [
        (D.GridSetupDialog(8, True, True, win), ("drawing", "grid-and-snap")),
        (D.GuidelinesDialog([100], [50], True, True, win), ("drawing", "guidelines")),
        (
            PageBrowserDialog(PageBrowserDialog.NEW, win),
            ("opening-and-saving", "create-a-page-or-library-element"),
        ),
        (D.FontDialog(style, win), ("text-and-numbers", "fonts")),
        (AlignDialog(win, units=3), ("selecting-and-arranging", "align-objects")),
        (AdjustPositionDialog((10, 10, 50, 40), win), ("selecting-and-arranging", "exact-position-and-size")),
        (
            D.PasteSpecialDialog(["AVEVA Plant SCADA Graphics Object(s)"], win),
            ("selecting-and-arranging", "paste-special"),
        ),
    ]
    for dlg, want in cases:
        got = _opened(dlg)
        dlg.close()
        assert got == want, (type(dlg).__name__, got)
        _check_anchor(*got)
    # a tabbed dialog opens the heading of its CURRENT tab
    sd = SettingsDialog(win)
    for k in range(sd._tabs.count()):
        sd._tabs.setCurrentIndex(k)
        got = _opened(sd, focus=sd._tabs.currentWidget())
        assert got == SETTINGS_PAGE_TOPICS.get(SETTINGS_TABS[k], ("settings", H.slug(SETTINGS_TABS[k]))), got
        _check_anchor(*got)
    sd.close()
    HelpWindow.instance().close()


def test_f1_in_every_listed_dialog_opens_an_existing_heading(win):
    """Every DIALOG_TOPICS entry names a dialog class of the code, and F1 in
    a dialog of that class (the routing goes by class name: ui/help.
    dialog_topic) opens a page and heading that exist -- the dialogs the
    test above does not build with real arguments included."""
    from PySide6 import QtWidgets
    from ctgedit.ui.help import DIALOG_TOPICS
    from ctgedit.ui.dialog.help_window import HelpWindow

    classes = set()
    for root, _d, files in os.walk(os.path.join(REPO, "ctgedit")):
        for f in files:
            if f.endswith(".py"):
                src = open(os.path.join(root, f), encoding="utf-8").read()
                classes.update(re.findall(r"(?m)^class (\w+)\(", src))
    stale = sorted(set(DIALOG_TOPICS) - classes)
    assert not stale, "DIALOG_TOPICS names classes that do not exist: %s" % stale
    from ctgedit.ui.help import dialog_topic

    for name in DIALOG_TOPICS:
        dlg = type(name, (QtWidgets.QDialog,), {})(win)
        QtWidgets.QLineEdit(dlg)
        want = dialog_topic(dlg)
        got = _opened(dlg)
        dlg.close()
        dlg.deleteLater()
        page, _, anchor = want.partition("#")
        assert got == (page, anchor), (name, want, got)
        _check_anchor(*got)
    HelpWindow.instance().close()
    QtWidgets.QApplication.processEvents()


def test_f1_in_the_object_sheet_opens_the_tab_or_page(win, fx_page):
    """The object sheet: F1 on a page with a topic of its own (Fill > Color)
    opens that heading, on any other page the heading of its outer tab. The object is
    FxShapes' FxRect (the fake install's FxMain): a plain top-level rectangle with its own
    style block, not a template record."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops import ObjectPropertiesDialog
    from ctgedit.ui.dialog.help_window import HelpWindow

    doc = EditableDocument.open(fx_page("FxShapes"))
    from ctgedit.model.document import ObjectKind

    [index] = doc.find_objects_by_name("FxRect")
    o = doc.page.objects[index]
    assert o.kind is ObjectKind.RECTANGLE and o.parent is None
    assert not o.raw.get("_from_template")
    dlg = ObjectPropertiesDialog(doc, index)
    seen = set()
    for k in range(dlg._tabs.count()):
        dlg._tabs.setCurrentIndex(k)
        page = dlg._tabs.currentWidget()
        got = _opened(dlg, focus=page)
        _check_anchor(*got)
        assert got[0] == "properties"
        seen.add(got[1])
    assert "appearance" in seen and "movement" in seen and "access" in seen
    assert "fill-colour" in seen  # Fill > Color names its own topic
    dlg.close()
    HelpWindow.instance().close()


def test_page_properties_help_button_opens_the_current_tab(win, fx_page, fx_project):
    """The page is FxMain's FxAnim of the fake install (its copy): it has a pages.DBF
    row."""
    from PySide6 import QtWidgets
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog
    from ctgedit.ui.dialog.help_window import HelpWindow

    doc = EditableDocument.open(fx_page("FxAnim"))
    dlg = PagePropertiesDialog(doc, Project(fx_project("FxMain")))
    dlg.show()
    QtWidgets.QApplication.processEvents()
    box = dlg.findChild(QtWidgets.QDialogButtonBox)
    helpb = box.button(QtWidgets.QDialogButtonBox.Help)
    assert helpb is not None and helpb.isEnabled()
    assert len([b for b in box.buttons() if box.buttonRole(b) == QtWidgets.QDialogButtonBox.HelpRole]) == 1
    hw = HelpWindow.instance()
    for k, tab in enumerate(PAGE_PROPERTY_TABS):
        dlg._tabs.setCurrentIndex(k)
        helpb.click()
        assert (hw.current_topic(), hw.current_anchor()) == ("page-properties", H.slug(tab))
        _check_anchor(hw.current_topic(), hw.current_anchor())
        assert _opened(dlg, focus=dlg._tabs.currentWidget()) == ("page-properties", H.slug(tab))
    dlg.close()
    hw.close()
