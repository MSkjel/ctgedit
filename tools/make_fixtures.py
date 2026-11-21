"""Write tests/fixtures/FxInstall, a small fake Plant SCADA install.

python tools/make_fixtures.py            write it again
python tools/make_fixtures.py --check    write to a temp folder and compare
python tools/make_fixtures.py --out DIR  write it into DIR
"""

from __future__ import annotations

import argparse
import contextlib
import filecmp
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(REPO, "tests", "fixtures", "FxInstall")
#: the folder MASTER.DBF names (it never exists: readers fall back to <User>\<name>)
PLACEHOLDER_USER = r"C:\FxInstall\User"
#: the seed of the fixed identities (GUIDs, save times)
SEED = "fxinstall"
#: the projects MASTER.DBF registers, in its order
PROJECTS = (
    "Include",
    "System",
    "FxStyle",
    "FxLib",
    "FxMain",
    "FxOther",
    "FxSim",
    "FxPerf",
    "FxSa",
    "FxCycleA",
    "FxCycleB",
    "FxDemo",
)
#: PROJECT.CIT's text streams (fixed: the fixtures do not change with the day they are made)
PROJECT_CIT_DATE = b"Oct 15th 2026"
PROJECT_CIT_TIME = b"12:00:00"
PROJECT_CIT_VERSION = b"8.50r0.368"
#: FxLib's Page Defaults background (FxMain, FxStyle and the rest: 0xD8D8D8)
FXLIB_BACKGROUND = 0xC8D0D8

# ----------------------------------------------------------------------------- schemas
#: every table is written in the layout CtDraw and Studio expect (ctgedit/format/
#: dbf_schema.json: citect.frm's forms, MASTER.DBF, the language tables), with the index
#: its form keeps (Build.table); a value longer than its field, a field the table has not,
#: or a row with more values than the table has fields stops the run. The fixtures'
#: table names that are not a schema table's: the language tables (fxlandic, fxish)
SCHEMA_OF = {"langtable": "language"}


#: the tables every page project gets, empty unless rows are given (with their index
#: field: the page register inserts into these indexes)
PAGE_PROJECT_TABLES = {
    t: "PAGE"
    for t in (
        "pgdynobj",
        "pgadv",
        "pgalmind",
        "pgansym",
        "pgbar",
        "pgbutton",
        "pgcol",
        "pgkey",
        "pgnum",
        "pgsizmve",
        "pgslider",
        "pgstr",
        "pgsym",
        "pgtext",
        "pgtouch",
        "pgtrend",
        "pgvertex",
        "activex",
    )
}
PAGE_PROJECT_TABLES.update({"pglang": "NATIVE", "changes": "KEY", "pages": "NAME"})
#: the tables a genie save in CtDraw writes in every project of the chain besides the page
#: tables ('Force Compile Genie Pages' = fcgpage, 'Library Objects' = pglibobj), and the tag
#: table a genie open reads ('Variable Tags' = variable: without it CtDraw cannot open a
#: genie whose parameters name tags)
LIBRARY_SAVE_TABLES = ("fcgpage", "pglibobj", "variable")


# ----------------------------------------------------------------------------- run setup
def _environment(work: str, out: str) -> dict:
    """The process environment of a deterministic run (returns what was replaced)."""
    env = {
        "CTG_FIXED_IDENTITY": SEED,
        "CTG_TEXT_DPI": "96",
        "CTG_CODEPAGE": "1252",
        "CTG_SCADA_ROOT": out,
        "CTG_SETTINGS_INI": os.path.join(work, "settings.ini"),
        "CTG_DATA_DIR": os.path.join(work, "data"),
        "CTG_NO_RUNTIME": "1",
        "CTG_NO_STUDIO": "1",
        "CTG_NO_SIM_ENGINE": "1",
        "CTG_NO_CTF_PRECHECK": "1",
        "CTG_NO_DISPLAY_CHECK": "1",
        "CTG_NO_ANIMATION": "1",
        "CTG_NO_DIALOG_REAP": "1",
        "QT_QPA_PLATFORM": "offscreen",
        "QT_QPA_FONTDIR": os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    }
    old = {}
    for k, v in env.items():
        old[k] = os.environ.get(k)
        os.environ[k] = v
    for k in (
        "CTG_LIB_PATH",
        "CTG_SCADA_BIN",
        "CTG_AVEVA_HELP",
        "CTG_LIB_SIBLINGS",
        "CICODESIM_SCADA_BIN",
        "CICODESIM_FUNC0",
    ):
        old[k] = os.environ.pop(k, None)
    return old


def _fill_applied(doc) -> None:
    """Apply every colour animation as CtDraw's Fill > Color Apply would."""
    from ctgedit.format import fillcolour as F
    from ctgedit.format import writer

    page = doc.page
    for i, po in enumerate(doc.parsed.page.objects):
        if po.raw_payload is not None:
            continue
        vb = writer.value_bag(po)
        if not vb or not any(k.upper().startswith("COL") for k, _v in vb):
            continue
        o = page.objects[i] if page is not None and i < len(page.objects) else None
        if o is not None and o.raw.get("_from_template"):
            continue
        if doc._in_genie_context(i):
            continue
        props = doc.object_properties(i)
        get = F._getter(props)
        fc = doc.fill_colour(i)
        target = F.target_values(fc, get)
        if fc.mode == F.THRESHOLD:
            for k, row in enumerate(fc.thresholds):
                v = get("COL_THR_%d" % k)
                if v and F.Threshold.parse(v) == row:
                    target["COL_THR_%d" % k] = v  # (the stored form of the row)
        changes = [(k, v) for k, v in target.items() if get(k) != v]
        if changes:
            assert doc.set_fill_keys(i, changes, label="fill colour"), (i, changes)


@contextlib.contextmanager
def _fill_apply_on_save():
    """Run :func:`_fill_applied` before every document save of the run (pages and library
    elements), so the stored colour keys are CtDraw's."""
    from ctgedit.editing import EditableDocument, ElementDocument

    classes = (EditableDocument, ElementDocument)
    own = {cls: cls.__dict__.get("save") for cls in classes}
    saves = {cls: cls.save for cls in classes}

    def applied(save):
        def applied_save(self, *a, **kw):
            _fill_applied(self)
            return save(self, *a, **kw)

        return applied_save

    for cls in classes:
        cls.save = applied(saves[cls])
    try:
        yield
    finally:
        for cls in classes:
            if own[cls] is None:
                del cls.save
            else:
                cls.save = own[cls]


@contextlib.contextmanager
def _guarded():
    """The hermetic guard around the run (tests/hermetic.py): a read of the real install,
    a COM import or a Citect program fails the run."""
    sys.path.insert(0, os.path.join(REPO, "tests"))
    try:
        import hermetic
    finally:
        sys.path.pop(0)
    with hermetic.guarding() as bad:
        yield
    if bad:
        raise SystemExit("make_fixtures: the run reached outside the fixtures:\n  " + "\n  ".join(bad[:10]))


