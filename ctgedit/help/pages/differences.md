# Coming from the Graphics Builder

ctgedit edits the same pages, symbols, genies and templates as the Graphics Builder, and writes
files the Graphics Builder, compiler and runtime accept. This page maps the Graphics Builder's
commands, lists what works differently, and what isn't supported yet.

## Where the commands are

| Graphics Builder | ctgedit |
|---|---|
| File > New, Open, Save, Save As, Save All | the same (Open and Save As use the [page browser](opening-and-saving.md#the-page-browser)) |
| File > Close ([[Ctrl+F4]]) | File > Close Page ([[Ctrl+W]] or [[Ctrl+F4]]) |
| File > Find | the search box of File > Open |
| File > Import, Import As Flashing | the same |
| File > Properties, Defaults | File > Page Properties…, File > Page Defaults… |
| File > Compile ([[Alt+F10]]), Run ([[F5]]) | File > Compile Project, File > Run in Plant SCADA (the same keys) |
| File > Print, Print Setup | the same |
| Edit > Insert ActiveX Control, Process Analyst | Edit > Insert |
| Edit > Paste Symbol ([[F6]]), Paste Symbol As Flashing, Paste Genie ([[F11]]), Paste Super Genie ([[Ctrl+F11]]) | the same, in Edit |
| Edit > Cut Link, Copy to Library, Paste Composite Genie, Attach Super Genies | Edit > Library |
| Edit > Lock object, Unlock Object, Break Lock Mode | the same |
| Edit > Properties, Genie Parameters | Edit > Object Properties…, Edit > Genie Parameters… |
| Edit > Edit Composite Genie | right-click the composite genie > Edit Composite Genie… |
| View > Refresh, Show Tools ([[F3]]), Show Status Bar, Cross Hair Cursor | the same, in View |
| View > Show Zoom ([[F10]]) | View > Magnifier ([[F10]]) |
| View > Grid Setup, Snap to Grid ([[F8]]) | View > Grid |
| View > Guidelines Setup, Snap to Guidelines ([[F7]]) | View > Guidelines |
| View > Show Tool Bar, Show Scroll Bars | always there (View > Focus Mode hides the menu bar) |
| View > Cursor Keys Slow Speed | Tools > Advanced > Cursor Keys Slow Speed |
| Objects menu (the drawing tools) | Tools > Draw, the ribbon's Create group, the toolbox ([[F3]]) |
| Objects > Trend, Cicode Object, Web Content, Scheduler | Edit > Insert (and More on the ribbon) |
| Text menu | the same |
| Arrange > Adjust Position and Size, Align | the same, with the same keys |
| Arrange > Rotate… ([[Ctrl+Shift+R]]), Mirror… ([[Ctrl+Shift+M]]) | Rotate 90° Clockwise / Anticlockwise, Flip Horizontal / Vertical (no dialog) |
| Arrange > Group, Ungroup, Bring to Front, Send to Back, Bring Forwards, Send Backwards | the same |
| Tools > Goto Object ([[Ctrl+G]]) | Edit > Goto Object… |
| Tools > Swap Colors, Adjust Colors, Edit Favorite Colors | Tools > Colours |
| Tools > Convert to Bitmap, Bitmap Editor ([[F9]]) | the same |
| Tools > Update Pages in Active and Included Projects | Tools > Update Pages… |
| Tools > Pack Libraries in Active (and Included) Projects | Tools > Pack Libraries… |
| Tools > Plant SCADA Studio ([[F12]]), Cicode Editor, Computer Setup Wizard / Editor, Runtime Manager | Tools > External Tools |
| Tools > Options | Tools > Settings… > Graphics Builder |
| Variable Tags ([[Ctrl+T]]) | Tools > Tags Panel |
| Window > Cascade, Tile | the same; pages are tabs, Window > New Window opens another window |
| Window > Arrange Icons | none: pages are tabs |
| Help > Cicode Functions | Help > Cicode Function Reference (AVEVA) |

## What works differently

- **Tabs**: pages and library elements open as tabs, each with its own undo, selection and zoom.
  Open tabs come back on the next start.
- **Saving needs no Graphics Builder**: a save writes the page, its fast-display file and its
  database rows ([Compile and run](compile-and-run.md)). Pages also open in the Graphics Builder
  are asked about ([Working alongside the Graphics Builder](graphics-builder.md)).
- **Properties**: property windows stay open, the Properties panel edits the selection directly,
  several objects can be edited at once, and every Apply is one undo step.
- **Windows stay open**: Goto Object, Align, Font, Grid Setup, Guidelines Setup, the colour tools,
  the bitmap editor and Used By. Align moves nothing until Apply.
- **Undo** covers everything, including page properties, template changes and genie parameters.
- **Finding things**: the page browser searches every project, the Library panel the whole
  include chain, the Objects panel the whole tree, and the command palette ([[Ctrl+K]]) every
  command.
- **Tags and lists** come from the page's project, its includes and the projects that include it,
  with each entry's project shown.
- **Templates** are chosen from thumbnails.
- **Placing**: smart guides snap to other objects; guidelines are per page. When a guideline and
  a grid point are equally near, ctgedit snaps to the guideline, the Graphics Builder to the
  grid. <!-- limit:WP34 -->
- **Labels**: View > Labels shows `@(…)` labels as stored, as source text, or translated.
- **Colours**: one colour picker everywhere, with recent colours and an eye dropper. Swap and
  Adjust Colours can change the whole page (the Graphics Builder only changes the selection).
  <!-- limit:WP33 -->
- **Find and Replace** is ctgedit's own, and replaces genie texts through their parameters.
- **Copies of genies**: a copied genie that refers to its own AN is bound to the copy's AN; the
  Graphics Builder's copy keeps driving the original. <!-- limit:WP22 -->
- **Alarm indicators** can be set on a placed genie (the Graphics Builder greys the tab but keeps
  the setting). <!-- limit:WP18 -->
- **Cut Link** of a turned or scaled symbol applies the placement to the objects; the Graphics
  Builder keeps it as a transformation. The page looks the same. <!-- limit:WP31 -->
- **Cut Link to Template** writes the objects as the Graphics Builder's next load leaves them; the
  Graphics Builder's own cut writes a form that load changes. <!-- limit:WP07 -->
- **Super genie pages**: where the Graphics Builder refuses to open one, ctgedit offers the super
  genie or a read-only view.
- **Update Pages** asks what to update and backs up every page.
- **Used By** includes indirect users and shows what's out of date; **Update Users** saves the
  genies in between, then updates pages. The Graphics Builder's Update Pages never updates copies
  nested in genies. Saving a genie writes ctgedit's form, which on some older genies moves a
  caption or extent slightly. <!-- limit:WP31 -->
- **Previews** show a page as saved; changed genies and templates update when it opens.
- **Help**: [[F1]] opens this manual on what you're doing, with links to AVEVA's help.

## Not supported yet

- **ActiveX controls** are drawn from a picture taken of each one, not run on the page. A control
  that isn't registered on this computer shows as a box. Its own property pages open from Control
  Properties… on its first page. <!-- limit:WP26 -->
- **Database Exchange and Pelco camera controls** already used in the project can be placed from Edit > Insert > ActiveX Control…. <!-- limit:WP26 -->
- **Industrial Graphics symbols** are edited in the Industrial Graphics Editor. Display a published
  Industrial Graphics page in a [Web Content object](special-objects.md#web-content). <!-- limit:WP31 -->

See [Troubleshooting](troubleshooting.md) when something doesn't work as described.
