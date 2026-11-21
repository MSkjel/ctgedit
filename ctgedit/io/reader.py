"""Turn a parsed .ctg file into the document model."""

from __future__ import annotations

import os
from ..format import geometry as _geom
from ..format.page import _extract_bmp, _parse_object, parse_ctg
from ..model.document import (
    GraphicObject,
    Geometry,
    ObjectKind,
    Page,
    PageAppearance,
    Style,
    Visibility,
)
import threading
from bisect import bisect_right
from collections import OrderedDict
from ..format import page as _pagemod
from ..format import legacy as _legacy
from ..format import placement as _pl
from ..format import textmetrics as _tm
from ..format.geometry import get_handle
from ..format.symbols import legacy_first_symbol as _legacy_first_symbol
from ..format.symbols import (
    SymbolReference,
    SymbolResolver,
    embedded_bitmap_from_body,
    reference_from_object,
    render_metafile,
)
from ..format.writer import genie_bindings, object_payload
from ..infra import gcguard as _gcguard
from .pgregister import ole_clsid
from ..format.olecontrol import storage as _ole_storage
from . import project as _project
from ..infra.logsetup import note_exception as _note_exc
from ..format import codes as _codes
from ..format import forest as _forest
from ..format import alarmind as _alarmind  # noqa: E402
from ..infra import caches as _caches  # noqa: E402
from .readerparts import common as _pc, style as _ps, symbols as _py, graft as _pg  # noqa: E402,F401
from .readerparts.common import (  # noqa: E402,F401  (re-exported)
    CATALOG_PATH,
    _CATALOG_KIND_TO_ENUM,
    _CLOSED_KINDS,
    _COMPOSITE_VAR,
    _ICON_FOR_TYPE,
    _LABEL_MODE_VAR,
    _LEAF_KINDS,
    _NON_DRAWABLE_TYPE_CODES,
    _NO_COLOUR,
    _OPEN_KINDS,
    _PAGE_CTX,
    _STYLED_TYPE_CODES,
    _apply_animations,
    _apply_symbol_set,
    _catalog,
    _class_icons,
    _key_family,
    _kind_for,
    _read_geometry,
    _transform_from_header,
    _typecode_kind,
    derive_options,
    get_composite_members,
    get_label_mode,
    get_render_option,
    get_value_placeholder,
    set_composite_members,
    set_label_mode,
    set_render_option,
    set_value_placeholder,
)
from .readerparts.style import (  # noqa: E402,F401  (re-exported)
    BUTTON_MODES,
    _DWF_GENIE,
    _DWF_METADATA,
    _DWF_STYLE_BLOCK,
    _KNOWN_FONTS,
    _apply_button_style,
    _apply_text_style,
    _clean_display_text,
    _decode_style,
    _design_colour,
    _find_panel_colours,
    _find_tail_colour,
    _is_colour_animated,
    _is_widget_container,
    _justify_from_props,
    _literal_display,
    _norm_colorref,
    _norm_nl,
    _pair_on,
    _pair_static,
    _re_expr,
    _style_block_exact,
    _text_font_size,
    _text_tail_colour,
)
from .readerparts.symbols import (  # noqa: E402,F401  (re-exported)
    _ART_TWINS_CACHE,
    _ART_TWINS_LOCK,
    _ART_TWINS_MAX_DEPTH,
    _ART_TWINS_TLS,
    _BITMAP_TYPE_CODE,
    _EXPANDABLE_EXTS,
    _GRAFT_TYPE_CODES,
    _MAX_SYMBOL_DEPTH,
    _MISSING,
    _NODE_TYPE_CODES,
    _PLACEABLE_KINDS,
    _SYMBOL_SET_TC,
    _TOKEN_RE,
    _art_twins_compute,
    _art_twins_entry,
    _art_twins_for,
    _art_twins_light,
    _attach_embedded_bitmap,
    _attach_metafile,
    _build_resolver,
    _element_root_records,
    _expand_element,
    _expand_symbol,
    _local_subtree,
    _member_has_art,
    _repair_stripped_nodes,
    _select_local_states,
    _select_set_state,
    _substitute_tokens,
    _template_alignment,
    _template_alignment_read,
    _template_alignment_safe,
    _template_alignment_span,
    _wire_local_forest,
)
from .readerparts.graft import (  # noqa: E402,F401  (re-exported)
    _GraftCtx,
    _content_bbox_min,
    _dib_pictures,
    _editor_display,
    _expand_nested_containers,
    _fill_button_faces,
    _graft_dib,
    _graft_stripped_text,
    _graft_template_member_colours,
    _graft_twin_extras,
    _is_target_btn,
    _map_slot_point,
    _member_nodes,
    _note_flash_slots,
    _own_text_caption,
    _scaled_text_size,
    _sibling_button_flags,
    _state0_pair,
    _suppress_redundant_hotspots,
    _text_needs_frame,
    _total_vscale,
    _twin_line_slot,
    _txtmode,
)


# --------------------------------------------------------------------------- main bridge
def _suppress_overlapping_duplicates(page: Page) -> None:
    """Collapse runtime-exclusive state stacks with identical art."""
    if _pc.get_render_option("stacked_labels") == "all":
        return
    groups: dict = {}
    for i, obj in enumerate(page.objects):
        if obj.raw.get("_non_drawable") or obj.children:
            continue
        k = obj.kind.value
        # only LABEL kinds: text/number/button state stacks overprint into a bold blur.
        # Lines/shapes/symbols are left alone: stacked dividers/borders are real detail.
        if k not in ("text", "number", "button") or not obj.style.text:
            continue
        # a label may OWN a non-drawable input child (Touch/Keyboard) and still draw only its own
        # caption -- include it; a label owning a drawable member is something else, skip it.
        if obj.members and any(
            not page.objects[m].raw.get("_non_drawable") and page.objects[m].style.text != obj.style.text
            for m in obj.members
            if m < len(page.objects)
        ):
            continue
        g = obj.geometry
        sig = (k, round(g.x), round(g.y), round(g.extent_x), round(g.extent_y), obj.style.text)
        groups.setdefault(sig, []).append(i)
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        # only a genuine state stack: every copy gated or template chrome
        if not all(
            page.objects[i].animations.visibility is not None or page.objects[i].raw.get("_from_template")
            for i in idxs
        ):
            continue
        for i in idxs[:-1]:  # keep the topmost (highest index)
            page.objects[i].raw["_dup_suppressed"] = True


def restack_labels(page: Page, moved) -> list:
    """Recompute stacked-label flags after objects moved."""
    objs = page.objects

    def stackable(i):
        o = objs[i] if 0 <= i < len(objs) else None
        st = getattr(o, "style", None)
        return (
            st is not None
            and st.text
            and o.kind.value in ("text", "number", "button")
            and (o.animations.visibility is not None or o.raw.get("_from_template"))
        )

    if not any(stackable(i) for i in moved):
        return []
    before = [bool(o.raw.pop("_dup_suppressed", None)) for o in objs]
    _suppress_overlapping_duplicates(page)
    return [i for i, o in enumerate(objs) if bool(o.raw.get("_dup_suppressed")) != before[i]]


#: (folder, id of io.project's lookup) -> the lookup the reader and the editor measure
#: with. io.project.tag_format_map keys its lookups by the tables' stamps; this is the
#: session's copy of them that View > Refresh (editing.forget_derived) clears.
_TAG_FORMAT_CACHE: dict = {}

_TAG_FORMAT_LOCK = threading.Lock()


def _tag_formats_for(path: str, fm=None):
    """``name -> FORMAT`` the page measures with: the tags of its project and the projects
    it includes (io.project.tag_format_map; not the projects compiled with it).
    ``fm`` = that map when the caller has it already."""
    if fm is None:
        fm = _tag_format_map(path)
    if fm is None:
        return None
    return lambda name: fm.get(name.strip().lower())


def _tag_format_map(path: str):
    """The shared lookup behind :func:`_tag_formats_for`: the same object while the
    scope's variable tables are unchanged (the derive context compares its identity).
    None when no project of the scope has a variable.dbf. The editor measures with
    the same object (EditableDocument.tag_format)."""
    try:
        pdir = os.path.dirname(os.path.abspath(path))
        fm = _project.tag_format_map(pdir)
    except Exception:  # noqa: BLE001
        _note_exc("reader._tag_format_map")
        return None
    if fm is None:
        return None
    key = (os.path.normcase(pdir), id(fm))
    hit = _TAG_FORMAT_CACHE.get(key)
    if hit is None:
        with _TAG_FORMAT_LOCK:
            if len(_TAG_FORMAT_CACHE) > 64:
                _TAG_FORMAT_CACHE.clear()
            # (the entry holds fm, so its id is not reused while it is cached)
            hit = _TAG_FORMAT_CACHE.setdefault(key, fm)
    return hit


def tag_format_lookup(path: str):
    """name (lower case) -> FORMAT of the page at ``path``'s tag scope: ``.get``,
    ``in``, iteration over the names. None when the scope has no variable.dbf."""
    return _tag_format_map(path)


def read_page(path: str) -> Page:
    """The semantic page of the ``.ctg`` file at ``path``."""
    return derive_page(parse_ctg(path), path, records_fresh=True)[0]


# ---------------------------------------------------------------------------------------------
# INCREMENTAL derivation (the editor re-derives the page after every edit). An edit changes a few
# records; with derive_page(previous=...) a ROOT UNIT (a top-level record and its member subtree) whose records are the same objects with the same bytes as at the
# previous derive keeps its derived objects; only the changed units are parsed and derived again.
# What a unit's derivation reads OUTSIDE its own records is checked, and a change there derives
# every unit again:
#   - the page TEMPLATE alignment (the template-prefix flags and grafts, _template_alignment),
#   - the root sibling group's Touch / TARGET-button flags (_fill_button_faces),
#   - the context: label mode, tag formats, language tables, render switches, the resolver.
# The page-wide passes (duplicate / value-state suppression, alarm indicators) are cheap and run
# over every object each time. EditableDocument keeps the PageDerivation; CTG_DERIVE_CHECK=1
# compares every incremental result with a full derive (model_difference).

