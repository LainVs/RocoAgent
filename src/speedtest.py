"""speedtest.py - where does the grab time go?"""
import sys
import time

import mss
import numpy as np

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
from cap import game_rect  # noqa: E402

r = game_rect()
print("rect", r)

t = time.time()
s = mss.mss()
print("mss init %.3f" % (time.time() - t))

regions = {
    "full": {"left": r[0], "top": r[1], "width": r[2], "height": r[3]},
    "hud": {"left": r[0], "top": r[1], "width": 900, "height": 400},
    "skill": {"left": r[0] + 250, "top": r[1] + 480, "width": 530, "height": 1040},
}
for name, box in regions.items():
    for trial in range(3):
        t = time.time()
        shot = s.grab(box)
        t1 = time.time()
        arr = np.asarray(shot)
        t2 = time.time()
        print("%-6s trial%d grab=%.3f np=%.3f  %s" % (
            name, trial, t1 - t, t2 - t1, arr.shape))
