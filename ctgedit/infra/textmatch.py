"""The match rule for search boxes: plain text or a regular expression."""

from __future__ import annotations

import re


class TextMatch:
    """What a search box matches, one rule for every box: plain text = a part of the text,
    without case; a regular expression (the box's '.*' toggle) = found anywhere in the text,
    without case (``^`` and ``$`` anchor it). One that does not compile matches nothing
    (``error`` says why). Call it with the texts of a row: True when any matches."""

    __slots__ = ("text", "regex", "error", "_low", "_rx")

    def __init__(self, text: str = "", regex: bool = False):
        self.text = (text or "").strip()
        self.regex = bool(regex)
        self.error = ""
        self._low = self.text.lower()
        self._rx = None
        if self.regex and self.text:
            try:
                self._rx = re.compile(self.text, re.IGNORECASE)
            except re.error as e:
                self.error = str(e)

    @property
    def empty(self) -> bool:
        return not self.text

    @property
    def ok(self) -> bool:
        return not self.error

    def __call__(self, *texts) -> bool:
        if not self.text:
            return True
        if self.error:
            return False
        if self._rx is not None:
            return any(self._rx.search(str(t or "")) for t in texts)
        return any(self._low in str(t or "").lower() for t in texts)

    def spans(self, text: str):
        """[(start, end)] of the matches in ``text`` (Find and Replace, highlights)."""
        if not self.text or self.error:
            return []
        if self._rx is not None:
            return [(m.start(), m.end()) for m in self._rx.finditer(text) if m.end() > m.start()]
        out, low, i = [], text.lower(), 0
        while True:
            i = low.find(self._low, i)
            if i < 0:
                return out
            out.append((i, i + len(self._low)))
            i += len(self._low)

    @property
    def pattern(self):
        """The compiled expression (None for plain text or one that does not compile)."""
        return self._rx


#: the field tokens of a search with fields (the Objects panel): 'name:Pump', 'tag:Mixer_',
#: 'an:581', 'kind:genie', 'lib:valve', 'is:hidden'
FIELDS = ("name", "tag", "an", "kind", "lib", "is")
#: fields whose value must equal one of the row's words (not a part of its text)
EXACT_FIELDS = ("an", "is")

_TOKEN = re.compile(r'[A-Za-z]+:"[^"]*"|"[^"]*"|\S+')


class FieldQuery:
    """A search query with field tokens, e.g. 'kind:genie tag:Mixer_ pump'."""

    __slots__ = ("text", "terms", "free")

    def __init__(self, text: str = "", regex: bool = False, fields=FIELDS):
        self.text = (text or "").strip()
        self.terms = []
        free = []
        for tok in _TOKEN.findall(self.text):
            f, sep, v = tok.partition(":")
            if sep and f.lower() in fields and v.strip('"'):
                self.terms.append((f.lower(), v.strip('"').lower()))
            else:
                free.append(tok)
        self.free = TextMatch(" ".join(free) if self.terms else self.text, regex)

    @property
    def empty(self) -> bool:
        return not self.terms and self.free.empty

    @property
    def error(self) -> str:
        return self.free.error

    @property
    def ok(self) -> bool:
        return self.free.ok

    def __call__(self, fields, *texts) -> bool:
        """``fields`` = {field: its text, or a list of its words}; ``texts`` = what the plain
        text is found in."""
        for f, v in self.terms:
            have = fields.get(f) if fields else None
            if not have:
                return False
            if f in EXACT_FIELDS:
                words = have.split() if isinstance(have, str) else have
                if not any(str(w).lower() == v for w in words):
                    return False
            elif v not in (have if isinstance(have, str) else " ".join(have)).lower():
                return False
        return self.free(*texts)

    def spans(self, text: str):
        """The plain text's matches in ``text`` (a row's bold part)."""
        return self.free.spans(text)