# the environment switches a derive reads: a change derives every unit again
_DERIVE_ENV = ("CTG_NO_SYMBOLS", "CTG_RAW_LABELS", "CTG_NO_EMBEDDED", "CTG_LIB_PATH", "CTG_CODEPAGE")


class PageDerivation:
    """One derive of a page's records -- what :func:`derive_page` derives the next one from."""

    def __init__(self) -> None:
        self.page = None  # the Page it produced
        self.parsed = None  # the ParsedCtg it was derived from
        self.records: list = []  # the records at that time (the caller's PageObjects)
        self.sigs: list = []  # per record: _record_sig at that time
        self.shadow: list = []  # per record: the parsed PageObject the reader read
        self.spans: dict = {}  # root index -> length of its subtree
        self.context = None
        self.resolver = None
        self.tpl_map: dict = {}  # the template alignment, the leading records it read
        self.tpl_read = None
        self.tpl_units: dict = {}  # root start -> its records' alignment ((k - start, id(tpl)))
        self.root_flags = None
        # relative to the previous derive: every object derived afresh / the first root from
        # which the root layout (each root's index + subtree length) differs (len = none) / the
        # layout unchanged / the roots whose objects changed -- every root from ``tail`` on
        # included (a scene rebuilds only these, and drops what lies past the page's end)
        self.full = True
        self.tail = 0
        self.same_layout = False
        self.changed_roots: list = []
        # root index now -> its index at the previous derive, of every root whose objects are
        # the previous derive's, untouched by the page-wide passes (moved or not): a view may
        # keep what it built of them and give it the new indices
        self.kept: dict = {}


#: (search paths, fonts.dbf stamps) -> {name: (face, pixel size, fg colour, bg colour)}
_NAMED_FONTS: dict = {}

_NAMED_FONTS_LOCK = threading.Lock()

#: the 16 colour names of a fonts.dbf row (V1 order = STANDARD16)
_COLOUR_NAMES = (
    "BLACK",
    "BLUE",
    "GREEN",
    "CYAN",
    "RED",
    "MAGENTA",
    "BROWN",
    "LIGHT_GREY",
    "DARK_GREY",
    "LIGHT_BLUE",
    "LIGHT_GREEN",
    "LIGHT_CYAN",
    "LIGHT_RED",
    "LIGHT_MAGENTA",
    "YELLOW",
    "WHITE",
)


def _named_colour(name: str):
    from ..format.colourpalette import STANDARD16

    n = (name or "").strip().upper().replace(" ", "_")
    if n in ("LIGHT_GRAY", "GREY", "GRAY"):
        n = "LIGHT_GREY"
    if n in ("DARK_GRAY",):
        n = "DARK_GREY"
    return STANDARD16[_COLOUR_NAMES.index(n)] if n in _COLOUR_NAMES else None


def _named_fonts(path: str) -> dict:
    """The fonts.dbf fonts of the page's project and its include chain (first definition
    wins, CtDraw's search order): name (lower case) -> (face, pixel size, fg, bg)."""
    try:
        dirs = tuple(SymbolResolver.for_page(path).search_paths)
    except Exception:  # noqa: BLE001
        _note_exc("reader._named_fonts/path")
        return {}
    files = []
    for d in dirs:
        try:
            names = {n.lower(): n for n in os.listdir(d)}
        except OSError:
            continue
        if "fonts.dbf" in names:
            f = os.path.join(d, names["fonts.dbf"])
            try:
                st = os.stat(f)
            except OSError:
                continue
            files.append((f, st.st_mtime_ns, st.st_size))
    key = tuple(files)
    with _NAMED_FONTS_LOCK:
        got = _NAMED_FONTS.get(key)
    if got is not None:
        return got
    out: dict = {}
    from ..format.projects import read_dbf_rows

    for f, _m, _s in files:
        try:
            rows = read_dbf_rows(f)
        except Exception:  # noqa: BLE001
            _note_exc("reader._named_fonts/read", page=f)
            continue
        for r in rows:
            name = (r.get("NAME") or "").strip().lower()
            if not name or name in out:
                continue
            try:
                px = int(float((r.get("SIZE") or "0").strip() or "0"))
            except ValueError:
                px = 0
            out[name] = (
                (r.get("TYPE") or "").strip(),
                px,
                _named_colour(r.get("FGND_COLR") or ""),
                _named_colour(r.get("BGND_COLR") or ""),
            )
    with _NAMED_FONTS_LOCK:
        if len(_NAMED_FONTS) > 64:
            _NAMED_FONTS.clear()
        return _NAMED_FONTS.setdefault(key, out)


def _apply_legacy_value_box(obj, po, raw_props) -> None:
    """A V3/V4 Number or String as CtDraw's editor draws it."""
    st = obj.style
    font_name = raw_props.get("FONT") or raw_props.get("OFF_FONT") or raw_props.get("ON_FONT")
    path = getattr(_pc._PAGE_CTX, "path", None)
    font = _named_fonts(path).get(str(font_name).strip().lower()) if (font_name and path) else None
    if font is not None:
        face, px, fg, bg = font
        if face:
            st.font = face
        if px > 0:
            obj.raw["_font_px"] = px
        if fg is not None:
            st.text_colour = fg
        if bg is not None:
            st.filled, st.fill_colour = True, bg
        else:
            st.filled, st.fill_colour = False, None
    else:
        st.filled, st.fill_colour = False, None
    # (the box around it is the genie's own rectangle: the record draws only its text)
    st.line_colour = None
    st.line_style = 5
    obj.raw["_label_left"] = True
    if po.type_code == 0x1005:
        t = str(raw_props.get("OFF_TEXT") or raw_props.get("ON_TEXT") or "").strip()
        if len(t) >= 2 and t[0] == t[-1] == '"':
            t = t[1:-1]
        st.text = t
    else:
        expr = str(raw_props.get("EXPR") or "").strip()
        fmt = None
        tf = getattr(_pc._PAGE_CTX, "tag_format_map", None)
        if expr and tf is not None:
            try:
                fmt = tf.get(expr.lower())
            except Exception:  # noqa: BLE001
                _note_exc("reader._apply_legacy_value_box")
                fmt = None
        st.text = fmt or "####.#"


def _set_page_context(path: str) -> None:
    """This thread's per-page reader context (_PAGE_CTX): the tag formats a literal display
    measures with, the language tables a translated label mode reads (both cached per
    project)."""
    # (found once per derive: _derive_context reads the same map from here)
    fm = _tag_format_map(path) if _pc.get_label_mode() == "literal" else None
    _pc._PAGE_CTX.tag_format_map = fm
    _pc._PAGE_CTX.tag_formats = _tag_formats_for(path, fm) if fm is not None else None
    _pc._PAGE_CTX.path = path
    # bind the project's language tables only when a translation is requested (literal/native
    # need no lookup); cached per project dir
    db = None
    if _pc.get_label_mode() not in ("literal", "native"):
        try:
            from .lang import LanguageDB

            db = LanguageDB.for_page(path)
        except Exception:  # noqa: BLE001
            _note_exc("reader._set_page_context")
            db = None
    _pc._PAGE_CTX.lang_db = db


def _callable_key(f):
    """A stable identity for a lookup callable (a bound method is a new object per access)."""
    if f is None:
        return None
    return (id(getattr(f, "__self__", None)), getattr(f, "__func__", f))


def _derive_context(path: str, generation) -> tuple:
    """What a derive reads besides the records (call after :func:`_set_page_context`)."""
    fm = getattr(_pc._PAGE_CTX, "tag_format_map", None) if _pc.get_label_mode() == "literal" else None
    tf = None
    if fm is None and _pc.get_label_mode() == "literal":
        # literal mode without a project tag table measures with the lookup the editing layer
        # installed (_literal_display)
        tf = _callable_key(_tm._tag_format)
    db = getattr(_pc._PAGE_CTX, "lang_db", None)
    return (
        os.path.normcase(os.path.abspath(path)),
        generation,
        _pc.get_label_mode(),
        _pc.get_composite_members(),
        tuple(os.environ.get(k) for k in _DERIVE_ENV),
        _pc.render_options_key(),
        _pc.get_value_placeholder(),
        id(fm) if fm is not None else None,
        tf,
        id(db) if db is not None else None,
    )


def _record_sig(po) -> tuple:
    """Everything of a record its payload is serialised from (writer.object_payload)."""
    body = po.body_raw
    return (
        po.class_flag,
        po.type_code,
        po.subtype_size,
        po.an_str,
        po.raw_payload,
        body if type(body) is bytes else bytes(body),
    )


def _template_units(tpl_map: dict, starts: list) -> dict:
    """Template alignment per root unit."""
    out: dict = {}
    for k, v in tpl_map.items():
        s = starts[bisect_right(starts, k) - 1]
        out.setdefault(s, []).append((k - s, id(v)))
    return {s: tuple(e) for s, e in out.items()}


# The private re-parses of records, by the VALUE of their _record_sig: an undo / redo puts
# back records whose bytes an earlier derive already parsed, so a unit of thousands of records
# is not parsed again. A sig names the whole payload (writer.object_payload), and the parse is a
# pure function of the payload (plus the diagnostic coverage switch, part of the key); the reader
# never reads a parse's index / record_offset / next_link, and never changes a parse. Only
# parses made here are kept -- never a caller's (live) record. Bounded by count and by the
# memory the entries keep alive (:func:`_memo_cost`: a parse is several times its body). The
# byte budget holds the two versions of a large genie an undo / redo alternates between.
_FRESH_MEMO: "OrderedDict[tuple, tuple]" = OrderedDict()  # key -> (parse, cost)

_FRESH_MEMO_MAX = 40000

_FRESH_MEMO_BYTES = 96 << 20

_fresh_memo_size = [0]

_FRESH_LOCK = threading.Lock()


