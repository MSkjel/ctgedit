# Trends, Cicode objects, web content and ActiveX

These are placed with the tools under **More** in the ribbon's Create group, or Edit > Insert.
Their properties open once placed.

## Trends

Edit > Insert > Trend, then drag a box (or click for the default size). On Appearance > General:

- **Cluster name**: blank if the client connects to one cluster.
- **Pens**: up to 8, each a trend tag (Insert Trend Tag lists them; for SPC use `tag.XUCL`,
  `tag.X` …) and a **Pen colour**.
- **Samples**: **Number of samples** and **Pixels per sample**, which set the width.
- **Command**: a Cicode command stored with the trend (up to 64 characters).
- **Display all trend types as periodic**.

The editor draws sample curves in the pen colours. Trends can have keyboard commands but not
touch commands. See [Add a Trend](aveva:1172142).

## Cicode objects

Edit > Insert > Cicode Object, then click. It's a point, drawn as an `f(x)` icon.

- **Command**: runs continually while the page is shown (up to 254 characters). Empty, the object
  is just an animation point.
- **Display as**: **Browse…** shows a library bitmap symbol; **Use Default** goes back to the
  icon.

See [Add a Cicode Object](aveva:1172144).

## Web content

Edit > Insert > Web Content, then drag a box.

- **URL**: the full address, protocol included (up to 2048 characters). Doubtful addresses are
  flagged but kept.
- **Enable 'Print' in context menu**.

At runtime it's drawn over everything else. Name it (Access > General) to change its URL with
DspWebContentSetURL.

> **Warning:** a web page in Plant SCADA can take the operator away from the plant controls. Read
> AVEVA's loss of control warning on the object's page before using it on an operator page.

See [Add a Web Content Object](aveva:1172149) and [Web Content](aveva:1172242).

## ActiveX controls

### Insert an ActiveX control

Edit > Insert > ActiveX Control… lists the controls ctgedit carries and the ones the project
already uses. Pick one, click **Insert**, then drag its box. Controls not registered on this
computer are marked. Edit > Insert > Process Analyst and Scheduler place those two directly.

The control's first page shows its name, ProgID, class ID and location. **Control Properties…**
opens the control in a window of its own, with its properties and its own property pages. OK
there stores the control's new settings in the page, as one undo step.

On the page, a control that is registered on this computer draws itself. ctgedit runs it once,
in a separate program, and keeps the picture until the control's settings or size change.
Until then, and for a control that isn't installed here, it shows as a box. To show every
control as a box, turn off Settings > Canvas > "Draw embedded controls as they look".

### Tag association

Appearance > **Tag Association** links control properties to variable tags:

1. Select a **Property** (from the control's type library, or type one if it isn't installed).
2. Type the **Tag**. **Bindable** shows if the control reports changes.
3. Pick the **Event** that makes Plant SCADA read the property.

**List Property Types** shows which tag types fit. **Clear Property** removes all associations.
Access > Identification holds the object name and event class. See
[Tag Association](aveva:1172151).

<!-- limit:WP26 -->
> **Coming from the Graphics Builder:** the page shows a picture of each control, so a control
> doesn't move or change while you edit. A control that paints nothing outside the Graphics
> Builder, or isn't registered on this computer, shows as a box.

<!-- limit:WP26 -->
> A new Plant SCADA Meter, Process Analyst or Scheduler is copied from the page of Plant
> SCADA's Example project that holds one, so those need the Example project installed.
> Edit > Insert > ActiveX Control… also lists every control the project's pages already use,
> such as a Database Exchange control or a Pelco viewer.

See [Add an ActiveX Object](aveva:1172150) and [Add a Database Exchange Control](aveva:1172154).
