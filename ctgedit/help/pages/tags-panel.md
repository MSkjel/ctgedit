# The Tags panel

The Tags panel (on the left; View > Panels > Tags, or Tools > Tags Panel [[Ctrl+T]]) lists
the variable tags the current page can use, with their **Type**, **Equipment**, **Comment** and
**Project**.

## Which tags are listed

A page can use the tags of its own project, of the projects it includes, and of the projects
that include it (the project that is compiled, and everything compiled with it). The panel
lists all of them, and each row shows the project the tag is defined in. A page of an include
project (for example a library project that has no tags of its own) therefore still lists the
tags of the projects that use it.

- **Project:** shows the tags of one project only, or of all of them.
- **From:** is shown when several compiled projects include the page's project. It chooses
  the compiled project whose tags are listed.

The same rule gives the lists everywhere else in ctgedit: Insert Tag, equipment, trend tags,
Cicode functions, clusters, devices, content types, page lists and the formats of numeric
displays. The lists follow the tag tables: a tag added in Plant SCADA Studio is listed when
you come back to ctgedit, and the Properties panel's advice and completion know it at once.

The tags are read in the background when the panel is shown; the count line says how many
projects are read. The read is a background task: the status bar's tasks button shows the
projects done and the time so far, and its **Cancel** stops it (the panel reads again the
next time it is shown). A hidden panel reads nothing.

## The columns

When the panel opens, Type fits its text and Tag, Equipment and Project share the width left
(Comment follows them; the scroll bar shows it). Tag and equipment names longer than their
column are cut: point at a cell for the whole text, drag a divider, or right-click the column
titles and choose **Fit Columns to Contents** for every column at its text's width. The
widths you set come back the next time. Settings > Appearance > "Tag column" and the other
rows of its Tags panel section change the first widths.

## Find a tag

Type in the search box: the list shows the tags whose name, equipment or comment contains the
text, and the count below says how many of how many. Tools > Tags Panel ([[Ctrl+T]]) brings
the panel to the front with the search box ready to type in.

## Use a tag

- **Double-click** a tag (or right-click it and choose **Copy Name**) to copy its name to the
  clipboard; the status bar says so.
- **Drag** a tag into any text or expression field (the object properties, Page Properties, a
  genie's parameters) to type its name there.
- **Drag** a tag onto a placed genie on the page. When the genie has exactly one parameter that
  takes a tag, the tag is bound to it at once (one step you can undo); to an **Equipment**
  parameter the tag's equipment is bound instead. Otherwise the genie's parameter dialog opens
  and the tag name is on the clipboard to paste into the right field. A genie inside another
  genie is bound through the outermost genie.

## Where the page uses a tag

Choose a tag in the list: **On this page** under it lists every field of the page that uses it,
and whether the field **Reads** the tag or **Writes** it (a command that assigns it, such as
`Pump1_Run = 1`, toggles it with `Toggle`, or gives its name to `TagWrite`; a slider does both).
`Pump1.Value` counts as a use of `Pump1`. Double-click a use to select its object. With **Mark
on page** ticked, the objects are outlined on the page, blue where they read the tag and amber
where they write it; point at one to see its fields.

Expression fields also complete tag names as you type, and their Wizard's **Insert Tag…** lists
the tags, local variables and equipment items the page can use, with their project (see
[Expressions](properties.md#expressions)).

> **Coming from the Graphics Builder:** the Graphics Builder has no tag list of its own. Its
> Insert Tag lists the active project's tags and shows 'List too big.' above 5,000 rows;
> ctgedit lists every tag the page can use, with its project.

See [Tag Extensions](aveva:1064764) in the AVEVA help for how tags and equipment items are
referenced in expressions.