def _fresh_record(po, index: int, sig=None):
    """``po`` parsed afresh from its serialised payload."""
    if sig is None:
        sig = _record_sig(po)
    key = (sig, _pagemod._WANT_COVERAGE)
    with _FRESH_LOCK:
        hit = _FRESH_MEMO.get(key)
        if hit is not None:
            _FRESH_MEMO.move_to_end(key)
            return hit[0]
    payload = object_payload(po)
    fp = getattr(po, "_fresh_parse", None)
    shadow = None
    if fp is not None:
        try:
            if fp[0] == payload and fp[1] is not po and fp[1].type_code == po.type_code:
                # (kept in the memo too: an undo / redo puts back COPIES of the records,
                # which carry no stash)
                shadow = fp[1]
        except Exception:  # noqa: BLE001 -- a malformed stash: parse it here
            _note_exc("reader._fresh_record")
            pass
    if shadow is None:
        shadow = _parse_object(index, 0, len(payload), 0, payload)
    with _FRESH_LOCK:
        if key not in _FRESH_MEMO:
            cost = _memo_cost(key, shadow)
            _FRESH_MEMO[key] = (shadow, cost)
            _fresh_memo_size[0] += cost
            while _FRESH_MEMO and (
                len(_FRESH_MEMO) > _FRESH_MEMO_MAX or _fresh_memo_size[0] > _FRESH_MEMO_BYTES
            ):
                _old, (_parse, old_cost) = _FRESH_MEMO.popitem(last=False)
                _fresh_memo_size[0] -= old_cost
    return shadow


def _memo_cost(key, parse) -> int:
    """Rough memory cost of a memo entry."""
    total = 600 + len(parse.body_raw or b"") + len(parse.raw_payload or b"")
    for bag in parse.bags:
        total += 120 + 200 * len(bag.pairs)
    for v in key[0]:
        if isinstance(v, (bytes, bytearray)):
            total += len(v)
    return total


def clear_fresh_memo() -> None:
    """Forget the memoised record re-parses."""
    with _FRESH_LOCK:
        _FRESH_MEMO.clear()
        _fresh_memo_size[0] = 0


def _same_but_generation(a: tuple, b: tuple) -> bool:
    """Two derive contexts (:func:`_derive_context`) alike but for the library version."""
    return len(a) == len(b) and a[:1] + a[2:] == b[:1] + b[2:]


def _library_stale_records(previous, path: str):
    """After a library change: a fresh resolver and the records to re-derive, or None."""
    old = getattr(previous, "resolver", None)
    opened = getattr(old, "opened", None)
    if old is None or opened is None:
        return None
    nc = lambda p: os.path.normcase(os.path.abspath(p))  # noqa: E731
    try:
        fresh = _py._build_resolver(path)
        changed = set()
        from ..format.fileio import file_version

        for p, stamp in list(opened.items()):
            try:
                # (the version the resolver read it at: a second write of one size within
                # one clock tick counts, fileio.file_version)
                now = file_version(p)
            except OSError:
                now = None
            if now != stamp:
                changed.add(p)
        found_now = {}
        for key, before in list(getattr(old, "found", {}).items()):
            name, _, ext = key.rpartition(".")
            now_paths = [nc(x) for x in fresh._find_library_files(name, ext)]
            found_now[key] = now_paths
            if now_paths != before:
                changed |= set(now_paths) | set(before)
        # the fresh resolver knows what the old one read (a unit kept is not derived again,
        # so it would not open its libraries: the next change must still find them)
        for p in opened:
            try:
                fresh.opened.setdefault(p, file_version(p))
            except OSError:
                pass
        for key, paths in found_now.items():
            fresh.found.setdefault(key, paths)
    except Exception:  # noqa: BLE001 -- then everything is derived again
        _note_exc("reader._library_stale_records")
        return None
    if any(p.lower().endswith(".ctt") for p in changed):
        return None
    if not changed:
        return fresh, set()
    from . import genieusage as GU
    from ..format import links

    memo: dict = {}
    #: library folder -> the resolver of its own search path: the genies and symbols nested
    #: in an element of a library are found as its composition finds them (genie_place
    #: _refresh_nested, updatepages._Current.for_library), not on the page's path
    own_paths: dict = {}

    def resolver_of(lib_file: str):
        d = os.path.normcase(os.path.dirname(os.path.abspath(lib_file)))
        r = own_paths.get(d)
        if r is None:
            from ..format.symbols import SymbolResolver

            r = own_paths[d] = SymbolResolver.for_page(lib_file)
        return r

    def files_of(lib: str, ext: str, depth: int = 0, res=None) -> set:
        res = fresh if res is None else res
        key = (lib.lower(), ext.lower(), tuple(getattr(res, "search_paths", ()) or ()))
        if key in memo:
            return memo[key]
        memo[key] = out = set()  # (a cycle ends here)
        for f in res._find_library_files(lib, ext):
            out.add(nc(f))
            if ext.lower() != "ctm" or depth > 8:
                continue
            for table in GU.library_tables(f).values():
                for nm in table.names:
                    if nm.startswith("\\") or "@super" in nm.lower():
                        continue
                    # (the library is all before the LAST dot of 'lib.elem':)
                    stem, _, e2 = links.strip_stamp(nm).rpartition(".")
                    l2 = stem.rpartition(".")[0]
                    e2 = e2.lstrip("<").lower()
                    if l2 and e2 in ("ctm", "ctl"):
                        out |= files_of(l2, e2, depth + 1, resolver_of(f))
        return out

    ids = set()
    for po in previous.parsed.page.objects:
        try:
            ref = reference_from_object(po)
        except Exception:  # noqa: BLE001 swallow-ok: an unreadable link is derived again
            ids.add(id(po))
            continue
        if ref is None:
            continue
        if fresh.resolve(ref) is None or files_of(ref.library, ref.ext) & changed:
            ids.add(id(po))
    return fresh, ids


def _nested_member_indices(parsed) -> frozenset:
    """The record indexes that lie inside a genie nested in the element (a record with a
    class_flag and dwFlags 0x200 below the root, and its subtree)."""
    try:
        from ..format import placement as _P

        objs = list(parsed.page.objects)
        parents = _P.forest_parents(objs)
    except Exception:  # noqa: BLE001  # swallow-ok: a forest that cannot be framed has no nested genies
        return frozenset()
    out = set()
    for k, po in enumerate(objs):
        a = k
        while a is not None:
            # (a placed genie's root carries 0x200 whether the element's root group wraps it,
            # as in the file, or not, as in the editing session that drops the synthesised
            # root; the element's own root group never carries it)
            if objs[a].class_flag and (objs[a].subtype_size & 0x200):
                out.add(k)
                break
            a = parents[a]
    return frozenset(out)


def derive_page(
    parsed,
    path: str,
    previous=None,
    *,
    records_fresh: bool = False,
    generation=None,
    touched=(),
    shadows=None,
) -> tuple:
    """Derive the page model from a parsed .ctg."""
    _set_page_context(path)
    # an element window: the records inside a genie nested in the element (their texts
    # show the binding chain, a direct member its own template: readerparts.style)
    _pc._PAGE_CTX.nested_members = _nested_member_indices(parsed) if _pc.get_element_window() else frozenset()
    context = _derive_context(path, generation)
    resolver = None
    if (
        previous is not None
        and previous.parsed is parsed
        and previous.page is not None
        and previous.context != context
        and _same_but_generation(previous.context, context)
    ):
        # only the libraries' version changed (a genie saved, a library refreshed): the units
        # that read a changed library file are derived again, the others kept
        stale = _library_stale_records(previous, path)
        if stale is None:
            previous = None
        else:
            resolver, ids = stale
            touched = set(touched or ()) | ids
    elif previous is not None and (
        previous.parsed is not parsed or previous.context != context or previous.page is None
    ):
        previous = None
    if resolver is None:
        resolver = previous.resolver if previous is not None else _py._build_resolver(path)
    page = Page(source_path=path, name=os.path.splitext(os.path.basename(path))[0])
    page.ts_created = parsed.container.ts_created
    page.ts_modified = parsed.container.ts_modified
    der = PageDerivation()
    der.parsed, der.context, der.resolver = parsed, context, resolver

    pb = parsed.page
    if pb is not None:
        page.appearance = PageAppearance(width=pb.page_width or 0, height=pb.page_height or 0)
        page.area = (pb.area_code or "").strip()
        page.identity = pb.page_identity_hex
        if pb.thumbnail:
            bmp = _extract_bmp(pb.thumbnail)
            if bmp:
                page.thumbnail_bmp = bmp
        _derive_objects(
            page,
            list(pb.objects),
            path,
            resolver,
            previous,
            der,
            records_fresh,
            set(touched or ()),
            shadows if shadows is not None and shadows.parsed is parsed else None,
        )

    at = parsed.attributes
    if at is not None:
        page.raw_attributes = at.page_properties()
        if not page.appearance.width:
            page.appearance.width = at.width
            page.appearance.height = at.height
        page.appearance.background_colour = at.bg_color0

    der.page = page
    # the editor's main allocation path: collect here, on the GUI thread (infra.gcguard)
    _gcguard.checkpoint()
    return page, der


