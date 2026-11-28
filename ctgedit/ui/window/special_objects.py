"""Composite and super genies, copy to library, special objects and the legacy tools."""

from __future__ import annotations

from ...infra.paths import path_key

from ..main_window_base import (
    ElementDocument,
    QtCore,
    QtWidgets,
    contextlib,
    os,
)
from ..messages import inform, warn


class SpecialObjects:
    """Placing composite genies, super genies, special objects and legacy objects; Copy to Library."""

    # --------------------------------------------- paste composite genie
    def _paste_composite_genie(self) -> None:
        """Edit > Paste Composite Genie…: the templates of the page's project
        and the projects it includes with their options; after OK a click on
        the page composes the chosen one with those options and places it
        (editing.paste_composite_genie). Not in a genie or symbol window."""
        if self.doc is None:
            return
        from ...editing import COMPOSITE_NOT_HERE

        if not self.doc.composites_allowed:
            self.statusBar().showMessage(COMPOSITE_NOT_HERE, 6000)
            return
        from ..dialog.composite_dialogs import PasteCompositeGenieDialog

        templates = self._composite_templates()
        if not templates:
            self.statusBar().showMessage(
                "No composite genie templates in this project or the projects it includes.", 6000
            )
            return
        comps = self._composite_completions()
        dlg = PasteCompositeGenieDialog(
            templates,
            {},
            self,
            completions=comps,
            advice=self._composite_advice(comps["Equipment"]),
            project_dir=self._doc_project_dir(),
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        guid, params = dlg.selection()
        if guid is None:
            return
        self._arm_composite_placement(guid, templates[guid].name, params)

    def _arm_composite_placement(self, guid: str, name: str = "", params: dict | None = None) -> None:
        """The next click on the page places composite genie ``guid`` there
        with the options ``params`` (its top-left at the click, like Paste
        Genie)."""
        self._set_tool("symbol")
        self._pending_symbol = None
        self._pending_composite = guid
        self._pending_composite_params = dict(params or {})
        self.statusBar().showMessage(
            "Click on the page to place the composite genie %s (Esc cancels)." % (name or guid), 8000
        )

    def _place_composite(self, guid: str, pos, params: dict | None = None) -> None:
        """Compose composite template ``guid`` with the options ``params``
        into the project's composition cache and place it with its top-left
        at ``pos`` (page coordinates)."""
        if params is None:
            params = getattr(self, "_pending_composite_params", None) or {}
        self._pending_composite_params = {}
        # (the composition runs here: the busy note says so once it takes a moment)
        try:
            with self._busy("Composing the composite genie…"):
                idx = self.doc.paste_composite_genie(guid, (int(pos[0]), int(pos[1])), params)
        except Exception as e:  # noqa: BLE001 -- a refusal is explained, never raised
            inform(self, "Paste Composite Genie", str(e))
            return
        self._after_composite_write()
        self._sel = [idx]
        self._refresh()
        self.statusBar().showMessage(
            "Composite genie placed (composed into the project's composition cache).", 7000
        )

    # ------------------------------------------------- attach super genies
    def _edit_genie_form(self) -> None:
        """Edit > Library > Genie Form…: the form of the genie open in its element window,
        or of the one placed genie selected."""
        doc = self.doc
        if doc is None:
            return
        if isinstance(doc, ElementDocument):
            if getattr(doc, "kind", "") != "genie":
                self.statusBar().showMessage("A genie form belongs to a genie.", 5000)
                return
            self._open_genie_form(doc.path, doc.element_name)
            return
        if len(self._sel) != 1:
            self.statusBar().showMessage("Select one placed genie first.", 4000)
            return
        root = self.doc.root_of(self._sel[0])
        link = doc.genie_link(root)
        path = doc.library_path(link[0], link[1], "ctm") if link is not None else None
        if not path:
            self.statusBar().showMessage("The genie's library was not found.", 6000)
            return
        self._open_genie_form(
            path,
            link[1],
            project_dir=os.path.dirname(os.path.abspath(doc.path)),
            list_values=doc.form_list_values,
        )

    def _open_genie_form(self, lib_path: str, element: str, project_dir: str = "", list_values=None) -> None:
        """The genie form editor of ``element`` in the library at ``lib_path`` (one window per
        genie: an open one comes to the front)."""
        from ..dialog.genie_form_editor import GenieFormEditor
        from ...format import genie_place as GP

        key = (path_key(lib_path), element.lower())
        wins = self.__dict__.setdefault("_genie_forms", {})
        w = wins.get(key)
        if w is not None:
            try:
                w.showNormal()
                w.raise_()
                w.activateWindow()
                return
            except RuntimeError:  # (closed and deleted)
                wins.pop(key, None)
        try:
            params = GP.declared_params_of(lib_path, element)
        except Exception:  # noqa: BLE001 -- a genie that does not load: no parameter list
            from ...infra import logsetup

            logsetup.note_exception("window._open_genie_form/params", page=lib_path)
            params = {}
        library = os.path.splitext(os.path.basename(lib_path))[0]
        if list_values is None:
            from ...io import project as PR

            pdir = project_dir or os.path.dirname(os.path.abspath(lib_path))

            def list_values(fld, d=pdir):
                if fld is None or not getattr(fld, "list_form", ""):
                    return None
                try:
                    return PR.list_values(d, fld.list_form, fld.list_field, fld.list_filter)
                except Exception:  # noqa: BLE001  # swallow-ok: a plain text box then
                    return None

        w = GenieFormEditor(
            lib_path, library, element, params, project_dir=project_dir, list_values=list_values, parent=self
        )
        w.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        w.destroyed.connect(lambda _o=None, k=key: wins.pop(k, None))
        w.saved.connect(lambda p: self.statusBar().showMessage("Saved the genie form in %s" % p, 6000))
        wins[key] = w
        w.show()

    def _attach_super_genies(self) -> None:
        """Edit > Attach Super Genies…: CtDraw offers it with the GENIE open in
        its element window (the list is saved with the genie); with a placed
        genie selected on a page, ctgedit opens its element, sets the list
        and saves the element in one go (after asking)."""
        if self.doc is None:
            return
        from ..dialog.supergenie_dialogs import AttachSuperGeniesDialog

        doc = self.doc
        in_element = isinstance(doc, ElementDocument)
        if in_element:
            if not doc.can_attach_super_genies:
                self.statusBar().showMessage("Super genies can only be attached to a genie.", 5000)
                return
            attachments = doc.attachments()
            genie = doc.display_name
            idx = None
        else:
            if len(self._sel) != 1:
                self.statusBar().showMessage("Select one placed genie first.", 4000)
                return
            idx = self._sel[0]
            attachments = doc.genie_attachments(idx)
            if attachments is None:
                is_super = False
                with contextlib.suppress(Exception):
                    from ...format.symbols import reference_from_object

                    ref = reference_from_object(doc.parsed.page.objects[idx])
                    is_super = ref is not None and "@super" in ref.element.lower()
                self.statusBar().showMessage(
                    "A super genie cannot have super genies attached. Select a genie."
                    if is_super
                    else "The selection is not a genie from a library.",
                    5000,
                )
                return
            target = doc._genie_template_target(idx)
            genie = "%s.%s" % (os.path.splitext(os.path.basename(target[0]))[0], target[1]) if target else ""
        dlg = AttachSuperGeniesDialog(
            attachments,
            self,
            genie=genie,
            project_dir=os.path.dirname(os.path.abspath(doc.path)),
            problem=doc.super_genie_problem,
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        new = dlg.names()
        if new == attachments:
            return
        try:
            if in_element:
                done = doc.set_attachments(new)
                if done:
                    self._update_title()
                    self.statusBar().showMessage(
                        "Attached super genies changed. Save the genie to write them into %s."
                        % os.path.basename(doc.path),
                        6000,
                    )
                return
            from ..messages import confirm

            if not confirm(
                self,
                "Attach Super Genies",
                "Save the new list into the genie %s now?\n\nThe genie's library is "
                "rewritten (like saving the genie in its own window): every page that "
                "places it opens the new list." % genie,
                "Save to Genie",
                safe_default=False,
            ):
                return
            if doc.set_genie_attachments(idx, new, page_thumbnail=self._page_thumbnail_for):
                self._after_library_write(target[0] if target else doc.path)
                self._library_cache_notice(getattr(doc, "save_warning", ""))
                self.statusBar().showMessage(
                    "Attached super genies of %s saved (%d)." % (genie, len(new)), 6000
                )
                self._report_super_pages(getattr(doc, "super_pages", None) or [])
        except (ValueError, OSError) as e:
            warn(self, "Attach Super Genies", str(e))

    # ------------------------------------------------------ copy to library
    def _copy_to_library(self) -> None:
        """Edit > Copy to Library...: selection -> named library element."""
        if self.doc is None or not self._sel:
            self.statusBar().showMessage("Select the objects to copy first.", 4000)
            return
        from ..dialog.library_dialogs import CopyToLibraryDialog

        dlg = CopyToLibraryDialog(
            os.path.dirname(os.path.abspath(self.doc.path)),
            self,
            exts=("ctl", "ctm"),
            **self._library_dialog_hooks(),
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        lib, elem = dlg.values()
        if not lib or not elem:
            return
        if not self._confirm_shipped_template(self.doc, lib, False):
            return
        thumb = b""
        with contextlib.suppress(Exception):
            sel = self.doc.expand_to_subtrees(self._sel)
            box = self.doc.selection_extent(sel, bounds=True)
            if box is not None:
                w, h = box[2] - box[0], box[3] - box[1]
                s = min(200.0 / w, 200.0 / h, 1.0)
                size = (
                    (w, h)
                    if s >= 1.0
                    else ((200, max(1, int(h * s))) if w >= h else (max(1, int(w * s)), 200))
                )
                thumb = self._render_region((box[0], box[1], w, h), size, set(sel))
        # (a copy the new element brings up to date is drawn from the element as stored)
        from ...render.headless_thumb import element_thumbnail

        try:
            ok = self.doc.copy_to_library(
                self._sel, lib, elem, thumbnail=thumb, thumbnail_for=element_thumbnail
            )
        except Exception as e:  # noqa: BLE001
            warn(self, "Copy to Library", str(e))
            return
        if ok:
            self.statusBar().showMessage("Copied selection to %s.%s" % (os.path.basename(lib), elem), 5000)
            self._after_library_write(lib)
            self._library_cache_notice(getattr(self.doc, "library_warning", ""))

    # ------------------------------------------ special objects (trend, web, ActiveX)
    #: the size a CLICK (no drag) with a special-object tool places
    _ACTIVEX_SIZES = {
        "activex": (160, 160),
        "process_analyst": (570, 320),
        "scheduler": (420, 300),
        "web_content": (480, 360),
        "trend": (301, 151),
        "db_exchange": (400, 250),
        "pelco": (320, 240),
    }

    def _place_special(self, tool: str, rect, click: bool = False) -> "int | None":
        """Place a special object, then open its sheet."""
        if self.doc is None:
            return None
        l, t = min(rect[0], rect[2]), min(rect[1], rect[3])
        r, b = max(rect[0], rect[2]), max(rect[1], rect[3])
        donor = None
        kind = tool
        if tool == "activex":
            choice = self._pending_control or ("kind", "activex")
            if choice[0] == "donor":
                donor = choice[1]
            else:
                kind = choice[1]
        if click:
            if donor is not None and donor.size[0] > 1 and donor.size[1] > 1:
                w, h = donor.size
            else:
                w, h = self._ACTIVEX_SIZES.get(kind, (200, 160))
            r, b = l + w, t + h
        if tool == "trend":
            # CtDraw's Trend tool: the box minus its last pixel --
            # a drag N pixels wide = N samples at one pixel per sample
            idx = self.doc.create_object("trend", (l, t, max(l + 1, r - 1), max(t + 1, b - 1)))
        elif tool == "cicode_object":
            # the click is the object's point; its f(x) icon sits above-right
            # of it (the point = the icon's bottom-left)
            from ...format.legacy import RECT2_DELTAS

            d = RECT2_DELTAS[0x1009]
            idx = self.doc.create_object("cicode_object", (l + d[0], t + d[1], l + d[2], t + d[3]))
        elif donor is not None:
            idx = self.doc.create_control(donor.path, donor.index, (l, t, r, b), donor.clsid)
        else:
            idx = self.doc.create_object(kind, (l, t, r, b))
        self._sel = [idx]
        self._refresh()
        # CtDraw opens the new object's sheet (not only with 'Open properties
        # after drawing'), to be filled in: it takes the keyboard
        QtCore.QTimer.singleShot(0, self, lambda i=idx: self.object_properties(i))
        return idx

    def _insert_activex_dialog(self) -> None:
        """Edit > Insert ActiveX Control: pick the control (the ones ctgedit
        carries + every control the project's pages use), then drag its box."""
        if self.doc is None:
            return
        from ..dialog.activex_insert import InsertActiveXDialog

        pdir = os.path.dirname(os.path.abspath(self.doc.path)) if self.doc.path else None
        dlg = InsertActiveXDialog(pdir, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted or dlg.choice() is None:
            return
        self._set_tool("activex")
        self._pending_control = dlg.choice()
        self.statusBar().showMessage(
            "Drag the box of the %s on the page (a click places it at its default size)."
            % dlg.choice_label(),
            8000,
        )

    # -------------------------------------------------------- legacy tools
    _LEGACY_SIZES = {
        "trend": (480, 280),
        "string": (140, 22),
        "cicode_object": (48, 48),
        "animation_keyboard": (32, 32),
        "pagewide_keyboard": (32, 32),
        "animated_symbol": (64, 64),
        "bar_graph": (40, 160),
        "slider": (40, 160),
        "digital_text": (140, 22),
        "color_flood": (80, 80),
    }

    def _create_legacy(self, kind: str) -> None:
        """Tools > V3/V4 Objects: create a legacy record at the page centre."""
        if self.doc is None:
            return
        w, h = self._LEGACY_SIZES.get(kind, (64, 64))
        pw = self.doc.page.appearance.width or 1024
        ph = self.doc.page.appearance.height or 768
        x, y = max(0, (pw - w) // 2), max(0, (ph - h) // 2)
        try:
            idx = self.doc.create_object(kind, (x, y, x + w, y + h), None)
        except Exception as e:  # noqa: BLE001
            warn(self, "Create Failed", str(e))
            return
        self._sel = [idx]
        self._refresh()
        if kind == "animated_symbol":
            # its states are the point of the object: pick them right away
            QtCore.QTimer.singleShot(0, self, lambda i=idx: self.symbol_set_properties(i))
            return
        self.statusBar().showMessage("Created %s. Open its properties to set it up." % kind, 6000)
