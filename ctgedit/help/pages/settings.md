# Settings

Tools > Settings… ([[Ctrl+,]], the gear at the right of the ribbon, or
[open it from here](action:options)) shows every page in one scrolling list: Editing, Canvas,
Drawing, Appearance, Keyboard, Ribbon, Pages and libraries, Simulate and Advanced. Click a page
on the left to jump to it. The Simulate window's Settings button opens the Simulate page.

Changes apply at once. Typed paths and numbers apply when you leave the box. **Revert to Opening Values**
puts back everything the window opened with; **Undo Last Change** puts back only the setting changed
last, one step at a time. With Advanced > "Apply each change at once" off,
you get OK, Cancel and Apply instead.

Every setting's tool tip says what it does and what its default is.

## Searching and resetting settings

Type in the search box ([[Ctrl+F]]) to filter settings across all pages. The **.\*** button
makes it a regular expression.

A changed setting has an accent bar on its left. Right-click a setting for **Reset to Default**
and **Copy Setting Name**. Search for **@modified** (or Filter > Show Modified Settings Only) to
see only what you've changed.

## Editing

- **Moving and resizing**: the arrow key step, whether quick presses join into one undo step,
  what Shift and Ctrl + arrow do, the large step size, whether arrows follow the grid or scroll
  the page with nothing selected, the smart guide snap radius, and how slow Mouse Slow Speed is.
- **Text**: double-click to edit in place, whether an uneven resize stretches text (on, as the
  Graphics Builder does), and whether Enter starts a new line or finishes. See
  [Text, numbers and fonts](text-and-numbers.md).
