"""Help with the Cicode of an expression or command field: colours (cicode_lex's tokens,
ui/style/syntax), wavy underlines under what the compiler would refuse or does not know
(with the names it may have meant), the arguments of the function being typed, a display
format's preview, what a name is on hover (and its value while the page is simulated), a
project function's file (Ctrl+click, F12), and, in a multi-line command box, the names and
functions completed as you type."""

from __future__ import annotations

import difflib
import html
import os
import re
import shutil
import subprocess
import weakref
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ...format.cnum import is_digits
from ... import cicode_lex as L
from ... import cicode_names as _cn

#: the longest description an argument hint shows
DESCRIPTION_CHARS = 220
#: the name before the cursor (what completion replaces)
_NAME_BEFORE = re.compile(r"[%s.]*$" % _cn.PART_CHARS)
#: the names "Did you mean" offers, and how alike they must be (difflib's ratio)
SUGGESTIONS = 3
SUGGEST_CUTOFF = 0.75
#: how often a hover with a simulated value reads it again (ms)
LIVE_MS = 1000


class Problem(NamedTuple):
    """Something wrong in the text: where (start, end), how bad ('error' or 'warning'),
    and why."""

    start: int
    end: int
    level: str
    message: str
    #: L.NAME or L.FUNCTION: a name the project does not have ('' any other problem)
    unknown: str = ""


def option(key: str) -> bool:
    try:
        from ..prefs.settings import option as _option

        return _option(key)
    except Exception:  # noqa: BLE001  # swallow-ok: no settings: the defaults
        return True


# ------------------------------------------------------------------ problems
def lexical_problems(tokens: Sequence[L.Token]) -> List[Problem]:
    """What the tokens themselves say is wrong (a character, a number or an operator
    Cicode does not have, a string or comment left open)."""
    return [Problem(t.start, t.end, "error", t.problem) for t in tokens if t.problem]


