# The file formats

A map of the formats ctgedit reads and writes, with the module that owns each part. The
modules' docstrings and comments carry the detail; the tests pin it.

## Container (`.ctg`, `.ctm`, `.ctl`, `.ctt`, `.ctF`)

Owner: `format/container.py`.

A container is a fixed header (`HEADER_FIXED` bytes) followed by a directory of 42-byte
entries (a tag, a 32-byte name, an offset and a size) and the blocks they point at.

- A page (`.ctg`) has an `attributes` block (`format/attributes.py`), a `page` block
  (`format/page.py`), a link / import table (`format/links.py`) and an embedded thumbnail
  (`format/thumbnail.py`).
- A library holds one block per element: symbols (`.ctl`), genies (`.ctm`) and page
  templates (`.ctt`). A deleted element keeps its data with the tag's low byte cleared until
  the library is packed (`Block.is_deleted`).
- `.ctF` is the fast display file the runtime reads next to a page (`format/ctf.py`).

Old page forms are upgraded to the current one when they load (`format/upgrade.py`);
`format/integrity.py` checks that a file is complete.

## Page block and records

Owner: `format/page.py`; walks in `format/forest.py`.

The page block is a page header (size, background, the page's own properties) followed by
the drawing objects as records in depth-first order. A record's child count says how many
of the records that follow are its children, so groups, genies, symbols and templates are
subtrees.

Each record has:

- a type code (`format/codes.TC`): line, text, rectangle, ellipse, polygon, pipe, picture,
  group, symbol set, button, trend, Cicode object, touch / keyboard command, ...;
- a flags word (`format/codes.DWF`): link, own style block, lock, property bags, dynamic,
  button font and text, metadata, placed genie;
- a geometry header with the object's rectangles and anchor (`format/geometry.py`);
- for an object with its own appearance, a 54-byte style block: line, fill and 3D colours
  and the line style (`format/geometry.find_colour_block`);
- type-specific data: a text's caption and font (`format/geometry.parse_text_tail`), a
  shape's points, a picture (`format/bitmaps.py`), an ActiveX control's properties
  (`format/olecontrol.py`), a web content object (`format/webcontent.py`);
- property bags: key / value pairs for the object's animations and settings. A genie
  member has one bag per level of the genies it sits in (see below).

Records are written back by `format/writer.py`; new objects come from blank records
(`format/factory.py`, `format/records.py`).

### Placement

A placed symbol or genie keeps up to three transform slots (move, scale, rotate, mirror)
and its own extent; `format/placement.py` composes them.

### Colours

Colours are packed RGB values with flags for transparency and flashing
(`format/colourtools.py`). The colour-state array of an animated object (`format/colourstate.py`)
is written the way the Graphics Builder writes it. The project palette is
`format/colourpalette.py`.

### Texts

Every string is stored in the page's ANSI code page (`format/textcodec.py`). A text's extent
is measured the way the Graphics Builder measures it (`format/textmetrics.py`). Display
values (numbers and strings) are read and written by `format/dspval.py`; fill colours and
levels by `format/fillcolour.py`.

## Libraries, genies and templates

- `format/symbols.py` reads libraries and resolves a placed symbol or genie to its element.
- A genie declares parameters (`%Name%`). A placed genie keeps one bag level per genie it is
  nested in; the values substitute level by level. `format/genie_place.py` places a genie
  and updates placed copies when the genie changes; `format/genie_norm.py` writes library
  elements in the form the Graphics Builder keeps.
- Genie forms (`<library>.frm`) are the dialog a genie shows when placed (`format/genieform.py`).
- Page templates are placed on a page as records the page owns (`format/template_place.py`).
- Super genies are pages a genie opens (`format/supergenie.py`).
- Composite genies are built from visual templates (`format/composites.py`,
  `format/composer*.py`) into the project's composition cache.
- Symbol sets keep one picture per state (`format/symbolset.py`).

## Project tables

A page save also writes rows in the project's dBASE tables: the page registry (`pages.DBF`)
and the tables of the page's animated objects, buttons, keys and touch commands
(`io/pgregister.py`, `format/dbf.py` for in-place table edits with their NDX indexes,
`format/dbfschema.py` for the layouts). The project registry is `MASTER.DBF`
(`format/projects.py`); a project's `PROJECT.CIT` is `io/projectcit.py`.

## What a save keeps stable

A page saved by ctgedit and then opened and saved by the Graphics Builder stays the same
bytes for the parts ctgedit writes; `tests/fixtures/oracle/` holds Graphics Builder saves to
compare with.
