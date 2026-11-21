# Update Pages

A page keeps its own copy of every template, genie and linked symbol it uses. When you change
one in its library, pages keep the old copy until they're updated. Tools > Update Pages… updates
them all at once, across a project and the projects it includes.

## Run Update Pages

1. Choose Tools > Update Pages… (or [open it from here](action:update_pages)). Nothing needs to
   be open.
2. Under **Projects**, pick the **Root project**. It starts at the open page's project, or the
   root you used last time, or Plant SCADA's active project. Any project can be the root, even
   one with no pages of its own. **Browse…** picks a project folder that isn't listed.
3. Under **Projects to update**, tick the projects whose pages you want updated. The **Pages**
   column shows how many registered pages each has. See [The include tree](#the-include-tree).
4. Under **Update**, choose what to bring up to date:
   - **Page templates**: pages whose template changed get the new template objects. This
     includes pages linked to a template built on another template that changed (see
     [Templates](templates.md#a-template-built-on-another-template)). "Also apply the
     template's page size, page colour and page properties" copies those too.
   - **Genies and super genies**: placed genies are composed again from the changed genie,
     keeping their parameter values. Super genie pages and composite genies are recreated.
   - **Symbols**: placed symbols take the new size and shape, keeping their position.
   - **Save the library genies and templates in the chain first**: also updates the genies and
     templates in the libraries before the pages. See [The chain](#the-chain).
   - **Update every page, also the ones that are up to date**: skips the change check and
     rewrites every page. Use it when a page doesn't match its library but Update Pages says
     it's up to date. It's much slower.
5. Leave **Back up each page before it is changed** ticked.
6. Choose how many **Helper processes** to use (see [Helper processes](#helper-processes)).
7. Click **Update Pages**.

A progress window shows how far it has got (see [Progress figures](#progress-figures)).
**Stop After This Page** stops the run; the pages already done stay updated (the summary lists
them). **Cancel** in the window before the run changes nothing.

Only pages that use a changed element are rewritten. Each is saved normally: the page, its
fast-display file and its project database rows. Compile the project afterwards to see the
changes in the runtime.

- **Pages open in ctgedit** are updated in their tabs. A page with no changes of yours on it
  (only the update made when it opened) is saved there too. One with your changes stays
  unsaved: save it to keep the update.
- **Pages open in the Graphics Builder** are skipped, since it would overwrite them. Close them
  there and run Update Pages again.
- Page files that aren't registered in their project are left alone.
- A page that still links a genie or symbol it no longer places is saved without the link when
  that element changes, as the Graphics Builder would.

## The summary

When the run ends, the summary opens in its own window:

- **Copy** puts the text on the clipboard.
- **Open Log** opens the activity log, which says what happened to each page.
- **Open Backup Folder** shows the backup, if one was made. Tools > Restore Backup puts it back.
- **Compile Project** compiles the project so the runtime shows the changes.

The first line also goes to the window's messages, as a warning if anything was left undone.
Update Users, Pack Libraries and Restore Backup use the same window.

## The include tree

- Each project appears once, where it's first included.
- Projects with no pages are shown greyed out.
- AVEVA's Include and System projects aren't listed and are never changed.
- Every project with pages starts ticked. ctgedit remembers the ones you clear, per root project.
- The line under the tree says how many pages will be checked.

## The chain

A genie that places another genie keeps its own copy of it, and so does a template. Update
Pages on its own only updates pages, so if genie B places a changed genie A, the pages get the
new A but B in its library keeps the old one.

"Save the library genies and templates in the chain first" fixes that. Before updating pages,
it saves every genie, super genie, template and symbol in the ticked projects' libraries that
places a changed element, deepest first, as Edit > Library > Update Users does (see
[Update users](library-elements.md#update-users)). Projects with no pages are always included,
since they often hold the genie libraries.

"Also compose their nested genies again where they differ from their libraries" catches genies
that claim to be up to date but hold an old nested copy. Each genie is composed and compared,
and any that differ are saved with their nested genies composed again. This is slow on large
libraries.

The chain leaves alone:

- Libraries of projects you didn't tick.
- Libraries that ship with Plant SCADA.
- Library files that can't be read. The summary names them.
- Genies open in a tab with unsaved changes. Save them and run again.

Library files go into the same backup as the pages. If the Graphics Builder is running,
ctgedit reminds you to restart it afterwards, since it caches library elements.

## Progress figures

The progress window shows what it's doing ("Checking the pages: 5 of 120 done"), the time so
far with an estimate of what's left, and what has happened to the pages so far (updated,
unchanged, skipped, failed). Settings > Pages and libraries > Progress figures turns these off
or tunes the estimate.

## Helper processes

Pages are updated in helper processes so the editor stays usable. More helpers finish a big
project sooner.

- **Automatic** uses half the processors, at most 4, limited by free memory. You can pick 1 to
  16 instead.
- Each helper uses about 400 MB and gets at least 8 pages.
- Pages with composite genies are done by one helper at the end.

## When a page cannot be updated

- **The project database is in use** (Plant SCADA Studio has it open): ctgedit asks whether to
  update the other projects. Close Studio and run again.
- **Changed by another save**: the page is checked again. If it keeps changing, run again later.
- **Rows not written yet**: a page was saved but its database rows weren't. The next run writes
  them first.
- **Not checked**: pages left after Stop After This Page. Run again to check them.

You can keep editing while Update Pages runs. Your own saves wait for it.

## How ctgedit knows a page is out of date

Each page records the version of every library element it uses. It's out of date when a
library has a newer version, or when a genie it places nests a genie that changed since. Pages
that are up to date aren't touched.

A page can stay out of date when there's nothing on it to update, for example when only a
nested genie changed and its parent genie hasn't been saved again yet (see
[The chain](#the-chain)).

When the Graphics Builder saves a page while a genie library is missing, it leaves placeholders
(see [A genie, symbol or template is missing](troubleshooting.md#a-genie-symbol-or-template-is-missing-or-empty)).
Once the library is back, Update Pages places the genies again. The same applies to genies and
templates that hold placeholders.

## Undo an Update Pages run

With the backup option on, every page, its database rows and any library files the chain saves
are copied first. Tools > Restore Backup… lists the runs and puts them back. See
[Restore a backup](troubleshooting.md#restore-a-backup).

## Other ways a page catches up

- Opening a page updates its changed template, symbols and genies. You can undo it, and turn it
  off in Settings > Pages and libraries. See
  [When you save a genie](library-elements.md#when-you-save-a-genie).
- Saving a template, symbol or super genie offers to run Update Pages.
- Tools > Update Template on This Page reapplies the current page's template.

> **Coming from the Graphics Builder:** this is its Tools > Update Pages, with a free choice of
> root and included projects, a choice of what to update, parallel helpers and a backup.

See [Update Pages in the AVEVA help](aveva:1172117).
