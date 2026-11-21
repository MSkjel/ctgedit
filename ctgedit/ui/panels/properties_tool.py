"""The Properties panel with a drawing tool armed: the look of the next object the tool draws."""

from __future__ import annotations

from ..style.theme import sp as _sp
from .properties_parts import GEOMETRY_MIN_WIDTH, LINE_STYLES, panel_option
from PySide6 import QtCore, QtGui, QtWidgets


class ToolSection:
    """The Properties panel with a drawing tool armed (mixed into PropertyPanel)."""

    # -- the armed drawing tool ----------------------------------------------------------------
    def set_tool(self, tool: str) -> None:
        """The drawing tool armed now ('select' = none): with nothing selected the panel
        shows the tool's 'New <Kind>' section."""
        tool = tool or "select"
        if tool == self._tool:
            return
        self._tool = tool
        if not self._indices:
            self.set_target(self._doc, self._indices)

    def _tool_kind_key(self) -> str:
        """The armed tool when it draws an object with a style, else ''."""
        from ..editor import draw_styles as DS

        return self._tool if DS.kind_of_tool(self._tool) is not None else ""

    def refresh_tool_style(self) -> None:
        """The 'New <Kind>' section shows the look as it is now (the swatches changed)."""
        if not self._indices and self._tool_kind_key() and not self._building:
            self._rebuild()

    def _tool_label(self, tool: str) -> str:
        """The tool's name as its command says it ('Rectangle (outline)')."""
        win = self.window()
        act = (getattr(win, "tool_actions", {}) or {}).get(tool) if win is not self else None
        if isinstance(act, QtGui.QAction) and act.text():
            return act.text().replace("&", "")
        from ..editor import draw_styles as DS

        kind = DS.kind_of_tool(tool)
        name = DS.label(kind) if kind else tool.replace("_", " ").capitalize()
        return name + (" (outline)" if tool in DS.OUTLINE_TOOLS else "")

    def _build_tool_root(self, root: QtWidgets.QWidget, doc) -> None:
        """'New <Kind>': the look the armed tool gives the next object (ui/editor/draw_styles):
        each field writes the tool's own style (or, with Last used, the kind's last used
        look); a colour the tool has no own value for is the ribbon's swatch colour."""
        from ..editor import draw_styles as DS

        tool = self._tool
        kind = DS.kind_of_tool(tool)
        lay = root.layout()
        self._form_parent = root
        try:
            sec = self._section(
                "New %s" % self._tool_label(tool), extra_menu=lambda m, t=tool: self._tool_menu(m, t)
            )
            from ..help import set_help

            set_help(sec, "drawing#the-look-of-new-objects")
            self._fill_tool_section(sec.form(), doc, kind, tool)
            lay.addWidget(sec)
            note = self._dim_label(
                "The next %s you draw takes this look. The page and its "
                "properties show again when you choose Select." % DS.label(kind).lower()
            )
            note.setWordWrap(True)
            note.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            lay.addWidget(note)
        finally:
            self._form_parent = None
        self._finish_root(root)

    def _tool_menu(self, menu: QtWidgets.QMenu, tool: str) -> None:
        a = menu.addAction("Reset to the Factory Look")
        a.triggered.connect(lambda: self._reset_tool(tool))

    def _reset_tool(self, tool: str) -> None:
        from ..editor import draw_styles as DS

        if DS.mode() == "last":
            DS.clear_last(DS.kind_of_tool(tool))
            DS.set_click_size(tool)  # (the tool's own click size: the factory one)
        else:
            DS.reset_tool(tool)
        self.statusMessage.emit(
            "New %s take the factory look again." % (DS.label(DS.kind_of_tool(tool), True).lower())
        )
        QtCore.QTimer.singleShot(0, self, self.refresh)

    def _fill_tool_section(self, form: QtWidgets.QFormLayout, doc, kind: str, tool: str):
        from ..editor import draw_styles as DS

        page = doc.page if doc is not None else None
        vals = DS.shown_values(kind, tool, page)
        own = DS.own_style(kind, tool)
        fields = DS.KIND_FIELDS.get(kind, ())
        mode = QtWidgets.QComboBox()
        for value, text in DS.MODES:
            mode.addItem(text, value)
        mode.setCurrentIndex(max(0, mode.findData(DS.mode())))
        mode.setToolTip(
            "This tool's own: the look set here. Last used: the look of the "
            "last %s you changed" % DS.label(kind).lower()
        )
        mode.setProperty("propKey", "new_object_style")
        mode.activated.connect(lambda _i, c=mode: self._set_tool_mode(c.currentData()))
        form.addRow("Style for new objects", mode)
        if "line_width" in fields:
            w = self._number(1, 32, " px", int(vals.get("line_width") or 1))
            w.setProperty("propKey", "line_width")
            w.valueCommitted.connect(lambda v: self._set_new(kind, tool, "line_width", int(v)))
            form.addRow(self._scrub("Pipe width" if kind == "pipe" else "Line width", w), w)
        if "line_style" in fields:
            scb = QtWidgets.QComboBox()
            scb.addItems(LINE_STYLES)
            scb.setCurrentIndex(int(vals.get("line_style") or 0) % 5)
            scb.setProperty("propKey", "line_style")
            scb.activated.connect(lambda v: self._set_new(kind, tool, "line_style", int(v)))
            form.addRow("Line style", scb)
        if "filled" in fields:
            cb = QtWidgets.QCheckBox()
            cb.setChecked(bool(vals.get("filled")))
            cb.setProperty("propKey", "filled")
            if tool in DS.OUTLINE_TOOLS:
                cb.setEnabled(False)
                cb.setToolTip("The outline tool draws hollow shapes")
            cb.toggled.connect(lambda on: self._set_new(kind, tool, "filled", bool(on)))
            form.addRow("Filled", cb)
        for field, label, which in (
            ("fill_colour", "Fill colour", "fill"),
            ("line_colour", "Highlight colour" if kind == "pipe" else "Line colour", "line"),
        ):
            if field not in fields:
                continue
            c = vals.get(field)
            if field == "fill_colour" and c is None:
                c = DS.colours(page)["fill"]
            btn = self._swatch_button(c, title=label)
            btn.setProperty("propKey", field)
            tip = (
                "This tool's own colour"
                if field in own
                else "The ribbon's %s colour (Create > Colours)" % which
            )
            getattr(btn, "swatch", btn).setToolTip(
                "%s: %s. %s" % (label, "#%06X" % (int(c) & 0xFFFFFF) if c is not None else "none", tip)
            )
            btn.clicked.connect(
                lambda _=False, f=field, w=which, b=btn, lab=label: self._pick_new_colour(
                    kind, tool, f, w, b, lab
                )
            )
            form.addRow(label, btn)
        if "lolight_colour" in fields:
            c = own.get("lolight_colour")
            hl = vals.get("line_colour") or 0
            auto = c is None
            if auto:
                c = (hl >> 1) & 0x7F7F7F
            btn = self._swatch_button(c, title="Lowlight colour")
            btn.setProperty("propKey", "lolight_colour")
            getattr(btn, "swatch", btn).setToolTip(
                "The shaded side of the pipe. %s"
                % ("Automatic: half the highlight colour" if auto else "This tool's own")
            )
            btn.clicked.connect(
                lambda _=False, b=btn: self._pick_new_colour(
                    kind, tool, "lolight_colour", "", b, "Lowlight colour"
                )
            )
            form.addRow("Lowlight colour", btn)
        if "gradient_mode" in fields:
            from ..objprops.appearance import GRADIENTS

            g = QtWidgets.QComboBox()
            g.addItems(GRADIENTS)
            g.setCurrentIndex(min(len(GRADIENTS) - 1, int(vals.get("gradient_mode") or 0)))
            g.setProperty("propKey", "gradient_mode")
            g.activated.connect(lambda v: self._set_new(kind, tool, "gradient_mode", int(v)))
            form.addRow("Gradient", g)
            if vals.get("gradient_mode"):
                gc = vals.get("gradient_colour")
                gb = self._swatch_button(0xFFFFFF if gc is None else gc, title="Gradient colour")
                gb.setProperty("propKey", "gradient_colour")
                gb.clicked.connect(
                    lambda _=False, b=gb: self._pick_new_colour(
                        kind, tool, "gradient_colour", "", b, "Gradient colour"
                    )
                )
                form.addRow("Gradient colour", gb)
        if "corner_radius" in fields:
            r = self._number(0, 32, " px", int(vals.get("corner_radius") or 0))
            r.setProperty("propKey", "corner_radius")
            r.setToolTip("0 = square corners")
            r.valueCommitted.connect(lambda v: self._set_new(kind, tool, "corner_radius", int(v)))
            form.addRow(self._scrub("Corner radius", r), r)
        if "rectangle_style" in fields:
            rs = QtWidgets.QComboBox()
            rs.addItems(["None", "Border", "Extra line", "Border and extra line"])
            rs.setCurrentIndex(int(vals.get("rectangle_style") or 0) & 3)
            rs.setProperty("propKey", "rectangle_style")
            rs.setToolTip(
                "Border: a 1 pixel black line around it. Extra line: with a Raised or Lowered 3D effect"
            )
            rs.activated.connect(lambda v: self._set_new(kind, tool, "rectangle_style", int(v)))
            form.addRow("Border", rs)
        if "font" in fields:
            fc = QtWidgets.QFontComboBox()
            fc.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
            face = str(vals.get("font") or "Arial")
            fc.setCurrentFont(QtGui.QFont(face))
            fc.setEditText(face)
            fc.setProperty("orig", face)
            fc.setProperty("propKey", "font")
            fc.activated.connect(lambda pos, c=fc: self._new_font(kind, tool, c, c.itemText(pos)))
            fc.lineEdit().editingFinished.connect(lambda c=fc: self._new_font(kind, tool, c, c.currentText()))
            form.addRow("Font", fc)
            sz = self._number(4, 96, " pt", int(vals.get("font_size") or 10))
            sz.setProperty("propKey", "font_size")
            sz.valueCommitted.connect(lambda v: self._set_new(kind, tool, "font_size", int(v)))
            form.addRow(self._scrub("Size", sz), sz)
            form.addRow(
                "Style",
                self._style_row(
                    {k: bool(vals.get(k)) for k in ("bold", "italic", "underline", "strikeout")},
                    vals.get("justify") or "left",
                    lambda flag, on: self._set_new(kind, tool, flag, bool(on)),
                    lambda j: self._set_new(kind, tool, "justify", j),
                ),
            )
        if "text_colour" in fields:
            c = vals.get("text_colour")
            btn = self._swatch_button(c, title="Text colour")
            btn.setProperty("propKey", "text_colour")
            getattr(btn, "swatch", btn).setToolTip(
                "Text colour: %s. %s"
                % (
                    "#%06X" % (int(c) & 0xFFFFFF) if c is not None else "none",
                    "This tool's own colour"
                    if "text_colour" in own
                    else "The ribbon's text colour (Create > Colours)",
                )
            )
            btn.clicked.connect(
                lambda _=False, b=btn: self._pick_new_colour(
                    kind, tool, "text_colour", "text", b, "Text colour"
                )
            )
            form.addRow("Text colour", btn)
        if kind == "button" and tool in DS.CLICK_SIZES:
            form.addRow("Size of a click", self._click_size_row(tool))
        if kind == "button":
            xp = QtWidgets.QCheckBox()
            xp.setChecked(panel_option("xp_buttons"))
            xp.setProperty("propKey", "xp_buttons")
            xp.setToolTip("Off: the classic raised look (also Settings > Editing > New objects)")
            xp.toggled.connect(self._set_xp_buttons)
            form.addRow("XP style", xp)
        reset = self._link("Reset to the factory look", -1, "", slot=lambda t=tool: self._reset_tool(t))
        reset.setToolTip(
            "Lines 1 px, solid shapes, Arial text and the ribbon's colours"
            + (
                ". A click makes a %d x %d px button again" % DS.CLICK_SIZES[tool]
                if kind == "button" and tool in DS.CLICK_SIZES
                else ""
            )
        )
        form.addRow(reset)

    def _click_size_row(self, tool: str) -> QtWidgets.QHBoxLayout:
        """'Size of a click': W and H of the box a click (no drag) with ``tool`` makes (its
        own size in draw/style: ui/editor/draw_styles.click_size). A layout, as the geometry rows."""
        from ..editor import draw_styles as DS

        lo, hi = DS.CLICK_SIZE_RANGE
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(_sp(1))
        for key, value in zip(("w", "h"), DS.click_size(tool)):
            sp = self._number(lo, hi, " px", int(value))
            sp.setProperty("propKey", "click_" + key)
            sp.setMinimumWidth(GEOMETRY_MIN_WIDTH)
            sp.setToolTip(
                "The %s of the box a click makes. Drag to draw any other size"
                % ("width" if key == "w" else "height")
            )
            sp.valueCommitted.connect(lambda v, k=key: self._set_click_size(tool, k, int(v)))
            lab = self._scrub(key.upper(), sp)
            lab.setMinimumWidth(lab.fontMetrics().horizontalAdvance("W") + 2)
            row.addWidget(lab)
            row.addWidget(sp, 1)
        return row

    def _set_click_size(self, tool: str, key: str, value: int) -> None:
        from ..editor import draw_styles as DS

        if self._building:
            return
        w, h = DS.click_size(tool)
        DS.set_click_size(tool, value if key == "w" else w, value if key == "h" else h)

    def _set_tool_mode(self, value: str) -> None:
        from ..editor import draw_styles as DS

        DS.set_mode(value)
        QtCore.QTimer.singleShot(0, self, self.refresh)

    def _set_new(self, kind: str, tool: str, field: str, value) -> None:
        """One field of the look of the next object (the tool's own, or the last used)."""
        from ..editor import draw_styles as DS

        if self._building:
            return
        if DS.mode() == "last":
            DS.set_last_field(kind, field, value)
        else:
            DS.set_tool_field(tool, field, value)
        QtCore.QTimer.singleShot(0, self, self.refresh)

    def _pick_new_colour(self, kind: str, tool: str, field: str, which: str, btn, title: str) -> None:
        """A colour row of 'New <Kind>': the tool's own colour when it has one (or a field
        only the tool keeps), else the ribbon's swatch colour."""
        from .. import colour_picker
        from ..editor import draw_styles as DS

        if self._building:
            return
        page = self._doc.page if self._doc is not None else None
        own = DS.own_style(kind, tool)
        cur = getattr(btn, "swatch", btn).property("packed")
        got = colour_picker.choose_colour(
            self,
            None if cur is None else (int(cur), int(cur)),
            title=title,
            flash=False,
            transparent=False,
            anchor=btn if isinstance(btn, QtWidgets.QWidget) else None,
            focus_entry=self._take_entry_focus(),
        )
        if got is None:
            return
        c = int(got[0]) & 0xFFFFFF
        if field in own or not which:
            self._set_new(kind, tool, field, c)
            return
        DS.set_colour(which, c, page)
        win = self.window()
        if win is not self and hasattr(win, "_update_colour_button"):
            win._update_colour_button()
        QtCore.QTimer.singleShot(0, self, self.refresh)

    def _new_font(self, kind: str, tool: str, combo, text: str) -> None:
        if self._building:
            return
        stored = str(combo.property("orig") or "")
        if self._font_unchanged(combo, text, stored):
            return
        fam = self._resolve_font(combo, text, stored)
        if fam is None or fam == stored:
            return
        combo.setProperty("orig", fam)
        self._set_new(kind, tool, "font", fam)

    @staticmethod
    def _set_xp_buttons(on: bool) -> None:
        from ..prefs.settings import set_option

        set_option("xp_buttons", bool(on))
