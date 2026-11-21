"""View rules shared by every window."""

from __future__ import annotations

from PySide6 import QtWidgets

#: the view consistency settings: their rows are in settings_dialog.SettingsDialog.ROWS
CONSISTENCY_KEYS = (
    "view/fit_margin",
    "opt/corner_double_click",
    "opt/bare_zoom_keys",
    "help/zoom_reset_key",
    "opt/card_wheel_step",
)
#: Settings > Keyboard > Other windows > "Text size 100 % in the manual" (help/zoom_reset_key)
HELP_RESET_KEYS = (("ctrl0", "Ctrl+0 (as in a browser)"), ("ctrl1", "Ctrl+1 (as View > Zoom 100%)"))
#: Settings > Pages and libraries > Library panel > "Ctrl + wheel sizes the cards by" (px a
#: notch)
CARD_WHEEL_STEP_DEFAULT = 8
CARD_WHEEL_STEP_RANGE = (2, 40)


def help_reset_key() -> str:
    """The key that puts the manual's text size back ('Ctrl+0' or 'Ctrl+1')."""
    from ..prefs.settings import choice

    return "Ctrl+1" if choice("help/zoom_reset_key", HELP_RESET_KEYS, "ctrl0") == "ctrl1" else "Ctrl+0"


def bare_zoom_keys() -> bool:
    """The bare + and - keys zoom in the Bitmap Editor and the magnifier."""
    from ..prefs.settings import option

    return option("bare_zoom_keys")


def card_wheel_step() -> int:
    """The px one notch of Ctrl + the wheel sizes a grid's cards by."""
    from ..prefs import settings as S

    return S.whole("opt/card_wheel_step", CARD_WHEEL_STEP_RANGE)


def _windows_with(method: str) -> list:
    """Every live top-level widget (and the widgets under it) with ``method``."""
    out = []
    app = QtWidgets.QApplication.instance()
    if app is None:
        return out
    for w in app.allWidgets():
        try:
            if callable(getattr(w, method, None)):
                out.append(w)
        except RuntimeError:  # swallow-ok: a widget being deleted
            continue
    return out


def apply_consistency_settings(window=None) -> None:
    """Read the package's settings into the modules and the open windows (once at a window's
    start, then after each change in Settings)."""
    from . import viewnav
    from ..prefs.settings import app_settings

    st = app_settings()
    before = viewnav.fit_margin()
    viewnav.set_fit_margin(st.value("view/fit_margin", viewnav.FIT_MARGIN_DEFAULT))
    if viewnav.fit_margin() != before:
        _refit_views(window)
    for w in _windows_with("apply_key_settings"):
        try:
            w.apply_key_settings()
        except RuntimeError:  # swallow-ok: a window being closed
            continue


def _refit_views(window=None) -> None:
    """The fitted views follow the new margin: the editor windows' canvases that are in
    auto-fit, and the Simulate window's fitted tabs."""
    from .canvas_options import editor_windows

    for w in editor_windows(window):
        v = getattr(w, "view", None)
        try:
            if v is not None and getattr(v, "_auto_fit", False):
                v._apply_fit()
        except RuntimeError:  # swallow-ok: a window being closed
            continue
    try:
        from ..simulate.window import simulate_window

        sw = simulate_window(create=False)
    except ImportError:  # swallow-ok: no Simulate window
        sw = None
    if sw is None:
        return
    for sp in sw.tab_pages():
        cv = getattr(sp, "canvas", None)
        try:
            if cv is not None and getattr(cv, "_auto_fit", False):
                cv.fit_page(cap=getattr(cv, "_fit_cap", None))
        except RuntimeError:  # swallow-ok: a tab being closed
            continue


def _register_settings_hooks() -> None:
    from ..prefs import settings_pages as SP

    for key in CONSISTENCY_KEYS:
        if apply_consistency_settings not in SP.APPLY_HOOKS.get(key, []):
            SP.register_apply(key, apply_consistency_settings)


_register_settings_hooks()
