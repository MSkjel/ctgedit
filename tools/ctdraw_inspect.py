"""Inspect and drive the real Graphics Builder."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import time

import win32api
import win32con
import win32gui
import win32process
import win32ui

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ctdraw_live")
os.makedirs(OUT, exist_ok=True)

user32 = ctypes.windll.user32

# declare handle-correct prototypes so 64-bit HMENU/HWND values are not
# truncated to c_int
user32.GetMenu.argtypes = [wt.HWND]
user32.GetMenu.restype = wt.HMENU
user32.GetMenuItemCount.argtypes = [wt.HMENU]
user32.GetMenuItemCount.restype = ctypes.c_int
user32.GetMenuItemInfoW.restype = wt.BOOL
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.PrintWindow.restype = wt.BOOL
user32.GetSubMenu.argtypes = [wt.HMENU, ctypes.c_int]
user32.GetSubMenu.restype = wt.HMENU
user32.GetMenuItemRect.argtypes = [wt.HWND, wt.HMENU, wt.UINT, ctypes.POINTER(wt.RECT)]
user32.GetMenuItemRect.restype = wt.BOOL

# ---- Win32 menu enumeration (read-only) -----------------------------------
MIIM_STATE = 0x00000001
MIIM_ID = 0x00000002
MIIM_SUBMENU = 0x00000004
MIIM_STRING = 0x00000040
MFS_DISABLED = 0x00000003
MFS_CHECKED = 0x00000008


class MENUITEMINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT),
        ("fMask", wt.UINT),
        ("fType", wt.UINT),
        ("fState", wt.UINT),
        ("wID", wt.UINT),
        ("hSubMenu", wt.HMENU),
        ("hbmpChecked", wt.HANDLE),
        ("hbmpUnchecked", wt.HANDLE),
        ("dwItemData", ctypes.c_void_p),
        ("dwTypeData", wt.LPWSTR),
        ("cch", wt.UINT),
        ("hbmpItem", wt.HANDLE),
    ]


def _menu_item(hmenu, i):
    buf = ctypes.create_unicode_buffer(512)
    mii = MENUITEMINFO()
    mii.cbSize = ctypes.sizeof(MENUITEMINFO)
    mii.fMask = MIIM_STATE | MIIM_ID | MIIM_SUBMENU | MIIM_STRING
    mii.dwTypeData = ctypes.cast(buf, wt.LPWSTR)
    mii.cch = 511
    if not user32.GetMenuItemInfoW(wt.HMENU(hmenu), i, True, ctypes.byref(mii)):
        return None
    return {
        "index": i,
        "text": buf.value.replace("&", ""),
        "raw_text": buf.value,
        "id": mii.wID,
        "has_submenu": bool(mii.hSubMenu),
        "_hsub": mii.hSubMenu,
        "disabled": bool(mii.fState & MFS_DISABLED),
        "checked": bool(mii.fState & MFS_CHECKED),
        "separator": buf.value == "" and not mii.hSubMenu,
    }


WM_INITMENU = 0x0116
WM_INITMENUPOPUP = 0x0117


def _prime(frame_hwnd, hsub, index):
    """Fire WM_INITMENUPOPUP so CtDraw fills a dynamically-built submenu
    (exactly what hovering the menu does -- no command is executed)."""
    if frame_hwnd and hsub:
        lparam = index & 0xFFFF
        win32gui.SendMessage(frame_hwnd, WM_INITMENUPOPUP, hsub, lparam)


def enumerate_menu(hmenu, depth=0, max_depth=6, frame_hwnd=None):
    out = []
    n = user32.GetMenuItemCount(hmenu)
    for i in range(n):
        it = _menu_item(hmenu, i)
        if it is None:
            continue
        sub = it.pop("_hsub")
        if it["has_submenu"] and sub and depth < max_depth:
            _prime(frame_hwnd, sub, i)
            items = enumerate_menu(sub, depth + 1, max_depth, frame_hwnd)
            if not items:  # retry after a settle (lazy fill)
                time.sleep(0.05)
                _prime(frame_hwnd, sub, i)
                items = enumerate_menu(sub, depth + 1, max_depth, frame_hwnd)
            it["items"] = items
        out.append(it)
    return out


# ---- window discovery ------------------------------------------------------
def ctdraw_windows():
    """All top-level windows belonging to a CTDRAW32 process: (hwnd, title,
    cls, pid)."""
    targets = []

    def cb(hwnd, _):
        if not win32gui.IsWindow(hwnd):
            return
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            h = win32api.OpenProcess(0x0400 | 0x0010, False, pid)
            name = win32process.GetModuleFileNameEx(h, 0)
        except Exception:  # noqa: BLE001
            name = ""
        if name.lower().endswith("ctdraw32.exe"):
            targets.append((hwnd, win32gui.GetWindowText(hwnd), win32gui.GetClassName(hwnd), pid))

    win32gui.EnumWindows(cb, None)
    return targets


def main_frame():
    """The CtDraw main frame: a visible CTDRAW32 top-level window that owns a
    menu bar."""
    best = None
    for hwnd, title, cls, pid in ctdraw_windows():
        if win32gui.GetMenu(hwnd) and win32gui.IsWindowVisible(hwnd):
            if best is None or "Graphics Builder" in title:
                best = (hwnd, title, cls, pid)
    return best


# ---- screenshots -----------------------------------------------------------
def shoot(hwnd, path, full_desktop_region=False):
    """PrintWindow a window to PNG; falls back to a desktop-region BitBlt
    (needed for popup menus, which PrintWindow can miss)."""
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return False
    if full_desktop_region:
        src = win32gui.GetDesktopWindow()
        sdc = win32gui.GetWindowDC(src)
        mdc = win32ui.CreateDCFromHandle(sdc)
        cdc = mdc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mdc, w, h)
        cdc.SelectObject(bmp)
        cdc.BitBlt((0, 0), (w, h), mdc, (l, t), win32con.SRCCOPY)
        bmp.SaveBitmapFile(cdc, path)
        mdc.DeleteDC()
        cdc.DeleteDC()
        win32gui.ReleaseDC(src, sdc)
        win32gui.DeleteObject(bmp.GetHandle())
        return True
    hdc = win32gui.GetWindowDC(hwnd)
    mdc = win32ui.CreateDCFromHandle(hdc)
    cdc = mdc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mdc, w, h)
    cdc.SelectObject(bmp)
    # PW_RENDERFULLCONTENT = 2 (capture even DWM/GDI+ content)
    user32.PrintWindow(hwnd, cdc.GetSafeHdc(), 2)
    bmp.SaveBitmapFile(cdc, path)
    mdc.DeleteDC()
    cdc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hdc)
    win32gui.DeleteObject(bmp.GetHandle())
    return True


def _bmp_to_png(bmp_path):
    """Convert the .bmp pywin32 saves into a .png (Qt offscreen)."""
    try:
        from PySide6 import QtGui, QtWidgets  # noqa: F401

        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        img = QtGui.QImage(bmp_path)
        if not img.isNull():
            png = os.path.splitext(bmp_path)[0] + ".png"
            img.save(png)
            os.remove(bmp_path)
            return png
    except Exception:  # noqa: BLE001
        pass
    return bmp_path


def _menu_letters(tree):
    return [it["raw_text"] for it in tree if it.get("has_submenu")]


def _popup_rects():
    """Rects of all visible menu-popup windows (class #32768), front-most
    last."""
    rects = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "#32768":
            rects.append(win32gui.GetWindowRect(hwnd))

    win32gui.EnumWindows(cb, None)
    return rects


# ---- mouse + dialog driving (for object/genie/text/page property dialogs) --
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010


def click_screen(x, y, double=False, button="left"):
    """Move the real cursor to screen (x, y) and click. Used to select/open
    objects on the live canvas exactly as a user would."""
    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.08)
    down, up = (
        (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
        if button == "left"
        else (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
    )
    n = 2 if double else 1
    for _ in range(n):
        win32api.mouse_event(down, 0, 0, 0, 0)
        win32api.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.06)


def top_windows(pid):
    """Visible top-level windows owned by `pid`: {hwnd: (title, class)}."""
    out = {}

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        _tid, wpid = win32process.GetWindowThreadProcessId(hwnd)
        if wpid == pid:
            out[hwnd] = (win32gui.GetWindowText(hwnd), win32gui.GetClassName(hwnd))

    win32gui.EnumWindows(cb, None)
    return out


def wait_new_window(pid, before, timeout=4.0):
    """Poll for a NEW top-level window of `pid` (a dialog that just opened).
    Returns (hwnd, title, class) or None."""
    end = time.time() + timeout
    while time.time() < end:
        now = top_windows(pid)
        new = [h for h in now if h not in before]
        # prefer a dialog-class / titled window over transient popups
        new.sort(key=lambda h: (now[h][1] != "#32770", h))
        for h in new:
            title, cls = now[h]
            if cls in ("#32770",) or title:
                return (h, title, cls)
        time.sleep(0.1)
    return None


def close_dialog(hwnd):
    """Cancel/close a dialog WITHOUT applying changes (ESC, then WM_CLOSE)."""
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.1)
    win32api.keybd_event(0x1B, 0, 0, 0)
    win32api.keybd_event(0x1B, 0, 2, 0)
    time.sleep(0.2)
    if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        time.sleep(0.2)


# ---- robust window / page driving (MRU ids SHIFT on every open/close) ------
def ensure_front(hwnd):
    """Un-minimize, maximize and foreground the frame so screenshots are
    full-size and key/command posts are processed."""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.3)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWMAXIMIZED)
    time.sleep(0.2)
    try:
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.3)


def mru_slot_for(frame_hwnd, name):
    """Resolve the CURRENT File-menu MRU entry whose label ends with `name`
    (e.g. 'Mixer'): returns (command_id, accelerator_digit) or (None, None).
    MFC renumbers both every time the list reorders, so they MUST be looked up
    live before each open."""
    hmenu = win32gui.GetMenu(frame_hwnd)
    win32gui.SendMessage(frame_hwnd, WM_INITMENU, hmenu, 0)
    tree = enumerate_menu(hmenu, frame_hwnd=frame_hwnd)
    for m in tree:
        if m["text"] != "File":
            continue
        for it in m.get("items", []):
            t = (it.get("text") or "").strip()
            if t and t.lower().rstrip().endswith(name.lower()):
                digit = t[0] if t[:1].isdigit() else None
                return it["id"], digit
    return None, None


def mru_id_for(frame_hwnd, name):
    """Just the command id (see mru_slot_for)."""
    return mru_slot_for(frame_hwnd, name)[0]


def _answer_no(pid, before, timeout=1.5):
    """If a 'save changes?' confirm popped, press N (No). Returns True if one
    was handled."""
    p = wait_new_window(pid, before, timeout=timeout)
    if not p:
        return False
    try:
        win32gui.SetForegroundWindow(p[0])
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.2)
    win32api.keybd_event(0x4E, 0, 0, 0)  # 'N'
    win32api.keybd_event(0x4E, 0, 2, 0)
    time.sleep(0.4)
    return True


def close_all_children(hwnd, pid, max_iters=8):
    """Close every open MDI page, answering No to save prompts."""
    ensure_front(hwnd)
    try:
        import pywinauto

        app = pywinauto.Application(backend="win32").connect(handle=hwnd)
        w = app.window(handle=hwnd)
    except Exception:  # noqa: BLE001
        w = None
    for _ in range(max_iters):
        if "[" not in win32gui.GetWindowText(hwnd):
            return True
        before = set(top_windows(pid))
        if w is not None:
            try:
                w.set_focus()
            except Exception:  # noqa: BLE001
                pass
            w.type_keys("^{F4}", pause=0.1)
        else:
            win32api.keybd_event(0x11, 0, 0, 0)
            win32api.keybd_event(0x73, 0, 0, 0)
            win32api.keybd_event(0x73, 0, 2, 0)
            win32api.keybd_event(0x11, 0, 2, 0)
        time.sleep(0.6)
        _answer_no(pid, before)
        time.sleep(0.3)
    return "[" not in win32gui.GetWindowText(hwnd)


def click_menu_item(hwnd, top_index, item_endswith, hold=0.25):
    """Open a top-level menu and click an item with the mouse."""
    ensure_front(hwnd)
    try:
        import pywinauto

        app = pywinauto.Application(backend="win32").connect(handle=hwnd)
        w = app.window(handle=hwnd)
        w.set_focus()
    except Exception:  # noqa: BLE001
        w = None
    time.sleep(0.3)
    mainmenu = win32gui.GetMenu(hwnd)
    # open the top menu by its accelerator letter so the popup is realised
    top = _menu_item(mainmenu, top_index)
    letter = None
    raw = top["raw_text"] if top else ""
    if "&" in raw:
        letter = raw[raw.index("&") + 1].lower()
    if w is not None and letter:
        w.type_keys("%" + letter, pause=0.12)
    time.sleep(0.4)
    sub = user32.GetSubMenu(wt.HMENU(mainmenu), top_index)
    n = user32.GetMenuItemCount(sub)
    target = None
    for i in range(n):
        it = _menu_item(sub, i)
        if it and (it["text"] or "").strip().lower().endswith(item_endswith.lower()):
            target = i
            break
    if target is None:
        win32api.keybd_event(0x1B, 0, 0, 0)
        win32api.keybd_event(0x1B, 0, 2, 0)
        return False
    rc = wt.RECT()
    if not user32.GetMenuItemRect(hwnd, wt.HMENU(sub), target, ctypes.byref(rc)):
        win32api.keybd_event(0x1B, 0, 0, 0)
        win32api.keybd_event(0x1B, 0, 2, 0)
        return False
    cx = (rc.left + rc.right) // 2
    cy = (rc.top + rc.bottom) // 2
    time.sleep(hold)
    click_screen(cx, cy)
    return True


def open_page(hwnd, name, project=None, timeout=12.0):
    """Open a page fresh from disk through CtDraw's File > Open."""
    _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
    ensure_front(hwnd)
    try:
        import pywinauto

        app = pywinauto.Application(backend="win32").connect(handle=hwnd)
        w = app.window(handle=hwnd)
        w.set_focus()
    except Exception:  # noqa: BLE001
        app = None
    time.sleep(0.3)
    before = set(top_windows(pid))
    win32api.keybd_event(0x11, 0, 0, 0)  # Ctrl
    win32api.keybd_event(0x4F, 0, 0, 0)  # O
    win32api.keybd_event(0x4F, 0, 2, 0)
    win32api.keybd_event(0x11, 0, 2, 0)
    dlg = wait_new_window(pid, before, timeout=4.0)
    if not dlg or dlg[1] != "Open":
        return False
    dh = dlg[0]
    try:
        if app is None:
            import pywinauto

            app = pywinauto.Application(backend="win32").connect(handle=dh)
        d = app.window(handle=dh)
        d.child_window(control_id=4201, class_name="Edit").set_edit_text(name)
        time.sleep(0.2)
        if project:
            try:
                d.child_window(control_id=4302, class_name="ListBox").select(project)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    # click OK (control id 1)
    win32gui.SendMessage(dh, win32con.WM_COMMAND, 1, win32gui.GetDlgItem(dh, 1))
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.3)
        if name.lower() in win32gui.GetWindowText(hwnd).lower():
            return True
    return False


