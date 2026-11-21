"""Look up the AVEVA help for system-project genies and their fields."""

from __future__ import annotations

import html
import json
import os
import re
from typing import Dict, List, Optional, Tuple

_INDEX: Optional[List[Tuple[str, "re.Pattern", str]]] = None
_DESC: Dict[str, Dict[str, str]] = {}

_NAME = re.compile(r"Genie Name:\s*</span>\s*([^<]+)", re.I)
_LIB = re.compile(r"Genie Library:\s*</span>\s*([^<]+)", re.I)
_TAG = re.compile(r"<[^>]+>")


def _docs() -> Optional[str]:
    """The installed help's folder (CTG_AVEVA_HELP wins), None with no install."""
    from ..infra.paths import aveva_docs

    return aveva_docs()


def _in(docs: Optional[str], name: str) -> str:
    """``<docs>/<name>``; OSError with no help folder (read as 'no reference')."""
    if not docs:
        raise OSError("no installed help")
    return os.path.join(docs, name)


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def _pattern(name: str) -> "re.Pattern":
    """'meo_<n> / meo_<n>_r' -> one regex over the alternatives ('<...>'
    stands for any text)."""
    alts = []
    for alt in name.split("/"):
        alt = alt.strip()
        if not alt:
            continue
        parts = re.split(r"<[^>]*>", alt)
        alts.append(".+".join(re.escape(p) for p in parts))
    return re.compile("^(?:%s)$" % "|".join(alts or ["$^"]), re.I)


def _walk(node, out: List[Tuple[str, str]]) -> None:
    if isinstance(node, dict):
        title = str(node.get("name") or node.get("title") or node.get("text") or "")
        tid = node.get("id") or node.get("topicId")
        if tid and (title.endswith(" Genie") or title.startswith("Use the ")):
            out.append((str(tid), title))
        for v in node.values():
            if isinstance(v, (dict, list)):
                _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)


