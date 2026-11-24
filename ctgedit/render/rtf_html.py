"""RTF as HTML for a QTextDocument: the subset report output uses.

Paragraphs (``\\par``) become one ``<p>`` each, so block k of the document is paragraph k (the
engine scrolls a rich text box by paragraphs). Read: ``\\par``, ``\\line``, ``\\tab``,
``\\b``, ``\\i``, ``\\ul`` / ``\\ulnone``, ``\\fs``, ``\\cf`` with ``\\colortbl``, ``\\f``
with ``\\fonttbl``, ``\\plain``, ``\\pard``, ``\\ql`` / ``\\qc`` / ``\\qr`` / ``\\qj``,
``\\'hh`` (in the ``\\ansicpg`` code page), ``\\uN``, the escaped ``{ } \\`` and a few named
characters. ``{\\*...}`` groups and the font, colour, style and information groups are not
text; other control words are ignored."""

from __future__ import annotations

import html
from typing import Dict, List, Optional, Tuple

#: the groups whose text is not shown, by their first control word
_HIDDEN = frozenset(
    {
        "stylesheet",
        "info",
        "pict",
        "object",
        "header",
        "headerl",
        "headerr",
        "headerf",
        "footer",
        "footerl",
        "footerr",
        "footerf",
        "listtable",
        "listoverridetable",
        "rsidtbl",
        "xmlnstbl",
        "generator",
    }
)
_NAMED = {
    "emdash": "\u2014",
    "endash": "\u2013",
    "bullet": "\u2022",
    "lquote": "\u2018",
    "rquote": "\u2019",
    "ldblquote": "\u201c",
    "rdblquote": "\u201d",
    "line": "\u2028",
    "tab": "\t",
}
_ALIGN = {"ql": "left", "qc": "center", "qr": "right", "qj": "justify"}
#: RTF's default font size in half points (12 pt)
_DEFAULT_FS = 24


class _State:
    """A group's state: the destination and the character and paragraph format."""

    __slots__ = ("dest", "bold", "italic", "ul", "fs", "cf", "f", "align", "uc")

    def __init__(self):
        self.dest = "text"  # text | fonttbl | colortbl | skip
        self.bold = self.italic = self.ul = False
        self.fs = _DEFAULT_FS
        self.cf = 0
        self.f: Optional[int] = None
        self.align = "left"
        self.uc = 1

    def copy(self) -> "_State":
        s = _State.__new__(_State)
        for k in _State.__slots__:
            setattr(s, k, getattr(self, k))
        return s

    def plain(self) -> None:
        self.bold = self.italic = self.ul = False
        self.fs = _DEFAULT_FS
        self.cf = 0
        self.f = None


_Fmt = Tuple[bool, bool, bool, int, int, Optional[int]]