def _derive_objects(
    page: Page,
    records: list,
    path: str,
    resolver,
    previous,
    der,
    records_fresh: bool,
    touched: set,
    shadows=None,
) -> None:
    """``page.objects`` for ``records`` (see :func:`derive_page`)."""
    n = len(records)
    sigs = [_record_sig(po) for po in records]
    parents = _forest_parents([max(0, int(po.class_flag or 0)) for po in records])
    starts = [i for i in range(n) if parents[i] is None]
    spans = {s: e - s for s, e in zip(starts, starts[1:] + [n])}
    objs: list = [None] * n
    shadow: list = [None] * n
    changed: list = []  # roots derived afresh
    reused: list = []  # roots whose objects come from the previous derive
    moved_from: dict = {}  # reused root -> its index at the previous derive
    old_at: dict = {}
    if previous is not None:
        prec, psig, pshadow, pobj = (previous.records, previous.sigs, previous.shadow, previous.page.objects)
        old_at = {id(po): i for i, po in enumerate(prec)}
        for s in starts:
            ln = spans[s]
            o = old_at.get(id(records[s]))
            ok = o is not None and previous.spans.get(o) == ln
            if ok:
                for k in range(ln):
                    r = records[s + k]
                    if r is not prec[o + k] or sigs[s + k] != psig[o + k] or id(r) in touched:
                        ok = False
                        break
            if ok:
                objs[s : s + ln] = pobj[o : o + ln]
                shadow[s : s + ln] = pshadow[o : o + ln]
                reused.append(s)
                moved_from[s] = o
            else:
                changed.append(s)
    else:
        changed = list(starts)

    sh_at = {id(po): i for i, po in enumerate(shadows.records)} if shadows is not None else {}

    def shadow_of(j: int):
        r = records[j]
        if previous is not None:
            o = old_at.get(id(r))
            if o is not None and sigs[j] == previous.sigs[o]:
                return previous.shadow[o]  # an unchanged record: its earlier parse
        elif records_fresh:
            return r
        o = sh_at.get(id(r))
        if (
            o is not None
            and o < len(shadows.shadow)
            and shadows.shadow[o] is not None
            and sigs[j] == shadows.sigs[o]
        ):
            return shadows.shadow[o]  # the other derive's parse of these bytes
        return _fresh_record(r, j, sigs[j])

    ctx = _pc._PAGE_CTX

    def derive(units) -> None:
        # the record's POSITION, for the rules that depend on where it lies (an element
        # window's members of nested genies, style._element_window_props): a parse may be one
        # made at another position (an unchanged record's earlier parse after a delete or an
        # insert, the memo's parse of the same bytes, a paste's parse at 0), so its .index is
        # never read
        before_at = getattr(ctx, "record_at", None)
        try:
            for s in units:
                for j in range(s, s + spans[s]):
                    if shadow[j] is None:
                        shadow[j] = shadow_of(j)
                    ctx.record_at = (shadow[j], j)
                    objs[j] = _object_from(shadow[j], resolver, 0)
        finally:
            ctx.record_at = before_at

    derive(changed)
    tpl_map, tpl_read = _py._template_alignment_read(shadow, resolver, previous)
    tpl_units = _template_units(tpl_map, starts)
    root_flags = _pg._sibling_button_flags([objs[s] for s in starts])
    full = previous is None
    if not full and root_flags != previous.root_flags:
        # what a unit's derivation reads outside its own records changed: derive every unit
        derive(reused)
        changed, reused, full = list(starts), [], True
    elif not full:
        # the template alignment: a unit's derivation reads the entries of its OWN records
        # (relative to its start) -- one whose entries are not what they were at its old place
        # is derived again; a template unit that only shifted (an edited genie before it grew /
        # shrank) keeps its objects
        ptu = previous.tpl_units
        redo = [s for s in reused if tpl_units.get(s, ()) != ptu.get(moved_from[s], ())]
        if redo:
            derive(redo)
            gone = set(redo)
            reused = [s for s in reused if s not in gone]
            changed = sorted(changed + redo)

    # the page-wide passes run over every object: forget what they set on a reused one
    before = {}
    for s in reused:
        for j in range(s, s + spans[s]):
            raw = objs[j].raw
            d, a = raw.pop("_dup_suppressed", None), raw.pop("_alarm_indicator", None)
            if d is not None or a is not None:
                before[j] = (d, a)
    page.objects = objs
    only = None if full else {j for s in changed for j in range(s, s + spans[s])}
    try:
        _build_object_forest(page, shadow, resolver, only=only, tpl_map=tpl_map)
        _suppress_overlapping_duplicates(page)
        # (stacked DIFFERENT states -- a multi-state value slot, a light table's Tag
        # column -- are all drawn: CtDraw overprints them)
        try:
            _alarm_indicators(page, shadow, resolver, path)
        except Exception:  # noqa: BLE001 -- editor chrome only, never fail a page load
            _note_exc("reader._derive_objects/alarm indicators")
            pass
    finally:
        # (the subtree ends of the build: the page's members may change after the derive)
        page.__dict__.pop("_subtree_ends", None)

    # the roots whose objects changed: derived afresh, or a page-wide pass flagged a reused
    # object differently (a duplicate label elsewhere was edited)
    touched_roots = set(changed)
    kept = {}
    for s in reused:
        for j in range(s, s + spans[s]):
            raw = objs[j].raw
            if before.get(j, (None, None)) != (raw.get("_dup_suppressed"), raw.get("_alarm_indicator")):
                touched_roots.add(s)
                break
        else:
            kept[s] = moved_from[s]
    # the first root from which the root LAYOUT differs from the previous derive (a unit that
    # moved, grew / shrank or is new; n = only a removed tail): a view rebuilds every root from
    # there on -- an object appended at the top of the z-order costs one unit, not the page
    tail = 0
    if not full:
        tail = next((s for s in starts if previous.spans.get(s) != spans[s] or moved_from.get(s, s) != s), n)
        touched_roots.update(s for s in starts if s >= tail)
    der.records, der.sigs, der.shadow, der.spans = records, sigs, shadow, spans
    der.tpl_map, der.tpl_read = tpl_map, tpl_read
    der.tpl_units, der.root_flags = tpl_units, root_flags
    der.full = full
    der.tail = tail
    der.same_layout = not full and tail == n and len(previous.records) == n
    der.changed_roots = sorted(touched_roots)
    der.kept = kept if not full else {}


def model_difference(a: Page, b: Page) -> str:
    """'' when two derived pages are the same model, else where they first differ (the check of
    an incremental derive against a full one). The page name / source path are not compared."""
    for f in (
        "appearance",
        "area",
        "identity",
        "thumbnail_bmp",
        "raw_attributes",
        "ts_created",
        "ts_modified",
    ):
        if getattr(a, f) != getattr(b, f):
            return "page %s differs" % f
    if len(a.objects) != len(b.objects):
        return "%d objects != %d" % (len(a.objects), len(b.objects))
    import dataclasses

    def short(v) -> str:
        s = repr(v)
        return s if len(s) <= 160 else s[:157] + "..."

    for i, (x, y) in enumerate(zip(a.objects, b.objects)):
        if x == y:
            continue
        for f in dataclasses.fields(x):
            va, vb = getattr(x, f.name), getattr(y, f.name)
            if va == vb:
                continue
            if f.name == "raw" and isinstance(va, dict) and isinstance(vb, dict):
                keys = sorted(k for k in set(va) | set(vb) if va.get(k) != vb.get(k))
                return "object %d (%s) raw %s: %s != %s" % (
                    i,
                    x.kind.value,
                    keys,
                    short({k: va.get(k) for k in keys}),
                    short({k: vb.get(k) for k in keys}),
                )
            return "object %d (%s) %s: %s != %s" % (i, x.kind.value, f.name, short(va), short(vb))
        return "object %d differs" % i
    return ""


# type_code of a group/genie/composite container (records nest their members)
_GROUP_TYPE_CODE = 0x08

# ---------------------------------------------------------------------------------------------
# CtDraw's design-time ALARM INDICATOR (editor chrome, never written to the .ctg).
#
# Only the GROUP class paints it: the editor's paint draws its
# children, then the indicator when atoi(AI_ON) != 0. No other class reads AI_ON
# for drawing, so an AI_ON on a TEXT draws nothing:
#   U      = union of the direct children's extents (Bounds/rect2); children whose
#            in-memory '!IsAttachedInput' is 1 (attached Touch/Keyboard input records) skipped
#   W      = AI_WIDTH (3), pad = AI_PADDING (5), inside = AI_INSIDE (0), pen = AI_COLOUR (0xBFBFBF,
#            0x00RRGGBB), fill = the highest alarm priority's UNACKONFG (almprior.DBF; red when none)
#   inside == 0: inner = U + 1 + pad ; outer = inner + (W + 1)
#   inside != 0: outer = U           ; inner = U - (W + 1)
#   FillRect x4 strips (W + 1) wide along outer's edges with `fill`; then with a 1px `pen` and
#   the NULL brush Rectangle(inner) and Rectangle(outer).
# flag (AI_FLAG_ON absent or != 0): the priority's LIBNAME.LIBOBJNAME genie (# default a 20x20 white box + X) with its Bounds top-left put at
#   (AI_FLAG_LOC, default 0) computed against `outer`.
# The engine's stored rect2 of such a group = outer U flag.
# Exports (print, DIB renders, the drag image) never draw it -> editor-only.
_AI_UNION_SKIP = frozenset({_codes.TC.TOUCH, _codes.TC.KEYBOARD, 0xFFFF})

_c_atoi = _alarmind.c_atoi

_c_strtoul = _alarmind.c_strtoul

_rgb_packed = _alarmind._rgb_packed

_ai_props = _alarmind.props_of

_ai_flag_xy = _alarmind.flag_xy

# the alarm priorities read per project dir (cleared between test modules)
_AI_PRIORITY_CACHE = _alarmind._PRIORITY_CACHE


def _ai_priority(page_path: str):
    """(fill_rgb, (libname, libobjname) | None) of the HIGHEST alarm priority (lowest PRIORITY
    number) -- CtDraw over the project's 'Alarm Priority' records (the page's
    own project's, never an included one's), UNACKONFG via (std::stoul,
    0x00RRGGBB; failure -> red)."""
    return _alarmind.top_priority(page_path, own=True)


_TOP_PRIORITY = object()


def _ai_flag_object(
    page_path: str, resolver, loc: int, outer, W: int, ref=_TOP_PRIORITY, strict: bool = True
):
    """An alarm indicator flag as a GraphicObject, or the default flag."""
    if ref is _TOP_PRIORITY:
        _fill, ref = _ai_priority(page_path)
    resolved = None
    if ref is not None and resolver is not None:
        try:
            resolved = resolver.resolve(SymbolReference(ref[0], ref[1], "ctm", "genie"), strict=strict)
        except Exception:  # noqa: BLE001
            _note_exc("reader._ai_flag_object")
            resolved = None
    if resolved is not None and (resolved.width or 0) > 0 and (resolved.height or 0) > 0:
        w, h = resolved.width, resolved.height  # element head W,H = its root Bounds
        xy = _ai_flag_xy(loc, outer, W, w, h)
        if xy is None:
            return None
        flag = GraphicObject(kind=ObjectKind.SYMBOL)
        flag.geometry.x, flag.geometry.y = xy
        flag.geometry.extent_x, flag.geometry.extent_y = w, h
        _py._expand_element(flag, resolved, resolver, 1)
        if flag.children:
            return flag
    xy = _ai_flag_xy(loc, outer, W, 20, 20)
    return ("default",) + tuple(xy) if xy is not None else None


