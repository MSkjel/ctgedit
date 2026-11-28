# Troubleshooting

## A page is not saved

A save writes to a temporary file first, so a failed save leaves the page on disk as it was and
your changes stay in the editor. The message says what went wrong, and offers **Save As…** when
that would help:

- **The file is read-only**: clear the attribute in Explorer, or use Save As.
- **The file is locked by another program**: the message names it when it can. Close the page
  there and save again.
- **The disk is full**: free space, or save elsewhere.
- **The page database is locked** (Studio has a database view open, or a compile is running):
  close it or wait, then save again.

A leftover `.ctgedit-tmp` file next to a page is from an interrupted save and can be deleted.

## The runtime or the Graphics Builder does not show my change

- **Not the active install**: if the status bar shows the project in amber with "(not the active
  install)", you're editing a copy Plant SCADA doesn't use.
- **Not compiled**: the runtime only shows changes after a compile. Use File > Run in Plant SCADA ([[F5]]) or
  File > Compile Project ([[Alt+F10]]).
- **The Graphics Builder still has the old page**: close it there without saving and reopen it
  ([Working alongside the Graphics Builder](graphics-builder.md)).
- **The Graphics Builder shows an old library element**: close it with Tools > Close Graphics
  Builder ([Close the Graphics Builder](graphics-builder.md#close-the-graphics-builder)).

## A genie, symbol or template is missing or empty

The page refers to a library element that can't be found, usually because the project holding it
isn't included. Include it in Plant SCADA Studio, or relink the object (see
[Symbol linkage](library-elements.md#symbol-linkage)).

Such an object draws as a white box with a cross, as in the Graphics Builder. Hover over it for
the element's name.

When the page opens, a template or genie with a missing library becomes a placeholder, as the
Graphics Builder does: one object that keeps the library name, position and parameter values.
Undo brings the original back, and Settings > Pages and libraries can turn this off. Once the
library is found again, the next open or Update Pages places the genie again.

## Tags or equipment are missing from a list

Tag, equipment, trend tag and function lists come from the page's project, its includes, and the
projects that include it. If a tag is missing, check its project is in that chain. See
[The Tags panel](tags-panel.md#which-tags-are-listed).

## Symbols not found

When library references don't resolve, the status bar shows an amber **⚠ N symbols not found**
chip. Click it for each reference, its library, the reason, and how many objects use it:

- **library not found on the search path**;
- **element not in the library**;
- **element could not be read** (the log has the error).

The folders searched are listed below, in order. **Copy** copies it all for a report.

## Check Page

Tools > Check Page ([[Ctrl+Shift+K]]) lists what would go wrong when the page is compiled or run,
in the **Page Checks** panel. The Cicode is compiled by the simulator, as the page compiler
would, running nothing:

- expressions and commands that don't compile;
- tags the project doesn't define, also a tag name given as text, such as `TagRead("Pump9_PV")`;
- pages the project doesn't have that the page opens, such as `PageDisplay("Mixer9")`;
- buttons without commands, numbers without expressions, empty symbol sets;
- objects entirely off the page;
- button captions wider than the button;
- symbols or genies whose element wasn't found;
- duplicate key sequences;
- a Hidden when or Disable when that is a constant, such as `1` or `FALSE`;
- a Multi-state fill colour or display value with a condition left empty before a later one,
  or the same condition twice: some of its states can never show;
- a Hidden when the same as its group's;
- an object that takes clicks lying entirely under another that takes them, so that a click
  never reaches it (see [Click map](#click-map));
- a placed genie's input left empty where its objects' Cicode cannot work without it, such as
  `%Equip%.Value` or `%Level% > 50`;
- on Situational Awareness pages, the [workspace checks](situational-awareness.md#workspace-checks).

Double-click a row to go to the object; **Properties…** opens where the field is set. **Check
Again** reruns the checks. Checks never change the page.

## Preview states

View > Preview States ([[Ctrl+Shift+J]]) shows the page as the runtime would for values you
choose, without running anything. The **Preview States** panel lists each condition and
expression of the page's animated objects once, however many objects use it:

- a condition (Hidden when, an On / Off or Multi-state colour, an On / Off text) is a check box:
  ticked is TRUE;
- an expression that moves, turns, sizes or fills an object, or picks a Threshold or Gradient
  colour, takes a percent of its range: click the value and drag the slider, and the page
  follows;
- an Array colour, text or symbol takes an index; a numeric or string display takes the text it
  shows.

The page draws each object for the values set, with the runtime's rules: Hidden when hides it,
Disable when greys it in its disabled style, the colours, texts and symbol sets show their
state. A condition you have not set leaves its part of the object as the page stores it. Expand
a row to see the fields it is set in; double-click one to select its object, or right-click a
row for **Draw as Stored** and **Select Objects**. **Selected objects** lists only the conditions
of the selection; **Reset** draws the whole page as stored again. The values are kept for each
open page while you edit it. Closing the panel ends the preview.

## Click map

View > Click Map shades what takes a click on the page: a touch command, a button or a slider.
An object that only takes keys is outlined with a dashed line. Point anywhere for where a click
there goes, what it runs, and what lies under it.

The runtime gives a click to the topmost object under the pointer that takes one; an object
without commands lets the click through. So an object that takes clicks hides the ones under it:
the part of an object another one covers is left clear, and one covered entirely is marked red,
since a click never reaches it. An object over it with a Hidden when covers it only while it
shows: then nothing is marked red, but the tip says what lies over it. With Preview States open,
the map follows the states set: hidden objects take no clicks. The page's Cicode can hide an
object too (`DspShow`), which the map cannot know.

## Check Project

Tools > Check Project… runs Check Page on every page of a project (and, with **And the projects
it includes**, of those too), as the pages are saved, in a separate process. It also compiles the
Cicode of each page's properties (On page entry and the other events), the menu configuration's
commands, the keyboard commands and the project's Cicode files. Click **Check** to start; **Stop**
ends it. **Show** filters the list; double-click a row (or **Go To**) to open its page at the
object.

## Pack the project database

Deleting, renaming and saving pages leaves deleted rows in the project's database tables until
they are packed. Tools > Pack Database… removes them and builds the tables' indexes again, as
Studio's Pack does, in a separate process; the results window lists what each table gave back.
Close Plant SCADA Studio and the Graphics Builder first. A table the project's change log still
names keeps its rows until the project is compiled. A table over 2 GB is one Studio cannot open
at all: Pack Database packs it.

## Check the page display

If art that should be there doesn't show, the display may be out of date. Tools > Check Page
Display ([check it now](action:check_display)) reads the page again in a separate process and
compares. Differences go to the activity log, and an amber **⚠ Display may be out of date** chip
appears; click it (or use View > Refresh) to redraw. Settings > Advanced can run this check after
every open.

## The activity log

Help > Open Log Folder shows `%LOCALAPPDATA%\ctgedit\logs`. `ctgedit.log` records every save,
every page open (what it placed, what didn't resolve and where libraries came from), every
automatic change, tab switches and closes, and errors the editor skipped while reading.

## My change to a genie's object was undone

Objects inside a placed genie are rebuilt from the genie's definition whenever the page opens, so
they're read-only. Change the genie's parameters, or edit its definition and run Update Pages. The
same goes for composite genies: change their options.

## A page opens as damaged

When a page file is incomplete, ctgedit opens what it can and says so. Saving asks first:
**Overwrite** or **Save As…** to keep the original.

## Recover unsaved changes

ctgedit keeps recovery copies of unsaved pages (Settings > Editing sets how often). If it didn't
close normally, the next start offers **Recover Unsaved Changes**:

- **Restore Selected** opens the copies as unsaved changes;
- **Discard Selected** deletes them;
- **Decide Later** keeps them for next time.

If ctgedit stopped mid-way through a project database update, it rolls that back on the next start
and lists the pages to save again (**Open These Pages**).

## Restore a backup

Update Pages, Pack Libraries, Replace All on closed pages, Delete Page and Duplicate Pictures back
up every file they rewrite to `%LOCALAPPDATA%\ctgedit\backups` first. Their results have **Open
Backup Folder** ([The summary](update-pages.md#the-summary)). Tools > Restore Backup… lists the
backups; **Restore** puts the files back, including the pages' database rows. ctgedit keeps the
last 10 backups for up to 30 days (Settings > Editing).

Every failure message has **Open Log**, and **Copy Details** for a report.

## Large pages feel slow

Pages with thousands of objects still open in a second or two and edit quickly. The animation
point count in the status bar turns amber above 3000, the Graphics Builder help's rule of thumb.
Split the page if it gets that large.

## Report a problem

- Help > About ctgedit… shows the version. **Copy Details** copies version and system details;
  **Open Log Folder** shows crash reports and logs.
- Help > Copy Diagnostics ([copy them now](action:copy_diag)) copies the version, open documents,
  recent activity, skipped errors and the end of the logs. Paste it into your report.

When a command fails, ctgedit shows "Command failed" and logs the details. Include the log in your
report.
