"""Read composite genies (visual templates)."""

from __future__ import annotations

import glob
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from .xsdlite import Problem, Schema, XmlSyntaxError, parse_positioned
from . import codes as _codes
from ..infra.logsetup import note_exception as _note_exc

_GUID_RE = re.compile(r"\{([0-9a-fA-F-]{36})\}")
_CACHE_REF_RE = re.compile(r"_CompositionCaches[\\/]\{([0-9a-fA-F-]{36})\}[\\/]cache\.([0-9A-Za-z_]+)", re.I)

INT_MIN, INT_MAX = -2147483648, 2147483647
PARAM_TYPES = ("String", "Int", "Bool")

#: CtDraw's messages (SE.CtDraw.Composition resources), verbatim
MSG_LENGTH = "The length must be between {0} and {1} characters."
MSG_RANGE = "The value must be between {0} and {1}."
MSG_STANDARD = (
    "The previous value '{0}' does not exist in the standard values. The "
    "value must be from one of the standard values."
)
MSG_TYPE = "Invalid value type, expecting type '{0}'."
MSG_EQUIP_LEVELS = "An equipment reference cannot be greater than 14 levels."
MSG_EQUIP_LONG = "Name may not exceed 63 characters."
MSG_EQUIP_CHARS = (
    "An equipment reference may only contain alphanumeric characters, "
    "backslash (\\), or an underscore (_). The dot (.) character is used "
    "to separate equipment levels."
)
MSG_TAG_CLUSTER = "A tag reference may only specify one cluster."
MSG_TAG_CHARS = (
    "A tag reference may only contain alphanumeric characters, backslash (\\) or an underscore (_)."
)
MSG_ROOT = "This is not a valid genie composite template. Expect 'VisualTemplate' as the root element."
MSG_DUPLICATE_GUID = (
    "More than one composite genie template has the template ID '{0}'. "
    "Plant SCADA refuses to insert or update a composite genie until each "
    "template has its own ID (a copied template needs a new Guid, and so "
    "does each of its compositions). The files are: {1}"
)


# -- typed values (TypedValueHandler) --------------------------------------------
def parse_typed(ptype: str, text) -> Tuple[bool, object]:
    """.NET TryParse of ``text`` as a parameter type: (ok, value)."""
    if text is None:
        return False, None
    s = str(text)
    if ptype == "Bool":
        v = s.strip(" \t\r\n\x0b\x0c\x00").lower()
        if v in ("true", "false"):
            return True, v == "true"
        return False, None
    if ptype == "Int":
        v = s.strip(" \t\r\n\x0b\x0c")
        if re.fullmatch(r"[+-]?[0-9]+", v):
            n = int(v)
            if INT_MIN <= n <= INT_MAX:
                return True, n
        return False, None
    return True, s


def type_default(ptype: str):
    """TypedValueHandler.GetDefault: '' / 0 / False."""
    return {"Int": 0, "Bool": False}.get(ptype, "")


def to_text(value) -> str:
    """A typed value as text (.NET ToString: 'True' / 'False')."""
    if isinstance(value, bool):
        return "True" if value else "False"
    return "" if value is None else str(value)


def same_value(a, b) -> bool:
    """.NET Object.Equals on boxed values: same type and equal."""
    return type(a) is type(b) and a == b


# -- conditions (ConditionElementParser) --------------------------------------------
COMPARISONS = (
    "IsEqualTo",
    "IsNotEqualTo",
    "IsGreaterThan",
    "IsGreaterThanOrEqualTo",
    "IsLessThan",
    "IsLessThanOrEqualTo",
    "StartsWith",
    "EndsWith",
    "Contains",
)


@dataclass(frozen=True)
class ParamCondition:
    name: str
    value: object
    comparison: str = "IsEqualTo"


@dataclass(frozen=True)
class GlobalRef:
    id: str
    target: object = field(compare=False, hash=False, repr=False)
    negation: bool = False


@dataclass(frozen=True)
class CompositeCondition:
    operator: str  # 'And' | 'Or'
    children: Tuple[object, ...] = ()
    negation: bool = False