def shoot_region(l, t, r, b, path):
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return False
    src = win32gui.GetDesktopWindow()
    sdc = win32gui.GetWindowDC(src)
    mdc = win32ui.CreateDCFromHandle(sdc)
    cdc = mdc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mdc, w, h)
    cdc.SelectObject(bmp)
    cdc.BitBlt((0, 0), (w, h), mdc, (l, t), win32con.SRCCOPY)
    bmp.SaveBitmapFile(cdc, path)
    mdc.DeleteDC()
    cdc.DeleteDC()
    win32gui.ReleaseDC(src, sdc)
    win32gui.DeleteObject(bmp.GetHandle())
    return True


# ---- main ------------------------------------------------------------------
def do_enumerate(frame):
    hwnd, title, cls, pid = frame
    hmenu = win32gui.GetMenu(hwnd)
    win32gui.SendMessage(hwnd, WM_INITMENU, hmenu, 0)
    tree = enumerate_menu(hmenu, frame_hwnd=hwnd)
    # window/control tree
    children = []

    def cb(child, _):
        children.append(
            {
                "class": win32gui.GetClassName(child),
                "text": win32gui.GetWindowText(child),
                "visible": bool(win32gui.IsWindowVisible(child)),
                "rect": win32gui.GetWindowRect(child),
            }
        )

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception:  # noqa: BLE001
        pass
    data = {"title": title, "class": cls, "pid": pid, "menu": tree, "children": children}
    path = os.path.join(OUT, "ctdraw_menu.json")
    with open(os.path.normpath(path), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    # flat list for quick parity diffing
    flat = []

    def walk(items, crumb):
        for it in items:
            if it.get("separator"):
                continue
            label = " > ".join(crumb + [it["text"]]) if it["text"] else None
            if it.get("items"):
                walk(it["items"], crumb + [it["text"]])
            elif label:
                flat.append({"path": label, "id": it["id"], "disabled": it["disabled"]})

    walk(tree, [])
    with open(os.path.normpath(os.path.join(OUT, "ctdraw_menu_flat.json")), "w", encoding="utf-8") as fh:
        json.dump(flat, fh, indent=1, ensure_ascii=False)
    print(
        "enumerated: %d top menus, %d leaf items -> %s"
        % (sum(1 for it in tree if it.get("has_submenu")), len(flat), os.path.normpath(path))
    )
    return tree


def do_shoot(frame):
    hwnd = frame[0]
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    bmp = os.path.join(OUT, "ctdraw_main.bmp")
    if shoot(hwnd, bmp):
        print("main window ->", _bmp_to_png(bmp))


def do_menus(frame, tree):
    """Open each top menu, screenshot the whole frame (menu is a popup over
    it), ESC. Never invokes an item."""
    hwnd = frame[0]
    import pywinauto

    app = pywinauto.Application(backend="win32").connect(handle=hwnd)
    win = app.window(handle=hwnd)
    win.set_focus()
    time.sleep(0.3)
    for it in tree:
        if not it.get("has_submenu"):
            continue
        name = it["text"]
        raw = it["raw_text"]
        # Alt+<accelerator letter> opens the menu
        letter = None
        if "&" in raw:
            k = raw.index("&")
            if k + 1 < len(raw):
                letter = raw[k + 1].lower()
        try:
            win.set_focus()
            if letter:
                win.type_keys("%" + letter, pause=0.05)
            else:
                win.type_keys("%", pause=0.05)
            time.sleep(0.4)
            bmp = os.path.join(OUT, "menu_%s.bmp" % name.replace(" ", "_"))
            rects = _popup_rects()
            if rects:
                # pad a few px so the border shows; capture the front popup
                l, t, r, b = rects[-1]
                shoot_region(l - 2, t - 2, r + 2, b + 2, bmp)
            else:  # fallback: whole frame region
                shoot(hwnd, bmp, full_desktop_region=True)
            png = _bmp_to_png(bmp)
            print("menu %-12s -> %s (%d popup)" % (name, os.path.basename(png), len(rects)))
            win.type_keys("{ESC}{ESC}", pause=0.05)
            time.sleep(0.15)
        except Exception as e:  # noqa: BLE001
            print("menu %s FAILED: %s" % (name, e))
            try:
                win.type_keys("{ESC}{ESC}")
            except Exception:  # noqa: BLE001
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--enumerate", action="store_true")
    ap.add_argument("--shoot", action="store_true")
    ap.add_argument("--menus", action="store_true")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if not any((args.enumerate, args.shoot, args.menus, args.all)):
        args.enumerate = True

    frame = main_frame()
    if frame is None:
        wins = ctdraw_windows()
        print("No CtDraw main frame found. CTDRAW32 windows:")
        for w in wins:
            print("  ", w)
        return 1
    print("CtDraw frame: hwnd=%#x pid=%d title=%r" % (frame[0], frame[3], frame[1]))

    tree = None
    if args.enumerate or args.all:
        tree = do_enumerate(frame)
    if args.shoot or args.all:
        do_shoot(frame)
    if args.menus or args.all:
        if tree is None:
            tree = do_enumerate(frame)
        do_menus(frame, tree)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
