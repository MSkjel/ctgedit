"""Themes, UI text size and drawn icons."""

from __future__ import annotations

import re
import sys
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

#: the authored (dark) scheme. Every role the UI uses. Contrast (WCAG 2.2, checked by
#: tests/test_theme_contrast.py): text roles >= 4.5 on bg / panel / hover / card,
#: 'control_edge' (the edge of a field, button or check box) and 'scroll_handle' >= 3.
DARK = {
    "bg": "#1f2226",
    "panel": "#26292e",
    "border": "#3a3f46",
    "text": "#d6d9de",
    "text_dim": "#969ca5",
    "accent": "#4da3ff",
    "selection": "#2b4a6f",
    "danger": "#f27070",
    "hover": "#2c3036",
    "raised": "#343941",
    "on_selection": "#ffffff",
    "head": "#f0f2f5",
    "label": "#b6bcc4",
    "warn": "#d9a441",
    "selection_hover": "#35598a",
    "deep": "#17191c",
    "popup": "#23262b",
    "card": "#262a30",
    "desk": "#2a2d31",
    "scroll_hover": "#8a929d",
    "banner_bg": "#3a3222",
    "banner_text": "#f0d9a8",
    "banner_edge": "#8a6d2b",
    "code": "#e6c07b",
    "note_bg": "#25303c",
    "tip_bg": "#25352a",
    "tip": "#5fbf77",
    "caution_bg": "#3a2a24",
    "caution": "#e0955c",
    "shadow": "#101214",
    "dark": "#17191c",
    "overlay": "#22262c",
    "control_edge": "#6e7681",
    "sel_text_dim": "#c3d3e6",
    "scroll_handle": "#727a85",
}
#: the same design in light colours, as Fluent layers: the window (bg) under white panels
#: and cards, a darker desk around the page, a light selection with dark text. No value is
#: pure white or black: those are page colours too, and a white in a stylesheet must stay
#: white (so the panels are #fefefe). No value is a dark design colour either
#: (map_colour reads a light value back as its role).
LIGHT = {
    "bg": "#eef0f3",
    "panel": "#fefefe",
    "border": "#d5d8dd",
    "text": "#1e1e1e",
    "text_dim": "#5b5f65",
    "accent": "#0a64c8",
    "selection": "#cfe0f7",
    "danger": "#c42b1c",
    "hover": "#e7eaee",
    "raised": "#dde1e6",
    "on_selection": "#0b1f3a",
    "head": "#050505",
    "label": "#3c4043",
    "warn": "#8a5a00",
    "selection_hover": "#bcd4f3",
    "deep": "#fdfdfd",
    "popup": "#fafafa",
    "card": "#fcfcfd",
    "desk": "#d4d7dc",
    "scroll_hover": "#6b7079",
    "banner_bg": "#fff4ce",
    "banner_text": "#5c4300",
    "banner_edge": "#d9b44a",
    "code": "#8a4b00",
    "note_bg": "#e8f1fb",
    "tip_bg": "#e8f5ea",
    "tip": "#2e7d32",
    "caution_bg": "#fdf0e7",
    "caution": "#9a4c00",
    "shadow": "#8e9297",
    "dark": "#a0a4a9",
    "overlay": "#f9f9f9",
    "control_edge": "#767b83",
    "sel_text_dim": "#3e5270",
    "scroll_handle": "#858a92",
}
SCHEMES = ("dark", "light", "contrast")

#: the ACTIVE scheme's colours (updated in place by :func:`apply_theme`, so a
#: module that reads ``COLORS[...]`` when it paints follows the theme); also
#: the derived roles 'field', 'sel_text', 'disabled' and 'on_accent' (_scheme_colours)
COLORS: Dict[str, str] = dict(
    DARK, field=DARK["bg"], sel_text=DARK["text"], disabled=DARK["text_dim"], on_accent=DARK["deep"]
)

#: design colours of the dark UI that are not a DARK value themselves -> the
#: role they play (notes, card edges, alarm-ish ambers and reds ...)
_DARK_ALIASES = {
    "#1e2126": "bg",
    "#343a42": "border",
    "#e6e9ee": "head",
    "#eeeeee": "text",
    "#8a8f98": "text_dim",
    "#8a93a0": "text_dim",
    "#9aa3ad": "text_dim",
    "#888888": "text_dim",
    "#777777": "text_dim",
    "#6f7680": "text_dim",
    "#e0625c": "danger",
    "#e5534b": "danger",
    "#e05252": "danger",
    "#ff6b6b": "danger",
    "#e06c6c": "danger",
    "#d7a44a": "warn",
    "#e0a84c": "warn",
    "#e0a030": "warn",
    "#c9a13b": "warn",
    "#c8a040": "warn",
    "#e0a040": "warn",
    "#d6a14a": "warn",
    "#2d3238": "hover",
    "#2c3138": "hover",
    "#15171a": "deep",
    "#1b1e22": "deep",
    "#30353c": "raised",
}
#: rgb triples of translucent rgba() design colours -> role
_RGB_ALIASES = {(34, 38, 44): "overlay", (77, 163, 255): "accent", (224, 160, 64): "warn"}
#: roles never mapped back from a colour value (their values are not distinctive)
_UNMAPPED = {"on_selection"}

#: the dark design's colours -> role (what a stylesheet as written uses)
_DESIGN_ROLE: Dict[str, str] = {v.lower(): r for r, v in DARK.items() if r not in _UNMAPPED}
_DESIGN_ROLE.update(_DARK_ALIASES)
#: the light scheme's values -> role (a stylesheet formatted from COLORS while
#: the light theme was on turns dark again)
_LIGHT_ROLE: Dict[str, str] = {v.lower(): r for r, v in LIGHT.items() if r not in _UNMAPPED}

#: a stylesheet starting with this shows data (a user colour): never recoloured
DATA_MARK = "/*data*/"

#: base UI text size (px) at 100 %
BASE_FONT_PX = 12

#: Settings > Appearance (``app_settings`` keys) and their defaults
APPEARANCE_DEFAULTS = {
    "ui/style/theme": "dark",  # dark / light / system (the Windows app mode)
    "ui/high_contrast": True,  # use Windows' high-contrast colours while it is on
    "ui/text_size": 100,  # percent of the 12 px base
    "ui/follow_text_size": True,  # times Windows' 'Make text bigger' factor
    "ui/accent": "blue",  # ACCENTS, 'windows' or a custom '#rrggbb'
    "ui/density": "comfortable",  # DENSITY
    "ui/focus_ring": "keyboard",  # FOCUS_RINGS: after keyboard use, or always
}
TEXT_SIZE_RANGE = (75, 200)

#: Settings > Appearance > Accent colour: the presets, (dark scheme, light scheme). Each is
#: fitted to the contrast rule of the scheme it is shown in (fit_contrast); 'windows' =
#: the Windows accent colour (DWM AccentColor), '#rrggbb' = the user's own colour
ACCENTS = {
    "blue": ("#4da3ff", "#0a64c8"),
    "teal": ("#2ec4b6", "#00756a"),
    "green": ("#4cc38a", "#1a7834"),
    "orange": ("#f0883e", "#b04a00"),
    "magenta": ("#e879d8", "#a8238f"),
}
#: the choices in the order of the Settings combo, with their labels
ACCENT_CHOICES = (
    ("blue", "Blue"),
    ("teal", "Teal"),
    ("green", "Green"),
    ("orange", "Orange"),
    ("magenta", "Magenta"),
    ("windows", "Match Windows"),
    ("custom", "Custom"),
)

#: Settings > Appearance > Show the keyboard focus
FOCUS_RINGS = (("keyboard", "After keyboard use"), ("always", "Always"))

#: the type scale: role -> (px at 100 %, weight). theme.font(role) scales it with the text
#: size; the application stylesheet has fs_<role> tokens; type_qss(role) for a widget's
# own stylesheet
TYPE = {
    "caption": (11, 400),
    "body": (12, 400),
    "body_strong": (12, 600),
    "subtitle": (14, 600),
    "title": (18, 600),
    "display": (24, 600),
}
#: the spacing steps (px at 100 %): sp(0) .. sp(5)
SPACE = (2, 4, 8, 12, 16, 24)

#: Settings > Appearance > Density: the paddings and heights of each (px at 100 %).
#: The application stylesheet takes them as tokens; the ribbon, the navigation bar, the
#: selection HUD and the panel title bars read density(key) in their _theme_changed hooks.
DENSITY = {
    "compact": {
        "row_pad": 1,
        "menu_pad": 3,
        "menubar_pad": 3,
        "tab_pad": (3, 10),
        "button_pad": (2, 10),
        "ribbon_h": 84,
        "big_h": 52,
        "small_h": 18,
        "qat_icon": 14,
        "dock_title_pad": 1,
        "captions": False,
    },
    "comfortable": {
        "row_pad": 2,
        "menu_pad": 4,
        "menubar_pad": 4,
        "tab_pad": (5, 12),
        "button_pad": (4, 12),
        "ribbon_h": 104,
        "big_h": 64,
        "small_h": 20,
        "qat_icon": 16,
        "dock_title_pad": 2,
        "captions": True,
    },
    "touch": {
        "row_pad": 6,
        "menu_pad": 8,
        "menubar_pad": 8,
        "tab_pad": (8, 16),
        "button_pad": (8, 16),
        "ribbon_h": 128,
        "big_h": 76,
        "small_h": 28,
        "qat_icon": 20,
        "dock_title_pad": 6,
        "captions": True,
    },
}
DENSITIES = (("compact", "Compact"), ("comfortable", "Comfortable"), ("touch", "Touch"))

#: themed = apply_theme ran; recolour = a scheme other than dark, or the dark scheme with
#: colours of its own (another accent), was applied in this run (until then the dark
#: design needs no mapping at all); custom = the active colours are not the authored
#: table's (the dark scheme maps its design colours then too)
_state = {
    "scheme": "dark",
    "scale": 1.0,
    "themed": False,
    "recolour": False,
    "signature": None,
    "custom": False,
    "density": "comfortable",
    "focus_ring": "keyboard",
    "accent": "blue",
}


