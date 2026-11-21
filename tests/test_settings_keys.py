"""Every settings key is declared once."""

import ast
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit")


def _uses():
    for dp, _dn, fns in os.walk(ROOT):
        if "__pycache__" in dp:
            continue
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dp, fn)
            rel = os.path.relpath(path, os.path.dirname(ROOT))
            for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("value", "setValue")
                ):
                    continue
                if (
                    not node.args
                    or not isinstance(node.args[0], ast.Constant)
                    or not isinstance(node.args[0].value, str)
                    or "/" not in node.args[0].value
                ):
                    continue
                default = None
                if node.func.attr == "value" and len(node.args) > 1:
                    try:
                        default = ("lit", ast.literal_eval(node.args[1]))
                    except ValueError:
                        default = None
                yield node.args[0].value, default, "%s:%d" % (rel, node.lineno)


def test_every_settings_key_is_declared_with_one_default():
    from ctgedit.ui.prefs import settings as ST
    from ctgedit.ui.simulate import options as SO

    sim = {k: d for k, d in SO.OPTIONS.values()}
    bad = []
    for key, default, where in _uses():
        if key.startswith("opt/") and key[4:] in ST.OPTION_DEFAULTS:
            declared = ST.OPTION_DEFAULTS[key[4:]]
        elif key in ST.SETTINGS_KEYS:
            declared = ST.SETTINGS_KEYS[key]
        elif key in sim:
            declared = sim[key]
        else:
            bad.append("%s: %s is not declared" % (where, key))
            continue
        if default is not None and declared is not None and default[1] != declared:
            bad.append("%s: %s read with %r, declared %r" % (where, key, default[1], declared))
    assert not bad, "\n".join(bad)


def test_every_settings_key_has_an_export_group():
    """Settings > Export / Import go by group (ui/prefs/settings.KEY_GROUPS): every declared key and
    every option resolves to one of GROUP_NAMES."""
    from ctgedit.ui.prefs import settings as ST
    from ctgedit.ui.simulate import options as SO

    keys = (
        list(ST.SETTINGS_KEYS)
        + ["opt/" + k for k in ST.OPTION_DEFAULTS]
        + [k for k, _d in SO.OPTIONS.values()]
        + ["keys/act_undo", "mru", "geometry/X"]
    )
    bad = [k for k in keys if ST.key_group(k, None) not in ST.GROUP_NAMES]
    assert not bad, bad
    assert ST.key_group("keys/act_undo") == "Keyboard shortcuts"
    assert ST.key_group("ui/prefs/keymap") == "Keyboard shortcuts"
    assert ST.key_group("ui/ribbon_custom") == "Ribbon and Quick Access"
    assert ST.key_group("view/grid_size") == "Guidelines and grid"
    assert ST.key_group("win/state") == "Window layout and workspaces"


def test_every_key_is_declared_once():
    """A key twice in one table (two packages each declaring it) keeps the last default
    silently: every declaration table of ui/prefs/settings.py names a key once."""
    import ast
    from ctgedit.ui.prefs import settings as ST

    with open(ST.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    twice = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            twice += ["line %d: %s" % (node.lineno, k) for k in sorted(set(keys)) if keys.count(k) > 1]
    assert not twice, twice
