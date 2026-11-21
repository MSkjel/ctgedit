"""Record type codes and flag bits, by name."""

from __future__ import annotations


class TC:
    """Record type codes (the u32 at payload offset 2)."""

    LINE = 0x01
    FREEHAND = 0x02
    TEXT = 0x05
    BITMAP = 0x07  # a picture, or a placed symbol / a symbol-set state
    GROUP = 0x08  # group / genie / symbol / template container
    METAFILE = 0x09
    POLYLINE_LEGACY = 0x0A
    PIPE = 0x0B
    SYMBOL_SET = 0x0C
    ACTIVEX = 0x0D
    RECTANGLE = 0x0F
    POLYGON = 0x10
    ELLIPSE = 0x11
    WEB_CONTENT = 0x13
    ANPOINT = 0x1000
    BUTTON = 0x1001
    BAR = 0x1002
    TREND = 0x1003
    STRING = 0x1005
    TEXT_AN = 0x1006
    NUMBER_LEGACY = 0x1007
    COLOUR_AN = 0x1008
    CICODE = 0x1009  # AdvancedAnimation: a Cicode object
    KEYBOARD = 0x100B
    ANIM_SYMBOL = 0x100D
    SLIDER = 0x100F
    TOUCH = 0x1010
    DUMMY = 0xFFFF  # DummyMarker (a template / genie placeholder)


#: the input records an object owns as children (its touch / keyboard commands)
INPUT_TCS = frozenset({TC.TOUCH, TC.KEYBOARD})


class DWF:
    """The record's dwFlags word (PageObject.subtype_size, payload offset 6)."""

    LINK = 0x1  # a link lpstr after rect2
    STYLE = 0x2  # own appearance: the 54-byte style block
    LOCK = 0x4  # NOSELECT (Edit > Lock)
    BAGS = 0x8  # property bags
    DYNAMIC = 0x10  # runtime-dynamic (derived by the saver)
    ORPHANED = 0x20
    BUTTON_FONT = 0x40  # the button's font block
    BUTTON_TEXT = 0x80  # the button caption lpstr
    METADATA = 0x100
    GENIE = 0x200  # a placed genie instance
