# Colours

## Choosing a colour

Every colour field opens the same colour picker:

- The grid is the **project's palette** (`citect.pal`, shared with the Graphics Builder): the
  standard colours, **Transparent** and the project's favourites.
- **Recent** holds your last colours.
- The field at the bottom takes a typed or pasted colour: **#3A7BD5**, **3A7BD5**, **58,123,213**,
  **rgb(58, 123, 213)** or a palette colour's name.
- The eye dropper (**Pick from Page**) takes a colour from the page or screen.
- **Edit…** opens Edit Favourite Colours.

The Properties panel shows each colour's name or #RRGGBB beside the swatch.

A colour can **flash** between an on and an off colour at runtime: tick **Flashing** next to it.
**Transparent** means no colour. Where a colour can't flash or be transparent, those options are
greyed.

## Which colour is which

| Object | Its colours |
|---|---|
| Line | the line colour |
| Rectangle, ellipse, polygon | the line (border) and the fill; a gradient's second colour |
| Pipe | the highlight and lowlight colours it is shaded between |
| Text | the foreground |
| Button | the caption colour, and with Custom fill colour the Up and Down colours |
| Any shape with a 3D effect | the highlight, lowlight and shadow colours |

A fill colour that changes at runtime is set on Fill > Colour; its OFF colour is the object's own
fill (see [Fill colour](properties.md#fill-colour)).

## Favourite colours

Tools > Colours > Edit Favourite Colours… (or **Edit…** in the picker):

1. Make the colour with the wheel, by numbers, by **Hex**, with the eye dropper, or from
   **Recent**. Tick **Flashing** for an on / off pair.
2. Give it a **Name**.
3. **Add**, **Replace** or **Clear** a favourite slot.

Favourites are saved in the project's `citect.pal`, so the Graphics Builder sees them too.

## Swap colours

Tools > Colours > Swap Colours… replaces one colour with another:

- Choose **From** and **To** (the eye droppers and the swap arrows help).
- **Colours in use** lists the colours present; click one to make it From, [[Shift]]+click for To.
- **From any colour** changes every colour to To.
- **Swap range** changes every shade of From's hue to the same shade of To's hue.

Group members are included; placed genies and symbols, locked objects and template objects are
not. Runtime fill animations aren't touched. **Apply to** is the selection or the whole page. The
window stays open; **Apply** and **OK** are one undo step each.

## Adjust colours

Tools > Colours > Adjust Colours… shifts hues, lightness and saturation:

- **Hue**: move a **From hue range** to a **To hue range**. Drag the bars' handles, or use the
  arrow keys.
- **Lightness** and **Saturation**: -100 % to +100 %, optionally for **Selected hues only**.

**Before / after** previews the change. It affects the same objects as Swap Colours.

## Named styles

A named style keeps a look: line, fill, colours, gradient, corners, border, font and alignment.
Styles show as tiles in the ribbon's **Styles** group and the Properties panel.

- **Click** a tile to apply it to the selection, as one undo step.
- **Point** at a tile to preview it on the page. Settings > Editing > "Preview a style on the
  page when you point at it" turns this off.
- **Right-click** a tile for Apply, Select Objects with This Style, Set as Default for New
  Objects, Update from Selection, Rename… and Remove.

Edit > Styles has **Save Appearance as Style…**, **Update Style from Selection**, **Rename
Style**, **Select Objects with This Style**, **Set as Default for New Objects** (see
[The look of new objects](drawing.md#the-look-of-new-objects)), **Use Selection as Default**, and
**Manage Styles…** (rename, reorder, import and export `.ctgstyles` files).

Styles are kept in ctgedit's settings, not the project. To share styles with a team, export them
to a shared folder and set Settings > Pages and libraries > "Shared styles file". Shared styles
appear under **Shared** and are read-only here.

## Transparent paste colour

Tools > Colours > Transparent Paste Colour… sets the colour made transparent in pasted or imported
pictures. See [Pictures and bitmaps](bitmaps.md#transparent-paste-colour).

> **Coming from the Graphics Builder:** the palette, favourites, Swap Color and Adjust Colors give
> the same results. The Graphics Builder changes only the selection; ctgedit can also change the
> whole page (Apply to Whole page). <!-- limit:WP33 -->

See the AVEVA help: [Colors](aveva:1172090), [Color Picker](aveva:1172091),
[Swap Color](aveva:1172093) and [Adjust Colors](aveva:1172094).
