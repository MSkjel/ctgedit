"""Quick Open (Ctrl+Shift+O): search pages, library items, tags, functions and objects."""

from __future__ import annotations

from ...infra.paths import path_key

from ..names import plural
from ...format.cnum import is_digits
from ..style.theme import sp as _sp

import dataclasses
import os
from typing import Callable, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...infra import caches as _caches
from ...infra import logsetup
from .. import search_rows as SR

#: the prefixes and the kinds they keep (longest first when parsed)
PREFIXES = {
    "p": ("page",),
    "s": ("symbol",),
    "g": ("genie",),
    "t": ("tag",),
    "f": ("function",),
    "tpl": ("template",),
    "sg": ("supergenie",),
}
KIND_ORDER = {"page": 0, "symbol": 1, "genie": 2, "template": 3, "supergenie": 4, "tag": 5, "function": 6}
KIND_LABEL = {
    "page": "page",
    "symbol": "symbol",
    "genie": "genie",
    "template": "template",
    "supergenie": "super genie",
    "tag": "tag",
    "function": "function",
    "object": "object",
    "tab": "open tab",
    "goto": "AN",
}
#: the icon of each kind (theme.icon names)
KIND_ICON = {
    "page": "page",
    "symbol": "symbol",
    "genie": "paste_genie",
    "template": "template",
    "supergenie": "super_genie",
    "tag": "variable_tags",
    "function": "cicode",
    "tab": "page",
    "goto": "goto",
}
#: the icon of each object kind ('@' rows)
OBJECT_ICON = {
    "line": "line",
    "rectangle": "rectangle",
    "ellipse": "ellipse",
    "polygon": "polygon",
    "pipe": "pipe",
    "text": "text",
    "number": "number",
    "button": "button",
    "symbol_set": "symbol_set",
    "trend": "trend",
    "cicode_object": "cicode_object",
    "symbol": "symbol",
    "genie": "paste_genie",
    "composite_genie": "composite_genie",
    "group": "group",
    "unknown": "unknown",
}
#: the library extension of each element kind
ELEMENT_EXT = {"symbol": "ctl", "genie": "ctm", "supergenie": "ctm", "template": "ctt"}
#: the rows shown, the tags and the objects a search looks at: the defaults of Settings >
#: Ribbon > Search > Quick Open shows at most (ui/prefs/timing_options.quick_open_limits: the tags
#: and objects follow the rows)
MAX_ROWS = 40
#: the two scopes Tab switches between
SCOPES = ("project", "install")
SCOPE_LABEL = {"project": "This project", "install": "All projects"}
SCOPE_TIP = {
    "project": "Searching this project and the projects it includes. Tab: every project of the install",
    "install": "Searching every project of the install. Tab: this project and its includes",
}


def _limits() -> tuple:
    """(rows, tags, objects): what Quick Open shows and searches at most (the setting)."""
    from ..prefs import timing_options

    return timing_options.quick_open_limits()


@dataclasses.dataclass
class Hit:
    """One row: ``kind``, ``name`` (what is searched), ``detail`` (shown after it), ``data``
    (what Enter needs) and ``entry`` (the catalog entry: its thumbnail)."""

    kind: str
    name: str
    detail: str
    data: object
    entry: object = None


def _rank(query: str, name: str) -> int:
    """Lower = better, -1 = no match (ui/search_rows.match: the start, a word or a part
    after ``_`` / ``.`` starts with it (10 + k), the name holds it (30 + where), its letters
    in order from word starts (100 +))."""
    return SR.rank(query, name, substring_min=1, within_word=True)


def split_query(text: str):
    """``(kinds, words)`` of a typed query: a ``x:`` prefix keeps one kind."""
    q = (text or "").strip()
    kinds = tuple(KIND_ORDER)
    low = q.lower()
    for p in sorted(PREFIXES, key=len, reverse=True):
        if low.startswith(p + ":") and len(q) >= len(p) + 1:
            return PREFIXES[p], q[len(p) + 1 :].strip()
    return kinds, q


def query_mode(text: str) -> str:
    """'palette' (the text is the command palette's), 'objects' ('@'), 'goto' (':' and a
    number) or 'find' (the catalog's kinds)."""
    q = (text or "").lstrip()
    low = q.lower()
    if q.startswith(">") or q.startswith("?") or low.startswith("set:"):
        return "palette"
    if q.startswith("@"):
        return "objects"
    if q.startswith(":"):
        return "goto"
    return "find"


# --------------------------------------------------------------------------- the sources
_FUNCTIONS: dict = {}
_caches.register("quick_open.functions", _FUNCTIONS, on=("refresh", "tests"))


def _chain(project_dir: str, scope: str = "project") -> list:
    from ...io.catalog import catalog_for

    cat = catalog_for()
    info = cat.folder(project_dir)
    if scope == "install":
        everyone = list(cat.projects())
        if info.is_folder:
            everyone.insert(0, info)
        return everyone
    return cat.closure(info) if not info.is_folder else [info]


