"""Every environment variable ctgedit reads."""

from __future__ import annotations

from typing import Dict, Tuple

#: name -> (kind, what it does)
FLAGS: Dict[str, Tuple[str, str]] = {
    # -- where things are --------------------------------------------------------------------
    "CTG_LIB_PATH": (
        "config",
        "extra library folders the symbol resolver searches after the "
        "page's project (os.pathsep-separated); the tests use it for "
        "pages copied out of their project. The engine doesn't read it",
    ),
    "CTG_CONTROL_BIN": (
        "config",
        "the Plant SCADA Bin folder ctgedit gives the control host (backend/controlhost.py), "
        "where the embedded controls' own DLLs are found",
    ),
    "CTG_TEXT_REPLACE": (
        "config",
        "1 = text the code page cannot store is written as '?', as "
        "the Graphics Builder does, instead of refused (Settings > "
        "Pages and libraries)",
    ),
    "CTG_GENIE_NESTED_COPIES": (
        "config",
        "1 = a placed genie where only a nested genie or "
        "symbol changed takes new copies of those only, not "
        "a full composition (Settings > Pages and "
        "libraries)",
    ),
    "CTG_CTF_REMOVE_STALE": (
        "config",
        "1 = a save removes an out-of-date fast display "
        "(.ctF) file instead of writing a new one (Settings > "
        "Pages and libraries)",
    ),
    "CTG_LIB_SIBLINGS": (
        "config",
        "1 = the symbol resolver also searches the other projects of "
        "the install (Settings > Pages and libraries; the Graphics "
        "Builder does not)",
    ),
    "CTG_SCADA_BIN": (
        "config",
        "the Plant SCADA Bin folder (the compiler, the runtime); default: "
        "the install's (infra/install.bin_dir)",
    ),
    "CTG_AVEVA_HELP": (
        "config",
        "the installed AVEVA help's content folder (default: the install's, infra/install.docs_dir)",
    ),
    "CTG_CTSIM_PATH": ("config", "the simulator engine (ctsim.exe) to start instead of the bundled one"),
    "CTG_CODEPAGE": ("config", "the code page page text is decoded with (default 1252)"),
    "CTG_SCADA_ROOT": (
        "test",
        "a fake Plant SCADA install (a folder with Config\\citect.ini, "
        "User, Bin, Docs) every install lookup uses instead of the "
        "registry and the real folders; 'none' = no install "
        "(infra/install.py)",
    ),
    "CTG_FIXED_IDENTITY": (
        "test",
        "a seed: every new GUID and save time stamp a file gets "
        "is made from it (counter GUIDs, 2026-01-01 00:00:00), so "
        "generated files are the same bytes each run "
        "(infra/identity.py)",
    ),
    "CTG_FIXTURE_CHECK": (
        "test",
        "1 = tests/test_fixtures.py also writes the fixtures again "
        "(tools/make_fixtures.py --check) and compares them byte "
        "for byte with tests/fixtures/FxInstall (Windows)",
    ),
    "CTG_HERMETIC": (
        "test",
        "1 = the test guard (tests/hermetic.py): a test fails when it "
        "reads the real install, imports COM or starts a Citect "
        "program",
    ),
    "CTG_DATA_DIR": ("test", "the data folder (logs, recovery copies, memos); the tests isolate it"),
    "CTG_SETTINGS_INI": ("test", "the settings file instead of the user's; the tests isolate it"),
    "CTG_LOG_DIR": ("test", "the crash log folder"),
    "CTG_JOURNAL_DIR": ("test", "the write journal's folder (crash-safe saves)"),
    # -- what runs ---------------------------------------------------------------------------
    "CTG_HELPER_WAIT": (
        "config",
        "seconds a helper process waits for a project database or a "
        "page another ctgedit process is writing; the editor sets it "
        "for the helpers it starts (Settings > Pages and libraries > "
        "Busy projects: 'Helper processes wait')",
    ),
    "CTG_TEST_EXIT_AT": (
        "test",
        "a kill point (infra.paths.kill_point): the process ends "
        "there, as a crash or a kill would (the save and Update "
        "Pages tests)",
    ),
    "CTG_TEST_COMPOSE_LOG": (
        "test",
        "a file each composition appends its process id to (format.composer_store: which helper composed)",
    ),
    "CTG_NO_STUDIO": ("test", "never start Plant SCADA Studio for the Graphics Builder's COM"),
    "CTG_NO_RUNTIME": ("test", "never start the Plant SCADA runtime (Run)"),
    "CTG_NO_SIM_ENGINE": ("test", "the Simulate window without the engine"),
    "CTG_NO_SINGLE_INSTANCE": (
        "test",
        "a second ctgedit opens its own window instead of handing its files to the first",
    ),
    "CTG_NO_CTF_PRECHECK": ("test", "no fast-display (.ctF) trust check at open"),
    "CTG_NO_DISPLAY_CHECK": ("test", "no display self-check at open"),
    "CTG_NO_CONTROL_HOST": ("test", "1: embedded controls keep their placeholder (no control host)"),
    "CTG_NO_MODAL_QUESTIONS": (
        "test",
        "a question no test answered (ui/messages.choose) raises instead of waiting (the tests set it)",
    ),
    "CTG_NO_DIALOG_REAP": (
        "test",
        "answered dialogs stay the window's children (ui/dialog_release; the tests set it)",
    ),
    "CTG_NO_ANIMATION": (
        "test",
        "the canvas's zoom and fit changes jump instead of gliding, the Simulate window's too "
        "(ui/editor/viewnav.py), and nothing on the canvas changes on its own: its notes stay "
        "until ended and its scroll bars do not fade (ui/editor/canvas_look.still; the tests set it)",
    ),
    "CTG_SIM_MODEL_INPROCESS": (
        "debug",
        "make the Simulate page model in the editor's process (not the helper)",
    ),
    # -- how a page is read and drawn --------------------------------------------------------
    "CTG_NO_SYMBOLS": ("debug", "no library resolution: placed symbols and genies as stored"),
    "CTG_RAW_LABELS": ("debug", "label texts as stored, @() not resolved"),
    "CTG_NO_EMBEDDED": ("debug", "no embedded pictures"),
    "CTG_DYN_BADGES": ("debug", "draw a badge on every animated object"),
    "CTG_TEXT_DPI": ("test", "the DPI text is measured at (default: the system's)"),
    "CTG_DERIVE_CHECK": ("debug", "compare every incremental derive with a full one"),
    "CTG_GC_GUARD": ("debug", "0: leave Python's garbage collector as it is (A/B)"),
    "GP_MEMO": ("debug", "0: no genie composition memo (A/B)"),
    "GP_MEMO_RECORDS": ("debug", "the genie composition memo's size, in records"),
}


def describe(name: str) -> str:
    """'kind: what it does' of a declared variable ('' for another name)."""
    hit = FLAGS.get(name)
    return "%s: %s" % hit if hit else ""