# --------------------------------------------------------------------------- Windows
def windows_high_contrast() -> bool:
    """Whether Windows high contrast is on (SPI_GETHIGHCONTRAST, flag
    HCF_HIGHCONTRASTON; Qt's contrast preference off Windows)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class HIGHCONTRASTW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT),
                    ("dwFlags", wintypes.DWORD),
                    ("lpszDefaultScheme", wintypes.LPWSTR),
                ]

            hc = HIGHCONTRASTW()
            hc.cbSize = ctypes.sizeof(HIGHCONTRASTW)
            if ctypes.windll.user32.SystemParametersInfoW(0x42, hc.cbSize, ctypes.byref(hc), 0):
                return bool(hc.dwFlags & 0x1)
        except Exception:  # noqa: BLE001 -- no high contrast then
            return False
        return False
    try:
        hints = QtGui.QGuiApplication.styleHints().accessibility()
        return hints.contrastPreference() == QtCore.Qt.ContrastPreference.HighContrast
    except Exception:  # noqa: BLE001
        return False


def _hkcu_dword(key: str, name: str) -> Optional[int]:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            v, _t = winreg.QueryValueEx(k, name)
            return int(v)
    except OSError, ValueError, TypeError:
        return None


def windows_text_scale() -> int:
    """Windows' 'Make text bigger' factor in percent (Settings > Accessibility
    > Text size, HKCU\\Software\\Microsoft\\Accessibility TextScaleFactor;
    100 when never changed)."""
    v = _hkcu_dword(r"Software\Microsoft\Accessibility", "TextScaleFactor")
    return v if v is not None and 100 <= v <= 225 else 100


def windows_light_apps() -> bool:
    """Whether Windows' app mode is Light: the AppsUseLightTheme registry
    value (what Settings > Personalisation > Colours writes -- read first, as
    Qt's colour scheme may follow the palette ctgedit itself installed), else
    Qt's colour scheme; light when unknown."""
    v = _hkcu_dword(r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme")
    if v is not None:
        return v != 0
    try:
        cs = QtGui.QGuiApplication.styleHints().colorScheme()
        if cs == QtCore.Qt.ColorScheme.Dark:
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def contrast_colours() -> Dict[str, str]:
    """The high-contrast scheme: every role from the Windows system colours
    (GetSysColor), so ctgedit shows the colours the user chose. Off Windows,
    Qt's palette."""
    sys_col: Dict[str, str] = {}
    if sys.platform == "win32":
        try:
            import ctypes

            get = ctypes.windll.user32.GetSysColor
            for name, idx in (
                ("window", 5),
                ("window_text", 8),
                ("face", 15),
                ("gray", 17),
                ("highlight", 13),
                ("highlight_text", 14),
                ("hot", 26),
                ("workspace", 12),
                ("button_text", 18),
            ):
                c = int(get(idx)) & 0xFFFFFF  # COLORREF 0x00BBGGRR
                sys_col[name] = "#%02x%02x%02x" % (c & 0xFF, (c >> 8) & 0xFF, c >> 16)
        except Exception:  # noqa: BLE001
            sys_col = {}
    if not sys_col:
        pal = QtGui.QGuiApplication.palette()
        R = QtGui.QPalette.ColorRole
        sys_col = {
            "window": pal.color(R.Window).name(),
            "window_text": pal.color(R.WindowText).name(),
            "face": pal.color(R.Button).name(),
            "gray": pal.color(QtGui.QPalette.Disabled, R.Text).name(),
            "highlight": pal.color(R.Highlight).name(),
            "highlight_text": pal.color(R.HighlightedText).name(),
            "hot": pal.color(R.Link).name(),
            "workspace": pal.color(R.Dark).name(),
            "button_text": pal.color(R.ButtonText).name(),
        }
    w, t, f = sys_col["window"], sys_col["window_text"], sys_col["face"]
    hl, hlt, hot = sys_col["highlight"], sys_col["highlight_text"], sys_col["hot"]
    out = {role: t for role in DARK}  # text-like roles: the window text
    out.update(
        {
            "bg": w,
            "panel": f,
            "border": t,
            "text_dim": t,
            "accent": hot,
            "selection": hl,
            "on_selection": hlt,
            "selection_hover": hl,
            "hover": f,
            "raised": f,
            "deep": w,
            "popup": w,
            "card": w,
            "desk": sys_col["workspace"],
            "scroll_hover": hl,
            "banner_bg": w,
            "note_bg": w,
            "tip_bg": w,
            "caution_bg": w,
            "shadow": t,
            "dark": t,
            "overlay": w,
            "disabled": sys_col["gray"],
            "field": w,
            "sel_text": hlt,
            # a control's edge: Windows' button text colour; a selected row's second line: the
            # selected text colour; the accent's own text: the window colour
            "control_edge": sys_col["button_text"],
            "sel_text_dim": hlt,
            "scroll_handle": sys_col["button_text"],
            "on_accent": w,
        }
    )
    return out


def appearance() -> dict:
    """The stored Settings > Appearance choices."""
    from ..prefs.settings import app_settings

    st = app_settings()
    out = {}
    for k, d in APPEARANCE_DEFAULTS.items():
        try:
            out[k] = st.value(k, d, type=type(d))
        except Exception:  # noqa: BLE001 -- a garbled value reads as the default
            out[k] = d
    lo, hi = TEXT_SIZE_RANGE
    out["ui/text_size"] = max(lo, min(hi, int(out["ui/text_size"] or 100)))
    if out["ui/style/theme"] not in ("dark", "light", "system"):
        out["ui/style/theme"] = "dark"
    acc = str(out["ui/accent"] or "blue").strip().lower()
    if acc not in ACCENTS and acc != "windows" and not _HEX_COLOUR.fullmatch(acc):
        acc = "blue"
    out["ui/accent"] = acc
    if out["ui/density"] not in DENSITY:
        out["ui/density"] = "comfortable"
    if out["ui/focus_ring"] not in dict(FOCUS_RINGS):
        out["ui/focus_ring"] = "keyboard"
    return out


_HEX_COLOUR = re.compile(r"#[0-9a-f]{6}")


# --------------------------------------------------------------------------- colour maths
def _rel_lum(c: QtGui.QColor) -> float:
    def ch(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(c.redF()) + 0.7152 * ch(c.greenF()) + 0.0722 * ch(c.blueF())


def contrast(a, b) -> float:
    """The WCAG 2.2 contrast ratio of two colours (QColor or '#rrggbb'): 1 to 21."""
    la, lb = _rel_lum(QtGui.QColor(a)), _rel_lum(QtGui.QColor(b))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def mix(a, b, t: float) -> str:
    """``a`` mixed ``t`` (0 to 1) into ``b``: t = 1 gives ``a``, 0 gives ``b`` ('#rrggbb')."""
    ca, cb = QtGui.QColor(a), QtGui.QColor(b)
    t = max(0.0, min(1.0, float(t)))
    return "#%02x%02x%02x" % tuple(
        int(round(x * t + y * (1.0 - t)))
        for x, y in ((ca.red(), cb.red()), (ca.green(), cb.green()), (ca.blue(), cb.blue()))
    )


def fit_contrast(colour, surfaces, ratio: float = 4.5, lighter: bool = True) -> str:
    """``colour`` moved toward white (``lighter``) or black in small steps until its
    contrast on every one of ``surfaces`` is at least ``ratio`` ('#rrggbb'); unchanged
    when it passes already."""
    c = QtGui.QColor(colour).name()
    target = "#ffffff" if lighter else "#000000"
    for step in range(0, 41):
        cand = mix(target, c, step / 40.0)
        if all(contrast(cand, s) >= ratio for s in surfaces):
            return cand
    return target


def readable_on(bg, candidates, ratio: float = 4.5) -> str:
    """The first of ``candidates`` with at least ``ratio`` contrast on ``bg``, else the one
    with the most."""
    best, best_r = None, -1.0
    for cand in candidates:
        r = contrast(cand, bg)
        if r >= ratio:
            return QtGui.QColor(cand).name()
        if r > best_r:
            best, best_r = cand, r
    return QtGui.QColor(best).name()


def windows_accent() -> Optional[str]:
    """The Windows accent colour ('#rrggbb'): HKCU\\Software\\Microsoft\\Windows\\DWM
    AccentColor (0xAABBGGRR), else ColorizationColor (0xAARRGGBB); None when unknown."""
    v = _hkcu_dword(r"Software\Microsoft\Windows\DWM", "AccentColor")
    if v is not None:
        return "#%02x%02x%02x" % (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
    v = _hkcu_dword(r"Software\Microsoft\Windows\DWM", "ColorizationColor")
    if v is not None:
        return "#%02x%02x%02x" % ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    role = getattr(QtGui.QPalette.ColorRole, "Accent", None)
    if role is None or sys.platform == "win32":
        return None
    try:
        return QtGui.QGuiApplication.palette().color(role).name()
    except Exception:  # noqa: BLE001 -- swallow-ok: no accent known
        return None


def accent_for(choice: str, scheme: str) -> str:
    """The accent colour ``choice`` gives in ``scheme`` ('dark' / 'light'), fitted to 4.5:1
    on the scheme's surfaces (a preset already passes; a Windows or custom colour is
    lightened or darkened until it does)."""
    table = LIGHT if scheme == "light" else DARK
    if choice in ACCENTS:
        raw = ACCENTS[choice][1 if scheme == "light" else 0]
    elif choice == "windows":
        raw = windows_accent() or ACCENTS["blue"][1 if scheme == "light" else 0]
    elif _HEX_COLOUR.fullmatch(str(choice or "")):
        raw = choice
    else:
        raw = ACCENTS["blue"][1 if scheme == "light" else 0]
    surfaces = [table[k] for k in ("bg", "panel", "hover", "card")]
    return fit_contrast(raw, surfaces, 4.5, lighter=scheme != "light")


def resolve(settings: Optional[dict] = None) -> tuple:
    """(scheme, scale) the stored choices give on this computer now."""
    a = dict(APPEARANCE_DEFAULTS)
    a.update(settings if settings is not None else appearance())
    if a["ui/high_contrast"] and windows_high_contrast():
        scheme = "contrast"
    elif a["ui/style/theme"] == "system":
        scheme = "light" if windows_light_apps() else "dark"
    else:
        scheme = a["ui/style/theme"] if a["ui/style/theme"] in ("dark", "light") else "dark"
    scale = int(a["ui/text_size"]) / 100.0
    if a["ui/follow_text_size"]:
        scale *= windows_text_scale() / 100.0
    return scheme, max(0.5, min(3.0, scale))


def current_scheme() -> str:
    return _state["scheme"]


def ui_scale() -> float:
    """The UI text-size factor applied (1.0 until :func:`apply_theme`)."""
    return float(_state["scale"])


def px(n: float) -> int:
    """``n`` logical pixels of the 100 % design at the current text size."""
    return int(round(n * ui_scale()))


def color(role: str) -> QtGui.QColor:
    """The active scheme's colour of ``role``."""
    return QtGui.QColor(COLORS.get(role) or DARK.get(role, DARK["text"]))


#: colours that are the same in every scheme, (r, g, b, alpha): marks drawn over page art
#: and thumbnails (badges, chips, the library well, the favourite star), which the window's
#: theme does not touch. Kept here so painters hold no colour literals
#: (tests/test_theme_literals.py).
FIXED = {
    "badge_text": (255, 255, 255, 255),
    "badge_grey": (110, 116, 126, 235),  # 'Hidden'
    "badge_amber": (190, 120, 30, 235),  # 'Not registered'
    "badge_blue": (77, 163, 255, 215),  # '!' (a system page)
    "chip_dark": (20, 22, 26, 210),  # the resolution chip on a thumbnail
    "element_well": (255, 255, 255, 255),  # CtDraw's element window white
    "well": (225, 228, 232, 255),  # the library card's light well
    "well_edge": (185, 190, 198, 255),
    "well_text": (91, 95, 101, 255),  # 'No preview' on the light well (5:1)
    "star_gold": (245, 197, 66, 255),  # a favourite
    "star_edge": (90, 64, 0, 200),
    "flash_a": (245, 197, 66, 255),  # a flashing symbol's two pictures
    "flash_b": (224, 98, 92, 255),
    "mark_edge": (40, 40, 40, 200),
    "edit_box": (255, 255, 255, 235),  # the in-place text editor over the page
    "edit_text": (17, 17, 17, 255),
    "guide": (95, 200, 240, 255),  # a guideline (its mark on a ruler)
    # View > Mark Changes Since Save over the page (and their dots in the Changes panel)
    "change_new": (60, 190, 90, 255),  # new since the save: a green wash
    "change_edit": (232, 160, 40, 255),  # changed: an amber outline
    "change_gone": (228, 80, 70, 255),  # removed: a dashed red ghost
    # the canvas's marks over the page art (ui/editor/canvas.py)
    "handle_fill": (255, 255, 255, 255),  # a sizing handle, a node, the rotation grip
    "marquee_touch": (80, 200, 120, 255),  # a Touch selection box (Enclose: the selection)
    "pending_wash": (255, 176, 32, 46),  # a genie a background update composes again
    "changed_edge": (60, 190, 90, 200),  # View > Mark Changes Since Save
    "changed_wash": (60, 190, 90, 36),
    "halo_dark": (0, 0, 0, 150),  # the cross hairs' halo on a light accent
    "halo_light": (255, 255, 255, 170),  # ... on a dark accent
    # the underlay's Blue tint (ui/editor/underlay.py): its dark end and its light end
    "underlay_blue_dark": (16, 52, 120, 255),
    "underlay_blue_light": (150, 196, 255, 255),
}


def fixed(name: str) -> QtGui.QColor:
    """A colour of :data:`FIXED` (the same in every scheme)."""
    r, g, b, a = FIXED[name]
    return QtGui.QColor(r, g, b, a)


# --------------------------------------------------------------------------- type, space
def type_px(role: str = "body") -> int:
    """The pixel size of a type-scale role at the current text size."""
    size, _w = TYPE.get(role, TYPE["body"])
    return max(6, int(round(size * ui_scale())))


def font(role: str = "body") -> QtGui.QFont:
    """A QFont of the type-scale role (:data:`TYPE`) at the current text size."""
    size, weight = TYPE.get(role, TYPE["body"])
    f = QtGui.QFont("Segoe UI")
    f.setPixelSize(type_px(role))
    f.setWeight(QtGui.QFont.Weight(weight))
    return f


def type_qss(role: str = "body") -> str:
    """'font-size: Npx; font-weight: W;' of a type-scale role, as written for 100 % (a
    widget's stylesheet goes through :func:`qss`, which scales it)."""
    size, weight = TYPE.get(role, TYPE["body"])
    return "font-size: %dpx; font-weight: %d;" % (size, weight)


def sp(n: int) -> int:
    """Spacing step ``n`` (0 to 5: :data:`SPACE`) at the current text size."""
    n = max(0, min(len(SPACE) - 1, int(n)))
    return px(SPACE[n])


def current_density() -> str:
    """'compact' / 'comfortable' / 'touch' (Settings > Appearance > Density)."""
    return _state.get("density") or "comfortable"


def density(key: str, name: Optional[str] = None):
    """A value of the density table (:data:`DENSITY`) for the active density (or
    ``name``): row_pad, menu_pad, menubar_pad, tab_pad, button_pad, ribbon_h, big_h,
    small_h, qat_icon, dock_title_pad, captions."""
    table = DENSITY.get(name or current_density()) or DENSITY["comfortable"]
    return table[key]


def focus_ring() -> str:
    """'keyboard' (the ring shows after keyboard use) or 'always'."""
    return _state.get("focus_ring") or "keyboard"


# --------------------------------------------------------------------------- canvas colours
#: canvas overlay roles and the alpha each draws with (theme.canvas_colour)
CANVAS_ALPHA = {"selection": 255, "hover": 170, "marquee": 40, "handle_edge": 255}
_CANVAS = {"selection": None}


def canvas_selection_setting() -> str:
    """Settings > Canvas > Selection colour: 'accent' or '#rrggbb' (view/selection_colour;
    a packed 0x00RRGGBB number is read as that colour)."""
    from ..prefs.settings import app_settings

    try:
        v = app_settings().value("view/selection_colour", "accent")
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return "accent"
    s = str(v if v is not None else "accent").strip().lower()
    if _HEX_COLOUR.fullmatch(s):
        return s
    try:
        n = int(s, 0)
    except ValueError:
        return "accent"
    if 0 <= n <= 0xFFFFFF:
        return "#%06x" % n
    return "accent"


def reload_canvas_colours() -> None:
    """Read Settings > Canvas > Selection colour again (after the dialog's OK)."""
    _CANVAS["selection"] = canvas_selection_setting()


def canvas_colour(role: str = "selection", alpha: Optional[int] = None) -> QtGui.QColor:
    """The colour the canvas draws an overlay of ``role`` in: 'selection' (outlines),
    'hover' (the hover outline), 'marquee' (the rubber band's fill), 'handle_edge' (the
    edge of the resize handles). The user's selection colour (Settings > Canvas), else
    the accent; ``alpha`` replaces the role's own (:data:`CANVAS_ALPHA`)."""
    if _CANVAS["selection"] is None:
        reload_canvas_colours()
    sel = _CANVAS["selection"]
    c = QtGui.QColor(COLORS["accent"] if sel == "accent" else sel)
    c.setAlpha(int(CANVAS_ALPHA.get(role, 255) if alpha is None else alpha))
    return c


# --------------------------------------------------------------------------- stylesheets
_HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(,\s*[\d.]+\s*)?\)")
_FONT_SIZE = re.compile(r"(font-size\s*:\s*)([\d.]+)(px|pt)")
_FONT = re.compile(r"(font\s*:[^;}]*?)([\d.]+)(px|pt)")


def _norm_hex(h: str) -> str:
    h = h.lower()
    return "#" + "".join(c * 2 for c in h[1:]) if len(h) == 4 else h


def map_colour(value: str) -> str:
    """A design colour (hex) -> the active scheme's colour of its role;
    anything else unchanged. In the dark scheme the design colours stay as
    written (only a light scheme's value turns back)."""
    h = _norm_hex(value)
    role = _LIGHT_ROLE.get(h)
    if role is None and (_state["scheme"] != "dark" or _state["custom"]):
        role = _DESIGN_ROLE.get(h)
    return COLORS.get(role, value) if role else value


def recolour(text: str) -> str:
    """Every known design colour in a stylesheet / CSS text -> the active
    scheme's colour of the same role (translucent rgba() colours keep their
    alpha)."""
    text = _HEX.sub(lambda m: map_colour(m.group(0)), text)
    if _state["scheme"] == "dark" and not _state["custom"]:
        return text

    def rgba(m):
        role = _RGB_ALIASES.get((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        if role is None:
            return m.group(0)
        c = QtGui.QColor(COLORS.get(role, DARK[role]))
        if m.group(4):
            return "rgba(%d, %d, %d%s)" % (c.red(), c.green(), c.blue(), m.group(4))
        return "rgb(%d, %d, %d)" % (c.red(), c.green(), c.blue())

    return _RGBA.sub(rgba, text)


def scale_fonts(text: str, scale: Optional[float] = None) -> str:
    """Pixel and point font sizes of a stylesheet times the UI text size."""
    s = ui_scale() if scale is None else scale
    if abs(s - 1.0) < 1e-6:
        return text

    def num(v: str, unit: str) -> str:
        x = float(v) * s
        return ("%d" % max(1, round(x))) if unit == "px" else ("%.1f" % x).rstrip("0").rstrip(".")

    text = _FONT_SIZE.sub(lambda m: m.group(1) + num(m.group(2), m.group(3)) + m.group(3), text)
    return _FONT.sub(lambda m: m.group(1) + num(m.group(2), m.group(3)) + m.group(3), text)


def qss(sheet: str) -> str:
    """A stylesheet as written for the dark 100 % design -> the active scheme
    and text size. The identity until another scheme or size was applied."""
    if not sheet or not _state["themed"]:
        return sheet
    out = sheet
    if _state["recolour"] and not sheet.lstrip().startswith(DATA_MARK):
        out = recolour(out)
    return scale_fonts(out)


_QSS_PROP = "ctgQss"
_orig_set_style_sheet = None


def _themed_set_style_sheet(self, sheet) -> None:
    sheet = "" if sheet is None else str(sheet)
    try:
        self.setProperty(_QSS_PROP, sheet)
    except RuntimeError:
        pass
    _orig_set_style_sheet(self, qss(sheet))


def _install_stylesheet_hook() -> None:
    global _orig_set_style_sheet
    if _orig_set_style_sheet is None:
        _orig_set_style_sheet = QtWidgets.QWidget.setStyleSheet
        QtWidgets.QWidget.setStyleSheet = _themed_set_style_sheet


def _uninstall_stylesheet_hook() -> None:
    """Tests: put Qt's own setStyleSheet back."""
    global _orig_set_style_sheet
    if _orig_set_style_sheet is not None:
        QtWidgets.QWidget.setStyleSheet = _orig_set_style_sheet
        _orig_set_style_sheet = None


def _retheme_widgets(app) -> None:
    """Re-apply every widget's own stylesheet (as it was written) in the
    active scheme; a widget styled before the hook existed is adopted."""
    for w in app.allWidgets():
        try:
            tmpl = w.property(_QSS_PROP)
            if tmpl is None:
                tmpl = w.styleSheet()
                if tmpl:
                    w.setProperty(_QSS_PROP, tmpl)
            if tmpl:
                new = qss(tmpl)
                if new != w.styleSheet():
                    _orig_set_style_sheet(w, new)
            hook = getattr(w, "_theme_changed", None)
            if callable(hook):
                hook()
        except RuntimeError:  # deleted while we walked
            continue


_TRACKED: List[tuple] = []


def tracked(value: str) -> QtGui.QColor:
    """A QColor constant in the design colour ``value`` that follows the
    theme: :func:`apply_theme` updates it in place."""
    c = QtGui.QColor(map_colour(value))
    _TRACKED.append((c, value))
    return c


def _update_tracked() -> None:
    for c, value in _TRACKED:
        c.setRgba(QtGui.QColor(map_colour(value)).rgba())


# --------------------------------------------------------------------------- theme
_QSS = """
QWidget { font-family: 'Segoe UI'; font-size: %(font_px)dpx; color: %(text)s; }
QMainWindow { background: %(bg)s; }
QMainWindow::separator { background: %(border)s; width: 3px; height: 3px; }
QDialog { background: %(bg)s; }

QMenuBar { background: %(panel)s; border-bottom: 1px solid %(border)s; padding: 1px 4px; }
QMenuBar::item { background: transparent; padding: %(menubar_pad)dpx 9px; border-radius: 3px; }
QMenuBar::item:selected { background: %(selection)s; color: %(sel_text)s; }
QMenu { background: %(panel)s; border: 1px solid %(border)s; padding: 4px 0px; }
QMenu::item { padding: %(menu_pad)dpx 24px; }
QMenu::item:selected { background: %(selection)s; color: %(sel_text)s; }
QMenu::item:disabled { color: %(text_dim)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 4px 8px; }

QToolBar { background: %(bg)s; border: none; padding: 2px; spacing: 2px; }
QToolBar::separator { background: %(border)s; width: 1px; margin: 3px 3px; }
QToolButton { background: transparent; border: 1px solid transparent;
              border-radius: 3px; padding: 2px; }
QToolButton:hover { background: %(hover)s; border-color: %(border)s; }
QToolButton[ctgMenu="true"] { padding-right: 14px; }
QToolButton:pressed { background: %(selection)s; }
QToolButton:checked { background: %(checked)s; border-color: %(accent)s; }
QToolButton:disabled { color: %(text_dim)s; }
QToolButton[kbdFocus="true"] { border: 2px solid %(accent)s; padding: 1px; }

QDockWidget { color: %(text)s; }
QDockWidget::title { background: %(panel)s; padding: %(dock_pad)dpx 8px;
                     border: 1px solid %(border)s; font-size: %(fs_body_strong)dpx;
                     font-weight: %(fw_body_strong)d; }
QWidget#DockTitleBar { background: %(panel)s; border: 1px solid %(border)s; }
QWidget#DockTitleBar QLabel { font-size: %(fs_body_strong)dpx;
                              font-weight: %(fw_body_strong)d; }
QWidget#DockTitleBar QToolButton { padding: 1px; }

QListWidget, QTreeWidget, QListView, QTreeView, QTableView {
    background: %(panel)s; border: 1px solid %(border)s;
    alternate-background-color: %(hover)s; }
QListWidget[kbdFocus="true"], QTreeWidget[kbdFocus="true"], QListView[kbdFocus="true"],
QTreeView[kbdFocus="true"], QTableView[kbdFocus="true"], QTableWidget[kbdFocus="true"] {
    border: 1px solid %(accent)s; }
QListView::item, QTreeView::item { padding: %(row_pad)dpx 4px; }
QListView::item:hover, QTreeView::item:hover { background: %(hover)s; }
QListView::item:selected, QTreeView::item:selected {
    background: %(selection)s; color: %(sel_text)s; }
QHeaderView::section { background: %(panel)s; color: %(text_dim)s; border: none;
    border-right: 1px solid %(border)s; border-bottom: 1px solid %(border)s;
    padding: 3px 6px; }

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: %(field)s; border: 1px solid %(control_edge)s; border-radius: 3px;
    padding: 2px 6px; selection-background-color: %(selection)s;
    selection-color: %(sel_text)s; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus { border-color: %(accent)s; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled,
QPlainTextEdit:disabled, QTextEdit:disabled { color: %(text_dim)s; background: %(panel)s;
    border-color: %(border)s; }
QLineEdit[ctgInvalid="true"], QSpinBox[ctgInvalid="true"], QComboBox[ctgInvalid="true"],
QPlainTextEdit[ctgInvalid="true"] { border: 1px solid %(danger)s; }
QComboBox QAbstractItemView { background: %(panel)s; border: 1px solid %(border)s;
    selection-background-color: %(selection)s; selection-color: %(sel_text)s; }

QPushButton { background: %(hover)s; border: 1px solid %(control_edge)s;
              border-radius: 3px; padding: %(btn_pad_v)dpx %(btn_pad_h)dpx; }
QPushButton:hover { background: %(raised)s; }
QPushButton:pressed { background: %(selection)s; color: %(sel_text)s; }
QPushButton:default { border-color: %(accent)s; }
QPushButton:disabled { color: %(text_dim)s; background: %(panel)s; border-color: %(border)s; }
QPushButton[kbdFocus="true"] { border: 2px solid %(accent)s;
    padding: %(btn_kbd_v)dpx %(btn_kbd_h)dpx; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0px; }
QScrollBar::handle:vertical { background: %(scroll_handle)s; border-radius: 3px;
                              min-height: 24px; margin: 1px 2px; }
QScrollBar[hot="true"]::handle:vertical { margin: 1px 0px; border-radius: 5px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0px; }
QScrollBar::handle:horizontal { background: %(scroll_handle)s; border-radius: 3px;
                                min-width: 24px; margin: 2px 1px; }
QScrollBar[hot="true"]::handle:horizontal { margin: 0px 1px; border-radius: 5px; }
QScrollBar::handle:hover { background: %(scroll_hover)s; }
QScrollBar::handle:pressed { background: %(accent)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QTabWidget::pane { border: 1px solid %(border)s; }
QTabBar::tab { background: %(bg)s; color: %(text_dim)s;
               padding: %(tab_pad_v)dpx %(tab_pad_h)dpx;
               border-bottom: 2px solid transparent; }
QTabBar::tab:selected { background: %(panel)s; color: %(text)s;
                        border-bottom: 2px solid %(accent)s; }
QTabBar::tab:hover:!selected { color: %(text)s; }
QTabBar[kbdFocus="true"]::tab:selected { background: %(hover)s; color: %(head)s;
    border: 1px solid %(accent)s; border-bottom: 2px solid %(accent)s;
    padding: %(tab_kbd_v)dpx %(tab_kbd_h)dpx; }

QStatusBar { background: %(panel)s; border-top: 1px solid %(border)s;
             color: %(text_dim)s; }
QStatusBar::item { border: none; }

QToolTip { background: %(hover)s; color: %(text)s; border: 1px solid %(border)s;
           padding: 3px 6px; }

QGroupBox { border: 1px solid %(border)s; border-radius: 4px;
            margin-top: %(group_top)dpx; padding-top: %(group_pad)dpx; font-size: %(fs_body_strong)dpx;
            font-weight: %(fw_body_strong)d; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0px 4px; }
QGroupBox > QWidget { font-size: %(fs_body)dpx; font-weight: %(fw_body)d; }

QSplitter::handle { background: %(border)s; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }
QSplitter::handle:hover { background: %(accent)s; }
"""

#: how much of the accent a derived selection takes (the rest: the window colour), per
#: scheme: about what the authored blue selections have (dark #2b4a6f, light #cfe0f7)
_SELECTION_MIX = {"dark": 0.32, "light": 0.2}


def _scheme_colours(scheme: str, accent: str = "blue") -> Dict[str, str]:
    """Every colour role of ``scheme``, plus the derived ones."""
    if scheme == "contrast":
        return contrast_colours()
    table = LIGHT if scheme == "light" else DARK
    c = dict(table)
    c["field"] = c["bg"] if scheme == "dark" else c["panel"]
    if accent and accent != "blue":
        acc = accent_for(accent, scheme)
        c["accent"] = acc
        k = _SELECTION_MIX["light" if scheme == "light" else "dark"]
        c["selection"] = mix(acc, c["bg"], k)
        c["selection_hover"] = mix(acc, c["bg"], k + (0.08 if scheme == "dark" else 0.1))
        if scheme == "light":
            c["on_selection"] = readable_on(c["selection"], (table["on_selection"], "#000000"))
            c["sel_text_dim"] = readable_on(c["selection"], (table["sel_text_dim"], table["on_selection"]))
        else:
            c["sel_text_dim"] = readable_on(c["selection"], (table["sel_text_dim"], "#ffffff"))
    c["sel_text"] = c["text"] if scheme == "dark" else c["on_selection"]
    if scheme == "dark":
        c["sel_text"] = readable_on(c["selection"], (c["text"], "#ffffff"))
    c["disabled"] = c["text_dim"]
    c["on_accent"] = readable_on(c["accent"], ("#ffffff", DARK["deep"]), 3.0)
    return c


def _palette(c: Dict[str, str], scheme: str) -> QtGui.QPalette:
    q = {k: QtGui.QColor(v) for k, v in c.items()}
    P = QtGui.QPalette
    pal = QtGui.QPalette()
    for role, key in (
        (P.Window, "bg"),
        (P.WindowText, "text"),
        (P.Base, "field"),
        (P.AlternateBase, "panel"),
        (P.Text, "text"),
        (P.Button, "panel"),
        (P.ButtonText, "text"),
        (P.ToolTipBase, "hover"),
        (P.ToolTipText, "text"),
        (P.Highlight, "selection"),
        (P.HighlightedText, "sel_text"),
        (P.Link, "accent"),
        (P.BrightText, "danger"),
        (P.PlaceholderText, "text_dim"),
        (P.Light, "border"),
        (P.Midlight, "border"),
        (P.Mid, "border"),
        (P.Dark, "dark"),
        (P.Shadow, "shadow"),
    ):
        pal.setColor(role, q[key])
    for role in (P.WindowText, P.Text, P.ButtonText, P.HighlightedText):
        pal.setColor(P.Disabled, role, q["disabled"])
    pal.setColor(P.Disabled, P.Highlight, q["hover"])
    return pal


#: the dynamic property the focus ring sets on the widget that has the keyboard focus after
#: keyboard use (ui/style/accessibility.FocusRing; the stylesheet's [kbdFocus="true"] rules)
KBD_FOCUS_PROP = "kbdFocus"


def has_keyboard_focus(widget) -> bool:
    """Whether ``widget`` shows the keyboard focus ring now."""
    if widget is None:
        return False
    try:
        return bool(widget.property(KBD_FOCUS_PROP)) and widget.hasFocus()
    except RuntimeError:
        return False


from shiboken6 import isValid as _is_valid  # noqa: E402  (asked thousands of times a paint)


def _alive_or_none(widget):
    """``widget``, or None when its Qt object is gone (a widget being destroyed asks the
    style for a metric while its parent is already deleted: handing the dead wrapper on
    would raise inside Qt's call)."""
    if widget is None:
        return None
    try:
        return widget if _is_valid(widget) else None
    except Exception:  # noqa: BLE001  # swallow-ok: a probe; the widget as given
        return widget


class AppStyle(QtWidgets.QProxyStyle):
    """Fusion with a few changes."""

    # (looked up once: Qt asks for a style hint thousands of times a second while it lays
    # out and paints)
    _GROWTH_HINT = QtWidgets.QStyle.SH_FormLayoutFieldGrowthPolicy
    _GROW = int(QtWidgets.QFormLayout.ExpandingFieldsGrow.value)
    _PE_CHECK = QtWidgets.QStyle.PE_IndicatorCheckBox
    _PE_RADIO = QtWidgets.QStyle.PE_IndicatorRadioButton
    _PE_VIEW_CHECK = QtWidgets.QStyle.PE_IndicatorItemViewItemCheck
    _PE_FOCUS = QtWidgets.QStyle.PE_FrameFocusRect
    _INDICATORS = frozenset((_PE_CHECK, _PE_RADIO, _PE_VIEW_CHECK))
    _INDICATOR_METRICS = frozenset(
        (
            QtWidgets.QStyle.PM_IndicatorWidth,
            QtWidgets.QStyle.PM_IndicatorHeight,
            QtWidgets.QStyle.PM_ExclusiveIndicatorWidth,
            QtWidgets.QStyle.PM_ExclusiveIndicatorHeight,
        )
    )
    _CC_SLIDER = QtWidgets.QStyle.CC_Slider
    _S = QtWidgets.QStyle
    #: the indicator's side at 100 %
    INDICATOR_PX = 14

    def styleHint(self, hint, option=None, widget=None, returnData=None):  # noqa: N802
        if hint == self._GROWTH_HINT:
            return self._GROW
        return QtWidgets.QProxyStyle.styleHint(self, hint, option, _alive_or_none(widget), returnData)

    def pixelMetric(self, metric, option=None, widget=None):  # noqa: N802
        if metric in self._INDICATOR_METRICS:
            return px(self.INDICATOR_PX)
        return QtWidgets.QProxyStyle.pixelMetric(self, metric, option, _alive_or_none(widget))

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802
        if element in self._INDICATORS:
            try:
                self._indicator(element, option, painter, widget)
                return
            except Exception:  # noqa: BLE001 -- a paint must never raise: Fusion's own then
                _note_exc("theme.AppStyle.drawPrimitive")
        elif element == self._PE_FOCUS:
            # (the label of a check box / radio button: the ring, keyboard only; the rest
            # as Fusion draws it)
            if isinstance(widget, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                if has_keyboard_focus(widget):
                    self._focus_outline(option, painter)
                return
        QtWidgets.QProxyStyle.drawPrimitive(self, element, option, painter, widget)

    def drawComplexControl(self, control, option, painter, widget=None):  # noqa: N802
        QtWidgets.QProxyStyle.drawComplexControl(self, control, option, painter, widget)
        if control == self._CC_SLIDER and has_keyboard_focus(widget):
            r = self.subControlRect(control, option, QtWidgets.QStyle.SC_SliderHandle, widget)
            painter.save()
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setPen(QtGui.QPen(color("accent"), 2.0))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(QtCore.QRectF(r).adjusted(1, 1, -1, -1), 3, 3)
            painter.restore()

    # -- the indicators --------------------------------------------------------------
    def _focus_outline(self, option, painter) -> None:
        painter.save()
        pen = QtGui.QPen(color("accent"), 1.0, QtCore.Qt.DotLine)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        painter.drawRect(QtCore.QRect(option.rect).adjusted(0, 0, -1, -1))
        painter.restore()

    def _indicator(self, element, option, painter, widget) -> None:
        S = self._S
        st = option.state
        on = bool(st & S.State_On)
        part = bool(st & S.State_NoChange)
        enabled = bool(st & S.State_Enabled)
        hover = enabled and bool(st & S.State_MouseOver)
        radio = element == self._PE_RADIO
        focus = isinstance(widget, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)) and has_keyboard_focus(
            widget
        )
        r = QtCore.QRectF(option.rect)
        side = max(6.0, min(r.width(), r.height(), float(px(self.INDICATOR_PX))))
        box = QtCore.QRectF(r.center().x() - side / 2.0, r.center().y() - side / 2.0, side, side)
        box = QtCore.QRectF(round(box.left()), round(box.top()), round(side), round(side))
        filled = on or part
        if not enabled:
            edge, fill = color("text_dim"), color("panel")
            if filled:
                fill = color("text_dim")
            mark = color("panel")
        elif filled:
            edge = fill = color("accent")
            mark = color("on_accent")
        else:
            edge = color("accent") if (hover or focus) else color("control_edge")
            fill = color("hover") if hover else color("field")
            mark = color("accent")
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        width = 2.0 if focus else 1.0
        inner = box.adjusted(width / 2.0, width / 2.0, -width / 2.0, -width / 2.0)
        painter.setPen(QtGui.QPen(edge, width))
        painter.setBrush(fill)
        if radio:
            painter.drawEllipse(inner)
            if on:
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(mark)
                d = side * 0.36
                painter.drawEllipse(box.center(), d / 2.0, d / 2.0)
        else:
            rad = max(2.0, side * 3.0 / 14.0)
            painter.drawRoundedRect(inner, rad, rad)
            if part:
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(mark)
                h = max(2.0, side / 7.0)
                painter.drawRoundedRect(
                    QtCore.QRectF(box.left() + side * 0.25, box.center().y() - h / 2.0, side * 0.5, h),
                    h / 2.0,
                    h / 2.0,
                )
            elif on:
                pen = QtGui.QPen(mark, max(1.5, side / 7.5))
                pen.setCapStyle(QtCore.Qt.RoundCap)
                pen.setJoinStyle(QtCore.Qt.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.NoBrush)
                x, y = box.left(), box.top()
                tick = QtGui.QPainterPath(QtCore.QPointF(x + side * 0.24, y + side * 0.53))
                tick.lineTo(x + side * 0.43, y + side * 0.71)
                tick.lineTo(x + side * 0.77, y + side * 0.31)
                painter.drawPath(tick)
        painter.restore()


def _note_exc(where: str) -> None:
    try:
        from ...infra.logsetup import note_exception

        note_exception(where)
    except Exception:  # noqa: BLE001 -- swallow-ok: the log itself failed
        pass


def _install_style(app: QtWidgets.QApplication) -> None:
    """The app style (:class:`AppStyle`), once; a later apply_theme keeps it.
    With an application stylesheet Qt wraps the style, so the installed one
    is either ``app.style()`` or its parent."""
    style = _state.get("style")
    try:
        cur = app.style()
        if style is not None and (cur is style or style.parent() is cur):
            return
    except RuntimeError:  # a style replaced since was deleted
        pass
    style = AppStyle("Fusion")
    app.setStyle(style)
    _state["style"] = style


def _tokens(c: Dict[str, str], scheme: str, scale: float) -> dict:
    """The application stylesheet's tokens: the colours, the type scale (fs_<role>,
    fw_<role>), the density's paddings and the checked-button tint."""
    acc = QtGui.QColor(c["accent"])
    tokens = dict(c)
    tokens["font_px"] = max(8, round(BASE_FONT_PX * scale))
    for role, (size, weight) in TYPE.items():
        tokens["fs_" + role] = max(6, int(round(size * scale)))
        tokens["fw_" + role] = weight
    # the checked tool button: the accent at 22 % (dark) or 18 % (light) with its edge
    alpha = 46 if scheme == "light" else 56
    tokens["checked"] = (
        "rgba(%d, %d, %d, %d)" % (acc.red(), acc.green(), acc.blue(), alpha)
        if scheme != "contrast"
        else c["selection"]
    )
    # a group box's title clearance from the title's own size: its top margin and padding
    # grow with the text size
    gfs = tokens["fs_body_strong"]
    tokens["group_top"] = max(10, int(round(gfs * 0.85)))
    tokens["group_pad"] = max(4, int(round(gfs * 0.5)))
    d = DENSITY.get(_state.get("density")) or DENSITY["comfortable"]
    bv, bh = d["button_pad"]
    tv, th = d["tab_pad"]
    tokens.update(
        row_pad=d["row_pad"],
        menu_pad=d["menu_pad"],
        menubar_pad=d["menubar_pad"],
        dock_pad=d["dock_title_pad"] + 3,
        btn_pad_v=bv,
        btn_pad_h=bh,
        btn_kbd_v=max(0, bv - 1),
        btn_kbd_h=max(0, bh - 1),
        tab_pad_v=tv,
        tab_pad_h=th,
        tab_kbd_v=max(0, tv - 1),
        tab_kbd_h=max(0, th - 1),
    )
    return tokens


def apply_theme(
    app: QtWidgets.QApplication, scheme: Optional[str] = None, scale: Optional[float] = None
) -> tuple:
    """Install a colour scheme and text size app-wide."""
    want_scheme, want_scale = resolve()
    scheme = scheme if scheme in SCHEMES else want_scheme
    scale = want_scale if scale is None else max(0.5, min(3.0, float(scale)))
    looks = appearance()
    accent = looks["ui/accent"]
    c = _scheme_colours(scheme, accent)
    COLORS.clear()
    COLORS.update(c)
    authored = LIGHT if scheme == "light" else DARK
    custom = scheme != "contrast" and any(c.get(k) != v for k, v in authored.items())
    _state.update(
        scheme=scheme,
        scale=scale,
        themed=True,
        custom=custom,
        recolour=_state["recolour"] or scheme != "dark" or custom,
        density=looks["ui/density"],
        focus_ring=looks["ui/focus_ring"],
        accent=accent,
    )
    _install_style(app)
    app.setPalette(_palette(c, scheme))
    font = QtGui.QFont("Segoe UI")
    font.setPixelSize(max(8, round(BASE_FONT_PX * scale)))
    app.setFont(font)
    _install_stylesheet_hook()
    app.setStyleSheet(_QSS % _tokens(c, scheme, scale) + arrow_qss(c))
    _update_tracked()
    reload_canvas_colours()
    _GENERATION[0] += 1
    try:
        from ...render import scene as _scene

        _scene.PageScene._MARGIN_COLOUR = QtGui.QColor(c["desk"])
    except Exception:  # noqa: BLE001 -- the canvas keeps its margin colour
        pass
    _retheme_widgets(app)
    _watch_windows(app)
    _state["signature"] = (scheme, scale)
    return scheme, scale


def refresh_from_windows(app: Optional[QtWidgets.QApplication] = None) -> bool:
    """Windows' app mode or high contrast changed: re-apply when the
    resolved scheme differs. True when it did."""
    app = app or QtWidgets.QApplication.instance()
    if app is None or not _state["themed"]:
        return False
    if resolve() == _state["signature"]:
        return False
    apply_theme(app)
    return True


def _watch_windows(app) -> None:
    if getattr(app, "_ctg_theme_watch", False):
        return
    app._ctg_theme_watch = True
    hints = app.styleHints()
    later = lambda *_a: QtCore.QTimer.singleShot(0, refresh_from_windows)  # noqa: E731
    try:
        hints.colorSchemeChanged.connect(later)
    except Exception:  # noqa: BLE001
        pass
    try:
        hints.accessibility().contrastPreferenceChanged.connect(later)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- arrows
#: the arrow images of combo and spin boxes: (logical width, height, up?)
_ARROWS = {"down": (9, 5, False), "spin_up": (7, 4, True), "spin_down": (7, 4, False)}
#: device pixels per logical pixel of the arrow images (crisp at 200 %)
_ARROW_SCALE = 2


def _arrow_file(colour: str, w: int, h: int, up: bool) -> Optional[str]:
    """A triangle PNG in ``colour`` in ctgedit's data folder (made once),
    as a stylesheet path; None when it cannot be written."""
    import os
    from ...infra.paths import data_dir

    folder = data_dir("ui")
    name = "arrow-%s-%dx%d-%s.png" % ("up" if up else "down", w, h, colour.lstrip("#").lower())
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        try:
            os.makedirs(folder, exist_ok=True)
            s = _ARROW_SCALE
            img = QtGui.QImage(w * s, h * s, QtGui.QImage.Format_ARGB32_Premultiplied)
            img.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(img)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(colour))
            W, H = float(w * s), float(h * s)
            pts = [_pt(0, H), _pt(W, H), _pt(W / 2, 0)] if up else [_pt(0, 0), _pt(W, 0), _pt(W / 2, H)]
            p.drawPolygon(QtGui.QPolygonF(pts))
            p.end()
            tmp = "%s.%d.tmp" % (path, os.getpid())
            if not img.save(tmp, "PNG"):
                return None
            os.replace(tmp, path)
        except OSError:
            return None
    return path.replace("\\", "/")


def _eye_file(colour: str, shut: bool, size: int = 16) -> Optional[str]:
    """An eye PNG (open, or struck through when ``shut``) in ``colour`` in ctgedit's data
    folder (made once); a stylesheet path, None when it cannot be written."""
    import os
    from ...infra.paths import data_dir

    folder = data_dir("ui")
    name = "eye-%s-%d-%s.png" % ("shut" if shut else "open", size, colour.lstrip("#").lower())
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        try:
            os.makedirs(folder, exist_ok=True)
            s = _ARROW_SCALE
            n = size * s
            img = QtGui.QImage(n, n, QtGui.QImage.Format_ARGB32_Premultiplied)
            img.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(img)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            col = QtGui.QColor(colour)
            pen = QtGui.QPen(col, 1.4 * s)
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            m = n * 0.12
            eye = QtGui.QPainterPath()
            eye.moveTo(m, n / 2)
            eye.quadTo(n / 2, n * 0.12, n - m, n / 2)
            eye.quadTo(n / 2, n * 0.88, m, n / 2)
            p.drawPath(eye)
            if not shut:
                p.setBrush(col)
                p.drawEllipse(QtCore.QPointF(n / 2, n / 2), n * 0.14, n * 0.14)
            else:
                p.drawLine(QtCore.QPointF(n * 0.2, n * 0.82), QtCore.QPointF(n * 0.8, n * 0.18))
            p.end()
            tmp = "%s.%d.tmp" % (path, os.getpid())
            if not img.save(tmp, "PNG"):
                return None
            os.replace(tmp, path)
        except OSError:
            return None
    return path.replace("\\", "/")


def eye_indicator_qss(selector: str = "QTreeView", c: Optional[Dict[str, str]] = None) -> str:
    """Rules that draw a view's check indicators as an eye: open = shown, struck through =
    hidden (the Objects tree's show / hide toggle; a check mark read as 'selected')."""
    c = c or COLORS
    shown = _eye_file(c.get("text") or DARK["text"], False)
    hidden = _eye_file(c.get("text_dim") or DARK["text_dim"], True)
    if shown is None or hidden is None:
        return ""
    return (
        "\n%s::indicator { width: 16px; height: 16px; }\n"
        '%s::indicator:checked { image: url("%s"); }\n'
        '%s::indicator:unchecked { image: url("%s"); }\n' % (selector, selector, shown, selector, hidden)
    )


def arrow_qss(c: Optional[Dict[str, str]] = None) -> str:
    """The rules that draw the drop-down arrow of combo boxes and the up /
    down arrows of spin boxes in the scheme's text colour (dimmed when
    disabled or at the limit). '' when the images cannot be made: the
    style's own arrows show then."""
    c = c or COLORS
    files = {}
    for key, (w, h, up) in _ARROWS.items():
        for state, role in (("", "text"), ("_dim", "disabled")):
            f = _arrow_file(c.get(role) or DARK["text"], w, h, up)
            if f is None:
                return ""
            files[key + state] = f
    rules = []
    w, h, _up = _ARROWS["down"]
    rules.append(
        'QComboBox::down-arrow { image: url("%s"); width: %dpx; height: %dpx; }' % (files["down"], w, h)
    )
    rules.append('QComboBox::down-arrow:disabled { image: url("%s"); }' % files["down_dim"])
    for key, sub in (("spin_up", "up-arrow"), ("spin_down", "down-arrow")):
        w, h, _up = _ARROWS[key]
        rules.append(
            'QAbstractSpinBox::%s { image: url("%s"); width: %dpx; height: %dpx; }' % (sub, files[key], w, h)
        )
        rules.append(
            "QAbstractSpinBox::%s:disabled, QAbstractSpinBox::%s:off "
            '{ image: url("%s"); }' % (sub, sub, files[key + "_dim"])
        )
    return "\n" + "\n".join(rules) + "\n"


def arrow_image(role: str = "text", w: int = 8, h: int = 4, up: bool = False) -> Optional[str]:
    """A triangle PNG in the active scheme's ``role`` colour for a stylesheet ``image: url()``
    (the ribbon's menu arrows); None when it cannot be written."""
    return _arrow_file(COLORS.get(role) or DARK.get(role, DARK["text"]), w, h, up)


# --------------------------------------------------------------------------- notes, search
#: the colour roles of note labels
NOTE_ROLES = ("text_dim", "warn", "danger")


def set_note_role(label: QtWidgets.QWidget, role: str = "text_dim", extra: str = "") -> None:
    """Colour a label by its role: 'text_dim' (a note), 'warn' (a warning), 'danger' (an
    error), or another theme colour ('label', 'border', 'head'); '' = the plain text colour
    again. ``extra`` = more stylesheet ('font-size: 9px;'). Follows the theme."""
    if not role:
        label.setProperty("noteRole", "")
        label.setStyleSheet(extra)
        return
    role = role if (role in NOTE_ROLES or role in DARK) else "text_dim"
    label.setProperty("noteRole", role)
    # the design colour: the stylesheet hook maps it to the active scheme
    label.setStyleSheet(("color: %s; " % DARK[role]) + extra)


#: the dynamic property of a field whose input cannot be applied (:func:`mark_invalid`)
INVALID_PROP = "ctgInvalid"
_INVALID_RULE = '*[ctgInvalid="true"] { border: 1px solid %s; }'


def mark_invalid(widget: QtWidgets.QWidget, message: str = "") -> None:
    """Frame ``widget`` (a field) in the danger colour with ``message`` as its tool tip; ''
    clears it: the frame goes and the tool tip it had comes back. The frame follows the
    theme (the stylesheet hook maps the design colour, a theme change re-applies it)."""
    try:
        bad = bool(message)
        was = bool(widget.property(INVALID_PROP))
        if not bad and not was:
            return
        if bad and not was:
            widget.setProperty("ctgTipBefore", widget.toolTip())
            base = widget.property(_QSS_PROP)
            widget.setProperty("ctgQssBefore", base if base is not None else widget.styleSheet())
        base = str(widget.property("ctgQssBefore") or "")
        widget.setProperty(INVALID_PROP, bad)
        if bad:
            widget.setStyleSheet((base + "\n" if base else "") + _INVALID_RULE % DARK["danger"])
            # (Qt's own setToolTip: a field's override adds its usage line to the tip)
            QtWidgets.QWidget.setToolTip(widget, str(message))
            widget.setAccessibleDescription(str(message))
        else:
            widget.setStyleSheet(base)
            QtWidgets.QWidget.setToolTip(widget, str(widget.property("ctgTipBefore") or ""))
            widget.setAccessibleDescription("")
    except RuntimeError:  # (the field went with its form)
        return


def is_invalid(widget: QtWidgets.QWidget) -> bool:
    """:func:`mark_invalid` framed ``widget`` (tests)."""
    try:
        return bool(widget.property(INVALID_PROP))
    except RuntimeError:
        return False


def note_label(text: str = "", role: str = "text_dim", parent=None) -> QtWidgets.QLabel:
    """A word-wrapped note in the role's colour (see :func:`set_note_role`)."""
    lab = QtWidgets.QLabel(text, parent)
    lab.setWordWrap(True)
    set_note_role(lab, role)
    return lab


class EmptyState(QtWidgets.QWidget):
    """The one empty-state look of the panels (Objects, Library, Tags, Page Checks): a
    dim icon, a centred note in the dim text colour and an optional button, in the middle
    of the panel. Made by :func:`empty_state`; follows the theme."""

    ICON_PX = 32

    def __init__(self, icon_name: str, text: str, button=None, parent=None):
        super().__init__(parent)
        self.setObjectName("EmptyState")
        self._icon_name = icon_name or ""
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(sp(4), sp(4), sp(4), sp(4))
        lay.setSpacing(sp(2))
        lay.addStretch(1)
        self.icon_label = QtWidgets.QLabel(self)
        self.icon_label.setAlignment(QtCore.Qt.AlignCenter)
        self.icon_label.setVisible(bool(self._icon_name))
        lay.addWidget(self.icon_label)
        self.label = note_label(text, "text_dim", self)
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(self.label)
        self.button = None
        if button is not None:
            if isinstance(button, QtGui.QAction):
                b = QtWidgets.QToolButton(self)
                b.setDefaultAction(button)
                b.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            elif isinstance(button, QtWidgets.QAbstractButton):
                b = button
                b.setParent(self)
            else:
                text_, slot = button
                b = QtWidgets.QPushButton(text_, self)
                b.clicked.connect(slot)
            row = QtWidgets.QHBoxLayout()
            row.addStretch(1)
            row.addWidget(b)
            row.addStretch(1)
            lay.addLayout(row)
            self.button = b
        lay.addStretch(1)
        self._theme_changed()

    def set_text(self, text: str) -> None:
        self.label.setText(text)

    def _theme_changed(self) -> None:
        if self._icon_name:
            n = px(self.ICON_PX)
            self.icon_label.setPixmap(
                icon(self._icon_name).pixmap(QtCore.QSize(n, n), QtGui.QIcon.Mode.Disabled)
            )


def scroll_body(dialog: QtWidgets.QWidget):
    """A dialog's two layouts: ``(body, outer)``. The body (its fields, lists and notes)
    scrolls when the window is shorter than it (a large text size on a small screen: the
    controls never draw over each other); the outer one keeps the button row below it,
    always in view."""
    outer = QtWidgets.QVBoxLayout(dialog)
    area = QtWidgets.QScrollArea(dialog)
    area.setObjectName("dialogBody")
    area.setFrameShape(QtWidgets.QFrame.NoFrame)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    inner = QtWidgets.QWidget(area)
    inner.setObjectName("dialogBodyInner")
    body = QtWidgets.QVBoxLayout(inner)
    body.setContentsMargins(0, 0, 0, 0)
    area.setWidget(inner)
    outer.addWidget(area, 1)
    # (never narrower than the form: only the height scrolls)
    keeper = _BodyWidth(area, inner)
    inner.installEventFilter(keeper)
    area._ctg_width = keeper  # (kept alive with the area)
    return body, outer


class _BodyWidth(QtCore.QObject):
    """Keeps a scroll_body area as wide as its form's minimum, plus room for the scroll bar."""

    def __init__(self, area, inner):
        super().__init__(area)
        self._area, self._inner = area, inner

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 (Qt API)
        if obj is self._inner and ev.type() in (QtCore.QEvent.LayoutRequest, QtCore.QEvent.Show):
            bar = self._area.verticalScrollBar().sizeHint().width()
            self._area.setMinimumWidth(self._inner.minimumSizeHint().width() + bar)
        return False


def empty_state(icon_name: str, text: str, button=None, parent=None) -> EmptyState:
    """An empty panel's note (:class:`EmptyState`): ``icon_name`` = a theme icon ('' for
    none), ``button`` = a QAction (a menu command: the button runs it), a button, or
    (text, slot)."""
    return EmptyState(icon_name, text, button, parent)


# the pause after the last key before a search field fires
SEARCH_DELAY_MS = 120

#: the setting that lists the search boxes whose '.*' toggle is on (by box name)
SEARCH_REGEX_SETTING = "ui/search_regex"

from ...infra.textmatch import TextMatch  # noqa: E402,F401 -- the search boxes' rule


def _regex_icon(on: bool) -> QtGui.QIcon:
    """The '.*' toggle's face (after citect-tracker's filter boxes): dim when off; bold in the
    accent colour on a tinted ground when on."""
    # (by the colours too: a theme change paints it again)
    key = "regex-%s-%s-%s" % ("on" if on else "off", COLORS["accent"], COLORS["text_dim"])
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    ic = QtGui.QIcon()
    for scale in (1, 2, 3):
        pm = QtGui.QPixmap(16 * scale, 16 * scale)
        pm.setDevicePixelRatio(scale)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)
        if on:
            ground = QtGui.QColor(COLORS["accent"])
            ground.setAlpha(70)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(ground)
            p.drawRoundedRect(QtCore.QRectF(0.0, 0.0, 16, 16), 3, 3)
        f = QtGui.QFont(QtWidgets.QApplication.font())
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QtGui.QColor(COLORS["accent"] if on else COLORS["text_dim"]))
        p.drawText(QtCore.QRectF(0, -1, 16, 16), QtCore.Qt.AlignCenter, ".*")
        p.end()
        ic.addPixmap(pm)
    _ICON_CACHE[key] = ic
    return ic


class RegexToggle(QtCore.QObject):
    """The '.*' toggle at the right end of a search box (any QLineEdit): on, the box's text is a
    regular expression (:class:`TextMatch`). Remembered per box (``key``, the box's name) in
    :data:`SEARCH_REGEX_SETTING`. A text that does not compile gives the box a red frame and
    says why in its tool tip. ``toggled(on)`` when it is switched."""

    toggled = QtCore.Signal(bool)

    TIP_OFF = "Use a regular expression (.*). Off: the text is found as it is, without case"
    TIP_ON = "Regular expression on (.*): found anywhere, without case; ^ and $ anchor it"

    def __init__(self, edit: QtWidgets.QLineEdit, key: str = ""):
        super().__init__(edit)
        self._edit = edit
        self._key = key or edit.accessibleName() or edit.placeholderText()
        self._tip = edit.toolTip()
        self.action = QtGui.QAction(edit)
        self.action.setCheckable(True)
        self.action.setChecked(self._key in self._saved())
        self.action.toggled.connect(self._toggled)
        edit.addAction(self.action, QtWidgets.QLineEdit.TrailingPosition)
        edit.textChanged.connect(lambda _t: self._validate())
        self._show()

    @staticmethod
    def _saved() -> List[str]:
        try:
            from ..prefs.settings import app_settings

            v = app_settings().value(SEARCH_REGEX_SETTING, [], type=list)
            return [str(x) for x in v or ()]
        except Exception:  # noqa: BLE001 -- swallow-ok: no settings (a tool): off
            return []

    def _store(self, on: bool) -> None:
        try:
            from ..prefs.settings import app_settings

            keys = [k for k in self._saved() if k != self._key] + ([self._key] if on else [])
            app_settings().setValue(SEARCH_REGEX_SETTING, keys)
        except Exception:  # noqa: BLE001 -- swallow-ok: not remembered then
            pass

    @property
    def on(self) -> bool:
        return self.action.isChecked()

    def set_on(self, on: bool) -> None:
        self.action.setChecked(bool(on))

    def matcher(self, text: Optional[str] = None) -> TextMatch:
        return TextMatch(self._edit.text() if text is None else text, self.on)

    def _toggled(self, on: bool) -> None:
        self._store(on)
        self._show()
        self.toggled.emit(on)

    def _show(self) -> None:
        on = self.on
        self.action.setIcon(_regex_icon(on))
        self.action.setText(".*")
        self.action.setToolTip(self.TIP_ON if on else self.TIP_OFF)
        self._validate()

    def _validate(self) -> None:
        m = self.matcher()
        bad = bool(m.error)
        if bool(self._edit.property("regexInvalid")) != bad:
            self._edit.setProperty("regexInvalid", bad)
            self._edit.setStyleSheet("QLineEdit { border: 1px solid %s; }" % COLORS["danger"] if bad else "")
        tip = self._tip
        if bad:
            tip = ("%s\n" % tip if tip else "") + "Not a regular expression: %s" % m.error
        if self._edit.toolTip() != tip:
            self._edit.setToolTip(tip)
        self._edit.setAccessibleDescription(("Not a regular expression: %s" % m.error) if bad else "")


class SearchField(QtWidgets.QLineEdit):
    """The standard search box."""

    #: the text after typing paused for the delay
    searchChanged = QtCore.Signal(str)

    def __init__(
        self,
        placeholder: str = "",
        parent=None,
        delay_ms: int = SEARCH_DELAY_MS,
        tip: str = "",
        regex_key: str = "",
    ):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        if placeholder:
            self.setAccessibleName(placeholder)
        if tip:
            self.setToolTip(tip)
        self.setClearButtonEnabled(True)
        self.addAction(icon("search"), QtWidgets.QLineEdit.LeadingPosition)
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, int(delay_ms)))
        self._timer.timeout.connect(self._emit)
        self.textChanged.connect(lambda _t: self._timer.start())
        self._list = None
        self._find_in = ()
        self.regex_toggle = RegexToggle(self, regex_key or placeholder)
        self.regex_toggle.toggled.connect(lambda _on: (self._timer.stop(), self._emit()))

    @property
    def regex(self) -> bool:
        """The '.*' toggle is on: the text is a regular expression."""
        return self.regex_toggle.on

    def set_regex(self, on: bool) -> None:
        self.regex_toggle.set_on(on)

    def matcher(self, text: Optional[str] = None) -> TextMatch:
        """How the box's list matches now (the text, or ``text``, in the box's mode)."""
        return self.regex_toggle.matcher(text)

    def _emit(self) -> None:
        self.searchChanged.emit(self.text())

    def flush(self) -> None:
        """Emit ``searchChanged`` now if a change is pending."""
        if self._timer.isActive():
            self._timer.stop()
            self._emit()

    def cancel(self) -> None:
        """Drop a pending ``searchChanged`` (the owner applies the text itself)."""
        self._timer.stop()

    def set_list_view(self, view: QtWidgets.QAbstractItemView) -> None:
        self._list = view
        view.installEventFilter(self)

    def attach(self, view: QtWidgets.QAbstractItemView, host: Optional[QtWidgets.QWidget] = None) -> None:
        """:meth:`set_list_view` plus Ctrl+F: in a dialog ``host`` Ctrl+F
        anywhere focuses the field (:meth:`bind_find_shortcut`); in a panel of
        the editor window (Edit > Find owns the key there) only while the
        list or ``host`` itself has the focus, without a shortcut of its own."""
        self.set_list_view(view)
        if host is None:
            return
        if isinstance(host, QtWidgets.QDialog):
            self.bind_find_shortcut(host)
        else:
            self._find_in = (view, host)
            host.installEventFilter(self)

    def bind_find_shortcut(self, window: QtWidgets.QWidget) -> None:
        """Ctrl+F anywhere in ``window`` focuses the field."""
        sc = QtGui.QShortcut(QtGui.QKeySequence.Find, window)
        sc.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
        sc.activated.connect(self._focus)

    def _focus(self) -> None:
        self.setFocus(QtCore.Qt.ShortcutFocusReason)
        self.selectAll()

    _NAV_KEYS = (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down, QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown)

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:  # noqa: N802 -- Qt naming
        if e.key() == QtCore.Qt.Key_Escape and self.text():
            self.clear()
            self.flush()
            e.accept()
            return
        if self._list is not None and e.key() in self._NAV_KEYS:
            QtWidgets.QApplication.sendEvent(
                self._list, QtGui.QKeyEvent(e.type(), e.key(), e.modifiers(), e.text())
            )
            e.accept()
            return
        super().keyPressEvent(e)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt naming
        t = e.type()
        find_key = (
            t in (QtCore.QEvent.ShortcutOverride, QtCore.QEvent.KeyPress)
            and e.key() == QtCore.Qt.Key_F
            and e.modifiers() == QtCore.Qt.ControlModifier
        )
        if obj in self._find_in and find_key:
            e.accept()  # this field, not Edit > Find
            if t == QtCore.QEvent.KeyPress:
                self._focus()
            return True
        if obj is self._list and t == QtCore.QEvent.KeyPress:
            mods = e.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier)
            txt = e.text()
            if not mods and txt and txt.isprintable() and not txt.isspace():
                self.setFocus(QtCore.Qt.OtherFocusReason)
                self.setText(self.text() + txt)
                return True
            if e.key() == QtCore.Qt.Key_Backspace and self.text():
                self.setFocus(QtCore.Qt.OtherFocusReason)
                self.setText(self.text()[:-1])
                return True
        return False