def page_hits(project_dir: str, scope: str = "project") -> list:
    """The pages of the project and the projects it includes (the catalog's lists)."""
    from ...io.catalog import catalog_for

    out = []
    try:
        cat = catalog_for()
        for p in _chain(project_dir, scope):
            for e in cat.pages(p):
                out.append(Hit("page", e.name, "%s  %s" % (p.name, e.title or ""), e.path, e))
    except Exception:  # noqa: BLE001 -- a source that fails leaves the rest listed
        logsetup.note_exception("quick_open.page_hits")
    return out


def element_hits(project_dir: str, kind: str, scope: str = "project") -> list:
    """The symbols, genies, templates or super genies the project can place (its own and the
    included projects'), a shadowed copy left out; a super genie is not a genie row."""
    from ...io.catalog import catalog_for, is_super_genie

    out = []
    try:
        cat = catalog_for()
        chain = _chain(project_dir, scope)
        for e in cat.entries(chain, kind, shadow=scope != "install"):
            if e.shadowed:
                continue
            if kind == "genie" and is_super_genie(e.name):
                continue
            out.append(
                Hit(kind, e.reference, e.project, (e.library, e.name, ELEMENT_EXT.get(kind, "ctl")), e)
            )
    except Exception:  # noqa: BLE001
        logsetup.note_exception("quick_open.element_hits")
    return out


def tag_hits(project_dir: str, text: str, limit: Optional[int] = None, built_only: bool = False) -> list:
    """Up to ``limit`` (None: the setting's) tags and equipment items whose name starts with
    ``text`` (the tag index of the page folder's scope; built now when it is not yet, unless
    ``built_only``)."""
    if not text:
        return []
    if limit is None:
        limit = _limits()[1]
    from ...io import project as PR

    out = []
    try:
        idx = PR.cached_tag_index(project_dir) if built_only else PR.tag_index(project_dir)
        if idx is None:
            return []
        for code in idx.search(text, limit=limit):
            name, kind, typ, _equip, proj, comment = idx.cells(code)
            out.append(Hit("tag", name, ("%s  %s  %s" % (proj, typ, comment)).strip(), name))
    except Exception:  # noqa: BLE001
        logsetup.note_exception("quick_open.tag_hits")
    return out


def function_hits(project_dir: str) -> list:
    """The project's Cicode functions (its .ci files and the included projects'). The
    list is kept while the .ci files' stamps are the same (cicode_catalog.ci_stamps): a
    function written in another program is listed on the next open."""
    from ...io import cicode_catalog as CC
    from ...io.tagscope import scope

    key = path_key(project_dir)
    try:
        stamps = CC.ci_stamps(project_dir)
    except Exception:  # noqa: BLE001
        logsetup.note_exception("quick_open.function_hits/stamps")
        stamps = None
    hit = _FUNCTIONS.get(key)
    if hit is not None and hit[0] == stamps and stamps is not None:
        return hit[1]
    out = []
    try:
        dirs = {p.name: p.dir for p in scope(project_dir).projects}
        for fn in CC.project_functions(project_dir):
            d = dirs.get(fn.source, "")
            path = os.path.join(d, fn.category) if d and fn.category else ""
            out.append(Hit("function", fn.name, "%s  %s" % (fn.source, fn.signature), path))
    except Exception:  # noqa: BLE001
        logsetup.note_exception("quick_open.function_hits")
    _FUNCTIONS[key] = (stamps, out)
    return out


class Sources:
    """The lists one search reads (``set_project_dir`` reads them; ``hits`` ranks them): the
    pages, the elements and the functions of a scope."""

    def __init__(self):
        self.project_dir = ""
        self.scope = "project"
        self._pages: list = []
        self._elements: list = []
        self._functions: list = []
        self._read = False

    def set_project_dir(self, project_dir: str, scope: Optional[str] = None) -> None:
        self.project_dir = project_dir or ""
        if scope in SCOPES:
            self.scope = scope
        self._read = False
        self._pages, self._elements, self._functions = [], [], []

    def _ensure(self) -> None:
        if self._read or not self.project_dir:
            return
        self._read = True
        d, s = self.project_dir, self.scope
        self._pages = page_hits(d, s)
        self._elements = (
            element_hits(d, "symbol", s)
            + element_hits(d, "genie", s)
            + element_hits(d, "template", s)
            + element_hits(d, "supergenie", s)
        )
        self._functions = function_hits(d)

    @property
    def pages(self) -> list:
        self._ensure()
        return self._pages

    def hits(
        self, q: str, kinds=tuple(KIND_ORDER), limit: Optional[int] = None, tags_built_only: bool = False
    ) -> List[tuple]:
        """``(rank, Hit, positions)`` best first (pages before symbols, genies, templates,
        super genies, tags and functions among equal matches), at most ``limit`` (None: the
        setting's rows)."""
        self._ensure()
        if limit is None:
            limit = _limits()[0]
        scored = []
        pool = []
        if "page" in kinds:
            pool += self._pages
        pool += [h for h in self._elements if h.kind in kinds]
        if "function" in kinds:
            pool += self._functions
        for h in pool:
            m = SR.match(q, h.name, 1, True) if q else (0, [])
            if m is not None:
                scored.append((m[0], KIND_ORDER[h.kind], h.name.lower(), h, m[1]))
        if "tag" in kinds and q:
            for h in tag_hits(self.project_dir, q, built_only=tags_built_only):
                m = SR.match(q, h.name, 1, True) or (90, [])
                scored.append((m[0], KIND_ORDER["tag"], h.name.lower(), h, m[1]))
        scored.sort(key=lambda t: t[:3])
        return [(t[0], t[3], t[4]) for t in scored[:limit]]


