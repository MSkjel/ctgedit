"""The browser's item model, with filtering and sorting."""

from __future__ import annotations

from ...infra.paths import path_key

import collections
import datetime
from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui

from ...infra.textmatch import TextMatch
from ...io.catalog import Entry, natural_key
from ..panels import panel_options as _panels
from . import search as _search
from .thumbs import CARD_SIZE

# Qt's values the model reads for every row and role, looked up once (a QtCore.Qt attribute
# lookup is slow, and the views ask a long listing for its flags many times)
_DISPLAY_ROLE = QtCore.Qt.DisplayRole
_DECORATION_ROLE = QtCore.Qt.DecorationRole
_TOOLTIP_ROLE = QtCore.Qt.ToolTipRole
_FOREGROUND_ROLE = QtCore.Qt.ForegroundRole
_NO_FLAGS = QtCore.Qt.NoItemFlags
_ROW_FLAGS = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable

EntryRole = QtCore.Qt.UserRole
CardPixmapRole = QtCore.Qt.UserRole + 1
BadgesRole = QtCore.Qt.UserRole + 2
KeyRole = QtCore.Qt.UserRole + 3
SubtitleRole = QtCore.Qt.UserRole + 4
ThumbStateRole = QtCore.Qt.UserRole + 5
ResolutionRole = QtCore.Qt.UserRole + 6

COL_NAME, COL_TITLE, COL_PROJECT, COL_TEMPLATE, COL_SIZE, COL_OBJECTS, COL_MODIFIED, COL_STATUS = range(8)
_HEADERS_PAGE = ["Name", "Title", "Project", "Template", "Size", "Objects", "Modified", "Registered"]
_HEADERS_ELEM = ["Name", "Library", "Project", "Resolution", "Size", "Objects", "Modified", "Status"]

COL_SORT = {
    COL_NAME: "name",
    COL_TITLE: "title",
    COL_PROJECT: "project",
    COL_TEMPLATE: "template",
    COL_SIZE: "size",
    COL_OBJECTS: "objects",
    COL_MODIFIED: "modified",
    COL_STATUS: "status",
}

from ..style.theme import COLORS  # noqa: E402


def dominant_prefix(names: List[str], share: float = 0.5, min_len: int = 8) -> str:
    """The longest ``'_'``-terminated prefix shared by >= ``share`` of ``names``
    (at least ``min_len`` chars) -- e.g. ``'Plant_Area_'`` -- so cards
    can fold it and show the part that tells pages apart. ``''`` if none."""
    if len(names) < 4:
        return ""
    cnt: "collections.Counter[str]" = collections.Counter()
    for n in names:
        seen = set()
        for i, c in enumerate(n):
            if c == "_" and i + 1 >= min_len and i + 1 < len(n):
                p = n[: i + 1]
                if p not in seen:
                    seen.add(p)
                    cnt[p] += 1
    need = max(2, int(len(names) * share + 0.9999))
    best = ""
    for p, k in cnt.items():
        if k >= need and len(p) > len(best):
            best = p
    return best