def evaluate(cond, values: Dict[str, object]) -> bool:
    """Evaluate a condition against the typed parameter values (keys
    lower-case). None (no condition) is true."""
    if cond is None:
        return True
    if isinstance(cond, ParamCondition):
        v = values.get(cond.name.lower())
        c, op = cond.value, cond.comparison
        if op == "IsEqualTo":
            return same_value(v, c)
        if op == "IsNotEqualTo":
            return not same_value(v, c)
        if op in ("IsGreaterThan", "IsGreaterThanOrEqualTo", "IsLessThan", "IsLessThanOrEqualTo"):
            if not (isinstance(v, int) and isinstance(c, int)):
                return False
            return {
                "IsGreaterThan": v > c,
                "IsGreaterThanOrEqualTo": not v < c,
                "IsLessThan": v < c,
                "IsLessThanOrEqualTo": not v > c,
            }[op]
        if not (isinstance(v, str) and isinstance(c, str)):
            return False
        return {"StartsWith": v.startswith(c), "EndsWith": v.endswith(c), "Contains": c in v}.get(op, False)
    if isinstance(cond, GlobalRef):
        r = evaluate(cond.target, values)
        return not r if cond.negation else r
    if isinstance(cond, CompositeCondition):
        r = None
        for ch in cond.children:
            if r is None:
                r = evaluate(ch, values)
            elif cond.operator == "And":
                r = r and evaluate(ch, values)
            else:
                r = r or evaluate(ch, values)
        r = bool(r)
        return not r if cond.negation else r
    return True


class ConditionError(ValueError):
    def __init__(self, message: str, node=None):
        super().__init__(message)
        self.node = node


def _parse_bool(text: str) -> bool:
    """.NET Boolean.Parse (the Negation attribute): true / false only."""
    ok, v = parse_typed("Bool", text)
    if not ok:
        raise ValueError("String was not recognized as a valid Boolean.")
    return v


def parse_condition(node: ET.Element, globals_: Dict[str, object], params: Dict[str, "TemplateParam"]):
    """A condition element -> its model; ``globals_`` = the global conditions
    defined so far (lower-case ids), ``params`` = the parameters (lower-case
    names). Raises :class:`ConditionError` with CtDraw's message."""
    tag = node.tag
    if tag == "ParameterCondition":
        name = node.get("Name") or ""
        p = params.get(name.lower())
        if p is None:
            raise ConditionError("The referenced parameter '%s' does not exist." % name, node)
        ok, val = parse_typed(p.type, node.get("Value"))
        if not ok:
            raise ConditionError(MSG_TYPE.format(p.type), node)
        op = node.get("Comparison") or "IsEqualTo"
        if op not in COMPARISONS:
            raise ConditionError("'%s' is not a comparison operator." % op, node)
        k = COMPARISONS.index(op)
        if (2 <= k <= 5 and p.type != "Int") or (6 <= k <= 8 and p.type != "String"):
            raise ConditionError(
                "Type mismatch between the type of the parameter '%s' and the comparison "
                "operator '%s'." % (p.name, op),
                node,
            )
        return ParamCondition(p.name, val, op)
    if tag == "GlobalConditionRef":
        cid = node.get("Id") or ""
        target = globals_.get(cid.lower())
        if cid.lower() not in globals_:
            raise ConditionError("The referenced global condition '%s' does not exist." % cid, node)
        try:
            neg = _parse_bool(node.get("Negation")) if node.get("Negation") is not None else False
        except ValueError as e:
            raise ConditionError(str(e), node) from None
        return GlobalRef(cid, target, neg)
    if tag == "CompositeCondition":
        op = node.get("Operator") or ""
        if op not in ("And", "Or"):
            raise ConditionError("'%s' is not a logical operator (And / Or)." % op, node)
        kids = tuple(parse_condition(ch, globals_, params) for ch in node)
        try:
            neg = _parse_bool(node.get("Negation")) if node.get("Negation") is not None else False
        except ValueError as e:
            raise ConditionError(str(e), node) from None
        return CompositeCondition(op, kids, neg)
    raise ConditionError("Unexpected condition element '%s'." % tag, node)


# -- naming rules (NamingRule) ------------------------------------------------------
_ASSOCIATION_RE = re.compile(r"^[^?]*\?[^?]+\?[^?]*$")


def _cat(ch: str) -> str:
    return unicodedata.category(ch)


def _name_char(ch: str) -> bool:
    """[\\p{L}\\p{M}\\p{Nd}_\\\\]"""
    c = _cat(ch)
    return c[0] in "LM" or c == "Nd" or ch in "_\\"


def _rule_regex(value: str, first_digit: bool) -> bool:
    """``^[first]\\.?(?>[name]{1,63}\\.{1})*[name]{1,63}$`` (first = \\p{L}\\p{M}_
    [+ \\p{Nd} for a tag]): the first character, an optional dot, then
    dot-separated parts of 1..63 name characters."""
    if not value:
        return False
    c0 = value[0]
    c = _cat(c0)
    if not (c[0] in "LM" or c0 == "_" or (first_digit and c == "Nd")):
        return False
    rest = value[1:]
    if rest.startswith("."):
        rest = rest[1:]
    if not rest:
        return False
    return all(1 <= len(p) <= 63 and all(_name_char(ch) for ch in p) for p in rest.split("."))


