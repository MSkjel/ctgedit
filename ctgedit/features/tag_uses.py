"""Where a page uses a tag: every expression and command field that names it, and whether
the field reads it or writes it (the Tags panel's On This Page list and the canvas marks).

A command writes a tag it assigns (``Tag = 1``), toggles (``Toggle(Tag)``) or names to
TagWrite; a slider reads and writes its tag; everything else reads. Names are matched as the
compiler reads them (case does not count); ``Pump1.Value`` is a use of ``Pump1``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from .. import cicode_lex as L
from ..format import codes as _codes
from . import page_checks as PC

READS, WRITES, BOTH = "reads", "writes", "reads and writes"

#: the functions that write the tag they are given (as a name or as a string)
WRITE_FUNCTIONS = frozenset(("toggle", "tagwrite", "tagwriteex", "tagwriteintarray", "tagwriteeventque"))
#: the functions that take a tag's name as a string (the first argument)
NAME_FUNCTIONS = WRITE_FUNCTIONS | frozenset(
    ("tagread", "tagreadex", "taggetproperty", "tagsubscribe", "tagsetoverride", "tagresolve", "taginfo")
)
SLIDER_KEYS = frozenset(("MVXSL", "MVYSL", "ROTSL"))
#: the keywords a statement starts after
_STATEMENT_WORDS = frozenset(("then", "else", "do"))


@dataclass(frozen=True)
class TagUse:
    """One field that uses a tag: its object (a touch or keyboard command's object for one of
    those), the field's key and label, reads or writes, and the field's text."""

    index: int
    key: str
    label: str
    access: str
    text: str


def _names(text: str, command: bool) -> List[Tuple[str, bool]]:
    """(name, written) of each tag name ``text`` uses."""
    toks = [t for t in L.tokenize(text) if t.kind != L.COMMENT]
    out: List[Tuple[str, bool]] = []
    for k, t in enumerate(toks):
        word = text[t.start : t.end]
        prev = toks[k - 1] if k > 0 else None
        nxt = toks[k + 1] if k + 1 < len(toks) else None
        if t.kind == L.NAME:
            written = False
            if command and nxt is not None and nxt.kind == L.OPERATOR and text[nxt.start : nxt.end] == "=":
                start = prev is None or (
                    (prev.kind == L.PUNCT and text[prev.start : prev.end] == ";")
                    or (prev.kind == L.KEYWORD and text[prev.start : prev.end].lower() in _STATEMENT_WORDS)
                )
                written = start
            call = _call_of(text, toks, k)
            if call in WRITE_FUNCTIONS:
                written = True
            out.append((word, written))
        elif t.kind == L.STRING:
            call = _call_of(text, toks, k)
            if call in NAME_FUNCTIONS and prev is not None and text[prev.start : prev.end] == "(":
                inner = word[1:-1] if len(word) >= 2 and word.endswith('"') else word[1:]
                if inner and '"' not in inner:
                    out.append((inner, call in WRITE_FUNCTIONS))
    return out


def _call_of(text: str, toks, k: int) -> str:
    """The function (lower case) whose first argument token ``k`` is, or ''."""
    if k >= 2 and text[toks[k - 1].start : toks[k - 1].end] == "(" and toks[k - 2].kind == L.FUNCTION:
        return text[toks[k - 2].start : toks[k - 2].end].lower()
    return ""


class PageTagUses:
    """The tag uses of one document, indexed by lower-case name (and each dotted prefix)."""

    def __init__(self, doc):
        self.doc = doc
        self._index: Dict[str, List[TagUse]] = {}
        self._build()

    def _build(self) -> None:
        doc = self.doc
        objs = doc.page.objects
        recs = doc.parsed.page.objects
        for f in PC.expression_fields(doc):
            target = f.index
            if recs[f.index].type_code in _codes.INPUT_TCS and objs[f.index].parent is not None:
                target = objs[f.index].parent
            up = f.key.upper()
            seen = {}
            for name, written in _names(f.text, f.kind == "command"):
                low = name.lower()
                seen[low] = seen.get(low, False) or written
            for low, written in seen.items():
                access = BOTH if up in SLIDER_KEYS else WRITES if written else READS
                what = f.label.rsplit(" (", 1)[0] if f.label.endswith(")") else f.label
                label = "%s: %s" % (PC.object_label(doc, target), what[:1].lower() + what[1:])
                use = TagUse(target, f.key, label, access, f.text)
                keys = {low}
                parts = low.split(".")
                for n in range(1, len(parts)):
                    keys.add(".".join(parts[:n]))
                for key in keys:
                    self._index.setdefault(key, []).append(use)

    def uses(self, tag: str) -> List[TagUse]:
        """The fields that use ``tag``, in page order."""
        return list(self._index.get(str(tag or "").strip().lower(), ()))

    def names(self) -> List[str]:
        return list(self._index)


def count_text(uses: List[TagUse]) -> str:
    """'3 reads, 1 write' of ``uses``."""
    r = sum(1 for u in uses if u.access in (READS, BOTH))
    w = sum(1 for u in uses if u.access in (WRITES, BOTH))
    parts = []
    if r:
        parts.append("%d read%s" % (r, "" if r == 1 else "s"))
    if w:
        parts.append("%d write%s" % (w, "" if w == 1 else "s"))
    return ", ".join(parts) if parts else "not used on this page"
