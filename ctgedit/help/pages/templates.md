# Page templates

A page template gives pages a shared frame: title area, navigation buttons, alarm banner,
background and size. Templates are elements of `.ctt` libraries. The library is the **style**
(such as `sxw_style_1` or `normal`), and the element name ends with the **resolution**:
`normal@HD1080` is for a window with a title bar, `normal@-HD1080` for one without.

| Resolution | Size |
|---|---|
| VGA | 640 x 480 |
| SVGA | 800 x 600 |
| XGA | 1024 x 768 |
| SXGA | 1280 x 1024 |
| HD720 | 1280 x 720 |
| HD768 | 1366 x 768 |
| HD1080 | 1920 x 1080 |
| WUXGA | 1920 x 1200 |
| UHD4K | 3840 x 2160 |
| User Defined | the page's own View area |

A page **linked** to its template holds a locked copy of the template's objects. Change the
template instead, or lift the lock with Settings > Editing or Edit > Break Lock Mode.

## The template picker

New Page, Page Properties > Appearance and a template's Linkage page all use the same picker:
**Style**, **Resolution** (or **All resolutions**), **Designed for showing title bar**, and
thumbnails to click. It lists the templates of the page's project and its includes. A template
that isn't installed shows as "Name (not found)".

## Create a page from a template

1. File > New… ([[Ctrl+N]]) > **Page**.
2. Choose the **Style** and **Resolution**, and **Designed for showing title bar**.
3. Click the template.
4. Keep **Linked** ticked so the page follows later template changes.
5. Click **Create Page**, choose the project, type the name and save.

The page takes the template's size, page properties, associations and background. **Edit
Template** opens the template itself instead.

## Page defaults

File > Page Defaults… sets the **Style**, **Resolution**, **Designed for showing title bar** and
**Background colour** for new pages in the project. They're stored in the project's
`PROJECT.CIT`. A new template made with no page open takes this resolution as its size (1920 x
1080 for Default or User Defined).

## Change a page's template

On Page Properties > Appearance, pick another template and click Apply. The template objects are
replaced and the page takes the new size, colour and page properties; your own objects stay. It's
one undo step. See [Page properties](page-properties.md#template).

## Template inputs

Some templates take inputs, such as a page label. Double-click the template on the page to edit
them; **OK** redraws the template's copy with the new values.

A template takes inputs once one of its objects names a parameter, such as `%Title%`. A new page
made from it shows such texts as typed, as the Graphics Builder does, until the template is
applied to the page again.

## When a template changes

After a template is saved, the pages that use it are out of date:

- **Opening** such a page applies the template again (you can undo). Settings > Pages and
  libraries can turn this off; Tools > Update Template on This Page then does it by hand.
- **Saving a template** updates what uses it per Settings > Pages and libraries > Templates and
  closed pages that use a saved template: ask (the Template Saved question), update in the
  background, or leave them. The background update saves templates built on it first, then
  updates closed pages (see [When you save a genie](library-elements.md#when-you-save-a-genie)).
  Open pages linked to it update at once either way, as one undo step each. The question also
  offers **Update Pages…**.
- **Tools > Update Pages** updates every page at once: see [Update Pages](update-pages.md).

Applying a template again keeps the page's own parameter values, size, colour and page
properties, unless `[Animator] TemplateUpdate` is 1. Update Pages can also apply those.

## Edit a template

Tools > Edit Page Template (or right-click the empty page) opens the current page's template.
File > Open's Templates tab opens any template; File > New… > Template creates one. Saving
updates what uses it, as described in [When a template changes](#when-a-template-changes).

> **Warning:** templates that ship with Plant SCADA are overwritten on upgrade and shared by every
> project. ctgedit warns before saving into one. Save a copy into your own library instead.

## A template built on another template

A template can hold a copy of another template, for example one made with Copy to Library from a
page that uses a template. When the inner template changes, the outer one is out of date:

- Opening or saving the outer template brings its copy up to date, as the Graphics Builder does.
- The update after the inner template's save, Update Users, and Update Pages with the chain box
  ticked save it before updating pages.
- Pages linked to the outer template show the change as soon as they're opened or updated.

A template can't hold a copy of itself, directly or through other templates. ctgedit refuses and
names the chain.

## Cut the link to the template

Edit > Library > Cut Link to Template… turns the template's objects into ordinary objects. A
changed template is applied first, so you keep what you see. It can only be undone while the page
is open.

On a page linked to a template built on another, Cut Link keeps the inner template's copy linked
as the page's template.

Genies and symbols in the template are copied as the template holds them; if they changed since,
the page is out of date for them until updated.

<!-- limit:WP07 -->
> **Coming from the Graphics Builder:** its own Cut Link writes records that its next load changes
> again; ctgedit writes them as that load leaves them. The page looks the same. ctgedit picks
> templates by thumbnail rather than from three text lists.

## Situational Awareness pages

Situational Awareness projects build pages on a master page with panes; their content pages still
link to the `situational_awareness` templates. See
[Situational Awareness pages](situational-awareness.md). Template menus are filled at runtime, so
the editor shows placeholders.

See the AVEVA help: [Page Templates](aveva:1172095), [Screen Resolution](aveva:1172096),
[Sizing the Runtime Window](aveva:1172097) and [Create a Template](aveva:1172114).
