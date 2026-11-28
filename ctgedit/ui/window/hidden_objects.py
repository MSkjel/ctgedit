"""Objects hidden in the editor, isolate and frame."""

from __future__ import annotations

from ..names import plural

from ..main_window_base import (
    ElementDocument,
    app_settings,
    os,
)


class HiddenObjects:
    """Hiding objects in the editor, isolating some and framing them."""

    # -------------------------------------- hidden in the editor / isolate / frame
    # The objects hidden in the editor (the Objects panel's eyes, Isolate Selection, Hide or
    # Show Selection) are ``_hidden_roots``: record indices of the page shown, any object (a
    # genie member hides itself only). They follow their objects: each hide takes a note of
    # the records it hides (_hidden_snap), and a refresh after a delete, a paste or a reorder
    # finds those records again (_resolve_hidden), else the object of the same AN. Saved per
    # page by AN in 'browser/hidden_an/<path>' (an index for an object without an AN, as
    # -1 - index); a page shown in a new tab hides them again (_saved_hidden). Editor-only:
    # nothing of it is ever saved into the page.

    def _hidden_key(self, doc=None):
        doc = doc if doc is not None else self.doc
        path = getattr(doc, "path", "") or ""
        if not path:
            return None
        key = "browser/hidden_an/%s" % os.path.abspath(path)
        element = getattr(doc, "element_name", "") if isinstance(doc, ElementDocument) else ""
        return key + ("|" + element if element else "")

    @staticmethod
    def _records_of(doc) -> list:
        """The document's records (the identities a hidden object is followed by)."""
        parsed = getattr(doc, "parsed", None)
        pg = getattr(parsed, "page", None)
        recs = getattr(pg, "objects", None)
        if recs is not None and len(recs) == len(doc.page.objects):
            return recs
        return doc.page.objects

    def _snap_hidden(self) -> None:
        """Note the records the hidden set holds now (what _resolve_hidden follows)."""
        doc = self.doc
        if doc is None or not self._hidden_roots:
            self._hidden_snap = None
            return
        recs = self._records_of(doc)
        objs = doc.page.objects
        n = min(len(recs), len(objs))
        self._hidden_roots = {i for i in self._hidden_roots if 0 <= i < n}
        self._hidden_snap = (
            doc,
            frozenset(self._hidden_roots),
            getattr(doc, "derive_serial", 0),
            len(recs),
            [(i, recs[i], objs[i].an) for i in sorted(self._hidden_roots)],
        )

    def _resolve_hidden(self) -> None:
        """The hidden set as record indices of the current model: each hidden record where it
        is now (a delete or an insert before it moved it), else the object of its AN; gone
        ones are dropped. A set given from elsewhere (a tab switch) is taken as it is."""
        doc = self.doc
        cur = set(self._hidden_roots)
        if doc is None or not cur:
            self._hidden_snap = None
            return
        snap = self.__dict__.get("_hidden_snap")
        recs = self._records_of(doc)
        if snap is None or snap[0] is not doc or snap[1] != frozenset(cur):
            self._snap_hidden()
            return
        if snap[2] == getattr(doc, "derive_serial", 0) and snap[3] == len(recs):
            return  # nothing derived since the note
        pos = {id(po): i for i, po in enumerate(recs)}
        objs = doc.page.objects
        by_an = None
        new = set()
        for _i, po, an in snap[4]:
            j = pos.get(id(po))
            if j is None and an > 0:
                if by_an is None:
                    by_an = {}
                    for k, o in enumerate(objs):
                        if o.an > 0 and not o.raw.get("_non_drawable"):
                            by_an.setdefault(o.an, k)
                j = by_an.get(an)
            if j is not None and j < len(objs):
                new.add(j)
        self._hidden_roots = new
        self._snap_hidden()
        if new != cur:
            self._save_hidden()

    def _save_hidden(self) -> None:
        """'browser/hidden_an/<path>': the hidden objects by AN (-1 - index without one)."""
        doc = self.doc
        key = self._hidden_key(doc) if doc is not None else None
        if key is None:
            return
        objs = doc.page.objects
        vals = sorted(
            {objs[i].an if objs[i].an > 0 else -1 - i for i in self._hidden_roots if 0 <= i < len(objs)}
        )
        try:
            st = app_settings()
            if vals:
                st.setValue(key, vals)
            else:
                st.remove(key)
        except Exception:  # noqa: BLE001
            from ...infra import logsetup

            logsetup.note_exception("view._save_hidden")

    def _saved_hidden(self, doc) -> set:
        """The objects of ``doc`` hidden when it was last shown (its saved ANs that are
        still on the page)."""
        key = self._hidden_key(doc)
        if key is None or doc.page is None:
            return set()
        try:
            vals = app_settings().value(key, [], type=list) or []
        except Exception:  # noqa: BLE001 -- swallow-ok: a garbled list hides nothing
            return set()
        objs = doc.page.objects
        by_an = {}
        for k, o in enumerate(objs):
            if o.an > 0 and not o.raw.get("_non_drawable"):
                by_an.setdefault(o.an, k)
        out = set()
        for v in vals:
            try:
                v = int(v)
            except TypeError, ValueError:
                continue
            j = by_an.get(v) if v > 0 else (-1 - v)
            if j is not None and 0 <= j < len(objs):
                out.add(j)
        return out

    def _set_hidden(self, indices, save: bool = True) -> None:
        """Hide exactly ``indices`` in the editor: the canvas, the Objects panel's eyes and
        the hidden chip follow; remembered for the page."""
        doc = self.doc
        n = len(doc.page.objects) if doc is not None else 0
        self._hidden_roots = {i for i in indices if 0 <= i < n}
        self._snap_hidden()
        self.scene.set_hidden(self._hidden_roots)
        self.objects_dock.set_hidden(self._hidden_roots)
        self._update_hidden_chip()
        if save:
            self._save_hidden()

    def isolate_selection(self) -> None:
        """View > Isolate Selection: every root but the selected units' hides (the Objects
        tree's eyes), so a unit is edited alone; the same command again, Show All or the
        hidden chip brings the rest back."""
        if self.doc is None or not self._sel:
            return
        page = self.doc.page
        keep = {self.doc.root_of(i) for i in self._sel if 0 <= i < len(page.objects)}
        hide = {r for r in page.roots() if r not in keep and not page.objects[r].raw.get("_non_drawable")}
        if not hide or hide == self._hidden_roots:
            self._show_all_roots()
            return
        self._set_hidden(hide)
        self.statusBar().showMessage(
            "%d object%s hidden: the selection is alone on the page (Isolate Selection again, "
            "or Show All, brings them back)." % (len(hide), "" if len(hide) == 1 else "s"),
            6000,
        )

    def _solo_row(self, index: int) -> None:
        """Alt+click on an eye: only that object shows (every other top-level object, and the
        objects beside it in the groups it is in, hide); again: everything shows."""
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return
        page = self.doc.page
        chain = [index] + page.ancestors(index)
        on_path = set(chain)
        hide = {r for r in page.roots() if r not in on_path and not page.objects[r].raw.get("_non_drawable")}
        for a in chain[1:]:
            hide.update(
                m
                for m in page.objects[a].members
                if m not in on_path and not page.objects[m].raw.get("_non_drawable")
            )
        if not hide or hide == self._hidden_roots:
            self._show_all_roots()
            return
        self._set_hidden(hide)
        from ..names import kind_name

        o = page.objects[index]
        self.statusBar().showMessage(
            "Only %s%s shows. Alt+click its eye again to show everything."
            % (kind_name(o), (" AN %d" % o.an) if o.an > 0 else ""),
            6000,
        )

    def _on_hide_toggled(self, index: int, hidden: bool) -> None:
        """An eye of the Objects panel (any row: a member hides itself only)."""
        s = set(self._hidden_roots)
        if hidden:
            s.add(index)
        else:
            s.discard(index)
            if self.doc is not None and 0 <= index < len(self.doc.page.objects):
                # (shown again: a hidden group around it shows it too)
                s.difference_update(self.doc.page.ancestors(index))
        self._set_hidden(s)

    def hide_or_show_selection(self) -> None:
        """Hide or Show Selection (Space in the Objects panel): the selected objects hide in
        the editor; when they are all hidden already, they show again. They stay selected
        (the same key brings them back)."""
        if self.doc is None or not self._sel:
            return
        page = self.doc.page
        sel = [i for i in self._sel if 0 <= i < len(page.objects)]
        covered = set()
        for h in self._hidden_roots:
            if 0 <= h < len(page.objects):
                covered.update(page.subtree(h))
        s = set(self._hidden_roots)
        if sel and all(i in covered for i in sel):
            for i in sel:
                s.discard(i)
                s.difference_update(page.ancestors(i))
            self._set_hidden(s)
            msg = "%s shown again." % (plural(len(sel), "object"))
        else:
            s.update(sel)
            self._set_hidden(s)
            msg = (
                "%d object%s hidden in the editor (they stay on the page). The same key "
                "shows them again." % (len(sel), "" if len(sel) == 1 else "s")
            )
        self.statusBar().showMessage(msg, 6000)

    def _show_all_roots(self) -> None:
        if self.doc is None:
            self._hidden_roots = set()
            self._hidden_snap = None
            self.scene.set_hidden(set())
            self.objects_dock.set_hidden(set())
            self._update_hidden_chip()
            return
        self._set_hidden(set())

    def _update_hidden_chip(self) -> None:
        chip = getattr(self, "_hidden_chip", None)
        if chip is None:
            return
        n = len(self._hidden_roots)
        chip.setVisible(n > 0)
        chip.setText("%d hidden  [show]" % n)