# --------------------------------------------------------------------------- the page's objects
def _strings(value, depth: int = 0, out: Optional[list] = None) -> list:
    """The text fields of an animation (expressions, tags): what '@' finds a tag in."""
    out = [] if out is None else out
    if depth > 3 or value is None:
        return out
    if isinstance(value, str):
        if value.strip() and len(value) < 300:
            out.append(value)
    elif isinstance(value, (list, tuple)):
        for v in value[:50]:
            _strings(v, depth + 1, out)
    elif isinstance(value, dict):
        for v in list(value.values())[:50]:
            _strings(v, depth + 1, out)
    elif dataclasses.is_dataclass(value):
        for f in dataclasses.fields(value):
            _strings(getattr(value, f.name, None), depth + 1, out)
    return out


@dataclasses.dataclass
class PageObject:
    """One object of the open page as '@' finds it."""

    index: int
    an: int
    name: str
    kind: str  # its kind in words ('Genie')
    kind_id: str  # ObjectKind value ('genie')
    element: str  # lib.element
    caption: str
    fields: tuple  # its tags and expressions


def object_index(doc) -> List[PageObject]:
    """The objects of ``doc``'s page from its derived model (no parse): AN, name, kind,
    library element, caption and the tags its fields name. Touch and keyboard records are
    their owner's."""
    from ...format import inputs
    from ..names import kind_name

    page = getattr(doc, "page", None)
    if page is None:
        return []
    out = []
    qualified = getattr(doc, "qualified_name", None)
    for i, o in enumerate(page.objects):
        try:
            if o.raw.get("_type_code") in inputs.INPUT_TCS and o.parent is not None:
                continue
            ref = o.genie_template or str(
                o.raw.get("_ref") or o.raw.get("_resolved_ref") or o.raw.get("_missing_element") or ""
            )
            for ext in (".<ctm", ".<ctl", ".<ctt"):
                if ref.lower().endswith(ext):
                    ref = ref[: -len(ext)]
            fields = []
            fields += [str(v) for v in o.substitutions.values() if str(v).strip()]
            fields += [str(v) for v in o.genie_params.values() if str(v).strip()]
            fields += _strings(o.animations)
            name = ""
            if qualified is not None:
                try:
                    name = qualified(i)
                except Exception:  # noqa: BLE001  # swallow-ok: an unnamed object
                    name = ""
            out.append(
                PageObject(
                    i,
                    int(o.an or 0),
                    name,
                    kind_name(o),
                    getattr(o.kind, "value", "unknown"),
                    ref,
                    " ".join((o.style.text or "").split()),
                    tuple(dict.fromkeys(fields)),
                )
            )
        except Exception:  # noqa: BLE001 -- one odd object leaves the rest listed
            logsetup.note_exception("quick_open.object_index")
    return out


class ObjectSource:
    """The open page's objects for '@', read again when the page or its model changed."""

    def __init__(self):
        self._key = None
        self._rows: List[PageObject] = []

    def objects(self, doc) -> List[PageObject]:
        key = (
            id(doc),
            getattr(doc, "derive_serial", None),
            len(getattr(getattr(doc, "page", None), "objects", ()) or ()),
        )
        if key != self._key:
            self._rows = object_index(doc) if doc is not None else []
            self._key = key
        return self._rows

    def hits(self, doc, text: str, limit: Optional[int] = None) -> list:
        """``(rank, Hit, positions, what matched)`` of the objects ``text`` finds: a number
        is an AN (that one first, then the ANs starting with it); text is found in the
        name, the kind, the library element, the caption and the tags. At most ``limit``
        (None: the setting's)."""
        q = (text or "").strip()
        if limit is None:
            limit = _limits()[2]
        out = []
        for po in self.objects(doc):
            label = po.name or po.kind
            best = None
            why = ""
            if is_digits(q):
                s = str(po.an) if po.an > 0 else ""
                if s == q:
                    best = (0, [])
                elif s.startswith(q):
                    best = (10 + len(s), [])
            if best is None and q:
                for field, score_base, what in (
                    (label, 0, ""),
                    (po.element, 40, "element"),
                    (po.caption, 60, "caption"),
                ):
                    if not field:
                        continue
                    m = SR.match(q, field, 2, True)
                    if m is not None and (best is None or m[0] + score_base < best[0]):
                        best = (m[0] + score_base, m[1] if not what else [])
                        why = "%s %s" % (what, field) if what else ""
                if best is None or best[0] > 60:
                    for f in po.fields:
                        if q.lower() in f.lower():
                            if best is None or 70 > best[0]:
                                best, why = (70, []), "tag %s" % f
                            break
            if not q:
                best = (0, [])
            if best is None:
                continue
            detail = "AN %d" % po.an if po.an > 0 else ""
            if po.name and po.kind:
                detail = ("%s  %s" % (detail, po.kind)).strip()
            if po.element:
                detail = ("%s  %s" % (detail, po.element)).strip()
            if why and not why.startswith("element"):
                detail = ("%s  %s" % (detail, why)).strip()
            out.append((best[0], po.index, Hit("object", label, detail, po.index), best[1], po))
        out.sort(key=lambda t: (t[0], t[1]))
        return [(t[0], t[2], t[3]) for t in out[:limit]]

    def by_an(self, doc, an: int) -> Optional[PageObject]:
        for po in self.objects(doc):
            if po.an == an:
                return po
        return None


