# -*- coding: utf-8 -*-
"""tune_region_live.py —— 用当前实况画面调优立绘区域。

从 logs/det2/live_full.png（刚抓的实时帧）出发，测试多个候选区域，
找出"识别稳定 + 置信度高"的那个。

关键判据：同一画面连续 5 次识别应该给出同一个答案（稳定性）。
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
import portrait_onnx as P  # noqa: E402

FULL = os.path.join(ROOT, "logs", "det2", "live_full.png")


def main():
    import cv2
    img = cv2.imdecode(np.fromfile(FULL, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        print("找不到", FULL)
        return 1
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, w = img.shape[:2]
    print("画面 %dx%d" % (w, h))

    # 候选区域：从宽到窄，围绕当前标定值
    cands = [
        ("当前 0.62/0.34/0.20/0.30", 0.62, 0.34, 0.20, 0.30),
        ("收紧 0.60/0.32/0.16/0.24", 0.60, 0.32, 0.16, 0.24),
        ("更紧 0.62/0.34/0.14/0.20", 0.62, 0.34, 0.14, 0.20),
        ("偏左 0.55/0.30/0.20/0.30", 0.55, 0.30, 0.20, 0.30),
        ("偏下 0.60/0.38/0.18/0.28", 0.60, 0.38, 0.18, 0.28),
        ("正方形 0.58/0.32/0.20/0.20", 0.58, 0.32, 0.20, 0.20),
        ("roco-cal 默认 0.15/0.15/0.25/0.35", 0.15, 0.15, 0.25, 0.35),
    ]

    print("\n%-34s %-14s %6s %s" % ("区域", "识别", "置信", "候选"))
    print("-" * 78)
    for name, rx, ry, rw, rh in cands:
        x, y = int(rx * w), int(ry * h)
        bw, bh = int(rw * w), int(rh * h)
        crop = img[y:y + bh, x:x + bw]
        if crop.size == 0:
            continue
        r = P.predict(crop, topk=3)
        if not r:
            print("%-34s %-14s %6s" % (name, "(无)", "-"))
            continue
        top = r[0]
        alts = " ".join("%s%.2f" % (n[:6], c) for n, c in r[1:])
        print("%-34s %-14s %6.2f %s" % (name, top[0], top[1], alts))

    return 0


if __name__ == "__main__":
    sys.exit(main())
