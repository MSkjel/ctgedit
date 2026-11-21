# Opening, creating and saving pages

## The page browser

File > Open… ([[Ctrl+O]]) opens the page browser. The same window is used for Save As, new pages,
and picking templates or library elements.

- **Left**: projects. **Recent pages** lists the pages you opened last; **Active install** lists
  every page of the install.
- **Middle**: tabs for **Pages**, **Templates**, **Symbols**, **Genies** and **Super Genies**
  ([[Alt+1]] …), a search box, and the results as thumbnails or a list ([[Ctrl+1]] /
  [[Ctrl+2]]).
- **Right**: a preview with the page's size, template, title and object count. **Live render**
  (or [[Space]]) draws the page itself instead of its stored thumbnail.

### Tree or flat project list

The buttons above the project list switch between **Tree** (projects nested under the projects
that include them) and **Flat** (every project once, with who includes it). Tick **With included
projects** to list a project's pages together with its includes'.

### Search and filters

Search matches names, titles, comments, areas, projects and libraries; every word must match.
Letters in order also work (`mtr` finds `motor`). Field filters: `tpl:normal`, `res:HD1080`,
`proj:Example`. A lone `!` shows only system pages.

The list next to the search shows all pages, hides system pages, or shows only them.

Pages not registered in their project are marked "Not registered"; a button above the results
hides or shows them.

### Open

Double-click a page, or select it and click **Open**. Templates and library elements open for
editing in their own tab (see [Editing symbols, genies and templates](library-elements.md)).
**Browse…** opens a `.ctg` from anywhere with the Windows file dialog.

Right-click a result to copy its path or name, show it in Explorer, or rename or delete an
element.

When the browser picks something for a page, it only lists what that page can use.

> **Coming from the Graphics Builder:** File > Find is the browser's search box. The browser shows
> every project and its includes together, rather than one at a time.

## Create a page or library element

File > New… ([[Ctrl+N]]) opens the browser in New mode. Choose **Page**, **Template**,
**Symbol**, **Genie** or **Super Genie**, and the project. The bottom bar says what's still needed
before **Create** works. It remembers what you used last.

### A new page

1. Choose the **Style**, **Resolution** and **Designed for showing title bar**, and click a
   template.
2. Leave **Linked** ticked to keep the template's objects locked and following the template.
   Cleared, they become ordinary objects.
3. Type the **Page name** and click **Create Page**. **Open Template to Edit…**, under the
   options, closes the window and opens the template itself instead.

The page opens in a new tab. Nothing is written until the first save, which also registers the
page in its project. See [Page templates](templates.md).

### A new template, symbol, genie or super genie

Choose the **Library** (or **New Library**) and type a **Name** (at most 31 characters). The
element opens empty; draw it and save. It needs at least one object. Start a super genie's name
with `!`. See [Editing symbols, genies and templates](library-elements.md) and
[Super genies](super-genies.md).

## Save a page

File > Save ([[Ctrl+S]]) writes:

- the page (`.ctg`), with a new thumbnail;
- the fast-display file (`.ctF`), or removes the old one if it can't be rebuilt exactly;
- the page's rows in the project database;
- any Page Properties changes.

Like the Graphics Builder, a save measures texts again in this computer's fonts, so a page made
elsewhere can shift by a pixel or two on its first save here.