def object_icon(window, hit) -> QtGui.QIcon:
    from ..style.theme import icon

    doc = getattr(window, "doc", None)
    try:
        kind = doc.page.objects[int(hit.data)].kind.value
    except AttributeError, IndexError, TypeError, ValueError:
        kind = "unknown"
    return icon(OBJECT_ICON.get(kind, "unknown"))


# --------------------------------------------------------------------------- rows and Enter
def hit_row(window, hit, positions=(), query: str = "") -> SR.Row:
    """The list row of a hit."""
    from ..style.theme import icon

    ic = object_icon(window, hit) if hit.kind == "object" else icon(KIND_ICON.get(hit.kind, "unknown"))
    return SR.Row(
        hit.kind,
        hit.name,
        " ".join(hit.detail.split()),
        hit,
        hits=positions,
        chip=KIND_LABEL.get(hit.kind, hit.kind),
        icon=ic,
        tip=hit.detail or "",
    )


#: what Enter does with each kind, for the footer
ENTER_HINT = {
    "page": "Enter opens it. Ctrl+Enter opens it in a new window",
    "tab": "Enter shows the tab",
    "symbol": "Enter places it on the page. Shift+Enter edits it",
    "genie": "Enter places it on the page. Shift+Enter edits it",
    "supergenie": "Enter places it on the page. Shift+Enter edits it",
    "template": "Enter makes a new page from it. Shift+Enter edits it",
    "tag": "Enter shows it in the Tags panel. Ctrl+C copies its name",
    "function": "Enter opens its Cicode file",
    "object": "Enter selects and shows it. Ctrl+Enter selects every object listed",
    "goto": "Enter selects and shows it",
}


def run_hit(window, h, mode: str = "default", listed=()) -> None:
    """What Enter does with a row. ``mode``: 'default' (Enter), 'edit' (Shift+Enter on an
    element: Edit Definition), 'new_window' (Ctrl+Enter on a page), 'all' (Ctrl+Enter on an
    object: every object of ``listed`` selected)."""
    w = window
    try:
        if h.kind == "page":
            if mode == "new_window" and hasattr(w, "_new_window"):
                nw = w._new_window()
                nw.open_document(h.data)
            else:
                w.open_document(h.data)
        elif h.kind == "tab":
            k = next((i for i, e in enumerate(getattr(w, "_docs", ())) if e["doc"] is h.data), -1)
            if k >= 0:
                w.show_tab(k)
        elif h.kind in ("symbol", "genie", "supergenie", "template"):
            lib, name, ext = h.data[:3]
            path = getattr(h.entry, "path", "") or ""
            if mode == "edit" and path:
                w._open_element(path, name)
            elif h.kind == "template":
                make_page_from_template(w, lib, name)
            else:
                w._arm_symbol_placement(lib, name, ext)
        elif h.kind == "tag":
            show = getattr(w, "show_tag", None) or (lambda n: show_tag(w, n))
            show(h.name)
        elif h.kind == "function":
            opener = getattr(w, "open_cicode_file", None) or (lambda p: open_cicode_file(w, p))
            opener(h.data)
        elif h.kind in ("object", "goto"):
            if mode == "all":
                select_objects(w, [x.data for x in listed if x.kind == "object"] or [h.data])
            else:
                frame = getattr(w, "_goto_frame", None)
                if frame is not None:
                    frame(int(h.data))
    except Exception:  # noqa: BLE001 -- a failed open is reported, the editor goes on
        logsetup.note_exception("quick_open.run_hit")
        w.statusBar().showMessage("Could not open %s" % h.name, 6000)


