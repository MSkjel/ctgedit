# The editor window

The menu bar and ribbon are at the top, the page canvas in the middle, panels on either side,
and the status bar at the bottom. Every command is in the menus; the ribbon, panels and context
menus are shortcuts to the same commands.

## The ribbon

**Quick Access** (top left) has New, Open, Save (its arrow has Save As, Save All and Recent
Pages), Undo and Redo. On the right are the **Search** field ([[Ctrl+K]], see
[Search](#search)), Compile Project, Run, Simulate Page and Settings ([[Ctrl+,]]).

A greyed command can't run right now; its tool tip says what it needs.

The **Design** tab has what you use while drawing:

| Group | What it has |
|---|---|
| Select | the Select tool, Goto Object, Find, locking, the selection **Filter** (which kinds of object can be clicked) and the **Box** mode (Enclose, Touch, or Auto, where right-to-left drags touch) |
| Place | Symbol, Paste Genie, Symbol Set, Import, Paste Special, Paste Composite Genie |
| Create | the drawing tools and **Colours** for new objects (see [The look of new objects](drawing.md#the-look-of-new-objects)) |
| Styles | named styles as tiles; point to preview, click to apply (see [Named styles](colours.md#named-styles)) |
| Modify | Duplicate, Rotate, Flip, Adjust Position and Size, Convert to Bitmap, Cut Link, Delete |
| Arrange | align, Align…, Distribute, Make Same Size, Group, Ungroup, Order, Space |
| Data | Genie Parameters, Object Properties, Page Properties, Copy / Paste Appearance |

The **Utilities** tab has the colour tools, library tools, the Bitmap Editor and the Plant SCADA
tools.

**Text**, **Shape** and **Genie** tabs appear when the selection has text, shapes or genies.
Selecting on the canvas brings them to the front.

### Group menus

Click a group's name for a menu of all its commands, including a few that have no button.

### When the window is narrow

The ribbon fits the window width. First the less used buttons lose their labels; then whole
groups fold into a single button. Data folds first, then Select, then Arrange, Modify and Create.
Place keeps its Symbol tool and shows its other commands as icons.

### Collapse the ribbon

View > Collapse the Ribbon ([[Ctrl+F1]]), or double-click a tab, leaves only the tab row.
Clicking a tab then shows its commands over the page until you use one or click away.

### Tool tips

Tool tips show the command's name and key, what it does, what it needs when greyed, and **F1 for
help** when the manual covers it (press [[F1]] while pointing). Settings > Ribbon can turn the
longer tips off, hide groups and add your own (see [Settings](settings.md#ribbon)).

## The canvas

Around the page:

- **Rulers** along the top and left (View > Rulers, [[Ctrl+R]]).
- The **navigation bar** at the bottom and the **navigator** map when the page is larger than
  the window.
- The **selection bar** next to the selection.
- Thin scroll bars that appear while scrolling. Point at one to use it.

A thin accent frame inside the canvas means the keyboard is on the page.

Right-click an object for its commands; a row of quick icons tops the menu. Right-click empty
space for Paste, Fit Page, Fit All Objects and Page Properties.

## Zoom and pan

- **Mouse wheel**: zooms about the pointer. [[Alt]] + wheel zooms finely, [[Shift]] + wheel
  scrolls sideways. Settings > Canvas can make the wheel scroll instead, with [[Ctrl]] + wheel
  zooming.
- **Touchpad**: pinch zooms. Two-finger panning is off by default; turn it on in Settings >
  Canvas.
- **Touch screen and pen**: two fingers pan and pinch, a long press opens the context menu.
- **Pan**: drag with the middle button, hold [[Space]] and drag, or use the **Hand** tool
  ([[H]]).
- **Zoom In / Zoom Out** ([[Ctrl+=]] / [[Ctrl+-]]) step through round levels from 2 % to 6400 %.
- **Zoom Box** ([[Z]]): drag a box to fill the window with it. Click to zoom in 2x, [[Alt]] +
  click to zoom out. Hold [[Z]] to use it briefly while another tool is armed.
  [[Ctrl+Shift]] + drag is a zoom box too.
- **Middle double-click**: Fit All Objects.
- When the page is out of view, a **Page** chip at the edge points to it. Click it for Fit
  Page.

## Previous View and Next View

View > Previous View ([[Alt+Left]]) goes back to where you were before the last zoom or pan;
Next View ([[Alt+Right]]) goes forward. The mouse's back and forward buttons do the same. Each
tab has its own view history.

## Saved views

View > Saved Views keeps named views of a page. **Save Current View…** names one; each then
appears in the menu and the command palette. **Manage Saved Views…** reorders, renames and
removes them. They're remembered by ctgedit, not saved into the page.

## The navigation bar

The bar at the bottom of the canvas has the zoom and view tools: Hand, Zoom Box, zoom level,
the fits, grid and snap toggles, rulers, navigator, guidelines, cross hair and magnifier.

- Click the zoom level to type one, or use its arrow for preset levels.
- Right-click the bar for **Customise…** and **Position**.
- It fades while you drag near it.

## The rulers

View > Rulers ([[Ctrl+R]]) shows rulers in page pixels. They mark the page, the selection, the
guidelines and the pointer.

- Drag from a ruler onto the page to make a guideline (see [Guidelines](drawing.md#guidelines)).
- Double-click a ruler for Guidelines Setup. Double-click the corner for Fit Page.

## The navigator

View > Navigator ([[Ctrl+Shift+N]]) shows a small map of the page with the visible part framed.
Click to jump, drag the frame to pan, double-click for Fit Page. It hides while the whole page is
visible. View > Panels > Navigator shows it as a panel instead.

## Around the page

The page sits on a much larger work surface. You can draw, select and move objects outside the
page too, within the -32000 to 32000 range a page can store.

- View > Fit Page ([[Ctrl+0]]) shows the whole page with a margin.
- View > Fit All Objects ([[Ctrl+3]]) shows everything, including objects off the page:
  [Fit All Objects](action:zoom_all).
- View > Zoom to Selection ([[Ctrl+2]]) centres the selection.
- View > Zoom 100% ([[Ctrl+1]]).
- Dragging to the edge of the canvas scrolls the page.

View > Outside the Page chooses what surrounds the page: a desk with the page on it (default),
the shaded page colour, the page colour, or the workspace colour. The page edge and shadow are
set in Settings > Appearance. None of this is saved, printed or put in thumbnails.

Animation points (old V3 / V4 records that reserve an AN, often kept by templates just off the
page) show as a small cross with their AN, in the editor only.

## Panels

| Panel | Use it to |
|---|---|
| [Objects](objects-panel.md) | see the page's objects as a tree, select inside groups and genies, hide objects while you work |
| [Library](library-panel.md) | find and place symbols and genies |
| [Tags](tags-panel.md) | find tags and equipment and drag them onto genies |
| [Properties](properties.md) | change the selected objects, or the page |
| [Workspace Checks](situational-awareness.md#workspace-checks) | see the checks of a Situational Awareness page |
| [Page Checks](troubleshooting.md#check-page) | see what Tools > Check Page found |
| [History](#history-panel) | see the undo steps and jump to any of them |
| [Changes](#changes-panel) | see what changed since the save |

View > Panels shows or hides each. Drag a panel by its title to dock it elsewhere, stack it with
another, or float it. Double-click a floating panel's title to dock it again.

In lists with column titles, drag a divider to resize a column or double-click it to fit.
Right-click the titles for **Fit Columns to Contents**.

## Document tabs

Each open page or library element has a tab with its own undo history, selection, zoom and
hidden objects. The icon shows what it is; a lock means read-only. Tabs with the same name show
their project too.

The window title names the page and project, with a dot (•) for unsaved changes.

- A tab with unsaved changes shows a dot that turns into a close button when you point at it. A
  middle click closes a tab.
- Tabs keep at least 12 characters of their name. When they don't fit, the bar scrolls.
- New tabs open at the end; Settings > Appearance can open them next to the current tab.

Right-click a tab for:

| Command | What it does |
|---|---|
| Close Page | closes it ([[Ctrl+W]]) |
| Close Others | closes every other tab |
| Close to the Right | closes the tabs after it |
| Close Saved | closes tabs without unsaved changes |
| Close All Pages | closes every tab ([[Ctrl+Shift+W]]) |
| Pin Tab / Unpin Tab | keeps the tab at the left |
| Reopen Closed Page | reopens the last closed page ([[Ctrl+Shift+T]]) |
| Copy Path, Copy Name | copies the path or name |
| Show in Explorer | opens its folder |
| Show in Page Browser… | opens File > Open with it selected |
| Page Properties…, Revert to Saved… | as in the File menu |
| Move to New Window, Move to Window | moves the page to another window |

When several tabs being closed have unsaved changes, one list asks about all of them (see
[Unsaved changes](opening-and-saving.md#unsaved-changes)). Close commands leave pinned tabs
open.

Switching from the keyboard:

| Keys | What they do |
|---|---|
| [[Ctrl+Tab]], [[Ctrl+Shift+Tab]] | the page shown before (hold Ctrl for the list) |
| [[Ctrl+PgDown]], [[Ctrl+PgUp]] | the next or previous tab |
| [[Alt+Shift+1]] to [[Alt+Shift+9]] | tab 1 to 9 |
| [[Ctrl+Shift+T]] | Reopen Closed Page |

The arrow at the end of the tab bar lists every open tab. The **+** button is File > New….

Drop a `.ctg` file from Explorer on the window to open it. A library file opens the page browser
on its elements.

Window > New Window opens another editor window. Move a page between windows with Window > Move
to New Window, or by dragging its tab out of the bar.

Property windows stay open while you work and follow their object. They hide while another tab
is shown.

When you close ctgedit, the open tabs are remembered (see
[Opening where you left off](opening-and-saving.md#opening-where-you-left-off)).

## The status bar

From left to right:

- **Messages**, sometimes with a button such as Undo. See [Messages and tasks](#messages-and-tasks).
- **Background tasks**, while something runs.
- **The bell**: the message history.
- **Project**: click it for the project's pages, included projects and more. Amber when it
  isn't the active install.
- **Animation points**: amber above 3000.
- **Hidden objects**: click to show them.
- **Underlay**: click to show or hide the [underlay](underlay.md), double-click for its window.
- **Problems**, such as missing symbols.
- **Document state**: Saved, unsaved changes, or Read-only. Click it for the History panel.
- **Selection**: what's selected, its size and position.
- **Pointer position** on the page.
- **Zoom**: click for levels, double-click to fit, [[Ctrl]] + click to type one.
- **Snap**, **Guidelines** and **Smart Guides** toggles.
- **Labels**, when labels aren't shown as stored.

Right-click the status bar to choose its fields.

## Messages and tasks

Click the bell (or View > Messages) for every message, newest first. A red badge counts new
errors.

The tasks button shows what's running in the background, with progress and **Cancel**. Tasks
that take longer than a few seconds report how they ended.

Large commands (big deletes or pastes, Replace All and so on) also show their result over the
page briefly, with **Undo**.

Error boxes say what went wrong and what to do. **Show Details** and **Copy Details** give you
what's needed for a bug report; **Open Log** opens the activity log.

## The drawing toolbox

View > Show Tools ([[F3]]) opens a floating window with the drawing tools. Right-click it for
**Show Labels**.

## The magnifier

View > Magnifier ([[F10]]) shows the area around the pointer magnified 2x to 16x. Zoom it with
its buttons or the mouse wheel.

## Search

One search finds commands, pages, symbols, genies, tags, objects, settings and manual sections.
Click the **Search** field, press [[Ctrl+K]], or press [[S]] over the page to open it at the
pointer with the tool box.

- Type part of a name, word starts ("dist h"), or initials ("dh"). The Graphics Builder's names
  for commands work too.
- Greyed rows can't run now; [[Enter]] on one says what it needs.
- With nothing typed it shows recent commands, commands for the selection, and suggestions.

| Keys | What they do |
|---|---|
| [[Up]], [[Down]], [[Page Up]], [[Page Down]], [[Home]], [[End]] | move in the list |
| [[Enter]] or a click | run, open or place |
| [[Shift+Enter]] | run and keep the list open; on a library element, edit it |
| [[Alt+Enter]] or [[Ctrl+Enter]] | change the command's keys (see [Keyboard](settings.md#keyboard)); open a page in a new window; select every object listed |
| [[Ctrl+C]] | copy the highlighted name |
| [[Esc]] | close the list |

### Prefixes

| Prefix | Finds | Enter |
|---|---|---|
| `>` | commands only | runs it |
| `@` | objects on the page, by AN, name, kind, element, text or tag (`@valve`) | selects and shows it |
| `:` and a number | the object with that AN (`:581`) | selects and shows it |
| `?` | manual sections (`?smart guides`) | opens the section |
| `set:` | settings with their current value (`set:grid`) | opens Settings on it |
| `p:` `s:` `g:` `tpl:` `sg:` `t:` `f:` | pages, symbols, genies, templates, super genies, tags, functions | as in [Quick Open](#quick-open) |

### The tool box

Opened with [[S]] over the page, the list starts with tiles for the drawing tools and any
commands you pin. Press [[1]] to [[9]] or click a tile. Right-click a row for **Pin to the Tool
Box**.

## Keyboard shortcuts

Help > Keyboard Shortcuts ([[Ctrl+/]]) lists every command's keys. Search by name, menu or key,
or click **Record Keys** and press one. **Change…** reassigns keys (see
[Keyboard](settings.md#keyboard)); **Print…** and **Export…** make a one-page PDF. The
[keyboard shortcut reference](keyboard-shortcuts.md) lists the same keys by menu.

Keys that work everywhere:

- [[Alt+0]] goes to the page; [[Alt+1]] to [[Alt+5]] go to the Objects panel, Library search,
  Tags search, Properties and History.
- [[Ctrl+F6]] cycles between the page, panels and tabs; [[Ctrl+Shift+F6]] goes back.
- [[F4]] (Edit > Repeat) repeats the last arrange, rotate, group, lock or text command.
- Zoom In also works as [[Ctrl++]] and [[Ctrl+Num +]], Zoom Out as [[Ctrl+Num -]]. Redo is
  [[Ctrl+Y]] or [[Ctrl+Shift+Z]].

## Quick Open

File > Quick Open ([[Ctrl+Shift+O]]) searches the pages, symbols, genies, templates, super
genies, tags and Cicode functions of the project and its includes. Word initials work ("mxl3"
finds Mixing_L3). With nothing typed it lists open and recent pages.

- Prefixes keep one kind: `p:` `s:` `g:` `tpl:` `sg:` `t:` `f:`. `@` and `:` find objects on the
  page; `>`, `?` and `set:` go to [Search](#search).
- [[Tab]] switches between **This project** and **All projects**.
- [[Enter]] opens a page, arms a symbol or genie for placing, makes a new page from a template,
  shows a tag, opens a function's .ci file, or selects an object.
- [[Shift+Enter]] edits a library element; [[Ctrl+Enter]] opens a page in a new window.

## Searching with a regular expression

Every search box has a **.\*** button that switches it to regular expressions (case
insensitive; use `^` and `$` to anchor). An invalid expression gets a red frame. In Find and
Replace, **Replace with** can use `\1` or `\g<name>`.

## Isolate Selection

View > Isolate Selection ([[Ctrl+Alt+I]]) hides everything but the selection, in the editor only.
Run it again, or click the hidden chip in the status bar, to bring the rest back.

## Undo and the saved state

Edit > Undo ([[Ctrl+Z]]) and Redo ([[Ctrl+Y]]) name the step they act on. Undoing back to the
saved state clears the unsaved mark. After Undo or Redo, the changed objects are selected,
scrolled into view and briefly flashed.

## History panel

View > Panels > History ([[Alt+5]]) lists the undo steps, oldest first. The current state is
bold, steps you can redo are italic, and the saved state is marked **Saved**.

- Click a row to go to that state. Nothing is lost: Redo brings later steps back.
- Right-click for **Go to This Step**, **Select Objects of This Step** and **Copy Steps**.

## Mark Changes Since Save

View > Mark Changes Since Save marks what differs from the saved file: new objects in green,
changed ones with an amber outline, removed ones as a red dashed outline. A status bar chip counts
them; click it to go to the next. File > Page History > Compare compares with an earlier version
instead (see [Earlier versions of a page](opening-and-saving.md#earlier-versions-of-a-page)).

View > Next Change ([[Alt+F5]]) and Previous Change ([[Alt+Shift+F5]]) step through them. View >
Select All Changed selects them.

## Changes panel

View > Panels > Changes lists each new, changed or removed object with what changed. Changes to
the page itself are one **Page** row. Double-click a row to go to the object.

## Focus Mode and resetting the layout

View > Focus Mode hides the menu bar ([[Alt]] shows it). View > Reset UI Layout restores the
default panels and ribbon. View > Refresh rereads the page's libraries and redraws.

> **Coming from the Graphics Builder:** the toolbox is the ribbon's Design tab (the floating one
> is still on [[F3]]); Tools > Options is Tools > Settings; pages are tabs rather than windows.
> The magnifier, cross hair, grid and guidelines work the same way. To match the Graphics
> Builder's look, set Outside the Page to Page colour, turn on Grid outside the page, and turn
> off Page shadow.
