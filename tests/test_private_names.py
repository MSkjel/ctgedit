"""No module uses another module's private names."""

import ast
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), "ctgedit")


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
                            and mod != src
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
                        and mod != target
                    ):
                        out.add("%s -> %s.%s" % (mod, target, n.attr))
    return out


def test_no_private_name_use_across_modules():
    now = sorted(uses())
    assert not now, (
        "a module uses another's private name (make it public, or keep it "
        "inside the module):\n  " + "\n  ".join(now)
    )
