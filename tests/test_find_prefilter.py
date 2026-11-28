"""Closed-page search skips pages that can't match."""

import json
import os

import pytest

ALL = ("expr", "name", "page", "param", "text")
NEEDLES = [
    ("xyzzy", ALL, "part"),
    ("pump", ALL, "part"),
    ("PUMP", ALL, "word"),
    ("Tag", ("param",), "part"),
    ("Page", ("page",), "part"),
    ("_PV", ALL, "part"),
    ("Login", ALL, "part"),
]


@pytest.fixture
def main(fx_project):
    """FxMain of the fake install's copy."""
    return fx_project("FxMain")


def _req(text, scopes, match, projects):
    from ctgedit.workers import find_worker as FW

    return {
        "format": FW.FORMAT,
        "projects": list(projects),
        "options": {
            "text": text,
            "scopes": list(scopes),
            "match": match,
            "inside_genies": True,
            "inside_templates": False,
            "template_editable": False,
            "break_lock": False,
            "regex": False,
        },
    }


def _find(req, prefilter: bool, monkeypatch):
    from ctgedit.features import find_replace as FR
    from ctgedit.workers import find_worker as FW
    from ctgedit.infra import runmemo

    if not prefilter:
        monkeypatch.setattr(FR.PagePrefilter, "may_match", lambda self, path, data: True)
        try:
            return FW.find(req)
        finally:
            monkeypatch.undo()
    with runmemo.kept():
        return FW.find(req)


def test_the_answers_equal_a_search_of_every_page(main, monkeypatch):
    from ctgedit.workers import find_worker as FW

    pages = FW.pages_of([main])[:24]
    assert len(pages) == 24
    monkeypatch.setattr(FW, "pages_of", lambda projects: pages)
    hits = 0
    for text, scopes, match in NEEDLES:
        req = _req(text, scopes, match, [main])
        with monkeypatch.context() as m:
            old = _find(req, False, m)
        new = _find(req, True, monkeypatch)
        assert json.dumps(new, sort_keys=True) == json.dumps(old, sort_keys=True), text
        hits += bool(old.get("rows"))
    assert hits >= 4  # (most needles find something: the equality is not empty)


REGEX_NEEDLES = [
    (r"pump\w*", ALL, "part"),
    (r"^Tag", ("param",), "part"),
    (r"_PV$", ALL, "whole"),
    (r"Log(in|out)", ALL, "word"),
]


def test_a_page_it_skips_has_no_hits(main):
    from ctgedit.features import find_replace as FR
    from ctgedit.workers import find_worker as FW
    from ctgedit.editing import EditableDocument

    pages = FW.pages_of([main])[:30]
    skipped = 0
    for text, scopes, match, regex in [n + (False,) for n in NEEDLES] + [n + (True,) for n in REGEX_NEEDLES]:
        opts = FW.options_from_json(dict(_req(text, scopes, match, ())["options"], regex=regex))
        pf = FR.PagePrefilter(opts)
        for p in pages:
            with open(p, "rb") as fh:
                data = fh.read()
            if not pf.may_match(p, data):
                skipped += 1
                assert FR.find_hits(EditableDocument.from_bytes(data, p), opts) == [], (text, p)
    assert skipped > 50  # (it skips most pages for most texts)


def test_it_is_off_for_a_regex_without_a_known_text_or_a_non_ascii_text():
    from ctgedit.features import find_replace as FR
    from ctgedit.workers import find_worker as FW

    for text, regex in ((r"\d+", True), ("(pump|valve)", True), ("[", True), ("pømp", False), ("", False)):
        opts = FW.options_from_json(dict(_req(text, ALL, "part", ())["options"], regex=regex))
        pf = FR.PagePrefilter(opts)
        assert not pf.on
        assert pf.may_match(os.path.join("FxMain", "FxShapes.ctg"), b"")


def test_the_text_every_match_of_a_regex_holds():
    from ctgedit.features import find_replace as FR

    cases = {
        r"Pump\w*": "pump",
        r"^TAG_\d+_PV$": "tag_",  # (the longest run of plain characters)
        r"(?:Valve)+_(\d)": "valve",
        r"Valve(_1)?": "valve",  # (an optional part holds nothing for sure)
        r"(Pump|Valve)_1": "_1",
        r"x{2,}yz": "yz",
        r"x*yz": "yz",
        r"\d+": "",
        r"[abc]+": "",
    }
    for pattern, want in cases.items():
        assert FR.required_text(pattern) == want, pattern
    # the letters a case-blind expression matches a non-ASCII letter with break the text
    assert FR.required_text("Mixing", unicode=True) == "ng"
    assert FR.required_text("Fill_1", unicode=True) == "ll_1"


def test_a_regex_text_is_in_every_field_it_matches():
    # every match of the expression in a field holds required_text, in lower case
    import random
    import re

    from ctgedit.features import find_replace as FR

    rnd = random.Random(7)
    patterns = [r"Pump\w*", r"TAG_\d+", r"(?:ab)+c", r"x(yz)?w", r"(Mix)+ing", r"_PV\b", r"k\d", r"A.B"]
    alphabet = "abcxyzwPUMPTAGmixingkK_0123456789 .ıſK"
    for pattern in patterns:
        rx = re.compile(pattern, re.IGNORECASE)
        for unicode in (False, True):
            need = FR.required_text(pattern, unicode=unicode)
            for _ in range(3000):
                s = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 12)))
                for m in rx.finditer(s):
                    if unicode:
                        assert need in m.group(0).lower(), (pattern, s)
                    elif m.group(0).isascii():
                        assert need in m.group(0).lower(), (pattern, s)


def test_a_title_in_the_page_table_alone_is_found(main):
    # the page's pages.DBF row is searched (Page scope): a word of its title that the page
    # file does not hold keeps the page in the search
    from ctgedit.features import find_replace as FR
    from ctgedit.workers import find_worker as FW
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project

    pj = Project(main)
    found = None
    for p in FW.pages_of([main]):
        name = os.path.splitext(os.path.basename(p))[0]
        title = (pj.page_record(name) or {}).get("TITLE", "")
        with open(p, "rb") as fh:
            low = fh.read().lower()
        for word in title.split():
            if word.isascii() and len(word) > 3 and word.lower().encode() not in low:
                found = (p, word)
                break
        if found:
            break
    assert found is not None, "no FxMain page title word outside its page file"
    p, word = found
    opts = FW.options_from_json(_req(word, ("page",), "part", ())["options"])
    with open(p, "rb") as fh:
        data = fh.read()
    assert FR.PagePrefilter(opts).may_match(p, data)
    assert FR.find_hits(EditableDocument.from_bytes(data, p), opts)


def test_the_run_keeps_the_include_chain_and_only_while_it_runs(main):
    from ctgedit.format import projects as PJ
    from ctgedit.infra import runmemo

    assert runmemo.table("x") is None
    want = PJ.include_search_dirs(main)
    assert len(want) > 1  # (FxMain's chain: FxLib, FxStyle, Include ...)
    with runmemo.kept():
        assert PJ.include_search_dirs(main) == want
        memo = runmemo.table("projects.include_search_dirs")
        assert memo and list(memo.values())[0] == want
        got = PJ.include_search_dirs(main)
        got.append("changed by a caller")
        assert PJ.include_search_dirs(main) == want  # (a copy each time)
        with runmemo.kept():  # (nested: the same run)
            assert runmemo.table("projects.include_search_dirs") is memo
    assert runmemo.table("projects.include_search_dirs") is None
