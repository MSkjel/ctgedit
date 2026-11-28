"""Files changed on disk by another program."""

from __future__ import annotations

from ..messages import ask_with_again, inform

from ...infra.logsetup import note_exception as _note_exc

from ...infra.paths import path_key

from ..main_window_base import (
    EditableDocument,
    ElementDocument,
    QtWidgets,
    contextlib,
    editing_mod,
    os,
    ui_state,
)
from ..main_window_base import (
    PendingPage,
)


class DiskChanges:
    """What happens when a page or library open here is changed on disk."""

    # ------------------------------------------------ files changed on disk
    def _watch(self, path: str) -> None:
        with contextlib.suppress(Exception):
            if path and os.path.exists(path) and path not in self._watcher.files():
                self._watcher.addPath(path)

    def _unwatch(self, path: str) -> None:
        key = path_key(path)
        if any(path_key(e["doc"].path) == key for e in self._docs):
            return  # another tab shows it
        with contextlib.suppress(Exception):
            self._watcher.removePath(path)

    def _on_file_changed(self, path: str) -> None:
        self._disk_pending.add(path)
        self._disk_timer.start()

    def _check_disk_changes(self, paths=None) -> None:
        """Notice open documents whose file another program changed."""
        from ..prefs.settings import disk_reload
        from ..editor.notices import disk_change_prompt
        from ..notify import notify
        from ..jobs.update_run import UpdatePagesRun

        if paths is None:
            paths, self._disk_pending = self._disk_pending, set()
        mode = disk_reload()
        if mode == "never":
            return
        dialog = disk_change_prompt() == "dialog"
        # a dialog is open: no question now, and no reload either (the page would change
        # under the dialog and the code waiting for it); a notice can be added at once
        busy = QtWidgets.QApplication.activeModalWidget() is not None or getattr(self, "_disk_asking", False)
        if dialog and busy:
            # not now: a dialog is open
            self._disk_pending |= set(paths)
            self._disk_timer.start(1500)
            return
        inactive = self.isVisible() and not self.isActiveWindow()
        later = set()
        keys = {path_key(p) for p in paths}
        for p in paths:
            self._watch(p)  # replaced files drop out of the watch
        for e in list(self._docs):
            doc = e["doc"]
            if path_key(doc.path) not in keys or isinstance(doc, PendingPage):  # read when first shown
                continue
            if doc is self.__dict__.get("_saving_doc"):
                # (its save is under way, ui/dialog/element_save: what another process wrote meanwhile
                # is replaced by this save, so it is looked at after it)
                later.add(doc.path)
                continue
            name = doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)
            if not os.path.exists(doc.path):
                text = "%s was deleted or moved by another program. Save writes it again." % name
                notify(self, text, "warning", detail=doc.path, key="disk:%s" % doc.path)
                from ..editor import notices

                notices.add(doc, "disk", "warning", text, [("Save", lambda d=doc: self._save_notice_doc(d))])
                continue
            if self._reload_waits(doc):
                # (its reload of what the chain after a genie save wrote waits for the user:
                # the tab takes it then)
                later.add(doc.path)
                continue
            if UpdatePagesRun.users_busy and self._chain_may_write(doc):
                # (the chain after a genie save may be saving this element, or this page:
                # what it stored from the version the tab holds is the tab's own, taken at
                # once with the user's changes kept (library._take_chain_write; composed
                # again on the tab's records at its end, which reads a clean tab again);
                # else its end reads it again here, or keeps the user's change, and it is
                # looked at again after it)
                if (
                    doc.changed_on_disk()
                    and (
                        isinstance(doc, ElementDocument)
                        or (doc.dirty and not self._only_automatic_since_saved(doc))
                    )
                    and self._take_chain_write(doc, look_again=False)
                ):
                    continue
                later.add(doc.path)
                continue
            if (
                getattr(doc, "read_only", False)
                and not isinstance(doc, ElementDocument)
                and (
                    self._libraries_being_saved()
                    or UpdatePagesRun.active is not None
                    or UpdatePagesRun.tabs_following
                )
            ):
                # (a page shown read-only is a closed page for the runs, which update its
                # file: their end reads it again (library._reload_read_only_views); looked
                # at again after it)
                later.add(doc.path)
                continue
            try:
                changed = doc.changed_on_disk()
            except Exception:  # noqa: BLE001
                _note_exc("disk_changes._check_disk_changes")
                changed = False
            stamp = EditableDocument.disk_stamp(doc.path)
            if not changed or ui_state(doc).disk_asked == stamp:
                continue
            if mode == "always" and not doc.dirty:
                if busy or (doc is self.doc and self.view.busy()) or self._pending_windows(doc):
                    # (reloaded once the dialog is closed, or the user has finished on the
                    # page: an in-place edit or arrow-key moves not committed yet would go
                    # with the version thrown away; values typed in a window of the page and
                    # not applied too)
                    later.add(doc.path)
                    continue
                if self.revert_document(doc, ask=False):
                    notify(
                        self,
                        "%s was changed by another program and was reloaded." % name,
                        detail=doc.path,
                        key="disk:%s" % doc.path,
                    )
                continue
            if not dialog:
                doc.ui.disk_asked = stamp
                self._disk_notice(doc)
                continue
            if doc is self.doc and self.view.busy():
                # (asked once the user has finished on the page: an in-place edit or
                # arrow-key moves not committed yet are changes the box did not know of, and
                # its Reload would throw them away)
                later.add(doc.path)
                continue
            if inactive:
                # (the user is in another program: asked when this window is active again)
                later.add(doc.path)
                continue
            doc.ui.disk_asked = stamp
            self._disk_asking = True
            try:
                self._ask_reload(doc)
            finally:
                self._disk_asking = False
        if later:
            self._disk_pending |= later
            self._disk_timer.start(1500)

    def _disk_notice(self, doc) -> None:
        """The page's notice that another program changed its file: Reload / Ignore, or with
        unsaved changes here Reload and Discard Mine / Keep Mine (saving it then asks before
        it overwrites the other program's changes)."""
        from ..editor import notices
        from ..notify import notify

        name = doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)

        def reload(discard: bool = False):
            # (decided at the click: a page edited since the notice came asks before its
            # changes go, revert's own question; only Reload and Discard Mine does not)
            win = notices.window_of(doc, self)
            return None if win.revert_document(doc, ask=not discard) else False

        if doc.dirty:
            text = "%s was changed by another program, and you have unsaved changes to it here." % name
            acts = [("Reload and Discard Mine", lambda: reload(True)), ("Keep Mine", None)]
        else:
            text = "%s was changed by another program." % name
            acts = [("Reload", reload), ("Ignore", None)]
        notices.add(doc, "disk", "warning", text, acts)
        notify(self, text, "warning", detail=doc.path, key="disk:%s" % doc.path)

    def _save_notice_doc(self, doc):
        """A notice's Save: save ``doc`` (shown first). False keeps the notice."""
        win = self
        k = next((i for i, e in enumerate(win._docs) if e["doc"] is doc), None)
        if k is None:
            return False
        if doc is not win.doc:
            win.show_tab(k)
        return None if win.save() else False

    def _ask_reload(self, doc) -> None:
        name = doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)
        if doc.dirty:
            text = "%s was changed by another program, and you have unsaved changes to it here." % name
            detail = (
                "Reload discards your changes and shows the version on disk. Keep "
                "Mine leaves your version open; saving it then asks before it "
                "overwrites the other program's changes."
            )
            ok_label = "Reload (Discard Mine)"
        else:
            text = "%s was changed by another program." % name
            detail = "Reload it to see that version?"
            ok_label = "Reload"
        # (Don't ask again: remembered as Settings > Pages and libraries, reload it / do
        # nothing; a page with unsaved changes is always asked about)
        choice, again = ask_with_again(
            self,
            "Changed on Disk",
            text,
            [ok_label],
            "Keep Mine" if doc.dirty else "Not Now",
            detail=detail,
            default=None if doc.dirty else ok_label,
            again=not doc.dirty,
            exec_=self._exec_disk_prompt,
        )
        if again:
            from ..prefs.settings import set_disk_reload

            set_disk_reload("always" if choice else "never")
        if choice:
            self.revert_document(doc, ask=False)

    @staticmethod
    def _exec_disk_prompt(box) -> None:
        """Show the Changed on Disk question (a seam: tests answer it)."""
        box.exec()

    def revert_document(self, doc=None, ask: bool = True) -> bool:
        """File > Revert to Saved."""
        if doc is None:
            self._show_target_tab()
        doc = doc or self.doc
        if doc is None:
            return False
        k = next((i for i, e in enumerate(self._docs) if e["doc"] is doc), None)
        if k is None:
            return False
        name = doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)
        is_new = isinstance(doc, ElementDocument) and doc.is_new
        if is_new or not os.path.exists(doc.path):
            inform(
                self,
                "Revert to Saved",
                "%s has never been saved, so there is no saved version to go back to." % name,
            )
            return False
        if ask and doc is self.doc:
            # (an in-place edit or arrow-key moves not committed yet are changes the question
            # asks about: committed later, they would go into the version thrown away)
            self._flush_text_edit()
        if (
            ask
            and doc.dirty
            and not self._confirm(
                "Revert to Saved",
                "Discard your unsaved changes to %s?" % name,
                "Discard and Reload",
                "The page is reloaded from the file on disk. This cannot be undone.",
            )
        ):
            return False
        try:
            if isinstance(doc, ElementDocument):
                new = ElementDocument.open_element(doc.path, doc.element_name)
            else:
                # (the open checks follow: a remembered genie update is applied first)
                new = self._open_page_file(doc.path, checks=True)
        except Exception as e:  # noqa: BLE001
            from ..messages import error_box

            error_box(
                self,
                "Revert to Saved",
                self._open_error_text(doc.path, e),
                exc=e,
                next_steps=("Your version is still open here; the file on disk was not read.",),
                actions=(("Show in Explorer", lambda p=doc.path: self._reveal_in_explorer(p)),),
            )
            return False
        if ui_state(doc).supergenie_view and new.supergenie_link() is not None:
            # a page created from a super genie that was opened read-only
            # (SuperGeniePageDialog) stays read-only when it is reloaded
            new.read_only = True
            new.ui.supergenie_view = True
        from ..recovery import AutosaveController

        AutosaveController.forget(doc)
        if doc is self.doc:
            self._stash_current()  # keep the zoom / scroll
        # its windows show the version thrown away
        self._close_page_windows(doc)
        entry = self._docs[k]
        entry["doc"] = new
        # (the underlay stays on the page: ui/editor/underlay.py)
        self._underlay_doc_replaced(doc, new)
        entry["sel"] = []
        # hidden / locked roots are record indices of the OLD version (the
        # file on disk may hold a different page now)
        entry.pop("hidden", None)
        entry.pop("locked", None)
        page = not isinstance(new, ElementDocument)
        if page:
            # (the open checks below compose its genies, as after an open)
            entry["genie_gen"] = editing_mod.LIBRARY_GENERATION[0]
        else:
            # (its first show re-derives its symbols and composes its nested genies, as an
            # element's open does: now when it is shown, else when its tab is shown)
            entry.pop("genie_gen", None)
        if doc is self.doc:
            self._activate_doc(entry)
        self._update_title()
        self._update_actions()
        from ..notify import notify

        # (over the page too when the user asked for it: the page they look at changed)
        notify(
            self,
            "Reloaded %s from disk." % name,
            "success",
            detail=doc.path,
            toast=ask and new is self.doc,
            key="revert:%s" % doc.path,
        )
        from ...infra import logsetup

        logsetup.get("open").info("reverted %s to the version on disk (%s)", name, doc.path)
        if page:
            # the checks a page's open runs (its damage notice, then the template, the
            # symbols, the genies ... that changed since it was saved, each an undoable
            # automatic step), once the canvas has painted it
            self._opened(new, new.path, "view" if ui_state(new).supergenie_view else "edit")
        elif getattr(new, "damage", None):
            self._damage_notice(new)
        return True
