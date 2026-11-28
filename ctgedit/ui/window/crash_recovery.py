"""Crash recovery: the autosaved pages offered back at start."""

from __future__ import annotations

from ...infra.logsetup import note_exception as _note_exc

from ..main_window_base import (
    ElementDocument,
    QtWidgets,
    os,
)


class CrashRecovery:
    """Autosave and the recovery of pages a crash left unsaved."""

    # ------------------------------------------------ crash recovery
    def offer_recovery(self) -> int:
        """Start-up: the recovery copies the last run left behind (it crashed
        or was killed) -- Restore / Discard / Decide Later. Returns the number
        of documents restored."""
        from ...infra import autosave, logsetup
        from ..recovery import RecoveryDialog

        try:
            snaps = autosave.orphans()
        except Exception:  # noqa: BLE001
            _note_exc("crash_recovery.offer_recovery")
            return 0
        if not snaps:
            return 0
        dlg = RecoveryDialog(snaps, self)
        dlg.exec()
        if dlg.choice == RecoveryDialog.DISCARD:
            for s in dlg.selected():
                autosave.remove(s.key)
            logsetup.get("autosave").info("discarded %d recovery copies", len(dlg.selected()))
            return 0
        if dlg.choice != RecoveryDialog.RESTORE:
            return 0
        return self.restore_recovered(dlg.selected())

    def restore_recovered(self, snaps) -> int:
        from ...infra import autosave, logsetup

        n, failed = 0, []
        for s in snaps:
            try:
                doc = autosave.restore_document(s)
            except Exception as e:  # noqa: BLE001 -- reported below, the copy is kept
                _note_exc("crash_recovery.restore_recovered")
                failed.append((s.path, e))
                continue
            # the session may have reopened the saved version already: the
            # recovered one takes over that (unchanged) tab
            tabs = self._tabs_of(
                "element" if isinstance(doc, ElementDocument) else "page", doc.path, s.element
            )
            clean = [k for k in tabs if not self._docs[k]["doc"].dirty]
            if clean:
                entry = self._docs[clean[0]]
                entry["doc"], entry["sel"] = doc, []
                if self.doc_tabs.currentIndex() != clean[0]:
                    self.doc_tabs.setCurrentIndex(clean[0])
                else:
                    self._activate_doc(entry)
            else:
                # (the new tab's hidden objects: its activation; the tab shown keeps its own)
                self.set_document(doc)
            self._locked.clear()
            autosave.remove(s.key)
            n += 1
            logsetup.get("autosave").info("restored the recovery copy of %s (%s)", s.title, s.path)
        self._update_title()
        self._update_actions()
        if n:
            from ..names import plural
            from ..notify import notify

            notify(
                self,
                "Restored %s with %s unsaved changes. Save %s to keep the "
                "changes." % (plural(n, "page"), "its" if n == 1 else "their", "it" if n == 1 else "them"),
                "success",
                key="recovery",
            )
        if failed:
            from ..messages import error_box

            error_box(
                self,
                "Recover Unsaved Changes",
                "These recovery copies could not be restored:\n\n%s"
                % "\n".join("• %s: %s" % (os.path.basename(p), e) for p, e in failed),
                exc=failed[0][1],
                next_steps=("They are kept and offered again the next time ctgedit starts.",),
            )
        return n

    def report_journal_recovery(self, notes) -> None:
        """Start-up: database writes of the last run that did not finish were
        rolled back (format/journal) -- say so (non-blocking)."""
        if not notes:
            return
        lines, texts, pages = [], [], []
        for n in notes:
            pages += [p for p in self._recovery_pages(n) if p not in pages]
            if n.get("text"):
                # (a note of its own: the pages saved without their database rows)
                texts.append(str(n["text"]))
                continue
            files = ", ".join(os.path.basename(p) for p in n.get("restored", ()))
            lines.append("• %s%s" % (n.get("label", "an update"), " (%s restored)" % files if files else ""))
            skipped = n.get("skipped") or ()
            if skipped:
                lines.append(
                    "   left alone, changed by another program since: %s"
                    % ", ".join(os.path.basename(p) for p in skipped)
                )
            failed = n.get("failed") or ()
            if failed:
                lines.append(
                    "   not undone yet, cannot be written now: %s"
                    % ", ".join(os.path.basename(p) for p in failed)
                )
        parts = []
        if lines:
            parts.append(
                "A ctgedit process stopped while it was updating project databases. Those "
                "unfinished updates were undone, so the databases are as they were "
                "before:\n\n%s\n\nThe pages those updates were for are saved, but not "
                "registered in their project yet: save them again, or run Tools > Update "
                "Pages." % "\n".join(lines)
            )
        parts += texts
        # (the box offers the pages it names and the log; shown without blocking:
        # a start-up note)
        from ..messages import ErrorBox

        actions = []
        if pages:
            actions.append(("Open These Pages", lambda ps=tuple(pages): self.open_files(ps)))
        box = ErrorBox(
            self,
            "Project Databases Restored" if lines else "Pages Not Registered Yet",
            "\n\n".join(parts),
            actions=tuple(actions),
            icon=QtWidgets.QMessageBox.Information,
        )
        box.show_modeless()
        self._journal_box = box

    @staticmethod
    def _recovery_pages(note) -> list:
        """The page files a recovery note is about: the ones it names (``pages``), else the
        page of a 'register page <name>' / 'restore the rows of page <name>' journal in the
        folder of the database files it put back, when that page file exists."""
        pages = [str(p) for p in (note.get("pages") or ()) if p]
        if pages:
            return pages
        label = str(note.get("label") or "")
        name = ""
        for head in ("register page ", "restore the rows of page "):
            if label.startswith(head):
                name = label[len(head) :].strip()
        files = list(note.get("restored") or ()) + list(note.get("skipped") or ())
        if not name or not files:
            return []
        path = os.path.join(os.path.dirname(str(files[0])), name + ".ctg")
        return [path] if os.path.isfile(path) else []
