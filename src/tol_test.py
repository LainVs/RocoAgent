"""tol_test.py - does relaxing the capsule colour tolerance rescue dim cards?

digitread.find_pills requires the capsule to be (61,61,61)+-8. When a card is
dimmed (low energy) the whole card darkens, the capsule drifts out of that
window, and find_pills keeps only the cost badges - so every slot reads '--'.

This measures strict vs relaxed on frames where the bot reported failure.
"""
import glob
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, r"D:\Tools")
import digitread as dr  # noqa: E402

BAR = dr.BAR
BG = np.array(dr.BG, np.int16)


def find_pills_tol(sub, tol):
    d = np.abs(sub.astype(np.int16) - BG).max(axis=2)
    m = (d <= tol).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (44 <= w <= 130 and 22 <= h <= 52):
            continue
        if not (0.30 <= h / float(w) <= 0.75):
            continue
        if area < 0.32 * w * h:
            continue
        reg = sub[y:y + h, x:x + w]
        gg, gr, gy = dr.gate_green(reg), dr.gate_red(reg), dr.gate_yellow(reg)
        n_green, n_red, n_yellow = int(gg.sum()), int(gr.sum()), int(gy.sum())
        if n_yellow >= 40:
            continue
        r, g, b = dr.channels(reg)
        bright = int(((r > 170) & (g > 160) & (b > 140)).sum())
        if n_green + n_red >= 20:
            blobs = dr.segment(dr.text_mask(reg))
            if not blobs or max(bl[2] for bl in blobs) < 8:
                continue
            out.append(("num", x, y, w, h))
        elif bright >= 50:
            out.append(("dash", x, y, w, h))
    out.sort(key=lambda t: t[2])
    return out


def probe(path):
    im = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]
    x0, y0, x1, y1 = BAR
    sub = im[y0:y1, x0:x1]
    res = {}
    for tol in (8, 14, 20, 26):
        pills = find_pills_tol(sub, tol)
        res[tol] = (len(pills), sum(1 for p in pills if p[0] == "num"))
    return res


files = sorted(glob.glob(r"D:\Projects\RocoAgent\logs\frames2\0343*.png"))[::3]
print("%-20s %-12s %-12s %-12s %-12s" % ("frame", "tol8", "tol14", "tol20", "tol26"))
agg = {t: [0, 0] for t in (8, 14, 20, 26)}
for f in files[:18]:
    r = probe(f)
    print("%-20s %-12s %-12s %-12s %-12s" % (
        os.path.basename(f),
        "%d/%d" % r[8], "%d/%d" % r[14], "%d/%d" % r[20], "%d/%d" % r[26]))
    for t in agg:
        agg[t][0] += r[t][1]
        agg[t][1] += 1
print()
for t in (8, 14, 20, 26):
    print("tol %-3d frames with >=1 numeric pill: %d/%d" % (t, agg[t][0], agg[t][1]))
