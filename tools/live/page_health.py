"""Parse, build and render every page of every project, and flag crashes and oddities."""

import os, sys, gc, json, time, glob, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import livepaths  # noqa: E402

livepaths.setup()
UPDATES = "--updates" in sys.argv

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"
OUT = os.path.join(livepaths.out_dir("page_health"), "_page_health.json")

from PySide6 import QtWidgets, QtCore, QtGui  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from ctgedit.io import reader  # noqa: E402
from ctgedit.render.scene import PageScene  # noqa: E402

reader.set_label_mode("literal")


def _dominant_fraction(img: QtGui.QImage) -> float:
    """Fraction of pixels that are the single most-common colour (≈1.0 => blank/one-colour)."""
    import numpy as np

    img = img.convertToFormat(QtGui.QImage.Format_RGB32)
    a = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(img.height(), img.width(), 4)
    packed = (a[:, :, 0].astype(np.uint32) << 16) | (a[:, :, 1].astype(np.uint32) << 8) | a[:, :, 2]
    vals, counts = np.unique(packed, return_counts=True)
    return float(counts.max()) / packed.size


def check_page(path: str, project_dir: str) -> dict:
    os.environ["CTG_LIB_PATH"] = project_dir
    rec = {"page": os.path.basename(path), "project": os.path.basename(project_dir.rstrip("\\/"))}
    try:
        pg = None
        if UPDATES:
            from ctgedit.editing import EditableDocument

            doc = EditableDocument.open(path)  # (in memory only: nothing is saved)
            rec["updates"] = doc.apply_open_updates()
            pg = doc.page
        if pg is None:
            pg = reader.read_page(path)
    except Exception as e:  # noqa: BLE001
        rec["status"] = "PARSE_CRASH"
        rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        rec["trace"] = traceback.format_exc()[-600:]
        return rec
    rec["objects"] = len(pg.objects)
    W = pg.appearance.width or 0
    H = pg.appearance.height or 0
    rec["page_wh"] = [W, H]
    try:
        sc = PageScene()
        sc.set_page(pg, thumbnail_opacity=0.0)
        items = sc._items
        rec["scene_items"] = len(items)
        # off-page / huge geometry (a transform/placement bug): item bbox far outside the page
        offpage = huge = 0
        if W and H:
            pr = QtCore.QRectF(-80, -80, W + 160, H + 160)
            for it in items.values():
                if type(it).__name__ == "_AnPointMarker":
                    continue  # (the template's anchor points)
                br = it.sceneBoundingRect()
                if br.width() > 6000 or br.height() > 6000:
                    huge += 1
                elif not pr.intersects(br):
                    offpage += 1
        rec["offpage_items"] = offpage
        rec["huge_items"] = huge
        # blank/one-colour render (everything failed to draw): only meaningful for content pages
        if W and H:
            iw = 480
            ih = max(1, int(iw * H / W))
            img = QtGui.QImage(iw, ih, QtGui.QImage.Format_RGB32)
            img.fill(QtGui.QColor("#d4d0c8"))
            p = QtGui.QPainter(img)
            sc.render(p, QtCore.QRectF(0, 0, iw, ih), QtCore.QRectF(0, 0, W, H))
            p.end()
            rec["dominant_frac"] = round(_dominant_fraction(img), 4)
        sc.clear()
        del sc
    except Exception as e:  # noqa: BLE001
        rec["status"] = "RENDER_CRASH"
        rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        rec["trace"] = traceback.format_exc()[-600:]
        return rec
    # classify
    flags = []
    if rec.get("objects", 0) > 25 and rec.get("scene_items", 0) == 0:
        flags.append("NO_ITEMS")
    if rec.get("objects", 0) > 30 and rec.get("dominant_frac", 0) >= 0.999:
        flags.append("BLANK")
    if rec.get("offpage_items", 0) > 0:
        flags.append("OFFPAGE(%d)" % rec["offpage_items"])
    if rec.get("huge_items", 0) > 0:
        flags.append("HUGE(%d)" % rec["huge_items"])
    rec["status"] = "OK" if not flags else "FLAG"
    rec["flags"] = flags
    return rec


def main():
    projects = sorted(d for d in glob.glob(os.path.join(ROOT, "*")) if os.path.isdir(d))
    pages = []
    for d in projects:
        for f in sorted(glob.glob(os.path.join(d, "*.ctg"))):
            pages.append((f, d))
    print("pages: %d across %d projects" % (len(pages), len(projects)), flush=True)
    results = []
    t0 = time.perf_counter()
    for i, (f, d) in enumerate(pages):
        rec = check_page(f, d)
        results.append(rec)
        if rec["status"] not in ("OK",):
            print(
                "  [%s] %s/%s %s"
                % (rec["status"], rec["project"], rec["page"], rec.get("flags") or rec.get("error", "")),
                flush=True,
            )
        if (i + 1) % 100 == 0:
            print("  ... %d/%d (%.0fs)" % (i + 1, len(pages), time.perf_counter() - t0), flush=True)
            json.dump(results, open(OUT, "w", encoding="utf-8"))
            gc.collect()
    json.dump(results, open(OUT, "w", encoding="utf-8"))
    # summary
    from collections import Counter

    by_status = Counter(r["status"] for r in results)
    print("\n=== SUMMARY (%.0fs) ===" % (time.perf_counter() - t0))
    for s, n in by_status.most_common():
        print("  %-14s %d" % (s, n))
    flagkinds = Counter(fk.split("(")[0] for r in results for fk in r.get("flags", []))
    print("  flag kinds:", dict(flagkinds))
    print("  wrote", OUT)


if __name__ == "__main__":
    main()
