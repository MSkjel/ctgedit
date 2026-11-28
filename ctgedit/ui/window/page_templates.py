"""The page's template: update, edit, cut its link; Page Defaults."""

from __future__ import annotations

from ...infra.logsetup import note_exception as _note_exc

from ..names import plural

from ..main_window_base import (
    ElementDocument,
    QtWidgets,
    os,
)
from ..messages import inform, warn


class PageTemplateCommands:
    """The commands about the page's template and the project's page defaults."""

    # ----------------------------------------------------------- page templates and defaults

    def _update_template_here(self) -> None:
        """Re-apply the page's (changed) template to this page only. The template is named
        as its library spells it."""
        from ..names import template_name

        if self.doc is None or isinstance(self.doc, ElementDocument):
            return
        state, link = self.doc.template_status()
        link = template_name(link)
        if state == "none":
            self.statusBar().showMessage("This page has no template.", 5000)
            return
        if state == "unresolved":
            warn(self, "Update Template", "The template %s is not on this project's include path." % link)
            return
        n = self.doc.update_template()
        self._refresh()
        self._update_actions()
        from ..names import plural
        from ..notify import notify, undo_action

        if n:
            # (told over the page too, with Undo)
            notify(
                self,
                "Template %s applied again (%s changed)." % (link, plural(n, "object")),
                "success",
                actions=[undo_action(self)],
                toast=True,
                key="edit:template",
            )
        else:
            notify(self, "The page already matches its template %s." % link, key="edit:template")

    def _page_has_template(self) -> bool:
        """The open PAGE is linked to a template (cached per edit state)."""
        if self.doc is None or isinstance(self.doc, ElementDocument):
            return False
        key = (id(self.doc), len(self.doc._undo), len(self.doc._redo), len(self.doc.parsed.page.objects))
        if getattr(self, "_has_tpl_key", None) != key:
            from ...format import template_place as TP

            try:
                self._has_tpl = bool(TP.template_roots(self.doc.parsed.page.objects))
            except Exception:  # noqa: BLE001
                _note_exc("page_templates._page_has_template")
                self._has_tpl = False
            self._has_tpl_key = key
        return self._has_tpl

    def _edit_page_template(self) -> None:
        """Tools > Edit Page Template: open the page's template (.ctt
        element) in its own tab. Saving it there offers Update Pages."""
        if self.doc is None or isinstance(self.doc, ElementDocument):
            return
        el = self.doc.template_element()
        if el is None:
            _i, ref = self.doc.template_reference()
            if ref is None:
                self.statusBar().showMessage("This page has no template.", 5000)
            else:
                warn(
                    self,
                    "Edit Page Template",
                    "The template %s.%s is not on this project's include path, so it "
                    "cannot be opened." % (ref.library, ref.element),
                )
            return
        self._open_element(*el)

    def _cut_template_link(self) -> None:
        """Edit > Library > Cut Link to Template: the page's template objects
        become ordinary objects of the page, which no longer follows the
        template. Asks first -- the link cannot be restored (only undone)."""
        if self.doc is None or isinstance(self.doc, ElementDocument):
            return
        state, link = self.doc.template_status()
        if state == "none":
            self.statusBar().showMessage("This page is not linked to a template.", 5000)
            return
        if state == "unresolved":
            from ..names import template_name

            warn(
                self,
                "Cut Link to Template",
                "The template %s is not on this project's include path, so its objects "
                "cannot be copied into the page. Include the template's project first." % template_name(link),
            )
            return
        from ..dialog.genie_dialogs import friendly_ref

        from ..messages import confirm

        if not confirm(
            self,
            "Cut Link to Template",
            "Cut the link between this page and its template %s?\n\nThe template's "
            "objects become ordinary objects of this page that you can edit; the page "
            "no longer follows changes to the template. The link cannot be restored "
            "later (Undo can, until the page is closed)." % friendly_ref(link),
            "Cut Link",
        ):
            return
        try:
            n = self.doc.cut_template_link()
        except ValueError as e:
            warn(self, "Cut Link to Template", str(e))
            return
        self._sel = []
        self._refresh()
        self._update_actions()
        self.statusBar().showMessage(
            "The page is no longer linked to %s: %s %s now page objects."
            % (friendly_ref(link), plural(n, "template object"), "is" if n == 1 else "are"),
            8000,
        )

    def _page_defaults_dialog(self) -> None:
        """File > Page Defaults: the template style / resolution / title bar
        and background new pages of the current project start with (stored
        in the project, as CtDraw does)."""
        from ..dialog.dialogs import PageDefaultsDialog

        pdir = self._current_project_dir()
        dlg = PageDefaultsDialog(pdir, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.statusBar().showMessage(
                "New pages of %s now start from the %s style (%s)."
                % (
                    os.path.basename(os.path.normpath(pdir)),
                    dlg.values()["style"],
                    "saved in the project" if dlg.stored_in == "project" else "saved in ctgedit's settings",
                ),
                6000,
            )

    def _update_pages_running(self) -> bool:
        """Update Pages is running (its helpers read the libraries and the composition
        caches): say so; Pack Libraries waits until it has finished."""
        from ..jobs.update_run import UpdatePagesRun

        text = UpdatePagesRun.busy_text("Pack the libraries")
        if not text:
            return False
        inform(self, "Pack Libraries", text)
        return True
