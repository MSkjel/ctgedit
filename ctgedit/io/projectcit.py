"""Read and write a project's ``PROJECT.CIT``."""

from __future__ import annotations

import struct
from typing import Dict, List, Optional, Tuple

from ..format.cfb import SIGNATURE as _CFB_SIG

#: the Page Defaults resolution list, index = the stored 'Res' value
RESOLUTIONS = ("Default", "VGA", "SVGA", "XGA", "SXGA", "User", "WUXGA", "HD1080", "HD768", "UHD4K")


def find(project_dir: str) -> Optional[str]:
    """The project's PROJECT.CIT (any case), or None."""
    from ..format.fileio import find_ci

    return find_ci(project_dir, "PROJECT.CIT")


def _layout(b: bytes) -> Optional[Dict[str, Tuple[int, List[int], int, int]]]:
    """{stream name: (dir entry offset, [file offset of each allocated
    sector], sector size, size)} of the compound file's streams."""
    if b[:8] != _CFB_SIG or len(b) < 512:
        return None
    ssz = 1 << struct.unpack_from("<H", b, 30)[0]
    mssz = 1 << struct.unpack_from("<H", b, 32)[0]
    nfat = struct.unpack_from("<I", b, 44)[0]
    dir_start = struct.unpack_from("<I", b, 48)[0]
    cutoff = struct.unpack_from("<I", b, 56)[0]
    minifat_start = struct.unpack_from("<I", b, 60)[0]
    difat = [x for x in struct.unpack_from("<109I", b, 76) if x < 0xFFFFFFFA][:nfat]

    def off(sec):
        return (sec + 1) * ssz

    fat: List[int] = []
    for sec in difat:
        if off(sec) + ssz <= len(b):
            fat += struct.unpack_from("<%dI" % (ssz // 4), b, off(sec))

    def chain(start, table):
        out, seen = [], set()
        while start < 0xFFFFFFFA and start < len(table) and start not in seen:
            seen.add(start)
            out.append(start)
            start = table[start]
        return out

    ents = [off(sec) + j * 128 for sec in chain(dir_start, fat) for j in range(ssz // 128)]
    ents = [eo for eo in ents if eo + 128 <= len(b)]
    if not ents:
        return None
    minifat: List[int] = []
    for sec in chain(minifat_start, fat):
        minifat += struct.unpack_from("<%dI" % (ssz // 4), b, off(sec))
    ministream = chain(struct.unpack_from("<I", b, ents[0] + 116)[0], fat)
    out: Dict[str, Tuple[int, List[int], int, int]] = {}
    for eo in ents:
        nl = struct.unpack_from("<H", b, eo + 64)[0]
        if b[eo + 66] != 2 or not 2 <= nl <= 64:
            continue
        name = b[eo : eo + nl - 2].decode("utf-16-le", "replace")
        start, size = struct.unpack_from("<II", b, eo + 116)
        spans: List[int] = []
        if size < cutoff:
            for ms in chain(start, minifat):
                sec_i, within = divmod(ms * mssz, ssz)
                if sec_i >= len(ministream):
                    spans = []
                    break
                spans.append(off(ministream[sec_i]) + within)
            unit = mssz
        else:
            spans = [off(s) for s in chain(start, fat)]
            unit = ssz
        if len(spans) * unit < size:
            continue  # a broken chain: not readable
        out[name] = (eo, spans, unit, size)
    return out


def read_streams(path: str) -> Dict[str, bytes]:
    """{stream name: bytes} of PROJECT.CIT (empty when it cannot be read)."""
    try:
        with open(path, "rb") as fh:
            b = fh.read()
    except OSError:
        return {}
    lay = _layout(b)
    if lay is None:
        return {}
    return {
        name: b"".join(b[so : so + unit] for so in spans)[:size]
        for name, (_eo, spans, unit, size) in lay.items()
    }


def patch_streams(path: str, values: Dict[str, bytes]) -> bool:
    """Rewrite streams of PROJECT.CIT in place."""
    from ..format import journal

    with journal.open_patch(path) as fh:
        b = fh.read()
        lay = _layout(b)
        if lay is None:
            return False
        cutoff = struct.unpack_from("<I", b, 56)[0]
        plan = []
        for name, value in values.items():
            ent = lay.get(name)
            if ent is None:
                return False
            eo, spans, unit, size = ent
            if len(value) > len(spans) * unit or (size < cutoff) != (len(value) < cutoff):
                return False
            plan.append((eo, spans, unit, bytes(value)))
        for eo, spans, unit, value in plan:
            data = value.ljust(len(spans) * unit, b"\0")
            for k, so in enumerate(spans):
                fh.seek(so)
                fh.write(data[k * unit : (k + 1) * unit])
            fh.seek(eo + 120)
            fh.write(struct.pack("<I", len(value)))
    return True


def _u32(v: Optional[bytes]) -> Optional[int]:
    return struct.unpack("<I", v[:4])[0] if v is not None and len(v) >= 4 else None


def page_defaults(project_dir: str) -> Optional[dict]:
    """The project's Page Defaults {'style', 'resolution' (a RESOLUTIONS
    name), 'title_bar', 'background' (0x00RRGGBB)}, None when the folder has
    no readable PROJECT.CIT carrying them."""
    from ..format.textcodec import CODEC

    path = find(project_dir)
    if path is None:
        return None
    s = read_streams(path)
    if "Style" not in s or "Res" not in s:
        return None
    res = _u32(s.get("Res"))
    colour = _u32(s.get("BackgroundColor"))
    return {
        "style": s["Style"].split(b"\0", 1)[0].decode(CODEC, "replace"),
        "resolution": RESOLUTIONS[res] if res is not None and res < len(RESOLUTIONS) else "Default",
        "title_bar": bool(_u32(s.get("ShowTitle"))),
        "background": (colour & 0xFFFFFF) if colour is not None else None,
    }


def set_page_defaults(project_dir: str, values: dict) -> bool:
    """Write Page Defaults into the project's PROJECT.CIT (what CtDraw's Page
    Defaults OK does): 'style', 'resolution' (a RESOLUTIONS name; '4K' =
    UHD4K), 'title_bar', 'background'. False when the project has no
    PROJECT.CIT, a value has no stored form or the file cannot be patched."""
    from ..format.textcodec import CODEC

    path = find(project_dir)
    if path is None:
        return False
    out: Dict[str, bytes] = {}
    if "style" in values:
        style = str(values["style"]).strip().encode(CODEC)
        if not style or len(style) >= 0x40:
            return False
        out["Style"] = style
    if "resolution" in values:
        want = str(values["resolution"]).strip().lower()
        want = "uhd4k" if want == "4k" else want
        idx = (
            [r.lower() for r in RESOLUTIONS].index(want) if want in [r.lower() for r in RESOLUTIONS] else None
        )
        if idx is None:
            return False
        out["Res"] = struct.pack("<I", idx)
    if "title_bar" in values:
        out["ShowTitle"] = struct.pack("<I", 1 if values["title_bar"] else 0)
    if values.get("background") is not None:
        out["Background"] = struct.pack("<I", 0x5443)
        out["BackgroundColor"] = struct.pack("<I", int(values["background"]) & 0xFFFFFF)
    try:
        return patch_streams(path, out)
    except OSError:
        return False
