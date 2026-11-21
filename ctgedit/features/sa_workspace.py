"""Situational Awareness workspaces: a page's panes and the checks the runtime would only log."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

#: the runtime's AN name of a pane (Workspace.ci: sANName = "Pane")
PANE_AN_NAME = "pane"
#: the genie that places a pane
PANE_ELEMENT = ("sa_workspace", "pane")
#: at most this many panes a window (Workspace.ci g_nMaxPanes)
MAX_PANES = 50

#: metadata name -> the Pane Properties label (sa_workspace.frm)
FIELD_LABELS = {
    "name": "Name",
    "defaultpage": "Default Page",
    "displaymode": "Display Mode",
    "isdefault": "Is Default Pane?",
    "hasscrollbars": "Display Scrollbars",
    "fillmode": "Fill Mode",
    "contenttypes": "Content Types",
    "excludedautofillpanes": "Excluded Autofill Panes",
    "tabpane": "Tab Header Pane Name",
    "tabcontrolname": "Tab Control Name",
    "equiprefassocs": "Categories",
}

#: FillMode / DisplayMode label names -> value (SA_Include labels.dbf)
FILL_MODES = {"static": 0, "autofill": 1, "autofillcontextmustmatch": 3, "staticcontextmustmatch": 4}
FILL_MODE_TEXT = {
    0: "Static",
    1: "Autofill",
    3: "Autofill, context must match",
    4: "Static, context must match",
}
DISPLAY_MODES = {"stretch": 1, "maintainaspectratio": 2, "maintainsize": 3}
DISPLAY_MODE_TEXT = {1: "Stretch", 2: "Maintain aspect ratio", 3: "Maintain size"}

#: the page events CtDraw's Page Properties > Events shows (pages.DBF)
EVENT_FIELDS = (
    ("ENTRY_FUNC", "On page entry"),
    ("EXIT_FUNC", "On page exit"),
    ("BGND_FUNC", "While page shown"),
    ("READY_FUNC", "On page shown"),
    ("ACT_FUNC", "On activate"),
    ("DEACT_FUNC", "On deactivate"),
)
_INIT_CALL = re.compile(r"\bWorkspace_Init\s*\(", re.I)
_RES_SUFFIX = re.compile(r"_(HD1080|UHD4K)$", re.I)

ERROR, WARNING, INFO = "error", "warning", "info"
_LEVEL_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


def _int(text: str) -> Optional[int]:
    """Cicode StrToInt of ``text`` (None = not an integer)."""
    t = (text or "").strip()
    if re.fullmatch(r"[+-]?\d+", t):
        return int(t)
    return None


def str_to_bool(text: str) -> Tuple[bool, bool]:
    """(value, understood) of SA's StrToBool (Include String.ci): 'TRUE' ->
    True, 'FALSE' / '' -> False, an integer -> non-zero; anything else is
    False and not understood."""
    t = (text or "").strip()
    if t.upper() == "TRUE":
        return True, True
    if t.upper() == "FALSE" or not t:
        return False, True
    n = _int(t)
    if n is None:
        return False, False
    return n != 0, True


def _mode(text: str, names: Dict[str, int], prefix: str) -> Optional[int]:
    """_Workspace_StrToFillMode / _StrToDisplayMode: an integer, or a label
    name with or without its prefix (case-insensitive); None = neither (the
    runtime then uses its default)."""
    n = _int(text)
    if n is not None:
        return n
    t = (text or "").strip().lower()
    if t.startswith(prefix):
        t = t[len(prefix) :]
    return names.get(t)


@dataclass
class Pane:
    """One pane of a page: ``index`` = the record the runtime reads (named
    'Pane'), ``root`` = the top-level unit that places it (what the editor
    selects), ``rect`` = its (left, top, right, bottom), ``values`` = its
    metadata (lower-case name -> value)."""

    index: int
    root: int
    rect: Tuple[int, int, int, int]
    values: Dict[str, str] = field(default_factory=dict)
    an: int = -1

    def get(self, key: str) -> str:
        return (self.values.get(key.lower()) or "").strip()

    @property
    def name(self) -> str:
        return self.get("Name")

    @property
    def default_page(self) -> str:
        return self.get("DefaultPage")

    @property
    def is_default(self) -> bool:
        return str_to_bool(self.get("IsDefault"))[0]

    @property
    def has_scrollbars(self) -> bool:
        return str_to_bool(self.get("HasScrollbars"))[0]

    @property
    def fill_mode(self) -> Optional[int]:
        return _mode(self.get("FillMode"), FILL_MODES, "pane_fillmode_")

    @property
    def display_mode(self) -> Optional[int]:
        return _mode(self.get("DisplayMode"), DISPLAY_MODES, "pane_displaymode_")

    @property
    def content_types(self) -> List[str]:
        return [p.strip() for p in self.get("ContentTypes").split(",") if p.strip()]

    @property
    def tab_pane(self) -> str:
        return self.get("TabPane")

    @property
    def tab_control(self) -> str:
        return self.get("TabControlName")

    def fill_mode_text(self) -> str:
        """The fill mode as the runtime understands it ('Static' when it does
        not)."""
        m = self.fill_mode
        return FILL_MODE_TEXT.get(m if m is not None else 0, "Fill mode %s" % m)

    def label(self) -> str:
        """How the pane is named in messages."""
        return "Pane '%s'" % self.name if self.name else "The pane without a name at (%d, %d)" % self.rect[:2]


def metadata_values(props) -> Dict[str, str]:
    """lower metadata name -> value of a record's properties: every
    'METADATA_<n>' = 'name=value' pair in stored order (the first of a name
    wins), split at the first '=' after the name."""
    out: Dict[str, str] = {}
    items = props.items() if hasattr(props, "items") else props
    for k, v in items:
        if len(k) > 9 and k.upper().startswith("METADATA_"):
            name, _eq, value = (v or "").lstrip("=").partition("=")
            name = name.strip().lower()
            if name:
                out.setdefault(name, value)
    return out


def is_pane_name(name: str) -> bool:
    return (name or "").strip().lower() == PANE_AN_NAME


def _model_name(obj) -> str:
    props = obj.raw.get("_props") or {}
    for k in ("__AN_NAME__", "NAME"):
        v = props.get(k)
        if v:
            return v
    return obj.raw.get("_name") or ""


def model_panes(page) -> List[Pane]:
    """The panes of a derived page (model.document.Page): every object whose
    name is 'Pane', in page order. Cheap (one pass over the objects), so the
    canvas overlay recomputes it after every edit."""
    out: List[Pane] = []
    if page is None:
        return out
    objs = page.objects
    for i, obj in enumerate(objs):
        raw = obj.raw
        # the header name (CtDraw writes the name there AND as __AN_NAME__ /
        # the legacy NAME), else the bag name
        hdr = raw.get("_name")
        if hdr:
            if hdr.strip().lower() != PANE_AN_NAME:
                continue
        elif not raw.get("_props") or not is_pane_name(_model_name(obj)):
            continue
        g = obj.geometry
        try:
            root = page.root_of(i)
        except Exception:  # noqa: BLE001 -- a broken forest: the record itself
            root = i
        out.append(
            Pane(
                i,
                root,
                (g.x, g.y, g.x + g.extent_x, g.y + g.extent_y),
                metadata_values(raw.get("_props") or {}),
                getattr(obj, "an", -1),
            )
        )
    return out


def locate_pane(page, pane: Pane) -> Optional[Pane]:
    """``pane`` (from an earlier check) on ``page`` as it is NOW -- records
    shift when objects are added or deleted: the same record while it still
    is a pane of that name, else the pane of that name (the one at the same
    place when several have it); None when it is no longer there."""
    panes = model_panes(page)
    name = pane.name.lower()
    for p in panes:
        if p.index == pane.index and p.name.lower() == name:
            return p
    same = [p for p in panes if p.name.lower() == name]
    at = [p for p in same if p.rect == pane.rect]
    if at:
        return at[0]
    return same[0] if len(same) == 1 else None


def local_problems(panes: Iterable[Pane]) -> Dict[int, str]:
    """pane record index -> the first problem the page ALONE shows (no name,
    a name used twice, a second default pane): what the canvas overlay marks."""
    out: Dict[int, str] = {}
    seen: Dict[str, Pane] = {}
    default: Optional[Pane] = None
    for p in panes:
        if not p.name:
            out[p.index] = "no name"
            continue
        key = p.name.lower()
        if key in seen:
            out[p.index] = "name used twice"
        else:
            seen[key] = p
        if p.is_default:
            if default is None:
                default = p
            else:
                out.setdefault(p.index, "second default pane")
    return out


# ------------------------------------------------------------------ findings


@dataclass
class Finding:
    """One problem: ``level`` error | warning | info, ``message`` (what is
    wrong, what the runtime does, what to do), the pane it is about (None =
    the page) and the field (a Pane Properties label or a page property)."""

    level: str
    message: str
    pane: Optional[Pane] = None
    field: str = ""
    code: str = ""

    @property
    def index(self) -> Optional[int]:
        """The unit to select for this finding (None = a page finding)."""
        return self.pane.root if self.pane is not None else None


@dataclass
class Report:
    """The workspace checks of one page: its panes and what was found."""

    page: str
    panes: List[Pane] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    master: bool = False

    def count(self, level: str) -> int:
        return sum(1 for f in self.findings if f.level == level)

    def summary(self) -> str:
        from .page_checks import count_summary

        return count_summary(self.count)


@dataclass
class PageContext:
    """What the workspace checks need to know about the page besides its panes."""

    page_name: str
    record: Optional[dict] = None
    environment: Dict[str, str] = field(default_factory=dict)
    resolution: str = ""
    page_exists: Callable[[str], Optional[bool]] = lambda _n: None
    content_types: Optional[set] = None
    object_names: Callable[[str], Optional[set]] = lambda _n: None


def _env(env: Dict[str, str], key: str) -> Optional[str]:
    low = key.lower()
    for k, v in env.items():
        if k.lower() == low:
            return v
    return None


def check_panes(panes: List[Pane], ctx: PageContext) -> Report:
    """Every check of the module docstring over ``panes`` of one page. The
    page's own findings come first, then each pane's in page order (errors,
    then warnings, then notes)."""
    rep = Report(ctx.page_name, list(panes))
    if not panes:
        return rep
    add = rep.findings.append
    rec = ctx.record
    # ---- the page: a workspace master page, or one that hosts nested panes
    if rec is None:
        add(
            Finding(
                INFO,
                "The page is not registered in a project yet, so its events and "
                "content type are not checked. Save it in a project to check them.",
                code="page-unregistered",
            )
        )
    else:
        ready = rec.get("READY_FUNC", "") or ""
        con_type = (rec.get("CON_TYPE", "") or "").strip()
        init_ready = bool(_INIT_CALL.search(ready))
        init_other = [
            label
            for key, label in EVENT_FIELDS
            if key != "READY_FUNC" and _INIT_CALL.search(rec.get(key, "") or "")
        ]
        master = init_ready or bool(init_other) or con_type.lower() == "master"
        rep.master = master
        if master:
            if not init_ready and init_other:
                add(
                    Finding(
                        WARNING,
                        "Workspace_Init() is called in the %s event. Call it in "
                        "On page shown instead (Page Properties > Events)." % init_other[0],
                        field="On page shown",
                        code="init-wrong-event",
                    )
                )
            elif not init_ready:
                add(
                    Finding(
                        ERROR,
                        "The page's content type is Master, but On page shown does "
                        "not call Workspace_Init(), so its panes are never created. Enter "
                        "Workspace_Init() in Page Properties > Events > On page shown.",
                        field="On page shown",
                        code="init-missing",
                    )
                )
            if con_type.lower() != "master":
                add(
                    Finding(
                        WARNING,
                        "The page calls Workspace_Init(), but its content type is "
                        "%s. Set it to Master (Page Properties > General > Content type)."
                        % ("'%s'" % con_type if con_type else "not set"),
                        field="Content type",
                        code="contype-not-master",
                    )
                )
        else:
            has = _env(ctx.environment, "HasPanes")
            if has is None:
                add(
                    Finding(
                        ERROR,
                        "The page has panes, but it does not call Workspace_Init() "
                        "in On page shown and has no HasPanes property, so its panes are "
                        "never created. Add HasPanes with the value 1 in Page Properties "
                        "> Environment. A master page calls Workspace_Init() in On page "
                        "shown instead.",
                        field="HasPanes",
                        code="haspanes-missing",
                    )
                )
            elif has != "1":  # (the runtime compares the exact string)
                add(
                    Finding(
                        ERROR,
                        "HasPanes is '%s'. The panes are only created when it is 1 "
                        "(Page Properties > Environment)." % has,
                        field="HasPanes",
                        code="haspanes-value",
                    )
                )
    # ---- names, the default pane
    by_name: Dict[str, List[Pane]] = {}
    for p in panes:
        if p.name:
            by_name.setdefault(p.name.lower(), []).append(p)
    # (the runtime counts the panes it CREATED: one per distinct name)
    if len(by_name) > MAX_PANES:
        add(
            Finding(
                ERROR,
                "The page has %d panes. At most %d are created; the others stay "
                "empty." % (len(by_name), MAX_PANES),
                code="too-many",
            )
        )
    defaults = [p for p in panes if p.is_default and p.name]
    for p in panes:
        if not p.name:
            add(
                Finding(
                    ERROR,
                    "%s has no Name, so the runtime skips it. Double-click the "
                    "pane and enter a Name." % p.label(),
                    p,
                    "Name",
                    "no-name",
                )
            )
            continue
        same = by_name.get(p.name.lower(), [])
        if len(same) > 1 and same[0] is not p:
            add(
                Finding(
                    ERROR,
                    "%s has the same Name as another pane on this page ('%s', "
                    "compared without case), so it is not created. Give each pane its "
                    "own Name." % (p.label(), p.name),
                    p,
                    "Name",
                    "duplicate-name",
                )
            )
    if len(defaults) > 1:
        first = defaults[0]
        for p in defaults[1:]:
            add(
                Finding(
                    WARNING,
                    "%s and pane '%s' are both the default pane (Is Default "
                    "Pane? = TRUE). Both get the default content, and the header bar "
                    "can follow either. Set Is Default Pane? to FALSE on all but one."
                    % (p.label(), first.name),
                    p,
                    "Is Default Pane?",
                    "two-defaults",
                )
            )
    if rep.master and not defaults:
        add(
            Finding(
                WARNING,
                "No pane is the default pane. The header bar takes its title "
                "from the default pane, and pages without a content type open there. "
                "Set Is Default Pane? to TRUE on the main content pane.",
                code="no-default",
            )
        )
    # ---- each pane's values
    own = (ctx.page_name or "").lower()
    by_lower = {p.name.lower(): p for p in panes if p.name}
    for p in panes:
        for key, label in (("IsDefault", "Is Default Pane?"), ("HasScrollbars", "Display Scrollbars")):
            raw = p.get(key)
            if raw and not str_to_bool(raw)[1]:
                add(
                    Finding(
                        WARNING,
                        "%s: %s is '%s'. Use TRUE, FALSE or a number; anything "
                        "else counts as FALSE." % (p.label(), label, raw),
                        p,
                        label,
                        "bool-value",
                    )
                )
        fm_raw = p.get("FillMode")
        fm = p.fill_mode
        if fm_raw and (fm is None or fm not in FILL_MODE_TEXT):
            add(
                Finding(
                    WARNING,
                    "%s: Fill Mode '%s' is not known, so the pane is Static. Use "
                    "Static, Autofill, AutofillContextMustMatch or StaticContextMustMatch "
                    "(or 0, 1, 3, 4)." % (p.label(), fm_raw),
                    p,
                    "Fill Mode",
                    "fill-mode",
                )
            )
        dm_raw = p.get("DisplayMode")
        dm = p.display_mode
        if dm_raw and (dm is None or dm not in DISPLAY_MODE_TEXT):
            add(
                Finding(
                    WARNING,
                    "%s: Display Mode '%s' is not known, so the pane stretches "
                    "its page. Use Stretch, MaintainAspectRatio or MaintainSize (or 1, 2, "
                    "3)." % (p.label(), dm_raw),
                    p,
                    "Display Mode",
                    "display-mode",
                )
            )
        if p.has_scrollbars and (dm if dm in DISPLAY_MODE_TEXT else 1) != 3:
            add(
                Finding(
                    INFO,
                    "%s: Display Scrollbars is TRUE, but scroll bars only appear "
                    "with Display Mode MaintainSize (3)." % p.label(),
                    p,
                    "Display Scrollbars",
                    "scrollbars",
                )
            )
        page = p.default_page
        if page and page.lower() != "none":
            if page.lower() == own:
                add(
                    Finding(
                        ERROR,
                        "%s has this page as its Default Page. The pane shows an "
                        "error page at runtime." % p.label(),
                        p,
                        "Default Page",
                        "default-page-loop",
                    )
                )
            else:
                exists = ctx.page_exists(page)
                if exists is False:
                    add(
                        Finding(
                            ERROR,
                            "%s: Default Page '%s' is not a page of this project or "
                            "the projects compiled with it, so the pane is not created." % (p.label(), page),
                            p,
                            "Default Page",
                            "default-page-missing",
                        )
                    )
                m = _RES_SUFFIX.search(page)
                if m and ctx.resolution and m.group(1).upper() != ctx.resolution.upper():
                    add(
                        Finding(
                            WARNING,
                            "%s: Default Page '%s' is a %s page, but this page is "
                            "%s. Use the %s version of the page."
                            % (
                                p.label(),
                                page,
                                m.group(1).upper(),
                                ctx.resolution.upper(),
                                ctx.resolution.upper(),
                            ),
                            p,
                            "Default Page",
                            "default-page-resolution",
                        )
                    )
        if ctx.content_types is not None:
            unknown = [c for c in p.content_types if c.lower() not in ctx.content_types]
            if unknown:
                add(
                    Finding(
                        WARNING,
                        "%s: Content Types %s %s not defined, so no page can "
                        "have %s (Plant SCADA Studio > Visualization > Content Types)."
                        % (
                            p.label(),
                            ", ".join("'%s'" % c for c in unknown),
                            "is" if len(unknown) == 1 else "are",
                            "it" if len(unknown) == 1 else "them",
                        ),
                        p,
                        "Content Types",
                        "content-type",
                    )
                )
        if fm in (1, 3) and not p.content_types and not p.is_default:
            add(
                Finding(
                    INFO,
                    "%s is an Autofill pane without Content Types, so it takes "
                    "content pages of every type. Enter the content types it shows (for "
                    "example FP for a faceplate pane)." % p.label(),
                    p,
                    "Content Types",
                    "autofill-no-types",
                )
            )
        # ---- tabbed panes
        tab, ctl = p.tab_pane, p.tab_control
        if tab:
            header = by_lower.get(tab.lower())
            if header is p:
                add(
                    Finding(
                        ERROR,
                        "%s names itself as its Tab Header Pane Name. Enter the "
                        "name of the pane that shows the tab bar." % p.label(),
                        p,
                        "Tab Header Pane Name",
                        "tab-self",
                    )
                )
            elif header is None:
                add(
                    Finding(
                        ERROR,
                        "%s: Tab Header Pane Name '%s' is not a pane on this page "
                        "(pane names: %s), so its tabs are never linked."
                        % (
                            p.label(),
                            tab,
                            ", ".join(sorted(q.name for q in panes if q.name and q is not p)) or "none",
                        ),
                        p,
                        "Tab Header Pane Name",
                        "tab-pane-missing",
                    )
                )
            elif not ctl:
                add(
                    Finding(
                        WARNING,
                        "%s has a Tab Header Pane Name but no Tab Control Name. "
                        "Enter the name of the tab bar genie on the page of pane '%s'."
                        % (p.label(), header.name),
                        p,
                        "Tab Control Name",
                        "tab-control-empty",
                    )
                )
            else:
                hp = header.default_page
                names = ctx.object_names(hp) if hp and hp.lower() != "none" else None
                if names is not None and ctl.lower() not in names:
                    add(
                        Finding(
                            WARNING,
                            "%s: Tab Control Name '%s' is not an object on "
                            "'%s', the default page of header pane '%s', so the tabs are "
                            "not linked. Enter the name of the tab bar genie (Access > "
                            "General > Name)." % (p.label(), ctl, hp, header.name),
                            p,
                            "Tab Control Name",
                            "tab-control-missing",
                        )
                    )
        elif ctl:
            add(
                Finding(
                    INFO,
                    "%s has a Tab Control Name but no Tab Header Pane Name, so the "
                    "tab control is not used." % p.label(),
                    p,
                    "Tab Control Name",
                    "tab-control-unused",
                )
            )
    rep.findings.sort(
        key=lambda f: (f.pane.index if f.pane is not None else -1, _LEVEL_ORDER.get(f.level, 3))
    )
    return rep


# ----------------------------------------------------------------- the page


def page_resolution(page_name: str, template_element: str = "", width: int = 0) -> str:
    """'HD1080' / 'UHD4K' / '' of a page: its situational_awareness template
    ('master@-HD1080', 'pagecontent@-4K'), else its name suffix, else its
    width (the runtime itself picks the BlankPage by PageInfo(21) = 1920)."""
    t = (template_element or "").upper()
    if "@" in t:
        res = t.rsplit("@", 1)[1].lstrip("-")
        if res in ("HD1080",):
            return "HD1080"
        if res in ("4K", "UHD4K"):
            return "UHD4K"
    m = _RES_SUFFIX.search(page_name or "")
    if m:
        return m.group(1).upper()
    if width == 1920:
        return "HD1080"
    if width == 3840:
        return "UHD4K"
    return ""


_NAMES_CACHE: Dict[str, tuple] = {}  # path -> (version, qualified names, top-level)


def page_object_names(path: str) -> Optional[set]:
    """The lower-case names of every named object of the page file ``path``,
    each also with its named containers ('group.name', as DspGetAnFromName
    looks them up); None when the file cannot be read. Cached per file
    version."""
    names = page_object_name_list(path)
    return None if names is None else {n.lower() for n in names}


def page_object_name_list(path: str) -> Optional[List[str]]:
    """:func:`page_object_names` in their stored spelling, page order."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = os.path.normcase(os.path.abspath(path))
    sig = (st.st_mtime_ns, st.st_size)
    hit = _NAMES_CACHE.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        from ..format import geometry as G
        from ..format.page import parse_ctg
        from ..format.placement import forest_parents

        pp = parse_ctg(path)
        objs = pp.page.objects if pp.page is not None else []
        parents = forest_parents(objs)
    except Exception:  # noqa: BLE001 -- a page that does not parse has no names
        return None
    own: List[str] = []
    for po in objs:
        name = ""
        # the object name: __AN_NAME__, else the legacy 'NAME' key (exactly
        # that spelling -- a genie's 'Name' PARAMETER is not its name), else
        # the header name (editing._name_key)
        for bag in po.bags:
            for k, v in bag.pairs:
                if (k.upper() == "__AN_NAME__" or k == "NAME") and v and not name:
                    name = v
        if not name:
            try:
                hdr = G.parse_geom_header(po.body_raw)
                name = hdr.name if hdr is not None else ""
            except Exception:  # noqa: BLE001
                name = ""
        own.append(name or "")
    out: List[str] = []
    seen = set()
    for i, name in enumerate(own):
        if not name:
            continue
        parts = [name]
        p = parents[i]
        while p is not None:
            if own[p]:
                parts.append(own[p])
            p = parents[p]
        # every suffix of the chain: 'TabBar' and 'Outer.TabBar'
        for n in range(1, len(parts) + 1):
            q = ".".join(reversed(parts[:n]))
            if q.lower() not in seen:
                seen.add(q.lower())
                out.append(q)
    tops = [n for i, n in enumerate(own) if n and parents[i] is None]
    if len(_NAMES_CACHE) > 64:
        _NAMES_CACHE.clear()
    _NAMES_CACHE[key] = (sig, out, tops)
    return out


