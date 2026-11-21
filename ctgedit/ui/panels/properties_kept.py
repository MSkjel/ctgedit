"""The object forms the Properties panel keeps: a selection that comes back to an object shows its form again."""

from __future__ import annotations

import contextlib
from .properties_parts import FIELD_CLASSES, FieldKeys, panel_is_field
from PySide6 import QtCore, QtWidgets


class KeptForms:
    """The object forms the Properties panel keeps (mixed into PropertyPanel)."""

    def _kept_state(self, key):
        """What every kept form depends on besides its selection: the document and its
        change counters (the build key without the selection, the read-only verdict and the
        tool), the theme and the label layout. None: nothing is kept."""
        if key is None or len(key) != 11 or self._doc is None:
            return None
        from ..style import theme

        return (key[0], key[2:8], key[9], theme.generation(), self._label_mode)

    def _kept_form(self, key):
        """The kept form of build key ``key`` (taken out of the kept ones), or None. The
        kept forms are dropped when what they depend on changed."""
        state = self._kept_state(key)
        if state is None or state != self._kept_for:
            self.drop_kept_forms()
            self._kept_for = state
            return None
        ent = self._kept_forms.pop(key, None)
        if ent is None:
            return None
        try:
            ent[0].isHidden()
        except RuntimeError:
            return None
        return ent

    def _keep_or_drop(self, root, key, state) -> None:
        """The object form no longer shown: kept for its selection when nothing it shows
        changed since it was built and no field holds an edit not made; else dropped."""
        was = self._kept_state(key)
        keep = (
            bool(root.property("ctgObjectForm"))
            and was is not None
            and was == self._kept_state(self._built_key)
        )
        if keep:
            with contextlib.suppress(RuntimeError):
                for cls in FIELD_CLASSES:
                    if any(panel_is_field(w) and FieldKeys.modified(w) for w in root.findChildren(cls)):
                        keep = False
                        break
        if not keep:
            self._drop_root(root)
            return
        if self._kept_for != was:
            self.drop_kept_forms()
            self._kept_for = was
        with contextlib.suppress(RuntimeError):
            if self._stack.currentWidget() is root:
                self._stack.forget_current()
            root.hide()
        old = self._kept_forms.pop(key, None)
        if old is not None and old[0] is not root:
            self._drop_root(old[0])
        self._kept_forms[key] = (root, state)
        while len(self._kept_forms) > self.KEPT_FORMS:
            first = next(iter(self._kept_forms))
            self._drop_root(self._kept_forms.pop(first)[0])

    def drop_kept_forms(self) -> None:
        """Forget the kept object forms (a setting may have changed what a form shows)."""
        forms, self._kept_forms = self._kept_forms, {}
        self._kept_for = None
        for root, _state in forms.values():
            self._drop_root(root)

    def kept_form_count(self) -> int:
        """How many object forms are kept (tests)."""
        return len(self._kept_forms)

    def _restyle(self, root) -> None:
        """A kept form shown again: laid out for the label layout and the width now."""
        with contextlib.suppress(RuntimeError):
            for form in root.findChildren(QtWidgets.QFormLayout):
                self.style_form(form)
            if self.isVisible():
                root.setMaximumWidth(max(1, self.viewport().width()))

    def changeEvent(self, e) -> None:  # noqa: N802 -- Qt API
        # (a dialog opened, or another program: a setting, the palette or a library may
        # change what a form shows; the kept forms go)
        if e.type() == QtCore.QEvent.ActivationChange and self._kept_forms:
            self.drop_kept_forms()
        super().changeEvent(e)
