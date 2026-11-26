"""The object property sheet, one module per tab."""

from __future__ import annotations

from .access import ACCESS_SUBS
from .appearance import VISIBILITY_SUB as _VISIBILITY_SUB  # noqa: F401 (re-exported)
from .display_value import DISPLAY_FIELDS as _DISPLAY_FIELDS  # noqa: F401 (re-exported)
from .fields import (
    _L_LOG,  # noqa: F401
    _Field,  # noqa: F401 (re-exported)
    _OptionalSpin,  # noqa: F401 (re-exported)
    _Sub,  # noqa: F401 (re-exported)
)
from .fill import FILL_SUBS
from .modules import all_subs as _all_subs  # noqa: F401
from .modules import sheet_keys
from .motion import MOVEMENT_SUBS, SCALING_SUBS, SLIDER_SUBS
from .sheet import ObjectPropertiesDialog

# CtDraw's two-level tab tree as the sheet's properties are grouped (the
# Input tab is built by .input: None)
_SHEET_TABS = [
    ("Movement", MOVEMENT_SUBS),
    ("Scaling", SCALING_SUBS),
    ("Fill", FILL_SUBS),
    ("Input", None),
    ("Slider", SLIDER_SUBS),
    ("Access", ACCESS_SUBS),
]

__all__ = ["ObjectPropertiesDialog", "sheet_keys"]
