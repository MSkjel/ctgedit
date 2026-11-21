"""The page model export runs off the GUI thread."""

import os

import pytest
from qtkit import app as _app

#: the text the tests write into FxSimValue01's TXT_STR (a tag FxSimBig does not show)
EDITED = "FX_SIM_NAME"


@pytest.fixture
def big(fx_page):
    """The path of FxSim's FxSimBig in the test's copy of the fake install."""
    return fx_page("FxSimBig", "FxSim")


def _value(doc):
    """The record of FxSimValue01 (a number display), found by its name."""
    hits = doc.find_objects_by_name("FxSimValue01")
    assert len(hits) == 1
    return hits[0]


def _norm(m):
    import json

    m = json.loads(json.dumps(m))
    m.pop("generator", None)
    return m


def test_the_helper_exports_the_model_the_editor_would(big):
    src = big
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.simulate import modeljob

    doc = EditableDocument.open(src)
    job = modeljob.start(doc)
    got = job.result(timeout=120)
    assert got is not None, job.error
    assert _norm(got) == _norm(doc.export_page_model())
    assert got["source"] == "saved" and job.seconds is not None


def test_an_unsaved_documents_model_comes_from_its_own_bytes(big):
    src = big
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.simulate import modeljob

    doc = EditableDocument.open(src)
    i = _value(doc)
    assert EDITED not in str(doc.export_page_model()["tables"])
    assert doc.set_property(i, "TXT_STR", EDITED)
    assert doc.dirty and modeljob.unsaved(doc)
    got = modeljob.start(doc).result(timeout=120)
    want = doc.export_page_model()
    assert got["source"] == want["source"] == "unsaved"
    assert not got["compiled"].get("current")
    assert _norm(got) == _norm(want)
    assert EDITED in str(got["tables"])  # (the edit's row)


def test_a_failed_helper_gives_no_model(tmp_path):
    from ctgedit.ui.simulate import modeljob

    job = modeljob.ModelJob(str(tmp_path / "NoSuchPage.ctg"), b"not a page")
    assert job.result(timeout=120) is None
    assert "failed" in job.error


def test_the_simulate_window_hands_the_job_to_the_connection(big, monkeypatch):
    src = big
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.simulate import window as W
    from ctgedit.ui.simulate.ctsim_source import EngineSimulation
    from ctgedit.ui.simulate.modeljob import ModelJob

    monkeypatch.delenv("CTG_SIM_MODEL_INPROCESS", raising=False)
    doc = EditableDocument.open(src)
    job = W.page_model_job(src, doc)
    assert isinstance(job, ModelJob)
    assert W.page_model_job(src, doc) is job  # kept for the next tab
    model = W.page_model(src, doc)  # (the job's model)
    assert _norm(model) == _norm(doc.export_page_model())
    assert W.page_model_job(src, doc) is model
    sim = EngineSimulation()
    sim.model = W.page_model_job(src, doc)
    assert sim._model_value() is model and sim.model is model
    sim.model = ModelJob(src, b"not a page")
    assert sim._model_value() is None and sim.model is None
    assert any("drawing data was not made" in e.text for e in sim._log)


def test_the_runtime_derive_reads_the_editors_parses(big):
    src = big
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.io import reader
    from ctgedit.ui.simulate import window as W

    doc = EditableDocument.open(src)
    ed = doc._derivation
    with reader.derive_options(label_mode=W.runtime_label_mode(), composite_members=True):
        reader.clear_fresh_memo()
        page, der = reader.derive_page(doc.parsed, src, shadows=ed)
    assert all(a is b for a, b in zip(der.shadow, ed.shadow))
    assert not reader.model_difference(page, W.runtime_page(src))
    assert not reader.model_difference(W.runtime_page(src, doc.parsed, doc), page)
    # an edited record is parsed afresh, the others still come from the editor's derive
    i = _value(doc)
    assert doc.set_property(i, "TXT_STR", EDITED)
    with reader.derive_options(label_mode=W.runtime_label_mode(), composite_members=True):
        reader.clear_fresh_memo()
        page2, der2 = reader.derive_page(doc.parsed, src, shadows=doc._derivation)
        reader.clear_fresh_memo()
        page3, _d = reader.derive_page(doc.parsed, src)
    assert not reader.model_difference(page2, page3)


def test_a_files_model_is_made_in_the_helper_and_kept(fx_page, monkeypatch):
    # a page the simulator opened (its startup page, a popup) whose file is not open in the
    # editor: the model comes from the helper as well, never exported on the GUI thread
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.simulate import window as W
    from ctgedit.ui.simulate.modeljob import ModelJob

    monkeypatch.delenv("CTG_SIM_MODEL_INPROCESS", raising=False)
    W._MODEL_CACHE.clear()
    # (a small page's model is made here: quicker than the helper's start)
    about = fx_page("FxSim1", "FxSim")
    src = fx_page("FxHuge", "FxPerf")
    assert os.path.getsize(about) < W.FILE_JOB_MIN_BYTES <= os.path.getsize(src)
    small = W.page_model_file_job(about)
    assert isinstance(small, dict) and W.page_model_cached(about) is small
    W._MODEL_CACHE.clear()
    monkeypatch.setattr(W, "FILE_JOB_MIN_BYTES", 0)
    made = []
    monkeypatch.setattr(W, "page_model", lambda *a, **k: made.append(a) or None)
    job = W.page_model_file_job(src)
    assert isinstance(job, ModelJob)
    assert W.page_model_file_job(src) is job  # kept while it runs
    got = job.result(timeout=120)
    assert got is not None, job.error
    assert W.page_model_file_job(src) is got
    assert W.page_model_cached(src) is got and made == []
    monkeypatch.undo()
    W._MODEL_CACHE.clear()
    assert _norm(got) == _norm(EditableDocument.open(src).export_page_model())


def test_simulate_page_starts_the_model_before_the_runtime_derive(big, monkeypatch):
    src = big
    # the helper makes the model while the GUI thread derives the page and builds its scene
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.simulate import ctsim_source, sources
    from ctgedit.ui.simulate import window as W

    order = []

    class Sim(sources.FakeSimulation):
        model = None

    real_derive = W.runtime_page
    monkeypatch.delenv("CTG_NO_SIM_ENGINE", raising=False)
    monkeypatch.setattr(ctsim_source, "prestart", lambda *a, **k: None)
    monkeypatch.setattr(sources, "default_simulation", lambda engine_path=None: Sim())
    monkeypatch.setattr(W, "page_model_job", lambda path, doc: order.append("model") or {"page": "FxSimBig"})
    monkeypatch.setattr(W, "runtime_page", lambda *a, **k: order.append("derive") or real_derive(*a, **k))
    doc = EditableDocument.open(src)
    win = W.SimulateWindow()
    try:
        sp = win.open_page(src, parsed=doc.parsed, doc=doc)
        assert order[:2] == ["model", "derive"]
        assert sp.sim.model == {"page": "FxSimBig"}
    finally:
        win.close()
        win.deleteLater()
