# Object properties and animation

Two places change an object:

- the **Properties panel** shows the most used settings of the selection and applies changes at
  once;
- **Object Properties** (double-click, Edit > Object Properties…, or the selection bar) has every
  setting, on the Graphics Builder's tabs.

## The Properties panel

With one object selected the panel shows:

- **Name**, **AN** and **Tool tip**. An invalid or clashing name is flagged as you type. The AN
  is only editable with Settings > Editing > Allow changing an object's AN.
- **Geometry**: position and size (see below).
- **Appearance**: style tiles (see [Named styles](colours.md#named-styles)), colours, fill, line
  width and style, corner radius, and 3D effect with depth.
- **Text**: caption, font, style, alignment and effect. Type a font name and press [[Enter]] to
  use the first installed font that starts with it.
- The object's animations, grouped by kind, each editable.
- **Add animation…**: visibility, movement, rotation, scaling, fill colour, fill level, touch or
  keyboard commands, disable, or a display value. New animations get the Graphics Builder's
  defaults.
- **Input**: touch and keyboard commands.
- For a genie, **Genie Parameters…** and **Edit Definition**.
- For a genie, **Inputs**: each value the genie is given, and under it every field of its
  objects that value goes into, through the genies nested in it, with the value the field ends
  up with. Copies of one object share a row. A value nothing uses says so. Double-click a field
  to select its objects. A nested genie lists its own inputs, worked out from the genie above.
- For an object in a genie, **Set by the genie**: each field the genie's inputs set, with the
  value it holds, the field as the genie's definition writes it (**From** `%Tag%.Alarm`) and
  each input in it with the input's value. Double-click an input to select the genie.
- **All properties**: every stored setting, named as in Object Properties.

With several objects selected:

- an **Arrange** row with the align, distribute and size commands;
- **Selection**: X, Y, W and H, either for the **Whole selection** or for **Each object**;
- shared colours, line, font and animation fields.

A value the objects don't share shows **Mixed**. Typing a value sets it on all of them.

With a drawing tool armed and nothing selected, the panel shows the look of the next object (see
[The look of new objects](drawing.md#the-look-of-new-objects)). With nothing selected it shows
the page: size, colour, parent page and content type.

Every change is one undo step ([[Ctrl+Z]]).

### Search properties

**Search properties** filters the panel by setting name, stored name or value. **In Object
Properties** below lists the sheet pages that have a match; click one to open it there.
[[Ctrl+F]] in the panel focuses the search, [[Esc]] clears it.

### Sections

Click a section's title to fold it. The **⋯** button has **Open in Object Properties**,
**Remove** (for animations), **Fold All Others** and the label layout.

### Keys in the panel

- [[Enter]] applies; [[Tab]] applies and moves on.
- [[Esc]] restores the field. A second [[Esc]] returns to the page.
- [[Ctrl+Z]] undoes typing in a field, or the last change if you haven't typed.
- In number fields, [[Up]] and [[Down]] step by 1, [[Shift]] by 10, [[Ctrl]] by the grid. Steps
  apply after a short pause as one undo step.

### Typing sums

Number fields take sums: 344+10, 1366/2, (100+20)*3. Starting with an operator changes the
current value: +20, *2, /2. Results are rounded and kept in range.

### Dragging labels

Drag a number field's label sideways to change it: 1 per 2 pixels, 10 with [[Shift]], finer with
[[Alt]]. [[Esc]] while dragging cancels. Settings > Editing turns this off or changes the speed.

### Geometry

- The 3 x 3 squares pick which point X and Y refer to. That point stays put when you change W or
  H.
- The chain between W and H keeps proportions.
- Lines also show **Length** and **Angle**.

## The property sheet

Object Properties has the tabs the Graphics Builder shows for that kind of object. It opens on
the first tab, or the last one used (Settings > Editing).

- **OK** applies and closes, **Apply** applies and stays, **Cancel** closes. Each Apply is one
  undo step.
- The sheet stays open while you work, follows its object, and closes when the object is
  deleted. It survives genie updates and keeps what you typed.
- **Clear Property** removes a page's settings.
- Values that can't be stored block Apply. Other advice (unknown tags and so on) is shown but
  doesn't block.

### Check marks and greyed pages

A tab with settings has a check mark. Some pages exclude each other, such as a horizontal slider
and horizontal movement; the other is greyed as soon as you type into one.

### Genie members and template objects

Objects inside a placed genie come from the genie's library element, so their sheet is
read-only, with **Edit Definition** to open the genie. Access > General stays editable per copy.

Objects from the page template are read-only while templates are locked (see
[Page templates](templates.md)).

A placed genie's own settings are just its name and alarm indicator.

## Expressions

Expression fields take a tag, an equipment item or any Cicode expression. Matching tags and
Cicode functions are suggested as you type; picking a function adds its opening bracket.

### Colours, underlines and hints

Expression and command fields colour their Cicode: keywords, strings, numbers, functions, tags,
display formats, comments and genie parameters (`%Tag%`) each have their own colour.

- A **red wavy underline** marks what the compiler refuses, such as `==`, a string that is not
  closed or a number that is too large. After a Check, the compiler's own errors are underlined
  at their place.
- An **amber wavy underline** marks a tag or function the project doesn't have. A name the genie
  builds from a parameter (`%Tag%_PV`) is not looked up. Hold the pointer over it to see the
  names the project has that are most like it. Right-click it and choose **Replace with** one
  of them.
- A genie parameter may give its default after `=`, quotes and all: `%WebIP="127.0.0.1"%`.
  Hold the pointer over it to see the default.
- Inside a function call, the function's arguments show above the field, with the one you are
  typing in bold. [[Esc]] hides them until you start another call.
- After the `:` of a display format (`Level:###.# EU`), the hint explains the format characters
  and shows how the tag's value looks with that format.
- Hold the pointer over a name to see what it is: a tag's type, range and comment, or a
  function's arguments and description. While the Simulate window runs a page of the same
  project, a tag also shows its value in the simulation.
- [[Ctrl]]+click a function of the project, or put the cursor on it and press [[F12]], to open its
  `.ci` file at the function. It opens in VS Code when VS Code is installed, else in the program
  Windows opens `.ci` files with.
- Type an equipment name and a dot (`Plant.Pump1.`) to choose from the level below it: its
  equipment and its items.

The page checks and the advice under a field find tags and functions the same way as the
underlines.

Settings > Editing > Expressions turns the colours and underlines, and the argument hints, on
and off.

### Edit in a larger box

Right-click an expression or command field and choose **Edit in a Larger Box…**, or press
[[Shift+F2]] in it, to edit its text in a large box with the same colours, hints and
suggestions. Line breaks become spaces when you press **OK**. A comment that starts with `!` or
`//` runs to the end of its line, so use `/* */` for a comment with more text after it.

### Check

An expression or command field compiles what you type with the project's Cicode when you
pause. The mark at the start of an expression field shows the result: a green tick when it
compiles, a warning sign for warnings, a red cross for errors. Hold the pointer over the mark
to read them. Errors are also underlined at their column. A command box shows the result
under it. Undefined tags are warnings. The simulator does the compiling, so the first check
can take a few seconds. Settings > Editing > Expressions sets how long the pause is.

In a genie or super genie, each parameter (`%Tag%`) and association (`?INT 2?`) is compiled as a
name. The check leaves out what the compiler says about those names and about types, which are
known when the genie is placed.

### The Wizard

The button at the end of an expression field (or **Wizard** next to a command field) opens a menu
that inserts at the cursor:

### Insert a tag

**Insert Tag…** lists the tags, local variables and equipment items the page can use, with their
type, project and comment. See [The Tags panel](tags-panel.md#which-tags-are-listed).

### Insert a function

**Insert Function…** lists built-in Cicode functions and the project's own, with their syntax.
**Insert arguments** inserts the argument names too. Functions you write appear as soon as the
file is saved.

### Insert a trend tag

**Insert Trend Tag…** lists the trend tags the page can use.

### Specify range

Movement, scaling, rotation, fill level and some fill colour fields have **Specify range** to set
the expression's minimum and maximum. Otherwise it's the tag's engineering range, 0 to 32000, or
0 to 100.

## Appearance

### Appearance > General

Depends on the object:

- **Lines**: width, style and colour.
- **Rectangles, ellipses and polygons**: line, fill, **Filled** and **Gradient**. Rectangles have
  **Border**, **Extra line** and **Corner radius**; ellipses a **Type** with start and end angles;
  polygons **Open (polyline)**.
- **Pipes**: width, **Highlight colour** and **Lowlight colour**.
- **Text**: see [Text, numbers and fonts](text-and-numbers.md).
- **Buttons**: **Text**, **Symbol** or **Target** type, custom fill colours, and the caption font.
- **Symbol sets**: see [Symbol sets](symbol-sets.md).
- **Placed symbols and templates**: see [Symbol linkage](library-elements.md#symbol-linkage).

Colour fields open the colour picker (see [Colours](colours.md)).

### 3D Effects

**None**, **Shadowed**, **Raised**, **Lowered** or **Embossed**, with **Depth** and its colours.
The effect grows the object's outline by its depth.

### Display value

See [Display values](text-and-numbers.md#display-values).

### Visibility

**Hidden when**: a condition that hides the object at runtime. The editor always draws it.

### Vertices

A polygon's points. Give a point an **Expression** with **From** and **To** offsets to animate
it. The Graphics Builder reads this from the project database, so save the page before opening it
there.

### Trend

See [Trends](special-objects.md#trends).

### Web content

See [Web Content](special-objects.md#web-content).

### ActiveX control

See [ActiveX controls](special-objects.md#activex-controls).

### Tag association

See [Tag association](special-objects.md#tag-association).

## Movement

**Horizontal** and **Vertical** move the object between two offsets as an expression goes from
its minimum to maximum. **Rotational** turns it between two angles about its **Centre axis
offset**.

### Centre axis offset

The point the object turns about or grows from: **Express** picks a point on its outline,
**Custom** takes an offset from its centre.

New movements default to offsets 0 and 500, angles 0 and 360, sizes 0 % and 100 %.

## Scaling

**Horizontal** and **Vertical** scale the object between two percentages, from its Centre axis
offset.

## Fill

### Fill colour

Fill > **Colour** changes the fill at runtime:

| Type | The fill is |
|---|---|
| On / Off | the ON colour while the condition is TRUE, OFF otherwise |
| Multi-state | one colour per combination of up to 5 conditions (A to E) |
| Array | the colour at the expression's value (0 to 255) |
| Threshold | a colour per threshold |
| Gradient | a blend between the colours at minimum and maximum |

The OFF colour, state 0, array colour 0 and gradient minimum are all the object's own fill colour.

### Fill level

Fill > **Level** fills part of the object by an expression, between two percentages, in a
direction, with a colour for the unfilled part.

## Input

### Touch commands

Input > **Touch** runs Cicode on **Up**, **Down**, **Repeat** (at a rate) or **Double-click**,
each with an optional log message.

### Keyboard commands

Input > **Keyboard Commands** runs a command when the operator types a key sequence over the
object. **Add…** a sequence (key names, `#` for each digit of an argument), then its
**Command**, **Security** and **Log message**.

Commands are stored as child objects; the Objects panel shows them as badges.

## Slider

**Horizontal**, **Vertical** and **Rotational** sliders let the operator drag the object to set a
**Tag**, within the movement's offsets or angles. **Continuous update of tag** writes while
dragging.

## Access

Access > **General**:

- **Object AN** (editable only with the setting on; must be unique).
- **Name**: letters, digits and `_`, starting with a letter, at most 32 characters, unique among
  its siblings.
- **Description** (128 characters) and **Tool tip** (48 characters).
- **Object area**, **Privilege level** and **Log device**.

### Disable

**Disable when**, **Disable on insufficient area or privilege**, and the **Disable style**:
Embossed, Greyed, Hidden or Unchanged.

### Identification

ActiveX controls: **Object name**, **Event class** and **Persist ActiveX data between page
transitions**.

## Metadata

Name / value pairs Cicode reads at runtime (DspAnGetMetadata). Names use letters, digits and `_`
(a genie's `%parameter%` is allowed); values are up to 255 characters. Situational Awareness panes
keep their settings here.

## Alarm Indicator

Groups and genies can show a border and flag while their equipment has an active alarm.

- **Has Alarm Indicator** turns it on.
- **Highest priority alarm from**: the **Equipment**, whether to count children, and **Include
  equipment references in count**.
- **Border Appearance**: **Is Inside**, **Width**, **Padding** and **Frame colour**.
- **Flag Appearance**: **Show Flag** and its **Position**.

The group's outline grows to fit.

<!-- limit:WP18 -->
> **Coming from the Graphics Builder:** it greys this tab on a placed genie. ctgedit can set it
> there, and the Graphics Builder keeps the setting.

## Cicode

### Cicode object

The **Command** runs continually while the page is shown. **Display as** picks a library symbol
instead of the `f(x)` icon. See [Cicode objects](special-objects.md#cicode-objects).

## Where the settings are stored

The property names the compiler and runtime use, as you'll see them in Find and Replace and
compile messages:

| Setting | Names |
|---|---|
| Hidden when | `VISIBLE` |
| Movement, rotation | `MVX`, `MVY`, `ROT` + `…MINPT` / `…MAXPT`, `ROTMINPC` / `ROTMAXPC`, `ROTMODE` |
| Scaling | `SZX`, `SZY` + `…MINPC` / `…MAXPC`, `…MODE` / `…ORG` |
| Fill colour | `COLMODE`, `COL_A` … `COL_E`, `COL_MAXON`, `COL_STA_n`, `COL_ARR`, `COL_EXP`, `COL_THR_n` |
| Fill level | `FIL`, `FILMINPC`, `FILMAXPC`, `FILMODE`, `FILCOL` |
| Display value | `TXTMODE`, `TXT_OFF`, `TXT_ON`, `TXT_A` … `TXT_E`, `STATE_n`, `ARRAY_n`, `TXT_STR` |
| Sliders | `MVXSL`, `MVYSL`, `ROTSL` + `…SLMODE` |
| Access | `__AN_NAME__`, `DESC`, `COMMENT`, `AREA`, `PRIV`, `DEVICE` |
| Disable | `DISABLED`, `DISMODE`, `GREY_STYLE` |
| Metadata | `METADATA_n` |
| Alarm indicator | `AI_ON`, `AI_WIDTH`, `AI_PADDING`, `AI_INSIDE`, `AI_COLOUR`, `AI_FLAG_LOC` … |
| Vertices | `VERTEX_VERTEXPn`, `VERTEX_VERTFROMXn` … |

> **Coming from the Graphics Builder:** the tabs, fields and defaults are the same. The sheet
> doesn't block the editor, names complete as you type, and every Apply can be undone.

See the AVEVA help: [Edit Common Object Properties](aveva:1172156), [Movement](aveva:1172160),
[Fill](aveva:1172167), [Sliders](aveva:1172170), [Input](aveva:1172174), [Access](aveva:1172177),
[Metadata](aveva:1172180) and [Alarm Indicator](aveva:1172183).
