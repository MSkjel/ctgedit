# tools/

Every script here, with the first sentence of its docstring. Scripts that drive a live
Graphics Builder are in tools/live/. Regenerate the list after adding or removing a tool:

    .venv\Scripts\python.exe tests\test_tools_index.py --write

## tools/

- `check_ui_text.py`: Check the UI text rules a script can check.
- `com_duplicate.py`: Check that PageSaveAs can create a page from a registered one.
- `com_newpage.py`: Create a page through COM and check it, then clean up.
- `com_smoketest.py`: Smoke test of the Graphics Builder COM server from 64-bit Python.
- `com_validate.py`: Compare our parser with the Graphics Builder COM engine (read only).
- `com_validate_factory.py`: Check that objects from the factory load in the Graphics Builder.
- `com_validate_inplace.py`: Check that our writer's output loads in the Graphics Builder, with backup and restore.
- `com_validate_write.py`: Write an edited page, open it in the Graphics Builder, then clean up.
- `command_sweep.py`: Fire every editor command and menu entry on copies of pages, and report exceptions.
- `corpus_index.py`: A queryable index of every record of every page (and library element) in the live corpus.
- `cross_vectors.py`: Write the vectors ctgedit and the Cicode engine both check against.
- `ctdraw_inspect.py`: Inspect and drive the real Graphics Builder.
- `dialog_gallery.py`: Screenshots of every dialog (tests/dialog_gallery.py) at 100 % and 150 % text, with what the layout check finds in each, to look at them side by side.
- `e2e_demo.py`: Open, edit, save, then check the Graphics Builder opens the result.
- `engine_roundtrip.py`: Push our records through the Graphics Builder's PageOpen + PageSave and capture what it writes back.
- `gen_parity_md.py`: Generate PARITY.md from the inventory and a status map.
- `genie_recompose_sweep.py`: Check that placed genies still draw after recomposing, across the corpus.
- `gesture_fuzz.py`: Random canvas gestures on copies of pages, then Undo back and compare.
- `live_editor_shot.py`: Screenshot the real CtDraw canvas for a page, next to ours.
- `lock_requirements.py`: Regenerate requirements.lock.
- `make_fixtures.py`: Write tests/fixtures/FxInstall, a small fake Plant SCADA install.
- `pin_corpus.py`: Pin the corpus files the tests read.
- `release_check.py`: Release gate: run before tagging or building a release.
- `render_compare.py`: Compare our renders with the embedded thumbnails.
- `render_page.py`: Render a page headless to PNG.
- `run_tests.py`: Run the test suite in groups, several pytest processes at once.
- `screenshots.py`: Take the README's screenshots from the fake install's FxDemo project.
- `session_soak.py`: Drive a long editor session and compare it with fresh-process baselines.
- `sim_coherence.py`: How closely the simulator's pages match the live runtime captures.
- `sim_outside_check.py`: Check the simulator's DLL and SQL access against the real things.
- `sim_page_capture.py`: Record live page captures for the simulator's page conformance.
- `sim_page_compare.py`: Compare the simulator's pages with the live runtime captures.
- `sim_page_model.py`: Export a page as the Cicode engine's ctsim-page/0 model (ctgedit/sim/pagemodel.py).
- `sim_page_model_check.py`: Acceptance checks of the ctsim-page/0 export (ctgedit/sim/pagemodel.py). Read only.
- `sim_scene_bench.py`: Time PageScene.apply_states on a big page.
- `sim_startup_soak.py`: Start-up and race soak of the Simulate window on the real simulator.
- `style_block.py`: Decode an object's style block.
- `sweep_guard.py`: Safety net for the sweeps that drive the whole editor. Import it before PySide6 and ctgedit.
- `symbol_set_live_check.py`: Check symbol set edits in the live Graphics Builder (Studio running, no builder window open).
- `text_extent_check.py`: Live DPI check: does ctgedit measure text boxes the way the Graphics Builder does in THIS Windows session?
- `typecode_correlate.py`: Correlate type codes with object kinds across the example pages.
- `typecode_profile.py`: Profile type codes against object kinds.
- `validate_container.py`: Check the container directory layout across all example .ctg and .ctF files.
- `verify_parser.py`: Stress the .ctg parser.

## tools/live/

- `ctdraw_dialogs.py`: Open the click-only dialogs in the live CtDraw with the mouse.
- `ctdraw_genie_dialog.py`: Read the live Graphics Builder's genie parameter dialog.
- `ctdraw_live_rt.py`: Edit a page with ctgedit and check the real CtDraw shows the edit.
- `ctdraw_load.py`: Load a page in the live Graphics Builder and screenshot it.
- `ctdraw_move_test.py`: Move a genie with ctgedit and check CtDraw shows it moved.
- `ctedit_compare.py`: Screenshot pages in the live Graphics Builder next to our render.
- `dbf_schema.py`: Write ctgedit/format/dbf_schema.json from the install's citect.frm and tables.
- `fixture_oracle.py`: Capture the fixtures through the real CtDraw, once.
- `livepaths.py`: Paths for the live tools.
- `montage.py`: Put the compare PNGs into contact sheets.
- `ours_render.py`: Render a page as the editor shows it after opening, to PNG.
- `page_health.py`: Parse, build and render every page of every project, and flag crashes and oddities.
- `roundtrip_check.py`: Check that our writer round-trips the live pages byte for byte.