def _alarm_indicators(page: Page, pobjs, resolver, page_path: str) -> None:
    """Tag every GROUP record whose own AI_ON atoi()s non-zero with CtDraw's alarm-indicator
    geometry (``raw['_alarm_indicator']``); the scene draws it after the group's subtree."""
    n = min(len(page.objects), len(pobjs))
    for i in range(n):
        po = pobjs[i]
        if po.type_code != _GROUP_TYPE_CODE:
            continue
        obj = page.objects[i]
        # cheap prefilter on the heuristic bag union, then the strict own-bag read
        if not any(str(k).upper() == "AI_ON" for k in (obj.raw.get("_props") or {})):
            continue
        pr = _ai_props(po)
        if "AI_ON" not in pr or _c_atoi(pr["AI_ON"]) == 0:
            continue
        U = None
        bounds = []
        for j in obj.members:
            # '!IsAttachedInput' is an in-memory key CtDraw sets on attached input records
            # (never on disk): skip those classes + the DummyMarker instead
            if j >= n or pobjs[j].type_code in _AI_UNION_SKIP:
                continue
            gh = _geom.parse_geom_header(pobjs[j].body_raw)
            if gh is None:
                continue
            a = gh.rect2
            if a[2] <= a[0] or a[3] <= a[1]:
                continue  # UnionRect ignores empty rects
            bounds.append((j, tuple(a)))
            U = a if U is None else (min(U[0], a[0]), min(U[1], a[1]), max(U[2], a[2]), max(U[3], a[3]))
        if U is None:
            continue
        st = _alarmind.settings(pr)
        W = int(st["width"])
        pen = int(st["colour"])
        outer, inner, s = _alarmind.frame(U, st)  # s = strip width (Wd - 1, Wd = W + 2)
        flag = None
        if st["flag_on"]:
            flag = _ai_flag_object(page_path, resolver, int(st["flag_loc"]), outer, W)
        sub = page.subtree(i) if hasattr(page, "subtree") else [i]
        obj.raw["_alarm_indicator"] = {
            "outer": outer,
            "inner": inner,
            "strip": s,
            "fill": _ai_priority(page_path)[0],
            "pen": _rgb_packed(pen),
            "flag": flag,
            "last": max(sub) if sub else i,
            # what a flag of another priority is placed by (the runtime's, sim_states)
            "width": W,
            "flag_loc": int(st["flag_loc"]),
            "flag_on": bool(st["flag_on"]),
            # what the frame is drawn round: the runtime sizes it to the children's CURRENT
            # Bounds (a wider value widens it, sim_states)
            "members": bounds,
            "settings": dict(st),
            # page coordinates above are for THIS rect1 origin: EditableDocument.move_objects
            # translates the model in place (no re-parse), so the scene offsets the indicator
            # by the group's current position minus this one
            "origin": (obj.geometry.x, obj.geometry.y),
        }


_forest_parents = _forest.parents_from_counts


def _graft_metafile_members(page: Page, objs, pobjs, resolver, tpl_map, mine, ctx=None) -> None:
    """Give an art-stripped metafile member its owner's picture."""
    if ctx is None:
        ctx = _pg._GraftCtx(page, pobjs, resolver)
    elements: dict = {}

    def via_owner_rule(i: int):
        tw = ctx.twin(i)
        return tw if tw is not None and tw.type_code == 0x09 else None

    def via_nearest_link(i: int, anc):
        owner = next((a for a in anc if pobjs[a].class_flag and pobjs[a].subtype_size & 0x1), None)
        if owner is None:
            return None
        try:
            ref = reference_from_object(pobjs[owner])
            if ref is None and tpl_map and owner in tpl_map:
                ref = reference_from_object(tpl_map[owner])  # the page copy lost its ref
            handle = int((pobjs[i].an_str or "").strip())
        except Exception:  # noqa: BLE001
            _note_exc("reader._graft_metafile_members.via_nearest_link/owner")
            return None
        if ref is None:
            return None
        key = (ref.library.lower(), ref.element.lower(), ref.ext.lower())
        if key not in elements:
            try:
                rs = resolver.resolve(ref)
            except Exception:  # noqa: BLE001
                _note_exc("reader._graft_metafile_members.via_nearest_link/resolve")
                rs = None
            elements[key] = rs.objects if rs is not None else None
        return next((r for r in elements[key] or () if r.type_code == 0x09 and get_handle(r) == handle), None)

    for i, po in enumerate(pobjs[: len(objs)]):
        if po.type_code != 0x09 or po.subtype_size & 0x2 or not mine(i):
            continue
        o = objs[i]
        if o.raw.get("_embedded_bmp") is not None:
            continue
        anc = page.ancestors(i)  # nearest first
        if not anc or any(objs[a].children for a in anc):
            continue  # the expanded element draws it
        for find in (lambda: via_owner_rule(i), lambda: via_nearest_link(i, anc)):
            twin = find()
            if twin is not None:
                _py._attach_metafile(o, twin, 0)
                if o.raw.get("_embedded_bmp") is not None:
                    break


def _sharpen_metafiles(objs, mine) -> None:
    """Re-render metafiles at the size CtDraw draws them."""

    def group_xform(o):
        # the scene positions a group from its CHILDREN's xform (_make_symbol_item); a nested
        # container's own '_symbol_xform' is its PARENT's mapping, overwritten by _expand_element
        return next((c.raw["_symbol_xform"] for c in o.children if c.raw.get("_symbol_xform")), None)

    def walk(children, sx: float, sy: float, depth: int) -> None:
        for c in children:
            if c.children:
                x = group_xform(c)
                if x is not None and depth < 8:
                    walk(c.children, sx * abs(x[2] or 1.0), sy * abs(x[3] or 1.0), depth + 1)
                continue
            wmf = c.raw.get("_wmf")
            cur = c.raw.get("_embedded_bmp")
            if wmf is None or cur is None or not c.geometry:
                continue
            w = int(round(abs(c.geometry.extent_x * sx)))
            h = int(round(abs(c.geometry.extent_y * sy)))
            if w < 2 or h < 2 or (w, h) == (c.geometry.extent_x, c.geometry.extent_y):
                continue  # drawn 1:1 already
            eb = render_metafile(b"", 0, w, h, wmf=wmf)
            if eb is not None:
                c.raw["_embedded_bmp"] = (eb.width, eb.height, eb.rgba, eb.has_alpha)

    for i, o in enumerate(objs):
        if o.children and mine(i):
            x = group_xform(o)
            if x is not None:
                walk(o.children, abs(x[2] or 1.0), abs(x[3] or 1.0), 0)


def _mark_missing_elements(page: Page, pobjs, resolver, mine) -> None:
    """Mark records whose library element can't be found."""
    objs = page.objects
    for idx, obj in enumerate(objs):
        if not mine(idx) or obj.raw.get("_under_missing") or idx >= len(pobjs):
            continue
        r1 = obj.raw.get("_rect1")
        if not r1 or abs(r1[2] - r1[0]) <= 1 or abs(r1[3] - r1[1]) <= 1:
            continue
        if pobjs[idx].type_code == 0xFFFF and not pobjs[idx].class_flag:
            name = _placeholder_name(pobjs[idx], resolver)
            if name is not None:
                obj.raw["_missing_element"] = name
            continue
        try:
            ref = reference_from_object(pobjs[idx])
            if ref is None or resolver.resolve(ref) is not None:
                continue
        except Exception:  # noqa: BLE001
            _note_exc("reader._mark_missing_elements")
            continue
        obj.raw["_missing_element"] = "%s.%s" % (ref.library, ref.element)
        for j in page.subtree(idx):
            if j != idx:
                objs[j].raw["_under_missing"] = True


def _placeholder_name(po, resolver) -> "str | None":
    """The library element a DummyMarker placeholder names ('_Library'), as
    'library.element', when it does not resolve; None when it does (the open updates place
    the genie there); "A library object" for a marker with no name."""
    from ..format.symbols import reference_from_body, reference_from_symbol_value

    try:
        lib = str(po.raw_properties().get("_Library") or "").strip()
    except Exception:  # noqa: BLE001
        _note_exc("reader._placeholder_name")
        lib = ""
    if not lib:
        return "A library object"
    try:
        ref = (
            reference_from_body(lib.encode("latin-1", "replace"))
            if ".<ct" in lib.lower()
            else reference_from_symbol_value(lib)
        )
        if ref is None:
            return lib
        if resolver.resolve(ref) is not None:
            return None
    except Exception:  # noqa: BLE001
        _note_exc("reader._placeholder_name")
        return lib
    return "%s.%s" % (ref.library, ref.element)


