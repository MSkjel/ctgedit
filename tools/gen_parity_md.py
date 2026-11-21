"""Generate PARITY.md from the inventory and a status map."""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
INV = os.path.join(REPO, "docs", "ctdraw_inventory.json")
OUT = os.path.join(REPO, "PARITY.md")

# keyword (case-insensitive regex) -> (mark, note). First match wins.
# x = done, ~ = partial, '' = missing.
_STATUS = [
    (
        r"^New",
        "x",
        "five-class chooser (Page/Template/Symbol/Genie/Super Genie); library classes create the element",
    ),
    (r"^Open", "x", "file dialog; class-tab browser missing"),
    (r"^Close", "x", ""),
    (
        r"^Save \(",
        "x",
        "+ auto pages.DBF registration + native fast display (.ctF) + pg*.DBF register -- no Graphics Builder",
    ),
    (r"^Save As", "x", ""),
    (r"^Save All", "x", "saves every open editor window's dirty page"),
    (r"^Find\.\.\.$", "x", "Find Page browser (pages.DBF / *.ctg)"),
    (
        r"^Import\.\.\.$",
        "x",
        "every Qt picture format -> embedded bitmap, .wmf -> MetaFile object; drag files from Explorer; Transparent Paste colour",
    ),
    (
        r"Import As Flashing|Paste Symbol As Flashing",
        "x",
        "two-DIB flashing bitmap (flag 0x101 + second zlib DIB;-pinned); File / Edit menus, library thumbnail pickers",
    ),
    (
        r"Paste Composite Genie",
        "x",
        "typed template picker (XML schemas) + donor-instance clone; cache regenerates at compile",
    ),
    (r"^Print \(", "x", "QPrinter render of the scene"),
    (r"Print Setup", "x", "QPageSetupDialog"),
    (
        r"Paste Special",
        "x",
        "object format + Device Independent Bitmap (Ctrl+V pastes a clipboard picture too)",
    ),
    (r"Convert to Bitmap", "x", "rasterise selection in place"),
    (
        r"^Options",
        "x",
        "Tools > Settings: every CtDraw Options dialog option mapped to a ctgedit setting on the page of what it does (ui/prefs/settings.CTDRAW_OPTIONS; a search for its CtDraw name finds it; Fast runtime display = citect.ini [Animator] FastDisplay; ActiveX automatic page saving n/a) + ctgedit's own options, Restore Defaults, Export / Import",
    ),
    (
        r"Plant SCADA Studio|Cicode Editor|Setup Wizard|Setup Editor|Runtime Manager",
        "x",
        "external launchers (when installed)",
    ),
    (r"Cross Hair", "x", "full-width / full-height lines through the pointer (View menu)"),
    (r"^About|^Help \(F1\)", "x", "About box + AVEVA web-help links (F1)"),
    (
        r"Grid Setup",
        "x",
        "pitch x / y + origin, display, snap: a moved object's top-left lands on the nearest intersection",
    ),
    (
        r"Show Zoom",
        "x",
        "View > Magnifier (F10): floating magnifier, Zoom In / Out buttons, Ctrl+= / Ctrl+- (Num+/- while Settings > Keyboard keeps the bare keys) and the wheel (2x-16x)",
    ),
    (r"Genie Parameters", "x", "incl. classic %template% re-substitution of members"),
    (r"Properties\s+\[page properties\]", "x", "PagePropertiesDialog, 6 tabs 1:1"),
    (r"^Defaults", "x", "page-defaults dialog (style/resolution/title bar/background), persisted"),
    (r"^Compile", "x", "COM backend"),
    (r"^Run", "x", "launches the Plant SCADA runtime (F5)"),
    (r"^Print", "", ""),
    (r"^Exit", "x", ""),
    (r"MRU", "", ""),
    (r"^Undo", "x", ""),
    (r"^Redo", "x", ""),
    (r"^Cut \(", "x", ""),
    (r"^Copy \(", "x", ""),
    (r"^Paste \(", "x", ""),
    (r"Paste Special", "", ""),
    (
        r"Insert ActiveX|Insert Process",
        "x",
        "clones engine-authored control records (CiMeter / Process Analyst); engine-validated",
    ),
    (
        r"Insert Database",
        "x",
        "clones the engine-authored Database Exchange record CtDraw's own Insert Database Exchange made (2026-10-09); engine-validated but the control storage's save time",
    ),
    (r"^Delete", "x", ""),
    (r"^Duplicate", "x", ""),
    (r"^Select All", "x", ""),
    (r"^Find\.\.\. \(Ctrl\+F\)", "x", "object-text find across the page"),
    (r"^Replace", "x", "Replace / Replace All on object text"),
    (r"Cut Link", "x", "converts a linked symbol to a local group (1:1-scaled placements)"),
    (r"Paste Symbol\.\.\.", "x", "library dock browser"),
    (r"Paste Symbol As Flashing", "", ""),
    (
        r"Paste Genie",
        "x",
        "library dock places genies; super-genie binds readable/editable via Attach Super Genies",
    ),
    (r"Paste Composite Genie", "", ""),
    (r"Copy to Library", "x", "native library write (new or existing .ctl/.ctm), resolver-verified"),
    (
        r"Attach Super Genies",
        "x",
        "the Attach Super Genies dialog; binds live in the template's link/import table",
    ),
    (r"Lock object|Unlock Object|Break Lock", "x", "session lock mode (locked objects unselectable)"),
    (
        r"Properties\.\.\.\s+\[object property sheet\]",
        "x",
        "tabbed sheet incl. per-kind variants: corner radius, rectangle style, pie/arc angles, polygon open, gradient (modes 0-8), 3D effects",
    ),
    (r"Genie Parameters", "x", "incl. composite member union"),
    (
        r"Edit Composite Genie",
        "x",
        "TYPED Presentation Options form generated from the template XML schema (raw editor as fallback)",
    ),
    (r"^Refresh", "x", ""),
    (r"Grid Setup", "~", "grid size menu; full setup dialog pending"),
    (r"Snap to Grid", "x", ""),
    (
        r"Guidelines",
        "x",
        "setup dialog (per-direction Set/Clear/Clear All) + painted + snap F7 (edges / centre); drag, Ctrl+drag = new, drag to the page edge = delete, double-click = setup; kept per page",
    ),
    (
        r"Show Tool Bar|Show Tools|Show Status Bar",
        "x",
        "ribbon + F3 floating toolbox; View > Show Status Bar",
    ),
    (r"Show Zoom", "", "View > Magnifier (F10)"),
    (r"Show Scroll Bars", "x", ""),
    (r"Cross Hair|Cursor Keys", "x", "slow speed = 1px nudge; normal = grid pitch"),
    (r"Free Hand Line", "x", "drag-collected open polyline"),
    (r"^Straight Line", "x", ""),
    (r"^Rectangle", "x", ""),
    (r"^Ellipse", "x", ""),
    (r"^Polygon", "x", ""),
    (r"^Pipe", "x", ""),
    (r"^Text$", "x", ""),
    (r"^Number", "x", ""),
    (r"^Button", "x", ""),
    (r"^Symbol Set", "x", "placement; per-state editing pending"),
    (r"^Trend", "x", "renders + creation tool (corpus seed, pens cleared on create)"),
    (r"Cicode Object", "x", "V3/V4 Objects menu (0x1009 AdvancedAnimation seed; CMD cleared on create)"),
    (
        r"Web Content|Scheduler",
        "x",
        "cloned engine-authored records (0x13 / Scheduler control); engine-validated",
    ),
    (
        r"Pelco",
        "",
        "the Pelco control (CLSID {CCC8A45C-7444-4D46-8FB2-B97C37F908FA}) is not installed here: no menu item in CtDraw; Insert ActiveX Control places one the project already uses",
    ),
    (r"^Font\.\.\.", "x", "byte-pinned face/point-size/flags dialog"),
    (r"Bold|Italic|Underline|Strikeout", "x", "attribs byte, Ctrl+B/I/U"),
    (r"Justified|Centered", "x", "attribs low nibble"),
    (r"Normal$|Shadowed|Raised|Lowered|Embossed", "x", "effects byte 0-4, ghost-rendered"),
    (r"Adjust Position", "x", "Ctrl+Shift+P dialog"),
    (r"^Align", "x", "Ctrl+Shift+A dialog + direct actions + distribute/same-size beyond CtDraw"),
    (
        r"^Rotate",
        "x",
        "90° CW/ACW incl. box CONTENTS: text orient field, symbol slot-1 matrices, bitmap pixels (engine-validated)",
    ),
    (r"^Mirror", "x", "H/V incl. contents (slot-1 flip matrices / flipped DIBs; engine-validated)"),
    (r"Group Objects", "x", "structural container records"),
    (r"Ungroup Objects", "x", ""),
    (r"Bring to Front|Send to Back|Bring Forwards|Send Backwards", "x", ""),
    (
        r"Goto Object",
        "x",
        "object tree (input / Cicode records too), AN or name search "
        "with Previous / Next Match, OK opens the properties",
    ),
    (r"MRU list", "x", "Recent Pages submenu (QSettings)"),
    (
        r"Swap Colors|Adjust Colors|Favorite Colors",
        "x",
        "swap/HLS-adjust static colours and bitmap pixels; favourites feed the colour pickers",
    ),
    (
        r"Bitmap Editor",
        "x",
        "in-app raster editor: both flash states, Eye Dropper (Shift+P), right button = transparent, Bitmap Size grow/shrink, undo; patches only the DIB section",
    ),
    (r"Convert to Bitmap", "x", "rasterise selection in place"),
    (r"Pack Libraries", "x", "drops deleted-element stubs + re-tiles the container"),
    (
        r"Update Pages",
        "x",
        "genies + page templates whose element stamp changed are re-composed + saved (.ctF + pg rows), active + included projects; per-page backups",
    ),
    (
        r"Plant SCADA Studio|Cicode Editor|Setup Wizard|Setup Editor|Runtime Manager",
        "",
        "external tool launchers",
    ),
    (r"^Options", "", "editor options dialog"),
    (r"Cascade|Tile|open page windows", "x", "multi-window: cascade/tile + Window-menu list"),
    (r"Arrange Icons", "~", "MDI child icons do not exist in the multi-window UI"),
    (r"Help|About", "x", "AVEVA web-help links (F1) + About"),
    (r"Cicode Functions", "x", "web Cicode reference link"),
    (r"^Cut \(Ctrl\+X\)$|^Copy \(Ctrl\+C\)$|^Paste \(Ctrl\+V\)$", "x", ""),
    (r"^Group$|^Ungroup$", "x", ""),
    (r"Page Properties\.\.\.", "x", ""),
    # canvas context menu
    (r"^Properties\.\.\.$", "x", "context menu -> object property sheet"),
    # Zoom window (F10) keys
    (r"^Zoom In \(Num\+\)$|^Zoom Out \(Num-\)$", "x", "ZoomWindow magnifier factors"),
    # embedded Bitmap Editor's own menus
    (
        r"^Save$|^Zoom In \(\+\)$|^Zoom Out \(-\)$|^Dimensions$|^Color Select|^Symbols$|^Zoom$|^Tools$",
        "x",
        "in-app Bitmap Editor controls (Save / zoom +- / Bitmap Size / Eye Dropper Shift+P / tool buttons)",
    ),
    # legacy V3/V4 'Objects' tools (hidden in CtDraw unless the option is set)
    (r"Variable Tags", "x", "Tools > Tags Panel (Ctrl+T): the Tags panel, its search focused"),
    (
        r"^String$|^Animated Symbol$|^Animation Keyboard$|^Pagewide Keyboard$",
        "x",
        "V3/V4 Objects menu (engine-authored corpus seeds; bindings via Properties)",
    ),
    (r"^Symbol$", "x", "covered by the modern Symbol placement tool"),
    (
        r"^Bar Graph$|^Slider$",
        "x",
        "V3/V4 Objects menu (seeds from FACEPLAT.CTM; super-genie bindings cleared on create)",
    ),
    (
        r"^Digital Text$|^Color Flood$",
        "x",
        "V3/V4 Objects menu (engine-AUTHORED seeds: re-typed sibling candidates echoed back by PageSave)",
    ),
]


