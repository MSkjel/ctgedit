"""Check the UI text rules a script can check.

python tools/check_ui_text.py [<file or folder> ...]
python tools/check_ui_text.py --write-baseline     (the known offenders)
python tools/check_ui_text.py --stale              (baseline lines fixed since)
"""

from __future__ import annotations

import ast
import os
import re
import sys
from collections import Counter
from typing import Iterable, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHES = {chr(0x2014): "em dash", chr(0x2013): "en dash"}
EXEMPT = "# text-ok"
#: ' -- ' used as a dash (also at a line end or start inside the text; a
#: bare '--', for example a command line argument, is not)
DOUBLE = re.compile(r"\s--(?:\s|$)|^--\s", re.M)
#: the message rules' accepted offenders
BASELINE = os.path.join(ROOT, "tools", "ui_text_baseline.txt")

RULE_PLURAL = "'(s)' in a message"
RULE_STOP = "message without a final '.'"
#: '(s)' after a word
PLURAL_S = re.compile(r"\w\(s\)")
#: a message ends with one of these
ENDINGS = (".", "?", "!", "…")
#: a text that ends with a value put in: not checked ('... %s', '... {name}')
TRAILING_VALUE = re.compile(r"(%[-#0 +]*\d*(?:\.\d+)?[sdirfxXeEgGc]|\{[^{}]*\})$")
#: call -> the index of its message argument
MESSAGE_ARG = {"showMessage": 0, "notify": 1, "error_box": 2, "setInformativeText": 0, "status_message": 0}
#: QMessageBox's static boxes (their text is the third argument)
BOX_STATICS = ("warning", "information", "critical", "question")

Problem = Tuple[str, int, str]  # (path relative to ROOT, line, what)


def _rel(path: str) -> str:
    try:
        return os.path.relpath(os.path.abspath(path), ROOT).replace("\\", "/")
    except ValueError:  # (another drive than the checkout)
        return os.path.abspath(path).replace("\\", "/")


def _exempt(lines: List[str], lineno: int) -> bool:
    return 0 < lineno <= len(lines) and lines[lineno - 1].rstrip().endswith(EXEMPT)


def _dash_problems(rel: str, lines: List[str]) -> List[Problem]:
    out = []
    for n, line in enumerate(lines, 1):
        if line.rstrip().endswith(EXEMPT):
            continue
        for ch, name in DASHES.items():
            if ch in line:
                out.append((rel, n, name))
    return out


def _docstring_nodes(tree: ast.AST) -> set:
    """The string constants that are docstrings or bare string statements
    (a string on a line of its own is a comment, not text anyone sees)."""
    ids = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            ids.add(id(node.value))
    return ids


# --------------------------------------------------------------------------- the messages
def _func_name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def _message_arg(call: ast.Call) -> Optional[ast.AST]:
    """The message argument of ``call`` when it is a message call."""
    name = _func_name(call)
    f = call.func
    if (
        name in BOX_STATICS
        and isinstance(f, ast.Attribute)
        and (
            (isinstance(f.value, ast.Attribute) and f.value.attr == "QMessageBox")
            or (isinstance(f.value, ast.Name) and f.value.id == "QMessageBox")
        )
    ):
        k = 2
    elif name == "QMessageBox":
        k = 2
    elif name in MESSAGE_ARG:
        k = MESSAGE_ARG[name]
    else:
        return None
    if len(call.args) > k:
        return call.args[k]
    want = {
        "showMessage": "text",
        "notify": "text",
        "error_box": "text",
        "setInformativeText": "text",
        "status_message": "text",
    }.get(name)
    for kw in call.keywords:
        if kw.arg == want:
            return kw.value
    return None


def _texts(node: ast.AST) -> List[Tuple[str, bool]]:
    """(the literal text, whether its end is the message's end) of a message expression:
    every literal piece; a '%' format or a .format() call gives its template."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [(node.value, True)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _texts(node.left)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = [(t, False) for t, _e in _texts(node.left)]
        right = _texts(node.right)
        if not right:  # (a value put in at the end: not known)
            return left + [("%s", True)]
        return left + right
    if isinstance(node, ast.IfExp):
        return _texts(node.body) + _texts(node.orelse)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _texts(node.func.value)
    if isinstance(node, ast.JoinedStr):
        parts, end_known = [], True
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
                end_known = True
            else:
                parts.append("{}")
                end_known = False
        return [("".join(parts), True)] if end_known or parts else []
    return []


def _message_problems(rel: str, tree: ast.AST, lines: List[str]) -> List[Tuple[int, str, str]]:
    """(line, rule, text) of the messages that break the message rules."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        arg = _message_arg(node)
        if arg is None:
            continue
        line = getattr(node, "lineno", 0) or 0
        if _exempt(lines, line):
            continue
        for text, at_end in _texts(arg):
            if PLURAL_S.search(text):
                out.append((line, RULE_PLURAL, text))
            t = text.rstrip()
            if not at_end or not t:
                continue
            if TRAILING_VALUE.search(t) or t.endswith(ENDINGS):
                continue
            out.append((line, RULE_STOP, text))
    return out


def _key(rel: str, rule: str, text: str) -> str:
    """A baseline line: path | rule | the start of the text (one line, no line number: the
    code around it moves)."""
    short = " ".join(str(text).split())[:70]
    return "%s | %s | %s" % (rel, rule, short)


