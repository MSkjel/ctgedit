"""The Properties panel's search: matches marked in the form, links to the Object Properties pages that have it."""

from __future__ import annotations

import contextlib
from ..style.theme import sp as _sp
from .properties_parts import PAGE_WORDS, PanelSection, panel_is_field
from .properties_widgets import AddAnimationCombo, sheet_labels
from PySide6 import QtCore, QtWidgets


class PanelSearch:
    """The Properties panel's search (mixed into PropertyPanel)."""

    # -- the property search ------------------------------------------------------------------
    def focus_search(self) -> None:
        """Ctrl+F in the panel: the keyboard in the property search."""
        self.search.setFocus(QtCore.Qt.ShortcutFocusReason)
        self.search.selectAll()

    def _on_search(self, _text: str = "") -> None:
        self._apply_search()

    @staticmethod
    def _texts_of(item) -> list:
        """The words a form row is found by: its widgets' texts, values and keys."""
        out = []
        if item is None:
            return out
        w = item.widget() if hasattr(item, "widget") else None
        lay = item.layout() if hasattr(item, "layout") else None
        widgets = []
        if w is not None:
            widgets = [w] + w.findChildren(QtWidgets.QWidget)
        elif lay is not None:
            stack = [lay]
            while stack:
                cur = stack.pop()
                for k in range(cur.count()):
                    it = cur.itemAt(k)
                    if it.widget() is not None:
                        widgets.append(it.widget())
                        widgets.extend(it.widget().findChildren(QtWidgets.QWidget))
                    elif it.layout() is not None:
                        stack.append(it.layout())
        import re

        for x in widgets:
            try:
                key = x.property("propKey")
                if key:
                    out.append(str(key))
                if isinstance(x, QtWidgets.QLineEdit):
                    if not isinstance(x.parentWidget(), (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)):
                        out.append(x.text())
                elif isinstance(x, QtWidgets.QAbstractSpinBox):
                    out.append(x.text())
                elif isinstance(x, QtWidgets.QComboBox):
                    out.append(x.currentText())
                elif isinstance(x, QtWidgets.QLabel):
                    out.append(re.sub(r"<[^>]+>", " ", x.text()))
                elif isinstance(x, QtWidgets.QAbstractButton):
                    out.append(x.text() or x.accessibleName())
                elif isinstance(x, QtWidgets.QPlainTextEdit):
                    out.append(x.toPlainText()[:200])
            except RuntimeError:  # swallow-ok: a widget deleted while we looked
                continue
        return out

    def _mark_hit(self, item, on: bool) -> None:
        w = item.widget() if item is not None else None
        targets = []
        if w is not None:
            targets = [w]
        elif item is not None and item.layout() is not None:
            lay = item.layout()
            targets = [
                lay.itemAt(k).widget()
                for k in range(lay.count())
                if lay.itemAt(k).widget() is not None and panel_is_field(lay.itemAt(k).widget())
            ]
        for t in targets:
            if bool(t.property("searchHit")) == on:
                continue
            t.setProperty("searchHit", on)
            t.style().unpolish(t)
            t.style().polish(t)

    def _apply_search(self) -> None:
        """Show the rows whose label, key or value has the search text (their fields get the
        accent outline), hide the sections without one, and list the Object Properties
        pages that have it ('In Object Properties'). An empty search shows every row."""
        root = self.widget()
        self._clear_search_box()
        if root is None:
            return
        text = self.search.text().strip()
        m = self.search.matcher(text)
        searching = bool(text) and not m.empty
        root.setProperty("ctgSearched", searching)
        L = QtWidgets.QFormLayout
        for sec in root.findChildren(PanelSection):
            if searching and not sec.is_filled():
                sec._fill_now()
        any_hit = False
        for form in root.findChildren(QtWidgets.QFormLayout):
            owner = form.parentWidget()
            sec = owner if isinstance(owner, PanelSection) and owner.layout() is form else None
            title_hit = searching and sec is not None and m(sec.title().lower())
            hits = 0
            for r in range(form.rowCount()):
                if sec is not None and r == 0:
                    continue  # (the header)
                label = form.itemAt(r, L.LabelRole)
                field_ = form.itemAt(r, L.FieldRole) or form.itemAt(r, L.SpanningRole)
                if not searching:
                    self._mark_hit(field_, False)
                    if sec is None or not sec.folded():
                        form.setRowVisible(r, True)
                    continue
                hay = " ".join(self._texts_of(label) + self._texts_of(field_)).lower()
                hit = title_hit or (bool(hay.strip()) and m(hay))
                form.setRowVisible(r, bool(hit))
                self._mark_hit(field_, bool(hit) and not title_hit)
                hits += bool(hit)
            if sec is not None:
                if searching:
                    sec.setVisible(bool(hits))
                    sec.setProperty("ctgSearchHidden", not hits)
                    if hits:
                        sec.header.chevron.setArrowType(QtCore.Qt.DownArrow)
                elif sec.property("ctgSearchHidden"):
                    sec.setVisible(True)
                    sec.setProperty("ctgSearchHidden", False)
                if not searching:
                    sec.sync_fold()
            any_hit = any_hit or bool(hits)
        for combo in root.findChildren(AddAnimationCombo):
            combo.setVisible(not searching)
        if searching:
            self._show_search_box(root, m, any_hit)

    def _clear_search_box(self) -> None:
        box, self._search_box = self._search_box, None
        if box is not None:
            with contextlib.suppress(RuntimeError):
                box.hide()
                box.setParent(None)
                box.deleteLater()

    def _sheet_hits(self, m) -> list:
        """(page id, 'Tab > Sub') of the selected object's Object Properties pages a search
        finds: by the page's title, its fields' labels and its words."""
        if self._doc is None or len(self._indices) != 1:
            return []
        try:
            from ..objprops import registry as R

            ctx = R.SheetContext.for_object(self._doc, self._indices[0])
            pages = R.pages_for(ctx)
        except Exception:  # noqa: BLE001  # swallow-ok: no sheet known, no links
            return []
        labels = sheet_labels()
        out = []
        for p in pages:
            keys = [k.rstrip("*") for k in p.keys]
            words = [p.tab, p.sub, PAGE_WORDS.get(p.id, "")]
            words += [labels.get(k.upper(), "") for k in keys] + [k for k in p.keys]
            if p.id in ("access",):
                words.append("tool tip")
            hay = " ".join(words).lower()
            if m(hay):
                title = "%s > %s" % (p.tab, p.sub)
                if (p.id, title) not in out:
                    out.append((p.id, title))
        return out

    def _show_search_box(self, root, m, any_hit: bool) -> None:
        hits = self._sheet_hits(m)
        if not hits and any_hit:
            return
        box = QtWidgets.QWidget(root)
        box.setObjectName("searchSheetHits")
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        v.setSpacing(_sp(0))
        if hits:
            v.addWidget(self._dim_label("In Object Properties"))
            index = self._indices[0]
            for page_id, title in hits:
                v.addWidget(self._link(title, index, page_id))
        else:
            v.addWidget(self._dim_label("No property matches the search."))
        lay = root.layout()
        at = lay.count()
        if at and lay.itemAt(at - 1).spacerItem() is not None:
            at -= 1  # (before the stretch)
        lay.insertWidget(at, box)
        self._search_box = box
