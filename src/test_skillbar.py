"""test_skillbar.py - regression test for skill-bar detection and digit reads.

Every case below is a real frame that broke the bot at some point:
  * v1  - the only readable card was the one find_pills missed  -> 0% detected
  * q1  - same failure mode
  * r1/h8/p1/h2/h5/f2 - menus/VS/splash/scenery, must stay EMPTY (false
    positives here send the bot pressing keys into a menu)

usage: python test_skillbar.py
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")
import digitread as dr  # noqa: E402
import robot  # noqa: E402

LOGS = r"D:\Projects\RocoAgent\logs"
X0, Y0, X1, Y1 = dr.BAR
REG = (0, 0, X1 - X0, Y1 - Y0)

# frame -> (expected visible, expected four slot texts)
CASES = {
    "v1.png": (True, ["--", "546", "--", "--"]),
    "k1.png": (True, ["--", "212", "70", "100"]),
    "q1.png": (True, ["--", "415", "--", "--"]),
    # f2.png was originally a scenery frame, but the file on disk is now a real
    # battle frame. Verified at 2.5x zoom on 2026-11-18: 吞咽 315 / 力量增效 -- /
    # 跌落 251 / 火云车 --. So the old "must be empty" expectation was stale; the
    # detector was right and the test case was wrong.
    "f2.png": (True, ["315", "--", "251", "--"]),
    "r1.png": (False, ["-", "-", "-", "-"]),
    "h8.png": (False, ["-", "-", "-", "-"]),
    "h2.png": (False, ["-", "-", "-", "-"]),
    "h5.png": (False, ["-", "-", "-", "-"]),
    "p1.png": (False, ["-", "-", "-", "-"]),
    "livebar2.png": (False, ["-", "-", "-", "-"]),
}


def main():
    tpl = dr.load_templates()
    bad = 0
    for name, (want_vis, want_vals) in CASES.items():
        path = os.path.join(LOGS, name)
        if not os.path.exists(path):
            print("%-14s missing, skipped" % name)
            continue
        im = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]
        crop = im[Y0:Y1, X0:X1]
        vis, _ = robot._is_skillbar(crop, REG)
        slots = robot._slots(crop, REG)
        vals = [dr.read_skill(crop, p, tpl)["text"] for p in slots]
        ok = (vis == want_vis) and (vals == want_vals)
        if not ok:
            bad += 1
        print("%-14s vis=%-5s slots=%-18s %s" % (
            name, vis, " ".join(vals), "OK" if ok else "**FAIL** want %s %s" % (want_vis, want_vals)))
    print()
    if bad:
        print("%d FAILURE(S)" % bad)
        return 1
    print("all skill-bar cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
