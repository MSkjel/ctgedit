"""A page's area code comes from the link table."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ctgedit.format.page import parse_ctg_bytes  # noqa: E402

PAGE = "FxCicode"  # (an FxMain page without a link table)


def test_a_page_without_a_link_table_has_the_default_area_code(fx_install):
    from ctgedit.format import pagesummary

    path = fx_install.page(PAGE)
    pb = parse_ctg_bytes(open(path, "rb").read()).page
    assert pb.link_count == 0 and pb.objects
    assert pb.area_code == "    "
    s = pagesummary.read_page_summary(path)
    assert s is not None
    assert s.area_code == ""


def test_the_derive_check_holds_when_the_first_record_changes(fx_install):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page(PAGE))
    doc.derive_check = True  # (the incremental derive equals a full one)
    first = len(doc.parsed.page.objects[0].body_raw)
    doc.delete_object(0)  # (the next record is the first one now)
    assert len(doc.parsed.page.objects[0].body_raw) != first
    doc.create_object("text", rect=(20, 20, 120, 40), text="@(Fx Area Label)")
    doc.save()  # (the save lists the text's label: a new table)
    pb = parse_ctg_bytes(open(doc.path, "rb").read()).page
    assert pb.link_count >= 1 and pb.link_table_raw[:4] == b"    "
