"""The Qt application of a test process, and a picture of a canvas."""


def app():
    """The process's QApplication, made on first use (offscreen: tests/conftest.py)."""
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def canvas_picture(view):
    """``view``'s page as the canvas draws it, without the widgets laid over the canvas (the
    scroll bars, shown by a scroll; the navigation bar). A test comparing two pictures of a
    canvas takes them with this, not viewport().grab(). (The canvas itself holds still in the
    tests: ctgedit/ui/editor/canvas_look.still.)"""
    from PySide6 import QtCore, QtGui, QtWidgets

    vp = view.viewport()
    QtWidgets.QApplication.processEvents()
    dpr = vp.devicePixelRatioF()
    img = QtGui.QImage(
        round(vp.width() * dpr), round(vp.height() * dpr), QtGui.QImage.Format_ARGB32_Premultiplied
    )
    img.setDevicePixelRatio(dpr)
    img.fill(0)
    vp.render(img, QtCore.QPoint(), QtGui.QRegion(), QtWidgets.QWidget.RenderFlag.DrawWindowBackground)
    return img
