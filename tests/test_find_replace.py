"""Find / Replace."""

import os
from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")


def _doc(fx_install, name):
    """Page ``name`` of FxMain in the test's fx_install copy, opened."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page(name))
    doc.derive_check = True  # every re-derive == a full one
    return doc


# --------------------------------------------------------------------------- matching
def test_match_modes():
    from ctgedit.features import find_replace as FR

    s = "LOOP_1_SP + loop_10 + Loop_1.PV"
    assert FR.match_spans(s, "LOOP_1") == [(0, 6), (12, 18), (22, 28)]  # any case
    # a whole tag name / word: not inside LOOP_1_SP or LOOP_10; '.' ends a name
    assert FR.match_spans(s, "LOOP_1", FR.MATCH_WORD) == [(22, 28)]
    assert FR.match_spans(s, "LOOP_1", FR.MATCH_WHOLE) == []
    assert FR.match_spans("Loop_1", "LOOP_1", FR.MATCH_WHOLE) == [(0, 6)]
    # a needle that ends in a non-name character needs no boundary there
    assert FR.match_spans("f(LOOP_1)", "LOOP_1)", FR.MATCH_WORD) == [(2, 9)]
    assert FR.replace_spans(s, FR.match_spans(s, "loop_1"), "LOOP_9") == "LOOP_9_SP + LOOP_90 + LOOP_9.PV"
    assert FR.match_spans("", "x") == [] and FR.match_spans("x", "") == []


# --------------------------------------------------------------------------- acceptance
def test_replace_in_expressions_is_one_undo_step_and_pg_rows_follow(fx_install):
    """Replacing a tag in FxGenies' expressions: every field that uses it changes, as ONE
    undo step; the page's pg*.DBF rows (what the save registers, io.pgregister.page_rows)
    follow. FX_PUMP1 is FxPump1's Tag: four rows (the parameter, and the members' COL_A,
    touch CMD and TXT_STR it feeds)."""
    from ctgedit.features import find_replace as FR
    from ctgedit.io import pgregister

    doc = _doc(fx_install, "FxGenies")
    exprs = {FR.SCOPE_EXPR, FR.SCOPE_PARAM, FR.SCOPE_NAME}
    hits = doc.find_text("FX_PUMP1", scopes=exprs)
    assert len(hits) == 4
    assert any(h.kind == FR.K_PARAM for h in hits)  # the genie parameter row
    assert any(h.key == "TXT_STR" for h in hits)  # an expression
    member = [h for h in hits if h.kind != FR.K_PARAM]
    assert member and all(h.params == ("Tag",) and not h.reason for h in member)
    rows_before = pgregister.page_rows(doc.serialize(), "FxGenies")
    assert "FX_PUMP1" in str(rows_before)
    n_undo = len(doc._undo)
    rep = doc.replace_text("FX_PUMP1", "FX_PUMP7", scopes=exprs)
    assert rep.replaced >= 1 and not rep.problems
    assert len(rep.docs) == 1 and rep.docs[0] is doc
    assert len(doc._undo) == n_undo + 1 and doc._undo[-1].label == "replace"
    assert doc.find_text("FX_PUMP1", scopes=exprs) == []
    assert len(doc.find_text("FX_PUMP7", scopes=exprs)) == len(hits)
    rows = str(pgregister.page_rows(doc.serialize(), "FxGenies"))
    assert "FX_PUMP1" not in rows and "FX_PUMP7" in rows
    # the save writes what the register reads
    doc.save()
    from ctgedit.editing import EditableDocument

    again = EditableDocument.open(doc.path)
    assert len(again.find_text("FX_PUMP7", scopes=exprs)) == len(hits)
    assert "FX_PUMP7" in str(pgregister.page_rows(doc.path, "FxGenies"))
    # one undo takes the whole replace back, redo re-applies it
    doc.undo()
    assert len(doc.find_text("FX_PUMP1", scopes=exprs)) == len(hits)
    doc.redo()
    assert doc.find_text("FX_PUMP1", scopes=exprs) == []


def test_loop_tag_replace_across_a_genie_page(fx_install):
    """'FX_LOOP_4' on FxParts: FxLoop4's LOOP parameter and the metadata its member
    derives from it (%LOOP%_SP, %LOOP%_PV) change together (one undo)."""
    doc = _doc(fx_install, "FxParts")
    hits = doc.find_text("FX_LOOP_4")
    assert {h.kind for h in hits} >= {"param", "metadata"}
    n = len(doc._undo)
    rep = doc.replace_text("FX_LOOP_4", "FX_LOOP_7")
    assert rep.replaced and len(doc._undo) == n + 1
    assert doc.find_text("FX_LOOP_4") == []
    root = next(h.index for h in hits if h.kind == "param")
    assert doc.find_objects_by_name("FxLoop4") == [root]
    assert doc.genie_all_params(root)["LOOP"] == "FX_LOOP_7"
    meta = [h for h in doc.find_text("FX_LOOP_7") if h.kind == "metadata"]
    assert meta and meta[0].value == "FX_LOOP_7_SP"


# --------------------------------------------------------------------------- genie routing
def test_genie_member_hit_changes_the_genie_parameter(fx_install):
    """A hit in a genie member changes the genie's parameter."""
    from ctgedit.features import find_replace as FR
    from ctgedit.format import genie_place as GP

    doc = _doc(fx_install, "FxParts")
    hits = doc.find_text("FX_T201_SP", scopes={FR.SCOPE_EXPR})
    cmd = next(h for h in hits if h.key == "CMD")
    assert cmd.params == ("TAG",) and cmd.genie is not None and not cmd.reason
    root = cmd.genie
    n = len(doc._undo)
    rep = doc.replace_text("FX_T201_SP", "FX_T202_SP", hits=[cmd], scopes={FR.SCOPE_EXPR})
    assert rep.replaced == 1 and len(doc._undo) == n + 1
    assert doc.genie_all_params(root)["TAG"] == "FX_T202_SP"
    # every member the parameter feeds followed, not only the one replaced
    after = {h.key: h.value for h in doc.find_text("FX_T202_SP", scopes={FR.SCOPE_EXPR})}
    assert after["CMD"] == "FX_T202_SP = ArgValue1" and after["TXT_STR"] == "FX_T202_SP"
    objs = doc.parsed.page.objects
    i = next(
        k for k in doc.subtree(root) if objs[k].type_code == 0x100B and "CMD" in dict(FR.own_values(doc, k))
    )
    assert GP.decode(objs[i]).entries[0][2].get("CMD") == "%TAG% = ArgValue1"


