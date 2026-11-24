# Simulate a page

The Simulate window runs a page: objects move, turn, change colour, fill, show values and swap
symbols scan by scan. You can change the tags the page reads and click its buttons. Nothing is
saved, and the page in the editor doesn't change.

Pages run in the Cicode simulator (ctsim), the same one the Cicode extension for VS Code uses.
If the simulator can't run a page, the window shows it as saved and says why at the top.

## Open a page in the simulator

- Tools > Simulate Page ([[Ctrl+F5]]), the Simulate button next to Compile and Run, or
  right-click the page and choose **Simulate Page**.
- The page runs as it is in the editor now, unsaved edits included. The simulator compiles
  what it needs.
- Later edits reload the running page automatically (see
  [Editing while simulating](#editing-while-simulating)).
- A page that was only copied into the folder, never saved into the project, can't run. Open it
  in ctgedit and save it.
- Close a tab to stop its simulation. Right-click a tab for **Close**, **Close Others**,
  **Reload Page** and **Edit Page in Editor**.

## Docking the Simulate window

The Simulate window starts as its own window. Drag its title bar into the editor window to dock
it, or out again to float it. It opens next time the way you left it.

While you work in it, keys go to the simulated page, not the editor. The Simulate window's own
keys (Run [[F5]], Fit Page [[Ctrl+0]], Inputs [[Alt+1]] and so on) work there; in the editor
the same keys do the editor's commands.

Closing the Simulate window, or the editor window it's docked in, stops its simulations.

## The Cicode simulator

- Simulating a page connects to a simulator for the page's project, starting one if none is
  running. All pages of a project share one simulation: tags, clock and log.
- An included project runs as part of the project that compiles it, as in the runtime. When
  several projects compile it, the [Tags panel](tags-panel.md)'s **From:** box picks which.
- The status bar says which simulator and project the page runs in. If something's wrong, a
  line over the page says why, with **Reconnect** and **Choose Simulator…**.
- A simulator ctgedit started stops shortly after its last page closes, unless another program
  still uses it.
- **Choose Simulator…** picks ctsim.exe if ctgedit can't find it.
- Cicode files changed since the last compile are compiled by the simulator itself.

## The page

- The page is drawn as the runtime shows it: no animation point marks, Cicode object icons or
  pane labels, and labels show their text rather than `@(...)`.
- Zooming and panning work as on the editor canvas: the mouse wheel, the middle button,
  [[Space]] + drag, and the zoom commands **Fit Page** ([[Ctrl+0]]), **Zoom 100%**
  ([[Ctrl+1]]), **Zoom In** ([[Ctrl+=]]) and **Zoom Out** ([[Ctrl+-]]).
- A new tab fits the page. Settings > Simulate can open pages at 100 % instead, as the runtime
  does.
- Clicks and drags go to the object under the pointer that takes input, as in the runtime.
- Only the left button runs touch commands and moves sliders. Other buttons and modified clicks
  run the keyboard commands for those mouse keys (RBUTTON_UP, MBUTTON_DN and so on).
- Keys go to the page's keyboard commands, coded as the runtime codes them (Ctrl+S is
  KEY_S_CTRL). A few window commands keep their keys: Open Page, Inspector, Copy Image and the
  zoom keys. Settings > Keyboard changes those.
- **Page Keys** on the toolbar turns key forwarding off.
- Point at an object to see its tool tip.

## Editing while simulating

- Edit the page in the editor and it reloads in the simulator a second after your last change.
  Its open events run again; tags, alarms and the clock keep their state. **Reloading the
  page…** shows until it's drawn again.
- Saving a genie, symbol or template library the page uses reloads it with the new drawing.
  If the editor is still updating the page's genies, the reload waits for that.
- When the editor updates the page from its libraries on its own after the simulation started,
  the window says **The editor updated this page from its libraries.** and **Reload Page**
  picks it up. **Also when the editor updates it from its libraries** (Settings > Simulate)
  reloads automatically instead.
- When another program saves the page, it reloads from the editor or from its file.
- With **Reload the page when it changes in the editor or on disk** off, the window tells you
  instead and **Reload Page** reloads.
- Saving a .ci file of the project recompiles it about half a second later. Compile errors
  show in Outputs; the old code keeps running until the file compiles.
- A simulation another program started (such as VS Code) isn't reloaded automatically.
  **Reload Cicode** on the toolbar compiles the changed files.

## Windows the page opens

- Pinned windows (WinNewPinAt, workspace panes) are drawn inside their parent and zoom with it.
- Other windows (popups, AssPopUp, WinNewAt, WinNew) float over the Simulate window at their
  position. Drag the caption to move one. Clicks go to that window's page, and the Inputs panel
  follows the window you last clicked.
- Zoom over a popup, or drag its corner, to size it separately. Double-click its caption to
  follow the page's zoom again.
- Closing a popup closes the window in the simulator. Closing a tab closes the windows opened
  from it, and runs the page's close events.
- **Open each window in a tab of its own** and **Open each page in a new tab** (Settings >
  Simulate) give windows and page changes their own tabs.

## Opening other pages

**Open Page…** ([[Ctrl+O]]) opens another page of the project in a new tab, in the same
simulation: same tags, alarms, clock and user. The startup code doesn't run again.

## The project's startup code

When ctgedit starts the simulator, it starts the page the way a runtime user reaches it:

1. The project's startup page (`[MultiMonitors]StartupPageScreen1`, else `[Page]Startup`, else
   `!Startup`) shows in window 0.
