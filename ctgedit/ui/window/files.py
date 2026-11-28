"""Open, new, save, delete and rename pages and library elements."""

from __future__ import annotations

from ...infra.paths import folder_key, path_key

from ..messages import ask_with_again, warn

from ...infra.logsetup import note_exception as _note_exc


from ..prefs.settings import option

from ..main_window_base import (
    EditableDocument,
    ElementDocument,
    QtCore,
    QtGui,
    QtWidgets,
    app_settings,
    contextlib,
    os,
    ui_state,
)

#: a new template's page with no page open and no named size in the Page Defaults (never
#: set, 'Default' or 'User')
NEW_TEMPLATE_SIZE = (1920, 1080)


def new_template_size(lib: str) -> tuple:
    """The page size of a new template made with no page open."""
    from ...format.template_place import RESOLUTIONS
    from ..dialog.dialogs import page_defaults

    d = page_defaults(os.path.dirname(os.path.abspath(lib)), resolve_default=False)
    if d.get("source") == "settings" and not app_settings().contains("pagedefaults/resolution"):
        return NEW_TEMPLATE_SIZE
    res = str(d.get("resolution") or "").lower()
    for name, w, h in RESOLUTIONS:
        if name.lower() == res:
            return (w, h)
    return NEW_TEMPLATE_SIZE


#: the key of an element tab's nested genies in its genie_sources_done (_update_stale_nested)
NESTED_DONE = ("", "<nested>")


