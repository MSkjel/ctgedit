"""A small XML Schema validator, enough for ``CompositionTemplate.xsd``."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import xml.parsers.expat
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

XS = "{http://www.w3.org/2001/XMLSchema}"
UNBOUNDED = None


@dataclass(frozen=True)
class Problem:
    """One validation problem: ``message`` at ``line`` / ``column`` (1-based;
    0 = unknown)."""

    message: str
    line: int = 0
    column: int = 0

    def __str__(self) -> str:
        if self.line:
            return "Line %d, position %d: %s" % (self.line, self.column, self.message)
        return self.message


# -- positioned parsing -------------------------------------------------------
class PositionedTree:
    """An ElementTree root plus the source position of every element."""

    def __init__(self, root: ET.Element, positions: Dict[int, Tuple[int, int]]):
        self.root = root
        self._pos = positions

    def position(self, el: ET.Element) -> Tuple[int, int]:
        return self._pos.get(id(el), (0, 0))


def parse_positioned(data: bytes) -> PositionedTree:
    """Parse XML bytes into elements that remember their line / position.
    Raises :class:`XmlSyntaxError` for XML that is not well formed."""
    builder = ET.TreeBuilder()
    positions: Dict[int, Tuple[int, int]] = {}
    p = xml.parsers.expat.ParserCreate()
    p.buffer_text = True
    # expat counts a byte order mark as line 1's first column; .NET does not
    bom = 1 if data[:3] == b"\xef\xbb\xbf" or data[:2] in (b"\xff\xfe", b"\xfe\xff") else 0

    def start(tag, attrs):
        el = builder.start(tag, attrs)
        # expat points at the '<' (0-based column); .NET reports the name
        line = p.CurrentLineNumber
        positions[id(el)] = (line, p.CurrentColumnNumber + 2 - (bom if line == 1 else 0))

    p.StartElementHandler = start
    p.EndElementHandler = builder.end
    p.CharacterDataHandler = builder.data
    try:
        p.Parse(data, True)
    except xml.parsers.expat.ExpatError as e:
        raise XmlSyntaxError(
            Problem(
                "The file is not valid XML: %s."
                % xml.parsers.expat.errors.messages.get(e.code, str(e)).rstrip("."),
                e.lineno,
                (e.offset or 0) + 1 - (bom if e.lineno == 1 else 0),
            )
        ) from None
    root = builder.close()
    return PositionedTree(root, positions)


class XmlSyntaxError(ValueError):
    def __init__(self, problem: Problem):
        super().__init__(str(problem))
        self.problem = problem


# -- the schema model ---------------------------------------------------------
@dataclass
class _Elem:
    name: str
    type: Optional[str] = None  # a named complex type
    inline: Optional["_Complex"] = None  # or an anonymous one
    min: int = 1
    max: Optional[int] = 1
    uniques: List[Tuple[str, List[str], str]] = field(default_factory=list)


@dataclass
class _Group:
    kind: str  # 'sequence' | 'choice' | 'all'
    items: List[object] = field(default_factory=list)
    min: int = 1
    max: Optional[int] = 1


@dataclass
class _Complex:
    name: str = ""
    attrs: Dict[str, Tuple[str, bool]] = field(default_factory=dict)  # name -> (type, required)
    content: Optional[_Group] = None
    base: Optional[str] = None
    mixed: bool = False


@dataclass
class _Simple:
    name: str
    base: str = "xs:string"
    enums: Tuple[str, ...] = ()
    patterns: Tuple[str, ...] = ()


def _occurs(node: ET.Element, attr: str, default: int) -> Optional[int]:
    v = node.get(attr)
    if v is None:
        return default
    if v == "unbounded":
        return UNBOUNDED
    try:
        return int(v)
    except ValueError:
        return default


class Schema:
    """A loaded schema (see the module docstring for the supported subset)."""

    def __init__(self):
        self.elements: Dict[str, _Elem] = {}
        self.complex: Dict[str, _Complex] = {}
        self.simple: Dict[str, _Simple] = {}

    # -- loading ------------------------------------------------------------
    @classmethod
    def load(cls, path: str) -> "Schema":
        with open(path, "rb") as f:
            return cls.from_bytes(f.read())

    @classmethod
    def from_bytes(cls, data: bytes) -> "Schema":
        root = ET.fromstring(data)
        s = cls()
        for node in root:
            tag = node.tag
            if tag == XS + "element":
                e = s._element(node)
                s.elements[e.name] = e
            elif tag == XS + "complexType":
                ct = s._complex(node)
                s.complex[ct.name] = ct
            elif tag == XS + "simpleType":
                st = s._simple(node)
                s.simple[st.name] = st
        return s

    def _element(self, node: ET.Element) -> _Elem:
        e = _Elem(
            name=node.get("name", ""),
            type=node.get("type"),
            min=_occurs(node, "minOccurs", 1) or 0,
            max=_occurs(node, "maxOccurs", 1),
        )
        for sub in node:
            if sub.tag == XS + "complexType":
                e.inline = self._complex(sub)
            elif sub.tag == XS + "unique":
                sel = sub.find(XS + "selector")
                fld = sub.find(XS + "field")
                if sel is not None and fld is not None:
                    paths = [p.strip() for p in (sel.get("xpath") or "").split("|")]
                    e.uniques.append((sub.get("name", ""), paths, fld.get("xpath", "")))
        return e

    def _group(self, node: ET.Element) -> _Group:
        g = _Group(
            kind=node.tag[len(XS) :],
            min=_occurs(node, "minOccurs", 1) or 0,
            max=_occurs(node, "maxOccurs", 1),
        )
        for sub in node:
            if sub.tag == XS + "element":
                g.items.append(self._element(sub))
            elif sub.tag in (XS + "sequence", XS + "choice", XS + "all"):
                g.items.append(self._group(sub))
        return g

    def _complex(self, node: ET.Element) -> _Complex:
        ct = _Complex(name=node.get("name", ""), mixed=node.get("mixed") == "true")
        self._complex_body(ct, node)
        return ct

    def _complex_body(self, ct: _Complex, node: ET.Element) -> None:
        for sub in node:
            if sub.tag in (XS + "sequence", XS + "choice", XS + "all"):
                ct.content = self._group(sub)
            elif sub.tag == XS + "attribute":
                ct.attrs[sub.get("name", "")] = (sub.get("type", "xs:string"), sub.get("use") == "required")
            elif sub.tag == XS + "complexContent":
                for ext in sub:
                    if ext.tag in (XS + "extension", XS + "restriction"):
                        ct.base = ext.get("base")
                        self._complex_body(ct, ext)

    def _simple(self, node: ET.Element) -> _Simple:
        st = _Simple(name=node.get("name", ""))
        r = node.find(XS + "restriction")
        if r is not None:
            st.base = r.get("base", "xs:string")
            st.enums = tuple(e.get("value", "") for e in r.findall(XS + "enumeration"))
            st.patterns = tuple(p.get("value", "") for p in r.findall(XS + "pattern"))
        return st

    # -- resolved views -----------------------------------------------------
    def _resolve(self, decl: _Elem) -> Optional[_Complex]:
        """The effective complex type of an element declaration: base content
        and attributes merged in for an extension."""
        ct = decl.inline if decl.inline is not None else self.complex.get(decl.type or "")
        return self._effective(ct)

    def _effective(self, ct: Optional[_Complex]) -> Optional[_Complex]:
        if ct is None or not ct.base:
            return ct
        base = self._effective(self.complex.get(ct.base))
        if base is None:
            return ct
        merged = _Complex(
            name=ct.name, attrs=dict(base.attrs), content=base.content, mixed=ct.mixed or base.mixed
        )
        merged.attrs.update(ct.attrs)
        if ct.content is not None:
            merged.content = (
                ct.content if base.content is None else _Group("sequence", [base.content, ct.content])
            )
        return merged

    # -- validation ---------------------------------------------------------
    def validate(self, tree: PositionedTree, fold_attrs: Sequence[str] = ()) -> List[Problem]:
        """Every problem of ``tree`` against the schema. ``fold_attrs``: attributes
        whose values are compared case-insensitively by the unique constraints
        (CtDraw upper-cases every AnimationName before it validates)."""
        out: List[Problem] = []
        root = tree.root
        decl = self.elements.get(root.tag)
        if decl is None:
            out.append(Problem("The '%s' element is not declared." % root.tag, *tree.position(root)))
            return out
        self._validate_element(tree, root, decl, out, {a.lower() for a in fold_attrs})
        return out

    def _validate_element(self, tree, el, decl: _Elem, out: List[Problem], fold: Set[str]) -> None:
        line, col = tree.position(el)
        ct = self._resolve(decl)
        if ct is None:
            return
        # attributes
        for name, value in el.attrib.items():
            if name.startswith("xmlns") or ":" in name:
                continue  # namespace declarations / xsi:*
            spec = ct.attrs.get(name)
            if spec is None:
                out.append(Problem("The '%s' attribute is not declared." % name, line, col))
                continue
            msg = self._check_value(spec[0], value)
            if msg:
                out.append(Problem("The '%s' attribute is invalid - %s" % (name, msg), line, col))
        for name, (_t, required) in ct.attrs.items():
            if required and name not in el.attrib:
                out.append(Problem("The required attribute '%s' is missing." % name, line, col))
        # text content
        if not ct.mixed:
            texts = [el.text or ""] + [c.tail or "" for c in el]
            if any(t.strip() for t in texts):
                out.append(Problem("The element '%s' cannot contain text." % el.tag, line, col))
        # children
        kids = list(el)
        names = [k.tag for k in kids]
        if ct.content is None:
            if kids:
                out.append(
                    Problem(
                        "The element '%s' cannot contain child element '%s' "
                        "because the parent element's content model is empty." % (el.tag, names[0]),
                        *tree.position(kids[0]),
                    )
                )
            return
        decls: Dict[str, _Elem] = {}
        _collect(ct.content, decls)
        if ct.content.kind == "all":
            self._check_all(tree, el, ct.content, kids, out)
        else:
            ok, bad, expected = _match(ct.content, names)
            if not ok:
                if bad < len(kids):
                    out.append(
                        Problem(_invalid_child(el.tag, names[bad], expected), *tree.position(kids[bad]))
                    )
                else:
                    out.append(Problem(_incomplete(el.tag, expected), line, col))
        for k in kids:
            d = decls.get(k.tag)
            if d is not None:
                self._validate_element(tree, k, d, out, fold)
        for uname, selectors, fld in decl.uniques:
            self._check_unique(tree, el, uname, selectors, fld, out, fold)

    def _check_all(self, tree, el, group: _Group, kids, out) -> None:
        allowed = {e.name: e for e in group.items if isinstance(e, _Elem)}
        seen: Dict[str, int] = {}
        for k in kids:
            e = allowed.get(k.tag)
            n = seen.get(k.tag, 0) + 1
            seen[k.tag] = n
            if e is None or (e.max is not None and n > e.max):
                exp = [nm for nm, d in allowed.items() if d.max is None or seen.get(nm, 0) < d.max]
                out.append(Problem(_invalid_child(el.tag, k.tag, exp), *tree.position(k)))
        missing = [nm for nm, d in allowed.items() if d.min > 0 and not seen.get(nm)]
        if missing:
            out.append(Problem(_incomplete(el.tag, missing), *tree.position(el)))

    def _check_unique(self, tree, el, uname, selectors, fld, out, fold) -> None:
        if not fld.startswith("@"):
            return
        attr = fld[1:]
        tags = []
        for s in selectors:
            if s.startswith(".//"):
                tags.append(s[3:])
        seen: Dict[str, bool] = {}
        for d in el.iter():
            if d is el or d.tag not in tags:
                continue
            v = d.get(attr)
            if v is None:
                continue
            key = v.upper() if attr.lower() in fold else v
            if key in seen:
                out.append(
                    Problem(
                        "There is a duplicate key sequence '%s' for the '%s' key or unique "
                        "identity constraint." % (key, uname),
                        *tree.position(d),
                    )
                )
            seen[key] = True

    # -- simple values --------------------------------------------------------
    def _check_value(self, type_name: str, value: str) -> str:
        """'' when ``value`` is valid for ``type_name``, else why not."""
        st = self.simple.get(type_name)
        if st is None:
            return _builtin(type_name, value)
        base_msg = self._check_value(st.base, value)
        if base_msg:
            return base_msg
        v = value
        if _base_builtin(self, st.base) in ("xs:token", "xs:integer", "xs:boolean", "xs:language"):
            v = " ".join(value.split())
        if st.enums and v not in st.enums:
            return (
                "The value '%s' is invalid according to its datatype '%s' - The "
                "Enumeration constraint failed." % (value, st.name)
            )
        for pat in st.patterns:
            try:
                ok = re.search("^" + pat + "$", v) is not None
            except re.error:
                ok = True
            if not ok:
                return (
                    "The value '%s' is invalid according to its datatype '%s' - The "
                    "Pattern constraint failed." % (value, st.name)
                )
        return ""


def _base_builtin(schema: Schema, name: str) -> str:
    seen = 0
    while name in schema.simple and seen < 20:
        name = schema.simple[name].base
        seen += 1
    return name


_LANG_RE = re.compile(r"[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*\Z")


def _builtin(type_name: str, value: str) -> str:
    v = " ".join(value.split())
    if type_name == "xs:integer":
        if not re.fullmatch(r"[+-]?[0-9]+", v):
            return "The string '%s' is not a valid Integer value." % value
    elif type_name == "xs:boolean":
        if v not in ("true", "false", "1", "0"):
            return "The string '%s' is not a valid Boolean value." % value
    elif type_name == "xs:language":
        if not _LANG_RE.match(v):
            return "The value '%s' is not a valid language code." % value
    return ""


# -- content model matching -----------------------------------------------------
def _collect(p, out: Dict[str, _Elem]) -> None:
    if isinstance(p, _Elem):
        out.setdefault(p.name, p)
    else:
        for it in p.items:
            _collect(it, out)


def _match(group: _Group, names: List[str]):
    """Match the child element names against a sequence / choice model.
    Returns (complete, furthest position reached, names expected there)."""
    expected: Dict[int, List[str]] = {}
    far = [0]

    def once(p, starts: Set[int]) -> Set[int]:
        if isinstance(p, _Elem):
            res = set()
            for i in starts:
                lst = expected.setdefault(i, [])
                if p.name not in lst:
                    lst.append(p.name)
                if i < len(names) and names[i] == p.name:
                    res.add(i + 1)
            return res
        if p.kind == "choice":
            res = set()
            for it in p.items:
                res |= occurs(it, starts)
            return res
        cur = set(starts)  # sequence
        for it in p.items:
            cur = occurs(it, cur)
            if not cur:
                break
        return cur

    def occurs(p, starts: Set[int]) -> Set[int]:
        lo, hi = p.min, p.max
        result = set(starts) if lo == 0 else set()
        cur = set(starts)
        k = 0
        visited: Set[int] = set(starts)
        while cur and (hi is None or k < hi):
            cur = once(p, cur)
            k += 1
            if cur:
                far[0] = max(far[0], max(cur))
            if k >= lo:
                result |= cur
            if hi is None:
                cur -= visited
                visited |= cur
        return result

    ends = occurs(group, {0})
    ok = len(names) in ends
    bad = far[0]
    return ok, bad, expected.get(bad, [])


def _invalid_child(parent: str, child: str, expected: Sequence[str]) -> str:
    msg = "The element '%s' has invalid child element '%s'." % (parent, child)
    if expected:
        msg += " List of possible elements expected: '%s'." % ", ".join(expected)
    return msg


def _incomplete(parent: str, expected: Sequence[str]) -> str:
    msg = "The element '%s' has incomplete content." % parent
    if expected:
        msg += " List of possible elements expected: '%s'." % ", ".join(expected)
    return msg