def test_member_hit_changes_only_the_parameters_its_field_uses(fx_install):
    """'Pump' is in two parameters of FxGenies' FxPump1 (Tag 'FX_PUMP1', Label
    'Pump 1'; the search is without case); its caption comes from Label alone, its
    COL_A from Tag alone (the member's own template names them). Replacing the caption
    changes Label only: the tag stays."""
    from ctgedit.features import find_replace as FR

    doc = _doc(fx_install, "FxGenies")
    hits = doc.find_text("Pump")
    root = next(h.index for h in hits if h.kind == FR.K_PARAM and h.key == "Tag" and h.value == "FX_PUMP1")
    assert doc.find_objects_by_name("FxPump1") == [root]
    params = dict(doc.genie_all_params(root))
    assert "Pump" in params["Label"]
    mine = [h for h in hits if h.genie == root and h.kind != FR.K_PARAM]
    cap = next(h for h in mine if h.kind == FR.K_CAPTION)
    col = next(h for h in mine if h.key == "COL_A")
    assert cap.params == ("Label",) and col.params == ("Tag",)
    rep = doc.replace_text("Pump", "Motor", hits=[cap])
    assert rep.replaced == 1 and not rep.problems
    after = dict(doc.genie_all_params(root))
    assert after["Tag"] == "FX_PUMP1"
    assert after["Label"] == params["Label"].replace("Pump", "Motor")
    assert dict(FR.own_values(doc, col.index))["COL_A"] == col.value


def test_nested_genie_member_is_traced_through_the_nested_binding(fx_install):
    """A nested genie member is traced through the nested binding."""
    from ctgedit.features import find_replace as FR

    doc = _doc(fx_install, "FxGenies")
    g = doc.place_library_element(
        "fxnest", "n4", "ctm", (800, 400), params={"Name": "FX_T201", "Extra": "FX_T201 tip"}
    )
    assert g is not None
    objs = doc.parsed.page.objects
    hits = [h for h in doc.find_text("FX_T201") if h.genie == g]
    nested = {}
    for h in hits:
        genies = [a for a in doc.page.ancestors(h.index) if objs[a].subtype_size & 0x200]
        if len(genies) >= 2 and h.kind == FR.K_CAPTION:
            nested[h.value] = h
    assert set(nested) == {"EFX_T201 tip", "FX_T201 3", "FX_T201 3 L1"}, sorted(nested)
    outer = FR.genie_params(doc, g)
    assert "FX_T201" in outer["Name"] and "FX_T201" in outer["Extra"]
    assert nested["EFX_T201 tip"].params == ("Extra",)
    assert nested["FX_T201 3"].params == ("Name",)
    assert nested["FX_T201 3 L1"].params == ("Name",)  # two bindings down
    assert not any(x.reason for x in nested.values())


def test_parameter_replace_keeps_member_access_values(fx_install):
    """A genie member's own tool tip / description ('value<User>', what
    CtDraw's member sheet writes and its page load keeps) survives the
    re-composition a genie parameter replace makes (FxGenies' FxPump1, Tag FX_PUMP1:
    its only parameter row of that text)."""
    from ctgedit.features import find_replace as FR
    from ctgedit.format import genie_place as GP

    doc = _doc(fx_install, "FxGenies")
    hits = doc.find_text("FX_PUMP1", scopes={FR.SCOPE_PARAM})
    (root,) = doc.find_objects_by_name("FxPump1")
    assert [h.index for h in hits] == [root]
    member = doc.subtree(root)[1]  # its first member (the body)
    assert doc.ensure_property(member, "COMMENT", "Operator note")
    assert doc.ensure_property(member, "DESC", "Recipe pump")
    rep = doc.replace_text("FX_PUMP1", "FX_PUMP9", scopes={FR.SCOPE_PARAM})
    assert rep.replaced == 1
    assert doc.genie_all_params(root)["Tag"] == "FX_PUMP9"
    r = GP.decode(doc.parsed.page.objects[member])
    for b in (1, 2):
        assert r.entries[0][b].get("COMMENT") == "Operator note<User>"
        assert r.entries[0][b].get("DESC") == "Recipe pump<User>"
    # the member's own Access field is found (without the marker) and changes directly
    tip = [h for h in doc.find_text("Operator note") if h.index == member]
    assert tip and tip[0].value == "Operator note" and not tip[0].reason and not tip[0].params
    doc.replace_text("Operator note", "Shift note", hits=tip)
    assert GP.decode(doc.parsed.page.objects[member]).entries[0][1].get("COMMENT") == "Shift note<User>"


