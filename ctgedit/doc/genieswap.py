"""Replace Genie: point placed genies at another genie, in place."""

from __future__ import annotations


from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..editing_base import (  # noqa: F401
    PageObject,
    parse_object,
    geom,
    guard_text,
    writer,
)

#: binding keys that are not parameters: the implicit AN and CtDraw's '!*' editor state
_NOT_PARAMS = ("an",)
#: the windows Replace Genie works in (EditableDocument: 'page'; ElementDocument.kind)
SWAP_WINDOWS = ("page", "genie", "template", "supergenie")


def is_param(key: str) -> bool:
    return bool(key) and not key.startswith("!*") and key.lower() not in _NOT_PARAMS


def suggested_mapping(old_params: Iterable[str], new_params: Iterable[str]) -> "OrderedDict[str, str]":
    """{new parameter: '%Old%'} for every new parameter an old one shares the name of
    (without case, the old spelling in the token); the others are left out (their
    default)."""
    by_low = {}
    for k in old_params:
        if is_param(k):
            by_low.setdefault(k.lower(), k)
    out: "OrderedDict[str, str]" = OrderedDict()
    for n in new_params:
        o = by_low.get(n.lower())
        if o is not None and is_param(n):
            out[n] = "%" + o + "%"
    return out


def map_value(text: str, old: Dict[str, str]) -> str:
    """``text`` with each ``%Old%`` replaced by the old binding."""
    from ..format import genie_place as GP

    text = text or ""
    low = {k.lower(): v for k, v in old.items()}
    out = []
    i = 0
    while i < len(text):
        if text[i] != "%":
            out.append(text[i])
            i += 1
            continue
        tok, j = GP.read_token(text, i)
        if tok is None:  # unterminated: the rest as written
            out.append(text[i:])
            break
        nm, dflt = GP.token_name(tok) if tok else ("", None)
        if nm and nm.lower() in low:
            out.append(low[nm.lower()] or dflt or "")
        else:
            out.append(text[i:j])
        i = j
    return "".join(out)


def mapping_tokens(mapping: Dict[str, str]) -> List[str]:
    """The parameter names the mapping texts' ``%tokens%`` name (in order, each once, as
    written; genie_place.template_refs: the composition's reading)."""
    from ..format import genie_place as GP

    return GP.template_refs(GP.Bag([(str(k), v or "") for k, v in mapping.items()]))


