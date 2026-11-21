"""Does a nested genie copy differ in content from another copy of it?"""

from __future__ import annotations

import struct
from typing import List, Optional, Sequence, Tuple

from .codes import TC
from .page import PageObject

#: the largest pixel difference that is rounding (integer boxes, transform offsets)
PIXEL_TOLERANCE = 1
#: the relative difference of a transform's linear part that is float noise
LINEAR_TOLERANCE = 1e-9
#: a bag key that holds the property sheet's own state, never the genie's content
SHEET_KEY_PREFIX = "!*"
#: the records whose box is the union of their members' (genie_norm._refit), not their own
UNION_TCS = frozenset({TC.GROUP, TC.SYMBOL_SET})
#: the bag key (lower case) that holds a named record's name again: derived from the name
NAME_KEY = "__an_name__"
#: the records whose Bounds (rect2) are their text measured in their named font, derived
#: again on every load (legacy.value_extent): a legacy Number and String
MEASURED_BOUNDS_TCS = frozenset({0x1004, 0x1005})


def copy_spans(records: Sequence[PageObject]) -> List[Tuple[int, int]]:
    """(start, end) of each nested genie copy in an element's records."""
    from .template_place import is_template_copy_root

    out = []
    k, n = 0, len(records)
    while k < n:
        po = records[k]
        if not (po.class_flag and ((po.subtype_size & 0x201) == 0x201 or is_template_copy_root(po))):
            k += 1
            continue
        end, pending = k, 1
        while pending and end < n:
            pending -= 1
            pending += records[end].class_flag
            end += 1
        out.append((k, end))
        k = end
    return out


def copy_key(po: PageObject) -> Optional[Tuple[str, str]]:
    """(library, element), lower case, of the genie a copy's root names; None when its
    link cannot be read."""
    from .symbols import reference_from_object

    try:
        ref = reference_from_object(po)
    except Exception:  # noqa: BLE001  # swallow-ok: an unreadable link names nothing
        return None
    if ref is None:
        return None
    return ref.library.lower(), ref.element.lower()


def _bag_content(bag, name: str = "") -> dict:
    """A bag's pairs by lower-case key, without the sheet's '!*' keys and without an
    '__AN_NAME__' that holds the record's ``name`` (its header name: the save derives it)."""
    out = {k.lower(): v for k, v in bag.pairs if not k.startswith(SHEET_KEY_PREFIX)}
    if name and out.get(NAME_KEY) == name:
        del out[NAME_KEY]
    return out


def _near(a, b, tol) -> bool:
    return (
        a is None
        and b is None
        or (
            a is not None
            and b is not None
            and len(a) == len(b)
            and all(abs(x - y) <= tol for x, y in zip(a, b))
        )
    )


def _slots_same(sa, sb) -> bool:
    if len(sa) != len(sb):
        return False
    for (ma, va), (mb, vb) in zip(sa, sb):
        if ma != mb:
            return False
        va, vb = tuple(va or ()), tuple(vb or ())
        if len(va) != len(vb):
            return False
        for i, (x, y) in enumerate(zip(va, vb)):
            if i >= 4:
                if abs(x - y) > PIXEL_TOLERANCE:  # the offset
                    return False
            elif abs(x - y) > LINEAR_TOLERANCE * max(1.0, abs(x), abs(y)):
                return False
    return True


def _own_matrix(r) -> bool:
    """The record's own transform (slot 2: its twin's in the element) has a linear part: a
    mirror, a turn or a stretch (a text that keeps a matrix)."""
    try:
        m, (a, b, c, d, _h, _k) = r.slots[1]
    except IndexError, TypeError, ValueError:
        return False
    return bool(m) and any(abs(v - w) > LINEAR_TOLERANCE for v, w in zip((a, b, c, d), (1.0, 0.0, 0.0, 1.0)))


