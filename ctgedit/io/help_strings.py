"""Read the localised strings referenced by SCADAHELP.DBF."""

from __future__ import annotations

import ctypes
import locale
import os
import re
import struct
from functools import lru_cache
from pathlib import Path

from ..infra import caches as _caches

_TOKEN = re.compile(r"\$\((IDS_\w+)\)")
_RESOURCE_MAGIC = bytes.fromhex("ce ca ef be")


def _length(data: memoryview, pos: int) -> tuple[int, int]:
    size = 0
    for shift in range(0, 35, 7):
        byte = data[pos]
        pos += 1
        size |= (byte & 127) << shift
        if byte < 128:
            return size, pos
    raise ValueError("Invalid string length")


def _string(data: memoryview, pos: int, encoding: str) -> tuple[str, int]:
    size, start = _length(data, pos)
    end = start + size
    if end > len(data):
        raise ValueError("Truncated resource string")
    return bytes(data[start:end]).decode(encoding), end


def _read_table(data: memoryview) -> dict[str, str]:
    header_version, header_size = struct.unpack_from("<II", data, 4)
    if header_version != 1:
        return {}
    pos = 12 + header_size
    version, count, types = struct.unpack_from("<III", data, pos)
    if version != 2 or types or count > len(data) // 8:
        return {}
    # String-only .resources v2: aligned hash and name-offset arrays, then
    # one offset to the value section. Name offsets start after that word.
    offsets = ((pos + 12 + 7) & ~7) + count * 4
    names = offsets + count * 4 + 4
    (values,) = struct.unpack_from("<I", data, names - 4)
    out = {}
    for i in range(count):
        (offset,) = struct.unpack_from("<I", data, offsets + i * 4)
        key, end = _string(data, names + offset, "utf-16-le")
        if key.startswith("IDS_"):
            (relative,) = struct.unpack_from("<I", data, end)
            pos = values + relative
            if data[pos] == 1:  # ResourceTypeCode.String
                out[key], _ = _string(data, pos + 1, "utf-8")
    return out


@lru_cache(maxsize=16)
def _assembly_strings(path: Path, stamp: int, size: int) -> dict[str, str]:
    raw = path.read_bytes()
    data = memoryview(raw)
    result = {}
    start = 4
    while (pos := raw.find(_RESOURCE_MAGIC, start)) >= 0:
        start = pos + 4
        (length,) = struct.unpack_from("<I", raw, pos - 4)
        if length < 24 or pos + length > len(raw):
            continue
        try:
            result.update(_read_table(data[pos : pos + length]))
        except IndexError, ValueError, struct.error:
            continue
    return result


# (keyed by the file's path, time and size: never stale)
_caches.register("help_strings.assembly", _assembly_strings, on=())


def translate(value: str, bin_dir: str) -> str | None:
    if not _TOKEN.search(value):
        return value
    base = Path(bin_dir)
    paths = [base / "SE.SCADASystem.dll"]
    if os.name == "nt":
        lang = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "")
        culture = lang.replace("_", "-")
        for name in dict.fromkeys((culture.partition("-")[0], culture)):
            if name:
                paths.append(base / name / "SE.SCADASystem.resources.dll")
    found = {}
    for path in paths:
        try:
            stat = path.stat()
            found.update(_assembly_strings(path, stat.st_mtime_ns, stat.st_size))
        except OSError:
            continue
    try:
        return _TOKEN.sub(lambda m: found[m[1]], value)
    except KeyError:
        return None
