# Getting started

This page takes you from starting ctgedit to seeing a changed page in the runtime.

## Start ctgedit

Start ctgedit from its shortcut, or from a command prompt:

- `ctgedit` reopens the pages you had open last time (each in its own tab). With none, it shows
  the start page.
- `ctgedit MyPage.ctg` opens that page. If ctgedit is already running, the page opens there
  instead of in a second copy. To get a window per start, turn off Settings > Pages and
  libraries > "Open pages in the ctgedit already running". To also get back the pages you had
  open, turn on "A page opened from Explorer also reopens the last pages".
- `ctgedit --new-instance` starts a separate editor.

If ctgedit did not close normally last time, it first offers the unsaved changes it kept
(see [Recover unsaved changes](troubleshooting.md#recover-unsaved-changes)).

## The start page

With no page open, the canvas shows the start page: thumbnails of your recent pages, **Open
Page…** ([[Ctrl+O]]) and **New…** ([[Ctrl+N]]). Click a thumbnail to open its page.

## Open a page

1. Choose File > Open… ([[Ctrl+O]], or [open it from here](action:open)). The
   [page browser](opening-and-saving.md#the-page-browser) opens on the projects of the active
   install.
2. Pick the project on the left, then the page. Type in the search box to find a page by name,
   title or comment.
3. Click **Open** (or double-click the page).

The page opens in a tab. The status bar shows the install and project it belongs to, the
number of animation points on the page, the selection, the pointer position and the zoom.

## Make a change

1. Click an object to select it. Drag it to move it; drag a handle to resize it.
2. Double-click the object to open its properties (a genie opens its parameters).
3. To draw, pick a tool on the Design tab of the ribbon (or press its letter over the canvas:
   [[R]] rectangle, [[T]] text, [[L]] line) and drag on the page.

Every change can be undone with [[Ctrl+Z]] and redone with [[Ctrl+Y]].

## Save

Choose File > Save ([[Ctrl+S]]). ctgedit writes the page, its fast-display file and its rows
in the project database, so the compiler and the runtime see the change. The status bar says
what the save did. See [Opening, creating and saving pages](opening-and-saving.md#save-a-page).

> **Warning:** if the page is also open in the Graphics Builder, ctgedit asks first. The
> Graphics Builder keeps its own copy in memory, and saving the page there later overwrites
> your change. Close the page in the Graphics Builder without saving, or open it there again
> after saving here.

## Compile and run

- File > Compile Project ([[Alt+F10]]) runs the Plant SCADA compiler on the project and lists
  its messages.
- File > Run in Plant SCADA ([[F5]]) saves, compiles and shows the page in the runtime. When the runtime is
  already running this project, it shows the updated page without a restart.

See [Compile and run](compile-and-run.md).

## Updates

ctgedit looks for a new version on GitHub when it starts, at most once a day. When there is one,
the status bar says so: click it to see what is new. Help > Check for Updates looks at any time.

**Install** downloads the installer and checks it. ctgedit then closes, asking about unsaved
pages first, installs the new version and opens again. **Skip This Version** stops the notice
for that version. Turn the check off in Settings > Advanced > Updates.

## Next steps

- [The editor window](the-window.md): the ribbon, the panels and the canvas.
- [Drawing objects](drawing.md) and [Object properties and animation](properties.md).
- [Symbols and genies: the Library panel](library-panel.md).

> **Coming from the Graphics Builder:** you do not choose a project first. The page browser
> shows every project of the install, and each page works with its own project and the projects
> it includes. Several pages open as tabs of one window; Window > New Window opens another
> window.
