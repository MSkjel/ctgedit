# Glossary

| Term | Meaning |
|---|---|
| Active install | The Plant SCADA install that Studio, the Graphics Builder and the runtime use. File > Open starts there; another install's pages are marked in amber. |
| AN (animation number) | The number Plant SCADA gives every object of a page. Cicode functions (DspGetAnFromName, DspAnGetMetadata …) and compile messages refer to objects by it. Shown in the status bar, the Objects panel, Goto Object and Access > General. |
| Animation point | An object with an AN. Keep a page under about 3000; the status bar counts them. |
| Association | A `?name?` placeholder in a super genie page's expressions, filled with a tag when the page is opened at runtime. Listed in Page Properties > Associations. |
| Command object | A touch or keyboard command of an object, stored as a small record that belongs to the object (the Objects panel shows them as `[touch, 2 keys]`). |
| Compile | Plant SCADA's compiler turns the project database into the files the runtime uses (the `.rdb` compiled pages among them). File > Compile Project, File > Run in Plant SCADA. |
| Compiled project | The project that is compiled for the runtime: the page's project, or the project that includes it. Its tags are listed for every page it compiles. |
| Composite genie | A genie built from an XML template and a set of options; stored in the project's composition cache (`_CompositionCaches`). |
| Content type | The kind of content a page holds (for Situational Awareness panes). Page Properties > General. |
| `.ctF` | A page's fast-display file: the drawing the runtime shows. Written by the save. |
| `.ctg` | A graphics page file: what ctgedit and the Graphics Builder open and save. |
| `.ctl`, `.ctm`, `.ctt` | Library files: symbols, genies and super genies, page templates. |
| Default form | The genie dialog built from the parameters a genie declares, when its library has no usable form. |
| Element | One symbol, genie, super genie or template inside a library file, named `library.element`. |
| Equipment, equipment item | A piece of plant defined in Plant SCADA's equipment editor (`equip.dbf`), and one of its items (`Pump01.Speed`), usable in expressions. |
| Flashing colour | A colour with two halves the runtime alternates between (a steady colour has two equal halves). |
| Form | A layout for a genie's parameter dialog, in the library's `.frm` file. |
| Genie | A group of objects in a library with parameters (`%name%`) filled in when it is placed. |
| Hot spot | The point of a library element that lands where you click when you place it. |
| Include chain, included project | The projects a project includes (Plant SCADA Studio's include list), and the ones they include, in search order. A page can use the libraries of all of them, and of Plant SCADA's Include and System projects. Its tags also come from the projects that include it. |
| Linked | A placed symbol or a page's template that follows later changes of its library element (Update Pages). An unlinked copy does not. |
| Metadata | Name / value pairs on an object that Cicode reads at runtime (the Metadata tab). |
| Palette | The project's colours (`citect.pal`): standard colours, Transparent and favourites. |
| Page browser | The dialog of File > Open, Save As and the pickers: every project of the install as a tree or a flat list, with the pages, templates, symbols and genies of each. |
| Page database | The project's `pages.DBF` and the `pg*.DBF` tables: a page's properties, animation and commands as the compiler reads them. Written by the save. |
| Placed genie | A copy of a genie on a page, with its own parameter values. Its objects come from the genie's definition and are read-only. |
| Super genie | A popup page kept as a genie (`name@SUPER`), shown at runtime with the tags passed to it. |
| System page (!) | A page whose name starts with `!`, for example a popup or a page made from a super genie. The page browser can hide or show only these. |
| Symbol | A drawing kept in a library (`.ctl`) for reuse. |
| Symbol set | An object that shows one of several symbols, chosen at runtime by an expression. |
| Tag | A variable tag (`variable.dbf`) or a local variable of the page's project, its included projects or the projects that include it. |
| Template | A library element (`.ctt`) that gives pages their frame, size and background. |
| Transparent | No colour: what is behind shows through. |
| Update Pages | Tools > Update Pages: brings pages up to date with the templates, genies and symbols that changed in their libraries. |
