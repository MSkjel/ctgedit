"""The text codec for every string CtDraw stores."""

from __future__ import annotations

import codecs
import importlib
import os
import sys
from typing import List, Tuple

# code pages Python ships a single-byte charmap table for
_SINGLE_BYTE = {874, 1250, 1251, 1252, 1253, 1254, 1255, 1256, 1257, 1258}
#: the double-byte (DBCS) ANSI code pages and the Python codec that decodes
#: each (936 = GBK)
_DOUBLE_BYTE = {932: "cp932", 936: "gbk", 949: "cp949", 950: "cp950"}
#: single bytes a Windows DBCS code page maps that Python's codec leaves out
#: (MultiByteToWideChar(936) maps 0x80 to the euro sign)
_DBCS_EXTRA = {936: {0x80: "€"}}


def _lossless_table(cp: int) -> str:
    """256-char decoding table for code page ``cp`` with every byte the
    Python table leaves undefined mapped to a code point of its own (the
    matching U+00xx C1 control when free -- what Windows does for 1252 --
    else a private-use code point), so the table is a bijection."""
    base = importlib.import_module("encodings.cp%d" % cp).decoding_table
    used = {ch for ch in base if ch != "￾"}
    out = []
    for i, ch in enumerate(base):
        if ch == "￾":
            ch = chr(i) if chr(i) not in used else chr(0xF700 + i)
            used.add(ch)
        out.append(ch)
    table = "".join(out)
    assert len(table) == 256 and len(set(table)) == 256
    return table


def _detect_codepage() -> int:
    env = os.environ.get("CTG_CODEPAGE", "").strip().lower().lstrip("cp")
    if env.isdigit():
        cp = int(env)
        return cp if cp in _SINGLE_BYTE or cp in _DOUBLE_BYTE else 1252
    if sys.platform == "win32":
        try:
            import ctypes

            cp = int(ctypes.windll.kernel32.GetACP())
            if cp in _SINGLE_BYTE or cp in _DOUBLE_BYTE:
                return cp
        except Exception:
            pass
    return 1252


CODEPAGE = _detect_codepage()
CODEC = "ctg%d" % CODEPAGE
#: True on a double-byte (East-Asian) ANSI code page: one character may be
#: two bytes, so byte lengths are not character counts
MULTIBYTE = CODEPAGE in _DOUBLE_BYTE


# --------------------------------------------------------------------------- DBCS
def _dbcs_scan(b: bytes, base: str, extra: dict, final: bool = True) -> Tuple[str, int]:
    """Decode DBCS bytes losslessly: (text, bytes consumed). A byte the code
    page does not map -- or a sequence that does not encode back to the same
    bytes -- becomes the escaped byte U+DC00 + byte. With ``final`` False a
    trailing lead byte waiting for its second byte is not consumed."""

    def same(s: str, raw: bytes) -> bool:
        try:
            return _dbcs_encode(s, "strict", base, extra) == raw
        except UnicodeError:
            return False

    try:
        s = b.decode(base)
        if same(s, b):
            return s, len(b)
    except UnicodeError:
        pass
    out: List[str] = []
    i, n = 0, len(b)
    while i < n:
        c = b[i]
        if c < 0x80:
            out.append(chr(c))
            i += 1
            continue
        if i + 1 < n:
            pair = b[i : i + 2]
            try:
                ch = pair.decode(base)
                if len(ch) == 1 and same(ch, pair):
                    out.append(ch)
                    i += 2
                    continue
            except UnicodeError:
                pass
        one = bytes((c,))
        ch = extra.get(c)
        if ch is None:
            try:
                ch = one.decode(base)
                if not same(ch, one):
                    ch = None
            except UnicodeError:
                ch = None
        if ch is None and i + 1 == n and not final:
            break  # a lead byte whose trail byte comes next
        out.append(ch if ch is not None else chr(0xDC00 + c))
        i += 1
    return "".join(out), i


def _escape_or(repl: bytes):
    """An encode error handler: escaped bytes (U+DC80..U+DCFF) go back to their
    byte, any other character the code page cannot store becomes ``repl``."""

    def handler(exc):
        if not isinstance(exc, UnicodeEncodeError):
            raise exc
        out = bytearray()
        for ch in exc.object[exc.start : exc.end]:
            o = ord(ch)
            if 0xDC80 <= o <= 0xDCFF:
                out.append(o - 0xDC00)
            else:
                out += repl
        return bytes(out), exc.end

    return handler


codecs.register_error("ctgdbcs_replace", _escape_or(b"?"))
codecs.register_error("ctgdbcs_ignore", _escape_or(b""))
# 'strict' keeps the escaped bytes and still refuses a real unencodable character
_DBCS_ERRORS = {"strict": "surrogateescape", "replace": "ctgdbcs_replace", "ignore": "ctgdbcs_ignore"}


def _dbcs_encode(s: str, errors: str, base: str, extra: dict) -> bytes:
    if extra:
        for byte, ch in extra.items():
            s = s.replace(ch, chr(0xDC00 + byte))
    return s.encode(base, _DBCS_ERRORS.get(errors, "surrogateescape"))


