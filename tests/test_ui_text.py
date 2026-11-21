"""UI text rules: no em or en dashes."""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

import check_ui_text as C  # noqa: E402

#: files allowed to fail the check while a change is in progress (keep empty)
PENDING: set = set()


def test_no_dashes_in_code_manual_or_readme():
    problems = [p for p in C.check() if p[0] not in PENDING]
    assert not problems, "\n".join("%s:%d: %s" % p for p in problems)


def test_the_check_finds_what_it_should(tmp_path):
    py = tmp_path / "sample.py"
    py.write_text(
        '"""A docstring -- fine."""\n'
        'A = "one -- two"\n'
        'B = "kept -- here"  # text-ok\n'
        'C = ["git", "--", "path"]\n'
        "# a comment " + chr(0x2014) + " not fine\n"
        'D = f"{A} -- {B}"\n',
        encoding="utf-8",
    )
    got = sorted((line, what) for _rel, line, what in C.check_python(str(py)))
    assert got == [(2, "' -- ' in a string"), (5, "em dash"), (6, "' -- ' in a string")]
    md = tmp_path / "page.md"
    md.write_text(
        "# Page\n\nText -- here.\n\nCode `a -- b` is fine.\n\n"
        "```\nx -- y\n```\n\nAn en dash " + chr(0x2013) + " no.\n"
        "<!-- note -- fine -->\n",
        encoding="utf-8",
    )
    got = sorted((line, what) for _rel, line, what in C.check_markdown(str(md)))
    assert got == [(3, "' -- ' in the text"), (11, "en dash")]


def test_pending_names_only_files_that_exist():
    missing = sorted(p for p in PENDING if not os.path.exists(os.path.join(REPO, p)))
    assert not missing, missing


def test_an_added_extension_asks_before_it_replaces_a_file(tmp_path, monkeypatch):
    """Save dialogs that add the extension after the dialog: the file with it is asked about
    (the dialog asked about the name it was given only)."""
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.messages import with_extension

    from ctgedit.ui import messages

    asked = []
    monkeypatch.setattr(messages, "choose", lambda *a, **k: asked.append(a[2]))
    base = str(tmp_path / "page")
    assert with_extension(None, "Export Page", base, ".png") == base + ".png" and not asked
    assert with_extension(None, "Export Page", base + ".PNG", ".png") == base + ".PNG"
    (tmp_path / "page.png").write_bytes(b"x")
    assert with_extension(None, "Export Page", base, ".png") is None
    assert asked and "page.png already exists" in asked[0]
    monkeypatch.setattr(messages, "choose", lambda *a, **k: a[3][0])
    assert with_extension(None, "Export Page", base, ".png") == base + ".png"
