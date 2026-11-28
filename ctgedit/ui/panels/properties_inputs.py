"""The Properties panel's genie sections: a genie's Inputs (each value the genie is given and
the fields of its objects the value reaches, through the genies nested in it:
EditableDocument.genie_inputs) and an object's Set by the Genie (the fields of an object in a
genie that its inputs set, each with the template it is made from)."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

#: the rows the list shows before it scrolls
VISIBLE_ROWS = 12
#: the characters a use shows before the input's value in it
_LEAD = 12
_INDEX = QtCore.Qt.UserRole


def _one_line(text) -> str:
    return " ".join(str(text or "").split())


def use_text(label: str, value: str, given: str) -> str:
    """'Button: command = …OnClick("TopBar")…': a use's field and the value it holds on one
    line, from a little before where the input's value ``given`` lands in it."""
    v = _one_line(value)
    at = v.find(_one_line(given)) if given else -1
    if at > _LEAD:
        v = "…" + v[at - _LEAD + 1 :]
    return "%s = %s" % (label, v) if v else label


def inputs_section(panel, index: int):
    """The Inputs section of genie ``index`` (filled when first unfolded), None for an object
    whose inputs are not known (no placed genie, a composite genie)."""
    fn = getattr(panel._doc, "genie_inputs", None)
    if fn is None:
        return None
    try:
        rows = fn(index)
    except Exception:  # noqa: BLE001
        from ...infra.logsetup import note_exception

        note_exception("properties_inputs.inputs_section", index=index)
        return None
    if not rows:
        return None
    box = panel._section("Inputs")
    box.setToolTip("What each value the genie is given goes into. Double-click a use to select its object.")

    def fill():
        tree = InputsTree(panel._doc.page, index, rows)
        tree.objectsChosen.connect(panel.selectObjects.emit)
        box.form().addRow(tree)

    box.set_lazy(fill)
    return box


class FitTree(QtWidgets.QTreeWidget):
    """A list as high as the rows it shows, up to VISIBLE_ROWS (then it scrolls)."""

    def _shown_rows(self) -> int:
        n = 0
        for k in range(self.topLevelItemCount()):
            top = self.topLevelItem(k)
            n += 1 + (top.childCount() if top.isExpanded() else 0)
        return n

    def _fit(self) -> None:
        rows = max(1, min(self._shown_rows(), VISIBLE_ROWS))
        # (before the list is laid out its rows report no height: the font's then)
        h = max(self.sizeHintForRow(0), self.fontMetrics().height() + 6)
        self.setFixedHeight(self.header().sizeHint().height() + rows * h + 2 * self.frameWidth())

    def showEvent(self, ev):  # noqa: N802 -- Qt naming
        super().showEvent(ev)
        self._fit()


class InputsTree(FitTree):
    """One row an input (its name and value) with a row under it for each field it reaches
    (the object and field, the value the field holds: :func:`use_text`); the copies of one
    object in nested genies share a row."""

    objectsChosen = QtCore.Signal(list)

    def __init__(self, page, index: int, rows, parent=None):
        super().__init__(parent)
        self.setObjectName("genieInputs")
        self.setColumnCount(2)
        self.setHeaderLabels(["Input", "Value"])
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setTextElideMode(QtCore.Qt.ElideRight)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        head = self.header()
        head.setStretchLastSection(True)
        head.setSectionsMovable(False)
        dim = self.palette().brush(QtGui.QPalette.PlaceholderText)
        from ..names import element_label, plural

        vias: dict = {}

        def genie_of(u) -> str:
            """The element of the nested genie use ``u`` is in ('' for the genie's own)."""
            if u.via == index or page is None:
                return ""
            if u.via not in vias:
                vias[u.via] = element_label(page.objects[u.via], page, short=False)
            return vias[u.via]

        def where(u) -> str:
            el = genie_of(u)
            return "object %d in genie %s" % (u.index, el) if el else "object %d" % u.index

        names_w = 0
        for name, value, uses in rows:
            top = QtWidgets.QTreeWidgetItem([str(name), _one_line(value)])
            n = len({u.index for u in uses})
            top.setToolTip(
                0, "%s goes into %s." % (name, plural(n, "object")) if uses else "Nothing uses %s." % name
            )
            top.setToolTip(1, str(value) or "No value")
            self.addTopLevelItem(top)
            names_w = max(names_w, self.fontMetrics().horizontalAdvance(str(name)))
            if not uses:
                none = QtWidgets.QTreeWidgetItem(["Not used by its objects"])
                none.setForeground(0, dim)
                none.setFlags(QtCore.Qt.ItemIsEnabled)
                top.addChild(none)
                none.setFirstColumnSpanned(True)
                continue
            # (one row a field: the copies of one object, the same place in genies of one
            # element, share it)
            fields: dict = {}
            for u in uses:
                fields.setdefault((u.label, genie_of(u), u.index - u.via), []).append(u)
            for (label, _el, _at), us in fields.items():
                shown = "%s (%d)" % (label, len(us)) if len(us) > 1 else label
                it = QtWidgets.QTreeWidgetItem([use_text(shown, us[0].value, value)])
                it.setData(0, _INDEX, [u.index for u in us])
                if len(us) > 1:
                    tip = "%s, %d fields. The first, %s:\n%s\n\nDouble-click to select them."
                    it.setToolTip(0, tip % (label, len(us), where(us[0]), us[0].value or "Empty"))
                else:
                    tip = "%s, %s:\n%s\n\nDouble-click to select it."
                    it.setToolTip(0, tip % (label, where(us[0]), us[0].value or "Empty"))
                top.addChild(it)
                it.setFirstColumnSpanned(True)
        # (the names' column as wide as the names, the branch marks and a gap)
        self.setColumnWidth(0, min(names_w + self.indentation() + 24, 160))
        self.itemActivated.connect(self._chosen)
        self.itemExpanded.connect(lambda _it: self._fit())
        self.itemCollapsed.connect(lambda _it: self._fit())
        self._fit()

    def _chosen(self, item, _col) -> None:
        got = item.data(0, _INDEX)
        if got:
            self.objectsChosen.emit(sorted(set(got)))


