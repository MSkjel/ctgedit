"""Convert .emf to .wmf with Windows GDI."""

from __future__ import annotations

import ctypes
import os
import struct
from typing import Optional, Tuple

#: EMR_HEADER's record type and its ' EMF' signature (at byte 40)
_EMR_HEADER = 1
_SIGNATURE = b" EMF"
_MM_ANISOTROPIC = 8
_CF_ENHMETAFILE = 14
#: a placeable Windows Metafile's key (bitmaps._PLACEABLE_KEY)
_PLACEABLE_KEY = 0x9AC6CDD7
#: the units per inch of the placeable header written here (twips)
_TWIPS = 1440


class EmfError(ValueError):
    """An enhanced metafile that cannot be converted; the message says why."""


def is_emf(data: bytes) -> bool:
    """``data`` starts with an enhanced metafile header."""
    return (
        len(data) >= 88 and struct.unpack_from("<I", data, 0)[0] == _EMR_HEADER and data[40:44] == _SIGNATURE
    )


def frame_size(data: bytes, dpi: int = 96) -> Tuple[int, int]:
    """The picture's size in pixels at ``dpi``: its frame (rclFrame, 0.01 mm), else its
    bounds (rclBounds, device pixels)."""
    if not is_emf(data):
        raise EmfError("the file is not an enhanced metafile")
    l, t, r, b = struct.unpack_from("<4i", data, 24)
    w, h = abs(r - l), abs(b - t)
    if w and h:
        return max(1, round(w * dpi / 2540)), max(1, round(h * dpi / 2540))
    l, t, r, b = struct.unpack_from("<4i", data, 8)
    return max(1, abs(r - l) + 1), max(1, abs(b - t) + 1)


def _gdi():
    if os.name != "nt":
        raise EmfError("enhanced metafiles can be converted on Windows only")
    gdi = ctypes.windll.gdi32
    user = ctypes.windll.user32
    vp = ctypes.c_void_p
    gdi.SetEnhMetaFileBits.argtypes = [ctypes.c_uint, ctypes.c_char_p]
    gdi.SetEnhMetaFileBits.restype = vp
    gdi.GetWinMetaFileBits.argtypes = [vp, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int, vp]
    gdi.GetWinMetaFileBits.restype = ctypes.c_uint
    gdi.GetEnhMetaFileBits.argtypes = [vp, ctypes.c_uint, ctypes.c_void_p]
    gdi.GetEnhMetaFileBits.restype = ctypes.c_uint
    gdi.DeleteEnhMetaFile.argtypes = [vp]
    gdi.DeleteEnhMetaFile.restype = ctypes.c_int
    user.GetDC.argtypes = [vp]
    user.GetDC.restype = vp
    user.ReleaseDC.argtypes = [vp, vp]
    user.ReleaseDC.restype = ctypes.c_int
    return gdi, user


def to_wmf(data: bytes, dpi: int = 96) -> bytes:
    """An enhanced metafile -> a PLACEABLE Windows Metafile (the .wmf file form) of the same
    picture, its bounding box the EMF's frame at ``dpi``. Raises :class:`EmfError`."""
    if not is_emf(data):
        raise EmfError("the file is not an enhanced metafile")
    w, h = frame_size(data, dpi)
    gdi, user = _gdi()
    hemf = gdi.SetEnhMetaFileBits(len(data), data)
    if not hemf:
        raise EmfError("Windows could not read the enhanced metafile")
    hdc = user.GetDC(None)
    try:
        n = gdi.GetWinMetaFileBits(hemf, 0, None, _MM_ANISOTROPIC, hdc)
        if not n:
            raise EmfError("Windows could not convert the enhanced metafile")
        buf = ctypes.create_string_buffer(n)
        if gdi.GetWinMetaFileBits(hemf, n, buf, _MM_ANISOTROPIC, hdc) != n:
            raise EmfError("Windows could not convert the enhanced metafile")
        wmf = buf.raw
    finally:
        user.ReleaseDC(None, hdc)
        gdi.DeleteEnhMetaFile(hemf)
    # the placeable header: the frame in twips, so the picture keeps its size
    bw, bh = round(w * _TWIPS / dpi), round(h * _TWIPS / dpi)
    head = struct.pack("<IH4hHI", _PLACEABLE_KEY, 0, 0, 0, bw, bh, _TWIPS, 0)
    check = 0
    for (word,) in struct.iter_unpack("<H", head):
        check ^= word
    return head + struct.pack("<H", check) + wmf


def clipboard_emf() -> Optional[bytes]:
    """The enhanced metafile another program put on the clipboard (CF_ENHMETAFILE), or
    None (none there, the clipboard is busy, not Windows)."""
    if os.name != "nt":
        return None
    try:
        gdi, user = _gdi()
        vp = ctypes.c_void_p
        user.OpenClipboard.argtypes = [vp]
        user.OpenClipboard.restype = ctypes.c_int
        user.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
        user.IsClipboardFormatAvailable.restype = ctypes.c_int
        user.GetClipboardData.argtypes = [ctypes.c_uint]
        user.GetClipboardData.restype = vp
        user.CloseClipboard.restype = ctypes.c_int
        if not user.IsClipboardFormatAvailable(_CF_ENHMETAFILE):
            return None
        if not user.OpenClipboard(None):
            return None
        try:
            h = user.GetClipboardData(_CF_ENHMETAFILE)
            if not h:
                return None
            n = gdi.GetEnhMetaFileBits(h, 0, None)
            if not n:
                return None
            buf = ctypes.create_string_buffer(n)
            if gdi.GetEnhMetaFileBits(h, n, buf) != n:
                return None
            return buf.raw
        finally:
            user.CloseClipboard()
    except OSError, AttributeError, EmfError:  # swallow-ok: no EMF on the clipboard
        return None


def clipboard_has_emf() -> bool:
    if os.name != "nt":
        return False
    try:
        user = ctypes.windll.user32
        user.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
        return bool(user.IsClipboardFormatAvailable(_CF_ENHMETAFILE))
    except OSError, AttributeError:  # swallow-ok: not on Windows
        return False
