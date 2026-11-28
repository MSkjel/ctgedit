"""The project colour palette and CtDraw's legacy colour numbers."""

from __future__ import annotations

from ..infra.paths import folder_key, path_key


import functools
import glob
import os
import re
from typing import List, Optional, Sequence, Tuple

NO_COLOUR = 0xFF000000
Pair = Tuple[int, int]
#: bumped by every :meth:`Palette.save` (documents re-find their palette file)
GENERATION = 0

#: CtDraw's legacy colour order: palette entry i
#: holds the legacy (V1) colour number V1_ORDER[i]
V1_ORDER = (
    0,
    129,
    130,
    131,
    132,
    133,
    134,
    135,
    136,
    137,
    138,
    139,
    140,
    141,
    142,
    15,
    40,
    143,
    144,
    145,
    146,
    147,
    148,
    149,
    150,
    151,
    152,
    153,
    154,
    155,
    156,
    47,
    56,
    157,
    158,
    159,
    160,
    161,
    162,
    163,
    164,
    165,
    166,
    167,
    168,
    169,
    170,
    63,
    88,
    171,
    172,
    173,
    174,
    175,
    176,
    177,
    178,
    179,
    180,
    181,
    182,
    183,
    184,
    95,
    120,
    242,
    185,
    243,
    186,
    244,
    187,
    245,
    188,
    246,
    189,
    247,
    190,
    191,
    192,
    127,
    193,
    12,
    194,
    14,
    195,
    10,
    196,
    11,
    197,
    9,
    198,
    13,
    199,
    200,
    201,
    7,
    248,
    60,
    202,
    62,
    203,
    58,
    204,
    59,
    205,
    57,
    206,
    61,
    207,
    208,
    209,
    39,
    249,
    92,
    210,
    94,
    211,
    90,
    212,
    91,
    213,
    89,
    214,
    93,
    215,
    216,
    217,
    55,
    250,
    4,
    218,
    6,
    219,
    2,
    220,
    3,
    221,
    1,
    222,
    5,
    223,
    224,
    225,
    87,
    251,
    52,
    226,
    54,
    227,
    50,
    228,
    51,
    229,
    49,
    230,
    53,
    231,
    232,
    233,
    103,
    128,
    84,
    234,
    86,
    235,
    82,
    236,
    83,
    237,
    81,
    238,
    85,
    239,
    240,
    241,
    8,
    16,
    80,
    20,
    22,
    18,
    19,
    17,
    21,
    71,
    28,
    30,
    26,
    27,
    25,
    29,
    252,
    32,
    96,
    36,
    38,
    34,
    35,
    33,
    37,
    24,
    44,
    46,
    42,
    43,
    41,
    45,
    31,
    48,
    112,
    68,
    70,
    66,
    67,
    65,
    69,
    72,
    76,
    78,
    74,
    75,
    73,
    77,
    79,
    64,
    253,
    100,
    102,
    98,
    99,
    97,
    101,
    104,
    108,
    110,
    106,
    107,
    105,
    109,
    111,
    254,
    255,
    116,
    118,
    114,
    115,
    113,
    117,
    119,
    124,
    126,
    122,
    123,
    121,
    125,
    23,
)

#: the sixteen classic Citect colours (V1 0..15), used when no palette file
#: is found: black, blue, green, cyan, red, magenta, brown, grey, dark grey,
#: light blue / green / cyan / red / magenta, yellow, white
STANDARD16 = (
    0x000000,
    0x000080,
    0x008000,
    0x008080,
    0x800000,
    0x800080,
    0x808000,
    0xBFBFBF,
    0x7F7F7F,
    0x0000FF,
    0x00FF00,
    0x00FFFF,
    0xFF0000,
    0xFF00FF,
    0xFFFF00,
    0xFFFFFF,
)

_LINE = re.compile(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)")


def parse(text: str) -> List[Pair]:
    """The palette entries of a ``citect.pal`` text, in file order."""
    out: List[Pair] = []
    for ln in text.splitlines()[1:]:
        m = _LINE.match(ln)
        if m is None:
            continue
        v = [int(x) & 0xFF for x in m.groups()]
        out.append(
            (
                ((v[3] << 24) | (v[0] << 16) | (v[1] << 8) | v[2]) & 0xFFFFFFFF,
                ((v[7] << 24) | (v[4] << 16) | (v[5] << 8) | v[6]) & 0xFFFFFFFF,
            )
        )
    return out


def v1_table(entries: Sequence[Pair]) -> Tuple[Pair, ...]:
    """Colour number (V1 0..255) -> (on, off), from the palette entries."""
    table = [(NO_COLOUR, NO_COLOUR)] * 256
    for i, v1 in enumerate(V1_ORDER):
        if i < len(entries):
            table[v1] = entries[i]
    return tuple(table)


