# Welcome to ctgedit

ctgedit is an editor for AVEVA Plant SCADA (Citect) graphics pages. It opens, edits and saves
pages (`.ctg`) and the symbols, genies, super genies and templates in project libraries (`.ctl`,
`.ctm`, `.ctt`), without needing the Graphics Builder.

A save writes everything the compiler and runtime need:

- the page file (`.ctg`);
- the fast-display file (`.ctF`), or removes an out-of-date one;
- the page's rows in the project database, marked for the next incremental compile.

Compile and run from ctgedit with File > Compile Project ([[Alt+F10]]) and File > Run in Plant SCADA ([[F5]]).
See [Compile and run](compile-and-run.md).

## Which projects ctgedit edits

ctgedit works on the active install: the one Studio, the Graphics Builder and the runtime use.
Pages from another install show their project in amber, since Plant SCADA won't see changes to
them.

## Finding your way

| To | Read |
|---|---|
| Open a page, make a first change, save it | [Getting started](getting-started.md) |
| Learn the ribbon, panels and canvas | [The editor window](the-window.md) |
| Create pages and library elements | [Opening, creating and saving pages](opening-and-saving.md) |
| Set a page's title, template, events and keys | [Page properties](page-properties.md) |
| Draw and change objects | [Drawing objects](drawing.md), [Selecting, moving and arranging](selecting-and-arranging.md) |
| Animate objects and add commands | [Object properties and animation](properties.md) |
| Place and edit symbols and genies | [The Library panel](library-panel.md), [Genies](genies.md) |
| Bring pages up to date after a library change | [Update Pages](update-pages.md) |
| Compile and see the page in the runtime | [Compile and run](compile-and-run.md) |
| See what's different from the Graphics Builder | [Coming from the Graphics Builder](differences.md) |
| Fix a problem | [Troubleshooting](troubleshooting.md) |

Press [[F1]] anywhere to open the manual on what you're working with. Help > Keyboard Shortcuts
lists every key ([Keyboard shortcuts](keyboard-shortcuts.md)).

> **Coming from the Graphics Builder:** the tools, object properties, genie forms, templates and
> libraries work the same way, as do most keys (F5 Run, F6 Paste Symbol, F7 / F8 snap, F9 Bitmap
> Editor, F10 magnifier, F11 Paste Genie). The main differences: pages open as tabs, property
> windows stay open while you work, and saving never needs the Graphics Builder. See
> [Coming from the Graphics Builder](differences.md).

## Using the manual

- The **Search** tab finds every section with your words, with matching menu commands first.
  Click a section to open it, or a command to run it.
- Menu paths in the text (View > Smart Guides) are links: click to open the menu with the command
  highlighted, [[Shift]] + click to run it. Settings links open the Settings window on that
  option.
- [[Ctrl+=]] and [[Ctrl+-]] change the text size, [[Ctrl+0]] resets it. Back ([[Alt+Left]]),
  Forward ([[Alt+Right]]), Contents ([[Alt+Home]]) and Print ([[Ctrl+P]]) are on the toolbar.

## About this manual

This manual covers ctgedit. For Plant SCADA itself (Cicode, tags, equipment, the runtime) it links
to AVEVA's documentation installed with Plant SCADA, such as
[Graphics Builder in the AVEVA help](aveva:1172081). Help > Cicode Function Reference (AVEVA)
opens the Cicode function list.