def select_objects(window, indices) -> None:
    """Select ``indices`` on the page (the Objects panel follows) and bring them into view."""
    idx = sorted({int(i) for i in indices})
    if not idx or getattr(window, "doc", None) is None:
        return
    window._on_dock_selection(idx, reveal=False)  # (framed below)
    dock = getattr(window, "objects_dock", None)
    if dock is not None:
        dock.set_selection(window._sel)
    box = window.doc.selection_extent(window._sel or idx, bounds=True)
    if box is not None:
        l, t, r, b = box
        window.view.frame_rect(QtCore.QRectF(l, t, max(1, r - l), max(1, b - t)))
    window.statusBar().showMessage("%s selected." % (plural(len(idx), "object")), 4000)


def make_page_from_template(window, style: str, element: str) -> None:
    """A new page of the current project from template ``style.element`` (named 'NewPage'
    or as Settings > Pages and libraries 'Name new pages:' says, or the first free
    'NewPage_2' ...), linked as File > New links it."""
    from ..browser.naming import unique_page_name
    from ..prefs.settings import app_settings

    pdir = window._current_project_dir()
    try:
        existing = [os.path.splitext(f)[0] for f in os.listdir(pdir) if f.lower().endswith(".ctg")]
    except OSError:
        existing = []
    existing += window.open_page_names(pdir)
    from ..prefs.window_options import new_page_name

    name = unique_page_name(new_page_name(), existing)
    linked = bool(app_settings().value("newpage/linked", True, type=bool))
    window._create_page("%s.%s.<ctt" % (style, element), linked, os.path.join(pdir, name + ".ctg"))


def show_tag(window, name: str) -> None:
    """The Tags panel shown with ``name`` as its search (the row to drag onto a genie)."""
    dock = getattr(window, "dock_tags", None)
    panel = getattr(window, "tags_dock", None)
    if dock is not None:
        dock.show()
        dock.raise_()
    if panel is not None:
        panel._filter.setText(name)
        panel._filter.flush()
        panel.focus_search()
    window.statusBar().showMessage("%s is in the Tags panel." % name, 5000)


def open_cicode_file(window, path: str) -> None:
    """The .ci file in the program Windows opens it with (Plant SCADA Studio when it is
    set up for .ci files)."""
    if not path or not os.path.isfile(path):
        window.statusBar().showMessage("The Cicode file was not found.", 6000)
        return
    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
    window.statusBar().showMessage("Opened %s" % os.path.basename(path), 4000)


def open_tab_hits(window) -> list:
    """The window's open tabs but the current one (a 'tab' hit each)."""
    out = []
    cur = getattr(window, "doc", None)
    for e in list(getattr(window, "_docs", ()) or ()):
        d = e.get("doc")
        if d is None or d is cur:
            continue
        try:
            title = window._tab_title(d)
            proj = window._doc_project(d)
        except Exception:  # noqa: BLE001  # swallow-ok: a tab being closed
            continue
        out.append(Hit("tab", title, proj, d))
    return out


