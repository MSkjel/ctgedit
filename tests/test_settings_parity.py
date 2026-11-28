"""Settings map to the Graphics Builder's Tools > Options."""

from ctgedit.ui.prefs import settings_transfer
from ctgedit.format.fileio import find_ci
import os
import shutil
import struct

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402

import fxinstall  # noqa: E402

from ctgedit.format import geometry as G  # noqa: E402
from ctgedit.ui.prefs import settings as S  # noqa: E402
from qtkit import app as _app

#: the page the window opens: a template, a placed genie, a free rectangle
MIXER = "FxTplLinked"
#: the system page with a registered top-level dynamic text (Modify AN)
PUMP = "!FxSystem"
#: the system page with its .ctF (a matching pair)
POPUP = "!fx_popup"


@pytest.fixture()
def win(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_install.page(MIXER))
    yield w
    _close_dialogs(w)
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()
    w.deleteLater()
    QtWidgets.QApplication.processEvents()


def _close_dialogs(w):
    """Close the window's modeless sheets / forms (and let Qt delete them)."""
    for d in list(getattr(w, "_dialogs", {}).values()):
        d.close()
    QtWidgets.QApplication.processEvents()


def _mini_project(EX, dst, pages, tables=("pgdynobj", "pgtouch", "pgkey", "pgbutton", "pgalmind")):
    """A copy of project ``EX`` (the fx_install copy's FxMain) holding ``pages`` and their
    rows in ``tables`` (the source project is only read)."""
    from ctgedit.format.dbf import Dbf, Ndx, create_ndx
    from ctgedit.io import pgregister as PR

    os.makedirs(dst, exist_ok=True)
    for f in ("pages.DBF", "changes.DBF", "PROJECT.CIT"):
        src = find_ci(EX, f)
        if src:
            shutil.copyfile(src, os.path.join(dst, f))
    for t in tables:
        src = find_ci(EX, t + ".DBF")
        if src is None:
            continue
        d = Dbf(src, writable=False)
        n = PR.open_ndx(EX, t)
        recs = [r for p in pages for _i, r in sorted(PR.page_records(d, n, "PAGE", p))]
        if n is not None:
            n.close()
        with open(src, "rb") as fh:
            head = bytearray(fh.read(d.hlen))
        struct.pack_into("<I", head, 4, len(recs))
        out = os.path.join(dst, t + ".DBF")
        with open(out, "wb") as fh:
            fh.write(bytes(head) + b"".join(recs) + b"\x1a")
        create_ndx(os.path.join(dst, t + ".NDX"), "PAGE", d.fmap["PAGE"][1])
        d.close()
        dd = Dbf(out)
        with Ndx(os.path.join(dst, t + ".NDX")) as nx:
            for i in range(dd.nrec):
                nx.insert(dd.field(dd.read(i), "PAGE"), i + 1)
        dd.close()
    for p in pages:
        for ext in (".ctg", ".ctF"):
            if os.path.exists(os.path.join(EX, p + ext)):
                shutil.copyfile(os.path.join(EX, p + ext), os.path.join(dst, p + ext))
    return dst


def _live_ans(project, table, page):
    from ctgedit.format.dbf import Dbf
    from ctgedit.io import pgregister as PR

    path = find_ci(project, table + ".DBF")
    d = Dbf(path, writable=False)
    n = PR.open_ndx(project, table)
    try:
        return [d.decode(r).get("AN", "") for _i, r in PR.page_records(d, n, "PAGE", page) if r[:1] != b"*"]
    finally:
        if n is not None:
            n.close()
        d.close()


# --------------------------------------------------------------------------- the mapping
def test_every_ctdraw_option_is_mapped_or_explained():
    opts = S.CTDRAW_OPTIONS
    assert [o.label for o in opts] == [
        "Display properties on new",
        "Display properties on copy",
        "Save template warning",
        "Modify AN field",
        "Disable genie forms",
        "Display group button",
        "Compile enquiry message",
        "Fast runtime display",
        "List system pages",
        "Show version 3.xx/4.xx tools",
        "XP style buttons by default",
        "Transparent paste",
        "ActiveX automatic page saving",
    ]
    for o in opts:
        assert o.note, o.label
        if o.setting.startswith("opt/"):
            key = o.setting[4:]
            if key != "transparent_paste":
                assert S.OPTION_DEFAULTS[key] == bool(o.default), o.label
        elif not o.setting:
            assert "Not needed" in o.note  # the n/a one says why
    assert [o.label for o in opts if not o.setting] == ["ActiveX automatic page saving"]
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    keys = {k for k, _l in SettingsDialog.FLAGS}
    assert {o.setting[4:] for o in opts if o.setting.startswith("opt/")} - keys == {"transparent_paste"}


