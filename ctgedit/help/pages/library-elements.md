# Editing symbols, genies and templates

Symbols (`.ctl`), genies and super genies (`.ctm`) and page templates (`.ctt`) are **elements**
of library files. ctgedit edits an element in its own tab, like a page.

## Open an element for editing

- File > Open…, then the Templates, Symbols, Genies or Super Genies tab.
- Right-click a placed genie, symbol or template > **Edit Definition (library.element)**, or
  Tools > Edit Page Template for the current page's template.
- Right-click a card in the Library panel > **Edit Element**.

The tab is named `library.element`, with a mark for its kind: ◇ symbol, ◆ genie or super genie,
▭ template. The **hot spot** (a circle with a cross) is the point that lands where you click when
placing the element.

As in the Graphics Builder, the element's own texts show what you typed (`%Label%`), and genies
placed inside it show the values they're given.

## Save an element

File > Save writes the element back into its library. Large elements are saved in a helper
process so the window stays responsive. Then:

- open pages that place it are updated in their tabs. Those with no changes of yours are
  saved; save the others to keep it;
- ctgedit offers to run [Update Pages](update-pages.md) for the other pages;
- a genie with attached super genies creates any super genie pages the project lacks.

File > Save As… saves it into another library or under another name (at most 31 characters).

> **Warning:** the Graphics Builder keeps loaded elements in memory and writes its own copy back
> the next time it saves them. Close it after changing a library in ctgedit (Tools > Close
> Graphics Builder).

