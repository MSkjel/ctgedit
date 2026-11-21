"""Every broad exception handler on the read / render path notes what it swallowed."""

import ast
import glob
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit")

#: the read / render path
FILES = (
    "io/reader.py",
    "format/genie_place.py",
    "render/scene.py",
    "format/symbols.py",
    "format/ctf.py",
    "format/genie_norm.py",
    "format/placement.py",
    "render/sim_states.py",
    # (the rest of the read / render path)
    "io/readerparts/__init__.py",
    "io/readerparts/common.py",
    "io/readerparts/style.py",
    "io/readerparts/symbols.py",
    "io/readerparts/graft.py",
    "format/template_place.py",
    "format/geometry.py",
    "format/composites.py",
    "render/sim_drawn.py",
    "render/sim_alarmlists.py",
    "format/colourstate.py",
    "sim/pagemodel.py",
)

_NOTE_CALLS = (
    "note_exception",
    "_note_exc",
    "note_problem",
    "exception",
    "warning",
    "error",
    "debug",
    "info",
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


def test_the_read_and_render_path_notes_what_it_swallows():
    bad = [x for rel in FILES for x in silent_handlers(rel)]
    assert not bad, "%d silent handlers:\n%s" % (len(bad), "\n".join(bad))


BUDGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "swallow_budget.json")


def _all_modules():
    for p in sorted(glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True)):
        yield os.path.relpath(p, ROOT).replace(os.sep, "/")


def test_no_module_swallows_more_than_it_did():
    """Everywhere else, a ratchet:
    a module may not gain a silent handler; one it lost lowers its budget (re-write
    tests/swallow_budget.json with ``python tests/test_swallowed_errors.py``)."""
    budget = json.load(open(BUDGET, encoding="utf-8"))
    over = []
    for rel in _all_modules():
        n = len(silent_handlers(rel))
        if n > budget.get(rel, 0):
            over.append("%s: %d silent handlers (budget %d)" % (rel, n, budget.get(rel, 0)))
    assert not over, "\n".join(over)


if __name__ == "__main__":
    counts = {rel: n for rel in _all_modules() for n in [len(silent_handlers(rel))] if n}
    with open(BUDGET, "w", encoding="utf-8") as fh:
        json.dump(counts, fh, indent=1, sort_keys=True)
    print("%d silent handlers in %d modules" % (sum(counts.values()), len(counts)))
