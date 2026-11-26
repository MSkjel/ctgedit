"""Which property pages each object kind gets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from ...format.cnum import c_atoi
from ...format import codes as _codes


@dataclass(frozen=True)
class Page:
    id: str  # builder key in the module's BUILDERS
    bit: int  # CtDraw page-mask bit (0 = the class's own page)
    tab: str  # outer tab (CtDraw's text)
    sub: str  # inner tab
    module: str  # ctgedit.ui.objprops.<module>
    keys: Tuple[str, ...] = ()  # a non-blank value marks it configured (the
    #                                   page's mode picks which: is_configured)
    prefixes: Tuple[str, ...] = ()  # ... or any key with one of these prefixes
    check: bool = True  # CtDraw draws a check mark


# CtDraw's pages, in sheet order
PAGES: Tuple[Page, ...] = (
    Page("linkage", 0x200000, "Appearance", "General", "linkage", check=False),
    Page("set_object", 0x100000, "Appearance", "General", "appearance"),
    Page("effects", 0x1, "Appearance", "3D Effects", "appearance", check=False),
    Page(
        "display_value",
        0x80000,
        "Appearance",
        "Display Value",
        "display_value",
        keys=("TXT_A", "TXT_B", "TXT_C", "TXT_D", "TXT_E", "TXT_STR"),
    ),
    Page(
        "tag_association",
        0x400000,
        "Appearance",
        "Tag Association",
        "activex",
        prefixes=("ACTIVEX_",),
    ),
    Page("visibility", 0x2, "Appearance", "Visibility", "appearance", keys=("VISIBLE",)),
    Page("vertices", 0x2000000, "Appearance", "Vertices", "vertices", prefixes=("VERTEX_",)),
    Page("move_h", 0x4, "Movement", "Horizontal", "motion", keys=("MVX",)),
    Page("move_v", 0x8, "Movement", "Vertical", "motion", keys=("MVY",)),
    Page("move_rot", 0x10, "Movement", "Rotational", "motion", keys=("ROT",)),
    Page("scale_h", 0x20, "Scaling", "Horizontal", "motion", keys=("SZX",)),
    Page("scale_v", 0x40, "Scaling", "Vertical", "motion", keys=("SZY",)),
    Page(
        "fill_color",
        0x80,
        "Fill",
        "Colour",
        "fill",
        keys=("COL_A", "COL_B", "COL_C", "COL_D", "COL_E", "COL_ARR", "COL_EXP"),
    ),
    Page("fill_level", 0x100, "Fill", "Level", "fill", keys=("FIL",)),
    Page("touch", 0x200, "Input", "Touch", "input"),
    Page("keyboard", 0x400, "Input", "Keyboard Commands", "input"),
    Page("slider_h", 0x1000, "Slider", "Horizontal", "motion", keys=("MVXSL",)),
    Page("slider_v", 0x2000, "Slider", "Vertical", "motion", keys=("MVYSL",)),
    Page("slider_rot", 0x4000, "Slider", "Rotational", "motion", keys=("ROTSL",)),
    Page(
        "access",
        0x10000,
        "Access",
        "General",
        "access",
        keys=(
            "__AN_NAME__",
            "DESC",
            "COMMENT",
            "AREA",
            "PRIV",
            "DEVICE",
            "ACTIVEXEXT_*NAME",
            "ACTIVEXEXT_*EVENTCLASS",
        ),
    ),
    Page("disable", 0x8000, "Access", "Disable", "access", keys=("DISABLED", "DISMODE")),
    Page("identification", 0x800000, "Access", "Identification", "access"),
    Page(
        "metadata",
        0x1000000,
        "Metadata",
        "General",
        "metadata",
        prefixes=("METADATA_",),
    ),
    Page(
        "alarm_indicator",
        0x4000000,
        "Alarm Indicator",
        "General",
        "alarm_indicator",
        keys=("AI_ON",),
    ),
)
PAGE_BY_ID: Dict[str, Page] = {p.id: p for p in PAGES}

#: the pages a CONFIGURED page disables:
#: configured page -> the pages it disables
EXCLUDES: Dict[str, Tuple[str, ...]] = {
    "slider_h": ("move_h", "slider_rot"),  # 0x1000 -> 0x4, and 0x4004 -> 0x1000
    "slider_v": ("move_v", "slider_rot"),  # 0x2000 -> 0x8, and 0x4008 -> 0x2000
    "slider_rot": ("move_rot", "slider_h", "slider_v"),  # 0x4000 -> 0x10, 0x1000, 0x2000
    "move_h": ("slider_h",),
    "move_v": ("slider_v",),
    "move_rot": ("slider_rot",),
}

#: pages a CLASS always has disabled (CtDraw's Bitmap
#: always has 0x80, Fill > Color): type code -> {page: why}
CLASS_DISABLED: Dict[int, Dict[str, str]] = {
    0x07: {"fill_color": "A bitmap or placed symbol has no fill colour animation."},
}

#: the class's OWN first page: type code -> (page, module)
_GENERAL = ("Appearance", "General")
OWN_PAGES: Dict[int, Page] = {}
for _tc in (0x01, 0x02, 0x03, 0x04, 0x05, 0x0A, 0x0B, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x1001, 0x1006):
    OWN_PAGES[_tc] = Page("general", 0, "Appearance", "General", "appearance", check=False)
OWN_PAGES[0x13] = Page("general", 0, "Appearance", "General", "web", check=False)
OWN_PAGES[0x1003] = Page("general", 0, "Appearance", "General", "trend", check=False)
OWN_PAGES[0x1009] = Page("general", 0, "Cicode", "General", "cicode", keys=("CMD",))
OWN_PAGES[0x0D] = Page("general", 0, "Appearance", "General", "activex", check=False)
del _tc

#: type code -> CtDraw's page mask (object as the class sets it)
CLASS_MASKS: Dict[int, int] = {
    0x01: 0x8107FFFF,
    0x02: 0x8107FFFF,
    0x03: 0x8107FFFF,
    0x0E: 0x8107FFFF,
    0x0F: 0x8107FFFF,
    0x04: 0x8107FFFF,
    0x11: 0x8107FFFF,
    0x12: 0x8107FFFE,
    0x0B: 0x8107FFFE,
    0x07: 0x8107FFFE,
    0x05: 0x810FFFFF,
    0x0A: 0x8307FFFF,
    0x10: 0x8307FFFF,
    0x08: 0x8507FFFE,
    0x0C: 0x8117FF7E,
    0x0D: 0x01C1006E,
    0x13: 0x8901006E,
    0x1001: 0x81078E0F,
    0x1003: 0x81070C02,
    0x1006: 0x81070C02,
    0x1009: 0x80070C00,
}
LINKAGE = 0x200000
GENIE_KEEP, GENIE_ADD = 0x7AD80001, 0x05010000
ALARM_INDICATOR = 0x4000000

#: fields a class shows but greys out: key -> why (help 1172149: "The
#: Tooltip and Log Device fields are disabled for Web Content objects")
DISABLED_FIELDS: Dict[int, Dict[str, str]] = {
    0x13: {
        "COMMENT": "A Web Content object has no tool tip.",
        "DEVICE": "A Web Content object has no log device.",
    },
}

#: why a class has no sheet
CLASS_NAMES: Dict[int, str] = {
    0x06: "Sparkle",
    0x09: "Metafile",
    0x1000: "Animation Point",
    0x1002: "Bar Graph",
    0x1004: "Number (V3/V4)",
    0x1005: "String (V3/V4)",
    0x1007: "Symbol (V3/V4)",
    0x1008: "Color Flood",
    _codes.TC.KEYBOARD: "Keyboard Command",
    0x100D: "Animated Symbol",
    0x100E: "Size / Move",
    0x100F: "Slider (V3/V4)",
    _codes.TC.TOUCH: "Touch Command",
}


@dataclass
class SheetContext:
    """What the registry needs to know about the object a sheet edits."""

    doc: object
    index: int
    type_code: int
    dwflags: int
    link_ext: str = ""  # 'ctl' / 'ctm' / 'ctt' of the record's own link
    props: Dict[str, str] = field(default_factory=dict)
    read_only: str = ""  # why the sheet is read-only ('' = editable)

    @classmethod
    def for_object(cls, doc, index: int) -> "SheetContext":
        po = doc.parsed.page.objects[index]
        ext = ""
        if po.subtype_size & 0x1:
            try:
                from ...format.symbols import reference_from_body

                ref = reference_from_body(po.body_raw)
                ext = (ref.ext or "").lower() if ref is not None else ""
            except Exception:  # noqa: BLE001
                ext = ""
        try:
            props = dict(doc.object_properties(index))
        except Exception:  # noqa: BLE001
            props = {}
        return cls(doc, index, po.type_code, po.subtype_size, ext, props, read_only_reason(doc, index))

    # -- CtDraw's page mask --------------------------------------------------
    @property
    def mask(self) -> int:
        m = CLASS_MASKS.get(self.type_code, 0)
        if self.dwflags & 0x1:
            m |= LINKAGE  # a linked record
        # a nested genie instance (0x200) stores no link of its own, but CtDraw
        # re-composes it from its element on load, so in memory it IS linked to
        # a .ctm (its sheet has the pages of a placed genie)
        ext = self.link_ext or ("ctm" if self.type_code == 0x08 and self.dwflags & 0x200 else "")
        if self.type_code == 0x08 and ext:
            if ext == "ctm":
                m = (m & GENIE_KEEP) | GENIE_ADD
            elif ext == "ctl":
                m &= ~ALARM_INDICATOR
            elif ext == "ctt":
                m = LINKAGE
        return m

    @property
    def own_page(self) -> Optional[Page]:
        if self.type_code == 0x08:
            return None
        return OWN_PAGES.get(self.type_code)

    def has_sheet(self) -> bool:
        return self.own_page is not None or any(self.mask & p.bit for p in PAGES)


def pages_for(ctx: SheetContext) -> List[Page]:
    """The object's pages in CtDraw's order (own page first)."""
    out = []
    own = ctx.own_page
    if own is not None:
        out.append(own)
    m = ctx.mask
    out.extend(p for p in PAGES if m & p.bit)
    return out


def tabs_for(ctx: SheetContext) -> List[Tuple[str, List[Page]]]:
    """``pages_for`` grouped by outer tab, in order of first appearance."""
    order: List[str] = []
    groups: Dict[str, List[Page]] = {}
    for p in pages_for(ctx):
        if p.tab not in groups:
            order.append(p.tab)
            groups[p.tab] = []
        groups[p.tab].append(p)
    return [(t, groups[t]) for t in order]


def _blank(v) -> bool:
    return v is None or not str(v).strip()


def is_configured(page: Page, ctx: SheetContext, props: Optional[Dict[str, str]] = None) -> bool:
    """CtDraw's check mark for page. CtDraw's key lookup
    ignores ASCII case."""
    props = _kept_props(ctx, ctx.props if props is None else props)
    low = {k.lower(): v for k, v in props.items()}

    def val(key):
        return low.get(key.lower())

    if page.id in ("touch", "keyboard"):
        return _input_configured(page.id, ctx)
    if page.id in ("move_h", "move_v", "move_rot"):
        slider = {"move_h": "MVXSLMODE", "move_v": "MVYSLMODE", "move_rot": "ROTSLMODE"}
        if c_atoi(val(slider[page.id])) & 1:
            return False  # the slider owns the range
        return not _blank(val(page.keys[0]))
    if page.id in ("slider_h", "slider_v", "slider_rot"):
        mode = {"slider_h": "MVXSLMODE", "slider_v": "MVYSLMODE", "slider_rot": "ROTSLMODE"}[page.id]
        return bool(c_atoi(val(mode)) & 1) and not _blank(val(page.keys[0]))
    if page.id in ("fill_color", "display_value"):
        # the key depends on the page's mode (CtDraw loads it into per
        # case: COLMODE 0 COL_A, 1 COL_A..COL_E, 2 COL_ARR, 3/4 COL_EXP;
        # TXTMODE 0/2 TXT_A, 1 TXT_A..TXT_E, 3/4 TXT_STR; any other mode: none)
        fill = page.id == "fill_color"
        pre = "COL_" if fill else "TXT_"
        mode = c_atoi(val("COLMODE" if fill else "TXTMODE"))
        if mode == 1:  # multi-state: any of A..E
            return any(not _blank(val(pre + c)) for c in "ABCDE")
        key = {
            0: pre + "A",
            2: "COL_ARR" if fill else "TXT_A",
            3: "COL_EXP" if fill else "TXT_STR",
            4: "COL_EXP" if fill else "TXT_STR",
        }.get(mode)
        return key is not None and not _blank(val(key))
    if page.id == "set_object":
        # a symbol set's mode is its TXTMODE: 0 and 3 TXT_A, 1 TXT_A..TXT_E, 2 SET_ARR; other modes none
        mode = c_atoi(val("TXTMODE"))
        if mode == 1:
            return any(not _blank(val("TXT_" + c)) for c in "ABCDE")
        key = {0: "TXT_A", 2: "SET_ARR", 3: "TXT_A"}.get(mode)
        return key is not None and not _blank(val(key))
    if page.id == "disable":
        # 'Disable when' set, or DISMODE stored at all (a raw non-empty test)
        return not _blank(val("DISABLED")) or bool(val("DISMODE"))
    if page.id == "alarm_indicator":
        return c_atoi(val("AI_ON")) != 0
    if page.id == "access" and not _blank(val("NAME")):
        # the legacy object-name key counts as the name
        return True
    if page.id == "general" and page.module == "cicode":
        return not _blank(val("CMD"))
    if page.id in ("vertices", "tag_association"):
        # CtDraw only looks for a KEY with the prefix (strncmp over the key
        # list), whatever its value -- an art-stripped polygon's zeroed
        # VERTEX_ keys count
        return any(k.startswith(p) for k in props for p in page.prefixes)
    if any(not _blank(val(k)) for k in page.keys):
        return True
    return (
        any(not _blank(v) and any(k.upper().startswith(p) for p in page.prefixes) for k, v in props.items())
        if page.prefixes
        else False
    )


def _kept_props(ctx: SheetContext, props: Dict[str, str]) -> Dict[str, str]:
    """``props`` without the keys a placed genie ROOT loses when CtDraw loads
    the page (editing._genie_root_drops: only its name and alarm indicator
    stay)."""
    if ctx.type_code != 0x08 or not ctx.dwflags & 0x200 or ctx.doc is None:
        return props
    drops = getattr(ctx.doc, "_genie_root_drops", None)
    if drops is None:
        return props
    try:
        return {k: v for k, v in props.items() if not drops(ctx.index, k)}
    except Exception:  # noqa: BLE001 -- a report only
        return props


def _input_configured(which: str, ctx: SheetContext) -> bool:
    doc = ctx.doc
    try:
        if which == "keyboard":
            return bool(doc.keyboard_children(ctx.index))
        info = doc.input_commands(ctx.index)
        return bool(info.get("touch"))
    except Exception:  # noqa: BLE001 -- a report only
        return False


def configured_pages(ctx: SheetContext, props: Optional[Dict[str, str]] = None) -> set:
    return {p.id for p in pages_for(ctx) if p.check and is_configured(p, ctx, props)}


def disabled_pages(configured: Iterable[str], type_code: Optional[int] = None) -> Dict[str, str]:
    """page id -> the configured page that disables it (or, for a page the
    object's class always disables, ``CLASS_DISABLED``'s reason prefixed
    with '!')."""
    out: Dict[str, str] = {}
    for c in configured:
        for d in EXCLUDES.get(c, ()):
            out.setdefault(d, c)
    for d, why in CLASS_DISABLED.get(type_code, {}).items():
        out.setdefault(d, "!" + why)
    return out


def sheet_title(ctx: SheetContext, kind: str = "") -> str:
    """The object's name in its sheet's title."""
    if ctx.type_code == 0x08:
        if kind == "composite_genie":
            return "Composite Genie"
        linked = {"ctm": "Genie", "ctl": "Symbol", "ctt": "Template"}.get(ctx.link_ext)
        if linked:
            return linked
        return "Genie" if ctx.dwflags & 0x200 else "Group"
    named = _SHEET_TITLES.get(ctx.type_code)
    if named:
        return named
    return (kind or "object").replace("_", " ").title()


#: classes whose reader kind is no name for the sheet's caption
_SHEET_TITLES = {0x13: "Web Content", 0x1009: "Cicode Object", 0x0D: "ActiveX"}


def page_title(page_id: str) -> str:
    p = PAGE_BY_ID.get(page_id)
    return "%s > %s" % (p.tab, p.sub) if p else page_id


# -- read-only sheets ---------------------------------------------------------
def placed_genies_of(doc, index: int) -> List[int]:
    """The placed genies that contain ``index``, nearest first."""
    try:
        objs = doc.parsed.page.objects
        anc = list(doc.page.ancestors(index))
    except Exception:  # noqa: BLE001
        return []
    return [a for a in anc if a < len(objs) and (objs[a].subtype_size & 0x200)]


def placed_genie_of(doc, index: int) -> Optional[int]:
    """The nearest placed genie that contains ``index`` (see
    :func:`placed_genies_of`), or None."""
    genies = placed_genies_of(doc, index)
    return genies[0] if genies else None


def genie_member_edits(doc, indices) -> set:
    """The indices that are genie members edited without their genie."""
    chosen = set(indices)
    out = set()
    for i in chosen:
        genies = placed_genies_of(doc, i)
        if genies and genies[-1] not in chosen:
            out.add(i)
    return out


def genie_member_reason(doc, index: int) -> str:
    """Why a placed genie's member cannot be edited ('' = it is no member)."""
    genies = placed_genies_of(doc, index)
    if not genies:
        return ""
    objs = doc.parsed.page.objects
    name = next((n for n in (_genie_name(objs[g]) for g in genies) if n), "")
    if "_CompositionCaches" in name:
        # a composite genie: named by its template, not by its cache path
        import os
        from ..names import composite_template_name

        pdir = os.path.dirname(os.path.abspath(doc.path)) if getattr(doc, "path", "") else ""
        tn = composite_template_name(name, pdir)
        return (
            "This object is part of the composite genie %s. Edit the genie (Genie "
            "Parameters, or its template) to change it." % ("'%s'" % tn if tn else "on this page")
        )
    return "This object is part of the genie %s. Edit the genie definition to change it." % (
        name or "on this page"
    )


