"""Every environment variable is declared in infra/flags.py."""

import os
import re

from ctgedit.infra.flags import FLAGS

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit")
TESTS = os.path.dirname(os.path.abspath(__file__))
#: the test files that read a declared switch of their own (CTG_HERMETIC, CTG_FIXTURE_CHECK)
TEST_READERS = ("hermetic.py", "conftest.py", "shared_fixtures.py", "test_fixtures.py")
_NAME = re.compile(r"""["']((?:CTG|GP)_[A-Z0-9_]+)["']""")


def _names_in_code():
    out = {}
    for d, _sub, files in os.walk(ROOT):
        for f in files:
            if f.endswith(".py") and not (d.endswith("infra") and f == "flags.py"):
                p = os.path.join(d, f)
                for m in _NAME.finditer(open(p, encoding="utf-8").read()):
                    out.setdefault(m.group(1), os.path.relpath(p, ROOT))
    return out


def _names_in_test_plugins():
    out = {}
    for f in TEST_READERS:
        p = os.path.join(TESTS, f)
        for m in _NAME.finditer(open(p, encoding="utf-8").read()):
            out.setdefault(m.group(1), f)
    return out


def test_every_switch_the_code_reads_is_declared():
    used = _names_in_code()
    undeclared = sorted("%s (%s)" % (n, f) for n, f in used.items() if n not in FLAGS)
    assert not undeclared, "declare them in ctgedit/infra/flags.py:\n" + "\n".join(undeclared)


def test_every_declared_switch_is_read_somewhere():
    used = dict(_names_in_test_plugins(), **_names_in_code())
    stale = sorted(n for n in FLAGS if n not in used)
    assert not stale, "no longer read, take them out of infra/flags.py: %s" % stale


def test_each_has_a_kind_and_a_purpose():
    for name, (kind, what) in FLAGS.items():
        assert kind in ("config", "test", "debug") and len(what) > 10, name