- **Expressions**: how long a pause in typing before the Cicode is checked. How many letters
  before name suggestions appear. See [Check](properties.md#check) and
  [Expressions](properties.md#expressions).
- **New objects**: colours of new objects, the style they take (see
  [The look of new objects](drawing.md#the-look-of-new-objects)), what Esc does while drawing
  point by point, whether new objects open their properties, XP-style buttons, whether a
  placed symbol lands by its top left or centre, and whether a style previews on the page when
  you point at it (see [Named styles](colours.md#named-styles)).
- **Objects**: allow changing an object's AN, and which tab Object Properties opens on. See
  [The property sheet](properties.md#the-property-sheet).
- **Genie forms**: skip the genie form, and add a Group button to it.
- **Arrange**: align to the selection box, the last selected object, or the page (see
  [Align objects](selecting-and-arranging.md#align-objects)), turn gradients with the shape, and
  turn about the same centre each time so four quarter turns land exactly where they started.
- **Safety**: confirm deletes, autosave interval, undo memory, how many earlier versions to keep
  and how much space they may use (see
  [Earlier versions of a page](opening-and-saving.md#earlier-versions-of-a-page)), and how many
  batch backups to keep and for how long (see
  [Restore a backup](troubleshooting.md#restore-a-backup)).
- **Tools**: show the old V3 / V4 tools, and keep the current tool after drawing. The tool keys
  are on the [Keyboard](#keyboard) page.
- **Properties panel**: arrow keys in number fields, label dragging and its speed, what X, Y, W
  and H mean with several objects selected, and whether the property search survives a
  selection change. See [The Properties panel](properties.md#the-properties-panel).

## Canvas

- **Mouse wheel**: zoom (default) or scroll. The Simulate window follows the same choice.
- **Touchpad**: two fingers pan and pinch zooms. Off by default, since Windows reports touchpads
  as small wheel turns.
- **Grab handles and nodes within** and **A drag starts after the pointer moves**: how precise
  the pointer has to be.
- **Mouse back and forward buttons**, **Double-click on the empty page**, **Middle
  double-click**: what they do.
- **Zoom**: wheel zoom step, stopping at 100 %, round zoom levels, animated zoom, the margin
  Fit Page leaves, and the most Zoom to Selection zooms in.
- **Edge scrolling**, **Scroll bars**, **Touch screen and pen**.
- **Navigation bar**, **Navigator** and **Overlay opacity**: where they sit, how big they are,
  and how much they fade.
- **Saved views a page keeps**.
- **Cross hair** and showing the pointer position.
- **Grid and guidelines**: the same switches as the View menu, plus guideline width, grab
  distance and snap distance. See [Grid and snap](drawing.md#grid-and-snap).
- **Selecting**: live selection box preview, dragging on large objects, Tab to select the next
  object and its order, Ctrl+Shift + drag, scrolling to objects selected in a panel, and the
  hover outline. See [Select with the keyboard](selecting-and-arranging.md#select-with-the-keyboard).
- **Moving, copying and pasting**: the modifier key scheme (Graphics Builder or design tools),
  which key drags a copy, whether Duplicate repeats the last offset, the paste offset, whether
  paste goes next to the original or at the pointer, and whether pasted objects open their
  properties. See [Move](selecting-and-arranging.md#move).
- **Pictures**: when picture options open (see [Picture options](bitmaps.md#picture-options)),
  what Ctrl+V takes from other programs, the size that's flagged as large, and a colour to make
  transparent.
- **Opening a page**: where a page opens (where you left it, fitted or 100 %), the most a small
  page zooms in, and how many page views are remembered. See
  [Opening where you left off](opening-and-saving.md#opening-where-you-left-off).
- **Undo and Redo select and show what they change**, and how long they flash. See
  [Undo and the saved state](the-window.md#undo-and-the-saved-state).
- **Underlay**: defaults for new underlays, remembering them per page, and drawing resolution.
  See [The underlay](underlay.md).

## Drawing

Where ctgedit and the Graphics Builder draw differently, pick which you want. Open pages redraw.

- **Labels**: how `@(…)` labels show (see [Text, numbers and fonts](text-and-numbers.md#labels-in-several-languages)).
- **Stacked labels**: draw only the top copy (default) or every copy, as the Graphics Builder
  does.
- **Draw a composite genie from**: its own objects (default) or its composition cache.
- **Draw embedded controls as they look**: ActiveX, Process Analyst and the other controls show
  as they look (default) or as boxes. See [ActiveX controls](special-objects.md#activex-controls).

## Appearance

The preview at the top shows your choices before they apply.

- **The window**: theme (Dark, Light or Match Windows), text size, following the Windows text
  size, and high contrast.
- **Accent colour**: the colour of selections and focus. It's adjusted where needed so text on
  it stays readable.
- **Density**: Compact, Comfortable (default) or Touch.
- **Show the keyboard focus**: after keyboard use (default) or always.
- **Canvas**: what shows outside the page (see [Around the page](the-window.md#around-the-page)),
  the page edge and shadow, grid style and major lines, grid outside the page, the pixel grid,
  selection colour, handle size, outline width, the box around several selected objects, and
  guideline colour.
- **Document tabs**: close buttons, pinned tab names, where new tabs open,
  **After closing a tab, show** (the tab next to it, or the page you had open before),
  confirming before closing a pinned tab, and tab width. See
  [Document tabs](the-window.md#document-tabs).
- **Messages**: how long messages stay, results shown over the page, and taskbar progress. See
  [Messages and tasks](the-window.md#messages-and-tasks).
- **Status bar**: the animation point count that turns the status bar amber. See
  [The status bar](the-window.md#the-status-bar).
- **Objects panel**: what rows show, where the eye and padlock go, rename on second click,
  double-click action, following the page selection, and keeping the filter. See
  [Reading a row](objects-panel.md#reading-a-row) and [The Objects panel](objects-panel.md).
- **Panels**: tabs above or below, and compact title bars.
- **Tables**: how columns fit their content. See [Panels](the-window.md#panels).
- **Tags panel**: starting column widths for the [Tags panel](tags-panel.md#the-columns).

None of this is saved in pages or printed.

## Keyboard

- **Keymap**: **ctgedit** (default; [[A]] is the Symbol tool, [[S]] opens the command palette),
  **Graphics Builder** ([[S]] is the Symbol tool) or **Design tools** ([[Ctrl+G]] groups,
  [[Ctrl+Shift+G]] ungroups). The Graphics Builder's function keys are in every keymap.
- Search by command name, menu or key ("ctrl+d"). **Record Keys** filters by the next key you
  press.
- The table shows each command's keys, where they work (**Window** or **Canvas**), and whether
  they're the keymap's (**Default**) or yours (**User**). Keys marked **Fixed** (arrows, Esc,
  Space, Enter and so on) can't be reassigned.
- Double-click a command to change its keys. A key already in use shows in red: **Use Anyway**
  moves it, **Choose Another** picks a different key. **Remove** clears the command's keys and
  **Reset** restores the keymap's.
- The Simulate window's commands are listed last. Their keys only work in the Simulate window,
  so they don't clash with the editor's. While Page Keys is on, the simulated page gets every
  key except a few window commands (see [The page](simulate.md#the-page)).
- **Reset All** drops all your own keys. **Export…** and **Import…** save and load keymaps.
- **Other windows**: whether + and - zoom in the Bitmap Editor and magnifier, and which key
  resets the manual's text size.

Key changes apply everywhere at once: menus, tool tips, the palette and Help > Keyboard
Shortcuts.

## Ribbon

- **Groups shown**: hide ribbon groups you don't use. Their commands stay in the menus and the
  palette.
- **My Commands**: your own group at the end of the Design tab.
- **Show the key and a description in tool tips**.
- **Quick Access commands**, **Quick row of the right-click menu** and **Selection toolbar
  commands**: which commands they hold, and how long the selection toolbar waits before showing
  again.
- **Selecting a text, shape or genie opens its tab**. Off by default: the tab appears and is
  tinted, and Design stays shown with its drawing tools.
- **The command palette opens with the last search**.
- **Quick Open searches first** this project, all projects, or whichever you used last, and how
  many rows Quick Open and the palette show.

**Reset All Settings…** shows every group again and empties My Commands.

## Pages and libraries

- **When a page opens**: reapply a changed template (see
  [Page templates](templates.md#when-a-template-changes)), update changed symbols and genies,
  rebuild a whole genie when only a nested genie changed, use placeholders for missing libraries
  (see [A genie, symbol or template is missing](troubleshooting.md#a-genie-symbol-or-template-is-missing-or-empty)),
  and show the bar listing automatic updates. Also: how long an automatic update waits so you
  can still redo, whether open tabs wait for the background update after a genie save, in what
  order open tabs are updated, and how many at once.
- **After saving**: write fast display files (.ctF) and replace out-of-date ones, ask what to
  update after saving a template, offer Update Pages after a super genie save, what to do with
  the genies and closed pages that use a saved genie, symbol or template (ask, update in the
  background, or leave them; see [When you save a genie](library-elements.md#when-you-save-a-genie)
  and [When a template changes](templates.md#when-a-template-changes)), close the Graphics
  Builder after saving a library (see [Close the Graphics
  Builder](graphics-builder.md#close-the-graphics-builder)), and warn before saving
  into a library that ships with Plant SCADA.
- **Pages**: the default name for new pages, allow editing template objects on pages, list
  system pages in Page Properties' Previous and Next lists, and the default print orientation.
- **Run**: ask whether to compile before Run, and when to show compile results.
- **Files and folders**: what to do when another program changes an open page, how to tell you
  (see [Notices over the page](opening-and-saving.md#notices-over-the-page)), following
  libraries changed by other programs, extra library folders, a shared styles file (see
  [Named styles](colours.md#named-styles)), searching all projects for libraries, the text code
  page, and whether unstorable characters are saved as ?.
- **Find in closed pages** and **Update Pages**: how many helper processes to use. See
  [Helper processes](update-pages.md#helper-processes).
- **Progress figures**: show elapsed time, time left and counts, and how the estimate behaves.
  See [Progress figures](update-pages.md#progress-figures).
- **Busy projects**: how long saves wait when another ctgedit process is writing the same
  project. See [When a page cannot be updated](update-pages.md#when-a-page-cannot-be-updated).
- **Long jobs**: timings for background tasks, helper processes, previews and large saves.
- **Library panel**: card preview size, Ctrl + wheel card sizing, and how many recent elements
  to keep. See [Find an element](library-panel.md#find-an-element).
- **Searching**: also match names with the letters in order (`mtr` finds `motor`).
- **Open and Save As**: shorten shared name prefixes, when the preview pane shows, and whether
  it shows the saved picture or a fresh render. See
  [The page browser](opening-and-saving.md#the-page-browser).
- **Starting and switching pages**: what opens on start, Ctrl+Tab order (see
  [Document tabs](the-window.md#document-tabs)), recent pages kept, and whether a second launch
  goes to the running editor.

**Write fast display files (.ctF)** is Plant SCADA's own setting (`[Animator] FastDisplay` in
`citect.ini`), shared with the runtime and the Graphics Builder.

## Simulate

The Simulate window's options are described in
[Simulate settings](simulate.md#simulate-settings). Changes reach an open Simulate window at
once. They include Space + drag panning, the starting clock speed, what Step runs, the zoom a
page opens at, reload timings, list limits, refresh rates, and whether page code may use DLLs
and SQL databases (off by default; see [DLLs and databases](simulate.md#dlls-and-databases)).

## Advanced

- **Developer and diagnostic tools**: the thumbnail backdrop and dynamics badges.
- **Check each opened page's display**: draws each page again in a second process and reports
  differences (see [Check the page display](troubleshooting.md#check-the-page-display)).
- **Updates**: look for a new version when ctgedit starts (see [Updates](getting-started.md#updates)).
- **The settings window**: apply each change at once, and after what pause.

## Reset, export and import

- **Reset All Settings…** asks first, then resets every setting on every page, except fast
  display files, also the ones the search hides. **Undo Reset** then puts back the values from
  just before it.
- **Export…** saves the groups you tick (options, keys, ribbon, layout, styles, colours, recent
  pages, guidelines) to a file.
- **Import…** loads them. **Merge** (default) only sets what's in the file; **Replace** also
  resets the rest of those groups. Recent pages and window layout aren't ticked by default.

## Where ctgedit keeps its settings and files

- Settings: the registry, under `HKEY_CURRENT_USER\Software\ctgedit\editor`.
- Logs, recovery copies and backups: `%LOCALAPPDATA%\ctgedit` (Help > Open Log Folder).
- Nothing is written into pages or projects.

Environment variables for support: `CTG_LIB_PATH` (extra library folders), `CTG_SCADA_BIN` (the
Plant SCADA `Bin` folder), `CTG_CODEPAGE` (the code page), `CTG_SETTINGS_INI` (an INI file
instead of the registry) and `CTG_DATA_DIR` (the data folder).

## Coming from the Graphics Builder

Each option from the Graphics Builder's Options dialog is on the page that fits it. Searching
for its Graphics Builder name finds it.

| Graphics Builder | ctgedit |
|---|---|
| Display properties on new | Editing > New objects open their properties |
| Display properties on copy | Canvas > Pasted and duplicated objects open their properties |
| Save template warning | Pages and libraries > Ask what to update after saving a template |
| Modify AN field | Editing > Allow changing an object's AN |
| Disable genie forms | Editing > Skip the genie form and open the object properties |
| Display group button | Editing > Add a Group button to genie forms |
| Compile enquiry message | Pages and libraries > Ask whether to compile before Run |
| Fast runtime display | Pages and libraries > Write fast display files (.ctF) |
| List system pages | Pages and libraries > List system pages in Page Properties |
| Show version 3.xx/4.xx tools | Editing > Show the old V3 and V4 tools |
| XP style buttons by default | Editing > Draw new buttons in the XP style |
| Transparent paste | Canvas > Make a colour transparent in pasted pictures |

They default to the Graphics Builder's values but are stored separately, except fast display
files, which both share.

See [Set Graphics Builder Options](aveva:1172083) in the AVEVA help.