def read_only_reason(doc, index: int, template_locked: bool = True) -> str:
    """Why the sheet is read-only ('' if it isn't)."""
    try:
        why = genie_member_reason(doc, index)
    except Exception:  # noqa: BLE001
        why = ""
    if why:
        return why
    try:
        obj = doc.page.objects[index]
        if template_locked and obj.raw.get("_from_template"):
            return "This object belongs to the page template. Edit the template to change it."
    except Exception:  # noqa: BLE001
        pass
    return ""


def member_editable_keys(doc, index: int) -> frozenset:
    """The keys a read-only sheet still edits."""
    try:
        objs = doc.parsed.page.objects
        anc = list(doc.page.ancestors(index))
        if doc.page.objects[index].raw.get("_from_template"):
            return frozenset()
    except Exception:  # noqa: BLE001
        return frozenset()
    if not any(a < len(objs) and objs[a].subtype_size & 0x200 for a in anc):
        return frozenset()
    from ...editing import USER_MARKED_KEYS

    return frozenset(USER_MARKED_KEYS)


def _genie_name(po) -> str:
    try:
        from ...format.symbols import reference_from_body

        ref = reference_from_body(po.body_raw) if po.subtype_size & 0x1 else None
    except Exception:  # noqa: BLE001
        ref = None
    if ref is None:
        return ""
    return "'%s.%s'" % (ref.library, ref.element)
