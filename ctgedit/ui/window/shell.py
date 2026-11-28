"""Windows, help, recent pages, view options and preferences."""

from __future__ import annotations

from ...infra.logsetup import note_exception as _note_exc

from ..main_window_base import editor_windows, main_window_class

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # (names only the annotations use)
    from ..main_window import MainWindow

from ..main_window_base import (
    QtCore,
    QtGui,
    TITLE_APP,
    app_settings,
    os,
)
from ..main_window_base import (
    LABEL_MODE_NAMES,
)


class WindowShell:
    """MainWindow's windows, help, recent pages, view options, labels, the context menu and
    preferences."""

    # ----------------------------------------------------------- windows
    def _new_window(self) -> "MainWindow":
        win = main_window_class()()
        win.show()
        return win

    @staticmethod
    def _open_windows() -> list["MainWindow"]:
        return [w for w in editor_windows() if w.isVisible()]

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
        return out[: main_window_class().MRU_MAX]

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
        add("literal", LABEL_MODE_NAMES["literal"]).setStatusTip(
            "Show the @( ) labels as they are stored, with the @( ) round the native text."
        )
        add("native", LABEL_MODE_NAMES["native"]).setStatusTip(
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
                _note_exc("shell._rebuild_labels_menu")
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
                _note_exc("shell._set_label_mode")
                pass
            self._refresh()
        self.statusBar().showMessage("Labels: %s" % LABEL_MODE_NAMES.get(mode, mode), 3000)

    def _toggle_thumb(self) -> None:
        self._show_thumb = self.act_thumb.isChecked()
        self._refresh()

    # ------------------------------------------------------------- context menu
