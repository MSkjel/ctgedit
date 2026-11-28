"""Event filters and small widgets shared by main_window.py and its mixins."""

from __future__ import annotations

from typing import Any

import contextlib
import hashlib  # noqa: F401 (re-exported)
import json  # noqa: F401 (re-exported)
import os

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401 (re-exported)

from .prefs.settings import app_settings, option  # noqa: F401 (re-exported)

from ..editing import EditableDocument, ElementDocument, CLIPBOARD_MIME, TransformError  # noqa: F401 (re-exported)
from .. import editing as editing_mod  # noqa: F401 (re-exported)
from ..model.document import Page  # noqa: F401 (re-exported)
from ..render.scene import PageScene  # noqa: F401 (re-exported)
from .editor.canvas import CanvasView  # noqa: F401 (re-exported to main_window)
from .style.theme import icon, set_note_role  # noqa: F401 (re-exported)
from .panels.properties import PropertyPanel  # noqa: F401 (re-exported to main_window)
from .panels.objects_dock import ObjectsDock  # noqa: F401 (re-exported to main_window)
from .panels.library_panel import LibraryPanel, paste_linked, set_paste_linked  # noqa: F401 (re-exported)
from .panels.diag_ui import DiagnosticsMixin  # noqa: F401 (re-exported to main_window)
from .modeless import ModelessMixin, ModelessWindows, doc_key  # noqa: F401 (re-exported)
from ..editing import ui_state  # noqa: F401 (re-exported)


def default_open_dir() -> str:
    """The live project's Example dir (the User tree the running Graphics
    Builder/runtime read), so the editor opens the files the engine sees, not
    a stale copy in another install; read at each call (CTG_SCADA_ROOT = a fake
    install). The home folder when no active citect.ini is found."""
    try:
        from ..io.citect_config import live_project_dir

        live = live_project_dir("Example")
        if live:
            return live
    except Exception:  # noqa: BLE001  # swallow-ok: no install found: the home folder
        pass
    return os.path.expanduser("~")


def __getattr__(name: str):
    # EXAMPLE_DIR is read at each use (a test or a fake install sets CTG_SCADA_ROOT after
    # import)
    if name == "EXAMPLE_DIR":
        return default_open_dir()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


#: the program's name in window titles ('Mixer[*] - ctgedit')
TITLE_APP = "ctgedit"


def saved_message(path: str, report: str = "", notes=()) -> str:
    """The status message of a save: 'Saved Mixer (Example): <what the save did>. <note>.'
    (the path is the message's detail: ui/notify.py)."""
    name = os.path.splitext(os.path.basename(path))[0]
    project = os.path.basename(os.path.dirname(os.path.abspath(path)))
    text = "Saved %s (%s)" % (name, project) if project else "Saved %s" % name
    if report:
        text += ": %s" % report
    parts = [text] + [n.strip().rstrip(".") for n in notes if n and n.strip()]
    return ". ".join(parts) + "."


TOOLS = [
    # (tool id, label, icon, shortcut)
    ("select", "Select", "select", "V"),
    ("line", "Line", "line", "L"),
    ("freehand", "Free Hand Line", "freehand", "F"),
    ("rectangle", "Rectangle", "rectangle", "R"),
    ("rectangle_outline", "Rectangle (outline)", "rectangle_outline", "Shift+R"),
    ("ellipse", "Ellipse", "ellipse", "E"),
    ("ellipse_outline", "Ellipse (outline)", "ellipse_outline", "Shift+E"),
    ("polygon", "Polygon", "polygon", "P"),
    ("pipe", "Pipe", "pipe", "I"),
    ("text", "Text", "text", "T"),
    ("number", "Number", "number", "N"),
    ("button", "Button", "button", "B"),
    ("symbol", "Symbol", "symbol", "S"),
    ("symbol_set", "Symbol Set", "symbol_set", None),
    # not a drawing tool: a drag shows a distance, nothing is drawn (Tools > Measure)
    ("measure", "Measure", "measure", "M"),
    # the view tools (View menu): a drag zooms into a box / pans the page
    ("zoom", "Zoom Box", "zoom_box", "Z"),
    ("pan", "Hand", "hand", "H"),
]

ALIGN = [
    ("left", "Align Left", "align_left"),
    ("hcentre", "Align Horizontal Centres", "align_hcentre"),
    ("right", "Align Right", "align_right"),
    ("top", "Align Top", "align_top"),
    ("vcentre", "Align Vertical Centres", "align_vcentre"),
    ("bottom", "Align Bottom", "align_bottom"),
]

V34_KINDS = [
    ("trend", "Trend"),
    ("string", "String"),
    ("digital_text", "Digital Text"),
    ("color_flood", "Color Flood"),
    ("bar_graph", "Bar Graph"),
    ("slider", "Slider"),
    ("animated_symbol", "Animated Symbol"),
    ("cicode_object", "Cicode Object"),
    ("animation_keyboard", "Animation Keyboard"),
    ("pagewide_keyboard", "Pagewide Keyboard"),
]

#: the special objects that are canvas TOOLS (tool id -> icon): CtDraw's
#: toolbox draws them (help 1172142 / 1172144 / 1172149 / 1172150) and opens
#: their property sheet once placed
SPECIAL_TOOLS = {
    "trend": "trend",
    "cicode_object": "cicode_object",
    "web_content": "web",
    "activex": "activex",
    "process_analyst": "process_analyst",
    "scheduler": "scheduler",
    "db_exchange": "db_exchange",
    "pelco": "pelco",
}

