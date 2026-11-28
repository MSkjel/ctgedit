"""Genie forms, library links, placement, symbols, composite and super genies."""

from __future__ import annotations

from ..infra.paths import lib_path_dirs, path_key


from typing import TYPE_CHECKING
from ..infra.logsetup import note_exception as _note_exc

if TYPE_CHECKING:
    from collections import OrderedDict
    from typing import Dict

from ..editing_base import (  # noqa: F401
    CODEC,
    COMPOSITE_LOCKED,
    COMPOSITE_NOT_HERE,
    Callable,
    LibraryLinkError,
    List,
    ObjectKind,
    Optional,
    PageObject,
    Sequence,
    Tuple,
    USER_MARKED_KEYS,
    contextlib,
    create_super_genie_pages,
    ctdraw_cache_warning,
    factory,
    geom,
    guard_text,
    hashlib,
    libraries_changed,
    nesting_refusal,
    os,
    parse_ctg,
    struct,
    writer,
)
from ..editing_base import (  # noqa: F401
    Command,
    codes,
    ini_back_color,
    parse_object,
)

#: one read of each library per composition: a copy and its stamp from the same bytes
from ..format.symbols import current_hold, one_read


#: the errors genie_place._refresh_nested and its helpers note where a nested copy then stays
#: as stored (not the ones with a fallback that composes it all the same: _composed_if_grown,
#: _copy_form, _merge_copy_root): an instance with one is not brought up to date
_KEPT_STORED = frozenset(
    {
        "genie_place._refresh_nested",
        "genie_place._load_nested_copy",
        "genie_place._recompose_unaligned",
    }
)


def nested_in_full() -> bool:
    """A placed genie where only a genie or symbol nested in it changed is composed again in
    full (CtDraw's load, the default), not just its nested copies (Settings > Pages and
    libraries: off sets CTG_GENIE_NESTED_COPIES)."""
    import os

    return os.environ.get("CTG_GENIE_NESTED_COPIES") != "1"


def _libraries_moved() -> bool:
    """A library the current composition read (its symbols.held_libraries) was written
    since: what it composed is not what the files hold now, so it is not remembered as the
    files' answer."""
    hold = current_hold()
    return hold is not None and bool(hold.changed())


#: the environment switches a genie composition reads (Settings > Pages and libraries,
#: ui/prefs/settings.apply_library_folders): the nested-copies mode, the extra library folders,
#: the sibling libraries, the text replacement
COMPOSITION_ENV = ("CTG_GENIE_NESTED_COPIES", "CTG_LIB_PATH", "CTG_LIB_SIBLINGS", "CTG_TEXT_REPLACE")


def composition_environment() -> tuple:
    """The values of :data:`COMPOSITION_ENV` now ('' for one not set)."""
    import os

    return tuple(os.environ.get(k, "") for k in COMPOSITION_ENV)


def _keep_root_name(out: list, name: str) -> list:
    """The re-composed root of a placed group-root symbol takes the placement's own record
    name (``name``, the stored root's): the name is the placement's, not the element's, as
    CtDraw's load + save keeps it (the fixture oracle, pages FxSymbols / FxSymbols2)."""
    from ..format import genie_place as GP

    if not out:
        return out
    try:
        rn = GP.decode(out[0])
    except Exception:  # noqa: BLE001
        from ..infra import logsetup

        logsetup.note_exception("library._keep_root_name")
        return out
    if rn.name == (name or ""):
        return out
    rn.name = name or ""
    return [GP.to_po(GP.encode(rn))] + list(out[1:])


def _fold_bag(L, B, bo, bn, tn=None):
    """A bag of a stored record folded with its composed twin (level ``L``, bag ``B``)."""
    from ..format import genie_place as GP

    # as CtDraw's load of stale genies does: a linked record keeps its stored key
    # order with the current values in place and new keys appended; a binding
    # level keeps its stored keys in place, the element's or not, and the levels are then
    # derived again top down, which updates a templated value in place and
    # removes an empty one (a nested instrument's itemName under '%itemName%');
    # a declaration bag is the element's own (its order too)
    if L >= 1 and B == 0:
        return bn
    if L >= 1:
        return GP.merge_bag(bo, bn, templated=GP.bag_keys(bo))
    if B == 2 and tn is not None:
        # a stored template whose key the element still gives as a value stays
        return GP.merge_bag(bo, bn, templated=GP.bag_keys(tn) & GP.bag_keys(bo))
    return GP.merge_bag(bo, bn)


def _fold_level(L, eo, en):
    """Level ``L`` of a stored record (``eo``) folded with its composed twin (``en``)."""
    return [
        _fold_bag(L, B, bo, bn, en[2] if B == 1 else (en[1] if B == 2 else None))
        for B, (bo, bn) in enumerate(zip(eo, en))
    ]


def _merge_whole(n, shells: bool):
    """The composed record ``n`` taken as it is (a fresh object for ``shells``)."""
    from ..format import genie_place as GP

    return GP.to_po(writer.object_payload(n)) if shells else n


def _merge_new_object(n, shells: bool):
    """The composed record ``n`` that links to no stored one, as CtDraw makes it."""
    from ..format import genie_place as GP

    # CtDraw's new object (an element record that links to no stored one): the
    # polygon vertex offsets it generates come after its values (VERTEX_VERT*
    # last in e0.b1, the template bag in the element's order)
    try:
        r = GP.decode(n)
    except Exception:  # noqa: BLE001  # swallow-ok: taken as composed
        return _merge_whole(n, shells)
    if not r.entries:
        return _merge_whole(n, shells)
    bag = r.entries[0][1]
    vert = [p for p in bag.pairs if p[0].upper().startswith("VERTEX_VERT")]
    if not vert:
        return _merge_whole(n, shells)
    pairs = [p for p in bag.pairs if not p[0].upper().startswith("VERTEX_VERT")] + vert
    if pairs == bag.pairs:
        return _merge_whole(n, shells)
    r.entries[0][1] = GP.Bag(pairs, bag.ov)
    return GP.to_po(GP.encode(r))


def _merge_twins(old, new, element_records) -> tuple:
    """(the Element, its records, the stored twin of each composed record: an index into
    ``old`` or None). The records are None when they do not pair with ``new``."""
    from ..format import genie_place as GP

    # the re-composition kept the handle of every record it linked with a stored one
    # (genie_place.link_instance), so the stored twin of a record is the stored record
    # with its handle and class; a record without one is taken as composed
    element = None
    if isinstance(element_records, GP.Element):
        element, element_records = element_records, element_records.records
    if element_records is not None and len(element_records) == len(new):
        twin = GP.link_instance(old, element_records) if old and new else [None] * len(new)
    else:
        element_records = None
        by = {}
        for i in range(1, len(old)):
            by.setdefault((geom.get_handle(old[i]), old[i].type_code), []).append(i)
        twin = [0 if old and new else None]
        for n in new[1:]:
            q = by.get((geom.get_handle(n), n.type_code))
            twin.append(q.pop(0) if q else None)
    return element, element_records, twin


def _fold_with_memory(ro, rn, mem, n, element_form: bool, specials: dict) -> None:
    """``rn``'s levels folded with the stored ``ro``'s and the element's memory values
    ``mem`` (e0's values derived again from the folded templates)."""
    from ..format import genie_place as GP

    composed = rn.entries[0][1]
    vals = GP.merge_bag(ro.entries[0][1], mem)
    templ = GP.bag_keys(rn.entries[0][2])
    have = GP.bag_keys(composed)
    rest = [_fold_level(L, eo, en) for L, (eo, en) in enumerate(zip(ro.entries, rn.entries))]
    # the derived values are set in the FOLDED template order (the stored
    # order with the element's new keys after it), then the composed values
    # without a template: a key the merge dropped comes back in that order
    cd = {kk.lower(): v for kk, v in composed.pairs}
    names = [kk for kk, _v in rest[0][2].pairs if kk.lower() in cd]
    seen = {kk.lower() for kk in names}
    names += [kk for kk, _v in composed.pairs if kk.lower() not in seen]
    for kk in names:
        v = cd[kk.lower()]
        # the composed value as it is (an empty one too: a left-trimmed
        # blank COMMENT stays as '', genie_place)
        at = next((i for i, (k2, _v2) in enumerate(vals.pairs) if k2.lower() == kk.lower()), None)
        if at is None:
            vals.pairs.append((next(k2 for k2, _ in composed.pairs if k2.lower() == kk.lower()), v))
        else:
            vals.pairs[at] = (vals.pairs[at][0], v)
    vals = GP.Bag(
        [(kk, v) for kk, v in vals.pairs if kk.lower() in have or kk.lower() not in templ],
        vals.ov,
    )
    if len(rest) >= 3:
        # the binding levels are derived again from the folded ones, top down,
        # as CtDraw derives after its merge; in the
        # element form a member's levels under the placed one (the level
        # under the element's own) from the placed level's templates as
        # typed, as genie_place._build_recs' member rule does (a nested
        # genie's root from the values: its own level is level 1)
        eff = None
        member_rule = element_form and not (n.class_flag and n.subtype_size & 0x200)
        for lvl in range(len(rest) - 2, 0, -1):
            GP.subst_level(rest[lvl], rest[lvl + 1], specials, outer_vals=eff)
            if eff is not None:
                eff = rest[lvl][1]
            elif member_rule:
                eff = GP.top_level_templates(rest[lvl])
    if len(rest) >= 2:
        # a value whose template names a binding the fold kept (a stored one
        # the element no longer gives) is derived again from it, as CtDraw
        # derives after its merge
        was = {kk.lower(): v for kk, v in rn.entries[1][1].pairs}
        now = {kk.lower(): v for kk, v in rest[1][1].pairs}
        moved = {kk for kk in set(was) | set(now) if was.get(kk) != now.get(kk)}
        for key, tmpl in rn.entries[0][2].pairs if moved else ():
            refs = {t.lower() for t in GP.template_refs(GP.Bag([(key, tmpl)]))}
            if not refs & moved:
                continue
            v = GP.substitute(tmpl, rest[1][1], rest[1][0], key, specials)
            vals.set(key, v)
    rest[0][1] = vals
    rn.entries = rest


def _fold_extra_levels(ro, rn, n) -> None:
    """``rn``'s levels folded with the stored ``ro``'s, which has more levels."""
    from ..format import genie_place as GP

    # the stored record has a binding level the element no longer gives it (a
    # nested genie replaced by a plain group of the same handle and class):
    # e0 and the outer levels pair from both ends, and the extra stored level
    # stays without declarations, as CtDraw's load keeps it
    # (a record that is no longer a nested member keeps its stored levels and
    # their declarations; a GROUP's declarations are its members', none at
    # that level any more; a state set keeps its own, and an element that lost
    # its only parameter leaves the instance's levels as stored)
    extra = len(ro.entries) - len(rn.entries)
    box = bool(n.class_flag) and n.type_code == 0x08
    ents = [_fold_level(0, ro.entries[0], rn.entries[0])]
    for L in range(1, 1 + extra):
        eo = ro.entries[L]
        ents.append([GP.Bag([], 0) if box else eo[0].copy(), eo[1].copy(), eo[2].copy()])
    for L in range(1, len(rn.entries)):
        lvl = _fold_level(L, ro.entries[L + extra], rn.entries[L])
        if not box:
            lvl[0] = ro.entries[L + extra][0].copy()
        ents.append(lvl)
    rn.entries = ents


