"""The tab menu, switching and reopening tabs."""

from __future__ import annotations

from ..main_window_base import (
    ElementDocument,
    QtCore,
    QtGui,
    QtWidgets,
    contextlib,
    os,
)
from ..main_window_base import (
    PendingPage,
)
from .tabs import (
    ShortcutKeyRecorder,
)


class TabMenu:
    """The tab's menu and its commands, switching tabs and reopening a closed one."""

    # -------------------------------------------------- the tab menu and its commands
    def _target_entry(self):
        """The tab a tab command works on: the one its menu was opened on, else the current
        one (None: no tabs)."""
        t = self.__dict__.get("_tab_target")
        if t is not None and any(t is e for e in self._docs):
            return t
        k = self.doc_tabs.currentIndex()
        return self._docs[k] if 0 <= k < len(self._docs) else None

    def _tab_menu(self, k: int, global_pos: QtCore.QPoint) -> None:
        """Right-click on a tab: its commands (the File and Window menus' actions)."""
        if not 0 <= k < len(self._docs):
            return
        menu = self.build_tab_menu(k)
        try:
            self._exec_tab_menu(menu, global_pos)
        finally:
            self._tab_target = None
            self._update_actions()
            menu.deleteLater()

    @staticmethod
    def _exec_tab_menu(menu, pos) -> None:
        """Show the tab menu (a seam: tests trigger its actions instead)."""
        menu.exec(pos)

    def build_tab_menu(self, k: int) -> QtWidgets.QMenu:
        """The menu of tab ``k`` (its commands then act on that tab)."""
        self._tab_target = self._docs[k]
        self._update_actions()
        menu = QtWidgets.QMenu(self)
        for a in (
            self.act_close_doc,
            self.act_close_others,
            self.act_close_right,
            self.act_close_saved,
            self.act_close_all,
        ):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(self.act_pin_tab)
        menu.addAction(self.act_reopen_closed)
        menu.addSeparator()
        for a in (
            self.act_copy_page_path,
            self.act_copy_page_name,
            self.act_reveal_page,
            self.act_show_in_browser,
        ):
            menu.addAction(a)
        menu.addSeparator()
        # (these two show the tab first: they act on the page shown)
        menu.addAction(self.act_page_props)
        menu.addAction(self.act_revert)
        menu.addSeparator()
        menu.addAction(self.act_move_to_window)
        sub = menu.addMenu("Move to Window")
        self._fill_move_to_menu(sub)
        return menu

    def _show_target_tab(self) -> None:
        """A command of the tab menu that acts on the page shown: its tab is shown first."""
        t = self.__dict__.get("_tab_target")
        if t is not None and any(t is e for e in self._docs) and t["doc"] is not self.doc:
            self.show_tab(self._entry_index(t))

    def _closable(self, entries) -> list:
        """``entries`` without the pinned tabs."""
        return [e for e in entries if not e.get("pinned")]

    def close_other_tabs(self) -> bool:
        """Close Others: every unpinned tab but the target one (one save question)."""
        t = self._target_entry()
        if t is None:
            return False
        ok = self._close_entries(self._closable(e for e in self._docs if e is not t), show=t)
        if ok and any(t is e for e in self._docs) and self.doc is not t["doc"]:
            self.show_tab(self._entry_index(t))
        return ok

    def close_tabs_to_the_right(self) -> bool:
        t = self._target_entry()
        if t is None:
            return False
        k = self._entry_index(t)
        return self._close_entries(self._closable(self._docs[k + 1 :]))

    def close_saved_tabs(self) -> bool:
        """Close Saved: the unpinned tabs without unsaved changes."""
        return self._close_entries(
            [
                e
                for e in self._closable(self._docs)
                if not e["doc"].dirty and not self._pending_windows(e["doc"])
            ]
        )

    def close_all_tabs(self) -> bool:
        """Close All Pages (Ctrl+Shift+W): every unpinned tab (one save question)."""
        return self._close_entries(self._closable(self._docs))

    def _close_entries(self, entries, show=None) -> bool:
        """Close the tabs of ``entries``."""
        entries = [e for e in entries if any(e is d for d in self._docs)]
        if not entries:
            return True
        self._flush_text_edit()
        unsaved = [(e, self._pending_windows(e["doc"])) for e in entries]
        unsaved = [(e, p) for e, p in unsaved if e["doc"].dirty or p]
        if len(unsaved) >= 2:
            if not self._save_changes_list(unsaved, "closing them"):
                return False
        elif unsaved:
            # one unsaved page: its own question first (Cancel closes nothing)
            if not self._ask_to_close(self._entry_index(unsaved[0][0])):
                return False
        left = [e for e in entries if any(e is d for d in self._docs)]
        shown = next((e for e in left if e["doc"] is self.doc), None)
        for e in sorted((e for e in left if e is not shown), key=self._entry_index, reverse=True):
            if not any(e is d for d in self._docs):
                continue
            if not self._close_doc_tab(self._entry_index(e), ask=False):
                return False
        if shown is not None and any(shown is d for d in self._docs):
            if not self._close_doc_tab(self._entry_index(shown), ask=False, show=show):
                return False
        return True

    def _save_changes_list(self, unsaved, close_word: str = "closing") -> bool:
        """The Save Changes list for ``unsaved`` = [(entry, pending windows)] (two or more):
        the ticked pages are saved in tab order (stopping at the first that fails, with its
        message), the others are discarded (their recovery copies go). True: go on and
        close them all."""
        from ..dialog.save_changes import SaveChangesDialog

        rows = []
        for e, pending in sorted(unsaved, key=lambda u: self._entry_index(u[0])):
            rows.append(
                (
                    e,
                    self._tab_title(e["doc"]),
                    self._doc_project(e["doc"]),
                    self._change_note(e["doc"], pending),
                    os.path.dirname(os.path.abspath(e["doc"].path)),
                    not (not pending and self._only_auto_changes(e["doc"])),
                )
            )
        dlg = SaveChangesDialog(rows, self)
        r = self._exec_save_changes(dlg)
        if r == SaveChangesDialog.CANCEL:
            return False
        keep = dlg.selected() if r == SaveChangesDialog.SAVE else []
        if r == SaveChangesDialog.SAVE and not keep:
            return False  # (Save with nothing ticked never discards: only Don't Save does)
        failed = None
        for e, pending in sorted(unsaved, key=lambda u: self._entry_index(u[0])):
            if not any(e is x for x in keep):
                continue
            k = self._entry_index(e)
            if k != self.doc_tabs.currentIndex():
                self.doc_tabs.setCurrentIndex(k)
            try:
                ok = self._apply_pending_windows(pending) and (not e["doc"].dirty or self.save())
            except Exception as ex:  # noqa: BLE001  # swallow-ok: _save_failed says it
                ok = False
                with contextlib.suppress(Exception):
                    self._save_failed(e["doc"].path, ex, offer_save_as=False)
            if not ok:
                failed = e
                break
        if failed is not None:
            self.statusBar().showMessage(
                "Not closed: %s was not saved." % self._tab_title(failed["doc"]), 12000
            )
            return False
        from ..recovery import AutosaveController

        for e, _p in unsaved:
            if not any(e is x for x in keep):
                AutosaveController.forget(e["doc"])
        return True

    @staticmethod
    def _exec_save_changes(dlg) -> int:
        """Show the Save Changes list (a seam: tests answer it)."""
        return dlg.exec_choice()

    def _change_note(self, doc, pending) -> str:
        """What a page's unsaved changes are, for the Save Changes list."""
        undo = getattr(doc, "_undo", None) or []
        if not pending and self._only_auto_changes(doc):
            text = "updated automatically"
        elif len(undo) > 1:
            text = "%d steps" % len(undo)
        else:
            text = "edited" if doc.dirty else ""
        if pending:
            names = ", ".join(n for _d, n in pending)
            text = ("%s + %s not applied" % (text, names)) if text else "%s not applied" % names
        return text

    # -- copy, show, pin --------------------------------------------------------------------
    def copy_tab_path(self) -> None:
        e = self._target_entry()
        if e is None:
            return
        p = os.path.abspath(e["doc"].path)
        QtWidgets.QApplication.clipboard().setText(p)
        from ..notify import notify

        notify(self, "Copied the path of %s." % self._tab_title(e["doc"]), detail=p)

    def copy_tab_name(self) -> None:
        e = self._target_entry()
        if e is None:
            return
        d = e["doc"]
        if isinstance(d, ElementDocument):
            name = d.display_name
        elif getattr(d, "element", ""):
            name = "%s.%s" % (os.path.splitext(os.path.basename(d.path))[0], d.element)
        else:
            name = os.path.splitext(os.path.basename(d.path))[0]
        QtWidgets.QApplication.clipboard().setText(name)
        self.statusBar().showMessage("Copied %s." % name, 4000)

    def reveal_tab(self) -> None:
        """Show in Explorer: the page's folder with the file selected."""
        e = self._target_entry()
        if e is None:
            return
        p = os.path.abspath(e["doc"].path)
        self._reveal_in_explorer(p)

    @staticmethod
    def _reveal_in_explorer(path: str) -> None:
        """Open Explorer on ``path`` with it selected (a seam: tests replace it)."""
        from ..messages import reveal

        reveal(path)

    def show_tab_in_browser(self) -> None:
        """Show in Page Browser: File > Open's browser with this page (element) selected."""
        e = self._target_entry()
        if e is None:
            return
        from ..browser import PageBrowserDialog

        d = e["doc"]
        element = d.element_name if isinstance(d, ElementDocument) else (getattr(d, "element", "") or None)
        r = PageBrowserDialog.get_open_target(
            self, d.path, before_delete=self._before_delete_or_rename, start_element=element
        )
        if r is None:
            return
        if r.kind == "page":
            self.open_page(r.path)
        elif r.element:
            self._open_element(r.path, r.element)

    def toggle_pin_tab(self) -> None:
        """Pin Tab / Unpin Tab: a pinned tab sits at the left, short, without a close
        button; Close All, Close Others and Close Saved leave it."""
        e = self._target_entry()
        if e is None:
            return
        e["pinned"] = not e.get("pinned")
        self._enforce_pin_order()
        self._refresh_tabs()
        self._update_actions()
        self.statusBar().showMessage(
            "%s %s." % (self._tab_title(e["doc"]), "pinned" if e["pinned"] else "unpinned"), 4000
        )

    def _unpin_tab(self, k: int) -> None:
        """A click on a pinned tab's pin."""
        if 0 <= k < len(self._docs) and self._docs[k].get("pinned"):
            self._tab_target = self._docs[k]
            try:
                self.toggle_pin_tab()
            finally:
                self._tab_target = None

    def _enforce_pin_order(self) -> None:
        """The pinned tabs first (a tab dragged across the pinned edge goes back)."""
        want = [e for e in self._docs if e.get("pinned")] + [e for e in self._docs if not e.get("pinned")]
        if all(a is b for a, b in zip(want, self._docs)):
            return
        for t, e in enumerate(want):
            k = self._entry_index(e)
            if k != t:
                self._switching = True
                try:
                    self.doc_tabs.moveTab(k, t)  # (tabMoved keeps _docs in step)
                finally:
                    self._switching = False
        self._refresh_tabs()

    # -- switching -------------------------------------------------------------------------
    def _text_has_focus(self) -> bool:
        """A text field or the in-place text editor has the keyboard (the tab keys leave it
        alone)."""
        if getattr(self, "view", None) is not None and self.view.text_editing():
            return True
        fw = QtWidgets.QApplication.focusWidget()
        sw = self.__dict__.get("_switcher")
        if fw is None or (sw is not None and (fw is sw or sw.isAncestorOf(fw))):
            return False
        return isinstance(
            fw,
            (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox),
        )

    def _install_tab_keys(self) -> None:
        """Next / Previous Page learn which of their keys was pressed (Ctrl+Tab goes in the
        order the pages were used, Ctrl+PgDown along the tabs)."""
        rec = self.__dict__.get("_tab_key_recorder")
        if rec is None:
            rec = self._tab_key_recorder = ShortcutKeyRecorder(self)
        for a in (self.act_next_page, self.act_prev_page):
            a.installEventFilter(rec)

    def switch_page(self, step: int, via_tab=None) -> None:
        """Window > Next / Previous Page."""
        sw = self.__dict__.get("_switcher")
        if sw is not None and sw.isVisible():
            sw.step(1 if step > 0 else -1)
            return
        act = self.act_next_page if step > 0 else self.act_prev_page
        if via_tab is None:
            via_tab = act.property("ctg_last_key") == "tab"
        act.setProperty("ctg_last_key", None)
        if self._text_has_focus() or len(self._docs) < 2:
            return
        from ..panels.doc_tabs import ctrl_tab_order

        if via_tab and ctrl_tab_order() == "mru":
            order = self._mru_order()
            held = bool(QtGui.QGuiApplication.queryKeyboardModifiers() & QtCore.Qt.ControlModifier)
            if held and self.isVisible():
                self._show_switcher(order, 1 if step > 0 else len(order) - 1)
                return
            target = order[1 if step > 0 else -1]
        else:
            k = (self.doc_tabs.currentIndex() + (1 if step > 0 else -1)) % len(self._docs)
            target = self._docs[k]
        self.show_tab(self._entry_index(target))

    def _mru_order(self) -> list:
        """The tabs, the one shown first, then in the order they were last shown (the
        never-shown ones last, in tab order)."""
        mru = [e for e in self.__dict__.get("_mru_entries", ()) if any(e is d for d in self._docs)]
        rest = [e for e in self._docs if not any(e is m for m in mru)]
        cur = self._target_entry()
        out = mru + rest
        if cur is not None and out and out[0] is not cur:
            out = [cur] + [e for e in out if e is not cur]
        return out

    def _show_switcher(self, order: list, start: int) -> None:
        sw = self.__dict__.get("_switcher")
        if sw is None:
            from ..panels.doc_tabs import TabSwitcher

            sw = self._switcher = TabSwitcher(self)
            sw.chosen.connect(
                lambda e: self.show_tab(self._entry_index(e)) if any(e is d for d in self._docs) else None
            )
            sw.cancelled.connect(lambda: self.view.setFocus(QtCore.Qt.OtherFocusReason))
        rows = [
            (
                e,
                self._tab_title(e["doc"]),
                self._doc_project(e["doc"]),
                self._doc_kind(e["doc"]),
                bool(e["doc"].dirty),
            )
            for e in order
        ]
        sw.open(rows, start)

    def show_tab(self, k: int) -> None:
        """Show tab ``k``; the canvas takes the keyboard."""
        if not 0 <= k < len(self._docs):
            return
        if k != self.doc_tabs.currentIndex():
            self.doc_tabs.setCurrentIndex(k)
        elif self.doc is not self._docs[k]["doc"]:
            self._activate_doc(self._docs[k])
        self.view.setFocus(QtCore.Qt.OtherFocusReason)

    def go_to_tab(self, k: int) -> None:
        """Window > Go to Page > Page n (Alt+Shift+1 to 9)."""
        if not self._text_has_focus():
            self.show_tab(k)

    def tab_rows(self) -> list:
        """(name, project, kind, unsaved, current) per tab (the list at the end of the tab
        bar)."""
        return [
            (
                self._tab_title(e["doc"]),
                self._doc_project(e["doc"]),
                self._doc_kind(e["doc"]),
                bool(e["doc"].dirty),
                e["doc"] is self.doc,
            )
            for e in self._docs
        ]

    def _fill_goto_tab_menu(self) -> None:
        for n, a in enumerate(self.goto_tab_actions):
            if n < len(self._docs):
                a.setText("&%d %s" % (n + 1, self._tab_title(self._docs[n]["doc"])))
            else:
                a.setText("Page %d" % (n + 1))

    # -- reopen a closed tab ----------------------------------------------------------------
    #: the closed tabs File > Reopen Closed Page keeps (per window)
    CLOSED_MAX = 20

    def _push_closed(self, entry: dict, k: int) -> None:
        d = entry["doc"]
        path = getattr(d, "path", "")
        if not path or getattr(d, "is_new", False) or not os.path.exists(path):
            return  # (never saved: nothing to reopen)
        element = d.element_name if isinstance(d, ElementDocument) else getattr(d, "element", "")
        view = self._entry_view(entry)
        stack = self.__dict__.setdefault("_closed_stack", [])
        stack.append(
            {
                "path": os.path.abspath(path),
                "element": element or "",
                "view": view,
                "index": k,
                "pinned": bool(entry.get("pinned")),
            }
        )
        del stack[: -self.CLOSED_MAX]

    def _entry_view(self, entry: dict):
        """(zoom, (x, y) of the view centre) the tab is shown at, or None (never shown)."""
        if entry["doc"] is self.doc and not isinstance(entry["doc"], PendingPage):
            self._stash_view(entry)
        return entry.get("view")

    def reopen_closed_tab(self) -> bool:
        """File > Reopen Closed Page (Ctrl+Shift+T): the page closed last opens again, at
        the zoom and place it had, where its tab was."""
        if self._text_has_focus():
            return False
        stack = self.__dict__.get("_closed_stack") or []
        while stack:
            it = stack.pop()
            if not os.path.exists(it["path"]):
                continue
            before = len(self._docs)
            if it["element"]:
                self._open_element(it["path"], it["element"])
            elif not self.open_page(it["path"]):
                continue
            k = self.doc_tabs.currentIndex()
            e = self._docs[k] if self.doc is not None and 0 <= k < len(self._docs) else None
            if e is None or e["doc"] is not self.doc:
                return False
            if len(self._docs) > before:
                # a new tab: back at its old place, pinned again, at its old view
                if it.get("pinned"):
                    e["pinned"] = True
                want = max(0, min(it.get("index", len(self._docs) - 1), len(self._docs) - 1))
                k = self._entry_index(e)
                if k != want:
                    self._switching = True
                    try:
                        self.doc_tabs.moveTab(k, want)
                    finally:
                        self._switching = False
                self._enforce_pin_order()
                self._refresh_tabs()
                if it.get("view"):
                    doc = e["doc"]
                    z, c = it["view"]
                    # (after the element's own framing, which runs on the next turn)
                    QtCore.QTimer.singleShot(0, self, lambda d=doc, zz=z, cc=c: self._show_view_at(d, zz, cc))
                    e["view_restored"] = True
            self._update_actions()
            return True
        self._update_actions()
        self.statusBar().showMessage("No closed page to reopen.", 4000)
        return False
