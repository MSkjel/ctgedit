"""The built-in user manual."""

from __future__ import annotations

import functools
import html
import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, List, NamedTuple, Optional, Tuple

from ..format.cnum import is_digits

HELP_DIR = os.path.dirname(os.path.abspath(__file__))
PAGES_DIR = os.path.join(HELP_DIR, "pages")
IMAGES_DIR = os.path.join(HELP_DIR, "images")
TOC_FILE = os.path.join(HELP_DIR, "toc.json")

#: the AVEVA Plant SCADA help installed with the product (Author-it pages): aveva_docs(),
#: read at each call (infra/install.docs_dir; None with no install)
from ..infra.paths import aveva_docs  # noqa: E402,F401  (the help reads it from here too)

AVEVA_SERVER = "https://localhost:28808/Plant%20SCADA/content/en/%s.html"

#: generated topics (id -> (title, html provider)); the editor registers the
#: keyboard-shortcut reference, which is built from its live menus
_GENERATED: Dict[str, Tuple[str, Callable[[], str]]] = {}


@dataclass
class TocEntry:
    id: str
    title: str
    children: List["TocEntry"]


def register_generated(topic: str, title: str, provider: Callable[[], str]) -> None:
    """A topic whose HTML body is produced at view time."""
    _GENERATED[topic] = (title, provider)


def is_generated(topic: str) -> bool:
    return topic in _GENERATED


@functools.lru_cache(maxsize=1)
def _toc_raw() -> list:
    with open(TOC_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def toc() -> List[TocEntry]:
    def build(n) -> TocEntry:
        return TocEntry(
            n["id"], n.get("title") or page_title(n["id"]), [build(k) for k in n.get("children", [])]
        )

    return [build(n) for n in _toc_raw()]


def walk_toc(entries: Optional[List[TocEntry]] = None) -> Iterator[TocEntry]:
    for e in entries if entries is not None else toc():
        yield e
        yield from walk_toc(e.children)


def topics() -> List[str]:
    """Every page on disk (file stems)."""
    if not os.path.isdir(PAGES_DIR):
        return []
    return sorted(os.path.splitext(n)[0] for n in os.listdir(PAGES_DIR) if n.endswith(".md"))


def page_path(topic: str) -> Optional[str]:
    p = os.path.join(PAGES_DIR, topic + ".md")
    return p if os.path.isfile(p) else None


def exists(topic: str) -> bool:
    return topic in _GENERATED or page_path(topic) is not None


@functools.lru_cache(maxsize=512)
def _read(path: str, mtime: float) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def page_markdown(topic: str) -> str:
    p = page_path(topic)
    if p is None:
        return ""
    return _read(p, os.path.getmtime(p))


def page_title(topic: str) -> str:
    if topic in _GENERATED:
        return _GENERATED[topic][0]
    for line in page_markdown(topic).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return topic.replace("-", " ").capitalize()


def split_link(target: str) -> Tuple[str, str]:
    """'page.md#anchor' -> ('page', 'anchor'); '#anchor' -> ('', 'anchor')."""
    path, _, anchor = target.partition("#")
    if path.endswith(".md"):
        path = path[:-3]
    return os.path.basename(path), anchor


def slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", s)


def aveva_target(topic_id: str) -> str:
    """URL of an AVEVA help topic: the installed file when present (works
    without the help server), else the local help server."""
    docs = aveva_docs()
    f = os.path.join(docs, "%s.html" % topic_id) if docs else ""
    if f and os.path.isfile(f):
        return "file:///" + f.replace("\\", "/")
    return AVEVA_SERVER.replace("%s", str(topic_id))  # (its %20 is no format)


def aveva_available() -> bool:
    docs = aveva_docs()
    return bool(docs) and os.path.isdir(docs)


# ---------------------------------------------------------------- Markdown --
_INLINE = [
    (re.compile(r"`([^`]+)`"), lambda m: "<code>%s</code>" % m.group(1)),
    (re.compile(r"\*\*(.+?)\*\*"), lambda m: "<b>%s</b>" % m.group(1)),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), lambda m: "<i>%s</i>" % m.group(1)),
]
_LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")
_KEY = re.compile(r"\[\[([^\]]+)\]\]")  # [[Ctrl+S]] -> key cap
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_LIMIT = re.compile(r"<!--\s*limit:\s*([A-Za-z0-9_-]+)\s*-->")
#: call-out labels -> their CSS class
_CALLOUTS = {
    "Tip": "tip",
    "Note": "note",
    "Warning": "warning",
    "Important": "important",
    "Coming from the Graphics Builder": "gb",
}
_CALLOUT = re.compile(r"^\*\*(%s):\*\*\s*(.*)$" % "|".join(re.escape(k) for k in _CALLOUTS), re.S)


