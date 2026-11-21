"""Render a page as the editor shows it after opening, to PNG.

ours_render.py <project_dir> <page_basename> [--out <dir>] [--no-updates]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import livepaths  # noqa: E402

livepaths.setup()
OUT = livepaths.out_dir("ours_render")
UPDATES = "--no-updates" not in sys.argv
if not UPDATES:
    sys.argv.remove("--no-updates")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
PROJ = sys.argv[1]
PAGE = sys.argv[2]
os.environ["CTG_LIB_PATH"] = PROJ

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from ctgedit.io import reader  # noqa: E402
from ctgedit.render.scene import PageScene  # noqa: E402

reader.set_label_mode("literal")
path = os.path.join(PROJ, PAGE + ".ctg")
pg = None
if UPDATES:
    from ctgedit.editing import EditableDocument  # noqa: E402

    doc = EditableDocument.open(path)  # (in memory only: nothing is saved)
    done = doc.apply_open_updates()
    if done:
        print("open-time updates: " + ", ".join(done))
    pg = doc.page
if pg is None:
    pg = reader.read_page(path)
w = int(pg.appearance.width or 1920)
h = int(pg.appearance.height or 1058)
sc = PageScene()
sc.set_page(pg, thumbnail_opacity=0.0)
img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
img.fill(QtGui.QColor("#d4d0c8"))
pr = QtGui.QPainter(img)
pr.setRenderHint(QtGui.QPainter.Antialiasing, True)
sc.render(pr, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(0, 0, w, h))
pr.end()
out = os.path.join(OUT, "ours_%s.png" % PAGE.replace("!", ""))
img.save(out)
print("ours %dx%d" % (w, h))
print(out)
