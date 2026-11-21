"""Render a page or element thumbnail without a window."""

from __future__ import annotations

import os
from typing import Optional


def page_thumbnail(doc) -> Optional[bytes]:
    """The TH thumbnail blob of ``doc``'s page as it is now, or None (no page, no size)."""
    from ..format import thumbnail as T

    page = getattr(doc, "page", None)
    pb = doc.parsed.page if doc.parsed is not None else None
    if page is None or pb is None:
        return None
    pw = page.appearance.width or getattr(pb, "page_width", 0)
    ph = page.appearance.height or getattr(pb, "page_height", 0)
    tw, th = T.thumb_size(pw, ph)
    if not tw or not th:
        return None
    return _render(page, (0, 0, pw, ph), (tw, th))


def element_thumbnail(doc) -> Optional[bytes]:
    """The TH thumbnail blob of the library element ``doc`` (an ElementDocument) as it is
    now: its content, longest side 200 (ElementDocument.thumbnail_geometry), as the
    window's element save renders it (ui/window/files _element_thumbnail). None when it has
    no page or no extent."""
    page = getattr(doc, "page", None)
    geo = doc.thumbnail_geometry() if page is not None else None
    if geo is None:
        return None
    src, size = geo
    if not size[0] or not size[1]:
        return None
    return _render(page, src, size)


def composite_with_thumbnail(page_path: str, lib_path: str, name: str, content: bytes) -> bytes:
    """Add a preview to a composed element before the cache library is written."""
    from ..format import element, element_session
    from ..io.reader import derive_page
    from ..io.readerparts.common import derive_options

    library = element.LibraryFile.new()
    library.write_element(name, content)
    session, parsed = element_session.open_element_session(lib_path, name, library)
    geometry = element_session.thumbnail_geometry(session, parsed)
    if geometry is None:
        return content
    with derive_options(element_window=True):
        page, _ = derive_page(parsed, page_path, records_fresh=True)
    src, size = geometry
    session.pe.page.thumbnail = _render(page, src, size)
    return element.element_content(session.pe)


def _render(page, src, size) -> bytes:
    """``page``'s scene rect ``src`` (x, y, w, h) drawn into a ``size`` image on white: the
    TH blob. In a process with no QApplication (a helper) one is made offscreen; the
    editor's own (Update Users runs here in the window's process) is used as it is, and
    its environment is left, so the programs it starts later show their windows."""
    from PySide6 import QtCore, QtGui, QtWidgets
    from ..format import thumbnail as T
    from .scene import PageScene

    tw, th = int(size[0]), int(size[1])
    if QtWidgets.QApplication.instance() is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QtWidgets.QApplication([])
    scene = PageScene()
    try:
        scene.set_page(page)
        img = QtGui.QImage(tw, th, QtGui.QImage.Format_RGB32)
        img.fill(QtCore.Qt.white)
        painter = QtGui.QPainter(img)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        try:
            with scene.export_only(None):
                scene.render(painter, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(*src))
        finally:
            painter.end()
        bits = bytes(memoryview(img.constBits()))[: tw * th * 4]
        return T.encode(bits, tw, th)
    finally:
        scene.clear()
        scene.deleteLater()
