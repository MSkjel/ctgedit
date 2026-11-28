"""The settings store."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Optional

from PySide6 import QtCore

ORG, APP = "ctgedit", "editor"


#: one QSettings per thread (a QSettings object is not shared between threads; one per
#: call would make hundreds on a page open)
_LOCAL = threading.local()


def app_settings() -> QtCore.QSettings:
    ini = os.environ.get("CTG_SETTINGS_INI") or ""
    try:
        held = _LOCAL.settings
        if held[0] == ini:
            return held[1]
    except AttributeError:
        pass
    s = QtCore.QSettings(ini, QtCore.QSettings.Format.IniFormat) if ini else QtCore.QSettings(ORG, APP)
    _LOCAL.settings = (ini, s)
    return s


#: every on / off option (``opt/<key>``) and its default. The Graphics
#: Builder's own options keep the defaults CtDraw registers for
#: them ([CtDraw.RSC] in citect.ini): DspAttNew 1, DspAttCopy 0, DspTmpWarn 1,
#: ModifyAN 0, DisableMacroForms 0, DspGroupButton 0, CompileEnquiry 0,
#: ListSystemPages 0, OldTools 0, XPButtonsDefault 1.
OPTION_DEFAULTS = {
    # the Graphics Builder's Tools > Options
    "props_on_new": True,
    "props_on_copy": False,
    "save_template_warning": True,
    "modify_an": False,
    "disable_genie_forms": False,
    "genie_group_button": False,
    "compile_enquiry": False,
    "list_system_pages": False,
    "show_legacy_tools": False,
    "xp_buttons": True,
    # ctgedit's own
    # a double-click on a text edits its words on the page (off: it opens the properties,
    # as the Graphics Builder does)
    "dblclick_edits_text": True,
    # an expression or command field colours its Cicode and underlines what is wrong or
    # not known (ui/objprops/cicode_assist)
    "colour_cicode": True,
    # the arguments of the function being typed show above the field
    "function_hints": True,
    "confirm_delete": False,
    "unlock_template": False,
    "developer_tools": False,
    "dyn_badges": False,
    "raw_labels": False,
    "auto_update_template": True,
    "auto_update_symbols": True,
    "auto_update_genies": True,
    # the bar over the canvas that lists what was updated by itself (open checks, a genie
    # saved in another tab), with its Undo; off: only the status bar says so
    "show_auto_changes": True,
    # a template or genie whose library does not open becomes a placeholder when a page
    # opens, as the Graphics Builder's load does (the page keeps its records when off)
    "auto_placeholder_missing": True,
    # the library search also looks in the other projects of the install (not the Graphics
    # Builder's: it looks in the page's project and the projects it includes only)
    "library_siblings": False,
    # text the code page cannot store is written as '?' (the Graphics Builder's way) instead
    # of being refused
    "text_question_marks": False,
    # a rotate or mirror turns a gradient with its shape (the Graphics Builder keeps it
    # pointing the old way)
    "turn_gradients": True,
    # a save writes a new fast display file (.ctF) when the old one is out of date (its
    # genies or libraries changed since), as the Graphics Builder does; off: it is removed
    # and the runtime draws the page from the .ctg
    "ctf_rebuild_stale": True,
    # a placed genie where only a genie or symbol nested in it changed is composed again in
    # full when its page opens, as the Graphics Builder does; off: only its nested copies
    "genie_nested_full": True,
    # four quarter turns give the selection back exactly (the Graphics Builder truncates a
    # half pixel at each turn, so it drifts)
    "reversible_turns": False,
    # a text resized unevenly keeps the stretch as a matrix, as the Graphics Builder does
    # (off: its point size follows the new height and its box is measured again)
    "stretch_texts": True,
    "warn_shipped_template": True,
    "save_supergenie_warning": True,
    # after a library is saved, the Graphics Builder is closed (ui/builder_close): it reads
    # the library again when it next opens; with a page open there it asks first
    "auto_close_builder": False,
    # Settings > Canvas: the mouse wheel scrolls the page (Ctrl + the wheel zooms); off: the
    # wheel zooms
    "wheel_scrolls": False,
    # Settings > Canvas: a touchpad's two fingers and other scroll steps smaller than one notch
    # pan, a pinch zooms (off, the default: they do what the wheel does; a smooth-scrolling
    # mouse and a Remote Desktop connection send such steps too, and nothing tells them from a
    # touchpad's: ui/editor/viewnav); the wheel zoom stops at 100 % for one notch; zoom changes
    # glide; a drag near the canvas edge scrolls it; the cross hairs show the position at the
    # pointer
    "touchpad_pans": False,
    "wheel_stop_at_100": True,
    "animate_zoom": True,
    "drag_autoscroll": True,
    "crosshair_readout": True,
    # a second process re-reads each opened page and compares what it draws (diag)
    # off: a second process per opened page (100-200 MB) for a check of ctgedit's own drawing;
    # Tools > Check Page Display runs it on demand
    "display_check": False,
    # Settings > Canvas: the grid is drawn outside the page too (the Graphics Builder dots
    # the whole window); off: on the page only
    "grid_outside_page": False,
    # Settings > Canvas: a soft shadow under the page, whatever is around it
    "page_shadow": True,
    # the Properties panel: an arrow key, the wheel or a spin arrow in a number field applies
    # after a short pause (one undo step for a run of steps); off: Enter applies
    "props_arrow_live": True,
    # the Properties panel: drag a number field's label sideways to change the value
    "props_scrub": True,
    # the canvas: Tab / Shift+Tab select the next / previous object (off: Tab
    # moves the keyboard to the next panel); a selection made in a panel (the Objects tree,
    # Goto Object, Find) scrolls the canvas to it; with Snap to Grid an arrow key moves to the
    # next grid line; with nothing selected the arrow keys scroll the page
    "tab_selects_objects": True,
    "reveal_selection": True,
    "arrow_grid": False,
    "arrows_pan": True,
    # Settings > Canvas > Underlay (ui/editor/underlay.py): a new underlay draws above the objects;
    # each page's underlay comes back when the page opens again; dragged objects snap to a
    # page underlay's objects
    "underlay_above": False,
    "underlay_remember": True,
    "underlay_snap": True,
    # Settings > Editing > Tools: a drawing tool, or a symbol or genie being placed, stays
    # chosen after each object (Esc or Select goes back); off: back to Select after each
    "keep_tool": True,
    # ui/prefs/window_options.py
    # a page opened from Explorer or a second start goes to the editor already running
    # (off: each start opens its own window); a page named on the command line also brings
    # the last pages back (with On start: Reopen the last pages)
    "single_instance": True,
    "explorer_session": False,
    # Settings > Pages and libraries > Files and folders: the editor window watches the
    # library folders of its open pages (ui/window/libwatch.py): a symbol, genie or template
    # saved by another program is drawn again, after the last change settled (ms)
    "library_watch": True,
    "library_watch_delay_ms": 1000,
    # ui/editor/view_consistency.py: the bare + and - keys zoom in the Bitmap
    # Editor and the magnifier (the Graphics Builder's keys; off: the editor's Ctrl+= and
    # Ctrl+- only, so - never zooms in one window and deletes in another)
    "bare_zoom_keys": True,
    # Settings > Advanced > Updates: the installed ctgedit looks for a newer version on GitHub
    # when it starts, at most once a day (ui/dialog/updates)
    "check_updates": True,
}


#: every other settings key ctgedit reads or writes by its name, with the default it is read
#: with (None: computed where it is read). Keys built at run time ('geometry/<class>',
#: 'columns/<window class>/<table>' = the column widths the user set in that table, one
#: per logical column then 'v<the view's width>', 'w0,w1,...,v432' (ui/tables.py),
#: 'guides/<page>', 'views/<page>', 'underlay/<page>' = the page's
#: underlay, JSON (ui/editor/underlay.py), 'updatepages/ticks/<root project, lower case>' = the
#: projects Update Pages left unticked for that root, JSON (ui/dialog/dialogs.UpdatePagesDialog),
#: 'pagebrowser/<purpose>/...',
#: 'view/last/<path>' = the zoom and place a page was left at, JSON, at most 200,
#: 'keys/<command id>' = the user's own keys of a command, a list of key strings, '' = no
#: key: ui/prefs/keymap) and the Simulate
#: window's options (ui/simulate/options.OPTIONS) are not listed. tests/test_settings_keys.py
#: fails on a key read or written by a name that is not here (or in OPTION_DEFAULTS as
#: 'opt/<name>'), or read with another default.
SETTINGS_KEYS = {
    # Arrange > Space Equally… and Arrange in Grid…: their last values
    "arrange/grid_by_rows": True,
    "arrange/grid_columns": 2,
    "arrange/grid_hgap": 10,
    "arrange/grid_vgap": 10,
    "arrange/space_axis": "h",
    "arrange/space_gap": 10,
    # Space Equally: a gap of so many pixels (else the room shared between the outer two)
    "arrange/space_fixed": False,
    "colour/adjust/hue_from": 0,
    "colour/adjust/hue_range": 0,
    "colour/adjust/hue_to": 0,
    "colour/adjust/light_selected": None,
    "colour/adjust/lightness": None,
    "colour/adjust/sat_selected": None,
    "colour/adjust/saturation": None,
    "colour/swap/any": False,
    "colour/swap/from": None,
    "colour/swap/range": False,
    "colour/swap/to": None,
    "goto/columns": None,
    # Find and Replace: its Options (Include, Search in, Match, Direction) shown
    "find/options_shown": False,
    # Rename Objects / Rename Pages: the last naming choices (JSON, NamingPanel)
    "rename/objects": None,
    "rename/pages": None,
    # File > Import: the recent and favourite pictures, the picture options' last choices
    "import/favourites": [],
    "import/recent": [],
    "import/size_mode": "own",
    "import/store_at_size": True,
    "import/threshold": 128,
    "import/tolerance": 0,
    "library/copy_preview": True,
    "library/paste_linked": True,
    "newpage/linked": True,
    "opt/autosave_minutes": None,
    "opt/codepage": "auto",
    "opt/composite_genies": "members",
    "opt/library_folders": "",
    "opt/open_zoom": "last",
    # Settings > Pages and libraries 'On start:' (doc_tabs.STARTUP)
    "opt/startup": "session",
    "opt/large_picture_kb": 1024,
    "opt/paste_drawing": "bitmap",
    "opt/paste_offset": 10,
    "opt/nudge_burst_ms": 200,
    # (the same review step however a picture arrives, a drop or a paste included)
    "opt/picture_options": "all",
    "opt/stacked_labels": "top",
    "opt/undo_mb": 384,
    # Settings > Editing > Safety: the earlier versions each save keeps of a page (0 = none)
    # and all of them together at most (MB): infra/history.py, File > Page History
    "opt/history_versions": 10,
    "opt/history_mb": 200,
    # Settings > Canvas: Undo and Redo select what they change and bring it into view
    "ui/undo_reveal": True,
    "opt/find_processes": 0,  # 0 = automatic (find_processes)
    # Tools > Update Pages: its helper processes (0 = automatic: update_parts), the pages a
    # helper gets at least, and the root project it last ran on (its name)
    "opt/update_processes": 0,
    "opt/update_pages_per_helper": 8,
    # the progress figures of Update Pages, its chain, Update Users and the chain after a
    # genie save (ui/jobs/run_stats): shown or not, and the estimate's 'enough data' (items done
    # AND seconds run) and its smoothing (s)
    "opt/run_figures": True,
    "opt/run_estimate_items": 3,
    "opt/run_estimate_seconds": 5,
    "opt/run_estimate_smoothing": 10,
    # how long a save waits for a project database or a page another ctgedit process is
    # writing (s): the editor's waits and the helper processes' (format/projectlock)
    "opt/wait_project": 10,
    "opt/wait_page": 30,
    "opt/wait_helpers": 120,
    "updatepages/root": "",
    # (the root's folder when it is no listed project: picked with Browse, or a loose page's)
    "updatepages/root_dir": "",
    # the box 'Save the library genies and templates in the chain first' (genieusage.
    # chain_plan): on by default, as the Graphics Builder's Update Pages saves an outer
    # template before the pages that use it
    "updatepages/chain": True,
    # its sub box 'Also compose their nested genies again where they differ from their
    # libraries' (genieusage.chain_plan compose_all): slow, so off by default
    "updatepages/chain_compose_all": False,
    "opt/genie_save_pages": "ask",
    # after a symbol save, what uses it (symbol_save_pages: ask / always / never)
    "opt/symbol_save_pages": "ask",
    # after a template save, the templates built on it and the closed pages that use it
    # (template_save_pages: ask / always / never)
    "opt/template_save_pages": "ask",
    "opt/disk_reload": "ask",
    "opt/shift_arrow": "resize",
    # the Properties panel: labels beside / above the fields ('auto': above under 300 px),
    # the folded sections (by title), the Graphics Builder's own notes ('!*' keys) under All
    # properties, the aspect lock of W / H
    "props/label_layout": "auto",
    "props/collapsed": ["All properties"],
    "props/show_private_keys": False,
    "props/lock_aspect": False,
    # Settings > Canvas: the canvas's navigation (ui/editor/viewnav.py, ui/editor/navbar.py)
    "opt/wheel_zoom_step": 15,  # percent per wheel notch
    "opt/zoom_steps": "levels",  # Zoom In / Out: 'levels' (round levels) | 'factor'
    "opt/middle_double_click": "fit_all",  # 'fit_all' | 'fit_page' | 'nothing'
    "opt/crosshair_style": "full",  # 'full' | 'short'
    "pagedefaults/background": None,
    "pagedefaults/resolution": None,
    "pagedefaults/style": None,
    "pagedefaults/title_bar": None,
    "dir/export": "",  # the folder of the last Export Page
    "export/png_scale": 100,  # Export Page's PNG scale (percent)
    "styles/list": "[]",  # the named styles (Edit > Styles): JSON [{name, appearance}]
    "sel/filter": 0,
    "sel/marquee": 0,
    "session/last_file": "",
    "session/open_files": [],
    # the session's tabs as JSON rows {path, element, transform, scroll, pinned}, and the
    # index of the current one (session/open_files says which files were open)
    "session/tabs": "",
    "session/current": -1,
    "session/pinned": [],
    # the template File > New used last for a page ('style|element|resolution|title bar')
    "pagebrowser/new/template": "",
    "sim/docked": "false",
    "simulate/dock_area": None,
    "simulate/dock_floating": "false",
    "simulate/dock_size": 0,
    "simulate/docked_state": None,
    "simulate/float_rect": "",
    "simulate/geometry": None,
    "simulate/log_details_height": 0,
    "simulate/not_simulated_details_height": 0,
    "simulate/state": None,
    # the point of a box the X / Y fields show (the Properties panel's reference point)
    "ui/geometry_reference": "top_left",
    "ui/palette_mru": [],
    # the command palette's tool box (S on the page): its commands as ids (unset: the
    # drawing tools; ui/dialog/palette.pins)
    "ui/palette_pins": None,
    # the manual: its text size (steps of 10 %) and the width of its contents pane
    "help/zoom": 0,
    "help/splitter": None,
    # Settings > Appearance (ui/style/theme.APPEARANCE_DEFAULTS: read there by name)
    "ui/accent": "blue",
    "ui/density": "comfortable",
    "ui/focus_ring": "keyboard",
    # Settings > Ribbon: the groups hidden ('Tab/Group') and the user's own group's commands
    "ui/ribbon_custom": [],
    "ui/ribbon_hidden_groups": [],
    "ui/search_regex": [],
    "ui/ribbon_collapsed": False,
    # the navigation bar's buttons ([] = all) and the canvas overlays' opacity (percent)
    "ui/navbar_buttons": [],
    "ui/overlay_opacity": 100,
    "ui/ribbon_tab": 0,
    # the rich tool tips of the command buttons (ui/style/tips.py); off = "Name (key)"
    "ui/rich_tooltips": True,
    # View > Show Tools: right-click > Show Labels (one column of icon and name)
    "ui/toolbox_labels": False,
    # the document tabs (doc_tabs.py): close buttons, compact pinned tabs, Ctrl+Tab order
    "ui/tab_close_button": "hover",
    "ui/pinned_tab_compact": True,
    "ui/ctrl_tab_order": "mru",
    "view/aliased_text": False,
    "view/crosshair": False,
    # the canvas: scroll bars 'auto' (thin, over the canvas) | 'always' | 'never'; touch
    # 'auto' | 'on' | 'off'; the navigation bar's place; the rulers; the navigator
    "view/canvas_scrollbars": "auto",
    "view/touch": "auto",
    "view/navbar_pos": "bottom_centre",
    "view/rulers": True,
    "view/navigator": True,
    "view/navigator_autohide": True,
    "view/navigator_collapsed": False,
    "view/grid": True,
    # Grid Setup / Settings > Canvas: a major grid line every N grid steps (0 = none), the
    # grid style ('lines' / 'dots'), the pixel grid at 800 % and more (View > Grid)
    "view/grid_major": 8,
    "view/grid_style": "lines",
    "view/pixel_grid": True,
    "view/grid_origin_x": 0,
    "view/grid_origin_y": 0,
    "view/grid_size": 8,
    "view/grid_y": 0,
    # Settings > Canvas: the page edge ('dotted' as the Graphics Builder / 'line' / 'none')
    # and the colour of the canvas's selection outlines ('accent' or '#rrggbb')
    "view/page_edge": "dotted",
    "view/selection_colour": "accent",
    "view/guides_visible": True,
    "view/label_mode": "literal",
    "view/nudge_step": 1,
    # Settings > Canvas: the largest zoom a page / a library element opens at (%; '0' = no
    # limit; doc_tabs.OPEN_ZOOM_MAX)
    "view/open_zoom_max": "200",
    "view/open_zoom_max_element": "400",
    "view/pane_labels": True,
    "view/selection_hud": True,
    "view/slow_keys": False,
    "view/slow_mouse": False,
    "view/smart_guide_outline": True,
    "view/smart_guide_tol": None,
    "view/smart_guides": True,
    "view/snap": True,
    "view/snap_guides": True,
    "view/status_bar": True,
    "view/surround": None,
    "view/thumb": False,
    "view/toolbox": False,
    "view/toolbox_geometry": None,
    "view/zoom_window_factor": 4,
    "win/geometry": None,
    "win/state": None,
    # Settings > Keyboard: the keymap preset ('ctgedit' | 'builder' | 'design')
    "ui/prefs/keymap": "ctgedit",
    # the first run's status hint naming Ctrl+K and Ctrl+/ was shown
    "ui/keys_hint_shown": False,
    # Settings: each change applies at once (Close / Revert to Opening Values); off: OK / Cancel / Apply
    "ui/settings_live": True,
    # the last check for a newer ctgedit (seconds since 1970) and the version the user said to
    # skip (ui/dialog/updates)
    "updates/last_check": 0.0,
    "updates/skip_version": "",
    # Settings > Appearance: the canvas's selection handles, the panels' tab position and
    # title bars (ui/panels/docking.apply_settings), the Objects panel's eye and padlock
    "view/handle_size": "medium",  # small / medium / large
    "view/guide_width": "thin",  # thin / medium / thick (1, 2, 3 px)
    "view/guide_reach": "near",  # near / medium / far (3, 6, 10 px)
    "view/outline_width": "thin",  # thin / medium / thick (1, 2, 3 px)
    "ui/dock_tab_position": "top",  # top / bottom
    "ui/compact_dock_titles": True,
    "ui/tree_eye": "always",  # always / hover
    # how long messages stay (ui/notify.py), the results over the page (ui/toast),
    # the taskbar button's progress (ui/jobs/taskbar), the status bar's fields ('+name' / '-name':
    # ui/panels/status_items), a page changed on disk told in its notice bar or asked in a box
    "ui/message_time": "normal",  # short / normal / long
    "ui/toasts": True,
    "ui/taskbar_progress": True,
    "ui/statusbar_items": [],
    "ui/disk_change_prompt": "bar",  # bar / dialog
    # Settings > Appearance > Objects panel (ui/panels/objects_dock.py): the rows' labels ('smart' =
    # the name, element and tag first; 'classic' = the kind and AN first), the AN column,
    # the side of the eye and padlock; the panel's own columns and filters
    "objects/row_label": "smart",
    "objects/show_an": True,
    "objects/toggles_side": "right",  # right / left
    "objects/columns": ["object"],
    "objects/filter": [],
    # the canvas's selecting and transforming: Ctrl + arrow keys ('move' as the
    # arrow keys, 'x10' one large step, 'grid' one grid pitch); Tab's order ('reading' / 'drawing');
    # the box with handles around several selected objects; the selection box's live preview;
    # a plain drag on a large object starts a selection box; the modifier keys of drawing,
    # resizing and moving ('builder' / 'design'); a click on one of several selected objects
    # selects only it; the key that makes a drag copy ('ctrl' / 'alt'); Duplicate repeats the
    # offset its copy was moved by; Ctrl+Shift + drag with the Select tool ('zoom' a zoom box,
    # 'touch' a Touch selection box from anywhere)
    "opt/ctrl_arrow": "move",
    "sel/tab_order": "reading",
    "view/selection_box": True,
    "canvas/box_preview": True,
    "canvas/box_from_background": False,
    "canvas/modifier_scheme": "builder",
    "canvas/click_selected_isolates": False,
    "canvas/drag_copy_key": "ctrl",
    "edit/smart_duplicate": True,
    # Replace Genie > Line up by: the top-left corner (else the hot spot)
    "edit/replace_genie_topleft": False,
    # Insert ActiveX Control: the control inserted last (its CLSID), selected next time
    "edit/last_activex": "",
    # a run's result window (Update Pages, Pack Libraries ...): its details shown last time
    "edit/run_details": False,
    "canvas/ctrl_shift_drag": "zoom",
    # the look of new objects (ui/editor/draw_styles): a style per tool (JSON {tool: {field:
    # value}}, '{}' = the factory look), the last used look per kind, which of the two new
    # objects take ('tool' | 'last'), the ribbon's fill / line / text colours (packed
    # 0x00RRGGBB; 'auto' contrasts with the page until a colour is picked: 'fixed'), new
    # shapes hollow
    "draw/style": "{}",
    "draw/last_style": "{}",
    "opt/new_object_style": "tool",
    "draw/fill_colour": 0xC0C0C0,
    "draw/line_colour": 0x202020,
    "draw/text_colour": 0x202020,
    "draw/colour_mode": "auto",
    "draw/no_fill": False,
    # the Properties panel: several objects' X / Y / W / H apply to the selection box or to
    # each object; the property search keeps its text when the selection changes
    "props/multi_apply": "selection",
    "props/search_keep": True,
    # a JSON file of styles shared by a team (Edit > Styles lists them read-only)
    "styles/shared_file": "",
    # Settings > Canvas > Underlay (ui/editor/underlay.py): a new underlay's opacity (percent) and
    # tint ('none' / 'grey' / 'blue'), the resolution a page underlay is drawn at (1, 2 or 4
    # picture pixels a page pixel), the folder of the last picture underlaid
    "opt/underlay_opacity": 40,
    "opt/underlay_tint": "grey",
    "opt/underlay_resolution": 2,
    "dir/underlay": "",
    # ui/editor/canvas_options.apply_canvas_settings: how near the pointer
    # grabs a handle or node ('near' / 'medium' / 'far'), how far it moves before a drag starts
    # ('short' / 'medium' / 'long'), the mouse's back and forward buttons ('view' / 'tabs' /
    # 'nothing'), a double-click where no object is ('nothing' / 'page_props'), the hover
    # outline, the guideline snap ('near' / 'normal' / 'far') and colour ('default' or
    # '#rrggbb'), the largest zoom of Zoom to Selection and the like (%, '0' = no limit), the
    # edge scroll speed ('slow' / 'normal' / 'fast'), the navigator's size and corner, Esc
    # while drawing point by point ('cancel' / 'finish'), Mouse Slow Speed's gearing ('half' /
    # 'quarter' / 'tenth'), Enter in a text ('newline' / 'store')
    "canvas/pointer_reach": "near",
    "canvas/drag_start": "short",
    "opt/mouse_back_forward": "view",
    "opt/page_double_click": "nothing",
    "view/hover_outline": True,
    "view/guide_snap": "normal",
    "view/guide_colour": "default",
    "view/frame_zoom_max": "400",
    "opt/edge_scroll_speed": "normal",
    "view/navigator_size": "medium",
    "view/navigator_corner": "bottom_right",
    "opt/esc_while_drawing": "cancel",
    "view/slow_mouse_factor": "quarter",
    "opt/text_enter": "newline",
    # Settings > Editing: the steps of the large arrow key move (Ctrl / Shift + arrow); the
    # part of the view an arrow key scrolls with nothing selected (16, 8, 4: a sixteenth, an
    # eighth, a quarter); what Align lines the objects up with ('selection' / 'last' /
    # 'page'); where a placed symbol or genie lands ('top_left' / 'centre'); the backups of
    # batch changes kept (the newest, and days; 0 = no age limit: infra/backups) and the
    # least time between two earlier versions of a page (minutes; 0 = every save:
    # infra/history)
    "opt/big_nudge": 10,
    "opt/arrows_pan_step": 8,
    "opt/align_to": "selection",
    "opt/place_anchor": "top_left",
    "opt/backup_keep": 10,
    "opt/backup_days": 30,
    "opt/history_every_min": 0,
    # Settings > Canvas > Copy and paste: Paste puts the objects next to the copied ones
    # ('offset') or at the pointer ('pointer'); Display properties on copy opens at most so
    # many windows (0 = one per object, as the Graphics Builder)
    "canvas/paste_at": "offset",
    "opt/copy_sheets_max": 7,
    # Settings > Pages and libraries: the recent pages kept; File > Print starts in
    # 'landscape', 'portrait', 'page' (the page's shape) or 'last' (the orientation of the
    # last print, opt/print_last_orientation)
    "opt/mru_max": 16,
    "opt/print_orientation": "landscape",
    "opt/print_last_orientation": "landscape",
    # ui/prefs/window_options.py: where a new tab goes ('end' / 'next'),
    # the tab shown after a close ('next' / 'last' = the page used before), the question
    # before a pinned tab closes, the tabs' width ('narrow' / 'normal' / 'wide'), the
    # contextual ribbon tabs open on a canvas selection, the commands (ids) of the Quick
    # Access row, the right-click menu's quick row ([] = none) and the selection toolbar,
    # the command palette's last search, Quick Open's first scope ('project' / 'install' /
    # 'last': the one used last, ui/quick_open_last), the name of new pages, when File >
    # Compile shows its results ('always' / 'problems' / 'errors'), the animation points a
    # page has before the status bar warns
    "ui/new_tab_place": "end",
    "ui/after_close": "next",
    "ui/ask_unpin": True,
    "ui/tab_width": "normal",
    "ui/context_tabs_open": False,
    "ui/qat": ["act_new", "act_open", "act_save", "act_undo", "act_redo"],
    "ui/menu_quick_row": ["align.left", "align.hcentre", "align.right", "act_group", "act_front", "act_back"],
    "ui/hud_buttons": ["act_obj_props", "act_group", "act_front", "act_back", "act_del"],
    "ui/palette_keep_search": False,
    "ui/quick_open_scope": "project",
    "ui/quick_open_last": "project",
    "opt/new_page_name": "NewPage",
    "opt/compile_results": "errors",
    "opt/an_warn": 3000,
    # ui/panels/panel_options.py reads them when a window is made and when
    # the Settings window changes one; the Objects panel's in ObjectsDock._read_view_settings.
    # The Properties panel's label drag speed ('slow' / 'normal' / 'fast': 1 per 4, 2 or 1 px);
    # the Objects panel: a slow second click renames, a row's double-click ('zoom' / 'scroll'
    # / 'props' / 'rename'), the tree follows a selection made elsewhere, its filter comes
    # back at a start; the Library panel's card preview ('off' / 'normal' / 'large'); the
    # letters in order matches of the searches; the page browser's folded name start, its
    # preview pane ('auto' / 'always' / 'never') and what it shows ('thumbnail' / 'live');
    # pointing at a style previews it; the letters before an expression field suggests names
    # (0 = never); the tab Object Properties opens on ('first' / 'last')
    "opt/scrub_speed": "normal",
    "objects/rename_click": True,
    "objects/double_click": "zoom",
    "objects/follow_selection": True,
    "objects/keep_filter": True,
    "library/hover_preview": "normal",
    "browser/fuzzy": True,
    "browser/short_names": True,
    "browser/preview_pane": "auto",
    "browser/preview_live": "thumbnail",
    "draw/style_preview": True,
    "opt/tag_suggest": 2,
    "objprops/open_tab": "first",
    # Settings > Pages and libraries > When a page
    # opens: an automatic update of an open tab (a changed template, symbol or genie, the
    # chain's reload) waits this long for the user's steps to redo, which it would clear
    # (s, from its first wait; 0: it does not wait; ui/window/open_genie_updates.redo_wait_s)
    "opt/auto_update_redo_wait_s": 60,
    # what is open comes first: a page or genie open in a tab takes a
    # saved genie, symbol or template at once, also while the Genie Saved question is up and
    # while the update of what uses it runs, and each library file that update writes
    # (ui/window/libwatch; a Simulate page that follows the tab with it, ui/simulate/
    # live_reload.run_holds_documents). On: it waits for that update's end, which composes
    # it once
    "opt/tabs_wait_for_runs": False,
    # the order the open tabs take a saved genie in after the tab shown (and the tabs shown
    # since): 'near' = the element tabs nearest the saved element, then the pages, smaller
    # ones first; 'tabs' = tab order (ui/jobs/genie_jobs.GenieJobs.order_key)
    "opt/genie_jobs_order": "near",
    # how many genie helpers update the open tabs at a time, the window's helper the first
    # (ui/jobs/genie_jobs.GenieJobs._free_worker; one more starts only while free memory is not
    # below Settings > Long jobs 'Stop it sooner when free memory is below')
    "opt/genie_helpers": 3,
    # ui/prefs/job_options.py reads them at a start and when Settings changes one:
    # Settings > Pages and libraries > Long jobs. A task that ran longer than this says how
    # it ended in the messages (s; ui/jobs/tasks.LONG_S); how often a helper process's progress
    # is read (ms; ui/jobs/helper_job, the compile and the display check); the genie helper ends
    # when idle this long (s; ui/jobs/genie_jobs.WarmWorker), sooner when free memory is below
    # (MB); a File > Open live preview is given up after (s; ui/browser/preview); the
    # Simulate window waits for the page model helper (s; ui/simulate/modeljob); a slow step
    # shows its busy note after (ms; ui/window/files._busy); the finished note of the update
    # after a genie save stays in the status bar for (s, times Messages stay; 0: until
    # closed; ui/window/library._start_users_run); Find in closed pages: the pages each
    # helper gets at least
    "opt/task_report_s": 5,
    "opt/job_poll_ms": 300,
    "opt/worker_idle_s": 300,
    "opt/worker_low_memory_mb": 1500,
    "opt/preview_timeout_s": 120,
    "opt/model_wait_s": 120,
    "opt/busy_note_ms": 400,
    "opt/users_note_s": 60,
    # the search for what uses an element (after a library save, Show Users, Used By, Update
    # Users) shows its window only when it takes longer than this (ms; 0: at once); its
    # progress is in the status bar meanwhile (ui/window/library._element_users_read)
    "opt/users_read_window_ms": 2000,
    "opt/find_pages_per_helper": 8,
    # the save of a library element of at least this many
    # records normalises them in a helper process of the window while the window stays
    # alive (0: never; ui/dialog/element_save), and its progress window shows only after this
    # (ms; 0: at once; the status bar until then). Read at each save
    "opt/save_helper_records": 1000,
    "opt/save_window_ms": 10000,
    # ui/editor/view_consistency.py applies them: the page px
    # Fit Page keeps around the page (the editor's canvas and the Simulate page alike:
    # ui/editor/viewnav.fit_zoom), a double-click on the navigator or the ruler corner ('fit_page' /
    # 'fit_all' / 'nothing': viewnav.corner_double_click), the key that puts the manual's
    # text size back ('ctrl0' as a browser / 'ctrl1' as View > Zoom 100%), the px a notch of
    # Ctrl + the wheel sizes thumbnail cards by (the Library panel, Open and the pickers, the
    # template picker: ui/browser/thumbs.card_wheel_delta), the template picker's card width
    # (None: thumbs.CARD_SIZE)
    "view/fit_margin": 40,
    "opt/corner_double_click": "fit_page",
    "help/zoom_reset_key": "ctrl0",
    "opt/card_wheel_step": 8,
    "pagebrowser/template/card_size": None,
    # ui/prefs/timing_options.py reads them into module values: the pause
    # before an expression is checked while
    # typing (ms); the pause after an arrow step in a number field of the Properties panel
    # (ms); the pause a second click waits before it renames a row of the Objects panel (ms);
    # the pause in editing after which a recovery copy is written (s; 0 = only by the
    # minutes); the Undo / Redo flash (ms); the selection toolbar's settle pause (ms); the
    # distance the pointer fades the navigation bar within (px) and how much of it still
    # shows while faded (percent); the rows Quick Open and the
    # command palette list at most; the pages whose last view and whose underlay are
    # remembered; the saved views a page keeps; the Library panel's recent elements; the
    # Settings window's own live-apply pause (ms)
    "opt/expression_check_ms": 700,
    "opt/props_step_pause_ms": 350,
    "objects/rename_click_ms": 600,
    "opt/autosave_idle_s": 10,
    "view/undo_flash_ms": 400,
    "ui/hud_delay_ms": 250,
    "ui/navbar_fade_px": 40,
    "ui/navbar_faded_pct": 35,
    "ui/quick_open_rows": 40,
    "ui/palette_rows": 40,
    "view/last_views_kept": 200,
    # embedded controls draw from a picture the control host takes (render/control_pictures)
    "view/control_pictures": True,
    "opt/underlays_kept": 200,
    "view/saved_views_max": 40,
    "library/recent_kept": 40,
    "ui/settings_live_delay_ms": 150,
    # Settings > Appearance > Tables (ui/tables.SETTINGS, read into its module values by
    # tables.read_settings): the most a fit gives a column (px); in a panel that fits its
    # width, the most a fitted column gets and the least the main column keeps (percent of
    # the panel); the least a main column and a squeezed column get (px); the rows a fit
    # measures. Settings > Appearance > Tags panel (ui/panels/tags_dock._fit_columns): the Type
    # column's most, the Tag and Equipment columns' shares of the rest (percent), the least
    # the Tag column and the other columns get (px)
    "tables/max_fit_width": 360,
    "tables/fit_share": 35,
    "tables/main_share": 33,
    "tables/min_main_width": 60,
    "tables/min_squeezed_width": 36,
    "tables/fit_sample": 1000,
    "tags/type_max_width": 80,
    "tags/name_share": 42,
    "tags/equipment_share": 29,
    "tags/name_min_width": 90,
    "tags/min_column_width": 60,
}

#: Settings > Export… / Import…: the group of each key, by its longest matching prefix
#: (tests/test_settings_keys.py: every declared key has one). GROUP_NAMES is their order.
GROUP_NAMES = (
    "Options",
    "Keyboard shortcuts",
    "Ribbon and Quick Access",
    "Window layout and workspaces",
    "Styles",
    "Favourite colours",
    "Recent pages and folders",
    "Guidelines and grid",
)
KEY_GROUPS = (
    ("opt/", "Options"),
    ("view/", "Options"),
    ("ui/", "Options"),
    ("arrange/", "Options"),
    ("colour/", "Options"),
    ("draw/", "Options"),
    ("export/", "Options"),
    ("library/", "Options"),
    ("newpage/", "Options"),
    ("pagedefaults/", "Options"),
    ("sel/", "Options"),
    ("sim/", "Options"),
    ("goto/", "Options"),
    ("find/", "Options"),
    ("scope/", "Options"),
    ("remember/", "Options"),
    ("rename/", "Options"),
    ("import/", "Options"),
    ("objects/", "Options"),
    ("tags/", "Options"),
    ("checks/", "Options"),
    ("browser/", "Options"),
    ("pagebrowser/", "Options"),
    ("help/", "Options"),
    ("props/", "Options"),
    ("canvas/", "Options"),
    ("edit/", "Options"),
    ("updates/", "Options"),
    ("keys/", "Keyboard shortcuts"),
    ("ui/prefs/keymap", "Keyboard shortcuts"),
    ("ui/ribbon_custom", "Ribbon and Quick Access"),
    ("ui/ribbon_hidden_groups", "Ribbon and Quick Access"),
    ("ui/qat", "Ribbon and Quick Access"),
    ("ui/palette_", "Ribbon and Quick Access"),
    ("win/", "Window layout and workspaces"),
    ("geometry/", "Window layout and workspaces"),
    ("columns/", "Window layout and workspaces"),
    ("simulate/", "Window layout and workspaces"),
    ("sim/docked", "Window layout and workspaces"),
    ("view/toolbox", "Window layout and workspaces"),
    ("ui/ribbon_collapsed", "Window layout and workspaces"),
    ("ui/statusbar_items", "Window layout and workspaces"),
    ("ui/ribbon_tab", "Window layout and workspaces"),
    ("help/splitter", "Window layout and workspaces"),
    ("ui/autohide/", "Window layout and workspaces"),
    ("workspaces/", "Window layout and workspaces"),
    ("ui/workspace", "Window layout and workspaces"),
    ("styles/", "Styles"),
    ("colour/favourites", "Favourite colours"),
    ("mru", "Recent pages and folders"),
    ("dir/", "Recent pages and folders"),
    ("import/recent", "Recent pages and folders"),
    ("import/favourites", "Recent pages and folders"),
    ("session/", "Recent pages and folders"),
    ("guides/", "Guidelines and grid"),
    ("view/grid", "Guidelines and grid"),
    ("view/snap", "Guidelines and grid"),
    ("view/guides_visible", "Guidelines and grid"),
    ("view/smart_guide", "Guidelines and grid"),
    ("views/", "Guidelines and grid"),
    ("view/last/", "Guidelines and grid"),
    ("underlay/", "Guidelines and grid"),
    # the command lists, as the Quick Access row's
    ("ui/menu_quick_row", "Ribbon and Quick Access"),
    ("ui/hud_buttons", "Ribbon and Quick Access"),
    ("objprops/", "Options"),
    # Tools > Update Pages: the root project and the ticks per root
    ("updatepages/", "Options"),
    ("tables/", "Options"),
)


def key_group(key: str, fallback: Optional[str] = "Options") -> Optional[str]:
    """The Export / Import group of the settings key ``key`` (its longest prefix in
    :data:`KEY_GROUPS`); ``fallback`` when none matches."""
    best, group = -1, fallback
    for prefix, g in KEY_GROUPS:
        if key.startswith(prefix) and len(prefix) > best:
            best, group = len(prefix), g
    return group


def option(key: str, default: Optional[bool] = None) -> bool:
    """The on / off option ``opt/<key>`` (its :data:`OPTION_DEFAULTS` default
    when it was never set)."""
    if default is None:
        default = OPTION_DEFAULTS.get(key, False)
    try:
        return stored_bool(app_settings().value("opt/" + key, default), default)
    except Exception:  # noqa: BLE001  # swallow-ok: a garbled value reads as the default
        return bool(default)


def stored_bool(value, default: bool) -> bool:
    """A stored on / off value: a boolean, or true / false / 1 / 0 in any case; ``default``
    for anything else (a garbled value reads as the default, not as on)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1"):
            return True
        if v in ("false", "0"):
            return False
    return bool(default)