def strip_comments(md: str) -> str:
    """The page without its HTML comments (limit markers, notes to authors)."""
    return _COMMENT.sub("", md)


def limit_markers(topic: str) -> List[str]:
    """The work packages a page's ``<!-- limit:WPxx -->`` markers name, in
    page order (one per known-limit statement)."""
    return _LIMIT.findall(page_markdown(topic))


def _inline(text: str) -> str:
    """Inline Markdown -> HTML (the text is escaped first; code spans are
    protected from the other rules)."""
    out, pos = [], 0
    for m in _LINK.finditer(text):
        out.append(_inline_plain(text[pos : m.start()]))
        bang, label, target = m.groups()
        if bang:
            src = target
            if not re.match(r"^[a-z]+:", src):
                src = "file:///" + os.path.join(IMAGES_DIR, src).replace("\\", "/")
            out.append('<img src="%s" alt="%s">' % (html.escape(src), html.escape(label)))
        else:
            out.append('<a href="%s">%s</a>' % (html.escape(target), _inline_plain(label)))
        pos = m.end()
    out.append(_inline_plain(text[pos:]))
    return "".join(out)


def _inline_plain(text: str) -> str:
    parts = re.split(r"(`[^`]+`)", text)
    res = []
    for p in parts:
        if p.startswith("`") and p.endswith("`") and len(p) > 1:
            res.append("<code>%s</code>" % html.escape(p[1:-1]))
            continue
        s = html.escape(p, quote=False)
        s = _KEY.sub(lambda m: "<span class='key'>%s</span>" % m.group(1), s)
        for rx, fn in _INLINE[1:]:
            s = rx.sub(fn, s)
        res.append(s)
    return "".join(res)