def suggestions(context, kind: str, name: str, n: int = SUGGESTIONS) -> List[str]:
    """The names (L.NAME) or functions (L.FUNCTION) of the project most like ``name``, the
    closest first ("Did you mean")."""
    if context is None:
        return []
    try:
        if kind == L.FUNCTION:
            pool = [r[1] for r in context.function_rows()]
        elif _names_ready(context):
            pool = context.tag_names()
        else:
            return []
    except Exception:  # noqa: BLE001  # swallow-ok: no lists: no suggestions
        return []
    low = name.lower()
    room = max(2, len(low) // 3)
    alike: Dict[str, str] = {}
    for c in pool:
        if abs(len(c) - len(low)) <= room:
            alike.setdefault(c.lower(), c)
    alike.pop(low, None)
    m = difflib.SequenceMatcher()
    m.set_seq2(low)
    scored = []
    for c in alike:
        m.set_seq1(c)
        if m.real_quick_ratio() >= SUGGEST_CUTOFF and m.quick_ratio() >= SUGGEST_CUTOFF:
            r = m.ratio()
            if r >= SUGGEST_CUTOFF:
                scored.append((-r, c))
    return [alike[c] for _r, c in sorted(scored)[:n]]  # (the closest; alike ones by name)


def did_you_mean(names: Sequence[str]) -> str:
    """'Did you mean A, B or C?' ('' none)."""
    if not names:
        return ""
    if len(names) == 1:
        return "Did you mean %s?" % names[0]
    return "Did you mean %s or %s?" % (", ".join(names[:-1]), names[-1])


def _names_ready(context) -> bool:
    """The context's tags can be looked up without waiting (ExpressionContext.names_ready;
    a context without the test: yes)."""
    ready = getattr(context, "names_ready", None)
    if ready is None:
        return True
    try:
        return bool(ready())
    except Exception:  # noqa: BLE001  # swallow-ok: not known: no lookup
        return False


def _known(context, how: str, name: str) -> Optional[bool]:
    fn = getattr(context, how, None)
    if fn is None:
        return None
    try:
        return fn(name)
    except Exception:  # noqa: BLE001  # swallow-ok: not known: no underline
        return None


#: E2064 Function 'X' not found
UNKNOWN_FUNCTION = 2064


def check_problems(text: str, tokens: Sequence[L.Token], result: Optional[dict]) -> List[Problem]:
    """The errors and warnings of a check result, underlined at their column (a warning without
    one is not underlined). An undefined tag name or unknown function is ``unknown``."""
    out: List[Problem] = []
    if not result:
        return out
    toks = list(tokens)
    for level, items in (("error", result.get("errors")), ("warning", result.get("warnings"))):
        for e in items or ():
            col = int(e.get("column") or 0)
            msg = str(e.get("message") or "")
            t = None
            if col <= 0 or col > len(text) + 1:
                if level == "warning":
                    continue
                start, end = 0, len(text)
            else:
                t = L.token_at(toks, col - 1)
                start, end = (t.start, t.end) if t is not None else (col - 1, min(len(text), col))
            unknown = ""
            if t is not None and t.kind == L.NAME and level == "warning" and e.get("kind") == "tag":
                unknown = L.NAME
            elif t is not None and t.kind == L.FUNCTION and e.get("code") == UNKNOWN_FUNCTION:
                unknown = L.FUNCTION
            out.append(Problem(start, max(end, start + 1), level, msg, unknown))
    return out


# ------------------------------------------------------------------ texts
def signature_html(fn, argument: int) -> str:
    """A function's arguments with the one being typed in bold, and its description
    ('' when its signature is not known)."""
    sig = fn.signature if "(" in (fn.signature or "") else (fn.name + fn.args if fn.args else "")
    parts = L.split_signature(sig)
    if parts is None:
        return ""
    head, args, tail = parts
    pieces = []
    for i, a in enumerate(args):
        t = html.escape(a.text)
        if a.optional:
            t = "[%s]" % t
        pieces.append("<b>%s</b>" % t if i == argument else t)
    lines = [html.escape(head) + ", ".join(pieces) + html.escape(tail)]
    if args and argument >= len(args):
        lines.append(
            "%s takes %s."
            % (html.escape(fn.name), "1 argument" if len(args) == 1 else "%d arguments" % len(args))
        )
    elif not args and argument > 0:
        lines.append("%s takes no arguments." % html.escape(fn.name))
    if fn.description:
        d = fn.description
        if len(d) > DESCRIPTION_CHARS:
            d = d[: DESCRIPTION_CHARS - 1].rstrip() + "…"
        lines.append("<span style='color:%s'>%s</span>" % (_dim(), html.escape(d)))
    return "<br>".join(lines)


def _dim() -> str:
    try:
        from ..style.theme import COLORS

        return COLORS.get("text_dim", "#969ca5")
    except Exception:  # noqa: BLE001  # swallow-ok: the default colour
        return "#969ca5"


_KINDS = {"variable": "Variable tag", "local": "Local variable", "equipment": "Equipment item"}


def _tag_html(name: str, context) -> str:
    if context is not None and not _names_ready(context):
        return "<b>%s</b>: the project's tags are still being read." % html.escape(name)
    idx = context.tag_index() if context is not None else None
    e = idx.get(name) if idx is not None else None
    field = ""
    if e is None and "." in name:
        head, field = name.rsplit(".", 1)
        e = idx.get(head) if idx is not None else None
    if e is None:
        known = _known(context, "is_name", name)
        return "<b>%s</b>: %s" % (
            html.escape(name),
            "a name the project knows." if known else "not a tag in the project.",
        )
    bits = [_KINDS.get(e.kind, e.kind)]
    if e.type:
        bits.append(e.type)
    if e.eng_zero or e.eng_full:
        bits.append(("%s to %s %s" % (e.eng_zero or "?", e.eng_full or "?", e.eng_units or "")).rstrip())
    if e.equip and e.kind != "equipment":
        bits.append("equipment %s" % (e.equip + ("." + e.item if e.item else "")))
    bits.append("in %s" % e.project)
    out = "<b>%s</b>%s: %s." % (
        html.escape(e.name),
        (" field <b>%s</b>" % html.escape(field)) if field else "",
        html.escape(", ".join(bits)),
    )
    if e.comment:
        out += "<br>" + html.escape(e.comment)
    return out


def _function_html(name: str, context) -> str:
    fn = context.function(name) if context is not None and hasattr(context, "function") else None
    if fn is None:
        return "<b>%s</b>: not a known Cicode function." % html.escape(name)
    sig = fn.signature if "(" in (fn.signature or "") else fn.name + (fn.args or "()")
    where = (
        "Built-in function" if fn.source == "built-in" else "Function in %s (%s)" % (fn.category, fn.source)
    )
    out = "<b>%s</b><br>%s." % (html.escape(sig), html.escape(where))
    if fn.description:
        out += "<br>" + html.escape(fn.description)
    if getattr(fn, "path", ""):
        out += "<br><span style='color:%s'>Ctrl+click or F12 opens it at line %d.</span>" % (
            _dim(),
            fn.line,
        )
    return out


def _live_text(name: str, context) -> Optional[str]:
    """The value ``name`` has in the page's simulation (ExpressionContext.live_value: ''
    while it is read), None when no simulation runs the project."""
    fn = getattr(context, "live_value", None) if context is not None else None
    if fn is None:
        return None
    try:
        return fn(name)
    except Exception:  # noqa: BLE001  # swallow-ok: no simulation: no value
        return None


def _live_html(name: str, context) -> str:
    v = _live_text(name, context)
    if v is None:
        return ""
    return "Value in the simulation: %s" % ("<b>%s</b>" % html.escape(v) if v else "being read")


#: the hover help of a part of a name a genie makes ('%Tag%_PV')
_MADE = "%s is a part of a name the genie makes. The name is known when the genie is placed."

_TIPS = {
    L.LABEL: "%s is a label of the Include project: TRUE is 1, FALSE is 0.",
    L.ASSOCIATION: "%s is a super genie association: the page that opens the super genie gives it.",
    L.FORMAT: "%s is a display format: # is a digit, . the decimal point, 0 pads with zeros, "
    "- aligns to the left, EU adds the engineering units.",
    L.VBA: "VBA script: CiVBA runs the rest of the command as VBA.",
}


def hover_html(
    text: str, tokens: Sequence[L.Token], pos: int, context, problems: Sequence[Problem], suggest=None
) -> str:
    """The hover help at ``pos`` (rich text, '' none): the problems there, then what the token is."""
    lines = []
    for p in problems:
        if p.start <= pos < p.end:
            line = html.escape(p.message)
            if p.unknown and suggest is not None:
                meant = did_you_mean(suggest(p.unknown, text[p.start : p.end]))
                if meant:
                    line = line.rstrip(".") + ". " + html.escape(meant)
            lines.append(line)
    tokens = list(tokens)
    t = L.token_at(tokens, pos)
    if t is not None and t.start <= pos <= t.end:
        word = text[t.start : t.end]
        unknown = any(p.unknown and p.start == t.start for p in problems)  # (said above)
        if t.kind == L.FUNCTION:
            if not unknown:
                lines.append(_function_html(word, context))
        elif t.kind == L.NAME and L.beside_template(tokens, tokens.index(t)):
            lines.append(html.escape(_MADE % word))
        elif t.kind == L.NAME:
            if not unknown:
                lines.append(_tag_html(word, context))
                live = _live_html(word, context)
                if live:
                    lines.append(live)
        elif t.kind == L.FORMAT:
            lines.append(format_html(text, tokens, t, context))
        elif t.kind == L.PARAM:
            name, default = L.param_parts(word)
            line = "%%%s%% is a genie parameter: the genie's form gives it a value." % name
            if default is not None:
                line += " Its default is %s." % default
            lines.append(html.escape(line))
        elif t.kind in _TIPS:
            lines.append(
                html.escape(_TIPS[t.kind] % word.strip() if "%s" in _TIPS[t.kind] else _TIPS[t.kind])
            )
    # <qt>: Qt shows a tip without tags as plain text, entities and all
    return "<qt>%s</qt>" % "<br>".join(dict.fromkeys(lines)) if lines else ""


# ------------------------------------------------------------------ display formats
#: a picture format: '#', '0' and '-' before the point, '#' after it, then the units
_PICTURE = re.compile(r"^\s*([#0\-]*)(?:\.(#*))?\s*((?:eu|engunit)?)\s*$", re.IGNORECASE)
#: the value a preview shows when the tag's range is not known
SAMPLE_VALUE = 1234.5678


def format_preview(spec: str, value: float, units: str = "") -> Optional[str]:
    """``value`` as the picture format ``spec`` shows it ('###.# EU', 45.67, 'm' ->
    ' 45.7 m'): '#' a digit, '.' the point, '0' leading zeros, '-' left aligned, EU the
    units; None for a format it does not draw."""
    m = _PICTURE.match(spec or "")
    if m is None or "#" not in spec:
        return None
    head, tail, eu = m.group(1), m.group(2), m.group(3)
    places = len(tail) if tail is not None else 0
    width = sum(1 for c in head if c in "#0") + (1 + places if tail is not None else 0)
    out = "%.*f" % (places, value)
    if "0" in head:
        sign = "-" if out.startswith("-") else ""
        out = sign + out[len(sign) :].rjust(width - len(sign), "0")
    elif "-" in head:
        out = out.ljust(width)
    else:
        out = out.rjust(width)
    if eu and units:
        out += " " + units
    return out


def _sample(context, name: str) -> Tuple[float, str, str]:
    """(a value, its units, what it is) for the preview of ``name``'s format: a point of
    its range when the project gives one."""
    idx = context.tag_index() if context is not None and name and _names_ready(context) else None
    e = idx.get(name) if idx is not None else None
    if e is not None:
        try:
            lo, hi = float(e.eng_zero or 0), float(e.eng_full or 0)
        except ValueError:
            lo = hi = 0.0
        if hi != lo:
            return lo + (hi - lo) * 0.4567, e.eng_units or "", "a value of %s" % e.name
        return SAMPLE_VALUE, e.eng_units or "", "a value of %s" % e.name
    return SAMPLE_VALUE, "", "the value %g" % SAMPLE_VALUE


def _format_target(text: str, tokens: Sequence[L.Token], colon: int) -> str:
    """The name the display format after the ':' at ``colon`` shows ('' not one name)."""
    before = [t for t in tokens if t.end <= colon]
    if before and before[-1].kind == L.NAME:
        return text[before[-1].start : before[-1].end]
    return ""


def format_html(text: str, tokens: Sequence[L.Token], t: Optional[L.Token], context, colon: int = -1) -> str:
    """The display format help: what its characters do and how the value looks (``t`` the
    format token, None while nothing is typed after the ':' at ``colon``)."""
    tokens = list(tokens)
    if t is not None:
        k = tokens.index(t)
        colon = tokens[k - 1].start if k and text[tokens[k - 1].start] == ":" else t.start
    lines = [
        "<b>Display format</b>: # is a digit, . the decimal point, 0 pads with zeros, "
        "- aligns to the left, EU adds the engineering units."
    ]
    if t is not None:
        spec = text[t.start : t.end]
        value, units, what = _sample(context, _format_target(text, tokens, colon))
        shown = format_preview(spec, value, units)
        if shown is not None:
            lines.append(
                "%s shows as <span style='white-space:pre; font-family:monospace'>%s</span>"
                % (html.escape(what[:1].upper() + what[1:]), html.escape("[" + shown + "]"))
            )
    else:
        lines.append(
            "For example ###.# EU shows %s." % html.escape("[" + format_preview("###.# EU", 45.67, "m") + "]")
        )
    return "<br>".join(lines)


def _vscode() -> Optional[str]:
    """VS Code's command line launcher (code.cmd), None when it is not installed."""
    found = shutil.which("code")
    if found:
        return found
    for root in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles")):
        if root:
            for sub in (("Programs", "Microsoft VS Code"), ("Microsoft VS Code",)):
                p = os.path.join(root, *sub, "bin", "code.cmd")
                if os.path.isfile(p):
                    return p
    return None


def open_definition(fn) -> bool:
    """Open a project function's .ci file at its line: in VS Code when it is installed,
    else in the program Windows opens .ci files with."""
    path = getattr(fn, "path", "")
    if not path or not os.path.isfile(path):
        return False
    code = _vscode()
    if code:
        try:
            subprocess.Popen(
                [code, "--goto", "%s:%d" % (path, max(1, int(fn.line or 1)))],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except OSError:
            from ...infra import logsetup

            logsetup.note_exception("cicode_assist.open_definition")
    return QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))