def test_genie_definition_text_is_read_only(fx_install):
    """A genie member's field its DEFINITION holds (no %parameter% in its
    template) cannot be replaced -- Studio's 'Field is Read-only'; Replace All
    skips it and says so (FxParts' FxSetpoint1: its keyboard command's KEY_SEQ
    ENTER)."""
    doc = _doc(fx_install, "FxParts")
    hits = [h for h in doc.find_text("ENTER") if h.key == "KEY_SEQ"]
    assert hits and all(h.reason and "definition" in h.reason for h in hits)
    n = len(doc._undo)
    rep = doc.replace_text("ENTER", "RETURN", hits=hits)
    assert rep.replaced == 0 and rep.read_only == len(hits)
    assert len(doc._undo) == n and not doc.dirty


def test_inside_genies_option_and_scopes(fx_install):
    """FxParts' FxSetpoint1 (TAG FX_T201_SP; its member's metadata Comment 'Setpoint',
    its own name FxSetpoint1): the name scope finds names and metadata, never texts."""
    from ctgedit.features import find_replace as FR

    doc = _doc(fx_install, "FxParts")
    all_hits = doc.find_text("FX_T201_SP")
    outside = doc.find_text("FX_T201_SP", inside_genies=False)
    # without the genies' objects only the genie's parameter row is left
    assert outside and all(h.kind == FR.K_PARAM for h in outside)
    assert len(all_hits) > len(outside)
    only_params = doc.find_text("FX_T201_SP", scopes={FR.SCOPE_PARAM})
    assert [h.kind for h in only_params] == [FR.K_PARAM]
    names = doc.find_text("Setpoint", scopes={FR.SCOPE_NAME})
    assert {h.kind for h in names} == {FR.K_NAME, FR.K_META}
    assert [h.value for h in names if h.kind == FR.K_META] == ["Setpoint"]
    assert doc.find_text("Setpoint", scopes={FR.SCOPE_TEXT}) == []


def test_whole_field_and_whole_name(fx_install):
    from ctgedit.features import find_replace as FR

    doc = _doc(fx_install, "FxParts")
    part = doc.find_text("FX_LOOP_4")
    word = doc.find_text("FX_LOOP_4", match=FR.MATCH_WORD)
    whole = doc.find_text("fx_loop_4", match=FR.MATCH_WHOLE)
    assert any(h.value == "FX_LOOP_4_SP" for h in part)
    assert all(h.value != "FX_LOOP_4_SP" for h in word) and word
    assert whole and all(h.value.lower() == "fx_loop_4" for h in whole)


# --------------------------------------------------------------------------- own fields
def test_caption_replace_writes_the_display_value_text(fx_install):
    """A text's caption changes the way CtDraw's Display Value apply writes
    it: the text tail the loader reads and TXT_OFF together."""
    from ctgedit.features import find_replace as FR

    doc = _doc(fx_install, "FxParts")
    i = doc.create_object("text", (40, 40, 160, 60), None, text="Pump 1 running")
    assert [h.kind for h in doc.find_text("pump 1", scopes={FR.SCOPE_TEXT}) if h.index == i] == [FR.K_CAPTION]
    n = len(doc._undo)
    rep = doc.replace_text("pump 1", "Motor 7", scopes={FR.SCOPE_TEXT})
    assert rep.replaced == 1 and len(doc._undo) == n + 1
    assert doc.text_caption(i) == "Motor 7 running"
    assert doc.object_properties(i).get("TXT_OFF") == "Motor 7 running"
    from ctgedit.editing import EditableDocument

    doc.save()
    again = EditableDocument.open(doc.path)
    assert again.text_caption(i) == "Motor 7 running"


def test_names_metadata_and_too_long_values(fx_install):
    from ctgedit.features import find_replace as FR

    doc = _doc(fx_install, "FxParts")
    i = doc.create_object("rectangle", (10, 10, 60, 60), None)
    doc.set_object_name(i, "Tank_A")
    doc.set_metadata(i, [("Equipment", "Tank_A.Level")])
    hits = [h for h in doc.find_text("Tank_A") if h.index == i]
    assert {h.kind for h in hits} == {FR.K_NAME, FR.K_META}
    rep = doc.replace_text("Tank_A", "Tank_B", hits=hits)
    assert rep.replaced == 2
    assert doc.object_name(i) == "Tank_B"
    assert doc.metadata(i) == [("Equipment", "Tank_B.Level")]
    # a value past the field's limit is refused with the reason, nothing else changes
    doc.ensure_property(i, "VISIBLE", "Tank_B.Level > 1")
    n = len(doc._undo)
    rep = doc.replace_text("Tank_B.Level", "X" * 300, scopes={FR.SCOPE_EXPR})
    assert rep.replaced == 0 and rep.problems and "characters" in rep.problems[0]
    assert len(doc._undo) == n


def test_legacy_name_key_is_the_object_name(fx_install):
    """An object named by the legacy NAME key (an older builder's form; its
    header holds the same name) is ONE 'Object name' row, and a Replace
    renames it the way the Access page does -- the NAME key and the header
    together, no __AN_NAME__ added."""
    from ctgedit.features import find_replace as FR
    from ctgedit.format import geometry as G

    doc = _doc(fx_install, "FxParts")
    i = doc.create_object("rectangle", (10, 10, 60, 60), None)
    assert doc.ensure_property(i, "NAME", "Tank_L")
    doc.set_object_name(i, "Tank_L")
    assert "__AN_NAME__" not in doc.object_properties(i)
    hits = [h for h in doc.find_text("Tank_L") if h.index == i]
    assert [(h.kind, h.key) for h in hits] == [(FR.K_NAME, "NAME")]
    rep = doc.replace_text("Tank_L", "Tank_R", hits=hits)
    assert rep.replaced == 1
    props = doc.object_properties(i)
    assert props.get("NAME") == "Tank_R" and "__AN_NAME__" not in props
    assert G.parse_geom_header(doc.parsed.page.objects[i].body_raw).name == "Tank_R"