class LibraryEdits:
    """EditableDocument's genie forms, library links, placement, symbols that follow their library,
    composite genies, super genies."""

    # -- genie forms (the <library>.frm dialog, format/genieform.py) -----------
    def genie_link(self, index: int) -> Optional[Tuple[str, str, str]]:
        """(library, element, ext) the genie / symbol record links to, as its
        link names them; None for an unlinked record."""
        from ..format.symbols import reference_from_object

        try:
            ref = reference_from_object(self.parsed.page.objects[index])
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits.genie_link", index=index)
            ref = None
        if ref is None or not ref.library or not ref.element:
            return None
        return ref.library, ref.element, (ref.ext or "").lower()

    def _project_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.path or ""))

    def genie_form_for(self, library: str, element: str):
        """The genie form the dialog for ``library.element`` shows, or None for the default."""
        from ..format import genieform as GF

        # the page's project, then (a page copied outside its project) the
        # extra library folders the resolver searches too (CTG_LIB_PATH)
        dirs = [self._project_dir()] + lib_path_dirs()
        for d in dirs:
            try:
                form = GF.find_form(d, library, element)
            except Exception:  # noqa: BLE001 -- swallow-ok: a broken .frm must never block the dialog
                form = None
            if form is not None:
                return form
        return None

    def genie_form(self, index: int):
        """The FORM a placed genie's dialog shows (None = the default form)."""
        link = self.genie_link(index)
        if link is None or link[2] != "ctm":
            return None
        return self.genie_form_for(link[0], link[1])

    def genie_form_problems(self, library: str, element: str) -> List[Tuple[str, str]]:
        """(file, reason) of the .frm FORM blocks for ``library.element`` the
        Graphics Builder cannot use (why it shows the default form)."""
        from ..format import genieform as GF

        out: List[Tuple[str, str]] = []
        for d in [self._project_dir()] + lib_path_dirs():
            try:
                for hit in GF.form_problems(d, library, element):
                    if hit not in out:
                        out.append(hit)
            except Exception:  # noqa: BLE001
                _note_exc("library.LibraryEdits.genie_form_problems", library=library, element=element)
        return out

    def genie_an(self, index: int) -> int:
        """The genie's animation number (AN = its handle + 1): the value of
        its %AN% parameter."""
        return geom.live_an(self.parsed.page.objects[index])

    def next_placement_an(self) -> int:
        """The AN the root of the next pasted genie gets (its first fresh
        handle + 1, place_library_element)."""
        return self.next_handle() + 1

    def form_list_values(self, fld) -> Optional[List[str]]:
        """A genie-form field's drop-down (io.project.list_values over this
        page's tag scope; a page copied outside its project reaches its
        project through CTG_LIB_PATH); None when it has no list this install
        can read."""
        from ..io import project as PR

        if fld is None or not getattr(fld, "list_form", ""):
            return None
        try:
            return PR.list_values(self._project_dir(), fld.list_form, fld.list_field, fld.list_filter)
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits.form_list_values")
            return None

    def _recompose_genie(
        self, index: int, bindings: dict, label: str = "genie parameters", dialog_an: bool = False
    ) -> Optional[bool]:
        """Recompose a placed genie from its current element with new ``bindings``."""
        from ..format import genie_place as GP
        from ..format.symbols import reference_from_object

        objs = self.parsed.page.objects
        po = objs[index]
        sem = self.page.objects if self.page is not None else []
        if not po.class_flag or (po.subtype_size & 0x201) != 0x201:
            return None
        # inside plain groups only (CtDraw's page load re-composes those too); never
        # under a linked symbol, a template or another genie: its owner re-composes it
        a = sem[index].parent if index < len(sem) else None
        while a is not None:
            if objs[a].subtype_size & 0x201:
                return None
            a = sem[a].parent
        # (a template window with parameters of its own holds its copies in the element form
        # too: _places_in_element_form)
        in_element = getattr(self, "kind", "") in ("genie", "template")
        try:
            ref = reference_from_object(po)
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits._recompose_genie")
            ref = None
        res = self._resolver()
        if ref is None or ref.ext != "ctm" or res is None:
            return None
        # a genie inside another genie (a member of a library genie: its record has a level
        # per genie around it) is not re-composed: that would write a top-level instance's
        # two levels and drop its authored values (a TAG = '%Tag%_PV' binding). In a genie
        # ELEMENT window the instance has one level more (the element level): it is
        # re-composed in the element form (genie_place.Options.into_genie_element), the
        # members' binding levels and element-level declarations with it
        try:
            levels = len(GP.decode(po).entries or ())
            if levels > (3 if in_element else 2):
                return None
        except Exception:  # noqa: BLE001 -- swallow-ok: a record that does not decode is not re-composed
            return None
        # the element form only in an element with parameters of its own, or one the new
        # bindings give some (a copy in a parameterless element keeps the page form, as
        # CtDraw's genie dialog does)
        if in_element and levels < 3:
            gp_tokens = GP.template_refs(GP.Bag([(k, str(v)) for k, v in bindings.items()]))
            is_genie = getattr(self, "is_genie_element", None)
            in_element = (bool(gp_tokens) and getattr(self, "kind", "") == "genie") or bool(
                is_genie and is_genie()
            )
        # a COMPOSITE genie's parameters live on its members and its element is
        # a composition-cache snapshot -- never re-compose one from it
        if "_compositioncaches" in (ref.library or "").lower() or (
            index < len(sem) and sem[index].kind is ObjectKind.COMPOSITE_GENIE
        ):
            return None
        idx = self.subtree(index)
        if idx != list(range(idx[0], idx[0] + len(idx))):
            return None
        inst = [objs[k] for k in idx]
        self._use_tag_formats()
        used = set(self._used_handles())
        page_name = self.page.name if self.page is not None else ""
        try:
            # shell records: _merge_instance only decodes them again (and parses
            # in full what it keeps)
            new = GP.refresh_instance(
                inst,
                res,
                page_name,
                used,
                bindings=bindings,
                shells=True,
                opts=GP.Options(into_genie_element=in_element, dialog_an=dialog_an),
            )
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits._recompose_genie")
            new = None
        if not new:
            return None
        try:
            erecs = GP.load_element(res.resolve(ref).library_path, ref.element, compose=True)
        except Exception:  # noqa: BLE001  # swallow-ok: records then pair by handle
            erecs = None
        new = self._regrow_alarm_root(
            self._merge_instance(
                inst, new, shells=True, keep_stored=True, element_records=erecs, element_form=in_element
            )
        )
        new = self._instance_in_page_context(objs, idx[0], new)
        if [writer.object_payload(p) for p in new] == [writer.object_payload(p) for p in inst]:
            return False
        pb = self.parsed.page
        old_order = list(objs)
        new_order = old_order[: idx[0]] + list(new) + old_order[idx[-1] + 1 :]

        def apply(order):
            pb.objects[:] = order
            pb.object_count = len(order)
            self._rerender_page()

        self._apply(label, lambda: apply(new_order), lambda: apply(old_order))
        return True

    @staticmethod
    def _instance_in_page_context(objs, start: int, new: list) -> list:
        """Derive the recomposed instance's dynamic flag in its page context."""
        import copy as _copy
        from ..format import colourstate as CS

        if not new:
            return new
        cand = [_copy.copy(q) for q in new]
        order = (
            list(objs[:start]) + cand + list(objs[start + len(new) :])
            if start + len(new) <= len(objs)
            else list(objs[:start]) + cand
        )
        try:
            changed = set(CS.derive_dynamic_flags(order, start))
        except Exception:  # noqa: BLE001 -- swallow-ok: the composition's own bits then
            return new
        return [cand[k] if (start + k) in changed else new[k] for k in range(len(new))]

    def is_composite_genie(self, index: int) -> bool:
        """``index`` is a composite genie or one of its objects (its root, or
        an ancestor, links to a composition cache)."""
        from ..model.document import ObjectKind

        sem = self.page.objects if self.page is not None else []
        if not 0 <= index < len(sem):
            return False
        chain = [index] + list(self.page.ancestors(index))
        for k in chain:
            if sem[k].kind is ObjectKind.COMPOSITE_GENIE:
                return True
            try:
                from ..format.symbols import reference_from_object

                ref = reference_from_object(self.parsed.page.objects[k])
            except Exception:  # noqa: BLE001
                _note_exc("library.LibraryEdits.is_composite_genie", index=k)
                ref = None
            if ref is not None and "_compositioncaches" in (ref.library or "").lower():
                return True
        return False

    def has_composite(self, indices: Sequence[int]) -> bool:
        """One of the records ``indices`` is a placed composite genie's root
        (pass whole subtrees: a group holding a composite counts)."""
        from ..model.document import ObjectKind

        sem = self.page.objects if self.page is not None else []
        objs = self.parsed.page.objects
        for i in indices:
            if not 0 <= i < len(sem):
                continue
            if sem[i].kind is ObjectKind.COMPOSITE_GENIE:
                return True
            po = objs[i] if i < len(objs) else None
            if (
                po is not None
                and po.type_code == 0x08
                and po.class_flag
                and b"_compositioncaches" in bytes(po.body_raw[:512]).lower()
            ):
                return True
        return False

    @property
    def composites_allowed(self) -> bool:
        """A composite genie can be placed in this document (a page; in an
        element window only a template or a super genie -- help 1172148_1)."""
        return True

    def set_genie_binding(self, index: int, key: str, value: str) -> bool:
        """Edit one genie parameter binding (targets the substitution bag).
        False for a composite genie (:data:`COMPOSITE_LOCKED`): its options
        come from its composition cache."""
        guard_text(key, value)
        if self.is_composite_genie(index):
            return False
        po = self.parsed.page.objects[index]
        own = writer.genie_bindings(po)
        # CtDraw's genie dialog OK re-composes the instance from its element
        # with the new value (members' substituted bags, the TEXT STRING
        # SECTIONS the runtime draws, extents, nested genies). Do exactly
        # that for a top-level classic instance; the member re-substitution
        # below is the fallback (nested / unresolvable / composite genies).
        done = self._recompose_genie(index, {key: value})
        if done is not None:
            return done
        if own:
            # classic genie root: update the root binding AND re-substitute every
            # member's current-value bags from their stored %templates% (CtDraw does
            # this on apply; a root-only edit leaves members stale).
            # the full substitution set incl. the implicit AN (members' %AN%)
            own_all = writer.genie_substitution_params(po)
            params = dict(own_all)
            params[key] = value
            root_po = po
            targets = [index] + [j for j in self.subtree(index) if j != index]

            def mutate(p):
                if p is root_po:
                    done = writer.set_genie_binding(p, key, value)
                    # inside a library genie: the authored values too (what the dialog
                    # shows, and what placing the outer genie substitutes from)
                    return writer.set_authored_genie_binding(p, key, value) or done
                if p.type_code in (codes.TC.TOUCH, codes.TC.KEYBOARD):
                    # input records keep their CMD/KEY bindings keyed too
                    if key in writer.genie_bindings(p):
                        writer.set_genie_binding(p, key, value)
                    return True
                if key in writer.genie_bindings(p):
                    writer.set_genie_binding(p, key, value)
                if (
                    writer.resubstitute_member(p, params, old_params=own_all)
                    and p.subtype_size & writer.METADATA_FLAG
                ):
                    # a re-substituted METADATA value: the .ctF keeps the section
                    writer.rebuild_metadata_section(p)
                return True

            return self._multi_body_edit("genie parameters", targets, mutate)
        targets = []
        for j in self.subtree(index):
            if j == index:
                continue
            mpo = self.parsed.page.objects[j]
            if mpo.type_code in self._BEHAVIOUR_TCS:
                continue
            if key in writer.genie_bindings(mpo):
                targets.append(j)
        if not targets:
            return False
        return self._multi_body_edit(
            "genie parameters", targets, lambda p: writer.set_genie_binding(p, key, value)
        )

    def set_genie_bindings(
        self, index: int, values: dict, label: str = "genie parameters", dialog: bool = False
    ) -> bool:
        """Set several genie parameters with one recomposition and one undo step."""
        vals = {str(k): str(v) for k, v in (values or {}).items() if str(k).upper() != "AN"}
        if not vals or self.is_composite_genie(index):
            return False
        guard_text(*vals.keys(), *vals.values())
        done = self._recompose_genie(index, vals, label, dialog_an=dialog)
        if done is not None:
            return done
        changed = False
        with self.batch(label):
            for k, v in vals.items():
                if self.set_genie_binding(index, k, v):
                    changed = True
        return changed

    def _multi_body_edit(
        self,
        label: str,
        indices: Sequence[int],
        mutate: Callable[[PageObject], object],
        rerender: bool = True,
    ) -> bool:
        """Apply one body mutation across several records as a SINGLE undo
        step. Atomic: if any mutate() raises, every record is restored."""
        edits = []  # (po, old_bytes, new_bytes)
        snap = [(self.parsed.page.objects[i], bytes(self.parsed.page.objects[i].body_raw)) for i in indices]
        try:
            for po, old in snap:
                if mutate(po) is False:
                    continue
                new = bytes(po.body_raw)
                if new != old:
                    edits.append((po, old, new))
        except Exception:
            for po, old in snap:
                if po.body_raw != old:
                    po.body_raw = old
                    writer.refresh_object_bags(po)
            raise
        if not edits:
            return False

        def _apply(use_new: bool) -> None:
            for po, old, new in edits:
                po.body_raw = new if use_new else old
                writer.refresh_object_bags(po)
            if rerender:
                self._rerender_page()

        # mutate() already applied the change; register without re-applying
        self._push_step(Command(label, lambda: _apply(True), lambda: _apply(False)))
        self._note_edit()
        if rerender:
            self._rerender_page()
        return True

    # -- a placed symbol's library link (Linkage page, Cut Link) --------------
    def symbol_link(self, index: int) -> Optional[dict]:
        """What the Linkage page shows for record ``index`` placed from a
        library element: {'library', 'element', 'ext', 'path' (None when it
        does not resolve), 'kind' ('symbol' | 'group' | 'template' | 'genie'),
        'reference'}. None for an object without a library link."""
        from ..format.symbols import reference_from_object

        objs = self.parsed.page.objects
        if not 0 <= index < len(objs) or not objs[index].subtype_size & 0x1:
            return None
        po = objs[index]
        try:
            ref = reference_from_object(po)
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits.symbol_link")
            ref = None
        if ref is None:
            return None
        ext = (ref.ext or "ctl").lower()
        kind = {"ctt": "template", "ctm": "genie"}.get(ext, "symbol" if po.type_code == 0x07 else "group")
        return dict(
            library=ref.library,
            element=ref.element,
            ext=ext,
            path=self.library_path(ref.library, ref.element, ext),
            kind=kind,
            reference="%s.%s" % (ref.library, ref.element),
        )

    def _cut_link_problem(self, index: int) -> str:
        """Why the placed symbol ``index`` cannot be cut from its library
        ('' = it can): its link does not resolve, it is a genie, a state of a
        symbol set, a member of a genie or of a transformed group."""
        from ..format import placement

        link = self.symbol_link(index)
        if link is None:
            return "This object is not linked to a library symbol."
        if link["kind"] == "genie":
            return (
                "%s is a genie: its parameters live in the genie definition. Cut Link "
                "works on library symbols." % link["reference"]
            )
        if link["kind"] == "template":
            return "This is the page's template: use Cut Link with nothing selected."
        if link["path"] is None:
            return (
                "%s is not on this project's include path, so there is nothing to copy "
                "in its place." % link["reference"]
            )
        objs = self.parsed.page.objects
        for a in self.page.ancestors(index) if self.page is not None else ():
            if objs[a].type_code == 0x0C:
                return (
                    "This symbol is a state of a symbol set: change the set's states in "
                    "its properties instead."
                )
            if objs[a].subtype_size & 0x200:
                return (
                    "This symbol is part of a genie; the genie places it again when the "
                    "page is opened. Edit the genie definition instead."
                )
            if placement.carries(objs[a]):
                return (
                    "This symbol is inside a group that is itself turned or scaled: "
                    "ungroup it first, then cut its link."
                )
        return ""

    def break_library_link(self, index: int) -> Optional[int]:
        """Edit > Cut Link: replace a placed library symbol with an unlinked copy."""
        from ..format import colourstate
        from ..format import genie_place as GP
        from ..format import links, placement

        objs = self.parsed.page.objects
        link = self.symbol_link(index)
        if link is None or link["ext"] not in ("ctl", "ctm", "ctt"):
            return None
        why = self._cut_link_problem(index)
        if why:
            raise LibraryLinkError(why)
        po = objs[index]
        leaf = po.type_code == 0x07
        # the placement's Total: slot 2 = Local o T(-e) (ancestors carry no
        # transform: _cut_link_problem), and the box it shows
        if leaf:
            info = self._link_info(po)
            if info is None:
                raise LibraryLinkError("%s could not be read from its library." % link["reference"])
            (m1, L, _o, _n), _s2, _s3 = placement.read_slots(po.body_raw)[0]
            T, _mT = placement.slot2_for_linked(L, m1, info["e"])
        else:
            (_s1, (m2, T, _o2, _n2), _s3), _e = placement.read_slots(po.body_raw)
            if not m2:
                T = placement.IDENT
        steps = placement.linear_ops(T)
        if steps is None:
            raise LibraryLinkError(
                "%s is turned by an angle that is not a multiple of 90 degrees; a copy "
                "cannot show it that way. Turn it back first." % link["reference"]
            )
        ops, _scale = steps
        target = geom.norm_rect(geom.parse_geom_header(po.body_raw).rect1)
        kids = self._direct_children(index)
        inputs_ = [k for k in kids if objs[k].type_code in geom.INPUT_TYPE_CODES]
        # the unlinked copy of the element, in element coordinates
        used = set(self._used_handles())
        el = GP.load_element(link["path"], link["element"])
        out = GP.place_symbol(
            self.path,
            link["path"],
            link["element"],
            el.N[0],
            el.N[1],
            linked=False,
            used_handles=used,
            library_name=link["library"],
            element_name=link["element"],
        )
        copies = list(out["records"])
        if not copies:
            raise LibraryLinkError("%s has nothing to copy." % link["reference"])
        own = self._strict_value_bag(index)
        name = self.object_name(index)
        handle = geom.get_handle(po)
        if leaf or copies[0].type_code != 0x08 or not copies[0].class_flag:
            from ..format.element_session import forest_roots

            box = self._detached_extent(copies)
            container = factory.make_group_record(
                handle if handle is not None else self._alloc_handle(used), box, len(forest_roots(copies))
            )
            forest = [container] + copies
        else:
            forest = copies
            geom.set_handle(forest[0], handle)
        root = forest[0]
        root.an_str = factory.NO_LINK
        # the placement's turns, then its size and place (the Arrange rules on
        # the detached forest -- the engine-exact move / resize / turn code)
        for op in ops:
            self._turn_detached(forest, op)
        self._place_detached(forest, target)
        # the symbol's own properties / name / lock go onto the root
        for k, v in own:
            if not writer.set_object_property(root, k, v):
                writer.add_object_property(root, k, v)
        if name:
            geom.set_object_name(root, name)
        root.subtype_size = (root.subtype_size & ~0x4) | (po.subtype_size & 0x4)
        geom.refresh_bags(root)
        # the symbol's touch / keyboard commands belong to the group now; a
        # command record under a linked symbol carries the symbol's frame in
        # its slots (Local T(e), Total = the symbol's Total o Local), under a
        # plain group it has identity slots; copies, so Undo gets the old ones
        moved_inputs = []
        for k in inputs_:
            for j in self.subtree(k):
                cp = parse_object(0, 0, 0, 0, writer.object_payload(objs[j]))
                if [s[:2] for s in placement.read_slots(cp.body_raw)[0]] != [(0, placement.IDENT)] * 3:
                    placement.write_slots(cp, [(0, placement.IDENT)] * 3)
                    writer.refresh_object_bags(cp)
                moved_inputs.append(cp)
        if moved_inputs:
            root.class_flag += len(inputs_)
            forest = forest + moved_inputs
        end = index + len(self.subtree(index))
        order = list(objs)
        order[index:end] = forest
        # dwFlags 0x10: the group carries the symbol's dynamic keys now
        colourstate.derive_dynamic_flags(order, index)
        # the link table: the element's entry goes when nothing uses it any
        # more; the copies' own links (nested symbols, @(labels)) are added
        pb = self.parsed.page
        t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
        area = t[0] if t else getattr(pb, "area_code", "    ") or "    "
        entries = GP.used_link_entries(order, t[1] if t else [])
        probe = type("_LT", (), {})()
        probe.link_table_raw, probe.link_count = links.serialize_link_table(area, entries)
        probe.area_code = area
        # (the copies are the element's stored ones: an element the page lists at its
        # current version keeps the older one they hold, so the page stays out of date)
        GP.add_link_entries(
            probe,
            out.get("link_entries") or [],
            keep_older=True,
            current=GP.current_entry_lookup(self._resolver()),
        )
        new_state = (order, probe.link_table_raw, probe.link_count)
        old_state = (list(objs), pb.link_table_raw, pb.link_count)

        def apply(state):
            o, raw, cnt = state
            pb.objects[:] = o
            pb.object_count = len(o)
            pb.link_table_raw, pb.link_count = raw, cnt
            self._rerender_page()

        self._apply("cut link", lambda: apply(new_state), lambda: apply(old_state))
        return index

    def free_picture(self, index: int) -> bool:
        """Record ``index`` is an embedded bitmap of the page's own: not inside a
        library symbol, genie or template copy (those come back from the library),
        not a symbol-set state, not in a turned or scaled group."""
        from ..format import bitmaps, placement

        objs = self.parsed.page.objects
        if not 0 <= index < len(objs) or bitmaps.dib_section(objs[index]) is None:
            return False
        page = self.page
        if (
            page is not None
            and 0 <= index < len(page.objects)
            and page.objects[index].raw.get("_from_template")
        ):
            return False
        for a in page.ancestors(index) if page is not None else ():
            if objs[a].subtype_size & 0x201 or objs[a].type_code == 0x0C or placement.carries(objs[a]):
                return False
        return not placement.carries(objs[index])

    def link_picture_to_symbol(self, index: int, library: str, element: str) -> int:
        """Replace an embedded bitmap with a linked library symbol."""
        from ..format import colourstate
        from ..format import genie_place as GP

        objs = self.parsed.page.objects
        if not self.free_picture(index):
            raise ValueError(
                "Only a bitmap of the page's own can become a library symbol "
                "(not one inside a symbol, genie or template, or in a turned "
                "group)."
            )
        path = self.library_path(library, element, "ctl")
        if path is None:
            raise ValueError("%s.%s is not on this project's include path." % (library, element))
        po = objs[index]
        l, t, r, b = geom.norm_rect(geom.parse_geom_header(po.body_raw).rect1)
        own = self._strict_value_bag(index)
        name = self.object_name(index)
        handle = geom.get_handle(po)
        cmds = self.input_commands(index)
        used = set(self._used_handles())
        out = GP.place_symbol(
            self.path,
            path,
            element,
            l,
            t,
            linked=True,
            used_handles=used,
            library_name=library,
            element_name=element,
        )
        recs = list(out["records"])
        if len(recs) != 1 or recs[0].type_code != 0x07:
            raise ValueError("%s.%s is not a single picture symbol." % (library, element))
        new = recs[0]
        if handle is not None:
            geom.set_handle(new, handle)  # (the AN stays)
        for k, v in own:
            if not writer.set_object_property(new, k, v):
                writer.add_object_property(new, k, v)
        if name:
            geom.set_object_name(new, name)
        new.subtype_size = (new.subtype_size & ~0x4) | (po.subtype_size & 0x4)
        geom.refresh_bags(new)
        end = index + len(self.subtree(index))
        order = list(objs)
        order[index:end] = [new]
        colourstate.derive_dynamic_flags(order, index)
        pb = self.parsed.page
        probe = type("_LT", (), {})()
        probe.link_table_raw, probe.link_count = pb.link_table_raw, pb.link_count
        probe.area_code = getattr(pb, "area_code", "    ") or "    "
        GP.add_link_entries(probe, out.get("link_entries") or [])
        new_state = (order, probe.link_table_raw, probe.link_count)
        old_state = (list(objs), pb.link_table_raw, pb.link_count)

        def apply(state):
            o, raw, cnt = state
            pb.objects[:] = o
            pb.object_count = len(o)
            pb.link_table_raw, pb.link_count = raw, cnt
            self._rerender_page()

        with self.batch("make library symbol"):
            self._apply("make library symbol", lambda: apply(new_state), lambda: apply(old_state))
            # the commands go on at the symbol's own size, then it takes the bitmap's
            # size (the resize carries them); each step reads the page as derived
            touch = cmds.get("touch")
            keys = cmds.get("keys") or []
            if touch or keys:
                self._derive_in_batch()
                self.set_input_commands(index, touch=touch, keys=keys or None)
            if geom.norm_rect(geom.parse_geom_header(self.parsed.page.objects[index].body_raw).rect1) != (
                l,
                t,
                r,
                b,
            ):
                self._derive_in_batch()
                self.set_object_rect(index, (l, t, r, b))
        return index

    def _derive_in_batch(self) -> None:
        """The page derived from the records now, inside a batch (which otherwise
        derives once at its end): for a step that reads the page."""
        if getattr(self, "_rerender_pending", False):
            self._rerender_pending = False
            self._derive()

    def _strict_value_bag(self, index: int) -> List[Tuple[str, str]]:
        """The record's own properties in stored order (its level-0 value
        bag), '!' editor keys left out."""
        try:
            pairs = writer.value_bag(self.parsed.page.objects[index])
        except Exception:  # noqa: BLE001 -- swallow-ok: a record the strict walk cannot read: no value bag
            pairs = None
        if pairs is None:
            pairs = list(self.object_properties(index).items())
        return [(k, v) for k, v in pairs if not k.startswith("!")]

    @staticmethod
    def _detached_extent(records: Sequence[PageObject]):
        """Union of the ROOT records' rect1 of a detached DFS forest."""
        from ..format.element_session import forest_roots, union_rects

        u1, _u2 = union_rects(list(records), forest_roots(list(records)))
        return u1 or (0, 0, 0, 0)

    @staticmethod
    def _refit_detached(records: List[PageObject], parents, k: int) -> None:
        """Container ``records[k]``'s rect1 / rect2 = the union of its direct
        children (inputs left out), its hotspot (l, b-1) when it followed
        that rule -- the page rule (_refit_container) on a detached forest."""
        kids = [
            j for j, p in enumerate(parents) if p == k and records[j].type_code not in geom.INPUT_TYPE_CODES
        ]
        boxes = [geom.parse_geom_header(records[j].body_raw) for j in kids]
        boxes = [g for g in boxes if g is not None]
        po = records[k]
        gh = geom.parse_geom_header(po.body_raw)
        if not boxes or gh is None:
            return
        if po.type_code == 0x0C:
            r1, r2 = geom.norm_rect(boxes[0].rect1), tuple(boxes[0].rect2)
        else:
            r1 = (
                min(geom.norm_rect(g.rect1)[0] for g in boxes),
                min(geom.norm_rect(g.rect1)[1] for g in boxes),
                max(geom.norm_rect(g.rect1)[2] for g in boxes),
                max(geom.norm_rect(g.rect1)[3] for g in boxes),
            )
            r2 = (
                min(g.rect2[0] for g in boxes),
                min(g.rect2[1] for g in boxes),
                max(g.rect2[2] for g in boxes),
                max(g.rect2[3] for g in boxes),
            )
        old = geom.norm_rect(gh.rect1)
        body = bytearray(po.body_raw)
        if po.type_code == 0x08 and tuple(gh.anchor) == (old[0], old[3] - 1):
            struct.pack_into("<2h", body, gh.anchor_off, int(r1[0]), int(r1[3]) - 1)
        struct.pack_into("<4h", body, gh.rect1_off, *(int(v) for v in r1))
        struct.pack_into("<4h", body, gh.rect2_off, *(int(v) for v in r2))
        po.body_raw = bytes(body)
        writer.refresh_object_bags(po)

    def _turn_detached(self, records: List[PageObject], op: str) -> None:
        """Rotate / mirror a DETACHED record forest (DFS, closed under
        subtrees) about the centre of its roots' union extent by the rules of
        :meth:`_transform_objects` (carriers compose onto their Local, baked
        shapes turn by kind, a bitmap turns its pixels), containers refit."""
        from ..format import placement

        parents = placement.forest_parents(records)
        box = self._detached_extent(records)
        fn, Q, local_op = self._turn_about(box, op)
        conts = {k for k, po in enumerate(records) if po.class_flag and po.type_code in (0x08, 0x0C)}
        self._use_tag_formats()
        placement.turn_records(
            records,
            parents,
            local_op,
            fn,
            self._link_info,
            None,
            conts,
            lambda k, po, inh: self._transform_one(po, fn, op, Q, inh),
        )
        for k in sorted(conts, reverse=True):
            self._refit_detached(records, parents, k)

    def _place_detached(self, records: List[PageObject], target) -> None:
        """Scale and move a DETACHED forest so its roots' union extent becomes
        ``target`` (l, t, r, b): the container-resize rules
        (:func:`placement.scale_records`), then the containers refit; a forest
        of the target's size only moves (exact)."""
        from ..format import placement

        l, t, r, b = self._detached_extent(records)
        nl, nt, nr, nb = (int(v) for v in target)
        if (r - l, b - t) != (nr - nl, nb - nt):
            sx = (nr - nl) / (r - l) if r - l > 1 else 1.0
            sy = (nb - nt) / (b - t) if b - t > 1 else 1.0
            parents = placement.forest_parents(records)
            conts = {k for k, po in enumerate(records) if po.class_flag and po.type_code in (0x08, 0x0C)}
            placement.scale_records(
                records, parents, (sx, 0.0, 0.0, sy, nl - sx * l, nt - sy * t), self._link_info, None, conts
            )
            for k in sorted(conts, reverse=True):
                self._refit_detached(records, parents, k)
            l, t, r, b = self._detached_extent(records)
        if (l, t) != (nl, nt):
            placement.translate_subtrees(records, nl - l, nt - t, self._link_info)

    def relink_symbol(self, index: int, library: str, element: str, ext: str = "ctl") -> bool:
        """Point a placed symbol at another library element."""
        from ..format import genie_place as GP
        from ..format import links, placement

        link = self.symbol_link(index)
        if link is None or link["kind"] not in ("symbol", "group"):
            raise ValueError("Only a placed library symbol can be pointed at another symbol.")
        ext = (ext or "ctl").lower()
        if ext != "ctl":
            raise ValueError("Pick a symbol (.ctl) for a placed symbol.")
        guard_text(library, element)
        path = self.library_path(library, element, ext)
        if path is None:
            raise ValueError("%s.%s is not on this project's include path." % (library, element))
        if (library.lower(), element.lower()) == (link["library"].lower(), link["element"].lower()):
            return False
        res = self._resolver()
        info = placement.element_info(res, library, element, ext) if res else None
        if info is None:
            raise ValueError("%s.%s could not be read from its library." % (library, element))
        objs = self.parsed.page.objects
        po = objs[index]
        # a symbol whose element root is a GROUP is placed as an instance of
        # its objects, a single-object symbol as ONE linked record
        # (genie_place.place_symbol): the object keeps its form, so the new
        # symbol must have the same one
        try:
            r0 = GP.load_element(path, element).records[0]
            grouped = r0.type_code == 0x08 and bool(r0.class_flag)
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits.relink_symbol", lib=path, element=element)
            grouped = info.get("root_tc") == 0x08
        if grouped != (po.type_code != 0x07):
            # a symbol-set state takes any symbol: the set rebuilds the state in the new
            # symbol's form (one tc 0x07 record, or a group-root symbol's instance forest),
            # as its Symbol Set sheet does
            n = self._set_state_number(index)
            if n is not None:
                self.set_state_symbol(n[0], n[1], (library, element))
                return True
            raise ValueError(
                "%s.%s is %s, but this symbol is %s: delete it and place %s.%s instead."
                % (
                    library,
                    element,
                    "a grouped symbol (several objects)" if grouped else "a single picture",
                    "a single picture" if po.type_code == 0x07 else "a grouped symbol (several objects)",
                    library,
                    element,
                )
            )
        ref = "%s.%s.<%s" % (library, element, ext)
        if po.type_code == 0x07:
            new = [parse_object(0, 0, 0, 0, writer.object_payload(po))]
            if not writer.set_body_reference(new[0], ref):
                raise ValueError("The symbol's link could not be rewritten.")
            chain = self._ancestor_chain(index)
            placement.rederive_from_element(new[0], info, chain or None)
            # an_str = the element root record's handle (placed symbols)
            new[0].an_str = str(info["root_handle"]) if info.get("root_handle") is not None else new[0].an_str
            geom.refresh_bags(new[0])
            span = (index, index + 1)  # its input children stay after it
        else:
            span = (index, index + len(self.subtree(index)))
            inst = [parse_object(0, 0, 0, 0, writer.object_payload(o)) for o in objs[span[0] : span[1]]]
            if not writer.set_body_reference(inst[0], ref):
                raise ValueError("The symbol's link could not be rewritten.")
            used = set(self._used_handles())
            new = GP.refresh_instance(
                inst,
                res,
                self.page.name if self.page else "",
                used,
                parent_chain=self._ancestor_chain(index) or None,
            )
            if not new:
                raise ValueError("%s.%s could not be composed." % (library, element))
            new = self._regrow_alarm_root(self._keep_symbol_root(inst, new))
        order = list(objs)
        order[span[0] : span[1]] = new
        if po.type_code != 0x07:
            from ..format import colourstate

            colourstate.derive_dynamic_flags(order, span[0])
        # the containers around it follow its new box: a group that followed
        # the union rule, a symbol set (its extent = its first state's)
        fit = set(self._fitting_ancestors([index]))
        sem = self.page.objects if self.page is not None else []
        par = sem[index].parent if index < len(sem) else None
        if par is not None and objs[par].type_code == 0x0C:
            fit.add(par)
        self._refit_in_order(order, fit)
        pb = self.parsed.page
        t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
        area = t[0] if t else getattr(pb, "area_code", "    ") or "    "
        entries = GP.used_link_entries(order, t[1] if t else [])
        probe = type("_LT", (), {})()
        probe.link_table_raw, probe.link_count = links.serialize_link_table(area, entries)
        probe.area_code = area
        try:
            el = GP.load_element(path, element)
            el.library, el.element = library, element
            GP.add_link_entries(probe, [GP.link_table_entry(el)])
        except Exception:  # noqa: BLE001 -- swallow-ok: the table is a cache; the link is what counts
            pass
        new_state = (order, probe.link_table_raw, probe.link_count)
        old_state = (list(objs), pb.link_table_raw, pb.link_count)

        def apply(state):
            o, raw, cnt = state
            pb.objects[:] = o
            pb.object_count = len(o)
            pb.link_table_raw, pb.link_count = raw, cnt
            self._rerender_page()

        self._apply("change symbol", lambda: apply(new_state), lambda: apply(old_state))
        return True

    def missing_library_objects(self) -> List[Tuple[int, str]]:
        """(root index, link) of every template and genie whose library doesn't open."""
        from ..format import template_place as TP
        from ..format import genie_place as GP
        from ..format.projects import is_project_dir

        dirs = [os.path.dirname(os.path.abspath(self.path))] if self.path else []
        dirs += lib_path_dirs()
        if not any(is_project_dir(d) for d in dirs):
            return []
        res = self._resolver()
        if res is None:
            return []
        objs = self.parsed.page.objects
        out = []
        for a, _b, ref in TP.template_roots(objs):
            try:
                tpl = TP.resolve_template(res, ref)
            except Exception:  # noqa: BLE001  # swallow-ok: unresolvable then
                tpl = None
            if tpl is None:
                link = GP.decode(objs[a]).link or getattr(ref, "raw", "")
                out.append((a, str(link)))
        for i, (lib, elem) in self.genie_instances():
            if "_compositioncaches" in lib.lower():
                continue
            if self.library_path(lib, elem, "ctm") is None:
                out.append((i, str(GP.decode(objs[i]).link or "%s.%s.<ctm" % (lib, elem))))
        return sorted(out)

    def placeholders_for_missing_libraries(self, label: str = "placeholders for missing libraries") -> int:
        """Turn templates and genies with missing libraries into placeholders, as CtDraw's load does."""
        from ..format import genie_place as GP
        from ..format import links

        found = self.missing_library_objects()
        if not found:
            return 0
        pb = self.parsed.page
        objs = list(pb.objects)
        spans = [(i, GP.subtree_end(objs, i), link) for i, link in found]
        gone = set().union(*(range(a, b) for a, b, _l in spans))
        used = {geom.get_handle(p) for k, p in enumerate(objs) if k not in gone}
        used.discard(None)
        order = list(objs)
        made = []
        for a, b, link in spans:  # page order: the lowest free in turn
            h = 0
            while h in used:
                h += 1
            used.add(h)
            made.append((a, b, GP.placeholder_record(objs[a], h, links.strip_stamp(link))))
        for a, b, ph in sorted(made, key=lambda x: -x[0]):
            order[a:b] = [ph]
        raw, cnt = pb.link_table_raw, pb.link_count
        t = links.parse_link_table(raw) if cnt else None
        if t is not None:
            missing = {links.strip_stamp(l).lower() for _a, _b, l in spans}
            # (the entry keeps the bare name, its stamp gone and a zero GUID)
            ents = [
                (bytes(16), links.strip_stamp(n)) if links.strip_stamp(n).lower() in missing else (g, n)
                for g, n in t[1]
            ]
            raw, cnt = links.serialize_link_table(t[0], ents)
        old = (list(objs), pb.link_table_raw, pb.link_count)
        new_state = (order, raw, cnt)

        def apply(state):
            o, r, c = state
            pb.objects[:] = o
            pb.object_count = len(o)
            pb.link_table_raw, pb.link_count = r, c
            self._rerender_page()

        self._apply(label, lambda: apply(new_state), lambda: apply(old))
        return len(spans)

    def placeholder_genies(self) -> List[Tuple[int, str, str, str]]:
        """(index, library, element, ext) of every placeholder genie on the page."""
        from ..format import genie_place as GP

        out = []
        objs = self.parsed.page.objects
        sem = self.page.objects if self.page is not None else []
        ref_el = getattr(self, "element_ref", None)
        own = ref_el[1] if ref_el and self.path.lower().endswith(".ctm") else None
        for i, po in enumerate(objs):
            got = GP.placeholder_genie(po)
            if got is None:
                continue
            a = sem[i].parent if i < len(sem) else None
            inside = False
            while a is not None:
                if objs[a].subtype_size & 0x1:
                    inside = True
                    break
                a = sem[a].parent if a < len(sem) else None
            if inside:
                continue
            ref = got[0]
            path = self.library_path(ref.library, ref.element, "ctm")
            if path is None:
                continue
            if (
                own is not None
                and GP.nesting_chain(path, ref.element, self.path, own, self._resolver()) is not None
            ):
                continue
            out.append((i, ref.library, ref.element, "ctm"))
        return out

    def instantiate_placeholder_genies(self, label: str = "place genies") -> int:
        """Place the real genie for every placeholder, as CtDraw's load does."""
        st = self.placeholder_genie_state()
        if st is None:
            return 0
        order, raw, cnt, placed = st
        pb = self.parsed.page
        old_state = (list(pb.objects), pb.link_table_raw, pb.link_count)

        def apply(state):
            o, r, c = state
            pb.objects[:] = o
            pb.object_count = len(o)
            pb.link_table_raw, pb.link_count = r, c
            self._rerender_page()

        self._apply(label, lambda: apply((order, raw, cnt)), lambda: apply(old_state))
        return placed

    def placeholder_genie_state(self, page_form: bool = False):
        """What instantiate_placeholder_genies would produce, without changing the document."""
        from ..format import genie_place as GP, links

        found = self.placeholder_genies()
        if not found:
            return None
        pb = self.parsed.page
        objs = pb.objects
        self._use_tag_formats()
        res = self._resolver()
        # (an element window, also a new element's before its first save)
        element_doc = getattr(self, "element_ref", None) is not None
        loaded = []
        for i, library, element, ext in found:
            try:
                path = self.library_path(library, element, ext)
                el = GP.load_element(path, element, compose=True)
                el.library, el.element = library, element
                loaded.append((i, el))
            except Exception:  # noqa: BLE001 -- that marker stays as it is
                from ..infra import logsetup

                logsetup.note_exception("editing.instantiate_placeholder_genies", page=self.path)
        if not loaded:
            return None
        mine = {i for i, _el in loaded}
        outside = {geom.get_handle(p) for k, p in enumerate(objs) if k not in mine}
        if element_doc:
            # (the element window holds no root group; CtDraw's element load does)
            root = getattr(self.session, "root", None)
            if root is not None:
                outside.add(geom.get_handle(root))
        outside.discard(None)

        def opts_for(_i, el, params):
            # (the element form as a placement into this element takes it:
            # _places_in_element_form)
            return GP.Options(into_genie_element=self._places_in_element_form(el, params))

        order, entries, done = GP.place_page_placeholders(
            objs,
            loaded,
            outside=outside,
            resolver=res,
            page_name=self.page.name if self.page is not None else "",
            opts_for=opts_for if element_doc and not page_form else None,
            chain_for=self._ancestor_chain,
            where="editing.instantiate_placeholder_genies",
        )
        placed = len(done)
        if not placed:
            return None
        fit = set()
        for i in done:
            fit.update(self._fitting_ancestors([i]))
        self._refit_in_order(order, fit)
        t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
        area = t[0] if t else getattr(pb, "area_code", "    ") or "    "
        # a placeholder's bare entry (no stamp, a zero GUID) takes the placed genie's
        # current one in its place, as CtDraw's load + save writes it (the FxReturned and
        # chain4/placeholder_turned captures)
        fresh = {links.strip_stamp(n).lower(): (g, n) for g, n in entries if not n.startswith("\\")}
        ents = [
            fresh.get(links.strip_stamp(n).lower(), (g, n))
            if not n.startswith("\\") and links.entry_stamp(n) is None
            else (g, n)
            for g, n in (t[1] if t else [])
        ]
        probe = type("_LT", (), {})()
        probe.link_table_raw, probe.link_count = links.serialize_link_table(area, ents)
        probe.area_code = area
        GP.add_link_entries(probe, entries)
        return order, probe.link_table_raw, probe.link_count, placed

    def swap_colours(self, indices: Optional[Sequence[int]], frm, to, *, swap_range: bool = False) -> int:
        """Tools > Swap Colors."""
        from ..format import bitmaps, colourtools as T

        to_p = T.as_pair(to)
        frm_p = None if frm is None else T.as_pair(frm)
        if swap_range and frm_p is not None:
            adj = T.swap_adjustment(frm_p, to_p)
            if adj is not None:
                return self.adjust_colours(indices, adj, label="swap colours")
        objs = self.parsed.page.objects

        def channel(p):
            return to_p if frm_p is None or T.same_pair(p, frm_p) else p

        def pixel(half):
            if frm_p is None:
                return lambda c, v=to_p[half]: v  # a fill: the whole picture
            return lambda c, f=frm_p[half], v=to_p[half]: v if T.same_colour(c, f) else c

        changed = 0
        with self.batch("swap colours"):
            for i in self._colour_targets(indices):
                if bitmaps.dib_section(objs[i]) is not None:
                    # a bitmap's colours are its PIXELS (help 1172093)
                    if self._recolour_bitmap_states(
                        i, pixel(0), pixel(1), add_flash=to_p[0] != to_p[1], label="swap colours"
                    ):
                        changed += 1
                    continue
                if self._map_colours(i, channel, "swap colours"):
                    changed += 1
        return changed

    def adjust_colours(
        self, indices: Optional[Sequence[int]], adjustment, label: str = "adjust colours"
    ) -> int:
        """Tools > Adjust Colors."""
        from ..format import bitmaps, colourtools as T

        objs = self.parsed.page.objects
        if isinstance(adjustment, T.Adjustment):
            if adjustment.is_identity():
                return 0
            pix = T.adjust_pixel_fn(adjustment)

            def channel(p):
                return T.adjust_pair(p, adjustment)

            def bitmap(i):
                return self._recolour_bitmap_states(i, pix, pix, label=label)
        else:
            fn = adjustment

            def one(c):
                return c if T.is_clear(c) else (c & 0xFF000000) | (int(fn(c & 0xFFFFFF)) & 0xFFFFFF)

            def channel(p):
                return one(p[0]), one(p[1])

            def bitmap(i):
                return self.recolour_bitmap(i, fn)

        changed = 0
        with self.batch(label):
            for i in self._colour_targets(indices):
                if bitmaps.dib_section(objs[i]) is not None:
                    changed += bool(bitmap(i))
                    continue
                if self._map_colours(i, channel, label):
                    changed += 1
        return changed

    def colour_owned(self, index: int) -> bool:
        """Is ``index`` part of a placed library element? Swap / Adjust Colors leave those alone."""
        objs = self.parsed.page.objects
        try:
            anc = list(self.page.ancestors(index))
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits.colour_owned", index=index)
            return False
        return any(a < len(objs) and objs[a].subtype_size & 0x201 for a in anc)

    def _colour_targets(self, indices: Optional[Sequence[int]]) -> List[int]:
        """The records Swap / Adjust Colors reach: the selection with its members (a group
        forwards the messages to its children), None = the whole page; placed-element
        members left out (:meth:`colour_owned`)."""
        n = len(self.page.objects)
        sel = range(n) if indices is None else self.expand_to_subtrees(indices)
        return [i for i in sel if i < n and not self.colour_owned(i)]

    def _map_colours(self, index: int, fn, label: str) -> bool:
        """One record's Swap / Adjust colour channels through ``fn`` (a pair
        in, a pair out; colourstate.map_colour_channels); dwFlags 0x10
        follows a flashing result."""
        from ..format import colourstate

        def mutate(po):
            ok = colourstate.map_colour_channels(po, fn)
            if ok:
                self._derive_dynamic(index, po)
            return ok

        return bool(self._body_edit(label, index, mutate))

    def _recolour_bitmap_states(
        self, index: int, fn_primary, fn_flash, *, add_flash: bool = False, label: str = "recolour bitmap"
    ) -> bool:
        """A bitmap's two pictures through their own pixel functions
        (bitmaps.recolour_states), one undo step (in a batch: part of it)."""
        from ..format import bitmaps

        objs = self.parsed.page.objects
        if not 0 <= index < len(objs) or bitmaps.dib_section(objs[index]) is None:
            return False

        def mutate(po):
            if not bitmaps.recolour_states(po, fn_primary, fn_flash, add_flash=add_flash):
                return False
            # (in its derive context: an element member inherits 0x10 from the
            # unwrapped root, which the parsed list does not hold)
            self._derive_dynamic(index, po)
            return True

        return bool(self._body_edit(label, index, mutate))

    def selection_colour(self, indices: Sequence[int]):
        """The colour of the first selected object that has one of its own
        -- what Swap Colors offers as From: a pipe's colour, a text's, a line's
        line colour, a filled shape's fill, a hollow one's line -- as an (on,
        off) pair; None when no selected object has one."""
        from ..format import colourstate

        objs = self.parsed.page.objects
        for i in self._colour_targets(list(indices)):
            po = objs[i]
            ch = colourstate.colour_channels(po)
            if ch is None:
                continue
            if "class" in ch:
                return ch["class"]
            if po.type_code == colourstate.LINE_TC:
                return ch["line"]
            L = colourstate.layout(po)
            filled = L is not None and "style_at" in L and po.body_raw[L["style_at"] + 2] != 0
            return ch["fill"] if filled or po.type_code == colourstate.TEXT_TC else ch["line"]
        return None

    def copy_to_library(
        self, indices: Sequence[int], lib_path: str, element: str, thumbnail: bytes = b"", thumbnail_for=None
    ) -> bool:
        """Edit > Copy to Library: store the selection as a library element."""
        from ..format import element as E

        guard_text(element)
        if not element:
            return False
        if len(element.encode(CODEC, "replace")) > 31:
            raise ValueError("Library element names can be at most 31 characters.")
        idx = sorted(set(self.expand_to_subtrees(indices)))
        idx = [i for i in idx if i < len(self.parsed.page.objects)]
        if not idx:
            raise ValueError("Select the objects to copy into the library.")
        if lib_path.lower().endswith((".ctm", ".ctt")):
            # (stored as a genie the selection holds a copy of, at any depth, a placeholder
            # of it too: the genie would hold itself, which CtDraw refuses to save; stored as
            # a template whose copy the selection holds, directly or through another
            # template's copy: the template would hold itself, which CtDraw refuses to save
            # too: oracle chain7/self_template)
            from ..format import genie_place as GP
            from ..format import template_place as TP

            res = self._resolver()
            sel = [self.parsed.page.objects[i] for i in idx]
            walk = (
                TP.template_nesting_chain if lib_path.lower().endswith(".ctt") else GP.records_nesting_chain
            )
            chain = walk(sel, lib_path, element, res) if res else None
            if chain is not None:
                label = "%s.%s" % (os.path.splitext(os.path.basename(lib_path))[0], element)
                # (the chain only when it goes through other copies)
                via = " (%s)" % GP.cycle_text(chain) if len(chain) > 1 else ""
                raise ValueError(
                    "The selection holds a copy of %s%s, so it cannot be stored as %s." % (label, via, label)
                )
        if self.has_composite(idx) and not (lib_path.lower().endswith(".ctt") or "@super" in element.lower()):
            # help 1172148_1: a composite genie goes into a page, template or
            # super genie, never into a genie or a symbol
            raise ValueError(COMPOSITE_NOT_HERE)
        copies: List[PageObject] = []
        for i in idx:
            blob = writer.object_payload(self.parsed.page.objects[i])
            copies.append(parse_object(0, 0, len(blob), 0, blob))
        ext = os.path.splitext(lib_path)[1].lower().lstrip(".")
        box = self.selection_extent(idx) or (0, 0, 0, 0)
        page_size = None
        origin = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
        if ext == "ctt":  # a template IS a page
            origin = (0, 0)
            page_size = (self.parsed.page.page_width, self.parsed.page.page_height)
        elif "@super" in element.lower():  # super genie: a popup window
            origin = (0, 0)
            page_size = (max(1, box[2]), max(1, box[3]))
        self._use_tag_formats()
        stamp = E.ctdraw_timestamp()
        # the page's entries for what the copies link: the versions they hold (a stale
        # copy is not stamped current in the new element)
        from ..format import genie_place as GP

        carried = GP.used_link_entries(copies, self._link_entries(), keep_super=False)
        content = E.new_element_content(
            copies,
            origin=origin,
            page_size=page_size,
            trailer=E.default_trailer(),
            thumbnail=thumbnail,
            resolver=self._resolver(),
            ext=ext,
            carried=carried,
            lib_path=lib_path,
        )
        content = E.refresh_identity(content, stamp)
        src = self.parsed.container
        E.store_element(
            lib_path, element, content, version=src.version, word2=src.word2, stamp=stamp, newer=True
        )
        libraries_changed()
        behind = thumbnail_for is not None and self._carries_older(carried)
        self._update_copied_element(lib_path, element, thumbnail_for if behind else None)
        self.library_warning = ctdraw_cache_warning()
        return True

    def _carries_older(self, entries) -> bool:
        """A library entry of ``entries`` quotes another stamp than its element's current
        one on this page's search path (the staleness checks' rule: the stamp alone)."""
        from ..format import genie_place as GP
        from ..format import links

        current = GP.current_entry_lookup(self._resolver())
        for _g, n in entries:
            if n.startswith("\\") or "@super" in n.lower():
                continue  # (labels, attachments: no copy to draw)
            cur = current(n)
            if cur is not None and links.entry_stamp(cur[1]) != links.entry_stamp(n):
                return True
        return False

    @staticmethod
    def _update_copied_element(lib_path: str, element: str, thumbnail_for=None) -> None:
        """Re-derive stale placed symbols in the element Copy to Library just stored."""
        from ..editing import ElementDocument
        from ..format import element as E

        try:
            doc = ElementDocument.open_element(lib_path, element)
            sym = doc.stale_symbol_elements() if doc.symbol_placements() else set()
            # (none could be derived: the old stamps stay)
            derived = bool(sym) and bool(doc.update_symbol_placements(sym) or doc.symbol_stamps_updated)
            # (the save composes them: serialize_element with refresh)
            derived = derived or bool(doc.nested_genie_changes())
            if not derived and thumbnail_for is None:
                return
            memo: dict = {}
            content = doc.serialize_element(refresh=derived, memo=memo)
            thumb = doc._thumbnail_as_stored(content, thumbnail_for) if thumbnail_for is not None else None
            if thumb is not None:
                content = doc.serialize_element(thumb, refresh=derived, memo=memo)
            elif not derived:
                return  # (not drawn: the page's picture stays)
            stamp = E.ctdraw_timestamp()
            content = E.refresh_identity(content, stamp)
            lib = doc.session.pe.library
            E.store_element(
                lib_path,
                element,
                content,
                stamp=stamp,
                newer=True,
                version=lib.version,
                word2=lib.word2,
                expect=bytes(doc.session.pe.block.data),
            )
        except Exception:  # noqa: BLE001 -- the element stays as copied (out of date)
            from ..infra import logsetup

            logsetup.note_exception("editing.copy_to_library/symbols", page=lib_path)
            return
        libraries_changed()

    # -- library placement (Paste Genie / Paste Symbol) -------------------------
    def library_path(self, library: str, element: str, ext: str) -> Optional[str]:
        """The file of ``library.element`` on this page's include chain."""
        res = self._resolver()
        if res is None:
            return None
        for lib in res.libraries(library, ext):
            if lib.element(element) is not None:
                return lib.path
        return None

    def library_element_size(self, library: str, element: str, ext: str) -> Optional[Tuple[int, int]]:
        """(width, height) of ``library.element`` as a placement lands it: its root's box
        (placement.element_info, read without composing the genie). None when it does not
        resolve. Settings > Editing 'A placed symbol or genie lands with: Its centre at the
        pointer' centres it with this."""
        from ..format import placement as P

        try:
            res = self._resolver()
            info = None if res is None else P.element_info(res, library, element, (ext or "ctl").lower())
            if info is None:
                return None
            l, t, r, b = geom.norm_rect(info["N"])
        except Exception:  # noqa: BLE001 -- the element is then placed by its top left
            from ..infra import logsetup

            logsetup.note_exception("editing.library_element_size")
            return None
        return (int(r - l), int(b - t))

    def genie_parameters(self, library: str, element: str, ext: str = "ctm") -> "OrderedDict[str, str]":
        """The parameters a paste of genie ``library.element`` asks for, as
        CtDraw's paste dialog lists them (name -> declared default)."""
        from collections import OrderedDict
        from ..format import genie_place as GP

        path = self.library_path(library, element, ext)
        if path is None:
            return OrderedDict()
        # the declarations without composing the genie (a full load of a large genie is slow)
        return GP.declared_params_of(path, element)

    def genie_uses_for(self, library: str, element: str, params=(), ext: str = "ctm"):
        """Where each parameter of genie ``library.element`` goes (features/genie_uses);
        None when the library is not found."""
        from ..features import genie_uses as GU

        path = self.library_path(library, element, ext)
        if path is None:
            return None
        try:
            return GU.element_uses(path, element, list(params or ()))
        except Exception:  # noqa: BLE001
            from ..infra import logsetup

            logsetup.note_exception("library.genie_uses_for")
            return None

    def place_library_element(
        self,
        library: str,
        element: str,
        ext: str,
        pos: Tuple[int, int],
        params: Optional[dict] = None,
        linked: bool = True,
        instantiate: bool = True,
        page_thumbnail=None,
        dialog: bool = False,
    ) -> Optional[int]:
        """Paste a library genie or symbol at ``pos``, the way CtDraw stores it."""
        self.super_pages = []
        idx = self._place_library_element(library, element, ext, pos, params, linked, dialog=dialog)
        if (ext or "").lower() == "ctm" and instantiate and idx is not None:
            self.super_pages = self.instantiate_super_genies(library, element, thumbnail=page_thumbnail)
        return idx

    def placement_cycle(self, library: str, element: str, ext: str = "ctm") -> Optional[List[str]]:
        """In a genie element window: the genies by which genie ``library.element`` is the
        genie edited here or holds a copy of it at any depth (genie_place.nesting_chain:
        ['fxnest.n2', 'fxnest.n1', 'fxnest.n0'] for n2 holding n1 holding n0), so that placed
        here the genie would hold itself; None when it would not (and on a page)."""
        ref = getattr(self, "element_ref", None)
        if not ref or (ext or "").lower() != "ctm" or not self.path.lower().endswith(".ctm"):
            return None
        from ..format import genie_place as GP

        path = self.library_path(library, element, "ctm")
        if path is None:
            return None
        return GP.nesting_chain(path, element, self.path, ref[1], self._resolver())

    def placement_refusal(self, library: str, element: str, ext: str = "ctm") -> str:
        """Why genie ``library.element`` cannot be placed here (editing_base.nesting_refusal:
        it would hold itself, :meth:`placement_cycle`); '' when it can."""
        chain = self.placement_cycle(library, element, ext)
        if chain is None:
            return ""
        host = "%s.%s" % (os.path.splitext(os.path.basename(self.path))[0], self.element_ref[1])
        return nesting_refusal(chain, host)

    def _places_in_element_form(self, el=None, params: Optional[dict] = None) -> bool:
        """Does a genie placed in this window take the element form?"""
        from ..format import genie_place as GP

        kind = getattr(self, "kind", "")
        if kind not in ("genie", "template"):
            return False
        is_genie = getattr(self, "is_genie_element", None)
        if is_genie is not None and is_genie():
            return True
        return kind == "genie" and el is not None and GP.binds_outer_tokens(el, params)

    def _place_library_element(
        self,
        library: str,
        element: str,
        ext: str,
        pos: Tuple[int, int],
        params: Optional[dict] = None,
        linked: bool = True,
        dialog: bool = False,
    ) -> Optional[int]:
        from ..format import genie_place as GP

        ext = (ext or "ctl").lower()
        path = self.library_path(library, element, ext)
        if path is None:
            raise ValueError(
                "Library element %s.%s is not in this project or the "
                "projects it includes." % (library, element)
            )
        if ext == "ctm":
            # (a genie placed into itself, or into a genie it holds a copy of: CtDraw
            # places it and refuses the save; a composition of it would never end)
            why = self.placement_refusal(library, element, ext)
            if why:
                raise ValueError(why)
        guard_text(*(params or {}).values())
        self._use_tag_formats()
        used = set(self._used_handles())
        x, y = int(pos[0]), int(pos[1])
        if ext == "ctm":
            # (a PAGE composition: nested genie copies older than their element are
            # re-composed from it, as refresh_instance does)
            el = GP.load_element(path, element, compose=True)
            el.library, el.element = library, element  # CtDraw writes the caller's spelling
            handles = GP.paste_handles(el.records, used)
            opts = GP.Options(into_genie_element=self._places_in_element_form(el, params), dialog_an=dialog)
            recs = GP.build_instance_records(
                el,
                GP.local_for_topleft(el, x, y),
                dict(params or {}),
                handles=handles,
                page_name=self.page.name if self.page is not None else "",
                resolver=self._resolver(),
                opts=opts,
            )
            entries = GP.instance_link_entries(el, recs)
        else:
            out = GP.place_symbol(
                self.path,
                path,
                element,
                x,
                y,
                linked=linked,
                used_handles=used,
                library_name=library,
                element_name=element,
            )
            recs, entries = out["records"], out["link_entries"]
        return self._append_placed(recs, entries, "place %s.%s" % (library, element))

    def genie_instances(self) -> List[Tuple[int, Tuple[str, str]]]:
        """(root index, (library, element)) of every placed classic genie."""
        from ..format.symbols import reference_from_object

        out = []
        objs = self.parsed.page.objects
        sem = self.page.objects if self.page is not None else []
        for i, po in enumerate(objs):
            if not po.class_flag or (po.subtype_size & 0x201) != 0x201:
                continue
            a = sem[i].parent if i < len(sem) else None
            linked = False
            while a is not None:
                if objs[a].subtype_size & 0x1:
                    linked = True
                    break
                a = sem[a].parent
            if linked:
                continue
            try:
                ref = reference_from_object(po)
            except Exception:  # noqa: BLE001
                _note_exc("library.LibraryEdits.genie_instances")
                ref = None
            if ref is None or ref.ext != "ctm":
                continue
            if "@super" in ref.element.lower() and po.subtype_size & 0x4:
                # the locked root of a super genie PAGE (update_supergenie_page);
                # a super genie pasted onto a page is an ordinary instance
                continue
            if "_compositioncaches" in (ref.library or "").lower():
                # a COMPOSITE genie: update_composites re-derives it (its layout
                # groups' Bounds follow their alarm indicators)
                continue
            out.append((i, (ref.library, ref.element)))
        return out

    #: the stored records' decodes of the last _merge_instance, (record, body,
    #: dwFlags, class_flag, type_code, an_str, Rec) by id: a genie edited again
    #: merges onto the same stored records (read only: the merge never changes them)
    _MERGE_DECODED = {}  # (not a dataclass field: no annotation)

    @staticmethod
    def _decoded_stored(o: PageObject, memo: dict, keep: dict):
        from ..format import genie_place as GP

        hit = memo.get(id(o))
        if (
            hit is not None
            and hit[0] is o
            and hit[1] is o.body_raw
            and hit[2:6] == (o.subtype_size, o.class_flag, o.type_code, o.an_str)
        ):
            r = hit[6]
        else:
            r = GP.decode(o)
        keep[id(o)] = (o, o.body_raw, o.subtype_size, o.class_flag, o.type_code, o.an_str, r)
        return r

    @staticmethod
    def _merge_instance(
        old: List[PageObject],
        new: List[PageObject],
        shells: bool = False,
        keep_stored: bool = False,
        element_records=None,
        element_form: bool = False,
    ) -> List[PageObject]:
        """Merge a recomposed instance onto the stored one the way CtDraw's load keeps it."""
        from ..format import genie_place as GP

        element, element_records, twin = _merge_twins(old, new, element_records)
        memo, keep = _ed().EditableDocument._MERGE_DECODED, {}
        # the special tokens the composition substituted (genie_place._build_recs): '%AN%'
        # is the instance's own AN (its root handle + 1), '%Page%' empty
        h0 = geom.get_handle(new[0]) if new else None
        specials = {"page": "", "an": str(h0 + 1) if h0 is not None else ""}
        out = []
        for k, n in enumerate(new):
            if twin[k] is None:
                out.append(
                    _merge_new_object(n, shells)
                    if element_records is not None and k
                    else _merge_whole(n, shells)
                )
                continue
            o = old[twin[k]]
            try:
                ro, rn = _ed().EditableDocument._decoded_stored(o, memo, keep), GP.decode(n)
            except Exception:  # noqa: BLE001 -- swallow-ok: a record the strict walk cannot read: kept whole
                out.append(_merge_whole(n, shells))
                continue
            mem = None
            if element_records is not None and k and ro.entries and rn.entries:
                try:
                    # (read only: the element's own decode when it is the Element's)
                    me = (
                        GP.shared_decode(element, k) if element is not None else GP.decode(element_records[k])
                    ).entries
                    mem = GP.memory_values(me) if me else None
                except Exception:  # noqa: BLE001  # swallow-ok: the composed values then
                    mem = None
            if mem is not None and len(ro.entries) == len(rn.entries):
                _fold_with_memory(ro, rn, mem, n, element_form, specials)
            elif ro.entries and rn.entries and len(ro.entries) == len(rn.entries):
                rn.entries = [
                    _fold_level(L, eo, en) for L, (eo, en) in enumerate(zip(ro.entries, rn.entries))
                ]
            elif ro.entries and rn.entries and len(ro.entries) > len(rn.entries) >= 1:
                _fold_extra_levels(ro, rn, n)
            if k and ro.entries and rn.entries:
                # the member's own Access values, after the fold: a key the composition
                # lacks comes back at the end; k 0 = the instance root: its page load
                # drops Access keys
                for b in (1, 2):
                    for key, v in ro.entries[0][b].pairs:
                        if key.upper() in USER_MARKED_KEYS and v.endswith("<User>"):
                            rn.entries[0][b].set(key, v)
            # the composed link number: CtDraw's load links an instance record with the element
            # record whose handle its AN string names (AN + class), so a record
            # match_instance linked by that key already has it, and one it only paired by
            # position (the element renumbered / replaced its record) must name its element
            # record now: CtDraw's next load and the .ctF writer's owner rule pair by that
            # string
            if rn.tc == 0x05 and all(
                abs(a - b) <= 1
                for a, b in zip(rn.rect1 + rn.rect2 + rn.anchor, ro.rect1 + ro.rect2 + ro.anchor)
            ):
                rn.rect1, rn.rect2, rn.anchor = ro.rect1, ro.rect2, ro.anchor
            blob = GP.encode(rn)
            # a member the re-composition leaves byte-identical keeps its STORED
            # record (the next derive reuses its parse; undo states share it as
            # they share every record an edit leaves alone); never the root:
            # _regrow_alarm_root patches the root of the returned list in place
            out.append(o if keep_stored and k and blob == writer.object_payload(o) else GP.to_po(blob))
        _ed().EditableDocument._MERGE_DECODED = keep
        return out

    @staticmethod
    def _keep_symbol_root(inst: List[PageObject], new: List[PageObject]) -> List[PageObject]:
        """A recomposed group-root symbol keeps its root's own properties and commands."""
        from ..format import genie_place as GP
        from ..format import placement

        if not new or not inst:
            return new
        out = list(new)
        # the root's own command records (not composed from the element)
        ip, np_ = placement.forest_parents(inst), placement.forest_parents(out)
        if not any(np_[k] == 0 and o.type_code in geom.INPUT_TYPE_CODES for k, o in enumerate(out)):
            extra = []
            for k, o in enumerate(inst):
                if ip[k] == 0 and o.type_code in geom.INPUT_TYPE_CODES:
                    extra.extend(inst[k : GP.subtree_end(inst, k)])
            if extra:
                root = GP.to_po(writer.object_payload(out[0]))
                root.class_flag += sum(
                    1 for k, o in enumerate(inst) if ip[k] == 0 and o.type_code in geom.INPUT_TYPE_CODES
                )
                out = [root] + out[1:] + [GP.to_po(writer.object_payload(o)) for o in extra]
        try:
            ro = GP.decode(inst[0])
        except Exception:  # noqa: BLE001 -- swallow-ok: a record the strict walk cannot read
            return out
        out = _keep_root_name(out, ro.name)
        if not ro.entries or not any(len(ro.entries[0]) > b and ro.entries[0][b].pairs for b in (1, 2)):
            return out
        root = GP.to_po(writer.object_payload(out[0]))
        try:
            rn = GP.decode(root)
            if not rn.entries:
                writer.ensure_bag_section(root)
                rn = GP.decode(root)
        except Exception:  # noqa: BLE001 -- swallow-ok: a record the strict walk cannot read
            return out
        if not rn.entries:
            return out
        for b in (1, 2):
            if b >= len(ro.entries[0]) or b >= len(rn.entries[0]):
                continue
            bag = rn.entries[0][b]
            for key, v in ro.entries[0][b].pairs:
                low = key.lower()
                for j, (k2, _v2) in enumerate(bag.pairs):
                    if k2.lower() == low:
                        bag.pairs[j] = (k2, v)
                        break
                else:
                    bag.pairs.append((key, v))
        return [GP.to_po(GP.encode(rn))] + out[1:]

    def _genie_update_candidates(
        self, objs, res, page_name: str, used: set, want, prev: dict, left: set
    ) -> list:
        """The placed genies update_genie_instances composes again: (lib, elem, idx, inst,
        before, element). A genie it leaves as stored goes into ``left``."""
        from ..format import genie_place as GP
        from ..format.symbols import reference_from_object

        cands = []  # (lib, elem, idx, inst, before, element)
        for i, (lib, elem) in self.genie_instances():
            if want is not None and (lib.lower(), elem.lower()) not in want:
                continue
            idx = self.subtree(i)
            if idx != list(range(idx[0], idx[0] + len(idx))):
                left.add((lib.lower(), elem.lower()))
                continue  # not a contiguous DFS range
            inst = [objs[k] for k in idx]
            before = prev.get((lib.lower(), elem.lower()))
            if before is not None:
                try:
                    was = GP.refresh_instance(inst, res, page_name, used, element=before)
                    was = (
                        self._regrow_alarm_root(
                            self._merge_instance(inst, was, keep_stored=True, element_records=before)
                        )
                        if was
                        else None
                    )
                except Exception:  # noqa: BLE001
                    from ..infra import logsetup

                    logsetup.note_exception("editing._genie_update_candidates/previous", page=self.path)
                    was = None
                same = bool(was) and (
                    [writer.object_payload(p) for p in was] == [writer.object_payload(p) for p in inst]
                )
                if not same:
                    self.genie_update_skipped += 1
                    left.add((lib.lower(), elem.lower()))
                    continue
            from ..infra import logsetup as _ls

            with _ls.error_scope() as errs:
                try:
                    ref = reference_from_object(inst[0])
                    el = GP.load_element(res.resolve(ref).library_path, ref.element, compose=True)
                except Exception:  # noqa: BLE001 -- swallow-ok: unresolvable / malformed: keep it
                    el = None
            if el is None:
                left.add((lib.lower(), elem.lower()))
                continue
            if any(k[0] in _KEPT_STORED for k in errs):
                # a genie nested in it could not be loaded or composed (its library locked
                # then, ...): that copy stays as stored, so the page keeps the element's old
                # stamp (the next run composes it again). The load is remembered for the
                # operation, so the instance's own composition below says nothing more
                left.add((lib.lower(), elem.lower()))
            cands.append((lib, elem, idx, inst, before, el))
        return cands

    def _genie_update_handles(self, objs, cands: list, placeholders) -> tuple:
        """The handles of the candidates and the placeholders placed again: (the
        placeholders (start, end, link, record), the handles outside them, each
        candidate's handles, each placeholder's handle)."""
        from ..format import genie_place as GP

        # the handles of every instance composed again, at once, as CtDraw's load gives them
        # (genie_place.page_instance_handles: every record that links to no stored one is
        # deleted first, page-wide; then the new ones claim their element handles, then the
        # lowest free ones, in page order); a placeholder takes its turn among them
        from ..format import links as _links

        phs = []
        for a, link in placeholders or ():
            if any(a in c[2] for c in cands):
                continue
            b = GP.subtree_end(objs, a)
            phs.append((a, b, link, GP.placeholder_record(objs[a], 0, _links.strip_stamp(link))))
        order_cases = sorted(
            [(c[2][0], "g", ci) for ci, c in enumerate(cands)]
            + [(a, "p", pi) for pi, (a, _b, _l, _r) in enumerate(phs)]
        )
        mine = set().union(*(set(c[2]) for c in cands)) if cands else set()
        mine |= set().union(*(set(range(a, b)) for a, b, _l, _r in phs)) if phs else set()
        outside = {geom.get_handle(p) for k, p in enumerate(objs) if k not in mine}
        outside.discard(None)
        cases, claim = [], []
        for _a, kind, i in order_cases:
            if kind == "g":
                cases.append((cands[i][3], cands[i][5].records))
                claim.append(True)
            else:
                a, b, _l, ph = phs[i]
                cases.append((objs[a:b], [ph]))
                claim.append(False)
        try:
            got = GP.page_instance_handles(cases, outside, claim)
        except Exception:  # noqa: BLE001 -- each instance then allocates on its own
            from ..infra import logsetup

            logsetup.note_exception("editing._genie_update_handles", page=self.path)
            got = [None] * len(cases)
        all_hs = [None] * len(cands)
        ph_h = [None] * len(phs)
        for (_a, kind, i), hs in zip(order_cases, got):
            if kind == "g":
                all_hs[i] = hs
            else:
                ph_h[i] = hs[0] if hs else None
        return phs, outside, all_hs, ph_h

    def _genie_update_recompose(
        self,
        objs,
        cands: list,
        all_hs: list,
        res,
        page_name: str,
        used: set,
        outside: set,
        left: set,
        current: dict,
        repl: list,
    ) -> None:
        """Compose each candidate again with its handles: its replacement into ``repl``,
        its element's current link entry into ``current``, a genie left as stored into
        ``left``."""
        from ..format import genie_place as GP
        from ..infra import logsetup as _ls

        for (lib, elem, idx, inst, before, cel), hs in zip(cands, all_hs):
            with _ls.error_scope() as errs:
                try:
                    new = GP.refresh_instance(inst, res, page_name, used, handles=hs, outside_handles=outside)
                except Exception:  # noqa: BLE001 -- swallow-ok: unresolvable / malformed: keep it
                    new = None
            if not new:
                left.add((lib.lower(), elem.lower()))
                continue
            if any(k[0] in _KEPT_STORED for k in errs):
                # a genie nested in it could not be loaded or composed (its library locked
                # then, ...): its copy stayed as stored, so the page does not take the
                # element's current stamp (the next run composes it again)
                left.add((lib.lower(), elem.lower()))
            new = self._regrow_alarm_root(
                self._merge_instance(inst, new, keep_stored=True, element_records=cel)
            )
            new = self._instance_in_page_context(objs, idx[0], new)
            same = [writer.object_payload(p) for p in new] == [writer.object_payload(p) for p in inst]
            if not same or before is None:
                # the page's link table quotes the element's CURRENT stamp after this, also
                # for an instance that already composes to the same records, as CtDraw's load +
                # save of the page writes it
                path = self.library_path(lib, elem, "ctm")
                if path:
                    try:
                        ent = GP.current_link_entry(path, lib, elem)
                        current[(lib.lower(), elem.lower())] = ent
                    except Exception:  # noqa: BLE001
                        from ..infra import logsetup

                        logsetup.note_exception("editing._genie_update_recompose/stamp", page=self.path)
            if same:
                continue
            used |= {geom.get_handle(p) for p in new if geom.get_handle(p) is not None}
            repl.append((idx[0], idx[-1] + 1, new))

    def _genie_update_link_stamps(self, stamps: dict) -> None:
        """Only the link table's stamps brought up to date (no instance changed), as one
        undo step."""
        from ..format import links

        pb = self.parsed.page
        t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
        if t is not None:
            ents = [stamps.get(links.strip_stamp(n).lower(), (g, n)) for g, n in t[1]]
            raw, cnt = links.serialize_link_table(t[0], ents)
            if (raw, cnt) != (pb.link_table_raw, pb.link_count):
                old_t = (pb.link_table_raw, pb.link_count)

                def put(state):
                    pb.link_table_raw, pb.link_count = state

                self._apply("update genie versions", lambda: put((raw, cnt)), lambda: put(old_t))
                self.genie_stamps_updated = True

    def _genie_update_apply(self, objs, repl: list, stamps: dict) -> int:
        """Put the replacements ``repl`` and the link entries ``stamps`` into the page, as
        one undo step; how many instances changed."""
        from ..format import links

        order = list(objs)
        # plain-group ancestors that followed the union rule take the new one, as CtDraw's
        # load refits them
        refit = []
        for a, _b, _new in repl:
            for anc in self.page.ancestors(a) if self.page is not None else ():
                if anc not in refit and self._container_fits(anc):
                    refit.append(anc)
        for a, b, new in sorted(repl, key=lambda r: -r[0]):
            order[a:b] = new
        self._refit_in_order(order, refit)
        pb = self.parsed.page
        old = (list(objs), pb.link_table_raw, pb.link_count)
        raw, count = pb.link_table_raw, pb.link_count
        t = links.parse_link_table(raw) if count else None
        if t is not None:
            ents = list(t[1])
            if stamps:
                ents = [stamps.get(links.strip_stamp(n).lower(), (g, n)) for g, n in ents]
            # the labels of the texts the update brings in, after their genie's entry, as
            # CtDraw's save lists them (the old ones go at the save's prune)
            ents = self._with_record_labels(ents, [new for _a, _b, new in sorted(repl, key=lambda r: r[0])])
            if ents != list(t[1]):
                raw, count = links.serialize_link_table(t[0], ents)
        new_state = (order, raw, count)

        def apply(state):
            o, r, c = state
            pb.objects[:] = o
            pb.object_count = len(o)
            pb.link_table_raw, pb.link_count = r, c
            self._rerender_page()

        self._apply("update genies from library", lambda: apply(new_state), lambda: apply(old))
        return len(repl)

    @one_read
    def update_genie_instances(self, elements=None, previous=None, placeholders=None) -> int:
        """Recompose placed genies from their current library definition."""
        from ..format import links

        want = None if elements is None else {(a.lower(), b.lower()) for a, b in elements}
        prev = {(a.lower(), b.lower()): v for (a, b), v in (previous or {}).items()}
        self.genie_update_skipped = 0
        res = self._resolver()
        if res is None:
            return 0
        self._use_tag_formats()
        objs = list(self.parsed.page.objects)
        used = set(self._used_handles())
        page_name = self.page.name if self.page is not None else ""
        repl = []  # (start, end, new records)
        stamps = {}
        #: (library, element) lower case -> the element's current entry, and the keys
        #: with an instance not brought up to date (their stored entry stays)
        current, left = {}, set()
        cands = self._genie_update_candidates(objs, res, page_name, used, want, prev, left)
        phs, outside, all_hs, ph_h = self._genie_update_handles(objs, cands, placeholders)
        self._genie_update_recompose(objs, cands, all_hs, res, page_name, used, outside, left, current, repl)
        #: the keys with an instance not brought up to date (apply_genie_changes: then no
        #: 'nothing changes' memo)
        self.genie_update_left = set(left)
        for key, ent in current.items():
            if key not in left:
                stamps[links.strip_stamp(ent[1]).lower()] = ent
        for (a, b, link, ph), h in zip(phs, ph_h):
            if h is not None:
                geom.set_handle(ph, h)
            repl.append((a, b, [ph]))
            stamps[links.strip_stamp(link).lower()] = (bytes(16), links.strip_stamp(link))
        self.genie_stamps_updated = False
        if not repl:
            if previous is None and stamps:
                self._genie_update_link_stamps(stamps)
            return 0
        return self._genie_update_apply(objs, repl, stamps)

    @staticmethod
    def _with_record_labels(entries, spans, after=None) -> list:
        """``entries`` (the page's link table) with the labels of the records of ``spans``
        (each a list of records an update composed; its root's link, else ``after``, names
        the entry they follow) it does not list yet (genie_place.add_record_labels)."""
        from ..format import genie_place as GP

        out = list(entries)
        for recs in spans:
            if not recs:
                continue
            link = after
            if recs[0].subtype_size & 0x1:
                try:
                    link = GP.decode(recs[0]).link or after
                except Exception:  # noqa: BLE001 -- then at the end
                    from ..infra import logsetup

                    logsetup.note_exception("editing._with_record_labels")
            out = GP.add_record_labels(out, recs, after=link)
        return out

    # -- placed library SYMBOLS follow their library (CtDraw's load + save) ---
    def symbol_placements(self) -> List[Tuple[int, Tuple[str, str], str]]:
        """(index, (library, element), 'leaf' | 'group') of every placed library symbol."""
        from ..format import supergenie as SG
        from ..format import template_place as TP
        from ..format.symbols import reference_from_object

        objs = self.parsed.page.objects
        skip = [(a, b) for a, b, _r in TP.template_roots(objs)]
        skip += [(a, b) for a, b, _r in SG.page_roots(objs)]
        out = []
        for i, po in enumerate(objs):
            if not po.subtype_size & 0x1 or po.raw_payload is not None:
                continue
            if any(a <= i < b for a, b in skip):
                continue
            leaf = po.type_code == 0x07
            group = po.type_code == 0x08 and po.class_flag and not po.subtype_size & 0x200
            if not (leaf or group):
                continue
            try:
                ref = reference_from_object(po)
            except Exception:  # noqa: BLE001
                _note_exc("library.LibraryEdits.symbol_placements")
                ref = None
            if ref is None or ref.ext != "ctl":
                continue
            out.append((i, (ref.library, ref.element), "leaf" if leaf else "group"))
        return out

    def stale_symbol_elements(self) -> set:
        """(library, element), lower case, of the library SYMBOLS placed on
        the page whose element changed since the page was saved: the stamp
        the page's link table quotes differs from the element's current one
        (io/updatepages.stale_symbol_entries -- the Update Pages test)."""
        from ..io import updatepages

        res = self._resolver()
        if res is None or not self.parsed.page.link_count:
            return set()
        placed = {(a.lower(), b.lower()) for _i, (a, b), _k in self.symbol_placements()}
        stale = updatepages.stale_symbol_entries(self.parsed.page.link_table_raw, updatepages.Current(res))
        return stale & placed

    def _instances_misaligned(self, key) -> bool:
        """An instance of the placed genie ``key`` ((library, element), lower case) stores
        another record structure (type + child count) than its element composes to now."""
        from ..format import genie_place as GP
        from ..format.symbols import reference_from_object

        res = self._resolver()
        if res is None:
            return False
        objs = self.parsed.page.objects
        sig = None
        for i, (lib, elem) in self.genie_instances():
            if (lib.lower(), elem.lower()) != key:
                continue
            if sig is None:
                try:
                    ref = reference_from_object(objs[i])
                    el = GP.load_element(res.resolve(ref).library_path, ref.element, compose=True)
                except Exception:  # noqa: BLE001  # swallow-ok: left to the nested path
                    return False
                sig = [(po.type_code, po.class_flag) for po in el.records]
            idx = self.subtree(i)
            if [(objs[k].type_code, objs[k].class_flag) for k in idx] != sig:
                return True
        return False

    @one_read
    def update_nested_genie_copies(
        self, changed, label: str = "update changed genies", skip=frozenset()
    ) -> int:
        """Recompose copies of the ``changed`` genies nested in this page's genies."""
        from ..format import genie_place as GP

        only = frozenset((a.lower(), b.lower()) for a, b in changed)
        res = self._resolver()
        if res is None or not only:
            return 0
        self._use_tag_formats()
        objs = list(self.parsed.page.objects)
        used = set(self._used_handles())
        page_name = self.page.name if self.page is not None else ""
        repl = []  # (start, end, new records) in page indexes
        for i, (lib, elem) in self.genie_instances():
            if (lib.lower(), elem.lower()) in only or (lib.lower(), elem.lower()) in skip:
                continue  # placed directly / composed in full
            idx = self.subtree(i)
            if idx != list(range(idx[0], idx[0] + len(idx))):
                continue
            inst = [objs[k] for k in idx]
            info = {}
            try:
                new = GP.refresh_instance(inst, res, page_name, used, only=only, info=info)
            except Exception:  # noqa: BLE001 -- unresolvable / malformed: keep it
                from ..infra import logsetup

                logsetup.note_exception("editing.update_nested_genie_copies")
                new = None
            if not new or not info.get("spans"):
                continue
            for k, end in info["spans"]:
                root = new[k]
                h = geom.get_handle(root)
                s = next(
                    (
                        j
                        for j, po in enumerate(inst)
                        if geom.get_handle(po) == h and po.type_code == root.type_code and po.class_flag
                    ),
                    None,
                )
                if s is None:
                    continue
                n, pending = s, 1
                while pending and n < len(inst):
                    pending -= 1
                    pending += inst[n].class_flag
                    n += 1
                old_sub, new_sub = inst[s:n], new[k:end]

                # a record the stored copy has keeps its handle (CtDraw's load links
                # records by AN string + class): the outer genie's other copies keep
                # their stored structure, so the composition gave records outside its
                # lined-up handles new ones
                # (AN strings repeat inside one copy -- each nested instrument copy
                # numbers its own states alike -- so the key is the AN-string path from
                # the copy's root down to the record, with its type)
                def paths(recs):
                    from ..format import placement as P

                    par = P.forest_parents(recs)
                    out = []
                    for j, po in enumerate(recs):
                        key, q = [], j
                        while q is not None:
                            key.append(recs[q].an_str)
                            q = par[q]
                        out.append((tuple(key), po.type_code))
                    return out

                if len(old_sub) == len(new_sub) and all(
                    o.type_code == n_.type_code and o.class_flag == n_.class_flag
                    for o, n_ in zip(old_sub, new_sub)
                ):
                    # the same structure: every record keeps its stored AN string and
                    # handle (the stored numbering of records a composition adds is the
                    # outer genie's, over ALL its nested copies -- composing only this
                    # copy would number them differently)
                    for o, po in zip(old_sub, new_sub):
                        po.an_str = o.an_str
                        h0 = geom.get_handle(o)
                        if h0 is not None and geom.get_handle(po) != h0:
                            geom.set_handle(po, h0)
                else:
                    stored = {}
                    for key, po in zip(paths(old_sub), old_sub):
                        stored.setdefault(key, []).append(geom.get_handle(po))
                    for key, po in zip(paths(new_sub), new_sub):
                        q = stored.get(key)
                        h0 = q.pop(0) if q else None
                        if h0 is not None and geom.get_handle(po) != h0:
                            geom.set_handle(po, h0)
                merged = self._merge_instance(old_sub, new_sub, keep_stored=True)
                if [writer.object_payload(p) for p in merged] == [writer.object_payload(p) for p in old_sub]:
                    continue
                used |= {geom.get_handle(p) for p in merged if geom.get_handle(p) is not None}
                repl.append((idx[0] + s, idx[0] + n, merged, i))
        if not repl:
            return 0
        order = list(objs)
        for a, b, new, _i in sorted(repl, key=lambda r: -r[0]):
            order[a:b] = new
        pb = self.parsed.page
        old = (list(objs), pb.link_table_raw, pb.link_count)
        raw, cnt = pb.link_table_raw, pb.link_count
        from ..format import links as _links

        t = _links.parse_link_table(raw) if cnt else None
        if t is not None:
            # (the labels of the texts the copies bring in, after their host genie's entry)
            ents = list(t[1])
            for _a, _b, new, i in sorted(repl, key=lambda r: r[0]):
                try:
                    host = GP.decode(objs[i]).link
                except Exception:  # noqa: BLE001 -- then at the end
                    from ..infra import logsetup

                    logsetup.note_exception("editing.update_nested_genie_copies/labels")
                    host = None
                ents = GP.add_record_labels(ents, new, after=host)
            if ents != list(t[1]):
                raw, cnt = _links.serialize_link_table(t[0], ents)

        def apply(state):
            o, r, c = state
            pb.objects[:] = o
            pb.object_count = len(o)
            pb.link_table_raw, pb.link_count = r, c
            self._rerender_page()

        self._apply(label, lambda: apply((order, raw, cnt)), lambda: apply(old))
        return len(repl)

    def apply_record_ops(
        self,
        ops,
        link_table=None,
        label: str = "update genies from library",
        rerender: bool = True,
        facts: Optional[dict] = None,
    ) -> Optional[int]:
        """Apply a genie update computed in another process."""
        from ..format import genie_place as GP

        objs = list(self.parsed.page.objects)
        shas = [hashlib.sha1(writer.object_payload(p)).hexdigest() for p in objs]

        def fits(j, olds, anchor):
            return shas[j : j + len(olds)] == list(olds) and (
                (j == 0 and anchor is None) or (j > 0 and shas[j - 1] == anchor)
            )

        found = []
        for i1, i2, olds, news, anchor in ops:
            at = (
                i1
                if fits(i1, olds, anchor)
                else next((j for j in range(len(shas) - len(olds) + 1) if fits(j, olds, anchor)), None)
            )
            if at is None:
                return None
            found.append((at, at + len(olds), news))
        spans = sorted(found, key=lambda r: r[0])
        for (a1, b1, _n1), (a2, _b2, _n2) in zip(spans, spans[1:]):
            if a2 < b1:
                return None  # overlapping: not the page the answer is about
        rows = [(p, False) for p in objs]
        for a, b, news in sorted(found, key=lambda r: -r[0]):
            rows[a:b] = [(GP.to_po(bytes(x)), True) for x in news]
        order = [p for p, _new in rows]
        pb = self.parsed.page
        old = (list(objs), pb.link_table_raw, pb.link_count)
        raw, cnt = pb.link_table_raw, pb.link_count
        roots = (facts or {}).get("roots")
        # (the genies it composed again: a copy of one added meanwhile was not composed, also
        # when the table does not change, a nested genie's change; and below, those whose
        # entry it restamps or adds)
        current = set((facts or {}).get("composed") or ())
        if link_table is not None:
            from ..format import links

            new_raw, new_cnt, old_raw, old_cnt = link_table
            base = links.parse_link_table(old_raw) if old_cnt else ("    ", [])
            theirs = links.parse_link_table(new_raw) if new_cnt else ("    ", [])
            if (pb.link_table_raw or b"", pb.link_count) == (old_raw, old_cnt):
                raw, cnt = new_raw, new_cnt
            else:
                # the page's table changed since the request: what the answer changed in it
                # is merged into it
                mine = links.parse_link_table(pb.link_table_raw) if pb.link_count else ("    ", [])
                if base is None or theirs is None or mine is None:
                    return None  # (a table that cannot be read: asked again)
                ents = links.merge_link_entries(base[1], theirs[1], mine[1])
                if ents != mine[1]:
                    raw, cnt = links.serialize_link_table(mine[0], ents)
            if base is not None and theirs is not None:
                current |= links.changed_references(base[1], theirs[1])
        if roots is not None and current and self._copy_added_meanwhile(rows, roots, current):
            return None
        self._free_clashing_handles(rows, (facts or {}).get("fresh"))
        if not found and (raw, cnt) == (pb.link_table_raw, pb.link_count):
            return 0

        def apply(state, derive=True):
            o, r, c = state
            pb.objects[:] = o
            pb.object_count = len(o)
            pb.link_table_raw, pb.link_count = r, c
            if derive:
                self._rerender_page()

        first = [not rerender]

        def do():
            apply((order, raw, cnt), derive=not first[0])
            first[0] = False

        self._apply(label, do, lambda: apply(old))
        return len(found)

    @staticmethod
    def _free_clashing_handles(rows, fresh) -> int:
        """Give new records whose handle clashes with a user-added record the lowest free handles."""
        from ..format import genie_place as GP

        fresh = set(fresh or ())
        if not fresh:
            return 0
        clash = fresh & {geom.get_handle(p) for p, new in rows if not new}
        if not clash:
            return 0
        used = {geom.get_handle(p) for p, _new in rows}
        used.discard(None)
        next_free = GP.handle_allocator(used)
        remap: dict = {}
        n = 0
        for p, new in rows:
            h = geom.get_handle(p) if new else None
            if h in clash:
                if h not in remap:
                    remap[h] = next_free()
                geom.set_handle(p, remap[h])
                n += 1
        return n

    @staticmethod
    def _copy_added_meanwhile(rows, roots, current: set) -> bool:
        """Is this a genie copy the page gained since the request was made?"""
        from ..workers import genie_worker as GW
        from ..format.symbols import reference_from_object

        if not isinstance(roots, dict):
            roots = dict.fromkeys(roots)
        for p, new in rows:
            if new or not p.class_flag or (p.subtype_size & 0x201) != 0x201:
                continue
            h = geom.get_handle(p)
            if h in roots and (roots[h] is None or roots[h] == GW.root_signature(p)):
                continue
            try:
                ref = reference_from_object(p)
            except Exception:  # noqa: BLE001  # swallow-ok: an unreadable link names nothing
                continue
            if ref is not None and ("%s.%s.<%s" % (ref.library, ref.element, ref.ext)).lower() in current:
                return True
        return False

    def apply_open_updates(
        self, symbols: bool = True, genies: bool = True, template: bool = True, placeholders: bool = True
    ) -> List[str]:
        """Apply the updates the editor does when it opens a page, in the same order."""
        done: List[str] = []
        if self.read_only:
            return done
        steps = []
        missing = []
        if placeholders:
            # a template or genie whose library does not open becomes CtDraw's placeholder:
            # with the genies, which allocate the handles together (a template's first)
            missing = self.missing_library_objects()
            tpl = [m for m in missing if m[1].lower().endswith(".<ctt")]
            if tpl or not genies:
                steps.append(("placeholders", lambda: self.placeholders_for_missing_libraries()))
                missing = []
            # (a placeholder an element holds, in a placed genie, its library still missing:
            # its box derived as CtDraw's load does)
            steps.append(("placeholder boxes", lambda: self.rederive_placeholder_boxes()))
        if template:
            steps.append(
                ("template", lambda: self.update_template() if self.template_status()[0] == "stale" else 0)
            )
        if symbols:
            steps.append(
                (
                    "symbols",
                    lambda: (
                        self.update_symbol_placements(self.stale_symbol_elements())
                        if self.symbol_placements()
                        else 0
                    ),
                )
            )
        steps.append(
            (
                "placeholder genies",
                lambda: self.instantiate_placeholder_genies() if self.placeholder_genies() else 0,
            )
        )
        if genies:

            def _genies():
                ch = self.genie_changes()
                # (the placeholders' root indexes as the earlier steps left them)
                ph = self.missing_library_objects() if missing else None
                return self.apply_genie_changes(ch, placeholders=ph) if (ch or ph) else 0

            steps.append(("genies", _genies))
        for what, step in steps:
            try:
                n = step()
            except Exception:  # noqa: BLE001 -- the page stays as it was read
                from ..infra import logsetup

                logsetup.note_exception("editing.apply_open_updates/" + what, page=self.path)
                continue
            if n:
                done.append("%s %s" % (n, what))
        return done

    def genie_changes(self) -> dict:
        """What changed in the genies placed on this page since it was saved
        (io/updatepages.genie_changes over the link table in memory): {(library, element):
        None} = the element itself changed, {(library, element): {nested genies}} = only
        genies nested in it changed. Placed genies only (not the page template)."""
        from ..io import updatepages

        res = self._resolver()
        if res is None or not self.parsed.page.link_count:
            return {}
        placed = {(a.lower(), b.lower()) for _i, (a, b) in self.genie_instances()}
        if not placed:
            return {}
        ch = updatepages.genie_changes(self.parsed.page.link_table_raw, updatepages.Current(res))
        out = {k: v for k, v in ch.items() if k in placed}
        for k in self.placeholder_hosts():
            out[k] = None
        return out

    def placeholder_boxes_due(self) -> List[int]:
        """Placeholder markers whose box CtDraw's load would reset."""
        from ..format import genie_place as GP

        objs = self.parsed.page.objects
        if not any(po.type_code == 0xFFFF for po in objs):
            return []
        sem = self.page.objects if self.page is not None else []
        out = []
        for i, _key in self.genie_instances():
            end = GP.subtree_end(objs, i)
            for k in range(i + 1, end):
                po = objs[k]
                got = GP.placeholder_genie(po)
                if got is None or self.library_path(got[0].library, got[0].element, "ctm") is not None:
                    continue  # (one that opens: the host is composed again)
                p = sem[k].parent if k < len(sem) else None
                if p is not None and objs[p].type_code == 0x0C:
                    continue  # (a symbol set's state)
                want = self._origin_box(po)
                gh = geom.parse_geom_header(po.body_raw)
                if (
                    want is not None
                    and gh is not None
                    and (tuple(gh.anchor), tuple(gh.rect1), tuple(gh.rect2)) != want
                ):
                    out.append(k)
        return out

    @staticmethod
    def _origin_box(po) -> Optional[tuple]:
        """(anchor, rect1, rect2) of a free DummyMarker at its Total's origin, the empty rect
        (genie_place._build_recs' load rule)."""
        from ..format import placement as P

        try:
            m, T = P.slot3(po)
        except Exception:  # noqa: BLE001 -- its box stays
            from ..infra import logsetup

            logsetup.note_exception("editing._origin_box")
            return None
        h, k = (int(T[4]), int(T[5])) if m else (0, 0)
        return (h, k), (h, k, h, k), (h, k, h, k)

    def rederive_placeholder_boxes(self, label: str = "placeholder boxes") -> int:
        """:meth:`placeholder_boxes_due` derived again, as CtDraw's page load does: each
        marker the empty rect at its Total's origin, the containers that follow the union
        rule (the genie root) refitted. One undo step; returns how many changed."""
        from ..format import genie_place as GP, placement as P

        due = self.placeholder_boxes_due()
        if not due:
            return 0
        pb = self.parsed.page
        objs = pb.objects
        order = list(objs)
        fit = self._fitting_ancestors(due)
        for k in due:
            po = GP.to_po(writer.object_payload(objs[k]))
            anchor, r1, r2 = self._origin_box(po)
            P.write_header_geom(po, anchor, r1, r2)
            order[k] = po
        self._refit_in_order(order, fit)
        old = list(objs)

        def apply(o):
            pb.objects[:] = o
            pb.object_count = len(o)
            self._rerender_page()

        self._apply(label, lambda: apply(order), lambda: apply(old))
        return len(due)

    def placeholder_hosts(self) -> set:
        """Genies holding a placeholder whose nested genie now resolves."""
        from ..format import genie_place as GP

        objs = self.parsed.page.objects
        if not any(po.type_code == 0xFFFF for po in objs):
            return set()
        out = set()
        for i, (lib, elem) in self.genie_instances():
            end = GP.subtree_end(objs, i)
            for po in objs[i + 1 : end]:
                got = GP.placeholder_genie(po)
                if got is not None and self.library_path(got[0].library, got[0].element, "ctm") is not None:
                    out.add((lib.lower(), elem.lower()))
                    break
        return out

    @one_read
    def apply_genie_changes(self, changes: dict, placeholders=None, force: bool = False) -> int:
        """Bring the page's genies up to date as one undo step."""
        # every composition below in the context the instances are composed in (the tag
        # formats a member text is measured with), so its memo entries are ones the update
        # can use
        self._use_tag_formats()
        self.genie_update_left = set()
        #: this run left something as stored (an instance left, a swallowed error): the
        #: caller tries again later (genie_worker: no remembered answer; the tab's
        #: genie_sources_done is emptied)
        self.genie_update_unfinished = False
        direct = {k for k, v in changes.items() if v is None}
        full = nested_in_full()
        if full:
            # a genie where only a genie or symbol nested in it changed is composed again in
            # full too, as CtDraw's load does: its other members are derived again with it
            # (its input records move to their library twins' points; the nested copies
            # alone would leave them as stored)
            direct |= {k for k, v in changes.items() if v is not None}
        if not changes and not placeholders:
            return 0
        memo_key = None if force else self.genie_noop_key(changes)
        if memo_key is not None:
            from ..infra import noop_memo

            if noop_memo.known(memo_key):
                return 0  # (composed before with these files: no change)
        if not full:
            # a genie whose element now composes to another structure than an instance of
            # it stores (a nested genie gained or lost records) is composed again in full, as
            # CtDraw's load does (the nested copies alone cannot be spliced into a stored
            # instance that no longer lines up)
            direct |= {k for k, v in changes.items() if v is not None and self._instances_misaligned(k)}
        nested = set()
        for k, v in changes.items():
            if v is not None and k not in direct:
                nested |= set(v)
        if not direct and not nested and not placeholders:
            return 0
        steps = len(self._undo)
        n = 0
        from ..infra import logsetup

        with logsetup.error_scope() as errors:
            with self.batch("update genies from library"):
                if direct or placeholders:
                    n += self.update_genie_instances(direct, placeholders=placeholders) or 0
                if nested:
                    if getattr(self, "_rerender_pending", False):
                        # the copies are found through the derived model (subtree, the
                        # parents), which the batch re-derives only at its end: the
                        # instances above may have changed the record count
                        self._rerender_pending = False
                        self._derive()
                    n += self.update_nested_genie_copies(nested, skip=direct) or 0
        # 'nothing changes' is remembered only for a run that composed everything it was
        # asked: an instance left (its library unreadable at that moment, a composition
        # that failed) or a swallowed error makes no undo step either, and the memo (a file
        # shared by the editor, the helpers and Update Pages) would skip the page on every
        # later run until a file changed
        failed = bool(errors) or bool(self.genie_update_left)
        self.genie_update_unfinished = failed
        if memo_key is not None and len(self._undo) == steps and not failed and not _libraries_moved():
            from ..infra import noop_memo

            noop_memo.remember(memo_key)
        return n

    def genie_noop_key(self, changes: dict) -> Optional[str]:
        """The key under which 'composing these genie changes changes nothing' is
        remembered (infra/noop_memo): the page bytes, the changes and the library files
        they are composed from. None when it cannot be made."""
        try:
            from ..infra import noop_memo

            sources = self.genie_sources(set(changes))
            dig = self.genie_page_digest()
            return noop_memo.key(
                None,
                changes,
                {"%s.%s" % k: [list(x) for x in v] for k, v in sources.items()},
                page_sha1=dig,
                mode="full" if nested_in_full() else "copies",
                context=self._genie_memo_context(),
            )
        except Exception:  # noqa: BLE001 -- no memo, the work is done
            _note_exc("library.LibraryEdits.genie_noop_key")
            return None

    def genie_page_digest(self, data: Optional[bytes] = None) -> bytes:
        """The SHA-1 the genie memo keys this page by."""

        dig = (
            getattr(self, "_open_sha1", None) if not getattr(self, "_undo", None) and not self.dirty else None
        )
        if dig is None:
            dig = hashlib.sha1(self.serialize() if data is None else data).digest()
        return dig

    def _genie_memo_context(self) -> tuple:
        """The inputs besides the page that a genie composition depends on."""
        from ..format import textmetrics

        env = composition_environment() + (textmetrics.measure_context(),)
        tags: tuple = ()
        path = getattr(self, "path", "") or ""
        if path:
            try:
                from ..io import tagscope as TS

                sc = TS.scope(os.path.dirname(os.path.abspath(path)))
                near = {id(p) for p in sc.near()}
                tags = tuple(
                    (os.path.normcase(tp), st)
                    for p, tp, st in sc.stamped_tables("variable.dbf")
                    if id(p) in near
                )
            except Exception:  # noqa: BLE001 -- the memo keyed without them
                from ..infra.logsetup import note_exception

                note_exception("doc.library._genie_memo_context")
        return env + (tags, self._palette_stamp())

    def _palette_stamp(self) -> tuple:
        """The colour palette file the composition reads (format/colourpalette.palette_file
        of the page's folder: its own citect.pal, else the install's) as (path, mtime_ns,
        size, content tag); () when there is none."""
        from ..format import colourpalette
        from ..format.fileio import content_tag

        pdir = os.path.dirname(os.path.abspath(getattr(self, "path", "") or ""))
        try:
            pal = colourpalette.palette_file(pdir)
            if not pal:
                return ()
            st = os.stat(pal)
            return (path_key(pal), st.st_mtime_ns, st.st_size, content_tag(pal))
        except OSError:  # swallow-ok: a palette that went: keyed as none
            return ()

    def stale_genie_elements(self) -> set:
        """(library, element) of the placed genies that are out of date."""
        from ..io import updatepages

        res = self._resolver()
        if res is None or not self.parsed.page.link_count:
            return set()
        placed = {(a.lower(), b.lower()) for _i, (a, b) in self.genie_instances()}
        if not placed:
            return set()
        stale = updatepages.stale_genie_entries(self.parsed.page.link_table_raw, updatepages.Current(res))
        return stale & placed

    def genie_sources(self, keys) -> dict:
        """{(library, element) lower case: the library files that placed genie is composed
        from, with their modification times} (io/updatepages.genie_sources), for the genies
        ``keys`` the page's link table names. An open-check uses it to see whether anything
        a genie is made of changed since the page was last updated from it."""
        from ..format import links
        from ..io import updatepages

        res = self._resolver()
        if res is None or not self.parsed.page.link_count:
            return {}
        want = {(a.lower(), b.lower()) for a, b in keys}
        t = links.parse_link_table(self.parsed.page.link_table_raw)
        cur = updatepages.Current(res)
        out = {}
        from ..format.symbols import split_lib_element

        for _g, name in t[1] if t else []:
            base = links.strip_stamp(name)
            stem, _, ext = base.rpartition(".")
            lib, elem = split_lib_element(stem)  # the LAST dot
            k = (lib.lower(), elem.lower())
            if k in want and k not in out and ext.lstrip("<").lower() == "ctm":
                out[k] = updatepages.genie_sources(base, cur)
        return out

    def symbol_status(self) -> Tuple[str, List[str]]:
        """('none' | 'current' | 'stale', ['lib.element', ...]): whether the
        page places library symbols and whether any changed in its library
        since the page was saved."""
        placed = self.symbol_placements()
        if not placed:
            return "none", []
        stale = self.stale_symbol_elements()
        if not stale:
            return "current", []
        names = sorted(
            {"%s.%s" % key for _i, key, _k in placed if (key[0].lower(), key[1].lower()) in stale},
            key=str.lower,
        )
        return "stale", names

    @one_read
    def update_symbol_placements(self, elements=None) -> int:
        """Re-derive placed library symbols from their current elements."""
        from ..format import genie_place as GP
        from ..format import links

        want = None if elements is None else {(a.lower(), b.lower()) for a, b in elements}
        #: the link table took new stamps (the page must be saved even when no
        #: placement moved: its fast display bakes the element's new art) --
        #: reset first: a call that returns early changed nothing
        self.symbol_stamps_updated = False
        res = self._resolver()
        if res is None:
            return 0
        self._use_tag_formats()
        places = [
            p for p in self.symbol_placements() if want is None or (p[1][0].lower(), p[1][1].lower()) in want
        ]
        if not places:
            return 0
        n = 0
        left: set = set()  # (library, element) lower case not brought up
        with self.batch("update symbols from library"):
            n += self._rederive_leaf_symbols([p for p in places if p[2] == "leaf"], left)
            n += self._recompose_symbol_groups([p for p in places if p[2] == "group"], res, left)
            # the link table quotes each element's current identity + stamp (an element
            # only when every placement of it was brought up to date)
            keys = {(lib, elem) for _i, (lib, elem), _k in places if (lib.lower(), elem.lower()) not in left}
            stamps = {}
            for lib, elem in keys:
                path = self.library_path(lib, elem, "ctl")
                if not path:
                    continue
                try:
                    ent = GP.current_link_entry(path, lib, elem)
                    stamps[links.strip_stamp(ent[1]).lower()] = ent
                except Exception:  # noqa: BLE001  # swallow-ok: an element gone keeps its entry
                    pass
            pb = self.parsed.page
            t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
            if t is not None and stamps:
                ents = []
                for g, nm in t[1]:
                    base = links.strip_stamp(nm)
                    cur = stamps.get(base.lower())
                    if cur is None or nm.startswith("\\"):
                        ents.append((g, nm))
                        continue
                    # the name keeps the page's own spelling; guid + stamp move
                    ents.append((cur[0], base + cur[1][len(links.strip_stamp(cur[1])) :]))
                raw, cnt = links.serialize_link_table(t[0], ents)
                if (raw, cnt) != (pb.link_table_raw, pb.link_count):
                    old = (pb.link_table_raw, pb.link_count)

                    def put(state):
                        pb.link_table_raw, pb.link_count = state

                    self._apply("update symbol versions", lambda: put((raw, cnt)), lambda: put(old))
                    self.symbol_stamps_updated = True
        return n

    def _rederive_leaf_symbols(self, places, left=None) -> int:
        """Re-derive linked symbol records in place."""
        from ..format import placement

        objs = self.parsed.page.objects
        sem = self.page.objects if self.page is not None else []
        targets = []
        for i, key, _kind in places:
            info = self._link_info(objs[i])
            if info is not None:
                targets.append((i, info))
            elif left is not None:
                left.add((key[0].lower(), key[1].lower()))
        if not targets:
            return 0
        idx = [i for i, _info in targets]
        fit = set(self._fitting_ancestors(idx))
        for i in idx:  # a symbol set's extent is its first state's
            a = sem[i].parent if i < len(sem) else None
            if a is not None and objs[a].type_code == 0x0C:
                fit.add(a)
        fit = sorted(fit, reverse=True)  # deepest first
        before = {k: (bytes(objs[k].body_raw), objs[k].subtype_size) for k in idx + fit}
        changed = 0
        for i, info in targets:
            chain = self._ancestor_chain(i)
            if placement.rederive_from_element(objs[i], info, chain or None):
                geom.refresh_bags(objs[i])
                changed += 1
        if not changed:
            return 0
        for a in fit:
            self._refit_container(a)
        self._touch_model(idx)
        after = {k: (bytes(objs[k].body_raw), objs[k].subtype_size) for k in before}

        def put(state):
            for k, (body, fl) in state.items():
                objs[k].body_raw = body
                objs[k].subtype_size = fl
                writer.refresh_object_bags(objs[k])
            self._touch_model(list(state))
            self._rerender_page()

        self._apply("update symbols from library", lambda: put(after), lambda: put(before))
        return changed

    def _recompose_symbol_groups(self, places, res, left=None) -> int:
        """Recompose linked group-root symbols from their current elements."""
        from ..format import colourstate
        from ..format import genie_place as GP

        objs = list(self.parsed.page.objects)
        used = set(self._used_handles())
        page_name = self.page.name if self.page is not None else ""
        repl = []
        spans = []
        for i, key, _kind in sorted(places):
            lkey = (key[0].lower(), key[1].lower())
            idx = self.subtree(i)
            if idx != list(range(idx[0], idx[0] + len(idx))):
                if left is not None:
                    left.add(lkey)
                continue
            if any(a <= i < b for a, b in spans):
                continue  # inside an instance re-composed here already
            spans.append((idx[0], idx[-1] + 1))
            inst = [objs[k] for k in idx]
            try:
                new = GP.refresh_instance(
                    inst, res, page_name, used, parent_chain=self._ancestor_chain(i) or None
                )
            except Exception:  # noqa: BLE001 -- swallow-ok: unresolvable / malformed: keep it
                new = None
            if not new:
                if left is not None:
                    left.add(lkey)
                continue
            new = self._keep_symbol_root(inst, new)
            new = self._regrow_alarm_root(self._merge_instance(inst, new))
            # dwFlags 0x10 in its page context (the root's own animation --
            # VISIBLE, movement -- and a dynamic parent) before comparing
            tmp = list(objs)
            tmp[idx[0] : idx[-1] + 1] = new
            colourstate.derive_dynamic_flags(tmp, idx[0])
            if [writer.object_payload(p) for p in new] == [writer.object_payload(p) for p in inst]:
                continue
            used |= {geom.get_handle(p) for p in new if geom.get_handle(p) is not None}
            repl.append((idx[0], idx[-1] + 1, new, i))
        if not repl:
            return 0
        order = list(objs)
        # plain-group ancestors that followed the union rule take the new one
        # (page indices before the change; records compared by IDENTITY -- a
        # PageObject is a dataclass, == compares its fields)
        refit = []
        for _a, _b, _new, i in repl:
            for anc in self.page.ancestors(i) if self.page is not None else ():
                if anc not in refit and self._container_fits(anc):
                    refit.append(anc)
        for a, b, new, _i in sorted(repl, key=lambda r: -r[0]):
            order[a:b] = new
        self._refit_in_order(order, refit)
        pb = self.parsed.page
        old = list(objs)

        def apply(o):
            pb.objects[:] = o
            pb.object_count = len(o)
            self._rerender_page()

        self._apply("update symbols from library", lambda: apply(order), lambda: apply(old))
        return len(repl)

    def _refit_in_order(self, order: List[PageObject], containers) -> None:
        """Refit containers inside a new record list, deepest first."""
        from ..format import placement

        if not containers:
            return
        objs = self.parsed.page.objects
        parents = placement.forest_parents(order)
        pos = {id(o): k for k, o in enumerate(order)}
        for a_old in sorted(
            {a for a in containers if id(objs[a]) in pos}, key=lambda a: -pos[id(objs[a])]
        ):  # deepest first
            anc = objs[a_old]
            k = pos[id(anc)]
            kids = [
                j for j, p in enumerate(parents) if p == k and order[j].type_code not in geom.INPUT_TYPE_CODES
            ]
            boxes = [geom.parse_geom_header(order[j].body_raw) for j in kids]
            boxes = [g for g in boxes if g is not None]
            if not boxes:
                continue
            if anc.type_code == 0x0C:
                from ..format import symbolset as SS

                real = [g for j, g in zip(kids, boxes) if order[j].type_code != SS.DUMMY_TC]
                g0 = (real or boxes)[0]
                u1, u2 = tuple(geom.norm_rect(g0.rect1)), tuple(g0.rect2)
            else:
                u1 = (
                    min(geom.norm_rect(g.rect1)[0] for g in boxes),
                    min(geom.norm_rect(g.rect1)[1] for g in boxes),
                    max(geom.norm_rect(g.rect1)[2] for g in boxes),
                    max(geom.norm_rect(g.rect1)[3] for g in boxes),
                )
                # (the Bounds leave a zero-size child out, structure._union_rects)
                b2 = [
                    g for g in boxes if not (g.rect2[0] == g.rect2[2] and g.rect2[1] == g.rect2[3])
                ] or boxes
                u2 = (
                    min(g.rect2[0] for g in b2),
                    min(g.rect2[1] for g in b2),
                    max(g.rect2[2] for g in b2),
                    max(g.rect2[3] for g in b2),
                )
                u2 = self._alarm_bounds(a_old, u2)
            cp = parse_object(0, 0, 0, 0, writer.object_payload(anc))
            gh = geom.parse_geom_header(cp.body_raw)
            old1 = geom.norm_rect(gh.rect1)
            body = bytearray(cp.body_raw)
            if anc.type_code == 0x08 and tuple(gh.anchor) == (old1[0], old1[3] - 1):
                struct.pack_into("<2h", body, gh.anchor_off, int(u1[0]), int(u1[3]) - 1)
            struct.pack_into("<4h", body, gh.rect1_off, *(int(v) for v in u1))
            struct.pack_into("<4h", body, gh.rect2_off, *(int(v) for v in u2))
            cp.body_raw = bytes(body)
            writer.refresh_object_bags(cp)
            order[k] = cp

    def _append_placed(self, recs: List[PageObject], entries, label: str) -> int:
        """Append a placed instance and its link entries as one undo step."""
        from ..format import genie_place as GP

        pb = self.parsed.page
        old = (list(pb.objects), pb.link_table_raw, pb.link_count)
        at = len(pb.objects)
        self._note_composite_links(recs)
        probe = type("_LT", (), {})()
        probe.link_table_raw, probe.link_count = pb.link_table_raw, pb.link_count
        probe.area_code = getattr(pb, "area_code", "    ")
        GP.add_link_entries(
            probe, entries, keep_older=True, current=GP.current_entry_lookup(self._resolver())
        )
        new = (old[0] + list(recs), probe.link_table_raw, probe.link_count)

        def apply(state):
            order, raw, count = state
            pb.objects[:] = order
            pb.object_count = len(order)
            pb.link_table_raw, pb.link_count = raw, count
            self._rerender_page()

        self._apply(label, lambda: apply(new), lambda: apply(old))
        return at

    # -- composite genies (Visual Templates) ---------------------------------
    def _composite_links_seen(self, data: Optional[bytes] = None) -> set:
        """(template guid, instance hash) of every composite instance this page linked in the session."""
        seen = self.__dict__.get("_composite_seen")
        if seen is None:
            from ..format import composer_store as CS

            if data is None:
                # the file as opened, kept by _composite_links_opened
                data = self.__dict__.pop("_composite_open_data", None)
            if data is None:
                try:
                    data = self.serialize()
                except Exception:  # noqa: BLE001 -- swallow-ok: nothing to read yet
                    data = b""
            seen = self._composite_seen = set(CS.links_in(bytes(data or b"")))
        return seen

    def _composite_links_opened(self, data: bytes) -> None:
        """At open: the composite links of the file as opened, LAZILY (only
        Pack Libraries and a paste / placement read them). A file without the
        '\\{' every link has (composer_store._REF_RE) links none; else its
        bytes are kept until first needed."""
        self.__dict__.pop("_composite_seen", None)
        self.__dict__.pop("_composite_open_data", None)
        if b"\\{" not in data:
            self._composite_seen = set()
        else:
            self._composite_open_data = data

    def _note_composite_links(self, recs) -> None:
        """Remember the composition-cache links of records put on the page."""
        from ..format import composer_store as CS

        seen = self._composite_links_seen()
        for po in recs or ():
            if po.type_code == 0x08 and po.class_flag and po.subtype_size & 0x1:
                seen |= CS.links_in(bytes(po.body_raw or b""))

    def composite_links(self) -> set:
        """The composition-cache instances Pack Libraries must keep for this
        open document: what its page links now plus what it linked earlier in
        the session (an Undo can restore those)."""
        from ..format import composer_store as CS

        out = set(self._composite_links_seen())
        try:
            out |= CS.links_in(self.serialize())
        except Exception:  # noqa: BLE001 -- swallow-ok: nothing to read yet; the links seen still count
            pass
        return out

    def composite_templates(self) -> dict:
        """guid -> composites.CompositeTemplate of every Visual Template of
        this page's project and the projects it includes."""
        from ..format import composites

        return composites.load_templates(self.path or "")

    def composite_link(self, index: int) -> Optional[Tuple[str, str, str]]:
        """(template guid, instance hash, library as the link spells it) of a
        placed composite genie ROOT; None for anything else."""
        from ..format import composites
        from ..format.symbols import reference_from_object

        objs = self.parsed.page.objects
        if not 0 <= index < len(objs) or objs[index].type_code != 0x08 or not objs[index].class_flag:
            return None
        try:
            ref = reference_from_object(objs[index])
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits.composite_link", index=index)
            ref = None
        got = composites.instance_ref(ref.raw) if ref is not None else None
        if got is None:
            return None
        return got[0], got[1], ref.library

    def composite_info(self, index: int):
        """composites.CompositeInfo (template, stored options, cache folder)
        of the placed composite genie ``index``, or None."""
        from ..format import composites

        link = self.composite_link(index)
        if link is None:
            return None
        ref = "%s.%s.<ctm" % (link[2], link[1])
        return composites.composite_info(self.path or "", ref, self.composite_templates())

    def _compose_context(self):
        """The composer's view of this page's include chain."""
        from ..format import composer_build as CB

        shared = getattr(self, "_cctx_shared", None)
        if shared is not None:
            return shared
        res = self._resolver()
        ctx = getattr(self, "_cctx", None)
        if ctx is None or ctx.resolver is not res or ctx.page_path != (self.path or ""):
            ctx = self._cctx = CB.ComposeContext(self.path or "", res)
        return ctx

    def _composite_flag_sizes(self):
        try:
            return self._compose_context().flag_sizes()
        except Exception:  # noqa: BLE001 -- the default flag box still applies
            _note_exc("library.LibraryEdits._composite_flag_sizes")
            from ..format import alarmind

            return [alarmind.DEFAULT_FLAG]

    def compose_composite(
        self, guid: str, values: Optional[dict], *, final: bool = True, force: bool = False
    ):
        """Compose composite genie ``guid`` with ``values`` and update the project cache."""
        from ..format import composer_store as CS
        from ..format import composites

        tpl = self.composite_templates().get((guid or "").lower().strip("{}"))
        if tpl is None:
            raise ValueError(
                "The composite genie template {%s} is not in the Composite Genies "
                "folder of this project or the projects it includes." % guid
            )
        if tpl.problems:
            raise ValueError(
                "The composite genie template %s has problems and cannot be used "
                "(%s)." % (tpl.path, tpl.problems[0])
            )
        bad = composites.invalid_parameters(tpl, composites.typed_values(tpl, values or {}))
        if bad:
            name, why = next(iter(bad.items()))
            raise ValueError("The option %s is not valid: %s" % (name, why))
        if not self.path:
            raise ValueError(
                "Save the page into a project first: the composite genie is "
                "composed into the project's composition cache."
            )
        guard_text(*[str(v) for v in (values or {}).values()])
        # the content genies' texts are measured with this project's tag
        # formats (like every other placement)
        self._use_tag_formats()
        from ..render.headless_thumb import composite_with_thumbnail

        try:
            res = CS.compose(
                self.path,
                tpl.path,
                values or {},
                ctx=self._compose_context(),
                background=CS.background_colour(ini_back_color()),
                final=final,
                force=force,
                with_thumbnail=composite_with_thumbnail,
            )
        except CS.ComposeError as e:
            raise ValueError(str(e)) from e
        self.composite_files = list(getattr(self, "composite_files", [])) + res.written
        if res.written:
            libraries_changed()
            try:
                from ..infra import logsetup

                logsetup.files_written("composed composite genie %s (%s)" % (tpl.name, res.hash), res.written)
            except Exception:  # noqa: BLE001 -- swallow-ok: logging never fails a compose
                pass
        return res

    def _composite_records(self, res, *, pos=None, inst=None, lib: str = "", parent_chain=None):
        """The page records of a composed composite instance."""
        from ..format import composer_build as CB
        from ..format import genie_place as GP

        self._use_tag_formats()
        el = GP.load_element(res.library_path, res.hash)
        page_name = self.page.name if self.page is not None else ""
        used = set(self._used_handles())
        if inst is None:
            el.library, el.element = res.library, res.hash
            x, y = int(pos[0]), int(pos[1])
            next_free = GP.handle_allocator(used)
            handles = [next_free() for _ in range(GP.handle_count(el))]
            recs = GP.build_instance_records(
                el,
                GP.local_for_topleft(el, x, y),
                {},
                handles=handles,
                page_name=page_name,
                resolver=self._resolver(),
                parent_chain=parent_chain,
            )
        else:
            lib = lib or res.library
            copies = [parse_object(0, 0, 0, 0, writer.object_payload(o)) for o in inst]
            if not writer.set_body_reference(copies[0], "%s.%s.<ctm" % (lib, res.hash)):
                raise ValueError("The composite genie's link could not be rewritten.")
            recs = GP.refresh_instance(
                copies, self._resolver(), page_name, used, element=el, parent_chain=parent_chain
            )
            if not recs:
                raise ValueError("The composite genie could not be composed from %s." % res.library_path)
            recs = self._merge_instance(inst, recs)
            el.library, el.element = lib, res.hash
        CB.regrow_bounds(recs, self._composite_flag_sizes())
        return recs, GP.instance_link_entries(el, recs)

    def paste_composite_genie(
        self, guid: str, pos: Tuple[int, int], params: Optional[dict] = None
    ) -> Optional[int]:
        """Edit > Paste Composite Genie."""
        if not self.composites_allowed:
            raise ValueError(COMPOSITE_NOT_HERE)
        res = self.compose_composite(guid, params or {})
        recs, entries = self._composite_records(res, pos=pos)
        return self._append_placed(recs, entries, "paste composite genie")

    def set_composite_options(self, index: int, values: dict) -> bool:
        """Recompose a placed composite with new options."""
        info = self._composite_info_checked(index)
        merged = dict(info.values())
        low = {k.lower(): k for k in merged}
        for k, v in (values or {}).items():
            merged[low.get(str(k).lower(), str(k))] = v
        res = self.compose_composite(info.guid, merged)
        return self._relink_composite(index, res, "composite genie options")

    def update_composite(self, index: int, force: bool = False) -> bool:
        """Update one placed composite genie if it's stale."""
        info = self._composite_info_checked(index)
        if not force and self.composite_status(index) == "current":
            return False
        # no options file (deleted by hand): the options the dialog shows too
        vals = (
            info.instance.update_values(info.template)
            if info.instance is not None
            else dict(self.genie_bindings(index))
        )
        res = self.compose_composite(info.guid, vals, final=False)
        return self._relink_composite(index, res, "update composite genie")

    def _composite_info_checked(self, index: int):
        info = self.composite_info(index)
        if info is None:
            raise ValueError("The object is not a composite genie.")
        if info.template is None or info.template_source != "project":
            raise ValueError(
                "The template of this composite genie is not in the Composite Genies folder "
                "of this project or the projects it includes, so it cannot be composed "
                "again."
            )
        return info

    def composite_status(self, index: int) -> str:
        """'current'; 'stale' (its template, a content item or the alarm flags
        changed since it was composed, or the page's copy is older than its
        cache element); 'missing' (its options file or element is gone) or
        'unknown' (no template in the project)."""
        from ..format import composer_store as CS

        info = self.composite_info(index)
        if info is None:
            return "unknown"
        if info.template is None or info.template_source != "project":
            return "unknown"
        if info.instance is None or not info.cache_dir:
            return "missing"
        try:
            state = CS.state_of(
                self.path or "",
                info.template.path,
                info.instance.values(),
                info.cache_dir,
                info.hash,
                ctx=self._compose_context(),
            )
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits.composite_status")
            return "unknown"
        if state != "current":
            return state
        return "stale" if self._composite_page_stale(index, info.cache_dir) else "current"

    def _composite_page_stale(self, index: int, cache_dir: str) -> bool:
        """The page's link entry quotes an older stamp of the instance element
        than the cache holds (another page's update re-composed it)."""
        from ..format import genie_place as GP
        from ..format import links

        link = self.composite_link(index)
        pb = self.parsed.page
        t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
        if link is None or not t:
            return False
        try:
            cur = GP.current_link_entry(os.path.join(cache_dir, "cache.ctm"), link[2], link[1])[1]
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits._composite_page_stale")
            return False
        want = links.strip_stamp(cur).lower()
        for _g, n in t[1]:
            if links.strip_stamp(n).lower() == want:
                return n.lower() != cur.lower()
        return False

    def _relink_composite(self, index: int, res, label: str) -> bool:
        from ..format import genie_place as GP
        from ..format import links

        objs = self.parsed.page.objects
        link = self.composite_link(index)
        idx = self.subtree(index)
        if link is None or idx != list(range(idx[0], idx[0] + len(idx))):
            raise ValueError("The composite genie's objects are not stored together.")
        inst = [objs[k] for k in idx]
        chain = self._ancestor_chain(index) or None
        new, entries = self._composite_records(res, inst=inst, lib=link[2], parent_chain=chain)
        pb = self.parsed.page
        order = list(objs)
        order[idx[0] : idx[-1] + 1] = new
        t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
        area = t[0] if t else getattr(pb, "area_code", "    ") or "    "
        kept = GP.used_link_entries(order, t[1] if t else [])
        # the element's CURRENT entry (its stamp) replaces a stale one
        fresh = {links.strip_stamp(n).lower() for _g, n in entries}
        kept = [(g, n) for g, n in kept if links.strip_stamp(n).lower() not in fresh]
        probe = type("_LT", (), {})()
        probe.link_table_raw, probe.link_count = links.serialize_link_table(area, kept)
        probe.area_code = area
        GP.add_link_entries(probe, entries)
        if [writer.object_payload(p) for p in new] == [
            writer.object_payload(p) for p in inst
        ] and probe.link_table_raw == pb.link_table_raw:
            return False
        self._refit_in_order(order, set(self._fitting_ancestors([index])))
        self._note_composite_links(inst + list(new))
        new_state = (order, probe.link_table_raw, probe.link_count)
        old_state = (list(objs), pb.link_table_raw, pb.link_count)

        def apply(state):
            o, raw, cnt = state
            pb.objects[:] = o
            pb.object_count = len(o)
            pb.link_table_raw, pb.link_count = raw, cnt
            self._rerender_page()

        self._apply(label, lambda: apply(new_state), lambda: apply(old_state))
        return True

    def composite_roots(self) -> List[int]:
        """The placed composite genies of the page (outermost only)."""
        out = []
        k = 0
        objs = self.parsed.page.objects
        while k < len(objs):
            if self.composite_link(k) is not None:
                out.append(k)
                k += max(1, len(self.subtree(k)))
            else:
                k += 1
        return out

    def update_composites(self, ctx=None, force: bool = False) -> int:
        """Update every stale composite genie on the page."""
        if ctx is not None:
            self._cctx_shared = ctx
            try:
                return self.update_composites(force=force)
            finally:
                self._cctx_shared = None
        self.composite_update_skipped = 0
        n = 0
        sem = self.page.objects if self.page is not None else []
        objs = self.parsed.page.objects

        def in_genie(k: int) -> bool:
            par = sem[k].parent if k < len(sem) else None
            while par is not None:
                if objs[par].subtype_size & 0x200:
                    return True
                par = sem[par].parent
            return False

        for k in reversed(self.composite_roots()):  # later indices first: stable
            if k < len(sem) and sem[k].raw.get("_from_template"):
                continue  # the page template's copy: update_template's
            if in_genie(k):
                continue  # composed with the genie that holds it (its element)
            state = self.composite_status(k)
            if state == "current" and not force:
                continue
            if state == "unknown":
                self.composite_update_skipped += 1
                continue
            try:
                if self.update_composite(k, force=True):
                    n += 1
            except ValueError:
                self.composite_update_skipped += 1
        return n

    def paste_composite_copy(
        self, guid: str, pos: Tuple[int, int], params: Optional[dict] = None
    ) -> Optional[int]:
        """Place a copy of a DONOR instance (same template guid) from the
        project, with the donor's options (for pages whose template ctgedit
        cannot compose). ``params`` may only repeat the
        donor's values (ValueError(COMPOSITE_LOCKED))."""
        from ..format import composites

        if not self.composites_allowed:
            raise ValueError(COMPOSITE_NOT_HERE)
        donor = composites.donor_index(self.path).get(guid.lower())
        if donor is None:
            return None
        donor_path, donor_idx = donor
        dp = parse_ctg(donor_path)
        objs = dp.page.objects
        # DFS subtree length on the flat record list
        end = donor_idx
        pending = 1
        while pending and end < len(objs):
            pending -= 1
            pending += objs[end].class_flag
            end += 1
        sub = objs[donor_idx:end]
        if not sub:
            return None
        root_gh = geom.parse_geom_header(sub[0].body_raw)
        if root_gh is None:
            return None
        dx = int(pos[0]) - root_gh.rect1[0]
        dy = int(pos[1]) - root_gh.rect1[1]
        copies: List[PageObject] = []
        for spo in sub:
            blob = writer.object_payload(spo)
            copies.append(parse_object(0, 0, len(blob), 0, blob))
        if params:
            have = {}
            for cp in copies:
                if cp.type_code not in self._BEHAVIOUR_TCS:
                    for k, v in writer.genie_bindings(cp).items():
                        have.setdefault(k, v)
            if any(have.get(k, "") != str(v) for k, v in params.items()):
                raise ValueError(COMPOSITE_LOCKED)
        self._translate_detached(copies, dx, dy)
        old = [geom.get_handle(p) for p in copies]
        self._renumber(copies)
        copies = self._fix_copies(copies, old, os.path.splitext(os.path.basename(donor_path))[0])
        # the donor page's entry for the composition cache (it resolves from
        # no library path) + the labels, like any paste
        from ..format import genie_place as GP
        from ..format import links

        t = links.parse_link_table(dp.page.link_table_raw) if dp.page.link_count else None
        try:
            carried = GP.used_link_entries(copies, list(t[1]) if t else [], keep_super=False)
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits.paste_composite_copy")
            carried = []
        return self._append_placed(copies, self._copy_link_entries(copies, carried), "paste composite genie")

    # -- attached super genies (the genie TEMPLATE's link/import table) ------
    def _genie_template_target(self, index: int):
        """The placed genie's template ``(ctm_path, element_name)`` or None."""
        from ..format.symbols import SymbolResolver, reference_from_object

        if index >= len(self.parsed.page.objects):
            return None
        po = self.parsed.page.objects[index]
        try:
            ref = reference_from_object(po)
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits._genie_template_target")
            return None
        if ref is None or ref.ext != "ctm" or "@super" in ref.element.lower():
            return None  # CtDraw attaches in GENIE windows only
        res = SymbolResolver.for_page(self.path).resolve(ref)
        if res is None:
            return None
        return res.library_path, ref.element

    def genie_attachments(self, index: int) -> Optional[List[str]]:
        """Edit > Attach Super Genies...: the placed genie's attached super genies, read
        from its template element's link/import table in the ``.ctm``. None when not a
        template-backed genie."""
        from ..format import links
        from ..format.symbols import SymbolLibrary

        target = self._genie_template_target(index)
        if target is None:
            return None
        lib_path, element = target
        pb = SymbolLibrary.open(lib_path).element(element)
        if pb is None:
            return None
        parsed = links.parse_link_table(pb.link_table_raw)
        if parsed is None:
            return []
        return links.super_genie_attachments(parsed[1])

    def set_genie_attachments(self, index: int, names: List[str], page_thumbnail=None) -> bool:
        """Rewrite the super genies attached to a placed genie's element."""
        self.super_pages = []
        target = self._genie_template_target(index)
        if target is None:
            return False
        lib_path, element = target
        el = _ed().ElementDocument.open_element(lib_path, element)
        if not el.set_attachments(names, resolver=self._resolver()):
            return False
        el.save(page_thumbnail=page_thumbnail)
        self.save_warning = el.save_warning
        self.super_pages = list(getattr(el, "super_pages", None) or [])
        return True

    def super_genie_problem(self, name: str) -> Optional[str]:
        """Why ``name`` ('library.element', with or without '@SUPER') cannot be
        attached, None when it can: it must name a super genie element that
        is on this document's include path."""
        from ..format import supergenie as SG

        text = (name or "").strip()
        lib, dot, elem = text.partition(".")
        if not dot or not lib or not elem:
            return (
                "'%s' is not a super genie name: write library.name, for example "
                "controls.!SG1_OpenClose." % text
            )
        res = self._resolver()
        if res is None or res.resolve(SG.reference(lib, elem)) is None:
            return (
                "There is no super genie %s.%s on this project's include path (the "
                "library %s.ctm has no element %s)."
                % (lib, SG.page_name(elem) or elem, lib, SG.element_name(elem))
            )
        return None

    def _super_genie_spelling(self, name: str) -> str:
        """'library.name' with the name spelled as the super genie element
        spells it -- what CtDraw's Add picker stores (CtDraw writes the
        chosen element's own name); the page a placement creates is named
        after it. ``name`` itself when the element is not found."""
        from ..format import supergenie as SG

        lib, _, elem = (name or "").partition(".")
        want = SG.element_name(elem).lower()
        with contextlib.suppress(Exception):
            for L in self._resolver().libraries(lib, "ctm"):
                for n in L.element_names():
                    if n.lower() == want:
                        return "%s.%s" % (lib, n.split("@", 1)[0])
        return name

    def _super_resolve(self, resolver=None):
        """``resolve('lib.elem') -> (guid16, stamp) | None`` of a super genie
        element on the include path (links.set_super_genie_attachments)."""
        from ..format import supergenie as SG

        res = resolver or self._resolver()

        def resolve(name):
            lib, dot, elem = name.partition(".")
            if not dot or res is None:
                return None
            rs = res.resolve(SG.reference(lib, elem))
            if rs is None:
                return None
            ident = rs.page.page_identity
            return ident[4:20], struct.unpack_from("<I", ident, 0)[0]

        return resolve

    # -- super genie pages -------------------------------------------------------
    def attached_super_genies(self, library: str, element: str) -> List[Tuple[str, str]]:
        """(library, '<name>@SUPER') of the super genies genie ``library.element``
        attaches (its element's link table), [] when none / not a genie."""
        from ..format import genie_place as GP
        from ..format import links
        from ..format import supergenie as SG

        path = self.library_path(library, element, "ctm")
        if path is None:
            return []
        try:
            pb = GP.open_library(path).element(element)  # cached per file version
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits.attached_super_genies", lib=path, element=element)
            return []
        if pb is None or not pb.link_count:
            return []
        t = links.parse_link_table(pb.link_table_raw)
        return SG.attachment_targets(t[1]) if t else []

    def instantiate_super_genies(self, library: str, element: str, thumbnail=None) -> List[dict]:
        """Create the super genie pages a genie opens that the project doesn't have yet."""
        try:
            targets = self.attached_super_genies(library, element)
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits.instantiate_super_genies", library=library, element=element)
            targets = []
        return create_super_genie_pages(
            os.path.dirname(os.path.abspath(self.path)),
            targets,
            self._resolver(),
            thumbnail=thumbnail,
            placing=True,
        )

    def supergenie_link(self) -> Optional[Tuple[str, str]]:
        """(library, '<name>@SUPER') when this page was instantiated from a
        super genie (its locked root links the element), else None."""
        from ..format import supergenie as SG

        try:
            return SG.page_link(self.parsed.page.objects)
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits.supergenie_link")
            return None

    def supergenie_element(self) -> Optional[Tuple[str, str]]:
        """(library path, element) of the super genie this page was created
        from, on its include path -- what 'Edit Super Genie' opens."""
        link = self.supergenie_link()
        if link is None:
            return None
        path = self.library_path(link[0], link[1], "ctm")
        return (path, link[1]) if path else None

    def supergenie_status(self) -> Tuple[str, str]:
        """('none' | 'unresolved' | 'current' | 'stale', link text) of a super
        genie page against its element as it is now."""
        from ..format import supergenie as SG
        from ..format import genie_place as GP

        roots = SG.page_roots(self.parsed.page.objects)
        if not roots:
            return "none", ""
        link = GP.decode(self.parsed.page.objects[roots[0][0]]).link or roots[0][2].raw
        sg = SG.resolve(self._resolver(), roots[0][2].library, roots[0][2].element)
        if sg is None:
            return "unresolved", link
        return ("stale" if SG.is_stale(self.parsed.page, sg, link) else "current"), link

    def update_supergenie_page(self, force: bool = False) -> int:
        """Update a super genie page from its element."""
        from ..format import supergenie as SG

        res = self._resolver()
        if res is None:
            return 0
        if not force and self.supergenie_status()[0] != "stale":
            return 0
        self._use_tag_formats()
        pb = self.parsed.page
        r = SG.recompose(pb.objects, res, page_name=self.page.name if self.page else "")
        if r["objects"] is None:
            return 0
        old = [writer.object_payload(p) for p in pb.objects]
        new = [writer.object_payload(p) for p in r["objects"]]
        raw, cnt = SG.replace_link_entry(
            pb.link_table_raw, pb.link_count, getattr(pb, "area_code", "    "), r["link"], r["entry"]
        )
        changed = sum(1 for a, b in zip(old, new) if a != b) + abs(len(old) - len(new))
        props, assoc = self._supergenie_lists(r["template"])
        changed += (props is not None) + (assoc is not None)
        if not changed and (raw, cnt) == (pb.link_table_raw, pb.link_count):
            return 0
        self._apply_page_state(
            "update super genie page", r["objects"], raw, cnt, props=props, associations=assoc
        )
        return max(changed, 1)

    def _supergenie_lists(self, sg):
        """(new list 1 | None, new list 2 | None) this super genie page takes
        from element ``sg`` (format/supergenie.sync_lists)."""
        from ..format import supergenie as SG

        at = self.parsed.attributes
        if at is None or sg is None:
            return None, None
        return SG.sync_lists(at.list1.pairs, at.list2.pairs, sg)

    def sync_supergenie_lists(self) -> bool:
        """Sync a super genie page's environment and associations with its element, as CtDraw's load
        does."""
        from ..format import supergenie as SG

        link = self.supergenie_link()
        if link is None:
            return False
        sg = SG.resolve(self._resolver(), link[0], link[1])
        props, assoc = self._supergenie_lists(sg)
        if props is None and assoc is None:
            return False
        pb = self.parsed.page
        self._apply_page_state(
            "associations from the super genie",
            list(pb.objects),
            pb.link_table_raw,
            pb.link_count,
            props=props,
            associations=assoc,
        )
        return True

    def associations_in_use(self) -> "Dict[str, str]":
        """{lower name: name} of the ?associations? the page's objects and page
        keyboard commands use -- Page Properties > Associations 'In Use'
        (format/supergenie.used_associations)."""
        from ..format import supergenie as SG

        objs = list(self.parsed.page.objects) if self.parsed is not None else []
        root = getattr(getattr(self, "session", None), "root", None)
        if root is not None:
            objs.insert(0, root)
        extra = []
        try:
            for row in self.page_keyboard_commands():
                extra += [row.get(k) or "" for k in ("KEY_SEQ", "CMD", "MSG_LOG")]
        except Exception:  # noqa: BLE001
            _note_exc("library.LibraryEdits.associations_in_use")
        with contextlib.suppress(Exception):
            # a library element's page keyboard commands: its trailer record C
            for bag in getattr(self.session.pe.trailer, "keyboards", None) or ():
                extra += [v for k, v in bag if k.upper() in ("KEY_SEQ", "CMD", "MSG_LOG")]
        return SG.used_associations(objs, extra)

    def super_genie_associations(self, index: int) -> List[Tuple[str, List[str]]]:
        """[(super genie page, association names)] for the genie holding ``index``."""
        from ..format import supergenie as SG
        from ..format import template_place as TP
        from ..format.symbols import reference_from_object, split_lib_element

        targets: List[Tuple[str, str]] = []
        if isinstance(self, _ed().ElementDocument):
            with contextlib.suppress(Exception):
                if self.can_attach_super_genies:
                    for name in self.attachments():
                        # (the LAST dot: a library file named 'plant.v2')
                        lib, elem = split_lib_element(name)
                        targets.append((lib, SG.element_name(elem)))
        else:
            objs = self.parsed.page.objects
            chain = [index] + (self.page.ancestors(index) if self.page is not None else [])
            for i in reversed(chain):
                if not 0 <= i < len(objs):
                    continue
                try:
                    ref = reference_from_object(objs[i])
                except Exception:  # noqa: BLE001
                    _note_exc("library.LibraryEdits.super_genie_associations", index=i)
                    ref = None
                if ref is not None and ref.ext == "ctm" and not SG.is_super_element(ref.element):
                    targets = self.attached_super_genies(ref.library, ref.element)
                    if targets:
                        break
        out = []
        res = self._resolver()
        for lib, sgel in targets:
            names: List[str] = []
            with contextlib.suppress(Exception):
                sg = SG.resolve(res, lib, sgel)
                if sg is not None:
                    names = [k for k, _v in TP.template_associations(sg)]
                    low = {n.lower() for n in names}
                    names += [n for k, n in SG.used_associations(sg.records).items() if k not in low]
            out.append((SG.page_name(sgel), names))
        return out

    def convert_to_bitmap(self, indices: Sequence[int], rect, rgba: bytes, width: int, height: int) -> int:
        """Replace the selected units with one embedded bitmap in a single
        undo step (the bitmap takes the lowest unit's z-position). Returns
        the new record's index."""
        objs = self.parsed.page.objects
        roots = sorted({self.root_of(i) for i in indices})
        drop = set()
        for r in roots:
            drop.update(self.subtree(r))
        at = min(drop)
        po = factory.make_bitmap_record(self.next_handle(), rect, rgba, width, height)
        order = (
            [o for i, o in enumerate(objs[:at]) if i not in drop]
            + [po]
            + [o for i, o in enumerate(objs) if i >= at and i not in drop]
        )
        self._structural("convert to bitmap", order)
        return order.index(po)

    #: replace_bitmap's default for ``flash_rgba``: keep the stored flash picture
    KEEP_FLASH = object()

    def bitmap_pictures(self, index: int):
        """``(primary, flash_or_None)`` pictures (EmbeddedBitmap) of an
        embedded bitmap record's OWN DIBs -- not a picture the page borrows
        from a library element or template, not a metafile's rendering;
        ``(None, None)`` for anything else."""
        from ..format import bitmaps

        objs = self.parsed.page.objects
        if not 0 <= index < len(objs):
            return None, None
        return bitmaps.read_pictures(objs[index])

    def replace_bitmap(
        self, index: int, rect, rgba: bytes, width: int, height: int, flash_rgba=KEEP_FLASH
    ) -> bool:
        """Put a new picture into an embedded bitmap in place."""
        from ..format import bitmaps, colourstate

        objs = self.parsed.page.objects
        if not 0 <= index < len(objs) or bitmaps.dib_section(objs[index]) is None:
            return False
        if flash_rgba is self.KEEP_FLASH:
            _p, old_f = bitmaps.read_pictures(objs[index])
            flash_rgba = (
                None
                if old_f is None
                else bitmaps.resample(old_f.rgba, old_f.width, old_f.height, width, height)
            )

        def mutate(po):
            if not bitmaps.set_pictures(po, rgba, width, height, flash_rgba, rect=rect):
                return False
            colourstate.derive_dynamic_flags(self.parsed.page.objects, index)
            return True

        return bool(self._body_edit("edit bitmap", index, mutate))

    def replace_metafile(self, index: int, rect, wmf: bytes) -> bool:
        """Replace Picture on a MetaFile object: its drawing becomes ``wmf``
        (bitmaps.wmf_from_file) shown in ``rect``; the record's bags, name, AN
        and z-order stay. One undo step. False when the record holds no
        metafile."""
        from ..format import bitmaps

        objs = self.parsed.page.objects
        if not 0 <= index < len(objs) or bitmaps.metafile_bits(objs[index]) is None:
            return False
        return bool(
            self._body_edit("replace picture", index, lambda po: bitmaps.set_metafile(po, wmf, rect=rect))
        )

    def picture_copies(self, index: int) -> List[int]:
        """The other embedded bitmaps of the page with the same picture as
        ``index`` (bitmaps.picture_digest), in record order."""
        from ..format import bitmaps

        objs = self.parsed.page.objects
        if not 0 <= index < len(objs):
            return []
        d = bitmaps.picture_digest(objs[index])
        if d is None:
            return []
        return [
            i
            for i, po in enumerate(objs)
            if i != index
            and po.type_code == bitmaps.BITMAP_TC
            and bitmaps.dib_section(po) is not None
            and bitmaps.picture_digest(po) == d
        ]

    def recolour_bitmap(self, index: int, fn: Callable[[int], int]) -> bool:
        """Map an embedded bitmap's opaque pixels (both flash states) through
        ``fn`` (Swap / Adjust Colors). One undo step (inside a batch: part of it)."""
        from ..format import bitmaps, colourstate

        objs = self.parsed.page.objects
        if not 0 <= index < len(objs) or bitmaps.dib_section(objs[index]) is None:
            return False

        def mutate(po):
            if not bitmaps.recolour(po, fn):
                return False
            colourstate.derive_dynamic_flags(self.parsed.page.objects, index)
            return True

        return bool(self._body_edit("recolour bitmap", index, mutate))

    def add_metafile_object(self, rect, wmf: bytes) -> int:
        """File > Import of a Windows Metafile: a MetaFile record (tc 0x09)
        playing ``wmf`` (:func:`format.bitmaps.wmf_from_file`) into ``rect``."""
        po = factory.make_metafile_record(self.next_handle(), rect, wmf)
        return self.add_object(po)

    def add_bitmap_object(
        self, rect, rgba: bytes, width: int, height: int, flash_rgba: Optional[bytes] = None
    ) -> int:
        """Create an embedded-bitmap object from RGBA pixels (File > Import / Paste
        Special DIB / Convert to Bitmap). ``flash_rgba`` (same size) makes a two-DIB
        FLASHING bitmap (Import As Flashing)."""
        po = factory.make_bitmap_record(self.next_handle(), rect, rgba, width, height, flash_rgba=flash_rgba)
        return self.add_object(po)


def _ed():
    """ctgedit.editing (EditableDocument / ElementDocument are defined there, after this
    module is imported)."""
    from .. import editing

    return editing