#: one plain sentence per command for its tool tip and status bar line (ui/style/tips.py), by
#: command name: the MainWindow attribute ('act_dup'), 'tool.<id>', 'align.<mode>',
#: 'textflag.<key>', 'justify.<mode>', 'effect.<n>', 'external.<n>', 'insert.<kind>',
#: 'v34.<kind>' (MainWindow _command_actions). A command's own status tip wins.
DESCRIPTIONS = {
    "act_new": "Start a new page or library element.",
    "act_open": "Open a page or a library element of a project.",
    "act_messages": "List this window's messages again, newest first.",
    "act_save": "Save the page.",
    "act_save_as": "Save the page under another name or in another project.",
    "act_save_all": "Save every open page that has changes.",
    "act_close_doc": "Close the page shown.",
    "act_undo": "Take the last change back.",
    "act_redo": "Make the change you took back again.",
    "act_page_history": "See the earlier saved versions of the page, and open, compare or restore one.",
    "act_mark_changes": "Mark the objects that are new, changed or removed since the save.",
    "act_preview_states": "Set the page's conditions by hand and see the page in that state.",
    "act_click_map": "Shade what takes a click or a key, and mark what a click never reaches.",
    "act_next_change": "Select and show the next object that differs from the saved page.",
    "act_prev_change": "Select and show the previous object that differs from the saved page.",
    "act_select_changed": "Select every object that is new or changed since the save.",
    # the underlay: a page or a picture drawn faded on the canvas to place objects by
    # (ui/editor/underlay.py); never part of the page
    "act_underlay_page": "Draw another page faded on the canvas, lined up with this page, "
    "to place objects where it has them.",
    "act_underlay_image": "Draw a picture faded on the canvas to trace over.",
    "act_underlay_version": "Draw an earlier saved version of this page faded on the canvas.",
    "act_underlay_show": "Show or hide the underlay of this page.",
    "act_underlay_above": "Draw the underlay over the objects instead of under them.",
    "act_underlay_move": "Drag the underlay into place with the mouse instead of the objects.",
    "act_underlay_settings": "Change the underlay's place, size, opacity and tint.",
    "act_underlay_remove": "Take the underlay off this page.",
    "act_compile": "Compile the page's project.",
    "act_run": "Save the page, compile the project and start it in the Plant SCADA runtime.",
    "act_options": "Change how ctgedit looks and works.",
    "act_goto": "Find an object by its number, name or tag and select it.",
    "act_rename": "Give the selected object a name, in the Objects panel.",
    "act_rename_objects": "Name the selected objects by a pattern, or find and replace in their names.",
    "act_find": "Find text, tags and expressions on the page.",
    "act_replace": "Find text, tags and expressions and replace them.",
    "act_lock": "Keep the selected objects from moving while you edit others.",
    "act_unlock": "Let the selected locked objects move again.",
    "act_lock_toggle": "Lock the selection, or unlock it. With nothing selected, unlock the "
    "object under the pointer.",
    "act_unlock_all": "Unlock every locked object on the page.",
    "act_hide_toggle": "Hide the selected objects in the editor, or show them again. The page keeps them.",
    "act_deselect": "Select nothing.",
    "act_invert_sel": "Select every object that is not selected, and nothing that is.",
    "act_select_same_element": "Select every genie or symbol from the same library element.",
    "act_select_same_kind": "Select every object of the same kind.",
    "act_select_same_fill": "Select every object with the same fill colour.",
    "act_select_same_line_colour": "Select every object with the same line colour.",
    "act_select_same_line_width": "Select every object with the same line width.",
    "act_select_same_font": "Select every text, number and button with the same font and size.",
    "act_select_same_size": "Select every object of the same width and height.",
    "act_select_same_anim": "Select every object that uses one of the same animation expressions.",
    "act_select_tag": "Select every object whose expressions or genie parameters name a tag.",
    "act_select_previous": "Go back to the selection before the last change.",
    "act_break_lock": "Ignore every lock while this is on.",
    "act_paste_genie": "Choose a genie from the libraries, then click where it goes.",
    "act_paste_symbol": "Choose a symbol from the libraries, then click where it goes.",
    "act_paste_super": "Choose a super genie, then click where it goes.",
    "act_paste_flash": "Paste two picture symbols as one that flashes.",
    "act_paste_special": "Paste the clipboard in a format you choose.",
    "act_paste_composite": "Place a composite genie: several genies that move as one.",
    "act_import": "Put a picture file on the page.",
    "act_import_flash": "Import two pictures as one symbol that flashes.",
    "act_dup": "Copy the selected objects next to themselves.",
    "act_del": "Delete the selected objects, or the selected nodes.",
    "act_cut": "Move the selected objects to the clipboard.",
    "act_copy": "Copy the selected objects to the clipboard.",
    "act_paste": "Paste the clipboard onto the page.",
    "act_sel_all": "Select every object on the page.",
    "act_rot_cw": "Turn the selected objects a quarter turn to the right.",
    "act_rot_ccw": "Turn the selected objects a quarter turn to the left.",
    "act_flip_h": "Mirror the selected objects left to right.",
    "act_flip_v": "Mirror the selected objects top to bottom.",
    "act_adjust": "Type the position and size of the selected objects.",
    "act_to_bitmap": "Turn the selected objects into one picture.",
    "act_cut_link": "Unlink the selected symbols or genies from their libraries, or the "
    "page from its template.",
    "act_group": "Join the selected objects into one group.",
    "act_ungroup": "Split the selected groups and genies into their parts.",
    "act_dist_h": "Space three or more objects evenly from left to right.",
    "act_dist_v": "Space three or more objects evenly from top to bottom.",
    "act_same_size": "Give the selected objects the size of the one selected last.",
    "act_front": "Put the selected objects in front of all others.",
    "act_back": "Put the selected objects behind all others.",
    "act_fwd": "Move the selected objects one step forward.",
    "act_bwd": "Move the selected objects one step back.",
    "act_genie_params": "Change the values the selected genie was placed with.",
    "act_obj_props": "Open the full property sheet of the selected object.",
    "act_page_props": "Change the page's size, colours, template and events.",
    "act_copy_appearance": "Keep the selected object's look for Paste Appearance.",
    "act_paste_appearance": "Give the selected objects the copied look.",
    "act_save_style": "Keep the selected object's look as a named style.",
    "act_manage_styles": "Rename, remove, sort, import and export the named styles.",
    "act_style_default": "Draw new objects of this kind with the selected object's look.",
    "act_swap_colors": "Swap one colour for another on the page or the selection.",
    "act_new_fill_colour": "Choose the colour new shapes are filled with.",
    "act_new_line_colour": "Choose the colour of new lines, pipes and shape edges.",
    "act_new_text_colour": "Choose the colour of new text and numbers.",
    "act_new_colours_swap": "Swap the fill and line colours of new objects.",
    "act_new_no_fill": "Draw new shapes hollow.",
    "act_new_colours_auto": "Give new objects colours that contrast with the page.",
    "act_adjust_colors": "Change the hue, lightness and saturation of colours.",
    "act_fav_colors": "Choose the colours the colour pickers offer first.",
    "act_copy_to_lib": "Save the selected objects as a symbol or genie in a library.",
    "act_pack_libs": "Make library files smaller by removing deleted elements.",
    "act_pack_db": "Make the project's database tables smaller by removing deleted rows, as Studio's Pack does.",
    "act_update_pages": "Bring the symbols and genies of the project's pages up to date.",
    "act_check_project": "Check every page of a project: Cicode that does not compile, tags and pages it does not have, and the other page checks.",
    "act_rename_pages": "Rename the project's pages, one or many at once, with their project database rows.",
    "act_close_builder": "Close the Graphics Builder so that it reads the changed libraries again.",
    "act_attach_super": "Attach super genies to the genie, to open from its buttons.",
    "act_genie_form": "Design the form a genie's values are entered in.",
    "act_replace_genie": "Put another genie in place of placed genies, keeping their values.",
    "act_used_by": "List the pages and genies that use this element.",
    "act_hotspot": "Set the point a symbol or genie is placed by.",
    "act_paste_linked": "Keep pasted symbols linked to their library.",
    "act_bitmap_editor": "Edit the selected picture pixel by pixel.",
    "act_font_dialog": "Choose the font, size and style of the selected text.",
    "act_edit_text": "Type the selected text on the page.",
    "act_simulate": "Run the page in the Simulate window, with its tags to change.",
    "act_check_page": "List what is wrong on this page.",
    "act_zoom_in": "Show the page bigger.",
    "act_zoom_out": "Show the page smaller.",
    "act_zoom_fit": "Show the whole page.",
    "act_zoom_100": "Show the page at its real size.",
    "act_zoom_all": "Show the page and every object around it.",
    "act_zoom_sel": "Show the selected objects as big as they fit.",
    "act_grid": "Show the grid dots on the page.",
    "act_snap": "Make objects you draw and move keep to the grid.",
    "act_show_tools": "Show the floating drawing toolbox.",
    "act_collapse_ribbon": "Show only the ribbon's tabs. A click on a tab shows its commands over the page.",
    "act_print": "Print the page.",
    "tool.select": "Select, move and resize objects.",
    "tool.line": "Drag on the page to draw a straight line.",
    "tool.freehand": "Drag on the page to draw a free hand line.",
    "tool.pipe": "Click the corners of a pipe; double-click to end it.",
    "tool.rectangle": "Drag on the page to draw a filled rectangle. The arrow offers the outline.",
    "tool.rectangle_outline": "Drag on the page to draw a rectangle outline.",
    "tool.ellipse": "Drag on the page to draw a filled ellipse. The arrow offers the outline.",
    "tool.ellipse_outline": "Drag on the page to draw an ellipse outline.",
    "tool.polygon": "Click the corners of a polygon; double-click to end it.",
    "tool.text": "Click on the page and type a text.",
    "tool.number": "Drag on the page to place a number that shows a tag's value.",
    "tool.button": "Drag on the page to place a button that runs a command.",
    "tool.symbol": "Click on the page to place a symbol from the libraries.",
    "tool.symbol_set": "Place a symbol that changes with a tag's value.",
    "tool.measure": "Drag on the page to measure a distance; nothing is drawn.",
    "tool.trend": "Drag on the page to place a trend.",
    "tool.cicode_object": "Click on the page to place a Cicode object.",
    "tool.web_content": "Drag on the page to place a web page view.",
    "tool.process_analyst": "Drag on the page to place a Process Analyst view.",
    "tool.scheduler": "Drag on the page to place a scheduler.",
    "tool.db_exchange": "Drag on the page to place a Database Exchange control.",
    "tool.pelco": "Drag on the page to place a Pelco camera view.",
    "tool.activex": "Drag on the page to place the chosen ActiveX control.",
    "align.left": "Line the left edges of the selected objects up.",
    "align.hcentre": "Line the selected objects up on one vertical centre line.",
    "align.right": "Line the right edges of the selected objects up.",
    "align.top": "Line the top edges of the selected objects up.",
    "align.vcentre": "Line the selected objects up on one horizontal centre line.",
    "align.bottom": "Line the bottom edges of the selected objects up.",
    "textflag.bold": "Make the selected text bold.",
    "textflag.italic": "Make the selected text italic.",
    "textflag.underline": "Underline the selected text.",
    "textflag.strikeout": "Strike the selected text through.",
    "justify.left": "Line the text up on its left edge.",
    "justify.centre": "Centre the text.",
    "justify.right": "Line the text up on its right edge.",
    "effect.normal": "Draw the text flat.",
    "effect.shadowed": "Draw the text with a shadow.",
    "effect.raised": "Draw the text raised.",
    "effect.lowered": "Draw the text sunken.",
    "effect.embossed": "Draw the text embossed.",
    "external.studio": "Start Plant SCADA Studio.",
    "external.cicode_editor": "Start the Cicode Editor.",
    "external.runtime_manager": "Start the Runtime Manager.",
    "external.setup_wizard": "Start the Computer Setup Wizard.",
    "external.setup_editor": "Start the Computer Setup Editor.",
    "insert.activex": "Choose an ActiveX control, then drag on the page to place it.",
    "v34.string": "Place an old V3/V4 string in the middle of the page.",
    "v34.digital_text": "Place an old V3/V4 digital text in the middle of the page.",
    "v34.color_flood": "Place an old V3/V4 colour flood in the middle of the page.",
    "v34.bar_graph": "Place an old V3/V4 bar graph in the middle of the page.",
    "v34.slider": "Place an old V3/V4 slider in the middle of the page.",
    "v34.animated_symbol": "Place an old V3/V4 animated symbol and choose its symbols.",
    "v34.animation_keyboard": "Place an old V3/V4 animation keyboard in the middle of the page.",
    "v34.pagewide_keyboard": "Place an old V3/V4 pagewide keyboard in the middle of the page.",
    # every menu command has a sentence (tests/test_discover_ux.py walks
    # the menu bar); the guideline hints the menus show as status tips
    "act_zoom_window": "Open the magnifier: a window that shows the page around the pointer enlarged.",
    "tool.zoom": "Drag a box on the page to zoom in on it; a click zooms in one step.",
    "tool.pan": "Drag the page to scroll it.",
    "act_var_tags": "Show the Tags panel and put the keyboard in its search box.",
    "act_guidelines": "Add and remove guidelines by their position, and turn their display "
    "and snap on or off.",
    "act_show_guides": "Show the guidelines on the page. Drag one to move it, hold Ctrl when "
    "you let go to copy it, drop it at the page edge to delete it.",
    "act_snap_guides": "Make a moved edge or centre snap to a nearby guideline.",
    "act_clear_guides": "Remove every guideline of the page.",
    "act_rulers": "Show the rulers along the canvas. Drag from a ruler to add a guideline.",
    "act_navigator": "Show a small map of the page over the canvas.",
    "act_grid_setup": "Set the grid's pitch and origin, and turn its display and snap on or off.",
    "act_page_defaults": "Choose the style, size and template new pages start with.",
    "act_focus_mode": "Hide the menu bar to give the page the room. Alt shows it.",
    "act_reset_ui": "Put the panels, the ribbon and the toolbars back where they started.",
    "act_transparent_paste": "Choose the colour that is see-through in pasted pictures.",
    "act_print_setup": "Choose the printer, the paper and its orientation.",
    "act_slow_keys": "Move the selection 1 px per arrow key press, whatever the arrow key "
    "step in Settings is.",
    "act_slow_mouse": "Move the pointer's drags a part of the distance the mouse moves, for fine placing.",
    "act_manage_views": "Rename, reorder and remove the page's saved views.",
    "act_show_all_roots": "Show every object hidden in the editor again.",
    "act_update_page_template": "Bring this page up to date with its changed template.",
    "act_compose_nested": "Compose every genie nested in this library element again from "
    "its current definition, as one undo step. Save keeps it.",
    "act_edit_template": "Open the page's template to edit it.",
    "act_cut_template_link": "Make the template's objects part of this page and forget the template.",
    "act_status_bar": "Show the status bar under the page.",
    "act_revert": "Throw the unsaved changes away and read the page from disk again.",
    "act_copy_page_path": "Put the page file's full path on the clipboard.",
    "act_copy_page_name": "Put the page's name on the clipboard.",
    "act_reveal_page": "Open Explorer with the page file selected.",
    "act_show_in_browser": "Open the page browser with this page selected.",
    "act_exit": "Close every window of ctgedit.",
    "act_palette": "Search every command, page, symbol, genie, tag and setting by name.",
    "act_palette_here": "Open the command search at the pointer, with the drawing tools.",
    "act_restore_backup": "Put back the pages or libraries a batch command rewrote.",
    "act_next_page": "Show the next page tab.",
    "act_prev_page": "Show the previous page tab.",
    "act_new_window": "Open another editor window.",
    "act_cascade": "Lay the editor windows out one behind the other.",
    "act_tile_h": "Lay the editor windows out side by side.",
    "act_tile_v": "Lay the editor windows out one above the other.",
    "act_help": "Open the manual.",
    "act_getting_started": "Open the manual's first page.",
    "act_differences": "Open the manual's page for users of the Graphics Builder.",
    "act_troubleshooting": "Open the manual's page on problems and what to do about them.",
    "act_cicode_ref": "Open AVEVA's Cicode function reference in the browser.",
    "act_open_logs": "Open the folder of ctgedit's activity log.",
    "act_check_updates": "See whether a newer ctgedit is out, and install it.",
    "act_about": "Show ctgedit's version and the install it uses.",
    "act_prev_panel": "Move the keyboard to the previous panel, or back to the page.",
    "act_go_page": "Put the keyboard back on the page.",
    "act_go_objects": "Move the keyboard to the Objects panel.",
    "act_go_library": "Move the keyboard to the Library panel.",
    "act_go_tags": "Move the keyboard to the Tags panel.",
    "act_go_props": "Move the keyboard to the Properties panel.",
    "act_go_history": "Move the keyboard to the History panel.",
    "act_thumb": "Draw the page's stored thumbnail behind the page, to compare (a developer tool).",
}

