# Compile and run

## What saving does and what compiling does

**Saving** writes the page, its fast-display file and its rows in the project database.
**Compiling** runs Plant SCADA's compiler, which reads the project database (not the page files)
and builds the runtime's files. Save, then compile, before expecting the runtime to show a
change. File > Run in Plant SCADA does both.

## Compile the project

File > Compile Project ([[Alt+F10]]) saves the current page if needed, then compiles the
project the way Studio does: the page's project, or the project that includes it. A progress
window shows the time so far, with Cancel.

With no page open, it compiles the project of the page you opened last.

The compiler finds projects by name in the active install. A page outside that install's
projects can't be compiled or run from here; the message says where to open it from instead.

## Compile results

The results window lists the error and warning count, the time taken, and one row per message
(**Code**, **Project**, **File**, **Line**, **Message**, **Context**). Errors come first; messages
about the current page are bold. **Search messages** filters them.

By default it opens only when the compile has errors or didn't finish. Settings > Pages and
libraries can show it for warnings too, or always. The status bar always shows the result, with
**Show Results**. See [Compile Messages](aveva:1096199) in the AVEVA help for the codes.

## Run

File > Run in Plant SCADA ([[F5]]) saves and compiles, then shows the updated page in a runtime
already running this project, without restarting it. If the runtime runs another project, ctgedit
asks before stopping it. With no runtime running, it starts one through the Runtime Manager.

With a library element shown, the command is **Open Runtime Manager**: an element can't run on its
own. **Simulate Page** ([[Ctrl+F5]]) is the other way to see a page run: it saves, compiles and
starts nothing.

Settings > Pages and libraries can make Run ask before compiling, offering **Run Without
Compiling**.

After every save, a running runtime showing the page is told to reload it.

## The fast-display file

The runtime draws a page from its `.ctF` when there is one, else from its `.ctg`. A save rebuilds
the `.ctF` when ctgedit can make it exactly, and otherwise removes it so the runtime never shows a
stale drawing. With Settings > Pages and libraries > Write fast display files off (Plant SCADA's
`[Animator] FastDisplay`), every save removes it.

> **Coming from the Graphics Builder:** Compile ([[Alt+F10]]) and Run ([[F5]]) are the same
> commands and keys. ctgedit runs the compiler itself, shows its own results window, and Run
> refreshes the page in a running runtime instead of restarting it.

See [Compile a Project](aveva:1096197), [Incremental Compilation](aveva:1096203) and
[Runtime Manager](aveva:1121971) in the AVEVA help.