def _build_object_forest(page: Page, pobjs, resolver=None, only=None, tpl_map=None) -> None:
    """Wire ``page.objects`` into the record forest."""
    objs = page.objects
    n = len(objs)
    counts = [max(0, int(getattr(po, "class_flag", 0) or 0)) for po in pobjs[:n]]

    # the wiring runs over EVERY object: a reused unit keeps its objects but may sit at another
    # index now (a record inserted / deleted before it)
    parents = _forest_parents(counts)
    for obj in objs:
        obj.members = []
    for i, obj in enumerate(objs):
        parent = parents[i]
        obj.parent = parent
        obj.container_count = counts[i]
        if parent is not None:
            objs[parent].members.append(i)
    # a record's subtree is the run of its descendants (a depth-first forest): Page.subtree
    # reads the ends while the passes run; _derive_objects drops them
    ends = list(range(1, n + 1))
    for i in range(n - 1, -1, -1):
        p = parents[i]
        if p is not None and ends[i] > ends[p]:
            ends[p] = ends[i]
    page._subtree_ends = ends

    def mine(i) -> bool:
        return only is None or i in only

    # the appearance owners of the root units being derived, shared by every graft below
    ctx = _pg._GraftCtx(page, pobjs, resolver)
    for idx, obj in enumerate(objs):
        if not obj.members or not mine(idx):
            continue
        po = pobjs[idx] if idx < len(pobjs) else None
        _classify_container(obj, po)
        # A composite genie's artwork lives in its composition cache, not inline (its inline
        # members are artless state slots). Resolve the cache, draw it scaled into the placement
        # box, and suppress the artless inline members so they don't show as empty placeholders.
        if (
            obj.kind is ObjectKind.COMPOSITE_GENIE
            and resolver is not None
            and po is not None
            and _pc.get_composite_members()
        ):
            # the runtime look (set_composite_members): the instance's own member records with
            # the appearance of the records that OWN them -- the .ctF rule (ctf._Owners, the
            # runtime draws the page's records, not the cache element)
            try:
                _pg._graft_template_member_colours(page, objs, pobjs, idx, resolver, ctx=ctx)
            except Exception:  # noqa: BLE001
                _note_exc("reader._build_object_forest/composite members")
        elif obj.kind is ObjectKind.COMPOSITE_GENIE and resolver is not None and po is not None:
            try:
                _expand_composite(page, obj, po, idx, resolver)
            except Exception:  # noqa: BLE001
                _note_exc("reader._build_object_forest/composite")
                pass
        # The same art-stripping happens to a plain GENIE container placing a .ctm/.ctl element
        # (e.g. sa_dataobjects.ClockTimer): inline members are empty slots, the drawing lives in
        # the library element. Expand only when the inline subtree is genuinely artless -- a genie
        # whose members carry the substituted drawing renders inline (CtDraw composes those
        # without touching the template).
        elif obj.kind is ObjectKind.GENIE and resolver is not None and po is not None:
            if not any(_py._member_has_art(objs[j]) for j in page.subtree(idx) if j != idx):
                try:
                    _expand_composite(page, obj, po, idx, resolver, exts=_py._EXPANDABLE_EXTS)
                except Exception:  # noqa: BLE001
                    _note_exc("reader._build_object_forest/genie expand")
                    pass
            elif not obj.children:
                # kept INLINE (members carry substituted art): graft the template's STATIC fills
                # onto any member rect that lost its style block.
                try:
                    _pg._graft_template_member_colours(page, objs, pobjs, idx, resolver, ctx=ctx)
                except Exception:  # noqa: BLE001
                    _note_exc("reader._build_object_forest/genie graft")
                    pass

    if resolver is not None:
        _mark_missing_elements(page, pobjs, resolver, mine)

    # symbol-set state selection over the page stream too: CtDraw's editor draws ONE state of a
    # tc=0x0C set: the first with art
    for i, o in enumerate(objs):
        if i < len(pobjs) and pobjs[i].type_code == 0x0C and o.members and mine(i):
            states = [
                objs[m]
                for m in o.members
                if pobjs[m].type_code in _py.SET_STATE_TCS and not objs[m].raw.get("_state_hidden")
            ]
            if len(states) > 1:
                _py._select_set_state(states, objs)

    # Inline (un-expanded) genie subtrees on the page can carry the same art-stripped members as
    # a cache stream (vertex-less polygons, empty symbol slots): repair them from the referenced
    # library element. The PAGE copy often lost the ref too; the index-aligned TEMPLATE record
    # keeps it.
    if resolver is not None:
        if tpl_map is None:
            tpl_map = _py._template_alignment_safe(pobjs, resolver)
        # the aligned prefix records CAME FROM THE TEMPLATE: CtDraw locks them on the page (you
        # edit the .ctt, not its copies) -- flag them so the editor can refuse selection/move/delete.
        for idx in tpl_map:
            if idx < len(objs) and mine(idx):
                objs[idx].raw["_from_template"] = True

        # a kept-inline GENIE of the template prefix whose page copy lost its ref: its members
        # are art-stripped too -- graft their
        # appearance from the element the aligned TEMPLATE record names
        for idx, tpo in tpl_map.items():
            if (
                idx >= len(objs)
                or not mine(idx)
                or objs[idx].kind is not ObjectKind.GENIE
                or objs[idx].children
                or not objs[idx].members
            ):
                continue
            try:
                if reference_from_object(pobjs[idx]) is not None:
                    continue  # grafted with its own ref above
                tref = reference_from_object(tpo)
            except Exception:  # noqa: BLE001
                _note_exc("reader._build_object_forest/template ref")
                tref = None
            if tref is None:
                continue
            try:
                _pg._graft_template_member_colours(page, objs, pobjs, idx, resolver, ref=tref, ctx=ctx)
            except Exception:  # noqa: BLE001
                _note_exc("reader._build_object_forest/template graft")
                pass

        # template-prefix copies no genie graft reached (the template's own container kept its
        # link, so its members normally took their twins above): the aligned template record IS
        # the twin -- _template_alignment_span pairs records whose rect1 are equal -- for the
        # vertex array of a vertex-less polygon / pipe, a text's / free line's colour, a filled
        # face's NULL pen
        for idx, tpo in tpl_map.items():
            if idx >= len(objs) or idx >= len(pobjs) or not mine(idx):
                continue
            mpo, o = pobjs[idx], objs[idx]
            if mpo.subtype_size & 0x2 or o.raw.get("_twin_grafted") or tpo.type_code != mpo.type_code:
                continue
            try:
                _pg._graft_twin_extras(o, mpo, tpo, ctx)
                if (
                    mpo.type_code == 0x05
                    and not o.raw.get("_twin_text")
                    and _pg._graft_stripped_text(o, mpo, tpo, ctx, objs, idx)
                ):
                    o.raw["_twin_text"] = True
                tcb = ctx.cb(tpo)
                if (
                    tcb is not None
                    and o.style is not None
                    and o.style.filled
                    and mpo.type_code in _py._GRAFT_TYPE_CODES
                    and tcb.flags[2]
                    and _ps._design_colour(tcb.slots[0]) is None
                ):
                    o.style.line_colour = None
                    o.style.line_style = 5
            except Exception:  # noqa: BLE001
                _note_exc("reader._build_object_forest/template line")
                pass

        # ICON-FACE buttons: a TEXT caption of the form '#lib.element' draws the library element's
        # bitmap on the button face
        for bi, obj in enumerate(objs):
            if obj.kind is not ObjectKind.BUTTON or not mine(bi) or obj.raw.get("_button_icon") is not None:
                continue
            txt = str((obj.raw.get("_props") or {}).get("TEXT", "")).strip()
            if not txt.startswith("#") or "." not in txt:
                continue
            lib, _, elem = txt[1:].rpartition(".")  # (the last dot:)
            try:
                rs = resolver.resolve(SymbolReference(lib, elem, "ctl", "symbol"))
            except Exception:  # noqa: BLE001
                _note_exc("reader._build_object_forest/icon face resolve")
                rs = None
            if rs is None:
                continue
            for epo in rs.objects:
                if epo.type_code != 0x07:
                    continue
                geo = _pc._read_geometry(epo.body_raw, 0)
                try:
                    eb = embedded_bitmap_from_body(epo.body_raw, geo["end"] if geo else 0)
                except Exception:  # noqa: BLE001
                    _note_exc("reader._build_object_forest/icon face bitmap")
                    eb = None
                if eb is not None:
                    obj.raw["_button_icon"] = (eb.width, eb.height, eb.rgba, eb.has_alpha)
                    break
        for idx, obj in enumerate(objs):
            if not obj.members or obj.children or not mine(idx):
                continue
            po = pobjs[idx] if idx < len(pobjs) else None
            if po is None or po.type_code != _GROUP_TYPE_CODE:
                continue
            ref = None
            try:
                ref = reference_from_object(po)
                if ref is None and idx in tpl_map:
                    ref = reference_from_object(tpl_map[idx])
            except Exception:  # noqa: BLE001
                _note_exc("reader._build_object_forest/prefix ref")
                pass
            sub = [j for j in sorted(page.subtree(idx)) if j != idx]
            pairs = [(objs[j], pobjs[j]) for j in sub if j < len(pobjs)]

            def twins(idx=idx, n=len(pairs)):
                own = ctx.owners(idx)  # resolved only when the count pairing fails
                return own[1:] if own is not None and len(own) == n + 1 else None

            try:
                if _py._repair_stripped_nodes(po, pairs, resolver, 1, ref=ref, twins=twins):
                    # repaired state slots are mutually exclusive: draw one
                    for j in sub:
                        if j < len(pobjs) and pobjs[j].type_code == _py._SYMBOL_SET_TC and objs[j].members:
                            _py._reselect_set_state(objs[j], objs, pobjs)
            except Exception:  # noqa: BLE001
                _note_exc("reader._build_object_forest/prefix repair")
                pass

        # leaf graft: a stripped page symbol whose index-aligned TEMPLATE record carries the art
        # directly (embedded DIB or its own ref).
        for idx, tpo in tpl_map.items():
            if idx >= len(objs) or tpo.type_code != 0x07 or not mine(idx):
                continue
            o = objs[idx]
            if o.children or o.raw.get("_embedded_bmp") is not None or o.raw.get("_non_drawable"):
                continue
            try:
                if _pg._graft_dib(o, tpo):
                    continue
                tref = reference_from_object(tpo)
                if tref is not None and tref.ext in _py._EXPANDABLE_EXTS:
                    rs = resolver.resolve(tref)
                    if rs is not None:
                        _py._expand_element(o, rs, resolver, 1)
            except Exception:  # noqa: BLE001
                _note_exc("reader._build_object_forest/leaf graft")
                pass

    # art-stripped METAFILE members of kept-inline genies play their element twin's WMF;
    # element pictures are re-rendered at their drawn size
    if resolver is not None:
        try:
            _graft_metafile_members(page, objs, pobjs, resolver, tpl_map, mine, ctx=ctx)
        except Exception:  # noqa: BLE001
            _note_exc("reader._build_object_forest/metafile members")
            pass
    _sharpen_metafiles(objs, mine)

    # control-button FACES: a fill-less rect grouped with a Touch command (tc=0x1010) is an
    # XP-style button whose fill is the system button DEFAULT; CtDraw paints it gray.
    _pg._fill_button_faces(objs, only)

    # invisible setpoint/slider HOTSPOTS: a runtime-animated rect over a filled value field draws
    # nothing in CtDraw's editor -- suppress the spurious default-pen outline around the genie.
    _pg._suppress_redundant_hotspots(objs, only)

    # The genie orientation gates (`%Direction% <> N`) are not folded: CtDraw's editor runs no
    # Cicode and leaves an inline genie's VISIBLE gates alone (it folds them only when it
    # composes the genie), so it draws every orientation group.