def standard_table() -> Tuple[Pair, ...]:
    """The table without a palette file: the sixteen classic colours."""
    table = [(NO_COLOUR, NO_COLOUR)] * 256
    for i, c in enumerate(STANDARD16):
        table[i] = (c, c)
    return tuple(table)


def _bin_palettes() -> List[str]:
    try:
        from ..infra.paths import scada_bin_dirs

        dirs = scada_bin_dirs()
    except Exception:  # noqa: BLE001 -- swallow-ok: no install: the classic colours
        dirs = []
    return [os.path.join(d, "citect.pal") for d in dirs]


def palette_file(project_dir: Optional[str]) -> Optional[str]:
    """The palette CtDraw uses for a project: its own ``citect.pal`` (any
    case), else the install's ``Bin\\citect.pal``; None when neither exists."""
    if project_dir and os.path.isdir(project_dir):
        for p in glob.glob(os.path.join(glob.escape(project_dir), "*.pal")):
            if os.path.basename(p).lower() == "citect.pal":
                return p
    for p in _bin_palettes():
        if os.path.isfile(p):
            return p
    return None


@functools.lru_cache(maxsize=32)
def _table_for(path: str, mtime: float, size: int) -> Tuple[Pair, ...]:
    with open(path, encoding="latin-1") as f:
        entries = parse(f.read())
    return v1_table(entries) if entries else standard_table()


def table_for_project(project_dir: Optional[str]) -> Tuple[Pair, ...]:
    """Colour number -> (on, off) of the project's palette (cached per file
    version)."""
    path = palette_file(project_dir)
    if path is None:
        return standard_table()
    try:
        st = os.stat(path)
        return _table_for(path, st.st_mtime, st.st_size)
    except OSError:
        return standard_table()


def palette_stamp(project_dir: Optional[str]):
    """(path, mtime, size) of the palette a project uses -- what changes when
    its favourites are edited (None without a palette file)."""
    path = palette_file(project_dir)
    if path is None:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return path, st.st_mtime, st.st_size


# --------------------------------------------------------------------------- the picker palette
# The palette is also what CtDraw's Color Picker shows (help 1172091 / 1172092):
# a 16 x 16 grid of
# the file's 256 entries in file order -- the first 11 rows the standard
# colours (incl. 'Transparent', entry 160), the last 5 the favourites, where
# an entry named 'Unused' (white) is a free slot.
# Edit Favorite Colors writes the ACTIVE PROJECT's citect.pal (the old file
# is first renamed to citect_pal.bak; then
# '"Citect.Pal Version 2"' + 256 lines '%03.3i x 8 "name"' CRLF).
# The palette's rules: adding a colour takes the first 'Unused' entry
# (of the whole palette); a colour's name must not hold '"'; an
# empty or 'Unused' name, or one another entry has, becomes 'New Color (n)';
# the off half takes the on half's top byte, and a transparent on half
# makes off = on.

SIZE = 256
COLUMNS = 16
STANDARD_ROWS = 11
STANDARD = STANDARD_ROWS * COLUMNS  # entries 0..175 are the standard colours
UNUSED = "Unused"
TRANSPARENT = 0xFFFFFFFF
HEADER = '"Citect.Pal Version 2"'
_NAMED = re.compile(r'\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*"([^"]*)"')

#: CtDraw's messages
MSG_NAME_TAKEN = "A colour with this name already exists. Use another name."
MSG_FULL = "There are no unused colour favourites. Clear or replace a favourite."
MSG_NO_FLASHING = "Flashing colours are not available here."
MSG_UNUSED_NAME = "The name 'Unused' is reserved. Use another name."
MSG_WRITE_FAILED = "The palette file could not be written."


class PaletteError(ValueError):
    """A favourite the palette refuses, or a palette file that cannot be written."""


class PaletteColour(tuple):
    """One citect.pal entry: (on, off, name)."""

    __slots__ = ()

    def __new__(cls, on: int, off: int, name: str = ""):
        return tuple.__new__(cls, (int(on) & 0xFFFFFFFF, int(off) & 0xFFFFFFFF, str(name)))

    on = property(lambda self: self[0])
    off = property(lambda self: self[1])
    name = property(lambda self: self[2])

    @property
    def pair(self) -> Pair:
        return self[0], self[1]

    @property
    def flashing(self) -> bool:
        return self[0] != self[1]

    @property
    def transparent(self) -> bool:
        """Transparent: both halves' top byte 0xFF."""
        return (self[0] >> 24) == 0xFF and (self[1] >> 24) == 0xFF

    @property
    def used(self) -> bool:
        """In use: the name is not 'Unused' (without case)."""
        return self[2].lower() != UNUSED.lower()


