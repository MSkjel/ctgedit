# The underlay

An underlay is another page, a picture, or an earlier version of this page, drawn faded behind the
page. Use it as tracing paper: place objects where the reference has them, rebuild an old screen
from a screenshot, or line up with a sister page.

It's only shown in ctgedit: never saved, printed, exported or put in thumbnails. Each tab has its
own.

## Underlay a page

View > Underlay > Underlay a Page… opens the page browser; pick a page and click **Underlay**. It's
drawn in a separate process, so large pages don't hold up the editor.

A page underlay starts at 0, 0 and 100 %, so its objects sit exactly where they are on that page.
Its template and page colour are left out (**Draw its template** in the Underlay window shows the
template).

With **Snap to its objects** on (the default), objects you drag or place snap to the underlay's
objects. Drag a tank onto the reference's tank and it lands on the same pixel. Hold [[Alt]] to
move freely.

## Underlay a picture

View > Underlay > Underlay an Image… picks a picture file. At 100 % one picture pixel is one page
pixel. The Underlay window's **Fit Page** and **Actual Size** resize it. Very large pictures are
read at a smaller size.

## Underlay an earlier version

View > Underlay > Underlay an Earlier Version… lists the page's saved versions (see
[Earlier versions of a page](opening-and-saving.md#earlier-versions-of-a-page)) to compare with
the page as it is now.

## The underlay window

View > Underlay > Underlay Settings… (or double-click **Underlay** in the status bar). Changes
apply at once.

- **Source**, with **Change** and **Remove**.
- **Show** and **Above the objects**.
- **Opacity** (40 % by default).
- **Tint**: **Grey**, **Blue** or **None**.
- **X**, **Y** and **Scale**, with **Fit Page** and **Actual Size**.
- **Snap to its objects**, **Draw its template** and **Smooth scaling**.
- **Move with the Mouse**.

If the underlay couldn't be drawn, the window says why and offers **Try Again**.

## Move it with the mouse

View > Underlay > Move Underlay with the Mouse makes drags on the page move the underlay instead
of objects. Arrow keys move it like an object. [[Enter]] or choosing the command again finishes;
[[Esc]] puts it back where it started. The keys work on the page, not in the Underlay window.

## Above or under the objects

The underlay is normally drawn under the objects. View > Underlay > Underlay Above Objects draws it
over them, which helps on pages with a filled background panel.

## Show, hide and remove

View > Underlay > Show Underlay ([[Alt+Shift+U]]), or click **Underlay 40 %** in the status bar,
toggles it. **Underlay failed** in the status bar opens the window with the reason. The page's
right-click menu has an **Underlay** submenu. View > Underlay > Remove Underlay takes it off.

## Remembered with the page

With Settings > Canvas > "Remember each page's underlay" on, the underlay comes back when the page
reopens. If its source changes, it's redrawn; if its source is gone, it's dropped.

An underlay keeps a full copy of its picture in memory (a Full HD page at 2x is about 33 MB).
Settings > Canvas > Underlay sets the defaults and resolution. See [Settings](settings.md#canvas).
