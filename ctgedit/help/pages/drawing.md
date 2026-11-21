# Drawing objects

## Drawing tools

Pick a tool from the ribbon's Design tab, Tools > Draw, the floating toolbox ([[F3]]), or press
its key over the canvas:

| Tool | Key | Draws |
|---|---|---|
| Select | [[V]] | (selects and moves objects) |
| Line | [[L]] | a straight line |
| Free Hand Line | [[F]] | a line that follows the pointer |
| Rectangle | [[R]] | a filled rectangle |
| Rectangle (outline) | [[Shift+R]] | a rectangle without fill |
| Ellipse | [[E]] | a filled ellipse |
| Ellipse (outline) | [[Shift+E]] | an ellipse without fill |
| Polygon | [[P]] | a polygon, one click per corner |
| Pipe | [[I]] | a shaded pipe, one click per bend |
| Text | [[T]] | text |
| Number | [[N]] | a number display of a tag or expression |
| Button | [[B]] | a button |
| Symbol | [[A]] | a library symbol or genie |
| Symbol Set | | a symbol that changes with a tag value |
| Measure | [[M]] | nothing; shows a distance (see [Measure](#measure)) |

The tool stays chosen after each object, so you can draw several in a row. [[Esc]] or the Select
tool goes back to selecting. Settings > Editing can make the tool return to Select after each
object instead. Trends, Cicode objects, Web Content and ActiveX controls are under
**More** on the ribbon (see [Trends, Cicode objects, web content and ActiveX](special-objects.md)).

New objects take the look in [The look of new objects](#the-look-of-new-objects), and their
properties open once drawn (Settings > Editing can turn that off).

Modifier keys while drawing:

- [[Ctrl]] keeps lines and segments horizontal or vertical, and makes rectangles square and
  ellipses circular.
- [[Shift]] draws rectangles and ellipses from the centre.
- With the design-tool modifier scheme (Settings > Canvas), [[Shift]] constrains and [[Alt]]
  draws from the centre (see [Resize](selecting-and-arranging.md#resize)).
- With Snap to Grid on, [[Alt]] turns snapping off while you drag.

## The look of new objects

**Colours.** The **Colours** button in the Create group shows the fill, line and text colours for
new objects. Click a part to change it. The curved arrow (or [[Shift+X]]) swaps fill and line;
the red-stroked square toggles **No fill**.

Until you pick colours, new objects **contrast with the page**: near black lines and text with
light grey fills on a light page, and the opposite on a dark page.

**Style per tool.** With a drawing tool armed and nothing selected, the Properties panel shows
**New Rectangle**, **New Text** and so on. Set line, fill, gradient, corners, font and other
details there; each tool keeps its own. **Reset to the factory look** restores it.

- **Size of a click** (New Button): the size a click makes (110 x 36 px by default).
- **Style for new objects**: **This tool's own** or **Last used**.
- Edit > Styles > Use Selection as Default makes the selected object's look the default for its
  kind. Edit > Styles > Set as Default for New Objects does the same with a named style (see
  [Named styles](colours.md#named-styles)).

None of this is stored in the page.

## Lines

Press at the start and release at the end. Drag an end node later to move it (see
[Reshape](selecting-and-arranging.md#reshape-lines-polygons-and-pipes)).

## Free hand lines

Press and drag; the line follows the pointer. The result is an open polygon.

## Rectangles

Drag from corner to corner. Rounded corners, 3D edges, borders and gradients are in its
properties ([Appearance](properties.md#appearance)).

## Ellipses

Drag the bounding box. An ellipse can also be a pie slice or an arc (Appearance > General > Type).

## Polygons

Click each corner. Finish with a double-click, [[Enter]] or a right-click. [[Backspace]] removes
the last corner, [[Esc]] cancels. A polygon is closed and filled; Appearance > General > Open
(polyline) makes it open.

## Pipes

Click the start, then each bend, and finish like a polygon. Pipes are shaded across their width.
For a T piece, start a second pipe on the first.

## Text

Click (or drag a box) and type. [[Enter]] starts a new line; [[Ctrl+Enter]], [[Tab]] or clicking
elsewhere finishes; [[Esc]] cancels. You can also type first and then click where the text
goes. See [Text, numbers and fonts](text-and-numbers.md).

## Numbers

Click and enter the **Expression** (a tag or Cicode expression) and optional **Format** (`###.#`,
`#.## EU`). See [Text, numbers and fonts](text-and-numbers.md#numbers).

## Buttons

Click for a default-size button, or drag its box. Set the caption, look and command in its
properties (Appearance > General and Input > Touch).

## Symbols and genies

With the Symbol tool, click the page, pick a symbol in **Select Symbol**, and it's placed with its
top left at the click. Drag a box to give it another size. Genies are always placed at their own
size, after asking for their parameters.

Quicker: drag from the [Library panel](library-panel.md), or use Edit > Paste Symbol… ([[F6]]),
Paste Genie… ([[F11]]) or Paste Super Genie… ([[Ctrl+F11]]). See [Genies](genies.md).

## Symbol sets

The Symbol Set tool places a set and opens its properties to choose a symbol per state. See
[Symbol sets](symbol-sets.md).

## Measure

Tools > Measure ([[M]]) draws nothing. Drag to see the width, height, distance and angle. The
measurement stays until the next drag, [[Esc]] or another tool. It snaps like a line.

## Grid and snap

View > Grid:

- **Show Grid**. Every 8th line is a major line. Minor lines fade in as you zoom.
- **Pixel Grid** shows every pixel from 800 %.
- **Snap to Grid** ([[F8]]) snaps drawn points, handles and moved objects' top-left corners.
- **1 px** … **20 px** set both pitches.
- **Grid Setup…** sets separate horizontal and vertical pitches, the origin, major lines and
  lines or dots.

The grid is drawn on the page only, unless Settings > Appearance says otherwise. It's never saved,
printed or put in thumbnails.

## Guidelines

Guidelines are lines across the page that objects snap to.

- View > Guidelines > Guidelines Setup… adds them by position.
- Or drag from a ruler onto the page (see [The rulers](the-window.md#the-rulers)).
- Drag a guideline to move it; hold [[Ctrl]] on release to copy it. Drop it on the page edge to
  delete it.
- Double-click a guideline for Guidelines Setup; right-click it for **Guideline Position…** and
  **Delete Guideline**.
- View > Guidelines > Clear All Guidelines.
- **Display Guidelines** shows or hides them; **Snap to Guidelines** ([[F7]]) snaps edges and
  centres within 8 pixels.

Guidelines are kept per page by ctgedit, never saved into the page file.

## Smart guides

With View > Smart Guides on, a dragged object snaps its edges or centre to other objects and the
page, with a dashed line and outline showing what it lined up with. Smart guides beat Snap to
Grid within reach. Hold [[Alt]] to drag freely. Settings > Editing sets the snap radius (8 to 12
pixels works best).

A label shows the position, size, or a line's length and angle while you drag.

## Cross hairs and slow speeds

- View > Cross Hair Cursor draws lines through where a click will land, with the position next to
  the pointer.
- View > Text Without Smoothing draws text as the runtime does.
- Tools > Advanced > Mouse Slow Speed slows drags for fine placement.
- Tools > Advanced > Cursor Keys Slow Speed makes arrow keys move 1 pixel, instead of the step
  in Settings > Editing > "Arrow-key move step".

> **Coming from the Graphics Builder:** the tools, modifiers and type-then-click text work the
> same way, as do Snap to Grid, Snap to Guidelines, Grid Setup and Guidelines Setup. ctgedit also
> allows a 1-pixel grid.
>
> The Graphics Builder keeps one set of guidelines for its whole session; ctgedit keeps them per
> page. When a guideline and a grid point are equally near, ctgedit snaps to the guideline, the
> Graphics Builder to the grid. <!-- limit:WP34 -->

See the AVEVA help: [Use a Grid to Align Objects](aveva:1172086),
[Use Guidelines to Align Objects](aveva:1172087) and [Graphics Objects](aveva:1172122).
