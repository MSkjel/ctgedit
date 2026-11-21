"""Windows, help, recent pages, view options and preferences."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # (names only the annotations use)
    from ..main_window import MainWindow

from ..main_window_base import (  # noqa: F401
    default_open_dir,
    ElementDocument,
    PageScene,
    QtCore,
    QtGui,
    QtWidgets,
    TITLE_APP,
    app_settings,
    contextlib,
    doc_key,
    option,
    os,
    paste_linked,
)
from ..main_window_base import (  # noqa: F401
    _LABEL_MODE_NAMES,
    _PendingPage,
    _SPECIAL_TOOLS,
    _app_filter,
)


class WindowShell:
    """MainWindow's windows, help, recent pages, view options, labels, the context menu and
    preferences."""

    # ----------------------------------------------------------- windows
    def _new_window(self) -> "MainWindow":
        win = _mw().MainWindow()
        win.show()
        return win

    @staticmethod
    def _open_windows() -> list["MainWindow"]:
        return [w for w in _mw().MainWindow._instances if w.isVisible()]

    def _screen_area(self) -> QtCore.QRect:
        """The work area of the screen this window is on (not always the
        primary one on a multi-monitor desk)."""
        scr = self.screen() or QtGui.QGuiApplication.primaryScreen()
        return scr.availableGeometry()

    @staticmethod
    def _place_frame(win, rect: QtCore.QRect) -> None:
        """Put ``win``'s whole frame (title bar and borders included) on
        ``rect``. The window is moved onto that screen first, so on mixed-DPI
        desks its frame is measured at the target screen's scale."""
        win.move(rect.topLeft())
        fg, g = win.frameGeometry(), win.geometry()
        win.setGeometry(
            rect.adjusted(
                g.left() - fg.left(), g.top() - fg.top(), g.right() - fg.right(), g.bottom() - fg.bottom()
            )
        )

    def _cascade_windows(self) -> None:
        """Window > Cascade: every editor window, stepped down-right, each
        frame kept inside the work area of this window's screen."""
        area = self._screen_area()
        x, y = area.x() + 40, area.y() + 40
        for win in self._open_windows():
            win.showNormal()
            if x + 200 > area.right() or y + 150 > area.bottom():
                x, y = area.x() + 40, area.y() + 40  # start the steps again
            fg = win.frameGeometry()
            self._place_frame(
                win,
                QtCore.QRect(
                    x, y, min(fg.width(), area.right() + 1 - x), min(fg.height(), area.bottom() + 1 - y)
                ),
            )
            win.raise_()
            x += 32
            y += 32

    def _tile_windows(self, horizontal: bool) -> None:
        """Window > Tile Horizontally (full-width strips, top to bottom) /
        Tile Vertically (full-height strips, left to right), frames inside
        the work area of this window's screen."""
        wins = self._open_windows()
        if not wins:
            return
        area = self._screen_area()
        n = len(wins)
        for k, win in enumerate(wins):
            win.showNormal()
            if horizontal:  # full-width strips, top to bottom
                hh = area.height() // n
                rect = QtCore.QRect(area.x(), area.y() + k * hh, area.width(), hh)
            else:  # full-height strips, left to right
                ww = area.width() // n
                rect = QtCore.QRect(area.x() + k * ww, area.y(), ww, area.height())
            self._place_frame(win, rect)

    def _fill_window_list(self) -> None:
        """Window menu: a 'Pages' section with this window's tabs (the name, a dot when
        unsaved, the project), then a 'Windows' section only when more than one editor
        window is open."""
        for a in self._window_list_actions:
            self._window_menu.removeAction(a)
        self._window_list_actions = []
        if self._docs:
            self._window_list_actions.append(self._window_menu.addSection("Pages"))
        # this window's open pages (tabs)
        for k, e in enumerate(self._docs):
            d = e["doc"]
            a = self._window_menu.addAction(
                self._tab_title(d) + ("  •" if d.dirty else "") + "\t" + self._doc_project(d),
                lambda _=False, i=k: self.show_tab(i),
            )
            a.setCheckable(True)
            a.setChecked(d is self.doc)
            a.setStatusTip(os.path.abspath(d.path))
            self._window_list_actions.append(a)
        wins = self._open_windows()
        if len(wins) > 1:
            self._window_list_actions.append(self._window_menu.addSection("Windows"))
            for k, win in enumerate(wins, start=1):
                title = win.windowTitle()
                if title.endswith(" - " + TITLE_APP):
                    title = title[: -len(" - " + TITLE_APP)]
                a = self._window_menu.addAction(
                    "&%d %s" % (k, title), lambda _=False, w=win: (w.raise_(), w.activateWindow())
                )
                a.setCheckable(True)
                a.setChecked(win is self)
                a.setStatusTip("Bring this editor window to the front.")
                self._window_list_actions.append(a)

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
                    (w for w in _mw().MainWindow._instances if w.isActiveWindow()),
                    _mw().MainWindow._instances[0] if _mw().MainWindow._instances else None,
                )
                return win.context_help_topic() if win is not None else "index"

            _help.install(app, fallback, main=self, dispatcher=_app_filter())
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
        if tool in _SPECIAL_TOOLS:  # the ActiveX-family tools
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

    # ------------------------------------------------------------- recent
    def _fill_mru(self) -> None:
        self._fill_mru_into(self._mru_menu)

    #: the pages File > Recent Pages keeps
    MRU_MAX = 16

    @staticmethod
    def recent_pages() -> list:
        """The recent pages that still exist (.ctg files only), newest first."""
        st = app_settings()
        out = []
        for p in st.value("mru", [], type=list):
            p = str(p)
            if p.lower().endswith(".ctg") and os.path.isfile(p):
                out.append(p)
        return out[: _mw().MainWindow.MRU_MAX]

    def _fill_mru_into(self, menu) -> None:
        menu.clear()
        for k, path in enumerate(self.recent_pages()):
            proj = os.path.basename(os.path.dirname(path))
            a = menu.addAction(
                "&%d  %s  (%s)" % (k + 1, os.path.splitext(os.path.basename(path))[0], proj),
                lambda _=False, p=path: self.open_page(p),
            )
            a.setToolTip(path)
            a.setStatusTip("Open %s." % path)
        if menu.isEmpty():
            a = menu.addAction("No recent pages")
            a.setStatusTip("The pages opened last are listed here.")
            a.setEnabled(False)

    def _push_mru(self, path: str) -> None:
        st = app_settings()
        mru = [str(p) for p in st.value("mru", [], type=list)]
        path = os.path.abspath(path)
        mru = [path] + [p for p in mru if p.lower() != path.lower()]
        st.setValue("mru", mru[: self.MRU_MAX])

    # ------------------------------------------------------------- view options
    def _set_tool(self, tool: str) -> None:
        # (another tool: the underlay's Move with the Mouse ends where it is; also when the
        # tool is already the canvas's, as after a Z tap: the key armed the zoom box first)
        ended = getattr(self, "_underlay_tool_changed", None)
        if ended is not None:
            ended(tool)
        self.view.set_tool(tool)
        a = self.tool_actions.get(tool)
        if a is not None and not a.isChecked():
            a.setChecked(True)
        if tool != "symbol":
            if self._pending_symbol is not None or self._pending_composite is not None:
                # (Esc: the 'Click on the page to place ...' instruction goes with it)
                if self.statusBar().currentMessage().startswith("Click on the page to place"):
                    self.statusBar().clearMessage()
            self._pending_symbol = None
            self._pending_composite = None
        if tool != "activex":
            self._pending_control = None
        if tool == "text" and not self.view.hasFocus():
            # what is typed next is the new text (CtDraw's Text tool)
            self.view.setFocus(QtCore.Qt.OtherFocusReason)
        # with nothing selected, the Properties panel shows the look of the next object
        # (ui/editor/draw_styles: 'New Rectangle')
        panel = getattr(self, "props", None)
        if panel is not None and hasattr(panel, "set_tool"):
            panel.set_tool(tool)
        pair = getattr(self, "ribbon_colours", None)
        if pair is not None:
            pair.update()

    def _toggle_grid(self) -> None:
        self._show_grid = self.act_grid.isChecked()
        self._refresh()
        self._sync_grid_dialog()

    def _toggle_snap(self) -> None:
        self.view.set_snap(self.act_snap.isChecked())
        self._sync_grid_dialog()

    def _set_grid_size(self, size: int) -> None:
        """A View > Grid size preset: one pitch for both axes (the origin
        stays)."""
        self._apply_grid(size, None, *self._grid_origin)
        self._refresh()
        self._sync_grid_dialog()

    def _sync_grid_menu(self) -> None:
        """Keep the View > Grid size menu's checked preset in step with the current pitch (e.g.
        after a Grid Setup dialog change), so it never shows a stale size and never re-applies it.
        A grid with different horizontal / vertical pitches matches no preset."""
        for s, a in getattr(self, "_grid_actions", {}).items():
            a.setChecked(s == self._grid_size and self._grid_y is None)

    # ---- @() multi-language labels (see io/lang.py + io/reader label mode) ----
    def _rebuild_labels_menu(self) -> None:
        """(Re)populate View > Labels: Literal/Native + one entry per language
        in the open project's LANG.DBF."""
        m = getattr(self, "labels_menu", None)
        if m is None:
            return
        m.clear()
        self._label_group = QtGui.QActionGroup(self)
        self._label_group.setExclusive(True)

        def add(mode: str, text: str):
            a = m.addAction(text, lambda _=False, mo=mode: self._set_label_mode(mo))
            a.setCheckable(True)
            a.setChecked(mode == self._label_mode)
            self._label_group.addAction(a)
            return a

        # (status tips, which a menu shows; a tool tip it never does)
        add("literal", _LABEL_MODE_NAMES["literal"]).setStatusTip(
            "Show the @( ) labels as they are stored, with the @( ) round the native text."
        )
        add("native", _LABEL_MODE_NAMES["native"]).setStatusTip(
            "Show the text inside @( ): the native language of the labels."
        )
        langs = []
        if self.doc is not None:
            try:
                from ...io.lang import LanguageDB

                db = LanguageDB.for_page(self.doc.path)
                if db is not None:
                    langs = db.languages()
            except Exception:  # noqa: BLE001
                langs = []
        if langs:
            m.addSeparator()
            for lang in langs:
                add(lang, lang).setStatusTip(
                    "Show the @( ) labels translated to %s, from the project's language table." % lang
                )
        else:
            a = m.addAction("No languages in this project")
            a.setStatusTip(
                "A language of the project's language tables (LANG.DBF) would be "
                "listed here, to show the @( ) labels translated."
            )
            a.setEnabled(False)

    def _set_label_mode(self, mode: str) -> None:
        self._label_mode = mode
        from ...io import reader

        reader.set_label_mode(mode)
        self._settings().setValue("view/label_mode", mode)
        # re-derive every caption with the new mode (re-parses current edits)
        if self.doc is not None:
            try:
                self.doc._rerender_page()
            except Exception:  # noqa: BLE001
                pass
            self._refresh()
        self.statusBar().showMessage("Labels: %s" % _LABEL_MODE_NAMES.get(mode, mode), 3000)

    def _toggle_thumb(self) -> None:
        self._show_thumb = self.act_thumb.isChecked()
        self._refresh()

    # ----------------------------------------------------------- the colours of new objects
    @property
    def _draw_colour(self) -> int:
        """The one colour the armed tool gives a new object (draw_styles.creation_colour:
        the text colour for Text and Number, the line colour for lines and outlines, the
        fill colour for shapes), on the page shown. Read only: the colours are the ribbon
        swatches' (draw_styles.set_colour)."""
        from ..editor import draw_styles as DS

        view = getattr(self, "view", None)
        tool = "rectangle"
        try:
            tool = view.tool() if view is not None else tool
        except RuntimeError:  # swallow-ok: the canvas is going (the window closes)
            pass
        if DS.kind_of_tool(tool) is None:
            tool = "rectangle"
        c = DS.creation_colour(tool, self._colour_page())
        return DS.colours(self._colour_page())["fill"] if c is None else c

    def _create_styled(self, tool: str, rect=None, nodes=None, *, colour=None, **kw) -> int:
        """``doc.create_object`` with the look of new objects (ui/editor/draw_styles): the tool's
        colour, then its style (style_for, through doc.apply_appearance: the record of
        drawing the object and pasting that appearance), as ONE undo step. ``colour`` None
        keeps the factory colour (a button, a symbol)."""
        from ..editor import draw_styles as DS

        doc = self.doc
        style_tool = "freehand" if tool == "polyline" else tool
        kind = DS.kind_of_tool(style_tool)
        if kind is None:
            return doc.create_object(tool, rect, nodes, colour=colour, **kw)
        page = doc.page
        if colour is not None:
            colour = DS.creation_colour(style_tool, page)
        style = DS.style_for(kind, style_tool, page)
        with doc.batch("add object"):
            idx = doc.create_object(tool, rect, nodes, colour=colour, **kw)
            if style:
                doc.apply_appearance([idx], style, label="add object")
        return idx

    def _install_new_colours(self) -> None:
        """The colours of new objects follow the page shown (its colour, a tab switch, an
        undo): the swatch pair is painted again (MainWindow construction)."""
        self.props.changed.connect(self._update_colour_button)
        self.doc_tabs.currentChanged.connect(self._update_colour_button)
        for a in (self.act_undo, self.act_redo):
            a.triggered.connect(self._update_colour_button)

    def _colour_page(self):
        """The page the colours of new objects contrast with (None: no page open)."""
        doc = getattr(self, "doc", None)
        return getattr(doc, "page", None) if doc is not None else None

    def new_object_colours(self) -> dict:
        """What the ribbon's swatch pair shows: the fill, line and text colours on the page
        shown, No fill and whether they follow the page."""
        from ..editor import draw_styles as DS

        out = dict(DS.colours(self._colour_page()))
        out["no_fill"] = DS.no_fill()
        out["auto"] = DS.colour_mode() == "auto"
        return out

    def _update_colour_button(self, *_a) -> None:
        """The swatch pair and the colour commands show the colours as they are now."""
        from ..editor import draw_styles as DS

        pair = getattr(self, "ribbon_colours", None)
        if pair is not None:
            with contextlib.suppress(RuntimeError):
                pair.update()
        for name, on in (
            ("act_new_no_fill", DS.no_fill()),
            ("act_new_colours_auto", DS.colour_mode() == "auto"),
        ):
            a = getattr(self, name, None)
            if a is not None and a.isChecked() != on:
                blocked = a.blockSignals(True)
                a.setChecked(on)
                a.blockSignals(blocked)
        panel = getattr(self, "props", None)
        if (
            panel is not None
            and getattr(panel, "_tool", "select") not in ("", "select")
            and hasattr(panel, "refresh_tool_style")
        ):
            panel.refresh_tool_style()

    def _colour_popover(self, target: str = "fill") -> None:
        """The colour picker of a colour of new objects (the swatch pair, Tools > Colours >
        Colours of New Objects): ``target`` 'fill' / 'line' / 'text'."""
        from ..editor import draw_styles as DS
        from ..swatch_pair import NewColourPopover

        st = self.new_object_colours()
        pop = NewColourPopover(target, st, self, no_fill=st["no_fill"], auto=st["auto"])
        page = self._colour_page()

        def chosen(which, colour):
            DS.set_colour(which, colour, page)
            self._update_colour_button()
            self.statusBar().showMessage(
                {
                    "fill": "New shapes are filled with #%06X.",
                    "line": "New lines and shape edges are #%06X.",
                    "text": "New text is #%06X.",
                }[which]
                % (colour & 0xFFFFFF),
                4000,
            )

        def no_fill(on):
            self._set_new_no_fill(on)

        def auto(on):
            self._set_new_colours_auto(on)

        pop.chosen.connect(chosen)
        pop.noFillToggled.connect(no_fill)
        pop.autoToggled.connect(auto)
        # a new picker per click (the palette may have changed): let each go
        # once it is closed -- picked, eye-dropped or dismissed
        pop.finished.connect(pop.deleteLater)
        anchor = getattr(self, "ribbon_colours", None)
        if anchor is not None and not anchor.isVisible():
            anchor = None
        pop.open_under(anchor)

    def _on_swatch_part(self, part: str) -> None:
        """A click on a part of the ribbon's swatch pair."""
        if part == "swap":
            self._swap_new_colours()
        elif part == "no_fill":
            from ..editor import draw_styles as DS

            self._set_new_no_fill(not DS.no_fill())
        else:
            self._colour_popover(part)

    def _swap_new_colours(self) -> None:
        """Tools > Colours > Colours of New Objects > Swap Fill and Line (Shift+X)."""
        from ..editor import draw_styles as DS

        DS.swap_fill_and_line(self._colour_page())
        self._update_colour_button()
        c = DS.colours(self._colour_page())
        self.statusBar().showMessage("New shapes: fill #%06X, line #%06X." % (c["fill"], c["line"]), 4000)

    def _set_new_no_fill(self, on: bool) -> None:
        from ..editor import draw_styles as DS

        DS.set_no_fill(bool(on))
        self._update_colour_button()
        self.statusBar().showMessage(
            "New shapes are drawn hollow." if on else "New shapes are filled again.", 4000
        )

    def _set_new_colours_auto(self, on: bool) -> None:
        from ..editor import draw_styles as DS

        DS.set_colour_mode("auto" if on else "fixed", self._colour_page())
        self._update_colour_button()
        self.statusBar().showMessage(
            "The colours of new objects contrast with the page."
            if on
            else "New objects take the colours you picked.",
            4000,
        )

    def _arm_symbol_placement(self, library: str, element: str, ext: str) -> None:
        if self.doc is None:
            self.statusBar().showMessage("Open a page first to place %s.%s" % (library, element), 5000)
            return
        self._pending_symbol = (library, element, ext)
        self._pending_composite = None
        self._set_tool("symbol")
        what = "genie" if ext == "ctm" else "symbol"
        self.statusBar().showMessage(
            "Click on the page to place the %s %s.%s%s (Esc cancels)"
            % (what, library, element, "" if ext == "ctm" else ", or drag a box to size it"),
            8000,
        )

    def _paste_library_dialog(self, kind: str) -> None:
        """Edit > Paste Symbol / Genie / Super Genie."""
        if self.doc is None:
            self.statusBar().showMessage("Open a page first", 4000)
            return
        from ..browser import PageBrowserDialog

        titles = {"symbol": "Paste Symbol", "genie": "Paste Genie", "supergenie": "Paste Super Genie"}
        # a symbol pick shows the Linked check box (genies are always linked)
        extra = {"linked_option": True} if kind == "symbol" else {}
        pick = PageBrowserDialog.get_library_element(
            self,
            os.path.dirname(os.path.abspath(self.doc.path)),
            "symbol" if kind == "symbol" else "genie",
            title=titles.get(kind),
            super_genies=None if kind == "symbol" else kind == "supergenie",
            **extra,
        )
        self._sync_paste_linked()
        if pick is None:
            return
        library = os.path.splitext(os.path.basename(pick[0]))[0]
        self._arm_symbol_placement(library, pick[1], "ctl" if kind == "symbol" else "ctm")

    def _page_thumbnail_for(self, doc):
        """The TH thumbnail of another page document (a super genie page
        created by a placement), rendered in a scratch scene."""
        from ...format import thumbnail as _thumb

        pb = doc.parsed.page
        pw = (doc.page.appearance.width if doc.page is not None else 0) or pb.page_width
        ph = (doc.page.appearance.height if doc.page is not None else 0) or pb.page_height
        tw, th = _thumb.thumb_size(pw, ph)
        if not tw or doc.page is None:
            return None
        scene = PageScene()
        try:
            scene.set_page(doc.page)
            img = QtGui.QImage(tw, th, QtGui.QImage.Format_RGB32)
            img.fill(QtCore.Qt.white)
            painter = QtGui.QPainter(img)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            try:
                with scene.export_only(None):
                    scene.render(painter, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(0, 0, pw, ph))
            finally:
                painter.end()
            bits = bytes(memoryview(img.constBits()))[: tw * th * 4]
            return _thumb.encode(bits, tw, th)
        finally:
            scene.clear()
            scene.deleteLater()

    def _report_super_pages(self, reports) -> None:
        """After a genie placement / genie save: the super genie pages it
        created (CtDraw creates them then too) -- or why one could not be."""
        made = [r for r in reports if r.get("status") == "created"]
        # a page written with a save warning (its database rows refused ...)
        bad = [
            r
            for r in reports
            if r.get("status") in ("missing", "failed") or (r.get("status") == "created" and r.get("detail"))
        ]
        for r in made:
            with contextlib.suppress(Exception):
                self._saved_page_elsewhere(r["path"])
        if made:
            projects = sorted({os.path.basename(os.path.dirname(r["path"])) for r in made})
            self.statusBar().showMessage(
                "Created the super genie page%s %s in project %s (compile to use %s)"
                % (
                    "s" if len(made) > 1 else "",
                    ", ".join(r["name"] for r in made),
                    ", ".join(projects),
                    "them" if len(made) > 1 else "it",
                ),
                10000,
            )
        if bad:
            # non-blocking: the placement itself succeeded
            box = QtWidgets.QMessageBox(
                QtWidgets.QMessageBox.Warning,
                "Super Genie Page",
                "The genie opens super genies whose page could not be created, or not "
                "completely:\n\n%s\n\nThe genie was placed; its popup opens at runtime only "
                "once the page exists and is registered in the project."
                % "\n".join("• %s (%s): %s" % (r["name"], r["element"], r["detail"]) for r in bad),
                QtWidgets.QMessageBox.Ok,
                self,
            )
            box.setModal(False)
            box.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            box.show()
            self._super_page_box = box

    def _saved_page_elsewhere(self, path: str) -> None:
        """A page file written outside the open tabs: the browsers / catalog
        re-list its project."""
        from ...io.catalog import catalog_for

        with contextlib.suppress(Exception):
            cat = catalog_for()
            info = cat.project_for_dir(os.path.dirname(os.path.abspath(path)))
            if info is not None:
                cat.touch(info)

    def _on_library_drop(self, library: str, element: str, ext: str, pos: QtCore.QPointF) -> None:
        """A Library-panel card dropped on the page: paste it there."""
        self._place_library(library, element, ext, (int(pos.x()), int(pos.y())))

    def _place_library(self, library: str, element: str, ext: str, pos):
        """Paste a library genie or symbol at ``pos``."""
        idx = None
        try:
            if self.doc is None:
                self.statusBar().showMessage("Open a page first", 4000)
                return None
            x, y = int(pos[0]), int(pos[1])
            if getattr(self, "_place_anchor", "top_left") == "centre":
                size = self.doc.library_element_size(library, element, ext)
                if size is not None:
                    x, y = x - size[0] // 2, y - size[1] // 2
            # (an underlaid page's object corner near it catches it, else the grid: the
            # same element placed where the reference has it)
            snap = getattr(self, "act_snap", None)
            x, y = self.view.placement_point(x, y, grid=bool(snap is not None and snap.isChecked()))
            params = None
            if ext == "ctm":
                # (a genie placed into itself, or into a genie it holds a copy of: said
                # before its parameters are asked for)
                why = self.doc.placement_refusal(library, element, ext)
                if why:
                    QtWidgets.QMessageBox.information(self, "Place", why)
                    return None
            no_forms = app_settings().value("opt/disable_genie_forms", False, type=bool)
            if ext == "ctm" and not no_forms:
                try:
                    decl = self.doc.genie_parameters(library, element, ext)
                except Exception:  # noqa: BLE001
                    decl = {}
                # the genie's FORM (<library>.frm) when it has one -- CtDraw
                # shows the same form on Paste as on Edit
                form = self.doc.genie_form_for(library, element)
                if decl or form is not None:
                    from ..dialog.genie_dialogs import GeniePasteDialog

                    problems = [] if form is not None else self.doc.genie_form_problems(library, element)
                    dlg = GeniePasteDialog(
                        "%s.%s" % (library, element),
                        decl,
                        self._tag_completion(),
                        self,
                        form=form,
                        list_values=self.doc.form_list_values,
                        an=self.doc.next_placement_an(),
                        problems=problems,
                        pickers=self._genie_pickers(),
                    )
                    if dlg.exec() != QtWidgets.QDialog.Accepted:
                        return None
                    params = dlg.values()
            # a symbol is pasted linked or as an unlinked copy (the Linked
            # setting: Library panel / Edit > Library > Paste Symbols Linked)
            linked = ext != "ctl" or paste_linked()
            try:
                idx = self.doc.place_library_element(
                    library,
                    element,
                    ext,
                    (x, y),
                    params,
                    linked=linked,
                    page_thumbnail=self._page_thumbnail_for,
                    dialog=params is not None,
                )
            except Exception as e:  # noqa: BLE001
                QtWidgets.QMessageBox.warning(self, "Place", "%s.%s: %s" % (library, element, e))
                return None
            self._sel = [idx] if idx is not None else []
            self._refresh()
            with contextlib.suppress(Exception):
                self.library_dock.note_placed(library, element, ext)
            self.statusBar().showMessage(
                "Placed %s.%s%s" % (library, element, "" if linked else " (an unlinked copy)"), 4000
            )
            self._report_super_pages(getattr(self.doc, "super_pages", None) or [])
            if idx is not None and ext == "ctm" and no_forms:
                self._genie_object_forms(idx)
            elif idx is not None and ext == "ctl" and option("props_on_new"):
                QtCore.QTimer.singleShot(0, self, lambda i=idx: self._props_after_new(i))
        finally:
            if not (idx is not None and getattr(self, "_keep_tool", False)):
                self._set_tool("select")
        return idx

    # ------------------------------------------------------------- context menu
    # ------------------------------------------------------------- Settings > Ribbon
    CUSTOM_GROUP = "My Commands"

    def apply_ribbon_layout(self) -> None:
        """Settings > Ribbon: hide the groups the user turned off (ui/ribbon_hidden_groups,
        'Tab/Group' names) and build the user's own group on the Design tab from the
        command names in ui/ribbon_custom (the menu commands by their label)."""
        rb = getattr(self, "ribbon", None)
        if rb is None:
            return
        st = app_settings()
        hidden = {str(x) for x in (st.value("ui/ribbon_hidden_groups", [], type=list) or [])}
        for title, caption in rb.group_names():
            if caption == self.CUSTOM_GROUP:
                continue
            rb.set_group_hidden(title, caption, "%s/%s" % (title, caption) in hidden)
        wanted = [str(x) for x in (st.value("ui/ribbon_custom", [], type=list) or [])]
        old = getattr(self, "_custom_group", None)
        if old is not None:
            rb.remove_group(old)
            self._custom_group = None
        if not wanted:
            return
        from ..prefs import keymap

        page = rb.page_of("Design")
        if page is None:
            return
        g = rb.add_group(page, self.CUSTOM_GROUP, priority=0)
        n = 0
        for label in wanted:
            a = keymap.find_action(self, label)
            if a is None:
                continue
            g.add_small(a)
            n += 1
        if n == 0:
            rb.remove_group(g)
            return
        self._custom_group = g
        rb._reflow()

    @staticmethod
    def ribbon_command_labels(window) -> list:
        """The command names Settings > Ribbon offers for the user's group: every menu
        command, sorted, without duplicates."""
        from ..dialog.palette import harvest_actions

        seen = set()
        out = []
        for _a, _crumb, label in harvest_actions(window.menuBar()):
            if label and label not in seen:
                seen.add(label)
                out.append(label)
        return sorted(out, key=str.lower)

    def palette_context_actions(self) -> list:
        """The command palette's extra rows: the selection's context-menu commands no menu
        has, as (action, breadcrumb, label). The actions live on the window and are made
        anew for each palette (they act on the selection of that moment)."""
        out = []
        if self.doc is None or not self._sel:
            return out
        roots = {self.doc.root_of(i) for i in self._sel}
        if len(roots) != 1:
            return out
        i = self._sel[0]
        holder = self.__dict__.setdefault("_palette_ctx", [])
        for a in holder:
            a.deleteLater()
        holder.clear()

        doc, n = self.doc, len(self.doc.page.objects)

        def run(fn):
            # (made for this page as it was: another page shown, or an edit since that
            # changed its records, and the row's object is not that one any more)
            if self.doc is not doc or len(self.doc.page.objects) != n:
                self.statusBar().showMessage("The page changed since the search opened. Search again.", 5000)
                return
            fn()

        def make(label, fn):
            a = QtGui.QAction(label, self)
            a.triggered.connect(lambda _=False: run(fn))
            holder.append(a)
            out.append((a, "Selection", label))

        defn = self._definition_of(i)
        if defn is not None:
            make(
                "Edit Definition (%s.%s)" % (os.path.splitext(os.path.basename(defn[0]))[0], defn[1]),
                lambda d=defn: self._open_element(*d),
            )
        comp = self._composite_root(i)
        if comp is not None:
            make("Edit Composite Genie…", lambda r=comp: self._edit_genie(r, self.doc.page.objects[r]))
            make("Update Composite Genie", lambda r=comp: self._update_composite(r))
        return out

    def _context_menu(self, global_pos: QtCore.QPoint, indices: list) -> None:
        """The canvas's right-click menu: its sections (window/context_menu.py)."""
        from . import context_menu

        context_menu.show_canvas_menu(self, global_pos, indices)

    def _simulate_page(self) -> None:
        """Tools > Simulate Page: the page as it is now (unsaved edits too) in the Simulate
        window, read-only and live."""
        if self.doc is None or isinstance(self.doc, ElementDocument):
            return
        from ..simulate.window import open_simulation

        path = self.doc.path or getattr(self.doc.page, "source_path", "") or ""
        # (said at once: a large page takes a second to draw before the window shows)
        note = "Opening the page in the Simulate window…"
        self.statusBar().showMessage(note)
        self.statusBar().repaint()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            # the records as they are now, derived again in the runtime's label mode (the
            # editor's literal labels '@(...)' / '="..."' are Graphics Builder text)
            open_simulation(
                path,
                page=self.doc.page,
                parsed=getattr(self.doc, "parsed", None) if path else None,
                doc=self.doc if path else None,
                host=self,
            )
        except Exception as exc:  # noqa: BLE001 -- say why, keep the editor going
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Simulate Page", "The page could not be simulated. %s" % exc)
        else:
            QtWidgets.QApplication.restoreOverrideCursor()
        finally:
            if self.statusBar().currentMessage() == note:
                self.statusBar().clearMessage()

    def _composite_root(self, index: int):
        """The placed composite genie ``index`` is (or lies in, or whose unit
        holds it as its root), or None."""
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return None
        pg = self.doc.page
        for k in [self.doc.root_of(index), index] + list(pg.ancestors(index)):
            if 0 <= k < len(pg.objects) and pg.objects[k].kind.value == "composite_genie":
                return k
        return None

    def _definition_of(self, index: int):
        """(library path, element) of the library genie / symbol a placed
        object (or its unit root) instantiates -- for the page's template
        objects, the template (.ctt) -- or None. Never a composite genie's
        composition cache (only Plant SCADA writes it)."""
        if self.doc is None:
            return None
        from ...format.symbols import reference_from_object

        objs = self.doc.parsed.page.objects
        root = self.doc.root_of(index)
        for i in (root, index):
            if not (0 <= i < len(objs)):
                continue
            try:
                ref = reference_from_object(objs[i])
            except Exception:  # noqa: BLE001
                ref = None
            if (
                ref is None
                or ref.ext not in ("ctl", "ctm", "ctt")
                or "_compositioncaches" in (ref.library or "").lower()
            ):
                continue
            try:
                path = self.doc.library_path(ref.library, ref.element, ref.ext)
            except Exception:  # noqa: BLE001
                path = None
            if path:
                return path, ref.element
        if self._is_template_obj(root) and not isinstance(self.doc, ElementDocument):
            with contextlib.suppress(Exception):
                return self.doc.template_element()
        return None

    def _owner_definition(self, index: int):
        """(library path, element) whose edit changes a read-only object."""
        if self.doc is None:
            return None
        from ...format.symbols import reference_from_object
        from ..objprops.registry import placed_genies_of

        objs = self.doc.parsed.page.objects
        for g in placed_genies_of(self.doc, index):
            try:
                ref = reference_from_object(objs[g])
                path = (
                    self.doc.library_path(ref.library, ref.element, ref.ext)
                    if ref is not None and ref.ext in ("ctl", "ctm")
                    else None
                )
            except Exception:  # noqa: BLE001
                path = None
            if path and "_compositioncaches" in (ref.library or "").lower():
                # a composite genie: its composition cache is Plant SCADA's
                return None
            if path:
                return path, ref.element
        return self._definition_of(index)

    def _owner_definition_label(self, index: int) -> str:
        """'library.element' of :meth:`_owner_definition`, or ''."""
        d = self._owner_definition(index)
        return "%s.%s" % (os.path.splitext(os.path.basename(d[0]))[0], d[1]) if d else ""

    def _edit_owner_definition(self, index: int) -> None:
        d = self._owner_definition(index)
        if d is None:
            self.statusBar().showMessage(
                "The definition of this object is not in this project or the projects "
                "it includes, so it cannot be opened here.",
                6000,
            )
            return
        self._open_element(*d)

    def _read_only_reason(self, index: int) -> str:
        """Why object ``index``'s settings are shown read-only ('' = editable):
        a placed genie's member, or a template record while templates are
        locked (the Settings option, Break Lock Mode)."""
        if self.doc is None:
            return ""
        from ..objprops.registry import read_only_reason

        return read_only_reason(
            self.doc, index, template_locked=self._template_locked() and not self._break_lock
        )

    def page_properties(self) -> None:
        """CtDraw's Page Properties dialog (General/Appearance/Keyboard
        Commands/Events/Environment/Associations)."""
        self._show_target_tab()  # (from a tab's menu: that page)
        if self.doc is None:
            return
        from ...io.project import Project
        from ..dialog.page_props import PagePropertiesDialog

        # one window per document (tabs)
        if self._raise_modeless(self._modeless_for("pageprops")):
            return
        # a library element has no pages.DBF row (its properties / size /
        # colour live in the element itself)
        project = (
            None
            if isinstance(self.doc, ElementDocument)
            else Project(os.path.dirname(os.path.abspath(self.doc.path)))
        )
        dlg = PagePropertiesDialog(self.doc, project, self)
        # every Apply that changed the page refreshes the window at once (the
        # unsaved mark, Undo: its ``applied`` signal, _show_modeless); the
        # pages.DBF / keyboard edits are staged in the document and written
        # by the next Save
        self._show_modeless(dlg, ("pageprops", doc_key(self.doc)), scope="page", title="Page Properties")

    # ------------------------------------------------------------ preferences
    @staticmethod
    def _settings() -> QtCore.QSettings:
        return app_settings()

    def _dialog_dir(self, role: str = "open") -> str:
        """Last directory used for a file dialog of ``role``, falling back to
        the current document's folder, then the Example project."""
        st = self._settings()
        d = st.value("dir/" + role, "", type=str)
        if d and os.path.isdir(d):
            return d
        if self.doc is not None:
            return os.path.dirname(os.path.abspath(self.doc.path))
        return default_open_dir()

    def _remember_dir(self, role: str, path: str) -> None:
        if path:
            self._settings().setValue("dir/" + role, os.path.dirname(os.path.abspath(path)))

    def save_prefs(self) -> None:
        """Persist the session to QSettings (window geometry + dock layout,
        open file, view toggles)."""
        st = self._settings()
        st.setValue("win/geometry", self.saveGeometry())
        st.setValue("win/state", self.saveState())
        if self.doc is not None:
            st.setValue("session/last_file", os.path.abspath(self.doc.path))
        st.setValue("session/open_files", self.merged_session_files(st, self))
        tabs, current = self.session_tabs(st, self)
        st.setValue("session/tabs", json.dumps(tabs))
        st.setValue("session/current", current)
        # (the pinned pages' paths, also on their own: the rows above carry them too)
        st.setValue("session/pinned", [r["path"] for r in tabs if r.get("pinned")])
        st.setValue("view/grid", self._show_grid)
        st.setValue("view/grid_size", self._grid_size)
        st.setValue("view/grid_y", 0 if self._grid_y is None else self._grid_y)
        st.setValue("view/grid_origin_x", self._grid_origin[0])
        st.setValue("view/grid_origin_y", self._grid_origin[1])
        st.setValue("view/snap", self.act_snap.isChecked())
        st.setValue("view/snap_guides", self.act_snap_guides.isChecked())
        st.setValue("view/guides_visible", self.act_show_guides.isChecked())
        st.setValue("view/crosshair", self.act_crosshair.isChecked())
        st.setValue("view/selection_hud", self.act_selection_hud.isChecked())
        st.setValue("view/status_bar", self.act_status_bar.isChecked())
        st.setValue("view/toolbox", self.act_show_tools.isChecked())
        if getattr(self, "_toolbox", None) is not None:
            st.setValue("view/toolbox_geometry", self._toolbox.saveGeometry())
        st.setValue("view/smart_guides", self.act_smart_guides.isChecked())
        st.setValue("view/smart_guide_tol", self.view.SMART_GUIDE_TOL)
        st.setValue("view/smart_guide_outline", bool(self.view.SMART_GUIDE_OUTLINE))
        st.setValue("view/nudge_step", self._nudge_step)
        st.setValue("view/slow_keys", self.act_slow_keys.isChecked())
        st.setValue("view/slow_mouse", self.act_slow_mouse.isChecked())
        st.setValue("view/thumb", self._show_thumb)
        st.setValue("sel/filter", self.sel_filter.currentIndex())
        st.setValue("sel/marquee", self.sel_marquee.currentIndex())

    @classmethod
    def merged_session_files(cls, st, window=None) -> list:
        """The session's tab list merged with another ctgedit process's."""
        mine: list = []
        windows = [w for w in cls._instances if w is window or w.isVisible()]
        if window is not None and window not in windows:
            windows.insert(0, window)
        for w in windows:
            for e in getattr(w, "_docs", []):
                p = os.path.abspath(e["doc"].path)
                if p not in mine:
                    mine.append(p)
        mine_keys = {os.path.normcase(p) for p in mine}
        keep = []
        for f in st.value("session/open_files", [], type=list) or []:
            f = str(f)
            k = os.path.normcase(os.path.abspath(f)) if f else ""
            if f and k not in cls._session_seen and k not in mine_keys and f not in keep:
                keep.append(f)
        return keep + mine

    @classmethod
    def session_tabs(cls, st, window=None):
        """The tabs of the session as JSON rows ({path, element, transform: 6 numbers,
        scroll: [x, y], pinned}) and the index of the current one (-1: none): this process's
        windows' tabs, after the rows another ctgedit process stored for pages this one
        never had open (as :meth:`merged_session_files`)."""
        mine, current = [], -1
        windows = [w for w in cls._instances if w is window or w.isVisible()]
        if window is not None and window not in windows:
            windows.insert(0, window)
        keys = set()
        for w in windows:
            for e in getattr(w, "_docs", []):
                d = e["doc"]
                element = (
                    d.element_name if isinstance(d, ElementDocument) else getattr(d, "element", "") or ""
                )
                path = os.path.abspath(d.path)
                key = (os.path.normcase(path), element.lower())
                if key in keys:
                    continue
                keys.add(key)
                row = {"path": path, "element": element, "pinned": bool(e.get("pinned"))}
                if d is w.doc:
                    w._stash_view(e)
                tr, sc = e.get("transform"), e.get("scroll")
                if tr is not None and sc is not None:
                    row["transform"] = [tr.m11(), tr.m12(), tr.m21(), tr.m22(), tr.dx(), tr.dy()]
                    row["scroll"] = [int(sc[0]), int(sc[1])]
                if w is window and d is w.doc:
                    current = len(mine)
                mine.append(row)
        keep = []
        # (another process's rows: of the pages its session/open_files still lists)
        listed = {
            os.path.normcase(os.path.abspath(str(f)))
            for f in st.value("session/open_files", [], type=list) or []
            if f
        }
        for row in _session_rows(st):
            k = os.path.normcase(os.path.abspath(row["path"]))
            if (
                k not in cls._session_seen
                and k in listed
                and (k, (row.get("element") or "").lower()) not in keys
            ):
                keep.append(row)
        return keep + mine, (current + len(keep) if current >= 0 else -1)

    def apply_canvas_options(self) -> None:
        """Apply the canvas settings this window keeps."""
        from ..prefs.settings import OPEN_ZOOM, OPEN_ZOOM_DEFAULT, choice, paste_offset

        st = self._settings()
        v = self.view
        tol = type(v).SMART_GUIDE_TOL
        try:
            v.SMART_GUIDE_TOL = float(st.value("view/smart_guide_tol", tol, type=float))
        except TypeError, ValueError:  # swallow-ok: a garbled setting reads as its default
            v.SMART_GUIDE_TOL = tol
        v.SMART_GUIDE_OUTLINE = bool(st.value("view/smart_guide_outline", True, type=bool))
        try:
            self._nudge_step = int(st.value("view/nudge_step", 1, type=int))
        except TypeError, ValueError:  # swallow-ok: a garbled setting reads as its default
            self._nudge_step = 1
        # (a step changed in Settings while Cursor Keys Slow Speed is on would seem
        # ignored: say so; the first read is the window's start, not a change)
        seen = getattr(self, "_nudge_step_seen", None)
        if (
            seen is not None
            and seen != self._nudge_step
            and getattr(self, "act_slow_keys", None) is not None
            and self.act_slow_keys.isChecked()
        ):
            from ..notify import notify

            notify(
                self,
                "The arrow key step is %d px, but Cursor Keys Slow Speed (Tools > "
                "Advanced) is on, so the arrow keys move 1 px until it is unticked." % self._nudge_step,
                "info",
            )
        self._nudge_step_seen = self._nudge_step
        self._paste_offset = paste_offset()
        self._open_zoom = choice("opt/open_zoom", OPEN_ZOOM, OPEN_ZOOM_DEFAULT)

    #: the View menu's toggles that Settings > Canvas > "Grid and guidelines" shows too: the
    #: stored key, the action, the canvas's setter (None: the window's own _show_grid)
    VIEW_TOGGLES = (
        ("view/grid", "act_grid", None),
        ("view/snap", "act_snap", "set_snap"),
        ("view/snap_guides", "act_snap_guides", "set_snap_guides"),
        ("view/guides_visible", "act_show_guides", "set_guides_visible"),
        ("view/smart_guides", "act_smart_guides", "set_smart_guides"),
    )

    def apply_view_toggles(self) -> None:
        """Apply the View menu's toggles from settings."""
        st = self._settings()
        for key, act, setter in self.VIEW_TOGGLES:
            on = bool(st.value(key, True, type=bool))
            getattr(self, act).setChecked(on)
            if setter is None:
                self._show_grid = on
            else:
                getattr(self.view, setter)(on)

    def restore_session(self, reopen: bool = True, defer: bool = False) -> bool:
        """Restore geometry, dock layout, view settings and optionally the last file."""
        st = self._settings()
        geo = st.value("win/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = st.value("win/state")
        if state is not None:
            # (a stored layout names the Simulate dock too: it stays as the simulation
            # docked here has it, whatever the stored state says)
            dock = getattr(self, "_sim_dock", None)
            shown = dock is not None and not dock.isHidden()
            self.restoreState(state)
            if dock is not None:
                with contextlib.suppress(Exception):
                    dock.setFloating(False)
                    dock.setVisible(shown)
        # (the grid, snap, guideline and smart guide toggles: every window reads them when it
        # is made too)
        self.apply_view_toggles()
        gy = int(st.value("view/grid_y", 0, type=int) or 0)
        self._apply_grid(
            st.value("view/grid_size", 8, type=int),
            gy or None,
            st.value("view/grid_origin_x", 0, type=int),
            st.value("view/grid_origin_y", 0, type=int),
        )
        self.act_crosshair.setChecked(st.value("view/crosshair", False, type=bool))
        self.act_selection_hud.setChecked(st.value("view/selection_hud", True, type=bool))
        self._toggle_crosshair()
        self.act_status_bar.setChecked(st.value("view/status_bar", True, type=bool))
        self._toggle_status_bar()
        if st.value("view/toolbox", False, type=bool):
            self.act_show_tools.setChecked(True)
            # shown once the editor window is (restore runs before its show)
            QtCore.QTimer.singleShot(0, self, self._toggle_toolbox)
        # Settings > Canvas and Editing (every window reads them when it is made too; the
        # wheel's and the other navigation choices are the canvas's own: apply_nav_options)
        self.apply_canvas_options()
        from ..prefs.settings import apply_drawing_options, apply_undo_budget

        apply_drawing_options()
        apply_undo_budget()
        self.act_slow_keys.setChecked(st.value("view/slow_keys", False, type=bool))
        self.act_slow_mouse.setChecked(st.value("view/slow_mouse", False, type=bool))
        self.view.set_slow_mouse(self.act_slow_mouse.isChecked())
        self._show_thumb = st.value("view/thumb", False, type=bool)
        self.act_thumb.setChecked(self._show_thumb)
        self._update_colour_button()
        self.sel_filter.setCurrentIndex(st.value("sel/filter", 0, type=int))
        self.sel_marquee.setCurrentIndex(st.value("sel/marquee", 0, type=int))
        if reopen:
            # an element tab stores its LIBRARY (.ctl/.ctm/.ctt) path: element
            # tabs are not reopened by the session, so they are skipped quietly
            # (open_document would refuse the library as "not a page")
            def _page(f):
                return bool(f) and os.path.splitext(f)[1].lower() not in (".ctl", ".ctm", ".ctt")

            last = st.value("session/last_file", "", type=str)
            if not _page(last):
                last = ""
            stored = [str(f) for f in st.value("session/open_files", [], type=list)]
            # this process takes the stored list over: an entry that does not
            # reopen (a deleted page, a library tab) is not kept for ever as
            # "another editor's" (merged_session_files)
            _mw().MainWindow._session_seen.update(
                os.path.normcase(os.path.abspath(f)) for f in stored + [last] if f
            )
            rows = _session_rows(st)
            if rows:
                # the tabs as they were: their order, pins, library elements and views
                return self._restore_tab_rows(rows, st, stored, defer)
            files = [f for f in stored if _page(f)]
            return self._restore_tabs(files, last, defer)
        return False

    def _restore_tab_rows(self, rows: list, st, stored: list, defer: bool = False) -> bool:
        """The session's tabs from its rows (session/tabs, :meth:`session_tabs`): pinned ones
        first, each opened when first shown, the current one (session/current) shown. A page
        listed in session/open_files but not in the rows (another editor window's) comes
        after them."""
        failures = []
        try:
            current = int(st.value("session/current", -1))
        except TypeError, ValueError:  # swallow-ok: a garbled index shows the last tab
            current = -1
        cur_row = rows[current] if 0 <= current < len(rows) else None
        if stored:
            # session/open_files says which files were open last (it is kept current while
            # ctgedit runs, so a crash keeps it); the rows add their order, pins and views
            listed = {os.path.normcase(os.path.abspath(f)) for f in stored if f}
            rows = [r for r in rows if os.path.normcase(os.path.abspath(r["path"])) in listed]
        known = {
            (os.path.normcase(os.path.abspath(r["path"])), (r.get("element") or "").lower()) for r in rows
        }
        for f in stored:
            if (
                f
                and os.path.splitext(f)[1].lower() == ".ctg"
                and (os.path.normcase(os.path.abspath(f)), "") not in known
            ):
                rows.append({"path": f, "element": "", "pinned": False})
        open_keys = {
            (
                os.path.normcase(os.path.abspath(e["doc"].path)),
                (getattr(e["doc"], "element_name", "") or "").lower(),
            )
            for e in self._docs
        }
        pinned = {
            os.path.normcase(os.path.abspath(str(p)))
            for p in st.value("session/pinned", [], type=list) or []
            if p
        }
        for r in rows:
            if not r.get("element") and os.path.normcase(os.path.abspath(r["path"])) in pinned:
                r["pinned"] = True
        ordered = [r for r in rows if r.get("pinned")] + [r for r in rows if not r.get("pinned")]
        cur_entry = None
        for r in ordered:
            path, element = r["path"], r.get("element") or ""
            key = (os.path.normcase(os.path.abspath(path)), element.lower())
            if key in open_keys:
                continue
            open_keys.add(key)
            if not os.path.exists(path):
                failures.append((path, FileNotFoundError(2, "No such file", path)))
                continue
            view = None
            tr, sc = r.get("transform"), r.get("scroll")
            if isinstance(tr, list) and len(tr) == 6 and isinstance(sc, list) and len(sc) == 2:
                with contextlib.suppress(TypeError, ValueError):
                    view = (
                        QtGui.QTransform(
                            float(tr[0]), float(tr[1]), float(tr[2]), float(tr[3]), float(tr[4]), float(tr[5])
                        ),
                        (int(sc[0]), int(sc[1])),
                    )
            entry = self._add_pending_tab(path, element, bool(r.get("pinned")), view)
            if not element:
                self._push_mru(path)
            if r is cur_row:
                cur_entry = entry
        if not self._docs:
            if failures:
                QtCore.QTimer.singleShot(0, self, lambda f=failures: self.report_open_failures(f))
            return False
        if cur_entry is None:
            cur_entry = self._docs[-1]
        self._switching = True
        try:
            self.doc_tabs.setCurrentIndex(self._entry_index(cur_entry))
        finally:
            self._switching = False
        if defer:

            def later(fl=failures):
                self._open_restored(fl)
                if fl:
                    self.report_open_failures(fl)

            QtCore.QTimer.singleShot(0, self, later)
            return True
        opened = self._open_restored(failures)
        if failures:
            QtCore.QTimer.singleShot(0, self, lambda f=failures: self.report_open_failures(f))
        return opened and self.doc is not None

    def _restore_tabs(self, files: list, last: str, defer: bool = False) -> bool:
        """Restore the session's tabs. Only the active page is read now."""
        failures = []  # a page that was open and does not reopen is REPORTED
        last_key = os.path.normcase(last or "")
        open_keys = {os.path.normcase(os.path.abspath(e["doc"].path)) for e in self._docs}
        opened = False
        for f in files:
            if not f or os.path.normcase(f) == last_key:
                continue
            k = os.path.normcase(os.path.abspath(f))
            if k in open_keys:
                continue
            open_keys.add(k)
            if not os.path.exists(f):
                failures.append((f, FileNotFoundError(2, "No such file", f)))
                continue
            self._add_pending_tab(f)
            self._push_mru(f)
            opened = True
        was_open = any(os.path.normcase(f) == last_key for f in files)
        if last and (os.path.exists(last) or was_open):
            if defer and os.path.exists(last) and os.path.normcase(os.path.abspath(last)) not in open_keys:
                entry = self._add_pending_tab(last)  # the active tab last
                self._push_mru(last)
                self._switching = True
                try:
                    self.doc_tabs.setCurrentIndex(self._entry_index(entry))
                finally:
                    self._switching = False
                opened = True
            else:
                try:
                    self.open_document(last, ask_supergenie=False)  # the active tab last
                    opened = True
                except Exception as e:  # noqa: BLE001
                    failures.append((last, e))
        if self.doc is None and self._docs:
            # the active page did not come back: the LAST tab is shown, as when
            # every tab was opened in turn (addTab had made the first current)
            self._switching = True
            try:
                self.doc_tabs.setCurrentIndex(len(self._docs) - 1)
            finally:
                self._switching = False
        if defer:
            # the window shows first; the active page opens on the next turn
            def later(fl=failures):
                self._open_restored(fl)
                if fl:
                    self.report_open_failures(fl)

            QtCore.QTimer.singleShot(0, self, later)
            return opened
        if self.doc is None and self._docs:
            opened = self._open_restored(failures)
        if failures:
            # after the window is shown (the app shows it right after this)
            QtCore.QTimer.singleShot(0, self, lambda f=failures: self.report_open_failures(f))
        return opened and self.doc is not None

    def _open_restored(self, failures: list) -> bool:
        """Show the current tab of a restored session, opening it (a page
        that no longer opens is dropped: the last remaining tab is tried
        next). False when none opened."""
        while self._docs:
            k = self.doc_tabs.currentIndex()
            if not 0 <= k < len(self._docs):
                k = len(self._docs) - 1
            entry = self._docs[k]
            if not isinstance(entry["doc"], _PendingPage):
                if entry["doc"] is not self.doc:
                    self._activate_doc(entry)
                return True
            if self._materialise(entry, failures):
                entry["post_open"] = True
                self._switching = True
                try:
                    self.doc_tabs.setCurrentIndex(k)
                finally:
                    self._switching = False
                self._activate_doc(entry)
                return True
            if self._docs:  # dropped: the last tab next
                self._switching = True
                try:
                    self.doc_tabs.setCurrentIndex(len(self._docs) - 1)
                finally:
                    self._switching = False
        return False

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        # every open page with unsaved edits is asked about (its tab shown);
        # a page whose save fails keeps the window open -- but the other pages
        # are still asked, so one read-only / locked page never costs their edits
        self._flush_text_edit()
        # two or more unsaved pages: one Save Changes list (save_changes.SaveChangesDialog)
        dirty = [(en, self._pending_windows(en["doc"])) for en in self._docs]
        dirty = [(en, p) for en, p in dirty if en["doc"].dirty or p]
        if len(dirty) >= 2:
            if not self._save_changes_list(dirty, "closing"):
                e.ignore()
                return
            self._finish_close(e)
            return
        unsaved = []
        for k in range(len(self._docs) - 1, -1, -1):
            doc = self._docs[k]["doc"]
            # typed values of its windows not applied yet count as its edits
            pending = self._pending_windows(doc)
            if not doc.dirty and not pending:
                continue
            self.doc_tabs.setCurrentIndex(k)
            r = self._ask_save_on_close(doc, pending, "closing")
            if r == QtWidgets.QMessageBox.Save:
                try:
                    ok = self._apply_pending_windows(pending) and (not doc.dirty or self.save())
                except Exception as ex:  # noqa: BLE001 -- never close over a failed save
                    ok = False
                    with contextlib.suppress(Exception):
                        self._save_failed(doc.path, ex, offer_save_as=False)
                if not ok:
                    unsaved.append(doc)
            elif r == QtWidgets.QMessageBox.Cancel:
                e.ignore()
                return
            else:  # Discard: its recovery copy goes too
                from ..recovery import AutosaveController

                AutosaveController.forget(doc)
        if unsaved:
            e.ignore()  # a refused / failed save keeps the window
            for k, entry in enumerate(self._docs):
                if entry["doc"] is unsaved[-1]:
                    self.doc_tabs.setCurrentIndex(k)
                    break
            self.statusBar().showMessage(
                "The window stays open. Not saved: %s"
                % ", ".join(os.path.basename(d.path) for d in reversed(unsaved)),
                12000,
            )
            return
        self._finish_close(e)

    def _finish_close(self, e: QtGui.QCloseEvent) -> None:
        """The window closes (every unsaved page was saved or discarded): the views of its
        tabs are kept for the next open, the session is written, its windows go."""
        with contextlib.suppress(Exception):
            self._remember_all_views()
        # only the last window writes the session (a closing child must not
        # clobber the layout still open elsewhere)
        if len([w for w in _mw().MainWindow._instances if w.isVisible()]) <= 1:
            with contextlib.suppress(Exception):
                self.save_prefs()
        if self in _mw().MainWindow._instances:
            _mw().MainWindow._instances.remove(self)
        # its windows go with it (another editor window may keep the program
        # running: Grid Setup / Goto Object / a sheet must not float on,
        # acting on a closed window)
        with contextlib.suppress(Exception):
            self._close_modeless()
        # its library watch stops (window/libwatch.py)
        with contextlib.suppress(Exception):
            self._close_library_watch()
        # a simulation docked in this window stops, as closing the Simulate window does
        with contextlib.suppress(Exception):
            from ..simulate.window import simulate_window

            sw = simulate_window(create=False)
            if sw is not None:
                sw.release_host(self)
        for name in ("_find_dlg", "_unresolved_dlg"):
            d = getattr(self, name, None)
            if d is not None:
                with contextlib.suppress(RuntimeError):
                    d.close()
        jobs = getattr(self, "_genie_jobs", None)
        if jobs is not None:
            jobs.shutdown()  # (genie updates of pages that close now)
        from ..dialog import element_save

        element_save.stop(self)  # (its helper for the saves of large elements)
        # (nothing lands on its pages any more, and they are let go)
        try:
            self._release_closed_window()
        except Exception:  # noqa: BLE001 -- the window closes anyway
            from ...infra import logsetup

            logsetup.note_exception("ui.window.shell.release_closed_window")
        e.accept()
        self._free_closed_window()

    #: the window's timers that act on its documents (stopped when it closes)
    _DOC_TIMERS = (
        "_idle_timer",
        "_after_run_timer",
        "_lib_timer",
        "_disk_timer",
        "_ctf_timer",
        "_nudge_timer_obj",
        "_hud_timer",
        "_window_sync_timer",
        "_chain_named_timer",
    )

    def _release_closed_window(self) -> None:
        """Clean up after a window closes."""
        self._window_closed = True
        for name in self._DOC_TIMERS:
            t = self.__dict__.get(name)
            if t is not None:
                with contextlib.suppress(RuntimeError):
                    t.stop()
        self._idle_docs, self._after_run_docs = [], []
        # (and those waiting for the running chain's answer to name them, or whose reload
        # of what its helper wrote waits for the user: files._when_chain_names,
        # library._chain_writes_waiting)
        self._chain_named_docs, self._chain_waits = [], {}
        self._post_open_pending = []
        self._lib_pending = set()
        self._disk_pending = set()
        q = self.__dict__.get("_ctf_queue")
        if q:
            q.clear()
        from ..recovery import AutosaveController

        for entry in list(self._docs):
            doc = entry["doc"]
            if not isinstance(doc, _PendingPage):
                with contextlib.suppress(Exception):
                    doc.stop_ctf_precheck()
                with contextlib.suppress(Exception):
                    self._underlay_doc_closed(doc)
                with contextlib.suppress(Exception):
                    AutosaveController.forget(doc)
            with contextlib.suppress(Exception):
                self._drop_tab_scene(entry)
        self._switching = True
        try:
            self._docs[:] = []
            with contextlib.suppress(RuntimeError):
                while self.doc_tabs.count():
                    self.doc_tabs.removeTab(self.doc_tabs.count() - 1)
        finally:
            self._switching = False
        self.__dict__.pop("_mru_entries", None)
        self.__dict__.pop("_scene_order", None)
        self.doc = None
        self._sel = []
        with contextlib.suppress(Exception):
            self.scene.clear_page()
        # (the panels let the page go too)
        with contextlib.suppress(Exception):
            self.objects_dock.set_page(None, [])
        with contextlib.suppress(Exception):
            self.props.set_target(None, [])
        with contextlib.suppress(Exception):
            self.tags_dock.set_project_dir(None)
        with contextlib.suppress(Exception):
            self._auto_bar.show_for(None)
        self._hist_sel = None
        self._cap_arm = None

    def _free_closed_window(self) -> None:
        """A closed window is deleted (its widgets and scenes go) once no task of its own
        runs: the chain after a library save or an Update Pages run started here goes on on
        a timer of the window, and says its result in another window. Outside a running
        event loop (scripts, tests) the window is left as it is: nothing would delete it."""
        if not self.__dict__.get("_window_closed") or any(w is self for w in _mw().MainWindow._instances):
            return
        if QtCore.QThread.currentThread().loopLevel() == 0:
            return
        from ..jobs import tasks

        if tasks.running(self):
            from ..prefs.job_options import poll_ms

            QtCore.QTimer.singleShot(poll_ms(), self, self._free_closed_window)
            return
        self.deleteLater()


def _session_rows(st) -> list:
    """session/tabs as a list of rows with a 'path' (a garbled value: none)."""
    raw = st.value("session/tabs", "")
    if not raw:
        return []
    try:
        rows = json.loads(str(raw))
    except ValueError:  # swallow-ok: a garbled value reads as no rows
        return []
    return (
        [r for r in rows if isinstance(r, dict) and isinstance(r.get("path"), str) and r["path"]]
        if isinstance(rows, list)
        else []
    )


def _mw():
    """ctgedit.ui.main_window (MainWindow is defined there, after this module is imported)."""
    from .. import main_window

    return main_window
