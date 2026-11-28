"""Lets a library element be edited as if it were a page."""

from __future__ import annotations

import copy
import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .container import Block, Container
from .page import PAGE_HEAD_SIZE, PageBlock, PageObject, ParsedCtg, parse_ctg_bytes, parse_object
from . import writer, placement
from . import geometry as G

from .element import (
    ParsedElement,
    element_content,
    open_element,
    keeps_bare_root,
    root_group,
    to_member_form,
    wrapped_tree,
)
from ..infra.logsetup import note_exception as _note_exc

NO_COLOUR = 0xFF000000
GROUP_TC = 0x08
CANVAS_MARGIN = 40


def _clone(po: PageObject) -> PageObject:
    blob = writer.object_payload(po)
    return parse_object(0, 0, len(blob), 0, blob)


def forest_roots(objs: List[PageObject]) -> List[int]:
    out, i, n = [], 0, len(objs)

    def end(k):
        j = k + 1
        for _ in range(max(0, objs[k].class_flag)):
            if j >= n:
                break
            j = end(j)
        return j

    while i < n:
        out.append(i)
        i = end(i)
    return out


def union_rects(objs: List[PageObject], idx: List[int]):
    """(union rect1 normalised, union rect2) over records ``idx`` -- the
    container Extent / Bounds rule (input records' icon rect2 left out,
    EditableDocument._union_rects)."""
    r1s, r2s = [], []
    for i in idx:
        if objs[i].type_code in G.INPUT_TYPE_CODES:
            continue
        gh = G.parse_geom_header(objs[i].body_raw)
        if gh is None:
            continue
        r1s.append(G.norm_rect(gh.rect1))
        r2s.append(tuple(gh.rect2))
    if not r1s:
        return None, None

    def u(rs):
        return (min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs))

    return u(r1s), u(r2s)


@dataclass
class ElementSession:
    pe: ParsedElement
    root: Optional[PageObject]  # hidden root group (None: single-object root)
    session_payloads: List[bytes]  # for change detection
    canvas: Tuple[int, int]
    attr_state: tuple = ()  # (list1, list2, bg0, bg1, w, h) as opened
    wrapper: bytes = b""

    @property
    def origin(self) -> Tuple[int, int]:
        return self.pe.origin


def attributes_block(identity: bytes, w: int, h: int, bg: Tuple[int, int], list1, list2) -> bytes:
    """A valid .ctg 'attributes' block (attributes.py layout) -- a page
    with one record: [u32 0][bg0][bg1][u32 type][u16 vw][u16 vh][u32 0]
    [list1 bag][list2 bag]."""
    from .element import bag_bytes

    l1, l2 = bag_bytes(list1), bag_bytes(list2)
    data_end = 0x54 + len(l1) + len(l2)
    head = bytearray(0x54)
    struct.pack_into("<II", head, 0x00, data_end - 0x34, 1)
    struct.pack_into("<II", head, 0x14, w, h)
    head[0x20:0x34] = identity
    struct.pack_into("<I", head, 0x34, data_end - 0x3C)
    struct.pack_into("<II", head, 0x40, bg[0] & 0xFFFFFFFF, bg[1] & 0xFFFFFFFF)
    return bytes(head) + l1 + l2 + b"\0" * 0x200


def page_sized(pe: ParsedElement) -> bool:
    """Templates (.ctt) and super genies (<name>@SUPER) are PAGES: their head
    W,H is the page / popup-window size (super genies: e = 0,0 and W,H <
    root bounds), edited through Page Properties. Symbols / genies:
    W,H = the root's Bounds size."""
    return pe.ext == "ctt" or pe.kind == "supergenie"


