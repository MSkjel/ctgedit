"""Cross-module use of private names is limited to the allow list."""

import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), "ctgedit")
LIST = os.path.join(HERE, "private_uses.json")


def _module_of(path):
    rel = os.path.relpath(path, os.path.dirname(ROOT)).replace("\\", "/")[:-3]
    return rel.replace("/", ".").removesuffix(".__init__")


def _resolve(mod, level, name, is_pkg):
    if not level:
        return name
    base = mod.split(".")
    base = base if is_pkg else base[:-1]
    base = base[: len(base) - (level - 1)]
    return ".".join(base + ([name] if name else []))


#: modules that are one unit (the reader and its parts reach each other's names by design)
UNITS = (
    ("ctgedit.io.reader", "ctgedit.io.readerparts"),
    # EditableDocument and MainWindow split into mixins (their module-level helpers live
    # in the *_base modules and are re-exported): one class each
    ("ctgedit.editing", "ctgedit.editing_base", "ctgedit.doc"),
    ("ctgedit.ui.main_window", "ctgedit.ui.main_window_base", "ctgedit.ui.window"),
)


def _unit(name):
    for u in UNITS:
        if any(name == m or name.startswith(m + ".") for m in u):
            return u
    return name


def uses():
    out = set()
    for d, _sub, files in os.walk(ROOT):
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(d, f)
            mod = _module_of(p)
            is_pkg = f == "__init__.py"
            tree = ast.parse(open(p, encoding="utf-8").read())
            alias = {}
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom):
                    src = _resolve(mod, n.level, n.module or "", is_pkg)
                    for a in n.names:
                        target = src + "." + a.name if src else a.name
                        alias[a.asname or a.name] = target
                        # 'from x import _name' is a use too
                        if (
                            a.name.startswith("_")
                            and not a.name.startswith("__")
                            and src.startswith("ctgedit")
                            and src != mod
                            and not mod.startswith(src + ".")
                            and _unit(mod) != _unit(src)
                        ):
                            out.add("%s -> %s.%s" % (mod, src, a.name))
                elif isinstance(n, ast.Import):
                    for a in n.names:
                        if a.asname:
                            alias[a.asname] = a.name
            for n in ast.walk(tree):
                if (
                    isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name)
                    and n.attr.startswith("_")
                    and not n.attr.startswith("__")
                ):
                    target = alias.get(n.value.id)
                    if (
                        target
                        and target.startswith("ctgedit")
                        and target != mod
                        and not mod.startswith(target + ".")
                        and _unit(mod) != _unit(target)
                    ):
                        out.add("%s -> %s.%s" % (mod, target, n.attr))
    return out


def test_no_new_private_name_use_across_modules():
    allowed = set(json.load(open(LIST, encoding="utf-8")))
    now = uses()
    new = sorted(now - allowed)
    assert not new, (
        "a module uses another's private name (make it public, or keep it "
        "inside the module):\n  " + "\n  ".join(new)
    )
    gone = sorted(allowed - now)
    assert not gone, (
        "no longer used, take them out of tests/private_uses.json "
        "(run this file with --write):\n  " + "\n  ".join(gone)
    )


if __name__ == "__main__" and "--write" in sys.argv:
    with open(LIST, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(sorted(uses()), fh, indent=0)
    print(len(uses()), "uses written")
