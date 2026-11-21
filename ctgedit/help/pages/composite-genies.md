# Composite genies

A composite genie is built from a **template** (an XML file in a project's `Composite Genies`
folder; SA_Library ships Drive, Valve, Meter …) and a set of **options**: which parts to show,
where, and for which equipment. The genie that comes out is stored in the project's composition
cache (`_CompositionCaches`), and the page links to it. ctgedit composes composite genies
itself.

## Insert a composite genie

1. Choose Edit > Library > Paste Composite Genie… (also in the Place group of the ribbon).
2. Pick the **Template**: the list has every template of the project and the projects it
   includes. A template whose XML file has problems is marked and cannot be chosen; select it to
   see the problems.
3. Set the options (see [Presentation options](#presentation-options)). The template's defaults
   are filled in. OK stays disabled while a shown option is not valid.
4. Click OK, then click on the page: the genie is composed with your options into the project's
   composition cache and placed with its top-left corner there.

Composite genies can be placed on pages, templates and super genies, not inside a genie or a
symbol.

## Presentation options

Double-click a placed composite genie (or right-click > Edit Composite Genie…) to see and change
its options:

- Each option has the right kind of field: a check box, a list of its allowed values, a number
  or a text. An option that only applies when another one has a certain value is shown only
  then.
- A value that is not valid (out of range, not an allowed value, not a valid equipment or tag
  name) is marked in red with the reason; tick **Show only parameters with invalid input** to
  list them. The field of the option you are on shows its description at the bottom.
- **Search parameters** finds an option by name.
- **Template ID** and **Instance** name the template and this composition (select and copy them
  to find the files); **Show in Explorer** shows the composition cache in Explorer.

OK composes the genie with the new options and relinks the placed genie to the new composition,
in one step you can undo. The options are read-only when:

- the template isn't in the project or its includes (only the cache has a copy);
- the template file has problems;
- the composite is part of another genie or of the page template;
- the composite is locked.

## Keep composite genies up to date

When a template, or a genie it uses, changes:

- right-click the composite > **Update Composite Genie** composes it again with the newest
  template, keeping its options;
- Tools > Update Pages with "Genies and super genies" does it for every page
  ([Update Pages](update-pages.md)).

Tools > Pack Libraries offers to delete compositions that nothing in the project, or in a
project that includes it, uses any more. They go into the pack's backup.

## Rules

- Change a composite genie only through its options. Its objects are built again from its
  composition every time the page is opened, so they and their genie parameters cannot be
  edited (Find and Replace shows their fields read-only).
- Never change or delete files in `_CompositionCaches` yourself; ctgedit does not open them for
  editing either.
- Composite genies can be rotated and flipped like any genie.

When a template changes, Update Composite Genie uses the replacement options saved for that
instance if the new template accepts them. Otherwise it uses the original options.

> **Coming from the Graphics Builder:** Paste Composite Genie, Edit Composite Genie and the
> Presentation Options form are the Graphics Builder's; ctgedit does not need the search box to
> be cleared before OK, and lists the problems of a template file instead of refusing it.

See [Composite Genies](aveva:1172219), [Insert a Composite Genie](aveva:1172148_1) and
[Composite Genie Templates](aveva:1172223) in the AVEVA help.