def naming_problem(rule: str, value: str) -> str:
    """'' when ``value`` follows naming rule ``rule`` ('Equipment' / 'Tag' /
    ''), else CtDraw's message."""
    if not rule or rule not in ("Equipment", "Tag"):
        return ""
    if _ASSOCIATION_RE.match(value):
        return ""  # a ?association? is accepted
    if rule == "Equipment":
        if value.count(".") >= 14:
            return MSG_EQUIP_LEVELS
        if any(len(p) > 63 for p in value.split(".") if p):
            return MSG_EQUIP_LONG
        return "" if _rule_regex(value, False) else MSG_EQUIP_CHARS
    if value.count(".") > 1:
        return MSG_TAG_CLUSTER
    return "" if _rule_regex(value, True) else MSG_TAG_CHARS


# -- the template model ---------------------------------------------------------------
@dataclass(frozen=True)
class TemplateParam:
    name: str
    label: str = ""
    type: str = "String"  # String | Int | Bool
    default: str = ""  # the DefaultValue text ('' when absent)
    description: str = ""
    values: Tuple[str, ...] = ()  # the standard value labels
    min_length: int = 0
    max_length: int = INT_MAX
    min_value: int = INT_MIN
    max_value: int = INT_MAX
    naming_rule: str = ""  # 'Equipment' | 'Tag' | ''
    dependency: object = field(default=None, compare=False, hash=False, repr=False)
    has_default: bool = False

    @property
    def default_value(self):
        """The typed default (DefaultValue parsed; absent: '' / 0 / false)."""
        if self.has_default:
            ok, v = parse_typed(self.type, self.default)
            if ok:
                return v
        return type_default(self.type)

    @property
    def standard_values(self) -> Tuple[object, ...]:
        out = []
        for lab in self.values:
            ok, v = parse_typed(self.type, lab)
            if ok:
                out.append(v)
        return tuple(out)


@dataclass(frozen=True)
class CompositeTemplate:
    guid: str  # lower-case, no braces
    name: str
    path: str
    params: Tuple[TemplateParam, ...] = field(default_factory=tuple)
    #: every problem of the file (XML, schema, CtDraw's own checks); CtDraw
    #: refuses a template with any of them
    problems: Tuple[Problem, ...] = field(default=(), compare=False, hash=False)
    #: other template files on the page's path with the same Guid
    duplicates: Tuple[str, ...] = field(default=(), compare=False, hash=False)

    def param(self, name: str) -> Optional[TemplateParam]:
        low = (name or "").lower()
        for p in self.params:
            if p.name.lower() == low:
                return p
        return None

    @property
    def template_id(self) -> str:
        """The ID as CtDraw shows it (Guid.ToString("B"): braces, lower case)."""
        return "{%s}" % self.guid


# -- value rules on a template -------------------------------------------------------
def adapt_value(param: TemplateParam, value):
    """``AdaptValue``: an input value converted to the parameter's type (a
    string parsed, a bool <-> 0 / 1), else the default."""
    default = param.default_value
    if not isinstance(value, (str, int, bool)):
        return default
    if param.type == "String":
        return to_text(value)
    if param.type == "Int":
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, int):
            return value
        ok, v = parse_typed("Int", value)
        return v if ok else default
    if param.type == "Bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        ok, v = parse_typed("Bool", value)
        return v if ok else default
    return default


def validate_value(param: TemplateParam, value) -> str:
    """``ValidateValue``: '' when valid, else CtDraw's message."""
    want = {"String": str, "Int": int, "Bool": bool}.get(param.type, str)
    if type(value) is not want:
        return MSG_TYPE.format(param.type)
    std = param.standard_values
    if std and not any(same_value(s, value) for s in std):
        return MSG_STANDARD.format(to_text(value))
    if param.type == "String":
        if not param.min_length <= len(value) <= param.max_length:
            return MSG_LENGTH.format(param.min_length, param.max_length)
        return naming_problem(param.naming_rule, value)
    if param.type == "Int" and not param.min_value <= value <= param.max_value:
        return MSG_RANGE.format(param.min_value, param.max_value)
    return ""


def typed_values(template: CompositeTemplate, current: Optional[dict] = None) -> Dict[str, object]:
    """``AdaptParameterInputs``: every parameter's value (lower-case keys) --
    the default, or ``current``'s value (any case) adapted to the type."""
    cur = {str(k).lower(): v for k, v in (current or {}).items()}
    out: Dict[str, object] = {}
    for p in template.params:
        low = p.name.lower()
        out[low] = adapt_value(p, cur[low]) if low in cur else p.default_value
    return out


def shown(param: TemplateParam, values: Dict[str, object]) -> bool:
    """The parameter's dependency holds (it is shown in the form)."""
    try:
        return evaluate(param.dependency, values)
    except Exception:  # noqa: BLE001 -- a broken condition never hides a row
        _note_exc("composites.shown")
        return True


