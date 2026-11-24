"""In-place text, edit intents and node editing."""

from __future__ import annotations

from ..main_window_base import (  # noqa: F401
    CLIPBOARD_MIME,
    PASTE_POINT_UNREAD,
    QtCore,
    QtGui,
    QtWidgets,
    app_settings,
    contextlib,
    menu_just_closed,
    option,
    os,
    paste_linked,
    TransformError,
)
from ..main_window_base import (  # noqa: F401
    _SPECIAL_TOOLS,
)
from ..editor.canvas_look import signed as _signed
from ..prefs.settings import nudge_burst_ms

_Ev = QtCore.QEvent
_Key = QtCore.Qt.Key


class _NudgeBurstFilter(QtCore.QObject):
    """Installed on the application while an arrow-key burst waits to reach the page
    (CanvasEdits.nudge): the burst is committed before any other input is handled (a
    click, a wheel turn, a drop, another key or shortcut, an arrow key outside the
    canvas) and when the arrow key is let go."""

    #: the keys that move the selection on the canvas (MainWindow._MOVE_KEYS)
    MOVE_KEYS = frozenset(
        {
            _Key.Key_Left,
            _Key.Key_Right,
            _Key.Key_Up,
            _Key.Key_Down,
            _Key.Key_Home,
            _Key.Key_End,
            _Key.Key_PageUp,
            _Key.Key_PageDown,
        }
    )
    #: held with an arrow key, they do not end a burst
    MODIFIERS = frozenset(
        {
            _Key.Key_Shift,
            _Key.Key_Control,
            _Key.Key_Alt,
            _Key.Key_Meta,
            _Key.Key_AltGr,
            _Key.Key_CapsLock,
            _Key.Key_NumLock,
        }
    )
    INPUT = frozenset(
        {
            _Ev.Type.MouseButtonPress,
            _Ev.Type.MouseButtonDblClick,
            _Ev.Type.Wheel,
            _Ev.Type.Drop,
            _Ev.Type.ContextMenu,
            _Ev.Type.TabletPress,
        }
    )
    KEYS = frozenset({_Ev.Type.KeyPress, _Ev.Type.ShortcutOverride})

    def __init__(self, win):
        super().__init__(win)
        self._win = win
        self.installed = False

    def eventFilter(self, obj, ev):  # noqa: N802 (Qt API)
        t = ev.type()
        if t in self.INPUT:
            self._win.commit_nudge()
        elif t in self.KEYS:
            k = ev.key()
            if k not in self.MODIFIERS and not (k in self.MOVE_KEYS and self._on_canvas()):
                self._win.commit_nudge()
        elif t == _Ev.Type.KeyRelease:
            if ev.key() in self.MOVE_KEYS and not ev.isAutoRepeat():
                self._win.commit_nudge()
        return False

    def _on_canvas(self) -> bool:
        """The keyboard is on the canvas (where the arrow keys move the selection)."""
        view = getattr(self._win, "view", None)
        fw = QtWidgets.QApplication.focusWidget()
        return view is not None and fw is not None and (fw is view or view.isAncestorOf(fw))


