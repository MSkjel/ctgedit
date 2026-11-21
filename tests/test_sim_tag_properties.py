"""The Tags panel's Properties tab."""

from ctgedit.sim import client as C
from qtkit import app as _app


class _Src:
    def __init__(self, rows, can=True):
        self.rows, self.can, self.calls, self.n = rows, can, [], 1

    def tags(self, page_only=False):
        return list(self.rows)

    def serial(self):
        return self.n

    def set_tag(self, *a):
        pass

    def can_set_tag_properties(self):
        return self.can

    def set_tag_property(self, name, field, value=None):
        self.calls.append((name, field, value))
        return True


def _tag(**kw):
    from ctgedit.ui.simulate.sources import TagInfo

    d = dict(
        name="Level",
        type="REAL",
        value=12.0,
        eng_zero=0.0,
        eng_full=100.0,
        units="%",
        format="###.#",
        comment="Tank level",
    )
    d.update(kw)
    return TagInfo(**d)


def test_the_properties_show_overrides_and_send_edits_and_resets():
    _app()
    from ctgedit.ui.simulate import panels as P

    src = _Src([_tag(eng_full=250.0, overrides={"ENG_FULL": "250"}, configured={"ENG_FULL": "100"})])
    p = P.InputsPanel()
    p.page_only.setChecked(False)
    p.set_source(src)
    assert p.select_tag("Level")
    lab, edit, reset = p.prop_rows["ENG_FULL"]
    assert edit.text() == "250" and edit.font().bold() and reset.isEnabled()
    assert "The project's value: 100" in edit.toolTip()
    assert p.prop_rows["FORMAT"][1].text() == "###.#"
    assert not p.prop_rows["FORMAT"][2].isEnabled()  # (not overridden)
    assert p.prop_reset_all.isEnabled() and "1 overridden" in p.prop_note.text()
    # an edit is sent; leaving a box unchanged sends nothing
    p.prop_rows["COMMENT"][1].setText("Test level")
    p._prop_edited("COMMENT")
    p._prop_edited("FORMAT")
    reset.click()
    p.prop_reset_all.click()
    assert src.calls == [
        ("Level", "COMMENT", "Test level"),
        ("Level", "ENG_FULL", None),
        ("Level", None, None),
    ]


def test_what_cannot_be_overridden_says_why():
    _app()
    from ctgedit.ui.simulate import panels as P

    for tag, can, why in (
        (_tag(), False, "not connected"),
        (_tag(name="U", defined=False), True, "does not define"),
    ):
        src = _Src([tag], can=can)
        p = P.InputsPanel()
        p.page_only.setChecked(False)
        p.set_source(src)
        assert p.select_tag(tag.name)
        assert not p.prop_rows["COMMENT"][1].isEnabled() and why in p.prop_note.text()
    assert "no tag properties" in p._props_settable(_tag(name="P", param=True))
    assert "no tag properties" in p._props_settable(_tag(name="V", page_var=True))


def test_a_tag_entry_carries_the_properties_and_their_overrides():
    n = C.normalise_tag(
        {
            "name": "Level",
            "format": "###.##",
            "rawZero": 0,
            "rawFull": 4095,
            "deadband": 0.5,
            "unit": "PLC1",
            "address": "D100",
            "custom3": "x",
            "overrides": {"FORMAT": "###.##"},
            "configured": {"FORMAT": "###.#"},
        }
    )
    assert (n["format"], n["raw_full"], n["deadband"], n["io_device"], n["address"]) == (
        "###.##",
        4095.0,
        0.5,
        "PLC1",
        "D100",
    )
    assert n["custom"][2] == "x" and n["overrides"] == {"FORMAT": "###.##"}
    assert C.Session.tag_property_args("Level", "FORMAT", "#.#") == {
        "name": "Level",
        "field": "FORMAT",
        "value": "#.#",
    }
    assert C.Session.tag_property_args("Level") == {"name": "Level"}


def test_selecting_a_tag_writes_nothing():
    # the Drive controls (a simulator that runs signal generators) set their boxes under the
    # same guard as the value control, so the slider never writes the selected tag's value back
    # (rounded to its steps)
    _app()
    import threading
    from ctgedit.ui.simulate import panels as P

    class Session:
        alive = True
        hello = {}

    class Src(_Src):
        def __init__(self, rows):
            super().__init__(rows)
            self._lock = threading.Lock()
            self._session = Session()
            self.writes = []

        def _add_log(self, entry):
            pass

        def set_tag(self, *a):
            self.writes.append(a)
            self.n += 1

    src = Src([_tag(value=12.3456), _tag(name="Flow", value=7.25)])
    p = P.InputsPanel()
    p.page_only.setChecked(False)
    p.set_source(src)
    assert p.drive_box.isVisibleTo(p) or p._tools().available()
    for name in ("Level", "Flow", "Level"):
        assert p.select_tag(name)
        assert p.selected_tag().name == name
    assert src.writes == []