The save writes to a temporary file first, so a failed save leaves the old page intact. The old
version is kept (see [Earlier versions of a page](#earlier-versions-of-a-page)). If the runtime
is showing the page, it's told to reload.

ctgedit asks first when:

- the page changed on disk since you opened it;
- it's open in the Graphics Builder (see
  [Working alongside the Graphics Builder](graphics-builder.md));
- it was damaged when opened: **Overwrite** or **Save As…**.

Read-only pages are saved with Save As.

File > Save All saves every changed page and element in every window, with progress and
**Cancel**.

## Save As

File > Save As… ([[Ctrl+Shift+S]]): pick the project and type the name. The name is checked as you
type (length, forbidden characters, reserved names, code page). **Browse…** saves outside the
install. The copy is registered with the page's properties and rows.

## Export a picture of the page

File > Export Page… saves the page as PNG (at 100 to 300 %), SVG or PDF, without editor marks.

## Close, revert and recent pages

- File > Close Page ([[Ctrl+W]]) and Close All Pages ([[Ctrl+Shift+W]]).
- Window > Close Others, Close to the Right and Close Saved. Pinned tabs stay (see
  [Document tabs](the-window.md#document-tabs)).
- Window > Reopen Closed Page ([[Ctrl+Shift+T]]).
- File > Revert to Saved… discards unsaved changes and reloads from disk.
- File > Page History… (see below).
- File > Recent Pages.
- File > Print… ([[Ctrl+P]]) prints the page fitted to the paper.
- File > Copy Path, Copy Name, Show in Explorer and Show in Page Browser….

## Earlier versions of a page

Each save keeps the previous version first, in `%LOCALAPPDATA%\ctgedit\history` (never in the
project). File > Page History… lists them, newest first, with a picture. For a version:

- **Open Read-only** opens it in its own tab to look at or copy from. Save on that tab is Save As.
- **Compare** marks the differences on the canvas and in the
  [Changes panel](the-window.md#changes-panel).
- **Underlay** draws it faded under the page (see [The underlay](underlay.md)).
- **Restore…** puts it back as unsaved changes.

Settings > Editing > Safety sets how many versions to keep (10), how much space they may use
(200 MB), and how often a new one is kept. **Clear…** removes them all.

## Unsaved changes

Closing a page with unsaved changes asks **Save**, **Don't Save** or **Cancel**. A page changed
only by automatic updates says so, and defaults to **Don't Save**.

When several pages close at once, one list shows them all with what changed. Pages changed only by
automatic updates start unticked (see [When a page opens](settings.md#pages-and-libraries)).

- **Save All** saves every page. With some unticked, the button says what it does, for example
  **Save 1, Discard 1**: it saves the ticked pages and closes the rest without saving. It stops at
  the first failure. Enter does not press it then: Enter is Cancel.
- **Don't Save** closes them all without saving.
- **Cancel** closes nothing.

## Opening where you left off

Pages open at the zoom and position you left them (Settings > Canvas > **Open a page at: Where I
left it**). A small page fitted on opening isn't zoomed in past **Largest zoom when a page
opens** (200 %).

On start, ctgedit reopens the last session's tabs. Settings > Pages and libraries > **On start**
can show the start page or nothing instead.

When another program changes an open page, ctgedit shows a notice with **Reload** or **Ignore**
(or **Reload and Discard Mine** / **Keep Mine** if you have unsaved changes). Settings can reload
automatically or use a question box instead.

When another program saves a library an open page uses, ctgedit redraws the page and updates its
symbols and genies about a second later. Each update can be undone, and waits while you're in the
middle of something. ctgedit's own library saves update open pages as they're saved (see
[Genies](genies.md)).

## Notices over the page

Lasting states of a page show in a bar over the canvas:

- read-only (**Save As…**);
- made from a super genie (**Edit Super Genie**);
- open in the Graphics Builder too (**Check Again**);
- damaged when opened (**Save As…**, **Details**);
- changed by another program (**Reload**, **Ignore**);
- updated automatically when it opened (**Undo**).

Each page keeps its own notices. They also go to the bell (see
[Messages and tasks](the-window.md#messages-and-tasks)).

## Delete a page or library element

In the page browser, select the page and click **Delete…**:

- **Delete Page and Files** removes the files and the database rows.
- **Remove from Project Only** removes the rows and compiled page but keeps the `.ctg`.

The page is backed up first; **Tools > Restore Backup** puts it back. Compile the project
afterwards. Deleting a library element works the same way and lists the pages that use it first.

See the AVEVA help for [creating](aveva:1172099) and [opening or saving](aveva:1172100) pages.
