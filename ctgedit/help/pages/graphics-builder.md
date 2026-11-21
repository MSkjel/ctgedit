# Working alongside the Graphics Builder and Studio

ctgedit and the Graphics Builder edit the same files, so you can use both on one project, but
not on the same page or library at the same time. Plant SCADA Studio starts a Graphics Builder
of its own, even when you do not see it.

## The Graphics Builder keeps its own copy

When the Graphics Builder opens a page, it reads it once and works on its copy in memory. After
you save the page in ctgedit it still shows the old version, and if you save the page there, its
copy overwrites your change.

So ctgedit asks before saving a page that is also open in the Graphics Builder. **Save Anyway**
saves (then reopen the page in the Graphics Builder to see this version); Cancel leaves the page
unsaved here. Better: close the page in the Graphics Builder without saving first.

For the same reason:

- Update Pages skips pages that are open in the Graphics Builder (close them there and run it
  again);
- Pack Libraries is refused while the Graphics Builder runs;
- the Graphics Builder keeps every library element it has loaded in memory until it is closed,
  and writes its copy back when it saves that element. After changing a library in ctgedit,
  restart the Graphics Builder before you work with that library there. ctgedit reminds you once
  per session.

## A page changed by another program

When the Graphics Builder or another program changes a page or library you have open, ctgedit
asks what to do. **Reload** shows the version on disk. With unsaved changes you choose between
**Reload (Discard Mine)** and **Keep Mine**; if you keep yours, a later save asks before it
overwrites the other program's changes.

- Tick **Don't ask again** to keep your answer: Reload then reloads such a page by itself, and
  Not Now leaves it. A page with unsaved changes is always asked about.
- Settings > Pages and libraries > **When another program changes an open page** sets it
  again: **Ask me**, **Reload it** or **Do nothing**.
- You aren't asked about a library element that ctgedit itself has just saved over.
- A page that is being simulated follows: when ctgedit reloads it, the Simulate window shows
  the reloaded page. A simulated page that is not open in ctgedit reloads from its file when
  the Graphics Builder saves it.

## The install and project

ctgedit works on the active install: the one Studio, the Graphics Builder and the runtime use.
The status bar shows each page's install and project; it turns amber when a page is not in the
active install (a copy the Graphics Builder and the runtime do not see).

## Studio's own tasks

Some things are configured in Plant SCADA Studio, not in a graphics editor: tags, equipment,
content types, menus, alarms, the project's include list, clusters and the runtime setup. Tools >
External Tools starts the Plant SCADA tools: **Plant SCADA Studio** ([[F12]]), **Cicode
Editor**, **Runtime Manager**, **Computer Setup Wizard** and **Computer Setup Editor**.

> **Coming from the Graphics Builder:** you do not need the Graphics Builder to make a page
> reach the compiler and the runtime: a ctgedit save writes the same files and database rows.
> Keep one editor per page at a time and nothing is lost.

See [Graphics Builder](aveva:1172081) in the AVEVA help.