class CatalogModel(QtCore.QAbstractTableModel):
    """Table model over catalog entries; filter + sort done in Python."""

    countsChanged = QtCore.Signal(int, int, int)  # shown, total, hidden unregistered

    def __init__(self, thumbs=None, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        self.thumbs = thumbs
        self.kind = "page"
        self._all: List[Entry] = []
        self._rows: List[int] = []
        self._pos: Dict[str, int] = {}  # entry key -> index into _rows
        self._query = ""
        self._regex = False  # the search box's '.*': the query is one expression
        self.hide_unregistered = False
        self.popups = "any"  # any | hide | only
        self.sort_key = "name"
        self.sort_desc = False
        self.common_prefix = ""
        self.fuzzy_used = False
        self.hidden_unregistered = 0
        self.unregistered_total = 0
        self.keep_order = False
        #: the entries come from more than one project (cards name the project)
        self.spans_projects = False
        #: the grid's card width: above CARD_SIZE the cards use the full thumbnail
        self.card_width = CARD_SIZE.width()
        self._preps: List[tuple] = []  # element search fields, per entry
        self._rank: Dict[int, tuple] = {}  # entry index -> element rank (a query)
        # summaries land in the background; re-apply a filter / sort that uses
        # them (tpl: / res: / template / size / objects), coalesced
        self._resummarise = QtCore.QTimer(self)
        self._resummarise.setSingleShot(True)
        self._resummarise.setInterval(250)
        self._resummarise.timeout.connect(self._apply_summaries)
        if thumbs is not None:
            thumbs.thumbnailReady.connect(self._on_thumb)
            thumbs.summaryReady.connect(self._on_summary)

    # -- population -----------------------------------------------------------
    def set_entries(self, entries: List[Entry], kind: str, keep_order: bool = False) -> None:
        """A new scope. ``keep_order``: the default (name) sort keeps the given
        order instead -- the Recent list stays most-recent-first."""
        self.beginResetModel()
        self._all = list(entries)
        self.kind = kind
        self.keep_order = keep_order
        self._preps = [] if kind == "page" else [_search.prep(e) for e in self._all]
        self.spans_projects = len({(e.project_dir or e.project).lower() for e in self._all}) > 1
        # (Settings > Pages and libraries > Open and Save As > Shorten a name start most
        # pages share: panel_options.SHORT_NAMES)
        self.common_prefix = (
            dominant_prefix([e.name for e in self._all]) if kind == "page" and _panels.SHORT_NAMES else ""
        )
        self.unregistered_total = sum(1 for e in self._all if e.registered is False)
        self._filter()
        self._sort_rows()
        self._reindex()
        self.endResetModel()
        self._emit_counts()

    def set_query(self, text: str, regex: bool = False) -> None:
        """The search: words that must all match (with tpl: / res: / proj: fields), or with
        ``regex`` (the box's '.*') one regular expression found in the names, titles,
        comments, areas, projects and libraries, without case."""
        text = (text or "").strip()
        if (text, bool(regex)) == (self._query, self._regex):
            return
        self._query, self._regex = text, bool(regex)
        self._refilter()

    @property
    def query(self) -> str:
        return self._query

    def set_facets(self, hide_unregistered: Optional[bool] = None, popups: Optional[str] = None) -> None:
        changed = False
        if hide_unregistered is not None and bool(hide_unregistered) != self.hide_unregistered:
            self.hide_unregistered = bool(hide_unregistered)
            changed = True
        if popups is not None and popups != self.popups:
            self.popups = popups
            changed = True
        if changed:
            self._refilter()

    def set_sort(self, key: str, descending: bool = False) -> None:
        if key == self.sort_key and descending == self.sort_desc:
            return
        self.sort_key, self.sort_desc = key, descending
        self.layoutAboutToBeChanged.emit()
        old = self.persistentIndexList()
        old_keys = [
            self._all[self._rows[i.row()]].key if i.isValid() and i.row() < len(self._rows) else None
            for i in old
        ]
        self._sort_rows()
        self._reindex()
        new = []
        for i, k in zip(old, old_keys):
            r = self._pos.get(k) if k is not None else None
            new.append(self.index(r, i.column()) if r is not None else QtCore.QModelIndex())
        self.changePersistentIndexList(old, new)
        self.layoutChanged.emit()

    def sort(self, column: int, order=QtCore.Qt.AscendingOrder) -> None:  # noqa: D401 (Qt API)
        key = COL_SORT.get(column, "name")
        self.set_sort(key, order == QtCore.Qt.DescendingOrder)

    def _refilter(self) -> None:
        self.beginResetModel()
        self._filter()
        self._sort_rows()
        self._reindex()
        self.endResetModel()
        self._emit_counts()

    # -- filter / sort -----------------------------------------------------------
    def _facet_ok(self, e: Entry, popups: Optional[str] = None) -> bool:
        popups = popups or self.popups
        if e.kind == "page":
            if popups == "hide" and e.is_popup:
                return False
            if popups == "only" and not e.is_popup:
                return False
        return True

    def _template_text(self, e: Entry) -> str:
        if e.kind == "template":
            return e.library.lower()
        s = self.thumbs.summary(e) if self.thumbs is not None else None
        t = getattr(s, "template", None)
        return t.reference.lower() if t is not None else ""

    def _resolution(self, e: Entry) -> str:
        if e.kind == "template":
            return e.resolution
        s = self.thumbs.summary(e) if self.thumbs is not None else None
        t = getattr(s, "template", None)
        return t.resolution if t is not None else ""

    def _parse_query(self):
        toks, fields, popup_only = [], [], False
        for t in self._query.lower().split():
            if t == "!":
                popup_only = True
            elif ":" in t and t.split(":", 1)[0] in ("tpl", "res", "proj") and t.split(":", 1)[1]:
                f, v = t.split(":", 1)
                fields.append((f, v))
            else:
                toks.append(t)
        return toks, fields, popup_only

    def _field_ok(self, e: Entry, fields) -> bool:
        for f, v in fields:
            if f == "proj" and v not in e.project.lower():
                return False
            if f == "tpl" and v not in self._template_text(e):
                return False
            if f == "res" and v not in self._resolution(e).lower():
                return False
        return True

    def _filter(self) -> None:
        regex = TextMatch(self._query, True) if self._regex and self._query else None
        toks, fields, popup_only = ([], {}, False) if regex is not None else self._parse_query()
        # a search that asks for '!' pages ('!', or a word starting with '!')
        # finds them also while they are hidden (the default with Settings >
        # List system pages off): the user named them
        popups = self.popups
        if popups == "hide" and (popup_only or any(t.startswith("!") for t in toks)):
            popups = "any"
        base = []
        hidden = 0
        for i, e in enumerate(self._all):
            if not self._facet_ok(e, popups):
                continue
            if popup_only and not e.is_popup:
                continue
            if fields and not self._field_ok(e, fields):
                continue
            base.append(i)
        self._rank = {}
        self.fuzzy_used = False
        if regex is not None:
            rows = [i for i in base if regex(self._all[i].haystack, self._all[i].name)]
            self.hidden_unregistered = 0
            if self.hide_unregistered:
                kept = [i for i in rows if self._all[i].registered is not False]
                self.hidden_unregistered = len(rows) - len(kept)
                rows = kept
            self._rows = rows
            return
        if toks and self.kind != "page" and self._preps:
            # library elements: ranked like the Library panel
            for i in base:
                r = _search.score_tokens(self._preps[i], toks)
                if r is not None:
                    self._rank[i] = r
            self.fuzzy_used = bool(self._rank) and all(r[0] >= 7 for r in self._rank.values())
            self._rows = [i for i in base if i in self._rank]
            self.hidden_unregistered = 0
            return
        rows = [i for i in base if all(t in self._all[i].haystack for t in toks)]
        if toks and not rows and _search.FUZZY:
            # (the letters in order of a name, when nothing has the words: Settings >
            # Pages and libraries > Searching)
            cand = [t for t in toks if len(t) >= 3]
            if cand:
                rows = [
                    i
                    for i in base
                    if all(
                        t in self._all[i].haystack
                        or (len(t) >= 3 and _search.subsequence(t, self._all[i].name.lower()))
                        for t in toks
                    )
                ]
                self.fuzzy_used = bool(rows)
        if self.hide_unregistered:
            kept = []
            for i in rows:
                if self._all[i].registered is False:
                    hidden += 1
                else:
                    kept.append(i)
            rows = kept
        self._rows = rows
        self.hidden_unregistered = hidden

    def _sort_value(self, e: Entry, key: str):
        if key == "modified":
            return e.mtime_ns
        if key == "title":
            return ((e.title or e.library).lower(), natural_key(e.name))
        if key == "project":
            return (e.project.lower(), natural_key(e.name))
        if key == "template":
            return (self._template_text(e), natural_key(e.name))
        if key in ("size", "objects"):
            s = self.thumbs.summary(e) if self.thumbs is not None else None
            if key == "size":
                return ((getattr(s, "width", 0) or 0) * (getattr(s, "height", 0) or 0), natural_key(e.name))
            return (getattr(s, "object_count", 0) or 0, natural_key(e.name))
        if key == "status":
            return (0 if e.registered is not False else 1, bool(e.shadowed_by), natural_key(e.name))
        # name; same-named elements (one per library copy) tie-break by library
        if e.is_element:
            return (natural_key(e.name), e.library.lower())
        return natural_key(e.name)

    def _sort_rows(self) -> None:
        key = self.sort_key
        desc = self.sort_desc
        if self.keep_order and key == "name" and not desc:
            self._rows.sort()
            return
        if self._rank and key == "name" and not desc:
            # a search over library elements: the best match first
            self._rows.sort(key=lambda i: (self._rank[i], self._sort_value(self._all[i], "name")))
            return
        if key == "modified":
            desc = not desc  # newest first is the natural "modified" order
        self._rows.sort(key=lambda i: self._sort_value(self._all[i], key), reverse=desc)

    def _reindex(self) -> None:
        self._pos = {self._all[i].key: r for r, i in enumerate(self._rows)}

    def _emit_counts(self) -> None:
        self.countsChanged.emit(len(self._rows), len(self._all), self.hidden_unregistered)

    # -- access ------------------------------------------------------------------
    def entry(self, row: int) -> Optional[Entry]:
        if 0 <= row < len(self._rows):
            return self._all[self._rows[row]]
        return None

    def entries(self) -> List[Entry]:
        """The visible (filtered, sorted) entries."""
        return [self._all[i] for i in self._rows]

    def all_entries(self) -> List[Entry]:
        return list(self._all)

    @property
    def total(self) -> int:
        """Entries in the scope (before filtering)."""
        return len(self._all)

    def row_of(self, entry_or_key) -> int:
        """Row of an Entry / entry key / file path (``-1`` when not shown)."""
        if isinstance(entry_or_key, Entry):
            return self._pos.get(entry_or_key.key, -1)
        k = str(entry_or_key)
        if k in self._pos:
            return self._pos[k]
        norm = path_key(k) if k else ""
        for r, i in enumerate(self._rows):
            e = self._all[i]
            if e.kind == "page" and path_key(e.path) == norm:
                return r
        if norm.endswith((".ctt", ".ctl", ".ctm")):
            # a library file: its first element shown
            for r, i in enumerate(self._rows):
                e = self._all[i]
                if e.is_element and path_key(e.path) == norm:
                    return r
        return -1

    def find(self, name: str, library: str = "") -> int:
        """Row of the first visible entry named ``name`` (case-insensitive)."""
        low, lib = name.lower(), library.lower()
        for r, i in enumerate(self._rows):
            e = self._all[i]
            if e.name.lower() == low and (not lib or e.library.lower() == lib):
                return r
        return -1

    # -- Qt model API --------------------------------------------------------------
    def rowCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_HEADERS_PAGE)

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole:
            heads = _HEADERS_PAGE if self.kind == "page" else _HEADERS_ELEM
            if 0 <= section < len(heads):
                return heads[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return _NO_FLAGS
        return _ROW_FLAGS

    def _summary(self, e: Entry):
        return self.thumbs.summary(e) if self.thumbs is not None else None

    def display(self, e: Entry, col: int) -> str:
        """The text of cell (``e``, ``col``) -- also used by the card delegate."""
        if col == COL_NAME:
            return e.name
        if col == COL_TITLE:
            return e.title if e.kind == "page" else e.library
        if col == COL_PROJECT:
            return e.project
        if col == COL_TEMPLATE:
            if e.kind == "template":
                res = e.resolution
                if e.title_bar:
                    return res
                return (res + ", no title bar") if res else "No title bar"
            if e.kind != "page":
                return ""
            s = self._summary(e)
            t = getattr(s, "template", None)
            return t.reference if t is not None else ""
        if col == COL_SIZE:
            s = self._summary(e)
            w, h = getattr(s, "width", 0), getattr(s, "height", 0)
            return "%d × %d" % (w, h) if w and h else ""
        if col == COL_OBJECTS:
            s = self._summary(e)
            n = getattr(s, "object_count", None)
            return str(n) if s is not None and n is not None else ""
        if col == COL_MODIFIED:
            if not e.mtime_ns:
                return ""
            return datetime.datetime.fromtimestamp(e.mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M")
        if col == COL_STATUS:
            if e.kind == "page":
                if e.registered is None:
                    return ""
                return "Yes" if e.registered else "No"
            return ("Hidden by %s" % e.shadowed_by) if e.shadowed_by else ""
        return ""

    def subtitle(self, e: Entry) -> str:
        """The card's second line: the page title (else comment) or the
        element's library; ``'Project · ...'`` when the scope spans projects."""
        if e.kind == "page":
            # (a title that only repeats the name says nothing: the comment then)
            title = e.title if e.title and e.title.strip().lower() != e.name.lower() else ""
            sub = title or e.comment or ""
        elif e.kind == "template":
            res = self.display(e, COL_TEMPLATE)
            sub = "%s · %s" % (e.library, res) if res else e.library
        else:
            sub = e.library
        if self.spans_projects:
            return "%s · %s" % (e.project, sub) if sub else e.project
        return sub

    def badges(self, e: Entry) -> List[str]:
        out = []
        if e.kind == "page":
            if e.registered is False:
                out.append("unregistered")
            if e.is_popup:
                out.append("popup")
        if e.shadowed_by:
            out.append("shadowed")
        if e.is_other_install:
            out.append("other_install")
        return out

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        e = self.entry(index.row())
        if e is None:
            return None
        col = index.column()
        if role == _DISPLAY_ROLE:
            return self.display(e, col)
        if role == EntryRole:
            return e
        if role == KeyRole:
            return e.key
        if role == CardPixmapRole:
            if self.thumbs is None:
                return None
            if self.card_width > CARD_SIZE.width():
                # bigger cards than the prepared ones: the full thumbnail
                full = self.thumbs.request_full(e)
                if full is not None:
                    return full
            return self.thumbs.request(e, "card")
        if role == ThumbStateRole:
            return self.thumbs.state(e) if self.thumbs is not None else "none"
        if role == BadgesRole:
            return self.badges(e)
        if role == ResolutionRole:
            if e.kind in ("page", "template"):
                return self._resolution(e)
            return ""
        if role == SubtitleRole:
            return self.subtitle(e)
        if role == _DECORATION_ROLE and col == COL_NAME:
            if self.thumbs is None:
                return None
            return self.thumbs.request(e, "row")
        if role == _TOOLTIP_ROLE:
            tip = e.reference if e.is_element else e.name
            if e.title:
                tip += "\n" + e.title
            tip += "\n" + e.project
            if e.kind == "page" and e.registered is False:
                tip += "\nNot registered in pages.DBF"
            if e.shadowed_by:
                tip += "\nHidden by the copy in %s" % e.shadowed_by
            return tip + "\n" + e.path
        if role == _FOREGROUND_ROLE:
            if e.kind == "page" and e.registered is False:
                return QtGui.QColor(COLORS["warn"])
            if e.shadowed_by or e.is_other_install:
                return QtGui.QColor(COLORS["text_dim"])
        return None

    # -- thumbnail service ---------------------------------------------------------
    def _on_thumb(self, key: str) -> None:
        r = self._pos.get(key)
        if r is not None:
            self.dataChanged.emit(self.index(r, 0), self.index(r, len(_HEADERS_PAGE) - 1))

    def needs_summaries(self) -> bool:
        """True when the active filter / sort depends on background summaries."""
        _toks, fields, _pop = self._parse_query()
        return self.sort_key in ("template", "size", "objects") or any(
            f in ("tpl", "res") for f, _v in fields
        )

    def _on_summary(self, key: str, _summary) -> None:
        r = self._pos.get(key)
        if r is not None:
            self.dataChanged.emit(self.index(r, COL_TEMPLATE), self.index(r, COL_OBJECTS))
        if self.kind == "page" and self.needs_summaries() and not self._resummarise.isActive():
            self._resummarise.start()

    def _apply_summaries(self) -> None:
        _toks, fields, _pop = self._parse_query()
        if any(f in ("tpl", "res") for f, _v in fields):
            self._refilter()
        elif self.sort_key in ("template", "size", "objects"):
            key, desc = self.sort_key, self.sort_desc
            self.sort_key = ""  # force set_sort to re-sort
            self.set_sort(key, desc)