# --------------------------------------------------------------------------- the dialog
def test_settings_dialog_tabs_and_graphics_builder_options(win):
    """The Graphics Builder's options sit on the page of what they do (SettingsDialog.TABS),
    and a search for the Graphics Builder's name of each finds it."""
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win)
    tabs = [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())]
    assert tabs == [
        "Editing",
        "Canvas",
        "Drawing",
        "Appearance",
        "Keyboard",
        "Ribbon",
        "Pages and libraries",
        "Simulate",
        "Advanced",
    ]
    for title, keys in SettingsDialog.TABS:
        page = dlg._tabs.widget(tabs.index(title))
        for key in keys:
            assert page.isAncestorOf(dlg._flags[key]), (title, key)
    libs = dlg._tabs.widget(tabs.index("Pages and libraries"))
    canvas = dlg._tabs.widget(tabs.index("Canvas"))
    assert libs.isAncestorOf(dlg._fast_display) and canvas.isAncestorOf(dlg._chk_transparent)
    assert not hasattr(dlg, "_na_note")  # (ActiveX page saving: not offered)
    # CtDraw's defaults
    assert dlg._flags["props_on_new"].isChecked() and dlg._flags["xp_buttons"].isChecked()
    assert not dlg._flags["props_on_copy"].isChecked()
    assert not dlg._flags["list_system_pages"].isChecked()
    # a search for a Graphics Builder name finds its option
    for o in S.CTDRAW_OPTIONS:
        if not o.setting:
            continue
        dlg._tabs.search.setText(o.label)
        dlg._tabs._filter()
        shown = [
            r
            for p in (dlg._tabs.widget(i) for i in range(dlg._tabs.count()))
            for r in p.rows()
            if not r.isHidden()
        ]
        assert shown, o.label
    dlg._tabs.search.setText("")
    from ctgedit.ui.help import dialog_topic

    dlg._tabs.setCurrentIndex(tabs.index("Pages and libraries"))
    assert dialog_topic(dlg) == "settings#pages-and-libraries"


def test_restore_defaults_and_export_import(win, tmp_path, monkeypatch):
    from ctgedit.ui.dialog.bitmap_import import transparent_colour
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    dlg = SettingsDialog(win)
    dlg._flags["props_on_copy"].setChecked(True)
    dlg._flags["props_on_new"].setChecked(False)
    dlg._nudge.setValue(9)
    dlg._chk_transparent.setChecked(True)
    dlg._transparent.set_pair(0x00FF00)
    dlg._apply()
    assert S.option("props_on_copy") and not S.option("props_on_new")
    assert transparent_colour() == 0x00FF00 and win._nudge_step == 9
    path = str(tmp_path / "mine.ini")
    dlg = SettingsDialog(win)
    assert dlg.export_to(path) and os.path.isfile(path)
    # Restore Defaults: every option back, saved with OK
    dlg.restore_defaults()
    assert dlg._flags["props_on_new"].isChecked()
    assert not dlg._flags["props_on_copy"].isChecked()
    assert dlg._nudge.value() == 1 and not dlg._chk_transparent.isChecked()
    assert S.option("props_on_copy")  # nothing saved yet
    dlg._apply()
    assert not S.option("props_on_copy") and S.option("props_on_new")
    assert transparent_colour() is None and win._nudge_step == 1
    # Import brings the exported settings back and applies them
    dlg = SettingsDialog(win)
    assert dlg.import_from(path)
    assert dlg._flags["props_on_copy"].isChecked() and dlg._nudge.value() == 9
    assert S.option("props_on_copy") and not S.option("props_on_new")
    assert win._nudge_step == 9 and transparent_colour() == 0x00FF00
    # a file that is not a ctgedit settings export changes nothing
    bad = tmp_path / "other.ini"
    bad.write_text("[General]\nx=1\n")
    with pytest.raises(S.SettingsFileError, match="not a settings file"):
        settings_transfer.import_groups(str(bad))
    assert not dlg.import_from(str(bad))
    assert S.option("props_on_copy")


