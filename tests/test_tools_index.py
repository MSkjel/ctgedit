r"""tools/README.md lists every script in tools/. Regenerate it after adding or removing one:

.venv\Scripts\python.exe tests\test_tools_index.py --write
"""

import ast
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(REPO, "tools", "README.md")
HEAD = """# tools/

Every script here, with the first sentence of its docstring. Scripts that drive a live
Graphics Builder are in tools/live/. Regenerate the list after adding or removing a tool:

    .venv\\Scripts\\python.exe tests\\test_tools_index.py --write
"""


def _tools():
    out = subprocess.run(
        ["git", "ls-files", "tools"], cwd=REPO, capture_output=True, text=True
    ).stdout.split()
    return sorted(
        f
        for f in out
        if f.endswith(".py") and not f.endswith("__init__.py") and os.path.isfile(os.path.join(REPO, f))
    )


def _summary(path):
    src = open(os.path.join(REPO, path), encoding="utf-8-sig").read()
    doc = ast.get_docstring(ast.parse(src)) or ""
    first = " ".join(doc.strip().split("\n\n")[0].split())
    first = first.replace("\u2014", ",").replace("\u2013", "-").replace(" -- ", ": ")
    if len(first) > 150:
        first = first[:147].rsplit(" ", 1)[0] + " ..."
    return first or "(no docstring)"


def render():
    groups = {}
    for f in _tools():
        groups.setdefault(os.path.dirname(f).replace("\\", "/"), []).append(f)
    out = [HEAD]
    for g in sorted(groups):
        out.append("## %s/\n" % g)
        out += ["- `%s`: %s" % (os.path.basename(f), _summary(f)) for f in groups[g]]
        out.append("")
    return "\n".join(out)


def _listed():
    text = open(README, encoding="utf-8").read()
    names, group = set(), ""
    for line in text.splitlines():
        m = re.match(r"## (\S+)/$", line)
        if m:
            group = m.group(1)
        m = re.match(r"- `([^`]+\.py)`", line)
        if m:
            names.add(group + "/" + m.group(1))
    return names


def test_every_tool_is_listed_and_every_listed_tool_exists():
    have = set(_tools())
    listed = _listed()
    assert not sorted(have - listed), "not in tools/README.md (run --write): %s" % sorted(have - listed)
    assert not sorted(listed - have), "listed but gone (run --write): %s" % sorted(listed - have)


if __name__ == "__main__" and "--write" in sys.argv:
    with open(README, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render())
    print("%d tools listed" % len(_tools()))
