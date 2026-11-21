"""Every MainWindow command says when it's enabled."""

import inspect
import re

from PySide6 import QtWidgets


def test_every_action_has_an_enable_rule():
    from ctgedit.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    w = MainWindow()
    try:
        acts = {a for a in vars(w) if a.startswith("act_")}
        table = {n for names in MainWindow.ENABLE_WHEN.values() for n in names}
        custom, always = set(MainWindow.CUSTOM_ENABLE), set(MainWindow.ALWAYS_ENABLED)
        assert not (table & custom) and not (table & always) and not (custom & always)
        missing = sorted(acts - table - custom - always)
        assert not missing, "say when these are enabled (ui/window/view.py): %s" % missing
        stale = sorted((table | custom | always) - acts)
        assert not stale, "no such actions any more: %s" % stale
        src = inspect.getsource(MainWindow._update_actions)
        own = set(re.findall(r"self\.(act_[a-z0-9_]+)\.setEnabled", src))
        assert custom <= own | {"act_font_dialog"}, sorted(custom - own)
        assert not (own & table), sorted(own & table)
    finally:
        w.close()
        w.deleteLater()


def test_the_table_greys_commands_without_a_page():
    from ctgedit.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    w = MainWindow()
    try:
        w._update_actions()
        for rule, names in MainWindow.ENABLE_WHEN.items():
            for n in names:
                assert not getattr(w, n).isEnabled(), (rule, n)
        assert w.act_new.isEnabled() and w.act_open.isEnabled()
        # (Update Pages picks any root project of the install: nothing needs to be open)
        assert w.act_update_pages.isEnabled()
    finally:
        w.close()
        w.deleteLater()


def test_without_a_page_the_drawing_tools_and_page_commands_look_unavailable():
    """No page open (the start page shows): every drawing tool but Select, Paste Linked,
    Break Lock Mode and Refresh are unavailable, in the menu and on the ribbon; Compile and
    Run too while no recent page names a project."""
    from ctgedit.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    w = MainWindow()
    try:
        w._update_actions()
        assert w.doc is None
        for name, a in w.tool_actions.items():
            assert a.isEnabled() == (name == "select"), name
        for n in (
            "act_paste_linked",
            "act_break_lock",
            "act_refresh",
            "act_compile",
            "act_run",
            "act_simulate",
        ):
            assert not getattr(w, n).isEnabled(), n
        for b in w.findChildren(QtWidgets.QToolButton):
            a = b.defaultAction()
            if a is not None and a in w.tool_actions.values() and a is not w.tool_actions.get("select"):
                assert not b.isEnabled(), a.text()
        assert w.act_new.isEnabled() and w.act_open.isEnabled() and w.act_messages.isEnabled()
    finally:
        w.close()
        w.deleteLater()


def test_no_window_key_belongs_to_two_commands():
    """Every key the editor window answers has one command (a list's own Copy must not take
    Ctrl+C from the page)."""
    from PySide6 import QtCore, QtGui
    from ctgedit.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    w = MainWindow()
    try:
        owners = {}
        for a in w.actions():
            assert a.shortcutContext() not in (
                QtCore.Qt.WidgetShortcut,
                QtCore.Qt.WidgetWithChildrenShortcut,
            ), "%r (a widget's own key) is attached to the window" % a.text()
            for k in a.shortcuts():
                owners.setdefault(k.toString(), set()).add(a)
        both = {k: sorted(x.text() for x in v) for k, v in owners.items() if len(v) > 1}
        assert not both, both
        copy_key = QtGui.QKeySequence(QtGui.QKeySequence.Copy).toString()
        assert owners[copy_key] == {w.act_copy}
    finally:
        w.close()
        w.deleteLater()


def test_ctrl_c_on_the_page_copies_the_selection(fx_install):
    """Ctrl+C on the canvas copies the selected root of a page (FxMain's FxShapes in the
    fake install: its rectangle FxRect is a root not from the template)."""
    from PySide6 import QtGui, QtTest
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.main_window_base import CLIPBOARD_MIME

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    page = fx_install.page("FxShapes")
    w = MainWindow()
    try:
        w.show()
        w.open_document(page)
        i = w.doc.find_objects_by_name("FxRect")[0]
        o = w.doc.page.objects[i]
        assert o.parent is None and not o.raw.get("_from_template")
        w._set_selection([i])
        w.activateWindow()
        QtTest.QTest.qWaitForWindowActive(w, 2000)
        w.view.setFocus()
        assert w.act_copy.isEnabled()
        QtWidgets.QApplication.clipboard().clear()
        QtTest.QTest.keySequence(w.view, QtGui.QKeySequence(QtGui.QKeySequence.Copy))
        mime = QtWidgets.QApplication.clipboard().mimeData()
        assert mime is not None and mime.hasFormat(CLIPBOARD_MIME)
    finally:
        w.close()
        w.deleteLater()


def test_run_says_what_it_does_where_it_is():
    """With a page Run saves, compiles and starts Plant SCADA; with a library element it only
    opens Runtime Manager: its label and tip say which."""
    from ctgedit.ui.main_window import MainWindow

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = MainWindow()
    try:
        w._update_actions()
        assert w.act_run.text() == "Run in Plant SCADA"
        assert "compile the project" in w.act_run.toolTip()
        w._label_run(True)  # (what _update_actions does with a library element shown)
        assert w.act_run.text() == "Open Runtime Manager"
        assert "Runtime Manager" in w.act_run.toolTip()
        assert "not started" in w.act_simulate.toolTip()
    finally:
        w.close()
        w.deleteLater()