# ------------------------------------------------------------------ formats
def char_keys(text: str, tokens: Sequence[L.Token], problems: Sequence[Problem], colour: bool) -> list:
    """(colour role or None, problem level or None) of every character."""
    from ..style.syntax import ROLE

    keys: list = [(None, None)] * len(text)
    if colour:
        for t in tokens:
            role = ROLE.get(t.kind)
            if role is not None:
                for i in range(t.start, min(t.end, len(text))):
                    keys[i] = (role, None)
    for p in sorted(problems, key=lambda p: p.level == "error"):  # an error over a warning
        for i in range(max(0, p.start), min(p.end, len(text))):
            keys[i] = (keys[i][0], p.level)
    return keys


def runs(keys: list) -> List[Tuple[int, int, tuple]]:
    """(start, length, key) of each run of equal keys that is not plain text."""
    out = []
    i, n = 0, len(keys)
    while i < n:
        j = i
        while j < n and keys[j] == keys[i]:
            j += 1
        if keys[i] != (None, None):
            out.append((i, j - i, keys[i]))
        i = j
    return out


def make_format(key: tuple, table: Dict[str, str]) -> QtGui.QTextCharFormat:
    """The format of a (colour role, problem level) key."""
    from ..style import syntax

    role, level = key
    base = syntax.role_format(role, table) if role is not None else None
    if level is not None:
        return syntax.problem_format(level, base)
    return base if base is not None else QtGui.QTextCharFormat()


