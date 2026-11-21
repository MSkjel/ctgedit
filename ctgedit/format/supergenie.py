"""Super genies: what a genie attaches, the super genie page, Update Pages and the
?association? tokens."""

from __future__ import annotations

import collections
import os
import re
from typing import Iterable, List, Optional, Sequence, Tuple

from .. import cicode_names as _cn
from . import genie_place as GP
from . import geometry as G
from . import links
from . import template_place as TP
from .page import PageObject

SUPER = "@SUPER"
ROOT_FLAGS = 0x1 | 0x4  # LINK + NOSELECT (a locked, linked root)
GENIE_BIT = 0x200  # the genie-instance bit of the page root
GROUP_TC = 0x08
#: the project CtDraw never instantiates a super genie page into
NO_INSTANCE_PROJECT = "Include"
#: CtDraw's association types ('?DIGITAL 2?')
TYPE_WORDS = ("DIGITAL", "LONG", "INT", "OBJECT", "REAL", "STRING")


# --------------------------------------------------------------- names
def is_super_element(element: str) -> bool:
    """An element name of a super genie ('<name>@SUPER')."""
    return SUPER.lower() in (element or "").lower()


def page_name(element: str) -> str:
    """The page a super genie element instantiates: the element name up to
    its first '@' (CtDraw strtok '@' on the <= 31-byte name)."""
    return (element or "")[:31].split("@", 1)[0]


def element_name(name: str) -> str:
    """'<name>@SUPER' for a super genie name given with or without it."""
    name = (name or "").strip()
    return name if is_super_element(name) else name + SUPER


def link_text(library: str, element: str) -> str:
    """The page root's link: '<lib>.<name>@SUPER.<ctm'."""
    return "%s.%s.<ctm" % (library, element_name(element))


def attachment_targets(entries: Sequence[Tuple[bytes, str]]) -> List[Tuple[str, str]]:
    """(library, '<name>@SUPER') of each attached super genie of a genie
    element's link table, in stored order (a '\\\\label' entry is not one)."""
    out = []
    for _g, nm in entries:
        base = links.strip_stamp(nm)
        if base.startswith("\\") or SUPER.lower() not in base.lower():
            continue
        # '<lib>.<name>@SUPER' -- never a '.<ctm' page self-reference; the library is all
        # before the LAST dot
        if base.lower().endswith(".<ctm"):
            base = base[:-5]
        lib, _, elem = base.rpartition(".")
        if lib and elem:
            out.append((lib, elem))
    return out


def reference(library: str, element: str):
    from .symbols import SymbolReference

    return SymbolReference(library, element_name(element), "ctm", "genie")


# ------------------------------------------------------------ page scan
def page_roots(objs: Sequence[PageObject]) -> List[Tuple[int, int, object]]:
    """(start, end, SymbolReference) of every top-level record that makes the
    page a super genie page (a locked tc 0x08 group linked to
    an '@SUPER' element)."""
    from .symbols import reference_from_object

    out = []
    i = 0
    while i < len(objs):
        end = TP.subtree_end(objs, i)
        po = objs[i]
        if po.type_code == GROUP_TC and po.raw_payload is None and po.subtype_size & ROOT_FLAGS == ROOT_FLAGS:
            try:
                ref = reference_from_object(po)
            except Exception:  # noqa: BLE001
                ref = None
            if ref is not None and ref.ext == "ctm" and is_super_element(ref.element):
                out.append((i, end, ref))
        i = end
    return out


def page_link(objs: Sequence[PageObject]) -> Optional[Tuple[str, str]]:
    """(library, element) the page instantiates, or None for an ordinary page."""
    roots = page_roots(objs)
    if not roots:
        return None
    ref = roots[0][2]
    return ref.library, ref.element


def load(lib_path: str, element: str) -> TP.Template:
    """The super genie element as the page composition reads it (its stored
    records, page size and trailer -- the template loader)."""
    return TP.load_template(lib_path, element)