def invalid_parameters(template: CompositeTemplate, values: Dict[str, object]) -> Dict[str, str]:
    """``GetInvalidParameters``: {name: message} of the SHOWN parameters whose
    value is invalid (``values`` from :func:`typed_values`)."""
    out: Dict[str, str] = {}
    for p in template.params:
        v = values.get(p.name.lower(), p.default_value)
        if not shown(p, values):
            continue
        msg = validate_value(p, v)
        if msg:
            out[p.name] = msg
    return out


def final_values(template: CompositeTemplate, values: Dict[str, object]) -> Dict[str, object]:
    """``GetFinalParameterValues``: a hidden parameter takes its default."""
    return {
        p.name: (values.get(p.name.lower(), p.default_value) if shown(p, values) else p.default_value)
        for p in template.params
    }


# -- template loading + checks -----------------------------------------------------------
_ANIMATION_NAME_RE = re.compile(r"^[A-Z|a-z]{1}?[A-Z|a-z|0-9|_]{0,31}$")
MSG_ANIMATION_NAME = (
    "The animation name '{0}' is invalid. An animation name must begin with a letter and "
    "contain only English letters, numbers and the underscore character '_'. An animation "
    "name defined in a composite genie template must not exceed 32 characters."
)


def schema_path() -> Optional[str]:
    """Bin\\CompositionTemplate.xsd of the Plant SCADA install (CTG_SCADA_BIN
    names the Bin folder searched first), or None. The installs by product family, the
    newest first within each (infra/install.bin_dirs_newest_first)."""
    from ..infra import install

    for b in install.bin_dirs_newest_first():
        p = os.path.join(b, "CompositionTemplate.xsd")
        if os.path.isfile(p):
            return p
    return None


_SCHEMA: Dict[str, object] = {}


def _schema() -> Optional[Schema]:
    p = schema_path()
    if p is None:
        return None
    key = os.path.normcase(p)
    if key not in _SCHEMA:
        try:
            _SCHEMA[key] = Schema.load(p)
        except Exception:  # noqa: BLE001 -- an unreadable schema skips the check
            _note_exc("composites._schema")
            _SCHEMA[key] = None
    return _SCHEMA[key]


_TEMPLATE_CACHE: Dict[tuple, Optional[CompositeTemplate]] = {}


