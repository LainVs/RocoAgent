"""test_flow.py - regression test for the screen-flow detectors.

The bot once sat on the 确认首发 (confirm lead) screen for five minutes because
nothing recognised that button: the old code only looked for 退出, in a ROI 300 px
too high. These cases pin the behaviour down.

usage: python test_flow.py
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")
import robot  # noqa: E402

LOGS = r"D:\Projects\RocoAgent\logs"
RECT = (443, 39, 2586, 1671)


class FakeScreen:
    def __init__(self, path, rect=RECT):
        self.im = cv2.imdecode(np.fromfile(path, np.uint8),
                              cv2.IMREAD_COLOR)[:, :, ::-1]
        self.rect = rect

    def grab(self, box):
        x, y, w, h = box
        return self.im[y:y + h, x:x + w]

    def grab_full(self):
        return self.im


# frame -> expected action-button presence
ACTION_CASES = {
    "w1.png": True,    # 确认首发 (pre-match lineup)
    "h5.png": True,    # 退出 (result)
    "h6.png": True,    # 退出 (result)
    "h8.png": False,   # lobby
    "k1.png": False,   # battle
    "q1.png": False,   # battle
    "p1.png": False,   # VS splash
    "z1.png": False,   # loading screen (pale floor must not be mistaken for a button)
}

# frame -> expected has_start_button (开始挑战 in the lobby)
START_CASES = {"h8.png": True, "k1.png": False, "w1.png": False}

# frame -> how many party rows the forced-switch panel should show
SWITCH_CASES = {
    "frames2/063750_2698.png": 5,   # real forced-switch panel
    "frames2/063637_2667.png": 0,   # death animation, panel not up yet
    "k1.png": 0,                    # normal battle
    "h8.png": 0,                    # lobby
}


def main():
    bad = 0
    print("--- bottom-centre action button ---")
    for name, want in ACTION_CASES.items():
        p = os.path.join(LOGS, name)
        if not os.path.exists(p):
            print("%-10s missing" % name)
            continue
        got = robot.find_action_button(FakeScreen(p))
        ok = (got is not None) == want
        if not ok:
            bad += 1
        print("%-10s want=%-5s got=%-18s %s" % (
            name, want, str(got), "OK" if ok else "**FAIL**"))

    print()
    print("--- lobby start button ---")
    for name, want in START_CASES.items():
        p = os.path.join(LOGS, name)
        if not os.path.exists(p):
            continue
        got = robot.has_start_button(FakeScreen(p))
        ok = got == want
        if not ok:
            bad += 1
        print("%-10s want=%-5s got=%-5s %s" % (
            name, want, got, "OK" if ok else "**FAIL**"))

    print()
    print("--- forced-switch party panel ---")
    for name, want in SWITCH_CASES.items():
        p = os.path.join(LOGS, name)
        if not os.path.exists(p):
            print("%-28s missing, skipped" % name)
            continue
        rows = robot.find_switch_rows(FakeScreen(p))
        ok = (len(rows) == want) if want else (len(rows) < 3)
        if not ok:
            bad += 1
        print("%-28s want=%-2d got=%d %s" % (
            name, want, len(rows), "OK" if ok else "**FAIL**"))

    print()
    if bad:
        print("%d FAILURE(S)" % bad)
        return 1
    print("all flow cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