#: the status bar line of each submenu of the menu bar, by its title
SUBMENU_DESCRIPTIONS = {
    "Recent Pages": "The pages opened last.",
    "Import Recent": "The pictures imported last, to put on the page again.",
    "Select": "Select objects by what they are, or change the selection.",
    "Select Same": "Select every object that shares a property with the selected one.",
    "Styles": "Keep an object's look as a named style and give it to other objects.",
    "Update Style from Selection": "Give a style the look of the selected object.",
    "Rename Style": "Give a style another name.",
    "Select Objects with This Style": "Select every object drawn with a style.",
    "Set as Default for New Objects": "Draw new objects of a kind with a style.",
    "Insert": "Place a trend, a Cicode object or a control on the page.",
    "Library": "Symbols, genies and templates: copy to a library, unlink, attach, find their users.",
    "Saved Views": "Named zooms and positions of the page, to come back to.",
    "Outside the Page": "What the canvas shows around the page.",
    "Grid": "The grid's display, snap and pitch.",
    "Guidelines": "Lines across the page that objects snap to.",
    "Underlay": "A page, a picture or an earlier version drawn faded on the canvas to place objects by.",
    "Labels": "How the page's @( ) language labels are shown: as stored, the native text, "
    "or one language of the project.",
    "Panels": "Show or hide the panels, and move the keyboard to one.",
    "Draw": "The drawing tools.",
    "Colours": "Swap, adjust and choose colours, and the colours of new objects.",
    "Colours of New Objects": "The fill, line and text colours new objects get.",
    "Pictures": "Replace, export and resample the selected picture; find duplicates.",
    "External Tools": "Start the Plant SCADA programs.",
    "V3/V4 Objects": "The old V3/V4 object kinds.",
    "Advanced": "Rarely used Graphics Builder commands.",
    "Go to Page": "Show one of the first nine page tabs.",
    "Move to Window": "Move this page tab to another editor window.",
}