def search_field(
    placeholder: str, parent=None, delay_ms: int = SEARCH_DELAY_MS, tip: str = ""
) -> SearchField:
    """A :class:`SearchField` ('Search <things>' as the placeholder)."""
    return SearchField(placeholder, parent, delay_ms, tip)


# --------------------------------------------------------------------------- glyph helpers
def _pt(x: float, y: float) -> QtCore.QPointF:
    return QtCore.QPointF(x, y)


def _r(x: float, y: float, w: float, h: float) -> QtCore.QRectF:
    return QtCore.QRectF(x, y, w, h)


def _lines(p: QtGui.QPainter, segs) -> None:
    for x1, y1, x2, y2 in segs:
        p.drawLine(QtCore.QLineF(x1, y1, x2, y2))


def _polyline(p: QtGui.QPainter, pts) -> None:
    p.drawPolyline(QtGui.QPolygonF([_pt(x, y) for x, y in pts]))


def _thin(p: QtGui.QPainter, width: float) -> None:
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(width)
    p.setPen(pen)


def _punch(p: QtGui.QPainter, rect: QtCore.QRectF) -> None:
    """Erase under a 'front' shape so overlapped outlines read as occluded."""
    p.save()
    p.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
    p.fillRect(rect.adjusted(-0.9, -0.9, 0.9, 0.9), QtCore.Qt.black)
    p.restore()


