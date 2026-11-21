"""The main window's parts (main_window, main_window_base, window/*) share state only through
attributes declared on ui/main_window_base.WindowState: an attribute one file sets and another
reads is declared there, and nothing declared there is stale."""

import ast
import collections
import glob
import os

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit", "ui")
FILES = sorted(glob.glob(os.path.join(UI, "window", "*.py"))) + [
    os.path.join(UI, "main_window.py"),
    os.path.join(UI, "main_window_base.py"),
]


def _shared():
    writes = collections.defaultdict(set)
    reads = collections.defaultdict(set)
    members = set()
    for f in FILES:
        tree = ast.parse(open(f, encoding="utf-8").read(), f)
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef) and n.name != "WindowState":
                for b in n.body:
                    if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        members.add(b.name)
                    elif isinstance(b, (ast.Assign, ast.AnnAssign)):
                        for t in b.targets if isinstance(b, ast.Assign) else [b.target]:
                            if isinstance(t, ast.Name):
                                members.add(t.id)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self":
                (writes if isinstance(n.ctx, ast.Store) else reads)[n.attr].add(f)
    return {a for a in writes if a not in members and (reads[a] - writes[a])}


def _declared():
    tree = ast.parse(open(os.path.join(UI, "main_window_base.py"), encoding="utf-8").read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WindowState")
    return {b.target.id for b in cls.body if isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name)}


def test_shared_window_state_is_declared():
    missing = sorted(_shared() - _declared())
    assert not missing, (
        "attributes one window file sets and another reads, not declared on "
        "main_window_base.WindowState: %s" % missing
    )


def test_no_stale_window_state_declarations():
    stale = sorted(_declared() - _shared())
    assert not stale, "declared on WindowState but no longer shared: %s" % stale


def test_the_window_has_the_declaration():
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.main_window_base import WindowState

    assert issubclass(MainWindow, WindowState)
