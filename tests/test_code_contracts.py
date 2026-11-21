"""Code rules, checked."""

import ast
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit")


def _files():
    for dp, _dn, fns in os.walk(ROOT):
        if "__pycache__" in dp:
            continue
        for fn in fns:
            if fn.endswith(".py"):
                yield os.path.join(dp, fn)


def test_no_parent_item_calls_on_scene_items():
    bad = []
    for path in _files():
        for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
            # (QTreeWidget.topLevelItem(i) takes an index: another API)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and (node.func.attr == "parentItem" or (node.func.attr == "topLevelItem" and not node.args))
            ):
                bad.append("%s:%d" % (os.path.relpath(path, ROOT), node.lineno))
    assert not bad, "\n".join(bad)


_MUTATE = {"append", "extend", "insert", "pop", "remove", "clear", "sort", "reverse"}


def test_no_bag_list_is_changed_in_place():
    bad = []
    for path in _files():
        for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
            # x.bags.append(...) and the like
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _MUTATE
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "bags"
            ):
                bad.append("%s:%d" % (os.path.relpath(path, ROOT), node.lineno))
            # x.bags[i] = ... / del x.bags[i]
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else []
            for t in targets:
                if (
                    isinstance(t, ast.Subscript)
                    and isinstance(t.value, ast.Attribute)
                    and t.value.attr == "bags"
                ):
                    bad.append("%s:%d" % (os.path.relpath(path, ROOT), node.lineno))
    assert not bad, "\n".join(bad)


def test_every_switch_the_scene_reads_is_part_of_render_env():
    from ctgedit.render import scene

    src = open(scene.__file__, encoding="utf-8").read()
    read = set(re.findall(r'environ\.get\("(CTG_[A-Z0-9_]+)"', src))
    body = src[src.index("def _render_env") :]
    body = body[: body.index("\n\n")]
    listed = set(re.findall(r'"(CTG_[A-Z0-9_]+)"', body))
    assert read <= listed, "read by the scene, not in _render_env: %s" % sorted(read - listed)


def test_the_search_popups_hold_no_hex_colours():
    """Quick Open, the command palette and their rows draw in the theme's colours: no
    '#rrggbb' in their code."""
    for name in ("dialog/quick_open.py", "dialog/palette.py", "search_rows.py"):
        src = open(os.path.join(ROOT, "ui", name), encoding="utf-8").read()
        bad = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                bad += re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", node.value)
        if name == "search_rows.py":
            # (the list's focus ring rule is written in the design accent: the theme maps it)
            bad = [b for b in bad if b.lower() != "#4da3ff"]
        assert not bad, "%s: %s" % (name, bad)


def test_a_message_box_check_box_has_the_box_as_its_parent():
    """QMessageBox.setCheckBox(cb) leaves a check box made without a parent owned by Python
    (PySide6 6.11): it is deleted when the method that made it returns while the box, a
    child of the window, still uses it, and the next theme change crashes the editor."""
    bad = []
    for path in _files():
        tree = ast.parse(open(path, encoding="utf-8").read())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            made = {}
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    f = node.value.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                    if name == "QCheckBox":
                        for t in node.targets:
                            if isinstance(t, ast.Name):
                                made[t.id] = node.value
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setCheckBox"
                    and node.args
                    and isinstance(node.args[0], ast.Name)
                ):
                    call = made.get(node.args[0].id)
                    if (
                        call is not None
                        and len(call.args) < 2
                        and not any(k.arg == "parent" for k in call.keywords)
                    ):
                        bad.append("%s:%d" % (os.path.relpath(path, ROOT), call.lineno))
    assert not bad, "a check box for setCheckBox without its box as parent: " + ", ".join(sorted(set(bad)))


def test_a_delayed_call_that_acts_on_a_qt_object_has_it_as_its_context():
    """QTimer.singleShot(ms, fn) whose fn is a lambda or a bound method: fn can run after
    its widget is gone (a window deleted in between), and calling into a deleted Qt object
    is an access violation. singleShot(ms, context, fn) is dropped with its context."""
    bad = []
    for path in _files():
        for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "singleShot"
                and len(node.args) == 2
                and (isinstance(node.args[1], ast.Lambda) or isinstance(node.args[1], ast.Attribute))
            ):
                bad.append("%s:%d" % (os.path.relpath(path, ROOT), node.lineno))
    assert not bad, "\n".join(bad)