def _expand_composite(page: Page, obj: GraphicObject, po, idx: int, resolver, exts: tuple = ()) -> None:
    """Resolve a container genie's element and attach its artwork."""
    ref = reference_from_object(po)
    if ref is None or (exts and ref.ext not in exts):
        return
    resolved = resolver.resolve(ref)
    if resolved is None:
        return
    _py._expand_element(obj, resolved, resolver, 0)
    if obj.children and exts and not _has_static_art(obj.children):
        obj.children = []
        obj.raw.pop("_symbol_xform", None)
        return
    if obj.children:
        obj.raw["_resolved_ref"] = str(resolved.reference)
        # suppress the artless inline member subtree from independent drawing
        for j in page.subtree(idx):
            if j != idx:
                page.objects[j].raw["_in_composite"] = True


def _has_static_art(children: list) -> bool:
    """Does any (recursively) expanded child draw something at rest, i.e. carries art AND is not
    hidden behind a VISIBLE animation gate? A gate that rest-evaluates True does not hide; an
    unevaluable gate counts as hidden (conservative: don't expand a genie for art CtDraw would
    not show)."""
    for c in children:
        if c.raw.get("_non_drawable") or c.raw.get("_state_hidden"):
            continue
        if c.children and _has_static_art(c.children):
            return True
        if c.members:
            continue
        if c.animations.visibility is not None and c.raw.get("_rest_visible") is not True:
            continue
        if _py._member_has_art(c):
            return True
    return False


def _classify_container(obj: GraphicObject, po) -> None:
    """Reclassify a container object (Group vs Composite Genie) and lift its
    group-level visibility + genie bindings from the container record."""
    raw_props = obj.raw.get("_props", {}) or {}
    ref = obj.genie_template or obj.raw.get("_ref") or obj.raw.get("_resolved_ref") or ""
    # the genuine library/cache reference is usually a BODY lpstr (not a bag key)
    if not ref and po is not None:
        try:
            bref = reference_from_object(po)
            if bref is not None:
                ref = bref.raw or str(bref)
                obj.genie_template = ref
                obj.raw["_ref"] = ref
        except Exception:  # noqa: BLE001
            _note_exc("reader._classify_container")
            pass
    is_cache = "_CompositionCaches" in ref or ref.lower().endswith((".ctm", "(ctm)"))
    equipment = raw_props.get("Equipment") or ""
    # GENIE = what CtDraw's saver marks dwFlags 0x200: the linked element (or
    # one up its master chain) is of type GENIE. Every .ctm link root carries it,
    # no .ctl/.ctt root or free record does; inside templates it marks exactly
    # the nested genie copies (whose link lpstr is not stored). A substitution
    # bag alone does NOT make a genie (free groups / symbol sets carry
    # group-level animation bags too).
    low = ref.lower()
    template = ".ctt" in low or "(ctt)" in low or ".<ctt" in low
    ref_genie = is_cache or ".<ctm" in low or "(ctm)" in low
    dw_genie = bool(po is not None and (po.subtype_size & 0x200))
    true_genie = (dw_genie or ref_genie or bool(equipment)) and not template
    raw_bindings = genie_bindings(po) if po is not None else {}
    # The RENDER kind stays GENIE for every LIBRARY placement:
    # a .ctl symbol-placement container expands its art through the same
    # path, and the extra art-stripped .ctt chrome copies on pages
    # graft their template's logo DIB there. Only a LINK-LESS group whose
    # genie-ness came purely from a substitution bag is demoted to GROUP.
    # (template placements take this render path too: the page-template/chrome
    # graft needs it)
    genie = true_genie or bool(ref) or bool(equipment)

    # the genie's parameter bindings are the key/value pairs on its substitution
    # bags (entry trailer >= 11): TimeRemaining, MaxTime, Equipment, RefAN, ...
    bindings = raw_bindings if true_genie else {}
    # a DRAWABLE leaf that merely OWNS behaviour children (a tc=0x05 text whose child is its
    # Touch/Keyboard input; a tc=0x0F rect that IS a button face with an attached Touch)
    # keeps its drawable kind so the
    # scene paints its own art; genie metadata (is_genie/bindings) still applies for the param
    # dialogs. A real container (tc=0x08 group/genie) has a container kind and reclassifies.
    keep_kind = obj.kind in _pc._LEAF_KINDS
    if genie:
        if not keep_kind:
            obj.kind = ObjectKind.COMPOSITE_GENIE if is_cache else ObjectKind.GENIE
        obj.is_genie = true_genie
        subs = dict(bindings)
        if equipment and "Equipment" not in subs:
            subs["Equipment"] = str(equipment)
        for k, v in raw_props.items():
            if k.startswith("%"):
                subs[k] = str(v)
        obj.substitutions = subs
    elif not keep_kind:
        obj.kind = ObjectKind.GROUP
    if po is not None and po.type_code == _py._SYMBOL_SET_TC:
        # a symbol SET (CtDraw class 'Set'): its children are
        # the STATES it switches between, never group members (not ungroupable),
        # and it is a genie only when CtDraw marked it a genie instance (dwFlags
        # 0x200): a TXT_A / SET_ARR / Name /
        # Equipment bag value is not a library link
        obj.kind = ObjectKind.SYMBOL_SET
        obj.is_genie = dw_genie
    # group/genie level "hidden when" expression (applies to the whole subtree)
    vis = raw_props.get("VISIBLE")
    if vis:
        obj.animations.visibility = Visibility(expression=str(vis))


def _cicode_shows_no_symbol(po, raw_props, resolver) -> bool:
    """Does this Cicode object show CtDraw's default icon instead of a symbol?"""
    sym = str(raw_props.get("SYMBOL") or "").strip()
    if not sym or sym.lower() == "cicode.default" or (sym.startswith("<") and sym.endswith(">")):
        return True
    if resolver is None:
        return False
    try:
        ref = reference_from_object(po)
        return ref is None or resolver.resolve(ref) is None
    except Exception:  # noqa: BLE001
        _note_exc("reader._cicode_shows_no_symbol")
        return False


