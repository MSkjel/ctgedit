"""Compare our renders with the embedded thumbnails.

python tools/render_compare.py [N] [--calibrate] [--dump DIR]
"""

from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# real glyphs, not tofu boxes: the editor draws text with the system fonts
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import numpy as np  # noqa: E402
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit.io import reader as _reader  # noqa: E402
from ctgedit.io.reader import read_page  # noqa: E402
from ctgedit.render.scene import PageScene  # noqa: E402

# literal @(...) labels: the app default and what the thumbnails show (the headless
# reader's default, 'native', strips @())
_reader.set_label_mode("literal")

EXAMPLE_DIR = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example"
GRID = 48  # downscaled comparison grid (square)


# --------------------------------------------------------------------------- image helpers
def _qimage_to_gray(img: QtGui.QImage, tw: int, th: int) -> np.ndarray:
    """QImage -> float grayscale array at (th, tw), values 0..255."""
    img = img.convertToFormat(QtGui.QImage.Format_RGB32)
    if img.width() != tw or img.height() != th:
        img = img.scaled(tw, th, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
        img = img.convertToFormat(QtGui.QImage.Format_RGB32)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    arr = np.frombuffer(memoryview(ptr), dtype=np.uint8).reshape((h, w, 4))  # BGRA
    b = arr[:, :, 0].astype(np.float64)
    g = arr[:, :, 1].astype(np.float64)
    r = arr[:, :, 2].astype(np.float64)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _box_downscale(gray: np.ndarray, n: int = GRID) -> np.ndarray:
    """Area-average a grayscale image down to an n x n grid (robust to misalignment)."""
    h, w = gray.shape
    ys = (np.linspace(0, h, n + 1)).astype(int)
    xs = (np.linspace(0, w, n + 1)).astype(int)
    out = np.empty((n, n), dtype=np.float64)
    for i in range(n):
        y0, y1 = ys[i], max(ys[i] + 1, ys[i + 1])
        for j in range(n):
            x0, x1 = xs[j], max(xs[j] + 1, xs[j + 1])
            out[i, j] = gray[y0:y1, x0:x1].mean()
    return out


def _sobel_mag(a: np.ndarray) -> np.ndarray:
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[:, 1:-1] = a[:, 2:] - a[:, :-2]
    gy[1:-1, :] = a[2:, :] - a[:-2, :]
    return np.hypot(gx, gy)


# --------------------------------------------------------------------------- metrics
def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    da = np.sqrt((a * a).sum())
    db = np.sqrt((b * b).sum())
    if da < 1e-9 or db < 1e-9:
        # one image is flat. correlation undefined; treat as 0 (no shared structure)
        return 0.0
    return float((a * b).sum() / (da * db))


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Global SSIM (single window over the whole grid)."""
    L = 255.0
    c1 = (0.01 * L) ** 2
    c2 = (0.03 * L) ** 2
    mu_a, mu_b = a.mean(), b.mean()
    va, vb = a.var(), b.var()
    cov = ((a - mu_a) * (b - mu_b)).mean()
    num = (2 * mu_a * mu_b + c1) * (2 * cov + c2)
    den = (mu_a**2 + mu_b**2 + c1) * (va + vb + c2)
    return float(num / den) if den else 0.0


def _edge_corr(a: np.ndarray, b: np.ndarray) -> float:
    ea, eb = _sobel_mag(a), _sobel_mag(b)
    r = _ncc(ea, eb)
    return max(0.0, r)


def compare_grids(native_g: np.ndarray, thumb_g: np.ndarray) -> dict:
    ncc = _ncc(native_g, thumb_g)
    ssim = _ssim(native_g, thumb_g)
    edge = _edge_corr(native_g, thumb_g)
    ncc01 = max(0.0, ncc)
    ssim01 = max(0.0, ssim)
    score = 100.0 * (ncc01 + ssim01 + edge) / 3.0
    return {"ncc": ncc, "ssim": ssim, "edge": edge, "score": score}


# --------------------------------------------------------------------------- per-page
def render_native_gray(page, tw: int, th: int) -> np.ndarray:
    scene = PageScene()
    scene.set_page(page, thumbnail_opacity=0.0)  # backdrop OFF
    w = int(page.appearance.width or tw)
    h = int(page.appearance.height or th)
    native = QtGui.QImage(tw, th, QtGui.QImage.Format_RGB32)
    native.fill(QtGui.QColor("#000000"))
    pr = QtGui.QPainter(native)
    pr.setRenderHint(QtGui.QPainter.Antialiasing, True)
    # map the page's logical scene rect onto the thumbnail-resolution target
    scene.render(pr, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(0, 0, w, h))
    pr.end()
    return _qimage_to_gray(native, tw, th)


def score_page(path: str, calibrate: bool = False, dump_dir: str | None = None) -> dict | None:
    page = read_page(path)
    if not page.thumbnail_bmp:
        return None
    thumb = QtGui.QImage.fromData(page.thumbnail_bmp, "BMP")
    if thumb.isNull():
        return None
    tw, th = thumb.width(), thumb.height()
    if tw < 4 or th < 4:
        return None

    thumb_gray = _qimage_to_gray(thumb, tw, th)
    native_gray = render_native_gray(page, tw, th)

    tg = _box_downscale(thumb_gray)
    ng = _box_downscale(native_gray)
    res = compare_grids(ng, tg)

    from collections import Counter

    kinds = Counter(o.kind.value for o in page.objects)
    drawn = [o for o in page.objects if not o.raw.get("_non_drawable")]
    placeholders = sum(
        1
        for o in drawn
        if o.kind.value in ("symbol", "symbol_set", "genie", "composite_genie", "trend", "unknown")
    )
    styled = sum(
        1 for o in page.objects if o.style.fill_colour is not None or o.style.line_colour is not None
    )
    rec = {
        "name": os.path.basename(path),
        "tw": tw,
        "th": th,
        "objs": len(page.objects),
        "drawn": len(drawn),
        "placeholders": placeholders,
        "styled": styled,
        "kinds": dict(kinds),
        **res,
    }

    if calibrate:
        # baseline 1: flat page background only (no objects at all)
        flat = np.full_like(tg, tg.mean())
        rec["base_flat"] = compare_grids(flat, tg)["score"]
        # baseline 2: rotated thumbnail vs thumbnail (worst-case "wrong but same stats")
        rot = np.rot90(tg)
        rec["base_rot90"] = compare_grids(rot, tg)["score"]

    if dump_dir:
        os.makedirs(dump_dir, exist_ok=True)
        w = int(page.appearance.width or tw)
        h = int(page.appearance.height or th)
        scene = PageScene()
        scene.set_page(page, thumbnail_opacity=0.0)
        nat = QtGui.QImage(tw, th, QtGui.QImage.Format_RGB32)
        nat.fill(QtGui.QColor("#000000"))
        pr = QtGui.QPainter(nat)
        pr.setRenderHint(QtGui.QPainter.Antialiasing, True)
        scene.render(pr, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(0, 0, w, h))
        pr.end()
        combo = QtGui.QImage(tw * 2 + 6, th, QtGui.QImage.Format_RGB32)
        combo.fill(QtGui.QColor("#404040"))
        cp = QtGui.QPainter(combo)
        cp.drawImage(0, 0, nat)
        cp.drawImage(tw + 6, 0, thumb)
        cp.end()
        base = os.path.splitext(rec["name"])[0]
        combo.save(os.path.join(dump_dir, "%05.1f_%s.png" % (res["score"], base)), "PNG")

    return rec


# --------------------------------------------------------------------------- driver
def main() -> int:
    args = [a for a in sys.argv[1:]]
    calibrate = "--calibrate" in args
    args = [a for a in args if a != "--calibrate"]
    dump_dir = None
    if "--dump" in args:
        i = args.index("--dump")
        dump_dir = args[i + 1]
        del args[i : i + 2]
    limit = int(args[0]) if args else 0

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    files = sorted(glob.glob(os.path.join(EXAMPLE_DIR, "*.ctg")))
    if limit:
        # take a diverse spread: stride across the sorted list rather than the first N
        step = max(1, len(files) // limit)
        files = files[::step][:limit]

    rows = []
    for f in files:
        try:
            rec = score_page(f, calibrate=calibrate, dump_dir=dump_dir)
        except Exception as e:  # noqa: BLE001
            print("ERROR %-28s %r" % (os.path.basename(f), e), file=sys.stderr)
            continue
        if rec is None:
            continue
        rows.append(rec)
        extra = ""
        if calibrate:
            extra = " | flatbase=%4.1f rot90=%4.1f" % (rec["base_flat"], rec["base_rot90"])
        print(
            "%-30s %3dx%-3d score=%5.1f  ncc=%+.2f ssim=%+.2f edge=%.2f | "
            "drawn=%3d ph=%3d styled=%3d%s"
            % (
                rec["name"],
                rec["tw"],
                rec["th"],
                rec["score"],
                rec["ncc"],
                rec["ssim"],
                rec["edge"],
                rec["drawn"],
                rec["placeholders"],
                rec["styled"],
                extra,
            )
        )

    if not rows:
        print("no pages scored", file=sys.stderr)
        return 1

    scores = np.array([r["score"] for r in rows])
    med = float(np.median(scores))
    mean = float(scores.mean())
    print("\n==== SUMMARY over %d pages ====" % len(rows))
    print(
        "median score = %.1f   mean = %.1f   min = %.1f   max = %.1f"
        % (med, mean, scores.min(), scores.max())
    )
    if calibrate:
        fb = np.array([r["base_flat"] for r in rows])
        print(
            "flat-background baseline: median=%.1f mean=%.1f  "
            "(native must beat this to be meaningful)" % (np.median(fb), fb.mean())
        )
        beat = int((scores > fb + 2.0).sum())
        print("pages where native clearly beats flat baseline (>+2): %d / %d" % (beat, len(rows)))

    worst = sorted(rows, key=lambda r: r["score"])[:12]
    print("\nworst 12 pages:")
    for r in worst:
        print(
            "  %-30s score=%5.1f  ncc=%+.2f ssim=%+.2f edge=%.2f  ph=%d/%d kinds=%s"
            % (
                r["name"],
                r["score"],
                r["ncc"],
                r["ssim"],
                r["edge"],
                r["placeholders"],
                r["drawn"],
                r["kinds"],
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
