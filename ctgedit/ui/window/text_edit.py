"""Starting an in-place text edit, and the Number tool's prompt."""

from __future__ import annotations

from ..main_window_base import QtCore, QtWidgets


class TextEditMixin:
    """F2 / Edit Text in Place, why a caption cannot be edited in place, and the Number
    tool's expression prompt."""

    # ------------------------------------------------------------ in-place text
    # (typed line breaks reach the page CRLF, as CtDraw stores them:
    # editing.crlf_text in set_text / create_object)
    def _inplace_refusal(self, index: int):
        """Why the object's caption cannot be edited in place, or None."""
        from ...model.document import ObjectKind

        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return "Select a text or a button first."
        obj = self.doc.page.objects[index]
        if obj.kind is ObjectKind.NUMBER:
            return (
                "A number shows the value of its expression. Change the "
                "expression on the Display Value tab of Object Properties."
            )
        if obj.kind not in (ObjectKind.TEXT, ObjectKind.BUTTON):
            return "Only a text or a button caption can be edited in place."
        if self.doc.page.nearest_genie(index) is not None:
            return (
                "This text is part of a genie: the genie sets it. Change "
                "the genie's parameters instead (double-click the genie)."
            )
        if obj.kind is ObjectKind.TEXT:
            try:
                mode = (
                    self.doc.parsed.page.objects[index].raw_properties().get("TXTMODE") or "0"
                ).strip() or "0"
            except Exception:  # noqa: BLE001 -- swallow-ok: no TXTMODE reads as plain text
                mode = "0"
            if mode != "0":
                return (
                    "This text shows a changing value. Change it on the "
                    "Display Value tab of Object Properties."
                )
        if self._unit_locked(self.doc.root_of(index)):
            if self._is_template_obj(self.doc.root_of(index)):
                return (
                    "This text belongs to the page template. Change it in the "
                    "template (Tools > Edit Page Template)."
                )
            return "This object is locked. Turn on Edit > Break Lock Mode to change it."
        return None

    def edit_text_in_place(self, index: int | None = None) -> bool:
        """F2 / Edit Text in Place: type the selected text's (or button's)
        caption on the page; Enter starts a new line, Ctrl+Enter or a click
        elsewhere stores it (one undo step), Esc cancels."""
        if self.doc is None:
            return False
        if index is None and self.view.text_editing():
            # F2 while typing: store the text and leave the editor
            self.view.finish_text_edit(True)
            return False
        # an open edit is stored first -- the caption read below must see it
        if self._flush_text_edit():
            self._refresh()
        if index is None:
            if len(self._sel) != 1:
                self.statusBar().showMessage(
                    "Select one text or button, then press F2 to edit its text.", 5000
                )
                return False
            index = self._sel[0]
        why = self._inplace_refusal(index)
        if why is not None:
            self.statusBar().showMessage(why, 6000)
            return False
        obj = self.doc.page.objects[index]
        g = obj.geometry
        font = self.scene._object_font(obj)
        target = {
            "kind": "edit",
            "index": index,
            "doc": self.doc,
            "po": self.doc.parsed.page.objects[index],
            "orig": str(obj.style.text or ""),
        }
        return self.view.begin_text_edit(QtCore.QPointF(g.x, g.y), target["orig"], font, target)

    def _ask_number_expression(self):
        """The Number tool's prompt: the TXT_STR ('expr' or 'expr:format') a
        new numeric display shows, or None when cancelled."""
        from ..dialog.dialogs import NumberExpressionDialog

        tags = self.doc.tag_names() if self.doc is not None else []
        dlg = NumberExpressionDialog(tags, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        return dlg.txt_str() or None
