# Architecture

ctgedit is a PySide6 editor for AVEVA Plant SCADA (Citect) graphics: it reads `.ctg` pages
and libraries natively, edits them, and writes files the AVEVA tools accept. A Cicode
simulator (a separate .NET engine, `external/cicode-sim`) runs a page's animation in the
Simulate window.

## Packages

| Package | What it holds | May import |
|---|---|---|
| `ctgedit/format/` | The file formats: the `.ctg` / `.ctF` container, page records, property bags, geometry, styles, libraries, genies, templates, composite genies, the project tables a save writes. Pure stdlib. | `infra` |
| `ctgedit/model/` | The document model (`Page`, `GraphicObject`) the reader builds and the renderer draws. | stdlib only |
| `ctgedit/infra/` | Data folder, activity log, caches registry, GC guard, autosave, backups, history versions, environment flags. | `format` |
| `ctgedit/io/` | Turning parsed files into the model (`reader.py` and `readerparts/`), project tables, tag scope, catalogues, Update Pages. | `format`, `model`, `infra` |
| `ctgedit/editing.py`, `ctgedit/doc/` | `EditableDocument`: one open page or library element. Its edits are mixins by topic in `doc/` (geometry, properties, structure, library, page operations, history). | `format`, `io`, `model`, `infra` |
| `ctgedit/render/` | Drawing the model on a `QGraphicsScene`, and the simulator's live states on top of it. | the above |
| `ctgedit/sim/` | The simulator client, the page model the engine reads, the state messages. | `format`, `io`, `infra` |
| `ctgedit/ui/` | The application. `editor/`: the canvas and what sits on it (its drawing, smart guides, selection and look are separate modules); `panels/`: the docks (Properties is split into its page form, tool section, search and kept forms); `dialog/`; `prefs/`: settings and keys; `jobs/`: long runs; `style/`: theme and glyphs; `browser/`: the page browser; `objprops/`: the object sheet; `simulate/`: the Simulate window; `window/`: the main window's commands. | everything |
| `ctgedit/backend/` | Plant SCADA's own programs: the compiler, the runtime, the Graphics Builder over COM, CtAPI, the control host. | `format`, `io`, `infra` |
| `ctgedit/diag/` | What a page open found: reports, a second opinion on what a page draws. | `format`, `io`, `infra`, `model` |
| `ctgedit/workers/` | Helper processes for batch work on closed pages: genie updates, Replace Genie, Find and Replace across a project, duplicate pictures. | `format`, `io`, `infra`, `editing` |
| `ctgedit/features/` | Page-level features with no UI of their own: Find / Replace, Check Page, Situational Awareness workspace checks, symbol set editing, object labels. | `format`, `io`, `infra`, `editing` |
| `ctgedit/help/` | The built-in manual (Markdown pages and a table of contents). | `infra` |

`tests/test_import_layers.py` enforces the layering: `format`, `model` and `infra` never
import `io`, `render`, `ui` or `editing`, and `io` never imports `render` or `ui`. The few
deliberate exceptions are listed in that test.

## From file to screen

1. `format/container.py` reads the container: a directory of named blocks.
2. `format/page.py` parses the `page` block into records, in depth-first order (a record's
   child count says how many of the following records are its children).
3. `io/reader.py` (`derive_page`) turns the records into the model: one `GraphicObject` per
   record, with its geometry, style, texts, animations, and the art of placed symbols and
   genies resolved from their libraries (`format/symbols.py`).
4. `render/scene.py` (`PageScene`) draws the model. The canvas (`ui/editor/canvas.py`, a
   `QGraphicsView`) shows the scene and handles the mouse and keyboard.

## Editing and saving

- An edit changes the record bytes (`format/geometry.py`, `format/writer.py` and friends), then
  derives again only what changed. A move or another in-place edit calls `_touch_model`
  (`CTG_DERIVE_CHECK=1` compares every incremental derive with a full one).
- Undo and redo keep compact snapshots of the changed records (`doc/history.py`).
- Save writes the page (`format/writer.py`), its fast display file (`format/ctf.py`) and the
  project database rows a page needs (`io/pgregister.py`), the way the Graphics Builder does.
- Library edits (genies, symbols, templates) are documents of their own (`format/element.py`,
  `format/element_session.py`); placing and updating genies is `format/genie_place.py`,
  `format/genie_norm.py` and `format/template_place.py`.

## Processes and threads

- The GUI process parses and derives on the GUI thread only. Work that parses many records
  runs in a helper process (`ctgedit/workers/`, started through `infra/workers.py`); the File >
  Open preview and the display checks run in their own processes too.
- Find in closed pages: the helper keeps the searchable fields of each page it reads on disk,
  with the files and library lookups its read depended on (`features/find_index.py`). The GUI
  process matches the kept fields of the pages that have not changed (no record is parsed
  there) and sends only the others to the helper.
- Threads may only read page summaries and thumbnails or build tag indexes. Garbage
  collection runs on the GUI thread (`infra/gcguard.py`); a worker thread calls
  `gcguard.before_worker()` first.

## The main window

`ui/main_window.py` builds the window (docks, actions, menus, ribbon). Its commands live in
mixins in `ui/window/` by topic (tabs, files, library, canvas edits, view, workspace ...).
State the mixins share is declared once on `ui/main_window_base.WindowState`;
`tests/test_window_state.py` checks that no mixin reads window state that is not declared.
`ViewState.ENABLE_WHEN` lists when each command is enabled.

## Caches, settings and logs

- Every process-wide cache registers with `infra/caches.py` and says when it is emptied
  (`tests/test_cache_registry.py`).
- Settings keys are declared once (`ui/prefs/settings.py`: `SETTINGS_KEYS`, `OPTION_DEFAULTS`);
  `tests/test_settings_keys.py` checks every use.
- The activity log is `infra/logsetup.py`. Every broad exception handler logs what it
  swallowed (`note_exception`) or gives a `swallow-ok` reason on its except line
  (`tests/test_swallowed_errors.py`).

## The simulator

See [CICODE_SIM.md](CICODE_SIM.md).

## More

- [CTG_FORMAT.md](CTG_FORMAT.md): the file formats, by part, with the module that owns each.
- [TESTING.md](TESTING.md): the test suites and how to run them.
- [CONTRIBUTING.md](../CONTRIBUTING.md): what to run before a pull request, and the Qt,
  threading and Plant SCADA pitfalls.
- [UI_GUIDE.md](UI_GUIDE.md): how the UI is written.