def same_content(a: PageObject, b: PageObject) -> bool:
    """Records ``a`` and ``b`` hold the same composed content (the module's rules)."""
    if a.class_flag != b.class_flag or a.type_code != b.type_code:
        return False
    if a.raw_payload is not None or b.raw_payload is not None:
        return a.raw_payload == b.raw_payload and a.body_raw == b.body_raw
    if a.body_raw == b.body_raw:
        return True
    from .genie_place import BUTTON_TC, TEXT_TC, decode

    try:
        ra, rb = decode(a), decode(b)
    except Exception:  # noqa: BLE001  # swallow-ok: undecodable records compare as bytes
        return False
    if ra.tc != TC.DUMMY and ra.tc not in UNION_TCS:
        # (a group's box is its members' union, refit on every save: its placement is its
        # slots, and its members' boxes are compared. Not every record
        # with children: a rectangle's touch command is its child, its box its own)
        if not _near(ra.anchor, rb.anchor, PIXEL_TOLERANCE):
            return False
        if (ra.tc != TEXT_TC or _own_matrix(ra)) and not (
            _near(ra.rect1, rb.rect1, PIXEL_TOLERANCE)
            and (ra.tc in MEASURED_BOUNDS_TCS or _near(ra.rect2, rb.rect2, PIXEL_TOLERANCE))
        ):
            # (a legacy Number's or String's rect2 is measured again by every load and
            # save in this machine's fonts: MEASURED_BOUNDS_TCS)
            # (a text that keeps a matrix in its genie or template keeps its box through
            # every load and save, which measure a text again only when its slots have no
            # linear part (genie_place.text_extent_now, stripped_text_extent), so a box
            # beside the composition's is no DPI rounding)
            return False
    if ra.tc != TC.DUMMY and not _slots_same(ra.slots, rb.slots):
        # (a DummyMarker's slots: any save derives them again from the current definition)
        return False
    ea, eb = ra.entries, rb.entries
    if (ea is None) != (eb is None) or (ea is not None and len(ea) != len(eb)):
        return False
    button = ra.tc == BUTTON_TC
    for la, lb in zip(ea or (), eb or ()):
        if len(la) != len(lb):
            return False
        if not all(_bags_same(x, y, button, ra.name, rb.name) for x, y in zip(la, lb)):
            return False
    cls_a, cls_b = ra.cls, rb.cls
    if button and ra.dw & 2 and len(cls_a) >= 8 and len(cls_b) >= 8:
        # (an own-appearance button's class section ends in its box: the rounding rule)
        if not _near(struct.unpack("<4h", cls_a[-8:]), struct.unpack("<4h", cls_b[-8:]), PIXEL_TOLERANCE):
            return False
        cls_a, cls_b = cls_a[:-8], cls_b[:-8]
    return cls_a == cls_b and all(
        getattr(ra, f) == getattr(rb, f)
        for f in ("dw", "an", "res", "handle", "name", "link", "s40", "s80", "cs", "style")
    )


def _bags_same(x, y, button: bool, name_x: str = "", name_y: str = "") -> bool:
    """Two bags hold the same content (:func:`_bag_content`, with their records' names);
    a button's WIDTH and HEIGHT are its box (genie_place._button_box_size), so they follow
    the rounding rule."""
    dx, dy = _bag_content(x, name_x), _bag_content(y, name_y)
    if dx == dy:
        return True
    if not button or set(dx) != set(dy):
        return False
    for k in dx:
        if dx[k] == dy[k]:
            continue
        if k not in ("width", "height"):
            return False
        try:
            if abs(int(dx[k]) - int(dy[k])) > PIXEL_TOLERANCE:
                return False
        except ValueError:
            return False
    return True


def changed_copies(
    before: Sequence[PageObject], after: Sequence[PageObject]
) -> List[Optional[Tuple[str, str]]]:
    """(library, element) of genies whose nested copies differ between two forms of an element."""
    sa, sb = copy_spans(before), copy_spans(after)
    out: List[Optional[Tuple[str, str]]] = []

    def add(key):
        if key not in out:
            out.append(key)

    if len(sa) != len(sb):
        for k, _e in sa:
            add(copy_key(before[k]))
        for k, _e in sb:
            add(copy_key(after[k]))
        return out
    for (k, e), (k2, e2) in zip(sa, sb):
        key = copy_key(before[k])
        if (
            key != copy_key(after[k2])
            or e - k != e2 - k2
            or not all(same_content(before[k + j], after[k2 + j]) for j in range(e - k))
        ):
            add(key if key is not None else copy_key(after[k2]))
    return out