# --------------------------------------------------------------------------- citect.ini
_INI = "[General]\r\nproject=\r\n\r\n[Animator]\r\nFullScreen = 0\r\n\r\n[CtEdit]\r\nRun =\r\n"


def test_set_ini_value_keeps_the_file(tmp_path):
    from ctgedit.io import citect_config as cc

    ini = tmp_path / "citect.ini"
    ini.write_bytes(_INI.encode("cp1252"))
    assert cc.set_ini_value("animator", "FastDisplay", "0", ini=str(ini))
    text = ini.read_bytes().decode("cp1252")
    assert "[Animator]\r\nFullScreen = 0\r\nFastDisplay =0\r\n\r\n[CtEdit]" in text
    assert (tmp_path / "citect.ini.ctgedit-bak").read_bytes() == _INI.encode("cp1252")
    assert cc.set_ini_value("Animator", "fastdisplay", "1", ini=str(ini))
    assert "FastDisplay =1\r\n" in ini.read_bytes().decode("cp1252")
    assert not cc.set_ini_value("Animator", "FastDisplay", "1", ini=str(ini))  # no change
    assert cc.set_ini_value("Animator", "FullScreen", "1", ini=str(ini))
    assert "FullScreen = 1\r\n" in ini.read_bytes().decode("cp1252")  # its spacing
    assert cc.set_ini_value("CtDraw.RSC", "ModifyAN", "1", ini=str(ini))
    assert ini.read_bytes().decode("cp1252").endswith("[CtDraw.RSC]\r\nModifyAN =1\r\n")


def _fake_ini(tmp_path, monkeypatch, text=_INI):
    """A fake install at tmp_path (CTG_SCADA_ROOT): its Config/citect.ini is the active one."""
    ini = tmp_path / "Config" / "citect.ini"
    ini.parent.mkdir(exist_ok=True)
    ini.write_bytes(text.encode("cp1252"))
    monkeypatch.setenv("CTG_SCADA_ROOT", str(tmp_path))
    return ini


def test_fast_runtime_display_is_written_to_citect_ini(win, tmp_path, monkeypatch):
    from ctgedit.io import citect_config as cc
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    ini = _fake_ini(tmp_path, monkeypatch)
    dlg = SettingsDialog(win)
    assert dlg._fast_display.isChecked()  # absent = on (the default)
    assert str(ini) in dlg._fast_display.toolTip()
    dlg._fast_display.setChecked(False)
    dlg._apply()
    assert not cc.fast_display_enabled()
    assert "FastDisplay =0" in ini.read_text()
    before = ini.read_bytes()
    SettingsDialog(win)._apply()  # unchanged: not written
    assert ini.read_bytes() == before
    dlg = SettingsDialog(win)
    assert not dlg._fast_display.isChecked()
    dlg.restore_defaults()  # leaves the Plant SCADA setting
    assert not dlg._fast_display.isChecked()