# --------------------------------------------------------------------------- glyphs (20x20)
def _g_select(p, fg):
    pts = [(6.2, 3.0), (6.2, 15.6), (9.3, 12.6), (11.2, 16.6), (13.2, 15.7), (11.3, 11.8), (15.2, 11.8)]
    p.setBrush(fg)
    p.drawPolygon(QtGui.QPolygonF([_pt(x, y) for x, y in pts]))


def _g_line(p, fg):
    p.drawLine(QtCore.QLineF(4.0, 16.0, 16.0, 4.0))


def _g_freehand(p, fg):
    path = QtGui.QPainterPath(QtCore.QPointF(3.5, 15.5))
    path.cubicTo(QtCore.QPointF(7.0, 6.0), QtCore.QPointF(11.0, 17.0), QtCore.QPointF(16.5, 5.0))
    p.drawPath(path)


def _g_rectangle(p, fg):
    p.setBrush(fg)
    p.drawRect(_r(3.8, 5.2, 12.4, 9.6))


def _g_rectangle_outline(p, fg):
    p.drawRect(_r(3.8, 5.2, 12.4, 9.6))


def _g_ellipse(p, fg):
    p.setBrush(fg)
    p.drawEllipse(_r(3.8, 3.8, 12.4, 12.4))


def _g_ellipse_outline(p, fg):
    p.drawEllipse(_r(3.8, 3.8, 12.4, 12.4))


