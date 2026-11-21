# Symbol sets

A symbol set shows one of several library symbols, chosen at runtime by an expression: a pump
that is green when it runs and grey when it stops, a valve in four positions.

## Place a symbol set

Choose Tools > Draw > Symbol Set (or Symbol Set in the Place group of the ribbon) and click the
page. The new set's properties open at once; its first symbol's top-left corner lands where you
clicked.

## Choose the type and the symbols

Double-click a symbol set to open its **Symbol Set Properties**. Choose the **Type**:

| Type | Shows |
|---|---|
| On / Off | the ON symbol while the expression (**ON symbol when**) is TRUE, the OFF symbol while it is FALSE |
| Multi-state | one symbol for each combination of up to 5 conditions (A is the lowest bit; a blank condition counts as FALSE; up to 32 combinations) |
| Array | symbol n for the value n of the **Array expression** (0 to 255; the runtime clamps other values and rounds real numbers) |
| Animated | the frames in sequence while **Animate when** is TRUE (at the rate of Plant SCADA's `[Page]AnmDelay`), the Off frame while it is FALSE |

For Multi-state, **Add**, **Edit** and **Remove** manage the conditions (one must remain).
Under **Symbols**, select a state and click **Edit…** to choose its library symbol (Select
Symbol shows thumbnails of the libraries the page can use), or **Clear** to show nothing in that
state. For Array and Animated, **Add…** adds a state at the end and **Remove** removes the
selected one (the later ones move up).

**Clear Property** blanks the expressions and clears every symbol. **Other Properties…** opens
the rest of the set's properties (movement, scaling, fill, input, access). OK and Apply write the
set as one undo step.

A state that shows a picture stored in the page itself (not a library symbol) says so in its
tool tip; Browse… replaces it with a library symbol. The symbols of a set inside a genie or a
symbol come from that genie's or symbol's library definition.

## At design time

The editor shows the set in its first state that has a symbol. The states can change the set's
size: each symbol is placed with its own origin on the set's hot spot.

## Keep it a symbol set

A symbol set is never ungrouped: Arrange > Ungroup Objects refuses it, because its states would
become loose symbols the runtime cannot switch. To change the symbols, use its properties.

> **Coming from the Graphics Builder:** this is the Graphics Builder's Symbol Set tool and its
> Appearance > General page. With Settings > Editing > "Show the old V3 and V4 tools",
> Tools > V3/V4 Objects > Animated Symbol places the old animated symbol, edited in the same
> dialog (its Mode: SOFT or ANM_ARRAY).

See [Add a Symbol Set](aveva:1172137) in the AVEVA help.