def test_a_save_with_fast_display_off_removes_the_ctf(tmp_path, monkeypatch, fx_project):
    """CtDraw's save writes no .ctF with FastDisplay 0 (CtDraw masks
    bit 4); ctgedit's removes the old one, so a later 'on' cannot show it."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import ctf

    proj = _mini_project(fx_project("FxMain"), str(tmp_path / "FxMini"), [POPUP])
    page = os.path.join(proj, POPUP + ".ctg")
    assert ctf.ctf_path_for(page) is not None  # (the old one is there)
    _fake_ini(tmp_path, monkeypatch, _INI.replace("FullScreen = 0", "FullScreen = 0\r\nFastDisplay =0"))
    doc = EditableDocument.open(page)
    doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    doc.save()
    assert ctf.ctf_path_for(page) is None


def test_turning_fast_display_on_offers_to_remove_stale_ctf(win, tmp_path, monkeypatch, fx_project):
    from ctgedit.format import ctf
    from ctgedit.format.pagesummary import read_directory
    from ctgedit.io import citect_config as cc
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    # (!FxSystem, not the super genie page: opening that in the window asks about its
    # host first; its .ctF is the one ctgedit's writer builds of these bytes)
    proj = _mini_project(fx_project("FxMain"), str(tmp_path / "FxMini"), [PUMP], tables=())
    page = os.path.join(proj, PUMP + ".ctg")
    assert ctf.ctf_path_for(page) is None
    with open(page, "rb") as fh:
        blob = ctf.build_ctf(fh.read(), page)
    with open(page[:-1] + "F", "wb") as fh:
        fh.write(blob)
    assert ctf.ctf_is_stale(page) is False  # a saved pair: same version
    # a Graphics Builder save with Fast runtime display off: a new version in
    # the .ctg, the .ctF left as it was
    d = read_directory(page)
    pg = next(e for e in d.entries if e.name == "page")
    raw = bytearray(open(page, "rb").read())
    raw[pg.offset + 0x24] ^= 0xFF
    open(page, "wb").write(bytes(raw))
    assert ctf.ctf_is_stale(page) is True
    assert ctf.stale_ctf_files(proj) == [ctf.ctf_path_for(page)]
    ini = _fake_ini(tmp_path, monkeypatch, _INI.replace("FullScreen = 0", "FullScreen = 0\r\nFastDisplay=0"))
    win.open_document(page)
    asked = []
    dlg = SettingsDialog(win)
    assert not dlg._fast_display.isChecked()
    dlg._fast_display.setChecked(True)
    dlg.ask_remove_stale = lambda stale: asked.append(stale) or "cancel"
    dlg._apply()
    assert asked and dlg.result() != QtWidgets.QDialog.Accepted
    assert "FastDisplay=0" in ini.read_text() and os.path.exists(ctf.ctf_path_for(page))
    dlg.ask_remove_stale = lambda stale: "remove"
    dlg._apply()
    assert cc.fast_display_enabled() and "FastDisplay=1" in ini.read_text()
    assert ctf.ctf_path_for(page) is None  # the runtime draws the .ctg


@pytest.mark.golden
def test_ctdraws_own_pair_is_not_stale(tmp_path):
    """CtDraw's own PageOpen + PageSave of !FxSystem (with FastDisplay on: the capture sets
    it) writes a .ctg and a .ctF of the same version; the stale check takes them as a pair,
    and a new version in the .ctg alone makes the .ctF stale (the test above builds its pair
    with ctgedit's writer)."""
    from ctgedit.format import ctf
    from ctgedit.format.pagesummary import read_directory

    unit = "FxMain/" + PUMP
    page = tmp_path / (PUMP + ".ctg")
    page.write_bytes(fxinstall.oracle_bytes("pages", unit, "page.ctg"))
    (tmp_path / (PUMP + ".ctF")).write_bytes(fxinstall.oracle_bytes("pages", unit, "page.ctF"))
    assert ctf.ctf_is_stale(str(page)) is False
    d = read_directory(str(page))
    pg = next(e for e in d.entries if e.name == "page")
    raw = bytearray(page.read_bytes())
    raw[pg.offset + 0x24] ^= 0xFF
    page.write_bytes(bytes(raw))
    assert ctf.ctf_is_stale(str(page)) is True


# --------------------------------------------------------------------------- Modify AN
def test_set_object_an_renumbers_the_object_and_its_commands(tmp_path, fx_project):
    from ctgedit.editing import EditableDocument
    from ctgedit.io import pgregister as PR

    proj = _mini_project(fx_project("FxMain"), str(tmp_path / "FxMini"), [PUMP])
    doc = EditableDocument.open(os.path.join(proj, PUMP + ".ctg"))
    doc.derive_check = True  # every re-derive == a full one
    # (the fixture's system page holds its one text: another object to collide with)
    doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    objs = doc.parsed.page.objects
    idx = next(
        i
        for i, po in enumerate(objs)
        if doc.page.objects[i].parent is None and po.type_code == 0x05 and po.subtype_size & 0x10
    )
    old_an = G.get_handle(objs[idx]) + 1
    assert str(old_an) in _live_ans(proj, "pgdynobj", PUMP)
    touch = doc.add_input(idx, "touch", {"CMD": "Beep()"})
    assert G.get_handle(objs[touch]) + 1 == old_an
    used = {G.get_handle(po) + 1 for po in objs}
    new_an = max(a for a in used if a < 30000) + 50
    other = next(
        i
        for i, po in enumerate(objs)
        if i != idx and G.get_handle(po) + 1 != old_an and po.type_code not in G.INPUT_TYPE_CODES
    )
    taken = G.get_handle(objs[other]) + 1
    # CtDraw's rule: no two objects of a page share an AN
    assert "already used" in doc.an_change_problem(idx, taken)
    with pytest.raises(ValueError, match="already used"):
        doc.set_object_an(idx, taken)
    assert "whole number" in doc.an_change_problem(idx, "12a")
    assert "from 1 to 31999" in doc.an_change_problem(idx, 40000)
    assert "belongs to" in doc.an_change_problem(touch, new_an)  # a command: its owner's
    assert doc.an_change_problem(idx, old_an) == ""
    assert doc.set_object_an(idx, old_an) is False
    n = len(doc._undo)
    assert doc.set_object_an(idx, str(new_an))
    assert len(doc._undo) == n + 1
    objs = doc.parsed.page.objects
    assert G.get_handle(objs[idx]) + 1 == new_an and doc.page.objects[idx].an == new_an
    assert G.get_handle(objs[touch]) + 1 == new_an  # the command follows
    doc.undo()
    assert G.get_handle(objs[idx]) + 1 == old_an and G.get_handle(objs[touch]) + 1 == old_an
    doc.redo()
    assert doc.page.objects[idx].an == new_an
    # the save moves the project database rows to the new AN
    doc.save()
    dyn = _live_ans(proj, "pgdynobj", PUMP)
    assert str(new_an) in dyn and str(old_an) not in dyn
    tch = _live_ans(proj, "pgtouch", PUMP)
    assert tch == [str(new_an)]
    assert PR.page_rows(os.path.join(proj, PUMP + ".ctg"), PUMP)["pgtouch"][0]["AN"] == str(new_an)


def test_an_cannot_change_on_genies_members_and_template_records(fx_page):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page(MIXER))
    objs = doc.parsed.page.objects
    sem = doc.page.objects
    tpl = next(i for i, o in enumerate(sem) if o.raw.get("_from_template"))
    assert "page template" in doc.an_change_problem(tpl)
    genie = next(
        i for i, po in enumerate(objs) if po.subtype_size & 0x200 and not sem[i].raw.get("_from_template")
    )
    assert "placed genie" in doc.an_change_problem(genie)
    member = sem[genie].members[0]
    assert "part of a genie" in doc.an_change_problem(member)
    free = next(
        i
        for i, o in enumerate(sem)
        if o.parent is None
        and not o.members
        and not o.raw.get("_from_template")
        and not objs[i].subtype_size & 0x200
        and objs[i].type_code < 0x1000
    )
    assert doc.an_change_problem(free) == ""