def test_template_objects_read_only_unless_allowed(fx_install):
    """FxTplLinked on fx_style normal@XGA: the template's title caption 'FxTitle'."""
    from ctgedit.features import find_replace as FR

    doc = _doc(fx_install, "FxTplLinked")
    sem = doc.page.objects
    tpl = [
        h for h in doc.find_text("FxTitle", inside_templates=True) if sem[h.index].raw.get("_from_template")
    ]
    assert tpl and all(h.reason == FR.TEMPLATE_REASON for h in tpl)
    assert not [
        h for h in doc.find_text("FxTitle") if h.po is not None and sem[h.index].raw.get("_from_template")
    ]
    free = doc.find_text("FxTitle", inside_templates=True, template_editable=True)
    assert any(sem[h.index].raw.get("_from_template") and not h.reason for h in free)
    # the template's own linked root is not a genie / symbol: 'Inside the page
    # template' finds its records with 'Inside genies and symbols' off too
    only_tpl = [
        h
        for h in doc.find_text("FxTitle", inside_templates=True, inside_genies=False)
        if h.po is not None and sem[h.index].raw.get("_from_template")
    ]
    assert only_tpl


def test_locked_object_is_read_only_until_break_lock(fx_install):
    """A locked object (Edit > Lock: dwFlags 0x4 on its unit's root) is not
    changed by Replace -- like every other edit of it -- until Break Lock
    Mode; the dialog passes the window's Break Lock Mode through."""
    _app()
    from ctgedit.features import find_replace as FR
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

    doc = _doc(fx_install, "FxParts")
    i = doc.create_object("text", (40, 40, 200, 60), None, text="Locked caption")
    assert doc.set_locked([i], True) == 1
    hits = [h for h in doc.find_text("Locked caption") if h.index == i]
    assert hits and all(h.reason == FR.LOCK_REASON for h in hits)
    n = len(doc._undo)
    rep = doc.replace_text("Locked caption", "Changed", hits=hits)
    assert rep.replaced == 0 and rep.read_only == len(hits) and len(doc._undo) == n
    assert doc.text_caption(i) == "Locked caption"
    free = [h for h in doc.find_text("Locked caption", break_lock=True) if h.index == i]
    assert free and not any(h.reason for h in free)
    rep = doc.replace_text("Locked caption", "Changed", hits=free, break_lock=True)
    assert rep.replaced == 1 and doc.text_caption(i) == "Changed"
    dlg = FindReplaceDialog(doc, break_lock=lambda: True)
    assert dlg.options().break_lock
    assert not FindReplaceDialog(doc).options().break_lock
    dlg.close()


def test_composite_genie_members_and_parameters_read_only(fx_install):
    """Composite genie members and parameters are read-only."""
    from ctgedit.features import find_replace as FR

    doc = _doc(fx_install, "FxComposites")
    hits = doc.find_text("Tank")
    params = [h for h in hits if h.kind == FR.K_PARAM]
    members = [h for h in hits if h.kind != FR.K_PARAM and h.genie is not None]
    assert params and all(h.reason == FR.COMPOSITE_PARAM_REASON for h in params)
    assert members and all(h.reason == FR.COMPOSITE_REASON for h in members)
    before = doc.serialize()
    rep = doc.replace_text("Tank", "Vessel", hits)
    assert rep.replaced == 0 and rep.read_only == len(hits)
    assert doc.serialize() == before


def _page_key(doc, seq):
    """The CMD of the page keyboard command ``seq`` (FxAnim: F2 and LBUTTON_UP)."""
    return next(k["CMD"] for k in doc.page_keyboard_commands() if k["KEY_SEQ"] == seq)


def test_page_properties_are_searched_and_replaced(fx_install):
    """Page properties are searched and replaced."""
    from ctgedit.features import find_replace as FR

    doc = _doc(fx_install, "FxAnim")
    hits = doc.find_text("Fx", scopes={FR.SCOPE_PAGE})
    kinds = {h.kind for h in hits}
    assert kinds >= {FR.K_PAGEREC, FR.K_PAGEKEY} and all(h.po is None for h in hits)
    n = len(doc._undo)
    rep = doc.replace_text("FX_MODE, 0", "FX_MODE, 2", scopes={FR.SCOPE_PAGE})
    assert rep.replaced == 1 and len(doc._undo) == n + 1
    assert _page_key(doc, "LBUTTON_UP") == "FxSet(FX_MODE, 2)"
    rep = doc.replace_text("Fx Start", "Fx Begin", scopes={FR.SCOPE_PAGE})
    assert rep.replaced == 1 and doc.page_record()["TITLE"] == "@(Fx Begin)"
    doc.undo()
    doc.undo()
    assert doc.page_record()["TITLE"] == "@(Fx Start)"
    assert _page_key(doc, "LBUTTON_UP") == "FxSet(FX_MODE, 0)"


