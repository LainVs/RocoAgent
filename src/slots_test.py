"""slots_test.py - verify skill-slot assignment against known frames.

Ground truth (read off 1:1 crops by eye):
  livebar.png  地绡银龙 : --   212  70   100
  k1.png       地绡银龙 : --   212  70   140
  g1.png       冥古龙   : --   --   162  150
  b013.png     奶龙     : --   302  --   --
"""
import paths
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, str(paths.ROOT / "src"))
sys.path.insert(0, str(paths.TOOLS))
import digitread as dr  # noqa: E402

BOX = dr.BAR


def load(p):
    return cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]


def stats(pill):
    x0, y0, x1, y1 = pill["box"]
    return x0, y0, x1, y1


def classify_pills(crop, region):
    """Return (damage_pills, badge_pills) with slot order preserved."""
    pills = dr.find_pills(crop, region)
    pills.sort(key=lambda p: p["box"][1])
    dmg, bad = [], []
    for p in pills:
        x0, y0, x1, y1 = p["box"]
        reg = crop[y0:y1, x0:x1]
        r, g, b = dr.channels(reg)
        bright = int(((r > 170) & (g > 160) & (b > 140)).sum())
        yel = int(dr.gate_yellow(reg).sum())
        grn = int(dr.gate_green(reg).sum())
        red = int(dr.gate_red(reg).sum())
        is_badge = (red + yel >= 150) and grn < 20 and bright < 90
        (bad if is_badge else dmg).append(p)
    return dmg, bad, pills


def report(name):
    p = os.path.join(str(paths.ROOT / "logs"), name)
    if not os.path.exists(p):
        print("missing", name)
        return
    im = load(p)
    x0, y0, x1, y1 = BOX
    crop = im[y0:y1, x0:x1]
    reg = (0, 0, x1 - x0, y1 - y0)
    dmg, bad, pills = classify_pills(crop, reg)
    tpl = dr.load_templates()
    vals = []
    for i in range(4):
        pill = dmg[i] if i < len(dmg) else None
        r = dr.read_skill(crop, pill, tpl)
        vals.append("%s/%s" % (r["text"], r["color"]))
    print("%-12s pills=%d badges=%d dmg=%d  %s" % (
        name, len(pills), len(bad), len(dmg), "  ".join(vals)))


for n in ["livebar.png", "k1.png", "g1.png", "b013.png", "f0.png", "f1.png"]:
    report(n)
