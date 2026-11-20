# -*- coding: utf-8 -*-
"""probe_det.py —— 单张帧的检测器诊断（看候选、看为什么被拒）

    python scripts\\probe_det.py logs\\shot\\120411_T3_energy.jpg
    python scripts\\probe_det.py <帧> --save    # 同时存一张画框图到 logs\\det2
"""
from __future__ import annotations

import argparse
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import matdet  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("img")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()

    img = cv2.imread(a.img)
    if img is None:
        print("cannot read", a.img)
        return 2
    if img.shape[:2] == (1671, 2586):
        img = img[58:58 + 1600, 13:13 + 2560]
    H, W = img.shape[:2]
    print("image %dx%d" % (W, H))

    for name in ("my_hud", "foe_hud"):
        bx, by, bw, bh = matdet.band_box(img, matdet.DEFAULT_BANDS[name])
        roi = img[by:by + bh, bx:bx + bw]
        print("\n[%s] band=(%d,%d,%d,%d)" % (name, bx, by, bw, bh))
        bars = matdet.find_slabs(roi, H)
        if not bars:
            print("   no slab candidates")
        for i, b in enumerate(bars[:6]):
            print("   #%d y=%4d x=%4d w=%4d h=%3d conf=%.2f %s"
                  % (i, b["y"] + by, b["x"] + bx, b["w"], b["h"],
                     b["conf"], b["evidence"]))

    bx, by, bw, bh = matdet.band_box(img, matdet.DEFAULT_BANDS["cards"])
    roi = img[by:by + bh, bx:bx + bw]
    badges = matdet.find_disc_badges(roi)
    print("\n[cards] band=(%d,%d,%d,%d) badges=%d"
          % (bx, by, bw, bh, len(badges)))
    for b in badges[:8]:
        print("   x=%4d y=%4d d=%5.1f fill=%.2f dark=%.2f"
              % (b["x"] + bx, b["y"] + by, b["d"], b["fill"], b["dark"]))

    hud = matdet.locate_hud(img)
    cards = matdet.locate_cards(img)
    print("\nlocate_hud -> my=%s foe=%s dy=%s ratios=%s/%s"
          % (bool(hud["my_bar"]), bool(hud["foe_bar"]), hud["dy"],
             hud["my_ratio"], hud["foe_ratio"]))
    print("locate_cards -> %d cards" % len(cards["cards"]))

    if a.save:
        import numpy as np
        vis = img.copy()

        def box(b, col, t=""):
            if not b:
                return
            cv2.rectangle(vis, (b["x"], b["y"]),
                          (b["x"] + b["w"], b["y"] + b["h"]), col, 2)
            if t:
                cv2.putText(vis, t, (b["x"], max(12, b["y"] - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)

        box(hud["my_bar"], (0, 255, 0), "my %.2f" % (hud["my_ratio"] or -1))
        box(hud["foe_bar"], (0, 255, 0), "foe %.2f" % (hud["foe_ratio"] or -1))
        for c in cards["cards"]:
            box(c, (255, 160, 0), "#%d" % c["index"])
            box(c.get("capsule"), (0, 160, 255))
            box(c.get("nameplate"), (0, 255, 255))
        for b in cards["badges"]:
            box(b, (255, 0, 255))
        out = os.path.join(ROOT, "logs", "det2",
                           "probe_" + os.path.basename(a.img))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if vis.shape[1] > 1400:
            s = 1400.0 / vis.shape[1]
            vis = cv2.resize(vis, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        cv2.imwrite(out, vis)
        print("saved", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