def page_top_level_names(path: str) -> Optional[List[str]]:
    """The names of the TOP-LEVEL named objects of the page file ``path``
    (a tab bar genie, an alarm list ...), page order; None = unreadable."""
    if page_object_name_list(path) is None:
        return None
    hit = _NAMES_CACHE.get(os.path.normcase(os.path.abspath(path)))
    return list(hit[2]) if hit is not None else []


def _extra_dirs() -> List[str]:
    """The extra library folders the editor searches too (CTG_LIB_PATH: a
    page copied outside its project names its project there)."""
    return [p for p in os.environ.get("CTG_LIB_PATH", "").split(os.pathsep) if p]


def search_dirs(project_dir: str) -> List[str]:
    """The folders of the page folder's tag scope (io.tagscope: the project,
    the projects it includes and the projects compiled with it; the extra
    folders and theirs follow)."""
    from ..io.tagscope import scope

    return scope(project_dir).dirs()


def find_page_file(project_dir: str, page: str) -> Optional[str]:
    """The .ctg of ``page`` in the page folder's tag scope (the project's
    own first), or None."""
    name = (page or "").strip()
    if not name:
        return None
    from ..io.tagscope import find_table

    for d in search_dirs(project_dir):
        p = find_table(d, name + ".ctg")
        if p:
            return p
    return None