def set_option(key: str, on: bool) -> None:
    app_settings().setValue("opt/" + key, bool(on))


#: Settings > Canvas: how far a paste or a duplicate lands from the original (px; 0 = on it)
PASTE_OFFSET_RANGE = (0, 100)


def paste_offset() -> int:
    """opt/paste_offset (10 px, CtDraw's own step, when unset or garbled)."""
    lo, hi = PASTE_OFFSET_RANGE
    try:
        # (as text: Qt reads a garbled number as 0, not as an error)
        v = int(str(app_settings().value("opt/paste_offset", 10)).strip())
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return 10
    return max(lo, min(hi, v))


#: Settings > Editing: arrow-key presses that come closer together than this reach the page
#: as one move and one undo step (ms; 0 = each press is a move and an undo step of its own)
NUDGE_BURST_RANGE = (0, 2000)
NUDGE_BURST_DEFAULT = 200


def nudge_burst_ms() -> int:
    """opt/nudge_burst_ms (200 ms when unset or garbled)."""
    lo, hi = NUDGE_BURST_RANGE
    try:
        v = int(str(app_settings().value("opt/nudge_burst_ms", NUDGE_BURST_DEFAULT)).strip())
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return NUDGE_BURST_DEFAULT
    return max(lo, min(hi, v))


