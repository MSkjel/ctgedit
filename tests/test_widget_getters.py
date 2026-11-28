"""No widget class hides one of Qt's own getters (style(), rect(), font() ...) behind a method
or an attribute of its own: Qt's code and ours (the theme, accessibility, the dialog checks)
ask every widget for them."""

import ast
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit")
#: QWidget getters a widget class must leave as they are
GETTERS = frozenset(
    (
        "style",
        "rect",
        "font",
        "palette",
        "layout",
        "window",
        "size",
        "width",
        "height",
        "pos",
        "geometry",
        "parent",
        "children",
    )
)


#: Qt base classes that are not widgets (a model's parent(index) is its own API)
NOT_WIDGETS = (
    "Model",
    "Delegate",
    "Validator",
    "Style",
    "Object",
    "Thread",
    "Item",
    "Highlighter",
    "Event",
    "Layout",
    "Action",
)


def _qt_widget(cls: ast.ClassDef) -> bool:
    for b in cls.bases:
        name = b.attr if isinstance(b, ast.Attribute) else b.id if isinstance(b, ast.Name) else ""
        if name.startswith("Q") and not any(k in name for k in NOT_WIDGETS):
            return True
        if name.endswith(("Dialog", "Widget", "Window")):
            return True
    return False


def test_no_widget_hides_a_qt_getter():
    bad = []
    for dp, _dn, fn in os.walk(ROOT):
        for f in fn:
            if not f.endswith(".py"):
                continue
            path = os.path.join(dp, f)
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and _qt_widget(n)):
                where = "%s:%s" % (os.path.relpath(path, ROOT), cls.name)
                for node in ast.walk(cls):
                    if isinstance(node, ast.FunctionDef) and node.name in GETTERS:
                        bad.append("%s.%s()" % (where, node.name))
                    elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                        if (
                            isinstance(node.value, ast.Name)
                            and node.value.id == "self"
                            and node.attr in GETTERS
                        ):
                            bad.append("%s: self.%s =" % (where, node.attr))
    assert not bad, "\n".join(sorted(set(bad)))