# --------------------------------------------------------------------------- the popup
class QuickOpen(QtWidgets.QFrame):
    """The Quick Open popup. ``open_at(project_dir)`` shows it; Enter acts on the current
    row through the window: ``open_document(path)``, ``_arm_symbol_placement(lib, name,
    ext)``, ``show_tag(name)`` and ``open_cicode_file(path)`` (the last two are this
    module's defaults when the window has none)."""

    #: (kind, name, data) of the row Enter chose
    chosen = QtCore.Signal(str, str, object)

    WIDTH = 540
    PREVIEW_W = 196

    def __init__(self, window, opener: Optional[Callable] = None):
        super().__init__(window, QtCore.Qt.Popup)
        self.setObjectName("QuickOpen")
        self._window = window
        self._opener = opener
        self._sources = Sources()
        self._objects = ObjectSource()
        self._hits: list = []
        self._project_dir = ""
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(_sp(1))
        from ..style.theme import SearchField

        self._edit = SearchField(
            "Open a page, symbol, genie, tag or function",
            delay_ms=0,
            tip="Opens the result. p: pages, s: symbols, g: genies, tpl: templates, sg: super "
            "genies, t: tags, f: functions, @ objects of the page, : an AN, > commands. To "
            "select many objects use the Objects panel's search; Edit > Find and Replace lists "
            "each matching field",
            regex_key="Quick Open",
        )
        self._edit.setAccessibleName("Quick Open")
        top.addWidget(self._edit, 1)
        self._scope_btn = QtWidgets.QToolButton()
        self._scope_btn.setObjectName("QuickOpenScope")
        self._scope_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self._scope_btn.clicked.connect(self.toggle_scope)
        top.addWidget(self._scope_btn)
        lay.addLayout(top)
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(_sp(1))
        self._list = SR.RowList(self)
        self._list.setMaximumHeight(380)
        self._list.setFocusPolicy(QtCore.Qt.NoFocus)
        body.addWidget(self._list, 1)
        self._preview = QtWidgets.QFrame()
        self._preview.setObjectName("QuickOpenPreview")
        pl = QtWidgets.QVBoxLayout(self._preview)
        pl.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        pl.setSpacing(_sp(1))
        self._thumb = QtWidgets.QLabel()
        self._thumb.setAlignment(QtCore.Qt.AlignCenter)
        from ..browser.thumbs import CARD_SIZE

        self._thumb.setFixedSize(CARD_SIZE)
        pl.addWidget(self._thumb, 0, QtCore.Qt.AlignHCenter)
        from ..style.theme import note_label

        self._pv_name = QtWidgets.QLabel()
        self._pv_name.setWordWrap(True)
        self._pv_detail = note_label("")
        pl.addWidget(self._pv_name)
        pl.addWidget(self._pv_detail)
        pl.addStretch(1)
        self._preview.setFixedWidth(self.PREVIEW_W)
        body.addWidget(self._preview)
        lay.addLayout(body, 1)
        self._footer = note_label("")
        self._footer.setWordWrap(False)
        lay.addWidget(self._footer)
        self._edit.textChanged.connect(self._refill)
        self._edit.regex_toggle.toggled.connect(lambda _on: self._refill())
        self._edit.installEventFilter(self)
        self._list.clicked.connect(self._clicked)
        self._list.doubleClicked.connect(lambda _i: self._run_current())
        self._list.selectionModel().currentChanged.connect(self._current_changed)
        self._thumbs = None
        self._apply_qss()
        # (Settings > Ribbon > Search 'Quick Open searches first:'; Tab changes it while
        # the window lives)
        from ..prefs.window_options import quick_open_scope

        self._sources.scope = quick_open_scope()
        self._show_scope()
        from ..help import set_help

        set_help(self, "the-window#quick-open")

    # -- the look -----------------------------------------------------------------------
    @staticmethod
    def _qss() -> str:
        from ..style.theme import COLORS

        return (
            "#QuickOpen { background: %(popup)s; border: 1px solid %(border)s; "
            "border-radius: 6px; }"
            "#QuickOpenPreview { background: %(panel)s; border: 1px solid %(border)s; "
            "border-radius: 4px; }"
            "QToolButton#QuickOpenScope { color: %(text_dim)s; border: 1px solid "
            "%(control_edge)s; border-radius: 9px; padding: 1px 8px; }"
            "QToolButton#QuickOpenScope:hover { background: %(hover)s; }"
            'QToolButton#QuickOpenScope[kbdFocus="true"] { border: 1px solid %(accent)s; }' % COLORS
        )

    def _apply_qss(self) -> None:
        self.setStyleSheet(self._qss())

    def _theme_changed(self) -> None:
        """Settings > Appearance changed: the frame in the new colours."""
        self._apply_qss()
        self._list.viewport().update()

    # -- keys -----------------------------------------------------------------------------
    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt naming
        if obj is self._edit and e.type() == QtCore.QEvent.KeyPress:
            return self._key(e)
        return False

    def _key(self, e) -> bool:
        k, mods = e.key(), e.modifiers()
        ctrl = bool(mods & QtCore.Qt.ControlModifier)
        no_mods = not mods & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier)
        if k == QtCore.Qt.Key_Tab and no_mods:
            self.toggle_scope()
            return True
        if k in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Up):
            self._list.move(1 if k == QtCore.Qt.Key_Down else -1)
            return True
        if k in (QtCore.Qt.Key_PageDown, QtCore.Qt.Key_PageUp):
            self._list.move_page(1 if k == QtCore.Qt.Key_PageDown else -1)
            return True
        if k in (QtCore.Qt.Key_Home, QtCore.Qt.Key_End) and (ctrl or not self._edit.text()):
            self._list.move_end(k == QtCore.Qt.Key_End)
            return True
        if k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if mods & QtCore.Qt.ShiftModifier:
                self._run_current("edit")
            elif ctrl:
                h = self.current_hit()
                self._run_current("all" if h is not None and h.kind == "object" else "new_window")
            else:
                self._run_current()
            return True
        if ctrl and k == QtCore.Qt.Key_C and not self._edit.hasSelectedText():
            self.copy_current()
            return True
        return False

    def keyPressEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if not self._key(e):
            super().keyPressEvent(e)

    # -- the sources ----------------------------------------------------------------
    def set_project_dir(self, project_dir: str) -> None:
        """Read the lists of ``project_dir`` (the page's folder)."""
        self._project_dir = project_dir or ""
        self._sources.set_project_dir(self._project_dir)

    @property
    def scope(self) -> str:
        return self._sources.scope

    def toggle_scope(self) -> None:
        """Tab: this project and its includes, or every project of the install (kept for
        Settings > Ribbon > Search 'Quick Open searches first: The one used last')."""
        new = "install" if self._sources.scope == "project" else "project"
        self._sources.set_project_dir(self._project_dir, new)
        from ..prefs.window_options import remember_quick_open_scope

        remember_quick_open_scope(new)
        self._show_scope()
        self._refill()

    def set_scope(self, scope: str) -> None:
        """Search ``scope`` ('project' / 'install') from now on (Settings: the scope Quick
        Open starts with)."""
        if scope in SCOPES and scope != self._sources.scope:
            self._sources.set_project_dir(self._project_dir, scope)
            self._show_scope()
            if self.isVisible():
                self._refill()

    def _show_scope(self) -> None:
        s = self._sources.scope
        self._scope_btn.setText(SCOPE_LABEL[s])
        self._scope_btn.setToolTip(SCOPE_TIP[s])
        self._scope_btn.setAccessibleName("Scope: %s" % SCOPE_LABEL[s])

    def hits(self, text: str) -> list:
        """The rows ``text`` finds, best first (pages before symbols, genies, tags and
        functions among equal matches), at most the setting's rows (no headers)."""
        return [h for h, _pos in self._ranked(text)]

    def _ranked(self, text: str) -> list:
        """``(Hit, positions)`` of ``text``."""
        mode = query_mode(text)
        doc = getattr(self._window, "doc", None)
        if mode == "objects":
            return [(h, pos) for _s, h, pos in self._objects.hits(doc, text.lstrip()[1:])]
        if mode == "goto":
            return [(h, pos) for h, pos in goto_hits(self._window, self._objects, text)]
        if mode == "palette":
            return []
        kinds, q = split_query(text)
        if self._edit.regex and q:
            m = self._edit.matcher(q)
            if not m.ok:
                return []
            out = []
            rows = _limits()[0]
            for _s, h, _p in self._sources.hits("", kinds, limit=100000):
                if m(h.name):
                    out.append((h, []))
                    if len(out) >= rows:
                        break
            return out
        if not q and kinds == tuple(KIND_ORDER):
            return [(h, []) for _hd, h in self._empty_rows()]
        return [(h, pos) for _s, h, pos in self._sources.hits(q, kinds)]

    def _empty_rows(self) -> list:
        """Nothing typed: (section, Hit) of the open tabs, the recent pages, then the pages
        of the project (each page once)."""
        out = []
        seen = set()
        for h in open_tab_hits(self._window):
            out.append(("Open tabs", h))
            p = getattr(h.data, "path", "")
            if p:
                seen.add(path_key(p))
        cur = getattr(getattr(self._window, "doc", None), "path", "") or ""
        cur_key = path_key(cur) if cur else ""
        recent = []
        try:
            recent = [path_key(p) for p in self._window.recent_pages()]
        except Exception:  # noqa: BLE001  # swallow-ok: no recent list, the pages as they are
            pass
        pages = self._sources.pages
        by_path = {path_key(h.data): h for h in pages}
        for p in recent:
            h = by_path.get(p)
            if h is not None and p not in seen and p != cur_key:
                out.append(("Recent", h))
                seen.add(p)
        most = _limits()[0] * 4
        for h in sorted(pages, key=lambda h: h.name.lower()):
            p = path_key(h.data)
            if p in seen:
                continue
            seen.add(p)
            out.append(("Pages of the project", h))
            if len(out) >= most:
                break
        return out

    # -- showing ----------------------------------------------------------------------
    def open_at(self, project_dir: str, global_pos=None, prefill: str = "") -> None:
        self.set_project_dir(project_dir)
        self._edit.setText(prefill)
        self._refill()
        w = self._window.width() if self._window is not None else 0
        show_pv = w <= 0 or w >= 900
        self._preview.setVisible(show_pv)
        self.setFixedWidth(self.WIDTH + (self.PREVIEW_W + 6 if show_pv else 0))
        self.adjustSize()
        if global_pos is None:
            g = self._window.geometry()
            global_pos = QtCore.QPoint(g.center().x() - self.width() // 2, g.top() + 120)
        self.move(global_pos)
        self.show()
        self._edit.setFocus()

    def _refill(self) -> None:
        q = self._edit.text()
        mode = query_mode(q)
        if mode == "palette":
            QtCore.QTimer.singleShot(0, self, self._hand_over)
            return
        rows: list = []
        self._hits = []
        kinds, words = split_query(q)
        if mode == "find" and not words and kinds == tuple(KIND_ORDER):
            last = None
            for section, h in self._empty_rows():
                if section != last:
                    rows.append(SR.header(section))
                    last = section
                rows.append(hit_row(self._window, h))
                self._hits.append(h)
        else:
            for h, pos in self._ranked(q):
                rows.append(hit_row(self._window, h, pos, words))
                self._hits.append(h)
        if not self._hits:
            rows = [SR.note(self._empty_text(q, mode, kinds, words))]
        self._list.set_rows(rows)
        self._fit_list()
        self._current_changed()

    def _empty_text(self, q, mode, kinds, words) -> str:
        if mode == "objects":
            return (
                "Open a page first: @ searches its objects"
                if getattr(self._window, "doc", None) is None
                else 'No object matches "%s"' % q.lstrip()[1:].strip()
            )
        if mode == "goto":
            # (the palette's words: an AN typed that no object has is not 'type an AN')
            return goto_note(self._window, q.lstrip()[1:].strip())
        if self._edit.regex and words and not self._edit.matcher(words).ok:
            return "Not a regular expression: %s" % self._edit.matcher(words).error
        if q.strip():
            what = "page, symbol, genie, tag or function" if len(kinds) > 1 else KIND_LABEL[kinds[0]]
            return 'No %s matches "%s"' % (what, words or q)
        if not self._project_dir:
            return "Open a page first: Quick Open searches its project"
        return "No pages found"

    def _fit_list(self) -> None:
        n = self._list.count()
        h = 0
        dl = self._list.itemDelegate()
        for k in range(min(n, 14)):
            r = self._list.rows_model.row_at(k)
            h += dl.header_height() if r is not None and r.kind == "header" else dl.row_height()
        self._list.setFixedHeight(max(SR.RowDelegate.row_height(), min(380, h + 4)))

    def _hand_over(self) -> None:
        """'>', '?' or 'set:': the command palette takes the text."""
        text = self._edit.text()
        if query_mode(text) != "palette":
            return
        pal = getattr(self._window, "palette_popup", None)
        if pal is None:
            return
        pos = self.pos()
        self.hide()
        pal.open_at(pos, prefill=text.lstrip())

    def current_hit(self):
        r = self._list.current_row_obj()
        return r.data if r is not None and r.selectable else None

    def _current_changed(self, *_a) -> None:
        h = self.current_hit()
        self._footer.setText(ENTER_HINT.get(h.kind, "") if h is not None else "")
        self._show_preview(h)

    def _show_preview(self, h) -> None:
        if not self._preview.isVisible() and self.isVisible():
            return
        self._pv_name.setText(h.name if h is not None else "")
        self._pv_detail.setText(" ".join(h.detail.split()) if h is not None else "")
        entry = getattr(h, "entry", None) if h is not None else None
        pm = None
        if entry is not None:
            try:
                svc = self._thumb_service()
                pm = svc.request(entry, "card")
            except Exception:  # noqa: BLE001  # swallow-ok: the preview is decoration
                pm = None
        if pm is not None and not pm.isNull():
            self._thumb.setPixmap(
                pm.scaled(self._thumb.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            )
            self._thumb.setText("")
        else:
            self._thumb.setPixmap(QtGui.QPixmap())
            self._thumb.setText("No preview" if entry is not None else "")

    def _thumb_service(self):
        if self._thumbs is None:
            from ..browser.thumbs import ThumbnailService

            self._thumbs = ThumbnailService.shared()
            self._thumbs.thumbnailReady.connect(self._thumb_ready)
        return self._thumbs

    def _thumb_ready(self, key: str) -> None:
        h = self.current_hit()
        entry = getattr(h, "entry", None) if h is not None else None
        if entry is not None and getattr(entry, "key", None) == key:
            self._show_preview(h)

    def _clicked(self, index) -> None:
        r = index.data(SR.ROW_ROLE)
        if r is not None and r.selectable:
            self._list.setCurrentRow(index.row())

    def listed(self) -> list:
        return list(self._hits)

    def copy_current(self) -> None:
        """Ctrl+C on a row: its name on the clipboard."""
        h = self.current_hit()
        if h is None:
            return
        QtWidgets.QApplication.clipboard().setText(h.name)
        self._footer.setText("%s is on the clipboard" % h.name)

    def _run_current(self, mode: str = "default") -> None:
        h = self.current_hit()
        if h is None:
            return
        listed = self.listed()
        self.hide()
        self.chosen.emit(h.kind, h.name, h.data)
        if self._opener is not None:
            run = self._opener
            QtCore.QTimer.singleShot(0, self, lambda: run(h))
            return
        QtCore.QTimer.singleShot(0, self, lambda: run_hit(self._window, h, mode, listed))

    # -- Enter ------------------------------------------------------------------------
    def run_hit(self, h, mode: str = "default") -> None:
        """What Enter does with a row (see :func:`run_hit`)."""
        run_hit(self._window, h, mode, self.listed())


def goto_note(window, an: str) -> str:
    """What ':' shows when no object is found: ``an`` is the text after the colon."""
    if not is_digits(an):
        return "Type an AN after the colon"
    if getattr(window, "doc", None) is None:
        return "Open a page first: ':' goes to an object by its AN"
    return "No object has AN %s" % an


def goto_hits(window, objects: ObjectSource, text: str) -> list:
    """':581': the object with AN 581 (one row), or nothing."""
    q = (text or "").strip()[1:].strip()
    doc = getattr(window, "doc", None)
    if not is_digits(q) or doc is None:
        return []
    po = objects.by_an(doc, int(q))
    if po is None:
        return []
    name = po.name or po.kind
    detail = "%s  %s" % (po.kind, po.element) if po.element else po.kind
    return [(Hit("goto", "Go to AN %d: %s" % (po.an, name), detail.strip(), po.index), [])]