def resolve(resolver, library: str, element: str) -> Optional[TP.Template]:
    return TP.resolve_template(resolver, reference(library, element))


# -------------------------------------------------------------- compose
def _with_genie_bit(recs: List[PageObject]) -> List[PageObject]:
    if not recs:
        return recs
    r = GP.decode(recs[0])
    if not r.dw & GENIE_BIT:
        r.dw |= GENIE_BIT
        recs = [GP.to_po(GP.encode(r))] + list(recs[1:])
    return recs


def compose(sg: TP.Template, link: str, **kw) -> List[PageObject]:
    """The super genie page's record forest: the template composition of the
    element with the root's genie-instance bit (live dwFlags 0x20d)."""
    return _with_genie_bit(TP.compose_prefix(sg, link_text=link, **kw))


def link_entries(sg: TP.Template, link: str, records) -> List[Tuple[bytes, str]]:
    entries = [TP.link_entry(sg, link)]
    TP._add_entries(entries, GP.label_entries(records))
    return entries


def new_page_bytes(sg: TP.Template, link: str, *, stamp: Optional[int] = None, background=None) -> bytes:
    """The .ctg CtDraw creates from a super genie."""
    bg, props = TP.template_page_properties(sg)
    recs = compose(sg, link)
    return TP.new_page_bytes(
        sg.width,
        sg.height,
        background=bg or background,
        page_properties=props,
        associations=TP.template_associations(sg),
        stamp=stamp,
        objects=recs,
        link_entries=link_entries(sg, link, recs),
    )


def is_stale(page_block, sg: TP.Template, link: str) -> bool:
    return TP.is_stale(page_block, sg, link)


def recompose(objs: Sequence[PageObject], resolver, *, page_name: str = "") -> dict:
    """Update Pages for a super genie page."""
    objs = list(objs)
    roots = page_roots(objs)
    out, pos = [], 0
    entry = sg0 = link0 = None
    for a, b, ref in roots:
        sg = TP.resolve_template(resolver, ref)
        if sg is None:
            continue
        out.extend(objs[pos:a])
        old = objs[a:b]
        outside = {G.get_handle(po) for j, po in enumerate(objs) if not (a <= j < b)}
        vals, vov = TP.root_values(old[0])
        link = GP.decode(old[0]).link
        new = compose(
            sg,
            link,
            local=TP.root_local(old[0]),
            root_values=vals,
            root_values_ov=vov,
            root_an=old[0].an_str,
            root_handle=G.get_handle(old[0]),
            old_prefix=old,
            used_outside=outside,
            page_name=page_name,
        )
        out.extend(TP.merge_onto(old, new))
        pos = b
        if entry is None:
            entry, sg0, link0 = TP.link_entry(sg, link), sg, link
    if sg0 is None:
        return dict(objects=None, entry=None, template=None, link=None)
    out.extend(objs[pos:])
    return dict(objects=out, entry=entry, template=sg0, link=link0)


def replace_link_entry(link_raw: bytes, link_count: int, area: str, link: str, entry) -> Tuple[bytes, int]:
    """The page link table with the super genie's entry replaced in place
    (or put first when the page lost it)."""
    t = links.parse_link_table(link_raw) if link_count else None
    area0, ents = t if t is not None else (area or "    ", [])
    ents = list(ents)
    base = links.strip_stamp(link).lower()
    for i, (_g, nm) in enumerate(ents):
        if links.strip_stamp(nm).lower() == base:
            ents[i] = entry
            break
    else:
        ents.insert(0, entry)
    return links.serialize_link_table(area0, ents)


def element_lists(sg: TP.Template) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """(environment, associations) of super genie element ``sg`` as the page
    load compares them: its page properties not starting with '!' (record A
    without CtDraw's '@<...>' marker keys) and its associations (record B)."""
    _bg, props = TP.template_page_properties(sg)
    return list(props), list(TP.template_associations(sg))