# --------------------------------------------------------------------------- an object's side
def object_sources(doc, index: int):
    """(the outermost placed genie object ``index`` is in, the fields of it that genie's
    inputs set: genie_uses.InputUse with the template in the genie's inputs, the genie's
    values by lower-case name); None outside a placed genie or in a composite genie."""
    from ...features.genie_uses import object_sources as _sources

    root, uses = _sources(doc.parsed.page.objects, doc.page, index)
    if root is None or root == index or not uses:
        return None
    try:
        if doc.is_composite_genie(root):
            return None
        values = {k.lower(): v for k, v in doc.genie_values(root)}
    except Exception:  # noqa: BLE001
        from ...infra.logsetup import note_exception

        note_exception("properties_inputs.object_sources", index=index)
        return None
    return root, uses, values


def sources_section(panel, index: int):
    """The Set by the Genie section of an object in a placed genie: each field the genie's
    inputs set, with the template it is made from and the inputs in it. None when none."""
    try:
        got = object_sources(panel._doc, index)
    except Exception:  # noqa: BLE001
        from ...infra.logsetup import note_exception

        note_exception("properties_inputs.sources_section", index=index)
        return None
    if got is None:
        return None
    root, uses, values = got
    box = panel._section("Set by the genie")
    box.setToolTip(
        "The fields of this object the genie's inputs set. Double-click an input to select the genie."
    )

    def fill():
        from ..names import element_label

        tree = SourcesTree(uses, values, root, element_label(panel._doc.page.objects[root], panel._doc.page))
        tree.objectsChosen.connect(panel.selectObjects.emit)
        box.form().addRow(tree)

    box.set_lazy(fill)
    return box


class SourcesTree(FitTree):
    """One row a field (its label and the value it holds) with rows under it: the template it
    is made from, then each input in it with the input's value."""

    objectsChosen = QtCore.Signal(list)

    def __init__(self, uses, values: dict, root: int, genie: str, parent=None):
        super().__init__(parent)
        self.setObjectName("genieSources")
        self.setColumnCount(2)
        self.setHeaderLabels(["Field", "Value"])
        self.setUniformRowHeights(True)
        self.setTextElideMode(QtCore.Qt.ElideRight)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.header().setStretchLastSection(True)
        dim = self.palette().brush(QtGui.QPalette.PlaceholderText)
        widest = 0
        for u in uses:
            field = u.label.split(": ", 1)[1] if ": " in u.label else u.label
            top = QtWidgets.QTreeWidgetItem([field[:1].upper() + field[1:], _one_line(u.value)])
            top.setToolTip(0, u.label)
            top.setToolTip(1, u.value or "Empty")
            self.addTopLevelItem(top)
            widest = max(widest, self.fontMetrics().horizontalAdvance(top.text(0)))
            tmpl = QtWidgets.QTreeWidgetItem(["From " + _one_line(u.template)])
            tmpl.setForeground(0, dim)
            tmpl.setToolTip(0, "The field in the inputs of genie %s:\n%s" % (genie, u.template))
            tmpl.setFlags(QtCore.Qt.ItemIsEnabled)
            top.addChild(tmpl)
            tmpl.setFirstColumnSpanned(True)
            for name in u.names():
                v = values.get(name.lower())
                it = QtWidgets.QTreeWidgetItem([name, _one_line(v) if v else "Empty"])
                # (a child row: one more step in)
                widest = max(widest, self.fontMetrics().horizontalAdvance(name) + self.indentation())
                it.setData(0, _INDEX, [root])
                if not v:
                    it.setForeground(1, dim)
                it.setToolTip(0, "Input %s of genie %s. Double-click to select the genie." % (name, genie))
                it.setToolTip(1, v or "The input is empty")
                top.addChild(it)
            top.setExpanded(len(uses) <= 3)
        self.setColumnWidth(0, min(widest + self.indentation() + 24, 180))
        self.itemActivated.connect(self._chosen)
        self.itemExpanded.connect(lambda _it: self._fit())
        self.itemCollapsed.connect(lambda _it: self._fit())
        self._fit()

    def _chosen(self, item, _col) -> None:
        got = item.data(0, _INDEX)
        if got:
            self.objectsChosen.emit(list(got))
