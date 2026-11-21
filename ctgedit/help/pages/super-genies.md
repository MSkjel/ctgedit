# Super genies

A super genie is a popup page kept in a genie library: one design shared by many pieces of
equipment. It is an element named `<name>@SUPER` in a `.ctm` library, and its expressions use
**associations** (`?name?`, `?REAL 1?` …) instead of fixed tags. At runtime a genie on another
page opens it with AssPopUp(), AssWin() or a similar function and passes the tags, and the page
shows them.

For the page to exist at runtime, the project needs a page made from the super genie, named
after it without `@SUPER` (for example `!PumpDetail`). ctgedit makes that page for you.

## Create a super genie

1. File > New… > **Super Genie**, choose the project and library and type the name. Start it
   with `!` (for example `!PumpDetail`): the page made from it takes that name and is a system
   page (!).
2. Draw the popup. Use `?name?` in expressions for the values passed in at runtime.
3. File > Page Properties… > **Associations** lists the associations with their defaults (see
   [Associations](page-properties.md#associations)).
4. Save. The pages made from it that are open, in any editor window, are made again in their
   tabs at once (save them to keep it). Saving a super genie offers to run Update Pages for
   the other pages already made from it (Settings > Pages and libraries).

## Attach super genies

A genie "attaches" the super genies it opens, so that placing the genie creates their pages.

1. Open the genie for editing (right-click a placed genie > Edit Definition, or File > Open >
   Genies).
2. Choose Edit > Library > Attach Super Genies…
3. **Add…** picks a super genie (Select Super Genie lists the libraries of the project and its
   includes); **Remove**, **Move Up** and **Move Down** change the list. Each row names the page
   it creates; a super genie that cannot be found is marked "(not found)".
4. Click OK, then save the genie.

With a placed genie selected on a page, Attach Super Genies… edits that genie's definition and
saves it after asking. A super genie cannot attach other super genies.

## Pages created from a super genie

When you place a genie that has attached super genies, or save such a genie, ctgedit creates
every super genie page the project does not have yet (not in the project, nor in a project it
includes) and registers it. The status bar says which pages were created; compile the project
to use them. No page is made when the genie is placed on a page of Plant SCADA's Include
project, or when the super genie cannot be found (a message says so; the genie is placed
anyway).

Update Pages creates such a page again from its super genie, so do not edit it. When you open
one, ctgedit asks:

- **Edit Super Genie** opens the super genie itself;
- **Open Read-Only** shows the page without letting you change it;
- **Open Page** (only when Plant SCADA's `[CtDraw.RSC] AllowEditSuperGeniePage` is 1) opens it
  for editing.

When a super genie page is opened for editing, it takes the environment variables and
associations of its super genie.

## Place a super genie

Edit > Paste Super Genie… ([[Ctrl+F11]]) lists only the super genies of the project's libraries
(Paste Genie leaves them out) and places the chosen one where you click.

> **Coming from the Graphics Builder:** super genies, Attach Super Genies and the pages created
> at placement work the same way. ctgedit adds the order buttons, marks super genies that are
> not found, and offers Edit Super Genie / Open Read-Only where the Graphics Builder only
> refuses to open the page.

See [Super Genies](aveva:1172236), [Create a New Super Genie](aveva:1172237),
[Attach a Super Genie to a Genie](aveva:1172239) and [Dynamic Associations](aveva:1172244) in the
AVEVA help.