#: the manual's section of each drawing tool (F1 over its button or menu item)
TOOL_HELP = {
    "line": "drawing#lines",
    "freehand": "drawing#free-hand-lines",
    "rectangle": "drawing#rectangles",
    "rectangle_outline": "drawing#rectangles",
    "ellipse": "drawing#ellipses",
    "ellipse_outline": "drawing#ellipses",
    "polygon": "drawing#polygons",
    "pipe": "drawing#pipes",
    "text": "drawing#text",
    "number": "drawing#numbers",
    "button": "drawing#buttons",
    "symbol": "drawing#symbols-and-genies",
    "symbol_set": "drawing#symbol-sets",
    "measure": "drawing#measure",
    "select": "selecting-and-arranging",
    "trend": "special-objects#trends",
    "cicode_object": "special-objects#cicode-objects",
    "web_content": "special-objects#web-content",
    "activex": "special-objects#activex-controls",
    "process_analyst": "special-objects#activex-controls",
    "scheduler": "special-objects#activex-controls",
    "db_exchange": "special-objects#activex-controls",
    "pelco": "special-objects#activex-controls",
}

#: what a command with its own enable rule (ViewState.CUSTOM_ENABLE) needs while greyed
NEEDS_CUSTOM = {
    "act_undo": "There is nothing to undo.",
    "act_redo": "There is nothing to redo.",
    "act_revert": "Open a page that is saved on disk.",
    "act_paste_appearance": "Copy an appearance first, then select objects.",
    "act_ungroup": "Select a group or a genie.",
    "act_cut_link": "Select symbols or genies, or open a page with a template.",
    "act_cut_template_link": "Open a page with a template.",
    "act_edit_template": "Open a page with a template.",
    "act_update_page_template": "Open a page with a template.",
    "act_genie_params": "Select a genie.",
    "act_attach_super": "Select one genie, or open a genie of a library.",
    "act_genie_form": "Select one genie, or open a genie of a library.",
    "act_replace_genie": "Open a page or a library genie.",
    "act_hotspot": "Open a symbol or genie of a library.",
    "act_compose_nested": "Open a symbol or genie of a library.",
    "act_used_by": "Select one symbol or genie, or open a library element.",
    "act_update_users": "Select one symbol or genie, or open a library element.",
    "act_bitmap_editor": "Select one picture.",
    "act_replace_picture": "Select one picture.",
    "act_export_picture": "Select one picture.",
    "act_resample_picture": "Select one stretched picture.",
    "act_font_dialog": "Select a text, number or button.",
    "act_edit_text": "Select one text, number or button.",
    "act_underlay_version": "Save the page with earlier versions kept (Settings > Editing > Safety).",
    "act_underlay_show": "Underlay a page or a picture first.",
    "act_underlay_above": "Underlay a page or a picture first.",
    "act_underlay_move": "Show an underlay first.",
    "act_underlay_remove": "Underlay a page or a picture first.",
    "textflag": "Select a text, number or button.",
    "justify": "Select a text, number or button.",
    "effect": "Select a text, number or button.",
    "align": "Select two or more objects.",
    # its own rule: one object with Align to: The page, then
    # NEEDS_ALIGN_PAGE
    "act_align_dialog": "Select two or more objects.",
    "tool": "Open a page first.",
    "insert": "Open a page first.",
    "v34": "Open a page first.",
}
#: what Align and Align... need with Settings > Editing 'Align to:
#: The page' (MainWindow.apply_editing_options puts it on them, NEEDS_CUSTOM's otherwise)
NEEDS_ALIGN_PAGE = "Select an object."
#: the ``at`` of paste_clipboard / _paste_image when the caller has
#: not read where the paste lands: they read MainWindow.paste_point then (None = next to the
#: copied objects)
PASTE_POINT_UNREAD = object()

