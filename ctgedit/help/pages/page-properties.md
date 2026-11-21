# Page properties

File > Page Properties… (or right-click the empty page) opens the page's properties
([open them from here](action:page_props)), on the Graphics Builder's six tabs.

**Apply** or **OK** makes your changes one undo step. File > Save writes them into the project
(`pages.DBF`, `pgkey.DBF`) with the page. Compile the project for the runtime to use them.

Lists of pages, areas, devices, clusters and content types come from the page's project, its
includes, and the projects that include it, with each entry's project shown.

> **Coming from the Graphics Builder:** the tabs and fields are the same. Templates are chosen from
> thumbnails. The Previous and Next page lists leave out system pages (!) unless Settings > Pages
> and libraries > "List system pages in Page Properties" is on.

## General

- **Window title**: the runtime window's title, with `?name?` associations on super genie pages.
- **Description**: a note for maintainers.
- **Previous page** / **Next page**: the browse sequence for PagePrev() and PageNext().
  **Parent page**: the page this one sits under in Studio's hierarchy.
- **Content type**: used by Situational Awareness panes to pick pages.
- **Security**: **All areas**, or a specific **Area**.
- **Page scan time**: **Default** (`[Page]ScanTime`), or milliseconds (at most 60000).
- **Logging**: the **Log device** for keyboard command log messages.
- **Cluster context**: **Inherit from caller**, or a default cluster.

See the AVEVA help: [Page Properties: General](aveva:1172103) and
[Use a Browse Sequence](aveva:1172110).

## Appearance

### Template

The template picker: **Style**, **Resolution**, **Designed for showing title bar**, and
thumbnails. A missing template shows as "Name (not found)". Choosing another and clicking Apply
swaps the template objects and takes the new size, colour and page properties; your own objects
stay. See [Page templates](templates.md).

### View area, page colour and quality

- **Width** / **Height**: the runtime page size.
- **Page colour**: the background (no Transparent or flashing).
- **Ignore Quality**: how bad-quality values are shown.

See [Page Properties: Appearance](aveva:1172104).

## Keyboard Commands

Key sequences that run a command while the page is shown.

1. **Add…** a key sequence: key names separated by spaces, `#` per argument digit (such as
   `### ENTER`), or pick from **Keys**.
2. Type the **Key sequence command** (up to 254 characters).
3. Set **Security**.
4. Optionally add a **Log message** (up to 32 characters).

**Edit…**, **Remove** and **Clear Property** manage them. They're stored in `pgkey.DBF`. Object
keyboard commands are on the object's Input tab
([Object properties](properties.md#keyboard-commands)).

See [Page Properties: Keyboard Commands](aveva:1172105).

## Events

Pick an event and type its command (up to 254 characters):

| Event | Runs |
|---|---|
| On page entry | while the page opens, before it is shown |
| On page exit | when the operator leaves the page |
| While page shown | repeatedly at the page scan time |
| On page shown | once the page and its ActiveX controls are ready |
| On activate | when the page's window becomes active |
| On deactivate | when it stops being active |

ctgedit warns about commands the AVEVA help advises against, such as Login() in On page entry or
navigation in On page exit.

See [Page Properties: Events](aveva:1172106).

## Environment

Page variables Cicode reads with DspGetEnv(). **Add…** a **Property** (case-sensitive) and
**Value**, up to 127 characters each. Situational Awareness pages with nested panes need
`HasPanes` = `1` ([Situational Awareness pages](situational-awareness.md)).

See [Page Properties: Environment](aveva:1172107).

## Associations

Default values for the page's `?name?` super genie associations:

| Column | Meaning |
|---|---|
| Name | the association as `?name?`: letters, digits, `_` and `\`, at most 253 characters |
| Default | used when the association isn't made: a quoted literal (`'1.23'`) or a tag or equipment.item |
| Value on Error | shown when there's no default and the association doesn't resolve |
| Description | a note for maintainers |
| In Use | Yes when something on the page uses it (read only) |

Double-click a cell to edit it. **Add** offers associations the page uses but doesn't list yet.
See [Super genies](super-genies.md) and [Page Properties: Associations](aveva:1172108).
