"""Save chain leftovers."""

import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ------------------------------------------------------------------ the last-dot rule
def test_a_template_reference_of_a_dotted_library_splits_at_the_last_dot():
    from ctgedit.format import template_place as TP

    assert TP.parse_ref_text("fx_style.normal@XGA.<ctt") == ("fx_style", "normal@XGA", "ctt")
    assert TP.parse_ref_text("plant.v2.normal@XGA.<ctt") == ("plant.v2", "normal@XGA", "ctt")
    assert TP.parse_ref_text("plant.v2.normal") == ("plant.v2", "normal", "")
    ref = TP.reference_for("plant.v2.normal@XGA.<ctt")
    assert (ref.library, ref.element, ref.ext) == ("plant.v2", "normal@XGA", "ctt")


def test_a_page_summary_template_of_a_dotted_library_splits_at_the_last_dot():
    from ctgedit.format import pagesummary as PS

    t = PS.parse_template_ref("plant.v2.normal@HD768.<ctt")
    assert (t.library, t.element) == ("plant.v2", "normal@HD768")
    t = PS.parse_template_ref("sxw_style_1.normal@HD768")
    assert (t.library, t.element) == ("sxw_style_1", "normal@HD768")


def test_a_super_genie_attachment_of_a_dotted_library_splits_at_the_last_dot():
    from ctgedit.format import supergenie as SG

    got = SG.attachment_targets(
        [
            (b"", "plant.v2.!detail@SUPER#00000001"),
            (b"", "fxsuper.!fx_detail@SUPER.<ctm#00000002"),
            (b"", "\\label"),
        ]
    )
    assert got == [("plant.v2", "!detail@SUPER"), ("fxsuper", "!fx_detail@SUPER")]


# ------------------------------------------------------------- Update Pages' 'closed meanwhile'
def test_a_page_needs_an_update_only_while_it_quotes_an_old_stamp(fx_install):
    """A page saved before it was closed and current now is not named as left."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.io import genieusage as GU
    from ctgedit.io import updatepages

    lib = fx_install.file("FxLib", "fxgenie.ctm")
    # a page that places fx_value directly and is current for it
    page = next(
        os.path.abspath(u.ref.path)
        for u in GU.element_users(lib, "fx_value").users
        if u.ref.kind == "page" and not u.needs_update and not u.via
    )
    assert not updatepages.page_needs_update(page)
    d = ElementDocument.open_element(lib, "fx_value")
    t = next(i for i, o in enumerate(d.page.objects) if o.parent is None and d.objects[i].type_code == 0x05)
    d.set_text(t, "%Description% LEFTOVER")
    d.save()
    libraries_changed()
    assert updatepages.page_needs_update(page)  # (it quotes fx_value's old stamp)
    doc = EditableDocument.open(page)
    assert doc.apply_genie_changes(doc.genie_changes()) > 0
    doc.save()
    libraries_changed()
    assert not updatepages.page_needs_update(page)  # (saved current: not named)
    assert updatepages.page_needs_update(os.path.join(os.path.dirname(page), "nosuch.ctg"))


# ------------------------------------------------------------------ the save question's count
def test_the_save_question_counts_the_elements_by_their_kind():
    from ctgedit.ui.main_window import MainWindow

    def u(lib, el, ext):
        return types.SimpleNamespace(ref=types.SimpleNamespace(library=lib, element=el, ext=ext))

    users = [
        u("fxnest", "n1", "ctm"),
        u("fxnest", "n2", "ctm"),
        u("fx_style", "normal@XGA", "ctt"),
        u("fxsuper", "!fx_detail@SUPER", "ctm"),
    ]
    text = MainWindow._users_count_text(users, 1, "fxnest.n0")
    assert text == (
        "2 library genies, 1 super genie, 1 template and 1 closed page use "
        "fxnest.n0 and still show its old version."
    ), text
    assert (
        MainWindow._users_count_text([u("fx_style", "normal@XGA", "ctt")], 0, "fxsym.fx_on")
        == "1 template uses fxsym.fx_on and still shows its old version."
    )
    assert MainWindow._users_count_text(2, 0, "x") == "2 library genies use x and still show its old version."