class PageFiles:
    """MainWindow's open / new / save of pages and library elements, deleting and renaming them,
    and passing a saved element on to the pages and genies that use it."""

    # ------------------------------------------------ a new page from the last template
    def last_template(self):
        """(style, element) of the template File > New used last for a page, or None."""
        raw = str(app_settings().value("pagebrowser/new/template", "") or "")
        parts = raw.split("|")
        if len(parts) != 4 or not parts[0] or not parts[1]:
            return None
        return parts[0], parts[1]

    def new_page_from_last_template(self) -> bool:
        """File > New Page from the Last Template (the '+' button's menu): a new page in this
        project ('NewPage' or Settings > Pages and libraries 'Name new pages:', or the first
        free 'NewPage_2' ...) from the template File > New
        used last, linked as it was then, without the New window."""
        lt = self.last_template()
        if lt is None:
            self.statusBar().showMessage(
                "Create a page with File > New first: its template is used next time.", 6000
            )
            return False
        from ..browser.naming import unique_page_name

        pdir = self._current_project_dir()
        try:
            existing = [os.path.splitext(f)[0] for f in os.listdir(pdir) if f.lower().endswith(".ctg")]
        except OSError:
            existing = []
        # a new page is on disk only after its first Save: the open tabs' names are taken too
        existing += self.open_page_names(pdir)
        from ..prefs.window_options import new_page_name

        name = unique_page_name(new_page_name(), existing)
        linked = bool(app_settings().value("newpage/linked", True, type=bool))
        before = len(self._docs)
        self._create_page("%s.%s.<ctt" % lt, linked, os.path.join(pdir, name + ".ctg"))
        return len(self._docs) > before

    def open_page_names(self, pdir: str) -> list:
        """The names of the pages of folder ``pdir`` open in a tab of any editor window (saved
        or not)."""
        key = path_key(pdir)
        out = []
        for w in [self] + [w for w in list(type(self).instances) if w is not self]:
            try:
                docs = [e["doc"] for e in w._docs]
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            for d in docs:
                p = getattr(d, "path", "") or ""
                if self._tab_element(d) or not p.lower().endswith(".ctg"):
                    continue
                if folder_key(p) == key:
                    out.append(os.path.splitext(os.path.basename(p))[0])
        return out

    def _page_open_in_tab(self, path: str) -> bool:
        """Page ``path`` is open in a tab of an editor window (a new page not saved yet too)."""
        name = os.path.splitext(os.path.basename(path))[0].lower()
        return name in {n.lower() for n in self.open_page_names(os.path.dirname(os.path.abspath(path)))}

    def _current_project_dir(self) -> str:
        """The project new pages / templates are listed for: the open page's
        (or the open library element's), else the last used folder."""
        if self.doc is not None:
            return os.path.dirname(os.path.abspath(self.doc.path))
        return self._dialog_dir("open")

    def _new_page(self) -> None:
        """File > New > Page: the New window on its Page tab."""
        self.new_blank_dialog("page")

    def _create_page(self, ref: str, linked: bool, new_path: str) -> None:
        """A new page from template ``ref`` at ``new_path`` (linked to it or not). It opens in
        a new tab; nothing is written until its first Save."""
        from ..dialog.dialogs import page_defaults

        if not ref or not new_path:
            return
        if os.path.exists(new_path):
            warn(
                self, "New Page", "%s already exists. Choose another page name." % os.path.basename(new_path)
            )
            return
        if self._page_open_in_tab(new_path):
            # (a new page not saved yet: two tabs of one file, the second save would
            # overwrite the first)
            warn(
                self,
                "New Page",
                "A page named %s is already open in a tab. Choose another "
                "page name." % os.path.splitext(os.path.basename(new_path))[0],
            )
            return
        # a template without its own background: the Page Defaults colour of
        # the project the page is created in (CtDraw: every new page)
        target_dir = os.path.dirname(os.path.abspath(new_path))
        bg = page_defaults(target_dir)["background"]
        try:
            doc = EditableDocument.new_from_template(
                ref, new_path, linked=linked, background=(bg, bg), register=False, write=False
            )
        except KeyError:
            from ..dialog.genie_dialogs import friendly_ref

            warn(
                self,
                "New Page",
                "Project %s cannot use template %s: the template is not in that project "
                "or the projects it includes."
                % (os.path.basename(os.path.dirname(os.path.abspath(new_path))), friendly_ref(ref)),
            )
            return
        except Exception as e:  # noqa: BLE001
            from ..messages import error_box

            error_box(
                self,
                "New Page Failed",
                "The page could not be made: %s" % self._error_text(e),
                exc=e,
                next_steps=(
                    "Choose another template, or check that the template opens in the Library panel.",
                ),
                icon=QtWidgets.QMessageBox.Critical,
            )
            return
        self.set_document(doc)
        self._locked.clear()
        self._remember_dir("save", new_path)
        # nothing is written until the first Save (CtDraw's new page too); that save writes
        # the page, its runtime files and its project rows
        self._update_title()
        self._update_actions()
        from ..dialog.genie_dialogs import friendly_ref

        name = os.path.splitext(os.path.basename(new_path))[0]
        self.statusBar().showMessage(
            "New page %s from template %s%s. Save it to add it to its project."
            % (name, friendly_ref(ref), "" if linked else " (unlinked)"),
            10000,
        )

    def _create_library_element(self, kind: str, lib: str, elem: str) -> None:
        """An EMPTY element ``elem`` of ``lib`` opens in its own tab; draw it, then Save writes
        it into the library (an element needs at least one object)."""
        if not lib or not elem:
            return
        page_size = None
        if kind in ("template", "supergenie"):
            pb = self.doc.parsed.page if self.doc is not None else None
            page_size = (
                (pb.page_width, pb.page_height)
                if (pb and kind == "template")
                else new_template_size(lib)
                if kind == "template"
                else (400, 300)
            )
        try:
            doc = ElementDocument.new_element(lib, elem, kind, page_size=page_size)
        except Exception as e:  # noqa: BLE001
            from ..messages import error_box

            error_box(
                self,
                "New",
                "%s.%s could not be made: %s"
                % (os.path.splitext(os.path.basename(lib))[0], elem, self._error_text(e)),
                exc=e,
                next_steps=("Choose another name, or check that the library can be written to.",),
                actions=(("Show in Explorer", lambda p=lib: self._reveal_in_explorer(p)),),
            )
            return
        # (a new element hides nothing: its tab's activation; the tab shown keeps its own)
        self.set_document(doc)
        self._locked.clear()
        self.statusBar().showMessage(
            "New %s %s. Draw it, then save it into %s."
            % ({"supergenie": "super genie"}.get(kind, kind), doc.display_name, os.path.basename(lib)),
            8000,
        )

    def _render_region(self, src: tuple, size: tuple, indices=None):
        """Render scene region ``src`` (x, y, w, h) into a ``size`` TH
        thumbnail blob (format/thumbnail.encode)."""
        from ...format import thumbnail as _thumb

        tw, th = size
        img = QtGui.QImage(tw, th, QtGui.QImage.Format_RGB32)
        img.fill(QtCore.Qt.white)
        painter = QtGui.QPainter(img)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        try:
            with self.scene.export_only(indices):
                self.scene.render(painter, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(*src))
        finally:
            painter.end()
        bits = bytes(memoryview(img.constBits()))[: tw * th * 4]
        return _thumb.encode(bits, tw, th)

    def _element_thumbnail(self):
        """The TH thumbnail of the open ELEMENT (its content, longest side
        200 -- elements are not page-scaled). An element whose tab is not shown (Save All
        saves it without showing it) is drawn in a scratch scene."""
        geo = self.doc.thumbnail_geometry()
        if geo is None:
            return None
        if self.doc.page is not self._page:
            return self._scratch_region(self.doc, geo[0], geo[1])
        return self._render_region(geo[0], geo[1])

    @staticmethod
    def _scratch_region(doc, src: tuple, size: tuple):
        """:meth:`_render_region` of a document that is not the one shown: its page in a
        scratch scene (as _page_thumbnail_for draws another page's thumbnail)."""
        from ...format import thumbnail as _thumb
        from ...render.scene import PageScene

        if doc.page is None:
            return None
        tw, th = size
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
                    scene.render(painter, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(*src))
            finally:
                painter.end()
            bits = bytes(memoryview(img.constBits()))[: tw * th * 4]
            return _thumb.encode(bits, tw, th)
        finally:
            scene.clear()
            scene.deleteLater()

    def _save_document(self, doc, errors) -> bool:
        """Save ``doc`` (a tab of this window) in place as :meth:`_save_to` does, without
        showing its tab (File > Save All: the tab shown stays as it is; the thumbnail of a
        page or element not shown is drawn in a scratch scene). ``errors`` collects what
        could not be done. Never raises."""
        name = doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)
        shown, self.doc = self.doc, doc
        swapped = doc is not shown
        # (the save's own refreshes (an element's save refreshes the window) would draw the
        # swapped document on the canvas: held while the swap lasts, and the tab shown is
        # drawn once afterwards, since its genies may have been composed again)
        own_hold = swapped and self.__dict__.get("_refresh_hold") is None
        if own_hold:
            self._refresh_hold = []
        try:
            if self._version_refused(doc, errors):
                return False
            if doc.read_only and EditableDocument.file_read_only(doc.path):
                errors.append("%s is read-only. Use Save As on its tab to save a copy." % name)
                return False
            return self._save_to(errors=errors)
        except Exception as ex:  # noqa: BLE001 -- keep saving the others
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.save_document", page=doc.path)
            errors.append("%s could not be saved: %s" % (name, self._error_text(ex)))
            return False
        finally:
            self.doc = shown
            if own_hold:
                self.__dict__.pop("_refresh_hold", None)
            if swapped and shown is not None:
                with contextlib.suppress(RuntimeError):
                    self._refresh()
                    self._update_actions()

    def _render_thumbnail(self) -> None:
        """Regenerate the page's embedded thumbnail from the current content
        (CtDraw does on save; a stale one mis-previews the page in every
        page browser)."""
        from ...format import thumbnail as _thumb

        if isinstance(self.doc, ElementDocument):
            return
        pb = self.doc.parsed.page
        pw = self.doc.page.appearance.width or getattr(pb, "page_width", 0)
        ph = self.doc.page.appearance.height or getattr(pb, "page_height", 0)
        tw, th = _thumb.thumb_size(pw, ph)
        if not tw:
            return
        if self.doc.page is not self._page:
            # (a page whose tab is not shown, Save All: drawn in a scratch scene)
            thumb = self._page_thumbnail_for(self.doc)
            if thumb is not None:
                pb.thumbnail = thumb
            return
        img = QtGui.QImage(tw, th, QtGui.QImage.Format_RGB32)
        img.fill(QtCore.Qt.white)
        painter = QtGui.QPainter(img)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        try:
            with self.scene.export_only(None):
                self.scene.render(painter, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(0, 0, pw, ph))
        finally:
            painter.end()
        bits = bytes(memoryview(img.constBits()))[: tw * th * 4]
        pb.thumbnail = _thumb.encode(bits, tw, th)

    def _save_element_to(self, path=None, name=None, errors=None) -> bool:
        """Save the open library ELEMENT into its library (or, Save As, into
        ``path`` under ``name``). Never raises; ``errors`` as in _save_to."""
        doc = self.doc
        in_place = path is None and name is None
        if in_place and self._version_refused(doc, errors):
            return False
        if not self._confirm_shipped_template(doc, path or doc.path, in_place):
            return False
        # (a block the chain after a genie save stored from the block this tab holds is the
        # tab's own: no question, library._take_chain_write)
        if in_place and doc.changed_on_disk() and not self._take_chain_write(doc, look_again=False):
            from ..messages import confirm

            if not confirm(
                self,
                "Element Changed on Disk",
                "%s was changed in its library since it was opened here.\n\nOverwrite "
                "it with this version?" % doc.display_name,
                "Overwrite",
            ):
                if errors is not None:
                    errors.append(
                        "%s was not saved: it changed in its library since it "
                        "was opened, and you chose not to overwrite it." % doc.display_name
                    )
                return False
        thumb = None
        with contextlib.suppress(Exception):
            thumb = self._element_thumbnail()
        # (a copy the save brings up to date is drawn from the element as stored)
        from ...render.headless_thumb import element_thumbnail
        from ..dialog import element_save

        # (a large element is normalised in the window's helper while the window paints and
        # says what it does: ui/dialog/element_save; the automatic updates of this tab wait for the
        # save meanwhile, _genie_may_land)
        self._saving_doc = doc
        try:
            with self._busy("Saving %s…" % doc.display_name):
                doc.save(
                    path,
                    name,
                    thumbnail=thumb,
                    page_thumbnail=self._page_thumbnail_for,
                    thumbnail_for=element_thumbnail,
                    prepare=element_save.prepare_for(self),
                )
        except Exception as e:  # noqa: BLE001 -- a refused save must never escape
            _note_exc("files._save_element_to")
            return self._save_failed(path or doc.path, e, errors, offer_save_as=in_place)
        finally:
            self._saving_doc = None
        from ..recovery import AutosaveController

        AutosaveController.forget(doc)
        jobs = getattr(self, "_genie_jobs", None)
        if jobs is not None and in_place:
            # (its save composed the genies nested in it: an update that has not landed
            # would only compose them again, from the block the tab no longer holds)
            with contextlib.suppress(Exception):
                if jobs.cancel_automatic(doc, templates=False):
                    self._genies_updated(doc)
                    self._clear_auto_update_line(doc, templates=False)
        with contextlib.suppress(Exception):
            # (its save composed the genies nested in it: the tab does not compose them
            # again until a file they are made of changes, _update_stale_nested)
            ui_state(doc).genie_sources_done[NESTED_DONE] = doc.nested_genie_sources()
        self._watch(doc.path)  # a replace drops the watch
        self._after_library_write(doc.path)
        self._library_cache_notice(getattr(doc, "save_warning", ""))
        self._propagate_element(doc)
        self._refresh()
        # a genie save creates the missing pages of its super genies (CtDraw)
        self._report_super_pages(getattr(doc, "super_pages", None) or [])
        if (doc.path or "").lower().endswith(".ctt"):
            self._template_saved_prompt(doc)
        elif getattr(doc, "kind", "") == "supergenie":
            self._supergenie_saved_prompt(doc)
        elif getattr(doc, "kind", "") in ("symbol", "genie") and getattr(doc, "save_files", None):
            # the pages and genies that use it, directly or through other genies
            self._users_saved_prompt(doc)
        return True

    def _confirm_shipped_template(self, doc, target: str, in_place: bool) -> bool:
        """Confirm before saving into a library that ships with Plant SCADA."""
        from ...format.template_place import shipped_project

        if not (target or "").lower().endswith((".ctt", ".ctl", ".ctm")):
            return True
        proj = shipped_project(target)
        if proj is None or ui_state(doc).shipped_ok or not option("warn_shipped_template"):
            return True
        what = {".ctt": "template", ".ctl": "symbol", ".ctm": "genie"}[os.path.splitext(target)[1].lower()]
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Standard %s Library" % what.title())
        box.setText(
            "%s is a %s library supplied with Plant SCADA (project %s)."
            % (os.path.basename(target), what, proj)
        )
        box.setInformativeText(
            "Saving here changes the pages of every project that includes %s, and a "
            "Plant SCADA update or reinstall replaces the file. Save a copy into your "
            "own project instead?" % proj
        )
        copy_btn = box.addButton("Save a Copy…", QtWidgets.QMessageBox.AcceptRole) if in_place else None
        anyway = box.addButton("Save Here Anyway", QtWidgets.QMessageBox.DestructiveRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is anyway:
            doc.ui.shipped_ok = True
            return True
        if copy_btn is not None and clicked is copy_btn:
            self._save_element_as()
        return False

    def _template_saved_prompt(self, doc) -> None:
        """After a template save, ask about updating what uses it."""
        from ..dialog.library_dialogs import shipped_library

        if getattr(doc, "save_files", None):
            # (the open pages take the saved template now, also when nothing closed needs
            # an update)
            self._follow_saved_template("template", doc.display_name)
        if not shipped_library(doc.path):
            if getattr(doc, "save_files", None):
                self._users_saved_prompt(doc)
            return
        if not option("save_template_warning"):
            return
        choice, again = ask_with_again(
            self,
            "Template Saved",
            "The pages that use template %s still show its old version." % doc.display_name,
            ["Update Pages…"],
            "Later",
            detail="Run Update Pages now to update them?",
            icon="information",
            exec_=self._exec_prompt,
        )
        if again:
            app_settings().setValue("opt/save_template_warning", False)
        if choice:
            # (the chain box ticked for this run, as the question of a template of the
            # user's own: the templates built on it are saved first)
            self._update_pages(chain=True)

    def _supergenie_saved_prompt(self, doc) -> None:
        """After a super genie save, ask about updating its pages."""
        from .open_tabs import page_tab

        n_open = 0
        changed = []
        for w in type(self).instances or [self]:
            with contextlib.suppress(RuntimeError):  # a window being destroyed
                w._open_pending_placing(doc.element_name)
            for e in list(getattr(w, "_docs", ())):
                d = e["doc"]
                # (a read-only tab, an earlier version or a page opened to view, is left: its
                # page is a closed one for Update Pages)
                if d is doc or not page_tab(d):
                    continue
                with contextlib.suppress(Exception):
                    # a page created from THIS element: the same name in the
                    # library file the page resolves (another library may hold an
                    # element of the same name)
                    where = d.supergenie_element()
                    if (
                        where
                        and where[1].lower() == doc.element_name.lower()
                        and path_key(where[0]) == path_key(doc.path)
                        and d.supergenie_status()[0] == "stale"
                        and d.update_supergenie_page()
                    ):
                        n_open += 1
                        changed.append((w, d))
                        w._mark_modeless_reason([d], "replace")
        # (a page shown in another window is drawn again now, with its title)
        self._follow_tabs_changed(changed)
        if n_open:
            self._update_title()
            from ..names import plural
            from ..notify import notify

            notify(
                self,
                "Updated %s from super genie %s. Save them to keep the change."
                % (plural(n_open, "open page"), doc.display_name),
                "success",
            )
        if not option("save_supergenie_warning"):
            return
        choice, again = ask_with_again(
            self,
            "Super Genie Saved",
            "The pages created from super genie %s still show its old version." % doc.display_name,
            ["Update Pages…"],
            "Later",
            detail="Run Update Pages now to update them?",
            icon="information",
            exec_=self._exec_prompt,
        )
        if again:
            app_settings().setValue("opt/save_supergenie_warning", False)
        if choice:
            self._update_pages()

    @staticmethod
    def _exec_prompt(box) -> None:
        """Show a modal question (a seam: tests answer it)."""
        box.exec()


#: the files a drop from Explorer opens: pages, and libraries (their elements in the browser)
PAGE_SUFFIXES = (".ctg",)
LIBRARY_SUFFIXES = (".ctl", ".ctm", ".ctt", ".ctc")
