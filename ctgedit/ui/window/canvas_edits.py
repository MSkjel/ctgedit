"""In-place text, edit intents and node editing."""

from __future__ import annotations

from ...infra.logsetup import note_exception as _note_exc

from ..names import plural

from ..main_window_base import (
    CLIPBOARD_MIME,
    PASTE_POINT_UNREAD,
    QtCore,
    QtGui,
    QtWidgets,
    contextlib,
    menu_just_closed,
    option,
    os,
)
from ..messages import inform, warn

_Ev = QtCore.QEvent
_Key = QtCore.Qt.Key


class NudgeBurstFilter(QtCore.QObject):
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
            warn(self, "Text Not Stored", "%s. Replace those characters and type the text again." % exc)
        except Exception as exc:  # noqa: BLE001
            warn(self, "Text Not Stored", str(exc))
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
                self.statusBar().showMessage("Updated object properties.", 4000)

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
            self.statusBar().showMessage("Updated the symbol set.", 4000)

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
            inform(
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
                self.statusBar().showMessage("Updated the template's inputs.", 4000)

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
        if option("disable_genie_forms"):
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
            group_button=option("genie_group_button"),
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
            from ..busy import wait_cursor

            with wait_cursor():
                ok = self.doc.set_genie_bindings(r, values, dialog=True)
            if ok:
                self._refresh()
                self.statusBar().showMessage("Updated genie parameters.", 4000)

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
        from ..objprops.expression import warm_tags, completion_model_for

        pdir = self._doc_project_dir()
        if not pdir:
            return None
        warm_tags(pdir)
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
            inform(
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
                _note_exc("canvas_edits._edit_composite")
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
            warn(self, "Presentation Options", str(e))
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
            inform(self, "Update Composite Genie", reason)
            return
        # (the composition runs here: the busy note says so once it takes a moment)
        try:
            with self._busy("Composing the composite genie…"):
                changed = self.doc.update_composite(root)
        except Exception as e:  # noqa: BLE001
            warn(self, "Update Composite Genie", str(e))
            return
        self._after_composite_write()
        self._refresh()
        self.statusBar().showMessage(
            "Composite genie updated with the newest template."
            if changed
            else "The composite genie is up to date.",
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
            inform(self, "Paste", str(e))
            return
        if new:
            # the pasted top-level units are selected, as they show (not every record:
            # the Objects tree and the Properties panel would work on each of them)
            self._sel = self._unit_roots(new)
            self._refresh()
            self._props_after_copy(self._sel)
            if len(self._sel) >= 10:
                # (a large paste is told over the page too, with Undo)
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