> **Warning:** libraries that ship with Plant SCADA (Include, the style projects, SA_Library …)
> are replaced on upgrade and shared by every project. ctgedit warns before saving into one
> (Settings > Pages and libraries > "Warn before saving into a library supplied with Plant
> SCADA"). Save a copy into your own library instead.

## Hot spot

In a symbol's or genie's tab, drag the hot spot marker, or double-click it (Edit > Library > Hot
Spot Location…) to type a position or snap it to **Top left**, **Centre** or **Bottom left**.

Pages that already place the element keep the placed point where it is, so after Update Pages
their copies shift by the opposite of the move. Templates have no hot spot.

## Copy to library

Select objects and choose Edit > Library > Copy to Library…:

1. **Project**: this project or one it includes.
2. **Library**: pick one, type a new name, or **New Library…**.
3. Click an element to replace it, or **New** to type a new name. **Delete…** deletes the selected
   element after listing the pages that use it.
4. **Name**: at most 31 characters.

The objects become one element with its hot spot at their centre. File > New… and Save As use the
same dialog.

> **Coming from the Graphics Builder:** its Copy To only makes symbols. ctgedit can also make a
> genie or super genie.

## Cut link

Edit > Library > Cut Link turns a placed symbol into a plain group that no longer follows its
library, keeping its position, size, turn, AN, name, properties and commands. It's one undo
step.

It's refused for genies, symbol set states, symbols inside genies, symbols turned by a
non-quarter angle, and symbols whose library isn't installed. For the page template use Edit >
Library > Cut Link to Template… (see
[Page templates](templates.md#cut-the-link-to-the-template)).

With several objects selected, it cuts every placed symbol among them.

<!-- limit:WP31 -->
> **Coming from the Graphics Builder:** its Cut Link keeps the placement as a transformation of
> the copied objects; ctgedit applies it to the objects. The page looks the same.

## Symbol linkage

A placed symbol's Appearance > General page shows what it's linked to, with a preview.
**Browse…** points it at another element, keeping position, scale and turn. **Edit Symbol** /
**Edit Template** opens the element. "Not found on this project's include path" means the link
is broken.

## Used by

Edit > Library > Used By… (or right-click a card > Used By…) lists everything that uses the
element, in its project and every project that includes it:

- **Uses it directly**: pages and elements that place it.
- **Uses it through other genies**, with **Through** naming the genies in between.

**Status** says whether each shows the element's current version:

- **Out of date**: it placed an older version.
- **Out of date once ... is updated**: it's current, but a genie in between holds an older copy.

**Open** opens the selected item. **Show only what needs an update** filters the list. The
window refreshes itself after saves and updates. Damaged library files are named under the list.

Finding users reads every page and library of the project and its includers, with progress
([Progress figures](update-pages.md#progress-figures)) and **Cancel**.

## Update users

**Update Out of Date…** in Used By, or Edit > Library > Update Users…, brings everything that uses
the element up to date:

1. Every out-of-date genie, template or symbol that places it is saved again, deepest first, so
   each takes the current version of what it nests.
2. Then the pages that use it are updated, like Update Pages on just those pages.

Pages open in a tab are updated there, and saved when nothing on them is yours; save the
others yourself. A genie open with unsaved changes is skipped. Libraries that ship with Plant SCADA aren't changed. Every file is backed up first.
The summary opens in the usual window ([The summary](update-pages.md#the-summary)).

## Compose nested genies again

A genie placed inside an element is kept as a copy. Each save composes again the copies whose
genie changed. Edit > Library > **Compose Nested Genies Again** composes every copy, whatever
the saved stamps say, as one undo step. Use it when a nested genie looks out of date though
its users say they're current.

Copies keep the values the element gives them, and the element keeps its own parameters. A new
object in a nested genie takes the copy's values, as the Graphics Builder's save does. A default
a nested genie gains (`%Q=5%`) is set on its copies, which then keep it.

The element only changes when a copy actually differs from its genie. Differences a save makes
anyway (list sizes, marker boxes, measured text sizes, rounding) don't count. If a copy can't be
composed, the message names it and the copy stays as it was.

## When you save a genie

Pages and genies that use the genie, directly or through others, pick up the new version without
Update Users:

- **Open pages** update right after the save, in every window, the current page first. Only the
  copies of the changed genie change; your unsaved edits stay. It runs in the background and
  lands as one undo step, listed in the bar of automatic changes.
- **Genies open in a tab** that nest it update the same way and show as unsaved.
- **Order**: the tab you're looking at, then tabs you show while waiting, then genies nearest the
  saved one, then pages, smaller first. Up to three tabs update at once (Settings > Pages and
  libraries).
- **Library genies and closed pages** follow Settings > Pages and libraries > Genies and closed
  pages that use a saved genie: ask, update in the background, or leave them. The background
  update works like Update Users: genies, templates and super genies that place it are saved
  again, then closed pages are updated. Every file is backed up first. The status bar shows
  progress, and the result stays in the bell with Details and the backup folder. Skipped items
  (unsaved genies, read-only or shipped libraries, unregistered pages) are listed in Details.
- **Opening a page** updates genies that changed since it was saved, as the Graphics Builder does.
- **Opening a genie** (or template, or super genie) updates its nested copies that changed. You
  can undo it.

Tools > Update Pages with "Save the library genies and templates in the chain first" saves every
changed genie of the projects at once (see [The chain](update-pages.md#the-chain)).

Saving a symbol works the same way, per Settings > Pages and libraries > Genies and closed pages
that use a saved symbol. "Don't ask again" in the Symbol Saved question remembers your answer.
Open pages and genies take the new symbol as an automatic change you can undo.

A copy of an out-of-date genie pasted onto a page keeps that page out of date until it's
updated. So does an unlinked copy of a symbol or template whose contents changed since.

Copy to Library and the first save of a new element bring their copies up to date.

A nested genie is looked up in the projects of the library that holds it, not the page's own
project.

<!-- limit:WP31 -->
Saving a genie again writes ctgedit's form of it: on some genies made with older Graphics Builder
versions a caption or extent can shift slightly.

## Rename or delete an element

Right-click it in the page browser or the Library panel > Rename element… or Delete element….
ctgedit lists the pages that place it first.

## Pack libraries

Deleting or replacing elements leaves unused space in a library. Tools > Pack Libraries… rewrites
a project's libraries without it (tick "And the projects it includes" for those too) and offers
to delete unused composite genie compositions. Everything is backed up first. It packs in a
separate process while you go on working, and the results window lists what each library gave
back; Update Pages waits until it has finished. Close the Graphics Builder first; Pack Libraries
won't run while it's open.

See [Symbols](aveva:1172200), [Copy an Object to the Library](aveva:1172198) and
[Pack Libraries](aveva:1172118) in the AVEVA help.
