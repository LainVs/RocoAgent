# -*- coding: utf-8 -*-
"""Regression for the "can I press this card" test.

The skill card greys out when its energy cost is above the current energy.
That grey is an overlay on the whole card, and the only part of the card whose
colour does not depend on the skill art is the cream name plate under the
damage capsule: 245 when usable, 196 when greyed.

The old test measured the art *above* the capsule, which reads any naturally
dark icon (the purple 蝙蝠毒蛊) as unusable and any bright greyed card as
usable - that is how the bot kept pressing cards with no energy left.

Run: python test_lit.py
It uses the frames captured in logs/litcap2 (full skill-bar crops, real
matches); when they are missing it says so and exits 0.
"""
from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"D:\Tools")

import cv2          # noqa: E402
import numpy as np  # noqa: E402

import digitread as dr  # noqa: E402
import robot            # noqa: E402

FRAMES = os.path.join(robot.LOG_DIR, "litcap2", "*.png")


def load(path):
    return cv2.imdecode(np.fromfile(path, np.uint8), 1)[:, :, ::-1].copy()


def main():
    files = sorted(glob.glob(FRAMES))
    if not files:
        print("no frames in %s - skipped" % FRAMES)
        return 0

    bad, seen = 0, {245: 0, 196: 0}
    for f in files:
        im = load(f)
        h, w = im.shape[:2]
        pills = dr.find_pills(im, (0, 0, w, h))
        slots = robot._slots(im, (0, 0, w, h), pills)
        row = []
        for pill in slots:
            if not pill:
                row.append("--")
                continue
            p75 = robot._plate_p75(im, pill)
            lit = robot._card_lit(im, pill)
            if p75 is None:
                row.append("??")
                continue
            seen[round(p75)] = seen.get(round(p75), 0) + 1
            if abs(p75 - 245) > 12 and abs(p75 - 196) > 12:
                print("  %s: unexpected plate %.0f" % (os.path.basename(f), p75))
                bad += 1
            if lit != (p75 > 220.0):
                print("  %s: lit=%s but plate=%.0f" % (os.path.basename(f), lit, p75))
                bad += 1
            row.append("%d%s" % (round(p75), "L" if lit else "."))
        print("%-22s %s" % (os.path.basename(f)[:22], " ".join(row)))

    bright = sum(v for k, v in seen.items() if k > 220)
    grey = sum(v for k, v in seen.items() if k <= 220)
    print("\nplates: %d usable / %d greyed" % (bright, grey))
    if not grey or not bright:
        print("WARNING: the sample does not contain both states")
    if bad:
        print("%d unexpected reads" % bad)
        return 1
    print("all lit cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