def status_for(item: str):
    for pat, mark, note in _STATUS:
        if re.search(pat, item, re.IGNORECASE):
            return mark, note
    return "", ""


def main() -> int:
    with open(INV, encoding="utf-8") as fh:
        data = json.load(fh)
    inv = data["menus"]
    lines = [
        "# CtDraw 1:1 feature parity contract",
        "",
        "Source of truth for the parity program. Generated from the inventory of CtDraw's",
        "menus and dialogs (docs/ctdraw_inventory.json) by tools/gen_parity_md.py;",
        "statuses curated by hand. `[x]` done, `[~]` partial, `[ ]` missing.",
        "",
        "## Menus",
        "",
    ]
    done = part = todo = 0
    for menu in inv["menus"]:
        lines.append("### %s" % menu["menu"])
        for item in menu["items"]:
            if item.startswith("---"):
                continue
            mark, note = status_for(item)
            box = {"x": "[x]", "~": "[~]", "": "[ ]"}[mark]
            done += mark == "x"
            part += mark == "~"
            todo += mark == ""
            lines.append("- %s %s%s" % (box, item, (" -- _%s_" % note) if note else ""))
        lines.append("")

    lines += ["## Dialogs", ""]
    for dlg in inv["dialogs"]:
        lines.append("### %s" % dlg["title"])
        for c in dlg["controls"]:
            lines.append("- %s" % c)
        lines.append("")

    lines += ["## Editor behaviours", ""]
    for f in inv["features"]:
        lines.append("- %s" % f)
    lines += [
        "",
        "## Status summary (menu items)",
        "",
        "done: %d | partial: %d | missing: %d" % (done, part, todo),
        "",
    ]
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("wrote", OUT, "| done=%d partial=%d missing=%d" % (done, part, todo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
