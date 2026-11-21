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
  text does not fit.
- `CTG_NO_MODAL_QUESTIONS=1` makes a modal question raise instead of waiting (tests).

## State

- A document's UI state (fold state, scroll, chosen tab) lives in `doc.ui`
  (`DocumentUiState`), not on the window.
- Settings keys are declared once in `ui/prefs/settings.py`, with their defaults.

## Help

Every user-visible feature has a page or a section in the built-in manual
(`ctgedit/help/pages/`, listed in `toc.json`). Change the manual with the feature.
