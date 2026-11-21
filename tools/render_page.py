"""Render a page headless to PNG.

QT_QPA_PLATFORM=offscreen python tools/render_page.py <file.ctg> [out.png]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit.io.reader import read_page  # noqa: E402
from ctgedit.render.scene import PageScene  # noqa: E402


def render(path: str, out: str, thumb_opacity: float = 0.0) -> str:
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = read_page(path)
    scene = PageScene()
    scene.set_page(page, thumbnail_opacity=thumb_opacity)
    w = int(page.appearance.width or 1024)
    h = int(page.appearance.height or 768)

    # render native scene
    native = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    native.fill(QtGui.QColor("#000000"))
    pr = QtGui.QPainter(native)
    pr.setRenderHint(QtGui.QPainter.Antialiasing, True)
    scene.render(pr, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(0, 0, w, h))
    pr.end()

    # citect thumbnail (if any)
    thumb = QtGui.QImage()
    if page.thumbnail_bmp:
        thumb = QtGui.QImage.fromData(page.thumbnail_bmp, "BMP")

    if not thumb.isNull():
        thumb = thumb.scaled(w, h, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
        combo = QtGui.QImage(w * 2 + 8, h, QtGui.QImage.Format_ARGB32)
        combo.fill(QtGui.QColor("#333333"))
        cp = QtGui.QPainter(combo)
        cp.drawImage(0, 0, native)
        cp.drawImage(w + 8, 0, thumb)
        cp.end()
        combo.save(out, "PNG")
    else:
        native.save(out, "PNG")

    # kind histogram
    from collections import Counter

    kinds = Counter(o.kind.value for o in page.objects)
    styled = sum(
        1 for o in page.objects if o.style.fill_colour is not None or o.style.line_colour is not None
    )
    print(
        "%-22s %dx%d objs=%d styled=%d kinds=%s -> %s"
        % (os.path.basename(path), w, h, len(page.objects), styled, dict(kinds), out)
    )
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: render_page.py <file.ctg> [out.png] [thumb_opacity]", file=sys.stderr)
        return 2
    path = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(os.path.basename(path))[0] + ".png"
    op = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    render(path, out, op)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