# ------------------------------------------------------------------ the hint popup
class HintPopup(QtWidgets.QLabel):
    """The argument hint: a small window above the field that takes no focus."""

    def __init__(self, owner: QtWidgets.QWidget):
        super().__init__(owner, QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
        self.setObjectName("cicodeHint")
        self.setTextFormat(QtCore.Qt.RichText)
        self.setWordWrap(True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)

    def show_text(self, text: str, anchor: QtCore.QPoint) -> None:
        """Show ``text`` with its bottom left corner at ``anchor`` (global), kept on the
        screen (under the anchor when there is no room above)."""
        from ..style.theme import COLORS, px

        self.setStyleSheet(
            "QLabel#cicodeHint { background: %s; color: %s; border: 1px solid %s; padding: %dpx %dpx; }"
            % (
                COLORS.get("popup", "#23262b"),
                COLORS.get("text", "#d6d9de"),
                COLORS.get("border", "#3a3f46"),
                px(3),
                px(6),
            )
        )
        self.setText(text)
        screen = QtGui.QGuiApplication.screenAt(anchor) or QtGui.QGuiApplication.primaryScreen()
        room = screen.availableGeometry() if screen is not None else QtCore.QRect(0, 0, 4000, 4000)
        # as wide as its longest line, wrapped only beyond the widest a hint gets (or the
        # screen's width)
        widest = min(px(620), room.width())
        self.setWordWrap(False)
        size = self.sizeHint()
        if size.width() > widest:
            self.setWordWrap(True)
            size = QtCore.QSize(widest, self.heightForWidth(widest))
        self.resize(size)
        x = max(room.left(), min(anchor.x(), room.right() - self.width()))
        y = anchor.y() - self.height() - px(2)
        if y < room.top():
            y = anchor.y() + px(24)
        self.move(x, y)
        self.show()
        self.raise_()


# ------------------------------------------------------------------ the highlighter
class _Highlighter(QtGui.QSyntaxHighlighter):
    """The colours of a multi-line command box (its assist's runs, block by block)."""

    def __init__(self, document, assist: "CicodeAssist"):
        super().__init__(document)
        self._assist = weakref.ref(assist)

    def highlightBlock(self, text: str) -> None:  # noqa: N802 -- Qt naming
        a = self._assist()
        if a is None:
            return
        start = self.currentBlock().position()
        for s, n, fmt in a.block_runs(start, len(text)):
            self.setFormat(s, n, fmt)


# ------------------------------------------------------------------ the assist
class CicodeAssist(QtCore.QObject):
    """The help of one Cicode field: a QLineEdit (an expression or a short command) or a
    QPlainTextEdit (a command). ``kind``: 'expr' or 'command'. The field's owner gives it
    the compiler's verdict with :meth:`set_check_result`."""

    def __init__(self, widget: QtWidgets.QWidget, context, kind: str = "expr", complete: bool = False):
        super().__init__(widget)
        _ALL.add(self)
        self._widget = weakref.ref(widget)
        self.context = context
        self.kind = kind
        self._line = isinstance(widget, QtWidgets.QLineEdit)
        self._text: Optional[str] = None
        self._tokens: List[L.Token] = []
        self._lexical: List[Problem] = []
        self._checked: List[Problem] = []
        self._checked_text: Optional[str] = None
        self._keys: list = []
        self._formats: Dict[tuple, QtGui.QTextCharFormat] = {}
        self._table: Optional[Dict[str, str]] = None
        self._composing = False
        self._applying = False  # (colouring now: the events it causes are its own)
        self._hint: Optional[HintPopup] = None
        self._hint_closed: Optional[int] = None  # the '(' of a call whose hint Esc closed
        self._highlighter = None if self._line else _Highlighter(widget.document(), self)
        widget.installEventFilter(self)
        #: a box's viewport (its mouse and hover events), kept: the filter never asks a box
        #: being deleted for it
        self._viewport = None if self._line else widget.viewport()
        if self._viewport is not None:
            self._viewport.installEventFilter(self)
        self._window = None
        widget.textChanged.connect(self._text_changed)
        widget.cursorPositionChanged.connect(self._cursor_moved)
        self._completer = None
        self._typed = False
        #: the names "Did you mean" found: (kind, lower-case name) -> names
        self._meant: Dict[tuple, List[str]] = {}
        # a hover that shows a simulated value reads it again while it is shown
        self._live_hover = None
        self._live_timer = QtCore.QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._refresh_hover)
        if complete and not self._line:
            self._set_up_completion()
        self.refresh()

    # -- what the field holds -----------------------------------------------------------
    @property
    def widget(self) -> Optional[QtWidgets.QWidget]:
        return self._widget()

    def text(self) -> str:
        w = self.widget
        if w is None:
            return ""
        return QtWidgets.QLineEdit.text(w) if self._line else w.toPlainText()

    def _ensure(self) -> None:
        text = self.text()
        if text == self._text:
            return
        self._text = text
        self._tokens = L.tokenize(text)
        self._lexical = lexical_problems(self._tokens)
        self._keys = []

    def tokens(self) -> List[L.Token]:
        self._ensure()
        return list(self._tokens)

    def problems(self) -> List[Problem]:
        """Every problem shown under the text now."""
        self._ensure()
        checked = self._checked if self._checked_text == self._text else []
        return self._lexical + checked

    # -- the compiler's verdict ---------------------------------------------------------------
    def set_check_result(self, text: str, result: Optional[dict]) -> None:
        """The compiler's verdict on ``text`` (ui/expression_check)."""
        self._ensure()
        if text.strip() != (self._text or "").strip():
            return
        self._checked = check_problems(self._text, self._tokens, result)
        self._checked_text = self._text
        self.refresh()

    # -- colours ---------------------------------------------------------------------------
    def keys(self) -> list:
        """(role, level) of each character (tests)."""
        self._ensure()
        if not self._keys and self._text:
            on = option("colour_cicode")
            self._keys = char_keys(
                self._text, self._tokens, self.problems() if on else self._checked_only(), on
            )
        return self._keys

    def _checked_only(self) -> List[Problem]:
        return self._checked if self._checked_text == self._text else []

    def _format(self, key: tuple) -> QtGui.QTextCharFormat:
        f = self._formats.get(key)
        if f is None:
            from ..style.syntax import colours

            if self._table is None:
                self._table = colours()
            f = self._formats[key] = make_format(key, self._table)
        return f

    def block_runs(self, start: int, length: int) -> List[Tuple[int, int, QtGui.QTextCharFormat]]:
        """The formats of the characters start .. start + length, from 0 (the highlighter)."""
        keys = self.keys()[start : start + length]
        return [(s, n, self._format(k)) for s, n, k in runs(keys)]

    def refresh(self) -> None:
        """Colour the field again (its text, a theme or an option changed)."""
        self._keys = []
        w = self.widget
        if w is None:
            return
        if self._line:
            self._apply_line(w)
        elif self._highlighter is not None:
            blocker = QtCore.QSignalBlocker(w)
            try:
                self._highlighter.rehighlight()
            finally:
                blocker.unblock()

    def _apply_line(self, w: QtWidgets.QLineEdit) -> None:
        if self._composing:
            return  # (an input method is composing: its own formats show)
        cur = w.cursorPosition()
        attrs = [
            QtGui.QInputMethodEvent.Attribute(QtGui.QInputMethodEvent.TextFormat, s - cur, n, self._format(k))
            for s, n, k in runs(self.keys())
        ]
        ev = QtGui.QInputMethodEvent("", attrs)
        self._applying = True
        try:
            if w.isReadOnly():
                # (a read-only line edit takes no input method event)
                QtWidgets.QLineEdit.setReadOnly(w, False)
                try:
                    QtCore.QCoreApplication.sendEvent(w, ev)
                finally:
                    QtWidgets.QLineEdit.setReadOnly(w, True)
            else:
                QtCore.QCoreApplication.sendEvent(w, ev)
        finally:
            self._applying = False

    def _theme_changed(self) -> None:
        self._table = None
        self._formats = {}
        self.refresh()
        if self._hint is not None and self._hint.isVisible():
            self.update_hint()

    # -- events ------------------------------------------------------------------------------
    def _text_changed(self, *_a) -> None:
        self._ensure()
        self.refresh()
        self.update_hint()
        if self._typed:
            self._typed = False
            self._update_completion()

    def _cursor_moved(self, *_a) -> None:
        self.update_hint()

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        w = self.widget
        if w is None:
            return False
        t = ev.type()
        if obj is self._window:
            if t in (QtCore.QEvent.Move, QtCore.QEvent.Resize, QtCore.QEvent.WindowDeactivate):
                self.hide_hint()
            return False
        on_text = obj is w or (self._viewport is not None and obj is self._viewport)
        if t == QtCore.QEvent.ToolTip and on_text:
            return self._tooltip(obj, ev)
        if t == QtCore.QEvent.ContextMenu and on_text and not getattr(w, "assist_menu", False):
            return self._context_menu(obj, ev)
        if (
            t == QtCore.QEvent.MouseButtonPress
            and on_text
            and ev.button() == QtCore.Qt.LeftButton
            and ev.modifiers() & QtCore.Qt.ControlModifier
        ):
            fn = self.definition_at(self._pos_at(ev.position().toPoint()))
            if fn is not None:
                open_definition(fn)
                return True
            return False
        if obj is not w:
            return False
        if t == QtCore.QEvent.KeyPress:
            return self._key(ev)
        if t == QtCore.QEvent.InputMethod and self._line:
            self._composing = bool(ev.preeditString())
        elif t in (QtCore.QEvent.FocusOut, QtCore.QEvent.Hide):
            self.hide_hint()
        elif t == QtCore.QEvent.FocusIn:
            self._watch_window()
            self.update_hint()
        elif (
            t in (QtCore.QEvent.PaletteChange, QtCore.QEvent.ApplicationPaletteChange) and not self._applying
        ):
            self._theme_changed()
        return False

    def _watch_window(self) -> None:
        w = self.widget
        win = w.window() if w is not None else None
        if win is not None and win is not self._window and win is not w:
            if self._window is not None:
                self._window.removeEventFilter(self)
            self._window = win
            win.installEventFilter(self)

    def _key(self, ev) -> bool:
        c = self._completer
        if (
            c is not None
            and c.popup().isVisible()
            and ev.key()
            in (
                QtCore.Qt.Key_Enter,
                QtCore.Qt.Key_Return,
                QtCore.Qt.Key_Escape,
                QtCore.Qt.Key_Tab,
                QtCore.Qt.Key_Backtab,
            )
        ):
            ev.ignore()
            return True  # the popup takes them
        if ev.key() == QtCore.Qt.Key_Escape and self._hint is not None and self._hint.isVisible():
            call = L.call_at(self.text(), self._cursor(), self.tokens())
            self._hint_closed = call.open_paren if call is not None else None
            self.hide_hint()
            return True
        if ev.key() == QtCore.Qt.Key_F12 and not ev.modifiers():
            fn = self.definition_at(self._cursor())
            if fn is not None:
                open_definition(fn)
                return True
        if ev.text() or ev.key() in (QtCore.Qt.Key_Backspace, QtCore.Qt.Key_Delete):
            self._typed = True
        return False

    def _pos_at(self, point: QtCore.QPoint) -> int:
        """The character at ``point`` (the line edit's, or the box's viewport's)."""
        w = self.widget
        if self._line:
            return w.cursorPositionAt(point)
        return w.cursorForPosition(point).position()

    def _hover_pos(self, point: QtCore.QPoint) -> Optional[int]:
        """The character under ``point``; None right of a line edit's text."""
        text = self.text()
        if not text:
            return None
        pos = self._pos_at(point)
        if self._line and pos >= len(text) and self._pos_at(point - QtCore.QPoint(8, 0)) >= len(text):
            return None
        return min(pos, max(0, len(text) - 1))

    def hover_text(self, pos: int) -> str:
        """The hover help at character ``pos``."""
        return hover_html(self.text(), self.tokens(), pos, self.context, self.problems(), self.suggestions)

    def _tooltip(self, obj, ev) -> bool:
        pos = self._hover_pos(ev.pos())
        if pos is None:
            return False
        tip = self.hover_text(pos)
        if not tip:
            return False
        QtWidgets.QToolTip.showText(ev.globalPos(), tip, obj)
        t = L.token_at(self.tokens(), pos)
        if (
            t is not None
            and t.kind == L.NAME
            and _live_text(self.text()[t.start : t.end], self.context) is not None
        ):
            self._live_hover = (obj, ev.globalPos(), pos, tip)
            self._live_timer.start(LIVE_MS)
        return True

    def _refresh_hover(self) -> None:
        """Show the hovered name's new simulated value (while its hover is shown)."""
        h = self._live_hover
        if h is None or not QtWidgets.QToolTip.isVisible():
            self._live_hover = None
            return
        obj, where, pos, old = h
        if pos >= len(self.text()):
            return
        tip = self.hover_text(pos)
        if tip and tip != old:
            QtWidgets.QToolTip.showText(where, tip, obj)
        self._live_hover = (obj, where, pos, tip)
        self._live_timer.start(LIVE_MS)

    # -- "Did you mean", the definition, the menu -------------------------------------------
    def suggestions(self, kind: str, name: str) -> List[str]:
        """The project's names most like ``name`` (kept for this field)."""
        key = (kind, name.lower())
        hit = self._meant.get(key)
        if hit is None:
            hit = suggestions(self.context, kind, name)
            if len(self._meant) > 64:
                self._meant.clear()
            self._meant[key] = hit
        return hit

    def definition_at(self, pos: int):
        """The project function whose call is at character ``pos`` (its .ci file known),
        else None."""
        t = L.token_at(self.tokens(), pos)
        if t is None or t.kind != L.FUNCTION or not (t.start <= pos <= t.end):
            return None
        fn = (
            self.context.function(self.text()[t.start : t.end]) if hasattr(self.context, "function") else None
        )
        return fn if fn is not None and getattr(fn, "path", "") else None

    def replace(self, start: int, end: int, new: str) -> None:
        """Put ``new`` in place of characters start .. end (one undo step)."""
        w = self.widget
        if w is None:
            return
        if self._line:
            w.setSelection(start, end - start)
            w.insert(new)
        else:
            cur = w.textCursor()
            cur.setPosition(start)
            cur.setPosition(end, QtGui.QTextCursor.KeepAnchor)
            cur.insertText(new)
            w.setTextCursor(cur)

    def menu_actions(self, pos: int) -> List[Tuple[str, object]]:
        """(label, callback) of what the field's menu offers at character ``pos``: the
        names an unknown name may be, and the definition of a project function."""
        out: List[Tuple[str, object]] = []
        w = self.widget
        text = self.text()
        if w is not None and not w.isReadOnly():
            for p in self.problems():
                if p.unknown and p.start <= pos <= p.end:
                    for name in self.suggestions(p.unknown, text[p.start : p.end]):
                        out.append(
                            (
                                "Replace with %s" % name,
                                lambda s=p.start, e=p.end, n=name: self.replace(s, e, n),
                            )
                        )
                    break
        fn = self.definition_at(pos)
        if fn is not None:
            out.append(("Go to Definition", lambda f=fn: open_definition(f)))
        return out

    def add_to_menu(self, menu: QtWidgets.QMenu, pos: int) -> None:
        """Put :meth:`menu_actions` at the top of ``menu``."""
        acts = self.menu_actions(pos)
        if not acts:
            return
        first = menu.actions()[0] if menu.actions() else None
        for label, fn in acts:
            a = QtGui.QAction(label, menu)
            if label == "Go to Definition":
                a.setShortcut(QtGui.QKeySequence(QtCore.Qt.Key_F12))
            a.triggered.connect(fn)
            menu.insertAction(first, a)
        if first is not None:
            menu.insertSeparator(first)

    def _context_menu(self, obj, ev) -> bool:
        w = self.widget
        pos = self._pos_at(ev.pos())
        menu = w.createStandardContextMenu() if self._line else w.createStandardContextMenu(ev.pos())
        self.add_to_menu(menu, pos)
        menu.exec(ev.globalPos())
        menu.deleteLater()
        return True

    # -- the argument hint ---------------------------------------------------------------
    def _cursor(self) -> int:
        w = self.widget
        return w.cursorPosition() if self._line else w.textCursor().position()

    def format_hint(self) -> str:
        """The display format help when the cursor is in an expression's format ('' not)."""
        if self.kind != "expr":
            return ""
        text, pos, tokens = self.text(), self._cursor(), self.tokens()
        t = L.token_at(tokens, pos)
        if t is None:
            return ""
        if t.kind == L.FORMAT and t.start <= pos <= t.end:
            return format_html(text, tokens, t, self.context)
        if t.kind == L.PUNCT and text[t.start] == ":" and t.end == pos:
            return format_html(text, tokens, None, self.context, t.start)
        return ""

    def hint_html(self) -> str:
        """The argument hint (or the display format help) for the cursor's place ('' none)."""
        fmt = self.format_hint()
        if fmt:
            return fmt
        if self.context is None or not hasattr(self.context, "function"):
            return ""
        text = self.text()
        call = L.call_at(text, self._cursor(), self.tokens())
        if call is None or call.open_paren == self._hint_closed:
            return ""
        fn = self.context.function(call.name)
        return signature_html(fn, call.argument) if fn is not None else ""

    def update_hint(self) -> None:
        w = self.widget
        if w is None or not w.hasFocus() or not option("function_hints"):
            self.hide_hint()
            return
        call = L.call_at(self.text(), self._cursor(), self.tokens())
        if call is None or call.open_paren != self._hint_closed:
            self._hint_closed = None  # (another call: its hint shows again)
        text = self.hint_html()
        if not text:
            self.hide_hint()
            return
        if self._hint is None:
            self._hint = HintPopup(w)
        if self._line:
            anchor = w.mapToGlobal(QtCore.QPoint(w.cursorRect().left(), 0))
        else:
            r = w.cursorRect()
            anchor = w.viewport().mapToGlobal(QtCore.QPoint(r.left(), r.top()))
        self._hint.show_text(text, anchor)

    def hide_hint(self) -> None:
        if self._hint is not None:
            self._hint.hide()

    def hint_visible(self) -> bool:
        return self._hint is not None and self._hint.isVisible()

    # -- completion in a multi-line box ------------------------------------------------------
    def _set_up_completion(self) -> None:
        if self.context is None or not getattr(self.context, "project_dir", None):
            return
        self._completer = name_completer(self.widget, self.context.completion_model())
        self._completer.activated[str].connect(self._complete_with)

    def _name_before(self) -> Tuple[int, str]:
        w = self.widget
        cur = w.textCursor().position()
        text = self.text()
        m = _NAME_BEFORE.search(text[:cur])
        return (m.start() if m else cur), (m.group(0) if m else "")

    def _update_completion(self) -> None:
        c = self._completer
        w = self.widget
        if c is None or w is None or w.isReadOnly():
            return
        from ..panels import panel_options as _panels

        start, tok = self._name_before()
        need = _panels.TAG_SUGGEST_AFTER
        if (
            need <= 0
            or len(tok) < need
            or is_digits(tok[0])
            or not completes_names_at(self.text(), self.tokens(), start)
        ):
            c.popup().hide()
            return
        model = c.model()
        # (the context's own list: the model keeps its sorted form while the same list comes)
        extras = getattr(self.context, "_extra", None) or ()
        n = model.update(tok, extras, functions=self.context.function_rows())
        c.setCompletionPrefix(tok)
        if not n:
            c.popup().hide()
            return
        show_completion(c, w.cursorRect().translated(w.viewport().pos()))

    def _complete_with(self, name: str) -> None:
        w = self.widget
        if w is None:
            return
        start, _tok = self._name_before()
        cur = w.textCursor()
        end = cur.position()
        cur.setPosition(start)
        cur.setPosition(end, QtGui.QTextCursor.KeepAnchor)
        model = self._completer.model() if self._completer is not None else None
        add = ""
        if model is not None and model.is_function_row(name) and self.text()[end : end + 1] != "(":
            add = "("
        elif model is not None and model.is_level_row(name):
            add = "."  # (and the level below it is offered)
        cur.insertText(name + add)
        w.setTextCursor(cur)
        if add == ".":
            QtCore.QTimer.singleShot(0, self, self._update_completion)