2. The startup code runs: Include's `_CitectBegin`, then the project's `StartupCode` parameters.
3. Once that has settled, your page is shown in window 0, as a page button would show it.

So the page sees whatever the startup code set up, and its back button has somewhere to go.
Outputs lists what ran. It runs once per simulation, on a clock 20 times faster until it
settles.

Turn it off with **Run the project's startup code** (Settings > Simulate), or run it at normal
speed with **Run the startup code on a fast clock** off.

## SA pages and their workspace

A Situational Awareness page never runs on its own in the runtime: the startup page builds a
workspace first, and navigation shows the page in a pane. Opened alone, its trees, lists and
buttons stay empty.

So when the startup page builds a workspace, the Simulate window does the same: the startup
page opens in window 0, the workspace starts, and your page is shown in its pane. Each pane gets
its own tab. If there's no pane for the page, it opens on its own and Outputs says why.

Turn it off with **Run SA pages inside their workspace** (Settings > Simulate).

## Dialogs the page shows

Popup menus (`DspPopupMenu`), `Message`, `Input` and `Form` dialogs open over the Simulate
window. The calling task waits for your answer; the rest of the simulation keeps running.
Closing a tab cancels its dialogs, and a dialog nobody answers closes after 60 seconds.

## Back to the editor

[[Shift]] + right-click an object on the simulated page for **Edit in Editor** (selects it in
the editor and opens its properties), **Show in Editor** and **Inspect Object**. A plain
right-click goes to the page. Outputs lines have **Edit in Editor** too.

## DLLs and databases

Page Cicode can call DLLs (`DLLOpen`, `DLLCall` and friends) and use SQL databases (`SQLConnect`,
`SQLExec` and friends). The simulator does these the way the runtime does, but only if you let
it: a DLL runs its own code on your computer, and the database is the real one. Both are **off**
by default, in Settings > Simulate > DLLs and databases.

- **Off**: `DLLOpen` fails as for a missing file (-1, error 289) and `SQLConnect` as for a down
  database (-1, error 307). Outputs lists the calls under Not simulated.
- **Ask**: you're asked before a DLL is loaded, before a database is connected, and before any
  statement other than a plain `SELECT` runs. Answer **Allow Once**, **Allow for This
  Simulation**, **Always Allow** or **Deny**. Unanswered questions are denied after 60 seconds.
  Page expressions can't wait, so their call fails and your answer applies from the next call.
- **On**: everything is allowed without asking.

**Allowed DLLs…** and **Allowed Databases…** manage the always-allowed lists. **Call time
limit** stops DLL calls that never return.

- The simulator is 32-bit, like the runtime, and uses the same DLLs and ODBC drivers.
- DLLs run in a helper process, so a crash ends the helper, not the simulation.
- `SQLTraceOn` writes to Outputs. No trace file is written.

## Devices, trends and schedules

The simulator keeps these for the length of a simulation:

- **Devices** (`DevOpen`, `DevWrite`, `DevRead` and friends): text and dBASE devices read and
  write their files in the simulation's own folder, never the project's. Printer devices write
  to a text file there. SQL devices fail as in the runtime without a database.
- **Event trends**: `TrnEventSetTable`, `TrnSetTable` and `TrnEventGetTable` keep the samples a
  page writes, and reading them back gives the same values.
- **Special days** (`SchdSpecialAdd`, `SchdSpecialItemAdd` and friends): groups and their days
  can be added, changed, removed and listed.
- **Server restarts**: `ServerRestart` takes the server offline for 5 simulated seconds, so
  `ServerIsOnline` gives 0 until it is back.
- **Run-time key commands** (`KeySetSeq`): typing the sequence on the page calls the function.
  The command lasts until another page is shown.

## The clock

- **Run** ([[F5]]) starts the clock and **Pause** ([[Shift+F5]]) stops it.
- **Step** ([[F10]]) runs one scan while paused. Settings > Simulate can make it ten scans or
  one second.