def load_baseline(path: str = BASELINE) -> Counter:
    c: Counter = Counter()
    try:
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.rstrip("\n")
                if ln.strip() and not ln.startswith("#"):
                    c[ln] += 1
    except OSError:
        pass
    return c


def message_offenders(paths: Iterable[str] = ()) -> List[Tuple[str, int, str, str]]:
    """Every message that breaks the message rules: (path, line, rule, text)."""
    out = []
    for f in list(paths) or default_files():
        if not f.endswith(".py"):
            continue
        rel = _rel(f)
        with open(f, encoding="utf-8") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for line, rule, text in _message_problems(rel, tree, src.splitlines()):
            out.append((rel, line, rule, text))
    return out


def check_python(path: str, baseline: Optional[Counter] = None) -> List[Problem]:
    rel = _rel(path)
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    lines = src.splitlines()
    out = _dash_problems(rel, lines)
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return out + [(rel, exc.lineno or 0, "syntax error")]
    docs = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docs or not DOUBLE.search(node.value):
            continue
        first = getattr(node, "lineno", 0) or 0
        last = getattr(node, "end_lineno", first) or first
        span = range(first, last + 1)
        if any(_exempt(lines, n) for n in span):
            continue
        hit = next((n for n in span if 0 < n <= len(lines) and "--" in lines[n - 1]), first)
        out.append((rel, hit, "' -- ' in a string"))
    left = baseline if baseline is not None else load_baseline()
    for line, rule, text in _message_problems(rel, tree, lines):
        k = _key(rel, rule, text)
        if left.get(k, 0) > 0:
            left[k] -= 1
            continue
        out.append((rel, line, "%s: %r" % (rule, " ".join(text.split())[:60])))
    return out


_CODE_SPAN = re.compile(r"`[^`\n]*`")


def check_markdown(path: str) -> List[Problem]:
    rel = _rel(path)
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out = _dash_problems(rel, lines)
    fenced = False
    for n, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or line.rstrip().endswith(EXEMPT):
            continue
        # an HTML comment is a note to authors, never shown
        text = re.sub(r"<!--.*?-->", "", _CODE_SPAN.sub("", line))
        if DOUBLE.search(text):
            out.append((rel, n, "' -- ' in the text"))
    return out


def check_text(path: str) -> List[Problem]:
    rel = _rel(path)
    with open(path, encoding="utf-8") as fh:
        return _dash_problems(rel, fh.read().splitlines())


def default_files() -> List[str]:
    files = []
    for dp, dn, fn in os.walk(os.path.join(ROOT, "ctgedit")):
        dn[:] = [d for d in dn if d != "__pycache__"]
        files += [os.path.join(dp, f) for f in fn if f.endswith(".py")]
    pages = os.path.join(ROOT, "ctgedit", "help", "pages")
    if os.path.isdir(pages):
        files += [os.path.join(pages, f) for f in os.listdir(pages) if f.endswith(".md")]
    for extra in (os.path.join(ROOT, "ctgedit", "help", "toc.json"), os.path.join(ROOT, "README.md")):
        if os.path.isfile(extra):
            files.append(extra)
    return sorted(files)


def check_file(path: str, baseline: Optional[Counter] = None) -> List[Problem]:
    if path.endswith(".py"):
        return check_python(path, baseline)
    if path.endswith(".md"):
        return check_markdown(path)
    return check_text(path)


def check(paths: Iterable[str] = ()) -> List[Problem]:
    files: List[str] = []
    for p in paths:
        if os.path.isdir(p):
            for dp, dn, fn in os.walk(p):
                dn[:] = [d for d in dn if d != "__pycache__"]
                files += [os.path.join(dp, f) for f in fn if f.endswith((".py", ".md", ".json"))]
        else:
            files.append(p)
    out: List[Problem] = []
    baseline = load_baseline()
    for f in files or default_files():
        out += check_file(f, baseline)
    return out


def write_baseline(path: str = BASELINE) -> int:
    """The message rules' offenders now, as the baseline."""
    rows = sorted(_key(rel, rule, text) for rel, _line, rule, text in message_offenders())
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "# Messages that break the message rules of tools/check_ui_text.py, accepted "
            "until their\n# package fixes them: remove a line when you fix its message "
            "(path | rule | the text).\n"
        )
        for r in rows:
            fh.write(r + "\n")
    return len(rows)


def stale_baseline() -> List[str]:
    """Baseline lines no message matches any more (fixed: remove them)."""
    left = load_baseline()
    for rel, _line, rule, text in message_offenders():
        k = _key(rel, rule, text)
        if left.get(k, 0) > 0:
            left[k] -= 1
    return sorted(k for k, n in left.items() for _ in range(n))


def main(argv: List[str]) -> int:
    if "--write-baseline" in argv:
        print("%d message(s) in the baseline" % write_baseline())  # text-ok
        return 0
    if "--stale" in argv:
        rows = stale_baseline()
        for r in rows:
            print(r)
        return 1 if rows else 0
    problems = check(argv)
    for rel, line, what in problems:
        print("%s:%d: %s" % (rel, line, what))
    if problems:
        print("%d problem(s) in %d file(s)" % (len(problems), len({p[0] for p in problems})))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