def _g_polygon(p, fg):
    pts = [(10.0, 4.0), (3.5, 8.7), (6.0, 16.3), (14.0, 16.3), (16.5, 8.7)]
    p.drawPolygon(QtGui.QPolygonF([_pt(x, y) for x, y in pts]))


def _g_pipe(p, fg):
    pen = QtGui.QPen(fg, 4.2)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    path = QtGui.QPainterPath(_pt(4.8, 16.2))
    path.lineTo(4.8, 6.6)
    path.lineTo(15.8, 6.6)
    p.drawPath(path)


def _g_text(p, fg):
    _lines(p, [(5.0, 4.8, 15.0, 4.8), (10.0, 4.8, 10.0, 16.0)])


def _g_number(p, fg):
    _thin(p, 1.5)
    _lines(
        p, [(8.3, 3.8, 6.9, 16.2), (13.3, 3.8, 11.9, 16.2), (4.6, 7.8, 15.6, 7.8), (4.2, 12.4, 15.2, 12.4)]
    )


def _g_button(p, fg):
    p.drawRoundedRect(_r(3.2, 5.2, 13.6, 9.6), 2.4, 2.4)
    p.drawLine(QtCore.QLineF(6.8, 10.0, 13.2, 10.0))


def _g_symbol(p, fg):
    for x, y in ((4.0, 4.0), (11.4, 4.0), (4.0, 11.4), (11.4, 11.4)):
        p.drawRect(_r(x, y, 4.6, 4.6))


