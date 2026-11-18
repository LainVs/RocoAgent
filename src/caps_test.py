"""caps_test.py - a more tolerant damage-capsule finder, validated on known frames.

digitread.find_pills demands the capsule be exactly (61,61,61)+-8. The capsule is
a translucent dark overlay on the card art, so its colour drifts and detection
silently drops out - which is why the bot sat through whole matches pressing
nothing.

Ground truth:
  k1      --   212  70   100
  q1      --   415  --   --
  024605  --   425  140  100
  g1      --   --   162  150
"""
import sys

import cv2
import numpy as np

sys.path.insert(0, r"D:\Tools")
import digitread as dr  # noqa: E402

BOX = dr.BAR


def load(p):
    return cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]


def find_capsules(crop, region):
    """Dark rounded capsules, tolerant of tint from the art underneath."""
    x0, y0, x1, y1 = region
    sub = crop[y0:y1, x0:x1]
    if sub.size == 0:
        return []
    mx = sub.max(axis=2).astype(np.int16)
    mn = sub.min(axis=2).astype(np.int16)
    m = ((mx < 118) & ((mx - mn) < 60)).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (40 <= w <= 140 and 20 <= h <= 56):
            continue
        if not (0.28 <= h / float(w) <= 0.80):
            continue
        if area < 0.30 * w * h:
            continue
        reg = sub[y:y + h, x:x + w]
        r, g, b = dr.channels(reg)
        yel = int(dr.gate_yellow(reg).sum())
        grn = int(dr.gate_green(reg).sum())
        red = int(dr.gate_red(reg).sum())
        bright = int(((r > 170) & (g > 160) & (b > 140)).sum())
        is_badge = (red + yel >= 150) and grn < 20 and bright < 90
        kind = "dash" if (bright >= 50 and grn + red < 20) else (
            "num" if (grn + red >= 20 or bright >= 50) else None)
        if is_badge:
            kind = "badge"
        if kind is None:
            continue
        out.append({"box": (x + x0, y + y0, x + x0 + w, y + y0 + h),
                    "kind": kind,
                    "color": ("green" if grn >= red else "red") if kind == "num" else None,
                    "badge": is_badge})
    out.sort(key=lambda p: p["box"][1])
    return out


def slots(crop, region):
    pills = find_capsules(crop, region)
    badges = [p for p in pills if p["badge"]]
    dmg = [p for p in pills if not p["badge"]]
    return dmg, badges


def report(label, im):
    x0, y0, x1, y1 = BOX
    crop = im[y0:y1, x0:x1]
    reg = (0, 0, x1 - x0, y1 - y0)
    dmg, badges = slots(crop, reg)
    tpl = dr.load_templates()
    vals = []
    for i in range(4):
        pill = dmg[i] if i < len(dmg) else None
        if pill is not None and pill["kind"] == "badge":
            pill = None
        r = dr.read_skill(crop, pill, tpl)
        vals.append("%s/%s" % (r["text"], r["color"]))
    print("%-14s dmg=%d badge=%d  %s" % (label, len(dmg), len(badges), "  ".join(vals)))


CASES = [
    ("k1", r"D:\Projects\RocoAgent\logs\k1.png"),
    ("q1", r"D:\Projects\RocoAgent\logs\q1.png"),
    ("024605", r"D:\Projects\RocoAgent\logs\frames2\024605_0416.png"),
    ("g1", r"D:\Projects\RocoAgent\logs\g1.png"),
    ("024605b", r"D:\Projects\RocoAgent\logs\burst\b013.png"),
    ("livebar", r"D:\Projects\RocoAgent\logs\livebar.png"),
]
for label, p in CASES:
    try:
        im = load(p)
    except Exception as e:
        print(label, "load fail", e)
        continue
    report(label, im)
