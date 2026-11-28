"""The derive-time extras a GraphicObject keeps under '_' keys in ``raw``."""

from __future__ import annotations

RAW_EXTRAS = {
    # -- record identity (copied from the parsed record) ------------------------------------
    "_type_code": "the record's type code (format.codes.TC)",
    "_dwflags": "the record's flag word (format.codes.DWF)",
    "_handle": "geometry-header handle (AN = handle + 1)",
    "_link_an": "the record's AN string (an_str): the element record a member is composed from",
    "_name": "the geometry-header object name",
    "_anchor": "the anchor point (x, y)",
    "_rect1": "rect1 (Extent) l, t, r, b",
    "_rect2": "rect2 (Bounds) l, t, r, b",
    "_props": "the record's property bag as a dict (its value bag)",
    "_ole_clsid": "an ActiveX control's OLE root CLSID",
    "_ole_state": "an ActiveX control's saved state (its OLE compound file): the canvas picture's input",
    "_web_url": "a Web Content object's URL.NonGenie",
    # -- links and resolution --------------------------------------------------------------
    "_ref": "the library reference the record links to (lib.element.<ext)",
    "_resolved_ref": "the reference after resolution (what was found)",
    "_resolved_lib": "the library file the reference resolved to (base name)",
    "_def_ref": "a genie copy nested in a placed genie or composite (no link of its own): the "
    "element it places, read from the element record it is composed from (lib.element.<ext)",
    "_symbol_xform": "the transform that maps the resolved element into the placement box",
    "_local_xform": "the slot-1 orthogonal part (a, b, c, d) when it turns or mirrors",
    "_slot_scale": "a genuine anisotropic slot-1 scale (sx, sy) kept by the box fit",
    "_in_composite": "a member drawn by its composite genie's resolved art (not on its own)",
    "_from_template": "a record of the page template's prefix (template lock)",
    "_missing_element": "the library element the record links to is not found (its text): "
    "drawn as CtDraw's X box over rect1",
    "_under_missing": "inside a record whose element is not found: its X box hides it",
    "_alarm_indicator": "the group's alarm indicator frame geometry (format.alarmind)",
    "_trend": "the trend's pens and samples (own or from the element twin)",
    # -- pictures --------------------------------------------------------------------------
    "_embedded_bmp": "the record's picture (width, height, rgba, has_alpha)",
    "_embedded_bmp_flash": "the second (flash) picture, same layout",
    "_bmp_grafted": "the picture came from the element twin (element frame: turns with the member)",
    "_wmf": "a MetaFile record's WMF bytes",
    "_pic_bytes": "the bytes the record's own picture takes in the page (the Objects panel)",
    "_metafile": "the picture is a played metafile (never turned, aspect not kept)",
    "_button_icon": "a symbol button's face picture",
    "_class_icon": "the CtRes32 class icon drawn for this record (render.ctres)",
    # -- appearance decided at derive ------------------------------------------------------
    "_flash_slots": "the style block's six (on, off) pairs when one flashes (Simulate's blink)",
    "_twin_grafted": "the art twin's extras (nodes, pipe, line) were grafted",
    "_twin_text": "a stripped text took its caption / font from the twin",
    "_twin_shape": "a stripped rect / ellipse took the twin's shape style",
    "_no_outline": "draw no frame (an invisible hotspot or a twin without a line)",
    "_button_mode": "the button's draw style (index of VIS_MODE in render.buttons)",
    "_button_face": "a button's own face colour",
    "_button_font": "the button caption's font came from its 0x40 font block",
    "_font_px": "a font size in pixels (button font blocks)",
    "_label_left": "a genie label drawn left without its own frame",
    "_text_frame": "the text draws the frame its style asks for",
    "_text_transparent": "the text background is transparent",
    "_text_turns": "the text's quarter turns from its tail orient",
    "_text_scale": "the (sx, sy) a text's matrix scales its font by (a mirrored text resized)",
    # -- markers: what a graft did (read by tests and the derive checks, not by drawing) --
    "_class_flag": "the record's child count",
    "_twin_style": "the appearance came from a styled owner (art twin) record",
    "_template_line": "the line treatment came from the template / element twin",
    "_template_font": "the font came from the template / element twin",
    "_template_radius": "the corner radius came from the template twin",
    "_template_hollow": "drawn hollow because the template twin is hollow",
    "_template_colour": "the fill came from the template twin",
    "_value_field": "the white field behind a value display was added",
    # -- visibility ------------------------------------------------------------------------
    "_non_drawable": "behaviour / anchor record: the scene draws nothing for it",
    "_state_hidden": "a symbol-set state that is not the one drawn",
    "_dup_suppressed": "an exact duplicate of a later record (only the topmost draws)",
    "_rest_visible": "the VISIBLE gate's rest verdict (io.cicode_rest; True / False / None)",
}
