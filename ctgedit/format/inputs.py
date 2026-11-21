"""Touch and keyboard commands stored under an object."""

from __future__ import annotations

from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Sequence

from .page import PageObject
from . import codes as _codes
from . import forest as _forest

TOUCH_TC = _codes.TC.TOUCH
KEYBOARD_TC = _codes.TC.KEYBOARD
INPUT_TCS = frozenset({TOUCH_TC, KEYBOARD_TC})
BUTTON_TC = 0x1001

#: the keys CtDraw's Touch tab writes, in the saver's bag order (the rest
#: follow the pgtouch.DBF column order). The Touch tab's Action list has a
#: fourth entry, Double click: DBLCLK_CMD / MSG_LOGDBL
TOUCH_KEYS = (
    "ACTION",
    "RPT_RATE",
    "CMD",
    "DOWN_CMD",
    "DBLCLK_CMD",
    "REPEAT_CMD",
    "MSG_LOG",
    "MSG_LOGDN",
    "MSG_LOGDBL",
    "MSG_LOGRPT",
)
#: the commands of the touch actions (Up / Down / Double click / Repeat) and
#: their log messages, pairwise
TOUCH_COMMANDS = ("CMD", "DOWN_CMD", "DBLCLK_CMD", "REPEAT_CMD")
TOUCH_LOGS = ("MSG_LOG", "MSG_LOGDN", "MSG_LOGDBL", "MSG_LOGRPT")
#: a button stores its touch commands in its OWN bag (pgbutton has no
#: MSG_LOGDN / MSG_LOGRPT columns)
BUTTON_TOUCH_KEYS = ("CMD", "DOWN_CMD", "REPEAT_CMD", "RPT_RATE", "MSG_LOG")
#: the keys of a keyboard command in the order CtDraw writes a NEW record's
#: bag (an area / privilege left 'same as the object' is no key). An edit of
#: an existing record sets its keys in place and appends a new one at the end,
#: as CtDraw does (``with_values``)
KEYBOARD_KEYS = ("KEY_SEQ", "CMD", "AREA", "PRIV", "MSG_LOG")
TOUCH_ACTION = "KEY_LBUTTON"
DEFAULT_RPT_RATE = "500"

#: field lengths from the Graphics Builder help (Object Touch / Keyboard
#: Commands): commands 253 characters, log messages 32
MAX_COMMAND = 253
MAX_LOG = 32
MAX_KEY_SEQ = 32


# --------------------------------------------------------------------------- forest
def parents(objs: Sequence[PageObject]) -> List[Optional[int]]:
    """Parent index of every record of a depth-first record list (the u16
    child count at payload 0 drives it), None for a root."""
    par: List[Optional[int]] = [None] * len(objs)
    stack: list = []
    for i, o in enumerate(objs):
        while stack and stack[-1][1] <= 0:
            stack.pop()
        if stack:
            par[i] = stack[-1][0]
            stack[-1][1] -= 1
        if o.class_flag:
            stack.append([i, o.class_flag])
    return par


subtree_end = _forest.subtree_end


def direct_children(objs: Sequence[PageObject], index: int) -> List[int]:
    """The direct children of record ``index``, in record order."""
    out: List[int] = []
    j, end = index + 1, subtree_end(objs, index)
    while j < end:
        out.append(j)
        j = subtree_end(objs, j)
    return out


def input_children(objs: Sequence[PageObject], index: int) -> List[int]:
    """The owner's Touch / Keyboard children, in record order."""
    return [c for c in direct_children(objs, index) if objs[c].type_code in INPUT_TCS]


def has_drawable_children(objs: Sequence[PageObject], index: int) -> bool:
    return any(objs[c].type_code not in INPUT_TCS for c in direct_children(objs, index))


# --------------------------------------------------------------------------- value bag
def _decode(po: PageObject):
    from . import genie_place as GP

    try:
        r = GP.decode(po)
    except Exception:  # noqa: BLE001 -- a record the strict walk cannot read
        return None
    if not r.entries or len(r.entries[0]) < 2:
        return None
    return r


def values(po: PageObject) -> "OrderedDict[str, str]":
    """The record's level-0 SUBSTITUTED value bag (e0.b1) -- the keys the
    register copies into pgtouch / pgkey (what the runtime uses)."""
    r = _decode(po)
    if r is None:
        return OrderedDict()
    return OrderedDict(r.entries[0][1].pairs)


def is_free(po: PageObject) -> bool:
    """A record with its own appearance (dwFlags 0x2) and a single-level bag:
    its value bag is the only copy of the values (an art-stripped genie /
    library copy re-derives them from the element)."""
    if not (po.subtype_size & 0x2):
        return False
    r = _decode(po)
    return r is not None and len(r.entries) == 1


def with_values(po: PageObject, updates: Dict[str, str], order: Iterable[str] = ()) -> PageObject:
    """A copy of an input record with its value bag updated."""
    from . import genie_place as GP

    r = _decode(po)
    if r is None:
        raise ValueError("record %d has no readable property bag" % po.index)
    bag = r.entries[0][1]
    keys = list(order) + [k for k in updates if k not in order]
    for k in keys:
        if k in updates:
            bag.set(k, "" if updates[k] is None else str(updates[k]))
    out = GP.to_po(GP.encode(r))
    out.index = po.index
    return out
