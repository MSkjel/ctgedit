"""Every broad exception handler notes what it swallowed, or says why it need not."""

import ast
import glob
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit")


_NOTE_CALLS = (
    "note_exception",
    "_note_exc",
    "note_problem",
    "exception",
    "warning",
    "error",
    "debug",
    "info",
    # ui/messages: the user is told what went wrong
    "warn",
    "inform",
)


def _broad(h: ast.ExceptHandler) -> bool:
    t = h.type
    if t is None:
        return True
    names = [t] if not isinstance(t, ast.Tuple) else list(t.elts)
    return any(isinstance(n, ast.Name) and n.id in ("Exception", "BaseException") for n in names)


def _notes(h: ast.ExceptHandler) -> bool:
    for node in ast.walk(ast.Module(body=h.body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            if name in _NOTE_CALLS:
                return True
            # ui/messages.error_box(parent, title, text, exc, ...) notes the exception it is
            # given (logsetup.note_exception) and shows it in its details
            if name == "error_box" and (len(node.args) > 3 or any(kw.arg == "exc" for kw in node.keywords)):
                return True
    return False


def silent_handlers(rel: str):
    path = os.path.join(ROOT, rel)
    src = open(path, encoding="utf-8").read()
    lines = src.split("\n")
    out = []
    for node in ast.walk(ast.parse(src, path)):
        if isinstance(node, ast.ExceptHandler) and _broad(node):
            line = lines[node.lineno - 1]
            if "swallow-ok" in line or _notes(node):
                continue
            out.append("%s:%d" % (rel, node.lineno))
    return out


def _all_modules():
    for p in sorted(glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True)):
        yield os.path.relpath(p, ROOT).replace(os.sep, "/")


def test_no_module_swallows_an_error_silently():
    """Every broad handler, everywhere, logs what it swallowed (note_exception) or says
    why it need not (a swallow-ok reason on the except line)."""
    bad = [x for rel in _all_modules() for x in silent_handlers(rel)]
    assert not bad, "%d silent handlers:\n%s" % (len(bad), "\n".join(bad))
