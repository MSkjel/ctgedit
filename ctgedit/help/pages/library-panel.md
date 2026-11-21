# Symbols and genies: the Library panel

The Library panel (View > Panels > Library) lists every symbol and genie the current page can
place: from its project, its includes, and Plant SCADA's Include and System projects. When two
libraries have an element with the same name, only the one placement uses is listed. It follows
changes on disk.

## Find an element

- Switch between **Symbols** (`.ctl`) and **Genies** (`.ctm`) at the top.
- Type in the search box ([[Ctrl+F]]). Every word must match the name, library or project;
  `lib.element` matches both. Letters in order also match (`mtr` finds `motor`). [[Esc]] clears;
  [[Down]] moves into the results.
- The tree (Favourites, Recent, All libraries, then projects and libraries) narrows to one
  library.
- Results are cards grouped by library. Rest the pointer on one for a large preview. A diamond
  marks a flashing symbol.
- **View options** switches between **Thumbnail Grid** ([[Ctrl+1]]) and **List** ([[Ctrl+2]]),
  sets the card size (or [[Ctrl]] + wheel), and rescans libraries ([[F5]]).

## Place an element

- **Drag** a card onto the page.
- Or **double-click** it (or press [[Enter]]) and click the page. Drag a box to size a symbol.
- Genies with parameters ask for them first (see [Place a genie](genies.md#place-a-genie)).

**Linked** (also Edit > Library > Paste Symbols Linked) decides whether symbols follow later
library changes. Genies are always linked.

Edit > Paste Symbol… ([[F6]]), Paste Genie… ([[F11]]) and Paste Super Genie… ([[Ctrl+F11]]) do the
same through a picker.

## Favourites and recent elements

Click the star on a card to add it to **Favourites**. **Recent** holds what you placed last.

## The context menu

Right-click a card for **Place on Page**, **Edit Element**, **Used By…**, favourites, **Copy
Reference**, **Show in Explorer**, **Show Library '…'**, **Rename Element…** and **Delete
Element…**. AVEVA-documented elements also link to their help.

> **Coming from the Graphics Builder:** the panel replaces the Paste Symbol and Paste Genie
> browsers (still on F6 / F11) and searches the whole include chain at once. Linked works the
> same.
<!-- limit:WP31 -->
Build and publish Industrial Graphics pages in the Industrial Graphics Editor. To display one
on a classic page, place a [Web Content object](special-objects.md#web-content) and enter the
published page's URL. See [Industrial Graphics in Plant SCADA](aveva:1094519).

See [Paste a Symbol](aveva:1172145) and [Paste a Genie](aveva:1172147) in the AVEVA help.
