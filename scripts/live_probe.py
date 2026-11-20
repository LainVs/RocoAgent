# -*- coding: utf-8 -*-
"""live_probe.py —— 现场只读探针：抓一帧，跑检测，出标注图

不点击、不按键、不连板子（只调 robot.Screen 抓屏）。
用途：在现场快速确认"当前画面能不能读出血量/数字"。

用法
    python scripts\\live_probe.py
    python scripts\\live_probe.py --loop 5      # 连抓 5 帧，看稳定性
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import matdet      # noqa: E402
import digitmat    # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=1)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--save", default=os.path.join("logs", "det2", "live.png"))
    a = ap.parse_args()

    from robot import Screen
    scr = Screen()
    scr.refresh()
    if scr.rect is None:
        print("no game window")
        return 2
    print("window rect=%s client=%s" % (scr.rect, scr.client_size))

    for i in range(a.loop):
        img = scr.grab_client((0, 0, 2560, 1600))
        if img is None:
            print("grab failed")
            return 2
        rgb = img
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

        t0 = time.perf_counter()
        hud = matdet.locate_hud(bgr, strict=True)
        t1 = time.perf_counter()
        rows = []
        for side in ("my", "foe"):
            box = hud["%s_bar" % side]
            px = hud["%s_ratio" % side]
            if box is None:
                rows.append("%s: no bar" % side)
                continue
            r, txt, conf = digitmat.read_bar_digits(bgr, box, side, prior=px)
            rows.append("%s: bar=%s px=%s digit=%-10s conf=%.2f fused=%s"
                        % (side, (box["x"], box["y"], box["w"], box["h"]),
                           ("%.3f" % px) if px is not None else "-",
                           txt or "-", conf, ("%.3f" % r) if r is not None else "-"))
        print("[%d] locate %5.1fms  hy=%s  dy=%s"
              % (i, (t1 - t0) * 1000,
                 bool(hud["my_bar"]) and bool(hud["foe_bar"]), hud["dy"]))
        for r in rows:
            print("     " + r)

        if i == 0:
            vis = bgr.copy()
            for side, col in (("my", (0, 255, 0)), ("foe", (0, 255, 0))):
                b = hud["%s_bar" % side]
                if b:
                    cv2.rectangle(vis, (b["x"], b["y"]),
                                  (b["x"] + b["w"], b["y"] + b["h"]), col, 2)
            out = os.path.join(ROOT, a.save) if not os.path.isabs(a.save) else a.save
            os.makedirs(os.path.dirname(out), exist_ok=True)
            cv2.imwrite(out, cv2.resize(vis, None, fx=0.55, fy=0.55,
                                        interpolation=cv2.INTER_AREA))
            print("     annotated ->", out)
        if i + 1 < a.loop:
            time.sleep(a.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