class _Reader:
    def __init__(self, rtf: str):
        self.s = rtf.split("\x00", 1)[0]
        self.fonts: Dict[int, str] = {}
        self.colours: List[Optional[str]] = []
        self.deff: Optional[int] = None
        self.codepage = "cp1252"
        self.paras: List[Tuple[str, List[Tuple[_Fmt, str]]]] = []
        self.runs: List[Tuple[_Fmt, str]] = []
        self.skip_chars = 0
        # the font table entry being read: its number and name so far
        self.font_no: Optional[int] = None
        self.font_name: List[str] = []
        self.colour = [0, 0, 0]
        self.colour_set = False

    # -- output ------------------------------------------------------------------------
    @staticmethod
    def _fmt(st: _State) -> _Fmt:
        return (st.bold, st.italic, st.ul, st.fs, st.cf, st.f)

    def text(self, st: _State, t: str) -> None:
        if st.dest == "skip" or not t:
            return
        if st.dest == "fonttbl":
            self.font_name.append(t)
            if ";" in t:
                self._end_font()
            return
        if st.dest == "colortbl":
            for ch in t:
                if ch == ";":
                    r, g, b = self.colour
                    self.colours.append(f"#{r:02x}{g:02x}{b:02x}" if self.colour_set else None)
                    self.colour = [0, 0, 0]
                    self.colour_set = False
            return
        if self.skip_chars > 0:
            n = min(self.skip_chars, len(t))
            self.skip_chars -= n
            t = t[n:]
            if not t:
                return
        fmt = self._fmt(st)
        if self.runs and self.runs[-1][0] == fmt:
            self.runs[-1] = (fmt, self.runs[-1][1] + t)
        else:
            self.runs.append((fmt, t))

    def par(self, st: _State) -> None:
        if not self.runs:
            # an empty paragraph keeps the height of its mark's font (html() gives it a space)
            self.runs.append((self._fmt(st), ""))
        self.paras.append((st.align, self.runs))
        self.runs = []

    def _end_font(self) -> None:
        name = "".join(self.font_name).split(";", 1)[0].strip()
        if self.font_no is not None and name:
            self.fonts[self.font_no] = name
        self.font_name = []

    # -- reading -----------------------------------------------------------------------
    def read(self) -> None:
        s, n, i = self.s, len(self.s), 0
        st = _State()
        stack: List[_State] = []
        while i < n:
            ch = s[i]
            if ch == "{":
                stack.append(st)
                st = st.copy()
                i += 1
                if s.startswith("\\*", i):
                    st.dest = "skip"
                elif s.startswith("\\", i):
                    word = _word(s, i + 1)
                    if word in _HIDDEN:
                        st.dest = "skip"
                    elif word in ("fonttbl", "colortbl") and st.dest == "text":
                        st.dest = word
                if st.dest == "fonttbl":
                    self.font_no, self.font_name = None, []
                continue
            if ch == "}":
                if st.dest == "fonttbl" and self.font_name:
                    self._end_font()
                st = stack.pop() if stack else st
                i += 1
                continue
            if ch in "\r\n":
                i += 1
                continue
            if ch != "\\":
                j = i
                while j < n and s[j] not in "\\{}\r\n":
                    j += 1
                self.text(st, s[i:j])
                i = j
                continue
            i += 1
            if i >= n:
                break
            c = s[i]
            if not ("a" <= c <= "z" or "A" <= c <= "Z"):
                i += 1
                if c == "'":
                    self.text(st, self._byte(s[i : i + 2]))
                    i += 2
                elif c in "{}\\":
                    self.text(st, c)
                elif c == "~":
                    self.text(st, "\u00a0")
                elif c == "_":
                    self.text(st, "-")
                elif c in "\r\n" and st.dest == "text":
                    self.par(st)
                continue
            word = _word(s, i)
            i += len(word)
            a = i
            if i < n and s[i] == "-":
                i += 1
            while i < n and s[i].isdigit():
                i += 1
            arg = int(s[a:i]) if i > a and s[i - 1] != "-" else None
            if i < n and s[i] == " ":
                i += 1
            self.word(st, word, arg)
        if self.runs:
            self.par(st)

    def _byte(self, hh: str) -> str:
        try:
            return bytes([int(hh, 16)]).decode(self.codepage, errors="replace")
        except ValueError, LookupError:
            return ""

    def word(self, st: _State, w: str, arg: Optional[int]) -> None:
        if st.dest == "fonttbl":
            if w == "f" and arg is not None:
                self.font_no = arg
            return
        if st.dest == "colortbl":
            if w in ("red", "green", "blue") and arg is not None:
                self.colour[("red", "green", "blue").index(w)] = max(0, min(255, arg))
                self.colour_set = True
            return
        if st.dest == "skip":
            return
        on = arg is None or arg != 0
        if w == "par":
            self.par(st)
        elif w in _NAMED:
            self.text(st, _NAMED[w])
        elif w == "b":
            st.bold = on
        elif w == "i":
            st.italic = on
        elif w in ("ulnone", "ul0"):
            st.ul = False
        elif w.startswith("ul") and w not in ("ulc",):
            st.ul = on
        elif w == "fs" and arg is not None and arg > 0:
            st.fs = arg
        elif w == "cf" and arg is not None:
            st.cf = arg
        elif w == "f" and arg is not None:
            st.f = arg
        elif w == "plain":
            st.plain()
        elif w == "pard":
            st.align = "left"
        elif w in _ALIGN:
            st.align = _ALIGN[w]
        elif w == "deff" and arg is not None:
            self.deff = arg
        elif w == "ansicpg" and arg is not None:
            self.codepage = f"cp{arg}"
        elif w == "uc" and arg is not None:
            st.uc = max(0, arg)
        elif w == "u" and arg is not None:
            self.text(st, chr(arg + 65536 if arg < 0 else arg))
            self.skip_chars = st.uc

    # -- HTML --------------------------------------------------------------------------
    def span(self, fmt: _Fmt, text: str) -> str:
        bold, italic, ul, fs, cf, f = fmt
        css = [f"font-size:{fs * 96.0 / 144.0:.2f}px"]
        font = self.fonts.get(f if f is not None else (self.deff if self.deff is not None else -1))
        if font:
            css.append("font-family:'%s'" % font.replace("'", ""))
        if bold:
            css.append("font-weight:bold")
        if italic:
            css.append("font-style:italic")
        if ul:
            css.append("text-decoration:underline")
        colour = self.colours[cf] if 0 < cf < len(self.colours) else None
        if colour:
            css.append(f"color:{colour}")
        return f'<span style="{";".join(css)}">{html.escape(text or " ", quote=False)}</span>'

    def html(self) -> str:
        out = ["<html><body>"]
        for align, runs in self.paras:
            body = "".join(self.span(fmt, t) for fmt, t in runs)
            out.append(f'<p style="margin:0px;white-space:pre;text-align:{align}">{body}</p>')
        out.append("</body></html>")
        return "".join(out)


def _word(s: str, i: int) -> str:
    j = i
    while j < len(s) and ("a" <= s[j] <= "z" or "A" <= s[j] <= "Z"):
        j += 1
    return s[i:j]


def rtf_paragraphs(rtf: str) -> List[str]:
    """The plain text of each paragraph of ``rtf``."""
    r = _Reader(rtf or "")
    r.read()
    return ["".join(t for _f, t in runs) for _a, runs in r.paras]


def rtf_to_html(rtf: str) -> str:
    """``rtf`` as HTML: one ``<p>`` per paragraph, its runs as styled spans (sizes in pixels
    at 96 dpi), white space kept."""
    r = _Reader(rtf or "")
    r.read()
    return r.html()
