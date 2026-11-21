# Testing

## The suites

| Folder | What it tests | Runs |
|---|---|---|
| `tests/` | Everything, against a synthetic install (`tests/fixtures/FxInstall`). Hermetic: no Plant SCADA install, no COM, no network. | By default (`pytest`) and in CI |
| `tests_corpus/` | Reading, rendering and placing on the projects AVEVA ships (Example, ExampleSA, the include projects). Read only. | When named on the command line |
| `tests_live/` | Driving the Graphics Builder over COM, Plant SCADA Studio, the runtime or the simulator engine. | When named, on a machine with Plant SCADA |

## Running them

```powershell
# the whole hermetic suite, in groups of files run in parallel processes
.\.venv\Scripts\python.exe tools\run_tests.py <log> --workers 2
# another suite: --suite corpus | live | all
# one file
.\.venv\Scripts\python.exe -m pytest tests\test_x.py -q
```

Each worker is a separate Qt process: use fewer workers (`--workers 1`) if memory runs short.
`--durations 40` ends the log with the 40 slowest tests and files.
`CTG_SKIP_HEAVY=1` skips the heavy tests.
`--shard K/N` runs only the K-th of N parts of the suite. CI runs four parts as parallel jobs.

### Test helpers

- The tests set `CTG_NO_ANIMATION`: the canvas doesn't glide or fade, its notes (the arrow
  keys' readout, the alignment lines after a nudge, an Alt+click's note) stay until ended, and
  its scroll bars stay as they are (`still()` in `ctgedit/ui/editor/canvas_look.py`).
- `wait_for` in `tests/test_sim_engine_source.py` waits for a condition.
- `_worker_here(..., by_hand=True)` in `tests/test_save_chain_jobs.py` runs the genie jobs in
  the test's process. Each answer lands when the test calls `_poll()`.
- `canvas_picture()` in `tests/qtkit.py` is a picture of a canvas without the widgets laid
  over it.
- `check_speed()` in `tests/timing.py` checks a time limit when `CTG_SPEED_TESTS=1` is set:

```powershell
$env:CTG_SPEED_TESTS = "1"; .\.venv\Scripts\python.exe tools\run_tests.py <log> --workers 2
```

## The synthetic install

`tests/fixtures/FxInstall` is a small Plant SCADA install made of synthetic projects
(`FxMain`, `FxLib`, `FxSa`, `FxPerf` ...). `tools/make_fixtures.py` writes it; never edit the
files by hand. Tests take a copy of it through the `fx_install` fixture (`tests/fxinstall.py`),
so a test can change it freely. Each test process makes one copy and puts it back as it was
after each test; `CTG_TEST_FRESH_INSTALL=1` makes a new copy for every test instead.

`tests/fixtures/oracle/` holds pages as the Graphics Builder saved them, for tests that
compare our output with its output. Those captures were taken from the committed fixture pages,
so the pages they name (the manifest's `source` entries) stay as they are until the oracle is
captured again with `tools/live/fixture_oracle.py`.

`FxDemo` is a small water treatment plant: tank, pump and valve genies, two pages and Cicode
that runs the process. `tools/screenshots.py` takes the README's pictures from it, and CI
publishes them to the `screens` branch.

## Isolation

`tests/shared_fixtures.py` gives every test its own settings file (`CTG_SETTINGS_INI`), data
folder (`CTG_DATA_DIR`) and fresh process caches, and closes windows a test leaves open (at the
end of a module, every top-level widget the module left). A
test never writes into a real Plant SCADA project: it copies what it needs into `tmp_path`.

## Rules the tests enforce

| Test | Rule |
|---|---|
| `test_import_layers.py` | The package layering (see ARCHITECTURE.md). |
| `test_code_contracts.py` | Qt usage rules: no `parentItem()` / `topLevelItem()` on scene items, a delayed call has a context object, a record's bags are never changed in place. |
| `test_swallowed_errors.py` | The read / render path notes what it swallows; no module adds a silent broad handler. |
| `test_cache_registry.py` | Every cache registers and says when it is emptied. |
| `test_settings_keys.py` | Settings keys are declared once. |
| `test_window_state.py` | The main window's mixins share only declared state. |
| `test_help.py` | The manual's links, images and table of contents. |
| `tools/check_ui_text.py` | The UI text rules (see UI_GUIDE.md). |

## Environment variables

`ctgedit/infra/flags.py` lists every variable ctgedit reads. The ones tests use most:
`CTG_SETTINGS_INI`, `CTG_DATA_DIR`, `CTG_SCADA_ROOT` (the install to use),
`CTG_LIB_PATH`, `CTG_HERMETIC`, `CTG_NO_MODAL_QUESTIONS` (a modal question raises instead
of waiting), `CTG_NO_SIM_ENGINE`, `CTG_NO_RUNTIME`, `CTG_DERIVE_CHECK`.

## Corpus tests

`tests_corpus/` reads the shipped projects of the installed Plant SCADA. A test may also
use a page of another project on the machine through a role: `tests_corpus/local.json`
(git-ignored) maps a role name to a page, and `tests_corpus/local.example.json` documents
the roles. A role that is not set skips its tests. Committed files name only the shipped
projects and the synthetic ones.