def test_page_fields_are_replaced_one_by_one(fx_install):
    """Page fields are replaced one by one."""
    from ctgedit.features import find_replace as FR

    doc = _doc(fx_install, "FxAnim")
    long = "X" * 60
    others = [h for h in doc.find_text("Fx", scopes={FR.SCOPE_PAGE}) if h.key != "TITLE"]
    assert len(others) == 3
    rep = doc.replace_text("Fx", long, scopes={FR.SCOPE_PAGE})
    assert doc.page_record()["TITLE"] == "@(Fx Start)"
    assert any("64" in p for p in rep.problems)
    # (without case: FxSet(FX_MODE, 0) holds the text twice)
    assert _page_key(doc, "LBUTTON_UP") == long + "Set(" + long + "_MODE, 0)"
    assert _page_key(doc, "F2") == 'PageDisplay("%sShapes")' % long
    assert rep.replaced == len(others)
    doc.undo()
    assert doc.set_associations(
        [("Tag", "Tag"), ("Mode", "Alarm"), ("Pump_Area", "Alarm_A\n\nAlarm description")]
    )
    hits = doc.find_text("Alarm", scopes={FR.SCOPE_PAGE})
    assert {(h.key, h.sub) for h in hits if h.kind == FR.K_ASSOC} == {
        ("Mode", 0),
        ("Pump_Area", 0),
        ("Pump_Area", 2),
    }
    n = len(doc._undo)
    rep = doc.replace_text(
        "Alarm", "Warn", hits=[h for h in hits if h.kind == FR.K_ASSOC], scopes={FR.SCOPE_PAGE}
    )
    assert rep.replaced == 3 and len(doc._undo) == n + 1
    assert list(doc.parsed.attributes.list2.pairs) == [
        ("Tag", "Tag"),
        ("Mode", "Warn"),
        ("Pump_Area", "Warn_A\n\nWarn description"),
    ]


# --------------------------------------------------------------------------- the window
def test_dialog_find_next_both_directions_and_replace_all(fx_install):
    _app()
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

    doc = _doc(fx_install, "FxParts")
    seen = []
    dlg = FindReplaceDialog(doc, replace_mode=True)
    dlg.found.connect(lambda d, i: seen.append((d, i)))
    dlg._ed_find.setText("FX_T201_SP")
    rows = dlg.find_all()
    assert len(rows) == dlg._list.topLevelItemCount() >= 3
    dlg.find_next()
    first = dlg._cursor
    assert first >= 0 and seen[-1][0] is doc and seen[-1][1] == first
    dlg.find_next()
    second = dlg._cursor
    dlg._rb_back.setChecked(True)
    dlg.find_next()
    assert dlg._cursor == first and second >= first
    dlg._ed_repl.setText("FX_T203_SP")
    changed = []
    dlg.replaced.connect(changed.append)
    n = len(doc._undo)
    dlg.replace_all()
    assert len(changed) == 1 and [d is doc for d in changed[0]] == [True]
    assert len(doc._undo) == n + 1
    assert dlg.hits() == [] and "changed" in dlg._status.text()
    # one way back: Undo Replace All undoes the replace step it made
    assert not dlg._b_undo_all.isHidden()
    dlg.undo_replace_all()
    assert len(doc._undo) == n and dlg._b_undo_all.isHidden()
    assert "undone in 1 page" in dlg._status.text()
    assert len(dlg.find_all()) >= 3  # (the text is back)
    # a tab changed after the replace keeps its later change: its replace is left, and said
    dlg._ed_repl.setText("FX_T203_SP")
    dlg.replace_all()
    doc.move_objects([doc.page.roots()[0]], 1, 0)
    dlg.undo_replace_all()
    assert len(doc._undo) == n + 2 and "Not undone" in dlg._status.text()
    dlg.close()


def test_dialog_replace_moves_on_past_the_genie_it_changed(fx_install):
    """Replace on a genie member's row changes the genie's parameter (new
    member records); the next row is the NEXT genie's -- not the page's
    first row, and not a row of the same genie again when the replacement
    still contains the text (FX_LOOP_ -> FX_LOOP_X on FxParts' FxLoop4 .. FxLoop6)."""
    _app()
    from ctgedit.features import find_replace as FR
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

    doc = _doc(fx_install, "FxParts")
    dlg = FindReplaceDialog(doc, replace_mode=True)
    dlg._ed_find.setText("FX_LOOP_")
    dlg._ed_repl.setText("FX_LOOP_X")
    hits = dlg.find_all()
    roots = [h.index for h in hits if h.kind == FR.K_PARAM]
    assert len(roots) == 3
    assert roots == [doc.find_objects_by_name("FxLoop%d" % n)[0] for n in (4, 5, 6)]
    member = next(k for k, h in enumerate(hits) if h.genie == roots[0] and h.kind != FR.K_PARAM)
    dlg._list.setCurrentItem(dlg._list.topLevelItem(member))
    dlg.replace_one()
    assert doc.genie_all_params(roots[0])["LOOP"] == "FX_LOOP_X4"
    assert dlg.current_hit().index == roots[1]
    dlg.replace_one()  # the second genie's parameter row
    assert doc.genie_all_params(roots[1])["LOOP"] == "FX_LOOP_X5"
    assert dlg.current_hit().index == roots[2]
    assert doc.genie_all_params(roots[0])["LOOP"] == "FX_LOOP_X4"  # not replaced twice
    dlg.close()


def test_dialog_searches_all_open_pages(fx_install):
    """'All open pages' (CtDraw's 'All views'): the current page first, one
    undo step on each page Replace All changes (FX_PUMP1 is a parameter of genies on
    both FxParts and FxGenies)."""
    _app()
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

    a = _doc(fx_install, "FxParts")
    b = _doc(fx_install, "FxGenies")
    dlg = FindReplaceDialog(a, replace_mode=True, documents=lambda: [b, a], current=lambda: a)
    dlg._ed_find.setText("FX_PUMP1")
    assert {id(h.doc) for h in dlg.find_all()} == {id(a)}
    dlg._rb_all.setChecked(True)
    hits = dlg.find_all()
    assert {id(h.doc) for h in hits} == {id(a), id(b)} and hits[0].doc is a
    na, nb = len(a._undo), len(b._undo)
    dlg._ed_repl.setText("FX_PUMP5")
    dlg.replace_all()
    assert len(a._undo) == na + 1 and len(b._undo) == nb + 1
    assert a.find_text("FX_PUMP1") == [] and b.find_text("FX_PUMP1") == []
    dlg.close()


