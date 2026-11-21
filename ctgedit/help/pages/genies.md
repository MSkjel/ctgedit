# Genies

A genie is a group of objects in a genie library (`.ctm`) with **parameters**, such as a tag or
equipment name, that you fill in each time you place it. A placed genie is a copy of the genie's
objects with your values put in, rebuilt from the genie's definition whenever the page opens. So
you change a placed genie through its parameters, and the genie itself through its definition.

## Place a genie

Drag a genie from the [Library panel](library-panel.md), double-click it there and click the page,
or use Edit > Paste Genie… ([[F11]]). ctgedit asks for its parameters:

- in the genie's **form** if its library has one (see [Genie forms](#genie-forms)), otherwise one
  row per declared parameter, filled with the defaults;
- the header shows the genie and the AN it will get.

The genie lands with its top-left at the click. Any super genie pages it opens that the project
lacks are created too (see [Super genies](super-genies.md)). Settings > Editing can skip the form
and use defaults.

A genie can't be placed inside itself, directly or through other genies. ctgedit refuses and
names the chain (`fxnest.n2 > fxnest.n1 > fxnest.n0`). The same applies to pastes and Copy to
Library.

## Genie parameters

Double-click a placed genie (or Edit > Genie Parameters…) to change its parameters. OK rebuilds
the genie with the new values, as one undo step. Nested genies are rebuilt from their own current
definitions, and every object keeps its AN.

A genie inside another genie is set through the outer genie, so double-click opens the outermost
one. For a genie inside a plain group, select it in the Objects panel and click **Genie
Parameters…** in the Properties panel.

Tag and equipment parameters suggest names as you type. Situational Awareness genies get pickers
(see [Situational Awareness pages](situational-awareness.md)).

Drag a tag from the [Tags panel](tags-panel.md) onto a genie: if it has one tag or equipment
parameter, the tag is bound at once.

## Replace a genie

Edit > Library > Replace Genie… swaps placed genies for another genie, as one undo step. In an
open library genie or template it replaces the genies placed inside.

- **Replace** lists the genies the project can place, with how many are **Here**. Tick one or
  more. **Placed here only** and **Filter genies** narrow the list. Genies with a missing library
  are listed as "not found".
- **Which**: the selected ones, all on this page, or all pages of a project (below).
- **With**: the new genie.
- **Parameters of the new genie**: a parameter with the same name as an old one is filled with
  `%Name%`, keeping each genie's value. Use `%Tag%` or `%Tag%_PV` to map from other names, or a
  fixed text. A blank takes the default. The note below names old parameters that will be dropped.
- **Line up by**: the **Hot spot** (see [Hot spot](library-elements.md#hot-spot)) or the
  **Top-left corner**. Size, turn, mirror, name, AN and group membership are kept either way.

Each new genie is built from the new definition, so this also fixes genies whose library is
missing. Composite genies aren't replaced.

A copy of a sub-genie on a page belongs to its outer genie and is rebuilt with it. Replace it in
the definition, or use All pages of the project, which does that for you.

### All pages of the project

Choose **All pages of the project:** and the project, as in
[Find and Replace](selecting-and-arranging.md#find-and-replace). Tick **And the projects it
includes** for those too. ctgedit finds every use, asks first, then:

1. changes and saves the library genies and templates that place it;
2. changes and saves the pages that place it;
3. updates what uses those library genies, as after a genie save.

Open pages and genies are changed in their tabs but not saved. Pages open in the Graphics Builder,
shipped libraries and unregistered pages are left alone and named in the result. Everything is
backed up first (the backup is called Replace Genie). It runs in the background.

## Genie forms

A genie library can have a form file (`<library>.frm`) that lays out the genie's dialog: title,
prompts, field widths and drop-down lists. ctgedit looks for it in the Graphics Builder's search
order.

- Parameters the form doesn't show are listed under **Other parameters (not on this form)**.
- A form with errors is reported and the default rows are shown instead.
- Genies that ship with Plant SCADA and are documented by AVEVA have a **Genie Reference** button
  and AVEVA's descriptions as tool tips.
- Settings > Editing can add a **Group…** button that opens the member objects' property sheets.

## Design a genie form

Edit > Library > **Genie Form…** (with a genie open, or one placed genie selected), or **Edit Genie
Form…** in the Library panel, opens the form designer. It edits the genie's form in the `.frm`
file and leaves other forms and comments alone.

- The grid is the form's own: a column is a character, a row is a line. Drag items to move them
  and field edges to resize. Arrow keys move the selection; [[Delete]] removes it.
- **Add Prompt**, **Add Field** and **Add Rule** add items. Double-click a parameter (or **Add to
  the Form**) to add its prompt and field together.
- A field has a **Parameter**, **Length**, **Width**, and optional **List form**, **List field**
  and **List filter** for drop-downs.
- **Title** is the dialog caption.
- **Preview** shows the resulting dialog.
- Problems the Graphics Builder would refuse are shown under the grid and block Save.

## Editing a genie's definition

Right-click a placed genie > **Edit Definition (library.element)** opens the genie itself. Save
it and the open pages that use it update at once; Tools > Update Pages updates the rest. See
[Editing symbols, genies and templates](library-elements.md).

To make a new genie, choose File > New… > Genie, draw it, and use `%name%` (or `%name=default%`)
in texts and expressions for each parameter. See [Define Genie Substitutions](aveva:1172206) and
[Use the IFDEF Macro](aveva:1172210) in the AVEVA help.

## Copying placed genies

Copy, paste and duplicate make an independent placed genie with its own AN. A genie that refers
to its own AN (alarm lists, trees) is bound to the copy's AN, and clashing names get `_1`, `_2` and
so on.

A genie copied from inside another genie's tab and pasted onto a page keeps the values it had
there.

> **Coming from the Graphics Builder:** the dialog lists exactly the same parameters, values and
> forms. A form's "readonly" access has no effect in either editor.
> Font fields list installed families. Function fields list built-ins and project functions.
> Translated help lists use the installed Plant SCADA resources. With genie forms disabled, placing or
> editing a genie opens a property sheet for each member object.

See [Genies](aveva:1172204), [Genie Forms](aveva:1172211) and
[Genie Parameters Dialog Box](aveva:1172216) in the AVEVA help.