def markdown_to_html(md: str) -> str:
    """The Markdown subset the manual uses: ATX headings, paragraphs, bullet
    and numbered lists (one nesting level by indentation), pipe tables,
    fenced code, block quotes / call-outs, rules, images, links, bold,
    italic, code, [[key caps]]."""
    lines = strip_comments(md.replace("\r\n", "\n")).split("\n")
    out: List[str] = []
    i = 0
    para: List[str] = []

    def flush_para():
        if para:
            out.append("<p>%s</p>" % _inline(" ".join(s.strip() for s in para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            flush_para()
            i += 1
            continue
        if s.startswith("```"):
            flush_para()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append("<pre>%s</pre>" % html.escape("\n".join(code)))
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            flush_para()
            level, text = len(m.group(1)), m.group(2).strip()
            out.append('<a name="%s"></a><h%d>%s</h%d>' % (slug(text), level, _inline(text), level))
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,})$", s):
            flush_para()
            out.append("<hr>")
            i += 1
            continue
        if s.startswith(">"):
            flush_para()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            # a '>' line of its own separates the call-out's paragraphs
            paras, cur = [], []
            for q in quote:
                if q:
                    cur.append(q)
                elif cur:
                    paras.append(" ".join(cur))
                    cur = []
            if cur:
                paras.append(" ".join(cur))
            body = "\n\n".join(paras)
            kind = "note"
            cm = _CALLOUT.match(body)
            if cm:
                kind = _CALLOUTS[cm.group(1)]
                rest = cm.group(2).split("\n\n")
                body = "<b>%s:</b> %s" % (cm.group(1), "<br><br>".join(_inline(p) for p in rest))
            else:
                body = "<br><br>".join(_inline(p) for p in body.split("\n\n"))
            out.append(
                '<table class="callout %s" width="100%%" cellpadding="8"><tr><td>%s</td></tr></table>'
                % (kind, body)
            )
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip()):
            flush_para()
            header = [c.strip() for c in s.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t = ['<table class="grid" cellspacing="0" cellpadding="5" width="100%"><tr>']
            t += ["<th>%s</th>" % _inline(c) for c in header]
            t.append("</tr>")
            for r in rows:
                t.append("<tr>" + "".join("<td>%s</td>" % _inline(c) for c in r) + "</tr>")
            t.append("</table>")
            out.append("".join(t))
            continue
        lm = re.match(r"^(\s*)([-*]|\d+[.)])\s+(.*)$", line)
        if lm:
            flush_para()
            items: List[Tuple[int, bool, str]] = []
            while i < len(lines):
                lm = re.match(r"^(\s*)([-*]|\d+[.)])\s+(.*)$", lines[i])
                if lm:
                    items.append(
                        (len(lm.group(1).expandtabs(4)) // 2, is_digits(lm.group(2)[0]), lm.group(3))
                    )
                    i += 1
                elif lines[i].strip() and lines[i].startswith("  ") and items:
                    d, o, t = items[-1]
                    items[-1] = (d, o, t + " " + lines[i].strip())
                    i += 1
                else:
                    break
            out.append(_render_list(items))
            continue
        para.append(line)
        i += 1
    flush_para()
    return "\n".join(out)


def _render_list(items: List[Tuple[int, bool, str]]) -> str:
    out: List[str] = []
    stack: List[str] = []
    for depth, ordered, text in items:
        depth = min(depth, len(stack))
        while len(stack) > depth + 1:
            out.append("</%s>" % stack.pop())
        if len(stack) == depth:
            tag = "ol" if ordered else "ul"
            out.append("<%s>" % tag)
            stack.append(tag)
        out.append("<li>%s</li>" % _inline(text))
    while stack:
        out.append("</%s>" % stack.pop())
    return "".join(out)


def links(topic: str) -> List[str]:
    """Every link target in a page (for the link checker)."""
    return [m.group(3) for m in _LINK.finditer(strip_comments(page_markdown(topic))) if not m.group(1)]


def _headings(md: str) -> List[Tuple[int, str]]:
    """(level, text) of every heading outside fenced code blocks."""
    out, fenced = [], False
    for line in strip_comments(md).splitlines():
        s = line.strip()
        if s.startswith("```"):
            fenced = not fenced
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m and not fenced:
            out.append((len(m.group(1)), m.group(2).strip()))
    return out


def anchors(topic: str) -> List[str]:
    return [slug(text) for _level, text in _headings(page_markdown(topic))]


def page_html(topic: str) -> str:
    """The body HTML of a topic (generated or Markdown)."""
    if topic in _GENERATED:
        return _GENERATED[topic][1]()
    md = page_markdown(topic)
    if not md:
        return "<h1>Page not found</h1><p>There is no help page <code>%s</code>.</p>" % html.escape(topic)
    return markdown_to_html(md)


# ------------------------------------------------------------------ search --
def _plain(md: str) -> str:
    s = _LINK.sub(lambda m: m.group(2), strip_comments(md))
    s = re.sub(r"[`*#>|]", " ", s)
    return re.sub(r"\s+", " ", s)


def search(query: str, limit: int = 40, regex: bool = False) -> List[Tuple[str, str, str]]:
    """(topic, title, snippet), ranked. Every query word must occur."""
    if regex:
        return _search_regex(query, limit)
    words = [w for w in re.findall(r"\w+", query.lower()) if w]
    if not words:
        return []
    phrase = " ".join(words)
    qslug = slug(phrase)
    scored = []
    for t in topics():
        md = page_markdown(t)
        text = _plain(md).lower()
        title = page_title(t).lower()
        if not all(w in text or w in title for w in words):
            continue
        heads = " ".join(h.lower() for _lv, h in _headings(md))
        score = sum(10 * (w in title) + 4 * (w in heads) + min(text.count(w), 8) for w in words)
        if title.startswith(phrase) or t.startswith(qslug):
            score += 25
        pos = min((text.find(w) for w in words if text.find(w) >= 0), default=0)
        plain = _plain(md)
        start = max(0, pos - 60)
        snippet = ("…" if start else "") + plain[start : start + 180].strip() + "…"
        scored.append((score, t, page_title(t), snippet))
    scored.sort(key=lambda r: (-r[0], r[2]))
    return [(t, ti, sn) for _s, t, ti, sn in scored[:limit]]


def _search_regex(query: str, limit: int) -> List[Tuple[str, str, str]]:
    try:
        rx = re.compile((query or "").strip(), re.IGNORECASE)
    except re.error:
        return []
    if not (query or "").strip():
        return []
    scored = []
    for t in topics():
        md = page_markdown(t)
        plain = _plain(md)
        title = page_title(t)
        first = rx.search(plain)
        in_title = rx.search(title) is not None
        if first is None and not in_title:
            continue
        score = 10 * in_title + min(sum(1 for _m in rx.finditer(plain)), 8)
        start = max(0, (first.start() if first is not None else 0) - 60)
        snippet = ("…" if start else "") + plain[start : start + 180].strip() + "…"
        scored.append((score, t, title, snippet))
    scored.sort(key=lambda r: (-r[0], r[2]))
    return [(t, ti, sn) for _s, t, ti, sn in scored[:limit]]


# ------------------------------------------------------------- sections --
_LIST_MARK = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{2,}")


def plain_inline(text: str) -> str:
    """One line of Markdown as the reader sees it: links -> their label, images left out,
    [[Alt]] -> Alt, `code` -> code, **bold** / *italic* -> the words."""
    s = re.sub(r"!\[[^\]]*\]\([^)\s]+\)", "", text)
    s = _LINK.sub(lambda m: m.group(2), s)
    s = _KEY.sub(lambda m: m.group(1), s)
    s = re.sub(r"`([^`]+)`", lambda m: m.group(1), s)
    s = re.sub(r"\*\*(.+?)\*\*", lambda m: m.group(1), s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", lambda m: m.group(1), s)
    return s


def plain_text(md: str) -> str:
    """A piece of a page as plain text: no markup, list markers, table rules or call-out
    marks; one space between words."""
    out: List[str] = []
    para: List[str] = []

    def flush():
        # (a paragraph at a time: bold and links may run over a line break)
        if para:
            out.append(plain_inline(" ".join(para)))
            para.clear()

    fenced = False
    for line in strip_comments(md).replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.startswith("```"):
            flush()
            fenced = not fenced
            continue
        if fenced:
            out.append(s)
            continue
        if not s or re.match(r"^(-{3,}|\*{3,})$", s) or _TABLE_RULE.match(s):
            flush()
            continue
        block = bool(re.match(r"^#{1,6}\s+", s)) or bool(_LIST_MARK.match(s)) or s.startswith("|")
        if block:
            flush()
        s = re.sub(r"^#{1,6}\s+", "", s)
        while s.startswith(">"):
            s = s[1:].lstrip()
        s = _LIST_MARK.sub("", s)
        if s.startswith("|"):
            flush()
            out.append(
                plain_inline(" · ".join(c.strip() for c in s.strip().strip("|").split("|") if c.strip()))
            )
            continue
        para.append(s)
    flush()
    return re.sub(r"\s+", " ", " ".join(out)).strip()


@dataclass(frozen=True)
class Section:
    """One part of a page: its intro (``anchor`` '', ``heading`` the page title) or a ## /
    ### section (a #### heading stays in its section). ``text`` is its plain text, ``keys``
    the [[key]] names it shows."""

    topic: str
    anchor: str
    heading: str
    level: int
    text: str
    keys: Tuple[str, ...]


class SectionHit(NamedTuple):
    """A search hit: the section's topic and anchor, its heading and a snippet."""

    topic: str
    anchor: str
    heading: str
    snippet: str


@functools.lru_cache(maxsize=256)
def _sections(topic: str, mtime: float) -> Tuple[Section, ...]:
    md = page_markdown(topic)
    title = page_title(topic)
    parts: List[Tuple[int, str, List[str]]] = [(1, title, [])]
    fenced = False
    for line in strip_comments(md).replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.startswith("```"):
            fenced = not fenced
        m = None if fenced else re.match(r"^(#{1,3})\s+(.*)$", s)
        if m and len(m.group(1)) >= 2:
            parts.append((len(m.group(1)), m.group(2).strip(), []))
            continue
        if m and len(m.group(1)) == 1:
            continue  # (the page title: the intro's heading)
        parts[-1][2].append(line)
    out = []
    for k, (level, heading, lines) in enumerate(parts):
        body = "\n".join(lines)
        text = plain_text(body)
        if k == 0 and not text:
            continue
        keys = tuple(dict.fromkeys(_KEY.findall(body)))
        out.append(Section(topic, slug(heading) if k else "", plain_inline(heading), level, text, keys))
    return tuple(out)


def sections(topic: str) -> List[Section]:
    """The sections of a page (generated pages have none)."""
    p = page_path(topic)
    if p is None:
        return []
    return list(_sections(topic, os.path.getmtime(p)))


def section_of(topic: str, anchor: str) -> Optional[Section]:
    for s in sections(topic):
        if s.anchor == anchor:
            return s
    return None


def snippet(text: str, words: List[str], width: int = 150, regex=None) -> str:
    """About ``width`` characters of ``text`` around the first of ``words`` (or the first
    match of ``regex``), cut at word boundaries, with '…' where it was cut."""
    low = text.lower()
    pos = -1
    if regex is not None:
        m = regex.search(text)
        pos = m.start() if m is not None else -1
    else:
        found = [low.find(w) for w in words if low.find(w) >= 0]
        pos = min(found) if found else -1
    if pos < 0:
        pos = 0
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    if start > 0:
        sp = text.find(" ", start)
        start = sp + 1 if 0 <= sp < pos else start
    if end < len(text):
        sp = text.rfind(" ", start, end)
        end = sp if sp > max(start, pos) else end
    out = text[start:end].strip()
    return ("…" if start > 0 else "") + out + ("…" if end < len(text) else "")


def search_sections(query: str, limit: int = 40, regex: bool = False) -> List[SectionHit]:
    """The sections that hold every word of ``query`` (in their text, their heading or their
    page's title), best first: a heading that is the query, then the words in the heading,
    then in the page title, then how often they occur. ``regex``: the query is one regular
    expression found in the heading or the text, without case."""
    q = (query or "").strip()
    if not q:
        return []
    rx = None
    if regex:
        try:
            rx = re.compile(q, re.IGNORECASE)
        except re.error:
            return []
    words = [w for w in re.findall(r"\w+", q.lower()) if w]
    if not words and rx is None:
        return []
    phrase = " ".join(words)
    scored = []
    order = 0
    try:
        in_toc = [e.id for e in walk_toc()]
    except OSError, ValueError:
        in_toc = []
    for t in dict.fromkeys(in_toc + topics()):
        if t in _GENERATED or page_path(t) is None:
            continue
        title = page_title(t).lower()
        for s in sections(t):
            order += 1
            head = s.heading.lower()
            text = s.text.lower()
            if rx is not None:
                in_head = rx.search(s.heading) is not None
                hits = sum(1 for _m in rx.finditer(s.text))
                if not in_head and not hits:
                    continue
                score = 30 * in_head + min(hits, 8)
            else:
                if not all(w in text or w in head or w in title for w in words):
                    continue
                score = 0
                for w in words:
                    score += 12 * (w in head) + 4 * (w in title) + min(text.count(w), 6)
                if head == phrase:
                    score += 40
                elif head.startswith(phrase):
                    score += 25
                elif phrase in head:
                    score += 12
                if not s.anchor and (title.startswith(phrase) or t.startswith(slug(phrase))):
                    score += 20
            scored.append((-score, order, s))
    seen = set()
    out = []
    for _s, _o, s in sorted(scored, key=lambda r: (r[0], r[1])):
        key = (s.topic, s.anchor)
        if key in seen:
            continue
        seen.add(key)
        out.append(SectionHit(s.topic, s.anchor, s.heading, snippet(s.text, words, regex=rx)))
        if len(out) >= limit:
            break
    return out


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("help.toc", _toc_raw, on=())
_caches.register("help.pages", _read, on=())
_caches.register("help.sections", _sections, on=())