def _g_symbol_set(p, fg):
    # a stack of state cards: the symbols a set switches between
    p.drawRect(_r(7.0, 3.5, 9.5, 9.5))
    p.drawRect(_r(3.5, 7.0, 9.5, 9.5))


def _g_undo(p, fg):
    rect = _r(4.0, 5.5, 12.0, 12.0)
    path = QtGui.QPainterPath()
    path.arcMoveTo(rect, 0.0)
    path.arcTo(rect, 0.0, 180.0)
    p.drawPath(path)
    # arrowhead at the left end (arc motion is downward there)
    _lines(p, [(4.0, 11.8, 1.9, 9.2), (4.0, 11.8, 6.6, 9.6)])


def _g_redo(p, fg):
    p.translate(20.0, 0.0)
    p.scale(-1.0, 1.0)
    _g_undo(p, fg)


def _g_delete(p, fg):
    _lines(p, [(4.6, 5.6, 15.4, 5.6), (8.2, 3.4, 11.8, 3.4)])
    _polyline(p, [(6.0, 5.6), (6.9, 16.4), (13.1, 16.4), (14.0, 5.6)])
    _thin(p, 1.2)
    _lines(p, [(8.8, 8.4, 8.8, 13.8), (11.2, 8.4, 11.2, 13.8)])