#: Settings > Canvas: stacked labels (identical labels at one place, each gated by its own
#: visibility): only the topmost copy, or every copy as the Graphics Builder draws them
STACKED_LABELS = (("top", "Only the top copy"), ("all", "Every copy (as the Graphics Builder)"))
#: Settings > Canvas: what a composite genie is drawn from
COMPOSITE_GENIES = (("members", "Its own objects (as the runtime)"), ("cache", "Its composition cache"))


def choice(key: str, choices, default: str) -> str:
    """A stored choice of ``choices`` ((value, label) pairs); ``default`` when it is unset
    or not one of them."""
    try:
        v = str(app_settings().value(key, default, type=str) or default)
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return default
    return v if v in dict(choices) else default


#: Settings > Canvas: the zoom a page opens at ('last': the zoom and place it was left at,
#: view/last/<path>; a page never opened, or of another size now, is fitted)
OPEN_ZOOM = (("last", "Where I left it"), ("fit", "Fit the page in the window"), ("100", "100 %"))
OPEN_ZOOM_DEFAULT = "last"

#: Settings > Pages and libraries: the helper processes that search the closed pages at
#: once (Edit > Find / Replace, all pages of the project); 0 = automatic
FIND_PROCESSES_RANGE = (0, 16)


def find_processes() -> int:
    """How many helper processes search the closed pages at once: the setting, else (0,
    automatic) half the processors, at most 4 (each takes about HELPER_MB while it runs)."""
    lo, hi = FIND_PROCESSES_RANGE
    try:
        # (as text: Qt reads a garbled number as 0, not as an error)
        v = int(str(app_settings().value("opt/find_processes", 0)).strip())
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as automatic
        v = 0
    v = max(lo, min(hi, v))
    if v == 0:
        v = max(1, min(4, (os.cpu_count() or 2) // 2))
    return v


#: Settings > Pages and libraries > Update Pages: the helper processes that update the
#: pages at once (0 = automatic), the memory one helper is counted with when Automatic
#: chooses (the tool tips say it too), and the pages each one gets at least
UPDATE_PROCESSES_RANGE = (0, 16)
HELPER_MB = 400
UPDATE_PER_HELPER_RANGE = (1, 1000)
#: Settings > Pages and libraries > Busy projects: the waits in seconds
LOCK_WAIT_RANGE = (1, 600)


def update_processes() -> int:
    """The Update Pages helper processes setting as stored (0 = automatic; a garbled value
    reads as 0; clamped to UPDATE_PROCESSES_RANGE)."""
    lo, hi = UPDATE_PROCESSES_RANGE
    try:
        # (as text: Qt reads a garbled number as 0, not as an error)
        v = int(str(app_settings().value("opt/update_processes", 0)).strip())
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as automatic
        v = 0
    return max(lo, min(hi, v))


def update_parts(pages: int, chosen: Optional[int] = None) -> int:
    """How many helper processes update ``pages`` pages at once."""
    lo, hi = UPDATE_PROCESSES_RANGE
    v = update_processes() if chosen is None else max(lo, min(hi, int(chosen)))
    if v == 0:
        v = max(1, min(4, (os.cpu_count() or 2) // 2))
        from ...infra.workers import free_memory_mb

        free = free_memory_mb()
        if free is not None:
            v = min(v, max(1, int(free) // HELPER_MB))
    per = whole("opt/update_pages_per_helper", UPDATE_PER_HELPER_RANGE)
    return max(1, min(v, int(pages) // per))


#: Settings > Pages and libraries > Progress figures: the estimate's items and seconds before
#: it shows, its smoothing time (s)
RUN_ESTIMATE_ITEMS_RANGE = (1, 1000)
RUN_ESTIMATE_SECONDS_RANGE = (0, 600)
RUN_ESTIMATE_SMOOTHING_RANGE = (0, 600)


def run_figures() -> tuple:
    """(shown, items, seconds, smoothing): whether the long runs show their progress
    figures, how many items they must have done and how many seconds they must have run
    before the estimate of the time left shows, and its smoothing time (ui/jobs/run_stats)."""
    try:
        show = stored_bool(app_settings().value("opt/run_figures", True), True)
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        show = True
    return (
        show,
        whole("opt/run_estimate_items", RUN_ESTIMATE_ITEMS_RANGE),
        whole("opt/run_estimate_seconds", RUN_ESTIMATE_SECONDS_RANGE),
        whole("opt/run_estimate_smoothing", RUN_ESTIMATE_SMOOTHING_RANGE),
    )


def lock_waits() -> tuple:
    """(project, page, helpers): how long a save in the editor waits for a project
    database and for a page another ctgedit process is writing, and how long a helper
    process waits for either (seconds, Settings > Pages and libraries > Busy projects)."""
    return tuple(whole(k, LOCK_WAIT_RANGE) for k in ("opt/wait_project", "opt/wait_page", "opt/wait_helpers"))


def apply_lock_waits() -> None:
    """Put :func:`lock_waits` into effect in this process (format/projectlock, the
    composition cache's wait: the project's) and for the helper processes it starts from
    now on (CTG_HELPER_WAIT: genie_worker.helper_waits)."""
    from ...format import composer_store, projectlock

    project, page, helpers = lock_waits()
    projectlock.set_waits(project, page)
    composer_store.set_lock_wait(project)
    os.environ["CTG_HELPER_WAIT"] = str(helpers)


#: Settings > Editing: the undo history all open pages keep together (MB)
UNDO_MB_RANGE = (64, 4096)


def undo_mb() -> int:
    lo, hi = UNDO_MB_RANGE
    try:
        # (as text: Qt reads a garbled number as 0, not as an error)
        v = int(str(app_settings().value("opt/undo_mb", 384)).strip())
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return 384
    return max(lo, min(hi, v))


def apply_undo_budget() -> None:
    """The undo memory of Settings > Editing for all open pages together; one page keeps at
    most two thirds of it (editing.UNDO_BYTES_TOTAL, editing._undo_page_cap)."""
    from ... import editing

    editing.UNDO_BYTES_TOTAL = undo_mb() * 1024 * 1024


# -- editing ------------------------------------------------------------------------------
#: Settings > Editing > Moving and resizing 'Large arrow key step:' (steps)
BIG_NUDGE_RANGE = (2, 100)
#: 'Arrow keys scroll by:' (the view's width or height divided by it)
ARROWS_PAN_STEPS = (16, 8, 4)
#: Settings > Editing > Arrange 'Align to:'
ALIGN_TO = ("selection", "last", "page")
#: Settings > Editing > New objects 'A placed symbol or genie lands with:'
PLACE_ANCHORS = ("top_left", "centre")
#: Settings > Canvas > Copy and paste 'Paste puts the objects:', 'Pasted objects whose properties open, at
#: most:' (0 = no limit)
PASTE_AT = ("offset", "pointer")
COPY_SHEETS_RANGE = (0, 100)
#: Settings > Editing > Safety: the backups of batch changes, the earlier versions' gap
BACKUP_KEEP_RANGE = (3, 100)
BACKUP_DAYS_RANGE = (0, 365)
HISTORY_EVERY_RANGE = (0, 120)
#: Settings > Pages and libraries 'Recent pages kept:', 'Print pages in:'
MRU_RANGE = (4, 50)
PRINT_ORIENTATIONS = ("landscape", "portrait", "page", "last")
#: the whole numbers above and their spans (the Settings window shows a stored number outside
#: its span as the default, as whole() reads it)
WHOLE_SPANS = {
    "opt/big_nudge": BIG_NUDGE_RANGE,
    "opt/copy_sheets_max": COPY_SHEETS_RANGE,
    "opt/backup_keep": BACKUP_KEEP_RANGE,
    "opt/backup_days": BACKUP_DAYS_RANGE,
    "opt/history_every_min": HISTORY_EVERY_RANGE,
    "opt/mru_max": MRU_RANGE,
    "opt/update_pages_per_helper": UPDATE_PER_HELPER_RANGE,
    "opt/run_estimate_items": RUN_ESTIMATE_ITEMS_RANGE,
    "opt/run_estimate_seconds": RUN_ESTIMATE_SECONDS_RANGE,
    "opt/run_estimate_smoothing": RUN_ESTIMATE_SMOOTHING_RANGE,
    "opt/wait_project": LOCK_WAIT_RANGE,
    "opt/wait_page": LOCK_WAIT_RANGE,
    "opt/wait_helpers": LOCK_WAIT_RANGE,
    # (Settings > Pages and libraries > When a page opens)
    "opt/auto_update_redo_wait_s": (0, 3600),
    # Settings > Pages and libraries > Long jobs; ui/prefs/job_options
    "opt/task_report_s": (0, 600),
    "opt/job_poll_ms": (50, 5000),
    "opt/worker_idle_s": (10, 3600),
    "opt/worker_low_memory_mb": (0, 65536),
    "opt/preview_timeout_s": (10, 3600),
    "opt/model_wait_s": (10, 3600),
    "opt/busy_note_ms": (0, 10000),
    "opt/users_note_s": (0, 3600),
    "opt/users_read_window_ms": (0, 60000),
    "opt/find_pages_per_helper": (1, 1000),
    # ui/dialog/element_save
    "opt/save_helper_records": (0, 1000000),
    "opt/save_window_ms": (0, 60000),
    # ui/tables.SETTINGS, ui/panels/tags_dock.COLUMN_SPANS
    "tables/max_fit_width": (100, 2000),
    "tables/fit_share": (10, 90),
    "tables/main_share": (10, 80),
    "tables/min_main_width": (20, 400),
    "tables/min_squeezed_width": (16, 200),
    "tables/fit_sample": (100, 100000),
    "tags/type_max_width": (40, 400),
    "tags/name_share": (10, 80),
    "tags/equipment_share": (10, 80),
    "tags/name_min_width": (24, 400),
    "tags/min_column_width": (24, 400),
}


# -- the spans of the timings and counts declared at the end of SETTINGS_KEYS
EXPRESSION_CHECK_RANGE = (200, 3000)
PROPS_STEP_PAUSE_RANGE = (100, 2000)
RENAME_CLICK_RANGE = (300, 2000)
#: Autosave: 'Also after a pause in editing of:' (s; 0 = only by the minutes) and the minutes
#: box's span (0 = off)
AUTOSAVE_IDLE_RANGE = (0, 600)
AUTOSAVE_MINUTES_RANGE = (0, 1440)
UNDO_FLASH_RANGE = (200, 3000)
HUD_DELAY_RANGE = (0, 2000)
NAVBAR_FADE_RANGE = (0, 200)
NAVBAR_FADED_RANGE = (10, 90)
RESULT_ROWS_RANGE = (10, 500)
REMEMBERED_PAGES_RANGE = (50, 5000)
SAVED_VIEWS_RANGE = (10, 500)
RECENT_ELEMENTS_RANGE = (10, 500)
SETTINGS_LIVE_DELAY_RANGE = (100, 2000)
WHOLE_SPANS.update(
    {
        "opt/expression_check_ms": EXPRESSION_CHECK_RANGE,
        "opt/props_step_pause_ms": PROPS_STEP_PAUSE_RANGE,
        "objects/rename_click_ms": RENAME_CLICK_RANGE,
        "opt/autosave_idle_s": AUTOSAVE_IDLE_RANGE,
        "view/undo_flash_ms": UNDO_FLASH_RANGE,
        "ui/hud_delay_ms": HUD_DELAY_RANGE,
        "ui/navbar_fade_px": NAVBAR_FADE_RANGE,
        "ui/navbar_faded_pct": NAVBAR_FADED_RANGE,
        "ui/quick_open_rows": RESULT_ROWS_RANGE,
        "ui/palette_rows": RESULT_ROWS_RANGE,
        "view/last_views_kept": REMEMBERED_PAGES_RANGE,
        "opt/underlays_kept": REMEMBERED_PAGES_RANGE,
        "view/saved_views_max": SAVED_VIEWS_RANGE,
        "library/recent_kept": RECENT_ELEMENTS_RANGE,
        "ui/settings_live_delay_ms": SETTINGS_LIVE_DELAY_RANGE,
    }
)


def whole(key: str, span) -> int:
    """The whole number ``key`` holds within ``span`` (lo, hi); its declared default when it
    is unset, garbled or outside the span (a stored -5 is not the lowest value's 'No limit'
    or 'Never')."""
    default = SETTINGS_KEYS[key]
    try:
        # (as text: Qt reads a garbled number as 0, not as an error)
        v = int(str(app_settings().value(key, default)).strip())
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return default
    return v if span[0] <= v <= span[1] else default


def one_of(key: str, values) -> str:
    """The stored choice ``key`` when it is one of ``values``, else its declared default."""
    default = SETTINGS_KEYS[key]
    try:
        v = app_settings().value(key, default)
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return default
    for allowed in values:
        if str(v) == str(allowed):
            return allowed
    return default


def apply_backup_limits() -> None:
    """Settings > Editing > Safety: how many backups of batch changes infra/backups keeps,
    and for how many days (0: no age limit). The next prune uses them."""
    from ...infra import backups

    backups.KEEP = whole("opt/backup_keep", BACKUP_KEEP_RANGE)
    backups.MAX_AGE_DAYS = float(whole("opt/backup_days", BACKUP_DAYS_RANGE))


#: Settings > Pages and libraries: the code pages page text can be decoded with ('auto' = the
#: Windows ANSI code page; format/textcodec)
CODEPAGES = (
    ("auto", "As Windows (its ANSI code page)"),
    ("1252", "1252 Western European"),
    ("1250", "1250 Central European"),
    ("1251", "1251 Cyrillic"),
    ("1253", "1253 Greek"),
    ("1254", "1254 Turkish"),
    ("1257", "1257 Baltic"),
    ("932", "932 Japanese"),
    ("936", "936 Chinese (Simplified)"),
    ("949", "949 Korean"),
    ("950", "950 Chinese (Traditional)"),
)

#: the CTG_LIB_PATH the process started with (a deployment's own folders come first)
_LIB_PATH_AT_START = os.environ.get("CTG_LIB_PATH", "")


def library_folders() -> list:
    """Settings > Pages and libraries: the extra folders the library search looks in after
    the page's project (opt/library_folders, separated by ';')."""
    raw = str(app_settings().value("opt/library_folders", "", type=str) or "")
    return [p.strip() for p in raw.split(";") if p.strip()]


def apply_library_folders() -> bool:
    """CTG_LIB_PATH = the folders it started with + :func:`library_folders` (the resolver
    reads it at each search), CTG_LIB_SIBLINGS = opt/library_siblings. True when either
    changed (the caller forgets the libraries)."""
    parts = [p for p in _LIB_PATH_AT_START.split(os.pathsep) if p]
    for p in library_folders():
        if p not in parts:
            parts.append(p)
    new = os.pathsep.join(parts)
    if app_settings().value("opt/genie_nested_full", OPTION_DEFAULTS["genie_nested_full"], type=bool):
        os.environ.pop("CTG_GENIE_NESTED_COPIES", None)
    else:
        os.environ["CTG_GENIE_NESTED_COPIES"] = "1"
    keep = app_settings().value("opt/ctf_rebuild_stale", OPTION_DEFAULTS["ctf_rebuild_stale"], type=bool)
    if keep:
        os.environ.pop("CTG_CTF_REMOVE_STALE", None)
    else:
        os.environ["CTG_CTF_REMOVE_STALE"] = "1"
    q = (
        "1"
        if app_settings().value("opt/text_question_marks", OPTION_DEFAULTS["text_question_marks"], type=bool)
        else ""
    )
    if q:
        os.environ["CTG_TEXT_REPLACE"] = q
    else:
        os.environ.pop("CTG_TEXT_REPLACE", None)
    sib = (
        "1"
        if app_settings().value("opt/library_siblings", OPTION_DEFAULTS["library_siblings"], type=bool)
        else ""
    )
    changed = False
    if sib != os.environ.get("CTG_LIB_SIBLINGS", ""):
        if sib:
            os.environ["CTG_LIB_SIBLINGS"] = sib
        else:
            os.environ.pop("CTG_LIB_SIBLINGS", None)
        changed = True
    if new == os.environ.get("CTG_LIB_PATH", ""):
        return changed
    if new:
        os.environ["CTG_LIB_PATH"] = new
    else:
        os.environ.pop("CTG_LIB_PATH", None)
    return True


def apply_codepage_at_start() -> None:
    """Before anything decodes page text (app.main): opt/codepage as CTG_CODEPAGE, unless the
    environment names one already (a deployment's choice wins). Takes effect at a start."""
    if os.environ.get("CTG_CODEPAGE"):
        return
    v = choice("opt/codepage", CODEPAGES, "auto")
    if v != "auto":
        os.environ["CTG_CODEPAGE"] = v


def apply_drawing_options() -> None:
    """Give the reader the drawing choices of Settings > Canvas (at start and after the
    dialog's OK; the pages derive again when they change)."""
    from ...io import reader

    reader.set_render_option("stacked_labels", choice("opt/stacked_labels", STACKED_LABELS, "top"))
    reader.set_composite_members(choice("opt/composite_genies", COMPOSITE_GENIES, "members") == "members")
    from ...doc.geometry import GeometryEdits

    GeometryEdits.TURN_GRADIENTS = bool(
        app_settings().value("opt/turn_gradients", OPTION_DEFAULTS["turn_gradients"], type=bool)
    )
    GeometryEdits.REVERSIBLE_TURNS = bool(
        app_settings().value("opt/reversible_turns", OPTION_DEFAULTS["reversible_turns"], type=bool)
    )
    GeometryEdits.STRETCH_TEXTS = bool(
        app_settings().value("opt/stretch_texts", OPTION_DEFAULTS["stretch_texts"], type=bool)
    )


#: after a genie save, the library genies and CLOSED pages that use it (setting
#: opt/genie_save_pages; open pages are always updated in their tabs)
GENIE_SAVE_PAGES = (
    ("ask", "Ask me"),
    ("always", "Update them in the background"),
    ("never", "Leave them (they update when opened)"),
)
#: the same after a symbol save (setting opt/symbol_save_pages)
SYMBOL_SAVE_PAGES = GENIE_SAVE_PAGES
#: the same after a template save (setting opt/template_save_pages): the templates built on
#: it and the closed pages linked to it or to them
TEMPLATE_SAVE_PAGES = GENIE_SAVE_PAGES


#: what happens after a save, or a change on disk: ask / always / never
SAVE_MODES = ("ask", "always", "never")


def _mode(key: str) -> str:
    return one_of(key, SAVE_MODES)


def _set_mode(key: str, mode: str) -> None:
    if mode in SAVE_MODES:
        app_settings().setValue(key, mode)


def genie_save_pages() -> str:
    """What uses a saved genie is updated: 'ask' | 'always' | 'never'."""
    return _mode("opt/genie_save_pages")


def set_genie_save_pages(mode: str) -> None:
    _set_mode("opt/genie_save_pages", mode)


def symbol_save_pages() -> str:
    """What uses a saved symbol is updated: 'ask' | 'always' | 'never'."""
    return _mode("opt/symbol_save_pages")


def set_symbol_save_pages(mode: str) -> None:
    _set_mode("opt/symbol_save_pages", mode)


def template_save_pages() -> str:
    """What uses a saved template is updated: 'ask' | 'always' | 'never'."""
    return _mode("opt/template_save_pages")


def set_template_save_pages(mode: str) -> None:
    _set_mode("opt/template_save_pages", mode)


#: a page or library open here that another program changed (the Graphics Builder saved it):
#: setting opt/disk_reload. One with unsaved changes here is always asked about.
DISK_RELOAD = (
    ("ask", "Ask me"),
    ("always", "Reload it (ask if it has unsaved changes)"),
    ("never", "Do nothing"),
)


def disk_reload() -> str:
    """'ask' | 'always' | 'never' (opt/disk_reload)."""
    return _mode("opt/disk_reload")


def set_disk_reload(mode: str) -> None:
    _set_mode("opt/disk_reload", mode)


#: view settings that are not on / off options, and their defaults
VIEW_DEFAULTS = {
    # View > Outside the Page: what the canvas paints around the page
    "view/surround": "desk",
    # View > Rulers (Ctrl+R) and View > Navigator (Ctrl+Shift+N)
    "view/rulers": True,
    "view/navigator": True,
}

#: View > Outside the Page (render.scene.SURROUND_MODES) with its labels, in
#: menu order: the page as a card on the desk colour (the default), the page
#: colour shaded toward the window's workspace colour, the page colour
#: everywhere (the Graphics Builder's own canvas: its page brush fills the whole
#: window), the workspace colour
SURROUND_LABELS = (
    ("desk", "Desk with the page on it"),
    ("shaded", "Shaded page colour"),
    ("page", "Page colour (as the Graphics Builder)"),
    ("workspace", "Workspace colour"),
)

#: Grid Setup / Settings > Canvas: the grid styles, the page edges (render.scene)
GRID_STYLES = (("lines", "Lines"), ("dots", "Dots"))
PAGE_EDGES = (("dotted", "Dotted (as the Graphics Builder)"), ("line", "Line"), ("none", "None"))
#: 'Major line every:' (grid steps; 0 = no major lines, 1 = every line a major one)
GRID_MAJOR_RANGE = (0, 100)


def canvas_look() -> dict:
    """The editor look of the canvas from the settings (render.scene.PageScene.set_look):
    grid_major, grid_style, grid_outside, pixel_grid, page_edge, page_shadow."""
    st = app_settings()
    lo, hi = GRID_MAJOR_RANGE
    try:
        # (as text: Qt reads a garbled number as 0, not as an error)
        major = int(str(st.value("view/grid_major", 8)).strip())
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        major = 8
    return {
        "grid_major": max(lo, min(hi, major)),
        "grid_style": choice("view/grid_style", GRID_STYLES, "lines"),
        "grid_outside": option("grid_outside_page"),
        "pixel_grid": bool(st.value("view/pixel_grid", True, type=bool)),
        "page_edge": choice("view/page_edge", PAGE_EDGES, "dotted"),
        "page_shadow": option("page_shadow"),
    }


def surround_mode() -> str:
    """The stored View > Outside the Page choice ('shaded' when unset or
    unknown)."""
    default = VIEW_DEFAULTS["view/surround"]
    try:
        mode = str(app_settings().value("view/surround", default, type=str) or default)
    except Exception:  # noqa: BLE001  # swallow-ok: a garbled value reads as the default
        return default
    return mode if mode in dict(SURROUND_LABELS) else default


@dataclass(frozen=True)
class CtDrawOption:
    """One option of the Graphics Builder's Tools > Options dialog."""

    label: str  # its name in the Graphics Builder
    ini_key: str  # where CtDraw keeps it (citect.ini section / key)
    default: object  # CtDraw's default
    setting: str  # where ctgedit keeps it: 'opt/<key>', a citect.ini value, '' = n/a
    note: str  # what it does in ctgedit, or why it is not needed


#: CtDraw's Options dialog in its own order (label -> key;
#: the keys live in [CtDraw.RSC] and are written only when they differ from
#: the default, -- except FastDisplay, [Animator] in citect.ini,
#: read by the runtime too). Every option maps to a ctgedit setting except
#: ActiveX Automatic Page Saving (see its note).
CTDRAW_OPTIONS = (
    CtDrawOption(
        "Display properties on new",
        "[CtDraw.RSC] DspAttNew",
        1,
        "opt/props_on_new",
        "Open an object's properties after you draw it or paste a symbol. A genie with "
        "parameters asks for them before it is placed.",
    ),
    CtDrawOption(
        "Display properties on copy",
        "[CtDraw.RSC] DspAttCopy",
        0,
        "opt/props_on_copy",
        "Open the properties of every object you paste or duplicate.",
    ),
    CtDrawOption(
        "Save template warning",
        "[CtDraw.RSC] DspTmpWarn",
        1,
        "opt/save_template_warning",
        "After you save a template, ask whether to update the templates and pages that use "
        "it, in the background or with Update Pages.",
    ),
    CtDrawOption(
        "Modify AN field",
        "[CtDraw.RSC] ModifyAN",
        0,
        "opt/modify_an",
        "Let the AN on Access > General be changed. The object's touch and keyboard "
        "commands take the new AN with it.",
    ),
    CtDrawOption(
        "Disable genie forms",
        "[CtDraw.RSC] DisableMacroForms",
        0,
        "opt/disable_genie_forms",
        "Open the member objects' property sheets when placing or editing a genie.",
    ),
    CtDrawOption(
        "Display group button",
        "[CtDraw.RSC] DspGroupButton",
        0,
        "opt/genie_group_button",
        "Add a Group button that opens the member objects' property sheets.",
    ),
    CtDrawOption(
        "Compile enquiry message",
        "[CtDraw.RSC] CompileEnquiry",
        0,
        "opt/compile_enquiry",
        "Run asks whether to compile the project first when it has changes that are not compiled.",
    ),
    CtDrawOption(
        "Fast runtime display",
        "[Animator] FastDisplay",
        1,
        "citect.ini [Animator] FastDisplay",
        "A Plant SCADA setting shared with the runtime and the Graphics Builder. On: "
        "saves keep the runtime's fast-display files (.ctF) up to date. Off: saves "
        "remove them and the runtime draws each page from its .ctg.",
    ),
    CtDrawOption(
        "List system pages",
        "[CtDraw.RSC] ListSystemPages",
        0,
        "opt/list_system_pages",
        "Show system pages (!) in the Previous and Next page lists of Page Properties.",
    ),
    CtDrawOption(
        "Show version 3.xx/4.xx tools",
        "[CtDraw.RSC] OldTools",
        0,
        "opt/show_legacy_tools",
        "Show the old creation tools (String, Bar Graph, Slider, Animated Symbol and "
        "others) in the Tools menu and on the ribbon.",
    ),
    CtDrawOption(
        "XP style buttons by default",
        "[CtDraw.RSC] XPButtonsDefault",
        1,
        "opt/xp_buttons",
        "Draw new buttons in the XP style. Off: the classic raised look.",
    ),
    CtDrawOption(
        "Transparent paste",
        "[CtDraw.RSC] TransparentPaste",
        0xFF000000,
        "opt/transparent_paste",
        # CtDraw's default is 0xFF000000 = no colour; its paste replaces the
        # colour with transparent pixels
        "The colour made transparent in bitmaps you paste or import.",
    ),
    CtDrawOption(
        # the Graphics Builder saves the page before it inserts an ActiveX
        # control, because running a faulty control can crash it; ctgedit copies
        # a control's saved state and never runs it
        "ActiveX automatic page saving",
        "[CtDraw.RSC] AutoPageRecovery",
        0,
        "",
        "Not needed: ctgedit never runs an ActiveX control when you insert it.",
    ),
)

#: the group an exported settings file carries (and an import requires)
EXPORT_GROUP = "ctgedit_export"
EXPORT_FORMAT = 1


#: settings that belong to this computer's running session (the open tabs),
#: never exported and kept as they are by an import: another computer's tab
#: list would be reopened as 'another editor's' pages
LOCAL_PREFIXES = ("session/",)


class SettingsFileError(ValueError):
    """A settings file that cannot be exported to / imported from."""
