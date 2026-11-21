"""The genie dialog's OK and pasting into a genie, against CtDraw."""

import os

import fxinstall


def _lib(tmp_path, monkeypatch):
    from ctgedit.editing import ElementDocument

    monkeypatch.setenv("CTG_LIB_PATH", str(tmp_path))
    lib = str(tmp_path / "_r8.ctm")
    for name, text in (("n0", "%Label%"), ("p", "P"), ("q", "Q")):
        d = ElementDocument.new_element(lib, name, "genie", canvas=(300, 200))
        d.create_object("text", rect=(10, 10, 120, 26), text=text)
        d.save()
    return lib


def _rows(objs):
    from ctgedit.format import genie_place as GP, geometry as geom

    out = []
    for po in objs:
        if po.raw_payload is not None:
            continue
        r = GP.decode(po)
        out.append(
            (
                po.type_code,
                po.subtype_size,
                geom.get_handle(po),
                [[list(b.pairs) for b in lvl] for lvl in r.entries],
            )
        )
    return out


def _placed(tmp_path, monkeypatch, element, params):
    from ctgedit.editing import ElementDocument
    from ctgedit.format.element import open_element

    lib = _lib(tmp_path, monkeypatch)
    d = ElementDocument.open_element(lib, element)
    d.place_library_element("_r8", "n0", "ctm", (10, 40), params=params, dialog=True)
    d.save()
    return _rows(open_element(lib, element).page.objects)


def test_a_constant_binding_in_a_parameterless_element_keeps_the_page_form(tmp_path, monkeypatch):
    rows = _placed(tmp_path, monkeypatch, "p", {"Label": "ABC"})
    E = [[], [], []]
    # (the element's own text has handle 0 here; in CtDraw's live run it had 20, the paste
    # took 0 and 1 and the root 2: the same lowest-free rule, one handle on)
    assert rows == [
        (0x08, 0x0A, 3, [E]),
        (0x05, 0x1A, 0, [[[], [("TXT_OFF", "P")], []]]),
        (0x08, 0x209, 1, [E, [[], [("AN", "2"), ("Label", "ABC")], []]]),
        (
            0x05,
            0x18,
            2,
            [
                [[], [("TXT_OFF", "ABC")], [("TXT_OFF", "%Label%")]],
                [[("Label", "\n")], [("Label", "ABC")], []],
            ],
        ),
    ]


def test_a_binding_naming_a_parameter_gives_the_element_its_level(tmp_path, monkeypatch):
    rows = _placed(tmp_path, monkeypatch, "q", {"Label": "%Outer%"})
    E = [[], [], []]
    assert rows == [
        (0x08, 0x0A, 3, [E, E]),
        (0x05, 0x1A, 0, [[[], [("TXT_OFF", "Q")], [("TXT_OFF", "Q")]], E]),
        (0x08, 0x209, 1, [E, [[], [("AN", "2")], [("AN", "2"), ("Label", "%Outer%")]], E]),
        (
            0x05,
            0x18,
            2,
            [
                [[], [("TXT_OFF", "%Outer%")], [("TXT_OFF", "%Label%")]],
                [[("Label", "\n")], [], [("Label", "%Outer%")]],
                [[("Outer", "\n")], [], []],
            ],
        ),
    ]


def test_a_paste_without_the_dialog_writes_no_an(tmp_path, monkeypatch):
    from ctgedit.editing import ElementDocument
    from ctgedit.format.element import open_element

    lib = _lib(tmp_path, monkeypatch)
    d = ElementDocument.open_element(lib, "p")
    d.place_library_element("_r8", "n0", "ctm", (10, 40))
    d.save()
    rows = _rows(open_element(lib, "p").page.objects)
    assert rows[2][3] == [[[], [], []], [[], [], []]]
    assert rows[3][3][1] == [[("Label", "\n")], [], []]


#: a small page of the fake install (FxMain's FxThumb), copied by the page test
SMALL_PAGE = os.path.join(fxinstall.FX_INSTALL, "User", "FxMain", "FxThumb.ctg")


def test_the_page_genie_dialog_writes_an_first(tmp_path, monkeypatch):
    import shutil
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP
    from ctgedit.format.page import parse_ctg

    _lib(tmp_path, monkeypatch)
    page = str(tmp_path / "FxGenieAn.ctg")
    shutil.copyfile(SMALL_PAGE, page)
    d = EditableDocument.open(page)
    k = d.place_library_element("_r8", "n0", "ctm", (100, 100))
    d.save()
    assert GP.decode(parse_ctg(page).page.objects[k]).entries[1][1].pairs == []
    d = EditableDocument.open(page)
    assert d.set_genie_bindings(k, {"Label": "XYZ"}, dialog=True)
    d.save()
    objs = parse_ctg(page).page.objects
    from ctgedit.format import geometry as geom

    an = str(geom.get_handle(objs[k]) + 1)
    assert GP.decode(objs[k]).entries[1][1].pairs == [("AN", an), ("Label", "XYZ")]
    m = GP.decode(objs[k + 1]).entries
    assert m[0][1].pairs == [("TXT_OFF", "XYZ")] and m[1][1].pairs == [("Label", "XYZ")]
    # a later OK sets the key in place
    d = EditableDocument.open(page)
    assert d.set_genie_bindings(k, {"Label": "W"}, dialog=True)
    d.save()
    assert GP.decode(parse_ctg(page).page.objects[k]).entries[1][1].pairs == [("AN", an), ("Label", "W")]