def _index() -> List[Tuple[str, "re.Pattern", str]]:
    """(library lower, element pattern, topic id) of every genie reference
    topic of the installed help (read once)."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    out: List[Tuple[str, "re.Pattern", str]] = []
    docs = _docs()
    try:
        with open(_in(docs, "toc.json"), encoding="utf-8") as fh:
            toc = json.load(fh)
    except OSError, ValueError:
        _INDEX = out
        return out
    topics: List[Tuple[str, str]] = []
    _walk(toc, topics)
    for tid, _title in topics:
        try:
            with open(_in(docs, "%s.html" % tid), encoding="utf-8", errors="replace") as fh:
                page = fh.read()
        except OSError:
            continue
        mn, ml = _NAME.search(page), _LIB.search(page)
        if not (mn and ml):
            continue
        out.append((html.unescape(ml.group(1)).strip().lower(), _pattern(html.unescape(mn.group(1))), tid))
    _INDEX = out
    return out


def topic_for(library: str, element: str) -> Optional[str]:
    """The AVEVA help topic describing genie ``library.element``, or None."""
    lib = (library or "").strip().lower()
    el = (element or "").strip()
    if not lib or not el:
        return None
    for tlib, pat, tid in _index():
        if tlib == lib and pat.match(el):
            return tid
    return None


#: a library topic: 'The SA_Include system project includes the "sa_filter"
#: library' / 'The Common Components Genie Library (named "sa_common")'; a
#: project topic: 'The Situational Awareness Include Project (named
#: "SA_Include")' (help 1206770 / 1206783 / 1206288)
_INCLUDES = re.compile(
    r'(?:The\s+(\S+)\s+system project\s+)?includes the\s+"([^"]+)"\s+'
    r"library",
    re.I,
)
_NAMED = re.compile(r'\(named\s+"([^"]+)"\)', re.I)
_LIBS: Optional[Tuple[Dict[Tuple[str, str], str], Dict[str, str]]] = None


def _library_index() -> Tuple[Dict[Tuple[str, str], str], Dict[str, str]]:
    """((project lower | '', library lower) -> topic of the library,
    project lower -> topic of the project) from the installed help's
    '... Genie Library' / '... Genie Libraries' / '... Project' pages (read
    once)."""
    global _LIBS
    if _LIBS is not None:
        return _LIBS
    libs: Dict[Tuple[str, str], str] = {}
    projects: Dict[str, str] = {}
    docs = _docs()
    nodes: List[Tuple[str, str]] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            title = str(node.get("name") or node.get("title") or node.get("text") or "")
            tid = node.get("id") or node.get("topicId")
            if tid and title.rstrip().endswith(("Library", "Libraries", "Project")):
                nodes.append((str(tid), title.strip()))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    try:
        with open(_in(docs, "toc.json"), encoding="utf-8") as fh:
            walk(json.load(fh))
    except OSError, ValueError:
        _LIBS = (libs, projects)
        return _LIBS
    for tid, title in nodes:
        try:
            with open(_in(docs, "%s.html" % tid), encoding="utf-8", errors="replace") as fh:
                page = _text(fh.read())
        except OSError:
            continue
        for m in _INCLUDES.finditer(page):
            proj, lib = (m.group(1) or "").lower(), m.group(2).strip().lower()
            libs.setdefault((proj, lib), tid)
            libs.setdefault(("", lib), tid)
        m = _NAMED.search(page)
        if m:
            name = m.group(1).strip().lower()
            if title.endswith(("Libraries", "Project")):
                projects.setdefault(name, tid)
            else:
                libs.setdefault(("", name), tid)
    _LIBS = (libs, projects)
    return _LIBS


def element_help(library: str, element: str, project: str = "") -> Tuple[Optional[str], str]:
    """(AVEVA topic id, 'element' | 'library' | 'project') describing library
    element ``library.element`` of ``project`` (a folder name such as
    'SA_Include'): the genie's own reference page, else its library's page,
    else its system project's page; (None, '') when the help has none."""
    tid = topic_for(library, element)
    if tid:
        return tid, "element"
    lib = (library or "").strip().lower()
    prj = (project or "").strip().lower()
    libs, projects = _library_index()
    tid = libs.get((prj, lib)) or libs.get(("", lib))
    if tid:
        return tid, "library"
    tid = projects.get(prj)
    if tid:
        return tid, "project"
    return None, ""


def norm_label(label: str) -> str:
    """A form label / parameter name as the description key: lower case,
    no trailing '?' / ':' and no blanks ('Is Default Pane?' -> 'isdefaultpane')."""
    return re.sub(r"[\s?:]+", "", (label or "").lower())


def field_descriptions(topic_id: str) -> Dict[str, str]:
    """norm_label(label) -> description of every field the topic describes
    (its Property | Description tables and its 'Label: text' list items)."""
    if not topic_id:
        return {}
    hit = _DESC.get(topic_id)
    if hit is not None:
        return hit
    out: Dict[str, str] = {}
    try:
        with open(_in(_docs(), "%s.html" % topic_id), encoding="utf-8", errors="replace") as fh:
            page = fh.read()
    except OSError:
        _DESC[topic_id] = out
        return out
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        if len(cells) < 2:
            continue
        label, desc = _text(cells[0]), _text(cells[1])
        if label and desc and len(label) <= 60:
            out.setdefault(norm_label(label), desc)
    for item in re.findall(r"<li[^>]*>(.*?)</li>", page, re.S | re.I):
        t = _text(item)
        label, sep, desc = t.partition(":")
        if sep and desc.strip() and 0 < len(label) <= 40:
            out.setdefault(norm_label(label), desc.strip())
    _DESC[topic_id] = out
    return out


from ..infra import caches as _caches  # noqa: E402

_caches.register("genie_help.descriptions", _DESC, on=("refresh", "tests"))