if MULTIBYTE:
    _BASE = _DOUBLE_BYTE[CODEPAGE]
    _EXTRA = _DBCS_EXTRA.get(CODEPAGE, {})

    class _Codec(codecs.Codec):
        def encode(self, s, errors="strict"):
            return _dbcs_encode(s, errors, _BASE, _EXTRA), len(s)

        def decode(self, b, errors="strict"):
            return _dbcs_scan(bytes(b), _BASE, _EXTRA)

    class _IncrementalEncoder(codecs.IncrementalEncoder):
        def encode(self, s, final=False):
            return _dbcs_encode(s, self.errors, _BASE, _EXTRA)

    class _IncrementalDecoder(codecs.BufferedIncrementalDecoder):
        def _buffer_decode(self, b, errors, final):
            return _dbcs_scan(bytes(b), _BASE, _EXTRA, final)
else:
    _DECODING_TABLE = _lossless_table(CODEPAGE)
    _ENCODING_TABLE = codecs.charmap_build(_DECODING_TABLE)

    class _Codec(codecs.Codec):
        def encode(self, s, errors="strict"):
            return codecs.charmap_encode(s, errors, _ENCODING_TABLE)

        def decode(self, b, errors="strict"):
            return codecs.charmap_decode(b, errors, _DECODING_TABLE)

    class _IncrementalEncoder(codecs.IncrementalEncoder):
        def encode(self, s, final=False):
            return codecs.charmap_encode(s, self.errors, _ENCODING_TABLE)[0]

    class _IncrementalDecoder(codecs.IncrementalDecoder):
        def decode(self, b, final=False):
            return codecs.charmap_decode(b, self.errors, _DECODING_TABLE)[0]


class _StreamWriter(_Codec, codecs.StreamWriter):
    pass


class _StreamReader(_Codec, codecs.StreamReader):
    pass


_INFO = codecs.CodecInfo(
    name=CODEC,
    encode=_Codec().encode,
    decode=_Codec().decode,
    incrementalencoder=_IncrementalEncoder,
    incrementaldecoder=_IncrementalDecoder,
    streamreader=_StreamReader,
    streamwriter=_StreamWriter,
)


def _search(name: str):
    return _INFO if name.replace("-", "_").lower() == CODEC else None


codecs.register(_search)


def dec(b: bytes) -> str:
    """Decode on-disk bytes (never fails: every byte is mapped)."""
    return bytes(b).decode(CODEC)


def enc(s: str, errors: str = "replace") -> bytes:
    """Encode text for disk. ``errors='replace'`` writes '?' for a character
    the ANSI code page cannot hold (CtDraw's WideCharToMultiByte default
    char); pass ``'strict'`` to get an exception instead."""
    return str(s).encode(CODEC, errors)


def unencodable(s: str) -> List[str]:
    """The distinct characters of ``s`` the page code page cannot store (in
    order of first appearance) -- empty when ``s`` encodes cleanly."""
    s = str(s)
    try:
        s.encode(CODEC)
        return []
    except UnicodeEncodeError:
        pass
    bad: List[str] = []
    for ch in s:
        if ch not in bad:
            try:
                ch.encode(CODEC)
            except UnicodeEncodeError:
                bad.append(ch)
    return bad


class UnencodableTextError(ValueError):
    """Text has characters the ANSI code page can't store."""


def describe(chars) -> str:
    """The characters as 'X' (U+0058), 'Y' (U+0059), for a message."""
    return ", ".join("'%s' (U+%04X)" % (c, ord(c)) for c in chars)


def replacing() -> bool:
    """Text the code page cannot store becomes '?', as the Graphics Builder and the Project
    Editor store it (``CTG_TEXT_REPLACE=1``, Settings > Pages and libraries), instead of being
    refused."""
    return os.environ.get("CTG_TEXT_REPLACE") == "1"


def encode_field(value, what: str = "") -> bytes:
    """``value`` as the bytes a project database / file field stores, or
    :class:`UnencodableTextError` naming the characters that code page cannot
    hold and where (``what``, e.g. 'the TITLE field of pages.DBF') -- never
    a silent '?' (unless :func:`replacing`)."""
    s = "" if value is None else str(value)
    try:
        return s.encode(CODEC)
    except UnicodeEncodeError:
        pass
    if replacing():
        return s.encode(CODEC, "replace")
    bad = unencodable(s)
    raise UnencodableTextError(
        "%s cannot be stored in %s: Plant SCADA keeps it in the Windows-%d code "
        "page. Remove or replace %s and try again."
        % (
            describe(bad),
            what or "the project database",
            CODEPAGE,
            "that character" if len(bad) == 1 else "those characters",
        )
    )


def truncate(raw: bytes, limit: int) -> bytes:
    """``raw`` cut to at most ``limit`` bytes without splitting a double-byte
    character (single-byte code pages: a plain slice)."""
    raw = bytes(raw)
    if len(raw) <= limit or not MULTIBYTE:
        return raw[:limit]
    # the character boundaries of the lossless decode
    out, used = b"", 0
    for ch in raw.decode(CODEC):
        b = ch.encode(CODEC)
        if used + len(b) > limit:
            break
        out += b
        used += len(b)
    return out
