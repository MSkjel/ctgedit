# Selecting, moving and arranging

## Select objects

With the Select tool ([[V]]):

- Click an object to select it. Groups, genies and symbols select as one unit. The object under
  the pointer is outlined before you click.
- [[Shift]]+click or [[Ctrl]]+click adds or removes an object.
- Drag on empty space for a selection box. The ribbon's **Box** list sets what it takes:
  Enclose (fully inside), Touch (anything it crosses) or Auto (right-to-left touches,
  left-to-right encloses). The objects it will take are outlined as you drag.
- [[Shift]]+drag adds to the selection, [[Ctrl]]+drag toggles. [[Esc]] before letting go
  restores the old selection.
- [[Shift]]+drag starting on an object also makes a box, which helps on pages built on a large
  background panel. Right-click a large filled rectangle or picture > **Lock as Background** so
  every drag on it makes a box.
- [[Alt]]+click cycles through overlapping objects under the pointer. Right-click where objects
  overlap for a **Select** list.
- Edit > Select All ([[Ctrl+A]]) selects everything editable; [[Esc]] deselects.
- The ribbon's **Filter** list limits clicks to one kind of object.

To select inside a group or genie, use the [Objects panel](objects-panel.md), or
[[Ctrl]]+double-click to open the inner object's own properties.

### Select Same, Invert Selection, Select Previous

Edit > Select also has:

- **Invert Selection** ([[Ctrl+Shift+I]]).
- **Select Same**: objects like the last selected one, by **Library Element** ([[Ctrl+Alt+A]]),
  **Kind**, **Fill Colour**, **Line Colour**, **Line Width**, **Font and Size**, **Size** or
  **Animation Expression**. Right-click a genie > **Select All Instances of** does the same for
  its element.
