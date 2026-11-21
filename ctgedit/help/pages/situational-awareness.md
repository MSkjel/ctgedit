# Situational Awareness pages

Situational Awareness (SA) projects show their content in a **workspace**: a master page divided
into **panes**, each placed with the `sa_workspace.pane` genie. A pane shows pages by content
type, a default page, or the equipment the operator selected. The SA genies are ordinary genies
in ctgedit; this page covers the help ctgedit adds for them.

Content types, menus, equipment and colour themes are configured in Plant SCADA Studio. ctgedit
reads them from the page's project, its includes and its includers to offer lists.

## Workspace checks

When you save a page with SA panes, ctgedit checks the workspace the way the runtime reads it and
opens the **Workspace Checks** panel if it finds problems. It only reports; saves are never
blocked. Tools > Check Workspace (or **Check Again**) checks without saving; **Check when saving**
turns the automatic check off.

It checks for:

- panes without names, and duplicate names;
- more than one default pane, or none on a master page;
- `Workspace_Init()` missing from the master page's On page shown event; nested panes without
  `HasPanes` = `1`; more than 50 panes on a page;
- pane settings the runtime can't read, default pages that don't exist, missing tab header panes
  or controls, and undefined content types.

Double-click a row to select the pane; page-level problems open Page Properties.

## Pane labels

View > Pane Labels shows each pane's name, fill mode, default status, tabs and default page on
the canvas. The labels are never saved or printed.

## Pane and SA genie parameters

SA genie dialogs get pickers:

- **Equipment** parameters open [Choose Equipment](#choose-equipment).
- A navigation link's **Menu Item** opens [Choose a Menu Item](#choose-a-menu-item).
- Tab bar menus and tree data sources list the project's menus.
- A pane's **Tab Header Pane Name** and **Excluded Autofill Panes** list the page's other panes;
  **Tab Control Name** lists named objects on the header pane's default page.

## Choose equipment

**Select Equipment** shows the equipment as a tree of its dotted names, with full names and
projects. Type to search; double-click to use one. Equipment is added in Plant SCADA Studio
(System Model > Equipment).

## Choose a menu item

**Select Menu Item** shows the project's menu configuration as a tree. Pick the **Menu**, then the
item. Menus are made in Plant SCADA Studio (Visualization > Menu Configuration).

## Content types

Page Properties > General > **Content type** decides which panes take a page. A content page
without one is only shown by the default pane.

## Library help

SA library elements documented by AVEVA have a help link in the Library panel and a **Genie
Reference** button in their dialogs.

> **Coming from the Graphics Builder:** it offers plain text fields for these parameters and no
> checks. The genies, forms and pages are the same in both.

See the AVEVA help: [Situational Awareness Projects](aveva:1180262),
[Configure Panes on a Master Page](aveva:1180289),
[Create a Master Page for a Customized Workspace](aveva:1180298),
[Use Nested Panes](aveva:1180309) and [Content Types](aveva:1094525).
