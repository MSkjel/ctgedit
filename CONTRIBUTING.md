# Contributing

Set up a checkout as the README describes under
[Building from source](README.md#building-from-source). [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
explains how the parts fit together.

## Before you open a pull request

- Run the default test suite: `python tools/run_tests.py test-log.txt`. Each worker is a
  separate Qt process, so pass `--workers 1` if memory runs short.
- Format and lint with ruff: `ruff format .` and `ruff check .`.
- If you changed text the user sees, run `python tools/check_ui_text.py` and follow
  [docs/UI_GUIDE.md](docs/UI_GUIDE.md).
- Change the built-in manual (`ctgedit/help/pages/`) together with the feature.
- To change the fake install the tests use, change `tools/make_fixtures.py` and run it. Never
  edit the files in `tests/fixtures/` by hand ([docs/TESTING.md](docs/TESTING.md)).

Contributions are licensed under the GPL, version 3 or later, like the rest of the project.

## Pitfalls

Most have a test that fails when the rule is broken.

### Qt and PySide6

- Never call `QGraphicsItem.parentItem()` or `topLevelItem()` on scene items. PySide6 hands
  the C++ item to Python and may delete it when the wrapper goes. Keep your own maps instead.
- A delayed call takes a context object: `QTimer.singleShot(ms, context, fn)`. Without one,
  the call can run after the object it touches is gone.
- To drop a widget, hide it, call `setParent(None)`, then `deleteLater()`, all at once. A widget
  left to `deleteLater` under a parent that was deleted first crashes the next flush of
  deferred deletes.

### Threads and processes

- The GUI process parses records and builds pages on the GUI thread only. Batch work on many
  pages goes to a helper process (`ctgedit/workers/`).
- Other threads may only read page summaries and thumbnails or build tag indexes.
- Garbage collection runs on the GUI thread (`ctgedit/infra/gcguard.py`). Call
  `gcguard.before_worker()` before starting a worker thread.

### The document model

- A record's property bags are a tuple shared with the parse cache. Never change them in
  place: build a new tuple.
- An edit that changes the model in place calls `_touch_model` (a move passes its vector).
  `CTG_DERIVE_CHECK=1` compares every incremental update with a full rebuild.
- Every process-wide cache registers with `ctgedit/infra/caches.py` and says when it is
  emptied.
- Settings keys are declared once, in `ctgedit/ui/prefs/settings.py`.

### Errors

- The read and render path records every error it swallows (`_note_exc("module.func")`), or
  marks an expected one `# swallow-ok: <why>`. Other modules may not add new silent handlers.

### Plant SCADA

- Tests never write into an installed project. Copy what you need into `tmp_path` and point
  `CTG_SCADA_ROOT` or `CTG_LIB_PATH` at the copy.
- `citect.ini` is shared with the Graphics Builder and the runtime. Change it only through
  `ctgedit/io/citect_config.py` or `ctgedit/backend/runtime.py`, which keep a backup of the
  old file.
- The tools that drive the Graphics Builder over COM (`tools/live/`) need Plant SCADA Studio
  running, and a leftover `CtDraw32.exe` process blocks COM.
- Never post mouse clicks to the Graphics Builder's canvas: a click drags the object under it.
- Never call `AttributeText*` on an object that is not a text.