def _g_duplicate(p, fg):
    p.drawRect(_r(4.0, 3.6, 9.0, 9.0))
    _punch(p, _r(7.0, 7.4, 9.0, 9.0))
    p.drawRect(_r(7.0, 7.4, 9.0, 9.0))
    _thin(p, 1.4)
    _lines(p, [(9.8, 11.9, 13.2, 11.9), (11.5, 10.2, 11.5, 13.6)])


def _g_copy(p, fg):
    p.drawRect(_r(4.2, 3.4, 8.6, 10.4))
    _punch(p, _r(7.2, 6.2, 8.6, 10.4))
    p.drawRect(_r(7.2, 6.2, 8.6, 10.4))


def _g_cut(p, fg):
    _lines(p, [(7.4, 12.9, 13.4, 3.8), (12.6, 12.9, 6.6, 3.8)])
    p.drawEllipse(_pt(6.0, 14.7), 1.9, 1.9)
    p.drawEllipse(_pt(14.0, 14.7), 1.9, 1.9)


def _g_paste(p, fg):
    p.drawRoundedRect(_r(4.6, 4.2, 10.8, 13.2), 1.6, 1.6)
    p.fillRect(_r(7.4, 2.8, 5.2, 2.8), fg)
    _thin(p, 1.2)
    _lines(p, [(7.2, 9.6, 12.8, 9.6), (7.2, 12.4, 11.2, 12.4)])


def _magnifier(p):
    p.drawEllipse(_pt(8.8, 8.8), 5.0, 5.0)
    p.drawLine(QtCore.QLineF(12.5, 12.5, 16.6, 16.6))


def _g_zoom_in(p, fg):
    _magnifier(p)
    _lines(p, [(6.6, 8.8, 11.0, 8.8), (8.8, 6.6, 8.8, 11.0)])


def _g_zoom_out(p, fg):
    _magnifier(p)
    _lines(p, [(6.6, 8.8, 11.0, 8.8)])


def _g_search(p, fg):
    _magnifier(p)


def _g_zoom_fit(p, fg):
    for seg in (
        ((4.0, 7.5), (4.0, 4.0), (7.5, 4.0)),
        ((12.5, 4.0), (16.0, 4.0), (16.0, 7.5)),
        ((16.0, 12.5), (16.0, 16.0), (12.5, 16.0)),
        ((7.5, 16.0), (4.0, 16.0), (4.0, 12.5)),
    ):
        _polyline(p, seg)


def _g_grid(p, fg):
    _thin(p, 1.2)
    p.drawRect(_r(3.5, 3.5, 13.0, 13.0))
    _lines(
        p,
        [
            (7.83, 3.5, 7.83, 16.5),
            (12.17, 3.5, 12.17, 16.5),
            (3.5, 7.83, 16.5, 7.83),
            (3.5, 12.17, 16.5, 12.17),
        ],
    )


def _g_snap(p, fg):
    pen = QtGui.QPen(fg, 2.4)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    path = QtGui.QPainterPath(_pt(6.2, 3.6))
    path.lineTo(6.2, 9.4)
    path.arcTo(_r(6.2, 5.6, 7.6, 7.6), 180.0, 180.0)
    path.lineTo(13.8, 3.6)
    p.drawPath(path)
    _thin(p, 1.6)
    _lines(p, [(4.4, 6.2, 8.0, 6.2), (12.0, 6.2, 15.6, 6.2)])


def _g_problems(p, fg):
    """A warning triangle with an exclamation mark."""
    pen = QtGui.QPen(fg, 1.6)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    p.drawPolygon(QtGui.QPolygonF([_pt(10.0, 3.0), _pt(17.5, 16.5), _pt(2.5, 16.5)]))
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(fg)
    p.drawRoundedRect(_r(9.1, 7.0, 1.8, 5.5), 0.8, 0.8)
    p.drawEllipse(_r(9.05, 13.4, 1.9, 1.9))


def _g_lock(p, fg):
    """A padlock: a shackle over a filled body."""
    pen = QtGui.QPen(fg, 1.8)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    path = QtGui.QPainterPath(_pt(6.5, 9.0))
    path.lineTo(6.5, 6.6)
    path.arcTo(_r(6.5, 3.0, 7.0, 7.0), 180.0, -180.0)
    path.lineTo(13.5, 9.0)
    p.drawPath(path)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(fg)
    p.drawRoundedRect(_r(4.5, 9.0, 11.0, 8.0), 1.4, 1.4)


def _g_smart_guides(p, fg):
    """Two boxes whose top edges line up on a dashed alignment line."""
    _thin(p, 1.4)
    p.drawRect(_r(3.5, 7.0, 5.0, 5.0))
    p.drawRect(_r(11.5, 7.0, 5.0, 8.5))
    pen = QtGui.QPen(fg, 1.2)
    pen.setDashPattern([1.6, 1.4])
    p.setPen(pen)
    p.drawLine(_pt(1.5, 4.2), _pt(18.5, 4.2))


# (bar line as (x1, y1, x2, y2), filled boxes as (x, y, w, h))
_ALIGN_SPEC = {
    "align_left": ((3.8, 3.2, 3.8, 16.8), [(5.6, 5.0, 8.2, 3.4), (5.6, 11.6, 5.4, 3.4)]),
    "align_right": ((16.2, 3.2, 16.2, 16.8), [(6.4, 5.0, 8.2, 3.4), (9.2, 11.6, 5.4, 3.4)]),
    "align_top": ((3.2, 3.8, 16.8, 3.8), [(5.0, 5.6, 3.4, 8.2), (11.6, 5.6, 3.4, 5.4)]),
    "align_bottom": ((3.2, 16.2, 16.8, 16.2), [(5.0, 6.2, 3.4, 8.2), (11.6, 9.0, 3.4, 5.4)]),
    "align_hcentre": ((10.0, 3.2, 10.0, 16.8), [(5.9, 5.0, 8.2, 3.4), (7.3, 11.6, 5.4, 3.4)]),
    "align_vcentre": ((3.2, 10.0, 16.8, 10.0), [(5.0, 5.9, 3.4, 8.2), (11.6, 7.3, 3.4, 5.4)]),
}


def _g_align(p, fg, name):
    line, boxes = _ALIGN_SPEC[name]
    for b in boxes:
        p.fillRect(_r(*b), fg)
    p.setPen(QtGui.QPen(QtGui.QColor(COLORS["accent"]), 1.6))
    p.drawLine(QtCore.QLineF(*line))


def _g_distribute_h(p, fg):
    for x in (3.6, 8.6, 13.6):
        p.fillRect(_r(x, 6.0, 2.8, 8.0), fg)


def _g_distribute_v(p, fg):
    for y in (3.6, 8.6, 13.6):
        p.fillRect(_r(6.0, y, 8.0, 2.8), fg)


def _g_same_size(p, fg):
    p.drawRect(_r(3.8, 3.8, 8.6, 8.6))
    _punch(p, _r(7.6, 7.6, 8.6, 8.6))
    p.drawRect(_r(7.6, 7.6, 8.6, 8.6))


def _g_front(p, fg):
    p.drawRect(_r(4.0, 7.5, 8.5, 8.5))
    _punch(p, _r(7.5, 4.0, 8.5, 8.5))
    p.fillRect(_r(7.5, 4.0, 8.5, 8.5), fg)
    p.drawRect(_r(7.5, 4.0, 8.5, 8.5))


def _g_back(p, fg):
    p.fillRect(_r(4.0, 7.5, 8.5, 8.5), fg)
    p.drawRect(_r(4.0, 7.5, 8.5, 8.5))
    _punch(p, _r(7.5, 4.0, 8.5, 8.5))
    p.drawRect(_r(7.5, 4.0, 8.5, 8.5))


def _g_forward(p, fg):
    _g_front(p, fg)
    _polyline(p, [(3.0, 6.3), (5.0, 4.1), (7.0, 6.3)])


def _g_backward(p, fg):
    _g_back(p, fg)
    _polyline(p, [(13.2, 13.7), (15.2, 15.9), (17.2, 13.7)])


def _g_save(p, fg):
    path = QtGui.QPainterPath(_pt(4.5, 4.5))
    for x, y in ((12.8, 4.5), (15.5, 7.2), (15.5, 15.5), (4.5, 15.5)):
        path.lineTo(x, y)
    path.closeSubpath()
    p.drawPath(path)
    _thin(p, 1.2)
    p.drawRect(_r(7.2, 4.5, 5.2, 3.4))
    p.drawRect(_r(6.8, 11.0, 6.4, 4.5))


def _g_open(p, fg):
    path = QtGui.QPainterPath(_pt(3.6, 15.2))
    for x, y in ((3.6, 5.0), (8.2, 5.0), (9.8, 7.2), (16.4, 7.2), (16.4, 15.2)):
        path.lineTo(x, y)
    path.closeSubpath()
    p.drawPath(path)


def _g_new(p, fg):
    path = QtGui.QPainterPath(_pt(5.8, 3.4))
    for x, y in ((11.6, 3.4), (14.6, 6.4), (14.6, 16.6), (5.8, 16.6)):
        path.lineTo(x, y)
    path.closeSubpath()
    p.drawPath(path)
    _thin(p, 1.2)
    _polyline(p, [(11.6, 3.4), (11.6, 6.4), (14.6, 6.4)])


def _g_page(p, fg):
    p.drawRect(_r(5.4, 3.6, 9.2, 12.8))
    _thin(p, 1.2)
    _lines(p, [(7.4, 7.6, 12.6, 7.6), (7.4, 10.2, 12.6, 10.2), (7.4, 12.8, 10.8, 12.8)])


def _g_run(p, fg):
    p.setBrush(fg)
    p.drawPolygon(QtGui.QPolygonF([_pt(6.6, 4.4), _pt(15.8, 10.0), _pt(6.6, 15.6)]))


def _g_compile(p, fg):
    # a page with a tick: build the project (the play triangle is Run's)
    pen = QtGui.QPen(fg, 1.5)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    p.drawRect(_r(4.0, 3.0, 10.0, 14.0))
    p.drawPolyline(QtGui.QPolygonF([_pt(6.5, 10.5), _pt(8.8, 13.0), _pt(16.5, 5.0)]))


def _g_simulate(p, fg):
    # a screen showing a page running
    pen = QtGui.QPen(fg, 1.5)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    p.drawRect(_r(2.5, 3.5, 15.0, 10.5))
    p.drawLine(_pt(7.0, 16.8), _pt(13.0, 16.8))
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(fg)
    p.drawPolygon(QtGui.QPolygonF([_pt(8.2, 6.2), _pt(13.0, 8.75), _pt(8.2, 11.3)]))


def _g_pause(p, fg):
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(fg)
    p.drawRect(_r(5.4, 4.4, 3.2, 11.2))
    p.drawRect(_r(11.4, 4.4, 3.2, 11.2))


def _g_step(p, fg):
    p.setBrush(fg)
    p.drawPolygon(QtGui.QPolygonF([_pt(4.8, 4.4), _pt(12.4, 10.0), _pt(4.8, 15.6)]))
    p.setPen(QtCore.Qt.NoPen)
    p.drawRect(_r(13.2, 4.4, 2.6, 11.2))


def _g_thumbnail(p, fg):
    p.drawRect(_r(3.6, 4.6, 12.8, 10.8))
    p.save()
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(fg)
    p.drawEllipse(_pt(7.0, 7.8), 1.2, 1.2)
    p.restore()
    _thin(p, 1.3)
    _polyline(p, [(5.2, 13.4), (8.6, 9.6), (10.8, 11.8), (12.8, 9.8), (14.9, 13.4)])


def _g_unknown(p, fg):
    font = QtGui.QFont("Segoe UI")
    font.setPixelSize(13)
    font.setBold(True)
    p.setFont(font)
    p.drawText(_r(0.0, 0.0, 20.0, 20.0), QtCore.Qt.AlignCenter, "?")


def _g_group(p, fg):
    # two shapes inside a dashed selection frame
    pen = p.pen()
    dash = QtGui.QPen(pen)
    dash.setStyle(QtCore.Qt.DashLine)
    p.setPen(dash)
    p.drawRect(_r(3.0, 3.0, 14.0, 14.0))
    p.setPen(pen)
    p.drawRect(_r(5.6, 5.6, 5.0, 5.0))
    p.drawEllipse(_r(10.0, 10.0, 4.6, 4.6))