def _same_list(a, b) -> bool:
    """the same number of entries, and every key of a is in
    ``b`` with the same value (with case)."""
    if len(a) != len(b):
        return False
    look = {k.lower(): v for k, v in b}
    return all(look.get(k.lower()) == v for k, v in a)


def sync_lists(
    list1: Sequence[Tuple[str, str]], list2: Sequence[Tuple[str, str]], sg: TP.Template
) -> Tuple[Optional[list], Optional[list]]:
    """New association lists for a page created from a super genie."""
    env_el, assoc_el = element_lists(sg)
    list1 = list(list1)
    env_page = [(k, v) for k, v in list1 if not k.startswith("!")]
    new1 = None
    if not _same_list(env_page, env_el):
        new1 = [(k, v) for k, v in list1 if k.startswith("!")] + env_el
    new2 = None if _same_list(list(list2), assoc_el) else assoc_el
    return new1, new2


# ------------------------------------------------------ associations in use
_TOKEN = re.compile(r"\?\s*(?:(?:%s)\s+)?(%s+)\s*\?" % ("|".join(TYPE_WORDS), _cn.PART), re.IGNORECASE)


def _without_strings(text: str) -> str:
    """``text`` with every Cicode string literal ("...", '^' escapes the next
    character) blanked: CtDraw takes whole tokens from the Cicode
    lexer, so a '?name?' inside a string is part of a STRING token."""
    if '"' not in text:
        return text
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch != '"':
            out.append(ch)
            i += 1
            continue
        i += 1
        while i < n and text[i] != '"':
            i += 2 if text[i] == "^" else 1
        i += 1  # the closing quote
        out.append(" ")
    return "".join(out)


def association_tokens(text: str) -> List[str]:
    """Association names of the '?name?' tokens in a value."""
    if not text or "?" not in text:
        return []
    text = _without_strings(text)
    # tokens do not overlap: '?a?b?c?' is ?a?, b, ?c? (the Cicode tokenizer);
    # a number token is listed as the number ('?INT 02?' -> '2')
    return [str(int(n)) if n.isdigit() else n for n in (m.group(1) for m in _TOKEN.finditer(text))]


def used_associations(
    objects: Iterable[PageObject], extra: Iterable[str] = ()
) -> "collections.OrderedDict[str, str]":
    """{lower name: name as first written} of every association the page's
    objects (every bag value, any level) and ``extra`` strings (the page
    keyboard commands) use, in first-use order."""
    out: "collections.OrderedDict[str, str]" = collections.OrderedDict()

    def add(v):
        for name in association_tokens(v):
            out.setdefault(name.lower(), name)

    for po in objects:
        for bag in getattr(po, "bags", None) or ():
            for _k, v in bag.pairs:
                if isinstance(v, str) and "?" in v:
                    add(v)
    for v in extra:
        if isinstance(v, str):
            add(v)
    return out


# ------------------------------------------------------ page existence
def page_exists(project_dir: str, name: str) -> bool:
    """A page ``name`` of project ``project_dir``: its pages.DBF row AND its
    .ctg (the record, then _access on the file)."""
    if not os.path.exists(os.path.join(project_dir, name + ".ctg")):
        return False
    from .projects import read_dbf_rows

    for n in ("pages.DBF", "pages.dbf"):
        p = os.path.join(project_dir, n)
        if os.path.exists(p):
            low = name.strip().lower()[:64]
            return any((r.get("NAME") or "").strip().lower() == low for r in read_dbf_rows(p))
    return False


def page_in_chain(project_dir: str, name: str) -> Optional[str]:
    """The project folder of the include chain (``project_dir`` first) that
    already has page ``name``, else None."""
    from .projects import include_search_dirs

    dirs = [project_dir]
    try:
        dirs += include_search_dirs(project_dir)
    except Exception:  # noqa: BLE001 -- an unreadable include.dbf: the project only
        pass
    for d in dirs:
        if page_exists(d, name):
            return d
    return None
