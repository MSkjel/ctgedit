"""The Help menu."""

from __future__ import annotations

from ..main_window_base import (
    ElementDocument,
    QtWidgets,
    SPECIAL_TOOLS,
    app_filter,
)

from ..main_window_base import editor_windows


from ..main_window_base import (
    QtCore,
    QtGui,
)


class HelpCommands:
    """The Help menu and the built-in manual."""

    # --------------------------------------------------------------- help
    def _install_help(self) -> None:
        """F1 context help for the whole application (once), the generated
        shortcut reference, and the help topic of every panel."""
        from .. import help as _help
        from ... import help as H

        app = QtWidgets.QApplication.instance()
        if app is not None and not hasattr(app, "_ctg_f1"):

            def fallback():
                win = next(
                    (w for w in editor_windows() if w.isActiveWindow()),
                    editor_windows()[0] if editor_windows() else None,
                )
                return win.context_help_topic() if win is not None else "index"

            _help.install(app, fallback, main=self, dispatcher=app_filter())
        from ..dialog.help_window import shortcut_reference_html

        # the generated page is built from the menus of the window the manual
        # was opened from, looked up when it is shown (a closed window's menu
        # bar is gone)
        def shortcuts() -> str:
            win = _help.action_target()
            if win is None:
                return (
                    "<h1>Keyboard shortcuts</h1><p>Open an editor window to see its keyboard shortcuts.</p>"
                )
            return shortcut_reference_html(win.menuBar())

        H.register_generated("keyboard-shortcuts", "Keyboard shortcuts", shortcuts)
        # (action: links find their command by its object name, the command id that
        # keymap.assign_command_ids gave it in _build_actions)
        for w, topic in (
            (self.dock_objects, "objects-panel"),
            (self.dock_library, "library-panel"),
            (self.dock_props, "properties"),
            (self.dock_tags, "tags-panel"),
            (self.doc_tabs, "the-window#document-tabs"),
            (self.dock_sa_checks, "situational-awareness#workspace-checks"),
            (self.act_check_workspace, "situational-awareness#workspace-checks"),
            (self.act_check_display, "troubleshooting#check-the-page-display"),
            (self.act_simulate, "simulate"),
            (self.act_preview_states, "troubleshooting#preview-states"),
            (self.dock_preview_states, "troubleshooting#preview-states"),
            (self.act_click_map, "troubleshooting#click-map"),
            (self.act_pane_labels, "situational-awareness#pane-labels"),
        ):
            _help.set_help(w, topic)

    def show_help(self, topic: str = "") -> None:
        """Open the ctgedit manual on ``topic`` (default: what F1 would
        pick for the current context)."""
        from .. import help as _help

        _help.show_help(topic or self.context_help_topic(), main=self)

    def help_for_key(self) -> None:
        """F1: help for the command button under the mouse, else the focused widget."""
        from .. import help as _help
        from ..style.tips import pointer_topic

        _help.show_help(pointer_topic() or self.context_help_topic(), main=self)

    def context_help_topic(self) -> str:
        """F1 over the canvas / with nothing more specific focused: the
        active drawing tool, else what is selected, else the editor."""
        if self.doc is None:
            return "getting-started"
        if self.view.text_editing():
            return "text-and-numbers#editing-text-in-place"
        tool = self.view.tool() if hasattr(self.view, "tool") else "select"
        special = {
            "trend": "special-objects#trends",
            "cicode_object": "special-objects#cicode-objects",
            "web_content": "special-objects#web-content",
            "zoom": "the-window#zoom-and-pan",
            "pan": "the-window#zoom-and-pan",
        }
        if tool in special:
            return special[tool]
        if tool in SPECIAL_TOOLS:  # the ActiveX-family tools
            return "special-objects#activex-controls"
        if tool and tool != "select":
            return "drawing#%s" % {
                "rect": "rectangles",
                "rectangle": "rectangles",
                "ellipse": "ellipses",
                "polygon": "polygons",
                "polyline": "polygons",
                "freehand": "free-hand-lines",
                "line": "lines",
                "pipe": "pipes",
                "text": "text",
                "number": "numbers",
                "button": "buttons",
                "symbol": "symbols-and-genies",
                "symbol_set": "symbol-sets",
            }.get(tool, "drawing-tools")
        if isinstance(self.doc, ElementDocument):
            return "library-elements"
        if self._sel:
            try:
                o = self.doc.page.objects[self._sel[0]]
                kind = o.kind.name.lower()
            except IndexError, AttributeError:
                kind = ""
            if self.doc.is_symbol_set(self._sel[0]):
                return "symbol-sets"
            if kind in ("genie", "symbol_set") or getattr(o, "is_genie", False):
                return "genies"
            if kind in ("text", "number"):
                return "text-and-numbers"
            return "properties"
        return "the-window"

    def help_action(self, name: str):
        """The QAction an ``action:<name>`` link names, looked up NOW by its
        object name (``act_<name>``, set by _install_help) -- never a QAction
        kept from earlier, which a rebuilt menu or a closed window may have
        deleted. None when there is no live action of that name."""
        import shiboken6

        key = "act_" + name
        act = self.findChild(QtGui.QAction, key)
        if act is None or not shiboken6.isValid(act):
            act = getattr(self, key, None)
        if not isinstance(act, QtGui.QAction) or not shiboken6.isValid(act):
            return None
        return act

    def run_help_action(self, name: str) -> None:
        """An ``action:<name>`` link in the manual: trigger ``act_<name>``
        (looked up when the link is clicked), else the public method
        ``name``."""
        act = self.help_action(name)
        if act is not None:
            try:
                if act.isEnabled():
                    self.raise_()
                    self.activateWindow()
                    act.trigger()
                else:
                    self.statusBar().showMessage(
                        "%s is not available right now. Open a page first, or select "
                        "the objects it works on." % act.text().replace("&", "").split("\t")[0],
                        6000,
                    )
            except RuntimeError:  # deleted while the link was followed
                self.statusBar().showMessage("That command is not available in this window.", 6000)
            return
        fn = getattr(self, name, None)
        if callable(fn) and not name.startswith("_"):
            fn()

    def _open_cicode_reference(self) -> None:
        from ... import help as H

        # the Cicode language is AVEVA's: their reference, installed locally
        # when present (topic 'Cicode Reference'), else the web help
        target = (
            H.aveva_target("1060490")
            if H.aveva_available()
            else "https://gcsresource.aveva.com/PlantSCADA/WebHelp/Content/CicodeReferenceIntroduction.htm"
        )
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(target))