def test_goto_finds_non_drawable_records_by_an(fx_install):
    """Goto Object finds non-drawable records by AN."""
    _app()
    from ctgedit.format import geometry as G
    from ctgedit.ui.dialog.dialogs import GotoObjectDialog

    doc = _doc(fx_install, "FxLegacy")
    objs = doc.parsed.page.objects
    cic = next(i for i, po in enumerate(objs) if po.type_code == 0x1009)
    dlg = GotoObjectDialog(doc)
    dlg._filter.setText(str(G.live_an(objs[cic])))
    assert cic in dlg.matches()
    key = next(
        i for i, po in enumerate(objs) if po.type_code == 0x100B and doc.page.objects[i].parent is not None
    )
    owner = doc.page.objects[key].parent
    assert doc.find_objects_by_name("FxKeyText") == [owner]
    dlg._filter.setText("AN %d" % G.live_an(objs[key]))
    assert owner in dlg.matches() and key not in dlg.matches()
    dlg.close()


def test_old_dialog_api_still_finds(fx_install):
    """The dialog stays importable from ui.dialogs with its old entry points
    (_ed_find, find_next, _cursor)."""
    _app()
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

    doc = _doc(fx_install, "FxParts")
    f = FindReplaceDialog(doc)
    assert not f._ed_repl.isVisibleTo(f)  # Find: no replace fields
    f.set_replace_mode(True)
    assert f._ed_repl.isVisibleTo(f) and f.windowTitle() == "Replace"
    f._ed_find.setText("no such text anywhere")
    f.find_next()
    assert f._cursor == -1 and "not found" in f._status.text()
    f.close()


def test_window_find_survives_tab_switch_and_goes_to_hits(fx_install):
    """FxParts and FxGenies open, FxGenies shown; FX_PUMP1 is on both."""
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.open_document(fx_install.page("FxParts"))
        w.open_document(fx_install.page("FxGenies"))
        genies, loop = w.open_documents()
        w._find_dialog(False)
        dlg = w._find_dlg
        assert dlg is not None and dlg.isVisible()
        dlg._rb_all.setChecked(True)
        dlg._ed_find.setText("FX_PUMP1")
        hits = dlg.find_all()
        assert hits[0].doc is loop  # the page shown comes first
        other = next(h for h in hits if h.doc is genies and h.po is not None)
        dlg._list.setCurrentItem(dlg._list.topLevelItem(hits.index(other)))
        dlg.go_to()
        QtWidgets.QApplication.processEvents()
        assert w.doc is genies and w._sel == [other.index]
        assert w._find_dlg is dlg and dlg.isVisible()  # a tab switch keeps it open
        w._find_dialog(True)  # Ctrl+H: the same window
        assert w._find_dlg is dlg and dlg._ed_repl.isVisibleTo(dlg)
        dlg._ed_repl.setText("FX_PUMP8")
        dlg.replace_all()
        assert genies.dirty and loop.dirty
        assert all(e["doc"].find_text("FX_PUMP1") == [] for e in w._docs)
    finally:
        for e in w._docs:
            e["doc"].dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.close()


def test_dialog_becomes_column_previews_the_replacement(fx_install):
    """Replace (Ctrl+H): Find All's list has a Becomes column with each field after the
    replacement, following Replace with as it is typed; blank on a grey row; hidden in
    Find (Ctrl+F)."""
    _app()
    from ctgedit.ui.dialog.find_dialog import BECOMES, FindReplaceDialog

    doc = _doc(fx_install, "FxParts")
    dlg = FindReplaceDialog(doc, replace_mode=True)
    assert not dlg._list.isColumnHidden(BECOMES)
    assert dlg._list.headerItem().text(BECOMES) == "Becomes"
    dlg._ed_find.setText("FX_T201_SP")
    rows = dlg.find_all()
    assert rows
    items = [dlg._list.topLevelItem(k) for k in range(dlg._list.topLevelItemCount())]
    # nothing typed yet: the match is taken out (what Replace All would do)
    assert all("FX_T201_SP" not in it.text(BECOMES) for it, h in zip(items, rows) if not h.reason)
    dlg._ed_repl.setText("FX_T203_SP")
    for it, h in zip(items, rows):
        if h.reason:
            assert it.text(BECOMES) == ""
        else:
            assert "FX_T203_SP" in it.text(BECOMES) and "FX_T201_SP" not in it.text(BECOMES)
            assert it.toolTip(BECOMES) == dlg.becomes(h)
    # the preview follows the text, with no new search
    dlg._ed_repl.setText("TANK_SP")
    assert any("TANK_SP" in it.text(BECOMES) for it in items)
    assert dlg._list.topLevelItemCount() == len(rows)
    dlg.set_replace_mode(False)
    assert dlg._list.isColumnHidden(BECOMES)
    dlg.close()


def test_the_dialog_comes_back_with_what_was_searched_last(fx_install):
    """Find / Replace remembers the last find and replace texts: the window is made again
    each time it opens, and starts from them (the find text selected, so typing replaces
    it)."""
    _app()
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

    doc = _doc(fx_install, "FxParts")
    dlg = FindReplaceDialog(doc, replace_mode=True)
    dlg._ed_find.setText("FX_LOOP_")
    dlg._ed_repl.setText("FX_LOOP_X")
    dlg.done(0)
    again = FindReplaceDialog(doc, replace_mode=True)
    assert again._ed_find.text() == "FX_LOOP_" and again._ed_repl.text() == "FX_LOOP_X"
    assert again._ed_find.selectedText() == "FX_LOOP_"
    again.done(0)