def canvas_size(pe: ParsedElement, session: List[PageObject]) -> Tuple[int, int]:
    w, h = pe.extent
    if page_sized(pe):
        return max(1, w), max(1, h)
    ex, ey = pe.origin
    right, bottom = ex + w, ey + h
    u1, u2 = union_rects(session, forest_roots(session)) if session else (None, None)
    if u2:
        right, bottom = max(right, u2[2]), max(bottom, u2[3])
    return (
        max(320, -(-(right + CANVAS_MARGIN) // 10) * 10),
        max(240, -(-(bottom + CANVAS_MARGIN) // 10) * 10),
    )


def open_element_session(lib_path: str, element: str, library=None) -> Tuple[ElementSession, ParsedCtg]:
    """The editing session of an element (the module doc). ``library``: the library file
    as a LibraryFile in memory (format/element.open_element), not read from disk."""
    pe = open_element(lib_path, element, library=library)
    tree = pe.tree
    if not page_sized(pe):
        # a genie element holding instances in their PAGE form (copied from a page) is
        # shown and edited in the element form CtDraw composes for it, its values derived
        # again: what its save writes (genie_place.convert_page_form; an unchanged
        # element is not written)
        from .genie_place import convert_page_form
        from . import genie_norm as GN

        converted = convert_page_form(tree)
        if any(a is not b for a, b in zip(converted, tree)):
            try:
                tree = GN.normalise_records(converted, ext=pe.ext, convert=True)
            except Exception:  # noqa: BLE001 -- the display only; Save derives again
                _note_exc("element_session.open_element_session")
                tree = converted
    root = None
    # the session records are only measured and serialised into the wrapper
    # page below (whose parse the document edits), never kept or changed: the
    # element's own parsed records serve (a clone = the same payload parsed again)
    if tree and tree[0].type_code == GROUP_TC and tree[0].class_flag > 0 and not (tree[0].subtype_size & 0x1):
        root = tree[0]
        session = list(tree[1:])
    else:
        session = list(tree)
    cw, ch = canvas_size(pe, session)
    tr = pe.trailer
    bgpair = tr.background() or (NO_COLOUR, NO_COLOUR)
    list1 = tr.page_properties()
    list2 = list(tr.associations or [])
    # the session page block: element head (identity, links kept), session
    # records, canvas extent, no thumbnail (render only)
    pb = copy.copy(pe.page)
    pb.objects = session
    pb.page_width, pb.page_height = cw, ch
    pb.thumbnail = b""
    page_data = writer.serialize_page_block(pb, pe.block.data)
    attr_data = attributes_block(pe.page.page_identity, cw, ch, bgpair, list1, list2)
    lib = pe.library
    cont = Container(
        lib.version,
        lib.word2,
        lib.ts_created,
        lib.ts_modified,
        8,
        [Block(0x0001, "page", 0, 0, page_data), Block(0x0101, "attributes", 0, 0, attr_data)],
    )
    cont.relayout()
    wrapper = cont.to_bytes()
    parsed = parse_ctg_bytes(wrapper)
    sess = ElementSession(
        pe,
        root,
        [writer.object_payload(o) for o in parsed.page.objects],
        (cw, ch),
        (tuple(list1), tuple(list2), bgpair[0], bgpair[1], cw, ch),
        wrapper,
    )
    return sess, parsed


def _attr_now(parsed: ParsedCtg):
    at = parsed.attributes
    return (
        tuple(at.list1.pairs),
        tuple(at.list2.pairs),
        at.bg_color0,
        at.bg_color1,
        parsed.page.page_width,
        parsed.page.page_height,
    )


def set_root_origin(tree: List[PageObject], e: Tuple[int, int]) -> None:
    """Give an element's root the slots of origin ``e`` (the hot spot)."""
    root = tree[0]
    if placement.is_linked_symbol(root):
        # a root that is itself a placed symbol of another library (Include
        # ICONS left7 / left72): its slots 2 / 3 derive from THAT element's
        # origin (the saver); the head's e alone moves
        return
    ex, ey = int(e[0]), int(e[1])
    (m1, L, _o, _n), _s2, _s3 = placement.read_slots(root.body_raw)[0]
    if root.type_code == GROUP_TC and root.class_flag:
        t_e = (1, (1.0, 0.0, 0.0, 1.0, float(-ex), float(-ey))) if (ex or ey) else (0, placement.IDENT)
        placement.write_slots(root, [(m1, L), t_e, (0, placement.IDENT)])
    else:
        s2, m2 = placement.slot2_for_linked(L, m1, (ex, ey)) if (ex or ey or m1) else (placement.IDENT, 0)
        placement.write_slots(root, [(m1, L), (m2, s2), (0, placement.IDENT)])
    writer.refresh_object_bags(root)


def _normalised_tree(
    sess: ElementSession,
    parsed: ParsedCtg,
    tree,
    resolver,
    edited_handles,
    instantiated,
    e_new,
    nested_only,
    keep_stamps,
) -> tuple:
    """The element's records in CtDraw's engine-normal form (genie_norm) and its rebuilt
    link table: (records, link entries, the stale nested genies, the nesting cycles)."""
    from . import genie_norm as GN
    from .genie_place import declares_parameters as _gp_declares

    pe = sess.pe

    def GP_declares(recs):
        return _gp_declares(list(recs), placement.forest_parents(list(recs)))

    if resolver is None:
        try:
            from ctgedit.format.symbols import SymbolResolver

            resolver = SymbolResolver.for_page(pe.lib_path)
        except Exception:  # noqa: BLE001
            _note_exc("element_session._normalised_tree", lib=pe.lib_path)
            resolver = None
    edited = {k for k, o in enumerate(tree) if edited_handles and G.get_handle(o) in edited_handles}
    convert = (not GN.is_genie_element(tree)) and not page_sized(pe) and GP_declares(tree)
    if pe.ext == "ctt" and not GN.is_genie_element(tree):
        # a template's first own parameter: a genie template now, every record of it
        # with its level (genie_norm.convert_template_records)
        convert = GN.template_declares(tree)
    # an import-table-only edit (Attach Super Genies) normalises too: CtDraw's
    # save writes the element's engine-normal records (it normalises on load)
    norm_report: dict = {}
    tree = GN.normalise_records(
        tree,
        ext=pe.ext,
        convert=convert,
        resolver=resolver,
        lib_path=pe.lib_path,
        edited=edited,
        e=e_new,
        page_like=page_sized(pe),
        nested_only=nested_only,
        stored_payloads=set(sess.session_payloads),
        report=norm_report,
    )
    from ctgedit.format import links as _links

    src_raw = parsed.page.link_table_raw if parsed.page.link_count else b""
    lt = _links.parse_link_table(src_raw) if src_raw else None
    # (a changed nested genie whose copy could not be composed again keeps its old
    # stamp: the element stays out of date for it)
    stale_nested = frozenset(norm_report.get("stale_nested", ()))
    # (a nested genie that holds a copy of itself: genie_place.NestingCycle)
    cycles = frozenset(norm_report.get("cycles", ()))
    link_entries = (
        lt[0] if lt else "    ",
        GN.rebuild_link_table(
            tree,
            lt[1] if lt else [],
            resolver,
            instantiated=instantiated,
            keep_stale=stale_nested | frozenset(keep_stamps or ()),
            page_like=page_sized(pe),
        ),
    )
    return tree, link_entries, stale_nested, cycles


def _session_tree(
    sess: ElementSession, objs: List[PageObject], changed: bool, moved: bool, refresh: bool
) -> List[PageObject]:
    """The element's records from the session's ``objs``: the stored ones when nothing
    changed, else under one root."""
    pe = sess.pe
    roots = forest_roots(objs)
    symbol = pe.ext == "ctl"
    if not changed and not moved and not refresh:
        tree = pe.tree
    elif not changed:
        tree = [_clone(o) for o in pe.tree]
        if symbol:
            # (CtDraw's load + save of a one-object symbol it does not keep bare: the
            # group root, keeps_bare_root)
            tree = wrapped_tree(tree, pe.origin)
    elif sess.root is not None:
        root = _clone(sess.root)
        root.class_flag = len(roots)
        _refit(root, objs, roots)
        tree = [root] + objs
    elif len(roots) == 1 and not (symbol and not keeps_bare_root(objs[0])):
        tree = objs
        if G.get_handle(objs[0]) != G.get_handle(pe.tree[0]):
            # a new single root (the old one grouped, or replaced): the root's slots
            # (I, T(-e), I) / Local o T(-e), the old root a member
            _demote_former_root(pe, objs)
            set_root_origin(tree, pe.origin)
    else:
        tree = [_new_root(pe, objs, roots)] + objs
    return tree


def session_to_content(
    sess: ElementSession,
    parsed: ParsedCtg,
    thumbnail: Optional[bytes] = None,
    *,
    normalise: bool = True,
    edited_handles: Optional[set] = None,
    resolver=None,
    instantiated=(),
    origin: Optional[Tuple[int, int]] = None,
    refresh: bool = False,
    memo: Optional[dict] = None,
    nested_only=None,
    keep_stamps=(),
    report: Optional[dict] = None,
) -> bytes:
    """Fold an edited session back into element content."""
    pe = sess.pe
    payloads = [writer.object_payload(o) for o in parsed.page.objects]
    key = content_key(
        payloads,
        parsed,
        normalise=normalise,
        edited_handles=edited_handles,
        resolver=resolver,
        instantiated=instantiated,
        origin=origin,
        refresh=refresh,
        nested_only=nested_only,
        keep_stamps=keep_stamps,
    )
    if memo is not None and memo.get("key") == key:
        tree, link_entries, changed, links_edited, moved, e_new = memo["parts"]
        if report is not None:
            report["stale_nested"] = memo.get("stale_nested", frozenset())
            if memo.get("cycles"):
                report["cycles"] = memo["cycles"]
        return _assemble(
            sess, parsed, pe, tree, link_entries, changed, links_edited, moved, e_new, thumbnail, refresh
        )
    objs = [_clone(o) for o in parsed.page.objects]
    changed = payloads != sess.session_payloads
    if not objs:
        raise ValueError("a library element needs at least one object")
    e_new = tuple(int(v) for v in origin) if origin is not None else tuple(pe.origin)
    moved = e_new != tuple(pe.origin)
    tree = _session_tree(sess, objs, changed, moved, refresh)
    if moved:
        set_root_origin(tree, e_new)
        changed = True
    # LIVE: CtDraw re-derives the element on load/save (genie_norm) -- write
    # that engine-normal form so the next CtDraw save is a byte fixed point
    link_entries = None
    stale_nested = frozenset()
    cycles = frozenset()
    # Attach Super Genies edits only the import table: CtDraw's save still
    # rebuilds it (every entry with the referenced element's CURRENT stamp)
    links_edited = parsed.page.link_table_raw != pe.page.link_table_raw or bool(
        instantiated
    )  # a created page: its bind takes a guid
    if (changed or links_edited or refresh) and normalise:
        tree, link_entries, stale_nested, cycles = _normalised_tree(
            sess, parsed, tree, resolver, edited_handles, instantiated, e_new, nested_only, keep_stamps
        )
    if report is not None:
        report["stale_nested"] = stale_nested
        if cycles:
            report["cycles"] = cycles
    if memo is not None:
        memo["key"] = key
        memo["parts"] = (tree, link_entries, changed, links_edited, moved, e_new)
        memo["stale_nested"] = stale_nested
        memo["cycles"] = cycles
    return _assemble(
        sess, parsed, pe, tree, link_entries, changed, links_edited, moved, e_new, thumbnail, refresh
    )


def content_key(
    payloads,
    parsed: ParsedCtg,
    *,
    normalise: bool = True,
    edited_handles=None,
    resolver=None,
    instantiated=(),
    origin=None,
    refresh: bool = False,
    nested_only=None,
    keep_stamps=(),
) -> tuple:
    """The key of :func:`session_to_content`'s ``memo``: what its normalisation reads
    (``payloads`` = the session records' payloads). Its 8th item is the resolver's id: a
    key made in another process differs there only (:func:`key_digest`)."""
    return (
        tuple(payloads),
        parsed.page.link_table_raw,
        tuple(sorted(instantiated)),
        tuple(origin) if origin is not None else None,
        refresh,
        normalise,
        tuple(sorted(edited_handles or ())),
        id(resolver),
        None if nested_only is None else tuple(sorted(nested_only)),
        tuple(sorted(keep_stamps or ())),
    )


def key_digest(key: tuple) -> str:
    """A digest of a :func:`content_key` without its resolver id: equal in the editor and
    in a helper process that normalised the same records with the same choices (the save
    of a large element normalises in a helper, ui/dialog/element_save)."""
    import hashlib

    h = hashlib.sha1()
    for p in key[0]:
        h.update(hashlib.sha1(p).digest())
    h.update(repr(key[1:7] + key[8:]).encode("utf-8", "backslashreplace"))
    return h.hexdigest()


def memo_to_json(memo: dict) -> dict:
    """:func:`session_to_content`'s filled ``memo`` as JSON data (a helper process's answer,
    genie_worker.compute_save): the normalised records' payloads, the import table it
    rebuilt, its flags, and the digest of its key (:func:`key_digest`)."""
    tree, link_entries, changed, links_edited, moved, e_new = memo["parts"]
    links = None
    if link_entries is not None:
        area, entries = link_entries
        links = [area, [[bytes(g).hex(), n] for g, n in entries]]
    return {
        "digest": key_digest(memo["key"]),
        "tree": [writer.object_payload(o).hex() for o in tree],
        "links": links,
        "changed": bool(changed),
        "links_edited": bool(links_edited),
        "moved": bool(moved),
        "e_new": [int(v) for v in e_new],
        "stale_nested": [list(k) for k in memo.get("stale_nested") or ()],
        "cycles": [list(k) if isinstance(k, (tuple, list)) else k for k in memo.get("cycles") or ()],
    }


def memo_from_json(obj: dict, key: tuple) -> Optional[dict]:
    """The ``memo`` of :func:`session_to_content` from :func:`memo_to_json`'s data, under
    ``key`` (the editor's own :func:`content_key`): None when the data was made from other
    records or other choices (its digest is another one), so the editor normalises itself."""
    if not isinstance(obj, dict) or obj.get("digest") != key_digest(key):
        return None
    from .genie_place import to_po

    tree = [to_po(bytes.fromhex(x)) for x in obj["tree"]]
    links = obj.get("links")
    link_entries = None
    if links is not None:
        link_entries = (links[0], [(bytes.fromhex(g), n) for g, n in links[1]])
    return {
        "key": key,
        "parts": (
            tree,
            link_entries,
            bool(obj["changed"]),
            bool(obj["links_edited"]),
            bool(obj["moved"]),
            tuple(int(v) for v in obj["e_new"]),
        ),
        "stale_nested": frozenset(tuple(k) for k in obj.get("stale_nested") or ()),
        "cycles": frozenset(tuple(k) if isinstance(k, list) else k for k in obj.get("cycles") or ()),
    }


def _assemble(
    sess, parsed, pe, tree, link_entries, changed, links_edited, moved, e_new, thumbnail, refresh
) -> bytes:
    """The content from the normalised records (session_to_content's last part)."""
    # head extent: the template page size / the root's Bounds size (CtDraw
    # recomputes it on EVERY save)
    w, h = pe.extent
    if page_sized(pe):
        w, h = parsed.page.page_width, parsed.page.page_height
    elif changed or links_edited or refresh:
        gh = G.parse_geom_header(tree[0].body_raw)
        if gh is not None:
            w, h = gh.rect2[2] - gh.rect2[0], gh.rect2[3] - gh.rect2[1]
    # trailer from the attributes (unchanged -> the original bytes)
    tr = pe.trailer
    now = _attr_now(parsed)
    if now[:4] != sess.attr_state[:4]:
        tr = tr.with_changes(
            page_properties=list(now[0]), associations=list(now[1]), background=(now[2], now[3])
        )
    pb = copy.copy(pe.page)
    pb.page_width, pb.page_height = w, h
    if link_entries is not None:
        from . import genie_norm as GN

        pb.link_table_raw, pb.link_count = GN.link_table_bytes(*link_entries)
    elif parsed.page.link_table_raw != pe.page.link_table_raw:
        pb.link_table_raw = parsed.page.link_table_raw
        pb.link_count = parsed.page.link_count
    if thumbnail is not None:
        pb.thumbnail = thumbnail
    view = copy.copy(pe)
    view.page = pb
    if changed or links_edited or refresh:
        # a named record carries its name as '__AN_NAME__' too, as CtDraw's save writes it
        from . import genie_place as GP

        tree = GP.with_name_keys(tree)
    content = element_content(view, objects=tree, trailer=tr)
    if moved:
        # the head's origin (i32 0x0C / 0x10): element_content keeps the
        # stored head bytes, the new hot spot goes in here
        content = content[:0x0C] + struct.pack("<2i", *e_new) + content[0x14:]
    return content


def _refit(root: PageObject, objs: List[PageObject], roots: List[int]) -> None:
    u1, u2 = union_rects(objs, roots)
    if u1 is None:
        return
    gh = G.parse_geom_header(root.body_raw)
    body = bytearray(root.body_raw)
    struct.pack_into("<2h", body, gh.anchor_off, u1[0], u1[3] - 1)
    struct.pack_into("<4h", body, gh.rect1_off, *u1)
    struct.pack_into("<4h", body, gh.rect2_off, *u2)
    root.body_raw = bytes(body)


def _new_root(pe: ParsedElement, objs: List[PageObject], roots: List[int]) -> PageObject:
    """A .ctl whose single-object root gained siblings, or whose one root CtDraw does
    not keep bare (element.keeps_bare_root): wrap them in a group root with slots
    (I, T(-e), I) like every shipped root; the former root loses its T(-e) slot 2
    (session form: slot2 = Local, element.to_member_form)."""
    for i in roots:
        to_member_form(objs[i], pe.origin)
    _demote_former_root(pe, objs)
    # (after the members: a transformed marker's box is baked into its class part first,
    # element._bake_marker, and the root is the union of the boxes the save writes)
    return root_group(objs, roots, pe.origin)


def _demote_former_root(pe: ParsedElement, objs: List[PageObject]) -> None:
    """The element's stored single root, wherever the edit put it (grouped with the
    objects drawn beside it, say): a member, its T(-e) slot 2 dropped
    (element.to_member_form; a new record that took its handle has no T(-e) to drop)."""
    h = G.get_handle(pe.tree[0]) if pe.tree else None
    for po in objs:
        if G.get_handle(po) == h:
            to_member_form(po, pe.origin)
            return


def thumbnail_geometry(sess: ElementSession, parsed: ParsedCtg):
    """(scene rect, thumbnail size) for an element's thumbnail."""
    objs = parsed.page.objects
    if page_sized(sess.pe):
        x, y = 0, 0
        w, h = parsed.page.page_width, parsed.page.page_height
    else:
        u1, u2 = union_rects(objs, forest_roots(objs)) if objs else (None, None)
        if u2 is None:
            return None
        x, y, w, h = u2[0], u2[1], u2[2] - u2[0], u2[3] - u2[1]
    if w <= 0 or h <= 0:
        return None
    s = min(200.0 / w, 200.0 / h, 1.0)
    if s >= 1.0:
        tw, th = w, h
    elif w >= h:
        tw, th = 200, max(1, int(h * s))
    else:
        tw, th = max(1, int(w * s)), 200
    return (x, y, w, h), (tw, th)


def blank_session(
    canvas: Tuple[int, int], *, word2: int = 0x0C, page_properties=(), associations=(), background=None
) -> ParsedCtg:
    """The session page of a NEW (not yet saved) element: no records, the
    given canvas, attributes = the element's page properties / associations
    / background (the default trailer takes them on the first save)."""
    cw, ch = max(1, int(canvas[0])), max(1, int(canvas[1]))
    ident = bytes(20)
    pb = PageBlock(0, 0, 0, cw, ch, 0, ident, "    ", PAGE_HEAD_SIZE, PAGE_HEAD_SIZE, b"", [], b"")
    page_data = writer.serialize_page_block(pb, bytes(PAGE_HEAD_SIZE))
    bg = background or (NO_COLOUR, NO_COLOUR)
    attr_data = attributes_block(ident, cw, ch, bg, list(page_properties), list(associations))
    cont = Container(
        9,
        word2,
        0,
        0,
        8,
        [Block(0x0001, "page", 0, 0, page_data), Block(0x0101, "attributes", 0, 0, attr_data)],
    )
    cont.relayout()
    return parse_ctg_bytes(cont.to_bytes())
