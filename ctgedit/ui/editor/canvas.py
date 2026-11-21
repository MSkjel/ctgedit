"""The canvas: zoom, pan, select, move, resize and the drawing tools."""

from __future__ import annotations

import math
import time

from PySide6 import QtCore, QtGui, QtWidgets

from ...format import geometry as _geom
from ...infra import caches as _caches
from ...render.scene import COORD_LIMIT, PAGE_INDEX_ROLE, clamp_rect, font_px
from . import canvas_hooks
from . import draw_styles as _draw_styles
from .inplace_text import InplaceText
from .viewnav import CanvasNavigation, from_touch_or_pen
from . import viewnav as _viewnav
from .canvas_look import (
    GRIP_GAP_PX,
    NODE_BOX_PX,
    ROTATE_ZONE_PX,
    drag_page_px,
    drag_start,
    guide_reach,
    handle_size,
    modifier_roles,
    note_ms,
    start_note,
    pointer_reach,
    signed,
    slow_mouse_factor,
)
from .canvas_guides import SmartGuides
from .canvas_paint import CanvasPainting
from .canvas_select import CanvasSelection


#: the canvas's own pointer shapes, made once (they hold no page or theme state)
_CURSORS: dict = {}
_caches.register("ui.canvas.cursors", _CURSORS, on=())


def _cursor_pixmap(paint) -> QtGui.QPixmap:
    """A 32 x 32 cursor drawn by ``paint(p)`` in black with a white outline (a cursor's
    colours: the same on every theme and page)."""
    pm = QtGui.QPixmap(32, 32)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    for colour, width in ((QtCore.Qt.white, 4.2), (QtCore.Qt.black, 1.8)):
        pen = QtGui.QPen(colour, width)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        paint(p, colour)
    p.end()
    return pm


def _rotate_cursor() -> QtGui.QCursor:
    """The curved arrow over the rotation grip and just outside a corner."""
    c = _CURSORS.get("rotate")
    if c is None:

        def paint(p, colour):
            # three quarters of a circle turning clockwise, a filled head at its end on
            # the arc's tangent (lower left, pointing up and left)
            box = QtCore.QRectF(8, 8, 16, 16)
            start, sweep = 135.0, -250.0
            path = QtGui.QPainterPath()
            path.arcMoveTo(box, start)
            path.arcTo(box, start, sweep)
            p.drawPath(path)
            a = math.radians(start + sweep)
            end = QtCore.QPointF(16 + 8 * math.cos(a), 16 - 8 * math.sin(a))
            d = QtCore.QPointF(math.sin(a), math.cos(a))  # (the way a clockwise turn goes)
            n = QtCore.QPointF(-d.y(), d.x())
            head = QtGui.QPolygonF([end + d * 5.0, end + n * 3.6 - d * 1.0, end - n * 3.6 - d * 1.0])
            p.setBrush(colour)
            p.drawPolygon(head)

        c = _CURSORS["rotate"] = QtGui.QCursor(_cursor_pixmap(paint), 16, 16)
    return c


def _locked_cursor() -> QtGui.QCursor:
    """A 'no entry' sign with a padlock: the object under the pointer is locked."""
    c = _CURSORS.get("locked")
    if c is None:

        def paint(p, colour):
            p.drawEllipse(QtCore.QRectF(3, 3, 15, 15))
            p.drawLine(QtCore.QPointF(5.5, 15.5), QtCore.QPointF(15.5, 5.5))
            p.drawRoundedRect(QtCore.QRectF(17, 20, 11, 9), 1.5, 1.5)
            path = QtGui.QPainterPath(QtCore.QPointF(19.5, 20))
            path.lineTo(19.5, 17.5)
            path.arcTo(QtCore.QRectF(19.5, 14, 6, 7), 180, -180)
            path.lineTo(25.5, 20)
            p.drawPath(path)

        c = _CURSORS["locked"] = QtGui.QCursor(_cursor_pixmap(paint), 10, 10)
    return c


def _clamp(v: float) -> float:
    """A page coordinate kept within the record range (+/- COORD_LIMIT)."""
    return max(-COORD_LIMIT, min(COORD_LIMIT, v))


# the special objects placed like a box: a trend / web content / ActiveX
# control is dragged out, a Cicode object clicked (its point) -- their sheet
# opens once placed (main window)
SPECIAL_TOOLS = frozenset(
    {
        "trend",
        "cicode_object",
        "web_content",
        "activex",
        "process_analyst",
        "scheduler",
        "db_exchange",
        "pelco",
    }
)
_BOX_TOOLS = (
    frozenset(
        {
            "rectangle",
            "rectangle_outline",
            "ellipse",
            "ellipse_outline",
            "text",
            "button",
            "number",
            "symbol",
            "symbol_set",
        }
    )
    | SPECIAL_TOOLS
)
_NODE_TOOLS = frozenset({"polygon", "polyline", "pipe"})
# a symbol set is placed by a click (its states have their library size); a
# click with a special-object tool places it at its default size (the main
# window picks it: the payload says 'click'). A click with the Text, Button or Number tool
# makes the size of the tool's style (ui/editor/draw_styles.click_size: the factory size here)
_CLICK_DEFAULT_SIZE = {"symbol": (64, 64), "symbol_set": (16, 16)}
_CLICK_DEFAULT_SIZE.update(_draw_styles.CLICK_SIZES)
_CLICK_DEFAULT_SIZE.update({t: (1, 1) for t in SPECIAL_TOOLS})

_HANDLES = {
    0: (0.0, 0.0),
    1: (0.5, 0.0),
    2: (1.0, 0.0),
    3: (1.0, 0.5),
    4: (1.0, 1.0),
    5: (0.5, 1.0),
    6: (0.0, 1.0),
    7: (0.0, 0.5),
}
_HANDLE_CURSORS = {
    0: QtCore.Qt.SizeFDiagCursor,
    4: QtCore.Qt.SizeFDiagCursor,
    2: QtCore.Qt.SizeBDiagCursor,
    6: QtCore.Qt.SizeBDiagCursor,
    1: QtCore.Qt.SizeVerCursor,
    5: QtCore.Qt.SizeVerCursor,
    3: QtCore.Qt.SizeHorCursor,
    7: QtCore.Qt.SizeHorCursor,
}


# the new-text font while it is typed when the Text tool's style sets none (the factory
# text a click gives: Arial 14 pt; CanvasView.new_text_font)
NEW_TEXT_FONT = ("Arial", 14)
# keys the in-place editor keeps from the canvas shortcuts (nudge / resize /
# delete / tool letters) even with Ctrl held
_EDIT_NAV_KEYS = frozenset(
    {
        QtCore.Qt.Key_Left,
        QtCore.Qt.Key_Right,
        QtCore.Qt.Key_Up,
        QtCore.Qt.Key_Down,
        QtCore.Qt.Key_Home,
        QtCore.Qt.Key_End,
        QtCore.Qt.Key_PageUp,
        QtCore.Qt.Key_PageDown,
        QtCore.Qt.Key_Return,
        QtCore.Qt.Key_Enter,
        QtCore.Qt.Key_Backspace,
        QtCore.Qt.Key_Delete,
        QtCore.Qt.Key_Insert,
        QtCore.Qt.Key_Escape,
    }
)
_CTRL = QtCore.Qt.ControlModifier
_SHIFT = QtCore.Qt.ShiftModifier
_ALT = QtCore.Qt.AltModifier | QtCore.Qt.MetaModifier
#: pressed or let go during a drag, the preview follows (modifier_roles)
_DRAW_MOD_KEYS = frozenset(
    {QtCore.Qt.Key_Control, QtCore.Qt.Key_Shift, QtCore.Qt.Key_Alt, QtCore.Qt.Key_Meta}
)
_MODIFIER_KEYS = frozenset(
    {QtCore.Qt.Key_Control, QtCore.Qt.Key_Shift, QtCore.Qt.Key_Alt, QtCore.Qt.Key_Meta, QtCore.Qt.Key_AltGr}
)


def _typing_key(e: QtGui.QKeyEvent) -> bool:
    """A key that types a character: printable text with no Ctrl / Alt --
    or with BOTH, which is how Windows reports AltGr ('@', '{', '[', '$',
    '€' on Nordic / German layouts: the '@(Label)' captions start with one).
    A real Ctrl+Alt shortcut carries no printable text."""
    t = e.text()
    if not t or not t.isprintable():
        return False
    mods = e.modifiers()
    if (mods & _CTRL) and (mods & QtCore.Qt.AltModifier):
        return True  # AltGr
    return not (mods & (_CTRL | _ALT))


def _live_handles(r: QtCore.QRectF):
    """(id, (fx, fy)) of the handles a box offers: a flat Extent (a
    horizontal / vertical line) stretches along its length only -- its other
    handles would sit on the end handles."""
    for hid, (fx, fy) in _HANDLES.items():
        if (r.height() < 1 and fy != 0.5) or (r.width() < 1 and fx != 0.5):
            continue
        yield hid, (fx, fy)


#: a cache miss (distinct from a cached None)
_MISS = object()


class _OverlayWatch(QtCore.QObject):
    """Watches the canvas viewport's own child widgets (the navigation bar, the navigator,
    the selection bar ...): when one shows or hides, the canvas is repainted, so the 'Page'
    chip is drawn (and clicked) where it is clear of them."""

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        for ch in view.viewport().findChildren(QtWidgets.QWidget, options=QtCore.Qt.FindDirectChildrenOnly):
            self.watch(ch)

    def watch(self, child) -> None:
        from .overlay_scrollbars import OverlayBar

        if isinstance(child, QtWidgets.QWidget) and not isinstance(child, OverlayBar):
            child.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() in (QtCore.QEvent.Show, QtCore.QEvent.Hide, QtCore.QEvent.Move, QtCore.QEvent.Resize):
            v = self._view
            try:
                if v._page_chip is not None or ev.type() in (QtCore.QEvent.Show, QtCore.QEvent.Hide):
                    v.viewport().update()
            except RuntimeError:  # swallow-ok: the canvas is being destroyed
                pass
        return False


