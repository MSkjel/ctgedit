"""The startup page's drawing is made as the Simulate window opens: the page the simulator of a
project asked for first last time (window.prefetch_startup_drawing)."""

import os

from ctgedit.ui.simulate import window as W


def _page(tmp_path, name):
    p = tmp_path / "Proj" / (name + ".ctg")
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(b"page")
    return str(p)


def test_the_remembered_startup_page_is_drawn_early(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(W, "page_model_file_job", lambda path: made.append(path))
    mixer, menu = _page(tmp_path, "Mixer"), _page(tmp_path, "Menu")
    W.prefetch_startup_drawing(mixer)
    assert made == []  # nothing remembered yet
    W.note_startup_drawing(mixer, menu)
    W.prefetch_startup_drawing(mixer)
    assert made == [menu]
    # another page of the same project: the same startup page
    W.prefetch_startup_drawing(_page(tmp_path, "Other"))
    assert made == [menu, menu]


def test_no_early_drawing_of_the_page_itself_or_a_file_that_is_gone(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(W, "page_model_file_job", lambda path: made.append(path))
    menu, mixer = _page(tmp_path, "Menu"), _page(tmp_path, "Mixer")
    W.note_startup_drawing(mixer, menu)
    W.prefetch_startup_drawing(menu)  # the page simulated is the startup page
    assert made == []
    os.remove(menu)
    W.prefetch_startup_drawing(mixer)
    assert made == []


def test_an_unreadable_memory_is_none(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(W, "page_model_file_job", lambda path: made.append(path))
    f = W._startup_drawings_file()
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    W.prefetch_startup_drawing(_page(tmp_path, "Mixer"))
    assert made == []
    W.note_startup_drawing(_page(tmp_path, "Mixer"), _page(tmp_path, "Menu"))
    W.prefetch_startup_drawing(_page(tmp_path, "Mixer"))
    assert len(made) == 1
