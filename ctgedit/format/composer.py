"""Build a new composite genie instance from a visual template and its options."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import composites as C

INF = float("inf")

#: LayoutStrategy (the enum order CtDraw uses)
STACK_V, STACK_H, OVERLAID = 0, 1, 2
_LAYOUTS = {"StackedVertical": STACK_V, "StackedHorizontal": STACK_H, "Overlaid": OVERLAID}

#: AnchorPosition
ANCHORS = (
    "None",
    "Center",
    "TopLeft",
    "Top",
    "TopRight",
    "Right",
    "Left",
    "BottomRight",
    "Bottom",
    "BottomLeft",
)
#: AlarmFlagStyle
FLAG_STYLES = ("Hidden", "TopLeft", "Top", "TopRight", "Left", "Right", "BottomRight", "Bottom", "BottomLeft")
#: CompositionBuilder.FlagStyleToFlagLocation: AlarmFlagStyle -> AI_FLAG_LOC
FLAG_LOCATION = {1: 0, 2: 3, 3: 5, 4: 1, 5: 6, 6: 7, 7: 4, 8: 2}
HIERARCHIES = ("EquipmentOnly", "ChildrenOnly", "EquipmentAndChildren")
#: EquipmentHierarchyType -> AI_EQHIER (0 Equipment and Children / 1 Equipment Only /
#: 2 Children Only, the page's combo)
EQHIER = {0: 1, 1: 2, 2: 0}

STUB_NAME = "GroupStub_Internal"
STUB_PROJECT, STUB_LIBRARY, STUB_ITEM = "Include", "internal", "_stub_reserved"
CACHE_LIBRARY = "cache"
STORE_FOLDER = "_CompositionCaches"


class CompositionError(ValueError):
    """The template cannot be composed with these options (CtDraw's message)."""


# ============================================================ template model
@dataclass(frozen=True)
class ContentItemDef:
    """``<ContentItems><Content Id Type Project Library Item Anchor>``."""

    id: int
    type: str = "Genie"  # Genie | Symbol
    project: str = ""
    library: str = ""
    item: str = ""
    anchor: int = 0  # index into ANCHORS (0 = None)

    @property
    def ext(self) -> str:
        return "ctl" if self.type == "Symbol" else "ctm"

    @property
    def key(self) -> Tuple[str, str, str]:
        """LibraryItemReferenceComparer: project, library, item without case."""
        return (self.project.lower(), self.library.lower(), self.item.lower())


@dataclass(frozen=True)
class BorderStyle:
    width: int = 3
    padding: int = 1
    inside: bool = False
    frame_color: int = 0xBFBFBF


@dataclass(frozen=True)
class EquipmentLink:
    template_parameter: str
    include_reference: bool = False
    hierarchy: int = 0  # EquipmentHierarchyType


@dataclass
class Conditional:
    """ConditionalProperty: the fallback, or the value of the FIRST trigger
    whose condition holds."""

    fallback: object
    triggers: List[Tuple[object, object]] = field(default_factory=list)

    def value(self, values: Dict[str, object]):
        for cond, v in self.triggers:
            if _holds(cond, values):
                return v
        return self.fallback


@dataclass
class AlarmConfig:
    link: EquipmentLink
    border: BorderStyle
    flag_style: Conditional  # of AlarmFlagStyle ints
    visible_when: object = None


@dataclass
class Node:
    """A ``<Content>`` / ``<Container>`` of a composition's layout."""

    tag: str  # 'Content' | 'Container'
    h_align: str = "Center"
    v_align: str = "Center"
    margin: Conditional = field(default_factory=lambda: Conditional((0.0, 0.0, 0.0, 0.0)))
    visible_when: object = None  # None = always
    animation_name: str = ""
    zindex: int = -1
    alarm: Optional[AlarmConfig] = None
    # content
    item: Optional[ContentItemDef] = None
    bindings: List[Tuple[str, str]] = field(default_factory=list)  # (genie param, template param)
    # container
    layout: int = STACK_V
    citect_group: bool = False
    children: List["Node"] = field(default_factory=list)


@dataclass
class CompositionDef:
    id: str  # lower-case, no braces
    hotspot: Tuple[float, float] = (0.0, 0.0)
    prerequisite: object = None
    nodes: List[Node] = field(default_factory=list)


@dataclass
class TemplateModel:
    """A Visual Template with its compositions (composites.CompositeTemplate
    = the parameters)."""

    template: C.CompositeTemplate
    data: bytes
    contents: Dict[int, ContentItemDef]
    compositions: List[CompositionDef]
    links: Dict[str, EquipmentLink]
    borders: Dict[str, BorderStyle]
    default_border: BorderStyle

    @property
    def guid(self) -> str:
        return self.template.guid

    def composition_for(self, values: Dict[str, object]) -> Optional[CompositionDef]:
        """TryGetComposition: the first composition whose prerequisite holds."""
        for comp in self.compositions:
            if _holds(comp.prerequisite, values):
                return comp
        return None

    def library_items(self) -> List[ContentItemDef]:
        """The content items, distinct by (project, library, item), template
        order (the metadata's LibraryItemVersions)."""
        out, seen = [], set()
        for c in self.contents.values():
            if c.key not in seen:
                seen.add(c.key)
                out.append(c)
        return out


def _holds(cond, values) -> bool:
    try:
        return C.evaluate(cond, values)
    except Exception:  # noqa: BLE001 -- a broken condition never hides a node
        return True


def _bool_attr(text: Optional[str], default: bool) -> bool:
    if text is None:
        return default
    ok, v = C.parse_typed("Bool", text)
    if not ok:
        raise CompositionError("String was not recognized as a valid Boolean.")
    return v


def _int_attr(text: Optional[str], default: int) -> int:
    if text is None:
        return default
    try:
        return int(text.strip())
    except ValueError:
        raise CompositionError("Input string was not in a correct format.") from None


def _thickness(text: str) -> Tuple[float, float, float, float]:
    """CompositionBuilder.ReadMargin: 'l,t,r,b' as doubles."""
    parts = [p.strip() for p in (text or "").split(",")]
    if len(parts) != 4:
        raise CompositionError("'%s' is not a valid margin." % text)
    try:
        return tuple(float(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        raise CompositionError("'%s' is not a valid margin." % text) from None


def _hotspot(text: str) -> Tuple[float, float]:
    parts = [p.strip() for p in (text or "").split(",")]
    try:
        return float(parts[0]), float(parts[1])
    except ValueError, IndexError:
        raise CompositionError("'%s' is not a valid hotspot." % text) from None


_MODEL_CACHE: Dict[tuple, TemplateModel] = {}


def load_model(path: str) -> TemplateModel:
    """The template at ``path`` with its compositions (cached by file
    stamp). Raises CompositionError when the template has problems -- CtDraw
    refuses such a template -- or is not a template at all."""
    st = os.stat(path)
    key = (os.path.normcase(os.path.abspath(path)), st.st_mtime_ns, st.st_size)
    hit = _MODEL_CACHE.get(key)
    if hit is not None:
        return hit
    with open(path, "rb") as f:
        data = f.read()
    model = parse_model(data, path)
    if len(_MODEL_CACHE) > 64:
        _MODEL_CACHE.clear()
    _MODEL_CACHE[key] = model
    return model


def parse_model(data: bytes, path: str = "") -> TemplateModel:
    tpl = C.parse_template(data, path)
    if tpl is None:
        raise CompositionError(C.MSG_ROOT)
    if tpl.problems:
        raise CompositionError(
            "The composite genie template %s has problems; the first: %s"
            % (os.path.basename(path) or tpl.name, tpl.problems[0])
        )
    root = ET.fromstring(data)
    problems: list = []

    def at(_node, msg):
        problems.append(msg)

    _params, pmap = C._parse_params(root, at)
    globals_ = C._parse_globals(root, pmap, at)
    if problems:
        raise CompositionError(problems[0])
    contents: Dict[int, ContentItemDef] = {}
    cnode = root.find("ContentItems")
    for c in cnode if cnode is not None else []:
        if c.tag != "Content":
            continue
        cid = _int_attr(c.get("Id"), 0)
        anchor = c.get("Anchor")
        contents[cid] = ContentItemDef(
            id=cid,
            type=c.get("Type") or "Genie",
            project=c.get("Project") or "",
            library=c.get("Library") or "",
            item=c.get("Item") or "",
            anchor=ANCHORS.index(anchor) if anchor in ANCHORS else 0,
        )
    links: Dict[str, EquipmentLink] = {}
    borders: Dict[str, BorderStyle] = {}
    default_border = BorderStyle()
    ai = root.find("AlarmIndicators")
    if ai is not None:
        el = ai.find("EquipmentLinks")
        for e in el if el is not None else []:
            links[(e.get("Id") or "").lower()] = _equipment_link(e)
        bl = ai.find("BorderStyles")
        for b in bl if bl is not None else []:
            bs = _border_style(b)
            borders[(b.get("Id") or "").lower()] = bs
            if _bool_attr(b.get("IsDefault"), False):
                default_border = bs
    ctx = _Ctx(globals_, pmap, contents, links, borders, default_border)
    comps: List[CompositionDef] = []
    cnode = root.find("Compositions")
    for comp in cnode.findall("Composition") if cnode is not None else []:
        pre = comp.find("Prerequisite")
        prereq = None
        if pre is not None and len(pre):
            prereq = C.parse_condition(list(pre)[0], globals_, pmap)
        lay = comp.find("Layout")
        hs = lay.get("Hotspot") if lay is not None else None
        nodes = [ctx.node(n) for n in (lay if lay is not None else []) if n.tag in ("Content", "Container")]
        comps.append(
            CompositionDef(
                id=(comp.get("Id") or "").strip().strip("{}").lower(),
                hotspot=_hotspot(hs) if hs else (0.0, 0.0),
                prerequisite=prereq,
                nodes=nodes,
            )
        )
    return TemplateModel(
        template=tpl,
        data=data,
        contents=contents,
        compositions=comps,
        links=links,
        borders=borders,
        default_border=default_border,
    )


def _equipment_link(e) -> EquipmentLink:
    h = e.get("Hierarchy")
    return EquipmentLink(
        template_parameter=e.get("TemplateParameter") or "",
        include_reference=_bool_attr(e.get("IncludeEquipmentReference"), False),
        hierarchy=HIERARCHIES.index(h) if h in HIERARCHIES else 0,
    )


def _border_style(b) -> BorderStyle:
    fc = b.get("FrameColor")
    colour = 0xBFBFBF
    if fc is not None:
        try:
            colour = int(fc.replace("0x", ""), 16)
        except ValueError:
            raise CompositionError("'%s' is not a valid frame colour." % fc) from None
    return BorderStyle(
        width=_int_attr(b.get("Width"), 3),
        padding=_int_attr(b.get("Padding"), 1),
        inside=_bool_attr(b.get("IsInside"), False),
        frame_color=colour,
    )


class _Ctx:
    def __init__(self, globals_, pmap, contents, links, borders, default_border):
        self.globals_, self.pmap = globals_, pmap
        self.contents, self.links = contents, links
        self.borders, self.default_border = borders, default_border

    def cond_ref(self, cid: str):
        low = (cid or "").lower()
        if low not in self.globals_:
            raise CompositionError("The referenced global condition '%s' does not exist." % cid)
        return self.globals_[low]

    def visible_when(self, node):
        v = node.get("VisibleWhen")
        if v is not None:
            return self.cond_ref(v)
        sub = node.find("VisibleWhen")
        if sub is not None and len(sub):
            return C.parse_condition(list(sub)[0], self.globals_, self.pmap)
        return None

    def conditional(self, node, attr, parse, default) -> Conditional:
        """ParseConditionalProperty: the node's attribute (else ``default``) +
        its ``<Triggers>`` that set the attribute."""
        a = node.get(attr)
        prop = Conditional(parse(a) if a is not None else default)
        trig = node.find("Triggers")
        for t in trig if trig is not None else []:
            v = t.get(attr)
            if v is None:
                continue
            when = t.get("When")
            if when is not None:
                cond = self.cond_ref(when)
            else:
                sub = t.find("When")
                if sub is None or not len(sub):
                    raise CompositionError("'When' condition is missing in the trigger.")
                cond = C.parse_condition(list(sub)[0], self.globals_, self.pmap)
            prop.triggers.append((cond, parse(v)))
        return prop

    def alarm(self, el) -> AlarmConfig:
        attr, sub = el.get("EquipmentLink"), el.find("EquipmentLink")
        if attr is not None and sub is not None:
            raise CompositionError(
                "'EquipmentLink' cannot be simultaneously specified as both attribute and child element."
            )
        if attr is not None:
            link = self.links.get(attr.lower())
            if link is None:
                p = self.pmap.get(attr.lower())
                if p is None:
                    raise CompositionError(
                        "'%s' does not match any predefined equipment links or template parameters." % attr
                    )
                link = EquipmentLink(p.name)
        elif sub is not None:
            link = _equipment_link(sub)
        else:
            raise CompositionError("The alarm indicator has no 'EquipmentLink'.")
        battr, bsub = el.get("BorderStyle"), el.find("BorderStyle")
        if battr is not None and bsub is not None:
            raise CompositionError(
                "'BorderStyle' cannot be simultaneously specified in both attribute and element."
            )
        if battr is not None:
            border = self.borders.get(battr.lower())
            if border is None:
                raise CompositionError("'%s' does not match any predefined border styles." % battr)
        elif bsub is not None:
            border = _border_style(bsub)
        else:
            border = self.default_border

        def flag(text):
            for i, n in enumerate(FLAG_STYLES):
                if n.lower() == (text or "").strip().lower():
                    return i
            return 0

        return AlarmConfig(
            link=link,
            border=border,
            flag_style=self.conditional(el, "FlagStyle", flag, 0),
            visible_when=self.visible_when(el),
        )

    def node(self, el) -> Node:
        n = Node(tag=el.tag)
        n.h_align = el.get("HorizontalAlignment") or "Center"
        n.v_align = el.get("VerticalAlignment") or "Center"
        n.margin = self.conditional(el, "Margin", _thickness, (0.0, 0.0, 0.0, 0.0))
        n.visible_when = self.visible_when(el)
        n.animation_name = el.get("AnimationName") or ""
        n.zindex = _int_attr(el.get("ZIndex"), -1)
        ai = el.find("AlarmIndicator")
        n.alarm = self.alarm(ai) if ai is not None else None
        if el.tag == "Content":
            iid = _int_attr(el.get("ItemId"), 0)
            item = self.contents.get(iid)
            if item is None:
                raise CompositionError(
                    "The referenced content item Id '%s' does not exist." % el.get("ItemId")
                )
            n.item = item
            prm = el.find("Parameters")
            for p in prm if prm is not None else []:
                tp = p.get("TemplateParameter") or ""
                if tp.lower() not in self.pmap:
                    raise CompositionError("The referenced template parameter '%s' does not exist." % tp)
                n.bindings.append((p.get("Name") or "", self.pmap[tp.lower()].name))
            return n
        lay = el.get("Layout")
        n.layout = _LAYOUTS.get(lay, STACK_V) if lay else STACK_V
        cg = el.get("CreateGroup")
        if cg is not None:
            n.citect_group = cg == "Yes"
        else:
            n.citect_group = bool(n.animation_name) or ai is not None
        n.children = [self.node(c) for c in el if c.tag in ("Content", "Container")]
        return n


# ============================================================ the option values
def adapted_values(model: TemplateModel, inputs: Optional[dict]) -> Dict[str, object]:
    """AdaptParameterInputs keyed by the parameter's own name, template order."""
    cur = {str(k).lower(): v for k, v in (inputs or {}).items()}
    out: Dict[str, object] = {}
    for p in model.template.params:
        low = p.name.lower()
        out[p.name] = C.adapt_value(p, cur[low]) if low in cur else p.default_value
    return out


def final_values(model: TemplateModel, inputs: Optional[dict]) -> Dict[str, object]:
    """What the Presentation Options' OK hands the composer: the adapted
    values with every HIDDEN parameter at its default
    (GetFinalParameterValues), adapted again."""
    t = model.template
    lower = C.typed_values(t, inputs or {})
    fin = C.final_values(t, lower)
    return adapted_values(model, fin)


def _lower(values: Dict[str, object]) -> Dict[str, object]:
    return {k.lower(): v for k, v in values.items()}


def binding_text(value) -> str:
    """A content parameter's value (ParseContentParameterBindings): the
    invariant text, a Bool upper-cased."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return C.to_text(value)


# ============================================================ hash / digest
def instance_hash(composition_id: str, values: Dict[str, object]) -> str:
    """GenerateMd5HashFromParameterValues (31 hex digits)."""
    gid = str(uuid.UUID(composition_id))
    s = gid + "".join(k.upper() + "[%s, %s]" % (k, C.to_text(v)) for k, v in values.items())
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:31]


def _esc_attr(v: str) -> str:
    return (
        v.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace('"', "&quot;")
        .replace("\t", "&#x9;")
        .replace("\n", "&#xA;")
        .replace("\r", "&#xD;")
    )


def _esc_text(v: str) -> str:
    return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "&#xD;")


def canonical_xml(data: bytes) -> bytes:
    """Inclusive C14N 1.0 without comments, matching .NET's SignedXml."""
    stack: list = []
    root = None
    pending: list = []
    for ev, obj in ET.iterparse(io.BytesIO(data), events=("start-ns", "start", "end")):
        if ev == "start-ns":
            pending.append(obj)
        elif ev == "start":
            node = [obj, pending, []]
            pending = []
            if stack:
                stack[-1][2].append(node)
            else:
                root = node
            stack.append(node)
        else:
            stack.pop()
    out: List[str] = []

    def qname(tag: str, nsmap: dict) -> str:
        if tag.startswith("{"):
            uri, local = tag[1:].split("}", 1)
            for p, u in nsmap.items():
                if u == uri:
                    return "%s:%s" % (p, local) if p else local
            return local
        return tag

    def render(node, nsmap, rendered):
        el, decls, kids = node
        nsmap = dict(nsmap)
        for p, u in decls:
            nsmap[p] = u
        name = qname(el.tag, nsmap)
        out.append("<" + name)
        newr = dict(rendered)
        for p, u in sorted(nsmap.items()):
            if rendered.get(p) != u:
                out.append(' xmlns%s="%s"' % ((":" + p) if p else "", _esc_attr(u)))
                newr[p] = u
        attrs = []
        for k, v in el.attrib.items():
            if k.startswith("{"):
                uri, local = k[1:].split("}", 1)
                pre = next((p for p, u in nsmap.items() if u == uri), "")
                attrs.append((uri, local, "%s:%s" % (pre, local) if pre else local, v))
            else:
                attrs.append(("", k, k, v))
        attrs.sort(key=lambda a: (a[0], a[1]))
        for _u, _l, q, v in attrs:
            out.append(' %s="%s"' % (q, _esc_attr(v)))
        out.append(">")
        if el.text and el.text.strip():
            out.append(_esc_text(el.text))
        for k in kids:
            render(k, nsmap, newr)
            if k[0].tail and k[0].tail.strip():
                out.append(_esc_text(k[0].tail))
        out.append("</%s>" % name)

    if root is not None:
        render(root, {}, {})
    return "".join(out).encode("utf-8")


def template_digest(data: bytes) -> str:
    """XmlHelper.GetDigest of the template document."""
    return base64.b64encode(hashlib.sha1(canonical_xml(data)).digest()).decode("ascii")


def template_copy(data: bytes) -> bytes:
    """The cache's copy of the template."""
    root = ET.fromstring(data)  # drops comments / PIs
    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    rev = {}

    def qname(tag: str) -> str:
        if tag.startswith("{"):
            uri, local = tag[1:].split("}", 1)
            p = rev.get(uri, "")
            return "%s:%s" % (p, local) if p else local
        return tag

    # namespace declarations the root carries (in document order, as written)
    decls = []
    for _ev, (pfx, uri) in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
        decls.append((pfx, uri))
        rev.setdefault(uri, pfx)

    def attrs(el, first: bool) -> str:
        out = []
        if first:
            for p, u in decls:
                out.append(' xmlns%s="%s"' % ((":" + p) if p else "", _xml_attr(u)))
        for k, v in el.attrib.items():
            out.append(' %s="%s"' % (qname(k), _xml_attr(v)))
        return "".join(out)

    def write(el, depth: int, first: bool = False) -> None:
        pad = "  " * depth
        text = el.text if (el.text and el.text.strip()) else ""
        kids = list(el)
        if not kids and not text:
            lines.append("%s<%s%s />" % (pad, qname(el.tag), attrs(el, first)))
            return
        if not kids:
            lines.append(
                "%s<%s%s>%s</%s>" % (pad, qname(el.tag), attrs(el, first), _xml_text(text), qname(el.tag))
            )
            return
        lines.append("%s<%s%s>%s" % (pad, qname(el.tag), attrs(el, first), _xml_text(text)))
        for k in kids:
            write(k, depth + 1)
        lines.append("%s</%s>" % (pad, qname(el.tag)))

    write(root, 0, True)
    return b"\xef\xbb\xbf" + "\r\n".join(lines).encode("utf-8")


# ============================================================ WPF layout
class _Box:
    """One WPF element of the layout (the panel tree CompositionBuilder builds)."""

    __slots__ = (
        "node",
        "kind",
        "w",
        "h",
        "margin",
        "ha",
        "va",
        "vis",
        "children",
        "desired",
        "unclipped",
        "offset",
        "size_error",
    )

    def __init__(self, node, kind):
        self.node = node
        self.kind = kind  # 'leaf' | STACK_V | STACK_H | OVERLAID
        self.w = self.h = None
        self.margin = (0.0, 0.0, 0.0, 0.0)
        self.ha, self.va = "Center", "Center"
        self.vis = "Visible"
        self.children: List[_Box] = []
        self.desired = (0.0, 0.0)
        self.unclipped = None
        self.offset = (0.0, 0.0)
        self.size_error = False


def _close(a: float, b: float) -> bool:
    """DoubleUtil.AreClose."""
    if a == b:
        return True
    eps = (abs(a) + abs(b) + 10.0) * 2.220446049250313e-16
    return -eps < a - b < eps


def _less(a: float, b: float) -> bool:
    return a < b and not _close(a, b)


def _minmax(b: _Box):
    """FrameworkElement.MinMax for an element whose only size property is
    Width / Height (Min 0, Max infinity)."""
    minw, maxw = 0.0, INF
    if b.w is not None:
        maxw = max(min(b.w, INF), 0.0)
        minw = max(min(INF, b.w), 0.0)
    minh, maxh = 0.0, INF
    if b.h is not None:
        maxh = max(min(b.h, INF), 0.0)
        minh = max(min(INF, b.h), 0.0)
    return minw, maxw, minh, maxh


def _measure(b: _Box, aw: float, ah: float) -> None:
    if b.vis == "Collapsed":
        b.desired = (0.0, 0.0)
        return
    ml, mt, mr, mb = b.margin
    mw, mh = ml + mr, mt + mb
    minw, maxw, minh, maxh = _minmax(b)
    fw, fh = max(aw - mw, 0.0), max(ah - mh, 0.0)
    fw = max(minw, min(fw, maxw))
    fh = max(minh, min(fh, maxh))
    if b.kind == "leaf":
        dw = dh = 0.0
    elif b.kind == STACK_V:
        dw = dh = 0.0
        for c in b.children:
            _measure(c, fw, INF)
            dw = max(dw, c.desired[0])
            dh += c.desired[1]
    elif b.kind == STACK_H:
        dw = dh = 0.0
        for c in b.children:
            _measure(c, INF, fh)
            dw += c.desired[0]
            dh = max(dh, c.desired[1])
    else:
        dw = dh = 0.0
        for c in b.children:
            _measure(c, fw, fh)
            dw = max(dw, c.desired[0])
            dh = max(dh, c.desired[1])
    dw, dh = max(dw, minw), max(dh, minh)
    unclipped = (dw, dh)
    clipped = False
    if dw > maxw:
        dw, clipped = maxw, True
    if dh > maxh:
        dh, clipped = maxh, True
    cw, ch = dw + mw, dh + mh
    if cw > aw:
        cw, clipped = aw, True
    if ch > ah:
        ch, clipped = ah, True
    b.unclipped = unclipped if (clipped or cw < 0 or ch < 0) else None
    b.desired = (max(0.0, cw), max(0.0, ch))


def _align(ha, va, client, ink):
    """FrameworkElement.ComputeAlignmentOffset."""
    if ha == "Stretch" and ink[0] > client[0]:
        ha = "Left"
    if va == "Stretch" and ink[1] > client[1]:
        va = "Top"
    if ha in ("Center", "Stretch"):
        x = (client[0] - ink[0]) * 0.5
    elif ha == "Right":
        x = client[0] - ink[0]
    else:
        x = 0.0
    if va in ("Center", "Stretch"):
        y = (client[1] - ink[1]) * 0.5
    elif va == "Bottom":
        y = client[1] - ink[1]
    else:
        y = 0.0
    return x, y


def _arrange(b: _Box, x: float, y: float, w: float, h: float) -> None:
    if b.vis == "Collapsed":
        return
    ml, mt, mr, mb = b.margin
    mw, mh = ml + mr, mt + mb
    arw, arh = max(0.0, w - mw), max(0.0, h - mh)
    if b.unclipped is not None:
        uw, uh = b.unclipped
    else:
        uw, uh = max(0.0, b.desired[0] - mw), max(0.0, b.desired[1] - mh)
    if _less(arw, uw):
        arw = uw
    if _less(arh, uh):
        arh = uh
    if b.ha != "Stretch":
        arw = uw
    if b.va != "Stretch":
        arh = uh
    minw, maxw, minh, maxh = _minmax(b)
    emw, emh = max(uw, maxw), max(uh, maxh)
    if _less(emw, arw):
        arw = emw
    if _less(emh, arh):
        arh = emh
    if b.kind == STACK_V:
        cy = 0.0
        for c in b.children:
            ch = c.desired[1]
            _arrange(c, 0.0, cy, max(arw, c.desired[0]), ch)
            cy += ch
    elif b.kind == STACK_H:
        cx = 0.0
        for c in b.children:
            cw = c.desired[0]
            _arrange(c, cx, 0.0, cw, max(arh, c.desired[1]))
            cx += cw
    elif b.kind == OVERLAID:
        for c in b.children:
            _arrange(c, 0.0, 0.0, arw, arh)
    ink = (min(arw, maxw), min(arh, maxh))
    client = (max(0.0, w - mw), max(0.0, h - mh))
    ox, oy = _align(b.ha, b.va, client, ink)
    b.offset = (ox + x + ml, oy + y + mt)


# ============================================================ composing a layout
@dataclass
class AlarmLayout:
    """AlarmIndicatorLayout: what an item's AI_* keys are written from."""

    equipment: str
    include_reference: bool
    hierarchy: int
    width: int
    padding: int
    inside: bool
    frame_color: int
    flag_style: int

    def keys(self) -> List[Tuple[str, str]]:
        """The AI_* keys in the order CtDraw's GenieLayout writes them
        (ON, EXPR, INCLUDE, EQHIER,
        INSIDE, WIDTH, PADDING, COLOUR, FLAG_ON, FLAG_LOC)."""
        out = [("AI_ON", "1")]
        if self.equipment:
            out.append(("AI_EXPR", self.equipment))
        out += [
            ("AI_INCLUDE", "1" if self.include_reference else "0"),
            ("AI_EQHIER", "%d" % EQHIER.get(self.hierarchy, 1)),
            ("AI_INSIDE", "1" if self.inside else "0"),
            ("AI_WIDTH", "%d" % self.width),
            ("AI_PADDING", "%d" % self.padding),
            ("AI_COLOUR", "%d" % (self.frame_color & 0xFFFFFFFF)),
            ("AI_FLAG_ON", "1" if self.flag_style else "0"),
        ]
        if self.flag_style:
            out.append(("AI_FLAG_LOC", "%d" % FLAG_LOCATION.get(self.flag_style, 0)))
        return out


@dataclass
class Item:
    """A layout item (IItem): a content, a group, or the stub of a
    one-item CtDraw group."""

    kind: str  # 'content' | 'group' | 'stub'
    name: str = ""
    zindex: int = -1
    position: Tuple[float, float] = (0.0, 0.0)  # content / stub top-left
    size: Tuple[float, float] = (0.0, 0.0)
    item: Optional[ContentItemDef] = None
    params: List[Tuple[str, str]] = field(default_factory=list)  # genie param, text
    alarm: Optional[AlarmLayout] = None
    citect_group: bool = False
    children: List["Item"] = field(default_factory=list)


@dataclass
class Layout:
    """A composed layout: the composition, the option values it was composed
    with (adapted, template order) and the items."""

    model: TemplateModel
    composition: CompositionDef
    values: Dict[str, object]
    items: List[Item]
    root_size: Tuple[float, float] = (0.0, 0.0)

    @property
    def hash(self) -> str:
        return instance_hash(self.composition.id, self.values)

    def contents(self) -> List[Item]:
        out: List[Item] = []

        def walk(items):
            for it in items:
                if it.kind == "content":
                    out.append(it)
                elif it.kind == "group":
                    walk(it.children)

        walk(self.items)
        return out

    def used_items(self) -> List[ContentItemDef]:
        """LayoutLibraryItemReferenceIdentifier + ToInstanceMetadata: the
        template's content items (distinct, template order) the layout uses."""
        used = {c.item.key for c in self.contents()}
        return [c for c in self.model.library_items() if c.key in used]

    def has_alarm_flags(self) -> bool:
        """LayoutAlarmFlagItemsIdentifier: an item shows an alarm flag."""

        def walk(items):
            for it in items:
                if it.alarm is not None and it.alarm.flag_style:
                    return True
                if it.kind == "group" and walk(it.children):
                    return True
            return False

        return walk(self.items)


SizeFn = Callable[[ContentItemDef, List[Tuple[str, str]]], Optional[Tuple[float, float]]]
ExpansionFn = Callable[[Tuple[float, float], AlarmLayout], Tuple[Tuple[float, float], Tuple[float, float]]]


def compose_layout(
    model: TemplateModel,
    inputs: Optional[dict],
    size_of: SizeFn,
    expansion: Optional[ExpansionFn] = None,
    final: bool = True,
) -> Layout:
    """Compose the layout of ``model`` for the options ``inputs``."""
    values = final_values(model, inputs) if final else adapted_values(model, inputs)
    low = _lower(values)
    comp = model.composition_for(low)
    if comp is None:
        raise CompositionError(
            "No composition of the template '%s' applies to these options (every "
            "composition's prerequisite fails)." % model.template.name
        )
    root = _Box(None, STACK_V)
    root.ha, root.va = "Left", "Top"
    root.children = [_build_box(n) for n in comp.nodes]
    bad: List[str] = []
    _sizes(root, low, size_of, bad)
    if bad:
        raise CompositionError(
            "Failed to read the size of the content item(s): %s" % ", ".join(sorted(set(bad)))
        )
    for c in root.children:
        _visibility(c, low, expansion)
    _measure(root, INF, INF)
    _arrange(root, 0.0, 0.0, root.desired[0], root.desired[1])
    items = _panel_items(root, low, (0.0, 0.0))
    return Layout(model=model, composition=comp, values=values, items=items, root_size=root.desired)


def _build_box(n: Node) -> _Box:
    b = _Box(n, "leaf" if n.tag == "Content" else n.layout)
    b.ha, b.va = n.h_align, n.v_align
    b.margin = n.margin.fallback
    if n.tag == "Container":
        b.children = [_build_box(c) for c in n.children]
    return b


def _bindings(n: Node, low: Dict[str, object]) -> List[Tuple[str, str]]:
    return [(name, binding_text(low.get(tp.lower()))) for name, tp in n.bindings]


def _sizes(b: _Box, low, size_of: SizeFn, bad: List[str]) -> None:
    if b.kind == "leaf":
        n = b.node
        sz = size_of(n.item, _bindings(n, low))
        if sz is None:
            bad.append("%s.%s.%s" % (n.item.project, n.item.library, n.item.item))
            sz = (0.0, 0.0)
        b.w, b.h = float(sz[0]), float(sz[1])
        return
    for c in b.children:
        _sizes(c, low, size_of, bad)


def _alarm_layout(cfg: AlarmConfig, low) -> Optional[AlarmLayout]:
    """AlarmIndicatorConfigurationHost.TryGetAlarmIndicatorConfiguration."""
    if not _holds(cfg.visible_when, low):
        return None
    eq = low.get(cfg.link.template_parameter.lower())
    return AlarmLayout(
        equipment=C.to_text(eq) if eq is not None else "",
        include_reference=cfg.link.include_reference,
        hierarchy=cfg.link.hierarchy,
        width=cfg.border.width,
        padding=cfg.border.padding,
        inside=cfg.border.inside,
        frame_color=cfg.border.frame_color,
        flag_style=int(cfg.flag_style.value(low)),
    )


def _visibility(b: _Box, low, expansion: Optional[ExpansionFn]) -> None:
    """CheckVisibilityAndMargin (children first)."""
    n = b.node
    any_visible = True
    if b.kind != "leaf":
        for c in b.children:
            _visibility(c, low, expansion)
        any_visible = any(c.vis == "Visible" for c in b.children)
    if not _holds(n.visible_when, low):
        b.vis = "Collapsed"
        return
    b.vis = "Visible" if any_visible else "Hidden"
    m = n.margin.value(low)
    if n.alarm is not None and any_visible:
        al = _alarm_layout(n.alarm, low)
        if al is not None:
            if not al.flag_style:
                if not al.inside:
                    g = al.width + al.padding + 2.0
                    m = (m[0] + g, m[1] + g, m[2] + g, m[3] + g)
            else:
                b.margin = m
                _measure(b, INF, INF)
                size = b.desired
                if expansion is None:
                    raise CompositionError("Unknown error code '1' while resolving alarm indicator extent.")
                (tl, br) = expansion(size, al)
                m = (m[0] + tl[0], m[1] + tl[1], m[2] + br[0], m[3] + br[1])
    b.margin = m


def _count_citect(it: Item) -> int:
    """Group.CountCitectChildItems."""
    n = 0
    for c in it.children:
        if c.kind == "content":
            n += 1
        elif c.kind == "group":
            k = _count_citect(c)
            if c.citect_group:
                if k > 0:
                    n += 1
            else:
                n += k
    return n


def _panel_items(b: _Box, low, origin: Tuple[float, float]) -> List[Item]:
    """GetPanelLayout: the VISIBLE children of a panel as items, ordered by
    ZIndex (stable); ``origin`` = the panel's position in the root."""
    out: List[Item] = []
    for c in b.children:
        if c.vis != "Visible":
            continue
        pos = (origin[0] + c.offset[0], origin[1] + c.offset[1])
        n = c.node
        al = _alarm_layout(n.alarm, low) if n.alarm is not None else None
        if c.kind == "leaf":
            out.append(
                Item(
                    kind="content",
                    name=n.animation_name,
                    zindex=n.zindex,
                    position=pos,
                    size=(c.w, c.h),
                    item=n.item,
                    params=_bindings(n, low),
                    alarm=al,
                )
            )
        else:
            out.append(
                Item(
                    kind="group",
                    name=n.animation_name,
                    zindex=n.zindex,
                    alarm=al,
                    citect_group=n.citect_group,
                    children=_panel_items(c, low, pos),
                )
            )
    out.sort(key=lambda it: it.zindex)
    return out


def marshal(items: Sequence[Item]) -> List[Item]:
    """What the LayoutMarshaller hands CtDraw (Group.Accept): a non-CtDraw
    group's items in its place, an empty group dropped, a CtDraw group with
    one item given the stub first (StubbedGroup: stubs, then the children)."""
    out: List[Item] = []
    for it in items:
        if it.kind != "group":
            out.append(it)
            continue
        n = _count_citect(it)
        if n == 0:
            continue
        if not it.citect_group:
            out.extend(marshal(it.children))
            continue
        kids = marshal(it.children)
        if n == 1:
            first = _first_content(it)
            if first is not None:
                kids = [
                    Item(
                        kind="stub",
                        name=STUB_NAME,
                        position=first.position,
                        item=ContentItemDef(
                            id=0x7FFFFFFF,
                            type="Genie",
                            project=STUB_PROJECT,
                            library=STUB_LIBRARY,
                            item=STUB_ITEM,
                        ),
                    )
                ] + kids
        out.append(
            Item(
                kind="group", name=it.name, zindex=it.zindex, alarm=it.alarm, citect_group=True, children=kids
            )
        )
    return out


def _first_content(it: Item) -> Optional[Item]:
    for c in it.children:
        if c.kind == "content":
            return c
        if c.kind == "group":
            f = _first_content(c)
            if f is not None:
                return f
    return None


def placement(value: float) -> int:
    """GenieLayout's position: (int)(v + 0.5) as a signed 16-bit value."""
    v = int(value + 0.5)
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


# ============================================================ the cache files
def store_dir(project_dir: str, guid: str) -> str:
    return os.path.join(project_dir, STORE_FOLDER, "{%s}" % guid.lower())


def cache_library(guid: str) -> str:
    """The library token of the composite root's link."""
    return "%s\\{%s}\\%s" % (STORE_FOLDER, guid.lower(), CACHE_LIBRARY)


def copied_template_name(guid: str) -> str:
    return guid.replace("-", "").lower() + ".xml"


class _XmlOut:
    """XDocument.Save: UTF-8 with BOM, CRLF, 2-space indent, self-closing
    empty elements with a space before '/>'."""

    def __init__(self):
        self.lines = ['<?xml version="1.0" encoding="utf-8"?>']

    def open(self, depth: int, name: str, attrs: Sequence[Tuple[str, str]] = (), empty: bool = False) -> None:
        a = "".join(' %s="%s"' % (k, _xml_attr(v)) for k, v in attrs)
        self.lines.append("  " * depth + "<%s%s%s>" % (name, a, " /" if empty else ""))

    def text(self, depth: int, name: str, value: str) -> None:
        self.lines.append("  " * depth + "<%s>%s</%s>" % (name, _xml_text(value), name))

    def close(self, depth: int, name: str) -> None:
        self.lines.append("  " * depth + "</%s>" % name)

    def bytes(self) -> bytes:
        return b"\xef\xbb\xbf" + "\r\n".join(self.lines).encode("utf-8")


def _xml_attr(v: str) -> str:
    return (
        str(v)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\r", "&#xD;")
        .replace("\n", "&#xA;")
        .replace("\t", "&#x9;")
    )


def _xml_text(v: str) -> str:
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _type_name(v) -> str:
    """TypedValueHandler.GetDisplayName."""
    if isinstance(v, bool):
        return "Bool"
    if isinstance(v, int):
        return "Int"
    return "String"


def _versions(out: _XmlOut, depth: int, items: Sequence[Tuple[ContentItemDef, str]]) -> None:
    if not items:
        out.open(depth, "LibraryItemVersions", empty=True)
        return
    out.open(depth, "LibraryItemVersions")
    for it, version in items:
        out.open(
            depth + 1, "ItemVersion", [("Project", it.project), ("Library", it.library), ("Item", it.item)]
        )
        out.text(depth + 2, "Version", version)
        out.close(depth + 1, "ItemVersion")
    out.close(depth, "LibraryItemVersions")


def metadata_xml(guid: str, digest: str, versions: Sequence[Tuple[ContentItemDef, str]]) -> bytes:
    """``metadata.xml`` (VisualTemplateMetadata.SaveTo)."""
    out = _XmlOut()
    out.open(0, "TemplateMetadata", [("TemplateId", guid.lower()), ("Digest", digest)])
    _versions(out, 1, versions)
    out.close(0, "TemplateMetadata")
    return out.bytes()


def instance_xml(
    guid: str,
    digest: str,
    values: Dict[str, object],
    versions: Sequence[Tuple[ContentItemDef, str]],
    flag_sizes: Optional[Sequence[Tuple[float, float]]] = None,
    migrations: Optional[Dict[str, object]] = None,
) -> bytes:
    """``Instances\\<hash>.xml`` (AddOrUpdateCompositionInstance)."""
    out = _XmlOut()
    out.open(0, "CompositeInstance", [("TemplateId", guid.lower()), ("Digest", digest)])
    if values:
        out.open(1, "Parameters")
        for k, v in values.items():
            out.open(
                2, "Parameter", [("Name", k), ("Type", _type_name(v)), ("Value", C.to_text(v))], empty=True
            )
        out.close(1, "Parameters")
    else:
        out.open(1, "Parameters", empty=True)
    _versions(out, 1, versions)
    if flag_sizes:
        out.open(1, "AlarmFlags")
        for w, h in flag_sizes:
            out.open(2, "FlagSize", [("Width", _num(w)), ("Height", _num(h))], empty=True)
        out.close(1, "AlarmFlags")
    if migrations:
        out.open(1, "ParameterMigrations")
        for name, value in migrations.items():
            out.open(
                2,
                "Parameter",
                [("Name", name), ("Type", _type_name(value)), ("Value", C.to_text(value))],
                empty=True,
            )
        out.close(1, "ParameterMigrations")
    out.close(0, "CompositeInstance")
    return out.bytes()


def _num(v) -> str:
    """A double as .NET prints it for XAttribute (R format; 20.0 -> '20')."""
    f = float(v)
    return str(int(f)) if f == int(f) else repr(f)


@dataclass
class StoredMetadata:
    template_id: str = ""
    digest: str = ""
    versions: Dict[Tuple[str, str, str], str] = field(default_factory=dict)
    flag_sizes: List[Tuple[float, float]] = field(default_factory=list)
    has_flags: bool = False


def read_metadata(path: str) -> Optional[StoredMetadata]:
    """TemplateId / Digest (the attribute, else the older signature's
    DigestValue) / LibraryItemVersions / AlarmFlags of a metadata.xml or an
    instance file (VisualTemplateMetadata.LoadFrom). None when unreadable."""
    try:
        root = ET.parse(path).getroot()
    except OSError, ET.ParseError:
        return None
    md = StoredMetadata(template_id=(root.get("TemplateId") or "").strip("{}").lower())
    md.digest = root.get("Digest") or ""
    if not md.digest:
        sig = root.find("Signatures")
        if sig is not None:
            for e in sig.iter():
                if e.tag.endswith("DigestValue") and e.text:
                    md.digest = e.text.strip()
                    break
    liv = root.find("LibraryItemVersions")
    for iv in liv if liv is not None else []:
        v = iv.find("Version")
        key = (
            (iv.get("Project") or "").lower(),
            (iv.get("Library") or "").lower(),
            (iv.get("Item") or "").lower(),
        )
        md.versions[key] = (v.text or "").strip().lower() if v is not None else ""
    af = root.find("AlarmFlags")
    if af is not None:
        md.has_flags = True
        for fs in af:
            try:
                md.flag_sizes.append((float(fs.get("Width")), float(fs.get("Height"))))
            except TypeError, ValueError:
                pass
    return md


def instance_current(
    stored: Optional[StoredMetadata],
    digest: str,
    versions: Dict[Tuple[str, str, str], str],
    flag_sizes: Optional[Sequence[Tuple[float, float]]],
    needs_flags: bool,
) -> bool:
    """CompositionInstanceAccessor.IsMetadataMatchTo + IsAlarmFlagMatchTo:
    the instance was composed from this template text, with these content
    item versions (every version it lists is still the current one) and --
    when it shows a flag -- these alarm flag sizes."""
    if stored is None or stored.digest != digest:
        return False
    for key, v in stored.versions.items():
        if versions.get(key, "") != v:
            return False
    if needs_flags or stored.has_flags:
        if [tuple(map(float, s)) for s in (flag_sizes or [])] != [
            tuple(map(float, s)) for s in stored.flag_sizes
        ]:
            return False
    return True


def element_version(identity: bytes) -> str:
    """A content item's version = its element's identity GUID
    (GenieInfoBinder: element, the page-head identity
    bytes 4..20)."""
    if not identity or len(identity) < 20:
        return str(uuid.UUID(int=0))
    return str(uuid.UUID(bytes_le=bytes(identity[4:20])))


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("composer.models", _MODEL_CACHE, on=("refresh", "tests"))
