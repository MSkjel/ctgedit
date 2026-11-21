"""Closed-page search split over helper processes."""

import json
import os
import shutil
import time


ALL = ["expr", "name", "page", "param", "text"]


def _req(text, regex=False, projects=(), part=None):
    from ctgedit.workers import find_worker as FW

    r = {
        "format": FW.FORMAT,
        "projects": list(projects),
        "options": {
            "text": text,
            "scopes": ALL,
            "match": "part",
            "inside_genies": True,
            "inside_templates": False,
            "template_editable": False,
            "break_lock": False,
            "regex": regex,
        },
    }
    if part is not None:
        r["part"] = part
    return r


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_merged_parts_answer_as_one_search(monkeypatch, fx_project):
    from ctgedit.workers import find_worker as FW

    main = fx_project("FxMain")
    pages = FW.pages_of([main])[:20]
    assert len(pages) == 20
    monkeypatch.setattr(FW, "pages_of", lambda projects: pages)
    skip = [pages[3]]
    for cap in (FW.MAX_ROWS, 25):
        monkeypatch.setattr(FW, "MAX_ROWS", cap)
        for text, regex in (("pump", False), ("Pu.p", True), (r"^\d+$", True)):
            one = FW.find(dict(_req(text, regex, projects=[main]), skip=skip))
            assert len(one["rows"]) >= min(cap, 26), (cap, text, len(one["rows"]))
            for n in (2, 3):
                parts = [
                    FW.find(dict(_req(text, regex, projects=[main], part=[k, n]), skip=skip))
                    for k in range(n)
                ]
                assert _same(FW.merge(parts), one), (cap, text, n)


def test_the_merge_cuts_at_the_limit_and_where_a_part_stopped():
    from ctgedit.workers import find_worker as FW

    rows = lambda i, n: [{"page": "p%d" % i, "n": j} for j in range(n)]  # noqa: E731
    a = {
        "format": FW.FORMAT,
        "pages": 5,
        "part": [0, 2],
        "records": [[0, "hits", rows(0, 2)], [2, "failed", "P2: bad"], [4, "hits", rows(4, 1)]],
    }
    b = {"format": FW.FORMAT, "pages": 5, "part": [1, 2], "records": [[1, "skip"], [3, "hits", rows(3, 3)]]}
    m = FW.merge([a, b])
    assert [r["page"] for r in m["rows"]] == ["p0", "p0", "p3", "p3", "p3", "p4"]
    assert (m["searched"], m["failed"], m["more"]) == (3, ["P2: bad"], False)
    # a part stopped (Stop) after its first page: the list ends where its pages end
    b2 = dict(b, records=[[1, "skip"]], stopped=True)
    m = FW.merge([a, b2])
    assert [r["page"] for r in m["rows"]] == ["p0", "p0"] and m["stopped"]
    assert m["searched"] == 1 and m["failed"] == ["P2: bad"]
    # an error of a part is the answer
    assert FW.merge([a, {"format": FW.FORMAT, "error": "boom"}])["error"] == "boom"


def test_a_part_stops_once_its_rows_pass_the_limit(monkeypatch, fx_project):
    from ctgedit.workers import find_worker as FW

    main = fx_project("FxMain")
    pages = FW.pages_of([main])[:20]
    monkeypatch.setattr(FW, "pages_of", lambda projects: pages)
    monkeypatch.setattr(FW, "MAX_ROWS", 5)
    part = FW.find(_req("e", projects=[main], part=[0, 2]))
    found = [len(r[2]) for r in part["records"] if r[1] == "hits"]
    assert sum(found) > 5 and sum(found[:-1]) <= 5
    assert len(part["records"]) < len(range(0, 20, 2))


def test_the_setting_reads_automatic_and_its_range(monkeypatch):
    from ctgedit.ui.prefs import settings as S

    st = S.app_settings()
    try:
        st.setValue("opt/find_processes", 0)
        auto = S.find_processes()
        assert auto == max(1, min(4, (os.cpu_count() or 2) // 2))
        st.setValue("opt/find_processes", 3)
        assert S.find_processes() == 3
        st.setValue("opt/find_processes", 99)
        assert S.find_processes() == S.FIND_PROCESSES_RANGE[1]
        st.setValue("opt/find_processes", "garbled")
        assert S.find_processes() == auto
    finally:
        st.remove("opt/find_processes")


def test_helper_jobs_search_in_processes_and_answer_as_one(tmp_path, fx_install):
    """Two helper processes on copies of 12 FxMain pages (a new project of the fake
    install that includes FxLib and FxStyle, without pages.DBF: every page counts), run as
    the Find dialog runs them, against one search in this process. The processes inherit
    CTG_SCADA_ROOT (the fake install)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.workers import find_worker as FW
    from ctgedit.ui.jobs.helper_job import HelperJobs

    proj = fx_install.new_project("FxFind", tables=False)
    for p in FW.pages_of([fx_install.project("FxMain")])[:12]:
        shutil.copyfile(p, os.path.join(proj, os.path.basename(p)))
    req = dict(_req("pump", projects=[proj]), mode="find")
    want = FW.find(req)
    assert want["rows"]
    got, progress = [], []
    job = HelperJobs(None, FW.command, req, str(tmp_path / "jobs"), 2, len(FW.pages_of([proj])), FW.merge)
    job.progress.connect(lambda i, n, name: progress.append((i, n)))
    job.finished.connect(got.append)
    assert job.start()
    end = time.monotonic() + 120
    while not got and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.05)
    assert got, "no answer"
    assert _same(got[0], want)
    assert all(n == 12 for _i, n in progress)
    assert not os.listdir(tmp_path / "jobs")  # (the request / answer files go)