def test_access_page_edits_the_an_only_with_the_option(fx_page):
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    doc = EditableDocument.open(fx_page(MIXER))
    a = doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)
    b = doc.create_object("rectangle", rect=(60, 5, 100, 40), colour=0x336699)
    dlg = ObjectPropertiesDialog(doc, b)
    assert dlg.access_editor.an.isReadOnly()  # CtDraw's default: greyed
    dlg.close()
    S.set_option("modify_an", True)
    dlg = ObjectPropertiesDialog(doc, b)
    ed = dlg.access_editor
    assert not ed.an.isReadOnly()
    ed.an.setText(str(doc.page.objects[a].an))
    n = len(doc._undo)
    assert dlg.apply() is False and len(doc._undo) == n
    assert "already used" in dlg._status.text()
    free = max(G.get_handle(po) for po in doc.parsed.page.objects if G.get_handle(po) < 30000) + 11
    ed.an.setText(str(free))
    assert dlg.apply() and len(doc._undo) == n + 1
    assert doc.page.objects[b].an == free and ed.an.text() == str(free)
    dlg.close()
    # a placed genie's AN stays greyed, with the reason
    genie = next(
        i
        for i, po in enumerate(doc.parsed.page.objects)
        if po.subtype_size & 0x200 and not doc.page.objects[i].raw.get("_from_template")
    )
    dlg = ObjectPropertiesDialog(doc, genie)
    assert dlg.access_editor.an.isReadOnly()
    assert "placed genie" in dlg.access_editor.an.toolTip()
    dlg.close()


