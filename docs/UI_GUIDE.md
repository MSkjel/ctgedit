# UI guide

## Text

`tools/check_ui_text.py` checks the rules a script can check.

- Plain, short sentences. Say what happens, not how it works inside.
- UK spelling: colour, centre, organise.
- No em or en dashes. Use a colon, a comma or two sentences.
- Commands and menu items in title case (Save As, Update Pages); messages in sentence case.
- A command that opens a dialog ends in an ellipsis (Page Properties…).
- Tooltips say what a control acts on and its scope (this page, the open pages, the project).
- Error messages say what went wrong and what to do next, without codes the user cannot act on.
- Name a button by its action (Replace, Save All, Discard), not OK, when the action is
  destructive or not obvious.

## Dialogs and windows

- A new dialog reuses the picker an existing dialog uses for the same choice.
- Enter is the safe default when a dialog would discard or overwrite something.
- Long-running work shows progress and can be stopped ("Stop" / "Stop After This Page").
- Windows that stay open while the user works (Find and Replace, Page Checks, the Simulate
  window) are modeless and follow the page shown.
- Dialogs and panels lay out for the window's text size; nothing is a fixed pixel size that
  text does not fit. tests/test_dialog_layout.py checks every dialog of tests/dialog_gallery.py
  at 100 % and 150 % (a new dialog gets a builder there); `tools/dialog_gallery.py` saves their
  screenshots.
- A project is picked with `pick_fields.ProjectScopeField`; a dialog's other choices are kept
  with `ui/remember` (`keep`, `keep_one_of`, `file_path`). Values of the object being edited are
  loaded from it, not remembered.
- A helper-process run in a dialog goes through `jobs/helper_job.DialogJob` (progress, Stop,
  Open Log); a run of the window's, through `JobProgress`.
- Questions and notices go through `ui/messages` (`confirm`, `choose`, `warn`, `inform`,
  `ask_text`, `ask_item`); `CTG_NO_MODAL_QUESTIONS=1` makes one the test did not answer raise
  instead of waiting.
- Buttons: the dialog's own actions left (QDialogButtonBox ResetRole), OK / Cancel / Apply /
  Close and Help together right; Enter is a default button; no two controls share an Alt
  letter.
- A widget class never hides one of Qt's getters (`style()`, `rect()`, `font()` ...) with a
  method or attribute of its own (tests/test_widget_getters.py).

## State

- A document's UI state (fold state, scroll, chosen tab) lives in `doc.ui`
  (`DocumentUiState`), not on the window.
- Settings keys are declared once in `ui/prefs/settings.py`, with their defaults.

## Help

Every user-visible feature has a page or a section in the built-in manual
(`ctgedit/help/pages/`, listed in `toc.json`). Change the manual with the feature.
