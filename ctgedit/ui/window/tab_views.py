"""A page's first view: where it was left, or fitted."""

from __future__ import annotations

from ...infra.paths import path_key

from ..main_window_base import (
    ElementDocument,
    QtCore,
    app_settings,
    contextlib,
)
from ..main_window_base import (
    PendingPage,
)


class TabViews:
    """Where a page's view starts when it opens, and where it was left."""

    # -- the first view of a page: where it was left, or fitted at most at the cap ----------
    def _open_zoom_mode(self) -> str:
        mode = self.__dict__.get("_open_zoom")
        if mode is None:
            from ..prefs.settings import OPEN_ZOOM, OPEN_ZOOM_DEFAULT, choice

            mode = choice("opt/open_zoom", OPEN_ZOOM, OPEN_ZOOM_DEFAULT)
        return mode

    def _first_view(self, entry: dict) -> None:
        """A tab shown for the first time: at the zoom and place the page was left at
        (Settings > Canvas 'Where I left it'; the page's size must be the same), at 100 %,
        or fitted -- at most at the largest opening zoom."""
        doc = entry["doc"]
        frame = entry.pop("frame", False)
        mode = self._open_zoom_mode()
        moved = entry.pop("moved_view", None)
        if moved is not None:
            entry["view_restored"] = True
            self._show_view_at(doc, *moved)
            return
        if mode == "last":
            saved = self._saved_view(doc)
            if saved is not None:
                entry["view_restored"] = True
                self._show_view_at(doc, *saved)
                return
            sv = entry.pop("session_view", None)
            if sv is not None:
                entry["view_restored"] = True
                self.view.restore_view(*sv)
                return
        if mode == "100":
            self.view.open_actual_size()
        else:
            self._fit_capped(isinstance(doc, ElementDocument))
        if frame and isinstance(doc, ElementDocument):
            # a restored library element: framed on its objects once the canvas has its
            # size, as files._open_element frames one opened from the library
            QtCore.QTimer.singleShot(
                0,
                self,
                lambda d=doc: self._when_laid_out(lambda: self._frame_content() if self.doc is d else None),
            )

    def _when_laid_out(self, fn, tries: int = 40) -> None:
        """Run ``fn`` once the canvas is shown and has its size (a page opened before the
        window shows waits for it)."""
        vp = self.view.viewport()
        if self.view.isVisible() and vp.width() > 10 and vp.height() > 10:
            fn()
            return
        if tries > 0:
            QtCore.QTimer.singleShot(
                0 if tries > 20 else 25, self, lambda: self._when_laid_out(fn, tries - 1)
            )

    def _show_view_at(self, doc, zoom: float, centre) -> None:
        def go():
            if self.doc is not doc:
                return
            self.view.set_zoom(float(zoom))
            self.view.centerOn(QtCore.QPointF(float(centre[0]), float(centre[1])))

        self._when_laid_out(go)

    def _fit_zoom(self) -> float:
        """The zoom Fit Page gives in the canvas now (0: no page)."""
        sc = self.view.scene()
        page = sc.page_rect() if sc is not None and hasattr(sc, "page_rect") else None
        if page is None or page.isNull():
            return 0.0
        from ..editor import viewnav

        return viewnav.fit_zoom(page, self.view.maximumViewportSize())

    def _fit_capped(self, element: bool = False) -> None:
        """Fit Page for a page shown the first time, at most at Settings > Canvas 'Largest
        zoom when a page opens'. While the window settles (the fit follows its size) the cap
        is kept."""
        from ..panels.doc_tabs import open_zoom_cap

        self.view.fit()
        cap = open_zoom_cap(element)
        if not cap:
            return
        self._cap_arm = (self.doc, cap)
        if not self.__dict__.get("_cap_hooked"):
            self._cap_hooked = True
            self.view.zoomChanged.connect(lambda _z: self._apply_cap())
        self._when_laid_out(self._apply_cap)

    def _apply_cap(self) -> None:
        arm = self.__dict__.get("_cap_arm")
        if not arm or arm[0] is not self.doc:
            return
        cap = arm[1]
        z = self.view.zoom()
        fit = self._fit_zoom()
        if fit <= 0:
            return
        if abs(z - fit) > 0.01 * max(fit, 1e-6):
            if abs(z - cap) > 1e-6 and self.view.isVisible():
                self._cap_arm = None  # the user zoomed: theirs from now on
            return
        if z <= cap * 1.0001:
            return  # (fits under the cap: auto-fit goes on)
        self._cap_arm = None
        sc = self.view.scene()
        page = sc.page_rect()
        self.view.set_zoom(cap)
        self.view.centerOn(page.center())

    # -- 'where I left it': view/last/<path> --------------------------------------------------
    #: the pages whose last view is kept (the oldest goes): the default of Settings > Canvas >
    #: Opening a page > Remember the view of at most (ui/prefs/timing_options.LAST_VIEWS_KEPT)
    VIEW_LAST_MAX = 200

    @staticmethod
    def _view_key(doc) -> str:
        key = path_key(doc.path)
        el = doc.element_name if isinstance(doc, ElementDocument) else getattr(doc, "element", "")
        if el:
            key += "|" + el.lower()
        return "view/last/" + key.replace("\\", "/")

    @staticmethod
    def _page_size(doc):
        try:
            ap = doc.page.appearance
            pb = doc.parsed.page
            return (
                int(ap.width or getattr(pb, "page_width", 0) or 0),
                int(ap.height or getattr(pb, "page_height", 0) or 0),
            )
        except AttributeError:
            return (0, 0)

    def _remember_view(self, entry: dict) -> None:
        """Keep where tab ``entry`` was left (its zoom, the page point at the view's centre and
        the page's size), for the next time the page opens."""
        doc = entry["doc"]
        if isinstance(doc, PendingPage) or not getattr(doc, "path", ""):
            return
        view = self._entry_view(entry)
        if view is None:
            return
        import json
        import time

        z, (x, y) = view
        w, h = self._page_size(doc)
        st = app_settings()
        st.setValue(
            self._view_key(doc),
            json.dumps(
                {
                    "z": round(float(z), 6),
                    "x": round(float(x), 2),
                    "y": round(float(y), 2),
                    "w": w,
                    "h": h,
                    "t": round(time.time(), 3),
                }
            ),
        )
        self._prune_views(st)

    def _prune_views(self, st) -> None:
        import json
        from ..prefs import timing_options

        keep = timing_options.LAST_VIEWS_KEPT
        st.beginGroup("view/last")
        try:
            keys = st.allKeys()
            if len(keys) <= keep:
                return
            stamped = []
            for k in keys:
                try:
                    stamped.append((float(json.loads(str(st.value(k))).get("t", 0)), k))
                except ValueError, TypeError, AttributeError:  # swallow-ok: garbled = oldest
                    stamped.append((0.0, k))
            stamped.sort()
            for _t, k in stamped[: len(keys) - keep]:
                st.remove(k)
        finally:
            st.endGroup()

    def _saved_view(self, doc):
        """(zoom, (x, y)) the page was left at, when its size is the same now; else None."""
        import json

        raw = app_settings().value(self._view_key(doc), "")
        if not raw:
            return None
        try:
            v = json.loads(str(raw))
            z, x, y = float(v["z"]), float(v["x"]), float(v["y"])
            size = (int(v.get("w", 0)), int(v.get("h", 0)))
        except ValueError, TypeError, KeyError:  # swallow-ok: a garbled entry fits instead
            return None
        if size != self._page_size(doc) or z <= 0:
            return None  # (another page now: it is fitted)
        return z, (x, y)

    def _remember_all_views(self) -> None:
        """The window closes: every opened tab's view is kept."""
        for e in list(self._docs):
            with contextlib.suppress(Exception):
                self._remember_view(e)