# --------------------------------------------------------------------------- the window
def test_display_properties_on_new_and_on_copy(win):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    win._on_draw_committed("rectangle", {"rect": (40, 40, 120, 90)})
    QtWidgets.QApplication.processEvents()
    (i,) = win._sel
    assert isinstance(win._dialogs.get(("objprops", i)), ObjectPropertiesDialog)  # default on
    _close_dialogs(win)
    assert not win._dialogs
    win.duplicate_selected()  # on copy: off by default
    QtWidgets.QApplication.processEvents()
    assert not win._dialogs
    S.set_option("props_on_copy", True)
    win._sel = [i]
    win.duplicate_selected()
    QtWidgets.QApplication.processEvents()
    (j,) = win._sel
    assert j != i and isinstance(win._dialogs.get(("objprops", j)), ObjectPropertiesDialog)
    win.copy_selected()
    try:
        win.paste_clipboard()
        QtWidgets.QApplication.processEvents()
    finally:
        # clipboard data left at exit crashes Qt's offscreen teardown
        QtWidgets.QApplication.clipboard().clear()
        QtWidgets.QApplication.processEvents()
    k = win.doc.root_of(win._sel[0])
    assert ("objprops", k) in win._dialogs
    S.set_option("props_on_new", False)
    _close_dialogs(win)
    assert not win._dialogs
    win._on_draw_committed("rectangle", {"rect": (140, 40, 220, 90)})
    QtWidgets.QApplication.processEvents()
    assert not win._dialogs


def test_xp_style_buttons_by_default(win):
    win._on_draw_committed("button", {"rect": (40, 80, 130, 110)})
    (i,) = win._sel
    assert win.doc.object_properties(i)["VIS_MODE"] == "XP_STYLE"
    S.set_option("xp_buttons", False)
    win._on_draw_committed("button", {"rect": (140, 80, 230, 110)})
    (j,) = win._sel
    assert win.doc.object_properties(j)["VIS_MODE"] == "NORMAL"
    assert win.doc.button_face(j)["xp"] is False


def test_compile_enquiry_message(win, monkeypatch):
    from ctgedit.backend import runtime

    calls = []
    monkeypatch.setattr(win, "compile_project", lambda show="always": calls.append(show))
    monkeypatch.setattr(runtime, "is_running", lambda: False)
    monkeypatch.setattr(runtime, "start", lambda pdir: calls.append("start") or ["started"])
    win.doc.create_object("rectangle", rect=(5, 5, 50, 40), colour=0x336699)  # dirty
    asked = []
    monkeypatch.setattr(win, "_compile_enquiry", lambda project, dirty: asked.append(dirty) or "run")
    win._run_project()  # option off (CtDraw's default): no question
    assert calls == ["errors"] and not asked
    calls.clear()
    S.set_option("compile_enquiry", True)
    win._run_project()  # 'Run Without Compiling'
    assert asked == [True] and calls == ["start"]
    calls.clear()
    monkeypatch.setattr(win, "_compile_enquiry", lambda project, dirty: "cancel")
    win._run_project()
    assert calls == []
    monkeypatch.setattr(win, "_compile_enquiry", lambda project, dirty: "compile")
    win._run_project()
    assert calls == ["errors"]


