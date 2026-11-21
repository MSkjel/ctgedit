"""The fake install and the pieces it's built from."""

import importlib.util
import os
import subprocess
import sys

import pytest

import fxinstall
import hermetic

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _generator():
    spec = importlib.util.spec_from_file_location(
        "make_fixtures", os.path.join(REPO, "tools", "make_fixtures.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- the DBF writer
def test_a_new_table_reads_back_with_its_index(tmp_path):
    from ctgedit.format import dbf
    from ctgedit.format.projects import read_dbf_rows

    path = str(tmp_path / "fxtable.DBF")
    fields = [("NAME", 12), ("Comment", 20)]
    rows = [
        {"NAME": "beta", "COMMENT": "second"},
        {"name": "Alpha"},
        ("gamma", "a comment longer than its field"),
    ]
    dbf.create_table(path, fields, rows, indexes=["NAME"])
    # (a field name keeps its case, as the Languages form's 'Language'; rows match any case)
    assert read_dbf_rows(path) == [
        {"NAME": "beta", "Comment": "second"},
        {"NAME": "Alpha", "Comment": ""},
        {"NAME": "gamma", "Comment": "a comment longer tha"},
    ]
    with dbf.Dbf(path, writable=False) as d:
        assert [(n, w) for n, _o, w in d.fields] == [("NAME", 12), ("Comment", 20)]
        assert d.nrec == 3 and d.rlen == 33
    with dbf.Ndx(str(tmp_path / "fxtable.NDX"), writable=False) as x:
        assert x.expr == "NAME"
        assert x.find("ALPHA") == [2]
        assert [r for _k, r in x.entries()] == [2, 1, 3]  # alpha, beta, gamma
    with open(path, "rb") as fh:
        data = fh.read()
    assert data[0] == 3 and data.endswith(b"\x1a")
    with pytest.raises(ValueError):
        dbf.create_table(path, [("A_NAME_TOO_LONG", 4)])
    with pytest.raises(ValueError):
        dbf.create_table(path, fields, [{"NOPE": "x"}])
    # a row with more values than the table has fields is refused (never dropped)
    with pytest.raises(ValueError, match="3 values"):
        dbf.table_bytes(fields, [("delta", "c", "lost value")])


def test_the_header_date_is_the_fixed_identity_day(tmp_path, monkeypatch):
    from ctgedit.format import dbf

    monkeypatch.setenv("CTG_FIXED_IDENTITY", "fx")
    data = dbf.table_bytes([("NAME", 4)], [])
    assert data[1:4] == bytes((126, 1, 1))  # 2026-01-01


# --------------------------------------------------------------------------- the install
def test_the_fake_install_is_the_only_install(fx_install):
    from ctgedit.format import projects
    from ctgedit.infra import install
    from ctgedit.io import citect_config

    with hermetic.guarding() as bad:
        assert install.mode() == "fake" and install.root() == fx_install.root
        assert os.path.normcase(citect_config.live_user_dir()) == os.path.normcase(fx_install.user)
        assert install.active_ini() == fx_install.config
        assert install.bin_dirs() == [fx_install.bin]
        reg = projects.master_projects(fx_install.user)
        assert sorted(reg) == sorted(p.lower() for p in fxinstall.PROJECTS)
        assert all(
            os.path.normcase(p) == os.path.normcase(os.path.join(fx_install.user, n))
            for n, p in ((n, reg[n.lower()]) for n in fxinstall.PROJECTS)
        )
        chain = [os.path.basename(d) for d in projects.include_search_dirs(fx_install.project("FxMain"))]
        assert chain == ["FxLib", "FxStyle", "Include", "System"]
    assert bad == []


def test_every_fixture_page_opens_and_finds_its_objects_by_name(fx_install):
    from ctgedit.editing import EditableDocument

    with hermetic.guarding() as bad:
        names = {}
        for project in ("FxMain", "FxOther", "FxSim", "FxPerf", "FxSa"):
            folder = fx_install.project(project)
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith(".ctg"):
                    doc = EditableDocument.open(os.path.join(folder, f))
                    assert not doc.damage, f
                    names[(project, f[:-4])] = doc
        shapes = names[("FxMain", "FxShapes")]
        for name in (
            "FxLine",
            "FxRect",
            "FxPolygon",
            "FxPipe",
            "FxText",
            "FxButton",
            "FxOuterGroup",
            "FxBitmap",
            "FxMetafile",
            "FxCp1252",
        ):
            assert shapes.find_objects_by_name(name), name
        genies = names[("FxMain", "FxGenies")]
        (i,) = genies.find_objects_by_name("FxPump2")
        assert genies.genie_bindings(i) == {"Tag": "FX_PUMP2", "Label": "Pump 2"}
        assert len(genies.genie_instances()) >= 12
        for page in ("FxTplLinked", "FxTplNoTitle", "FxTplBlank"):
            assert names[("FxMain", page)].template_status()[0] == "current", page
        assert names[("FxMain", "FxMissing")].missing_library_objects()
        assert ("FxMain", "!fx_popup") in names  # the super genie's page
        for (project, page), wanted in NAMED.items():
            doc = names[(project, page)]
            assert not doc.dirty, (project, page)  # no automatic open change
            for name in wanted:
                assert len(doc.find_objects_by_name(name)) == 1, (project, page, name)
    assert bad == []


# the second set's named objects, by page (the catalogue)
NAMED = {
    ("FxMain", "FxArrange"): (
        "FxFreeBefore",
        "FxBigGroup",
        "FxBigInner",
        "FxLeafBefore",
        "FxLeafAfter",
        "FxFreeAfter",
        "FxCarrier",
        "FxSameSize",
        "FxPointGenie",
        "FxKeyText",
        "FxLoneText",
        "FxTripleSet",
        "FxTouchFill",
        "FxTouchKeyButton",
    ),
    ("FxMain", "FxInputs"): (
        "FxTouchText",
        "FxKeyRect",
        "FxInputGenie",
        "FxSymTouch",
        "FxRotatedTouch",
        "FxSetTouch",
        "FxInButton1",
    ),
    ("FxMain", "FxButtons"): ("FxTouch01", "FxTouch12", "FxKey01", "FxKey12", "FxBtn01", "FxBtn08"),
    ("FxMain", "FxTextKinds"): (
        "FxTxtOffLabel",
        "FxLabelOne",
        "FxLogin1",
        "FxPrint3",
        "FxLoginGate",
        "FxColStaText",
        "FxTouchNumber",
        "FxIconButton",
        "FxPageGetStr",
        "FxStrLeftover",
        "FxEmptyString",
        "FxQuotedMenu",
        "FxNumParen",
        "FxNumBang",
        "FxNumUndefined",
        "FxNumField",
        "FxNumOverride",
        "FxNumEquip",
        "FxGreyText",
        "FxCalibri",
        "FxDigit22",
        "FxMagentaM",
        "FxBold",
        "FxBullets",
        "FxSlot0",
        "FxSlot6",
        "FxFont1",
        "FxFont5",
        "FxNumIdleRef",
        "FxNumExtField",
        "FxNumExtOverride",
        "FxTransparentText",
    ),
    ("FxMain", "FxColours"): (
        "FxCm0",
        "FxCm1",
        "FxCm2",
        "FxCm3",
        "FxCm4",
        "FxFilCol",
        "FxFlatPair",
        "FxEllipseMaxon",
        "FxPennant",
        "FxPennantShadow",
        "FxGrey1_1",
        "FxGrey4_4",
        "FxArrGroup",
        "FxHollowRaised",
        "FxEmbossed1",
        "FxEmbossed3",
        "FxHollowShadow",
        "FxTargetButton",
        "FxXpButton",
        "FxWidePipe",
        "FxValueFrame",
        "FxValueText",
        "FxGradientTB",
        "FxRounded",
    ),
    ("FxMain", "FxMembers"): (
        "FxPair",
        "FxMixed",
        "FxInputOwner",
        "FxFree1",
        "FxFree2",
        "FxGenieInGroup",
        "FxInnerGenie",
        "FxNestedHost",
    ),
    ("FxMain", "FxMany"): (
        "FxManyValve1",
        "FxManyValve8",
        "FxSame01",
        "FxSame12",
        "FxManyText01",
        "FxStackA",
        "FxStackC",
        "FxManyGroup1",
        "FxManyPump6",
        "FxEquipGenie1",
        "FxPlainPipe",
        "FxValveLabel",
    ),
    ("FxMain", "FxParts"): (
        "FxManyParams",
        "FxOrder1",
        "FxNone1",
        "FxDefault1",
        "FxGoto1",
        "FxInputs1",
        "FxAnList1",
        "FxNamed1",
        "FxOffset1",
        "FxPoints1",
        "FxGroupRoot1",
        "FxDummy1",
        "FxHandles1",
        "FxRow1",
        "FxRow3",
        "FxMixed1",
        "FxAlarm1",
        "FxPicture1",
        "FxValveLeft1",
        "FxPipe1",
        "FxSets1",
        "FxStale1",
        "FxNamedBox",
        "FxSymSym",
        "FxBroken1",
        "FxListy1",
        "FxInsertObject1",
        "FxAgitator1",
        "FxLoop4",
        "FxLoop6",
        "FxSetpoint1",
        "FxHiddenBase1",
        "FxLinkText1",
        "FxStale2",
    ),
    ("FxMain", "FxTplMarker"): ("FxOnMarker",),
    ("FxMain", "FxTplSets"): ("FxOnSets",),
    ("FxMain", "FxTplFlash"): ("FxSteady",),
    ("FxMain", "FxTplStale"): ("FxOwn",),
    ("FxMain", "FxTplLast"): ("FxBeforeTpl",),
    ("FxMain", "FxTplArrows"): ("FxArrowsPipe",),
    ("FxMain", "FxParity"): ("FxStatus1", "FxTurnedPic1", "FxSpinner1"),
    ("FxMain", "FxAnim"): ("FxAlarmGroup", "FxAlarmGroup2", "FxTxtFirst", "FxColFirst", "FxOnOff"),
    ("FxMain", "FxGenies"): ("FxLamp", "FxBitmapGenie"),
    ("FxMain", "FxMissing"): ("FxGoneValue", "FxGonePlain", "FxStays", "FxGoneThin"),
    ("FxMain", "FxLegacyNames"): ("FxLink", "FxLink_1"),
    ("FxMain", "FxAnPoints"): ("FxPointBox",),
    ("FxMain", "FxNoAnNames"): ("FxArrow", "FxTipGenie", "FxTipButton", "FxNoNamePump", "FxPlainBox"),
    ("FxMain", "FxScaled"): ("FxScaledSet", "FxScaledLeft", "FxScaledRight"),
    ("FxMain", "FxFreehand"): ("FxFreehand1", "FxRedLine", "FxBlueLine"),
    ("FxMain", "FxLegacy2"): ("FxV3Symbol", "FxV4AnimSymbol", "FxV3String", "FxV3Number"),
    ("FxMain", "FxHd1080"): ("FxHdPump",),
    ("FxMain", "FxHd768"): ("FxHdRect",),
    ("FxMain", "FxAssoc"): ("FxAssocButton", "FxAssocSet", "FxAssocTip", "FxEquipRef", "FxDetailHost"),
    ("FxMain", "FxMaster"): ("FxMasterText",),
    ("FxMain", "FxComposites"): tuple("FxComp%d" % n for n in range(1, 10)),
    ("FxMain", "FxCicode"): (
        "FxCicodeIcon",
        "FxCicodeDefault",
        "FxCicodePage",
        "FxCicodePriority",
        "FxCicodePoint",
        "FxCicodeAngle1",
        "FxCicodeAngle2",
    ),
    ("FxMain", "FxMotion"): (
        "FxRotExpress",
        "FxRotSlider",
        "FxRotBoom",
        "FxRotClaw",
        "FxRotTop",
        "FxSzxGrow",
        "FxSzyMove",
        "FxSzyCustom",
        "FxSliderHV",
        "FxTplLikeRect",
        "FxNeedle1",
        "FxNeedle3",
        "FxNavButton",
        "FxSymRotate",
        "FxSymStretched",
    ),
    ("FxMain", "FxActiveX"): ("FxCtl01", "FxCtl40", "FxAnalyst", "FxTextBox"),
    ("FxMain", "FxStyles"): ("FxStyle01", "FxStyle48"),
    ("FxMain", "FxAlarms"): ("FxAi01", "FxAi30", "FxAiBox01", "FxAiText30"),
    ("FxMain", "FxSymbols2"): (
        "FxHiddenEmbedded",
        "FxVisibleGroupSym",
        "FxThumbSymbol",
        "FxPointerA",
        "FxPointerB",
        "FxCp1252Symbol",
        "FxIconHelp",
        "FxSquareSet",
        "FxHalfSet",
        "FxBmp1",
        "FxBmp4",
    ),
    ("FxMain", "FxWork"): ("FxBelow1", "FxBelow6", "FxWorkPump", "FxWorkGroup", "FxWorkMove"),
    ("FxMain", "FxWide"): ("FxMarkLeft", "FxMarkRight", "FxIconBelow", "FxWideRect"),
    ("FxMain", "FxUnderlay"): ("FxOpaque01", "FxOpaque24"),
    ("FxMain", "FxThumb"): ("FxThumbRect",),
    ("FxSim", "FxSimAlarms"): ("FxAlarmList1", "FxAlarmList2", "FxTreeList", "FxAlarmFlag"),
    ("FxSim", "FxSimTrend"): ("FxSimTrend8",),
    ("FxSim", "!FxSimPop"): ("FxPopClose", "FxPopCount"),
    ("FxSim", "FxSimPopup"): ("FxOpenPop", "FxSimDetail"),
    ("FxSim", "FxSimBig"): (
        "FxSimLamp01",
        "FxSimLamp28",
        "FxSimValue28",
        "FxSimFill01",
        "FxSimSet10",
        "FxSimGroup4",
        "FxSimLogin",
    ),
    ("FxPerf", "FxLarge"): (
        "FxLargePump01",
        "FxLargePump30",
        "FxLargePipe20",
        "FxLargeGroup10",
        "FxLarge001",
        "FxLarge050",
    ),
    ("FxPerf", "FxHuge"): ("FxHugeFirst", "FxHugeLast"),
    ("FxSa", "FxSaMaster"): tuple(
        "FxPane%s" % n
        for n in ("Content", "HeaderBar", "FacePlate", "FacePlateHeader", "Navigation", "Info", "InfoHeader")
    ),
    ("FxSa", "FxSaNested1"): ("FxPaneInner",),
    ("FxSa", "FxSaNested2"): ("FxPaneLoop",),
    ("FxSa", "FxSaHeader"): ("FxTabBar",),
    ("FxSa", "FxSaNav"): ("FxNav1", "FxNav3"),
    ("FxSa", "FxSaBlank"): ("FxSaBlankText",),
    ("FxSa", "FxSaContent"): ("FxSaPump", "FxSaAlarm", "FxSaLamp", "FxSaBox"),
}

#: the second set's library elements, by (project, library)
ELEMENTS = {
    ("Include", "cicode.ctl"): ("default", "fx_cicode_box"),
    ("Include", "thumbs.ctl"): ("pointer2_e_r", "pointer2_e_grey", "pointer_s_red"),
    ("Include", "icons.ctl"): ("help1", "note", "hand2"),
    ("Include", "lights_square_small.ctl"): ("grey", "red"),
    ("Include", "fxprio.ctl"): (
        "fx_p1",
        "fx_p1_small",
        "fx_p2",
        "fx_p2_small",
        "fx_p3",
        "fx_p3_small",
        "fx_shelved",
        "fx_disabled",
    ),
    ("Include", "fxprio.ctm"): ("fx_p1", "fx_p2", "fx_p3"),
    ("Include", "fxicons16.ctl"): ("default", "page"),
    ("FxLib", "fxmore.ctl"): (
        "fx_bitmap2",
        "fx_flash2",
        "fx_blå_sø",
        "fx_symsym",
        "fx_bigsym",
        "fx_grouped",
        "fx_dspsym",
    ),
    ("FxLib", "fxmany.ctl"): ("fx_pump_small", "fx_pmp_mark", "fx_agitator_grey", "fx_last_big"),
    ("FxLib", "fxempty.ctl"): (),
    ("FxLib", "fxbmpgrp.ctl"): ("fx_bg_dyn1", "fx_bg_dyn3", "fx_bg_static1", "fx_bg_static3"),
    ("FxLib", "fxgenie.ctm"): ("fx_pump", "fx_lamp", "fx_bitmaps"),
    ("FxLib", "fxsuper.ctm"): ("!fx_popup@SUPER", "!fx_detail@SUPER"),
    ("FxLib", "fxparts.ctm"): (
        "fx_many",
        "fx_order",
        "fx_none",
        "fx_goto",
        "fx_inputs",
        "fx_anconst",
        "fx_anlist",
        "fx_named",
        "fx_insert_object",
        "fx_listy",
        "fx_broken",
        "fx_offset",
        "fx_points",
        "fx_grouproot",
        "fx_dummy",
        "fx_handles",
        "fx_row",
        "fx_mixed",
        "fx_alarm",
        "fx_picture",
        "fx_valve_left",
        "fx_default",
        "fx_stale",
        "fx_pipe",
        "fx_sets",
        "fx_agitator",
        "fx_agitator_mixer",
        "fx_popper2",
        "fx_loop",
        "fx_setpoint",
        "fx_hidden_base",
        "fx_linktext",
        "fx_tipbutton",
        "fx_lost_pipe",
        "fx_stale_note",
        "fx_bowtie",
        "fx_status",
        "fx_turned_pic",
        "fx_spinner",
    ),
    ("FxStyle", "fx_alt.ctt"): (
        "pagecontent@-XGA",
        "blank@-XGA",
        "marker@-XGA",
        "sets@-XGA",
        "flash@-XGA",
        "stale@-XGA",
        "arrows@-XGA",
    ),
    ("FxStyle", "fx_hd.ctt"): ("normal@HD1080", "normal@HD768"),
    ("FxStyle", "fx_sa.ctt"): ("normal@-HD1080", "blank@-HD1080", "pagecontent@-HD1080", "normal@-UHD4K"),
    ("FxSa", "fxws.ctm"): ("pane", "navlink"),
}


def _element_names(path):
    from ctgedit.format import element as E

    with open(path, "rb") as fh:
        return E.LibraryFile(fh.read()).element_names()


def test_fxdemo_is_a_working_plant(fx_install):
    """FxDemo, the README's screenshots: its pages open whole, every genie it places is one of
    its own and names a tag the project has, and the Cicode the pages call is there."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.projects import read_dbf_rows

    folder = fx_install.project("FxDemo")
    tags = {r["NAME"] for r in read_dbf_rows(os.path.join(folder, "variable.DBF"))}
    ci = open(os.path.join(folder, "fxdemo.ci"), encoding="cp1252").read()
    with hermetic.guarding() as bad:
        for page, count in (("Overview", 5), ("Pump", 2)):
            doc = EditableDocument.open(os.path.join(folder, page + ".ctg"))
            assert not doc.damage, page
            placed = doc.genie_instances()
            assert len(placed) == count, page
            for i, (lib, _element) in placed:
                assert lib.lower() == "fxdemo", page
                assert doc.genie_bindings(i)["Tag"] in tags, page
            assert {k.upper(): v for k, v in doc.page_record().items()}["ENTRY_FUNC"] == "DemoStart()"
    assert not bad
    for fn in ("DemoStart", "DemoLine", "DemoProcess"):
        assert "FUNCTION %s(" % fn in ci
    # (TRUE is a label of AVEVA's Include, which the fake install has not)
    assert "WHILE TRUE" not in ci


def test_the_libraries_hold_their_elements(fx_install):
    from ctgedit.editing import ElementDocument
    from ctgedit.format import genie_place as GP
    from ctgedit.format import geometry as G

    with hermetic.guarding() as bad:
        for (project, lib), wanted in ELEMENTS.items():
            names = _element_names(fx_install.file(project, lib))
            for name in wanted:
                assert name in names, (lib, name)
            if not wanted:
                assert names == [], lib  # only a deleted element
        parts = fx_install.file("FxLib", "fxparts.ctm")
        many = GP.declared_params_of(parts, "fx_many")
        assert len(many) == 42 and next(iter(many)) == "Tag"
        assert list(GP.declared_params_of(parts, "fx_order")) == [
            "buttonText ",
            "MixedCase",
            "lower",
            "UPPER",
            "Title",
        ]
        assert dict(GP.declared_params_of(parts, "fx_default"))["Title"] == "Pump"
        assert list(GP.declared_params_of(parts, "fx_none")) == []

        def records(project, lib, name):
            d = ElementDocument.open_element(fx_install.file(project, lib), name)
            return d.parsed.page.objects

        assert [G.get_handle(po) for po in records("FxLib", "fxparts.ctm", "fx_handles")] == [9, 9, 3, 6]
        # (each state of the set is the forest of a group-root symbol: its root, its ellipse)
        assert [G.get_handle(po) for po in records("FxLib", "fxmore.ctl", "fx_dspsym")] == [
            12,
            18,
            2,
            16,
            9,
            1,
            3,
            5,
            4,
            11,
            7,
            6,
            10,
            8,
            15,
            13,
            17,
        ]
        assert sum(po.type_code == 0xFFFF for po in records("FxLib", "fxparts.ctm", "fx_dummy")) == 2
        assert [po.type_code for po in records("FxLib", "fxparts.ctm", "fx_points")] == [0x1007, 0x100D]
    assert bad == []


def test_the_second_set_states(fx_install):
    """The states the second set is made to have: a stale page and genie, placeholders whose
    library is back, a template stored last, the workspace, the startup pages, a cycle."""
    from ctgedit.editing import EditableDocument
    from ctgedit.features import sa_workspace as SA
    from ctgedit.format.projects import include_search_dirs
    from ctgedit.ui.simulate import sa_start

    with hermetic.guarding() as bad:

        def page(name, project="FxMain"):
            return EditableDocument.open(fx_install.page(name, project))

        for name in (
            "FxTplMarker",
            "FxTplSets",
            "FxTplFlash",
            "FxTplLast",
            "FxHd1080",
            "FxHd768",
            "FxUnderlay",
        ):
            assert page(name).template_status()[0] == "current", name
        assert page("FxTplStale").template_status()[0] == "stale"
        assert page("FxSaContent", "FxSa").template_status()[0] == "current"
        doc = page("FxParts")
        assert doc.stale_genie_elements() == {("fxparts", "fx_stale"), ("fxparts", "fx_stale_note")}
        i = doc.find_objects_by_name("FxStale1")[0]
        assert doc.genie_bindings(i).get("Equip") == "FxPlant.Pump1"  # no longer declared
        assert "Equip" not in doc.genie_declared_params(i)
        assert len(page("FxReturned").placeholder_genies()) == 4
        assert len(page("FxComposites").composite_roots()) == 9
        doc = page("FxTplLast")
        assert doc.template_reference()[0] > doc.find_objects_by_name("FxBeforeTpl")[0]
        doc = page("FxMaster")
        rec = doc.page_record()
        assert rec["CON_TYPE"] == "FxMaster" and rec["READY_FUNC"]
        assert doc.page_keyboard_commands()[0]["KEY_SEQ"] == "LBUTTON_UP"
        doc = page("FxSaMaster", "FxSa")
        panes = SA.model_panes(doc.page)
        assert [p.name for p in panes] == [
            "Content",
            "HeaderBar",
            "FacePlate",
            "FacePlateHeader",
            "Navigation",
            "Info",
            "InfoHeader",
        ]
        assert [p.name for p in panes if p.is_default] == ["Content"]
        assert doc.genie_form(doc.find_objects_by_name("FxPaneContent")[0]).name == "pane"
        start = sa_start.workspace_start(fx_install.project("FxSa"))
        assert start and start[0] == "FxSaMaster"
        assert sa_start.workspace_start(fx_install.project("FxSim")) is None
        assert sa_start.startup_candidates(fx_install.project("FxSim"))[0] == "FxSim1"
        assert page("FxThumb").parsed.page.thumbnail
        chain = [os.path.basename(d) for d in include_search_dirs(fx_install.project("FxCycleA"))]
        assert "FxCycleB" in chain and "FxNowhere" not in chain
        func0 = fx_install.write_func0()
        assert os.path.dirname(func0) == fx_install.bin
        big = fx_install.big_library("FxMain", "fxbig.ctl", 12)
        assert _element_names(big)[-1] == "fx_gen0012" and len(_element_names(big)) == 12
    assert bad == []


def test_the_catalogue_says_what_is_stored(fx_install):
    """The fixture pages hold what they should: the unregistered pages
    have no rows, the captions and the state 0 colours survive their display keys, the
    trend count, and no name of the AVEVA projects."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G
    from ctgedit.format.projects import read_dbf_rows

    with hermetic.guarding() as bad:
        assert read_dbf_rows(fx_install.file("FxPerf", "pages.DBF")) == []
        for project, page in (("FxPerf", "FxLarge"), ("FxMain", "FxUnderlay")):
            folder = fx_install.project(project)
            for f in os.listdir(folder):
                if f.lower().startswith(("pg", "pages")) and f.lower().endswith(".dbf"):
                    rows = read_dbf_rows(os.path.join(folder, f))
                    assert not [
                        r for r in rows if page.lower() in {str(v).strip().lower() for v in r.values()}
                    ], (page, f)
        doc = EditableDocument.open(fx_install.page("FxTextKinds"))
        objs = doc.parsed.page.objects

        def tail(name):
            return G.parse_text_tail(objs[doc.find_objects_by_name(name)[0]])["text"]

        assert tail("FxNumEquip") == "Idle"
        assert tail("FxNumField") == "" and tail("FxNumOverride") == ""
        assert tail("FxNumParen") == "####.#"
        i = doc.find_objects_by_name("FxColStaText")[0]
        assert dict(doc.object_properties(i))["COL_STA_0"] == "0x00ff0000,0x00ff0000"  # red
        doc = EditableDocument.open(fx_install.page("FxColours"))
        for name, key in (("FxCm1", "COL_STA_0"), ("FxCm2", "COL_ARR_0")):
            props = dict(doc.object_properties(doc.find_objects_by_name(name)[0]))
            assert props[key] == "0x00ff0000,0x00ff0000", name
        # the AVEVA example projects' names: none in any fixture file
        foreign = (b"PMP05", b"SPRAY02", b"TopMilk", b"F & B", b"Mixing")
        for d, _s, files in os.walk(fx_install.user):
            for f in files:
                with open(os.path.join(d, f), "rb") as fh:
                    data = fh.read()
                assert not [n for n in foreign if n in data], f
    assert bad == []


def test_every_page_table_index_finds_its_rows():
    """Each project's pages.NDX holds a NAME entry for every live row of its pages.DBF (the
    generator writes the index again from the table: a page save appends the row only)."""
    from ctgedit.format import dbf

    checked = 0
    for project in fxinstall.PROJECTS:
        folder = os.path.join(fxinstall.FX_INSTALL, "User", project)
        table, index = (os.path.join(folder, "pages" + e) for e in (".DBF", ".NDX"))
        if not os.path.exists(table):
            continue
        with dbf.Dbf(table, writable=False) as t:
            rows = [
                (r + 1, t.field(rec, "NAME"))
                for r, rec in ((r, t.read(r)) for r in range(t.nrec))
                if rec[:1] != b"*"
            ]
        with dbf.Ndx(index, writable=False) as x:
            assert sorted(r for _k, r in x.entries()) == [r for r, _n in rows], project
            for r, name in rows:
                assert r in x.find(name.strip()), (project, name)
        checked += len(rows)
    assert checked >= 40


def test_the_install_stays_small():
    """The committed install stays under 8.5 MB (the page registers' DBFs are most of it:
    wide rows of spaces, which git compresses to almost nothing)."""
    total = sum(
        os.path.getsize(os.path.join(d, f)) for d, _s, files in os.walk(fxinstall.FX_INSTALL) for f in files
    )
    assert total < 8.5 * 1024 * 1024, total


def test_the_old_forms_read_as_the_current_one(fx_install):
    from ctgedit.format import writer
    from ctgedit.format.page import parse_ctg

    pages = {w: parse_ctg(fx_install.page("FxOld%d" % w)) for w in (9, 10, 11)}
    for w, p in pages.items():
        assert p.container.upgraded_from == w
    first = [writer.object_payload(po) for po in pages[9].page.objects]
    for w in (10, 11):
        assert [writer.object_payload(po) for po in pages[w].page.objects] == first


def test_downgrade_is_the_inverse_of_the_upgrade(fx_install):
    from ctgedit.format import writer
    from ctgedit.format.page import parse_ctg_bytes

    gen = _generator()
    with open(fx_install.page("FxShapes"), "rb") as fh:
        data = fh.read()
    with pytest.raises(ValueError):  # named records have no word2 9 form
        gen.downgrade(data, 9)
    old = gen.downgrade(data, 11)
    p = parse_ctg_bytes(old)
    assert p.container.upgraded_from == 11
    assert [writer.object_payload(po) for po in p.page.objects] == [
        writer.object_payload(po) for po in parse_ctg_bytes(data).page.objects
    ]


@pytest.mark.skipif(
    os.environ.get("CTG_FIXTURE_CHECK") != "1" or os.name != "nt",
    reason="the fixture regeneration check: set CTG_FIXTURE_CHECK=1 (Windows)",
)
def test_the_committed_fixtures_are_what_the_generator_writes():
    env = dict(os.environ, PYTHONPATH=REPO)
    env.pop("CTG_HERMETIC", None)
    r = subprocess.run(
        [sys.executable, os.path.join(REPO, "tools", "make_fixtures.py"), "--check"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, r.stdout + r.stderr
