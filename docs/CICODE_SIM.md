# The Cicode simulator

The Simulate window runs a page's Cicode and animation without Plant SCADA's runtime. The
engine is a separate .NET program in the `external/cicode-sim` submodule.

## The engine

- `ctsim.exe` (shipped with ctgedit): `serve` (the named-pipe server the editor talks to),
  `dap` (a debug adapter) and `servers`.
- `ctsim-dev.exe`: development commands (compile, page runs, world dumps ...).
- Build (Windows, .NET SDK):
  `dotnet build external\cicode-sim\engine\src\ctsim\ctsim.csproj -c Release -f net48`.
  `packaging\build.ps1` builds it and refuses to package without it.
- The protocol is documented in `external/cicode-sim/engine/PROTOCOL.md`.

## The editor's side

| Module | Role |
|---|---|
| `ctgedit/sim/client.py` | The connection to `ctsim serve`: sessions, requests, events. |
| `ctgedit/sim/pagemodel.py` | The page model the engine reads (`ctsim-page/0`): the page's animated objects and their expressions. |
| `ctgedit/sim/state.py` | One animated object's state for one scan, as the engine sends it. |
| `ctgedit/render/sim_states.py` | Draws the states on top of the page scene. |
| `ctgedit/render/sim_drawn.py`, `sim_alarmlists.py` | What the page's Cicode drew: shapes, text, alarm lists. |
| `ctgedit/ui/simulate/` | The Simulate window: canvas, tags and watch panels, inspector, outputs. |

## Tests

`tests/fake_ctsim.py` is a fake engine for the hermetic suite. `tests_live/` runs the real
engine on the shipped projects. The engine has its own test suite
(`external/cicode-sim/engine/tests`).