def test_list_system_pages(tmp_path, fx_project):
    _app()
    from ctgedit.io.catalog import ProjectCatalog
    from ctgedit.ui.browser import PageBrowserDialog

    EX = fx_project("FxMain")
    proj = tmp_path / "Loose"
    proj.mkdir()
    shutil.copyfile(os.path.join(EX, "pages.DBF"), proj / "pages.DBF")
    for n in (MIXER + ".ctg", PUMP + ".ctg"):
        shutil.copyfile(os.path.join(EX, n), proj / n)
    cat = ProjectCatalog(str(tmp_path / "no_install"))
    has_pump = True
    # the page browser does not read Settings > List system pages: it starts
    # on All pages and keeps its own choice (pagebrowser/popups); the option
    # is for Page Properties' Previous / Next lists (the next test)
    for opt in (False, True):
        S.set_option("list_system_pages", opt)
        dlg = PageBrowserDialog(
            PageBrowserDialog.OPEN, catalog=cat, start_path=str(proj / (MIXER + ".ctg")), kinds=("page",)
        )
        try:
            assert dlg.cb_popups.currentData() == "any"
            if has_pump:
                assert any(e.name.startswith("!") for e in dlg.model.entries())
        finally:
            dlg.deleteLater()
    S.app_settings().setValue("pagebrowser/popups", "hide")
    dlg = PageBrowserDialog(
        PageBrowserDialog.OPEN, catalog=cat, start_path=str(proj / (MIXER + ".ctg")), kinds=("page",)
    )
    try:
        assert dlg.cb_popups.currentData() == "hide"  # the remembered choice
        assert not any(e.name.startswith("!") for e in dlg.model.entries())
    finally:
        dlg.deleteLater()
    if has_pump:
        # the page being edited is a system page: shown for it
        dlg = PageBrowserDialog(
            PageBrowserDialog.OPEN, catalog=cat, start_path=str(proj / (PUMP + ".ctg")), kinds=("page",)
        )
        try:
            assert dlg.current_entry() is not None and dlg.current_entry().name == PUMP
            assert dlg.cb_popups.currentData() == "any"
        finally:
            dlg.deleteLater()


def test_page_properties_previous_next_leave_system_pages_out(tmp_path, fx_project):
    """FxMain's pages.DBF registers !fx_popup and !fx_detail beside !FxSystem."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import project as P
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    _app()
    proj = _mini_project(fx_project("FxMain"), str(tmp_path / "FxMini"), [PUMP], tables=())
    assert any(n.startswith("!") and n != PUMP for n in P.chain_page_names(proj))
    doc = EditableDocument.open(os.path.join(proj, PUMP + ".ctg"))
    dlg = PagePropertiesDialog(doc, P.Project(proj))
    try:
        prev = dlg._dbf_edits["PREV_PAGE"]
        # (pages() lists the names: the field fills when it opens or takes the focus)
        items = prev.pages()
        assert items and not any(t.startswith("!") for t in items)
        parent = dlg._dbf_edits["PARENT"]
        assert any(t.startswith("!") for t in parent.pages())
    finally:
        dlg.close()
    S.set_option("list_system_pages", True)
    dlg = PagePropertiesDialog(doc, P.Project(proj))
    try:
        prev = dlg._dbf_edits["PREV_PAGE"]
        assert any(t.startswith("!") for t in prev.pages())
    finally:
        dlg.close()


def test_export_does_not_store_the_dialogs_pending_choices(win, tmp_path):
    """Export writes the dialog's choices to the FILE only: Cancel afterwards
    must still discard them."""
    from PySide6 import QtCore
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win)
    dlg._flags["props_on_copy"].setChecked(True)
    dlg._nudge.setValue(7)
    path = str(tmp_path / "pending.ini")
    assert dlg.export_to(path)
    dlg.reject()
    assert not S.option("props_on_copy")  # nothing stored
    assert int(S.app_settings().value("view/nudge_step", 1, type=int)) != 7
    out = QtCore.QSettings(path, QtCore.QSettings.Format.IniFormat)
    assert out.value("opt/props_on_copy", False, type=bool)  # but exported
    assert int(out.value("view/nudge_step", 0, type=int)) == 7


def test_import_keeps_this_computers_session_and_applies_the_layout(win, tmp_path):
    """The open-tab list is this computer's (never exported, kept by an
    import); the imported view state reaches the window at once, so its
    close-time save does not overwrite it."""
    from PySide6 import QtCore
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    st = S.app_settings()
    st.setValue("session/open_files", [r"C:\here\mine.ctg"])
    st.setValue("view/crosshair", True)
    path = str(tmp_path / "other.ini")
    settings_transfer.export_groups(path)
    out = QtCore.QSettings(path, QtCore.QSettings.Format.IniFormat)
    assert not any(k.startswith("session/") for k in out.allKeys())
    out.setValue("session/open_files", [r"C:\elsewhere\theirs.ctg"])  # a foreign one
    out.setValue("view/crosshair", False)
    out.sync()
    st.setValue("session/open_files", [r"C:\here\now.ctg"])
    win.act_crosshair.setChecked(True)
    dlg = SettingsDialog(win)
    assert dlg.import_from(path)
    st = S.app_settings()
    assert [str(f) for f in st.value("session/open_files", [], type=list)] == [r"C:\here\now.ctg"]
    assert not win.act_crosshair.isChecked()  # applied to the window now
    win.save_prefs()
    assert not S.app_settings().value("view/crosshair", True, type=bool)


def test_a_bang_search_finds_hidden_system_pages():
    """With '!' pages hidden (List system pages off), typing '!' or a '!'
    name in the browser's search still finds them."""
    _app()
    from ctgedit.io.catalog import Entry
    from ctgedit.ui.browser.model import CatalogModel

    m = CatalogModel(None)
    m.set_entries(
        [Entry("page", "P", n, r"C:\x\%s.ctg" % n) for n in ("!FxPopup", "FxMixer", "FxOther")], "page"
    )
    m.set_facets(popups="hide")
    names = lambda: [e.name for e in m.entries()]  # noqa: E731
    assert names() == ["FxMixer", "FxOther"]
    m.set_query("!")
    assert names() == ["!FxPopup"]
    m.set_query("!fxpop")
    assert names() == ["!FxPopup"]
    m.set_query("mix")
    assert names() == ["FxMixer"]