def document_context(doc) -> PageContext:
    """The PageContext of an open EditableDocument (its project, include
    chain, staged Page Properties)."""
    from ..io import project as PR

    path = os.path.abspath(doc.path or "")
    project_dir = os.path.dirname(path)
    page_name = os.path.splitext(os.path.basename(path))[0]
    try:
        rec = doc.page_record()
    except Exception:  # noqa: BLE001
        rec = None
    try:
        env = {k: v for k, v in (doc.page_properties() or {}).items() if not str(k).startswith("!")}
    except Exception:  # noqa: BLE001
        env = {}
    template = ""
    try:
        _i, ref = doc.template_reference()
        template = ref.element if ref is not None else ""
    except Exception:  # noqa: BLE001
        template = ""
    width = 0
    try:
        width = int(doc.page.appearance.width or 0)
    except Exception:  # noqa: BLE001
        width = 0
    # the page's tag scope; a loose folder that is no project (no pages.DBF)
    # and names none cannot say which pages exist
    pages: set = set()
    types: set = set()
    if PR.Project(project_dir).has_page_table() or _extra_dirs():
        try:
            pages = {n.lower() for n in PR.chain_page_names(project_dir)}
        except Exception:  # noqa: BLE001
            pages = set()
        try:
            types = {(r.get("NAME") or "").strip().lower() for r in PR.content_types(project_dir)} - {""}
        except Exception:  # noqa: BLE001
            types = set()

    def exists(name: str) -> Optional[bool]:
        if not pages:
            return None
        return name.strip().lower() in pages

    def names(page: str) -> Optional[set]:
        p = find_page_file(project_dir, page)
        return page_object_names(p) if p else None

    return PageContext(
        page_name, rec, env, page_resolution(page_name, template, width), exists, types or None, names
    )


def check_document(doc) -> Report:
    """The workspace checks of an open page (no panes = an empty report)."""
    page = getattr(doc, "page", None)
    path = getattr(doc, "path", "") or ""
    name = os.path.splitext(os.path.basename(path))[0]
    panes = model_panes(page)
    if not panes:
        return Report(name)
    return check_panes(panes, document_context(doc))


def has_panes(doc) -> bool:
    """The open page places at least one pane (the checks apply)."""
    return bool(model_panes(getattr(doc, "page", None)))


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("sa_workspace.names", _NAMES_CACHE, on=("refresh", "tests"))
