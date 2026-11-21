"""Every '_' key on a GraphicObject's raw dict is declared."""

import os
import re

from ctgedit.model.extras import RAW_EXTRAS

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit")
_USE = re.compile(
    r"""raw(?:\.get\(|\[|\.setdefault\(|\.pop\()\s*["'](_[A-Za-z][A-Za-z0-9_]*)["']"""
    r"""|["'](_[A-Za-z][A-Za-z0-9_]*)["']\s+(?:not\s+)?in\s+(?:\w+\.)*raw\b"""
)
# (``(?:\w+\.)*raw``: ``obj.raw``, not a record's ``body_raw`` bytes, where CtDraw's own
# '_Library' key of a placeholder is looked for)


def _used():
    out = {}
    for d, _sub, files in os.walk(ROOT):
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(d, f)
                for m in _USE.finditer(open(p, encoding="utf-8").read()):
                    out.setdefault(m.group(1) or m.group(2), p)
    return out


def test_every_raw_extra_is_declared():
    used = _used()
    missing = sorted(k for k in used if k not in RAW_EXTRAS)
    assert not missing, "declare in ctgedit/model/extras.py: %s" % ", ".join(
        "%s (%s)" % (k, os.path.relpath(used[k], ROOT)) for k in missing
    )


def test_no_declared_extra_is_unused():
    used = _used()
    unused = sorted(k for k in RAW_EXTRAS if k not in used)
    assert not unused, "no code uses these any more (remove them): %s" % ", ".join(unused)
