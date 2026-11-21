"""Symbol set editing, mixed into EditableDocument."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from ..format import symbolset as S
from ..format import geometry as geom
from ..format import writer
from ..format.page import PageObject, _parse_object

#: a state spec: None (empty), (library, element) or an int = the page index
#: of an existing state record to keep unchanged (an embedded picture)
StateSpec = object


class SymbolSetError(ValueError):
    """A symbol-set edit that cannot be made (the message says why)."""


def _clone(po: PageObject) -> PageObject:
    blob = writer.object_payload(po)
    return _parse_object(0, 0, len(blob), 0, blob)


def _same_symbol(a, b) -> bool:
    return bool(a and b) and a[0].lower() == b[0].lower() and a[1].lower() == b[1].lower()


class SymbolSetEditing:
    """Symbol-set commands of EditableDocument."""

    # -- queries -----------------------------------------------------------
    def is_symbol_set(self, index: int) -> bool:
        objs = self.parsed.page.objects
        return 0 <= index < len(objs) and objs[index].type_code == S.SET_TC

    def is_animated_symbol(self, index: int) -> bool:
        objs = self.parsed.page.objects
        return 0 <= index < len(objs) and objs[index].type_code == S.ANIMSYM_TC

    def symbol_set_editable(self, index: int) -> Tuple[bool, str]:
        """(True, '') when the set's states can be changed here, else (False,
        the reason)."""
        if not self.is_symbol_set(index):
            return False, "The object is not a symbol set."
        po = self.parsed.page.objects[index]
        sem = self.page.objects if self.page is not None else []
        if index < len(sem):
            if sem[index].raw.get("_from_template"):
                return False, (
                    "This symbol set comes from the page template. Change it in the template (.ctt)."
                )
            for a in self.page.ancestors(index):
                if sem[a].is_genie:
                    return False, (
                        "This symbol set is part of a genie. Its symbols come "
                        "from the genie's library definition; edit the "
                        "definition to change them."
                    )
        if not (po.subtype_size & 0x2) or S.hotspot(po) is None:
            return False, (
                "This symbol set is a copy of a library element. Its symbols "
                "come from the library definition; edit the definition."
            )
        return True, ""

    def symbol_set_info(self, index: int) -> Optional[dict]:
        """The set's type, expressions and states as the Symbol Set sheet
        shows them; None when ``index`` is not a symbol set."""
        if not self.is_symbol_set(index):
            return None
        objs = self.parsed.page.objects
        # what the property sheet shows: in a genie the member's %parameter% values
        # (its template bag; its own value bag has none of them)
        props = self.object_properties(index)
        kind = S.set_type(props)
        exprs = S.expressions(props)
        states = []
        for n, k in enumerate(S.state_children(objs, index)):
            sym = S.state_symbol(objs[k])
            states.append(
                {
                    "index": n,
                    "record": k,
                    "kind": S.state_kind(objs[k]),
                    "symbol": (sym[0], sym[1]) if sym else None,
                }
            )
        ok, why = self.symbol_set_editable(index)
        return {
            "type": kind,
            "type_label": S.TYPE_LABELS.get(kind, "Type %d" % kind),
            "expressions": exprs,
            "conditions": len(exprs) if kind == S.MULTI_STATE else 0,
            "states": states,
            "capacity": S.capacity(kind, len(exprs)),
            "editable": ok,
            "reason": why,
            "an": geom.live_an(objs[index]),
        }

    def symbol_set_specs(self, index: int) -> List[StateSpec]:
        """The current states as apply specs (a linked symbol by name, an
        empty slot None, any other record kept by its index)."""
        info = self.symbol_set_info(index) or {"states": []}
        out = []
        for st in info["states"]:
            if st["kind"] == "linked":
                out.append(st["symbol"])
            elif st["kind"] == "empty":
                out.append(None)
            else:
                out.append(st["record"])
        return out

    def _set_state_number(self, index: int) -> Optional[Tuple[int, int]]:
        """(set index, state number) when record ``index`` is a state of an editable
        symbol set, else None."""
        sem = self.page.objects if self.page is not None else []
        par = sem[index].parent if 0 <= index < len(sem) else None
        if par is None or not self.is_symbol_set(par):
            return None
        info = self.symbol_set_info(par)
        if not info or not info["editable"]:
            return None
        n = next((st["index"] for st in info["states"] if st["record"] == index), None)
        return None if n is None else (par, n)

    # -- create --------------------------------------------------------------
    def _state_info(self, symbol) -> dict:
        from ..format import placement

        res = self._resolver()
        info = placement.element_info(res, symbol[0], symbol[1], "ctl") if res else None
        if info is None:
            raise SymbolSetError(
                "The symbol %s.%s is not in any library on this project's include "
                "path. Pick a symbol from the list." % (symbol[0], symbol[1])
            )
        return info

    def _state_records(self, handle: int, spec, info: dict, hot, chain, used: set) -> List[PageObject]:
        """A linked state showing ``spec`` (library, element): the instance forest of a
        group-root symbol, else one tc 0x07 record (format.symbolset.state_records)."""
        return S.state_records(
            handle,
            (spec[0], spec[1], "ctl"),
            info,
            hot,
            chain,
            used,
            lib_path=self.library_path(spec[0], spec[1], "ctl"),
            resolver=self._resolver(),
        )

    def default_set_states(self) -> List[StateSpec]:
        """CtDraw's new-set states (light_1_black / light_1_red) when the
        include chain has them, else none."""
        from ..format import placement

        res = self._resolver()
        if res is None:
            return []
        out = []
        for lib, elem in S.DEFAULT_STATES:
            if placement.element_info(res, lib, elem, "ctl") is None:
                return []
            out.append((lib, elem))
        return out

    def create_symbol_set(
        self,
        pos: Tuple[int, int],
        kind: int = S.ON_OFF,
        exprs: Optional[Sequence[str]] = None,
        states: Optional[Sequence[StateSpec]] = None,
    ) -> int:
        """Draw a new symbol set with its first state's top-left at ``pos``
        (One undo step, like CtDraw's Symbol Set tool: the set plus its state
        records and their import-table entries). ``states`` default = CtDraw's
        two default lights. Returns the new set's index."""
        from ..editing import guard_text
        from ..format import genie_place as GP

        kind = int(kind)
        if kind not in S.TYPE_LABELS:
            raise SymbolSetError("Unknown symbol set type %r." % kind)
        exprs = [str(e or "") for e in exprs] if exprs else [""]
        guard_text(*exprs)
        for e in exprs:
            if len(e) > S.EXPRESSION_MAX:
                raise SymbolSetError(
                    "An expression can be at most %d characters "
                    "(this one has %d)." % (S.EXPRESSION_MAX, len(e))
                )
        specs = list(self.default_set_states() if states is None else states)
        specs = specs[: max(1, S.capacity(kind, len(exprs)))]
        while specs and specs[-1] is None:
            specs.pop()
        infos = [self._state_info(s) if s is not None else None for s in specs]
        first = next((i for i in infos if i is not None), None)
        hot = S.hotspot_for_topleft(pos, first)
        used = set(self._used_handles())
        set_po = S.make_set_record(self._alloc_handle(used), kind, exprs, hot)
        chain = S.set_chain(set_po)
        recs, roots = [], []
        for spec, info in zip(specs, infos):
            h = self._alloc_handle(used)
            if spec is None:
                block = [S.make_dummy_record(h, hot, chain)]
            else:
                block = self._state_records(h, spec, info, hot, chain, used)
            roots.append(block[0])
            recs.extend(block)
        S.finish_set(set_po, roots, len(roots))
        out = [set_po] + recs
        entries = GP.page_link_entries(out, self._resolver())
        return self._append_placed(out, entries, "create symbol set")

    # -- edit ----------------------------------------------------------------
    def apply_symbol_set(
        self,
        index: int,
        kind: int,
        exprs: Sequence[str],
        states: Sequence[StateSpec],
        label: str = "symbol set",
    ) -> int:
        """Apply the Symbol Set sheet as one undo step."""
        from ..editing import guard_text
        from ..format import genie_place as GP

        ok, why = self.symbol_set_editable(index)
        if not ok:
            raise SymbolSetError(why)
        kind = int(kind)
        if kind not in S.TYPE_LABELS:
            raise SymbolSetError("Unknown symbol set type %r." % kind)
        exprs = [str(e or "") for e in (exprs or [""])]
        if kind == S.MULTI_STATE:
            exprs = exprs[: S.MAX_CONDITIONS] or [""]
            while len(exprs) > 1 and not exprs[-1]:
                exprs.pop()
        else:
            exprs = exprs[:1]
        guard_text(*exprs)
        for e in exprs:
            if len(e) > S.EXPRESSION_MAX:
                raise SymbolSetError(
                    "An expression can be at most %d characters "
                    "(this one has %d)." % (S.EXPRESSION_MAX, len(e))
                )
        specs = list(states)[: S.capacity(kind, len(exprs))]
        while specs and specs[-1] is None:
            specs.pop()

        pb = self.parsed.page
        objs = pb.objects
        end = S.subtree_end(objs, index)
        kids = S.direct_children(objs, index)
        old_states = [k for k in kids if objs[k].type_code not in S.INPUT_TCS]
        new_set = _clone(objs[index])
        S.set_expressions(new_set, kind, exprs)
        hot = S.hotspot(new_set)
        chain = S.set_chain(new_set, self._ancestor_chain(index))
        used = set(self._used_handles())

        def block(k):
            return [_clone(objs[j]) for j in range(k, S.subtree_end(objs, k))]

        def state_block(n):
            spec = specs[n]
            old = old_states[n] if n < len(old_states) else None
            if isinstance(spec, int):
                if not (index < spec < end):
                    raise SymbolSetError("State %d refers to a record outside the set." % n)
                return block(spec)
            if spec is None:
                if old is not None and objs[old].type_code == S.DUMMY_TC:
                    return block(old)
                h = geom.get_handle(objs[old]) if old is not None else self._alloc_handle(used)
                return [S.make_dummy_record(h, hot, chain)]
            if old is not None and _same_symbol(S.state_symbol(objs[old]), spec):
                return block(old)
            info = self._state_info(spec)
            h = geom.get_handle(objs[old]) if old is not None else self._alloc_handle(used)
            return self._state_records(h, spec, info, hot, chain, used)

        blocks: List[List[PageObject]] = []
        states_out: List[PageObject] = []
        n = 0
        for k in kids:
            if objs[k].type_code in S.INPUT_TCS:
                blocks.append(block(k))
                continue
            if n < len(specs):
                b = state_block(n)
                blocks.append(b)
                states_out.append(b[0])
            n += 1
        while n < len(specs):
            b = state_block(n)
            blocks.append(b)
            states_out.append(b[0])
            n += 1
        S.finish_set(new_set, states_out, len(blocks))
        new_recs = [new_set] + [r for b in blocks for r in b]
        # the page import table lists every distinct linked element (the
        # entries already there are kept: add_link_entry skips them)
        entries = GP.page_link_entries(new_recs, self._resolver())

        fit = self._fitting_ancestors([index])
        old = (list(objs), pb.link_table_raw, pb.link_count, {a: bytes(objs[a].body_raw) for a in fit})
        order = list(objs[:index]) + new_recs + list(objs[end:])
        probe = type("_LT", (), {})()
        probe.link_table_raw, probe.link_count = pb.link_table_raw, pb.link_count
        probe.area_code = getattr(pb, "area_code", "    ")
        GP.add_link_entries(probe, entries)
        if (
            probe.link_table_raw == pb.link_table_raw
            and len(new_recs) == end - index
            and all(
                writer.object_payload(a) == writer.object_payload(b)
                for a, b in zip(new_recs, objs[index:end])
            )
        ):
            return index  # nothing changed: no undo step

        def apply(state):
            o, raw, cnt, anc = state
            pb.objects[:] = o
            pb.object_count = len(o)
            pb.link_table_raw, pb.link_count = raw, cnt
            for a, body in anc.items():
                pb.objects[a].body_raw = body
                writer.refresh_object_bags(pb.objects[a])
            self._rerender_page()

        apply((order, probe.link_table_raw, probe.link_count, {}))
        for a in fit:
            self._refit_container(a)
        new = (order, probe.link_table_raw, probe.link_count, {a: bytes(objs[a].body_raw) for a in fit})
        if fit:
            self._rerender_page()
        from ..editing import _Command

        self._push_step(_Command(label, lambda: apply(new), lambda: apply(old)))
        return index

    def set_symbol_set_type(self, index: int, kind: int) -> int:
        """Change the type; the first expression carries over and the states
        beyond the new type's capacity are dropped."""
        info = self.symbol_set_info(index)
        exprs = info["expressions"][:1] if info else [""]
        return self.apply_symbol_set(
            index, kind, exprs, self.symbol_set_specs(index), label="symbol set type"
        )

    def set_state_symbol(self, index: int, state: int, symbol: Optional[Tuple[str, str]]) -> int:
        """Set (a (library, element) symbol) or clear (None) state ``state``;
        a state beyond the list pads it with empty slots."""
        info = self.symbol_set_info(index)
        if info is None:
            raise SymbolSetError("The object is not a symbol set.")
        specs = self.symbol_set_specs(index)
        if state < 0 or state >= max(1, info["capacity"]):
            raise SymbolSetError("A %s symbol set has no state %d." % (info["type_label"], state))
        while len(specs) <= state:
            specs.append(None)
        specs[state] = tuple(symbol[:2]) if symbol else None
        return self.apply_symbol_set(
            index,
            info["type"],
            info["expressions"],
            specs,
            label="clear state" if symbol is None else "set state",
        )

    def clear_state(self, index: int, state: int) -> int:
        return self.set_state_symbol(index, state, None)

    def remove_state(self, index: int, state: int) -> int:
        """Delete state ``state``; the later states move up one."""
        info = self.symbol_set_info(index)
        specs = self.symbol_set_specs(index)
        if info is None or not (0 <= state < len(specs)):
            raise SymbolSetError("The symbol set has no state %d." % state)
        del specs[state]
        return self.apply_symbol_set(index, info["type"], info["expressions"], specs, label="remove state")

    # -- V3/V4 Animated Symbol --------------------------------------------------
    def animated_symbol_info(self, index: int) -> Optional[dict]:
        if not self.is_animated_symbol(index):
            return None
        props = self.parsed.page.objects[index].raw_properties()
        return {
            "expression": str(props.get("EXPR", "") or ""),
            "mode": str(props.get("MODE", "") or ""),
            "states": S.animsym_states(props),
        }

    def apply_animated_symbol(
        self, index: int, expression: str, mode: str, states: Sequence[Optional[Tuple[str, str]]]
    ) -> bool:
        """EXPR, MODE and the OFF_SYMBOL / SYMBOL_1..8 states of an Animated
        Symbol (one undo step). Its natural extent follows the first state
        that resolves (legacy.animsym_extent_deltas)."""
        from ..editing import guard_text
        from ..format import legacy, placement

        if not self.is_animated_symbol(index):
            raise SymbolSetError("The object is not an Animated Symbol.")
        states = list(states)[: len(S.ANIMSYM_STATES)]
        states += [None] * (len(S.ANIMSYM_STATES) - len(states))
        vals = ["%s.%s" % (s[0], s[1]) if s else "" for s in states]
        guard_text(expression, mode, *vals)
        res = self._resolver()
        size = None
        for s in states:
            if s and res is not None:
                info = placement.element_info(res, s[0], s[1], "ctl")
                if info is not None:
                    n = info["N"]
                    size = (n[2] - n[0], n[3] - n[1])
                    break

        def mutate(po):
            writer.ensure_object_property(po, "EXPR", expression or "")
            if mode or "MODE" in po.raw_properties():
                writer.ensure_object_property(po, "MODE", mode or "")
            for key, v in zip(S.ANIMSYM_STATES, vals):
                if v:
                    writer.ensure_object_property(po, key, v)
                else:
                    writer.remove_object_property(po, key)
            if size is not None and legacy.class_tail(po) is not None:
                legacy.set_point_extent(po, legacy.animsym_extent_deltas(*size))
            return True

        return bool(self._body_edit("animated symbol", index, mutate))
