"""cmp_fill.py - compare the old and new _fill_missing search band.

Commit 1d51d0b narrowed the band from a fixed 140..430 to "the x range of the
capsules already found, +30/-110". That was meant to stop the matcher grabbing a
neighbouring card's digits, but it backfires when find_pills only returns two
capsules: the band becomes too narrow to recover the missing ones.

This measures both variants on the same live frame so the regression is a number
rather than an opinion.
"""
from __future__ import annotations

import sys

import cv2
import numpy as np

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")
import robot          # noqa: E402
import digitread as dr  # noqa: E402


def slots_with_band(crop, region, pills, band_mode):
    """Re-run the lattice fit, forcing one of the two band strategies."""
    cy = {id(p): (p["box"][1] + p["box"][3]) / 2.0 for p in pills}
    best = None
    for anchor in pills:
        y0 = cy[id(anchor)]
        for pitch in range(150, 216, 5):
            cand = [None, None, None, None]
            for p in pills:
                k = int(round((cy[id(p)] - y0) / pitch))
                if not (0 <= k <= 3):
                    continue
                if cand[k] is None or p["box"][0] > cand[k]["box"][0]:
                    cand[k] = p
            filled = sum(1 for c in cand if c is not None)
            score = (filled, -abs(pitch - 180))
            if best is None or score > best[0]:
                best = (score, cand)
    if best is None:
        return [None] * 4

    slots = best[1]
    h, w = crop.shape[:2]
    ys = [((s["box"][1] + s["box"][3]) / 2.0) if s else None for s in slots]
    known = [y for y in ys if y is not None]
    if len(known) < 2:
        return slots
    gaps = sorted(b - a for a, b in zip(sorted(known), sorted(known)[1:]))
    pitch = gaps[0] if gaps else 180.0
    if not (120 <= pitch <= 240):
        pitch = 180.0

    n_slots = len(slots)
    for i, s in enumerate(slots):
        if s is not None:
            continue
        ref = next(((j, ys[j]) for j in (i - 1, i + 1, i - 2, i + 2)
                    if 0 <= j < n_slots and 0 <= j < len(ys)
                    and ys[j] is not None), None)
        if ref is None:
            continue
        j, yj = ref
        want = yj + (i - j) * pitch
        if not (0 <= want < h):
            continue
        y0 = max(0, int(want - 60))
        y1 = min(h, int(want + 60))
        if band_mode == "fixed":
            bx0, bx1 = 140, min(w, 430)
        else:
            xs_known = [int(t["box"][0]) for t in slots if t]
            bx0 = max(0, min(xs_known) - 30) if xs_known else 140
            bx1 = min(w, max(xs_known) + 110) if xs_known else min(w, 430)
        band = crop[y0:y1, bx0:bx1]
        if band.size == 0:
            continue
        r_, g_, b_ = dr.channels(band)
        ink = (dr.gate_green(band) | dr.gate_red(band)).astype(np.uint8)
        if ink.sum() >= 20:
            n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
            blobs = []
            for k in range(1, n):
                bx, by, bw2, bh2, area = stats[k]
                if area < 12 or not (8 <= bh2 <= 30) or not (2 <= bw2 <= 26):
                    continue
                blobs.append((bx, by, bw2, bh2))
            if blobs:
                blobs.sort(key=lambda t: t[0])
                best_row = None
                for bx, by, bw2, bh2 in blobs:
                    row = [t for t in blobs if abs(t[1] - by) <= 10]
                    if best_row is None or len(row) > len(best_row):
                        best_row = row
                row = best_row or blobs
                pad = 3
                px0 = max(0, min(t[0] for t in row) - pad)
                px1 = min(band.shape[1], max(t[0] + t[2] for t in row) + pad)
                py0 = max(0, min(t[1] for t in row) - pad)
                py1 = min(band.shape[0], max(t[1] + t[3] for t in row) + pad)
                slots[i] = {"box": (px0 + bx0, py0 + y0, px1 + bx0, py1 + y0),
                            "kind": "num", "color": None}
                continue
    return slots


def main():
    scr = robot.Screen()
    x0, y0, x1, y1 = robot.SKILL_BOX
    crop = scr.grab((x0, y0, x1 - x0, y1 - y0))
    if crop is None:
        print("no frame")
        return 1
    reg = (0, 0, crop.shape[1], crop.shape[0])
    pills = dr.find_pills(crop, reg)
    print("find_pills -> %d" % len(pills))
    for p in sorted(pills, key=lambda z: z["box"][1]):
        print("   y=%d x=%d" % (p["box"][1], p["box"][0]))

    for mode in ("fixed", "narrow"):
        slots = slots_with_band(crop, reg, pills, mode)
        vis, _ = robot._is_skillbar(crop, reg, pills, slots)
        ys = [int(s["box"][1]) for s in slots if s]
        print("%-8s filled=%d  ys=%s  is_skillbar=%s" % (
            mode, len(ys), ys, vis))
    return 0


if __name__ == "__main__":
    sys.exit(main())