def test_a_changed_query_lists_its_own_matches(fx_install):
    """After a search, a new query or scope lists its matches a moment later (no Go To on a
    row of the old one meanwhile); a filter that does not compile clears the list until
    the next search."""
    _app()
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

    doc = _doc(fx_install, "FxParts")
    dlg = FindReplaceDialog(doc)
    dlg._ed_find.setText("FX_")
    dlg.find_all()
    assert dlg.hits()
    dlg._list.setCurrentItem(dlg._list.topLevelItem(0))
    assert dlg._b_goto.isEnabled()
    dlg._ed_find.setText("zznomatch99")
    assert not dlg._b_goto.isEnabled() and dlg._refresh.isActive()
    dlg._refresh_now()
    assert dlg._list.topLevelItemCount() == 0 and dlg.hits() == []
    assert dlg._status.text() == "'zznomatch99' was not found."
    dlg._ed_find.setText("FX_")
    dlg._refresh_now()
    n = len(dlg.hits())
    assert n and dlg._list.topLevelItemCount() == n
    dlg._filter_list.set_state([{"kind": "fields", "text": "(", "exclude": False, "regex": True}])
    assert dlg._filter_problem()
    assert dlg._list.topLevelItemCount() == 0 and not dlg._following and not dlg._refresh.isActive()
    dlg.done(0)


def test_before_a_search_a_change_lists_nothing(fx_install):
    _app()
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

    doc = _doc(fx_install, "FxParts")
    dlg = FindReplaceDialog(doc)
    dlg._ed_find.setText("FX_")
    assert not dlg._refresh.isActive() and dlg.hits() == []
    dlg.done(0)


def test_the_options_are_folded_and_say_what_is_not_the_default(fx_install):
    """Include, Search in, Match and Direction sit under Options, closed by default; closed,
    its line names what differs from the defaults; the helpers' row shows for a project
    search only."""
    _app()
    from ctgedit.features import find_replace as FR
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog
    from ctgedit.ui.prefs.settings import app_settings

    doc = _doc(fx_install, "FxParts")
    dlg = FindReplaceDialog(doc, replace_mode=True)
    try:
        assert not dlg._opt_toggle.isChecked() and dlg._opt_box.isHidden()
        assert dlg._opt_toggle.text() == "Options"
        dlg._rb_match[FR.MATCH_WHOLE].setChecked(True)
        dlg._rb_back.setChecked(True)
        assert dlg._opt_toggle.text() == "Options: match whole field only; backward"
        dlg._opt_toggle.setChecked(True)
        assert not dlg._opt_box.isHidden() and dlg._opt_toggle.text() == "Options"
        assert app_settings().value("find/options_shown", False, type=bool)
        assert dlg._procs_row.isHidden()
        dlg._rb_project.setChecked(True)
        assert not dlg._procs_row.isHidden()
    finally:
        dlg.close()


def test_the_kept_search_fields_follow_an_edit_a_replace_and_an_undo(fx_install):
    """A search keeps each record's searchable fields while the document is the same; a
    caption edited, replaced or put back by Undo is found as it is now."""
    from ctgedit.features import find_replace as FR

    doc = _doc(fx_install, "FxParts")
    i = doc.create_object("text", rect=(10, 10, 160, 30), text="ZQX_START")
    opts = lambda t: FR.FindOptions(text=t)  # noqa: E731
    assert not FR.find_hits(doc, opts("ZQX_EDITED"))
    assert doc.set_text(i, "ZQX_EDITED")
    assert [h.index for h in FR.find_hits(doc, opts("ZQX_EDITED"))] == [i]
    FR.replace_all([doc], opts("ZQX_EDITED"), "ZQX_REPLACED")
    assert not FR.find_hits(doc, opts("ZQX_EDITED"))
    assert [h.index for h in FR.find_hits(doc, opts("ZQX_REPLACED"))] == [i]
    doc.undo()
    assert [h.index for h in FR.find_hits(doc, opts("ZQX_EDITED"))] == [i]
    # a search without the texts decodes no caption, and a later one with them finds it
    no_text = FR.FindOptions(text="ZQX_EDITED", scopes=FR.ALL_SCOPES - {FR.SCOPE_TEXT})
    assert not FR.find_hits(doc, no_text)
    assert [h.index for h in FR.find_hits(doc, opts("ZQX_EDITED"))] == [i]


# --------------------------------------------------------------------------- filters
def test_name_filter_keeps_what_matches_and_leaves_out_the_rest():
    from ctgedit.features import find_replace as FR

    f = FR.NameFilter("^pump", "2$")
    assert f.on and f.allows(["Pump1"]) and not f.allows(["Pump2"]) and not f.allows(["Valve1"])
    assert f.allows(["Valve", "pump7"])  # (one name of the list is enough)
    assert not f.allows([]) and FR.NameFilter("", "x").allows([])
    assert not FR.NameFilter().on and FR.NameFilter().allows([])
    assert FR.NameFilter("(").problem() and not FR.NameFilter("a|b").problem()
    assert not FR.NameFilter("(").allows(["("])  # (a pattern that does not compile keeps nothing)
    assert FR.FindOptions("x", fields=FR.NameFilter("", "[")).filter_problem().startswith("Fields: ")


