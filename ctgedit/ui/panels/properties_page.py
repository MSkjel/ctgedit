"""The Properties panel's page form: page size, background, details and the page's record, kept per document."""

from __future__ import annotations

from ...infra.logsetup import note_exception as _note_exc

import contextlib
import weakref
from ..number_field import NumberField, ScrubLabel
from ..style.theme import sp as _sp
from .properties_parts import PAGE_RECORD_ROWS, PanelSection, panel_ALL_AREAS, panel_option
from .properties_widgets import AreaField, ScanTimeSpin
from PySide6 import QtCore, QtGui, QtWidgets


class PageForm:
    """The Properties panel's page form (mixed into PropertyPanel)."""

    def _prune_page_forms(self) -> None:
        """Drop the page forms of documents that are gone (closed), and all of them when
        no document is shown (the last page was closed)."""
        for k, (ref, _key, root) in list(self._page_forms.items()):
            if self._doc is None or ref() is None:
                del self._page_forms[k]
                self._drop_root(root)
        kept_for = self._kept_for
        if self._kept_forms and (kept_for is None or self._doc is None or kept_for[0] != id(self._doc)):
            self.drop_kept_forms()

    def page_form_count(self) -> int:
        """How many page forms the panel keeps (tests)."""
        return len(self._page_forms)

    # -- page mode ------------------------------------------------------------------------
    def _page_key(self):
        """What the page form shows (None: not known, build it again)."""
        doc = self._doc
        try:
            from ...editing import ElementDocument

            ap = doc.page.appearance
            key = [
                id(doc),
                doc.path,
                doc.page.name,
                ap.width,
                ap.height,
                ap.background_colour,
                getattr(doc, "edit_serial", 0),
            ]
            if isinstance(doc, ElementDocument):
                key += [
                    "element",
                    doc.kind,
                    doc.element_name,
                    doc.page_sized,
                    getattr(doc, "derive_serial", 0),
                ]
            else:
                avail = bool(doc.page_record_available())
                rec = (doc.page_record() or {}) if avail else {}
                key += [
                    avail,
                    tuple(sorted((k, v) for k, v in rec.items())),
                    doc._page_record_name() if avail else "",
                ]
                import os
                from ...io import project as P

                pdir = os.path.dirname(os.path.abspath(doc.path)) if avail and doc.path else None
                key.append(tuple(P.chain_page_names(pdir) or ()) if pdir else None)
            return tuple(key)
        except Exception:  # noqa: BLE001  # swallow-ok: no key, the form is built again
            return None

    def _page_root(self) -> QtWidgets.QWidget:
        """The page form of the document, kept (in the stack) while what it shows is the
        same; a form not kept (its key not known) is an object root, dropped after use."""
        doc = self._doc
        key = self._page_key()
        ent = self._page_forms.get(id(doc))
        if ent is not None and ent[0]() is doc and key is not None and ent[1] == key:
            with contextlib.suppress(RuntimeError):
                for form in ent[2].findChildren(QtWidgets.QFormLayout):
                    self.style_form(form)
            return ent[2]
        if ent is not None:
            del self._page_forms[id(doc)]
            if ent[2] is not self._stack.currentWidget():
                self._drop_root(ent[2])
            else:
                self._object_root = ent[2]  # (shown now: dropped once the new one shows)
        # (in the panel, hidden, before its rows: a form built on its own and then moved
        # in has every widget styled once more)
        root = self._new_root()
        self._stack.addWidget(root)
        try:
            w = QtWidgets.QWidget(root)
            lay = QtWidgets.QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(_sp(1))
            root.layout().addWidget(w)
            self._form_parent = w
            try:
                self._build_page(lay)
            finally:
                self._form_parent = None
            self._sweep_strays(w)
            self._finish_root(root)
        except BaseException:
            self._drop_root(root)
            raise
        if key is not None:
            root.layout().addStretch(1)
            self._page_forms[id(doc)] = (weakref.ref(doc), key, root)
        return root

    def _page_action(self):
        """The window's Page Properties… action (the menu's own QAction), or None."""
        win = self.window()
        act = getattr(win, "act_page_props", None) if win is not self else None
        return act if isinstance(act, QtGui.QAction) else None

    def _build_page(self, lay: QtWidgets.QVBoxLayout) -> None:
        doc = self._doc
        from ...editing import ElementDocument

        element = isinstance(doc, ElementDocument)
        box = PanelSection(
            doc.kind.replace("supergenie", "super genie").title() if element else "Page",
            self,
            parent=self._form_parent,
        )
        form = box.form()
        ap = doc.page.appearance
        if element:
            import os

            form.addRow("Name", self._ro_label(doc.element_name))
            lib = self._ro_label(os.path.basename(doc.path))
            lib.setToolTip(doc.path)
            form.addRow("Library", lib)
            if doc.page_sized:
                form.addRow("Size", self._ro_label("%d × %d" % (ap.width, ap.height)))
            else:
                box_ = doc.selection_extent([i for i, o in enumerate(doc.page.objects) if o.parent is None])
                if box_ is not None:
                    form.addRow("Size", self._ro_label("%d × %d" % (box_[2] - box_[0], box_[3] - box_[1])))
            if doc.kind in ("genie", "supergenie"):
                params = self._element_params(doc)
                txt = ", ".join(("%s = %s" % (k, v)) if v else k for k, v in params.items())
                lbl = self._ro_label(txt)
                lbl.setWordWrap(True)
                lbl.setToolTip("The parameters a paste of this genie asks for")
                form.addRow("Parameters", lbl)
        else:
            form.addRow("Name", self._ro_label(doc.page.name or ""))
            self._add_page_size(form, doc)
        from ..colour_picker import ColourButton

        bg = ap.background_colour
        # the 0xFF000000 sentinel = no colour of its own (an element without
        # @<BackgroundColor>): shown as none, not as the black its low bytes would give
        unset = bg is None or (int(bg) >> 24) & 0xFF == 0xFF
        btn = ColourButton(
            None if unset else int(bg) & 0xFFFFFF, title="Page Colour", flash=False, transparent=False
        )
        if unset:
            btn.setToolTip("No colour of its own (the window's default)")
        btn.changed.connect(lambda b=btn: self._on_page_background(b))
        form.addRow("Page colour", btn)
        lay.addWidget(box)
        if not element:
            lay.addWidget(self._page_record_box(doc))
            act = self._page_action()
            if act is not None:
                # the menu's own action (File > Page Properties…): its text, its state
                row = QtWidgets.QHBoxLayout()
                row.setContentsMargins(0, _sp(1), 0, 0)
                b = QtWidgets.QPushButton(act.text().replace("&", ""))
                b.setObjectName("pagePropertiesButton")
                b.setToolTip("Every property of the page")
                b.clicked.connect(lambda _=False, a=act: a.trigger())
                row.addWidget(b)
                row.addStretch(1)
                lay.addLayout(row)

    def _add_page_size(self, form, doc) -> None:
        """Width and Height (Page Properties > Appearance's view area: doc.set_page_size,
        one undo step) with a note when the size is not the template's."""
        ap = doc.page.appearance
        fields = []
        # (a row each, as the panel's other numbers: side by side, a narrow panel at a large
        # text size cut the fields and their units)
        for key, label, val in (("w", "Width", ap.width), ("h", "Height", ap.height)):
            sp = NumberField(lo=16, hi=32767, suffix=" px", value=int(val or 0))
            sp.setProperty("propKey", "page_" + key)
            sp.setToolTip("The %s of the page at runtime" % ("width" if key == "w" else "height"))
            lab = ScrubLabel(label, sp, enabled=panel_option("props_scrub"))
            form.addRow(lab, sp)
            fields.append(sp)
        for sp in fields:
            sp.valueCommitted.connect(
                lambda _v, a=fields[0], b=fields[1]: self._on_page_size(a.value(), b.value())
            )
        tsize = self._template_size(doc)
        if tsize is not None and tsize != (int(ap.width or 0), int(ap.height or 0)):
            note = self._dim_label("The template is %d × %d." % tsize)
            note.setWordWrap(True)
            note.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            form.setWidget(form.rowCount(), QtWidgets.QFormLayout.FieldRole, note)

    def _on_page_size(self, width: int, height: int) -> None:
        if self._building or self._doc is None or self._doc.page is None:
            return
        ap = self._doc.page.appearance
        if (int(width), int(height)) == (int(ap.width or 0), int(ap.height or 0)):
            return
        try:
            self._doc.set_page_size(int(width), int(height))
        except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
            self.statusMessage.emit(str(exc))
            QtCore.QTimer.singleShot(0, self, self.refresh)
            return
        self.changed.emit()

    def _page_record_box(self, doc) -> QtWidgets.QGroupBox:
        """'Page details': the page's own fields of its pages.DBF row, as
        Page Properties > General stages them (EditableDocument.
        set_page_record; written when the page is saved)."""
        from ...editing import PAGE_FIELD_LIMITS
        from ...io import project as P
        from ..pick_fields import PageField

        # (the page's own fields: 'Page details', not a third 'Page properties' next to the
        # Page section and the Page Properties window)
        pbox = PanelSection("Page details", self, parent=self._form_parent)
        pform = pbox.form()
        avail = False
        try:
            avail = bool(doc.page_record_available())
        except Exception:  # noqa: BLE001
            _note_exc("properties_page._page_record_box/available")
            avail = False
        rec = (doc.page_record() or {}) if avail else {}
        widths = doc.page_record_widths() if avail else {}

        def limit(col):
            return int(widths.get(col) or PAGE_FIELD_LIMITS.get(col) or 0)

        import os

        pdir = os.path.dirname(os.path.abspath(doc.path)) if avail and doc.path else None
        own = ""
        try:
            own = doc._page_record_name() if avail else ""
        except Exception:  # noqa: BLE001
            _note_exc("properties_page._page_record_box/name")
            own = doc.page.name or ""
        widgets = []
        for col, label in PAGE_RECORD_ROWS:
            value = (rec.get(col) or "").strip() if col != "COMMENT" else rec.get(col) or ""
            if col == "AREA":
                w = AreaField()
                w.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
                w.setMinimumContentsLength(6)
                w.setEditable(True)
                w.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
                w.addItem(panel_ALL_AREAS)  # (the areas 1 to 255: AreaField.fill)
                w.lineEdit().setMaxLength(max(limit(col), len(value)))
                w.setEditText(value or panel_ALL_AREAS)
                w.setProperty("orig", value or panel_ALL_AREAS)
                w.setToolTip(
                    "Only operators with access to this area can see the page (1 to 255, or an area name)"
                )
                w.lineEdit().editingFinished.connect(
                    lambda c=col, cb=w: self._on_page_record(c, self._area_value(cb))
                )
                w.activated.connect(lambda _i, c=col, cb=w: self._on_page_record(c, self._area_value(cb)))
            elif col == "SCANTIME":
                w = ScanTimeSpin(P.default_scan_time())
                w.setRange(-1, 60000)
                w.setSingleStep(50)
                w.setSpecialValueText("Default")
                w.setSuffix(" ms")
                try:
                    w.load(int(value) if value else -1)
                except ValueError:
                    w.load(-1)
                w.setToolTip(
                    "How often the page is updated. Default: the project's page "
                    "scan time (%s ms)" % P.default_scan_time()
                )
                w.editingFinished.connect(
                    lambda c=col, sp=w: self._on_page_record(c, "" if sp.value() < 0 else str(sp.value()))
                )
            elif col in ("PREV_PAGE", "NEXT_PAGE", "PARENT"):
                w = PageField(pdir, value=value, exclude=own, limit=limit(col))
                w.setProperty("orig", value)
                w.setToolTip(
                    {
                        "PREV_PAGE": "The page PagePrev() opens from this page",
                        "NEXT_PAGE": "The page PageNext() opens from this page",
                        "PARENT": "The parent page in the page hierarchy",
                    }[col]
                )
                w.lineEdit().editingFinished.connect(lambda c=col, cb=w: self._on_page_record(c, cb.value()))
                w.activated.connect(lambda _i, c=col, cb=w: self._on_page_record(c, cb.value()))
            elif col == "CON_TYPE":
                from ..dialog.page_props import content_type_combo

                # (its list is read when it is first used: the form is built again
                # after every edit with nothing selected)
                w = content_type_combo(pdir, value, limit(col), lazy=True)
                w.setProperty("orig", value)
                w.lineEdit().editingFinished.connect(
                    lambda c=col, cb=w: self._on_page_record(c, cb.currentText().strip())
                )
                w.activated.connect(lambda _i, c=col, cb=w: self._on_page_record(c, cb.currentText().strip()))
            else:
                w = self._line(value)
                w.setMaxLength(max(limit(col), len(value)))
                if col == "COMMENT" and "\n" in value:
                    w.setReadOnly(True)
                    w.setToolTip("Edit a description of several lines in Page Properties")
                elif col == "TITLE":
                    w.setToolTip("The window title at runtime (at most %d characters)" % limit(col))
                w.editingFinished.connect(
                    lambda c=col, e=w: None if e.isReadOnly() else self._on_page_record(c, e.text())
                )
            w.setProperty("propKey", col)
            pform.addRow(label, w)
            widgets.append(w)
        if not avail:
            for w in widgets:
                w.setEnabled(False)
            note = self._dim_label(
                "This page is not in a project folder, so these properties cannot be stored."
            )
            note.setWordWrap(True)
            note.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            pform.addRow(note)
        return pbox

    def _on_page_background(self, btn) -> None:
        if self._building or self._doc is None or self._doc.page is None:
            return
        pair = btn.pair() if hasattr(btn, "pair") else None
        if pair is None:
            return
        try:
            self._doc.set_page_background(pair[0] & 0xFFFFFF)
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        self.changed.emit()

    def _on_page_record(self, column: str, value: str) -> None:
        """Stage a pages.DBF field (Page Properties' rule: one undo step,
        written when the page is saved)."""
        if self._building or self._doc is None:
            return
        cur = (self._doc.page_record() or {}).get(column, "") or ""
        if value.strip() == cur.strip():
            return
        try:
            ok = self._doc.set_page_record({column: value})
        except ValueError as exc:
            self.statusMessage.emit(str(exc))
            QtCore.QTimer.singleShot(0, self, self.refresh)  # show the stored value again
            return
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit("Could not set page property %s: %s" % (column, exc))
            return
        if ok:
            self.changed.emit()
