"""test_switch.py - regression test for the forced-switch panel.

Fixtures are the only real panel ever captured at 1:1: logs/frames/021323..021354,
the four seconds between our pet dying (02:13:20) and the game replacing it
(02:13:58). They pin down three things that were wrong in production:

  * the panel has five rows numbered 1-5, and no sixth row even if you search
    down to y=1650 - so key "6" does not exist;
  * the fainted pet stays in the list with an empty HP bar and cannot be picked,
    so "press 1" is not a safe default;
  * the old channel-spread classifier scored that fainted row 28.7 (above its
    threshold of 25) on the first panel frame, i.e. it would pick the corpse.

usage: python test_switch.py
"""
import paths
import os
import sys
import types

import cv2
import numpy as np

sys.path.insert(0, str(paths.ROOT / "src"))
sys.path.insert(0, str(paths.TOOLS))
import robot  # noqa: E402

FRAMES = str(paths.ROOT / "logs/frames")
RECT = (443, 39, 2586, 1671)

PANEL = ["021323_0915.png", "021329_0916.png", "021334_0917.png",
         "021337_0918.png", "021341_0919.png", "021344_0920.png",
         "021347_0921.png", "021351_0922.png", "021354_0923.png"]
NOT_PANEL = ["021303_0911.png", "021308_0912.png", "021312_0913.png",
             "021318_0914.png", "021358_0924.png"]


class FakeScreen:
    def __init__(self, path):
        im = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            raise IOError("cannot read " + path)
        self.im = im[:, :, ::-1]
        self.rect = RECT

    def grab(self, box):
        x, y, w, h = box
        return self.im[y:y + h, x:x + w]

    def grab_full(self):
        return self.im


def main():
    bad = 0
    missing = 0

    print("--- panel detection ---")
    for name in PANEL:
        p = os.path.join(FRAMES, name)
        if not os.path.exists(p):
            missing += 1
            continue
        fs = FakeScreen(p)
        rows = robot.find_switch_rows(fs)
        ok = len(rows) == 5
        bad += 0 if ok else 1
        print("%-18s rows=%d %s" % (name, len(rows), "OK" if ok else "**FAIL**"))

    for name in NOT_PANEL:
        p = os.path.join(FRAMES, name)
        if not os.path.exists(p):
            missing += 1
            continue
        fs = FakeScreen(p)
        rows = robot.find_switch_rows(fs)
        ok = len(rows) < 3
        bad += 0 if ok else 1
        print("%-18s rows=%d %s" % (name, len(rows), "OK" if ok else "**FAIL**"))

    print()
    print("--- living / fainted classification ---")
    p = os.path.join(FRAMES, PANEL[4])
    if os.path.exists(p):
        fs = FakeScreen(p)
        rows = robot.find_switch_rows(fs)
        cand = robot.Bot._living_rows(types.SimpleNamespace(screen=fs), rows)
        for slot, _pos, fill in cand:
            want_alive = slot != 1
            got_alive = fill >= 0.06
            ok = got_alive == want_alive
            bad += 0 if ok else 1
            print("row %d fill=%.2f -> %-5s %s" % (
                slot, fill, "alive" if got_alive else "fainted",
                "OK" if ok else "**FAIL**"))

    print()
    print("--- mid-battle switch slot range (party list is 1-6) ---")
    bot = types.SimpleNamespace(switch_count=0)
    seen = []
    for i in range(12):
        bot.switch_count = i
        seen.append(robot.Bot.next_switch_slot(bot))
    ok = max(seen) <= 6 and min(seen) >= 1
    bad += 0 if ok else 1
    print("slots over 12 rotations: %s %s" % (seen, "OK" if ok else "**FAIL**"))

    print()
    if missing:
        print("(%d fixture(s) missing, skipped)" % missing)
    if bad:
        print("%d FAILURE(S)" % bad)
        return 1
    print("all switch cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
