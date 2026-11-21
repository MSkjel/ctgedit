"""Put the compare PNGs into contact sheets.

montage.py [--out <the compare folder>]
"""

import os, sys, glob
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import livepaths  # noqa: E402

D = livepaths.out_dir("compare")
files = sorted(f for f in glob.glob(os.path.join(D, "*.png")) if not os.path.basename(f).startswith("_"))
W = 1500
PERSHEET = 4
i = n = 0
sheets = []
while i < len(files):
    group = files[i : i + PERSHEET]
    i += PERSHEET
    ims = []
    for f in group:
        im = Image.open(f).convert("RGB")
        ims.append(im.resize((W, int(im.height * W / im.width))))
    H = sum(im.height for im in ims) + (len(ims) - 1) * 4
    sheet = Image.new("RGB", (W, H), (170, 170, 170))
    y = 0
    for im in ims:
        sheet.paste(im, (0, y))
        y += im.height + 4
    p = os.path.join(D, "_sheet_%02d.png" % n)
    sheet.save(p)
    sheets.append(p)
    n += 1
print("%d compare images -> %d sheets" % (len(files), len(sheets)))
for s in sheets:
    print(s)
