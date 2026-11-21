# Text, numbers and fonts

## Add text

Choose the Text tool ([[T]]), click and type. [[Enter]] starts a new line; [[Ctrl+Enter]],
[[Tab]] or clicking elsewhere finishes; [[Esc]] cancels. You can also type first and click where
the text should go.

Settings > Editing can swap Enter and Ctrl+Enter for one-line labels.

## Editing text in place

Double-click a text or button, or select it and press [[F2]] (Text > Edit Text in Place), to edit
its caption on the page. [[F2]] again finishes. Settings > Editing can make double-click open the
properties instead, as in the Graphics Builder.

Numbers, value displays, genie texts and template texts can't be edited in place; ctgedit says
what to do instead.

## Fonts

- The Text menu has **Bold** ([[Ctrl+B]]), **Italic** ([[Ctrl+I]]), **Underline** ([[Ctrl+U]]),
  **Strikeout**, the three justifications and the 3D effects.
- Text > Font… sets **Font**, **Size**, **Style** and **Alignment** for every selected text,
  number and button, including those in selected groups (not genie texts). It stays open and
  follows the selection.
- The ribbon's Text tab has the same controls.
- Appearance > General also sets **Foreground** and **Script**. A warning shows when the font
  lacks that script.

A text's box is measured from its font, so it matches what the runtime draws. The alignment
decides which way it grows.

## Display values

Object Properties > Appearance > **Display Value** makes a text show a value. Choose the **Type**:

| Type | Shows |
|---|---|
| On / Off | the **ON text** while a condition is TRUE, the **OFF text** otherwise |
| Multi-state | one text per combination of up to 5 conditions (A to E) |
| Array | the text at the expression's value (0 to 255) |
| Numeric | an expression's value, with a **Format** |
| String | the text an expression returns |

The OFF text, state 0 and array row 0 are the object's own caption. **Format** uses `#` per digit
(`####.#`); `#!` adds engineering units; `<default>` uses the tag's own format.

At design time a number shows its format, a string display `="expression"`, and a state text its
first state.

## Numbers

The Number tool ([[N]]) asks for the **Expression** and an optional **Format**. Change them later
on its Display Value page.

## Labels in several languages

Captions written as `@(Label)` are translated at runtime from the project's language tables. View
> Labels sets how the editor shows them:

- **As Stored: @(text)** (default): `@(Full Milk)`.
- **Native Text**: `Full Milk`.
- a project language: the translation, such as `Vollmilch`.

This only changes the display; the page keeps `@(Full Milk)`.

## Characters a page can store

Pages store text in the install's Windows code page (Windows-1252 for Western European languages).
Characters it can't store are refused with a message rather than saved as `?`.

> **Coming from the Graphics Builder:** the Text menu, Font dialog, display value types and
> placeholders are the same. In-place editing ([[F2]]) and View > Labels are ctgedit's own.

See the AVEVA help: [Add Text](aveva:1172129) and [Display Value (Numeric)](aveva:1172133).