def load_template(path: str) -> Optional[CompositeTemplate]:
    """The Visual Template in ``path`` (None when the file is not a template
    or has no Guid). Its problems -- XML, schema, CtDraw's own checks -- are
    in ``.problems``; what can be read is read anyway."""
    try:
        st = os.stat(path)
        key = (os.path.normcase(os.path.abspath(path)), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    if key in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[key]
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    tpl = parse_template(data, path)
    if len(_TEMPLATE_CACHE) > 256:
        _TEMPLATE_CACHE.clear()
    _TEMPLATE_CACHE[key] = tpl
    return tpl


def parse_template(data: bytes, path: str = "") -> Optional[CompositeTemplate]:
    """Parse template bytes (see :func:`load_template`)."""
    try:
        tree = parse_positioned(data)
    except XmlSyntaxError:
        return None
    root = tree.root
    if root.tag != "VisualTemplate":
        return None
    guid = (root.get("Guid") or "").strip().strip("{}").lower()
    if not guid:
        return None
    problems: List[Problem] = []

    def at(node, msg: str) -> None:
        problems.append(Problem(msg, *tree.position(node)))

    schema = _schema()
    if schema is not None:
        problems += schema.validate(tree, fold_attrs=("AnimationName",))
    if root.get("xmlns"):
        at(root, MSG_ROOT)
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", guid):
        at(root, "The template Guid '%s' is not a valid GUID." % root.get("Guid"))
    name = root.get("Name") or os.path.splitext(os.path.basename(path))[0]
    params, pmap = _parse_params(root, at)
    globals_ = _parse_globals(root, pmap, at)
    deps = _parse_dependencies(root, globals_, pmap, at)
    params = [replace(p, dependency=deps.get(p.name.lower())) for p in params]
    _check_rest(root, globals_, pmap, at)
    return CompositeTemplate(guid=guid, name=name, path=path, params=tuple(params), problems=tuple(problems))


def _parse_params(root, at):
    params: List[TemplateParam] = []
    pmap: Dict[str, TemplateParam] = {}
    pnode = root.find("Parameters")
    for p in pnode if pnode is not None else []:
        if p.tag != "Parameter" or not p.get("Name"):
            continue
        pname = p.get("Name")
        if pname.lower() in pmap:
            at(p, "Duplicate parameter name - '%s'." % pname)
            continue
        ptype = p.get("Type") or "String"
        if ptype not in PARAM_TYPES:
            ptype = "String"

        def num(attr, default):
            try:
                return int(p.get(attr)) if p.get(attr) is not None else default
            except ValueError:
                return default

        dv = p.get("DefaultValue")
        if dv is not None and not parse_typed(ptype, dv)[0]:
            at(p, "The default value is of the wrong type for the parameter '%s'." % pname)
        labels = []
        vals = p.find("Values")
        for v in vals if vals is not None else []:
            lab = v.get("Label")
            if lab is None:
                continue
            if not parse_typed(ptype, lab)[0]:
                at(v, MSG_TYPE.format(ptype))
                continue
            labels.append(lab)
        rule = p.get("NamingRules") or ""
        tp = TemplateParam(
            name=pname,
            label=p.get("Label") or pname,
            type=ptype,
            default=dv or "",
            description=p.get("Description") or "",
            values=tuple(labels),
            min_length=num("MinLength", 0),
            max_length=num("MaxLength", INT_MAX),
            min_value=num("MinValue", INT_MIN),
            max_value=num("MaxValue", INT_MAX),
            naming_rule=rule if rule in ("Equipment", "Tag") else "",
            has_default=dv is not None,
        )
        params.append(tp)
        pmap[pname.lower()] = tp
    return params, pmap


def _first_child(node):
    for ch in node:
        return ch
    return None


def _parse_globals(root, pmap, at) -> Dict[str, object]:
    globals_: Dict[str, object] = {}
    gnode = root.find("GlobalConditions")
    for g in gnode if gnode is not None else []:
        gid = g.get("Id") or ""
        if gid.lower() in globals_:
            at(g, "Duplicate global condition Id - '%s'." % gid)
            continue
        ch = _first_child(g)
        cond = None
        if ch is not None:
            try:
                cond = parse_condition(ch, globals_, pmap)
            except ConditionError as e:
                at(e.node if e.node is not None else g, str(e))
        globals_[gid.lower()] = cond
    return globals_


def _parse_dependencies(root, globals_, pmap, at) -> Dict[str, object]:
    deps: Dict[str, object] = {}
    pnode = root.find("Parameters")
    for p in pnode if pnode is not None else []:
        dep = p.find("Dependency")
        if dep is None or not p.get("Name"):
            continue
        ch = _first_child(dep)
        if ch is None:
            continue
        try:
            deps[p.get("Name").lower()] = parse_condition(ch, globals_, pmap)
        except ConditionError as e:
            at(e.node if e.node is not None else dep, str(e))
    return deps


def _check_rest(root, globals_, pmap, at) -> None:
    """CtDraw's checks of the content items, alarm indicator declarations and
    compositions (CompositionBuilder / AlarmIndicatorConfigurationParser)."""

    def item_key(text):
        # content item Ids are integers (ParseContentItems keys an Int32
        # dictionary): '01' and '1' are the same item
        t = (text or "").strip()
        ok, v = parse_typed("Int", t)
        return v if ok else t

    items = set()
    cnode = root.find("ContentItems")
    for c in cnode if cnode is not None else []:
        cid = (c.get("Id") or "").strip()
        if item_key(cid) in items:
            at(c, "Duplicate content item Id - '%s'." % cid)
        items.add(item_key(cid))
    links, styles = set(), set()
    ai = root.find("AlarmIndicators")
    if ai is not None:
        for e in ai.iter("EquipmentLink"):
            lid = (e.get("Id") or "").lower()
            if lid in links:
                at(e, "Duplicate equipment link Id - '%s'." % e.get("Id"))
            links.add(lid)
        defaults = 0
        for b in ai.iter("BorderStyle"):
            bid = (b.get("Id") or "").lower()
            if bid in styles:
                at(b, "Duplicate border style Id - '%s'." % b.get("Id"))
            styles.add(bid)
            if (b.get("IsDefault") or "").strip().lower() in ("true", "1"):
                defaults += 1
                if defaults == 2:
                    at(b, "More than one border style has been assigned as default.")
    comps = root.find("Compositions")
    if comps is None:
        return

    def gref(node, attr) -> None:
        v = node.get(attr)
        if v is not None and v.lower() not in globals_:
            at(node, "The referenced global condition '%s' does not exist." % v)

    def cond_child(node, tag) -> None:
        sub = node.find(tag)
        ch = _first_child(sub) if sub is not None else None
        if ch is not None:
            try:
                parse_condition(ch, globals_, pmap)
            except ConditionError as e:
                at(e.node if e.node is not None else sub, str(e))

    for comp in comps.findall("Composition"):
        cond_child(comp, "Prerequisite")
        for node in comp.iter():
            if node.tag not in ("Content", "Container"):
                continue
            gref(node, "VisibleWhen")
            cond_child(node, "VisibleWhen")
            name = node.get("AnimationName")
            if name is not None and not _ANIMATION_NAME_RE.match(name.upper()):
                at(node, MSG_ANIMATION_NAME.format(name.upper()))
            if node.tag == "Content":
                iid = (node.get("ItemId") or "").strip()
                if iid and item_key(iid) not in items:
                    at(node, "The referenced content item Id '%s' does not exist." % iid)
                prm = node.find("Parameters")
                for cp in prm if prm is not None else []:
                    tp = cp.get("TemplateParameter")
                    if tp is not None and tp.lower() not in pmap:
                        at(cp, "The referenced template parameter '%s' does not exist." % tp)
            trig = node.find("Triggers")
            for t in trig if trig is not None else []:
                if t.get("When") is None and t.find("When") is None:
                    at(t, "'When' condition is missing in the trigger.")
                gref(t, "When")
                cond_child(t, "When")
            ain = node.find("AlarmIndicator")
            if ain is not None:
                _check_indicator(ain, links, styles, pmap, at)


def _check_indicator(ain, links, styles, pmap, at) -> None:
    el = ain.find("EquipmentLink")
    attr = ain.get("EquipmentLink")
    if attr is not None and el is not None:
        at(ain, "'EquipmentLink' cannot be simultaneously specified as both attribute and child element.")
    elif attr is not None and attr.lower() not in links and attr.lower() not in pmap:
        at(ain, "'%s' does not match any predefined equipment links or template parameters." % attr)
    bs = ain.get("BorderStyle")
    if bs is not None and ain.find("BorderStyle") is not None:
        at(ain, "'BorderStyle' cannot be simultaneously specified in both attribute and element.")
    elif bs is not None and bs.lower() not in styles:
        at(ain, "'%s' does not match any predefined border styles." % bs)


def check_template(path: str) -> List[Problem]:
    """Every problem of a template file -- XML syntax first (then nothing
    else can be checked), else the schema and CtDraw's own checks."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return [Problem("The file cannot be read: %s" % e.strerror)]
    try:
        tree = parse_positioned(data)
    except XmlSyntaxError as e:
        return [e.problem]
    if tree.root.tag != "VisualTemplate":
        return [Problem(MSG_ROOT, *tree.position(tree.root))]
    tpl = parse_template(data, path)
    if tpl is None:
        return [Problem("The template has no Guid attribute.", *tree.position(tree.root))]
    return list(tpl.problems)


# -- the template list of a page ----------------------------------------------------------
def _project_roots(page_path: str) -> List[str]:
    """The page's project folder, then the projects it includes (recursive,
    format/projects.include_search_dirs). ``CTG_LIB_PATH`` folders (a page
    copied out of its project) count as the page's project, as for the
    symbol resolver."""
    from .projects import include_search_dirs

    proj = os.path.dirname(os.path.abspath(page_path))
    extra = [p for p in os.environ.get("CTG_LIB_PATH", "").split(os.pathsep) if p]
    roots = [proj] + extra
    out = list(roots)
    for r in roots:
        try:
            out += include_search_dirs(r)
        except Exception:  # noqa: BLE001 -- a broken registry still has the project
            _note_exc("composites._project_roots")
    seen, uniq = set(), []
    for d in out:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    return uniq


def _template_dirs(page_path: str) -> List[str]:
    """'Composite Genies' folders of the page's project and the projects it
    includes -- the only place CtDraw takes a template from
    (TemplatePicker.IsTemplatePathValid, ErrorMessage_TemplateNotInCurrentProject)."""
    roots = _project_roots(page_path)
    seen, out = set(), []
    for d in roots:
        cand = os.path.join(d, "Composite Genies")
        key = os.path.normcase(os.path.abspath(cand))
        if key not in seen and os.path.isdir(cand):
            seen.add(key)
            out.append(cand)
    return out


def load_templates(page_path: str) -> Dict[str, CompositeTemplate]:
    """Every Visual Template reachable from ``page_path``: guid -> template,
    the first file per guid (the page's own project first). A template whose
    guid another file on the path shares lists those files in
    ``.duplicates``."""
    found: Dict[str, List[CompositeTemplate]] = {}
    seen = set()
    for d in _template_dirs(page_path):
        # a project name contributes the folder of EVERY install that has it
        # (format/projects.resolve_project_dirs): the same project's same
        # file seen through a second install is one template, not a copy
        proj = os.path.basename(os.path.dirname(os.path.abspath(d))).lower()
        for f in sorted(glob.glob(os.path.join(d, "*.xml"))):
            key = (proj, os.path.basename(f).lower())
            if key in seen:
                continue
            seen.add(key)
            tpl = load_template(f)
            if tpl is not None:
                found.setdefault(tpl.guid, []).append(tpl)
    out: Dict[str, CompositeTemplate] = {}
    for guid, tpls in found.items():
        first = tpls[0]
        if len(tpls) > 1:
            first = replace(first, duplicates=tuple(t.path for t in tpls[1:]))
        out[guid] = first
    return out


def duplicate_warning(tpl: CompositeTemplate) -> str:
    """The duplicate-GUID warning of ``tpl`` ('' when it has none)."""
    if not tpl.duplicates:
        return ""
    return MSG_DUPLICATE_GUID.format(tpl.template_id, "; ".join((tpl.path,) + tpl.duplicates))


# -- placed instances -----------------------------------------------------------------------
def cache_guid(ref_raw: str) -> Optional[str]:
    """The template guid in a ``_CompositionCaches\\{guid}\\cache.x`` ref."""
    if "CompositionCaches" not in (ref_raw or ""):
        return None
    m = _GUID_RE.search(ref_raw)
    return m.group(1).lower() if m else None


def instance_ref(ref_raw: str) -> Optional[Tuple[str, str]]:
    """(template guid, instance hash) of a composite link
    ``_CompositionCaches\\{guid}\\cache.<hash>.<ctm``, or None."""
    m = _CACHE_REF_RE.search(ref_raw or "")
    return (m.group(1).lower(), m.group(2)) if m else None


def in_composition_cache(path: str) -> bool:
    """``path`` lies in a project's ``_CompositionCaches`` folder (which only
    Plant SCADA's composer may write)."""
    parts = os.path.normcase(os.path.abspath(path or "")).split(os.sep)
    return "_compositioncaches" in parts


def cache_dirs(page_path: str, guid: str) -> List[str]:
    """The existing ``_CompositionCaches\\{guid}`` folders of the page's
    project, then of the projects it includes (the order a cache link
    resolves in)."""
    out = []
    for d in _project_roots(page_path):
        cand = os.path.join(d, "_CompositionCaches", "{%s}" % guid.lower())
        if os.path.isdir(cand):
            out.append(cand)
    return out


@dataclass
class CompositeInstance:
    """``Instances\\<hash>.xml``: the options an instance was composed with."""

    path: str
    hash: str
    template_id: str = ""
    #: name -> (type, typed value), document order
    params: Dict[str, Tuple[str, object]] = field(default_factory=dict)
    problems: List[str] = field(default_factory=list)
    migrations: Dict[str, Tuple[str, object]] = field(default_factory=dict)

    def update_values(self, template: CompositeTemplate) -> dict:
        """Use a saved replacement set when the current template accepts it."""
        if self.migrations:
            values = {k: v for k, (_type, v) in self.migrations.items()}
            if not invalid_parameters(template, typed_values(template, values)):
                return values
        return self.values()

    def values(self) -> Dict[str, object]:
        return {k: v for k, (_t, v) in self.params.items()}


def load_instance(path: str, inst_hash: str = "") -> CompositeInstance:
    """Read an instance options file; what cannot be read is reported in
    ``.problems`` with CtDraw's message (it would refuse the instance)."""
    inst_hash = inst_hash or os.path.splitext(os.path.basename(path))[0]
    inst = CompositeInstance(path=path, hash=inst_hash)
    bad = (
        "An unexpected issue has occurred while loading the cached composite genie "
        "instance with the instance hash '%s'." % inst_hash
    )
    try:
        root = ET.parse(path).getroot()
    except OSError, ET.ParseError:
        inst.problems.append(bad)
        return inst
    inst.template_id = (root.get("TemplateId") or "").strip("{}").lower()
    pnode = root.find("Parameters")
    if pnode is None:
        inst.problems.append(bad)
        return inst
    for group, target in [(pnode, inst.params), (root.find("ParameterMigrations"), inst.migrations)]:
        if group is None:
            continue
        seen = set()
        for p in group:
            name, ptype, value = p.get("Name"), p.get("Type"), p.get("Value")
            if not name or ptype not in PARAM_TYPES or value is None:
                ok = False
            else:
                ok, v = parse_typed(ptype, value)
            if not ok or name.lower() in seen:
                inst.problems.append(bad)
                if target is inst.migrations:
                    target.clear()
                    break
                continue
            target[name] = (ptype, v)
            seen.add(name.lower())
    return inst


@dataclass
class CompositeInfo:
    """Everything the Presentation Options of a placed composite shows."""

    guid: str
    hash: str
    template: Optional[CompositeTemplate] = None
    template_source: str = ""  # 'project' | 'cache' | ''
    cache_dir: str = ""
    instance: Optional[CompositeInstance] = None
    notes: List[str] = field(default_factory=list)

    def values(self) -> Dict[str, object]:
        """The instance's options, adapted to the template's parameters
        (``typed_values``); {} without a template."""
        if self.template is None:
            return {}
        return typed_values(self.template, self.instance.values() if self.instance else {})


def cached_template(cache_dir: str, guid: str) -> Optional[CompositeTemplate]:
    """The copy of the template the composition cache keeps
    (``<cache>\\<guid without dashes>.xml``), or None."""
    p = os.path.join(cache_dir, guid.replace("-", "").lower() + ".xml")
    return load_template(p) if os.path.isfile(p) else None


def composite_info(
    page_path: str, ref_raw: str, templates: Optional[Dict[str, CompositeTemplate]] = None
) -> Optional[CompositeInfo]:
    """The template, instance options and cache folder of a placed composite
    whose link is ``ref_raw`` (None when it is not a composite link).
    ``templates`` = :func:`load_templates` of the page (loaded when None)."""
    ref = instance_ref(ref_raw)
    if ref is None:
        return None
    guid, inst_hash = ref
    info = CompositeInfo(guid=guid, hash=inst_hash)
    dirs = cache_dirs(page_path, guid)
    for d in dirs:
        ip = os.path.join(d, "Instances", inst_hash + ".xml")
        if os.path.isfile(ip):
            info.cache_dir = d
            info.instance = load_instance(ip, inst_hash)
            break
    if not info.cache_dir and dirs:
        info.cache_dir = dirs[0]
    templates = load_templates(page_path) if templates is None else templates
    tpl = templates.get(guid)
    if tpl is not None:
        info.template, info.template_source = tpl, "project"
    elif info.cache_dir:
        tpl = cached_template(info.cache_dir, guid)
        if tpl is not None:
            info.template, info.template_source = tpl, "cache"
            info.notes.append(
                "The template of this composite genie is not in the Composite Genies folder "
                "of this project or the projects it includes. The options are shown with "
                "the copy of the template kept in the composition cache."
            )
    if info.template is None:
        info.notes.append(
            "The template {%s} of this composite genie was not found in this project or the "
            "projects it includes." % guid
        )
    if info.instance is None:
        info.notes.append(
            "The options file of this composite genie (Instances\\%s.xml in the composition "
            "cache) was not found. The values shown are the ones stored on the genie's "
            "objects." % inst_hash
        )
    elif info.instance.problems:
        info.notes.append(info.instance.problems[0])
    return info


# -- donors (Paste Composite Genie copies a placed instance) ---------------------------------
def donor_index(page_path: str) -> Dict[str, Tuple[str, int]]:
    """Scan the page's project dir for composite instances: template guid ->
    (donor page path, donor root index). First hit per guid wins."""
    from .page import parse_ctg
    from .symbols import reference_from_object

    out: Dict[str, Tuple[str, int]] = {}
    proj = os.path.dirname(os.path.abspath(page_path))
    for path in sorted(glob.glob(os.path.join(proj, "*.ctg"))):
        try:
            parsed = parse_ctg(path)
        except Exception:  # noqa: BLE001
            _note_exc("composites.donor_index")
            continue
        if not parsed.page:
            continue
        for po in parsed.page.objects:
            if po.type_code != 0x08 or po.class_flag == 0:
                continue
            try:
                ref = reference_from_object(po)
            except Exception:  # noqa: BLE001
                _note_exc("composites.donor_index")
                continue
            guid = cache_guid(ref.raw) if ref is not None else None
            if guid and guid not in out:
                out[guid] = (path, po.index)
    return out


def donor_ref(donor: Tuple[str, int]) -> str:
    """The composite link of donor instance ``(page path, root index)``."""
    from .page import parse_ctg
    from .symbols import reference_from_object

    try:
        objs = parse_ctg(donor[0]).page.objects
        ref = reference_from_object(objs[donor[1]])
    except Exception:  # noqa: BLE001
        _note_exc("composites.donor_ref")
        return ""
    return ref.raw if ref is not None else ""


def donor_options(donor: Tuple[str, int]) -> Dict[str, str]:
    """The options of donor instance ``(page path, root index)`` -- what a
    pasted copy of it keeps: the root's own bindings, else the union of its
    members' (input records skipped; first occurrence wins)."""
    from .page import parse_ctg
    from .writer import genie_bindings

    try:
        objs = parse_ctg(donor[0]).page.objects
    except Exception:  # noqa: BLE001
        _note_exc("composites.donor_options")
        return {}
    k = donor[1]
    if not 0 <= k < len(objs):
        return {}
    own = genie_bindings(objs[k])
    if own:
        return dict(own)
    end, pending = k, 1
    while pending and end < len(objs):
        pending += objs[end].class_flag - 1
        end += 1
    out: Dict[str, str] = {}
    for po in objs[k + 1 : end]:
        if po.type_code in (_codes.TC.TOUCH, _codes.TC.KEYBOARD):
            continue
        for key, v in genie_bindings(po).items():
            out.setdefault(key, v)
    return out


def donor_info(
    donor: Tuple[str, int], templates: Optional[Dict[str, CompositeTemplate]] = None
) -> Optional[CompositeInfo]:
    """:func:`composite_info` of a donor instance (its own page's cache)."""
    ref = donor_ref(donor)
    return composite_info(donor[0], ref, templates) if ref else None


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("composites.templates", _TEMPLATE_CACHE, on=("refresh", "tests"))


# (a composite schema file read per path)
_caches.register("composites.schemas", _SCHEMA, on=("refresh", "tests"))