UNUSED_COLOUR = PaletteColour(0x00FFFFFF, 0x00FFFFFF, UNUSED)


def verify(on: int, off: int) -> Pair:
    """A palette colour as CtDraw keeps it: the off half takes the on half's top byte; a
    transparent colour cannot flash (off = on)."""
    on &= 0xFFFFFFFF
    off = (off & 0x00FFFFFF) | (on & 0xFF000000)
    if on >> 24 == 0xFF:
        off = on
    return on, off


def parse_entries(text: str) -> List[PaletteColour]:
    """The named entries of a ``citect.pal`` text, in file order."""
    from .textcodec import CODEC

    out: List[PaletteColour] = []
    for ln in text.splitlines()[1:]:
        m = _NAMED.match(ln)
        if m is None:
            m2 = _LINE.match(ln)
            if m2 is None:
                continue
            v = [int(x) & 0xFF for x in m2.groups()]
            name = ""
        else:
            v = [int(x) & 0xFF for x in m.groups()[:8]]
            name = m.group(9)
        try:  # the file is ANSI (read as latin-1)
            name = name.encode("latin-1").decode(CODEC)
        except UnicodeError, LookupError:
            pass
        out.append(
            PaletteColour(
                (v[3] << 24) | (v[0] << 16) | (v[1] << 8) | v[2],
                (v[7] << 24) | (v[4] << 16) | (v[5] << 8) | v[6],
                name,
            )
        )
    return out


def format_entries(entries: Sequence[PaletteColour]) -> bytes:
    """The file CtDraw saves: the header, then per entry the on R G B A and
    off R G B A as %03.3i and the quoted name, CRLF."""
    from .textcodec import CODEC

    lines = [HEADER]
    for e in entries:
        on, off, name = e[0], e[1], e[2]
        lines.append(
            '%03d %03d %03d %03d %03d %03d %03d %03d "%s"'
            % (
                (on >> 16) & 0xFF,
                (on >> 8) & 0xFF,
                on & 0xFF,
                (on >> 24) & 0xFF,
                (off >> 16) & 0xFF,
                (off >> 8) & 0xFF,
                off & 0xFF,
                (off >> 24) & 0xFF,
                name,
            )
        )
    return ("\r\n".join(lines) + "\r\n").encode(CODEC, errors="replace")


def _builtin_entries() -> List[PaletteColour]:
    """No palette file anywhere: the sixteen classic colours + Transparent,
    the rest free."""
    out = [PaletteColour(c, c, "") for c in STANDARD16]
    out.append(PaletteColour(TRANSPARENT, TRANSPARENT, "Transparent"))
    out += [UNUSED_COLOUR] * (SIZE - len(out))
    return out