class CanvasView(CanvasSelection, CanvasPainting, SmartGuides, CanvasNavigation, QtWidgets.QGraphicsView):
    selectionChanged = QtCore.Signal(list)  # representative root indices
    objectsMoved = QtCore.Signal(list, int, int)  # all moved indices, dx, dy
    objectResized = QtCore.Signal(int, tuple)  # index, (l, t, r, b)
    objectActivated = QtCore.Signal(int)  # double-clicked unit root
    #: Ctrl+double-click: the EXACT object under the cursor (a member of a
    #: group / symbol / genie / the template -- CtDraw opens its own sheet)
    memberActivated = QtCore.Signal(int)
    drawCommitted = QtCore.Signal(str, dict)  # tool kind, geometry payload
    toolResetRequested = QtCore.Signal()  # Esc ladder: back to Select
    tagDropped = QtCore.Signal(int, str)  # leaf index, tag name
    #: a Library-panel card dropped on the page: library, element, ext, scene pos
    libraryElementDropped = QtCore.Signal(str, str, str, QtCore.QPointF)
    #: picture files dragged from Explorer onto the page: paths, scene pos
    filesDropped = QtCore.Signal(list, QtCore.QPointF)
    hoverPos = QtCore.Signal(int, int)
    zoomChanged = QtCore.Signal(float)
    contextRequested = QtCore.Signal(QtCore.QPoint, list)  # global pos, roots
    #: an in-place text edit was committed: (target dict, typed text). The
    #: target is what begin_text_edit was given, or {"kind": "place", "pos":
    #: (x, y)} for a text the Text tool places
    textEditFinished = QtCore.Signal(dict, str)
    #: the selected nodes of object ``index`` were dragged: (index, node
    #: numbers, dx, dy)
    nodesMoved = QtCore.Signal(int, list, int, int)
    #: Insert / '+' on the outline of the reshaped object: (index, the new
    #: node's number, x, y)
    nodeInsertRequested = QtCore.Signal(int, int, int, int)
    #: a node key that could not apply, with the reason (the window shows it)
    reshapeRefused = QtCore.Signal(str)
    #: a short note for the status bar (a drawing cancelled with Esc ...)
    statusMessage = QtCore.Signal(str)
    #: a guideline was moved, added or deleted on the canvas: what happened,
    #: in words for the status bar (the window stores the positions)
    guidelinesChanged = QtCore.Signal(str)
    #: a guideline was double-clicked: open Guidelines Setup (CtDraw's
    #: canvas WM_LBUTTONDBLCLK on a guideline opens its 'GuideLines' dialog)
    guidelinesDialogRequested = QtCore.Signal()
    #: the element window's HOT SPOT marker was dragged to (x, y) (CtDraw: a
    #: press within 2 px of it enters canvas state 0x100, the release moves
    #: the hot spot there) / double-clicked (Hot Spot Location)
    hotspotMoved = QtCore.Signal(int, int)
    hotspotDialogRequested = QtCore.Signal()
    #: Previous View / Next View changed (the window enables its commands)
    viewHistoryChanged = QtCore.Signal()
    #: the canvas asks for a tool (holding Z arms the zoom box and gives the tool back)
    toolRequested = QtCore.Signal(str)
    #: the canvas arms a tool for a moment (Z held): what the tool before had waiting (a
    #: symbol to place) stays for when it comes back
    toolArmRequested = QtCore.Signal(str)
    #: another scene (page) shows in the canvas (a tab switch)
    sceneSwapped = QtCore.Signal()
    #: the box of a multi-selection was scaled with a handle: the selection's new top-left
    #: (x, y) and its scale (sx, sy) about that point (Adjust Position and Size's terms)
    selectionScaled = QtCore.Signal(int, int, float, float)
    #: a move drag released with the copy key held: copy these indices, then move the copy
    #: by (dx, dy) (the original stays)
    objectsCopied = QtCore.Signal(list, int, int)
    #: the rotation grip was let go a number of quarter turns clockwise (1, 2 or 3)
    rotateRequested = QtCore.Signal(int)
    #: the unit under the pointer changed (its indices, or None): the Objects panel follows
    hoverChanged = QtCore.Signal(object)
    #: the marker is caught within this many SCREEN px of its centre
    HOTSPOT_GRAB_PX = 4
    #: the mouse's back / forward button with Settings' 'The previous and next tab': -1 / +1
    pageStepRequested = QtCore.Signal(int)
    #: a double-click where no object or guideline is, with Settings' 'Opens Page Properties'
    pageDoubleClicked = QtCore.Signal()

    # ---- selecting and transforming (Settings; MainWindow.apply_select_options) ----
    #: View > a box with handles around a multi-selection (view/selection_box)
    SELECTION_BOX = True
    #: the selection box outlines what it will take while it is dragged (canvas/box_preview)
    BOX_PREVIEW = True
    #: a plain drag on an unselected object covering more than a quarter of the view starts a
    #: selection box (canvas/box_from_background)
    BOX_FROM_BACKGROUND = False
    #: a click without a drag on one of several selected units selects only it
    #: (canvas/click_selected_isolates)
    CLICK_SELECTED_ISOLATES = False
    #: Ctrl+Shift + drag with the Select tool: 'zoom' (a zoom box) or 'touch' (a Touch
    #: selection box from anywhere) (canvas/ctrl_shift_drag)
    CTRL_SHIFT_DRAG = "zoom"
    #: Tab / Shift+Tab select the next / previous object (opt/tab_selects_objects) in
    #: 'reading' or 'drawing' order (sel/tab_order)
    TAB_SELECTS = True
    TAB_ORDER = "reading"
    #: the Text tool stays after a text is typed on the page (opt/keep_tool,
    #: MainWindow.apply_editing_options; Esc still goes back to Select)
    KEEP_TOOL = True
    #: a box preview waits this long, a key pill stays this long (ms; the notes that stay
    #: are times Settings > Appearance > Messages stay: note_ms)
    BOX_PREVIEW_MS = 30
    KEY_PILL_MS = 800
    #: an Alt+click's pill and the outline of the unit it reached
    CYCLE_PILL_MS = 1500
    CYCLE_FLASH_MS = 600
    #: the alignment lines stay this long after an arrow key
    SG_LINGER_MS = 900
    #: a keyboard selection or a reveal keeps this margin to the canvas edge (screen px)
    REVEAL_MARGIN = 40

    # smart-guide snap radius in SCREEN px (the /zoom keeps it zoom-independent)
    SMART_GUIDE_TOL = 12.0
    #: Settings > Canvas: the wheel scrolls the page and Ctrl + the wheel zooms (False: the
    #: wheel zooms, as in the Graphics Builder)
    WHEEL_SCROLLS = False
    # outline the objects a smart guide lines up with while dragging (Settings)
    SMART_GUIDE_OUTLINE = True
    # at most this many outlined at once (a line shared by many aligned objects)
    SMART_GUIDE_OUTLINE_MAX = 24
    # CtDraw's guideline catch distance: 8 page px; a
    # constant 8 screen px once zoomed in beyond 100 %
    GUIDE_SNAP_PX = 8.0
    # dropped this close to the page edge, a dragged guideline is deleted
    # (position < 9 or >= page size - 8)
    GUIDE_DELETE_EDGE = 8

    # set per canvas by ui/editor/canvas_options.apply_canvas_settings
    #: Settings > Canvas > Grid and guidelines > Snap to a guideline within (view/guide_snap)
    GUIDE_SNAPS = {"near": 4.0, "normal": GUIDE_SNAP_PX, "far": 16.0}
    #: Settings > Canvas > Selecting > Outline the object under the pointer
    #: (view/hover_outline; the Objects panel's highlight is drawn either way)
    HOVER_OUTLINE = True
    #: Settings > Canvas > Zoom and scroll > Zoom to an object zooms in at most
    #: (view/frame_zoom_max): frame_rect's cap when its caller names none
    FRAME_ZOOM_MAX = 4.0
    #: Settings > Canvas > Mouse > Mouse back and forward buttons (opt/mouse_back_forward):
    #: 'view' Previous / Next View, 'tabs' the tab before / after (pageStepRequested),
    #: 'nothing'
    SIDE_BUTTONS = "view"
    #: Settings > Canvas > Mouse > Double-click on the empty page (opt/page_double_click):
    #: 'nothing' or 'page_props' (pageDoubleClicked)
    EMPTY_DOUBLE_CLICK = "nothing"
    #: Settings > Editing > New objects > Esc while drawing point by point
    #: (opt/esc_while_drawing): False throws the shape away, True keeps it as Enter does
    ESC_FINISHES = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(
            QtGui.QPainter.Antialiasing
            | QtGui.QPainter.TextAntialiasing
            | QtGui.QPainter.SmoothPixmapTransform
        )
        # Leave the VIEW background unset (NoBrush). QGraphicsView delegates to the
        # scene's drawBackground (page + margin + grid) ONLY when the view has no
        # background brush; a view brush short-circuits it to one flat colour.
        self.setBackgroundBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        self.setMouseTracking(True)
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        # drawForeground overlays (selection box, marquee, draw preview) need a full
        # repaint each frame, else minimal-update leaves smears/black trails behind a
        # drag.
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
        # the background (surround, page, grid) is drawn once and blitted. paintEvent
        # re-draws it whenever the scene's background signature or the zoom changes
        self.setCacheMode(QtWidgets.QGraphicsView.CacheBackground)
        self._bg_sig = None
        # a zoom re-draws the background: drop the cache before its repaint
        self.zoomChanged.connect(self._sync_background_cache)
        self._nav_busy = False  # inside a zoom / fit / scene-rect sync
        # the selected unit's bounds, per scene build (a drag shifts them)
        self._urect_cache: dict = {}
        # the selection's bounds (selection_box): (key, box ahead of no drag offset)
        self._sel_box_memo = None
        self._units_gen = 0  # counts the selections set
        self._sel_set: set = set()
        self.setAcceptDrops(True)  # tag drag-to-bind

        self._tool = "select"
        self._units: list[list[int]] = []  # each = one selection unit's indices
        self._selection: list[int] = []  # flat union of all units (for move)
        self._resolve_unit = lambda i: [i]  # canvas click -> top-level unit
        #: what the units a click can select depend on outside the scene (the window's
        #: locks, Break Lock Mode, the derive): set_units_key; None = never cached
        self._units_key = lambda: None
        self._selectable_memo = None
        self._resolve_subtree = lambda i: [i]  # exact node -> its own subtree
        self._member_ok = lambda i: True  # Ctrl+double-click may reach it
        self._snap = True
        self._grid = 8  # horizontal pitch (px)
        self._grid_y: int | None = None  # vertical pitch; None = the horizontal one
        self._grid_origin = (0, 0)  # the grid's base point (Grid Setup)
        # AUTO-FIT: keep the page fitted to the viewport across show/resize until the
        # user manually zooms. A startup fit runs before the viewport has its final size
        # (unrealised, then docks and maximise settle), so re-fit on every resize until
        # the first manual zoom.
        self._auto_fit = False

        self._panning = False
        self._pan_start = QtCore.QPoint()

        self._moving = False
        self._move_start = QtCore.QPointF()
        self._move_last = QtCore.QPointF()
        self._maybe_drag = False

        self._resizing: int | None = None
        self._resize_index: int | None = None
        self._resize_origin = QtCore.QRectF()
        self._resize_rect = QtCore.QRectF()

        self._marquee: QtCore.QRectF | None = None
        self._marquee_origin = QtCore.QPointF()

        self._draw_start: QtCore.QPointF | None = None
        self._draw_cur: QtCore.QPointF | None = None
        self._draw_press = QtCore.QPointF()  # unsnapped press (slow-mouse base)
        self._draw_mods = QtCore.Qt.NoModifier  # Ctrl / Shift while drawing
        self._nodes: list[QtCore.QPointF] = []
        self._node_press: QtCore.QPoint | None = None  # press of the FIRST vertex
        self._freehand = False
        self._resize_press = QtCore.QPointF()

        # in-place text editor (F2 / Text tool) + Mouse Slow Speed
        self._inplace: InplaceText | None = None
        self._inplace_target: dict = {}
        self._inplace_follow = False  # typed first: rides the cursor
        self._hover_scene: QtCore.QPointF | None = None
        self._slow_mouse = False

        self._vguides: list[int] = []
        self._hguides: list[int] = []
        self._guides_visible = True
        self._snap_guides = True
        # a guideline pressed / being dragged: {"axis": "v"|"h", "orig": its
        # position, "pos": where it is now, "copy": Ctrl held (a new one),
        # "moved": past the drag threshold, "press": the viewport press point,
        # "mods": the press modifiers}
        self._guide_drag: dict | None = None
        # a hot spot drag in an element window: the marker's point at the press
        self._hotspot_drag: tuple | None = None
        # a MOVE drag: the unit's model box (Extent) + the point that snaps
        self._mv_box: QtCore.QRectF | None = None
        # View > Cross Hair Cursor: lines through the pointer's scene point
        self._crosshair = False
        self._cross_pos: QtCore.QPointF | None = None

        # selection modes
        self._kind_filter = None  # fn(index)->bool or None
        self._marquee_mode = "auto"  # auto | enclose | touch
        self._marquee_touch = False  # resolved for THIS drag

        # smart guides + drag ergonomics
        self._smart_guides = True
        self._sg_bbox = QtCore.QRectF()
        self._sg_targets_x: list[float] = []
        self._sg_targets_y: list[float] = []
        # the extra targets of canvas_hooks.register_snap_targets (the underlay's objects):
        # also snapped to with smart guides off
        self._sg_extra_x: list[float] = []
        self._sg_extra_y: list[float] = []
        self._sg_active: list[tuple] = []
        # alignment lines linger briefly after an arrow-key nudge, then fade
        self._sg_flash = QtCore.QTimer(self)
        self._sg_flash.setSingleShot(True)
        self._sg_flash.setInterval(self.SG_LINGER_MS)
        self._sg_flash.timeout.connect(self._clear_sg_flash)
        self._move_applied = QtCore.QPointF()
        # the items a move drag moves (taken once when it starts)
        self._drag_items: list = []
        # an arrow-key burst the window has not committed yet (nudge_preview): its
        # scene, items, indices and displacement
        self._nudge: dict | None = None
        self._pill: str | None = None
        self._last_vp = QtCore.QPoint()
        self._hover_unit: list[int] = []
        self._highlight: list[int] = []
        self._pending: list[int] = []
        self._changed: list[int] = []  # View > Mark Changes Since Save
        self._space_pan = False

        # node editing (reshape): the object whose nodes are selected, the
        # selected node numbers, an armed / running node drag, and the
        # window's gate (locked objects, genie members, template records)
        self._node_owner: int | None = None
        self._node_sel: set[int] = set()
        self._node_drag: dict | None = None
        self._node_editable = lambda i: True

        # navigation (ui/editor/viewnav.py): the glide, the view history, the edge scroll, touch
        self._init_navigation()
        # a zoom box being dragged: {"origin": viewport point, "cur": viewport point,
        # "out": Alt held}
        self._zbox: dict | None = None
        # Z held to arm the zoom box for a moment: {"t": when, "prev": the tool before}
        self._zhold: dict | None = None
        self._zhold_return: str | None = None
        # Esc cancelled a move / resize: the coming release is not a commit
        self._swallow_release = False
        # the selection before a selection box began (Esc gives it back)
        self._marquee_before: list | None = None
        # the keyboard focus frame shows after keyboard use, not after a click
        self._focus_visible = False
        # the 'Page' chip shown when the page is out of view (viewport rect) or None
        self._page_chip: QtCore.QRectF | None = None
        # a drop handler's hint shown at the pointer while something is dragged over
        self._drop_hint: tuple | None = None
        # where the polygon preview's fill comes from: () -> 0x00RRGGBB, or None
        self._fill_colour_source = None
        #: View > Cross Hair Cursor: the position at the pointer, 'full' / 'short' lines
        self.CROSSHAIR_READOUT = True
        self.CROSSHAIR_STYLE = "full"
        self._init_select_state()
        from .overlay_scrollbars import OverlayScrollBars

        self.overlay_bars = OverlayScrollBars(self)
        # an overlay over the canvas shows or hides: the 'Page' chip finds its place again
        self._overlay_watch = _OverlayWatch(self)
        self.apply_nav_options()
        canvas_hooks.attach(self)

    def _init_select_state(self) -> None:
        """The state of selecting and transforming."""
        # the press modifiers of a selection box: Shift adds, Ctrl toggles, none replaces
        self._marquee_mods = QtCore.Qt.NoModifier
        # a Touch box whatever the Box list says (Ctrl+Shift + drag by Settings)
        self._marquee_force_touch = False
        # the units the box takes now (its live preview) and the timer that finds them
        self._box_preview: list = []
        self._box_timer = QtCore.QTimer(self)
        self._box_timer.setSingleShot(True)
        self._box_timer.setInterval(self.BOX_PREVIEW_MS)
        self._box_timer.timeout.connect(self._update_box_preview)
        # a click whose meaning waits for the release (or turns into a box on a drag):
        # {"unit", "op": add / remove / select / isolate, "box": a drag starts a box, "vp"}
        self._pending_click: dict | None = None
        # an Alt press: the units under the pointer to cycle through on the release, and
        # whether the press is on the selection (a drag then moves it freely)
        self._alt_press: dict | None = None
        # the unit last added by a click (Select Same starts from it)
        self._key_root: int | None = None
        # a constrained move: ('x' or 'y', the scene point the line runs through)
        self._constrain: tuple | None = None
        # the rotation grip's drag: {"centre", "a0", "steps", "box"}
        self._rotating: dict | None = None
        # a multi-selection's handles scale the whole selection
        self._resize_multi = False
        self._multi_memo = None
        self._multi_box_source = None
        # the handle under the pointer (drawn 2 px larger)
        self._hover_handle: int | None = None
        # an Alt+click's pill at the pointer and the outline of the unit it reached
        self._flash_pill: tuple | None = None
        self._flash_unit: list = []
        self._flash_timer = QtCore.QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash_pill)
        self._flash_unit_timer = QtCore.QTimer(self)
        self._flash_unit_timer.setSingleShot(True)
        self._flash_unit_timer.timeout.connect(self._end_flash_unit)
        # an arrow-key burst's readout next to the selection
        self._key_pill: str | None = None
        self._key_pill_timer = QtCore.QTimer(self)
        self._key_pill_timer.setSingleShot(True)
        self._key_pill_timer.setInterval(self.KEY_PILL_MS)
        self._key_pill_timer.timeout.connect(self.hide_key_pill)
        # names a unit for the status bar and the pills ('Genie AN 80'); says whether a hit
        # belongs to a locked unit (its root, or None)
        self._unit_labeler = None
        self._locked_probe = None
        self._locked_hover: int | None = None
        # a keyboard context menu just closed (Shift+F10 can come twice)
        self._kbd_menu_closed = 0.0

    # -- selecting and transforming: the window's hooks -------------------------------
    def set_multi_box_source(self, fn) -> None:
        """``fn(roots) -> (l, t, r, b)`` or None: the box of a multi-selection its handles
        scale (Adjust Position and Size's box of the units it would change); None = the
        units' extents."""
        self._multi_box_source = fn
        self._multi_memo = None

    def set_unit_labeler(self, fn) -> None:
        """``fn(root) -> str``: a unit's name for the status bar and the pills."""
        self._unit_labeler = fn

    def set_locked_probe(self, fn) -> None:
        """``fn(index) -> root or None``: the root of the LOCKED unit (Edit > Lock, the
        Graphics Builder's lock; not the template) an object under the pointer belongs to."""
        self._locked_probe = fn

    def unit_label(self, root: int) -> str:
        fn = self._unit_labeler
        if fn is not None:
            try:
                return str(fn(root) or "")
            except Exception:  # noqa: BLE001 -- swallow-ok: a label is a nicety
                return ""
        return "Object %d" % root

    def key_root(self) -> int | None:
        """The unit last added by a click (or the last selected), None with no selection."""
        roots = self.selection_roots()
        if self._key_root in roots:
            return self._key_root
        return roots[-1] if roots else None

    def kind_filter(self):
        """The Select filter's ``fn(index) -> bool`` (None = everything)."""
        return self._kind_filter

    def reveal_rect(self, rect: QtCore.QRectF | None, margin: float | None = None) -> bool:
        """Scroll ``rect`` (scene) into view with ``margin`` screen px round it, the zoom
        kept: the least scroll that shows it, centred when it is bigger than the view. True
        when the view moved."""
        if rect is None or rect.isNull():
            return False
        vp = self.viewport().rect()
        if vp.width() < 4 or vp.height() < 4:
            return False
        z = max(self.zoom(), 0.01)
        m = (self.REVEAL_MARGIN if margin is None else margin) / z
        seen = self.mapToScene(vp).boundingRect()
        want = rect.adjusted(-m, -m, m, m)
        if seen.contains(want):
            return False
        bigger = want.width() > seen.width() or want.height() > seen.height()
        if bigger and seen.intersects(rect):
            return False  # (partly in view and too big to show whole)
        self._note_burst()
        self.sync_scene_rect(extra=want)
        if bigger:
            self.centerOn(rect.center())
        else:
            self.ensureVisible(rect, int(round(m * z)), int(round(m * z)))
        return True

    def reveal_selection(self) -> bool:
        """Scroll the selection into view (opt/reveal_selection: the window decides)."""
        return self.reveal_rect(self.selection_box())

    # ---- the readouts: an Alt+click's pill, an arrow-key burst's pill ----------------
    def flash_pill(self, text: str, vp: QtCore.QPoint | None = None, ms: int | None = None) -> None:
        """A pill at the pointer (``vp``) for a moment (``ms``, else CYCLE_PILL_MS, times
        Messages stay)."""
        if vp is None:
            vp = self.viewport().mapFromGlobal(QtGui.QCursor.pos())
        self._flash_pill = (str(text), QtCore.QPoint(vp))
        start_note(self._flash_timer, note_ms(self.CYCLE_PILL_MS if ms is None else ms))
        self.viewport().update()

    def _end_flash_pill(self) -> None:
        self._flash_pill = None
        self.viewport().update()

    def _flash_outline(self, unit: list) -> None:
        """Outline ``unit`` above everything with a light fill for a moment (it may be
        covered by the objects on top of it)."""
        self._flash_unit = list(unit)
        start_note(self._flash_unit_timer, note_ms(self.CYCLE_FLASH_MS))
        self.viewport().update()

    def _end_flash_unit(self) -> None:
        self._flash_unit = []
        self.viewport().update()

    def show_key_pill(self, text: str) -> None:
        """The arrow keys' readout next to the selection ('x, y  (+12, 0)'), gone 800 ms
        after the last key (times Messages stay: :func:`note_ms`), or :meth:`hide_key_pill`."""
        self._key_pill = str(text)
        start_note(self._key_pill_timer, note_ms(self.KEY_PILL_MS))
        self.viewport().update()

    def hide_key_pill(self) -> None:
        self._key_pill_timer.stop()
        if self._key_pill is not None:
            self._key_pill = None
            self.viewport().update()

    def key_pill(self) -> str | None:
        return self._key_pill

    def _key_pill_at(self) -> QtCore.QPoint:
        """Where the key pill goes: by the selection's top-right corner on the canvas."""
        box = self.selection_box()
        vp = self.viewport().rect()
        if box is None:
            return vp.center()
        r = self.mapFromScene(box).boundingRect()
        at = QtCore.QPoint(r.right() - 6, r.top() - 30)
        return QtCore.QPoint(
            max(vp.left() + 4, min(vp.right() - 40, at.x())), max(vp.top() + 4, min(vp.bottom() - 24, at.y()))
        )

    # -- public API ----------------------------------------------------------
    def set_unit_resolver(self, fn) -> None:
        """``fn(index) -> list[int]`` maps a CLICKED leaf to its top-level unit."""
        self._resolve_unit = fn or (lambda i: [i])

    def set_subtree_resolver(self, fn) -> None:
        """``fn(index) -> list[int]`` maps an EXACT node to its own subtree (used for
        programmatic selection from the tree / property panel)."""
        self._resolve_subtree = fn or (lambda i: [i])

    def set_member_filter(self, fn) -> None:
        """``fn(index) -> bool``: whether a Ctrl+double-click may open that
        exact object (a locked template record may: CtDraw shows its sheet
        read-only; the template's page-wide container may not)."""
        self._member_ok = fn or (lambda i: True)

    def set_tool(self, tool: str) -> None:
        # another drawing tool commits an open text edit; the automatic return
        # to Select after drawing leaves it open
        if self._inplace is not None and tool not in ("select", self._tool):
            self.finish_text_edit(True)
        self._cancel_draw()
        self._guide_drag = None
        self._zbox = None
        self._pending_click = None
        self._alt_press = None
        self._rotating = None
        self._tool = tool
        self.viewport().setCursor(self._idle_cursor())
        self.viewport().update()

    def tool(self) -> str:
        return self._tool

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802 -- Qt naming
        """On a screen at a fractional scale (150 %) Qt moves the cached background by whole
        device pixels (dx * 1.5 truncated), so the page edge, the grid and the underlay drift
        from where a full repaint draws them, one pixel a step: draw the background again."""
        super().scrollContentsBy(dx, dy)
        dpr = self.viewport().devicePixelRatioF()
        if (dx and (dx * dpr) % 1.0) or (dy and (dy * dpr) % 1.0):
            self.resetCachedContent()

    def pans_on_left_press(self) -> bool:
        """A left press now pans the page (Space held, or the Hand tool)."""
        return bool(self._space_pan or self._tool == "pan")

    def on_scroll_bar(self, pos) -> bool:
        """``pos`` (viewport coordinates) lies on one of the thin overlay scroll bars."""
        bars = getattr(self, "overlay_bars", None)
        if bars is None:
            return False
        p = QtCore.QPointF(pos)
        return any(b.isVisible() and (b.hot() or b.hit(p)) for b in (bars.h, bars.v))

    def _idle_cursor(self):
        """The pointer shape when nothing is being dragged."""
        shape = canvas_hooks.pointer_mode(self)
        if shape is not None:
            return shape
        if self._tool == "pan":
            return QtCore.Qt.OpenHandCursor
        if self._tool != "select" or self._crosshair:
            return QtCore.Qt.CrossCursor
        return QtCore.Qt.ArrowCursor

    def set_crosshair(self, on: bool) -> None:
        """View > Cross Hair Cursor (help 1172089): full-width / full-height
        lines through the pointer while it is over the drawing area."""
        self._crosshair = bool(on)
        self._cross_pos = None
        if self._crosshair:
            # shown at once where the pointer is (not only after it moves)
            vp = self.viewport().mapFromGlobal(QtGui.QCursor.pos())
            if self.viewport().rect().contains(vp):
                self._cross_pos = self.mapToScene(vp)
        if not self._moving and self._resizing is None:
            self.viewport().setCursor(self._idle_cursor())
        self.viewport().update()

    def crosshair(self) -> bool:
        return self._crosshair

    def _cross_hair_to(self, sp: QtCore.QPointF | None) -> None:
        """Move the cross hairs to ``sp`` (None = hide them). Only the strips
        under the old and the new lines are repainted, so they follow the
        pointer without a full-page repaint on every mouse move."""
        old, self._cross_pos = self._cross_pos, sp
        vp = self.viewport()
        region = QtGui.QRegion()
        for p in (old, sp):
            if p is None:
                continue
            c = self.mapFromScene(self._cross_point(p))
            if self.CROSSHAIR_STYLE == "short":
                region += QtCore.QRect(c.x() - 44, c.y() - 44, 89, 89)
            else:
                region += QtCore.QRect(c.x() - 3, 0, 7, vp.height())
                region += QtCore.QRect(0, c.y() - 3, vp.width(), 7)
                region += QtCore.QRect(c.x() - 9, c.y() - 9, 19, 19)
            if self.CROSSHAIR_READOUT:
                # (the readout rides the pointer; near the right or bottom edge it flips)
                region += self._crosshair_readout_rect(p).toAlignedRect().adjusted(-2, -2, 2, 2)
        if not region.isEmpty():
            vp.update(region)

    def _crosshair_readout_text(self, sp: QtCore.QPointF) -> str:
        c = self._cross_point(sp)
        return "%d, %d" % (c.x(), c.y())

    def _crosshair_readout_rect(self, sp: QtCore.QPointF) -> QtCore.QRectF:
        """Where the cross hairs' position readout is drawn for the pointer at ``sp``."""
        return self._pill_box(self.mapFromScene(sp), self._crosshair_readout_text(sp))

    def _cross_point(self, sp: QtCore.QPointF) -> QtCore.QPointF:
        """Where the cross hairs cross for the pointer at ``sp``: the point a click there
        lands on (snapped to the grid / a guideline with Snap to Grid on)."""
        if self._snap:
            return self._snap_pt(sp)
        return QtCore.QPointF(_clamp(round(sp.x())), _clamp(round(sp.y())))

    def cross_hair_point(self) -> QtCore.QPointF | None:
        """The scene point the cross hairs cross at, or None (off, or the pointer away)."""
        if not self._crosshair or self._cross_pos is None:
            return None
        return self._cross_point(self._cross_pos)

    def pointer_scene(self) -> QtCore.QPointF | None:
        """The pointer's last scene position over the canvas, or None."""
        return None if self._hover_scene is None else QtCore.QPointF(self._hover_scene)

    def selection_box(self) -> QtCore.QRectF | None:
        """The selected units' bounds (following a drag), or None. Remembered until the
        selection, the scene build or the measured bounds change (:meth:`_drop_unit_rects`),
        with a move drag's or an arrow-key burst's offset added: the rulers ask on every
        canvas paint."""
        sc = self.scene()
        serial = getattr(sc, "build_serial", None)
        off = None
        uniform = True  # (the drag offset moves the whole selection)
        if self._moving:
            off = self._move_applied
        elif self._nudge is not None:
            moved = self._nudge["set"]
            if self._sel_set <= moved:
                off = self._nudge["off"]
            elif not self._sel_set.isdisjoint(moved):
                uniform = False
        key = (self._units_gen, id(sc), serial)
        memo = self._sel_box_memo
        if serial is not None and uniform and memo is not None and memo[0] == key:
            base = memo[1]
            box = None if base is None else (base.translated(off) if off is not None else QtCore.QRectF(base))
        else:
            box = None
            for unit in self._units:
                # (the outlines' own measure: the same cache entries the paint uses)
                rect, shape = self._outline_parts(unit)
                r = rect if rect is not None else (shape.boundingRect() if shape is not None else None)
                if r is not None and not r.isNull():
                    box = QtCore.QRectF(r) if box is None else box.united(r)
            if serial is not None and uniform:
                base = (
                    None if box is None else (box.translated(-off) if off is not None else QtCore.QRectF(box))
                )
                self._sel_box_memo = (key, base)
        if self._resizing is not None and box is not None:
            box = QtCore.QRectF(self._resize_rect.normalized())
        return box

    def _drop_unit_rects(self) -> None:
        """Forget the measured bounds and outlines of the selected units (and the
        selection's box): the items moved back, were put where the page has them or
        another scene shows."""
        self._urect_cache.clear()
        self._sel_box_memo = None
        self.__dict__["_multi_memo"] = None

    def set_fill_colour_source(self, fn) -> None:
        """``fn()`` -> the 0x00RRGGBB colour new objects get (the polygon preview's light
        fill), or None (the accent)."""
        self._fill_colour_source = fn

    def set_ruler_margin(self, px: int) -> None:
        """Room at the top and the left of the canvas for the rulers (ui/editor/rulers.py): the
        viewport margins, never over the page."""
        self.setViewportMargins(int(px), int(px), 0, 0)

    # -- guidelines dragged out of a ruler (ui/editor/rulers.py) ----------------------------
    def begin_ruler_guide(self, axis: str, vp: QtCore.QPoint, mods) -> None:
        """A press on a ruler: a NEW guideline ('h' from the top ruler, 'v' from the left)
        follows the pointer once it is dragged (and the canvas scrolls at its edge)."""
        self._begin_guide_drag(axis, 0, vp, mods)
        self._guide_drag.update({"copy": True, "ruler": True, "orig": None})
        self._edge_ruler_press(vp)

    def drag_ruler_guide(self, vp: QtCore.QPoint, mods) -> None:
        if self._guide_drag is not None:
            self._edge_ruler_move(vp, mods)
            self._drag_guide_to(self.mapToScene(vp), vp, mods)

    def end_ruler_guide(self, vp: QtCore.QPoint, mods, cancel: bool = False) -> None:
        """Let go: added over the canvas (snapped as a dragged guideline snaps); let go
        over a ruler, ``cancel``, it is not added."""
        self._edge_ruler_release()
        if self._guide_drag is None:
            return
        if cancel or not self._guide_drag.get("moved"):
            self._guide_drag = None
            self._pill = None
            self.viewport().setCursor(self._idle_cursor())
            self.viewport().update()
            return
        self._end_guide_drag(vp, mods)

    def set_slow_mouse(self, on: bool) -> None:
        """Mouse Slow Speed: drags (move, resize, drawing a box or a line)
        follow the pointer at a quarter of its travel, for fine positioning (Settings >
        Editing > "Slow mouse speed": slow_mouse_factor())."""
        self._slow_mouse = bool(on)

    def slow_mouse(self) -> bool:
        return self._slow_mouse

    def _gear(self, base: QtCore.QPointF, sp: QtCore.QPointF) -> QtCore.QPointF:
        if not self._slow_mouse:
            return sp
        return base + (sp - base) * slow_mouse_factor()

    # -- in-place text editing ------------------------------------------------
    def text_editing(self) -> bool:
        """True while an in-place text editor is open."""
        return self._inplace is not None

    def busy(self) -> bool:
        """The user is in the middle of something on the canvas (an in-place text edit, a
        shape being drawn point by point, a node drag, a mouse button held down, a Space
        pan, arrow-key moves not committed yet): a background change must wait until it
        is done."""
        return bool(
            self._inplace is not None
            or self._nodes
            or self._node_drag is not None
            or self._draw_start is not None
            or self._space_pan
            or self._nudge is not None
            or self._rotating is not None
            or QtWidgets.QApplication.mouseButtons() != QtCore.Qt.NoButton
        )

    def text_edit_item(self):
        return self._inplace

    def begin_text_edit(
        self, pos: QtCore.QPointF, text: str, font: QtGui.QFont, target: dict, follow: bool = False
    ) -> bool:
        """Open the in-place text editor at ``pos``."""
        if self.scene() is None:
            return False
        if self._inplace is not None:
            self.finish_text_edit(True)  # (its commit may rebuild the page)
        sc = self.scene()
        if sc is None:
            return False
        # a box / polygon being drawn is abandoned: its release would land
        # in the editor and the half-drawn preview would outlive it
        self._cancel_draw()
        item = InplaceText(self, text, font)
        item.setPos(pos)
        sc.addItem(item)
        self._inplace = item  # a Python reference keeps it alive
        self._inplace_target = dict(target)
        self._inplace_target.setdefault("orig", text)
        self._inplace_follow = bool(follow)
        self._hover_unit = []
        self.setFocus(QtCore.Qt.OtherFocusReason)
        item.setFocus(QtCore.Qt.OtherFocusReason)
        cur = item.textCursor()
        cur.select(QtGui.QTextCursor.Document)
        item.setTextCursor(cur)
        self.viewport().update()
        return True

    def _take_item(self):
        """Detach the editor: (target, text), or None when none is open (or
        its item went with a scene rebuild -- then the text is lost)."""
        item, target = self._inplace, self._inplace_target
        self._inplace = None
        self._inplace_target = {}
        self._inplace_follow = False
        if item is None:
            return None
        try:
            text = item.toPlainText()
            if item.scene() is not None:
                item.scene().removeItem(item)
        except RuntimeError:  # deleted by scene.clear()
            return None
        self.viewport().update()
        return target, text

    def take_text_edit(self):
        """Close the editor WITHOUT emitting: (target, text) or None. The
        window applies it itself before a scene rebuild (a commit then would
        rebuild the scene from inside its own rebuild)."""
        return self._take_item()

    def finish_text_edit(self, commit: bool = True) -> None:
        """Close the editor; ``commit`` emits ``textEditFinished`` with the
        typed text (the window stores it), else the edit is discarded. A text
        the Text tool placed returns the canvas to Select either way (a stored one keeps the
        tool with Settings 'Keep the tool after drawing or placing an object')."""
        taken = self._take_item()
        if taken is None:
            return
        target, text = taken
        if commit:
            self.textEditFinished.emit(target, text)
        if target.get("kind") == "place" and self._tool == "text" and not (commit and self.KEEP_TOOL):
            self.toolResetRequested.emit()

    def _finish_from(self, item, commit: bool) -> None:
        if item is self._inplace:
            self.finish_text_edit(commit)

    def _focus_left(self, item) -> None:
        """The editor lost the focus to another widget: commit (unless it came
        straight back)."""
        if item is not self._inplace:
            return
        try:
            if item.hasFocus() and self.hasFocus():
                return
        except RuntimeError:
            self._inplace = None
            return
        self.finish_text_edit(True)

    def _inplace_contains(self, vp: QtCore.QPoint) -> bool:
        item = self._inplace
        if item is None:
            return False
        try:
            return item.sceneBoundingRect().contains(self.mapToScene(vp))
        except RuntimeError:
            self._inplace = None
            return False

    def new_text_font(self) -> QtGui.QFont:
        """The font a new text is typed in: the face, size, bold, italic, underline and
        strikeout of the Text tool's style (or, with Last used, the last text's), else the
        factory text a click gives (Arial 14), sized as the page draws text."""
        own = _draw_styles.own_style("text", "text")  # (its fields in their types)
        f = QtGui.QFont(own.get("font") or NEW_TEXT_FONT[0])
        f.setPixelSize(font_px(max(1, own.get("font_size") or NEW_TEXT_FONT[1])))
        for flag, setter in (
            ("bold", f.setBold),
            ("italic", f.setItalic),
            ("underline", f.setUnderline),
            ("strikeout", f.setStrikeOut),
        ):
            setter(bool(own.get(flag)))
        sc = self.scene()
        return sc.text_font(f) if hasattr(sc, "text_font") else f

    def _begin_placement(self, pos: QtCore.QPointF, text: str = "", follow: bool = False) -> None:
        """The Text tool: an empty editor at ``pos`` whose commit creates the
        text there, typed in the font it will have (:meth:`new_text_font`)."""
        self.begin_text_edit(
            pos,
            text,
            self.new_text_font(),
            {"kind": "place", "pos": (int(pos.x()), int(pos.y())), "orig": ""},
            follow=follow,
        )
        if text and self._inplace is not None:
            cur = self._inplace.textCursor()
            cur.movePosition(QtGui.QTextCursor.End)
            self._inplace.setTextCursor(cur)

    def _place_followed(self, sp: QtCore.QPointF) -> None:
        """Move the type-first text to ``sp`` (snapped) and remember it."""
        p = self._snap_pt(sp)
        try:
            self._inplace.setPos(p)
        except RuntimeError:
            self._inplace = None
            return
        self._inplace_target["pos"] = (int(p.x()), int(p.y()))

    def event(self, e: QtCore.QEvent) -> bool:
        if e.type() == QtCore.QEvent.ShortcutOverride:
            # while text is typed, the canvas's own shortcuts (arrow nudge,
            # Shift / Ctrl+Shift resize, '-' delete, the tool letters) must
            # not fire: the keys belong to the text
            if self._inplace is not None:
                mods = e.modifiers()
                if _typing_key(e) or (not (mods & _ALT) and e.key() in _EDIT_NAV_KEYS):
                    e.accept()
                    return True
            elif self._tool == "text" and _typing_key(e) and not (e.key() == QtCore.Qt.Key_Space):
                e.accept()  # typed before the click: CtDraw's text entry
                return True
            elif self._zoom_hold_key(e):
                e.accept()  # Z: the zoom box, held or tapped (keyPressEvent)
                return True
        elif (
            e.type() == QtCore.QEvent.KeyPress
            and e.key() in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab)
            and not e.modifiers() & (_CTRL | _ALT)
            and self._tab_selects()
        ):
            # Tab / Shift+Tab step the selection from object to object (off: the keyboard
            # moves to the next panel)
            back = e.key() == QtCore.Qt.Key_Backtab or bool(e.modifiers() & _SHIFT)
            if not self.select_next(not back):
                # (nothing to select, or past the last object: the keyboard moves on to the
                # next panel, the object still selected)
                super().focusNextPrevChild(not back)
                e.accept()
                return True
            if not self._focus_visible:
                self._focus_visible = True
                self.viewport().update()
            e.accept()
            return True
        return super().event(e)

    def _tab_selects(self) -> bool:
        return bool(
            self.TAB_SELECTS
            and self._tool == "select"
            and self._inplace is None
            and not self._nodes
            and self.scene() is not None
        )

    def focusNextPrevChild(self, nxt: bool) -> bool:  # noqa: N802 -- Qt naming
        # (Tab is the canvas's own while it selects objects: event() takes it first; from a
        # bar over the canvas the keyboard moves on as usual)
        if self._tab_selects() and self.hasFocus():
            return False
        return super().focusNextPrevChild(nxt)

    def set_selection(self, indices: list[int]) -> None:
        """Programmatic selection from EXACT node indices (no signal). Each node
        highlights its own subtree, so a tree-selected nested genie is outlined
        precisely rather than via its outer group."""
        self._set_units([self._resolve_subtree(i) for i in indices], emit=False)

    def selection(self) -> list[int]:
        return list(self._selection)

    def selection_roots(self) -> list[int]:
        return [min(u) for u in self._units if u]

    def set_snap(self, on: bool) -> None:
        self._snap = on

    def set_grid_size(self, px: int) -> None:
        """One pitch for both axes (the View > Grid size presets)."""
        self._grid = max(1, int(px))
        self._grid_y = None

    def set_aliased_text(self, on: bool) -> None:
        """Draw the page's text without smoothing, the way the Graphics Builder and the
        runtime draw it (GDI's aliased text), or smoothed (the default in the editor)."""
        self.setRenderHint(QtGui.QPainter.TextAntialiasing, not on)
        self._aliased_text_on = bool(on)
        sc = self.scene()
        if hasattr(sc, "set_aliased_text"):
            sc.set_aliased_text(on)
        self.viewport().update()

    def set_grid(self, pitch_x: int, pitch_y: int | None = None, origin=(0, 0)) -> None:
        """View > Grid Setup: the horizontal and vertical pitch and the grid
        origin (CtDraw's GridCX / GridCY / GridX / GridY)."""
        self._grid = max(1, int(pitch_x))
        self._grid_y = None if pitch_y is None else max(1, int(pitch_y))
        self._grid_origin = (int(origin[0]), int(origin[1]))

    def grid(self) -> tuple[int, int, int, int]:
        """(horizontal pitch, vertical pitch, origin x, origin y)."""
        gx, gy = self._pitch()
        return gx, gy, self._grid_origin[0], self._grid_origin[1]

    def _pitch(self) -> tuple[int, int]:
        gx = max(1, int(self._grid))
        return gx, (gx if self._grid_y is None else max(1, int(self._grid_y)))

    def set_guidelines(self, vert: list[int], horiz: list[int]) -> None:
        self._guide_drag = None
        self._vguides = sorted(set(int(v) for v in vert))
        self._hguides = sorted(set(int(h) for h in horiz))
        self.viewport().update()

    def guidelines(self) -> tuple[list[int], list[int]]:
        return list(self._vguides), list(self._hguides)

    def set_guides_visible(self, on: bool) -> None:
        self._guides_visible = bool(on)
        self.viewport().update()

    def guides_visible(self) -> bool:
        return self._guides_visible

    def set_snap_guides(self, on: bool) -> None:
        self._snap_guides = bool(on)

    def snap_guides(self) -> bool:
        return self._snap_guides

    def set_smart_guides(self, on: bool) -> None:
        self._smart_guides = bool(on)

    def set_marquee_mode(self, mode: str) -> None:
        """'auto' (L-to-R = enclose, R-to-L = touch, the CAD convention), 'enclose'
        (fully contained) or 'touch' (intersecting)."""
        if mode in ("auto", "enclose", "touch"):
            self._marquee_mode = mode

    def set_highlight(self, indices: list[int]) -> None:
        """Soft outline for tree-hover sync."""
        self._highlight = list(indices)
        self.viewport().update()

    def set_pending(self, indices: list[int]) -> None:
        """Objects about to change by themselves (genies a background update is composing
        again): a light amber wash over each until the update lands."""
        new = list(indices)
        if new != self._pending:
            self._pending = new
            self.viewport().update()

    def frame_rect(self, rect: QtCore.QRectF, margin: float = 0.2, max_zoom: float | None = None) -> None:
        """Centre + zoom onto ``rect`` with a margin, capped at
        ``max_zoom`` (None: Settings > Canvas > "Zoom to an object zooms in at most",
        FRAME_ZOOM_MAX). The view's slack is set up for the target zoom first, so an object
        near or off the page edge is centred too."""
        if rect.isNull():
            return
        if max_zoom is None:
            max_zoom = self.FRAME_ZOOM_MAX
        with self.view_change():
            self._auto_fit = False  # user navigated to a region
            grown = rect.adjusted(
                -rect.width() * margin, -rect.height() * margin, rect.width() * margin, rect.height() * margin
            )
            self._fit_to(grown, rect.center(), max_zoom)
            self.zoomChanged.emit(self.zoom())

    # ---- the work surface (see the module notes) -----------------------------
    def setScene(self, scene) -> None:
        old = self.scene()
        if old is not None and hasattr(old, "workAreaChanged"):
            try:
                old.workAreaChanged.disconnect(self._on_work_area)
            except RuntimeError, TypeError:
                pass
        self._drop_copy_ghost()  # (a copy drag's picture: the old scene's)
        if "_key_pill_timer" in self.__dict__:
            self.hide_key_pill()  # (an arrow-key readout of the old page)
        super().setScene(scene)
        self._drop_burst()
        if scene is not old:
            self.sceneSwapped.emit()
        if scene is not None and hasattr(scene, "set_aliased_text"):
            scene.set_aliased_text(getattr(self, "_aliased_text_on", False))
        self._drop_unit_rects()
        self._bg_sig = None
        if scene is not None and hasattr(scene, "workAreaChanged"):
            scene.workAreaChanged.connect(self._on_work_area)
            self.sync_scene_rect()
        else:
            QtWidgets.QGraphicsView.setSceneRect(self, QtCore.QRectF())

    def _on_work_area(self, _rect=None) -> None:
        if not self._nav_busy:
            self.sync_scene_rect()

    def sync_scene_rect(self, zoom: float | None = None, extra: QtCore.QRectF | None = None) -> None:
        """Set the view's scene rect: the work area plus half a viewport of slack."""
        sc = self.scene()
        work_rect = getattr(sc, "work_rect", None)
        if sc is None or work_rect is None:
            return
        work = work_rect()
        if work.isNull() or work.isEmpty():
            return
        z = zoom if zoom else self.zoom()
        if z <= 0:
            return
        size = self.maximumViewportSize()
        sx = 0.5 * max(1, size.width()) / z
        sy = 0.5 * max(1, size.height()) / z
        r = clamp_rect(work.adjusted(-sx, -sy, sx, sy))
        if r.isNull():
            r = QtCore.QRectF(work)
        vp = self.viewport().rect()
        if vp.width() > 1 and vp.height() > 1:
            r = r.united(self.mapToScene(vp).boundingRect())
        if extra is not None and not extra.isNull():
            r = r.united(extra)
        if r != self.sceneRect():
            busy, self._nav_busy = self._nav_busy, True
            try:
                self.setSceneRect(r)
            finally:
                self._nav_busy = busy

    def _visible_at(self, centre: QtCore.QPointF, zoom: float) -> QtCore.QRectF:
        """The scene rect the viewport shows at ``zoom`` centred on ``centre``
        (a few pixels bigger: the scroll range rounds)."""
        vp = self.viewport()
        w = (vp.width() + 8) / zoom
        h = (vp.height() + 8) / zoom
        return QtCore.QRectF(centre.x() - w / 2, centre.y() - h / 2, w, h)

    def _fit_to(
        self,
        rect: QtCore.QRectF,
        centre: QtCore.QPointF,
        max_zoom: float | None = None,
        whole_viewport: bool = False,
    ) -> None:
        """Zoom so ``rect`` fits, capped at ``max_zoom``."""
        vp = self.viewport()
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0 or vp.width() <= 4 or vp.height() <= 4:
            return
        if whole_viewport:
            size = self.maximumViewportSize()
            vw, vh = size.width(), size.height()
        else:
            vw, vh = vp.width(), vp.height()
        t = min((vw - 4) / rect.width(), (vh - 4) / rect.height())
        capped = max_zoom is not None and t > max_zoom
        if capped:
            t = max_zoom
        busy, self._nav_busy = self._nav_busy, True
        try:
            self.sync_scene_rect(zoom=t, extra=self._visible_at(centre, t))
            if capped or whole_viewport:
                self._set_scale(t)
            else:
                self.fitInView(rect, QtCore.Qt.KeepAspectRatio)
            self.sync_scene_rect(extra=self._visible_at(centre, self.zoom()))
            self.centerOn(centre)
            # again, now the view shows its target: the syncs above also kept
            # what was visible BEFORE (another tab's far corner after a tab
            # switch), which would linger as empty scroll range
            self.sync_scene_rect()
        finally:
            self._nav_busy = busy

    def _set_scale(self, t: float) -> None:
        """Set the zoom to ``t`` without Qt re-anchoring the view."""
        anchor = self.transformationAnchor()
        self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        try:
            self.setTransform(QtGui.QTransform.fromScale(t, t))
        finally:
            self.setTransformationAnchor(anchor)

    def _zoom_to(self, t: float, anchor: QtCore.QPoint) -> None:
        """Zoom to ``t`` keeping the scene point under viewport point
        ``anchor`` there (to within half a screen pixel: scroll positions
        are whole pixels). The slack is set up for ``t`` before the scale, so
        the anchor never slips at the edge of the scroll range."""
        hs, vs = self.horizontalScrollBar(), self.verticalScrollBar()
        # consecutive steps about the same point (wheel notches) keep the
        # exact scene point, so the rounding never adds up
        last = getattr(self, "_zoom_anchor", None)
        if last is not None and last[0] == anchor and last[2] == (hs.value(), vs.value(), self.zoom()):
            p = last[1]
        else:
            p = self.mapToScene(anchor)
        vp = self.viewport()
        after = QtCore.QRectF(p.x() - anchor.x() / t, p.y() - anchor.y() / t, vp.width() / t, vp.height() / t)
        after = after.adjusted(-4 / t, -4 / t, 4 / t, 4 / t)
        busy, self._nav_busy = self._nav_busy, True
        try:
            self.sync_scene_rect(zoom=t, extra=after)
            self._set_scale(t)
            hs.setValue(int(math.floor(p.x() * t - anchor.x() + 0.5)))
            vs.setValue(int(math.floor(p.y() * t - anchor.y() + 0.5)))
            self.sync_scene_rect(extra=after)
            hs.setValue(int(math.floor(p.x() * t - anchor.x() + 0.5)))
            vs.setValue(int(math.floor(p.y() * t - anchor.y() + 0.5)))
        finally:
            self._nav_busy = busy
        self._zoom_anchor = (QtCore.QPoint(anchor), QtCore.QPointF(p), (hs.value(), vs.value(), self.zoom()))

    def _centre_point(self) -> QtCore.QPoint:
        vp = self.viewport()
        return QtCore.QPoint(vp.width() // 2, vp.height() // 2)

    def set_zoom(self, t: float) -> None:
        """Zoom to ``t`` keeping the view centre (the navigation bar's
        presets)."""
        t = min(self.ZOOM_MAX, max(self.ZOOM_MIN, float(t)))
        with self.view_change():
            self._auto_fit = False
            self._zoom_to(t, self._centre_point())
            self.zoomChanged.emit(self.zoom())

    def fit_all(self) -> None:
        """View > Fit All Objects (Ctrl+3): frame the page and everything
        drawn around it (off-page objects, a genie's far members) with a 3 %
        margin."""
        sc = self.scene()
        if sc is None:
            return
        rect = sc.page_rect() if hasattr(sc, "page_rect") else QtCore.QRectF()
        content = sc.itemsBoundingRect()
        if not content.isNull():
            rect = content if rect.isNull() else rect.united(content)
        if rect.isNull():
            return
        self.frame_rect(clamp_rect(rect), margin=0.03, max_zoom=self.ZOOM_MAX)

    def restore_view(self, transform: QtGui.QTransform, scroll) -> None:
        """Show a tab again at the zoom and scroll position it was left at
        (the slack is set up for that view first, so a position in the far
        surround is not clamped)."""
        self.finish_view_animation()
        z = transform.m11() or 1.0
        h, v = int(scroll[0]), int(scroll[1])
        vp = self.viewport()
        seen = QtCore.QRectF(h / z, v / z, vp.width() / z, vp.height() / z)
        seen = seen.adjusted(-4 / z, -4 / z, 4 / z, 4 / z)
        busy, self._nav_busy = self._nav_busy, True
        try:
            self.sync_scene_rect(zoom=z, extra=seen)
            anchor = self.transformationAnchor()
            self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
            try:
                self.setTransform(transform)
            finally:
                self.setTransformationAnchor(anchor)
            self.sync_scene_rect(extra=seen)
            self.horizontalScrollBar().setValue(h)
            self.verticalScrollBar().setValue(v)
            # drop what the previous tab showed (it was kept while switching)
            self.sync_scene_rect()
        finally:
            self._nav_busy = busy
        self.zoomChanged.emit(self.zoom())

    def drawBackground(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        # the scene paints the EDITOR look for the canvas (the view keeps NO
        # background brush, so Qt hands the whole background to the scene)
        chrome = getattr(self.scene(), "editor_chrome", None)
        if chrome is None:
            super().drawBackground(painter, rect)
        else:
            with chrome():
                super().drawBackground(painter, rect)
        # (the background is cached: request_overlay_update(background=True) repaints it)
        canvas_hooks.paint_overlays(self, painter, rect, "background")

    def request_overlay_update(self, background: bool = False) -> None:
        """Repaint the overlays (canvas_hooks); ``background``: the cached background
        too."""
        if background:
            self._bg_sig = None
            self.resetCachedContent()
        self.viewport().update()

    def _sync_background_cache(self, *_args) -> None:
        """Drop the cached background when what it shows changed."""
        sc = self.scene()
        sig = getattr(sc, "background_signature", None)
        t = self.transform()
        key = (
            sig() if sig is not None else None,
            (t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy()),
            self.viewport().devicePixelRatioF(),
        )
        if key != self._bg_sig:
            self._bg_sig = key
            self.resetCachedContent()

    def paintEvent(self, e: QtGui.QPaintEvent) -> None:
        self._sync_background_cache()
        super().paintEvent(e)

    def smart_guide_partners(self, lines=None) -> list:
        """The objects (their extents) the active smart-guide lines come from:
        every target object with an edge or centre on such a line, at most
        SMART_GUIDE_OUTLINE_MAX. The page's own lines name no object."""
        out: list[QtCore.QRectF] = []
        for axis, coord in self._sg_active if lines is None else lines:
            for r in getattr(self, "_sg_rects", ()):
                vals = (
                    (r.left(), r.center().x(), r.right())
                    if axis == "v"
                    else (r.top(), r.center().y(), r.bottom())
                )
                if any(abs(v - coord) < 0.01 for v in vals) and r not in out:
                    out.append(r)
                    if len(out) >= self.SMART_GUIDE_OUTLINE_MAX:
                        return out
        return out

    # ---- grid / guideline snap of a MOVE --------------
    def _model_box(self, indices) -> QtCore.QRectF | None:
        """The union of the selected units' Extents (rect1, the coordinates
        the Properties panel shows) -- not the item bounds, which include the
        pen. Items without an Extent fall back to their bounds."""
        page = getattr(self.scene(), "_page", None)
        box = None
        roots = sorted({min(u) for u in self._units if u}) or sorted(set(indices))
        for i in roots:
            r = None
            if page is not None and 0 <= i < len(page.objects):
                g = page.objects[i].geometry
                if g is not None and (g.extent_x > 0 or g.extent_y > 0 or g.x or g.y):
                    r = QtCore.QRectF(g.x, g.y, max(0, g.extent_x), max(0, g.extent_y))
            if r is None:
                r = self._unit_rect(next((u for u in self._units if u and min(u) == i), [i]))
            if r is None:
                continue
            box = QtCore.QRectF(r) if box is None else box.united(r)
        return box

    def selection_start_boxes(self) -> list:
        """Each selected unit's Extents as the page has them now (before the drag or the
        resize running applies): the extra snap boxes equal to one of them are left out, so
        an object never catches its own place on an underlaid earlier version."""
        page = getattr(self.scene(), "_page", None)
        out = []
        for u in self._units:
            if not u:
                continue
            i = min(u)
            r = None
            if page is not None and 0 <= i < len(page.objects):
                g = page.objects[i].geometry
                if g is not None and (g.extent_x > 0 or g.extent_y > 0 or g.x or g.y):
                    r = QtCore.QRectF(g.x, g.y, max(0, g.extent_x), max(0, g.extent_y))
            if r is None:
                r = self._unit_rect(u)
            if r is not None:
                out.append(r)
        return out

    def _extra_snap_sets(self) -> list:
        """The extra snap targets (canvas_hooks.snap_sets) for a point snapped now: while a
        handle, a vertex or the hot spot is dragged, without the selection's own start boxes
        (worked out once per gesture)."""
        sets = canvas_hooks.snap_sets(self)
        if not sets or (self._resizing is None and self._node_drag is None):
            return sets
        gesture = self._resize_origin if self._resizing is not None else self._node_drag
        memo = self.__dict__.get("_extra_snap_memo")
        if (
            memo is not None
            and memo[0] is gesture
            and len(memo[1]) == len(sets)
            and all(a is b for a, b in zip(memo[1], sets))
        ):
            return memo[2]
        own = self.selection_start_boxes()
        out = [s.without(own) for s in sets]
        # (the gesture and the provider's sets are kept: the identity checks stay true)
        self.__dict__["_extra_snap_memo"] = (gesture, sets, out)
        return out

    def _move_snap(self, raw: QtCore.QPointF, mods=None):
        """Snap a move drag and work out which guide lines to show."""
        if self._move_free(mods):
            return self._keep_in_range(raw), []
        sg_out, sg_lines = self._sg_snap_move(raw, mods)
        box = self._mv_box.translated(raw) if self._mv_box is not None else None
        pitch = self._pitch()
        out = [raw.x(), raw.y()]
        lines: list[tuple] = []
        for axis, tag in ((0, "v"), (1, "h")):
            r = out[axis]
            cands = []
            sg = next((c for a, c in sg_lines if a == tag), None)
            if sg is not None:
                d = (sg_out.x(), sg_out.y())[axis] - r
                cands.append((abs(d), 1, d, (tag, sg)))
            guides = self._vguides if axis == 0 else self._hguides
            if box is not None and self._snap_guides and guides:
                edges = (
                    (box.left(), box.center().x(), box.right())
                    if axis == 0
                    else (box.top(), box.center().y(), box.bottom())
                )
                tol = self._guide_tol(axis)
                best = None
                for e in edges:
                    for g in guides:
                        d = g - e
                        if abs(d) <= tol and (best is None or abs(d) < abs(best[0])):
                            best = (d, float(g))
                if best is not None:
                    cands.append((abs(best[0]), 0, best[0], (tag, best[1])))
            grid_d = None
            if self._snap and pitch[axis] > 1:
                if box is not None:
                    v = (box.left(), box.top())[axis]
                    grid_d = self._grid_value(v, axis) - v
                else:  # no box: round the displacement
                    g = pitch[axis]
                    grid_d = round(r / g) * g - r
            chosen = None
            for dist, prio, d, line in sorted(cands):
                if prio == 0 and grid_d is not None and dist > abs(grid_d):
                    continue  # a nearer grid point beats a guideline
                chosen = (d, line)  # a smart guide in reach beats the grid
                break
            if chosen is not None:
                out[axis] = r + chosen[0]
                lines.append(chosen[1])
            elif grid_d is not None:
                out[axis] = r + grid_d
        return self._keep_in_range(QtCore.QPointF(out[0], out[1])), lines

    @staticmethod
    def _move_free(mods=None) -> bool:
        """The keys ``mods`` (default: held now) move without snapping (Alt; Alt+Shift when
        Alt copies)."""
        if mods is None:
            mods = QtWidgets.QApplication.keyboardModifiers()
        return "free" in modifier_roles(mods, moving=True)

    def _keep_in_range(self, d: QtCore.QPointF) -> QtCore.QPointF:
        """A move displacement cut so the moved unit stays within +/- 32000
        (the record range; the far surround at 2 % zoom lies beyond it)."""
        box = self._mv_box
        if box is None:
            return d
        out = [d.x(), d.y()]
        for axis, (lo, hi) in enumerate(((box.left(), box.right()), (box.top(), box.bottom()))):
            dmin, dmax = -COORD_LIMIT - lo, COORD_LIMIT - hi
            if dmin <= dmax:
                out[axis] = max(dmin, min(dmax, out[axis]))
        return QtCore.QPointF(out[0], out[1])

    def flash_alignment_guides(self) -> None:
        """Flash alignment lines after an arrow-key move."""
        sc = self.scene()
        if not self._smart_guides or self._moving or sc is None or not hasattr(sc, "_items"):
            return
        st = self._nudge
        if st is None or not st.get("guides"):
            self._sg_begin_drag()
            if st is not None:
                st["guides"] = True
        bb = self._sg_bbox
        if bb is None or bb.isNull():
            return
        if st is not None:
            bb = bb.translated(st["off"])
        tol = 1.0
        active: list[tuple] = []
        for edge in (bb.left(), bb.center().x(), bb.right()):
            for t in self._sg_targets_x:
                if abs(t - edge) <= tol and ("v", t) not in active:
                    active.append(("v", t))
                    break
        for edge in (bb.top(), bb.center().y(), bb.bottom()):
            for t in self._sg_targets_y:
                if abs(t - edge) <= tol and ("h", t) not in active:
                    active.append(("h", t))
                    break
        self._sg_active = active
        self.viewport().update()
        if active:
            start_note(self._sg_flash, note_ms(self.SG_LINGER_MS))

    # ---- arrow-key bursts (the window commits ONE move per burst) ----------
    def _items_of(self, indices) -> list:
        """The scene items of ``indices`` and their alarm indicators: what a move
        drag or an arrow-key burst moves."""
        sc = self.scene()
        items = getattr(sc, "_items", None) or {}
        ai_items = getattr(sc, "_ai_items", None) or {}
        out = []
        for i in indices:
            it = items.get(i)
            if it is not None:
                out.append(it)
            ai = ai_items.get(i)  # a selected group's alarm indicator
            if ai is not None:
                out.append(ai)
        return out

    @staticmethod
    def _move_items(items, dx: float, dy: float) -> None:
        if not (dx or dy):
            return
        for it in items:
            try:
                it.moveBy(dx, dy)
            except RuntimeError:  # swallow-ok: the scene was built again; its item is gone
                pass

    def nudge_preview(self, indices, dx: float, dy: float) -> None:
        """Move the items of ``indices`` by (dx, dy) on the canvas only. The arrow
        keys of a burst move the items at once; the window commits the summed move
        to the page once (:meth:`end_nudge_preview`). The first press of a burst
        takes the items and the alignment targets."""
        st = self._nudge
        if st is None:
            if self._smart_guides:
                self._sg_begin_drag()
            st = self._nudge = {
                "scene": self.scene(),
                "items": self._items_of(indices),
                "set": frozenset(indices),
                "off": QtCore.QPointF(),
                "guides": self._smart_guides,
            }
        self._move_items(st["items"], dx, dy)
        st["off"] = st["off"] + QtCore.QPointF(dx, dy)
        self.viewport().update()

    def nudge_pending(self) -> bool:
        """An arrow-key burst is shown on the canvas and not committed yet."""
        return self._nudge is not None

    def nudge_offset(self) -> tuple:
        """(dx, dy) of the burst shown, (0, 0) when there is none."""
        st = self._nudge
        if st is None:
            return (0, 0)
        return int(round(st["off"].x())), int(round(st["off"].y()))

    def end_nudge_preview(self) -> tuple:
        """End the burst shown: its items go back where the page has them (the
        window then commits the move, and the scene moves them by what the page
        commits). Returns the burst's (dx, dy)."""
        st, self._nudge = self._nudge, None
        if st is None:
            return (0, 0)
        off = st["off"]
        self._move_items(st["items"], -off.x(), -off.y())
        self._drop_unit_rects()
        self.viewport().update()
        return int(round(off.x())), int(round(off.y()))

    def _live_shift(self, index: int) -> QtCore.QPointF | None:
        """How far the item of ``index`` is moved ahead of the page: a move drag's
        displacement or an arrow-key burst's; None when it is where the page has it."""
        if self._moving:
            return self._move_applied if index in self._sel_set else None
        st = self._nudge
        if st is not None and index in st["set"]:
            return st["off"]
        return None

    def _clear_sg_flash(self) -> None:
        if self._moving:
            return
        self._sg_active = []
        self.viewport().update()

    def fit(self) -> None:
        """Fit the page to the viewport and ENTER auto-fit mode (track the page on
        resize until the user manually zooms). At once: a page opening uses it;
        the Fit Page command is :meth:`fit_page`."""
        self.finish_view_animation()
        self._auto_fit = True
        self._apply_fit()

    def fit_page(self) -> None:
        """View > Fit Page: :meth:`fit`, gliding there; Previous View comes back."""
        with self.view_change():
            self.fit()

    def _apply_fit(self) -> None:
        """Do the fit, or nothing if the view isn't laid out yet."""
        sc = self.scene()
        if sc is None:
            return
        vp = self.viewport()
        # not realized yet (before show, or zero-size): fitInView would miscompute;
        # the show/resize handlers re-apply.
        if not self.isVisible() or vp.width() <= 1 or vp.height() <= 1:
            return
        page = sc.page_rect() if hasattr(sc, "page_rect") else QtCore.QRectF()
        if page.isNull():
            self.fitInView(sc.sceneRect(), QtCore.Qt.KeepAspectRatio)
        else:
            rect = _viewnav.fit_rect(page)
            self._fit_to(rect, rect.center(), whole_viewport=True)
        self.zoomChanged.emit(self.zoom())

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if self._auto_fit:
            # let the layout settle (viewport gets its real size) before fitting;
            # re-check the flag at fire time in case the user zoomed meanwhile
            QtCore.QTimer.singleShot(0, self, lambda: self._auto_fit and self._apply_fit())

    def resizeEvent(self, ev) -> None:
        if self._nav_busy:
            # a zoom / fit / sync of ours toggled the scroll bars: it finishes
            # with the new viewport itself
            super().resizeEvent(ev)
            return
        centre = None
        old = ev.oldSize()
        if not self._auto_fit and old.width() > 0 and old.height() > 0 and self.scene() is not None:
            # the view centre before the resize (Qt re-centres on it, but
            # within the slack of the old size)
            vp = self.viewport()
            dw = ev.size().width() - old.width()
            dh = ev.size().height() - old.height()
            centre = self.mapToScene(QtCore.QPoint((vp.width() - dw) // 2, (vp.height() - dh) // 2))
        super().resizeEvent(ev)
        self._nav_busy = True
        try:
            self.sync_scene_rect()
            if centre is not None:
                self.centerOn(centre)
        finally:
            self._nav_busy = False
        if self._auto_fit:
            self._apply_fit()

    def open_actual_size(self) -> None:
        """A page just opened at 100 % (Settings > Canvas): centred when it fits, else from
        its top-left corner. Done once the view is laid out."""

        def go():
            sc = self.scene()
            self._auto_fit = False
            self.setTransform(QtGui.QTransform())
            r = sc.page_rect() if sc is not None and hasattr(sc, "page_rect") else None
            if r is not None and not r.isNull():
                vp = self.viewport().rect()
                if r.width() <= vp.width() and r.height() <= vp.height():
                    self.centerOn(r.center())
                else:
                    self.centerOn(r.left() + vp.width() / 2.0 - 12, r.top() + vp.height() / 2.0 - 12)
            self.zoomChanged.emit(1.0)

        if self.viewport().width() < 10:
            QtCore.QTimer.singleShot(0, self, go)
        else:
            go()

    def reset_zoom(self) -> None:
        """Zoom 100 % (Ctrl+1), keeping the view centre."""
        with self.view_change():
            self._auto_fit = False  # user took manual zoom control
            self._zoom_to(1.0, self._centre_point())
            self.zoomChanged.emit(1.0)

    def zoom(self) -> float:
        return self.transform().m11()

    ZOOM_MIN, ZOOM_MAX = _viewnav.ZOOM_MIN, _viewnav.ZOOM_MAX  # (the Simulate page's too)

    def zoom_by(self, factor: float, under_mouse: bool = True, at=None) -> None:
        """Zoom by ``factor`` about viewport point ``at`` (the wheel's
        position), else the pointer when ``under_mouse`` and it is over the
        canvas, else the view centre."""
        self.finish_view_animation()
        self._auto_fit = False  # user took manual zoom control
        z = self.zoom()
        target = min(self.ZOOM_MAX, max(self.ZOOM_MIN, z * factor))
        if abs(target - z) < 1e-9:
            return
        vp = self.viewport()
        m = QtCore.QPoint(at) if at is not None else None
        if m is None and under_mouse and vp.underMouse():
            pos = vp.mapFromGlobal(QtGui.QCursor.pos())
            if vp.rect().contains(pos):
                m = pos
        if m is None:
            m = self._centre_point()
        self._zoom_to(target, m)
        self.zoomChanged.emit(self.zoom())

    # -- nodes (reshape) -----------------------------------------------------
    def set_node_editable(self, fn) -> None:
        """``fn(index) -> bool``: whether the object's nodes may be edited
        (the window refuses locked objects, genie members and template
        records). None = every reshapable object."""
        self._node_editable = fn or (lambda i: True)
        self.viewport().update()

    def reshape_target(self):
        """(index, geometry.ReshapeInfo) of the single selected object when
        its nodes can be edited on the canvas, else (None, None). Read from
        the scene's model (no record parse)."""
        idx = self._single_object_unit()
        if idx is None:
            return None, None
        page = getattr(self.scene(), "_page", None)
        if page is None or not 0 <= idx < len(page.objects):
            return None, None
        obj = page.objects[idx]
        tc = obj.raw.get("_type_code")
        if tc not in _geom.RESHAPE_TCS or not (obj.raw.get("_dwflags") or 0) & 0x2:
            return None, None
        t = obj.geometry.transform
        if (t.a, t.b, t.c, t.d, t.h, t.k) != (1.0, 0.0, 0.0, 1.0, 0.0, 0.0):
            return None, None
        nodes = [(int(x), int(y)) for x, y in (obj.geometry.nodes or ())]
        if len(nodes) < 2:
            return None, None
        try:
            if not self._node_editable(idx):
                return None, None
        except Exception:  # noqa: BLE001 -- a gate that fails refuses
            return None, None
        kind = {_geom.LINE_TC: "line", _geom.PIPE_TC: "pipe"}.get(tc, "polyline")
        closed = False
        if kind == "polyline":
            fl = obj.style.style_flags or b""
            if len(fl) >= 6:
                closed = not (fl[_geom.FLAG_ATTRIBS] & 0x01) or bool(fl[_geom.FLAG_FILL_STYLE] & 0x0F)
            else:
                closed = not obj.style.polygon_open or obj.style.filled
        return idx, _geom.ReshapeInfo(kind, nodes, closed)

    def node_selection(self):
        """(index, sorted node numbers) of the selected nodes, or None."""
        idx, info = self.reshape_target()
        if idx is None or idx != self._node_owner:
            return None
        sel = sorted(k for k in self._node_sel if k < len(info.nodes))
        return (idx, sel) if sel else None

    def set_node_selection(self, index: int, nodes) -> None:
        """Select nodes of object ``index`` (after an insert: the new node)."""
        self._node_owner = index
        self._node_sel = {int(k) for k in nodes}
        self.viewport().update()

    def clear_node_selection(self) -> None:
        self._node_drag = None
        if self._node_sel or self._node_owner is not None:
            self._node_sel = set()
            self._node_owner = None
            self.viewport().update()

    def _node_tol(self) -> float:
        """Half the node hit box in scene units: CtDraw's 7 x 7 box round
        each node (-3 .. +4), a little wider on screen, and the reach of
        Settings > Canvas > "Grab handles and nodes within" on top."""
        return (max(NODE_BOX_PX, handle_size()) / 2.0 + 1.0 + pointer_reach()) / max(self.zoom(), 0.01)

    def _node_at(self, viewpos: QtCore.QPoint):
        """The number of the reshape target's node under the cursor (the
        nearest when several are in reach), or None."""
        idx, info = self.reshape_target()
        if idx is None:
            return None
        sp = self.mapToScene(viewpos)
        tol = self._node_tol()
        best = None
        for k, (x, y) in enumerate(info.nodes):
            dx, dy = abs(sp.x() - x), abs(sp.y() - y)
            if dx <= tol and dy <= tol:
                d = dx * dx + dy * dy
                if best is None or d < best[0]:
                    best = (d, k)
        return None if best is None else best[1]

    def _near_node(self, x: float, y: float) -> bool:
        idx, info = self.reshape_target()
        if idx is None:
            return False
        off = self._live_shift(idx)  # (x, y) follows the moved object
        if off is not None:
            x, y = x - off.x(), y - off.y()
        tol = self._node_tol()
        return any(abs(x - nx) <= tol and abs(y - ny) <= tol for nx, ny in info.nodes)

    def insert_node_at_cursor(self) -> bool:
        """Insert / '+': a node where the pointer is on the selected pipe's
        or polygon's outline (help 1172187). True when the key was taken (a
        node requested, or the reason it cannot be sent via
        ``reshapeRefused``)."""
        idx, info = self.reshape_target()
        if idx is None:
            return False
        if not info.can_insert:
            self.reshapeRefused.emit("A line has exactly two nodes. To add nodes, draw a polyline instead.")
            return True
        sp = self._hover_scene
        hit = self._outline_hit(sp) if sp is not None else None
        if hit is None:
            noun = "pipe" if info.kind == "pipe" else "polygon"
            self.reshapeRefused.emit(
                "Point at the outline of the selected %s where the new "
                "node should go, then press Insert or +." % noun
            )
            return True
        at, (x, y) = hit
        self.nodeInsertRequested.emit(idx, at, x, y)
        return True

    def _begin_node_press(self, k: int, mods, sp: QtCore.QPointF) -> None:
        """A press on node ``k``: select it (Ctrl adds / removes it) and arm a
        drag of the selected nodes."""
        idx, info = self.reshape_target()
        if idx != self._node_owner:
            self._node_sel = set()
        self._node_owner = idx
        was = k in self._node_sel
        if mods & _CTRL:
            if was:
                self._node_sel.discard(k)  # Ctrl+click deselects it
                self._node_drag = None
                self.viewport().update()
                return
            self._node_sel.add(k)
        elif not was:
            self._node_sel = {k}  # another node: only this one
        self._node_drag = {
            "index": idx,
            "info": info,
            "grab": k,
            "press": QtCore.QPointF(sp),
            "delta": (0, 0),
            "moved": False,
            "toggle": was and not mods & _CTRL,
        }
        self.viewport().setCursor(QtCore.Qt.CrossCursor)
        self.viewport().update()

    def _drag_nodes_to(self, sp: QtCore.QPointF, vp: QtCore.QPoint) -> None:
        d = self._node_drag
        press = d["press"]
        if not d["moved"]:
            if (sp - press).manhattanLength() < drag_start() / max(self.zoom(), 0.01):
                return
            d["moved"] = True
        raw = self._gear(press, sp) - press
        gx, gy = d["info"].nodes[d["grab"]]
        target = QtCore.QPointF(gx + raw.x(), gy + raw.y())
        if QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.AltModifier:
            target = QtCore.QPointF(round(target.x()), round(target.y()))
        else:
            target = self._snap_pt(target)  # the grabbed node lands on the grid
        # every selected node stays within the record range (+/- 32000); the nearest pixel,
        # not the truncated one
        delta = [round(target.x()) - gx, round(target.y()) - gy]
        nodes = d["info"].nodes
        moved = [nodes[k] for k in self._node_sel if 0 <= k < len(nodes)] + [(gx, gy)]
        for axis in (0, 1):
            lo = min(p[axis] for p in moved)
            hi = max(p[axis] for p in moved)
            if -COORD_LIMIT - lo <= COORD_LIMIT - hi:
                delta[axis] = max(-COORD_LIMIT - lo, min(COORD_LIMIT - hi, delta[axis]))
        target = QtCore.QPointF(gx + delta[0], gy + delta[1])
        d["delta"] = (delta[0], delta[1])
        self._pill = "%d, %d  (%+d, %+d)" % (target.x(), target.y(), d["delta"][0], d["delta"][1])
        self._last_vp = vp
        self.viewport().update()

    def _end_node_drag(self) -> None:
        d, self._node_drag = self._node_drag, None
        self._pill = None
        self.viewport().setCursor(self._idle_cursor())
        if d is None:
            return
        dx, dy = d["delta"]
        if d["moved"] and (dx or dy):
            self.nodesMoved.emit(d["index"], sorted(self._node_sel), dx, dy)
        elif not d["moved"] and d["toggle"]:
            # a plain click on a selected node toggles it off (help 1172187)
            self._node_sel.discard(d["grab"])
        self.viewport().update()

    def _dragged_nodes(self, info):
        """The reshape target's nodes as a running node drag leaves them."""
        d = self._node_drag
        if d is None or not d["moved"]:
            return list(info.nodes)
        dx, dy = d["delta"]
        return [(x + dx, y + dy) if k in self._node_sel else (x, y) for k, (x, y) in enumerate(info.nodes)]

    # -- selection helpers ---------------------------------------------------
    def _set_units(self, units: list[list[int]], emit: bool = True) -> None:
        clean = [sorted(set(u)) for u in units if u]
        self._units = clean
        self._units_gen += 1
        self._sel_box_memo = None
        self._hover_handle = None  # (the next pointer move finds it on the new box)
        flat: list[int] = []
        for u in clean:
            flat.extend(u)
        self._sel_set = set(flat)
        self._selection = sorted(self._sel_set)
        if self._node_owner is not None and self._single_object_unit() != self._node_owner:
            # another selection: its nodes are not the old object's
            self._node_sel = set()
            self._node_owner = None
            self._node_drag = None
        self.viewport().update()
        if emit:
            self.selectionChanged.emit(self.selection_roots())

    def _toggle_unit(self, unit: list[int]) -> None:
        root = min(unit)
        units = [u for u in self._units if min(u) != root]
        if len(units) == len(self._units):
            units.append(unit)
            self._key_root = root
        self._set_units(units)

    def _add_unit(self, unit: list[int]) -> None:
        """Add ``unit`` to the selection (a Shift / Ctrl click): it becomes the key unit."""
        root = min(unit)
        if root not in self.selection_roots():
            self._set_units([list(u) for u in self._units] + [list(unit)])
        self._key_root = root

    def _remove_unit(self, unit: list[int]) -> None:
        root = min(unit)
        if root in self.selection_roots():
            self._set_units([u for u in self._units if min(u) != root])

    # -- coordinate helpers --------------------------------------------------
    def _grid_value(self, v: float, axis: int) -> float:
        """The grid line nearest ``v`` on ``axis`` (0 = x, 1 = y), counted from
        the grid origin; halfway between two lines takes the lower one."""
        g = self._pitch()[axis]
        o = self._grid_origin[axis]
        lo = o + math.floor((v - o) / g) * g
        return float(lo + g) if (lo + g - v) < (v - lo) else float(lo)

    def _guide_tol(self, axis: int | None = None) -> float:
        """How far a guideline catches, in page px: 8 (8 screen px once
        zoomed in); with Snap to Grid on ``axis`` at least the grid pitch
        (max(8, pitch)) -- so a guideline nearer than
        the grid point always wins, however coarse the grid."""
        tol = self.GUIDE_SNAP_PX / max(1.0, self.zoom())
        if axis is not None and self._snap:
            pitch = self._pitch()[axis]
            if pitch > 1:
                tol = max(tol, float(pitch))
        return tol

    def _snap_axis(self, v: float, axis: int) -> float:
        grid_on = self._snap and self._pitch()[axis] > 1
        out = self._grid_value(v, axis) if grid_on else float(round(v))
        guides = self._vguides if axis == 0 else self._hguides
        guides = guides if self._snap_guides and guides else ()
        extra = self._extra_snap_sets()
        if guides or extra:
            tol = self._guide_tol(axis)
            best = None
            for g in guides:
                d = abs(g - v)
                if d <= tol and (best is None or d < best[0]):
                    best = (d, float(g))
            # the edges and centres of the extra snap boxes (the underlay's objects) catch as
            # a guideline does, on whole page pixels (a centre may lie half way: the object
            # is stored on whole pixels)
            for s in extra:
                g = s.nearest(v, axis, tol)
                if g is not None:
                    g = float(math.floor(g + 0.5))
                    d = abs(g - v)
                    if best is None or d < best[0]:
                        best = (d, g)
            # a guideline wins over the grid when it is at least as near
            if best is not None and (not grid_on or best[0] <= abs(out - v)):
                out = float(best[1])
        return out

    def placement_point(self, x: float, y: float, grid: bool = True) -> tuple[int, int]:
        """Where a placed symbol, genie or picture lands for (x, y)."""
        gx, gy = self._pitch()
        sets = canvas_hooks.snap_sets(self)
        tol = self.SMART_GUIDE_TOL / max(self.zoom(), 0.01)
        corner = None
        for s in sets:
            for r in s.rects:
                cx = float(math.floor(r.left() + 0.5))
                cy = float(math.floor(r.top() + 0.5))
                dx, dy = abs(cx - x), abs(cy - y)
                if dx <= tol and dy <= tol:
                    d = (max(dx, dy), dx + dy)
                    if corner is None or d < corner[0]:
                        corner = (d, cx, cy)
        if corner is not None:
            return int(_clamp(corner[1])), int(_clamp(corner[2]))
        out = []
        for v, axis, pitch in ((x, 0, gx), (y, 1, gy)):
            snapped = self._grid_value(v, axis) if grid and pitch > 1 else float(round(v))
            best = None
            for s in sets:
                for r in s.rects:
                    e = float(math.floor((r.left() if axis == 0 else r.top()) + 0.5))
                    d = abs(e - v)
                    if d <= tol and (best is None or d < best[0]):
                        best = (d, e)
            if best is not None:
                snapped = best[1]
            out.append(int(_clamp(snapped)))
        return out[0], out[1]

    def _snap_pt(self, p: QtCore.QPointF) -> QtCore.QPointF:
        """``p`` snapped to the grid or a guideline."""
        return QtCore.QPointF(_clamp(self._snap_axis(p.x(), 0)), _clamp(self._snap_axis(p.y(), 1)))

    def next_grid_line(self, v: float, axis: int, direction: int) -> float:
        """The grid line after ``v`` on ``axis`` (0 = x, 1 = y) in ``direction`` (+1 / -1),
        counted from the grid origin: an arrow key's step with Snap to Grid."""
        g = self._pitch()[axis]
        o = self._grid_origin[axis]
        k = (v - o) / g
        if direction > 0:
            n = math.floor(k + 1e-9) + 1
        else:
            n = math.ceil(k - 1e-9) - 1
        return float(o + n * g)

    def selection_model_box(self) -> QtCore.QRectF | None:
        """The selected units' Extents (the coordinates the Properties panel shows),
        following a move drag or an arrow-key burst; None with nothing selected."""
        if not self._units:
            return None
        box = self._model_box(self._selection)
        if box is None:
            return None
        off = self._live_shift(self._units[0][0])
        return box.translated(off) if off is not None else box

    def grid_point(self, x: float, y: float) -> tuple[int, int]:
        """(x, y) on the nearest grid intersection (a placement point: a pasted
        genie's or an imported picture's top-left); whole pixels when the grid
        is finer than 2 px."""
        gx, gy = self._pitch()
        return (
            int(_clamp(self._grid_value(x, 0) if gx > 1 else round(x))),
            int(_clamp(self._grid_value(y, 1) if gy > 1 else round(y))),
        )

    def set_kind_filter(self, fn) -> None:
        """``fn(index) -> bool`` gates which objects are selectable (the Select-filter
        dropdown); None = everything. Filtered-out objects are CLICK-THROUGH: the hit
        walk continues to items beneath them."""
        self._kind_filter = fn
        self.viewport().update()

    def _hits_under(self, viewpos: QtCore.QPoint) -> list[int]:
        """Page indices under the cursor, topmost first, deduped, kind-filtered."""
        out: list[int] = []
        seen = set()
        for item in self.items(viewpos):
            # the root's page index, stamped on every item of its subtree by
            # PageScene.set_page. NEVER walk up with parentItem() /
            # topLevelItem(): PySide6 hands the top-level item it reaches back
            # to Python ownership and DELETES it when the wrapper is dropped
            # (see render.scene.PAGE_INDEX_ROLE).
            d = item.data(PAGE_INDEX_ROLE)
            idx = d if isinstance(d, int) and self._scene_has_index(d) else None
            if idx is None or idx in seen:
                continue
            if self._is_template_anchor(idx, item):
                continue  # its members still resolve to the template unit
            seen.add(idx)
            if self._kind_filter is not None and not self._kind_filter(idx):
                continue
            out.append(idx)
        return out

    def _is_template_anchor(self, idx: int, item) -> bool:
        """Is ``item`` the page template root's invisible anchor?"""
        if item.data(0) != idx:
            return False  # a drawn child item of the root (resolved art)
        page = getattr(self.scene(), "_page", None)
        if page is None or not 0 <= idx < len(page.objects):
            return False
        o = page.objects[idx]
        if o.parent is not None or not o.members:
            return False
        return bool(o.raw.get("_from_template")) or str(o.raw.get("_ref") or "").lower().endswith("ctt")

    def _hit_index(self, viewpos: QtCore.QPoint):
        """The topmost selectable hit under the pointer."""
        for h in self._hits_under(viewpos):
            if self._resolve_unit(h):
                return h, None
        return None, None

    def _scene_has_index(self, idx: int) -> bool:
        sc = self.scene()
        return bool(sc is not None and hasattr(sc, "_items") and idx in sc._items)

    def _index_rect(self, idx: int) -> QtCore.QRectF | None:
        sc = self.scene()
        if sc is None or not hasattr(sc, "_items"):
            return None
        item = sc._items.get(idx)
        return item.sceneBoundingRect() if item is not None else None

    def _unit_rect(self, unit: list[int]) -> QtCore.QRectF | None:
        """The union of a unit's item bounds, cached per build."""
        sc = self.scene()
        serial = getattr(sc, "build_serial", None)
        if not unit or serial is None:
            return self._measure_unit(unit)
        key = (id(sc), serial, tuple(unit))
        off = self._live_shift(unit[0])
        cache = self._urect_store(sc, serial)
        base = cache.get(key, _MISS)
        if base is _MISS:
            r = self._measure_unit(unit)
            base = None if r is None else (r.translated(-off) if off is not None else QtCore.QRectF(r))
            cache[key] = base
        if base is None:
            return None
        return base.translated(off) if off is not None else QtCore.QRectF(base)

    #: the bounds and outlines the cache keeps beyond two per selected unit
    URECT_SPARE = 32

    def _urect_store(self, sc, serial) -> dict:
        """The bounds cache of the scene build shown (emptied when the scene is built
        again). It holds every selected unit's bounds and outline, and URECT_SPARE more."""
        build = (id(sc), serial)
        cache = self._urect_cache
        if self.__dict__.get("_urect_build") != build:
            cache.clear()
            self._urect_build = build
        elif len(cache) >= 2 * len(self._units) + self.URECT_SPARE:
            cache.clear()
        return cache

    def _measure_unit(self, unit) -> QtCore.QRectF | None:
        rect: QtCore.QRectF | None = None
        for i in unit:
            r = self._index_rect(i)
            if r is None or r.isNull():
                continue
            rect = r if rect is None else rect.united(r)
        return rect

    def _single_object_unit(self) -> int | None:
        """The index when exactly one unit of exactly one object is selected."""
        if len(self._units) == 1 and len(self._units[0]) == 1:
            return self._units[0][0]
        return None

    def _extent_rect(self, idx: int) -> QtCore.QRectF | None:
        """The object's Extent (rect1, what a resize rewrites) in scene
        coordinates, following a live drag; the item bounds when the model
        has none. The item bounds include the pen, so a resize mapped from
        them would grow the object on every drag."""
        sc = self.scene()
        page = getattr(sc, "_page", None)
        if page is None or not (0 <= idx < len(page.objects)):
            return self._index_rect(idx)
        g = page.objects[idx].geometry
        if g is None or (g.extent_x <= 1 and g.extent_y <= 1):
            # a point Extent (a V3/V4 point class, a keyboard command): its
            # editor icon is what can be grabbed -- handles on the point
            # itself would turn every click on it into a resize
            return self._index_rect(idx)
        r = QtCore.QRectF(g.x, g.y, g.extent_x, g.extent_y)
        off = self._live_shift(idx)
        if off is not None:
            r.translate(off)
        return r

    def _resize_target(self):
        """(root index, rect) of the ONE selected unit that can be resized --
        a single object (its Extent), or a whole group / genie (the window
        scales its subtree the way the engine does); (None, None) otherwise."""
        if len(self._units) != 1:
            return None, None
        unit = self._units[0]
        if len(unit) == 1:
            return unit[0], self._extent_rect(unit[0])
        return min(unit), self._unit_rect(unit)

    _SHAPE_ITEM_TYPES = (
        QtWidgets.QGraphicsPathItem,
        QtWidgets.QGraphicsPolygonItem,
        QtWidgets.QGraphicsEllipseItem,
        QtWidgets.QGraphicsLineItem,
    )

    def _item_scene_shape(self, item) -> QtGui.QPainterPath:
        """Scene-space selection shape: the true outline for node/curve items
        (pipe along the pipe, polygon as the polygon), the bbox otherwise --
        mirrors the hover/selection highlight so marquee hit-testing matches."""
        if isinstance(item, self._SHAPE_ITEM_TYPES):
            return item.mapToScene(item.shape())
        p = QtGui.QPainterPath()
        p.addRect(item.sceneBoundingRect())
        return p

    def _unit_outline(self, unit: list[int]) -> QtGui.QPainterPath | None:
        """The selection/hover outline: the actual ITEM SHAPE for a single node/curve
        object (pipe as a pipe, ellipse as an ellipse, line along the line), the united
        bbox otherwise."""
        sc = self.scene()
        if len(unit) == 1 and sc is not None and hasattr(sc, "_items"):
            item = sc._items.get(unit[0])
            if isinstance(item, self._SHAPE_ITEM_TYPES):
                path = self._shape_outline(sc, unit[0], item)
                if not path.isEmpty():
                    return path
        r = self._unit_rect(unit)
        if r is None:
            return None
        p = QtGui.QPainterPath()
        p.addRect(r)
        return p

    def _update_outlines(self, *units) -> None:
        """Repaint where the outlines of ``units`` are drawn (the hover outline that
        went and the one that came), not the whole canvas. The outline's pen is about
        a pixel wide; the margin covers it and its antialiasing."""
        box = QtCore.QRect()
        for unit in units:
            outline = self._unit_outline(unit) if unit else None
            if outline is not None:
                r = self.mapFromScene(outline.boundingRect()).boundingRect()
                box = box.united(r.adjusted(-4, -4, 4, 4))
        if not box.isEmpty():
            self.viewport().update(box)

    def _shape_outline(self, sc, index: int, item) -> QtGui.QPainterPath:
        """The scene-space shape of a node / curve item, cached per scene build like
        the units' bounds (a pipe's stroked shape is dear) and following a drag's or a
        burst's offset."""
        serial = getattr(sc, "build_serial", None)
        if serial is None:
            return item.mapToScene(item.shape())
        key = ("shape", id(sc), serial, index)
        off = self._live_shift(index)
        cache = self._urect_store(sc, serial)
        base = cache.get(key)
        if base is None:
            path = item.mapToScene(item.shape())
            base = path.translated(-off) if off is not None else path
            cache[key] = base
        return base.translated(off) if off is not None else QtGui.QPainterPath(base)

    def _small_axes(self, r: QtCore.QRectF) -> tuple:
        """(narrow, short): box ``r`` is narrower / shorter than three handles on screen
        (an axis without a size, a flat line's, is neither: its handles sit at its ends)."""
        z = max(self.zoom(), 0.01)
        limit = 3.0 * handle_size()
        return (r.width() >= 1 and r.width() * z < limit, r.height() >= 1 and r.height() * z < limit)

    def _handle_specs(self, r: QtCore.QRectF):
        """(id, x, y, size) of the sizing handles for box ``r``."""
        z = max(self.zoom(), 0.01)
        hs = handle_size()
        narrow, short = self._small_axes(r)
        out = (4.0 + hs / 2.0) / z
        for hid, (fx, fy) in _live_handles(r):
            hx = r.left() + fx * r.width()
            hy = r.top() + fy * r.height()
            if self._near_node(hx, hy):
                continue
            corner = fx != 0.5 and fy != 0.5
            dx = -1.0 if fx == 0.0 else (1.0 if fx == 1.0 else 0.0)
            dy = -1.0 if fy == 0.0 else (1.0 if fy == 1.0 else 0.0)
            if narrow and short:
                if not corner:
                    continue
                hx, hy = hx + dx * out, hy + dy * out
            elif narrow:
                if fx == 0.5:
                    continue  # (the top / bottom middle handles go)
                if fy == 0.5:
                    hx += dx * out
            elif short:
                if fy == 0.5:
                    continue
                if fx == 0.5:
                    hy += dy * out
            yield hid, hx, hy, (hs if corner else max(4.0, hs - 2.0))

    def _resize_handles(self, r: QtCore.QRectF):
        """(id, x, y) of the sizing handles shown on box ``r`` (:meth:`_handle_specs`)."""
        for hid, hx, hy, _size in self._handle_specs(r):
            yield hid, hx, hy

    def _handle_box(self):
        """(root index or -1 for a multi-selection, box) whose handles show, or (None, None)."""
        idx, r = self._resize_target()
        if idx is not None and r is not None:
            return idx, r
        box = self._multi_box()
        if box is not None:
            return -1, box
        return None, None

    def _handle_at(self, viewpos: QtCore.QPoint):
        """(handle id, root index) of the handle a press grabs."""
        idx, r = self._handle_box()
        if idx is None:
            return None, None
        sp = self.mapToScene(viewpos)
        z = max(self.zoom(), 0.01)
        narrow, short = self._small_axes(r)
        inside = (
            r.width() >= 1
            and r.height() >= 1
            and r.left() < sp.x() < r.right()
            and r.top() < sp.y() < r.bottom()
        )
        inner = r.adjusted(r.width() / 4.0, r.height() / 4.0, -r.width() / 4.0, -r.height() / 4.0)
        if inside and inner.contains(sp):
            return None, None
        best = None
        extra = pointer_reach()
        for hid, hx, hy, size in self._handle_specs(r):
            d = max(abs(sp.x() - hx), abs(sp.y() - hy)) * z
            if inside:
                lim = 2.0 if (narrow or short) else size / 2.0 + 1.0 + extra
            else:
                lim = max(5.0, size / 2.0 + 1.0) + extra
            if d <= lim and (best is None or d < best[0]):
                best = (d, hid)
        if best is None:
            return None, None
        return best[1], idx

    # -- a multi-selection's box ------------------------------------------------------
    def _multi_box(self) -> QtCore.QRectF | None:
        """The box of two or more selected units (view/selection_box), following a move
        drag or an arrow-key burst: what Adjust Position and Size would change (the
        window's source), else the units' extents."""
        if not self.SELECTION_BOX or len(self._units) < 2 or self._tool != "select":
            return None
        sc = self.scene()
        key = (self._units_gen, id(sc), getattr(sc, "build_serial", None))
        memo = self._multi_memo
        if memo is not None and memo[0] == key:
            base = memo[1]
        else:
            base = None
            fn = self._multi_box_source
            if fn is not None:
                try:
                    b = fn(self.selection_roots())
                except Exception:  # noqa: BLE001 -- the box is a view aid
                    from ...infra.logsetup import note_exception

                    note_exception("ui.canvas._multi_box")
                    b = None
                if b is not None and b[2] - b[0] >= 0 and b[3] - b[1] >= 0:
                    base = QtCore.QRectF(b[0], b[1], b[2] - b[0], b[3] - b[1])
            else:
                base = self._model_box(self._selection)
            self._multi_memo = (key, base)
        if base is None:
            return None
        off = None
        if self._moving:
            off = self._move_applied
        elif self._nudge is not None and self._sel_set <= self._nudge["set"]:
            off = self._nudge["off"]
        return base.translated(off) if off is not None else QtCore.QRectF(base)

    # -- the rotation grip ------------------------------------------------------------
    def _grip_point(self) -> QtCore.QPointF | None:
        """The rotation grip's scene point: GRIP_GAP_PX above the top-middle handle of the
        selection's box (one unit or several), or None."""
        if (
            self._tool != "select"
            or self._moving
            or self._resizing is not None
            or self._node_drag is not None
        ):
            return None
        idx, r = self._handle_box()
        if idx is None:
            return None
        z = max(self.zoom(), 0.01)
        _narrow, short = self._small_axes(r)
        top = r.top() - (4.0 + handle_size() / 2.0) / z if short else r.top()
        return QtCore.QPointF(r.center().x(), top - GRIP_GAP_PX / z)

    def _grip_at(self, vp: QtCore.QPoint) -> bool:
        """The pointer is on the rotation grip (or just outside a corner of the box, where
        the pointer shows the curved arrow)."""
        p = self._grip_point()
        if p is None:
            return False
        c = self.mapFromScene(p)
        reach = 7.0 + pointer_reach()
        if abs(c.x() - vp.x()) <= reach and abs(c.y() - vp.y()) <= reach:
            return True
        return self._corner_zone(vp)

    def _corner_zone(self, vp: QtCore.QPoint) -> bool:
        """``vp`` lies just outside a corner of the selection's box, past its handle."""
        idx, r = self._handle_box()
        if idx is None or r.width() < 1 or r.height() < 1:
            return False
        box = self.mapFromScene(r).boundingRect()
        x, y = vp.x(), vp.y()
        extra = pointer_reach()
        reach = handle_size() / 2.0 + 5.0 + extra  # (past the handle's own reach)
        for cx, cy, sx, sy in (
            (box.left(), box.top(), -1, -1),
            (box.right(), box.top(), 1, -1),
            (box.left(), box.bottom(), -1, 1),
            (box.right(), box.bottom(), 1, 1),
        ):
            ox, oy = (x - cx) * sx, (y - cy) * sy  # how far outside, per axis
            if ox >= 0 and oy >= 0 and max(ox, oy) > reach and max(ox, oy) <= reach + ROTATE_ZONE_PX + extra:
                # (over another object a press selects that object)
                roots = self.selection_roots()
                return not any(min(u) not in roots for u in self.units_under(vp))
        return False

    def _begin_rotate(self, vp: QtCore.QPoint) -> None:
        idx, r = self._handle_box()
        c = self.mapFromScene(r.center())
        self._rotating = {
            "centre": QtCore.QPointF(r.center()),
            "box": QtCore.QRectF(r),
            "a0": math.atan2(vp.y() - c.y(), vp.x() - c.x()),
            "steps": 0,
        }
        self._hover_unit = []
        self.viewport().setCursor(_rotate_cursor())
        self.viewport().update()

    def _rotate_to(self, vp: QtCore.QPoint) -> None:
        d = self._rotating
        c = self.mapFromScene(d["centre"])
        a = math.atan2(vp.y() - c.y(), vp.x() - c.x())
        turn = math.degrees(a - d["a0"])
        turn = (turn + 180.0) % 360.0 - 180.0  # (-180, 180]: on screen, + = clockwise
        d["steps"] = int(round(turn / 90.0)) % 4
        self._pill = {0: "No turn", 1: "90 deg clockwise", 2: "180 deg", 3: "90 deg anticlockwise"}[
            d["steps"]
        ]
        self._last_vp = QtCore.QPoint(vp)
        self.viewport().update()

    def _end_rotate(self) -> None:
        d, self._rotating = self._rotating, None
        self._pill = None
        self.viewport().setCursor(self._idle_cursor())
        self.viewport().update()
        if d is not None and d["steps"]:
            self.rotateRequested.emit(d["steps"])

    def rotating(self) -> bool:
        return self._rotating is not None

    # -- guidelines on the canvas (help 1172087) ---------------------------------------
    def _guide_at(self, viewpos: QtCore.QPoint):
        """('v' | 'h', position) of the displayed guideline under the pointer,
        or None ('v' = a vertical guideline at x = position)."""
        if not self._guides_visible or not (self._vguides or self._hguides):
            return None
        sp = self.mapToScene(viewpos)
        tol = guide_reach() / max(self.zoom(), 0.01)
        best = None
        for axis, lines, v in (("v", self._vguides, sp.x()), ("h", self._hguides, sp.y())):
            for g in lines:
                d = abs(v - g)
                if d <= tol and (best is None or d < best[0]):
                    best = (d, axis, g)
        return None if best is None else (best[1], best[2])

    def _topmost_unit(self, viewpos: QtCore.QPoint) -> list[int]:
        """The topmost SELECTABLE unit under the pointer ([] = none): what a
        plain click there selects."""
        for h in self._hits_under(viewpos):
            u = self._resolve_unit(h)
            if u:
                return u
        return []

    def _guide_grab(self, viewpos: QtCore.QPoint, mods):
        """The guideline a left press grabs, or None."""
        if mods & QtCore.Qt.AltModifier:
            return None
        g = self._guide_at(viewpos)
        if g is None:
            return None
        if not mods & (QtCore.Qt.ShiftModifier | _CTRL):
            unit = self._topmost_unit(viewpos)
            if unit and min(unit) in self.selection_roots():
                return None
        return g

    def _guide_deletes(self, axis: str, pos: float) -> bool:
        """A guideline dropped at ``pos`` is deleted: within 8 px of the page
        edge or off the page (CtDraw: position < 9 or >= page
        size - 8). A library element's window has no page edge: there it is
        deleted when dropped outside the visible part of the view."""
        sc = self.scene()
        if getattr(sc, "_element_mode", False):
            seen = self.mapToScene(self.viewport().rect()).boundingRect()
            lo, hi = (seen.left(), seen.right()) if axis == "v" else (seen.top(), seen.bottom())
            return not lo <= pos <= hi
        size = int(getattr(sc, "_page_w" if axis == "v" else "_page_h", 0) or 0)
        e = self.GUIDE_DELETE_EDGE
        return pos <= e or (size > 0 and pos >= size - e)

    def _begin_guide_drag(self, axis: str, pos: int, vp: QtCore.QPoint, mods) -> None:
        """Press on a guideline: it follows the pointer once dragged; Ctrl
        held when the button is RELEASED makes a new one and leaves this one
        (CtDraw's button-up tests MK_CONTROL, with a free slot)."""
        self._guide_drag = {
            "axis": axis,
            "orig": int(pos),
            "pos": float(pos),
            "copy": bool(mods & _CTRL),
            "moved": False,
            "press": QtCore.QPoint(vp),
            "mods": mods,
        }
        self._hover_unit = []
        self.viewport().setCursor(QtCore.Qt.SplitHCursor if axis == "v" else QtCore.Qt.SplitVCursor)
        self.viewport().update()

    def _drag_guide_to(self, sp: QtCore.QPointF, vp: QtCore.QPoint, mods) -> None:
        d = self._guide_drag
        if not d["moved"]:
            if (vp - d["press"]).manhattanLength() < drag_start():
                return
            d["moved"] = True
        d["copy"] = True if d.get("ruler") else bool(mods & _CTRL)
        axis = 0 if d["axis"] == "v" else 1
        v = sp.x() if axis == 0 else sp.y()
        # the dragged guideline lands on the grid when Snap to Grid is on
        d["pos"] = self._grid_value(v, axis) if (self._snap and self._pitch()[axis] > 1) else float(round(v))
        self._pill = self._guide_pill(d)
        self._last_vp = vp
        self.viewport().update()

    def _guide_pill(self, d: dict) -> str:
        """What the dragged guideline's release will do, for the pointer chip."""
        if self._guide_deletes(d["axis"], d["pos"]):
            return (
                "Release to delete the guideline"
                if not d["copy"]
                else "Too close to the page edge for a guideline"
            )
        return "%s = %d%s" % (
            "x" if d["axis"] == "v" else "y",
            d["pos"],
            "  (new guideline)" if d["copy"] else "",
        )

    def _end_guide_drag(self, vp: QtCore.QPoint, mods) -> None:
        """Drop the dragged guideline: moved, copied (Ctrl), or deleted at the
        page edge; ``guidelinesChanged`` tells the window (it stores them). A
        click without a drag is a click on what lies under the guideline."""
        d, self._guide_drag = self._guide_drag, None
        self._pill = None
        self.viewport().setCursor(self._idle_cursor())
        self.viewport().update()
        if d is None:
            return
        if not d["moved"]:
            self._click_select(vp, d["mods"])
            return
        axis, orig, pos = d["axis"], d["orig"], int(d["pos"])
        copy = True if d.get("ruler") else bool(mods & _CTRL)
        lines = self._vguides if axis == "v" else self._hguides
        name = "vertical" if axis == "v" else "horizontal"
        coord = "x" if axis == "v" else "y"
        if self._guide_deletes(axis, pos):
            if copy or orig not in lines:
                return  # a copy dropped at the edge: nothing made
            lines.remove(orig)
            msg = "Deleted the %s guideline at %s = %d" % (name, coord, orig)
        elif pos == orig or (copy and pos in lines):
            return  # dropped where it was: no change
        elif not copy:
            if orig in lines:
                lines.remove(orig)
            if pos not in lines:
                lines.append(pos)
                lines.sort()
            msg = "Moved the %s guideline to %s = %d" % (name, coord, pos)
        else:
            lines.append(pos)
            lines.sort()
            msg = (
                ("Guideline added at %s = %d" % (coord, pos))
                if d.get("ruler")
                else "Added a %s guideline at %s = %d" % (name, coord, pos)
            )
        self.viewport().update()
        self.guidelinesChanged.emit(msg)

    def _click_select(self, vp: QtCore.QPoint, mods) -> None:
        """A plain click (no drag) at ``vp``: select the topmost selectable
        unit there (Shift / Ctrl adds or removes it), or clear the selection
        on empty page -- for a click on a guideline that did not drag it."""
        unit = self._topmost_unit(vp)
        if mods & (QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier):
            if unit:
                self._toggle_unit(unit)
        elif unit:
            if min(unit) not in self.selection_roots():
                self._set_units([unit])
        elif self._units:
            self._set_units([])

    # -- events --------------------------------------------------------------
    def viewportEvent(self, e: QtCore.QEvent) -> bool:
        t = e.type()
        if t == QtCore.QEvent.Leave and self._cross_pos is not None:
            # off the drawing area the normal pointer is back (help 1172089)
            self._cross_hair_to(None)
        elif t == QtCore.QEvent.Gesture and self._touch_on:
            if self._gesture_event(e):
                return True
        elif t in (QtCore.QEvent.TouchBegin, QtCore.QEvent.TabletPress):
            self._press_touch = True  # (a long press may open the menu)
        elif t == QtCore.QEvent.ChildAdded and "_overlay_watch" in self.__dict__:
            self._overlay_watch.watch(e.child())
        if (
            t == QtCore.QEvent.TabletPress
            and self._touch_on
            and self._tool == "select"
            and e.button() in (QtCore.Qt.RightButton, QtCore.Qt.MiddleButton)
        ):
            # a pen's barrel button is the right mouse button: the context menu
            self._context_at(e.position().toPoint(), e.globalPosition().toPoint())
            e.accept()
            return True
        return super().viewportEvent(e)

    def focusInEvent(self, e: QtGui.QFocusEvent) -> None:
        super().focusInEvent(e)
        if e.reason() in (
            QtCore.Qt.TabFocusReason,
            QtCore.Qt.BacktabFocusReason,
            QtCore.Qt.ShortcutFocusReason,
        ):
            self._focus_visible = True  # (reached with the keyboard: show where)
        elif e.reason() == QtCore.Qt.MouseFocusReason:
            self._focus_visible = False
        self.viewport().update()

    def focusOutEvent(self, e: QtGui.QFocusEvent) -> None:
        super().focusOutEvent(e)
        self.viewport().update()

    def keyboard_focus_shown(self) -> bool:
        """The canvas draws its keyboard focus frame: it has the focus, reached or used with
        the keyboard (or any way with Settings > Appearance > Show the keyboard focus
        'Always')."""
        if not self.hasFocus():
            return False
        if self._focus_visible:
            return True
        from ..style.theme import focus_ring

        return focus_ring() == "always"

    # (the wheel, a touchpad and touch: ui/editor/viewnav.CanvasNavigation.wheelEvent)

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if self._inplace is not None:
            # the scene hands the key to the focused editor item
            super().keyPressEvent(e)
            return
        if self._tool == "text" and _typing_key(e) and e.key() != QtCore.Qt.Key_Space:
            # CtDraw's Text tool: type first -- the text rides the cursor --
            # then click to place it (help 1172129)
            at = self._hover_scene
            if at is None:
                at = self.mapToScene(self.viewport().rect().center())
            self._begin_placement(self._snap_pt(at), e.text(), follow=True)
            e.accept()
            return
        if e.key() in _DRAW_MOD_KEYS:
            self._update_draw_mods(e.modifiers())
        elif e.key() not in _MODIFIER_KEYS and not self._focus_visible:
            self._focus_visible = True  # keyboard use: the focus frame shows
            self.viewport().update()
        if e.key() == QtCore.Qt.Key_Space and not e.isAutoRepeat() and not self._space_pan:
            # Space-hold = temporary pan
            self.begin_space_pan()
            e.accept()
            return
        if self._zoom_hold_key(e):
            if not e.isAutoRepeat() and self._zhold is None and not self._nodes:
                # Z: arm the zoom box; held longer than a moment, the tool before comes
                # back when it is let go (like Space for the pan)
                self._zhold = {"t": time.monotonic(), "prev": self._tool}
                if self._tool != "zoom":
                    self.toolArmRequested.emit("zoom")
            e.accept()
            return
        if self._tool in _NODE_TOOLS and self._nodes:
            if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                self._commit_nodes()
                return
            if e.key() == QtCore.Qt.Key_Backspace:
                # the last point goes (the one following the pointer stays)
                if len(self._nodes) > 1:
                    self._nodes.pop()
                else:
                    self._cancel_draw()
                self.viewport().update()
                e.accept()
                return
            if e.key() == QtCore.Qt.Key_Escape:
                # Esc cancels, as the help and the shortcut list say; Enter or a
                # double-click finishes it, Backspace takes the last point back.
                # Settings > Editing > "Esc while drawing point by point" can make it
                # keep the shape, as Enter does
                if self.ESC_FINISHES:
                    self._commit_nodes()
                    e.accept()
                    return
                kept = len(self._nodes) >= (3 if self._tool == "polygon" else 2)
                self._cancel_draw()
                if kept:
                    self.statusMessage.emit(
                        "Drawing cancelled. Enter or a double-click finishes a shape; "
                        "Backspace takes the last point back."
                    )
                return
        if (
            self._tool == "select"
            and e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)
            and not e.modifiers() & _ALT
            and self._node_drag is None
            and self._units
        ):
            # Enter: what a double-click does (the genie's parameters, else the properties);
            # Shift+Enter selects the group or genie it is in; Ctrl+Enter its first member
            if e.modifiers() & _SHIFT:
                self.select_parent()
            elif e.modifiers() & _CTRL:
                self.select_first_member()
            else:
                root = self.key_root()
                if root is not None:
                    self.objectActivated.emit(root)
            e.accept()
            return
        if (
            self._tool == "select"
            and e.key() in (QtCore.Qt.Key_Insert, QtCore.Qt.Key_Plus)
            and not e.modifiers() & (_CTRL | _ALT)
            and self._node_drag is None
        ):
            # CtDraw's node insert: Insert, '+' or keypad '+' (the canvas
            # WM_KEYDOWN VK_INSERT / VK_ADD, WM_CHAR '+'; help 1172187)
            if self.insert_node_at_cursor():
                e.accept()
                return
        if e.key() == QtCore.Qt.Key_Escape:
            # Esc ladder: a running drag (move / resize / selection box / node / zoom
            # box) -> a guideline drag -> cancel draw -> revert to Select -> selected
            # nodes -> clear selection
            if self.cancel_drag():
                e.accept()
                return
            if self._guide_drag is not None:
                self._guide_drag = None  # the guideline stays where it was
                self._pill = None
                self.viewport().setCursor(self._idle_cursor())
                self.viewport().update()
            elif self._draw_start is not None or self._freehand:
                self._cancel_draw()
            elif self._tool != "select":
                self._cancel_draw()
                self.toolResetRequested.emit()
            elif self._node_sel:
                self.clear_node_selection()
            else:
                self._set_units([])
            return
        super().keyPressEvent(e)

    def _update_draw_mods(self, mods) -> None:
        """Ctrl / Shift pressed or released mid-draw: the preview follows."""
        gd = self._guide_drag
        if gd is not None and gd["moved"] and not gd.get("ruler") and gd["copy"] != bool(mods & _CTRL):
            # a dragged guideline: Ctrl now = a new one (the original shows)
            gd["copy"] = bool(mods & _CTRL)
            self._pill = self._guide_pill(gd)
            self.viewport().update()
        if self._draw_start is not None or self._nodes:
            self._draw_mods = mods
            if self._nodes and self._draw_cur is not None and self._hover_scene is not None:
                self._draw_cur = self._node_point(self._hover_scene, mods, snap=False)
            self.viewport().update()
        if self._moving:
            # a move: the constrain key holds it on one axis, the copy key shows '+'
            self._move_drag_to(self._move_last, self._last_vp, mods)
        elif self._resizing is not None and self._hover_scene is not None:
            self._resize_rect = self._resize_to(self._hover_scene, mods)
            self._pill = self._resize_pill(self._resize_rect.normalized())
            self.viewport().update()

    def keyReleaseEvent(self, e: QtGui.QKeyEvent) -> None:
        if self._inplace is not None:
            super().keyReleaseEvent(e)
            return
        if e.key() in _DRAW_MOD_KEYS:
            self._update_draw_mods(e.modifiers())
        if e.key() == QtCore.Qt.Key_Space and not e.isAutoRepeat() and self._space_pan:
            self.end_space_pan()
            e.accept()
            return
        if self._zoom_hold_key(e):
            if not e.isAutoRepeat():
                h, self._zhold = self._zhold, None
                if h is not None and h["prev"] != "zoom" and time.monotonic() - h["t"] > self.ZOOM_HOLD_S:
                    if self._zbox is not None:
                        self._zhold_return = h["prev"]  # after the box is let go
                    else:
                        self.toolRequested.emit(h["prev"])
                elif h is not None and h["prev"] != "zoom":
                    self.toolRequested.emit("zoom")  # a tap: the Zoom Box tool
            e.accept()
            return
        super().keyReleaseEvent(e)

    #: held longer than this, Z arms the zoom box only while it is held
    ZOOM_HOLD_S = 0.2

    def _zoom_hold_key(self, e) -> bool:
        """Z with no modifier (not while text is typed: the Text tool takes it)."""
        return (
            e.key() == QtCore.Qt.Key_Z
            and not (e.modifiers() & (_CTRL | _ALT | QtCore.Qt.ShiftModifier))
            and self._inplace is None
            and self._tool != "text"
        )

    def cancel_drag(self) -> bool:
        """Esc during a drag: a move or a resize goes back where it started (the selection
        stays, the coming release commits nothing), a selection box goes and the selection
        before it comes back, a node drag or a zoom box goes. False when no drag runs."""
        if self._moving:
            items, self._drag_items = self._drag_items, []
            self._move_items(items, -self._move_applied.x(), -self._move_applied.y())
            self._moving = False
            self._maybe_drag = False
            self._mv_box = None
            self._move_applied = QtCore.QPointF()
            self._drop_unit_rects()
            self._drop_copy_ghost()
            msg = "Move cancelled."
        elif self._resizing is not None:
            self._resizing = None
            self._resize_index = None
            self._resize_multi = False
            msg = "Resize cancelled."
        elif self._rotating is not None:
            self._rotating = None
            msg = "Rotation cancelled."
        elif self._marquee is not None:
            self._marquee = None
            self._box_eligible = None
            self._box_timer.stop()
            self._box_preview = []
            before, self._marquee_before = self._marquee_before, None
            if before is not None and before != self._units:
                self._set_units(before)
            msg = ""
        elif self._node_drag is not None and self._node_drag.get("moved"):
            self._node_drag = None
            msg = "Node move cancelled."
        elif self._zbox is not None:
            self._zbox = None
            self._zoom_box_return()
            msg = ""
        else:
            return False
        self._sg_active = []
        self._constrain = None
        self._pending_click = None
        self._alt_press = None
        self._pill = None
        self._edge_timer.stop()
        self._swallow_release = True
        self.viewport().setCursor(self._idle_cursor())
        self.viewport().update()
        if msg:
            self.statusMessage.emit(msg)
        return True

    def _hotspot_at(self, vp: QtCore.QPoint) -> bool:
        """The element window's hot spot marker is under viewport point ``vp``
        (CtDraw: within 2 px of it at 1:1; here a constant few screen px --
        the marker keeps its size on screen)."""
        sc = self.scene()
        hs = getattr(sc, "_element_hotspot", None) if sc is not None else None
        if hs is None or self._tool != "select":
            return False
        p = self.mapFromScene(QtCore.QPointF(float(hs[0]), float(hs[1])))
        reach = self.HOTSPOT_GRAB_PX + pointer_reach()
        return abs(p.x() - vp.x()) <= reach and abs(p.y() - vp.y()) <= reach

    def _end_hotspot_drag(self, commit: bool) -> None:
        """Finish a hot spot drag: report the new point (hotspotMoved) or put
        the marker back."""
        start, self._hotspot_drag = self._hotspot_drag, None
        self._pill = None
        sc = self.scene()
        cur = tuple(getattr(sc, "_element_hotspot", None) or start or ())
        if not commit or not cur or cur == tuple(start):
            if sc is not None and start is not None:
                sc._element_hotspot = tuple(start)
                sc.update()
        else:
            self.hotspotMoved.emit(int(cur[0]), int(cur[1]))
        self.viewport().setCursor(self._idle_cursor())
        self.viewport().update()

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        vp = e.position().toPoint()
        self.finish_view_animation()
        self._press_touch = from_touch_or_pen(e)
        left = e.button() == QtCore.Qt.LeftButton
        if left:
            self._left_down = True
            self._press_vp = QtCore.QPoint(vp)
            self._dragged = False
            self._swallow_release = False
            self.hide_key_pill()
            if self._focus_visible:
                self._focus_visible = False  # (a click: the keyboard frame goes)
                self.viewport().update()
        if e.button() in (QtCore.Qt.BackButton, QtCore.Qt.ForwardButton):
            # the mouse's back / forward buttons: Previous View / Next View, or (Settings >
            # Canvas > "Mouse back and forward buttons") the tab before / after, or nothing
            back = e.button() == QtCore.Qt.BackButton
            if self.SIDE_BUTTONS == "tabs":
                self.pageStepRequested.emit(-1 if back else 1)
            elif self.SIDE_BUTTONS != "nothing":
                (self.go_back if back else self.go_forward)()
            e.accept()
            return
        chip = self._page_chip
        if left and chip is not None and chip.contains(QtCore.QPointF(vp)) and self._inplace is None:
            self.fit_page()  # the 'Page' chip: back to the page
            e.accept()
            return
        both = _CTRL | QtCore.Qt.ShiftModifier
        if left and self._tool == "pan" and (e.modifiers() & both) == both:
            self._begin_zoom_box(vp, e.modifiers())  # Ctrl+Shift+drag: a zoom box
            e.accept()
            return
        if (
            e.button() == QtCore.Qt.MiddleButton
            or (self._space_pan and left)
            or (self._tool == "pan" and left)
        ):
            self._auto_fit = False  # panning = manual navigation
            self._note_burst()
            self._panning = True
            self._pan_start = vp
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            e.accept()
            return

        if self._inplace is not None:
            if self._inplace_follow and e.button() == QtCore.Qt.LeftButton:
                # the typed-first text is placed where it is clicked
                self._place_followed(self.mapToScene(vp))
                self.finish_text_edit(True)
            elif self._inplace_contains(vp):
                super().mousePressEvent(e)  # caret / selection in the text
            else:
                self.finish_text_edit(True)  # a click elsewhere commits
            e.accept()
            return

        if self._tool == "zoom":
            if left:
                self._begin_zoom_box(vp, e.modifiers())
            e.accept()
            return

        if (
            left
            and self._tool == "select"
            and (e.modifiers() & both) == both
            and self._handle_at(vp)[0] is None
        ):
            # Ctrl+Shift+drag: a zoom box, also over an object (a click without a drag
            # still adds the object to the selection or takes it out); on a handle it
            # resizes, keeping the proportions about the centre. Settings > Canvas >
            # "Ctrl+Shift + drag" can make it a Touch selection box from anywhere instead
            unit = self._topmost_unit(vp)
            if self.CTRL_SHIFT_DRAG == "touch":
                sp = self.mapToScene(vp)
                self._move_start = self._move_last = sp
                self._maybe_drag = False
                if unit:
                    self._pending_click = {
                        "unit": unit,
                        "op": "toggle",
                        "box": True,
                        "touch": True,
                        "vp": QtCore.QPoint(vp),
                    }
                else:
                    self._begin_marquee(sp, QtCore.Qt.NoModifier, touch=True)
                e.accept()
                return
            self._begin_zoom_box(vp, e.modifiers())
            self._zbox["click_unit"] = unit or None
            e.accept()
            return

        if self._tool == "freehand":
            if e.button() == QtCore.Qt.LeftButton:
                sp = self.mapToScene(vp)
                self._freehand = True
                self._nodes = [QtCore.QPointF(_clamp(round(sp.x())), _clamp(round(sp.y())))]
                self.viewport().update()
            e.accept()
            return

        if self._tool in _NODE_TOOLS:
            if e.button() == QtCore.Qt.LeftButton:
                # a drag from the first vertex adds the second on release
                self._node_press = vp if not self._nodes else None
                self._draw_mods = e.modifiers()
                self._nodes.append(self._node_point(self.mapToScene(vp), e.modifiers()))
                self._draw_cur = self._nodes[-1]
                self.viewport().update()
            elif e.button() == QtCore.Qt.RightButton:
                self._commit_nodes()
            e.accept()
            return

        if self._tool != "select":
            if e.button() == QtCore.Qt.LeftButton:
                self._draw_press = self.mapToScene(vp)
                self._draw_start = self._snap_pt(self._draw_press)
                self._draw_cur = self._draw_start
                self._draw_mods = e.modifiers()
                self.viewport().update()
            e.accept()
            return

        # ---- select tool ----
        if e.button() == QtCore.Qt.RightButton:
            self._context_at(vp, e.globalPosition().toPoint())
            e.accept()
            return
        if e.button() != QtCore.Qt.LeftButton:
            super().mousePressEvent(e)
            return

        k = self._node_at(vp)
        if k is not None and not e.modifiers() & QtCore.Qt.AltModifier:
            # a node of the selected line / pipe / polygon (help 1172187)
            self._begin_node_press(k, e.modifiers(), self.mapToScene(vp))
            e.accept()
            return
        # a click anywhere other than on a node deselects every node
        self.clear_node_selection()

        if self._hotspot_at(vp) and not e.modifiers() & QtCore.Qt.AltModifier:
            # the element window's hot spot marker: drag it (Alt reaches the
            # objects under it)
            self._hotspot_drag = tuple(self.scene()._element_hotspot)
            self.viewport().setCursor(QtCore.Qt.SizeAllCursor)
            e.accept()
            return

        mods = e.modifiers()
        alt = bool(mods & QtCore.Qt.AltModifier)
        if not alt:
            # the rotation grip (and just outside a corner of the box): turn the selection
            if self._grip_at(vp):
                self._begin_rotate(vp)
                e.accept()
                return
            # a sizing handle (Alt skips them: it always reaches the objects)
            hid, hidx = self._handle_at(vp)
            if hid is not None:
                box = self._handle_box()[1]
                self._resizing = hid
                self._resize_multi = hidx == -1
                self._resize_index = None if self._resize_multi else hidx
                self._resize_press = self.mapToScene(vp)
                self._resize_origin = QtCore.QRectF(box)
                self._resize_rect = QtCore.QRectF(box)
                e.accept()
                return

        # a guideline under the pointer: drag it; Ctrl+drag makes a new one
        # (help 1172087). Alt reaches the objects under it, and a plain press
        # on a selected object moves the object (_guide_grab).
        g = self._guide_grab(vp, mods)
        if g is not None:
            self._begin_guide_drag(g[0], g[1], vp, mods)
            e.accept()
            return

        sp = self.mapToScene(vp)
        self._move_start = sp
        self._move_last = sp
        self._pending_click = None
        if alt:
            # Alt: a click selects the next object under the pointer (on the release, so
            # the selection does not change under a drag); a drag on the selection moves
            # it without snapping, a drag elsewhere takes the object on top
            units = self.units_under(vp)
            roots = self.selection_roots()
            on_sel = any(min(u) in roots for u in units)
            self._alt_press = {"units": units, "vp": QtCore.QPoint(vp), "on_sel": on_sel}
            self._maybe_drag = bool(on_sel)
            e.accept()
            return

        shift = bool(mods & QtCore.Qt.ShiftModifier)
        ctrl = bool(mods & QtCore.Qt.ControlModifier)
        # topmost SELECTABLE unit under the cursor -- skip locked/template hits
        # (the page-template genie's invisible anchor covers the whole page, so
        # stopping at the first hit would block both clicking and marquee).
        unit = self._topmost_unit(vp)
        if not unit:  # nothing selectable here -> background drag
            self._begin_marquee(sp, mods)
            e.accept()
            return
        root = min(unit)
        selected = root in self.selection_roots()
        if shift and not ctrl:
            # Shift: a click adds or takes out the object on the release; a drag from an
            # object that is not selected is a box that adds (Inkscape), a drag on the
            # selection moves it
            self._pending_click = {
                "unit": unit,
                "op": "remove" if selected else "add",
                "box": not selected,
                "vp": QtCore.QPoint(vp),
            }
            self._maybe_drag = selected
        elif ctrl:
            # Ctrl: the object is added at once (a drag then moves or copies the grown
            # selection); a selected one is taken out on the release, unless it is dragged
            if selected:
                self._pending_click = {"unit": unit, "op": "remove", "box": False, "vp": QtCore.QPoint(vp)}
            else:
                self._add_unit(unit)
            self._maybe_drag = True
        elif not selected:
            if self.BOX_FROM_BACKGROUND and self._covers_view(unit):
                # a large background object: a drag boxes what is on it, a click selects it
                self._pending_click = {"unit": unit, "op": "select", "box": True, "vp": QtCore.QPoint(vp)}
                self._maybe_drag = False
            else:
                self._set_units([unit])
                self._key_root = root
                self._maybe_drag = True
        else:
            if self.CLICK_SELECTED_ISOLATES and len(self._units) > 1:
                self._pending_click = {"unit": unit, "op": "isolate", "box": False, "vp": QtCore.QPoint(vp)}
            # arm a move drag of the whole selection
            self._maybe_drag = True
        e.accept()

    def units_under(self, vp: QtCore.QPoint) -> list:
        """The selectable units under ``vp``, topmost first, each once."""
        units, seen = [], set()
        for h in self._hits_under(vp):
            u = self._resolve_unit(h)
            if u and min(u) not in seen:
                seen.add(min(u))
                units.append(u)
        return units

    def _covers_view(self, unit: list) -> bool:
        """``unit`` covers more than a quarter of the canvas's view."""
        r = self._unit_rect(unit)
        if r is None or r.isNull():
            return False
        seen = self.mapToScene(self.viewport().rect()).boundingRect()
        common = r.intersected(seen)
        area = seen.width() * seen.height()
        return area > 0 and common.width() * common.height() > 0.25 * area

    def _begin_marquee(self, sp: QtCore.QPointF, mods, touch: bool = False) -> None:
        """A selection box from ``sp``: Shift adds what it takes, Ctrl toggles it, else it
        replaces the selection (cleared at once; Esc gives it back). ``touch``: a Touch box
        whatever the ribbon's Box list says (Ctrl+Shift + drag by Settings)."""
        self._marquee_before = [list(u) for u in self._units]
        self._marquee_mods = mods & (QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier)
        if not self._marquee_mods and self._units:
            self._set_units([])
        self._marquee_origin = QtCore.QPointF(sp)
        self._marquee = QtCore.QRectF(self._marquee_origin, self._marquee_origin)
        # (per box drag: what each item resolves to for the box; nothing it depends on, the
        # locks, the filter, the template, changes while the box is dragged)
        self._box_eligible = {}
        self._marquee_force_touch = bool(touch)
        self._marquee_touch = touch or (self._marquee_mode == "touch")
        self._box_preview = []
        self._maybe_drag = False

    def _alt_cycle(self, vp: QtCore.QPoint) -> None:
        """An Alt+click: the next object under the pointer (wrapping), with a pill saying
        which of how many it is, and its outline over everything for a moment."""
        press, self._alt_press = self._alt_press, None
        units = press["units"] if press else []
        if not units:
            return
        roots = self.selection_roots()
        pos = next((k for k, u in enumerate(units) if min(u) in roots), -1)
        k = (pos + 1) % len(units)
        nxt = units[k]
        self._set_units([nxt])
        self._key_root = min(nxt)
        self.flash_pill("%d of %d: %s" % (k + 1, len(units), self.unit_label(min(nxt))), vp)
        self._flash_outline(nxt)

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:
        vp = e.position().toPoint()
        sp = self.mapToScene(vp)
        self.hoverPos.emit(int(sp.x()), int(sp.y()))
        self._hover_scene = sp
        if self._crosshair:
            # the cross hairs follow the pointer in every state (drawing,
            # dragging, after a drag)
            self._cross_hair_to(sp)
        # a drag near the canvas edge scrolls it (viewnav: the edge scroll)
        self._edge_check(vp, e.modifiers(), e.buttons())

        if self._inplace is not None and not self._panning:
            if self._inplace_follow:
                self._place_followed(sp)  # the typed text rides the cursor
                e.accept()
            else:
                super().mouseMoveEvent(e)  # drag-select inside the text
            return

        if self._panning:
            delta = vp - self._pan_start
            self._pan_start = vp
            self._note_burst()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            e.accept()
            return

        if self._zbox is not None:
            if e.buttons() & QtCore.Qt.LeftButton:
                self._zbox["cur"] = QtCore.QPoint(vp)
                self.viewport().update()
            else:
                self._zbox = None  # released outside the canvas
                self._zoom_box_return()
                self.viewport().update()
            e.accept()
            return

        if e.buttons() == QtCore.Qt.NoButton:
            shape = canvas_hooks.pointer_mode(self)
            if shape is not None:
                # another module has the pointer (the underlay's Move with the Mouse): its
                # pointer, no hover outline or handle; the readout and the rulers went on above
                if self._hover_unit:
                    old, self._hover_unit = self._hover_unit, []
                    self._update_outlines(old, [])
                    self.hoverChanged.emit(None)
                if self._hover_handle is not None:
                    self._hover_handle = None
                    self.viewport().update()
                self.viewport().setCursor(shape)
                e.accept()
                return

        if self._tool == "freehand":
            if self._freehand and self._nodes:
                if (sp - self._nodes[-1]).manhattanLength() >= 3.0 / max(self.zoom(), 0.01):
                    self._nodes.append(QtCore.QPointF(_clamp(round(sp.x())), _clamp(round(sp.y()))))
                self._draw_cur = sp
                self.viewport().update()
            return

        if self._tool in _NODE_TOOLS:
            self._draw_mods = e.modifiers()
            self._draw_cur = self._node_point(sp, e.modifiers(), snap=False)
            self.viewport().update()
            return

        if self._tool == "measure":
            # the readout follows the drag; after the release the measurement stays
            # until the next press, Esc or another tool
            if self._draw_start is not None and (e.buttons() & QtCore.Qt.LeftButton):
                self._draw_cur = self._snap_pt(self._gear(self._draw_press, sp))
                self._pill = self._measure_pill()
                self._last_vp = vp
                self.viewport().update()
            return

        if self._tool != "select":
            # a Cicode object is placed at the press (a click tool): no box
            if self._draw_start is not None and self._tool != "cicode_object":
                self._draw_cur = self._gear(self._draw_press, sp)
                self._draw_mods = e.modifiers()
                end = self._snap_pt(self._draw_cur)
                if self._tool == "line":
                    import math

                    v = self._line_end(self._draw_start, end, self._draw_mods) - self._draw_start
                    self._pill = "%d px  %d°" % (
                        math.hypot(v.x(), v.y()),
                        math.degrees(math.atan2(-v.y(), v.x())) % 360,
                    )
                else:
                    r = self._box_rect(self._draw_start, end, self._draw_mods)
                    self._pill = "%d × %d" % (r.width(), r.height())
                self._last_vp = vp
                self.viewport().update()
            return

        if self._node_drag is not None:
            if e.buttons() & QtCore.Qt.LeftButton:
                self._drag_nodes_to(sp, vp)
                e.accept()
                return
            # the button went up outside the canvas: the drag is over
            self._node_drag = None
            self._pill = None
            self.viewport().update()

        if self._guide_drag is not None:
            if e.buttons() & QtCore.Qt.LeftButton:
                self._drag_guide_to(sp, vp, e.modifiers())
                e.accept()
                return
            self._guide_drag = None  # released outside the canvas
            self._pill = None
            self.viewport().update()

        if getattr(self, "_hotspot_drag", None) is not None:
            if e.buttons() & QtCore.Qt.LeftButton:
                p = self._snap_pt(sp)
                sc = self.scene()
                sc._element_hotspot = (int(round(p.x())), int(round(p.y())))
                sc.update()
                self._pill = "hot spot %d, %d" % sc._element_hotspot
                self._last_vp = vp
                self.viewport().update()
                e.accept()
                return
            self._end_hotspot_drag(commit=False)  # released outside the canvas

        if self._rotating is not None:
            if e.buttons() & QtCore.Qt.LeftButton:
                self._rotate_to(vp)
            else:
                self._rotating = None  # released outside the canvas
                self._pill = None
                self.viewport().update()
            e.accept()
            return

        if self._resizing is not None:
            self._resize_rect = self._resize_to(sp, e.modifiers())
            self._pill = self._resize_pill(self._resize_rect.normalized())
            self._last_vp = vp
            self.viewport().update()
            e.accept()
            return

        if self._marquee is not None:
            self._marquee = QtCore.QRectF(self._marquee_origin, sp).normalized()
            if self._marquee_mode == "auto" and not self._marquee_force_touch:
                # CAD convention: R-to-L drag = TOUCH (crossing)
                self._marquee_touch = sp.x() < self._marquee_origin.x()
            self._last_vp = vp
            if self.BOX_PREVIEW:
                self._box_timer.start()
            self.viewport().update()
            e.accept()
            return

        held = bool(e.buttons() & QtCore.Qt.LeftButton)
        far = (vp - getattr(self, "_press_vp", vp)).manhattanLength() >= drag_start()
        pend = self._pending_click
        if pend is not None and held and far and pend.get("box"):
            # a Shift+drag from an object (or a drag from a large background object, or a
            # Ctrl+Shift Touch box by Settings): a selection box from the press, the press's
            # object not toggled
            self._pending_click = None
            self._begin_marquee(
                self.mapToScene(pend["vp"]),
                e.modifiers() if pend["op"] == "add" else QtCore.Qt.NoModifier,
                touch=bool(pend.get("touch")),
            )
            self.mouseMoveEvent(e)
            return
        press = self._alt_press
        if press is not None and held and far:
            # an Alt+drag: the selection moves freely; off the selection, the object on top
            self._alt_press = None
            if not press["on_sel"] and press["units"]:
                self._set_units([press["units"][0]])
                self._key_root = min(press["units"][0])
                self._maybe_drag = True
        if self._maybe_drag and self._selection and held:
            if not self._moving:
                if (sp - self._move_start).manhattanLength() < drag_start() / max(self.zoom(), 0.01):
                    return
                self._pending_click = None  # (a drag: the click's toggle goes)
                self._moving = True
                self._move_applied = QtCore.QPointF()
                self._sg_begin_drag()
                self._mv_box = self._model_box(self._selection)
                # the items are looked up once per drag
                self._drag_items = self._items_of(self._selection)
            self._move_drag_to(sp, vp, e.modifiers())
            e.accept()
            return

        # hover pre-selection outline (select tool only, idle)
        locked = None
        if self._tool == "select" and not self._marquee:
            idx, _item = self._hit_index(vp)
            unit = self._resolve_unit(idx) if idx is not None else []
            if unit != self._hover_unit:
                old, self._hover_unit = self._hover_unit, unit
                self._update_outlines(old, unit)
                self.hoverChanged.emit(list(unit) if unit else None)
            if not unit and not held:
                locked = self._locked_under(vp)
        self._note_locked_hover(locked)

        hid, _ = self._handle_at(vp) if self._tool == "select" else (None, None)
        if hid != self._hover_handle:
            self._hover_handle = hid
            self.viewport().update()
        guide = self._guide_grab(vp, e.modifiers()) if self._tool == "select" and not self._moving else None
        if self._tool == "select" and self._node_at(vp) is not None:
            self.viewport().setCursor(QtCore.Qt.CrossCursor)  # 'a small cross'
        elif self._tool == "select" and self._hotspot_at(vp):
            self.viewport().setCursor(QtCore.Qt.SizeAllCursor)  # the hot spot: drag it
        elif self._tool == "select" and not (e.modifiers() & QtCore.Qt.AltModifier) and self._grip_at(vp):
            self.viewport().setCursor(_rotate_cursor())  # the rotation grip
        elif hid is not None:
            self.viewport().setCursor(_HANDLE_CURSORS[hid])
        elif guide is not None:
            # over a guideline: it can be dragged (Ctrl = a new copy)
            self.viewport().setCursor(QtCore.Qt.SplitHCursor if guide[0] == "v" else QtCore.Qt.SplitVCursor)
        elif self._tool == "select" and self._outline_hit(sp) is not None:
            # 'a pointing hand': Insert / '+' adds a node here (help 1172187)
            self.viewport().setCursor(QtCore.Qt.PointingHandCursor)
        elif locked is not None:
            self.viewport().setCursor(_locked_cursor())
        elif not self._moving:
            self.viewport().setCursor(self._idle_cursor())
        super().mouseMoveEvent(e)

    def _move_drag_to(self, sp: QtCore.QPointF, vp: QtCore.QPoint, mods) -> None:
        """A move drag with the pointer at ``sp``: the items follow the snapped
        displacement; the constrain key keeps it on one axis (the line through the press
        shows which); the copy key shows the '+' pointer."""
        raw = sp - self._move_start
        if self._slow_mouse:
            raw = raw * slow_mouse_factor()
        roles = modifier_roles(mods, moving=True)
        axis = None
        if "constrain" in roles:
            axis = "x" if abs(raw.x()) >= abs(raw.y()) else "y"
            raw = QtCore.QPointF(raw.x(), 0.0) if axis == "x" else QtCore.QPointF(0.0, raw.y())
        # the preview shows where the unit lands: grid / guideline / smart
        # guide snapped (the release commits exactly this displacement)
        desired, lines = self._move_snap(raw, mods)
        if axis == "x":
            desired = QtCore.QPointF(desired.x(), 0.0)
            lines = [ln for ln in lines if ln[0] == "v"]
        elif axis == "y":
            desired = QtCore.QPointF(0.0, desired.y())
            lines = [ln for ln in lines if ln[0] == "h"]
        self._sg_active = lines
        self._constrain = (axis, QtCore.QPointF(self._move_start)) if axis else None
        d = desired - self._move_applied
        self._move_items(self._drag_items, d.x(), d.y())
        self._move_applied = desired
        self._move_last = sp
        bb = (self._mv_box if self._mv_box is not None else self._sg_bbox).translated(desired)
        self._pill = "%d, %d  (%s, %s)%s" % (
            bb.left(),
            bb.top(),
            signed(desired.x()),
            signed(desired.y()),
            "  copy" if "copy" in roles else "",
        )
        self._last_vp = vp
        self.viewport().setCursor(QtCore.Qt.DragCopyCursor if "copy" in roles else QtCore.Qt.SizeAllCursor)
        self._show_copy_ghost("copy" in roles)
        self.viewport().update()

    # -- a copy drag leaves the original drawn where it was ---------------------------
    #: the copy ghost's picture is at most this many device pixels (else only the part in view)
    COPY_GHOST_MAX_PX = 16_000_000

    def _show_copy_ghost(self, on: bool) -> None:
        """While the copy key is held in a move drag, a picture of the objects as they were
        where the drag started lies under every object of the page: the original stays in
        view while its copy is dragged away. The key let go hides it; the drag's end drops it
        (:meth:`_drop_copy_ghost`). Made once per drag (_MISS: it could not be made)."""
        g = self.__dict__.get("_copy_ghost")
        if on and g is None:
            g = self._make_copy_ghost()
            g = self._copy_ghost = _MISS if g is None else g
        if g is None or g is _MISS:
            return
        try:
            if g.isVisible() != on:
                g.setVisible(on)
        except RuntimeError:  # swallow-ok: the scene was built again under the drag
            self._copy_ghost = _MISS

    def copy_ghost(self):
        """The picture of the original a copy drag shows (or None)."""
        g = self.__dict__.get("_copy_ghost")
        return None if g is _MISS else g

    def _make_copy_ghost(self):
        sc = self.scene()
        box = self.selection_box()
        if sc is None or box is None or not self._drag_items:
            return None
        off = QtCore.QPointF(self._move_applied)
        z = max(self.zoom(), 0.01)
        dpr = self.viewport().devicePixelRatioF() or 1.0
        rect = box.translated(-off).adjusted(-3 / z, -3 / z, 3 / z, 3 / z)
        if rect.width() * rect.height() * (z * dpr) ** 2 > self.COPY_GHOST_MAX_PX:
            # (a huge selection at a high zoom: the part round the view)
            seen = self.mapToScene(self.viewport().rect()).boundingRect()
            rect = rect.intersected(seen.adjusted(-40 / z, -40 / z, 40 / z, 40 / z))
        w, h = int(math.ceil(rect.width() * z * dpr)), int(math.ceil(rect.height() * z * dpr))
        if rect.isEmpty() or w < 1 or h < 1 or w * h > self.COPY_GHOST_MAX_PX:
            return None
        img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(QtCore.Qt.transparent)
        # (the objects as they were: the dragged items back for the picture, then on again;
        # the scene's "selection" look draws no background, margin, grid or frame, so the
        # page shows through round the objects)
        had = getattr(sc, "_export", _MISS)
        self._move_items(self._drag_items, -off.x(), -off.y())
        try:
            if had is not _MISS:
                sc._export = "selection"
            p = QtGui.QPainter(img)
            try:
                p.setRenderHints(self.renderHints())
                sc.render(p, QtCore.QRectF(0, 0, w, h), rect)
            finally:
                p.end()
        finally:
            if had is not _MISS:
                sc._export = had
            self._move_items(self._drag_items, off.x(), off.y())
        pm = QtGui.QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr * z)
        item = QtWidgets.QGraphicsPixmapItem(pm)
        item.setPos(rect.topLeft())
        item.setZValue(-1e9)  # (under every object of the page)
        item.setAcceptedMouseButtons(QtCore.Qt.NoButton)
        item.setTransformationMode(QtCore.Qt.SmoothTransformation)
        sc.addItem(item)
        return item

    def _drop_copy_ghost(self) -> None:
        """The drag ended (let go, Esc, another page): the original's picture goes."""
        g = self.__dict__.pop("_copy_ghost", None)
        if g is None or g is _MISS:
            return
        try:
            s = g.scene()
            if s is not None:
                s.removeItem(g)
        except RuntimeError:  # swallow-ok: the scene that held it is gone
            pass

    def _resize_pill(self, r: QtCore.QRectF) -> str:
        """The readout of a handle drag: 'W x H at X, Y'; a multi-selection's adds its
        scale ('(150 %, 150 %)')."""
        if self._resize_multi:
            o = self._resize_origin
            sx = r.width() / o.width() * 100.0 if o.width() > 0 else 100.0
            sy = r.height() / o.height() * 100.0 if o.height() > 0 else 100.0
            return "%d × %d  (%d %%, %d %%)" % (r.width(), r.height(), round(sx), round(sy))
        return "%d × %d at %d, %d" % (r.width(), r.height(), r.left(), r.top())

    def _locked_under(self, vp: QtCore.QPoint):
        """The root of the locked unit on top under ``vp`` (none selectable is above it)."""
        fn = self._locked_probe
        if fn is None:
            return None
        for h in self._hits_under(vp):
            try:
                r = fn(h)
            except Exception:  # noqa: BLE001 -- swallow-ok: a hover hint only
                return None
            return r
        return None

    def locked_under_pointer(self):
        """The root of the locked object under the pointer, or None."""
        vp = self.viewport().mapFromGlobal(QtGui.QCursor.pos())
        if not self.viewport().rect().contains(vp):
            return None
        return self._locked_under(vp)

    def _note_locked_hover(self, root) -> None:
        """Say once on the status bar that the object under the pointer is locked."""
        if root == self._locked_hover:
            return
        self._locked_hover = root
        if root is not None:
            self.statusMessage.emit(
                "Locked: %s. Use the padlock in the Objects panel or Ctrl+L to unlock it."
                % self.unit_label(root)
            )

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        vp = e.position().toPoint()
        if e.button() == QtCore.Qt.LeftButton:
            self._left_down = False
            self._edge_timer.stop()
            if self._swallow_release:
                # Esc cancelled this drag: the release commits nothing
                self._swallow_release = False
                self._maybe_drag = False
                e.accept()
                return
        if e.button() == QtCore.Qt.MiddleButton or (
            self._panning and (self._space_pan or self._tool == "pan")
        ):
            self._panning = False
            self.viewport().setCursor(QtCore.Qt.OpenHandCursor if self._space_pan else self._idle_cursor())
            e.accept()
            return

        if self._zbox is not None and e.button() == QtCore.Qt.LeftButton:
            self._end_zoom_box(vp, e.modifiers())
            e.accept()
            return

        if self._inplace is not None:
            if not self._inplace_follow:
                super().mouseReleaseEvent(e)
            e.accept()
            return

        if self._tool == "freehand":
            if e.button() == QtCore.Qt.LeftButton and self._freehand:
                self._freehand = False
                pts = [(int(p.x()), int(p.y())) for p in self._nodes]
                self._cancel_draw()
                if len(pts) >= 2:
                    # a free-hand line is an open polyline record
                    self.drawCommitted.emit("polyline", {"nodes": pts})
            e.accept()
            return

        if self._tool == "measure":
            if e.button() == QtCore.Qt.LeftButton and self._draw_start is not None:
                sp = self._gear(self._draw_press, self.mapToScene(vp))
                self._draw_cur = self._snap_pt(sp)
                self._pill = self._measure_pill()
                self._last_vp = vp
                self.viewport().update()
            e.accept()
            return

        if self._tool in _BOX_TOOLS | {"line"}:
            if e.button() == QtCore.Qt.LeftButton and self._draw_start is not None:
                end = self._gear(self._draw_press, self.mapToScene(vp))
                self._commit_box_or_line(self._snap_pt(end), e.modifiers())
            e.accept()
            return

        if self._tool in _NODE_TOOLS:
            press, self._node_press = self._node_press, None
            if (
                e.button() == QtCore.Qt.LeftButton
                and press is not None
                and len(self._nodes) == 1
                and (vp - press).manhattanLength() >= drag_start()
            ):
                # CtDraw (help 1172126 / 1172128): the first segment is
                # DRAGGED -- releasing the button adds the second vertex
                p = self._node_point(self.mapToScene(vp), e.modifiers())
                if p != self._nodes[-1]:
                    self._nodes.append(p)
                    self._draw_cur = p
                    self.viewport().update()
            e.accept()
            return

        if self._node_drag is not None and e.button() == QtCore.Qt.LeftButton:
            self._end_node_drag()
            e.accept()
            return

        if self._guide_drag is not None and e.button() == QtCore.Qt.LeftButton:
            self._end_guide_drag(vp, e.modifiers())
            e.accept()
            return

        if getattr(self, "_hotspot_drag", None) is not None and e.button() == QtCore.Qt.LeftButton:
            self._end_hotspot_drag(commit=True)
            e.accept()
            return

        if self._rotating is not None and e.button() == QtCore.Qt.LeftButton:
            self._end_rotate()
            e.accept()
            return

        if self._resizing is not None and e.button() == QtCore.Qt.LeftButton:
            r = self._resize_rect.normalized()
            idx = self._resize_index
            multi, self._resize_multi = self._resize_multi, False
            self._resizing = None
            self._resize_index = None
            self._pill = None
            self.viewport().update()
            o = self._resize_origin
            if multi:
                if r != o and r.width() >= 1 and r.height() >= 1:
                    # the whole selection scales about where its box is fixed: Adjust
                    # Position and Size's move-then-scale (one undo step in the window)
                    sx = r.width() / o.width() if o.width() > 0 else 1.0
                    sy = r.height() / o.height() if o.height() > 0 else 1.0
                    self.selectionScaled.emit(int(round(r.left())), int(round(r.top())), float(sx), float(sy))
            elif r != o and (r.width() >= 1 or o.width() < 1) and (r.height() >= 1 or o.height() < 1):
                # the box the handles were drawn on (a unit's may differ from
                # its root record's extent -- the window maps between them)
                self.last_resize_origin = QtCore.QRectF(self._resize_origin)
                self.objectResized.emit(idx, (int(r.left()), int(r.top()), int(r.right()), int(r.bottom())))
            e.accept()
            return

        if self._marquee is not None and e.button() == QtCore.Qt.LeftButton:
            rect = self._marquee
            self._marquee = None
            self._marquee_before = None
            self._box_eligible = None
            self._box_timer.stop()
            self._box_preview = []
            self._pill = None
            self.viewport().update()
            lim = drag_page_px(2, self.zoom())  # (a smaller box is a click)
            if rect.width() > lim or rect.height() > lim:
                boxed = self._box_units(rect, self._marquee_touch)
                mods = self._marquee_mods
                if mods & QtCore.Qt.ControlModifier:
                    # Ctrl: each boxed unit in or out
                    units = [list(u) for u in self._units]
                    have = {min(u) for u in units}
                    for u in boxed:
                        if min(u) in have:
                            units = [x for x in units if min(x) != min(u)]
                        else:
                            units.append(u)
                    self._set_units(units)
                elif mods & QtCore.Qt.ShiftModifier:
                    # Shift: added after what was selected, in its order
                    have = {min(u) for u in self._units}
                    self._set_units([list(u) for u in self._units] + [u for u in boxed if min(u) not in have])
                else:
                    self._set_units(boxed)
                if boxed:
                    self._key_root = min(boxed[-1])
            self._marquee_mods = QtCore.Qt.NoModifier
            e.accept()
            return

        if self._moving and e.button() == QtCore.Qt.LeftButton:
            # the displacement the preview showed: already on the grid /
            # guideline / smart guide (_move_snap); Alt dragged it free
            total = self._move_applied
            self._moving = False
            self._maybe_drag = False
            self._mv_box = None
            self._constrain = None
            self._pending_click = None
            self._drop_copy_ghost()
            # the moved items are re-synced (a rebuild) or put back: measure again
            self._drop_unit_rects()
            self._sg_active = []
            self._pill = None
            self.viewport().setCursor(self._idle_cursor())
            dx, dy = int(round(total.x())), int(round(total.y()))
            items, self._drag_items = self._drag_items, []
            # the preview goes back first: the scene moves the items by what the
            # document commits (MainWindow._shift_scene) or builds them again; with
            # nothing to commit they are where the model has them again (the scene
            # only rebuilds what the document changed)
            self._move_items(items, -total.x(), -total.y())
            copy = "copy" in modifier_roles(e.modifiers(), moving=True)
            if dx or dy:
                # the copy key held when the button goes up: the original stays and a
                # copy lands there (decided now, like a guideline's Ctrl+drag)
                (self.objectsCopied if copy else self.objectsMoved).emit(list(self._selection), dx, dy)
            else:
                self.viewport().update()
            e.accept()
            return

        if e.button() == QtCore.Qt.LeftButton:
            if self._alt_press is not None:
                self._alt_cycle(vp)  # an Alt+click: the next one down
            pend, self._pending_click = self._pending_click, None
            if pend is not None:
                self._finish_click(pend)
        self._maybe_drag = False
        super().mouseReleaseEvent(e)

    def _finish_click(self, pend: dict) -> None:
        """A click (no drag) whose meaning waited for the release."""
        unit, op = pend["unit"], pend["op"]
        if op == "add":
            self._add_unit(unit)
        elif op == "toggle":
            self._toggle_unit(unit)
        elif op == "remove":
            self._remove_unit(unit)
        elif op in ("select", "isolate"):
            self._set_units([unit])
            self._key_root = min(unit)

    def _update_box_preview(self) -> None:
        """The selection box's live preview: the units it would take outlined, and their
        number at the pointer."""
        rect = self._marquee
        if rect is None or not self.BOX_PREVIEW:
            self._box_preview = []
            return
        lim = drag_page_px(2, self.zoom())  # (the release's test: a smaller box is a click)
        if rect.width() <= lim and rect.height() <= lim:
            self._box_preview = []
            self._pill = None
        else:
            self._box_preview = self._box_units(rect, self._marquee_touch)
            n = len(self._box_preview)
            self._pill = "%d object%s%s" % (
                n,
                "" if n == 1 else "s",
                " touched" if self._marquee_touch else "",
            )
        self.viewport().update()

    def mouseDoubleClickEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.MiddleButton:
            # Settings > Canvas > "Middle double-click": Fit All Objects (AutoCAD's zoom
            # extents), Fit Page or nothing
            self._panning = False
            if self.MIDDLE_DOUBLE_CLICK == "fit_all":
                self.fit_all()
            elif self.MIDDLE_DOUBLE_CLICK == "fit_page":
                self.fit_page()
            e.accept()
            return
        if self._tool in ("zoom", "pan") and e.button() == QtCore.Qt.LeftButton:
            if self._tool == "zoom":
                self.mousePressEvent(e)  # (a second click zooms again)
            e.accept()
            return
        if self._inplace is not None:
            if self._inplace_contains(e.position().toPoint()):
                super().mouseDoubleClickEvent(e)  # select a word
            e.accept()
            return
        if self._tool in _NODE_TOOLS and self._nodes:
            self._commit_nodes()
            e.accept()
            return
        if self._tool == "select" and e.button() == QtCore.Qt.LeftButton:
            vp = e.position().toPoint()
            if self._hotspot_at(vp) and not e.modifiers() & QtCore.Qt.AltModifier:
                # the hot spot marker: its X / Y (Hot Spot Location)
                self._hotspot_drag = None
                self.hotspotDialogRequested.emit()
                e.accept()
                return
            if (
                not e.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier)
                and self._guide_at(vp) is not None
                and not self._topmost_unit(vp)
            ):
                # a guideline double-clicked where no object is: Guidelines
                # Setup (CtDraw's canvas WM_LBUTTONDBLCLK tests the objects
                # first, then -> its 'GuideLines' dialog); over
                # an object the double-click is the object's, like its click
                self.guidelinesDialogRequested.emit()
                e.accept()
                return
            if e.modifiers() & QtCore.Qt.ControlModifier:
                # Ctrl+double-click: the exact object under the cursor -- a
                # member of a group / symbol / genie, or a (locked) template
                # record -- opens ITS sheet (help 1172121 / 1172204 / 1172115)
                for h in self._hits_under(e.position().toPoint()):
                    if self._member_ok(h):
                        self.memberActivated.emit(h)
                        break
                e.accept()
                return
            # activate the topmost SELECTABLE object, skipping locked/template
            # hits (the page template's full-page anchor must not swallow the
            # double-click and open the template's own parameters); the window
            # resolves it to its top-level unit.
            for h in self._hits_under(e.position().toPoint()):
                if self._resolve_unit(h):
                    self.objectActivated.emit(h)
                    break
            else:
                # no object here: Settings > Canvas > "Double-click on the empty page"
                if self.EMPTY_DOUBLE_CLICK == "page_props" and not e.modifiers() & _ALT:
                    self.pageDoubleClicked.emit()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def _context_at(self, vp: QtCore.QPoint, global_pos: QtCore.QPoint) -> None:
        """A right-click (or a long press) at ``vp``: select what is there and ask the
        window for the context menu."""
        if self._node_at(vp) is None:
            # like any click away from a node: the nodes are deselected,
            # so the menu's Delete deletes the object, not hidden nodes
            self.clear_node_selection()
        idx, _item = self._hit_index(vp)
        unit = self._resolve_unit(idx) if idx is not None else []
        if unit:
            if min(unit) not in self.selection_roots():
                self._set_units([unit])
        elif self._selection:
            # right-click on empty page (or a locked object): the menu is
            # for the PAGE, not the previous selection
            self._set_units([])
        self.contextRequested.emit(QtCore.QPoint(global_pos), self.selection_roots())

    def long_press(self, vp: QtCore.QPoint, global_pos: QtCore.QPoint) -> None:
        """A long press of a finger or a pen (touch): the context menu, as a right-click
        there. The drag the press began goes."""
        self.cancel_drag()
        self._maybe_drag = False
        self._swallow_release = True
        if self._tool == "select":
            self._context_at(vp, global_pos)

    # -- the context menu from the keyboard (the Menu key, Shift+F10) -----------------
    def contextMenuEvent(self, e: QtGui.QContextMenuEvent) -> None:  # noqa: N802 -- Qt
        if e.reason() == QtGui.QContextMenuEvent.Keyboard:
            self.open_context_menu_from_keyboard()
            e.accept()
            return
        # (a right-click opened the menu at the press; the in-place text keeps its own)
        super().contextMenuEvent(e)

    def keyboard_menu_point(self) -> QtCore.QPoint:
        """Where a menu opened from the keyboard appears (viewport px): the middle of the
        selection when it shows, else the pointer over the canvas, else the canvas's
        middle."""
        vp = self.viewport().rect()
        box = self.selection_box()
        if box is not None:
            r = self.mapFromScene(box).boundingRect()
            c = r.center()
            if vp.intersects(r):
                return QtCore.QPoint(
                    max(vp.left() + 2, min(vp.right() - 2, c.x())),
                    max(vp.top() + 2, min(vp.bottom() - 2, c.y())),
                )
        cur = self.viewport().mapFromGlobal(QtGui.QCursor.pos())
        if vp.contains(cur):
            return cur
        return vp.center()

    def open_context_menu_from_keyboard(self) -> bool:
        """The canvas's right-click menu for the selection, at :meth:`keyboard_menu_point`
        (Shift+F10 and the Menu key: some keyboards send both, one menu opens)."""
        if (
            self._tool != "select"
            or self._inplace is not None
            or time.monotonic() - self._kbd_menu_closed < 0.3
        ):
            return False
        at = self.viewport().mapToGlobal(self.keyboard_menu_point())
        try:
            self.contextRequested.emit(at, self.selection_roots())
        finally:
            self._kbd_menu_closed = time.monotonic()
        return True

    # -- the selection from the keyboard (Tab, Ctrl+Alt + arrows, Enter) ---------------
    def set_units_key(self, fn) -> None:
        """``fn()``: a value that changes whenever a unit could become selectable or not
        outside the scene (the locks, Break Lock Mode, the page's derive); None = no cache."""
        self._units_key = fn or (lambda: None)
        self._selectable_memo = None

    # -- the zoom box (View > Zoom Box, Z; Ctrl+Shift+drag) ---------------------------
    def _begin_zoom_box(self, vp: QtCore.QPoint, mods) -> None:
        self._zbox = {
            "origin": QtCore.QPoint(vp),
            "cur": QtCore.QPoint(vp),
            "out": bool(mods & QtCore.Qt.AltModifier),
        }
        self.viewport().update()

    def zoom_box_rect(self) -> QtCore.QRect | None:
        """The zoom box being dragged (viewport pixels), or None."""
        z = self._zbox
        if z is None:
            return None
        return QtCore.QRect(z["origin"], z["cur"]).normalized()

    def _end_zoom_box(self, vp: QtCore.QPoint, mods) -> None:
        """A zoom box let go: the box fills the view; a click zooms in 2x about the point
        (Alt: out)."""
        z, self._zbox = self._zbox, None
        self.viewport().update()
        if z is None:
            return
        box = QtCore.QRect(z["origin"], vp).normalized()
        if box.width() >= 5 and box.height() >= 5:
            rect = self.mapToScene(box).boundingRect()
            self.frame_rect(rect, margin=0.0, max_zoom=self.ZOOM_MAX)
        elif z.get("click_unit"):
            self._toggle_unit(z["click_unit"])  # (Ctrl+Shift+click on an object)
        else:
            out = z["out"] or bool(mods & QtCore.Qt.AltModifier)
            self.zoom_about(0.5 if out else 2.0, vp)
        self._zoom_box_return()

    def _zoom_box_return(self) -> None:
        """A zoom box made while Z was held ended (or went): the tool before comes back."""
        back, self._zhold_return = self._zhold_return, None
        if back is not None:
            self.toolRequested.emit(back)

    # -- draw modifiers (CtDraw: Ctrl constrains, Shift = from the centre) ----
    @staticmethod
    def _hv(anchor: QtCore.QPointF, p: QtCore.QPointF) -> QtCore.QPointF:
        """``p`` moved onto the horizontal or the vertical through ``anchor``,
        whichever is nearer (Ctrl while drawing a line or a polygon / pipe
        segment)."""
        if abs(p.x() - anchor.x()) >= abs(p.y() - anchor.y()):
            return QtCore.QPointF(p.x(), anchor.y())
        return QtCore.QPointF(anchor.x(), p.y())

    def _line_end(self, start: QtCore.QPointF, end: QtCore.QPointF, mods) -> QtCore.QPointF:
        """A line's end: the constrain key (Ctrl, or Shift with the design keys) keeps it
        horizontal or vertical."""
        return self._hv(start, end) if "constrain" in modifier_roles(mods) else end

    def _node_point(self, sp: QtCore.QPointF, mods, snap: bool = True) -> QtCore.QPointF:
        """A polygon / pipe vertex at ``sp``: grid-snapped, and with the constrain key
        (Ctrl) kept horizontal or vertical to the previous vertex (help 1172126)."""
        p = self._snap_pt(sp) if snap else sp
        if "constrain" in modifier_roles(mods) and self._nodes:
            p = self._hv(self._nodes[-1], p)
        return p

    # -- draw-tool commits ---------------------------------------------------
    def _commit_box_or_line(self, end: QtCore.QPointF, mods=QtCore.Qt.NoModifier) -> None:
        start = self._draw_start
        self._draw_start = None
        self._draw_cur = None
        self._pill = None
        self.viewport().update()
        if start is None:
            return
        tool = self._tool
        z = self.zoom()
        if tool == "line":
            end = self._line_end(start, end, mods)
            if (end - start).manhattanLength() < drag_page_px(2, z):
                return
            self.drawCommitted.emit(
                tool, {"nodes": [(int(start.x()), int(start.y())), (int(end.x()), int(end.y()))]}
            )
            return
        w, h = abs(end.x() - start.x()), abs(end.y() - start.y())
        click = False
        lim = drag_page_px(3, z)  # (a drag shorter than this is a click)
        if (w < lim and h < lim or tool == "cicode_object") and tool in _CLICK_DEFAULT_SIZE:
            dw, dh = _draw_styles.click_size(tool) or _CLICK_DEFAULT_SIZE[tool]
            # the default box stays within the record range (shifted back from it)
            x0 = int(min(start.x(), COORD_LIMIT - dw))
            y0 = int(min(start.y(), COORD_LIMIT - dh))
            rect = (x0, y0, x0 + dw, y0 + dh)
            click = True
        elif lim > 3 and w < lim and h < lim:
            return  # (a click: a longer drag start draws no box)
        else:
            r = clamp_rect(self._box_rect(start, end, mods))
            if r.width() < 3 or r.height() < 3:
                return
            rect = (int(r.left()), int(r.top()), int(r.right()), int(r.bottom()))
        if tool == "text":
            # the text is typed where it goes (committed by textEditFinished)
            self._begin_placement(QtCore.QPointF(rect[0], rect[1]))
            return
        if tool in SPECIAL_TOOLS or tool == "symbol":
            # (a click with the Symbol tool places the element at its own size: the
            # window reads 'click', the default box is only the preview's)
            self.drawCommitted.emit(tool, {"rect": rect, "click": click})
            return
        self.drawCommitted.emit(tool, {"rect": rect})

    def _commit_nodes(self) -> None:
        pts = [(int(p.x()), int(p.y())) for p in self._nodes]
        tool = self._tool
        self._cancel_draw()
        if len(pts) >= 2:
            self.drawCommitted.emit(tool, {"nodes": pts})

    def _measure_pill(self) -> str:
        """The Measure tool's readout: the width and height of the dragged box, the
        straight distance and its angle (page pixels, anticlockwise from the x axis)."""
        import math

        a, b = self._draw_start, self._draw_cur
        if a is None or b is None:
            return ""
        dx, dy = b.x() - a.x(), b.y() - a.y()
        return "%d × %d   %d px  %d°" % (
            abs(dx),
            abs(dy),
            round(math.hypot(dx, dy)),
            math.degrees(math.atan2(-dy, dx)) % 360,
        )

    def measurement(self):
        """The Measure tool's current measurement as (x0, y0, x1, y1) in page pixels, or
        None."""
        if self._tool != "measure" or self._draw_start is None or self._draw_cur is None:
            return None
        a, b = self._draw_start, self._draw_cur
        return (int(a.x()), int(a.y()), int(b.x()), int(b.y()))

    def _cancel_draw(self) -> None:
        self._draw_start = None
        self._draw_cur = None
        self._nodes = []
        self._node_press = None
        self._freehand = False
        self._node_drag = None
        self._pill = None
        self.viewport().update()

    # -- resize math ---------------------------------------------------------
    def _resize_to(self, sp: QtCore.QPointF, mods=QtCore.Qt.NoModifier) -> QtCore.QRectF:
        """The box a handle drag to ``sp`` makes."""
        o = QtCore.QRectF(self._resize_origin)
        p = self._snap_pt(self._gear(self._resize_press, sp))
        fx, fy = _HANDLES[self._resizing]
        roles = modifier_roles(mods)
        centre = "centre" in roles
        prop = "proportional" in roles and o.width() > 0 and o.height() > 0
        cx, cy = o.center().x(), o.center().y()
        left, top, right, bottom = o.left(), o.top(), o.right(), o.bottom()
        if fx == 0.0:
            left = p.x()
            right = 2 * cx - p.x() if centre else right
        elif fx == 1.0:
            right = p.x()
            left = 2 * cx - p.x() if centre else left
        if fy == 0.0:
            top = p.y()
            bottom = 2 * cy - p.y() if centre else bottom
        elif fy == 1.0:
            bottom = p.y()
            top = 2 * cy - p.y() if centre else top
        if prop:
            w0, h0 = o.width(), o.height()
            sx = (right - left) / w0 if fx != 0.5 else None
            sy = (bottom - top) / h0 if fy != 0.5 else None
            if sx is not None and sy is not None:
                s = sx if abs(sx) >= abs(sy) else sy
            else:
                s = sx if sx is not None else sy
            nw, nh = w0 * s, h0 * s
            if fx == 0.5 or centre:
                left, right = cx - abs(nw) / 2.0, cx + abs(nw) / 2.0
                if centre and fx != 0.5 and s < 0:
                    left, right = right, left
            elif fx == 1.0:
                left, right = o.left(), o.left() + nw
            else:
                left, right = o.right() - nw, o.right()
            if fy == 0.5 or centre:
                top, bottom = cy - abs(nh) / 2.0, cy + abs(nh) / 2.0
                if centre and fy != 0.5 and s < 0:
                    top, bottom = bottom, top
            elif fy == 1.0:
                top, bottom = o.top(), o.top() + nh
            else:
                top, bottom = o.bottom() - nh, o.bottom()

        def px(v):
            return float(math.floor(v + 0.5))

        return QtCore.QRectF(QtCore.QPointF(px(left), px(top)), QtCore.QPointF(px(right), px(bottom)))

    # -- tag drag-to-bind ------------------------------------
    def _hook_drag(self, e) -> bool:
        """A drop handler of canvas_hooks takes this drag: its hint shows at the pointer."""
        vp = e.position().toPoint()
        idx, _item = self._hit_index(vp)
        unit = self._resolve_unit(idx) if idx is not None else []
        found = canvas_hooks.drop_hint(self, e, self._drop_point(e), unit)
        old = self._drop_hint
        self._drop_hint = None if found is None else (QtCore.QPoint(vp), found[1])
        if old is not None or self._drop_hint is not None:
            self.viewport().update()
        if found is None:
            return False
        e.acceptProposedAction()
        return True

    def dragLeaveEvent(self, e) -> None:
        if self._drop_hint is not None:
            self._drop_hint = None
            self.viewport().update()
        super().dragLeaveEvent(e)

    def dragEnterEvent(self, e) -> None:
        from ..panels.tags_dock import TAG_MIME
        from ..panels.library_panel import LIBELEM_MIME
        from ..dialog.bitmap_import import dropped_files

        if self._hook_drag(e):
            return
        if e.mimeData().hasFormat(LIBELEM_MIME) or dropped_files(e.mimeData()):
            e.setDropAction(QtCore.Qt.CopyAction)
            e.accept()
        elif e.mimeData().hasFormat(TAG_MIME):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e) -> None:
        from ..panels.tags_dock import TAG_MIME
        from ..panels.library_panel import LIBELEM_MIME
        from ..dialog.bitmap_import import dropped_files

        if self._hook_drag(e):
            return
        if e.mimeData().hasFormat(LIBELEM_MIME) or dropped_files(e.mimeData()):
            e.setDropAction(QtCore.Qt.CopyAction)
            e.accept()
        elif e.mimeData().hasFormat(TAG_MIME):
            idx, _item = self._hit_index(e.position().toPoint())
            if idx is not None:
                e.acceptProposedAction()
            else:
                e.ignore()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e) -> None:
        from ..panels.tags_dock import TAG_MIME
        from ..panels.library_panel import LIBELEM_MIME, parse_libelem_mime

        if self._drop_hint is not None:
            self._drop_hint = None
            self.viewport().update()
        idx, _item = self._hit_index(e.position().toPoint())
        unit = self._resolve_unit(idx) if idx is not None else []
        if canvas_hooks.run_drop(self, e, self._drop_point(e), unit):
            e.acceptProposedAction()
            return
        if e.mimeData().hasFormat(LIBELEM_MIME):
            r = parse_libelem_mime(e.mimeData())
            if r:
                self.libraryElementDropped.emit(*r, self._drop_point(e))
                e.setDropAction(QtCore.Qt.CopyAction)
                e.accept()
            return
        from ..dialog.bitmap_import import dropped_files

        files = dropped_files(e.mimeData())
        if files:
            # CtDraw: drag a graphics file from Explorer onto the page = import
            self.filesDropped.emit(files, self._drop_point(e))
            e.setDropAction(QtCore.Qt.CopyAction)
            e.accept()
            return
        if e.mimeData().hasFormat(TAG_MIME):
            idx, _item = self._hit_index(e.position().toPoint())
            if idx is not None:
                tag = bytes(e.mimeData().data(TAG_MIME)).decode("utf-8")
                self.tagDropped.emit(idx, tag)
                e.acceptProposedAction()
            return
        super().dropEvent(e)

    def _drop_point(self, e) -> QtCore.QPointF:
        """Where a drop lands on the page, within +/- 32000."""
        p = self.mapToScene(e.position().toPoint())
        return QtCore.QPointF(_clamp(p.x()), _clamp(p.y()))

    #: from this many selected units a paint first culls them by their kept bounds
    OUTLINE_CULL_FROM = 200

    def drawForeground(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        """The editor's overlays over the page. Each block paints on its own painter state
        (saved and restored), so no pen or brush of one leaks into the next."""
        super().drawForeground(painter, rect)
        # the overlays over the items, under the canvas's own marks (ui/editor/canvas_hooks.py)
        canvas_hooks.paint_overlays(self, painter, rect, "over_items")
        z = max(self.zoom(), 0.01)
        # (the theme's colours at paint time: Settings > Appearance > Canvas > Selection
        # colour, else the accent of the theme shown)
        from ..style.theme import canvas_colour

        accent = canvas_colour("selection")
        for paint in (
            self._paint_guidelines,
            self._paint_smart_guides,
            self._paint_marks,
            self._paint_hover,
            self._paint_selection,
            self._paint_draw_preview,
        ):
            painter.save()
            try:
                paint(painter, rect, z, accent)
            finally:
                painter.restore()
        # the overlays registered from outside (ui/editor/canvas_hooks.py)
        canvas_hooks.paint_overlays(self, painter, rect, "foreground")
        # viewport-space marks: the cross hairs, the zoom box, the chips, the focus frame
        painter.save()
        try:
            painter.resetTransform()
            self._paint_crosshair(painter)
            self._paint_zoom_box(painter)
            self._paint_page_chip(painter)
            self._paint_selection_chips(painter, accent)
            if self._pill:
                self._paint_pill(painter, self._last_vp, self._pill)
            elif self._drop_hint is not None:
                self._paint_pill(painter, self._drop_hint[0], self._drop_hint[1])
            elif self._flash_pill is not None:
                self._paint_pill(painter, self._flash_pill[1], self._flash_pill[0])
            elif self._key_pill:
                self._paint_pill(painter, self._key_pill_at(), self._key_pill)
            if self.keyboard_focus_shown():
                self._paint_focus_frame(painter)
        finally:
            painter.restore()

    def _preview_fill(self, accent: QtGui.QColor) -> QtGui.QColor:
        """The polygon preview's fill: the colour the polygon will get, at 12 %."""
        c = QtGui.QColor(accent)
        fn = self._fill_colour_source
        if fn is not None:
            try:
                rgb = int(fn()) & 0xFFFFFF
                c = QtGui.QColor((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)
            except TypeError, ValueError:  # swallow-ok: no colour yet, the accent then
                pass
        c.setAlpha(31)
        return c

    # -- viewport-space marks (the painter is reset to viewport pixels) -----------------
    @staticmethod
    def _halo_for(c: QtGui.QColor) -> QtGui.QColor:
        from ..style.theme import fixed

        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        return fixed("halo_dark") if lum > 110 else fixed("halo_light")

    @staticmethod
    def _pill_font() -> QtGui.QFont:
        """The readouts' and badges' font: the caption type at Settings > Appearance > Text
        size."""
        from ..style.theme import font

        return font("caption")

    @staticmethod
    def _chip_font() -> QtGui.QFont:
        """The 'Page' chip's font: the body type at the text size."""
        from ..style.theme import font

        return font("body")

    def _pill_box(self, at: QtCore.QPoint, text: str) -> QtCore.QRectF:
        """Where a pill for the pointer at ``at`` is drawn: below right of it, flipped to the
        left or above near the right or bottom edge (kept on the canvas)."""
        fm = QtGui.QFontMetricsF(self._pill_font())
        box = QtCore.QRectF(at.x() + 14, at.y() + 18, fm.horizontalAdvance(text) + 12, fm.height() + 6)
        vp = self.viewport().rect()
        if box.right() > vp.right() - 2:
            box.moveRight(at.x() - 10)
        if box.bottom() > vp.bottom() - 2:
            box.moveBottom(at.y() - 10)
        return box

    def page_chip_rect(self) -> QtCore.QRectF | None:
        """The 'Page' chip (viewport pixels) when the page is out of view, else None."""
        sc = self.scene()
        page = sc.page_rect() if sc is not None and hasattr(sc, "page_rect") else None
        if page is None or page.isNull() or getattr(sc, "_element_mode", False):
            return None
        vpr = self.viewport().rect()
        if vpr.width() < 80 or vpr.height() < 60:
            return None
        seen = self.mapToScene(vpr).boundingRect()
        if page.intersects(seen):
            return None
        fm = QtGui.QFontMetricsF(self._chip_font())
        w, h = fm.horizontalAdvance("Page") + 36, fm.height() + 10
        c = QtCore.QPointF(vpr.center())
        d = QtCore.QPointF(self.mapFromScene(page.center())) - c
        if abs(d.x()) < 1e-6 and abs(d.y()) < 1e-6:
            return None
        hw, hh = vpr.width() / 2.0 - w / 2.0 - 14, vpr.height() / 2.0 - h / 2.0 - 14
        t = min(
            hw / abs(d.x()) if abs(d.x()) > 1e-6 else 1e12, hh / abs(d.y()) if abs(d.y()) > 1e-6 else 1e12
        )
        at = c + d * t
        box = QtCore.QRectF(at.x() - w / 2.0, at.y() - h / 2.0, w, h)
        return self._chip_place(box, d, hh)

    def _chip_place(self, box: QtCore.QRectF, d: QtCore.QPointF, hh: float) -> QtCore.QRectF:
        """The 'Page' chip clear of the overlays over the canvas (the selection bar, the
        navigation bar, the navigator; not the thin scroll bars, which come and go with
        every scroll): slid along the edge it sits on, then along the other edge toward the
        page, then moved inward; ``box`` when the canvas has no free place."""
        from .overlay_scrollbars import OverlayBar

        vpr = self.viewport().rect()
        over = [
            QtCore.QRectF(ch.geometry()).adjusted(-6, -6, 6, 6)
            for ch in self.viewport().findChildren(
                QtWidgets.QWidget, options=QtCore.Qt.FindDirectChildrenOnly
            )
            if ch.isVisible() and not isinstance(ch, OverlayBar)
        ]
        bound = QtCore.QRectF(vpr).adjusted(4, 4, -4, -4)

        def free(cand: QtCore.QRectF) -> bool:
            return bound.contains(cand) and not any(cand.intersects(o) for o in over)

        def slides(start: QtCore.QRectF, horizontal: bool):
            for k in range(0, 60):
                step = (k + 1) // 2 * 24 * (1 if k % 2 else -1)
                yield start.translated(step, 0) if horizontal else start.translated(0, step)

        along_x = abs(box.center().y() - vpr.center().y()) >= hh - 1
        for cand in slides(box, along_x):
            if free(cand):
                return cand
        # the other edge the page lies toward (the right or left edge for a chip on the
        # top or bottom edge, and the other way round)
        other = QtCore.QRectF(box)
        if along_x and abs(d.x()) > 1e-6:
            if d.x() > 0:
                other.moveRight(vpr.right() - 14)
            else:
                other.moveLeft(vpr.left() + 14)
        elif not along_x and abs(d.y()) > 1e-6:
            if d.y() > 0:
                other.moveBottom(vpr.bottom() - 14)
            else:
                other.moveTop(vpr.top() + 14)
        if other != box:
            for cand in slides(other, not along_x):
                if free(cand):
                    return cand
        # inward, away from the edge (above a navigator in the corner)
        inward = QtCore.QPointF(vpr.center()) - box.center()
        if along_x:
            inward = QtCore.QPointF(0, 1 if inward.y() > 0 else -1)
        else:
            inward = QtCore.QPointF(1 if inward.x() > 0 else -1, 0)
        for k in range(1, 40):
            cand = box.translated(inward * (k * 12))
            if not bound.contains(cand):
                break
            if free(cand):
                return cand
        return box
