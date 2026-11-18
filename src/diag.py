"""diag.py - is the bot's perception starving, and why?

Runs N polls. For each poll it records:
  * what Screen thinks the window rect is, vs a fresh lookup
  * bar / npills from the cached rect
  * bar / npills from a FRESH rect grab (to test a stale rect hypothesis)
  * the countdown-ROI white count (the 'my turn' signal)
"""
import sys

import numpy as np

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")
import digitread as dr  # noqa: E402
import robot  # noqa: E402

CNT_ROI = (1120, 55, 340, 150)


def countdown_white(rgb):
    x, y, w, h = CNT_ROI
    sub = rgb[y:y + h, x:x + w]
    if sub.size == 0:
        return 0
    return int((sub.min(axis=2) > 200).sum())


n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
s = robot.Screen()
p = robot.Perc(s)
x0, y0, x1, y1 = robot.SKILL_BOX
reg = (0, 0, x1 - x0, y1 - y0)

hit = 0
for i in range(n):
    st = p.observe()
    g = robot.find_game()
    fresh_rect = (g[1], g[2], g[3], g[4]) if g else None
    stale = (s.rect != fresh_rect)

    # independent grab using a fresh rect
    fresh_bar, fresh_pills = False, 0
    if fresh_rect:
        gx, gy = fresh_rect[0], fresh_rect[1]
        shot = s.sct.grab({"left": gx + x0, "top": gy + y0,
                           "width": x1 - x0, "height": y1 - y0})
        import cv2
        crop = cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2RGB)
        fresh_bar, fresh_pills = robot._is_skillbar(crop, reg)

    rgb = s.grab((0, 0, s.rect[2], s.rect[3])) if s.rect else None
    cw = countdown_white(rgb) if rgb is not None else -1
    if st["bar"]:
        hit += 1
    print("%2d battle=%d bar=%d/%d fresh=%d/%d stale=%d cnt_white=%d me=%s foe=%s"
          % (i, st["battle"], st["bar"], st["npills"], fresh_bar, fresh_pills,
             stale, cw, st["my_pct"], st["foe_pct"]), flush=True)
print("bar hits %d/%d" % (hit, n))