def test_set_ini_value_keeps_every_byte(tmp_path):
    """citect.ini is rewritten in the lossless code page: a byte cp1252
    leaves undefined (0x81, 0x8D ...) survives the write."""
    from ctgedit.io import citect_config as cc

    ini = tmp_path / "citect.ini"
    raw = b"[General]\r\nTitle=Pl\x81nt \x8d\xe6\r\n\r\n[Animator]\r\nFullScreen = 0\r\n"
    ini.write_bytes(raw)
    assert cc.set_ini_value("Animator", "FastDisplay", "0", ini=str(ini))
    assert ini.read_bytes() == raw.replace(b"FullScreen = 0\r\n", b"FullScreen = 0\r\nFastDisplay =0\r\n")


def test_the_search_keeps_the_matching_settings_on_every_page(win):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win)
    pages = dlg._tabs
    try:

        def visible_pages():
            return [pages.tabText(k) for k in range(pages.count()) if not pages.list.item(k).isHidden()]

        pages.search.setText("wheel")
        pages.search.flush()
        # (Canvas: the mouse wheel rows; Pages and libraries: "Ctrl + wheel sizes the cards by")
        assert visible_pages() == ["Canvas", "Pages and libraries"]
        assert pages.tabText(pages.currentIndex()) == "Canvas"
        rows = [r for r in pages.widget(pages.currentIndex()).rows() if not r.isHidden()]
        # (the mouse wheel, the wheel zoom step, the stop at 100 %)
        assert len(rows) == 3 and any("mouse wheel" in r.search_text.lower() for r in rows)
        pages.search.setText("mouse wheel")
        pages.search.flush()
        rows = [r for r in pages.widget(pages.currentIndex()).rows() if not r.isHidden()]
        assert len(rows) == 1 and "mouse wheel" in rows[0].search_text.lower()
        # a setting of the Simulate page, by a word of its tool tip
        pages.search.setText("VS Code")
        pages.search.flush()
        assert "Simulate" in visible_pages()
        # nothing matches: the list is empty and the window says so
        pages.search.setText("zzzz no such setting")
        pages.search.flush()
        assert visible_pages() == [] and not pages.empty.isHidden()
        # a regular expression
        pages.search.set_regex(True)
        pages.search.setText("^Undo memory")
        pages.search.flush()
        assert visible_pages() == ["Editing"]
        pages.search.set_regex(False)
        pages.search.setText("")
        pages.search.flush()
        assert len(visible_pages()) == pages.count()
    finally:
        dlg.close()


def test_settings_opens_at_the_simulate_page_and_stores_it(win):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.simulate import options

    dlg = SettingsDialog(win, page="Simulate")
    try:
        assert dlg._tabs.tabText(dlg._tabs.currentIndex()) == "Simulate"
        from ctgedit.ui.help import dialog_topic

        assert dialog_topic(dlg) == "simulate#simulate-settings"
        dlg._sim.fast_startup.setChecked(False)
        dlg._store()
        assert options.get("fast_startup") is False
        dlg.restore_defaults()
        assert dlg._sim.fast_startup.isChecked()
    finally:
        dlg.close()