- **Select Objects Using Tag…**: objects whose expressions, commands or genie parameters name a
  tag (`LOOP_1` doesn't match `LOOP_10`).
- **Select Previous** ([[Ctrl+Alt+Shift+A]]): the selection before the last change.

These skip locked, template and hidden objects.

### Select with the keyboard

- [[Tab]] and [[Shift+Tab]] step through objects in reading order (or drawing order; Settings >
  Canvas). Past the last object, [[Tab]] goes on to the next panel with that object still
  selected, so its properties are next. [[Ctrl+F6]] goes to the panels at once.
- [[Ctrl+Alt]] + an arrow selects the nearest object in that direction.
- [[Enter]] does what a double-click does. [[Shift+Enter]] selects the parent group or genie,
  [[Ctrl+Enter]] the first object inside it.
- [[Shift+F10]] or the Menu key opens the context menu.

Objects selected from a panel are scrolled into view.

### The selection toolbar

A small toolbar next to the selection has Properties, Group, Bring to Front, Send to Back and
Delete. Settings > Ribbon changes its commands; **View > Selection Toolbar** turns it off.

## Move

- Drag the selection. It snaps to the grid ([[F8]]), guidelines and, with Smart Guides, other
  objects. Dragging to the canvas edge scrolls. [[Esc]] before letting go cancels.
- [[Alt]]+drag moves without snapping.
- [[Shift]] while dragging keeps the move horizontal or vertical.
- [[Ctrl]] when you let go drops a copy instead.
- Settings > Canvas can switch to design-tool modifier keys and copy with [[Alt]]:

| While you drag | Default (Graphics Builder keys) | Design tools keys | Copy with Alt |
|---|---|---|---|
| Keep horizontal or vertical | [[Shift]] | [[Shift]] | [[Ctrl]] ([[Shift]] with the design keys) |
| Drop a copy | [[Ctrl]] | [[Ctrl]] | [[Alt]] |
| Move without snapping | [[Alt]] | [[Alt]] | [[Alt+Shift]] |

- Arrow keys move one step; [[Home]], [[Page Up]], [[End]] and [[Page Down]] move diagonally.
  [[Ctrl]] + arrow moves one step, one large step or one grid step (Settings > Editing). Quick
  presses join into one undo step.
- With nothing selected, arrow keys scroll the page.

## Resize

- Drag a handle. Groups and genies resize as a whole. [[Esc]] cancels.
- [[Ctrl]] keeps proportions; [[Shift]] resizes about the centre. With design-tool keys,
  [[Shift]] keeps proportions and [[Alt]] works from the centre.
- On thin objects the handles move outside, so dragging the middle still moves it. Handle size
  and colour are in Settings > Appearance.
- With several objects selected, one box surrounds them all. Dragging its handles scales the
  whole selection.
- [[Shift]] + arrow resizes from the top-left corner; [[Ctrl+Shift]] + arrow keeps proportions.
- Text resized evenly changes point size. Resized unevenly it stretches, as in the Graphics
  Builder. Settings > Editing can make it keep its shape instead.

## Reshape lines, polygons and pipes

Select a single line, polygon, polyline or pipe to see its nodes.

- Click a node to select it; [[Ctrl]]+click to add or remove.
- Drag nodes, or move them with the arrow keys.
- [[Insert]] or [[+]] adds a node on the outline at the pointer.
- [[Delete]] deletes selected nodes. Polygons and pipes keep at least two.

Nodes can be animated on the Vertices tab ([Object properties](properties.md#vertices)).

## Delete

Edit > Delete ([[Delete]]). The Graphics Builder also deletes with the minus keys ([[-]], [[Num -]]);
Settings > Keyboard can give Delete those keys, and then they work on the canvas only. Settings > Editing can
ask for confirmation.

## Cut, copy, paste and duplicate

Cut, Copy and Paste ([[Ctrl+X]] / [[Ctrl+C]] / [[Ctrl+V]]) work across tabs and windows. Edit >
Duplicate ([[Ctrl+D]]) copies next to the original.

- Duplicate repeats the last offset: duplicate, move the copy, then each [[Ctrl+D]] continues at
  the same spacing. Handy for rows of equipment.
- [[Ctrl]]+drag copies to where you let go (see [Move](#move)).
- Copies get new ANs. A genie that refers to its own AN is updated, and clashing names get `_1`,
  `_2` and so on.
- Copied genies and symbols stay linked to their library.
- Paste lands next to the original, or at the pointer (Settings > Canvas).
- Pasting a picture from another program makes a bitmap (see [Pictures and bitmaps](bitmaps.md)).

## Paste special

When the clipboard holds both ctgedit objects and a bitmap, Edit > Paste Special… asks which to
paste.

## Copy an appearance, and styles

Edit > Copy Appearance ([[Ctrl+Alt+C]]) copies an object's look; Paste Appearance
([[Ctrl+Alt+V]]) applies it to the selection, taking whatever fields fit.

Edit > Styles keeps named looks. **Save Appearance as Style…** names one; styles then appear in the
menu, the ribbon and the Properties panel. See [Named styles](colours.md#named-styles).

## Lock objects

Locked objects can't be selected, moved or changed. Template objects are locked too.

- Edit > Lock or Unlock Selection ([[Ctrl+L]]) toggles. With nothing selected it unlocks the
  object under the pointer.
- Edit > Unlock All Objects ([[Ctrl+Alt+L]]).
- Right-click a locked object to unlock it.
- Edit > Break Lock Mode ignores locks while it's on.

The quickest way to toggle one object is its padlock in the Objects panel (see
[The eye and the padlock](objects-panel.md#the-eye-and-the-padlock)).

## Rename objects

An object's name is how Cicode finds it. Edit > Rename… renames one in the Objects panel (see
[Renaming](objects-panel.md#renaming)). Edit > Rename Objects… ([[Ctrl+Alt+N]]) renames several:

- **Pattern**: `Valve_{n:02}` gives Valve_01, Valve_02 and so on. Fields: `{n}`, `{name}`,
  `{element}`, `{kind}`, `{an}`. Set the start, step and order.
- **Find and Replace**, optionally with a regular expression.

Invalid or clashing names show in red and block **Rename**. Names from genies, symbols or the
template are skipped.

Edit > Hide or Show Selection ([[Ctrl+Shift+H]]) hides objects in the editor only. View > Show All
Hidden Objects brings them back.

## Group and ungroup

Arrange > Group Objects ([[Ctrl+Shift+G]]) and Ungroup Objects ([[Ctrl+Shift+U]]). Groups can have
their own visibility, movement, fill, input and access. To break a genie or symbol into plain
objects, use Cut Link (see
[Editing symbols, genies and templates](library-elements.md#cut-link)).

## Change the order

Later objects cover earlier ones. Arrange > Bring to Front (`Ctrl+Shift+]` or [[Alt+Home]]), Bring
Forward (`Ctrl+]` or [[Alt+PgUp]]), Send Backward (`Ctrl+[` or [[Alt+PgDown]]) and Send to Back
(`Ctrl+Shift+[` or [[Alt+End]]). The Alt keys work on every keyboard layout. Objects never step
past the page template.

## Align objects

- Arrange > Align Left ([[Alt+Shift+Left]]), Align Horizontal Centres ([[Alt+Shift+C]]), Align
  Right ([[Alt+Shift+Right]]), Align Top ([[Alt+Shift+Up]]), Align Vertical Centres
  ([[Alt+Shift+M]]) and Align Bottom ([[Alt+Shift+Down]]). Settings > Editing can align to the
  last selected object or the page instead.
- Arrange > Align… ([[Ctrl+Shift+A]]) does both directions at once, with **Even (centres)** spacing
  for three or more objects. **Align to** at its top shows and changes what the objects line up
  with: the selection, the object selected last, or the page. Nothing moves until **Apply** or
  **OK**.
- Arrange > Distribute Horizontally ([[Alt+Shift+H]]) / Vertically ([[Alt+Shift+V]]) spaces
  centres equally.
- Edit > Repeat ([[F4]]) repeats the last arrange command on the current selection.
- Arrange > Space Equally… makes the gaps between edges equal, or sets a fixed gap.
- Arrange > Arrange in Grid… lays the selection out in rows and columns.
- Arrange > Make Same Size uses the last selected object's size.

## Rotate and flip

Arrange > Rotate 90° Clockwise ([[Ctrl+Shift+R]]), Rotate 90° Anticlockwise ([[Ctrl+Shift+L]]),
Flip Horizontal ([[Ctrl+Shift+M]]) and Flip Vertical ([[Ctrl+Shift+V]]) turn or mirror about the
centre. The round grip above the selection turns it in quarter turns with the mouse.

A flipped text keeps the flip as a matrix, as in the Graphics Builder.

## Exact position and size

Arrange > Adjust Position and Size… ([[Ctrl+Shift+P]]) sets the top-left position and the size, by
percentage or in pixels. The window stays open and follows the selection.

## Go to an object

Edit > Goto Object… ([[Ctrl+G]]) shows the page's objects as a tree. Type an AN or the start of a
name in **Object AN** (use `Group.Name` for names inside groups). **Go To** selects and zooms to
the object; **Properties…** opens it. **Columns** adds more detail, such as position, fill or
animations, and makes those searchable too.

## Find and replace

Edit > Find… ([[Ctrl+F]]) and Replace… ([[Ctrl+H]]) open one window that stays open.

1. **Find what** (not case-sensitive) and **Replace with**.
2. **Look in**: this page, all open pages, or all pages of a project and its includes. Closed
   pages are searched in the background by helper processes. **Replace All** on closed pages asks
   first, backs each page up, and saves it.
3. **Options** (closed at first; closed, its line names any choice that isn't the default):
   - **Search in**: text, expressions and commands, genie parameters, names and metadata, page
     properties.
   - **Include**: objects inside genies, symbols and the page template.
   - **Match**: anywhere, whole names and words, or whole fields. **Direction** for Find Next.
   - **Filters**: **Add Filter** adds a row: what it looks at (pages, fields, genies and
     symbols, or objects), **match** or **do not match**, and the text. The text is found
     anywhere in the name, not case-sensitive; with its .* on it is a regular expression (^ and
     $ anchor it). Two match rows of one kind keep what either matches. A field is named by its
     key or the name in the Field column (`CMD`, `Command`), a genie parameter by its name
     (`^Tag$`), a genie or symbol as library.element (`pumps.pump_`), an object by its name or
     AN (`AN12`) or the group or genie it is in. A page a filter leaves out is never opened.
     For example, Find what `.+` (with .* on), Fields matching `^Tag$` and Genies and symbols
     matching `fx_pump` lists the Tag of every fx_pump genie.
4. **Find All** lists matches, with a **Becomes** column when replacing. **Find Next** ([[F3]]),
   **Replace**, **Replace All**, **Go To** and **Properties…** work from there.

Text from a genie parameter is changed through that parameter. Rows that can't be replaced are
grey, with the reason in the tool tip.

After a Replace All, **Undo Replace All** beside the result line takes it all back: the change in
each open page, and the closed pages it saved, from their backup. A page you changed again since
is left as it is, and named.

## Rename pages

Tools > Rename Pages… renames a project's pages, one or many at once. In the Open dialog,
Rename… on a page opens it with that page ticked.

1. Pick the **Project** (and, as in Find and Replace, **And the projects it includes**), then
   tick the pages. **Show** filters the list (its .* makes it a regular expression), and **Only
   ticked** shows the ticked ones alone.
2. Name them with a **Pattern** (`{name}`, `{title}`, `{n}` the number in the list, `{n:02}`
   with two digits) or **Find and Replace** in their names (the .* in Find makes it a regular
   expression, and `\1` in Replace with puts in a group). **New name** shows the result; a name
   that can't be used, also one a page of a project compiled with it has, is red, with the
   reason in its tool tip. Rename Objects names objects the same way, and both remember what
   you used last.
3. **Rename** renames each page's files and its rows in the project database. **Update the pages
   that link to them** also changes the Previous, Next and Parent page of the other pages.
   **Update the project's tables that name them** changes equipment pages, menu items, alarm help
   pages and the startup, home and splash page parameters.

The rename runs in the background, the progress under the options; **Stop** leaves the
project being renamed as it was. Names can swap (A to B and B to A). A page open here stays open,
unsaved changes too, and saves under its new name; a page open in the Graphics Builder must be
closed there first. Afterwards, **Undo Rename**
gives the pages their old names back, and **Check References** runs
[Check Project](troubleshooting.md#check-project) to find the Cicode that still opens a page by
its old name, such as `PageDisplay("Mixing")`, followed through Include's and the project's
functions. **Replace References** there gives each one the new name: the open pages in their
tabs, the closed pages and the Cicode files saved after a backup, the project's tables in place.
A name a genie parameter or a genie's definition
holds is listed, not changed. Compile the project to use the new names.

> **Coming from the Graphics Builder:** its Find and Replace hands off to Studio; ctgedit has its
> own. Rotate and Mirror are direct commands. Align moves nothing until Apply. Goto Object stays
> open after Go To.
>
> A copied genie that refers to its own AN is bound to the copy's AN in ctgedit; the Graphics
> Builder's copy keeps driving the original. <!-- limit:WP22 -->

See the AVEVA help: [Manipulate Graphics Objects](aveva:1172184),
[Align Objects](aveva:1172194) and [Adjust Position and Size](aveva:1172199).