#: every live assist (refresh_all)
_ALL: "weakref.WeakSet[CicodeAssist]" = weakref.WeakSet()


def refresh_all() -> None:
    """Colour every open field again (an option changed)."""
    for a in list(_ALL):
        try:
            a._theme_changed()
            if not option("function_hints"):
                a.hide_hint()
        except RuntimeError:  # swallow-ok: a field being deleted
            continue


# ------------------------------------------------------------------ completers
def completes_names_at(text: str, tokens: Sequence[L.Token], start: int) -> bool:
    """A name typed from character ``start`` may be one of the project's: it is not in a
    comment or a display format, not the name of a genie parameter or super genie
    association being typed ('%Ta'), and not a part of a name a genie makes ('%Tag%_P')."""
    tokens = list(tokens)
    t = L.token_at(tokens, start)
    if t is not None and (
        (t.kind == L.COMMENT and t.start < start) or (t.kind == L.FORMAT and t.start <= start)
    ):
        return False
    if start and text[start - 1] in "%?":
        return False
    return not any(x.end == start and x.kind in (L.PARAM, L.ASSOCIATION) for x in tokens)


def name_completer(parent: QtWidgets.QWidget, model) -> QtWidgets.QCompleter:
    """A completer over a scope's completion model (expression.TagCompletionModel): two
    columns, the name and what it is."""
    c = QtWidgets.QCompleter(parent)
    c.setModel(model)
    c.setCompletionMode(QtWidgets.QCompleter.UnfilteredPopupCompletion)
    c.setCompletionColumn(0)
    popup = QtWidgets.QTreeView()
    popup.setHeaderHidden(True)
    popup.setRootIsDecorated(False)
    popup.setUniformRowHeights(True)
    popup.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    popup.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    c.setPopup(popup)
    c.setWidget(parent)
    c.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
    c.setMaxVisibleItems(12)
    return c


def show_completion(c: QtWidgets.QCompleter, rect: QtCore.QRect) -> None:
    """Open ``c``'s popup under ``rect`` (the cursor), as wide as its two columns."""
    popup = c.popup()
    if isinstance(popup, QtWidgets.QTreeView):
        popup.resizeColumnToContents(0)
        popup.resizeColumnToContents(1)
        width = popup.columnWidth(0) + popup.columnWidth(1) + 24
    else:
        width = popup.sizeHintForColumn(0)
    rect = QtCore.QRect(rect)
    rect.setWidth(min(900, max(300, width + popup.verticalScrollBar().sizeHint().width())))
    c.complete(rect)


def attach(widget: QtWidgets.QWidget, context, kind: str = "command") -> Optional[CicodeAssist]:
    """The assist of a command box with a Wizard (one per box)."""
    if context is None or widget is None:
        return None
    have = getattr(widget, "cicode_assist", None)
    if have is not None:
        return have
    a = CicodeAssist(widget, context, kind, complete=True)
    widget.cicode_assist = a
    return a