class Palette:
    """The project's citect.pal as CtDraw's Color Picker shows it: 256 named
    entries (the first :data:`STANDARD` fixed, the rest favourites). Edits
    change this object; :meth:`save` writes the project's file."""

    def __init__(
        self,
        entries: Sequence[PaletteColour],
        path: Optional[str] = None,
        project_dir: Optional[str] = None,
        source: str = "builtin",
    ):
        es = [PaletteColour(*e) for e in entries][:SIZE]
        es += [UNUSED_COLOUR] * (SIZE - len(es))
        self.entries: List[PaletteColour] = es
        self.path = path
        self.project_dir = project_dir
        #: 'project' (the project's own file), 'install' (Bin\citect.pal) or
        #: 'builtin' (no file)
        self.source = source

    @classmethod
    def for_project(cls, project_dir: Optional[str]) -> "Palette":
        path = palette_file(project_dir)
        entries = None
        if path is not None:
            try:
                with open(path, encoding="latin-1") as f:
                    entries = parse_entries(f.read())
            except OSError:
                entries = None
        if not entries:
            return cls(_builtin_entries(), None, project_dir, "builtin")
        own = bool(project_dir) and folder_key(path) == path_key(project_dir)
        return cls(entries, path, project_dir, "project" if own else "install")

    def copy(self) -> "Palette":
        return Palette(list(self.entries), self.path, self.project_dir, self.source)

    # -- queries ----------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, i: int) -> PaletteColour:
        return self.entries[i]

    @staticmethod
    def is_standard(index: int) -> bool:
        return 0 <= index < STANDARD

    def favourites(self) -> List[Tuple[int, PaletteColour]]:
        """The favourite slots (index, entry), used or not."""
        return [(i, self.entries[i]) for i in range(STANDARD, SIZE)]

    def find_unused(self) -> int:
        """The first 'Unused' entry (-1 when full)."""
        for i, e in enumerate(self.entries):
            if not e.used:
                return i
        return -1

    def index_of(self, on: int, off: Optional[int] = None) -> int:
        """The first USED entry holding this colour pair (-1 none)."""
        off = on if off is None else off
        want = (on & 0xFFFFFFFF, off & 0xFFFFFFFF)
        for i, e in enumerate(self.entries):
            if e.used and e.pair == want:
                return i
        return -1

    def transparent_index(self) -> int:
        for i, e in enumerate(self.entries):
            if e.used and e.transparent:
                return i
        return -1

    def name_problem(self, name: str, exclude: Optional[int] = None) -> str:
        """Why ``name`` cannot name a favourite ('' = it can; an empty name
        is given 'New Color (n)')."""
        name = name.strip()
        if not name:
            return ""
        if '"' in name:
            return 'A colour name cannot contain a double quote (").'
        if name.lower() == UNUSED.lower():
            return MSG_UNUSED_NAME
        for i, e in enumerate(self.entries):
            if i != exclude and e.used and e.name.lower() == name.lower():
                return MSG_NAME_TAKEN
        return ""

    def unique_name(self) -> str:
        """The lowest free 'New Color (n)'."""
        taken = {e.name.lower() for e in self.entries if e.used}
        n = 1
        while ("new color (%d)" % n) in taken:
            n += 1
        return "New Color (%d)" % n

    # -- edits (favourites only) ---------------------------------------------------------
    def _entry(self, on: int, off: int, name: str, exclude: Optional[int]) -> PaletteColour:
        problem = self.name_problem(name, exclude)
        if problem:
            raise PaletteError(problem)
        on, off = verify(on, off)
        name = name.strip()[:127] or self.unique_name()
        return PaletteColour(on, off, name)

    def add(self, on: int, off: Optional[int] = None, name: str = "") -> int:
        """Add a favourite in the first free slot.
        Raises :class:`PaletteError` when the name is refused or no slot is free."""
        off = on if off is None else off
        entry = self._entry(on, off, name, None)
        i = self.find_unused()
        while i != -1 and self.is_standard(i):
            # a free STANDARD slot (a hand-edited file) is not a favourite
            i = next((k for k in range(i + 1, SIZE) if not self.entries[k].used), -1)
        if i == -1:
            raise PaletteError(MSG_FULL)
        self.entries[i] = entry
        return i

    def replace(self, index: int, on: int, off: Optional[int] = None, name: str = "") -> None:
        """Replace favourite ``index``; an empty
        name keeps the entry's own."""
        self._check_favourite(index)
        off = on if off is None else off
        name = name.strip() or (self.entries[index].name if self.entries[index].used else "")
        self.entries[index] = self._entry(on, off, name, index)

    def clear(self, index: int) -> None:
        """Clear favourite ``index`` back to a free slot."""
        self._check_favourite(index)
        self.entries[index] = UNUSED_COLOUR

    def _check_favourite(self, index: int) -> None:
        if not 0 <= index < SIZE:
            raise PaletteError("There is no palette entry %d." % index)
        if self.is_standard(index):
            raise PaletteError(
                "The first %d rows are the standard colours and cannot be "
                "changed. Pick a favourite in the rows below them." % STANDARD_ROWS
            )

    # -- file ---------------------------------------------------------------------------
    def target_path(self) -> Optional[str]:
        """Where :meth:`save` writes: the project's own citect.pal (created
        from the install's when the project has none, as CtDraw does when it
        opens the project -- mode 2); None outside a project."""
        if self.source == "project" and self.path:
            return self.path
        from .projects import is_project_dir

        if is_project_dir(self.project_dir):
            return os.path.join(self.project_dir, "citect.pal")
        return None

    def save(self) -> str:
        """Write the palette into the project's citect.pal the way CtDraw does
        (the previous file kept as citect_pal.bak). Returns the path written;
        raises :class:`PaletteError` outside a project or when the file
        cannot be written."""
        from .fileio import write_atomic

        path = self.target_path()
        if path is None:
            raise PaletteError(
                "This page is not in a Plant SCADA project, so there is no "
                "project palette (citect.pal) for favourite colours."
            )
        data = format_entries(self.entries)
        try:
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    old = f.read()
                if old == data:
                    return path
                bak = os.path.join(os.path.dirname(path), "citect_pal.bak")
                write_atomic(bak, old)
            write_atomic(path, data)
        except OSError as exc:
            raise PaletteError("%s (%s)" % (MSG_WRITE_FAILED, exc)) from exc
        global GENERATION
        GENERATION += 1
        self.path, self.source = path, "project"
        return path


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("colourpalette.tables", _table_for, on=())