def test_filters_by_genie_object_field_and_page(fx_install):
    """FxGenies: FxPump1..3 (fxgenie.fx_pump, Tag), FxValve1..8 (fx_valve,
    TagPrefix), FxKeys (fx_keys) and FxLamp (fx_lamp, Tag FX_PUMP3)."""
    from ctgedit.features import find_replace as FR

    doc = _doc(fx_install, "FxGenies")
    every = doc.find_text("FX_")
    pumps = doc.find_text("FX_", genies=r"\.fx_pump$")
    assert pumps and {h.genie for h in pumps if h.genie is not None} <= {
        h.index for h in pumps if h.kind == FR.K_PARAM
    }
    assert {h.value for h in pumps if h.kind == FR.K_PARAM} == {"FX_PUMP1", "FX_PUMP2", "FX_PUMP3"}
    no_valves = doc.find_text("FX_", genies=("", "valve"))
    assert len(no_valves) == len(every) - len([h for h in every if h.value.startswith("FX_V")])
    # an object by its name or AN: its own rows and those of the objects inside it
    p3 = doc.find_text("FX_", objects="^FxPump3$")
    assert p3 and all(h.value.endswith("FX_PUMP3)") or h.value == "FX_PUMP3" for h in p3)
    assert len(p3) < len([h for h in every if "FX_PUMP3" in h.value])  # (FxLamp's are left out)
    an = next(h for h in p3 if h.kind == FR.K_PARAM).index
    assert len(doc.find_text("FX_", objects="^AN%d$" % FR.G.live_an(doc.parsed.page.objects[an]))) == len(p3)
    # fields by key or by the Field column's name; a genie parameter by its name
    assert {h.key for h in doc.find_text("FX_", fields="^CMD$")} == {"CMD"}
    assert {h.key for h in doc.find_text("FX_", fields="^Command")} == {"CMD"}
    assert {(h.kind, h.key) for h in doc.find_text("FX_", fields="^TagPrefix$")} == {
        (FR.K_PARAM, "TagPrefix")
    }
    assert all(h.key != "COL_A" for h in doc.find_text("FX_", fields=("", "COL_A")))
    # the page by its name; a Replace keeps to what the filters keep
    assert doc.find_text("FX_", pages="^FxGen") and doc.find_text("FX_", pages=("", "genies$")) == []
    rep = doc.replace_text("FX_PUMP3", "FX_PUMP9", genies="fx_lamp")
    assert rep.replaced == 1
    assert doc.genie_all_params(next(h.index for h in p3 if h.kind == FR.K_PARAM))["Tag"] == "FX_PUMP3"


def test_filters_reach_the_closed_pages_helper(fx_install):
    from ctgedit.features import find_replace as FR
    from ctgedit.workers import find_worker as FW

    opts = FR.FindOptions(
        "FX_",
        pages=FR.NameFilter("^FxGenies$"),
        fields=FR.NameFilter("", "COL_A"),
        genies=FR.NameFilter("pump"),
    )
    assert FW.options_from_json(FW.options_to_json(opts)) == opts
    assert FW.options_from_json(FW.options_to_json(FR.FindOptions("x"))) == FR.FindOptions("x")
    proj = os.path.dirname(fx_install.page("FxGenies"))
    ans = FW.find({"format": FW.FORMAT, "projects": [proj], "options": FW.options_to_json(opts)})
    rows = ans["rows"]
    assert rows and {r["name"] for r in rows} == {"FxGenies"}
    assert all(r["key"] != "COL_A" for r in rows)
    assert {r["value"] for r in rows if r["kind"] == FR.K_PARAM} == {"FX_PUMP1", "FX_PUMP2", "FX_PUMP3"}


def test_the_dialog_filters(fx_install):
    """Filter rows with their own '.*'; a bad pattern frames its box and stops the search; the
    folded Options line names the filters; they come back with the window."""
    _app()
    from ctgedit.ui.dialog.find_dialog import FindReplaceDialog

    doc = _doc(fx_install, "FxGenies")
    dlg = FindReplaceDialog(doc, replace_mode=True)
    try:
        dlg._ed_find.setText("FX_")
        n = len(dlg.find_all())
        lst = dlg._filter_list
        assert not lst.rows and not lst.note.isHidden()
        valve = lst.add("genies", "fx_valve")
        assert lst.note.isHidden()
        assert dlg._opt_toggle.text() == "Options: genies and symbols matching fx_valve"
        valves = dlg.find_all()
        assert 0 < len(valves) < n and all(h.value.startswith("FX_V") for h in valves)
        # plain text: '.' is a dot; with .* on it is any character
        dot = lst.add("objects", "Valve.$", exclude=True)
        assert len(dlg.find_all()) == len(valves)
        dot.regex.set_on(True)
        assert dlg.find_all() == []
        dot.edit.setText("(")
        assert dot.edit.property("regexInvalid") is True and valve.edit.property("regexInvalid") is not True
        assert (
            not dlg._b_all.isEnabled() and "objects filter is not a regular expression" in dlg._status.text()
        )
        dot.edit.setText("Valve8$")
        assert dlg._b_all.isEnabled() and len(dlg.find_all()) == len(valves) - 3
        # two 'match' rows of one kind: either is enough
        lst.add("genies", "fx_pump")
        assert len(dlg.find_all()) > len(valves) - 3
        lst.remove(lst.rows[-1])
    finally:
        dlg.close()
    again = FindReplaceDialog(doc, replace_mode=True)
    try:
        assert [r.state() for r in again._filter_list.rows] == [
            {"kind": "genies", "exclude": False, "text": "fx_valve", "regex": False},
            {"kind": "objects", "exclude": True, "text": "Valve8$", "regex": True},
        ]
    finally:
        again.close()