class CanvasEdits:
    """MainWindow's in-place text commits, edit intents and node editing (starting an
    in-place edit is window/text_edit.TextEditMixin)."""

    # (typed line breaks reach the page CRLF, as CtDraw stores them:
    # editing.crlf_text in set_text / create_object)
    def _on_text_edit_finished(self, target: dict, text: str) -> None:
        idx = self._apply_text_edit(target, text)
        if idx is None:
            return
        self._refresh()
        if target.get("kind") == "place" and option("props_on_new"):
            QtCore.QTimer.singleShot(0, self, lambda i=idx: self._props_after_new(i))

    def _apply_text_edit(self, target: dict, text: str):
        """Store an in-place edit WITHOUT refreshing: the edited / created
        index, or None when nothing changed."""
        from ...editing import UnencodableTextError

        doc = self.doc
        if doc is None or target.get("doc", doc) is not doc:
            return None
        try:
            if target.get("kind") == "place":
                if not text.strip():
                    self.statusBar().showMessage("No text was typed, so nothing was placed.", 4000)
                    return None
                x, y = target.get("pos", (0, 0))
                from ..editor import draw_styles as DS

                dw, dh = DS.click_size("text")  # the Text tool's click size
                idx = self._create_styled(
                    "text", (x, y, x + dw, y + dh), None, colour=self._draw_colour, text=text
                )
                self._sel = [idx]
                return idx
            if text == target.get("orig"):
                return None
            po = target.get("po")
            objs = doc.parsed.page.objects
            i = target.get("index", -1)
            if not (0 <= i < len(objs) and objs[i] is po):
                i = next((k for k, o in enumerate(objs) if o is po), None)
            if i is None:
                self.statusBar().showMessage(
                    "The text you were editing no longer exists; the typed text was not stored.", 6000
                )
                return None
            if not doc.set_text(i, text):
                self.statusBar().showMessage(
                    "This object cannot take a caption; the text was not stored.", 6000
                )
                return None
            return i
        except UnencodableTextError as exc:
            QtWidgets.QMessageBox.warning(
                self, "Text Not Stored", "%s. Replace those characters and type the text again." % exc
            )
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Text Not Stored", str(exc))
        return None

    def _flush_text_edit(self) -> bool:
        """Store an open in-place edit before the page is rebuilt, saved or
        switched away from (without a refresh of its own). True when it
        changed the page. Arrow-key moves not on the page yet go first (with a
        refresh of their own: :meth:`commit_nudge`)."""
        self.commit_nudge()
        view = getattr(self, "view", None)
        if view is None or not view.text_editing():
            return False
        taken = view.take_text_edit()
        if taken is None:
            return False
        done = self._apply_text_edit(*taken) is not None
        # (a placed text keeps the Text tool with Settings 'Keep the tool after drawing or
        # placing an object')
        if (
            taken[0].get("kind") == "place"
            and view.tool() == "text"
            and not (done and getattr(self, "_keep_tool", False))
        ):
            self._set_tool("select")
        return done

    def _drawable_members(self, index: int) -> list:
        """The record's direct children minus its Touch / Keyboard records."""
        from ...format import inputs

        objs = self.doc.page.objects
        return [
            m
            for m in objs[index].members
            if m < len(objs) and objs[m].raw.get("_type_code") not in inputs.INPUT_TCS
        ]

    def _genie_object_forms(self, index: int) -> None:
        """Open the property sheets for the objects that make up this genie."""
        from ...model.document import ObjectKind

        members = list(reversed(self._drawable_members(index)))
        if not members:
            self.object_properties(index)
            return
        while members:
            member = members.pop()
            if self.doc.page.objects[member].kind == ObjectKind.GROUP:
                children = self._drawable_members(member)
                if children:
                    members.extend(reversed(children))
                    continue
            self.object_properties(member, activate=not members)

    def edit_input(self, index: int) -> None:
        """The Properties dialog of the object, on its Input tab."""
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return
        self.object_properties(index)
        dlg = self._modeless_for("objprops", index)
        inp = getattr(dlg, "_input", None)
        if dlg is not None and inp is not None:
            dlg._tabs.setCurrentWidget(inp)

    def edit_object_page(self, index: int, page_id: str) -> None:
        """The Properties dialog of the object, on one of its pages (an
        objprops registry id: 'move_rot', 'scale_h', ...)."""
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return
        self.object_properties(index)
        dlg = self._modeless_for("objprops", index)
        show = getattr(dlg, "show_page", None)
        if show is not None:
            show(page_id)

    def _props_after_new(self, index: int) -> None:
        """Settings > Display properties on new."""
        prev = getattr(self, "_new_sheet", None)
        geom = None
        try:
            if prev is not None and prev.isVisible():
                pending = getattr(prev, "has_pending_edits", None)
                if pending is not None and pending():
                    prev = None  # (the user is working in it: it stays)
                else:
                    geom = prev.geometry()
                    prev.reject()
        except RuntimeError:  # (closed meanwhile)
            prev = None
        self.object_properties(index, activate=False)
        dlg = self._modeless_for("objprops", index)
        if dlg is not None:
            self._new_sheet = dlg
            if geom is not None:
                dlg.setGeometry(geom)

    def object_properties(
        self, index: int | None = None, generic: bool = False, activate: bool = True
    ) -> None:
        """Open the property sheet for the selection."""
        if self.doc is None:
            return
        if index is None:
            if not self._sel:
                return
            index = self._sel[0]
        from ..objprops import ObjectPropertiesDialog

        # CtDraw shows a genie member's / a template record's sheet READ-ONLY;
        # a template record is editable while templates are unlocked
        reason = self._read_only_reason(index)
        if (
            not generic
            and not reason
            and (self.doc.is_symbol_set(index) or self.doc.is_animated_symbol(index))
        ):
            self.symbol_set_properties(index)
            return
        if self._raise_modeless(self._modeless_for("objprops", index)):
            return  # one window per object
        defn = self._owner_definition(index) if reason else None
        dlg = ObjectPropertiesDialog(self.doc, index, self, read_only=reason, definition=defn)
        if defn is not None:

            def edit_definition(d=defn, sheet=dlg):
                sheet.reject()
                self._open_element(*d)

            dlg.editDefinitionRequested.connect(edit_definition)
        # every Apply / OK / Convert that changed the page refreshes the canvas
        # at once (the ``applied`` signal: _show_modeless); OK needs no second
        # refresh below

        def apply(d):
            if d.changed:
                self.statusBar().showMessage("Updated object properties", 4000)

        def cancelled():
            # Apply, or an OK that kept the sheet open because a field could
            # not be stored, has already changed the page: Cancel must still
            # show those changes (a sheet closed by the window, its object
            # gone, is already inside a refresh)
            if not getattr(dlg, "_ctg_dropped", False):
                apply(dlg)

        if self._show_modeless(dlg, ("objprops", index), apply, scope="object", activate=activate) is dlg:
            dlg.rejected.connect(cancelled)

    def _raise_modeless(self, dlg) -> bool:
        """Bring an open modeless window to the front (shown again when its
        page tab hid it). False when there is none."""
        if dlg is None:
            return False
        try:
            if dlg.isHidden():
                dlg._ctg_tab_hidden = False
                dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        except RuntimeError:
            return False
        return True

    def symbol_set_properties(self, index: int):
        """The Symbol Set sheet (type, expressions, a symbol per state) of a
        symbol set or V3/V4 Animated Symbol (modeless). Returns the dialog."""
        if self.doc is None:
            return None
        existing = self._modeless_for("symset", index)
        if self._raise_modeless(existing):
            return existing
        from ..dialog.symbol_set_dialog import SymbolSetDialog

        dlg = SymbolSetDialog(self.doc, index, self)

        def applied():
            self._refresh()
            self.statusBar().showMessage("Updated the symbol set", 4000)

        return self._show_modeless(dlg, ("symset", index), scope="object", on_applied=applied)

    def _edit_genie(self, root: int, obj) -> None:
        if self._raise_modeless(self._modeless_for("genie", root)):
            return  # one window per genie
        with self._busy("Opening the genie's parameters…"):
            self._edit_genie_open(root, obj)

    def _edit_template_inputs(self, root: int, obj, tpl_ref) -> None:
        """The page template's inputs in the genie parameter form (one undo step on OK)."""
        display = self.doc.template_inputs()
        if not display:
            QtWidgets.QMessageBox.information(
                self, "Template Inputs", "Template %s.%s has no inputs." % (tpl_ref.library, tpl_ref.element)
            )
            return
        from ..dialog.genie_dialogs import GenieSubstitutionDialog

        dlg = GenieSubstitutionDialog(
            obj,
            self._tag_completion(),
            self,
            bindings=display,
            ref="%s.%s" % (tpl_ref.library, tpl_ref.element),
        )
        dlg.setWindowTitle("Template Inputs - %s.%s" % (tpl_ref.library, tpl_ref.element))
        dlg._ctg_loaded = dict(display)

        def apply(d):
            values = d.changed_values(getattr(d, "_ctg_loaded", display))
            if not values:
                return
            with self._busy("Applying the template's inputs…"):
                ok = self.doc.set_template_inputs(values)
            if ok:
                self._refresh()
                self.statusBar().showMessage("Updated the template's inputs", 4000)

        self._show_modeless(
            dlg, ("tplinputs", root), apply, scope="object", reload=self._reload_template_inputs
        )

    def _reload_template_inputs(self, dlg, _index: int) -> bool:
        """Template Inputs re-reads the template's inputs (the fields the user has not
        changed; the template applied again from its library, an Undo); False when the
        window cannot (it closed on a Replace or an Undo, with what was typed)."""
        fn = getattr(dlg, "set_values", None)
        if fn is None or self.doc is None:
            return False
        display = self.doc.template_inputs()
        if not display:
            return False
        fn(display)
        dlg._ctg_loaded = dict(display)
        return True

    def _edit_genie_open(self, root: int, obj) -> None:
        bindings = self.doc.genie_bindings(root)
        tpl_i, tpl_ref = self.doc.template_reference()
        if tpl_i is not None and self.doc.root_of(root) == tpl_i:
            # A page TEMPLATE's inputs: edited like a genie's parameters, and applied the
            # way CtDraw's template dialog does -- the template's copy composed again with
            # the new values (EditableDocument.set_template_inputs; a bag-only edit is what
            # the Graphics Builder throws away on its next save)
            self._edit_template_inputs(root, obj, tpl_ref)
            return
        # a composite genie gets CtDraw's typed 'Presentation Options' form
        # (generated from the template XML) with the instance's CURRENT options
        if self._edit_composite(root, obj, bindings):
            return
        if app_settings().value("opt/disable_genie_forms", False, type=bool):
            self._genie_object_forms(root)
            return
        # show EVERY template-declared parameter (CtDraw does), the instance's
        # values overlaid; fall back to just the instance bindings when the
        # template library isn't on the search path
        display = self.doc.genie_all_params(root)
        # the genie's FORM from its library's .frm (CtDraw's genie dialog,
        # format/genieform.py); None = the default form (the parameters)
        form = self.doc.genie_form(root)
        if not display and form is None:
            # a genie that declares no parameters: CtDraw's double-click opens
            # its 'Genie Properties' object sheet (AN / name / tooltip /
            # security), not an empty parameter form (live-verified)
            self.object_properties(root)
            return
        link = self.doc.genie_link(root)
        problems = (
            self.doc.genie_form_problems(link[0], link[1]) if (form is None and link is not None) else []
        )
        from ..dialog.genie_dialogs import GenieSubstitutionDialog

        dlg = GenieSubstitutionDialog(
            obj,
            self._tag_completion(),
            self,
            bindings=display,
            form=form,
            list_values=self.doc.form_list_values,
            an=self.doc.genie_an(root),
            group_button=app_settings().value("opt/genie_group_button", False, type=bool),
            problems=problems,
            ref="%s.%s" % link[:2] if link is not None else "",
            pickers=self._genie_pickers(root),
            uses=self.doc.genie_param_uses(root),
        )
        dlg.groupRequested.connect(
            lambda d=dlg, r=root: self._genie_object_forms(getattr(d, "_ctg_index", r))
        )
        dlg._ctg_loaded = dict(display)

        def apply(d):
            # CtDraw's OK: every changed parameter in ONE re-composition of
            # the instance (one undo step), compared with what the window
            # loaded; on the genie where it is NOW (the window follows it)
            r = self._modeless_target_index(d, root)
            if r is None:
                self.statusBar().showMessage(
                    "The page changed while the parameters were open, so nothing was applied.", 8000
                )
                return
            values = d.changed_values(getattr(d, "_ctg_loaded", display))
            if not values:
                return
            big = len(self.doc.subtree(r)) > 400
            if big and QtCore.QThread.currentThread().loopLevel() > 0 and not self.doc.is_composite_genie(r):
                # a big genie re-composes in the worker (seconds): the window stays usable
                link = self.doc.genie_link(r)
                name = "%s.%s" % link[:2] if link is not None else "the genie"
                self._genie_job_runner().submit_bindings(self.doc, r, values, name)
                self.statusBar().showMessage("Applying the parameters of %s (you can go on working)…" % name)
                return
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                ok = self.doc.set_genie_bindings(r, values, dialog=True)
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            if ok:
                self._refresh()
                self.statusBar().showMessage("Updated genie parameters", 4000)

        self._show_modeless(dlg, ("genie", root), apply, scope="object", reload=self._reload_genie_dialog)

    def _modeless_target_index(self, dlg, fallback: int):
        """The record index an object window's OK / Apply acts on now: where
        its record is (the window follows it), None when it is gone."""
        if self.doc is None or getattr(dlg, "_ctg_doc", self.doc) is not self.doc:
            return None
        objs = self.doc.parsed.page.objects
        target = getattr(dlg, "_ctg_target", None)
        i = getattr(dlg, "_ctg_index", fallback)
        if target is None:
            return i if 0 <= i < len(objs) else None
        if 0 <= i < len(objs) and objs[i] is target:
            return i
        return next((k for k, po in enumerate(objs) if po is target), None)

    def _reload_genie_dialog(self, dlg, index: int) -> bool:
        """Genie Parameters re-reads the genie's parameters (the fields the
        user has not changed); False when the window cannot."""
        fn = getattr(dlg, "set_values", None)
        if fn is None or self.doc is None:
            return False
        params = self.doc.genie_all_params(index)
        fn(params)
        dlg._ctg_loaded = dict(params)
        return True

    def _composite_templates(self) -> dict:
        """The Visual Templates of the open page's project and the projects it
        includes (format/composites.load_templates; each file is re-read only
        when it changed)."""
        from ...format import composites

        return composites.load_templates(self.doc.path) if self.doc is not None else {}

    @staticmethod
    def _composite_ref(obj) -> str:
        """The composition-cache link of a placed composite genie ('' for any
        other object). The link as stored when the cache element did not
        resolve (the reader sets ``_resolved_ref`` only for a cache it could
        expand) -- its Instances\\<hash>.xml may still be there."""
        from ...format import composites

        for ref in (obj.raw.get("_resolved_ref"), obj.raw.get("_ref"), obj.genie_template):
            if ref and composites.instance_ref(str(ref)) is not None:
                return str(ref)
        return ""

    def _composite_info_for(self, obj):
        """The template, instance options and cache folder of a placed
        composite genie (composites.CompositeInfo), or None."""
        from ...format import composites

        ref = self._composite_ref(obj)
        if not ref:
            return None
        return composites.composite_info(self.doc.path, ref, self._composite_templates())

    def _composite_completions(self) -> dict:
        """Completion for the composite parameters with naming rules: the
        equipment names and the shared tag completion model of the page's tag
        scope (its project, the projects it includes and the projects compiled
        with it)."""
        out = {"Equipment": [], "Tag": []}
        if self.doc is None:
            return out
        pdir = os.path.dirname(os.path.abspath(self.doc.path))
        with contextlib.suppress(Exception):
            from ...io.project import equipment

            out["Equipment"] = [r.get("NAME", "") for r in equipment(pdir) if r.get("NAME")]
        with contextlib.suppress(Exception):
            out["Tag"] = self._tag_completion()
        return out

    def _tag_completion(self):
        """The tag completion model of the open page's scope (one per scope, shared
        by every field and dialog); its index is read in the background while the
        dialog is read (a large project scope takes seconds)."""
        from ..objprops.expression import _warm_tags, completion_model_for

        pdir = self._doc_project_dir()
        if not pdir:
            return None
        _warm_tags(pdir)
        return completion_model_for(pdir)

    def _doc_project_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.doc.path)) if self.doc is not None else ""

    @staticmethod
    def _composite_advice(equipment_names):
        """A non-blocking note for an equipment-named composite parameter whose
        value the scope's equipment list (``equipment_names``) does not have."""
        names = {n.lower() for n in equipment_names}

        def advice(p, value):
            if p.naming_rule != "Equipment" or not names or not isinstance(value, str):
                return ""
            v = value.strip()
            if not v or "?" in v or v.lower() in names:
                return ""
            return "No equipment named '%s' in this project or the projects compiled with it." % v

        return advice

    def _edit_composite(self, root: int, obj, bindings=None) -> bool:
        """Edit Composite Genie."""
        if not self._composite_ref(obj):
            return False
        info = self._composite_info_for(obj)
        if info is None or info.template is None:
            QtWidgets.QMessageBox.information(
                self,
                "Presentation Options",
                "\n\n".join(
                    (info.notes if info is not None else [])
                    + ["The options cannot be shown without the template."]
                ),
            )
            return True
        current = (
            info.values()
            if info.instance is not None
            else (bindings if bindings is not None else self.doc.genie_bindings(root))
        )
        reason = self._composite_read_only_reason(root, info)
        notes = list(info.notes)
        if not reason:
            try:
                status = self.doc.composite_status(root)
            except Exception:  # noqa: BLE001 -- the status is advice only
                status = "unknown"
            if status in ("stale", "missing"):
                notes.insert(
                    0,
                    "The template or a genie it uses changed since this composite "
                    "genie was made. OK makes it again with the current template.",
                )
        from ..dialog.composite_dialogs import PresentationOptionsDialog

        comps = self._composite_completions()
        dlg = PresentationOptionsDialog(
            info.template,
            current,
            self,
            read_only=bool(reason),
            note=reason,
            instance=info.hash,
            cache_dir=info.cache_dir,
            notes=notes,
            completions=comps,
            advice=self._composite_advice(comps["Equipment"]),
            project_dir=self._doc_project_dir(),
            index=root,
            options_now=lambda: self._composite_options_now(dlg, root),
        )
        self._show_modeless(
            dlg,
            ("genie", root),
            scope="object",
            title="Presentation Options",
            on_accept=lambda d, r=root: self._apply_composite_options(getattr(d, "_ctg_index", r), d),
        )
        return True

    def _composite_options_now(self, dlg, fallback: int):
        """The options the composite of an open Presentation Options has now
        (its reload): its instance options, else its bindings; None when the
        genie is gone or its options cannot be read."""
        i = self._modeless_target_index(dlg, getattr(dlg, "index", fallback))
        if i is None or self.doc is None or self.doc.page is None or not 0 <= i < len(self.doc.page.objects):
            return None
        info = self._composite_info_for(self.doc.page.objects[i])
        if info is None or info.template is None:
            return None
        return info.values() if info.instance is not None else self.doc.genie_bindings(i)

    def _composite_read_only_reason(self, root: int, info) -> str:
        """Why a placed composite's options cannot be changed here ('' when
        they can)."""
        if info.template_source != "project":
            return (
                "The template of this composite genie is not in the Composite Genies "
                "folder of this project or the projects it includes, so its options "
                "cannot be changed."
            )
        if getattr(info.template, "problems", None):
            return (
                "The template file %s has problems, so the options cannot be changed. "
                "Fix the XML file first. The problems are listed below." % info.template.path
            )
        if not self.doc.composites_allowed:
            from ...editing import COMPOSITE_NOT_HERE

            return COMPOSITE_NOT_HERE
        if self._filter_template([root], quiet=True) != [root]:
            return (
                "This composite genie is part of the page template or locked, so its "
                "options cannot be changed here."
            )
        objs = self.doc.parsed.page.objects
        sem = self.doc.page.objects if self.doc.page is not None else []
        par = sem[root].parent if root < len(sem) else None
        while par is not None:
            if objs[par].subtype_size & 0x200:
                return "This composite genie is part of another genie. Change it in that genie's definition."
            par = sem[par].parent
        return ""

    def _apply_composite_options(self, root: int, dlg) -> None:
        """OK of an editable Presentation Options: compose + relink."""
        objs = self.doc.parsed.page.objects if self.doc is not None else []
        target = getattr(dlg, "_ctg_target", None)
        if self.doc is None or (target is not None and (root >= len(objs) or objs[root] is not target)):
            self.statusBar().showMessage(
                "The page changed while the options were open, so nothing was applied.", 8000
            )
            return
        # (the composition runs here: the busy note says so once it takes a moment)
        try:
            with self._busy("Composing the composite genie…"):
                changed = self.doc.set_composite_options(root, dlg.values())
        except Exception as e:  # noqa: BLE001 -- a refusal is explained, never raised
            QtWidgets.QMessageBox.warning(self, "Presentation Options", str(e))
            return
        self._after_composite_write()
        self._sel = [root]
        self._refresh()
        from ..notify import notify, undo_action

        if changed:
            # (the relink is told over the page too, with Undo)
            notify(
                self,
                "Composite genie composed with the new options.",
                "success",
                actions=[undo_action(self)],
                toast=True,
                key="edit:composite",
            )
        else:
            notify(self, "The composite genie already had these options.", key="edit:composite")

    def _after_composite_write(self) -> None:
        """A composition cache was written: other documents re-resolve it."""
        files = list(getattr(self.doc, "composite_files", []) or [])
        if files:
            self.doc.composite_files = []
            with contextlib.suppress(Exception):
                self._after_library_write(*files)

    def _update_composite(self, root: int) -> None:
        """Context menu > Update Composite Genie: re-compose one placed
        composite with the newest template and content genies (its options
        kept). Not for one the Presentation Options could not change either
        (inside another genie, locked, part of the template)."""
        if self.doc is None:
            return
        info = self.doc.composite_info(root)
        reason = self._composite_read_only_reason(root, info) if info is not None else ""
        if reason and info.template_source == "project":
            QtWidgets.QMessageBox.information(self, "Update Composite Genie", reason)
            return
        # (the composition runs here: the busy note says so once it takes a moment)
        try:
            with self._busy("Composing the composite genie…"):
                changed = self.doc.update_composite(root)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Update Composite Genie", str(e))
            return
        self._after_composite_write()
        self._refresh()
        self.statusBar().showMessage(
            "Composite genie updated with the newest template"
            if changed
            else "The composite genie is up to date",
            7000,
        )

    def _on_dock_selection(self, indices: list, reveal: bool = True) -> None:
        # the tree is the precise selector: select exactly the clicked node(s)
        # (a nested genie selects the genie, not its outer group), so the
        # property panel follows the tree and genie params become editable.
        # (in the order the rows were selected: Rename Objects' 'Selection order')
        seen = set()
        new = [i for i in indices if not (i in seen or seen.add(i))]
        # (a double-click on the canvas comes here too: what it selected is in view)
        from_canvas = sorted(self.view.selection_roots()) == sorted(new)
        self._sel = new
        self.view.set_selection(self._sel)
        self.props.set_target(self.doc, self._sel)
        self._update_sel_label()
        self._update_actions()
        self._queue_context_update("tree")
        self._note_selection_history(self._sel)
        # (``reveal`` False: the caller moves the view itself, Goto Object frames the
        # object, and Previous View goes back to the view before it)
        if reveal and not from_canvas:
            self._reveal_selection()

    def _reveal_selection(self) -> None:
        """Settings > Canvas 'Scroll to objects selected in a panel': a selection made in a
        panel (the Objects tree, Goto Object, Find, Select Same ...) is scrolled into view,
        the zoom kept."""
        if option("reveal_selection"):
            self.view.reveal_selection()

    # ------------------------------------------------------------- selecting
    def _unit_label(self, root: int) -> str:
        """'Genie AN 80': a unit's kind and AN, as the status bar names it."""
        if self.doc is None or not 0 <= root < len(self.doc.page.objects):
            return "Object"
        from ..names import kind_name

        o = self.doc.page.objects[root]
        return "%s AN %d" % (kind_name(o), o.an) if o.an > 0 else kind_name(o)

    def _unit_row_text(self, root: int) -> str:
        """'Genie AN 80 · energydemo.valve_left': the label and what the unit shows (its
        library element, its text or its name), as the Objects panel's row reads."""
        o = self.doc.page.objects[root]
        what = self._element_key(o, keep_case=True) or " ".join((o.style.text or "").split())[:24] or o.cls
        return "%s · %s" % (self._unit_label(root), what) if what else self._unit_label(root)

    def _select_pool(self) -> list:
        """The page's top-level units a click could select now: not locked, not the page
        template, not hidden in the editor, as the ribbon's Filter allows."""
        if self.doc is None:
            return []
        page = self.doc.page
        ok = self.view.kind_filter()
        hidden = self._hidden_roots
        out = []
        for i in page.roots():
            o = page.objects[i]
            if (
                o.raw.get("_non_drawable")
                or o.raw.get("_from_template")
                or self._unit_locked(i)
                or i in hidden
            ):
                continue
            if str(o.raw.get("_ref") or "").lower().endswith("<ctt"):
                continue  # (the template's own root)
            if ok is not None and not ok(i):
                continue
            out.append(i)
        return out

    def _filter_note(self) -> str:
        """' (Filter)' while the ribbon's Filter limits what can be selected."""
        sf = getattr(self, "sel_filter", None)
        return " (Filter)" if sf is not None and sf.currentIndex() > 0 else ""

    def select_all(self) -> None:
        """Edit > Select All: every top-level unit the Filter lets through (a group / genie
        is one unit), not the locked ones."""
        if self.doc is None:
            return
        self.commit_nudge()
        roots = self._select_pool()
        self._set_selection(roots)
        n = len(roots)
        self.statusBar().showMessage(
            "Selected %d object%s%s." % (n, "" if n == 1 else "s", self._filter_note()), 4000
        )

    def deselect_all(self) -> None:
        """Edit > Select > Deselect All."""
        if self.doc is not None:
            self.view.clear_node_selection()
            self._set_selection([])

    def invert_selection(self) -> None:
        """Edit > Select > Invert Selection: every unit Select All would take but the
        selected ones."""
        if self.doc is None:
            return
        n = len(self.doc.page.objects)
        have = {self.doc.root_of(i) for i in self._sel if 0 <= i < n}
        roots = [r for r in self._select_pool() if r not in have]
        self._set_selection(roots)
        self.statusBar().showMessage(
            "Selected %d object%s%s." % (len(roots), "" if len(roots) == 1 else "s", self._filter_note()),
            4000,
        )

    #: Select Same: (mode, menu text, what the status bar says the key object lacks)
    SAME_MODES = (
        ("element", "Library Element", "no library element"),
        ("kind", "Kind", ""),
        ("fill", "Fill Colour", "no fill"),
        ("line_colour", "Line Colour", "no line colour"),
        ("line_width", "Line Width", "no line"),
        ("font", "Font and Size", "no font"),
        ("size", "Size", "no size"),
        ("anim", "Animation Expression", "no animation expression"),
    )
    _LINE_KINDS = frozenset(("line", "pipe", "rectangle", "ellipse", "polygon"))
    _TEXT_KINDS = frozenset(("text", "number", "button"))

    @staticmethod
    def _element_key(o, keep_case: bool = False) -> str:
        """The library element a genie / symbol unit places ('energydemo.valve_left'), ''
        for any other object."""
        for ref in (o.genie_template, o.raw.get("_ref"), o.raw.get("_resolved_ref")):
            s = str(ref or "").strip()
            if not s:
                continue
            low = s.lower()
            for ext in (".<ctm", ".<ctl", ".<ctt", "<ctm", "<ctl", "<ctt"):
                if low.endswith(ext):
                    s = s[: -len(ext)].rstrip(".")
                    break
            s = os.path.basename(s.replace("\\", "/")) if "/" in s or "\\" in s else s
            return s if keep_case else s.lower()
        return ""

    def _unit_expressions(self, root: int) -> frozenset:
        """Every animation expression of the unit's records (case and spaces ignored)."""
        out = set()
        objs = self.doc.page.objects
        for i in self.doc.subtree(root):
            if not 0 <= i < len(objs):
                continue
            a = objs[i].animations
            vals = []
            if a.visibility:
                vals.append(a.visibility.expression)
            if a.movement:
                m = a.movement
                vals += [
                    m.rotation_expr,
                    m.scale_h_expr,
                    m.scale_v_expr,
                    m.move_h_expr,
                    m.move_v_expr,
                    m.slider_expr,
                ]
            if a.fill_colour:
                vals += [t.expression for t in a.fill_colour.thresholds]
            if a.fill_level:
                vals.append(a.fill_level.expression)
            if a.symbol_set:
                vals += list(a.symbol_set.expressions)
            if a.display_value:
                vals.append(a.display_value.expression)
            if a.access_disable:
                vals.append(a.access_disable.expression)
            vals.append(a.cicode_expression)
            for v in vals:
                v = " ".join(str(v or "").split()).lower()
                if v:
                    out.add(v)
        return frozenset(out)

    def _same_key(self, root: int, mode: str):
        """What Select Same compares for ``mode`` on unit ``root`` (None: it has none)."""
        o = self.doc.page.objects[root]
        kind = o.kind.value
        st = o.style
        if mode == "element":
            return self._element_key(o) or None
        if mode == "kind":
            from ..names import kind_code

            code = kind_code(o)  # (a placed group-root symbol: 'symbol')
            for k, group in enumerate(self._SEL_FILTERS):
                if group and code in group:
                    return "group%d" % k
            return code
        if mode == "fill":
            return st.fill_colour if st.filled and st.fill_colour is not None else None
        if mode == "line_colour":
            return st.line_colour if kind not in self._TEXT_KINDS else None
        if mode == "line_width":
            return st.line_width if kind in self._LINE_KINDS else None
        if mode == "font":
            return ((st.font or "").lower(), st.font_size) if kind in self._TEXT_KINDS and st.font else None
        if mode == "size":
            g = o.geometry
            return (g.extent_x, g.extent_y) if g.extent_x > 0 or g.extent_y > 0 else None
        if mode == "anim":
            return self._unit_expressions(root) or None
        return None

    def select_same(self, mode: str) -> None:
        """Edit > Select > Select Same: every unit like the key object (the one added last
        by a click) in ``mode`` (SAME_MODES), as Select All would take them."""
        if self.doc is None:
            return
        self.commit_nudge()
        key = self.view.key_root() if self._sel else None
        n = len(self.doc.page.objects)
        if key is None and self._sel and 0 <= self._sel[-1] < n:
            key = self.doc.root_of(self._sel[-1])
        if key is None or not 0 <= key < n:
            self.statusBar().showMessage(
                "Select an object first: Select Same selects the objects like it.", 5000
            )
            return
        want = self._same_key(key, mode)
        lacks = dict((m, why) for m, _t, why in self.SAME_MODES).get(mode, "")
        if want is None:
            self.statusBar().showMessage("%s has %s." % (self._unit_label(key), lacks), 5000)
            return
        roots = []
        for r in self._select_pool():
            k = self._same_key(r, mode)
            if k is None:
                continue
            if (mode == "anim" and k & want) or (mode != "anim" and k == want):
                roots.append(r)
        if key not in roots and key in self._select_pool():
            roots.append(key)
        self._set_selection(sorted(roots), reveal=False)
        self.statusBar().showMessage(
            "Selected %d object%s like %s%s."
            % (len(roots), "" if len(roots) == 1 else "s", self._unit_label(key), self._filter_note()),
            6000,
        )

    def instances_of(self, root: int) -> list:
        """The selectable units that place the same library element as ``root``."""
        want = self._element_key(self.doc.page.objects[root]) if self.doc is not None else ""
        if not want:
            return []
        return [r for r in self._select_pool() if self._element_key(self.doc.page.objects[r]) == want]

    def select_instances(self, root: int) -> None:
        """Right-click > Select All Instances of <element>."""
        roots = self.instances_of(root)
        if roots:
            self._set_selection(roots, reveal=False)
            self.statusBar().showMessage(
                "Selected %d object%s placing %s%s."
                % (
                    len(roots),
                    "" if len(roots) == 1 else "s",
                    self._element_key(self.doc.page.objects[root], keep_case=True),
                    self._filter_note(),
                ),
                6000,
            )

    def select_using_tag(self, tag: str | None = None) -> None:
        """Edit > Select > Select Objects Using Tag…: every unit with an expression, a
        command or a genie parameter naming the tag (a whole tag name, not a part of a
        longer one)."""
        if self.doc is None:
            return
        if tag is None:
            from ..dialog.select_dialogs import SelectByTagDialog

            dlg = SelectByTagDialog(self, self._tag_completion(), getattr(self, "_last_select_tag", ""))
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return
            tag = dlg.tag()
        tag = (tag or "").strip()
        if not tag:
            return
        self._last_select_tag = tag
        from ...features import find_replace as FR

        opts = FR.FindOptions(
            tag,
            scopes=frozenset((FR.SCOPE_EXPR, FR.SCOPE_PARAM)),
            match=FR.MATCH_WORD,
            inside_genies=True,
            inside_templates=False,
            template_editable=False,
            break_lock=self._break_lock,
        )
        n = len(self.doc.page.objects)
        found = {self.doc.root_of(h.index) for h in FR.find_hits(self.doc, opts) if 0 <= h.index < n}
        roots = [r for r in self._select_pool() if r in found]
        self._set_selection(roots, reveal=True)
        if roots:
            self.statusBar().showMessage(
                "Selected %d object%s that use %s%s."
                % (len(roots), "" if len(roots) == 1 else "s", tag, self._filter_note()),
                6000,
            )
        else:
            self.statusBar().showMessage("No object you can select uses %s." % tag, 6000)

    #: Edit > Select > Select Previous goes back this many selections
    SEL_HISTORY_MAX = 10

    def _note_selection_history(self, indices) -> None:
        """Remember the selection before this one (Select Previous), by record: an edit
        that renumbers the records keeps it."""
        doc = self.doc
        if doc is None or self.__dict__.get("_sel_restoring"):
            return
        ui = getattr(doc, "ui", None)
        if ui is None:
            return
        objs = doc.parsed.page.objects if doc.parsed is not None else []
        recs = tuple(objs[i] for i in indices if 0 <= i < len(objs))
        last = getattr(ui, "sel_now", ())
        if [id(r) for r in recs] == [id(r) for r in last]:
            return
        hist = getattr(ui, "sel_history", None)
        if hist is None:
            hist = ui.sel_history = []
        if last:
            hist.append(last)
            del hist[: -self.SEL_HISTORY_MAX]
        ui.sel_now = recs

    def _on_canvas_selection_noted(self, roots: list) -> None:
        """A click on the canvas changed the selection (Select Previous remembers it)."""
        self._note_selection_history(roots)

    def select_previous(self) -> None:
        """Edit > Select > Select Previous: the selection before the last change (ten are
        kept per page)."""
        doc = self.doc
        ui = getattr(doc, "ui", None) if doc is not None else None
        hist = getattr(ui, "sel_history", None) if ui is not None else None
        objs = doc.parsed.page.objects if doc is not None and doc.parsed is not None else []
        pos = {id(po): k for k, po in enumerate(objs)}
        while hist:
            recs = hist.pop()
            idx = [pos[id(r)] for r in recs if id(r) in pos]
            if not idx:
                continue  # (those objects are gone)
            self._sel_restoring = True
            try:
                self._set_selection(idx, reveal=True)
            finally:
                self._sel_restoring = False
            ui.sel_now = tuple(objs[i] for i in idx)
            self.statusBar().showMessage(
                "Selected the %d object%s selected before." % (len(idx), "" if len(idx) == 1 else "s"), 4000
            )
            return
        self.statusBar().showMessage("No earlier selection on this page.", 4000)

    # ------------------------------------------------------------- edit intents
    def _on_objects_moved(self, indices: list, dx: int, dy: int) -> None:
        self.commit_nudge()
        if self.doc is None:
            return
        dragged = list(indices)  # the canvas moved these items: rebuild them
        indices = self._filter_template(indices, "move")
        if not indices:
            self._refresh(units=dragged)  # snap items back to the model
            return
        try:
            self.doc.move_objects(indices, dx, dy)
        except TransformError as e:
            self.statusBar().showMessage(str(e), 8000)
            self._refresh(units=dragged)  # snap items back to the model
            return
        # move = geometry-only; tree unchanged
        self._refresh(rebuild_tree=False, units=dragged)

    def _on_object_resized(self, index: int, rect: tuple) -> None:
        self.commit_nudge()
        if self.doc is None:
            return
        if not self._filter_template([index], "resize"):
            self._refresh(units=[index])
            return
        self._resize_unit(index, rect, getattr(self.view, "last_resize_origin", None))
        # resize = geometry-only; tree unchanged; the dragged items always re-sync
        self._refresh(rebuild_tree=False, units=[index])

    # -- node editing (reshape, help 1172187 / 1172188) --------------------------
    def _node_editable(self, index: int) -> bool:
        """The canvas may show / edit this object's nodes: not locked (Edit >
        Lock, CtDraw's soft lock, a locked template record) and not part of a
        placed genie (the page re-composes its members from the genie)."""
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return False
        if self._unit_locked(self.doc.root_of(index)) or (not self._break_lock and self._ct_locked(index)):
            return False
        from ..objprops.registry import read_only_reason

        return not read_only_reason(self.doc, index, self._template_locked() and not self._break_lock)

    def _on_nodes_moved(self, index: int, nodes: list, dx: int, dy: int) -> None:
        if self.doc is None:
            return
        if self._filter_template([index], "reshape") and not self.doc.move_object_nodes(index, nodes, dx, dy):
            if self.doc.reshape_info(index) is None:
                msg = (
                    "This object's nodes cannot be moved: its shape comes from a library or genie definition."
                )
            else:
                msg = (
                    "The nodes could not be moved there: a node would leave the page's "
                    "coordinate range (-32768 to 32767)."
                )
            self.statusBar().showMessage(msg, 6000)
        self._refresh(rebuild_tree=False, units=[index])

    def _on_node_insert(self, index: int, at: int, x: int, y: int) -> None:
        if self.doc is None or not self._filter_template([index], "reshape"):
            return
        if self.doc.insert_object_node(index, at, (x, y)):
            self.view.set_node_selection(index, [at])  # CtDraw selects the new node
            self.statusBar().showMessage("Node added at %d, %d" % (x, y), 3000)
        else:
            self.statusBar().showMessage("No node could be added there.", 4000)
        self._refresh(rebuild_tree=False, units=[index])

    def _delete_command(self) -> None:
        """Delete / '-': the selected NODES when nodes of a line, pipe or
        polygon are selected, else the selected objects (help 1172187)."""
        ns = self.view.node_selection() if self.doc is not None else None
        if ns:
            self._delete_nodes(*ns)
            return
        self.delete_selected()

    def _delete_nodes(self, index: int, nodes: list) -> None:
        info = self.doc.reshape_info(index)
        if info is None:
            return
        if info.kind == "line":
            # a line always has its two nodes: deleting one deletes the line
            # (help 1172188)
            self.view.clear_node_selection()
            self._delete_indices([index])
            return
        if not self._filter_template([index], "reshape"):
            return
        from ...format.geometry import MIN_NODES

        if len(info.nodes) - len(set(nodes)) < MIN_NODES:
            noun = "pipe" if info.kind == "pipe" else "polygon"
            self.statusBar().showMessage(
                "A %s keeps at least %d nodes, so these cannot all be deleted. To delete "
                "the whole %s, click away from its nodes and press Delete." % (noun, MIN_NODES, noun),
                8000,
            )
            return
        if self.doc.delete_object_nodes(index, nodes):
            self.view.clear_node_selection()
            self.statusBar().showMessage("Deleted %d node(s)" % len(set(nodes)), 3000)
        self._refresh(rebuild_tree=False, units=[index])

    def _resize_unit(self, index: int, rect: tuple, origin) -> None:
        """Resize one unit to ``rect`` (the box its handles were drawn on is
        ``origin``) -- no refresh."""
        obj = self.doc.page.objects[index] if index < len(self.doc.page.objects) else None
        if (
            obj is not None
            and obj.members
            and origin is not None
            and origin.width() > 0
            and origin.height() > 0
        ):
            # the handles framed the whole unit; map the drag onto the root
            # record's own extent (the unit is scaled from it)
            g = obj.geometry
            sx = (rect[2] - rect[0]) / origin.width()
            sy = (rect[3] - rect[1]) / origin.height()

            def mx(v):
                return int(round(rect[0] + (v - origin.left()) * sx))

            def my(v):
                return int(round(rect[1] + (v - origin.top()) * sy))

            rect = (mx(g.x), my(g.y), mx(g.x + g.extent_x), my(g.y + g.extent_y))
        self.doc.set_object_rect(index, rect)

    def _on_draw_committed(self, tool: str, payload: dict) -> None:
        if self.doc is None:
            self.statusBar().showMessage("Open a document first", 4000)
            self._set_tool("select")
            return
        # (an object was made: Settings 'Keep the tool after drawing or placing an object'
        # keeps the tool for the next one; a cancelled one goes back to Select)
        placed = False
        try:
            if tool in _SPECIAL_TOOLS:
                placed = (
                    self._place_special(tool, payload.get("rect") or (0, 0, 0, 0), bool(payload.get("click")))
                    is not None
                )
                return
            kw = {}
            if tool in ("text",):
                kw["text"] = "Text"
            elif tool == "button":
                kw["text"] = "Button"
                # Settings > XP style buttons by default (CtDraw XPButtonsDefault)
                kw["xp_button"] = option("xp_buttons")
            elif tool == "number":
                # a number without an expression does not compile: ask first
                expr = self._ask_number_expression()
                if expr is None:
                    self.statusBar().showMessage("Number not placed: no expression was entered", 4000)
                    return
                kw["text"] = expr
            if tool == "symbol_set":
                # CtDraw's Symbol Set tool: a click places a new set (its first
                # state's top-left at the click) and opens its sheet
                rect = payload.get("rect") or (0, 0, 0, 0)
                idx = self.doc.create_symbol_set((min(rect[0], rect[2]), min(rect[1], rect[3])))
                self._sel = [idx]
                self._refresh()
                placed = True
                QtCore.QTimer.singleShot(0, self, lambda i=idx: self.symbol_set_properties(i))
                return
            if tool == "symbol" and self._pending_composite:
                # Paste Composite Genie: the copy's top-left lands at the click (once: its
                # options were chosen for this one)
                guid, self._pending_composite = self._pending_composite, None
                rect = payload.get("rect") or (0, 0, 0, 0)
                self._place_composite(guid, (min(rect[0], rect[2]), min(rect[1], rect[3])))
                return
            if tool == "symbol":
                if self._pending_symbol is None:
                    from ..browser import PageBrowserDialog

                    pick = PageBrowserDialog.get_library_element(
                        self, os.path.dirname(os.path.abspath(self.doc.path)), "symbol", linked_option=True
                    )
                    self._sync_paste_linked()
                    if pick is None:
                        self._set_tool("select")
                        return
                    self._pending_symbol = (os.path.splitext(os.path.basename(pick[0]))[0], pick[1], "ctl")
                lib, elem, ext = self._pending_symbol
                rect = payload.get("rect") or (0, 0, 0, 0)
                # (a click is never a box)
                boxed = not payload.get("click") and abs(rect[2] - rect[0]) > 4 and abs(rect[3] - rect[1]) > 4
                if ext == "ctm" or not boxed:
                    # a click pastes the element at its NATIVE size with its
                    # top-left there (CtDraw's Paste Genie / Paste Symbol); a
                    # genie always pastes (its parameters are asked first)
                    idx = self._place_library(lib, elem, ext, (min(rect[0], rect[2]), min(rect[1], rect[3])))
                    placed = idx is not None
                    return
                if not paste_linked():
                    # an UNLINKED copy dragged to a box: pasted, then sized
                    box = (
                        min(rect[0], rect[2]),
                        min(rect[1], rect[3]),
                        max(rect[0], rect[2]),
                        max(rect[1], rect[3]),
                    )
                    idx = self.doc.place_library_element(lib, elem, ext, box[:2], linked=False)
                    if idx is not None:
                        self.doc.set_object_rect(idx, box)
                    self._sel = [idx] if idx is not None else []
                    self._refresh()
                    placed = idx is not None
                    if idx is not None and option("props_on_new"):
                        QtCore.QTimer.singleShot(0, self, lambda i=idx: self._props_after_new(i))
                    return
                kw["symbol"] = self._pending_symbol
            colour = None if tool in ("button", "symbol") else self._draw_colour
            idx = self._create_styled(tool, payload.get("rect"), payload.get("nodes"), colour=colour, **kw)
            self._sel = [idx]
            self._refresh()
            placed = True
            if option("props_on_new"):
                QtCore.QTimer.singleShot(0, self, lambda i=idx: self._props_after_new(i))
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Create Failed", str(e))
        finally:
            if not (placed and getattr(self, "_keep_tool", False)):
                self._set_tool("select")

    _ARROWS = ("Left", "Right", "Up", "Down")

    def _move_key(self, dx: int, dy: int, key: str) -> None:
        """Move the selection by an arrow key."""
        if self._nudge_nodes(dx, dy):
            return
        if not self._sel:
            self._pan_key(dx, dy, key)
            return
        if key in self._ARROWS and option("arrow_grid") and self.act_snap.isChecked():
            self._grid_nudge(dx, dy, pitch=False)
            return
        self.nudge(dx, dy)

    def _ctrl_arrow(self, dx: int, dy: int, key: str) -> None:
        """Ctrl + an arrow key: Settings > Editing 'Ctrl + arrow keys': move one step (the
        Graphics Builder), one large step ('Large arrow key step', 10), or one grid pitch;
        with nothing selected the page scrolls, as the plain arrows do."""
        if self._nudge_nodes(dx, dy):
            return
        if not self._sel:
            self._pan_key(dx, dy, key)
            return
        mode = str(app_settings().value("opt/ctrl_arrow", "move"))
        if mode == "x10":
            self.nudge(dx, dy, fast=True)
        elif mode == "grid":
            self._grid_nudge(dx, dy, pitch=True)
        else:
            self.nudge(dx, dy)

    def _pan_key(self, dx: int, dy: int, key: str, page: bool = False) -> None:
        """A move key with nothing selected: Page Up / Page Down scroll a page; with
        'opt/arrows_pan' an arrow scrolls an eighth of the view (Settings 'Arrow keys scroll
        by': a sixteenth or a quarter; ``page``: Shift, a whole view), Home / End show the
        page's left / right edge 20 px from the canvas's."""
        view = self.view
        act = QtWidgets.QAbstractSlider.SliderAction
        if key in ("PgUp", "PgDown") and not page:
            view.verticalScrollBar().triggerAction(
                act.SliderPageStepSub if key == "PgUp" else act.SliderPageStepAdd
            )
            return
        if not option("arrows_pan"):
            return
        if key in ("Home", "End") and not page:
            sc = view.scene()
            pr = sc.page_rect() if sc is not None and hasattr(sc, "page_rect") else None
            if pr is None or pr.isNull():
                return
            z = max(view.zoom(), 0.01)
            vw = view.viewport().width()
            hs = view.horizontalScrollBar()
            left = view.mapFromScene(pr.topLeft()).x()
            right = view.mapFromScene(pr.topRight()).x()
            shift = (left - 20) if key == "Home" else (right - (vw - 20))
            view.sync_scene_rect(
                extra=view.mapToScene(view.viewport().rect()).boundingRect().translated(shift / z, 0)
            )
            hs.setValue(hs.value() + int(round(shift)))
            return
        vp = view.viewport()
        for bar, d, size in (
            (view.horizontalScrollBar(), dx, vp.width()),
            (view.verticalScrollBar(), dy, vp.height()),
        ):
            if not d:
                continue
            if page:
                bar.triggerAction(act.SliderPageStepAdd if d > 0 else act.SliderPageStepSub)
            else:
                step = max(1, int(size / getattr(self, "_pan_divisor", 8)))
                bar.setValue(bar.value() + d * step)

    def _grid_nudge(self, dx: int, dy: int, pitch: bool) -> None:
        """Move the selection to the next grid line in the key's direction (``pitch``:
        exactly one grid pitch), as part of the arrow-key burst."""
        view = self.view
        gx, gy, ox, oy = view.grid()
        if pitch:
            self.nudge(dx * gx, dy * gy, raw=True)
            return
        box = view.selection_model_box()
        if box is None:
            self.nudge(dx, dy)
            return
        mx = view.next_grid_line(box.left(), 0, dx) - box.left() if dx else 0
        my = view.next_grid_line(box.top(), 1, dy) - box.top() if dy else 0
        self.nudge(int(round(mx)), int(round(my)), raw=True)

    def _nudge_nodes(self, dx: int, dy: int) -> bool:
        """Move the selected nodes by an arrow key."""
        ns = self.view.node_selection() if self.doc is not None else None
        if not ns:
            return False
        self.commit_nudge()
        index, nodes = ns
        step = self._key_step()
        self._on_nodes_moved(index, nodes, dx * step, dy * step)
        return True

    def _key_step(self) -> int:
        """The arrow-key step: the configured step, 1 px with Cursor Keys
        Slow Speed."""
        return 1 if self.act_slow_keys.isChecked() else max(1, int(self._nudge_step))

    def _shift_arrow(self, dx: int, dy: int) -> None:
        """Shift + an arrow / diagonal key: the Graphics Builder RESIZES the
        selection (help 1172187, the default); Settings can make it move one
        large step instead ('Large arrow key step', 10)."""
        if self._nudge_nodes(dx, dy):
            return
        if not self._sel:
            self._pan_key(dx, dy, "", page=True)  # (nothing selected: a page's scroll)
            return
        mode = app_settings().value("opt/shift_arrow", "resize", type=str)
        if mode == "nudge10":
            self.nudge(dx, dy, fast=True)
        else:
            self.resize_by_keys(dx, dy)

    def resize_by_keys(self, dx: int, dy: int, keep_aspect: bool = False) -> None:
        """Resize the selection from the keyboard."""
        if self.doc is None or not self._sel:
            return
        self.commit_nudge()  # (the moves of the keys before go first)
        view = self.view
        units = [u for u in view._units if u]
        if not units:
            return
        roots = set(self._filter_template([min(u) for u in units], "resize"))
        boxes = []
        for u in units:
            if min(u) not in roots:
                continue
            r = view._extent_rect(u[0]) if len(u) == 1 else view._unit_rect(u)
            if r is not None and (r.width() > 0 or r.height() > 0):
                boxes.append((min(u), QtCore.QRectF(r)))
        if not boxes:
            return
        b = QtCore.QRectF(boxes[0][1])
        for _i, r in boxes[1:]:
            b = b.united(r)
        step = self._key_step()
        w, h = b.width(), b.height()
        sx = max(1.0, w + dx * step) / w if w > 0 and dx else 1.0
        sy = max(1.0, h + dy * step) / h if h > 0 and dy else 1.0
        if keep_aspect:
            if dx and w > 0 and (not dy or h <= 0 or abs(sy) <= abs(sx)):
                sy = sx if h > 0 else 1.0
            elif dy and h > 0:
                sx = sy if w > 0 else 1.0
        if sx == 1.0 and sy == 1.0:
            return

        def mx(v):
            return int(round(b.left() + (v - b.left()) * sx))

        def my(v):
            return int(round(b.top() + (v - b.top()) * sy))

        # (the readout sums the keys of a burst: presses closer than the pill lasts)
        import time as _time

        burst = self.__dict__.get("_key_resize")
        now = _time.monotonic()
        key = (id(self.doc), tuple(sorted(i for i, _r in boxes)))
        if burst is None or burst["key"] != key or now - burst["t"] > 0.8:
            burst = self._key_resize = {"key": key, "w0": w, "h0": h}
        burst["t"] = now
        with self.doc.batch("resize"):
            for i, r in boxes:
                rect = (mx(r.left()), my(r.top()), mx(r.right()), my(r.bottom()))
                if rect != (int(r.left()), int(r.top()), int(r.right()), int(r.bottom())):
                    self._resize_unit(i, rect, r)
        self._refresh(rebuild_tree=False)
        nw, nh = mx(b.right()) - mx(b.left()), my(b.bottom()) - my(b.top())
        self.view.show_key_pill(
            "%d × %d  (%s, %s)" % (nw, nh, _signed(nw - burst["w0"]), _signed(nh - burst["h0"]))
        )

    def nudge(self, dx: int, dy: int, fast: bool = False, raw: bool = False) -> None:
        """Nudge the selection by the key step."""
        if self.doc is None or not self._sel:
            return
        # arrow keys move by the configured step; Slow Speed forces 1px;
        # ``fast`` (Ctrl / Shift + arrow set to the large move) = Settings 'Large arrow key
        # step' steps (10); ``raw``: (dx, dy) are pixels already (a grid step)
        step = 1 if raw else self._key_step() * (getattr(self, "_big_nudge", 10) if fast else 1)
        sel = self.view.selection()
        burst = self.__dict__.get("_nudge_burst")
        if burst is not None and (burst["doc"] is not self.doc or burst["sel"] != sel):
            self.commit_nudge()  # another page or selection: that one is done
            burst = None
        if burst is None:
            # move the full subtree of the selection (genie members included), same
            # set the mouse drag moves -- self._sel holds only the unit ROOTS, so
            # moving that alone would leave members behind and stretch the bbox.
            movable = self._filter_template(sel, "move")
            if not movable:
                return
            objs = self.doc.parsed.page.objects
            burst = self._nudge_burst = {
                "doc": self.doc,
                "sel": sel,
                "indices": movable,
                "dx": 0,
                "dy": 0,
                # (the records: the indices are found again should the page change)
                "records": [objs[i] for i in movable if 0 <= i < len(objs)],
            }
            self._watch_nudge(True)
        burst["dx"] += dx * step
        burst["dy"] += dy * step
        self.view.nudge_preview(burst["indices"], dx * step, dy * step)
        self.view.flash_alignment_guides()
        box = self.view.selection_model_box()
        if box is not None:
            self.view.show_key_pill(
                "%d, %d  (%s, %s)" % (box.left(), box.top(), _signed(burst["dx"]), _signed(burst["dy"]))
            )
        wait = nudge_burst_ms()
        if wait <= 0:
            self.commit_nudge()  # each press is its own move
        else:
            self._nudge_timer().start(wait)

    def _nudge_timer(self) -> QtCore.QTimer:
        t = self.__dict__.get("_nudge_timer_obj")
        if t is None:
            t = self._nudge_timer_obj = QtCore.QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self.commit_nudge)
        return t

    def _watch_nudge(self, on: bool) -> None:
        """While a burst waits: the application filter that commits it before other input."""
        app = QtWidgets.QApplication.instance()
        f = self.__dict__.get("_nudge_filter")
        if on:
            if f is None:
                f = self._nudge_filter = _NudgeBurstFilter(self)
            if app is not None and not f.installed:
                app.installEventFilter(f)
                f.installed = True
        elif f is not None and f.installed:
            if app is not None:
                app.removeEventFilter(f)
            f.installed = False

    def nudge_pending(self) -> bool:
        """Arrow-key moves are shown on the canvas and not on the page yet."""
        return self.__dict__.get("_nudge_burst") is not None

    def commit_nudge(self) -> bool:
        """The arrow-key burst reaches the page: ONE move of the summed vector (one
        undo step), then the canvas shows the page. True when the page changed."""
        burst = self.__dict__.get("_nudge_burst")
        if burst is None:
            return False
        self._nudge_burst = None
        t = self.__dict__.get("_nudge_timer_obj")
        if t is not None:
            t.stop()
        self._watch_nudge(False)
        self.view.end_nudge_preview()
        doc, dx, dy = burst["doc"], burst["dx"], burst["dy"]
        shown = doc is self.doc
        if not (dx or dy):
            return False
        indices = self._burst_indices(burst)
        if indices is None:
            if shown:
                self.statusBar().showMessage(
                    "The arrow-key move was not kept: the page changed meanwhile.", 6000
                )
                self._refresh(rebuild_tree=False)
            return False
        try:
            doc.move_objects(indices, dx, dy)
        except Exception as e:  # noqa: BLE001 -- said on the status bar, the page stays
            from ...infra import logsetup

            logsetup.note_exception("ui.canvas_edits.commit_nudge")
            if shown:
                self.statusBar().showMessage("The objects could not be moved: %s" % e, 8000)
                self._refresh(rebuild_tree=False, units=indices)
            return False
        if shown:
            # geometry only, like the drag's commit: the Objects tree is unchanged
            self._refresh(rebuild_tree=False, units=indices)
        return True

    @staticmethod
    def _burst_indices(burst):
        """The burst's record indices on its page now (the records are looked up again
        when the page changed meanwhile); None when one of them is gone."""
        objs = burst["doc"].parsed.page.objects
        idx, recs = burst["indices"], burst["records"]
        if len(recs) == len(idx) and all(0 <= i < len(objs) and objs[i] is r for i, r in zip(idx, recs)):
            return idx
        pos = {id(po): k for k, po in enumerate(objs)}
        out = [pos.get(id(r)) for r in recs]
        if not out or None in out:
            return None
        return sorted(out)

    def undo(self) -> None:
        # an open in-place text edit is stored FIRST, so Undo takes back the
        # typing (Redo brings it back) -- stored after the undo it would land
        # on the undone page and wipe the redo history
        if self.doc is not None:
            self._flush_text_edit()
        if self.doc and self.doc.can_undo():
            label = self.doc._undo[-1].label
            self.doc.undo()
            # (said first: a window closed by the undo says so over it)
            self.statusBar().showMessage(self._history_message("Undid", label), 3000)
            # selected node numbers refer to the page before the undo
            self.view.clear_node_selection()
            self._sync_modeless("history")
            self._refresh()
            self._reveal_history_targets()

    def go_to_history(self, k: int) -> None:
        """The History panel's click: the document after ``k`` steps (0 = as opened): the
        later steps undone, or the undone ones redone up to it."""
        if self.doc is None:
            return
        self._flush_text_edit()
        doc = self.doc
        moved = 0
        while len(doc._undo) > k and doc.can_undo():
            doc.undo()
            moved += 1
        while len(doc._undo) < k and doc.can_redo():
            doc.redo()
            moved += 1
        if moved:
            self.view.clear_node_selection()
            self._sync_modeless("history")
            self._refresh()
            self.statusBar().showMessage(
                "History: %d step%s %s."
                % (moved, "" if moved == 1 else "s", "undone" if len(doc._undo) <= k else "redone"),
                3000,
            )
        self._update_actions()
        self._reveal_history_targets()

    def redo(self) -> None:
        if self.doc is not None and self._flush_text_edit():
            self._refresh()  # the typing is the newest step now
            return
        if self.doc and self.doc.can_redo():
            label = self.doc._redo[-1].label
            self.doc.redo()
            self.statusBar().showMessage(self._history_message("Redid", label), 3000)
            self.view.clear_node_selection()
            self._sync_modeless("history")
            self._refresh()
            self._reveal_history_targets()

    def duplicate_selected(self) -> None:
        """Edit > Duplicate (Ctrl+D): a copy next to the selection (Settings > Canvas >
        Paste and duplicate offset). With Settings 'Duplicate repeats the last offset', a
        copy that was moved before anything else changed tells the next Duplicate its
        offset: Ctrl+D, move the copy 120 px right, Ctrl+D puts the next one 120 px on."""
        self.commit_nudge()
        if self.doc is None or not self._sel:
            return
        sel = self._filter_template(self._sel, "duplicate")
        if not sel:
            return
        doc = self.doc
        offset = self._smart_offset(sel)
        if offset is None:
            po = getattr(self, "_paste_offset", 10)
            new = doc.duplicate_objects(sel, offset=po)
            note = ""
            self._remember_duplicate(new, (po, po), smart=False)
        else:
            new = doc.duplicate_and_move(sel, offset[0], offset[1])
            note = "Duplicated with the last offset (%s, %s)." % (_signed(offset[0]), _signed(offset[1]))
            self._remember_duplicate(new, offset, smart=True)
        self._sel = new
        self._refresh()
        if note:
            self.statusBar().showMessage(note, 5000)
        self._props_after_copy(self._sel)

    def _unit_boxes(self, roots) -> list:
        objs = self.doc.page.objects
        out = []
        for r in roots:
            g = objs[r].geometry
            out.append((g.x, g.y, g.extent_x, g.extent_y))
        return out

    def _remember_duplicate(self, copies, offset, smart: bool) -> None:
        """The copies Duplicate (or a drag copy) just made, where they are, the offset they
        were made at and the step: a move of them alone tells the next Duplicate its offset
        (``smart``: the offset itself is one the user gave, so Duplicate repeats it)."""
        doc = self.doc
        objs = doc.parsed.page.objects
        roots = sorted(set(copies))
        doc.ui.smart_dup = {
            "records": tuple(objs[r] for r in roots),
            "boxes": self._unit_boxes(roots),
            "undo": len(doc._undo),
            "count": len(objs),
            "offset": tuple(offset),
            "smart": smart,
        }

    def _smart_offset(self, sel):
        """The offset Duplicate uses now (Settings 'Duplicate repeats the last offset'):
        when the copies Duplicate or a drag copy made last are selected and were only moved
        since (a drag, the arrow keys, the Properties panel's X / Y), the offset from their
        originals to where they are now; None for the paste offset."""
        doc = self.doc
        memo = getattr(doc.ui, "smart_dup", None)
        if memo is None or not app_settings().value("edit/smart_duplicate", True, type=bool):
            return None
        objs = doc.parsed.page.objects
        if len(objs) != memo["count"] or len(doc._undo) < memo["undo"]:
            return None
        if any(c.label not in ("move", "resize") for c in doc._undo[memo["undo"] :]):
            return None  # (another edit came between)
        pos = {id(po): k for k, po in enumerate(objs)}
        roots = [pos.get(id(r)) for r in memo["records"]]
        if None in roots:
            return None
        n = len(objs)
        chosen = sorted({doc.root_of(i) for i in sel if 0 <= i < n})
        if chosen != sorted(roots):
            return None
        d = None
        for (x0, y0, w0, h0), (x1, y1, w1, h1) in zip(memo["boxes"], self._unit_boxes(roots)):
            if (w0, h0) != (w1, h1):
                return None
            step = (x1 - x0, y1 - y0)
            if d is None:
                d = step
            elif step != d:
                return None
        if d is None:
            return None
        if d == (0, 0):
            return memo["offset"] if memo["smart"] else None
        ox, oy = memo["offset"]
        return (ox + d[0], oy + d[1])

    def _on_objects_copied(self, indices: list, dx: int, dy: int) -> None:
        """A move drag let go with the copy key (Ctrl, or Alt by Settings): the original
        stays, a copy lands where the drag put it. Duplicate in place, then a move of the
        copy: one undo step."""
        self.commit_nudge()
        if self.doc is None:
            return
        dragged = list(indices)
        sel = self._filter_template(indices, "duplicate")
        if not sel:
            self._refresh(units=dragged)
            return
        doc = self.doc
        new = doc.duplicate_and_move(sel, dx, dy)
        self._sel = new
        self._remember_duplicate(new, (dx, dy), smart=True)
        self._refresh(units=dragged)
        self.statusBar().showMessage(
            "Copied %d object%s (%s, %s)."
            % (len(new), "" if len(new) == 1 else "s", _signed(dx), _signed(dy)),
            4000,
        )
        self._props_after_copy(self._sel)

    #: Display properties on copy opens at most this many windows for one
    #: paste / duplicate (the rest are named on the status bar): the default of Settings >
    #: Canvas 'Open the properties of at most' (opt/copy_sheets_max, 0 = no limit;
    #: apply_editing_options keeps it in _copy_sheets_max)
    COPY_SHEETS_MAX = 7

    def _props_after_copy(self, indices) -> None:
        """Settings > Display properties on copy."""
        if self.doc is None or not indices or not option("props_on_copy"):
            return
        from ...format import inputs

        objs = self.doc.page.objects
        roots = []
        for i in sorted({self.doc.root_of(i) for i in indices if 0 <= i < len(objs)}):
            if objs[i].raw.get("_type_code") not in inputs.INPUT_TCS:
                roots.append(i)
        most = getattr(self, "_copy_sheets_max", self.COPY_SHEETS_MAX)
        shown = roots[:most] if most > 0 else roots

        def open_all(doc=self.doc):
            if self.doc is not doc:
                return
            for r in shown:
                if 0 <= r < len(self.doc.page.objects):
                    obj = self.doc.page.objects[r]
                    if obj.is_genie:
                        self._edit_genie(r, obj)
                    else:
                        self.object_properties(r)

        QtCore.QTimer.singleShot(0, open_all)
        if len(roots) > len(shown):
            self.statusBar().showMessage(
                "Opened the properties of the first %d of the %d objects copied" % (len(shown), len(roots)),
                6000,
            )

    def delete_selected(self) -> None:
        self._delete_indices(self._sel)

    def _delete_indices(self, indices: list) -> None:
        self.commit_nudge()
        if self.doc is None or not indices:
            return
        indices = self._filter_template(indices, "delete")
        if not indices:
            return
        if app_settings().value("opt/confirm_delete", False, type=bool):
            n = len({self.doc.root_of(i) for i in indices})
            from ..messages import confirm

            if not confirm(self, "Delete", "Delete %d object%s?" % (n, "" if n == 1 else "s"), "Delete"):
                return
        units = len({self.doc.root_of(i) for i in indices})
        self.doc.delete_objects(indices)
        self._sel = []
        self._refresh()
        if units >= 5:
            # (a large delete is told over the page too, with Undo)
            from ..names import plural
            from ..notify import notify, undo_action

            notify(
                self,
                "Deleted %s." % plural(units, "object"),
                "success",
                actions=[undo_action(self)],
                toast=True,
                key="edit:delete",
            )

    def cut_selected(self) -> None:
        if self.doc is None or not self._filter_template(self._sel, "cut"):
            return
        self.copy_selected()
        self.delete_selected()

    def copy_selected(self) -> None:
        self.commit_nudge()  # (the copy is taken from the page)
        if self.doc is None or not self._sel:
            return
        payload = self.doc.copy_payload(self._sel)
        mime = QtCore.QMimeData()
        mime.setData(CLIPBOARD_MIME, payload.encode("utf-8"))
        mime.setText(payload)
        QtWidgets.QApplication.clipboard().setMimeData(mime)
        self.statusBar().showMessage("Copied %d object(s)" % len(self._sel), 3000)

    # -- editing settings ---------------------------------------------------------------
    def apply_editing_options(self) -> None:
        """Settings > Editing, Canvas > Copy and paste, Pages and libraries: read here, when
        the window is made and after each change in Settings, and kept on the window (a key
        press, a paste or a drop never reads the settings)."""
        from ..prefs import settings as S

        self._keep_tool = S.option("keep_tool")
        self.view.KEEP_TOOL = self._keep_tool
        self._big_nudge = S.whole("opt/big_nudge", S.BIG_NUDGE_RANGE)
        self._pan_divisor = int(S.one_of("opt/arrows_pan_step", S.ARROWS_PAN_STEPS))
        self._paste_at = S.one_of("canvas/paste_at", S.PASTE_AT)
        self._copy_sheets_max = S.whole("opt/copy_sheets_max", S.COPY_SHEETS_RANGE)
        self._align_to = S.one_of("opt/align_to", S.ALIGN_TO)
        self._place_anchor = S.one_of("opt/place_anchor", S.PLACE_ANCHORS)
        S.apply_backup_limits()
        from .. import main_window as _main

        mru_before = _main.MainWindow.MRU_MAX
        _main.MainWindow.MRU_MAX = S.whole("opt/mru_max", S.MRU_RANGE)
        # (the start page shows as many cards as the list keeps)
        sp = getattr(self, "start_page", None)
        if sp is not None and mru_before != _main.MainWindow.MRU_MAX and sp.isVisible():
            with contextlib.suppress(RuntimeError):  # swallow-ok: a window being closed
                sp.refresh()
        dlg = getattr(self, "_dialogs", {}).get("align")
        if dlg is not None:
            with contextlib.suppress(RuntimeError):  # swallow-ok: a window being closed
                dlg.set_reference(self._align_to)
        # (Align to: The page aligns one object too: the greyed commands' tips say so)
        from ..main_window_base import NEEDS_ALIGN_PAGE, NEEDS_CUSTOM
        from ..style.tips import NEEDS_PROP, refresh_need_tip

        need = NEEDS_ALIGN_PAGE if self._align_to == "page" else NEEDS_CUSTOM["align"]
        for a in list(getattr(self, "align_actions", ())) + [getattr(self, "act_align_dialog", None)]:
            if a is not None:
                a.setProperty(NEEDS_PROP, need)
                refresh_need_tip(a)  # (the greyed item's status tip says it)
        if hasattr(self, "align_actions"):
            self._update_actions()

    def paste_point(self):
        """Where a paste lands with 'At the pointer'."""
        if getattr(self, "_paste_at", "offset") != "pointer" or self.doc is None:
            return None
        pt = getattr(self, "_menu_paste_point", None)
        if pt is None:
            if menu_just_closed():
                return None
            vp = self.view.viewport()
            gpos = QtGui.QCursor.pos()
            pos = vp.mapFromGlobal(gpos)
            if not vp.rect().contains(pos):
                return None
            on = self.view.childAt(self.view.mapFromGlobal(gpos))
            if on is not None and on is not vp:
                return None
            sp = self.view.mapToScene(pos)
            pt = (int(round(sp.x())), int(round(sp.y())))
        sc = self.view.scene()
        page = sc.page_rect() if sc is not None and hasattr(sc, "page_rect") else None
        if page is not None and not page.isNull() and not page.contains(QtCore.QPointF(pt[0], pt[1])):
            return None
        return pt

    def paste_clipboard(self, at=PASTE_POINT_UNREAD) -> None:
        """Paste the objects ctgedit copied: next to the originals (each further paste of the
        same copy one more paste offset on), or with their top left corner at ``at`` (page
        coordinates, grid-snapped when snapping is on; None = next to the originals; default:
        :meth:`paste_point`, read now)."""
        self.commit_nudge()
        if self.doc is None:
            return
        if at is PASTE_POINT_UNREAD:
            at = self.paste_point()
        mime = QtWidgets.QApplication.clipboard().mimeData()
        payload = None
        if mime is None:
            return
        if mime.hasFormat(CLIPBOARD_MIME):
            payload = bytes(mime.data(CLIPBOARD_MIME)).decode("utf-8", "replace")
        elif mime.hasText() and "ctgedit_objects" in mime.text():
            payload = mime.text()
        if not payload:
            return
        # repeated pastes of the same clipboard step down-right instead of
        # stacking exactly on the previous copy
        if (
            payload == getattr(self, "_last_paste", None)
            and getattr(self, "_last_paste_doc", None) is self.doc
        ):
            self._paste_n += 1
        else:
            self._last_paste, self._last_paste_doc, self._paste_n = payload, self.doc, 1
        if at is not None:
            snap = getattr(self, "act_snap", None)
            at = self.view.placement_point(
                int(at[0]), int(at[1]), grid=bool(snap is not None and snap.isChecked())
            )
        try:
            new = self.doc.paste_payload(
                payload, offset=getattr(self, "_paste_offset", 10) * self._paste_n, at=at
            )
        except ValueError as e:
            # e.g. a composite genie pasted into a genie / symbol window
            QtWidgets.QMessageBox.information(self, "Paste", str(e))
            return
        if new:
            # the pasted top-level units are selected, as they show (not every record:
            # the Objects tree and the Properties panel would work on each of them)
            self._sel = self._unit_roots(new)
            self._refresh()
            self._props_after_copy(self._sel)
            if len(self._sel) >= 10:
                # (a large paste is told over the page too, with Undo)
                from ..names import plural
                from ..notify import notify, undo_action

                notify(
                    self,
                    "Pasted %s." % plural(len(self._sel), "object"),
                    "success",
                    actions=[undo_action(self)],
                    toast=True,
                    key="edit:paste",
                )

    def _unit_roots(self, indices) -> list:
        """The records of ``indices`` whose container is not among them: the
        top-level units a paste or an ungroup leaves, as the canvas selects them."""
        objs = self.doc.page.objects
        got = set(indices)
        return [i for i in sorted(got) if 0 <= i < len(objs) and objs[i].parent not in got]

    def _reorder(self, indices: list, where: str) -> None:
        self.commit_nudge()
        if self.doc is None or not indices:
            return
        indices = self._filter_template(indices, "reorder")
        if not indices:
            return
        self._sel = self.doc.reorder(indices, where)
        self._refresh()

    def _align(self, mode: str) -> None:
        self.commit_nudge()
        sel = self._filter_template(self._sel, "align") if self.doc else []
        if self.doc is None or not sel:
            return
        ref = self.align_reference(sel)
        if ref is False:
            return
        self.doc.align(sel, mode, reference=ref)
        self._refresh()

    def align_reference(self, sel):
        """The box Align lines the units of ``sel`` up with (Settings > Editing 'Align to'):
        None = the selection box (the default, as the Graphics Builder), the box of the
        object selected last (it stays where it is), or the page; False when there is
        nothing to align (one unit, unless to the page)."""
        doc = self.doc
        n = len(doc.page.objects)
        roots = sorted({doc.root_of(i) for i in sel if 0 <= i < n})
        mode = getattr(self, "_align_to", "selection")
        if mode == "page":
            if not roots:
                return False
            pb = doc.parsed.page
            w = doc.page.appearance.width or pb.page_width or 0
            h = doc.page.appearance.height or pb.page_height or 0
            return (0, 0, int(w), int(h)) if w > 0 and h > 0 else False
        if len(roots) < 2:
            return False
        if mode == "last":
            key = self.view.key_root()
            key = doc.root_of(key) if key is not None and 0 <= key < n else None
            if key in roots:
                return doc.unit_box(key)
        return None

    def _distribute(self, axis: str) -> None:
        self.commit_nudge()
        sel = self._filter_template(self._sel, "distribute") if self.doc else []
        if self.doc is None or len(sel) < 3:
            return
        self.doc.distribute(sel, axis)
        self._refresh()

    def _same_size(self) -> None:
        self.commit_nudge()
        sel = self._filter_template(self._sel, "resize") if self.doc else []
        if self.doc is None or len(sel) < 2:
            return
        self.doc.make_same_size(sel, sel[-1])
        self._refresh()

    def _group_selection(self) -> None:
        self.commit_nudge()
        sel = self._filter_template(self._sel, "group") if self.doc else []
        if self.doc is None or len(sel) < 2:
            return
        idx = self.doc.group_objects(sel)
        if idx is not None:
            self._sel = [idx]
            self._refresh()

    def _ungroup_selection(self) -> None:
        self.commit_nudge()
        if self.doc is None or not self._sel:
            return
        from ...editing import SymbolSetError

        # every selected group / genie, one undo step
        sel = self._filter_template(self._sel, "ungroup")
        roots = {self.doc.root_of(i) for i in sel if 0 <= i < len(self.doc.parsed.page.objects)}
        commands = sum(len(self.doc.input_children(r)) for r in roots)
        try:
            members = self.doc.ungroup_objects(sel) if sel else []
        except SymbolSetError as e:
            self.statusBar().showMessage(str(e), 8000)
            return
        if members:
            # the group's former direct children are selected (the top-level units now),
            # not each of its records
            self._sel = self._unit_roots(members)
            self._refresh()
            if commands:
                # the group's own touch / keyboard commands go with the group
                self.statusBar().showMessage(
                    "Ungrouped. The group's own touch / keyboard commands (%d) were "
                    "removed with it; Undo restores them." % commands,
                    8000,
                )
