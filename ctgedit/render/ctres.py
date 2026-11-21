"""CtDraw's class icons, loaded from CtRes32.DLL."""

from __future__ import annotations

import functools
import glob
import os
import struct
import sys

from PySide6 import QtGui

#: the f(x) badge. KBDSHOW / COLRSHOW stay off: CtDraw shows no keyboard badge for a page's
#: keyboard records.
from ..format.class_icons import ICON_FOR_TYPE  # noqa: E402,F401 (re-exported)


def _res_dll() -> str | None:
    try:
        from ..backend.compiler import scada_bin

        base = scada_bin()
    except Exception:  # noqa: BLE001
        return None
    hits = glob.glob(os.path.join(base, "CtRes32.dll")) or glob.glob(
        os.path.join(base, "**", "CtRes32.dll"), recursive=True
    )
    return hits[0] if hits else None


@functools.lru_cache(maxsize=None)
def _bitmap_resource(name: str) -> bytes | None:
    if sys.platform != "win32":
        return None
    dll = _res_dll()
    if dll is None:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.LoadLibraryExW.restype = wintypes.HMODULE
        k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
        k32.FindResourceW.restype = wintypes.HANDLE
        k32.FindResourceW.argtypes = [wintypes.HMODULE, wintypes.LPCWSTR, ctypes.c_void_p]
        k32.LoadResource.restype = wintypes.HANDLE
        k32.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
        k32.LockResource.restype = ctypes.c_void_p
        k32.LockResource.argtypes = [wintypes.HANDLE]
        k32.SizeofResource.restype = wintypes.DWORD
        k32.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
        # LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE: no code runs
        mod = k32.LoadLibraryExW(dll, None, 0x2 | 0x20)
        if not mod:
            return None
        res = k32.FindResourceW(mod, name, ctypes.c_void_p(2))  # RT_BITMAP
        if not res:
            return None
        size = k32.SizeofResource(mod, res)
        return ctypes.string_at(k32.LockResource(k32.LoadResource(mod, res)), size)
    except Exception:  # noqa: BLE001
        return None


@functools.lru_cache(maxsize=None)
def resource_image(name: str) -> QtGui.QImage | None:
    """The named CtRes32 bitmap as an ARGB image, as stored (no key colour)."""
    dib = _bitmap_resource(name)
    if not dib or len(dib) < 40:
        return None
    hsize, _w, _h, _planes, bpp, comp = struct.unpack_from("<IiiHHI", dib, 0)
    used = struct.unpack_from("<I", dib, 32)[0] if hsize >= 36 else 0
    ncol = used or ((1 << bpp) if bpp <= 8 else 0)
    off = 14 + hsize + ncol * 4 + (12 if comp == 3 and hsize == 40 else 0)
    bmp = b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, off) + dib
    img = QtGui.QImage.fromData(bmp, "BMP")
    if img.isNull():
        return None
    return img.convertToFormat(QtGui.QImage.Format_ARGB32)


@functools.lru_cache(maxsize=None)
def class_icon(name: str) -> QtGui.QImage | None:
    """The named CtRes32 bitmap as an ARGB image, pure blue keyed out."""
    base = resource_image(name)
    if base is None:
        return None
    img = QtGui.QImage(base)
    for y in range(img.height()):
        for x in range(img.width()):
            if (img.pixel(x, y) & 0xFFFFFF) == 0x0000FF:
                img.setPixel(x, y, 0)
    return img


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("ctres.bitmaps", _bitmap_resource, on=())
_caches.register("ctres.images", resource_image, on=())
_caches.register("ctres.class_icons", class_icon, on=())
