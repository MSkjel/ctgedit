"""Structural edits: group / ungroup, arrange, clipboard, touch and keyboard records."""

from __future__ import annotations

from ..editing_base import (  # noqa: F401
    CODEC,
    COMPOSITE_NOT_HERE,
    Callable,
    Iterable,
    KEYBOARD_ONLY_TCS,
    List,
    Optional,
    PageObject,
    Sequence,
    SymbolSetError,
    Tuple,
    base64,
    check_coords,
    crlf_text,
    factory,
    geom,
    guard_text,
    inputs,
    json,
    math,
    nesting_refusal,
    os,
    parse_ctg,
    struct,
    writer,
)
from ..editing_base import (  # noqa: F401
    parse_object,
)
from ..format.forest import whole_forest
from ..infra.logsetup import note_exception as _note_exc


class StructureEdits:
    """EditableDocument's structural edits, touch / keyboard records, forest helpers, group /
    ungroup, arrangement, clipboard, copies."""

    # -- structural edits ----------------------------------------------------------
    def _structural(
        self, label: str, new_order: List[PageObject], child_counts: Optional[list] = None
    ) -> None:
        """Replace the record list as one undo step. ``child_counts`` =
        [(record, old count, new count)] for surviving records whose direct
        child count changes with it (undo restores the old counts too)."""
        old_order = list(self.parsed.page.objects)
        counts = list(child_counts or ())

        def apply(order, which):
            for po, *cc in counts:
                po.class_flag = cc[which]
            self.parsed.page.objects[:] = order
            self.parsed.page.object_count = len(order)
            self._rerender_page()

        self._apply(label, lambda: apply(new_order, 1), lambda: apply(old_order, 0))

    def add_object(self, po: PageObject, index: Optional[int] = None) -> int:
        order = list(self.parsed.page.objects)
        at = len(order) if index is None else max(0, min(index, len(order)))
        order.insert(at, po)
        self._structural("add object", order)
        return at

    def create_object(
        self,
        kind: str,
        rect=None,
        nodes=None,
        *,
        colour=None,
        text=None,
        symbol=None,
        xp_button: Optional[bool] = None,
    ) -> int:
        """Create a new object on top of the z-order."""
        # a caption / number expression the page code page cannot store would
        # be written as '?' (the factory encodes with 'replace'): refuse it
        guard_text(text)
        check_coords(rect, nodes)
        if kind in ("text", "button"):
            text = crlf_text(text)
        link_info = None
        if kind == "symbol" and symbol is not None:
            from ..format import placement

            res = self._resolver()
            link_info = placement.element_info(res, symbol[0], symbol[1], symbol[2] or "ctl") if res else None
            if link_info is None:
                raise ValueError(
                    "Library element %s.%s is not in this project or the "
                    "projects it includes." % (symbol[0], symbol[1])
                )
            from ..format import genie_place as GP

            path = self.library_path(symbol[0], symbol[1], symbol[2] or "ctl")
            if path and GP.places_as_forest(path, symbol[1]):
                # a GROUP-ROOT symbol (every one-object symbol but a
                # bitmap, a bar or a trend has one) is an instance forest on the page,
                # never one tc 7 record: pasted with its top-left at the box's,
                # then sized to the box (CtDraw's Paste Symbol + resize)
                box = geom.norm_rect(rect) if rect else tuple(link_info["N"])
                with self.batch("add object"):
                    idx = self._place_library_element(symbol[0], symbol[1], symbol[2] or "ctl", box[:2])
                    gh = geom.parse_geom_header(self.parsed.page.objects[idx].body_raw)
                    if tuple(geom.norm_rect(gh.rect1)) != tuple(box):
                        if getattr(self, "_rerender_pending", False):
                            # (the resize scales the members the model lists: derive it)
                            self._rerender_pending = False
                            self._derive()
                        self.set_object_rect(idx, box)
                return idx
        self._use_tag_formats()
        po = factory.create_object(
            kind,
            rect,
            nodes,
            colour=colour,
            text=text,
            symbol=symbol,
            handle=self.next_handle(),
            link_info=link_info,
        )
        if kind == "button" and xp_button is not None:
            vis = "XP_STYLE" if xp_button else "NORMAL"
            if (po.raw_properties().get("VIS_MODE") or "").strip().upper() != vis:
                writer.set_object_property(po, "VIS_MODE", vis)
        if link_info is not None and po.subtype_size & 0x1 and self._COPY_RELINK:
            # a linked symbol drawn to a box (CtDraw's Paste Symbol + resize):
            # the page's link table lists its element like every linked record,
            # in the same undo step
            from ..format import genie_place as GP

            try:
                entries = GP.page_link_entries([po], self._resolver())
            except Exception:  # noqa: BLE001 -- the record is still added
                _note_exc("structure.StructureEdits.create_object")
                entries = []
            return self._append_placed([po], entries, "add object")
        return self.add_object(po)

    def create_control(self, donor_path: str, donor_index: int, rect, clsid: Optional[str] = None) -> int:
        """Insert a new ActiveX control copied from a donor record."""
        from ..io.pgregister import ole_clsid

        name = os.path.basename(donor_path)
        try:
            parsed = parse_ctg(donor_path)
        except Exception as exc:  # noqa: BLE001 -- gone, locked or damaged
            raise ValueError("The control's page %s could not be read (%s)." % (name, exc))
        if parsed.page is None or not 0 <= donor_index < len(parsed.page.objects):
            raise ValueError("The control's page %s could not be read." % name)
        donor = parsed.page.objects[donor_index]
        if clsid and ole_clsid(donor.body_raw).upper() != clsid.upper():
            raise ValueError(
                "The page %s has changed since the control list was made: "
                "open Insert ActiveX Control again." % name
            )
        po = factory.clone_control(donor, rect, handle=self.next_handle())
        return self.add_object(po)

    # -- touch / keyboard commands (input child records, format.inputs) --------
    def input_children(self, index: int) -> List[int]:
        """The object's Touch / Keyboard child records, in record order."""
        return inputs.input_children(self.parsed.page.objects, index)

    def touch_child(self, index: int) -> Optional[int]:
        objs = self.parsed.page.objects
        return next((c for c in self.input_children(index) if objs[c].type_code == inputs.TOUCH_TC), None)

    def keyboard_children(self, index: int) -> List[int]:
        objs = self.parsed.page.objects
        return [c for c in self.input_children(index) if objs[c].type_code == inputs.KEYBOARD_TC]

    def _input_record_free(self, po: PageObject) -> bool:
        """A touch / keyboard record whose values are its own (inputs.is_free); a genie
        window answers for its members too (ElementDocument)."""
        return inputs.is_free(po)

    def input_owner_status(self, index: int) -> Tuple[bool, str]:
        """(editable, reason): whether the object's touch / keyboard commands
        can be edited here, and why not when they cannot."""
        objs = self.parsed.page.objects
        if not 0 <= index < len(objs):
            return False, "The object no longer exists."
        po = objs[index]
        sem = self.page.objects[index] if self.page is not None and index < len(self.page.objects) else None
        if po.type_code in inputs.INPUT_TCS:
            return False, "A touch or keyboard command has the AN of its object."
        if (
            po.type_code >= 0x1000
            and po.type_code != inputs.BUTTON_TC
            and po.type_code not in KEYBOARD_ONLY_TCS
        ):
            return False, ("This kind of object cannot own touch or keyboard commands.")
        if sem is not None and sem.raw.get("_from_template"):
            return False, (
                "This object belongs to the page template. Change its "
                "commands in the template (.ctt) instead."
            )
        # a child is FREE (its own values) exactly when no ANCESTOR of its
        # owner is art-stripped (dwFlags 0x2 clear: a genie / library copy)
        # and the owner is not itself one; a placed LINKED symbol (0x1 set,
        # 0x2 clear) owns free children too
        chain = self._input_owner_ancestors(index)
        stripped = not (po.subtype_size & 0x2) and not (po.subtype_size & 0x1)
        if (
            stripped
            or (po.subtype_size & 0x200)
            or (sem is not None and sem.is_genie)
            or any(not (objs[a].subtype_size & 0x2) or (objs[a].subtype_size & 0x200) for a in chain)
        ):
            return False, (
                "This object is part of a genie or library symbol: its "
                "commands come from the library element. Open the "
                "element (Edit Definition) to change them."
            )
        return True, ""

    def _input_owner_ancestors(self, index: int) -> list:
        """The record indices of ``index``'s ancestors, nearest first: from the model's
        parents when the model has the records' structure (no derive pending), else from
        the records (inputs.parents over the whole page)."""
        objs = self.parsed.page.objects
        page = self.page
        if (
            page is not None
            and len(page.objects) == len(objs)
            and not getattr(self, "_rerender_pending", False)
        ):
            model = page.objects
            chain, k = [], model[index].parent
            while k is not None and 0 <= k < len(model) and len(chain) <= len(model):
                chain.append(k)
                k = model[k].parent
            return chain
        par = inputs.parents(objs)
        chain, k = [], par[index]
        while k is not None:
            chain.append(k)
            k = par[k]
        return chain

    def input_commands(self, index: int) -> dict:
        """What the object's Input tabs show."""
        objs = self.parsed.page.objects
        po = objs[index]
        editable, reason = self.input_owner_status(index)
        button = po.type_code == inputs.BUTTON_TC
        out = {
            "button": button,
            "editable": editable,
            "reason": reason,
            "touch": None,
            "keys": [],
        }
        if button:
            own = po.raw_properties()
            touch = {k: own[k] for k in inputs.BUTTON_TOUCH_KEYS if own.get(k)}
            out["touch"] = touch or None
        else:
            t = self.touch_child(index)
            if t is not None:
                out["touch"] = dict(inputs.values(objs[t]))
        out["keys"] = [dict(inputs.values(objs[c])) for c in self.keyboard_children(index)]
        return out

    @staticmethod
    def _check_input_lengths(new: dict, old: dict) -> None:
        """Refuse a CHANGED value longer than the Graphics Builder allows
        (commands 253, log messages 32, key sequences 32 characters)."""
        if "KEY_SEQ" in new:
            names = {
                "CMD": "command of key sequence '%s'" % new.get("KEY_SEQ", ""),
                "MSG_LOG": "log message of key sequence '%s'" % new.get("KEY_SEQ", ""),
                "KEY_SEQ": "key sequence",
            }
        else:
            names = {
                "CMD": "Up command",
                "DOWN_CMD": "Down command",
                "REPEAT_CMD": "Repeat command",
                "DBLCLK_CMD": "Double click command",
                "MSG_LOG": "Up log message",
                "MSG_LOGDN": "Down log message",
                "MSG_LOGRPT": "Repeat log message",
                "MSG_LOGDBL": "Double click log message",
            }
        for k, v in new.items():
            if not v or v == old.get(k, ""):
                continue
            limit = (
                inputs.MAX_LOG
                if k in inputs.TOUCH_LOGS
                else inputs.MAX_KEY_SEQ
                if k == "KEY_SEQ"
                else inputs.MAX_COMMAND
                if k in inputs.TOUCH_COMMANDS
                else None
            )
            if limit is not None and len(v) > limit:
                raise ValueError(
                    "The %s is %d characters long; Plant SCADA allows at "
                    "most %d. Shorten it (call a Cicode function for long "
                    "commands)." % (names.get(k, k), len(v), limit)
                )

    def _owner_flags_without_inputs(self, po: PageObject, body: bytes) -> int:
        """The owner's dwFlags once it has no input children left: a root
        shape re-derives bit 0x10 from its own keys (the saver's rule), a
        group without dynamic keys drops it; texts / buttons keep theirs."""
        from ..format import colourstate
        import copy

        probe = copy.copy(po)
        probe.body_raw = body
        writer.refresh_object_bags(probe)
        if colourstate.derive_dynamic_flag(probe):
            return probe.subtype_size
        if po.type_code == 0x08 and not colourstate.dynamic_keys(probe.raw_properties()):
            return po.subtype_size & ~0x10
        return po.subtype_size

    def set_input_commands(self, index: int, *, touch=None, keys=None, label: str = "input commands") -> bool:
        """Write an object's touch and keyboard commands as one undo step."""
        objs = self.parsed.page.objects
        ok, reason = self.input_owner_status(index)
        if not ok:
            raise ValueError(reason)
        owner = objs[index]
        guard_text(
            *[
                v
                for d in ([touch] if touch else []) + list(keys or [])
                for k, v in d.items()
                if v and not k.startswith("_")
            ]
        )
        if (
            owner.type_code in KEYBOARD_ONLY_TCS
            and touch is not None
            and any(v for k, v in touch.items() if k != "RPT_RATE")
        ):
            raise ValueError(
                "A %s has keyboard commands only (the Graphics Builder "
                "gives it no Touch page)." % KEYBOARD_ONLY_TCS[owner.type_code]
            )
        if owner.type_code == inputs.BUTTON_TC and touch is not None:
            return self._set_button_touch(index, touch, keys, label)

        import copy

        end = inputs.subtree_end(objs, index)
        kids = inputs.direct_children(objs, index)
        blocks = [(c, objs[c : inputs.subtree_end(objs, c)]) for c in kids]
        handle = geom.get_handle(owner)
        anchor = geom.parse_geom_header(owner.body_raw).anchor
        live = inputs.TOUCH_COMMANDS + inputs.TOUCH_LOGS
        frame = self._input_chain(index)

        def new_record(kind, want):
            if frame is not None and not factory.input_chain_supported(frame):
                raise ValueError(
                    (
                        "A new touch or keyboard command cannot be added to a scaled or rotated object "
                        "yet. Add it in the Graphics Builder. Existing commands can be edited here."
                    )
                )
            return factory.make_input_record(kind, handle, anchor, want, chain=frame)

        # ---- keyboard entries -> (source child or None, wanted values)
        kb_children = [c for c, blk in blocks if blk[0].type_code == inputs.KEYBOARD_TC]
        plan: List[Tuple[Optional[int], dict]] = []
        if keys is not None:
            named = any(d.get("_child") is not None for d in keys)
            for n, d in enumerate(keys):
                want = {k: str(d.get(k) or "") for k in inputs.KEYBOARD_KEYS}
                if not (want["KEY_SEQ"] or want["CMD"]):
                    continue
                src = d.get("_child") if named else (kb_children[n] if n < len(kb_children) else None)
                src = src if src in kb_children else None
                if src is None and not want["KEY_SEQ"].strip():
                    raise ValueError(
                        "A keyboard command needs a key sequence (the command %r has none)." % want["CMD"]
                    )
                plan.append((src, want))
        reuse = {src: want for src, want in plan if src is not None}

        touch_vals = None
        if touch is not None:
            touch_vals = {k: str(touch.get(k) or "") for k in live}
            touch_vals["ACTION"] = inputs.TOUCH_ACTION
            if "RPT_RATE" in touch:
                touch_vals["RPT_RATE"] = str(touch.get("RPT_RATE") or "")

        new_blocks: List[List[PageObject]] = []
        touch_seen = False
        for c, blk in blocks:
            head = blk[0]
            if head.type_code == inputs.TOUCH_TC and touch_vals is not None and not touch_seen:
                touch_seen = True
                old = dict(inputs.values(head))
                if not any(touch_vals[k] for k in live):
                    continue  # the last command went: drop it
                want = dict(touch_vals)
                if not want.get("RPT_RATE"):
                    want["RPT_RATE"] = old.get("RPT_RATE", "")
                if all(old.get(k, "") == want.get(k, old.get(k, "")) for k in inputs.TOUCH_KEYS):
                    new_blocks.append(blk)
                    continue
                self._check_input_lengths(want, old)
                if not self._input_record_free(head):
                    raise ValueError(
                        "This touch command comes from a library copy and cannot be edited here."
                    )
                new_blocks.append([inputs.with_values(head, want, inputs.TOUCH_KEYS)] + blk[1:])
            elif head.type_code == inputs.KEYBOARD_TC and keys is not None:
                if c not in reuse:
                    continue  # a removed key sequence
                want = reuse[c]
                old = dict(inputs.values(head))
                if all(old.get(k, "") == want[k] for k in inputs.KEYBOARD_KEYS):
                    new_blocks.append(blk)
                    continue
                self._check_input_lengths(want, old)
                if not self._input_record_free(head):
                    raise ValueError(
                        "This keyboard command comes from a library copy and cannot be edited here."
                    )
                new_blocks.append([inputs.with_values(head, want, inputs.KEYBOARD_KEYS)] + blk[1:])
            else:
                new_blocks.append(blk)
        for src, want in plan:
            if src is None:
                self._check_input_lengths(want, {})
                new_blocks.append([new_record("keyboard", want)])
        if touch_vals is not None and not touch_seen and any(touch_vals[k] for k in live):
            want = dict(touch_vals)
            want["RPT_RATE"] = want.get("RPT_RATE") or inputs.DEFAULT_RPT_RATE
            self._check_input_lengths(want, {})
            new_blocks.append([new_record("touch", want)])

        new_body = owner.body_raw
        flat = [po for blk in new_blocks for po in blk]
        if [id(p) for p in flat] == [id(p) for p in objs[index + 1 : end]] and new_body == owner.body_raw:
            return False
        if any(b[0].type_code in inputs.INPUT_TCS for b in new_blocks):
            new_dw = owner.subtype_size | 0x10
        elif inputs.input_children(objs, index) or new_body != owner.body_raw:
            new_dw = self._owner_flags_without_inputs(owner, new_body)
        else:
            new_dw = owner.subtype_size
        if owner.type_code == 0x05 and inputs.parents(objs)[index] is None:
            # the commands are the text's own properties, and the engine
            # re-measures a text on load: write its extent (as _body_edit does
            # for every edited text; a member's container would go stale)
            self._use_tag_formats()
            probe = copy.copy(owner)
            probe.body_raw = new_body
            writer.refresh_object_bags(probe)
            if geom.refit_text(probe):
                new_body = probe.body_raw
        order = list(objs[: index + 1]) + flat + list(objs[end:])
        self._replace_owner_records(label, owner, order, (new_body, len(new_blocks), new_dw))
        return True

    def _input_chain(self, index: int) -> Optional[list]:
        """The (Local, mode, e) links from the owner up to its root (placement.
        link_of, nearest first) when any of them carries a transform -- a new
        input record under it must be a carrier too (factory.make_input_record);
        None for an owner whose frame is the page."""
        from ..format import placement

        objs = self.parsed.page.objects
        par = inputs.parents(objs)
        chain, k = [], index
        while k is not None:
            chain.append(placement.link_of(objs[k]))
            k = par[k]
        return chain if placement.chain_total(chain) is not None else None

    def _replace_owner_records(
        self, label: str, owner: PageObject, order: List[PageObject], new_state
    ) -> None:
        """One undo step: the record list AND the owner's body / child count /
        dwFlags change together."""
        old_order = list(self.parsed.page.objects)
        old_state = (owner.body_raw, owner.class_flag, owner.subtype_size)
        old_flags = [(p, p.subtype_size) for p in old_order]

        def apply(order_, state, flags=None):
            owner.body_raw, owner.class_flag, owner.subtype_size = state
            writer.refresh_object_bags(owner)
            self.parsed.page.objects[:] = order_
            self.parsed.page.object_count = len(order_)
            if flags is not None:
                for p, f in flags:
                    p.subtype_size = f
            else:
                # the owner's members inherit its dynamic bit (a group or symbol set that
                # gains a touch command: CtDraw saves its members 0x1a)
                from ..format import colourstate

                objs, off = self._dynamic_context()
                k = next((i for i, p in enumerate(objs) if p is owner), None)
                if k is not None and owner.type_code in colourstate.INHERIT_TCS:
                    colourstate.derive_member_flags(objs, k)
            self._rerender_page()

        self._apply(label, lambda: apply(order, new_state), lambda: apply(old_order, old_state, old_flags))

    def _set_button_touch(self, index: int, touch: dict, keys, label: str) -> bool:
        """A button keeps its Up / Down / Repeat commands, repeat rate and log
        message in its OWN bag (pgbutton columns); its keyboard commands are
        child records like any object's."""
        po = self.parsed.page.objects[index]
        own = po.raw_properties()
        want = {k: str(touch.get(k) or "") for k in inputs.BUTTON_TOUCH_KEYS if k in touch or k != "RPT_RATE"}
        self._check_input_lengths(want, own)
        changed = False
        with self.batch(label):
            for k, v in want.items():
                if v == own.get(k, ""):
                    continue
                if v:
                    if not self.ensure_property(index, k, v):
                        raise ValueError("The button has no property list to store its %s in." % k)
                    changed = True
                elif k in own:
                    changed |= bool(self.remove_property(index, k))
            if keys is not None:
                changed |= self.set_input_commands(index, keys=keys, label=label)
        return changed

    def _keyboard_entries(self, owner: int) -> List[dict]:
        """The owner's keyboard commands as ``set_input_commands`` entries
        that name their records."""
        objs = self.parsed.page.objects
        return [dict(inputs.values(objs[c]), _child=c) for c in self.keyboard_children(owner)]

    def add_input(self, index: int, kind: str, values: Optional[dict] = None) -> int:
        """Add a Touch ('touch') or Keyboard ('keyboard') child to an object
        (one undo step). Returns the new record's index."""
        vals = dict(values or {})
        if kind == "touch":
            if self.input_commands(index)["button"]:
                raise ValueError("A button's touch commands are set on its Input page.")
            if self.touch_child(index) is not None:
                raise ValueError("The object already has a touch command; edit it instead.")
            if not any(vals.get(k) for k in inputs.TOUCH_COMMANDS + inputs.TOUCH_LOGS):
                raise ValueError("A touch command needs an Up, Down or Repeat command.")
            self.set_input_commands(index, touch=vals, label="add touch command")
            return self.touch_child(index)
        if kind == "keyboard":
            if not str(vals.get("KEY_SEQ") or "").strip():
                raise ValueError("A keyboard command needs a key sequence.")
            self.set_input_commands(
                index, keys=self._keyboard_entries(index) + [vals], label="add keyboard command"
            )
            return self.keyboard_children(index)[-1]
        raise ValueError("unknown input kind %r" % kind)

    def input_owner(self, child: int) -> int:
        """The object a Touch / Keyboard record belongs to."""
        objs = self.parsed.page.objects
        par = inputs.parents(objs)
        if not 0 <= child < len(objs) or objs[child].type_code not in inputs.INPUT_TCS or par[child] is None:
            raise ValueError("Object %d is not a touch or keyboard command." % child)
        return par[child]

    def remove_input(self, child: int) -> bool:
        """Delete one Touch / Keyboard child record (one undo step)."""
        owner = self.input_owner(child)
        objs = self.parsed.page.objects
        if objs[child].type_code == inputs.TOUCH_TC:
            return self.set_input_commands(owner, touch={}, label="remove touch command")
        return self.set_input_commands(
            owner,
            keys=[e for e in self._keyboard_entries(owner) if e["_child"] != child],
            label="remove keyboard command",
        )

    def set_input_property(self, child: int, key: str, value: str) -> bool:
        """Set (or, with an empty value, remove) one key on a Touch / Keyboard
        child (one undo step). Clearing the last command removes the record."""
        owner = self.input_owner(child)
        objs = self.parsed.page.objects
        if objs[child].type_code == inputs.TOUCH_TC:
            vals = dict(inputs.values(objs[child]))
            vals[key] = value
            return self.set_input_commands(owner, touch=vals, label="touch command")
        entries = self._keyboard_entries(owner)
        for e in entries:
            if e["_child"] == child:
                e[key] = value
        return self.set_input_commands(owner, keys=entries, label="keyboard command")

    # -- forest helpers ------------------------------------------------------
    def root_of(self, index: int) -> int:
        return self.page.root_of(index) if self.page else index

    def subtree(self, index: int) -> List[int]:
        return self.page.subtree(index) if self.page else [index]

    def expand_to_subtrees(self, indices: Iterable[int]) -> List[int]:
        """Every given index plus all descendants (deduped, sorted), so a container
        (group/genie) selection acts on its whole subtree."""
        out: set[int] = set()
        for i in indices:
            for j in self.subtree(i):
                out.add(j)
        return sorted(out)

    def delete_objects(self, indices: Iterable[int]) -> None:
        """Delete objects, and the subtrees of containers."""
        drop = set(self.expand_to_subtrees(indices))
        if not drop:
            return
        objs = self.parsed.page.objects
        par = inputs.parents(objs)
        counts: dict = {}  # parent index -> new child count
        for i in sorted(drop):
            parent = par[i]
            if parent is not None and parent not in drop:
                counts[parent] = max(0, counts.get(parent, objs[parent].class_flag) - 1)
        order = [po for i, po in enumerate(objs) if i not in drop]
        # the counts change with the record list, so undo restores them too
        self._structural("delete", order, [(objs[p], objs[p].class_flag, n) for p, n in counts.items()])

    def delete_object(self, index: int) -> None:
        self.delete_objects([index])

    def duplicate_objects(self, indices: Sequence[int], offset: int = 10) -> List[int]:
        """Duplicate whole subtrees (so a group/genie copies as one unit).

        Each selected subtree block is deep-copied, offset, and appended at the top
        of the z-order as a new root subtree. Returns each new subtree's root index."""
        objs = self.parsed.page.objects
        roots = sorted({self.root_of(i) for i in indices})
        at = len(objs)
        new_roots: List[int] = []
        added: List[PageObject] = []
        used = self._used_handles()
        for r in roots:
            block = self.subtree(r)  # contiguous, sorted
            dups = [factory.clone_object(objs[j]) for j in block]
            self._translate_detached(dups, offset, offset)
            old = [geom.get_handle(p) for p in dups]
            self._renumber(dups, used)
            new_roots.append(at + len(added))
            added.extend(self._fix_copies(dups, old))
        self._unique_pasted_names(added)
        self._append_placed(added, self._copy_link_entries(added, self._link_entries()), "duplicate")
        return new_roots

    def duplicate_and_move(
        self, indices: Sequence[int], dx: int, dy: int, label: str = "duplicate"
    ) -> List[int]:
        """Duplicate in place and move the copies, as one undo step."""
        with self.batch(label):
            new = self.duplicate_objects(indices, offset=0)
            if new and (dx or dy):
                self._derive_in_batch()
                self.move_objects(new, dx, dy)
        return new

    # -- group / ungroup (structural forest edits) ---------------------------
    def group_objects(self, indices: Sequence[int]) -> Optional[int]:
        """Arrange > Group."""
        objs = self.parsed.page.objects
        roots = sorted({self.root_of(i) for i in indices if 0 <= i < len(objs)})
        if len(roots) < 2:
            return None
        moved: List[int] = []
        for r in roots:
            moved.extend(self.subtree(r))
        moved_set = set(moved)
        bbox, bounds = self._union_rects(roots)
        container = factory.make_group_record(self.next_handle(), bbox, len(roots), bounds=bounds)
        insert_at = roots[0]
        order: List[PageObject] = []
        new_index: Optional[int] = None
        for i, po in enumerate(objs):
            if i == insert_at:
                new_index = len(order)
                order.append(container)
                order.extend(objs[j] for j in moved)
            if i in moved_set:
                continue
            order.append(po)
        self._structural("group", order)
        return new_index

    # dwFlags bit 0x4 = CtDraw's NOSELECT lock (& 8): Edit > Lock sets it
    # Edit > Unlock clears it, the saver persists
    # it and the selection gate skips it unless Break Lock Mode.
    LOCK_BIT = 0x4

    def set_locked(self, indices: Sequence[int], locked: bool) -> int:
        """Edit > Lock / Unlock: set/clear the persisted NOSELECT bit on each
        selected UNIT root (one undo step). Returns the number changed."""
        objs = self.parsed.page.objects
        roots = sorted({self.root_of(i) for i in indices if 0 <= i < len(objs)})
        before = {r: objs[r].subtype_size for r in roots}
        after = {r: (v | self.LOCK_BIT) if locked else (v & ~self.LOCK_BIT) for r, v in before.items()}
        changed = [r for r in roots if before[r] != after[r]]
        if not changed:
            return 0

        def put(vals):
            self._touch_model(changed)
            for r in changed:
                objs[r].subtype_size = vals[r]
                if self.page is not None and r < len(self.page.objects):
                    self.page.objects[r].raw["_dwflags"] = vals[r]

        self._apply("lock" if locked else "unlock", lambda: put(after), lambda: put(before))
        return len(changed)

    def _direct_children(self, index: int) -> List[int]:
        page = self.page
        if page is None:
            return []
        return [j for j in page.subtree(index) if j != index and page.objects[j].parent == index]

    def _container_fits(self, index: int, any_container: bool = False) -> bool:
        """A tc=8 container whose rect1/rect2 are the union of its direct
        children's (CtDraw's rule): only those are refitted after a member
        edit, so a hand-authored oddity is kept.
        ``any_container`` applies the same test to a symbol set (tc 0x0C)."""
        objs = self.parsed.page.objects
        po = objs[index]
        if not po.class_flag or (po.type_code != 0x08 and not (any_container and po.type_code == 0x0C)):
            return False
        kids = self._direct_children(index)
        gh = geom.parse_geom_header(po.body_raw)
        if not kids or gh is None:
            return False
        u1, u2 = self._union_rects(kids)
        return geom.norm_rect(gh.rect1) == tuple(u1) and tuple(gh.rect2) == tuple(
            self._alarm_bounds(index, u2)
        )

    def _alarm_bounds(self, index: int, bounds):
        """A group's Bounds with its ALARM INDICATOR: the children's union
        ``bounds`` grown by the frame and the flag when AI_ON is set
        (format.alarmind.grown_bounds, as CtDraw's Bounds);
        ``bounds`` unchanged otherwise."""
        objs = self.parsed.page.objects
        if not 0 <= index < len(objs) or objs[index].type_code != 0x08:
            return bounds
        from ..format import alarmind as AI

        po = objs[index]
        if b"AI_ON" not in po.body_raw.upper():
            return bounds
        s = AI.settings(AI.props_of(po))
        if not s["on"]:
            return bounds
        try:
            res = self._resolver()
        except Exception:  # noqa: BLE001 -- the default flag box still applies
            _note_exc("structure.StructureEdits._alarm_bounds")
            res = None
        return AI.grown_bounds(tuple(bounds), s, AI.flag_sizes(self.path, res))

    def _regrow_alarm_root(self, records: List[PageObject]) -> List[PageObject]:
        """Grow a recomposed genie root's bounds for its alarm indicator."""
        if not records or records[0].type_code != 0x08 or not records[0].class_flag:
            return records
        from ..format import alarmind as AI
        from ..format.placement import forest_parents

        root = records[0]
        if b"AI_ON" not in root.body_raw.upper():
            return records
        s = AI.settings(AI.props_of(root))
        gh = geom.parse_geom_header(root.body_raw)
        if not s["on"] or gh is None:
            return records
        parents = forest_parents(records)
        r2s = []
        for k in range(1, len(records)):
            if parents[k] != 0 or records[k].type_code in geom.INPUT_TYPE_CODES:
                continue
            ch = geom.parse_geom_header(records[k].body_raw)
            if ch is not None:
                r2s.append(tuple(ch.rect2))
        if not r2s:
            return records
        u2 = (min(r[0] for r in r2s), min(r[1] for r in r2s), max(r[2] for r in r2s), max(r[3] for r in r2s))
        try:
            res = self._resolver()
        except Exception:  # noqa: BLE001 -- the default flag box still applies
            _note_exc("structure.StructureEdits._regrow_alarm_root")
            res = None
        want = tuple(int(v) for v in AI.grown_bounds(u2, s, AI.flag_sizes(self.path, res)))
        if tuple(gh.rect2) != want:
            body = bytearray(root.body_raw)
            struct.pack_into("<4h", body, gh.rect2_off, *want)
            root.body_raw = bytes(body)
            writer.refresh_object_bags(root)
        return records

    def _refit_alarm_bounds(self, index: int, po: PageObject) -> bool:
        """Rewrite group ``po``'s Bounds (rect2) after an alarm-indicator edit:
        the union of its children's, grown by the indicator when it is on."""
        objs = self.parsed.page.objects
        if not (0 <= index < len(objs) and objs[index] is po):
            return False
        kids = self._direct_children(index)
        gh = geom.parse_geom_header(po.body_raw)
        if gh is None or not kids:
            return False
        _u1, u2 = self._union_rects(kids)
        want = tuple(int(v) for v in self._alarm_bounds(index, u2))
        if tuple(gh.rect2) == want:
            return False
        body = bytearray(po.body_raw)
        struct.pack_into("<4h", body, gh.rect2_off, *want)
        po.body_raw = bytes(body)
        writer.refresh_object_bags(po)
        return True

    def _fitting_ancestors(self, indices: Sequence[int]) -> List[int]:
        """Ancestor containers (not themselves edited) that follow the union
        rule, deepest first (a deeper ancestor has the larger index)."""
        if self.page is None:
            return []
        edited = set(indices)
        anc = set()
        for i in indices:
            if 0 <= i < len(self.page.objects):
                anc.update(self.page.ancestors(i))
        return [a for a in sorted(anc - edited, reverse=True) if self._container_fits(a)]

    def _refit_container(self, index: int, anchor: bool = True) -> None:
        """Set a container's rects to the union of its children, and optionally its hotspot."""
        po = self.parsed.page.objects[index]
        gh = geom.parse_geom_header(po.body_raw)
        kids = self._direct_children(index)
        if gh is None or not kids:
            return
        u1, u2 = self._union_rects(kids)
        if po.type_code == 0x0C:
            # a symbol set's extent is its FIRST real state's, not the union
            # (a DummyMarker slot sits off at 2 x the hotspot)
            from ..format import symbolset as SS

            objs = self.parsed.page.objects
            real = [k for k in kids if objs[k].type_code not in (SS.DUMMY_TC,) + tuple(geom.INPUT_TYPE_CODES)]
            if real:
                sh = geom.parse_geom_header(objs[real[0]].body_raw)
                u1, u2 = tuple(sh.rect1), tuple(sh.rect2)
        u2 = self._alarm_bounds(index, u2)  # a group's indicator frame + flag
        body = bytearray(po.body_raw)
        old = geom.norm_rect(gh.rect1)
        if anchor and tuple(gh.anchor) == (old[0], old[3] - 1):
            struct.pack_into("<2h", body, gh.anchor_off, int(u1[0]), int(u1[3]) - 1)
        struct.pack_into("<4h", body, gh.rect1_off, *(int(v) for v in u1))
        struct.pack_into("<4h", body, gh.rect2_off, *(int(v) for v in u2))
        po.body_raw = bytes(body)
        self._touch_model([index])
        if index < len(self.page.objects):
            g = self.page.objects[index].geometry
            g.x, g.y = int(u1[0]), int(u1[1])
            g.extent_x, g.extent_y = int(u1[2] - u1[0]), int(u1[3] - u1[1])
            # (its anchor and rects in the model as the record now holds them)
            self._model_header_from_record(index)

    def _union_rects(self, children: Sequence[int]):
        """(union of the children's normalised rect1, union of their rect2) --
        a container's Extent/Bounds. Input records (Touch/Keyboard) carry an
        editor icon box as rect2 and are left out."""
        objs = self.parsed.page.objects
        r1s, r2s = [], []
        for c in children:
            if objs[c].type_code in geom.INPUT_TYPE_CODES:
                continue
            gh = geom.parse_geom_header(objs[c].body_raw)
            if gh is None:
                continue
            r1s.append(geom.norm_rect(gh.rect1))
            r2s.append(tuple(gh.rect2))
        if not r1s:
            g = self.page.objects[children[0]].geometry
            box = (g.x, g.y, g.x + g.extent_x, g.y + g.extent_y)
            return box, box

        def union(rs):
            return (
                min(r[0] for r in rs),
                min(r[1] for r in rs),
                max(r[2] for r in rs),
                max(r[3] for r in rs),
            )

        # the Bounds leave a zero-size child out (an empty text), the Extent does not
        r2n = [r for r in r2s if not (r[0] == r[2] and r[1] == r[3])] or r2s
        return union(r1s), union(r2n)

    def ungroup_object(self, index: int) -> List[int]:
        """Arrange > Ungroup: dissolve a container record."""
        return self.ungroup_objects([index])

    def ungroup_objects(self, indices: Sequence[int]) -> List[int]:
        """Ungroup every container in the selection, as one edit."""
        objs = self.parsed.page.objects
        roots = sorted({self.root_of(i) for i in indices if 0 <= i < len(objs)})
        roots = [r for r in roots if inputs.has_drawable_children(objs, r)]
        sets = [r for r in roots if self.is_symbol_set(r)]
        roots = [r for r in roots if r not in sets]
        if sets and not roots:
            raise SymbolSetError(
                "A symbol set cannot be ungrouped: its members are the symbols it "
                "switches between. Double-click it to change its symbols."
            )
        if not roots:
            return []
        drop = set()
        for r in roots:
            drop.add(r)
            drop.update(inputs.input_children(objs, r))
        members = [j for r in roots for j in self.subtree(r) if j not in drop]
        # a dissolved GENIE instance's members lose its level (CtDraw breaks the instance
        # apart: plain objects with their current values; a nested genie among them keeps
        # its own bindings as values)
        from ..format import genie_place as GP

        work = list(objs)
        for r in roots:
            if not (objs[r].class_flag and (objs[r].subtype_size & 0x200)):
                continue
            inside = [j for j in self.subtree(r) if j != r and j not in drop]

            def between(j, r=r):
                n, a = 0, self.page.objects[j].parent if self.page is not None else None
                while a is not None and a != r:
                    if objs[a].class_flag and (objs[a].subtype_size & 0x200):
                        n += 1
                    a = self.page.objects[a].parent
                return n

            work = GP.drop_instance_level(work, inside, between)
        order = [po for i, po in enumerate(work) if i not in drop]
        self._structural("ungroup", order)
        return sorted(j - sum(1 for d in drop if d < j) for j in members)

    def reorder(self, indices: Sequence[int], where: str) -> List[int]:
        """Z-order on whole subtrees: 'front', 'back', 'forward' or 'backward'."""
        objs = self.parsed.page.objects
        roots = sorted({self.root_of(i) for i in indices})
        if not roots:
            return []
        sel = set()
        blocks = []  # list of (root, [block indices])
        for r in roots:
            blk = self.subtree(r)
            blocks.append((r, blk))
            sel.update(blk)
        heads = [objs[blk[0]] for _r, blk in blocks]  # identity refs (pre-apply)
        picked = [objs[j] for _r, blk in blocks for j in blk]
        rest = [o for i, o in enumerate(objs) if i not in sel]

        if where == "front":
            order = rest + picked
        elif where == "back":
            # CtDraw's Send to Back
            # inserts the object at the HEAD of the list, with no template check.
            # DELIBERATE DIFFERENCE, kept only while the template is the page's
            # FIRST root (the normal layout): the picked records go just above
            # it rather than vanish behind the chrome. When records already
            # precede the template, the template is never moved: the picked go
            # to index 0 like CtDraw.
            tpl_i = self._first_root_template()
            tpl = set(self.subtree(tpl_i)) - sel if tpl_i is not None else set()
            base = [o for i, o in enumerate(objs) if i in tpl]
            order = base + picked + [o for i, o in enumerate(objs) if i not in sel and i not in tpl]
        elif where in ("forward", "backward"):
            order = self._z_step(roots, where == "forward")
            if order is None:
                return roots
        else:
            return roots
        # representative index of each moved subtree root = head position in the
        # NEW order (found by identity; _structural will swap this list in).
        reps = sorted(next(k for k, o in enumerate(order) if o is h) for h in heads)
        self._structural("order", order)
        return reps

    def _first_root_template(self) -> Optional[int]:
        """The page template's root index when it is the page's FIRST root
        (record 0, the normal layout), else None -- a template stored after
        other roots is not held at the bottom by the z-order commands.
        (= ``template_reference()[0] == 0``, without scanning the page.)"""
        from ..format.symbols import reference_from_object

        objs = self.parsed.page.objects
        if not objs or objs[0].type_code != 0x08:
            return None
        try:
            ref = reference_from_object(objs[0])
        except Exception:  # noqa: BLE001
            _note_exc("structure.StructureEdits._first_root_template")
            return None
        return 0 if ref is not None and ref.ext == "ctt" else None

    def _z_step(self, roots: Sequence[int], forward: bool) -> Optional[List[PageObject]]:
        """Bring Forward / Send Backward one sibling subtree."""
        objs = self.parsed.page.objects
        page_roots = self.page.roots() if self.page is not None else []
        blocks = [self.subtree(r) for r in page_roots]
        if sum(len(b) for b in blocks) != len(objs):
            return None  # not a clean forest: leave it alone
        chosen = set(roots)
        tpl_i, _ref = self.template_reference()
        sel = [r in chosen for r in page_roots]
        fixed = [tpl_i is not None and r == tpl_i for r in page_roots]
        moved = False
        n = len(blocks)
        steps = range(n - 2, -1, -1) if forward else range(1, n)
        for k in steps:
            j = k + 1 if forward else k - 1
            if sel[k] and not sel[j] and not fixed[k] and not fixed[j]:
                blocks[k], blocks[j] = blocks[j], blocks[k]
                sel[k], sel[j] = sel[j], sel[k]
                fixed[k], fixed[j] = fixed[j], fixed[k]
                moved = True
        if not moved:
            return None
        return [objs[i] for blk in blocks for i in blk]

    # -- multi-object arrangement ---------------------------------------------------
    def _obj_bbox(self, index: int) -> Tuple[int, int, int, int]:
        po = self.parsed.page.objects[index]
        gh = geom.parse_geom_header(po.body_raw)
        l, t, r, b = gh.rect1
        bbox = (min(l, r), min(t, b), max(l, r), max(t, b))
        if bbox[2] - bbox[0] <= 1 and bbox[3] - bbox[1] <= 1:
            l2, t2, r2, b2 = gh.rect2
            bbox = (min(l2, r2), min(t2, b2), max(l2, r2), max(t2, b2))
        return bbox

    def unit_box(self, root: int) -> Tuple[int, int, int, int]:
        """The box Align uses for the unit (a genie and its members) of ``root``."""
        return self._unit_bbox(self.subtree(self.root_of(root)))

    def _units_from(self, indices: Sequence[int]) -> List[List[int]]:
        """Group flat indices into distinct top-level subtrees, so a genie/group
        aligns or distributes as ONE object (all members move together)."""
        units: List[List[int]] = []
        seen: set[int] = set()
        for i in indices:
            if not (0 <= i < len(self.objects)):
                continue
            r = self.root_of(i)
            if r in seen:
                continue
            seen.add(r)
            units.append(self.subtree(r))
        return units

    def _unit_bbox(self, unit: Sequence[int]) -> Tuple[int, int, int, int]:
        """A unit's extent: its root record's own box."""
        root = self._obj_bbox(unit[0])
        if root[2] - root[0] > 1 or root[3] - root[1] > 1:
            return root
        l = t = r = b = None
        for i in unit:
            x0, y0, x1, y1 = self._obj_bbox(i)
            if x1 - x0 <= 1 and y1 - y0 <= 1:
                continue
            l = x0 if l is None else min(l, x0)
            t = y0 if t is None else min(t, y0)
            r = x1 if r is None else max(r, x1)
            b = y1 if b is None else max(b, y1)
        if l is None:  # all degenerate: fall back to the root
            return self._obj_bbox(unit[0])
        return (l, t, r, b)

    def align(
        self, indices: Sequence[int], mode: str, reference: Optional[Tuple[int, int, int, int]] = None
    ) -> None:
        """Align units to the selection box. Each top-level unit moves as one."""
        units = self._units_from(indices)
        if len(units) < (1 if reference is not None else 2):
            return
        boxes = [self._unit_bbox(u) for u in units]
        if reference is not None:
            ls, ts, rs, bs = ([int(reference[k])] for k in range(4))
        else:
            ls = [bx[0] for bx in boxes]
            ts = [bx[1] for bx in boxes]
            rs = [bx[2] for bx in boxes]
            bs = [bx[3] for bx in boxes]
        moves: List[Tuple[List[int], int, int]] = []
        for u, (l, t, r, b) in zip(units, boxes):
            dx = dy = 0
            if mode == "left":
                dx = min(ls) - l
            elif mode == "right":
                dx = max(rs) - r
            elif mode == "hcentre":
                dx = (min(ls) + max(rs)) // 2 - (l + r) // 2
            elif mode == "top":
                dy = min(ts) - t
            elif mode == "bottom":
                dy = max(bs) - b
            elif mode == "vcentre":
                dy = (min(ts) + max(bs)) // 2 - (t + b) // 2
            if dx or dy:
                moves.append((u, dx, dy))
        if not moves:
            return

        def shift(sign):
            for u, dx, dy in moves:
                self._translate_records(u, dx * sign, dy * sign)
            self._rerender_page()

        self._apply("align", lambda: shift(1), self._moves_undo(moves))

    def _moves_undo(self, moves) -> Callable[[], None]:
        """The undo of moving units (align / distribute): their records' stored state back
        (see move_objects -- moving back re-derives the transform slots instead)."""
        before = self._record_states([i for u, _dx, _dy in moves for i in u])

        def undo():
            self._put_record_states(before)
            self._rerender_page()

        return undo

    def distribute(self, indices: Sequence[int], axis: str) -> None:
        """Space units evenly by their midpoints along 'h' or 'v'."""
        units = self._units_from(indices)
        if len(units) < 3:
            return
        boxes = [self._unit_bbox(u) for u in units]
        lo, hi = (0, 2) if axis == "h" else (1, 3)
        mids = [(bx[lo] + bx[hi]) * 0.5 for bx in boxes]

        def order_key(k):
            gh = geom.parse_geom_header(self.parsed.page.objects[units[k][0]].body_raw)
            bnd = geom.norm_rect(gh.rect2) if gh is not None else None
            if bnd is None or (bnd[2] <= bnd[0] and bnd[3] <= bnd[1]):
                bnd = boxes[k]  # no bounds of its own: the extent
            return bnd[lo] + bnd[hi]

        order = sorted(range(len(units)), key=order_key)
        first, last = min(mids), max(mids)
        step = (last - first) / (len(units) - 1)
        moves: List[Tuple[List[int], int, int]] = []
        for n, k in enumerate(order):
            d = int(math.floor(n * step + first - mids[k] + 0.4995))
            if d:
                moves.append((units[k], d if axis == "h" else 0, d if axis == "v" else 0))
        if not moves:
            return

        def shift(sign):
            for u, dx, dy in moves:
                self._translate_records(u, dx * sign, dy * sign)
            self._rerender_page()

        self._apply("distribute", lambda: shift(1), self._moves_undo(moves))

    def space_equally(self, indices: Sequence[int], axis: str, gap: Optional[int] = None) -> None:
        """Arrange > Space Equally: equal GAPS between the units' edges along ``axis``
        ('h' or 'v'), in the order of their left / top edges (a genie or group is one
        unit). ``gap`` None = the room between the first and the last unit is shared
        (both stay); a number = that gap, the first unit stays. One undo step."""
        units = self._units_from(indices)
        if len(units) < 2:
            return
        boxes = [self._unit_bbox(u) for u in units]
        lo, hi = (0, 2) if axis == "h" else (1, 3)
        order = sorted(range(len(units)), key=lambda k: (boxes[k][lo], k))
        sizes = [boxes[k][hi] - boxes[k][lo] for k in order]
        if gap is None:
            if len(units) < 3:
                return
            first, last = boxes[order[0]], boxes[order[-1]]
            room = (last[lo] - first[hi]) - sum(sizes[1:-1])
            step = room / (len(units) - 1)
        else:
            step = int(gap)
        moves: List[Tuple[List[int], int, int]] = []
        pos = float(boxes[order[0]][hi])
        for n, k in enumerate(order[1:-1] if gap is None else order[1:], 1):
            target = int(round(pos + step))
            d = target - boxes[k][lo]
            if d:
                moves.append((units[k], d if axis == "h" else 0, d if axis == "v" else 0))
            pos = target + sizes[n]
        if not moves:
            return

        def shift(sign):
            for u, dx, dy in moves:
                self._translate_records(u, dx * sign, dy * sign)
            self._rerender_page()

        self._apply("space equally", lambda: shift(1), self._moves_undo(moves))

    def arrange_in_grid(
        self, indices: Sequence[int], columns: int, hgap: int = 0, vgap: int = 0, by_rows: bool = True
    ) -> None:
        """Arrange > Arrange in Grid."""
        units = self._units_from(indices)
        if len(units) < 2:
            return
        columns = max(1, min(int(columns), len(units)))
        boxes = [self._unit_bbox(u) for u in units]
        order = sorted(range(len(units)), key=lambda k: (boxes[k][1], boxes[k][0], k))
        rows = -(-len(units) // columns)
        cell: dict[int, Tuple[int, int]] = {}
        for n, k in enumerate(order):
            cell[k] = (n // columns, n % columns) if by_rows else (n % rows, n // rows)
        widths = [0] * columns
        heights = [0] * rows
        for k, (r, c) in cell.items():
            widths[c] = max(widths[c], boxes[k][2] - boxes[k][0])
            heights[r] = max(heights[r], boxes[k][3] - boxes[k][1])
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        xs = [x0]
        for w in widths[:-1]:
            xs.append(xs[-1] + w + int(hgap))
        ys = [y0]
        for h in heights[:-1]:
            ys.append(ys[-1] + h + int(vgap))
        moves: List[Tuple[List[int], int, int]] = []
        for k, (r, c) in cell.items():
            dx, dy = xs[c] - boxes[k][0], ys[r] - boxes[k][1]
            if dx or dy:
                moves.append((units[k], dx, dy))
        if not moves:
            return

        def shift(sign):
            for u, dx, dy in moves:
                self._translate_records(u, dx * sign, dy * sign)
            self._rerender_page()

        self._apply("arrange in grid", lambda: shift(1), self._moves_undo(moves))

    def make_same_size(self, indices: Sequence[int], reference: int) -> None:
        """Resize every selected unit to the reference unit's size, as one undo step."""
        objs = self.parsed.page.objects
        if not (0 <= reference < len(objs)):
            return
        ref = self.root_of(reference)
        units = [u for u in self._units_from(indices) if u[0] != ref]
        if not units:
            return
        rl, rt, rr, rb = self._unit_bbox(self.subtree(ref))
        w, h = rr - rl, rb - rt
        with self.batch("same size"):
            for u in units:
                l, t, r, b = self._unit_bbox(u)
                nw = w if w > 0 else r - l
                nh = h if h > 0 else b - t
                if (nw, nh) != (r - l, b - t):
                    self.set_object_rect(u[0], (l, t, l + nw, t + nh))

    def adjust_roots(self, indices: Sequence[int]) -> List[int]:
        """The units Adjust Position and Size acts on: each selected object
        with its subtree, minus those inside another selected object's
        subtree (they move and scale with it). An object selected in the
        Objects tree inside a plain group is a unit of its own."""
        objs = self.parsed.page.objects
        roots = [i for i in dict.fromkeys(indices) if 0 <= i < len(objs)]
        if self.page is None:
            return roots
        keep = set(roots)
        return [i for i in roots if not keep.intersection(self.page.ancestors(i))]

    def adjust_extent(self, indices: Sequence[int]):
        """(l, t, r, b) of the units :meth:`adjust_roots` gives -- the box
        Arrange > Adjust Position and Size shows (X, Y = its top-left, Width
        and Height = its size); None for an empty selection."""
        boxes = [self._unit_bbox(self.subtree(r)) for r in self.adjust_roots(indices)]
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def adjust_position_and_size(
        self, indices: Sequence[int], x: int, y: int, sx: float = 1.0, sy: float = 1.0
    ) -> bool:
        """Arrange > Adjust Position and Size."""
        from ..format import placement

        roots = self.adjust_roots(indices)
        box = self.adjust_extent(roots)
        if box is None:
            return False
        dx, dy = int(x) - box[0], int(y) - box[1]
        scale = sx != 1.0 or sy != 1.0
        if not (dx or dy or scale):
            return False
        before = len(self._undo)
        with self.batch("adjust position and size"):
            if dx or dy:
                self.move_objects(self.expand_to_subtrees(roots), dx, dy)
            if scale:
                ox, oy = box[0] + dx, box[1] + dy
                hx, hy = ox - ox * sx, oy - oy * sy
                objs = self.parsed.page.objects
                for r in roots:
                    l, t, rr, b = self._unit_bbox(self.subtree(r))
                    nl, nt = int(l * sx + hx), int(t * sy + hy)
                    nr, nb = int(rr * sx + hx), int(b * sy + hy)
                    # a unit keeps at least one pixel where it had a size
                    # (a group / genie scaled to 0 loses its members' frames)
                    kept = True
                    if rr > l and nr <= nl:
                        nr, kept = nl + 1, False
                    if b > t and nb <= nt:
                        nb, kept = nt + 1, False
                    if (nl, nt, nr, nb) == (l, t, rr, b):
                        continue
                    po = objs[r]
                    # the exact map only where the unit IS its root's extent
                    # and every scaled axis has a size (a 1-px axis / a 1x1
                    # V3/V4 hotspot keeps _scale_unit's point rule)
                    exact = (
                        kept
                        and self._obj_bbox(r) == (l, t, rr, b)
                        and (rr - l > 1 or sx == 1.0)
                        and (b - t > 1 or sy == 1.0)
                        and (
                            (r < len(self.page.objects) and self.page.objects[r].members)
                            or placement.carries(po)
                            or geom.find_node_array(po) is not None
                        )
                    )
                    if exact:
                        self._scale_unit(r, (nl, nt, nr, nb), matrix=(sx, 0.0, 0.0, sy, hx, hy))
                    else:
                        self.set_object_rect(r, (nl, nt, nr, nb))
        return len(self._undo) > before

    # -- clipboard -------------------------------------------------------------------
    def copy_payload(self, indices: Sequence[int]) -> str:
        """Serialize objects to a JSON clipboard payload. Containers travel
        with their whole subtree (a record's child count must be followed by
        that many children -- a root-only payload would paste a container
        that swallows the next records on load)."""
        out = []
        records = []
        for i in self.expand_to_subtrees(indices):
            po = self.parsed.page.objects[i]
            records.append(po)
            out.append(
                {
                    "class_flag": po.class_flag,
                    "type_code": po.type_code,
                    "subtype_size": po.subtype_size,
                    "an_str": po.an_str,
                    "body_b64": base64.b64encode(po.body_raw).decode(),
                }
            )
        data = {"ctgedit_objects": out}
        # the source page's link-table entries the copy uses travel with it:
        # a paste onto another page lists them there (paste_payload)
        try:
            from ..format import genie_place as GP

            used = GP.used_link_entries(records, self._link_entries(), keep_super=False)
        except Exception:  # noqa: BLE001 -- the paste falls back to the library
            _note_exc("structure.StructureEdits.copy_payload")
            used = []
        if used:
            data["links"] = [[g.hex(), n] for g, n in used]
        if self.page is not None and self.page.name:
            data["page"] = self.page.name  # the %page% the copy was composed with
        if not self._COPY_RELINK:
            data["element"] = True  # copied from an element window
        return json.dumps(data)

    def paste_payload(
        self, payload: str, offset: int = 10, at: Optional[Tuple[int, int]] = None
    ) -> List[int]:
        """Insert objects from a clipboard payload. Returns the new indices."""
        try:
            data = json.loads(payload)
            items = data["ctgedit_objects"]
        except ValueError, KeyError, TypeError:
            return []
        recs: List[PageObject] = []
        for it in items:
            body = base64.b64decode(it["body_b64"])
            an = str(it.get("an_str", "-1")).encode(CODEC)
            blob = (
                struct.pack("<H", int(it["class_flag"]))
                + struct.pack("<I", int(it["type_code"]))
                + struct.pack("<I", int(it["subtype_size"]))
                + struct.pack("<H", len(an))
                + an
                + body
            )
            recs.append(parse_object(0, 0, len(blob), 0, blob))
        if not recs or not whole_forest([p.class_flag for p in recs]):
            return []  # (a container would take the page's next records as its members)
        if not self.composites_allowed and any(
            p.type_code == 0x08 and p.class_flag and b"_compositioncaches" in bytes(p.body_raw[:512]).lower()
            for p in recs
        ):
            raise ValueError(COMPOSITE_NOT_HERE)
        self._refuse_pasted_cycle(recs)
        box = self._detached_box(recs) if at is not None else None
        if box is not None:
            self._translate_detached(recs, int(at[0]) - box[0], int(at[1]) - box[1])
        elif offset:
            self._translate_detached(recs, offset, offset)
        from_element = bool(self._COPY_RELINK and data.get("element"))
        if from_element:
            # a copy from an ELEMENT window pasted on a page: the element level goes (a
            # direct member becomes a plain object with its values, a nested genie an
            # instance with its bindings as values; the instances are composed again below)
            recs = self._page_form_from_element(recs)
        old = [geom.get_handle(p) for p in recs]
        self._renumber(recs, keep_free=True)
        src = data.get("page")
        recs = self._fix_copies(recs, old, src if isinstance(src, str) else None)
        composed = set()
        if from_element:
            # (after _fix_copies, whose old handles line up with the pasted records one by
            # one: a composition can change an instance's record count, a genie that gained
            # members since the element window read it)
            recs, composed = self._compose_pasted_instances(recs)
        carried = []
        for ent in data.get("links") or ():
            try:
                g, n = bytes.fromhex(ent[0]), str(ent[1])
            except Exception:  # noqa: BLE001 -- swallow-ok: a foreign / damaged entry is skipped
                continue
            if len(g) == 16 and n:
                if composed and self._entry_names(n, composed):
                    continue  # (composed from its current genie: its current entry)
                carried.append((g, n))
        self._unique_pasted_names(recs)
        at = self._append_placed(recs, self._copy_link_entries(recs, carried), "paste")
        return list(range(at, at + len(recs)))

    def _refuse_pasted_cycle(self, recs: List[PageObject]) -> None:
        """In a genie window, refuse a paste that would make the genie contain itself."""
        ref = getattr(self, "element_ref", None)
        low = self.path.lower()
        if not ref or not low.endswith((".ctm", ".ctt")):
            return
        from ..format import genie_place as GP
        from ..format import template_place as TP

        res = self._resolver()
        if res is None:
            return
        tpl = low.endswith(".ctt")
        walk = TP.template_nesting_chain if tpl else GP.records_nesting_chain
        chain = walk(recs, self.path, ref[1], res)
        if chain is not None:
            host = "%s.%s" % (os.path.splitext(os.path.basename(self.path))[0], ref[1])
            raise ValueError(nesting_refusal(chain, host, "template" if tpl else "genie"))

    @staticmethod
    def _detached_box(recs: List[PageObject]) -> Optional[Tuple[int, int, int, int]]:
        """The box of a detached block's top-level units (DFS order: each root's own box, as
        :meth:`_obj_bbox`; Touch / Keyboard records left out), None when none has one."""
        from ..format import placement as P

        parents = P.forest_parents(recs)
        boxes = []
        for k, po in enumerate(recs):
            if parents[k] is not None or po.type_code in geom.INPUT_TYPE_CODES:
                continue
            gh = geom.parse_geom_header(po.body_raw)
            if gh is None:
                continue
            r = geom.norm_rect(gh.rect1)
            if r[2] - r[0] <= 1 and r[3] - r[1] <= 1:
                r = geom.norm_rect(gh.rect2)
            boxes.append(r)
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def _page_form_from_element(self, recs: List[PageObject]) -> List[PageObject]:
        """Records copied from an element window, as a page holds them (see paste)."""
        from ..format import genie_place as GP
        from ..format import placement as P

        parents = P.forest_parents(recs)

        def is_root(j):
            return bool(recs[j].class_flag and (recs[j].subtype_size & 0x200))

        def between(j):
            n, a = 0, parents[j]
            while a is not None:
                if is_root(a):
                    n += 1
                a = parents[a]
            return n

        return GP.drop_instance_level(recs, range(len(recs)), between)

    @staticmethod
    def _entry_names(name: str, keys) -> bool:
        """The link-table entry ``name`` ('lib.elem.<ext#stamp') names one of ``keys``
        ((library, element), lower case); the library splits at the last dot."""
        if name.startswith("\\"):
            return False
        from ..format import links
        from ..format.symbols import split_lib_element

        lib, elem = split_lib_element(links.strip_stamp(name).rpartition(".")[0])
        return (lib.lower(), elem.lower()) in keys

    def _compose_pasted_instances(self, recs: List[PageObject]):
        """Compose genie instances pasted from an element window."""
        from ..format import genie_place as GP
        from ..format.symbols import reference_from_object

        res = self._resolver()
        roots = GP.instance_roots(recs)
        if res is None or not roots:
            return recs, set()
        self._use_tag_formats()
        out = list(recs)
        page_name = self.page.name if self.page is not None else ""
        on_page = set(self._used_handles())
        done, left = set(), set()
        for k in reversed(roots):
            end = GP.subtree_end(out, k)
            inst = out[k:end]
            try:
                ref = reference_from_object(inst[0])
                key = (ref.library.lower(), ref.element.lower()) if ref is not None else None
            except Exception:  # noqa: BLE001  # swallow-ok: an unreadable link is pasted as is
                key = None
            others = on_page | {geom.get_handle(p) for j, p in enumerate(out) if not k <= j < end}
            others.discard(None)
            try:
                new = GP.refresh_instance(inst, res, page_name, outside_handles=others)
            except Exception:  # noqa: BLE001 -- pasted as it is (the element's entry)
                from ..infra import logsetup

                logsetup.note_exception("editing.paste_payload/compose", page=self.path)
                new = None
            if not new or key is None:
                if key is not None:
                    left.add(key)
                continue
            # (not merged onto the pasted records: a member would keep the element's binding
            # template, '%Name% L1' in n0's text level, which a placement does not hold)
            out[k:end] = self._regrow_alarm_root(list(new))
            done.add(key)
        return out, done - left

    # -- copies of placed genies / linked symbols ---------------------------------
    #: copy / paste / duplicate re-derive what depends on the copy's identity
    #: (%AN%) and list its library links in the page's link table. A library
    #: ELEMENT does neither: its import table is rebuilt from its records on
    #: save (genie_norm.rebuild_link_table) and its values from the templates
    _COPY_RELINK = True

    def _link_entries(self) -> list:
        """The page's link/import-table entries [(guid, name)], in order."""
        from ..format import links

        pb = self.parsed.page if self.parsed is not None else None
        if pb is None or not pb.link_count:
            return []
        t = links.parse_link_table(pb.link_table_raw)
        return list(t[1]) if t else []

    def _fix_copies(
        self, recs: List[PageObject], old_handles, source_page: Optional[str] = None
    ) -> List[PageObject]:
        """Fix up a copied block's genie instances after it got fresh handles."""
        if not self._COPY_RELINK:
            return recs
        from ..format import genie_place as GP

        out = list(recs)
        page_name = (
            source_page if source_page is not None else (self.page.name if self.page is not None else "")
        )
        for k in GP.instance_roots(out):
            old, new = old_handles[k], geom.get_handle(out[k])
            if old is None or new is None or old == new:
                continue
            end = GP.subtree_end(out, k)
            try:
                sub = GP.resubstitute_an(out[k:end], old + 1, new + 1, page_name)
            except Exception:  # noqa: BLE001 -- a malformed copy stays as copied
                _note_exc("structure.StructureEdits._fix_copies")
                sub = None
            if sub is not None:
                out[k:end] = sub
        return out

    def _copy_link_entries(self, recs: List[PageObject], carried=()) -> list:
        """The link table entries a copied block needs on this page."""
        from ..format import genie_place as GP

        if not self._COPY_RELINK:
            if not carried:
                return []
            try:
                return [e for e in GP.pasted_link_entries(recs, None, carried) if not e[1].startswith("\\")]
            except Exception:  # noqa: BLE001 -- the records still paste
                from ..infra import logsetup

                logsetup.note_exception("editing._copy_link_entries", page=self.path)
                return []
        try:
            return GP.pasted_link_entries(recs, self._resolver(), carried)
        except Exception:  # noqa: BLE001 -- the records still paste
            _note_exc("structure.StructureEdits._copy_link_entries")
            return []