#: the View > Labels (and Settings) names of the label modes; a language mode
#: shows as the language
LABEL_MODE_NAMES = {"literal": "As Stored: @(text)", "native": "Native Text"}

INSERT_KINDS = [
    ("activex", "ActiveX Control…"),
    ("process_analyst", "Process Analyst"),
    ("web_content", "Web Content"),
    ("scheduler", "Scheduler"),
    # Database Exchange / Pelco: only when a page of the install holds one (factory.CONTROL_SOURCES)
    ("db_exchange", "Database Exchange"),
    ("pelco", "Pelco Camera Viewer"),
]


class WindowState:
    """The state the main window's parts share: every attribute one of its files (main_window,
    main_window_base, window/*) sets and another reads, with its type and the file that sets
    it. Declaration only (nothing is created here); tests/test_window_state.py keeps it in step
    with the code."""

    _auto_bar: Any  # set in main_window.py
    _page: Any  # set in window/view.py (the page shown)
    _break_lock: Any  # set in main_window.py, window/commands.py
    _dialogs: Any  # ModelessWindows, set in main_window.py
    _disk_timer: Any  # QTimer, set in main_window.py
    _docs: list  # set in main_window.py
    _grid_origin: tuple  # set in main_window.py, window/commands.py
    _grid_size: Any  # set in main_window.py, window/commands.py
    _grid_y: Any  # set in main_window.py, window/commands.py
    _hidden_roots: Any  # set in main_window.py, window/tabs.py, window/view.py
    _import_recent_menu: Any  # set in main_window.py
    _locked: set  # set in main_window.py, window/tabs.py
    _mru_menu: Any  # set in main_window.py
    _nudge_step: int  # set in main_window.py, window/shell.py
    _show_grid: Any  # set in main_window.py, window/commands.py, window/shell.py
    _show_thumb: Any  # set in main_window.py, window/shell.py
    _toolbox: Any  # ToolboxWindow, set in window/commands.py
    _watcher: Any  # QFileSystemWatcher, set in main_window.py
    _window_menu: Any  # set in main_window.py
    act_aliased_text: Any  # set in main_window.py
    act_align_dialog: Any  # set in main_window.py
    act_attach_super: Any  # set in main_window.py
    act_back: Any  # set in main_window.py
    act_bitmap_editor: Any  # set in main_window.py
    act_break_lock: Any  # set in main_window.py
    act_bwd: Any  # set in main_window.py
    act_check_display: Any  # set in main_window.py
    act_check_workspace: Any  # set in main_window.py
    act_close_all: Any  # set in main_window.py
    act_close_doc: Any  # set in main_window.py
    act_close_others: Any  # set in main_window.py
    act_close_right: Any  # set in main_window.py
    act_close_saved: Any  # set in main_window.py
    act_compose_nested: Any  # set in main_window.py
    act_copy: Any  # set in main_window.py
    act_copy_page_name: Any  # set in main_window.py
    act_copy_page_path: Any  # set in main_window.py
    act_crosshair: Any  # set in main_window.py
    act_cut: Any  # set in main_window.py
    act_cut_link: Any  # set in main_window.py
    act_cut_template_link: Any  # set in main_window.py
    act_del: Any  # set in main_window.py
    act_dup: Any  # set in main_window.py
    act_edit_template: Any  # set in main_window.py
    act_edit_text: Any  # set in main_window.py
    act_export_picture: Any  # set in main_window.py
    act_focus_mode: Any  # set in main_window.py
    act_font_dialog: Any  # set in main_window.py
    act_front: Any  # set in main_window.py
    act_fwd: Any  # set in main_window.py
    act_genie_form: Any  # set in main_window.py
    act_genie_params: Any  # set in main_window.py
    act_grid: Any  # set in main_window.py
    act_group: Any  # set in main_window.py
    act_hotspot: Any  # set in main_window.py
    act_isolate: Any  # set in main_window.py
    act_lock: Any  # set in main_window.py
    act_manage_views: Any  # set in main_window.py
    act_move_to_window: Any  # set in main_window.py
    act_navigator: Any  # set in main_window.py
    act_new_from_last: Any  # set in main_window.py
    act_next_page: Any  # set in main_window.py
    act_next_view: Any  # set in main_window.py
    act_obj_props: Any  # set in main_window.py
    act_page_props: Any  # set in main_window.py
    act_pane_labels: Any  # set in main_window.py
    act_paste_appearance: Any  # set in main_window.py
    act_paste_composite: Any  # set in main_window.py
    act_paste_linked: Any  # set in main_window.py
    act_pin_tab: Any  # set in main_window.py
    act_prev_page: Any  # set in main_window.py
    act_prev_view: Any  # set in main_window.py
    act_redo: Any  # set in main_window.py
    act_rename: Any  # set in main_window.py
    act_reopen_closed: Any  # set in main_window.py
    act_repeat: Any  # set in main_window.py
    act_replace_genie: Any  # set in main_window.py
    act_replace_picture: Any  # set in main_window.py
    act_resample_picture: Any  # set in main_window.py
    act_reveal_page: Any  # set in main_window.py
    act_revert: Any  # set in main_window.py
    act_rulers: Any  # set in main_window.py
    act_save_view: Any  # set in main_window.py
    act_selection_hud: Any  # set in main_window.py
    act_show_all_roots: Any  # set in main_window.py
    act_show_guides: Any  # set in main_window.py
    act_show_in_browser: Any  # set in main_window.py
    act_show_tools: Any  # set in main_window.py
    act_simulate: Any  # set in main_window.py
    act_preview_states: Any  # set in main_window.py
    act_click_map: Any  # set in main_window.py
    act_slow_keys: Any  # set in main_window.py
    act_slow_mouse: Any  # set in main_window.py
    act_smart_guides: Any  # set in main_window.py
    act_snap: Any  # set in main_window.py
    act_snap_guides: Any  # set in main_window.py
    act_status_bar: Any  # set in main_window.py
    act_thumb: Any  # set in main_window.py
    act_undo: Any  # set in main_window.py
    act_ungroup: Any  # set in main_window.py
    act_unlock: Any  # set in main_window.py
    act_update_page_template: Any  # set in main_window.py
    act_update_users: Any  # set in main_window.py
    act_used_by: Any  # set in main_window.py
    act_zoom_sel: Any  # set in main_window.py
    act_zoom_window: Any  # set in main_window.py
    align_actions: list  # set in main_window.py
    doc: tuple  # set in main_window.py, window/files.py, window/shell.py, window/tabs.py
    doc_tabs: Any  # DocTabBar, set in main_window.py
    dock_history: Any  # set in main_window.py
    dock_library: tuple  # set in main_window.py
    dock_navigator: Any  # set in window/navigation.py
    dock_objects: tuple  # set in main_window.py
    dock_page_checks: Any  # set in main_window.py
    dock_preview_states: Any  # set in window/page_debug.py
    dock_props: tuple  # set in main_window.py
    dock_sa_checks: Any  # set in main_window.py
    dock_tags: Any  # set in main_window.py
    effect_actions: list  # set in main_window.py
    font_combo: Any  # QFontComboBox, set in main_window.py
    font_size: Any  # QSpinBox, set in main_window.py
    goto_tab_actions: list  # set in main_window.py
    hud: Any  # SelectionHUD, set in main_window.py
    insert_actions: list  # set in main_window.py
    justify_actions: list  # set in main_window.py
    library_dock: Any  # LibraryPanel, set in main_window.py
    modern_v34_actions: list  # set in main_window.py
    objects_dock: Any  # ObjectsDock, set in main_window.py
    preview_states: Any  # PreviewStatesPanel, set in window/page_debug.py
    props: Any  # PropertyPanel, set in main_window.py
    ribbon: Any  # RibbonBar, set in main_window.py
    sa_checks: Any  # WorkspaceChecksPanel, set in main_window.py
    scene: Any  # set in main_window.py, window/tabs.py
    sel_filter: Any  # QComboBox, set in main_window.py
    sel_marquee: Any  # QComboBox, set in main_window.py
    shape_filled: Any  # QCheckBox, set in main_window.py
    shape_style: Any  # QComboBox, set in main_window.py
    shape_width: Any  # QSpinBox, set in main_window.py
    surround_actions: dict  # set in main_window.py
    tags_dock: Any  # TagsDock, set in main_window.py
    text_flag_actions: list  # set in main_window.py
    tool_actions: dict  # set in main_window.py
    view: Any  # CanvasView, set in main_window.py