class GenieSwapEdits:
    """EditableDocument's Replace Genie."""

    # -- what the dialog shows --------------------------------------------------------
    def genie_instance_bindings(self, index: int) -> "OrderedDict[str, str]":
        """The parameter bindings a placed genie instance holds on its root (its binding
        level, the one refresh_instance re-composes from), in stored order; AN and '!*'
        keys left out."""
        from ..format import genie_place as GP

        out: "OrderedDict[str, str]" = OrderedDict()
        try:
            r0 = GP.decode(self.parsed.page.objects[index])
        except Exception:  # noqa: BLE001 -- swallow-ok: a root that does not decode binds nothing
            return out
        if not r0.entries or len(r0.entries) < 2:
            return out
        for k, v in self._binding_bag(r0).pairs:
            if is_param(k) and k not in out:
                out[k] = v
        return out

    def _swap_window(self) -> str:
        """The window's kind ('page', 'genie', 'template', 'supergenie'), or '' where Replace
        Genie does not work (a symbol)."""
        kind = getattr(self, "kind", "page") or "page"
        return kind if kind in SWAP_WINDOWS else ""

    def _binding_bag(self, r0):
        """The bag an instance's bindings are read from (what refresh_instance re-composes
        from): in a library genie or template window, an instance in the ELEMENT form (a
        level more: its placed level under the element level) keeps them as the typed
        TEMPLATES of its placed level ('%Name% L1'); else the values of its last level."""
        n = len(r0.entries)
        if self._swap_window() in ("genie", "template") and n >= 3:
            lvl = r0.entries[n - 2]
            return lvl[2] if len(lvl) > 2 and lvl[2].pairs else lvl[1]
        return r0.entries[-1][1]

    def replaceable_genies(
        self, indices: Optional[Sequence[int]] = None
    ) -> List[Tuple[int, Tuple[str, str]]]:
        """(root index, (library, element)) of the instances Replace Genie can replace: every
        one on the page (genie_instances), or those among ``indices`` (a selected member
        counts for its instance's root). In a library genie, template or super genie window
        the genies it places (not the copies nested in those)."""
        if not self._swap_window():
            return []
        every = self.genie_instances()
        if indices is None:
            return every
        roots = {i: link for i, link in every}
        picked = []
        sem = self.page.objects if self.page is not None else []
        for i in indices:
            k = i
            while k is not None and k not in roots:
                k = sem[k].parent if 0 <= k < len(sem) else None
            if k is not None and (k, roots[k]) not in picked:
                picked.append((k, roots[k]))
        return sorted(picked)

    # -- the edit -------------------------------------------------------------------
    def replace_genies(
        self,
        indices: Sequence[int],
        library: str,
        element: str,
        mapping: Optional[Dict[str, str]] = None,
        *,
        keep: str = "origin",
        label: str = "replace genie",
        old_params: Iterable[str] = (),
    ) -> int:
        """Replace the genies at ``indices`` with ``library.element``, as one undo step."""
        from ..format import colourstate
        from ..format import genie_place as GP
        from ..format import links

        if keep not in ("topleft", "origin"):
            raise ValueError("keep must be 'topleft' or 'origin'")
        guard_text(library, element, *((mapping or {}).values()))
        if not self._swap_window():
            raise ValueError(
                "Genies can be replaced on a page, or in a library genie, template or super genie."
            )
        path = self.library_path(library, element, "ctm")
        if path is None:
            raise ValueError(
                "Genie %s.%s is not in this project or the projects it includes." % (library, element)
            )
        # (in a genie window: the new genie must not hold the genie edited here)
        why = self.placement_refusal(library, element, "ctm")
        if why:
            raise ValueError(why)
        roots = dict(self.genie_instances())
        todo = sorted(set(int(i) for i in indices))
        bad = [i for i in todo if i not in roots]
        if bad:
            raise ValueError("Object %d is not a genie that can be replaced." % bad[0])
        if not todo:
            return 0
        res = self._resolver()
        if res is None:
            raise ValueError("This page's project libraries could not be read.")
        el = GP.load_element(path, element, compose=True)
        el.library, el.element = library, element
        declared = GP.declared_params(el)
        new_names = [n for n in declared if is_param(n)]
        self._use_tag_formats()
        objs = self.parsed.page.objects
        page_name = self.page.name if self.page is not None else ""
        ref = "%s.%s.<ctm" % (library, element)
        order = list(objs)
        used_all = set(self._used_handles())
        # back to front: a replaced span changes the indexes after it only
        spans = [(i, i + len(self.subtree(i))) for i in todo]
        sizes = {}
        for start, end in sorted(spans, reverse=True):
            inst = objs[start:end]
            old = self.genie_instance_bindings(start)
            m = suggested_mapping(old, new_names) if mapping is None else mapping
            new = self._swapped_instance(
                inst, el, ref, old, m, declared, res, page_name, used_all, keep, known=old_params
            )
            sizes[start] = len(new) - (end - start)
            order[start:end] = new
            try:
                colourstate.derive_dynamic_flags(order, start)
            except Exception:  # noqa: BLE001 -- swallow-ok: the composition's own bits then
                pass
        fit = set(self._fitting_ancestors(todo))
        self._refit_in_order(order, fit)
        pb = self.parsed.page
        t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
        area = t[0] if t else getattr(pb, "area_code", "    ") or "    "
        entries = GP.used_link_entries(order, t[1] if t else [])
        probe = type("_LT", (), {})()
        probe.link_table_raw, probe.link_count = links.serialize_link_table(area, entries)
        probe.area_code = area
        GP.add_link_entries(probe, [GP.link_table_entry(el)])
        new_state = (order, probe.link_table_raw, probe.link_count)
        old_state = (list(objs), pb.link_table_raw, pb.link_count)

        def apply(state):
            o, raw, cnt = state
            pb.objects[:] = o
            pb.object_count = len(o)
            pb.link_table_raw, pb.link_count = raw, cnt
            self._rerender_page()

        self._apply(label, lambda: apply(new_state), lambda: apply(old_state))
        #: the new roots' indexes (the window selects them)
        shift, self.replaced_roots = 0, []
        for start in todo:
            self.replaced_roots.append(start + shift)
            shift += sizes[start]
        # a genie that attaches super genies creates their pages, as a placement does
        self.super_pages = self.instantiate_super_genies(library, element)
        return len(todo)

    def _swap_element_form(self, root: PageObject, bindings: Dict[str, str]) -> bool:
        """Should the new instance take the element form?"""
        from ..format import genie_place as GP

        in_element = self._swap_window() in ("genie", "template")
        levels = len(GP.decode(root).entries or ())
        if levels > (3 if in_element else 2):
            raise ValueError("A genie inside another genie is replaced in that genie's definition.")
        if in_element and levels < 3:
            tokens = GP.template_refs(GP.Bag([(k, str(v)) for k, v in bindings.items()]))
            is_genie = getattr(self, "is_genie_element", None)
            in_element = (bool(tokens) and self._swap_window() == "genie") or bool(is_genie and is_genie())
        return in_element

    def _swapped_instance(
        self,
        inst: Sequence[PageObject],
        el,
        ref: str,
        old: Dict[str, str],
        mapping: Dict[str, str],
        declared: Dict[str, str],
        res,
        page_name: str,
        used_all: set,
        keep: str,
        known: Iterable[str] = (),
    ) -> List[PageObject]:
        """One instance composed from ``el`` in place of ``inst`` (DFS, the root first).
        ``known`` = parameter names a token takes as old ones ('' where ``old`` lacks them:
        replace_genies' ``old_params``)."""
        from ..format import genie_place as GP
        from ..format import placement

        root = parse_object(0, 0, 0, 0, writer.object_payload(inst[0]))
        if not writer.set_body_reference(root, ref):
            raise ValueError("The genie's link could not be rewritten.")
        # the new bindings: every old one dropped, each new parameter mapped or defaulted
        bindings = {k: "" for k in old}
        values = dict({k: "" for k in known if is_param(k)}, **old)
        low_map = {k.lower(): v for k, v in mapping.items()}
        for name, dflt in declared.items():
            if not is_param(name):
                continue
            text = low_map.get(name.lower())
            bindings[name] = map_value(text, values) if text is not None else (dflt or "")
        guard_text(*bindings.values())
        opts = GP.Options(into_genie_element=self._swap_element_form(inst[0], bindings))
        # only the root: the old members name another element's records, so none of them
        # is linked with a new one (their handles are free for the new members)
        outside = used_all - {geom.get_handle(p) for p in inst}
        outside.discard(None)

        def compose(r):
            out = GP.refresh_instance(
                [r], res, page_name, element=el, bindings=bindings, outside_handles=outside, opts=opts
            )
            if not out:
                raise ValueError("%s.%s could not be composed." % (el.library, el.element))
            return out

        new = compose(root)
        if keep == "topleft":
            was = geom.parse_geom_header(inst[0].body_raw)
            now = geom.parse_geom_header(new[0].body_raw)
            if was is not None and now is not None:
                a, b = geom.norm_rect(was.rect1), geom.norm_rect(now.rect1)
                dx, dy = a[0] - b[0], a[1] - b[1]
                if dx or dy:
                    placement.shift_slots(root, [(dx, dy), None, None])
                    new = compose(root)
        # the root's AN string names the element root it links with: the NEW element's
        # (refresh_instance keeps the stored one, right for the same element only)
        h = geom.get_handle(el.records[0]) if el.records else None
        if h is not None and new[0].an_str != str(h):
            new[0] = parse_object(0, 0, 0, 0, writer.object_payload(new[0]))
            new[0].an_str = str(h)
        return self._regrow_alarm_root(list(new))
