# Pictures and bitmaps

A bitmap is a picture stored inside the page. A flashing bitmap holds two pictures that the
runtime shows in turn. A metafile is a drawing stored inside the page that stays sharp at any
size.

## Import pictures

- File > Import… places pictures from files:
  - common image formats (PNG, BMP, JPEG, GIF, TIFF, WebP, ICO …) as bitmaps;
  - SVG as a bitmap drawn at the placed size;
  - `.wmf` and `.emf` metafiles (from Visio, Office and most drawing programs) as sharp
    metafile objects.

  Several files at once are placed as one undo step. The picture options open first.
- File > Import Recent lists favourite and recent pictures.
- Drag picture files from Explorer onto the page.
- [[Ctrl+V]] pastes a picture copied in another program, or picture files copied in Explorer.
- Edit > Paste Special… chooses **Device Independent Bitmap**, **Enhanced Metafile**, or
  **Picture, choosing its size and transparency…**.

## Picture options

Before a picture is placed:

- **Size on the page**: its own size, fit the visible page, or a typed size.
- **Place it as** (metafiles): a sharp drawing or a bitmap.
- **Pixels**: store the picture at its size on the page, which is smaller and sharper than the
  runtime's stretching. The stored size is shown; large pictures in the warning colour.
- **Transparency**: none, the picture's own, or a colour you **Pick** from the preview, with a
  **Tolerance**.
- Keep it in File > Import Recent as a favourite.

The options open however a picture arrives: File > Import, a paste or a drop. Settings > Canvas can
open them at File > Import only, or never.

## Replace, export and resample a picture

Select one bitmap or metafile:

- Tools > Pictures > Replace Picture… swaps in another file, keeping position, size, animations,
  commands, name and AN. It can also replace other copies of the same picture on the page.
- File > Export Picture… saves a bitmap as PNG, BMP or JPEG (two files if flashing), or a
  metafile as `.wmf` or PNG.
- Tools > Pictures > Resample Picture to Its Size stores a stretched bitmap at its displayed size,
  which is sharper and often smaller.

The Objects panel shows each picture's pixels and stored size.

## Duplicate pictures

Each page stores its own copy of a picture, so a logo on 50 pages is stored 50 times. Tools >
Pictures > Find Duplicate Pictures… lists pictures stored more than once, costliest first. For
one:

- **Make Symbol and Link Everywhere…** stores it once as a library symbol and links every copy, on
  every page, to it.
- **Replace Everywhere…** swaps in another file for every copy.

Pages are backed up first. Open pages change in their tabs; pages open in the Graphics Builder are
skipped. Only the pages' own pictures count.

## Flashing bitmaps

- File > Import As Flashing… asks for the primary picture, then the flashing one.
- Edit > Paste Symbol As Flashing… does the same with two bitmap symbols from the libraries.

## Transparent paste colour

Tools > Colours > Transparent Paste Colour… picks a colour, such as the white around an icon,
that becomes transparent in pasted and imported pictures. It's also in Settings > Canvas > "Make a
colour transparent in pasted pictures".

## Convert to bitmap

Tools > Convert to Bitmap turns the selected objects into one bitmap of how they look, as one undo
step. Use it for decoration only: the bitmap has no animation.

## The bitmap editor

Select a bitmap and choose Tools > Bitmap Editor ([[F9]]):

- **Pen** draws pixels; the right button draws transparent ones.
- **Eraser** makes pixels transparent.
- **Fill** fills an area.
- **Eye Dropper** ([[Shift+P]]) picks a colour.
- **Select** and **Crop** keep an area; **Trim** removes transparent edges.
- **Rotate Left**, **Rotate Right**, **Flip Horizontal** and **Flip Vertical**.
- A flashing colour draws flashing pixels.
- **Show** picks **Both states**, **On state** or **Off state**.
- Zoom with [[Ctrl+=]] and [[Ctrl+-]]. [[Ctrl+Z]] / [[Ctrl+Y]] undo and redo inside the editor.

**Apply to Page** puts the changes on the page as one undo step (save the page to keep them on
disk); **Cancel** discards them. If the bitmap changed on the page meanwhile, Apply to Page is
refused and your edits stay in the editor. The editor can't edit metafiles, linked symbols or genie
bitmaps.

## Bitmap size

**Bitmap Size…** in the editor adds (**Grow**) or removes (**Shrink**) pixels at the edges, up to
8192 x 8192.

> **Coming from the Graphics Builder:** the bitmap editor works on the same pair of pictures, and
> ctgedit adds Fill, Select, Crop, Trim, Rotate, Flip, undo and the state views. The Graphics
> Builder doesn't import SVG or EMF, and has no picture options, Replace or Export Picture.

See [The Bitmap Editor](aveva:1172116) and [Import Graphics](aveva:1172155) in the AVEVA help.
