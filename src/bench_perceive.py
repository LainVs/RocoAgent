# -*- coding: utf-8 -*-
"""感知层基准：测耗时 + 复读已存的技能栏截图。

为什么不用 logs/shot
--------------------
一开始想拿 `logs/shot/` 的 400 张动作帧当"技能栏正样本"，跑了才发现全是
0%。原因是 `_shot()` 在**动作之后**截图，那时技能栏已经收起来了，抓到的
多半是换人面板或对战动画。所以那批帧对技能栏毫无意义。

真正可用的语料只有 `_dump_bar()` 存下来的 `logs/bar_*.png` ——
那是技能栏本身的裁剪，尺寸正好 530x1040。目前只有个别几张，
但以后每次实盘读不出来都会多一张，慢慢就攒成回归集。

用法
----
    python src/bench_perceive.py --live 30        # 实测 observe() 耗时（需游戏在跑）
    python src/bench_perceive.py --bar logs       # 复读 logs/bar_*.png 并校对数字
    python src/bench_perceive.py --bar logs --truth 171,114,45,102
                                                  # 带真值校验（按槽位顺序）

耗时基线（2026-11-21，2560x1600 客户端，venv 解释器）
    大厅态  p50 73ms   战斗态旧实现约 170ms -> 新实现约 85ms
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, r"D:\Tools")

import config            # noqa: E402
import digitread as dr   # noqa: E402
import perceive as P     # noqa: E402


def _quant(v):
    v = sorted(v)
    if not v:
        return (0.0, 0.0, 0.0)
    return (v[len(v) // 2], v[int(len(v) * 0.9)], v[-1])


def bench_live(n):
    import winio
    if not winio.find_game():
        print("游戏没在跑，--live 跳过")
        return 0
    scr = winio.Screen()
    perc = P.Perc(scr)
    for _ in range(3):
        perc.observe()
    ts, bars = [], 0
    for _ in range(n):
        t0 = time.perf_counter()
        st = perc.observe()
        ts.append((time.perf_counter() - t0) * 1000)
        bars += 1 if st.get("bar") else 0
    p50, p90, mx = _quant(ts)
    print("observe()  n=%d   p50 %.1f ms  p90 %.1f  max %.1f"
          % (n, p50, p90, mx))
    print("技能栏可见 %d / %d（大厅态为 0 是正常的）" % (bars, n))
    over = sum(1 for x in ts if x > 200)
    print("超过 200ms（控制台标红阈值）的次数：%d" % over)
    return 0


def bench_bar(paths, truth):
    tpl = dr.load_templates()
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "bar_*.png")))
        else:
            files.append(p)
    files = [f for f in files if os.path.exists(f)]
    if not files:
        print("没有 bar_*.png 可复读")
        return 0

    total_ok = 0
    total = 0
    ms = []
    for f in files:
        img = cv2.imread(f)
        if img is None:
            continue
        rgb = img[:, :, ::-1]
        h, w = rgb.shape[:2]
        reg = (0, 0, w, h)
        t0 = time.perf_counter()
        pills = dr.find_pills(rgb, reg)
        slots = P._slots(rgb, reg, pills)
        vis, npills = P._is_skillbar(rgb, reg, pills, slots)
        texts = []
        if vis:
            for s in slots:
                if not s:
                    texts.append(None)
                    continue
                texts.append(dr.read_skill(rgb, s, tpl)["text"])
        ms.append((time.perf_counter() - t0) * 1000)
        line = "%-30s bar=%-5s npills=%d  %s" % (
            os.path.basename(f), vis, npills, texts)
        if truth:
            want = truth.split(",")
            good = sum(1 for a, b in zip(texts, want) if a == b)
            total_ok += good
            total += len(want)
            line += "  -> %d/%d 对" % (good, len(want))
        print(line)

    p50, p90, mx = _quant(ms)
    print("")
    print("技能栏处理  p50 %.1f ms  p90 %.1f  max %.1f" % (p50, p90, mx))
    if truth:
        print("槽位数字准确率 %d / %d = %.0f%%"
              % (total_ok, total, 100.0 * total_ok / max(1, total)))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", type=int, default=0,
                    help="实测 observe() 耗时，跑 N 次")
    ap.add_argument("--bar", nargs="*", default=[],
                    help="复读技能栏截图（文件或目录）")
    ap.add_argument("--truth", default="",
                    help="真值，逗号分隔，按槽位顺序，如 171,114,45,102")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.bar:
        paths = args.bar or [config.LOG_DIR]
        if args.limit:
            files = []
            for p in paths:
                files += sorted(glob.glob(os.path.join(p, "bar_*.png"))) \
                    if os.path.isdir(p) else [p]
            paths = files[:args.limit]
        bench_bar(paths, args.truth)
    if args.live:
        bench_live(args.live)
    if not args.bar and not args.live:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