class WheelGuard(QtCore.QObject):
    """A mouse wheel over an UNFOCUSED spinbox / combo scrolls the panel
    instead of silently editing the object under it."""

    TYPES = frozenset({QtCore.QEvent.Type.Wheel})

    def eventFilter(self, obj, ev):
        return self.handle(obj, ev, ev.type())

    def handle(self, obj, ev, t) -> bool:
        if (
            t == QtCore.QEvent.Wheel
            and isinstance(obj, (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox))
            and not obj.hasFocus()
        ):
            ev.ignore()
            return True
        return False


class _PopupKeyGuard(QtCore.QObject):
    """F4 (and Alt+Down) in a drop-down list opens its list, as everywhere in Windows: a
    window command on that key (Edit > Repeat's F4) does not run instead while a combo box
    has the keyboard."""

    TYPES = frozenset({QtCore.QEvent.Type.ShortcutOverride})

    def eventFilter(self, obj, ev):
        return self.handle(obj, ev, ev.type())

    def handle(self, obj, ev, t) -> bool:
        if t != QtCore.QEvent.ShortcutOverride:
            return False
        key = ev.key()
        if key == QtCore.Qt.Key_F4:
            want = QtCore.Qt.NoModifier
        elif key == QtCore.Qt.Key_Down:
            want = QtCore.Qt.AltModifier
        else:
            return False
        if (ev.modifiers() & ~QtCore.Qt.KeypadModifier) != want:
            return False
        w = obj
        if isinstance(w, QtWidgets.QLineEdit) and isinstance(w.parentWidget(), QtWidgets.QComboBox):
            w = w.parentWidget()  # (an editable combo box's text)
        if isinstance(w, QtWidgets.QComboBox) and w.isEnabled():
            ev.accept()  # the combo box's own key: it opens its list
            return True
        return False


