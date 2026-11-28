"""Node editing: reshaping lines, polygons and pipes."""

from __future__ import annotations

from ..names import plural

from ..main_window_base import (
    CLIPBOARD_MIME,
    QtCore,
    QtWidgets,
    app_settings,
    option,
    os,
    paste_linked,
)
from ..main_window_base import (
    SPECIAL_TOOLS,
)
from ..editor.canvas_look import signed as _signed
from ..prefs.settings import nudge_burst_ms
from ..messages import warn
from .canvas_edits import (
    NudgeBurstFilter,
)


class NodeEditing:
    """Editing the nodes of a line, polygon or pipe on the canvas."""

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
            self.statusBar().showMessage("Deleted %s." % plural(len(set(nodes)), "node"), 3000)
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
            self.statusBar().showMessage("Open a document first.", 4000)
            self._set_tool("select")
            return
        # (an object was made: Settings 'Keep the tool after drawing or placing an object'
        # keeps the tool for the next one; a cancelled one goes back to Select)
        placed = False
        try:
            if tool in SPECIAL_TOOLS:
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
                    self.statusBar().showMessage("Number not placed: no expression was entered.", 4000)
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
            warn(self, "Create Failed", str(e))
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
                f = self._nudge_filter = NudgeBurstFilter(self)
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
                "Opened the properties of the first %d of the %d objects copied." % (len(shown), len(roots)),
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
        if option("confirm_delete"):
            n = len({self.doc.root_of(i) for i in indices})
            from ..messages import confirm

            if not confirm(self, "Delete", "Delete %s?" % (plural(n, "object")), "Delete"):
                return
        units = len({self.doc.root_of(i) for i in indices})
        self.doc.delete_objects(indices)
        self._sel = []
        self._refresh()
        if units >= 5:
            # (a large delete is told over the page too, with Undo)
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
        self.statusBar().showMessage("Copied %s." % plural(len(self._sel), "object"), 3000)