- **Speed** runs from 0.25x to 10x.
- The bar shows scans done and simulated time.

## The user

**User** in the clock bar sets who's logged in. Objects that need a privilege only take clicks
when the user has it.

- **Full access** (default): every privilege everywhere.
- **Not logged in**: as the runtime starts.
- A project user: their roles, no password needed.

## Inputs

The Inputs panel lists the tags the page reads, grouped by equipment.

- **Search tags** filters by name, equipment or comment.
- Tags the page's Cicode reads while it runs are added under **Read by the page's Cicode** as
  they're used.
- Clear **Only tags this page reads** to list every tag in the simulation.
- Select a tag to change it under **Selected tag**: a slider for numbers, **On** for digitals,
  a text box for strings, plus its **Quality**. Double-click a digital tag to toggle it.
- **Lock the value** stops Cicode changing it. Ignored writes are noted in Outputs.
- For a popup, tags read through the window's associations are listed, with the
  **Association** column showing where each comes from.

### Tags the project does not define

Tags the page reads that the project doesn't define are listed under **Not defined in the
project**, with a type guessed from how they're used. They read as not found, as in the
runtime. Tick **Simulate undefined tags** (Settings > Simulate) to set them in the simulator.
The project isn't changed.

### Page variables

Values the page keeps with `PageSetInt` / `PageSetStr` are listed under **Page variables** once
the code has used them. Select one to set it, for example to show a menu without clicking.

### Parameters

Parameters read with `ParameterGet` are listed under **Parameters** as `[Section]Name`, with
where each value came from. Select one to override it for the simulation; clear it to go back.

### Alarms and equipment

- **Alarms**: the configured alarms and their state. Select one and set its **State**. It holds
  until one of its tags changes.
- **Equipment**: MODE, STATE and DRMODE for each equipment. Double-click a value to set it.
- **Watch**: see below.

### Driving a tag

**Drive** writes a signal to a tag on the simulated clock: **Ramp**, **Sine**, **Random** or
**Square** (with **Duty**), between Min and Max over Period seconds. **Off** stops it.

### Properties

The **Properties** tab overrides a tag's definition for the simulation: scaling, units, format,
comment, deadband, I/O device, address and Custom 1 to 8. Overrides are bold; **Reset** puts the
project's value back.

## Watch

Type a tag name or a function call with literal arguments and press Enter. Its value updates
every second, run in the page's window. A call that changes something changes it every time, so
use watches for reading. **Call a function in the page's window** runs a call once.

