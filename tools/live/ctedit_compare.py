"""Screenshot pages in the live Graphics Builder next to our render.

tools/live/ctedit_compare.py [--out <dir>]
"""

import os, sys, time, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import livepaths  # noqa: E402

livepaths.setup()
OUT = livepaths.out_dir("compare")  # (before the project:page arguments are read)
import win32con  # noqa: E402
import win32gui  # noqa: E402
import pythoncom  # noqa: E402
import win32com.client  # noqa: E402
import ctdraw_inspect as ci  # noqa: E402
from ctgedit.backend.graphics_builder import CLSID  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

ROOT = livepaths.ROOT
REPO = livepaths.REPO

sys.path.append(os.path.join(REPO, "tests_corpus"))
import corpus  # noqa: E402


def _role_pages(name):
    """(project, page stem) of each 'Project/Page.ctg' a local list role names."""
    out = []
    for rel in corpus.role_value(name, []):
        parts = [p for p in str(rel).replace("\\", "/").split("/") if p]
        if len(parts) >= 2:
            out.append((parts[0], os.path.splitext(parts[-1])[0]))
    return out


PAGES = [
    ("Example", "Mixer"),
    ("ExampleSA", "Mixing_L3"),
] + _role_pages("ctedit_compare_pages")
if "--wide" in sys.argv:
    import glob as _glob

    PAGES = []
    for d in sorted(_glob.glob(os.path.join(ROOT, "*"))):
        if not os.path.isdir(d):
            continue
        proj = os.path.basename(d)
        pgs = sorted(os.path.splitext(os.path.basename(f))[0] for f in _glob.glob(os.path.join(d, "*.ctg")))
        if not pgs:
            continue
        disp = [p for p in pgs if not p.startswith("!")] or pgs  # prefer real display pages
        sel = [disp[0]] + ([disp[len(disp) // 2]] if len(disp) > 1 else [])
        for p in sel:
            PAGES.append((proj, p))
elif len(sys.argv) > 1:
    PAGES = [tuple(a.split(":", 1)) for a in sys.argv[1:]]


def _canvas_hwnd(frame):
    res = []

    def cb(c, _):
        if win32gui.GetClassName(c) == "ctDraw.canvas" and win32gui.IsWindowVisible(c):
            res.append(c)
        return True

    win32gui.EnumChildWindows(frame, cb, None)
    return res[0] if res else None


def grab_editor(proj, page):
    pythoncom.CoInitialize()
    try:
        c = win32com.client.GetActiveObject(CLSID)
        try:
            c.ProjectSelect(proj)
        except Exception:  # noqa: BLE001
            pass
        try:
            c.PageClose()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
        c.PageOpen(proj, page)
        time.sleep(1.4)
        c = None
    finally:
        pythoncom.CoUninitialize()
    fr = ci.main_frame()
    if not fr:
        return None
    frame = fr[0]
    # maximised: a smaller editor window clips the canvas grab
    win32gui.ShowWindow(frame, win32con.SW_MAXIMIZE)
    ci.ensure_front(frame)
    time.sleep(0.6)
    h = _canvas_hwnd(frame)
    if not h:
        return None
    bmp = os.path.join(OUT, "_ed_tmp.bmp")
    ci.shoot(h, bmp)
    im = Image.open(bmp).convert("RGB")
    try:
        os.remove(bmp)
    except OSError:
        pass
    return im


def render_ours(proj, page):
    pdir = os.path.join(ROOT, proj)
    env = dict(
        os.environ,
        PYTHONPATH=REPO,
        CTG_LIB_PATH=pdir,
        QT_QPA_PLATFORM="offscreen",
        QT_QPA_FONTDIR=r"C:\Windows\Fonts",
    )
    py = os.path.join(REPO, ".venv", "Scripts", "python.exe")
    r = subprocess.run(
        [py, os.path.join(REPO, "tools", "live", "ours_render.py"), pdir, page, "--out", OUT],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    lines = (r.stdout or "").strip().splitlines()
    out = lines[-1].strip() if lines else ""  # (its last line: the PNG's path)
    if out and os.path.exists(out):
        return Image.open(out).convert("RGB")
    sys.stderr.write("render_ours failed for %s: %s\n" % (page, r.stderr[-200:]))
    return None


def side_by_side(ed, ours, page):
    TH = 560

    def fit(im):
        w = int(im.width * TH / im.height)
        return im.resize((w, TH))

    ed2 = fit(ed) if ed else Image.new("RGB", (400, TH), (40, 40, 40))
    ou2 = fit(ours) if ours else Image.new("RGB", (400, TH), (40, 40, 40))
    cmp = Image.new("RGB", (ed2.width + ou2.width + 24, TH + 28), (255, 255, 255))
    cmp.paste(ed2, (8, 24))
    cmp.paste(ou2, (ed2.width + 16, 24))
    d = ImageDraw.Draw(cmp)
    d.text((8, 6), "CtEdit", (0, 0, 0))
    d.text((ed2.width + 16, 6), "OURS  -  " + page, (0, 0, 0))
    p = os.path.join(OUT, page.replace("!", "") + ".png")
    cmp.save(p)
    return p


def main():
    for proj, page in PAGES:
        try:
            ed = grab_editor(proj, page)
            ours = render_ours(proj, page)
            p = side_by_side(ed, ours, page)
            print("OK  %s/%s -> %s" % (proj, page, p), flush=True)
        except Exception as e:  # noqa: BLE001
            print("FAIL %s/%s : %s" % (proj, page, e), flush=True)


if __name__ == "__main__":
    main()
