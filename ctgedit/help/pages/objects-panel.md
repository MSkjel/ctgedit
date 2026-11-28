# The Objects panel

The Objects panel (View > Panels > Objects) lists the page's objects as a tree, in drawing order
(first drawn at the top), with each group, genie or symbol's parts under it.

## Reading a row

`pump_b  D100  Genie (1)                    AN 588`

- First, the object's name if it has one; otherwise a genie's element and bound tag, a text's
  caption, an expression, or a symbol's element.
- Then, dimmer: its kind and how many objects it holds, the first tag it reads, `[touch, 2 keys]`
  for input commands, and a picture's size.
- At the right: its AN.

The tool tip has the full details. Template objects are grey; large pictures are amber.

A genie nested inside another genie is named by its own element. For an object inside nested
genies, the tool tip shows the tree from the outermost genie down. Each level shows its element
and the values it gets. Each level's values are worked out from the level above, as the
Graphics Builder does when it opens the page: they are the values the objects inside it use. To
see where each value goes, select the genie and open **Inputs** in the Properties panel.

Settings > Appearance > Objects panel can put the kind and AN first instead. Right-click a row or
the header > **Columns** to add Name, Kind, AN, Element, Tag, Parameters and Position columns.

## Selecting

Selecting a row selects the object on the page and vice versa. [[Shift]] and [[Ctrl]] select
several. This is how you reach an object inside a group or genie; the Properties panel then shows
it (read-only for genie members).

Double-click a row to zoom to the object. Settings > Appearance can make it scroll, open the
properties or rename instead.

Selecting on the page scrolls the panel to the rows and expands their parents. Settings >
Appearance can turn that off.

## Renaming

To name a top-level object (or one in a plain group):

- select its row and press [[F2]];
- click the name of an already selected row and wait a moment; or
- right-click > **Rename…**.

[[Enter]] applies as one undo step, [[Esc]] cancels, and an empty name removes it. Invalid or
clashing names are flagged and the field stays open. Objects inside placed genies, symbols and the
template can't be renamed here. To rename many at once, use Edit > Rename Objects…
([[Ctrl+Alt+N]]).

## The eye and the padlock

Point at a row to see an eye and a padlock.

- Click the **eye** to hide the object in the editor. Hidden rows are dim and slanted.
- [[Alt]]+click an eye to show only that object; again to show everything.
- Drag down over several eyes to set them all the same way.
- Click the **padlock** of a top-level row to lock or unlock it (saved in the page). Template
  rows show a fixed padlock.

View > Isolate Selection hides everything else; View > Show All Hidden Objects, or "N hidden
[show]" in the status bar, shows it all again.

Hiding is for editing only: hidden objects are still saved and shown at runtime. ctgedit
remembers what you hid on each page.

## Search and filter

Type in the search box to list matching objects with their parents. It matches the row text plus
names, genie bindings (`Tag=D100`), library elements, text, expressions and commands. **Select
N** (or [[Enter]]) selects the matches.

Fields narrow the search:

| Word | Lists |
|------|-------|
| `name:Pump` | objects whose name has 'Pump' |
| `tag:Mixer_` | objects bound to, or reading, a tag with 'Mixer_' |
| `an:581` | the object of AN 581 |
| `kind:genie` | genies (also `kind:text`, `kind:symbol` ...) |
| `lib:valve` | objects of a library element with 'valve' |
| `is:hidden`, `is:locked` | hidden or locked objects (also `is:named`, `is:animated`) |

The `.*` button switches to regular expressions.

The funnel button filters by **Kinds** and **States** (hidden, locked, from the template,
animated, has commands, large pictures, named, selected only). **Clear Filters** resets it. The
filter is remembered between sessions.

## Keys in the panel

| Key | Does |
|-----|------|
| [[Enter]] | a top-level row: what a double-click on the page does; a member: its own properties |
| [[Shift+Enter]] | selects the parent |
| [[Space]] | hides or shows the selected objects |
| [[F2]] | renames |
| [[F]] | zooms to the selected object |
| [[Ctrl+A]] | selects the listed rows |
| [[Delete]] | deletes the selected objects |
| [[Up]], [[Down]] | move the selection |

Typing letters starts a search; [[Esc]] clears it.

## The right-click menu

The same commands as on the canvas: Object Properties, Genie Parameters, Edit Definition, Rename,
clipboard, drawing order, Group and Ungroup, Lock, Isolate Selection and Zoom to Selection. For
genie members, order, Cut and Delete are off.

> **Coming from the Graphics Builder:** this panel replaces Tools > Goto Object's tree for
> everyday work (Goto Object is still on [[Ctrl+G]]). Hiding objects is ctgedit's own and never
> changes the page.
