# -*- coding: utf-8 -*-
"""check_digits.py —— 字形数字通道的独立核验（不依赖 OCR）

三个指标
--------
1. **读率**：切字 + 匹配后能给出合法序列（`cur/max` 或 `NN%`）的比例
2. **耗时**：单帧读两次（我方+敌方）
3. **一致率**：与像素通道的比例差 < 2pt 的比例
   （两条通道独立，一致就是强证据；不一致时记下来人工看）

用法
----
    python scripts\\check_digits.py --set resultshots --limit 20
    python scripts\\check_digits.py --set frames --limit 20

注意：血条高 <12px 的帧（即 1280x800 的缩略图）数字只有 6px，
切字必然失败 —— 这不是算法问题，是分辨率问题，脚本会把它们单独统计。
"""
from __future__ import annotations

import argparse
import glob
import os
import statistics
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import matdet      # noqa: E402
import digitmat    # noqa: E402


def to_client(img):
    if img is None:
        return None
    if img.shape[:2] == (1671, 2586):
        return img[58:58 + 1600, 13:13 + 2560]
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="resultshots",
                    choices=["resultshots", "frames", "shot", "result_hit"])
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()

    pats = {"resultshots": "logs/resultshots/*.jpg",
            "frames": "logs/frames/*.png",
            "shot": "logs/shot/*.jpg",
            "result_hit": "logs/result_hit_*.png"}
    files = sorted(glob.glob(os.path.join(ROOT, pats[a.set])))
    step = max(1, len(files) // a.limit) if len(files) > a.limit else 1
    files = files[::step][:a.limit]

    ok = tot = 0
    tall = tall_ok = 0
    dts = []
    agree = []
    rows = []
    for f in files:
        img = to_client(cv2.imread(f))
        if img is None:
            continue
        hud = matdet.locate_hud(img)
        if not hud["my_bar"]:
            continue
        tot += 1
        bar_h = hud["my_bar"]["h"]
        t0 = time.perf_counter()
        rm, tm, cm = digitmat.read_bar_digits(img, hud["my_bar"], "my",
                                              prior=hud["my_ratio"])
        rf, tf, cf = (digitmat.read_bar_digits(img, hud["foe_bar"], "foe",
                                               prior=hud["foe_ratio"])
                      if hud["foe_bar"] else (None, "", 0.0))
        dts.append((time.perf_counter() - t0) * 1000.0)
        got = (rm is not None) or (rf is not None)
        ok += 1 if got else 0
        if bar_h >= 12:
            tall += 1
            tall_ok += 1 if got else 0
        for r, prior in ((rm, hud["my_ratio"]), (rf, hud["foe_ratio"])):
            if r is not None and prior is not None:
                agree.append(abs(r - prior) * 100.0)
        rows.append((os.path.basename(f), bar_h, tm, rm, hud["my_ratio"],
                     tf, rf, hud["foe_ratio"]))

    print("=" * 78)
    print("set=%s  frames=%d  bar_height>=12px: %d" % (a.set, tot, tall))
    print("  读率（至少一侧读出）  %5.1f%%  (%d/%d)" % (100.0 * ok / max(1, tot), ok, tot))
    if tall:
        print("  其中高条帧                    %5.1f%%  (%d/%d)"
              % (100.0 * tall_ok / tall, tall_ok, tall))
    if dts:
        dts.sort()
        print("  耗时/帧（双侧）        mean %.2fms  p50 %.2f  p95 %.2f"
              % (sum(dts) / len(dts), dts[len(dts) // 2],
                 dts[min(len(dts) - 1, int(len(dts) * 0.95))]))
    if agree:
        agree.sort()
        print("  与像素通道一致(<2pt)   %5.1f%%  中位差 %.2fpt"
              % (100.0 * sum(1 for e in agree if e < 2.0) / len(agree),
                 statistics.median(agree)))
    print("-" * 78)
    for nm, bh, tm, rm, pm, tf, rf, pf in rows[:14]:
        print("  %-22s h=%2d  my %-10s %6s / px %6s   foe %-6s %6s / px %6s"
              % (nm[:22], bh, tm, ("%.3f" % rm) if rm is not None else "-",
                 ("%.3f" % pm) if pm is not None else "-",
                 tf, ("%.3f" % rf) if rf is not None else "-",
                 ("%.3f" % pf) if pf is not None else "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
