"""Package layers hold, including imports inside functions."""

import ast
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit")

#: the editing layer and above: EditableDocument (editing, its helpers in editing_base and its
#: mixins in doc/), the features and helper processes on documents (features/, workers/), the
#: built-in help, the UI and the app
UPPER = ("editing", "editing_base", "doc", "features", "workers", "help", "ui", "app")

#: what a package may not import (its own name excluded)
FORBIDDEN = {
    "format": ("io", "render", "backend", "sim", "diag") + UPPER,
    "model": ("io", "render", "backend", "sim", "diag") + UPPER,
    "infra": ("io", "render", "backend", "sim", "diag") + UPPER,
    "io": ("render", "backend", "sim", "diag") + UPPER,
    "render": UPPER,
    "backend": UPPER,
    "sim": UPPER,
    "diag": ("help", "ui", "app"),
    "doc": ("help", "ui", "app"),
}
#: packages that may not import Qt at all
NO_QT = ("format", "model", "infra", "io")
#: (file, imported module) crossings that are the design, with why
ALLOWED = {
    # the batch workflows open, update and save documents (EditableDocument)
    ("ctgedit/io/updatepages.py", "ctgedit.editing"),
    ("ctgedit/io/genieusage.py", "ctgedit.editing"),
    # a recovery copy is a document: it is restored as one
    ("ctgedit/infra/autosave.py", "ctgedit.editing"),
    # its job: run Python's GC on Qt's GUI thread only (a QTimer)
    ("ctgedit/infra/gcguard.py", "PySide6"),
    # the canvas labels a page's workspace panes: features/sa_workspace reads the model only
    ("ctgedit/render/scene.py", "ctgedit.features"),
}


def _imports(path, pkg_parts):
    """(target module, line) of every import in the file, relative ones resolved."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg_parts[: len(pkg_parts) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
                if node.module is None:
                    for a in node.names:
                        yield mod + "." + a.name, node.lineno
                    continue
                yield mod, node.lineno
            else:
                yield node.module or "", node.lineno


def _violations():
    out = []
    for dp, _dn, fns in os.walk(ROOT):
        if "__pycache__" in dp:
            continue
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dp, fn)
            rel = os.path.relpath(path, os.path.dirname(ROOT)).replace("\\", "/")
            parts = rel[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            layer = parts[1] if len(parts) > 1 else ""
            pkg_parts = parts[:-1] if not rel.endswith("__init__.py") else parts
            for target, line in _imports(path, pkg_parts):
                t = target.split(".")
                if (rel, ".".join(t[:2])) in ALLOWED or (rel, t[0]) in ALLOWED:
                    continue
                if layer in NO_QT and t[0] in ("PySide6", "shiboken6"):
                    out.append("%s:%d imports %s" % (rel, line, target))
                if t[0] == "ctgedit" and len(t) > 1 and t[1] in FORBIDDEN.get(layer, ()):
                    out.append("%s:%d imports %s" % (rel, line, target))
    return out


def test_lower_layers_never_import_upper_ones_or_qt():
    bad = _violations()
    assert not bad, "\n".join(bad)