def _g_ungroup(p, fg):
    # shapes split apart, frame broken
    p.drawRect(_r(3.2, 3.2, 5.4, 5.4))
    p.drawEllipse(_r(11.4, 11.4, 5.2, 5.2))
    _thin(p, 1.2)
    _polyline(p, [(9.6, 9.6), (12.4, 7.0)])
    _polyline(p, [(11.2, 9.0), (12.4, 7.0), (10.2, 6.6)])


def _g_trend(p, fg):
    # a trend chart: the frame and two pen traces
    _thin(p, 1.2)
    p.drawRect(_r(3.0, 4.0, 14.0, 12.0))
    _polyline(p, [(4.2, 12.4), (7.0, 8.6), (9.8, 11.0), (12.6, 6.4), (15.8, 9.0)])
    _polyline(p, [(4.2, 14.2), (8.0, 13.0), (12.0, 13.8), (15.8, 12.2)])


def _g_cicode(p, fg):
    # CtDraw's Cicode object badge: f(x)
    font = QtGui.QFont("Segoe UI")
    font.setPixelSize(10)
    font.setItalic(True)
    font.setBold(True)
    p.setFont(font)
    p.drawText(_r(0.0, 0.0, 20.0, 20.0), QtCore.Qt.AlignCenter, "f(x)")


def _g_web(p, fg):
    # a globe
    _thin(p, 1.2)
    p.drawEllipse(_r(3.0, 3.0, 14.0, 14.0))
    p.drawEllipse(_r(7.0, 3.0, 6.0, 14.0))
    _lines(p, [(3.0, 10.0, 17.0, 10.0), (4.4, 6.6, 15.6, 6.6), (4.4, 13.4, 15.6, 13.4)])


def _g_activex(p, fg):
    # a control: a box with a plug
    _thin(p, 1.3)
    p.drawRect(_r(3.0, 6.0, 11.0, 10.0))
    _lines(p, [(14.0, 11.0, 17.0, 11.0), (17.0, 8.6, 17.0, 13.4)])
    _lines(p, [(6.0, 9.4, 11.0, 9.4), (6.0, 12.6, 9.4, 12.6)])


def _g_measure(p, fg):
    # a ruler: a slanted bar with ticks
    _thin(p, 1.3)
    p.drawLine(QtCore.QLineF(3.5, 16.5, 16.5, 3.5))
    _lines(p, [(5.5, 14.5, 7.5, 16.5), (8.5, 11.5, 10.5, 13.5), (11.5, 8.5, 13.5, 10.5)])


def _g_space_equally(p, fg):
    for x in (3.0, 9.0, 15.0):
        p.fillRect(_r(x, 5.0, 2.0, 10.0), fg)
    _thin(p, 1.0)
    _lines(p, [(5.5, 10.0, 8.5, 10.0), (11.5, 10.0, 14.5, 10.0)])


def _g_arrange_grid(p, fg):
    for x in (3.5, 11.0):
        for y in (3.5, 11.0):
            p.fillRect(_r(x, y, 5.5, 5.5), fg)


def _g_settings(p, fg):
    """A gear: eight teeth around a ring (Tools > Settings)."""
    import math

    pts = []
    for k in range(16):
        a = math.pi * 2 * k / 16.0 - math.pi / 16.0
        r = 8.0 if k % 2 == 0 else 6.2
        for da in (-0.13, 0.13):
            pts.append(_pt(10 + r * math.cos(a + da), 10 + r * math.sin(a + da)))
    _thin(p, 1.4)
    p.drawPolygon(QtGui.QPolygonF(pts))
    p.drawEllipse(_pt(10, 10), 2.6, 2.6)


_GLYPHS: dict[str, Callable[[QtGui.QPainter, QtGui.QColor], None]] = {
    "settings": _g_settings,
    "trend": _g_trend,
    "cicode": _g_cicode,
    "web": _g_web,
    "activex": _g_activex,
    "select": _g_select,
    "line": _g_line,
    "freehand": _g_freehand,
    "rectangle": _g_rectangle,
    "rectangle_outline": _g_rectangle_outline,
    "ellipse": _g_ellipse,
    "ellipse_outline": _g_ellipse_outline,
    "polygon": _g_polygon,
    "pipe": _g_pipe,
    "text": _g_text,
    "number": _g_number,
    "button": _g_button,
    "symbol": _g_symbol,
    "symbol_set": _g_symbol_set,
    "undo": _g_undo,
    "redo": _g_redo,
    "delete": _g_delete,
    "duplicate": _g_duplicate,
    "copy": _g_copy,
    "cut": _g_cut,
    "paste": _g_paste,
    "zoom_in": _g_zoom_in,
    "zoom_out": _g_zoom_out,
    "zoom_fit": _g_zoom_fit,
    "grid": _g_grid,
    "snap": _g_snap,
    "smart_guides": _g_smart_guides,
    "lock": _g_lock,
    "problems": _g_problems,
    "distribute_h": _g_distribute_h,
    "distribute_v": _g_distribute_v,
    "same_size": _g_same_size,
    "measure": _g_measure,
    "space_equally": _g_space_equally,
    "arrange_grid": _g_arrange_grid,
    "front": _g_front,
    "back": _g_back,
    "forward": _g_forward,
    "backward": _g_backward,
    "group": _g_group,
    "save": _g_save,
    "open": _g_open,
    "new": _g_new,
    "compile": _g_compile,
    "run": _g_run,
    "simulate": _g_simulate,
    "pause": _g_pause,
    "step": _g_step,
    "thumbnail": _g_thumbnail,
    "search": _g_search,
    "page": _g_page,
    "unknown": _g_unknown,
    "ungroup": _g_ungroup,
}
for _name in _ALIGN_SPEC:
    _GLYPHS[_name] = lambda p, fg, _n=_name: _g_align(p, fg, _n)
del _name
# the command glyphs (an icon for every ribbon and menu command): ui/style/glyphs_cmd.py
from .glyphs_cmd import GLYPHS as _CMD_GLYPHS  # noqa: E402

_GLYPHS.update({_k: _v for _k, _v in _CMD_GLYPHS.items() if _k not in _GLYPHS})


# --------------------------------------------------------------------------- icon factory
_ICON_CACHE: dict[str, QtGui.QIcon] = {}
#: bumped by apply_theme: every glyph engine repaints in the new colours
_GENERATION = [0]


def generation() -> int:
    """How many times the theme was applied: what was styled for an older one is stale."""
    return _GENERATION[0]


#: the engines Qt holds (a clone Qt asks for must outlive the Python call)
_ENGINES: list = []


def _paint_glyph(name: str, w: int, h: int, dpr: float, fg: QtGui.QColor) -> QtGui.QPixmap:
    pw, ph = max(1, round(w * dpr)), max(1, round(h * dpr))
    pm = QtGui.QPixmap(pw, ph)
    pm.fill(QtCore.Qt.transparent)
    side = min(pw, ph)
    painter = QtGui.QPainter(pm)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.translate((pw - side) / 2.0, (ph - side) / 2.0)
    painter.scale(side / 20.0, side / 20.0)
    pen = QtGui.QPen(fg, 1.6)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    draw = _GLYPHS.get(name, _g_unknown)
    try:
        draw(painter, fg)
    except Exception:  # noqa: BLE001 -- an icon must never raise
        pass
    finally:
        painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


#: how much of the text colour a disabled icon keeps (a greyed command must read as
#: unavailable at a glance: the dim text colour alone looks nearly enabled)
DISABLED_ICON_OPACITY = 0.35


def _faded(pm: QtGui.QPixmap, opacity: float) -> QtGui.QPixmap:
    """``pm`` at ``opacity`` as a whole (overlapping strokes do not add up)."""
    out = QtGui.QPixmap(pm.size())
    out.fill(QtCore.Qt.transparent)
    out.setDevicePixelRatio(pm.devicePixelRatio())
    p = QtGui.QPainter(out)
    p.setOpacity(opacity)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


class _GlyphEngine(QtGui.QIconEngine):
    """Paints a glyph at the size and device pixel ratio Qt asks for (crisp
    at 125 / 150 % and in every button size), in the active scheme's text
    colour (dim when disabled); repainted after a theme change."""

    def __init__(self, name: str):
        super().__init__()
        self._name = name
        self._cache: dict = {}

    def _pixmap(self, size: QtCore.QSize, mode, dpr: float) -> QtGui.QPixmap:
        key = (
            size.width(),
            size.height(),
            int(mode == QtGui.QIcon.Mode.Disabled),
            round(dpr, 3),
            _GENERATION[0],
        )
        pm = self._cache.get(key)
        if pm is None:
            if len(self._cache) > 32:
                self._cache.clear()
            # a disabled icon: the text colour at DISABLED_ICON_OPACITY (high contrast: the
            # solid Windows grey text colour)
            disabled = mode == QtGui.QIcon.Mode.Disabled
            hc = _state["scheme"] == "contrast"
            pm = _paint_glyph(
                self._name,
                size.width(),
                size.height(),
                dpr,
                color("disabled") if disabled and hc else color("text"),
            )
            if disabled and not hc:
                pm = _faded(pm, DISABLED_ICON_OPACITY)
            self._cache[key] = pm
        return pm

    def pixmap(self, size, mode, state):
        return self._pixmap(size, mode, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        return self._pixmap(size, mode, float(scale or 1.0))

    def paint(self, painter, rect, mode, state):
        dev = painter.device()
        dpr = dev.devicePixelRatioF() if dev is not None else 1.0
        painter.drawPixmap(rect, self._pixmap(rect.size(), mode, dpr))

    def clone(self):
        e = _GlyphEngine(self._name)
        _ENGINES.append(e)
        return e

    def key(self):
        return "ctgglyph"


def icon(name: str, size: int = 20) -> QtGui.QIcon:
    """Programmatically painted icon (cached per name; ``size`` is kept for
    the callers -- the engine paints whatever size a widget asks for).
    Unknown names fall back to '?'."""
    cached = _ICON_CACHE.get(name)
    if cached is not None:
        return cached
    engine = _GlyphEngine(name)
    _ENGINES.append(engine)
    out = QtGui.QIcon(engine)
    _ICON_CACHE[name] = out
    return out


# --------------------------------------------------------------------------- app icon
APP_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def app_icon_image(size: int) -> QtGui.QImage:
    """The application icon (window, taskbar, the .exe and its shortcuts): a
    graphics page with a tank and a pipe on an accent tile. Painted on a
    QImage (no text), so the build can make the .ico without a GUI."""
    img = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32_Premultiplied)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.scale(size / 32.0, size / 32.0)
    grad = QtGui.QLinearGradient(0, 0, 0, 32)
    grad.setColorAt(0, QtGui.QColor("#4da3ff"))
    grad.setColorAt(1, QtGui.QColor("#1f6fc9"))
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(QtCore.QRectF(1, 1, 30, 30), 6, 6)
    # the page, its corner folded
    page = QtGui.QPolygonF([_pt(8, 5), _pt(20, 5), _pt(25, 10), _pt(25, 27), _pt(8, 27)])
    p.setBrush(QtGui.QColor("#f4f6f9"))
    p.drawPolygon(page)
    p.setBrush(QtGui.QColor("#b9c7d8"))
    p.drawPolygon(QtGui.QPolygonF([_pt(20, 5), _pt(25, 10), _pt(20, 10)]))
    # a tank (half full) and the pipe leaving it
    ink = QtGui.QColor(DARK["bg"])  # the program's icon: the same in every theme
    tank = QtCore.QRectF(10.5, 12, 7.5, 11)
    p.setBrush(QtGui.QColor("#4da3ff"))
    p.drawRect(QtCore.QRectF(tank.left(), 17.5, tank.width(), tank.bottom() - 17.5))
    pen = QtGui.QPen(ink, 1.5)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    p.drawRoundedRect(tank, 1.4, 1.4)
    p.drawPolyline(QtGui.QPolygonF([_pt(18, 20.5), _pt(22.5, 20.5), _pt(22.5, 14)]))
    p.end()
    return img


def app_icon() -> QtGui.QIcon:
    out = QtGui.QIcon()
    for s in APP_ICON_SIZES:
        out.addPixmap(QtGui.QPixmap.fromImage(app_icon_image(s)))
    return out


def write_app_ico(path: str) -> str:
    """Write the icon as a Windows .ico (one PNG-compressed image per size,
    the Vista+ layout) for the executable and the installer."""
    import struct

    blobs = []
    for s in APP_ICON_SIZES:
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.WriteOnly)
        app_icon_image(s).save(buf, "PNG")
        blobs.append((s, bytes(buf.data())))
    head = struct.pack("<HHH", 0, 1, len(blobs))
    offset = len(head) + 16 * len(blobs)
    entries, data = b"", b""
    for s, png in blobs:
        dim = 0 if s >= 256 else s  # 0 means 256 in an ICONDIRENTRY
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    with open(path, "wb") as fh:
        fh.write(head + entries + data)
    return path


# the process caches of this module (infra/caches.py: when each is emptied)
from ...infra import caches as _caches  # noqa: E402

_caches.register("theme.icons", _ICON_CACHE, on=())
# (the selection colour of Settings > Canvas, read once: emptied = read again)
_caches.register("theme.canvas_colour", _CANVAS, clear=lambda: _CANVAS.update(selection=None), on=("tests",))