Both boxes help like an expression field (see [Colours, underlines and hints](properties.md#colours-underlines-and-hints)):
they suggest the page's tags and functions as you type, colour the Cicode and show a function's
arguments. Hold the pointer over a tag to see its value now.

## Scenarios and snapshots

**Scenario** on the toolbar:

- **Record** records your tag writes and clicks; choose it again to save them.
- **Play Scenario…** plays a scenario file (`ctsim-scenario/0`; see the simulator's
  `scenarios/README.md`). **Stop Scenario** stops it.
- **Save Snapshot…** and **Load Snapshot…** save and restore tags, overrides and page variables.

## Simulate settings

**Settings…** on the toolbar opens Tools > Settings at its Simulate page:

- **Open each page in a new tab** and **Open each window in a tab of its own**.
- **Run the clock when a page opens**.
- **Ask before closing a simulation with a recording or changed tags**.
- **Space + drag pans the page** (on). Off sends [[Space]] to the page.
- **User at start**.
- **Simulate undefined tags**.
- **Run the project's startup code** (on) and **Run the startup code on a fast clock** (on).
- **Show trend history from the start** (on): trends show each pen's first value back to the
  start, so long spans fill at once.
- **Run SA pages inside their workspace** (on).
- **Clock speed at start**, **Step runs** and **Open a page at**.
- **Reload Cicode when a .ci file changes** and **Reload the page when it changes in the
  editor** (both on), **Also when the editor updates it from its libraries** (off), and their
  timings.
- **A text before its value**: what a Cicode-driven text shows until it gets a value.
- **Show errors on the page** and **Show object numbers (AN) in tool tips**.
- **Stop at breakpoints in page code** (on) and **Also stop page code on its hardware errors**
  (off).
- **DLLs** and **SQL databases** (both off), with their allowed lists.
- **Draw text without smoothing, like the runtime** (on).
- **Panels**: how many tags Inputs lists, how many lines Outputs keeps, and how often the
  Inspector and Watch refresh.
- **Simulator**: which ctsim.exe to use.

## Outputs

The Outputs panel shows what the simulation did. **Log** lists each line with its type:

| Type | What it holds |
|---|---|
| Cicode error | Hardware errors the page's Cicode raised |
| Task | Tasks started, ended and halted |
| Cicode message | TraceMsg, ErrLog, DspError and other messages |
| Tag write | Values written to tags, alarms, equipment and parameters |
| Not simulated | Calls the simulator doesn't simulate |
| Reload | Cicode files and pages reloaded |
| Startup | Load notes, startup code and workspace |
| Scenario | Scenario steps and results |
| Simulator | The simulator's own notes |

Select a line to see all of it below the list: the message, the object and its expression, and
the call stack.

- The **error**, **warning** and **info** buttons show counts and toggle each.
- **Types** toggles each type. Task lines are hidden by default.
- The search box takes comma-separated words; `!word` excludes. For example `Bad tag, !FIL1`.
- Right-click a line to hide that message, hide or show only its type, copy it, or
  **Show on Page**.
- **Group repeats** (on) folds identical lines from the same place into one row with a
  **Count**.
- **Clear Log** empties the log and the error marks its lines put on the page. The **Not
  simulated** list stays.

## Debugging on the page

- **Show errors on the page** (on) marks objects whose code reported an error (red) or warning
  (orange), with a count.
- **Show object numbers (AN) in tool tips** adds the AN to tool tips.
- **Not Simulated** lists the functions the page called that the simulator doesn't simulate.

## The Inspector

**Inspector** ([[Ctrl+I]]) shows what the current page is made of while it runs:

- **Objects**: every object with its state and error. Select one for its genie values,
  expressions, tags and associations. **Only with errors** filters.
- **Tags**: the page's tags with value, quality, source and the objects that use them.
- **Associations**: each window's associations, and `?names?` the window lacks.
- **Variables**: page variables and parameters.
- **Windows**: the open windows and what each shows.
- **Performance**: **Start** times scans, expressions, functions and tasks. Views:
  **Functions** (calls, total and own time, callers), **Call tree**, **Flame graph**,
  **Expressions** and **Scans and tasks**. Double-click a function to focus on it.

**Evaluate Parts** runs each call in an object's expressions and shows the results. Right-click
a tag for **Show Users on Page** or **Watch**. [[Ctrl+F]] searches, **Copy** copies rows.

## Debugging page code with VS Code

The Cicode debugger in VS Code attaches to the simulation ctgedit runs. Unlike the runtime, the
simulator also stops page code at breakpoints: the whole simulation waits and each tab says
where it's paused. Turn this off with **Stop at breakpoints in page code**.

Hardware errors in page code are reported in VS Code's Debug Console but don't stop it, unless
**Also stop page code on its hardware errors** is on.

## Copying a picture of the page

**Copy Image** ([[Ctrl+Shift+C]]) copies the whole page at its own size, with its pinned windows
and popups, regardless of zoom. The Simulate window's own marks are left out.

Cicode's `WinFile`, `WinPrint` and `WinCopy` take the same picture of the window they run in, at
the scale they ask for. `WinFile` writes it as a .bmp file in the simulator's sandbox, `WinPrint`
puts it in the sandbox's Printers folder, and `WinCopy` puts it on the clipboard.

## How the states are drawn

- Hidden objects aren't drawn. Disabled objects are embossed or greyed by their disable style.
- A group's state applies to everything in it.
- Fill levels fill from the side their direction says.
- Values and errors such as #COM replace the text of a text object.
- Objects sized to 0 aren't drawn.
- Flashing pictures and colours alternate with the page's flash.
- Alarm indicators show only while their equipment has an active alarm, with the priority's
  flag.
- Alarm lists draw their alarms (SA lists and AlarmDsp lists).
- Sized objects keep their line width, as in the runtime.
- Trends draw the samples the simulator sends. A long trend samples rarely, so turn on **Show
  trend history from the start** to see its whole span.
- Bad or uncertain quality is drawn as a black dot pattern, as the runtime does.
- ActiveX text boxes accept typing. A ciText combo box displays the items added by Cicode.
  Choose a row to update its ListIndex and Text, or type a value into the field.
- Composite genies are drawn from their own parts, as the runtime does.

## What the page's Cicode draws

Cicode's display functions (DspText, DspStr, DspSym, DspSymAtSize, DspSymAnm, DspButton and so
on) draw on the page, and the Simulate window shows what each object last drew: text in its font
and colours, symbols and genies from the project's libraries, and buttons, which take clicks. A
bar drawn by Cicode shows as a plain level box. Trends, charts and web content set by Cicode
aren't shown yet.

> **Coming from the Graphics Builder:** the Graphics Builder has no simulator. You only see a
> page run in the Plant SCADA runtime, after a compile.