def _object_from(po, resolver=None, depth: int = 0) -> GraphicObject:
    raw_props = po.raw_properties()
    kind = _pc._kind_for(po.type_code, raw_props)
    if (
        kind is ObjectKind.SYMBOL_SET
        and po.type_code == 0x1009
        and _cicode_shows_no_symbol(po, raw_props, resolver)
    ):
        kind = ObjectKind.UNKNOWN  # the default display: non-drawable + f(x)
    obj = GraphicObject(kind=kind, an=-1)
    # payload an_str = the LIBRARY-LINK number (CtDraw: handle of the twin
    # record in the referenced library element, -1 = none); the builder's AN is
    # the geometry-header handle + 1 (set below)
    obj.raw["_link_an"] = po.an_id
    geo = _pc._read_geometry(po.body_raw, 0)
    geom_end = geo["end"] if geo else None
    if geo:
        # rect1 = left,top,right,bottom (int16), the bounding box for most primitives. For
        # library/symbol placements (type_code high nibble 0x1, e.g. 0x1000) rect1 collapses to a
        # 1x1 anchor POINT and the real bbox is rect2 (tools/typecode_correlate.py). Use rect2
        # when rect1 is degenerate so placed symbols render at true size, not a 1px dot.
        l, t, r, b = geo["rect1"]
        bx = (min(l, r), min(t, b), abs(r - l), abs(b - t))
        if bx[2] <= 1 and bx[3] <= 1 and geo.get("rect2"):
            l2, t2, r2, b2 = geo["rect2"]
            if abs(r2 - l2) > 1 or abs(b2 - t2) > 1:
                bx = (min(l2, r2), min(t2, b2), abs(r2 - l2), abs(b2 - t2))
        obj.geometry = Geometry(
            x=bx[0],
            y=bx[1],
            extent_x=bx[2],
            extent_y=bx[3],
            transform=_pc._transform_from_header(geo["transforms"]),
        )
        obj.raw["_handle"] = geo["handle"]
        if geo["handle"] <= 0x7FFE:
            obj.an = geo["handle"] + 1  # live AN (COM/GOTOAN/compiler)
        obj.raw["_anchor"] = geo["anchor"]
        obj.raw["_rect1"] = geo["rect1"]
        obj.raw["_rect2"] = geo["rect2"]
        if geo.get("name"):
            obj.raw["_name"] = geo["name"]
            if not obj.cls:
                obj.cls = geo["name"]
        # vertex decode: lines carry endpoints in rect1 directly (raw, unnormalised ->
        # orientation preserved); pipes/polygons carry a counted point array located by bbox
        # validation. Falls back to bbox rendering when nothing is found.
        try:
            nodes = _geom.get_object_nodes(po)
            if nodes:
                obj.geometry.nodes = [tuple(p) for p in nodes]
                if po.type_code == _geom.FREEHAND_TC:
                    # CtDraw's free-hand ('Draw') shape draws as its point run with its
                    # own style block
                    kind = obj.kind = ObjectKind.POLYGON
        except Exception:  # noqa: BLE001 -- never crash on odd bodies
            _note_exc("reader._object_from/polygon")
            pass

    # ---- placed Windows MetaFile (tc=0x09) -> render to a drawable picture -
    if po.type_code == 0x09:
        _py._attach_metafile(obj, po, geom_end)

    # ---- style (line/fill/3D colours + text face/size/colour) -------------
    try:
        decoded = po.properties()
    except Exception:  # noqa: BLE001
        _note_exc("reader._object_from")
        decoded = {}
    # (the record's style block, located once for the three readers of it below)
    once = _ps.OnceColourBlock(po)
    if po.type_code in _pc._STYLED_TYPE_CODES:
        try:
            obj.style = _ps._decode_style(po, kind, geom_end, decoded, once)
        except Exception:  # noqa: BLE001 -- never crash on unknown data
            _note_exc("reader._object_from/style")
            obj.style = Style()
        # a record whose own colours flash (the saver sets dwFlags 0x10 for them)
        if po.subtype_size & 0x12 == 0x12:
            try:
                _pg._note_flash_slots(obj, once.get())
            except Exception:  # noqa: BLE001
                _note_exc("reader._object_from/flash slots")
    if kind in (ObjectKind.TEXT, ObjectKind.NUMBER, ObjectKind.BUTTON):
        try:
            _ps._apply_text_style(obj, po, decoded, once)
        except Exception:  # noqa: BLE001
            _note_exc("reader._object_from/text style")
            pass

    _pc._apply_animations(obj, raw_props, po.type_code)
    if po.type_code == _py._SYMBOL_SET_TC:
        _pc._apply_symbol_set(obj, raw_props)

    # ---- reference label for symbol / set / group placeholders ------------
    if kind in (
        ObjectKind.SYMBOL,
        ObjectKind.SYMBOL_SET,
        ObjectKind.GROUP,
        ObjectKind.GENIE,
        ObjectKind.COMPOSITE_GENIE,
    ):
        ref = (
            raw_props.get("SYMBOL")
            or _legacy_first_symbol(po.type_code, raw_props)
            or raw_props.get("Name")
            or raw_props.get("SET_ARR")
            or raw_props.get("MenuName")
            or ""
        )
        if ref:
            obj.genie_template = str(ref)
            obj.raw["_ref"] = str(ref)

    # slot-1 'Local' header transform: only its ORTHOGONAL part matters for display (rotation/
    # mirror of placed symbols + mirrored text); H,K are placement bookkeeping already in rect1.
    # Extracted BEFORE symbol/genie resolution so _expand_element can map the element art into a
    # ROTATED placement box correctly (a 90/270 turn swaps the box dimensions vs the element).
    # Scan ALL three transform slots for the rotation/flip: a COMPOSED genie member carries its
    # orientation in the Total slot (index 2), NOT the Local slot (index 0, identity).
    try:
        rot = _geom.rotation_from_transforms(geo["transforms"]) if geo else _geom.get_rotation_transform(po)
    except Exception:  # noqa: BLE001
        _note_exc("reader._object_from")
        rot = None
    if rot is not None:
        obj.raw["_local_xform"] = rot
    # the slot-1 'Local' SCALE (A, D) of a placed symbol: a genuine anisotropic scale is how
    # CtDraw stretches the art
    try:
        (m1, L1, _o1, _n1), _s2, _s3 = _pl.read_slots_of(po)[0]
        if (
            m1
            and (m1 & 0x2 or m1 > 3)
            and abs(L1[1]) < 1e-9
            and abs(L1[2]) < 1e-9
            and L1[0] > 0
            and L1[3] > 0
        ):
            obj.raw["_slot_scale"] = (L1[0], L1[3])
    except Exception:  # swallow-ok: a record without transform slots (input records, short bodies)
        pass

    # ---- resolve placed library symbols/genies into real child objects ----
    # ONLY for genuine LEAF placements (no inlined children). A container record (Group/Genie,
    # type_code 0x08, child-count > 0) already carries its members inlined at correct page
    # coordinates; resolving its library template would double-render the art at element-local
    # (~30000 px) coordinates. The forest pass below wires those inlined children instead.
    if kind in _py._PLACEABLE_KINDS and po.class_flag == 0:
        try:
            _py._expand_symbol(obj, po, resolver, depth)
        except Exception:  # noqa: BLE001 -- never crash on unresolved/odd data
            _note_exc("reader._object_from/symbol")
            pass

    # ---- inline picture: a tc=7 "Bitmap" that is NOT a library reference carries its artwork as
    # a zlib-compressed BITMAPINFOHEADER + BGRA pixels in its body. Decode it so the renderer
    # draws the real image. Skipped when the object already resolved to children (a genuine
    # reference wins).
    if (
        po.type_code == _py._BITMAP_TYPE_CODE
        and not obj.children
        and os.environ.get("CTG_NO_EMBEDDED") != "1"
    ):
        try:
            _py._attach_embedded_bitmap(obj, po, geom_end)
        except Exception:  # noqa: BLE001 -- never crash on odd/partial data
            _note_exc("reader._object_from/embedded bitmap")
            pass

    # CtDraw's design-time look of a trend (its 8 pen colours, samples: the
    # class section, format.legacy) and a web content object (its URL)
    if po.type_code == 0x1003:
        try:
            obj.raw["_trend"] = _legacy.trend_info(po)
        except Exception:  # noqa: BLE001 -- the default pens are drawn then
            _note_exc("reader._object_from/trend")
            pass
    elif po.type_code == 0x13:
        obj.raw["_web_url"] = raw_props.get("URL.NonGenie", "") or ""
    elif po.type_code == 0x0D:
        # the control's CLSID (its OLE storage's root entry): which control
        # it is, whatever the object is called
        try:
            obj.raw["_ole_clsid"] = ole_clsid(po.body_raw)
            # its saved state: the canvas shows the control's own picture of it
            obj.raw["_ole_state"] = _ole_storage(po) or b""
        except Exception:  # noqa: BLE001
            _note_exc("reader._object_from")
            pass

    # preserve everything for lossless round-trip / inspection
    obj.raw["_type_code"] = po.type_code
    obj.raw["_class_flag"] = po.class_flag
    obj.raw["_dwflags"] = po.subtype_size
    obj.raw["_props"] = raw_props
    # flag non-drawable behaviour/anchor objects so the renderer skips them (except a
    # SYMBOL-displaying AdvancedAnimation, drawable -- see _kind_for)
    if po.type_code == 0x05 and _pg._text_needs_frame(po):
        obj.raw["_text_frame"] = True
    if po.type_code in (0x1004, 0x1005) and kind is ObjectKind.NUMBER:
        try:
            _apply_legacy_value_box(obj, po, raw_props)
        except Exception:  # noqa: BLE001
            _note_exc("reader._object_from/legacy value box")
    if (
        po.type_code in _pc._NON_DRAWABLE_TYPE_CODES
        and not (po.type_code == 0x1009 and kind is ObjectKind.SYMBOL_SET)
        and not (po.type_code == 0x1005 and kind is ObjectKind.NUMBER)
        and not (po.type_code == 0x09 and obj.raw.get("_embedded_bmp"))
        and not (po.type_code == _geom.FREEHAND_TC and obj.geometry.nodes)
    ):
        # a tc=0x09 MetaFile whose WMF rendered IS drawable; an
        # art-stripped placeholder (no WMF -> no _embedded_bmp) stays non-drawable so its genie
        # expands from its library element instead.
        obj.raw["_non_drawable"] = True
        # ... but CtDraw shows its class icon at design time ('f(x)' for a Cicode object,
        # the keyboard / colour-animation badges -- render/ctres.py)
        ICON_FOR_TYPE = _pc._class_icons()
        if po.type_code in ICON_FOR_TYPE:
            obj.raw["_class_icon"] = ICON_FOR_TYPE[po.type_code]
    return obj


if __name__ == "__main__":
    import sys

    p = read_page(sys.argv[1])
    print(
        "page:",
        p.name,
        "| %dx%d" % (p.appearance.width, p.appearance.height),
        "| objects:",
        len(p.objects),
        "| thumb:",
        len(p.thumbnail_bmp),
        "bytes",
    )
    from collections import Counter

    kinds = Counter(o.kind.value for o in p.objects)
    print("kinds:", dict(kinds))
    for o in p.objects[:8]:
        dyn = [
            n
            for n in ("visibility", "movement", "fill_colour", "display_value", "touch", "keyboard")
            if getattr(o.animations, n)
        ]
        print(
            "  #%d %-10s xy=(%d,%d) ext=(%d,%d) dyn=%s"
            % (o.an, o.kind.value, o.geometry.x, o.geometry.y, o.geometry.extent_x, o.geometry.extent_y, dyn)
        )


def _clear_art_twins() -> None:
    with _py._ART_TWINS_LOCK:
        _py._ART_TWINS_CACHE.clear()


def _clear_tag_formats() -> None:
    with _TAG_FORMAT_LOCK:
        _TAG_FORMAT_CACHE.clear()


_caches.register(
    "reader.art_twins", _py._ART_TWINS_CACHE, clear=_clear_art_twins, on=("libraries", "refresh", "tests")
)

_caches.register(
    "reader.tag_formats", _TAG_FORMAT_CACHE, clear=_clear_tag_formats, on=("libraries", "refresh", "tests")
)

_caches.register("reader.fresh_records", _FRESH_MEMO, clear=clear_fresh_memo, on=("refresh", "tests"))

_caches.register("reader.catalog", _pc._catalog, on=())

_caches.register("reader.key_family", _pc._key_family, on=())

_caches.register("reader.visibility_keys", _pc._visibility_keys, on=())

_caches.register("reader.typecode_kind", _pc._typecode_kind, on=())


# -- the reader's entry points for the simulator's drawing (render/sim_*) ---------------------
#: expand a resolved library element into ``obj.children`` (as the reader places a symbol)
expand_element = _py._expand_element
#: the library resolver for a page's path (its include chain)
build_resolver = _py._build_resolver
#: set the per-thread page context (tag formats, language) a text display reads
set_page_context = _set_page_context


# (a project's named fonts, fonts.dbf)
_caches.register("reader.named_fonts", _NAMED_FONTS, on=("libraries", "refresh", "tests"))