def install_popup_key_guard() -> None:
    app = QtWidgets.QApplication.instance()
    if app is not None and getattr(app, "_ctg_popup_keys", None) is None:
        app._ctg_popup_keys = _PopupKeyGuard(app)
        app_filter().add(app._ctg_popup_keys, _PopupKeyGuard.TYPES)


class _MenuCloseWatch(QtCore.QObject):
    """Notes that a menu closed in this turn of the event loop. A command picked in a menu
    runs right after its menu hides, with the pointer still on the menu's item, which can
    hang over the canvas: a paste at the pointer then goes next to the copied objects
    (MainWindow.paste_point; the canvas's right-click menu keeps the point it was opened at)."""

    TYPES = frozenset({QtCore.QEvent.Type.Hide})

    def __init__(self, parent=None):
        super().__init__(parent)
        self.closing = False

    def handle(self, obj, ev, t) -> bool:
        if not self.closing and isinstance(obj, QtWidgets.QMenu):
            self.closing = True
            QtCore.QTimer.singleShot(0, self, self._done)
        return False

    def _done(self) -> None:
        self.closing = False


def menu_just_closed() -> bool:
    """A menu closed in this turn of the event loop (a command picked in it runs now)."""
    app = QtWidgets.QApplication.instance()
    watch = getattr(app, "_ctg_menu_watch", None) if app is not None else None
    return watch is not None and watch.closing


