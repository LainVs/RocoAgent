"""End-to-end accuracy on the 396 recorded skill-bar frames.

Measures what the bot would have read per slot, not just timing. This is the
number that matters for the win rate: an unread card is a wasted turn.

usage: python bench_skillbar.py [--limit N]
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from collections import Counter

import cv2
import numpy as np

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")
import robot          # noqa: E402
import digitread as dr  # noqa: E402

CAP = r"C:\Users\Lenovo\AppData\Local\Temp\opencode\roco_cap"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(CAP, "*.jpg")))
    if args.limit:
        paths = paths[:args.limit]
    tpl = dr.load_templates()
    x0, y0, x1, y1 = robot.SKILL_BOX
    cw, ch = x1 - x0, y1 - y0

    frames = 0
    bars = 0
    slot_state = [Counter() for _ in range(4)]
    obs_ms = []
    for p in paths:
        im = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            continue
        im = im[:, :, ::-1]
        crop = im[y0:y1, x0:x1]
        if crop.shape[0] != ch or crop.shape[1] != cw:
            continue
        frames += 1
        t0 = time.perf_counter()
        reg = (0, 0, cw, ch)
        pills = dr.find_pills(crop, reg)
        slots = robot._slots(crop, reg, pills)
        vis, _ = robot._is_skillbar(crop, reg, pills, slots)
        if vis:
            bars += 1
            for i, s in enumerate(slots):
                if s is None:
                    slot_state[i]["missing"] += 1
                    continue
                r = dr.read_skill(crop, s, tpl)
                if r["kind"] == "num" and r["value"] is not None:
                    slot_state[i]["num"] += 1
                elif r["kind"] == "num":
                    slot_state[i]["partial"] += 1
                else:
                    slot_state[i]["dash"] += 1
        obs_ms.append((time.perf_counter() - t0) * 1000)

    obs_ms.sort()
    if not obs_ms:
        print("no usable frames in %s" % CAP)
        return 1
    print("frames %d, skill bar visible %d" % (frames, bars))
    print("per-observation median %.1f ms  p90 %.1f ms"
          % (obs_ms[len(obs_ms) // 2], obs_ms[int(len(obs_ms) * 0.9)]))
    print()
    print("%-6s %8s %8s %8s %8s" % ("slot", "num", "partial", "dash", "missing"))
    for i, c in enumerate(slot_state, 1):
        print("%-6d %8d %8d %8d %8d" % (
            i, c["num"], c["partial"], c["dash"], c["missing"]))
    if bars:
        tot = sum(sum(c.values()) for c in slot_state)
        nums = sum(c["num"] for c in slot_state)
        print()
        print("readable slots: %d / %d = %.1f%%" % (nums, tot, 100.0 * nums / tot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