# ----------------------------------------------------------------------------- helpers
def _w(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(data, str):
        data = data.encode("cp1252")
    with open(path, "wb") as fh:
        fh.write(data)


def _ci(text: str) -> bytes:
    """A Cicode / ini / form text as Windows writes it (CRLF, cp1252)."""
    return text.strip("\n").replace("\r\n", "\n").replace("\n", "\r\n").encode("cp1252") + b"\r\n"


def _wmf(width: int, height: int, colour: int) -> bytes:
    """A small Windows Metafile of our own: a window of width x height and one filled
    rectangle over it (placeable header, as a .wmf file has)."""
    recs = b""

    def rec(fn: int, *words) -> bytes:
        return struct.pack("<IH", 3 + len(words), fn) + struct.pack("<%dh" % len(words), *words)

    recs += rec(0x020B, 0, 0)  # SetWindowOrg(y, x)
    recs += rec(0x020C, height, width)  # SetWindowExt(h, w)
    r, g, b = (colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF
    recs += struct.pack("<IHHBBBBH", 7, 0x02FC, 0, r, g, b, 0, 0)  # CreateBrushIndirect
    recs += rec(0x012D, 0)  # SelectObject(0)
    recs += rec(0x041B, height, width, 0, 0)  # Rectangle(b, r, t, l)
    recs += rec(0x0000)  # end
    words = (18 + len(recs)) // 2
    head = struct.pack("<HHHIHIH", 1, 9, 0x0300, words, 1, 7, 0)
    body = head + recs
    place = struct.pack("<IH4hHI", 0x9AC6CDD7, 0, 0, 0, width, height, 96, 0)
    chk = 0
    for (v,) in struct.iter_unpack("<H", place[:20]):
        chk ^= v
    return place + struct.pack("<H", chk) + body


def _rgba(width: int, height: int, colour: int, border: int = 0x000000) -> bytes:
    """RGBA pixels of a filled square with a 1 px border (our own little picture)."""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            c = border if x in (0, width - 1) or y in (0, height - 1) else colour
            out += bytes(((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF, 0xFF))
    return bytes(out)


class Build:
    """One run of the generator: writes the install under ``root``."""

    def __init__(self, root: str):
        self.root = root
        self.user = os.path.join(root, "User")

    # -- paths
    def proj(self, name: str) -> str:
        return os.path.join(self.user, name)

    # -- tables
    def table(
        self, project: str, table: str, rows=(), file: str = None, index: str = None, schema: str = None
    ):
        from ctgedit.format import dbfschema

        path = os.path.join(self.proj(project), (file or table) + ".DBF")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # (with no ``index``, the schema's: an INDEX / WRITEINDEXED form's KEY, a language
        # table's NATIVE; every shipped project has these .NDX files)
        dbfschema.create_table(
            path, schema or SCHEMA_OF.get(table, table), list(rows), indexes=[index] if index else None
        )
        return path

    def page_tables(self, project: str, pages=()) -> None:
        """Every table a page project's register writes, empty (pages.DBF: ``pages``)."""
        for t, key in PAGE_PROJECT_TABLES.items():
            self.table(project, t, pages if t == "pages" else (), index=key)

    def project_cit(
        self, project: str, *, style="fx_style", res="XGA", title=True, background=0xD8D8D8
    ) -> None:
        """The project's PROJECT.CIT, in the shipped projects' layout: their streams in
        their order, the revisions, date, time and version as text, the GUID in lower case
        without braces. Studio takes a folder without one for a project it cannot locate
        ('Remove it from the list of projects?') and never starts the Graphics Builder."""
        from ctgedit.format import cfb
        from ctgedit.io import projectcit

        streams = [
            ("Name", project.encode("cp1252")),
            ("CompState", b"COMPILED"),
            ("Desc", ("Fixture project %s" % project).encode("cp1252")),
            ("MajorRev", b"1"),
            ("MinorRev", b"0"),
            ("Date", PROJECT_CIT_DATE),
            ("Time", PROJECT_CIT_TIME),
            ("Version", PROJECT_CIT_VERSION),
            ("Style", style.encode("cp1252")),
            ("Res", struct.pack("<I", projectcit.RESOLUTIONS.index(res))),
            ("ShowTitle", struct.pack("<I", 1 if title else 0)),
            ("RO", struct.pack("<I", 0)),
            ("Background", struct.pack("<I", 0x5443)),
            ("Privilege", b""),
            ("ProjectOID", b""),
            ("BackgroundColor", struct.pack("<I", background)),
            ("ProjectGUID", _guid_text().lower().encode("ascii")),
        ]
        _w(os.path.join(self.proj(project), "PROJECT.CIT"), cfb.build("{%s}" % _guid_text(), streams))

    # -- documents
    def blank_page(
        self, project: str, name: str, size=(1024, 768), background=0xD8D8D8, register: bool = True
    ):
        """A new empty page (CtDraw's New Page without a template), opened."""
        from ctgedit.editing import EditableDocument
        from ctgedit.format import template_place as TP

        path = os.path.join(self.proj(project), name + ".ctg")
        data = TP.new_page_bytes(size[0], size[1], background=(background, background))
        _w(path, data)
        doc = EditableDocument.open(path)
        if register:
            doc._creating = True
        else:
            doc._fx_unregistered = True
        return doc

    def template_page(self, project: str, name: str, ref: str, linked: bool = True, register: bool = True):
        """A new page on template ``ref``, opened."""
        from ctgedit.editing import EditableDocument

        doc = EditableDocument.new_from_template(
            ref, os.path.join(self.proj(project), name + ".ctg"), linked=linked, register=register
        )
        if not register:
            doc._fx_unregistered = True
        if linked:
            doc.update_template(force=True, page_properties=False)
        return doc

    def element(self, project: str, lib: str, name: str, kind: str, **kw):
        from ctgedit.editing import ElementDocument

        return ElementDocument.new_element(os.path.join(self.proj(project), lib), name, kind, **kw)


def _guid_text() -> str:
    from ctgedit.infra import identity

    return str(identity.new_guid()).upper()


def _save(doc, path: str = None) -> None:
    """Save a page; a page opened with ``register=False`` is saved without a pages.DBF row
    or pg rows (the save registers every page of a folder with a pages.DBF: the table
    steps aside for it)."""
    target = path or doc.path
    if not getattr(doc, "_fx_unregistered", False):
        doc.save(path)
        return
    folder = os.path.dirname(os.path.abspath(target))
    held = []
    for f in os.listdir(folder):
        if f.lower() == "pages.dbf":
            os.rename(os.path.join(folder, f), os.path.join(folder, f + ".fxhold"))
            held.append(f)
    try:
        doc.save(path)
    finally:
        for f in held:
            os.rename(os.path.join(folder, f + ".fxhold"), os.path.join(folder, f))


def _name(doc, index: int, name: str) -> int:
    assert doc.set_object_name(index, name), name
    return index


# ============================================================================ the install
def build_install(b: Build) -> None:
    from ctgedit.format import colourpalette as CP

    _w(
        os.path.join(b.root, "Config", "citect.ini"),
        _ci(r"""
; the fixture install's citect.ini (the tests' fx_install fixture adds [CtEdit] USER)
[CtEdit]
LastDatabase=FxMain

[Animator]
FastDisplay=1
TemplateUpdate=0

[Page]
ScanTime=250

[LANGUAGE]
CaseSensitive=0
"""),
    )
    # CtDraw's layout: the standard area (entries 0 .. 175) holds colour number n at
    # entry V1_ORDER.index(n), so the sixteen classic colours are numbers 0 .. 15;
    # Transparent at 160; the favourites after the standard area
    pal = [CP.UNUSED_COLOUR] * CP.SIZE
    for n, c in enumerate(CP.STANDARD16):
        pal[CP.V1_ORDER.index(n)] = CP.PaletteColour(c, c, "")
    pal[160] = CP.PaletteColour(CP.TRANSPARENT, CP.TRANSPARENT, "Transparent")
    pal[CP.STANDARD : CP.STANDARD + 3] = [
        CP.PaletteColour(0x00C0C0C0, 0x00C0C0C0, "Fx Silver"),
        CP.PaletteColour(0x00FF0000, 0x00000000, "Fx Flash Red"),
        CP.PaletteColour(0x0000FF00, 0x00000000, "Fx Flash Green"),
    ]
    _w(os.path.join(b.root, "Bin", "citect.pal"), CP.format_entries(pal))
    _w(os.path.join(b.root, "Bin", "CompositionTemplate.xsd"), _ci(COMPOSITION_XSD))
    # (Studio's rows: TITLE = the name, a version, the date and time of the registration)
    b.table(
        "",
        "master",
        [
            {
                "NAME": p,
                "TITLE": p,
                "PATH": PLACEHOLDER_USER + "\\" + p,
                "MAJOR_VER": "0000",
                "MINOR_VER": "0000",
                "DATE": " 1 Oct 2026",
                "TIME": "12:00",
            }
            for p in PROJECTS
        ],
        file="MASTER",
    )


#: Bin\CompositionTemplate.xsd: our own schema of a composite genie template, the subset
#: the fixtures' templates and the editor's checks use (format/xsdlite reads it): upper-case
#: GUIDs, the comparison operators, Label required, integer Value ids, Parameters holding
#: only Parameter, the AnimationName unique key of a Layout (case-insensitive: the editor
#: upper-cases the names before it validates)
COMPOSITION_XSD = r"""<?xml version="1.0" encoding="utf-8"?>
<!-- A schema of our own for composite genie templates (the fixtures' subset) -->
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" elementFormDefault="qualified">
  <xs:simpleType name="Guid">
    <xs:restriction base="xs:string">
      <xs:pattern value="\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}"/>
    </xs:restriction>
  </xs:simpleType>
  <xs:simpleType name="ComparisonOperator">
    <xs:restriction base="xs:string">
      <xs:enumeration value="IsEqualTo"/>
      <xs:enumeration value="IsNotEqualTo"/>
      <xs:enumeration value="IsGreaterThan"/>
      <xs:enumeration value="IsGreaterThanOrEqualTo"/>
      <xs:enumeration value="IsLessThan"/>
      <xs:enumeration value="IsLessThanOrEqualTo"/>
      <xs:enumeration value="StartsWith"/>
      <xs:enumeration value="EndsWith"/>
      <xs:enumeration value="Contains"/>
    </xs:restriction>
  </xs:simpleType>
  <xs:simpleType name="LogicalOperator">
    <xs:restriction base="xs:string">
      <xs:enumeration value="And"/>
      <xs:enumeration value="Or"/>
    </xs:restriction>
  </xs:simpleType>
  <xs:simpleType name="ParameterType">
    <xs:restriction base="xs:string">
      <xs:enumeration value="String"/>
      <xs:enumeration value="Int"/>
      <xs:enumeration value="Bool"/>
    </xs:restriction>
  </xs:simpleType>
  <xs:complexType name="ParameterCondition">
    <xs:attribute name="Name" type="xs:string" use="required"/>
    <xs:attribute name="Value" type="xs:string" use="required"/>
    <xs:attribute name="Comparison" type="ComparisonOperator"/>
    <xs:attribute name="Negation" type="xs:boolean"/>
  </xs:complexType>
  <xs:complexType name="GlobalConditionRef">
    <xs:attribute name="Id" type="xs:string" use="required"/>
    <xs:attribute name="Negation" type="xs:boolean"/>
  </xs:complexType>
  <xs:complexType name="Condition">
    <xs:choice>
      <xs:element name="ParameterCondition" type="ParameterCondition"/>
      <xs:element name="GlobalConditionRef" type="GlobalConditionRef"/>
      <xs:element name="CompositeCondition" type="CompositeCondition"/>
    </xs:choice>
  </xs:complexType>
  <xs:complexType name="CompositeCondition">
    <xs:choice minOccurs="1" maxOccurs="unbounded">
      <xs:element name="ParameterCondition" type="ParameterCondition"/>
      <xs:element name="GlobalConditionRef" type="GlobalConditionRef"/>
      <xs:element name="CompositeCondition" type="CompositeCondition"/>
    </xs:choice>
    <xs:attribute name="Operator" type="LogicalOperator" use="required"/>
    <xs:attribute name="Negation" type="xs:boolean"/>
  </xs:complexType>
  <xs:complexType name="GlobalCondition">
    <xs:complexContent>
      <xs:extension base="Condition">
        <xs:attribute name="Id" type="xs:string" use="required"/>
      </xs:extension>
    </xs:complexContent>
  </xs:complexType>
  <xs:complexType name="Value">
    <xs:attribute name="Id" type="xs:integer" use="required"/>
    <xs:attribute name="Label" type="xs:string" use="required"/>
  </xs:complexType>
  <xs:complexType name="Values">
    <xs:sequence>
      <xs:element name="Value" type="Value" minOccurs="1" maxOccurs="unbounded"/>
    </xs:sequence>
  </xs:complexType>
  <xs:complexType name="Parameter">
    <xs:all>
      <xs:element name="Dependency" type="Condition" minOccurs="0"/>
      <xs:element name="Values" type="Values" minOccurs="0"/>
    </xs:all>
    <xs:attribute name="Name" type="xs:string" use="required"/>
    <xs:attribute name="Label" type="xs:string" use="required"/>
    <xs:attribute name="Type" type="ParameterType" use="required"/>
    <xs:attribute name="DefaultValue" type="xs:string"/>
    <xs:attribute name="Description" type="xs:string"/>
    <xs:attribute name="NamingRules" type="xs:string"/>
    <xs:attribute name="MinLength" type="xs:integer"/>
    <xs:attribute name="MaxLength" type="xs:integer"/>
    <xs:attribute name="MinValue" type="xs:integer"/>
    <xs:attribute name="MaxValue" type="xs:integer"/>
  </xs:complexType>
  <xs:complexType name="Parameters">
    <xs:sequence>
      <xs:element name="Parameter" type="Parameter" minOccurs="0" maxOccurs="unbounded"/>
    </xs:sequence>
  </xs:complexType>
  <xs:complexType name="ContentItem">
    <xs:attribute name="Id" type="xs:integer" use="required"/>
    <xs:attribute name="Type" type="xs:string" use="required"/>
    <xs:attribute name="Project" type="xs:string"/>
    <xs:attribute name="Library" type="xs:string" use="required"/>
    <xs:attribute name="Item" type="xs:string" use="required"/>
    <xs:attribute name="Anchor" type="xs:string"/>
  </xs:complexType>
  <xs:complexType name="ContentItems">
    <xs:sequence>
      <xs:element name="Content" type="ContentItem" minOccurs="0" maxOccurs="unbounded"/>
    </xs:sequence>
  </xs:complexType>
  <xs:complexType name="BoundParameter">
    <xs:attribute name="Name" type="xs:string" use="required"/>
    <xs:attribute name="TemplateParameter" type="xs:string"/>
    <xs:attribute name="Value" type="xs:string"/>
  </xs:complexType>
  <xs:complexType name="BoundParameters">
    <xs:sequence>
      <xs:element name="Parameter" type="BoundParameter" minOccurs="0" maxOccurs="unbounded"/>
    </xs:sequence>
  </xs:complexType>
  <xs:complexType name="LayoutContent">
    <xs:all>
      <xs:element name="Parameters" type="BoundParameters" minOccurs="0"/>
      <xs:element name="VisibleWhen" type="Condition" minOccurs="0"/>
    </xs:all>
    <xs:attribute name="ItemId" type="xs:integer" use="required"/>
    <xs:attribute name="ZIndex" type="xs:integer"/>
    <xs:attribute name="VisibleWhen" type="xs:string"/>
    <xs:attribute name="AnimationName" type="xs:string"/>
    <xs:attribute name="HorizontalAlignment" type="xs:string"/>
    <xs:attribute name="VerticalAlignment" type="xs:string"/>
  </xs:complexType>
  <xs:complexType name="Layout">
    <xs:sequence>
      <xs:element name="Content" type="LayoutContent" minOccurs="0" maxOccurs="unbounded"/>
    </xs:sequence>
    <xs:attribute name="Hotspot" type="xs:string"/>
  </xs:complexType>
  <xs:complexType name="Composition">
    <xs:sequence>
      <xs:element name="Prerequisite" type="Condition" minOccurs="0"/>
      <xs:element name="Layout" type="Layout">
        <xs:unique name="UniqueAnimationName">
          <xs:selector xpath=".//Content"/>
          <xs:field xpath="@AnimationName"/>
        </xs:unique>
      </xs:element>
    </xs:sequence>
    <xs:attribute name="Id" type="Guid" use="required"/>
  </xs:complexType>
  <xs:complexType name="Compositions">
    <xs:sequence>
      <xs:element name="Composition" type="Composition" minOccurs="1" maxOccurs="unbounded"/>
    </xs:sequence>
  </xs:complexType>
  <xs:complexType name="GlobalConditions">
    <xs:sequence>
      <xs:element name="GlobalCondition" type="GlobalCondition" minOccurs="0"
                  maxOccurs="unbounded"/>
    </xs:sequence>
  </xs:complexType>
  <xs:element name="VisualTemplate">
    <xs:complexType>
      <xs:all>
        <xs:element name="Parameters" type="Parameters" minOccurs="0"/>
        <xs:element name="ContentItems" type="ContentItems"/>
        <xs:element name="Compositions" type="Compositions"/>
        <xs:element name="GlobalConditions" type="GlobalConditions" minOccurs="0"/>
      </xs:all>
      <xs:attribute name="Guid" type="Guid" use="required"/>
      <xs:attribute name="Name" type="xs:string" use="required"/>
      <xs:attribute name="Thumbnail" type="xs:string"/>
      <xs:attribute name="Description" type="xs:string"/>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""

# ----------------------------------------------------------------------------- Include
#: the alarm priorities: (priority, unacknowledged colour, flag width, flag height). Each
#: flag is a genie of fxprio.ctm (the kind alarmind.flag_sizes measures, as CtDraw does) and a
#: symbol of fxprio.ctl of the same name and drawing (the alarm lists' symbols); the mode
#: flags are .ctl symbols only, so they never count. They are Include's: CtDraw reads only the
#: page's own project's almprior, so no FxMain indicator grows by them (the FxAlarms capture);
#: the runtime (the Simulate window) reads the include chain
PRIORITY_FLAGS = ((1, 0xFF0000, 20, 12), (2, 0xFF8000, 24, 14), (3, 0xFFFF00, 16, 10))
#: the six keyboard sequences fxparts.fx_inputs binds (keys.DBF lists them all)
GENIE_KEYS = ("LBUTTON_DN", "LBUTTON_UP", "LBUTTON_DBL", "LBUTTON_CMD_DN", "LBUTTON_CMD_UP", "RBUTTON_UP")


def build_include(b: Build) -> None:
    """Include: the composite stub genie, the default symbol set lights, a small symbol
    library, stand-in Cicode functions, named fonts, keys and alarm priorities."""
    os.makedirs(b.proj("Include"), exist_ok=True)
    b.table("Include", "include", [])
    # internal.ctm: the stub a one-item composite container holds (composer.STUB_*)
    d = b.element("Include", "internal.ctm", "_stub_reserved", "genie")
    d.create_object("rectangle_outline", (0, 0, 4, 4))
    d.save()
    # lights.ctl: the states a new symbol set starts with (symbolset.DEFAULT_STATES)
    for name, colour in (("light_1_black", 0x000000), ("light_1_red", 0xFF0000), ("light_1_green", 0x00FF00)):
        d = b.element("Include", "lights.ctl", name, "symbol")
        d.create_object("ellipse", (0, 0, 20, 20), colour=colour)
        d.save()
    # fxinc.ctl: symbols only the implicit include supplies
    d = b.element("Include", "fxinc.ctl", "fx_banner", "symbol")
    d.create_object("rectangle", (0, 0, 120, 24), colour=0x3060A0)
    d.create_object("text", (6, 4, 110, 20), text="FxBanner")
    d.save()
    d = b.element("Include", "fxinc.ctl", "fx_icon", "symbol")
    d.add_bitmap_object((0, 0, 16, 16), _rgba(16, 16, 0xFFCC00), 16, 16)
    d.save()
    _w(
        os.path.join(b.proj("Include"), "fxinc.ci"),
        _ci("""
// Stand-ins for functions every project can call (our own, no Plant SCADA code)
INT FUNCTION FxIncPing()
    RETURN 1;
END

STRING FUNCTION FxIncName(INT n)
    RETURN "Fx" + IntToStr(n);
END
"""),
    )
    b.table(
        "Include",
        "fonts",
        [
            {
                "NAME": "FxBig",
                "TYPE": "Arial",
                "SIZE": "18",
                "FGND_COLR": "BLACK",
                "BGND_COLR": "-1",
                "COMMENT": "a named font",
            },
            {"NAME": "FxSmall", "TYPE": "Arial", "SIZE": "10", "FGND_COLR": "BLUE", "BGND_COLR": "WHITE"},
            {
                "NAME": "FxSP",
                "TYPE": "Courier New",
                "SIZE": "14",
                "FGND_COLR": "LIGHT_RED",
                "BGND_COLR": "-1",
                "COMMENT": "the V3 / V4 objects' font",
            },
            # (the font a V3 / V4 Number or String names when it names none; the
            # install's own Include defines it so, and a page's save measures their Bounds in it)
            {
                "NAME": "DefaultFont",
                "TYPE": "Courier New",
                "SIZE": "14",
                "FGND_COLR": "WHITE",
                "BGND_COLR": "-1",
                "COMMENT": "the default V3 / V4 font",
            },
        ],
    )
    b.table(
        "Include",
        "keys",
        [
            {"NAME": "ENTER", "CODE": "KEY_ENTER", "COMMENT": "Enter key"},
            {"NAME": "ESC", "CODE": "KEY_ESC"},
            {"NAME": "F2", "CODE": "KEY_F2"},
            {"NAME": "LBUTTON_UP", "CODE": "KEY_LBUTTON_UP"},
            {"NAME": "LBUTTON_CMD_UP", "CODE": "KEY_LBTN_CMD_UP"},
        ]
        # (a code has at most 16 characters: the command cursor's are KEY_LBTN_CMD_*)
        + [
            {"NAME": k, "CODE": "KEY_" + k.replace("LBUTTON_CMD", "LBTN_CMD")}
            for k in GENIE_KEYS
            if k not in ("LBUTTON_UP", "LBUTTON_CMD_UP")
        ]
        + [
            {"NAME": "F3", "CODE": "KEY_F3"},
            {"NAME": "UP", "CODE": "KEY_UP"},
            {"NAME": "DOWN", "CODE": "KEY_DOWN"},
        ],
    )
    # the alarm priorities draw their flags from fxprio.ctm / .ctl (build_include_stand_ins)
    b.table(
        "Include",
        "almprior",
        [
            {
                "PRIORITY": str(p),
                "SHORTNAME": "P%d" % p,
                "LIBNAME": "fxprio",
                "LIBOBJNAME": "fx_p%d" % p,
                "THUMBNAIL": "fx_p%d_small" % p,
                "UNACKONFG": "0x%06x" % c,
                "COMMENT": "priority %d" % p,
            }
            for p, c, _w, _h in PRIORITY_FLAGS
        ],
    )
    b.table(
        "Include",
        "almmodes",
        [
            {
                "DISPNAME": "Fx Shelved",
                "SHORTNAME": "Shelved",
                "LIBNAME": "fxprio",
                "LIBOBJNAME": "fx_shelved",
            },
            {
                "DISPNAME": "Fx Disabled",
                "SHORTNAME": "Disabled",
                "LIBNAME": "fxprio",
                "LIBOBJNAME": "fx_disabled",
            },
        ],
    )


def build_system(b: Build) -> None:
    """System: empty tables and a genie form file."""
    os.makedirs(b.proj("System"), exist_ok=True)
    b.table("System", "include", [])
    b.table("System", "labels", [{"NAME": "FX_ON", "EXPR": "1", "COMMENT": "a label"}])
    b.table(
        "System",
        "contype",
        [{"NAME": "FxMaster", "COMMENT": "a master page"}, {"NAME": "FxFace", "COMMENT": "a faceplate"}],
    )
    b.table("System", "cluster", [{"NAME": "FxCluster", "COMMENT": "the only cluster"}])
    _w(
        os.path.join(b.proj("System"), "fxsys.frm"),
        _ci(r"""
! a genie form file of the System stand-in
FORM "fxsys_none", "Genie - nothing";
1: "Name", 16;
NORMAL
"Name {1       }"
"""),
    )


# ----------------------------------------------------------------------------- FxStyle
TEMPLATE_PROPS = [("PageCicodeOn", "1"), ("FxPageKind", "plain")]
TEMPLATE_ASSOCS = [("FxEquipment", "''"), ("FxTitle", "'Fixture'")]


def _template_art(d, w: int, h: int, title: bool) -> None:
    """A template's own art: a frame, a title bar (or none), a caption, a corner line."""
    d.create_object("rectangle_outline", (0, 0, w - 1, h - 1))
    if title:
        d.create_object("rectangle", (0, 0, w, 30), colour=0x204080)
        d.create_object("text", (8, 6, 200, 24), text="FxTitle")
    d.create_object("line", (10, h - 40, 10, h - 13))
    d.create_object("text", (w - 120, h - 30, w - 20, h - 14), text="tpl")
    d.create_object("rectangle", (w - 60, 40, w - 20, 60), colour=0x80C0FF)


#: the free DummyMarker of normal@XGA: its rect (in its class data) and so its extent
TPL_MARKER_RECT = (700, 100, 803, 119)


def _cleared_marker(d, rect) -> int:
    """A free DummyMarker as a template may carry one: no point in its three slots, its
    rect and hotspot in its class data (dwFlags 0x0A). Placed linked, CtDraw's page copy
    is the empty rect at the origin; unlinked, it takes the element's extent back
    (format/template_place)."""
    from ctgedit.format import records as R

    l, t, r, bt = rect
    po = R.to_po(
        R.record(
            0xFFFF,
            handle=d.next_handle(),
            anchor=(l, bt - 1),
            rect1=rect,
            rect2=rect,
            props=(),
            style=R.DEFAULT_STYLE,
            cls=R.dummy_part(rect, (l, bt - 1)),
        )
    )
    return d.add_object(po)


def build_style(b: Build) -> None:
    """FxStyle: fx_style.ctt with normal@XGA (title bar), normal@-XGA (none) and
    blank@SVGA (no background), with page properties and associations; fx_alt.ctt with
    no-title-bar templates only."""
    os.makedirs(b.proj("FxStyle"), exist_ok=True)
    b.table("FxStyle", "include", [{"NAME": "FxLib", "COMMENT": "the genies of the templates"}])
    b.project_cit("FxStyle")
    for name, (w, h), title, bg in (
        ("normal@XGA", (1024, 768), True, 0xD8D8D8),
        ("normal@-XGA", (1024, 768), False, 0xE0E0E0),
        ("blank@SVGA", (800, 600), False, None),
    ):
        # (blank@SVGA has no background: a new page takes the Page Defaults colour)
        d = b.element(
            "FxStyle",
            "fx_style.ctt",
            name,
            "template",
            page_size=(w, h),
            page_properties=TEMPLATE_PROPS,
            associations=TEMPLATE_ASSOCS,
            background=(bg, bg) if bg is not None else None,
        )
        if name.startswith("blank"):
            d.create_object("rectangle_outline", (0, 0, w - 1, h - 1))
        else:
            _template_art(d, w, h, title)
            # a nested genie whose text is a label (the page's link table lists '\Fx Menu')
            i = d.place_library_element(
                "fxgenie", "fx_value", "ctm", (w - 200, h - 70), params={"Description": "Fx Menu"}
            )
            _name(d, i, "FxTplMenu")
            if name == "normal@XGA":
                _cleared_marker(d, TPL_MARKER_RECT)
        d.save()
    # fx_alt.ctt: a second style, with no-title-bar variants only
    for name in ("pagecontent@-XGA", "blank@-XGA"):
        d = b.element(
            "FxStyle",
            "fx_alt.ctt",
            name,
            "template",
            page_size=(1024, 768),
            page_properties=[("FxPageKind", name.split("@")[0])],
        )
        d.create_object("rectangle_outline", (10, 10, 1013, 757))
        d.save()


# ----------------------------------------------------------------------------- FxLib
#: the nested genie chain (element, its own text, the genie it nests, its bindings)
NEST_CHAIN = (
    ("n0", "%Label%", None, {}),
    ("n1", "%Name%", "n0", {"Label": "%Name% L1"}),
    ("n2", "N2", "n1", {"Name": "%Name%"}),
    ("n3", "%Extra%", "n2", {"Name": "%Name% 3"}),
    ("n4", "N4", "n3", {"Name": "%Name%", "Extra": "E%Extra%"}),
)

FXGENIE_FRM = r"""
! the genie forms of fxgenie.ctm
FORM "fx_pump", "Genie - Fx pump";
1: "Tag", 32, readwrite, "Variable Tags", "NAME", "TYPE=DIGITAL";
2: "Label", 16;
NORMAL
"Tag    {1                 }
{LINE, 20}
Label  {2          }"

FORM "fx_pane", "Genie - Fx pane";
1: "Name", 40;
2: "DefaultPage", 32;
3: "IsDefault", 6, readwrite, "FxBoolean", "NAME";
4: "HasScrollbars", 6, readwrite, "FxBoolean", "NAME";
NORMAL
"Name           {1                          }
Default page   {2                 }
{LINE, 30}
Is default     {3   }   Scroll bars {4   }"
"""


def _symbol(b, lib, name, build):
    d = b.element("FxLib", lib, name, "symbol")
    build(d)
    d.save()
    return d


def build_lib(b: Build) -> None:
    """FxLib: fxsym.ctl (vector, group-root, bitmap, flashing bitmap, metafile and state
    symbols, a symbol set, a deleted element), fxgenie.ctm (parameterised genies), fxnest.ctm (the
    nested chain n0..n4), fxsuper.ctm (a super genie), the genie forms and a composite
    genie template."""
    os.makedirs(b.proj("FxLib"), exist_ok=True)
    b.table("FxLib", "include", [])
    lib = "fxsym.ctl"
    _symbol(b, lib, "fx_box", lambda d: d.create_object("rectangle", (0, 0, 40, 30), colour=0x3080C0))
    _symbol(b, lib, "fx_round", lambda d: d.create_object("ellipse", (0, 0, 30, 30), colour=0xC08030))

    def group(d):
        a = d.create_object("rectangle", (0, 0, 60, 40), colour=0x808080)
        t = d.create_object("text", (6, 12, 54, 28), text="Grp")
        d.group_objects([a, t])

    _symbol(b, lib, "fx_group", group)
    _symbol(
        b, lib, "fx_bitmap", lambda d: d.add_bitmap_object((0, 0, 14, 23), _rgba(14, 23, 0x40A040), 14, 23)
    )
    _symbol(
        b,
        lib,
        "fx_flash",
        lambda d: d.add_bitmap_object(
            (0, 0, 16, 16), _rgba(16, 16, 0xFF0000), 16, 16, flash_rgba=_rgba(16, 16, 0x400000)
        ),
    )
    from ctgedit.format import bitmaps

    wmf, ww, wh = bitmaps.wmf_from_file(_wmf(40, 20, 0x2060C0))
    _symbol(b, lib, "fx_wmf", lambda d: d.add_metafile_object((0, 0, ww, wh), wmf))
    for name, colour in (("fx_on", 0x00C000), ("fx_off", 0x606060), ("fx_fault", 0xFF0000)):
        _symbol(b, lib, name, lambda d, c=colour: d.create_object("ellipse", (0, 0, 16, 16), colour=c))
    _symbol(b, lib, "fx_pump_tall", lambda d: d.create_object("rectangle", (0, 0, 30, 80), colour=0x5050A0))
    _symbol(
        b,
        lib,
        "fx_set",
        lambda d: d.create_symbol_set(
            (0, 0), kind=0, exprs=["FX_PUMP1"], states=[("fxsym", "fx_off"), ("fxsym", "fx_on")]
        ),
    )
    _symbol(b, lib, "fx_gone", lambda d: d.create_object("rectangle", (0, 0, 10, 10)))
    from ctgedit.format import element as E

    E.delete_library_element(os.path.join(b.proj("FxLib"), lib), "fx_gone")

    # fxgenie.ctm
    g = "fxgenie.ctm"
    d = b.element("FxLib", g, "fx_pump", "genie")
    body = d.create_object("rectangle", (0, 0, 60, 40), colour=0xA0A0A0)
    d.ensure_property(body, "COLMODE", "0")
    d.ensure_property(body, "COL_A", "%Tag%")
    d.create_object("text", (4, 44, 80, 60), text="%Label%")
    val = d.create_object("number", (4, 62, 80, 78), text="####.#")
    d.ensure_property(val, "TXT_STR", "%Tag%")
    d.set_input_commands(body, touch={"CMD": "FxToggle(%Tag%)"})
    d.save()
    d = b.element("FxLib", g, "fx_valve", "genie")
    v = d.create_object("polygon", None, [(0, 0), (30, 15), (0, 30)], colour=0x40A0A0)
    d.ensure_property(v, "COLMODE", "0")
    d.ensure_property(v, "COL_A", "%TagPrefix%_ON")
    d.create_object("text", (0, 34, 60, 50), text="%TagPrefix%")
    d.save()
    d = b.element("FxLib", g, "fx_keys", "genie")
    k = d.create_object("text", (0, 0, 80, 16), text="%Tag%")
    d.set_input_commands(
        k,
        touch={"CMD": "FxSet(%Tag%, 1)"},
        keys=[{"KEY_SEQ": "ENTER", "CMD": "FxSet(%Tag%, 2)"}, {"KEY_SEQ": "ESC", "CMD": "FxSet(%Tag%, 0)"}],
    )
    d.save()
    d = b.element("FxLib", g, "fx_value", "genie")
    d.create_object("text", (0, 0, 120, 16), text="@(%Description%)")
    d.create_object("rectangle_outline", (0, 18, 120, 36))
    d.save()
    d = b.element("FxLib", g, "fx_plain", "genie")
    d.create_object("rectangle", (0, 0, 20, 20), colour=0x202020)
    d.create_object("ellipse", (24, 0, 44, 20), colour=0xE0E0E0)
    d.save()
    d = b.element("FxLib", g, "fx_pane", "genie")
    d.create_object("rectangle_outline", (0, 0, 200, 120))
    d.create_object("text", (4, 4, 196, 20), text="%Name%")
    d.create_object("text", (4, 24, 196, 40), text="%DefaultPage%")
    d.create_object("text", (4, 44, 196, 60), text="%IsDefault% %HasScrollbars%")
    d.save()
    # fx_lamp: a set of fxsym's bitmaps (a placed copy stores art-stripped tc 7 slots that
    # the reader grafts from the library) and its caption
    d = b.element("FxLib", g, "fx_lamp", "genie")
    d.create_symbol_set(
        (0, 0), kind=0, exprs=["%Tag%"], states=[("fxsym", "fx_bitmap"), ("fxsym", "fx_flash")]
    )
    d.create_object("text", (20, 0, 100, 16), text="%Tag%")
    d.save()
    # fx_bitmaps: two pairs of an invisible frame (no fill, line style 5) and an embedded
    # bitmap, direct members (placed linked, the page's slots are art-stripped)
    d = b.element("FxLib", g, "fx_bitmaps", "genie")
    for k, colour in enumerate((0x3080E0, 0xE08030)):
        f = d.create_object("rectangle_outline", (40 * k, 0, 40 * k + 32, 32))
        _null_pen(d, f)
        d.add_bitmap_object((40 * k + 4, 4, 40 * k + 28, 28), _rgba(24, 24, colour), 24, 24)
    d.save()
    _w(os.path.join(b.proj("FxLib"), "fxgenie.frm"), _ci(FXGENIE_FRM))

    # fxnest.ctm: n0 .. n4, each nesting the one before
    for name, own, inner, binds in NEST_CHAIN:
        d = b.element("FxLib", "fxnest.ctm", name, "genie", canvas=(300, 200))
        d.create_object("text", (10, 10, 120, 26), text=own)
        if inner:
            assert d.place_library_element("fxnest", inner, "ctm", (10, 40), params=binds) is not None
        d.save()

    # fxsuper.ctm: a super genie (a popup) and the genie that attaches it
    d = b.element(
        "FxLib", "fxsuper.ctm", "!fx_popup", "supergenie", page_size=(300, 200), associations=[("Tag", "''")]
    )
    btn = d.create_object("button", (20, 150, 120, 180), text="Close")
    d.set_input_commands(btn, touch={"CMD": "WinFree();"})
    d.create_object("text", (20, 20, 200, 36), text="?Tag?")
    d.save()
    d = b.element("FxLib", g, "fx_popper", "genie")
    d.create_object("button", (0, 0, 80, 24), text="Popup")
    d.set_attachments(["fxsuper.!fx_popup"])
    d.save()

    # the composite genie template (its content items are fxgenie genies)
    _w(os.path.join(b.proj("FxLib"), "Composite Genies", "fx_tank.xml"), _ci(FX_TANK_XML))


FX_TANK_GUID = "6B1F3C2A-8D4E-4F5A-9B6C-7D8E9F0A1B2C"
FX_TANK_XML = (
    """<?xml version="1.0" encoding="utf-8"?>
<VisualTemplate Guid="{%s}" Name="FxTank" Thumbnail="fx_tank">
  <Parameters>
    <Parameter Name="Equipment" Label="Equipment" Type="String" NamingRules="Equipment"
        MinLength="1" MaxLength="160" Description="The equipment the tank shows."/>
    <Parameter Name="Label" Label="Label" Type="String" DefaultValue="Tank"/>
    <Parameter Name="ShowValue" Label="Show value" Type="Bool" DefaultValue="true"/>
    <Parameter Name="Orientation" Label="Orientation" Type="String" DefaultValue="Left">
      <Values><Value Id="0" Label="Left"/><Value Id="1" Label="Right"/></Values>
    </Parameter>
  </Parameters>
  <ContentItems>
    <Content Id="1" Type="Genie" Project="FxLib" Library="fxgenie" Item="fx_plain"/>
    <Content Id="2" Type="Genie" Project="FxLib" Library="fxgenie" Item="fx_value"/>
  </ContentItems>
  <Compositions>
    <Composition Id="{6B1F3C2A-8D4E-4F5A-9B6C-7D8E9F0A1B2D}">
      <Layout>
        <Content ItemId="1" ZIndex="1"/>
        <Content ItemId="2" ZIndex="2" VisibleWhen="ShowIt">
          <Parameters><Parameter Name="Description" TemplateParameter="Label"/></Parameters>
        </Content>
      </Layout>
    </Composition>
  </Compositions>
  <GlobalConditions>
    <GlobalCondition Id="ShowIt">
      <ParameterCondition Name="ShowValue" Value="true"/></GlobalCondition>
  </GlobalConditions>
</VisualTemplate>"""
    % FX_TANK_GUID
)


# ----------------------------------------------------------------------------- tables
#: FxMain's tags: (name, type, equipment, item, format, comment)
FX_TAGS = [
    ("FX_PUMP%d" % i, "DIGITAL", "FxPlant.Pump%d" % i, "Run", "", "pump %d running" % i) for i in range(1, 4)
]
FX_TAGS += [
    ("FX_V%d_ON" % i, "DIGITAL", "FxPlant.Valve%d" % i, "Open", "", "valve %d" % i) for i in range(1, 9)
]
FX_TAGS += [
    ("FX_LEVEL%d" % i, "REAL", "FxPlant.Tank%d" % i, "Level", "###.#", "level %d" % i) for i in range(1, 4)
]
FX_TAGS += [
    ("FX_TEMP", "INT", "FxPlant.Tank1", "Temp", "####", "temperature"),
    ("FX_NAME", "STRING", "", "", "", "a text tag"),
    ("FX_SPEED", "REAL", "FxPlant.Pump1", "Speed", "##.##", "speed"),
    ("FX_MODE", "INT", "FxPlant", "Mode", "#", "plant mode"),
]


def _tag_rows(tags, unit="FxDev"):
    return [
        {
            "NAME": n,
            "TYPE": t,
            "CLUSTER": "FxCluster",
            "UNIT": unit,
            "ADDR": "%s%d" % ("D" if t == "DIGITAL" else "R", i + 1),
            "RAW_ZERO": "0",
            "RAW_FULL": "100" if t != "DIGITAL" else "",
            "ENG_ZERO": "0",
            "ENG_FULL": "100" if t != "DIGITAL" else "",
            "FORMAT": f,
            "COMMENT": c,
            "EQUIP": e,
            "ITEM": it,
        }
        for i, (n, t, e, it, f, c) in enumerate(tags)
    ]


def _equip_rows(names):
    return [
        {
            "NAME": n,
            "TYPE": n.split(".")[-1].rstrip("0123456789") or "Plant",
            "COMMENT": "equipment %s" % n,
            "CLUSTER": "FxCluster",
            "AREA": "1",
            "SCHEDULED": "TRUE" if i % 2 else "FALSE",
            "CONTENT": "FxFace",
        }
        for i, n in enumerate(names)
    ]


def project_tables(b: Build, project: str, tags, equipment) -> None:
    """The project tables a page project reads (tags, equipment, trends, alarms, labels,
    languages, devices, menus) besides the page register's."""
    b.table(project, "variable", _tag_rows(tags))
    b.table(
        project,
        "locvar",
        [
            {
                "NAME": "fx_local_count",
                "TYPE": "INT",
                "ZERO": "0",
                "FULL": "100",
                "COMMENT": "a local variable",
                "ASIZE": "1",
            },
            {"NAME": "fx_local_text", "TYPE": "STRING", "COMMENT": "a local text"},
        ],
    )
    b.table(project, "equip", _equip_rows(equipment))
    b.table(
        project,
        "eqref",
        [{"EQUIP": equipment[1], "REFEQUIP": equipment[2], "REFCAT": "FxFeeds", "COMMENT": "a reference"}],
    )
    reals = [t for t in tags if t[1] == "REAL"]
    b.table(
        project,
        "trend",
        [
            {
                "NAME": "FX_TR_%s" % n,
                "EXPR": n,
                "SAMPLEPER": "00:00:01",
                "ENG_ZERO": "0",
                "ENG_FULL": "100",
                "TYPE": "TRN_PERIODIC",
                "CLUSTER": "FxCluster",
                "COMMENT": "trend of %s" % n,
            }
            for (n, _t, _e, _i, _f, _c) in reals[:3]
        ],
    )
    digital = [t for t in tags if t[1] == "DIGITAL"]
    b.table(
        project,
        "digalm",
        [
            {
                "TAG": "FX_DA_%d" % (i + 1),
                "NAME": "Alarm %s" % n,
                "DESC": "%s stopped" % n,
                "VAR_A": n,
                "CATEGORY": str(1 + i % 3),
                "CLUSTER": "FxCluster",
                "EQUIP": e,
                "ITEM": "Alarm",
            }
            for i, (n, _t, e, _it, _f, _c) in enumerate(digital[:4])
        ],
    )
    b.table(
        project,
        "anaalm",
        [
            {
                "TAG": "FX_AA_%d" % (i + 1),
                "NAME": "High %s" % n,
                "COMMENT": "%s is high" % n,
                "VAR": n,
                "HIGH": "80",
                "HIGHHIGH": "95",
                "LOW": "10",
                "LOWLOW": "5",
                "CATEGORY": "2",
                "CLUSTER": "FxCluster",
                "EQUIP": e,
                "ITEM": "LevelAlarm",
            }
            for i, (n, _t, e, _it, _f, _c) in enumerate(reals[:3])
        ],
    )
    b.table(
        project,
        "advalm",
        [
            {
                "TAG": "FX_ADV_1",
                "NAME": "Two running",
                "DESC": "the first two run",
                "EXPR": "%s AND %s" % (digital[0][0], digital[1][0]),
                "CATEGORY": "3",
                "CLUSTER": "FxCluster",
            }
        ],
    )
    b.table(
        project,
        "category",
        [{"CATEGORY": str(c), "PRIORITY": str(c), "COMMENT": "Category %d" % c} for c in (1, 2, 3)],
    )
    b.table(
        project,
        "labels",
        [{"NAME": "FX_TRUE", "EXPR": "1", "COMMENT": "true"}, {"NAME": "FX_FALSE", "EXPR": "0"}],
    )
    b.table(
        project,
        "devices",
        [
            {
                "NAME": "FxLog",
                "FORMAT": "{Time,12} {MsgLog,40}",
                "TYPE": "ASCII_DEV",
                "FILES": "1",
                "COMMENT": "logs messages",
            },
            {"NAME": "FxPrinter", "FORMAT": "{Time,12} {Name,20}", "TYPE": "PRINTER_DEV"},
        ],
    )
    b.table(
        project,
        "lang",
        [
            {"LANGUAGE": "Fxlandic(Fxland)", "SRC_FILE": "fxlandic.dbf", "COMMENT": "a language"},
            {"LANGUAGE": "Fxish(Fxland)", "SRC_FILE": "fxish.dbf"},
        ],
        file="LANG",
    )
    b.table(
        project,
        "langtable",
        [{"NATIVE": "Fx Start", "LOCAL": "Fx Anfang"}, {"NATIVE": "Fx Stop", "LOCAL": "Fx Halt"}],
        file="fxlandic",
    )
    b.table(project, "langtable", [{"NATIVE": "Fx Start", "LOCAL": "Fx Begin"}], file="fxish")
    # the Menu Configuration (pagemenu): PAGE = the page the menu is on (blank: every page,
    # as Example's own menu), LEVEL1 .. LEVEL6 = the item's path in that menu
    b.table(
        project,
        "pagemenu",
        [
            {
                "PAGE": "",
                "LEVEL1": "FxPlant",
                "LEVEL2": "Shapes",
                "COMMAND": 'PageDisplay("FxShapes")',
                "ORDER": "1",
            },
            {
                "PAGE": "",
                "LEVEL1": "FxPlant",
                "LEVEL2": "Genies",
                "COMMAND": 'PageDisplay("FxGenies")',
                "ORDER": "2",
            },
        ],
    )


# ----------------------------------------------------------------------------- FxMain
FX_CI = """
// FxMain's own Cicode (stand-ins the pages call)
INT FUNCTION FxToggle(INT tag)
    RETURN NOT tag;
END

FUNCTION FxSet(INT tag, INT value)
    tag = value;
END

INT FUNCTION FxExpanded()
    RETURN 0;
END
"""

#: the templates the template pages use
TPL_NORMAL = "fx_style.normal@XGA.<ctt"
TPL_NOTITLE = "fx_style.normal@-XGA.<ctt"
TPL_BLANK = "fx_style.blank@SVGA.<ctt"


def _adder(doc):
    def add(name, kind, rect=None, nodes=None, **kw):
        return _name(doc, doc.create_object(kind, rect, nodes, **kw), name)

    return add


def _keys(doc, i, pairs):
    for k, v in pairs:
        assert doc.ensure_property(i, k, v), (k, v)
    return i


def _shapes(doc) -> None:
    """One of each primitive object, named after its kind."""
    add = _adder(doc)
    add("FxLine", "line", (20, 20, 200, 20))
    add("FxRect", "rectangle", (20, 40, 120, 100), colour=0x3366CC)
    add("FxRectOutline", "rectangle_outline", (140, 40, 240, 100))
    add("FxEllipse", "ellipse", (260, 40, 340, 100), colour=0xCC6633)
    add("FxEllipseOutline", "ellipse_outline", (360, 40, 440, 100))
    add("FxPolygon", "polygon", None, [(20, 120), (100, 120), (60, 180)], colour=0x33CC66)
    add("FxPolyline", "polyline", None, [(120, 120), (160, 180), (200, 120), (240, 180)])
    add("FxPipe", "pipe", None, [(260, 120), (340, 120), (340, 180), (420, 180)])
    add("FxText", "text", (20, 200, 200, 216), text="Fx text")
    add("FxMultiLine", "text", (220, 200, 400, 232), text="Line one\r\nLine two")
    add("FxNumber", "number", (20, 240, 120, 256), text="####.#")
    add("FxButton", "button", (140, 240, 240, 270), text="Fx button")
    # code page 1252 letters beyond ASCII (a bullet 0x95, AE 0xC6, o slash 0xF8, degree 0xB0)
    add("FxCp1252", "text", (260, 240, 440, 256), text="Fx \u2022 \u00c6r\u00f8 25\u00b0")
    a = add("FxGroupA", "rectangle", (460, 40, 520, 80), colour=0x999999)
    c = add("FxGroupB", "ellipse", (530, 40, 570, 80), colour=0x666666)
    inner = _name(doc, doc.group_objects([a, c]), "FxInnerGroup")
    t = add("FxGroupText", "text", (460, 90, 570, 106), text="grouped")
    _name(doc, doc.group_objects([inner, t]), "FxOuterGroup")
    i = add("FxRaised", "rectangle", (20, 290, 100, 330), colour=0xC0C0C0)
    doc.set_effect(i, 1, 2)
    i = add("FxLowered", "rectangle", (110, 290, 190, 330), colour=0xC0C0C0)
    doc.set_effect(i, 2, 2)
    i = add("FxShadowed", "rectangle_outline", (200, 290, 280, 330))
    doc.set_effect(i, 3, 3)
    i = add("FxGradient", "rectangle", (290, 290, 370, 330), colour=0x2040A0)
    doc.set_gradient(i, 3, 0xFFFFFF)
    i = add("FxPie", "ellipse", (380, 290, 440, 350), colour=0xA02040)
    doc.set_ellipse(i, style=1, start=0, end=180)
    i = doc.add_bitmap_object((460, 120, 492, 152), _rgba(32, 32, 0x2080FF), 32, 32)
    _name(doc, i, "FxBitmap")
    i = doc.add_bitmap_object(
        (500, 120, 516, 136), _rgba(16, 16, 0xFF2020), 16, 16, flash_rgba=_rgba(16, 16, 0x202020)
    )
    _name(doc, i, "FxFlashBitmap")
    from ctgedit.format import bitmaps

    wmf, ww, wh = bitmaps.wmf_from_file(_wmf(60, 30, 0x30A030))
    _name(doc, doc.add_metafile_object((520, 160, 520 + ww, 160 + wh), wmf), "FxMetafile")


def _legacy_objects(doc) -> None:
    """The V3/V4 library-class objects, input records, web content and two synthetic
    ActiveX controls."""
    from ctgedit.format import records as R

    add = _adder(doc)
    i = add("FxTrend", "trend", (20, 20, 320, 160))
    doc.set_trend_properties(i, pens=[("FX_LEVEL1", None), ("FX_LEVEL2", None)])
    _keys(doc, add("FxString", "string", (20, 180, 140, 196)), [("EXPR", "FX_PUMP1")])
    i = add("FxCicode", "cicode_object", (160, 180, 180, 200))
    doc.set_cicode_object(i, command="FxToggle(FX_PUMP1)")
    _keys(doc, add("FxAnimSymbol", "animated_symbol", (200, 180, 230, 210)), [("EXPR", "FX_MODE")])
    _keys(doc, add("FxBar", "bar_graph", (260, 180, 290, 260)), [("EXPR", "FX_LEVEL1")])
    # (THUMB '': the app's new slider names the Include pointer, a library the fake
    # install does not have)
    _keys(doc, add("FxSlider", "slider", (320, 180, 340, 280)), [("TAG", "FX_SPEED"), ("THUMB", "")])
    _keys(doc, add("FxDigitalText", "digital_text", (360, 180, 460, 196)), [("VAR_A", "FX_PUMP2")])
    _keys(doc, add("FxColourFlood", "color_flood", (480, 180, 560, 220)), [("VAR_A", "FX_PUMP3")])
    i = add("FxWeb", "web_content", (20, 300, 320, 460))
    doc.set_web_content(i, url="about:blank", enable_print=True)
    t = add("FxTouchRect", "rectangle", (360, 300, 440, 340), colour=0x808040)
    doc.set_input_commands(t, touch={"CMD": "FxToggle(FX_PUMP1)"})
    k = add("FxKeyText", "text", (360, 360, 460, 376), text="##.#")
    doc.set_input_commands(k, keys=[{"KEY_SEQ": "ENTER", "CMD": "FxSet(FX_MODE, 1)"}])
    bt = add("FxCmdButton", "button", (480, 300, 580, 330), text="Run")
    doc.set_input_commands(bt, touch={"CMD": 'PageDisplay("FxShapes")'})
    # (controls of the made-up CLSID, registered nowhere: CtDraw cannot create them, so its
    # save keeps their records as they are. A real control would come back from CtDraw's
    # save in its own storage, which the tests cannot reproduce)
    for n, (x, name) in enumerate(((600, "FxGaugeA"), (720, "FxGaugeB"))):
        po = R.new_record(
            "activex",
            handle=doc.next_handle(),
            rect1=(x, 20, x + 100, 120),
            props=[("ACTIVEXEXT_OBJNAME", name)],
            properties=[("Value", str(n)), ("Caption", name)],
        )
        _name(doc, doc.add_object(po), name)


def _null_pen(doc, index: int) -> None:
    """The pen style PS_NULL (5: no line), which the Line Style menu (0 to 4) cannot set."""
    from ctgedit.format import geometry as geom

    assert doc._body_edit(
        "line style", index, lambda po: geom.set_colour_block_flag(po, geom.FLAG_LINE_STYLE, 5)
    )


def _alarm_on(
    doc, group: int, expr: str, width: int = 3, padding: int = 5, inside: bool = False, flag_on: bool = True
) -> None:
    """Turn on the alarm indicator of ``group`` as the Alarm Indicator page's Apply does
    (doc.set_alarm_indicator takes the page's values: AI_ON 1, the Bounds grown)."""
    assert doc.set_alarm_indicator(
        group,
        {"has": True, "expr": expr, "width": width, "padding": padding, "inside": inside, "flag_on": flag_on},
    )


def _animated(doc) -> None:
    """Objects with every kind of animation key, metadata, an alarm indicator and the
    text display types."""
    add = _adder(doc)
    _keys(
        doc,
        add("FxColA", "rectangle", (20, 20, 80, 60), colour=0x808080),
        [("COLMODE", "0"), ("COL_A", "FX_PUMP1")],
    )
    _keys(
        doc,
        add("FxColSta", "rectangle", (100, 20, 160, 60), colour=0x808080),
        [("COLMODE", "1"), ("COL_A", "FX_PUMP1"), ("COL_B", "FX_PUMP2")],
    )
    _keys(
        doc,
        add("FxColArr", "ellipse", (180, 20, 240, 60), colour=0x808080),
        [("COLMODE", "2"), ("COL_ARR", "FX_MODE")],
    )
    _keys(
        doc,
        add("FxFill", "rectangle", (260, 20, 300, 120), colour=0x2060C0),
        [("FIL", "FX_LEVEL1"), ("FILMINPC", "0"), ("FILMAXPC", "100")],
    )
    _keys(
        doc,
        add("FxMove", "rectangle", (320, 20, 360, 60), colour=0x60C020),
        [
            ("MVX", "FX_LEVEL1"),
            ("MVXMINPT", "0"),
            ("MVXMAXPT", "200"),
            ("MVY", "FX_LEVEL2"),
            ("MVYMINPT", "0"),
            ("MVYMAXPT", "100"),
        ],
    )
    _keys(
        doc,
        add("FxSize", "rectangle", (380, 20, 420, 60), colour=0xC06020),
        [("SZX", "FX_LEVEL1"), ("SZXMINPC", "0"), ("SZXMAXPC", "400"), ("SZXORG", "-1")],
    )
    _keys(
        doc,
        add("FxRotate", "line", (460, 60, 460, 20)),
        [("ROT", "FX_SPEED"), ("ROTMINPC", "0"), ("ROTMAXPC", "360"), ("ROTYORG", "1")],
    )
    _keys(doc, add("FxVisible", "text", (20, 80, 120, 96), text="shown"), [("VISIBLE", "FX_PUMP1")])
    _keys(doc, add("FxHidden", "text", (20, 100, 120, 116), text="Login..."), [("VISIBLE", "FxExpanded()")])
    _keys(
        doc,
        add("FxDisabled", "button", (140, 80, 240, 110), text="Disabled"),
        [("DISABLED", "FX_MODE = 0"), ("DISMODE", "1")],
    )
    _keys(
        doc,
        add("FxSliderKeys", "rectangle", (260, 140, 290, 170), colour=0x404040),
        [("MVXSL", "FX_SPEED"), ("MVX", "FX_SPEED"), ("MVXMINPT", "0"), ("MVXMAXPT", "300")],
    )
    _keys(
        doc,
        add("FxAccess", "rectangle", (300, 140, 340, 170), colour=0x404040),
        [("COMMENT", "@(Fx Start)"), ("DESC", "a described object"), ("AREA", "1")],
    )
    _keys(
        doc,
        add("FxVertex", "polygon", None, [(360, 140), (420, 140), (390, 190)]),
        [("VERTEX_VERTEXP1", "FX_LEVEL1")],
    )
    # the text displays: TXTMODE 0 on / off, 1 multi-state, 2 array, 3 numeric, 4 string
    _keys(
        doc,
        add("FxOnOff", "text", (20, 200, 120, 216), text="Stopped"),
        [("TXTMODE", "0"), ("TXT_A", "FX_PUMP1"), ("TXT_ON", "Running"), ("TXT_OFF", "Stopped")],
    )
    _keys(
        doc,
        add("FxMulti", "text", (140, 200, 240, 216), text="S"),
        [
            ("TXTMODE", "1"),
            ("TXT_A", "FX_PUMP1"),
            ("TXT_B", "FX_PUMP2"),
            ("STATE_0", "Idle"),
            ("STATE_1", "One"),
            ("STATE_2", "Two"),
            ("STATE_3", "Both"),
        ],
    )
    _keys(
        doc,
        add("FxArray", "text", (260, 200, 360, 216), text="A"),
        [("TXTMODE", "2"), ("SET_ARR", "FX_MODE"), ("ARRAY_0", "Auto"), ("ARRAY_1", "Manual")],
    )
    _keys(
        doc,
        add("FxNumeric", "number", (380, 200, 480, 216), text="###.#"),
        [("TXTMODE", "3"), ("TXT_STR", "FX_LEVEL1:###.#")],
    )
    _keys(
        doc,
        add("FxStringDisplay", "text", (500, 200, 600, 216), text="text"),
        [("TXTMODE", "4"), ("TXT_STR", "FX_NAME")],
    )
    add("FxLabelText", "text", (20, 240, 300, 256), text="@(Fx Start) @(Fx Stop) @(Fx Mode)")
    m = add("FxMeta", "rectangle", (320, 240, 380, 280), colour=0x707070)
    doc.set_metadata(m, [("Equipment", "FxPlant.Tank1"), ("Role", "level")])
    a = add("FxAlarmA", "rectangle", (400, 240, 440, 280), colour=0x7070A0)
    t = add("FxAlarmB", "text", (400, 284, 480, 300), text="Tank1")
    grp = _name(doc, doc.group_objects([a, t]), "FxAlarmGroup")
    _alarm_on(doc, grp, "FxPlant.Tank1", width=2)
    # a second alarm indicator (a status light: inside, no flag)
    a = add("FxAlarmC", "rectangle", (500, 240, 540, 280), colour=0x70A070)
    t = add("FxAlarmD", "text", (500, 284, 580, 300), text="Tank2")
    grp = _name(doc, doc.group_objects([a, t]), "FxAlarmGroup2")
    _alarm_on(doc, grp, "FxPlant.Tank2", width=15, inside=True, flag_on=False)
    # two on / off texts with a multi-state colour (COLMODE 1): TXTMODE before COLMODE in
    # the value bag (FxTxtFirst) and after it (FxColFirst)
    multi = [("COLMODE", "1"), ("COL_A", "FX_PUMP1"), ("COL_B", "FX_PUMP2")]
    t = add("FxTxtFirst", "text", (600, 240, 700, 256), text="Off")
    doc.set_text_display(t, [("TXTMODE", "0"), ("TXT_A", "FX_PUMP1"), ("TXT_ON", "On"), ("TXT_OFF", "Off")])
    doc.set_fill_keys(t, multi)
    t = add("FxColFirst", "text", (600, 270, 700, 286), text="Off")
    doc.set_fill_keys(t, multi)
    doc.set_text_display(t, [("TXTMODE", "0"), ("TXT_A", "FX_PUMP2"), ("TXT_ON", "On"), ("TXT_OFF", "Off")])


def build_main(b: Build) -> None:
    """FxMain: a page per object kind, genies, symbols, template pages, a super genie
    page, a composite, old page forms and the project tables."""
    from ctgedit.editing import EditableDocument, libraries_changed

    main = b.proj("FxMain")
    os.makedirs(main, exist_ok=True)
    b.table(
        "FxMain",
        "include",
        [{"NAME": "FxLib", "COMMENT": "symbols and genies"}, {"NAME": "FxStyle", "COMMENT": "templates"}],
    )
    b.page_tables("FxMain")
    b.project_cit("FxMain")
    project_tables(
        b,
        "FxMain",
        FX_TAGS + FX_T_TAGS,
        [
            "FxPlant",
            "FxPlant.Pump1",
            "FxPlant.Pump2",
            "FxPlant.Pump3",
            "FxPlant.Tank1",
            "FxPlant.Tank2",
            "FxPlant.Tank3",
        ]
        + ["FxPlant.Valve%d" % i for i in range(1, 9)]
        + ["FxPlant.T%d" % n for n in range(201, 210)],
    )
    _w(os.path.join(main, "fxmain.ci"), _ci(FX_CI))
    shutil.copyfile(os.path.join(b.root, "Bin", "citect.pal"), os.path.join(main, "citect.pal"))

    doc = b.blank_page("FxMain", "FxShapes")
    _shapes(doc)
    doc.save()

    doc = b.blank_page("FxMain", "FxLegacy")
    _legacy_objects(doc)
    doc.save()

    doc = b.blank_page("FxMain", "FxAnim")
    _animated(doc)
    doc.save()
    doc = EditableDocument.open(doc.path)
    doc.set_page_keyboard_commands(
        [
            {"KEY_SEQ": "F2", "CMD": 'PageDisplay("FxShapes")'},
            {"KEY_SEQ": "LBUTTON_UP", "CMD": "FxSet(FX_MODE, 0)"},
        ]
    )
    doc.set_page_record(
        {
            "TITLE": "@(Fx Start)",
            "COMMENT": "every animation key",
            "AREA": "1",
            "SCANTIME": "500",
            "ENTRY_FUNC": "FxSet(FX_MODE, 1)",
        }
    )
    doc.save()

    doc = b.blank_page("FxMain", "FxSymbols")
    _name(
        doc, doc.create_object("symbol", (20, 20, 60, 50), symbol=("fxsym", "fx_box", "ctl")), "FxLinkedBox"
    )
    _name(doc, doc.place_library_element("fxsym", "fx_round", "ctl", (80, 20)), "FxPlacedRound")
    _name(doc, doc.place_library_element("fxsym", "fx_box", "ctl", (140, 20), linked=False), "FxEmbeddedBox")
    _name(doc, doc.place_library_element("fxsym", "fx_group", "ctl", (200, 20)), "FxGroupSymbol")
    i = _name(doc, doc.place_library_element("fxsym", "fx_bitmap", "ctl", (280, 20)), "FxBitmapSymbol")
    doc.rotate_objects([i])
    _name(doc, doc.place_library_element("fxsym", "fx_flash", "ctl", (320, 20)), "FxFlashSymbol")
    _name(doc, doc.place_library_element("fxsym", "fx_wmf", "ctl", (360, 20)), "FxWmfSymbol")
    _name(
        doc,
        doc.create_symbol_set(
            (20, 100), kind=0, exprs=["FX_PUMP1"], states=[("fxsym", "fx_off"), ("fxsym", "fx_on")]
        ),
        "FxOnOffSet",
    )
    _name(
        doc,
        doc.create_symbol_set(
            (80, 100),
            kind=1,
            exprs=["FX_PUMP1", "FX_PUMP2"],
            states=[("fxsym", "fx_off"), ("fxsym", "fx_on"), ("fxsym", "fx_fault"), ("fxsym", "fx_on")],
        ),
        "FxMultiSet",
    )
    _name(doc, doc.create_symbol_set((140, 100)), "FxDefaultSet")
    i = _name(doc, doc.place_library_element("fxsym", "fx_box", "ctl", (200, 100)), "FxTouchSymbol")
    doc.set_input_commands(i, touch={"CMD": "FxToggle(FX_PUMP2)"})
    _name(doc, doc.place_library_element("fxinc", "fx_banner", "ctl", (20, 160)), "FxIncludeBanner")
    doc.save()

    doc = b.blank_page("FxMain", "FxGenies", size=(1280, 800))
    for n in range(3):
        i = doc.place_library_element(
            "fxgenie",
            "fx_pump",
            "ctm",
            (20 + 100 * n, 20),
            params={"Tag": "FX_PUMP%d" % (n + 1), "Label": "Pump %d" % (n + 1)},
        )
        _name(doc, i, "FxPump%d" % (n + 1))
    for n in range(8):
        i = doc.place_library_element(
            "fxgenie",
            "fx_valve",
            "ctm",
            (20 + 80 * (n % 4), 140 + 70 * (n // 4)),
            params={"TagPrefix": "FX_V%d" % (n + 1)},
        )
        _name(doc, i, "FxValve%d" % (n + 1))
    _name(
        doc,
        doc.place_library_element("fxgenie", "fx_keys", "ctm", (400, 20), params={"Tag": "FX_MODE"}),
        "FxKeys",
    )
    _name(
        doc,
        doc.place_library_element(
            "fxgenie", "fx_value", "ctm", (400, 60), params={"Description": "Fx Start"}
        ),
        "FxValue",
    )
    _name(doc, doc.place_library_element("fxgenie", "fx_plain", "ctm", (400, 120)), "FxPlain")
    _name(
        doc,
        doc.place_library_element("fxnest", "n4", "ctm", (600, 20), params={"Name": "Pump", "Extra": "X"}),
        "FxNested",
    )
    g = doc.place_library_element("fxgenie", "fx_plain", "ctm", (400, 180))
    r = doc.create_object("rectangle", (460, 180, 500, 200), colour=0x505050)
    _name(doc, doc.group_objects([g, r]), "FxGroupedGenie")
    _name(
        doc,
        doc.place_library_element(
            "fxgenie",
            "fx_pane",
            "ctm",
            (600, 200),
            params={"Name": "Main", "DefaultPage": "FxShapes", "IsDefault": "TRUE", "HasScrollbars": "FALSE"},
        ),
        "FxPane",
    )
    _name(doc, doc.place_library_element("fxgenie", "fx_popper", "ctm", (600, 360)), "FxPopper")
    _name(
        doc,
        doc.place_library_element("fxgenie", "fx_lamp", "ctm", (600, 420), params={"Tag": "FX_PUMP3"}),
        "FxLamp",
    )
    _name(doc, doc.place_library_element("fxgenie", "fx_bitmaps", "ctm", (800, 20)), "FxBitmapGenie")
    doc.save()

    # a genie whose library goes away: FxMissing keeps the unresolved references,
    # FxPlaceholder their placeholder records
    shutil.copyfile(os.path.join(b.proj("FxLib"), "fxgenie.ctm"), os.path.join(main, "fxgone.ctm"))
    d = b.element("FxMain", "fxgone.ctm", "fx_thin", "genie")  # a 1 px wide genie
    d.create_object("rectangle", (0, 0, 1, 40), colour=0x406080)
    d.save()
    libraries_changed()
    doc = b.blank_page("FxMain", "FxMissing")
    _name(
        doc,
        doc.place_library_element("fxgone", "fx_value", "ctm", (20, 20), params={"Description": "Fx Stop"}),
        "FxGoneValue",
    )
    _name(doc, doc.place_library_element("fxgone", "fx_plain", "ctm", (20, 80)), "FxGonePlain")
    _name(doc, doc.create_object("rectangle", (200, 20, 260, 60), colour=0x406080), "FxStays")
    _name(doc, doc.place_library_element("fxgone", "fx_thin", "ctm", (300, 20)), "FxGoneThin")
    doc.save()
    os.remove(os.path.join(main, "fxgone.ctm"))
    libraries_changed()
    doc = EditableDocument.open(os.path.join(main, "FxMissing.ctg"))
    doc._creating = True
    doc.placeholders_for_missing_libraries()
    doc.save(os.path.join(main, "FxPlaceholder.ctg"))

    # template pages
    doc = b.template_page("FxMain", "FxTplLinked", TPL_NORMAL)
    _name(doc, doc.create_object("rectangle", (100, 200, 300, 260), colour=0x408040), "FxOwnRect")
    _name(
        doc,
        doc.place_library_element(
            "fxgenie", "fx_pump", "ctm", (400, 200), params={"Tag": "FX_PUMP1", "Label": "Tpl pump"}
        ),
        "FxTplPump",
    )
    doc.save()
    doc = b.template_page("FxMain", "FxTplUnlinked", TPL_NORMAL, linked=False)
    doc.create_object("text", (100, 200, 300, 216), text="unlinked")
    doc.save()
    doc = b.template_page("FxMain", "FxTplNoTitle", TPL_NOTITLE)
    doc.create_object("ellipse", (100, 200, 160, 260), colour=0x804040)
    doc.save()
    doc = b.template_page("FxMain", "FxTplBlank", TPL_BLANK)
    doc.create_object("text", (40, 40, 200, 56), text="blank")
    doc.save()

    # a composite genie (its composition cache goes to FxMain\_CompositionCaches)
    doc = b.blank_page("FxMain", "FxComposite")
    doc.paste_composite_genie(FX_TANK_GUID, (40, 40), {"Equipment": "FxPlant.Tank1", "Label": "Tank one"})
    doc.save()

    # a '!' system page and a bigger page
    doc = b.blank_page("FxMain", "!FxSystem", size=(400, 300))
    i = doc.create_object("number", (20, 20, 200, 36), text="##.#")
    doc.ensure_property(i, "TXT_STR", "FX_LEVEL1")
    doc.save()
    doc = b.blank_page("FxMain", "FxBig", size=(1920, 1080))
    for n in range(120):
        x, y = 20 + 75 * (n % 24), 20 + 180 * (n // 24)
        kind = ("rectangle", "ellipse", "text", "line")[n % 4]
        if kind == "text":
            doc.create_object("text", (x, y, x + 60, y + 16), text="T%d" % n)
        elif kind == "line":
            doc.create_object("line", (x, y, x + 60, y + 40))
        else:
            doc.create_object(kind, (x, y, x + 60, y + 40), colour=(n * 2654435761) & 0xFFFFFF)
    for n in range(4):
        doc.place_library_element(
            "fxgenie", "fx_pump", "ctm", (100 + 400 * n, 960), params={"Tag": "FX_PUMP1", "Label": "B%d" % n}
        )
    doc.save()

    # old page forms: FxOld9 / FxOld10 / FxOld11 (word2 9, 10, 11) of one page
    src = b.blank_page("FxMain", "FxOldSource", register=False)
    src.create_object("rectangle", (20, 20, 120, 80), colour=0x336699)
    src.create_object("ellipse", (140, 20, 220, 80), colour=0x993366)
    src.create_object("button", (20, 100, 120, 130), text="Old")
    src.create_object("text", (140, 100, 260, 116), text="old form")
    src.create_object("symbol", (280, 20, 320, 50), symbol=("fxsym", "fx_box", "ctl"))
    src.place_library_element("fxgenie", "fx_plain", "ctm", (280, 100))
    data = src.serialize()
    os.remove(src.path)
    for word2, name in ((9, "FxOld9"), (10, "FxOld10"), (11, "FxOld11")):
        _w(os.path.join(main, name + ".ctg"), downgrade(data, word2))
    libraries_changed()


def build_other(b: Build) -> None:
    """FxOther: a second project with a page named like one of FxMain's and its own
    fxsym.ctl (other elements than FxLib's)."""
    os.makedirs(b.proj("FxOther"), exist_ok=True)
    # (FxOther includes FxMain: each has a page FxShapes, so each sees the other's)
    b.table("FxOther", "include", [{"NAME": "FxLib"}, {"NAME": "FxMain"}])
    b.page_tables("FxOther")
    d = b.element("FxOther", "fxsym.ctl", "fx_other_only", "symbol")
    d.create_object("rectangle", (0, 0, 20, 20), colour=0x00A0A0)
    d.save()
    doc = b.blank_page("FxOther", "FxShapes", size=(800, 600))
    doc.create_object("text", (20, 20, 200, 36), text="FxOther shapes")
    doc.create_object("symbol", (20, 60, 40, 80), symbol=("fxsym", "fx_other_only", "ctl"))
    doc.save()


# ----------------------------------------------------------------------------- FxSim
FXSIM_CI = """
// FxSim: the Cicode the simulator runs (our own)
INT FUNCTION FxSimStart()
    FX_SIM_RUN = 1;
    RETURN 1;
END

INT FUNCTION FxSimStop()
    FX_SIM_RUN = 0;
    RETURN 0;
END

REAL FUNCTION FxSimScaled(REAL value)
    RETURN value * 2.0;
END

FUNCTION FxSimTick()
    WHILE TRUE DO
        IF FX_SIM_RUN THEN
            FX_SIM_COUNT = FX_SIM_COUNT + 1;
        END
        Sleep(1);
    END
END
"""

FXSIM_TAGS = [
    ("FX_SIM_RUN", "DIGITAL", "FxSim.Line", "Run", "", "line running"),
    ("FX_SIM_STOP", "DIGITAL", "FxSim.Line", "Stop", "", "line stopped"),
    ("FX_SIM_COUNT", "LONG", "FxSim.Line", "Count", "######", "count"),
    ("FX_SIM_NAME", "STRING", "", "", "", "a text"),
]
FXSIM_TAGS += [
    ("FX_SIM_D%02d" % i, "DIGITAL", "FxSim.Unit%d" % (i % 4), "State%d" % i, "", "digital %d" % i)
    for i in range(1, 29)
]
FXSIM_TAGS += [
    ("FX_SIM_R%02d" % i, "REAL", "FxSim.Unit%d" % (i % 4), "Value%d" % i, "###.##", "real %d" % i)
    for i in range(1, 29)
]


def build_sim(b: Build) -> None:
    """FxSim: Cicode sources, 30 tags, alarms, equipment and three pages that call each
    other through PageDisplay (no compiled RDBs: the engine compiles the sources)."""
    os.makedirs(b.proj("FxSim"), exist_ok=True)
    b.table("FxSim", "include", [{"NAME": "FxStyle", "COMMENT": "templates for new pages"}])
    b.page_tables("FxSim")
    b.project_cit("FxSim", res="SVGA", title=True, background=0xE8E8E8)
    project_tables(b, "FxSim", FXSIM_TAGS, ["FxSim", "FxSim.Line"] + ["FxSim.Unit%d" % i for i in range(4)])
    _w(os.path.join(b.proj("FxSim"), "fxsim.ci"), _ci(FXSIM_CI))
    names = ("FxSim1", "FxSim2", "FxSim3")
    for k, name in enumerate(names):
        doc = b.blank_page("FxSim", name, size=(800, 600))
        nxt = names[(k + 1) % len(names)]
        bt = _name(doc, doc.create_object("button", (20, 20, 160, 50), text="To %s" % nxt), "FxNext")
        doc.set_input_commands(bt, touch={"CMD": 'PageDisplay("%s")' % nxt})
        v = _name(doc, doc.create_object("number", (20, 70, 160, 86), text="######"), "FxCount")
        doc.ensure_property(v, "TXT_STR", "FX_SIM_COUNT")
        r = _name(doc, doc.create_object("rectangle", (200, 20, 260, 80), colour=0x808080), "FxRunLamp")
        _keys(doc, r, [("COLMODE", "0"), ("COL_A", "FX_SIM_RUN")])
        t = _name(doc, doc.create_object("button", (20, 100, 160, 130), text="Start"), "FxStart")
        doc.set_input_commands(t, touch={"CMD": "FxSimStart()"})
        h = _name(doc, doc.create_object("text", (200, 100, 360, 116), text="running"), "FxRunning")
        doc.ensure_property(h, "VISIBLE", "FX_SIM_RUN")
        for j in range(4):
            q = doc.create_object("rectangle", (20 + 50 * j, 160, 60 + 50 * j, 200), colour=0x606060)
            _keys(doc, q, [("COLMODE", "0"), ("COL_A", "FX_SIM_D%02d" % (j + 1 + 4 * k))])
        doc.save()


# ============================================================================ more fixtures
# The second set. Each step adds to a project the first set made; new libraries and pages
# have names of their own, so the first set's files keep their content.


def _bitmap_symbol(b, project, lib, name, w, h, colour, flash=None, hotspot=None):
    d = b.element(project, lib, name, "symbol")
    d.add_bitmap_object(
        (0, 0, w, h), _rgba(w, h, colour), w, h, flash_rgba=_rgba(w, h, flash) if flash is not None else None
    )
    if hotspot is not None:
        d.set_hotspot(*hotspot)
    d.save()
    return d


def _vector_symbol(b, project, lib, name, build):
    d = b.element(project, lib, name, "symbol")
    build(d)
    d.save()
    return d


def _payload_with_handle(po, handle: int) -> bytes:
    """A record's payload with another handle (the u16 after the reserved word of its
    geometry header)."""
    from ctgedit.format import writer

    pl = bytearray(writer.object_payload(po))
    an_end = 0x0C + struct.unpack_from("<H", pl, 0x0A)[0]
    struct.pack_into("<H", pl, an_end + 2, handle & 0xFFFF)
    return bytes(pl)


def _rehandle(doc, handles) -> None:
    """Give the records of ``doc`` the handles ``handles`` (in record order)."""
    from ctgedit.format import records as R

    objs = list(doc.parsed.page.objects)
    assert len(objs) == len(handles), (len(objs), handles)
    doc._structural("handles", [R.to_po(_payload_with_handle(po, h)) for po, h in zip(objs, handles)])


def build_include_stand_ins(b: Build) -> None:
    """Include: stand-ins for the libraries the code names by default (cicode.default,
    the slider thumbs) and the ones the tests used from the real Include (icons,
    lights_square_small), the alarm priority flags (fxprio.ctm and fxprio.ctl, almprior.DBF)
    and a 16 x 16 icon library (fxicons16.ctl). Every picture is our own."""
    inc = "Include"
    # a Cicode object without a symbol draws cicode.default: the f(x) icon's 31 x 17
    # (its hot spot is the bottom left: the icon's extent about its point is
    # RECT2_DELTAS[0x1009] = (0, -16, 31, 1))
    _bitmap_symbol(b, inc, "cicode.ctl", "default", 31, 17, 0xE0E0FF, hotspot=(0, 16))
    _vector_symbol(
        b,
        inc,
        "cicode.ctl",
        "fx_cicode_box",
        lambda d: d.create_object("rectangle", (0, 0, 31, 17), colour=0xC0C0FF),
    )
    # the slider thumbs (factory.SLIDER_THUMB is thumbs.pointer2_e_r)
    for name, c in (("pointer2_e_r", 0xC00000), ("pointer2_e_grey", 0x808080), ("pointer_s_red", 0xFF4040)):
        _bitmap_symbol(b, inc, "thumbs.ctl", name, 22, 11, c)
    _bitmap_symbol(b, inc, "icons.ctl", "help1", 16, 16, 0x2060FF)
    _vector_symbol(
        b, inc, "icons.ctl", "note", lambda d: d.create_object("rectangle", (0, 0, 16, 20), colour=0xFFFFA0)
    )
    _vector_symbol(
        b, inc, "icons.ctl", "hand2", lambda d: d.create_object("ellipse", (0, 0, 16, 16), colour=0xE0A070)
    )
    for name, c in (("grey", 0x909090), ("red", 0xFF0000)):
        _vector_symbol(
            b,
            inc,
            "lights_square_small.ctl",
            name,
            lambda d, c=c: d.create_object("rectangle", (0, 0, 10, 10), colour=c),
        )
    # fxprio.ctl: a flag and a small thumbnail per priority, a shelved and a disabled flag.
    # Their COL_ARR pairs are the alarm states (sim_alarmlists.state_index: 1 unacknowledged
    # on, 2 unacknowledged off, 3 acknowledged on), each 0x00RRGGBB (on, off); the flag's
    # text shows the priority's SHORTNAME at run time (its TXT_STR reads 'Label').
    # fxprio.ctm: each priority's flag again as a genie: CtDraw grows an alarm indicator's
    # Bounds by the .ctm flag genies only, never by a .ctl symbol, and only by
    # those of the page's own project's almprior
    for p, colour, w, h in PRIORITY_FLAGS:

        def flag(d, p=p, colour=colour, w=w, h=h):
            r = d.create_object("rectangle", (0, 0, w, h), colour=colour)
            d.set_fill_keys(r, _state_pairs(colour))
            # (the keys as they are: a display edit would measure the box for its TXT_STR)
            t = _keys(
                d,
                d.create_object("text", (2, 0, w - 2, h), text=str(p)),
                [("TXTMODE", "4"), ("TXT_STR", "Label")],
            )
            d.set_fill_keys(
                t,
                [
                    ("COLMODE", "2"),
                    ("COL_ARR", "FX_MODE"),
                    ("COL_ARR_0", "0x00000000,0x00000000"),
                    ("COL_ARR_1", "0x00ffffff,0x00000000"),
                    ("COL_ARR_3", "0x00000000,0x00000000"),
                ],
            )

        _vector_symbol(b, inc, "fxprio.ctl", "fx_p%d" % p, flag)
        d = b.element(inc, "fxprio.ctm", "fx_p%d" % p, "genie")
        flag(d)
        d.save()

        def small(d, colour=colour):
            r = d.create_object("rectangle", (0, 0, 8, 8), colour=colour)
            d.set_fill_keys(r, _state_pairs(colour))

        _vector_symbol(b, inc, "fxprio.ctl", "fx_p%d_small" % p, small)
    _vector_symbol(
        b,
        inc,
        "fxprio.ctl",
        "fx_shelved",
        lambda d: d.create_object("rectangle", (0, 0, 14, 14), colour=0x8080FF),
    )
    _vector_symbol(
        b,
        inc,
        "fxprio.ctl",
        "fx_disabled",
        lambda d: d.create_object("rectangle", (0, 0, 14, 14), colour=0x404040),
    )
    # fxicons16.ctl: 16 x 16 icons whose hot spot is their bottom-left corner
    for name, c in (("default", 0x60A060), ("page", 0xA06060)):
        _bitmap_symbol(b, inc, "fxicons16.ctl", name, 16, 16, c, hotspot=(0, 16))


def _state_pairs(colour: int) -> list:
    """The COL_ARR keys of a priority symbol of ``colour`` (0x00RRGGBB): state 0 its own
    colour, unacknowledged on flashing with white, unacknowledged off the other way round,
    acknowledged on steady."""

    def pair(a, c):
        return "0x%08x,0x%08x" % (a, c)

    return [
        ("COLMODE", "2"),
        ("COL_ARR", "FX_MODE"),
        ("COL_ARR_0", pair(colour, colour)),
        ("COL_ARR_1", pair(colour, 0x00FFFFFF)),
        ("COL_ARR_2", pair(0x00FFFFFF, colour)),
        ("COL_ARR_3", pair(colour, colour)),
    ]


#: fxparts.ctm's many-parameter genie: Tag first, then 41 more (P01 .. P41)
MANY_PARAMS = ["Tag"] + ["P%02d" % i for i in range(1, 42)]
#: fx_order's parameters in their declaration order (a trailing space, mixed case)
ORDER_PARAMS = ["buttonText ", "MixedCase", "lower", "UPPER", "Title"]

FXPARTS_FRM = r"""
! the genie forms of fxparts.ctm (our own forms)
! a second comment line before the first form
FORM "fx_named", "Genie - Fx named";
1: "Name", 40;
2: "Tag", 32, readwrite, "Variable Tags", "NAME", "TYPE=DIGITAL";
3: "Spare", 16;
NORMAL
"Name {1                     }
{LINE, 40}
Tag  {2                 }"

FORM "fx_insert_object", "Genie - Insert Object";
1: "GUID", 40;
2: "EVENTCLASS", 32;
3: "W", 8;
4: "H", 8;
5: "AN", 8, readonly;
NORMAL
"GUID        {1                     }
Event class {2                 }
Width       {3     }
Height      {4     }
AN          {5     }"

FORM "fx_listy", "Genie - Fx lists";
1: "Mode", 24, readwrite, "Labels", "Name", "FX_MODE_*";
2: "Flag", 6, readwrite, "FxBoolean", "NAME";
3: "Tag", 32, readwrite, "Variable Tags", "NAME";
NORMAL
"Mode {1             }
{LINE, 30}
Flag {2   }
{LINE, 30}
Tag  {3                 }"

FORM "fx_listy", "Genie - Fx lists (the later block)";
1: "Mode", 24;
NORMAL
"Mode {1             }"

FORM "fx_broken", "Genie - a form numbered 4 to 11";
4: "Tag", 16;
5: "Label", 16;
6: "A", 8;
7: "B", 8;
8: "C", 8;
9: "D", 8;
10: "E", 8;
11: "F", 8;
NORMAL
"Tag {4          }
Label {5          }"
"""

FXWS_FRM = r"""
! the pane form of fxws.ctm (a workspace pane of our own)
FORM "pane", "Genie - Fx pane";
1: "Name", 64;
2: "DefaultPage", 32, readwrite, "Pages", "NAME";
3: "IsDefault", 6, readwrite, "FxBoolean", "NAME";
4: "HasScrollbars", 6, readwrite, "FxBoolean", "NAME";
5: "DisplayMode", 32, readwrite, "Labels", "Name", "PANE_DISPLAYMODE_*";
6: "FillMode", 32, readwrite, "Labels", "Name", "PANE_FILLMODE_*";
7: "ContentTypes", 64, readwrite, "Contype", "NAME";
8: "TabPaneName", 32;
9: "TabControlName", 32;
10: "ExcludedAutofillPanes", 64;
11: "EquipRefAssocs", 64;
NORMAL
"Name                 {1                               }
Default page         {2                 }
{LINE, 60}
Is default           {3   }   Scroll bars {4   }
Display mode         {5                 }
Fill mode            {6                 }
{LINE, 60}
Content types        {7                               }
Tab pane             {8                 }
Tab control          {9                 }
Excluded panes       {10                              }
Categories           {11                              }"
"""


def build_lib_more(b: Build) -> None:
    """FxLib, the second set: fxmore.ctl (bitmap variants, a cp1252 name, a symbol of a
    symbol, a big element, the DspSym element with its handles), fxmany.ctl (14 elements:
    the directory grows past 8), fxempty.ctl (only a deleted element), fxparts.ctm (the
    genies the record-shape tests need) with its forms, and four more composite templates."""
    from ctgedit.format import element as E

    lib = "fxmore.ctl"
    _bitmap_symbol(b, "FxLib", lib, "fx_bitmap2", 14, 23, 0xA04040)
    _bitmap_symbol(b, "FxLib", lib, "fx_flash2", 16, 16, 0x00FF00, flash=0x004000)
    _vector_symbol(
        b,
        "FxLib",
        lib,
        CP1252_SYMBOL,
        lambda d: d.create_object("rectangle", (0, 0, 24, 16), colour=0x2080A0),
    )
    _vector_symbol(
        b,
        "FxLib",
        lib,
        "fx_symsym",
        lambda d: d.create_object("symbol", (0, 0, 40, 30), symbol=("fxsym", "fx_box", "ctl")),
    )
    _vector_symbol(
        b,
        "FxLib",
        lib,
        "fx_bigsym",
        lambda d: d.create_object(
            "polygon",
            None,
            [(int(40 + 38 * _cos(k)), int(40 + 38 * _sin(k))) for k in range(150)],
            colour=0x8040C0,
        ),
    )
    _vector_symbol(
        b,
        "FxLib",
        lib,
        "fx_grouped",
        lambda d: d.group_objects(
            [
                d.create_object("rectangle", (0, 0, 50, 30), colour=0x406080),
                d.create_object("text", (4, 6, 46, 22), text="Grp2"),
                d.create_object("ellipse", (52, 0, 70, 18), colour=0x80C040),
            ]
        ),
    )
    # fx_dspsym: the element a runtime DspSym draws, with CtDraw-like scattered handles:
    # a group (14) of a group (12: a hollow box 18 and a label 2), an expand mark (16)
    # and a symbol set (9) of six states (1, 5, 11, 6, 8, 13; each a group-root symbol's
    # forest, its ellipse 3, 4, 7, 10, 15, 17)
    d = b.element("FxLib", lib, "fx_dspsym", "symbol")
    box = d.create_object("rectangle_outline", (0, 0, 120, 20))
    lab = d.create_object("text", (4, 2, 100, 18), text="FxLabel")
    inner = d.group_objects([box, lab])
    mark = d.create_object("polygon", None, [(104, 6), (114, 6), (109, 14)], colour=0x000000)
    sset = d.create_symbol_set(
        (124, 2),
        kind=2,
        exprs=["FX_MODE"],
        states=[("fxsym", n) for n in ("fx_off", "fx_on", "fx_fault", "fx_off", "fx_on", "fx_fault")],
    )
    d.group_objects([inner, mark, sset])
    _rehandle(d, DSPSYM_HANDLES)
    d.save()

    # fxmany.ctl: 13 elements (pumps, the letters p-m-p, tanks, valves, agitators)
    for n, name in enumerate(MANY_SYMBOLS):
        _vector_symbol(
            b,
            "FxLib",
            "fxmany.ctl",
            name,
            lambda d, n=n: d.create_object("rectangle", (0, 0, 20 + n, 20), colour=0x101010 * (n % 15 + 1)),
        )
    # the last block of fxmany.ctl is a big one (over 1,000 bytes)
    _vector_symbol(
        b,
        "FxLib",
        "fxmany.ctl",
        "fx_last_big",
        lambda d: d.create_object(
            "polygon",
            None,
            [(int(30 + 28 * _cos(k)), int(30 + 28 * _sin(k))) for k in range(150)],
            colour=0x40C080,
        ),
    )
    # fxbmpgrp.ctl: plain and flashing bitmaps inside groups, under a dynamic group
    # (VISIBLE on the group) or a static one: 12 bitmaps in 6 elements
    for k in range(6):

        def bmpgroup(d, k=k):
            c = 0x2060A0 + 0x101010 * k
            a = d.add_bitmap_object((0, 0, 8, 8), _rgba(8, 8, c), 8, 8)
            f = d.add_bitmap_object(
                (10, 0, 18, 8), _rgba(8, 8, 0xC04040), 8, 8, flash_rgba=_rgba(8, 8, 0x400000 + 0x10 * k)
            )
            r = d.create_object("rectangle_outline", (0, 10, 18, 16))
            g = d.group_objects([a, f, r])
            if k % 2 == 0:
                d.ensure_property(g, "VISIBLE", "FX_PUMP%d" % (k // 2 + 1))

        _vector_symbol(
            b,
            "FxLib",
            "fxbmpgrp.ctl",
            "fx_bg_%s%d" % ("dyn" if k % 2 == 0 else "static", k // 2 + 1),
            bmpgroup,
        )
    # fxempty.ctl: its only element is deleted (a library the lists hide)
    _vector_symbol(
        b, "FxLib", "fxempty.ctl", "fx_was", lambda d: d.create_object("rectangle", (0, 0, 10, 10))
    )
    E.delete_library_element(os.path.join(b.proj("FxLib"), "fxempty.ctl"), "fx_was")

    # !fx_detail: a super genie with numbered associations (?DIGITAL n?) in a button, a
    # symbol set and Cicode objects (one showing AssTitle)
    d = b.element(
        "FxLib",
        "fxsuper.ctm",
        "!fx_detail",
        "supergenie",
        page_size=(320, 240),
        associations=[("1", "''"), ("2", "''"), ("3", "''")],
    )
    btn = d.create_object("button", (20, 190, 120, 220), text="Close")
    d.set_input_commands(btn, touch={"CMD": "WinFree();"})
    btn = d.create_object("button", (140, 190, 240, 220), text="Set")
    d.set_input_commands(btn, touch={"CMD": "?DIGITAL 2?=1"})
    d.create_symbol_set(
        (20, 20), kind=0, exprs=["?DIGITAL 1?"], states=[("fxsym", "fx_off"), ("fxsym", "fx_on")]
    )
    c = d.create_object("cicode_object", (60, 20, 80, 40))
    d.set_cicode_object(c, command="FxSet(?DIGITAL 3?, 0)")
    c = d.create_object("cicode_object", (100, 20, 120, 40))
    d.set_cicode_object(c, command="FxShowTitle(AssTitle())")
    d.create_object("text", (20, 60, 300, 76), text="?DIGITAL 1?")
    d.save()

    _parts_genies(b)
    _w(os.path.join(b.proj("FxLib"), "fxparts.frm"), _ci(FXPARTS_FRM))
    b.table(
        "FxLib",
        "labels",
        [
            {"NAME": "FxBoolean", "EXPR": "TRUE,FALSE"},
            {"NAME": "FX_MODE_AUTO", "EXPR": "0", "COMMENT": "a list label"},
            {"NAME": "FX_MODE_MANUAL", "EXPR": "1"},
            {"NAME": "PANE_DISPLAYMODE_STRETCH", "EXPR": "0"},
            {"NAME": "PANE_DISPLAYMODE_MAINTAIN", "EXPR": "1"},
            {"NAME": "PANE_FILLMODE_STATIC", "EXPR": "0"},
            {"NAME": "PANE_FILLMODE_AUTOFILL", "EXPR": "1"},
        ],
    )
    b.table(
        "FxLib",
        "locvar",
        [
            {
                "NAME": "fx_lib_local",
                "TYPE": "INT",
                "ZERO": "0",
                "FULL": "10",
                "COMMENT": "a local variable of an included project",
                "ASIZE": "1",
            }
        ],
    )
    b.table("FxLib", "equip", _equip_rows(["FxLibEquip", "FxLibEquip.Unit1"]))
    _w(
        os.path.join(b.proj("FxLib"), "fxlib.ci"),
        _ci("""
// FxLib's Cicode: a function of an included project (expression completion); it calls no
// built-in function, so an engine without a function list compiles it
FUNCTION FxLibShow(STRING sName, INT nMode)
    fx_lib_local = nMode;
END
"""),
    )
    for name, xml in COMPOSITE_XMLS.items():
        _w(os.path.join(b.proj("FxLib"), "Composite Genies", name + ".xml"), _ci(xml))


def _cos(k):
    import math

    return math.cos(2 * math.pi * k / 150)


def _sin(k):
    import math

    return math.sin(2 * math.pi * k / 150)


#: the symbol with code page 1252 letters in its name (an a with ring, an o slash)
CP1252_SYMBOL = "fx_blå_sø"
#: fx_dspsym's handles in record order (each state of the set is fxsym's group-root symbol:
#: its root, then its ellipse)
DSPSYM_HANDLES = (14, 12, 18, 2, 16, 9, 1, 3, 5, 4, 11, 7, 6, 10, 8, 15, 13, 17)
#: fxmany.ctl's elements
MANY_SYMBOLS = (
    "fx_pump_small",
    "fx_pump_large",
    "fx_pump_side",
    "fx_pmp_mark",
    "fx_primp",
    "fx_tank_tall",
    "fx_tank_small",
    "fx_motor",
    "fx_fan",
    "fx_valve_left",
    "fx_valve_right",
    "fx_agitator",
    "fx_agitator_grey",
)


def _constant_an(d, root: int, value: str) -> None:
    """The nested genie at ``root`` keeps the constant AN ``value`` (as CtDraw stores a
    nested genie placed with AN bound: its level bags hold AN, its text shows the value);
    the placement drops an AN binding (writer.genie_bindings), so it is written here."""
    from ctgedit.format import genie_place as GP

    objs = list(d.parsed.page.objects)
    for j in d.subtree(root):
        r = GP.decode(objs[j])
        if len(r.entries) < 2:
            continue
        level = r.entries[1]
        if j != root and level[0].get("AN") is None:
            continue
        level[1] = GP.Bag([("AN", value)], level[1].ov)
        level[2] = GP.Bag([("AN", value)], level[2].ov)
        if j != root:
            own = r.entries[0]
            own[1] = GP.Bag([(k, v.replace("%AN%", value)) for k, v in own[2].pairs], own[1].ov)
        objs[j] = GP.to_po(GP.encode(r))
    d._structural("constant AN", objs)


def _genie(b, name, build, lib="fxparts.ctm", **kw):
    d = b.element("FxLib", lib, name, "genie", **kw)
    build(d)
    d.save()
    return d


def _parts_genies(b: Build) -> None:
    from ctgedit.format import records as R

    add_po = lambda d, payload: d.add_object(R.to_po(payload))  # noqa: E731

    def many(d):
        d.create_object("text", (0, 0, 80, 16), text="%Tag%")
        for row in range(7):
            names = MANY_PARAMS[1 + 6 * row : 7 + 6 * row]
            d.create_object(
                "text", (0, 20 + 18 * row, 300, 36 + 18 * row), text=" ".join("%%%s%%" % n for n in names)
            )

    _genie(b, "fx_many", many)

    def order(d):
        for k, p in enumerate(ORDER_PARAMS):
            d.create_object("text", (0, 18 * k, 120, 16 + 18 * k), text="%%%s%%" % p)

    _genie(b, "fx_order", order)
    _genie(b, "fx_none", lambda d: d.create_object("rectangle", (0, 0, 30, 30), colour=0x305070))

    def goto(d):
        bt = d.create_object("button", (0, 0, 90, 24), text="Go")
        d.set_input_commands(bt, touch={"CMD": 'FxGoto("%Page%", 1)'})

    _genie(b, "fx_goto", goto)

    def inputs(d):
        r = d.create_object("rectangle", (0, 0, 60, 30), colour=0x606090)
        d.set_input_commands(
            r,
            touch={"CMD": "FxSet(%Tag%, 1)", "DOWN_CMD": "FxSet(%Tag%, 2)", "REPEAT_CMD": "FxSet(%Tag%, 3)"},
            keys=[{"KEY_SEQ": k, "CMD": "FxSet(%%Tag%%, %d)" % (10 + n)} for n, k in enumerate(GENIE_KEYS)],
        )
        d.create_object("text", (0, 34, 60, 50), text="%Tag%")

    _genie(b, "fx_inputs", inputs)

    def anconst(d):
        d.create_object("text", (0, 0, 60, 16), text="%AN%")

    _genie(b, "fx_anconst", anconst)

    def anlist(d):
        frame = d.create_object("rectangle_outline", (0, 0, 300, 120))
        d.ensure_property(frame, "ArrayAN", "%AN%")
        bt = d.create_object("button", (0, 124, 80, 148), text="Show")
        d.set_input_commands(bt, touch={"CMD": "FxListShow(%AN%);"})
        bar = d.create_object("rectangle", (304, 0, 316, 30), colour=0x808080)
        d.ensure_property(bar, "MVY", "FxListPos(%AN%)")
        d.ensure_property(bar, "MVYMINPT", "0")
        d.ensure_property(bar, "MVYMAXPT", "90")
        d.ensure_property(bar, "SZY", "FxListSize(%AN%)")
        d.ensure_property(bar, "SZYMINPC", "10")
        d.ensure_property(bar, "SZYMAXPC", "100")
        d.ensure_property(bar, "SZYORG", "-1")
        n = d.place_library_element("fxparts", "fx_anconst", "ctm", (90, 124), params={"AN": "10"})
        assert n is not None
        _constant_an(d, n, "10")
        d.create_object("text", (180, 124, 300, 140), text="%Page%")

    _genie(b, "fx_anlist", anlist)

    def named(d):
        t = d.create_object("text", (0, 0, 120, 16), text="Name")
        d.set_text_display(t, [("TXTMODE", "0"), ("TXT_OFF", "%Name%")])
        n = d.create_object("number", (0, 20, 120, 36), text="###.#")
        d.ensure_property(n, "TXT_STR", "%Tag%_LT")
        d.create_object("text", (0, 40, 120, 56), text="%Spare%")

    _genie(b, "fx_named", named)
    _genie(
        b,
        "fx_insert_object",
        lambda d: d.create_object("text", (0, 0, 200, 16), text="%GUID% %EVENTCLASS% %W% %H%"),
    )
    _genie(b, "fx_listy", lambda d: d.create_object("text", (0, 0, 200, 16), text="%Mode% %Flag% %Tag%"))

    def broken(d):
        d.create_object("text", (0, 0, 100, 16), text="%Tag%")
        d.create_object("text", (0, 20, 100, 36), text="%Label%")

    _genie(b, "fx_broken", broken)

    def offset(d):
        d.create_object("rectangle", (0, 0, 80, 60), colour=0x70A070)
        d.create_object("text", (4, 4, 76, 20), text="%Tag%")
        d.set_hotspot(40, 30)

    _genie(b, "fx_offset", offset)

    def points(d):
        # V3 / V4 point records: a number (0x1007) and an animated symbol (0x100D), both
        # 1 x 1 with no symbol, so their natural extent is CtDraw's 22 x 22 box at the point
        # (its load derives that again for every such record, whatever was stored)
        add_po(
            d,
            R.point_record(
                0x1007,
                handle=d.next_handle(),
                at=(0, 0),
                deltas=NO_SYMBOL_DELTAS,
                props=(("EXPR", "%Tag%"), ("COMMENT", "")),
            ),
        )
        add_po(
            d,
            R.animated_symbol(
                handle=d.next_handle(),
                at=(40, 0),
                deltas=NO_SYMBOL_DELTAS,
                props=(("EXPR", "%Tag%"), ("COMMENT", ""), ("MODE", "ANM_ARRAY")),
            ),
        )

    _genie(b, "fx_points", points)

    def grouproot(d):
        a = d.create_object("rectangle", (0, 0, 40, 40), colour=0x407040)
        t = d.create_object("text", (4, 12, 36, 28), text="%Tag%")
        d.group_objects([a, t])
        d.set_hotspot(20, 20)

    _genie(b, "fx_grouproot", grouproot)

    def dummies(d):
        d.create_object("rectangle", (0, 0, 40, 20), colour=0x907050)
        _cleared_marker(d, (50, 0, 50, 1))  # 0 x 1
        _cleared_marker(d, (60, 0, 61, 1))  # 1 x 1
        d.create_object("text", (0, 24, 60, 40), text="%Tag%")

    _genie(b, "fx_dummy", dummies)

    def handles(d):
        # out-of-order handles; the touch command shares its owner's handle
        r = d.create_object("rectangle", (0, 0, 50, 30), colour=0x5070A0)
        d.set_input_commands(r, touch={"CMD": "FxSet(%Tag%, 4)"})
        d.create_object("text", (0, 34, 80, 50), text="%Tag%")
        d.create_object("ellipse", (56, 0, 80, 24), colour=0xA07050)
        _rehandle(d, (9, 9, 3, 6))

    _genie(b, "fx_handles", handles)

    def row(d):
        d.create_object("text", (0, 0, 40, 16), text="%RowNum%")
        assert (
            d.place_library_element("fxnest", "n1", "ctm", (50, 0), params={"Name": "Row %RowNum%"})
            is not None
        )
        d.create_object("pipe", None, [(0, 40), (60, 40), (60, 60)])

    _genie(b, "fx_row", row)

    def mixed(d):
        # one member of each class the member-access tests walk: text 0x05, rectangle
        # 0x0F, line 0x01, ellipse 0x11, polygon 0x10, and a nested group
        d.create_object("text", (0, 0, 60, 16), text="%Tag%")
        d.create_object("rectangle", (0, 20, 40, 40), colour=0x406040)
        d.create_object("line", (0, 44, 60, 44))
        d.create_object("ellipse", (44, 20, 64, 40), colour=0x604040)
        d.create_object("polygon", None, [(0, 50), (20, 50), (10, 66)], colour=0x404060)
        a = d.create_object("rectangle", (70, 0, 90, 20), colour=0x202020)
        c = d.create_object("rectangle", (70, 24, 90, 44), colour=0x808080)
        d.group_objects([a, c])

    _genie(b, "fx_mixed", mixed)

    def alarm(d):
        a = d.create_object("rectangle", (0, 0, 40, 30), colour=0x709070)
        t = d.create_object("text", (0, 32, 60, 48), text="%Equipment%")
        g = d.group_objects([a, t])
        _alarm_on(d, g, "%Equipment%", width=2, padding=3, flag_on=True)

    _genie(b, "fx_alarm", alarm)

    def picture(d):
        from ctgedit.format import bitmaps

        wmf, ww, wh = bitmaps.wmf_from_file(_wmf(48, 24, 0x806020))
        d.add_metafile_object((0, 0, ww, wh), wmf)
        d.create_object("text", (0, wh + 4, 80, wh + 20), text="%Tag%")

    _genie(b, "fx_picture", picture)

    def valve_left(d):
        v = d.create_object("polygon", None, [(30, 0), (0, 15), (30, 30)], colour=0x40A0A0)
        d.ensure_property(v, "COLMODE", "0")
        d.ensure_property(v, "COL_A", "%Tag%")

    _genie(b, "fx_valve_left", valve_left)

    def defaults(d):
        # a declared default: the paste dialog shows 'Pump' for an unbound Title
        d.create_object("text", (0, 0, 120, 16), text="%Title=Pump%")
        d.create_object("text", (0, 20, 120, 36), text="%Tag%")

    _genie(b, "fx_default", defaults)

    def stale(d):
        # (build_main_more takes FxStaleEquip out after FxParts placed it: the instance
        # then binds Equip, which the element no longer declares)
        d.create_object("rectangle", (0, 0, 40, 20), colour=0x405060)
        d.create_object("text", (0, 24, 80, 40), text="%Tag%")
        _name(d, d.create_object("text", (0, 44, 80, 60), text="%Equip%"), "FxStaleEquip")

    _genie(b, "fx_stale", stale)

    def loop(d):
        r = d.create_object("rectangle", (0, 0, 40, 20), colour=0x506070)
        d.set_metadata(r, [("SP", "%LOOP%_SP"), ("PV", "%LOOP%_PV")])
        d.create_object("text", (0, 24, 80, 40), text="%LOOP%")

    _genie(b, "fx_loop", loop)

    def setpoint(d):
        n = d.create_object("number", (0, 0, 80, 16), text="###.#")
        d.ensure_property(n, "TXT_STR", "%TAG%")
        d.set_input_commands(n, keys=[{"KEY_SEQ": "ENTER", "CMD": "%TAG% = ArgValue1"}])
        d.set_metadata(n, [("Comment", "Setpoint")])

    _genie(b, "fx_setpoint", setpoint)

    def pipe(d):
        d.create_object("pipe", None, [(0, 0), (60, 0), (60, 40)])
        d.create_object("text", (0, 10, 50, 26), text="%Tag%")

    _genie(b, "fx_pipe", pipe)

    def setgenie(d):
        # the states link fxsym's BITMAP symbols: a bitmap state is one tc 7 record, which
        # a placed genie stores art-stripped (an empty symbol slot: with no other art the
        # reader draws the genie from its library); fx_off / fx_on are group-root symbols,
        # whose states are instance forests that carry their shapes (FxSymbols' sets)
        d.create_symbol_set(
            (0, 0), kind=0, exprs=["%Tag%"], states=[("fxsym", "fx_bitmap"), ("fxsym", "fx_flash")]
        )
        d.create_symbol_set(
            (24, 0),
            kind=2,
            exprs=["%Tag%_MODE"],
            states=[("fxsym", "fx_bitmap"), ("fxsym", "fx_flash"), ("fxsym", "fx_bitmap")],
        )

    _genie(b, "fx_sets", setgenie)

    for name, caption in (("fx_agitator", "Agitator"), ("fx_agitator_mixer", "Mixer")):
        _genie(
            b,
            name,
            lambda d, caption=caption: (
                d.create_object("ellipse", (0, 0, 40, 40), colour=0x6060A0),
                d.create_object("text", (0, 44, 80, 60), text=caption + " %Tag%"),
            ),
        )

    def hidden_base(d):
        # a blue base rectangle with a constant VISIBLE '1', a caption over it
        r = d.create_object("rectangle", (0, 0, 60, 30), colour=0x2050E0)
        d.ensure_property(r, "VISIBLE", "1")
        d.create_object("text", (4, 34, 60, 50), text="%Tag%")

    _genie(b, "fx_hidden_base", hidden_base)

    def linktext(d):
        # a black text whose flat green COL_MAXON is link-driven ('!*#' key: a runtime
        # colour), in a group with a black (hollow) frame
        frame = d.create_object("rectangle_outline", (0, 0, 100, 24))
        t = d.create_object("text", (4, 4, 96, 20), text="Fx step:")
        d.set_fill_keys(t, [("COL_MAXON", "0x0000ff00,0x0000ff00")])
        d.ensure_property(t, "!*#1410", "#1406")
        d.group_objects([frame, t])

    _genie(b, "fx_linktext", linktext)

    def bowtie(d):
        # a valve bowtie: two static black triangles, a caption of the tag
        for pts in ([(0, 0), (20, 10), (0, 20)], [(40, 0), (20, 10), (40, 20)]):
            d.create_object("polygon", None, pts, colour=0x000000)
        d.create_object("text", (0, 24, 60, 40), text="%Tag%")

    _genie(b, "fx_bowtie", bowtie)

    def stale_note(d):
        # (build_main_more adds an ellipse after FxParts placed it as FxStale2, which binds
        # Tag only: Note stays unbound, and two members show it)
        d.create_object("rectangle", (0, 0, 40, 20), colour=0x605040)
        d.create_object("text", (0, 24, 80, 40), text="%Tag%")
        d.create_object("text", (0, 44, 80, 60), text="%Note%")
        d.create_object("text", (0, 64, 80, 80), text="(%Note%)")

    _genie(b, "fx_stale_note", stale_note)

    def status(d):
        # a status box (white, a 1 px black frame), a tag text shown with VISIBLE '1'
        # (anchored left) and a multi-state text whose first set state is 'S'
        r = _name(d, d.create_object("rectangle", (0, 0, 40, 20), colour=0xFFFFFF), "FxStatusBox")
        assert d.set_colour(r, 0x000000, line=True)
        t = _name(d, d.create_object("text", (44, 2, 200, 18), text="-FX.T501/%Tag%"), "FxHiddenTag")
        d.ensure_property(t, "VISIBLE", "1")
        t = _name(d, d.create_object("text", (0, 24, 40, 40), text="S"), "FxStateText")
        d.set_text_display(
            t, [("TXTMODE", "1"), ("TXT_A", "%Tag%"), ("STATE_0", "S"), ("STATE_1", "R")], caption="S"
        )

    _genie(b, "fx_status", status)

    def turned_pic(d):
        # a 19 x 19 picture stretched into a 19 x 31 rect (FxParity turns the instance)
        _null_pen(d, d.create_object("rectangle_outline", (0, 0, 19, 31)))
        _name(d, d.add_bitmap_object((0, 0, 19, 31), _rgba(19, 19, 0x30C060), 19, 19), "FxPic")

    _genie(b, "fx_turned_pic", turned_pic)

    def spinner(d):
        # a busy spinner picture and a caption of the instance's %Desc%; build_main_third
        # gives the picture a stored slot-1 translation of (-587, 3)
        _name(d, d.add_bitmap_object((0, 0, 16, 16), _rgba(16, 16, 0x6060E0), 16, 16), "FxSpinPic")
        d.create_object("text", (20, 0, 120, 16), text="%Desc%")

    _genie(b, "fx_spinner", spinner)

    def lost_pipe(d):
        # (build_main_more takes the pipe out after FxTplArrows placed it: the page's
        # art-stripped pipe then has no twin to take its vertices from)
        d.create_object("text", (0, 0, 60, 16), text="%Tag%")
        _name(d, d.create_object("pipe", None, [(0, 30), (60, 30), (60, 60)]), "FxLostPipe")

    _genie(b, "fx_lost_pipe", lost_pipe)

    def tipbutton(d):
        # a named member button whose COMMENT (its tool tip) binds %Tip%
        bt = _name(d, d.create_object("button", (0, 0, 80, 24), text="Tip"), "FxTipButton")
        d.ensure_property(bt, "COMMENT", "%Tip%")
        d.create_object("text", (0, 28, 80, 44), text="%Tip%")

    _genie(b, "fx_tipbutton", tipbutton)

    def popper2(d):
        d.create_object("button", (0, 0, 80, 24), text="Detail")
        d.create_object("text", (0, 28, 80, 44), text="%Tag%")
        d.set_attachments(["fxsuper.!fx_detail", "fxsuper.!fx_popup"])

    _genie(b, "fx_popper2", popper2)


COMPOSITE_XMLS = {
    "fx_status_c": """<?xml version="1.0" encoding="utf-8"?>
<VisualTemplate Guid="{B0C1D2E3-F405-4A16-8B27-C3D4E5F60718}" Name="FxStatusC" Thumbnail="fx_status">
  <Parameters>
    <Parameter Name="Equipment" Label="Equipment" Type="String" NamingRules="Equipment"/>
  </Parameters>
  <ContentItems>
    <Content Id="1" Type="Genie" Project="FxLib" Library="fxparts" Item="fx_sets"/>
    <Content Id="2" Type="Genie" Project="FxLib" Library="fxparts" Item="fx_alarm"/>
    <Content Id="3" Type="Genie" Project="FxLib" Library="fxparts" Item="fx_bowtie"/>
  </ContentItems>
  <Compositions>
    <Composition Id="{B0C1D2E3-F405-4A16-8B27-C3D4E5F60719}">
      <Layout>
        <Content ItemId="2" ZIndex="1"><Parameters>
          <Parameter Name="Equipment" TemplateParameter="Equipment"/></Parameters></Content>
        <Content ItemId="1" ZIndex="2"/>
        <Content ItemId="3" ZIndex="3"/>
      </Layout>
    </Composition>
  </Compositions>
</VisualTemplate>""",
    "fx_valve_c": """<?xml version="1.0" encoding="utf-8"?>
<VisualTemplate Guid="{7C2E4D3B-9E5F-4A6B-8C7D-8E9F0A1B2C3D}" Name="FxValveC" Thumbnail="fx_valve">
  <Parameters>
    <Parameter Name="Equipment" Label="Equipment" Type="String" NamingRules="Equipment"/>
    <Parameter Name="ValveType" Label="Valve type" Type="String" DefaultValue="Block">
      <Values><Value Id="0" Label="Block"/><Value Id="1" Label="Control"/>
        <Value Id="2" Label="Damper"/><Value Id="3" Label="Hand"/></Values>
    </Parameter>
    <Parameter Name="DisplayAlarm" Label="Display alarm" Type="Bool" DefaultValue="false"/>
  </Parameters>
  <ContentItems>
    <Content Id="1" Type="Genie" Project="FxLib" Library="fxgenie" Item="fx_plain"/>
    <Content Id="2" Type="Genie" Project="FxLib" Library="fxparts" Item="fx_valve_left"/>
  </ContentItems>
  <Compositions>
    <Composition Id="{7C2E4D3B-9E5F-4A6B-8C7D-8E9F0A1B2C3E}">
      <Layout>
        <Content ItemId="2" ZIndex="1"><Parameters>
          <Parameter Name="Tag" TemplateParameter="Equipment"/></Parameters></Content>
        <Content ItemId="1" ZIndex="2" VisibleWhen="ShowAlarm"/>
      </Layout>
    </Composition>
  </Compositions>
  <GlobalConditions>
    <GlobalCondition Id="ShowAlarm">
      <ParameterCondition Name="DisplayAlarm" Value="true"/></GlobalCondition>
  </GlobalConditions>
</VisualTemplate>""",
    "fx_drive_c": """<?xml version="1.0" encoding="utf-8"?>
<VisualTemplate Guid="{8D3F5E4C-AF60-4B7C-9D8E-9FA0B1C2D3E4}" Name="FxDriveC" Thumbnail="fx_motor">
  <Parameters>
    <Parameter Name="Equipment" Label="Equipment" Type="String" NamingRules="Equipment"
        MinLength="1" MaxLength="160"/>
    <Parameter Name="MEO" Label="Mode" Type="Int" DefaultValue="0">
      <Values><Value Id="0" Label="0"/><Value Id="1" Label="1"/><Value Id="2" Label="2"/>
        <Value Id="3" Label="3"/><Value Id="4" Label="4"/><Value Id="5" Label="5"/></Values>
    </Parameter>
    <Parameter Name="DisplayLabel" Label="Label position" Type="String" DefaultValue="Below">
      <Values><Value Id="0" Label="Below"/><Value Id="1" Label="Above"/></Values>
    </Parameter>
    <Parameter Name="Label" Label="Label" Type="String" DefaultValue="Drive">
      <Dependency><ParameterCondition Name="DisplayLabel" Value="Above"/></Dependency>
    </Parameter>
    <Parameter Name="DriveType" Label="Drive type" Type="String" DefaultValue="Pump">
      <Values><Value Id="0" Label="Pump"/><Value Id="1" Label="Fan"/>
        <Value Id="2" Label="Sprayer"/></Values>
    </Parameter>
    <Parameter Name="DisplayStatus" Label="Status" Type="String" DefaultValue="None">
      <Values><Value Id="0" Label="None"/><Value Id="1" Label="Inside Right"/></Values>
    </Parameter>
    <Parameter Name="Orientation" Label="Orientation" Type="String" DefaultValue="Left">
      <Values><Value Id="0" Label="Left"/><Value Id="1" Label="Right"/></Values>
    </Parameter>
  </Parameters>
  <ContentItems>
    <Content Id="1" Type="Genie" Project="FxLib" Library="fxgenie" Item="fx_pump"/>
    <Content Id="2" Type="Genie" Project="FxLib" Library="fxgenie" Item="fx_value"/>
  </ContentItems>
  <Compositions>
    <Composition Id="{8D3F5E4C-AF60-4B7C-9D8E-9FA0B1C2D3E5}">
      <Layout>
        <Content ItemId="1" ZIndex="1"><Parameters>
          <Parameter Name="Tag" TemplateParameter="Equipment"/>
          <Parameter Name="Label" TemplateParameter="Label"/></Parameters></Content>
        <Content ItemId="2" ZIndex="2" VisibleWhen="LabelAbove"><Parameters>
          <Parameter Name="Description" TemplateParameter="Label"/></Parameters></Content>
      </Layout>
    </Composition>
  </Compositions>
  <GlobalConditions>
    <GlobalCondition Id="LabelAbove">
      <ParameterCondition Name="DisplayLabel" Value="Above"/></GlobalCondition>
  </GlobalConditions>
</VisualTemplate>""",
    "fx_switch_c": """<?xml version="1.0" encoding="utf-8"?>
<VisualTemplate Guid="{9E4A6F5D-B071-4C8D-AE9F-A0B1C2D3E4F5}" Name="FxSwitchC" Thumbnail="fx_switch">
  <Parameters>
    <Parameter Name="Equipment" Label="Equipment" Type="String" NamingRules="Equipment"/>
    <Parameter Name="Size" Label="Size" Type="String" DefaultValue="Small">
      <Values><Value Id="0" Label="Small"/><Value Id="1" Label="Large"/></Values>
    </Parameter>
  </Parameters>
  <ContentItems>
    <Content Id="1" Type="Genie" Project="FxLib" Library="fxgenie" Item="fx_plain"/>
    <Content Id="2" Type="Genie" Project="FxLib" Library="fxgenie" Item="fx_value"/>
  </ContentItems>
  <Compositions>
    <Composition Id="{9E4A6F5D-B071-4C8D-AE9F-A0B1C2D3E4F6}">
      <Prerequisite><ParameterCondition Name="Size" Value="Large"/></Prerequisite>
      <Layout>
        <Content ItemId="2" ZIndex="1"><Parameters>
          <Parameter Name="Description" TemplateParameter="Equipment"/></Parameters></Content>
      </Layout>
    </Composition>
    <Composition Id="{9E4A6F5D-B071-4C8D-AE9F-A0B1C2D3E4F7}">
      <Layout>
        <Content ItemId="1" ZIndex="1" VisibleWhen="IsSmall"/>
      </Layout>
    </Composition>
  </Compositions>
  <GlobalConditions>
    <GlobalCondition Id="IsSmall">
      <ParameterCondition Name="Size" Value="Small" Comparison="IsEqualTo"/></GlobalCondition>
  </GlobalConditions>
</VisualTemplate>""",
    "fx_meter_c": """<?xml version="1.0" encoding="utf-8"?>
<VisualTemplate Guid="{AF5B7A6E-C182-4D9E-BFA0-B1C2D3E4F5A6}" Name="FxMeterC" Thumbnail="fx_meter">
  <Parameters>
    <Parameter Name="Equipment" Label="Equipment" Type="String" NamingRules="Equipment"/>
    <Parameter Name="DisplayOutput" Label="Display output" Type="Bool" DefaultValue="false"/>
    <Parameter Name="DisplayReadback" Label="Display readback" Type="Bool" DefaultValue="false">
      <Dependency><ParameterCondition Name="DisplayOutput" Value="true"/></Dependency>
    </Parameter>
    <Parameter Name="DisplayTrend" Label="Display trend" Type="Bool" DefaultValue="false"/>
    <Parameter Name="TrendType" Label="Trend type" Type="String" DefaultValue="Line">
      <Dependency><GlobalConditionRef Id="HasTrend"/></Dependency>
      <Values><Value Id="0" Label="Line"/><Value Id="1" Label="Bar"/></Values>
    </Parameter>
    <Parameter Name="Span" Label="Span" Type="Int" DefaultValue="60" MinValue="1" MaxValue="3600">
      <Dependency><GlobalConditionRef Id="HasTrend"/></Dependency>
    </Parameter>
    <Parameter Name="DisplaySecond" Label="Display a second meter" Type="Bool" DefaultValue="false"/>
    <Parameter Name="Equipment2" Label="Second equipment" Type="String" NamingRules="Equipment">
      <Dependency><GlobalConditionRef Id="TwoMeters"/></Dependency>
    </Parameter>
    <Parameter Name="ReferenceEquipment" Label="Reference" Type="String" NamingRules="Equipment">
      <Dependency><ParameterCondition Name="DisplayOutput" Value="true"/></Dependency>
    </Parameter>
  </Parameters>
  <ContentItems>
    <Content Id="1" Type="Genie" Project="FxLib" Library="fxgenie" Item="fx_value"/>
    <Content Id="2" Type="Genie" Project="FxLib" Library="fxgenie" Item="fx_plain"/>
  </ContentItems>
  <Compositions>
    <Composition Id="{AF5B7A6E-C182-4D9E-BFA0-B1C2D3E4F5A7}">
      <Layout>
        <Content ItemId="1" ZIndex="1"><Parameters>
          <Parameter Name="Description" TemplateParameter="Equipment"/></Parameters></Content>
        <Content ItemId="2" ZIndex="2" VisibleWhen="HasTrend"/>
      </Layout>
    </Composition>
  </Compositions>
  <GlobalConditions>
    <GlobalCondition Id="HasTrend">
      <ParameterCondition Name="DisplayTrend" Value="true"/></GlobalCondition>
    <GlobalCondition Id="TwoMeters">
      <ParameterCondition Name="DisplaySecond" Value="true"/></GlobalCondition>
  </GlobalConditions>
</VisualTemplate>""",
}


def build_style_more(b: Build) -> None:
    """FxStyle, second set."""
    from ctgedit.format import geometry as G

    alt = "fx_alt.ctt"
    d = b.element(
        "FxStyle",
        alt,
        "marker@-XGA",
        "template",
        page_size=(1024, 768),
        page_properties=[("FxPageKind", "marker")],
    )
    d.create_object("rectangle_outline", (10, 10, 1013, 757))
    r = d.create_object("rectangle", (400, 12, 620, 40), colour=0x6080A0)
    m = _cleared_marker(d, (420, 16, 520, 35))
    _name(d, d.group_objects([r, m]), "FxTplMarkerGroup")
    d.save()
    d = b.element(
        "FxStyle",
        alt,
        "sets@-XGA",
        "template",
        page_size=(1024, 768),
        page_properties=[("FxPageKind", "sets")],
    )
    d.create_object("rectangle_outline", (10, 10, 1013, 757))
    _name(
        d,
        d.create_symbol_set(
            (900, 20), kind=0, exprs=["FX_PUMP1"], states=[("fxmore", CP1252_SYMBOL), ("fxsym", "fx_on")]
        ),
        "FxTplSet",
    )
    d.save()
    d = b.element(
        "FxStyle",
        alt,
        "flash@-XGA",
        "template",
        page_size=(1024, 768),
        page_properties=[("FxPageKind", "flash")],
    )
    for i in (
        d.create_object("rectangle_outline", (10, 10, 1013, 757)),
        d.create_object("text", (20, 20, 200, 36), text="FxFlashText"),
        d.create_object("line", (20, 50, 400, 50)),
        d.create_object("line", (20, 60, 400, 120)),
    ):
        slot = G.SLOT_FILL if d.parsed.page.objects[i].type_code == 0x05 else G.SLOT_LINE
        assert d.set_colour_slot(i, slot, FLASH_ON, FLASH_OFF)
    d.add_bitmap_object(
        (500, 20, 516, 36), _rgba(16, 16, 0xFF8080), 16, 16, flash_rgba=_rgba(16, 16, 0x800000)
    )
    d.save()
    d = b.element(
        "FxStyle",
        alt,
        "stale@-XGA",
        "template",
        page_size=(1024, 768),
        page_properties=[("FxPageKind", "stale")],
    )
    d.create_object("rectangle_outline", (10, 10, 1013, 757))
    _name(
        d,
        d.group_objects(
            [
                d.create_object("rectangle", (30, 30, 90, 60), colour=0x4060A0),
                d.create_object("ellipse", (100, 30, 130, 60), colour=0xA06040),
            ]
        ),
        "FxTplStaleGroup",
    )
    _name(d, d.create_object("text", (30, 80, 200, 96), text="FxBefore"), "FxTplCaption")
    d.save()
    # arrows@-XGA: a header with back / forward arrows and two alarm diamonds, four
    # bitmaps that each own a touch command
    d = b.element(
        "FxStyle",
        alt,
        "arrows@-XGA",
        "template",
        page_size=(1024, 768),
        page_properties=[("FxPageKind", "arrows")],
    )
    d.create_object("rectangle_outline", (10, 10, 1013, 757))
    d.create_object("rectangle", (10, 10, 1013, 40), colour=0x404850)
    for k, (colour, cmd) in enumerate(
        (
            (0x60A0E0, "PageBack()"),
            (0x60A0E0, "PageForward()"),
            (0xE02020, 'PageDisplay("FxAlarms")'),
            (0xE02020, 'PageDisplay("FxAnim")'),
        )
    ):
        x = 20 + 30 * k
        i = d.add_bitmap_object((x, 14, x + 22, 36), _rgba(22, 22, colour), 22, 22)
        d.set_input_commands(i, touch={"CMD": cmd})
    d.save()
    for name, (w, h) in (("normal@HD1080", (1920, 1080)), ("normal@HD768", (1366, 768))):
        d = b.element(
            "FxStyle",
            "fx_hd.ctt",
            name,
            "template",
            page_size=(w, h),
            page_properties=TEMPLATE_PROPS,
            associations=TEMPLATE_ASSOCS,
            background=(0xE5E5E5, 0xE5E5E5),
        )
        _template_art(d, w, h, True)
        d.save(thumbnail=_thumb(40, 23, 0x204080))
    for name, (w, h) in (
        ("normal@-HD1080", (1920, 1080)),
        ("blank@-HD1080", (1920, 1080)),
        ("pagecontent@-HD1080", (1920, 1080)),
        ("normal@-UHD4K", (3840, 2160)),
    ):
        d = b.element(
            "FxStyle",
            "fx_sa.ctt",
            name,
            "template",
            page_size=(w, h),
            page_properties=[("FxPageKind", name.split("@")[0]), ("HasPanes", "0")],
            associations=[("__EquipmentRef", "''"), ("__EquipmentName", "''")],
            background=(0xDDDDDD, 0xDDDDDD),
        )
        d.create_object("rectangle_outline", (0, 0, w - 1, h - 1))
        if not name.startswith("blank"):
            d.create_object("rectangle", (0, 0, w, 40), colour=0x303840)
            i = d.place_library_element("fxmany", "fx_valve_right", "ctl", (w - 40, 10))
            _name(d, i, "FxTplNav")
            d.set_input_commands(i, touch={"CMD": 'PageDisplay("FxShapes")'})
        d.save()


@contextlib.contextmanager
def _later(seconds: int = 3600):
    """The fixed clock an hour on (an element saved in here is newer than the pages that
    placed it before: they read stale)."""
    import datetime
    from ctgedit.infra import identity

    old = identity.FIXED_TIME
    identity.FIXED_TIME = old + datetime.timedelta(seconds=seconds)
    try:
        yield
    finally:
        identity.FIXED_TIME = old


def _idx(doc, name: str) -> int:
    hits = doc.find_objects_by_name(name)
    assert len(hits) == 1, (name, hits)
    return hits[0]


def _place(doc, name, lib, element, pos, params=None, ext="ctm", **kw) -> int:
    i = doc.place_library_element(lib, element, ext, pos, params=params, **kw)
    assert i is not None, (lib, element)
    return _name(doc, i, name)


#: FxMain's second-set tags: FX_T201 .. FX_T226, each with LT, PV, SP and OUT (all trended)
FX_T_TAGS = [
    ("FX_T%d_%s" % (n, s), "REAL", "FxPlant.T%d" % n, s, "###.#", "tank %d %s" % (n, s.lower()))
    for n in range(201, 227)
    for s in ("LT", "PV", "SP", "OUT")
]


#: FxMaster's page-wide LBUTTON_UP command: two lines (CR LF)
MASTER_KEY_CMD = "FxSet(FX_MODE, 0);\r\nFxSet(FX_MODE, 1);"


def build_main_more(b: Build) -> None:
    """FxMain, the second set: the pages the record-shape, input, display, colour, member,
    selection and genie tests need, more template pages, composites, a master page, a page
    of placeholders whose library is back, and the T2xx tags with their trends."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed

    libraries_changed()
    main = b.proj("FxMain")
    # every REAL tag is trended (more than 100 trend tags for the trend picker)
    b.table(
        "FxMain",
        "trend",
        [
            {
                "NAME": "FX_TR_%s" % n,
                "EXPR": n,
                "SAMPLEPER": "00:00:01",
                "ENG_ZERO": "0",
                "ENG_FULL": "100",
                "TYPE": "TRN_PERIODIC",
                "CLUSTER": "FxCluster",
                "EQUIP": e,
                "ITEM": it,
                "COMMENT": "trend of %s" % n,
            }
            for (n, t, e, it, _f, _c) in FX_TAGS + FX_T_TAGS
            if t == "REAL"
        ],
    )

    # ---- FxArrange: the record shapes of the arrange / transform tests
    doc = b.blank_page("FxMain", "FxArrange", size=(1280, 800), register=False)
    add = _adder(doc)
    add("FxFreeBefore", "rectangle", (20, 20, 60, 50), colour=0x806040)
    add("FxLeafBefore", "ellipse", (20, 60, 50, 90), colour=0x408060)
    for k in range(93):
        x, y = 80 + 12 * (k % 31), 60 + 12 * (k // 31)
        doc.create_object("rectangle", (x, y, x + 10, y + 10), colour=0x202020 + 0x030303 * k)
    cells = list(range(len(doc.parsed.page.objects) - 93, len(doc.parsed.page.objects)))
    _name(doc, doc.group_objects(cells), "FxBigInner")
    add("FxLeafAfter", "text", (20, 110, 80, 126), text="after")
    _name(
        doc,
        doc.group_objects([_idx(doc, "FxLeafBefore"), _idx(doc, "FxBigInner"), _idx(doc, "FxLeafAfter")]),
        "FxBigGroup",
    )
    add("FxFreeAfter", "rectangle", (460, 20, 500, 50), colour=0x604080)
    _place(doc, "FxCarrier", "fxgenie", "fx_plain", (600, 20))
    add("FxSameSize", "rectangle", (680, 20, 740, 40), colour=0x707070)
    _place(doc, "FxPointGenie", "fxparts", "fx_points", (600, 100), {"Tag": "FX_MODE"})
    t = add("FxKeyText", "text", (600, 180, 700, 196), text="keys")
    doc.set_input_commands(t, keys=[{"KEY_SEQ": "ENTER", "CMD": "FxSet(FX_MODE, 1)"}])
    add("FxLoneText", "text", (720, 180, 820, 196), text="alone")
    _name(
        doc,
        doc.create_symbol_set(
            (600, 240),
            kind=2,
            exprs=["FX_MODE"],
            states=[("fxsym", "fx_off"), ("fxsym", "fx_on"), ("fxsym", "fx_fault")],
        ),
        "FxTripleSet",
    )
    r = add("FxTouchFill", "rectangle", (600, 300, 680, 340), colour=0x40A040)
    doc.set_input_commands(r, touch={"CMD": "FxToggle(FX_PUMP1)"})
    bt = add("FxTouchKeyButton", "button", (700, 300, 800, 330), text="Both")
    doc.set_input_commands(
        bt, touch={"CMD": "FxSet(FX_MODE, 2)"}, keys=[{"KEY_SEQ": "F3", "CMD": "FxSet(FX_MODE, 3)"}]
    )
    _save(doc)

    # ---- FxInputs: input records on every kind of owner (registered: pgtouch, pgkey)
    doc = b.blank_page("FxMain", "FxInputs")
    add = _adder(doc)
    t = add("FxTouchText", "text", (20, 20, 140, 36), text="touch me")
    doc.set_input_commands(t, touch={"CMD": "WinFree();"})
    r = add("FxKeyRect", "rectangle", (160, 20, 240, 60), colour=0x8080C0)
    doc.set_input_commands(r, keys=[{"KEY_SEQ": "ENTER", "CMD": "WinFree()"}])
    _place(doc, "FxInputGenie", "fxparts", "fx_inputs", (260, 20), {"Tag": "FX_MODE"})
    s = _name(
        doc, doc.create_object("symbol", (20, 100, 60, 130), symbol=("fxsym", "fx_box", "ctl")), "FxSymTouch"
    )
    doc.set_input_commands(s, touch={"CMD": "FxToggle(FX_PUMP3)"})
    r = add("FxRotatedTouch", "rectangle", (100, 100, 180, 130), colour=0xC08040)
    doc.set_input_commands(r, touch={"CMD": "FxToggle(FX_PUMP2)"})
    doc.rotate_objects([r])
    st = _name(
        doc,
        doc.create_symbol_set(
            (220, 100), kind=0, exprs=["FX_PUMP1"], states=[("fxsym", "fx_off"), ("fxsym", "fx_on")]
        ),
        "FxSetTouch",
    )
    doc.set_input_commands(st, touch={"CMD": "FxToggle(FX_PUMP1)"})
    for k in range(3):
        bt = add(
            "FxInButton%d" % (k + 1), "button", (20 + 110 * k, 160, 120 + 110 * k, 190), text="B%d" % (k + 1)
        )
        doc.set_input_commands(bt, touch={"CMD": "FxSet(FX_MODE, %d)" % k})
    _save(doc)

    # ---- FxButtons: dozens of touch / keyboard rows (pgtouch, pgkey) and input objects
    doc = b.blank_page("FxMain", "FxButtons")
    add = _adder(doc)
    for k in range(12):
        x, y = 20 + 80 * (k % 6), 20 + 60 * (k // 6)
        r = add("FxTouch%02d" % (k + 1), "rectangle", (x, y, x + 70, y + 40), colour=0x5080B0)
        doc.set_input_commands(r, touch={"CMD": "FxSet(FX_MODE, %d)" % k})
    for k in range(12):
        x, y = 20 + 80 * (k % 6), 160 + 30 * (k // 6)
        t = add("FxKey%02d" % (k + 1), "text", (x, y, x + 70, y + 16), text="K%d" % (k + 1))
        doc.set_input_commands(
            t, keys=[{"KEY_SEQ": ("ENTER", "F2", "F3")[k % 3], "CMD": "FxSet(FX_MODE, %d)" % (20 + k)}]
        )
    for k in range(8):
        x = 20 + 80 * k
        bt = add("FxBtn%02d" % (k + 1), "button", (x, 240, x + 70, 266), text="Btn%d" % (k + 1))
        doc.set_input_commands(bt, touch={"CMD": 'PageDisplay("FxShapes")'})
    _save(doc)

    # ---- FxTextKinds: the text and display shapes of the render / display tests
    doc = b.blank_page("FxMain", "FxTextKinds", size=(1280, 900), register=False)
    add = _adder(doc)
    add("FxTk0", "rectangle", (20, 20, 60, 40), colour=0x909090)
    add("FxTk1", "text", (80, 20, 200, 36), text="first")
    t = add("FxTxtOffLabel", "text", (220, 20, 400, 36), text="label")  # record 2
    doc.set_text_display(
        t, [("TXTMODE", "0"), ("TXT_A", "FX_PUMP1"), ("TXT_ON", "Running"), ("TXT_OFF", "@(Fx Start)")]
    )
    add("FxLabelOne", "text", (420, 20, 560, 36), text="@(Fx Start)")
    for k in range(3):
        _keys(
            doc,
            add("FxLogin%d" % (k + 1), "text", (20, 60, 140, 76), text="Login..."),
            [("VISIBLE", "FX_MODE = %d" % k)],
        )
        _keys(
            doc,
            add("FxPrint%d" % (k + 1), "text", (160, 60, 280, 76), text="Print..."),
            [("VISIBLE", "FX_MODE = %d" % k)],
        )
    _keys(doc, add("FxLoginGate", "text", (300, 60, 420, 76), text="Login..."), [("VISIBLE", "FxExpanded()")])
    t = add("FxColStaText", "text", (20, 100, 80, 116), text="##")
    doc.set_colour(t, 0xFF0000)  # state 0 is the text's own colour: red
    doc.set_fill_keys(
        t,
        [
            ("COLMODE", "1"),
            ("COL_A", "FX_PUMP1"),
            ("COL_B", "FX_PUMP2"),
            ("COL_STA_0", "0x00ff0000,0x00ff0000"),
            ("COL_STA_1", "0x0000ff00,0x0000ff00"),
        ],
    )
    t = add("FxTouchNumber", "text", (100, 100, 180, 116), text="##.#")
    doc.ensure_property(t, "TXT_STR", "FX_LEVEL1")
    doc.set_input_commands(t, touch={"CMD": "FxSet(FX_MODE, 5)"})
    bt = add("FxIconButton", "button", (200, 96, 260, 126), text="icon")
    assert doc.set_button_face(bt, type="symbol", symbol="fxsym.fx_bitmap")
    for name, rect, keys in (
        ("FxPageGetStr", (20, 140, 200, 156), [("TXTMODE", "4"), ("TXT_STR", 'PageGetStr("FxKey")')]),
        (
            "FxStrLeftover",
            (220, 140, 400, 156),
            [("TXTMODE", "4"), ("TXT_STR", "FX_NAME"), ("TXT_OFF", "####")],
        ),
        ("FxEmptyString", (420, 140, 600, 156), [("TXTMODE", "4"), ("TXT_STR", "")]),
        ("FxQuotedMenu", (620, 140, 800, 156), [("TXTMODE", "4"), ("TXT_STR", '"@(Fx Menu)"')]),
        ("FxQuotedHome", (820, 140, 1000, 156), [("TXTMODE", "4"), ("TXT_STR", '"@(Fx Home)"')]),
    ):
        t = add(name, "text", rect, text="text")
        if name == "FxEmptyString":  # an EMPTY string display: no TXT_OFF, no caption
            doc.set_text_display(t, keys + [("TXT_OFF", "")], caption="")
        else:
            doc.set_text_display(t, keys)
    for name, x, expr in (
        ("FxNumParen", 20, "(FX_LEVEL1):#.#"),
        ("FxNumBang", 160, "FX_LEVEL2:#!"),
        ("FxNumUndefined", 300, "FX_NOT_A_TAG:##.#"),
        ("FxNumField", 440, "FX_LEVEL1.Field:##"),
        ("FxNumOverride", 580, "FX_LEVEL1.Override:##"),
        ("FxNumEquip", 720, "FxPlant.Tank1.Level:###"),
    ):
        # the caption goes in the same edit: a later caption would be re-derived as the mask
        t = add(name, "number", (x, 180, x + 120, 196), text="##.#")
        doc.set_text_display(
            t,
            [("TXTMODE", "3"), ("TXT_STR", expr)],
            caption={"FxNumEquip": "Idle", "FxNumField": "", "FxNumOverride": ""}.get(name),
        )
    doc.set_font(_idx(doc, "FxNumEquip"), face="Lucida Console")
    # numeric displays with no format of their own (CtDraw stores the '####.#' box): an
    # item of equipment the project lacks (caption Idle, Lucida Console), and a field and
    # an override of an undefined tag (empty captions)
    for name, x, expr, caption in (
        ("FxNumIdleRef", 860, "FxNoPlant.Tank9.Level", "Idle"),
        ("FxNumExtField", 1000, "FX_NOT_A_TAG.Field", ""),
        ("FxNumExtOverride", 1140, "FX_NOT_A_TAG.Override", ""),
    ):
        t = add(name, "number", (x, 180, x + 120, 196), text="##.#")
        doc.set_text_display(t, [("TXTMODE", "3"), ("TXT_STR", expr)], caption=caption)
    doc.set_font(_idx(doc, "FxNumIdleRef"), face="Lucida Console")
    # a value whose colour slot is the no-colour sentinel (Transparent): it draws nothing
    from ctgedit.format import colourpalette as CP

    t = add("FxTransparentText", "number", (20, 340, 120, 356), text="####.#")
    doc.ensure_property(t, "TXT_STR", "FX_LEVEL2")
    from ctgedit.format import geometry as G

    assert doc.set_colour_slot(t, G.SLOT_FILL, CP.TRANSPARENT, CP.TRANSPARENT)
    doc.set_colour(add("FxGreyText", "text", (20, 220, 140, 236), text="grey"), 0xCCCCCC)
    doc.set_font(
        add("FxCalibri", "text", (160, 220, 300, 238), text="Calibri twelve"), face="Calibri", size=12
    )
    doc.set_font(add("FxDigit22", "text", (320, 210, 360, 250), text="8"), size=22)
    doc.set_colour(add("FxMagentaM", "text", (380, 220, 400, 236), text="M"), 0xFF00FF)
    doc.set_font(add("FxBold", "text", (420, 220, 560, 236), text="bold"), bold=True)
    doc.set_metadata(_idx(doc, "FxBold"), [("Role", "title")])
    t = add("FxBullets", "text", (580, 220, 760, 236), text="• one • two")
    doc.set_text_display(
        t, [("TXTMODE", "0"), ("TXT_A", "FX_PUMP1"), ("TXT_ON", "• on"), ("TXT_OFF", "• one • two")]
    )
    for k in range(7):  # seven gated captions in one slot
        _keys(
            doc,
            add("FxSlot%d" % k, "text", (150, 263, 431, 279), text="State %d" % k),
            [("VISIBLE", "FX_MODE = %d" % k)],
        )
    for k, face in enumerate(("Arial", "Tahoma", "Verdana", "Times New Roman", "Courier New")):
        doc.set_font(
            add("FxFont%d" % (k + 1), "text", (20 + 150 * k, 300, 160 + 150 * k, 318), text=face),
            face=face,
            size=10 + k,
        )
    _save(doc)

    # ---- FxColours: every colour mode, flat COL_MAXON pairs, greys, effects
    doc = b.blank_page("FxMain", "FxColours", size=(1280, 800), register=False)
    add = _adder(doc)
    red, green, blue = 0x00FF0000, 0x0000FF00, 0x000000FF  # (0x00RRGGBB, as the keys)

    def pair(a, c=None):
        return "0x%08x,0x%08x" % (a, a if c is None else c)

    r = add("FxCm0", "rectangle", (20, 20, 80, 60), colour=0x808080)
    doc.set_fill_keys(r, [("COLMODE", "0"), ("COL_A", "FX_PUMP1"), ("COL_MAXON", pair(green))])
    # state 0 (COL_STA_0, COL_ARR_0) is the shape's own colour: the shape is red to keep it
    r = add("FxCm1", "rectangle", (100, 20, 160, 60), colour=red)
    doc.set_fill_keys(
        r,
        [("COLMODE", "1"), ("COL_A", "FX_PUMP1"), ("COL_B", "FX_PUMP2")]
        + [("COL_STA_%d" % k, pair(c)) for k, c in enumerate((red, green, blue, 0x00FFFFFF))],
    )
    r = add("FxCm2", "rectangle", (180, 20, 240, 60), colour=red)
    doc.set_fill_keys(
        r,
        [
            ("COLMODE", "2"),
            ("COL_ARR", "FX_MODE"),
            ("COL_ARR_0", pair(red)),
            ("COL_ARR_1", pair(green)),
            ("COL_ARR_5", pair(blue)),
        ],
    )
    r = add("FxCm3", "rectangle", (260, 20, 320, 60), colour=0x808080)
    doc.set_fill_keys(
        r,
        [
            ("COLMODE", "3"),
            ("COL_EXP", "FX_LEVEL1"),
            ("COL_THR_0", "10," + pair(green)),
            ("COL_THR_1", "80," + pair(red)),
        ],
    )
    r = add("FxCm4", "rectangle", (340, 20, 400, 60), colour=0x808080)
    doc.set_fill_keys(r, [("COLMODE", "4"), ("COL_EXP", "FX_LEVEL2"), ("COL_THR_0", "50," + pair(blue))])
    r = add("FxFilCol", "rectangle", (420, 20, 460, 120), colour=0x808080)
    doc.set_fill_keys(
        r, [("FIL", "FX_LEVEL1"), ("FILMINPC", "0"), ("FILMAXPC", "100"), ("FILCOL", pair(blue))]
    )
    r = add("FxFlatPair", "rectangle", (480, 20, 540, 60), colour=0x969696)
    doc.set_fill_keys(r, [("COL_MAXON", pair(0x00969696))])
    _keys(doc, r, [("SZX", "FX_LEVEL1"), ("SZXMINPC", "0"), ("SZXMAXPC", "100"), ("DISABLED", "FX_MODE = 0")])
    e = add("FxEllipseMaxon", "ellipse", (560, 20, 620, 60), colour=0x808080)
    doc.set_fill_keys(e, [("COL_MAXON", pair(0x00FFCC00))])
    _keys(doc, e, [("MVX", "FX_LEVEL1"), ("MVXMINPT", "0"), ("MVXMAXPT", "100")])
    p = add("FxPennant", "polygon", None, [(20, 100), (80, 115), (20, 130)], colour=0xFFFFFF)
    doc.set_fill_keys(p, [("COL_A", "TRUE"), ("COL_MAXON", pair(0x00DDDDDD))])
    doc.set_colour(p, 0x969696, line=True)
    p = add("FxPennantShadow", "polygon", None, [(22, 102), (82, 117), (22, 132)], colour=0xC0C0C0)
    doc.set_fill_keys(p, [("COL_A", "FALSE"), ("COL_MAXON", pair(0x00FF99CC))])
    for k, (grey, uses) in enumerate(((0x808080, 3), (0xA0A0A0, 2), (0x404040, 1), (0xC8C8C8, 4))):
        for u in range(uses):
            add(
                "FxGrey%d_%d" % (k + 1, u + 1),
                "rectangle",
                (100 + 50 * u, 100 + 40 * k, 140 + 50 * u, 130 + 40 * k),
                colour=grey,
            )
    a = add("FxArrA", "rectangle", (360, 100, 400, 130), colour=0x808080)
    c = add("FxArrB", "ellipse", (410, 100, 440, 130), colour=0x808080)
    g = _name(doc, doc.group_objects([a, c]), "FxArrGroup")
    doc.set_fill_keys(
        g, [("COLMODE", "2"), ("COL_ARR", "FX_MODE"), ("COL_ARR_0", pair(red)), ("COL_ARR_1", pair(green))]
    )
    for k, (name, effect, depth) in enumerate(
        (
            ("FxHollowRaised", 1, 2),
            ("FxHollowLowered", 2, 2),
            ("FxEmbossed1", 4, 1),
            ("FxEmbossed3", 4, 3),
            ("FxHollowShadow", 3, 3),
        )
    ):
        i = add(name, "rectangle_outline", (20 + 90 * k, 300, 100 + 90 * k, 340))
        doc.set_effect(i, effect, depth)
    bt = add("FxTargetButton", "button", (20, 360, 120, 390), text="Target")
    doc.set_button_face(bt, type="target", mode="TARGET")
    bt = add("FxXpButton", "button", (140, 360, 260, 390), text="XP")
    doc.set_font(bt, face="Segoe UI", size=12)
    pp = add("FxWidePipe", "pipe", None, [(300, 370), (400, 370), (400, 420)])
    doc.set_line_width(pp, 3)
    doc.set_pipe_colour(pp, 0x0000FFFF)
    add("FxValueFrame", "rectangle", (440, 360, 540, 380), colour=0xFFFFFF)
    _keys(doc, add("FxValueText", "number", (444, 362, 536, 378), text="##.#"), [("TXT_STR", "FX_LEVEL3")])
    i = add("FxGradientTB", "rectangle", (560, 360, 680, 400), colour=0x999999)
    doc.set_gradient(i, 1, 0xBFBFBF)
    i = add("FxRounded", "rectangle", (700, 360, 800, 400), colour=0x6090C0)
    assert doc.set_corner_radius(i, 8)
    _save(doc)

    # ---- FxMembers: group / genie member access
    doc = b.blank_page("FxMain", "FxMembers", size=(1024, 768), register=False)
    add = _adder(doc)
    a = add("FxPairA", "rectangle", (20, 20, 60, 50), colour=0x507050)
    c = add("FxPairB", "text", (20, 54, 80, 70), text="pair")
    _name(doc, doc.group_objects([a, c]), "FxPair")  # record 0
    _place(doc, "FxMixed", "fxparts", "fx_mixed", (120, 20), {"Tag": "FX_PUMP1"})
    r = add("FxInputOwner", "rectangle", (260, 20, 320, 60), colour=0x705050)
    doc.set_input_commands(
        r, touch={"CMD": "FxToggle(FX_PUMP1)"}, keys=[{"KEY_SEQ": "ENTER", "CMD": "FxSet(FX_MODE, 1)"}]
    )
    add("FxFree1", "rectangle", (340, 20, 400, 60), colour=0x505070)
    add("FxFree2", "rectangle", (420, 20, 480, 60), colour=0x505070)
    g = _place(doc, "FxInnerGenie", "fxgenie", "fx_plain", (20, 120))
    r = add("FxBesideGenie", "rectangle", (80, 120, 120, 140), colour=0x303030)
    _name(doc, doc.group_objects([g, r]), "FxGenieInGroup")
    _place(doc, "FxNestedHost", "fxnest", "n2", (200, 120), {"Name": "Host"})
    _save(doc)

    # ---- FxMany: more than 50 selectable roots
    doc = b.blank_page("FxMain", "FxMany", size=(1280, 800), register=False)
    add = _adder(doc)
    for n in range(8):
        _place(
            doc,
            "FxManyValve%d" % (n + 1),
            "fxgenie",
            "fx_valve",
            (20 + 80 * (n % 4), 20 + 70 * (n // 4)),
            {"TagPrefix": "FX_V%d" % (n + 1)},
        )
    for n in range(12):
        x, y = 360 + 70 * (n % 6), 20 + 60 * (n // 6)
        i = add("FxSame%02d" % (n + 1), "rectangle", (x, y, x + 50, y + 40), colour=0x6699CC)
        doc.set_line_width(i, 2)
    for n in range(10):
        doc.set_font(
            add(
                "FxManyText%02d" % (n + 1),
                "text",
                (20 + 120 * (n % 5), 200 + 30 * (n // 5), 130 + 120 * (n % 5), 218 + 30 * (n // 5)),
                text="Text %d" % (n + 1),
            ),
            face=("Arial", "Tahoma")[n % 2],
            size=10 + n % 3,
        )
    for k, kind in enumerate(("rectangle", "ellipse", "rectangle")):
        add(
            "FxStack%s" % "ABC"[k],
            kind,
            (700 - 5 * k, 400 - 5 * k, 780 - 5 * k, 460 - 5 * k),
            colour=0x303030 + 0x404040 * k,
        )
    for g in range(3):
        a = add(
            "FxManyGroup%dA" % (g + 1), "rectangle", (20 + 120 * g, 300, 70 + 120 * g, 340), colour=0x996633
        )
        c = add(
            "FxManyGroup%dB" % (g + 1), "ellipse", (75 + 120 * g, 300, 115 + 120 * g, 340), colour=0x339966
        )
        _name(doc, doc.group_objects([a, c]), "FxManyGroup%d" % (g + 1))
    for n in range(6):
        _place(
            doc,
            "FxManyPump%d" % (n + 1),
            "fxgenie",
            "fx_pump",
            (420 + 110 * (n % 3), 520 + 110 * (n // 3)),
            {"Tag": "FX_PUMP%d" % (n % 3 + 1), "Label": "Pump %d" % (n + 1)},
        )
    for n in range(2):
        _place(
            doc,
            "FxEquipGenie%d" % (n + 1),
            "fxparts",
            "fx_alarm",
            (20 + 100 * n, 400),
            {"Equipment": "FxPlant.Tank%d" % (n + 1)},
        )
    add("FxPlainRect", "rectangle", (20, 600, 80, 640), colour=0xAAAA00)
    add("FxPlainEllipse", "ellipse", (100, 600, 160, 640), colour=0x00AAAA)
    add("FxPlainLine", "line", (180, 600, 260, 640))
    add("FxPlainText", "text", (280, 600, 380, 616), text="plain")
    add("FxPlainPolygon", "polygon", None, [(20, 680), (80, 680), (50, 730)], colour=0xAA00AA)
    add("FxPlainPipe", "pipe", None, [(100, 680), (200, 680), (200, 740)])
    add("FxValveLabel", "text", (300, 680, 400, 696), text="valve row")
    _save(doc)

    # ---- FxParts: an instance of each fxparts genie
    doc = b.blank_page("FxMain", "FxParts", size=(1600, 1000), register=False)
    _place(
        doc,
        "FxManyParams",
        "fxparts",
        "fx_many",
        (20, 20),
        {"Tag": "FX_PUMP1", "P01": "first", "P05": "fifth", "P41": "last"},
    )
    i = _place(doc, "FxOrder1", "fxparts", "fx_order", (340, 20), {"MixedCase": "mixed", "Title": "Order"})
    _place(doc, "FxNone1", "fxparts", "fx_none", (500, 20))
    _place(doc, "FxDefault1", "fxparts", "fx_default", (560, 20), {"Tag": "FX_PUMP2"})
    _place(doc, "FxGoto1", "fxparts", "fx_goto", (700, 20), {"Page": "FxShapes"})
    _place(doc, "FxInputs1", "fxparts", "fx_inputs", (820, 20), {"Tag": "FX_MODE"})
    _place(doc, "FxAnList1", "fxparts", "fx_anlist", (20, 200), {"Page": "FxParts"})
    _place(doc, "FxNamed1", "fxparts", "fx_named", (360, 200), {"Name": "Tank one", "Tag": "FX_T201"})
    _place(doc, "FxOffset1", "fxparts", "fx_offset", (520, 200), {"Tag": "FX_PUMP3"})
    _place(doc, "FxPoints1", "fxparts", "fx_points", (640, 200), {"Tag": "FX_MODE"})
    _place(doc, "FxGroupRoot1", "fxparts", "fx_grouproot", (740, 200), {"Tag": "FX_PUMP1"})
    _place(doc, "FxDummy1", "fxparts", "fx_dummy", (820, 200), {"Tag": "FX_PUMP1"})
    _place(doc, "FxDummy2", "fxparts", "fx_dummy", (920, 200), {"Tag": "FX_PUMP2"})
    _place(doc, "FxHandles1", "fxparts", "fx_handles", (1020, 200), {"Tag": "FX_PUMP1"})
    for n in range(3):
        _place(doc, "FxRow%d" % (n + 1), "fxparts", "fx_row", (20, 400 + 80 * n), {"RowNum": str(n + 1)})
    _place(doc, "FxMixed1", "fxparts", "fx_mixed", (400, 400), {"Tag": "FX_PUMP2"})
    _place(doc, "FxAlarm1", "fxparts", "fx_alarm", (520, 400), {"Equipment": "FxPlant.Tank1"})
    _place(doc, "FxPicture1", "fxparts", "fx_picture", (640, 400), {"Tag": "FX_LEVEL1"})
    _place(doc, "FxValveLeft1", "fxparts", "fx_valve_left", (760, 400), {"Tag": "FX_V1_ON"})
    _place(doc, "FxPipe1", "fxparts", "fx_pipe", (840, 400), {"Tag": "FX_LEVEL2"})
    _place(doc, "FxSets1", "fxparts", "fx_sets", (940, 400), {"Tag": "FX_PUMP1"})
    _place(doc, "FxStale1", "fxparts", "fx_stale", (1040, 400), {"Tag": "FX_PUMP1", "Equip": "FxPlant.Pump1"})
    for n in range(3):
        _place(
            doc,
            "FxLoop%d" % (n + 4),
            "fxparts",
            "fx_loop",
            (1140 + 100 * n, 600),
            {"LOOP": "FX_LOOP_%d" % (n + 4)},
        )
    _place(doc, "FxSetpoint1", "fxparts", "fx_setpoint", (1140, 700), {"TAG": "FX_T201_SP"})
    _place(doc, "FxNamedBox", "fxmore", "fx_grouped", (1140, 400), ext="ctl")
    _place(doc, "FxSymSym", "fxmore", "fx_symsym", (1240, 400), ext="ctl")
    _place(doc, "FxBroken1", "fxparts", "fx_broken", (20, 700), {"Tag": "FX_PUMP1", "Label": "b"})
    _place(
        doc,
        "FxListy1",
        "fxparts",
        "fx_listy",
        (200, 700),
        {"Mode": "FX_MODE_AUTO", "Flag": "TRUE", "Tag": "FX_PUMP2"},
    )
    _place(
        doc,
        "FxInsertObject1",
        "fxparts",
        "fx_insert_object",
        (420, 700),
        {"GUID": "{00000000-0000-0000-0000-000000000000}", "EVENTCLASS": "FxEvents", "W": "100", "H": "50"},
    )
    _place(doc, "FxAgitator1", "fxparts", "fx_agitator_mixer", (700, 700), {"Tag": "FX_PUMP1"})
    _place(doc, "FxHiddenBase1", "fxparts", "fx_hidden_base", (900, 700), {"Tag": "FX_PUMP2"})
    _place(doc, "FxLinkText1", "fxparts", "fx_linktext", (1000, 700))
    _place(doc, "FxStale2", "fxparts", "fx_stale_note", (1100, 800), {"Tag": "FX_PUMP3"})
    _save(doc)
    # fx_stale changes after FxParts placed it: FxStale1 is an instance older than its element
    e = ElementDocument.open_element(os.path.join(b.proj("FxLib"), "fxparts.ctm"), "fx_stale")
    e.delete_objects([_idx(e, "FxStaleEquip")])
    e.create_object("ellipse", (44, 0, 64, 20), colour=0xA05040)
    with _later():
        e.save()
    e = ElementDocument.open_element(os.path.join(b.proj("FxLib"), "fxparts.ctm"), "fx_stale_note")
    e.create_object("ellipse", (44, 0, 64, 20), colour=0x40A050)
    with _later():
        e.save()
    libraries_changed()

    # ---- template pages of the second set
    doc = b.template_page("FxMain", "FxTplMarker", "fx_alt.marker@-XGA.<ctt")
    _name(doc, doc.create_object("rectangle", (100, 200, 200, 260), colour=0x408080), "FxOnMarker")
    _save(doc)
    doc = b.template_page("FxMain", "FxTplSets", "fx_alt.sets@-XGA.<ctt")
    _name(doc, doc.create_object("text", (100, 200, 300, 216), text="sets"), "FxOnSets")
    _save(doc)
    doc = b.template_page("FxMain", "FxTplFlash", "fx_alt.flash@-XGA.<ctt")
    _name(doc, doc.create_object("rectangle", (100, 200, 200, 260), colour=0x808080), "FxSteady")
    _save(doc)
    doc = b.template_page("FxMain", "FxTplStale", "fx_alt.stale@-XGA.<ctt")
    _name(doc, doc.create_object("rectangle", (100, 200, 200, 260), colour=0x804080), "FxOwn")
    _save(doc)
    e = ElementDocument.open_element(os.path.join(b.proj("FxStyle"), "fx_alt.ctt"), "stale@-XGA")
    e.delete_objects([_idx(e, "FxTplStaleGroup")])
    e.set_text(_idx(e, "FxTplCaption"), "FxAfter")
    with _later():
        e.save()
    libraries_changed()
    doc = b.template_page("FxMain", "FxTplLast", TPL_NORMAL)
    _place(doc, "FxBeforeTpl", "fxparts", "fx_many", (100, 300), {"Tag": "FX_PUMP1"})
    doc.reorder([0], "front")  # the template's records: stored last, after the genie
    _save(doc)
    # FxTplArrows: a genie, then arrows@-XGA stored last (its four bitmaps with touch)
    doc = b.template_page("FxMain", "FxTplArrows", "fx_alt.arrows@-XGA.<ctt", register=False)
    _place(doc, "FxArrowsPipe", "fxparts", "fx_lost_pipe", (100, 300), {"Tag": "FX_LEVEL1"})
    doc.reorder([0], "front")
    _save(doc)
    e = ElementDocument.open_element(os.path.join(b.proj("FxLib"), "fxparts.ctm"), "fx_lost_pipe")
    e.delete_objects([_idx(e, "FxLostPipe")])
    e.create_object("rectangle", (0, 30, 60, 60), colour=0x506080)
    with _later():
        e.save()
    libraries_changed()
    doc = b.template_page("FxMain", "FxHd1080", "fx_hd.normal@HD1080.<ctt")
    _place(doc, "FxHdPump", "fxgenie", "fx_pump", (200, 200), {"Tag": "FX_PUMP1", "Label": "HD pump"})
    _save(doc)
    doc = b.template_page("FxMain", "FxHd768", "fx_hd.normal@HD768.<ctt")
    _name(doc, doc.create_object("rectangle", (100, 100, 200, 160), colour=0x407040), "FxHdRect")
    _save(doc)

    # ---- FxAssoc: ?association? tokens on a page, the super genie host
    doc = b.blank_page("FxMain", "FxAssoc")
    add = _adder(doc)
    bt = add("FxAssocButton", "button", (20, 20, 140, 50), text="?Tag?")
    doc.set_input_commands(bt, touch={"CMD": "FxSet(?Tag?, 1)"})
    _name(
        doc,
        doc.create_symbol_set(
            (160, 20), kind=0, exprs=["?DIGITAL 1?"], states=[("fxsym", "fx_off"), ("fxsym", "fx_on")]
        ),
        "FxAssocSet",
    )
    r = add("FxAssocTip", "rectangle", (220, 20, 280, 60), colour=0x606060)
    doc.ensure_property(r, "COMMENT", "?Tag? tip")
    doc.set_input_commands(r, touch={"CMD": "FxToggle(?DIGITAL 2?)"})
    add("FxEquipRef", "text", (300, 20, 460, 36), text="?__EquipmentRef?")
    _place(doc, "FxDetailHost", "fxparts", "fx_popper2", (20, 100), {"Tag": "FX_PUMP1"})
    _save(doc)
    doc = EditableDocument.open(doc.path)
    doc.set_page_record({"TITLE": "Fx genie host", "COMMENT": "association tokens"})
    _save(doc)

    # ---- FxMaster: a registered master page with its page events and page keys
    doc = b.blank_page("FxMain", "FxMaster")
    _name(doc, doc.create_object("text", (20, 20, 200, 36), text="master"), "FxMasterText")
    _save(doc)
    doc = EditableDocument.open(doc.path)
    doc.set_page_keyboard_commands([{"KEY_SEQ": "LBUTTON_UP", "CMD": MASTER_KEY_CMD}])
    doc.set_page_record(
        {
            "TITLE": "@(Fx Start)",
            "COMMENT": "a master page",
            "CON_TYPE": "FxMaster",
            "PARENT": "FxShapes",
            "PREV_PAGE": "FxAnim",
            "NEXT_PAGE": "FxGenies",
            "IGNOREVQ": "1",
            "AREA": "2",
            "SCANTIME": "250",
            "CINHERIT": "TRUE",
            "READY_FUNC": "FxSet(FX_MODE, 1)",
            "EXIT_FUNC": "FxSet(FX_MODE, 0)",
        }
    )
    _save(doc)

    # ---- FxComposites: instances of every composite template
    doc = b.blank_page("FxMain", "FxComposites", size=(1280, 800), register=False)
    for n, (tpl, at, params) in enumerate(
        (
            (
                "fx_valve_c",
                (20, 20),
                {"Equipment": "FxPlant.Valve1", "ValveType": "Control", "DisplayAlarm": "true"},
            ),
            ("fx_valve_c", (200, 20), {"Equipment": "FxPlant.Valve2"}),
            (
                "fx_drive_c",
                (380, 20),
                {
                    "Equipment": "FxPlant.Pump1",
                    "MEO": "1",
                    "DisplayLabel": "Above",
                    "Label": "Spray",
                    "DriveType": "Sprayer",
                    "DisplayStatus": "Inside Right",
                    "Orientation": "Right",
                },
            ),
            ("fx_drive_c", (560, 20), {"Equipment": "FxPlant.Pump2"}),
            ("fx_switch_c", (20, 260), {"Equipment": "FxPlant.Pump3", "Size": "Large"}),
            ("fx_switch_c", (200, 260), {"Equipment": "FxPlant.Pump1", "Size": "Small"}),
            (
                "fx_meter_c",
                (380, 260),
                {
                    "Equipment": "FxPlant.Tank2",
                    "DisplayTrend": "true",
                    "DisplayOutput": "true",
                    "DisplaySecond": "true",
                    "Equipment2": "FxPlant.Tank1",
                    "ReferenceEquipment": "FxPlant",
                },
            ),
            ("tank", (560, 260), {"Equipment": "FxPlant.Tank3", "Label": "Tank three"}),
            # FxComp9: fxparts.fx_alarm (its indicator on; Equipment from the template's, a
            # dotted path), fx_sets (two symbol sets) and fx_bowtie, both unbound
            ("fx_status_c", (740, 260), {"Equipment": "FxPlant.Area.FxSpray02"}),
        )
    ):
        guid = FX_TANK_GUID if tpl == "tank" else COMPOSITE_GUIDS[tpl]
        i = doc.paste_composite_genie(guid, at, params)
        _name(doc, i, "FxComp%d" % (n + 1))
    _save(doc)

    # ---- FxReturned: placeholders of a genie library that was gone at save and is back
    shutil.copyfile(os.path.join(b.proj("FxLib"), "fxgenie.ctm"), os.path.join(main, "fxback.ctm"))
    libraries_changed()
    doc = b.blank_page("FxMain", "FxReturnedSource", register=False)
    for n in range(2):
        _place(
            doc,
            "FxBackValue%d" % (n + 1),
            "fxback",
            "fx_value",
            (20, 20 + 60 * n),
            {"Description": "Fx Brann %d" % (n + 1)},
        )
        _place(doc, "FxBackPlain%d" % (n + 1), "fxback", "fx_plain", (200, 20 + 60 * n))
    _save(doc)
    os.remove(os.path.join(main, "fxback.ctm"))
    libraries_changed()
    doc = EditableDocument.open(os.path.join(main, "FxReturnedSource.ctg"))
    doc.placeholders_for_missing_libraries()
    doc._fx_unregistered = True
    _save(doc, os.path.join(main, "FxReturned.ctg"))
    os.remove(os.path.join(main, "FxReturnedSource.ctg"))
    shutil.copyfile(os.path.join(b.proj("FxLib"), "fxgenie.ctm"), os.path.join(main, "fxback.ctm"))
    libraries_changed()


def _thumb(w: int, h: int, colour: int) -> bytes:
    """A small TH thumbnail of our own: a filled box with a dark border."""
    from ctgedit.format import thumbnail

    px = bytearray()
    for y in range(h):
        for x in range(w):
            c = 0x202020 if x in (0, w - 1) or y in (0, h - 1) else colour
            px += bytes((c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF, 0))
    return thumbnail.encode(bytes(px), w, h)


def build_main_extra(b: Build) -> None:
    """FxMain, the rest of the second set (all unregistered): Cicode objects with symbols,
    motion keys, ActiveX controls, styled shapes, alarm indicator groups, symbol variants,
    work-area pages, a page with a thumbnail."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import records as R
    from ctgedit.io.activex import PROCESS_ANALYST_CLSID
    from ctgedit.ui.simulate.controls import TEXT_CONTROLS

    # ---- FxCicode: Cicode objects with and without a symbol
    doc = b.blank_page("FxMain", "FxCicode", size=(800, 600), register=False)
    for k, (name, symbol, cmd) in enumerate(
        (
            ("FxCicodeIcon", "", "FxToggle(FX_PUMP1)"),
            ("FxCicodeDefault", "cicode.default", "FxSet(FX_MODE, 1)"),
            ("FxCicodePage", "fxicons16.page", 'PageDisplay("FxShapes")'),
            ("FxCicodePriority", "fxprio.fx_p1", "FxSet(FX_MODE, 2)"),
            ("FxCicodePoint", "", ""),
        )
    ):
        i = _name(doc, doc.create_object("cicode_object", (20 + 80 * k, 20, 40 + 80 * k, 40)), name)
        if cmd or symbol:
            doc.set_cicode_object(i, command=cmd or None, symbol=symbol or None)
    for k, raw in enumerate(("<default>", "<deAlarm>")):
        i = _name(
            doc,
            doc.create_object("cicode_object", (20 + 80 * k, 100, 40 + 80 * k, 120)),
            "FxCicodeAngle%d" % (k + 1),
        )
        doc.set_cicode_object(i, command="FxSet(FX_MODE, %d)" % (3 + k))
        doc.ensure_property(i, "SYMBOL", raw)
    _save(doc)

    # ---- FxMotion: rotation, scaling, movement and slider keys
    doc = b.blank_page("FxMain", "FxMotion", size=(1024, 768), register=False)
    add = _adder(doc)
    for name, kind, rect, keys in (
        (
            "FxRotExpress",
            "rectangle",
            (20, 20, 80, 60),
            [("ROT", "FX_SPEED"), ("ROTMINPC", "0"), ("ROTMAXPC", "135"), ("ROTYORG", "1")],
        ),
        (
            "FxRotSlider",
            "rectangle",
            (100, 20, 160, 60),
            [
                ("ROTSL", "FX_SPEED"),
                ("ROTSLMODE", "1"),
                ("ROTMODE", "1"),
                ("ROTXORG", "30"),
                ("ROTYORG", "43"),
                ("ROTMINPC", "135"),
                ("ROTMAXPC", "135"),
            ],
        ),
        (
            "FxRotBoom",
            "rectangle",
            (180, 20, 360, 40),
            [
                ("ROT", "FX_LEVEL1"),
                ("ROTMODE", "1"),
                ("ROTXORG", "168"),
                ("ROTYORG", "-3"),
                ("ROTMINPC", "0"),
                ("ROTMAXPC", "45"),
            ],
        ),
        (
            "FxRotClaw",
            "rectangle",
            (380, 20, 420, 60),
            [
                ("ROT", "FX_LEVEL2"),
                ("ROTMODE", "1"),
                ("ROTXORG", "40"),
                ("ROTYORG", "0"),
                ("ROTMINPC", "0"),
                ("ROTMAXPC", "-90"),
            ],
        ),
        (
            "FxRotTop",
            "rectangle",
            (440, 20, 480, 60),
            [("ROT", "FX_LEVEL3"), ("ROTMINPC", "0"), ("ROTMAXPC", "360"), ("ROTYORG", "-1")],
        ),
        (
            "FxSzxGrow",
            "rectangle",
            (20, 100, 80, 140),
            [("SZX", "FX_LEVEL1"), ("SZXMINPC", "0"), ("SZXMAXPC", "400"), ("SZXORG", "-1")],
        ),
        (
            "FxSzyMove",
            "rectangle",
            (100, 100, 160, 140),
            [
                ("SZY", "FX_LEVEL2"),
                ("SZYORG", "-1"),
                ("MVY", "FX_LEVEL2"),
                ("MVYMINPT", "0"),
                ("MVYMAXPT", "-4000"),
            ],
        ),
        (
            "FxSzyCustom",
            "rectangle",
            (180, 100, 240, 140),
            [
                ("SZY", "FX_LEVEL3"),
                ("SZYMODE", "1"),
                ("SZYORG", "65"),
                ("SZYMINPC", "0"),
                ("SZYMAXPC", "100"),
            ],
        ),
        (
            "FxSliderHV",
            "rectangle",
            (260, 100, 300, 140),
            [
                ("MVXSL", "FX_SPEED"),
                ("MVXSLMODE", "4097"),
                ("MVX", "FX_SPEED"),
                ("MVXMINPT", "0"),
                ("MVXMAXPT", "300"),
                ("MVYSL", "FX_LEVEL1"),
                ("MVY", "FX_LEVEL1"),
                ("MVYMINPT", "0"),
                ("MVYMAXPT", "200"),
                ("COMMENT", "@(Fx Start)<User>"),
            ],
        ),
        (
            "FxTplLikeRect",
            "rectangle",
            (320, 100, 380, 140),
            [
                ("MVX", "FX_LEVEL1"),
                ("MVXMINPT", "0"),
                ("MVXMAXPT", "500"),
                ("SZX", "FX_LEVEL2"),
                ("SZXMINPC", "0"),
                ("SZXMAXPC", "100"),
            ],
        ),
    ):
        _keys(doc, add(name, kind, rect, colour=0x608090), keys)
    for k in range(3):  # gauge needles turning 0..360 about their bottom end
        _keys(
            doc,
            add("FxNeedle%d" % (k + 1), "line", (500 + 60 * k, 200, 500 + 60 * k, 140)),
            [("ROT", "FX_LEVEL%d" % (k + 1)), ("ROTMINPC", "0"), ("ROTMAXPC", "360"), ("ROTYORG", "1")],
        )
    bt = add("FxNavButton", "button", (20, 200, 140, 230), text="Back")
    _keys(
        doc,
        bt,
        [
            ("DISABLED", 'PageInfo(6)=""'),
            ("DISMODE", "1"),
            ("GREY_STYLE", "GRAY_HIDE"),
            ("COMMENT", "@(Fx Back)"),
        ],
    )
    s = _place(doc, "FxSymRotate", "fxsym", "fx_box", (160, 200), ext="ctl")
    _keys(doc, s, [("ROT", "FX_SPEED"), ("ROTMINPC", "0"), ("ROTMAXPC", "180")])
    s = _place(doc, "FxSymStretched", "fxsym", "fx_box", (240, 200), ext="ctl")
    doc.set_object_rect(s, (240, 200, 280, 245))  # local scale (1, 1.5)
    _save(doc)

    # ---- FxActiveX: 40 controls of the made-up CLSID, a Process Analyst-like and a text
    # box record (the CLSIDs the code knows), an unnamed one
    doc = b.blank_page("FxMain", "FxActiveX", size=(1280, 800), register=False)
    for k in range(40):
        x, y = 20 + 120 * (k % 10), 20 + 110 * (k // 10)
        name = "FxCtl%02d" % (k + 1)
        po = R.new_record(
            "activex",
            handle=doc.next_handle(),
            rect1=(x, y, x + 100, y + 90),
            props=[("ACTIVEXEXT_OBJNAME", name)],
            properties=[("Value", str(k)), ("Caption", name)],
        )
        _name(doc, doc.add_object(po), name)
    textbox = next(iter(TEXT_CONTROLS))
    for name, clsid, prog, props in (
        ("FxAnalyst", PROCESS_ANALYST_CLSID, "FxAnalyst.Chart", [("ACTIVEXEXT_PANAME", "FxAnalyst")]),
        ("FxTextBox", textbox, "FxText.Box", [("ACTIVEXEXT_OBJNAME", "FxTextBox")]),
        (None, R.FX_CONTROL_CLSID, R.FX_CONTROL_PROGID, []),
    ):
        po = R.to_po(
            R.activex(
                handle=doc.next_handle(),
                rect1=(20, 480, 220, 560),
                clsid=clsid,
                prog_id=prog,
                props=props,
                properties=[("Text", "fx")],
            )
        )
        i = doc.add_object(po)
        if name:
            _name(doc, i, name)
    _save(doc)

    # ---- FxStyles: 48 styled shapes (widths, effects, hollow / filled)
    doc = b.blank_page("FxMain", "FxStyles", size=(1280, 800), register=False)
    add = _adder(doc)
    n = 0
    for kind in ("rectangle", "rectangle_outline", "ellipse", "ellipse_outline"):
        for width in (1, 2, 3, 4):
            for effect in (0, 1, 3):
                x, y = 20 + 70 * (n % 12), 20 + 70 * (n // 12)
                i = add("FxStyle%02d" % (n + 1), kind, (x, y, x + 50, y + 40), colour=0x406080 + 0x080808 * n)
                doc.set_line_width(i, width)
                if effect:
                    doc.set_effect(i, effect, 2)
                n += 1
    _save(doc)

    # ---- FxAlarms: 30 alarm indicator groups
    doc = b.blank_page("FxMain", "FxAlarms", size=(1280, 800), register=False)
    add = _adder(doc)
    for k in range(30):
        x, y = 20 + 120 * (k % 10), 20 + 120 * (k // 10)
        a = add("FxAiBox%02d" % (k + 1), "rectangle", (x, y, x + 60, y + 40), colour=0x708090)
        t = add("FxAiText%02d" % (k + 1), "text", (x, y + 44, x + 80, y + 60), text="AI %d" % k)
        g = _name(doc, doc.group_objects([a, t]), "FxAi%02d" % (k + 1))
        # every one on: widths 1 to 3, paddings 0 to 3, the flag on every other; the
        # last two are status lights (inside, width 15, no flag)
        light = k >= 28
        _alarm_on(
            doc,
            g,
            "FxPlant.Tank%d" % (k % 3 + 1),
            width=15 if light else 1 + k % 3,
            padding=k % 4,
            inside=light,
            flag_on=bool(k % 2) and not light,
        )
    _save(doc)

    # ---- FxSymbols2: symbol variants
    doc = b.blank_page("FxMain", "FxSymbols2", size=(1024, 768), register=False)
    i = _place(doc, "FxHiddenEmbedded", "fxsym", "fx_group", (20, 20), ext="ctl", linked=False)
    doc.ensure_property(i, "VISIBLE", "0")
    i = _place(doc, "FxVisibleGroupSym", "fxmore", "fx_grouped", (120, 20), ext="ctl")
    doc.ensure_property(i, "VISIBLE", "FX_PUMP1")
    _place(doc, "FxThumbSymbol", "thumbs", "pointer2_e_grey", (220, 20), ext="ctl")
    _place(doc, "FxPointerA", "fxsym", "fx_bitmap", (260, 20), ext="ctl")
    _place(doc, "FxPointerB", "fxmore", "fx_bitmap2", (290, 20), ext="ctl")
    _place(doc, "FxCp1252Symbol", "fxmore", CP1252_SYMBOL, (320, 20), ext="ctl")
    _place(doc, "FxIconHelp", "icons", "help1", (360, 20), ext="ctl")
    _name(
        doc,
        doc.create_symbol_set(
            (20, 100),
            kind=0,
            exprs=["FX_PUMP2"],
            states=[("lights_square_small", "grey"), ("lights_square_small", "red")],
        ),
        "FxSquareSet",
    )
    _name(
        doc,
        doc.create_symbol_set((80, 100), kind=0, exprs=["FX_PUMP3"], states=[("icons", "help1"), None]),
        "FxHalfSet",
    )
    for k in range(4):  # free bitmaps: a locked one, one with metadata
        i = _name(
            doc,
            doc.add_bitmap_object(
                (20 + 40 * k, 200, 52 + 40 * k, 232), _rgba(32, 32, 0x40A0E0 + 0x101000 * k), 32, 32
            ),
            "FxBmp%d" % (k + 1),
        )
        if k == 1:
            doc.set_locked([i], True)
        elif k == 2:
            doc.set_metadata(i, [("Role", "picture")])
    _save(doc)

    # ---- FxWork: a 1366 x 746 page with content below the page; FxWide: 1920 x 1080
    # with markers far left and right and a Cicode object below the page
    doc = b.blank_page("FxMain", "FxWork", size=(1366, 746), register=False)
    add = _adder(doc)
    for k in range(6):
        add("FxBelow%d" % (k + 1), "rectangle", (40 + 200 * k, 800, 200 + 200 * k, 900), colour=0x8090A0)
    _place(doc, "FxWorkPump", "fxgenie", "fx_pump", (100, 100), {"Tag": "FX_PUMP1", "Label": "W"})
    a = add("FxWorkA", "rectangle", (400, 100, 460, 140), colour=0x605040)
    c = add("FxWorkB", "ellipse", (470, 100, 510, 140), colour=0x406050)
    _name(doc, doc.group_objects([a, c]), "FxWorkGroup")
    add("FxWorkMove", "rectangle", (600, 300, 700, 360), colour=0x507090)
    _save(doc)
    doc = b.blank_page("FxMain", "FxWide", size=(1920, 1080), register=False)
    _name(doc, _cleared_marker(doc, (-847, 400, -846, 401)), "FxMarkLeft")
    _name(doc, _cleared_marker(doc, (3042, 400, 3043, 401)), "FxMarkRight")
    i = _name(doc, doc.create_object("cicode_object", (300, 1150, 320, 1170)), "FxIconBelow")
    doc.set_cicode_object(i, command="FxSet(FX_MODE, 7)")
    _name(doc, doc.create_object("rectangle", (800, 400, 1000, 600), colour=0x909090), "FxWideRect")
    _save(doc)
    doc = b.template_page("FxMain", "FxUnderlay", "fx_hd.normal@HD768.<ctt", register=False)
    add = _adder(doc)
    for k in range(24):
        x, y = 40 + 110 * (k % 12), 200 + 140 * (k // 12)
        add(
            "FxOpaque%02d" % (k + 1),
            ("rectangle", "ellipse")[k % 2],
            (x, y, x + 30 + 5 * (k % 5), y + 25 + 5 * (k % 4)),
            colour=0x305070 + 0x050505 * k,
        )
    _save(doc)

    # ---- FxThumb: a small page with a stored thumbnail of our own
    doc = b.blank_page("FxMain", "FxThumb", size=(400, 300), register=False)
    _name(doc, doc.create_object("rectangle", (40, 40, 360, 260), colour=0x3070B0), "FxThumbRect")
    doc.parsed.page.thumbnail = _thumb(80, 60, 0x3070B0)
    _save(doc)
    # metadata on more than 20 records, styled shapes and a styled text among them
    doc = EditableDocument.open(os.path.join(b.proj("FxMain"), "FxMany.ctg"))
    doc._fx_unregistered = True
    for k in range(12):
        doc.set_metadata(
            _idx(doc, "FxSame%02d" % (k + 1)), [("Equipment", "FxPlant.Tank1"), ("Index", str(k))]
        )
    for k in range(10):
        doc.set_metadata(_idx(doc, "FxManyText%02d" % (k + 1)), [("Role", "label %d" % k)])
    _save(doc)


#: FxAnPoints: a pool of free animation points parked below the page (as a template parks
#: its AN pool), a point on a white box, one on the grey page itself; the pick box CtDraw
#: stores as an AnPoint's rect2 is (x - 4, y - 9) .. (x + 21, y + 6)
ANPOINT_POOL = tuple((862 + 40 * k, 820) for k in range(5))
ANPOINT_WHITE_BOX = (300, 300, 360, 340)
ANPOINT_ON_WHITE = (330, 320)
ANPOINT_ON_PAGE = (700, 600)
ANPOINT_DELTAS = (-4, -9, 21, 6)
#: the natural extent (rect2 - anchor) CtDraw's load gives a V3 / V4 number (0x1007) or
#: animated symbol (0x100D) with no symbol that resolves to a bitmap: 22 x 22 at the point
#: (the oracle's pages FxArrange, FxParts, FxLegacy and FxLegacy2)
NO_SYMBOL_DELTAS = (0, 0, 22, 22)
#: FxFreehand's FxFreehand1: its point count
FREEHAND_POINTS = 320
#: FxScaled's FxScaledSet: the scale its symbol set's slots 2 and 3 store (by hand)
SCALED_SET = (0.80, 0.77)


def _raw_page(doc, objs) -> None:
    """Write ``doc``'s page with the records ``objs`` as they are (no save step: a save
    would add what CtDraw's save adds, such as the name keys)."""
    from ctgedit.format import writer

    p = doc.parsed
    p.page.objects = list(objs)
    _w(doc.path, writer.serialize_ctg(p.container, p.page, p.attributes))


def build_main_third(b: Build) -> None:
    """FxMain, the third set (all unregistered but FxNoAnNames): legacy NAME-keyed groups
    (FxLegacyNames), free animation points (FxAnPoints), named records without their
    '__AN_NAME__' key (FxNoAnNames, registered, then written as is) and a symbol set whose
    stored slots hold a scale the re-derivation does not give (FxScaled)."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import placement as PL
    from ctgedit.format import records as R

    # ---- FxLegacyNames: groups named by the legacy NAME key (== the header name, no
    # __AN_NAME__), as the SA help navigation links are; CtDraw's paste made FxLink_1
    doc = b.blank_page("FxMain", "FxLegacyNames", size=(800, 600), register=False)
    for k, name in enumerate(("FxLink", "FxLink_1")):
        x = 20 + 200 * k
        a = doc.create_object("rectangle", (x, 20, x + 80, 50), colour=0x405060)
        c = doc.create_object("text", (x, 54, x + 80, 70), text="link %d" % k)
        _legacy_name(doc, a, "FxShadow")  # each group's own FxShadow, c unnamed
        _legacy_name(doc, doc.group_objects([a, c]), name)
    _save(doc)
    _without_name_keys(EditableDocument.open(doc.path))  # (the save added them)

    # ---- FxAnPoints: free V3 / V4 animation points (tc 0x1000) on a grey page
    doc = b.blank_page("FxMain", "FxAnPoints", size=(1024, 768), background=0xE5E5E5, register=False)
    for at in ANPOINT_POOL:
        doc.add_object(
            R.to_po(R.point_record(0x1000, handle=doc.next_handle(), at=at, deltas=ANPOINT_DELTAS))
        )
    _name(doc, doc.create_object("rectangle", ANPOINT_WHITE_BOX, colour=0xFFFFFF), "FxPointBox")
    for at in (ANPOINT_ON_WHITE, ANPOINT_ON_PAGE):
        doc.add_object(
            R.to_po(R.point_record(0x1000, handle=doc.next_handle(), at=at, deltas=ANPOINT_DELTAS))
        )
    _save(doc)

    # ---- FxNoAnNames: named records whose bags lack '__AN_NAME__' (a page CtDraw's save
    # has not seen): an arrow polyline whose value bag ends in a vertex animation run (as
    # CtDraw's Apply writes it: per vertex the integer offsets FROMX, FROMY, TOX, TOY, then
    # the expression VERTEXP; CtDraw's load drops a run whose offsets are not integers), a
    # genie whose member button's template bag binds COMMENT (COMMENT last in its value
    # bag), records with non-empty template bags. The page is registered (as a shipped
    # page is): CtDraw's load takes a polygon's vertex animation from the project's
    # pgvertex rows, not from the bag, and drops the run of a page with no rows
    doc = b.blank_page("FxMain", "FxNoAnNames", size=(800, 600))
    add = _adder(doc)
    i = add("FxArrow", "polyline", None, [(20, 40), (120, 40), (140, 60)])
    _keys(
        doc,
        i,
        [
            ("DESC", "Fx arrow"),
            ("VERTEX_VERTFROMX0", "0"),
            ("VERTEX_VERTFROMY0", "0"),
            ("VERTEX_VERTTOX0", "20"),
            ("VERTEX_VERTTOY0", "0"),
            ("VERTEX_VERTEXP0", "FX_LEVEL1"),
            ("VERTEX_VERTFROMX2", "0"),
            ("VERTEX_VERTFROMY2", "0"),
            ("VERTEX_VERTTOX2", "0"),
            ("VERTEX_VERTTOY2", "-20"),
            ("VERTEX_VERTEXP2", "FX_LEVEL2"),
        ],
    )
    _place(doc, "FxTipGenie", "fxparts", "fx_tipbutton", (200, 40), {"Tip": "Fx tip"})
    _place(doc, "FxNoNamePump", "fxgenie", "fx_pump", (400, 40), {"Tag": "FX_PUMP1", "Label": "Pump"})
    add("FxPlainBox", "rectangle", (20, 200, 100, 240), colour=0x608040)
    _save(doc)
    _without_name_keys(EditableDocument.open(doc.path))

    # ---- FxLegacy2: V3 / V4 records that draw (the editor shows a Symbol's SYMB000 state,
    # an Animated Symbol's OFF_SYMBOL, a String's OFF text and a Number's mask, in the
    # fonts.DBF font FxSP: Courier New 14 px light red)
    doc = b.blank_page("FxMain", "FxLegacy2", size=(800, 600), register=False)
    # (fx_off is no bitmap, so CtDraw's load gives the 0x1007 the no-symbol box)
    i = doc.add_object(
        R.to_po(
            R.point_record(
                0x1007,
                handle=doc.next_handle(),
                at=(20, 20),
                deltas=NO_SYMBOL_DELTAS,
                props=(("VAR_A", "FX_PUMP1"), ("SYMB000", "fxsym.fx_off"), ("SYMB001", "fxsym.fx_on")),
            )
        )
    )
    _name(doc, i, "FxV3Symbol")
    i = _name(doc, doc.create_object("animated_symbol", (80, 20, 110, 50)), "FxV4AnimSymbol")
    _keys(doc, i, [("EXPR", "FX_MODE"), ("OFF_SYMBOL", "fxsym.fx_off"), ("SYMBOL_1", "fxsym.fx_on")])
    i = _name(doc, doc.create_object("string", (20, 80, 140, 96)), "FxV3String")
    _keys(
        doc,
        i,
        [("EXPR", "FX_PUMP1"), ("OFF_TEXT", '"@(Fx OK)"'), ("ON_TEXT", '"@(Fx Run)"'), ("FONT", "FxSP")],
    )
    # (it owns a keyboard command record, as a V3 page stores it: the editor gives a new
    # String none)
    import dataclasses

    objs = list(doc.parsed.page.objects)
    objs[i] = dataclasses.replace(objs[i], class_flag=1)
    objs.insert(
        i + 1,
        R.to_po(
            R.keyboard(
                handle=doc.next_handle(),
                at=(20, 80),
                props=(("KEY_SEQ", "ENTER"), ("CMD", "FxToggle(FX_PUMP1)")),
            )
        ),
    )
    doc._structural("keyboard", objs)
    i = doc.add_object(
        R.to_po(
            R.point_record(
                0x1004,
                handle=doc.next_handle(),
                at=(20, 120),
                deltas=(0, 0, 80, 16),
                props=(("EXPR", "FX_LEVEL1"), ("FONT", "FxSP")),
            )
        )
    )
    _name(doc, i, "FxV3Number")
    _save(doc)
    # (the Number keeps the Bounds it was made with, its point + (0, 0, 80, 16), which the
    # save derived again from its mask and font (legacy.value_extent, CtDraw's load does the
    # same): the oracle's pages and chain7/legacy_measure captures read the page so)
    _with_bounds(EditableDocument.open(doc.path), "FxV3Number", (20, 120, 100, 136))

    # ---- FxParity: genie members whose looks the editor takes from their twins: a status
    # box, a VISIBLE '1' tag text, a multi-state text (FxStatus1), a turned picture
    # (FxTurnedPic1) and a spinner picture with a stored slot-1 translation, placed right
    # of the page edge (FxSpinner1)
    e = ElementDocument.open_element(os.path.join(b.proj("FxLib"), "fxparts.ctm"), "fx_spinner")
    objs = list(e.parsed.page.objects)
    k = _idx(e, "FxSpinPic")
    po = R.to_po(_payload_of(objs[k]))
    (s1, s2, s3), _end = PL.read_slots(po.body_raw)
    PL.write_slots(po, [(1, (1.0, 0.0, 0.0, 1.0, -587.0, 3.0)), s2[:2], s3[:2]])
    objs[k] = po
    e._structural("slot 1", objs)
    e.save()
    libraries_changed()
    doc = b.blank_page("FxMain", "FxParity", size=(800, 600), register=False)
    _place(doc, "FxStatus1", "fxparts", "fx_status", (20, 20), {"Tag": "FX_PUMP1"})
    i = _place(doc, "FxTurnedPic1", "fxparts", "fx_turned_pic", (20, 100))
    doc.rotate_objects([i])  # a quarter turn: its box is 31 x 19
    _place(doc, "FxSpinner1", "fxparts", "fx_spinner", (820, 100), {"Desc": "Fx busy"})
    _save(doc)

    # ---- FxFreehand: a free-hand shape (tc 0x02: a style block, then a counted point
    # array that ends the body), orange 0xFF952B, raised, filled, open, 320 points
    doc = b.blank_page("FxMain", "FxFreehand", size=(800, 600), register=False)
    import math

    pts = [
        (int(60 + 2 * k), int(200 + 60 * math.sin(k / 9.0) + 20 * math.cos(k / 3.0)))
        for k in range(FREEHAND_POINTS)
    ]
    l, t = min(x for x, _y in pts), min(y for _x, y in pts)
    r, bt = max(x for x, _y in pts), max(y for _x, y in pts)
    orange = 0x00FF952B
    st = R.Style(effects=1, fill_style=1, attribs=1, line=(orange, orange), fill=(orange, orange))
    po = R.to_po(
        R.record(
            0x02,
            handle=doc.next_handle(),
            anchor=pts[0],
            rect1=(l, t, r, bt),
            rect2=R.bounds(0x02, (l, t, r, bt), st, len(pts)),
            style=st,
            cls=R.nodes_part(pts),
        )
    )
    _name(doc, doc.add_object(po), "FxFreehand1")
    # open lines whose LINE slot is red / blue while their FILL slot is black (0, 0)
    from ctgedit.format import geometry as G

    for name, kind, nodes, colour in (
        ("FxRedLine", "line", [(60, 400), (400, 400)], 0xFF0000),
        ("FxBlueLine", "polyline", [(60, 440), (200, 440), (200, 480), (400, 480)], 0x0000FF),
    ):
        i = _name(doc, doc.create_object(kind, None, nodes), name)
        assert doc.set_colour_slot(i, G.SLOT_LINE, colour, colour)
        assert doc.set_colour_slot(i, G.SLOT_FILL, 0, 0)
    _save(doc)

    # ---- FxScaled: a placed fx_lamp whose symbol set stores a scaled Total (0.80 x 0.77)
    # in its slots 2 and 3, which the re-derivation from the element does not give
    doc = b.blank_page("FxMain", "FxScaled", size=(800, 600), register=False)
    _place(doc, "FxScaledSet", "fxgenie", "fx_lamp", (100, 100), {"Tag": "FX_PUMP1"})
    add = _adder(doc)
    add("FxScaledLeft", "rectangle", (20, 300, 80, 340), colour=0x506070)
    add("FxScaledRight", "rectangle", (300, 300, 360, 340), colour=0x705060)
    _save(doc)
    doc = EditableDocument.open(doc.path)
    objs = list(doc.parsed.page.objects)
    root = _idx(doc, "FxScaledSet")
    k = next(j for j in doc.page.subtree(root) if objs[j].type_code == 0x0C)
    po = R.to_po(_payload_of(objs[k]))
    (s1, s2, s3), _end = PL.read_slots(po.body_raw)
    sx, sy = SCALED_SET
    slots = [s1[:2]] + [(3, (sx, 0.0, 0.0, sy, s[1][4], s[1][5])) for s in (s2, s3)]
    PL.write_slots(po, slots)
    objs[k] = po
    _raw_page(doc, objs)


def _with_bounds(doc, name: str, rect2) -> None:
    """Write ``doc``'s page again with the record named ``name`` holding Bounds ``rect2``
    (the header's rect2 alone, as is: a save would derive them again)."""
    from ctgedit.format import geometry as G

    objs = list(doc.parsed.page.objects)
    (k,) = doc.find_objects_by_name(name)
    po = objs[k]
    gh = G.parse_geom_header(po.body_raw)
    body = bytearray(po.body_raw)
    struct.pack_into("<4h", body, gh.rect2_off, *rect2)
    po.body_raw = bytes(body)
    _raw_page(doc, objs)


def _without_name_keys(doc) -> None:
    """Write ``doc``'s page again with no '__AN_NAME__' key in the bags of its named
    records (as is: a save would add them back)."""
    from ctgedit.format import genie_place as GP

    objs = []
    for po in doc.parsed.page.objects:
        r = GP.decode(po) if po.raw_payload is None else None
        if r is not None and r.name and r.entries:
            e0 = r.entries[0]
            for k in range(1, len(e0)):
                e0[k] = GP.Bag([p for p in e0[k].pairs if p[0] != "__AN_NAME__"], e0[k].ov)
            po = GP.to_po(GP.encode(r))
        objs.append(po)
    _raw_page(doc, objs)


def _payload_of(po) -> bytes:
    from ctgedit.format import writer

    return writer.object_payload(po)


def _legacy_name(doc, index: int, name: str) -> None:
    """Name a record the legacy way: the header name and a NAME bag key of the same value,
    no '__AN_NAME__' (set_object_name keeps a NAME-keyed record in that form)."""
    assert doc.ensure_property(index, "NAME", name)
    assert doc.set_object_name(index, name), name
    assert "__AN_NAME__" not in doc.object_properties(index), name


def _bulk(doc, specs) -> int:
    """Append factory records in ONE structural step (fast for thousands): ``specs`` =
    [(kind, rect, nodes, {colour, text})]. Returns the index of the first new record."""
    from ctgedit.format import factory

    h = doc.next_handle()
    objs = list(doc.parsed.page.objects)
    first = len(objs)
    for k, (kind, rect, nodes, kw) in enumerate(specs):
        objs.append(factory.create_object(kind, rect, nodes, handle=(h + k) & 0xFFFF, **kw))
    doc._structural("bulk", objs)
    return first


def build_perf(b: Build) -> None:
    """FxPerf: the perf tests' pages, unregistered (an empty pages.DBF beside them).
    FxLarge: about 1,200 records on the normal@XGA template (genies with members,
    groups, styled shapes, texts, a pipe genie, numbers, named objects). FxHuge: about
    5,000 records (rectangles and texts)."""
    os.makedirs(b.proj("FxPerf"), exist_ok=True)
    b.table("FxPerf", "include", [{"NAME": "FxLib"}, {"NAME": "FxStyle"}])
    b.page_tables("FxPerf")
    b.project_cit("FxPerf")
    b.table("FxPerf", "variable", _tag_rows(FX_TAGS))
    doc = b.template_page("FxPerf", "FxLarge", TPL_NORMAL, register=False)
    for n in range(30):
        _place(
            doc,
            "FxLargePump%02d" % (n + 1),
            "fxgenie",
            "fx_pump",
            (20 + 100 * (n % 10), 60 + 100 * (n // 10)),
            {"Tag": "FX_PUMP%d" % (n % 3 + 1), "Label": "P%d" % (n + 1)},
        )
    for n in range(20):
        _place(
            doc,
            "FxLargePipe%02d" % (n + 1),
            "fxparts",
            "fx_pipe",
            (20 + 100 * (n % 10), 380 + 70 * (n // 10)),
            {"Tag": "FX_LEVEL%d" % (n % 3 + 1)},
        )
    for g in range(10):
        first = _bulk(
            doc,
            [
                (
                    "rectangle",
                    (
                        20 + 100 * g + 12 * (k % 4),
                        540 + 12 * (k // 4),
                        30 + 100 * g + 12 * (k % 4),
                        550 + 12 * (k // 4),
                    ),
                    None,
                    {"colour": 0x204060 + 0x101010 * k},
                )
                for k in range(8)
            ],
        )
        _name(doc, doc.group_objects(list(range(first, first + 8))), "FxLargeGroup%02d" % (g + 1))
    specs = []
    for k in range(300):
        x, y = 10 + 34 * (k % 30), 600 + 16 * (k // 30)
        specs.append(("rectangle", (x, y, x + 30, y + 12), None, {"colour": (k * 2654435761) & 0xFFFFFF}))
    for k in range(350):
        x, y = 10 + 50 * (k % 20), 30 + 14 * (k // 20)
        specs.append(("text", (x, y, x + 48, y + 12), None, {"text": "T%d" % k}))
    for k in range(100):
        x, y = 1040 + 30 * (k % 10), 60 + 30 * (k // 10)
        specs.append(("ellipse", (x, y, x + 24, y + 24), None, {"colour": 0x30A0C0}))
    for k in range(50):
        x, y = 1040 + 60 * (k % 5), 380 + 40 * (k // 5)
        specs.append(("polygon", None, [(x, y), (x + 40, y), (x + 20, y + 30)], {"colour": 0xC0A030}))
        specs.append(("line", None, [(x, y + 34), (x + 50, y + 34)], {}))
    for k in range(10):
        specs.append(("pipe", None, [(20 + 90 * k, 740), (80 + 90 * k, 740), (80 + 90 * k, 760)], {}))
    for k in range(20):
        specs.append(
            (
                "number",
                (1040 + 60 * (k % 5), 700 + 16 * (k // 5), 1096 + 60 * (k % 5), 714 + 16 * (k // 5)),
                None,
                {"text": "##.#"},
            )
        )
    first = _bulk(doc, specs)
    for k in range(50):
        _name(doc, first + 13 * k, "FxLarge%03d" % (k + 1))
    _save(doc)

    doc = b.blank_page("FxPerf", "FxHuge", size=(1920, 1080), register=False)
    specs = []
    for k in range(2500):
        x, y = 10 + 38 * (k % 50), 10 + 21 * (k // 50)
        specs.append(("rectangle", (x, y, x + 36, y + 19), None, {"colour": (k * 40503) & 0xFFFFFF}))
        specs.append(("text", (x + 2, y + 3, x + 34, y + 16), None, {"text": "%d" % k}))
    _bulk(doc, specs)
    _name(doc, 0, "FxHugeFirst")
    _name(doc, len(doc.parsed.page.objects) - 1, "FxHugeLast")
    _save(doc)


#: FxSa's workspace panes on FxSaMaster: (name, rect, the other bindings)
SA_PANES = (
    (
        "Content",
        (0, 120, 1500, 1000),
        {
            "IsDefault": "TRUE",
            "FillMode": "PANE_FILLMODE_AUTOFILL",
            "DefaultPage": "FxSaContent",
            "ContentTypes": "FxL1",
        },
    ),
    ("HeaderBar", (0, 0, 1920, 60), {"DefaultPage": "FxSaHeader", "HasScrollbars": "FALSE"}),
    (
        "FacePlate",
        (1500, 160, 1920, 700),
        {
            "ContentTypes": "FxFP",
            "DisplayMode": "1",
            "FillMode": "PANE_FILLMODE_AUTOFILL",
            "TabPaneName": "FacePlateHeader",
            "TabControlName": "FxTabBar",
        },
    ),
    ("FacePlateHeader", (1500, 120, 1920, 160), {"DefaultPage": "FxSaHeader"}),
    ("Navigation", (0, 60, 1920, 120), {"DefaultPage": "FxSaNav"}),
    (
        "Info",
        (1500, 740, 1920, 1080),
        {"DefaultPage": "FxSaBlank", "HasScrollbars": "TRUE", "DisplayMode": "PANE_DISPLAYMODE_MAINTAIN"},
    ),
    (
        "InfoHeader",
        (1500, 700, 1920, 740),
        {"DefaultPage": "", "ExcludedAutofillPanes": "Content", "EquipRefAssocs": "MEO"},
    ),
)
#: the pane genie's parameters in their declaration order
PANE_PARAMS = (
    "Name",
    "IsDefault",
    "FillMode",
    "DisplayMode",
    "DefaultPage",
    "ContentTypes",
    "TabPaneName",
    "TabControlName",
    "ExcludedAutofillPanes",
    "EquipRefAssocs",
    "HasScrollbars",
)
#: the runtime's metadata name of each pane parameter (sa_workspace.FIELD_LABELS)
PANE_METADATA = {"TabPaneName": "TabPane"}

FXSA_TAGS = [
    ("FX_SA_FXPMP1_RUN", "DIGITAL", "FxCo.FxSite.Line1.FXPMP1", "Run", "", "running"),
    ("FX_SA_FXPMP1_SPD", "REAL", "FxCo.FxSite.Line1.FXPMP1", "Speed", "###.#", "speed"),
    ("FX_SA_FXSPRAY1_ON", "DIGITAL", "FxCo.FxSite.Line1.FXSPRAY1", "On", "", "spray"),
]


def build_sa(b: Build) -> None:
    """FxSa: an SA workspace project."""
    from ctgedit.editing import EditableDocument, libraries_changed

    os.makedirs(b.proj("FxSa"), exist_ok=True)
    b.table("FxSa", "include", [{"NAME": "FxLib"}, {"NAME": "FxStyle"}])
    b.page_tables("FxSa")
    b.project_cit("FxSa", style="fx_sa", res="HD1080", title=False, background=0xDDDDDD)
    project_tables(
        b,
        "FxSa",
        FX_TAGS[:4] + FXSA_TAGS,
        [
            "FxCo",
            "FxCo.FxSite",
            "FxCo.FxSite.Line1",
            "FxCo.FxSite.Line1.FXPMP1",
            "FxCo.FxSite.Line1.FXSPRAY1",
        ],
    )
    b.table(
        "FxSa",
        "equip",
        [
            dict(r, TYPE=t)
            for r, t in zip(
                _equip_rows(
                    [
                        "FxCo",
                        "FxCo.FxSite",
                        "FxCo.FxSite.Line1",
                        "FxCo.FxSite.Line1.FXPMP1",
                        "FxCo.FxSite.Line1.FXSPRAY1",
                    ]
                ),
                ("FxCompany", "FxSite", "FxLine", "FxDrive_MEO", "FxSprayer"),
            )
        ],
    )
    b.table(
        "FxSa",
        "eqref",
        [
            {
                "EQUIP": "FxCo.FxSite.Line1.FXSPRAY1",
                "REFEQUIP": "FxCo.FxSite.Line1.FXPMP1",
                "REFCAT": "MEO",
                "COMMENT": "the sprayer's drive",
            }
        ],
    )
    b.table(
        "FxSa",
        "contype",
        [
            {"NAME": "FxL1", "COMMENT": "a level 1 page"},
            {"NAME": "FxFP", "COMMENT": "a faceplate"},
            {"NAME": "Master", "COMMENT": "a workspace master page"},
            {"NAME": "__Internal", "COMMENT": "internal"},
        ],
    )
    # a workspace's menus (pagemenu): PAGE = the menu ('Navigation' is the one the
    # navigation genies read, io.project.NAVIGATION_MENU), LEVEL1 .. LEVEL6 = the item's
    # path in it, TARGETPAGE = the page it shows
    b.table(
        "FxSa",
        "pagemenu",
        [
            {
                "PAGE": "Navigation",
                "LEVEL1": "FxPlant",
                "LEVEL2": "Fx & Area",
                "LEVEL3": "FxContent (Fx3)",
                "COMMAND": 'FxShow("FxSaContent")',
                "TARGETPAGE": "FxSaContent",
                "ORDER": "1",
                "COMMENT": "the content page",
            },
            {
                "PAGE": "FxHeaderbar",
                "LEVEL1": "Home",
                "COMMAND": 'FxShow("FxSaContent")',
                "TARGETPAGE": "FxSaContent",
                "ORDER": "1",
            },
            {
                "PAGE": "FxHelp",
                "LEVEL1": "About",
                "COMMAND": 'FxShow("FxSaBlank")',
                "TARGETPAGE": "FxSaBlank",
                "ORDER": "1",
            },
            {
                "PAGE": "FxInfoZone",
                "LEVEL1": "Info",
                "COMMAND": 'FxShow("FxSaBlank")',
                "TARGETPAGE": "FxSaBlank",
                "ORDER": "2",
            },
        ],
    )
    _w(
        os.path.join(b.proj("FxSa"), "citect.ini"),
        _ci("""
; FxSa's own citect.ini: the startup page builds the workspace
[Page]
Startup=FxSaMaster
"""),
    )
    _w(
        os.path.join(b.proj("FxSa"), "fxsa.ci"),
        _ci("""
// FxSa's Cicode stand-ins (our own; the runtime's workspace code is not here)
FUNCTION Workspace_Init()
END

FUNCTION FxShow(STRING page)
    PageDisplay(page);
END
"""),
    )
    d = b.element("FxSa", "fxws.ctm", "pane", "genie")
    r = d.create_object("rectangle_outline", (0, 0, 400, 300))
    _name(d, r, "Pane")
    d.ensure_property(r, "VISIBLE", "FALSE")
    d.set_metadata(r, [(PANE_METADATA.get(p, p), "%%%s%%" % p) for p in PANE_PARAMS])
    d.create_object("text", (4, 4, 396, 20), text="%Name%")
    d.save()
    d = b.element("FxSa", "fxws.ctm", "navlink", "genie")
    bt = d.create_object("button", (0, 0, 120, 30), text="%Tooltip%")
    d.set_metadata(
        bt,
        [
            ("MenuItemPath", "%MenuItemPath%"),
            ("IsControlLink", "%IsControlLink%"),
            ("EquipmentFrom", "%EquipmentFrom%"),
            ("EquipmentTo", "%EquipmentTo%"),
        ],
    )
    d.ensure_property(bt, "COMMENT", "%Tooltip%")
    d.save()
    _w(os.path.join(b.proj("FxSa"), "fxws.frm"), _ci(FXWS_FRM))
    libraries_changed()

    def pane(doc, name, rect, extra):
        params = {p: "" for p in PANE_PARAMS}
        params.update({"Name": name, "IsDefault": "FALSE", "HasScrollbars": "FALSE"})
        params.update(extra)
        i = _place(doc, "FxPane%s" % name, "fxws", "pane", rect[:2], params)
        doc.set_object_rect(i, rect)
        return i

    tpl = "fx_sa.normal@-HD1080.<ctt"
    doc = b.template_page("FxSa", "FxSaBlank", "fx_sa.blank@-HD1080.<ctt")
    _name(doc, doc.create_object("text", (40, 40, 200, 56), text="blank"), "FxSaBlankText")
    doc.save()
    doc = b.template_page("FxSa", "FxSaHeader", "fx_sa.blank@-HD1080.<ctt")
    _name(doc, doc.create_object("rectangle", (0, 0, 420, 40), colour=0x404850), "FxTabBar")
    doc.save()
    doc = b.template_page("FxSa", "FxSaNav", "fx_sa.blank@-HD1080.<ctt")
    for n, (label, path) in enumerate(
        (("Home", "FxPlant"), ("FxArea", "FxPlant.Fx & Area"), ("Line", "FxPlant.Fx & Area.FxContent (Fx3)"))
    ):
        _place(
            doc,
            "FxNav%d" % (n + 1),
            "fxws",
            "navlink",
            (20 + 140 * n, 10),
            {
                "Tooltip": label,
                "MenuItemPath": path,
                "IsControlLink": "FALSE",
                "EquipmentFrom": "FxCo.FxSite",
                "EquipmentTo": "FxCo.FxSite.Line1",
            },
        )
    doc.save()
    shutil.copyfile(
        os.path.join(b.proj("FxLib"), "fxgenie.ctm"), os.path.join(b.proj("FxSa"), "fxsa_gone.ctm")
    )
    libraries_changed()
    doc = b.template_page("FxSa", "FxSaContent", tpl)
    _place(doc, "FxSaPump", "fxgenie", "fx_pump", (100, 100), {"Tag": "FX_SA_FXPMP1_RUN", "Label": "FXPMP1"})
    _place(doc, "FxSaAlarm", "fxparts", "fx_alarm", (300, 100), {"Equipment": "FxCo.FxSite.Line1.FXPMP1"})
    _place(doc, "FxSaLamp", "fxgenie", "fx_lamp", (500, 100), {"Tag": "FX_SA_FXSPRAY1_ON"})
    _place(doc, "FxSaBox", "fxsym", "fx_box", (700, 100), ext="ctl")
    _place(doc, "FxSaGone", "fxsa_gone", "fx_value", (100, 300), {"Description": "Gone"})
    doc.save()
    os.remove(os.path.join(b.proj("FxSa"), "fxsa_gone.ctm"))
    libraries_changed()
    doc = EditableDocument.open(os.path.join(b.proj("FxSa"), "FxSaContent.ctg"))
    doc.placeholders_for_missing_libraries()
    doc.save()
    doc = b.template_page("FxSa", "FxSaMaster", tpl)
    for name, rect, extra in SA_PANES:
        pane(doc, name, rect, extra)
    doc.save()
    doc = EditableDocument.open(doc.path)
    doc.set_page_record(
        {
            "TITLE": "Fx workspace",
            "CON_TYPE": "Master",
            "READY_FUNC": "Workspace_Init()",
            "PARENT": "FxSaContent",
        }
    )
    doc.save()
    for name, inner, target in (
        ("FxSaNested1", "Inner", "FxSaNested2"),
        ("FxSaNested2", "Loop", "FxSaNested2"),
    ):
        doc = b.template_page("FxSa", name, "fx_sa.blank@-HD1080.<ctt")
        doc.set_page_property("HasPanes", "1")
        pane(doc, inner, (100, 100, 900, 700), {"IsDefault": "TRUE", "DefaultPage": target})
        doc.save()


def build_sim_more(b: Build) -> None:
    """FxSim, the second set: a plain startup page (citect.ini), alarm lists, an
    eight-pen trend, popups (a super genie host and a '!' popup page) and a page of more
    than 100 animated objects (unregistered)."""
    _w(
        os.path.join(b.proj("FxSim"), "citect.ini"),
        _ci("""
; FxSim's own citect.ini: a plain startup page
[Page]
Startup=FxSim1
"""),
    )
    # the alarm categories' priorities (category 1 is priority 3, 3 is 1)
    b.table(
        "FxSim",
        "category",
        [{"CATEGORY": str(c), "PRIORITY": str(4 - c), "COMMENT": "category %d" % c} for c in (1, 2, 3)],
    )
    doc = b.blank_page("FxSim", "FxSimAlarms", size=(800, 600))
    _place(doc, "FxAlarmList1", "fxparts", "fx_anlist", (20, 20), {"Page": "FxSimAlarms"})
    _place(doc, "FxAlarmList2", "fxparts", "fx_anlist", (20, 200), {"Page": "FxSimAlarms"})
    _place(doc, "FxTreeList", "fxparts", "fx_anlist", (420, 20), {"Page": "FxSimTree"})
    _place(doc, "FxAlarmFlag", "fxparts", "fx_alarm", (420, 300), {"Equipment": "FxSim.Line"})
    doc.save()
    doc = b.blank_page("FxSim", "FxSimTrend", size=(800, 600))
    i = _name(doc, doc.create_object("trend", (20, 20, 520, 320)), "FxSimTrend8")
    doc.set_trend_properties(i, pens=[("FX_SIM_R%02d" % k, None) for k in range(1, 9)])
    doc.save()
    doc = b.blank_page("FxSim", "!FxSimPop", size=(300, 200))
    bt = _name(doc, doc.create_object("button", (20, 150, 120, 180), text="Close"), "FxPopClose")
    doc.set_input_commands(bt, touch={"CMD": "WinFree();"})
    v = _name(doc, doc.create_object("number", (20, 20, 160, 36), text="######"), "FxPopCount")
    doc.ensure_property(v, "TXT_STR", "FX_SIM_COUNT")
    doc.save()
    doc = b.blank_page("FxSim", "FxSimPopup", size=(800, 600))
    bt = _name(doc, doc.create_object("button", (20, 20, 160, 50), text="Popup"), "FxOpenPop")
    doc.set_input_commands(bt, touch={"CMD": 'WinNewAt("!FxSimPop", 100, 100, 1)'})
    _place(doc, "FxSimDetail", "fxparts", "fx_popper2", (20, 80), {"Tag": "FX_SIM_RUN"})
    doc.save()
    doc = b.blank_page("FxSim", "FxSimBig", size=(1280, 800), register=False)
    add = _adder(doc)
    for k in range(1, 29):
        x, y = 20 + 44 * ((k - 1) % 14), 20 + 50 * ((k - 1) // 14)
        _keys(
            doc,
            add("FxSimLamp%02d" % k, "rectangle", (x, y, x + 40, y + 40), colour=0x606060),
            [("COLMODE", "0"), ("COL_A", "FX_SIM_D%02d" % k)],
        )
        x, y = 20 + 88 * ((k - 1) % 7), 140 + 22 * ((k - 1) // 7)
        _keys(
            doc,
            add("FxSimValue%02d" % k, "number", (x, y, x + 84, y + 16), text="###.##"),
            [("TXT_STR", "FX_SIM_R%02d" % k)],
        )
        x, y = 20 + 30 * (k - 1), 260
        _keys(
            doc,
            add("FxSimFill%02d" % k, "rectangle", (x, y, x + 24, y + 100), colour=0x2060C0),
            [("FIL", "FX_SIM_R%02d" % k), ("FILMINPC", "0"), ("FILMAXPC", "100")],
        )
    for k in range(1, 21):
        _name(
            doc,
            doc.create_symbol_set(
                (20 + 40 * ((k - 1) % 10 + 1), 400 + 30 * ((k - 1) // 10)),
                kind=0,
                exprs=["FX_SIM_D%02d" % k],
                states=[("fxsym", "fx_off"), ("fxsym", "fx_on")],
            ),
            "FxSimSet%02d" % k,
        )
    for k in range(1, 5):
        a = add("FxSimGroup%dA" % k, "rectangle", (20 + 120 * k, 460, 70 + 120 * k, 500), colour=0x808040)
        c = add("FxSimGroup%dB" % k, "text", (20 + 120 * k, 504, 120 + 120 * k, 520), text="@(Fx Start)")
        g = _name(doc, doc.group_objects([a, c]), "FxSimGroup%d" % k)
        _keys(doc, g, [("DISABLED", "FX_SIM_D%02d" % (10 + k))])
    _keys(
        doc,
        add("FxSimLogin", "text", (20, 560, 200, 576), text="Login..."),
        [("TXTMODE", "4"), ("TXT_STR", '"@(Fx Start)"')],
    )
    _save(doc)


def build_cycle(b: Build) -> None:
    """FxCycleA / FxCycleB: two projects that include each other (a cycle); FxCycleA also
    includes FxNowhere, a project the install does not have. Tables only."""
    for name, inc in (("FxCycleA", ("FxCycleB", "FxNowhere")), ("FxCycleB", ("FxCycleA",))):
        os.makedirs(b.proj(name), exist_ok=True)
        b.table(name, "include", [{"NAME": n} for n in inc])
        b.table(
            name,
            "variable",
            _tag_rows(
                [
                    (
                        "FX_%s_%d" % (name[-1], k),
                        ("DIGITAL", "INT", "REAL")[k % 3],
                        "",
                        "",
                        "##.#" if k % 3 == 2 else "",
                        "tag %d" % k,
                    )
                    for k in range(1, 6)
                ]
            ),
        )


#: the flashing pair of flash@-XGA's records (the colour words as stored)
FLASH_ON, FLASH_OFF = 0x00FE8787, 0x00FF0000


#: the template GUIDs of COMPOSITE_XMLS (as the XML writes them)
COMPOSITE_GUIDS = {
    "fx_valve_c": "7C2E4D3B-9E5F-4A6B-8C7D-8E9F0A1B2C3D",
    "fx_drive_c": "8D3F5E4C-AF60-4B7C-9D8E-9FA0B1C2D3E4",
    "fx_switch_c": "9E4A6F5D-B071-4C8D-AE9F-A0B1C2D3E4F5",
    "fx_meter_c": "AF5B7A6E-C182-4D9E-BFA0-B1C2D3E4F5A6",
    "fx_status_c": "B0C1D2E3-F405-4A16-8B27-C3D4E5F60718",
}


# ----------------------------------------------------------------------------- old forms
def _legacy_links(raw: bytes) -> tuple:
    """A current link table region as a word2 < 0x0C file stores it (the bare references,
    no GUIDs): (region, link_count)."""
    from ctgedit.format import links

    t = links.parse_link_table(raw) if raw else None
    if not t or not t[1]:
        return b"", 0
    names = [n for _g, n in t[1]]
    blobs, offs, pos = [], [], 0
    for n in names:
        offs.append(pos)
        blob = n.encode("cp1252") + b"\x00"
        blobs.append(blob)
        pos += len(blob)
    body = (
        b"    "
        + struct.pack("<II", len(names), pos)
        + struct.pack("<%dI" % len(names), *offs)
        + b"".join(blobs)
    )
    count = (len(body) + links.LINK_ENTRY_SIZE - 1) // links.LINK_ENTRY_SIZE
    return body.ljust(count * links.LINK_ENTRY_SIZE, b"\x00"), count


def _grow_class_rect(payload: bytes) -> bytes:
    """A rectangle, ellipse or button record in its pre-word2-11 form."""
    from ctgedit.format import geometry as G, placement as P, upgrade as U, writer
    from ctgedit.format.page import parse_payload

    po = parse_payload(payload)
    if po.type_code not in U._RECT_CLASSES or not po.subtype_size & 0x2:
        return payload
    o = P.class_section_offset(po)
    n = 2 if po.type_code == 0x1001 and U._xp_style(po) else 1
    body = bytearray(po.body_raw)
    l, t, r, b = struct.unpack_from("<4h", body, o)
    dr, db = (n if r > l else 0), (n if b > t else 0)
    struct.pack_into("<4h", body, o, l, t, r + dr, b + db)
    gh = G.parse_geom_header(bytes(body))
    for at in (gh.rect1_off, gh.rect2_off):
        rl, rt, rr, rb = struct.unpack_from("<4h", body, at)
        struct.pack_into("<4h", body, at, rl, rt, rr + dr, rb + db)
    if po.type_code != 0x11:
        ax, ay = gh.anchor
        struct.pack_into("<2h", body, gh.anchor_off, ax, ay + db)
    po.body_raw = bytes(body)
    if po.type_code == 0x1001:
        props = po.raw_properties()
        for key, d in (("WIDTH", dr), ("HEIGHT", db)):
            if d and key in props:
                writer.set_object_property(po, key, str(int(props[key]) + d))
    return writer.object_payload(po)


def _downgrade_payload(payload: bytes, word2: int) -> bytes:
    """One current record payload in the form of ``word2`` (format/upgrade, backwards)."""
    if word2 < 0x0B:
        payload = _grow_class_rect(payload)
    an_end = 0x0C + struct.unpack_from("<H", payload, 0x0A)[0]
    body = bytearray(payload[an_end:])
    if word2 < 10:
        if struct.unpack_from("<H", body, 4)[0] != 0:
            raise ValueError("a named record has no form before word2 10")
        del body[4:6]
    return bytes(payload[:an_end]) + bytes(body)


def downgrade(data: bytes, word2: int) -> bytes:
    """A current page file in an old form."""
    from ctgedit.format import upgrade as U
    from ctgedit.format import writer
    from ctgedit.format.container import Container
    from ctgedit.format.page import parse_ctg_bytes

    c = Container.parse(data)
    pg, at = c.block("page"), c.block("attributes")
    d = pg.data
    objdata, count = struct.unpack_from("<II", d, 0)
    link_count = struct.unpack_from("<I", d, 0x1C)[0]
    start = U._HEAD + link_count * 0x60
    link_raw, new_links = _legacy_links(d[U._HEAD : start])
    payloads, off = [], start
    for _ in range(count):
        size = struct.unpack_from("<I", d, off)[0]
        payloads.append(_downgrade_payload(d[off + 8 : off + 8 + size], word2))
        off += 8 + size
    recs, running = bytearray(), 0
    for i, pl in enumerate(payloads):
        running += 8 + len(pl)
        recs += struct.pack("<II", len(pl), running if i + 1 < len(payloads) else 0) + pl
    head = bytearray(d[: U._HEAD_LEGACY])
    struct.pack_into("<I", head, 0, len(recs))
    struct.pack_into("<I", head, 0x1C, new_links)
    pg.data = bytes(head) + link_raw + bytes(recs) + d[start + objdata :]
    at.data = at.data[:0x24] + at.data[0x24 + 16 :]
    c.word2, c.version = word2, 8
    c.relayout()
    out = c.to_bytes()
    back, want = parse_ctg_bytes(out), parse_ctg_bytes(data)
    assert [writer.object_payload(p) for p in back.page.objects] == [
        writer.object_payload(p) for p in want.page.objects
    ], "the old form reads back wrong"
    return out


def build_indexes(b: Build, projects=None) -> None:
    """The last step: each project's pages.NDX holds a NAME entry for every live row of its
    pages.DBF (a page save appends its row to the table only, so the index is written
    again here from the table)."""
    from ctgedit.format import dbf

    for project in projects or _BEFORE_DEMO:
        folder = b.proj(project)
        table, index = (os.path.join(folder, "pages" + ext) for ext in (".DBF", ".NDX"))
        if not (os.path.exists(table) and os.path.exists(index)):
            continue
        with dbf.Dbf(table, writable=False) as t:
            width = next(w for n, _o, w in t.fields if n == "NAME")
            items = [
                (t.field(rec, "NAME"), r + 1)
                for r, rec in ((r, t.read(r)) for r in range(t.nrec))
                if rec[:1] != b"*"
            ]
        dbf.create_ndx(index, "NAME", width)
        if items:
            dbf._fill_index(index, items)


def build_project_files(b: Build, projects=None) -> None:
    """The files every shipped project has."""
    from ctgedit.format.element import LibraryFile
    from ctgedit.infra import identity
    from ctgedit.io import projectcit

    for project in projects or _BEFORE_DEMO:
        folder = b.proj(project)
        names = {f.lower() for f in os.listdir(folder)}
        if projectcit.find(folder) is None:
            # (FxLib's Page Defaults have a colour of their own: the super genie page
            # CtDraw makes in FxMain from fxsuper, which has none, then tells FxMain's
            # colour from FxLib's)
            b.project_cit(project, **({"background": FXLIB_BACKGROUND} if project == "FxLib" else {}))
        if "global.ctl" not in names:
            _w(os.path.join(folder, "GLOBAL.CTL"), LibraryFile.new(ts=identity.now_stamp()).to_bytes())
        if "units.dbf" not in names:
            b.table(project, "units")
        if "fonts.dbf" not in names:
            b.table(project, "fonts")
        for t, key in PAGE_PROJECT_TABLES.items():
            if t + ".dbf" not in names:
                b.table(project, t, index=key)
        for t in LIBRARY_SAVE_TABLES:
            if t + ".dbf" not in names:
                b.table(project, t)


# ============================================================================ FxDemo
# A small water treatment plant: the pages the README's screenshots show
# (tools/screenshots.py), with genies, animations and Cicode the simulator runs. It is the
# last step, so the identities (GUIDs, stamps) of every file before it stay as they are.
DEMO_PROJECT = "FxDemo"

#: (name, type, equipment, item, format, comment): the digital fault tags come first, so the
#: alarms project_tables makes from them stay quiet while the plant runs
DEMO_TAGS = (
    ("P101_FAULT", "DIGITAL", "FxDemo.P101", "Fault", "", "pump P-101 fault"),
    ("T201_HIGH", "DIGITAL", "FxDemo.T201", "High", "", "clear water tank high level switch"),
    ("CL2_LOW", "DIGITAL", "FxDemo.Dosing", "Low", "", "chlorine dosing low"),
    ("DOOR_OPEN", "DIGITAL", "FxDemo", "Door", "", "plant room door open"),
    ("LINE_RUN", "DIGITAL", "FxDemo", "Run", "", "line running"),
    ("P101_RUN", "DIGITAL", "FxDemo.P101", "Run", "", "pump P-101 running"),
    ("V102_OPEN", "DIGITAL", "FxDemo.V102", "Open", "", "valve V-102 open"),
    ("V201_OPEN", "DIGITAL", "FxDemo.V201", "Open", "", "valve V-201 open"),
    ("T101_LEVEL", "REAL", "FxDemo.T101", "Level", "###", "raw water tank level %"),
    ("T201_LEVEL", "REAL", "FxDemo.T201", "Level", "###", "clear water tank level %"),
    ("P101_SPEED", "REAL", "FxDemo.P101", "Speed", "###", "pump P-101 speed %"),
    ("FT101_FLOW", "REAL", "FxDemo.T101", "Flow", "###.#", "inlet flow m3/h"),
    ("FT201_FLOW", "REAL", "FxDemo.T201", "Flow", "###.#", "outlet flow m3/h"),
    ("PT101_PRESS", "REAL", "FxDemo.P101", "Pressure", "#.##", "pump pressure bar"),
    ("AT101_TURB", "REAL", "FxDemo.Quality", "Turbidity", "#.##", "turbidity NTU"),
    ("AT102_PH", "REAL", "FxDemo.Quality", "pH", "#.##", "pH"),
    ("AT103_CL2", "REAL", "FxDemo.Dosing", "Chlorine", "#.##", "free chlorine mg/l"),
)
DEMO_EQUIPMENT = (
    "FxDemo",
    "FxDemo.T101",
    "FxDemo.T201",
    "FxDemo.P101",
    "FxDemo.V102",
    "FxDemo.V201",
    "FxDemo.Quality",
    "FxDemo.Dosing",
)

DEMO_CI = r"""
// FxDemo: the plant the simulator runs (our own)
MODULE INT mStarted = 0;

FUNCTION DemoStart()
    IF mStarted = 0 THEN
        mStarted = 1;
        T101_LEVEL = 72;
        T201_LEVEL = 41;
        P101_SPEED = 64;
        LINE_RUN = 1;
        TaskNew("DemoProcess", "", 0);
    END
END

FUNCTION DemoLine(INT on)
    LINE_RUN = on;
END

FUNCTION DemoProcess()
    REAL t = 0;
    // (1, not TRUE: TRUE is a label of AVEVA's Include, which the fake install has not)
    WHILE 1 DO
        t = t + 1;
        P101_RUN = LINE_RUN;
        V102_OPEN = LINE_RUN;
        IF LINE_RUN THEN
            FT101_FLOW = 82 + 6 * Sin(t / 7);
            T101_LEVEL = Max(5, T101_LEVEL - 0.4);
            T201_LEVEL = Min(95, T201_LEVEL + 0.35);
            PT101_PRESS = 3.4 + 0.1 * Sin(t / 3);
        ELSE
            FT101_FLOW = 0;
            PT101_PRESS = 0;
        END
        IF T201_LEVEL > 90 THEN
            V201_OPEN = 1;
        END
        IF V201_OPEN THEN
            T201_LEVEL = T201_LEVEL - 1.2;
            FT201_FLOW = 75 + 4 * Sin(t / 5);
            IF T201_LEVEL < 30 THEN
                V201_OPEN = 0;
            END
        ELSE
            FT201_FLOW = 0;
        END
        IF T101_LEVEL < 10 THEN
            T101_LEVEL = 85;
        END
        T201_HIGH = T201_LEVEL > 92;
        AT101_TURB = 0.18 + 0.02 * Sin(t / 11);
        AT102_PH = 7.2 + 0.05 * Sin(t / 13);
        AT103_CL2 = 0.6 + 0.04 * Sin(t / 9);
        Sleep(1);
    END
END
"""

DEMO_FRM = r"""
! the genie forms of fxdemo.ctm
FORM "tank", "Genie - Demo tank";
1: "Tag", 32, readwrite, "Variable Tags", "NAME", "TYPE=REAL";
2: "Label", 16;
NORMAL
"Level  {1                 }
Label  {2          }"

FORM "pump", "Genie - Demo pump";
1: "Tag", 32, readwrite, "Variable Tags", "NAME", "TYPE=DIGITAL";
2: "Label", 16;
NORMAL
"Running  {1                 }
Label    {2          }"

FORM "valve", "Genie - Demo valve";
1: "Tag", 32, readwrite, "Variable Tags", "NAME", "TYPE=DIGITAL";
2: "Label", 16;
NORMAL
"Open   {1                 }
Label  {2          }"
"""

_INK, _STEEL, _PIPE, _WATER = 0x3A3F44, 0x8A9199, 0x6F7780, 0x2E86DE
_GREEN, _RED, _WHITE, _LCD, _LCD_TEXT = 0x27AE60, 0xC0392B, 0xFFFFFF, 0x1F2326, 0x7CFC9A


def _demo_text(d, rect, s, size=10, bold=False, colour=_INK, justify="left"):
    """A text in Segoe UI. A text is anchored at one point (its left edge, centre or right
    edge), so the rectangle is moved to put that point where ``justify`` asks."""
    x0, y0, x1, y1 = rect
    ax = {"left": x0, "centre": (x0 + x1) // 2, "right": x1}[justify]
    i = d.create_object("text", (ax, y0, ax + (x1 - x0), y1), text=s)
    d.set_font(i, face="Segoe UI", size=size, bold=bold, justify=justify)
    d.set_colour(i, colour)
    return i


def _demo_value(d, rect, expr, unit=""):
    """A dark readout: a lowered box, the value in green and its unit."""
    x0, y0, x1, y1 = rect
    box = d.create_object("rectangle", rect, colour=_LCD)
    d.set_effect(box, 2, 1)
    v = d.create_object("number", (x0 + 4, y0 + 3, x1 - 40, y1 - 3), text="###.#")
    assert d.ensure_property(v, "TXT_STR", expr)
    d.set_font(v, face="Segoe UI", size=10, bold=True)
    d.set_colour(v, _LCD_TEXT)
    if unit:
        _demo_text(d, (x1 - 38, y0 + 3, x1 - 4, y1 - 3), unit, size=9, colour=_LCD_TEXT)
    return v


def _demo_panel(d, rect, title):
    p = d.create_object("rectangle", rect, colour=0xE4E7EA)
    d.set_effect(p, 1, 2)
    _demo_text(d, (rect[0] + 10, rect[1] + 8, rect[2] - 10, rect[1] + 26), title, size=11, bold=True)
    return p


def _demo_status(d, rect, on_expr, on_colour=_GREEN, off_colour=_RED):
    """Two lamps in one place: one shows while ``on_expr`` holds, the other while it doesn't
    (VISIBLE is Plant SCADA's 'Hidden when': the object hides while it is true)."""
    for colour, expr in ((off_colour, on_expr), (on_colour, "NOT (%s)" % on_expr)):
        lamp = d.create_object("ellipse", rect, colour=colour)
        d.set_gradient(lamp, 4, _WHITE)
        assert d.ensure_property(lamp, "VISIBLE", expr)


def _demo_genies(b: Build) -> None:
    lib = "fxdemo.ctm"
    d = b.element(DEMO_PROJECT, lib, "tank", "genie")
    shell = d.create_object("rectangle", (0, 22, 110, 192), colour=_STEEL)
    d.set_gradient(shell, 1, 0xD5DADF)
    water = d.create_object("rectangle", (8, 30, 102, 184), colour=_WATER)
    d.set_gradient(water, 1, 0x7FB8F0)
    for k, v in (("FIL", "%Tag%"), ("FILMINPC", "0"), ("FILMAXPC", "100")):
        assert d.ensure_property(water, k, v)
    _demo_text(d, (0, 0, 110, 18), "%Label%", size=10, bold=True, justify="centre")
    _demo_value(d, (14, 200, 96, 222), "%Tag%:###", "%")
    d.save()

    d = b.element(DEMO_PROJECT, lib, "pump", "genie")
    _demo_status(d, (0, 0, 44, 44), "%Tag%")
    d.create_object("polygon", None, [(14, 10), (36, 22), (14, 34)], colour=_WHITE)
    _demo_text(d, (-20, 50, 64, 66), "%Label%", size=9, justify="centre")
    d.save()

    # (its centre, on the pipe, is (20, 20); the label is on the pump's baseline)
    d = b.element(DEMO_PROJECT, lib, "valve", "genie")
    d.create_object("rectangle", (19, 6, 21, 20), colour=_INK)
    act = d.create_object("rectangle", (12, 0, 28, 6), colour=_STEEL)
    d.set_gradient(act, 1, 0xD5DADF)
    for colour, expr in ((_RED, "%Tag%"), (_GREEN, "NOT %Tag%")):
        v = d.create_object("polygon", None, [(0, 8), (40, 32), (40, 8), (0, 32)], colour=colour)
        assert d.ensure_property(v, "VISIBLE", expr)
    _demo_text(d, (-15, 48, 55, 64), "%Label%", size=9, justify="centre")
    d.save()
    _w(os.path.join(b.proj(DEMO_PROJECT), "fxdemo.frm"), _ci(DEMO_FRM))


def _demo_header(d, width, title):
    bar = d.create_object("rectangle", (0, 0, width, 48), colour=0x2C3E50)
    d.set_gradient(bar, 1, 0x34495E)
    _demo_text(d, (20, 10, 600, 38), title, size=16, bold=True, colour=_WHITE)
    _demo_text(
        d, (width - 320, 16, width - 20, 34), "Line 1  |  Shift A", size=10, colour=0xD0D6DC, justify="right"
    )


def _demo_button(d, rect, caption, command, name):
    bt = _name(d, d.create_object("button", rect, text=caption), name)
    d.set_input_commands(bt, touch={"CMD": command})
    return bt


def _demo_pipe(d, points):
    p = d.create_object("pipe", None, points)
    d.set_colour(p, _PIPE)
    d.set_line_width(p, 10)
    return p


def _demo_place(d, element, pos, tag, label, name):
    i = d.place_library_element("fxdemo", element, "ctm", pos, params={"Tag": tag, "Label": label})
    return _name(d, i, name)


def _demo_overview(b: Build) -> None:
    d = b.blank_page(DEMO_PROJECT, "Overview", size=(1024, 640), background=0xC8CCD0)
    _demo_header(d, 1024, "Water treatment: overview")
    _demo_panel(d, (20, 64, 724, 470), "Process")
    # the pipes first, so the equipment draws over them
    # (the process line runs at y 380; a pump is 44 across and a valve 40, placed by their
    # centres on it; the tanks' walls are x 60 to 170 and 470 to 580, y 122 to 292)
    line = 380
    _demo_pipe(d, [(28, 150), (60, 150)])
    _demo_pipe(d, [(170, 270), (210, 270), (210, line), (250, line)])
    _demo_pipe(d, [(294, line), (350, line)])
    _demo_pipe(d, [(390, line), (440, line), (440, 270), (470, 270)])
    _demo_pipe(d, [(580, 270), (620, 270), (620, line), (640, line)])
    _demo_pipe(d, [(680, line), (716, line)])
    _demo_place(d, "tank", (60, 100), "T101_LEVEL", "Raw water", "T101")
    _demo_place(d, "pump", (272 - 22, line - 22), "P101_RUN", "P-101", "P101")
    _demo_place(d, "valve", (370 - 20, line - 20), "V102_OPEN", "V-102", "V102")
    _demo_place(d, "tank", (470, 100), "T201_LEVEL", "Clear water", "T201")
    _demo_place(d, "valve", (660 - 20, line - 20), "V201_OPEN", "V-201", "V201")

    _demo_panel(d, (740, 64, 1004, 470), "Readings")
    rows = (
        ("Inlet flow", "FT101_FLOW:###.#", "m3/h"),
        ("Outlet flow", "FT201_FLOW:###.#", "m3/h"),
        ("Pressure", "PT101_PRESS:#.##", "bar"),
        ("Turbidity", "AT101_TURB:#.##", "NTU"),
        ("pH", "AT102_PH:#.##", ""),
        ("Chlorine", "AT103_CL2:#.##", "mg/l"),
    )
    for k, (label, expr, unit) in enumerate(rows):
        y = 104 + k * 40
        _demo_text(d, (756, y, 870, y + 18), label)
        _demo_value(d, (870, y - 3, 990, y + 21), expr, unit)

    _demo_panel(d, (20, 486, 1004, 620), "Control")
    _demo_button(d, (40, 530, 170, 570), "Start line", "DemoLine(1)", "StartLine")
    _demo_button(d, (190, 530, 320, 570), "Stop line", "DemoLine(0)", "StopLine")
    _demo_button(d, (340, 530, 470, 570), "Pump P-101", 'PageDisplay("Pump")', "ToPump")
    _demo_status(d, (680, 538, 704, 562), "LINE_RUN")
    h = _demo_text(d, (714, 540, 980, 560), "Line running", size=10, bold=True)
    d.set_text_display(
        h,
        [("TXTMODE", "0"), ("TXT_A", "LINE_RUN"), ("TXT_ON", "Line running"), ("TXT_OFF", "Line stopped")],
        caption="Line running",
    )
    d.set_page_record({"TITLE": "Overview", "ENTRY_FUNC": "DemoStart()"})
    d.save()


def _demo_pump_page(b: Build) -> None:
    d = b.blank_page(DEMO_PROJECT, "Pump", size=(1024, 640), background=0xC8CCD0)
    _demo_header(d, 1024, "Pump P-101")
    _demo_panel(d, (20, 64, 500, 470), "Pump")
    # (the line at y 260, the pump and the valve placed by their centres on it)
    line = 260
    _demo_pipe(d, [(40, line), (230, line)])
    _demo_pipe(d, [(274, line), (350, line)])
    _demo_pipe(d, [(390, line), (480, line)])
    _demo_text(d, (40, line - 30, 200, line - 14), "Suction", size=9)
    _demo_text(d, (320, line - 30, 480, line - 14), "Discharge", size=9, justify="right")
    _demo_place(d, "pump", (252 - 22, line - 22), "P101_RUN", "P-101", "P101")
    _demo_place(d, "valve", (370 - 20, line - 20), "V102_OPEN", "V-102", "V102")
    _demo_panel(d, (520, 64, 1004, 470), "Values")
    rows = (
        ("Speed", "P101_SPEED:###", "%"),
        ("Flow", "FT101_FLOW:###.#", "m3/h"),
        ("Pressure", "PT101_PRESS:#.##", "bar"),
    )
    for k, (label, expr, unit) in enumerate(rows):
        y = 110 + k * 44
        _demo_text(d, (540, y, 700, y + 18), label)
        _demo_value(d, (700, y - 3, 840, y + 21), expr, unit)
    for k, (label, expr) in enumerate((("Running", "P101_RUN"), ("No fault", "NOT P101_FAULT"))):
        y = 110 + (len(rows) + k) * 44
        _demo_text(d, (540, y, 700, y + 18), label)
        _demo_status(d, (700, y - 2, 722, y + 20), expr)
    _demo_panel(d, (20, 486, 1004, 620), "Control")
    _demo_button(d, (40, 530, 170, 570), "Start line", "DemoLine(1)", "StartLine")
    _demo_button(d, (190, 530, 320, 570), "Stop line", "DemoLine(0)", "StopLine")
    _demo_button(d, (340, 530, 470, 570), "Overview", 'PageDisplay("Overview")', "ToOverview")
    d.set_page_record({"TITLE": "Pump P-101", "ENTRY_FUNC": "DemoStart()"})
    d.save()


def build_demo(b: Build) -> None:
    """FxDemo: tags, alarms, equipment, Cicode, three genies and two pages."""
    os.makedirs(b.proj(DEMO_PROJECT), exist_ok=True)
    b.page_tables(DEMO_PROJECT)
    b.project_cit(DEMO_PROJECT, res="XGA", title=True, background=0xC8CCD0)
    project_tables(b, DEMO_PROJECT, DEMO_TAGS, DEMO_EQUIPMENT)
    _w(os.path.join(b.proj(DEMO_PROJECT), "fxdemo.ci"), _ci(DEMO_CI))
    _w(os.path.join(b.proj(DEMO_PROJECT), "citect.ini"), _ci("[Page]\nStartup=Overview\n"))
    _demo_genies(b)
    _demo_overview(b)
    _demo_pump_page(b)
    build_indexes(b, (DEMO_PROJECT,))
    build_project_files(b, (DEMO_PROJECT,))


#: the projects the steps before build_demo make
_BEFORE_DEMO = tuple(p for p in PROJECTS if p != DEMO_PROJECT)

STEPS = (
    build_install,
    build_include,
    build_include_stand_ins,
    build_system,
    build_lib,
    build_lib_more,
    build_style,
    build_style_more,
    build_main,
    build_main_more,
    build_main_extra,
    build_main_third,
    build_other,
    build_sim,
    build_sim_more,
    build_perf,
    build_sa,
    build_cycle,
    build_indexes,
    build_project_files,
    build_demo,
)


def generate(out: str) -> None:
    """Write the install into ``out`` (emptied first)."""
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    work = tempfile.mkdtemp(prefix="fxgen_")
    old = _environment(work, out)
    try:
        sys.path.insert(0, REPO)
        from ctgedit.infra import identity

        identity.reset()
        b = Build(out)
        with _guarded(), _fill_apply_on_save():
            for step in STEPS:
                step(b)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(work, ignore_errors=True)


def _tree(root: str) -> dict:
    out = {}
    for d, _subs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root).replace("\\", "/")] = p
    return out


def check(committed: str) -> int:
    tmp = tempfile.mkdtemp(prefix="fxcheck_")
    try:
        fresh = os.path.join(tmp, "FxInstall")
        generate(fresh)
        a, b = _tree(committed), _tree(fresh)
        bad = sorted(set(a) ^ set(b))
        bad += sorted(k for k in set(a) & set(b) if not filecmp.cmp(a[k], b[k], shallow=False))
        if bad:
            print("make_fixtures --check: %d files differ:" % len(bad))
            for k in bad[:40]:
                print("  " + k)
            return 1
        print("make_fixtures --check: %d files, all the same" % len(a))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--check",
        action="store_true",
        help="write into a temp folder and compare with the committed fixtures",
    )
    ap.add_argument("--out", default=DEFAULT_OUT, help="the folder to write")
    a = ap.parse_args(argv)
    if a.check:
        return check(a.out)
    generate(a.out)
    total = sum(os.path.getsize(p) for p in _tree(a.out).values())
    print("make_fixtures: wrote %s (%d files, %d KB)" % (a.out, len(_tree(a.out)), total // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