def install_menu_close_watch() -> None:
    app = QtWidgets.QApplication.instance()
    if app is not None and getattr(app, "_ctg_menu_watch", None) is None:
        app._ctg_menu_watch = _MenuCloseWatch(app)
        app_filter().add(app._ctg_menu_watch, _MenuCloseWatch.TYPES)


#: the user input a page's deferred open checks run before
INPUT_EVENTS = frozenset(
    {
        QtCore.QEvent.Type.MouseButtonPress,
        QtCore.QEvent.Type.MouseButtonDblClick,
        QtCore.QEvent.Type.KeyPress,
        QtCore.QEvent.Type.ShortcutOverride,
        QtCore.QEvent.Type.Drop,
        QtCore.QEvent.Type.ContextMenu,
    }
)


class AppEventFilter(QtCore.QObject):
    """The one application-wide event filter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._handlers: list = []  # (handler, its event types), called in order
        self._types = frozenset(INPUT_EVENTS)
        #: () -> None, set while a page open's deferred checks are pending
        self.before_input = None

    def add(self, handler, types) -> None:
        """Register ``handler.handle(obj, ev, type)`` for ``types``; a later
        one is called first, as Qt calls the application filters."""
        types = frozenset(types)
        self._handlers.insert(0, (handler, types))
        self._types = self._types | types

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t not in self._types:
            return False
        hook = self.before_input
        if hook is not None and t in INPUT_EVENTS:
            self.before_input = None
            try:
                hook()
            except Exception:  # noqa: BLE001  # swallow-ok: reported through sys.excepthook; the input goes on
                import sys

                sys.excepthook(*sys.exc_info())
        for h, types in self._handlers:
            if t in types:
                # (one handler's error does not stop the next)
                try:
                    if h.handle(obj, ev, t):
                        return True
                except Exception:  # noqa: BLE001  # swallow-ok: reported through sys.excepthook; the next handler runs
                    import sys

                    sys.excepthook(*sys.exc_info())
        return False


def app_filter() -> AppEventFilter | None:
    """The application's combined event filter (installed once)."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    f = getattr(app, "_ctg_app_filter", None)
    if f is None:
        f = app._ctg_app_filter = AppEventFilter(app)
        app.installEventFilter(f)
    return f


def install_wheel_guard() -> None:
    app = QtWidgets.QApplication.instance()
    if app is not None and getattr(app, "_ctg_wheel_guard", None) is None:
        app._ctg_wheel_guard = WheelGuard(app)
        app_filter().add(app._ctg_wheel_guard, WheelGuard.TYPES)


def run_all_post_open() -> None:
    """User input is about to reach the application: every window's pending
    page-open checks run first (MainWindow._opened)."""
    for w in editor_windows():
        with contextlib.suppress(RuntimeError):  # a window being destroyed
            if w.__dict__.get("_post_open_pending"):
                w._run_post_open()


class PaintWatch(QtCore.QObject):
    """Calls ``fn`` once, right after ``widget``'s next paint has finished."""

    def __init__(self, widget: QtWidgets.QWidget, fn):
        super().__init__(widget)
        self._fn = fn
        widget.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.Type.Paint and self._fn is not None:
            obj.removeEventFilter(self)
            QtCore.QTimer.singleShot(0, self, self._fire)
        return False

    def _fire(self) -> None:
        fn, self._fn = self._fn, None
        self.deleteLater()
        if fn is not None:
            fn()


class PendingPage:
    """A tab of the restored session that is not opened yet: the page is read
    when the tab is first shown (MainWindow._materialise). It answers what
    the window asks of every open tab -- its path; never unsaved, read-only
    or damaged -- and nothing else (AttributeError)."""

    def __init__(self, path: str):
        self.path = path
        self.dirty = False
        self.read_only = False
        self.damage = ()
        from ..editing import DocumentUiState

        self.ui = DocumentUiState()  # (what the window keeps about every tab)

    def __repr__(self) -> str:
        return "<not opened yet: %s>" % self.path


#: the bar over the canvas with the page's notices (read-only, damaged, changed on disk, the
#: automatic updates ...): ui/editor/notices.PageNoticeBar, kept by the window as ``_auto_bar``
from .editor.notices import PageNoticeBar  # noqa: E402

AutoChangesBar = PageNoticeBar


def main_window_class():
    """MainWindow (defined in ctgedit.ui.main_window, which imports this module first)."""
    from . import main_window

    return main_window.MainWindow


def editor_windows() -> list:
    """The open editor windows, in the order they opened."""
    return list(main_window_class().instances)
