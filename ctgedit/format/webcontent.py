"""The Web Content object."""

from __future__ import annotations

import re
import struct
from typing import Optional, Tuple

from .cnum import c_atoi
from .page import PageObject
from .textcodec import CODEC

WEB_TC = 0x13
URL_KEY = "URL.NonGenie"
PRINT_KEY = "ENABLEPRINT"
MAX_URL = 2048  # the Web Content page's URL field takes 2048 characters
MAX_URL_CICODE = 255  # DspWebContentSetURL (help 1172149)
FRAME = 0x6B6F73  # the design-time frame / text colour (COLORREF 0x736f6b)
FACE = 0xF3F4F5  # the design-time face (COLORREF 0xf5f4f3)

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def string_section_offset(po: PageObject) -> Optional[int]:
    """Where the web content's string section starts (after the colour-state
    array, the style block and the metadata bag, in the loader's order);
    None when the record is not a web content object or does not parse."""
    if po.type_code != WEB_TC:
        return None
    from .placement import class_section_offset

    try:
        o = class_section_offset(po)
    except Exception:  # noqa: BLE001
        return None
    b = po.body_raw
    if o is None or o + 2 > len(b):
        return None
    n = struct.unpack_from("<H", b, o)[0]
    if o + 2 + n + 1 + 8 != len(b):
        return None  # not [u16 n][n bytes][u8][8-byte rect]
    return o


def read_string_section(po: PageObject) -> Optional[Tuple[str, int]]:
    """(URL, enable-print byte) as the string section stores them."""
    o = string_section_offset(po)
    if o is None:
        return None
    b = po.body_raw
    n = struct.unpack_from("<H", b, o)[0]
    return b[o + 2 : o + 2 + n].decode(CODEC, "replace"), b[o + 2 + n]


def rebuild_string_section(po: PageObject) -> bool:
    """Re-derive the string section from the record's bags (URL.NonGenie,
    atoi(ENABLEPRINT)) like the class saver does. Returns True
    when it changed; False when the record has no section to rewrite."""
    o = string_section_offset(po)
    if o is None:
        return False
    props = po.raw_properties()
    url = props.get(URL_KEY, "") or ""
    raw = url.encode(CODEC, "replace")[:0xFFFF]
    flag = c_atoi(props.get(PRINT_KEY, "")) & 0xFF
    b = po.body_raw
    n = struct.unpack_from("<H", b, o)[0]
    new = struct.pack("<H", len(raw)) + raw + bytes([flag])
    old = b[o : o + 2 + n + 1]
    if new == old:
        return False
    po.body_raw = b[:o] + new + b[o + 2 + n + 1 :]
    return True


def url_problem(url: str) -> Optional[str]:
    """Why ``url`` cannot be stored (too long), None when it can."""
    if len(url) > MAX_URL:
        return "The URL is %d characters long; a Web Content object takes at most %d. Shorten it." % (
            len(url),
            MAX_URL,
        )
    return None


def is_valid_url(url: str) -> bool:
    """The Graphics Builder's check (urlmon IsValidURL) where
    it is available, else a scheme test ('https://...')."""
    try:
        import ctypes

        fn = ctypes.windll.urlmon.IsValidURL  # type: ignore[attr-defined]
        fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
        fn.restype = ctypes.c_long
        return fn(None, url, 0) == 0
    except Exception:  # noqa: BLE001 -- not Windows / no urlmon
        return bool(_SCHEME.match(url or ""))


def url_advice(url: str) -> list:
    """Non-blocking advice for a URL (the Graphics Builder only warns too:
    IsValidURL, then it stores the URL anyway)."""
    out = []
    u = (url or "").strip()
    if not u:
        return out
    if u != url:
        out.append("The URL starts or ends with spaces; the browser may not open it.")
    if not _SCHEME.match(u) or not is_valid_url(u):
        out.append(
            "This does not look like a complete URL: include the protocol, for "
            "example https://www.example.com/."
        )
    elif not u.lower().startswith("https://"):
        out.append(
            "The page is not loaded over https://: its content and your runtime "
            "can be tampered with on the way."
        )
    if re.search(r"%[A-Za-z_][A-Za-z0-9_]*%", u):
        out.append(
            "Expressions and genie substitutions are not supported in the URL: "
            "enter a static address (use DspWebContentSetURL to change it at "
            "runtime)."
        )
    if len(u) > MAX_URL_CICODE:
        out.append(
            "URLs set at runtime with DspWebContentSetURL are limited to %d characters." % MAX_URL_CICODE
        )
    return out


#: help 1172149's safety message (shown on the Web Content page)
LOSS_OF_CONTROL = (
    "LOSS OF CONTROL: do not use a Web Content object to show an untrusted or "
    "potentially unsafe web site, and do not enter a URL that lets users navigate to "
    "one (a search engine, for example): your runtime system may become vulnerable to "
    "malicious activity. An internet site also exposes your SCADA system and network "
    "externally."
)
